"""Paying part of a workshop balance.

A workshop is a few thousand euros booked months ahead, and a guest may want to
chip away at it rather than settle it in one go. The ledger could already
represent that — charges and payments sum correctly — but there was no way for a
guest to make a part-payment, only the whole balance.

The interesting part is not the happy path but the bounds. An amount above what
is owed would take money the château is not due. A trivial amount costs more in
card fees than it settles. And the whole-balance link already in guests' inboxes
has to keep working untouched, which is why "no amount" and "the whole balance"
are the same request through the same route.
"""
from datetime import date, timedelta

from _harness import Suite, db, house_today
import _harness

m = _harness.m
TAG = "ZZPP"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(total=2000.0, deposit_paid=True):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', ?, 8, 1, 97, ?, 30)""", (f"{TAG} Atelier", total, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=120)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 8, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} sitting", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} sitting",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           deposit_paid_at) VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} guest", f"{TAG.lower()}@example.invalid", f"{TAG}1", f"tok{TAG}1", now,
         total, total * 0.3, total * 0.7, now if deposit_paid else None))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}1",)).fetchone()["id"]
    # The deposit, so there is a real balance left to chip at.
    if deposit_paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", total * 0.3, method="stripe")
        conn.commit()
    conn.close()
    return bid


def run():
    s = Suite("Part payments")
    _cleanup()
    bid = _booking(total=2000.0)
    pub = m.app.test_client()

    s.section("What is owed to begin with")
    conn = db()
    due, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("2000 charged", charged == 2000.0, detail=f"got {charged}")
    s.check("600 deposit received", paid == 600.0, detail=f"got {paid}")
    s.check("so 1400 is the balance", due == 1400.0, detail=f"got {due}")

    s.section("A part-payment lands on the ledger")
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Part payment — Stripe", 500.0, method="stripe")
    conn.commit()
    due, charged, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("received goes up by the part paid", paid == 1100.0, detail=f"got {paid}")
    s.check("the total charged does not move", charged == 2000.0, detail=f"got {charged}")
    s.check("and the balance comes down to 900", due == 900.0, detail=f"got {due}")

    s.section("Two part-payments settle it exactly")
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Part payment — Stripe", 900.0, method="stripe")
    conn.commit()
    due, _, paid = m.workshop_balance_due(conn, bid)
    conn.close()
    s.check("nothing is left owing", due == 0.0, detail=f"got {due}")
    s.check("without overshooting", paid == 2000.0, detail=f"got {paid}")

    s.section("The bounds — where money goes wrong")
    _cleanup()
    bid = _booking(total=2000.0)
    token = f"tok{TAG}1"
    # More than is owed would take money the château is not due.
    r = pub.post(f"/workshops/pay-balance/{token}", data={"amount": "5000"},
                 follow_redirects=True)
    conn = db()
    after = m.workshop_balance_due(conn, bid)[0]
    conn.close()
    s.check("more than the balance is refused", after == 1400.0, detail=f"balance is now {after}")
    s.check("and the guest is told the range",
            any("between" in f.lower() for f in _harness.flashes(r)),
            detail=f"flashes: {_harness.flashes(r)}")

    # Below the floor the card fee is a large share of what is settled.
    r = pub.post(f"/workshops/pay-balance/{token}", data={"amount": "3"}, follow_redirects=True)
    s.check("a trivial amount is refused",
            any("between" in f.lower() for f in _harness.flashes(r)),
            detail=f"flashes: {_harness.flashes(r)}")
    s.check("the floor is stated once in the code, not typed twice",
            m.PART_PAYMENT_MINIMUM == 20.0, detail=f"got {m.PART_PAYMENT_MINIMUM}")

    # Junk in the box must not become a charge of some other size.
    for junk in ("abc", "", "-50"):
        r = pub.post(f"/workshops/pay-balance/{token}", data={"amount": junk},
                     follow_redirects=True)
        s.check(f"{junk!r} does not become a payment", r.status_code == 200,
                detail=f"HTTP {r.status_code}")
    conn = db()
    untouched = m.workshop_balance_due(conn, bid)[0]
    conn.close()
    s.check("and none of it moved the balance", untouched == 1400.0, detail=f"got {untouched}")

    s.section("The old whole-balance link still works")
    # Stripe is off in tests, so the honest outcome is a polite refusal rather
    # than a checkout — what matters is that the GET is still accepted.
    r = pub.get(f"/workshops/pay-balance/{token}", follow_redirects=True)
    s.check("a plain GET with no amount is still the whole balance",
            r.status_code == 200, detail=f"HTTP {r.status_code}")
    r = pub.get("/workshops/pay-balance/not-a-real-token")
    s.check("an unknown token is a clean 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    s.section("The balance cannot be part-paid before the deposit")
    _cleanup()
    unpaid = _booking(total=2000.0, deposit_paid=False)
    conn = db()
    blocked = m.start_workshop_stripe_payment(conn, unpaid, "balance", amount_override=100.0)
    conn.close()
    s.check("a part-payment is refused while the deposit is outstanding",
            blocked is None, detail=f"got {blocked!r}")

    s.section("The form offers what the route accepts")
    page = pub.get(f"/workshops/manage/tok{TAG}1").get_data(as_text=True)
    s.check("the manage page renders", "Balance due" in page or "balance" in page.lower())
    # The form itself only appears with Stripe configured, which tests pin off,
    # so this reads the template: the point is that the floor is interpolated
    # from the app rather than typed, so the form and the route cannot drift
    # apart and start disagreeing about what is allowed.
    import os
    tpl = open(os.path.join(_harness.ROOT, "templates", "workshop_manage.html"),
               encoding="utf-8").read()
    s.check("the form's minimum comes from the app, not a typed number",
            'min="{{ part_payment_minimum }}"' in tpl,
            detail="the form's floor could drift from the route's")
    s.check("and the route hands it over", "part_payment_minimum=PART_PAYMENT_MINIMUM"
            in open(os.path.join(_harness.ROOT, "app.py"), encoding="utf-8").read())

    # These three exist because the block really does keep getting lost. A
    # handover zip (final_17) arrived carrying a workshop_manage.html from
    # before part-payments landed -- 35 lines of pure deletion -- and only the
    # `min=` check above failed, with a message about the floor drifting. That
    # reads like a rounding quibble, not "the guest can no longer pay part of
    # their balance". Each of these names the actual loss instead.
    s.check("the part-payment form is still on the page",
            "workshop_pay_balance" in tpl and "Or pay part of it" in tpl,
            detail="the part-payment form has been deleted from "
                   "templates/workshop_manage.html — a guest can no longer pay "
                   "part of a balance. Likely a handover template from before "
                   "the feature landed.")
    s.check("it still posts the amount field the route reads",
            'name="amount"' in tpl,
            detail="the amount input was renamed or removed, so every "
                   "part-payment silently becomes a full one")
    s.check("and it still carries a CSRF token",
            tpl.count("csrf_token()") >= 2,
            detail=f"only {tpl.count('csrf_token()')} csrf_token() call(s) in the "
                   "template — a form has lost its token and will be rejected")

    _cleanup()
    return s
