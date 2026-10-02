# -*- coding: utf-8 -*-
"""The three routes that START a card payment, on the branch where one starts.

Every other payment suite in here tests coming BACK — the success page, the
cancel page, the webhook, the guest who refreshes. These three are the other
end, and until now the only branch of them anything ran was the refusal:
Stripe is stood down under test, all three check that first, and all three
answer "this can't be paid online right now". That answer is correct and it
is tested elsewhere. It is also the branch that will stop being taken the day
the live keys go in, which makes it the least interesting one to have cover.

What had never run:

  THE REDIRECT ITSELF. All three answer 303, not 302, and the distinction is
  not cosmetic — a 302 invites the browser to repeat the original method, and
  the two workshop routes accept POST. Nothing had ever read the code.

  THE AMOUNT. Each route hands Stripe a figure in CENTS, built by a different
  path: the deposit off the registration, the balance off the ledger, the
  tab off whatever is outstanding after part payments. A wrong figure here
  renders perfectly and charges the wrong money. The stand-in records what
  was actually asked for, so these check the number rather than the redirect.

  THE PART-PAYMENT BOUNDS, ON THE ACCEPTING SIDE. workshop_pay_balance takes
  an optional amount and guards it with `part < PART_PAYMENT_MINIMUM or
  part > due`. Only the rejecting half had ever run. Both ends are checked
  here exactly ON the boundary, because `<` and `<=` are one character apart
  and the failure is silent: the wrong one either refuses a guest paying off
  their whole balance, or takes an amount the card fee eats.

  THE SESSION ID ON THE TAB. pos_pay_link writes stripe_session_id onto the
  order, and settle_pos_from_stripe_session looks the payment up by exactly
  that. If the write never happens the guest pays, the money arrives at
  Stripe, and the tab stays open with nobody told. That write had never run.

HOW THIS STAYS SAFE, which matters more here than anywhere else in the suite
because these are the routes whose whole job is to call Stripe.

Three layers, and the first two are the harness's, not this file's:
_harness sets stripe.api_key to None at import, so the library itself refuses
before any request leaves; and stripe_enabled() is False. This file flips
only the second, for the length of a `with`, and puts it back in a finally.
For that window every Stripe entry point these routes can reach is replaced:
Session.create and Customer.create with recorders, Session.retrieve with a
function that RAISES. If a branch ever falls through to a call this file did
not anticipate, it fails loudly here instead of dialling out. The harness's
own assertion that the key is neutralised is re-checked at the end, so a
stand-in that failed to restore cannot be left behind for the next suite.
"""
from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZCKO"
FAKE_URL = "https://checkout.stripe.invalid/c/pay/" + TAG


def _cleanup():
    conn = db()
    rows = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code LIKE ?",
                        (TAG + "%",)).fetchall()
    for r in rows:
        conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id = ?",
                     (r["id"],))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    tabs = conn.execute("SELECT id FROM pos_orders WHERE table_label LIKE ?",
                        (TAG + "%",)).fetchall()
    for t in tabs:
        conn.execute("DELETE FROM pos_order_lines WHERE order_id = ?", (t["id"],))
        conn.execute("DELETE FROM pos_payments WHERE order_id = ?", (t["id"],))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


class _Made:
    """Whatever Stripe was handed, without Stripe."""

    def __init__(self, **kw):
        self.kw = kw
        self.id = TAG + "-session-id"
        self.url = FAKE_URL


class _CardMachine:
    """Stripe answers, for the length of one branch, and never actually.

    Records every Session.create so the checks can read the amount that was
    asked for rather than trusting the redirect. Session.retrieve is replaced
    with a raise: nothing under test here retrieves anything, and "it returns
    before that call" is a claim about today's code that must fail here rather
    than reach the network if it stops being true.
    """

    def __enter__(self):
        self.sessions = []
        self.customers = []
        self._enabled = m.stripe_enabled
        self._create = m.stripe.checkout.Session.create
        self._retrieve = m.stripe.checkout.Session.retrieve
        self._customer = m.stripe.Customer.create
        m.stripe_enabled = lambda: True

        def _session(**kw):
            self.sessions.append(kw)
            return _Made(**kw)

        def _cust(**kw):
            self.customers.append(kw)
            return _Made(**kw)

        def _refuse(*_a, **_kw):
            raise AssertionError(
                "a checkout-start route reached Stripe on a call this suite "
                "did not stand in. Nothing here is supposed to retrieve.")

        m.stripe.checkout.Session.create = _session
        m.stripe.checkout.Session.retrieve = _refuse
        m.stripe.Customer.create = _cust
        return self

    def __exit__(self, *_exc):
        m.stripe_enabled = self._enabled
        m.stripe.checkout.Session.create = self._create
        m.stripe.checkout.Session.retrieve = self._retrieve
        m.stripe.Customer.create = self._customer
        return False

    def cents(self, which=-1):
        """What the last checkout was actually asked to charge."""
        if not self.sessions:
            return None
        return self.sessions[which]["line_items"][0]["price_data"]["unit_amount"]


def run():
    s = Suite("starting a card payment, on the branch where one starts")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    conn = db()
    now = _harness.datetime_now()
    guest = m.app.test_client()

    sess = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()
    if not sess:
        s.check("a workshop sitting exists to register onto", False,
                detail="reported rather than skipped — every check below "
                       "would pass on nothing")
        conn.close()
        return s

    tok = TAG.lower() + "-ws"
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                   guest_name, guest_email, party_size, status, total_price,
                   deposit_amount, created_at)
           VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 600, 150, ?)""",
        (sess["id"], TAG + "WS", tok, TAG + " Maker",
         (TAG + ".m@example.invalid").lower(), now))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?",
                       (tok,)).fetchone()["id"]

    s.section("A deposit that CAN be paid online")
    with _CardMachine() as card:
        r = guest.get("/workshops/pay-deposit/" + tok, follow_redirects=False)
    s.check("the guest is sent to the card page",
            r.status_code == 303,
            detail="status %s — 303 and not 302, because a 302 invites the "
                   "browser to repeat the original method" % r.status_code)
    s.check("and it is the URL Stripe gave us",
            (r.headers.get("Location") or "") == FAKE_URL,
            detail=r.headers.get("Location") or "no location")
    s.check("Stripe was asked for the deposit, in cents",
            card.cents() == 15000,
            detail="asked for %s, the registration says 150.00" % card.cents())

    s.section("The balance, once the deposit is in")
    conn.execute("UPDATE workshop_bookings SET deposit_paid_at = ? WHERE id = ?",
                 (now, bid))
    conn.commit()
    m.add_workshop_transaction(conn, bid, "payment", "deposit taken", 150.0)
    conn.commit()
    due, _charged, _paid = m.workshop_balance_due(conn, bid)
    s.check("the ledger says 450 is left on a 600 place with 150 paid",
            abs(due - 450.0) < 0.01, detail=str(due))

    with _CardMachine() as card:
        r = guest.get("/workshops/pay-balance/" + tok, follow_redirects=False)
    s.check("asking to pay the balance reaches the card page",
            r.status_code == 303, detail="status %s" % r.status_code)
    s.check("for the whole of what is left",
            card.cents() == 45000,
            detail="asked for %s, the ledger says %.2f" % (card.cents(), due))

    s.section("A part payment, which is the half that had never run")
    with _CardMachine() as card:
        r = guest.post("/workshops/pay-balance/" + tok, data={"amount": "50"},
                       follow_redirects=False)
    s.check("a part payment is accepted", r.status_code == 303,
            detail="status %s" % r.status_code)
    s.check("and Stripe is asked for exactly that, not the whole balance",
            card.cents() == 5000,
            detail="asked for %s — the amount must reach the checkout, not "
                   "just pass the guard" % card.cents())

    # The boundaries themselves. `part < PART_PAYMENT_MINIMUM or part > due`
    # is one character away from refusing a guest who pays off their balance,
    # or taking an amount the card fee eats, and both fail silently.
    with _CardMachine() as card:
        r = guest.post("/workshops/pay-balance/" + tok,
                       data={"amount": "%.2f" % m.PART_PAYMENT_MINIMUM},
                       follow_redirects=False)
    s.check("the minimum itself is allowed, not refused",
            r.status_code == 303 and card.cents() == int(m.PART_PAYMENT_MINIMUM * 100),
            detail="status %s, asked for %s — the guard is `< MINIMUM`, so "
                   "the minimum is payable" % (r.status_code, card.cents()))

    with _CardMachine() as card:
        r = guest.post("/workshops/pay-balance/" + tok,
                       data={"amount": "%.2f" % due}, follow_redirects=False)
    s.check("and so is the whole balance to the cent",
            r.status_code == 303 and card.cents() == int(round(due * 100)),
            detail="status %s, asked for %s — the guard is `> due`, so the "
                   "exact balance is payable" % (r.status_code, card.cents()))

    s.section("And the amounts that must not reach a card at all")
    with _CardMachine() as card:
        r = guest.post("/workshops/pay-balance/" + tok, data={"amount": "5"},
                       follow_redirects=False)
        under = list(card.sessions)
    s.check("a few euros is turned away", r.status_code in (301, 302),
            detail="status %s" % r.status_code)
    s.check("and no checkout was opened for it", not under,
            detail="%d session(s) created — a refused amount must not reach "
                   "Stripe at all" % len(under))

    with _CardMachine() as card:
        r = guest.post("/workshops/pay-balance/" + tok,
                       data={"amount": "%.2f" % (due + 100)},
                       follow_redirects=False)
        over = list(card.sessions)
    s.check("more than is owed is turned away", r.status_code in (301, 302),
            detail="status %s" % r.status_code)
    s.check("and no checkout was opened for that either", not over,
            detail="%d session(s) created" % len(over))

    s.section("The till's pay-link, and the one write the webhook depends on")
    conn.execute(
        "INSERT INTO pos_orders (table_label, covers, status, opened_at) "
        "VALUES (?, 2, 'open', ?)", (TAG + "TAB", now))
    conn.commit()
    tab = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                       (TAG + "TAB",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                   created_at) VALUES (?, ?, 40.0, 2, ?)""",
        (tab, TAG + " dish", now))
    conn.commit()
    bill = m.pos_bill(conn, tab)
    outstanding = bill["outstanding"]
    s.check("the tab is open with something on it", outstanding > 0,
            detail="outstanding %.2f" % outstanding)

    with _CardMachine() as card:
        r = oc.post("/pos/%d/pay-link" % tab, follow_redirects=False)
    s.check("the till is sent to the card page", r.status_code == 303,
            detail="status %s" % r.status_code)
    s.check("and it is the URL Stripe gave us",
            (r.headers.get("Location") or "") == FAKE_URL,
            detail=r.headers.get("Location") or "no location")
    s.check("Stripe was asked for what is outstanding",
            card.cents() == int(round(outstanding * 100)),
            detail="asked for %s, the bill says %.2f" % (card.cents(), outstanding))

    # The prize. settle_pos_from_stripe_session finds the payment by this id;
    # without the write the guest pays, Stripe takes the money, and the tab
    # stays open with nobody told.
    saved = conn.execute("SELECT stripe_session_id FROM pos_orders WHERE id = ?",
                         (tab,)).fetchone()["stripe_session_id"]
    s.check("the session id is written onto the tab",
            saved == TAG + "-session-id",
            detail="stored %r — the webhook settles a tab by looking this up, "
                   "so without it the money arrives and the tab stays open"
                   % saved)

    s.section("A tab with nothing to pay does not reach a card")
    conn.execute("UPDATE pos_orders SET status = 'paid' WHERE id = ?", (tab,))
    conn.commit()
    with _CardMachine() as card:
        r = oc.post("/pos/%d/pay-link" % tab, follow_redirects=False)
        closed = list(card.sessions)
    s.check("a closed tab is refused", r.status_code in (301, 302),
            detail="status %s" % r.status_code)
    s.check("and no checkout was opened for it", not closed,
            detail="%d session(s) created" % len(closed))

    s.section("Nothing was left standing in")
    # A stand-in that failed to restore would hand the next suite a live
    # Stripe, and the harness only asserts this at import.
    s.check("stripe_enabled() is off again", not m.stripe_enabled())
    s.check("and the key is still neutralised",
            not getattr(m.stripe, "api_key", None),
            detail="the harness blanks this at import; a suite that leaves it "
                   "set has armed every run after it")
    s.check("Session.create is the library's again",
            m.stripe.checkout.Session.create.__module__ != __name__,
            detail=str(getattr(m.stripe.checkout.Session.create, "__module__", "?")))

    conn.close()
    _cleanup()
    return s
