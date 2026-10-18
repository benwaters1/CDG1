"""Three rooms let a night, never four (z28 item 1, owner, 17 October 2026;
three since 18 October -- "its as we are saying private bathroom").

Written against MAX_ROOMS_LET_PER_NIGHT, so "the full set" below is however
many that is, and "the next room" is one more. Five was the first number.

THE RULE. Chambres d'hôtes are limited to five rooms and fifteen guests (Code
du tourisme L324-3, D324-13). The house has six rooms since 17 October, and
the owner chose "never more than five rooms let on any one night". The sixth
room stays off sale until this is live, so this is what lets it go on sale.

WHAT THIS PINS.
  - The full set taken refuses the next room: in the booking check, on the Stay
    page, in the quote, on the room page's calendar, and in the booking
    itself. "The house is full", not "not available".
  - Four taken does not.
  - The owner's closures do not count; a channel's imported nights do; a
    channel range that is one of the house's own closures sent back does not,
    and the owner is told the channel is sending them.
  - The spare room's calendar link closes that night for the channels, and a
    room taken off sale here closes every night there.
  - One more let anyway -- two channel bookings inside one sync -- is said on the
    owner home and raised as a task, which closes itself.
  - Nightly lettings only: a workshop's rooming is not counted, refused or
    warned about (owner, 18 October -- workshops are a separate thing).
"""
import re
from datetime import timedelta

from _harness import BOOKING_DETAILS, Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZLET"
N = m.MAX_ROOMS_LET_PER_NIGHT
OVER_TASK = f"More than {N} rooms let on%"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM ical_sources WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (OVER_TASK,))
    conn.commit()


def _ask(fn, *args, **kw):
    conn = db()
    try:
        with m.app.test_request_context():
            return fn(conn, *args, **kw)
    finally:
        conn.close()


def _book(conn, room_id, a, b, status="confirmed", n=0):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 300, ?)""",
        (room_id, f"{TAG}{room_id}N{n}", f"tok-{TAG.lower()}-{room_id}-{n}",
         f"{TAG} Guest {room_id}", f"zzlet{room_id}@example.invalid",
         a.isoformat(), b.isoformat(), status, _harness.datetime_now()))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _ics_spans(feed):
    """[(uid, DTSTART, DTEND)] out of a calendar, as dates."""
    out = []
    for ev in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", feed, re.S):
        uid = re.search(r"UID:([^\r\n]+)", ev).group(1)
        a = re.search(r"DTSTART;VALUE=DATE:(\d{8})", ev).group(1)
        b = re.search(r"DTEND;VALUE=DATE:(\d{8})", ev).group(1)
        out.append((uid, m.date(int(a[:4]), int(a[4:6]), int(a[6:])),
                    m.date(int(b[:4]), int(b[4:6]), int(b[6:]))))
    return out


def run():
    s = Suite("Three rooms let a night")
    clients()
    conn = db()
    _cleanup(conn)
    put_back = []
    try:
        _checks(s, conn, put_back)
    finally:
        for sql, args in put_back:
            conn.execute(sql, args)
        conn.commit()
        _cleanup(conn)
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        conn.close()
    return s


def _checks(s, conn, put_back):
    s.section("One value")
    s.check("three rooms a night: one bathroom each (owner, 18 October 2026)", N == 3)
    owner = clients()[0]
    s.check("said on the owner's Rooms page",
            f"No more than <b>{N} rooms let on any one night</b>"
            in owner.get("/admin/rooms").get_data(as_text=True))

    # Six rooms on sale to work with: the seeded sixth room ticked, or one of
    # this suite's own on a database that does not have it. Put back after.
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    if len(rooms) < 6:
        hidden = conn.execute("SELECT id FROM rooms WHERE active = 0 ORDER BY id").fetchall()
        for h in hidden[:6 - len(rooms)]:
            conn.execute("UPDATE rooms SET active = 1 WHERE id = ?", (h["id"],))
            put_back.append(("UPDATE rooms SET active = 0 WHERE id = ?", (h["id"],)))
        conn.commit()
        rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    while len(rooms) < 6:
        conn.execute("""INSERT INTO rooms (name, export_token, active, max_occupancy,
                        max_adults, price_per_night, min_nights, sort_order)
                        VALUES (?, ?, 1, 2, 2, 300, 1, 90)""",
                     (f"{TAG} Room {len(rooms)}", f"tok-{TAG.lower()}-room-{len(rooms)}"))
        conn.commit()
        rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    rooms = [dict(r) for r in rooms]
    let_rooms, spare = rooms[:N], rooms[N]
    s.check("there are six rooms on sale to test with", len(rooms) >= 6)

    # Two nights nothing else holds: well inside the year the feed sells.
    a = None
    for weeks in range(30, 46):
        try_a = house_today() + timedelta(days=7 * weeks + 2)
        b_ = try_a + timedelta(days=2)
        free = all(_ask(m.is_range_available, r["id"], try_a, b_)[0] for r in rooms)
        with m.app.test_request_context():
            pic = m._availability_picture(conn, fresh=True)
        if free and not any(m.rooms_let_on(pic, try_a + timedelta(days=i)) for i in range(2)):
            a = try_a
            break
    s.check("two clear nights to fill", a is not None)
    if a is None:
        return
    b = a + timedelta(days=2)
    ids = [_book(conn, r["id"], a, b) for r in let_rooms]
    anon = m.app.test_client()

    s.section("The full set taken: the next room is refused, and says why")
    ok, why = _ask(m.is_range_available, spare["id"], a, b)
    s.check("the booking check refuses the next room",
            not ok and why == m.house_full_reason(a), detail=str(why))
    s.check("in plain words", (why or "").startswith("The house is full on"), detail=str(why))
    quote = anon.get(f"/api/quote?room_id={spare['id']}&arrival={a.isoformat()}"
                     f"&departure={b.isoformat()}&party_size=2").get_json() or {}
    s.check("the quote says so before the form is filled in",
            quote.get("available") is False and (quote.get("reason") or "").startswith("The house is full"),
            detail=str(quote.get("reason")))
    page = anon.get(f"/book?arrival={a.isoformat()}&departure={b.isoformat()}").get_data(as_text=True)
    start = page.find(f'id="room-{spare["id"]}"')
    card = page[start:page.find("</article>", start)] if start >= 0 else ""
    s.check("the Stay page's card says the house is full, not 'not available'",
            "The house is full on" in card and "Not available these dates" not in card,
            detail=card[-300:])
    # Owner, 18 October 2026: "just display them booked out and show next
    # available date". Two nights asked, both full: the first arrival that
    # gives two nights is the morning after the last full one.
    nxt = "next free from " + m.format_date_short(b.isoformat())
    s.check("and gives the next date it can be had", nxt in card, detail=card[-300:])
    s.check("so does the quote", nxt in (quote.get("reason") or ""), detail=str(quote.get("reason")))
    nights = _ask(m.unavailable_nights, spare["id"], a, b)
    s.check("the room page's calendar greys both nights, in the same words",
            nights == {a.isoformat(): m.HOUSE_FULL_NIGHT,
                       (a + timedelta(days=1)).isoformat(): m.HOUSE_FULL_NIGHT}, detail=str(nights))
    anon.post(f"/book/{spare['id']}", data={
        "arrival_date": a.isoformat(), "departure_date": b.isoformat(),
        "guest_name": f"{TAG} Sixth", "guest_email": "zzlet.sixth@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "0",
        "special_requests": "", "agree_terms": "on", **BOOKING_DETAILS})
    s.check("and a booking for it is not taken",
            conn.execute("SELECT COUNT(*) FROM bookings WHERE guest_name = ?",
                         (f"{TAG} Sixth",)).fetchone()[0] == 0)
    ok_own, _ = _ask(m.is_range_available, let_rooms[0]["id"], a, b, exclude_booking_id=ids[0])
    s.check("a booking being changed does not count against itself", ok_own)

    s.section("The channels are told")
    feed = _ask(m.generate_room_ics, spare)
    full = [(u, x, y) for u, x, y in _ics_spans(feed) if u.startswith("full-")]
    s.check("the spare room's calendar closes those nights, as one span",
            [(x, y) for _u, x, y in full] == [(a, b)], detail=str(full))
    s.check("in the house's own words", "SUMMARY:Closed — the house is full" in feed)
    booked = _ask(m.generate_room_ics, let_rooms[0])
    s.check("a room that is one of the let ones is not closed for being full",
            not [u for u, *_r in _ics_spans(booked) if u.startswith("full-")])

    s.section("One fewer is not full")
    conn.execute("DELETE FROM bookings WHERE id = ?", (ids[-1],))
    conn.commit()
    s.check("one fewer taken: the spare room can be had", _ask(m.is_range_available, spare["id"], a, b)[0])
    s.check("and its calendar is not closed for it",
            not [u for u, *_r in _ics_spans(_ask(m.generate_room_ics, spare)) if u.startswith("full-")])

    s.section("A pending request counts, until confirming decides")
    pend = _book(conn, let_rooms[-1]["id"], a, b, status="pending", n=1)
    s.check("a pending request takes the room on the site",
            not _ask(m.is_range_available, spare["id"], a, b)[0])
    s.check("but confirming a rival is weighed on confirmed stays only",
            _ask(m.is_range_available, spare["id"], a, b, include_pending=False)[0])
    conn.execute("DELETE FROM bookings WHERE id = ?", (pend,))
    conn.commit()

    s.section("The owner's closures are not rooms let")
    done, _skipped = m.close_rooms(conn, [let_rooms[-1]["id"]], a, b, "season", "", TAG + " rest", None)
    conn.commit()
    s.check("a closure for the season, on the fifth room", bool(done))
    s.check("leaves the spare room for sale", _ask(m.is_range_available, spare["id"], a, b)[0])

    s.section("A channel's nights are rooms let, and the house's own coming back are not")
    conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, ?, ?)",
                 (let_rooms[-1]["id"], TAG + " Booking.com", "https://example.invalid/zzlet.ics"))
    src = conn.execute("SELECT id FROM ical_sources WHERE label = ?", (TAG + " Booking.com",)).fetchone()["id"]
    # The closure above, sent back exactly as it went out.
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, ?, ?)", (let_rooms[-1]["id"], src, a.isoformat(), b.isoformat()))
    conn.commit()
    s.check("the house's closure sent back is not a room let",
            _ask(m.is_range_available, spare["id"], a, b)[0])
    echo = _ask(m.channel_echo_problem)
    s.check("but the owner is told the channel is sending closures back",
            echo is not None and "Booked dates only" in echo[1], detail=str(echo))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()
    s.check("with the closure gone, the same nights are a stay on the channel, and count",
            not _ask(m.is_range_available, spare["id"], a, b)[0])
    s.check("and the echo warning has closed itself", _ask(m.channel_echo_problem) is None)
    conn.execute("UPDATE rooms SET active = 0 WHERE id = ?", (let_rooms[-1]["id"],))
    conn.commit()
    s.check("a channel's nights on a room taken off sale here are not counted",
            _ask(m.is_range_available, spare["id"], a, b)[0])
    off = _ask(m.generate_room_ics, conn.execute(
        "SELECT * FROM rooms WHERE id = ?", (let_rooms[-1]["id"],)).fetchone())
    whole = [(x, y) for u, x, y in _ics_spans(off) if u.startswith("off-sale")]
    s.check("and that room's own calendar is closed from today, whole (z28 item 2)",
            len(whole) == 1 and whole[0][0] == house_today()
            and (whole[0][1] - whole[0][0]).days >= 3 * 365 - 1, detail=str(whole))
    s.check("while a room on sale has no such span",
            not [u for u, *_r in _ics_spans(_ask(m.generate_room_ics, spare)) if u.startswith("off-sale")])
    conn.execute("UPDATE rooms SET active = 1 WHERE id = ?", (let_rooms[-1]["id"],))
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id = ?", (src,))
    conn.commit()

    s.section("One more let anyway: said the same day, and closed when it is not")
    ids[-1] = _book(conn, let_rooms[-1]["id"], a, b, n=2)
    _book(conn, spare["id"], a, b, n=3)      # past the gate, as a channel race would be
    over = _ask(m.rooms_over_the_limit)
    s.check("the nights are found", [o["night"] for o in over] == [a, a + timedelta(days=1)],
            detail=str([o["night"] for o in over]))
    with m.app.test_request_context("/"):
        home = [w for w in m.owner_home_warnings(conn, house_today())
                if "rooms are let" in w["title"]]
        m.generate_watch_tasks(conn)
    s.check("on the owner home, as a blocker", len(home) == 1 and home[0]["severity"] == "blocker",
            detail=str(home))
    tasks = [r["title"] for r in conn.execute(
        "SELECT title FROM tasks WHERE status != 'done' AND title LIKE ?", (OVER_TASK,))]
    s.check("and as a task for each night", len(tasks) == 2, detail=str(tasks))
    conn.execute("DELETE FROM bookings WHERE room_id = ? AND guest_name LIKE ?", (spare["id"], TAG + "%"))
    conn.commit()
    with m.app.test_request_context("/"):
        m.generate_watch_tasks(conn)
        home = [w for w in m.owner_home_warnings(conn, house_today()) if "rooms are let" in w["title"]]
    s.check("one stay moved: the line goes", home == [], detail=str(home))
    s.check("and the tasks close themselves", not conn.execute(
        "SELECT 1 FROM tasks WHERE status != 'done' AND title LIKE ?", (OVER_TASK,)).fetchone())

    s.section("The house's closures sent out are the ones recognised coming back")
    # own_feed_spans and generate_room_ics must describe the same closures,
    # or an echo stops being recognised and counts as a room let.
    m.close_rooms(conn, [spare["id"]], b + timedelta(days=20), b + timedelta(days=25),
                  "booked", "", TAG + " span", None)
    conn.commit()
    with m.app.test_request_context():
        spans = m.own_feed_spans(conn, m._availability_picture(conn, fresh=True))
    sent = [(x, y) for u, x, y in _ics_spans(_ask(m.generate_room_ics, spare))
            if u.split("-")[0] in ("closure", "ws", "house")]
    known = spans.get(spare["id"], set()) | spans[None]
    s.check("every closure the feed sends is one the count would recognise",
            sent and all(x in known for x in sent), detail=str([x for x in sent if x not in known]))

    s.section("Workshops are not counted (owner, 18 October)")
    # "Workshops are a separate thing: their rooms aren't the nightly rooms or
    # assigned the same way. The limit counts nightly lettings only." A
    # session with guests in six rooms raises nothing.
    now = _harness.datetime_now()
    conn.execute("INSERT INTO workshops (title, description, price_per_person, default_capacity, "
                 "active, created_at) VALUES (?, '', 400, 12, 1, ?)", (TAG + " Atelier", now))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    w_a = b + timedelta(days=40)
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, "
                 "created_at) VALUES (?, ?, ?, 12, ?)",
                 (wid, w_a.isoformat(), (w_a + timedelta(days=3)).isoformat(), now))
    sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for i, r in enumerate(rooms[:6]):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
               status, occupancy_type, assigned_room_id, reference_code, manage_token, created_at)
               VALUES (?, ?, ?, 1, 'confirmed', 'solo', ?, ?, ?, ?)""",
            (sid, f"{TAG} Maker {i}", f"zzlet.maker{i}@example.invalid", r["id"],
             f"{TAG}W{i}", f"tok-{TAG.lower()}-w-{i}", now))
    conn.commit()
    over = [o["night"] for o in _ask(m.rooms_over_the_limit)]
    s.check("the limit does not count it: nightly lettings only",
            not [n for n in over if w_a <= n < w_a + timedelta(days=3)], detail=str(over))
    plan = _ask(m.rooming_plan, sid)
    s.check("and its rooming page says nothing about five rooms",
            not [p for p in plan["problems"] if "five" in p["detail"] or "rooms, and" in p["detail"]],
            detail=str([p["detail"] for p in plan["problems"]]))
