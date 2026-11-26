"""The owner's second sweep of the site, 26 November 2026 (BRIEF-sweep-and-code-review-26-nov.md).

What this holds, in the brief's own numbering:

1.24  The restaurant page is not public. While dining is switched off, /restaurant, /fr/restaurant and
      /es/restaurant are not found, none of them is in the sitemap, and no public page links to one. The
      reservation page stays (an occasion's dinner night is booked there even with La Table shut). Closed
      for the season and open, the page answers as before. The terms no longer mention La Table.
1.19  "Bruce in the hay meadow", caption and photograph description, on /facilities in all three languages.
1.26  The home hero's third line, and only that line, in all three languages.

Negative control: put "Bruce and Alfie" back in facilities.html, or take the dining check out of the
sitemap, and the matching section goes red.
"""
import re

from _harness import Suite, db, visible_text
import _harness
from test_copy_unchanged import public_paths

m = _harness.m

HERO_EN = "A restoration followed the world over, and a château to experience as it returns to life."
HERO_FR = "Une restauration suivie dans le monde entier, et un château à vivre tandis qu’il renaît."
HERO_ES = "Una restauración seguida en todo el mundo, y un castillo que vivir mientras vuelve a la vida."
HERO_OLD = "an invitation extended quietly"
LA_TABLE_LINE = "La Table is not taking bookings at present."


def _setting(conn, key, value):
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    if value is not None:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _flat(text):
    return " ".join((text or "").split())


def _sitemap_paths(anon):
    body = anon.get("/sitemap.xml").get_data(as_text=True)
    return [re.sub(r"^https?://[^/]+", "", loc) for loc in re.findall(r"<loc>([^<]+)</loc>", body)]


# A link to the dining page itself, in any language -- not /restaurant/book.
_DINING_LINK = re.compile(r'href="(?:https?://[^"/]+)?(?:/(?:fr|es))?/restaurant/?(?:\?[^"]*)?"')


def _restaurant(s, anon, conn):
    s.section("1.24 While dining is switched off, the restaurant page is not found")
    _setting(conn, "dining_mode", "hidden")
    for path in ("/restaurant", "/fr/restaurant", "/es/restaurant"):
        r = anon.get(path)
        s.check(f"{path} is not found", r.status_code == 404, detail=f"HTTP {r.status_code}")
    listed = [p for p in _sitemap_paths(anon) if re.fullmatch(r"(?:/(?:fr|es))?/restaurant/?", p)]
    s.check("and none of the three is in the sitemap", not listed, detail=str(listed))
    s.check("while the sitemap still lists the rest", any(p.rstrip("/") == "/book" for p in _sitemap_paths(anon)))
    linking = {}
    for path in dict.fromkeys(public_paths(anon) + ["/restaurant/book", "/this-page-is-not-here"]):
        r = anon.get(path)
        if r.status_code not in (200, 404):
            continue
        hits = _DINING_LINK.findall(r.get_data(as_text=True))
        if hits:
            linking[path] = hits[:2]
    s.check("and no public page links to it", not linking, detail=str(linking)[:400])
    r = anon.get("/restaurant/book")
    s.check("the reservation page still answers, for an occasion's dinner night", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    for mode in ("closed", "open"):
        s.section(f"1.24 With dining {mode}, the page is there as before")
        _setting(conn, "dining_mode", mode)
        for path in ("/restaurant", "/fr/restaurant", "/es/restaurant"):
            r = anon.get(path)
            s.check(f"{path} answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
        s.check("and it is in the sitemap", "/restaurant" in _sitemap_paths(anon))


def _terms(s, anon, conn):
    s.section("1.24 The terms no longer mention La Table")
    s.check("the shipped terms do not", "La Table" not in m.DEFAULT_TERMS)
    was = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()
    stored = ("2. Making a booking\r\nA room booked on this website is confirmed as it is booked.\r\n\r\n"
              "La Table is not taking bookings at present.\r\n\r\nBookings made through another website, "
              "such as Booking.com, are confirmed\r\nby that website, on its own terms.\r\n")
    try:
        _setting(conn, "terms_and_conditions", stored)
        _setting(conn, m.TERMS_SWEEP_26NOV, None)
        s.check("the stored terms lose the sentence once", m.correct_terms_sweep_26nov(conn) == 1)
        conn.commit()
        text = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()[0]
        s.check("and only the sentence, with the blank line after it",
                LA_TABLE_LINE not in text and "as it is booked.\n\nBookings made through another website" in text,
                detail=repr(text[:200]))
        page = _flat(visible_text(m.app.test_client().get("/terms").get_data(as_text=True)))
        s.check("so /terms does not say it", "not taking bookings" not in page and "Bookings made through" in page,
                detail=page[:200])
        _setting(conn, "terms_and_conditions", stored)
        s.check("and it runs once: the sentence written back by hand stays", m.correct_terms_sweep_26nov(conn) == 0
                and LA_TABLE_LINE in conn.execute(
                    "SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()[0])
    finally:
        _setting(conn, "terms_and_conditions", was[0] if was else None)


def _bruce(s, anon):
    s.section("1.19 Bruce, alone, in the hay meadow")
    for path, caption in (("/facilities", "Bruce in the hay meadow"),
                          ("/fr/facilities", "Bruce dans la prairie de fauche"),
                          ("/es/facilities", "Bruce en el prado de siega")):
        html = m.app.test_client().get(path).get_data(as_text=True)
        s.check(f"{path} says {caption!r}", caption in html, detail="the caption")
        s.check(f"{path} names Alfie nowhere, words or photograph descriptions", "Alfie" not in
                re.sub(r'src="[^"]*"', "", html), detail="the photograph's file is still called Alfie+And+Bruce")
    html = m.app.test_client().get("/facilities").get_data(as_text=True)
    s.check("and the description matches", 'alt="Bruce in the hay meadow below the cliffs, an old cart behind him"'
            in html)


def _hero(s, anon):
    s.section("1.26 The hero's third line, and nothing else on the first screen")
    for path, line in (("/", HERO_EN), ("/fr/", HERO_FR), ("/es/", HERO_ES)):
        # A client of its own for each: the language chosen sticks to the visitor.
        html = m.app.test_client().get(path).get_data(as_text=True)
        lede = re.search(r'<p class="g-hero__lede">(.*?)</p>', html, re.S)
        s.check(f"{path}: the new line", lede is not None and _flat(lede.group(1)) == line,
                detail=_flat(lede.group(1)) if lede else "no lede")
        s.check(f"{path}: and not the old one", HERO_OLD not in html)
    html = m.app.test_client().get("/").get_data(as_text=True)
    s.check("the motto above it is unchanged", '<p class="g-hero__motto">Where History Becomes Home</p>' in html)
    s.check("and the line below it",
            "An eighteenth-century château in the French Pyrénées, under restoration since 2013." in html)


def run():
    s = Suite("The site's second sweep, 26 November 2026")
    anon = m.app.test_client()
    conn = db()
    was_dm = (conn.execute("SELECT value FROM app_settings WHERE key = 'dining_mode'").fetchone() or [None])[0]
    try:
        _restaurant(s, anon, conn)
        _terms(s, anon, conn)
        _bruce(s, anon)
        _hero(s, anon)
    finally:
        _setting(conn, "dining_mode", was_dm)
        conn.close()
    return s


if __name__ == "__main__":
    raise SystemExit(0 if run().report() else 1)
