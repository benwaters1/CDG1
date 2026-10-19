"""A charge on the card reader survives the bill being settled another way.

Found by reading on 16 Oct 2026 and probed the same day. While the reader was
asking a guest to tap EUR 20, every other button on the tab stayed live. A
waiter took EUR 20 in cash, the tab closed with the reader still asking, the
guest tapped anyway -- and the till's poll, seeing a closed tab, answered
"Settled" and wrote nothing. Stripe had EUR 40, the books EUR 20. A QR link
paid on a phone while the reader asked did the same (EUR 60 against EUR 30),
and so did Cancel pressed a second after the guest had tapped: it cleared the
charge whatever Stripe said, so the tab stayed open with EUR 12 taken and
nothing on the books, and the next thing anybody did was take the bill again.

The rule this holds the till to: a charge comes off a tab only by being
written to the books, or once Stripe has said it can no longer be taken.
Nothing else may settle a tab while the reader asks; what nothing can refuse
(a phone paying the link) stops the reader instead; and a charge that lands
on a settled tab anyway is written there, shown on the tab, and put in front
of the owner -- never refunded by the till.

Pressing Take card again keeps the same rule. A charge Stripe could not be
asked about used to be overwritten by a new one, so the till forgot a charge
the guest could still pay and asked them to pay twice; it now stays on the
tab. A declined one is cancelled at Stripe before a fresh one replaces it,
and the audit log names it.

Nothing here reaches Stripe. The reader's three calls are stood in, and the
stripe module itself is swapped for one that raises on any use, so a path that
went round the stand-ins fails here rather than charging anybody. One section
puts a fake stripe module in for a moment to read what terminal_cancel itself
asks of the reader.
"""
import types
from datetime import timedelta

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZSETTLED"
READER = "tmr_settled_fakereader"


class _NoStripe:
    """Any use of the stripe module during this suite is a fault in the suite."""
    def __getattr__(self, name):
        raise AssertionError(f"test_reader_while_settled reached stripe.{name}")


class FakeReader:
    """The card reader, as the till sees it through its three helpers.

    Behaves as Stripe does where it matters here: a payment the guest has
    tapped (or is part-way through tapping) cannot be cancelled, and one that
    has been cancelled can no longer be taken.
    """

    def __init__(self):
        self.asked = {}            # intent id -> euros put on the reader
        self.status = {}           # intent id -> Stripe status
        self.cancelled = []        # intents Stripe accepted a cancel for
        self.offline = False       # Stripe cannot be reached at all
        self.meanwhile = None      # runs while Stripe is being asked to collect
        self.declined = {}         # intent id -> the reason Stripe gave
        self.after_ask = None      # runs once, after the next answer about a charge

    def collect(self, conn, order_id, total, description, reader_id):
        pid = f"pi_{TAG.lower()}_{len(self.asked) + 1}"
        self.asked[pid] = round(total, 2)
        self.status[pid] = "requires_payment_method"
        if self.meanwhile:
            self.meanwhile()
        return pid, None

    def state(self, intent_id):
        if self.offline:
            return "unknown", "Couldn't check the payment: connection refused", None
        status = self.status.get(intent_id, "requires_payment_method")
        cents = int(round(self.asked.get(intent_id, 0) * 100))
        intent = {"id": intent_id, "status": status, "amount": cents,
                  "amount_received": cents if status == "succeeded" else 0}
        if status == "succeeded":
            answer = "succeeded", "Paid.", intent
        elif status == "canceled":
            answer = "failed", "The payment was cancelled.", intent
        elif intent_id in self.declined:
            answer = "failed", self.declined[intent_id], intent
        else:
            answer = "waiting", "Waiting for the guest to tap.", intent
        if self.after_ask:
            then, self.after_ask = self.after_ask, None
            then()
        return answer

    def cancel(self, reader_id, intent_id):
        if self.offline:
            return "Could not connect to Stripe"
        status = self.status.get(intent_id)
        if status in ("succeeded", "processing"):
            return f"This PaymentIntent's status is {status}; it cannot be canceled."
        self.status[intent_id] = "canceled"
        self.cancelled.append(intent_id)
        return ""

    def last(self):
        return list(self.asked)[-1] if self.asked else None


def _open_tab(label, *lines):
    conn = db()
    cur = conn.execute("""INSERT INTO pos_orders (table_label, covers, status, opened_at,
                          service_date) VALUES (?, 2, 'open', datetime('now'), ?)""",
                       (label, m.house_today_iso()))
    oid = cur.lastrowid
    for name, price in lines:
        conn.execute("""INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                        voided, created_at) VALUES (?, ?, ?, 1, 0, datetime('now'))""",
                     (oid, f"{TAG} {name}", price))
    conn.commit()
    conn.close()
    return oid


def _books(oid):
    """What the till has on record for a tab."""
    conn = db()
    pays = conn.execute(
        "SELECT amount, method, reference FROM pos_payments WHERE order_id = ? ORDER BY id",
        (oid,)).fetchall()
    order = conn.execute(
        "SELECT status, payment_intent_id, settled_total FROM pos_orders WHERE id = ?",
        (oid,)).fetchone()
    bill = m.pos_bill(conn, oid)
    journal = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM pos_journal WHERE order_id = ? ORDER BY sequence", (oid,))]
    conn.close()
    return {"rows": [dict(p) for p in pays], "paid": round(sum(p["amount"] for p in pays), 2),
            "methods": [p["method"] for p in pays],
            "status": order["status"], "intent": order["payment_intent_id"],
            "settled_total": order["settled_total"], "outstanding": bill["outstanding"],
            "settles": journal.count("tab_settled"),
            "payments_journalled": journal.count("payment_taken")}


def _pay_by_phone(oid, euros, session_id):
    """The Stripe webhook for a QR link paid on the guest's phone."""
    conn = db()
    with m.app.test_request_context("/webhooks/stripe", method="POST"):
        m.settle_pos_from_stripe_session(
            conn, {"id": session_id, "amount_total": int(round(euros * 100))},
            {"kind": "pos", "pos_order_id": str(oid)})
    conn.close()


def _warnings():
    conn = db()
    with m.app.test_request_context("/"):
        found = m.owner_home_warnings(conn, m.house_today())
    conn.close()
    return found


def _line(found, words):
    return next((w for w in found if words in w["title"]), None)


def _count(found, words):
    line = _line(found, words)
    return line["count"] if line else 0


def _audit(action, needle):
    conn = db()
    row = conn.execute("SELECT details FROM audit_log WHERE action = ? AND details LIKE ? "
                       "ORDER BY id DESC LIMIT 1", (action, f"%{needle}%")).fetchone()
    conn.close()
    return row["details"] if row else None


LEFT = "left on the reader"
OVER = "more than"


def run():
    s = Suite("A reader charge survives the bill being settled another way")
    oc, ec, owner, emp = clients()

    fake = FakeReader()
    saved = {name: getattr(m, name) for name in (
        "stripe", "stripe_enabled", "terminal_collect_payment",
        "terminal_payment_state", "terminal_cancel")}
    m.stripe = _NoStripe()
    m.stripe_enabled = lambda: True
    m.terminal_collect_payment = fake.collect
    m.terminal_payment_state = fake.state
    m.terminal_cancel = fake.cancel
    conn = db()
    put_back_reader = _harness.keep_rows(conn, "app_settings", "key = 'terminal_reader_id'")
    conn.execute("""INSERT INTO app_settings (key, value) VALUES ('terminal_reader_id', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (READER,))
    conn.commit()
    before = _warnings()
    left_before, over_before = _count(before, LEFT), _count(before, OVER)

    try:
        # ------------------------------------------------------------------
        s.section("While the reader asks, nothing else settles the tab")
        oid = _open_tab(f"{TAG}-cash", ("lunch", 20.00))
        page = oc.get(f"/pos/{oid}").get_data(as_text=True)
        s.check("before the reader is asked, the page offers cash and the QR link",
                f'action="/pos/{oid}/pay"' in page and f"/pos/{oid}/pay-link" in page)
        oc.post(f"/pos/{oid}/take-card")
        charge = fake.last()
        s.check("the reader is asked for the EUR 20", fake.asked.get(charge) == 20.00,
                detail=str(fake.asked))
        page = oc.get(f"/pos/{oid}").get_data(as_text=True)
        s.check("the page waits for the tap, with a way to cancel it",
                'id="card-waiting"' in page and f"/pos/{oid}/cancel-card" in page)
        s.check("and offers no other way to pay while it does",
                f'action="/pos/{oid}/pay"' not in page and f"/pos/{oid}/pay-link" not in page,
                detail="a form posting to /pay or /pay-link is still on the page")
        s.check("saying why", 'id="reader-waiting-note"' in page
                and "waiting on the reader" in page)

        for label, form in (("cash", {"method": "cash"}),
                            ("cash with the change worked out",
                             {"method": "cash", "cash_received": "50"}),
                            ("a card on another terminal", {"method": "card_terminal"}),
                            ("a room charge", {"method": "room", "room_booking_id": "1"}),
                            ("on the house", {"method": "comp"}),
                            ("part of it", {"method": "cash", "amount": "5"}),
                            ("by seat", {"method": "cash", "seat": "1"})):
            r = oc.post(f"/pos/{oid}/pay", data=form, follow_redirects=True)
            b = _books(oid)
            s.check(f"{label} is refused with a sentence",
                    any("waiting on the reader" in f for f in flashes(r))
                    and not b["rows"] and b["status"] == "open" and b["intent"] == charge,
                    r, detail=f"{flashes(r)} / {b['rows']} / {b['status']}")
        r = oc.post(f"/pos/{oid}/pay-link", follow_redirects=True)
        b = _books(oid)
        s.check("and so is the QR link, before Stripe is asked for one",
                any("waiting on the reader" in f for f in flashes(r)) and not b["rows"],
                r, detail=str(flashes(r)))

        fake.status[charge] = "succeeded"           # the guest taps
        data = oc.get(f"/pos/{oid}/card-status").get_json() or {}
        b = _books(oid)
        s.check("the tap settles the tab", data.get("settled") is True and b["status"] == "paid",
                detail=f"{data} / {b['status']}")
        s.check("EUR 20 charged, EUR 20 on the books, once",
                b["rows"] == [{"amount": 20.00, "method": "card_terminal", "reference": charge}],
                detail=str(b["rows"]))

        s.section("The way to cancel is there even with the reader switched off")
        oid_off = _open_tab(f"{TAG}-off", ("tea", 6.00))
        oc.post(f"/pos/{oid_off}/take-card")
        m.stripe_enabled = lambda: False            # keys taken out mid-service
        try:
            page = oc.get(f"/pos/{oid_off}").get_data(as_text=True)
        finally:
            m.stripe_enabled = lambda: True
        s.check("a charge on the tab still draws the waiting box and its Cancel",
                'id="card-waiting"' in page and f"/pos/{oid_off}/cancel-card" in page)
        oc.post(f"/pos/{oid_off}/cancel-card")

        # ------------------------------------------------------------------
        s.section("Cancel records a tap that beat it")
        oid2 = _open_tab(f"{TAG}-beat", ("coffee", 12.00))
        oc.post(f"/pos/{oid2}/take-card")
        charge2 = fake.last()
        fake.status[charge2] = "succeeded"          # tapped a second before the press
        r = oc.post(f"/pos/{oid2}/cancel-card", follow_redirects=True)
        b = _books(oid2)
        s.check("the EUR 12 the guest paid goes on the books",
                b["rows"] == [{"amount": 12.00, "method": "card_terminal", "reference": charge2}],
                detail=str(b["rows"]))
        s.check("the tab is settled, not left open to be paid again",
                b["status"] == "paid" and b["intent"] is None, detail=f"{b['status']}")
        s.check("and the waiter is told the payment went through",
                any("already tapped" in f for f in flashes(r)), r, detail=str(flashes(r)))

        oid3 = _open_tab(f"{TAG}-stop", ("soup", 9.00))
        oc.post(f"/pos/{oid3}/take-card")
        charge3 = fake.last()
        r = oc.post(f"/pos/{oid3}/cancel-card", follow_redirects=True)
        b = _books(oid3)
        s.check("a payment nobody tapped is cancelled at Stripe",
                charge3 in fake.cancelled, detail=str(fake.cancelled))
        s.check("and leaves the tab, open and clear",
                b["intent"] is None and b["status"] == "open" and not b["rows"],
                detail=str(b))
        s.check("saying the reader is clear",
                any("reader is clear" in f for f in flashes(r)), r, detail=str(flashes(r)))
        r = oc.post(f"/pos/{oid3}/pay", data={"method": "cash"}, follow_redirects=True)
        s.check("after which cash is taken as normal", _books(oid3)["status"] == "paid",
                r, detail=str(flashes(r)))

        oid4 = _open_tab(f"{TAG}-midtap", ("wine", 18.00))
        oc.post(f"/pos/{oid4}/take-card")
        charge4 = fake.last()
        fake.status[charge4] = "processing"         # card on the reader, authorising
        r = oc.post(f"/pos/{oid4}/cancel-card", follow_redirects=True)
        b = _books(oid4)
        s.check("a tap in progress cannot be cancelled, so it stays on the tab",
                b["intent"] == charge4 and b["status"] == "open" and not b["rows"],
                detail=str(b))
        s.check("and the waiter is told so", any("could not be stopped" in f for f in flashes(r)),
                r, detail=str(flashes(r)))
        r = oc.post(f"/pos/{oid4}/pay", data={"method": "cash"}, follow_redirects=True)
        s.check("cash is still refused while it finishes", not _books(oid4)["rows"],
                r, detail=str(flashes(r)))
        fake.status[charge4] = "succeeded"
        oc.get(f"/pos/{oid4}/card-status")
        b = _books(oid4)
        s.check("and when it lands, it is recorded and the tab settles",
                b["paid"] == 18.00 and b["status"] == "paid", detail=str(b))

        # ------------------------------------------------------------------
        s.section("Stripe out of reach: the charge stays, and only the owner can let it go")
        oid5 = _open_tab(f"{TAG}-offline", ("cheese", 14.00))
        oc.post(f"/pos/{oid5}/take-card")
        charge5 = fake.last()
        fake.offline = True
        try:
            r = oc.post(f"/pos/{oid5}/cancel-card", follow_redirects=True)
            s.check("Cancel cannot ask Stripe, so the charge stays on the tab",
                    _books(oid5)["intent"] == charge5, detail=str(_books(oid5)))
            s.check("saying Stripe could not be reached",
                    any("could not be reached" in f for f in flashes(r)), r, detail=str(flashes(r)))
            page = oc.get(f"/pos/{oid5}").get_data(as_text=True)
            s.check("the owner sees a way to let it go", 'name="let_go"' in page)
            staff_page = ec.get(f"/pos/{oid5}").get_data(as_text=True)
            s.check("staff do not", 'name="let_go"' not in staff_page
                    and 'id="card-waiting"' in staff_page)
            ec.post(f"/pos/{oid5}/cancel-card", data={"let_go": "1"})
            s.check("and staff asking for it anyway changes nothing",
                    _books(oid5)["intent"] == charge5, detail=str(_books(oid5)))
            r = oc.post(f"/pos/{oid5}/cancel-card", data={"let_go": "1"}, follow_redirects=True)
            s.check("the owner can", _books(oid5)["intent"] is None, detail=str(_books(oid5)))
            s.check("told plainly where the charge is if the reader did take it",
                    any(charge5 in f and "not on the books" in f for f in flashes(r)),
                    r, detail=str(flashes(r)))
            s.check("and it goes on the audit log with the charge's Stripe id",
                    _audit("pos_reader_let_go_unchecked", charge5) is not None)
        finally:
            fake.offline = False
        oid6 = _open_tab(f"{TAG}-letgo", ("pudding", 8.00))
        oc.post(f"/pos/{oid6}/take-card")
        charge6 = fake.last()
        fake.status[charge6] = "succeeded"
        oc.post(f"/pos/{oid6}/cancel-card", data={"let_go": "1"})
        b = _books(oid6)
        s.check("letting go when Stripe does answer cannot throw away a tap that went through",
                b["paid"] == 8.00 and b["status"] == "paid", detail=str(b))
        s.check("nothing is put down as let go",
                _audit("pos_reader_let_go_unchecked", charge6) is None)

        # ------------------------------------------------------------------
        s.section("Paid on a phone while the reader asked")
        oid7 = _open_tab(f"{TAG}-phone", ("dinner", 30.00))
        oc.post(f"/pos/{oid7}/take-card")
        charge7 = fake.last()
        _pay_by_phone(oid7, 30.00, f"cs_{TAG.lower()}_7")
        b = _books(oid7)
        s.check("the phone payment settles the tab",
                b["status"] == "paid" and b["methods"] == ["card_link"], detail=str(b))
        s.check("and the reader is stopped there and then, not left asking",
                charge7 in fake.cancelled and b["intent"] is None,
                detail=f"{fake.cancelled} / {b['intent']!r}")
        data = oc.get(f"/pos/{oid7}/card-status").get_json() or {}
        s.check("so the page still polling is told it is settled",
                data.get("settled") is True and not data.get("paid_twice"), detail=str(data))

        oid8 = _open_tab(f"{TAG}-twice", ("wedding table", 450.00))
        oc.post(f"/pos/{oid8}/take-card")
        charge8 = fake.last()
        fake.status[charge8] = "succeeded"          # tapped on the reader too
        _pay_by_phone(oid8, 450.00, f"cs_{TAG.lower()}_8")
        b = _books(oid8)
        s.check("a tap that beat the phone is not cancelled -- it is recorded",
                sorted(b["methods"]) == ["card_link", "card_terminal"] and b["paid"] == 900.00,
                detail=str(b["rows"]))
        s.check("against the tab it was for, which stays settled once",
                b["status"] == "paid" and b["settled_total"] == 450.00 and b["settles"] == 1,
                detail=f"{b['status']}, {b['settled_total']}, {b['settles']} settlements")
        s.check("both payments are in the journal", b["payments_journalled"] == 2,
                detail=str(b["payments_journalled"]))
        s.check("the bill shows it took EUR 450 more than it came to",
                abs(b["outstanding"] + 450.00) < 0.01, detail=str(b["outstanding"]))
        s.check("and the audit log says how", _audit("pos_reader_charge_after_settled", charge8)
                is not None)
        page = oc.get(f"/pos/{oid8}").get_data(as_text=True)
        s.check("the settled tab says so, and that a refund is the owner's call",
                "450.00 more than it came to" in page and "refund the extra from Stripe" in page)

        # ------------------------------------------------------------------
        s.section("Settled another way while Stripe could not stop the reader")
        oid9 = _open_tab(f"{TAG}-stuck", ("lunch", 25.00))
        oc.post(f"/pos/{oid9}/take-card")
        charge9 = fake.last()
        fake.offline = True
        try:
            _pay_by_phone(oid9, 25.00, f"cs_{TAG.lower()}_9")
            b = _books(oid9)
            s.check("the phone payment is saved even though the reader could not be stopped",
                    b["status"] == "paid" and b["methods"] == ["card_link"], detail=str(b))
            s.check("and the charge stays on the settled tab rather than being forgotten",
                    b["intent"] == charge9, detail=repr(b["intent"]))
            data = oc.get(f"/pos/{oid9}/card-status").get_json() or {}
            s.check("the page still polling keeps polling rather than leaving",
                    data.get("settled") is False, detail=str(data))
        finally:
            fake.offline = False
        found = _warnings()
        line = _line(found, LEFT)
        s.check("the owner home says a card payment was left on the reader",
                _count(found, LEFT) == left_before + 1, detail=str([w["title"] for w in found]))
        s.check("linking to the tab", line is not None and line["href"] == f"/pos/{oid9}",
                detail=str(line))
        page = oc.get(f"/pos/{oid9}").get_data(as_text=True)
        s.check("the settled tab offers to check the reader",
                "Check the reader" in page and f"/pos/{oid9}/cancel-card" in page)
        fake.status[charge9] = "succeeded"          # the guest had tapped after all
        r = oc.post(f"/pos/{oid9}/cancel-card", follow_redirects=True)
        b = _books(oid9)
        s.check("checking it records the tap on the settled tab",
                b["paid"] == 50.00 and b["intent"] is None and b["settles"] == 1,
                detail=str(b))
        s.check("and says the guest may have paid twice",
                any("refund it from Stripe" in f for f in flashes(r)), r, detail=str(flashes(r)))
        found = _warnings()
        s.check("the reader line closes itself once checked",
                _count(found, LEFT) == left_before, detail=str([w["title"] for w in found]))
        s.check("and the bills that took too much are counted instead",
                _count(found, OVER) == over_before + 2, detail=str(_line(found, OVER)))
        worst = _line(found, OVER)
        s.check("the worst first, linked", worst is not None and worst["href"] == f"/pos/{oid8}"
                and "450" in worst["detail"], detail=str(worst))

        conn.execute("UPDATE pos_orders SET service_date = ? WHERE id IN (?, ?)",
                     ((m.house_today() - timedelta(days=20)).isoformat(), oid8, oid9))
        conn.commit()
        s.check("a fortnight on, they leave the front page",
                _count(_warnings(), OVER) == over_before)

        # ------------------------------------------------------------------
        s.section("A settled tab the old code left with a live charge on it")
        # Exactly what the old till left behind: cash closed the tab and the
        # intent stayed on it. The old poll answered "Settled" for it forever.
        oid10 = _open_tab(f"{TAG}-old", ("supper", 16.00))
        oc.post(f"/pos/{oid10}/take-card")
        charge10 = fake.last()
        with m.app.test_request_context("/"):
            c = db()
            m.pos_take_payment(c, oid10, 16.00, "cash")
            m.pos_close_if_settled(c, oid10, "cash")
            c.commit()
            c.close()
        s.check("(set up: closed in cash, charge still on it)",
                _books(oid10)["status"] == "paid" and _books(oid10)["intent"] == charge10)
        fake.status[charge10] = "succeeded"
        data = oc.get(f"/pos/{oid10}/card-status").get_json() or {}
        b = _books(oid10)
        s.check("the poll records the tap instead of answering 'Settled'",
                b["paid"] == 32.00 and "card_terminal" in b["methods"], detail=str(b))
        s.check("and tells the page the guest paid twice", data.get("paid_twice") is True,
                detail=str(data))
        data = oc.get(f"/pos/{oid10}/card-status").get_json() or {}
        s.check("asking again records nothing more", _books(oid10)["paid"] == 32.00,
                detail=str(data))

        oid11 = _open_tab(f"{TAG}-legacy", ("dinner", 22.00))
        oc.post(f"/pos/{oid11}/take-card")
        charge11 = fake.last()
        fake.status[charge11] = "succeeded"
        oc.get(f"/pos/{oid11}/card-status")
        # Settled on the reader before 16 Oct 2026: the charge is on the books
        # and the tab still carries its id.
        conn.execute("UPDATE pos_orders SET payment_intent_id = ? WHERE id = ?", (charge11, oid11))
        conn.commit()
        page = oc.get(f"/pos/{oid11}").get_data(as_text=True)
        s.check("a tab settled on the reader before the fix is not flagged",
                "Check the reader" not in page
                and _count(_warnings(), LEFT) == left_before, detail=str(_line(_warnings(), LEFT)))

        # ------------------------------------------------------------------
        s.section("Settled on another device while the reader was being asked")
        oid12 = _open_tab(f"{TAG}-race", ("tea", 7.00))

        def settled_elsewhere():
            with m.app.test_request_context("/"):
                c = db()
                m.pos_take_payment(c, oid12, 7.00, "cash")
                m.pos_close_if_settled(c, oid12, "cash")
                c.commit()
                c.close()
        fake.meanwhile = settled_elsewhere
        try:
            r = oc.post(f"/pos/{oid12}/take-card", follow_redirects=True)
        finally:
            fake.meanwhile = None
        charge12 = fake.last()
        b = _books(oid12)
        s.check("the charge put on the reader meanwhile is stopped",
                charge12 in fake.cancelled and b["intent"] is None,
                detail=f"{fake.cancelled} / {b['intent']!r}")
        s.check("the cash stands alone", b["rows"] == [{"amount": 7.00, "method": "cash",
                                                        "reference": None}], detail=str(b["rows"]))
        s.check("and the waiter is told", any("settled while the reader" in f for f in flashes(r)),
                r, detail=str(flashes(r)))

        # ------------------------------------------------------------------
        s.section("Take card pressed again with a charge already on the tab")
        oid13 = _open_tab(f"{TAG}-again", ("lunch", 24.00))
        oc.post(f"/pos/{oid13}/take-card")
        first = fake.last()
        oc.post(f"/pos/{oid13}/take-card")
        s.check("while the reader asks, a second press resumes the same charge",
                fake.last() == first and _books(oid13)["intent"] == first,
                detail=str(list(fake.asked)))

        # Stripe would not answer about the charge, though it would have
        # taken a new one: the flaky middle, not a dead line.
        fake.offline = True
        try:
            r = oc.post(f"/pos/{oid13}/take-card", follow_redirects=True)
        finally:
            fake.offline = False
        b = _books(oid13)
        s.check("with Stripe out of reach, no second charge is started",
                fake.last() == first, detail=str(list(fake.asked)))
        s.check("and the one already asked for stays on the tab, not cancelled",
                b["intent"] == first and b["status"] == "open" and first not in fake.cancelled,
                detail=str(b))
        said = [f for f in flashes(r) if "could not be reached to check" in f]
        s.check("saying so in one sentence: try again in a minute, or cancel",
                len(said) == 1 and "try again in a minute" in said[0]
                and "cancel" in said[0] and said[0].count(".") == 1,
                r, detail=str(flashes(r)))
        s.check("nothing is put down as replaced", _audit("pos_reader_replaced", first) is None)
        fake.status[first] = "succeeded"            # the guest had tapped meanwhile
        oc.get(f"/pos/{oid13}/card-status")
        b = _books(oid13)
        s.check("so the tap that was on its way is the one the till records, once",
                b["rows"] == [{"amount": 24.00, "method": "card_terminal", "reference": first}]
                and b["status"] == "paid", detail=str(b["rows"]))

        oid14 = _open_tab(f"{TAG}-declined", ("dinner", 38.00))
        oc.post(f"/pos/{oid14}/take-card")
        declined = fake.last()
        fake.declined[declined] = "Your card was declined."
        r = oc.post(f"/pos/{oid14}/take-card", follow_redirects=True)
        b = _books(oid14)
        fresh = fake.last()
        s.check("after a decline, a press puts a fresh charge on the reader for what is owed",
                fresh != declined and fake.asked.get(fresh) == 38.00 and b["intent"] == fresh,
                detail=f"{fake.asked} / {b['intent']!r}")
        s.check("having cancelled the declined one at Stripe first, so it can't be taken too",
                declined in fake.cancelled and fake.status[declined] == "canceled",
                detail=str(fake.cancelled))
        s.check("with no message: the reader asking again is the answer", flashes(r) == [],
                r, detail=str(flashes(r)))
        line = _audit("pos_reader_replaced", declined)
        s.check("and the audit log names the charge it replaced, and why",
                line is not None and "Your card was declined." in line, detail=repr(line))
        fake.status[fresh] = "succeeded"
        oc.get(f"/pos/{oid14}/card-status")
        b = _books(oid14)
        s.check("the second card settles the tab, with one charge on the books",
                b["rows"] == [{"amount": 38.00, "method": "card_terminal", "reference": fresh}]
                and b["status"] == "paid", detail=str(b["rows"]))

        # requires_payment_method is what a charge the reader is still asking
        # for looks like too, so a "declined" can be a tap a second away.
        # Stripe refuses to cancel a charge that has gone through.
        oid15 = _open_tab(f"{TAG}-beatdecline", ("wine", 26.00))
        oc.post(f"/pos/{oid15}/take-card")
        late = fake.last()
        fake.declined[late] = "Your card was declined."

        def tapped_meanwhile():
            fake.declined.pop(late, None)
            fake.status[late] = "succeeded"
        fake.after_ask = tapped_meanwhile
        try:
            r = oc.post(f"/pos/{oid15}/take-card", follow_redirects=True)
        finally:
            fake.after_ask = None
        b = _books(oid15)
        s.check("a 'declined' that went through before the cancel is recorded, not replaced",
                b["rows"] == [{"amount": 26.00, "method": "card_terminal", "reference": late}]
                and b["status"] == "paid", detail=str(b["rows"]))
        s.check("and no second charge goes on the reader", fake.last() == late,
                detail=str(list(fake.asked)))
        s.check("the waiter is told the guest had already tapped",
                any("already tapped" in f for f in flashes(r)), r, detail=str(flashes(r)))
        s.check("and nothing is put down as replaced",
                _audit("pos_reader_replaced", late) is None)

        # ------------------------------------------------------------------
        s.section("Stopping the reader never stops another table's tap")
        real_cancel = saved["terminal_cancel"]
        calls = []

        def reader_doing(intent):
            action = None
            if intent:
                action = {"type": "process_payment_intent", "status": "in_progress",
                          "process_payment_intent": {"payment_intent": intent}}
            return types.SimpleNamespace(
                retrieve=lambda rid: {"id": rid, "action": action},
                cancel_action=lambda rid: calls.append(("reader", rid)))

        def fake_stripe(intent_on_reader):
            return types.SimpleNamespace(
                terminal=types.SimpleNamespace(Reader=reader_doing(intent_on_reader)),
                PaymentIntent=types.SimpleNamespace(
                    cancel=lambda pid: calls.append(("intent", pid))))
        try:
            m.stripe = fake_stripe("pi_table_9_now")
            problems = real_cancel(READER, "pi_table_4_earlier")
            s.check("with the reader busy on another table, only our payment is cancelled",
                    calls == [("intent", "pi_table_4_earlier")] and problems == "",
                    detail=str(calls))
            calls.clear()
            m.stripe = fake_stripe("pi_table_4_now")
            real_cancel(READER, "pi_table_4_now")
            s.check("with the reader asking for ours, the reader is cleared too",
                    calls == [("reader", READER), ("intent", "pi_table_4_now")],
                    detail=str(calls))
            calls.clear()
            m.stripe = fake_stripe(None)
            real_cancel(READER, "pi_table_4_idle")
            s.check("with the reader idle, it is left alone",
                    calls == [("intent", "pi_table_4_idle")], detail=str(calls))
        finally:
            m.stripe = _NoStripe()

    finally:
        m.__dict__.update(saved)
        conn.rollback()
        put_back_reader()
        conn.execute("DELETE FROM pos_order_lines WHERE name LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
