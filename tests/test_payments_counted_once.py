"""Every payment recorded once -- and every one of them recorded.

Two faults of one shape, found by reading the code rather than by anything
going red:

  - A workshop balance paid online in two parts was recorded once. The first
    part stamped the balance paid; the second found the stamp and wrote
    nothing, though Stripe had taken it. The ledger then still showed that
    money as owed -- and the ledger is what the charge on the due date reads,
    so the guest could have been charged it a second time.
  - An event payment was credited only on the contact's return page. The
    webhook had no branch for it, so somebody who paid towards a wedding and
    closed the tab had paid, was shown as owing, and was chased for it.

And the card a deposit was meant to keep for the balance: a Checkout given
only an email creates no Stripe Customer, so nothing was kept, and the balance
could never have been taken from it.

Stripe is stood in for throughout. Nothing here reaches the network.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZPC"


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_payments WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (f"{TAG.lower()}%",))
    conn.commit()
    conn.close()


def _registration(ref, *, total=2000.0, deposit_paid=True, card=False, opt_out=0,
                  due_in_days=60):
    """A confirmed registration with its deposit (30%) on the ledger."""
    conn = db()
    now = _harness.datetime_now()
    if not conn.execute("SELECT 1 FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone():
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, default_capacity,
               active, sort_order, created_at, deposit_percent)
               VALUES (?, '', ?, 20, 1, 95, ?, 30)""", (f"{TAG} Atelier", total, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    if not conn.execute("SELECT 1 FROM workshop_sessions WHERE notes = ?", (f"{TAG} sitting",)).fetchone():
        start = house_today() + timedelta(days=due_in_days + 30)
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
               VALUES (?, ?, ?, 20, ?, ?)""",
            (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} sitting", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} sitting",)).fetchone()["id"]
    due_date = (house_today() + timedelta(days=due_in_days)).isoformat()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           deposit_paid_at, balance_due_date, stripe_customer_id, stripe_payment_method_id,
           autocharge_opt_out)
           VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} {ref}", f"{TAG.lower()}{ref.lower()}@example.invalid", f"{TAG}{ref}",
         f"tok{TAG}{ref}", now, total, round(total * 0.3, 2), round(total * 0.7, 2),
         now if deposit_paid else None, due_date,
         "cus_test" if card else None, "pm_test" if card else None, opt_out))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if deposit_paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", round(total * 0.3, 2),
                                   method="stripe")
        conn.commit()
    conn.close()
    return bid


def _checkout(sid, bid, euros, kind="workshop_balance"):
    """A paid Checkout Session, shaped the way Stripe reports one."""
    return {"id": sid, "payment_status": "paid", "amount_total": int(round(euros * 100)),
            "metadata": {"workshop_booking_id": str(bid), "kind": kind}}


def _ledger(bid):
    conn = db()
    due, charged, paid = m.workshop_balance_due(conn, bid)
    lines = conn.execute("SELECT * FROM workshop_transactions WHERE workshop_booking_id = ? "
                         "ORDER BY id", (bid,)).fetchall()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return due, paid, lines, row


def _credit(session):
    conn = db()
    try:
        m.mark_workshop_payment_paid(conn, session)
    finally:
        conn.close()


def _event(ref, price=3000.0):
    conn = db()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, contact_phone, preferred_date, guest_count,
           message, status, quoted_price, amount_paid, created_at)
           VALUES (?, ?, 'wedding', ?, ?, '', ?, 60, 'ZZ test', 'confirmed', ?, 0, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}ev{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}ev{ref.lower()}@example.invalid",
         (house_today() + timedelta(days=200)).isoformat(), price,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _event_paid(event_id):
    conn = db()
    bill = m.event_bill(conn, event_id)
    lines = conn.execute("SELECT * FROM event_payments WHERE event_id = ?", (event_id,)).fetchall()
    conn.close()
    return bill["paid"], lines


def _webhook(client, session):
    """Drive the webhook with the signature check stood in for."""
    original = m.stripe.Webhook.construct_event
    original_secret = m.STRIPE_WEBHOOK_SECRET
    m.STRIPE_WEBHOOK_SECRET = "whsec_test_only_never_real"
    m.stripe.Webhook.construct_event = (
        lambda payload, sig, secret: {"type": "checkout.session.completed",
                                      "data": {"object": session}})
    try:
        return client.post("/webhooks/stripe", data=b"{}",
                           headers={"Stripe-Signature": "t=1,v1=stub"})
    finally:
        m.stripe.Webhook.construct_event = original
        m.STRIPE_WEBHOOK_SECRET = original_secret


class _FakeCheckout:
    """Records what a checkout was asked for, and hands back a URL."""
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return type("S", (), {"url": "https://checkout.stripe.test/c/pay"})()


class _FakeCustomers:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise Exception("Stripe could not be reached")
        return {"id": f"cus_made_{len(self.calls)}"}


class _FakeStripe:
    def __init__(self, customer_fails=False):
        self.checkout = type("C", (), {})()
        self.checkout.Session = _FakeCheckout()
        self.Customer = _FakeCustomers(customer_fails)


def _deposit_checkout(bid, fake):
    real_stripe, real_enabled = m.stripe, m.stripe_enabled
    m.stripe, m.stripe_enabled = fake, (lambda: True)
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.start_workshop_stripe_payment(conn, bid, "deposit")
    finally:
        conn.close()
        m.stripe, m.stripe_enabled = real_stripe, real_enabled


def run():
    s = Suite("Payments counted once")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    _cleanup()

    s.section("Two part payments towards a balance are both recorded")
    bid = _registration("Parts")
    _credit(_checkout(f"cs_{TAG}_p1", bid, 500))
    _credit(_checkout(f"cs_{TAG}_p2", bid, 500))
    due, paid, lines, row = _ledger(bid)
    s.check("both payments are on the ledger", paid == 1600.0 and due == 400.0,
            detail=f"paid {paid}, owed {due} -- the second part was taken by Stripe "
                   "and never written")
    s.check("each line names the checkout it came through",
            {l["stripe_ref"] for l in lines if l["stripe_ref"]} == {f"cs_{TAG}_p1", f"cs_{TAG}_p2"},
            detail=f"{[l['stripe_ref'] for l in lines]}")
    s.check("and with money still owed the balance is not stamped paid",
            not row["balance_paid_at"],
            detail="the first part payment stamped the whole balance paid")

    s.section("The same payment reported again is not credited again")
    # Stripe retries its webhook, and the guest's return page reports the
    # same checkout besides.
    _credit(_checkout(f"cs_{TAG}_p2", bid, 500))
    _credit(_checkout(f"cs_{TAG}_p2", bid, 500))
    due, paid, lines, _row = _ledger(bid)
    s.check("still 1600 paid, not 2100", paid == 1600.0, detail=f"paid {paid}")
    s.check("and three lines, not five", len(lines) == 3, detail=f"{len(lines)} lines")

    s.section("The last of it settles the balance, and the stamp follows")
    _credit(_checkout(f"cs_{TAG}_p3", bid, 400))
    due, paid, _lines, row = _ledger(bid)
    s.check("nothing is owed", due == 0.0, detail=f"owed {due}")
    s.check("and the balance is stamped paid", bool(row["balance_paid_at"]))

    s.section("A charge added after that is money owed again")
    # A breakage, a supplement agreed late. A stamp left in place would hide
    # it from the reminder and from the list of balances to collect.
    conn = db()
    m.add_workshop_transaction(conn, bid, "charge", "Broken glass", 40.0)
    conn.commit()
    conn.close()
    due, _paid, _lines, row = _ledger(bid)
    s.check("40 is owed", due == 40.0, detail=f"owed {due}")
    s.check("and the paid stamp has come off", not row["balance_paid_at"])

    s.section("A checkout the old recording already credited is recognised")
    # Before this, the session was kept on the registration and the ledger line
    # carried no reference. A webhook retry for one of those must not be
    # credited a second time.
    _cleanup()
    bid = _registration("Legacy")
    conn = db()
    conn.execute("UPDATE workshop_bookings SET balance_stripe_session_id = ? WHERE id = ?",
                 (f"cs_{TAG}_old", bid))
    m.add_workshop_transaction(conn, bid, "payment", "Balance — Stripe", 700.0, method="stripe")
    conn.commit()
    conn.close()
    _credit(_checkout(f"cs_{TAG}_old", bid, 700))
    _due, paid, _lines, _row = _ledger(bid)
    s.check("it is not credited twice", paid == 1300.0, detail=f"paid {paid}")

    s.section("A deposit paid twice is recorded twice, and receipted once")
    # Two tabs, two checkouts, both paid. The money is real either way, and a
    # ledger that shows it is how it gets noticed and given back.
    _cleanup()
    bid = _registration("Twice", deposit_paid=False)
    with m.app.test_request_context("/"):
        _credit(_checkout(f"cs_{TAG}_d1", bid, 600, kind="workshop_deposit"))
        _credit(_checkout(f"cs_{TAG}_d2", bid, 600, kind="workshop_deposit"))
    due, paid, _lines, row = _ledger(bid)
    conn = db()
    receipts = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ? AND subject LIKE '%eposit%'",
        (f"{TAG.lower()}twice@example.invalid",)).fetchone()["c"]
    conn.close()
    s.check("both deposits are on the ledger", paid == 1200.0, detail=f"paid {paid}")
    s.check("the deposit is stamped with the first checkout",
            row["deposit_stripe_session_id"] == f"cs_{TAG}_d1",
            detail=f"got {row['deposit_stripe_session_id']}")
    s.check("and one receipt went out, not two", receipts == 1, detail=f"{receipts} receipts")

    s.section("'Mark balance paid' records what is left, not the figure from booking day")
    _cleanup()
    bid = _registration("Mark")
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Part payment", 500.0, method="stripe")
    conn.commit()
    conn.close()
    oc.post(f"/admin/workshops/registrations/{bid}/mark-balance-paid",
            data={"method": "bank_transfer"}, follow_redirects=True)
    due, paid, lines, row = _ledger(bid)
    s.check("900 is recorded, not the 1400 set when they booked",
            lines[-1]["amount"] == 900.0 and due == 0.0,
            detail=f"last line {lines[-1]['amount']}, owed {due}")
    s.check("and the balance is stamped paid", bool(row["balance_paid_at"]))
    r = oc.post(f"/admin/workshops/registrations/{bid}/mark-balance-paid",
                data={"method": "bank_transfer"}, follow_redirects=True)
    _due, paid_after, lines_after, _row = _ledger(bid)
    s.check("pressing it again records nothing", len(lines_after) == len(lines)
            and paid_after == paid, detail=f"{len(lines_after)} lines, paid {paid_after}")
    s.check("and says why", any("Nothing is owed" in f for f in flashes(r)),
            detail=f"{flashes(r)}")

    s.section("What the old recording left behind is held, not charged")
    # A registration stamped paid while its ledger still shows money owed is a
    # guest who still owes it -- or one who paid online and was never
    # credited. Only Stripe knows which, so nobody charges it on a guess.
    _cleanup()
    bid = _registration("Held", card=True)
    conn = db()
    conn.execute("UPDATE workshop_bookings SET balance_paid_at = ? WHERE id = ?",
                 (_harness.datetime_now(), bid))
    conn.commit()
    held = m.hold_legacy_balance_stamps(conn)
    conn.close()
    _due, _paid, _lines, row = _ledger(bid)
    s.check("it is found", held >= 1, detail=f"held {held}")
    s.check("the stamp comes off, so the debt shows again", not row["balance_paid_at"])
    s.check("and it is held, naming the reference to look for in Stripe",
            f"{TAG}Held" in (row["collect_hold"] or ""), detail=f"{row['collect_hold']!r}")
    conn = db()
    again = m.hold_legacy_balance_stamps(conn)
    conn.close()
    s.check("running it again finds nothing more", again == 0, detail=f"held {again}")

    s.section("An event payment reaches the books through the webhook alone")
    # The contact paid and closed the tab: no return page, only Stripe's own
    # report of it.
    ev = _event("Hook")
    session = {"id": f"cs_{TAG}_ev1", "payment_status": "paid", "amount_total": 90000,
               "metadata": {"kind": "event_payment", "event_id": str(ev["id"])}}
    r = _webhook(anon, session)
    paid, lines = _event_paid(ev["id"])
    s.check("the webhook accepts it", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and 900 is credited to the event", abs(paid - 900.0) < 0.01,
            detail=f"credited {paid} -- the contact would be chased for money they had paid")
    _webhook(anon, session)

    s.section("The webhook and the return page between them credit it once")
    real_session, real_enabled = m.stripe.checkout.Session, m.stripe_enabled
    m.stripe.checkout.Session = type("R", (), {"retrieve": staticmethod(lambda sid: session)})
    m.stripe_enabled = lambda: True
    try:
        anon.get(f"/events/paid/{ev['manage_token']}?session_id={session['id']}",
                 follow_redirects=True)
    finally:
        m.stripe.checkout.Session, m.stripe_enabled = real_session, real_enabled
    paid, lines = _event_paid(ev["id"])
    s.check("still 900, after a retried webhook and the page", abs(paid - 900.0) < 0.01,
            detail=f"credited {paid}")
    s.check("and one payment line", len(lines) == 1, detail=f"{len(lines)} lines")

    s.section("A checkout paid for something else is not credited to this event")
    # The page took any paid session id it was handed and credited it to
    # whichever event's link it was opened from -- a dinner deposit could be
    # replayed onto a wedding.
    other = _event("Other")
    dinner = {"id": f"cs_{TAG}_dinner", "payment_status": "paid", "amount_total": 5000,
              "metadata": {"kind": "restaurant"}}
    m.stripe.checkout.Session = type("R", (), {"retrieve": staticmethod(lambda sid: dinner)})
    m.stripe_enabled = lambda: True
    try:
        anon.get(f"/events/paid/{other['manage_token']}?session_id={dinner['id']}",
                 follow_redirects=True)
    finally:
        m.stripe.checkout.Session, m.stripe_enabled = real_session, real_enabled
    paid, _lines = _event_paid(other["id"])
    s.check("nothing is credited", paid == 0.0, detail=f"credited {paid}")

    s.section("A deposit keeps its card on a Customer")
    _cleanup()
    bid = _registration("Card", deposit_paid=False)
    fake = _FakeStripe()
    url = _deposit_checkout(bid, fake)
    asked = fake.checkout.Session.calls[0] if fake.checkout.Session.calls else {}
    conn = db()
    kept = conn.execute("SELECT stripe_customer_id FROM workshop_bookings WHERE id = ?",
                        (bid,)).fetchone()["stripe_customer_id"]
    conn.close()
    s.check("the guest is sent to a checkout", bool(url))
    s.check("a Customer is made for the card to be kept on",
            len(fake.Customer.calls) == 1 and asked.get("customer") == "cus_made_1",
            detail=f"customer {asked.get('customer')!r}: without one Stripe keeps the card "
                   "on nobody, and the balance can never be taken")
    s.check("the checkout is told to keep the card",
            asked.get("payment_intent_data", {}).get("setup_future_usage") == "off_session")
    s.check("and the Customer is remembered on the registration", kept == "cus_made_1",
            detail=f"got {kept!r}")
    fake = _FakeStripe()
    _deposit_checkout(bid, fake)
    s.check("coming back to pay makes no second Customer", not fake.Customer.calls,
            detail=f"{len(fake.Customer.calls)} made")

    s.section("A guest who opted out gets a one-off payment and nothing kept")
    _cleanup()
    bid = _registration("NoKeep", deposit_paid=False, opt_out=1)
    fake = _FakeStripe()
    _deposit_checkout(bid, fake)
    asked = fake.checkout.Session.calls[0] if fake.checkout.Session.calls else {}
    s.check("no Customer is made", not fake.Customer.calls)
    s.check("and the card is not kept", "payment_intent_data" not in asked
            and asked.get("customer_email"), detail=f"{sorted(asked)}")

    s.section("A Customer that cannot be made costs the convenience, not the deposit")
    _cleanup()
    bid = _registration("NoCus", deposit_paid=False)
    fake = _FakeStripe(customer_fails=True)
    url = _deposit_checkout(bid, fake)
    asked = fake.checkout.Session.calls[0] if fake.checkout.Session.calls else {}
    s.check("the deposit can still be paid", bool(url))
    s.check("as a one-off, by email", asked.get("customer_email") and "customer" not in asked
            and "payment_intent_data" not in asked, detail=f"{sorted(asked)}")

    s.section("The guest's page says what will happen from what is on file")
    _cleanup()
    cases = {
        "NoCardPage": dict(card=False),
        "CardPage": dict(card=True),
        "OptPage": dict(card=True, opt_out=1),
        "NoDepPage": dict(deposit_paid=False),
    }
    pages = {}
    for ref, kw in cases.items():
        _registration(ref, **kw)
        pages[ref] = anon.get(f"/workshops/manage/tok{TAG}{ref}").get_data(as_text=True)
    s.check("with no card kept, it does not say the card will be charged",
            "taken from the card" not in pages["NoCardPage"]
            and "Please pay it by" in pages["NoCardPage"],
            detail="a guest told the card would be charged does not pay")
    s.check("with a card kept, it says the rest is taken from it",
            "will be taken from the card you paid the deposit with" in pages["CardPage"])
    s.check("having opted out, it says it will not be",
            "We will not take it from your card" in pages["OptPage"])
    s.check("before the deposit, it says the card will be kept for it",
            "The card you pay the deposit with is kept" in pages["NoDepPage"])
    s.check("and the choice to pay it yourself is there whenever there is a card to take it from",
            all('name="autocharge_opt_out"' in pages[r] for r in ("CardPage", "OptPage", "NoDepPage")))

    s.section("Run now works for all three balance reminders")
    # Rooms and events were never taught to Run now: pressing it looked them
    # up in a registry they were not in and recorded a KeyError as the job
    # failing.
    for job in ("room_balance_reminder", "event_balance_reminder", "workshop_balance_reminder"):
        oc.post(f"/admin/automation/run/{job}", follow_redirects=True)
        conn = db()
        run_row = conn.execute("SELECT last_status, last_message FROM automation_runs "
                               "WHERE job_name = ?", (job,)).fetchone()
        conn.close()
        s.check(f"{job} runs", run_row is not None and run_row["last_status"] == "ok",
                detail=f"{dict(run_row) if run_row else 'no run recorded'}")

    s.section("The workshop reminder asks for what is left")
    _cleanup()
    bid = _registration("Remind", due_in_days=3)
    conn = db()
    m.add_workshop_transaction(conn, bid, "payment", "Part payment", 500.0, method="stripe")
    conn.commit()
    with m.app.test_request_context("/"):
        m.run_workshop_balance_reminder_job(conn, 7)
    body = conn.execute(
        "SELECT body FROM email_outbox WHERE to_address = ? ORDER BY id DESC LIMIT 1",
        (f"{TAG.lower()}remind@example.invalid",)).fetchone()
    conn.close()
    s.check("the reminder quotes the 900 left, not the 1400 set on booking day",
            body is not None and "900.00" in body["body"] and "1400.00" not in body["body"],
            detail=f"{(body['body'][:160] if body else 'no reminder sent')!r}")
    # The timer only printed its result, so the Job status table showed the
    # last time somebody pressed Run now and nothing about the daily runs.
    import inspect
    tick = inspect.getsource(m.automation_tick)
    s.check("and its daily run is recorded like the other two",
            'record_job_run(conn, "workshop_balance_reminder", True' in tick)

    _cleanup()
    return s
