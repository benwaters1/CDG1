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


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE name LIKE ?", (TAG + "%",)).fetchall()]
    for uid in ids:
        conn.execute("""DELETE FROM breaks WHERE time_entry_id IN
                        (SELECT id FROM time_entries WHERE user_id = ?)""", (uid,))
        conn.execute("DELETE FROM time_entries WHERE user_id = ?", (uid,))
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
        return {r["name"]: r for r in m.payroll_period_rows(conn, period)
                if r["name"].startswith(TAG)}
    finally:
        conn.close()


def _anchor():
    """Mid-morning a few days ago — safely inside the current month."""
    now = datetime.now(timezone.utc)
    base = now.replace(hour=9, minute=0, second=0, microsecond=0)
    # Keep clear of the month boundary so the whole scenario lands in one period.
    return base.replace(day=min(max(now.day, 2), 26))


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
