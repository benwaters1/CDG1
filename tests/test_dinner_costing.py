"""Dinner at Gudanes, complete (owner's brief of 27 November 2026), part 3: the costing, proved end to end.

"Does the costing all work?" One stay walked from the quote to the cancellation: two nights, dinner for two on both
nights at €95 a person, wine included (of which €30 a person is the wine). At every step the figures must agree:

  - the quote on the room form: the room, the two dinners and the tourist tax add up to its total;
  - the card page: the dinners packed into its metadata as charged (pack_dinner_choices), and the card charged that
    total to the cent (Stripe stood in);
  - the stay written after payment: two dinner lines on its bill at €190 each, and two tables in the restaurant's
    book tied to them;
  - the guest's confirmation letter and the stay's page (mail stood in): the same lines and the same total;
  - the bill and the VAT statement: each dinner split into food at vat_food and wine at vat_alcohol by the wine's
    share, net and VAT stated, the rows adding up to the total;
  - what the house reports: the month's revenue and the trend chart, the VAT working -- the same day, the same numbers;
  - one night's dinner taken off by the owner: its table and its line cancelled, the bill lower by €190, and the
    €190 paid over a refund TO DECIDE, never issued;
  - the whole stay cancelled: both tables gone from the kitchen's list;
  - the owner's figures on the occasion: covers, gross, net, food and wine, and the margin from the menu's cost.

Negative control built in: the occasion's price changed after booking, and the stay keeps what was paid.

Nothing reaches Stripe or the mail.
"""
import html as htmllib
import re
from datetime import date, timedelta

from _harness import Suite, clients, db, ensure_room, flashes, free_window, keep_rows
import _harness

m = _harness.m
TAG = "ZCST"


class _Letters:
    def __enter__(self):
        self.sent, self._was = [], m.send_email
        m.send_email = lambda to, subject, body, *a, **kw: self.sent.append(
            {"to": to, "subject": subject, "body": body, "html": kw.get("html") or ""}) or True
        return self

    def __exit__(self, *_exc):
        m.send_email = self._was
        return False


class _Card:
    def __enter__(self):
        self.sessions = []
        self._enabled, self._create = m.stripe_enabled, m.stripe.checkout.Session.create
        m.stripe_enabled = lambda: True

        class _Made(dict):
            url = "https://checkout.example.invalid/zcst"
            id = "cs_test_zcst"

        def _session(**kw):
            self.sessions.append(kw)
            return _Made(**kw)
        m.stripe.checkout.Session.create = _session
        return self

    def __exit__(self, *_exc):
        m.stripe_enabled, m.stripe.checkout.Session.create = self._enabled, self._create
        return False


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%'")]
    marks = ",".join("?" * len(ids)) or "NULL"
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasions WHERE id IN ({marks})", ids)
    conn.execute(f"DELETE FROM dinner_menus WHERE name LIKE '{TAG}%'")
    stays = f"SELECT id FROM bookings WHERE guest_name LIKE '{TAG}%'"
    conn.execute(f"DELETE FROM restaurant_bookings WHERE booking_id IN ({stays}) OR guest_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM tasks WHERE title LIKE 'Refund to decide: {TAG}%'")
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN ({stays})")
    conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN ({stays})")
    conn.execute(f"DELETE FROM bookings WHERE guest_name LIKE '{TAG}%'")
    conn.commit()


def run():
    s = Suite("Dinner at Gudanes, complete: the costing, proved end to end")
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


def _month(d):
    start = d.replace(day=1)
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return start, end


def _run(s, oc, conn):
    anon = m.app.test_client()
    room = ensure_room(min_occupancy=2)
    rid = room["id"]
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, dinner_time = '19:30' WHERE id = 1")
    conn.commit()
    first = free_window(rid, 3, after_days=290, clear_of_ateliers=True, clear_of_rate_overrides=True)
    n0, n1 = first, first + timedelta(days=1)
    N0, N1 = n0.isoformat(), n1.isoformat()
    arrival, departure = N0, (n0 + timedelta(days=2)).isoformat()
    food_rate, wine_rate = float(m.tax_rate(conn, "vat_food")), float(m.tax_rate(conn, "vat_alcohol"))

    # The menu, with what a cover costs the kitchen.
    mid = conn.execute("INSERT INTO dinner_menus (name, price, wine_included, cost_per_cover, created_at) "
                       "VALUES (?, 95, 1, 40, ?)", (f"{TAG} Autumn menu", _harness.datetime_now())).lastrowid
    conn.execute("INSERT INTO dinner_menu_courses (menu_id, position, course_fr, dish_fr, dish_en) "
                 "VALUES (?, 0, 'Plat', 'Agneau des Pyrénées', 'Pyrenean lamb')", (mid,))
    conn.commit()
    name = f"{TAG} Two nights with dinner"
    form = {"name": name, "first_night": N0, "last_night": N1, "kind": "stay", "chef_on_site": "1",
            "status": "live", "photo": "", "dinner_section_shown": "1", "serve_dinner": "1",
            "description": "Two nights in the valley, with breakfast each morning.",
            "dinner_price": "95", "dinner_covers": "10", "dinner_time": "19:30", "wine_included": "1",
            "wine_share": "30", "dinner_nights_shown": "1", "menu_choice_shown": "1", "dinner_menu_id": str(mid),
            f"dinner_{N0}": "1", f"dinner_{N1}": "1"}
    oc.post("/admin/stay-occasions/new", data=dict(form, status="draft"))
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=form)
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("the occasion: live, €95 a person, wine included, €30 of it the wine", occ["status"] == "live"
            and occ["dinner_price"] == 95 and occ["wine_share"] == 30, detail=occ["status"])
    month_start, month_end = _month(n0)
    with m.app.test_request_context("/"):
        before = m.financial_month_summary(conn, month_start, month_end)
        trend_before = {r["month"]: r["revenue"] for r in m.financial_trend(conn, 14, today=n0)}

    # ------------------------------------------------------------------
    s.section("The quote on the room form: room, dinners and tourist tax add up to the total")
    q = anon.get(f"/api/quote?room_id={rid}&arrival={arrival}&departure={departure}&party_size=2"
                 f"&dinner_{N0}=2&dinner_{N1}=2").get_json()
    lines = q.get("lines") or []
    dinners = [l for l in lines if l.get("kind") == "dinner"]
    s.check("two dinner lines, €190 each", [l["amount"] for l in dinners] == [190.0, 190.0], detail=dinners)
    s.check("the lines add up to the total, to the cent",
            round(sum(l["amount"] for l in lines), 2) == round(q["total"], 2),
            detail=(round(sum(l["amount"] for l in lines), 2), q["total"], [(l.get("kind"), l["amount"]) for l in lines]))
    tax = [l for l in lines if (l.get("kind") or "") in ("tax", "city_tax", "tourist_tax")]
    s.check("the tourist tax is a line of its own", len(tax) == 1, detail=[l.get("kind") for l in lines])
    total = round(q["total"], 2)

    # ------------------------------------------------------------------
    s.section("The card page: the dinners in its metadata, the total charged to the cent")
    booking_form = {
        "arrival_date": arrival, "departure_date": departure, "guest_name": f"{TAG} Diner",
        "guest_email": "diner.zcst@example.invalid", "guest_phone": "+33 6 12 34 56 78",
        "party_size": "2", "adults": "2", "guests_under_18": "0", "agree_terms": "on",
        "guest_address": "1 Rue du Château", "guest_city": "Foix", "guest_postcode": "09000",
        "guest_country": "France", "estimated_arrival_time": "After 6pm",
        f"dinner_{N0}": "2", f"dinner_{N1}": "2"}
    with _Card() as card:
        r = anon.post(f"/book/{rid}", data=booking_form)
    made = card.sessions[-1] if card.sessions else {}
    meta = made.get("metadata") or {}
    charged = sum(i["price_data"]["unit_amount"] * i["quantity"] for i in made.get("line_items") or [])
    s.check("to the card page", r.status_code == 303, detail=r.status_code)
    s.check("the dinners packed as charged", meta.get("dinners") == m.pack_dinner_choices(
        [{"date": N0, "covers": 2, "price": 95}, {"date": N1, "covers": 2, "price": 95}])
        == f"{N0}:2:95.00,{N1}:2:95.00", detail=meta.get("dinners"))
    s.check("the card charged the quote's total, to the cent", charged == round(total * 100),
            detail=(charged, total))

    # Negative control: the price changes after the card page is made. What was charged is what is kept.
    conn.execute("UPDATE stay_occasions SET dinner_price = 120 WHERE id = ?", (occ["id"],))
    conn.commit()

    # ------------------------------------------------------------------
    s.section("Paid: the stay written, its dinner lines and the kitchen's tables")
    paid = {"id": "cs_test_zcst_paid", "metadata": meta, "amount_total": charged,
            "payment_intent": "pi_test_zcst", "payment_status": "paid"}
    with _Letters() as letters, m.app.test_request_context("/"):
        m.create_booking_from_stripe_session(conn, paid)
    stay = conn.execute("SELECT * FROM bookings WHERE stripe_session_id = ?", ("cs_test_zcst_paid",)).fetchone()
    s.check("the stay is written and confirmed", stay is not None and stay["status"] == "confirmed")
    if not stay:
        return
    dl = conn.execute("SELECT * FROM booking_extras WHERE booking_id = ? AND name LIKE 'Dinner at Gudanes%' "
                      "ORDER BY scheduled_for", (stay["id"],)).fetchall()
    s.check("two dinner lines, two covers each at €95, €190 each",
            [(l["quantity"], l["unit_price"]) for l in dl] == [(2, 95.0), (2, 95.0)],
            detail=[dict(l) for l in dl])
    s.check("the price changed after the card page did not reach the stay (negative control)",
            all(l["unit_price"] == 95 for l in dl))
    s.check("each split: €30 a cover the wine, at the alcohol rate; the rest food",
            all(l["vat_part_amount"] == 30 and l["vat_part_rate"] == wine_rate and l["vat_rate"] == food_rate
                for l in dl), detail=[(l["vat_rate"], l["vat_part_amount"], l["vat_part_rate"]) for l in dl])
    tables = conn.execute("SELECT * FROM restaurant_bookings WHERE booking_id = ? ORDER BY dinner_date",
                          (stay["id"],)).fetchall()
    s.check("two tables, confirmed, each tied to its line",
            [(t["dinner_date"], t["party_size"], t["status"]) for t in tables]
            == [(N0, 2, "confirmed"), (N1, 2, "confirmed")]
            and [t["booking_extra_id"] for t in tables] == [l["id"] for l in dl])

    # ------------------------------------------------------------------
    s.section("The confirmation letter and the stay's page: the same lines, the same total")
    letter = next((x for x in letters.sent if (x["to"] or "").lower() == "diner.zcst@example.invalid"), None)
    said = htmllib.unescape(re.sub(r"<[^>]+>", " ", (letter or {}).get("html") or (letter or {}).get("body") or ""))
    s.check("the guest is written to", letter is not None, detail=[x["to"] for x in letters.sent])
    s.check("the letter names both dinners", m.dinner_nights_words([N0]) in said and m.dinner_nights_words([N1]) in said,
            detail=said[:600])
    bill = m.booking_bill(conn, stay["id"])
    s.check("the bill's total is the quote's", round(bill["total"], 2) == total, detail=(bill["total"], total))
    s.check("and nothing is owed", bill["owed"] < 0.005, detail=bill["owed"])
    page = htmllib.unescape(anon.get(f"/book/manage/{stay['manage_token']}").get_data(as_text=True))
    s.check("the stay's page: both dinners at 2 × €95.00",
            page.count("2 × €95.00") >= 2, detail=page.count("2 × €95.00"))

    # ------------------------------------------------------------------
    s.section("The VAT statement: food and wine at their rates, net and VAT stated, rows adding up")
    with m.app.test_request_context("/"):
        st = m.guest_statement(conn, stay)
    bands = {b["rate"]: b for b in st["vat"]}
    food_gross = round(2 * 2 * (95 - 30), 2)
    wine_gross = round(2 * 2 * 30, 2)
    s.check(f"the dinners' food at {food_rate:g}%: €{food_gross:.2f} gross",
            food_rate in bands and round(bands[food_rate]["gross"], 2) >= food_gross, detail=bands)
    s.check(f"the wine at {wine_rate:g}%: €{wine_gross:.2f}", wine_rate in bands
            and round(bands[wine_rate]["gross"], 2) >= wine_gross, detail=bands)
    s.check("each band: net and VAT add up to its gross",
            all(round(b["net"] + b["vat"], 2) == round(b["gross"], 2) for b in st["vat"]))
    s.check("the bands and the tourist tax add up to the total",
            round(sum(b["gross"] for b in st["vat"]) + st["city_tax"], 2) == round(st["total"], 2) == total,
            detail=(sum(b["gross"] for b in st["vat"]), st["city_tax"], st["total"], total))
    doc = htmllib.unescape(anon.get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True))
    s.check("the statement page states gross, net and VAT, the food's rate and the wine's",
            all(w in doc for w in ("Net", "VAT", "Gross")) and f"{food_rate:.1f}%" in doc and f"{wine_rate:.1f}%" in doc)

    # ------------------------------------------------------------------
    s.section("What the house reports: the same day, the same numbers")
    with m.app.test_request_context("/"):
        after = m.financial_month_summary(conn, month_start, month_end)
        trend_after = {r["month"]: r["revenue"] for r in m.financial_trend(conn, 14, today=n0)}
    gained = round(after["revenue"] - before["revenue"], 2)
    s.check("the month's revenue rises by exactly what the stay agreed", gained == round(stay["total_price"], 2),
            detail=(gained, stay["total_price"]))
    s.check("which is the bill's total less the tourist tax the house only collects",
            round(stay["total_price"], 2) in (round(bill["total"], 2), round(bill["total"] - st["city_tax"], 2)),
            detail=(stay["total_price"], bill["total"], st["city_tax"]))
    s.check("the chart above it says the same", round(trend_after[month_start] - trend_before[month_start], 2) == gained,
            detail=(trend_after.get(month_start), trend_before.get(month_start)))
    s.check("and the restaurant's tables are not counted a second time",
            after["revenue"] - before["revenue"] < round(stay["total_price"], 2) + 1)

    # ------------------------------------------------------------------
    s.section("One night's dinner taken off: line and table cancelled, the bill lower, the refund to decide")
    refunds = conn.execute("SELECT COUNT(*) AS n FROM refunds WHERE booking_id = ?", (stay["id"],)).fetchone()["n"]
    r = oc.post(f"/admin/bookings/extras/{dl[1]['id']}/cancel", follow_redirects=True)
    line = conn.execute("SELECT * FROM booking_extras WHERE id = ?", (dl[1]["id"],)).fetchone()
    table = conn.execute("SELECT * FROM restaurant_bookings WHERE booking_extra_id = ?", (dl[1]["id"],)).fetchone()
    bill2 = m.booking_bill(conn, stay["id"])
    s.check("its line cancelled", line["status"] == "cancelled")
    s.check("its table cancelled", table["status"] == "cancelled")
    s.check("the bill €190 lower", round(bill["total"] - bill2["total"], 2) == 190.0, detail=(bill["total"], bill2["total"]))
    s.check("€190 paid over it", round(bill2["overpaid"], 2) == 190.0, detail=bill2["overpaid"])
    task = conn.execute("SELECT * FROM tasks WHERE title = ? AND status != 'done'",
                        (f"Refund to decide: {stay['guest_name']} ({stay['reference_code']})",)).fetchone()
    s.check("a refund to decide, on the owner's list", task is not None and "€190.00" in task["notes"],
            detail=task and task["notes"])
    s.check("and nothing refunded", conn.execute("SELECT COUNT(*) AS n FROM refunds WHERE booking_id = ?",
                                                 (stay["id"],)).fetchone()["n"] == refunds)
    s.check("said on the page", "a refund to decide" in " ".join(flashes(r)), response=r)
    with m.app.test_request_context("/"):
        after2 = m.financial_month_summary(conn, month_start, month_end)
    s.check("the month's revenue falls by the same €190", round(after["revenue"] - after2["revenue"], 2) == 190.0,
            detail=(after["revenue"], after2["revenue"]))

    # ------------------------------------------------------------------
    s.section("The owner's figures on the occasion: covers, gross and net, food and wine, the margin")
    with m.app.test_request_context("/"):
        figs = m.occasion_dinner_figures(conn, occ)
    rows = {r["date"]: r for r in figs["nights"]}
    r0 = rows.get(N0)
    s.check("the first night: 2 of 10 covers", r0 and r0["booked"] == 2 and r0["covers"] == 10, detail=r0)
    s.check("€190 gross, €60 of it wine", r0 and r0["gross"] == 190.0 and r0["wine"] == 60.0, detail=r0)
    net_food = round(130 / (1 + food_rate / 100), 2)
    net_wine = round(60 / (1 + wine_rate / 100), 2)
    s.check("net, food and wine each at its own rate", r0 and r0["net_food"] == net_food and r0["net_wine"] == net_wine
            and r0["net"] == round(net_food + net_wine, 2), detail=r0)
    s.check("the margin: net less €40 a cover", r0 and r0["cost"] == 80.0
            and r0["margin"] == round(net_food + net_wine - 80, 2), detail=r0)
    s.check("the second night, taken off: nothing booked", rows.get(N1) and rows[N1]["booked"] == 0
            and rows[N1]["gross"] == 0)
    s.check("the rows add up to the totals", figs["total"]["gross"] == round(sum(r["gross"] for r in figs["nights"]), 2)
            and figs["total"]["booked"] == sum(r["booked"] for r in figs["nights"]))
    page = htmllib.unescape(oc.get(f"/admin/stay-occasions/{occ['id']}").get_data(as_text=True))
    s.check("on the occasion's form, with a link to each night's list",
            'id="dinner-figures"' in page and "2 of 10" in page and f"/admin/dinner-night/{N0}" in page)

    # ------------------------------------------------------------------
    s.section("The whole stay cancelled: both tables gone from the kitchen's list")
    with _Letters():
        oc.post(f"/admin/bookings/{stay['id']}/cancel", data={"cancel_reason": "changed plans"})
    stay = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["id"],)).fetchone()
    s.check("the stay is cancelled", stay["status"] == "cancelled", detail=stay["status"])
    left = conn.execute("SELECT COUNT(*) AS n FROM restaurant_bookings WHERE booking_id = ? "
                        "AND status IN ('pending', 'confirmed')", (stay["id"],)).fetchone()["n"]
    s.check("no table of it left in the restaurant's book", left == 0, detail=left)
    with m.app.test_request_context("/"):
        sheet = m.dinner_night_sheet(conn, N0)
    s.check("and none on the night's sheet", not any(t["booking_id"] == stay["id"] for t in sheet["tables"]))
    s.check("nothing refunded by the cancellation itself", conn.execute(
        "SELECT COUNT(*) AS n FROM refunds WHERE booking_id = ?", (stay["id"],)).fetchone()["n"] == refunds)
