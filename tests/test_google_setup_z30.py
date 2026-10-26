"""What setting the house up with Google found (z30, 17-19 October 2026).

G1. Sixteen addresses of the old site, or still listed by Google, answered
    404. They go where the design side mapped them: eleven to a page, by a
    301, and five to one atelier each, found by the words of its title (a 302,
    because which atelier answers is data). Old addresses are matched in any
    case and with a trailing slash, as Squarespace matched them.
G5. /preview is the front page's template on an address of its own, and
    Google offered it as a second homepage. It is noindex, and the
    breadcrumb's first link goes to /.
G7. Cloudflare Web Analytics, on the public site at the house's own address
    only: never on another host, never for staff, never for a browser that
    asks not to be tracked, and never on a page about one guest's booking.
    The privacy notice says so, and no longer says no other company counts.

G2 (the homepage's canonical) was already true. G3, G4 and G6 (the hosts)
are the admin-host work's.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
WWW = "https://www.chateaugudanes.com"
BEACON = "static.cloudflareinsights.com/beacon.min.js"

STATIC = {
    "/staynow": "/book", "/stay-with-us": "/book", "/stay-with-us-1": "/book",
    "/photoshoots": "/events/photoshoots", "/the-restoration-story": "/restoration",
    "/the-chateau-and-its-grounds": "/facilities", "/inside-the-chateau": "/gallery",
    "/life-at-the-chateau": "/gallery", "/gallery-overview": "/gallery",
    "/nestled-within-a-mountainous-valley": "/gallery", "/follow": "/contact",
}
ATELIERS = {"/stay-autumn-2026": "Immersive Artisan", "/stay-christmas-2026": "Noel",
            "/3-nights": "Antique Finds", "/the-weekender": "Antique Finds",
            "/7-nights": "Seven Starry Nights"}


def _path(location):
    return location.replace("http://localhost", "").replace(WWW, "")


def run():
    s = Suite("Google setup, z30")
    _oc, _ec, owner, _emp = clients()
    anon = m.app.test_client()

    s.section("G1: the old addresses go somewhere that answers")
    for old, new in STATIC.items():
        r = anon.get(old + "?utm_source=letter")
        s.check(f"{old} is a 301 to {new}, keeping the query",
                r.status_code == 301 and _path(r.headers.get("Location", ""))
                == new + "?utm_source=letter",
                detail=f"{r.status_code} {r.headers.get('Location')}")
        s.check(f"and {new} answers", anon.get(new).status_code == 200)
    conn = db()
    titles = conn.execute("SELECT id, title FROM workshops WHERE active = 1").fetchall()
    conn.close()
    for old, words in ATELIERS.items():
        r = anon.get(old)
        want = [t["id"] for t in titles if m._name_words(words) <= m._name_words(t["title"])]
        target = (f"/workshops/{want[0]}" if len(want) == 1 else "/workshops")
        s.check(f"{old} is a 302 to {target}",
                r.status_code == 302 and _path(r.headers.get("Location", "")) == target,
                detail=f"{r.status_code} {r.headers.get('Location')}; {len(want)} match(es)")
    r = anon.get("/StayNow/?a=1")
    s.check("an old address in capitals with a slash finds its way",
            r.status_code == 301 and _path(r.headers.get("Location", "")) == "/staynow?a=1",
            detail=f"{r.status_code} {r.headers.get('Location')}")
    s.check("and the next hop is the page", anon.get("/staynow").status_code == 301)
    s.check("a mistyped address that was never an old one is still a 404",
            anon.get("/StayNowhere/").status_code == 404)
    s.check("and a real page in capitals is not guessed at",
            anon.get("/BOOK").status_code == 404)

    s.section("G5: /preview is not a second homepage")
    r = anon.get("/preview")
    s.check("it still answers", r.status_code == 200)
    s.check("and says noindex", "noindex" in (r.headers.get("X-Robots-Tag") or ""),
            detail=str(r.headers.get("X-Robots-Tag")))
    page = anon.get("/book").get_data(as_text=True)
    crumbs = page[page.find('class="g-crumbs"'):][:600]
    s.check("the breadcrumb's first link is the front page", '<a href="/">Château</a>' in crumbs,
            detail=crumbs[:300])

    s.section("G7: Cloudflare Web Analytics where it may count, and nowhere else")
    was = (m.SITE_IS_LIVE, m.PUBLIC_BASE_URL)
    m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = True, WWW
    try:
        www = m.app.test_client()
        on = www.get("/book", base_url=WWW).get_data(as_text=True)
        s.check("on a public page at www", BEACON in on and m.CF_BEACON_TOKEN in on)
        s.check("not on another host",
                BEACON not in www.get("/privacy", base_url="https://cdg1-production-9186.up.railway.app")
                .get_data(as_text=True))
        s.check("not for a browser that asks not to be tracked",
                BEACON not in www.get("/book", base_url=WWW, headers={"Sec-GPC": "1"}).get_data(as_text=True)
                and BEACON not in www.get("/book", base_url=WWW, headers={"DNT": "1"}).get_data(as_text=True))
        guest = www.get("/book/manage", base_url=WWW)
        s.check("not on a page about one guest's booking (it says noindex)",
                guest.status_code == 200 and "noindex" in guest.get_data(as_text=True)
                and BEACON not in guest.get_data(as_text=True), detail=str(guest.status_code))
        # Asked of the function that decides, in a request at www: the
        # public pages have no sign of a login on them to prove one took.
        with m.app.test_request_context("/book", base_url=WWW):
            m.app.preprocess_request()
            visitor = m.inject_cf_beacon()["cf_beacon"]
            m.session["user_id"] = owner["id"]
            staff = m.inject_cf_beacon()["cf_beacon"]
        s.check("a visitor at www is counted", visitor == m.CF_BEACON_TOKEN, detail=repr(visitor))
        s.check("not a member of staff", staff == "", detail=repr(staff))
    finally:
        m.SITE_IS_LIVE, m.PUBLIC_BASE_URL = was

    privacy = anon.get("/privacy").get_data(as_text=True)
    s.check("the privacy notice names it", "Cloudflare Web Analytics" in privacy)
    s.check("and no longer says no other company counts",
            "No other company counts you here" not in privacy
            and "no other company involved" not in privacy)
    s.check("and says a browser that asks is not counted by Cloudflare either",
            "by us or by Cloudflare" in privacy)
    # Reviewed on or after the day Cloudflare was added; a later review (the
    # online check-in, 22 October) still covers it.
    from datetime import datetime as _dt
    s.check("and it is dated",
            _dt.strptime(m.PRIVACY_LAST_REVIEWED, "%d %B %Y") >= _dt(2026, 10, 19),
            detail=m.PRIVACY_LAST_REVIEWED)
    return s


if __name__ == "__main__":
    print(run().report())
