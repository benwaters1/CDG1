"""Work done on every page load and thrown away.

Three times now this has turned up by accident. Featured reviews were queried
on every booking-page load and rendered nowhere, so "Feature on booking page"
was a button with no effect. The same was true on the workshops page, and
nobody had looked. vendors.payment_terms sat in the schema for months with
nothing reading it, so a supplier's terms could be entered and changed nothing.

All three looked finished from outside. A button, a field, a section. That is
what makes this class expensive: there is nothing to notice, because the thing
you would notice is absence.

So it is swept rather than remembered. A route hands a template something; if
neither that template, nor anything it includes, nor anything that extends it
mentions the name, the work is thrown away — and the ones that cost a database
query are the ones worth failing over.

WHAT IS ALLOWED THROUGH, and why each is not the fault above:

  - A NAME THE BASE USES. The shell reads things every page is given.
  - A LITERAL. `today=house_today()` costs nothing and reads as context.
  - THE KNOWN LIST BELOW. A handful that are genuinely computed and genuinely
    unused, kept as a list of what is already known rather than a licence:
    anything NEW that starts throwing a query away fails this test.
"""
import ast
import os
import re

from _harness import Suite, house_today
import _harness

m = _harness.m
ROOT = _harness.ROOT
TEMPLATES = os.path.join(ROOT, "templates")

# Already known, and each is a judgement rather than an oversight. The point of
# the list is that it does not grow by accident: a new one fails.
KNOWN = {
    # Rendered by JavaScript in the page rather than by Jinja, so the sweep
    # cannot see the use.
    ("pos_home.html", "overview"),
    ("pos_home.html", "today"),
    ("pos_order.html", "vat_rates"),
    ("pos_kitchen.html", "capacity"),
    ("pos_day.html", "events"),
    ("admin_pos_journal.html", "perpetual"),
    # Non-HTML templates the sweep does not read.
    ("outlook_addin_manifest.xml", "*"),
    ("outlook_addin_launchevent.js", "*"),
}


def _templates():
    out = {}
    for name in os.listdir(TEMPLATES):
        if name.endswith((".html", ".xml", ".js", ".txt")):
            with open(os.path.join(TEMPLATES, name), encoding="utf-8") as fh:
                out[name] = fh.read()
    return out


def _uses(templates, name, var, seen=None):
    """Whether a template or anything it pulls in mentions the name."""
    seen = seen or set()
    if name in seen or name not in templates:
        return False
    seen.add(name)
    body = templates[name]
    if re.search(rf"\b{re.escape(var)}\b", body):
        return True
    for child in re.findall(r'(?:include|extends|import)\s+"([^"]+)"', body):
        if _uses(templates, child, var, seen):
            return True
    return False


def run():
    s = Suite("Work thrown away")

    templates = _templates()
    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)

    # A child template can use what its parent was handed.
    children = {}
    for name, body in templates.items():
        for parent in re.findall(r'extends\s+"([^"]+)"', body):
            children.setdefault(parent, []).append(name)

    wasted = []
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "render_template" and node.args):
            continue
        target = node.args[0]
        if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
            continue
        tmpl = target.value
        for kw in node.keywords:
            if kw.arg is None:
                continue
            # Only what costs something. A literal is context, not work.
            if not isinstance(kw.value, (ast.Call, ast.Name, ast.Subscript)):
                continue
            checked += 1
            if (tmpl, kw.arg) in KNOWN or (tmpl, "*") in KNOWN:
                continue
            if _uses(templates, tmpl, kw.arg):
                continue
            if any(_uses(templates, c, kw.arg) for c in children.get(tmpl, [])):
                continue
            wasted.append(f"{tmpl} <- {kw.arg} (app.py:{node.lineno})")

    s.section("Nothing is computed for a page that ignores it")
    s.check(f"all {checked} computed values reach the page they are sent to",
            not wasted,
            detail=("thrown away: " + "; ".join(sorted(wasted)[:4])) if wasted else "")

    s.section("The sweep can actually see a use")
    # Without this the check above passes on everything the moment the
    # template reader breaks, which is the failure it exists to prevent.
    s.check("a variable a template uses is seen",
            _uses(templates, "book_rooms.html", "featured_reviews"),
            detail="featured_reviews is rendered on the booking page; if this "
                   "reads as unused the sweep is blind and passes everything")
    s.check("and one nothing uses is not",
            not _uses(templates, "book_rooms.html", "zz_not_a_real_variable"),
            detail="if this reads as used the sweep passes everything too")
    s.check("a use inside an included partial counts",
            _uses(templates, "owner_home.html", "cells")
            or _uses(templates, "management_texting.html", "overview"),
            detail="overview bands are rendered by _overview_band.html, so a "
                   "sweep that ignored includes would report every band as dead")

    s.section("The known exceptions are still exceptions")
    # A list that quietly stops matching anything is a list that stops
    # protecting anything.
    stale = []
    for tmpl, var in sorted(KNOWN):
        if var == "*":
            if tmpl not in templates:
                stale.append(f"{tmpl} (gone)")
            continue
        if tmpl not in templates:
            stale.append(f"{tmpl} (gone)")
        elif _uses(templates, tmpl, var):
            stale.append(f"{tmpl} <- {var} (now used)")
    s.check("every exception still describes something real", not stale,
            detail=("no longer needed: " + ", ".join(stale[:4])) if stale else
                   f"{len(KNOWN)} exception(s)")

    return s


if __name__ == "__main__":
    print(run().report())
