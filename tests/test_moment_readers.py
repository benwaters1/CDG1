"""Nine readers that asked a stored moment about a bare date.

Every *_at column holds an instant in UTC. Asked `created_at >= '2033-07-01'`,
SQLite compares with midnight UTC -- one or two in the morning at the house --
so whatever was recorded in the small hours of a window's first day fell
outside it, and on the till the small hours of the night before fell inside.
The harness's MOMENTS watch found these nine by measuring the queries, but it
only sees that a bare date was used. This file pins what each reader answers
at the edge of its window, which is the part a guest or the owner would see.

Every reader is asked about a window in 2033, where nothing else in the shared
database lives, with rows planted either side of its edge:

  - THE HOUSE'S DAYS -- stock, the fridges, supplier invoices. A row at 00:30
    on the first day is in the window; one at 23:30 the evening before is not.
    Where there is an end, 23:30 on the last day is in and 00:30 the day after
    is out.
  - THE TILL'S DAYS run five to five. A line at 03:00 on the first day is the
    night before's and is out; one at 06:00 is in. A line sent at half past
    two is the service it was sent in, not the calendar date.

Both sides of every edge, because the cheap way to pass the first half is to
widen the window by a day.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZMOMENT"
TODAY = date(2033, 7, 15)
DAY = timedelta(days=1)


def _at(day, hhmm):
    """The stored string for a house wall-clock time on `day`, as the app
    writes it: the UTC instant, in isoformat."""
    h, mi = (int(x) for x in hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, h, mi, tzinfo=m.LOCAL_TZ)
    return local.astimezone(timezone.utc).isoformat()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM pos_order_lines WHERE order_id IN
                    (SELECT id FROM pos_orders WHERE table_label LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM fridge_readings WHERE unit_id IN
                    (SELECT id FROM fridge_units WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM fridge_units WHERE name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM stock_movements WHERE stock_item_id IN
                    (SELECT id FROM stock_items WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _item(conn, name):
    cur = conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost,
                                    reorder_level, active, created_at)
           VALUES (?, 'food', 'kg', 1.0, 0, 1, ?)""",
        (f"{TAG} {name}", _at(TODAY, "12:00")))
    return cur.lastrowid


def _move(conn, item, when, delta, reason, unit_cost=None, invoiced=None):
    conn.execute(
        """INSERT INTO stock_movements (stock_item_id, delta, reason, unit_cost,
                                        invoiced_quantity, note, created_at)
           VALUES (?, ?, ?, ?, ?, NULL, ?)""",
        (item, delta, reason, unit_cost, invoiced, when))


def _invoice(conn, vendor, when, amount):
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount,
                                 status, submitted_at)
           VALUES ('supplier_invoice', ?, 'delivery', ?, 'approved', ?)""",
        (f"{TAG} {vendor}", amount, when))


def _digit(total, place):
    """Which of the planted amounts (1, 10, 100, 1000) a total includes."""
    return int(round(total)) // place % 10


def _ask(fn, *args, **kwargs):
    conn = db()
    try:
        return fn(conn, *args, **kwargs)
    finally:
        conn.close()


def run():
    s = Suite("Nine readers ask about the house's day, not midnight UTC")
    _cleanup()
    try:
        # --------------------------------------------------------------
        s.section("The stock ledger, by the house's day")
        conn = db()
        waste, short = _item(conn, "Waste"), _item(conn, "Short")
        rising, steady = _item(conn, "Rising"), _item(conn, "Steady")
        sold = _item(conn, "Sold")

        w_first = TODAY - timedelta(days=90)
        _move(conn, waste, _at(w_first, "00:30"), -1, "wastage", 2.0)
        _move(conn, waste, _at(w_first - DAY, "23:30"), -10, "wastage", 2.0)

        d_first = TODAY - timedelta(days=30 * 6)
        d_in, d_out = _at(d_first, "00:30"), _at(d_first - DAY, "23:30")
        _move(conn, short, d_in, 8, "purchase", 3.0, invoiced=10)
        _move(conn, short, d_out, 5, "purchase", 3.0, invoiced=6)

        p_first = TODAY - timedelta(days=30 * 12)
        _move(conn, rising, _at(p_first - DAY, "23:30"), 10, "purchase", 10.0)
        _move(conn, rising, _at(p_first, "00:30"), 10, "purchase", 20.0)
        _move(conn, rising, _at(p_first + 10 * DAY, "12:00"), 10, "purchase", 30.0)
        _move(conn, steady, _at(p_first - DAY, "23:30"), 10, "purchase", 10.0)
        _move(conn, steady, _at(p_first + 10 * DAY, "12:00"), 10, "purchase", 20.0)
        conn.commit()
        conn.close()

        got = next((r for r in _ask(m.waste_log, days=90, today=TODAY)["rows"]
                    if r["name"] == f"{TAG} Waste"), None)
        qty = got["quantity"] if got else 0
        s.check("waste: a write-off at 00:30 on the first day is counted",
                qty in (1, 11), detail=f"{qty} counted, of 1 then and 10 the evening before")
        s.check("waste: one at 23:30 the evening before is not", qty in (0, 1),
                detail=f"{qty} counted")

        stamps = {x["row"]["created_at"] for x in
                  _ask(m.delivery_shortfalls, months=6, today=TODAY)
                  if x["row"]["name"] == f"{TAG} Short"}
        s.check("shortfalls: a delivery at 00:30 on the first day is listed",
                d_in in stamps, detail=f"{sorted(stamps)}")
        s.check("shortfalls: one at 23:30 the evening before is not",
                d_out not in stamps, detail=f"{sorted(stamps)}")

        moves = {r["name"]: r for r in _ask(m.price_changes, months=12, today=TODAY)}
        mine = moves.get(f"{TAG} Rising")
        s.check("price changes: the purchase at 00:30 on the first day is the one "
                "compared against",
                bool(mine) and mine["was"] == 20.0 and mine["now"] == 30.0,
                detail=f"{mine}" if mine else "no change found for the item")
        s.check("price changes: one at 23:30 the evening before is not compared at all",
                f"{TAG} Steady" not in moves, detail=f"{moves.get(TAG + ' Steady')}")

        # night_cost adds up everything consumed in its window, not only this
        # file's, so it is read before and after and the difference judged.
        start, end = date(2033, 6, 1), date(2033, 6, 30)
        before = _ask(m.night_cost, start=start, end=end)["consumed"]
        conn = db()
        for when, cost in ((_at(start - DAY, "23:30"), 1.0), (_at(start, "00:30"), 10.0),
                           (_at(end, "23:30"), 100.0), (_at(end + DAY, "00:30"), 1000.0)):
            _move(conn, sold, when, -1, "sale", cost)
        conn.commit()
        conn.close()
        took = round(_ask(m.night_cost, start=start, end=end)["consumed"] - before, 2)
        said = f"{took} consumed, of 1 / 10 / 100 / 1000 planted either side of both edges"
        s.check("night cost: a sale at 00:30 on the first day is in", _digit(took, 10) == 1,
                detail=said)
        s.check("night cost: one at 23:30 the evening before is not",
                _digit(took, 1) == 0, detail=said)
        s.check("night cost: one at 23:30 on the last day is in", _digit(took, 100) == 1,
                detail=said)
        s.check("night cost: one at 00:30 the day after is not", _digit(took, 1000) == 0,
                detail=said)

        # --------------------------------------------------------------
        s.section("The fridges, by the house's day")
        conn = db()
        unit = conn.execute(
            """INSERT INTO fridge_units (name, where_it_is, min_c, max_c, active, created_at)
               VALUES (?, 'Back kitchen', 0, 5, 1, ?)""",
            (f"{TAG} Cold room", _at(TODAY, "12:00"))).lastrowid
        f_first = TODAY - timedelta(days=14)
        f_in, f_out = _at(f_first, "00:30"), _at(f_first - DAY, "23:30")
        for when, celsius in ((f_in, 3.0), (f_out, 4.0)):
            conn.execute(
                """INSERT INTO fridge_readings (unit_id, read_at, celsius,
                           read_by_user_id, action_taken, note, created_at)
                   VALUES (?, ?, ?, NULL, NULL, NULL, ?)""", (unit, when, celsius, when))
        conn.commit()
        conn.close()
        ours = next((u for u in _ask(m.fridge_log, days=14, today=TODAY)
                     if u["unit"]["id"] == unit), None)
        read = {r["read_at"] for r in ours["readings"]} if ours else set()
        s.check("fridges: a reading at 00:30 on the first day is in the log", f_in in read,
                detail=f"{sorted(read)}")
        s.check("fridges: one at 23:30 the evening before is not", f_out not in read,
                detail=f"{sorted(read)}")

        # --------------------------------------------------------------
        s.section("Supplier invoices, by the house's day")
        conn = db()
        st_in, st_out = _at(p_first, "00:30"), _at(p_first - DAY, "23:30")
        _invoice(conn, "Wines", st_in, 10.0)
        _invoice(conn, "Wines", st_out, 1.0)
        s_start, s_end = date(2033, 6, 1), date(2033, 7, 1)      # end is exclusive
        for when, amount in ((_at(s_start - DAY, "23:30"), 1.0), (_at(s_start, "00:30"), 10.0),
                             (_at(s_end - DAY, "23:30"), 100.0), (_at(s_end, "00:30"), 1000.0)):
            _invoice(conn, "Butcher", when, amount)
        conn.commit()
        conn.close()
        statement = _ask(m.supplier_statement, f"{TAG} Wines", months=12, today=TODAY)
        listed = {r["submitted_at"] for r in statement["rows"]}
        s.check("statement: an invoice put in at 00:30 on the first day is on it",
                st_in in listed, detail=f"{sorted(listed)}")
        s.check("statement: one at 23:30 the evening before is not", st_out not in listed,
                detail=f"{sorted(listed)}")

        def butcher(**window):
            row = next((r for r in _ask(m.spend_by_vendor, **window)["rows"]
                        if r["vendor_name"] == f"{TAG} Butcher"), None)
            return row["total"] if row else 0.0

        # Asked with dates, and with ISO strings as the live page asks.
        for how, window in (("dates", {"start": s_start, "end": s_end}),
                            ("strings", {"start": s_start.isoformat(),
                                         "end": s_end.isoformat()})):
            paid = butcher(**window)
            said = f"{paid} paid, of 1 / 10 / 100 / 1000 planted either side of both edges"
            s.check(f"spend ({how}): an invoice at 00:30 on the first day counts",
                    _digit(paid, 10) == 1, detail=said)
            s.check(f"spend ({how}): one at 23:30 the evening before does not",
                    _digit(paid, 1) == 0, detail=said)
            s.check(f"spend ({how}): one at 23:30 on the last day counts",
                    _digit(paid, 100) == 1, detail=said)
            s.check(f"spend ({how}): one at 00:30 the day after the window does not",
                    _digit(paid, 1000) == 0, detail=said)

        # --------------------------------------------------------------
        s.section("The till, by service night -- five to five")
        ws_first = TODAY - timedelta(days=90)
        st_first = TODAY - timedelta(days=30)
        night = st_first + 5 * DAY          # a service with a late finish
        other = st_first + 7 * DAY          # a service whose last plate never went out

        # The precondition: nothing else is on the till in the window, so the
        # nights read back below are this file's and nobody else's.
        s.check("nothing else is on the till in the service-times window",
                not _ask(m.service_times, days=30, today=TODAY),
                detail=f"{_ask(m.service_times, days=30, today=TODAY)}")

        conn = db()
        order = conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_state,
                 service_date, opened_at)
               VALUES (?, 2, 'paid', 'seated', ?, ?)""",
            (f"{TAG}-till", ws_first.isoformat(), _at(ws_first, "19:00"))).lastrowid

        def line(name, created, sent=None, ready_after=None, served_after=None):
            sent_at = ready_at = served_at = None
            if sent:
                sent_dt = datetime.fromisoformat(sent)
                sent_at = sent
                ready_at = (sent_dt + timedelta(minutes=ready_after)).isoformat()
                if served_after is not None:
                    served_at = (sent_dt + timedelta(minutes=ready_after + served_after)
                                 ).isoformat()
            conn.execute(
                """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
                       voided, sent_at, ready_at, served_at, created_at, state)
                   VALUES (?, ?, 20.0, 1, 0, ?, ?, ?, ?, 'served')""",
                (order, f"{TAG} {name}", sent_at, ready_at, served_at, created))

        try:
            line("Early", _at(ws_first, "03:00"))      # the night before's service
            line("Late", _at(ws_first, "06:00"))       # the first day's
            small_hours = _at(st_first, "03:00")
            line("Before", small_hours, sent=small_hours, ready_after=10, served_after=2)
            evening, after_two = _at(night, "20:00"), _at(night + DAY, "02:30")
            line("Main", evening, sent=evening, ready_after=10, served_after=2)
            line("Last", after_two, sent=after_two, ready_after=20, served_after=4)
            unserved = _at(other, "21:00")
            line("Unserved", unserved, sent=unserved, ready_after=5)
            conn.commit()
        finally:
            conn.close()

        names = {r["name"] for r in _ask(m.what_sells, days=90, today=TODAY)}
        s.check("what sells: a dish at 06:00 on the first day is counted",
                f"{TAG} Late" in names)
        s.check("what sells: one at 03:00 that morning is the night before's, and is not",
                f"{TAG} Early" not in names)

        nights = _ask(m.service_times, days=30, today=TODAY)
        by_night = {n["night"]: n for n in nights}
        mine = by_night.get(night.isoformat())
        s.check("service times: a plate sent at half past two is the service it was "
                "sent in", bool(mine) and mine["lines"] == 2
                and (night + DAY).isoformat() not in by_night, detail=f"{nights}")
        s.check("service times: the small hours before the window opens are left out",
                (st_first - DAY).isoformat() not in by_night
                and st_first.isoformat() not in by_night, detail=f"{nights}")
        s.check("service times: that night's figures are both plates' -- 15 min to "
                "the pass on average, 3 to the table, 20 the worst",
                bool(mine) and (mine["to_ready"], mine["to_table"], mine["worst"])
                == (15.0, 3.0, 20.0), detail=f"{mine}")
        last = by_night.get(other.isoformat())
        s.check("service times: a plate never served still counts to the pass, and "
                "leaves the walk to the table blank rather than zero",
                bool(last) and (last["lines"], last["to_ready"], last["to_table"],
                                last["worst"]) == (1, 5.0, None, 5.0), detail=f"{last}")
        s.check("service times: newest night first",
                [n["night"] for n in nights] == [other.isoformat(), night.isoformat()],
                detail=f"{[n['night'] for n in nights]}")
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
