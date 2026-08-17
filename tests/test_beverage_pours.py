"""Glass, carafe, bottle — one wine, one stock, several pours.

Forty wines sold two ways was eighty menu_items rows, eighty prices to keep in
step, and a hand-typed depletion fraction on each that silently stopped
matching its bottle the moment either changed. The wine is now the parent and
holds the stock link; each pour holds only its own size and price, and what it
takes out of the cellar is computed.
"""
from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "pour-"


def _cleanup(conn):
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Beverage pours")
    oc, ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    _cleanup(conn)

    s.section("The arithmetic")
    # A 150ml glass off a 750ml bottle is 0.20 of it only if nothing is ever
    # lost. The trade loses 15-20% to tasting, spillage and corked bottles.
    s.check("with no loss a 150ml glass is a fifth of a 750ml bottle",
            m.pour_stock_qty(150, 750, 0) == 0.2, detail=str(m.pour_stock_qty(150, 750, 0)))
    s.check("with 15% loss it is closer to 0.235",
            abs(m.pour_stock_qty(150, 750, 15) - 0.2353) < 0.001,
            detail=str(m.pour_stock_qty(150, 750, 15)))
    s.check("a whole bottle is one bottle", m.pour_stock_qty(750, 750, 0) == 1.0)
    s.check("a half bottle is a half", m.pour_stock_qty(375, 750, 0) == 0.5)
    # Missing figures must not produce a zero-depletion pour that sells stock
    # for free forever.
    s.check("no volume falls back to a whole unit rather than nothing",
            m.pour_stock_qty(None, 750, 0) == 1.0)
    s.check("no bottle size does the same", m.pour_stock_qty(150, None, 0) == 1.0)
    s.check("an absurd loss cannot divide by zero", m.pour_stock_qty(150, 750, 100) > 0)

    s.section("Adding pours to a wine")
    cur = conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level, unit_cost,
           active, created_at) VALUES (?, 'drinks', 'bottle', 6, 18.0, 1, ?)""",
        (TAG + "Madiran stock", now))
    stock_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, always_available, stock_item_id, stock_qty_per_unit,
           container_ml, pour_loss_percent, sort_order, created_at)
           VALUES (?, 'drink', 'wine', 46.0, 1, 1, 1, 1, ?, 1, 750, 15, 0, ?)""",
        (TAG + "Madiran", stock_id, now))
    wine_id = cur.lastrowid
    conn.commit()

    r = oc.post(f"/admin/restaurant/menu/{wine_id}/pour",
                data={"serve_size": "glass", "serve_volume_ml": "150", "price": "9"},
                follow_redirects=True)
    glass = conn.execute("SELECT * FROM menu_items WHERE parent_item_id = ? AND serve_size = 'glass'",
                         (wine_id,)).fetchone()
    s.check("a glass is added", bool(glass), r)
    s.check("it inherits the wine's stock link",
            bool(glass) and glass["stock_item_id"] == stock_id)
    s.check("its depletion is computed, not typed",
            bool(glass) and abs(glass["stock_qty_per_unit"] - 0.2353) < 0.001,
            detail=str(glass["stock_qty_per_unit"] if glass else None))
    s.check("it inherits the course, so it is still a wine",
            bool(glass) and glass["course"] == "wine")
    s.check("and carries its own price", bool(glass) and glass["price"] == 9.0)

    oc.post(f"/admin/restaurant/menu/{wine_id}/pour",
            data={"serve_size": "bottle", "serve_volume_ml": "750", "price": "46"},
            follow_redirects=True)
    bottle = conn.execute(
        "SELECT * FROM menu_items WHERE parent_item_id = ? AND serve_size = 'bottle'",
        (wine_id,)).fetchone()
    s.check("a whole bottle can be sold too", bool(bottle))
    s.check("and takes more than a whole bottle once loss is counted",
            bool(bottle) and bottle["stock_qty_per_unit"] > 1.0,
            detail=str(bottle["stock_qty_per_unit"] if bottle else None))

    s.section("The wine itself stops being sellable once it has pours")
    till = m.pos_menu(conn)
    names = [i["name"] for items in till.values() for i in items]
    s.check("the glass is on the till", f"{TAG}Madiran — Glass" in names,
            detail=str([n for n in names if n.startswith(TAG)]))
    s.check("the bottle is on the till", f"{TAG}Madiran — Bottle" in names)
    # A bare wine with no size is not something a waiter can ring up.
    s.check("the sizeless parent is not", TAG + "Madiran" not in names)

    s.section("Correcting the bottle size moves every pour at once")
    # This is the whole point: eighty rows would have needed eighty edits.
    oc.post(f"/admin/restaurant/menu/{wine_id}/edit",
            data={"name": TAG + "Madiran", "course": "wine", "price": "46",
                  "container_ml": "1500", "pour_loss_percent": "15",
                  "stock_item_id": str(stock_id), "stock_qty_per_unit": "1",
                  "sold_in_pos": "1"},
            follow_redirects=True)
    moved = conn.execute("SELECT * FROM menu_items WHERE id = ?", (glass["id"],)).fetchone()
    s.check("the glass now takes half as much of a magnum",
            abs(moved["stock_qty_per_unit"] - 0.1176) < 0.001,
            detail=str(moved["stock_qty_per_unit"]))

    s.section("Selling a pour draws down the wine's stock")
    conn.execute("""INSERT INTO stock_movements (stock_item_id, delta, reason, created_at)
                    VALUES (?, 10, 'opening', ?)""", (stock_id, now))
    conn.commit()
    before = m.stock_levels(conn, [stock_id]).get(stock_id, 0)
    oc.post("/pos/open", data={"table_label": TAG + "T", "covers": "2"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "T",)).fetchone()
    oc.post(f"/pos/{order['id']}/add", data={"menu_item_id": moved["id"]},
            follow_redirects=True)
    after = m.stock_levels(conn, [stock_id]).get(stock_id, 0)
    s.check("one glass depletes a fraction of a bottle, not a whole one",
            0 < (before - after) < 1, detail=f"{before} -> {after}")
    s.check("and by exactly the computed amount",
            abs((before - after) - moved["stock_qty_per_unit"]) < 0.001,
            detail=str(before - after))

    s.section("Guards")
    cur = conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, sort_order, created_at)
           VALUES (?, 'drink', 'wine', 30.0, 1, 1, 1, 0, ?)""", (TAG + "Sizeless", now))
    sizeless = cur.lastrowid
    conn.commit()
    r = oc.post(f"/admin/restaurant/menu/{sizeless}/pour",
                data={"serve_size": "glass", "serve_volume_ml": "150", "price": "8"},
                follow_redirects=True)
    s.check("a pour on a wine with no bottle size is refused",
            not conn.execute("SELECT 1 FROM menu_items WHERE parent_item_id = ?",
                             (sizeless,)).fetchone(), r)
    s.check("and says to set the bottle size first",
            "bottle size" in r.get_data(as_text=True).lower())

    # arrêté du 27 mars 1987 — the volume has to appear on the card.
    conn.execute("UPDATE menu_items SET container_ml = 750 WHERE id = ?", (sizeless,))
    conn.commit()
    r = oc.post(f"/admin/restaurant/menu/{sizeless}/pour",
                data={"serve_size": "glass", "price": "8"}, follow_redirects=True)
    s.check("wine by the glass without a stated volume is refused",
            not conn.execute("SELECT 1 FROM menu_items WHERE parent_item_id = ?",
                             (sizeless,)).fetchone(), r)
    s.check("because French law requires the volume on the card",
            "volume" in r.get_data(as_text=True).lower())

    s.check("an employee cannot add a pour",
            ec.post(f"/admin/restaurant/menu/{wine_id}/pour",
                    data={"serve_size": "glass", "serve_volume_ml": "150", "price": "9"}
                    ).status_code in (302, 403))

    _cleanup(conn)
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
