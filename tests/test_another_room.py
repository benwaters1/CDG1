"""Another room, typed at the desk (owner, 28 November 2026).

Add a booking, step 3: "Another room (type the name)" under the room cards,
for a room that is not on the Rooms list -- two listed rooms opened into each
other and sold as one, "Les Chambres Communicantes". The desk types its name,
what it sleeps, the price for the stay, and ticks any listed room it takes.

WHAT THIS PINS.
  - The form offers it, and refuses it without a name, without a price, for
    a party bigger than it sleeps, under a listed room's name, or over a
    ticked room that is taken -- and a refusal leaves no room behind.
  - Book it and send a payment link (the owner's own test): 2 adults and 2
    under 18, EUR 1,600 typed, confirmed; the bill is EUR 1,600 and the taxe
    de sejour for the two adults; the letter, the guest's booking page, the
    card payment, the statement and the arrival card all carry the name.
  - It shows where a room's name does: the bookings list, the stay's page,
    both calendars, the CSV export. Never on the Rooms list, the website, the
    search's rooms, or a drop-down of rooms.
  - The rooms ticked are taken for its nights: the gate refuses them, a
    room's calendar greys them, Booking.com's feed carries them, the front
    desk and the room board show them occupied. A room not ticked is free.
  - The three-rooms-a-night limit counts it as the rooms it takes, or as one
    room when it takes none.
  - Moving its dates re-prices it pro rata, not at a rate card of nothing.
  - Book it now with no email; reserve it for them; a lapsed hold frees the
    rooms it took.
  - Reports and exports take it: its revenue is a row of the breakdown, its
    rooms are in the occupancy count, and the pages answer.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import BOOKING_DETAILS, Suite, clients, db, house_today
import _harness

m = _harness.m
NAME = "Les Chambres Communicantes"
GITE = "ZZ Le Gite du Jardin"
MAIL = "zzanother."


def _cleanup(conn, made=()):
    ids = f"(SELECT id FROM bookings WHERE guest_email LIKE '{MAIL}%' OR guest_name LIKE 'Zzanother%')"
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {ids}")
    conn.execute(f"DELETE FROM tasks WHERE booking_id IN {ids}")
    rooms = [r[0] for r in conn.execute(f"SELECT DISTINCT room_id FROM bookings WHERE id IN {ids}")]
    conn.execute(f"DELETE FROM bookings WHERE id IN {ids}")
    conn.execute(f"DELETE FROM guests WHERE email LIKE '{MAIL}%' OR name LIKE 'Zzanother%'")
    conn.execute(f"DELETE FROM mail_log WHERE to_address LIKE '{MAIL}%'")
    gone = set(made) | {r for r in rooms if conn.execute(
        "SELECT 1 FROM rooms WHERE id = ? AND one_off = 1", (r,)).fetchone()}
    for rid in gone:
        conn.execute("DELETE FROM one_off_room_blocks WHERE one_off_room_id = ?", (rid,))
        conn.execute("DELETE FROM rooms WHERE id = ? AND one_off = 1", (rid,))
    conn.commit()


def _one_offs(conn):
    return {r[0] for r in conn.execute("SELECT id FROM rooms WHERE one_off = 1")}


def _text(page):
    import html
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _empty_window(conn, rooms, nights, start_days):
    """The first run of nights from start_days on where every room is free
    and nothing is let -- so the limit checks below count only this suite."""
    a = house_today() + timedelta(days=start_days)
    for _ in range(80):
        b = a + timedelta(days=nights + 1)
        if all(m.is_range_available(conn, r["id"], a, b)[0] for r in rooms):
            pic = m._availability_picture(conn, fresh=True)
            if not any(m.rooms_let_on(pic, a + timedelta(days=i)) for i in range(nights + 1)):
                return a
        a += timedelta(days=9)
    return None


def run():
    s = Suite("Another room, typed at the desk")
    conn = db()
    _cleanup(conn)
    before = _one_offs(conn)
    sent = []
    was_send = m.send_email

    def capture(to, subject, body, *a, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "html": kw.get("html") or ""})
        return True

    m.send_email = capture
    try:
        _checks(s, conn, sent)
    finally:
        m.send_email = was_send
        _cleanup(conn, _one_offs(conn) - before)
        conn.close()
    return s


def _post(owner, **form):
    base = {"adults": "2", "guests_under_18": "0", "party_size": "2"}
    base.update(form)
    return owner.post("/admin/bookings/walk-in", data=base, follow_redirects=True)


def _checks(s, conn, sent):
    owner = clients()[0]
    listed = conn.execute(
        "SELECT * FROM rooms WHERE active = 1 AND one_off = 0 ORDER BY sort_order, name").fetchall()
    s.check("the house has at least three rooms to work with", len(listed) >= 3)
    if len(listed) < 3:
        return
    A, B, C = listed[0], listed[1], listed[2]
    a = _empty_window(conn, listed, 6, 160)
    s.check("an empty week to book into", a is not None)
    if a is None:
        return
    b = a + timedelta(days=4)
    nights = 4

    s.section("The form offers another room")
    page = owner.get("/admin/bookings/walk-in").get_data(as_text=True)
    s.check("a tile under the room cards", 'value="custom"' in page
            and "Another room (type the name)" in page)
    s.check("with its name, what it sleeps and the rooms it takes",
            'name="custom_room_name"' in page and 'name="custom_sleeps"' in page
            and page.count('name="custom_blocks"') == len(listed))

    s.section("Refused, and nothing left behind")
    made_before = _one_offs(conn)
    common = dict(mode="book", room_id="custom", arrival_date=a.isoformat(),
                  departure_date=b.isoformat(), guest_name="Zzanother Refused",
                  custom_sleeps="4", total_price="900")
    r = _text(_post(owner, **dict(common, custom_room_name="")).get_data(as_text=True))
    s.check("without a name", "Give the room a name" in r, detail=r[:300])
    r = _text(_post(owner, **dict(common, custom_room_name=NAME, total_price="")).get_data(as_text=True))
    s.check("without a price: there is no rate card", "Put in the price for the stay" in r)
    r = _text(_post(owner, **dict(common, custom_room_name=NAME, custom_sleeps="3",
                                  adults="2", guests_under_18="2", party_size="4")).get_data(as_text=True))
    s.check("for more guests than it sleeps", f"{NAME} sleeps 3" in r, detail=r[:300])
    r = _text(_post(owner, **dict(common, custom_room_name=A["name"].upper())).get_data(as_text=True))
    s.check("under the name of a room on the list", "already on the Rooms list" in r)
    s.check("and none of those left a room behind", _one_offs(conn) == made_before)
    s.check("nor a stay", not conn.execute(
        "SELECT 1 FROM bookings WHERE guest_name = 'Zzanother Refused'").fetchone())

    s.section("Book it and send a payment link: the owner's own test")
    to = f"{MAIL}famille@example.invalid"
    page = _post(owner, mode="paylink", room_id="custom", custom_room_name=NAME,
                 custom_sleeps="4", custom_blocks=[str(A["id"]), str(B["id"])],
                 arrival_date=a.isoformat(), departure_date=b.isoformat(),
                 guest_name="Zzanother Famille", guest_email=to,
                 adults="2", guests_under_18="2", party_size="4", total_price="1600")
    stay = conn.execute("SELECT * FROM bookings WHERE guest_email = ?", (to,)).fetchone()
    s.check("the stay is written", stay is not None, detail=_text(page.get_data(as_text=True))[:400])
    if not stay:
        return
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (stay["room_id"],)).fetchone()
    s.check("in a room of its own, under the name typed", room["name"] == NAME)
    s.check("never listed and never on sale", room["one_off"] == 1 and room["active"] == 0)
    s.check("sleeping the four typed", room["max_occupancy"] == 4)
    s.check("taking the two rooms ticked", sorted(i for i, _n in m.one_off_rooms_used(
        conn, room["id"])) == sorted([A["id"], B["id"]]))
    s.check("confirmed, with the party", stay["status"] == "confirmed"
            and stay["party_size"] == 4 and stay["guests_under_18"] == 2)
    bill = m.booking_bill(conn, stay["id"])
    tax = m.compute_city_tax(conn, 4, 2, nights)[0]
    room_lines = [l for l in bill["lines"] if l.get("kind") == "room"]
    s.check("the room at EUR 1,600, as typed", len(room_lines) == 1
            and abs(room_lines[0]["amount"] - 1600) < 0.01 and NAME in room_lines[0]["label"],
            detail=str(bill["lines"]))
    s.check("no discount off a rate card it does not have",
            not any(l.get("kind") == "discount" for l in bill["lines"]) and not stay["discount_amount"])
    s.check("the taxe de sejour worked out from the two adults",
            tax > 0 and abs((stay["city_tax"] or 0) - tax) < 0.01
            and abs(bill["total"] - (1600 + tax)) < 0.01,
            detail=f"{stay['city_tax']} vs {tax}, total {bill['total']}")
    flash = _text(page.get_data(as_text=True))
    s.check("the desk is told the letter went, with what it pays",
            f"{m.money(1600 + tax)}" in flash and "has gone to" in flash, detail=flash[:400])
    s.check("and not that it is above a rate card", "above the rate card" not in flash)

    s.section("The letter, the guest's page and the card")
    letter = [x for x in sent if x["to"] == to]
    s.check("one confirmation to the guest", len(letter) == 1, detail=str([x["subject"] for x in letter]))
    if letter:
        drawn = _text(letter[0]["html"])
        s.check("named in its subject and its words", NAME in letter[0]["subject"]
                and NAME in letter[0]["body"] and NAME in drawn)
        s.check("with what is to pay: the EUR 1,600 and the taxe de sejour together",
                m.money(1600 + tax) in drawn and "Pay" in drawn)
    guest = m.app.test_client()
    mp = _text(guest.get(f"/book/manage/{stay['manage_token']}").get_data(as_text=True))
    s.check("their booking page names it and itemises the bill",
            NAME in mp and "1,600.00" in mp and m.money(tax) in mp, detail=mp[:300])
    st = _text(guest.get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True))
    s.check("so does their statement", NAME in st and "1,600.00" in st and m.money(tax) in st)
    calls = []

    class _Checkout:
        url = "https://checkout.example.invalid/zzanother"

    def create(**kw):
        calls.append(kw)
        return _Checkout()

    was_enabled, was_create = m.stripe_enabled, m.stripe.checkout.Session.create
    m.stripe_enabled = lambda: True
    m.stripe.checkout.Session.create = create
    try:
        pay = guest.get(f"/book/pay/{stay['manage_token']}")
    finally:
        m.stripe_enabled, m.stripe.checkout.Session.create = was_enabled, was_create
    line = calls[0]["line_items"][0]["price_data"] if calls else {}
    s.check("Pay now opens the card page", pay.status_code == 303 and calls)
    s.check("charging the room and the tax, under the room's name",
            line.get("unit_amount") == int(round((1600 + tax) * 100))
            and NAME in line.get("product_data", {}).get("name", ""), detail=str(line))
    card = owner.get(f"/admin/bookings/{stay['id']}/card")
    s.check("the arrival card names it", card.status_code == 200 and NAME in card.get_data(as_text=True))

    s.section("Where a room's name goes, it goes")
    bp = _text(owner.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True))
    s.check("the stay's page, with the rooms it takes", NAME in bp and "Typed at the desk" in bp
            and A["name"] in bp and B["name"] in bp)
    s.check("the bookings list", NAME in owner.get(
        "/admin/bookings?q=Zzanother+Famille").get_data(as_text=True))
    cal = owner.get(f"/calendar?view=week&date={a.isoformat()}").get_data(as_text=True)
    s.check("the house calendar", NAME in cal)
    month = owner.get(f"/admin/calendar?month={a.strftime('%Y-%m')}").get_data(as_text=True)
    s.check("the month of rooms: in each room it takes, under its name",
            month.count(f"Zzanother Famille · {NAME}") >= 2, detail=str(month.count(NAME)))
    with m.app.test_request_context():
        rows = m.room_month_rows(conn, a.replace(day=1))[1]
    s.check("and no row of its own there", not any(r["room"]["id"] == room["id"] for r in rows))
    csv = owner.get("/admin/bookings/export.csv").get_data(as_text=True)
    s.check("the CSV export", NAME in csv)
    edit = owner.get(f"/admin/bookings/{stay['id']}/edit").get_data(as_text=True)
    s.check("its edit page keeps it in its own room",
            re.search(rf'<option value="{room["id"]}"\s*selected', edit) is not None)

    s.section("Not on the Rooms list, the website or a drop-down")
    s.check("not on the Rooms list", NAME not in owner.get("/admin/rooms").get_data(as_text=True))
    s.check("not offered on the website", NAME not in m.app.test_client().get(
        f"/book?arrival={a.isoformat()}&departure={b.isoformat()}").get_data(as_text=True))
    s.check("not a room to book online", m.app.test_client().get(
        f"/api/availability/{room['id']}").status_code == 404)
    found = owner.get(f"/search?q={NAME.split()[-1]}").get_data(as_text=True)
    s.check("not among the rooms the search finds", f"/admin/rooms/{room['id']}/edit" not in found)
    s.check("not in the rooms to put a breakage or a Booking.com stay in",
            NAME not in owner.get("/management/breakages").get_data(as_text=True)
            and NAME not in owner.get("/admin/channel-stays").get_data(as_text=True))
    desk_page = owner.get(
        f"/admin/bookings/walk-in?arrival_date={a.isoformat()}&departure_date={b.isoformat()}"
    ).get_data(as_text=True)
    s.check("nor a room card or a room to block on the add-a-booking page",
            f'value="{room["id"]}"' not in desk_page)

    s.section("The rooms it takes are taken, and only those")
    for r in (A, B):
        ok, why = m.is_range_available(conn, r["id"], a, b)
        s.check(f"{r['name']} refuses its nights", not ok and why == m.DATES_OVERLAP_BOOKING,
                detail=str(why))
    s.check(f"{C['name']}, not ticked, is free", m.is_range_available(conn, C["id"], a, b)[0])
    s.check("the night before and the morning it leaves are free",
            m.is_range_available(conn, A["id"], a - timedelta(days=1), a)[0]
            and m.is_range_available(conn, A["id"], b, b + timedelta(days=1))[0])
    greyed = m.unavailable_nights(conn, A["id"], a, b)
    s.check("its room page greys the nights", sorted(greyed) == [
        (a + timedelta(days=i)).isoformat() for i in range(nights)], detail=str(greyed))
    ics = m.generate_room_ics(conn, A)
    s.check("Booking.com's feed for it carries the stay", f"UID:booking-{stay['id']}@" in ics)
    s.check("and the feed of a room not ticked does not",
            f"UID:booking-{stay['id']}@" not in m.generate_room_ics(conn, C))
    r = _text(_post(owner, mode="book", room_id=str(A["id"]), arrival_date=a.isoformat(),
                    departure_date=(a + timedelta(days=1)).isoformat(),
                    guest_name="Zzanother Listed").get_data(as_text=True))
    s.check("the desk cannot let a room it takes", "already taken" in r
            and not conn.execute("SELECT 1 FROM bookings WHERE guest_name = 'Zzanother Listed'").fetchone())
    made_before = _one_offs(conn)
    r = _text(_post(owner, mode="book", room_id="custom", custom_room_name="ZZ Overlap",
                    custom_sleeps="2", custom_blocks=[str(B["id"])], total_price="500",
                    arrival_date=(b - timedelta(days=1)).isoformat(),
                    departure_date=(b + timedelta(days=1)).isoformat(),
                    guest_name="Zzanother Overlap").get_data(as_text=True))
    s.check("nor another room that takes one of them", "already taken" in r
            and f"it uses {B['name']}" in r, detail=r[:400])
    s.check("and that refusal left no room behind", _one_offs(conn) == made_before)
    with m.app.test_request_context():
        desk = m.front_desk_day(conn, today=a)
        board = {x["room"]["id"]: x for x in m.room_board(conn, today=a + timedelta(days=1))}
        sheet = m.arrivals_calendar(conn, a, a + timedelta(days=2))
    states = {x["room"]["id"]: x for x in desk["rooms"]}
    s.check("the front desk has its guests arriving in both rooms",
            all(states[r["id"]]["state"] == "Arriving"
                and states[r["id"]]["arriving"]["id"] == stay["id"] for r in (A, B))
            and states[C["id"]]["state"] != "Arriving", detail=str({k: v["state"] for k, v in states.items()}))
    s.check("two rooms let that night", desk["let_tonight"] == 2, detail=str(desk["let_tonight"]))
    s.check("the room board has them occupied the night after",
            all((board.get(r["id"], {}).get("in_house") or {"id": None})["id"] == stay["id"]
                for r in (A, B))
            and not board.get(C["id"], {}).get("in_house"),
            detail=str({k: v.get("state") for k, v in board.items()}))
    named = {row["room"] for row in sheet["rows"]
             if any("Zzanother Famille" in (c.get("label") or "") for c in row["cells"])}
    s.check("the arrivals sheet sleeps them in the two rooms, not a row of their own",
            named == {A["name"], B["name"]}, detail=str(named))

    s.section("The house's limit counts it as the rooms it takes")
    pic = m._availability_picture(conn, fresh=True)
    s.check("two rooms let, not three", m.rooms_let_on(pic, a) == {A["id"], B["id"]},
            detail=str(m.rooms_let_on(pic, a)))
    r = _post(owner, mode="book", room_id="custom", custom_room_name=GITE, custom_sleeps="2",
              arrival_date=a.isoformat(), departure_date=b.isoformat(),
              guest_name="Zzanother Gite", total_price="300")
    gite = conn.execute("SELECT * FROM bookings WHERE guest_name = 'Zzanother Gite'").fetchone()
    s.check("another room taking none of the listed ones, with no email: booked",
            gite is not None and gite["status"] == "confirmed" and not gite["guest_email"],
            detail=_text(r.get_data(as_text=True))[:300])
    pic = m._availability_picture(conn, fresh=True)
    if gite:
        s.check("it counts as one room let", m.rooms_let_on(pic, a) == {A["id"], B["id"], gite["room_id"]})
        with m.app.test_request_context():
            gite_rows = m.room_month_rows(conn, a.replace(day=1))[1]
        s.check("in a month with its stay, the calendar gives it a row of its own",
                any(r["room"]["id"] == gite["room_id"] for r in gite_rows))
    ok, why = m.is_range_available(conn, C["id"], a, b)
    s.check(f"three let: {C['name']} is not for sale", not ok and m.is_house_full_reason(why),
            detail=str(why))
    made_before = _one_offs(conn)
    r = _text(_post(owner, mode="book", room_id="custom", custom_room_name="ZZ Fourth",
                    custom_sleeps="2", arrival_date=a.isoformat(), departure_date=b.isoformat(),
                    guest_name="Zzanother Fourth", total_price="300").get_data(as_text=True))
    s.check("nor a fourth room typed at the desk", "The house is full" in r, detail=r[:300])
    s.check("which left no room behind", _one_offs(conn) == made_before)

    s.section("Reports and exports take it")
    start = a.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    if a + timedelta(days=nights) > end:
        end = (end + timedelta(days=32)).replace(day=1)
    rows = {r["room_name"]: r for r in m.room_revenue_breakdown(conn, start, end)}
    s.check("its revenue is a row of the breakdown, under its name",
            NAME in rows and abs(rows[NAME]["revenue"] - 1600) < 0.01, detail=str(list(rows)))
    s.check("one row per name, not per stay, and none for a room with no stay",
            list(rows).count(NAME) == 1 and "ZZ Overlap" not in rows)
    counted = m.occupied_rooms_by_date(conn, a, b)
    s.check("occupancy counts the two rooms it takes each night",
            all(counted[(a + timedelta(days=i)).isoformat()] == 2 for i in range(nights)),
            detail=str(counted))
    for path in ["/admin/bookings/export.csv",
                 f"/admin/reports/occupancy?period=month&date={a.isoformat()}",
                 f"/admin/reports/occupancy/export.csv?period=month&date={a.isoformat()}",
                 f"/admin/reports/financial?period=month&date={a.isoformat()}",
                 f"/admin/reports/financial/export.csv?period=month&date={a.isoformat()}",
                 f"/admin/reports/pace?period=month&date={a.isoformat()}",
                 f"/admin/reports/guest?period=month&date={a.isoformat()}",
                 f"/management/financials/annual-summary?year={a.year}",
                 "/management/rooms-league", "/management/rooms-league.csv",
                 "/management/rooms-economics", "/admin/city-tax/export.csv",
                 "/admin/vat/export.csv", "/admin/transactions/export.csv",
                 f"/admin/arrivals?date={a.isoformat()}", "/admin/desk", "/today", "/",
                 f"/admin/calendar?month={a.strftime('%Y-%m')}",
                 f"/admin/rooms/close?month={a.strftime('%Y-%m')}",
                 f"/guests/{stay['linked_guest_id']}/statement.csv"]:
        got = owner.get(path)
        s.check(f"{path.split('?')[0]} answers", got.status_code == 200, detail=str(got.status_code))

    s.section("Moving its dates re-prices it by the night, not at nothing")
    moved = owner.post(f"/admin/bookings/{stay['id']}/move-dates", data={
        "arrival_date": a.isoformat(), "departure_date": (b + timedelta(days=1)).isoformat()},
        follow_redirects=True)
    after = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["id"],)).fetchone()
    s.check("five nights at the 400 a night agreed", after["departure_date"] == (b + timedelta(days=1)).isoformat()
            and abs((after["total_price"] or 0) - 2000) < 0.01,
            detail=f"{after['departure_date']} {after['total_price']} {_text(moved.get_data(as_text=True))[:200]}")
    s.check("and the rooms it takes go with it", not m.is_range_available(conn, A["id"], b, b + timedelta(days=1))[0])

    s.section("Reserve it for them: held, then let go")
    w = _empty_window(conn, listed, 3, 260)
    s.check("an empty run of nights for it", w is not None)
    if w is None:
        return
    to2 = f"{MAIL}reserve@example.invalid"
    _post(owner, mode="reserve", room_id="custom", custom_room_name=NAME, custom_sleeps="3",
          custom_blocks=[str(C["id"])], arrival_date=w.isoformat(),
          departure_date=(w + timedelta(days=2)).isoformat(), guest_email=to2,
          adults="3", party_size="3", total_price="750", hold_days="1")
    held = conn.execute("SELECT * FROM bookings WHERE guest_email = ?", (to2,)).fetchone()
    s.check("reserved, waiting on them", held is not None and held["status"] == "pending"
            and held["reserved_until"])
    if not held:
        return
    s.check("in a room of its own, not the first one's", held["room_id"] != stay["room_id"])
    letters = [x for x in sent if x["to"] == to2]
    s.check("one letter, with the link to finish, naming the room",
            len(letters) == 1 and NAME in (letters[0]["body"] + letters[0]["html"]),
            detail=str([x["subject"] for x in letters]))
    fin = _text(m.app.test_client().get(f"/booking/{held['manage_token']}/finish").get_data(as_text=True))
    s.check("their page to finish names it, at the price agreed", NAME in fin and "750.00" in fin)
    too_many = dict(guest_name="Zzanother Reserve", adults="4", guests_under_18="0",
                    agree_terms="on", **BOOKING_DETAILS)
    m.app.test_client().post(f"/booking/{held['manage_token']}/finish", data=too_many)
    s.check("and hold them to what it sleeps", conn.execute(
        "SELECT details_done_at FROM bookings WHERE id = ?", (held["id"],)).fetchone()[0] is None)
    s.check(f"{C['name']} is held while they decide",
            not m.is_range_available(conn, C["id"], w, w + timedelta(days=2))[0])
    with m.app.test_request_context():
        m.release_lapsed_reservations(conn, now=datetime.now(timezone.utc) + timedelta(days=3))
    s.check("the hold runs out", conn.execute(
        "SELECT status FROM bookings WHERE id = ?", (held["id"],)).fetchone()[0] == "declined")
    s.check(f"and {C['name']} is for sale again, with nothing to tidy",
            m.is_range_available(conn, C["id"], w, w + timedelta(days=2))[0])


if __name__ == "__main__":
    print(run().report())
