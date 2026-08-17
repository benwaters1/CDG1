"""Will tonight's card get through tonight's bookings?

Three things the system already knew, in three different places: the dishes on
the published card, the stock ledger behind each of them, and the covers on the
booking sheet. Nobody holds all three at six in the evening, and the result is a
dish coming off at nine with four tables still waiting on it.

The answer has to be honest in both directions. Inventing a shortfall for a
course nobody counts stock for would train the chef to ignore it, and a warning
that is ignored is worse than none.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "cardcap-"


def _iso(days=0):
    return (m.service_day() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menu_dishes WHERE menu_id IN "
                 "(SELECT id FROM menus WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()


def _stock(conn, name, qty, unit="portions"):
    """A stock line with an opening balance, through the ledger like everything else."""
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level, active, created_at)
           VALUES (?, 'food', ?, 2, 1, ?)""",
        (TAG + name, unit, datetime.now(timezone.utc).isoformat()))
    sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO stock_movements (stock_item_id, delta, reason, note, created_at)
           VALUES (?, ?, 'opening', 'test opening', ?)""",
        (sid, qty, datetime.now(timezone.utc).isoformat()))
    return sid


def _item(conn, name, course, stock_id=None, per=1):
    conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, sold_in_pos,
             stock_item_id, stock_qty_per_unit, created_at)
           VALUES (?, 'main', ?, 28, 1, 1, ?, ?, ?)""",
        (TAG + name, course, stock_id, per, datetime.now(timezone.utc).isoformat()))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _dish(conn, menu_id, name, course, item_id=None, available=1):
    conn.execute(
        """INSERT INTO menu_dishes (menu_id, course, menu_item_id, name, carte_price,
             in_formule, available, sort_order, created_at)
           VALUES (?, ?, ?, ?, 28, 1, ?, 0, ?)""",
        (menu_id, course, item_id, TAG + name, available,
         datetime.now(timezone.utc).isoformat()))


def _booking(conn, name, party, on, status="confirmed"):
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, 'x@example.com', ?, ?, ?, ?)""",
        (TAG + name + on, TAG + "tok" + name + on, TAG + name, party, on, status,
         datetime.now(timezone.utc).isoformat()))


def run():
    s = Suite("Will the card survive the night")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    tonight = _iso(0)

    conn.execute(
        """INSERT INTO menus (service_date, service, title, status, formule_price,
             source, created_at) VALUES (?, 'dinner', ?, 'published', 65, 'manual', ?)""",
        (tonight, TAG + "Tonight", datetime.now(timezone.utc).isoformat()))
    menu_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    pigeon = _stock(conn, "pigeon", 6)
    turbot = _stock(conn, "turbot", 4)
    _dish(conn, menu_id, "Pigeon", "main", _item(conn, "Pigeon", "main", pigeon))
    _dish(conn, menu_id, "Turbot", "main", _item(conn, "Turbot", "main", turbot))
    # A starter nobody counts stock for — soup out of the kitchen.
    _dish(conn, menu_id, "Velouté", "starter", _item(conn, "Velouté", "starter"))
    conn.commit()

    s.section("With nothing booked there is nothing to warn about")
    cap = m.card_capacity(conn, menu_id, tonight)
    s.check("no covers", cap["covers"] == 0, detail=str(cap["covers"]))
    s.check("and nothing reported short", cap["short"] == [], detail=str(cap["short"]))

    s.section("Ten booked against ten portions of main")
    _booking(conn, "Dupont", 6, tonight)
    _booking(conn, "Martin", 4, tonight)
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    s.check("the covers are counted", cap["covers"] == 10, detail=str(cap["covers"]))
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    s.check("the mains total across the course, not per dish",
            mains["portions"] == 10, detail=str(mains["portions"]))
    s.check("so nothing is short", not cap["short"], detail=str(cap["short"]))
    s.check("but the tightest dish is still named",
            mains["first_out"] and mains["first_out"]["name"] == TAG + "Turbot",
            detail=str(mains["first_out"]))

    s.section("One more table and the mains do not reach")
    _booking(conn, "Blanc", 4, tonight)
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    s.check("fourteen booked", cap["covers"] == 14, detail=str(cap["covers"]))
    s.check("the mains are four short", mains["short_by"] == 4,
            detail=str(mains["short_by"]))
    s.check("and the course is flagged",
            any(c["course"] == "main" for c in cap["short"]))
    # This is the sentence the chef actually acts on.
    s.check("naming the dish that goes first",
            mains["first_out"]["name"] == TAG + "Turbot"
            and mains["first_out"]["portions"] == 4,
            detail=str(mains["first_out"]))

    s.section("A course nobody counts is never short")
    starters = next(c for c in cap["courses"] if c["course"] == "starter")
    s.check("the untracked starter is marked as such", starters["untracked"])
    s.check("and reports no shortfall rather than inventing one",
            starters["short_by"] == 0, detail=str(starters["short_by"]))

    s.section("A pending booking is not a promise")
    _booking(conn, "Perhaps", 20, tonight, status="pending")
    conn.commit()
    s.check("pending covers are not counted",
            m.card_capacity(conn, menu_id, tonight)["covers"] == 14)
    conn.execute("UPDATE restaurant_bookings SET status = 'cancelled' WHERE guest_name = ?",
                 (TAG + "Perhaps",))
    conn.commit()
    s.check("nor cancelled ones",
            m.card_capacity(conn, menu_id, tonight)["covers"] == 14)
    # A no-show that has already been marked is a table that will not eat.
    conn.execute("""UPDATE restaurant_bookings SET status = 'confirmed',
                    no_show_at = ? WHERE guest_name = ?""",
                 (datetime.now(timezone.utc).isoformat(), TAG + "Perhaps"))
    conn.commit()
    s.check("nor a table already marked a no-show",
            m.card_capacity(conn, menu_id, tonight)["covers"] == 14)
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name = ?", (TAG + "Perhaps",))
    conn.commit()

    s.section("Selling depletes, so the warning gets worse as service runs")
    m.record_stock_movement(conn, pigeon, -3, "sale", note="test service")
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    s.check("three pigeon sold leaves seven portions", mains["portions"] == 7,
            detail=str(mains["portions"]))
    s.check("and the shortfall grows to seven", mains["short_by"] == 7,
            detail=str(mains["short_by"]))
    s.check("the pigeon is now the tightest",
            mains["first_out"]["name"] == TAG + "Pigeon", detail=str(mains["first_out"]))

    s.section("Portions, not units — a dish taking two of something counts half")
    lamb_stock = _stock(conn, "lamb", 7, unit="kg")
    _dish(conn, menu_id, "Agneau", "main", _item(conn, "Agneau", "main", lamb_stock, per=2))
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    # 7kg at 2kg a portion is three portions, not seven and not three and a half.
    s.check("seven kilos at two a portion is three, rounded down",
            mains["portions"] == 10, detail=str(mains["portions"]))

    s.section("86ing a dish is not a shortfall, but the course carries it")
    conn.execute("UPDATE menu_dishes SET available = 0 WHERE menu_id = ? AND name = ?",
                 (menu_id, TAG + "Turbot"))
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    s.check("the turbot's four portions leave the count", mains["portions"] == 6,
            detail=str(mains["portions"]))
    s.check("and it is reported as already off", mains["off"] == 1,
            detail=str(mains["off"]))
    s.check("it is not offered as the dish that goes first",
            mains["first_out"]["name"] != TAG + "Turbot")

    s.section("Everything off is its own answer")
    conn.execute("UPDATE menu_dishes SET available = 0 WHERE menu_id = ? AND course = 'main'",
                 (menu_id,))
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    s.check("the course reads as entirely off", mains["all_off"])
    s.check("and is flagged", any(c["course"] == "main" for c in cap["short"]))
    conn.execute("UPDATE menu_dishes SET available = 1 WHERE menu_id = ?", (menu_id,))
    conn.commit()

    s.section("Covers that have already eaten are not still owed a portion")
    # Stock falls as dishes go out. If the demand side stayed at fourteen all
    # night the pass would be warned, at nine, about a shortfall it already ate.
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state,
             service_date, opened_at) VALUES (?, 4, 'open', 'main', ?, ?)""",
        (TAG + "T1", tonight, datetime.now(timezone.utc).isoformat()))
    oid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for _i in range(4):
        conn.execute(
            """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
                 state, vat_rate, created_at) VALUES (?, ?, 28, 1, 'main', 'served', 10, ?)""",
            (oid, TAG + "Pigeon", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    cap = m.card_capacity(conn, menu_id, tonight)
    mains = next(c for c in cap["courses"] if c["course"] == "main")
    s.check("four mains have gone out", mains["served"] == 4, detail=str(mains["served"]))
    s.check("so ten covers are still owed one", mains["still_to_come"] == 10,
            detail=str(mains["still_to_come"]))
    # Ten portions left, ten covers still to come. Measured against all fourteen
    # this would read four short, and the pass would be chasing a dish that is
    # already accounted for.
    s.check("and the shortfall is measured against those, not all fourteen",
            mains["short_by"] == 0, detail=str(mains["short_by"]))

    conn.execute("UPDATE pos_order_lines SET voided = 1 WHERE order_id = ?", (oid,))
    conn.commit()
    mains = next(c for c in m.card_capacity(conn, menu_id, tonight)["courses"]
                 if c["course"] == "main")
    s.check("voiding them puts the covers back", mains["still_to_come"] == 14,
            detail=str(mains["still_to_come"]))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id = ?", (oid,))
    conn.execute("DELETE FROM pos_orders WHERE id = ?", (oid,))
    conn.commit()

    s.section("On the page the chef actually looks at")
    r = oc.get(f"/admin/restaurant/menu/day?date={tonight}&service=dinner")
    s.check("the card page opens", r.status_code == 200, detail=str(r.status_code))
    s.check("with the covers on it", b"14 covers booked" in r.data)
    s.check("and the shortfall named", b"short" in r.data)
    s.check("and the dish that goes first",
            (TAG + "Pigeon").encode() in r.data)

    s.section("And on the pass, where 86ing is actually decided")
    r = oc.get("/pos/kitchen")
    s.check("the pass opens", r.status_code == 200, detail=str(r.status_code))
    s.check("with the shortfall on it", b"Running short" in r.data)
    s.check("naming the dish that goes first", (TAG + "Pigeon").encode() in r.data)

    # Nothing short must say nothing. A pass covered in reassurance is a pass
    # nobody reads, and then the one real warning is invisible.
    conn.execute("UPDATE stock_movements SET delta = 200 WHERE stock_item_id = ? "
                 "AND reason = 'opening'", (pigeon,))
    conn.commit()
    r = oc.get("/pos/kitchen")
    s.check("plenty of everything says nothing at all",
            b"Running short" not in r.data)

    # Tomorrow has the same card but no bookings, so no panel at all.
    conn.execute("UPDATE menus SET service_date = ? WHERE id = ?", (_iso(1), menu_id))
    conn.commit()
    r = oc.get(f"/admin/restaurant/menu/day?date={_iso(1)}&service=dinner")
    s.check("a night with nothing booked says nothing",
            b"covers booked" not in r.data, detail=str(r.status_code))

    _cleanup(conn)
    conn.close()
    return s
