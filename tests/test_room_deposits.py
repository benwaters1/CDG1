"""Taking a deposit on a stay, and dating the rest.

A room booking was all-or-nothing: the whole amount at checkout, or none of it.
Workshops have had a deposit and a dated balance since the start —
resolve_deposit_percent is called for "workshop" and "restaurant" and was never
called for "room" — so a €2,700 stay booked eight months out asked a stranger
for all of it up front or nothing at all.

The things that make this safe to ship, and what each check is for:

  - ZERO IS THE DEFAULT, and at zero nothing changes. A deposit changes what a
    card is actually charged, so it has to be a decision somebody makes rather
    than something that arrives on a deploy. Every stay booked before this
    existed also has no schedule, so the no-schedule path is the common one and
    is checked first.

  - THE CHARGE MUST MATCH THE RECEIPT. With a deposit, Stripe is sent ONE line
    for the deposit rather than the itemised stay. Itemising the nights and the
    extras and then charging 30% of the sum puts numbers on the guest's receipt
    that do not add up to what left their account — and the extras must not be
    billed a second time on top.

  - `owed` STAYS THE ONE FIGURE. The reminder, the outlook and the guest's own
    page all read booking_bill, which recomputes the stay from the rates. A
    stored balance_amount would go stale the moment dates change; the schedule
    says WHEN, the bill says HOW MUCH.

  - A DUE DATE CANNOT BE IN THE PAST. A stay booked for next week would
    otherwise get a balance that fell due before it was taken.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZDEP"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key IN "
                 "('room_deposit_percent', 'room_balance_due_days_before')")
    conn.execute("DELETE FROM deposit_rules WHERE category = 'room'")
    conn.commit()
    conn.close()


def _set(key, value):
    conn = db()
    conn.execute("""INSERT INTO app_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                 (key, str(value)))
    conn.commit()
    conn.close()


def _book(total, days_out=90, nights=3, paid=False):
    conn = db()
    room = _harness.ensure_room()
    arrival = _house_today() + timedelta(days=days_out)
    was = m.send_email
    m.send_email = lambda *a, **k: True
    try:
        with m.app.test_request_context("/"):
            _ref, token = m.create_booking(
                conn, room, f"{TAG} Guest", f"{TAG.lower()}@example.invalid", "",
                arrival, arrival + timedelta(days=nights), 2, None, [],
                payment_status="paid" if paid else "unpaid",
                total_price_override=total)
    finally:
        m.send_email = was
    # create_booking leaves a stay 'pending' — the owner confirms it. Both the
    # reminder and the outlook deliberately only count confirmed money, so a
    # fixture that skips this measures nothing.
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE manage_token = ?",
                 (token,))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE manage_token = ?", (token,)).fetchone()
    conn.close()
    return row, arrival


def _house_today():
    """What day it is AT THE HOUSE.

    Not _house_today(), which is the day on whatever machine is running the
    tests. The Ariege and this laptop disagree for part of every day, and
    a suite built on the second is testing where the developer is sitting.
    """
    return m.datetime.now(m.LOCAL_TZ).date()


def run():
    s = Suite("Room deposits")
    _cleanup()
    clients()

    s.section("With no deposit set, a stay behaves exactly as it did")
    b, _arrival = _book(900.0)
    s.check("no deposit is recorded", b["deposit_amount"] is None,
            detail=f"got {b['deposit_amount']}")
    s.check("no balance either", b["balance_amount"] is None)
    s.check("and no due date", b["balance_due_date"] is None,
            detail="a schedule appeared on a house that has not asked for one")
    conn = db()
    bill = m.booking_bill(conn, b["id"])
    conn.close()
    s.check("the bill still says what is owed", bill["owed"] > 0)
    s.check("and reports no schedule", bill["balance_due_date"] is None
            and bill["deposit"] is None)

    s.section("Set a percentage and the stay splits")
    _set("room_deposit_percent", 30)
    _set("room_balance_due_days_before", 14)
    b, arrival = _book(900.0)
    s.check("30% of 900 is taken now", abs((b["deposit_amount"] or 0) - 270.0) < 0.01,
            detail=f"got {b['deposit_amount']}")
    s.check("and 630 is left", abs((b["balance_amount"] or 0) - 630.0) < 0.01,
            detail=f"got {b['balance_amount']}")
    s.check("the two add up to the stay",
            abs((b["deposit_amount"] or 0) + (b["balance_amount"] or 0) - 900.0) < 0.01,
            detail="a guest paying both would not pay for the stay")
    s.check("due fourteen days before arrival",
            b["balance_due_date"] == (arrival - timedelta(days=14)).isoformat(),
            detail=f"got {b['balance_due_date']}, arrival {arrival}")

    s.section("A due date is never in the past")
    conn = db()
    deposit, balance, due = m.room_payment_schedule(
        conn, _house_today() + timedelta(days=3), 900.0, 2)
    conn.close()
    s.check("a stay next week still gets a workable date",
            due >= _house_today().isoformat(),
            detail=f"{due} — the balance fell due before the booking was taken")
    s.check("and it is still split", balance > 0)

    s.section("Paying the deposit is not paying for the stay")
    # payment_status says what has been RECEIVED. Marking a deposit-paid stay
    # as paid would take it off every list of who still owes money.
    b, _a = _book(900.0, paid=True)
    s.check("the deposit is stamped as paid", b["deposit_paid_at"] is not None)
    s.check("but the stay is not marked paid", b["payment_status"] != "paid",
            detail=f"payment_status={b['payment_status']!r} — a stay with 630 "
                   "outstanding was recorded as settled")

    s.section("What the reminder and the outlook read")
    conn = db()
    bill = m.booking_bill(conn, b["id"])
    conn.close()
    s.check("the bill carries the due date", bill["balance_due_date"] is not None)
    s.check("and names the deposit", bill["deposit"] is not None)
    s.check("owed is still the single figure for what is outstanding",
            bill["owed"] > 0,
            detail="the reminder, the outlook and the guest page all read this")

    s.section("The chase goes out against the due date, not arrival")
    # A stay 90 days out with a balance due in 76 must be chased on the due
    # date. Keying off arrival would chase it two and a half months late.
    _cleanup()
    _set("room_deposit_percent", 30)
    _set("room_balance_due_days_before", 14)
    b, arrival = _book(900.0, days_out=20)      # balance due in 6 days
    sent = []
    was = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]
    conn = db()
    try:
        with m.app.test_request_context("/"):
            result = m.run_room_balance_reminder_job(conn, 7)
    finally:
        conn.close()
        m.send_email = was
    s.check("the guest is chased inside the window",
            any(TAG.lower() in t for t in sent),
            detail=f"{sent} — {result}; the balance falls due in six days and "
                   "arrival is twenty away")

    s.section("And a stay with no schedule is still chased on arrival")
    # Every booking taken before this existed. Keying only off balance_due_date
    # would silently stop chasing all of them.
    _cleanup()
    b2, _a2 = _book(900.0, days_out=4)
    sent.clear()
    was = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]
    conn = db()
    try:
        with m.app.test_request_context("/"):
            m.run_room_balance_reminder_job(conn, 7)
    finally:
        conn.close()
        m.send_email = was
    s.check("it still goes out", any(TAG.lower() in t for t in sent),
            detail=f"{sent} — bookings with no deposit schedule stopped being "
                   "chased at all")

    s.section("The outlook expects the money when it falls due")
    _cleanup()
    _set("room_deposit_percent", 30)
    _set("room_balance_due_days_before", 60)
    conn = db()
    before = m.cash_outlook(conn, months=6)
    conn.close()
    b3, arrival3 = _book(1000.0, days_out=100)   # due 60 days before arrival
    conn = db()
    after = m.cash_outlook(conn, months=6)
    conn.close()
    due_month = (arrival3 - timedelta(days=60)).replace(day=1)
    arr_month = arrival3.replace(day=1)
    def row_for(o, first):
        return next((r for r in o["rows"] if r["start"] == first), None)
    if due_month != arr_month:
        a_before, a_after = row_for(before, due_month), row_for(after, due_month)
        if a_before and a_after:
            s.check("the income lands in the month the balance is due",
                    a_after["in_rooms"] > a_before["in_rooms"],
                    detail=f"{a_before['in_rooms']} -> {a_after['in_rooms']} "
                           f"for {due_month}")
        b_before, b_after = row_for(before, arr_month), row_for(after, arr_month)
        if b_before and b_after:
            s.check("and not in the month they arrive",
                    abs(b_after["in_rooms"] - b_before["in_rooms"]) < 0.01,
                    detail=f"{b_before['in_rooms']} -> {b_after['in_rooms']} "
                           f"for {arr_month}")
    else:
        s.check("the two months differ enough to tell apart", False,
                detail="fixture dates collapsed into one month")

    s.section("A rule can override the house percentage")
    # deposit_rules already scopes by date and party size for the other two
    # categories. Rooms now use the same table.
    _cleanup()
    _set("room_deposit_percent", 30)
    conn = db()
    conn.execute(
        """INSERT INTO deposit_rules (category, deposit_percent, min_party_size, created_at)
           VALUES ('room', 50, 2, ?)""", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()
    b4, _a4 = _book(1000.0)
    s.check("a party of two gets the 50% rule, not the 30% default",
            abs((b4["deposit_amount"] or 0) - 500.0) < 0.01,
            detail=f"got {b4['deposit_amount']}")

    s.section("The owner can set it without touching the database")
    oc, ec, _owner, _emp = clients()
    page = oc.get("/admin/deposit-rules")
    s.check("the deposit page loads", page.status_code == 200, page)
    s.check("and offers the room figures",
            "Room stays" in page.get_data(as_text=True),
            detail="the setting exists but nothing can set it")
    oc.post("/admin/deposit-rules/rooms",
            data={"room_deposit_percent": "25", "room_balance_due_days_before": "21"},
            follow_redirects=True)
    conn = db()
    s.check("the percentage saves",
            abs(m.room_payment_setting(conn, "room_deposit_percent") - 25.0) < 0.01)
    s.check("and the days with it",
            int(m.room_payment_setting(conn, "room_balance_due_days_before")) == 21)
    conn.close()
    oc.post("/admin/deposit-rules/rooms",
            data={"room_deposit_percent": "lots", "room_balance_due_days_before": "21"},
            follow_redirects=True)
    conn = db()
    s.check("junk is refused and the figure kept",
            abs(m.room_payment_setting(conn, "room_deposit_percent") - 25.0) < 0.01)
    conn.close()
    s.check("an employee cannot change it",
            ec.post("/admin/deposit-rules/rooms",
                    data={"room_deposit_percent": "0",
                          "room_balance_due_days_before": "1"}).status_code in (302, 403))
    conn = db()
    s.check("and it is still what the owner set",
            abs(m.room_payment_setting(conn, "room_deposit_percent") - 25.0) < 0.01)
    conn.close()

    s.section("A room rule can now be added at all")
    # The CHECK used to refuse the category outright.
    before = oc.get("/admin/deposit-rules").get_data(as_text=True)
    s.check("'Room stay' is offered as a rule category",
            'value="room"' in before,
            detail="rules could be written for everything except a room")

    _cleanup()
    return s
