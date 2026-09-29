"""Net profit was built on a labour cost that quietly left people out.

labour_cost_breakdown works out three things about every figure it makes:
how many people were costed from a WAGE THE OWNER TYPED, how many from a
number INFERRED from a free-text pay note, and who could not be costed at
all. CLAUDE.md is explicit that the inferred one is never a payroll figure,
and the wages page has always been honest about it — every row says which
of the two it came from.

Then estimated_labour_cost dropped two of the three at its own boundary,
and financial_month_summary dropped the third as well:

    labour_cost, _labour_hours, _unpriced = estimated_labour_cost(...)
    net = revenue - expenses - labour_cost

So net — on the dashboard, on the annual summary — was revenue minus a
labour cost that silently excluded everybody the app could not price. It is
overstated by exactly their wages. The reports page said so; the pages
people actually read net off did not.

Nothing about this changes a figure. It is about a figure being able to say
what it does not include, which is the house rule: gross vs net is stated
on every figure, and rows must add up to their total.

Found by a sweep for keys built into a returned dict that nothing ever
reads — the shape test_dead_context cannot see, because a dict of twelve
keys reaches a template as one kwarg and the eleven nobody opens are
invisible to it.
"""
import re as _re
from datetime import date, timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZLABH"


def _cleanup(conn):
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM wage_records WHERE user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("A labour figure that says what it leaves out")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    # A month far enough out that nothing else in the database is in it.
    start = date(2036, 5, 1)
    end = date(2036, 6, 1)

    def person(name, pay_rate=None, pay_type=None):
        conn.execute(
            """INSERT INTO users (name, email, password_hash, role, status,
                                  pay_rate, pay_type, created_at)
               VALUES (?, ?, 'x', 'employee', 'active', ?, ?, ?)""",
            (TAG + " " + name, f"{TAG.lower()}{name.lower()}@example.invalid",
             pay_rate, pay_type, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def worked(user_id, day, hours=8):
        """Clocked time, not a rostered shift.

        labour_hours_by_person sums time_entries and says in its own
        docstring that it is the one definition of hours worked for
        costing. A shift nobody turned up to costs nothing, which is right
        — so a fixture built out of shifts produces a breakdown with
        nobody in it.
        """
        day_at = start + timedelta(days=day)
        conn.execute(
            """INSERT INTO time_entries (user_id, clock_in_at, clock_out_at,
                                         auto_closed)
               VALUES (?, ?, ?, 0)""",
            (user_id, f"{day_at.isoformat()}T08:00:00+00:00",
             f"{day_at.isoformat()}T{8 + hours:02d}:00:00+00:00"))

    # Typed: a real wage the owner set.
    typed = person("Typed")
    conn.execute(
        """INSERT INTO wage_records (user_id, basis, gross_amount, employer_rate,
                              effective_from, created_at)
           VALUES (?, 'hourly', 20, 25, ?, ?)""",
        (typed, "2030-01-01", now))
    worked(typed, 1)

    # Inferred: nothing typed, but a free-text pay note the app can read.
    guessed = person("Guessed", pay_rate="18", pay_type="hourly")
    worked(guessed, 2)

    # Unpriced: nothing usable at all.
    unpriced = person("Unpriced", pay_rate="ask Isabelle", pay_type=None)
    worked(unpriced, 3)
    conn.commit()

    s.section("The breakdown tells the three apart")
    b = m.labour_cost_breakdown(conn, start.isoformat(), end.isoformat())
    s.check("one person costed from a wage on file", b["typed_count"] == 1,
            detail=str(b["typed_count"]))
    s.check("one from a free-text pay note", b["estimated_count"] == 1,
            detail=str(b["estimated_count"]))
    s.check("and one who cannot be costed at all",
            any(TAG in n for n in b["unpriced"]), detail=str(b["unpriced"]))

    s.section("And the counts survive the journey to the page")
    # This is the whole finding. They were computed here and dropped at the
    # next boundary, so no page could say what its total left out.
    cost, hours, un, est, typ = m.estimated_labour_cost(
        conn, start.isoformat(), end.isoformat())
    s.check("the number of unpriced people comes through", un == 1, detail=str(un))
    s.check("and how many were inferred rather than typed",
            est == 1 and typ == 1, detail=f"estimated {est}, typed {typ}")

    s.section("The month summary carries it, so net can be honest")
    fin = m.financial_month_summary(conn, start, end)
    s.check("the summary says how many are not in its labour cost",
            fin["labour_unpriced"] == 1, detail=str(fin.get("labour_unpriced")))
    s.check("and how many of those that are were guessed at",
            fin["labour_estimated"] == 1, detail=str(fin.get("labour_estimated")))

    s.section("The figures themselves are unchanged")
    # Eight hours at 20 plus 25% employer, and eight at 18 with the house
    # rate. What matters is that saying what is missing did not move
    # anything -- this was about honesty, not arithmetic.
    s.check("the priced total is still the priced total",
            cost == b["total"], detail=f"{cost} against {b['total']}")
    s.check("and the rows still add up to it",
            round(sum(r["total"] for r in b["rows"]), 2) == b["total"],
            detail=f"{sum(r['total'] for r in b['rows'])} against {b['total']}")
    s.check("the unpriced person is not silently costed at zero",
            not any(TAG + " Unpriced" == r["name"] for r in b["rows"]),
            detail="a zero would make the total look complete, which is the "
                   "opposite of the point")

    s.section("Net says what it does not include")
    # A page that shows net without this reads as a bank figure. It is
    # revenue minus a labour cost that cannot price somebody with no wage,
    # so it is overstated by exactly their wages.
    r = oc.get(f"/admin/reports/labour?period=month&date={start.isoformat()}")
    body = r.get_data(as_text=True)
    s.check("the labour report opens", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and distinguishes a guess from a wage",
            "pay note rather than a wage" in body,
            detail="'n people have no rate' is a different admission from "
                   "'n of the ones costed were guessed at', and only the "
                   "first was ever made")

    s.section("The labour report adds up, row by row")
    # Two faults, one page. The headline hours were the COSTED hours only, so
    # the unpriced person was a row in the table and missing from "Hours
    # worked" -- under a sentence saying their hours were counted. And every
    # row was costed apart, from the pay note alone, so the typed wage showed
    # "—" beside a total that had costed it.
    with m.app.test_request_context(
            f"/admin/reports/labour?period=month&date={start.isoformat()}"):
        period = m.period_from_request()
    data = m.report_labour(conn, period)
    mine = {p["name"]: p for p in data["people"] if p["name"].startswith(TAG)}
    costed = {r["name"]: r["total"] for r in b["rows"]}
    tagged_hours = round(sum(p["hours"] for p in mine.values()), 1)
    s.check("the unpriced person's hours are in the headline",
            TAG + " Unpriced" in mine and tagged_hours == 24.0
            and data["total_hours"] >= tagged_hours,
            detail=f"headline {data['total_hours']}h over rows "
                   f"{[(n, p['hours']) for n, p in mine.items()]}")
    s.check("and the headline is the sum of the rows",
            data["total_hours"] == round(sum(p["hours"] for p in data["people"]), 1),
            detail=f"{data['total_hours']} against "
                   f"{round(sum(p['hours'] for p in data['people']), 1)}")
    typed_row = mine.get(TAG + " Typed", {}).get("cost")
    s.check("a typed wage is costed on its row, at the wage",
            typed_row is not None and typed_row == costed.get(TAG + " Typed"),
            detail=f"row says {typed_row}, the costing {costed.get(TAG + ' Typed')} "
                   "-- the row read the pay note, which this person does not have")
    guessed_row = mine.get(TAG + " Guessed", {}).get("cost")
    s.check("a pay note is costed as the costing costs it",
            guessed_row is not None and guessed_row == costed.get(TAG + " Guessed"),
            detail=f"row says {guessed_row}, the costing {costed.get(TAG + ' Guessed')}")
    s.check("the unpriced person is a row with no cost, not a zero",
            TAG + " Unpriced" in mine and mine[TAG + " Unpriced"]["cost"] is None,
            detail=str(mine.get(TAG + " Unpriced")))
    s.check("and the costs on the rows add up to the estimated cost",
            round(sum(p["cost"] for p in data["people"] if p["cost"] is not None), 2)
            == data["total_cost"],
            detail=f"rows {sum(p['cost'] for p in data['people'] if p['cost'] is not None)} "
                   f"against {data['total_cost']}")
    body = oc.get(f"/admin/reports/labour?period=month&date={start.isoformat()}"
                  ).get_data(as_text=True)
    head = _re.search(r'<div class="overview-value">\s*([^<]*?)\s*<span class="overview-sub">h'
                      r'</span></div>\s*<div class="overview-label">Hours worked</div>', body)
    # Against the rows the PAGE shows, not against report_labour: a check that
    # compares the page with the function that drew it agrees with any break
    # in that function, and the first version of this one did exactly that.
    table = _re.search(r"<tbody>(.*?)</tbody>", body, _re.S)
    shown = []
    for tr in _re.findall(r"<tr>(.*?)</tr>", table.group(1) if table else "", _re.S):
        cells = _re.findall(r"<td[^>]*>(.*?)</td>", tr, _re.S)
        if len(cells) == 4:
            shown.append(float(cells[2].strip()))
    headline = float(head.group(1)) if head else None
    s.check("the page's headline is the sum of the page's own rows",
            headline is not None and shown and headline == round(sum(shown), 1)
            and headline >= 24.0,
            detail=f"headline reads {headline}, the rows below it {shown}")
    s.check("and the typed wage's row shows its cost",
            "€{:,.2f}".format(costed.get(TAG + " Typed") or 0) in body,
            detail="the row for somebody with a wage on file read '—'")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
