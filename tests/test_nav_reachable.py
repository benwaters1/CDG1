"""Every owner page can be reached by looking, not only by knowing.

Nine pages were built and none of them went into the visible navigation.
They were reachable three ways: by searching the palette for the right
word, by a cross-link from a sibling page, or by the warnings panel — which
fires once it is already too late to move a shift. A page you can only find
when you already know it exists is a page most people never find.

This checks the two places a page has to appear: the dropdown a person
browses, and the palette a person searches. Missing from both is invisible;
missing from one is worth knowing about.
"""
import re

from _harness import Suite, clients
import _harness

m = _harness.m

# Pages that deliberately live outside the nav, with the reason. Anything not
# listed here and not in the nav is a page somebody forgot.
OFF_NAV = {
    # Reached from the thing they belong to, not browsed on their own.
    "profile", "edit_employee", "admin_report", "view_document",
    # Public, kiosk and print surfaces have their own chrome entirely.
    "office_display", "staff_today", "privacy_page", "terms_page",
}


def _owner_pages():
    """Endpoints in the palette — the app's own list of what a person may go to."""
    return {endpoint for _label, endpoint, _kw in m.PALETTE_PAGES}


def run():
    s = Suite("navigation")
    oc, _ec, _owner, _emp = clients()

    home = oc.get("/").get_data(as_text=True)
    nav = home[:home.find("</nav>")] if "</nav>" in home else home
    linked = set(re.findall(r'href="(/[^"?#]*)"', nav))

    s.section("The nav is actually there to check against")
    s.check("the owner home renders a nav", "</nav>" in home)
    s.check("and it has a reasonable number of links in it", len(linked) > 20,
            detail=str(len(linked)))

    s.section("This week's pages are browsable, not only searchable")
    # Named explicitly rather than derived: these are the ones that went in
    # without nav entries, and naming them is what stops the check rotting
    # into "whatever happens to be there".
    built = {
        "/admin/rota-clashes": "Rota clashes",
        "/admin/cover": "Nobody on",
        "/admin/rota-vs-clock": "Rota vs clock",
        "/admin/skills": "Who can do what",
        "/admin/overtime": "Long weeks",
        "/admin/room-faults": "Rooms sold with a fault",
        "/management/spend-by-vendor": "Spend by supplier",
        "/management/discounts": "What discounts cost",
        "/management/held-not-earned": "Held, not earned",
    }
    for path, name in sorted(built.items()):
        s.check(f"{name} is in the nav", path in linked,
                detail="only reachable by searching for it")

    s.section("And findable by searching, for people who do that instead")
    palette = _owner_pages()
    for endpoint in ("rota_clashes_page", "cover_gaps_page", "rota_vs_clock_page",
                     "skills_page", "overtime_page", "room_faults_page",
                     "spend_by_vendor_page", "discount_cost_page",
                     "held_not_earned_page"):
        s.check(f"{endpoint} is in the palette", endpoint in palette)

    s.section("Every page the palette offers actually exists")
    # A palette entry for a route that has been renamed is a dead end that
    # only shows itself when somebody searches for it.
    missing = [e for _l, e, _k in m.PALETTE_PAGES if e not in m.app.view_functions]
    s.check("no palette entry points at a route that is gone", not missing,
            detail=", ".join(missing))

    s.section("And no owner page exists that neither list mentions")
    # THIS is the check the file was missing, and the reason it missed it is
    # worth keeping: everything above starts from the palette or from a
    # hand-written list of paths, so a page absent from BOTH — which is the
    # state this whole suite exists to prevent — was invisible to it. Twenty-one
    # pages were in exactly that state, including What's On, Money Ahead, the
    # till journal, wages and VAT.
    #
    # Started from the routing table instead, which is the only list that
    # cannot be forgotten to update.
    lost = sorted(_unlisted(linked))
    s.check("every owner page is in the nav or the palette", not lost,
            detail="built and reachable only by typing the URL: " + ", ".join(lost))

    return s


# Surfaces that are deliberately not browsed, with the reason. Anything not
# here and not in the nav or palette is a page somebody forgot.
NOT_BROWSED = (
    # Polled by the page that owns them; a person never opens these.
    "admin_inbox_flags_status", "admin_overview_status",
    # Rendered inside Outlook's own pane, which has no nav of ours.
    "admin_outlook_addin",
)


def _unlisted(linked):
    """Owner pages in neither the nav nor the palette, excluding downloads,
    new-record forms and print sheets — all of which are reached from the
    thing they belong to and would only clutter a menu."""
    palette = _owner_pages()
    nav_endpoints = set()
    adapter = m.app.url_map.bind("localhost")
    for path in linked:
        try:
            nav_endpoints.add(adapter.match(path, method="GET")[0])
        except Exception:
            continue
    for rule in m.app.url_map.iter_rules():
        path = str(rule.rule)
        if "GET" not in rule.methods or "<" in path:
            continue
        if not path.startswith(("/admin", "/management")):
            continue
        ep = rule.endpoint
        if ep in palette or ep in nav_endpoints or ep in NOT_BROWSED:
            continue
        if ep.startswith(("export_", "new_")) or path.endswith((".csv", ".json",
                                                                "/new", "/template")):
            continue
        yield ep


if __name__ == "__main__":
    print(run().report())
