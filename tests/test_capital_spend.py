"""A new roof and a box of soap were the same thing to this app.

`expenses` carried kind, ledger_code and doc_type, and nothing marked a
spend as CAPITAL. Restoration work, a boiler, re-leading a window all
landed in the same pot as the week's cleaning supplies — so net profit,
break-even and the annual summary read a château being restored as a
château having a terrible month.

Not a bookkeeping complaint. Pennylane owns the asset register and the
depreciation schedule and this is not trying to be that. It is that the
app's OWN figures were wrong about the business they describe.

The two halves have to move together, and that is what most of this suite
is about. Adding a capital total beside the operating one while leaving the
operating one unchanged would have been worse than doing nothing: the same
fourteen thousand euros would appear twice and net would still call the
month a disaster. So:

  - operating expenses EXCLUDE capital
  - the capital figure carries it
  - and the two add back to everything approved, which is checked

Net does not subtract it, because net says how the house TRADED and a roof
is not trading. It is reported beside net, because a net figure that
silently omits the restoration is as misleading as one that calls it soap.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ZZCAP"


def _cleanup(conn):
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Spending on the house, not on running it")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)

    # A month far enough out that nothing else in the database is in it.
    start = date(2037, 4, 1)
    end = date(2037, 5, 1)
    stamp = "2037-04-10T09:00:00+00:00"

    def spend(desc, amount, kind="supplier_invoice", capital=0):
        conn.execute(
            """INSERT INTO expenses (kind, vendor_name, description, amount,
                                     status, is_capital, submitted_at)
               VALUES (?, 'Maison Roux', ?, ?, 'approved', ?, ?)""",
            (kind, TAG + " " + desc, amount, capital, stamp))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    roof = spend("roof", 14200.0, capital=1)
    spend("soap", 180.0)
    spend("mileage", 60.0, kind="staff_expense")
    # A claim can be capital too -- somebody buying materials for the work.
    # Without this the staff half of the split is never exercised, and a
    # control proved that: breaking it changed nothing.
    spend("lime and sand for the wall", 420.0, kind="staff_expense", capital=1)
    conn.commit()

    summary = m.financial_month_summary(conn, start, end)

    s.section("The roof is not a month's running costs")
    s.check("operating expenses leave it out",
            summary["expenses_total"] == 240.0,
            detail=f"{summary['expenses_total']} — 180 of soap and 60 of "
                   "mileage; neither the roof nor the lime")
    s.check("and net is not 14,200 worse for it",
            summary["net"] == round(summary["revenue"] - 240.0
                                    - (summary["labour_cost"] or 0), 2),
            detail=str(summary["net"]))

    s.section("But it is not hidden either")
    s.check("the capital figure carries it",
            summary["capital_spend"] == 14620.0,
            detail=f"{summary['capital_spend']} — a net figure that silently "
                   "omits the restoration is as misleading as one that calls "
                   "it soap")

    s.section("The two halves add back to everything approved")
    # The check that catches the failure mode: counting it in both, or in
    # neither. Either way this arithmetic breaks.
    approved = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS t FROM expenses
            WHERE status IN ('approved','paid') AND description LIKE ?""",
        (TAG + "%",)).fetchone()["t"]
    s.check("nothing is counted twice and nothing is lost",
            round(summary["expenses_total"] + summary["capital_spend"], 2)
            == round(approved, 2),
            detail=f"{summary['expenses_total']} + {summary['capital_spend']} "
                   f"against {approved}")

    s.section("Marking one, and changing your mind")
    soap_id = conn.execute(
        "SELECT id FROM expenses WHERE description = ?",
        (TAG + " soap",)).fetchone()["id"]
    r = oc.post(f"/expenses/{soap_id}/capital", follow_redirects=True)
    after = m.financial_month_summary(conn, start, end)
    s.check("it moves out of the running costs",
            after["expenses_total"] == 60.0,
            detail=f"{after['expenses_total']} — {flashes(r)}")
    s.check("and into the capital figure",
            after["capital_spend"] == 14800.0, detail=str(after["capital_spend"]))
    s.check("with the total still reconciling",
            round(after["expenses_total"] + after["capital_spend"], 2)
            == round(approved, 2))

    oc.post(f"/expenses/{soap_id}/capital", follow_redirects=True)
    back = m.financial_month_summary(conn, start, end)
    s.check("pressing it again puts it back", back["expenses_total"] == 240.0,
            detail=str(back["expenses_total"]))

    s.section("Nothing already recorded was reclassified by the migration")
    # An expense becomes capital because somebody said so, never because a
    # column appeared. Everything untouched has to still be a running cost.
    stray = conn.execute(
        """SELECT COUNT(*) AS c FROM expenses
            WHERE is_capital = 1 AND description NOT LIKE ?""",
        (TAG + "%",)).fetchone()["c"]
    s.check("the default is 'a running cost'", stray == 0,
            detail=f"{stray} expense(s) outside this suite are marked capital "
                   "— the column defaults to 0 on purpose")

    s.section("The money still left the account")
    # The distinction is about what the spend BOUGHT, not about whether it
    # happened. Anything that forecasts cash has to keep seeing it.
    row = conn.execute("SELECT amount, status FROM expenses WHERE id = ?",
                       (roof,)).fetchone()
    s.check("the expense is untouched, only classified",
            row["amount"] == 14200.0 and row["status"] == "approved",
            detail=str(dict(row)))

    s.section("It reaches a page")
    body = oc.get("/expenses").get_data(as_text=True)
    s.check("the expenses list offers the mark", "On the house" in body
            or "Not the house" in body,
            detail="tagged where somebody knows — six months later a line "
                   "reading 'Maison Roux 14,200' cannot be classified by "
                   "anybody who was not there")

    s.section("An employee cannot reclassify spending")
    r = ec.post(f"/expenses/{roof}/capital", follow_redirects=False)
    s.check("the toggle is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
