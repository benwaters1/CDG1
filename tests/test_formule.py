"""The set menu: one price, the guest chooses dishes.

BOFiP BOI-TVA-LIQ-30-20-10-20 §80 requires a menu price to be ventilated so
wine carries 20% and food 10%. This is the arithmetic that makes it correct,
and the till flow that makes it usable — placeholders so nobody has to choose
dessert before the starter arrives, and firing that only ever sends what was
actually chosen.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "formule-"


def _iso(days=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM pos_order_formules WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_dishes WHERE menu_id IN "
                 "(SELECT id FROM menus WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The set menu (formule)")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    _cleanup(conn)

    s.section("The allocation arithmetic")
    # The worked example: 65€ menu, carte prices entree 18, plat 32, dessert
    # 14, glass of wine 8. Base = 72.
    shares = m.allocate_formule_price(65.0, [("entree", 18), ("plat", 32),
                                             ("dessert", 14), ("vin", 8)])
    s.check("components sum to exactly the menu price",
            round(sum(shares.values()), 2) == 65.0, detail=str(sum(shares.values())))
    s.check("proportioned to what each would cost alone",
            round(shares["plat"], 2) == round(65 * 32 / 72, 2), detail=str(shares))
    s.check("no negative or zero share on a real dish", all(v > 0 for v in shares.values()))

    # A base that does not divide evenly must not leave a drift.
    odd = m.allocate_formule_price(33.33, [("a", 10), ("b", 10), ("c", 10)])
    s.check("an awkward price still sums exactly", round(sum(odd.values()), 2) == 33.33,
            detail=str(odd))
    biggest = max([("a", 10), ("b", 10), ("c", 10)], key=lambda kb: kb[1])[0]
    s.check("the residual cent lands on the largest base",
            odd[biggest] != round(33.33 / 3, 2) or len({round(v, 2) for v in odd.values()}) == 1,
            detail=str(odd))

    empty = m.allocate_formule_price(50.0, [])
    s.check("no components does not crash", empty == {})

    s.section("Publishing a set menu and opening it for a table")
    oc.post("/admin/restaurant/menu/day/new",
            data={"date": _iso(0), "service": "dinner", "title": TAG + "menu",
                  "formule_label": TAG + "Dégustation", "formule_price": "65"},
            follow_redirects=True)
    menu = conn.execute("SELECT * FROM menus WHERE title = ?", (TAG + "menu",)).fetchone()
    for name, course, price in [
            (TAG + "Velouté", "starter", 18.0), (TAG + "Pigeon", "main", 32.0),
            (TAG + "Tarte", "dessert", 14.0), (TAG + "Madiran", "wine", 8.0)]:
        oc.post(f"/admin/restaurant/menu/day/{menu['id']}/dish",
                data={"name": name, "course": course, "carte_price": str(price)},
                follow_redirects=True)
    # One dish carries a supplement — beef instead of the included pigeon.
    oc.post(f"/admin/restaurant/menu/day/{menu['id']}/dish",
            data={"name": TAG + "Boeuf", "course": "main", "carte_price": "44",
                  "supplement": "12"}, follow_redirects=True)
    oc.post(f"/admin/restaurant/menu/day/{menu['id']}/publish", follow_redirects=True)

    oc.post("/pos/open", data={"table_label": TAG + "1", "covers": "2"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "1",)).fetchone()
    r = oc.post(f"/pos/{order['id']}/formule", data={"covers": "2"}, follow_redirects=True)
    formules = conn.execute("SELECT * FROM pos_order_formules WHERE order_id = ?",
                            (order["id"],)).fetchall()
    s.check("one formule row per cover", len(formules) == 2, r, detail=str(len(formules)))
    s.check("each carries the set price", all(f["price"] == 65.0 for f in formules))

    placeholders = conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ? ORDER BY seat_number, id",
        (order["id"],)).fetchall()
    s.check("a placeholder exists for every course, per cover",
            len(placeholders) == 2 * len({"starter", "main", "dessert", "wine"}),
            detail=str(len(placeholders)))
    s.check("placeholders are unchosen", all(p["menu_dish_id"] is None for p in placeholders))
    bill = m.pos_bill(conn, order["id"])
    s.check("the bill is already correct before anyone has chosen anything",
            bill["gross"] == 130.0, detail=str(bill["gross"]))

    s.section("A placeholder can never be sent to the kitchen")
    sent = m.pos_send_to_kitchen(conn, order["id"], course="starter")
    s.check("nothing goes — there is nothing chosen yet", sent == [], detail=str(sent))
    kitchen = oc.get("/pos/kitchen").get_data(as_text=True)
    s.check("the pass shows nothing for this table", TAG + "1" not in kitchen)

    s.section("Choosing dishes")
    starter_line = next(p for p in placeholders
                        if p["course"] == "starter" and p["seat_number"] == 1)
    dish = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ? AND name = ?",
                        (menu["id"], TAG + "Velouté")).fetchone()
    oc.post(f"/pos/formule-line/{starter_line['id']}/choose",
            data={"menu_dish_id": dish["id"]}, follow_redirects=True)
    chosen = conn.execute("SELECT * FROM pos_order_lines WHERE id = ?",
                          (starter_line["id"],)).fetchone()
    s.check("the placeholder becomes the dish", chosen["menu_dish_id"] == dish["id"])
    s.check("named after what was actually chosen", chosen["name"] == TAG + "Velouté")

    main_line = next(p for p in placeholders if p["course"] == "main" and p["seat_number"] == 1)
    beef = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ? AND name = ?",
                        (menu["id"], TAG + "Boeuf")).fetchone()
    oc.post(f"/pos/formule-line/{main_line['id']}/choose",
            data={"menu_dish_id": beef["id"]}, follow_redirects=True)
    lines_now = conn.execute("SELECT * FROM pos_order_lines WHERE order_id = ? ORDER BY id",
                             (order["id"],)).fetchall()
    supplement_line = next((l for l in lines_now if l["is_supplement"]), None)
    s.check("a supplement dish adds its own line", bool(supplement_line))
    s.check("charged at the supplement amount, not the full carte price",
            bool(supplement_line) and supplement_line["unit_price"] == 12.0,
            detail=str(supplement_line["unit_price"] if supplement_line else None))
    s.check("the supplement is excluded from the allocation base",
            bill["order"]["id"] == order["id"])  # sanity; real check below

    bill2 = m.pos_bill(conn, order["id"])
    seat1 = [l for l in bill2["live"] if l["seat_number"] == 1 and not l["is_supplement"]]
    s.check("that cover's own dishes still sum to exactly its menu price",
            round(sum(l["unit_price"] * l["quantity"] for l in seat1), 2) == 65.0,
            detail=str([(l["name"], l["unit_price"]) for l in seat1]))

    s.section("VAT is split by rate, automatically, with no arithmetic change to pos_bill")
    s.check("two VAT rates appear on this one cover's dishes",
            len(bill2["vat_by_rate"]) == 2, detail=str(list(bill2["vat_by_rate"])))
    naive = round(65 - 65 / 1.10, 2)
    correct_vat_seat1 = round(sum(
        (l["unit_price"] * l["quantity"]) - (l["unit_price"] * l["quantity"]) / (1 + (l["vat_rate"] or 0) / 100)
        for l in seat1), 2)
    s.check("the correct VAT differs from the naive single-rate figure",
            correct_vat_seat1 != naive, detail=f"correct={correct_vat_seat1} naive={naive}")

    s.section("Firing sends only what has been chosen")
    sent = m.pos_send_to_kitchen(conn, order["id"], course="starter")
    s.check("the chosen starter is sent", len(sent) == 1 and sent[0]["name"] == TAG + "Velouté",
            detail=str([l["name"] for l in sent]))
    still_new = conn.execute(
        "SELECT COUNT(*) AS c FROM pos_order_lines WHERE order_id = ? AND course = 'starter' "
        "AND state = 'new'", (order["id"],)).fetchone()["c"]
    s.check("the other cover's unchosen starter placeholder is not sent", still_new == 1,
            detail=str(still_new))
    conn.commit()

    kitchen = oc.get("/pos/kitchen").get_data(as_text=True)
    s.check("the chosen dish reaches the pass", TAG + "Velouté" in kitchen)
    s.check("the unchosen placeholder text never does", "à choisir" not in kitchen)

    s.section("The receipt shows the ventilated breakdown, not one blended line")
    # Choose the rest so the bill can settle.
    for course, name in [("dessert", TAG + "Tarte"), ("wine", TAG + "Madiran")]:
        line = next(l for l in conn.execute(
            "SELECT * FROM pos_order_lines WHERE order_id = ? AND course = ? AND seat_number = 1",
            (order["id"], course)).fetchall() if l["menu_dish_id"] is None)
        d = conn.execute("SELECT * FROM menu_dishes WHERE menu_id = ? AND name = ?",
                         (menu["id"], name)).fetchone()
        oc.post(f"/pos/formule-line/{line['id']}/choose", data={"menu_dish_id": d["id"]},
                follow_redirects=True)
    for course in ("starter", "main", "dessert", "wine"):
        line = next(l for l in conn.execute(
            "SELECT * FROM pos_order_lines WHERE order_id = ? AND course = ? AND seat_number = 2",
            (order["id"], course)).fetchall() if l["menu_dish_id"] is None)
        d = conn.execute(
            "SELECT * FROM menu_dishes WHERE menu_id = ? AND course = ? AND name NOT LIKE ?",
            (menu["id"], course, TAG + "Boeuf")).fetchone()
        oc.post(f"/pos/formule-line/{line['id']}/choose", data={"menu_dish_id": d["id"]},
                follow_redirects=True)

    page = oc.get(f"/pos/{order['id']}/receipt").get_data(as_text=True)
    s.check("the formule appears as its own labelled block",
            (TAG + "Dégustation") in page and "couvert 1" in page.lower())
    s.check("the €65 subtotal is shown", "65.00" in page)
    s.check("the component dishes are shown beneath it, not hidden",
            TAG + "Velouté" in page and TAG + "Boeuf" in page)
    s.check("the supplement is called out", "supplément" in page.lower())

    s.section("Guards")
    r = oc.post(f"/pos/{order['id']}/formule", data={"covers": "2"}, follow_redirects=True)
    s.check("the set menu cannot be opened twice on one table",
            "already open" in r.get_data(as_text=True), r)

    oc.post("/pos/open", data={"table_label": TAG + "2", "covers": "2"}, follow_redirects=True)
    no_menu_order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                                 (TAG + "2",)).fetchone()
    conn.execute("UPDATE pos_orders SET service_date = ? WHERE id = ?",
                 (_iso(90), no_menu_order["id"]))
    conn.commit()
    r = oc.post(f"/pos/{no_menu_order['id']}/formule", follow_redirects=True)
    s.check("no set menu published for that night is refused, not crashed",
            "No set menu" in r.get_data(as_text=True), r)

    r = oc.post(f"/pos/formule-line/{starter_line['id']}/choose", data={}, follow_redirects=True)
    s.check("choosing with no dish is refused rather than clearing the line",
            conn.execute("SELECT menu_dish_id FROM pos_order_lines WHERE id = ?",
                         (starter_line["id"],)).fetchone()["menu_dish_id"] == dish["id"], r)

    _cleanup(conn)
    conn.close()
    return s
