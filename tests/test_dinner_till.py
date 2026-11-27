"""Dinner at Gudanes, complete (owner's brief of 27 November 2026), part 4: into the till for the evening.

"Do the reservations link into the POS we built in case there are extras? I want it very developed."

A Dinner at Gudanes table is paid in full with the stay (or online, for a diner from outside), so the till must never
charge it again -- which crediting it as a deposit, or not at all, would have done.

  - TONIGHT ON THE TILL'S HOME: each Dinner at Gudanes table ready to seat, with the guest's name, room, covers and
    whether it is paid.
  - SEATED, PREPAID: the tab holds the dinner as a prepaid set menu -- one line, "Dinner at Gudanes × 2 -- prepaid
    with the stay", nothing to pay, tied to the stay's line -- and nothing is owed.
  - A BOTTLE ADDED: charged to the room by default for a staying guest, landing on the stay's bill as its own line at
    the alcohol rate; the prepaid dinner is not put on the bill a second time.
  - FROM OUTSIDE, PAID ONLINE: the same rule -- held as prepaid, not credited as a deposit.
  - A DEPOSIT-ONLY TABLE: still credits its deposit, as it always has.
  - THE NIGHT CLOSED: the prepaid covers counted apart, and the takings without them.
  - THE KITCHEN: the night on the pass -- covers by course, and each table's note read against the menu.

Nothing reaches Stripe or the mail.
"""
import html as htmllib
import re
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, flashes, keep_rows
import _harness

m = _harness.m
TAG = "ZTIL"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%'")]
    marks = ",".join("?" * len(ids)) or "NULL"
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasions WHERE id IN ({marks})", ids)
    orders = f"SELECT id FROM pos_orders WHERE table_label LIKE '{TAG}%'"
    conn.execute(f"DELETE FROM pos_payments WHERE order_id IN ({orders})")
    conn.execute(f"DELETE FROM pos_order_lines WHERE order_id IN ({orders})")
    conn.execute(f"DELETE FROM pos_order_formules WHERE order_id IN ({orders})")
    conn.execute(f"DELETE FROM pos_orders WHERE table_label LIKE '{TAG}%'")
    stays = f"SELECT id FROM bookings WHERE guest_name LIKE '{TAG}%'"
    conn.execute(f"DELETE FROM restaurant_bookings WHERE guest_name LIKE '{TAG}%' OR booking_id IN ({stays})")
    conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN ({stays})")
    conn.execute(f"DELETE FROM bookings WHERE guest_name LIKE '{TAG}%'")
    conn.commit()


def run():
    s = Suite("Dinner at Gudanes, complete: into the till, prepaid, with extras on the room")
    oc, _ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "restaurant_settings", "id = 1"),
                keep_rows(conn, "stay_occasions", "status = 'live'")]
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, conn)
    finally:
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _seat(oc, conn, booking_id):
    """Seat a booked table the way the till's home does, under a label this suite can find again."""
    rb = conn.execute("SELECT guest_name FROM restaurant_bookings WHERE id = ?", (booking_id,)).fetchone()
    r = oc.post("/pos/open", data={"restaurant_booking_id": str(booking_id),
                                   "table_label": f"{TAG} {rb['guest_name']}"}, follow_redirects=True)
    order = conn.execute("SELECT * FROM pos_orders WHERE restaurant_booking_id = ? ORDER BY id DESC LIMIT 1",
                         (booking_id,)).fetchone()
    return order, r


def _run(s, oc, conn):
    today = m.service_day()
    T = today.isoformat()
    room = ensure_room(min_occupancy=2)
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, dinner_time = '19:30' WHERE id = 1")
    conn.commit()
    name = f"{TAG} Tonight's dinner"
    form = {"name": name, "first_night": T, "last_night": T, "kind": "stay", "chef_on_site": "1",
            "status": "live", "photo": "", "dinner_section_shown": "1", "serve_dinner": "1",
            "description": "One night in the valley, with breakfast in the morning.",
            "dinner_price": "95", "dinner_covers": "12", "dinner_time": "19:30", "wine_included": "1",
            "wine_share": "30", "dinner_nights_shown": "1", "menu_choice_shown": "1",
            "dinner_menu_id": str(_harness.standing_menu(conn)), f"dinner_{T}": "1"}
    oc.post("/admin/stay-occasions/new", data=dict(form, status="draft"))
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=form)
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("a Dinner at Gudanes night tonight, live", occ["status"] == "live"
            and m.occasion_dinner_night(conn, T) is not None, detail=occ["status"])

    # A staying guest with dinner for two on their bill, as booking with the stay leaves it.
    now = _harness.datetime_now()
    sid = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email, guest_phone,
               arrival_date, departure_date, party_size, status, total_price, payment_status, created_at)
           VALUES (?, ?, ?, ?, 'stay.ztil@example.invalid', '+33 6 12 34 56 78', ?, ?, 2, 'confirmed', 0,
                   'paid', ?)""",
        (room["id"], m.make_reference_code(), m.secrets.token_urlsafe(16), f"{TAG} Staying guest", T,
         (today + timedelta(days=1)).isoformat(), now)).lastrowid
    stay = conn.execute("SELECT * FROM bookings WHERE id = ?", (sid,)).fetchone()
    line_id = m.add_dinner_to_stay(conn, stay, T, 2, 95, in_booking_total=True, note="no fish please")
    conn.commit()
    stay_table = conn.execute("SELECT * FROM restaurant_bookings WHERE booking_extra_id = ?", (line_id,)).fetchone()
    # A diner from outside, paid online in full; and an ordinary table that paid a deposit only.
    def table(who, total, deposit, status_paid):
        return conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email, party_size,
                   dinner_date, status, total_price, deposit_amount, payment_status, created_at)
               VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?, ?, ?, ?)""",
            (m.make_restaurant_reference_code(), m.secrets.token_urlsafe(12), f"{TAG} {who}",
             f"{who.split()[0].lower()}.ztil@example.invalid", T, total, deposit,
             "paid" if status_paid else "unpaid", now)).lastrowid
    online_id = table("Outside diner", 190, 190, True)
    deposit_id = table("Deposit table", 90, 30, True)
    conn.commit()

    # ------------------------------------------------------------------
    s.section("Tonight on the till's home: ready to seat, with the room and whether it is paid")
    home = htmllib.unescape(oc.get("/pos").get_data(as_text=True))
    at = home.find(f"{TAG} Staying guest")
    block = home[at:home.find("</form>", at)]
    s.check("the staying guest's table: Dinner at Gudanes, the room, two covers, prepaid with the stay",
            "Dinner at Gudanes" in block and room["name"] in block and "2 covers" in block
            and "prepaid with the stay" in block, detail=block[:300])
    s.check("and the note the kitchen needs", "no fish please" in block)
    at = home.find(f"{TAG} Outside diner")
    s.check("the outside diner: paid online", "paid online" in home[at:home.find("</form>", at)])
    at = home.find(f"{TAG} Deposit table")
    s.check("the deposit table: its deposit, as before", "€30.00 deposit" in home[at:home.find("</form>", at)])

    # ------------------------------------------------------------------
    s.section("Seated: the dinner held as prepaid, nothing owed")
    order, r = _seat(oc, conn, stay_table["id"])
    lines = conn.execute("SELECT * FROM pos_order_lines WHERE order_id = ?", (order["id"],)).fetchall()
    prepaid = [l for l in lines if l["prepaid_value"] is not None]
    s.check("one prepaid line, at nothing to pay", len(prepaid) == 1 and prepaid[0]["unit_price"] == 0
            and prepaid[0]["name"] == "Dinner at Gudanes × 2 — prepaid with the stay", detail=[dict(l) for l in lines])
    s.check("carrying what was paid and for how many, tied to the stay's line",
            prepaid and prepaid[0]["prepaid_value"] == 190 and prepaid[0]["prepaid_covers"] == 2
            and prepaid[0]["prepaid_extra_id"] == line_id)
    s.check("as a prepaid set menu", conn.execute(
        "SELECT prepaid, price FROM pos_order_formules WHERE order_id = ?", (order["id"],)).fetchone()[:] == (1, 0))
    s.check("no deposit credited on top of it", order["deposit_credit"] == 0)
    s.check("the tab is tied to the stay", order["room_booking_id"] == sid)
    bill = m.pos_bill(conn, order["id"])
    s.check("nothing owed", bill["outstanding"] == 0 and bill["total"] == 0, detail=(bill["total"], bill["outstanding"]))
    s.check("and the till says so as it opens", "nothing to pay for it here" in " ".join(flashes(r)), response=r)

    # ------------------------------------------------------------------
    s.section("A bottle added: on the room by default, on the stay's bill at the alcohol rate")
    bottle = m.pos_add_line(conn, order["id"], "Madiran AOP, la bouteille", 1, unit_price=38)
    conn.execute("UPDATE pos_order_lines SET vat_rate = ? WHERE id = ?", (m.tax_rate(conn, "vat_alcohol"), bottle))
    conn.commit()
    page = htmllib.unescape(oc.get(f"/pos/{order['id']}").get_data(as_text=True))
    s.check("the tab offers the guest's room first, by name", f"Charge €38.00 to {TAG} Staying guest’s room" in page)
    before = m.booking_bill(conn, sid)
    r = oc.post(f"/pos/{order['id']}/pay", data={"method": "room", "room_booking_id": str(sid)},
                follow_redirects=True)
    on_bill = conn.execute("SELECT * FROM booking_extras WHERE booking_id = ? AND name = 'Madiran AOP, la bouteille'",
                           (sid,)).fetchall()
    s.check("the bottle is a line of its own on the stay's bill", len(on_bill) == 1 and on_bill[0]["unit_price"] == 38,
            response=r)
    s.check("at the alcohol rate", on_bill and on_bill[0]["vat_rate"] == m.tax_rate(conn, "vat_alcohol"))
    s.check("the dinner is not put on the bill a second time", conn.execute(
        "SELECT COUNT(*) AS n FROM booking_extras WHERE booking_id = ? AND name LIKE 'Dinner at Gudanes%' "
        "AND COALESCE(status, '') != 'cancelled'", (sid,)).fetchone()["n"] == 1)
    after = m.booking_bill(conn, sid)
    s.check("the stay's bill rises by exactly the bottle", round(after["total"] - before["total"], 2) == 38.0,
            detail=(before["total"], after["total"]))
    s.check("and the tab is settled", conn.execute("SELECT status FROM pos_orders WHERE id = ?",
                                                    (order["id"],)).fetchone()["status"] == "paid")

    # ------------------------------------------------------------------
    s.section("From outside, paid online: prepaid too; a deposit-only table: its deposit")
    o2, _r = _seat(oc, conn, online_id)
    l2 = conn.execute("SELECT * FROM pos_order_lines WHERE order_id = ?", (o2["id"],)).fetchall()
    s.check("held as prepaid, paid online", len(l2) == 1 and l2[0]["prepaid_value"] == 190
            and l2[0]["name"] == "Dinner at Gudanes × 2 — paid online", detail=[dict(x) for x in l2])
    s.check("not credited as a deposit, nothing owed", o2["deposit_credit"] == 0
            and m.pos_bill(conn, o2["id"])["outstanding"] == 0)
    coffee = m.pos_add_line(conn, o2["id"], "Café", 2, unit_price=3)
    conn.commit()
    r = oc.post(f"/pos/{o2['id']}/pay", data={"method": "cash"}, follow_redirects=True)
    s.check("what they had besides is paid at the table: €6", conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM pos_payments WHERE order_id = ?", (o2["id"],)).fetchone()["t"] == 6,
        response=r)
    o3, r = _seat(oc, conn, deposit_id)
    s.check("a deposit-only table still credits its deposit", o3["deposit_credit"] == 30
            and not conn.execute("SELECT 1 FROM pos_order_lines WHERE order_id = ? AND prepaid_value IS NOT NULL",
                                 (o3["id"],)).fetchone(), response=r)
    m.pos_add_line(conn, o3["id"], "Dîner, La Table", 2, unit_price=45)
    conn.commit()
    oc.post(f"/pos/{o3['id']}/pay", data={"method": "card_terminal"})
    s.check("and takes the rest: €60", conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM pos_payments WHERE order_id = ?", (o3["id"],)).fetchone()["t"] == 60)

    # ------------------------------------------------------------------
    s.section("The night closed: the prepaid covers counted apart, the takings without them")
    report = m.pos_day_report(conn, today)
    mine = [o for o in report["orders"] if (o["table_label"] or "").startswith(TAG)]
    s.check("all three tabs are tonight's", len(mine) == 3, detail=[o["table_label"] for o in mine])
    s.check("four covers of Dinner at Gudanes prepaid, €380, counted apart",
            report["prepaid_covers"] >= 4 and report["prepaid_value"] >= 380, detail=(report["prepaid_covers"], report["prepaid_value"]))
    ours = round(sum(p["amount"] for p in conn.execute(
        f"SELECT amount FROM pos_payments WHERE order_id IN ({','.join(str(o['id']) for o in mine)})")), 2)
    s.check("what the till took for these tables: the bottle on the room, the coffees and La Table's rest, €104",
            ours == 104.0, detail=ours)
    s.check("and not a cent of the prepaid dinners in it", ours < 380)
    day = htmllib.unescape(oc.get("/pos/day").get_data(as_text=True))
    s.check("the cash-up page says the prepaid covers apart", 'id="prepaid-covers"' in oc.get("/pos/day").get_data(as_text=True)
            and "Dinner at Gudanes prepaid" in day and "not in the takings above" in day)

    # ------------------------------------------------------------------
    s.section("The kitchen: tonight's dinner on the pass, notes against the menu")
    kitchen = htmllib.unescape(oc.get("/pos/kitchen").get_data(as_text=True))
    sec = kitchen[kitchen.find('id="dinner-tonight"'):]
    s.check("the night, its time and its covers", "Dinner at Gudanes tonight, 19:30" in sec, detail=sec[:300])
    s.check("covers by course", "Truite de l’Ariège, beurre blanc" in sec)
    s.check("the staying guest's note, read against the menu: the trout is fish",
            "no fish please" in sec and "(Fish)" in sec, detail=sec[:800])
