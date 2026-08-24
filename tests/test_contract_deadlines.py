"""Trial periods and fixed terms — the two HR dates with legal consequence.

In France both of these decide themselves if nobody acts:

  - a trial period that lapses un-actioned confirms the employee permanently
  - a CDD left running past its end date becomes a CDI

So the expensive failure is not a wrong number on a screen, it is a date that
stopped being shown. contract_deadlines() is written to keep a lapsed date in
the list with a negative days_left, precisely because the naive version —
`WHERE date >= today` — hides the one row that matters most the morning after
it matters. That was untested across all three HR suites.

Also covered: a CDI cannot carry an end date, because "permanent contract,
expires in March" is a contradiction the form is supposed to resolve.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZDL"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _employee(name, contract_type=None, trial_end=None, contract_end=None,
              status="active"):
    conn = db()
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(users)").fetchall()}
    fields = {
        "name": f"{TAG} {name}", "email": f"{TAG.lower()}.{name.lower()}@example.invalid",
        "role": "employee", "status": status, "job_role": "Housekeeping",
        "password_hash": "x", "created_at": datetime.now(timezone.utc).isoformat(),
    }
    for key, val in (("contract_type", contract_type), ("trial_end_date", trial_end),
                     ("contract_end_date", contract_end)):
        if key in cols:
            fields[key] = val
    keys = [k for k in fields if k in cols]
    conn.execute(f"INSERT INTO users ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' * len(keys))})", [fields[k] for k in keys])
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _deadlines():
    conn = db()
    try:
        today = datetime.now(timezone.utc).date()
        return [d for d in m.contract_deadlines(conn, today)
                if (d["name"] or "").startswith(TAG)]
    finally:
        conn.close()


def run():
    s = Suite("Contract deadlines")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(timezone.utc).date()

    s.section("A trial period coming up is flagged")
    soon = (today + timedelta(days=7)).isoformat()
    _employee("Soon", contract_type="cdi", trial_end=soon)
    rows = [d for d in _deadlines() if d["kind"] == "trial"]
    s.check("it appears", len(rows) == 1, detail=f"{len(rows)} trial row(s)")
    if rows:
        s.check("with the days remaining", rows[0]["days_left"] == 7,
                detail=f"got {rows[0]['days_left']}")

    s.section("A trial that has ALREADY lapsed is still shown")
    # The one that matters. A lapsed trial has already confirmed the employee,
    # so hiding it is how somebody finds out months later.
    _employee("Lapsed", contract_type="cdi",
              trial_end=(today - timedelta(days=5)).isoformat())
    lapsed = [d for d in _deadlines() if d["name"].endswith("Lapsed")]
    s.check("a past trial date is not filtered out", len(lapsed) == 1,
            detail="a lapsed trial period disappeared from the list")
    if lapsed:
        s.check("and its days_left is negative, marking it overdue",
                lapsed[0]["days_left"] == -5, detail=f"got {lapsed[0]['days_left']}")

    s.section("A trial far in the future is not noise yet")
    _employee("Distant", contract_type="cdi",
              trial_end=(today + timedelta(days=m.TRIAL_WARNING_DAYS + 30)).isoformat())
    s.check(f"nothing is raised more than {m.TRIAL_WARNING_DAYS} days out",
            not [d for d in _deadlines() if d["name"].endswith("Distant")])

    s.section("A fixed term nearing its end is flagged")
    _employee("Fixed", contract_type="cdd",
              contract_end=(today + timedelta(days=10)).isoformat())
    fixed = [d for d in _deadlines() if d["kind"] == "contract"]
    s.check("the CDD end date appears", len(fixed) == 1, detail=f"{len(fixed)} row(s)")
    if fixed:
        s.check("marked as a contract deadline, not a trial",
                fixed[0]["kind"] == "contract" and fixed[0]["days_left"] == 10)

    s.section("Someone who has left is not chased")
    _employee("Gone", contract_type="cdd",
              contract_end=(today + timedelta(days=3)).isoformat(), status="inactive")
    s.check("an inactive employee raises nothing",
            not [d for d in _deadlines() if d["name"].endswith("Gone")])

    s.section("The most urgent comes first")
    both = _deadlines()
    dates = [d["on_date"] for d in both]
    s.check("the list is in date order, so the overdue one is at the top",
            dates == sorted(dates), detail=f"{[str(x) for x in dates]}")

    s.section("A permanent contract cannot carry an end date")
    # "Permanent, expires in March" is a contradiction; the form resolves it.
    with m.app.test_request_context(
            "/x", method="POST",
            data={"contract_type": "cdi", "contract_end_date": (today + timedelta(days=90)).isoformat(),
                  "trial_end_date": soon, "notice_period_days": "30"}):
        ctype, cend, tend, notice = m.contract_fields_from_form()
    s.check("a CDI's end date is dropped", cend is None, detail=f"got {cend!r}")
    s.check("its trial date is kept", tend == soon)
    s.check("and the notice period is read as a number", notice == 30)

    with m.app.test_request_context(
            "/x", method="POST",
            data={"contract_type": "cdd", "contract_end_date": "not-a-date",
                  "trial_end_date": "", "notice_period_days": "abc"}):
        ctype2, cend2, tend2, notice2 = m.contract_fields_from_form()
    s.check("an unparseable end date becomes None rather than being stored", cend2 is None)
    s.check("and a non-numeric notice period does not crash", notice2 is None)

    s.section("It reaches the owner's HR page")
    page = oc.get("/admin/hr")
    html = page.get_data(as_text=True)
    s.check("the HR page loads", page.status_code == 200, page)
    s.check("and lists the lapsed trial", f"{TAG} Lapsed" in html,
            detail="the deadline is computed but never shown")
    s.check("an employee cannot see the HR page",
            ec.get("/admin/hr").status_code in (302, 403))

    _cleanup()
    return s
