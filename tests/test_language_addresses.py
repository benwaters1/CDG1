"""French and Spanish pages at addresses of their own (site review, 25 November 2026).

The public site was in French or Spanish by a cookie, at the same address as
the English. Its hreflang tags pointed at ?lang=fr -- which robots.txt asked
Google not to fetch -- and the canonical tag on a French page named the English
address. Between them, Google could index no French or Spanish page at all:
"hreflang is wrong on every page", in the review's words.

Now /fr/<page> and /es/<page> are those pages in those languages. This holds:

  - with the owner's language switch OFF, nothing changes: no language
    addresses written into a page, nobody sent to one;
  - with it ON, /fr/book is French, names itself as canonical, and lists the
    English, French and Spanish addresses and x-default as alternates;
  - its links stay in French (/fr/...), but not its stylesheet, staff links or
    the language switcher;
  - a reader who chose French and asks for /book is sent to /fr/book; ?lang=es
    has moved for good (301) to /es/...; a form under /fr/ goes to its own
    address with the language kept; choosing English on /fr/book returns /book;
  - the sitemap lists every page in each language with its alternates, and the
    pages the review found missing; robots.txt lets ?lang= be fetched, so the
    move can be seen.
"""
import re

from _harness import Suite, db
import _harness

m = _harness.m


def _switch(on):
    conn = db()
    try:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('show_language_switcher', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if on else "",))
        conn.commit()
    finally:
        conn.close()


def _was_on():
    conn = db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'show_language_switcher'").fetchone()
        return bool(row and row["value"])
    finally:
        conn.close()


def run():
    s = Suite("Language addresses")
    was = _was_on()
    try:
        _off(s)
        _switch(True)
        _on(s)
        _moves(s)
        _sitemap(s)
    finally:
        _switch(was)
    return s


def _off(s):
    s.section("With the switch off, the site is as it was")
    _switch(False)
    c = m.app.test_client()
    r = c.get("/book")
    page = r.get_data(as_text=True)
    s.check("/book answers itself", r.status_code == 200, detail=str(r.status_code))
    s.check("and writes no language address into its links", 'href="/fr/' not in page and 'href="/es/' not in page)
    r = c.get("/book", headers={"Accept-Language": "fr-FR,fr;q=0.9"})
    s.check("a French browser is not sent anywhere", r.status_code == 200, detail=str(r.status_code))


def _on(s):
    s.section("/fr/book is the French page, and says so")
    c = m.app.test_client()
    r = c.get("/fr/book")
    page = r.get_data(as_text=True)
    s.check("it answers", r.status_code == 200, detail=str(r.status_code))
    s.check("in French", '<html lang="fr">' in page)
    canon = re.findall(r'<link rel="canonical" href="([^"]+)">', page)
    s.check("one canonical tag, naming itself", len(canon) == 1 and canon[0].endswith("/fr/book"), detail=str(canon))
    alts = dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)">', page))
    s.check("alternates in English, French, Spanish and x-default",
            alts.get("en", "").endswith("/book") and "/fr/" not in alts.get("en", "") and "/es/" not in alts.get("en", "")
            and alts.get("fr", "").endswith("/fr/book") and alts.get("es", "").endswith("/es/book")
            and alts.get("x-default") == alts.get("en"), detail=str(alts))
    s.check("no alternate is a ?lang= address any more", "?lang=" not in " ".join(alts.values()))
    s.check("each alternate once", len(re.findall(r'hreflang="fr"', page.split("</head>")[0])) == 1)
    s.check("og:url and og:locale are the French page's",
            re.search(r'<meta property="og:url" content="[^"]*/fr/book">', page) is not None
            and '<meta property="og:locale" content="fr_FR">' in page)
    links = re.findall(r'\shref="(/[^"]*)"', page)
    s.check("its links stay in French", "/fr/history" in links and "/fr/workshops" in links, detail=str(links[:12]))
    s.check("the stylesheet is not moved", not any(l.startswith("/fr/static") for l in links)
            and any(l.startswith("/static/") for l in links))
    s.check("the language switcher is not moved",
            "/language/en" in links and not any(l.startswith("/fr/language") for l in links))
    s.check("nor is an English page left among the main links", "/history" not in links)

    s.section("/es/ is the Spanish front page")
    r = c.get("/es/")
    page = r.get_data(as_text=True)
    s.check("it answers in Spanish", r.status_code == 200 and '<html lang="es">' in page, detail=str(r.status_code))
    s.check("and names /es/ as itself", re.search(r'<link rel="canonical" href="[^"]*/es/">', page) is not None)

    s.section("The English page names itself, and the others")
    page = m.app.test_client().get("/book").get_data(as_text=True)
    canon = re.findall(r'<link rel="canonical" href="([^"]+)">', page)
    s.check("canonical is /book", len(canon) == 1 and canon[0].endswith("/book")
            and "/fr/" not in canon[0], detail=str(canon))
    s.check("with the French alternate at /fr/book", re.search(r'hreflang="fr" href="[^"]*/fr/book"', page) is not None)


def _moves(s):
    s.section("Readers arrive at the address of the language they read")
    c = m.app.test_client()
    c.get("/fr/book")
    r = c.get("/history")
    s.check("chose French, asks for /history: sent to /fr/history (302)",
            r.status_code == 302 and r.headers.get("Location", "").endswith("/fr/history"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = m.app.test_client().get("/book?lang=es&arrival=2027-03-01")
    s.check("?lang=es has moved for good to /es/, the rest of the address kept",
            r.status_code == 301 and r.headers.get("Location", "").endswith("/es/book?arrival=2027-03-01"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = m.app.test_client().get("/press", headers={"Accept-Language": "fr-FR,fr;q=0.9"})
    s.check("a French browser on its first visit is sent to /fr/press",
            r.status_code == 302 and r.headers.get("Location", "").endswith("/fr/press"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = m.app.test_client().get("/press")
    s.check("a reader with no language asked for stays on the English page", r.status_code == 200,
            detail=str(r.status_code))
    r = c.get("/fr/book/manage")
    s.check("a page with no language address of its own is sent to its own address",
            r.status_code == 302 and not r.headers.get("Location", "").startswith("/fr/"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = c.get("/language/en", headers={"Referer": "http://localhost/fr/book?arrival=2027-01-01"})
    s.check("choosing English on /fr/book returns to /book, the dates kept",
            r.status_code == 302 and r.headers.get("Location", "").endswith("/book?arrival=2027-01-01")
            and "/fr/" not in r.headers.get("Location", ""),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = c.get("/book")
    s.check("and English then stays English", r.status_code == 200, detail=str(r.status_code))
    r = c.get("/language/es", headers={"Referer": "http://localhost/press"})
    s.check("choosing Spanish on /press goes to /es/press",
            r.status_code == 302 and r.headers.get("Location", "").endswith("/es/press"),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = c.get("/fr/no-such-page-here")
    s.check("a page that is not there is still not found under /fr/", r.status_code == 404, detail=str(r.status_code))


def _sitemap(s):
    s.section("The sitemap lists every language")
    xml = m.app.test_client().get("/sitemap.xml").get_data(as_text=True)
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    s.check("each page in English, French and Spanish",
            any(l.endswith("/book") and "/fr/" not in l and "/es/" not in l for l in locs)
            and any(l.endswith("/fr/book") for l in locs) and any(l.endswith("/es/book") for l in locs),
            detail=str(len(locs)))
    s.check("with its alternates and x-default", 'hreflang="x-default"' in xml and "xmlns:xhtml" in xml)
    s.check("no ?lang= address left in it", "?lang=" not in xml)
    for path in ("/getting-here", "/whats-on/at-gudanes", "/privacy", "/legal"):
        s.check(f"{path} is in it (the review found it missing)", any(l.endswith(path) for l in locs))
    robots = m.app.test_client().get("/robots.txt").get_data(as_text=True)
    s.check("robots.txt lets ?lang= be fetched, so its move can be seen", "?lang=" not in robots)
