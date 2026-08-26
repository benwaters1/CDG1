"""Every wide table scrolls inside its own box, not by dragging the page.

A table wider than a phone screen pushes the whole document sideways and
takes the header and navigation with it — the page looks broken rather than
merely cramped. The app already had the answer, `.table-wrap`, and its CSS
comment records the last time this happened: an 8-column payroll table at
531px on a 375px phone.

Six pages built in one sitting all missed it, which is what a convention
nothing enforces gets you. This is a source check rather than a browser one
on purpose: it costs nothing, it runs on every commit, and it fails on the
page somebody adds next week rather than the next time anyone thinks to
open a phone.
"""
import os
import re

from _harness import Suite

TEMPLATES = "templates"

# Pages whose tables are deliberately not in the responsive shell: the office
# wall display and the printable sheets are fixed-width by design and are
# never opened on a phone.
EXEMPT = {
    "office_display.html", "pos_kitchen.html", "staff_today.html",
    "menu_card.html", "pos_bill.html",
}


def run():
    s = Suite("table overflow")

    s.section("Every data table can scroll without moving the page")
    offenders, checked = [], 0
    for name in sorted(os.listdir(TEMPLATES)):
        if not name.endswith(".html") or name in EXEMPT:
            continue
        src = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
        for match in re.finditer(r"<table\b", src):
            checked += 1
            # Look back for an enclosing wrapper that has not been closed.
            before = src[:match.start()]
            opened = before.rfind('class="table-wrap"')
            if opened == -1:
                offenders.append(name)
                break
            # A wrapper counts only if it is still open at the table.
            if before.count("</div>", opened) > before.count("<div", opened):
                offenders.append(name)
                break

    s.check(f"all {checked} tables are inside a scrolling wrapper",
            not offenders,
            detail=("unwrapped: " + ", ".join(sorted(set(offenders)))
                    if offenders else ""))

    s.section("The wrapper actually scrolls")
    # A wrapper that does not scroll is decoration, and the check above would
    # then be enforcing nothing at all.
    css = ""
    for f in ("static/style.css", "static/gudanes.css"):
        if os.path.exists(f):
            css += open(f, encoding="utf-8").read()
    rule = re.search(r"\.table-wrap\s*\{([^}]*)\}", css)
    s.check(".table-wrap is defined", bool(rule))
    s.check("and sets overflow-x so wide content scrolls in place",
            bool(rule) and "overflow-x:auto" in rule.group(1).replace(" ", ""),
            detail=rule.group(1).strip()[:60] if rule else "")

    return s


if __name__ == "__main__":
    print(run().report())
