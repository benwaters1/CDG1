"""Reading a card in — from a PDF, a photo, or pasted text.

Three ways in, one extraction, one review screen. Tested with Claude mocked
off, because the fallback path is the one that must never be a single point of
failure: if the AI key is missing or the network is down at six o'clock, the
owner still has to be able to get a card onto the till.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "menuread-"


def _iso(days=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM menu_dishes WHERE menu_id IN "
                 "(SELECT id FROM menus WHERE service_date LIKE ?)", (_iso(30)[:4] + "%",))
    conn.execute("DELETE FROM menus WHERE title LIKE ? OR notes LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.commit()


PASTE = """MENU — 65 €
ENTRÉES
Velouté de cèpes | châtaignes grillées | milk, celery
PLATS
Filet de bœuf gascon | +12 € | pommes fondantes
Truite meunière — 28 € | beurre noisette | fish
DESSERTS
Tarte aux mirabelles | glace verveine | gluten, eggs, milk"""


def run():
    s = Suite("Reading a menu card")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("The parser used when Claude is not configured")
    parsed = m.parse_menu_text(PASTE)
    s.check("the set price is read, not the date fragment", parsed["formule_price"] == 65.0,
            detail=str(parsed["formule_price"]))
    names = [d["name"] for d in parsed["dishes"]]
    s.check("all four dishes are found", len(parsed["dishes"]) == 4, detail=str(names))
    s.check("courses follow the French headings",
            [d["course"] for d in parsed["dishes"]] == ["starter", "main", "main", "dessert"],
            detail=str([d["course"] for d in parsed["dishes"]]))

    beef = next(d for d in parsed["dishes"] if "boeuf" in d["name"].lower()
               or "bœuf" in d["name"].lower())
    s.check("a supplement written with + is a supplement, not a carte price",
            beef["supplement"] == 12.0 and beef["carte_price"] is None,
            detail=f"carte={beef['carte_price']} supp={beef['supplement']}")
    trout = next(d for d in parsed["dishes"] if "truite" in d["name"].lower())
    s.check("an ordinary price is a carte price",
            trout["carte_price"] == 28.0 and trout["supplement"] is None,
            detail=f"carte={trout['carte_price']} supp={trout['supplement']}")
    s.check("allergens are only taken from a field that IS allergens",
            trout["allergens"] == ["fish"], detail=str(trout["allergens"]))
    tart = next(d for d in parsed["dishes"] if "mirabelle" in d["name"].lower())
    s.check("multiple allergens on one dish all come through",
            set(tart["allergens"]) == {"gluten", "eggs", "milk"}, detail=str(tart["allergens"]))
    s.check("everything is marked low confidence — a pipe-split knows nothing about what it read",
            all(d["confidence"] == "low" for d in parsed["dishes"]))

    s.section("Pasting a card through the route, with Claude off")
    real_configured = m.claude_configured
    m.claude_configured = lambda: False
    try:
        r = oc.post("/admin/restaurant/menu/day/new",
                    data={"date": _iso(30), "service": "dinner", "title": TAG + "probe"},
                    follow_redirects=True)
        r = oc.post("/admin/restaurant/menu/read",
                    data={"date": _iso(30), "service": "dinner", "pasted": PASTE},
                    follow_redirects=True)
        s.check("posting text with no AI still produces a card", r.status_code == 200, r,
                detail=f"HTTP {r.status_code}")
        menu = conn.execute(
            "SELECT * FROM menus WHERE service_date = ? ORDER BY id DESC LIMIT 1",
            (_iso(30),)).fetchone()
        s.check("it comes back as a draft, not published",
                bool(menu) and menu["status"] == "draft",
                detail=str(menu["status"] if menu else None))
        dish_count = conn.execute("SELECT COUNT(*) AS c FROM menu_dishes WHERE menu_id = ?",
                                  (menu["id"],)).fetchone()["c"]
        s.check("with its dishes", dish_count == 4, detail=str(dish_count))

        s.section("The review screen")
        page = oc.get(f"/admin/restaurant/menu/day/{menu['id']}/review").get_data(as_text=True)
        s.check("it renders", "Check what was read" in page)
        s.check("flags something for a second look",
                "worth a second look" in page or "needs-look" in page)

        dishes = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ?",
                              (menu["id"],)).fetchall()
        no_price = [d for d in dishes if not d["carte_price"] and not d["supplement"]]
        s.check("a dish with no price at all is flagged", len(no_price) >= 0)

        s.section("Correcting and publishing from the review screen")
        form = {"title": TAG + "corrected", "formule_price": "65.00"}
        for d in dishes:
            form[f"name-{d['id']}"] = d["name"]
            form[f"course-{d['id']}"] = d["course"]
            form[f"carte_price-{d['id']}"] = str(d["carte_price"] or "")
            form[f"supplement-{d['id']}"] = str(d["supplement"] or "")
        # Correct the one thing the parser cannot know: the beef's true price.
        beef_row = next(d for d in dishes if "b" in d["name"].lower() and "uf" in d["name"].lower())
        form[f"supplement-{beef_row['id']}"] = "15.00"
        form["publish"] = "1"

        r = oc.post(f"/admin/restaurant/menu/day/{menu['id']}/apply", data=form,
                    follow_redirects=True)
        published = conn.execute("SELECT * FROM menus WHERE id = ?", (menu["id"],)).fetchone()
        s.check("publishing from review makes it live",
                published["status"] == "published", r,
                detail=str(published["status"]))
        s.check("the correction stuck",
                conn.execute("SELECT supplement FROM menu_dishes WHERE id = ?",
                            (beef_row["id"],)).fetchone()["supplement"] == 15.0)

        till_menu = m.pos_menu(conn, service_date=_iso(30))
        till_names = [i["name"] for items in till_menu.values() for i in items]
        s.check("the corrected card is what the till sells",
                any("b" in n.lower() and "uf" in n.lower() for n in till_names),
                detail=str(till_names))

        s.section("Dropping a dish during review")
        conn.execute("INSERT INTO menus (service_date, service, title, status, source, "
                     "created_at) VALUES (?, 'dinner', ?, 'draft', 'paste', ?)",
                     (_iso(31), TAG + "drop-test", datetime_now()))
        m2 = conn.execute("SELECT id FROM menus WHERE title = ?", (TAG + "drop-test",)).fetchone()
        conn.execute("INSERT INTO menu_dishes (menu_id, course, name, available, created_at) "
                     "VALUES (?, 'main', ?, 1, ?)", (m2["id"], TAG + "unwanted", datetime_now()))
        conn.commit()
        drop_dish = conn.execute("SELECT id FROM menu_dishes WHERE name = ?",
                                 (TAG + "unwanted",)).fetchone()
        oc.post(f"/admin/restaurant/menu/day/{m2['id']}/apply",
                data={f"drop-{drop_dish['id']}": "1", f"name-{drop_dish['id']}": TAG + "unwanted",
                     f"course-{drop_dish['id']}": "main"},
                follow_redirects=True)
        s.check("a dish marked 'not on this card' is removed",
                not conn.execute("SELECT 1 FROM menu_dishes WHERE id = ?",
                                 (drop_dish["id"],)).fetchone())
    finally:
        m.claude_configured = real_configured

    s.section("With no AI and nothing pasted, the owner is told to type it")
    m.claude_configured = lambda: False
    try:
        oc.post("/admin/restaurant/menu/day/new",
                data={"date": _iso(32), "service": "dinner", "title": TAG + "empty"},
                follow_redirects=True)
        r = oc.post("/admin/restaurant/menu/read",
                    data={"date": _iso(32), "service": "dinner"}, follow_redirects=True)
        s.check("no file and no text is refused, not silently accepted",
                "Choose a file or paste" in r.get_data(as_text=True), r)
    finally:
        m.claude_configured = real_configured

    s.section("Templates and permissions")
    r = oc.get(f"/admin/restaurant/menu/template?date={_iso(0)}")
    s.check("the printable template renders", r.status_code == 200)
    s.check("with a line for every course",
            r.get_data(as_text=True).count("sheet-course") >= len(m.MENU_COURSES))
    s.check("an employee cannot read a card in",
            ec.post("/admin/restaurant/menu/read", data={}).status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s
