"""Dinner at Gudanes, complete (owner's brief of 27 November 2026), part 2: with or without dinner, every guest path.

"Can you select with or without dinner ... no gaps." Each path walked as a guest, one section each:

   1. The occasion's page, Book dinner: /book, a room, and the room form arrives with dinner for the whole party on
      every dinner night (as far as the places allow), a line of its own in Your booking; lowered or unticked a night
      at a time, the total follows at once.
   2. Stay these nights: the same nights offered, unticked, each with its price and See the menu.
   3. /book on those dates, arriving any other way: the same offer, unticked.
   4. /book's own words on those dates: "Dinner at Gudanes is served on ...", and dinner off the "not included" line.
   5. The stay's page: a night not added can be added; one added can be taken off up to the evening before, the
      house's refund rule said, and nothing refunded -- whatever is paid over is a task for the owner.
   6. The letter before arrival offers the dinner nights whether or not La Table is open.
   7. A Booking.com guest, once checked in, is offered the dinner nights on their page, and counts as staying.
   8. Not staying: with the dinner open to outside guests, Book dinner books a table for that night, priced the
      same, from the same covers, paid in full, while La Table itself is switched off; closed to them, it is not.
   9. A guest on a retreat (every meal included) is never offered a paid dinner on its nights.
  10. Full: the panel says "Full on ...", Book dinner offers the other night, and with none left, the waitlist.
  11. The cut-off: the evening before. After it the panel says booking has closed.
  12. On a 375px phone: none of these pages is wider than the screen, the panel's buttons a thumb can press; and in
      a real browser the room form's total follows a dinner lowered.

Nothing reaches Stripe or the mail: the card page and send_email are stood in; the browser resolves no host but
the app's own, served on a local port.
"""
import html as htmllib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, flashes, free_window, keep_rows
import _harness
from test_staff_header_on_a_phone import find_browser

m = _harness.m
ROOT = _harness.ROOT
TAG = "ZGPT"


class _Letters:
    def __enter__(self):
        self.sent, self._was = [], m.send_email
        m.send_email = lambda to, subject, body, *a, **kw: self.sent.append(
            {"to": to, "subject": subject, "body": body}) or True
        return self

    def __exit__(self, *_exc):
        m.send_email = self._was
        return False


class _Card:
    """Stripe's checkout, stood in: records what it was asked to charge."""

    def __enter__(self):
        self.sessions = []
        self._enabled, self._create = m.stripe_enabled, m.stripe.checkout.Session.create
        m.stripe_enabled = lambda: True

        class _Made(dict):
            url = "https://checkout.example.invalid/zgpt"
            id = "cs_test_zgpt"

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
    conn.execute(f"DELETE FROM restaurant_bookings WHERE guest_name LIKE '{TAG}%' OR guest_email LIKE '%.zgpt@example.invalid'")
    conn.execute(f"DELETE FROM restaurant_waitlist WHERE name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM tasks WHERE title LIKE 'Refund to decide: {TAG}%'")
    stays = f"SELECT id FROM bookings WHERE guest_name LIKE '{TAG}%'"
    conn.execute(f"DELETE FROM workshop_bookings WHERE guest_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM workshop_sessions WHERE workshop_id IN (SELECT id FROM workshops WHERE title LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM workshops WHERE title LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN ({stays})")
    conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN ({stays})")
    conn.execute(f"DELETE FROM bookings WHERE guest_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM ota_reservations WHERE guest_name LIKE '{TAG}%'")
    conn.commit()


def _stay(conn, rid, arrival, departure, name, email, party=2):
    """A confirmed stay, paid for its room, as the card page would leave it."""
    now = _harness.datetime_now()
    sid = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email, guest_phone,
               arrival_date, departure_date, party_size, status, total_price, payment_status, created_at)
           VALUES (?, ?, ?, ?, ?, '+33 6 12 34 56 78', ?, ?, ?, 'confirmed', 0, 'paid', ?)""",
        (rid, m.make_reference_code(), m.secrets.token_urlsafe(20), name, email, arrival, departure,
         party, now)).lastrowid
    _pay_up(conn, sid)
    return conn.execute("SELECT * FROM bookings WHERE id = ?", (sid,)).fetchone()


def _pay_up(conn, booking_id):
    """Pay what the bill says is owed, as a card payment would."""
    bill = m.booking_bill(conn, booking_id)
    owed = round(bill["owed"], 2)
    if owed > 0.005:
        # What the bill reads as paid is the stay's amount_paid; the payment row is the record of it.
        conn.execute("INSERT INTO booking_payments (booking_id, amount, method, created_at) VALUES (?, ?, 'stripe', ?)",
                     (booking_id, owed, _harness.datetime_now()))
        conn.execute("UPDATE bookings SET amount_paid = COALESCE(amount_paid, 0) + ? WHERE id = ?", (owed, booking_id))
    conn.commit()


def _panel(page):
    at = page.find('<section class="g-dinpanel"')
    return page[at:page.find("</section>", at)] if at >= 0 else ""


def _href(block, label):
    """(path, query) of the link whose text is `label`."""
    for a in re.finditer(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', block, re.S):
        if re.sub(r"<[^>]+>", "", a.group(2)).strip() == label:
            u = urllib.parse.urlsplit(htmllib.unescape(a.group(1)))
            return u.path, dict(urllib.parse.parse_qsl(u.query)), u.fragment
    return None, {}, ""


def _restaurant(anon, conn, data, **kw):
    """A post to the restaurant's page, with the rate limiter's count cleared first: five an hour from one
    address is the limit, and a whole run comes from one address."""
    conn.execute("DELETE FROM submission_log WHERE action = 'book_restaurant'")
    conn.commit()
    return anon.post("/restaurant/book", data=data, **kw)


def _box(page, night):
    got = re.search(r'<input type="number"[^>]*name="dinner_' + re.escape(night) + r'"[^>]*value="(\d+)"', page)
    return int(got.group(1)) if got else None


def run():
    s = Suite("Dinner at Gudanes, complete: with or without dinner, every guest path")
    oc, _ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "restaurant_settings", "id = 1"),
                keep_rows(conn, "app_settings", "key IN ('dining_days', 'dining_mode')"),
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


def _run(s, oc, conn):
    anon = m.app.test_client()
    room = ensure_room(min_occupancy=2)
    rid = room["id"]
    # The guests' stays below go in another room, so the room the form books stays free on these dates.
    stay_room = conn.execute("SELECT id FROM rooms WHERE id != ? ORDER BY id LIMIT 1", (rid,)).fetchone()["id"]
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, price_per_person = 45, "
                 "dinner_time = '19:30', deposit_percent = 30 WHERE id = 1")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_mode', 'open') "
                 "ON CONFLICT(key) DO UPDATE SET value = 'open'")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_days', '') "
                 "ON CONFLICT(key) DO UPDATE SET value = ''")
    conn.commit()
    first = free_window(rid, 5, after_days=260, clear_of_ateliers=True, clear_of_rate_overrides=True)
    n0, n1 = first, first + timedelta(days=1)
    N0, N1 = n0.isoformat(), n1.isoformat()
    arrival, departure = N0, (n0 + timedelta(days=2)).isoformat()
    name = f"{TAG} An autumn dinner"
    form = {"name": name, "first_night": N0, "last_night": N1, "kind": "stay", "chef_on_site": "1",
            "status": "live", "photo": "", "dinner_section_shown": "1", "serve_dinner": "1",
            "description": "Two nights in the valley in autumn, with breakfast each morning.",
            "dinner_price": "95", "dinner_covers": "6", "dinner_time": "19:30", "wine_included": "1",
            "wine_share": "30", "dinner_nights_shown": "1", "menu_choice_shown": "1",
            "dinner_menu_id": str(_harness.standing_menu(conn)), f"dinner_{N0}": "1", f"dinner_{N1}": "1"}
    oc.post("/admin/stay-occasions/new", data=dict(form, status="draft"))
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()
    oc.post(f"/admin/stay-occasions/{occ['id']}", data=form)
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()
    s.check("a live occasion with dinner on both its nights, at €95, for six", occ["status"] == "live"
            and m.stay_occasion_dinners(occ, conn) == [N0, N1], detail=occ["status"])
    occ_page = f"/whats-on/at-gudanes/{occ['slug']}"
    w0, w1 = m.dinner_nights_words([N0]), m.dinner_nights_words([N1])
    short0, short1 = m.dinner_night_short(N0), m.dinner_night_short(N1)

    # ------------------------------------------------------------------
    s.section("1. Book dinner: through /book to the room, dinner added for the party, a line of its own")
    panel = _panel(anon.get(occ_page).get_data(as_text=True))
    path, q, _f = _href(panel, "Book dinner")
    s.check("Book dinner opens /book on the dates, with the dinner flag",
            path == "/book" and q == {"arrival": arrival, "departure": departure, "dinner": "1"}, detail=f"{path} {q}")
    rooms_page = anon.get(f"/book?arrival={arrival}&departure={departure}&dinner=1").get_data(as_text=True)
    s.check("and every Book this room there carries it on", f"/book/{rid}?" in rooms_page
            and re.search(rf'href="/book/{rid}\?[^"]*dinner=1', rooms_page) is not None)
    form_url = f"/book/{rid}?arrival={arrival}&departure={departure}"
    page = anon.get(form_url + "&party_size=2&dinner=1").get_data(as_text=True)
    s.check("the room form: dinner for two on BOTH dinner nights", _box(page, N0) == 2 and _box(page, N1) == 2,
            detail=(_box(page, N0), _box(page, N1)))
    plain = htmllib.unescape(page)
    s.check("each a line of its own in Your booking, with its price",
            f"Dinner at Gudanes, {short0}, 2 × €95" in plain and f"Dinner at Gudanes, {short1}, 2 × €95" in plain)
    s.check("a party of one: one each", _box(anon.get(form_url + "&party_size=1&dinner=1").get_data(as_text=True), N0) == 1)
    base = f"/api/quote?room_id={rid}&arrival={arrival}&departure={departure}&party_size=2"
    both = anon.get(base + f"&dinner_{N0}=2&dinner_{N1}=2").get_json()
    lower = anon.get(base + f"&dinner_{N0}=2&dinner_{N1}=1").get_json()
    off = anon.get(base + f"&dinner_{N0}=2&dinner_{N1}=0").get_json()
    s.check("lowered to one on the second night: the total falls by exactly one dinner",
            round(both["total"] - lower["total"], 2) == 95.0, detail=(both["total"], lower["total"]))
    s.check("unticked: by exactly two, and its line goes",
            round(both["total"] - off["total"], 2) == 190.0
            and not any(short1 in (l.get("label") or "") for l in off["lines"] if l.get("kind") == "dinner"))
    s.check("the form asks for the total again as a dinner box changes", "li[data-dinner]" in page
            and ("'input'" in page or '"input"' in page))

    # ------------------------------------------------------------------
    s.section("2 and 3. Stay these nights, or /book by any other way: the dinner nights offered, unticked")
    path, q, _f = _href(panel, "Stay these nights")
    s.check("Stay these nights: /book on the dates, no flag", path == "/book" and q == {
        "arrival": arrival, "departure": departure}, detail=f"{path} {q}")
    plain_rooms = anon.get(f"/book?arrival={arrival}&departure={departure}").get_data(as_text=True)
    path, q, _f = _href(plain_rooms[plain_rooms.find(f'id="room-{rid}"'):], "Book this room")
    s.check("and Book this room carries none", path == f"/book/{rid}" and "dinner" not in q, detail=f"{path} {q}")
    page = anon.get(form_url + "&party_size=2").get_data(as_text=True)
    s.check("the room form offers both nights, unticked", _box(page, N0) == 0 and _box(page, N1) == 0,
            detail=(_box(page, N0), _box(page, N1)))
    li0 = re.search(r'<li class="g-dinnerx" data-dinner="' + N0 + r'".*?</li>', page, re.S)
    li_text = htmllib.unescape(li0.group(0)) if li0 else ""
    s.check("each with its price", "€95 a person, wine included" in li_text, detail=li_text[:300])
    s.check("and See the menu", "data-menu-open" in li_text and "See the menu" in li_text)
    s.check("the room card on /book says it under the room's price", "is hosted on" in htmllib.unescape(plain_rooms)
            and "€95 a person, wine included" in htmllib.unescape(plain_rooms))

    # ------------------------------------------------------------------
    s.section("4. /book's own words on those dates")
    said = htmllib.unescape(plain_rooms)
    served = f"Dinner at Gudanes is served on {m.dinner_nights_words([N0, N1])}, 7.30pm, €95 a person, wine included"
    s.check("Key information: Dinner at Gudanes is served on the nights, the time, the price — add it below",
            served + " — add it below." in said, detail=said[said.find("g-key__dinner") - 10:said.find("g-key__dinner") + 300])
    s.check("the not-included line drops dinner", "Not included in a stay: lunch and drinks." in said
            and "Not included in a stay: lunch, dinner and drinks." not in said)
    s.check("the question about meals says it too", "Breakfast is. " + served + ", added when you book your room." in said)
    s.check("and the notice before booking does not send them to restaurants nearby for dinner",
            "On your dates the château hosts Dinner at Gudanes" in said)
    away = free_window(rid, 2, after_days=(n0 - m.house_today()).days + 20, clear_of_ateliers=True)
    other = htmllib.unescape(anon.get(f"/book?arrival={away.isoformat()}&departure={(away + timedelta(days=1)).isoformat()}")
                             .get_data(as_text=True))
    s.check("on dates with no dinner night, the page is as before",
            "Not included in a stay: lunch, dinner and drinks." in other and "is served on" not in other
            and "Les Cabannes is just down the hill" in other)

    # ------------------------------------------------------------------
    s.section("5. The stay's page: add a dinner night; take one off up to the evening before, refund left to the owner")
    stay = _stay(conn, stay_room, arrival, departure, f"{TAG} Staying guest", "stay.zgpt@example.invalid")
    mpage = f"/book/manage/{stay['manage_token']}"
    page = htmllib.unescape(anon.get(mpage).get_data(as_text=True))
    s.check("both dinner nights offered to add", f"Dinner at Gudanes, {w0}" in page and f"Dinner at Gudanes, {w1}" in page)
    with _Letters():
        r = anon.post(mpage, data={"action": "add_dinner", "night": N0, "covers": "2", "dinner_note": "no nuts"},
                      follow_redirects=True)
    line = conn.execute("SELECT * FROM booking_extras WHERE booking_id = ? AND status != 'cancelled' "
                        "AND name LIKE 'Dinner at Gudanes%'", (stay["id"],)).fetchone()
    table = conn.execute("SELECT * FROM restaurant_bookings WHERE booking_extra_id = ?",
                         (line["id"] if line else -1,)).fetchone()
    s.check("added: a line on the bill and a table, tied", line is not None and table is not None
            and line["quantity"] == 2 and line["unit_price"] == 95 and table["party_size"] == 2, response=r)
    _pay_up(conn, stay["id"])
    page = anon.get(mpage).get_data(as_text=True)
    s.check("the page offers Take it off, with the house's rule beside it",
            "Take it off" in page and m.DINNER_REMOVE_RULE in htmllib.unescape(page))
    refunds_before = conn.execute("SELECT COUNT(*) AS n FROM refunds WHERE booking_id = ?", (stay["id"],)).fetchone()["n"]
    with _Letters() as letters:
        r = anon.post(mpage, data={"action": "remove_dinner", "line_id": str(line["id"])}, follow_redirects=True)
    line = conn.execute("SELECT * FROM booking_extras WHERE id = ?", (line["id"],)).fetchone()
    table = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (table["id"],)).fetchone()
    s.check("taken off: its line cancelled", line["status"] == "cancelled", detail=line["status"])
    s.check("and its table, so the kitchen is not cooking for it", table["status"] == "cancelled")
    s.check("nothing refunded", conn.execute("SELECT COUNT(*) AS n FROM refunds WHERE booking_id = ?",
                                             (stay["id"],)).fetchone()["n"] == refunds_before)
    task = conn.execute("SELECT * FROM tasks WHERE title = ? AND status != 'done'",
                        (f"Refund to decide: {stay['guest_name']} ({stay['reference_code']})",)).fetchone()
    s.check("the €190 paid over is a task for the owner, to decide", task is not None and "€190.00" in task["notes"]
            and "Nothing has been refunded" in task["notes"], detail=task and task["notes"])
    s.check("and the house is told", any("took Dinner at Gudanes" in l["body"] for l in letters.sent))
    s.check("the guest is told the rule", m.DINNER_REMOVE_RULE in " ".join(flashes(r)), response=r)
    with _Letters():
        anon.post(mpage, data={"action": "add_dinner", "night": N1, "covers": "2"})
    l1 = conn.execute("SELECT id FROM booking_extras WHERE booking_id = ? AND status != 'cancelled' "
                      "AND name LIKE 'Dinner at Gudanes%'", (stay["id"],)).fetchone()
    line_row, why, _o = m.remove_dinner_from_stay(conn, stay, l1["id"], today=n1)
    conn.rollback()
    s.check("on the night itself it can no longer be taken off, and says why",
            line_row is None and "can no longer be taken off" in (why or ""), detail=why)
    with m.app.test_request_context("/"):
        letter = m.stay_dinner_letter(conn, stay, today=n1)
    s.check("and the page offers no Take it off then", not any(d["removable"] for d in letter["booked"]))

    # ------------------------------------------------------------------
    s.section("6. The letter before arrival offers the dinner nights, La Table open or not")
    conn.execute("UPDATE restaurant_settings SET enabled = 0 WHERE id = 1")
    conn.execute("UPDATE app_settings SET value = 'closed' WHERE key = 'dining_mode'")
    conn.commit()
    fresh = _stay(conn, stay_room, arrival, departure, f"{TAG} Letter guest", "letter.zgpt@example.invalid")
    with m.app.test_request_context("/"):
        cards = m.extras_offers_for_stay(conn, fresh, today=m.house_today())
    card = next((c for c in cards if c["kind"] == "dinner_at_gudanes"), None)
    s.check("with La Table closed, Dinner at Gudanes is offered", card is not None, detail=[c["kind"] for c in cards])
    s.check("its nights, time and price", card and card["description"] == m.dinner_nights_words([N0, N1]) + ", 7.30pm"
            and card["price"] == "€95 a person, wine included", detail=card)
    s.check("leading to the stay's page to add it", card and card["url"].endswith("#dinner-at-gudanes"))
    conn.execute("UPDATE restaurant_settings SET enabled = 1 WHERE id = 1")
    conn.execute("UPDATE app_settings SET value = 'open' WHERE key = 'dining_mode'")
    conn.commit()
    with m.app.test_request_context("/"):
        evenings = m.dinner_offer_evenings(conn, fresh, m.house_today())
    s.check("with La Table open, its own card never offers an occasion's night again",
            n0 not in evenings and n1 not in evenings, detail=evenings)

    # ------------------------------------------------------------------
    s.section("7. A Booking.com guest: once checked in, the dinner nights on their page; staying, for the dinner")
    tok = m.secrets.token_urlsafe(16)
    ota = conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name, arrival_date,
               departure_date, room_id, guests, adults, children, first_seen_at, updated_at, guest_email, checkin_token)
           VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, ?, 2, 2, 0, ?, ?, 'ota.zgpt@example.invalid', ?)""",
        (f"ZG{tok[:8]}", f"{TAG} Booking guest", arrival, departure, rid, _harness.datetime_now(),
         _harness.datetime_now(), tok)).lastrowid
    conn.commit()
    page = htmllib.unescape(anon.get(f"/checkin/stay/{tok}").get_data(as_text=True))
    s.check("before checking in, nothing about dinner yet", "Book dinner on" not in page)
    conn.execute("UPDATE ota_reservations SET checkin_done_at = ? WHERE id = ?", (_harness.datetime_now(), ota))
    conn.commit()
    raw = anon.get(f"/checkin/stay/{tok}").get_data(as_text=True)
    page = htmllib.unescape(raw)
    s.check("checked in: both nights offered", f"Book dinner on {short0}" in page and f"Book dinner on {short1}" in page)
    path, q, frag = _href(raw, f"Book dinner on {short0}")
    s.check("each to the restaurant's page for that night, their details filled",
            path == "/restaurant/book" and q.get("date") == N0 and q.get("email") == "ota.zgpt@example.invalid"
            and q.get("party_size") == "2" and q.get("name") == f"{TAG} Booking guest", detail=f"{path} {q}")
    s.check("on a staying-guests-only dinner, they count as staying",
            m.occasion_dinner_refusal(conn, n0, "ota.zgpt@example.invalid") is None
            and m.occasion_dinner_refusal(conn, n0, "stranger.zgpt@example.invalid") is not None)
    with _Card() as card_machine:
        r = _restaurant(anon, conn, {
            "guest_name": f"{TAG} Booking guest", "guest_email": "ota.zgpt@example.invalid",
            "guest_phone": "+33 6 12 34 56 78", "dinner_date": N0, "party_size": "2"})
    made = card_machine.sessions[-1] if card_machine.sessions else {}
    amount = sum(i["price_data"]["unit_amount"] * i["quantity"] for i in made.get("line_items") or [])
    s.check("and pay for it in full: €190 for two, no deposit", r.status_code == 303 and amount == 19000,
            detail=f"{r.status_code} {amount}")

    # ------------------------------------------------------------------
    s.section("8. Not staying: a dinner-only table, the same price, the same covers, La Table switched off")
    conn.execute("UPDATE stay_occasions SET outside_diners = 1 WHERE id = ?", (occ["id"],))
    conn.execute("UPDATE restaurant_settings SET enabled = 0 WHERE id = 1")
    conn.commit()
    panel = _panel(anon.get(occ_page).get_data(as_text=True))
    path, q, _f = _href(panel, "Book dinner")
    s.check("open to them: the panel says so, and Book dinner books a table for the night",
            "Open to guests from outside the château too." in htmllib.unescape(panel)
            and path == "/restaurant/book" and q == {"date": N0}, detail=f"{path} {q}")
    page = anon.get(f"/restaurant/book?date={N0}").get_data(as_text=True)
    s.check("with La Table switched off, the page still takes this night", 'id="restaurant_book-form"' in page
            and "only these evenings can be booked" in page)
    with _Card() as card_machine:
        r = _restaurant(anon, conn, {
            "guest_name": f"{TAG} Outside diner", "guest_email": "outside.zgpt@example.invalid",
            "guest_phone": "+33 6 12 34 56 78", "dinner_date": N1, "party_size": "2"})
    made = card_machine.sessions[-1] if card_machine.sessions else {}
    amount = sum(i["price_data"]["unit_amount"] * i["quantity"] for i in made.get("line_items") or [])
    s.check("priced the same as a staying guest's, paid in full", r.status_code == 303 and amount == 19000,
            detail=f"{r.status_code} {amount}")
    with _Card() as card_machine:
        r = _restaurant(anon, conn, {
            "guest_name": f"{TAG} Outside diner", "guest_email": "outside.zgpt@example.invalid",
            "guest_phone": "+33 6 12 34 56 78", "dinner_date": N1, "party_size": "7"})
    s.check("the same covers: no more than the night's six", not card_machine.sessions
            and "maximum of 6" in " ".join(flashes(r)), response=r)
    conn.execute("UPDATE stay_occasions SET outside_diners = 0 WHERE id = ?", (occ["id"],))
    conn.commit()
    panel = _panel(anon.get(occ_page).get_data(as_text=True))
    path, q, _f = _href(panel, "Book dinner")
    s.check("closed to them: the panel does not offer it", path == "/book"
            and "from outside the château" not in htmllib.unescape(panel), detail=f"{path} {q}")
    with _Card() as card_machine:
        r = _restaurant(anon, conn, {
            "guest_name": f"{TAG} Outside diner", "guest_email": "outside.zgpt@example.invalid",
            "guest_phone": "+33 6 12 34 56 78", "dinner_date": N1, "party_size": "2"})
    s.check("and the restaurant's page refuses them", not card_machine.sessions
            and "for guests staying at the château" in " ".join(flashes(r)), response=r)
    conn.execute("UPDATE restaurant_settings SET enabled = 1 WHERE id = 1")
    conn.commit()

    # ------------------------------------------------------------------
    s.section("9. A guest on a retreat is never offered a paid dinner on its nights")
    now = _harness.datetime_now()
    wid = conn.execute("INSERT INTO workshops (title, created_at) VALUES (?, ?)", (f"{TAG} Retreat", now)).lastrowid
    sess = conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, created_at) "
                        "VALUES (?, ?, ?, ?)", (wid, arrival, departure, now)).lastrowid
    retreat_stay = _stay(conn, stay_room, arrival, departure, f"{TAG} Retreat guest", "retreat.zgpt@example.invalid")
    conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name, guest_email,
                        party_size, status, booking_id, created_at)
                    VALUES (?, ?, ?, ?, 'retreat.zgpt@example.invalid', 2, 'confirmed', ?, ?)""",
                 (sess, f"ZGPT-{tok[:6]}", m.secrets.token_urlsafe(16), f"{TAG} Retreat guest",
                  retreat_stay["id"], now))
    conn.commit()
    s.check("the retreat's nights are known", m.retreat_nights_for(conn, retreat_stay["id"], "") == {N0, N1})
    with m.app.test_request_context("/"):
        letter = m.stay_dinner_letter(conn, retreat_stay)
    s.check("their stay's page offers no dinner on them", letter["more"] == [], detail=[n["date"] for n in letter["more"]])
    with _Letters():
        r = anon.post(f"/book/manage/{retreat_stay['manage_token']}",
                      data={"action": "add_dinner", "night": N0, "covers": "2"}, follow_redirects=True)
    s.check("and adding one is refused, saying why", "retreat already includes dinner" in " ".join(flashes(r))
            and not conn.execute("SELECT 1 FROM booking_extras WHERE booking_id = ? AND name LIKE 'Dinner%'",
                                 (retreat_stay["id"],)).fetchone(), response=r)
    with m.app.test_request_context("/"):
        cards = m.extras_offers_for_stay(conn, retreat_stay, today=m.house_today())
    s.check("the letter offers none", not any(c["kind"] == "dinner_at_gudanes" for c in cards))
    with _Card() as card_machine:
        r = _restaurant(anon, conn, {
            "guest_name": f"{TAG} Retreat guest", "guest_email": "retreat.zgpt@example.invalid",
            "guest_phone": "+33 6 12 34 56 78", "dinner_date": N0, "party_size": "2"})
    s.check("nor does the restaurant's page take their money", not card_machine.sessions
            and "retreat already includes dinner" in " ".join(flashes(r)), response=r)
    # The retreat taken away again: the rest walks the same dates as an ordinary stay.
    conn.execute(f"DELETE FROM workshop_bookings WHERE guest_name LIKE '{TAG}%'")
    conn.execute("DELETE FROM workshop_sessions WHERE id = ?", (sess,))
    conn.execute("DELETE FROM workshops WHERE id = ?", (wid,))
    conn.commit()

    # ------------------------------------------------------------------
    s.section("10. Full: said on the panel, the other night offered, and the waitlist when none is left")
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email, party_size,
                        dinner_date, status, created_at) VALUES (?, ?, ?, 'full.zgpt@example.invalid', 6, ?, 'confirmed', ?)""",
                 (m.make_restaurant_reference_code(), m.secrets.token_urlsafe(12), f"{TAG} Full table", N0, now))
    conn.commit()
    panel = htmllib.unescape(_panel(anon.get(occ_page).get_data(as_text=True)))
    s.check("the panel says Full on the night", f"Full on {w0}" in panel and f"Full on {w1}" not in panel)
    path, q, _f = _href(_panel(anon.get(occ_page).get_data(as_text=True)), "Book dinner")
    s.check("Book dinner is still offered, for the other night", path == "/book" and q.get("dinner") == "1")
    page = anon.get(form_url + "&party_size=2&dinner=1").get_data(as_text=True)
    s.check("and the room form adds dinner only where there are places", _box(page, N1) == 2
            and (_box(page, N0) in (None, 0)), detail=(_box(page, N0), _box(page, N1)))
    left1 = m.restaurant_remaining_capacity(conn, N1)
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email, party_size,
                        dinner_date, status, created_at) VALUES (?, ?, ?, 'full.zgpt@example.invalid', ?, ?, 'confirmed', ?)""",
                 (m.make_restaurant_reference_code(), m.secrets.token_urlsafe(12), f"{TAG} Full table", left1, N1, now))
    conn.commit()
    raw = _panel(anon.get(occ_page).get_data(as_text=True))
    path, q, frag = _href(raw, "Join the waitlist")
    s.check("with both full, Book dinner gives way to Join the waitlist", "Book dinner" not in raw
            and path == "/restaurant/book" and q == {"date": N0, "full": "1"} and frag == "waitlist", detail=f"{path} {q} {frag}")
    page = anon.get(f"/restaurant/book?date={N0}&full=1").get_data(as_text=True)
    s.check("which opens the waitlist for that night", 'id="waitlist"' in page
            and f'name="desired_date" value="{N0}"' in page)
    conn.execute(f"DELETE FROM restaurant_bookings WHERE guest_name = '{TAG} Full table'")
    conn.commit()

    # ------------------------------------------------------------------
    s.section("11. The cut-off: the evening before")
    with m.app.test_request_context("/"):
        on_n0 = m.occasion_dinner_panel(occ, [N0, N1], today=n0, conn=conn)
        on_n1 = m.occasion_dinner_panel(occ, [N0, N1], today=n1, conn=conn)
    s.check("on the first night, it is closed and the second still open",
            [n["state"] for n in on_n0["nights"]] == ["closed", "open"] and on_n0["book_dinner"])
    s.check("on the last, booking has closed and nothing is offered",
            on_n1["closed_all"] and not on_n1["book_dinner"] and not on_n1["waitlist"])
    with m.app.test_request_context("/"):
        html = m.app.jinja_env.from_string(
            "{% from '_dinner_panel.html' import dinner_panel %}{{ dinner_panel(occ, panel, dinners, '7.30pm', {}) }}"
        ).render(occ=m.stay_occasion_card(conn, occ), panel=on_n1, dinners=[N0, N1])
    said = htmllib.unescape(html)
    s.check("and the panel says so", "booking has closed" in said and f"Booking for {w1} closed the evening before" in said
            and "Book dinner" not in said)
    with m.app.test_request_context("/"):
        past_cut = [n["date"] for n in m.dinner_offer(conn, n0, n0 + timedelta(days=2), today=n0)]
    s.check("the room form offers no dinner on a night past its cut-off", past_cut == [N1], detail=past_cut)

    # ------------------------------------------------------------------
    _phone(s, anon, conn, occ_page, form_url, mpage, tok, N0, N1)


# --- 12. On a phone -----------------------------------------------------------------------------------------------

PROBE = r"""<script>
addEventListener('load', function(){ setTimeout(function(){
  var de = document.documentElement, out = {label: (location.hash.match(/label=([^&]*)/) || [])[1], w: innerWidth,
      sw: de.scrollWidth, cw: de.clientWidth};
  out.btns = [].map.call(document.querySelectorAll('.g-dinpanel__btns .g-btn'), function(b){
    var r = b.getBoundingClientRect(); return {h: Math.round(r.height), right: Math.round(r.right)}; });
  parent.postMessage(JSON.stringify(out), '*');
}, 400); });
</script>"""

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">%s
<div id="out"></div><script>
addEventListener('message', function(e){ var p = document.createElement('pre'); p.className = 'r';
  p.textContent = e.data; document.getElementById('out').appendChild(p); });
</script></body></html>"""


def _inline(page):
    def inline(match):
        tag = match.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        if not href or "stylesheet" not in tag:
            return "" if "preconnect" in tag else tag
        url = href.group(1)
        if url.startswith("/static/"):
            with open(os.path.join(ROOT, "static", url[len("/static/"):].split("?")[0]), encoding="utf-8") as fh:
                return "<style>\n" + fh.read() + "\n</style>"
        return ""
    return re.sub(r"<link\b[^>]*>", inline, page)


class _Probed:
    """The app, served for real, with a script put into one page: it lowers the second night's dinner and reads
    the total the page shows before and after."""

    def __init__(self, app, path, script):
        self.app, self.path, self.script = app, path, script

    def __call__(self, environ, start_response):
        from werkzeug.wrappers import Response
        resp = Response.from_app(self.app, environ)
        if environ.get("PATH_INFO") == self.path and resp.mimetype == "text/html":
            resp.set_data(resp.get_data(as_text=True).replace("</body>", self.script + "</body>", 1))
        return resp(environ, start_response)


LOWER = r"""<script>
addEventListener('load', function(){ setTimeout(function(){
  var bar = document.querySelector('[data-bar-total]'), out = {before: bar ? bar.textContent.trim() : null};
  var box = document.querySelector('input[name="dinner_%s"]');
  out.had = box ? box.value : null;
  if (box) { box.value = '0'; box.dispatchEvent(new Event('input', {bubbles: true}));
             box.dispatchEvent(new Event('change', {bubbles: true})); }
  setTimeout(function(){
    out.after = bar ? bar.textContent.trim() : null;
    var de = document.documentElement; out.sw = de.scrollWidth; out.cw = de.clientWidth;
    document.title = 'R:' + JSON.stringify(out);
  }, 2500);
}, 500); });
</script>"""


def _phone(s, anon, conn, occ_page, form_url, mpage, tok, N0, N1):
    s.section("12. On a 375px phone: nothing wider than the screen, and the total follows a dinner lowered")
    browser = find_browser()
    s.check("headless Chrome, Chromium or Edge is on this machine", bool(browser),
            detail="set GUDANES_CHROME to its path; this check fails rather than skips without one")
    if not browser:
        return
    pages = [("occasion", occ_page), ("book", f"/book?arrival={N0}&departure={(m.parse_date(N1) + timedelta(days=1)).isoformat()}"),
             ("room-form", form_url + "&party_size=2&dinner=1"), ("stay-page", mpage),
             ("restaurant", f"/restaurant/book?date={N1}"), ("checkin", f"/checkin/stay/{tok}"),
             ("waitlist", f"/restaurant/book?date={N0}&full=1")]
    with tempfile.TemporaryDirectory() as workdir:
        cells = []
        for i, (label, url) in enumerate(pages):
            fname = f"p{i}.html"
            with open(os.path.join(workdir, fname), "w", encoding="utf-8") as fh:
                fh.write(_inline(anon.get(url).get_data(as_text=True)).replace("</body>", PROBE + "</body>", 1))
            cells.append(f'<iframe src="{fname}#label={label}" style="display:block;border:0;width:375px;height:900px"></iframe>')
        wrapper = os.path.join(workdir, "wrapper.html")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(WRAPPER % "\n".join(cells))
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--user-data-dir=" + os.path.join(workdir, "profile"), "--hide-scrollbars",
               "--allow-file-access-from-files", "--host-resolver-rules=MAP * ~NOTFOUND",
               "--virtual-time-budget=20000", "--dump-dom", "file:///" + wrapper.replace("\\", "/").lstrip("/")]
        if sys.platform.startswith("linux"):
            cmd.insert(1, "--no-sandbox")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    got = {}
    for b in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        d = json.loads(htmllib.unescape(b))
        got[d.get("label")] = d
    s.check("every page came back from the browser", set(got) == {p[0] for p in pages}, detail=sorted(got))
    for label, _url in pages:
        d = got.get(label) or {}
        s.check(f"{label}: no wider than the 375px screen", d and d.get("sw", 999) <= d.get("cw", 0),
                detail=f"{d.get('sw')} wide in {d.get('cw')}")
    btns = (got.get("occasion") or {}).get("btns") or []
    s.check("the panel's buttons stack full width, each 44px or more",
            len(btns) == 2 and all(b["h"] >= 44 and b["right"] <= 375 for b in btns), detail=btns)

    # The room form, served for real: the total follows a dinner lowered.
    from werkzeug.serving import make_server
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    path = form_url.split("?")[0]
    server = make_server("127.0.0.1", 0, _Probed(m.app, path, LOWER % N1), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as workdir:
            url = f"http://127.0.0.1:{server.server_port}{form_url}&party_size=2&dinner=1"
            cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                   "--user-data-dir=" + os.path.join(workdir, "profile"), "--window-size=375,900",
                   "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                   "--virtual-time-budget=15000", "--dump-dom", url]
            if sys.platform.startswith("linux"):
                cmd.insert(1, "--no-sandbox")
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    finally:
        server.shutdown()
    t = re.search(r"<title>R:(.*?)</title>", proc.stdout, re.S)
    d = json.loads(htmllib.unescape(t.group(1))) if t else {}

    def euros(x):
        return float(re.sub(r"[^\d.]", "", (x or "").replace(",", ""))) if x and re.search(r"\d", x) else None
    before, after = euros(d.get("before")), euros(d.get("after"))
    s.check("in a real browser the form arrived with dinner for two on the second night", d.get("had") == "2", detail=d)
    s.check("and lowering it to none takes exactly two dinners off the total at once",
            before is not None and after is not None and round(before - after, 2) == 190.0, detail=d)
