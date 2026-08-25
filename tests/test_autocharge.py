"""Charging the balance on its due date, with nobody present.

This is the only thing in the app that takes money without a person there to
agree to it, so the failure paths matter more than the happy one and are what
most of this file is about.

  - It must never charge twice. Stripe is asked with an idempotency key, so a
    retry, a restart or two workers return the first charge instead of making
    a second. Without it the château could take several thousand euros twice.
  - A declined card must be tried once, not daily. Six bank alerts and still
    no payment is worse than one email asking them to pay.
  - Somebody who said they would rather pay by transfer is never charged.
  - The amount comes from the ledger at the moment of charging, so a guest who
    part-paid last week is charged only what is left.

Stripe is pinned off in tests, so the job is driven against a stand-in that
records what it was asked for and can be told to fail the way a real card
does. That is enough to prove the decisions; it is not a substitute for
charging a real test card before the job is switched on.
"""
from datetime import date, timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZAC"


class FakeStripeError(Exception):
    """Stands in for stripe.error.CardError, which carries a guest-facing
    message separate from the developer one."""
    def __init__(self, message):
        super().__init__(message)
        self.user_message = message


class FakeIntents:
    """Records every charge asked for, and can be told to refuse."""
    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with
        self.keys = set()

    def create(self, **kw):
        if self.fail_with:
            raise FakeStripeError(self.fail_with)
        # A real Stripe would return the first charge for a repeated key
        # rather than making a second one. Mirrored so a double-run here
        # behaves the way production would.
        key = kw.get("idempotency_key")
        if key in self.keys:
            return {"id": "pi_repeat", "status": "succeeded"}
        self.keys.add(key)
        self.calls.append(kw)
        return {"id": f"pi_{len(self.calls)}", "status": "succeeded"}


class FakeStripe:
    def __init__(self, fail_with=None):
        self.PaymentIntent = FakeIntents(fail_with)


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE '%Balance%' OR subject LIKE '%did not go through%'")
    conn.commit()
    conn.close()


def _booking(ref, due_date, *, card=True, opt_out=0, failed_at=None,
             status="confirmed", total=2000.0, paid=600.0):
    conn = db()
    now = _harness.datetime_now()
    if not conn.execute("SELECT 1 FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone():
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, default_capacity,
               active, sort_order, created_at, deposit_percent)
               VALUES (?, '', ?, 20, 1, 96, ?, 30)""", (f"{TAG} Atelier", total, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    if not conn.execute("SELECT 1 FROM workshop_sessions WHERE notes = ?", (f"{TAG} sitting",)).fetchone():
        start = date.today() + timedelta(days=200)
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
               VALUES (?, ?, ?, 20, ?, ?)""",
            (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} sitting", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} sitting",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           deposit_paid_at, balance_due_date, stripe_customer_id, stripe_payment_method_id,
           autocharge_opt_out, autocharge_failed_at)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} {ref}", f"{TAG.lower()}{ref}@example.invalid", status, f"{TAG}{ref}",
         f"tok{TAG}{ref}", now, total, total * 0.3, total * 0.7, now, due_date.isoformat(),
         "cus_test" if card else None, "pm_test" if card else None, opt_out, failed_at))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", paid, method="stripe")
        conn.commit()
    conn.close()
    return bid


def _run(fake):
    """Run the job with Stripe stood in for, and put the real one back."""
    real_stripe, real_key = m.stripe, m.STRIPE_SECRET_KEY
    m.stripe, m.STRIPE_SECRET_KEY = fake, "sk_test_stand_in"
    conn = db()
    try:
        with m.app.test_request_context():
            return m.run_workshop_autocharge_job(conn)
    finally:
        conn.commit()
        conn.close()
        m.stripe, m.STRIPE_SECRET_KEY = real_stripe, real_key


def run():
    s = Suite("Auto-charge")
    _cleanup()
    today = m.datetime.now(m.timezone.utc).date()

    s.section("It is off until somebody turns it on")
    # It takes money unattended. Shipping it enabled would charge cards on the
    # first deploy, before anyone had tested a real one.
    s.check("the job ships disabled",
            m.AUTOMATION_SETTING_DEFAULTS["automation_workshop_autocharge_enabled"] == "0",
            detail="it would start charging on deploy")
    s.check("but it is registered, so it can be switched on",
            any(j[0] == "workshop_autocharge" for j in m.AUTOMATION_JOBS))

    s.section("With no Stripe configured it does nothing at all")
    conn = db()
    out = m.run_workshop_autocharge_job(conn)
    conn.close()
    s.check("it declines to run rather than erroring",
            out.get("charged") == 0 and "skipped" in out, detail=f"got {out}")

    s.section("A balance due today is charged for exactly what is left")
    bid = _booking("Due", today)
    fake = FakeStripe()
    out = _run(fake)
    s.check("one booking is charged", out["charged"] == 1, detail=f"got {out}")
    s.check("for the outstanding 1400, not the 2000 total",
            fake.PaymentIntent.calls and fake.PaymentIntent.calls[0]["amount"] == 140000,
            detail=f"asked for {fake.PaymentIntent.calls[0]['amount'] if fake.PaymentIntent.calls else None} cents")
    s.check("off-session, since nobody is there to confirm it",
            fake.PaymentIntent.calls[0].get("off_session") is True)
    s.check("with an idempotency key, or a retry charges twice",
            bool(fake.PaymentIntent.calls[0].get("idempotency_key")))
    conn = db()
    due_after = m.workshop_balance_due(conn, bid)[0]
    conn.close()
    s.check("and the ledger shows nothing left owing", due_after == 0.0, detail=f"got {due_after}")

    s.section("Running it again takes nothing more")
    # The realistic accident: a restart, a retry, two workers.
    out = _run(FakeStripe())
    conn = db()
    still = m.workshop_balance_due(conn, bid)[0]
    conn.close()
    s.check("a settled booking is skipped", out["charged"] == 0, detail=f"got {out}")
    s.check("and the balance stays at zero rather than going negative",
            still == 0.0, detail=f"got {still}")

    s.section("A guest who part-paid is charged only the remainder")
    _cleanup()
    bid = _booking("Part", today)
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Part payment", 400.0, method="stripe")
    conn.commit()
    conn.close()
    fake = FakeStripe()
    _run(fake)
    s.check("1000 is taken, not 1400",
            fake.PaymentIntent.calls and fake.PaymentIntent.calls[0]["amount"] == 100000,
            detail=f"asked for {fake.PaymentIntent.calls[0]['amount'] if fake.PaymentIntent.calls else None} cents")

    s.section("Who is left alone")
    _cleanup()
    _booking("OptOut", today, opt_out=1)
    _booking("NoCard", today, card=False)
    _booking("Failed", today, failed_at=_harness.datetime_now())
    _booking("Later", today + timedelta(days=30))
    _booking("Cancelled", today, status="cancelled")
    fake = FakeStripe()
    out = _run(fake)
    s.check("nobody in that set is charged", out["charged"] == 0, detail=f"got {out}")
    s.check("and Stripe was not called at all", not fake.PaymentIntent.calls,
            detail=f"{len(fake.PaymentIntent.calls)} calls")

    s.section("A card that declines is tried once, and the guest is told")
    _cleanup()
    bid = _booking("Decline", today)
    fake = FakeStripe(fail_with="Your card was declined.")
    out = _run(fake)
    conn = db()
    row = conn.execute("SELECT autocharge_failed_at FROM workshop_bookings WHERE id = ?",
                       (bid,)).fetchone()
    owed = m.workshop_balance_due(conn, bid)[0]
    mail = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE subject LIKE '%did not go through%'").fetchone()["c"]
    conn.close()
    s.check("it is counted as a failure", out["failed"] == 1, detail=f"got {out}")
    s.check("the failure is recorded on the booking", bool(row["autocharge_failed_at"]))
    s.check("no payment is invented for a charge that never happened",
            owed == 1400.0, detail=f"balance is {owed}")
    s.check("the guest is emailed a way to pay it themselves", mail >= 1,
            detail=f"{mail} emails held")

    s.section("And not tried again the next day")
    fake = FakeStripe()          # a working card this time
    out = _run(fake)
    s.check("the failed booking is skipped on the next run",
            out["charged"] == 0 and not fake.PaymentIntent.calls,
            detail=f"got {out}, {len(fake.PaymentIntent.calls)} calls")

    s.section("The guest can say they would rather pay themselves")
    _cleanup()
    bid = _booking("Choice", today)
    pub = m.app.test_client()
    pub.post(f"/workshops/manage/tok{TAG}Choice",
             data={"action": "autocharge", "autocharge_opt_out": "on"}, follow_redirects=True)
    conn = db()
    opted = conn.execute("SELECT autocharge_opt_out FROM workshop_bookings WHERE id = ?",
                         (bid,)).fetchone()["autocharge_opt_out"]
    conn.close()
    s.check("the opt-out is saved", opted == 1, detail=f"got {opted}")
    fake = FakeStripe()
    out = _run(fake)
    s.check("and they are not charged", out["charged"] == 0 and not fake.PaymentIntent.calls,
            detail=f"got {out}")
    # And back again — a choice that cannot be undone is a trap.
    pub.post(f"/workshops/manage/tok{TAG}Choice", data={"action": "autocharge"},
             follow_redirects=True)
    conn = db()
    opted = conn.execute("SELECT autocharge_opt_out FROM workshop_bookings WHERE id = ?",
                         (bid,)).fetchone()["autocharge_opt_out"]
    conn.close()
    s.check("and they can change their mind back", opted == 0, detail=f"got {opted}")

    s.section("The guest can still find the opt-out")
    # The control and its notice live in workshop_manage.html, which keeps
    # arriving in handover zips from before this feature existed. Charging a
    # card automatically with no visible way to decline is the failure worth
    # catching loudly, so this names it rather than leaving it to a route test.
    import os as _os
    tpl = open(_os.path.join(_harness.ROOT, "templates", "workshop_manage.html"),
               encoding="utf-8").read()
    s.check("the opt-out checkbox is on the page",
            'name="autocharge_opt_out"' in tpl,
            detail="the opt-out has been deleted from templates/workshop_manage.html "
                   "— the balance is charged automatically with no way for the "
                   "guest to decline")
    s.check("and the notice saying what will happen is with it",
            "balance_due_date" in tpl and "autocharge_enabled" in tpl,
            detail="the guest is not told their card will be charged")

    _cleanup()
    return s
