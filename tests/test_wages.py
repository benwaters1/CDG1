"""What people are actually paid, as a number the app may do arithmetic with.

`users.pay_rate` and `users.pay_type` are free text and stay that way — they
hold what the contract says, including "SMIC + 5%" and "12,50 €/h net". So the
only costing the app had was `estimated_hourly_cost`, which regexes a number
out of that prose and gives up whenever it cannot. A monthly-salaried chef
therefore contributed exactly nothing to the wage bill, and the total said so
in a footnote nobody reads rather than in the number.

`wage_records` is the typed figure. What this file pins:

  - EFFECTIVE DATING. A rise dated today must not change what last month cost.
    Without this, every historical labour figure silently moves every time
    somebody gets a pay rise, and last year's accounts stop reconciling.

  - MONTHLY IS NOT HOURLY. A salary is apportioned by calendar day, so a quiet
    week does not make somebody cheaper. Costing a salaried person by hours
    would make them free in a week they took as leave.

  - GROSS AND EMPLOYER STAY SEPARATE, and the rows add up to the total. The
    house rule is that every figure states which it is and that lines
    reconcile; a labour report whose rows do not add up is worse than none.

  - UNPRICED IS NEVER ZERO. Somebody with no usable wage is named, not
    silently costed at nothing — that is the failure mode of the old estimate,
    and it always understated.

  - The typed record WINS over the free-text guess, and that is visible on the
    row rather than assumed.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZWAGE"


def _cleanup():
    conn = db()
    # Leftovers here would change the labour figure every other suite reads.
    conn.execute("DELETE FROM wage_records WHERE note LIKE ? OR note IS NULL AND user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%", TAG + "%"))
    conn.execute("""DELETE FROM wage_records WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM time_entries WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key = 'payroll_employer_contribution_percent'")
    conn.commit()
    conn.close()


def _person(name, pay_rate=None, pay_type=None):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, pay_rate, pay_type,
           created_at) VALUES (?, ?, 'x', 'employee', 'active', ?, ?, ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         pay_rate, pay_type, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _shift(user_id, on_day, hours):
    conn = db()
    start = datetime.combine(on_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=9)
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?,?,?)",
                 (user_id, start.isoformat(), (start + timedelta(hours=hours)).isoformat()))
    conn.commit()
    conn.close()


def _wage(user_id, effective_from, basis, amount, employer_rate=None):
    conn = db()
    conn.execute(
        """INSERT INTO wage_records (user_id, effective_from, basis, gross_amount,
           employer_rate, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, effective_from, basis, amount, employer_rate, TAG + " seeded",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _cost(start, end):
    conn = db()
    try:
        return m.labour_cost_breakdown(conn, start, end)
    finally:
        conn.close()


def _row_for(breakdown, user_id):
    return next((r for r in breakdown["rows"] if r["user_id"] == user_id), None)


def run():
    s = Suite("Wages")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("An hourly wage costs the hours at that rate")
    hourly = _person("Hourly")
    _wage(hourly["id"], "2035-01-01", "hourly", 12.00)
    _shift(hourly["id"], date(2035, 3, 10), 8)
    _shift(hourly["id"], date(2035, 3, 11), 5)
    b = _cost("2035-03-01", "2035-04-01")
    row = _row_for(b, hourly["id"])
    s.check("the hours are counted", row and abs(row["hours"] - 13.0) < 0.05,
            detail=f"{row['hours'] if row else None}")
    s.check("13h at 12.00 is 156.00", row and abs(row["gross"] - 156.0) < 0.01,
            detail=f"{row['gross'] if row else None}")
    s.check("and it says the figure came from a wage on file",
            row and row["source"] == "wage on file", detail=f"{row}")

    s.section("A rise does not rewrite what last month cost")
    # The reason these are effective-dated at all. Without it, every past
    # labour figure moves the moment somebody gets a pay rise.
    _wage(hourly["id"], "2035-06-01", "hourly", 20.00)
    again = _row_for(_cost("2035-03-01", "2035-04-01"), hourly["id"])
    s.check("March is still 156.00", again and abs(again["gross"] - 156.0) < 0.01,
            detail=f"{again['gross'] if again else None} — the rise reached back")
    _shift(hourly["id"], date(2035, 7, 10), 10)
    july = _row_for(_cost("2035-07-01", "2035-08-01"), hourly["id"])
    s.check("but July is at the new rate", july and abs(july["gross"] - 200.0) < 0.01,
            detail=f"{july['gross'] if july else None}")

    s.section("A salary is apportioned by day, not bought by the hour")
    salaried = _person("Salaried")
    _wage(salaried["id"], "2035-01-01", "monthly", 3100.00)
    _shift(salaried["id"], date(2035, 3, 4), 7)     # one token shift so they appear
    full = _row_for(_cost("2035-03-01", "2035-04-01"), salaried["id"])
    s.check("a whole month is the whole salary", full and abs(full["gross"] - 3100.0) < 0.01,
            detail=f"{full['gross'] if full else None}")
    half = _row_for(_cost("2035-03-01", "2035-03-17"), salaried["id"])
    # 16 of March's 31 days.
    want = round(3100.0 * 16 / 31, 2)
    s.check(f"sixteen days of March is {want}", half and abs(half["gross"] - want) < 0.05,
            detail=f"{half['gross'] if half else None}")
    s.check("and it is not driven by the hours they happened to clock",
            half and half["hours"] < 8.0 and half["gross"] > 100,
            detail=f"hours {half['hours'] if half else None}, "
                   f"gross {half['gross'] if half else None} — a quiet week "
                   "must not make a salaried person cheaper")

    s.section("Employer contributions are added, never folded in")
    conn = db()
    conn.execute("""INSERT INTO app_settings (key, value)
                    VALUES ('payroll_employer_contribution_percent', '42')
                    ON CONFLICT(key) DO UPDATE SET value = '42'""")
    conn.commit()
    conn.close()
    b = _cost("2035-03-01", "2035-04-01")
    row = _row_for(b, hourly["id"])
    s.check("gross is unchanged by the setting", abs(row["gross"] - 156.0) < 0.01,
            detail=f"{row['gross']}")
    s.check("employer contributions are 42% of gross",
            abs(row["employer"] - 65.52) < 0.02, detail=f"{row['employer']}")
    s.check("and the total is the two added", abs(row["total"] - 221.52) < 0.02,
            detail=f"{row['total']}")

    s.section("The rows add up to the total")
    # House rule: a report whose lines do not reconcile is worse than none.
    for field in ("gross", "employer", "total"):
        s.check(f"{field} reconciles",
                abs(sum(r[field] for r in b["rows"]) - b[field]) < 0.02,
                detail=f"rows {sum(r[field] for r in b['rows']):.2f} vs "
                       f"total {b[field]:.2f}")

    s.section("A record can name its own rate, overriding the house one")
    own = _person("OwnRate")
    _wage(own["id"], "2035-01-01", "hourly", 10.00, employer_rate=0)
    _shift(own["id"], date(2035, 3, 12), 10)
    row = _row_for(_cost("2035-03-01", "2035-04-01"), own["id"])
    s.check("their contributions use 0, not the house 42",
            row and abs(row["employer"]) < 0.01, detail=f"{row['employer'] if row else None}")
    s.check("and gross is still gross", row and abs(row["gross"] - 100.0) < 0.01)

    s.section("Nobody usable is costed at zero")
    # The old failure: no clean hourly rate meant no cost, and the total was
    # quietly short rather than visibly incomplete.
    ghost = _person("NoWage", pay_rate="SMIC + 5%", pay_type="monthly")
    _shift(ghost["id"], date(2035, 3, 13), 9)
    b = _cost("2035-03-01", "2035-04-01")
    s.check("they are named as unpriced",
            any(TAG + " NoWage" in n for n in b["unpriced"]), detail=f"{b['unpriced']}")
    s.check("and not sitting in the rows at zero",
            _row_for(b, ghost["id"]) is None,
            detail="an unpriced person appeared as a costed row")

    s.section("A typed wage beats the free-text guess")
    both = _person("Both", pay_rate="9.00/hour", pay_type="hourly")
    _shift(both["id"], date(2035, 3, 14), 10)
    guess = _row_for(_cost("2035-03-01", "2035-04-01"), both["id"])
    s.check("with no record, the pay note is used",
            guess and abs(guess["gross"] - 90.0) < 0.01 and guess["source"].startswith("estimated"),
            detail=f"{guess}")
    _wage(both["id"], "2035-01-01", "hourly", 15.00)
    typed = _row_for(_cost("2035-03-01", "2035-04-01"), both["id"])
    s.check("with a record, the record wins",
            typed and abs(typed["gross"] - 150.0) < 0.01
            and typed["source"] == "wage on file",
            detail=f"{typed} — the pay note overrode the typed figure")

    s.section("estimated_labour_cost still answers, and now from the record")
    conn = db()
    # Five now, not three: the counts of who was costed from a typed
    # wage and who from a free-text pay note used to be dropped here,
    # which is where net profit stopped being able to say what it left out.
    (cost, hours, unpriced, estimated,
     typed) = m.estimated_labour_cost(conn, "2035-03-01", "2035-04-01")
    conn.close()
    s.check("it returns three values as before", cost is not None and hours > 0)
    s.check("and the cost matches the breakdown's total",
            abs(cost - _cost("2035-03-01", "2035-04-01")["total"]) < 0.02,
            detail=f"{cost} vs {_cost('2035-03-01', '2035-04-01')['total']}")
    s.check("and it still reports how many it could not price", unpriced >= 1,
            detail=f"{unpriced}")

    # ------------------------------------------------------------ the page
    s.section("The page")
    page = oc.get("/admin/payroll/wages")
    html = page.get_data(as_text=True)
    s.check("it loads", page.status_code == 200, page)
    s.check("and names somebody with no wage on file", "No figure on file" in html)
    s.check("and says which figures are gross", "gross" in html.lower(),
            detail="a money page that does not say gross or net says nothing")

    s.section("Recording one through the form")
    fresh = _person("Formed")
    r = oc.post("/admin/payroll/wages/new", data={
        "user_id": str(fresh["id"]), "basis": "hourly", "gross_amount": "13,75",
        "effective_from": "2035-02-01", "note": TAG + " via the form",
    }, follow_redirects=True)
    conn = db()
    saved = conn.execute(
        "SELECT * FROM wage_records WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (fresh["id"],)).fetchone()
    conn.close()
    s.check("it is stored", saved is not None)
    s.check("a comma decimal is read as 13.75",
            saved and abs(saved["gross_amount"] - 13.75) < 0.01,
            detail=f"{saved['gross_amount'] if saved else None}")
    s.check("and who recorded it is kept",
            saved and saved["created_by_user_id"] is not None)

    s.section("Records are added, never edited")
    oc.post("/admin/payroll/wages/new", data={
        "user_id": str(fresh["id"]), "basis": "hourly", "gross_amount": "15.00",
        "effective_from": "2035-09-01", "note": TAG + " rise",
    }, follow_redirects=True)
    conn = db()
    n = conn.execute("SELECT COUNT(*) AS c FROM wage_records WHERE user_id = ?",
                     (fresh["id"],)).fetchone()["c"]
    conn.close()
    s.check("both rows are there", n == 2, detail=f"{n} row(s)")
    s.check("the earlier one is still what applies in March",
            abs(m.wage_on(db(), fresh["id"], "2035-03-01")["gross_amount"] - 13.75) < 0.01)

    s.section("Bad input is refused rather than stored")
    conn = db()
    before = conn.execute("SELECT COUNT(*) AS c FROM wage_records").fetchone()["c"]
    conn.close()
    for label, data in (
        ("no start date", {"user_id": str(fresh["id"]), "basis": "hourly",
                           "gross_amount": "10", "effective_from": ""}),
        ("not a number", {"user_id": str(fresh["id"]), "basis": "hourly",
                          "gross_amount": "about twelve", "effective_from": "2035-01-01"}),
        ("zero", {"user_id": str(fresh["id"]), "basis": "hourly",
                  "gross_amount": "0", "effective_from": "2035-01-01"}),
        ("an invented basis", {"user_id": str(fresh["id"]), "basis": "piecework",
                               "gross_amount": "10", "effective_from": "2035-01-01"}),
        ("nobody", {"user_id": "999999", "basis": "hourly",
                    "gross_amount": "10", "effective_from": "2035-01-01"}),
    ):
        resp = oc.post("/admin/payroll/wages/new", data=data, follow_redirects=True)
        s.check(f"{label}: not written and not a 500", resp.status_code < 500)
    conn = db()
    after = conn.execute("SELECT COUNT(*) AS c FROM wage_records").fetchone()["c"]
    conn.close()
    s.check("nothing was stored by any of them", after == before,
            detail=f"{before} -> {after}")

    s.section("The house contribution figure")
    oc.post("/admin/payroll/wages/settings",
            data={"employer_contribution_percent": "41.5"}, follow_redirects=True)
    conn = db()
    s.check("it is saved", abs(m.wage_setting(
        conn, "payroll_employer_contribution_percent") - 41.5) < 0.01)
    conn.close()
    r = oc.post("/admin/payroll/wages/settings",
                data={"employer_contribution_percent": "lots"}, follow_redirects=True)
    conn = db()
    s.check("junk is refused and the old figure kept", abs(m.wage_setting(
        conn, "payroll_employer_contribution_percent") - 41.5) < 0.01,
        detail=f"{flashes(r)[:1]}")
    conn.close()

    s.section("Guards")
    # What somebody earns is not for everyone who can see the staff list.
    s.check("an employee cannot open the page",
            ec.get("/admin/payroll/wages").status_code in (302, 403))
    s.check("nor record a wage",
            ec.post("/admin/payroll/wages/new",
                    data={"user_id": str(fresh["id"]), "basis": "hourly",
                          "gross_amount": "99", "effective_from": "2035-01-01"}
                    ).status_code in (302, 403))
    s.check("nor change the house figure",
            ec.post("/admin/payroll/wages/settings",
                    data={"employer_contribution_percent": "0"}).status_code in (302, 403))
    conn = db()
    s.check("and neither attempt changed anything", abs(m.wage_setting(
        conn, "payroll_employer_contribution_percent") - 41.5) < 0.01)
    conn.close()

    _cleanup()
    return s
