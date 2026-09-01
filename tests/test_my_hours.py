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
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZMYH"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM time_entries WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM wage_records WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()



def _person(name):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


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
    s.check("it loads for an employee", page.status_code == 200, page)
    # 8 + 5 = 13, less a 30 minute break = 12.5
    s.check("hours are net of breaks", "12.5" in html,
            detail="a 30 minute break was not taken off")
    s.check("and it is labelled as hours", "Hours" in html)

    s.section("It is not a payslip, and says so")
    s.check("the page says it plainly", "not a payslip" in html.lower(),
            detail="a page of pay figures that reads as a payslip is a page "
                   "somebody will hold you to")

    s.section("With no rate on file, the hours still count")
    s.check("it says there is no rate rather than showing zero",
            "no rate on file" in html.lower(),
            detail="an employee with no wage recorded was shown €0.00, which "
                   "reads as 'you earned nothing'")
    s.check("and the hours are still there", "12.5" in html)

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
    s.check("and it says the figure is gross", "gross" in html.lower())

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
    s.check("and their hours are not in the total", "21.5" not in html,
            detail="somebody else's 9 hours were added to this person's total")

    s.section("Signed out, it is not reachable")
    anon = m.app.test_client()
    r = anon.get("/hours/mine")
    s.check("a logged-out browser is sent to the login",
            r.status_code in (302, 401, 403), detail=f"HTTP {r.status_code}")

    _cleanup()
    return s
