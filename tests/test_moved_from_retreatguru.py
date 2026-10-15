"""A guest moved from RetreatGuru: booked by hand, deposit paid there, the rest owed.

October 2026. The Autumn Atelier (23 to 27 October) had eight people booked in
RetreatGuru, each with 30% paid, and the session was a week away. Brought into
the app by hand -- registered, their RetreatGuru price set with a discount line,
their deposit put on the ledger as a payment -- every one of them fell through
the floor:

  - the statement and the guest's own page were right: €1,638 still to pay;
  - but Balances to collect, the owner home, the morning notice and the task
    list looked for money due by reading balance_due_date. A registration made
    inside a month of its session has none -- the whole price is its deposit,
    due as it is booked -- so €1,638 was owed on the ledger and chased by
    nothing;
  - the balance reminder chose by balance_amount > 0, which for the same
    registration is nought, and never wrote;
  - and the registrations page offered "Quick: mark deposit (€2,600.00) paid",
    which recorded the whole price on top of the €702: the guest shown as owing
    nothing, most of the price wiped with one press.

This walks that path through the owner's own routes and the real letters, and
each promise has a negative control. Mail is stood in for; nothing is sent.
"""
import re
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRG"


class Recorder:
    """send_email, stood in for: every letter kept, every one 'delivered'."""
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *args, **kw):
        self.calls.append({"to": to_address, "subject": subject, "body": body,
                           "template": kw.get("template_key")})
        return True

    def to(self, address, template=None):
        return [c for c in self.calls if c["to"] == address
                and (template is None or c["template"] == template)]


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    like = f"{TAG.lower()}%"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute(f"DELETE FROM workshop_messages WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute(f"DELETE FROM workshop_booking_guests WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM notifications WHERE kind = 'balance_due'")
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.commit()
    conn.close()


def _session(days_away, label):
    """An atelier at €2,600 with a 30% deposit, `days_away` from today."""
    conn = db()
    now = _harness.datetime_now()
    title = f"{TAG} Autumn Atelier"
    if not conn.execute("SELECT 1 FROM workshops WHERE title = ?", (title,)).fetchone():
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, default_capacity,
               active, sort_order, created_at, deposit_percent)
               VALUES (?, '', 2600, 10, 1, 93, ?, 30)""", (title, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (title,)).fetchone()["id"]
    start = house_today() + timedelta(days=days_away)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} {label}", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {label}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return sid


def _register(oc, sid, who):
    email = f"{TAG.lower()}.{who}@example.invalid"
    r = oc.post(f"/admin/workshops/sessions/{sid}/add", data={
        "guest_name": f"{TAG} Guest {who}", "guest_email": email, "party_size": "1",
        "occupancy_type": "double", "notes": "Moved from RetreatGuru"})
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE guest_email = ?", (email,)).fetchone()
    conn.close()
    return r, row


def _ledger(bid):
    conn = db()
    got = m.workshop_balance_due(conn, bid)
    conn.close()
    return got


def _mine(items):
    return {r["email"]: r for r in items if (r["email"] or "").startswith(TAG.lower())}


def _card(page, reference):
    """One registration's card on the registrations page, and nothing of the
    cards around it."""
    return next((card for card in page.split('<div class="expense-card">')[1:]
                 if reference in card), "")


def run():
    s = Suite("a guest moved from RetreatGuru")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    mail = Recorder()
    real_send = m.send_email
    m.send_email = mail
    try:
        _run(s, oc, mail)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, mail):
    today = house_today()
    sid = _session(8, "next week")

    s.section("Registered by hand a week out, their RetreatGuru price and deposit put on the ledger")
    r, a = _register(oc, sid, "a")
    s.check("registered and confirmed", a is not None and a["status"] == "confirmed", r)
    s.check("inside a month, the whole price is the deposit and there is no balance due date",
            a and a["deposit_amount"] == 2600 and not a["balance_due_date"],
            detail=str(dict(a)) if a else "")
    oc.post(f"/admin/workshops/registrations/{a['id']}/add-transaction", data={
        "kind": "discount", "description": "Early bird price in RetreatGuru", "amount": "260"})
    oc.post(f"/admin/workshops/registrations/{a['id']}/add-transaction", data={
        "kind": "payment", "description": "Deposit paid in RetreatGuru", "amount": "702",
        "method": "bank_transfer"})
    s.check("the ledger: €2,340 charged, €702 paid, €1,638 owed",
            _ledger(a["id"]) == (1638.0, 2340.0, 702.0), detail=str(_ledger(a["id"])))

    s.section("Their statement")
    mail.calls.clear()
    oc.post(f"/admin/workshops/registrations/{a['id']}/statement")
    letters = mail.to(a["guest_email"], "workshop_statement")
    body = letters[0]["body"] if letters else ""
    s.check("the letter says what is left: €1,638.00", "Still to pay: €1,638.00" in body,
            detail=body[:200])
    s.check("and the statement under it shows the discount and the deposit",
            "Early bird price in RetreatGuru" in body and "702.00" in body and "260.00" in body)
    page = oc.get(f"/workshops/manage/{a['manage_token']}/statement").get_data(as_text=True)
    s.check("their own statement page agrees", "1,638.00" in page)

    s.section("It is chased: Balances to collect, the owner home, the notice")
    conn = db()
    listed = _mine(m.workshop_balances_due(conn, today=today))
    conn.close()
    item = listed.get(a["guest_email"])
    s.check("Balances to collect lists it, at what the ledger says",
            item is not None and item["owed"] == 1638.0, detail=str(item))
    s.check("due since the day it was booked", item and item["due_date"] == m.house_date_iso(a["created_at"]),
            detail=str(item and item["due_date"]))
    s.check("so it is due now, not coming", item and item["state"] != "coming",
            detail=str(item and item["state"]))
    page = oc.get("/management/balances").get_data(as_text=True)
    s.check("the page itself shows it", f"{TAG} Guest a" in page and "1,638.00" in page)
    real_due_on = m.workshop_balance_due_on
    m.workshop_balance_due_on = lambda row: row["balance_due_date"] or "9999-12-31"
    try:
        conn = db()
        old_way = _mine(m.workshop_balances_due(conn, today=today))
        conn.close()
    finally:
        m.workshop_balance_due_on = real_due_on
    s.check("(negative control: read by balance_due_date alone, it is not listed at all)",
            a["guest_email"] not in old_way)

    s.section("The balance reminder")
    mail.calls.clear()
    conn = db()
    with m.app.test_request_context():     # the job runner gives it one, for its links
        said = m.run_workshop_balance_reminder_job(conn, 7)
    conn.close()
    reminders = mail.to(a["guest_email"], "workshop_balance_reminder")
    body = reminders[0]["body"] if reminders else ""
    s.check("it reminds them", len(reminders) == 1, detail=said)
    s.check("of what is left, €1,638.00 -- not the €0.00 stamped at booking", "1,638.00" in body,
            detail=body[:220])
    s.check("in euros: the amount carries its sign, since the wording of 8 October took it "
            "out of the letter", "balance of €1,638.00" in body, detail=body[:220])
    s.check("due today, in words, not an ISO date or a day already gone",
            "due by today" in body and not re.search(r"\d{4}-\d{2}-\d{2}", body), detail=body[:220])
    mail.calls.clear()
    conn = db()
    with m.app.test_request_context():
        m.run_workshop_balance_reminder_job(conn, 7)
    conn.close()
    s.check("and once", not mail.to(a["guest_email"], "workshop_balance_reminder"))

    s.section("The quick buttons never wipe a debt")
    page = oc.get("/admin/workshops/registrations").get_data(as_text=True)
    block = _card(page, a["reference_code"])
    s.check("it offers the rest, at what is left", "mark the rest (€1,638.00) paid" in block,
            detail=re.findall(r"Quick:[^<]+", block))
    s.check("and not the whole price as a deposit", "mark deposit (€2,600.00) paid" not in block,
            detail=re.findall(r"Quick:[^<]+", block))
    oc.post(f"/admin/workshops/registrations/{a['id']}/mark-deposit-paid", data={"method": "other"})
    owed, charged, paid = _ledger(a["id"])
    s.check("pressed anyway, Mark deposit paid records what is left, not the price again",
            paid == 2340.0 and owed == 0.0, detail=f"owed {owed}, paid {paid}")

    r, b = _register(oc, sid, "b")
    mail.calls.clear()
    oc.post(f"/admin/workshops/registrations/{b['id']}/mark-deposit-paid", data={"method": "other"})
    owed, charged, paid = _ledger(b["id"])
    s.check("with nothing paid yet, it records the deposit set at booking",
            paid == 2600.0 and owed == 0.0, detail=f"owed {owed}, paid {paid}")
    receipt = mail.to(b["guest_email"], "workshop_deposit_receipt")
    s.check("and the receipt names what was received and what is left",
            receipt and "€2,600.00" in receipt[0]["body"] and "paid in full" in receipt[0]["body"],
            detail=receipt[0]["body"][:200] if receipt else "no receipt")

    s.section("A session further out: the deposit is 30%, and dates are written in words")
    far = _session(60, "far")
    mail.calls.clear()
    r, c = _register(oc, far, "c")
    s.check("a 30% deposit and a balance due date", c and c["deposit_amount"] == 780
            and c["balance_due_date"], detail=str(dict(c)) if c else "")
    oc.post(f"/admin/workshops/registrations/{c['id']}/add-transaction", data={
        "kind": "payment", "description": "Part of the deposit", "amount": "100", "method": "cash"})
    page = oc.get("/admin/workshops/registrations").get_data(as_text=True)
    block = _card(page, c["reference_code"])
    s.check("part of a deposit paid: it offers what is left of the deposit, and the rest",
            "mark deposit (€680.00) paid" in block and "mark the rest (€2,500.00) paid" in block,
            detail=re.findall(r"Quick:[^<]+", block))
    oc.post(f"/admin/workshops/registrations/{c['id']}/mark-deposit-paid", data={"method": "other"})
    owed, charged, paid = _ledger(c["id"])
    s.check("and pressed, records €680 of it", paid == 780.0 and owed == 1820.0,
            detail=f"owed {owed}, paid {paid}")
    receipt = mail.to(c["guest_email"], "workshop_deposit_receipt")
    body = receipt[0]["body"] if receipt else ""
    s.check("the receipt says €680.00 received and €1,820.00 still to pay",
            "deposit of €680.00" in body and "Still to pay: €1,820.00" in body, detail=body[:240])
    s.check("no letter carries a date the database's way",
            not any(re.search(r"\b\d{4}-\d{2}-\d{2}\b", l["body"]) for l in mail.calls),
            detail=[re.findall(r"\b\d{4}-\d{2}-\d{2}\b", l["body"]) for l in mail.calls])


if __name__ == "__main__":
    print(run().report())
