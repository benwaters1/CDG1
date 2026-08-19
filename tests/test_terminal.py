"""Taking a card on the reader, driven from the till.

Nothing here contacts Stripe. A fake stands in, and it deliberately raises on
attribute access the way a real StripeObject does — so if anyone reads a field
with `.status` or `.get()` instead of sval(), these tests fail rather than
production. That exact mistake once 500'd every paid booking in the app.

The check that matters is the last one: when Stripe says the money arrived, the
tab must close, with the payment's own reference on it. A payment that succeeds
while the tab stays open is money nobody can account for.
"""
from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZTERM"
READER = "tmr_faketestreader"


class FakeStripeObject(dict):
    """Behaves like a StripeObject: subscriptable, hostile to attributes."""
    def __getattr__(self, name):
        raise AttributeError(f"StripeObject has no attribute {name!r}")


class FakeStripe:
    """A Stripe stand-in whose payment outcome the test controls."""

    def __init__(self):
        self.status = "requires_payment_method"
        self.created = []
        self.sent_to_reader = []
        self.cancelled = []
        self.next_id = 0
        outer = self

        class PaymentIntent:
            @staticmethod
            def create(**kwargs):
                outer.next_id += 1
                pid = f"pi_fake_{outer.next_id}"
                outer.created.append({"id": pid, **kwargs})
                return FakeStripeObject({"id": pid, "status": "requires_payment_method"})

            @staticmethod
            def retrieve(pid):
                return FakeStripeObject({
                    "id": pid, "status": outer.status,
                    "last_payment_error": FakeStripeObject({"message": "Card declined."}),
                })

            @staticmethod
            def cancel(pid):
                outer.cancelled.append(pid)
                return FakeStripeObject({"id": pid, "status": "canceled"})

        class Reader:
            @staticmethod
            def process_payment_intent(reader_id, payment_intent=None):
                outer.sent_to_reader.append((reader_id, payment_intent))
                return FakeStripeObject({"id": reader_id, "action": "in_progress"})

            @staticmethod
            def cancel_action(reader_id):
                outer.cancelled.append(reader_id)
                return FakeStripeObject({"id": reader_id})

            @staticmethod
            def list(limit=50):
                return FakeStripeObject({"data": [FakeStripeObject({
                    "id": READER, "label": "Restaurant", "device_type": "stripe_s700",
                    "status": "online", "serial_number": "STR123456"})]})

        # A namespace, not a nested class: a class body cannot see names from the
        # enclosing function scope, so `Reader = Reader` inside one fails.
        import types
        self.PaymentIntent = PaymentIntent
        self.terminal = types.SimpleNamespace(Reader=Reader)


def _open_tab(label, price=18.50, quantity=2):
    conn = db()
    conn.execute("""INSERT INTO pos_orders (table_label, covers, status, opened_at)
                    VALUES (?, 2, 'open', datetime('now'))""", (label,))
    conn.commit()
    oid = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?", (label,)).fetchone()["id"]
    # `voided`, not a status column — voiding a line adds a compensating stock
    # movement rather than editing history.
    conn.execute("""INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                    voided, created_at) VALUES (?, ?, ?, ?, 0, datetime('now'))""",
                 (oid, f"{TAG} wine", price, quantity))
    conn.commit()
    conn.close()
    return oid


def run():
    s = Suite("Card reader")
    oc, ec, owner, emp = clients()

    real_stripe, real_key = m.stripe, m.STRIPE_SECRET_KEY
    fake = FakeStripe()
    m.stripe = fake
    m.STRIPE_SECRET_KEY = "sk_test_fake_for_tests"
    conn = db()
    conn.execute("""INSERT INTO app_settings (key, value) VALUES ('terminal_reader_id', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (READER,))
    conn.commit()
    conn.close()

    try:
        s.section("The settings page lists readers and says which can be used")
        r = oc.get("/admin/terminal")
        body = r.get_data(as_text=True)
        s.check("the page renders", r.status_code == 200, detail=f"HTTP {r.status_code}")
        s.check("the reader is listed", READER in body)
        s.check("a Bluetooth reader would be explained, not silently missing",
                "Bluetooth" in body)

        s.section("Putting the amount on the reader")
        oid = _open_tab(f"{TAG}-1")          # 2 x 18.50 = 37.00
        oc.post(f"/pos/{oid}/take-card", follow_redirects=True)
        s.check("a payment was created", len(fake.created) == 1,
                detail=f"{len(fake.created)} created")
        if fake.created:
            s.check("for the tab's total, in cents", fake.created[0]["amount"] == 3700,
                    detail=f"got {fake.created[0]['amount']}")
            s.check("as a card-present payment",
                    fake.created[0]["payment_method_types"] == ["card_present"])
            s.check("tagged with the tab it belongs to",
                    fake.created[0]["metadata"]["pos_order_id"] == str(oid))
        s.check("and handed to the configured reader",
                fake.sent_to_reader and fake.sent_to_reader[0][0] == READER,
                detail=f"{fake.sent_to_reader}")

        conn = db()
        stored = conn.execute("SELECT payment_intent_id FROM pos_orders WHERE id = ?",
                              (oid,)).fetchone()["payment_intent_id"]
        conn.close()
        s.check("the payment is remembered on the tab", bool(stored), detail=f"{stored!r}")

        s.section("Pressing it twice does not charge twice")
        # The guest is mid-tap. A second press, or a page reload, must resume the
        # same payment rather than start another.
        fake.status = "requires_confirmation"
        oc.post(f"/pos/{oid}/take-card", follow_redirects=True)
        s.check("still only one payment exists", len(fake.created) == 1,
                detail=f"{len(fake.created)} created")

        s.section("While waiting, nothing is settled")
        data = oc.get(f"/pos/{oid}/card-status").get_json()
        s.check("the till is told to keep waiting", data["state"] == "waiting",
                detail=f"got {data}")
        conn = db()
        still_open = conn.execute("SELECT status FROM pos_orders WHERE id = ?",
                                  (oid,)).fetchone()["status"]
        conn.close()
        s.check("and the tab stays open", still_open == "open", detail=f"got {still_open}")

        s.section("When the guest taps, the tab closes itself")
        fake.status = "succeeded"
        data = oc.get(f"/pos/{oid}/card-status").get_json()
        s.check("the till is told it is done", data.get("settled") is True, detail=f"got {data}")
        conn = db()
        row = conn.execute(
            """SELECT status, payment_method, payment_reference, settled_total
               FROM pos_orders WHERE id = ?""", (oid,)).fetchone()
        conn.close()
        s.check("the tab is marked paid", row["status"] == "paid", detail=f"got {row['status']}")
        s.check("by card on the terminal", row["payment_method"] == "card_terminal",
                detail=f"got {row['payment_method']}")
        # Without this the payment exists in Stripe and nothing in the app points
        # at it, which is exactly the reconciliation problem this replaces.
        s.check("carrying the payment's own reference", row["payment_reference"] == stored,
                detail=f"got {row['payment_reference']!r}, expected {stored!r}")
        s.check("for the right amount", abs((row["settled_total"] or 0) - 37.00) < 0.01,
                detail=f"got {row['settled_total']}")

        # The reader used to close a tab by its own arithmetic, which wrote no
        # payment row and no journal entry: the money arrived, the cash-up never
        # saw it, and the entry the law requires was simply absent. A card must
        # settle through exactly the same path as cash.
        conn = db()
        pay = conn.execute(
            """SELECT amount, method, reference FROM pos_payments
               WHERE order_id = ? ORDER BY id""", (oid,)).fetchall()
        entries = conn.execute(
            """SELECT event_type FROM pos_journal WHERE order_id = ?
               ORDER BY sequence""", (oid,)).fetchall()
        report = m.pos_day_report(conn, m.service_day())
        conn.close()
        s.check("the card payment is recorded as a payment, not just a closed tab",
                len(pay) == 1, detail=f"{len(pay)} payment row(s)")
        s.check("for what Stripe captured",
                pay and abs(pay[0]["amount"] - 37.00) < 0.01,
                detail=str(pay[0]["amount"] if pay else None))
        s.check("against its Stripe reference",
                pay and pay[0]["reference"] == stored, detail=str(pay[0]["reference"] if pay else None))
        kinds = [e["event_type"] for e in entries]
        s.check("the journal records the payment", "payment_taken" in kinds, detail=str(kinds))
        s.check("and the tab being settled", "tab_settled" in kinds, detail=str(kinds))
        s.check("and the cash-up counts it under card on the terminal",
                report["by_method"].get("card_terminal", 0) >= 37.00,
                detail=str(report["by_method"]))

        s.section("A decline leaves the tab open and retryable")
        oid2 = _open_tab(f"{TAG}-2", price=9.00, quantity=1)
        fake.status = "requires_payment_method"
        oc.post(f"/pos/{oid2}/take-card", follow_redirects=True)
        data = oc.get(f"/pos/{oid2}/card-status").get_json()
        s.check("the failure is reported", data["state"] == "failed", detail=f"got {data}")
        s.check("with the reason", "declined" in (data.get("message") or "").lower(),
                detail=f"got {data.get('message')!r}")
        conn = db()
        after = conn.execute(
            "SELECT status, payment_intent_id FROM pos_orders WHERE id = ?", (oid2,)).fetchone()
        conn.close()
        s.check("the tab is still open", after["status"] == "open")
        # Cleared, so the next attempt starts fresh instead of polling a dead one.
        s.check("and the dead payment is forgotten", after["payment_intent_id"] is None)

        s.section("Cancelling clears the reader")
        oid3 = _open_tab(f"{TAG}-3", price=5.00, quantity=1)
        fake.status = "requires_confirmation"
        oc.post(f"/pos/{oid3}/take-card", follow_redirects=True)
        before = len(fake.cancelled)
        oc.post(f"/pos/{oid3}/cancel-card", follow_redirects=True)
        s.check("the reader and the payment are both cancelled",
                len(fake.cancelled) >= before + 2, detail=f"{fake.cancelled}")
        conn = db()
        cleared = conn.execute("SELECT payment_intent_id FROM pos_orders WHERE id = ?",
                               (oid3,)).fetchone()["payment_intent_id"]
        conn.close()
        s.check("and the tab is clear to try again", cleared is None)

        s.section("Permissions")
        r = ec.get("/admin/terminal")
        s.check("an employee cannot choose the reader", r.status_code in (302, 403),
                detail=f"HTTP {r.status_code}")
        # Staff DO take payments, so the till routes stay open to them.
        oid4 = _open_tab(f"{TAG}-4")
        fake.status = "requires_confirmation"
        r = ec.post(f"/pos/{oid4}/take-card", follow_redirects=True)
        s.check("but staff can take a card payment", r.status_code == 200,
                detail=f"HTTP {r.status_code}")

    finally:
        m.stripe, m.STRIPE_SECRET_KEY = real_stripe, real_key
        conn = db()
        conn.execute("DELETE FROM app_settings WHERE key = 'terminal_reader_id'")
        conn.execute("DELETE FROM pos_order_lines WHERE name LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()

    s.section("With no reader configured, the till says so")
    oid5 = _open_tab(f"{TAG}-5")
    r = oc.post(f"/pos/{oid5}/take-card", follow_redirects=True)
    s.check("it refuses clearly rather than erroring",
            any("no card reader" in f.lower() for f in flashes(r)), r,
            detail=f"{flashes(r)[:1]}")
    conn = db()
    conn.execute("DELETE FROM pos_order_lines WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
