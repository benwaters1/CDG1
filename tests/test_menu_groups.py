"""The two longest menus read in groups, and two pages that were one are one.

WHAT WENT WRONG. Financial held twenty links and Management twenty-two, each
in the order the pages happened to be built, so the menu had to be read top
to bottom every time. And eight pages answered questions about money owed or
coming in; two of them -- What we are owed and Debtor Ageing -- read the very
same debts, one as a list and one by age, from two menu entries.

WHAT THIS PINS.
  - Financial and Management are in headed groups, none of them empty, and
    every page that was in either menu still is, once.
  - Debtor Ageing is a section of What we are owed: the old address lands on
    it, its figures are the list's, and the menu no longer offers it twice.
"""
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m

# What each menu held before it was grouped, less what was merged away.
FINANCIAL = {
    "admin_approvals", "expenses", "admin_refunds", "management_outstanding",
    "balances_to_collect", "held_not_earned_page", "management_on_the_books",
    "management_recurring_costs", "supplier_agreements_page", "spend_by_vendor_page",
    "management_payment_cost", "discount_cost_page", "management_financials",
    "admin_transactions", "management_cash_banking", "admin_reports",
    "management_break_even", "admin_tax", "management_bank_details",
}
MANAGEMENT = {
    "management", "admin_automation", "admin_access_levels", "management_company_info",
    "admin_promo_codes", "management_vault", "admin_readiness", "reports_index",
    "management_documents", "meetings_page", "restoration_record", "camera_roll",
    "channel_stays_page", "vendors", "admin_gallery", "admin_images",
    "import_catalogue", "import_guests", "audit_log", "mail_log", "changes_log",
}


def _menu(page, label):
    """The groups of one dropdown menu: [(heading, [hrefs])]."""
    at = page.find(f"</span>{label}</a>")
    if at < 0:
        at = page.find(f">{label}</a>")
    a = page.find('<div class="nav-dropdown-menu">', at)
    b = page.find("</div>", a)
    body = page[a:b]
    groups, current = [], None
    for tag in re.finditer(r'<span class="nav-subhead">([^<]+)</span>|<a href="([^"]+)"', body):
        if tag.group(1):
            current = (tag.group(1), [])
            groups.append(current)
        elif current is not None:
            current[1].append(tag.group(2))
        else:
            groups.append(("(no heading)", [tag.group(2)]))
    return groups


def run():
    s = Suite("menus in groups, and one page where there were two")
    oc, ec, _o, _e = clients()
    page = oc.get("/management/financials").get_data(as_text=True)
    with m.app.test_request_context():
        url = {ep: m.url_for(ep) for ep in FINANCIAL | MANAGEMENT}

    s.section("Financial and Management read in groups")
    for label, want in (("Financial", FINANCIAL), ("Management", MANAGEMENT)):
        groups = _menu(page, label)
        hrefs = [h for _h, links in groups for h in links]
        s.check(f"{label}: every link sits under a heading",
                groups and all(h != "(no heading)" for h, _l in groups),
                detail=str([h for h, _l in groups]))
        s.check(f"{label}: no heading is left with nothing under it",
                all(links for _h, links in groups),
                detail=str([h for h, links in groups if not links]))
        s.check(f"{label}: four or five groups, not one long list",
                4 <= len(groups) <= 6, detail=f"{len(groups)} groups")
        missing = sorted(ep for ep in want if url[ep] not in hrefs)
        twice = sorted({h for h in hrefs if hrefs.count(h) > 1})
        s.check(f"{label}: every page it held is still in it, once", not missing and not twice,
                detail=f"missing {missing}, twice {twice}")
    css = open("static/style.css", encoding="utf-8").read()
    s.check("the headings are styled as headings, not links",
            re.search(r"\.nav-subhead\s*\{[^}]*text-transform:\s*uppercase", css))

    s.section("Debtor Ageing is a section of What we are owed")
    r = oc.get("/management/debtors")
    s.check("the old address lands on the section",
            r.status_code == 302 and r.headers.get("Location", "").endswith(
                "/management/outstanding#ageing"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    owed = oc.get("/management/outstanding")
    body = owed.get_data(as_text=True)
    s.check("What we are owed draws", owed.status_code == 200)
    conn = db()
    rows = m.outstanding_balances(conn)
    ageing = m.debtor_ageing(conn)
    conn.close()
    s.check("its ageing reads the same debts as its list",
            abs(ageing["total"] - round(sum(float(r_["owed"]) for r_ in rows), 2)) < 0.01
            and len(ageing["items"]) == len(rows),
            detail=f"ageing {ageing['total']} over {len(ageing['items'])}, list "
                   f"{sum(float(r_['owed']) for r_ in rows):.2f} over {len(rows)}")
    if ageing["items"]:
        s.check("and the page shows the shape", 'id="ageing"' in body
                and all(ageing["labels"][k] in body for k in ageing["order"]))
    else:
        s.check("with nothing owed there is nothing to age, and the page says so",
                'id="ageing"' not in body)
    s.check("the age export is still reachable",
            oc.get("/management/debtors/export.csv").status_code == 200)
    s.check("and the menu offers it once, not twice",
            ">Debtor Ageing</a>" not in page and 'href="/management/debtors"' not in page)
    s.check("an employee is still refused", ec.get("/management/debtors").status_code in (302, 403))
    return s


if __name__ == "__main__":
    print(run().report())
