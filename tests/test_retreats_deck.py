"""The retreats copy deck (owner, 26 October 2026), held on the pages it was written for.

The deck replaced every earlier word about the retreats on the home page,
/workshops, each retreat's own page and the cards, and set rules for all of
them. Those pages arrive from the design side as whole files, so these are the
rules a handover or a hurried edit breaks without anything erroring:

  THE WORDS. Each retreat's card line, heading line and opening paragraph are
  the owner's, and so are the home page's retreats band and /workshops'
  sections. "All meals and drinks", "activities", "unique", "once in a
  lifetime" and exclamation marks appear nowhere on those pages, "luxury" only
  in the owner's own line on Seven Starry Nights, and nobody on the team is
  named.

  THE PRICES. None is typed into the words: the owner lowered every price on
  26 October. Each card and each date shows the Admin's figure, then "per
  person · nothing to pay at the end".

  THE DAYS. A retreat's first day opens with local sparkling wine and its last
  night is a banquet in the Salon de Musique, dressed for, then dancing;
  "fresh flowers in every room" is said once on its page; never why the walls
  are left as they are; and the three nights do not go to Mirepoix.

  THE PHOTOGRAPHS. The old Squarespace site expires on 14 November 2026 and
  its pictures with it. Every one the deck chose is in static/img/retreats,
  the pages point at the house's copy -- including the older galleries that
  still name the Squarespace address -- no page shows one twice, and the
  Commons ones carry a credit that is not inside a caption, because the owner
  has every photo caption hidden.

  THE OLD PAGES. /5-nights, /christmas and the rest of the deck's section 7
  land on a page that answers.
"""
import io
import os
import re

from _harness import Suite, db, visible_text
import _harness

m = _harness.m

# kind: (the card line, the heading line, the opening paragraph), each as it opens
DECK = {
    # 27 Oct: the Noël card and heading rewritten (no embroidery, no candles); the Immersive Artisan
    # Workshops off the site, so no words of their own -- see "Off the site" below.
    "noel": ("Four December days at home in the château: a gingerbread château to ice",
             "A French Christmas in a château, by the fire",
             "Four days in December as the château fills for Noël"),
    "cooking": ("Five summer days with the château’s chefs and pastry chef",
                "French cooking, in the kitchens where it belongs",
                "Five summer days in the kitchens of a château built in 1741"),
    "finds": ("Three days of chiner in the markets, brocantes and vide-greniers of the Ariège",
              "Chiner: the French art of the find",
              "Three days devoted to chiner, the French art of hunting"),
    "starry": ("A week of French summer with everything",
               "A French summer, from first light to the last dance",
               "A week of long, golden days in the Pyrénées"),
}
TITLES = {"noel": "Noël at Gudanes",
          "cooking": "Cooking in the Cuisine", "finds": "Antique & French Finds",
          "starry": "Seven Starry Nights"}
NIGHTS = {"noel": "4 nights / 5 days", "cooking": "5 nights / 6 days",
          "finds": "3 nights / 4 days", "starry": "7 nights / 8 days"}
NEVER = ["all meals and drinks", "activities", "unique", "once in a lifetime",
         "welcome drinks", "why the walls are left as they are",
         "A Retreat With a Purpose", "Experience the Château in Full", "Nicolas Jover"]
LUXURY_ONLY = "the rare luxury of having nowhere else to be"
RETREATS = os.path.join(_harness.ROOT, "static", "img", "retreats")


def words(html):
    """What a reader sees, with the space a tag leaves before punctuation taken out."""
    return re.sub(r"\s+([,.:;])", r"\1", visible_text(html))


def macro(name, w):
    with m.app.test_request_context("/"):
        return m.app.jinja_env.from_string(
            "{% from '_ws_copy.html' import " + name + " %}{{ " + name + "(w) }}").render(w=w)


def kind_of(title):
    with m.app.test_request_context("/"):
        return m.app.jinja_env.from_string(
            "{% from '_ws_copy.html' import ws_kind %}{{ ws_kind(w)|trim }}").render(w={"title": title})


def journey_days(html):
    """[visible text of each day card], in order."""
    sec = html[html.find('class="g-sec g-sec--sand g-journey-sec"'):]
    sec = sec[:sec.find("</section>")]
    return [words(d) for d in re.findall(r'<li class="g-journey__d">(.*?)</li>\s*(?=<li class="g-journey__d">|</ol>)',
                                         sec, re.S)], words(sec)


def img_srcs(html):
    return re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', html)


def run():
    s = Suite("The retreats deck, 26 October")
    c = m.app.test_client()

    s.section("Each retreat's own words, as the owner wrote them")
    for kind, (card, heading, para) in DECK.items():
        w = {"title": TITLES[kind], "nights_label": NIGHTS[kind]}
        said = words(macro("ws_lead", w))
        s.check(f"{TITLES[kind]}: the card line", said.startswith(card), detail=said[:120])
        said = words(macro("ws_heading", w))
        s.check(f"{TITLES[kind]}: the heading line", said == heading, detail=said)
        said = words(macro("ws_copy", w))
        s.check(f"{TITLES[kind]}: the opening paragraph", said.startswith(para), detail=said[:120])
        for bad in NEVER:
            if bad.lower() in said.lower():
                s.check(f"{TITLES[kind]}: never '{bad}'", False, detail=said[:160])
        s.check(f"{TITLES[kind]}: no price in the words", not re.search(r"€\s*\d", said), detail=said[:160])

    conn = db()
    today = m.house_today()
    offer = [(w, sittings) for w, sittings in m.workshops_on_offer(conn, today)
             if kind_of(w["title"]) in DECK]
    team = [r["instructor_name"] for r in conn.execute(
        "SELECT instructor_name FROM workshops WHERE COALESCE(instructor_name, '') != ''")]
    upcoming = conn.execute(
        f"""SELECT workshop_sessions.*, workshops.title, workshops.price_per_person, workshops.nights_label
              FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
             WHERE workshops.active = 1 AND workshop_sessions.start_date >= ? AND {m.SESSION_IS_LIVE}
             ORDER BY workshop_sessions.start_date LIMIT 3""", (m.house_today_iso(),)).fetchall()
    conn.close()
    s.check("there are retreats on offer to look at", bool(offer),
            detail="nothing on offer, so the page checks below would prove nothing")

    pages = {"/": c.get("/"), "/workshops": c.get("/workshops")}
    for w, _sittings in offer:
        pages["/workshops/%d" % w["id"]] = c.get("/workshops/%d" % w["id"])

    s.section("The pages answer, and say none of what the deck ruled out")
    for path, r in pages.items():
        html = r.get_data(as_text=True)
        seen = words(html)
        s.check(f"{path} answers", r.status_code == 200, detail=str(r.status_code))
        found = [bad for bad in NEVER if bad.lower() in seen.lower()]
        s.check(f"{path}: none of the ruled-out words", not found, detail=", ".join(found))
        s.check(f"{path}: no exclamation marks", "!" not in seen,
                detail=seen[max(0, seen.find("!") - 60):seen.find("!") + 5])
        lux = seen.lower().count("luxury")
        s.check(f"{path}: 'luxury' only in the owner's own line", lux == seen.lower().count(LUXURY_ONLY),
                detail=f"{lux} times")
        named = [n for n in team if n in seen]
        s.check(f"{path}: nobody on the team named", not named, detail=", ".join(named))

    s.section("The home page")
    home = words(pages["/"].get_data(as_text=True))
    for line in ["Or stay longer, as a guest of the house: three to seven nights of la vie de château.",
                 "For two centuries Gudanes was a house of guests.",
                 "Guests arrive as strangers and leave as friends, and so many of them return.",
                 "Many of our guests return, some year after year, each time for a different retreat.",
                 "A Retreat La vie de château",
                 "Three to seven nights. Nothing to pay at the end.",
                 "On a retreat the kitchen never rests",
                 "A restoration, not a renovation. New floors, new bathrooms and good French beds"]:
        s.check(f"says '{line[:60]}'", line in home)
    for fr in ["À table", "En cuisine", "Chiner", "Le marché", "La restauration", "La fête",
               "Le farniente", "La vallée"]:
        s.check(f"the moment '{fr}'", fr in home)
    s.check("and 'La Vie de Château' heads the retreats, once", home.count("La Vie de Château") == 1,
            detail=f"{home.count('La Vie de Château')} times")
    for row in upcoming:
        figure = "€{:,.0f} per person · nothing to pay at the end".format(row["price_per_person"])
        s.check(f"{row['title']}: the next date carries today's price", figure in home, detail=figure)
        hook = words(macro("ws_hook", {"title": row["title"]}))
        if hook:
            s.check(f"{row['title']}: and its line", hook in home, detail=hook)

    s.section("/workshops")
    ws = words(pages["/workshops"].get_data(as_text=True))
    for line in ["Les retraites", "La Vie de Château",
                 "A few days of French country-house life, in a château brought back from ruin.",
                 "This is not a hotel stay, and it is not a course.",
                 "Une journée à Gudanes", "Le petit-déjeuner", "Le dîner",
                 "In France, the table is the point of the day",
                 "The French have a word for it: chiner",
                 "Bring something beautiful to wear.",
                 "Very few people anywhere are ever invited to do this.",
                 "the great cave at Lombrives",
                 "by the second dinner it feels like a house party among friends",
                 "One price for all of it",
                 "Nothing is required of you. Do everything, or nothing at all."]:
        s.check(f"says '{line[:60]}'", line in ws)
    s.check("what is included is said once", ws.count("There is nothing to pay at the end.") == 1,
            detail=f"{ws.count('There is nothing to pay at the end.')} times")
    for w, _sittings in offer:
        card = words(macro("ws_lead", w))
        s.check(f"{w['title']}: its card line is on the page", card in ws, detail=card[:80])
        if w["price_per_person"]:
            figure = "€{:,.0f} per person · nothing to pay at the end".format(w["price_per_person"])
            s.check(f"{w['title']}: today's price, and nothing to pay at the end", figure in ws, detail=figure)

    s.section("Each retreat's page")
    for w, _sittings in offer:
        path = "/workshops/%d" % w["id"]
        html = pages[path].get_data(as_text=True)
        seen = words(html)
        kind = kind_of(w["title"])
        card, heading, para = DECK[kind]
        s.check(f"{w['title']}: the heading line", heading in seen)
        s.check(f"{w['title']}: the opening paragraph", para in seen)
        if w["price_per_person"]:
            s.check(f"{w['title']}: today's price",
                    "€{:,.0f} per person".format(w["price_per_person"]) in seen)
        s.check(f"{w['title']}: 'fresh flowers in every room', once",
                seen.lower().count("fresh flowers in every room") == 1,
                detail=f"{seen.lower().count('fresh flowers in every room')} times")
        days, journey = journey_days(html)
        s.check(f"{w['title']}: a day-by-day plan", len(days) >= 4, detail=f"{len(days)} days")
        if len(days) >= 4:
            s.check(f"{w['title']}: day one opens with local sparkling wine",
                    "local sparkling wine" in days[0], detail=days[0][:120])
            s.check(f"{w['title']}: the last night is the banquet in the Salon de Musique, then dancing",
                    "Salon de Musique" in days[-2] and "dressed for the occasion" in days[-2]
                    and "dancing" in days[-2], detail=days[-2][:200])
            s.check(f"{w['title']}: every morning, the village boulangerie", "boulangerie" in journey)
            s.check(f"{w['title']}: l'apéritif with its drinks named",
                    "Kir Royale" in journey and "Lillet tonic" in journey)
            s.check(f"{w['title']}: the crêpe cart, on one morning at least", "crêpe cart" in journey)
            s.check(f"{w['title']}: the restoration without a century or the walls",
                    "eighteenth-century frescoes" not in journey and "walls are left" not in journey)
        if kind == "finds":
            s.check("the three nights do not go to Mirepoix", "Mirepoix" not in journey)
        if kind in ("cooking", "starry"):
            s.check(f"{w['title']}: a visiting artisan, kept", "visiting artisan" in seen)
        if kind == "noel":
            # 27 Oct (owner): no embroidery or candle-pouring, no outings; day 3 is the gingerbread château
            made = re.findall(r"embroider\w*|candles? (?:poured|made)|pour(?:ing)? candles|candle-pouring"
                              r"|a last morning of making", seen, re.I)
            s.check("Noël: no embroidery or candle-pouring on the page", not made, detail=", ".join(made))
            s.check("Noël: and none on its card", not re.search(r"embroider|candle", words(macro("ws_lead", w)), re.I))
            if len(days) == 5:
                s.check("Noël: day three is the gingerbread château",
                        "Le pain d’épices" in days[2] and "gingerbread château" in days[2]
                        and "champagne jellies" in days[2] and "French wines by the fire" in days[2],
                        detail=days[2][:160])
                s.check("Noël: the last day opens slowly, with watercolours and the tree",
                        "watercolours at the long table" in days[3] and "dress the tree" in days[3]
                        and "Cocktail hour in the Château Bar" in days[3], detail=days[3][:160])
            s.check("Noël: no outings", not re.search(r"\bFoix\b|Lombrives|Ax-les-Thermes|Mirepoix|transfer from Toulouse",
                                                       journey), detail=journey[:120])
            s.check("Noël: the tasting by the fire and cocktail hour are on the programme",
                    "La dégustation French wines by the fire" in seen
                    and "L’heure du cocktail Cocktail hour in the Château Bar" in seen)

    s.section("No price typed into the words")
    comment = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)
    typed = []
    for name in ("_ws_copy.html", "workshops_public.html", "home.html", "workshop_detail.html"):
        text = comment.sub("", io.open(os.path.join(_harness.ROOT, "templates", name), encoding="utf-8").read())
        for hit in re.finditer(r"(?:€|&euro;)\s*\d", text):
            typed.append(f"{name}: {text[max(0, hit.start() - 30):hit.end() + 10]!r}")
    s.check("every figure comes from the Admin", not typed, detail="; ".join(typed[:3]))

    s.section("The photographs are the house's own")
    sources = io.open(os.path.join(RETREATS, "SOURCES.txt"), encoding="utf-8").read().splitlines()
    listed = [line.split()[0] for line in sources if line.split() and line.split()[0].endswith(".jpg")]
    s.check("SOURCES.txt lists the deck's photographs", len(listed) >= 100, detail=f"{len(listed)} listed")
    missing = [n for n in listed if not os.path.exists(os.path.join(RETREATS, n))
               or not os.path.exists(os.path.join(RETREATS, n[:-4] + "-800.jpg"))]
    s.check("and every one is in the folder, at both sizes", not missing, detail=", ".join(missing))
    s.check("each Squarespace address listed is swapped for the house's copy",
            all(any(k.startswith(u) for k in m.HOUSE_COPIES)
                for u in (line.split()[1] for line in sources
                          if len(line.split()) > 1 and "squarespace-cdn" in line.split()[1])))
    deck_bases = [line.split()[1] for line in sources
                  if len(line.split()) > 1 and "squarespace-cdn" in line.split()[1]]
    for path, r in pages.items():
        html = r.get_data(as_text=True)
        srcs = img_srcs(html)
        broken = [u for u in srcs if "/static/img/retreats/" in u
                  and not os.path.exists(os.path.join(RETREATS, u.split("/static/img/retreats/")[1].split("?")[0]))]
        s.check(f"{path}: every house copy it shows is there", not broken, detail=", ".join(broken[:3]))
        hot = [u for u in srcs if any(u.split("?")[0] == b or u.split("?")[0] == b.replace("/content/v1/", "/content/")
                                      for b in deck_bases)]
        s.check(f"{path}: none of the deck's photographs from Squarespace", not hot, detail=", ".join(hot[:2]))
        ours = [re.sub(r"-(800|2400)\.jpg$", ".jpg", u.split("?")[0]) for u in srcs if "/static/img/retreats/" in u]
        twice = sorted({u for u in ours if ours.count(u) > 1})
        s.check(f"{path}: none of them twice", not twice, detail=", ".join(twice))
    ws_html = pages["/workshops"].get_data(as_text=True)
    # Foix and Mirepoix are the chateau's own photographs since the deck's second section 6;
    # Lombrives and Ax are still from Commons.
    for credit in ("Traumrune", "Jeunamateur"):
        at = ws_html.find(credit)
        inside = at != -1 and ws_html.rfind("<figcaption", 0, at) > ws_html.rfind("</figcaption>", 0, at)
        s.check(f"/workshops credits {credit}, outside the hidden captions", at != -1 and not inside)
    home_html = pages["/"].get_data(as_text=True)
    s.check("the home page and /workshops open on the guests toasting",
            "img/retreats/guests-toasting" in home_html and "img/retreats/guests-toasting" in ws_html)
    s.check("and no Commons credit is left for a photograph no longer shown",
            "Patrick Castay" not in home_html + ws_html and "Babsy" not in ws_html)

    # 27 Oct (owner): three photographs out of the deck everywhere, new leads, and the dogs named as they are.
    s.section("The deck's 27 October photographs")
    dropped = {"cooking-class-kitchen": "1582798909935-HTB0747EBXEO4B6RPRYC",
               "brocante-gilt-frame": "1481179212968-BJ7WLHYBYGMTUL6RSR4A",
               "caramel-choux-recipe": "1587300154571-GB0GAI4BGXSJ8OTAPBT7"}
    shown = {p: r.get_data(as_text=True) for p, r in pages.items()}
    shown["/gallery"] = c.get("/gallery").get_data(as_text=True)
    for slug, asset in dropped.items():
        on = [p for p, html in shown.items() if slug in html or asset in html]
        s.check(f"{slug} is on no page", not on, detail=", ".join(on))
        s.check(f"{slug} is out of the folder and the list",
                not os.path.exists(os.path.join(RETREATS, slug + ".jpg"))
                and not any(line.startswith(slug + ".jpg") for line in sources))
    moments = home_html[home_html.find('class="g-moments"'):]
    moments = moments[:moments.find("</ul>")]
    for slug, fr in (("red-beret-tart", "En cuisine"), ("vide-grenier", "Chiner")):
        s.check(f"the home page: {fr} is the {slug}",
                re.search(r"%s-800\.jpg[^>]*>\s*<p[^>]*>%s<" % (slug, fr), moments) is not None)
    chiner = img_srcs(ws_html[ws_html.find("============ CHINER"):])
    s.check("/workshops: the vide-grenier leads Chiner",
            bool(chiner) and chiner[0].split("?")[0].endswith("img/retreats/vide-grenier.jpg"),
            detail=chiner[0] if chiner else "no photographs")
    for w, _sittings in offer:
        html = shown["/workshops/%d" % w["id"]]
        hero = re.search(r'class="g-hero__img" src="([^"]+)"', html)
        hero = hero.group(1) if hero else ""
        if kind_of(w["title"]) == "finds":
            s.check("Antique & French Finds opens on the brocante stalls",
                    hero.endswith("img/retreats/brocante-stalls.jpg"), detail=hero)
        if kind_of(w["title"]) == "cooking":
            s.check("Cooking in the Cuisine opens on the cooking class from above",
                    hero.endswith("img/retreats/cooking-class-hands.jpg"), detail=hero)
        s.check(f"{w['title']}: its lead picture is not in its gallery too",
                not hero or html.count(hero.split("/static/img/retreats/")[-1]) == 1)
    gallery_src = io.open(os.path.join(_harness.ROOT, "templates", "gallery.html"), encoding="utf-8").read()
    s.check("the dog in the snow is captioned Alfred",
            re.search(r"42ac040d-dc88-4253-8d3d-7084f6e05ea4[^}]*'caption': 'Alfred'", gallery_src) is not None)
    s.check("and the deck's other dogs are 'the dog', not Bruce",
            not any(x in html for html in shown.values()
                    for x in ("Bruce leading", "street with Bruce", "Bruce by the tree")))

    s.section("/workshops: the afternoon, and a visiting artisan")
    ws_words = words(ws_html)
    s.check("the afternoon has an easel and a box of watercolours",
            "the library, or an easel and a box of watercolours. Or a brocante" in ws_words)
    s.check("and the morning keeps a visiting artisan", "with a visiting artisan, or on the restorers’ scaffold" in ws_words)
    # 27 Oct (owner): the sorbet stays the afternoon picture; the painting goes to the restoration page instead.
    s.check("the afternoon picture is still the sorbet",
            re.search(r'<li class="g-day__i">\s*<figure><img src="/static/img/retreats/sorbet-pouring\.jpg"[^>]*></figure>'
                      r'\s*<div class="g-day__b">\s*<h3 class="g-day__t"><em lang="fr">L’après-midi</em>', ws_html) is not None
            and "guest-painting" not in ws_html)
    resto = c.get("/restoration").get_data(as_text=True)
    s.check("the painting is on the restoration page, captioned for what it shows",
            "img/retreats/guest-painting.jpg" in resto
            and "<figcaption>Painting a beam by hand, before the old painted walls</figcaption>" in resto
            and "guest" not in re.search(r'alt="([^"]*)"[^>]*>\s*<figcaption>Painting a beam', resto).group(1).lower()
            if "guest-painting" in resto else False)
    # The Autumn Atelier is over (owner, 27 Oct): "this autumn" goes stale in December, the year does not.
    for path in ("/", "/workshops"):
        page = words(shown[path]) if path in shown else words(c.get(path).get_data(as_text=True))
        s.check(f"{path}: the Autumn Atelier line carries its year",
                "In autumn 2026, every guest at the Autumn Atelier" in page and "This autumn, every guest" not in page)

    # 27 Oct (owner): "no card, no page, no date". Switched off once at start-up, not deleted:
    # its bookings and its money stay on the books.
    s.section("Off the site: the Immersive Artisan Workshops")
    conn = db()
    gone = conn.execute("SELECT id, active FROM workshops WHERE title = ?", (m.RETIRED_WORKSHOP_TITLE,)).fetchall()
    flag = conn.execute("SELECT 1 FROM app_settings WHERE key = 'artisan_workshops_retired'").fetchone()
    conn.close()
    s.check("switched off, and the switch recorded so a deploy never repeats it",
            bool(gone) and all(not r["active"] for r in gone) and flag is not None,
            detail=", ".join("#%d active=%s" % (r["id"], r["active"]) for r in gone))
    for r in gone:
        page = c.get("/workshops/%d" % r["id"])
        s.check(f"its page (/workshops/{r['id']}) is gone, not a redirect naming it", page.status_code == 404,
                detail=str(page.status_code))
    for path, html in shown.items():
        s.check(f"{path}: no card, no date, no name", not re.search(r"Immersive|Artisan Workshops", html),
                detail=path)
    s.check("and it has no words of its own left to show", not kind_of(m.RETIRED_WORKSHOP_TITLE))

    s.section("The old pages land somewhere that answers")
    for path in ("/5-nights", "/christmas", "/4-nights", "/stay-2026", "/stay-2024", "/workshops-2026"):
        r = c.get(path)
        where = r.headers.get("Location") or ""
        lands = c.get(where.replace("http://localhost", "") or "/nowhere").status_code if where else None
        s.check(f"{path} redirects to a page that answers", r.status_code in (301, 302) and lands == 200,
                detail=f"{r.status_code} -> {where} -> {lands}")
    cooking = [w for w, _sittings in offer if kind_of(w["title"]) == "cooking"]
    if cooking:
        where = c.get("/5-nights").headers.get("Location") or ""
        s.check("and /5-nights is the cooking retreat", where.endswith("/workshops/%d" % cooking[0]["id"]),
                detail=where)

    s.section("The Book menu and the stylesheet")
    s.check("the Book menu: three to seven nights of la vie de château",
            "Three to seven nights of la vie de château" in home)
    css = io.open(os.path.join(_harness.ROOT, "static", "gudanes.css"), encoding="utf-8").read()
    s.check("the deck's rules are in the stylesheet", ".g .g-moments{" in css and ".g .g-everyday{" in css)
    repair = io.open(os.path.join(_harness.ROOT, "tools", "repair_handover.py"), encoding="utf-8").read()
    s.check("and a handover that drops them gets them back", "repair_retreats_css" in repair)
    return s


if __name__ == "__main__":
    print(run().report())
