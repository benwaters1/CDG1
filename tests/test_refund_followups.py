"""What Stripe says about money after it has gone, and money still to give back.

  - A refund made in Stripe's own dashboard is recorded against the payment it
    came from. It was invisible: the booking read as paid, the money had gone.
  - A refund that failed after Stripe accepted it is booked back, so the books
    stop saying the guest was paid when they were not.
  - A card dispute is recorded and followed by a task that closes itself; a
    lost one is a refund in the record. The webhook heard none of it.
  - Money still held on a booking the house said no to is on the owner's list
    until it goes back. A decline by somebody who may not give money back
    moved it anyway; now it moves nothing, and the owner is told.
  - A house cancellation and a called-off sitting can refund in the same step.
  - A refund whose price stood can be taken off the bill afterwards.

Stripe is stood in for -- the signature check, the refund and session calls --
and put back in a `finally`. Mail is caught.
"""
import html
import itertools
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRF"
# One count across every stand-in, so two of them never hand out the same
# refund id -- Stripe's are unique, and a collision here would test nothing.
_IDS = itertools.count(1)


def _cleanup():
    conn = db()
    try:
        _clear(conn)
    finally:
        conn.close()


def _clear(conn):
    stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
    regs = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM payment_disputes WHERE stripe_dispute_id LIKE ?", ("dp_" + TAG + "%",))
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {regs}",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR title LIKE ?",
                 ("%" + TAG + "%", "%re_" + TAG + "%"))
    # The logins made here declined and cancelled things, and the audit trail
    # keeps who -- a reference with no delete rule, which is right for a real
    # person and in the way of a test's own.
    mine = "(SELECT id FROM users WHERE email LIKE ?)"
    conn.execute(f"UPDATE audit_log SET actor_user_id = NULL WHERE actor_user_id IN {mine}",
                 (TAG.lower() + "%",))
    conn.execute(f"UPDATE refunds SET refunded_by_user_id = NULL WHERE refunded_by_user_id IN {mine}",
                 (TAG.lower() + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM access_presets WHERE slug LIKE ?", (TAG.lower() + "%",))
    conn.commit()


class _FakeStripe:
    """Refunds, sessions and the webhook's signature check, stood in for."""

    def __init__(self, sessions=None, by_intent=None, listed=None):
        self.refunds, self.event = [], None
        self.sessions = dict(sessions or {})          # cs_ -> pi_
        self.by_intent = dict(by_intent or {})        # pi_ -> cs_
        self.listed = dict(listed or {})              # pi_ -> [refund dicts]
        outer = self

        class Refund:
            @staticmethod
            def create(**kw):
                outer.refunds.append(kw)
                obj = type("StripeRefund", (), {})()
                obj.id = f"re_{TAG}{next(_IDS)}"
                return obj

            @staticmethod
            def list(payment_intent=None, limit=None):
                return {"data": outer.listed.get(payment_intent, [])}

        class Session:
            @staticmethod
            def retrieve(sid):
                return {"id": sid, "payment_intent": outer.sessions.get(sid)}

            @staticmethod
            def list(payment_intent=None, limit=None):
                sid = outer.by_intent.get(payment_intent)
                return {"data": [{"id": sid}] if sid else []}

        class Checkout:
            pass

        class Webhook:
            @staticmethod
            def construct_event(payload, sig, secret):
                return outer.event

        Checkout.Session = Session
        self.Refund, self.checkout, self.Webhook = Refund, Checkout, Webhook


def _with_stripe(fake, fn):
    saved = m.stripe, m.STRIPE_SECRET_KEY, m.STRIPE_WEBHOOK_SECRET
    m.stripe, m.STRIPE_SECRET_KEY = fake, "sk_test_stand_in"
    m.STRIPE_WEBHOOK_SECRET = "whsec_test_only_never_real"
    try:
        return fn()
    finally:
        m.stripe, m.STRIPE_SECRET_KEY, m.STRIPE_WEBHOOK_SECRET = saved


def _hook(client, fake, event_type, obj):
    fake.event = {"type": event_type, "data": {"object": obj}}
    return _with_stripe(fake, lambda: client.post(
        "/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=stub"}))


def _stay(ref, *, status="confirmed", payments=(), cancel_reason=None):
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=70)
    departure = arrival + timedelta(days=2)
    paid = round(sum(p[0] for p in payments), 2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, payment_status, total_price,
           amount_paid, cancel_reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, 400, ?, ?, ?)""",
        (room, f"{TAG}{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} Guest {ref}",
         f"{TAG.lower()}{ref.lower()}@example.invalid", arrival.isoformat(),
         departure.isoformat(), status, "paid" if paid >= 400 else "unpaid", paid,
         cancel_reason, datetime.now(timezone.utc).isoformat()))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    m.stamp_room_total(conn, bid, 400.0, arrival.isoformat(), departure.isoformat())
    for n, (amount, method, intent, session_id) in enumerate(payments):
        conn.execute(
            """INSERT INTO booking_payments (booking_id, amount, method, stripe_session_id,
               stripe_payment_intent_id, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (bid, amount, method, session_id, intent,
             (datetime.now(timezone.utc) - timedelta(minutes=10 - n)).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _session(ref):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 900, 10, 1, 96, ?, 10)""", (f"{TAG} Atelier {ref}", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (f"{TAG} Atelier {ref}",)).fetchone()["id"]
    start = house_today() + timedelta(days=80)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
           created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), f"{TAG} {ref}", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {ref}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return sid


def _reg(sid, ref, *, paid=900.0, method="stripe", stripe_ref=None):
    conn = db()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
           status, reference_code, manage_token, created_at, total_price)
           VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, 900)""",
        (sid, f"{TAG} Maker {ref}", f"{TAG.lower()}.w{ref.lower()}@example.invalid",
         f"{TAG}{ref}", f"tokw{TAG}{ref}".lower(), _harness.datetime_now()))
    rid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, rid, "payment", "Paid", paid, method=method,
                                   stripe_ref=stripe_ref)
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (rid,)).fetchone()
    conn.close()
    return row


def _preset_client(areas, suffix):
    from werkzeug.security import generate_password_hash
    slug, email = f"{TAG.lower()}{suffix}", f"{TAG.lower()}{suffix}@example.invalid"
    conn = db()
    conn.execute(
        """INSERT INTO access_presets (slug, name, description, areas,
           is_full_access, sort_order, created_at) VALUES (?, ?, 'probe', ?, 0, 99, ?)""",
        (slug, f"{TAG} {suffix}", ",".join(areas), _harness.datetime_now()))
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at, access_preset) VALUES (?, ?, 'owner', ?, 'General', 'active', ?, ?)""",
        (email, generate_password_hash("probe-pw-123"), f"{TAG} {suffix}",
         _harness.datetime_now(), slug))
    conn.commit()
    conn.close()
    c = m.app.test_client()
    c.post("/login", data={"email": email, "password": "probe-pw-123"}, follow_redirects=True)
    return c


def _q(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _call(fn, *args, **kw):
    conn = db()
    try:
        return fn(conn, *args, **kw)
    finally:
        conn.commit()
        conn.close()


def _titles():
    found, _dropped = _call(m.watch_task_findings, house_today())
    return [title for _k, title, _n, _d, _p in found]


def _warnings():
    with m.app.test_request_context("/"):
        return [w["title"] for w in _call(m.owner_home_warnings, house_today())]


def run():
    s = Suite("After a refund, and before one")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, **k: sent.append((to, subject, body)) or True
    try:
        _run(s, oc, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, sent):
    s.section("A refund made in Stripe's dashboard is recorded")
    dash = _stay("DASH", payments=[(400.0, "stripe", "pi_dash", "cs_dash")])
    fake = _FakeStripe()
    refund = {"id": f"re_{TAG}d1", "amount": 10000, "payment_intent": "pi_dash",
              "status": "succeeded", "metadata": {}, "created": 1790000000}
    _hook(oc, fake, "refund.created", refund)
    rows = _q("SELECT * FROM refunds WHERE booking_id = ? AND category = 'room'", dash["id"])
    s.check("it is recorded against the payment it came from",
            [(r["amount"], r["reason_code"], r["stripe_refund_id"]) for r in rows]
            == [(100.0, "stripe_dashboard", f"re_{TAG}d1")] and rows[0]["payment_key"],
            detail=str([dict(r) for r in rows]))
    _hook(oc, fake, "refund.updated", refund)
    s.check("and Stripe telling us twice records it once",
            len(_q("SELECT id FROM refunds WHERE booking_id = ?", dash["id"])) == 1)
    _hook(oc, fake, "refund.created", dict(refund, id=f"re_{TAG}own",
                                           metadata={"made_by": "gudanes"}))
    s.check("while a refund this app made is left for the app to record",
            len(_q("SELECT id FROM refunds WHERE booking_id = ?", dash["id"])) == 1)
    fake.listed["pi_dash"] = [dict(refund, id=f"re_{TAG}d2", amount=5000)]
    _hook(oc, fake, "charge.refunded", {"id": "ch_dash", "payment_intent": "pi_dash"})
    s.check("a charge refunded in the dashboard is read refund by refund",
            sorted(r["amount"] for r in _q("SELECT amount FROM refunds WHERE booking_id = ?",
                                           dash["id"])) == [50.0, 100.0])
    s.check("the stay now asks for it again, and the owner is asked what was meant",
            any(f"left {TAG} Guest DASH owing" in t for t in _titles())
            and any("made in Stripe to decide" in w for w in _warnings()),
            detail=str([t for t in _titles() if TAG in t]))
    first = _q("SELECT id FROM refunds WHERE stripe_refund_id = ?", f"re_{TAG}d1")[0]["id"]
    oc.post(f"/admin/refunds/room/{dash['id']}/off-the-bill/{first}", follow_redirects=True)
    second = _q("SELECT id FROM refunds WHERE stripe_refund_id = ?", f"re_{TAG}d2")[0]["id"]
    oc.post(f"/admin/refunds/room/{dash['id']}/off-the-bill/{second}", follow_redirects=True)
    bill = _call(m.booking_bill, dash["id"])
    s.check("taken off the bill after the fact, nothing is owed", bill["owed"] == 0.0,
            detail=f"owed {bill['owed']}")
    s.check("and the question closes itself",
            not any(f"left {TAG} Guest DASH owing" in t for t in _titles()))

    old = _stay("OLD", payments=[(400.0, "stripe", None, "cs_old")])
    fake = _FakeStripe(by_intent={"pi_old": "cs_old"})
    _hook(oc, fake, "refund.created", {"id": f"re_{TAG}o1", "amount": 2000,
                                       "payment_intent": "pi_old", "status": "succeeded",
                                       "metadata": {}})
    s.check("a payment kept only by its Checkout session is still found",
            [r["amount"] for r in _q("SELECT amount FROM refunds WHERE booking_id = ?",
                                     old["id"])] == [20.0])
    _hook(oc, fake, "refund.created", {"id": f"re_{TAG}nowhere", "amount": 700,
                                       "payment_intent": "pi_nobody", "status": "succeeded",
                                       "metadata": {}})
    s.check("and one that matches no booking becomes a task, not silence",
            _q("SELECT id FROM tasks WHERE title LIKE ? AND status != 'done'",
               f"%re_{TAG}nowhere%"))

    s.section("A refund that failed after Stripe took it is booked back")
    gone = _stay("GONE", status="cancelled", payments=[(400.0, "stripe", "pi_gone", "cs_gone")])
    fake = _FakeStripe()
    ok, err, ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "room", gone, 400, "They cancelled", method="stripe",
        reason_code="guest_cancelled"))
    rid = _q("SELECT stripe_refund_id FROM refunds WHERE id = ?", ids[0])[0][0]
    s.check("the refund went, and the stay reads refunded",
            ok and _q("SELECT payment_status FROM bookings WHERE id = ?", gone["id"])[0][0]
            == "refunded", detail=str(err))
    _hook(oc, fake, "refund.failed", {"id": rid, "amount": 40000, "payment_intent": "pi_gone",
                                      "status": "failed",
                                      "failure_reason": "expired_or_canceled_card"})
    rows = _q("SELECT * FROM refunds WHERE booking_id = ? ORDER BY id", gone["id"])
    s.check("a failure is booked back against the refund it undoes",
            [r["amount"] for r in rows] == [400.0, -400.0] and rows[1]["reverses_refund_id"] == ids[0],
            detail=str([(r["amount"], r["reverses_refund_id"]) for r in rows]))
    s.check("so the money reads as still theirs to be given",
            _call(m.refundable_amount, "room", gone) == 400.0
            and _q("SELECT payment_status FROM bookings WHERE id = ?", gone["id"])[0][0] != "refunded")
    _hook(oc, fake, "refund.failed", {"id": rid, "amount": 40000, "status": "failed"})
    s.check("and a second report of the same failure books nothing more",
            len(_q("SELECT id FROM refunds WHERE booking_id = ?", gone["id"])) == 2)
    s.check("the owner is told it did not reach them",
            any(f"refund to {TAG} Guest GONE did not go through" in t for t in _titles())
            and any("did not reach the guest" in w for w in _warnings()),
            detail=str([t for t in _titles() if TAG in t]))
    _call(m.make_refund, "room", gone, 400, "Sent by bank transfer instead",
          method="bank_transfer", reason_code="guest_cancelled")
    s.check("made again, it closes itself",
            not any(f"refund to {TAG} Guest GONE" in t for t in _titles()))

    sid_f = _session("FAILW")
    wreg = _reg(sid_f, "FAILW", paid=900.0, stripe_ref="pi_wsfail")
    fake = _FakeStripe()
    ok, err, ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "workshop", wreg, 300, "Goodwill", method="stripe",
        reason_code="goodwill", bill_effect=m.BILL_REDUCED))
    wrid = _q("SELECT stripe_refund_id FROM refunds WHERE id = ?", ids[0])[0][0]
    _hook(oc, fake, "refund.failed", {"id": wrid, "amount": 30000, "status": "failed"})
    due, charged, paid = _call(m.workshop_balance_due, wreg["id"])
    s.check("on an atelier the ledger undoes exactly what the refund did",
            (due, charged, paid) == (0.0, 900.0, 900.0), detail=f"{due}, {charged}, {paid}")

    s.section("A card dispute is kept and followed")
    disp = _stay("DISP", payments=[(400.0, "stripe", "pi_disp", "cs_disp")])
    due_by = int((datetime.now(timezone.utc) + timedelta(days=9)).timestamp())
    dispute = {"id": f"dp_{TAG}1", "amount": 40000, "payment_intent": "pi_disp",
               "reason": "fraudulent", "status": "needs_response",
               "evidence_details": {"due_by": due_by}}
    fake = _FakeStripe()
    _hook(oc, fake, "charge.dispute.created", dispute)
    row = _q("SELECT * FROM payment_disputes WHERE stripe_dispute_id = ?", f"dp_{TAG}1")
    s.check("it is recorded against the booking, with its deadline",
            row and row[0]["booking_id"] == disp["id"] and row[0]["evidence_due_by"],
            detail=str([dict(r) for r in row]))
    s.check("and it is a task and a warning while it is open",
            any(f"disputed: {TAG} Guest DISP" in t for t in _titles())
            and any("being disputed" in w for w in _warnings()))
    _hook(oc, fake, "charge.dispute.closed", dict(dispute, status="lost"))
    back = _q("SELECT amount, reason_code FROM refunds WHERE booking_id = ?", disp["id"])
    s.check("lost, the money is a refund in the record, once",
            [(r["amount"], r["reason_code"]) for r in back] == [(400.0, "chargeback")],
            detail=str([tuple(r) for r in back]))
    _hook(oc, fake, "charge.dispute.closed", dict(dispute, status="lost"))
    s.check("however often Stripe says so",
            len(_q("SELECT id FROM refunds WHERE booking_id = ?", disp["id"])) == 1)
    s.check("and the task closes itself", not any(f"disputed: {TAG} Guest DISP" in t
                                                  for t in _titles()))
    won = _stay("WON", payments=[(400.0, "stripe", "pi_won", "cs_won")])
    _hook(oc, fake, "charge.dispute.created", dict(dispute, id=f"dp_{TAG}2",
                                                   payment_intent="pi_won"))
    _hook(oc, fake, "charge.dispute.closed", dict(dispute, id=f"dp_{TAG}2",
                                                  payment_intent="pi_won", status="won"))
    s.check("a dispute won takes nothing away",
            not _q("SELECT id FROM refunds WHERE booking_id = ?", won["id"]))

    s.section("A decline by somebody who may not give money back moves nothing")
    desk_staff = _preset_client(["guests"], "g")
    asked = _stay("ASK", status="pending", payments=[(150.0, "stripe", "pi_ask", "cs_ask")])
    fake = _FakeStripe()
    r = _with_stripe(fake, lambda: desk_staff.post(f"/admin/bookings/{asked['id']}/decline",
                                                   follow_redirects=True))
    s.check("the request is declined", _q("SELECT status FROM bookings WHERE id = ?",
                                          asked["id"])[0][0] == "declined")
    s.check("but no money moves on their say-so",
            not fake.refunds and not _q("SELECT id FROM refunds WHERE booking_id = ?", asked["id"]),
            detail=str(fake.refunds))
    s.check("and the page says it is on the owner's list",
            any("owner's list" in html.unescape(f) for f in flashes(r)), detail=str(flashes(r)))
    s.check("which it is, until it goes back",
            any(f"give back to {TAG} Guest ASK" in t for t in _titles())
            and any("still to give back" in w for w in _warnings()))
    fake = _FakeStripe()
    _with_stripe(fake, lambda: oc.post(f"/admin/refunds/room/{asked['id']}",
                                       data={"payment": "", "reason_code": "declined",
                                             "method": "stripe"}))
    s.check("the owner gives it back from its refund page, and it leaves the list",
            [k["payment_intent"] for k in fake.refunds] == ["pi_ask"]
            and not any(f"give back to {TAG} Guest ASK" in t for t in _titles()))
    mine = _stay("MINE", status="pending", payments=[(150.0, "stripe", "pi_mine", "cs_mine")])
    fake = _FakeStripe()
    _with_stripe(fake, lambda: oc.post(f"/admin/bookings/{mine['id']}/decline"))
    audited = _q("SELECT COUNT(*) FROM audit_log WHERE action = 'refund_issued' AND target LIKE ?",
                 f"%{TAG}MINE")[0][0]
    s.check("the owner's own decline refunds, with who did it written down",
            [k["payment_intent"] for k in fake.refunds] == ["pi_mine"] and audited == 1
            and _q("SELECT refunded_by_user_id FROM refunds WHERE booking_id = ?",
                   mine["id"])[0][0], detail=f"{fake.refunds} audited {audited}")

    s.section("The house calls it off, and gives it back in the same step")
    off = _stay("OFF", payments=[(400.0, "stripe", "pi_off", "cs_off")])
    fake = _FakeStripe()
    sent.clear()
    _with_stripe(fake, lambda: oc.post(f"/admin/bookings/{off['id']}/cancel",
                                       data={"refund_paid": "1"}, follow_redirects=True))
    rows = _q("SELECT amount, reason_code FROM refunds WHERE booking_id = ?", off["id"])
    s.check("ticked, what they paid goes back to the card",
            [k["payment_intent"] for k in fake.refunds] == ["pi_off"]
            and [(r["amount"], r["reason_code"]) for r in rows] == [(400.0, "house_cancelled")],
            detail=str([tuple(r) for r in rows]))
    s.check("and they are told of the refund, as well as the cancellation",
            any("refund" in subj.lower() for to, subj, _b in sent if to == off["guest_email"]),
            detail=str([x[1] for x in sent]))
    keep = _stay("KEEP", payments=[(400.0, "stripe", "pi_keep", "cs_keep")])
    fake = _FakeStripe()
    _with_stripe(fake, lambda: oc.post(f"/admin/bookings/{keep['id']}/cancel"))
    s.check("unticked, cancelling moves no money", not fake.refunds
            and not _q("SELECT id FROM refunds WHERE booking_id = ?", keep["id"]))
    theirs = _stay("THEIRS", payments=[(400.0, "stripe", "pi_theirs", "cs_theirs")])
    fake = _FakeStripe()
    _with_stripe(fake, lambda: desk_staff.post(f"/admin/bookings/{theirs['id']}/cancel",
                                               data={"refund_paid": "1"}))
    s.check("and ticked by somebody who may not refund, it still moves none",
            not fake.refunds and not _q("SELECT id FROM refunds WHERE booking_id = ?", theirs["id"]))

    sid = _session("CALL")
    card = _reg(sid, "CARD", paid=900.0, stripe_ref="pi_wscard")
    cash = _reg(sid, "CASH", paid=900.0, method="cash")
    fake = _FakeStripe()
    r = _with_stripe(fake, lambda: oc.post(f"/admin/workshops/session/{sid}/call-off",
                                           data={"reason": "The tutor is ill", "refund": "1"},
                                           follow_redirects=True))
    s.check("calling a sitting off refunds each card place to its card",
            [k["payment_intent"] for k in fake.refunds] == ["pi_wscard"]
            and _call(m.refundable_amount, "workshop", card) == 0.0,
            detail=f"{fake.refunds} {flashes(r)}")
    s.check("and what was paid another way is named, and stays on the owner's list",
            any(f"{TAG} Maker CASH" in f for f in flashes(r))
            and any(f"give back to {TAG} Maker CASH" in t for t in _titles()),
            detail=str(flashes(r)))

    s.section("A table declined by somebody who may not refund")
    kitchen = _preset_client(["restaurant"], "r")
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
           dinner_date, party_size, status, payment_status, total_price, deposit_amount,
           stripe_payment_intent_id, created_at)
           VALUES (?, ?, ?, ?, ?, 4, 'pending', 'paid', 200, 50, 'pi_table', ?)""",
        (f"{TAG}TAB", f"tokr{TAG}tab", f"{TAG} Diner", f"{TAG.lower()}.d@example.invalid",
         (house_today() + timedelta(days=5)).isoformat(), _harness.datetime_now()))
    tid = conn.execute("SELECT id FROM restaurant_bookings WHERE reference_code = ?",
                       (f"{TAG}TAB",)).fetchone()["id"]
    conn.commit()
    conn.close()
    fake = _FakeStripe()
    _with_stripe(fake, lambda: kitchen.post(f"/admin/restaurant/{tid}/decline"))
    s.check("the deposit does not move on their say-so", not fake.refunds
            and not _q("SELECT id FROM refunds WHERE booking_id = ? AND category = 'restaurant'", tid))
    s.check("and the owner is asked to decide it",
            _q("SELECT id FROM tasks WHERE title LIKE ? AND status != 'done'",
               f"Refund to decide: {TAG} Diner%"))

    s.section("The refunds page leads with what is still to do")
    text = oc.get("/admin/refunds?q=" + TAG).get_data(as_text=True)
    s.check("money still to give back, by name", "Still to do" in text
            and f"{TAG} Maker CASH" in text)
    s.check("and the card disputes, with where each stands", "Card disputes" in text
            and "they say they did not make it" in text)
