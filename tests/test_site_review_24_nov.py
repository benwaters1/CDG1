"""The owner's full review of the public site, 24 November 2026 (BRIEF-site-review-24-nov).

What it found, and what this holds:

1. The old words. The site calls them retreats wherever a guest chooses one, but "workshop" and "the full
   experience" survived in the footer, the newsletter, /contact, /book, /restoration, /history, /story, /restaurant
   and the 404 page. No public page now says either, except "Workshop guest" under the guests' own words (the
   owner chose that attribution). /terms is left out on purpose: its text is the owner's stored legal wording
   (app_settings), not a template, and is changed only when the owner says so.
2. "Lunch and dinner unavailable" read as a refusal. It says what the guest gets: lunch and dinner at the
   restaurants nearby, which we are happy to book for you.
3. /story said the Waters family bought the château in 2013 three times. Once now, under a photograph.
4. /restaurant, while the table is not serving, said "Not Serving at the Moment" at a public address. It was The
   Table until 26 Nov, when the owner took the page off the site: it is not found while dining is off
   (test_sweep_26_nov).
5. Two rooms printed "The Twin Room" under their titles. La Chambre Marie Curie is a queen and a double: its line
   is "The Queen Room (ground floor)" (owner, 24 Nov), a field in Admin > Rooms, and its name -- the key Booking.com
   and the rates use -- is untouched.
6. /book's facilities frame: the breakfast tile's photograph takes two cells, so twelve tiles made thirteen and one
   stood alone. The cells now fill every row, and "Les équipements" is said once.
7. /press and /gallery open on a photograph.
8. On a phone, "See N photographs" on the event pages and the arrows on /book's room photographs are 44px.

Negative control: put "workshops & events" back in the footer and the first section goes red.
"""
import os
import re
import shutil
import tempfile

from _harness import Suite, clients, db, visible_text
import _harness
from test_copy_unchanged import public_paths, words_of

m = _harness.m

OLD_WORDS = re.compile(r"workshop|full experience|unavailable", re.I)


def _flat(text):
    """Visible text with the space a link leaves before the punctuation after it taken out ("nearby , which")."""
    return re.sub(r"\s+(?=[,.;:])", "", text)


def _setting(conn, key, value):
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    if value is not None:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _old_words(html):
    """Every use of the old words in what a visitor reads (the words and the photographs' descriptions), less the
    guests' attribution "Workshop guest"."""
    words, captions, _stray = words_of(html)
    text = (words + " | " + " | ".join(captions)).replace("Workshop guest", "")
    return [text[max(0, x.start() - 50):x.end() + 30] for x in OLD_WORDS.finditer(text)]


def run():
    s = Suite("The public site, reviewed 24 November 2026")
    anon = m.app.test_client()
    conn = db()
    was_dm = (conn.execute("SELECT value FROM app_settings WHERE key = 'dining_mode'").fetchone() or [None])[0]
    made_room = None
    try:
        _old_words_everywhere(s, anon, conn)
        _lunch_and_dinner(s, anon)
        _story(s, anon)
        _restaurant(s, anon, conn)
        made_room = _room_labels(s, anon, conn)
        _facilities(s, anon)
        _photographs_first(s, anon)
        _open_next(s)
        _tap_targets(s, anon)
    finally:
        _setting(conn, "dining_mode", was_dm)
        if made_room:
            conn.execute("DELETE FROM rooms WHERE id = ?", (made_room,))
            conn.commit()
        conn.close()
    return s


def _old_words_everywhere(s, anon, conn):
    s.section("1. No public page says workshop, the full experience or unavailable")
    paths = [p for p in public_paths(anon) if p != "/terms"]
    paths += ["/this-page-is-not-here", "/restaurant", "/story", "/contact", "/history", "/restoration"]
    seen = {}
    for mode in ("hidden", "open"):
        _setting(conn, "dining_mode", mode)
        for p in dict.fromkeys(paths):
            r = anon.get(p)
            if r.status_code not in (200, 404):
                continue
            hits = _old_words(r.get_data(as_text=True))
            if hits:
                seen["%s (dining %s)" % (p, mode)] = hits[:2]
    s.check("the sitemap's pages, the 404 page and the restaurant, both ways the table is set", len(paths) >= 20,
            detail=str(len(paths)))
    s.check("none says workshop (bar \"Workshop guest\"), the full experience, or unavailable", not seen,
            detail=str(seen)[:900])
    home = anon.get("/").get_data(as_text=True)
    # 26 Nov (owner, the naming rule): "Retreat guest, summer 2024", not "Workshop guest".
    s.check("the guests' attribution is the owner's of 26 Nov", "Retreat guest" in home and "Workshop guest" not in home)
    s.check("the footer's label is retreats and events", "retreats and events" in home)
    s.check("so is its French and Spanish", m.translations.FR.get("retreats and events") == "séjours et événements"
            and m.translations.ES.get("retreats and events") == "retiros y eventos")
    s.check("and the newsletter's line has both", all(
        "retreat dates" in k and "séjours" in m.translations.FR[k] and "retiros" in m.translations.ES[k]
        for k in m.translations.FR if k.startswith("The restoration as it happens"))
        and any(k.startswith("The restoration as it happens") for k in m.translations.FR))
    s.check("/workshops' French eyebrow is not \"Les retraites\" (pensions, first)",
            "Les retraites" not in anon.get("/workshops").get_data(as_text=True))


def _lunch_and_dinner(s, anon):
    s.section("2. Lunch and dinner: what the guest gets, not a refusal")
    home = _flat(visible_text(anon.get("/").get_data(as_text=True)))
    book = _flat(visible_text(anon.get("/book").get_data(as_text=True)))
    ws = _flat(visible_text(anon.get("/workshops").get_data(as_text=True)))
    s.check("home: at the restaurants nearby, which we are happy to book for you",
            "Lunch and dinner at the restaurants nearby, which we are happy to book for you" in home)
    s.check("/book: the same, before a night is booked",
            "Lunch and dinner at the restaurants nearby, which we are happy to book for you" in book)
    s.check("/workshops' table: at restaurants nearby; we book for you", "At restaurants nearby; we book for you" in ws)


def _story(s, anon):
    s.section("3. /story says it once")
    html = anon.get("/story").get_data(as_text=True)
    text = _flat(visible_text(html))
    s.check("the 2013 purchase is told once", text.count("bought") == 1 and "bought Château de Gudanes in 2013" in text,
            detail=str(text.count("bought")))
    s.check("\"room by room\" once at most", text.lower().count("room by room") <= 1,
            detail=str(text.lower().count("room by room")))
    s.check("every fact kept: the ruin, the 500 tonnes, the Polignac medal, the household",
            all(x in text for x in ("classified as a ruin", "500 tonnes", "Prince Louis de Polignac",
                                    "Prince Albert II of Monaco", "family château, not a hotel",
                                    "every decision about the walls")))
    s.check("one pair of buttons, not two", text.count("Experience the château") == 1,
            detail=str(text.count("Experience the château")))
    s.check("and a photograph at the top", _photo_before_heading(html))


def _restaurant(s, anon, conn):
    # SUPERSEDED 26 Nov (owner, second sweep 1.24): the restaurant page is not to be public at all. While the
    # table is switched off /restaurant is not found, in every language, rather than The Table this section
    # used to read. test_sweep_26_nov holds that; this keeps only the half of item 4 that still stands: no
    # public address says "Not Serving at the Moment".
    s.section("4. /restaurant no longer says Not Serving at the Moment")
    _setting(conn, "dining_mode", "hidden")
    r = anon.get("/restaurant")
    s.check("while the table is switched off it is not found, rather than a dead end", r.status_code == 404
            and "Not Serving" not in r.get_data(as_text=True), detail=str(r.status_code))


def _room_labels(s, anon, conn):
    s.section("5. No two rooms share a label")
    made = None
    room = conn.execute("SELECT id FROM rooms WHERE name = 'The Twin Room (Ground Floor)'").fetchone()
    if not room:
        made = conn.execute(
            "INSERT INTO rooms (name, website_title, description, max_occupancy, max_adults, max_children, "
            "price_per_night, active, sort_order, bed_setup, export_token) "
            "VALUES ('The Twin Room (Ground Floor)', 'La Chambre Marie Curie', '', 2, 2, 0, 270, 1, 99, "
            "'A queen bed and a double bed', 'zz-site-review-24-nov')").lastrowid
        conn.commit()
        room_id = made
    else:
        room_id = room["id"]
    was = conn.execute("SELECT active, website_title, website_label FROM rooms WHERE id = ?", (room_id,)).fetchone()
    conn.execute("UPDATE rooms SET active = 1, website_title = 'La Chambre Marie Curie', website_label = NULL "
                 "WHERE id = ?", (room_id,))
    conn.execute("DELETE FROM app_settings WHERE key = 'room_labels_2026_11_24'")
    conn.commit()
    try:
        s.check("the once-only seed gives La Chambre Marie Curie its line", m.apply_room_labels_2026_11_24(conn) == 1)
        label = conn.execute("SELECT website_label FROM rooms WHERE id = ?", (room_id,)).fetchone()["website_label"]
        s.check("the line is the owner's: The Queen Room (ground floor)", label == "The Queen Room (ground floor)",
                detail=str(label))
        s.check("and it runs once: a line the owner changes is never put back",
                m.apply_room_labels_2026_11_24(conn) == 0)
        for path in ("/", "/book", "/book/%d" % room_id):
            html = anon.get(path).get_data(as_text=True)
            lines = [x.strip() for x in re.findall(r'class="g-room__plain">([^<]+)<', html)]
            s.check("%s: no two rooms carry the same line" % path, lines and len(lines) == len(set(lines)),
                    detail=str(lines))
            if path != "/book/%d" % room_id:
                s.check("%s: Marie Curie's line is The Queen Room (ground floor)" % path,
                        "The Queen Room (ground floor)" in lines and "The Twin Room (Ground Floor)" not in lines,
                        detail=str(lines))
        page = anon.get("/book/%d" % room_id).get_data(as_text=True)
        s.check("its own page's title says it too", "La Chambre Marie Curie, The Queen Room (ground floor)" in page)
        with m.app.test_request_context("/"):
            letter = m.room_title_and_plain("The Twin Room (Ground Floor)", conn)
            twin = m.room_title_and_plain("The Twin Room", conn)
        s.check("a letter names it without doubled brackets",
                letter == "La Chambre Marie Curie, The Queen Room (ground floor)", detail=letter)
        s.check("and the real twin room keeps its line", twin.endswith("(The Twin Room)"), detail=twin)
        name = conn.execute("SELECT name FROM rooms WHERE id = ?", (room_id,)).fetchone()["name"]
        s.check("the room keeps its name, the key Booking.com and the rates use", name == "The Twin Room (Ground Floor)")
        with m.app.test_request_context("/", method="POST", data={"name": "X", "website_label": "  A  line  "}):
            f = m.room_fields_from_form()
        s.check("Admin > Rooms saves the line, one line of words", f["website_label"] == "A line",
                detail=str(f.get("website_label")))
        s.check("and the room form has the field (the owner's own)", 'name="website_label"' in oc_get(room_id))
    finally:
        if not made:
            conn.execute("UPDATE rooms SET active = ?, website_title = ?, website_label = ? WHERE id = ?",
                         (was["active"], was["website_title"], was["website_label"], room_id))
            conn.commit()
    return made


def oc_get(room_id):
    oc, _ec, _o, _e = clients()
    return oc.get("/admin/rooms/%d/edit" % room_id).get_data(as_text=True)


def _cells(html):
    grid = re.search(r'<ul class="g-facil">(.*?)</ul>', html, re.S)
    if not grid:
        return None
    tiles = re.findall(r'<li class="g-facil__i( g-facil__i--photo)?"', grid.group(1))
    return sum(2 if t else 1 for t in tiles)


def _facilities(s, anon):
    s.section("6. The facilities frame fills its rows")
    for path in ("/book", "/facilities"):
        html = anon.get(path).get_data(as_text=True)
        cells = _cells(html)
        s.check("%s: the cells fill every row, at four, three and two across" % path,
                cells is not None and cells % 4 == 0 and cells % 3 == 0, detail=str(cells))
    book = anon.get("/book").get_data(as_text=True)
    s.check("/book keeps table tennis, inside the tennis court's tile", "and table tennis" in visible_text(book))
    s.check("/facilities keeps table tennis as a tile of its own",
            "Le ping-pong" in anon.get("/facilities").get_data(as_text=True))
    frame = re.search(r'<section class="g-sec g-facil-sec">(.*?)</section>', book, re.S)
    s.check("\"Les équipements\" is said once", frame and frame.group(1).count("Les équipements") == 1,
            detail=str(frame and frame.group(1).count("Les équipements")))
    fac = anon.get("/facilities").get_data(as_text=True)
    s.check("/facilities has one section called The Grounds", visible_text(fac).count("The Grounds") == 1,
            detail=str(visible_text(fac).count("The Grounds")))


def _photo_before_heading(html):
    main = html.split("<main", 1)[-1]
    img = re.search(r"<img\b", main)
    head = re.search(r"<h[12]\b", main)
    return bool(img and head and img.start() < head.start())


def _photographs_first(s, anon):
    s.section("7. /press and /gallery open on a photograph")
    for path in ("/press", "/gallery"):
        s.check("%s: a photograph above the first heading" % path, _photo_before_heading(anon.get(path).get_data(as_text=True)))
    press = anon.get("/press").get_data(as_text=True)
    s.check("/press: no drawing in a circle where a photograph belongs", "illus-camera" not in press
            and not re.search(r'g-vignette[^>]*>\s*<svg[^>]*camera', press))
    home = anon.get("/").get_data(as_text=True)
    s.check("home: the retreats' photographs say what they show", "A photograph from" not in home)


def _open_next(s):
    s.section("What is open next: the date once, the rooms beneath it")
    with m.app.test_request_context("/book"):
        macro = m.app.jinja_env.get_template("_availability_strip.html").make_module(
            {"house_rooms": {"count": 6, "count_word": "six"}}).next_free
        rows = [{"date": "28 November", "date_iso": "2026-11-28", "weekday": "Saturday", "room_name": "Room %d" % i,
                 "nights": 7, "price": 270} for i in range(5)]
        html = str(macro(rows))
    text = visible_text(html)
    s.check("five rooms on one night: the date said once, as a heading",
            text.count("28 November") == 1 and "From Saturday 28 November" in text, detail=text[:200])
    s.check("and the five rooms listed under it", html.count('class="g-nextf__i"') == 5)


def _tap_targets(s, anon):
    s.section("8. On a phone, the two small tap targets are 44px")
    from test_staff_header_on_a_phone import find_browser
    import test_public_pages_measured as measured
    from test_public_margins import _render
    browser = find_browser()
    s.check("headless Chrome, Chromium or Edge is on this machine", bool(browser),
            detail="set GUDANES_CHROME; a check that quietly does not run reads as cover")
    if not browser:
        return
    workdir = tempfile.mkdtemp(prefix="gudanes_review24_")
    was_live = m.SITE_IS_LIVE
    try:
        for css in ("gudanes.css", "gudanes-pictures.css"):
            shutil.copy(os.path.join(_harness.ROOT, "static", css), os.path.join(workdir, css))
        m.SITE_IS_LIVE = True
        pages = _render(anon, ["/events/weddings", "/events/private", "/events/photoshoots", "/book"])
        by = dict(pages)
        s.check("the three event pages carry a See N photographs button",
                all('class="g-nearby__more"' in by.get(p, "") for p in ("/events/weddings", "/events/private",
                                                                       "/events/photoshoots")))
        s.check("and /book its room photographs' arrows", 'g-car--room' in by.get("/book", ""))
        squeezed = by["/events/weddings"].replace(
            "</head>", "<style>.g .g-nearby__more{min-height:0 !important;height:20px !important;padding:0 !important}</style></head>", 1)
        got, n = measured._measure(browser, pages + [("control:squeezed", squeezed)], (320, 375), "phone", True,
                                   workdir, "rv24")
        s.check("every frame came back from the browser", len(got) == n, detail="%d of %d" % (len(got), n))
        ctrl = [d for d in got if d["page"] == "control:squeezed"]
        s.check("the reader finds a See N photographs button squeezed to 20px",
                ctrl and all(any("g-nearby__more" in x for x in d["small"]) for d in ctrl))
        for d in sorted((d for d in got if not d["page"].startswith("control:")), key=lambda d: (d["page"], d["w"])):
            small = [x for x in d["small"] if "g-nearby__more" in x or "g-car__btn" in x]
            s.check("%s at %dpx: See N photographs and the photo arrows are 44px or more" % (d["page"], d["w"]),
                    not small, detail="; ".join(small[:6]))
    finally:
        m.SITE_IS_LIVE = was_live
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    print(run().report())
