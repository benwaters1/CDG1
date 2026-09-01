"""One budget figure, in a house where August and February are different trades.

REVENUE_BUDGET_SETTING was a single number applied to all twelve months. The
owner's home page drew a dashed line at it across six months of bars, so the
line sat far above every winter month and far below every summer one. A
target that is wrong eleven months out of twelve is a target somebody stops
reading, and the month it finally matters is the month it gets skimmed.

It was also unsettable. Nothing in the app wrote that key — no form, no
route — so the line only ever appeared for somebody who had edited the
database by hand. A target nobody can set is a target nobody has.

And there was no cost side, so "are we ahead or behind" has only ever been
half a question: revenue can beat its target in a month that lost money.

Three things this holds in place:

  - BLANK IS NOT ZERO. A month nobody has budgeted must read as "not set".
    A target of zero is met by doing nothing, which is the worst reading a
    figure can have, and it would paint eleven months green.

  - REVENUE AND COST STAY APART. Beating the revenue target while
    overspending is a different month from both going to plan, and one
    netted number cannot say which happened.

  - THE OLD SINGLE FIGURE STILL WORKS. Somebody set it; they should keep
    seeing what they saw, and the page should say the figure was inherited
    rather than let it look typed for that month.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m


def _cleanup(conn):
    conn.execute("DELETE FROM monthly_budgets")
    conn.execute("DELETE FROM app_settings WHERE key = ?",
                 (m.REVENUE_BUDGET_SETTING,))
    conn.commit()


def run():
    s = Suite("A budget that knows about seasons")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = house_today()

    s.section("Twelve months, not one number")
    r = oc.post("/management/budget",
                data={"revenue_8": "40000", "cost_8": "18000",
                      "revenue_2": "6000", "cost_2": "9000",
                      "note_2": "shut for most of it"},
                follow_redirects=True)
    august = m.budget_for(conn, date(2026, 8, 1))
    february = m.budget_for(conn, date(2026, 2, 1))
    s.check("August and February can differ",
            august == (40000.0, 18000.0) and february == (6000.0, 9000.0),
            detail=f"August {august}, February {february} — {flashes(r)}")

    s.check("and the same month in another year is the same target",
            m.budget_for(conn, date(2031, 8, 1)) == august,
            detail="keyed to the month OF THE YEAR, so the house's shape "
                   "does not have to be retyped every January")

    s.section("A month nobody set is not a month that met its target")
    # The whole reason None is carried rather than 0. A target of zero is
    # beaten by doing nothing, and would paint ten months green.
    s.check("it reads as not set", m.budget_for(conn, date(2026, 5, 1)) == (None, None),
            detail=str(m.budget_for(conn, date(2026, 5, 1))))
    v = m.budget_variance(conn, date(2026, 5, 1), 12345.0, 6000.0)
    s.check("so no variance is claimed for it",
            v["revenue_delta"] is None and v["cost_delta"] is None,
            detail=str(v))
    s.check("rather than showing the whole month's takings as a win",
            v["revenue_pct"] is None, detail=str(v["revenue_pct"]))

    s.section("February can lose money while beating its revenue target")
    # Netting the two into one figure loses exactly this.
    v = m.budget_variance(conn, date(2026, 2, 1), 7000.0, 11000.0)
    s.check("revenue is ahead", v["revenue_delta"] == 1000.0, detail=str(v))
    s.check("and cost is ahead too, which is the bad direction",
            v["cost_delta"] == 2000.0, detail=str(v))
    s.check("both reported, neither netted",
            v["revenue_delta"] is not None and v["cost_delta"] is not None,
            detail="one number would say +1000 and hide that the month lost "
                   "money")

    s.section("The old single figure still works")
    _cleanup(conn)
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, '20000')",
                 (m.REVENUE_BUDGET_SETTING,))
    conn.commit()
    s.check("a month with nothing set falls back to it",
            m.budget_for(conn, date(2026, 3, 1))[0] == 20000.0,
            detail=str(m.budget_for(conn, date(2026, 3, 1))))
    body = oc.get("/management/budget").get_data(as_text=True)
    s.check("and the page says the figure was inherited, not typed",
            "inherited from the old all-year figure" in body,
            detail="a number carried over from a setting the owner has "
                   "forgotten about should not look like one they chose "
                   "for that month")

    # And a month that IS set beats the fallback.
    oc.post("/management/budget", data={"revenue_3": "31000"},
            follow_redirects=True)
    s.check("a month that is set wins over the fallback",
            m.budget_for(conn, date(2026, 3, 1))[0] == 31000.0,
            detail=str(m.budget_for(conn, date(2026, 3, 1))))

    s.section("Clearing a month means clearing it")
    oc.post("/management/budget", data={"revenue_3": "", "cost_3": "",
                                        "note_3": ""}, follow_redirects=True)
    s.check("the row goes rather than being stored as zero",
            conn.execute("SELECT COUNT(*) AS c FROM monthly_budgets "
                         "WHERE month = 3").fetchone()["c"] == 0,
            detail="a stored zero would be a target met by doing nothing")

    s.section("Each bar on the home page carries its own target")
    _cleanup(conn)
    with m.app.test_request_context():
        rows, budget, budget_pct = m.owner_home_revenue(conn, today)
    s.check("with none set, no bar claims one",
            all(r["budget_pct"] is None for r in rows),
            detail=str([r["budget_pct"] for r in rows]))

    # A target only on the month six back, to prove it is per-bar.
    old_month = (today.replace(day=1) - timedelta(days=150)).replace(day=1)
    conn.execute(
        """INSERT INTO monthly_budgets (month, revenue, updated_at)
           VALUES (?, 25000, ?)""",
        (old_month.month, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    with m.app.test_request_context():
        rows, budget, budget_pct = m.owner_home_revenue(conn, today)
    marked = [r for r in rows if r["budget_pct"] is not None]
    s.check("only the month with a target is marked", len(marked) == 1,
            detail=f"{len(marked)} of {len(rows)} bars marked — one line "
                   "across all six was the visual form of the bug")

    s.section("An employee cannot set the year")
    r = ec.post("/management/budget", data={"revenue_1": "1"},
                follow_redirects=False)
    s.check("the form is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
