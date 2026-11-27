"""The built menu drawn on the server: a picture for the newsletter, the A4 menu as a PDF (owner, 27 Nov 2026: yes to a
server-side renderer, so the newsletter carries the menu itself).

What this holds:
  - THE FONTS are in the repo, with their licences, and each opens at the weight the menu uses.
  - THE DRAWING: an A4 page at 150 dots an inch, the house's frame, corners and façade, the courses set on it; two
    menus draw differently; a long menu is set smaller until it fits the page, and nothing is ever drawn outside it.
  - THE FILES: a JPEG and an A4 PDF, kept until the menu or the occasion's price changes, then drawn again and the old
    pair removed.
  - THE ADDRESSES: the picture and the PDF answer for a live occasion with a built menu, and not for a draft (to a
    stranger) or an uploaded file; the builder downloads the PDF at the menu's own price.
  - THE NEWSLETTER: the picture is drawn in the letter (its courses in its words, for a mail app showing none), the PDF
    attached, and the courses not listed twice. If the drawing fails, the letter keeps the words and says so.

Nothing reaches the network, the mail or Stripe.
"""
import io
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, free_window, keep_rows
import _harness

m = _harness.m
TAG = "ZMRD"

COURSES = [
    ("L’apéritif", "Crémant de Limoux et gougères au fromage des Pyrénées", "Sparkling Crémant de Limoux, with warm cheese gougères",
     "Crémant de Limoux AOP", "12 cl", "Fromage des Pyrénées", "Pyrenean cheese", "France", "", "milk,gluten"),
    ("Entrée", "Truite de l’Ariège fumée au château, crème au raifort, radis", "Ariège trout smoked at the château",
     "Blanc · Côtes de Gascogne IGP", "12 cl", "Truite élevée en Ariège", "Trout raised in the Ariège", "France", "", "fish"),
    ("Plat", "Agneau des Pyrénées rôti au thym, pommes fondantes, petits pois", "Pyrenean lamb roasted with thyme",
     "Rouge · Madiran AOP", "12 cl", "Agneau", "Lamb", "France", "agneau", "sulphites"),
    ("Dessert", "Tarte aux fraises, crème légère à la vanille", "Strawberry tart with a light vanilla cream",
     "Moelleux · Jurançon AOP", "8 cl", "Fraises", "Strawberries", "France", "", "gluten,eggs,milk"),
]


def _view(courses=COURSES, price=95):
    menu = {"name": f"{TAG} menu", "price": price, "wine_included": 1, "service_time": "19:30"}
    cs = [m._course_view({"course_fr": k, "dish_fr": fr, "dish_en": en, "wine_fr": w, "wine_glass": g, "origin_fr": ofr,
                          "origin_en": oen, "country_fr": cfr, "country_en": m.country_english(cfr), "meat": meat,
                          "allergens": al, "fait_maison": 1})
          for k, fr, en, w, g, ofr, oen, cfr, meat, al in courses]
    return m.dinner_menu_view_of(menu, cs, nights=["2027-08-18"])


def _ink(img, box):
    """How many pixels in the box are dark enough to be ink, not paper or the tufted wall."""
    crop = img.crop(box).convert("L")
    # The menu's brown (#60441D) is about 72 in grey; anti-aliased type and the drawing's fine lines land up to
    # about 160, the tufted wall and the paper above 200.
    pixels = getattr(crop, "get_flattened_data", crop.getdata)()
    return sum(1 for v in pixels if v < 160)


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%'")]
    marks = ",".join("?" * len(ids)) or "NULL"
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasions WHERE id IN ({marks})", ids)
    conn.execute(f"DELETE FROM dinner_menus WHERE name LIKE '{TAG}%'")
    conn.commit()
    for occ_id in ids:
        for name in os.listdir(m.OCCASION_FILES_DIR) if os.path.isdir(m.OCCASION_FILES_DIR) else []:
            if name.startswith(f"menu-drawn-{occ_id}-"):
                m.remove_occasion_file(name)


def run():
    s = Suite("The built menu drawn on the server: a picture and a PDF, for the newsletter")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasions", "status = 'live'"),
                keep_rows(conn, "restaurant_settings", "id = 1")]
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.commit()
    _cleanup(conn)
    was = m.render_dinner_menu
    try:
        _run(s, oc, conn)
    finally:
        m.render_dinner_menu = was
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, conn):
    from PIL import Image, ImageFont
    anon = m.app.test_client()
    fonts = os.path.join(_harness.ROOT, "static", "fonts", "menu")

    # ------------------------------------------------------------------
    s.section("The fonts are in the repo, with their licences")
    for key, name in m.MENU_FONT_FILES.items():
        path = os.path.join(fonts, name)
        ok = os.path.exists(path)
        family = ImageFont.truetype(path, 20).getname()[0] if ok else None
        s.check(f"{name} opens", ok and family in ("EB Garamond", "Cormorant Garamond", "Pinyon Script"), detail=family)
    s.check("the SIL Open Font License is beside them, once for each family",
            all(os.path.exists(os.path.join(fonts, f"OFL-{n}.txt")) for n in ("EBGaramond", "CormorantGaramond", "PinyonScript")))
    s.check("Cormorant is set Light and EB Garamond Medium where the menu asks",
            m._menu_font("corm", 30, "Light").getname()[1] == "Light"
            and m._menu_font("ebg", 31, "Medium").getname()[1] in ("Medium", "Regular"))

    # ------------------------------------------------------------------
    s.section("The drawing: an A4 page, the house's frame and façade, the courses on it")
    with m.app.test_request_context("/"):
        img = m.render_dinner_menu(_view(), dpi=150)
    W, H = img.size
    s.check("A4 at 150 dots an inch: 1240 by 1754", (W, H) == (1240, 1754), detail=img.size)
    mm = 150 / 25.4
    s.check("the frame's rule down the left", _ink(img, (int(6 * mm), int(60 * mm), int(8.5 * mm), int(200 * mm))) > 1000)
    s.check("a corner ornament in each corner", all(_ink(img, box) > 200 for box in (
        (int(4 * mm), int(4 * mm), int(23 * mm), int(23 * mm)), (W - int(23 * mm), int(4 * mm), W - int(4 * mm), int(23 * mm)),
        (int(4 * mm), H - int(23 * mm), int(23 * mm), H - int(4 * mm)), (W - int(23 * mm), H - int(23 * mm), W - int(4 * mm), H - int(4 * mm)))))
    s.check("the façade under the dates", _ink(img, (int(80 * mm), int(22 * mm), int(130 * mm), int(45 * mm))) > 1500)
    s.check("the courses set down the page", _ink(img, (int(30 * mm), int(90 * mm), W - int(30 * mm), int(200 * mm))) > 6000)
    with m.app.test_request_context("/"):
        other = m.render_dinner_menu(_view(price=120), dpi=150)
    s.check("a different price draws a different page", list(other.crop((0, 0, W, 600)).getdata())
            != list(img.crop((0, 0, W, 600)).getdata()))

    long_menu = [(k, fr + " et encore un peu plus, pour voir", en, w, g, ofr, oen, cfr, meat, al)
                 for _i in range(2) for k, fr, en, w, g, ofr, oen, cfr, meat, al in COURSES]
    with m.app.test_request_context("/"):
        longer = m.render_dinner_menu(_view(long_menu), dpi=150)
    s.check("eight long courses are set smaller and still fit the A4 page", longer.size == (1240, 1754), detail=longer.size)
    band = int(17 * mm)
    s.check("and nothing is drawn below the frame's foot but the corners",
            _ink(longer, (int(30 * mm), H - band + int(3 * mm), W - int(30 * mm), H - int(9 * mm))) < 200,
            detail=_ink(longer, (int(30 * mm), H - band + int(3 * mm), W - int(30 * mm), H - int(9 * mm))))

    # ------------------------------------------------------------------
    s.section("The files: a picture and an A4 PDF, kept until the menu or the price changes")
    room = ensure_room(min_occupancy=2)
    first = free_window(room["id"], 2, after_days=320, clear_of_ateliers=True, clear_of_rate_overrides=True)
    N0 = first.isoformat()
    mid = conn.execute("INSERT INTO dinner_menus (name, price, wine_included, created_at, updated_at) "
                       "VALUES (?, 95, 1, ?, ?)", (f"{TAG} Autumn menu", _harness.datetime_now(), "2026-11-27T10:00:00")).lastrowid
    for i, (k, fr, en, w, g, ofr, oen, cfr, meat, al) in enumerate(COURSES):
        conn.execute("""INSERT INTO dinner_menu_courses (menu_id, position, course_fr, course_en, dish_fr, dish_en, wine_fr,
                            wine_glass, origin_fr, origin_en, country_fr, country_en, meat, allergens, fait_maison)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                     (mid, i, k, dict(m.MENU_DEFAULT_COURSES).get(k, ""), fr, en, w, g, ofr, oen, cfr, "France", meat, al))
    conn.commit()
    name = f"{TAG} One night with dinner"
    form = {"name": name, "first_night": N0, "last_night": N0, "kind": "stay", "chef_on_site": "1", "status": "live",
            "photo": "", "dinner_section_shown": "1", "serve_dinner": "1", "home_banner": "1",
            "description": "A night in the valley, with breakfast in the morning.", "dinner_price": "95",
            "dinner_covers": "12", "dinner_time": "19:30", "wine_included": "1", "wine_share": "30",
            "dinner_nights_shown": "1", "menu_choice_shown": "1", "dinner_menu_id": str(mid), f"dinner_{N0}": "1"}
    oc.post("/admin/stay-occasions/new", data=dict(form, status="draft"))
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=form)
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("an occasion serving the built menu, live", occ["status"] == "live" and occ["dinner_menu_id"] == mid)
    with m.app.test_request_context("/"):
        drawn = m.dinner_menu_rendering(conn, occ)
    s.check("drawn: a JPEG and a PDF beside the occasion's files", drawn and all(os.path.exists(p) for p in drawn)
            and drawn[0].endswith(".jpg") and drawn[1].endswith(".pdf"), detail=drawn)
    if not drawn:
        return
    pdf = open(drawn[1], "rb").read()
    box = re.search(rb"/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", pdf)
    s.check("the PDF is an A4 page", pdf.startswith(b"%PDF") and box
            and abs(float(box.group(1)) - 595.3) < 2 and abs(float(box.group(2)) - 841.9) < 2,
            detail=box.group(0) if box else pdf[:40])
    s.check("the picture is light enough for a letter", os.path.getsize(drawn[0]) < 900_000,
            detail=os.path.getsize(drawn[0]))
    mtime = os.path.getmtime(drawn[0])
    with m.app.test_request_context("/"):
        again = m.dinner_menu_rendering(conn, occ)
    s.check("asked again, the same files, not drawn twice", again == drawn and os.path.getmtime(drawn[0]) == mtime)
    conn.execute("UPDATE dinner_menus SET updated_at = '2026-11-27T11:00:00' WHERE id = ?", (mid,))
    conn.commit()
    with m.app.test_request_context("/"):
        redrawn = m.dinner_menu_rendering(conn, occ)
    s.check("the menu changed: drawn again", redrawn and redrawn != drawn and all(os.path.exists(p) for p in redrawn))
    s.check("and the old pair removed", not any(os.path.exists(p) for p in drawn))
    drawn = redrawn

    # ------------------------------------------------------------------
    s.section("The addresses: the picture and the PDF for the guest, the PDF for the builder")
    slug = occ["slug"]
    r = anon.get(f"/whats-on/at-gudanes/{slug}/menu.jpg")
    s.check("the picture answers", r.status_code == 200 and r.mimetype == "image/jpeg"
            and Image.open(io.BytesIO(r.data)).size == (1240, 1754), detail=r.status_code)
    r = anon.get(f"/whats-on/at-gudanes/{slug}/menu.pdf?download=1")
    s.check("the PDF answers, to download", r.status_code == 200 and r.mimetype == "application/pdf"
            and r.data.startswith(b"%PDF") and "attachment" in (r.headers.get("Content-Disposition") or ""))
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE id = ?", (occ["id"],))
    conn.commit()
    s.check("a draft's is not on the site", anon.get(f"/whats-on/at-gudanes/{slug}/menu.jpg").status_code == 404)
    s.check("but the owner may look", oc.get(f"/whats-on/at-gudanes/{slug}/menu.jpg").status_code == 200)
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE id = ?", (occ["id"],))
    conn.commit()
    r = oc.get(f"/admin/menus/{mid}/menu.pdf")
    s.check("the builder downloads the PDF", r.status_code == 200 and r.mimetype == "application/pdf"
            and r.data.startswith(b"%PDF") and f"{TAG} Autumn menu.pdf" in (r.headers.get("Content-Disposition") or ""))
    s.check("and offers it on the menu and the list", "/menu.pdf" in oc.get(f"/admin/menus/{mid}").get_data(as_text=True)
            and f"/admin/menus/{mid}/menu.pdf" in oc.get("/admin/menus").get_data(as_text=True))

    # ------------------------------------------------------------------
    s.section("The newsletter: the picture in the letter, the PDF attached, the courses not listed twice")
    with m.app.test_request_context("/"):
        pics = m.occasion_newsletter_pictures(conn, occ)
        subject, body, _fp, _b = m.occasion_newsletter_parts(conn, occ)
    menu_pic = next((p for p in pics if p["src"].endswith(f"/whats-on/at-gudanes/{slug}/menu.jpg")), None)
    s.check("the drawn menu is one of the letter's pictures, at the public address", menu_pic is not None,
            detail=[p["src"] for p in pics])
    s.check("its words name the courses, for a mail app showing no pictures",
            menu_pic and "Main course: Pyrenean lamb roasted with thyme" in menu_pic["alt"], detail=menu_pic)
    s.check("the letter draws its pictures where {{menu_pictures}} stands", "{{menu_pictures}}" in body)
    s.check("and does not list the courses a second time", "On the menu:" not in body)
    tmpl = conn.execute("SELECT id FROM campaign_templates WHERE occasion_id = ?", (occ["id"],)).fetchone()
    s.check("the occasion's newsletter was drafted", tmpl is not None)
    if tmpl:
        with m.app.test_request_context("/"):
            files, note = m.campaign_attachments(conn, tmpl["id"])
        s.check("the PDF is attached to it", len(files) == 1 and files[0]["mime"] == "application/pdf"
                and files[0]["filename"] == f"{name} - menu.pdf" and os.path.exists(files[0]["path"]), detail=files)
        s.check("and the page says so", "attached as a PDF" in note, detail=note)

    # If the drawing fails, the letter keeps the words and says why.
    def _fails(view, dpi=150):
        raise OSError("no fonts here")
    m.render_dinner_menu = _fails
    for name_ in os.listdir(m.OCCASION_FILES_DIR):
        if name_.startswith(f"menu-drawn-{occ['id']}-"):
            m.remove_occasion_file(name_)
    with m.app.test_request_context("/"):
        pics = m.occasion_newsletter_pictures(conn, occ)
        _s, body, _fp, _b = m.occasion_newsletter_parts(conn, occ)
        files, note = m.campaign_attachments(conn, tmpl["id"]) if tmpl else ([], "")
    s.check("drawing failed: no picture that could not be made", not any("/menu.jpg" in p["src"] for p in pics))
    s.check("the courses are listed in words instead", "On the menu:" in body and "Main course: Pyrenean lamb" in body)
    s.check("nothing attached, and the page says why", files == [] and "could not be drawn" in note, detail=note)
    s.check("and the picture's address answers 404 rather than a broken image",
            anon.get(f"/whats-on/at-gudanes/{slug}/menu.jpg").status_code == 404)
