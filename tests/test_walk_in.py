"""Somebody at the door, and the discount that never reached the bill.

TWO THINGS, and the second was found while building the first.

A ROOM BOOKING COULD ONLY BE MADE BY THE GUEST. The two callers of
create_booking were the public form and a Stripe session created by it, so a
walk-in on a wet Tuesday, or the telephone, could not be written down. The
house had to fill in the guest's own form pretending to be them.

THE DISCOUNT WAS ON THE STATEMENT AND NOT ON THE BILL. booking_bill recomputes
the nights from the rate card and read neither total_price nor discount_amount,
while guest_statement reads total_price. So a stay booked with a promo code had
two documents disagreeing about what was owed — and everything that asks a
guest for money reads booking_bill: the manage page, the balance reminder, what
we're owed, the account, and the pay button, which would have charged the full
rack rate back to somebody already promised less.

That is why the walk-in price is stored as a DISCOUNT rather than as a smaller
room total. A rewritten total_price would have been ignored by every one of
those, so a room sold at eighty would have been chased for two hundred.

The one thing this route must never do is let a room twice. That is checked
before anything else here, because it is the only mistake on the page a guest
cannot be talked round.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZWALK"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _room():
    conn = db()
    rid = _harness.ensure_room()["id"]
    row = conn.execute("SELECT * FROM rooms WHERE id = ?", (rid,)).fetchone()
    conn.close()
    return row


def _rack(room, a, d):
    conn = db()
    try:
        return m.compute_room_total(conn, room, a, d)
    finally:
        conn.close()


def _made(name):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE guest_name = ? "
                            "ORDER BY id DESC LIMIT 1", (f"{TAG} {name}",)).fetchone()
    finally:
        conn.close()


def _bill(booking_id):
    conn = db()
    try:
        return m.booking_bill(conn, booking_id)
    finally:
        conn.close()


def _post(client, room, a, d, name, **extra):
    data = {"room_id": str(room["id"]), "arrival_date": a.isoformat(),
            "departure_date": d.isoformat(), "guest_name": f"{TAG} {name}",
            "party_size": "2"}
    data.update(extra)
    return client.post("/admin/bookings/walk-in", data=data, follow_redirects=True)


def run():
    s = Suite("Walk-in")
    _cleanup()
    oc, ec, owner, emp = clients()
    room = _room()
    # Far out, so no other suite's fixture is holding these nights.
    a = date.today() + timedelta(days=400)
    d = a + timedelta(days=2)
    rack = _rack(room, a, d)

    s.section("The rate card gives a real number to work from")
    s.check("the room has a price", rack > 0, detail=f"{rack}")

    s.section("A booking with no email at all")
    # A walk-in may not have one and is standing in front of you. The booking
    # is worth more than the address.
    r = _post(oc, room, a, d, "Nomail")
    s.check("it is taken", r.status_code == 200, detail=f"HTTP {r.status_code}")
    made = _made("Nomail")
    s.check("and written down", bool(made), detail=f"{flashes(r)[:1]}")
    if made:
        s.check("confirmed, not left pending", made["status"] == "confirmed",
                detail=f"{made['status']}")
        s.check("with no email on it", not (made["guest_email"] or "").strip())
        s.check("and it has a reference to quote", bool(made["reference_code"]))
        s.check("priced from the rate card",
                abs(_bill(made["id"])["total"] - rack) < 0.01,
                detail=f"{_bill(made['id'])['total']} vs {rack}")

    s.section("The same room, the same nights, twice")
    # The only mistake on this page a guest cannot be talked round.
    r = _post(oc, room, a, d, "Double")
    s.check("the second is refused", not _made("Double"),
            detail="the room was let twice")
    s.check("and it says why",
            any("already taken" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("A price the house sets goes on the bill as a discount")
    # Stored as a smaller total_price it would be ignored by booking_bill, and
    # a room sold at eighty would be chased for the rack rate.
    a2 = a + timedelta(days=10)
    d2 = a2 + timedelta(days=2)
    rack2 = _rack(room, a2, d2)
    _post(oc, room, a2, d2, "Cheap", total_price=f"{rack2 - 120:.2f}")
    cheap = _made("Cheap")
    s.check("it is taken", bool(cheap))
    if cheap:
        bill = _bill(cheap["id"])
        s.check("the bill charges the agreed price, not the rack rate",
                abs(bill["total"] - (rack2 - 120)) < 0.01,
                detail=f"{bill['total']} vs {rack2 - 120:.2f}")
        s.check("and what they owe agrees with it",
                abs(bill["owed"] - (rack2 - 120)) < 0.01, detail=f"{bill['owed']}")
        labels = [l["label"] for l in bill["lines"]]
        s.check("the nights still show at the advertised price",
                any(f"{rack2:.2f}" == f"{l['amount']:.2f}" for l in bill["lines"]),
                detail=f"{[(l['label'], l['amount']) for l in bill['lines']]}")
        s.check("with the reduction on its own line",
                any("discount" in l.lower() for l in labels), detail=f"{labels}")
        s.check("and the rows add up to the total",
                abs(sum(l["amount"] for l in bill["lines"]) - bill["total"]) < 0.01,
                detail=f"{[l['amount'] for l in bill['lines']]} vs {bill['total']}")

    s.section("More than the rate card is ignored, not charged")
    a3 = a + timedelta(days=20)
    d3 = a3 + timedelta(days=2)
    rack3 = _rack(room, a3, d3)
    _post(oc, room, a3, d3, "Over", total_price=f"{rack3 + 500:.2f}")
    over = _made("Over")
    s.check("it is taken", bool(over))
    if over:
        s.check("at the rate card, not above it",
                abs(_bill(over["id"])["total"] - rack3) < 0.01,
                detail=f"{_bill(over['id'])['total']} vs {rack3}")

    s.section("Money handed over at the desk goes on with it")
    a4 = a + timedelta(days=30)
    d4 = a4 + timedelta(days=2)
    rack4 = _rack(room, a4, d4)
    _post(oc, room, a4, d4, "Paid", amount_taken="150", method="cash")
    paid = _made("Paid")
    s.check("it is taken", bool(paid))
    if paid:
        s.check("and comes off what they owe",
                abs(_bill(paid["id"])["owed"] - (rack4 - 150)) < 0.01,
                detail=f"{_bill(paid['id'])['owed']} vs {rack4 - 150}")
        conn = db()
        line = conn.execute(
            "SELECT * FROM booking_payments WHERE booking_id = ? ORDER BY id DESC LIMIT 1",
            (paid["id"],)).fetchone()
        conn.close()
        s.check("as a ledger line, not just a bigger number",
                bool(line) and abs(line["amount"] - 150) < 0.01,
                detail=f"{dict(line) if line else None}")
        s.check("saying how it was taken", bool(line) and line["method"] == "cash")
        s.check("and who took it",
                bool(line) and line["taken_by_user_id"] == owner["id"])

    s.section("What it refuses")
    for label, extra, why in (
        ("no name", {"guest_name": "  "}, "name"),
        ("leaving before arriving", {"departure_date": (a - timedelta(days=1)).isoformat()}, "after"),
        ("an email that is not one", {"guest_email": "not-an-address"}, "email"),
    ):
        before = _count()
        r = oc.post("/admin/bookings/walk-in",
                    data=dict({"room_id": str(room["id"]),
                               "arrival_date": (a + timedelta(days=50)).isoformat(),
                               "departure_date": (a + timedelta(days=52)).isoformat(),
                               "guest_name": f"{TAG} Refused", "party_size": "2"}, **extra),
                    follow_redirects=True)
        s.check(f"{label}: nothing is written", _count() == before,
                detail=f"{flashes(r)[:1]}")
        s.check(f"{label}: and it says which field",
                any(why in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("What the guest typed comes back after a refusal")
    r = oc.post("/admin/bookings/walk-in",
                data={"room_id": str(room["id"]), "guest_name": "  ",
                      "arrival_date": (a + timedelta(days=60)).isoformat(),
                      "departure_date": (a + timedelta(days=62)).isoformat(),
                      "guest_phone": "+33 5 61 00 00 00", "party_size": "4"})
    html = r.get_data(as_text=True)
    s.check("the telephone is still in the box", "+33 5 61 00 00 00" in html,
            detail="somebody at a desk retypes it while a guest waits")
    s.check("and the party size", 'value="4"' in html)

    s.section("Reception picks from what is free, and can see what is not")
    # The page used to list every room and say "taken" only after the button
    # was pressed, with somebody standing in front of you.
    page = oc.get(f"/admin/bookings/walk-in?arrival_date={a.isoformat()}"
                  f"&departure_date={d.isoformat()}")
    html = page.get_data(as_text=True)
    s.check("the picker opens for those nights", page.status_code == 200,
            detail=f"HTTP {page.status_code}")
    s.check("the room taken by the first booking is shown as taken",
            "is-taken" in html, detail="a full house looks the same as an empty one")
    s.check("and cannot be chosen", "disabled" in html,
            detail="reception can pick a room that is gone")
    s.check("it is still named rather than hidden", room["name"] in html,
            detail="reception has to be able to say WHICH room is gone")
    s.check("and the free nights carry a price for the stay",
            "€" in html, detail="no price to quote")

    s.section("A different set of nights asks the question again")
    far = a + timedelta(days=120)
    html2 = oc.get(f"/admin/bookings/walk-in?arrival_date={far.isoformat()}"
                   f"&departure_date={(far + timedelta(days=2)).isoformat()}"
                   ).get_data(as_text=True)
    s.check("nights nobody has booked come back free",
            html2.count("is-taken") < html.count("is-taken"),
            detail=f"{html2.count('is-taken')} taken then vs "
                   f"{html.count('is-taken')} taken now")

    s.section("Guards")
    before = _count()
    code = ec.post("/admin/bookings/walk-in",
                   data={"room_id": str(room["id"]),
                         "arrival_date": (a + timedelta(days=70)).isoformat(),
                         "departure_date": (a + timedelta(days=72)).isoformat(),
                         "guest_name": f"{TAG} Sneaky"}).status_code
    s.check("an employee cannot take a booking", code in (302, 403), detail=f"HTTP {code}")
    s.check("and none was written", _count() == before)
    s.check("the page itself is owner-only",
            ec.get("/admin/bookings/walk-in").status_code in (302, 403))
    s.check("and it opens for the owner",
            oc.get("/admin/bookings/walk-in").status_code == 200)

    _cleanup()
    return s


def _count():
    conn = db()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE guest_name LIKE ?",
                            (TAG + "%",)).fetchone()["c"]
    finally:
        conn.close()
