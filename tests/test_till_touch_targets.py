"""The till is the one screen never used with a mouse, and it had no touch sizing.

`static/style.css` carries a `pointer: coarse` block -- 44px minimums, 16px
inputs to stop iOS zooming the page -- and it is careful, specific work. It
covers the app's generic furniture: buttons, calendar chips, topbar links, the
guest-side footer, the consent checkbox.

It did not cover the till, and the reason is the reason this file exists. The
till does not use the generic classes. It has its own -- `till-state`,
`till-back`, `till-bar__exit` -- so it sat outside the one block in the
stylesheet written for fingers, silently, while every page that IS usually
driven by a mouse was handled. Exactly the wrong way round.

Measured on an iPad-sized viewport before the fix: the six course chips a
waiter taps all night (Seated, Ordered, Starters away, Mains away, Dessert,
Bill dropped) were 28px tall and sat in a single row, so a near-miss did not
do nothing -- it marked the wrong course away. "< Floor", the way back out of
a tab, was 17px. The X that leaves the till was 39px wide.

WHY THIS IS A SOURCE TEST. Rendered size needs a browser, and the suite has
none. But the fault was never a wrong number -- it was a control that no rule
applied to at all. That IS visible in the source: a class used on something
tappable in a till template, with no rule for it in any coarse block. So this
reads both, the way test_table_overflow reads the templates for a convention
nothing else enforces.

CHECKED IN BOTH DIRECTIONS. A till control added tomorrow with no coarse rule
reds the run. So does one on the exempt list below that has since been given a
rule -- otherwise the list only ever grows and stops meaning anything.
"""
import glob
import os
import re

from _harness import Suite

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Till controls that need no rule in the coarse block because they are ALREADY
# over the minimum at every size, with what each one actually measured. Not a
# list of things to get round to: a list of things checked and found fine.
#
# Checked in both directions. Give one of these a coarse rule and this list is
# a lie and the run says so; stop using one in a template and the run says that
# too, because a list nobody prunes stops being read.
ALREADY_BIG_ENOUGH = {
    "till-tile":      "the floor tiles, 169x118",
    "till-item":      "the menu buttons, 169x92",
    "till-course":    "the course tabs, 44 tall",
    "till-seat":      "the covers picker, 44x44",
    "till-bar__home": "74x44",
    # The one that says so in its own base rule rather than by accident.
    "till-send":      "min-height:44px already, in .till-send itself",
}


def _coarse_blocks(css):
    """Every rule that sits inside an `@media (pointer: coarse)` block."""
    out = []
    for m in re.finditer(r"@media[^{]*coarse[^{]*\{", css):
        depth, i = 1, m.end()
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        out.append(css[m.end():i - 1])
    return out


def run():
    s = Suite("the till under a finger")

    css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
    blocks = _coarse_blocks(css)

    s.section("There is a block for fingers at all")
    s.check("style.css has a pointer:coarse block", bool(blocks),
            detail="keyed off pointer rather than width, so it reaches a "
                   "tablet held in landscape as well as a phone")
    covered = set(re.findall(r"till-[a-z0-9_-]+", "\n".join(blocks)))

    s.section("The three that were actually wrong")
    # Named individually because each was measured, and because a regression
    # in any one of them is its own kind of bad night.
    for cls, was, why in (
        ("till-state", "28px tall",
         "six course chips in a row -- a near-miss marks the wrong course away"),
        ("till-back", "17px tall",
         "the way back out of a tab, and the hardest thing on the screen to hit"),
        ("till-bar__exit", "39px wide",
         "the control that leaves the till"),
    ):
        s.check("%s has a rule for touch" % cls, cls in covered,
                detail="was %s. %s" % (was, why))
    # The number, not just the presence of a rule. A rule that set 30px would
    # pass the check above and change nothing that matters.
    for cls in ("till-state", "till-back"):
        rule = re.search(re.escape("." + cls) + r"\s*\{[^}]*\}", "\n".join(blocks))
        s.check("%s is raised to at least 44px" % cls,
                bool(rule) and "44px" in rule.group(0),
                detail=repr(rule.group(0)[:90]) if rule else "no rule found")

    s.section("And nothing tappable in the till is left outside it")
    # The general form, so this finds the fourth one as readily as the first.
    used = set()
    for path in glob.glob(os.path.join(ROOT, "templates", "pos_*.html")):
        src = open(path, encoding="utf-8").read()
        # Only elements a finger is meant to land on.
        for tag in re.finditer(r"<(?:button|a|summary)\b[^>]*>", src):
            for m in re.finditer(r'class="([^"]*)"', tag.group(0)):
                used |= {c for c in m.group(1).split() if c.startswith("till-")}

    s.check("there are till controls to check", len(used) >= 3,
            detail="%d found across the till templates" % len(used))
    missing = sorted(used - covered - set(ALREADY_BIG_ENOUGH))
    s.check("every till control has touch sizing", not missing,
            detail="no rule in any pointer:coarse block for: "
                   + ", ".join(missing) if missing else "")

    # The other direction, so the exemption list cannot rot.
    stale = sorted(c for c in ALREADY_BIG_ENOUGH if c in covered)
    s.check("and the exempt list has nothing stale on it", not stale,
            detail="now covered, so the exemption is a lie: " + ", ".join(stale)
                   if stale else "")
    gone = sorted(c for c in ALREADY_BIG_ENOUGH if c not in used)
    s.check("and nothing on it has left the templates", not gone,
            detail="exempted but no longer used anywhere: " + ", ".join(gone)
                   if gone else "")

    return s


if __name__ == "__main__":
    print(run().report())
