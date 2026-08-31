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

WHAT COUNTS AS A WRAPPER IS READ FROM THE STYLESHEET, not from a list here.
A design pass arrived carrying a second wrapper, .g-compare-wrap, which does
the same job by the same property — and this test failed it for having the
wrong name. That is a test enforcing a spelling rather than the thing it
claims to be about, and the obvious way to "fix" such a failure is a
redundant div around a table that already scrolled. So the rule is the
authority: any class whose CSS actually sets overflow-x counts, and a
wrapper that does not scroll counts for nothing however it is spelled.
"""
import os
import re

from _harness import Suite
import _harness

TEMPLATES = os.path.join(_harness.ROOT, "templates")

# Pages whose tables are deliberately not in the responsive shell: the office
# wall display and the printable sheets are fixed-width by design and are
# never opened on a phone.
EXEMPT = {
    "office_display.html", "pos_kitchen.html", "staff_today.html",
    "menu_card.html", "pos_bill.html",
}


def _stylesheet():
    css = ""
    for f in (os.path.join(_harness.ROOT, "static", "style.css"),
              os.path.join(_harness.ROOT, "static", "gudanes.css")):
        if os.path.exists(f):
            css += open(f, encoding="utf-8").read()
    return css


def scrolling_classes(css):
    """Every class the stylesheet actually gives a horizontal scrollbar.

    Read from the CSS rather than listed here, so a wrapper only counts while
    its rule still says so. Renaming .table-wrap, or quietly dropping the
    overflow-x line out of it, takes it straight out of this set — which is
    the failure this whole file exists to catch.
    """
    found = set()
    for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        flat = body.replace(" ", "").replace("\n", "")
        if "overflow-x:auto" not in flat and "overflow-x:scroll" not in flat:
            continue
        for cls in re.findall(r"\.([A-Za-z0-9_-]+)", selector):
            found.add(cls)
    return found


def run():
    s = Suite("table overflow")

    css = _stylesheet()
    wrappers = scrolling_classes(css)

    s.section("What counts as a wrapper, and why")
    # Checked first, because the sweep below is measured against this set. A
    # wrapper that does not scroll is decoration, and the sweep would then be
    # enforcing nothing at all.
    s.check(".table-wrap is defined and scrolls", "table-wrap" in wrappers,
            detail="the app's own convention, the one named in CLAUDE.md")
    s.check("the set is read from the stylesheet, not hardcoded here",
            len(wrappers) >= 1, detail=", ".join(sorted(wrappers))[:90])
    s.check("and something that plainly does not scroll is not in it",
            "g-wrap" not in wrappers,
            detail="the page's own centring wrapper — if that counted, every "
                   "table on the site would pass without scrolling anywhere")

    s.section("Every data table can scroll without moving the page")
    offenders, checked = [], 0
    pattern = re.compile(r'class="([^"]*)"')
    for name in sorted(os.listdir(TEMPLATES)):
        if not name.endswith(".html") or name in EXEMPT:
            continue
        src = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
        for match in re.finditer(r"<table\b", src):
            checked += 1
            before = src[:match.start()]
            # Walk back through every class="..." for the nearest one that both
            # scrolls and is still open where the table actually sits.
            wrapped = False
            for m in reversed(list(pattern.finditer(before))):
                if not (set(m.group(1).split()) & wrappers):
                    continue
                if before.count("</div>", m.start()) > before.count("<div", m.start()):
                    continue                 # opened, but closed again before the table
                wrapped = True
                break
            if not wrapped:
                offenders.append(name)
                break

    s.check(f"all {checked} tables are inside a scrolling wrapper",
            not offenders,
            detail=("unwrapped: " + ", ".join(sorted(set(offenders)))
                    if offenders else ""))

    return s

if __name__ == "__main__":
    print(run().report())
