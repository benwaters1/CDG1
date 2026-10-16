"""The addresses the site had before, which people are still following.

The château was on Squarespace. Those paths are the ones in Google, in
newsletters people kept, and on other people's blogs, and every one of them
has answered 404 since the rebuild. A 404 on a link somebody followed on
purpose is a guest who went looking for the rooms and did not arrive.

WHAT THIS IS REALLY GUARDING, because "does /rooms redirect" is the easy half.

  IT MUST NEVER SHADOW A REAL PAGE. Five of the old paths ARE the page now --
  /book, /workshops, /restaurant, /gallery and /events. A redirect on one of
  those would send a guest away from the page they asked for, and the site
  would still answer 200 the whole way. The wiring skips a taken path rather
  than raising, because it runs at import and a hard failure there takes the
  house's website down over a redirect. So the loud failure is here, where it
  costs nothing.

  IT MUST LAND SOMEWHERE THAT ANSWERS. A redirect to a route that has since
  been renamed is a 404 with an extra step, and it is invisible: the redirect
  itself still returns a perfectly good 301.

  AND PERMANENT MEANS PERMANENT. A 301 is cached by browsers more or less
  forever, which is what makes it worth doing -- it hands the old page's
  search ranking over -- and also what makes it hard to take back. The three
  the house might one day want for itself go out as 302s. On a house that has
  kept a Captain's Log for a decade, /journal is not a safe thing to give away
  permanently.
"""
from datetime import datetime, timezone

from _harness import Suite, clients

import _harness

m = _harness.m


def run():
    s = Suite("The addresses people still have")
    _oc, _ec, _owner, _emp = clients()
    c = m.app.test_client()
    rules = {str(r.rule): r.endpoint for r in m.app.url_map.iter_rules()}

    s.section("An old link arrives somewhere")
    s.check("there are old paths wired at all", len(m.LEGACY_WIRED) >= 15,
            detail=str(len(m.LEGACY_WIRED)))
    landed, missed = [], []
    for path, endpoint, _permanent in m.LEGACY_PATHS:
        if path not in m.LEGACY_WIRED:
            continue
        r = c.get(path)
        (landed if r.status_code in (301, 302) else missed).append(path)
    s.check("and every one of them redirects", not missed, detail=str(missed))
    s.check("/rooms goes to the rooms",
            (c.get("/rooms").headers.get("Location") or "").endswith(
                m.app.url_map.bind("x").build("book_rooms")),
            detail=str(c.get("/rooms").headers.get("Location")))

    s.section("It never sends anybody away from a page that exists")
    # The five that ARE the page. A redirect on one of these would be a guest
    # asking for the workshops and being sent somewhere else, with a 200 at
    # the end of it, which is why nothing would ever notice.
    shadowed = [p for p, _e, _perm in m.LEGACY_PATHS if p in rules
                and not rules[p].startswith("legacy_")]
    s.check("no old path is listed over a real route", not shadowed,
            detail=f"{shadowed} — these are the pages themselves, and a "
                   "redirect on one answers 200 all the way to the wrong page")
    # /history was an old address sent to the Restoration page; since z22 it
    # is the History page itself, and a redirect left on it would hide it.
    for path in ("/workshops", "/gallery", "/events", "/restaurant", "/book",
                 "/history"):
        s.check(f"{path} still serves its own page",
                c.get(path).status_code == 200,
                detail=str(c.get(path).status_code))

    s.section("And it lands on something that answers")
    dead = []
    for path, endpoint, _permanent in m.LEGACY_PATHS:
        if path not in m.LEGACY_WIRED:
            continue
        if endpoint not in {r.endpoint for r in m.app.url_map.iter_rules()}:
            dead.append((path, endpoint))
            continue
        if c.get(path, follow_redirects=True).status_code != 200:
            dead.append((path, endpoint))
    s.check("every redirect ends on a page that renders", not dead,
            detail=f"{dead} — a redirect to a renamed route is a 404 with "
                   "an extra step, and the 301 itself still looks perfect")

    s.section("Permanent where it earns it, temporary where it does not")
    perm = {p: c.get(p).status_code for p, _e, want in m.LEGACY_PATHS
            if want and p in m.LEGACY_WIRED}
    s.check("the settled ones are permanent, so the ranking carries over",
            set(perm.values()) == {301}, detail=str(perm))
    temp = {p: c.get(p).status_code for p, _e, want in m.LEGACY_PATHS
            if not want and p in m.LEGACY_WIRED}
    s.check("and the ones the house may want back are not",
            set(temp.values()) == {302},
            detail=f"{temp} — a 301 sits in every returning reader's "
                   "browser cache; this house has kept a Captain's Log for a "
                   "decade and may yet publish it at its own address")
    s.check("/journal is one of them", "/journal" in temp, detail=str(list(temp)))

    s.section("The tail of the link comes with it")
    r = c.get("/rooms?utm_source=newsletter&utm_campaign=spring")
    s.check("a campaign tag survives the redirect",
            "utm_source=newsletter" in (r.headers.get("Location") or ""),
            detail=f"{r.headers.get('Location')} — dropping it turns every "
                   "arrival from a campaign the house paid for into an "
                   "anonymous one")
    s.check("and so does the rest of it",
            "utm_campaign=spring" in (r.headers.get("Location") or ""),
            detail=str(r.headers.get("Location")))
    # There was a check here that a link with no query string does not come
    # back as "/book?". It is gone because it cannot fail: Werkzeug strips an
    # empty query off the Location header, so the code could append a bare
    # question mark to every redirect and the check would still pass. A check
    # that cannot fail reads as cover, which is worse than not having one.

    s.section("An address nobody ever had still says so")
    s.check("a made-up path is still a 404",
            c.get("/definitely-not-a-page").status_code == 404,
            detail="a catch-all that redirects everything would hide every "
                   "broken link the site has")
    s.check("and so is one that merely sounds like an old section",
            c.get("/celebrations").status_code == 404,
            detail="a section covers what is beneath its own address, not "
                   "anything that starts with the same letters")

    _the_squarespace_sitemap(s, c)
    _old_sections(s, c)
    _old_ateliers(s, c)
    _one_address(s, c)
    return s


# Every <loc> in https://www.chateaugudanes.com/sitemap.xml on 14 October 2026,
# the day before the domain was due to move. Held here rather than fetched,
# because the suite never touches the network, and because the day Squarespace
# comes down there is nothing left to fetch. The list the app wired first was
# written from guesses, and not one of them was on this: 27 of these 32
# answered 404.
SQUARESPACE_SITEMAP = [
    "/celebrations-portfolio", "/celebrations-portfolio/dace-birthday",
    "/celebrations-portfolio/dan-and-carl", "/celebrations-portfolio/erica-and-edmund",
    "/celebrations-portfolio/more-moments-of-inspiration",
    "/celebrations-portfolio/rebecca-and-ken",
    "/chateau-ateliers",
    "/chateau-ateliers/p/artisan-workshops-autumn-2026",
    "/chateau-ateliers/p/cooking-in-the-cuisine-2027",
    "/chateau-ateliers/p/french-finds-atelier-2027-early-july",
    "/chateau-ateliers/p/french-finds-atelier-2027-late-july",
    "/chateau-ateliers/p/noel-atelier-2026",
    "/chateau-ateliers/p/seven-starry-nights-2027",
    "/chateau-letter-club", "/commercial", "/contact", "/gallery", "/home",
    "/letter-club-subscription",
    "/letter-club-subscription/p/seasonal-letter-subscription-from-chteau-de-gudanes",
    "/nightly-reservation", "/press", "/private-events", "/story", "/team",
    "/terms-and-conditions-ateliers", "/terms-and-conditions-overnight-stays",
    "/terms-and-conditions-private-events", "/terms-and-conditions-productions",
    "/terms-and-conditions-wedding-reservation",
    "/terms-and-conditions-weddings-for-guests", "/weddings",
]


def _the_squarespace_sitemap(s, c):
    s.section("Every address the old site actually had")
    lost = []
    for path in SQUARESPACE_SITEMAP:
        r = c.get(path, follow_redirects=True)
        if r.status_code != 200:
            lost.append(f"{path} ({r.status_code})")
    s.check(f"all {len(SQUARESPACE_SITEMAP)} answer, as a page or by sending "
            "the reader to one", not lost,
            detail=f"{lost} — these are the addresses Google holds for the "
                   "house, and a guest following one finds nothing")

    def lands(path):
        loc = c.get(path).headers.get("Location") or ""
        return loc.split("?")[0]

    rules = m.app.url_map.bind("x")
    for path, endpoint, why in (
            ("/nightly-reservation", "book_rooms", "the old booking page"),
            ("/private-events", "events_private", "private hire"),
            ("/commercial", "events_photoshoots", "the location-hire page"),
            ("/celebrations-portfolio/dace-birthday", "events_private",
             "a birthday, not a wedding"),
            ("/celebrations-portfolio/rebecca-and-ken", "events_weddings",
             "a wedding story"),
            ("/chateau-letter-club", "dashboard",
             "the Letter Club is going; the newsletter is on the front page"),
            ("/terms-and-conditions-weddings-for-guests", "terms_page",
             "the one set of terms")):
        want = rules.build(endpoint)
        s.check(f"{path} goes to {want}", lands(path) == want,
                detail=f"{lands(path)} — {why}")

    # Squarespace's own shop pages: every Squarespace site has them.
    for path in ("/boutique", "/cart"):
        r = c.get(path)
        s.check(f"{path}, the old shop, goes to the front page",
                r.status_code == 301 and (r.headers.get("Location") or "") == "/",
                detail=f"{r.status_code} {r.headers.get('Location')}")


def _old_sections(s, c):
    s.section("A page under an old section that no sitemap listed")
    for path, endpoint in (
            ("/celebrations-portfolio/a-story-added-later", "events_weddings"),
            ("/chateau-ateliers/category/2024", "workshops_public"),
            ("/letter-club-subscription/p/a-gift-subscription", "dashboard")):
        r = c.get(path)
        want = m.app.url_map.bind("x").build(endpoint)
        s.check(f"{path} goes where its section goes",
                r.status_code == 301 and (r.headers.get("Location") or "") == want,
                detail=f"{r.status_code} {r.headers.get('Location')}")
    r = c.get("/celebrations-portfolio/a-story?utm_source=pinterest")
    s.check("with the tail of the link",
            "utm_source=pinterest" in (r.headers.get("Location") or ""),
            detail=str(r.headers.get("Location")))


TAG = "Zzlegacy"


def _workshop(title, active=1):
    conn = _harness.db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, active,
               instructor_name, created_at) VALUES (?, '', 400, ?, 'A Gilder', ?)""",
            (title, active, now))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _old_ateliers(s, c):
    s.section("An atelier's old product page finds that atelier")
    conn = _harness.db()
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    try:
        wid = _workshop(f"{TAG} Noël Lantern Weekend")
        _workshop(f"{TAG} Gilding Lantern Weekend", active=0)
        slug = f"/chateau-ateliers/p/{TAG.lower()}-noel-lantern-weekend-atelier-2027-early-july"
        r = c.get(slug)
        s.check("matched on its name, whatever the year, month and accents",
                (r.headers.get("Location") or "").endswith(f"/workshops/{wid}"),
                detail=f"{r.status_code} {r.headers.get('Location')}")
        s.check("and temporary, because the answer is read from data that changes",
                r.status_code == 302, detail=str(r.status_code))
        s.check("and it lands on the atelier's page",
                c.get(slug, follow_redirects=True).status_code == 200)

        r = c.get(f"/chateau-ateliers/p/{TAG.lower()}-gilding-lantern-weekend-2026")
        s.check("a workshop taken off sale is not a match",
                (r.headers.get("Location") or "") == "/workshops",
                detail=str(r.headers.get("Location")))

        r = c.get(f"/chateau-ateliers/p/{TAG.lower()}-nothing-like-it-2026")
        s.check("no match goes to the list rather than a guess",
                (r.headers.get("Location") or "") == "/workshops",
                detail=str(r.headers.get("Location")))

        _workshop(f"{TAG} Noël Lantern Weekend, Again")
        r = c.get(slug)
        s.check("and two matches go to the list too, not to whichever came first",
                (r.headers.get("Location") or "") == "/workshops",
                detail=f"{r.headers.get('Location')} — landing on the wrong "
                       "atelier is worse than choosing from all of them")
    finally:
        conn = _harness.db()
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()


RAILWAY = "https://cdg1-production-9186.up.railway.app"
HOUSE = "https://www.chateaugudanes.com"


def _one_address(s, c):
    s.section("Once live, one address for the public site")
    was_live, was_base = m.SITE_IS_LIVE, m.PUBLIC_BASE_URL
    try:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = False, HOUSE
        s.check("before switchover the Railway address is left alone",
                c.get("/story", base_url=RAILWAY).status_code == 200,
                detail="it is the site under review until the domain moves")

        m.SITE_IS_LIVE = True
        r = c.get("/story", base_url=RAILWAY)
        s.check("a public page on the Railway address goes to the house's",
                r.status_code == 301 and r.headers.get("Location") == HOUSE + "/story",
                detail=f"{r.status_code} {r.headers.get('Location')}")
        r = c.get("/story?utm_source=newsletter", base_url=RAILWAY)
        s.check("with the tail of the link",
                r.headers.get("Location") == HOUSE + "/story?utm_source=newsletter",
                detail=str(r.headers.get("Location")))
        r = c.get("/story", base_url="https://chateaugudanes.com")
        s.check("and so does one on the bare domain, when the house uses www",
                r.status_code == 301 and r.headers.get("Location") == HOUSE + "/story",
                detail=f"{r.status_code} {r.headers.get('Location')}")
        s.check("the front page as well",
                c.get("/", base_url=RAILWAY).headers.get("Location") == HOUSE + "/",
                detail=str(c.get("/", base_url=RAILWAY).headers.get("Location")))
        s.check("HEAD as well as GET",
                c.head("/story", base_url=RAILWAY).status_code == 301)
        s.check("and on the house's own address it simply answers",
                c.get("/story", base_url=HOUSE).status_code == 200,
                detail="a redirect to itself would be a loop")

        r = c.get("/rooms", base_url=RAILWAY)
        s.check("an old link on the Railway address goes to the house in one hop",
                r.headers.get("Location") == HOUSE + "/book",
                detail=str(r.headers.get("Location")))

        r = c.get("/sitemap.xml", base_url=RAILWAY)
        s.check("the sitemap is moved, so it lists the house's addresses",
                r.status_code == 301 and r.headers.get("Location") == HOUSE + "/sitemap.xml",
                detail=f"{r.status_code} {r.headers.get('Location')}")
        body = c.get("/sitemap.xml", base_url=HOUSE).get_data(as_text=True)
        s.check("and on the house's address every line in it is the house's",
                HOUSE + "/story" in body and "railway" not in body,
                detail=body[:300])

        robots = c.get("/robots.txt", base_url=RAILWAY).get_data(as_text=True)
        s.check("the Railway copy asks crawlers to keep out of all of it",
                robots.strip().endswith("Disallow: /"), detail=robots[:200])
        robots = c.get("/robots.txt", base_url=HOUSE).get_data(as_text=True)
        s.check("while the house's own robots.txt opens the public site",
                "Disallow: /admin" in robots and HOUSE + "/sitemap.xml" in robots,
                detail=robots[:300])

        r = c.get("/login", base_url=RAILWAY)
        s.check("a staff page stays where it was asked",
                r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')} — whoever "
                       "signed in on one address keeps their session there")
        # Something that answers 200 and is not a page. The service worker
        # is the one to hand: a browser refuses to install one from a
        # redirect, and the feeds and webhooks fail the same way, quietly.
        r = c.get("/sw.js", base_url=RAILWAY)
        s.check("and nothing that is not a page is moved",
                r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')} — a "
                       "feed reader may not follow a redirect, and a browser "
                       "will not take a service worker from one")
        # Chosen because it answers a POST with a public page and a 200, so
        # only the method stands between it and the redirect.
        r = c.post("/events/find", data={}, base_url=RAILWAY)
        s.check("nor is a form being sent",
                r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')} — a 301 "
                       "turns a POST into a GET and what was sent is lost")
        r = c.get("/definitely-not-a-page", base_url=RAILWAY)
        s.check("and a page that is not there still says so",
                r.status_code == 404, detail=str(r.status_code))
        # The Outlook add-in is installed in everybody's Outlook at the Railway
        # address. Moved, it stops loading -- and Office will not follow a
        # redirect to a host its manifest does not list.
        moved = [p for p in ("/outlook-addin/taskpane", "/outlook-addin/compose",
                             "/outlook-addin/launchevent.html", "/outlook-addin/manifest.xml")
                 if (c.get(p, base_url=RAILWAY).headers.get("Location") or "").startswith(HOUSE)]
        s.check("and the Outlook add-in stays on the Railway address", not moved,
                detail=str(moved))

        s.section("It never sends the house to the platform")
        m.PUBLIC_BASE_URL = RAILWAY
        r = c.get("/story", base_url=HOUSE)
        s.check("live with PUBLIC_BASE_URL still the Railway one, nothing moves",
                r.status_code == 200,
                detail=f"{r.status_code} {r.headers.get('Location')} — a 301 "
                       "from chateaugudanes.com to Railway sits in every "
                       "visitor's browser more or less for ever")
        with m.app.test_request_context("/", base_url=HOUSE):
            conn = _harness.db()
            try:
                checks = m.readiness_checks(conn, include_slow=False)
            finally:
                conn.close()
        mine = [x for x in checks if x["label"] == "The house's own address"]
        s.check("and the readiness page says so as a blocker",
                mine and mine[0]["severity"] == "blocker" and not mine[0]["ok"],
                detail=str(mine))
        m.PUBLIC_BASE_URL = HOUSE
        with m.app.test_request_context("/", base_url=HOUSE):
            conn = _harness.db()
            try:
                mine = [x for x in m.readiness_checks(conn, include_slow=False)
                        if x["label"] == "The house's own address"]
            finally:
                conn.close()
        s.check("which clears when it names the house",
                mine and mine[0]["ok"], detail=str(mine))
        m.PUBLIC_BASE_URL = ""
        s.check("and live with no address set, nothing moves either",
                c.get("/story", base_url=RAILWAY).status_code == 200)
    finally:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = was_live, was_base


if __name__ == "__main__":
    print(run().report())
