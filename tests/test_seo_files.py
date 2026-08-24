"""robots.txt and sitemap.xml.

Neither existed, so crawlers had no map of the public site and no instruction
to stay out of anything else. The half that matters is the second one: manage
and confirmation links are unguessable, but they leak into referrer headers and
analytics, and a crawler that found one would index a guest's booking.

The sitemap is checked by parsing it. A sitemap that is well-formed to the eye
and malformed to a parser is worse than none, because nobody finds out.
"""
import xml.etree.ElementTree as ET

from _harness import Suite, clients, db
import _harness

m = _harness.m
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def run():
    s = Suite("robots.txt and sitemap.xml")
    _oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()

    s.section("robots.txt")
    r = anon.get("/robots.txt")
    s.check("it is served", r.status_code == 200, detail=str(r.status_code))
    s.check("as plain text, not HTML",
            r.headers.get("Content-Type", "").startswith("text/plain"),
            detail=r.headers.get("Content-Type", ""))
    body = r.get_data(as_text=True)
    # Everything a crawler must not walk into. The token-shaped ones are the
    # point: a guest's booking must never be indexed.
    for path in ("/admin", "/pos", "/management", "/book/manage",
                 "/book/confirmation", "/workshops/confirmation",
                 "/restaurant/confirmation", "/events/confirmation",
                 "/my", "/login", "/api"):
        s.check(f"{path} is disallowed", f"Disallow: {path}" in body,
                detail=f"{path} is crawlable")
    s.check("and it points at the sitemap", "Sitemap: " in body and "/sitemap.xml" in body)

    s.section("sitemap.xml is a document a parser accepts")
    r = anon.get("/sitemap.xml")
    s.check("it is served", r.status_code == 200, detail=str(r.status_code))
    s.check("as XML", "xml" in r.headers.get("Content-Type", ""),
            detail=r.headers.get("Content-Type", ""))
    try:
        root = ET.fromstring(r.get_data(as_text=True))
        locs = [u.find("s:loc", NS).text for u in root.findall("s:url", NS)]
        parsed = True
    except Exception as e:
        locs, parsed = [], False
        s.check("it parses", False, detail=f"{type(e).__name__}: {e}")
    if parsed:
        s.check("it parses", True)
    s.check("and lists something", len(locs) > 3, detail=f"{len(locs)} urls")

    s.section("The public pages are on it")
    for label, path in [("the front page", "/"), ("the rooms", "/book"),
                        ("the workshops", "/workshops"), ("the restaurant", "/restaurant")]:
        s.check(f"{label} is listed",
                any(l.rstrip("/").endswith(path.rstrip("/")) or l.endswith(path)
                    for l in locs), detail=f"{path} missing")

    s.section("And nothing private is")
    # The failure that would actually hurt: a guest's booking in a public
    # index. Asserted against the URLs, not against the intent.
    leaked = [l for l in locs if any(x in l for x in
              ("/admin", "/pos", "/manage", "/confirmation", "/my/", "/login", "/api"))]
    s.check("no admin, token or guest URL is listed", not leaked, detail=str(leaked[:3]))

    s.section("Each active room has its own entry")
    # Individual room pages are what people search for. A sitemap listing only
    # the index page hides them.
    n_rooms = conn.execute(
        "SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
    listed = len([l for l in locs if "/book/" in l])
    s.check(f"all {n_rooms} of them", listed == n_rooms, detail=f"{listed} listed")

    s.section("Only ateliers with dates still ahead")
    # The same rule the front page follows. A sitemap advertising a workshop
    # that finished in June sends people to a page they cannot book.
    ahead = conn.execute(
        """SELECT COUNT(DISTINCT workshops.id) AS c FROM workshops
             JOIN workshop_sessions ON workshop_sessions.workshop_id = workshops.id
            WHERE workshops.active = 1 AND workshop_sessions.start_date >= ?""",
        (m.service_day_iso(),)).fetchone()["c"]
    ws = len([l for l in locs if "/workshops/" in l])
    s.check(f"{ahead} atelier(s) with a date to come", ws == ahead, detail=f"{ws} listed")

    s.section("Every entry is usable")
    s.check("every listed url is absolute",
            all(l.startswith("http") for l in locs),
            detail=str([l for l in locs if not l.startswith("http")][:2]))

    conn.close()
    return s
