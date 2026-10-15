"""The sidebar, rebuilt as data on 15 October 2026.

The owner could not find the photo mirror (it was in no menu at all), nor
where to add somebody to the team, and the menu was fifteen entries in the
order they were written: "it'll be so hard to get Jasmine and Mum to use it".
It is now OWNER_MENU and STAFF_MENU in app.py, drawn by a loop in base.html.

What this guards is what a reshuffle quietly costs:

  A PAGE DROPPED. Moving a hundred lines between groups is how one goes
  missing, and a page in no menu is found only by somebody who already knows
  its address. Every page the old menus offered is listed below and must
  still be offered.

  A NAME THAT DOES NOT BUILD. The builder skips a link whose page has been
  renamed, so the sidebar survives it -- which means it would vanish without a
  sound. Every endpoint in both menus is checked to be a real page.

  A DOOR THAT REFUSES. A link is drawn only for somebody who can open it; the
  group label points at the first link they can open, never one they cannot.
"""
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZMENU"

# Every page the admin menu offered before the rebuild (base.html at 8e5398f).
OLD_ADMIN_PAGES = {
    "admin_access", "admin_access_levels", "admin_approvals", "admin_assets",
    "admin_automation", "admin_bookings", "admin_calendar", "admin_compliance",
    "admin_email_outbox", "admin_emails", "admin_events", "admin_extras",
    "admin_feedback", "admin_gallery", "admin_hr", "admin_hr_notes", "admin_images",
    "admin_inbox_flags", "admin_incidents", "admin_leave", "admin_overview",
    "admin_payroll", "admin_promo_codes", "admin_readiness", "admin_refunds",
    "admin_reports", "admin_restaurant", "admin_restaurant_settings",
    "admin_restaurant_waitlist", "admin_rooms", "admin_shifts", "admin_stock",
    "admin_tasks", "admin_tax", "admin_terminal", "admin_timesheets",
    "admin_transactions", "admin_waitlist", "admin_workshop_feedback",
    "admin_workshop_registrations", "admin_workshop_waitlist", "admin_workshops",
    "all_transfers", "announcements", "arrivals_sheet_page", "audit_log",
    "balances_to_collect", "breakfast", "camera_roll", "candidates", "changes_log",
    "channel_stays_page", "chat_home", "contacts", "cover_gaps_page", "dashboard",
    "dinner_covers_page", "directory", "discount_cost_page", "door_lock_page",
    "equipment_overview", "expenses", "extras_due_page", "guest_dates", "guests",
    "held_not_earned_page", "import_catalogue", "import_guests", "kitchen_fridges",
    "kitchen_prep", "mail_log", "management", "management_bank_details",
    "management_booking_com", "management_break_even", "management_breakages",
    "management_cash_banking", "management_cleaning", "management_company_info",
    "management_documents", "management_email_templates", "management_financials",
    "management_lost_property", "management_meters", "management_on_the_books",
    "management_outstanding", "management_payment_cost", "management_recurring_costs",
    "management_vault", "management_vehicles", "management_visitors", "manual",
    "meetings_page", "ops_calendar", "overtime_page", "pos_home", "reports_index",
    "restaurant_covers", "restaurant_tables", "restoration_record", "room_access_page",
    "room_checks_page", "room_faults_page", "room_issues", "rota_clashes_page",
    "rota_templates_page", "rota_vs_clock_page", "shift_handover", "shopping_list",
    "skills_page", "spend_by_vendor_page", "staff_today", "supplier_agreements_page",
    "team_calendar", "vendors", "website_analytics",
}
# And every page the plain-staff menu offered.
OLD_STAFF_PAGES = {
    "all_transfers", "announcements", "ask_hr", "breakfast", "contacts", "directory",
    "extras_due_page", "guest_dates", "guests", "kitchen_fridges", "kitchen_prep",
    "management_breakages", "management_cleaning", "management_lost_property",
    "management_meters", "management_visitors", "manual", "mileage",
    "my_availability", "my_expenses", "my_hours", "my_leave", "my_reviews",
    "my_shifts", "ops_calendar", "pos_home", "profile", "restaurant_covers",
    "restaurant_tables", "room_checks_page", "room_issues", "shift_handover",
    "shopping_list", "team_calendar",
}


def _endpoints(menu):
    return [i["endpoint"] for g in menu for _h, items in g[5] for i in items]


def _nav(page):
    return page[page.find('<nav class="nav">'):page.find("</nav>")]


def _groups(page):
    """[(label, [hrefs])] as drawn."""
    out = []
    for label, body in re.findall(
            r'class="nav-dropdown-label[^"]*">(?:<svg.*?</svg>)?\s*([^<]+?)(?:\s*\(\d+\))?</a>'
            r'.*?<div class="nav-dropdown-menu">(.*?)</div>', _nav(page), re.S):
        out.append((label.strip().replace("&amp;", "&"),
                    re.findall(r'<a href="([^"]+)"', body)))
    return out


def run():
    s = Suite("The sidebar")
    oc, ec, _o, _e = clients()
    top = [i["endpoint"] for i in m.NAV_TOP]
    admin = set(top) | set(_endpoints(m.OWNER_MENU))
    staff = set(top) | set(_endpoints(m.STAFF_MENU))

    s.section("Nothing was lost in the move")
    s.check("every page the admin menu offered is still offered",
            not OLD_ADMIN_PAGES - admin, detail=str(sorted(OLD_ADMIN_PAGES - admin)))
    s.check("and every page the staff menu offered",
            not OLD_STAFF_PAGES - staff, detail=str(sorted(OLD_STAFF_PAGES - staff)))
    unreal = sorted((admin | staff) - set(m.app.view_functions))
    s.check("every page either menu names is a real page", not unreal,
            detail=f"{unreal} — the builder skips a name that does not build, so a "
                   "renamed page would leave the menu without a sound")

    s.section("What the owner could not find")
    page = oc.get("/today").get_data(as_text=True)
    with m.app.test_request_context("/"):
        mirror, add = m.url_for("admin_photo_mirror"), m.url_for("new_employee")
    drawn = dict(_groups(page))
    s.check("the photo mirror is in the menu, under Website & Email",
            mirror in drawn.get("Website & Email", []), detail=str(list(drawn)))
    s.check("adding somebody to the team is under Employees",
            add in drawn.get("Employees", []), detail=str(drawn.get("Employees")))

    s.section("A menu somebody can read")
    s.check("ten groups or fewer under the four at the top",
            len(m.OWNER_MENU) <= 10 and len(m.NAV_TOP) == 4,
            detail=f"{len(m.OWNER_MENU)} groups, {len(m.NAV_TOP)} at the top")
    long_flat = [g[1] for g in m.OWNER_MENU
                 if any(h is None for h, _i in g[5]) and len(_endpoints([g])) > 8]
    s.check("no group of more than eight runs as one list without headings",
            not long_flat, detail=str(long_flat))
    twice = [g[1] for g in m.OWNER_MENU + m.STAFF_MENU
             if len(_endpoints([g])) != len(set(_endpoints([g])))]
    s.check("no group offers the same page twice", not twice, detail=str(twice))
    s.check("the owner sees every group", len(drawn) == len(m.OWNER_MENU),
            detail=str(list(drawn)))

    s.section("A member of staff gets their own day, not the back office")
    page = ec.get("/today").get_data(as_text=True)
    labels = [l for l, _h in _groups(page)]
    s.check("their five groups", labels == [g[1] for g in m.STAFF_MENU], detail=str(labels))
    adapter = m.app.url_map.bind("localhost")
    guarded = []
    for href in re.findall(r'href="(/[^"#?]*)', _nav(page)):
        try:
            ep, _a = adapter.match(href, method="GET")
        except Exception:
            continue
        if m.view_is_guarded(ep):
            guarded.append(ep)
    s.check("and no link to a page behind an area", not guarded, detail=str(guarded))

    s.section("Somebody with part of the house sees their part")
    from test_areas import _preset_client
    pc = _preset_client(["restaurant", "till"])
    page = pc.get("/today").get_data(as_text=True)
    groups = _groups(page)
    labels = [l for l, _h in groups]
    s.check("the restaurant, and nothing of the money or the people",
            "Restaurant" in labels and "Financial" not in labels
            and "Employees" not in labels, detail=str(labels))
    refused = []
    for label, hrefs in groups:
        for href in hrefs:
            if pc.get(href).status_code in (403,):
                refused.append(f"{label}: {href}")
    for label_href in re.findall(r'<a href="([^"]+)" class="nav-dropdown-label', _nav(page)):
        if pc.get(label_href).status_code == 403:
            refused.append(f"group label: {label_href}")
    s.check("and every link and every group label opens for them", not refused,
            detail=str(refused[:6]))
    conn = db()
    conn.execute("DELETE FROM users WHERE email LIKE 'zzarea%@example.invalid'")
    conn.execute("DELETE FROM access_presets WHERE slug LIKE 'zzarea%'")
    conn.commit()
    conn.close()

    s.section("A page knows its group")
    for path, want in (("/admin/photo-mirror", "Website & Email"),
                       ("/directory/new", "Employees"),
                       ("/admin/bookings", "Accommodation"),
                       ("/management/booking-com", "Accommodation")):
        body = oc.get(path).get_data(as_text=True)
        opened = [o.strip().replace("&amp;", "&") for o in re.findall(
            r'<div class="nav-dropdown open">\s*<a href="[^"]*" class="nav-dropdown-label[^"]*">'
            r'(?:<svg.*?</svg>)?\s*([^<]+?)(?:\s*\(\d+\))?</a>', body, re.S)]
        s.check(f"{path} opens {want}", opened == [want], detail=str(opened))
    return s


if __name__ == "__main__":
    print(run().report())
