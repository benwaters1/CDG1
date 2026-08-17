"""Paying for a room stay online.

Workshop guests have been able to settle up since the start. Room guests could
only be told "we'll take this on arrival" — even after adding an extra or a
night to their own booking, which put money on the bill with no way to pay it.

The dangerous half is recording the payment. Stripe retries webhooks and a
guest may reload the success page, so the same Checkout Session arrives more
than once. Crediting it twice takes the money off what they owe twice, and
somebody goes home believing they have paid when they have not.
"""
from datetime import date, timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZPAY"


class FakeStripeObject(dict):
    """Raises on attribute access the way a real StripeObject does, so code
    that reaches for .status or .get() instead of sval()/smeta() fails here
    rather than in production."""
    def __getattr__(self, name):
        raise AttributeError(name)


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE '%Payment received%'")
    conn.commit()
    conn.close()


def _booking(price=200.0, nights=2, status="confirmed"):
    conn = db()
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night, sort_order)
           VALUES (?, ?, 1, 4, ?, 995)""", (f"{TAG} Room", _harness.secrets_token(), price))
    conn.commit()
    room_id = conn.execute("SELECT id FROM rooms WHERE name = ?", (f"{TAG} Room",)).fetchone()["id"]
    arrival = date.today() + timedelta(days=280)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?)""",
        (room_id, f"{TAG}1", f"tok{TAG}1", f"{TAG} guest", f"{TAG.lower()}@example.invalid",
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(), status,
         _harness.datetime_now()))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}1",)).fetchone()["id"]
    conn.close()
    return bid


def _session(bid, cents, sid="cs_test_room_balance"):
    return FakeStripeObject({
        "id": sid,
        "payment_status": "paid",
        "amount_total": cents,
        "payment_intent": "pi_test_room",
        "metadata": FakeStripeObject({"kind": "room_balance", "booking_id": str(bid)}),
    })


def run():
    s = Suite("Booking payment")
    _cleanup()
    bid = _booking(price=200.0, nights=2)

    s.section("What is owed before anything is paid")
    conn = db()
    bill = m.booking_bill(conn, bid)
    conn.close()
    s.check("two nights at 200 is 400", bill["total"] == 400.0, detail=f"got {bill['total']}")
    s.check("nothing received yet", bill["paid"] == 0.0, detail=f"got {bill['paid']}")
    s.check("so all of it is owed", bill["owed"] == 400.0, detail=f"got {bill['owed']}")

    s.section("A payment reaches the bill")
    conn = db()
    with m.app.test_request_context():
        m.mark_booking_payment_paid(conn, _session(bid, 40000))
    bill = m.booking_bill(conn, bid)
    conn.close()
    s.check("the payment is received", bill["paid"] == 400.0, detail=f"got {bill['paid']}")
    s.check("in euros, not cents", bill["paid"] != 40000.0, detail=f"got {bill['paid']}")
    s.check("nothing is left to pay", bill["owed"] == 0.0, detail=f"got {bill['owed']}")
    s.check("and the stay reads as paid",
            bill["booking"]["payment_status"] == "paid",
            detail=f"got {bill['booking']['payment_status']!r}")

    s.section("The same payment arriving twice is only counted once")
    # The success redirect and the webhook both call this, and Stripe retries.
    conn = db()
    with m.app.test_request_context():
        m.mark_booking_payment_paid(conn, _session(bid, 40000))
    bill = m.booking_bill(conn, bid)
    rows = conn.execute("SELECT COUNT(*) AS c FROM booking_payments WHERE booking_id = ?",
                        (bid,)).fetchone()["c"]
    conn.close()
    s.check("replaying the same session changes nothing", bill["paid"] == 400.0,
            detail=f"got {bill['paid']}")
    s.check("and only one payment is recorded", rows == 1, detail=f"got {rows} rows")

    s.section("A second, genuinely different payment does count")
    # An extra added after the stay was paid for leaves a new balance.
    conn = db()
    with m.app.test_request_context():
        m.mark_booking_payment_paid(conn, _session(bid, 5000, sid="cs_test_room_balance_2"))
    bill = m.booking_bill(conn, bid)
    conn.close()
    s.check("a different Stripe session is credited", bill["paid"] == 450.0,
            detail=f"got {bill['paid']}")
    s.check("leaving the guest in credit rather than losing it",
            bill["overpaid"] == 50.0, detail=f"got {bill['overpaid']}")

    s.section("The receipt survives having no email provider")
    # Checked here, before the cleanup below wipes the outbox — send_email falls
    # back to email_outbox on its own connection, which cannot take a write lock
    # while the payment's transaction is open. That is how a workshop deposit
    # receipt was being lost to "database is locked".
    conn = db()
    held = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE subject LIKE '%Payment received%'"
    ).fetchone()["c"]
    conn.close()
    s.check("the payments above left receipts in the outbox rather than losing them",
            held >= 1, detail=f"{held} rows")

    s.section("A payment for something else is ignored")
    _cleanup()
    bid = _booking(price=200.0, nights=2)
    conn = db()
    other = FakeStripeObject({
        "id": "cs_test_not_ours", "payment_status": "paid", "amount_total": 9900,
        "metadata": FakeStripeObject({"kind": "workshop_deposit", "booking_id": str(bid)}),
    })
    with m.app.test_request_context():
        m.mark_booking_payment_paid(conn, other)
    bill = m.booking_bill(conn, bid)
    conn.close()
    s.check("a workshop payment does not credit a room stay", bill["paid"] == 0.0,
            detail=f"got {bill['paid']}")

    s.section("Nothing to pay means no checkout")
    conn = db()
    m.record_booking_payment(conn, bid, 400.0)
    conn.commit()
    nothing = m.start_booking_stripe_payment(conn, bid)
    conn.close()
    s.check("a settled stay cannot be sent to a checkout for zero", nothing is None,
            detail=f"got {nothing!r}")

    s.section("The guest's page and the pay link")
    pub = m.app.test_client()
    r = pub.get(f"/book/manage/tok{TAG}1")
    s.check("the manage page renders", r.status_code == 200, detail=f"HTTP {r.status_code}")
    # Stripe is pinned off in tests, so this must refuse politely, not error.
    r = pub.get(f"/book/pay/tok{TAG}1", follow_redirects=True)
    s.check("paying with no Stripe configured is refused, not an error",
            r.status_code == 200, detail=f"HTTP {r.status_code}")
    r = pub.get("/book/pay/not-a-real-token")
    s.check("an unknown token is a clean 404", r.status_code == 404, detail=f"HTTP {r.status_code}")

    _cleanup()
    return s
