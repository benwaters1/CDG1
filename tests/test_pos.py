"""A whole service, driven through the POS.

The old till could add a line and total it. It had no menu (it sold from
`extras` — champagne and ski transfers), it never told the kitchen anything,
and it did not know the table in front of it had a reservation with an allergy
on it and a deposit already paid. This runs a service end to end: seat a booked
table, order by seat, fire the courses, cook them, split the bill, and cash up.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now
import _harness

m = _harness.m
TAG = "postest-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_payments WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _menu(conn, now):
    """Three dishes across three courses, one of them alcoholic — because the
    VAT split is only testable if the tab holds both rates."""
    ids = {}
    for name, course, price, allerg in [
            (TAG + "Velouté", "starter", 14.0, "milk,celery"),
            (TAG + "Truite", "main", 28.0, "fish"),
            (TAG + "Madiran", "wine", 38.0, "sulphites")]:
        cur = conn.execute(
            """INSERT INTO menu_items (name, category, course, price, allergens, active,
               available, sold_in_pos, sort_order, created_at)
               VALUES (?, 'main', ?, ?, ?, 1, 1, 1, 0, ?)""",
            (name, course, price, allerg, now))
        ids[course] = cur.lastrowid
    conn.commit()
    return ids


def run():
    s = Suite("Restaurant POS")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    now = datetime_now()
    today = datetime.now(timezone.utc).date()
    _cleanup(conn)
    dish = _menu(conn, now)

    # ------------------------------------------------- seating a reservation
    s.section("Seating a table that booked")
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, dietary_notes, deposit_amount,
           total_price, created_at)
           VALUES (?, ?, 'Madame Cazenave', 'caz@example.invalid', 2, ?, 'confirmed',
                   'Severe nut allergy — seat 2', 40.0, 0, ?)""",
        (TAG + "RES", TAG + "tok", today.isoformat(), now))
    conn.commit()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE reference_code = ?",
                           (TAG + "RES",)).fetchone()

    floor = oc.get("/pos").get_data(as_text=True)
    s.check("a booked table waiting to be seated shows on the floor",
            "Madame Cazenave" in floor)
    s.check("with the allergy taken at booking, before anyone is seated",
            "Severe nut allergy" in floor)

    r = oc.post("/pos/open", data={"restaurant_booking_id": booking["id"],
                                   "table_label": TAG + "6"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                         (TAG + "6",)).fetchone()
    s.check("the table opens against its reservation",
            bool(order) and order["restaurant_booking_id"] == booking["id"], r)
    s.check("covers come from the booking, not retyped",
            bool(order) and order["covers"] == 2)
    # The old till knew nothing about a deposit and would have charged for it
    # a second time.
    s.check("the deposit already paid is credited to the tab",
            bool(order) and order["deposit_credit"] == 40.0,
            detail=f"credit={order['deposit_credit'] if order else None}")

    page = oc.get(f"/pos/{order['id']}").get_data(as_text=True)
    s.check("the allergy is on the till screen", "Severe nut allergy" in page)
    s.check("the menu is on the till as buttons", TAG + "Truite" in page)

    # ------------------------------------------------------------- ordering
    s.section("Ordering by seat and by course")
    oc.post(f"/pos/{order['id']}/add",
            data={"menu_item_id": dish["starter"], "seat_number": "1"}, follow_redirects=True)
    oc.post(f"/pos/{order['id']}/add",
            data={"menu_item_id": dish["main"], "seat_number": "1",
                  "notes": "no butter"}, follow_redirects=True)
    oc.post(f"/pos/{order['id']}/add",
            data={"menu_item_id": dish["main"], "seat_number": "2"}, follow_redirects=True)
    oc.post(f"/pos/{order['id']}/add",
            data={"menu_item_id": dish["wine"]}, follow_redirects=True)
    lines = conn.execute("SELECT * FROM pos_order_lines WHERE order_id = ? ORDER BY id",
                         (order["id"],)).fetchall()
    s.check("four lines are on the tab", len(lines) == 4, detail=str(len(lines)))
    s.check("each knows its course", {l["course"] for l in lines} == {"starter", "main", "wine"},
            detail=str({l["course"] for l in lines}))
    s.check("and whose seat it is", [l["seat_number"] for l in lines] == [1, 1, 2, None],
            detail=str([l["seat_number"] for l in lines]))
    # A kitchen note that never reaches the kitchen is a note to nobody.
    s.check("a note for the kitchen is kept",
            any((l["notes"] or "") == "no butter" for l in lines))
    s.check("nothing has been sent yet",
            all(l["state"] == "new" for l in lines))

    # VAT: food 10, wine 20. A single blended rate would be wrong on roughly
    # half a restaurant's turnover.
    bill = m.pos_bill(conn, order["id"])
    s.check("the tab carries two VAT rates, not one",
            len(bill["vat_by_rate"]) == 2, detail=str(list(bill["vat_by_rate"])))
    s.check("the gross is right", bill["gross"] == 108.0, detail=str(bill["gross"]))
    # 108 gross − 40 deposit already paid.
    s.check("the deposit comes off the total", bill["total"] == 68.0, detail=str(bill["total"]))
    food = bill["vat_by_rate"].get(10.0, {})
    s.check("VAT is extracted from the gross, not added",
            abs(food.get("vat", 0) - round(70 - 70 / 1.1, 2)) < 0.02,
            detail=str(food))

    # ------------------------------------------------------- to the kitchen
    s.section("Sending it to the kitchen")
    kitchen = oc.get("/pos/kitchen").get_data(as_text=True)
    s.check("the pass is empty until something is sent", TAG + "Truite" not in kitchen)

    r = oc.post(f"/pos/{order['id']}/send", data={"course": "starter"}, follow_redirects=True)
    states = {l["course"]: l["state"] for l in conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ?", (order["id"],)).fetchall()}
    # Firing by course is how service works: starters now, mains when they are
    # cleared. Sending the lot at once is the thing that ruins a table.
    s.check("firing one course sends only that course",
            states["starter"] == "sent" and states["main"] == "new", r, detail=str(states))

    kitchen = oc.get("/pos/kitchen").get_data(as_text=True)
    s.check("the ticket reaches the pass", TAG + "Velouté" in kitchen)
    s.check("with the table on it", TAG + "6" in kitchen)
    s.check("and the allergy, where the cooking happens", "nut allergy" in kitchen.lower())

    before = len(conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ? AND state = 'sent'",
        (order["id"],)).fetchall())
    oc.post(f"/pos/{order['id']}/send", data={"course": "starter"}, follow_redirects=True)
    after = len(conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ? AND state = 'sent'",
        (order["id"],)).fetchall())
    # Sending twice must not duplicate a ticket — that is how a kitchen plates
    # four of everything.
    s.check("sending the same course twice does not re-fire it", before == after,
            detail=f"{before} → {after}")

    oc.post(f"/pos/{order['id']}/send", follow_redirects=True)
    s.check("sending with no course fires everything left",
            not conn.execute(
                "SELECT 1 FROM pos_order_lines WHERE order_id = ? AND state = 'new'",
                (order["id"],)).fetchone())

    starter_line = conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ? AND course = 'starter'",
        (order["id"],)).fetchone()
    oc.post(f"/pos/line/{starter_line['id']}/state", data={"state": "ready"},
            follow_redirects=True)
    s.check("the kitchen can mark a dish ready",
            conn.execute("SELECT state FROM pos_order_lines WHERE id = ?",
                         (starter_line["id"],)).fetchone()["state"] == "ready")

    # --------------------------------------------------------------- voids
    s.section("Voiding")
    wine_line = conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ? AND course = 'wine'",
        (order["id"],)).fetchone()
    r = oc.post(f"/pos/line/{wine_line['id']}/void", data={}, follow_redirects=True)
    s.check("a void with no reason is refused",
            not conn.execute("SELECT voided FROM pos_order_lines WHERE id = ?",
                             (wine_line["id"],)).fetchone()["voided"], r)
    oc.post(f"/pos/line/{wine_line['id']}/void",
            data={"reason": "Guest changed their mind"}, follow_redirects=True)
    voided = conn.execute("SELECT * FROM pos_order_lines WHERE id = ?",
                          (wine_line["id"],)).fetchone()
    s.check("with a reason it is voided", voided["voided"] == 1)
    # A void with no name against it is the oldest hole in any till.
    s.check("the reason and the person are recorded",
            voided["void_reason"] == "Guest changed their mind" and voided["voided_by_user_id"],
            detail=f"{voided['void_reason']!r} by {voided['voided_by_user_id']}")
    s.check("and it reaches the audit log",
            bool(conn.execute(
                "SELECT 1 FROM audit_log WHERE action = 'pos_line_voided' AND target LIKE ?",
                ("%Madiran%",)).fetchone()))
    s.check("the voided line comes off the bill",
            m.pos_bill(conn, order["id"])["gross"] == 70.0,
            detail=str(m.pos_bill(conn, order["id"])["gross"]))

    # ------------------------------------------------------------ the bill
    s.section("Discount, service and splitting the bill")
    r = oc.post(f"/pos/{order['id']}/adjust",
                data={"kind": "discount", "percent": "10"}, follow_redirects=True)
    s.check("a discount with no reason is refused",
            m.pos_bill(conn, order["id"])["discount"] == 0, r)
    oc.post(f"/pos/{order['id']}/adjust",
            data={"kind": "discount", "percent": "10", "reason": "Long wait for mains"},
            follow_redirects=True)
    bill = m.pos_bill(conn, order["id"])
    s.check("with a reason it applies", bill["discount"] == 7.0, detail=str(bill["discount"]))
    s.check("and lands in the audit log",
            bool(conn.execute("SELECT 1 FROM audit_log WHERE action = 'pos_discount'").fetchone()))

    # 70 − 7 discount − 40 deposit = 23
    s.check("the total is gross less discount less deposit",
            bill["total"] == 23.0, detail=str(bill["total"]))

    r = oc.post(f"/pos/{order['id']}/pay",
                data={"method": "cash", "amount": "10"}, follow_redirects=True)
    after = m.pos_bill(conn, order["id"])
    # Splitting a bill is several payments against one tab. The old till took
    # one payment and closed, so a split table had to be rung up twice and the
    # covers count was wrong for ever after.
    s.check("a part payment is recorded", after["paid"] == 10.0, r, detail=str(after["paid"]))
    s.check("and the tab stays open with the rest outstanding",
            after["order"]["status"] == "open" and after["outstanding"] == 13.0,
            detail=f"{after['order']['status']} / {after['outstanding']}")

    r = oc.post(f"/pos/{order['id']}/pay",
                data={"method": "card_terminal", "amount": "999"}, follow_redirects=True)
    s.check("paying more than is owed is refused",
            m.pos_bill(conn, order["id"])["paid"] == 10.0, r)

    oc.post(f"/pos/{order['id']}/pay", data={"method": "card_terminal"}, follow_redirects=True)
    final = m.pos_bill(conn, order["id"])
    # 'paid' is the schema's own word for a closed tab; the column is
    # CHECK-constrained, so this is what settled looks like.
    s.check("paying the rest settles the tab",
            final["order"]["status"] == "paid" and final["outstanding"] == 0,
            detail=f"{final['order']['status']} / {final['outstanding']}")

    receipt = oc.get(f"/pos/{order['id']}/receipt").get_data(as_text=True)
    s.check("the bill prints", "Total TTC" in receipt)
    # A French note has to identify who issued it. The docstring used to claim
    # this while the template hardcoded an address and printed no SIRET.
    conn.execute("""INSERT INTO company_info (id, legal_name, registration_number,
                    vat_number, registered_address)
                    VALUES (1, 'SCI Gudanes', '80012345600017', 'FR40800123456',
                            '2 Route de Beille, 09310 Chateau-Verdun')
                    ON CONFLICT(id) DO UPDATE SET
                      legal_name = excluded.legal_name,
                      registration_number = excluded.registration_number,
                      vat_number = excluded.vat_number,
                      registered_address = excluded.registered_address""")
    conn.commit()
    identified = oc.get(f"/pos/{order['id']}/receipt").get_data(as_text=True)
    s.check("carrying the SIRET", "80012345600017" in identified)
    s.check("and the TVA number", "FR40800123456" in identified)
    s.check("and the address from Company info, not one written into the page",
            "2 Route de Beille" in identified)
    s.check("and its receipt number", "Note n" in identified)
    # And when they are absent it says so on the bill, rather than printing a
    # note that quietly is not compliant.
    conn.execute("UPDATE company_info SET registration_number = NULL, vat_number = NULL "
                 "WHERE id = 1")
    conn.commit()
    bare = oc.get(f"/pos/{order['id']}/receipt").get_data(as_text=True)
    s.check("a missing SIRET is called out on the bill itself",
            "manquants" in bare)
    # A French bill shows VAT by rate. One blended figure is not a document a
    # restaurant can hand over.
    s.check("showing VAT by rate", "TVA 10%" in receipt)
    s.check("and the deposit the guest already paid", "Acompte" in receipt)

    # ------------------------------------------------ defects found in review
    s.section("VAT follows the money, not the menu price")
    # A €100 bill with €20 off charges €80, so the VAT is the VAT contained in
    # €80. The first version computed it from the untouched line total and
    # overstated it — and pos_day_report inherited that, so the day's VAT was
    # wrong on every discounted table.
    conn.execute("INSERT INTO pos_orders (table_label, covers, status, service_state, opened_at) "
                 "VALUES (?, 2, 'open', 'seated', ?)", (TAG + "VAT", now))
    conn.commit()
    v = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?", (TAG + "VAT",)).fetchone()
    conn.execute("""INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
                    state, vat_rate, created_at)
                    VALUES (?, ?, 100.0, 1, 'main', 'new', 10, ?)""",
                 (v["id"], TAG + "dish", now))
    conn.commit()
    plain = m.pos_bill(conn, v["id"])
    s.check("undiscounted VAT is the VAT within the price",
            abs(plain["vat_total"] - round(100 - 100 / 1.1, 2)) < 0.01,
            detail=str(plain["vat_total"]))

    conn.execute("UPDATE pos_orders SET discount_amount = 20, discount_reason = 'test' WHERE id = ?",
                 (v["id"],))
    conn.commit()
    disc = m.pos_bill(conn, v["id"])
    s.check("a discount reduces the VAT base",
            abs(disc["vat_total"] - round(80 - 80 / 1.1, 2)) < 0.01,
            detail=f"{disc['vat_total']} on a total of {disc['total']}")

    # A deposit is money already paid for the same meal, not a smaller meal.
    # Netting it off the VAT base would under-declare on every booked table.
    conn.execute("UPDATE pos_orders SET discount_amount = 0, deposit_credit = 40 WHERE id = ?",
                 (v["id"],))
    conn.commit()
    dep = m.pos_bill(conn, v["id"])
    s.check("a deposit does not reduce the VAT base",
            abs(dep["vat_total"] - round(100 - 100 / 1.1, 2)) < 0.01,
            detail=f"{dep['vat_total']} with {dep['total']} left to pay")
    s.check("it only reduces what is left to pay", dep["total"] == 60.0,
            detail=str(dep["total"]))

    # Two rates, one discount: it must be split between them, not taken off
    # whichever happens to be first.
    conn.execute("""INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, course,
                    state, vat_rate, created_at)
                    VALUES (?, ?, 100.0, 1, 'wine', 'new', 20, ?)""",
                 (v["id"], TAG + "wine", now))
    conn.execute("UPDATE pos_orders SET deposit_credit = 0, discount_amount = 20 WHERE id = ?",
                 (v["id"],))
    conn.commit()
    split = m.pos_bill(conn, v["id"])
    s.check("a discount is apportioned across both VAT rates",
            abs(split["vat_by_rate"][10.0]["gross"] - 90) < 0.01
            and abs(split["vat_by_rate"][20.0]["gross"] - 90) < 0.01,
            detail=str({k: x["gross"] for k, x in split["vat_by_rate"].items()}))

    s.section("A card payment made on the guest's phone lands")
    # metadata kind='pos' was set on the Stripe session and the webhook had no
    # branch for it, so the guest paid, Stripe took the money, and the tab
    # stayed open with the takings missing from the day.
    conn.execute("UPDATE pos_orders SET discount_amount = 0 WHERE id = ?", (v["id"],))
    conn.commit()
    fake_session = {"id": TAG + "sess1", "amount_total": 20000, "payment_status": "paid"}
    # Inside a request context because that is where it runs — the webhook is a
    # POST, and log_audit reads the session off it.
    with m.app.test_request_context():
        m.settle_pos_from_stripe_session(conn, fake_session, {"pos_order_id": str(v["id"])})
    conn.commit()
    after_pay = m.pos_bill(conn, v["id"])
    s.check("the payment is recorded against the tab", after_pay["paid"] == 200.0,
            detail=str(after_pay["paid"]))
    s.check("and the tab closes", after_pay["order"]["status"] == "paid",
            detail=after_pay["order"]["status"])
    # Stripe retries webhooks, and the guest's success redirect can beat them.
    with m.app.test_request_context():
        m.settle_pos_from_stripe_session(conn, fake_session, {"pos_order_id": str(v["id"])})
    conn.commit()
    s.check("a retried webhook does not pay twice",
            m.pos_bill(conn, v["id"])["paid"] == 200.0,
            detail=str(m.pos_bill(conn, v["id"])["paid"]))

    s.section("A kitchen note can be attached to a dish on the menu")
    # pos_add_menu_line accepted notes= from the start; the menu button never
    # sent one, so the only way to say "no butter" was the off-menu form.
    till = oc.get(f"/pos/{order['id']}").get_data(as_text=True)
    s.check("the till has a note field", 'name="notes"' in till and "note-input" in till)
    conn.execute("INSERT INTO pos_orders (table_label, covers, status, service_state, opened_at) "
                 "VALUES (?, 2, 'open', 'seated', ?)", (TAG + "NOTE", now))
    conn.commit()
    noted_tab = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                             (TAG + "NOTE",)).fetchone()
    oc.post(f"/pos/{noted_tab['id']}/add",
            data={"menu_item_id": dish["starter"], "notes": "no cream", "seat_number": "2"},
            follow_redirects=True)
    noted = conn.execute(
        "SELECT * FROM pos_order_lines WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (noted_tab["id"],)).fetchone()
    s.check("the note is stored on the line", bool(noted) and noted["notes"] == "no cream",
            detail=str(noted["notes"] if noted else None))
    oc.post(f"/pos/{noted_tab['id']}/send", follow_redirects=True)
    s.check("and reaches the pass", "no cream" in oc.get("/pos/kitchen").get_data(as_text=True))

    s.section("A dish shows in the right place on the public menu")
    # The public page groups by `category`; no form ever set it, so every dish
    # added since the POS rewrite landed under "main".
    oc.post("/admin/restaurant/menu/new",
            data={"name": TAG + "Sauternes", "course": "wine", "price": "40"},
            follow_redirects=True)
    added = conn.execute("SELECT * FROM menu_items WHERE name = ?", (TAG + "Sauternes",)).fetchone()
    s.check("a wine is categorised as a drink, not a main",
            bool(added) and added["category"] == "drink",
            detail=str(added["category"] if added else None))

    # ------------------------------------------------------------- cash up
    s.section("Cash up")
    report = m.pos_day_report(conn, today)
    s.check("the day counts the tab", report["tabs"] >= 1)
    s.check("takings are split by method",
            report["by_method"].get("cash") == 10.0
            and report["by_method"].get("card_terminal") == 13.0,
            detail=str(report["by_method"]))
    s.check("covers are counted for average spend", report["covers"] >= 2)
    s.check("the day page renders", oc.get("/pos/day").status_code == 200)

    # --------------------------------------------------------------- 86ing
    s.section("Taking a dish off")
    r = oc.post(f"/admin/restaurant/menu/{dish['main']}/availability",
                data={"note": "ran out"}, follow_redirects=True)
    item = conn.execute("SELECT * FROM menu_items WHERE id = ?", (dish["main"],)).fetchone()
    s.check("a dish can be taken off mid-service", item["available"] == 0, r)
    s.check("with the reason kept", item["unavailable_note"] == "ran out")
    conn.execute("INSERT INTO pos_orders (table_label, covers, status, service_state, opened_at) "
                 "VALUES (?, 2, 'open', 'seated', ?)", (TAG + "9", now))
    conn.commit()
    other = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                         (TAG + "9",)).fetchone()
    r = oc.post(f"/pos/{other['id']}/add", data={"menu_item_id": dish["main"]},
                follow_redirects=True)
    s.check("and it cannot then be sold",
            not conn.execute("SELECT 1 FROM pos_order_lines WHERE order_id = ?",
                             (other["id"],)).fetchone(), r)

    _cleanup(conn)
    conn.close()
    return s
