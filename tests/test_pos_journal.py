"""The till journal and the period closures.

A cash-up that answers differently on Tuesday than it did on Monday is not a
cash-up. `/pos/day` used to query live rows, so voiding a line tonight silently
rewrote yesterday's takings and nothing recorded that anything had changed.

The tests that matter here are the tampering ones: it is easy to write a hash
chain that never notices anything. Each one alters the database directly —
behind the app's back, as somebody with the file would — and checks the chain
says so.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "journal-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_payments WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Till journal")
    oc, ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    # The service day, not the calendar date. The app buckets a night's
    # trade by service_day(), which runs to 05:00 — so between midnight and
    # five these two differ, and a fixture built on the calendar date lands
    # on the wrong night. That is five hours every day where this suite
    # would fail for no reason.
    today = m.service_day()
    _cleanup(conn)

    cur = conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, sort_order, created_at)
           VALUES (?, 'main', 'main', 40.0, 1, 1, 1, 0, ?)""", (TAG + "Plat", now))
    dish = cur.lastrowid
    conn.commit()

    s.section("Every money event lands in the journal")
    before = conn.execute("SELECT COUNT(*) AS c FROM pos_journal").fetchone()["c"]
    oc.post("/pos/open", data={"table_label": TAG + "1", "covers": "2"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "1",)).fetchone()
    oc.post(f"/pos/{order['id']}/add", data={"menu_item_id": dish}, follow_redirects=True)
    oc.post(f"/pos/{order['id']}/adjust",
            data={"kind": "discount", "amount": "4", "reason": "slow service"},
            follow_redirects=True)
    oc.post(f"/pos/{order['id']}/pay", data={"method": "cash"}, follow_redirects=True)

    kinds = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM pos_journal WHERE order_id = ? ORDER BY sequence",
        (order["id"],)).fetchall()]
    for wanted in ("line_added", "discount_set", "payment_taken", "tab_settled"):
        s.check(f"{wanted.replace('_', ' ')} is recorded", wanted in kinds, detail=str(kinds))
    s.check("the journal grew", conn.execute(
        "SELECT COUNT(*) AS c FROM pos_journal").fetchone()["c"] > before)

    s.section("A receipt number is allocated, gapless, at first payment")
    settled = conn.execute("SELECT * FROM pos_orders WHERE id = ?", (order["id"],)).fetchone()
    s.check("the settled tab has one", bool(settled["receipt_number"]),
            detail=str(settled["receipt_number"]))
    s.check("in the year-and-sequence form",
            (settled["receipt_number"] or "").startswith(f"{today.year}-"),
            detail=str(settled["receipt_number"]))
    # A tab that never pays must not eat a number and leave a hole nobody can
    # account for.
    oc.post("/pos/open", data={"table_label": TAG + "2", "covers": "2"}, follow_redirects=True)
    walked = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                          (TAG + "2",)).fetchone()
    s.check("an unpaid tab has no number", walked["receipt_number"] is None)

    s.section("The chain verifies")
    s.check("a freshly written journal verifies", m.pos_journal_verify(conn) is None,
            detail=str(m.pos_journal_verify(conn)))

    s.section("...and notices when somebody edits it behind the app's back")
    # Each of these is done straight against the database, which is how it
    # would really happen — nobody tampers through the user interface.
    row = conn.execute("SELECT * FROM pos_journal WHERE order_id = ? AND event_type = 'payment_taken'",
                       (order["id"],)).fetchone()

    original = row["payload"]
    conn.execute("UPDATE pos_journal SET payload = ? WHERE id = ?",
                 (original.replace('"amount":36.0', '"amount":6.0'), row["id"]))
    conn.commit()
    broken = m.pos_journal_verify(conn)
    s.check("changing an amount breaks the chain",
            bool(broken) and broken["sequence"] == row["sequence"],
            detail=str(broken))
    s.check("and it says what is wrong",
            bool(broken) and "altered" in broken["problem"], detail=str(broken))
    conn.execute("UPDATE pos_journal SET payload = ? WHERE id = ?", (original, row["id"]))
    conn.commit()
    s.check("putting it back makes it verify again", m.pos_journal_verify(conn) is None)

    # Deleting is the one that matters: a row removed from the middle is how
    # a night's cash disappears.
    conn.execute("DELETE FROM pos_journal WHERE id = ?", (row["id"],))
    conn.commit()
    gap = m.pos_journal_verify(conn)
    s.check("deleting an entry is caught", bool(gap), detail=str(gap))
    s.check("and reported as a gap in the sequence",
            bool(gap) and "gap" in gap["problem"], detail=str(gap))

    # Rebuild so the rest of the suite runs against a sound chain.
    conn.execute("DELETE FROM pos_journal")
    conn.commit()
    s.check("an empty journal verifies (nothing to contradict)",
            m.pos_journal_verify(conn) is None)

    s.section("Closing a day freezes it")
    conn.execute("DELETE FROM pos_closures WHERE period = ?", (today.isoformat(),))
    conn.execute("UPDATE pos_orders SET status = 'void' WHERE status = 'open'")
    conn.commit()
    closure, created = m.pos_close_period(conn, "day", today.isoformat())
    conn.commit()
    s.check("the day closes", created and closure["kind"] == "day")
    frozen_total = closure["taken_total"]
    s.check("with the takings recorded", frozen_total >= 36.0, detail=str(frozen_total))
    s.check("and a running total that never resets",
            closure["perpetual_total"] >= frozen_total,
            detail=str(closure["perpetual_total"]))

    # The whole point: change something now and yesterday's answer must not move.
    conn.execute("INSERT INTO pos_payments (order_id, amount, method, created_at) "
                 "VALUES (?, 500.0, 'cash', ?)", (order["id"], now))
    conn.commit()
    again = m.pos_closure_for(conn, "day", today.isoformat())
    s.check("a later payment does not change a closed day",
            again["taken_total"] == frozen_total,
            detail=f"{again['taken_total']} vs {frozen_total}")
    live = m.pos_day_report(conn, today)
    s.check("while the live query does move — which is exactly why the closure exists",
            live["taken"] != frozen_total, detail=f"live {live['taken']}")

    s.check("closing twice is refused rather than recomputed",
            m.pos_close_period(conn, "day", today.isoformat())[1] is False)

    s.section("A closed day cannot be reopened into")
    conn.execute("UPDATE pos_orders SET status = 'paid', closed_at = ? WHERE id = ?",
                 (now, order["id"]))
    conn.commit()
    r = oc.post(f"/pos/{order['id']}/reopen", follow_redirects=True)
    s.check("the tab stays closed",
            conn.execute("SELECT status FROM pos_orders WHERE id = ?",
                         (order["id"],)).fetchone()["status"] == "paid", r)
    s.check("and it says why", "closed off" in r.get_data(as_text=True))

    s.section("The pages render and tell the truth")
    page = oc.get(f"/pos/day?date={today.isoformat()}").get_data(as_text=True)
    s.check("cash up shows the day as closed", "Closed off" in page)
    s.check("and reports the frozen figure, not the live one",
            f"{frozen_total:.2f}" in page, detail=str(frozen_total))
    journal = oc.get("/admin/pos/journal")
    s.check("the journal page renders", journal.status_code == 200)
    s.check("an employee cannot read the journal",
            ec.get("/admin/pos/journal").status_code in (302, 403))

    # A broken chain has to be loud on the page, not only in a helper.
    bad = conn.execute("SELECT * FROM pos_journal ORDER BY sequence LIMIT 1").fetchone()
    if bad:
        conn.execute("UPDATE pos_journal SET payload = '{\"tampered\":true}' WHERE id = ?",
                     (bad["id"],))
        conn.commit()
        page = oc.get("/admin/pos/journal").get_data(as_text=True)
        s.check("a broken chain is stated plainly on screen",
                "does not verify" in page, detail="expected the warning band")

    _cleanup(conn)
    conn.execute("DELETE FROM pos_journal")
    conn.execute("DELETE FROM pos_closures WHERE period = ?", (today.isoformat(),))
    conn.commit()
    conn.close()
    return s
