"""A family taking three rooms was three unconnected bookings.

Three confirmations, three bills, three manage links, three arrival texts to
the same telephone on the same morning — each one billed. The house could not
see them as one party, could not say what the group owed, and "in the house
tonight: 3 rooms" read as three separate parties. In a seven-room château a
multi-room party is most of what a large booking actually is.

THE PARTY IS DELIBERATELY THIN. A name and an id on the bookings that belong
to it, and nothing else. Each booking keeps its own room, dates, price, manage
token and bill, because they genuinely ARE separate stays that happen to be
one group — and a "party booking" that owned the money would have to reinvent
everything a booking already does, then disagree with it.

So the checks are about what is said ONCE rather than three times, and about
the party changing nothing underneath it.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTPTY"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_parties WHERE id IN
                    (SELECT party_id FROM bookings WHERE reference_code LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM sms_outbox WHERE phone = '+33655555555'")
    conn.execute("DELETE FROM audit_log WHERE action LIKE 'booking_party%'")
    conn.commit()
    conn.close()


def _stay(ref, room_id, arrive_offset=1, nights=2, party_size=2, price=400.0,
          phone="+33655555555"):
    conn = db()
    today = datetime.now(m.LOCAL_TZ).date()
    start = today + timedelta(days=arrive_offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)""",
        (room_id, TAG + ref, TAG.lower() + "tok" + ref, "Fontaine " + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid", phone, start.isoformat(),
         (start + timedelta(days=nights)).isoformat(), party_size, price,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("One party, several rooms")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    conn = db()
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY sort_order, name LIMIT 3").fetchall()
    conn.close()
    s.check("there are three rooms to put a family in", len(rooms) == 3)
    if len(rooms) < 3:
        return s

    a = _stay("A", rooms[0]["id"], price=400.0, party_size=2)
    b = _stay("B", rooms[1]["id"], price=300.0, party_size=2)
    c = _stay("C", rooms[2]["id"], price=250.0, party_size=1)

    s.section("Three rooms, one family")
    r = oc.post("/admin/parties/new",
                data={"booking_ids": [str(a["id"]), str(b["id"]), str(c["id"])],
                      "name": TAG + " Fontaine party"}, follow_redirects=True)
    party = _one("SELECT * FROM booking_parties WHERE name = ?",
                 (TAG + " Fontaine party",))
    s.check("the party exists", party is not None, detail=str(flashes(r)))
    s.check("with all three bookings in it",
            _one("SELECT COUNT(*) AS c FROM bookings WHERE party_id = ?",
                 (party["id"],))["c"] == 3)
    s.check("and a lead the house can talk to", party["lead_booking_id"] is not None)
    s.check("it is on the record",
            _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'booking_party_created'")["c"] == 1)

    s.section("What the group owes, in one figure")
    conn = db()
    with m.app.test_request_context():
        detail = m.party_detail(conn, party["id"])
    conn.close()
    s.check("it knows how many rooms", detail["rooms"] == 3)
    s.check("and how many people", detail["guests"] == 5,
            detail=f"{detail['guests']} — two, two and one")
    # Summed from each booking's own bill rather than recomputed, so there is
    # one definition of what a stay costs and this is not a second one.
    # Against the three bills themselves, not against numbers written here.
    # booking_bill works the price out from the room's rate, the nights, the
    # extras and the tourist tax — so a figure typed into this test would be
    # a SECOND definition of what a stay costs, which is the exact thing the
    # party is built to avoid.
    bills = [_bill(x["id"]) for x in (a, b, c)]
    s.check("the total is the three bills added up",
            abs(detail["total"] - sum(x["total"] for x in bills)) < 0.01,
            detail=f"{detail['total']} vs {[x['total'] for x in bills]}")
    s.check("and what is still owed comes from the same place",
            abs(detail["owed"] - sum(x["owed"] for x in bills)) < 0.01,
            detail=f"{detail['owed']} vs {[x['owed'] for x in bills]}")
    s.check("which is a real figure, not zero",
            detail["total"] > 0, detail=str(detail["total"]))

    s.check("the party's dates span the whole group",
            detail["arrival"] == (today + timedelta(days=1)).isoformat()
            and detail["departure"] == (today + timedelta(days=3)).isoformat(),
            detail=f"{detail['arrival']} -> {detail['departure']}")

    s.section("The bookings underneath are untouched")
    # The party owns nothing. If it did, it would have to reinvent the bill,
    # the manage token and the room, then disagree with them.
    for ref, row in (("A", a), ("B", b), ("C", c)):
        fresh = _one("SELECT * FROM bookings WHERE reference_code = ?", (TAG + ref,))
        s.check(f"{ref} keeps its own room and dates",
                fresh["room_id"] == row["room_id"]
                and fresh["arrival_date"] == row["arrival_date"])
        s.check(f"{ref} keeps its own manage link",
                fresh["manage_token"] == row["manage_token"])
    s.check("and each still has its own bill",
            all(_bill(x["id"])["total"] > 0 for x in (a, b, c)))

    s.section("One arrival text, not three")
    # Three identical texts to the same telephone on the same morning, each
    # billed, is what this existed to stop.
    conn = db()
    with m.app.test_request_context():
        said = m.run_guest_text_job(conn, "checkin", days_before=1)
    conn.commit()
    conn.close()
    texts = _one("SELECT COUNT(*) AS c FROM sms_outbox WHERE phone = '+33655555555'")["c"]
    s.check("exactly one message was written", texts == 1,
            detail=f"{texts} — one telephone, one family, one text")
    s.check("and the job says why the others were not",
            "same party" in said, detail=str(said))

    # Every room is stamped, or the next run texts them again for the same stay.
    unstamped = _one(
        "SELECT COUNT(*) AS c FROM bookings WHERE reference_code LIKE ? "
        "AND checkin_text_sent_at IS NULL", (TAG + "%",))["c"]
    s.check("every room in the party is stamped, not only the one written to",
            unstamped == 0,
            detail=f"{unstamped} unstamped — the next run would text them again")

    conn = db()
    with m.app.test_request_context():
        again = m.run_guest_text_job(conn, "checkin", days_before=1)
    conn.commit()
    conn.close()
    s.check("so a second run sends nothing",
            _one("SELECT COUNT(*) AS c FROM sms_outbox WHERE phone = '+33655555555'")["c"] == 1,
            detail=str(again))

    s.section("A booking on its own behaves exactly as before")
    solo = _stay("SOLO", rooms[0]["id"], arrive_offset=40, phone="+33644444444")
    conn = db()
    with m.app.test_request_context():
        leads = m.party_lead_bookings(conn, [solo["id"]])
    conn.close()
    s.check("it is its own lead", leads == [solo["id"]],
            detail="a house of seven rooms does not want a party wrapper round "
                   "every single reservation")
    conn = db()
    with m.app.test_request_context():
        s.check("and it belongs to no party",
                m.party_for_booking(conn, solo["id"]) is None)
    conn.close()

    s.section("A booking cannot be quietly taken out of somebody else's party")
    d = _stay("D", rooms[1]["id"], arrive_offset=60)
    r = oc.post("/admin/parties/new",
                data={"booking_ids": [str(a["id"]), str(d["id"])],
                      "name": TAG + " second party"}, follow_redirects=True)
    s.check("it is refused", any("already in a party" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("the first party is intact",
            _one("SELECT party_id FROM bookings WHERE id = ?", (a["id"],))["party_id"]
            == party["id"])
    s.check("and no second party was made",
            _one("SELECT COUNT(*) AS c FROM booking_parties WHERE name = ?",
                 (TAG + " second party",))["c"] == 0)

    r = oc.post("/admin/parties/new", data={"booking_ids": [str(d["id"])]},
                follow_redirects=True)
    s.check("one booking is not a party",
            any("at least two" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("Untying it leaves the bookings alone")
    r = oc.post(f"/admin/parties/{party['id']}/disband", follow_redirects=True)
    s.check("the party is gone",
            _one("SELECT COUNT(*) AS c FROM booking_parties WHERE id = ?",
                 (party["id"],))["c"] == 0, detail=str(flashes(r)))
    s.check("but all three stays remain",
            _one("SELECT COUNT(*) AS c FROM bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))["c"] >= 3,
            detail="untying a group must not cancel anybody's holiday")
    s.check("each with its room and money",
            abs((_one("SELECT total_price FROM bookings WHERE reference_code = ?",
                      (TAG + "A",))["total_price"] or 0) - 400.0) < 0.01)
    s.check("and none of them still points at it",
            _one("SELECT COUNT(*) AS c FROM bookings WHERE party_id = ?",
                 (party["id"],))["c"] == 0)

    s.section("Who may tie one")
    e = _stay("E", rooms[0]["id"], arrive_offset=80)
    f = _stay("F", rooms[1]["id"], arrive_offset=80)
    r = ec.post("/admin/parties/new",
                data={"booking_ids": [str(e["id"]), str(f["id"])]},
                follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and no party was made",
            _one("SELECT party_id FROM bookings WHERE id = ?", (e["id"],))["party_id"] is None)
    r = m.app.test_client().post("/admin/parties/99999999/disband", follow_redirects=False)
    s.check("nor can a stranger untie one", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")
    r = oc.post("/admin/parties/99999999/disband", follow_redirects=False)
    s.check("a party that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    _cleanup()
    return s


def _bill(booking_id):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.booking_bill(conn, booking_id)
    finally:
        conn.close()


if __name__ == "__main__":
    print(run().report())
