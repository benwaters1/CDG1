"""Dated menus: build tonight's card, publish it, sell from it.

`menu_items` was one flat list with no date, so "what did we serve on the 10th"
had no answer and editing tonight's card destroyed last night's. None of the
commercial systems researched does this — Square, Lightspeed and Toast all
assume one live menu you edit each day — so the behaviour worth testing hardest
is the part they don't have: that yesterday survives today.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "menuday-"


def _iso(days=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM menu_dishes WHERE menu_id IN "
                 "(SELECT id FROM menus WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Dated menus")
    oc, ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    _cleanup(conn)

    s.section("Building tonight's card")
    r = oc.post("/admin/restaurant/menu/day/new",
                data={"date": _iso(0), "service": "dinner", "title": TAG + "Tonight",
                      "formule_label": "Menu Dégustation", "formule_price": "65"},
                follow_redirects=True)
    menu = conn.execute("SELECT * FROM menus WHERE title = ?", (TAG + "Tonight",)).fetchone()
    s.check("a card is created for the date", bool(menu), r)
    s.check("as a draft, not live", bool(menu) and menu["status"] == "draft",
            detail=str(menu["status"] if menu else None))
    s.check("carrying the set price", bool(menu) and menu["formule_price"] == 65.0)

    # A draft must not reach the floor. Half a card is worse than last night's.
    s.check("a draft is not what the till sees",
            m.menu_for_date(conn, _iso(0)) is None)

    for name, course, price, allerg in [
            (TAG + "Velouté", "starter", 14.0, "milk"),
            (TAG + "Truite", "main", 28.0, "fish"),
            (TAG + "Tarte", "dessert", 11.0, "gluten,eggs")]:
        oc.post(f"/admin/restaurant/menu/day/{menu['id']}/dish",
                data={"name": name, "course": course, "carte_price": str(price),
                      "allergens": allerg.split(",")}, follow_redirects=True)
    dishes = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ? ORDER BY sort_order",
                          (menu["id"],)).fetchall()
    s.check("dishes go on the card", len(dishes) == 3, detail=str(len(dishes)))
    s.check("each keeps its course",
            [d["course"] for d in dishes] == ["starter", "main", "dessert"],
            detail=str([d["course"] for d in dishes]))
    s.check("and its allergens",
            any("fish" in (d["allergens"] or "") for d in dishes))

    s.section("Publishing puts it on the till")
    r = oc.post(f"/admin/restaurant/menu/day/{menu['id']}/publish", follow_redirects=True)
    live = m.menu_for_date(conn, _iso(0))
    s.check("the card goes live", bool(live) and live["id"] == menu["id"], r)
    s.check("and is stamped with when", bool(live) and bool(live["published_at"]))

    till_menu = m.pos_menu(conn, service_date=_iso(0))
    names = [i["name"] for items in till_menu.values() for i in items]
    s.check("tonight's dishes are sellable", TAG + "Truite" in names,
            detail=str([n for n in names if n.startswith(TAG)]))

    s.section("Yesterday survives today — the thing no commercial POS does")
    oc.post(f"/admin/restaurant/menu/day/{menu['id']}/copy",
            data={"to": _iso(1)}, follow_redirects=True)
    tomorrow = conn.execute(
        "SELECT * FROM menus WHERE service_date = ? AND status = 'draft'", (_iso(1),)).fetchone()
    s.check("a card can be copied forward", bool(tomorrow))
    s.check("as a draft, so it can be changed before service",
            bool(tomorrow) and tomorrow["status"] == "draft")
    s.check("carrying the dishes",
            conn.execute("SELECT COUNT(*) AS c FROM menu_dishes WHERE menu_id = ?",
                         (tomorrow["id"],)).fetchone()["c"] == 3)

    # Change tomorrow, publish it, and today must be untouched.
    conn.execute("DELETE FROM menu_dishes WHERE menu_id = ? AND course = 'main'",
                 (tomorrow["id"],))
    conn.commit()
    oc.post(f"/admin/restaurant/menu/day/{tomorrow['id']}/dish",
            data={"name": TAG + "Pigeon", "course": "main", "carte_price": "34"},
            follow_redirects=True)
    oc.post(f"/admin/restaurant/menu/day/{tomorrow['id']}/publish", follow_redirects=True)

    today_names = [d["name"] for ds in m.menu_dishes_for(conn, menu["id"]).values() for d in ds]
    tomorrow_names = [d["name"] for ds in m.menu_dishes_for(conn, tomorrow["id"]).values()
                      for d in ds]
    s.check("tomorrow has the new dish", TAG + "Pigeon" in tomorrow_names,
            detail=str(tomorrow_names))
    s.check("and today still has the old one", TAG + "Truite" in today_names,
            detail=str(today_names))
    s.check("today is untouched by tomorrow", TAG + "Pigeon" not in today_names)

    s.section("A published card cannot be quietly rewritten")
    r = oc.post(f"/admin/restaurant/menu/day/{menu['id']}/edit",
                data={"title": TAG + "Rewritten", "formule_price": "999"},
                follow_redirects=True)
    unchanged = conn.execute("SELECT * FROM menus WHERE id = ?", (menu["id"],)).fetchone()
    s.check("editing a published card is refused",
            unchanged["formule_price"] == 65.0, r, detail=str(unchanged["formule_price"]))
    s.check("and it says to copy it instead", "copy it" in r.get_data(as_text=True).lower())

    s.section("86ing tonight leaves tomorrow alone")
    truite = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ? AND name = ?",
                          (menu["id"], TAG + "Truite")).fetchone()
    r = oc.post(f"/admin/restaurant/menu/dish/{truite['id']}/availability",
                data={"note": "ran out"}, follow_redirects=True)
    s.check("a dish can be taken off tonight",
            conn.execute("SELECT available FROM menu_dishes WHERE id = ?",
                         (truite["id"],)).fetchone()["available"] == 0, r)
    s.check("with the reason kept",
            conn.execute("SELECT unavailable_note FROM menu_dishes WHERE id = ?",
                         (truite["id"],)).fetchone()["unavailable_note"] == "ran out")
    # The old flat menu could not do this: 86ing removed the dish everywhere,
    # including from cards not yet served.
    tomorrow_truite = conn.execute(
        "SELECT available FROM menu_dishes WHERE menu_id = ? AND name = ?",
        (tomorrow["id"], TAG + "Velouté")).fetchone()
    s.check("tomorrow's card is unaffected",
            bool(tomorrow_truite) and tomorrow_truite["available"] == 1)
    # Staff, not owner-only — it is the chef who knows the sole has run out.
    conn.execute("UPDATE menu_dishes SET available = 1 WHERE id = ?", (truite["id"],))
    conn.commit()
    r = ec.post(f"/admin/restaurant/menu/dish/{truite['id']}/availability",
                follow_redirects=True)
    s.check("staff can 86 a dish, not just the owner",
            conn.execute("SELECT available FROM menu_dishes WHERE id = ?",
                         (truite["id"],)).fetchone()["available"] == 0, r)
    # ...and must not be dumped on a 403 for doing it: the card builder is
    # owner-only, so staff go back to the floor instead.
    s.check("and are not sent to a page they cannot open", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.section("Selling from the card")
    conn.execute("UPDATE menu_dishes SET available = 1 WHERE id = ?", (truite["id"],))
    conn.commit()
    oc.post("/pos/open", data={"table_label": TAG + "3", "covers": "2"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "3",)).fetchone()
    s.check("a tab records which night it belongs to",
            order["service_date"] == _iso(0), detail=str(order["service_date"]))

    oc.post(f"/pos/{order['id']}/add", data={"menu_dish_id": truite["id"], "seat_number": "1"},
            follow_redirects=True)
    line = conn.execute("SELECT * FROM pos_order_lines WHERE order_id = ? ORDER BY id DESC LIMIT 1",
                        (order["id"],)).fetchone()
    s.check("the dish sells", bool(line) and line["name"] == TAG + "Truite",
            detail=str(line["name"] if line else None))
    # The pointer is what keeps "what did table 3 eat on the 10th" answerable
    # after tomorrow's card replaces tonight's.
    s.check("the line points back at the exact dish on that night's card",
            bool(line) and line["menu_dish_id"] == truite["id"])
    s.check("priced from the card", bool(line) and line["unit_price"] == 28.0)
    s.check("and taxed at the food rate", bool(line) and line["vat_rate"] == 10.0,
            detail=str(line["vat_rate"] if line else None))

    off = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ? AND name = ?",
                       (menu["id"], TAG + "Tarte")).fetchone()
    conn.execute("UPDATE menu_dishes SET available = 0 WHERE id = ?", (off["id"],))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) AS c FROM pos_order_lines WHERE order_id = ?",
                          (order["id"],)).fetchone()["c"]
    oc.post(f"/pos/{order['id']}/add", data={"menu_dish_id": off["id"]}, follow_redirects=True)
    s.check("a dish that is off cannot be sold",
            conn.execute("SELECT COUNT(*) AS c FROM pos_order_lines WHERE order_id = ?",
                         (order["id"],)).fetchone()["c"] == before)

    s.section("Wines and drinks carry over without being re-entered")
    conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, always_available, sort_order, created_at)
           VALUES (?, 'drink', 'wine', 42.0, 1, 1, 1, 1, 0, ?)""", (TAG + "Madiran", now))
    conn.commit()
    till_menu = m.pos_menu(conn, service_date=_iso(0))
    names = [i["name"] for items in till_menu.values() for i in items]
    s.check("the standing list appears under tonight's card", TAG + "Madiran" in names,
            detail=str([n for n in names if n.startswith(TAG)]))
    s.check("alongside the food", TAG + "Truite" in names)

    s.section("The pages render")
    s.check("the day builder", oc.get(f"/admin/restaurant/menu/day?date={_iso(0)}").status_code == 200)
    printed = oc.get(f"/admin/restaurant/menu/day/{menu['id']}/print")
    s.check("the printable card", printed.status_code == 200)
    page = printed.get_data(as_text=True)
    s.check("showing the dishes", TAG + "Truite" in page)
    # arrêté du 27 mars 1987 — prices service compris, and whether drink is in.
    s.check("with the mentions French law requires",
            "service compris" in page and "Boisson" in page)
    s.check("a dish taken off is not printed", TAG + "Tarte" not in page)
    s.check("an employee cannot build the card",
            ec.get(f"/admin/restaurant/menu/day?date={_iso(0)}").status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s
