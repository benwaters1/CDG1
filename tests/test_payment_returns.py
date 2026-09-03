"""Coming back from the card page, and what happens when there is no card page.

Three routes the suite reached and only ever got a 404 out of, because Stripe
is pinned off here and every one of them checks that first. So the branch the
tests ran was "we do not know that payment", which is the branch that matters
least, and the coverage figure called all three tested.

The branches that matter need no Stripe at all:

  A GUEST WHO REFRESHES THE SUCCESS PAGE must not get a second booking. Both
  the room and the restaurant route look the session id up first and, if they
  already have it, send the guest to their booking. That is the whole of the
  idempotency, it runs before any call to Stripe, and it had never run.

  A PAYMENT THAT CANNOT START must say so and give a way back. workshop
  deposit asks for a checkout URL, gets nothing when Stripe is off, and is
  supposed to flash and return the guest to their booking. Every guest of
  this house hits that branch today, since the château is not taking cards
  online yet, and it was the one branch nothing exercised.

HOW THIS STAYS SAFE. stripe_enabled() is flipped on for a few lines and put
back in a finally. That alone would be a loaded gun, so for the duration
stripe.checkout.Session.retrieve is replaced with a function that RAISES:
if any of these routes ever falls through to the real call, this suite fails
loudly instead of reaching the payment provider. The harness's own assertion
that the key is neutralised is re-checked at the end.
"""
from _harness import Suite, db, house_today

import _harness

m = _harness.m
TAG = "ZZPAY"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.commit()


class _Loaded:
    """Stripe answered for the length of one branch, and never actually.

    stripe_enabled() is what these routes check first, so it has to be true
    to reach anything. The retrieve call behind it is replaced with a raise,
    because "the branch returns before it" is a claim about today's code and
    this is a test of today's code -- if that stops being true it must fail
    here rather than dial out.
    """

    def __enter__(self):
        self.real_enabled = m.stripe_enabled
        self.real_retrieve = m.stripe.checkout.Session.retrieve
        m.stripe_enabled = lambda: True

        def _refuse(*_a, **_kw):
            raise AssertionError(
                "a payment-return route reached Stripe. The branch under test "
                "is supposed to answer before this call.")

        m.stripe.checkout.Session.retrieve = _refuse
        return self

    def __exit__(self, *_exc):
        m.stripe_enabled = self.real_enabled
        m.stripe.checkout.Session.retrieve = self.real_retrieve
        return False


def run():
    s = Suite("coming back from the card page")
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today()
    guest = m.app.test_client()

    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.section("Setup")
        s.check("a room exists", False, detail="reported rather than skipped")
        conn.close()
        return s

    s.section("Refreshing the success page does not book the room twice")
    sid = TAG + "-sess-room"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, arrival_date, departure_date,
                   party_size, status, total_price, stripe_session_id,
                   created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?, ?)""",
        (room["id"], TAG + "ROOM", TAG.lower() + "-room", TAG + " Guest",
         f"{TAG}.r@example.invalid".lower(),
         (today.replace(day=1)).isoformat(),
         (today.replace(day=2)).isoformat(), sid, now))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

    with _Loaded():
        r = guest.get(f"/book/stripe-success?session_id={sid}", follow_redirects=False)
    s.check("it redirects rather than 404s", r.status_code in (301, 302),
            detail=f"status {r.status_code}")
    s.check("and lands on the booking they already have",
            TAG.lower() + "-room" in (r.headers.get("Location") or ""),
            detail=r.headers.get("Location") or "no location")
    after = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    s.check("with no second booking made", after == before,
            detail=f"{before} before, {after} after — a guest who "
                   "refreshes must not be charged into two rooms")

    s.section("And the same for a table")
    rsid = TAG + "-sess-rest"
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, guest_name,
                   guest_email, dinner_date, party_size, status,
                   manage_token, stripe_session_id, created_at)
           VALUES (?, ?, ?, ?, 2, 'confirmed', ?, ?, ?)""",
        (TAG + "REST", TAG + " Diner", f"{TAG}.d@example.invalid".lower(),
         today.isoformat(), TAG.lower() + "-rest", rsid, now))
    conn.commit()
    r_before = conn.execute(
        "SELECT COUNT(*) FROM restaurant_bookings").fetchone()[0]
    with _Loaded():
        r = guest.get(f"/restaurant/stripe-success?session_id={rsid}",
                      follow_redirects=False)
    s.check("it redirects rather than 404s", r.status_code in (301, 302),
            detail=f"status {r.status_code}")
    s.check("and lands on the table they already have",
            TAG.lower() + "-rest" in (r.headers.get("Location") or ""),
            detail=r.headers.get("Location") or "no location")
    r_after = conn.execute(
        "SELECT COUNT(*) FROM restaurant_bookings").fetchone()[0]
    s.check("with no second table booked", r_after == r_before)

    s.section("A deposit that cannot be paid online says so")
    # The branch every guest of this house takes today, because the château
    # is not taking cards online yet. It had never run: the only test of this
    # route posted a token that does not exist and got the 404.
    ws = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()
    if ws:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code,
                       guest_name, guest_email, party_size, status,
                       manage_token, deposit_amount, created_at)
               VALUES (?, ?, ?, ?, 1, 'confirmed', ?, 150, ?)""",
            (ws["id"], TAG + "WS", TAG + " Maker",
             f"{TAG}.m@example.invalid".lower(), TAG.lower() + "-ws", now))
        conn.commit()
        r = guest.get(f"/workshops/pay-deposit/{TAG.lower()}-ws",
                      follow_redirects=True)
        body = r.get_data(as_text=True)
        s.check("the guest is not shown an error page", r.status_code == 200,
                detail=f"status {r.status_code}")
        # Matched on the half with no apostrophe in it: the page escapes it
        # to &#39;, so the obvious search for the sentence as written in app.py
        # fails on a page that is saying exactly the right thing.
        s.check("they are told it cannot be paid online",
                "paid online right now" in body,
                detail="a dead button with no explanation is the email this "
                       "page exists to stop")
        s.check("and they land back on their own booking",
                TAG + " Maker" in body or "workshop" in r.request.path,
                detail=r.request.path)
    else:
        s.check("a workshop session exists to book onto", False,
                detail="reported rather than skipped: the checks above would "
                       "pass on nothing")

    s.section("And Stripe was never actually reachable")
    s.check("the key is still neutralised", not getattr(m.stripe, "api_key", None),
            detail="the harness clears it at import and this suite turns "
                   "stripe_enabled on for a few lines; the key is what stops "
                   "a mistake becoming a real call")
    s.check("and stripe_enabled is back off", m.stripe_enabled() is False,
            detail="left on, every later suite would take a different path "
                   "through the app than the one it was written for")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
