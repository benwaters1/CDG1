"""A service does not end at midnight.

Everything is stored in UTC, so the day boundary was falling at UTC midnight —
02:00 in a French summer, 01:00 in winter. Nobody chose that. Past it, one
service split across two days: the last tables landed on tomorrow's takings,
tomorrow's closure, and went looking for tomorrow's menu.

The cases worth testing are the awkward ones: half past midnight, three in the
morning, and the two days a year the clocks change.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "svcday-"
PARIS = ZoneInfo("Europe/Paris")


def _utc(local_str):
    """A local wall-clock time in Paris, as the UTC instant we would store."""
    return (datetime.fromisoformat(local_str).replace(tzinfo=PARIS)
            .astimezone(timezone.utc))


def _cleanup(conn):
    conn.execute("DELETE FROM pos_payments WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_closures WHERE period IN ('2026-08-18', '2026-08-19')")
    conn.commit()


def run():
    s = Suite("The service day")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("Where the boundary falls")
    # A French summer evening running late. Every one of these is the 18th's
    # service, and before this the last one landed on the 19th.
    for local, expected in [("2026-08-18 19:30", "2026-08-18"),
                            ("2026-08-18 23:50", "2026-08-18"),
                            ("2026-08-19 00:30", "2026-08-18"),
                            ("2026-08-19 02:30", "2026-08-18"),
                            ("2026-08-19 04:59", "2026-08-18")]:
        got = m.service_day_iso(_utc(local))
        s.check(f"{local} local is the {expected[-2:]}th's service", got == expected,
                detail=f"got {got}")

    # ...and breakfast the next morning is genuinely the next day.
    s.check("05:00 starts the new service day",
            m.service_day_iso(_utc("2026-08-19 05:00")) == "2026-08-19",
            detail=m.service_day_iso(_utc("2026-08-19 05:00")))
    s.check("and so does the middle of the following afternoon",
            m.service_day_iso(_utc("2026-08-19 15:00")) == "2026-08-19")

    s.section("Winter, when France is only one hour ahead")
    # The old UTC-midnight boundary moved by an hour between summer and winter.
    # This one must not.
    for local, expected in [("2026-01-14 23:50", "2026-01-14"),
                            ("2026-01-15 01:30", "2026-01-14"),
                            ("2026-01-15 03:30", "2026-01-14"),
                            ("2026-01-15 06:00", "2026-01-15")]:
        got = m.service_day_iso(_utc(local))
        s.check(f"{local} local -> {expected}", got == expected, detail=f"got {got}")

    s.section("The nights the clocks change")
    # Spring forward: 02:00 local jumps to 03:00. Autumn back: 02:00 happens
    # twice. Neither may produce a service that belongs to no day, or to two.
    s.check("the night the clocks go forward stays one service",
            m.service_day_iso(_utc("2026-03-29 01:30")) == "2026-03-28",
            detail=m.service_day_iso(_utc("2026-03-29 01:30")))
    s.check("and the night they go back does too",
            m.service_day_iso(_utc("2026-10-25 01:30")) == "2026-10-24",
            detail=m.service_day_iso(_utc("2026-10-25 01:30")))

    s.section("Bad input does not take the till down")
    s.check("a null timestamp is None, not a crash", m.service_day_iso(None) is not None)
    s.check("an unparseable one is None", m.service_day_iso("not a date") is None)

    s.section("A late table counts on the night it was served")
    late = _utc("2026-08-19 02:30").isoformat()        # 02:30 local, the 18th's service
    early = _utc("2026-08-18 20:00").isoformat()
    for label, opened, closed, total in [
            (TAG + "early", early, _utc("2026-08-18 22:00").isoformat(), 80.0),
            (TAG + "late", _utc("2026-08-18 21:30").isoformat(), late, 120.0)]:
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_state,
               service_date, settled_total, payment_method, opened_at, closed_at)
               VALUES (?, 2, 'paid', 'bill', '2026-08-18', ?, 'cash', ?, ?)""",
            (label, total, opened, closed))
        oid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
               state, vat_rate, created_at) VALUES (?, ?, ?, 1, 'main', 'served', 10, ?)""",
            (oid, label + " dish", total, opened))
        conn.execute(
            """INSERT INTO pos_payments (order_id, amount, method, created_at)
               VALUES (?, ?, 'cash', ?)""", (oid, total, closed))
    conn.commit()

    report = m.pos_day_report(conn, "2026-08-18")
    s.check("both tables are on the 18th's report", report["tabs"] == 2,
            detail=f"{report['tabs']} tabs, gross {report['gross']}")
    s.check("including the one that settled at half past two",
            report["gross"] == 200.0, detail=str(report["gross"]))
    s.check("and its covers", report["covers"] == 4, detail=str(report["covers"]))
    s.check("takings match the tabs", report["taken"] == 200.0, detail=str(report["taken"]))

    # The 19th must not inherit it — that was the actual bug.
    next_day = m.pos_day_report(conn, "2026-08-19")
    s.check("the 19th has none of it", next_day["tabs"] == 0,
            detail=f"{next_day['tabs']} tabs, gross {next_day['gross']}")

    s.section("The closure agrees with the report")
    closure, created = m.pos_close_period(conn, "day", "2026-08-18")
    conn.commit()
    s.check("the day closes", created)
    s.check("with the late table inside it", closure["gross_total"] == 200.0,
            detail=str(closure["gross_total"]))
    # Takings and tickets disagreeing is what the cash-up page warns about, so
    # the two have to be bucketed the same way.
    s.check("and its takings, not one bucketed differently",
            closure["taken_total"] == 200.0, detail=str(closure["taken_total"]))

    s.section("A tab opened after midnight sells from that evening's card")
    oc.post("/pos/open", data={"table_label": TAG + "now", "covers": "2"},
            follow_redirects=True)
    opened = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                          (TAG + "now",)).fetchone()
    s.check("the tab records the service day, not the calendar day",
            opened["service_date"] == m.service_day_iso(),
            detail=f"{opened['service_date']} vs {m.service_day_iso()}")

    _cleanup(conn)
    conn.close()
    return s
