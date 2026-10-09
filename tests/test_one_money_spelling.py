"""Money is written one way on the staff pages.

WHAT WENT WRONG. Four spellings: '%.2f' (€3400.00), '%.0f' (€3400),
'{:,.2f}' (€3,400.00) and euro() (€3,400), in 330 places across 72 staff
templates. The same invoice read differently from one page to the next, a
negative printed as "€-3461.20" -- the minus inside the amount, on the column
headed Running change, where it is the one thing that matters -- and a figure
nobody had recorded raised inside '%.2f' and took the whole page down.

WHAT THIS PINS.
  - |money: €3,400.00; −€163.00; never −€0.00; a dash for nothing recorded;
    halves rounded up from the decimal, not the float. euro() is money() to
    no places, so the two cannot drift.
  - No staff template formats money by hand.
  - No staff page prints the minus after the euro sign.
"""
import glob
import os
import re

from _harness import Suite, clients, db, datetime_now
import _harness
import test_owner_pages_read_cleanly as sweep

m = _harness.m
TAG = "ZZMONEY"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Money written out by hand: the euro sign, then an expression that formats
# a number itself.
BY_HAND = re.compile(r"""€\s*\{\{\s*['"](?:%\.?\d*[fg]|\{:,?\.\d+f\})['"]""")
# The minus inside the amount.
MINUS_INSIDE = re.compile(r"€\s*(?:-|−|&minus;)\s*\d")


def _staff_templates():
    for f in sorted(glob.glob(os.path.join(ROOT, "templates", "*.html"))):
        src = open(f, encoding="utf-8").read()
        if '{% extends "base.html" %}' in src:
            yield os.path.basename(f), src


def run():
    s = Suite("money is written one way")
    oc, _ec, _o, _e = clients()

    s.section("The one spelling")
    cases = [
        (3400, 2, "€3,400.00"), (-163, 2, "−€163.00"), (-0.004, 2, "€0.00"),
        (None, 2, "—"), ("", 2, "—"), (12.5, 0, "€13"), (2.675, 2, "€2.68"),
        ("12.5", 2, "€12.50"), (1234567.891, 2, "€1,234,567.89"), (0, 0, "€0"),
        (-0.0, 2, "€0.00"), (-2.5, 0, "−€3"),
    ]
    wrong = [f"{v!r} to {p}: {m.money(v, p)!r}, wanted {want!r}"
             for v, p, want in cases if m.money(v, p) != want]
    s.check("each case comes out as written", not wrong, detail="; ".join(wrong))
    s.check("euro() is money() to no places",
            all(m.euro(v) == m.money(v or 0, 0) for v in (0, None, 3461.2, -163, 12.5)))
    with m.app.test_request_context():
        drawn = m.app.jinja_env.from_string(
            "{{ a|money }} {{ b|money(0) }} {{ c|money }}").render(a=-3461.2, b=380, c=None)
    s.check("the filter is what the pages use", drawn == "−€3,461.20 €380 —", detail=drawn)

    s.section("No staff template writes money by hand")
    by_hand = [(name, hit.group(0)) for name, src in _staff_templates()
               for hit in BY_HAND.finditer(src)]
    s.check("none", not by_hand,
            detail="; ".join(f"{n}: {h}" for n, h in by_hand[:4]))
    s.check("the scan can see one",
            bool(BY_HAND.search("<td>€{{ '%.2f'|format(x) }}</td>"))
            and bool(BY_HAND.search("€{{ '{:,.0f}'.format(x) }}"))
            and not BY_HAND.search("<td>{{ x|money }}</td>"))
    s.check("and the scan looked at the staff pages",
            sum(1 for _ in _staff_templates()) > 200)

    s.section("No staff page prints the minus after the euro sign")
    conn = db()
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    # Owed today and nothing coming in: the running column goes below zero.
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, 'Roof slates', 3400, 'pending', ?)""",
        (TAG + " Ardoises", datetime_now()))
    conn.commit()
    ahead = oc.get("/management/money-ahead").get_data(as_text=True)
    s.check("Money ahead shows a negative the right way round",
            "−€" in ahead and not MINUS_INSIDE.search(ahead),
            detail=(MINUS_INSIDE.search(ahead).group(0) if MINUS_INSIDE.search(ahead)
                    else "no negative on the page -- the check would pass on nothing"))
    s.check("and the invoice is written in the one spelling", "€3,400.00" in ahead)
    inside = [(ep, MINUS_INSIDE.search(html).group(0)) for ep, _url, html in sweep.owner_pages(oc)
              if MINUS_INSIDE.search(html)]
    s.check("nor does any other staff page", not inside,
            detail="; ".join(f"{ep}: {h}" for ep, h in inside[:4]))
    s.check("the sweep can see one", bool(MINUS_INSIDE.search("<td>€-3461.20</td>")))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
