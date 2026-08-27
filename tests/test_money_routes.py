"""The four money routes that had no test at all.

Found by the coverage report, and they are the wrong four to have none: bank
details, a card link, and the page that turns an event enquiry into a price.

Two of them are gated off in the harness the same way they are on the live
site — the vault has no key, Stripe has no key — so an untested route here was
not merely unexercised, it was unexercised in the state where it 404s or
refuses. That is the easy half to get right. The half worth pinning:

  - the vault must FAIL CLOSED. With no key the whole feature 404s, and
    fernet_encrypt_json raises rather than quietly storing an IBAN as plaintext.
    A regression here would look identical on screen.

  - blank sensitive fields on the bank-details edit form mean "leave
    unchanged", not "erase". Getting that backwards silently wipes the
    account number of whoever edits a label, and nothing on the page would
    say so.

  - pos_pay_link must decide it cannot charge BEFORE it reaches Stripe. This
    suite makes stripe.checkout.Session.create raise if it is ever called, so
    a gate that moves below the API call fails here rather than in production.

  - re-confirming an event enquiry must not re-stamp the decision or send the
    guest a second confirmation. Same shape as the bug where re-sharing a
    performance review pinged the employee again.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZMONEY"
IBAN = "FR7630006000011234567890189"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bank_details WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _inquiry(status="new", price=2500.0):
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, guest_count, status, quoted_price, created_at)
           VALUES (?, ?, 'wedding', ?, 'money@example.invalid', 40, ?, ?, ?)""",
        (TAG + "-EV", TAG.lower() + "tok", TAG + " Couple", status, price, now))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (TAG + "-EV",)).fetchone()
    conn.close()
    return row


def _row(table, where, param):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE {where}", (param,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Money routes")
    _cleanup()
    oc, ec, owner, emp = clients()

    # ------------------------------------------------------- bank details
    s.section("With no key, the vault is not there at all")
    # This is the live state. It must 404 rather than offer a form that stores
    # an IBAN in the clear.
    was_key = m.VAULT_ENCRYPTION_KEY
    m.VAULT_ENCRYPTION_KEY = ""
    s.check("adding bank details 404s",
            oc.post("/management/bank-details/new",
                    data={"label": TAG + " acct", "iban": IBAN}).status_code == 404)
    s.check("and nothing was written",
            _row("bank_details", "label LIKE ?", TAG + "%") is None,
            detail="a route that 404s still wrote a row")
    raised = False
    try:
        m.fernet_encrypt_json({"iban": IBAN})
    except Exception:
        raised = True
    s.check("and encrypting refuses rather than storing plaintext", raised,
            detail="with no key configured this returned a value — check it is "
                   "not the IBAN in the clear")

    s.section("With a key, what is stored is not readable")
    from cryptography.fernet import Fernet
    m.VAULT_ENCRYPTION_KEY = Fernet.generate_key().decode()
    try:
        oc.post("/management/bank-details/new", data={
            "label": TAG + " operating", "bank_name": "Crédit Agricole",
            "account_holder": "Château de Gudanes", "currency": "EUR",
            "account_number": "12345678", "iban": IBAN, "swift_bic": "AGRIFRPP",
        }, follow_redirects=True)
        entry = _row("bank_details", "label = ?", TAG + " operating")
        s.check("the entry is saved", entry is not None)
        if entry:
            stored = entry["sensitive_encrypted"] or ""
            s.check("the IBAN is not in the stored value", IBAN not in stored,
                    detail="the account number is sitting in the database in "
                           "the clear")
            s.check("and it decrypts back to what was typed",
                    m.fernet_decrypt_json(stored).get("iban") == IBAN,
                    detail="stored, but not recoverable — the owner has lost it")
            s.check("the label is readable, because it is not a secret",
                    entry["label"] == TAG + " operating")
            s.check("and the write is on the audit trail",
                    _row("audit_log", "action = ?", "bank_details_created") is not None)

        s.section("Blank sensitive fields mean leave alone, not erase")
        # The documented convention, and the one that costs real money to get
        # wrong: somebody fixes a typo in the label and the IBAN vanishes.
        oc.post(f"/management/bank-details/{entry['id']}/edit", data={
            "label": TAG + " operating (main)", "bank_name": "Crédit Agricole",
            "account_number": "", "iban": "", "swift_bic": "",
        }, follow_redirects=True)
        after = _row("bank_details", "id = ?", entry["id"])
        s.check("the label changed", after["label"] == TAG + " operating (main)")
        s.check("and the IBAN is still there",
                m.fernet_decrypt_json(after["sensitive_encrypted"]).get("iban") == IBAN,
                detail="editing the label wiped the account details")

        s.section("But a new value does replace it")
        oc.post(f"/management/bank-details/{entry['id']}/edit", data={
            "label": TAG + " operating (main)", "iban": "FR9999999999999999999999999",
        }, follow_redirects=True)
        after = _row("bank_details", "id = ?", entry["id"])
        s.check("the new IBAN is stored",
                m.fernet_decrypt_json(after["sensitive_encrypted"]).get("iban")
                == "FR9999999999999999999999999")

        s.section("A blank label is refused")
        oc.post(f"/management/bank-details/{entry['id']}/edit",
                data={"label": ""}, follow_redirects=True)
        s.check("the label it had is kept",
                _row("bank_details", "id = ?", entry["id"])["label"]
                == TAG + " operating (main)")

        s.section("Guards")
        s.check("an employee cannot add bank details",
                ec.post("/management/bank-details/new",
                        data={"label": "x"}).status_code in (302, 403, 404))
        s.check("nor edit them",
                ec.post(f"/management/bank-details/{entry['id']}/edit",
                        data={"label": "y"}).status_code in (302, 403, 404))
        s.check("and the entry is untouched",
                _row("bank_details", "id = ?", entry["id"])["label"]
                == TAG + " operating (main)")
    finally:
        m.VAULT_ENCRYPTION_KEY = was_key

    # ---------------------------------------------------------- pay link
    s.section("The card link decides it cannot charge before calling Stripe")
    # If this suite ever reaches Stripe it is a bug in the app, not the test:
    # a gate that moves below the API call would create live objects from a
    # test run.
    calls = []

    class _Boom:
        @staticmethod
        def create(*a, **kw):
            calls.append(kw)
            raise AssertionError("pos_pay_link reached Stripe")

    real_session = m.stripe.checkout.Session
    m.stripe.checkout.Session = _Boom
    try:
        conn = db()
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, opened_at, service_date)
               VALUES (?, 2, 'open', ?, ?)""",
            (TAG + " T1", datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).date().isoformat()))
        conn.commit()
        order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                             (TAG + " T1",)).fetchone()
        conn.close()

        r = oc.post(f"/pos/{order['id']}/pay-link", follow_redirects=True)
        s.check("an empty tab is refused, not charged", r.status_code < 500,
                detail=f"HTTP {r.status_code}")
        s.check("and it says why",
                any(w in " ".join(flashes(r)).lower()
                    for w in ("nothing left", "isn't connected", "is not connected")),
                detail=f"{flashes(r)[:2]}")
        s.check("Stripe was never called", not calls,
                detail=f"{len(calls)} call(s) reached the payment provider "
                       "from a test run")

        s.section("And a tab already settled is refused too")
        # 'paid', not 'closed': pos_orders.status carries a CHECK constraint
        # allowing only open / paid / charged_to_room / void.
        conn = db()
        conn.execute("UPDATE pos_orders SET status = 'paid' WHERE id = ?", (order["id"],))
        conn.commit()
        conn.close()
        r = oc.post(f"/pos/{order['id']}/pay-link", follow_redirects=True)
        s.check("not a 500", r.status_code < 500, detail=f"HTTP {r.status_code}")
        s.check("it says the tab is closed",
                "closed" in " ".join(flashes(r)).lower(), detail=f"{flashes(r)[:2]}")
        s.check("and still no Stripe call", not calls, detail=f"{calls}")

        s.section("A tab that does not exist is not a stack trace")
        s.check("404 or a flash, not a 500",
                oc.post("/pos/999999/pay-link").status_code < 500)
    finally:
        m.stripe.checkout.Session = real_session

    # ----------------------------------------------------- event enquiry
    s.section("Pricing an event enquiry")
    sent = []

    def capture(to, subject, body, ics_content=None, ics_filename=None, keep=True):
        sent.append((to, subject))
        return True

    was_send = m.send_email
    m.send_email = capture
    try:
        ev = _inquiry(status="new", price=2500.0)
        oc.post(f"/admin/events/{ev['id']}/update",
                data={"status": "quoted", "quoted_price": "4800", "owner_note": "marquee"},
                follow_redirects=True)
        after = _row("event_inquiries", "id = ?", ev["id"])
        s.check("the status is set", after["status"] == "quoted")
        s.check("and the price with it", abs((after["quoted_price"] or 0) - 4800) < 0.01,
                detail=f"got {after['quoted_price']}")
        s.check("nothing is decided yet", after["decided_at"] is None)

        s.section("A blank price keeps the one that was quoted")
        # Not zero. A quote that silently becomes free is the expensive default.
        oc.post(f"/admin/events/{ev['id']}/update",
                data={"status": "contacted", "quoted_price": ""}, follow_redirects=True)
        after = _row("event_inquiries", "id = ?", ev["id"])
        s.check("the price is unchanged", abs((after["quoted_price"] or 0) - 4800) < 0.01,
                detail=f"got {after['quoted_price']} — a blank field wiped the quote")

        s.section("Confirming stamps the decision once")
        sent.clear()
        oc.post(f"/admin/events/{ev['id']}/update",
                data={"status": "confirmed", "quoted_price": "4800"}, follow_redirects=True)
        first = _row("event_inquiries", "id = ?", ev["id"])
        s.check("decided_at is stamped", first["decided_at"] is not None)
        s.check("and the couple are told", len(sent) == 1, detail=f"{sent}")

        s.section("Confirming again does neither")
        # The re-notify shape: a page reloaded or a button pressed twice must
        # not send a second confirmation or move the decision date.
        sent.clear()
        oc.post(f"/admin/events/{ev['id']}/update",
                data={"status": "confirmed", "quoted_price": "4800"}, follow_redirects=True)
        again = _row("event_inquiries", "id = ?", ev["id"])
        s.check("the decision date is the same",
                again["decided_at"] == first["decided_at"],
                detail="re-confirming moved the date the event was agreed")
        s.check("and no second email goes out", not sent, detail=f"{sent}")

        s.section("An invented status is refused")
        s.check("400, not stored",
                oc.post(f"/admin/events/{ev['id']}/update",
                        data={"status": "maybe"}).status_code == 400)
        s.check("and the status it had is kept",
                _row("event_inquiries", "id = ?", ev["id"])["status"] == "confirmed")

        s.section("Guards")
        s.check("an employee cannot price an event",
                ec.post(f"/admin/events/{ev['id']}/update",
                        data={"status": "declined"}).status_code in (302, 403))
        s.check("and it is still confirmed",
                _row("event_inquiries", "id = ?", ev["id"])["status"] == "confirmed")
    finally:
        m.send_email = was_send

    _cleanup()
    return s
