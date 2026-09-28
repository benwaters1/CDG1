"""What day it is AT THE HOUSE, on the pages that ask.

The breakfast checklist was a day behind for the first hours of every day.
Ticking an item wrote `datetime.now(timezone.utc).date()` against it, and
Paris runs one or two hours AHEAD of UTC -- so from midnight until 01:00 in
winter, or 02:00 in summer, the house was already on the new day and UTC was
still on the old one. Somebody closing down after a late service ticked off
the croissants for the morning; the tick went to yesterday; at seven the
list was blank again.

Nothing looked wrong because every part of it agreed with itself — the writer
and both readers used the same wrong day. That is the shape this file guards:
consistency is not correctness, and a test written with the same expression as
the code passes for ever.

So this does not compare the app's answer to the machine's clock at whatever
hour the suite happens to run. It moves the clock to the hours where the two
differ and asks the app what day it is. Those are:

    winter (UTC+1)   00:00–00:59 local
    summer (UTC+2)   00:00–01:59 local

and the same instants read as the previous day in UTC. A run at three in the
afternoon would never see it, which is why the original bug survived
everything.

The first check in each section says out loud that the two clocks disagree
at the instant being tested. It is not decoration: I first wrote these as
23:30, which is 21:30 UTC and the SAME day, and without that check the
section would have reported four cheerful passes while proving nothing.
"""
from _harness import Suite, clients, db, house_today

import inspect
import io
import os

import _harness

m = _harness.m
TAG = "ZZHOUSEDAY"


class _FrozenDatetime(m.datetime):
    """datetime.now() pinned to one instant, tz-aware, for both zones."""

    _at = None

    @classmethod
    def now(cls, tz=None):
        assert cls._at is not None, "nothing pinned"
        return cls._at.astimezone(tz) if tz else cls._at.replace(tzinfo=None)


def _at(instant, fn):
    """Run fn() as if it were `instant`, then put the real clock back.

    The same swap the breakfast section does inline, named so the sections
    below can ask the app a question at an hour that is otherwise unreachable.
    """
    real = m.datetime
    _FrozenDatetime._at = instant
    m.datetime = _FrozenDatetime
    try:
        return fn()
    finally:
        m.datetime = real


def _cleanup(conn):
    conn.execute(
        "DELETE FROM breakfast_checklist_log WHERE item_id IN "
        "(SELECT id FROM breakfast_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM breakfast_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the day at the house")
    conn = db()
    oc, _ec, _owner, _emp = clients()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO breakfast_items (name, category, active, low_stock,
                                        created_at)
           VALUES (?, 'bakery', 1, 0, ?)""",
        (TAG + " Croissants", m.datetime.now(m.timezone.utc).isoformat()))
    item = conn.execute("SELECT id FROM breakfast_items WHERE name LIKE ?",
                        (TAG + "%",)).fetchone()["id"]
    conn.commit()

    def ticks_on(day):
        return conn.execute(
            "SELECT COUNT(*) FROM breakfast_checklist_log "
            "WHERE item_id = ? AND checklist_date = ?",
            (item, day.isoformat())).fetchone()[0]

    # Half past midnight, on a summer date and a winter one. Both are inside
    # the window where the house has turned over and UTC has not.
    for label, local_iso in (("in summer, at 00:30", "2026-07-16T00:30:00+02:00"),
                             ("in winter, at 00:30", "2026-01-16T00:30:00+01:00")):
        at = m.datetime.fromisoformat(local_iso)
        local_day = at.astimezone(m.LOCAL_TZ).date()
        utc_day = at.astimezone(m.timezone.utc).date()

        s.section("Ticking off the breakfast list " + label)
        s.check("the two clocks really do disagree at this instant",
                local_day != utc_day,
                detail=f"{local_day} at the house, {utc_day} in UTC — if these "
                       "ever match, this section is proving nothing")

        _FrozenDatetime._at = at
        real = m.datetime
        m.datetime = _FrozenDatetime
        try:
            oc.post(f"/breakfast/{item}/toggle", follow_redirects=True)
            page = oc.get("/breakfast").get_data(as_text=True)
        finally:
            m.datetime = real

        s.check("the tick is written against today at the house",
                ticks_on(local_day) == 1,
                detail="somebody closing down after a late service ticks "
                       "off the croissants for the morning, and it lands on "
                       "the wrong day")
        s.check("and not against yesterday", ticks_on(utc_day) == 0,
                detail=f"a row on {utc_day} is a tick nobody will see in the "
                       "morning")

        # The reader has to agree with the writer. Them disagreeing is a
        # checklist that ticks in one place and reads back unticked in the
        # other, which is worse than either being wrong on its own.
        # The row for THIS item, and the class the template actually puts on
        # a ticked one. The first version of this check was
        # `row not in page or "checked" in page`, whose left half is true the
        # moment the markup changes -- so it would have gone on passing after
        # the thing it was looking at stopped existing.
        at = page.find(f'id="view-breakfast-{item}"')
        row = page[page.rfind("<div", 0, at):page.find(">", at) + 1] if at >= 0 else ""
        s.check("and the page reads it back as ticked", "task-done" in row,
                detail="the writer and the reader have to agree about what "
                       "day it is, or the list ticks in one place and reads "
                       "back blank in the other")

        conn.execute("DELETE FROM breakfast_checklist_log WHERE item_id = ?",
                     (item,))
        conn.commit()

    s.section("And at a normal hour it still works")
    # The half that stops this from being a test only about midnight.
    today = house_today()
    oc.post(f"/breakfast/{item}/toggle", follow_redirects=True)
    s.check("a tick now lands on today", ticks_on(today) == 1,
            detail=str(today))
    oc.post(f"/breakfast/{item}/toggle", follow_redirects=True)
    s.check("and unticking removes it", ticks_on(today) == 0)

    s.section("The pages that ask what day it is ask the house")
    # Named, because these three moved together and a later edit that puts
    # one of them back on UTC would leave the other two disagreeing with it.
    for fn_name, why in (
            ("toggle_breakfast_item", "writes the checklist"),
            ("breakfast", "reads it back"),
            ("today_sheet", "prints who is in the house today")):
        src = inspect.getsource(m.app.view_functions[fn_name])
        s.check(f"{fn_name} ({why}) uses the local day",
                "house_today" in src
                and "datetime.now(timezone.utc).date()" not in src,
                detail="a UTC calendar date where a local one belongs")

    s.section("Three answers, and the return type picks")
    # The breakfast sections above prove one page. These prove the rule the
    # rest of the app now follows, because 125 other call sites had the same
    # fault and no page of their own to catch it.
    late = m.datetime(2026, 8, 30, 22, 30, tzinfo=m.timezone.utc)   # 00:30 local
    s.check("the two clocks disagree at this instant too",
            late.date().isoformat() == "2026-08-30",
            detail="if this ever matches, the section below proves nothing")
    s.check("house_today answers with the house's day",
            _at(late, m.house_today) == m.date(2026, 8, 31),
            detail=str(_at(late, m.house_today)))
    s.check("and the iso form agrees with it",
            _at(late, m.house_today_iso) == "2026-08-31",
            detail=str(_at(late, m.house_today_iso)))

    s.section("A stored moment is read here too, not sliced")
    # The other half of the same fault, and the one that reads as harmless.
    # Truncating an ISO stamp reads UTC by definition, so anything recorded
    # after midnight carries yesterday's date all of the next day -- filed
    # under the wrong heading, aged a day out, invoiced on the wrong date.
    stamp = "2026-08-30T22:30:00+00:00"
    s.check("slicing the stamp gives the wrong day", stamp[:10] == "2026-08-30")
    s.check("reading it in local time gives the right one",
            m.house_date_iso(stamp) == "2026-08-31", detail=m.house_date_iso(stamp))
    s.check("a stamp with no zone is assumed UTC, as everything stored is",
            m.house_date_iso("2026-08-30T22:30:00") == "2026-08-31",
            detail=m.house_date_iso("2026-08-30T22:30:00"))
    s.check("midday is the same day either way, which is why this hid so long",
            m.house_date_iso("2026-08-30T11:00:00+00:00") == "2026-08-30")
    # Callers do `if not stamp: return None` on the result, so an unreadable
    # one has to come back falsy rather than raising or returning "None".
    s.check("nothing in, nothing out",
            m.house_date(None) is None and m.house_date("") is None
            and m.house_date("not a date") is None)
    s.check("and the iso form gives an empty string, not the word None",
            m.house_date_iso(None) == "" and m.house_date_iso("rubbish") == "",
            detail=repr(m.house_date_iso(None)))

    s.section("The restaurant has a third answer again")
    # 01:30 is a new day to the house and last night's service to the till.
    small_hours = m.datetime(2026, 8, 30, 23, 30, tzinfo=m.timezone.utc)
    s.check("after midnight the house has turned over",
            _at(small_hours, m.house_today) == m.date(2026, 8, 31))
    s.check("but the till is still serving last night",
            _at(small_hours, m.service_day) == m.date(2026, 8, 30),
            detail=str(_at(small_hours, m.service_day)))
    after_five = m.datetime(2026, 8, 31, 8, 0, tzinfo=m.timezone.utc)
    s.check("and past the rollover the two agree again",
            _at(after_five, m.service_day) == m.date(2026, 8, 31)
            and _at(after_five, m.house_today) == m.date(2026, 8, 31))

    s.section("Cashing up at 03:00 still says today")
    # What the fault cost the till. pos_day's `on` defaults to service_day(),
    # and pos_close_day independently refuses to close the current service day
    # without a confirmation tick. The page decided whether to DRAW that tick
    # box from a UTC date, so at 03:00 the two disagreed: the route demanded
    # the box and the page had not drawn one. An unclosable till, at the hour
    # a restaurant closes, and the only symptom is a flash telling you to tick
    # something that is not on the screen.
    three_am = m.datetime(2026, 9, 1, 1, 0, tzinfo=m.timezone.utc)
    s.check("the service day at 03:00 is the night before",
            _at(three_am, m.service_day) == m.date(2026, 8, 31),
            detail=str(_at(three_am, m.service_day)))
    s.check("while a UTC date would have called it the 1st",
            three_am.date() == m.date(2026, 9, 1),
            detail="the two disagreed exactly when the till needed them not to")
    page = _at(three_am, lambda: oc.get("/pos/day").get_data(as_text=True))
    s.check("at 03:00 the page offers the box the close route insists on",
            'name="confirm"' in page,
            detail="the till cannot be closed: refused for a missing tick box "
                   "that was never rendered")

    s.section("Nowhere left asks Greenwich what day it is")
    # The ratchet. Everything above is true today; this is what stops the
    # 126th call site being written next week, the way all 125 were.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_src = io.open(os.path.join(root, "app.py"), encoding="utf-8").read()
    stragglers = app_src.count("datetime.now(timezone.utc).date()")
    s.check("app.py has no UTC calendar dates left", stragglers == 0,
            detail="%d call site(s) -- a day is a date and belongs to the "
                   "house: house_today(), or service_day() if it is the till"
                   % stragglers)
    # And the RIGHT answer spelled longhand, which is how this became two
    # conventions instead of one: 95 sites were already correct, so nothing
    # ever looked wrong, and the other 125 were just as easy to copy.
    longhand = app_src.count("datetime.now(LOCAL_TZ).date()")
    s.check("and only one place decides what today is", longhand == 1,
            detail="%d site(s) spell it out; house_today() is the definition, "
                   "everything else calls it" % longhand)

    _moments(s, conn, app_src)

    _cleanup(conn)
    conn.close()
    return s


# What is left of the same mistake in SQL, by function, and why. Every entry is
# somebody's to mend -- none of them is a way of doing it right. The count is
# how many times the spelling appears there, so a second one added beside a
# known one is still caught. Checked both ways: a new one reds the run, and so
# does one that has been mended and is still on this list.
UTC_DAY_SQL_KNOWN = {
    ("pos_close_period", "date", "created_at"):
        (1, "widened a day each way, then filed by service_day_iso in Python: right"),
    ("pos_close_period", "date", "occurred_at"): (2, "till -- the other agent's"),
    ("pos_close_day", "date", "opened_at"): (1, "till -- the other agent's"),
    ("pos_archive_bundle", "date", "occurred_at"): (1, "till -- the other agent's"),
    ("pos_archive", "strftime", "occurred_at"): (1, "till -- the other agent's"),
    ("menu_engineering", "date", "pos_order_lines.created_at"): (1, "till -- the other agent's"),
    ("service_times", "date", "sent_at"): (2, "till -- the other agent's"),
    ("committed_stock", "date", "booking_extras.created_at"): (1, "stock -- the other agent's"),
    ("supplier_price_changes", "date", "stock_movements.created_at"): (1, "stock -- the other agent's"),
    ("wastage_rate", "date", "stock_movements.created_at"): (1, "stock -- the other agent's"),
    ("supplier_scorecard", "date", "submitted_at"): (2, "supplier invoices -- the other agent's"),
    ("find_duplicate_invoice", "substr", "submitted_at"): (1, "supplier invoices -- the other agent's"),
}

UTC_DAY_SQL = [
    r"\b(date)\(\s*((?:\w+\.)?\w+_at)\s*\)",
    r"\b(strftime)\(\s*'[^']*'\s*,\s*((?:\w+\.)?\w+_at)\s*\)",
    r"\b(substr)\(\s*((?:\w+\.)?\w+_at)\s*,\s*1\s*,\s*10\s*\)",
]

# Summer, far enough ahead that nothing real is on these days. 22:30 UTC is
# 00:30 the next day at the house (UTC+2): the hour the two disagree.
LATE = "2031-07-14T22:30:00+00:00"     # the house's 15 July, UTC's 14th


def _cleanup_moments(conn):
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM refunds WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicle_transfers WHERE vehicle_id IN "
                 "(SELECT id FROM vehicles WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.commit()


def _moments(s, conn, app_src):
    """The database files a moment under the house's day, not Greenwich's."""
    import re
    from datetime import date, timedelta

    _cleanup_moments(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    s.section("The database is asked the house's day too")
    s.check("the stamp these checks use is a different day in each clock",
            LATE[:10] == "2031-07-14" and m.house_date_iso(LATE) == "2031-07-15",
            detail=f"UTC {LATE[:10]}, house {m.house_date_iso(LATE)}")
    first, last = m.house_day_window("2031-07-15")
    s.check("a house day starts at its own midnight",
            first == "2031-07-14T22:00:00+00:00" and last == "2031-07-15T22:00:00+00:00",
            detail=f"{first} .. {last}")
    s.check("house_moment is where a house day begins, and leaves a moment alone",
            m.house_moment("2031-07-15") == first
            and m.house_moment(date(2031, 7, 15)) == first
            and m.house_moment(LATE) == LATE,
            detail=f"{m.house_moment('2031-07-15')}, {m.house_moment(LATE)}")
    w = m.house_day_window("2031-10-26")      # the clocks go back that night
    s.check("and a night the clocks change is 25 hours, not a gap",
            w == ("2031-10-25T22:00:00+00:00", "2031-10-26T23:00:00+00:00"), detail=str(w))

    # A transfer at half past midnight, counted against the leave it falls in.
    van = conn.execute("INSERT INTO vehicles (name, created_at) VALUES (?, ?)",
                       (TAG + " van", now)).lastrowid
    conn.execute("INSERT INTO vehicle_transfers (vehicle_id, direction, scheduled_at, "
                 "created_at) VALUES (?, 'pickup', ?, ?)", (van, LATE, now))
    conn.commit()
    on_15 = m.leave_impact(conn, "2031-07-15", "2031-07-15")["notes"]
    on_14 = m.leave_impact(conn, "2031-07-14", "2031-07-14")["notes"]
    s.check("a pickup at 00:30 counts on the day it happens at the house",
            any("1 transfer" in n for n in on_15) and not any("transfer" in n for n in on_14),
            detail=f"15th: {on_15}; 14th: {on_14}")

    # A booking made at half past midnight is on the books from that day.
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'G', 'g@example.invalid', '2031-07-20', '2031-07-22', 2,
                   'confirmed', 500, ?)""",
        (room, TAG + "-B", TAG + "-Btok", LATE))
    booking = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (TAG + "-B",)).fetchone()["id"]
    conn.commit()
    stays = lambda day: m.booking_pace(conn, months=1, today=day)["rows"][0]["now"]["stays"]
    s.check("a booking made at 00:30 is on the books as at that day, not the one before",
            stays(date(2031, 7, 15)) - stays(date(2031, 7, 14)) == 1,
            detail=f"as at the 14th {stays(date(2031, 7, 14))}, "
                   f"the 15th {stays(date(2031, 7, 15))}")

    # An extra sold at half past midnight, on the VAT working.
    conn.execute(
        """INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity,
           status, created_at) VALUES ('room', ?, ?, 1234.5, 1, 'confirmed', ?)""",
        (booking, TAG + " extra", LATE))
    conn.commit()
    extras = lambda a, b: sum(l["gross"] or 0 for l in m.vat_working(conn, a, b)["lines"]
                              if l["source"] == "Extras")
    s.check("an extra sold at 00:30 is on that day's VAT working",
            abs(extras(date(2031, 7, 15), date(2031, 7, 16)) - 1234.5) < 0.01
            and extras(date(2031, 7, 14), date(2031, 7, 15)) == 0,
            detail=f"15th {extras(date(2031, 7, 15), date(2031, 7, 16))}, "
                   f"14th {extras(date(2031, 7, 14), date(2031, 7, 15))}")

    # A refund at half past midnight on the 1st belongs to the new month --
    # in the summary and in the chart above it, which must agree.
    conn.execute(
        """INSERT INTO refunds (category, booking_id, amount, reason, method, created_at)
           VALUES ('room', ?, 77, ?, 'cash', '2031-06-30T22:30:00+00:00')""",
        (booking, TAG + " refund"))
    conn.commit()
    july = m.financial_month_summary(conn, date(2031, 7, 1), date(2031, 8, 1))
    june = m.financial_month_summary(conn, date(2031, 6, 1), date(2031, 7, 1))
    s.check("a refund at 00:30 on the 1st is the new month's",
            abs(july["refunds_total"] - 77) < 0.01 and june["refunds_total"] == 0,
            detail=f"July {july['refunds_total']}, June {june['refunds_total']}")
    trend = {b["month"].strftime("%Y-%m"): b for b in
             m.financial_trend(conn, 2, today=date(2031, 7, 15))}
    s.check("and the chart files it in the same month as the summary",
            abs(trend["2031-07"]["revenue"] - july["revenue"]) < 0.01
            and abs(trend["2031-06"]["revenue"] - june["revenue"]) < 0.01,
            detail=f"chart July {trend['2031-07']['revenue']} vs {july['revenue']}; "
                   f"June {trend['2031-06']['revenue']} vs {june['revenue']}")

    # Older than five days at the house, for the owner's queue.
    before = m.owner_queue_totals(conn, date(2031, 7, 20))[2]
    conn.execute("INSERT INTO expenses (kind, description, amount, status, submitted_at) "
                 "VALUES ('staff_expense', ?, 12, 'pending', ?)", (TAG + " taxi", LATE))
    conn.commit()
    after = m.owner_queue_totals(conn, date(2031, 7, 20))[2]
    s.check("a claim made at 00:30 on the cutoff day is not yet five days old",
            after == before, detail=f"old went {before} -> {after}")

    # The privacy purge, on a request that named no date of its own.
    def enquiry(ref, filed):
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
               contact_name, contact_email, status, created_at)
               VALUES (?, ?, 'wedding', 'Z', 'z@example.invalid', 'new', ?)""",
            (TAG + ref, TAG + ref + "tok", filed))
    # Cutoff 15 January 2001, winter (UTC+1): the house's day begins at 23:00 UTC.
    enquiry("-keep", "2001-01-14T23:30:00+00:00")   # 00:30 on the 15th here
    enquiry("-gone", "2001-01-14T21:30:00+00:00")   # 22:30 on the 14th here
    conn.commit()
    # Inside a request, as the automation loop runs it: it writes the audit
    # trail, which reads the session.
    with m.app.test_request_context("/"):
        m.purge_dead_enquiries(conn, today=date(2002, 1, 15))
    left = {r["reference_code"] for r in conn.execute(
        "SELECT reference_code FROM event_inquiries WHERE reference_code LIKE ?",
        (TAG + "%",)).fetchall()}
    s.check("the purge keeps a request filed at 00:30 on the cutoff day",
            TAG + "-keep" in left,
            detail="SUBSTR(created_at, 1, 10) called it the 14th, a day past the "
                   "twelve months the privacy notice promises")
    s.check("and removes one filed the evening before", TAG + "-gone" not in left)

    # A shift started at half past midnight on the 1st is the NEW month's --
    # in the one definition of hours worked, and on the payroll rows the
    # accountant is sent. A bare date had it in the month before.
    emp = conn.execute("SELECT id FROM users WHERE role = 'employee' "
                       "AND status = 'active' ORDER BY id LIMIT 1").fetchone()
    s.check("there is an employee to clock in", emp is not None)
    if emp:
        conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) "
                     "VALUES (?, '2031-07-31T22:30:00+00:00', '2031-08-01T02:30:00+00:00')",
                     (emp["id"],))
        conn.commit()

        def hours(first, after):
            row = next((r for r in m.labour_hours_by_person(conn, first, after)
                        if r["id"] == emp["id"]), None)
            return round(row["hours"], 2) if row else 0.0

        s.check("a shift from 00:30 on the 1st is worked in the new month",
                hours("2031-08-01", "2031-09-01") == 4.0
                and hours("2031-07-01", "2031-08-01") == 0.0,
                detail=f"August {hours('2031-08-01', '2031-09-01')}, "
                       f"July {hours('2031-07-01', '2031-08-01')}")
        august = m.resolve_period("month", "2031-08-01", today=date(2031, 8, 15))
        row = next((r for r in m.payroll_period_rows(conn, august)
                    if r["user_id"] == emp["id"]), None)
        s.check("and paid with it on the payroll rows",
                row is not None and row["shifts"] >= 1 and row["hours"] >= 4.0,
                detail=str({k: row[k] for k in ("hours", "shifts")}) if row else "no row")
        conn.execute("DELETE FROM time_entries WHERE user_id = ? AND clock_in_at LIKE '2031-%'",
                     (emp["id"],))
        conn.commit()

    s.section("Nor does the database spell it the old way")
    found, current = {}, None
    for line in app_src.splitlines():
        if line.startswith("def "):
            current = line[4:line.index("(")]
        if line.lstrip().startswith("#"):
            continue
        for pattern in UTC_DAY_SQL:
            for fn, col in re.findall(pattern, line, re.IGNORECASE):
                key = (current, fn.lower(), col.lower())
                found[key] = found.get(key, 0) + 1
    new = sorted(f"{k[0]}: {k[1]}({k[2]}) x{n}" for k, n in found.items()
                 if n > UTC_DAY_SQL_KNOWN.get(k, (0, ""))[0])
    mended = sorted(f"{k[0]}: {k[1]}({k[2]})" for k, (n, _why) in UTC_DAY_SQL_KNOWN.items()
                    if found.get(k, 0) < n)
    s.check("no query reads a stored moment as a UTC day", not new,
            detail=("; ".join(new) + " -- compare the stored string against "
                    "house_day_window(), or service_day_window() for the till")
            if new else "")
    s.check("and the list of what is left is current", not mended,
            detail=("mended, still listed: " + "; ".join(mended)) if mended else "")
    _cleanup_moments(conn)


if __name__ == "__main__":
    print(run().report())
