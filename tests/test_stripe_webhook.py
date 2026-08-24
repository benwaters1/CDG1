"""The webhook, which is the only thing that reliably turns money into a booking.

A guest can pay and then close the tab, or lose signal, before the success
redirect ever loads. Stripe says so plainly: fulfilment triggered only from the
landing page is fulfilment you will sometimes not do. So this handler is the
one path that must always work — and until now nothing tested it at all.

The half it was missing: a payment method that settles later completes the
Checkout Session with payment_status "unpaid". Every branch here is guarded on
"paid", correctly, so nothing is created. The money then arrives days later as
checkout.session.async_payment_succeeded — and nobody was listening. Guest paid,
no booking, no trace.

That is not theoretical for pos_pay_link, which is the one checkout that does
not pin payment_method_types and so already offers whatever the Dashboard has
enabled.

Stripe is stubbed throughout. construct_event is replaced so no signature is
needed, and nothing here reaches the network or moves money.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "hook-"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = 'payment' AND title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE action = 'stripe_payment_failed' "
                 "AND target LIKE ?", (TAG + "%",))
    conn.commit()


def _session(sid, *, paid, room_id, guest, arrival, departure):
    """A Checkout Session shaped the way the webhook reads one."""
    return {
        "id": sid,
        "payment_status": "paid" if paid else "unpaid",
        "payment_intent": sid + "-pi",
        "customer_email": "hook@example.com",
        "amount_total": 45000,
        "metadata": {
            "room_id": str(room_id),
            "guest_name": guest,
            "guest_email": "hook@example.com",
            "guest_phone": "",
            "arrival_date": arrival,
            "departure_date": departure,
            "party_size": "2",
            "special_requests": "",
            "extra_ids": "",
            "promo_code": "",
            "total_price": "450.00",
            "nights": "2",
        },
    }


def _post(client, event_type, session):
    """Drive the webhook with a stubbed signature check."""
    original = m.stripe.Webhook.construct_event
    original_secret = m.STRIPE_WEBHOOK_SECRET
    m.STRIPE_WEBHOOK_SECRET = "whsec_test_only_never_real"
    m.stripe.Webhook.construct_event = (
        lambda payload, sig, secret: {"type": event_type, "data": {"object": session}})
    try:
        return client.post("/webhooks/stripe", data=b"{}",
                           headers={"Stripe-Signature": "t=1,v1=stub"})
    finally:
        m.stripe.Webhook.construct_event = original
        m.STRIPE_WEBHOOK_SECRET = original_secret


def _booking(conn, sid):
    return conn.execute("SELECT * FROM bookings WHERE stripe_session_id = ?", (sid,)).fetchone()


def run():
    s = Suite("The Stripe webhook")
    _oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()
    _cleanup(conn)

    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    if not room:
        s.check("a room to book against", False, detail="no active rooms")
        conn.close()
        return s
    arrival = (m.service_day() + timedelta(days=120)).isoformat()
    departure = (m.service_day() + timedelta(days=122)).isoformat()

    s.section("A card pays during checkout")
    sid = TAG + "card"
    r = _post(anon, "checkout.session.completed",
              _session(sid, paid=True, room_id=room["id"], guest=TAG + "Card",
                       arrival=arrival, departure=departure))
    s.check("the webhook accepts it", r.status_code == 200, detail=str(r.status_code))
    s.check("and the booking exists", bool(_booking(conn, sid)),
            detail="no booking created from a paid session")

    s.section("Sending it twice does not book twice")
    # Stripe retries. The guest's success redirect can also get there first.
    _post(anon, "checkout.session.completed",
          _session(sid, paid=True, room_id=room["id"], guest=TAG + "Card",
                   arrival=arrival, departure=departure))
    n = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE stripe_session_id = ?",
                     (sid,)).fetchone()["c"]
    s.check("still one booking", n == 1, detail=f"{n} bookings")

    s.section("A payment that settles later books nothing yet")
    # This is correct and was already true: the session completes "unpaid",
    # and confirming a stay against money that has not arrived would be worse
    # than waiting.
    slow = TAG + "slow"
    r = _post(anon, "checkout.session.completed",
              _session(slow, paid=False, room_id=room["id"], guest=TAG + "Slow",
                       arrival=arrival, departure=departure))
    s.check("the webhook still accepts it", r.status_code == 200)
    s.check("but nothing is booked on an unpaid session", not _booking(conn, slow),
            detail="a booking was made before the money arrived")

    s.section("And is booked when the money actually arrives")
    # The half that was missing. Without this the guest has paid and there is
    # no booking, no error, and nothing to find.
    r = _post(anon, "checkout.session.async_payment_succeeded",
              _session(slow, paid=True, room_id=room["id"], guest=TAG + "Slow",
                       arrival=arrival, departure=departure))
    s.check("the webhook accepts the later event", r.status_code == 200,
            detail=str(r.status_code))
    made = _booking(conn, slow)
    s.check("the booking now exists", bool(made),
            detail="async_payment_succeeded did not fulfil")
    s.check("for the right guest", made and made["guest_name"] == TAG + "Slow",
            detail=made["guest_name"] if made else "?")
    s.check("and it is marked paid", made and made["payment_status"] == "paid",
            detail=made["payment_status"] if made else "?")

    s.section("A delayed payment that fails is not silent")
    failed = TAG + "failed"
    r = _post(anon, "checkout.session.async_payment_failed",
              _session(failed, paid=False, room_id=room["id"], guest=TAG + "Failed",
                       arrival=arrival, departure=departure))
    s.check("the webhook accepts it", r.status_code == 200, detail=str(r.status_code))
    s.check("nothing is booked", not _booking(conn, failed))
    task = conn.execute(
        """SELECT * FROM tasks WHERE origin = 'payment' AND title LIKE ?
           ORDER BY id DESC LIMIT 1""", ("%" + TAG + "Failed%",)).fetchone()
    s.check("somebody is told", bool(task),
            detail="no task raised for a failed delayed payment")
    s.check("with the session on it, so it can be looked up",
            task and failed in (task["notes"] or ""), detail=str(task["notes"])[:60] if task else "")
    s.check("and it is marked urgent", task and task["priority"] == "high",
            detail=task["priority"] if task else "?")
    logged = conn.execute(
        "SELECT 1 FROM audit_log WHERE action = 'stripe_payment_failed' AND target LIKE ?",
        (TAG + "%",)).fetchone()
    s.check("and it is in the audit log too", bool(logged))

    s.section("Guards")
    # An unsigned or wrongly-signed payload must never reach the branches above.
    original_secret = m.STRIPE_WEBHOOK_SECRET
    m.STRIPE_WEBHOOK_SECRET = "whsec_test_only_never_real"
    try:
        s.check("a payload that fails signature checking is refused",
                anon.post("/webhooks/stripe", data=b"{}",
                          headers={"Stripe-Signature": "nonsense"}).status_code == 400)
    finally:
        m.STRIPE_WEBHOOK_SECRET = original_secret
    s.check("and with no secret configured the endpoint does not exist",
            anon.post("/webhooks/stripe", data=b"{}").status_code in (400, 404))

    s.section("An event type nobody handles is accepted and ignored")
    # Stripe sends whatever the endpoint is subscribed to. Returning anything
    # other than 200 makes it retry an event we will never act on.
    r = _post(anon, "payment_intent.created",
              _session(TAG + "noop", paid=True, room_id=room["id"], guest=TAG + "Noop",
                       arrival=arrival, departure=departure))
    s.check("it is a 200", r.status_code == 200, detail=str(r.status_code))
    s.check("and nothing was created", not _booking(conn, TAG + "noop"))

    _cleanup(conn)
    conn.close()
    return s
