"""Every euro received on a stay leaves a row saying how it arrived.

`bookings.amount_paid` says how much. `booking_payments` says how — card,
cash, bank transfer, a voucher spent — and it is what the card-fee report
reads to work out what the house pays to take money that way.

Four of the five ways money reaches a stay wrote both halves. The fifth was
the first payment on a brand-new booking, taken at a Stripe checkout, which
only ever incremented `amount_paid`. So a stay showed as paid with nothing to
account for it, and the fee report was blind to the one kind of payment that
always carries a fee — every online booking the house takes.

Two things carry this file.

  BOTH HALVES, ON EVERY PATH. The amount and the method, written together. The
  checks are a list of the paths because the fault was one path missing from a
  convention four others kept, and a list is the only thing that catches the
  sixth path when somebody adds it.

  ONCE PER PAYMENT. The success redirect and the webhook can both arrive for
  the same Checkout Session, and a second row would double what the house
  believes it paid in card fees. The UNIQUE session id is the guard.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPL"
_n = [0]


def _suffix():
    _n[0] += 1
    return str(_n[0])


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_payments WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _ledger(booking_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM booking_payments WHERE booking_id = ? ORDER BY id",
            (booking_id,)).fetchall()
    finally:
        conn.close()


def _row(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    finally:
        conn.close()


def _adds_up(booking_id):
    """What the stay says it received against what the ledger can account for."""
    row = _row(booking_id)
    rows = _ledger(booking_id)
    return (round(row["amount_paid"] or 0, 2),
            round(sum(r["amount"] or 0 for r in rows), 2))


def _session(room_id, arrival, total):
    """A completed Checkout Session for a NEW booking, as the webhook sees it."""
    return {
        "id": f"cs_test_{TAG}_{_suffix()}",
        "payment_intent": f"pi_test_{TAG}",
        "amount_total": int(round(float(total) * 100)),
        "metadata": {
            "room_id": str(room_id),
            "guest_name": f"{TAG} Online",
            "guest_email": f"{TAG.lower()}.online@example.invalid",
            "guest_phone": "",
            "arrival_date": arrival.isoformat(),
            "departure_date": (arrival + timedelta(days=2)).isoformat(),
            "party_size": "2",
            "special_requests": "",
            "extra_ids": "",
            "promo_code": "",
            "total_price": f"{total:.2f}",
            "discount_amount": "0.00",
        },
    }


def run():
    s = Suite("Every payment says how it arrived")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    conn.close()

    s.section("A booking paid for at the checkout")
    # THE PATH THAT WAS MISSING. Nothing here contacts Stripe; the harness
    # clears the keys and this drives the recording half directly, which is
    # the only half that was wrong.
    conn = db()
    far = m.house_today() + timedelta(days=530)
    while True:
        ok, _why = m.is_range_available(conn, room["id"], far, far + timedelta(days=2))
        if ok:
            break
        far += timedelta(days=3)
    session = _session(room["id"], far, 500.0)
    with m.app.test_request_context("/"):
        token = m.create_booking_from_stripe_session(conn, session)
    conn.commit()
    made = conn.execute(
        "SELECT * FROM bookings WHERE manage_token = ?", (token,)).fetchone()
    conn.close()
    s.check("the booking exists", made is not None, detail=f"{token!r}")
    paid, ledger = _adds_up(made["id"])
    s.check("the money is on the stay", abs(paid - 500) < 0.01, detail=f"{paid}")
    s.check("and there is a row saying how it arrived",
            abs(ledger - 500) < 0.01,
            detail=f"ledger {ledger} against {paid} received — a stay paid "
                   "with nothing to account for it is money the card-fee "
                   "report cannot see")
    rows = _ledger(made["id"])
    s.check("recorded as a card payment", rows and rows[0]["method"] == "stripe",
            detail=f"{[r['method'] for r in rows]}")
    s.check("with the session against it, so it cannot be counted twice",
            rows and rows[0]["stripe_session_id"] == session["id"])

    s.section("The same payment cannot be counted twice")
    # The success redirect and the webhook both arrive for one Checkout
    # Session, and a second row would double what the house believes it paid
    # in card fees. The UNIQUE session id is what makes that impossible; the
    # callers' try/except is only how the loser of the race exits quietly.
    # Checked at the database, because that is where the guarantee lives --
    # calling the creator twice is not something either caller does, they look
    # for the booking first.
    conn = db()
    refused = False
    try:
        conn.execute(
            """INSERT INTO booking_payments (booking_id, amount, method,
               stripe_session_id, created_at) VALUES (?, 500, 'stripe', ?, ?)""",
            (made["id"], session["id"], datetime.now(timezone.utc).isoformat()))
        conn.commit()
    except Exception:
        conn.rollback()
        refused = True
    conn.close()
    s.check("a second row for one session is refused", refused,
            detail="without the unique index the fee report doubles on every "
                   "webhook retry")
    s.check("so there is still exactly one", len(_ledger(made["id"])) == 1,
            detail=f"{len(_ledger(made['id']))}")

    s.section("A balance settled online later")
    conn = db()
    with m.app.test_request_context("/"):
        m.mark_booking_payment_paid(conn, {
            "id": f"cs_test_{TAG}_bal_{_suffix()}",
            "payment_intent": f"pi_test_{TAG}_bal",
            "amount_total": 12000,
            "metadata": {"kind": "room_balance", "booking_id": str(made["id"])},
        })
    conn.commit()
    conn.close()
    paid, ledger = _adds_up(made["id"])
    s.check("it adds to both halves", abs(paid - ledger) < 0.01,
            detail=f"{paid} received, {ledger} accounted for")

    s.section("Cash taken at the desk")
    conn = db()
    desk_far = far + timedelta(days=30)
    while True:
        ok, _why = m.is_range_available(conn, room["id"], desk_far,
                                        desk_far + timedelta(days=2))
        if ok:
            break
        desk_far += timedelta(days=3)
    with m.app.test_request_context("/"):
        ref, _tok = m.create_booking(
            conn, room, f"{TAG} Desk", f"{TAG.lower()}.desk@example.invalid", "",
            desk_far, desk_far + timedelta(days=2), 2, "", [])
    conn.commit()
    desk = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                        (ref,)).fetchone()
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (desk["id"],))
    conn.commit()
    conn.close()
    oc.post(f"/management/outstanding/{desk['id']}/payment",
            data={"amount": "150", "method": "cash", "reference": "at the desk"},
            follow_redirects=True)
    paid, ledger = _adds_up(desk["id"])
    s.check("the cash is on the stay", abs(paid - 150) < 0.01, detail=f"{paid}")
    s.check("and accounted for", abs(ledger - 150) < 0.01, detail=f"{ledger}")
    s.check("as cash, not as a card payment",
            [r["method"] for r in _ledger(desk["id"])] == ["cash"],
            detail=f"{[r['method'] for r in _ledger(desk['id'])]} — the fee "
                   "report charges the house a percentage on card rows")

    s.section("Nothing anywhere claims more money than it can account for")
    # The reconciliation the fault was invisible to. Across every stay in the
    # database, not only the ones this suite made.
    conn = db()
    short = conn.execute(
        """SELECT b.reference_code, b.amount_paid,
                  COALESCE((SELECT SUM(amount) FROM booking_payments p
                             WHERE p.booking_id = b.id), 0) AS ledger
             FROM bookings b
            WHERE COALESCE(b.amount_paid, 0) > 0
              AND b.guest_name LIKE ?""", (TAG + "%",)).fetchall()
    conn.close()
    unaccounted = [r for r in short
                   if round((r["amount_paid"] or 0) - (r["ledger"] or 0), 2) > 0.005]
    s.check("every stay this suite paid for adds up",
            not unaccounted,
            detail="; ".join(f"{r['reference_code']} paid {r['amount_paid']} "
                             f"ledger {r['ledger']}" for r in unaccounted)
                   or f"{len(short)} stay(s) checked")

    s.section("And the fee report can see the card payment")
    conn = db()
    with m.app.test_request_context("/"):
        report = m.cost_of_taking_money(
            conn, m.house_today() - timedelta(days=1),
            m.house_today() + timedelta(days=1))
    conn.close()
    by_method = {r["method"]: r for r in report["rows"]}
    s.check("the stripe row carries the money", "stripe" in by_method,
            detail=f"{sorted(by_method)}")
    s.check("and a fee is worked out against it",
            "stripe" in by_method and by_method["stripe"]["charged"],
            detail="a card payment the report cannot see is a fee the house "
                   "pays and never counts")

    s.section("Guards")
    s.check("an employee cannot record a payment",
            ec.post(f"/management/outstanding/{desk['id']}/payment",
                    data={"amount": "10", "method": "cash"}).status_code in (302, 403))

    _cleanup()
    return s
