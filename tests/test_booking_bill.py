"""What a stay costs, what has been received, what is still owed.

A room booking had none of this. payment_status could only say
unpaid/paid/refunded, which cannot express "paid the deposit" or "added a night
and owes the difference" — so there was nowhere to put the cost of anything a
guest might add later, which is the real reason they could add nothing.
Workshops already had deposits and balances; stays did not.

The checks worth having are the ones about money not going missing: payments
accumulate rather than overwrite, a refund reduces what was received rather than
what the stay cost, and adding something later increases what is owed.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZBILL"


def _booking(nights=3, paid=0.0):
    room = ensure_room()
    conn = db()
    conn.execute("UPDATE rooms SET price_per_night = 250, min_nights = 1 WHERE id = ?",
                 (room["id"],))
    arrival = date.today() + timedelta(days=600)
    conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
           departure_date, party_size, status, reference_code, manage_token,
           created_at, amount_paid)
           VALUES (?,?,?,?,?,2,'confirmed',?,?,datetime('now'),?)""",
        (room["id"], f"{TAG} guest", f"{TAG.lower()}@example.invalid",
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         f"{TAG}1", f"tok{TAG}1", paid))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}1",)).fetchone()["id"]
    conn.close()
    return bid


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Booking bill")
    clients()
    _cleanup()

    s.section("A stay is priced and owed in full")
    bid = _booking(nights=3)
    conn = db()
    bill = m.booking_bill(conn, bid)
    conn.close()
    s.check("three nights at 250 totals 750", bill["total"] == 750.0,
            detail=f"got {bill['total']}")
    s.check("nothing has been received", bill["paid"] == 0.0)
    s.check("so all of it is owed", bill["owed"] == 750.0, detail=f"got {bill['owed']}")

    s.section("An extra added later increases what is owed")
    conn = db()
    conn.execute("""INSERT INTO extras (name, price, active, sort_order, category)
                    VALUES (?, 120, 1, 1, 'room')""", (f"{TAG} transfer",))
    conn.commit()
    extra = conn.execute("SELECT * FROM extras WHERE name = ?", (f"{TAG} transfer",)).fetchone()
    m.add_booking_extra(conn, "room", bid, extra, 1)
    conn.commit()
    bill = m.booking_bill(conn, bid)
    conn.close()
    # The whole point of the bill: something can now be added to a stay and the
    # cost has somewhere to go.
    s.check("the total rises by the extra", bill["total"] == 870.0, detail=f"got {bill['total']}")
    s.check("and appears as its own line", len(bill["lines"]) == 2,
            detail=f"{[l['label'] for l in bill['lines']]}")
    s.check("and is owed", bill["owed"] == 870.0, detail=f"got {bill['owed']}")

    s.section("Payments accumulate rather than overwrite")
    conn = db()
    m.record_booking_payment(conn, bid, 300.00, reference="pi_deposit")
    conn.commit()
    bill = m.booking_bill(conn, bid)
    s.check("a deposit is recorded", bill["paid"] == 300.0, detail=f"got {bill['paid']}")
    s.check("the rest is still owed", bill["owed"] == 570.0, detail=f"got {bill['owed']}")
    # A second payment must add to the first. Overwriting is how a deposit
    # disappears the moment the balance is taken.
    m.record_booking_payment(conn, bid, 570.00)
    conn.commit()
    bill = m.booking_bill(conn, bid)
    status = conn.execute("SELECT payment_status FROM bookings WHERE id = ?",
                          (bid,)).fetchone()["payment_status"]
    conn.close()
    s.check("paying the balance clears it", bill["owed"] == 0.0, detail=f"got {bill['owed']}")
    s.check("total received is both payments", bill["paid"] == 870.0, detail=f"got {bill['paid']}")
    s.check("and the booking reads as paid", status == "paid", detail=f"got {status}")

    s.section("A refund reduces what was received, not what it cost")
    conn = db()
    conn.execute(
        """INSERT INTO refunds (category, booking_id, reference_code, guest_name,
           guest_email, amount, reason, method, created_at)
           VALUES ('room', ?, ?, ?, ?, 120, 'transfer not needed', 'stripe', datetime('now'))""",
        (bid, f"{TAG}1", f"{TAG} guest", f"{TAG.lower()}@example.invalid"))
    conn.commit()
    bill = m.booking_bill(conn, bid)
    conn.close()
    # The stay still cost what it cost. A bill that quietly lowers the total to
    # hide a refund is one somebody has to reconcile by hand.
    s.check("the total is unchanged", bill["total"] == 870.0, detail=f"got {bill['total']}")
    s.check("the refund is shown", bill["refunded"] == 120.0, detail=f"got {bill['refunded']}")
    s.check("received drops by the refund", bill["paid"] == 750.0, detail=f"got {bill['paid']}")
    s.check("so it is owed again", bill["owed"] == 120.0, detail=f"got {bill['owed']}")

    s.section("A cancelled extra stops being charged")
    conn = db()
    line = conn.execute(
        "SELECT id FROM booking_extras WHERE booking_id = ? LIMIT 1", (bid,)).fetchone()
    if line:
        m.cancel_booking_extra(conn, line["id"])
        conn.commit()
        bill = m.booking_bill(conn, bid)
        s.check("the total comes back down", bill["total"] == 750.0, detail=f"got {bill['total']}")
        s.check("and the line is gone from the bill", len(bill["lines"]) == 1)
    conn.close()

    s.section("The guest can see it")
    pub = m.app.test_client()
    html = pub.get(f"/book/manage/tok{TAG}1").get_data(as_text=True)
    s.check("the bill is on their booking page", "Your bill" in html)
    s.check("with the total", "750.00" in html)

    s.section("Overpaying shows as credit, not a negative balance")
    conn = db()
    m.record_booking_payment(conn, bid, 500.00)
    conn.commit()
    bill = m.booking_bill(conn, bid)
    conn.close()
    s.check("nothing is owed", bill["owed"] == 0.0, detail=f"got {bill['owed']}")
    s.check("and the excess reads as credit", bill["overpaid"] > 0,
            detail=f"got {bill['overpaid']}")

    _cleanup()
    return s
