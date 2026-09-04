"""The bar that follows a guest down the page offering the next step.

`.g-stickybar` is the one that survived. A second fixed bar was proposed in a
handover and rejected here, with the reason written into the stylesheet: this
one already does the job and does it better, because it changes by page type --
dinner offers a table, an atelier offers dates, a bedroom offers a booking --
and it can be switched off per page.

Which pages switch it OFF is the whole of this file, because that is the half
that renders perfectly while being wrong. `book_room.html` and
`booking_confirmation.html` both set it to none. `book_rooms.html`, which is
/book itself, did not -- so on the page where a guest is already choosing a
room, a bar sat across the bottom offering "Book" as a link to that page.
Nothing errors and nothing looks broken.

The per-page wording is checked too. A bar is only worth its space if it
offers the thing this page is about; one that says "Stay the Night" on the
wedding pages is furniture with a cost.
"""
from _harness import Suite

import html as html_mod
import io
import os
import re

import _harness

m = _harness.m

# endpoint -> the wording and destination the bar should carry there
EXPECTED = {
    "restaurant_info": ("Dine at La Table", "restaurant_book"),
    "workshops_public": ("Château Ateliers", "workshops_public"),
    "events_weddings": ("Weddings & Celebrations", "events_info"),
    "facilities_page": ("Stay the Night", "book_rooms"),
}

# Pages where the guest is already doing the thing, so it must be silent.
SILENT = ["book_rooms", "book_room", "booking_confirmation"]


def _url(endpoint, conn):
    with m.app.test_request_context():
        rule = next((r for r in m.app.url_map.iter_rules()
                     if r.endpoint == endpoint), None)
        if rule is None:
            return None
        if not rule.arguments:
            return m.url_for(endpoint)
        if "room_id" in rule.arguments:
            row = conn.execute(
                "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
            return m.url_for(endpoint, room_id=row["id"]) if row else None
        if "manage_token" in rule.arguments:
            row = conn.execute(
                "SELECT manage_token FROM bookings WHERE manage_token IS NOT NULL "
                "AND manage_token != '' ORDER BY id DESC LIMIT 1").fetchone()
            return m.url_for(endpoint, manage_token=row["manage_token"]) if row else None
    return None


def _bar(html):
    """The stickybar's markup, or "" if the page does not carry one."""
    at = html.find('class="g-stickybar"')
    return html[at:at + 700] if at >= 0 else ""


def run():
    s = Suite("the sticky bar")
    anon = m.app.test_client()
    conn = m.get_db()

    s.section("It offers what the page is about")

    for endpoint, (wording, goes_to) in EXPECTED.items():
        url = _url(endpoint, conn)
        if not url:
            continue
        r = anon.get(url)
        if r.status_code != 200:
            s.check("%s answers so the bar can be read" % endpoint, False,
                    detail="HTTP %s" % r.status_code)
            continue
        bar = _bar(r.get_data(as_text=True))
        s.check("%s carries a bar" % endpoint, bool(bar),
                detail="no g-stickybar on %s" % url)
        if not bar:
            continue
        # Unescaped first: the wording goes through Jinja, so "Weddings &
        # Celebrations" arrives as "Weddings &amp; Celebrations" and a raw
        # comparison fails on a page that is perfectly correct.
        s.check("%s: it says %r" % (endpoint, wording),
                wording in html_mod.unescape(bar),
                detail=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", bar))[:90])
        with m.app.test_request_context():
            wants = m.url_for(goes_to)
        # An anchor is allowed on the end. The atelier bar points at
        # /workshops#dates precisely so that, on the workshops page itself, it
        # goes somewhere rather than nowhere.
        found = re.findall(r'href="([^"]+)" class="g-btn g-stickybar__cta"', bar)
        s.check("%s: and it goes to %s" % (endpoint, goes_to),
                any(h == wants or h.startswith(wants + "#") for h in found),
                detail="%s, found %s" % (wants, found))

    s.section("And it is silent where they are already doing it")

    for endpoint in SILENT:
        url = _url(endpoint, conn)
        if not url:
            s.check("%s could not be reached to check" % endpoint, False,
                    detail="no route, or no record to build one with")
            continue
        r = anon.get(url)
        if r.status_code != 200:
            s.check("%s answers so it can be checked" % endpoint, False,
                    detail="HTTP %s at %s" % (r.status_code, url))
            continue
        s.check("%s is quiet" % endpoint, not _bar(r.get_data(as_text=True)),
                detail="%s — the guest is already here" % url)

    s.section("Nothing offers a link to the page it is already on")

    # The fault this file was written for. It is not enough that the pages
    # above are quiet: any page carrying the bar must send the guest somewhere
    # other than where they are.
    for endpoint in ["book_rooms", "restaurant_info", "workshops_public",
                     "facilities_page", "events_weddings"]:
        url = _url(endpoint, conn)
        if not url:
            continue
        r = anon.get(url)
        if r.status_code != 200:
            continue
        bar = _bar(r.get_data(as_text=True))
        # An anchor makes it a different destination: /workshops#dates from
        # /workshops moves the guest to the dates. A bare self-link does not.
        found = re.findall(r'href="([^"]+)" class="g-btn g-stickybar__cta"', bar)
        s.check("%s does not nag a guest to go where they are" % url,
                url not in found,
                detail="the bar on this page links to this page: %s" % found)

    s.section("It is a real mechanism, not a decoration")

    with io.open(os.path.join(m.BASE_DIR, "templates", "public_base.html"),
                 encoding="utf-8") as fh:
        base = fh.read()
    s.check("the base draws it", 'class="g-stickybar"' in base)
    # Switched off by a page saying so, which is what makes the silence above
    # a decision each page takes rather than a coincidence of its markup.
    s.check("and a page can switch it off by name",
            "sticky_bar | default(none)" in base or "sticky_bar|default(none)" in base,
            detail="no per-page control; the silence above is accidental")
    s.check("the pages that are quiet say so themselves",
            all("sticky_bar = 'none'" in io.open(
                os.path.join(m.BASE_DIR, "templates", tpl), encoding="utf-8").read()
                for tpl in ("book_rooms.html", "book_room.html",
                            "booking_confirmation.html")),
            detail="one of them is quiet for some other reason")

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
