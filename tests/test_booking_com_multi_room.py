# -*- coding: utf-8 -*-
"""One Booking.com reservation, several rooms.

On 9 November 2026 the owner found that reservation 6348479172 (Albert
Chiodi, 4 adults, 21-28 December 2026) is ONE reservation for TWO rooms: the
King Suite and the Twin Room (Ground Floor). The app held one room a
reservation. It said "Not matched ... Choose the room by hand", showed both
rooms' nights as nameless stays "only on the calendar", and its hand form
could give the reservation one room: typing the second under the same number
put it in place of the first.

WHAT IS HELD HERE

  NOT GUESSED, BUT ONE STEP. Two rooms' calendars starting the same day with
  one reservation waiting is still not guessed at. The owner ticks "these are
  all this reservation" once, and both rooms are its rooms, each with its own
  calendar's nights.

  SAID IN THE EMAIL, PAIRED BY ITSELF. Where Booking.com's email says how
  many rooms ("2 rooms", "Rooms: 2") or names them, that is used -- and still
  not where another stay is waiting for a room the same day.

  EVERY ROOM, EVERYWHERE A ROOM IS ASKED ABOUT: the nightly count of rooms
  let, the room board, the desk, the calendar, the turnover checklist. AND
  ONE GUEST WHERE A GUEST IS: who is here, breakfast, the police fiche, the
  door link -- the party counted once, not once a room.

  THE HAND FORM ADDS, NEVER REPLACES. The same number with another room is a
  second room; with a room it has, new dates for that room. A room given by
  mistake comes off on its own, leaving the others.

  ONE RESERVATION MOVES AS ONE. A cancellation frees every room, a change of
  dates moves every room, and the guest's name is the same in each.

Nothing here reaches the network: the calendars and the mail are stood in for.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZBMR"
NUM = "9742200001"          # two blocks, nothing said: the owner groups it
NUM2 = "9742200002"         # the email says two rooms: paired by itself
NUM3 = "9742200003"         # says two rooms, but another stay waits that day
NUM4 = "9742200004"         # the one waiting beside it
NUM5 = "9742200005"         # typed by hand, room by room, leaving today
NUM6 = "9742200006"         # typed by hand, room by room, arriving today
FEED = "https://ical.booking.com/v1/export?t=" + TAG


def _now():
    return datetime.now(timezone.utc)


def _cleanup(conn):
    conn.execute("DELETE FROM ota_mail WHERE source_id LIKE ?", (f"<{TAG}%",))
    ours = "(SELECT id FROM ota_reservations WHERE reservation_number LIKE '97422%')"
    conn.execute(f"DELETE FROM tasks WHERE ota_reservation_id IN {ours}")
    conn.execute(f"DELETE FROM ota_reservation_rooms WHERE ota_reservation_id IN {ours}")
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '97422%'")
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    conn.execute("DELETE FROM ical_sync_log WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    conn.execute("DELETE FROM ical_sources WHERE url LIKE ?", (f"%{TAG}%",))
    conn.commit()


def _free(conn, start, days, want=2, offsets=(0,)):
    """`want` active rooms with nothing on them for `days` nights from a day
    `start` + one of `offsets`. Asked, not assumed: the suite runs on a copy
    of a live database."""
    rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1 "
                         "ORDER BY sort_order, name, id").fetchall()
    for offset in offsets:
        first = start + timedelta(days=offset)
        lo, hi = first.isoformat(), (first + timedelta(days=days)).isoformat()
        free = []
        for room in rooms:
            busy = conn.execute(
                """SELECT 1 FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
                     AND arrival_date < ? AND departure_date > ?
                   UNION ALL SELECT 1 FROM room_blocks WHERE room_id = ?
                     AND start_date < ? AND end_date > ?
                   UNION ALL SELECT 1 FROM blocked_dates WHERE room_id = ?
                     AND start_date < ? AND end_date > ?
                   UNION ALL SELECT 1 FROM ota_reservations WHERE room_id = ?
                     AND arrival_date < ? AND COALESCE(departure_date, arrival_date) >= ?""",
                (room["id"], hi, lo) * 4).fetchone()
            if not busy:
                free.append(room)
        if len(free) >= want:
            return free[:want], first
    return None, None


class _StandIns:
    """The calendars and the mail, stood in for; the network refuses underneath."""

    NAMES = ("fetch_ical_ranges", "send_email", "urlopen")

    def __init__(self, feeds, sent):
        self.feeds, self.sent = feeds, sent

    def __enter__(self):
        self.saved = {n: getattr(m, n) for n in self.NAMES}
        m.urlopen = _harness._refuse("the network", "a stand-in missed a path")
        m.fetch_ical_ranges = lambda url, timeout=8: (list(self.feeds.get(url, [])), None)

        def send(to, subject, body, *a, **kw):
            self.sent.append({"to": to, "subject": subject, "body": body})
            return True

        m.send_email = send
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(m, name, value)
        return False


def _subject(kind, number, day):
    return f"Booking.com - {kind}! ({number}, {day.strftime('%A, %d %B %Y')})"


def _mail(conn, n, subject, body_html="", received=None):
    mail_id, _kind, fresh = m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}{n}@booking.com>",
        received_at=(received or _now()).isoformat(), from_address="noreply@booking.com",
        from_name="Booking.com", subject=subject, html=body_html)
    assert fresh
    return mail_id


def _stay(conn, number):
    return conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = ?",
                        (number,)).fetchone()


def _held(conn, number):
    return m.ota_room_ids(conn, _stay(conn, number)["id"])


def _rows(conn, number):
    return [st for st in m.channel_stays(conn) if st["reference_code"] == number]


def run():
    s = Suite("One Booking.com reservation, several rooms")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    try:
        _run(s, oc, ec, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, ec, conn):
    s.section("What an email says about rooms")
    rooms = [(601, "The Twin Room", "Twin Room"),
             (602, "The Twin Room (Ground Floor)", None),
             (603, "The King Suite", "King Room with Mountain View")]
    day = m.house_today() + timedelta(days=400)
    subj = _subject("New booking", NUM, day)
    got = m.parse_booking_com_email(subj, "noreply@booking.com", "Booking.com", "",
                                    "Rooms: 2\nTotal price: EUR 5,530", [], rooms)
    s.check("'Rooms: 2' is a reservation for two rooms",
            got["room_count"] == 2, detail=str(got))
    got = m.parse_booking_com_email(subj, "noreply@booking.com", "Booking.com", "",
                                    "You have a new booking for 2 rooms.", [], rooms)
    s.check("and so is '2 rooms'", got["room_count"] == 2, detail=str(got))
    got = m.parse_booking_com_email(
        subj, "noreply@booking.com", "Booking.com", "",
        "King Room with Mountain View - 2 adults\nThe Twin Room (Ground Floor) - 2 adults", [], rooms)
    s.check("two rooms named are both its rooms, in the order written",
            got["room_ids"] == [603, 602] and got["room_id"] == 603
            and got["room_count"] == 2, detail=str(got))
    got = m.parse_booking_com_email(subj, "noreply@booking.com", "Booking.com", "",
                                    "Room: The Twin Room (Ground Floor)", [], rooms)
    s.check("one room named is one room: 'The Twin Room' is not found again inside "
            "'The Twin Room (Ground Floor)'",
            got["room_ids"] == [602] and got["room_id"] == 602 and not got["room_count"],
            detail=str(got))
    got = m.parse_booking_com_email(subj, "noreply@booking.com", "Booking.com", "", "", [], rooms)
    s.check("the subject alone says nothing about rooms",
            got["room_ids"] == [] and got["room_count"] is None, detail=str(got))

    pair, base = _free(conn, m.house_today(), 60, offsets=range(200, 900, 15))
    if not s.check("two rooms are free for sixty nights (a precondition)", bool(pair)):
        return
    one, two = pair
    sources = {}
    for room in (one, two):
        conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, 'Booking.com', ?)",
                     (room["id"], f"{FEED}-{room['id']}"))
        sources[room["id"]] = f"{FEED}-{room['id']}"
    conn.commit()
    sent = []

    # ------------------------------------------------------------------
    s.section("Two rooms start the same day, the email says nothing: one step")
    a = base + timedelta(days=3)
    feeds = {sources[one["id"]]: [(a, a + timedelta(days=7))],
             sources[two["id"]]: [(a, a + timedelta(days=8))]}
    with _StandIns(feeds, sent):
        mail_id = _mail(conn, 1, _subject("New booking", NUM, a))
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    stay = _stay(conn, NUM)
    s.check("not guessed: no room", stay["room_id"] is None, detail=str(dict(stay)))
    s.check("the note offers to say they are one reservation",
            "all this one reservation" in (stay["match_note"] or ""), detail=stay["match_note"])
    html = oc.get("/admin/channel-stays?q=" + NUM).get_data(as_text=True)
    text = visible_text(html)
    s.check("the page offers both rooms in one form",
            "These are this reservation" in text
            and f'action="/admin/channel-stays/{stay["id"]}/rooms"' in html, detail=text[:600])
    only = [st for st in m.channel_stays(conn) if st["from_calendar"]
            and st["arrival_date"] == a.isoformat() and st["room_id"] in (one["id"], two["id"])]
    s.check("until then both rooms' nights are 'only on the calendar'", len(only) == 2)

    r = oc.post(f"/admin/channel-stays/{stay['id']}/rooms",
                data={"room_id": [str(one["id"]), str(two["id"])]}, follow_redirects=True)
    stay = _stay(conn, NUM)
    s.check("one click: both rooms are its rooms",
            sorted(_held(conn, NUM)) == sorted([one["id"], two["id"]])
            and stay["matched_by"] == "hand" and stay["match_note"] is None,
            response=r, detail=str(dict(stay)))
    nights = {st["room_id"]: st["departure_date"] for st in _rows(conn, NUM)}
    s.check("each with the nights its own calendar shows",
            nights.get(one["id"]) == (a + timedelta(days=7)).isoformat()
            and nights.get(two["id"]) == (a + timedelta(days=8)).isoformat(), detail=str(nights))
    s.check("and the page says so, naming both", any(
        one["name"] in f and two["name"] in f for f in flashes(r)), detail=str(flashes(r)))
    only = [st for st in m.channel_stays(conn) if st["from_calendar"]
            and st["arrival_date"] == a.isoformat() and st["room_id"] in (one["id"], two["id"])]
    s.check("neither room is 'only on the calendar' now", only == [], detail=str(only))
    rows = _rows(conn, NUM)
    s.check("channel_stays gives a row for each room",
            sorted(st["room_id"] for st in rows) == sorted([one["id"], two["id"]]), detail=str(rows))
    s.check("with the second marked as a further room of the same reservation",
            [st["extra_room"] for st in rows].count(True) == 1
            and len({st["ota_reservation_id"] for st in rows}) == 1)
    s.check("and both rooms in words on each",
            all(one["name"] in st["rooms_label"] and two["name"] in st["rooms_label"]
                for st in rows))
    with _StandIns(feeds, sent):
        m.pair_booking_com_stays(conn)
    s.check("pairing leaves the owner's word alone",
            sorted(_held(conn, NUM)) == sorted([one["id"], two["id"]]))
    picture = m._availability_picture(conn)
    let = m.rooms_let_on(picture, a + timedelta(days=1))
    s.check("both rooms are let that night, for the five-a-night count",
            {one["id"], two["id"]} <= let, detail=str(let))
    page = visible_text(oc.get(f"/admin/channel-stays/{stay['id']}").get_data(as_text=True))
    s.check("the stay's own page lists both rooms, each to take off on its own",
            page.count("Take this room off") == 2, detail=page[:800])

    s.section("The guest's name, a change of dates, a cancellation: every room")
    with _StandIns(feeds, sent):
        _mail(conn, 2, _subject("Modified booking", NUM, a + timedelta(days=1)),
              f"<p>Guest name: Albert {TAG} Chiodi</p>")
    rows = _rows(conn, NUM)
    s.check("a change of dates moves every room, one with nights of its own too",
            len(rows) == 2 and all(st["arrival_date"] == (a + timedelta(days=1)).isoformat()
                                   and st["departure_date"] == _stay(conn, NUM)["departure_date"]
                                   for st in rows), detail=str(rows))
    s.check("and the guest's name is the name in each room",
            all(st["guest_name"] == f"Albert {TAG} Chiodi" for st in rows), detail=str(rows))
    a1 = a + timedelta(days=1)
    for room in (one, two):         # and Booking.com's calendars move with it
        feeds[sources[room["id"]]] = [(a1, a + timedelta(days=7))]
    with _StandIns(feeds, sent):
        m.sync_booking_com_calendars(conn)
    s.check("the owner's rooms stay its rooms through the change",
            sorted(_held(conn, NUM)) == sorted([one["id"], two["id"]]))
    with _StandIns(feeds, sent):
        _mail(conn, 3, _subject("Cancelled booking", NUM, a + timedelta(days=1)))
    s.check("a cancellation takes every room off the lists", _rows(conn, NUM) == [])
    picture = m._availability_picture(conn)
    let = m.rooms_let_on(picture, a + timedelta(days=2))
    s.check("and frees both rooms at once, their blocks still on the calendars",
            not ({one["id"], two["id"]} & let), detail=str(let))

    # ------------------------------------------------------------------
    s.section("The email says two rooms: paired by itself")
    b = base + timedelta(days=20)
    feeds[sources[one["id"]]].append((b, b + timedelta(days=3)))
    feeds[sources[two["id"]]].append((b, b + timedelta(days=4)))
    with _StandIns(feeds, sent):
        mail_id = _mail(conn, 4, _subject("New booking", NUM2, b),
                        "<p>You have a new booking for 2 rooms.</p>")
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    stay = _stay(conn, NUM2)
    s.check("both rooms, from their calendars",
            stay["room_count"] == 2 and stay["matched_by"] == "ical"
            and sorted(_held(conn, NUM2)) == sorted([one["id"], two["id"]]),
            detail=str(dict(stay)))
    rows = {st["room_id"]: st for st in _rows(conn, NUM2)}
    s.check("each room leaving on the day its own calendar says",
            rows.get(one["id"], {}).get("departure_date") == (b + timedelta(days=3)).isoformat()
            and rows.get(two["id"], {}).get("departure_date") == (b + timedelta(days=4)).isoformat(),
            detail=str(rows))
    told = [x for x in sent if NUM2 in x["subject"]]
    s.check("and the house is told of both rooms in one letter",
            len(told) == 1 and one["name"] in told[0]["body"] and two["name"] in told[0]["body"],
            detail=str(told))

    s.section("Two rooms said, but another stay waits that day: still not guessed")
    c = base + timedelta(days=30)
    feeds[sources[one["id"]]].append((c, c + timedelta(days=2)))
    feeds[sources[two["id"]]].append((c, c + timedelta(days=2)))
    with _StandIns(feeds, sent):
        _mail(conn, 5, _subject("New booking", NUM4, c))
        mail_id = _mail(conn, 6, _subject("New booking", NUM3, c), "<p>Rooms: 2</p>")
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    s.check("the two-room reservation is not given both rooms",
            _stay(conn, NUM3)["room_id"] is None and _held(conn, NUM3) == [],
            detail=str(dict(_stay(conn, NUM3))))
    s.check("nor is the other given either", _stay(conn, NUM4)["room_id"] is None)

    # ------------------------------------------------------------------
    s.section("By hand: the same number adds a room, never replaces one")
    pair, start = _free(conn, m.house_today() - timedelta(days=4), 9)
    if not s.check("two rooms are free around today (a precondition)", bool(pair)):
        return
    p, q = pair
    today = m.house_today()
    out_day, in_day = today - timedelta(days=3), today + timedelta(days=3)

    def add(number, room, arrive, leave, **more):
        return oc.post("/admin/channel-stays/add", data=dict(
            reservation_number=number, room_id=room["id"], arrival_date=arrive.isoformat(),
            departure_date=leave.isoformat(), **more), follow_redirects=True)

    add(NUM5, p, out_day, today, guest_name=f"Leo {TAG} Out", guests="3")
    r = add(NUM5, q, out_day, today)
    s.check("a second room under the same number is added",
            _held(conn, NUM5) == [p["id"], q["id"]], response=r, detail=str(flashes(r)))
    s.check("and the page says it now holds both",
            any("given another room" in f and q["name"] in f for f in flashes(r)),
            detail=str(flashes(r)))
    r = add(NUM5, q, out_day, today)
    s.check("the same room again changes nothing about which rooms",
            _held(conn, NUM5) == [p["id"], q["id"]], response=r)
    add(NUM6, p, today, in_day, guest_name=f"Ana {TAG} In", guests="4")
    add(NUM6, q, today, in_day)
    s.check("and the arriving one holds both too", _held(conn, NUM6) == [p["id"], q["id"]])

    s.section("Every room where a room is asked about")
    day_desk = m.front_desk_day(conn, today)
    states = {x["room"]["id"]: x["state"] for x in day_desk["rooms"]}
    s.check("the desk: both rooms are a turnaround",
            states.get(p["id"]) == "Turnaround" and states.get(q["id"]) == "Turnaround",
            detail=str(states))
    with m.app.test_request_context("/"):
        made = m.prep_channel_departures(conn, today)
    titles = [t["title"] for t in conn.execute(
        "SELECT title FROM tasks WHERE ota_reservation_id = ?", (_stay(conn, NUM5)["id"],))]
    s.check("the turnover checklist is made for each room",
            made == 2 and any(t.startswith(p["name"] + ": ") for t in titles)
            and any(t.startswith(q["name"] + ": ") for t in titles), detail=str(titles))
    board = {x["room"]["id"]: x for x in m.room_board(conn, today)}
    s.check("the room board finds each room's own checklist",
            all(board[r]["open_work"] and all(t["title"].startswith(name + ": ")
                                              for t in board[r]["open_work"])
                for r, name in ((p["id"], p["name"]), (q["id"], q["name"]))
                if r in board), detail=str({k: v["state"] for k, v in board.items()}))
    let = m.rooms_let_on(m._availability_picture(conn), today)
    s.check("both rooms are let tonight", {p["id"], q["id"]} <= let, detail=str(let))
    with m.app.test_request_context("/"):
        rows = m.build_overview(conn, "week", today)["rows"]
    arrive = [x for x in rows if x.get("facts", {}).get("ref") == NUM6 and x["date"] == today.isoformat()
              and "arrives" in x["title"]]
    s.check("the calendar has an arrival in each room, each its own row",
            sorted(x["facts"]["room_id"] for x in arrive) == sorted([p["id"], q["id"]])
            and len({x["id"] for x in arrive}) == 2, detail=str([x["id"] for x in arrive]))

    s.section("One guest where a guest is")
    guests = [x for x in m.stays_with_status(conn, today) if x["reference_code"] == NUM6]
    s.check("who is here: one line, both rooms named",
            len(guests) == 1 and p["name"] in guests[0]["room_name"]
            and q["name"] in guests[0]["room_name"], detail=str(guests))
    fiches = [x for x in m.stays_missing_fiches(conn, today)
              if x["booking"].get("reference_code") == NUM6]
    s.check("one police fiche to make, for four, not one a room",
            len(fiches) == 1 and fiches[0]["party"] == 4, detail=str(fiches))
    with m.app.test_request_context("/"):
        doors = [x for x in m.door_stays_ahead(conn, today=today) if x["reference"] == NUM6]
    s.check("one door link for the reservation", len(doors) == 1, detail=str(doors))
    with m.app.test_request_context("/"):
        sheet = m.kitchen_sheet(conn, today)
    room = next((x for x in sheet["sections"] if x["kind"] == "room"), {"covers": 0})
    with m.app.test_request_context("/"):
        conn.execute("UPDATE ota_reservations SET status = 'cancelled' WHERE reservation_number = ?",
                     (NUM6,))
        without = next((x for x in m.kitchen_sheet(conn, today)["sections"]
                        if x["kind"] == "room"), {"covers": 0})
        conn.execute("UPDATE ota_reservations SET status = 'confirmed' WHERE reservation_number = ?",
                     (NUM6,))
        conn.commit()
    s.check("breakfast counts the party of four once, and no room of it as 'not known'",
            room["covers"] - without["covers"] == 4
            and room.get("unknown_party", 0) == without.get("unknown_party", 0),
            detail=f"{room['covers']} with, {without['covers']} without")
    with m.app.test_request_context("/"):
        arrivals = m.arrivals_sheet(conn, today)
    mine = [x for x in arrivals["channel_arriving"] if x["reference_code"] == NUM6]
    s.check("the arrivals sheet: one guest, both rooms",
            len(mine) == 1 and p["name"] in mine[0]["room_name"]
            and q["name"] in mine[0]["room_name"], detail=str(mine))

    s.section("A room given by mistake comes off on its own")
    sid = _stay(conn, NUM6)["id"]
    r = oc.post(f"/admin/channel-stays/{sid}/rooms/{q['id']}/remove", follow_redirects=True)
    s.check("the second room comes off, the first stays",
            _held(conn, NUM6) == [p["id"]], response=r, detail=str(flashes(r)))
    add(NUM6, q, today, in_day)
    r = oc.post(f"/admin/channel-stays/{sid}/rooms/{p['id']}/remove", follow_redirects=True)
    s.check("taking off the first moves the next one up", _held(conn, NUM6) == [q["id"]],
            response=r, detail=str(flashes(r)))
    s.check("and the reservation keeps its nights",
            _stay(conn, NUM6)["arrival_date"] == today.isoformat()
            and _stay(conn, NUM6)["departure_date"] == in_day.isoformat())
    r = ec.post(f"/admin/channel-stays/{sid}/rooms/{q['id']}/remove")
    s.check("an employee cannot take a room off", r.status_code in (302, 403)
            and _held(conn, NUM6) == [q["id"]])
    r = ec.post(f"/admin/channel-stays/{sid}/rooms", data={"room_id": str(p["id"])})
    s.check("nor group rooms", r.status_code in (302, 403) and _held(conn, NUM6) == [q["id"]])
