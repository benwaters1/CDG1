"""Dinner at Gudanes: any occasion can serve dinner (owner's brief, 25 November 2026).

"Make it so I can do a 'Dinner at Gudanes', where I can put on an occasion that
dinner is possible. Then on Stay the Night it has it normal but it has a
notification ... On Stay the Night it displays the normal price, along with a
note that dinner will be hosted, and says the price, and they can add it as an
extra ... Also have a clickable pop-up where I can upload a menu and it pops up."
And: a banner under the video on the home page "like an advertisement"; the
newsletter drafting itself, for approval; "Wine included" on the price.

What this holds:
  - THE FORM: a plain stay occasion can serve dinner on the nights ticked, at a
    price a person; a price is required, a night must be the occasion's own,
    a night already booked at another price is refused until the owner says
    so, and unticking takes the nights away. The restaurant opens on them, at
    that price and those covers, exactly as a guest chef's do.
  - STAY THE NIGHT: the room's price is the normal one, with a note under it;
    the dinner is a line in the extras, first, priced by restaurant_night_rate;
    the quote, the card payment's line items and the page's own total and
    summary agree to the cent; paying writes the line on the bill AND the
    table in the restaurant's book, tied together, and cancelling either
    cancels both; covers past the night's limit are refused on the line.
  - THE MENU: a PDF or a picture, checked by what it is rather than its name,
    shown in a <dialog> wherever dinner is mentioned, and never mentioned when
    there is none; its address answers for a live occasion only.
  - THE LETTER and THE STAY'S PAGE: the dinners booked, and the dinner nights
    not added, with the way to add one.
  - THE CALENDAR: a plain occasion's dinner night, with its covers.
  - THE BANNER under the video: live and ticked only, until its last dinner
    night; the soonest of two; never a giant link.
  - THE NEWSLETTER: drafted once when it goes live, redrafted when the dates,
    price or menu change, never sent by itself, a task until it is.
  - IN A BROWSER: the pop-up fits a 375px phone and the banner a 320px one,
    with buttons a thumb can press.

Nothing here reaches Stripe, the mail or the network: Stripe is stood in,
send_email is recorded, and the browser is told no host resolves.
"""
import html as htmllib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, flashes, free_window, keep_rows
import _harness
from test_staff_header_on_a_phone import find_browser

m = _harness.m
ROOT = _harness.ROOT
TAG = "ZDIN"
WORDS = "A quiet Saturday in the valley, with the château lit for the evening."


def _occ(conn, name):
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _form(name, first, last, **over):
    """The occasion form as the page posts it, with its Dinner section drawn."""
    f = {"name": name, "first_night": first.isoformat(), "last_night": last.isoformat(),
         "kind": "stay", "chef_on_site": "0", "status": "draft", "description": WORDS,
         "photo": "", "dinner_section_shown": "1"}
    f.update(over)
    return f


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        f"SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%'")]
    marks = ",".join("?" * len(ids)) or "NULL"
    conn.execute(f"DELETE FROM campaign_sends WHERE template_id IN (SELECT id FROM campaign_templates "
                 f"WHERE occasion_id IN ({marks}) OR name LIKE 'Newsletter: {TAG}%')", ids)
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN ({marks}) "
                 f"OR name LIKE 'Newsletter: {TAG}%'", ids)
    conn.execute(f"DELETE FROM tasks WHERE origin = 'watch' AND title LIKE '%{TAG}%'")
    for occ_id in ids:
        for r in conn.execute("SELECT menu_file, banner_file FROM stay_occasions WHERE id = ?", (occ_id,)):
            m.remove_occasion_file(r["menu_file"])
            m.remove_occasion_file(r["banner_file"])
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasions WHERE id IN ({marks})", ids)
    conn.execute(f"DELETE FROM restaurant_bookings WHERE guest_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN "
                 f"(SELECT id FROM bookings WHERE guest_name LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN "
                 f"(SELECT id FROM bookings WHERE guest_name LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM bookings WHERE guest_name LIKE '{TAG}%'")
    conn.commit()


class _Letters:
    """send_email, recorded and never sent."""

    def __enter__(self):
        self.sent = []
        self._was = m.send_email

        def _record(to, subject, body, *a, **kw):
            self.sent.append({"to": to, "subject": subject, "body": body, "html": kw.get("html") or ""})
            return True
        m.send_email = _record
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
            url = "https://checkout.example.invalid/zdin"
            id = "cs_test_zdin"

        def _session(**kw):
            self.sessions.append(kw)
            return _Made(**kw)
        m.stripe.checkout.Session.create = _session
        return self

    def __exit__(self, *_exc):
        m.stripe_enabled, m.stripe.checkout.Session.create = self._enabled, self._create
        return False


def _pdf():
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 900), (240, 232, 214)).save(buf, "PNG")
    return buf.getvalue()


def _card_of(page, room_id):
    """One room's card on /book."""
    at = page.find(f'id="room-{room_id}"')
    if at < 0:
        return ""
    end = page.find("<article", at + 10)
    return page[at:end if end > 0 else len(page)]


def run():
    s = Suite("Dinner at Gudanes: dinner on any occasion, a note on Stay the Night, the menu in a pop-up")
    oc, _ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "restaurant_settings", "id = 1"),
                keep_rows(conn, "app_settings", "key IN ('dining_days', 'dining_mode')"),
                keep_rows(conn, "stay_occasions", "status = 'live'")]
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, owner, conn)
    finally:
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, owner, conn):
    anon = m.app.test_client()
    room = ensure_room(min_occupancy=2)
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    rid = room["id"]
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, price_per_person = 45, "
                 "dinner_time = '19:30' WHERE id = 1")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_mode', 'closed') "
                 "ON CONFLICT(key) DO UPDATE SET value = 'closed'")
    conn.commit()
    first = free_window(rid, 4, after_days=150, clear_of_ateliers=True, clear_of_rate_overrides=True)
    n1, n2 = first, first + timedelta(days=1)
    arrival, departure = n1.isoformat(), (n1 + timedelta(days=2)).isoformat()
    later, later_out = (n1 + timedelta(days=2)).isoformat(), (n1 + timedelta(days=4)).isoformat()
    name = f"{TAG} A Saturday dinner"
    words2 = m.dinner_nights_words([n2.isoformat()])
    short2 = m.dinner_night_short(n2.isoformat())

    # ------------------------------------------------------------------
    s.section("The occasion form: Dinner at Gudanes, on a plain stay occasion")
    r = oc.post("/admin/stay-occasions/new", data=_form(name, n1, n2, serve_dinner="1"),
                follow_redirects=True)
    s.check("ticked with no price, it is refused and nothing is made",
            any("needs a price a person" in f for f in flashes(r)) and _occ(conn, name) is None,
            response=r)
    r = oc.post("/admin/stay-occasions/new",
                data=_form(name, n1, n2, serve_dinner="1", dinner_price="65"), follow_redirects=True)
    occ = _occ(conn, name)
    s.check("with a price it is drafted", occ is not None and occ["status"] == "draft", response=r)
    if not occ:
        return
    oid = occ["id"]
    s.check("every night is a dinner night until one is unticked",
            m.stay_occasion_dinners(occ, conn) == [n1.isoformat(), n2.isoformat()],
            detail=str(m.stay_occasion_dinners(occ, conn)))
    s.check("serving dinner puts the chef on site, so the words and the tag follow", occ["chef_on_site"] == 1)
    s.check("the covers and the time default to the restaurant's own",
            occ["dinner_covers"] == 20 and occ["dinner_time"] == "19:30",
            detail=f"{occ['dinner_covers']} covers at {occ['dinner_time']}")

    live = dict(status="live", serve_dinner="1", dinner_price="65", dinner_covers="6",
                dinner_time="19:30", wine_included="1", dinner_nights_shown="1",
                **{f"dinner_{n2.isoformat()}": "1"})
    r = oc.post(f"/admin/stay-occasions/{oid}",
                data=_form(name, n1, n2, **dict(live, **{f"dinner_{(n1 - timedelta(days=3)).isoformat()}": "1"})),
                follow_redirects=True)
    s.check("a dinner night outside the occasion is refused",
            any("own nights" in f and "must be one of" in f for f in flashes(r)), response=r)
    r = oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **live), follow_redirects=True)
    occ = _occ(conn, oid and name)
    s.check("with no room prices typed, it still goes on sale: the rooms stay at their usual rate",
            occ["status"] == "live" and not conn.execute(
                "SELECT 1 FROM stay_occasion_prices WHERE occasion_id = ?", (oid,)).fetchone(),
            response=r)
    s.check("its dinner nights are the ones ticked, in stay_occasion_dinners",
            m.stay_occasion_dinners(occ, conn) == [n2.isoformat()],
            detail=str(m.stay_occasion_dinners(occ, conn)))
    with m.app.test_request_context("/"):
        rate2 = m.restaurant_night_rate(conn, n2.isoformat())
        rate1 = m.restaurant_night_rate(conn, n1.isoformat())
        seats = m.restaurant_remaining_capacity(conn, n2.isoformat())
    s.check("restaurant_night_rate prices the dinner night from the occasion", rate2 == 65, detail=str(rate2))
    s.check("and the other night keeps the restaurant's own price", rate1 == 45, detail=str(rate1))
    s.check("the night seats the occasion's covers", seats == 6, detail=str(seats))
    nights_ahead = [d["dinner_date"] for d in m.occasion_dinner_nights_ahead(conn)]
    s.check("occasion_dinner_nights_ahead reads a plain occasion, not only a guest chef",
            n2.isoformat() in nights_ahead, detail=str(nights_ahead))

    # ------------------------------------------------------------------
    s.section("Stay the Night: the normal price, and a note under it")
    search = f"/book?arrival={arrival}&departure={departure}&party_size=2"
    page = anon.get(search).get_data(as_text=True)
    card = _card_of(page, rid)
    note = (f"Dinner at Gudanes is hosted on {words2}, 7.30pm, €65 a person, wine included. "
            "Add it in the extras when you book.")
    s.check("the room's card carries the note: the night, the time and the price",
            note in htmllib.unescape(card), detail=htmllib.unescape(card)[-700:] if card else "no card")
    price_with = re.search(r'data-eur="([\d.]+)"', card)
    conn.execute("DELETE FROM stay_occasion_dinners WHERE occasion_id = ?", (oid,))
    conn.commit()
    card_without = _card_of(anon.get(search).get_data(as_text=True), rid)
    price_without = re.search(r'data-eur="([\d.]+)"', card_without)
    conn.execute("INSERT INTO stay_occasion_dinners (occasion_id, dinner_date) VALUES (?, ?)",
                 (oid, n2.isoformat()))
    conn.commit()
    s.check("the room's price is the same with the dinner and without it",
            price_with and price_without and price_with.group(1) == price_without.group(1),
            detail=f"{price_with and price_with.group(1)} / {price_without and price_without.group(1)}")
    with m.app.test_request_context("/"):
        usual = m.compute_room_total(conn, room, n1, n1 + timedelta(days=2))
    s.check("and it is the room's usual rate for those nights",
            price_with and abs(float(price_with.group(1)) - usual) < 0.005,
            detail=f"{price_with and price_with.group(1)} against {usual}")
    s.check("without dinner, no note", "is hosted on" not in card_without)
    s.check("and with no room prices of its own, nothing says the nights are priced for the occasion",
            "priced for the occasion" not in page and "includes 2 nights of" not in page)
    outside = anon.get(f"/book?arrival={later}&departure={later_out}&party_size=2").get_data(as_text=True)
    s.check("dates without a dinner night carry no note", "is hosted on" not in outside)

    # ------------------------------------------------------------------
    s.section("The extras: dinner first, a number box, only on a dinner night")
    room_url = f"/book/{rid}?arrival={arrival}&departure={departure}&party_size=2"
    page = anon.get(room_url).get_data(as_text=True)
    box = page.find(f'name="dinner_{n2.isoformat()}"')
    s.check("the booking form offers dinner on that night", box > 0
            and '<ul class="g-extras g-extras--dinner" id="dinner_extras">' in page)
    extra_at = page.find('name="extras"')
    s.check("first in the extras list", box > 0 and (extra_at < 0 or box < extra_at),
            detail=f"dinner at {box}, first catalogue extra at {extra_at}")
    s.check("worded with the night, the time and the price",
            f"Dinner at Gudanes, {words2}, 7.30pm" in htmllib.unescape(page)
            and "€65 a person, wine included" in htmllib.unescape(page))
    top = re.search(r'<div class="g-dinnernote" id="dinner_note"[^>]*>(.*?)</div>', page, re.S)
    s.check("and the note is at the top of the booking form, under the room's price",
            top and "hidden" not in top.group(0)[:80] and "is hosted on" in top.group(1),
            detail=top.group(0)[:200] if top else "no note")
    away = anon.get(f"/book/{rid}?arrival={later}&departure={later_out}&party_size=2").get_data(as_text=True)
    s.check("outside a dinner night the form offers no dinner", 'name="dinner_2' not in away)
    s.check("and 'what a night includes' says the château hosts dinner on these dates, not the restaurants nearby",
            "On your dates the château hosts Dinner at Gudanes, added in the extras below" in htmllib.unescape(page)
            and "Lunch and dinner at the" in away and "hosts Dinner at Gudanes" not in away)

    # ------------------------------------------------------------------
    s.section("The quote: a line of its own, and the total to the cent")
    base = f"/api/quote?room_id={rid}&arrival={arrival}&departure={departure}&party_size=2"
    q0 = anon.get(base).get_json()
    q2 = anon.get(base + f"&dinner_{n2.isoformat()}=2").get_json()
    lines = [l for l in (q2 or {}).get("lines", []) if l.get("kind") == "dinner"]
    s.check("two for dinner is one line, as the summary says it",
            len(lines) == 1 and lines[0]["label"] == f"Dinner at Gudanes, {short2}, 2 × €65"
            and lines[0]["amount"] == 130.0, detail=str(lines))
    s.check("the total rises by exactly two dinners",
            q0 and q2 and round(q2["total"] - q0["total"], 2) == 130.0,
            detail=f"{q0 and q0['total']} -> {q2 and q2['total']}")
    s.check("and the room's figures are untouched",
            q0 and q2 and q2["room_total"] == q0["room_total"] and q2["per_night"] == q0["per_night"])
    q3 = anon.get(base + f"&dinner_{n2.isoformat()}=3").get_json()
    s.check("more than the party is refused on the line", "At most 2" in
            (q3 or {}).get("dinner_problems", {}).get(n2.isoformat(), ""), detail=str(q3 and q3.get("dinner_problems")))

    # ------------------------------------------------------------------
    booking_form = {
        "arrival_date": arrival, "departure_date": departure, "guest_name": f"{TAG} Diner",
        "guest_email": "diner.zdin@example.invalid", "guest_phone": "+33 6 12 34 56 78",
        "party_size": "2", "adults": "2", "guests_under_18": "0", "agree_terms": "on",
        "guest_address": "1 Rue du Château", "guest_city": "Foix", "guest_postcode": "09000",
        "guest_country": "France", "estimated_arrival_time": "After 10pm",
        f"dinner_{n2.isoformat()}": "2"}
    s.section("Covers past the night's limit are refused, with the number left")
    conn.execute("UPDATE stay_occasions SET dinner_covers = 1 WHERE id = ?", (oid,))
    conn.commit()
    q = anon.get(base + f"&dinner_{n2.isoformat()}=2").get_json()
    s.check("the quote says how many places are left",
            (q or {}).get("dinner_problems", {}).get(n2.isoformat()) == "1 place left that night.",
            detail=str(q and q.get("dinner_problems")))
    with _Card() as card_machine:
        r = m.app.test_client().post(f"/book/{rid}", data=dict(booking_form, guest_name=f"{TAG} Late"))
    s.check("and Pay & book refuses it rather than taking the money",
            "1 place left that night." in " ".join(flashes(r)) and not card_machine.sessions, response=r)
    conn.execute("UPDATE stay_occasions SET dinner_covers = 6 WHERE id = ?", (oid,))
    conn.commit()

    # ------------------------------------------------------------------
    s.section("Pay & book: the card is charged the dinner as its own line")
    guest = m.app.test_client()
    with _Card() as card_machine:
        r = guest.post(f"/book/{rid}", data=booking_form)
    made = card_machine.sessions[-1] if card_machine.sessions else {}
    items = made.get("line_items") or []
    dinner_items = [i for i in items if i["price_data"]["product_data"]["name"].startswith("Dinner at Gudanes")]
    s.check("the guest is sent to the card page", r.status_code == 303, detail=str(r.status_code))
    s.check("the dinner is a line: two covers at 65.00",
            len(dinner_items) == 1 and dinner_items[0]["quantity"] == 2
            and dinner_items[0]["price_data"]["unit_amount"] == 6500, detail=str(dinner_items))
    charged = sum(i["price_data"]["unit_amount"] * i["quantity"] for i in items)
    s.check("the card is charged what the quote says, to the cent",
            q2 and charged == round(q2["total"] * 100), detail=f"{charged} against {q2 and q2['total']}")
    meta = made.get("metadata") or {}
    s.check("the dinners ride through the card page as charged",
            meta.get("dinners") == f"{n2.isoformat()}:2:65.00", detail=str(meta.get("dinners")))
    back = guest.get(f"/book/{rid}").get_data(as_text=True)
    bar = re.search(r"data-bar-total>([^<]+)<", back)
    s.check("coming back from the card page, the phone bar says the same total",
            bar and q2 and bar.group(1).strip() == f"€{q2['total']:.2f}", detail=bar.group(1) if bar else "no bar")
    s.check("the summary has the dinner line, and the box still says two",
            f"Dinner at Gudanes, {short2}, 2 × €65" in htmllib.unescape(back)
            and f'name="dinner_{n2.isoformat()}" min="0"' in back
            and re.search(rf'name="dinner_{n2.isoformat()}"[^>]*value="2"', back) is not None)

    # Paid: the webhook's own function, with what the card page was given. A
    # second dinner night is put on the stay first, not added, for the letter.
    conn.execute("INSERT INTO stay_occasion_dinners (occasion_id, dinner_date) VALUES (?, ?)",
                 (oid, n1.isoformat()))
    conn.commit()
    paid = {"id": "cs_test_zdin_paid", "metadata": meta, "amount_total": charged,
            "payment_intent": "pi_test_zdin", "payment_status": "paid"}
    with _Letters() as letters, m.app.test_request_context("/"):
        token = m.create_booking_from_stripe_session(conn, paid)
    stay = conn.execute("SELECT * FROM bookings WHERE stripe_session_id = ?", ("cs_test_zdin_paid",)).fetchone()
    s.check("paid, the stay is written and confirmed", stay is not None and stay["status"] == "confirmed",
            detail=str(stay and stay["status"]))
    if not stay:
        return
    line = conn.execute("SELECT * FROM booking_extras WHERE booking_id = ? AND category = 'room' "
                        "AND name LIKE 'Dinner at Gudanes%'", (stay["id"],)).fetchone()
    s.check("the dinner is a line on its bill: name, price and covers copied, the night in its notes",
            line is not None and line["quantity"] == 2 and line["unit_price"] == 65
            and n2.isoformat() in (line["notes"] or "") and line["name"] == f"Dinner at Gudanes, {short2}",
            detail=str(line and dict(line)))
    table = conn.execute("SELECT * FROM restaurant_bookings WHERE booking_extra_id = ?",
                         (line["id"] if line else -1,)).fetchone()
    s.check("and a table in the restaurant's book, confirmed, tied to the stay and the line",
            table is not None and table["status"] == "confirmed" and table["party_size"] == 2
            and table["booking_id"] == stay["id"] and table["dinner_date"] == n2.isoformat(),
            detail=str(table and dict(table)))
    bill = m.booking_bill(conn, stay["id"])
    s.check("the bill counts it once, inside what was paid",
            bill and abs(bill["owed"]) < 0.005
            and any(l["kind"] == "extra" and l["amount"] == 130.0 for l in bill["lines"]),
            detail=str(bill and [(l["label"], l["amount"]) for l in bill["lines"]]))
    letter = next((x for x in letters.sent if x["to"] == "diner.zdin@example.invalid"), None)
    s.check("the confirmation lists the dinner booked",
            letter and "Dinner at Gudanes is booked" in letter["html"] and words2 in letter["html"],
            detail=str(letter and letter["subject"]))
    words1 = m.dinner_nights_words([n1.isoformat()])
    s.check("and the dinner night not added, with the way to add it",
            letter and "Dinner is also served on" in letter["html"] and words1 in letter["html"]
            and "#dinner-at-gudanes" in letter["html"])

    # ------------------------------------------------------------------
    s.section("The house calendar: a plain occasion's dinner night")
    with m.app.test_request_context("/"):
        user = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
        month = m.build_calendar(conn, "month", n2, viewer=user)
    cell = next((c for c in month["cells"] if c["date"] == n2), {"events": []})
    titles = [e.get("title") or "" for e in cell["events"] if e.get("kind") == "occasion"]
    s.check("on its dinner night, with the covers booked of the covers it seats",
            any(t.startswith("Dinner at Gudanes — 2 covers booked of 6") for t in titles), detail=str(titles))

    # ------------------------------------------------------------------
    s.section("The stay's own page: the dinner booked, and Add dinner for the other night")
    mb = anon.get(f"/book/manage/{token}").get_data(as_text=True)
    s.check("the dinner booked is listed", 'id="dinner-at-gudanes"' in mb
            and f"{words2} — 2 × €65.00" in htmllib.unescape(mb))
    s.check("and the night not added offers Add dinner", 'value="add_dinner"' in mb
            and f'name="night" value="{n1.isoformat()}"' in mb)
    with _Letters():
        r = anon.post(f"/book/manage/{token}", data={"action": "add_dinner", "night": n1.isoformat(),
                                                    "covers": "2"}, follow_redirects=True)
    added = conn.execute("""SELECT booking_extras.*, restaurant_bookings.status AS t FROM booking_extras
                              JOIN restaurant_bookings ON restaurant_bookings.booking_extra_id = booking_extras.id
                             WHERE booking_extras.booking_id = ? AND booking_extras.scheduled_for = ?""",
                         (stay["id"], n1.isoformat())).fetchone()
    s.check("added, it is a line and a table together",
            added is not None and added["quantity"] == 2 and added["t"] == "confirmed", response=r)
    with _Letters():
        r = anon.post(f"/book/manage/{token}", data={"action": "add_dinner", "night": n1.isoformat(),
                                                    "covers": "2"}, follow_redirects=True)
    s.check("a second time, it says it is already there", any("already on your stay" in f for f in flashes(r)))

    s.section("Cancelling the line cancels the table, and the other way round")
    m.cancel_booking_extra(conn, line["id"])
    conn.commit()
    t_after = conn.execute("SELECT status FROM restaurant_bookings WHERE booking_extra_id = ?", (line["id"],)).fetchone()
    s.check("the line cancelled, its table is cancelled", t_after and t_after["status"] == "cancelled")
    table1 = conn.execute("SELECT * FROM restaurant_bookings WHERE booking_extra_id = ?", (added["id"],)).fetchone()
    r = oc.post(f"/admin/restaurant/{table1['id']}/cancel", follow_redirects=True)
    l_after = conn.execute("SELECT status FROM booking_extras WHERE id = ?", (added["id"],)).fetchone()
    s.check("the table cancelled from the restaurant's side, its line goes too",
            l_after and l_after["status"] == "cancelled", response=r)
    third = m.add_dinner_to_stay(conn, stay, n2.isoformat(), 1, 65)
    conn.commit()
    with _Letters():
        r = oc.post(f"/admin/bookings/{stay['id']}/cancel", follow_redirects=True)
    t3 = conn.execute("SELECT status FROM restaurant_bookings WHERE booking_extra_id = ?", (third,)).fetchone()
    l3 = conn.execute("SELECT status FROM booking_extras WHERE id = ?", (third,)).fetchone()
    s.check("the stay cancelled, the kitchen is not left cooking for it",
            t3 and t3["status"] == "cancelled", response=r)
    s.check("and the line stays on the bill: what is refunded is the owner's call",
            l3 and l3["status"] != "cancelled")

    # ------------------------------------------------------------------
    s.section("The menu: uploaded, checked by what it is, and shown in a pop-up")
    occ_page = f"/whats-on/at-gudanes/{occ['slug']}"
    p0 = anon.get(occ_page).get_data(as_text=True)
    s.check("with no menu, no page mentions one", "See the menu" not in p0 and "menu to come" not in p0.lower()
            and "See the menu" not in anon.get(search).get_data(as_text=True))
    s.check("the occasion's page says Dinner at Gudanes, the nights, the time and the price",
            "Dinner is hosted at the château on" in htmllib.unescape(p0)
            and "€65 a person, wine included" in htmllib.unescape(p0)
            and "Add it in the extras when you book your room" in htmllib.unescape(p0))
    save = dict(live, **{f"dinner_{n1.isoformat()}": "1"})
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **save),
                menu=(io.BytesIO(b"<html>not a menu</html>"), "menu.pdf")),
                content_type="multipart/form-data", follow_redirects=True)
    s.check("a file that is not a PDF or a picture is refused, whatever it is called",
            any("is not a PDF, JPG or PNG" in f for f in flashes(r)) and not _occ(conn, name)["menu_file"],
            response=r)
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **save),
                menu=(io.BytesIO(_pdf()), "Menu de Noël.pdf")),
                content_type="multipart/form-data", follow_redirects=True)
    occ = _occ(conn, name)
    pdf_name = occ["menu_file"]
    s.check("a PDF is kept, under a name the app makes up", bool(pdf_name) and pdf_name.endswith(".pdf")
            and "Noël" not in pdf_name and os.path.exists(os.path.join(m.OCCASION_FILES_DIR, pdf_name)),
            response=r)
    p1 = anon.get(occ_page).get_data(as_text=True)
    s.check("the occasion's page offers See the menu, opening a <dialog>",
            "See the menu" in p1 and "<dialog" in p1 and 'data-menu-pdf="1"' in p1)
    got = anon.get(f"{occ_page}/menu")
    s.check("the menu's address answers with the PDF", got.status_code == 200
            and got.mimetype == "application/pdf" and got.data.startswith(b"%PDF"), detail=str(got.status_code))
    got.close()
    dl = anon.get(f"{occ_page}/menu?download=1")
    s.check("and Download downloads it", "attachment" in (dl.headers.get("Content-Disposition") or ""))
    dl.close()
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **save),
                menu=(io.BytesIO(_png()), "menu.png")),
                content_type="multipart/form-data", follow_redirects=True)
    occ = _occ(conn, name)
    s.check("a picture replaces it, and the old file goes once the new one is saved",
            occ["menu_file"].endswith(".png") and not os.path.exists(os.path.join(m.OCCASION_FILES_DIR, pdf_name)),
            response=r)
    p2 = anon.get(occ_page).get_data(as_text=True)
    s.check("a picture menu opens as a picture, with its alt text",
            'data-menu-pdf="0"' in p2 and f'data-menu-alt="The menu for Dinner at Gudanes, ' in p2)
    srch = anon.get(search).get_data(as_text=True)
    s.check("the note on Stay the Night has See the menu, and the pop-up is on the page",
            "See the menu" in _card_of(srch, rid) and srch.count('id="g-menudlg"') == 1)
    home = anon.get("/").get_data(as_text=True)
    s.check("the occasion's card names the night and offers the menu",
            "Dinner at Gudanes</span> each night" in home and "g-occasion__menu" in home,
            detail=re.search(r'g-sit__meta">[^<]*<span class="g-occasion__tag">[^<]*</span>[^<]*', home).group(0)
            if re.search(r'g-sit__meta">[^<]*<span class="g-occasion__tag">[^<]*</span>[^<]*', home) else "")
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **save), **{
                f"menu_{n2.isoformat()}": (io.BytesIO(_pdf()), "samedi.pdf")}),
                content_type="multipart/form-data", follow_redirects=True)
    own = conn.execute("SELECT menu_file FROM stay_occasion_dinners WHERE occasion_id = ? AND dinner_date = ?",
                       (oid, n2.isoformat())).fetchone()
    s.check("a night can have a menu of its own", own and (own["menu_file"] or "").endswith(".pdf"), response=r)
    night_menu = anon.get(f"{occ_page}/menu/{n2.isoformat()}")
    s.check("served at its own address", night_menu.status_code == 200 and night_menu.data.startswith(b"%PDF"),
            detail=str(night_menu.status_code))
    night_menu.close()
    p3 = htmllib.unescape(anon.get(occ_page).get_data(as_text=True))
    s.check("and named on the occasion's page beside its night",
            f"{words2}: See the menu" in re.sub(r"<[^>]+>", "", p3))
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **save),
                **{f"menu_remove_{n2.isoformat()}": "1"}), follow_redirects=True)
    gone = conn.execute("SELECT menu_file FROM stay_occasion_dinners WHERE occasion_id = ? AND dinner_date = ?",
                        (oid, n2.isoformat())).fetchone()
    s.check("and removed again, the night shares the occasion's menu",
            gone and not gone["menu_file"] and anon.get(f"{occ_page}/menu/{n2.isoformat()}").status_code == 404,
            response=r)
    for state in ("draft", "dismissed"):
        conn.execute("UPDATE stay_occasions SET status = ? WHERE id = ?", (state, oid))
        conn.commit()
        s.check(f"the menu's address is a 404 for a {state} occasion",
                anon.get(f"{occ_page}/menu").status_code == 404)
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE id = ?", (oid,))
    conn.commit()

    # ------------------------------------------------------------------
    s.section("Wine included, on by default, and off when unticked")
    r = oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **{
        k: v for k, v in save.items() if k != "wine_included"}), follow_redirects=True)
    plain = htmllib.unescape(_card_of(anon.get(search).get_data(as_text=True), rid))
    s.check("unticked, the price says only the price", "€65 a person." in plain and "wine included" not in plain,
            response=r)
    oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **save))

    # ------------------------------------------------------------------
    s.section("Unticked, the nights go")
    r = oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **{
        k: v for k, v in save.items() if k != "serve_dinner"}), follow_redirects=True)
    occ = _occ(conn, name)
    s.check("unticking Serve dinner takes every night away", m.stay_occasion_dinners(occ, conn) == [],
            response=r)
    with m.app.test_request_context("/"):
        back_rate = m.restaurant_night_rate(conn, n2.isoformat())
    s.check("and the restaurant is back at its own price", back_rate == 45, detail=str(back_rate))
    oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **save))

    s.section("A night already booked at another price is said before it is changed")
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
                        party_size, dinner_date, status, total_price, created_at)
                    VALUES ('ZDIN-T1', 'zdin-t1', ?, 'table.zdin@example.invalid', 2, ?, 'confirmed', 90, ?)""",
                 (f"{TAG} Table", n2.isoformat(), _harness.datetime_now()))
    conn.commit()
    r = oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **dict(save, dinner_price="70")),
                follow_redirects=True)
    s.check("ticking it at a new price is refused, naming the price booked",
            any("€45.00 a person" in f and "already has 1 table" in f for f in flashes(r))
            and _occ(conn, name)["dinner_price"] == 65, response=r)
    r = oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **dict(
        save, dinner_price="70", confirm_dinner_price="1")), follow_redirects=True)
    s.check("and saved once the owner says so", _occ(conn, name)["dinner_price"] == 70, response=r)
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code = 'ZDIN-T1'")
    conn.commit()
    oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **dict(save, confirm_dinner_price="1")))

    # ------------------------------------------------------------------
    s.section("The banner under the video")
    def band(page):
        return re.search(r'<section class="g-hband".*?</section>', page, re.S)
    s.check("unticked, the home page has no banner", band(anon.get("/").get_data(as_text=True)) is None)
    banner = dict(save, home_banner="1", home_banner_text="Dinner at Gudanes")
    oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **banner))
    home = anon.get("/").get_data(as_text=True)
    b = band(home)
    s.check("ticked and live, it is drawn", b is not None)
    if b:
        text = htmllib.unescape(b.group(0))
        hero_end, where = home.find('id="g-hero-pause"'), home.find('class="g-where"')
        s.check("directly after the video, before the address line", hero_end < b.start() < where,
                detail=f"hero {hero_end}, banner {b.start()}, address {where}")
        s.check("with its line, its name and its dates",
                "Dinner at Gudanes" in text and name in text
                and m.format_date_range(n1.isoformat(), departure) in text)
        s.check("and the price", "Dinner from €65 a person, wine included" in re.sub(r"<[^>]+>", "", text))
        s.check("Book your stay opens /book with its dates",
                f'href="/book?arrival={arrival}&amp;departure={departure}"' in b.group(0)
                and "Book your stay" in text)
        s.check("See the menu, with a menu uploaded", "See the menu" in text and "data-menu-open" in b.group(0))
        alt = re.search(r'<img[^>]*alt="([^"]*)"', b.group(0))
        s.check("the photograph has real alt text", alt and name in htmllib.unescape(alt.group(1)),
                detail=alt.group(1) if alt else "no img")
        s.check("not one giant link: two buttons inside it",
                not re.search(r"<a[^>]*>\s*<section", home) and b.group(0).count("<a ") >= 4)
    oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **banner), menu_remove="1"))
    b = band(anon.get("/").get_data(as_text=True))
    s.check("with the menu removed, no See the menu", b and "See the menu" not in b.group(0))
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **banner),
                banner=(io.BytesIO(_pdf()), "banner.jpg")), content_type="multipart/form-data",
                follow_redirects=True)
    s.check("the banner's own photograph must be a picture, whatever it is called",
            any("not a JPG or PNG" in f for f in flashes(r)) and not _occ(conn, name)["banner_file"], response=r)
    r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **banner),
                banner=(io.BytesIO(_png()), "banner.png")), content_type="multipart/form-data",
                follow_redirects=True)
    b = band(anon.get("/").get_data(as_text=True))
    photo = anon.get(f"{occ_page}/banner")
    s.check("a photograph of its own takes the banner's left half",
            b and f'src="{occ_page}/banner"' in b.group(0) and photo.status_code == 200
            and photo.mimetype == "image/png", detail=b.group(0)[:300] if b else "no banner")
    photo.close()
    other = f"{TAG} The later one"
    oc.post("/admin/stay-occasions/new", data=_form(other, n1 + timedelta(days=2), n1 + timedelta(days=2),
                                                    serve_dinner="1", dinner_price="55"))
    o2 = _occ(conn, other)
    oc.post(f"/admin/stay-occasions/{o2['id']}", data=_form(other, n1 + timedelta(days=2), n1 + timedelta(days=2),
            **dict(status="live", serve_dinner="1", dinner_price="55", home_banner="1",
                   home_banner_text="Chef in residence", dinner_nights_shown="1",
                   **{f"dinner_{(n1 + timedelta(days=2)).isoformat()}": "1"})))
    home = anon.get("/").get_data(as_text=True)
    s.check("two ticked, one banner: the soonest",
            home.count('class="g-hband"') == 1 and name in htmllib.unescape(band(home).group(0))
            and other not in htmllib.unescape(band(home).group(0)))
    s.check("a one-night occasion is a Dinner at Gudanes evening of its own",
            _occ(conn, other)["status"] == "live"
            and m.stay_occasion_dinners(_occ(conn, other), conn) == [(n1 + timedelta(days=2)).isoformat()])
    for state in ("draft", "dismissed"):
        conn.execute("UPDATE stay_occasions SET status = ? WHERE name LIKE ?", (state, f"{TAG}%"))
        conn.commit()
        s.check(f"a {state} occasion has no banner", band(anon.get("/").get_data(as_text=True)) is None)
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE id = ?", (oid,))
    past = m.house_today() - timedelta(days=10)
    conn.execute("UPDATE stay_occasions SET first_night = ?, last_night = ? WHERE id = ?",
                 (past.isoformat(), (past + timedelta(days=1)).isoformat(), oid))
    conn.execute("UPDATE stay_occasion_dinners SET dinner_date = ? WHERE occasion_id = ? AND dinner_date = ?",
                 ((past + timedelta(days=1)).isoformat(), oid, n2.isoformat()))
    conn.execute("DELETE FROM stay_occasion_dinners WHERE occasion_id = ? AND dinner_date = ?", (oid, n1.isoformat()))
    conn.commit()
    s.check("once its last dinner night has passed, it takes itself down",
            band(anon.get("/").get_data(as_text=True)) is None)
    conn.execute("UPDATE stay_occasions SET first_night = ?, last_night = ? WHERE id = ?",
                 (n1.isoformat(), n2.isoformat(), oid))
    conn.execute("DELETE FROM stay_occasion_dinners WHERE occasion_id = ?", (oid,))
    conn.execute("INSERT INTO stay_occasion_dinners (occasion_id, dinner_date) VALUES (?, ?)", (oid, n2.isoformat()))
    conn.commit()

    # ------------------------------------------------------------------
    s.section("The newsletter drafts itself, and waits")
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN "
                 f"(SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%')")
    conn.commit()
    news = dict(banner, **{f"dinner_{n1.isoformat()}": None})
    news = {k: v for k, v in news.items() if v is not None}
    with _Letters() as letters:
        r = oc.post(f"/admin/stay-occasions/{oid}", data=dict(_form(name, n1, n2, **news),
                    menu=(io.BytesIO(_png()), "menu.png")), content_type="multipart/form-data",
                    follow_redirects=True)
        drafts = conn.execute("SELECT * FROM campaign_templates WHERE occasion_id = ?", (oid,)).fetchall()
        s.check("saved live with dinner, exactly one draft campaign", len(drafts) == 1, response=r)
        d0 = drafts[0] if drafts else None
        s.check("with the dates, Book your stay and the menu in it",
                d0 and m.format_date_range(n1.isoformat(), departure) in d0["body"]
                and "Book your stay:\n" in d0["body"] and "{{menu_pictures}}" in d0["body"]
                and "See the menu:\n" in d0["body"], detail=d0["body"] if d0 else "")
        s.check("its subject says the line and the dates",
                d0 and d0["subject"].startswith("Dinner at Gudanes, "), detail=d0["subject"] if d0 else "")
        s.check("its audience is the newsletter and past guests, ticked for the owner",
                d0 and set((d0["audience"] or "").split(",")) == {"newsletter", "room", "restaurant", "workshop"})
        oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **news))
        again = conn.execute("SELECT * FROM campaign_templates WHERE occasion_id = ?", (oid,)).fetchall()
        s.check("saved again unchanged, still one draft, untouched",
                len(again) == 1 and not again[0]["redrafted_at"])
        oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **dict(
            news, dinner_price="72", confirm_dinner_price="1")))
        again = conn.execute("SELECT * FROM campaign_templates WHERE occasion_id = ?", (oid,)).fetchall()
        s.check("a new price redrafts the same one, marked updated",
                len(again) == 1 and again[0]["redrafted_at"] and "€72 a person" in again[0]["body"],
                detail=again[0]["body"] if again else "")
        s.check("and nothing was sent while saving", not letters.sent, detail=str(letters.sent[:1]))
        # Written while the owner is on the staff address, its links are still the public site's.
        was_home = m.house_address
        m.house_address = lambda: ("https", "www.chateaugudanes.com")
        try:
            with m.app.test_request_context("/", base_url="https://admin.chateaugudanes.com"):
                _subj, body, _fp, _b = m.occasion_newsletter_parts(conn, _occ(conn, name))
                pics = m.occasion_newsletter_pictures(conn, _occ(conn, name))
        finally:
            m.house_address = was_home
        s.check("drafted from the staff address, its links and pictures are on the public site",
                "https://www.chateaugudanes.com/book?arrival=" in body and "admin." not in body
                and pics and all(p["src"].startswith("https://www.chateaugudanes.com/") for p in pics),
                detail=body[-300:])
        for state in ("draft", "dismissed"):
            other_name = f"{TAG} A {state} one"
            oc.post("/admin/stay-occasions/new", data=_form(other_name, n1, n1, serve_dinner="1", dinner_price="50"))
            oo = _occ(conn, other_name)
            oc.post(f"/admin/stay-occasions/{oo['id']}", data=_form(other_name, n1, n1, **dict(
                status=state, serve_dinner="1", dinner_price="50", home_banner="1")))
            s.check(f"a {state} occasion makes no draft", not conn.execute(
                "SELECT 1 FROM campaign_templates WHERE occasion_id = ?", (oo["id"],)).fetchone())
    tmpl = again[0] if again else None
    with m.app.test_request_context("/"):
        found, _dropped = m.watch_task_findings(conn)
        warn = m.owner_home_warnings(conn, m.house_today())
    title = next((f[1] for f in found if f[0] == "newsletter" and name in (f[2] or "")), None)
    s.check("a task for the owner: the newsletter ready to approve",
            title and title.startswith("Newsletter updated, ready to approve: "), detail=str(title))
    s.check("and a line on the owner home", any("ready to approve" in w["title"] for w in warn))
    with _Letters() as letters, m.app.test_request_context("/"):
        m.send_campaign(conn, tmpl, {"owner.zdin@example.invalid": "The Owner"}, owner["id"], as_test=True)
    test_letter = letters.sent[-1] if letters.sent else {}
    s.check("the owner's test letter draws the menu picture and the button",
            "/menu" in test_letter.get("html", "") and "<img" in test_letter.get("html", "")
            and "Book your stay" in test_letter.get("html", ""), detail=test_letter.get("subject", ""))
    s.check("a test to the owner does not count as sent", not m.occasion_newsletter_sent(conn, tmpl["id"]))
    conn.execute("""INSERT INTO campaign_sends (template_id, template_name, recipient_email, subject, status,
                        created_at) VALUES (?, ?, 'someone.zdin@example.invalid', ?, 'sent', ?)""",
                 (tmpl["id"], tmpl["name"], tmpl["subject"], _harness.datetime_now()))
    conn.commit()
    with m.app.test_request_context("/"):
        found, _dropped = m.watch_task_findings(conn)
    s.check("sent, the task closes itself", not any(f[0] == "newsletter" and name in (f[2] or "") for f in found))
    oc.post(f"/admin/stay-occasions/{oid}", data=_form(name, n1, n2, **dict(news, dinner_price="80",
                                                                        confirm_dinner_price="1")))
    after_send = conn.execute("SELECT body FROM campaign_templates WHERE id = ?", (tmpl["id"],)).fetchone()
    s.check("and once sent, a change to the occasion does not touch it", "€80" not in after_send["body"])

    # ------------------------------------------------------------------
    _browser(s, anon, conn, oid, name, occ_page)


PROBE = r"""<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) { var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || ''); });
  function report() {
    var out = {label: q.label, w: innerWidth};
    try {
      var de = document.documentElement;
      if (q.kind === 'menu') {
        var a = document.querySelector('[data-menu-open]'), d = document.getElementById('g-menudlg');
        out.link = !!a;
        /* as a tap or the keyboard would: nothing focused first, the page itself */
        a.click();
        out.open = d.open;
        var p = d.querySelector('.g-menudlg__in').getBoundingClientRect();
        out.panel = {left: Math.round(p.left), right: Math.round(p.right)};
        out.sw = de.scrollWidth; out.cw = de.clientWidth;
        var img = d.querySelector('.g-menudlg__b img');
        out.alt = img ? img.alt : null;
        out.pdf = !!d.querySelector('.g-menudlg__b iframe');
        out.acts = !d.querySelector('.g-menudlg__acts').hidden;
        d.querySelector('.g-menudlg__x').click();
        out.closed_x = !d.open;
        setTimeout(function () {
          out.focus_back = document.activeElement === a;
          var ae = document.activeElement; out.active = ae ? (ae.tagName + '.' + ae.className) : null;
          a.click();
          d.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          out.closed_outside = !d.open;
          parent.postMessage(JSON.stringify(out), '*');
        }, 100);
        return;
      } else {
        var b = document.querySelector('.g-hband');
        out.banner = !!b;
        out.sw = de.scrollWidth; out.cw = de.clientWidth;
        if (b) {
          out.after_hero = !!(b.previousElementSibling && b.previousElementSibling.classList.contains('g-hero'));
          out.btns = [].map.call(b.querySelectorAll('.g-hband__btns a'), function (x) {
            var r = x.getBoundingClientRect(); return {h: Math.round(r.height), right: Math.round(r.right)}; });
          var ph = b.querySelector('.g-hband__ph').getBoundingClientRect();
          out.photo = {w: Math.round(ph.width), h: Math.round(ph.height)};
        }
      }
    } catch (e) { out.error = String(e); }
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(report, 600); });
})();
</script>"""

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<script>window.addEventListener('message', function (e) { var p = document.createElement('pre'); p.className = 'r';
p.textContent = e.data; document.body.appendChild(p); });</script>
%s
</body></html>"""


def _inline(page):
    """The page with the site's stylesheets inline and nothing fetched."""
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


def _measure(browser, pages, workdir):
    cells = []
    for i, (label, kind, width, page) in enumerate(pages):
        fname = f"p{i}.html"
        with open(os.path.join(workdir, fname), "w", encoding="utf-8") as fh:
            fh.write(_inline(page).replace("</body>", PROBE + "</body>", 1))
        cells.append(f'<iframe src="{fname}#label={label}&kind={kind}" '
                     f'style="display:block;border:0;width:{width}px;height:800px"></iframe>')
    wrapper = os.path.join(workdir, "wrapper.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER % "\n".join(cells))
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--user-data-dir=" + os.path.join(workdir, "profile"),
           "--hide-scrollbars", "--allow-file-access-from-files", "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=20000", "--dump-dom", "file:///" + wrapper.replace("\\", "/").lstrip("/")]
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    return {json.loads(htmllib.unescape(b)).get("label"): json.loads(htmllib.unescape(b))
            for b in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S)}


def _browser(s, anon, conn, oid, name, occ_page):
    s.section("In a browser: the pop-up on a 375px phone, the banner on a 320px one")
    browser = find_browser()
    s.check("headless Chrome is on this machine to lay the pages out", bool(browser),
            detail="set GUDANES_CHROME; a check that could not run is not a pass")
    if not browser:
        return
    picture = anon.get(occ_page).get_data(as_text=True)
    home = anon.get("/").get_data(as_text=True)
    pdf_page = picture
    occ = conn.execute("SELECT * FROM stay_occasions WHERE id = ?", (oid,)).fetchone()
    # The same page with the PDF menu, written straight to the occasion for the length of one render.
    was = occ["menu_file"]
    name_pdf = f"menu-zdin{oid}.pdf"
    with open(os.path.join(m.OCCASION_FILES_DIR, name_pdf), "wb") as fh:
        fh.write(_pdf())
    conn.execute("UPDATE stay_occasions SET menu_file = ? WHERE id = ?", (name_pdf, oid))
    conn.commit()
    pdf_page = anon.get(occ_page).get_data(as_text=True)
    conn.execute("UPDATE stay_occasions SET menu_file = ? WHERE id = ?", (was, oid))
    conn.commit()
    m.remove_occasion_file(name_pdf)
    pages = [("menu-image-375", "menu", 375, picture), ("menu-pdf-375", "menu", 375, pdf_page),
             ("home-320", "home", 320, home), ("home-375", "home", 375, home)]
    # One page to a run: frames side by side pass focus between them as their dialogs open, and which one is
    # holding it when another closes is then a matter of timing, not of the page.
    seen = {}
    for page in pages:
        seen.update(_measure(browser, [page], tempfile.mkdtemp(prefix="gudanes_dinner_")))
    s.check("every page reported back", len(seen) == len(pages) and not any(v.get("error") for v in seen.values()),
            detail=str({k: v.get("error") for k, v in seen.items()}))
    for label in ("menu-image-375", "menu-pdf-375"):
        got = seen.get(label) or {}
        s.check(f"{label}: See the menu opens the pop-up", got.get("link") and got.get("open"), detail=str(got))
        s.check(f"{label}: it fits the phone, nothing scrolls sideways",
                got.get("sw", 10 ** 4) <= got.get("cw", 0) and (got.get("panel") or {}).get("left", -1) >= 0
                and (got.get("panel") or {}).get("right", 10 ** 4) <= 375, detail=str(got))
        s.check(f"{label}: closes on the cross, and focus goes back to the link",
                got.get("closed_x") and got.get("focus_back"), detail=str(got))
        s.check(f"{label}: and on a tap outside", got.get("closed_outside"), detail=str(got))
    got = seen.get("menu-image-375") or {}
    s.check("a picture menu fills it, with its alt text",
            (got.get("alt") or "").startswith("The menu for Dinner at Gudanes"), detail=str(got.get("alt")))
    got = seen.get("menu-pdf-375") or {}
    s.check("a PDF sits in a viewer, with Open in a new tab and Download beside it",
            got.get("pdf") and got.get("acts"), detail=str(got))
    for label, width in (("home-320", 320), ("home-375", 375)):
        got = seen.get(label) or {}
        s.check(f"{label}: the banner is drawn, straight after the video", got.get("banner") and got.get("after_hero"),
                detail=str(got))
        s.check(f"{label}: nothing scrolls sideways", got.get("sw", 10 ** 4) <= got.get("cw", 0), detail=str(got))
        btns = got.get("btns") or []
        s.check(f"{label}: its buttons are at least 48px tall and inside the screen",
                btns and all(b["h"] >= 48 and b["right"] <= width for b in btns), detail=str(btns))
        ph = got.get("photo") or {}
        s.check(f"{label}: the photograph on top, at 16:9", ph.get("w") and abs(ph["w"] / max(ph["h"], 1) - 16 / 9) < 0.05,
                detail=str(ph))


if __name__ == "__main__":
    print(run().report())
