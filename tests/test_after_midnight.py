"""The five hours a night when the calendar date and the service day disagree.

Between midnight and 05:00 local, service_day() is still yesterday while the
calendar has already moved on. Every one of these was written and shipped
believing it handled that, and two of them did not — they were only ever
exercised in the nineteen hours a day when the two dates agree, so they passed.

This suite pins the boundary itself. It does not depend on the hour the suite
happens to run at: it asks each function what it does with a timestamp of half
past one in the morning.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "midnight-"
PARIS = ZoneInfo("Europe/Paris")


def _utc(local_str):
    """A Paris wall-clock time as the UTC instant that would be stored."""
    return (datetime.fromisoformat(local_str).replace(tzinfo=PARIS)
            .astimezone(timezone.utc))


def _cleanup(conn):
    conn.execute("DELETE FROM pos_payments WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_closures WHERE period IN ('2026-11-17', '2026-11-18')")
    conn.commit()


def run():
    s = Suite("After midnight, before five")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("The two dates genuinely part company")
    late = _utc("2026-11-18 01:30")           # 00:30 UTC on the 18th
    s.check("half past one on the 18th is the 17th's service",
            m.service_day_iso(late) == "2026-11-17", detail=m.service_day_iso(late))
    s.check("while the UTC calendar has already said the 18th",
            late.date().isoformat() == "2026-11-18", detail=late.date().isoformat())

    s.section("A tab opened then belongs to the night before")
    # This is what pos_open_tab stamps. It used the UTC calendar date, so a
    # tab opened at half past one was recorded on the following day — landing
    # on the wrong takings, the wrong closure and the wrong menu. The whole
    # point of the service day, missed in the one line that writes it down.
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state,
             service_date, opened_at)
           VALUES (?, 2, 'open', 'seated', ?, ?)""",
        (TAG + "late", m.service_day_iso(late), late.isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                       (TAG + "late",)).fetchone()
    s.check("the tab is stamped with the service day",
            row["service_date"] == "2026-11-17", detail=row["service_date"])
    s.check("not with the calendar date it was opened on",
            row["service_date"] != late.date().isoformat())

    s.section("And the live code agrees")
    # Opened through the app rather than by hand: whatever the clock says when
    # this runs, the stamp must equal service_day_iso() and not the UTC date.
    oc.post("/pos/open", data={"table_label": TAG + "now", "covers": "2"},
            follow_redirects=True)
    now_row = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                           (TAG + "now",)).fetchone()
    s.check("a tab opened now carries the service day",
            now_row and now_row["service_date"] == m.service_day_iso(),
            detail=f"{now_row['service_date'] if now_row else '?'} "
                   f"vs service day {m.service_day_iso()}")

    s.section("A closed day cannot be reopened into, even after midnight")
    # The guard read the first ten characters of closed_at — a UTC timestamp —
    # while closures are keyed by service day. Past midnight the two differ,
    # the closure lookup found nothing, and the guard silently let the tab
    # reopen. A closed period whose contents can still change is not closed.
    settled = _utc("2026-11-18 01:45")
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state,
             service_date, settled_total, payment_method, opened_at, closed_at)
           VALUES (?, 2, 'paid', 'bill', '2026-11-17', 90.0, 'cash', ?, ?)""",
        (TAG + "closed", late.isoformat(), settled.isoformat()))
    oid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
             state, vat_rate, created_at) VALUES (?, ?, 90.0, 1, 'main', 'served', 10, ?)""",
        (oid, TAG + "dish", late.isoformat()))
    conn.execute(
        """INSERT INTO pos_payments (order_id, amount, method, created_at)
           VALUES (?, 90.0, 'cash', ?)""", (oid, settled.isoformat()))
    conn.commit()

    s.check("its closed_at reads as the 18th",
            settled.isoformat()[:10] == "2026-11-18", detail=settled.isoformat()[:10])
    s.check("but the tab belongs to the 17th", conn.execute(
        "SELECT service_date FROM pos_orders WHERE id = ?", (oid,)).fetchone()
        ["service_date"] == "2026-11-17")

    m.pos_close_period(conn, "day", "2026-11-17")
    conn.commit()
    s.check("the 17th closes", bool(m.pos_closure_for(conn, "day", "2026-11-17")))

    r = oc.post(f"/pos/{oid}/reopen", follow_redirects=True)
    after = conn.execute("SELECT status FROM pos_orders WHERE id = ?", (oid,)).fetchone()
    s.check("the tab stays closed", after["status"] == "paid", detail=after["status"])
    s.check("and the refusal names the day", "closed off" in r.get_data(as_text=True),
            detail="no explanation given")

    s.section("A tab in an OPEN day still reopens")
    # The guard must not be so eager that it blocks the ordinary correction it
    # was written to allow.
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state,
             service_date, settled_total, payment_method, opened_at, closed_at)
           VALUES (?, 2, 'paid', 'bill', '2026-11-18', 40.0, 'cash', ?, ?)""",
        (TAG + "open-day", settled.isoformat(), settled.isoformat()))
    oid2 = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    oc.post(f"/pos/{oid2}/reopen", follow_redirects=True)
    s.check("a tab on a day nobody has closed reopens normally", conn.execute(
        "SELECT status FROM pos_orders WHERE id = ?", (oid2,)).fetchone()["status"] == "open")

    s.section("The late tab is on the right day's figures")
    report = m.pos_day_report(conn, "2026-11-17")
    s.check("the 17th counts it", report["gross"] >= 90.0, detail=str(report["gross"]))
    s.check("and the 18th does not",
            m.pos_day_report(conn, "2026-11-18")["gross"] == 0.0,
            detail=str(m.pos_day_report(conn, "2026-11-18")["gross"]))

    _cleanup(conn)
    conn.close()
    return s
