"""The owner's second review of the site (BRIEF-site-review-2-9-nov.md), held.

9 November 2026, evening. The owner read www.chateaugudanes.com at a desk and
on a phone and found it three times too long, in eight button styles, with two
newsletter forms on most pages, the menu marking the wrong page, and words the
owner had asked never to see: "house" for the château, renovation, funding,
artisans, and dishes and drinks promised on a retreat's day. Section 8 of the
brief asked for tests; this suite is the ones that can be read from the page.
The ones that need a browser -- how long a page is, how wide a photograph is
drawn, what fits a phone -- are test_public_pages_measured.

Each check runs over every page in the sitemap, so a page added tomorrow is
held by it the day it appears. Each reader is proved first on a page doctored
to fail it: a reader that cannot find the fault reads as cover, and is worse
than none.

What is held:

  - Every <img> on a public page says what it shows (alt is not empty).
  - One newsletter form a page: the footer's. Eight pages had a second, mid-page,
    and the menu panel carried a third.
  - The menu marks the page you are on, by its address. /whats-on was marked as
    The Estate.
  - None of the words the owner rules out: "house" for the château,
    renovation, grants or funding, artisans, conservators, padel, and no
    exclamation marks. Two magazine titles are allowed by name.
  - No dish or drink named in a retreat's day-by-day. Tastings are activities
    and stay; "vin chaud", the cheeses, the cocktails and the sparkling wine
    were promises for a given day.
  - The home page is the owner's eight sections, in the owner's order, under
    the line the owner chose ("Where History Becomes Home").
  - /gallery puts no more than forty photographs in the page before a click.
  - What Is Open Next is one sentence when every room opens on the same night.
  - The reassurance band is only where a guest pays; the Monument line and the
    band's one line are in the footer.
  - The terms no longer sell La Table, and the two extras the owner reworded
    say what she wrote -- in a database that already had the old words.
"""
import html as htmllib
import re
from datetime import date

from _harness import Suite, db, visible_text
import _harness
from test_copy_unchanged import public_paths
from test_home_pictures import picture_key
from test_page_pictures import _visible

m = _harness.m

# ---------------------------------------------------------------------------
# The readers. Each takes a page's HTML and returns what it found wrong.
# ---------------------------------------------------------------------------

IMG = re.compile(r"<img\b[^>]*>", re.I)
TEMPLATE = re.compile(r"(?is)<template\b.*?</template>")


def imgs_without_alt(html):
    """<img> tags with no alt or an empty one. Inside a <template> they are not
    in the page until a click puts them there, and the lightbox reads their alt
    then; they are held all the same. A script's strings are not the page."""
    bad = []
    for tag in IMG.findall(re.sub(r"(?is)<script\b.*?</script>", " ", html)):
        mo = re.search(r'\salt="([^"]*)"', tag)
        if not mo or not mo.group(1).strip():
            src = re.search(r'\ssrc="([^"]+)"', tag)
            bad.append(src.group(1)[-60:] if src else tag[:60])
    return bad


def newsletter_forms(html):
    return len(re.findall(r'<form\b[^>]*action="[^"]*newsletter[^"]*"', html, re.I))


NAV = re.compile(r'(?is)<nav class="g-nav"[^>]*>(.*?)</nav>')
NAV_LINK = re.compile(r'<a\b([^>]*)>', re.I)


def nav_mismatch(path, html):
    """Where the menu's current-page mark disagrees with the address.

    A link is the page's when its address is the page's or a parent of it
    (/book for /book/3). Every marked link must be one; and a link whose
    address IS the page must be marked.
    """
    mo = NAV.search(html)
    if not mo:
        return ["no main menu on the page"]
    faults = []
    for attrs in NAV_LINK.findall(mo.group(1)):
        href = re.search(r'href="([^"]+)"', attrs)
        if not href:
            continue
        target = re.sub(r"^https?://[^/]+", "", href.group(1)).split("#")[0].split("?")[0] or "/"
        marked = "is-here" in attrs
        ours = path == target or (target != "/" and path.startswith(target.rstrip("/") + "/"))
        if marked and not ours:
            faults.append(f"marks {target}")
        if path == target and not marked:
            faults.append(f"does not mark {target}")
    return faults


FORBIDDEN = re.compile(
    r"\b(house|grants?|funded|funding|renovat\w*|artisans?|conservators?|padel)\b|!", re.I)
# Proper names that carry the letters, allowed by name and nowhere else.
ALLOWED = ["House & Garden", "FIN!"]


def forbidden_words(text):
    for name in ALLOWED:
        text = text.replace(name, " ")
    out = []
    for mo in FORBIDDEN.finditer(text):
        a = max(0, mo.start() - 40)
        out.append(f"{mo.group(0)!r} in ...{text[a:mo.end() + 30]}...")
    return out


DISH = re.compile(
    r"\b(kir|lillet|vin chaud|chees\w*|fromages?|b(?:oe|œ)uf|beef|foie|cassoulet|champagne|"
    r"wines?|crémant|cremant|rosé|spritz|prosecco|sparkling|cocktails? \w+ (?:royale|tonic)|"
    r"hot chocolate|charcuterie)\b", re.I)
# Activities, which the owner keeps: a tasting is something you do that day. And one line the owner
# approved by name for Noël after this brief was written (9 Nov, the retreat days read aloud,
# test_retreat_days): carols and vin chaud by the fire. Nothing else of the kind.
ACTIVITY = re.compile(r"(?i)a tasting of french wines|a wine tasting|wine tasting|carols and vin chaud by the fire")
JOURNEY = re.compile(r'(?is)<ol class="g-journey">(.*?)</ol>\s*(?:<div class="g-journey__|</div></section>)')


def dishes_in_days(html):
    """Dish or drink words inside a retreat's day cards, by day."""
    mo = JOURNEY.search(html)
    if not mo:
        return None
    out = []
    for n, card in enumerate(mo.group(1).split('<li class="g-journey__d">')[1:], start=1):
        words = ACTIVITY.sub(" ", visible_text(card))
        for hit in DISH.finditer(words):
            out.append(f"day {n}: {hit.group(0)!r}")
    return out


HOME_ORDER = ["La Vie de Château", r"The \w+ Rooms", "A Night, or la Vie de Château",
              "From Ruin, Room by Room", "Ways to Spend the Days", "What Guests Say",
              "Come and see it"]
HOME_GONE = ["France at Its Most French", "Bienvenue", "The Estate from Above", "A Night at Gudanes",
             "Brocante Antiques and New Comforts", "One Wedding at a Time", "Follow the Restoration",
             "Dine at La Table", 'class="g-bande', 'class="g-cred"']
# Not the address line under the hero (g-where): the brief took it out, and the owner kept it
# (10 Nov: "i wated another address further up"). test_house_address holds it there.


def home_headings(html):
    return [visible_text(h) for h in re.findall(r"(?is)<h2\b[^>]*>(.*?)</h2>",
                                                html.split('<footer class="g-footer">')[0])]


def home_out_of_order(html):
    heads = home_headings(html)
    at, faults = -1, []
    for want in HOME_ORDER:
        where = next((i for i, h in enumerate(heads) if re.fullmatch(want, h)), None)
        if where is None:
            faults.append(f"no {want!r}")
        elif where < at:
            faults.append(f"{want!r} out of order")
        else:
            at = where
    return faults


def page_imgs(html):
    """<img> in the page itself: not the ones a <template> holds back."""
    return len(IMG.findall(TEMPLATE.sub("", html)))


def run():
    s = Suite("The owner's second review of the site, held")
    anon = m.app.test_client()
    was_live = m.SITE_IS_LIVE
    m.SITE_IS_LIVE = True
    try:
        _run(s, anon)
    finally:
        m.SITE_IS_LIVE = was_live
    return s


def _run(s, anon):
    paths = public_paths(anon)
    pages = {}
    for path in paths:
        r = anon.get(path)
        if r.status_code == 200 and '<footer class="g-footer">' in r.get_data(as_text=True):
            pages[path] = r.get_data(as_text=True)
    s.section("The pages read")
    s.check("the sitemap gives the public pages to read", len(pages) >= 20,
            detail=f"{len(pages)} pages")
    home = pages.get("/", "")
    retreats = [p for p in pages if re.fullmatch(r"/workshops/\d+", p)]
    rooms = [p for p in pages if re.fullmatch(r"/book/\d+", p)]
    s.check("and among them the home page, the retreats and the rooms",
            bool(home) and len(retreats) >= 3 and len(rooms) >= 3,
            detail=f"retreats {retreats}, rooms {rooms}")

    s.section("Every photograph says what it shows")
    s.check("the reader finds a photograph with no words",
            imgs_without_alt('<p><img src="/a.jpg" alt=""><img src="/b.jpg" alt="The salon"></p>') == ["/a.jpg"])
    bad = {p: imgs_without_alt(h) for p, h in pages.items()}
    bad = {p: b for p, b in bad.items() if b}
    s.check("no <img> on a public page has an empty alt", not bad, detail=str(bad)[:600])

    s.section("One newsletter form a page, the footer's")
    s.check("the reader counts two forms where there are two",
            newsletter_forms('<form action="/newsletter/subscribe"></form><form action="/x"></form>'
                             '<form method="post" action="/newsletter/subscribe"></form>') == 2)
    counts = {p: newsletter_forms(h) for p, h in pages.items()}
    s.check("every public page has exactly one", all(c == 1 for c in counts.values()),
            detail=str({p: c for p, c in counts.items() if c != 1}))
    s.check("and it is the footer's",
            all(re.search(r'(?s)<footer class="g-footer">.*action="[^"]*newsletter', h) for h in pages.values()))

    s.section("The menu marks the page you are on")
    doctored = ('<nav class="g-nav"><a href="/book">Stay</a><a href="/whats-on">What’s On</a>'
                '<a href="/facilities" class="is-here">The Estate</a></nav>')
    s.check("the reader catches /whats-on marked as The Estate",
            sorted(nav_mismatch("/whats-on", doctored)) == ["does not mark /whats-on", "marks /facilities"],
            detail=str(nav_mismatch("/whats-on", doctored)))
    wrong = {p: nav_mismatch(p, h) for p, h in pages.items()}
    wrong = {p: w for p, w in wrong.items() if w}
    s.check("on every public page the mark matches the address", not wrong, detail=str(wrong)[:600])
    s.check("/whats-on marks What's On", 'class="is-here"' in NAV.search(pages.get("/whats-on", "")).group(1)
            and "/whats-on" in re.search(r'<a href="([^"]+)" class="is-here"',
                                         NAV.search(pages.get("/whats-on", "")).group(1)).group(1))
    s.check("and the menu says Retreats, at the address it always had",
            re.search(r'href="/workshops"[^>]*>Retreats</a>', NAV.search(home).group(1)) is not None)

    s.section("None of the words the owner rules out")
    s.check("the reader finds each of them",
            len(forbidden_words("the house, a grant, privately funded, a renovation, an artisan, padel, lovely!")) == 7)
    s.check("and passes the two magazine titles", forbidden_words("House & Garden and FIN! Magazine") == [])
    said = {p: forbidden_words(visible_text(h)) for p, h in pages.items()}
    said = {p: w for p, w in said.items() if w}
    s.check("no public page says them", not said, detail=str(said)[:800])

    s.section("No dish or drink named on a retreat's day")
    doctored = ('<ol class="g-journey"><li class="g-journey__d"><p>A wine tasting beside the cellar.</p></li>'
                '<li class="g-journey__d"><p>A Kir Royale on the terrace, and Ariège cheeses.</p></li></ol>'
                '</div></section>')
    found = dishes_in_days(doctored)
    s.check("the reader finds the Kir and the cheeses, and lets the tasting be",
            found == ["day 2: 'Kir'", "day 2: 'cheeses'"], detail=str(found))
    # A retreat has a written day-by-day when its title names one of the four plans (ws_kind in
    # _ws_copy.html); an atelier with any other title has none, and is not a failure of this rule.
    # So every planned retreat must show its days, and all four plans must be among those read --
    # which keeps the check from passing on pages that carry no days at all.
    kinds = {"noel": r"No[eë]l|Christmas", "cooking": r"Cooking|Cuisine",
             "finds": r"Antique|Finds|Brocante", "starry": r"Starry"}
    seen_kinds = set()
    for path in retreats:
        title = re.search(r"(?is)<title>(.*?)</title>", pages[path])
        title = htmllib.unescape(title.group(1)) if title else ""
        kind = next((k for k, pat in kinds.items() if re.search(pat, title)), None)
        found = dishes_in_days(pages[path])
        if kind:
            seen_kinds.add(kind)
            s.check(f"{path}: the day-by-day is there to read", found is not None, detail=title)
        s.check(f"{path}: and names no dish or drink", not found, detail=str(found))
    s.check("all four written plans are among the retreats read", seen_kinds == set(kinds),
            detail=f"read {sorted(seen_kinds)} of {sorted(kinds)}")
    # 10 Nov 2026 (owner): "there won't be pool, it'll be too cold" -- Cooking in the Cuisine, anywhere
    # in its days, its afternoons line or its opening paragraph.
    cooking = [p for p in retreats if "Cooking in the Cuisine" in pages[p]]
    s.check("Cooking in the Cuisine is among the retreats read", bool(cooking))
    for path in cooking:
        seen = visible_text(pages[path])
        s.check(f"{path}: Cooking in the Cuisine offers no pool", not re.search(r"(?i)\bpool\b", seen),
                detail=", ".join(seen[max(0, mo.start() - 40):mo.end() + 20]
                                 for mo in re.finditer(r"(?i)\bpool\b", seen)))

    s.section("The home page is the owner's eight sections")
    s.check("the reader catches a section out of its place",
            home_out_of_order("<h2>La Vie de Château</h2><h2>What Guests Say</h2><h2>The Six Rooms</h2>"
                              "<h2>A Night, or la Vie de Château</h2><h2>From Ruin, Room by Room</h2>"
                              "<h2>Ways to Spend the Days</h2><h2>Come and see it</h2>"
                              '<footer class="g-footer">') != [])
    s.check("in the owner's order", home_out_of_order(home) == [],
            detail=f"{home_out_of_order(home)} in {home_headings(home)}")
    s.check("with nothing between them that the owner took out",
            not [g for g in HOME_GONE if g in home], detail=str([g for g in HOME_GONE if g in home]))
    s.check("under the line the owner chose", "Where History Becomes Home" in home
            and "A restoration followed the world over, and an invitation extended quietly." in home)
    s.check("and not the one that replaced it", "Where History Comes Back to Life" not in home)
    s.check("the estate from the air is the rooms' first slide",
            re.search(r'g-homeroom--aerial g-car__s"[^>]*aria-label="1 / ', home) is not None)

    s.section("The gallery before a click")
    gal = pages.get("/gallery", "")
    s.check("the reader leaves out what a template holds",
            page_imgs('<img alt="a" src="/1"><template><img alt="b" src="/2"></template>') == 1)
    s.check("no more than forty photographs reach the page", 0 < page_imgs(gal) <= 40,
            detail=f"{page_imgs(gal)} images")
    s.check("and the rest wait behind See all", "<template data-lb-more>" in gal and "See all" in gal)
    # The pairs /restoration no longer shows, by their files: the kitchen, the bathrooms' room, its window,
    # the windows, and the second horse. Where the gallery already had one under its own caption it is not
    # drawn twice, so the address is what is looked for, in the page or behind See all.
    moved = ["horse-dining-room-b.jpg", "2b9c1192-bf9a-40a9-8913-4bc98c8a43c7", "92af7109-0e28-4b75-bdf0-4b1a6fe7cc9a",
             "1583425897142-1GIQ7UVNLLEXM34LO9PY", "1583425754468-1J36HQT5KRQFZCNPSE4S", "1583503973363-BORNI4RB6JAHAQXXA46X",
             "97532472-ef1c-439c-bc1e-da09f3d43046", "f42741a3-9246-4608-8c8d-fd0408104b5c"]
    s.check("every before-and-after /restoration let go is on the gallery, under The Restoration",
            'id="restoration"' in gal and all(k in gal for k in moved), detail=str([k for k in moved if k not in gal]))

    s.section("What Is Open Next")
    with m.app.test_request_context("/book"):
        macro = m.app.jinja_env.get_template("_availability_strip.html").make_module(
            {"house_rooms": {"count": 5, "count_word": "five"}}).next_free
        same = [{"date": "12 November", "date_iso": "2026-11-12", "room_name": f"Room {i}",
                 "nights": 10, "price": 270} for i in range(5)]
        differ = same[:4] + [dict(same[4], date="20 November", date_iso="2026-11-20")]
        one = visible_text(str(macro(same))).replace(" .", ".")
        table = str(macro(differ))
        short = visible_text(str(macro(same[:4])))
    s.check("every room open the same night is one sentence",
            "All five rooms are open from 12 November." in one and "Up to 10 nights" not in one, detail=one)
    s.check("but never \"all\" when a room is missing from the list", "All five" not in short, detail=short)
    s.check("and the rows are drawn only when the rooms differ", table.count('class="g-nextf__i"') == 5)

    s.section("The reassurance band where a guest pays, and a line elsewhere")
    s.check("/book draws the band", 'class="g-assure"' in pages.get("/book", ""))
    s.check("so does a retreat's page", all('class="g-assure"' in pages[p] for p in retreats))
    s.check("/events does not", 'class="g-assure"' not in pages.get("/events", ""))
    s.check("it has the one line in its footer instead", "g-footer__assure" in pages.get("/events", ""))
    s.check("no page ends on the Monument sliver", not [p for p, h in pages.items() if 'class="g-cred"' in h])
    s.check("its sentence is in the footer", all("g-footer__cred" in h for h in pages.values()))

    s.section("The terms stop selling La Table")
    terms = visible_text(pages.get("/terms", ""))
    s.check("/terms says La Table is not taking bookings", "La Table is not taking bookings at present." in terms)
    s.check("and has no table request or no-show rule",
            "A table at La Table is a request" not in terms and "no-shows" not in terms)
    conn = db()
    put_back = _harness.keep_rows(conn, "app_settings", "key IN ('terms_and_conditions', ?)",
                                  (m.TERMS_LA_TABLE_9NOV,))
    try:
        old_text = ("2. Making a booking\n" + m.TERMS_LA_TABLE_EDITS[0][0] + "\n4. Cancellations\n"
                    + m.TERMS_LA_TABLE_EDITS[1][0] + "\n5. Dining and workshops\n" + m.TERMS_LA_TABLE_EDITS[2][0]
                    + "- If we cancel a workshop for any reason, you are refunded.\n")
        conn.execute("DELETE FROM app_settings WHERE key IN ('terms_and_conditions', ?)", (m.TERMS_LA_TABLE_9NOV,))
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('terms_and_conditions', ?)",
                     (old_text.replace("\n", "\r\n"),))
        n = m.correct_terms_la_table(conn)
        now = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()["value"]
        s.check("a database holding the old terms has the three clauses changed", n == 3, detail=str(n))
        s.check("to the owner's line, with the workshop clause left alone",
                "La Table is not taking bookings at present." in now and "no-shows" not in now
                and "decline a table" not in now and "If we cancel a workshop" in now, detail=now)
        conn.execute("UPDATE app_settings SET value = ? WHERE key = 'terms_and_conditions'", (old_text,))
        s.check("and once only: terms put back afterwards are the owner's and stay",
                m.correct_terms_la_table(conn) == 0)
    finally:
        put_back()

    s.section("The two extras the owner reworded")
    mine = "name LIKE 'ZZRV2 %' OR name LIKE 'Airport Transfer ZZRV2%'"
    put_back_x = _harness.keep_rows(conn, "extras", mine)
    put_back_k = _harness.keep_rows(conn, "app_settings", "key = ?", (m.EXTRA_WORDING_9NOV,))
    try:
        # A database that already had the old words, which is the house's: the
        # correction has not run yet, and the rows say what the seed wrote.
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.EXTRA_WORDING_9NOV,))
        for name, desc in (("ZZRV2 tour", m.TOUR_OLD),
                           ("ZZRV2 tour long", m.TOUR_OLD + ", one price for the booking."),
                           ("ZZRV2 tour edited", "The owner's own tour, in her words."),
                           ("Airport Transfer ZZRV2", ""),
                           ("Airport Transfer ZZRV2 kept", "Her words.")):
            conn.execute("INSERT INTO extras (name, description, price, active) VALUES (?, ?, 1, 1)", (name, desc))
        conn.commit()
        m.correct_extra_wording(conn)
        conn.commit()
        got = {r["name"]: r["description"] for r in conn.execute(f"SELECT name, description FROM extras WHERE {mine}")}
        s.check("the tour that said only the old line says the owner's",
                got.get("ZZRV2 tour") == "A guided tour of the château and its restoration, on a day of your stay you choose.",
                detail=str(got.get("ZZRV2 tour")))
        s.check("a longer one keeps the rest of its words",
                (got.get("ZZRV2 tour long") or "").startswith(m.TOUR_NEW + ", one price"), detail=str(got.get("ZZRV2 tour long")))
        s.check("one the owner rewrote is hers", got.get("ZZRV2 tour edited") == "The owner's own tour, in her words.")
        s.check("the transfer with no line gets the owner's", got.get("Airport Transfer ZZRV2") == m.TRANSFER_LINE,
                detail=str(got.get("Airport Transfer ZZRV2")))
        s.check("a transfer the owner described keeps her words", got.get("Airport Transfer ZZRV2 kept") == "Her words.")
        conn.execute("UPDATE extras SET description = '' WHERE name = 'Airport Transfer ZZRV2'")
        conn.commit()
        s.check("and once only: a line cleared afterwards stays cleared",
                m.correct_extra_wording(conn) == 0 and conn.execute(
                    "SELECT description FROM extras WHERE name = 'Airport Transfer ZZRV2'").fetchone()["description"] == "")
    finally:
        put_back_x()
        put_back_k()
        conn.close()
    _book_cards(s, anon)
    _one_home(s, anon)


ROOM_CARD = re.compile(r'(?s)<article class="g-card g-card--room".*?</article>')


def card_photos(html):
    """Each /book room card: its name, and the alt of every photograph in its slider, in order."""
    out = {}
    for card in ROOM_CARD.findall(html):
        name = re.search(r'class="g-card__name">([^<]+)', card)
        out[name.group(1).strip() if name else "?"] = (re.findall(r'<img\b[^>]*\bsrc="([^"]*)"', card),
                                                       re.findall(r'<img\b[^>]*\balt="([^"]*)"', card))
    return out


def _book_cards(s, anon):
    """11 Nov 2026 (owner): "make this the primary photo" (the Marquis Suite, then the Family Suite), "whys there
    none of the views", "wheres the bathroom photos". Each card on /book ends with its own view, where the room
    has one; the bathrooms have their own section."""
    s.section("The room cards on /book: the photographs the owner chose, the views, and the bathrooms")
    cards = card_photos(anon.get("/book").get_data(as_text=True))
    s.check("the room cards are there to read", len(cards) >= 4, detail=str(list(cards)))
    marquis, family = cards.get("The Marquis Suite"), cards.get("The Family Suite")
    s.check("the Marquis Suite leads with the gold four-poster by the fireplace",
            bool(marquis) and "GudanesCookingintheKitchenStay59of925" in marquis[0][0], detail=str(marquis and marquis[0][:1]))
    s.check("the Family Suite leads with the two chairs at the open windows",
            bool(family) and "799483474" in family[0][0], detail=str(family and family[0][:1]))
    with m.app.test_request_context("/"):
        mod = m.app.jinja_env.get_template("_room_photos.html").module
        lists = {n: list(mod.ROOM_PHOTOS.get(n, [])) + list(mod.ROOM_EXTRA.get(n, [])) for n in cards}
        baths = {a for _, a in mod.BATH_PHOTOS}
    with_view = [n for n, l in lists.items() if any(a == "The view from your window" for _, a in l)]
    s.check("several rooms have a view of their own to show", len(with_view) >= 3, detail=str(with_view))
    lost = [n for n in with_view if "The view from your window" not in cards[n][1]]
    s.check("and every one of them shows it on its card", not lost, detail=str(lost))
    # The bathrooms have a section of their own (owner, 11 Nov: "they are a real feature, they are incredible"),
    # with every photograph of them and the owner's facts, so the cards no longer carry one each.
    # 11 Nov, evening (the owner's site-copy brief, part 1): one large photograph (the long bathroom) and three
    # beneath, and the points with a drawing each. 13 Nov (owner): "move this down on the page ... the parisian
    # wallpaper etc wheres all the stuff i said" -- below the rooms again, with every fact of 11 Nov.
    page = anon.get("/book").get_data(as_text=True)
    at = page.find('<section class="g-sec g-baths-sec"')
    sec = page[at:page.find("</div></section>", at)] if at != -1 else ""
    s.check("/book has a section on the bathrooms", bool(sec))
    bar_at, rooms_at = page.find('class="g-book-bar"'), page.find('id="rooms"')
    s.check("below the rooms, as #bathrooms", 'id="bathrooms"' in sec and bar_at < rooms_at < at,
            detail=f"bar {bar_at}, rooms {rooms_at}, bathrooms {at}")
    shown = re.findall(r'<img\b[^>]*\balt="([^"]*)"', sec)
    lead = re.search(r'<figure class="g-baths__lead">.*?\balt="([^"]*)"', sec, re.S)
    s.check("one large photograph, the long bathroom", bool(lead) and lead.group(1) == "One of the ground-floor bathrooms",
            detail=str(lead and lead.group(1)))
    # Four of the five: the fifth is the same bath from almost the same place as another.
    s.check("and three beneath it: four of them in all, each once", len(baths & set(shown)) >= 4 and len(shown) == 4,
            detail=str(shown))
    points = re.findall(r'<li class="g-baths__pt">.*?<svg', sec, re.S)
    s.check("the owner's eight points, each with its drawing", len(points) == 8, detail=f"{len(points)}")
    words = visible_text(sec)
    for fact in ("Installed in 2019", "historic monument", "together on the ground floor",
                 "Bespoke, by Antoinette Poisson, made by hand in Paris", "Eighteenth-century terracotta tiles",
                 "Painted with custom artwork and the château’s logo", "Luxury fittings throughout", "Luxury linen",
                 "Luxury shampoo, conditioner and bath gel", "Heaters", "Mountain views",
                 "The King Suite, the Marquis Suite and the Family Suite have the use of a larger bathroom, with a bath"):
        s.check(f"and says: {fact}", fact in words)
    cards = re.findall(r'<a class="g-tags__bath" href="#bathrooms">([^<]*)</a>', page)
    s.check("every room card's bathroom says it is your own, and opens the section",
            bool(cards) and all(c.startswith("Your own private bathroom") for c in cards), detail=str(cards))


# A photograph drawn large: a page's hero, a room page's hero, one across the page, one in a frame, a before or after.
FEATURE = re.compile(r"\b(g-hero__img|g-roomhero|g-plein__img|g-cadre__img|g-ba__img)\b")


def photos_by_page(pages):
    """For each page: the photographs it shows (by what each is), and those it draws large. Not the menu drawer's
    picture, not the lightbox's or a gallery's hidden extras, not the footer."""
    shown, large = {}, {}
    for path, page in pages.items():
        page = _visible(page)
        page = re.sub(r"(?s)<template\b.*?</template>", "", page)
        page = re.sub(r'(?s)<nav class="g-navwrap.*?</nav>', "", page)
        page = page.split('<footer class="g-footer">')[0]
        shown[path], large[path] = set(), set()
        for tag in re.findall(r"<img\b[^>]*>", page):
            src = re.search(r'\ssrc="([^"]*)"', tag)
            if not src or not src.group(1):
                continue
            key = picture_key(htmllib.unescape(src.group(1)))
            shown[path].add(key)
            cls = re.search(r'\sclass="([^"]*)"', tag)
            if cls and FEATURE.search(cls.group(1)):
                large[path].add(key)
    return shown, large


def two_homes(pages, exempt=frozenset()):
    """Photographs drawn large on one page and shown on another too, the gallery apart."""
    shown, large = photos_by_page(pages)
    out = []
    for path, keys in large.items():
        if path == "/gallery":
            continue
        for key in keys - set(exempt):
            elsewhere = sorted(p for p, ks in shown.items() if key in ks and p not in (path, "/gallery"))
            if elsewhere:
                out.append((key, path, elsewhere))
    return sorted(out)


def _one_home(s, anon):
    """The second review, 3.3: "One home per hero photograph; the gallery may repeat it." The woman on the ladder at
    the fresco was on five pages, the roof open to the mountains on four. A room's own photographs are not in this:
    a room is shown by its photographs wherever it is offered."""
    s.section("One home for each photograph drawn large")
    a = '<section><img class="g-hero__img" src="/static/img/x/one.jpg" alt="One"></section>'
    b = '<section><img src="/static/img/x/one.jpg" alt="One again"><img class="g-hero__img" src="/static/img/x/two.jpg" alt="Two"></section>'
    found = two_homes({"/a": a, "/b": b, "/gallery": b})
    s.check("the reader finds a hero shown again on another page, and lets the gallery be",
            [(k, p, e) for k, p, e in found] == [("x/one.jpg", "/a", ["/b"])], detail=str(found))
    pages = {}
    for path in public_paths(anon):
        r = anon.get(path)
        if r.status_code == 200:
            pages[path] = r.get_data(as_text=True)
    with m.app.test_request_context("/"):
        mod = m.app.jinja_env.get_template("_room_photos.html").module
        own = {picture_key(src) for lists in (mod.ROOM_PHOTOS, mod.ROOM_EXTRA) for v in lists.values() for src, _ in v}
        own |= {picture_key(src) for src, _ in mod.BATH_PHOTOS}
    found = two_homes(pages, own)
    s.check("no photograph drawn large on one page is shown on another", not found,
            detail="; ".join(f"{k} (large on {p}, also {', '.join(e)})" for k, p, e in found[:5]))
    shown, _ = photos_by_page(pages)
    for name, key in (("the roof open to the mountains", "found/mountains-through-the-roof.jpg"),
                      ("the woman on the ladder at the painted medallion",
                       picture_key("https://images.squarespace-cdn.com/content/53c3b576e4b02bad423517b8/"
                                   "1582799762807-88XQQSIAXWOEHIA2UVSO/restoration+1.jpg"))):
        homes = sorted(p for p, ks in shown.items() if key in ks and p != "/gallery")
        s.check(f"{name} lives on /restoration alone", homes == ["/restoration"], detail=str(homes))


if __name__ == "__main__":
    print(run().report())
