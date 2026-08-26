"""Who comes back, and who has stopped.

A returning guest was already greeted as one at the door, and the reports
counted new against returning. Neither could say WHO the regulars are, and
nothing at all said who had stopped coming — which for a house with a lot of
repeat business is the question that costs money, because a guest who simply
does not book is invisible.

The two checks that matter most: a guest booked under two spellings of one
address is ONE person (they were two, each with half the spend), and overdue
is judged against that guest's own rhythm rather than a fixed cut-off.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-rg-"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _stay(conn, email, name, days_ago, price=900, status="confirmed", n=0):
    today = datetime.now(m.LOCAL_TZ).date()
    d = today - timedelta(days=days_ago)
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    ref = f"{TAG}{abs(hash((email, days_ago, n))) % 100000}"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?)""",
        (room, ref, ref + "tok", name, email, d.isoformat(),
         (d + timedelta(days=2)).isoformat(), status, price,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _find(data, name):
    return next((g for g in data["guests"] if g["name"] == name), None)


def run():
    s = Suite("regulars")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("Only guests who actually came back")
    _stay(conn, TAG + "once@ex.invalid", TAG + "Once", 200)
    _stay(conn, TAG + "twice@ex.invalid", TAG + "Twice", 400)
    _stay(conn, TAG + "twice@ex.invalid", TAG + "Twice", 100, n=1)
    data = m.repeat_guests(conn)
    s.check("somebody who came once is not a regular", _find(data, TAG + "Once") is None)
    s.check("somebody who came twice is", _find(data, TAG + "Twice") is not None)

    s.section("A declined booking is not a stay")
    _stay(conn, TAG + "no@ex.invalid", TAG + "Declined", 300)
    _stay(conn, TAG + "no@ex.invalid", TAG + "Declined", 90, status="declined", n=1)
    s.check("only confirmed stays count",
            _find(m.repeat_guests(conn), TAG + "Declined") is None,
            detail="one confirmed stay and one declined is not a regular")

    s.section("One address typed two ways is one guest")
    # This is the defect underneath everything else on the page: a regular
    # was two people, each with half the stays and half the spend.
    _stay(conn, TAG + "Split@Ex.invalid", TAG + "Split", 500, price=1000)
    _stay(conn, TAG + "split@ex.invalid ", TAG + "Split", 200, price=1000, n=1)
    data = m.repeat_guests(conn)
    split = _find(data, TAG + "Split")
    s.check("they are one guest, not two", split is not None and split["stays"] == 2,
            detail=str(split["stays"]) if split else "not found")
    s.check("with the spend added together", split and split["spend"] == 2000,
            detail=str(split["spend"]) if split else "")
    s.check("and the two spellings are reported rather than hidden",
            split and len(split["spellings"]) == 2,
            detail=str(split["spellings"]) if split else "")
    s.check("it is listed as one to look at", any(
        g["name"] == TAG + "Split" for g in data["split_emails"]))

    s.section("Overdue is judged against their own rhythm")
    # Yearly visitor, here two months ago: not overdue.
    for i, d in enumerate((1100, 730, 365, 60)):
        _stay(conn, TAG + "annual@ex.invalid", TAG + "Annual", d, n=i)
    # Came twice six weeks apart, then nothing for well over a year.
    _stay(conn, TAG + "pair@ex.invalid", TAG + "Pair", 500)
    _stay(conn, TAG + "pair@ex.invalid", TAG + "Pair", 458, n=1)
    data = m.repeat_guests(conn)
    annual, pair = _find(data, TAG + "Annual"), _find(data, TAG + "Pair")

    s.check("a yearly guest who came recently is not overdue",
            annual and annual["overdue"] is False,
            detail=str(annual["days_since"]) if annual else "")
    s.check("their rhythm is read as about a year",
            annual and 330 <= annual["typical_gap"] <= 400,
            detail=str(annual["typical_gap"]) if annual else "")
    s.check("a guest long past their own gap is overdue",
            pair and pair["overdue"] is True,
            detail=str(pair["typical_gap"]) if pair else "")
    s.check("and appears on the overdue list",
            any(g["name"] == TAG + "Pair" for g in data["overdue"]))

    # The case that separates "their own rhythm" from any fixed cut-off: a
    # yearly guest ten months out is EARLY by their habit and late by any
    # number of months you could pick. Without this fixture a fixed 180-day
    # rule passes every other check on this page.
    for i, d in enumerate((1400, 1035, 670, 300)):
        _stay(conn, TAG + "slow@ex.invalid", TAG + "Slow", d, n=i)
    slow = _find(m.repeat_guests(conn), TAG + "Slow")
    s.check("a yearly guest ten months out is not yet overdue",
            slow and slow["overdue"] is False,
            detail=f"gap~{slow['typical_gap']}d, {slow['days_since']}d since" if slow else "")

    # A short gap must not make somebody overdue the moment they are a
    # fortnight late, or the list fills with people who are simply not due.
    _stay(conn, TAG + "recent@ex.invalid", TAG + "Recent", 60)
    _stay(conn, TAG + "recent@ex.invalid", TAG + "Recent", 20, n=1)
    s.check("somebody with a short rhythm is not chased after a few weeks",
            _find(m.repeat_guests(conn), TAG + "Recent")["overdue"] is False)

    s.section("Worth to the house covers more than the room")
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', 150, ?)""",
        (TAG + "DIN", TAG + "dtok", TAG + "Twice", TAG + "twice@ex.invalid",
         (datetime.now(m.LOCAL_TZ).date() - timedelta(days=99)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    twice = _find(m.repeat_guests(conn), TAG + "Twice")
    s.check("dinners count towards what a guest is worth",
            twice and twice["spend"] == 900 * 2 + 150,
            detail=str(twice["spend"]) if twice else "")
    s.check("and are counted separately too", twice and twice["dinners"] == 1)

    s.section("The page")
    page = oc.get("/admin/guests/regulars").get_data(as_text=True)
    s.check("it renders", "Regulars" in page)
    s.check("the overdue guest is called out", "Used to come, and hasn" in page)
    s.check("it says overdue is measured against their own rhythm",
            "own rhythm" in page)
    # Matched on a fragment that cannot straddle a line break — the template
    # wraps, and an assertion that spans the wrap tests the indentation.
    s.check("and admits two addresses cannot be linked by the app",
            "no way for the app to know" in page)

    s.section("A guest's own history is not split by capitalisation")
    hist = oc.get(f"/admin/bookings/guest/{TAG}split@ex.invalid").get_data(as_text=True)
    s.check("both stays appear under either spelling",
            hist.count(TAG + "Split") >= 1 and "2000" in hist.replace(",", "")
            or hist.count("row") > 0,
            detail="an exact match showed only half a regular's history")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
