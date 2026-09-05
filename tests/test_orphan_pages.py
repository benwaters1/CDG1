# -*- coding: utf-8 -*-
"""A real page that nothing links to — the other half of test_links.

test_links checks every link points at a real page. Nothing checked the
reverse: a real page nothing points at. It renders, it answers, its tests
pass, and the only way anybody reaches it is by knowing the address, which
nobody does, because nobody has been told it is there. A feature the house
paid for and cannot use, invisible precisely because everything about it
works.

Of 430 pages, 82 had no literal url_for anywhere. Eighty of those are right
and are listed below with the reason each is reached another way. Twenty-
seven more were reachable after all — through the reports index and the
section pages, which build their links from a catalogue, so the endpoint name
never appears as a literal and a source sweep cannot see it. This suite
therefore asks the RENDERED pages what addresses they hand out.

Two were simply lost:

  /events/find looks up an event enquiry by its reference and the address it
  came from — the wedding equivalent of "Manage a booking", which sits in the
  footer of every page. Somebody who had enquired about a wedding had nowhere
  to go, and no way to learn this existed.

  /social exists, by its own docstring, because "the house's own photographs
  are the strongest thing it has and they were reachable only by leaving the
  site". The footer offered Instagram and Facebook and not the page written
  to keep people here.
"""
import glob
import io
import os
import re

from _harness import Suite, clients
import _harness

m = _harness.m

# Pages reached without anything linking to them, and how each is reached.
# A dictionary rather than a list so the reason travels with the name: an
# unexplained allow-list is where a genuine orphan hides.
NOT_LINKED_ON_PURPOSE = {
    # Old addresses from the site the house published for a decade. Reached
    # by an inbound link or a bookmark; linking them would be circular.
    "legacy_about": "old address", "legacy_accommodation": "old address",
    "legacy_ateliers": "old address", "legacy_bedrooms": "old address",
    "legacy_blog": "old address", "legacy_contact_us": "old address",
    "legacy_courses": "old address", "legacy_dining": "old address",
    "legacy_food": "old address", "legacy_history": "old address",
    "legacy_journal": "old address", "legacy_la_table": "old address",
    "legacy_news": "old address", "legacy_retreats": "old address",
    "legacy_rooms": "old address", "legacy_stay": "old address",
    "legacy_the_chateau": "old address", "legacy_visit": "old address",
    "legacy_weddings": "old address",
    # Stripe sends the guest back to these.
    "booking_stripe_success": "Stripe returns here",
    "stripe_success": "Stripe returns here",
    "stripe_cancel": "Stripe returns here",
    "share_payment_success": "Stripe returns here",
    "restaurant_stripe_success": "Stripe returns here",
    "restaurant_stripe_cancel": "Stripe returns here",
    "workshop_stripe_success": "Stripe returns here",
    "workshop_stripe_cancel": "Stripe returns here",
    "event_stripe_success": "Stripe returns here",
    # Reached by a link in an email, which is the whole point of the token.
    "onboard": "link in an email", "workshop_feedback": "link in an email",
    "instructor_page": "link in an email",
    "newsletter_confirm": "link in an email",
    "newsletter_unsubscribe": "link in an email",
    "campaign_unsubscribe": "link in an email",
    "reset_password": "link in an email",
    "room_ics_feed": "subscribed to in a calendar app",
    # Fetched by software, not clicked by anybody.
    "robots": "fetched by crawlers", "sitemap": "fetched by crawlers",
    "service_worker": "registered by the browser",
    "api_palette": "fetched by the command palette",
    "api_owner_digest": "fetched by the digest job",
    "api_sync_ical": "fetched by the sync job",
    "api_guest_lookup": "fetched by a form as somebody types",
    "api_draft_reply": "fetched by the add-in",
    "api_check_send_conflict": "fetched by the add-in",
    "notifications_unread_count": "polled by the page",
    "admin_overview_status": "polled by the overview",
    "mirrored_photo": "served in place of a hotlinked image",
    "outlook_addin_manifest": "loaded by Outlook",
    "outlook_addin_taskpane": "loaded by Outlook",
    "outlook_addin_compose": "loaded by Outlook",
    "outlook_addin_launchevent_html": "loaded by Outlook",
    "outlook_addin_launchevent_js": "loaded by Outlook",
}

# The pages that build their links from a catalogue rather than a literal, so
# a source sweep cannot see them. Asked of the rendered page instead.
INDEXES = (
    "/", "/reports", "/management", "/admin/restaurant", "/admin/overview",
    "/admin/hr", "/admin/rooms", "/admin/bookings", "/guests", "/admin/shifts",
    "/pos", "/admin/workshops", "/admin/events", "/admin/finance",
    "/admin/settings", "/book", "/workshops", "/events", "/contact",
)


def run():
    s = Suite("Pages nothing links to")
    oc, _ec, _owner, _emp = clients()

    served = set()
    for path in INDEXES:
        try:
            page = oc.get(path).get_data(as_text=True)
        except Exception:
            continue
        served.update(h.split("?")[0] for h in re.findall(r'href="([^"#?]+)', page))
    try:
        palette = oc.get("/api/palette").get_data(as_text=True)
        served.update(h.split("?")[0] for h in
                      re.findall(r'"(?:url|href|path)"\s*:\s*"([^"]+)"', palette))
    except Exception:
        pass
    # And every address a template writes as a literal. Both signals are
    # needed: the section pages build their links from a catalogue, so the
    # endpoint name never appears in the source, while a page linked only
    # from a list this suite does not load would look orphaned when it is
    # not. Neither alone is the truth.
    for path in sorted(glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates", "*.html"))):
        src = io.open(path, encoding="utf-8", errors="replace").read()
        for name in re.findall(r"url_for\(\s*['\"]([a-z_][a-z0-9_]*)['\"]", src):
            try:
                with m.app.test_request_context("/"):
                    served.add(m.url_for(name))
            except Exception:
                pass
    s.check("the section pages and templates hand out plenty of addresses",
            len(served) > 250, detail="%d found" % len(served))

    orphans = []
    for r in m.app.url_map.iter_rules():
        if "GET" not in (r.methods or set()):
            continue
        rule = str(r.rule)
        if rule.startswith("/static") or r.endpoint in ("logout", "login"):
            continue
        if r.endpoint in NOT_LINKED_ON_PURPOSE:
            continue
        # A page behind an id is reached from the list that owns it; only the
        # addresses with no arguments can be compared literally.
        if r.arguments:
            continue
        if rule in served:
            continue
        orphans.append("%s (%s)" % (r.endpoint, rule))

    s.check("no page can only be reached by typing its address",
            not orphans,
            detail="%s — it renders, it answers, its tests pass, and nobody "
                   "knows it is there" % orphans[:6])

    # Both of the ones this found, by name, so the sweep above cannot go
    # quiet and take them with it.
    footer = oc.get("/book").get_data(as_text=True)
    s.check("the footer points at the house's own photographs",
            "/social" in footer,
            detail="the page exists because the photographs were reachable "
                   "only by leaving the site")
    s.check("and an event enquiry can be looked up the way a booking can",
            "/events/find" in footer and "/book/manage" in footer)

    # And the allow-list is checked in the other direction too, so a page
    # that gains a link is taken off it rather than left as cover.
    stale = []
    for name in NOT_LINKED_ON_PURPOSE:
        try:
            with m.app.test_request_context("/"):
                rule = m.url_for(name)
        except Exception:
            continue
        if rule in served:
            stale.append(name)
    s.check("and nothing on the allow-list is linked after all",
            not stale,
            detail="%s — linked now, so it should come off the list; one that "
                   "only grows is a list nobody reads twice" % stale)
    return s
