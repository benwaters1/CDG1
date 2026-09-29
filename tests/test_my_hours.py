"""What an employee can see about their own hours and pay.

Somebody could see the shifts they were given and clock in and out of them, and
had no way at all to see what that came to. The number existed — payroll reads
it every month — but only the owner could look at it, so the first time an
employee saw a figure was on a payslip they had no way to check.

Three things worth pinning:

  - IT IS THE SAME NUMBER THE OWNER SEES. The page reads labour_cost_breakdown,
    the helper payroll and the financials use. Two definitions of "hours
    worked" is how an employee and a payslip come to disagree, and the employee
    is the one who cannot audit it.

  - GROSS ONLY. Employer contributions are what employing somebody costs the
    house, not part of anybody's wage. Showing them here would inflate what a
    person believes they earn by about forty per cent.

  - IT IS NOT A PAYSLIP AND SAYS SO. Hours can still be corrected and the rate
    is what the owner recorded, not what a payroll bureau will calculate.

Plus the obvious one: nobody sees anybody else's.

TWO FAULTS THAT A GREEN RUN HID, found on a page this suite rendered:

  - THE TOTAL READ 0.0 UNDER SHIFTS OF 4.50 AND 8.00, for anybody with no
    rate on file. The costing leaves an unpriced person out of its rows, and
    the page read its hours from those rows alone. The checks here said
    "12.5" in html, and it always was: the nav's icons are drawn with path
    data like "M12.5 11.3h1" on every page. So the figures are now read out
    of the foot row and the band, where a person reads them.

  - STARTED PRINTED THE RAW UTC MOMENT, 2026-09-04T09:00:00+00:00, because
    date_short reads a date and handed anything else back untouched. The day
    a shift belongs to is the house's: clocked in at half past midnight here
    is the previous evening in UTC.
"""
import re
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZMYH"


def _foot_total(html):
    """The figure in the foot row, read from the row itself."""
    mt = re.search(r"Total, net of breaks</th>\s*<th[^>]*>\s*([^<]*?)\s*</th>", html)
    return mt.group(1) if mt else None


def _band(html, label):
    """One figure from the overview band, by the label under it."""
    # Tempered so a match cannot run on from one cell into the next.
    mt = re.search(r'<div class="overview-value">((?:(?!overview-value).)*?)</div>\s*'
                   r'<div class="overview-label">' + re.escape(label) + r"</div>",
                   html, re.S)
    if not mt:
        return None
    words = visible_text(mt.group(1)).split()
    return words[0] if words else ""


def _rows(html):
    """[(started, finished, hours)] as each row of the table reads."""
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", body.group(1) if body else "", re.S):
        cells = [visible_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) == 4:
            out.append((cells[0], cells[1], cells[3]))
    return out


def _summed(rows):
    return round(sum(float(h) for _s, _f, h in rows if h not in ("", "—")), 2)


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM breaks WHERE time_entry_id IN
                    (SELECT time_entries.id FROM time_entries
                       JOIN users ON users.id = time_entries.user_id
                      WHERE users.name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM time_entries WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM wage_records WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()



def _person(name, email_key=None):
    conn = db()
    cur = conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (f"{TAG} {name}",
         f"{TAG.lower()}.{(email_key or name).lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    # By the row just written, not by name: two of these share one on purpose.
    row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return row


def _shift_at(user_id, start, hours):
    """A shift from an exact moment, for the ones whose hour is the point."""
    conn = db()
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?,?,?)",
                 (user_id, start.astimezone(timezone.utc).isoformat(),
                  (start + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _local(day, hour, minute=0):
    """A wall-clock time at the house, as an aware datetime."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=m.LOCAL_TZ)


def _shift(user_id, day, hours, break_minutes=0):
    conn = db()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?,?,?)",
                 (user_id, start.isoformat(), (start + timedelta(hours=hours)).isoformat()))
    entry_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    if break_minutes:
        conn.execute(
            "INSERT INTO breaks (time_entry_id, start_at, end_at) VALUES (?,?,?)",
            (entry_id, (start + timedelta(hours=1)).isoformat(),
             (start + timedelta(hours=1, minutes=break_minutes)).isoformat()))
    conn.commit()
    conn.close()
    return entry_id


def _open_shift(user_id, day):
    conn = db()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at) VALUES (?,?)",
                 (user_id, start.isoformat()))
    conn.commit()
    conn.close()


def _as(user_id):
    """A client signed in as one specific person.

    Deliberately NOT the harness's shared employee: several other suites clock
    that person in and out, so any exact total asserted against them holds
    alone and fails in a full run. The bug is then in the test, and it looks
    like the page.
    """
    client = m.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return client


def run():
    s = Suite("My hours")
    _cleanup()
    oc, _shared, owner, _shared_emp = clients()
    emp = _person("Mine")
    ec = _as(emp["id"])
    today = house_today()
    month_start = today.replace(day=1)
    when = month_start + timedelta(days=2)

    s.section("The page shows this period's hours")
    _shift(emp["id"], when, 8)
    _shift(emp["id"], when + timedelta(days=1), 5, break_minutes=30)
    page = ec.get("/hours/mine")
    html = page.get_data(as_text=True)
    rows = _rows(html)
    s.check("it loads for an employee", page.status_code == 200, page)
    # 8 + 5 = 13, less a 30 minute break = 12.5
    s.check("each shift is a row, net of its break",
            sorted(h for _s, _f, h in rows) == ["4.50", "8.00"],
            detail=f"rows read {rows} -- a 30 minute break was not taken off")
    s.check("and the band has a cell for the hours", _band(html, "Hours") is not None,
            detail="no overview cell labelled Hours")

    s.section("It is not a payslip, and says so")
    s.check("the page says it plainly", "not a payslip" in html.lower(),
            detail="a page of pay figures that reads as a payslip is a page "
                   "somebody will hold you to")

    s.section("With no rate on file, the hours still count")
    # Nobody has recorded a wage for this person yet, which is where the
    # total fell to 0.0: the costing leaves them out of its rows, and those
    # rows were the only place the page looked for hours.
    s.check("it says there is no rate rather than showing zero",
            "no rate on file" in html.lower(),
            detail="an employee with no wage recorded was shown €0.00, which "
                   "reads as 'you earned nothing'")
    s.check("the foot row is the total of the rows above it",
            _foot_total(html) == "12.5" and _summed(rows) == 12.5,
            detail=f"foot row reads {_foot_total(html)!r} under rows of {rows}")
    s.check("the band says the same hours", _band(html, "Hours") == "12.5",
            detail=f"band reads {_band(html, 'Hours')!r}")
    s.check("and counts both shifts", _band(html, "Shifts") == "2",
            detail=f"band reads {_band(html, 'Shifts')!r}")
    s.check("only the money is missing", _band(html, "At your rate") == "—",
            detail=f"band reads {_band(html, 'At your rate')!r}")
    conn = db()
    with m.app.test_request_context("/hours/mine"):
        period = m.period_from_request()
    unpriced = m.labour_cost_breakdown(conn, period["start_iso"], period["end_iso"])
    conn.close()
    kept = next((r for r in unpriced["unpriced_rows"] if r["user_id"] == emp["id"]), None)
    s.check("and the owner's costing still has their hours",
            kept is not None and kept["hours"] == 12.5 and kept["shifts"] == 2,
            detail=f"unpriced_rows holds {kept} -- the hours of somebody with no "
                   "rate went nowhere at all")
    s.check("without costing them at zero",
            not any(r["user_id"] == emp["id"] for r in unpriced["rows"]),
            detail="an unpriced person appeared among the costed rows")

    s.section("With a rate, it shows what that comes to")
    conn = db()
    conn.execute("""INSERT INTO wage_records (user_id, effective_from, basis,
                    gross_amount, created_at) VALUES (?, ?, 'hourly', 14.0, ?)""",
                 (emp["id"], (month_start - timedelta(days=400)).isoformat(),
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    html = ec.get("/hours/mine").get_data(as_text=True)
    s.check("12.5 hours at 14.00 is 175.00", "175.00" in html,
            detail="the figure does not match the rate on file")
    s.check("and the band carries it", _band(html, "At your rate") == m.euro(175.0),
            detail=f"band reads {_band(html, 'At your rate')!r}")
    s.check("and it says the figure is gross", "gross" in html.lower())
    s.check("the hours did not move when the rate arrived",
            _foot_total(html) == "12.5" and _band(html, "Hours") == "12.5",
            detail=f"foot {_foot_total(html)!r}, band {_band(html, 'Hours')!r}")
    s.check("and it no longer says there is no rate",
            "no rate on file" not in html.lower(),
            detail="the rate is on file and the page still said it was not")

    s.section("Employer contributions are not shown as the employee's pay")
    # They are the house's cost, not this person's wage. Adding them here would
    # inflate what somebody believes they earn by the contribution rate.
    conn = db()
    conn.execute("""INSERT INTO app_settings (key, value)
                    VALUES ('payroll_employer_contribution_percent', '42')
                    ON CONFLICT(key) DO UPDATE SET value = '42'""")
    conn.commit()
    conn.close()
    html = ec.get("/hours/mine").get_data(as_text=True)
    s.check("the figure is still the gross 175.00", "175.00" in html,
            detail="employer contributions leaked into the employee's own figure")
    s.check("and gross-plus-42% appears nowhere",
            "248.50" not in html and "€248" not in html,
            detail="gross plus 42% was shown to the employee as their pay")
    conn = db()
    conn.execute("DELETE FROM app_settings WHERE key = 'payroll_employer_contribution_percent'")
    conn.commit()
    conn.close()

    s.section("It agrees with what the owner sees")
    # One definition. Two is how a payslip and an employee come to disagree.
    conn = db()
    period = None
    with m.app.test_request_context("/hours/mine"):
        period = m.period_from_request()
    breakdown = m.labour_cost_breakdown(conn, period["start_iso"], period["end_iso"])
    conn.close()
    theirs = next((r for r in breakdown["rows"] if r["user_id"] == emp["id"]), None)
    s.check("the owner's costing has the same hours",
            theirs and abs(theirs["hours"] - 12.5) < 0.05,
            detail=f"owner sees {theirs['hours'] if theirs else None}, page says 12.5")
    s.check("and the same gross", theirs and abs(theirs["gross"] - 175.0) < 0.01,
            detail=f"owner sees {theirs['gross'] if theirs else None}")

    s.section("A clocking that cannot be right is flagged, not hidden")
    _open_shift(emp["id"], when + timedelta(days=3))
    html = ec.get("/hours/mine").get_data(as_text=True)
    s.check("an open shift is called out", "still open" in html.lower(),
            detail="a shift with no clock-out silently made the total wrong")
    s.check("and the page says it needs a look", "needs a look" in html.lower()
            or "need a look" in html.lower(), detail="nothing told them to check it")

    s.section("Nobody sees anybody else's")
    other = _person("Other")
    _shift(other["id"], when, 9)
    html = ec.get("/hours/mine").get_data(as_text=True)
    s.check("the other person's name is not on the page",
            TAG + " Other" not in html,
            detail="one employee could read another's hours")
    s.check("and their hours are not in the total",
            _foot_total(html) == "12.5" and _band(html, "Hours") == "12.5",
            detail=f"foot {_foot_total(html)!r}, band {_band(html, 'Hours')!r} -- "
                   "somebody else's 9 hours reached this person's total")
    s.check("nor their shift in the rows", len(_rows(html)) == 3,
            detail=f"{_rows(html)} -- two shifts and the open one are this person's")

    s.section("A shift is filed under the house's day, not UTC's")
    # Clocked in at half past midnight on the 6th, here. In UTC that is the
    # evening of the 5th, and the column printed the stamp as it was stored.
    night = _person("Night")
    nc = _as(night["id"])
    sixth = month_start + timedelta(days=5)
    _shift_at(night["id"], _local(sixth, 0, 30), 3)
    stamp = _local(sixth, 0, 30).astimezone(timezone.utc)
    html = nc.get("/hours/mine").get_data(as_text=True)
    rows = _rows(html)
    started = rows[0][0] if rows else ""
    s.check("the fixture is the case: UTC puts it on the day before",
            stamp.date() == sixth - timedelta(days=1),
            detail=f"{stamp.isoformat()} -- without this the check below passes "
                   "on a slice as readily as on the house's day")
    s.check("the row says the house's day",
            started.startswith(m.format_date_short(sixth.isoformat())),
            detail=f"Started reads {started!r}, wanted "
                   f"{m.format_date_short(sixth.isoformat())!r}")
    s.check("at the house's time", "00:30" in started, detail=started)
    s.check("and not the day UTC was in",
            m.format_date_short((sixth - timedelta(days=1)).isoformat()) not in started,
            detail=started)
    s.check("and no raw stamp anywhere in the table",
            not any("+00:00" in c or re.search(r"\d{4}-\d\d-\d\dT", c)
                    for row in rows for c in row),
            detail=str(rows))

    s.section("A shift that runs past midnight says which day it ended")
    late = _person("Late")
    lc = _as(late["id"])
    ninth = month_start + timedelta(days=8)
    _shift_at(late["id"], _local(ninth, 22, 0), 4)
    rows = _rows(lc.get("/hours/mine").get_data(as_text=True))
    finished = rows[0][1] if rows else ""
    next_day = m.format_date_short((ninth + timedelta(days=1)).isoformat())
    s.check("it finished at 02:00, the next day, and says so",
            "02:00" in finished and next_day in finished,
            detail=f"Finished reads {finished!r}, wanted 02:00 and {next_day!r}")
    same_day = _rows(nc.get("/hours/mine").get_data(as_text=True))
    s.check("and a shift inside one day does not repeat its date",
            bool(same_day) and same_day[0][1] == "03:30",
            detail=f"Finished reads {same_day[0][1] if same_day else None!r} -- a date "
                   "beside every finish reads as if each shift ran over")

    s.section("Two people with one name are two people")
    # Whether somebody had a rate was decided by matching their NAME against
    # the costing's list, so one person's missing wage was announced to
    # another who shares the name -- and hid their own figure from them.
    twin_paid = _person("Twin", email_key="twin.paid")
    twin_bare = _person("Twin", email_key="twin.bare")
    _shift(twin_paid["id"], when, 6)
    _shift(twin_bare["id"], when, 7)
    conn = db()
    conn.execute("""INSERT INTO wage_records (user_id, effective_from, basis,
                    gross_amount, created_at) VALUES (?, ?, 'hourly', 10.0, ?)""",
                 (twin_paid["id"], (month_start - timedelta(days=400)).isoformat(),
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    paid = _as(twin_paid["id"]).get("/hours/mine").get_data(as_text=True)
    bare = _as(twin_bare["id"]).get("/hours/mine").get_data(as_text=True)
    s.check("the one with a rate is not told they have none",
            "no rate on file" not in paid.lower() and "60.00" in paid,
            detail="the other's missing wage was announced on this page")
    s.check("and the one without is",
            "no rate on file" in bare.lower() and _foot_total(bare) == "7.0",
            detail=f"foot {_foot_total(bare)!r}")

    s.section("The owner's own clocked time adds up too")
    # Drawings rather than a wage, so it is in no costing -- and the foot row
    # read 0.0 under the owner's own shifts for the same reason as above.
    conn = db()
    cur = conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?,?,?)",
        (owner["id"], _local(month_start + timedelta(days=11), 9).astimezone(timezone.utc).isoformat(),
         _local(month_start + timedelta(days=11), 13).astimezone(timezone.utc).isoformat()))
    owner_entry = cur.lastrowid
    conn.commit()
    conn.close()
    try:
        html = oc.get("/hours/mine").get_data(as_text=True)
        rows = _rows(html)
        foot = _foot_total(html)
        s.check("their shift is on the page", any(h == "4.00" for _s, _f, h in rows),
                detail=str(rows[:6]))
        # The sum, not a fixed figure: other suites clock the shared owner in
        # and out, so the rows are whatever they are. Rows at two places and
        # the total at one can differ by the rounding and no more.
        s.check("and the foot row is the sum of the rows, not 0.0",
                foot is not None and abs(float(foot) - _summed(rows)) < 0.051,
                detail=f"foot {foot!r} under rows summing to {_summed(rows)}")
    finally:
        conn = db()
        conn.execute("DELETE FROM time_entries WHERE id = ?", (owner_entry,))
        conn.commit()
        conn.close()

    s.section("Signed out, it is not reachable")
    anon = m.app.test_client()
    r = anon.get("/hours/mine")
    s.check("a logged-out browser is sent to the login",
            r.status_code in (302, 401, 403), detail=f"HTTP {r.status_code}")

    _cleanup()
    return s
