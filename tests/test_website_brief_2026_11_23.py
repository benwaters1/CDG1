"""The owner's website brief of 23 November 2026: four changes to the public site.

1. /press rebuilt. The seven unlinked titles of "Where It Has Appeared" give way
   to the introduction, the names as type (never logos), the six pages in one
   captioned row, the stories with a "Read in" button only where there is
   somewhere to read them, the line of other titles, the Medal of Honour in its
   new words, the book, and For Editors as it was.
2. The old Squarespace press archive, /press-2 and everything under it, lands
   on /press with a 301. So does /press/ itself, and any of this site's own
   pages asked for with a slash on the end -- the address exactly, the slash
   and nothing else taken off.
3. The rooms are named after French women. The website shows the French title
   as the heading and the plain name small beneath it; letters use the title;
   and nothing that talks to Booking.com reads it, because Booking.com's side
   is keyed to the plain names.
4. /links, for the Instagram bio: three buttons whose prices are the site's own,
   linked from nowhere, out of the sitemap, and noindex even when the site is.

What breaks quietly here, and is therefore checked: a "Read in" button on a
story with no address (a button to nowhere); a caption the site-wide rule hides
again; a title that leaks into the Booking.com feed; a price typed into /links
that stays at €270 the day the owner changes it; and /links indexable once
SITE_IS_LIVE is on, which is the only state in which it matters.
"""
import io
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db, keep_rows, visible_text
import _harness

m = _harness.m
ROOT = _harness.ROOT
TAG = "ZZ website brief"

STORY_LINKS = [
    "https://www.vogue.com/slideshow/past-present-chateau-de-gudanes",
    "https://www.vogue.com/slideshow/doen-chateau-de-gudanes-to-paris-travel-diary",
    "https://www.kinfolk.com/stories/home-tour-chateau-de-gudanes/",
    "https://www.homebeautiful.com.au/renovation-ideas/chateau-de-gudanes-karina-craig-waters/",
    "https://www.connexionfrance.com/news/photos-we-wanted-a-small-bolthole-but-bought-a-ruined-french-chateau/244599",
]
LEAD = ["Vogue", "Harper’s Bazaar", "Architectural Digest", "The Financial Times",
        "The New York Times"]
SECOND = ["ELLE Décor", "The World of Interiors", "House & Garden", "Veranda", "Kinfolk",
          "Tatler", "GQ", "The Robb Report", "Condé Nast Traveller",
          "National Geographic Traveler", "Australian Financial Review",
          "The Sydney Morning Herald", "Lonely Planet", "Gourmet Traveller", "Home Beautiful"]
MEDAL = ("In 2016 the Fondation Prince Louis de Polignac awarded Château de Gudanes its "
         "Medal of Honour, in recognition of the restoration. It was presented by Prince "
         "Albert II of Monaco at the Institut de France in Paris.")


def _between(html, start, end):
    a = html.find(start)
    b = html.find(end, a + 1) if a > -1 else -1
    return html[a:b] if a > -1 and b > -1 else ""


def _items(ul_html):
    return [visible_text(x) for x in re.findall(r"<li>(.*?)</li>", ul_html, re.S)]


def _restore_room(conn, row):
    """Every column of the room back as it was, by UPDATE. Never a delete and a
    re-insert: rooms cascade to their bookings, photographs and rates."""
    cols = [c for c in row if c != "id"]
    conn.execute(f"UPDATE rooms SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                 tuple(row[c] for c in cols) + (row["id"],))
    conn.commit()


def _press(s, anon):
    s.section("1. /press, rebuilt in the brief's order")
    r = anon.get("/press")
    html = r.get_data(as_text=True)
    words = visible_text(html)
    s.check("it answers", r.status_code == 200, detail=str(r.status_code))
    s.check("its heading is the brief's title",
            re.search(r"<h1[^>]*>\s*Written About, the World Over\s*</h1>", html) is not None)
    hero = re.search(r'<img[^>]*class="g-hero__img"[^>]*>', html)
    s.check("its hero is the front elevation, unless the owner fills the page's own slot",
            hero is not None and ("chateau-at-night.jpg" in hero.group(0)
                                  or "/room-photos/" in hero.group(0)),
            detail=hero.group(0)[:160] if hero else "no hero image")
    order = ["Since the first photographs of a roofless château", "As featured in",
             "The Pages Themselves", "Selected Stories", "Also in", "The Medal of Honour",
             "The Book", "For Editors"]
    at = [words.find(w) for w in order]
    s.check("the sections come in the brief's order", all(x > -1 for x in at) and at == sorted(at),
            detail=str(dict(zip(order, at))))
    s.check("the old unlinked list is gone",
            "Where It Has Appeared" not in words and "Zara Home" not in words
            and 'class="g-press"' not in html)
    s.check("the introduction names the architect of the Petit Trianon",
            "an eighteenth-century château, designed by Ange-Jacques Gabriel, architect of the "
            "Petit Trianon, being given back its life one room at a time." in words)

    s.section("As featured in: the names as type, in two rows")
    lead = _between(html, '<ul class="g-featured g-featured--lead"', "</ul>")
    second = _between(html, '<ul class="g-featured" ', "</ul>")
    s.check("the first, larger row is the five in the brief's order", _items(lead) == LEAD,
            detail=str(_items(lead)))
    s.check("the second row is the fifteen in the brief's order", _items(second) == SECOND,
            detail=str(_items(second)))
    s.check("as words, not logos", "<img" not in lead + second and "<svg" not in lead + second)
    s.check("and the translator leaves the names alone",
            'translate="no"' in lead[:120] and 'translate="no"' in second[:120])
    css = io.open(os.path.join(ROOT, "static", "gudanes.css"), encoding="utf-8").read()
    s.check("a middot between them, carried by each name so a line never opens on one",
            re.search(r"\.g-featured li:not\(:last-child\)::after\{\s*content:\s*\"\\00B7\"", css)
            is not None)

    s.section("In print: the six pages in one row, each captioned, each opening larger")
    row = _between(html, '<div class="g-print', "</section>")
    figures = re.findall(r'<figure class="g-print__p">(.*?)</figure>', row, re.S)
    caps = [visible_text(c) for c in re.findall(r"<figcaption[^>]*>(.*?)</figcaption>", row, re.S)]
    s.check("six pages", len(figures) == 6, detail=str(len(figures)))
    s.check("each with a caption naming its publication",
            len(caps) == 6 and caps[0].startswith("Harper’s Bazaar")
            and sum(c.startswith("Harper’s Bazaar") for c in caps) == 3
            and any(c.startswith("Vogue Netherlands") for c in caps)
            and any(c.startswith("The Australian Financial Review") for c in caps)
            and caps[-1].startswith("Moments Magazine"), detail=str(caps))
    for line in ("A feature across several pages, photographed at the château.",
                 "The November issue.", "The château and the work behind it."):
        s.check(f"the caption line {line!r}", any(line in c for c in caps))
    s.check("the row is one lightbox set: a tap on any page opens them all",
            'class="g-print g-lb-scope" data-lb-group' in html and "<dialog" in html)
    s.check("and the site-wide rule hiding captions lets these through",
            re.search(r"\.g main \.g-print figcaption\.g-print__cap\{\s*display:\s*block !important",
                      css) is not None,
            detail="`.g main figure figcaption{display:none !important}` hides every other one")

    s.section("Selected stories: a button only where there is something to read")
    cards = re.findall(r'<li class="g-pstory">(.*?)</li>', html, re.S)
    s.check("seven stories", len(cards) == 7, detail=str(len(cards)))
    hrefs = re.findall(r'<a class="g-btn g-btn--line" href="([^"]+)" target="_blank" rel="noopener">Read in',
                       html)
    s.check("five 'Read in' buttons, to the five addresses that answered", hrefs == STORY_LINKS,
            detail=str(hrefs))
    s.check("each opens in a new tab and says so to a screen reader",
            html.count("(opens in a new tab)") == 5)
    # Site review 25 Nov 2026 (2.10): the two stories with no address stopped being stories that could not be
    # read; they are named under "Also in" until somebody has their links.
    for title in ("Why This French Château Is Worthy of Its Own Instagram Account",
                  "The family and the restoration"):
        card = next((c for c in cards if title in c), "")
        s.check(f"'{title}' is no longer a story with nothing to read", not card, detail=card[:200])
    s.check("publication and year over each title",
            "Vogue · 2020" in words and "Kinfolk · Issue 30" in words)

    s.section("Also in, the Medal, the book and For Editors")
    s.check("the one line of other titles",
            "Architectural Digest · House & Garden · Lonely Planet · Gourmet Traveller · Condé Nast "
            "Traveller · My Modern Met · Bob Vila · Suitcase · The Good Life France · Daily Mail" in words)
    s.check("and the Shorty Award",
            "In 2018 the château was also nominated for a Shorty Award for the community that "
            "follows it online." in words)
    s.check("the Medal of Honour in the brief's words", MEDAL in words)
    # Site review 25 Nov 2026 (1.20, owner's yes): "Every copy sold goes towards the restoration" is funding
    # wording, which the owner ruled out, so the book's line stops at what it tells.
    s.check("the book, without the funding line",
            "Château de Gudanes: A True Love Story Never Ends (2018) tells the story of the "
            "château, its past, present and future." in words
            and "Every copy sold" not in words)
    s.check("For Editors is as it was",
            'href="mailto:ariege@chateaugudanes.com"' in html
            and "usage is agreed in writing rather than assumed" in words)
    s.check("no exclamation marks (the brief's tone)", "!" not in words)


def _redirects(s, anon):
    s.section("2. The old press archive lands on /press, permanently")
    for path in ("/press-2", "/press-2/moments-magazine",
                 "/press-2/2017/1/18/sydney-morning-herald", "/press-2/2020/2/18/kinfolk"):
        r = anon.get(path)
        s.check(f"{path} 301s to /press",
                r.status_code == 301 and r.headers.get("Location", "").endswith("/press"),
                detail=f"{r.status_code} {r.headers.get('Location')}")
    r = anon.get("/press-2/the-australian-financial-review?utm_source=afr")
    s.check("and keeps the campaign a magazine's link carried",
            r.status_code == 301 and r.headers.get("Location", "").endswith("/press?utm_source=afr"),
            detail=str(r.headers.get("Location")))
    r = anon.get("/press-2/", follow_redirects=True)
    s.check("/press-2/ ends on the press page", r.status_code == 200
            and "Written About, the World Over" in r.get_data(as_text=True))

    s.section("And this site's own pages with a slash on the end")
    for path, to in (("/press/", "/press"), ("/contact/", "/contact"), ("/history/", "/history")):
        r = anon.get(path)
        s.check(f"{path} 301s to {to}",
                r.status_code == 301 and r.headers.get("Location", "").endswith(to),
                detail=f"{r.status_code} {r.headers.get('Location')}")
    r = anon.get("/no-such-page-anywhere/")
    s.check("an address that is no page is still not found", r.status_code == 404,
            detail=str(r.status_code))
    r = anon.get("/Press/")
    s.check("nor is the slash rule a guess at case: /Press/ is not this site's address",
            r.status_code == 404, detail=f"{r.status_code} {r.headers.get('Location')}")

    s.section("Press stays in the menu and the footer")
    home = anon.get("/").get_data(as_text=True)
    drawer = _between(home, 'id="g-mainnav"', "Plan Your Visit")
    footer = _between(home, '<footer class="g-footer"', "</footer>")
    s.check("in the slide-out menu, under The Château",
            'href="/press"' in drawer and "The Château" in drawer)
    s.check("and in the footer's links", 'href="/press"' in footer)


def _rooms(s, oc, anon):
    s.section("3. The rooms' French titles: seeded once, by name")
    conn = db()
    rooms = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM rooms")}
    for name, title in m.ROOM_TITLES_2026_11_23:
        if name in rooms:
            s.check(f"{name} is {title} on the website", rooms[name]["website_title"] == title,
                    detail=repr(rooms[name]["website_title"]))
    s.check("the names themselves are untouched -- Booking.com's side is keyed to them",
            {"The Twin Room", "The King Room", "The King Suite", "The Marquis Suite",
             "The Family Suite"} <= set(rooms), detail=str(sorted(rooms)))
    s.check("it records that it has run",
            conn.execute("SELECT 1 FROM app_settings WHERE key = 'room_titles_2026_11_23'")
            .fetchone() is not None)
    twin = rooms.get("The Twin Room")
    put_flag = keep_rows(conn, "app_settings", "key = ?", ("room_titles_2026_11_23",))
    try:
        conn.execute("UPDATE rooms SET website_title = 'La Chambre du Test' WHERE id = ?",
                     (twin["id"],))
        conn.execute("DELETE FROM app_settings WHERE key = 'room_titles_2026_11_23'")
        conn.commit()
        changed = m.apply_room_titles_2026_11_23(conn)
        now = conn.execute("SELECT website_title FROM rooms WHERE id = ?",
                           (twin["id"],)).fetchone()["website_title"]
        s.check("a title the owner has changed is never put back", now == "La Chambre du Test",
                detail=f"{now!r}, {changed} changed")
        s.check("and run again it does nothing", m.apply_room_titles_2026_11_23(conn) == 0)
    finally:
        conn.execute("UPDATE rooms SET website_title = ? WHERE id = ?",
                     (twin["website_title"], twin["id"]))
        conn.commit()
        put_flag()
    conn.close()

    s.section("The Stay page: the title, and the plain name small beneath it")
    html = anon.get("/book").get_data(as_text=True)
    pairs = re.findall(r'<h3 translate="no" class="g-card__name">([^<]*)</h3>\s*'
                       r'<p class="g-room__plain">([^<]*)</p>', html)
    on_sale = {n: r for n, r in rooms.items() if r["active"]}
    s.check("every room on sale is headed by its title over its plain name",
            sorted(pairs) == sorted((r["website_title"], n) for n, r in on_sale.items()
                                    if r["website_title"]), detail=str(pairs))
    s.check("'La Chambre Jeanne d’Arc' over 'The Twin Room', as the brief has it",
            ("La Chambre Jeanne d’Arc", "The Twin Room") in pairs)
    s.check("the lightbox's 'See …' names the room by its title",
            'data-room-name="La Chambre Jeanne d’Arc"' in html)
    s.check("the photographs are still the room's own: matched on the name, not the title",
            'alt="The Twin Room' in html or "The Twin Room:" in html or "The Twin Room" in html)
    home = anon.get("/").get_data(as_text=True)
    s.check("the home page's room cards the same",
            '<h3 translate="no" class="g-homeroom__name">La Chambre Jeanne d’Arc</h3>' in home
            and '<p class="g-room__plain">The Twin Room</p>' in home)
    page = anon.get(f"/book/{twin['id']}").get_data(as_text=True)
    s.check("the room's own page: the title as its heading, the name beneath",
            re.search(r'<h1 translate="no" class="g-display">La Chambre Jeanne d’Arc</h1>\s*'
                      r'<p class="g-room__plain">The Twin Room</p>', page) is not None)
    s.check("and its <title> carries both, for search",
            "<title>Book La Chambre Jeanne d’Arc, The Twin Room — Château de Gudanes</title>" in page)

    s.section("A room with no title shows its name alone, with no second line")
    conn = db()
    try:
        conn.execute("UPDATE rooms SET website_title = '' WHERE id = ?", (twin["id"],))
        conn.commit()
        html = anon.get("/book").get_data(as_text=True)
        s.check("headed by its name", '<h3 translate="no" class="g-card__name">The Twin Room</h3>'
                in html)
        s.check("with no plain-name line under it",
                not re.search(r'>The Twin Room</h3>\s*<p class="g-room__plain">', html))
    finally:
        conn.execute("UPDATE rooms SET website_title = ? WHERE id = ?",
                     (twin["website_title"], twin["id"]))
        conn.commit()
        conn.close()

    s.section("One field in Admin > Rooms, so the four unconfirmed titles change without code")
    form = oc.get(f"/admin/rooms/{twin['id']}/edit").get_data(as_text=True)
    s.check("the edit form has the field, filled",
            'name="website_title"' in form and 'value="La Chambre Jeanne d’Arc"' in form)
    the_form = next((f for f in _harness.forms_on(form)
                     if any(x["name"] == "website_title" for x in f["fields"])), None)
    answers = {"website_title": "  La Chambre   d’Essai "}
    if twin["active"]:
        answers["active"] = "on"
    if twin.get("workshop_room"):
        answers["workshop_room"] = "on"
    answers = _harness.fill(the_form, answers) if the_form else answers
    r = oc.post(f"/admin/rooms/{twin['id']}/edit", data=answers)
    conn = db()
    try:
        row = conn.execute("SELECT * FROM rooms WHERE id = ?", (twin["id"],)).fetchone()
        s.check("saving it stores the title, its spaces tidied",
                r.status_code in (302, 303) and row["website_title"] == "La Chambre d’Essai",
                detail=f"{r.status_code} {row['website_title']!r}")
        s.check("and leaves the name and the Booking.com name exactly as they were",
                row["name"] == twin["name"]
                and (row["channel_name"] or None) == (twin["channel_name"] or None),
                detail=f"{row['name']!r} {row['channel_name']!r}")
        html = anon.get("/book").get_data(as_text=True)
        s.check("the Stay page says the new title at once",
                '<h3 translate="no" class="g-card__name">La Chambre d’Essai</h3>' in html)
        rooms_page = oc.get("/admin/rooms").get_data(as_text=True)
        s.check("Admin > Rooms shows it beside the name",
                "La Chambre d’Essai on the website" in visible_text(rooms_page))
    finally:
        _restore_room(conn, twin)
        conn.close()

    s.section("Booking.com's side keeps the plain names")
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (twin["id"],)).fetchone()
    feed = anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True)
    conn.close()
    s.check("the calendar feed Booking.com reads never carries the title",
            "Jeanne" not in feed, detail=feed[:200])
    src = io.open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    readers = set()
    for match in re.finditer(r"website_title", src):
        head = src.rfind("\ndef ", 0, match.start())
        readers.add(src[head + 5:src.find("(", head)] if head > -1 else "?")
    s.check("the only code that reads the title is the title's own: the seed, the form, the "
            "Rooms page's save and room_website_titles",
            readers <= {"apply_room_titles_2026_11_23", "room_website_titles", "room_title",
                        "room_fields_from_form", "new_room", "edit_room", "init_db"},
            detail=str(sorted(readers)))

    s.section("Letters say the title too")
    conn = db()
    try:
        with m.app.test_request_context("/"):
            subject, body, _letter = m.render_email_template(conn, "room_request_received", {
                "guest_name": "Test", "room_name": "The Twin Room", "stay_lines": "",
                "reference_code": "GUD-TEST", "checkin_url": "https://example.invalid/x"})
            s.check("the request letter's subject", subject and "La Chambre Jeanne d’Arc" in subject,
                    detail=repr(subject))
            s.check("and its words", body and "Your request for La Chambre Jeanne d’Arc" in body,
                    detail=(body or "")[:160])
            subject, body, _letter = m.render_email_template(conn, "channel_checkin", {
                "guest_name": "Test", "room_name": "The Twin Room", "arrival_date": "1 May",
                "departure_date": "3 May", "checkin_url": "https://example.invalid/x"})
            s.check("but a Booking.com guest is written to under the name they booked by",
                    body and "booking The Twin Room at" in body and "Jeanne" not in (body or ""),
                    detail=(body or "")[:160])
            s.check("a name that is no room's is left as it came",
                    m.letter_room_title(conn, "room_cancelled", {"room_name": "Room not stated"})
                    == {"room_name": "Room not stated"})
            room = conn.execute("SELECT * FROM rooms WHERE id = ?", (twin["id"],)).fetchone()
            arrival = _harness.free_window(twin["id"], 2, after_days=200)
            ref, _token = m.create_booking(
                conn, room, TAG + " Guest", "zzbrief@example.invalid", "",
                arrival, arrival + timedelta(days=2), 2, "", [], payment_status="unpaid")
            conn.commit()
            booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                                   (ref,)).fetchone()
            if booking is None:
                s.check("there is a booking on the Twin Room to write about", False)
            else:
                context, card = m.room_confirmation_context(conn, booking, "The Twin Room")
                s.check("the confirmation names the room by its title",
                        context["room_name"] == "La Chambre Jeanne d’Arc", detail=context["room_name"])
                s.check("and its stay details carry both, where a guest checks which room",
                        "Room: La Chambre Jeanne d’Arc (The Twin Room)" in context["stay_details"])
                drawn = m.booking_confirmation_html(conn, booking, "The Twin Room")
                s.check("the drawn confirmation's Room line: the title, the name small beneath",
                        re.search(r"La Chambre Jeanne d’Arc<br><span[^>]*>The Twin Room</span>", drawn)
                        is not None)
    finally:
        conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()


def _links(s, anon):
    s.section("4. /links, for the Instagram bio")
    was_live = m.SITE_IS_LIVE
    try:
        m.SITE_IS_LIVE = True
        r = anon.get("/links")
        html = r.get_data(as_text=True)
        s.check("it answers", r.status_code == 200, detail=str(r.status_code))
        s.check("noindex, even when the site is live -- the only state in which it matters",
                re.search(r'<meta name="robots" content="noindex', html) is not None)
        home = anon.get("/").get_data(as_text=True)
        s.check("the home page is still indexable (the expensive mistake)",
                re.search(r'<meta name="robots" content="noindex', home) is None)
        sitemap = anon.get("/sitemap.xml").get_data(as_text=True)
        s.check("it is not in the sitemap", "/links" not in sitemap and "<urlset" in sitemap)
        s.check("and the site links to it from nowhere", 'href="/links"' not in home)
    finally:
        m.SITE_IS_LIVE = was_live
    words = visible_text(html)
    s.check("the house's name and its one line",
            "Château de Gudanes" in words
            and "An 18th-century château in the French Pyrénées" in words)
    buttons = re.findall(r'<a class="g-links__btn" href="([^"]+)">\s*<span class="g-links__k">([^<]+)</span>',
                         html)
    s.check("three large buttons: Stay, Retreats, Celebrate",
            [(k, h) for h, k in buttons] == [("Stay", "/book"), ("Retreats", "/workshops"),
                                             ("Celebrate", "/events/weddings")],
            detail=str(buttons))
    s.check("Instagram and the newsletter, small at the foot",
            'href="https://www.instagram.com/chateaugudanes/"' in html
            and 'href="#g-newsletter"' in html and 'id="g-newsletter"' in html)
    s.check("no exclamation marks", "!" not in words)

    s.section("The old pram is on no public page (owner, 23 November 2026)")
    shown = [p for p in ("/", "/gallery", "/events/photoshoots", "/restoration", "/history", "/events",
                         "/events/weddings", "/events/private", "/facilities", "/workshops")
             if "fireplace-and-pram" in anon.get(p).get_data(as_text=True)]
    s.check("the fireplace and the old pram are shown nowhere", not shown, detail=str(shown))
    shoot = anon.get("/events/photoshoots").get_data(as_text=True)
    hero = re.search(r'<img[^>]*class="g-hero__img"[^>]*>', shoot)
    s.check("/events/photoshoots opens on a photograph, not a plain band",
            hero is not None and ("mille-feuille" in hero.group(0) or "/room-photos/" in hero.group(0)),
            detail=hero.group(0)[:160] if hero else "no hero photograph")

    s.section("Its prices are the site's own, and follow the owner's changes")
    conn = db()
    facts = m.house_room_facts(conn)
    with m.app.test_request_context("/"):
        retreats = m.retreats_from_price(m.offered_workshops())
    s.check("Stay: room and breakfast from the cheapest room on sale",
            f"Room and breakfast from €{facts['from_price']:,.0f}" in words, detail=words[:300])
    if retreats:
        s.check("Retreats: from the cheapest retreat on offer, per person",
                f"La vie de château, from €{retreats:,.0f} per person" in words.replace(" ,", ","),
                detail=words[:400])
        press = visible_text(anon.get("/press").get_data(as_text=True))
        s.check("the same figure the stay panel says on the other pages",
                f"From €{retreats:,.0f} per person" in press)
    cheapest = conn.execute(
        "SELECT id, price_per_night FROM rooms WHERE active = 1 AND price_per_night > 0 "
        "ORDER BY price_per_night LIMIT 1").fetchone()
    try:
        conn.execute("UPDATE rooms SET price_per_night = 199 WHERE id = ?", (cheapest["id"],))
        conn.commit()
        moved = visible_text(anon.get("/links").get_data(as_text=True))
        s.check("a room's price changed in Admin is the price on /links",
                "Room and breakfast from €199" in moved, detail=moved[:300])
    finally:
        conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?",
                     (cheapest["price_per_night"], cheapest["id"]))
        conn.commit()
        conn.close()
    s.check("no price at all rather than 'from €0'",
            m.retreats_from_price([{"price_per_person": 0}, {"price_per_person": None}]) is None)


def _form_notices(s, anon):
    """The owner, the same day: the form notices came as one sentence, "too annoying to copy paste -- make it
    Name: Email: etc". A wedding enquiry, as the house receives it."""
    s.section("5. A form's notice to the house: a line to each thing they gave")
    sent = []
    was_send, was_to = m.send_email, m.house_address_for
    m.send_email = lambda to, subject, body, **k: sent.append((to, subject, body)) or True
    m.house_address_for = lambda conn, area: "zzhouse@example.invalid"
    try:
        anon.post("/events/inquire", data=dict(
            _harness.contact_form_fields(), event_type="wedding", contact_name=TAG + " Mathilde",
            contact_email="zzbrief.mz@example.invalid", contact_phone="+33 6 12 34 56 78",
            preferred_date="2027-09-18", guest_count="100", message="A September wedding."))
    finally:
        m.send_email, m.house_address_for = was_send, was_to
    note = next((b for to, subj, b in sent if to == "zzhouse@example.invalid"), "")
    fields = dict(re.findall(r"^([A-Z][A-Za-z ]+): (.+)$", note, re.M))
    s.check("the house is told", bool(note), detail=str([x[1] for x in sent]))
    s.check("name, email and telephone each on a line of their own",
            fields.get("Name") == TAG + " Mathilde" and fields.get("Email") == "zzbrief.mz@example.invalid"
            and fields.get("Telephone") == "+33 6 12 34 56 78", detail=note[:300])
    s.check("the event, the date and how many, each on a line",
            fields.get("Event") == "Wedding" and fields.get("Preferred date") == "18 September 2027"
            and fields.get("Guests") == "100", detail=str(fields))
    s.check("a question left empty says so", fields.get("Other date") == "not said")
    s.check("their own words follow, under their own heading", "Their message:\nA September wedding." in note)
    s.check("and the link opens this enquiry, not the list of them all",
            re.search(r"Open the enquiry:\n\S+/admin/events/\d+$", note.rstrip()) is not None,
            detail=note[-160:])
    s.check("nothing of it run into a sentence", "inquired about" not in note)
    conn = db()
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("The website brief of 23 November 2026")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    _press(s, anon)
    _redirects(s, anon)
    _rooms(s, oc, anon)
    _links(s, anon)
    _form_notices(s, anon)
    return s


if __name__ == "__main__":
    run().report()
