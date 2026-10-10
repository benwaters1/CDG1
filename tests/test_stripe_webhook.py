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
            "guest_address": "1 Hook Lane",
            "guest_city": "Foix",
            "guest_postcode": "09000",
            "guest_country": "France",
            "estimated_arrival_time": "4pm-5pm",
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
    # READ BACK ON A FRESH CONNECTION, after the request has closed its own.
    # Everything after the booking itself -- the payment, the address -- was
    # written and never committed, so the request's close threw it away. A
    # check that only asked whether the booking existed passed throughout,
    # while every paid booking said the whole stay was still owed. Found by
    # the owner's test booking on 10 October.
    fresh = db()
    try:
        saved = _booking(fresh, sid)
        pays = fresh.execute(
            """SELECT amount, method FROM booking_payments
                WHERE booking_id = ?""", (saved["id"],)).fetchall() if saved else []
        s.check("what was paid is saved against it",
                saved and abs((saved["amount_paid"] or 0) - 450.0) < 0.005,
                detail="amount_paid=%r" % (saved["amount_paid"] if saved else None))
        s.check("with a payment row saying it came by card online",
                [(round(p["amount"], 2), p["method"]) for p in pays] == [(450.0, "stripe")],
                detail=str([dict(p) for p in pays]))
        s.check("and the address and arrival time the guest typed are kept",
                saved and saved["guest_address"] == "1 Hook Lane"
                and saved["estimated_arrival_time"] == "4pm-5pm",
                detail="%r / %r" % ((saved["guest_address"], saved["estimated_arrival_time"])
                                    if saved else (None, None)))
        bill = m.booking_bill(fresh, saved["id"]) if saved else None
        s.check("so the bill counts it as received",
                bill and abs(bill["paid"] - 450.0) < 0.005,
                detail="paid=%r owed=%r" % ((bill["paid"], bill["owed"]) if bill else (None, None)))
    finally:
        fresh.close()

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

    # ------------------------------------------------------------------
    # MONEY GOING BACK. Everything above is a guest paying. The handler also
    # listens for five refund events and every charge.dispute.*, and until
    # now neither branch had ever run: record_refund_from_stripe and
    # record_dispute appear in no test file at all.
    #
    # They are the half that fails silently. A payment that does not arrive
    # is noticed, because somebody is waiting for a booking. A refund that
    # does not arrive leaves the books saying a guest was paid back when the
    # money is still here, or still says they paid when it has gone -- and
    # nothing on any page disagrees.
    # ------------------------------------------------------------------
    s.section("A refund made in Stripe's own dashboard")
    # The case the helper's own docstring calls invisible: refunded in
    # Stripe, so the money has gone, while the booking here still reads paid.
    rid = TAG + "re-unplaced"
    title = "A refund in Stripe that matches no booking (%s)" % rid
    conn.execute("DELETE FROM tasks WHERE title = ?", (title,))
    conn.commit()
    r = _post(anon, "refund.created",
              {"id": rid, "status": "succeeded", "amount": 4500,
               "payment_intent": TAG + "pi-nothing-matches", "metadata": {}})
    s.check("the webhook accepts it", r.status_code == 200, detail=str(r.status_code))
    task = conn.execute("SELECT * FROM tasks WHERE title = ?", (title,)).fetchone()
    s.check("and a refund matching no booking becomes a task, not a silence",
            bool(task),
            detail="45.00 left the account and nothing here would ever have "
                   "said so")
    if task:
        s.check("the task says what to do about it",
                "record it on the booking's refund page" in (task["notes"] or ""),
                detail=(task["notes"] or "")[:90])
        s.check("and it is raised as high priority",
                task["priority"] == "high", detail=str(task["priority"]))

    # Twice is one task. Stripe retries, and a duplicate for every retry is
    # how a list stops being read.
    _post(anon, "refund.created",
          {"id": rid, "status": "succeeded", "amount": 4500,
           "payment_intent": TAG + "pi-nothing-matches", "metadata": {}})
    again = conn.execute("SELECT COUNT(*) FROM tasks WHERE title = ?", (title,)).fetchone()[0]
    s.check("and Stripe retrying it does not raise a second task", again == 1,
            detail="%d task(s)" % again)
    conn.execute("DELETE FROM tasks WHERE title = ?", (title,))
    conn.commit()

    s.section("A refund this app made is not counted twice")
    # The app records its own refunds when it makes them, and the same refund
    # then arrives here as an event. Counting it again would double every
    # refund the house issues.
    #
    # ASKED AS A DECISION, NOT AS A ROW COUNT, and the difference matters.
    # Counting rows in `refunds` before and after looked like the obvious
    # check and was worthless: take the dedup out altogether and the refund
    # falls through to the branch for one that matches no booking, which
    # writes a TASK and not a refund row -- so the count is unchanged and the
    # check passes against broken code. It was a negative control that found
    # that, by removing the dedup and watching this go green.
    #
    # record_refund_from_stripe returns a word for precisely this decision.
    # Pinned on the word, losing the dedup turns "ours" into "unplaced".
    word = m.record_refund_from_stripe(
        conn, {"id": TAG + "re-ours", "status": "succeeded", "amount": 2500,
               "payment_intent": TAG + "pi-ours",
               "metadata": {"made_by": "gudanes"}})
    s.check("it is recognised as one we made, and left alone",
            word == "ours",
            detail="returned %r -- anything else means the house's own "
                   "refunds are being recorded a second time" % word)
    conn.commit()

    # And through the webhook, which is how it actually arrives.
    before = conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]
    r = _post(anon, "refund.created",
              {"id": TAG + "re-ours2", "status": "succeeded", "amount": 2500,
               "payment_intent": TAG + "pi-ours",
               "metadata": {"made_by": "gudanes"}})
    s.check("the webhook accepts it", r.status_code == 200, detail=str(r.status_code))
    after = conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]
    s.check("and no second refund is written", after == before,
            detail="%d refunds before, %d after" % (before, after))

    s.section("charge.refunded, when Stripe cannot be asked")
    # This branch asks Stripe to list the refunds on the payment, and falls
    # back to the ones embedded in the event if that call fails. Under test
    # the key is neutralised so the call cannot succeed -- which makes the
    # FALLBACK the path that runs, and it is the path that matters when
    # Stripe is unreachable. Stood in explicitly rather than relying on the
    # library to refuse, so this is a test of the fallback and not of luck.
    real_list = m.stripe.Refund.list

    def _no_list(*_a, **_kw):
        raise AssertionError("Stripe was asked to list refunds")

    m.stripe.Refund.list = _no_list
    try:
        embedded = {
            "id": TAG + "ch-1", "payment_intent": TAG + "pi-embedded",
            "refunds": {"data": [
                {"id": TAG + "re-embedded", "status": "succeeded",
                 "amount": 1000, "payment_intent": TAG + "pi-embedded",
                 "metadata": {"made_by": "gudanes"}}]},
        }
        r = _post(anon, "charge.refunded", embedded)
    finally:
        m.stripe.Refund.list = real_list
    s.check("it falls back to the refunds in the event rather than failing",
            r.status_code == 200,
            detail="status %s -- if this 500s, an unreachable Stripe loses "
                   "every refund it tries to tell us about" % r.status_code)

    s.section("A card dispute, which has a deadline")
    # A chargeback the house loses by default if nobody answers it in time.
    did = TAG + "dp-1"
    conn.execute("DELETE FROM payment_disputes WHERE stripe_dispute_id = ?", (did,))
    conn.commit()
    due = int((m.datetime.now(m.timezone.utc) + timedelta(days=10)).timestamp())
    r = _post(anon, "charge.dispute.created",
              {"id": did, "status": "needs_response", "amount": 12000,
               "reason": "fraudulent", "payment_intent": TAG + "pi-dispute",
               "evidence_details": {"due_by": due}})
    s.check("the webhook accepts it", r.status_code == 200, detail=str(r.status_code))
    row = conn.execute("SELECT * FROM payment_disputes WHERE stripe_dispute_id = ?",
                       (did,)).fetchone()
    s.check("the dispute is written down", bool(row),
            detail="a chargeback nobody records is one the house loses by "
                   "default when the clock runs out")
    if row:
        s.check("with the amount in euros, not cents",
                abs((row["amount"] or 0) - 120.0) < 0.01, detail=str(row["amount"]))
        s.check("and the date the evidence is due",
                bool(row["evidence_due_by"]), detail=str(row["evidence_due_by"]))
        s.check("and it is open", not row["closed_at"])

    s.section("The same dispute again, which Stripe sends often")
    r = _post(anon, "charge.dispute.updated",
              {"id": did, "status": "under_review", "amount": 12000,
               "reason": "fraudulent", "payment_intent": TAG + "pi-dispute",
               "evidence_details": {}})
    n = conn.execute("SELECT COUNT(*) FROM payment_disputes WHERE stripe_dispute_id = ?",
                     (did,)).fetchone()[0]
    s.check("it updates rather than duplicating", n == 1, detail="%d row(s)" % n)
    row = conn.execute("SELECT * FROM payment_disputes WHERE stripe_dispute_id = ?",
                       (did,)).fetchone()
    s.check("the status moves on", row and row["status"] == "under_review",
            detail=str(row["status"] if row else None))
    s.check("and an update carrying no deadline does not erase the one we had",
            row and bool(row["evidence_due_by"]),
            detail="COALESCE keeps it; without that, one ordinary update "
                   "would drop the only date that matters")

    s.section("And when it closes")
    r = _post(anon, "charge.dispute.closed",
              {"id": did, "status": "won", "amount": 12000,
               "reason": "fraudulent", "payment_intent": TAG + "pi-dispute",
               "evidence_details": {}})
    row = conn.execute("SELECT * FROM payment_disputes WHERE stripe_dispute_id = ?",
                       (did,)).fetchone()
    s.check("it is marked closed", row and bool(row["closed_at"]),
            detail="a won dispute left open sits on the list for ever")
    conn.execute("DELETE FROM payment_disputes WHERE stripe_dispute_id = ?", (did,))
    conn.commit()

    s.section("The go-live checklist says whether the keys are test or live")
    # Nothing on any page said which, and on launch day it is the question:
    # test keys move no money, so a live site on them takes bookings nobody
    # pays for. Made-up strings with the right prefixes; nothing calls Stripe.
    kept = (m.stripe_enabled, m.STRIPE_SECRET_KEY, m.STRIPE_PUBLISHABLE_KEY,
            m.STRIPE_WEBHOOK_SECRET, m.SITE_IS_LIVE)

    def keys_row(secret, public, live_site):
        m.stripe_enabled = lambda: True
        m.STRIPE_SECRET_KEY, m.STRIPE_PUBLISHABLE_KEY = secret, public
        m.STRIPE_WEBHOOK_SECRET = "whsec_made_up_for_this_check"
        m.SITE_IS_LIVE = live_site
        return next((r for r in m.readiness_checks(conn, include_slow=False)
                     if r["label"] == "Stripe keys"), None)
    try:
        row = keys_row("sk_test_" + "x" * 20, "pk_test_" + "x" * 20, False)
        s.check("test keys before launch are named, and are no fault yet",
                row and row["ok"] and "Test keys" in row["detail"], detail=str(row))
        row = keys_row("sk_test_" + "x" * 20, "pk_test_" + "x" * 20, True)
        s.check("test keys on a live site are a blocker",
                row and not row["ok"] and row["severity"] == "blocker"
                and "nobody is paying" in row["detail"], detail=str(row))
        row = keys_row("sk_live_" + "x" * 20, "pk_live_" + "x" * 20, True)
        s.check("live keys say real cards are charged",
                row and row["ok"] and "real cards are charged" in row["detail"],
                detail=str(row))
        row = keys_row("sk_live_" + "x" * 20, "pk_test_" + "x" * 20, True)
        s.check("a live secret with a test publishable key is caught",
                row and not row["ok"] and "same kind" in row["detail"], detail=str(row))
        s.check("and no part of a key is ever printed",
                "xxxx" not in str(row), detail=str(row))
        s.check("the mode comes from the prefix alone",
                (m.stripe_key_mode("rk_live_abc"), m.stripe_key_mode("whsec_abc"),
                 m.stripe_key_mode(None)) == ("live", None, None))
    finally:
        (m.stripe_enabled, m.STRIPE_SECRET_KEY, m.STRIPE_PUBLISHABLE_KEY,
         m.STRIPE_WEBHOOK_SECRET, m.SITE_IS_LIVE) = kept

    _cleanup(conn)
    conn.close()
    return s
