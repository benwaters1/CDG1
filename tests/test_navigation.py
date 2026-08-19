"""One door per room.

The staff app grew a category per feature, and the cost lands on whoever is
looking for something: Company Info listed the insurance broker while the
policies sat on a separate page, and Vehicles was reachable from two different
menus. Both are the same fault — one answer, two places to look.

These checks are about consolidation holding rather than quietly coming apart
again: that the merged page really carries both halves, that every address
which used to work still does, and that nothing on the Management page offers
the same destination twice.

The last one is written as a rule rather than a list, so it catches the next
duplicate as well as today's.
"""
import os
import re

from _harness import Suite, ROOT, clients
import _harness

m = _harness.m


def run():
    s = Suite("Navigation")
    oc, ec, owner, emp = clients()

    s.section("Company and insurance are one page")
    with m.app.test_request_context():
        url = m.url_for("management_company_info")
    body = oc.get(url).get_data(as_text=True)
    s.check("it opens", "Company" in body)
    s.check("the company half is there", "Legal &amp; Registration" in body
            or "Legal & Registration" in body)
    s.check("and the insurance half with it",
            "Add a policy" in body, detail="the policies section is missing")
    s.check("the broker and the policies are no longer in different places",
            "insurance_broker_name" in body and 'id="insurance"' in body)

    s.section("The old address still works")
    # Bookmarks, and the dashboard's expiry links, point at the old page.
    r = oc.get("/management/insurance")
    s.check("it redirects rather than 404s", r.status_code in (301, 302),
            detail=f"HTTP {r.status_code}")
    s.check("and lands on the insurance half of the new page",
            "#insurance" in r.headers.get("Location", ""),
            detail=f"went to {r.headers.get('Location')!r}")

    s.section("Adding a policy comes back to the same page")
    r = oc.post("/management/insurance/new", data={"provider": "ZZNAV Test Assurance"})
    s.check("it returns to the company page",
            "company-info" in r.headers.get("Location", ""),
            detail=f"went to {r.headers.get('Location')!r}")
    conn = _harness.db()
    conn.execute("DELETE FROM insurance_policies WHERE provider = 'ZZNAV Test Assurance'")
    conn.commit()
    conn.close()

    s.section("Nothing is offered twice on the Management page")
    # Written as a rule, not a list, so the next duplicate is caught too.
    page = open(os.path.join(ROOT, "templates", "management.html"), encoding="utf-8").read()
    # Only the cards. A sentence in a summary can reasonably link to the same
    # place a card goes ("…see full financials"); two cards to one destination
    # is the thing that makes a menu feel bigger than it is.
    targets = re.findall(
        r"""<a href="\{\{ url_for\('([a-z_]+)'\)[^>]*class="action-card""", page)
    seen, twice = set(), []
    for endpoint in targets:
        if endpoint in seen:
            twice.append(endpoint)
        seen.add(endpoint)
    s.check(f"each of the {len(seen)} cards goes somewhere different", not twice,
            detail=", ".join(twice))

    s.section("And nothing points at a page that no longer exists")
    missing = [ep for ep in set(targets) if ep not in m.app.view_functions]
    s.check("every card's destination is a real route", not missing,
            detail=", ".join(sorted(missing)))

    s.section("Vehicles has one home, not two")
    s.check("the Management page no longer duplicates Estate's vehicles",
            "management_vehicles" not in targets,
            detail="vehicles is offered from two menus again")
    nav = open(os.path.join(ROOT, "templates", "base.html"), encoding="utf-8").read()
    s.check("but it is still reachable from Estate",
            "management_vehicles" in nav, detail="the vehicles link was lost entirely")

    s.section("Payroll stays out of Team")
    # Deliberate: payroll used to sit inside the team area, which let anyone
    # with team access open the payroll pack. It is its own access area and
    # consolidating categories must not quietly undo that.
    s.check("payroll is still its own access area", "payroll" in m.NAV_AREAS,
            detail="payroll folded back into another area")
    team_pages = set(m.NAV_AREAS.get("team", []))
    payroll_pages = set(m.NAV_AREAS.get("payroll", []))
    s.check("and none of its pages leaked into team", not (team_pages & payroll_pages),
            detail=", ".join(sorted(team_pages & payroll_pages)))

    return s
