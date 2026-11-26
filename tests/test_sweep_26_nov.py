"""The owner's second sweep of the site, 26 November 2026 (BRIEF-sweep-and-code-review-26-nov.md).

What this holds, in the brief's own numbering:

1.24  The restaurant page is not public. While dining is switched off, /restaurant, /fr/restaurant and
      /es/restaurant are not found, none of them is in the sitemap, and no public page links to one. The
      reservation page stays (an occasion's dinner night is booked there even with La Table shut). Closed
      for the season and open, the page answers as before. The terms no longer mention La Table.
1.19  "Bruce in the hay meadow", caption and photograph description, on /facilities in all three languages.
1.26  The home hero's third line, and only that line, in all three languages.
C     The naming rule. They are retreats, and "the full château experience" is what describes them: under the
      Retreats heading, on each retreat's page, in the night-or-retreat comparison, and "Experience the château"
      on the buttons. No public template says workshop, atelier or session where a guest reads it; the guests'
      words are signed "Retreat guest". French says séjour, never retraite or atelier; Spanish retiro, never
      taller -- in the hand-written tables, in the model's instructions, and in what it had already written.
C2    Antique & French Finds is a brocante tour: in its title, description, eyebrow and opening, and on its
      cards, two or three times in its body and no more, and still a retreat.

Negative control: put "Bruce and Alfie" back in facilities.html, or take the dining check out of the
sitemap, and the matching section goes red. Put "Discover the retreats" back on the home page, or "Les
retraites" back in translations.py, and the naming rule's section goes red.
"""
import os
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
    """Visible text on one line, without the space an inline element leaves before the punctuation after it."""
    return re.sub(r"\s+(?=[,.;:])", "", " ".join((text or "").split()))


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


FULL = "The full château experience"
OLD_WORDS = re.compile(r"\b(?:workshops?|ateliers?|sessions?)\b", re.I)
# Where "session" is not the category: the privacy notice's browser session and a recorder of sessions, which
# are the accurate words for what a cookie does; and What's On's three-hour "Découverte Aqua" session at the
# Thermes d'Ax, which is the spa's, not the house's.
NOT_THE_CATEGORY = {"privacy.html": ("session recorder", "remembers your session"),
                    "whats_on.html": ("“Découverte Aqua” session",)}
# The partials the public pages are drawn from, which do not extend the base themselves.
PUBLIC_PARTIALS = ("_stay_or_workshop.html", "_ws_copy.html", "_exclusive.html", "_notify.html",
                   "_retreat_included.html", "_stay_bits.html", "_stay_panel.html", "_arrival_card.html")


def _template_words(src):
    """What a guest reads in a template: comments, code, tags and expressions out; visible attributes kept."""
    t = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.S | re.I)
    quoted = " | ".join(re.findall(r"""(?:t\(|email_button\([^,]*,\s*|or\s+)'([^']*)'""", t))
    t = re.sub(r"\{%.*?%\}", " ", t, flags=re.S)
    t = re.sub(r"\{\{.*?\}\}", " ", t, flags=re.S)
    attrs = " | ".join(re.findall(r'(?:alt|title|aria-label|placeholder)="([^"]*)"', t))
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join((t + " | " + attrs + " | " + quoted).split())


def _public_templates():
    folder = os.path.join(_harness.ROOT, "templates")
    out = {}
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".html"):
            continue
        src = open(os.path.join(folder, name), encoding="utf-8").read()
        guest = ('{% extends "public_base.html" %}' in src or name in PUBLIC_PARTIALS
                 or name in ("public_base.html", "_email.html", "guest_account.html", "workshop_manage.html",
                             "workshop_feedback_form.html", "guest_full_statement.html", "unsubscribe.html"))
        if guest:
            out[name] = src
    return out


def _naming_rule(s, anon, conn):
    s.section("C The full château experience, where the brief puts it")
    page = m.app.test_client().get("/workshops").get_data(as_text=True)
    under = re.search(r'<h2 class="g-place">The Retreats</h2>\s*<p class="g-p g-lede-first">([^<]+)</p>', page)
    s.check("under The Retreats heading on /workshops", bool(under) and under.group(1).startswith(FULL),
            detail=under.group(1) if under else "no line under the heading")
    home = m.app.test_client().get("/").get_data(as_text=True)
    s.check("under La Vie de Château on the home page",
            re.search(r'id="full-h">La Vie de Château</h2>\s*<p class="g-full__p"><em>' + FULL, home) is not None)
    s.check("in the comparison: a night, a room and breakfast; a retreat, the full château experience",
            "A night: a room and breakfast. A retreat: the full château experience" in page)
    s.check("and on the retreat's own column of the short comparison",
            "The full château experience, over three to seven nights." in home)
    s.check("on the page a stray address lands on",
            FULL in m.app.test_client().get("/not-a-page-here").get_data(as_text=True))
    retreats = [r["id"] for r in conn.execute(
        "SELECT DISTINCT workshops.id FROM workshops JOIN workshop_sessions ON workshop_sessions.workshop_id = workshops.id "
        f"WHERE workshops.active = 1 AND workshop_sessions.start_date >= ? AND {m.WORKSHOP_IS_PUBLIC}",
        (m.house_today_iso(),))]
    missing = []
    for wid in retreats:
        r = m.app.test_client().get(f"/workshops/{wid}", follow_redirects=True)
        if r.status_code == 200 and "One price for the full château experience" not in r.get_data(as_text=True):
            missing.append(wid)
    s.check("on each retreat's own page", retreats and not missing, detail=f"{len(retreats)} retreats, missing {missing}")
    # A night is never called that: the phrase belongs to the retreats.
    night_cols = re.findall(r'<article class="g-sow__col(?! g-sow__col--full)[^"]*">(.*?)</article>', home, re.S)
    s.check("and a night is never called it", night_cols and not any("full château experience" in c.lower()
                                                                     for c in night_cols))

    s.section("C The buttons say Experience the château")
    still = [p for p in public_paths(anon) if "Discover the retreats" in m.app.test_client().get(p).get_data(as_text=True)]
    s.check("no public page says Discover the retreats", not still, detail=str(still[:5]))
    s.check("the home page's do", home.count(">Experience the château</a>") >= 2,
            detail=str(home.count(">Experience the château</a>")))

    s.section("C The guests' words are signed as retreat guests")
    s.check("six of them, on the home page and /workshops",
            home.count("Retreat guest, ") + page.count("Retreat guest, ") == 6
            and "Workshop guest" not in home + page,
            detail=f"{home.count('Retreat guest, ')} + {page.count('Retreat guest, ')}")

    s.section("C No workshop, atelier or session where a guest reads")
    found = {}
    for name, src in _public_templates().items():
        words = _template_words(src)
        for ok in NOT_THE_CATEGORY.get(name, ()):
            words = words.replace(ok, "")
        hits = [words[max(0, x.start() - 40):x.end() + 30] for x in OLD_WORDS.finditer(words)]
        if hits:
            found[name] = hits[:2]
    s.check("in any public template, its partials, a guest's own pages or the letters' frame", not found,
            detail=str(found)[:500])

    s.section("C In French a séjour, in Spanish a retiro")
    import translations
    import page_text
    fr_bad = re.compile(r"\b(?:retraites?|ateliers?)\b", re.I)
    es_bad = re.compile(r"\btaller(?:es)?\b", re.I)
    s.check("the public prose table says neither retraite nor atelier",
            not [v for v in page_text.FR.values() if fr_bad.search(v)])
    s.check("nor taller in Spanish", not [v for v in page_text.ES.values() if es_bad.search(v)])
    guest_keys = ("Retreats", "Retreat", "Your retreat", "Your retreats", "The retreats", "Workshops", "Workshop",
                  "Your workshop", "Pay for this retreat", "The ateliers", "View this workshop", "Workshops & Retreats")
    s.check("the named words a guest sees are séjours",
            all("séjour" in translations.FR[k].lower() and not fr_bad.search(translations.FR[k]) for k in guest_keys),
            detail=str({k: translations.FR[k] for k in guest_keys if fr_bad.search(translations.FR[k])}))
    s.check("and retiros", all(not es_bad.search(translations.ES.get(k, "")) for k in guest_keys))
    s.check("so a guest's page does not head two lists Vos séjours",
            translations.FR["Your stays"] != translations.FR["Your retreats"])
    s.check("the model is told the house's words", "un sejour" in m.TRANSLATION_SYSTEM
            and "NEVER 'une retraite'" in m.TRANSLATION_SYSTEM and "never 'un taller'" in m.TRANSLATION_SYSTEM
            and "Ateliers" not in m.TRANSLATION_SYSTEM and "workshops" not in m.TRANSLATION_SYSTEM)

    s.section("C What the model had already written goes back to it, once")
    put_back = _harness.keep_rows(conn, "page_translations", "source_text LIKE 'ZZSWEEP%'")
    put_back_k = _harness.keep_rows(conn, "app_settings", "key = ?", (m.TRANSLATIONS_NAMING_26NOV,))
    try:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.TRANSLATIONS_NAMING_26NOV,))
        now = "2026-11-26T09:00:00+00:00"
        rows = [("ZZSWEEP one", "fr", "Les retraites au château", "machine"),
                ("ZZSWEEP two", "es", "Los talleres del castillo", "machine"),
                ("ZZSWEEP three", "fr", "Une retraite relue par une personne", "approved"),
                ("ZZSWEEP four", "fr", "Le séjour au château", "machine")]
        for src, lang, said, status in rows:
            conn.execute("INSERT INTO page_translations (source_text, lang, translated, status, created_at, translated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (src, lang, said, status, now, now))
        conn.commit()
        s.check("with no model there, the job leaves them as they are",
                m.run_page_translation_job(conn) == "no model provider configured"
                and conn.execute("SELECT status FROM page_translations WHERE source_text = 'ZZSWEEP one'")
                .fetchone()[0] == "machine")
        s.check("asked to, it sends back the two that use an old word", m.requeue_old_retreat_words(conn) == 2)
        got = dict(conn.execute("SELECT source_text, status FROM page_translations WHERE source_text LIKE 'ZZSWEEP%'"))
        s.check("as wanted, to be translated again", got.get("ZZSWEEP one") == "wanted" and got.get("ZZSWEEP two") == "wanted",
                detail=str(got))
        s.check("leaving the one a person approved, and the one already right",
                got.get("ZZSWEEP three") == "approved" and got.get("ZZSWEEP four") == "machine", detail=str(got))
        conn.execute("UPDATE page_translations SET status = 'machine', translated = 'Une retraite' "
                     "WHERE source_text = 'ZZSWEEP one'")
        conn.commit()
        s.check("and only once", m.requeue_old_retreat_words(conn) == 0)
    finally:
        put_back()
        put_back_k()


def _brocante_tour(s, anon, conn):
    s.section("C2 Antique & French Finds, a brocante tour and still a retreat")
    r = m.app.test_client().get("/retreats/antique-and-french-finds")
    html = r.get_data(as_text=True)
    if r.status_code != 200:
        s.check("the page answers", False, detail=f"HTTP {r.status_code}")
        return
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()
    s.check("its title", title == "Antique &amp; French Finds · A Brocante Tour &amp; Château Retreat — Château de Gudanes",
            detail=title)
    s.check("its description", '<meta name="description" content="A three-night brocante and antiques tour from a château '
            'in the French Pyrénées: markets, dealers and vide-greniers by day, the full château experience by night.">' in html)
    s.check("the line above its heading", '<p class="g-hero__eyebrow">A Brocante Tour &amp; Château Retreat</p>' in html)
    body = _flat(visible_text(html.split("<body", 1)[1]))
    s.check("the invitation opens on it", "A three-day brocante tour from the château, devoted to chiner: the French art "
            "of hunting, unhurried, for beautiful old things." in body)
    s.check("its heading is kept", "Chiner: the French art of the find" in body)
    tours = len(re.findall(r"brocante tour|antiques tour", body, re.I))
    s.check("two or three times in the body, no more", 2 <= tours <= 3, detail=str(tours))
    s.check("and it is still the full château experience", "full château experience" in body)
    s.check("the château's own tour is left as it is", "A tour of the château" in body or "tour of the château" in body)
    # The home page's cards carry the short line (ws_hook); /workshops's carry the longer one (ws_lead), below.
    page = _flat(visible_text(m.app.test_client().get("/").get_data(as_text=True)))
    s.check("/: its card says so", "A brocante and antiques tour: three days of chiner, the art of the find" in page)
    page = _flat(visible_text(m.app.test_client().get("/workshops").get_data(as_text=True)))
    s.check("/workshops: its card description", "A three-day brocante tour of the markets, brocantes and vide-greniers "
            "of the Ariège" in page)
    s.check("/workshops: What You’ll Do, the brocantes", "A guided tour of the brocantes and vide-greniers of the Ariège"
            in page)
    # What a reader sees, the tab's title included. The link preview's og:title is an attribute, and the page
    # translator has never translated attributes, on this page or any other.
    fr = m.app.test_client().get("/fr/retreats/antique-and-french-finds").get_data(as_text=True)
    fr_seen = " ".join(m.translation_key(n) for _a, _b, n in m.page_text_spans(fr))
    s.check("in French, un circuit des brocantes", fr_seen.count("circuit des brocantes") >= 2
            and "Brocante Tour" not in fr_seen and "brocante tour" not in fr_seen, detail=fr_seen[:200])


def _occasion_label(s, conn):
    s.section("2.12 An occasion page says Room and breakfast once")
    # One of its own, live and ahead: the local copy holds none, and the house's thirteen are not this test's.
    from datetime import timedelta
    today, now = m.house_today(), "2026-11-26T09:00:00+00:00"
    made = conn.execute(
        """INSERT INTO stay_occasions (name, slug, first_night, last_night, status, kind, created_at, updated_at)
           VALUES ('ZZSWEEP Occasion', 'zzsweep-occasion', ?, ?, 'live', 'stay', ?, ?)""",
        ((today + timedelta(days=200)).isoformat(), (today + timedelta(days=202)).isoformat(), now, now)).lastrowid
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute("INSERT INTO stay_occasion_prices (occasion_id, room_id, price) VALUES (?, ?, 300)", (made, room["id"]))
    conn.commit()
    occ = conn.execute("SELECT id, slug, chef_on_site FROM stay_occasions WHERE id = ?", (made,)).fetchone()
    try:
        conn.execute("UPDATE stay_occasions SET chef_on_site = 0 WHERE id = ?", (occ["id"],))
        conn.commit()
        with m.app.test_request_context("/"):
            path = m.url_for("stay_occasion_page", slug=occ["slug"])
        text = _flat(visible_text(m.app.test_client().get(path).get_data(as_text=True)))
        s.check("with the chef away, the owner's line is there", m.CHEF_OFF_SITE_LINE in text, detail=path)
        s.check("and the label is not printed in front of it again", "Room and breakfast Room and breakfast" not in text)
        conn.execute("UPDATE stay_occasions SET chef_on_site = 1 WHERE id = ?", (occ["id"],))
        conn.commit()
        text = _flat(visible_text(m.app.test_client().get(path).get_data(as_text=True)))
        s.check("with the chef in, the label still leads its line", "Chef in the house " + m.CHEF_ON_SITE_LINE in text)
    finally:
        conn.execute("DELETE FROM stay_occasion_prices WHERE occasion_id = ?", (made,))
        conn.execute("DELETE FROM stay_occasions WHERE id = ?", (made,))
        conn.commit()


def _footer(s):
    s.section("1.21 The newsletter promises no more rooms")
    for path, line in (("/", "The restoration as it happens, and the retreat dates."),
                       ("/fr/", "La restauration au fil des travaux, et les dates des séjours."),
                       ("/es/", "La restauración a medida que avanza y las fechas de los retiros.")):
        html = m.app.test_client().get(path).get_data(as_text=True)
        s.check(f"{path}: {line[:40]}...", line in html and "as they open" not in html
                and "à mesure qu'elles ouvrent" not in html)


def _winter_rest(s, conn):
    s.section("2.2 The winter rest is said as one")
    rooms = [r["id"] for r in conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 3")]
    year = m.house_today().year + 1
    start, end = f"{year}-12-01", f"{year + 1}-02-15"
    put_back = _harness.keep_rows(conn, "app_settings", "key = ?", (m.WINTER_REST_AS_SEASON_26NOV,))
    made_blocks, made_season = [], None
    try:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.WINTER_REST_AS_SEASON_26NOV,))
        now = "2026-11-26T09:00:00+00:00"
        made_season = conn.execute(
            "INSERT INTO room_seasons (name, start_md, end_md, room_ids, display_mode, note, reason, created_at) "
            "VALUES ('ZZSWEEP winter', '12-01', '02-15', ?, 'booked', NULL, 'too large to heat', ?)",
            (str(rooms[0]), now)).lastrowid
        for rid, a, b, reason, season in ((rooms[0], start, end, "too large to heat", made_season),
                                          (rooms[1], f"{year}-12-20", f"{year + 1}-01-03", "family", None),
                                          (rooms[2], start, end, "Booking.com stay", None)):
            made_blocks.append(conn.execute(
                "INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at, display_mode, season_id) "
                "VALUES (?, ?, ?, ?, ?, 'booked', ?)", (rid, a, b, reason, now, season)).lastrowid)
        conn.commit()
        s.check("the winter's closure is changed, and only it", m.show_winter_rest_as_season(conn) == 1)
        conn.commit()
        got = {r["id"]: (r["display_mode"], r["note"]) for r in conn.execute(
            f"SELECT id, display_mode, note FROM room_blocks WHERE id IN ({','.join('?' * len(made_blocks))})",
            made_blocks)}
        mode, note = got[made_blocks[0]]
        s.check("closed for the season, with the date it reopens", mode == "season" and note and "reopens on 15 Feb"
                in note, detail=str(got[made_blocks[0]]))
        s.check("a family Christmas and a channel's stay still show as booked",
                got[made_blocks[1]][0] == "booked" and got[made_blocks[2]][0] == "booked", detail=str(got))
        s.check("and the saved season puts it on that way next year",
                conn.execute("SELECT display_mode FROM room_seasons WHERE id = ?", (made_season,)).fetchone()[0] == "season")
        page = m.app.test_client().get(f"/book?arrival={year}-12-08&departure={year}-12-11&party_size=2")
        text = _flat(visible_text(page.get_data(as_text=True)))
        s.check("a winter search says the suite rests, in its own words", note in text and "belle saison" in text,
                detail=text[:200])
        conn.execute("UPDATE room_blocks SET display_mode = 'booked' WHERE id = ?", (made_blocks[0],))
        conn.commit()
        s.check("and it runs once: a closure put back to booked by hand stays booked",
                m.show_winter_rest_as_season(conn) == 0)
    finally:
        if made_blocks:
            conn.execute(f"DELETE FROM room_blocks WHERE id IN ({','.join('?' * len(made_blocks))})", made_blocks)
        if made_season:
            conn.execute("DELETE FROM room_seasons WHERE id = ?", (made_season,))
        conn.commit()
        put_back()


def _see_through(s):
    s.section("A3 The logo keeps its transparency at every size")
    import io
    from PIL import Image
    name = "e" * 40 + ".png"
    path = os.path.join(m.MIRROR_DIR, name)
    # The wordmark's own kind of file: a palette PNG with one colour marked transparent, white letters on it.
    img = Image.new("P", (900, 240), 0)
    img.putpalette([0, 0, 0, 255, 255, 255] + [0] * (254 * 3))
    img.paste(1, (300, 80, 600, 160))
    img.info["transparency"] = 0
    os.makedirs(m.MIRROR_DIR, exist_ok=True)
    img.save(path, transparency=0)
    try:
        s.check("its sizes have an address of their own", m.pic_address(480, "~m/" + name).endswith("?a"),
                detail=m.pic_address(480, "~m/" + name))
        jpg = next((f for f in sorted(os.listdir(os.path.join(_harness.ROOT, "static", "img", "rk")))
                    if f.endswith(".jpg")), None)
        s.check("which a photograph does not need", jpg and not m.pic_address(480, "rk/" + jpg).endswith("?a"))
        r = m.app.test_client().get(f"/pic/480/~m/{name}?a")
        made = Image.open(io.BytesIO(r.get_data())) if r.status_code == 200 else None
        corner = made.convert("RGBA").getpixel((2, 2)) if made else None
        middle = made.convert("RGBA").getpixel((240, 64)) if made else None
        s.check("the size made keeps the see-through corner see-through", made is not None and made.mode == "RGBA"
                and corner[3] == 0, detail=f"HTTP {r.status_code}, {made.mode if made else '-'}, corner {corner}")
        s.check("and the letters white", middle is not None and middle[3] == 255 and min(middle[:3]) > 200,
                detail=str(middle))
        with m.app.test_request_context("/"):
            page = m.app.response_class(
                '<!doctype html><body class="g"><a class="g-logo"><img loading="lazy" src="/mirrored-photo/'
                + name + '" alt="Château de Gudanes"></a></body>', mimetype="text/html")
            done = m.pictures_at_their_size(page).get_data(as_text=True)
        s.check("and the masthead's srcset asks for those sizes", f"/pic/480/~m/{name}?a 480w" in done, detail=done[-300:])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


_EN_FIGURE = re.compile(r"€\s?\d|\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|"
                        r"November|December)\b|\b(?:January|February|March|April|May|June|July|August|September|"
                        r"October|November|December)\s+\d{4}")


def _figures(s):
    s.section("5 Dates and prices the way French and Spanish write them")
    f = m.localise_figures
    s.check("French dates", f("Friday 1 January 2027", "fr") == "vendredi 1er janvier 2027"
            and f("5 – 9 December 2026", "fr") == "5 – 9 décembre 2026", detail=f("5 – 9 December 2026", "fr"))
    s.check("Spanish dates", f("5 – 9 December 2026", "es") == "5 – 9 de diciembre de 2026"
            and f("December 2026", "es") == "diciembre de 2026")
    s.check("French prices", f("€3,800", "fr") == "3 800 €" and f("€0.94", "fr") == "0,94 €")
    s.check("Spanish prices", f("€3,800", "es") == "3.800 €")
    s.check("English is left as it is", f("€3,800 on 5 December 2026", "en") == "€3,800 on 5 December 2026")
    for path in ("/fr/workshops", "/es/workshops", "/fr/"):
        html = m.app.test_client().get(path).get_data(as_text=True)
        spans = [m.translation_key(n) for _a, _b, n in m.page_text_spans(html)]
        left = [x for x in spans if _EN_FIGURE.search(x)]
        s.check(f"{path}: no English date or price left", not left, detail=str(left[:3]))
        s.check(f"{path}: and the dates are there, in its words", any(
            ("décembre" if "/fr" in path else "diciembre") in x for x in spans))
    html = m.app.test_client().get("/workshops").get_data(as_text=True)
    s.check("/workshops in English keeps €3,800 and December",
            re.search(r"€\d,\d{3}", html) is not None and "December" in html)


def _small_things(s):
    s.section("A2 and A3: the press description, and the French and Spanish menu")
    html = m.app.test_client().get("/press").get_data(as_text=True)
    desc = re.search(r'<meta name="description" content="([^"]*)"', html)
    import html as htmllib
    said = htmllib.unescape(desc.group(1)) if desc else ""
    s.check("/press's description is within 160 characters", 0 < len(said) <= 160, detail=f"{len(said)}: {said}")
    # Measured in a browser on the live site, 26 Nov: at 1,498px the French links needed 69px more than a 1,216px
    # row left them, so the header folded on every desktop; at 1,320px they fit with 35px to spare. What this can
    # check without a browser is that the rule is there, for both languages, and not for English.
    css = open(os.path.join(_harness.ROOT, "static", "gudanes.css"), encoding="utf-8").read()
    rule = re.search(r'html\[lang="fr"\] \.g-top__in, html\[lang="es"\] \.g-top__in\{ max-width: max\(var\(--wrap\), (\d+)px\); \}',
                     css)
    s.check("the French and Spanish header row may run to 1,320px", rule is not None and int(rule.group(1)) >= 1320)
    s.check("and the English row keeps --wrap", 'html[lang="en"] .g-top__in' not in css)


def _site_errors(s, conn):
    s.section("B2.2 A page that fails is written down, and the owner hears of it")
    from _harness import clients
    oc, ec, _owner, _emp = clients()

    def boom(*args, **kwargs):
        raise RuntimeError("ZZSWEEP broke with key sk-ABCDEFGHIJKLMNOPQRST in hand")

    def ask(endpoint, path):
        was = m.app.view_functions[endpoint]
        m.app.view_functions[endpoint] = boom
        try:
            try:
                status = m.app.test_client().get(path).status_code
            except RuntimeError:
                status = "raised"
        finally:
            m.app.view_functions[endpoint] = was
        return status

    conn.execute("DELETE FROM site_errors")
    conn.commit()
    try:
        status = ask("press", "/press")
        rows = [dict(r) for r in conn.execute("SELECT * FROM site_errors")]
        s.check("the failure is written down", len(rows) == 1 and rows[0]["place"] == "GET /press"
                and rows[0]["error_type"] == "RuntimeError" and rows[0]["in_function"] == "boom",
                detail=f"{status}: {rows}"[:300])
        s.check("with any key in it taken out", rows and "sk-ABCDEFGHIJKLMNOPQRST" not in
                (rows[0]["message"] or "") + (rows[0]["traceback"] or ""), detail=rows[0]["message"] if rows else "")
        ask("press", "/press")
        rows = [dict(r) for r in conn.execute("SELECT * FROM site_errors")]
        s.check("the same fault again is counted, not written twice", len(rows) == 1 and rows[0]["times"] == 2,
                detail=str([(r["place"], r["times"]) for r in rows]))
        ask("manage_booking", "/book/manage/ZZSECRETTOKEN123")
        row = conn.execute("SELECT * FROM site_errors WHERE place LIKE '%manage%'").fetchone()
        s.check("a guest's page is named by its route, never the key in its address",
                row is not None and "<" in row["place"] and "ZZSECRETTOKEN123" not in " ".join(
                    str(v) for v in dict(row).values()), detail=row["place"] if row else "not written")
        try:
            raise ValueError("ZZSWEEP the loop fell over")
        except ValueError as e:
            m.note_site_error("loop", "the automation loop", e)
        s.check("and so is a background loop that fell over",
                conn.execute("SELECT 1 FROM site_errors WHERE kind = 'loop'").fetchone() is not None)

        with m.app.test_request_context("/"):
            warned = [w for w in m.owner_home_warnings(conn, m.house_today()) if "failed this week" in w["title"]]
        s.check("the owner home says so, linking to the page", warned and warned[0]["count"] == 3
                and warned[0]["href"].endswith("/admin/site-errors"), detail=str(warned)[:300])
        s.check("as a warning, while none of it is money's", warned and warned[0]["severity"] == "warn")
        with m.app.test_request_context("/"):
            found, _dropped = m.watch_task_findings(conn, m.house_today())
        titles = [t for kind, t, *_rest in found if kind == "site_error"]
        s.check("the one that keeps failing becomes a task; the ones seen once do not",
                titles == ["A page is failing: GET /press"], detail=str(titles))
        conn.execute("UPDATE site_errors SET place = 'POST /stripe/webhook' WHERE place LIKE '%manage%'")
        conn.commit()
        with m.app.test_request_context("/"):
            warned = [w for w in m.owner_home_warnings(conn, m.house_today()) if "failed this week" in w["title"]]
            found, _dropped = m.watch_task_findings(conn, m.house_today())
        s.check("a payment's failure is a blocker, and a task the first time",
                warned and warned[0]["severity"] == "blocker"
                and "A page is failing: POST /stripe/webhook" in [t for k, t, *_r in found if k == "site_error"])

        page = oc.get("/admin/site-errors")
        text = _flat(visible_text(page.get_data(as_text=True)))
        s.check("the owner's page lists them", page.status_code == 200 and "GET /press" in text
                and "RuntimeError in boom" in text, detail=text[:200])
        s.check("and nobody else's", ec.get("/admin/site-errors").status_code in (302, 403))
        s.check("the Automation page points to it", "/admin/site-errors" in oc.get("/admin/automation").get_data(as_text=True))

        conn.execute("UPDATE site_errors SET last_at = '2026-01-01T00:00:00+00:00'")
        conn.commit()
        with m.app.test_request_context("/"):
            quiet = [w for w in m.owner_home_warnings(conn, m.house_today()) if "failed this week" in w["title"]]
            found, _dropped = m.watch_task_findings(conn, m.house_today())
        s.check("a week after the fault stops, the home is quiet and the task gone",
                not quiet and not [t for k, t, *_r in found if k == "site_error"])
        s.check("and ninety days after, it is deleted, by the daily retention pass",
                m.purge_site_errors(conn)["site errors"] == 3
                and "purge_site_errors" in __import__("inspect").getsource(m.run_health_notes_purge_job))
        notice = _flat(visible_text(m.app.test_client().get("/privacy").get_data(as_text=True)))
        s.check("which is what the privacy notice says", "deleted ninety days after the fault last happened" in notice
                and m.SITE_ERROR_KEEP_DAYS == 90)
    finally:
        conn.execute("DELETE FROM site_errors")
        conn.commit()


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
        _naming_rule(s, anon, conn)
        _brocante_tour(s, anon, conn)
        _occasion_label(s, conn)
        _footer(s)
        _winter_rest(s, conn)
        _see_through(s)
        _figures(s)
        _small_things(s)
        _site_errors(s, conn)
    finally:
        _setting(conn, "dining_mode", was_dm)
        conn.close()
    return s


if __name__ == "__main__":
    raise SystemExit(0 if run().report() else 1)
