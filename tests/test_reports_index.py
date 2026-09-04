"""Thirty-six reports existed that no page linked to.

They were in the command palette, which finds a page for somebody who already
knows it is there. Nobody wakes up knowing this house has a table-utilisation
report. Every one had been built, tested and shipped, and then had no address
a person could arrive at — the voids page written the same morning included.

The rule is already in the repo, applied to findings and never to the reports
themselves: a check nobody opens is worth nothing.

WHAT THE INDEX HAS TO GET RIGHT.

  THE DESCRIPTION COMES FROM THE PAGE. Read off each view's docstring at
  request time rather than kept in a second list beside the palette. A
  description held apart from the code drifts from it, and a page being
  different from what somebody believes it is is the exact failure this fixes.

  IT NAMES WHAT IT CANNOT DESCRIBE. Sixty-one pages have no docstring first
  line. Listing them blank would read as sixty-one reports that do nothing;
  naming them is a job somebody can finish in a minute each. The number is
  pinned here so it can only go down.

  AND THE INDEX ITSELF MUST BE REACHABLE. An index of unreachable pages that
  is itself unreachable is the same joke twice.
"""
import glob
import inspect
import re

from _harness import Suite, clients

import _harness

m = _harness.m

# Nought, and it must stay nought. It was sixty-one when the index was built:
# the index named them rather than showing blanks, and then they were written.
# A page added without a first line of its own would otherwise join a list
# nobody is looking at any more, which is how it got to sixty-one.
UNDESCRIBED_TODAY = 0


def _linked_endpoints():
    """Every endpoint a template actually points at, comments stripped.

    Comments stripped because a url_for inside {# #} is a mention, not a way
    in — a mistake made four times in this repo before it was written down.
    """
    text = ""
    for path in glob.glob("templates/**/*.html", recursive=True):
        raw = open(path, encoding="utf-8", errors="replace").read()
        text += re.sub(r"\{#(?:.|\n)*?#\}", " ", raw)
    return set(re.findall(r"url_for\(\s*['\"](\w+)['\"]", text))


def _describes_live():
    """Swap a view's __doc__ and see whether the catalogue notices.

    Restored immediately: the module is shared with every suite after this
    one, and leaving a rewritten docstring behind is the same fault as the
    suite that left five automations switched off.
    """
    view = m.app.view_functions["admin_voids"]
    was = view.__doc__
    try:
        view.__doc__ = "A sentence no page has ever contained."
        with m.app.test_request_context("/reports"):
            rows = m.report_catalogue()
        got = next(r["what"] for r in rows if r["endpoint"] == "admin_voids")
        return got.startswith("A sentence no page has ever contained")
    finally:
        view.__doc__ = was


def run():
    s = Suite("Every report the house has, and how to find it")
    oc, ec, _owner, _emp = clients()

    with m.app.test_request_context("/reports"):
        rows = m.report_catalogue()
    by_endpoint = {r["endpoint"] for r in rows}

    s.section("It knows about every page the palette knows about")
    s.check("nothing in the palette is missing from the index",
            {e for _l, e, _k in m.PALETTE_PAGES if e in m.app.view_functions}
            <= by_endpoint,
            detail=str(len(rows)))
    s.check("and it is a real number of them", len(rows) > 100,
            detail=str(len(rows)))

    s.section("The description is the page's own")
    voids = next((r for r in rows if r["endpoint"] == "admin_voids"), None)
    s.check("a report is described", voids and voids["described"],
            detail=str(voids))
    s.check("in the words of its own docstring",
            voids and voids["what"].startswith(
                inspect.getdoc(m.app.view_functions["admin_voids"]).split("\n")[0]),
            detail=str(voids and voids["what"])[:120])
    # A check saying "the description is not kept in a second list" was here
    # and ended in `or True`, so it passed whatever the code did. Replaced
    # with one that can fail: change the docstring and the page must change
    # with it, which is the property that matters.
    s.check("change the page's docstring and the index changes with it",
            _describes_live(),
            detail="a description read at request time cannot drift from the "
                   "page; one copied into a list beside the palette can")

    s.section("It says which pages cannot describe themselves")
    undescribed = [r for r in rows if not r["described"]]
    s.check("every page can say what it does",
            len(undescribed) <= UNDESCRIBED_TODAY,
            detail=f"{len(undescribed)} against a pinned {UNDESCRIBED_TODAY}: "
                   f"{[r['label'] for r in undescribed][:6]} — a page that "
                   "cannot describe itself is one nobody can find by "
                   "searching for what they want to know")
    # The direct one, and it needs a blank page to look at. There are none
    # left -- there were sixty-one when this index was built -- so one is
    # made briefly and put back, the same way the live-docstring check works.
    #
    # It is the property that let sixty-one build up in the first place: the
    # index saying a page is described when it is not. The ceiling above
    # cannot catch that, because a ceiling passes when the number falls.
    view = m.app.view_functions["admin_voids"]
    was_doc = view.__doc__
    try:
        view.__doc__ = None
        with m.app.test_request_context("/reports"):
            blanked = m.report_catalogue()
        hit = next(r for r in blanked if r["endpoint"] == "admin_voids")
        s.check("a page with no docstring is reported as having none",
                hit["described"] is False and not hit["what"],
                detail=str(hit)[:150])
        s.check("and the page names it out loud",
                "cannot say what they do"
                in oc.get("/reports").get_data(as_text=True),
                detail="a blank line reads as a report that does nothing; a "
                       "named one is a job somebody can finish in a minute")
    finally:
        view.__doc__ = was_doc
    s.check("and the docstring is put back",
            bool((inspect.getdoc(view) or "").strip()),
            detail="every suite after this one reads the same module")

    s.check("and every report a page does not link to can describe itself",
            not [r for r in rows
                 if r["endpoint"] not in _linked_endpoints()
                 and not r["described"]],
            detail="these are the ones only this index can lead anybody to, "
                   "so a blank line beside one is the whole page wasted")

    s.section("It is grouped by the part of the house, from one source")
    body = oc.get("/reports").get_data(as_text=True)
    s.check("the page renders", oc.get("/reports").status_code == 200)
    s.check("with the voids report on it", "Voids" in body)
    s.check("and a report from another part of the house entirely",
            "Exit interviews" in body or "Mileage" in body)
    s.check("the grouping is the navigation's own",
            all(r["area"] == (m.NAV_AREA_OF.get(r["endpoint"]) or "other")
                for r in rows),
            detail="two ideas of which area a page is in is how a page ends "
                   "up filed under one heading and linked from another")

    s.section("Searching finds it by the question, not the name")
    hit = oc.get("/reports?q=who%20voided").get_data(as_text=True)
    s.check("a phrase nobody put in the title still finds the page",
            "Voids" in hit,
            detail="the palette keywords are searched too, which is what "
                   "makes 'theft' find the voids report")
    s.check("and it narrows rather than listing everything",
            "Exit interviews" not in hit,
            detail="a search that returns the whole list has not searched")

    s.section("The index is itself reachable")
    s.check("a template links to it",
            "reports_index" in _linked_endpoints(),
            detail="an index of unreachable pages that is itself unreachable "
                   "is the same joke twice")
    s.check("and it is in the palette as well",
            "reports_index" in {e for _l, e, _k in m.PALETTE_PAGES})

    s.section("It is the owner's")
    s.check("an employee cannot open it", ec.get("/reports").status_code == 403,
            detail="it lists the money reports, the payroll pack and the "
                   "access register by name")

    return s


if __name__ == "__main__":
    print(run().report())
