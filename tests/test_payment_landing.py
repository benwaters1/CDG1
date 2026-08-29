"""Where a guest lands after paying, and where they land after not paying.

`/book/payment-success` is the page a guest sees the moment their card goes
through. It is the only route in the app where the guest's own browser is what
records money — the webhook is the other half, and either can arrive first.
That gives it one job it must never get wrong in each direction:

  CREDIT ONCE. Stripe retries webhooks, and a guest reloads a page that took a
  moment. The same Checkout Session therefore arrives several times, and
  crediting twice takes the money off what they owe twice and sends somebody
  home believing they have paid. The guard is the session id written into the
  payments row; the helper is covered by test_booking_payment, and this covers
  the route that calls it.

  CREDIT NOTHING when nothing was paid. A session that came back unpaid, or
  belongs to a different kind of purchase entirely, must leave the bill alone —
  and must still tell the guest something true rather than a blank page.

SAFETY. The harness sets stripe.api_key to None so the library refuses before
any request; that is the layer underneath. The stand-ins here sit ABOVE it, on
stripe_enabled and stripe.checkout.Session.retrieve, so anything this file
forgets to stand in for falls through to the refusal rather than reaching the
real account. The last section checks the block is still in place afterwards,
because a stand-in restored wrongly would leave the NEXT suite talking to
Stripe.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZPAY"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_payments WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, total=900.0, paid=0.0):
    conn = db()
    room = _harness.ensure_room()
    arrival = date.today() + timedelta(days=120)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}@example.invalid", arrival.isoformat(),
         (arrival + timedelta(days=3)).isoformat(), total, paid,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _session(session_id, booking_id, cents, status="paid", kind="room_balance"):
    """A Checkout Session as the route reads it.

    A plain dict on purpose — sval and smeta both fall back to dict access, so
    this exercises the same code path a real StripeObject would without any of
    it leaving the machine.
    """
    return {
        "id": session_id,
        "payment_status": status,
        "amount_total": cents,
        "metadata": {"kind": kind, "booking_id": str(booking_id)},
    }


def _payments(booking_id):
    conn = db()
    try:
        rows = conn.execute(
            """SELECT amount, stripe_session_id FROM booking_payments
               WHERE booking_id = ? ORDER BY id""", (booking_id,)).fetchall()
        return [(r["amount"], r["stripe_session_id"]) for r in rows]
    finally:
        conn.close()


def _owed(booking_id):
    conn = db()
    try:
        bill = m.booking_bill(conn, booking_id)
        return bill["owed"] if bill else None
    finally:
        conn.close()


def run():
    s = Suite("Payment landing")
    _cleanup()
    clients()
    anon = m.app.test_client()

    was_enabled = m.stripe_enabled
    was_session = m.stripe.checkout.Session
    handed_out = []

    class _Stub:
        """Stands in one layer above the harness's block, and records use."""
        @staticmethod
        def retrieve(session_id):
            handed_out.append(session_id)
            found = _Stub.sessions.get(session_id)
            if found is None:
                raise Exception("no such checkout session")
            return found
        sessions = {}

    try:
        s.section("Both halves of the link are required")
        # The route needs the session to know what was paid and the token to
        # know where to send them. Neither alone is enough.
        stay = _stay("A")
        m.stripe_enabled = lambda: True
        m.stripe.checkout.Session = _Stub
        _Stub.sessions = {"sess_a": _session("sess_a", stay["id"], 30000)}

        s.check("no session id is a 404",
                anon.get(f"/book/payment-success?manage_token={stay['manage_token']}"
                         ).status_code == 404)
        s.check("no manage token is a 404",
                anon.get("/book/payment-success?session_id=sess_a").status_code == 404)
        s.check("and nothing was credited by either",
                not _payments(stay["id"]), detail=f"{_payments(stay['id'])}")

        s.section("With Stripe switched off it does not even look")
        m.stripe_enabled = lambda: False
        handed_out.clear()
        r = anon.get(f"/book/payment-success?session_id=sess_a"
                     f"&manage_token={stay['manage_token']}")
        s.check("a 404 rather than an attempt", r.status_code == 404,
                detail=f"HTTP {r.status_code}")
        s.check("and no session was fetched", not handed_out, detail=f"{handed_out}")
        m.stripe_enabled = lambda: True

        s.section("A payment that went through is credited once")
        owed_before = _owed(stay["id"])
        r = anon.get(f"/book/payment-success?session_id=sess_a"
                     f"&manage_token={stay['manage_token']}", follow_redirects=True)
        s.check("the guest is told it worked",
                any("received" in f.lower() or "thank" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:1]}")
        s.check("one payment is recorded", len(_payments(stay["id"])) == 1,
                detail=f"{_payments(stay['id'])}")
        s.check("for the amount Stripe actually took",
                abs(_payments(stay["id"])[0][0] - 300.0) < 0.01,
                detail=f"{_payments(stay['id'])} — 30000 cents is EUR300")
        s.check("and it comes off what they owe",
                abs(_owed(stay["id"]) - (owed_before - 300.0)) < 0.01,
                detail=f"{owed_before} -> {_owed(stay['id'])}")

        s.section("Reloading the page does not charge them again")
        # Stripe retries webhooks and guests reload a page that took a moment.
        owed_after_first = _owed(stay["id"])
        for _ in range(3):
            anon.get(f"/book/payment-success?session_id=sess_a"
                     f"&manage_token={stay['manage_token']}", follow_redirects=True)
        s.check("still exactly one payment", len(_payments(stay["id"])) == 1,
                detail=f"{_payments(stay['id'])} — the money came off the bill "
                       "more than once and somebody goes home thinking they paid")
        s.check("and what they owe has not moved",
                abs(_owed(stay["id"]) - owed_after_first) < 0.01,
                detail=f"{owed_after_first} -> {_owed(stay['id'])}")

        s.section("A payment that did not complete credits nothing")
        unpaid = _stay("B")
        _Stub.sessions["sess_b"] = _session("sess_b", unpaid["id"], 30000, status="unpaid")
        r = anon.get(f"/book/payment-success?session_id=sess_b"
                     f"&manage_token={unpaid['manage_token']}", follow_redirects=True)
        s.check("nothing is recorded", not _payments(unpaid["id"]),
                detail=f"{_payments(unpaid['id'])}")
        # flashes() returns the escaped text, so an apostrophe arrives as
        # &#39; — matching on "wasn't" silently never matches.
        import html as _html
        said = " ".join(_html.unescape(f) for f in flashes(r)).lower()
        s.check("and the guest is told plainly, not shown a blank page",
                "completed" in said and ("wasn't" in said or "not completed" in said),
                detail=f"{flashes(r)[:1]} — a guest whose card failed needs to "
                       "know, or they arrive believing they have paid")

        s.section("A session for something else does not pay for a stay")
        # kind is checked before anything is written. A workshop deposit
        # arriving here must not come off a room bill.
        other = _stay("C")
        _Stub.sessions["sess_c"] = _session("sess_c", other["id"], 50000,
                                            kind="workshop_deposit")
        anon.get(f"/book/payment-success?session_id=sess_c"
                 f"&manage_token={other['manage_token']}", follow_redirects=True)
        s.check("the stay is not credited", not _payments(other["id"]),
                detail=f"{_payments(other['id'])} — a workshop payment was "
                       "taken off a room bill")

        s.section("A zero-amount session is not a payment")
        free = _stay("D")
        _Stub.sessions["sess_d"] = _session("sess_d", free["id"], 0)
        anon.get(f"/book/payment-success?session_id=sess_d"
                 f"&manage_token={free['manage_token']}", follow_redirects=True)
        s.check("nothing is recorded", not _payments(free["id"]),
                detail=f"{_payments(free['id'])}")

        s.section("A session id nobody issued is refused, not a 500")
        r = anon.get(f"/book/payment-success?session_id=made_up"
                     f"&manage_token={stay['manage_token']}")
        s.check("404", r.status_code == 404, detail=f"HTTP {r.status_code}")
        s.check("and the earlier payment is untouched",
                len(_payments(stay["id"])) == 1, detail=f"{_payments(stay['id'])}")
    finally:
        m.stripe_enabled = was_enabled
        m.stripe.checkout.Session = was_session

    s.section("Paying a workshop deposit when there is nothing to pay")
    # Refuses by explaining and sending them back to their own page, rather
    # than erroring — none of the reasons is the guest's mistake.
    conn = db()
    reg = conn.execute(
        "SELECT manage_token FROM workshop_bookings WHERE manage_token IS NOT NULL LIMIT 1"
    ).fetchone()
    conn.close()
    if reg:
        r = anon.get(f"/workshops/pay-deposit/{reg['manage_token']}", follow_redirects=True)
        s.check("it comes back as a page, not an error", r.status_code == 200,
                detail=f"HTTP {r.status_code}")
        s.check("and says why rather than failing silently",
                bool(flashes(r)) or "deposit" in r.get_data(as_text=True).lower(),
                detail=f"{flashes(r)[:1]}")
    s.check("a registration nobody holds is a 404",
            anon.get("/workshops/pay-deposit/not-a-token").status_code == 404)

    s.section("Its URL is its own, and the balance path does not 500")
    # Both handlers were registered on /book/stripe-success. Werkzeug serves the
    # first, so this one was unreachable and a guest paying a balance was
    # answered by the new-booking handler — which tried to build a stay out of a
    # payment session, whose metadata has no room_id. KeyError, 500, no payment
    # recorded, on the page somebody lands on immediately after paying.
    rules = [r for r in m.app.url_map.iter_rules()
             if r.endpoint in ("stripe_success", "booking_stripe_success")]
    paths = {r.endpoint: str(r.rule) for r in rules}
    s.check("the two success handlers have different URLs",
            len(set(paths.values())) == 2, detail=f"{paths}")
    adapter = m.app.url_map.bind("localhost")
    served, _args = adapter.match(paths.get("booking_stripe_success", "/nope"), method="GET")
    s.check("and the payment one is actually reachable",
            served == "booking_stripe_success",
            detail=f"that path is served by {served!r} instead")

    s.section("A payment session cannot be mistaken for a new booking")
    conn = db()
    made = m.create_booking_from_stripe_session(
        conn, {"id": "sess_nokey", "payment_status": "paid", "amount_total": 1000,
               "metadata": {"kind": "room_balance", "booking_id": "1"}})
    conn.close()
    s.check("it declines rather than raising", made is None,
            detail="a KeyError here reaches the guest as a 500 after they paid")

    s.section("And Stripe is still switched off for whoever runs next")
    # A stand-in restored wrongly would leave the next suite talking to the
    # live account, and it would pass while doing it.
    s.check("stripe_enabled is back to the harness's answer", not m.stripe_enabled(),
            detail="a stand-in was left installed")
    s.check("and the api key is still blank",
            not getattr(m.stripe, "api_key", None),
            detail="the library would now accept a real request")
    s.check("Session.retrieve is the library's own again",
            m.stripe.checkout.Session is not _Stub,
            detail="the stub outlived this suite")

    _cleanup()
    return s
