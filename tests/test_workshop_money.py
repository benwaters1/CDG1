"""Workshop deposits and balances.

The owner calls this vital, and it was untested. A workshop is booked months
ahead on a deposit, so the money arrives in two parts with a long gap — which is
exactly the shape that goes wrong quietly. If the ledger drifts, somebody either
arrives having paid twice or is chased for money already sent.

The arithmetic lives in workshop_balance_due(): charges add to what's owed,
discounts subtract, payments and refunds move what's been received. These checks
drive it through a real booking rather than calling it in isolation, and cover
the ordering rule — no balance before the deposit — and the guard against paying
the same part twice.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWSM"


def _setup():
    """A workshop, a session, and a confirmed booking with a 30% deposit."""
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, instructor_name, price_per_person,
           default_capacity, active, sort_order, created_at, deposit_percent)
           VALUES (?, 'Test', 'Tutor', 900.0, 8, 1, 99, ?, 30)""",
        (f"{TAG} Watercolour", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (f"{TAG} Watercolour",)).fetchone()["id"]
    start = date.today() + timedelta(days=120)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
           notes, created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} session", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} session",)).fetchone()["id"]
    # Two people at 900 each, 30% deposit = 540.
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
           status, reference_code, manage_token, created_at, total_price, deposit_amount)
           VALUES (?, ?, ?, 2, 'confirmed', ?, ?, ?, 1800, 540)""",
        (sid, f"{TAG} guest", f"{TAG.lower()}@example.invalid", f"{TAG}1", f"tok{TAG}1", now))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}1",)).fetchone()["id"]
    conn.close()
    return bid


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE '%Deposit received%'")
    conn.commit()
    conn.close()


def run():
    s = Suite("Workshop money")
    clients()
    _cleanup()
    bid = _setup()

    s.section("What is owed before anything is paid")
    conn = db()
    balance, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("two places at 900 is 1800 charged", charged == 1800.0, detail=f"got {charged}")
    s.check("nothing received", paid == 0.0, detail=f"got {paid}")
    s.check("so all of it is due", balance == 1800.0, detail=f"got {balance}")

    s.section("The deposit must come first")
    # Paying the balance before the deposit would leave the deposit permanently
    # unpaid while the booking looked settled.
    conn = db()
    blocked = m.start_workshop_stripe_payment(conn, bid, "balance")
    conn.close()
    s.check("the balance cannot be paid before the deposit", blocked is None,
            detail=f"got {blocked!r}")

    s.section("Paying the deposit")
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Deposit", 540.0, method="stripe")
    conn.execute("UPDATE workshop_bookings SET deposit_paid_at = ? WHERE id = ?",
                 (_harness.datetime_now(), bid))
    conn.commit()
    balance, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("the deposit is received", paid == 540.0, detail=f"got {paid}")
    s.check("the total charged is unchanged", charged == 1800.0, detail=f"got {charged}")
    s.check("and the balance is the rest", balance == 1260.0, detail=f"got {balance}")

    s.section("The deposit cannot be paid twice")
    conn = db()
    again = m.start_workshop_stripe_payment(conn, bid, "deposit")
    conn.close()
    s.check("a second deposit payment is refused", again is None, detail=f"got {again!r}")

    s.section("An added charge increases the balance")
    # A private lesson, an extra night, materials — added after booking.
    conn = db()
    m.add_workshop_transaction(conn, bid, "charge", "Extra materials", 80.0)
    conn.commit()
    balance, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("the charge is added to the total", charged == 1880.0, detail=f"got {charged}")
    s.check("received is untouched", paid == 540.0, detail=f"got {paid}")
    s.check("and the balance rises", balance == 1340.0, detail=f"got {balance}")

    s.section("A discount reduces it")
    conn = db()
    m.add_workshop_transaction(conn, bid, "discount", "Returning guest", 100.0)
    conn.commit()
    balance, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("the total comes down", charged == 1780.0, detail=f"got {charged}")
    s.check("and so does the balance", balance == 1240.0, detail=f"got {balance}")

    s.section("Paying the balance settles it")
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Balance", 1240.0, method="stripe")
    conn.commit()
    balance, charged, paid = m.workshop_balance_due(conn, bid)
    s.check("nothing is left to pay", balance == 0.0, detail=f"got {balance}")
    s.check("and everything charged has been received", paid == charged,
            detail=f"paid {paid}, charged {charged}")
    # Once settled, there must be nothing further to collect — otherwise a guest
    # who has paid in full can be sent to a checkout page for zero.
    nothing_left = m.start_workshop_stripe_payment(conn, bid, "balance")
    conn.close()
    s.check("and no further payment can be started", nothing_left is None,
            detail=f"got {nothing_left!r}")

    s.section("A refund reopens the balance")
    conn = db()
    m.add_workshop_transaction(conn, bid, "refund", "Cancelled one place", 890.0, method="stripe")
    conn.commit()
    balance, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    # A refund moves what was received, not what was charged — the same rule as
    # a room booking's bill, so the two cannot tell different stories.
    s.check("received drops by the refund", paid == 890.0, detail=f"got {paid}")
    s.check("charged is unchanged", charged == 1780.0, detail=f"got {charged}")
    s.check("so a balance is owed again", balance == 890.0, detail=f"got {balance}")

    s.section("The guest's own pages work")
    pub = m.app.test_client()
    for path, label in ((f"/workshops/manage/tok{TAG}1", "manage page"),
                        (f"/workshops/confirmation/tok{TAG}1", "confirmation page")):
        r = pub.get(path)
        s.check(f"the {label} renders", r.status_code == 200, detail=f"HTTP {r.status_code}")
    # Stripe is off in tests, so this must say so rather than break.
    r = pub.get(f"/workshops/pay-balance/tok{TAG}1", follow_redirects=True)
    s.check("paying with no Stripe configured is refused, not an error",
            r.status_code == 200, detail=f"HTTP {r.status_code}")
    r = pub.get("/workshops/pay-balance/not-a-real-token")
    s.check("an unknown token is a clean 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    s.section("A Stripe payment is actually recorded")
    # The part that would fail silently: the guest pays, and nothing lands in the
    # ledger, so they are chased for money already sent. The fake raises on
    # attribute access the way a real StripeObject does, so reading a field with
    # .status or .get() instead of sval()/smeta() fails here, not in production.
    class FakeStripeObject(dict):
        def __getattr__(self, name):
            raise AttributeError(name)

    bid2 = _setup2()
    session = FakeStripeObject({
        "id": "cs_test_ws_deposit",
        "payment_status": "paid",
        "amount_total": 54000,          # €540.00, in cents
        "metadata": FakeStripeObject({"kind": "workshop_deposit",
                                      "workshop_booking_id": str(bid2)}),
    })
    conn = db()
    with m.app.test_request_context():
        m.mark_workshop_payment_paid(conn, session)
    conn.commit()
    balance, charged, paid = m.workshop_balance_due(conn, bid2)
    marked = conn.execute("SELECT deposit_paid_at FROM workshop_bookings WHERE id = ?",
                          (bid2,)).fetchone()["deposit_paid_at"]
    conn.close()
    s.check("the deposit reaches the ledger", paid == 540.0, detail=f"got {paid}")
    s.check("in cents, not euros", paid != 54000.0, detail=f"got {paid}")
    s.check("the booking is marked as deposit-paid", bool(marked))
    s.check("and the balance is the remainder", balance == 1260.0, detail=f"got {balance}")

    # The success redirect and the webhook both call this, and Stripe retries
    # webhooks. Recording it twice would take €540 off what they owe for a single
    # payment.
    conn = db()
    with m.app.test_request_context():
        m.mark_workshop_payment_paid(conn, session)
    conn.commit()
    _, _, paid_again = m.workshop_balance_due(conn, bid2)
    conn.close()
    s.check("replaying the same payment changes nothing", paid_again == 540.0,
            detail=f"got {paid_again}")

    # The receipt has to survive having no email provider. It was being lost to
    # "database is locked": send_email writes into email_outbox on its own
    # connection, which cannot get a write lock while the payment's transaction
    # is still open. A guest who paid a €540 deposit and got no receipt has no
    # reason to believe it worked.
    conn = db()
    receipt = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE subject LIKE '%Deposit received%'"
    ).fetchone()["c"]
    conn.close()
    s.check("the deposit receipt is held rather than lost", receipt >= 1,
            detail=f"{receipt} rows in the outbox")

    s.section("A payment for something else is ignored")
    conn = db()
    with m.app.test_request_context():
        m.mark_workshop_payment_paid(conn, FakeStripeObject({
        "id": "cs_test_other", "payment_status": "paid", "amount_total": 9900,
            "metadata": FakeStripeObject({"kind": "room_booking",
                                         "workshop_booking_id": str(bid2)}),
        }))
    conn.commit()
    _, _, unchanged = m.workshop_balance_due(conn, bid2)
    conn.close()
    s.check("a room payment does not credit a workshop", unchanged == 540.0,
            detail=f"got {unchanged}")

    _cleanup()
    return s


def _setup2():
    """A second booking, so the Stripe checks start from a clean ledger."""
    conn = db()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes LIKE ?",
                       (TAG + "%",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
           status, reference_code, manage_token, created_at, total_price, deposit_amount)
           VALUES (?, ?, ?, 2, 'confirmed', ?, ?, ?, 1800, 540)""",
        (sid, f"{TAG} payer", f"{TAG.lower()}p@example.invalid", f"{TAG}2", f"tok{TAG}2",
         _harness.datetime_now()))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}2",)).fetchone()["id"]
    conn.close()
    return bid
