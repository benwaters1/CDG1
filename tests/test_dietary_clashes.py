"""The allergy taken at booking, read against the card written this afternoon.

Two facts the system has held separately since the day it was built. The note
is taken weeks in advance by whoever answers the email; the card is written at
four by the chef. Nobody puts them together until a plate is already down in
front of somebody who cannot eat it.

The hard part is honesty. A dish with no allergens filled in is unknown, not
safe — and "nothing on the card they can eat" and "nobody has filled the
allergens in" need different people to do different things.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "diet-"


def _cleanup(conn):
    conn.execute("DELETE FROM menu_dishes WHERE menu_id IN "
                 "(SELECT id FROM menus WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()


def _dish(conn, menu_id, name, course, allergens, tags=None):
    item_id = None
    if tags is not None:
        conn.execute(
            """INSERT INTO menu_items (name, category, course, price, active, sold_in_pos,
                 dietary_tags, allergens, created_at)
               VALUES (?, 'main', ?, 28, 1, 1, ?, ?, ?)""",
            (TAG + name, course, tags, allergens,
             datetime.now(timezone.utc).isoformat()))
        item_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO menu_dishes (menu_id, course, menu_item_id, name, allergens,
             carte_price, in_formule, available, sort_order, created_at)
           VALUES (?, ?, ?, ?, ?, 28, 1, 1, 0, ?)""",
        (menu_id, course, item_id, TAG + name, allergens,
         datetime.now(timezone.utc).isoformat()))


def _booking(conn, name, party, on, note):
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, dietary_notes, created_at)
           VALUES (?, ?, ?, 'x@example.com', ?, ?, 'confirmed', ?, ?)""",
        (TAG + name, TAG + "tok" + name, TAG + name, party, on, note,
         datetime.now(timezone.utc).isoformat()))


def run():
    s = Suite("Dietary notes against the card")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    tonight = m.service_day().isoformat()

    s.section("Reading the note")
    # Both languages, because the booking form is in both.
    for note, want in [
            ("no gluten please", {"gluten"}),
            ("sans gluten", {"gluten"}),
            ("coeliac", {"gluten"}),
            ("severe nut allergy", {"nuts"}),
            ("allergie aux fruits à coque", {"nuts"}),
            ("lactose intolerant", {"milk"}),
            ("shellfish and fish", {"crustaceans", "fish"}),
            ("pas d'œufs", {"eggs"}),
            ("", set())]:
        got = m.parse_dietary(note)["allergens"]
        s.check(f"{note!r} reads as {sorted(want) or 'nothing'}", got == want,
                detail=f"got {sorted(got)}")

    s.check("a vegetarian is a diet, not an allergen",
            m.parse_dietary("vegetarian")["diets"] == {"vegetarian"}
            and not m.parse_dietary("vegetarian")["allergens"])
    s.check("and a végétalien is vegan",
            "vegan" in m.parse_dietary("végétalien")["diets"])
    # No cleverness about negation, deliberately: a parser that decided "nuts
    # are fine" meant no restriction would eventually be wrong in the one
    # direction that matters. The words are shown to a human either way.
    s.check("'nuts are fine' still flags nuts to be looked at",
            m.parse_dietary("nuts are fine")["allergens"] == {"nuts"})

    s.section("A card with a safe option says nothing")
    conn.execute(
        """INSERT INTO menus (service_date, service, title, status, formule_price,
             source, created_at) VALUES (?, 'dinner', ?, 'published', 65, 'manual', ?)""",
        (tonight, TAG + "Tonight", datetime.now(timezone.utc).isoformat()))
    menu_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _dish(conn, menu_id, "Velouté", "starter", "milk,celery")
    _dish(conn, menu_id, "Salade", "starter", "mustard")
    _dish(conn, menu_id, "Turbot", "main", "fish")
    _dish(conn, menu_id, "Bœuf", "main", "sulphites")
    _dish(conn, menu_id, "Tarte", "dessert", "gluten,eggs,milk")
    conn.commit()

    _booking(conn, "Dupont", 2, tonight, "no fish")
    conn.commit()
    clashes = m.dietary_clashes(conn, menu_id, tonight)
    s.check("the beef covers a no-fish table, so nothing is raised",
            clashes == [], detail=str(clashes))

    s.section("A course with nothing safe is named")
    _booking(conn, "Martin", 3, tonight, "coeliac — no gluten at all")
    conn.commit()
    clashes = m.dietary_clashes(conn, menu_id, tonight)
    s.check("one table is raised", len(clashes) == 1, detail=str(len(clashes)))
    c = clashes[0]
    s.check("the right one", c["guest"] == TAG + "Martin", detail=c["guest"])
    s.check("with its covers", c["covers"] == 3)
    s.check("and what was read from the note", c["avoiding"] == ["gluten"],
            detail=str(c["avoiding"]))
    s.check("only the dessert is a problem",
            [co["course"] for co in c["courses"]] == ["dessert"],
            detail=str([co["course"] for co in c["courses"]]))
    dessert = c["courses"][0]
    s.check("and it is the hard kind — the dish declares the allergen",
            dessert["nothing_at_all"] and dessert["clashing"] == [TAG + "Tarte"],
            detail=str(dessert))
    # The guest's own words travel with it. The parse is a prompt to look, not
    # a verdict on what they meant.
    s.check("the note itself is carried through", "coeliac" in c["note"])

    s.section("Blank allergens are unknown, not safe")
    _dish(conn, menu_id, "Sorbet", "dessert", None)
    conn.commit()
    clashes = m.dietary_clashes(conn, menu_id, tonight)
    # Guarded, because the regression this guards against — treating a blank
    # allergen list as safe — makes the list empty, and a clean FAIL says more
    # than an IndexError.
    dessert = next((co for c in clashes for co in c["courses"]
                    if co["course"] == "dessert"), None)
    s.check("the course is still raised", bool(dessert), detail=str(clashes))
    s.check("but as the soft kind, not 'nothing they can eat'",
            bool(dessert) and not dessert["nothing_at_all"], detail=str(dessert))
    s.check("naming the dish whose allergens were never filled in",
            bool(dessert) and dessert["unknown"] == [TAG + "Sorbet"], detail=str(dessert))
    s.check("and still naming the one that clashes",
            bool(dessert) and dessert["clashing"] == [TAG + "Tarte"])

    # Fill it in and the warning goes, which is the point of distinguishing them.
    conn.execute("UPDATE menu_dishes SET allergens = 'milk' WHERE menu_id = ? AND name = ?",
                 (menu_id, TAG + "Sorbet"))
    conn.commit()
    s.check("filling the allergens in clears it",
            m.dietary_clashes(conn, menu_id, tonight) == [],
            detail=str(m.dietary_clashes(conn, menu_id, tonight)))

    s.section("A dish that is off is not an option")
    conn.execute("UPDATE menu_dishes SET available = 0 WHERE menu_id = ? AND name = ?",
                 (menu_id, TAG + "Sorbet"))
    conn.commit()
    clashes = m.dietary_clashes(conn, menu_id, tonight)
    s.check("86ing the safe dessert brings the warning back", len(clashes) == 1,
            detail=str(clashes))
    conn.execute("UPDATE menu_dishes SET available = 1 WHERE menu_id = ?", (menu_id,))
    conn.commit()

    s.section("Diets need the dish to say so, not merely to be harmless")
    _booking(conn, "Blanc", 2, tonight, "vegetarian")
    conn.commit()
    clashes = m.dietary_clashes(conn, menu_id, tonight)
    veg = next((c for c in clashes if c["guest"] == TAG + "Blanc"), None)
    s.check("a vegetarian is raised on the mains", bool(veg)
            and any(co["course"] == "main" for co in veg["courses"]),
            detail=str(veg))
    s.check("as the soft kind — nothing is declared unsuitable, only undeclared",
            all(not co["nothing_at_all"] for co in veg["courses"]),
            detail=str(veg["courses"]))
    # Tag one as vegetarian and the mains stop being a question.
    _dish(conn, menu_id, "Légumes", "main", "milk", tags="vegetarian")
    conn.commit()
    clashes = m.dietary_clashes(conn, menu_id, tonight)
    veg = next((c for c in clashes if c["guest"] == TAG + "Blanc"), None)
    s.check("a dish tagged vegetarian answers the mains",
            not veg or not any(co["course"] == "main" for co in veg["courses"]),
            detail=str(veg))

    s.section("Only tables that are actually coming")
    conn.execute("UPDATE restaurant_bookings SET status = 'cancelled' WHERE guest_name = ?",
                 (TAG + "Martin",))
    conn.commit()
    s.check("a cancelled table is not checked",
            not any(c["guest"] == TAG + "Martin"
                    for c in m.dietary_clashes(conn, menu_id, tonight)))
    conn.execute("""UPDATE restaurant_bookings SET status = 'confirmed', no_show_at = ?
                    WHERE guest_name = ?""",
                 (datetime.now(timezone.utc).isoformat(), TAG + "Martin"))
    conn.commit()
    s.check("nor one already marked a no-show",
            not any(c["guest"] == TAG + "Martin"
                    for c in m.dietary_clashes(conn, menu_id, tonight)))
    conn.execute("UPDATE restaurant_bookings SET no_show_at = NULL WHERE guest_name = ?",
                 (TAG + "Martin",))
    conn.execute("UPDATE menu_dishes SET allergens = NULL WHERE menu_id = ? AND name = ?",
                 (menu_id, TAG + "Sorbet"))
    conn.commit()

    s.section("On the pass and on the card page")
    r = oc.get("/pos/kitchen")
    s.check("the pass opens", r.status_code == 200, detail=str(r.status_code))
    s.check("with the table on it", (TAG + "Martin").encode() in r.data)
    s.check("and the guest's own words", b"coeliac" in r.data)
    r = oc.get(f"/admin/restaurant/menu/day?date={tonight}&service=dinner")
    s.check("and the card page carries the same block",
            r.status_code == 200 and (TAG + "Martin").encode() in r.data,
            detail=str(r.status_code))

    # A night where every note is answered must say nothing at all.
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    r = oc.get("/pos/kitchen")
    s.check("no notes means no block", b"Check before service" not in r.data)

    _cleanup(conn)
    conn.close()
    return s
