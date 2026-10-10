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

# Where app.py still writes money by hand, and why each may. Everything a
# member of staff reads -- a flash, a tile, an audit line, a task, a letter
# to the house, the assistant -- goes through money() or euro(). What a GUEST
# reads is left in the wording and spelling it was written in: the letters,
# their statement, the page they book on and the names Stripe shows them.
# Checked both ways: a hand-written amount anywhere else is a red, and so is
# a name here that no longer writes one.
GUEST_FACING = {
    "create_booking": "the booking's lines in the confirmation letter",
    "book_room": "what a guest sees while booking, and the names sent to Stripe",
    "restaurant_book": "the same, for a table",
    "manage_booking": "the guest's own page, telling them what changed",
    "workshop_pay_balance": "the guest paying an atelier balance",
    "validate_promo_code": "a promo code refused at the guest's checkout",
    "api_validate_promo_code": "the same, as the form asks",
    "statement_text": "the guest's statement, as text",
    "statement_text_lines": "the same, line by line",
    "statement_balance_line": "the line that ends it",
    "email_booking_statement": "the statement emailed to the guest",
    "mark_booking_payment_paid": "the guest's payment letter",
    "event_payment_context": "the same, for an event",
    "workshop_payment_context": "the same, for an atelier",
    "event_email_context": "an event letter's price lines",
    "restaurant_email_context": "a table letter's price lines",
    "workshop_email_context": "an atelier letter's price lines",
    "send_share_request": "the letter asking a guest for their share",
    "send_refund_letter": "the refund letter",
    "_refund_balance_line": "its balance line",
    "send_autocharge_taken_email": "the guest told their balance was taken",
    "send_autocharge_failed_email": "the guest told it was not",
}
HAND_MONEY_PY = re.compile(r"(?:€|EUR\s?)\{[^{}]*:[^{}]*f\}|(?:€|EUR\s?)%(?:\.\d)?f")


def hand_money_in_app(src):
    """{function: [line]} for every hand-written amount in app.py."""
    found, fn = {}, None
    for i, line in enumerate(src.splitlines(), 1):
        m_ = re.match(r"def (\w+)\(", line)
        if m_:
            fn = m_.group(1)
        if HAND_MONEY_PY.search(line):
            found.setdefault(fn, []).append(i)
    return found

# The euro sign, however a template spells it. The character alone let 125
# amounts on 27 pages through: they wrote it &euro;, and a rendered page
# carries the entity, not the character, so the sweep below was blind to
# them too.
EURO = r"(?:€|&euro;|&#8364;)"
# Money written out by hand: the euro sign, then an expression that formats
# a number itself.
BY_HAND = re.compile(EURO + r"""\s*\{\{\s*['"](?:%\.?\d*[fg]|\{:,?\.\d+f\})['"]""")
# The minus inside the amount.
MINUS_INSIDE = re.compile(EURO + r"\s*(?:-|−|&minus;)\s*\d")


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
    # A supplier's unit price keeps the fraction of a cent it was quoted in.
    unit = [(0.345, "€0.345"), (2.5, "€2.50"), (0.34567, "€0.3457"), (-0.345, "−€0.345"),
            (12, "€12.00"), (1234.5678, "€1,234.5678"), (None, "—")]
    wrong = [f"{v!r}: {m.money(v, 2, 4)!r}, wanted {want!r}"
             for v, want in unit if m.money(v, 2, 4) != want]
    s.check("a unit price keeps up to four decimals, and never fewer than two",
            not wrong, detail="; ".join(wrong))
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
            and bool(BY_HAND.search("<td>&euro;{{ '%.2f'|format(x) }}</td>"))
            and bool(BY_HAND.search("&euro;{{ '%g'|format(r.was) }}"))
            and not BY_HAND.search("<td>{{ x|money }}</td>"))
    s.check("and the scan looked at the staff pages",
            sum(1 for _ in _staff_templates()) > 200)

    s.section("A figure that can go negative is spelled by money(), in Python too")
    # Found by the sweep below on a full run: the reports index built its
    # headline as f"€{net:,.0f} net", and a month that lost money read
    # "€-2 net". Only a run whose data happens to go negative can see that on a
    # page, so the source is asked as well. Balances and change due are never
    # negative and are left to their own spelling; these names can be.
    signed = re.compile(r"€\{[^}:]*\b(?:net|profit|difference|margin|delta|running|variance)"
                        r"[^}:]*:[^}]*f\}")
    app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    hits = [line.strip()[:90] for line in app_src.split("\n") if signed.search(line)]
    s.check("no f-string writes one with the minus inside", not hits, detail="; ".join(hits[:3]))
    s.check("the scan can see one",
            bool(signed.search('''"financial": f"€{fin['summary']['net']:,.0f} net",''')))

    s.section("Money app.py builds for the staff is written by money(), too")
    found = hand_money_in_app(open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())
    staff = {fn: lines for fn, lines in found.items() if fn not in GUEST_FACING}
    s.check("nowhere a member of staff reads", not staff,
            detail="; ".join(f"{fn} (line {lines[0]})" for fn, lines in sorted(staff.items())[:4]))
    stale = sorted(fn for fn in GUEST_FACING if fn not in found)
    s.check("and every guest-facing exception still writes one", not stale,
            detail=", ".join(stale))
    s.check("the scan can see one",
            hand_money_in_app('def x():\n    flash(f"€{amount:.2f} taken")\n') == {"x": [2]}
            and hand_money_in_app('def y():\n    flash(f"EUR {amount:,.2f}")\n') == {"y": [2]}
            and not hand_money_in_app('def z():\n    flash(f"{money(amount)} taken")\n'))

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
    s.check("the sweep can see one, however the sign is spelled",
            bool(MINUS_INSIDE.search("<td>€-3461.20</td>"))
            and bool(MINUS_INSIDE.search("<td>&euro;-3461.20</td>")))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
