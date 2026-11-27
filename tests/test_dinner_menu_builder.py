"""Dinner at Gudanes, complete (owner's brief of 27 November 2026), parts 1, 5 and 6: the menus are built in the app.

"I want it to be highly functional and present very well, no gaps, very easy to use on the back end and front, menu
easy, presented beautifully ... is it easy to create a menu?"

What this holds:
  - THE BUILDER (/admin/menus): a menu is a name, a price, wine included or not, and courses in order, the house's
    seven laid out for a new one. Each course has its dish and wine in French and English, the glass, the main
    product's origin, the 14 allergens and the meat in it. A course with meat cannot be saved without its country
    (the law), and the form says why in one line. Every dish and wine is kept in a library; the English for words the
    library knows comes from the library, the rest from the assistant (stood in here), and only as a suggestion.
    Courses keep the order posted; a removed one goes. Duplicate makes the next; a menu an occasion serves cannot be
    deleted, only archived. The preview draws the form as it stands without saving it.
  - WHAT ONE MENU MAKES: the guest's page in the house style (the pop-up and /menu), the A4 table menu, the A3 board
    with every line the law asks (copied from Menus/menu_a3.py), the allergen sheet, the night's sheet for the kitchen
    with each table's notes read against the menu, and the newsletter's words.
  - THE OCCASION'S FORM: the menu is chosen from the library, built from the form (and comes back as its menu), or
    uploaded; a choice and a file replace each other; a night can have its own. A live dinner names everything it
    still lacks -- price, covers, menu -- before it can go on sale. The panel preview is the guest's panel.
  - PRESENTED: the panel's photograph is never drawn wider than half its pixels on a desk; the pop-up frames the
    built menu as a page, with Print, not a PDF viewer; a card for an occasion with dinner carries the mark and price.

Nothing reaches the assistant, the mail or Stripe: the English is stood in, and no letter is sent.
"""
import re
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from _harness import Suite, clients, db, ensure_room, flashes, free_window, keep_rows
import _harness

m = _harness.m
TAG = "ZMNB"

HOUSE = [
    ("L’apéritif", "Crémant de Limoux et gougères au fromage des Pyrénées",
     "Sparkling Crémant de Limoux, with warm cheese gougères", "Crémant de Limoux AOP", "Sparkling · Crémant de Limoux AOP",
     "12 cl", "Fromage des Pyrénées", "Pyrenean cheese", "France", "", ["milk", "gluten", "eggs", "sulphites"]),
    ("Entrée", "Truite de l’Ariège fumée au château, crème au raifort, radis",
     "Ariège trout smoked at the château, horseradish cream and radishes", "Blanc · Côtes de Gascogne IGP",
     "White · Côtes de Gascogne IGP", "12 cl", "Truite élevée en Ariège", "Trout raised in the Ariège", "France", "",
     ["fish", "milk"]),
    ("Plat", "Agneau des Pyrénées rôti au thym, pommes fondantes, petits pois",
     "Pyrenean lamb roasted with thyme, fondant potatoes and garden peas", "Rouge · Madiran AOP", "Red · Madiran AOP",
     "12 cl", "Agneau", "Lamb", "France", "agneau", ["sulphites"]),
    ("Dessert", "Tarte aux noix, crème légère à la vanille", "Walnut tart with a light vanilla cream",
     "Moelleux · Jurançon AOP", "Sweet · Jurançon AOP", "8 cl", "Noix", "Walnuts", "France", "",
     ["gluten", "eggs", "milk", "nuts"]),
]


def menu_form(name, courses=HOUSE, *, price="95", wine="1", time="19:30", order=None, drop=(), **extra):
    """The builder's form as the page posts it."""
    md = MultiDict({"name": name, "price": price, "service_time": time})
    if wine:
        md.add("wine_included", "1")
    for i, (k, fr, en, w, wen, g, ofr, oen, cfr, meat, al) in enumerate(courses):
        pos = order[i] if order else i
        for key, value in {"pos": str(pos), "course_fr": k, "dish_fr": fr, "dish_en": en, "wine_fr": w,
                           "wine_en": wen, "wine_glass": g, "origin_fr": ofr, "origin_en": oen,
                           "country_fr": cfr, "meat": meat, "fm": "1"}.items():
            md.add(f"c-{i}-{key}", value)
        for a in al:
            md.add(f"c-{i}-al", a)
        if i in drop:
            md.add(f"c-{i}-remove", "1")
    for k, v in extra.items():
        md[k] = v
    return md


def _menu(conn, name):
    return conn.execute("SELECT * FROM dinner_menus WHERE name = ?", (name,)).fetchone()


def _courses(conn, menu_id):
    return conn.execute("SELECT * FROM dinner_menu_courses WHERE menu_id = ? ORDER BY position",
                        (menu_id,)).fetchall()


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%'")]
    marks = ",".join("?" * len(ids)) or "NULL"
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN ({marks})", ids)
    for occ_id in ids:
        for r in conn.execute("SELECT menu_file, banner_file FROM stay_occasions WHERE id = ?", (occ_id,)):
            m.remove_occasion_file(r["menu_file"])
        for r in conn.execute("SELECT menu_file FROM stay_occasion_dinners WHERE occasion_id = ?", (occ_id,)):
            m.remove_occasion_file(r["menu_file"])
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasions WHERE id IN ({marks})", ids)
    conn.execute(f"DELETE FROM restaurant_bookings WHERE guest_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM dinner_menus WHERE name LIKE '{TAG}%'")
    conn.execute("DELETE FROM dinner_dishes WHERE dish_fr IN ({})".format(",".join("?" * len(HOUSE) + "?")),
                 [c[1] for c in HOUSE] + [f"{TAG} plat du jour"])
    conn.execute("DELETE FROM dinner_wines WHERE wine_fr IN ({})".format(",".join("?" * len(HOUSE))),
                 [c[3] for c in HOUSE])
    conn.commit()


def run():
    s = Suite("Dinner at Gudanes, complete: the menu builder, what a menu makes, the occasion's form, the panel")
    oc, _ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasions", "status = 'live'"),
                keep_rows(conn, "restaurant_settings", "id = 1")]
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.commit()
    _cleanup(conn)
    was_ask = m.menu_english_ask
    try:
        _run(s, oc, conn)
    finally:
        m.menu_english_ask = was_ask
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, conn):
    anon = m.app.test_client()
    name = f"{TAG} Dîner à Gudanes, automne"

    # ------------------------------------------------------------------
    s.section("The builder: a new menu starts with the house's seven courses")
    r = oc.get("/admin/menus/new")
    page = r.get_data(as_text=True)
    s.check("the builder answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    for fr, _en in m.MENU_DEFAULT_COURSES:
        s.check(f"{fr} is laid out", f'value="{fr}"' in page)
    s.check("each course has the 14 allergens to tick",
            page.count('name="c-0-al"') == len(m.ALLERGENS), detail=page.count('name="c-0-al"'))
    s.check("and the meat in it", 'name="c-0-meat"' in page)
    s.check("and a live preview beside it", 'id="mb-frame"' in page and "admin_dinner_menu_preview" not in page
            and "/admin/menus/preview" in page)
    s.check("the staff side's restaurant area reaches it",
            m.ENDPOINT_AREA.get("admin_dinner_menus") == "restaurant"
            and m.ENDPOINT_AREA.get("admin_dinner_night") == "restaurant")

    # ------------------------------------------------------------------
    s.section("The law: meat says where it comes from, or the menu is not saved")
    no_origin = [list(c) for c in HOUSE]
    no_origin[2][8] = ""   # the lamb with no country
    r = oc.post("/admin/menus/new", data=menu_form(name, [tuple(c) for c in no_origin]))
    page = r.get_data(as_text=True)
    s.check("refused, and the form comes back", r.status_code == 400, detail=f"HTTP {r.status_code}")
    s.check("saying why, in one line", "French law requires the origin of every meat on a menu" in page
            and "agneau in it" in page)
    s.check("nothing was saved", _menu(conn, name) is None)
    s.check("what was typed is still there", "Agneau des Pyrénées rôti au thym" in page)
    s.check("a meat with its country passes",
            not m.dinner_menu_problems(m.read_dinner_menu_form(menu_form(name))))

    # ------------------------------------------------------------------
    s.section("Saved: the courses in the order posted, the library kept")
    # Posted with the dessert first and the starter last (as a drag would leave them), one course removed.
    r = oc.post("/admin/menus/new", data=menu_form(name, order=[1, 3, 2, 0]))
    menu = _menu(conn, name)
    s.check("saved", r.status_code == 302 and menu is not None, detail=f"HTTP {r.status_code}")
    courses = _courses(conn, menu["id"])
    s.check("in the order posted", [c["course_fr"] for c in courses] == ["Dessert", "L’apéritif", "Plat", "Entrée"],
            detail=[c["course_fr"] for c in courses])
    lamb = next(c for c in courses if c["course_fr"] == "Plat")
    s.check("the lamb holds its meat, its country and its allergens",
            lamb["meat"] == "agneau" and lamb["country_fr"] == "France" and lamb["allergens"] == "sulphites"
            and lamb["country_en"] == "France")
    s.check("the price, the wine and the time", menu["price"] == 95 and menu["wine_included"] == 1
            and menu["service_time"] == "19:30")
    lib = conn.execute("SELECT * FROM dinner_dishes WHERE dish_fr = ?", (HOUSE[2][1],)).fetchone()
    s.check("the dish is in the library with its English, origin and allergens",
            lib is not None and lib["dish_en"] == HOUSE[2][2] and lib["origin_fr"] == "Agneau"
            and lib["meat"] == "agneau" and lib["allergens"] == "sulphites")
    s.check("and the wine with its glass", conn.execute(
        "SELECT 1 FROM dinner_wines WHERE wine_fr = ? AND glass = '12 cl' AND wine_en = ?",
        (HOUSE[2][3], HOUSE[2][4])).fetchone() is not None)
    r = oc.post(f"/admin/menus/{menu['id']}", data=menu_form(name, drop=(1,)))
    s.check("a course ticked Remove goes", [c["course_fr"] for c in _courses(conn, menu["id"])]
            == ["L’apéritif", "Plat", "Dessert"], detail=[c["course_fr"] for c in _courses(conn, menu["id"])])
    oc.post(f"/admin/menus/{menu['id']}", data=menu_form(name))
    s.check("and comes back when posted again", len(_courses(conn, menu["id"])) == 4)
    s.check("the library counts the dish used twice, not two dishes",
            conn.execute("SELECT COUNT(*) AS n FROM dinner_dishes WHERE dish_fr = ?", (HOUSE[2][1],)).fetchone()["n"] == 1)

    # ------------------------------------------------------------------
    s.section("The next menu is mostly picking: the library and the English")
    r = oc.get(f"/admin/menus/{menu['id']}")
    page = r.get_data(as_text=True)
    s.check("the builder offers the saved dishes as you type", '<datalist id="dl-dishes">' in page
            and f'<option value="{HOUSE[2][1]}">' in page)
    asked = []
    m.menu_english_ask = lambda text, kind: asked.append((text, kind)) or "Duck breast, cherries"
    got = oc.post("/admin/menus/english", data={"kind": "dish", "text": HOUSE[2][1]}).get_json()
    s.check("words the library knows: its English, the assistant not asked",
            got == {"en": HOUSE[2][2], "from": "library"} and not asked, detail=(got, asked))
    got = oc.post("/admin/menus/english", data={"kind": "dish", "text": "Magret de canard aux cerises"}).get_json()
    s.check("new words: the assistant's, as a suggestion", got == {"en": "Duck breast, cherries", "from": "assistant"}
            and asked == [("Magret de canard aux cerises", "dish")], detail=(got, asked))
    s.check("a country: the house's own list", oc.post("/admin/menus/english", data={
        "kind": "country", "text": "Éthiopie"}).get_json()["en"] == "Ethiopia")

    def _boom(text, kind):
        raise RuntimeError("no network here")
    m.menu_english_ask = _boom
    got = oc.post("/admin/menus/english", data={"kind": "dish", "text": "Pigeon rôti"}).get_json()
    s.check("the assistant failing suggests nothing, and the page still answers", got == {"en": "", "from": None}, detail=got)
    s.check("nothing suggested is saved unseen: an English left blank stays blank",
            m.read_dinner_menu_form(MultiDict({"name": "x", "c-0-dish_fr": "Pigeon rôti", "c-0-course_fr": "Plat"}))
            ["courses"][0]["dish_en"] == "")

    # ------------------------------------------------------------------
    s.section("The preview draws the form as it stands, and saves nothing")
    r = oc.post("/admin/menus/preview", data=menu_form(f"{TAG} not saved", price="88"))
    page = r.get_data(as_text=True)
    s.check("the guest's page, drawn", r.status_code == 200 and "Dîner à Gudanes" in page
            and "Agneau des Pyrénées rôti au thym" in page and "88 €" in page)
    s.check("framed by the builder only", r.headers.get("X-Frame-Options") == "SAMEORIGIN"
            and "frame-ancestors 'self'" in (r.headers.get("Content-Security-Policy") or ""))
    s.check("and nothing saved", _menu(conn, f"{TAG} not saved") is None)
    s.check("an A3 preview too", "Prix nets en euros" in oc.post("/admin/menus/preview", data=menu_form(
        name, preview_mode="a3")).get_data(as_text=True))

    # ------------------------------------------------------------------
    s.section("What one menu makes: the A4 table menu, the A3 board, the allergen sheet")
    a4 = oc.get(f"/admin/menus/{menu['id']}/print/a4").get_data(as_text=True)
    a3 = oc.get(f"/admin/menus/{menu['id']}/print/a3").get_data(as_text=True)
    al = oc.get(f"/admin/menus/{menu['id']}/print/allergens").get_data(as_text=True)
    s.check("A4: the house's frame, the façade, 1213 · 1741 · 2013 and the script heading",
            "1213 · 1741 · 2013" in a4 and "img/menu/facade.png" in a4 and "corner-tl.png" in a4
            and 'class="script">Dîner à Gudanes' in a4)
    s.check("A4: French with the English beneath, the wine and the origin",
            HOUSE[1][1] in a4 and HOUSE[1][2] in a4 and "Blanc · Côtes de Gascogne IGP" in a4
            and "Agneau · origine : France" in a4 and "Lamb · origin: France" in a4)
    s.check("A4: on A4 paper, with Print / Save as PDF", "@page{ size:A4" in a4 and "Print / Save as PDF" in a4)
    s.check("A4: no price on the table menu (as the printed one)", "Menu · 95" not in a4)
    for line in ("Menu unique · 95 € par personne · vins compris",
                 "Prix nets en euros, taxes comprises. Prix service compris. Boissons comprises. Une carafe d’eau est offerte.",
                 "Origine des viandes : agneau, origine France.",
                 "Tous nos plats sont faits maison, élaborés sur place à partir de produits bruts.",
                 "Les 14 allergènes présents dans chaque plat sont indiqués dans le classeur à l’entrée",
                 "Service le soir à 19 h 30, sur réservation."):
        s.check(f"A3 board: {line[:48]}", line in a3)
    s.check("A3: the glass sizes", "Rouge · Madiran AOP · 12 cl" in a3 and "Jurançon AOP · 8 cl" in a3)
    s.check("A3: on A3 paper", "@page{ size:A3" in a3)
    s.check("allergen sheet: every dish against the 14", all(c[1] in al for c in HOUSE)
            and all(m.ALLERGENS[k] in al for k in m.ALLERGENS))
    walnut_row = al[al.find(HOUSE[3][1]):al.find("</tr>", al.find(HOUSE[3][1]))]
    s.check("and the walnut tart marked for tree nuts, the lamb not",
            "contains Tree nuts" in walnut_row
            and "contains Tree nuts" not in al[al.find(HOUSE[2][1]):al.find("</tr>", al.find(HOUSE[2][1]))])
    s.check("the sheet's table can scroll on a phone", '<div class="table-wrap">' in al)

    # ------------------------------------------------------------------
    s.section("Duplicate, archive, and never deleted while an occasion serves it")
    r = oc.post(f"/admin/menus/{menu['id']}/duplicate")
    copy = _menu(conn, f"{name} (copy)")
    s.check("one click makes the next menu", copy is not None and len(_courses(conn, copy["id"])) == 4
            and copy["copied_from"] == menu["id"])
    room = ensure_room(min_occupancy=2)
    first = free_window(room["id"], 3, after_days=230, clear_of_ateliers=True, clear_of_rate_overrides=True)
    n1, n2 = first, first + timedelta(days=1)
    occ_name = f"{TAG} An autumn dinner"
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, dinner_time = '19:30' WHERE id = 1")
    conn.commit()
    form = {"name": occ_name, "first_night": n1.isoformat(), "last_night": n2.isoformat(), "kind": "stay",
            "chef_on_site": "1", "status": "draft", "photo": "", "dinner_section_shown": "1", "serve_dinner": "1",
            "description": "Two nights in the valley in autumn, with breakfast each morning.",
            "dinner_price": "105", "dinner_covers": "12", "dinner_time": "19:30", "wine_included": "1",
            "wine_share": "30", "dinner_nights_shown": "1", "menu_choice_shown": "1",
            f"dinner_{n1.isoformat()}": "1", f"dinner_{n2.isoformat()}": "1"}
    oc.post("/admin/stay-occasions/new", data=form)
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (occ_name,)).fetchone()
    s.check("an occasion serving dinner, saved as a draft", occ is not None and occ["status"] == "draft")

    # A live dinner names everything it lacks.
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(form, status="live"), follow_redirects=True)
    said = " ".join(flashes(r)) + r.get_data(as_text=True)
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("with no menu it cannot go on sale", occ["status"] != "live", detail=occ["status"])
    s.check("and the form says what it needs", "Before the dinner can go on sale it needs a menu" in said)
    s.check("everything missing, named at once",
            m.dinner_go_live_missing(conn, dict(occ, dinner_price=None, dinner_covers=None))
            == ["the price a person", "the covers a night", "a menu"])

    # Chosen from the library: the occasion's menu, and the page draws it.
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(form, status="live", dinner_menu_id=str(menu["id"])))
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("a menu chosen from the library is the occasion's", occ["dinner_menu_id"] == menu["id"])
    s.check("and now it goes on sale", occ["status"] == "live", detail=occ["status"])
    r = oc.post(f"/admin/menus/{menu['id']}/delete", follow_redirects=True)
    s.check("a menu an occasion serves cannot be deleted", _menu(conn, name) is not None
            and "Archive it instead" in " ".join(flashes(r)))
    oc.post(f"/admin/menus/{menu['id']}/archive")
    s.check("archived, it is still the occasion's", _menu(conn, name)["status"] == "archived"
            and conn.execute("SELECT dinner_menu_id FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()[0]
            == menu["id"])
    lst = oc.get("/admin/menus").get_data(as_text=True)
    s.check("the list keeps it out of the way", f">{name}</a>" not in lst and f">{name} (copy)</a>" in lst)
    s.check("one chip away", name in oc.get("/admin/menus?state=Archived").get_data(as_text=True))
    form_page = oc.get(f"/admin/stay-occasions/{occ['id']}").get_data(as_text=True)
    s.check("and the occasion's form still offers it, marked", f"{name} · €95 (archived)" in form_page)
    oc.post(f"/admin/menus/{menu['id']}/archive")
    oc.post(f"/admin/menus/{copy['id']}/delete")
    s.check("a menu nothing serves is deleted", _menu(conn, f"{name} (copy)") is None)

    # ------------------------------------------------------------------
    s.section("The guest's menu: the house style, the occasion's price, in the pop-up as a page")
    slug = occ["slug"]
    r = anon.get(f"/whats-on/at-gudanes/{slug}/menu")
    page = r.get_data(as_text=True)
    s.check("the menu answers as a page", r.status_code == 200 and r.mimetype == "text/html")
    s.check("with the price the guest is charged, not the menu's", "Menu · 105 €" in page and "95 €" not in page)
    s.check("its nights and time", m.dinner_nights_words_fr([n1.isoformat(), n2.isoformat()]).capitalize() in page
            and "19 h 30" in page)
    s.check("each course's allergens for the guest", "Allergens: sulphites" in page)
    s.check("framed by the house's own pages only", r.headers.get("X-Frame-Options") == "SAMEORIGIN")
    occ_page = anon.get(f"/whats-on/at-gudanes/{slug}").get_data(as_text=True)
    s.check("See the menu opens it as a page", 'data-menu-kind="html"' in occ_page)
    dlg = open(m.os.path.join(_harness.ROOT, "templates", "_menu_dialog.html"), encoding="utf-8").read()
    s.check("the pop-up frames it at a reading width, with Print", "g-menudlg--page" in dlg
            and "dl.textContent = 'Print'" in dlg and "'embed=1'" in dlg)
    s.check("embedded, it drops the way back", '<p class="back">' not in anon.get(
        f"/whats-on/at-gudanes/{slug}/menu?embed=1").get_data(as_text=True))
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE id = ?", (occ["id"],))
    conn.commit()
    s.check("a draft's menu is not on the site", anon.get(f"/whats-on/at-gudanes/{slug}/menu").status_code == 404)
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE id = ?", (occ["id"],))
    conn.commit()

    # One night's own menu.
    menu2_name = f"{TAG} Dîner du samedi"
    oc.post("/admin/menus/new", data=menu_form(menu2_name, HOUSE[:2], price="120"))
    menu2 = _menu(conn, menu2_name)
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(
        form, status="live", dinner_menu_id=str(menu["id"]), **{f"night_menu_{n2.isoformat()}": str(menu2["id"])}))
    s.check("a night can have its own", conn.execute(
        "SELECT dinner_menu_id FROM stay_occasion_dinners WHERE occasion_id = ? AND dinner_date = ?",
        (occ["id"], n2.isoformat())).fetchone()[0] == menu2["id"])
    night = anon.get(f"/whats-on/at-gudanes/{slug}/menu/{n2.isoformat()}").get_data(as_text=True)
    s.check("and its page draws that menu", HOUSE[1][1] in night and HOUSE[2][1] not in night)
    whole = anon.get(f"/whats-on/at-gudanes/{slug}/menu").get_data(as_text=True)
    s.check("while the occasion's names only the nights it covers",
            m.dinner_nights_words_fr([n1.isoformat()]).capitalize() in whole
            and m.dinner_nights_words_fr([n2.isoformat()]) not in whole)

    # A file uploaded replaces the menu chosen; a menu chosen replaces the file.
    import io
    pdf = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    data = dict(form, status="live", dinner_menu_id=str(menu["id"]))
    data["menu"] = (io.BytesIO(pdf), "chef.pdf")
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=data, content_type="multipart/form-data")
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("a file uploaded in the same save wins", occ["menu_file"] and occ["dinner_menu_id"] is None)
    s.check("and the page serves the file", anon.get(f"/whats-on/at-gudanes/{slug}/menu").mimetype
            == "application/pdf")
    file_was = occ["menu_file"]
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(form, status="live", dinner_menu_id=str(menu["id"])))
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("a menu chosen replaces the file", occ["dinner_menu_id"] == menu["id"] and occ["menu_file"] is None)
    s.check("and the file is gone from the disk",
            not m.os.path.exists(m.os.path.join(m.OCCASION_FILES_DIR, file_was)))

    # Built from the occasion's form: comes back as its menu.
    menu3_name = f"{TAG} Built from the form"
    r = oc.post(f"/admin/menus/new?for_occasion={occ['id']}", data=menu_form(menu3_name, HOUSE[:3]))
    menu3 = _menu(conn, menu3_name)
    s.check("building one from the occasion returns to it", r.status_code == 302
            and f"/admin/stay-occasions/{occ['id']}" in r.headers.get("Location", ""))
    s.check("as its menu", conn.execute("SELECT dinner_menu_id FROM stay_occasions WHERE id = ?",
                                        (occ["id"],)).fetchone()[0] == menu3["id"])
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(form, status="live", dinner_menu_id=str(menu["id"])))

    # ------------------------------------------------------------------
    s.section("The occasion's form: the dinner section in order, the panel as guests see it")
    page = oc.get(f"/admin/stay-occasions/{occ['id']}").get_data(as_text=True)
    sec = page[page.find('id="dinner"'):page.find('id="guest-chef"')]
    order = ["1. Which nights", "2. Time", "3. Price a person", "4. Wine included", "5. Covers a night",
             "6. Open to guests not staying", "7. The menu", "8. As guests will see it"]
    at = [sec.find(x) for x in order]
    s.check("on/off, nights, time, price, wine, covers, outside guests, menu, preview: in that order",
            all(a >= 0 for a in at) and at == sorted(at), detail=dict(zip(order, at)))
    s.check("Live on the site, said", "Live on the site" in sec)
    s.check("Build a new menu from here", f"/admin/menus/new?for_occasion={occ['id']}" in sec)
    s.check("the preview frames the guest's panel", f"/admin/stay-occasions/{occ['id']}/panel-preview" in sec)
    pv = oc.get(f"/admin/stay-occasions/{occ['id']}/panel-preview")
    pvp = pv.get_data(as_text=True)
    real = anon.get(f"/whats-on/at-gudanes/{slug}").get_data(as_text=True)
    panel_real = real[real.find('<section class="g-dinpanel"'):real.find("</section>", real.find('<section class="g-dinpanel"'))]
    panel_prev = pvp[pvp.find('<section class="g-dinpanel"'):pvp.find("</section>", pvp.find('<section class="g-dinpanel"'))]
    s.check("the preview's panel is the page's panel, exactly", panel_real and panel_real == panel_prev)
    s.check("drawn with the public stylesheet", "gudanes.css" in pvp and pv.headers.get("X-Frame-Options") == "SAMEORIGIN")

    # ------------------------------------------------------------------
    s.section("Presented: the photograph sharp, the mark on the cards")
    s.check("the panel's photograph offers its 384 and its 768",
            re.search(r"long-table-tall-candles-384\.jpg(\?[^ ]*)? 384w", panel_real) is not None
            and re.search(r"long-table-tall-candles\.jpg(\?[^ ]*)? 768w", panel_real) is not None
            and 'width="768" height="1024"' in panel_real)
    css = open(m.os.path.join(_harness.ROOT, "static", "gudanes.css"), encoding="utf-8").read()
    s.check("and its column is never wider than 384px on a desk",
            "grid-template-columns: minmax(0, 384px) minmax(0, 1fr)" in css)
    wo = anon.get("/whats-on/at-gudanes").get_data(as_text=True)
    card = wo[wo.find(occ_name) - 1500:wo.find(occ_name)]
    s.check("a card for a dinner occasion carries the mark and the price",
            "g-occasion__dinmark" in card and "Dinner at Gudanes · " in card and "€105" in card)

    # ------------------------------------------------------------------
    s.section("The newsletter uses the built menu")
    subject, body, fingerprint, _b = m.occasion_newsletter_parts(conn, occ)
    s.check("the courses are in the letter, in English", "On the menu:" in body
            and f"Main course: {HOUSE[2][2]}" in body and f"Starter: {HOUSE[1][2]}" in body)
    s.check("and See the menu opens the page", "See the menu:" in body and f"/whats-on/at-gudanes/{slug}/menu" in body)
    conn.execute("UPDATE dinner_menus SET updated_at = '2099-01-01T00:00:00+00:00' WHERE id = ?", (menu["id"],))
    s.check("a change to the menu redrafts it", m.occasion_newsletter_parts(conn, occ)[2] != fingerprint)
    conn.rollback()

    # ------------------------------------------------------------------
    s.section("The night's sheet: who, how many, and what they cannot eat against the menu")
    now = m.datetime.now(m.timezone.utc).isoformat()
    for who, n, note in ((f"{TAG} Vegetarian table", 2, "One of us is vegetarian"),
                         (f"{TAG} Nut allergy", 3, "severe nut allergy"),
                         (f"{TAG} Nothing to say", 2, None)):
        conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
                           party_size, dinner_date, dietary_notes, status, created_at)
                        VALUES (?, ?, ?, 'zmnb@example.invalid', ?, ?, ?, 'confirmed', ?)""",
                     (m.make_restaurant_reference_code(), m.secrets.token_urlsafe(12), who, n,
                      n1.isoformat(), note, now))
    conn.commit()
    sheet = oc.get(f"/admin/dinner-night/{n1.isoformat()}").get_data(as_text=True)
    s.check("the night's covers", "<strong>7</strong> covers booked of 12" in sheet)
    s.check("the vegetarian table: the lamb and the trout, not vegetarian",
            "Plat: " + HOUSE[2][1] + " (not vegetarian)" in sheet and "Entrée: " + HOUSE[1][1] + " (not vegetarian)" in sheet)
    s.check("the nut allergy: the walnut tart", "Dessert: " + HOUSE[3][1] + " (Tree nuts)" in sheet)
    s.check("what each course must make", sheet.count("<td>7</td>") == 4)
    s.check("the sheet's tables can scroll on a phone", sheet.count('<div class="table-wrap">') >= 2)
