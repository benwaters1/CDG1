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
import itertools
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-rg-"

# One number per fixture, so no two references can collide. They were
# hash((email, days_ago, n)) % 100000, and Python salts string hashes afresh
# on every run: two fixtures landing on one reference would fail the insert
# at random, a little more often with every fixture added.
_SEQ = itertools.count()


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _stay(conn, email, name, days_ago, price=900, status="confirmed", n=0,
          nights=2):
    """A stay that began `days_ago` days ago; a negative number is one still
    to come."""
    today = m.house_today()
    d = today - timedelta(days=days_ago)
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    ref = f"{TAG}s{next(_SEQ)}"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?)""",
        (room, ref, ref + "tok", name, email, d.isoformat(),
         (d + timedelta(days=nights)).isoformat(), status, price,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _find(data, name):
    return next((g for g in data["guests"] if g["name"] == name), None)


def _row_cells(page, name):
    """The text of each cell in the table row that names this guest, or None.

    Read from the row, because the page as a whole carries every other guest's
    dates as well, and a date found anywhere on it proves nothing about whose
    it is.
    """
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        if name in row:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            return [" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in cells]
    return None


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
    # Three stays = two gaps, the commonest repeat shape. Taking the upper
    # middle made "typical" the LONGEST gap, so somebody long overdue by their
    # real rhythm read as early.
    for i, d in enumerate((800, 770, 400)):
        _stay(conn, TAG + "uneven@ex.invalid", TAG + "Uneven", d, n=i)
    uneven = _find(m.repeat_guests(conn), TAG + "Uneven")
    s.check("two gaps of 30 and 370 give a typical of 200, not 370",
            uneven and abs(uneven["typical_gap"] - 200) <= 1,
            detail=str(uneven["typical_gap"]) if uneven else "")

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

    s.section("A stay still to come is not a visit")
    # The defect this section is for. "Last" was the latest arrival on file,
    # booked or not, so a guest with a stay ahead read "Last here: -90 days
    # ago" on Fill a gap, the booking was counted as a stay, and their rhythm
    # took in a gap that ends on a day that has not come.
    today = m.house_today()

    def iso(days_from_today):
        return (today + timedelta(days=days_from_today)).isoformat()

    # Their only recent stay is the one ahead: two visits over a year ago,
    # and a booking ninety days out.
    for i, d in enumerate((800, 430)):
        _stay(conn, TAG + "ahead@ex.invalid", TAG + "Ahead", d, n=i)
    _stay(conn, TAG + "ahead@ex.invalid", TAG + "Ahead", -90, n=2)
    # Past and future both: here a month ago, and booked again.
    for i, d in enumerate((400, 30)):
        _stay(conn, TAG + "both@ex.invalid", TAG + "Both", d, n=i)
    _stay(conn, TAG + "both@ex.invalid", TAG + "Both", -45, n=2)
    # Every three hundred days until nine hundred days ago: exactly who the
    # overdue list is for. One of the pair has booked back in since.
    for who in ("Gone", "Back"):
        for i, d in enumerate((1200, 900)):
            _stay(conn, f"{TAG}{who.lower()}@ex.invalid", TAG + who, d, n=i)
    _stay(conn, TAG + "back@ex.invalid", TAG + "Back", -60, n=2)
    # In the house tonight: here once before, and arrived yesterday.
    _stay(conn, TAG + "here@ex.invalid", TAG + "Here", 500)
    _stay(conn, TAG + "here@ex.invalid", TAG + "Here", 1, n=1, nights=3)
    # Been once and booked a second, and never been with one booked.
    _stay(conn, TAG + "oncemore@ex.invalid", TAG + "OnceMore", 300)
    _stay(conn, TAG + "oncemore@ex.invalid", TAG + "OnceMore", -30, n=1)
    _stay(conn, TAG + "first@ex.invalid", TAG + "First", -20)

    data = m.repeat_guests(conn, today=today)
    ahead, both = _find(data, TAG + "Ahead"), _find(data, TAG + "Both")
    s.check("a guest whose only recent stay is booked is last here at the stay they made",
            ahead and ahead["last"] == iso(-430),
            detail=f"last={ahead['last']} (the booking is {iso(90)})" if ahead else "not found")
    s.check("so it was a positive number of days ago",
            ahead and ahead["days_since"] == 430,
            detail=f"{ahead['days_since']} — this read -90 on Fill a gap"
            if ahead else "")
    s.check("the booking is carried as their next stay",
            ahead and ahead["next"] == iso(90) and ahead["booked"] == 1,
            detail=f"next={ahead['next']}, booked={ahead['booked']}" if ahead else "")
    s.check("and is not counted as a stay",
            ahead and ahead["stays"] == 2, detail=str(ahead["stays"]) if ahead else "")
    s.check("nor as a gap in their rhythm",
            ahead and ahead["typical_gap"] == 370,
            detail=f"{ahead['typical_gap']} — 800 to 430 days ago is 370; the "
                   "gap to the booking would make it 445" if ahead else "")
    s.check("a guest with stays behind and ahead is last here at the latest behind",
            both and both["last"] == iso(-30) and both["days_since"] == 30,
            detail=f"last={both['last']}, {both['days_since']}d" if both else "not found")
    s.check("and next due at the one ahead",
            both and both["next"] == iso(45) and both["stays"] == 2,
            detail=f"next={both['next']}, stays={both['stays']}" if both else "")

    gone, back = _find(data, TAG + "Gone"), _find(data, TAG + "Back")
    # Without this the next check could pass on a rhythm that never flags
    # anybody: the only difference between the two is the booking.
    s.check("nine hundred days out on a three-hundred-day rhythm is overdue",
            gone and gone["overdue"] is True,
            detail=f"gap~{gone['typical_gap']}d, {gone['days_since']}d since" if gone else "")
    s.check("but not once they have booked to come back",
            back and back["overdue"] is False and back["days_since"] == 900,
            detail=f"overdue={back['overdue']}, {back['days_since']}d since, "
                   f"next={back['next']} — their next stay is in the diary"
            if back else "not found")
    s.check("so they are not on the list of guests who have stopped coming",
            not any(g["name"] == TAG + "Back" for g in data["overdue"])
            and any(g["name"] == TAG + "Gone" for g in data["overdue"]))

    here = _find(data, TAG + "Here")
    s.check("a guest in the house tonight is here now, until they leave",
            here and here["here_now"] is True and here["until"] == iso(2),
            detail=f"here_now={here['here_now']}, until={here['until']}" if here else "not found")
    s.check("and the stay they are in is their last one, begun yesterday",
            here and here["last"] == iso(-1) and here["days_since"] == 1
            and here["stays"] == 2 and not here["overdue"],
            detail=f"last={here['last']}, stays={here['stays']}" if here else "")
    # Somebody a year into a long stay, who used to come every fifty days: by
    # their old rhythm the four hundred days since they arrived make them
    # late, and they are sitting in the house.
    for i, d in enumerate((1000, 950, 900)):
        _stay(conn, TAG + "resident@ex.invalid", TAG + "Resident", d, n=i)
    _stay(conn, TAG + "resident@ex.invalid", TAG + "Resident", 400, n=3, nights=430)
    resident = _find(m.repeat_guests(conn, today=today), TAG + "Resident")
    s.check("a guest living in the house is not overdue a visit",
            resident and resident["here_now"] and resident["overdue"] is False,
            detail=f"overdue={resident['overdue']}, {resident['days_since']}d since "
                   f"arriving, rhythm {resident['typical_gap']}d" if resident else "not found")

    s.check("been once and booked a second is not a regular yet",
            _find(data, TAG + "OnceMore") is None,
            detail="they come back on the day they arrive, as stay_number counts it")
    anyone = m.repeat_guests(conn, today=today, min_stays=1)
    once_more = _find(anyone, TAG + "OnceMore")
    s.check("though they are known, with one stay made and one booked",
            once_more and once_more["stays"] == 1 and once_more["next"] == iso(30),
            detail=str(once_more and (once_more["stays"], once_more["next"])))
    s.check("and somebody who has only ever booked has made no stay at all",
            _find(anyone, TAG + "First") is None,
            detail="a booking with nothing behind it is not a guest history")
    early = [(g["name"], g["last"], g["days_since"]) for g in anyone["guests"]
             if g["days_since"] < 0 or g["last"] > today.isoformat()]
    s.check("nobody is ever last here on a day that has not come",
            not early, detail=str(early[:3]))

    s.section("The same split as who is in the house")
    # guest_visits says it makes the three-way split stays_with_status makes
    # for In residence. Asked about the days either side of a stay, where the
    # two could disagree: a stay leaving today, one arriving today, one ahead.
    _stay(conn, TAG + "three@ex.invalid", TAG + "Three", 5, n=0, nights=5)
    _stay(conn, TAG + "three@ex.invalid", TAG + "Three", 0, n=1, nights=2)
    _stay(conn, TAG + "three@ex.invalid", TAG + "Three", -20, n=2)
    house = [st for st in m.stays_with_status(conn, today, channels=False)
             if st["email"] == TAG + "three@ex.invalid"]
    split = m.guest_visits(house, today)
    by_phase = {p: sorted(st["arrival_date"] for st in house if st["stay_status"] == p)
                for p in ("past", "current", "upcoming")}
    s.check("what has begun is what the house calls past or in residence",
            sorted(st["arrival_date"] for st in split["visits"])
            == sorted(by_phase["past"] + by_phase["current"]),
            detail=f"{by_phase} against {[st['arrival_date'] for st in split['visits']]}")
    s.check("what is ahead is what it calls upcoming",
            [st["arrival_date"] for st in split["ahead"]] == by_phase["upcoming"],
            detail=str(by_phase["upcoming"]))
    s.check("here now is in residence",
            split["here_now"] is True and by_phase["current"] == [iso(0)]
            and split["until"] == iso(2),
            detail=f"here_now={split['here_now']}, current={by_phase['current']}")
    # Alone, because above the stay arriving today would keep them here now
    # whatever the stay leaving today said.
    leaving = m.guest_visits([{"arrival_date": iso(-5), "departure_date": iso(0)}], today)
    s.check("and a guest leaving today is not",
            leaving["here_now"] is False and len(leaving["visits"]) == 1
            and by_phase["past"] == [iso(-5)],
            detail=f"here_now={leaving['here_now']} — the house calls a stay "
                   "that ends today past")

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
    # Columns: Guest, Stays, Also, Worth, Last here, Rated.
    row = _row_cells(page, TAG + "Both")
    s.check("a guest booked again shows the stay they made as last here",
            row and row[4].startswith(m.format_date_short(iso(-30))),
            detail=f"{row[4] if row else 'no row'} — the booking was showing as "
                   "the last stay")
    s.check("with the booking beside it, as booked",
            row and "booked" in row[4] and m.format_date_short(iso(45)) in row[4],
            detail=row[4] if row else "")
    s.check("and their stays count only what has happened",
            row and row[1] == "2", detail=str(row[1] if row else ""))
    here_row = _row_cells(page, TAG + "Here")
    s.check("somebody in the house tonight is shown as here now",
            here_row and "here now" in here_row[4],
            detail=here_row[4] if here_row else "no row")
    s.check("and the page says a booking is not a visit",
            "A stay counts once it has begun" in page)

    s.section("A guest's own history is not split by capitalisation")
    hist = oc.get(f"/admin/bookings/guest/{TAG}split@ex.invalid").get_data(as_text=True)
    # No "or" fallback. The previous version ended `or hist.count("row") > 0`,
    # and every page the app renders contains the substring "row" — so the
    # check could not fail and was reading as cover for an untested path.
    s.check("both stays appear under either spelling",
            hist.count(TAG + "Split") >= 1,
            detail="an exact email match showed only half a regular's history")
    s.check("and the lifetime spend covers both, not one",
            "2000" in hist.replace(",", "").replace(".00", ""),
            detail="spend was halved when the two spellings were separate guests")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
