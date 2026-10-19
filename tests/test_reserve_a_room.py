"""Reserve a room for a guest, at an agreed price, and let them finish it.

18 October 2026, the owner, about a booking agreed by email: "i need to be
able to enter the amount for the booking ... where i add it then they fill in
the rest of the details (like a reserve room option) then they pay".

WHAT THIS PINS.
  - Add a booking, "Reserve it and send them a link": the room is taken from
    then, at the price typed -- above the rate card as well as below -- and
    the stay waits on the guest, held until a time.
  - One letter goes, with the link; not "request received", and the house is
    not told it needs to answer.
  - It is not on the list of requests the house owes an answer, and the
    48-hour expiry leaves it alone; its own hold ends it, without a "declined"
    letter, unless money is on it.
  - The guest's page asks the booking form's details and the party, held to
    the room's limits; the taxe de sejour is stamped for the party they give;
    then the card page.
  - Paying confirms it.
"""
from datetime import datetime, timedelta, timezone

from _harness import BOOKING_DETAILS, Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZRESV"


def _cleanup(conn):
    ids = "(SELECT id FROM bookings WHERE guest_email LIKE 'zzresv.%')"
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {ids}")
    conn.execute(f"DELETE FROM guests WHERE email LIKE 'zzresv.%'")
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE 'zzresv.%'")
    conn.execute("DELETE FROM mail_log WHERE to_address LIKE 'zzresv.%'")
    conn.commit()


def _letters(conn, to):
    return [r["template_key"] or r["subject"] for r in conn.execute(
        "SELECT template_key, subject FROM mail_log WHERE to_address = ? ORDER BY id", (to,))]


class _Session(dict):
    """Raises on attribute access the way a real StripeObject does (as in
    test_booking_payment), so the code reaches it through sval()/smeta()."""
    def __getattr__(self, name):
        raise AttributeError(name)


def run():
    s = Suite("Reserve a room for a guest")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    owner = clients()[0]
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (_harness.ensure_room()["id"],)).fetchone()
    a = house_today() + timedelta(days=150)
    while not m.is_range_available(conn, room["id"], a, a + timedelta(days=2))[0]:
        a += timedelta(days=5)
    b = a + timedelta(days=2)
    rack = m.compute_room_total(conn, room, a, b)
    agreed = round(rack + 75, 2)
    to = "zzresv.inna@example.invalid"

    s.section("Reserved at the desk, at the agreed price")
    r = owner.post("/admin/bookings/walk-in", data={
        "mode": "reserve", "room_id": str(room["id"]), "arrival_date": a.isoformat(),
        "departure_date": b.isoformat(), "guest_email": to, "guest_name": "",
        "party_size": "2", "guests_under_18": "0", "total_price": f"{agreed:.2f}",
        "hold_days": "2"}, follow_redirects=True)
    stay = conn.execute("SELECT * FROM bookings WHERE guest_email = ?", (to,)).fetchone()
    s.check("a stay is written, with only an email", stay is not None, detail=r.get_data(as_text=True)[-400:])
    if not stay:
        return
    s.check("waiting on the guest, not confirmed", stay["status"] == "pending")
    held = m.parse_datetime_iso(stay["reserved_until"])
    s.check("held for the two days chosen", held is not None
            and abs((held - datetime.now(timezone.utc)).total_seconds() - 2 * 86400) < 600,
            detail=str(stay["reserved_until"]))
    s.check("the room is off sale from now",
            not m.is_range_available(conn, room["id"], a, b)[0])
    bill = m.booking_bill(conn, stay["id"])
    room_lines = round(sum(l["amount"] for l in bill["lines"] if l.get("kind") != "city_tax"), 2)
    s.check("at the price typed, above the rate card", abs(room_lines - agreed) < 0.01,
            detail=f"{room_lines} vs {agreed} (rack {rack})")
    s.check("and the bill adds up", abs(sum(l["amount"] for l in bill["lines"]) - bill["total"]) < 0.01)
    page = r.get_data(as_text=True)
    s.check("the owner is told what was done", "reserved for" in page and "above the rate card" in page)
    s.check("and the stay's page says it is waiting on them, with the link",
            "Reserved for them" in owner.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True))

    s.section("One letter: the link to finish")
    letters = _letters(conn, to)
    s.check("the reservation letter is written", "room_reserved" in letters, detail=str(letters))
    s.check("and nothing saying a request is awaiting confirmation",
            "room_request_received" not in letters and "room_confirmed" not in letters, detail=str(letters))
    s.check("nor the house told it needs an answer", not conn.execute(
        "SELECT 1 FROM mail_log WHERE subject LIKE 'Needs you%' AND subject LIKE ?",
        (f"%{stay['reference_code']}%",)).fetchone())

    s.section("The house does not owe it an answer, and the 48 hours leave it")
    s.check("not among the requests waiting on the house",
            stay["id"] not in [w["booking"]["id"] for w in m.bookings_awaiting_answer(
                conn, now=datetime.now(timezone.utc) + timedelta(days=3), hours=0)])
    conn.execute("UPDATE bookings SET created_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(days=4)).isoformat(), stay["id"]))
    conn.commit()
    with m.app.test_request_context():
        m.expire_stale_pending_bookings(conn)
    s.check("an old reservation still inside its hold is kept", conn.execute(
        "SELECT status FROM bookings WHERE id = ?", (stay["id"],)).fetchone()[0] == "pending")

    s.section("The guest finishes it")
    guest = m.app.test_client()
    url = f"/booking/{stay['manage_token']}/finish"
    page = guest.get(url)
    body = page.get_data(as_text=True)
    s.check("their page opens", page.status_code == 200)
    s.check("never indexed", 'name="robots" content="noindex' in body)
    s.check("with the price agreed", f"{agreed:,.2f}" in body)
    base = {"guest_name": "Inna Mona", "guest_phone": "+380 44 123 4567", "adults": "2",
            "guests_under_18": "0", "agree_terms": "on", **BOOKING_DETAILS}
    too_many = dict(base, adults=str((room["max_adults"] or room["max_occupancy"]) + 1))
    guest.post(url, data=too_many)
    s.check("more adults than the room takes is refused", conn.execute(
        "SELECT details_done_at FROM bookings WHERE id = ?", (stay["id"],)).fetchone()[0] is None)
    guest.post(url, data=dict(base, guest_address=""))
    s.check("so is a missing address", conn.execute(
        "SELECT details_done_at FROM bookings WHERE id = ?", (stay["id"],)).fetchone()[0] is None)
    r = guest.post(url, data=base)
    done = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["id"],)).fetchone()
    s.check("their details are kept", done["details_done_at"] and done["guest_name"] == "Inna Mona"
            and done["guest_address"] == BOOKING_DETAILS["guest_address"]
            and done["estimated_arrival_time"] == BOOKING_DETAILS["estimated_arrival_time"])
    tax, _n, _rate = m.compute_city_tax(conn, 2, 0, 2)
    s.check("the taxe de sejour stamped for the party they gave", abs((done["city_tax"] or 0) - tax) < 0.01,
            detail=f"{done['city_tax']} vs {tax}")
    s.check("and they are sent on to pay", r.status_code in (302, 303)
            and f"/book/pay/{stay['manage_token']}" in r.headers.get("Location", ""),
            detail=r.headers.get("Location", ""))
    s.check("coming back, the page says it is done and offers the card",
            "your details are in" in guest.get(url).get_data(as_text=True))

    s.section("Paying confirms it")
    owed = m.booking_bill(conn, stay["id"])["owed"]
    session = _Session({"id": "cs_test_zzresv", "payment_status": "paid",
                        "amount_total": int(round(owed * 100)), "payment_intent": "pi_test_zzresv",
                        "metadata": _Session({"kind": "room_balance", "booking_id": str(stay["id"])})})
    with m.app.test_request_context():
        m.mark_booking_payment_paid(conn, session)
    s.check("the stay is confirmed", conn.execute(
        "SELECT status FROM bookings WHERE id = ?", (stay["id"],)).fetchone()[0] == "confirmed")
    s.check("with its confirmation letter", "room_confirmed" in _letters(conn, to), detail=str(_letters(conn, to)))

    s.section("A hold that runs out gives the room back")
    to2 = "zzresv.quiet@example.invalid"
    owner.post("/admin/bookings/walk-in", data={
        "mode": "reserve", "room_id": str(room["id"]), "arrival_date": (b + timedelta(days=10)).isoformat(),
        "departure_date": (b + timedelta(days=12)).isoformat(), "guest_email": to2,
        "party_size": "2", "guests_under_18": "0", "hold_days": "1"})
    quiet = conn.execute("SELECT * FROM bookings WHERE guest_email = ?", (to2,)).fetchone()
    s.check("a second reservation", quiet is not None)
    if not quiet:
        return
    with m.app.test_request_context():
        s.check("inside its hold it is kept", m.release_lapsed_reservations(conn) == 0)
        released = m.release_lapsed_reservations(conn, now=datetime.now(timezone.utc) + timedelta(days=2))
    s.check("after it, released", released == 1 and conn.execute(
        "SELECT status FROM bookings WHERE id = ?", (quiet["id"],)).fetchone()[0] == "declined")
    s.check("without a 'request declined' letter", "room_declined" not in _letters(conn, to2),
            detail=str(_letters(conn, to2)))
    s.check("and their link says the hold has ended",
            "has now been released" in guest.get(f"/booking/{quiet['manage_token']}/finish").get_data(as_text=True))
    s.check("a stay nobody reserved has no such page",
            guest.get(f"/booking/not-a-token/finish").status_code == 404)


if __name__ == "__main__":
    print(run().report())
