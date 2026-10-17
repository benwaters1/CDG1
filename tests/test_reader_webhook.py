"""A tap on the card reader reaches the books by Stripe's own report.

The till learnt of a reader charge only by asking: the tab page's poll, the
Cancel button, or the owner's Check the reader. A tab page closed, asleep or
out of signal asks nobody, so a guest could tap, Stripe could take the money,
and nothing would be on the books until somebody opened that tab again --
which, for a bill everyone believes is paid, is never. Stripe sends
payment_intent.succeeded whatever the till is doing, and the webhook now
records the charge from it.

What this holds the webhook to:
- it writes through the poll's own claim, so the poll and the webhook arriving
  together write one row between them, and a retried event writes nothing;
- a charge left on no tab -- let go by the owner while Stripe could not be
  asked -- still lands, on the tab it was for, following a merge, and a tab
  settled meanwhile shows it took more than it came to;
- every other payment on the account fires the same event, and none of them
  is touched;
- a failure answers 500, so Stripe tries again, and leaves the charge where
  the poll can still find it.

Nothing here reaches Stripe. The reader's three calls are stood in, and the
stripe module is swapped for one that raises on any use except the webhook's
signature check, which accepts the event the suite hands it.
"""
import json
import os
import threading
import types

from _harness import Suite, ROOT, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZHOOK"
READER = "tmr_hook_fakereader"


class StripeStandIn:
    """The stripe module as this suite allows it: the webhook's signature
    check, answering with whatever event the suite has queued, and nothing
    else at all."""

    def __init__(self):
        self.event = None
        self.Webhook = types.SimpleNamespace(construct_event=self._construct)

    def _construct(self, payload, sig, secret):
        return self.event

    def __getattr__(self, name):
        raise AssertionError(f"test_reader_webhook reached stripe.{name}")


class FakeReader:
    """The card reader, as the till sees it through its three helpers."""

    def __init__(self):
        self.asked = {}            # intent id -> euros put on the reader
        self.status = {}           # intent id -> Stripe status
        self.cancelled = []
        self.offline = False
        self.on_answer = None      # runs as Stripe answers a poll "succeeded"

    def collect(self, conn, order_id, total, description, reader_id):
        pid = f"pi_{TAG.lower()}_{len(self.asked) + 1}"
        self.asked[pid] = round(total, 2)
        self.status[pid] = "requires_payment_method"
        return pid, None

    def state(self, intent_id):
        if self.offline:
            return "unknown", "Couldn't check the payment: connection refused", None
        status = self.status.get(intent_id, "requires_payment_method")
        cents = int(round(self.asked.get(intent_id, 0) * 100))
        intent = {"id": intent_id, "status": status, "amount": cents,
                  "amount_received": cents if status == "succeeded" else 0}
        if status == "succeeded":
            if self.on_answer:
                hook, self.on_answer = self.on_answer, None
                hook()
            return "succeeded", "Paid.", intent
        if status == "canceled":
            return "failed", "The payment was cancelled.", intent
        return "waiting", "Waiting for the guest to tap.", intent

    def cancel(self, reader_id, intent_id):
        if self.offline:
            return "Could not connect to Stripe"
        if self.status.get(intent_id) in ("succeeded", "processing"):
            return "This PaymentIntent cannot be canceled."
        self.status[intent_id] = "canceled"
        self.cancelled.append(intent_id)
        return ""

    def last(self):
        return list(self.asked)[-1] if self.asked else None

    def intent(self, pid, oid, *, kind="pos_reader"):
        """The PaymentIntent as payment_intent.succeeded carries it."""
        cents = int(round(self.asked[pid] * 100))
        meta = {"pos_order_id": str(oid)}
        if kind:
            meta["kind"] = kind
        return {"id": pid, "object": "payment_intent", "status": "succeeded",
                "amount": cents, "amount_received": cents, "currency": "eur",
                "payment_method_types": ["card_present"], "metadata": meta}


def _deliver(stand_in, intent, event_type="payment_intent.succeeded"):
    """One delivery of an event to the webhook, from a client of its own --
    Stripe is nobody's session."""
    stand_in.event = {"id": f"evt_{intent['id']}", "type": event_type,
                      "data": {"object": intent}}
    r = m.app.test_client().post("/webhooks/stripe", data=b"{}",
                                 headers={"Stripe-Signature": "t=1,v1=stub"})
    return r.status_code


def _open_tab(label, *lines):
    conn = db()
    cur = conn.execute("""INSERT INTO pos_orders (table_label, covers, status, opened_at,
                          service_date) VALUES (?, 2, 'open', datetime('now'), ?)""",
                       (label, m.house_today_iso()))
    oid = cur.lastrowid
    for name, price in lines:
        _add_line(conn, oid, name, price)
    conn.commit()
    conn.close()
    return oid


def _add_line(conn, oid, name, price):
    conn.execute("""INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                    voided, created_at) VALUES (?, ?, ?, 1, 0, datetime('now'))""",
                 (oid, f"{TAG} {name}", price))


def _books(oid):
    """What the till has on record for a tab, read on a fresh connection."""
    conn = db()
    pays = conn.execute(
        "SELECT amount, method, reference FROM pos_payments WHERE order_id = ? ORDER BY id",
        (oid,)).fetchall()
    order = conn.execute(
        "SELECT status, payment_intent_id, settled_total FROM pos_orders WHERE id = ?",
        (oid,)).fetchone()
    bill = m.pos_bill(conn, oid)
    journal = conn.execute(
        "SELECT event_type, payload FROM pos_journal WHERE order_id = ? ORDER BY sequence",
        (oid,)).fetchall()
    conn.close()
    kinds = [j["event_type"] for j in journal]
    return {"rows": [dict(p) for p in pays], "paid": round(sum(p["amount"] for p in pays), 2),
            "status": order["status"], "intent": order["payment_intent_id"],
            "settled_total": order["settled_total"], "outstanding": bill["outstanding"],
            "settles": kinds.count("tab_settled"),
            "payments_journalled": kinds.count("payment_taken"),
            "journal": [dict(j) for j in journal]}


def _charge_rows(pid):
    """Every payment row for one reader charge, whichever tab it is on."""
    conn = db()
    rows = conn.execute("SELECT order_id, amount FROM pos_payments "
                        "WHERE method = 'card_terminal' AND reference = ?", (pid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _audit(action, needle):
    conn = db()
    row = conn.execute("SELECT details FROM audit_log WHERE action = ? AND details LIKE ? "
                       "ORDER BY id DESC LIMIT 1", (action, f"%{needle}%")).fetchone()
    conn.close()
    return row["details"] if row else None


def _over_count():
    conn = db()
    with m.app.test_request_context("/"):
        found = m.owner_home_warnings(conn, m.house_today())
    conn.close()
    line = next((w for w in found if "more than" in w["title"]), None)
    return line["count"] if line else 0


def run():
    s = Suite("A reader charge reaches the books by Stripe's report")
    oc, ec, owner, emp = clients()

    fake = FakeReader()
    stand_in = StripeStandIn()
    saved = {name: getattr(m, name) for name in (
        "stripe", "stripe_enabled", "terminal_collect_payment", "terminal_payment_state",
        "terminal_cancel", "STRIPE_WEBHOOK_SECRET", "pos_take_payment")}
    m.stripe = stand_in
    m.stripe_enabled = lambda: True
    m.terminal_collect_payment = fake.collect
    m.terminal_payment_state = fake.state
    m.terminal_cancel = fake.cancel
    m.STRIPE_WEBHOOK_SECRET = "whsec_test_only_never_real"
    conn = db()
    put_back_reader = _harness.keep_rows(conn, "app_settings", "key = 'terminal_reader_id'")
    conn.execute("""INSERT INTO app_settings (key, value) VALUES ('terminal_reader_id', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (READER,))
    conn.commit()
    over_before = _over_count()

    try:
        # ------------------------------------------------------------------
        s.section("A tap nobody polled for arrives by webhook")
        oid = _open_tab(f"{TAG}-nopoll", ("lunch", 20.00))
        oc.post(f"/pos/{oid}/take-card")
        charge = fake.last()
        s.check("(set up: the reader is asked for EUR 20, the charge is on the tab)",
                fake.asked.get(charge) == 20.00 and _books(oid)["intent"] == charge)
        fake.status[charge] = "succeeded"           # the guest taps; the page is shut
        code = _deliver(stand_in, fake.intent(charge, oid))
        b = _books(oid)
        s.check("Stripe is answered 200", code == 200, detail=str(code))
        s.check("EUR 20 on the books, once, against the charge",
                b["rows"] == [{"amount": 20.00, "method": "card_terminal", "reference": charge}],
                detail=str(b["rows"]))
        s.check("the tab is settled, once, and the charge is off it",
                b["status"] == "paid" and b["settles"] == 1 and b["intent"] is None,
                detail=f"{b['status']}, {b['settles']} settlements, {b['intent']!r}")
        s.check("the payment is in the journal", b["payments_journalled"] == 1)
        data = oc.get(f"/pos/{oid}/card-status").get_json() or {}
        s.check("a page opened afterwards is told it is settled",
                data.get("settled") is True and not data.get("paid_twice"), detail=str(data))

        s.section("Stripe sends the same event again")
        for _ in range(3):
            code = _deliver(stand_in, fake.intent(charge, oid))
        again = _books(oid)
        s.check("it is answered 200, so Stripe stops sending it", code == 200, detail=str(code))
        s.check("and writes nothing", again["rows"] == b["rows"]
                and again["journal"] == b["journal"], detail=str(again["rows"]))

        # ------------------------------------------------------------------
        s.section("A tap that leaves something owing")
        oid2 = _open_tab(f"{TAG}-part", ("dinner", 30.00))
        oc.post(f"/pos/{oid2}/take-card")
        charge2 = fake.last()
        c = db()
        _add_line(c, oid2, "coffee", 4.00)          # added while the guest tapped
        c.commit()
        c.close()
        fake.status[charge2] = "succeeded"
        _deliver(stand_in, fake.intent(charge2, oid2))
        b = _books(oid2)
        s.check("what was tapped goes on the books",
                b["paid"] == 30.00 and b["rows"][0]["reference"] == charge2, detail=str(b["rows"]))
        s.check("the tab stays open for the coffee, with nothing left on the reader",
                b["status"] == "open" and abs(b["outstanding"] - 4.00) < 0.01
                and b["intent"] is None, detail=str(b))
        data = oc.get(f"/pos/{oid2}/card-status").get_json() or {}
        s.check("the page still polling hears there is nothing on the reader",
                data.get("state") == "idle", detail=str(data))
        tpl = open(os.path.join(ROOT, "templates", "pos_order.html"), encoding="utf-8").read()
        s.check("and its script reloads on that, rather than waiting four minutes",
                "d.state === 'idle'" in tpl and "window.location.reload" in tpl)
        page = oc.get(f"/pos/{oid2}").get_data(as_text=True)
        s.check("once reloaded, it no longer waits for a tap",
                'id="card-waiting"' not in page)
        r = oc.post(f"/pos/{oid2}/cancel-card", follow_redirects=True)
        s.check("Cancel pressed a moment too late says the card went through",
                any("already gone through" in f and "30.00" in f for f in flashes(r)),
                r, detail=str(flashes(r)))
        s.check("and changes nothing", _books(oid2)["rows"] == b["rows"])
        r = oc.post(f"/pos/{oid2}/pay", data={"method": "cash"}, follow_redirects=True)
        b = _books(oid2)
        s.check("the coffee is taken in cash and the bill adds up",
                b["status"] == "paid" and b["paid"] == 34.00 and b["settled_total"] == 34.00,
                r, detail=str(b))

        # ------------------------------------------------------------------
        s.section("The webhook lands between the poll's read and its claim")
        oid3 = _open_tab(f"{TAG}-between", ("wine", 26.00))
        oc.post(f"/pos/{oid3}/take-card")
        charge3 = fake.last()
        fake.status[charge3] = "succeeded"
        heard = {}
        # The poll has read the tab with the charge on it and asked Stripe;
        # Stripe's report arrives and is handled before the poll writes.
        fake.on_answer = lambda: heard.update(code=_deliver(stand_in, fake.intent(charge3, oid3)))
        data = oc.get(f"/pos/{oid3}/card-status").get_json() or {}
        b = _books(oid3)
        s.check("(the webhook did arrive in between)", heard.get("code") == 200,
                detail=str(heard))
        s.check("one charge, one row", _charge_rows(charge3) == [{"order_id": oid3,
                                                                  "amount": 26.00}],
                detail=str(_charge_rows(charge3)))
        s.check("the tab settled once", b["status"] == "paid" and b["settles"] == 1,
                detail=f"{b['status']}, {b['settles']}")
        s.check("and the poll says settled, not paid twice",
                data.get("settled") is True and not data.get("paid_twice"), detail=str(data))

        s.section("The poll gets there first")
        oid4 = _open_tab(f"{TAG}-pollfirst", ("cheese", 14.00))
        oc.post(f"/pos/{oid4}/take-card")
        charge4 = fake.last()
        fake.status[charge4] = "succeeded"
        oc.get(f"/pos/{oid4}/card-status")
        code = _deliver(stand_in, fake.intent(charge4, oid4))
        s.check("the webhook after it writes nothing",
                code == 200 and len(_charge_rows(charge4)) == 1, detail=str(_charge_rows(charge4)))

        s.section("Both at once, on two threads")
        oid5 = _open_tab(f"{TAG}-race", ("tasting menu", 90.00))
        oc.post(f"/pos/{oid5}/take-card")
        charge5 = fake.last()
        fake.status[charge5] = "succeeded"
        gate = threading.Barrier(2, timeout=10)
        answers = {}

        def poll():
            fake.on_answer = gate.wait               # meet the webhook, then claim
            r = ec.get(f"/pos/{oid5}/card-status")
            answers["poll"] = r.status_code

        def hook():
            gate.wait()
            answers["webhook"] = _deliver(stand_in, fake.intent(charge5, oid5))

        threads = [threading.Thread(target=poll), threading.Thread(target=hook)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        fake.on_answer = None
        b = _books(oid5)
        s.check("both answer without an error",
                answers.get("poll") == 200 and answers.get("webhook") == 200,
                detail=str(answers))
        s.check("one charge, one row", len(_charge_rows(charge5)) == 1 and b["paid"] == 90.00,
                detail=str(_charge_rows(charge5)))
        s.check("the tab settled once", b["status"] == "paid" and b["settles"] == 1,
                detail=f"{b['status']}, {b['settles']}")

        # ------------------------------------------------------------------
        s.section("Let go while Stripe could not be asked, and the guest had tapped")
        oid6 = _open_tab(f"{TAG}-letgo", ("supper", 18.00))
        oc.post(f"/pos/{oid6}/take-card")
        charge6 = fake.last()
        fake.offline = True
        try:
            oc.post(f"/pos/{oid6}/cancel-card", data={"let_go": "1"})
        finally:
            fake.offline = False
        s.check("(set up: the owner let the charge go unchecked)",
                _books(oid6)["intent"] is None
                and _audit("pos_reader_let_go_unchecked", charge6) is not None)
        oc.post(f"/pos/{oid6}/pay", data={"method": "cash"})
        s.check("(and the table then paid in cash)", _books(oid6)["status"] == "paid")
        fake.status[charge6] = "succeeded"
        code = _deliver(stand_in, fake.intent(charge6, oid6))
        b = _books(oid6)
        s.check("Stripe's report puts the tap on the books, against that tab",
                code == 200 and sorted(r["method"] for r in b["rows"]) == ["card_terminal", "cash"]
                and b["paid"] == 36.00, detail=str(b["rows"]))
        s.check("which stays settled once, for what it came to",
                b["status"] == "paid" and b["settles"] == 1 and b["settled_total"] == 18.00,
                detail=f"{b['status']}, {b['settles']}, {b['settled_total']}")
        s.check("the bill shows it took EUR 18 more than it came to",
                abs(b["outstanding"] + 18.00) < 0.01, detail=str(b["outstanding"]))
        s.check("the audit log says how it got there",
                _audit("pos_reader_charge_reported", charge6) is not None
                and _audit("pos_reader_charge_after_settled", charge6) is not None)
        s.check("and the owner home counts it among the bills that took too much",
                _over_count() == over_before + 1, detail=f"{_over_count()} vs {over_before}")
        _deliver(stand_in, fake.intent(charge6, oid6))
        s.check("sent again, it writes nothing", _books(oid6)["rows"] == b["rows"])

        s.section("Let go, and nothing else taken")
        oid7 = _open_tab(f"{TAG}-letgo-open", ("tea", 7.50))
        oc.post(f"/pos/{oid7}/take-card")
        charge7 = fake.last()
        fake.offline = True
        try:
            oc.post(f"/pos/{oid7}/cancel-card", data={"let_go": "1"})
        finally:
            fake.offline = False
        fake.status[charge7] = "succeeded"
        _deliver(stand_in, fake.intent(charge7, oid7))
        b = _books(oid7)
        s.check("the tap settles the open tab",
                b["status"] == "paid" and b["paid"] == 7.50 and b["settles"] == 1,
                detail=str(b))

        s.section("Let go, and the table merged into another")
        oid8 = _open_tab(f"{TAG}-letgo-merged", ("aperitif", 12.00))
        into = _open_tab(f"{TAG}-merged-into", ("mains", 40.00))
        oc.post(f"/pos/{oid8}/take-card")
        charge8 = fake.last()
        fake.offline = True
        try:
            oc.post(f"/pos/{oid8}/cancel-card", data={"let_go": "1"})
        finally:
            fake.offline = False
        with m.app.test_request_context("/"):
            c = db()
            survivor, said = m.pos_merge_tabs(c, oid8, into)
            c.commit()
            c.close()
        s.check("(set up: merged)", survivor == into, detail=said)
        fake.status[charge8] = "succeeded"
        _deliver(stand_in, fake.intent(charge8, oid8))
        b, gone = _books(into), _books(oid8)
        s.check("the tap follows the lines to the tab they are on now",
                b["paid"] == 12.00 and not gone["rows"], detail=f"{b['rows']} / {gone['rows']}")
        taken = [json.loads(j["payload"]) for j in b["journal"]
                 if j["event_type"] == "payment_taken"]
        s.check("and its journal entry names the tab it was raised on",
                len(taken) == 1 and taken[0].get("raised_on_order_id") == oid8
                and taken[0].get("reference") == charge8, detail=str(taken))
        s.check("which still owes the rest", b["status"] == "open"
                and abs(b["outstanding"] - 40.00) < 0.01, detail=str(b))

        # ------------------------------------------------------------------
        s.section("Every other payment on the account is left alone")
        oid9 = _open_tab(f"{TAG}-notours", ("lunch", 25.00))
        cases = {
            "a QR link's checkout, paid by card online": dict(
                payment_method_types=["card"],
                metadata={"pos_order_id": str(oid9), "kind": "pos"}),
            "another integration's card reader, naming no tab": dict(
                metadata={"order": "webkul-55"}),
            "another integration's card reader, with a tab number that is not ours": dict(
                metadata={"pos_order_id": str(oid9)}),
            "a charge that has not succeeded": dict(
                status="requires_payment_method", amount_received=0,
                metadata={"pos_order_id": str(oid9), "kind": "pos_reader"}),
        }
        for n, (label, fields) in enumerate(cases.items(), 1):
            intent = {"id": f"pi_{TAG.lower()}_other_{n}", "object": "payment_intent",
                      "status": "succeeded", "amount": 2500, "amount_received": 2500,
                      "currency": "eur", "payment_method_types": ["card_present"]}
            intent.update(fields)
            code = _deliver(stand_in, intent)
            b = _books(oid9)
            s.check(f"{label}: answered 200, nothing written",
                    code == 200 and not b["rows"] and b["status"] == "open",
                    detail=f"{code} / {b['rows']}")
        code = _deliver(stand_in, {"id": "cs_zzhook", "object": "checkout.session"},
                        event_type="checkout.session.expired")
        s.check("and an event it does not handle is still answered 200", code == 200)

        # ------------------------------------------------------------------
        s.section("The till's own charge for a tab that is not here")
        c = db()
        missing = c.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS i FROM pos_orders").fetchone()["i"]
        c.close()
        fake.asked["pi_zzhook_lost"] = 15.00
        for _ in range(2):
            code = _deliver(stand_in, fake.intent("pi_zzhook_lost", missing))
        c = db()
        tasks = c.execute("SELECT title, notes, priority FROM tasks WHERE origin = 'payment' "
                          "AND instr(notes, 'pi_zzhook_lost')").fetchall()
        c.close()
        s.check("goes in front of the owner as a task, once however often it is sent",
                code == 200 and len(tasks) == 1 and tasks[0]["priority"] == "high",
                detail=str([dict(t) for t in tasks]))
        s.check("saying how much and that it is not on the books",
                tasks and "15.00" in tasks[0]["title"] and "not on the books" in tasks[0]["notes"],
                detail=str([dict(t) for t in tasks]))
        s.check("and nothing is written for it", not _charge_rows("pi_zzhook_lost"))

        # ------------------------------------------------------------------
        s.section("A failure is a 500, so Stripe tries again")
        oid10 = _open_tab(f"{TAG}-fails", ("dessert", 9.00))
        oc.post(f"/pos/{oid10}/take-card")
        charge10 = fake.last()
        fake.status[charge10] = "succeeded"

        def breaks(*a, **k):
            raise RuntimeError("disk full")
        m.pos_take_payment = breaks
        # As production answers it: a 500 to Stripe, not an exception
        # raised into this suite.
        kept = m.app.config.get("PROPAGATE_EXCEPTIONS")
        m.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            code = _deliver(stand_in, fake.intent(charge10, oid10))
        finally:
            m.pos_take_payment = saved["pos_take_payment"]
            m.app.config["PROPAGATE_EXCEPTIONS"] = kept
        b = _books(oid10)
        s.check("Stripe is told it failed", code == 500, detail=str(code))
        s.check("nothing half-written, and the charge still on the tab for the poll",
                not b["rows"] and b["intent"] == charge10 and b["status"] == "open",
                detail=str(b))
        code = _deliver(stand_in, fake.intent(charge10, oid10))
        b = _books(oid10)
        s.check("Stripe's retry records it", code == 200 and b["paid"] == 9.00
                and b["status"] == "paid", detail=str(b))

        # ------------------------------------------------------------------
        s.section("The till marks the charges it makes as its own")
        made = {}
        m.stripe = types.SimpleNamespace(
            PaymentIntent=types.SimpleNamespace(
                create=lambda **kw: made.update(kw) or {"id": "pi_zzhook_marked"}),
            terminal=types.SimpleNamespace(Reader=types.SimpleNamespace(
                process_payment_intent=lambda reader, payment_intent: None)))
        try:
            c = db()
            pid, error = saved["terminal_collect_payment"](c, 4242, 10.0, "Table 9", READER)
            c.close()
        finally:
            m.stripe = stand_in
        s.check("the reader's charge carries the tab and the till's mark",
                pid == "pi_zzhook_marked" and error is None
                and made.get("metadata") == {"pos_order_id": "4242", "kind": "pos_reader"}
                and made.get("payment_method_types") == ["card_present"],
                detail=str(made))

    finally:
        m.__dict__.update(saved)
        conn.rollback()
        put_back_reader()
        conn.execute("DELETE FROM tasks WHERE origin = 'payment' AND instr(notes, ?)",
                     ("pi_zzhook",))
        conn.execute("DELETE FROM pos_order_lines WHERE name LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
