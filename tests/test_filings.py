"""The app works out the figures and had no idea when they were due.

vat_working assembles a VAT return from four streams. city_tax_arrears
knows what taxe de séjour is owed. The payroll pack exists. And nothing
recorded WHEN any of it had to be filed or paid — zero mentions of a
deadline in 45k lines. In France that is penalties and interest.

WHY THE DATES ARE THE OWNER'S, not French tax law in code. The regime a
house is on — réel simplifié, réel normal, whatever schedule its commune
sets for the taxe de séjour — varies, changes, and is the accountant's
business. Software that confidently asserts the wrong deadline is worse
than software that says nothing, and there is no version of this app that
should be telling somebody their VAT is monthly.

So its job is only to not let a date be forgotten, which it is well suited
to: it already has a calendar, tasks that close themselves, and a home page
that shows what is coming.

Two things worth holding:

  - A FILING IS RECORDED, NOT TICKED. A recurring obligation marked "done"
    is ambiguous the moment the next one falls due. A row saying what was
    filed and when answers which one, and whether the next is open.

  - THE DAY OF THE MONTH SURVIVES A SHORT MONTH. Rolling forward from the
    last date clamps to February and never recovers: due the 31st becomes
    due the 28th and stays there, three days early every month afterwards,
    always plausible on the page and never noticed.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ZZFILE"


def _cleanup(conn):
    conn.execute("DELETE FROM filings_made WHERE obligation_id IN "
                 "(SELECT id FROM filing_obligations WHERE name LIKE ?)",
                 (TAG + "%",))
    conn.execute("DELETE FROM filing_obligations WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Filings and deadlines")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = date.today()

    s.section("The day of the month survives a short month")
    # The quiet one. Rolling from the clamped result means a deadline on
    # the 31st is on the 28th for the rest of its life.
    d = date(2026, 1, 31)
    kept = []
    for _ in range(4):
        d = m.next_filing_due(d, "monthly", due_day=31)
        kept.append(d.isoformat())
    s.check("the 31st comes back after February",
            kept == ["2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31"],
            detail=str(kept))

    drift = date(2026, 1, 31)
    drifted = []
    for _ in range(4):
        drift = m.next_filing_due(drift, "monthly")
        drifted.append(drift.isoformat())
    s.check("and without the remembered day it would not have",
            drifted[1:] == ["2026-03-28", "2026-04-28", "2026-05-28"],
            detail=f"{drifted} — three days early every month, always "
                   "plausible, never noticed")

    s.check("a quarterly return steps three months, not ninety-two days",
            m.next_filing_due(date(2026, 11, 30), "quarterly",
                              due_day=30).isoformat() == "2027-02-28",
            detail=str(m.next_filing_due(date(2026, 11, 30), "quarterly",
                                         due_day=30)))

    s.section("Adding one")
    soon = today + timedelta(days=5)
    r = oc.post("/management/filings",
                data={"name": TAG + " VAT return", "authority": "SIE Foix",
                      "every": "quarterly", "due_on": soon.isoformat(),
                      "warn_days": "14", "note": "four streams, one page"},
                follow_redirects=True)
    row = conn.execute("SELECT * FROM filing_obligations WHERE name LIKE ?",
                       (TAG + "%",)).fetchone()
    s.check("it is on the calendar", row is not None, detail=str(flashes(r)))
    s.check("with the day of the month remembered",
            row and row["due_day"] == soon.day,
            detail=str(row["due_day"]) if row else "")

    s.section("What is due, and what is merely coming")
    due = [d for d in m.filings_due(conn, today)
           if d["obligation"]["name"].startswith(TAG)]
    s.check("something due in five days is listed", len(due) == 1,
            detail=str(len(due)))
    s.check("and is not called late", not due[0]["late"],
            detail=str(due[0]["days"]))

    far = today + timedelta(days=120)
    conn.execute(
        """INSERT INTO filing_obligations (name, every, due_on, due_day,
                   warn_days, active, created_at)
           VALUES (?, 'annual', ?, ?, 14, 1, ?)""",
        (TAG + " Something distant", far.isoformat(), far.day,
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    names = [d["obligation"]["name"] for d in m.filings_due(conn, today)]
    s.check("something four months off is not",
            TAG + " Something distant" not in names,
            detail=f"{names} — a page that lists everything eventually gets "
                   "scrolled past")

    s.section("Late is a different state")
    was_due = today - timedelta(days=3)
    conn.execute(
        """INSERT INTO filing_obligations (name, every, due_on, due_day,
                   warn_days, active, created_at)
           VALUES (?, 'monthly', ?, ?, 14, 1, ?)""",
        (TAG + " Overdue thing", was_due.isoformat(), was_due.day,
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    overdue = [d for d in m.filings_due(conn, today)
               if d["obligation"]["name"] == TAG + " Overdue thing"]
    s.check("it is reported", len(overdue) == 1)
    s.check("as late, with the days negative rather than as time in hand",
            overdue[0]["late"] and overdue[0]["days"] == -3,
            detail=str(overdue[0]["days"]))
    s.check("and the worst comes first",
            m.filings_due(conn, today)[0]["days"]
            <= m.filings_due(conn, today)[-1]["days"])

    s.section("Recording a filing rolls it forward")
    r = oc.post(f"/management/filings/{row['id']}/filed",
                data={"filed_on": today.isoformat(), "amount": "4200",
                      "reference": "TVA-2026-Q3"}, follow_redirects=True)
    after = conn.execute("SELECT * FROM filing_obligations WHERE id = ?",
                         (row["id"],)).fetchone()
    s.check("the next one is three months on",
            after["due_on"] == m.next_filing_due(
                soon, "quarterly", due_day=soon.day).isoformat(),
            detail=f"{row['due_on']} -> {after['due_on']}")
    s.check("and the one just filed drops off the due list",
            not [d for d in m.filings_due(conn, today)
                 if d["obligation"]["id"] == row["id"]],
            detail="self-closing, like every other watch finding")

    made = conn.execute(
        "SELECT * FROM filings_made WHERE obligation_id = ?",
        (row["id"],)).fetchone()
    s.check("what was filed is recorded, not just ticked",
            made and made["covered_due_on"] == soon.isoformat()
            and made["filed_on"] == today.isoformat(),
            detail=str(dict(made)) if made else "nothing recorded")
    s.check("with the amount and the reference",
            made and made["amount"] == 4200.0
            and made["reference"] == "TVA-2026-Q3",
            detail="a tick cannot answer which return, or for how much")

    s.section("It reaches the owner and the calendar")
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    mine = [w for w in warnings if "filing" in w["title"]]
    s.check("a due filing appears on the home page", bool(mine),
            detail=str([w["title"] for w in warnings][:4]))
    s.check("an overdue one is a blocker, not a warning",
            any(w["severity"] == "blocker" for w in mine),
            detail="a late return is already costing money")

    found, _d = m.watch_task_findings(conn, today)
    ours = [f for f in found if f[0] == "filing" and TAG in f[1]]
    s.check("and becomes a task", bool(ours),
            detail=f"kinds: {sorted({f[0] for f in found})}")

    s.section("Taking one off keeps what was filed against it")
    oc.post(f"/management/filings/{row['id']}/stop", follow_redirects=True)
    still = conn.execute(
        "SELECT COUNT(*) AS c FROM filings_made WHERE obligation_id = ?",
        (row["id"],)).fetchone()["c"]
    s.check("the filing history survives", still == 1,
            detail="what the house filed is a record, and it outlives the "
                   "obligation")
    s.check("but it stops being due",
            not [d for d in m.filings_due(conn, today)
                 if d["obligation"]["id"] == row["id"]])

    s.section("What it refuses")
    before = conn.execute("SELECT COUNT(*) AS c FROM filing_obligations "
                          "WHERE name LIKE ?", (TAG + "%",)).fetchone()["c"]
    r = oc.post("/management/filings",
                data={"name": TAG + " No date", "every": "monthly",
                      "due_on": ""}, follow_redirects=True)
    s.check("one with no date is refused",
            any("needs a name, a date" in f for f in flashes(r)),
            detail=str(flashes(r)))
    r = oc.post("/management/filings",
                data={"name": TAG + " Odd", "every": "fortnightly",
                      "due_on": today.isoformat()}, follow_redirects=True)
    s.check("and one on a cycle it does not know is refused",
            conn.execute("SELECT COUNT(*) AS c FROM filing_obligations "
                         "WHERE name LIKE ?",
                         (TAG + "%",)).fetchone()["c"] == before,
            detail="a CHECK constraint would raise; refusing it in the route "
                   "gives the owner a sentence instead of a 500")

    s.section("An employee cannot touch the calendar")
    r = ec.post("/management/filings", data={"name": "x", "every": "monthly",
                                             "due_on": today.isoformat()},
                follow_redirects=False)
    s.check("adding is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = ec.get("/management/filings", follow_redirects=False)
    s.check("and so is looking", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
