"""The house's address on the public site: a link to the gates, and near the
top of the home page (9 Nov 2026).

The owner, looking at the footer: "the address here isnt clickable it should
be, its also alot of scrolling on the home page to get to it, we should have
the address elsewhere further up".

WHAT IS HELD HERE.

  - The footer and the menu print the address, and in each it is a link. So
    do Contact (twice), the Stay page's key facts and La Table's, each in the
    layout it always had (the same evening) -- and a sweep of every page in
    the sitemap fails on the address printed as plain words anywhere else, so
    a page that comes back from a design handover with the old line is
    caught. Three pages are allowed it, named below: they state who the house
    is, in law, rather than where. A line under the home page's hero printed
    it too, until the owner's second review of the site (9 Nov 2026, evening)
    took it out: the home page is eight sections now, so the footer's is two
    scrolls away, not seventeen screens.
  - The link goes to the PIN, the same one every other "open in maps" on the
    site uses (house_map_links), and never to a search. A search by name once
    answered from Tokyo, and one by address puts a guest in the village square
    at the bottom of an unlit road: test_where_the_gates_are has the history.
  - With no pin set the address is still printed, and is not a link: absent,
    not guessed.
  - The words come from app.py -- HOUSE_PLACE, built from the same parts as
    the letters' HOUSE_POSTAL -- so the three places cannot drift apart, and
    the line on the foot of every letter is not changed by any of it.
  - "Further up" means the first thing after the hero, before any section.
    test_public_margins measures where that lands on a phone.
  - The telephone and the two addresses beside it in the footer are tel: and
    mailto: links. They already were; the owner's screenshot raised the
    question, so it is held.

The address is drawn by one macro, _house_address.html, imported WITH CONTEXT
in each place. Without the context it cannot see house_place or house_maps,
and every public page goes down -- which these renders would catch too.
"""
import html as htmllib
import re
from html.parser import HTMLParser

from _harness import Suite, db
import _harness
from test_copy_unchanged import public_paths

m = _harness.m

# Made-up coordinates, so the link is seen to follow the setting rather than
# any figure that happens to be in the code.
LAT, LNG = "42.700001", "1.600002"


def _setting(key):
    conn = db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def _pin(lat, lng):
    conn = db()
    for key, value in (("house_lat", lat), ("house_lng", lng)):
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        if value is not None:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _words(fragment):
    """The visible words of a piece of markup, a <br> read as " | "."""
    fragment = re.sub(r"<br\s*/?>", " | ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(htmllib.unescape(fragment).split())


def _addr(fragment):
    """(tag, href or None, words) for the one g-addr in a piece of markup, or None."""
    found = _addrs(fragment)
    return found[0] if found else None


# The class exactly, so the town's own span (g-addr__town) is never taken for
# the address.
ADDR = re.compile(r'<(a|span) class="g-addr(?: [^"]*)?"(?: href="([^"]*)")?[^>]*>(.*?)</\1>', re.S)


def _addrs(fragment):
    """Every g-addr in a piece of markup, as (tag, href or None, words)."""
    return [(hit.group(1), htmllib.unescape(hit.group(2)) if hit.group(2) else None,
             _words(hit.group(3))) for hit in ADDR.finditer(fragment or "")]


def _main(page):
    hit = re.search(r"<main\b[^>]*>(.*?)</main>", page, re.S)
    return hit.group(1) if hit else ""


# Pages that name the house in law -- the data controller, the publisher, the
# party to the contract -- in a sentence, rather than telling anybody where to
# drive. A link there would be noise.
STATED_IN_LAW = ("/privacy", "/legal", "/terms")


class _PlainAddress(HTMLParser):
    """How many times the street is printed as words outside a g-addr."""
    SKIP = {"script", "style", "template", "head", "title", "noscript"}
    VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "col", "embed"}

    def __init__(self, street):
        super().__init__(convert_charrefs=True)
        self.street, self.stack, self.found = street, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        cls = (dict(attrs).get("class") or "").split()
        self.stack.append((tag, "g-addr" in cls or tag in self.SKIP))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        if self.street in data and not any(hidden for _, hidden in self.stack):
            self.found += data.count(self.street)


def _footer(page):
    hit = re.search(r'<footer class="g-footer">(.*?)</footer>', page, re.S)
    return hit.group(1) if hit else ""


def _contact_block(page):
    hit = re.search(r'<p class="g-footer__contact"[^>]*>(.*?)</p>', _footer(page), re.S)
    return hit.group(1) if hit else ""


def _menu(page):
    hit = re.search(r'<p class="g-navwrap__addr">(.*?)</p>', page, re.S)
    return hit.group(1) if hit else ""


def _where(page):
    hit = re.search(r'<p class="g-where">(.*?)</p>', page, re.S)
    return hit.group(1) if hit else ""


def run():
    s = Suite("The address on the public site")
    anon = m.app.test_client()
    was = (_setting("house_lat"), _setting("house_lng"))
    place = m.HOUSE_PLACE
    town = "%s, %s" % (place["locality"], place["country"])
    line = "%s, %s" % (place["street"], town)     # the one-line form
    try:
        s.section("The words, from one place")
        s.check("the public pages print the address as they always have",
                line == "2 Route de Beille, Château-Verdun, Ariège 09310, France",
                detail=str(place))
        s.check("and the letters' line is the one they always had",
                m.HOUSE_POSTAL == "2 Route de Beille · 09310 Château-Verdun · Ariège, France",
                detail=m.HOUSE_POSTAL)

        s.section("With the pin set, every printing of the address opens it")
        _pin(LAT, LNG)
        with m.app.test_request_context("/"):
            google = m.house_map_links()["google"]
        s.check("the pin's link is coordinates, not a search for words",
                re.fullmatch(r"https://www\.google\.com/maps/search/\?api=1&query=%s,%s"
                             % (re.escape(LAT), re.escape(LNG)), google) is not None,
                detail=google)

        home = anon.get("/")
        page = home.get_data(as_text=True)
        s.check("the home page answers", home.status_code == 200, detail=str(home.status_code))

        footer = _addr(_contact_block(page))
        s.check("the footer's address is a link",
                footer is not None and footer[0] == "a", detail=str(footer))
        s.check("to the pin at the gates",
                footer is not None and footer[1] == google, detail=str(footer))
        s.check("on the street and the town, the two lines it has always had",
                footer is not None and footer[2] == "%s | %s" % (place["street"], town),
                detail=str(footer))

        menu = _addr(_menu(page))
        s.check("so is the menu's",
                menu is not None and menu[0] == "a" and menu[1] == google
                and menu[2] == line, detail=str(menu))

        # The owner's second review (9 Nov, evening, section 1): the line under
        # the hero went; the footer's is the address, two scrolls away now.
        s.check("the home page no longer prints it under the hero",
                '<p class="g-where">' not in page, detail=str(_where(page))[:200])

        other = anon.get("/legal").get_data(as_text=True)
        s.check("every public page's footer links it, not only the home page's",
                (_addr(_contact_block(other)) or ("", "", ""))[1] == google,
                detail=str(_addr(_contact_block(other))))

        s.section("And on every page that tells somebody where the house is")
        three = "%s | %s | %s" % (place["street"], place["locality"], place["country"])
        for path, label, expected in (
                ("/contact", "Contact's Finding Us and its key facts", [line, three]),
                ("/book", "the Stay page's key facts", [three]),
                ("/restaurant", "La Table's key facts",
                 ["%s, %s" % (place["street"], place["locality"])])):
            body = anon.get(path).get_data(as_text=True)
            found = _addrs(_main(body))
            s.check("%s: the address is a link to the pin, in the words it always had" % label,
                    [w for _, _, w in found] == expected
                    and all(t == "a" and h == google for t, h, _ in found),
                    detail=str(found))

        m.SITE_IS_LIVE, was_live = True, m.SITE_IS_LIVE
        try:
            paths = [p for p in public_paths(anon) if p not in STATED_IN_LAW]
            plain = []
            for path in paths:
                r = anon.get(path)
                if r.status_code != 200:
                    continue
                parser = _PlainAddress(place["street"])
                parser.feed(r.get_data(as_text=True))
                if parser.found:
                    plain.append("%s (%d)" % (path, parser.found))
        finally:
            m.SITE_IS_LIVE = was_live
        s.check("no page in the sitemap prints the address as words that open nothing",
                len(paths) >= 15 and not plain,
                detail="%d pages swept; plain: %s" % (len(paths), plain))

        s.section("Beside it, the telephone and the addresses are links too")
        block = _contact_block(page)
        shown = re.sub(r"<[^>]+>", " ", block)
        emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", shown)))
        s.check("the footer names the two addresses", len(emails) >= 2, detail=str(emails))
        unlinked = [e for e in emails if 'href="mailto:%s"' % e not in block]
        s.check("each one is a mailto: link", not unlinked, detail=str(unlinked))
        phones = re.findall(r"\+33[\d ]{9,}", shown)
        s.check("the footer gives the telephone", bool(phones), detail=shown[:300])
        untapped = [p for p in phones
                    if 'href="tel:%s"' % re.sub(r"\s", "", p) not in block]
        s.check("and it is a tel: link", not untapped, detail=str(untapped))

        s.section("With no pin, the address is printed and nothing pretends")
        _pin(None, None)
        page = anon.get("/").get_data(as_text=True)
        footer = _addr(_contact_block(page))
        menu = _addr(_menu(page))
        s.check("the footer still gives the street and the town",
                footer is not None and footer[2] == "%s | %s" % (place["street"], town),
                detail=str(footer))
        s.check("but neither is a link",
                all(x is not None and x[0] == "span" and x[1] is None
                    for x in (footer, menu)),
                detail=str((footer, menu)))
        s.check("and nothing in the footer searches a map for the words instead",
                "google.com/maps" not in _footer(page) and "maps.apple.com" not in _footer(page),
                detail="a pin or nothing: a search is a guess, weighted by "
                       "where the person searching happens to be")
    finally:
        _pin(*was)
    return s
