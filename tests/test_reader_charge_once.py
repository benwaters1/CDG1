"""One charge on the card reader is one payment on the books.

The till's poll recorded a reader payment and left its PaymentIntent on the
tab. Whenever that charge left something owing -- a coffee added while the
guest was tapping, which the reader route allows on purpose -- the tab stayed
open, the page reloaded, saw the intent still there, polled again and recorded
the same charge a second time. Probed on 15 Oct 2026: EUR 20 on the reader, a
EUR 4 coffee added mid-tap; the first poll wrote EUR 20 and left EUR 4 owing,
the second wrote EUR 20 again and closed the tab. EUR 40 on the books for EUR 20
charged, the coffee never taken. A tab settled on the reader and then reopened
did the same: the old intent drew the "waiting for the guest" box and the poll
booked the old charge again.

Nothing here reaches Stripe. The reader's three calls -- putting an amount on
it, asking where a payment has got to, cancelling -- are stood in, and the
stripe module itself is swapped for one that raises on any use, so a path that
went round the stand-ins would fail here rather than charge anybody.
"""
import threading
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZONCE"
READER = "tmr_once_fakereader"


class _NoStripe:
    """Any use of the stripe module during this suite is a fault in the suite."""
    def __getattr__(self, name):
        raise AssertionError(f"test_reader_charge_once reached stripe.{name}")


class FakeReader:
    """The card reader, as the till sees it through its three helpers."""

    def __init__(self):
        self.put_on_reader = []      # (intent id, amount in euros)
        self.status = {}             # intent id -> Stripe status
        self.barrier = None          # set to make two polls meet before writing
        self.raced = 0
        self.meanwhile = None        # runs while Stripe is being asked

    def collect(self, conn, order_id, total, description, reader_id):
        pid = f"pi_{TAG.lower()}_{len(self.put_on_reader) + 1}"
        self.put_on_reader.append((pid, round(total, 2)))
        self.status[pid] = "requires_payment_method"
        return pid, None

    def state(self, intent_id):
        if not intent_id:
            return "unknown", "No payment in progress.", None
        if self.barrier is not None:
            # Both polls have read the tab and are about to hear "succeeded":
            # neither may go on to write until the other has got this far too.
            try:
                self.barrier.wait()
                self.raced += 1
            except threading.BrokenBarrierError:
                pass
        if self.meanwhile:
            self.meanwhile()
        status = self.status.get(intent_id, "requires_payment_method")
        asked = dict(self.put_on_reader).get(intent_id, 0)
        # What the reader charged is what it was asked for -- the guest taps the
        # amount on its screen, not whatever the tab has grown to since.
        intent = {"id": intent_id, "status": status,
                  "amount_received": int(round(asked * 100)) if status == "succeeded" else 0}
        if status == "succeeded":
            return "succeeded", "Paid.", intent
        if status == "declined":
            return "failed", "Card declined.", intent
        return "waiting", "Waiting for the guest to tap.", intent

    def cancel(self, reader_id, intent_id):
        return ""


def _open_tab(label, *lines):
    conn = db()
    cur = conn.execute("""INSERT INTO pos_orders (table_label, covers, status, opened_at)
                          VALUES (?, 2, 'open', datetime('now'))""", (label,))
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    for name, price in lines:
        _add_line(oid, name, price)
    return oid


def _add_line(oid, name, price):
    conn = db()
    conn.execute("""INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                    voided, created_at) VALUES (?, ?, ?, 1, 0, datetime('now'))""",
                 (oid, f"{TAG} {name}", price))
    conn.commit()
    conn.close()


def _books(oid):
    """What the till has on record for a tab: payments, status, intent, owing."""
    conn = db()
    pays = conn.execute(
        "SELECT amount, method, reference FROM pos_payments WHERE order_id = ? ORDER BY id",
        (oid,)).fetchall()
    order = conn.execute(
        "SELECT status, payment_intent_id, payment_reference, settled_total "
        "FROM pos_orders WHERE id = ?", (oid,)).fetchone()
    bill = m.pos_bill(conn, oid)
    taken = conn.execute(
        "SELECT COUNT(*) AS n FROM pos_journal WHERE order_id = ? AND event_type = 'payment_taken'",
        (oid,)).fetchone()["n"]
    conn.close()
    return {"rows": [dict(p) for p in pays], "paid": round(sum(p["amount"] for p in pays), 2),
            "status": order["status"], "intent": order["payment_intent_id"],
            "reference": order["payment_reference"] or "",
            "settled_total": order["settled_total"],
            "total": bill["total"], "outstanding": bill["outstanding"],
            "journal_payments": taken}


def run():
    s = Suite("A card charge is recorded once")
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

    try:
        s.section("A coffee added while the guest taps is owed, not paid twice")
        oid = _open_tab(f"{TAG}-coffee", ("lunch", 20.00))
        oc.post(f"/pos/{oid}/take-card")
        s.check("the reader is asked for the EUR 20 on the tab",
                fake.put_on_reader and fake.put_on_reader[-1][1] == 20.00,
                detail=str(fake.put_on_reader))
        first = fake.put_on_reader[-1][0] if fake.put_on_reader else None
        _add_line(oid, "coffee", 4.00)          # the waiter rings it up mid-tap
        fake.status[first] = "succeeded"

        data = oc.get(f"/pos/{oid}/card-status").get_json() or {}
        b = _books(oid)
        s.check("the first poll records the charge", len(b["rows"]) == 1, detail=str(b["rows"]))
        s.check("for the EUR 20 the reader took", b["paid"] == 20.00, detail=str(b["paid"]))
        s.check("and says the tab is not settled", data.get("settled") is False,
                detail=str(data))
        s.check("so the tab stays open with the coffee owing",
                b["status"] == "open" and abs(b["outstanding"] - 4.00) < 0.01,
                detail=f"{b['status']}, owing {b['outstanding']}")
        s.check("and the recorded charge is off the tab", b["intent"] is None,
                detail=repr(b["intent"]))

        # The page reloads on a part payment. That reload is what used to book
        # the same EUR 20 again and close the tab.
        page = oc.get(f"/pos/{oid}").get_data(as_text=True)
        data = oc.get(f"/pos/{oid}/card-status").get_json() or {}
        b = _books(oid)
        s.check("the reloaded page no longer waits on the paid charge",
                'id="card-waiting"' not in page)
        s.check("it offers the reader again for what is left",
                f"/pos/{oid}/take-card" in page)
        s.check("a second poll records nothing", len(b["rows"]) == 1,
                detail=f"{len(b['rows'])} rows, {b['paid']} paid")
        s.check("the books still say EUR 20, not EUR 40", b["paid"] == 20.00,
                detail=str(b["paid"]))
        s.check("the journal has one payment, not two", b["journal_payments"] == 1,
                detail=str(b["journal_payments"]))
        s.check("and the tab has not closed on money never taken", b["status"] == "open",
                detail=b["status"])
        s.check("the poll goes quiet rather than reporting a payment",
                data.get("state") == "idle", detail=str(data))

        oc.post(f"/pos/{oid}/take-card")
        s.check("the next tap asks for the EUR 4 coffee alone",
                len(fake.put_on_reader) == 2 and fake.put_on_reader[-1][1] == 4.00,
                detail=str(fake.put_on_reader))
        second = fake.put_on_reader[-1][0]
        fake.status[second] = "succeeded"
        data = oc.get(f"/pos/{oid}/card-status").get_json() or {}
        b = _books(oid)
        s.check("which settles the tab", data.get("settled") is True and b["status"] == "paid",
                detail=f"{data} / {b['status']}")
        s.check("two charges, two payments, EUR 24 in all",
                len(b["rows"]) == 2 and b["paid"] == 24.00 and b["settled_total"] == 24.00,
                detail=f"{b['rows']}, settled {b['settled_total']}")
        s.check("each against its own charge",
                [r["reference"] for r in b["rows"]] == [first, second],
                detail=str([r["reference"] for r in b["rows"]]))
        s.check("and the tab names both", first in b["reference"] and second in b["reference"],
                detail=b["reference"])

        s.section("A tab settled on the reader and reopened does not book it again")
        oid2 = _open_tab(f"{TAG}-reopen", ("dinner", 15.00))
        oc.post(f"/pos/{oid2}/take-card")
        charge = fake.put_on_reader[-1][0]
        fake.status[charge] = "succeeded"
        oc.get(f"/pos/{oid2}/card-status")
        b = _books(oid2)
        s.check("it settles once", b["status"] == "paid" and len(b["rows"]) == 1,
                detail=f"{b['status']}, {b['rows']}")
        s.check("and the settled tab no longer carries the charge", b["intent"] is None,
                detail=repr(b["intent"]))
        # Every tab settled on the reader before this fix still carries it, so
        # put it back the way the old code left it. And on a day nobody has
        # closed: a closed day refuses a reopen, as it should, and other suites
        # close today's.
        closed = {r["period"] for r in conn.execute(
            "SELECT period FROM pos_closures WHERE kind = 'day'")}
        day = m.house_today()
        while day.isoformat() in closed:
            day += timedelta(days=1)
        conn.execute("UPDATE pos_orders SET payment_intent_id = ?, service_date = ? WHERE id = ?",
                     (charge, day.isoformat(), oid2))
        conn.commit()
        r = oc.post(f"/pos/{oid2}/reopen", follow_redirects=True)
        s.check("the owner reopens it", _books(oid2)["status"] == "open", r,
                detail=_books(oid2)["status"])
        page = oc.get(f"/pos/{oid2}").get_data(as_text=True)
        s.check("the reopened tab shows its old charge as waiting (until the poll looks)",
                'id="card-waiting"' in page)
        data = oc.get(f"/pos/{oid2}/card-status").get_json() or {}
        b = _books(oid2)
        s.check("the poll books nothing new", len(b["rows"]) == 1 and b["paid"] == 15.00,
                detail=f"{b['rows']}")
        s.check("and does not close the reopened tab behind the owner",
                b["status"] == "open" and data.get("settled") is False,
                detail=f"{b['status']} / {data}")
        s.check("the stale charge is taken off it", b["intent"] is None, detail=repr(b["intent"]))
        page = oc.get(f"/pos/{oid2}").get_data(as_text=True)
        s.check("so the page stops waiting for a tap nobody will make",
                'id="card-waiting"' not in page)

        s.section("The page open on two devices, both polling as the money lands")
        oid3 = _open_tab(f"{TAG}-race", ("wine", 30.00))
        oc.post(f"/pos/{oid3}/take-card")
        charge = fake.put_on_reader[-1][0]
        fake.status[charge] = "succeeded"
        fake.barrier = threading.Barrier(2, timeout=10)
        answers = {}

        def poll(name, client):
            r = client.get(f"/pos/{oid3}/card-status")
            answers[name] = (r.status_code, r.get_json(silent=True))

        threads = [threading.Thread(target=poll, args=("owner", oc)),
                   threading.Thread(target=poll, args=("staff", ec))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        fake.barrier = None
        b = _books(oid3)
        s.check("both polls heard 'succeeded' before either wrote", fake.raced == 2,
                detail=f"{fake.raced} reached the barrier together")
        s.check("both answer without an error",
                all(code == 200 for code, _ in answers.values()), detail=str(answers))
        s.check("one charge, one payment", len(b["rows"]) == 1 and b["paid"] == 30.00,
                detail=str(b["rows"]))
        s.check("the tab is settled for EUR 30",
                b["status"] == "paid" and b["settled_total"] == 30.00,
                detail=f"{b['status']}, {b['settled_total']}")
        s.check("and both devices are told so",
                all((d or {}).get("settled") is True for _, d in answers.values()),
                detail=str(answers))

        s.section("The database refuses a second row for one reader charge")
        # The last word, under whatever path is added next. Done in a
        # transaction that is rolled back either way.
        refused = False
        try:
            m.pos_take_payment(conn, oid3, 30.00, "card_terminal", reference=charge)
        except m.sqlite3.IntegrityError:
            refused = True
        finally:
            conn.rollback()
        s.check("the same PaymentIntent cannot be written twice", refused)
        other = False
        try:
            m.pos_take_payment(conn, oid3, 5.00, "cash", reference=charge)
            other = True
        except m.sqlite3.IntegrityError:
            pass
        finally:
            conn.rollback()
        s.check("while a typed reference on a cash payment may repeat", other)

        s.section("Pressing the button, cancelling, and a decline still behave")
        oid4 = _open_tab(f"{TAG}-press", ("tea", 6.00))
        oc.post(f"/pos/{oid4}/take-card")
        waiting = fake.put_on_reader[-1][0]
        fake.status[waiting] = "requires_confirmation"
        before = len(fake.put_on_reader)
        oc.post(f"/pos/{oid4}/take-card")
        s.check("a second press while the guest taps resumes, not recharges",
                len(fake.put_on_reader) == before, detail=str(fake.put_on_reader[before - 1:]))
        data = oc.get(f"/pos/{oid4}/card-status").get_json() or {}
        s.check("waiting is still waiting", data.get("state") == "waiting"
                and _books(oid4)["intent"] == waiting, detail=str(data))
        oc.post(f"/pos/{oid4}/cancel-card")
        s.check("cancelling clears the tab", _books(oid4)["intent"] is None)
        oc.post(f"/pos/{oid4}/take-card")
        s.check("and the next press starts a fresh payment",
                len(fake.put_on_reader) == before + 1 and _books(oid4)["intent"]
                == fake.put_on_reader[-1][0], detail=str(fake.put_on_reader[-2:]))
        declined = fake.put_on_reader[-1][0]
        fake.status[declined] = "declined"
        data = oc.get(f"/pos/{oid4}/card-status").get_json() or {}
        b = _books(oid4)
        s.check("a decline is reported and forgotten, the tab still open",
                data.get("state") == "failed" and b["intent"] is None
                and b["status"] == "open" and not b["rows"], detail=f"{data} / {b}")

        # The other device presses again while this one is still asking Stripe
        # about the declined payment. Clearing the dead one must not clear the
        # payment that has just gone on the reader in its place.
        oc.post(f"/pos/{oid4}/take-card")
        dead = fake.put_on_reader[-1][0]
        fake.status[dead] = "declined"
        fresh = "pi_zzonce_pressed_elsewhere"

        def pressed_elsewhere():
            c = db()
            c.execute("UPDATE pos_orders SET payment_intent_id = ? WHERE id = ?", (fresh, oid4))
            c.commit()
            c.close()
        fake.meanwhile = pressed_elsewhere
        try:
            oc.get(f"/pos/{oid4}/card-status")
        finally:
            fake.meanwhile = None
        s.check("a decline clears only the payment it was about",
                _books(oid4)["intent"] == fresh, detail=repr(_books(oid4)["intent"]))

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
