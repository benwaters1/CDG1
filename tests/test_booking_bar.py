"""The bar that offers the booking action on a phone.

One CTA at screen 19 of 42 is one a phone never reaches, so a bar now follows
the page and offers "Check dates" once the hero has scrolled away.

Which pages it must NOT appear on is the whole of this file, because that is
the half that renders perfectly while being wrong. The bar arrived excluding
`book_room` -- a single room's page -- but not `book_rooms`, which is /book
itself. So on the booking page the bar sat there offering a link to the page
it was already on. Nothing errors, nothing looks broken, and the one page
where the guest is already doing the thing is the page that nags them to go
and do it.

The exclusions are not decoration either. Somebody reading their own
confirmation, or looking up a booking they already hold, should not be sold a
room; the comment in public_base says so, and this is what holds the markup to
it.
"""
from _harness import Suite

import io
import os
import re

import _harness

m = _harness.m

# endpoint -> a URL that reaches it
MUST_NOT_CARRY = ["book_rooms", "book_room", "booking_confirmation", "find_booking"]
SHOULD_CARRY = ["/", "/restoration", "/workshops", "/restaurant", "/facilities"]


def _url_for_endpoint(endpoint, conn):
    """A real URL for an endpoint, filling any parameter from real data."""
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


def run():
    s = Suite("the booking bar")
    anon = m.app.test_client()
    conn = m.get_db()

    s.section("It is there where somebody has not started yet")

    carried = 0
    for path in SHOULD_CARRY:
        r = anon.get(path)
        if r.status_code != 200:
            continue
        body = r.get_data(as_text=True)
        has = 'class="g-mobar"' in body
        if has:
            carried += 1
        s.check("%s offers the booking action" % path, has,
                detail="a phone reaching screen 19 of 42 to find it has left")
    s.check("it is on more than one page", carried >= 3, detail=str(carried))

    # It must point somewhere other than here, or it is furniture.
    body = anon.get("/restoration").get_data(as_text=True)
    with m.app.test_request_context():
        wants = m.url_for("book_rooms")
    s.check("and it points at the rooms", wants in body.split('class="g-mobar"')[-1][:400],
            detail="expected a link to %s" % wants)

    s.section("And absent where they are already doing it")

    for endpoint in MUST_NOT_CARRY:
        url = _url_for_endpoint(endpoint, conn)
        if not url:
            s.check("%s could not be reached to check" % endpoint, False,
                    detail="no route, or no record to build one with")
            continue
        r = anon.get(url)
        if r.status_code != 200:
            s.check("%s answers so it can be checked" % endpoint, False,
                    detail="HTTP %s at %s" % (r.status_code, url))
            continue
        s.check("%s does not carry it" % endpoint,
                'class="g-mobar"' not in r.get_data(as_text=True),
                detail="%s — the guest is already here" % url)

    # The specific fault this file was written for, said plainly: a CTA whose
    # destination is the page it is drawn on.
    s.section("Nothing offers a link to the page it is already on")

    for endpoint in ["book_rooms"]:
        url = _url_for_endpoint(endpoint, conn)
        r = anon.get(url)
        body = r.get_data(as_text=True)
        after = body.split('class="g-mobar"')[-1][:400] if 'class="g-mobar"' in body else ""
        s.check("%s does not nag a guest to go where they are" % url,
                'href="%s"' % url not in after,
                detail="the bar links to this very page")

    s.section("It is a phone thing, and the stylesheet says so")

    with io.open(os.path.join(m.BASE_DIR, "static", "gudanes.css"),
                 encoding="utf-8") as fh:
        css = fh.read()

    s.check("the stylesheet was read", ".g-mobar" in css,
            detail="nothing below this proves anything if it was not")
    # Looked for as a min-width rule that hides it, not as the two strings
    # appearing somewhere in 320KB. The first version of this check was
    # `".g-mobar" in css and "display: none" in css`, which is true of almost
    # any stylesheet and passed with the desktop rule deleted.
    desktop_hides = re.search(
        r"@media\s*\(\s*min-width[^)]*\)\s*\{[^}]*\.g-mobar\s*\{[^}]*display:\s*none",
        css)
    s.check("the bar is hidden on a wide screen", bool(desktop_hides),
            detail="a fixed bar across the foot of a desktop page is a mistake")
    # It reveals by taking a class OFF the body, so if the class is never
    # applied the bar is simply always there, which is the other failure.
    s.check("and reveals by the class the header already maintains",
            "body:not(.at-top) .g-mobar" in css,
            detail="a second scroll listener with its own flag is how the "
                   "header ended up with two fighting over one class")

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
