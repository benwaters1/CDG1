"""Beverage packages: what's included with dinner, and what's extra.

The arithmetic that matters is the allowance — "€25 of wine included" means
drinks are rung up as normal and the package absorbs them up to the limit, with
anything past it charged. Get it wrong and either the guest is billed for what
they were told was included, or the house gives away the cellar.

The VAT treatment is the other half: a package covers drinks specifically, so
the credit comes off the 20% base. Spreading it proportionally across the food
would move consideration from the 20% base to the 10% one and under-declare.
"""
from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "pkg-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM drink_package_items WHERE package_id IN "
                 "(SELECT id FROM drink_packages WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM drink_packages WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _line(conn, order_id, name, price, course, vat, now, qty=1):
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
           state, vat_rate, created_at) VALUES (?,?,?,?,?,'new',?,?)""",
        (order_id, name, price, qty, course, vat, now))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def run():
    s = Suite("Beverage packages")
    oc, ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    _cleanup(conn)

    s.section("Creating a package")
    r = oc.post("/admin/restaurant/packages/new",
                data={"name": TAG + "Formule boisson", "kind": "allowance",
                      "allowance_amount": "25", "per": "cover", "price": "0",
                      "disclosure": "1 verre de vin (15 cl) par personne"},
                follow_redirects=True)
    package = conn.execute("SELECT * FROM drink_packages WHERE name = ?",
                           (TAG + "Formule boisson",)).fetchone()
    s.check("the package is created", bool(package), r)
    s.check("as an allowance", bool(package) and package["kind"] == "allowance")
    s.check("with the wording the card legally needs",
            bool(package) and "15 cl" in (package["disclosure"] or ""))

    # An allowance with no amount is not a package, it is a typo.
    r = oc.post("/admin/restaurant/packages/new",
                data={"name": TAG + "Nothing", "kind": "allowance", "per": "cover"},
                follow_redirects=True)
    s.check("an allowance with no amount is refused",
            not conn.execute("SELECT 1 FROM drink_packages WHERE name = ?",
                             (TAG + "Nothing",)).fetchone(), r)

    s.section("An allowance absorbs drinks up to its limit")
    oc.post("/pos/open", data={"table_label": TAG + "1", "covers": "2"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "1",)).fetchone()
    # 2 covers x EUR25 = EUR50 of allowance.
    _line(conn, order["id"], TAG + "Madiran", 46.0, "wine", 20.0, now)
    _line(conn, order["id"], TAG + "Water", 5.0, "drink", 20.0, now)
    _line(conn, order["id"], TAG + "Magret", 32.0, "main", 10.0, now)
    conn.commit()

    oc.post(f"/pos/{order['id']}/package", data={"package_id": package["id"]},
            follow_redirects=True)
    bill = m.pos_bill(conn, order["id"])
    s.check("drinks are covered up to the allowance", bill["package"] == 50.0,
            detail=str(bill["package"]))
    # 46 + 5 = 51 of drink, only 50 covered — EUR1 spills over.
    s.check("the excess over the allowance is still charged",
            bill["total"] == round(83.0 - 50.0, 2), detail=str(bill["total"]))
    # The food is never touched: a package that absorbed the beef would be a
    # discount wearing a different hat.
    food = next(l for l in bill["live"] if l["name"] == TAG + "Magret")
    s.check("food is never covered by a beverage package",
            (food["package_covered"] or 0) == 0, detail=str(food["package_covered"]))
    # Spent on the dearest first — a guest with a EUR46 bottle and a EUR5 water
    # expects the bottle to be what the package went on.
    wine = next(l for l in bill["live"] if l["name"] == TAG + "Madiran")
    s.check("the allowance goes on the dearest drink first",
            wine["package_covered"] == 46.0, detail=str(wine["package_covered"]))

    s.section("The credit comes off the drink VAT rate, not spread across the food")
    drink_base = bill["vat_by_rate"].get(20.0, {}).get("gross", 0)
    food_base = bill["vat_by_rate"].get(10.0, {}).get("gross", 0)
    s.check("the food base is untouched at its full amount", food_base == 32.0,
            detail=str(food_base))
    s.check("the drink base is reduced by what the package covered",
            round(drink_base, 2) == 1.0, detail=str(drink_base))
    s.check("so the VAT total reflects only what is actually charged",
            abs(bill["vat_total"] - (round(32 - 32 / 1.1, 2) + round(1 - 1 / 1.2, 2))) < 0.02,
            detail=str(bill["vat_total"]))

    s.section("It is recomputed, not accumulated")
    # Voiding a covered drink must release what it was holding, or the next
    # drink ordered gets charged in full against an allowance already spent.
    oc.post(f"/pos/line/{wine['id']}/void", data={"reason": "Rung up in error"},
            follow_redirects=True)
    after = m.pos_bill(conn, order["id"])
    s.check("voiding a covered drink releases its allowance",
            after["package"] == 5.0, detail=str(after["package"]))
    water = next(l for l in after["live"] if l["name"] == TAG + "Water")
    s.check("and the remaining drink is now fully covered",
            water["package_covered"] == 5.0, detail=str(water["package_covered"]))

    # Adding another drink should use the freed allowance without being asked.
    conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, always_available, sort_order, created_at)
           VALUES (?, 'drink', 'wine', 30.0, 1, 1, 1, 1, 0, ?)""", (TAG + "Jurancon", now))
    conn.commit()
    new_wine = conn.execute("SELECT id FROM menu_items WHERE name = ?",
                            (TAG + "Jurancon",)).fetchone()["id"]
    oc.post(f"/pos/{order['id']}/add", data={"menu_item_id": new_wine}, follow_redirects=True)
    grown = m.pos_bill(conn, order["id"])
    s.check("a drink added afterwards uses the allowance automatically",
            grown["package"] == 35.0, detail=str(grown["package"]))

    s.section("Removing the package puts everything back")
    oc.post(f"/pos/{order['id']}/package", data={"package_id": ""}, follow_redirects=True)
    bare = m.pos_bill(conn, order["id"])
    s.check("nothing is covered", bare["package"] == 0.0, detail=str(bare["package"]))
    s.check("and the full amount is chargeable again",
            bare["total"] == bare["gross"], detail=f"{bare['total']} vs {bare['gross']}")

    s.section("A fixed package covers a named list and charges its own price")
    oc.post("/admin/restaurant/packages/new",
            data={"name": TAG + "Pairing", "kind": "fixed", "price": "40",
                  "per": "cover", "disclosure": "Accord mets et vins"},
            follow_redirects=True)
    fixed = conn.execute("SELECT * FROM drink_packages WHERE name = ?",
                         (TAG + "Pairing",)).fetchone()
    s.check("a fixed package can be created", bool(fixed))
    oc.post(f"/admin/restaurant/packages/{fixed['id']}/item",
            data={"menu_item_id": new_wine}, follow_redirects=True)
    s.check("drinks can be added to its list",
            bool(conn.execute("SELECT 1 FROM drink_package_items WHERE package_id = ?",
                              (fixed["id"],)).fetchone()))

    oc.post("/pos/open", data={"table_label": TAG + "2", "covers": "1"}, follow_redirects=True)
    order2 = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                          (TAG + "2",)).fetchone()
    oc.post(f"/pos/{order2['id']}/add", data={"menu_item_id": new_wine}, follow_redirects=True)
    oc.post(f"/pos/{order2['id']}/package", data={"package_id": fixed["id"]},
            follow_redirects=True)
    fixed_bill = m.pos_bill(conn, order2["id"])
    s.check("a drink on the list is covered outright",
            fixed_bill["package"] == 30.0, detail=str(fixed_bill["package"]))

    s.section("It is on the bill, and in the journal")
    page = oc.get(f"/pos/{order2['id']}/receipt").get_data(as_text=True)
    s.check("the guest sees what the package took off", "compris" in page.lower())
    s.check("named, so it can be accounted for", TAG + "Pairing" in page)
    s.check("applying a package is journalled",
            bool(conn.execute(
                "SELECT 1 FROM pos_journal WHERE event_type = 'package_set' AND order_id = ?",
                (order2["id"],)).fetchone()))
    s.check("the journal still verifies", m.pos_journal_verify(conn) is None,
            detail=str(m.pos_journal_verify(conn)))

    s.section("Guards")
    s.check("the admin page renders",
            oc.get("/admin/restaurant/packages").status_code == 200)
    s.check("an employee cannot create a package",
            ec.post("/admin/restaurant/packages/new",
                    data={"name": TAG + "Sneaky", "kind": "allowance",
                          "allowance_amount": "10"}).status_code in (302, 403))
    r = oc.post(f"/pos/{order2['id']}/package", data={"package_id": "999999"},
                follow_redirects=True)
    s.check("an unknown package is a 404, not silently applied", r.status_code == 404)

    _cleanup(conn)
    conn.close()
    return s
