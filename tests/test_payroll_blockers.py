"""The payroll pack, and what it refuses to send.

payroll_period_rows exists to hand figures to whoever actually runs payroll,
and its docstring is explicit about the standard: an unsafe number is reported
per person and the export refuses "rather than quietly sending a wrong number
to the accountant". It caught two things — a shift ending before it starts, and
a missing hourly rate — and none of it was tested.

It missed the case that actually happens. Somebody forgets to clock out. The
app already notices: on their next login the entry is closed and stamped
`auto_closed = 1`, and the timesheet page labels it "auto-closed — forgot to
log out?". Payroll never looked at that flag. A shift left open on Friday and
closed on Monday counted as 72 hours of continuous work and priced itself at
EUR 864, with an empty blocker list and a clean export.

That is the exact failure the function was written to prevent, in the direction
that costs money, caused by the most ordinary mistake there is.

A shift never closed at all was the mirror of it: worth zero hours, no warning,
so somebody is quietly underpaid instead.

Both are blockers now. Both are clearable — repair_time_entry sets a real
clock-out or voids the entry and clears the flag — but the repair form was only
rendered for impossible shifts, so blocking on auto-closed without offering the
form would have produced the "alert pointing at a page with no way to act"
problem that route's own docstring says it was written to fix. The form is
offered for all three now.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZPAY"
period_iso = []          # filled by _pack(), so the cross-check uses the same window


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE name LIKE ?", (TAG + "%",)).fetchall()]
    for uid in ids:
        conn.execute("""DELETE FROM breaks WHERE time_entry_id IN
                        (SELECT id FROM time_entries WHERE user_id = ?)""", (uid,))
        conn.execute("DELETE FROM time_entries WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM wage_records WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _employee(name, rate="12.00", pay_type="hourly", status="active"):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, role, status, job_role, pay_rate, pay_type,
           password_hash, created_at)
           VALUES (?, ?, 'employee', ?, 'Housekeeping', ?, ?, 'x', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid", status,
         rate, pay_type, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _wage(user_id, basis, gross):
    """A typed wage record in force from the start of last month."""
    conn = db()
    first = m.house_today().replace(day=1) - timedelta(days=1)
    conn.execute(
        """INSERT INTO wage_records (user_id, effective_from, basis, gross_amount,
           created_at) VALUES (?, ?, ?, ?, ?)""",
        (user_id, first.replace(day=1).isoformat(), basis, gross,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _shift(user_id, start, end, auto_closed=0, break_minutes=0):
    """A time entry inside the current month. end=None leaves it open."""
    conn = db()
    conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) "
        "VALUES (?, ?, ?, ?)",
        (user_id, start.isoformat(), end.isoformat() if end else None, auto_closed))
    conn.commit()
    entry_id = conn.execute(
        "SELECT id FROM time_entries WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)).fetchone()["id"]
    if break_minutes:
        conn.execute(
            "INSERT INTO breaks (time_entry_id, start_at, end_at) VALUES (?, ?, ?)",
            (entry_id, start.isoformat(),
             (start + timedelta(minutes=break_minutes)).isoformat()))
        conn.commit()
    conn.close()
    return entry_id


def _pack():
    """The payroll rows for this month, ours only."""
    conn = db()
    try:
        with m.app.test_request_context("/admin/payroll?period=month"):
            period = m.period_from_request()
        period_iso[:] = [period["start_iso"], period["end_iso"]]
        return {r["name"]: r for r in m.payroll_period_rows(conn, period)
                if r["name"].startswith(TAG)}
    finally:
        conn.close()


def _anchor():
    """Mid-morning, already past, and inside the current month.

    All three conditions matter and the old version dropped the second:

        base.replace(day=min(max(now.day, 2), 26))

    It lands in the FUTURE in two separate ways. On the first of a month
    the clamp to 2 makes it tomorrow. And on any day, `hour=9` is ahead of
    the clock until nine in the morning. Sampled across a month at five
    times of day, 55 of 155 combinations produced a date that had not
    happened yet.

    Every entry the suite built then sat in the future, the timesheet
    page's fourteen-day window ends today, and the repair forms this suite
    checks for were not on it. It went red on a morning run or on a first,
    and green the rest of the time — rare enough, and arbitrary enough, to
    read as a flake rather than as a bug.

    Staying inside the month is deliberate and not negotiable — the pack is
    drawn per period, and a scenario split across two of them is testing
    something else. So when nine in the morning has not happened yet, this
    steps back within the day rather than into the previous month.
    """
    now = datetime.now(timezone.utc)
    base = now.replace(day=min(now.day, 26), hour=9,
                       minute=0, second=0, microsecond=0)
    if base > now:
        base = now.replace(hour=0, minute=1, second=0, microsecond=0)
    return base


def run():
    s = Suite("Payroll blockers")
    _cleanup()
    oc, ec, owner, emp = clients()
    day = _anchor()

    s.section("An ordinary week goes through")
    fine = _employee("Nadine")
    _shift(fine["id"], day, day + timedelta(hours=8))
    _shift(fine["id"], day + timedelta(days=1), day + timedelta(days=1, hours=8),
           break_minutes=30)
    row = _pack().get(fine["name"])
    s.check("their hours are counted", row is not None and row["hours"] == 15.5,
            detail=f"got {row['hours'] if row else None} (expected 8 + 7.5 after a 30m break)")
    s.check("both shifts are counted", row["shifts"] == 2, detail=f"got {row['shifts']}")
    s.check("and it is priced", row["cost"] == 186.0, detail=f"got {row['cost']}")
    s.check("with nothing blocking it", not row["blockers"], detail=f"{row['blockers']}")

    s.section("A shift that ends before it starts")
    backwards = _employee("Olivier")
    _shift(backwards["id"], day + timedelta(hours=8), day)
    b = _pack()[backwards["name"]]
    s.check("it contributes no hours", b["hours"] == 0, detail=f"got {b['hours']}")
    s.check("and is reported", any("impossible" in x for x in b["blockers"]),
            detail=f"{b['blockers']}")

    s.section("A shift nobody clocked out of, closed automatically days later")
    # The one that actually happens. Clocked in Friday, next login Monday: the
    # app closes it and stamps auto_closed, and the timesheet page says so.
    forgot = _employee("Pascal")
    _shift(forgot["id"], day, day + timedelta(days=3), auto_closed=1)
    f = _pack()[forgot["name"]]
    s.check("it is reported rather than paid",
            any("auto-closed" in x or "forgot" in x for x in f["blockers"]),
            detail=f"{f['hours']}h priced at {f['cost']} with blockers {f['blockers']}")
    # The hours stay in the figure on purpose -- labour_hours_by_person is the
    # one definition of hours worked, and filtering only here would put two
    # different numbers on the same shifts. What must not happen is those hours
    # being SENT, so the blocker has to say why they cannot be trusted.
    s.check("and the reason is stated, not just the number",
            any("guess" in x or "clocked out" in x for x in f["blockers"]),
            detail=f"{f['blockers']} — a bare figure with no explanation reads as real")

    s.section("A shift still running when payroll is drawn")
    open_one = _employee("Quentin")
    _shift(open_one["id"], day, None)
    q = _pack()[open_one["name"]]
    s.check("it is reported rather than silently worth nothing",
            any("clock" in x.lower() or "open" in x.lower() for x in q["blockers"]),
            detail=f"{q['hours']}h with blockers {q['blockers']}")

    s.section("Hours with no usable rate on file")
    unpriced = _employee("Roland", rate="ask Ben", pay_type="hourly")
    _shift(unpriced["id"], day, day + timedelta(hours=6))
    u = _pack()[unpriced["name"]]
    s.check("the hours are still counted", u["hours"] == 6.0, detail=f"got {u['hours']}")
    s.check("but the missing rate is reported",
            any("rate" in x for x in u["blockers"]), detail=f"{u['blockers']}")

    s.section("Nobody is nagged about a week they did not work")
    idle = _employee("Sabine", rate="", pay_type="")
    i = _pack()[idle["name"]]
    s.check("no hours and no rate raises nothing",
            i["hours"] == 0 and not i["blockers"], detail=f"{i['blockers']}")

    s.section("Somebody who has left is not in the pack")
    gone = _employee("Thierry", status="inactive")
    _shift(gone["id"], day, day + timedelta(hours=8))
    s.check("an inactive employee is left out", gone["name"] not in _pack())

    s.section("The export refuses while anything is unsafe")
    r = oc.get("/admin/payroll/export.csv", follow_redirects=True)
    said = " ".join(flashes(r)).lower()
    s.check("no CSV is produced",
            "text/csv" not in r.headers.get("Content-Type", ""),
            detail=f"content type {r.headers.get('Content-Type')!r}")
    s.check("and it says who to fix first", "fix these before exporting" in said,
            detail=f"flash was {said!r}")

    s.section("The page offers a way to fix each one")
    # Blocking on something the owner cannot act on from the page is the
    # failure repair_time_entry was written to remove.
    page = oc.get("/admin/timesheets")
    html = page.get_data(as_text=True)
    s.check("the timesheet page loads", page.status_code == 200, page)
    forms = html.count("/repair")
    s.check("a repair form is offered for more than just the backwards shift",
            forms >= 3, detail=f"{forms} repair form(s) for 3 broken entries")

    s.section("A blocker outside the viewed fortnight is still offered")
    # Date-independent on purpose. The check above happens to place its entries
    # outside the default window on some days and inside it on others, so on its
    # own it guards this only about one day in fourteen. Here the window is named
    # explicitly and the entry is deliberately outside it: the page defaults to a
    # fortnight, the payroll pack covers a month, and an entry from the 2nd
    # blocks the export on the 20th. Being told "fix these before exporting" and
    # sent to a page the entry is not on is the whole failure.
    old_day = datetime.now(timezone.utc).replace(
        hour=9, minute=0, second=0, microsecond=0) - timedelta(days=90)
    stale = _employee("Perrine")
    _shift(stale["id"], old_day, None)
    conn = db()
    stale_id = conn.execute(
        "SELECT id FROM time_entries WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (stale["id"],)).fetchone()["id"]
    conn.close()
    recent = (m.house_today() - timedelta(days=6)).isoformat()
    html = oc.get(f"/admin/timesheets?start={recent}").get_data(as_text=True)
    s.check("the ninety-day-old open shift is on the page",
            f"/{stale_id}/repair" in html,
            detail="it blocks the export and the page the owner is sent to does "
                   "not show it, so there is nowhere to fix it")
    s.check("and the window they asked for is still what they got",
            html.count("Perrine") >= 1,
            detail="the blockers replaced the fortnight instead of following it")

    s.section("Once repaired, the pack clears")
    conn = db()
    ids = {n: [r["id"] for r in conn.execute(
        "SELECT id FROM time_entries WHERE user_id = ? ORDER BY id", (u2,)).fetchall()]
        for n, u2 in (("forgot", forgot["id"]), ("open", open_one["id"]),
                      ("backwards", backwards["id"]))}
    conn.close()
    for key in ("forgot", "open", "backwards"):
        oc.post(f"/admin/timesheets/{ids[key][0]}/repair",
                data={"action": "void"}, follow_redirects=True)
    after = _pack()
    still = {n: r["blockers"] for n, r in after.items() if r["blockers"]
             and not n.endswith("Roland")}
    s.check("voiding each one clears its blocker", not still, detail=f"{still}")
    s.check("and the voided shifts are worth nothing rather than deleted",
            after[forgot["name"]]["hours"] == 0
            and after[forgot["name"]]["shifts"] == 0,
            detail=f"{after[forgot['name']]['hours']}h / "
                   f"{after[forgot['name']]['shifts']} shifts")

    s.section("The wage on file is what gets paid")
    # Every person above has a free-text pay note and no wage record, so the
    # whole suite only ever exercised the fallback. The typed wage — the field
    # the wages page exists to fill, and the one CLAUDE.md says is the payroll
    # figure — was never read here at all.
    salaried = _employee("Ursule", rate="", pay_type="monthly")
    _wage(salaried["id"], "monthly", 2500)
    _shift(salaried["id"], day, day + timedelta(hours=8))
    sal = _pack()[salaried["name"]]
    s.check("a salaried employee is priced from their salary",
            sal["cost"] is not None, detail=f"cost was {sal['cost']}")
    s.check("at the salary, not at hours x something",
            sal["cost"] and abs(sal["cost"] - 2500) < 1, detail=f"got {sal['cost']}")
    s.check("and nothing blocks them",
            not sal["blockers"], detail=f"{sal['blockers']}")
    s.check("the row says the figure came from the wage on file",
            sal["cost_source"] == "wage on file", detail=str(sal["cost_source"]))

    # The consequence, and the reason this is worth more than one person's row:
    # a blocker refuses the CSV for EVERYBODY. One correctly-configured salaried
    # employee stopped the whole house exporting.
    s.check("their hours are still counted", sal["hours"] == 8.0,
            detail=f"got {sal['hours']}")

    hourly = _employee("Victoire", rate="", pay_type="hourly")
    _wage(hourly["id"], "hourly", 15)
    _shift(hourly["id"], day, day + timedelta(hours=4))
    hr = _pack()[hourly["name"]]
    s.check("an hourly wage record still prices by the hour",
            hr["cost"] and abs(hr["cost"] - 60) < 0.01, detail=f"got {hr['cost']}")

    # Precedence, stated: the typed wage wins over the note, or the note can
    # silently override the figure somebody deliberately set.
    both = _employee("Wilfrid", rate="9.00", pay_type="hourly")
    _wage(both["id"], "hourly", 20)
    _shift(both["id"], day, day + timedelta(hours=10))
    bo = _pack()[both["name"]]
    s.check("a wage record beats the free-text pay note",
            bo["cost"] and abs(bo["cost"] - 200) < 0.01,
            detail=f"got {bo['cost']} — 90.00 means the note won")

    # And the fallback is labelled as a fallback, so the two are never confused
    # in the same column.
    s.check("a figure read out of the pay note is marked as such",
            _pack()[fine["name"]]["cost_source"] == "estimated from the pay note",
            detail=str(_pack()[fine["name"]]["cost_source"]))

    s.section("One page, one number")
    # The labour report and the payroll pack must not disagree about what one
    # person cost. They had two separate implementations of the arithmetic.
    conn = db()
    breakdown = m.labour_cost_breakdown(conn, period_iso[0], period_iso[1])
    conn.close()
    mine = next((r for r in breakdown["rows"] if r["name"] == salaried["name"]), None)
    s.check("the labour report prices them too", mine is not None)
    s.check("and at the same number as payroll",
            mine and abs(mine["gross"] - sal["cost"]) < 0.01,
            detail=f"labour {mine['gross'] if mine else None} vs payroll {sal['cost']}")

    s.section("The page says which kind of figure each row is")
    # One euro column meaning "hours x rate" on one row and "salary for the
    # window" on the next is how somebody pays a salaried person eight hours.
    html = oc.get("/admin/payroll?period=month").get_data(as_text=True)
    s.check("a wage on file is named as one", "monthly wage on file" in html)
    s.check("and the salary figure says it is not hours x rate",
            "salary for this window" in html)
    s.check("a figure read out of the pay note is flagged on the page",
            "from the pay note" in html)
    # The old remedy link sent the owner to the free-text pay field, which is
    # the one field that must not be used as a payroll figure.
    s.check("the fix offered is to put a real wage on file",
            "put a wage on file" in html)

    s.section("Guards")
    s.check("an employee cannot open the payroll pack",
            ec.get("/admin/payroll").status_code in (302, 403))
    s.check("nor export it",
            ec.get("/admin/payroll/export.csv").status_code in (302, 403))
    s.check("nor repair a timesheet entry",
            ec.post(f"/admin/timesheets/{ids['forgot'][0]}/repair",
                    data={"action": "void"}).status_code in (302, 403))

    _cleanup()
    return s
