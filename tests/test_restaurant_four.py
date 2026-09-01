"""What a dish earns, how many are coming, what sells, and where they sit.

WHAT A DISH EARNS. menu_items carries a price and a single stock link,
which is right for a glass poured from a bottle and useless for a plate of
anything. A dish is several things and nothing multiplied them out, so the
restaurant has never known which plates make money.

Two rules the costing must not break:

  - UNCOSTED IS NOT ZERO. A dish with nothing against it would otherwise
    read as the cheapest thing on the menu and therefore the best, which is
    the exact opposite of what it means.
  - PARTLY COSTED IS ITS OWN STATE, and a worse one than uncosted: a dish
    priced from two of its four ingredients reads as a wonderful margin.
    It is named rather than blended in.

HOW MANY ARE COMING. No-shows are taken off. Buying for people who never
came is the mistake a covers forecast exists to prevent, not to cause.

WHAT SELLS. Voided lines are left out — a struck-off line was not sold, and
counting it would rank a dish by how often it is keyed in by mistake.

WHERE THEY SIT. Seating a party onto a full table is allowed and SAYS SO,
because two parties on one table is a real thing at a long table and a
mistake at a small one, and only the person laying up knows which.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZREST"


def _cleanup(conn):
    conn.execute("DELETE FROM menu_item_ingredients WHERE menu_item_id IN "
                 "(SELECT id FROM menu_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM dining_tables WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The restaurant four")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    def stock(name, unit, cost):
        conn.execute(
            """INSERT INTO stock_items (name, category, unit, reorder_level,
                                        unit_cost, active, created_at)
               VALUES (?, 'food', ?, 0, ?, 1, ?)""",
            (TAG + " " + name, unit, cost, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def dish(name, price):
        conn.execute(
            """INSERT INTO menu_items (name, description, category, price,
                                       active, sort_order, created_at)
               VALUES (?, '', 'main', ?, 1, 0, ?)""",
            (TAG + " " + name, price, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    duck = stock("Duck leg", "each", 6.0)
    salt = stock("Salt", "kg", None)          # no cost on file
    confit = dish("Confit de canard", 24.0)
    nothing = dish("Mystery plate", 18.0)
    conn.commit()

    s.section("A dish is a list, not a field")
    r = oc.post(f"/restaurant/margins/{confit}/ingredient",
                data={"stock_item_id": str(duck), "quantity": "1"},
                follow_redirects=True)
    costed = {d["item"]["id"]: d for d in m.dish_costs(conn)}
    s.check("an ingredient can be put against it",
            costed[confit]["cost"] == 6.0, detail=str(flashes(r)))
    s.check("and what it leaves is worked out",
            costed[confit]["margin"] == 18.0
            and costed[confit]["margin_pct"] == 75,
            detail=f"{costed[confit]['margin']} at "
                   f"{costed[confit]['margin_pct']}%")

    s.section("Uncosted is not zero")
    # The check that stops a dish with no recipe reading as the best thing
    # on the menu.
    s.check("a dish with nothing against it is not costed",
            costed[nothing]["cost"] is None and not costed[nothing]["costed"],
            detail=str(costed[nothing]["cost"]))
    s.check("and claims no margin",
            costed[nothing]["margin"] is None
            and costed[nothing]["margin_pct"] is None,
            detail="zero cost would make it the most profitable plate in "
                   "the house")

    s.section("Partly costed is worse than uncosted, so it is named")
    oc.post(f"/restaurant/margins/{confit}/ingredient",
            data={"stock_item_id": str(salt), "quantity": "0.01"},
            follow_redirects=True)
    now_costed = {d["item"]["id"]: d for d in m.dish_costs(conn)}[confit]
    s.check("an ingredient with no price on file is named",
            any("Salt" in x for x in now_costed["missing_costs"]),
            detail=str(now_costed["missing_costs"]))
    s.check("and the dish still reports the cost it does know",
            now_costed["cost"] == 6.0,
            detail=f"{now_costed['cost']} — a dish priced from two of its "
                   "four ingredients reads as a wonderful margin, so the "
                   "gap has to be visible rather than silent")

    s.section("Adding the same ingredient twice edits it")
    oc.post(f"/restaurant/margins/{confit}/ingredient",
            data={"stock_item_id": str(duck), "quantity": "2"},
            follow_redirects=True)
    rows = conn.execute(
        """SELECT COUNT(*) AS c FROM menu_item_ingredients
            WHERE menu_item_id = ? AND stock_item_id = ?""",
        (confit, duck)).fetchone()["c"]
    s.check("rather than doubling the line", rows == 1, detail=str(rows))
    s.check("and the cost follows",
            {d["item"]["id"]: d for d in m.dish_costs(conn)}[confit]["cost"] == 12.0)

    s.section("How many are coming")
    def booking(name, when, party, status="confirmed", no_show=None):
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token,
                       guest_name, guest_email, party_size, dinner_date,
                       status, no_show_at, created_at)
               VALUES (?, ?, ?, 'r@example.invalid', ?, ?, ?, ?, ?)""",
            (f"{TAG}{name[:4].upper()}{party}", f"tok-{TAG.lower()}-{name}",
             TAG + " " + name, party, when.isoformat(), status, no_show, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    friday = today + timedelta(days=3)
    booking("Aline", friday, 4)
    booking("Bruno", friday, 2)
    booking("Chantal", friday, 6, status="cancelled")
    booking("Damien", friday, 8, no_show=now)
    conn.commit()

    ahead = {d["date"]: d for d in m.covers_ahead(conn, days=21, today=today)}
    s.check("confirmed parties are added up", ahead[friday]["covers"] == 6,
            detail=f"{ahead[friday]['covers']} — four and two")
    # Stated against what the fixture actually holds, so each check can
    # fail on its own. Three checks with the same condition are one check
    # written three times.
    everybody = conn.execute(
        """SELECT COALESCE(SUM(party_size), 0) AS c FROM restaurant_bookings
            WHERE guest_name LIKE ? AND dinner_date = ?""",
        (TAG + "%", friday.isoformat())).fetchone()["c"]
    s.check("the fixture really does hold parties that must not count",
            everybody == 20,
            detail=f"{everybody} booked in total against 6 that should "
                   "count — without this the checks below could pass on an "
                   "empty night")
    cancelled = conn.execute(
        """SELECT party_size FROM restaurant_bookings
            WHERE guest_name = ?""", (TAG + " Chantal",)).fetchone()["party_size"]
    s.check("a cancellation of six is left out",
            cancelled == 6 and ahead[friday]["covers"] == 6,
            detail="counting it would give twelve")
    no_show = conn.execute(
        """SELECT party_size, no_show_at FROM restaurant_bookings
            WHERE guest_name = ?""", (TAG + " Damien",)).fetchone()
    s.check("and a no-show of eight is too",
            no_show["no_show_at"] and ahead[friday]["covers"] == 6,
            detail="buying for people who never came is the mistake this "
                   "exists to prevent, not to cause")
    s.check("empty nights are listed too",
            (today + timedelta(days=4)) in ahead,
            detail="a list of only the busy nights cannot say which are quiet")

    s.section("What sells leaves out what was struck off")
    order = conn.execute(
        "SELECT id FROM pos_orders LIMIT 1").fetchone()
    if order:
        for qty, voided in ((3, 0), (5, 1)):
            conn.execute(
                """INSERT INTO pos_order_lines (order_id, name, unit_price,
                           quantity, voided, menu_item_id, created_at)
                   VALUES (?, ?, 24.0, ?, ?, ?, ?)""",
                (order["id"], TAG + " Confit", qty, voided, confit, now))
        conn.commit()
        sold = {r["name"]: r for r in m.what_sells(conn, days=30, today=today)}
        s.check("the sold line is counted",
                sold.get(TAG + " Confit", {}).get("sold") == 3,
                detail=str(sold.get(TAG + " Confit")))
        s.check("and the voided one is not",
                sold.get(TAG + " Confit", {}).get("sold") == 3,
                detail="counting a struck-off line would rank a dish by how "
                       "often it is keyed in by mistake")
    else:
        s.check("a till order exists to hang lines from", False,
                detail="no pos_orders row, so the what-sells checks could "
                       "not run — reported rather than skipped quietly")

    s.section("Where they sit")
    conn.execute(
        """INSERT INTO dining_tables (name, seats, active, sort_order, created_at)
           VALUES (?, 4, 1, 0, ?)""", (TAG + " Window", now))
    table = conn.execute("SELECT id FROM dining_tables WHERE name = ?",
                         (TAG + " Window",)).fetchone()["id"]
    conn.commit()
    aline = conn.execute("SELECT id FROM restaurant_bookings WHERE guest_name = ?",
                         (TAG + " Aline",)).fetchone()["id"]
    ec.post(f"/restaurant/tables/seat/{aline}",
            data={"dining_table_id": str(table)}, follow_redirects=True)
    s.check("a party can be put on a table",
            conn.execute("SELECT dining_table_id FROM restaurant_bookings "
                         "WHERE id = ?", (aline,)).fetchone()["dining_table_id"]
            == table)

    s.section("Overfilling a table is allowed and says so")
    # Two parties on one table is a real thing at a long table and a
    # mistake at a small one, and only the person laying up knows which.
    bruno = conn.execute("SELECT id FROM restaurant_bookings WHERE guest_name = ?",
                         (TAG + " Bruno",)).fetchone()["id"]
    r = ec.post(f"/restaurant/tables/seat/{bruno}",
                data={"dining_table_id": str(table)}, follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("it warns rather than refusing",
            "seats 4" in said and "would have 6" in said, detail=said)
    s.check("naming who is already there",
            TAG + " Aline" in said, detail=said)
    s.check("and seats them anyway",
            conn.execute("SELECT dining_table_id FROM restaurant_bookings "
                         "WHERE id = ?", (bruno,)).fetchone()["dining_table_id"]
            == table,
            detail="refusing would make the app wrong about a long table")

    s.section("And a party can be taken off a table")
    ec.post(f"/restaurant/tables/seat/{bruno}", data={"dining_table_id": ""},
            follow_redirects=True)
    s.check("back to not seated",
            conn.execute("SELECT dining_table_id FROM restaurant_bookings "
                         "WHERE id = ?", (bruno,)).fetchone()["dining_table_id"]
            is None)

    s.section("An employee cannot rewrite the menu costs")
    r = ec.post(f"/restaurant/margins/{confit}/ingredient",
                data={"stock_item_id": str(duck), "quantity": "9"},
                follow_redirects=False)
    s.check("the recipe is the owner's", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = ec.get("/restaurant/what-sells", follow_redirects=False)
    s.check("and so is what sells", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
