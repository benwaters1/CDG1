# -*- coding: utf-8 -*-
"""Booking.com, watched: names, check-in, the sync both ways, answers.

The owner, 9 November 2026, took four of five suggestions for the Booking.com
side of the house. What was checked first, on the live mailbox and on
Booking.com's own help, and what it changed:

  A GUEST'S MESSAGE NAMES THEM. Booking.com's "New booking!" email carries
  the number and the day and nothing else, so every stay arrived nameless.
  But a guest's message says "We received this message from Nicolas
  Regnault" and carries "Guest name:", "Booking number:", "Total guests:".
  Those emails were kept and never read for the stay. Now a gap in the stay
  is filled from them -- never a name somebody typed, and never from mail
  that is not Booking.com's own.

  THE POLICE FICHE DOES NOT COME FROM BOOKING.COM. Its online check-in is
  for properties on a channel manager, and single-room bookings only. The
  house's own online check-in was only ever sent where it had an address,
  which the booking email does not give. So there is one address for every
  guest, /booking-com/checkin, which Booking.com's own scheduled message
  carries: the number AND the arrival day find the stay, never the number
  alone, and an old or cancelled stay is not found.

  THE SYNC, BOTH WAYS. Nothing said when the house stopped reading a room's
  Booking.com calendar, or when Booking.com stopped reading the house's --
  the second could not even be known. Each read of a room's feed is now
  noted (a staff member opening it does not count), and the owner home and
  a self-closing task say when either way is broken or late.

  A NIGHT SOLD TWICE. A Booking.com stay on nights the house had already
  taken -- its own booking in that room, a closure it made, the whole house
  held -- is a blocker until one of them moves. Not the same stay typed in
  here as well, not a channel stay typed in as a closure, and not the house's
  own closures sent back.

  ANSWERED FROM OUTLOOK. A guest answered by replying in Outlook stayed
  "waiting" until somebody ticked it here too. The Sent Items of the house's
  mailboxes are read for mail to that guest's address, after the message.

Nothing here reaches the network: Graph, the calendars and the mail are
stood in for.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, visible_text
import _harness

m = _harness.m
TAG = "ZZBCW"
NUM = "9743300001"          # named from a message
NUM2 = "9743300002"         # a name typed by hand, kept
NUM3 = "9743300003"         # a message from somebody who is not Booking.com
NUM4 = "9743300004"         # found by the check-in page
NUM5 = "9743300005"         # cancelled: not found
FEED = "https://ical.booking.com/v1/export?t=" + TAG


def _now():
    return datetime.now(timezone.utc)


def _cleanup(conn):
    conn.execute("DELETE FROM ota_mail WHERE source_id LIKE ?", (f"<{TAG}%",))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '97433%'")
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ? OR note LIKE ?", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    conn.execute("DELETE FROM ical_sync_log WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    conn.execute("DELETE FROM ical_sources WHERE url LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM ics_feed_reads WHERE user_agent LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'find_channel_checkin'")
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title IN (?, ?)",
                 (m.WATCH_TASK_ORIGIN, "Move or cancel a Booking.com stay on nights already taken",
                  "Put the Booking.com calendar sync right"))
    conn.commit()


def _mail(conn, n, subject, body="", *, from_address="noreply@booking.com",
          from_name="Booking.com", reply_to="", received=None):
    mail_id, _kind, fresh = m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}{n}@booking.com>",
        received_at=(received or _now()).isoformat(), from_address=from_address,
        from_name=from_name, reply_to=reply_to, subject=subject, text=body, notify=False)
    assert fresh
    return mail_id


def _new_booking(conn, n, number, day):
    return _mail(conn, n, f"Booking.com - New booking! ({number}, {day.strftime('%A, %d %B %Y')})")


def _message(conn, n, number, name, *, alias=None, guests=2, received=None, from_address=None):
    alias = alias or f"{TAG.lower()}{n}@guest.booking.com"
    body = (f"##- Please type your reply above this line -##\nConfirmation number: {number}\n"
            f"You have a new message from a guest\n{name} said:\nHello, is there dinner?\n"
            f"Reservation details\nGuest name:\n{name}\nBooking number:\n{number}\n"
            f"Total guests:\n{guests}\nTotal rooms:\n1\n")
    return _mail(conn, n, f"We received this message from {name}", body,
                 from_address=from_address or alias, from_name=name, reply_to=alias,
                 received=received)


def _stay(conn, number):
    return conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = ?",
                        (number,)).fetchone()


def _free_room(conn, start, nights):
    """An active room with nothing at all on it for `nights` from a day near
    `start`. Asked, not assumed: the suite may run on a copy of a live house."""
    rooms = conn.execute("SELECT id, name, export_token FROM rooms WHERE active = 1 "
                         "ORDER BY sort_order, id").fetchall()
    for offset in range(0, 600, 11):
        first = start + timedelta(days=offset)
        lo, hi = first.isoformat(), (first + timedelta(days=nights)).isoformat()
        for room in rooms:
            busy = conn.execute(
                """SELECT 1 FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
                     AND arrival_date < ? AND departure_date > ?
                   UNION ALL SELECT 1 FROM room_blocks WHERE room_id = ?
                     AND start_date < ? AND end_date > ?
                   UNION ALL SELECT 1 FROM blocked_dates WHERE room_id = ?
                     AND start_date < ? AND end_date > ?""",
                (room["id"], hi, lo) * 3).fetchone()
            held = any(h_start and h_end and h_start < first + timedelta(days=nights)
                       and first <= h_end for h_start, h_end, _w in m._availability_picture(conn, fresh=True)["house"])
            if not busy and not held:
                return room, first
    return None, None


def run():
    s = Suite("Booking.com, watched: names, check-in, the sync both ways, answers")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    saved = {n: getattr(m, n) for n in ("graph_get", "fetch_ical_ranges", "send_email")}
    m.fetch_ical_ranges = lambda url, timeout=8: ([], None)
    m.send_email = lambda *a, **kw: True
    try:
        _run(s, oc, conn)
    finally:
        for name, value in saved.items():
            setattr(m, name, value)
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, conn):
    today = m.house_today()

    s.section("A guest's message names their stay")
    day = today + timedelta(days=300)
    _new_booking(conn, 1, NUM, day)
    s.check("the booking email alone leaves the stay nameless (a precondition)",
            not (_stay(conn, NUM)["guest_name"] or "").strip())
    _message(conn, 2, NUM, "Ana Exampleton", guests=3)
    named = m.name_booking_com_stays_from_messages(conn)
    stay = _stay(conn, NUM)
    s.check("the message's name is the stay's name", stay["guest_name"] == "Ana Exampleton",
            detail=str(dict(stay)))
    s.check("and its head count the stay's", stay["guests"] == 3, detail=str(stay["guests"]))
    s.check("and the function says which stays it named", stay["id"] in named, detail=str(named))
    linked = conn.execute("SELECT ota_reservation_id FROM ota_mail WHERE source_id = ?",
                          (f"<{TAG}2@booking.com>",)).fetchone()
    s.check("the message is tied to its stay, so the stay's page lists it",
            linked and linked["ota_reservation_id"] == stay["id"])

    _new_booking(conn, 3, NUM2, day + timedelta(days=3))
    conn.execute("UPDATE ota_reservations SET guest_name = 'Typed By Hand', guests = 4 "
                 "WHERE reservation_number = ?", (NUM2,))
    conn.commit()
    _message(conn, 4, NUM2, "Somebody Else", guests=1)
    m.name_booking_com_stays_from_messages(conn)
    stay2 = _stay(conn, NUM2)
    s.check("a name somebody typed is never replaced by a message's",
            stay2["guest_name"] == "Typed By Hand" and stay2["guests"] == 4, detail=str(dict(stay2)))

    _new_booking(conn, 5, NUM3, day + timedelta(days=6))
    _message(conn, 6, NUM3, "Not Booking", from_address=f"{TAG.lower()}@example.invalid",
             alias=f"{TAG.lower()}@example.invalid")
    m.name_booking_com_stays_from_messages(conn)
    s.check("an email that is not Booking.com's own names nobody",
            not (_stay(conn, NUM3)["guest_name"] or "").strip())

    s.section("One check-in address for every Booking.com guest")
    arrive = today + timedelta(days=20)
    _new_booking(conn, 7, NUM4, arrive)
    _new_booking(conn, 8, NUM5, arrive)
    conn.execute("UPDATE ota_reservations SET status = 'cancelled' WHERE reservation_number = ?",
                 (NUM5,))
    conn.commit()
    anon = m.app.test_client()
    page = anon.get("/booking-com/checkin")
    s.check("the page asks for the number and the day",
            page.status_code == 200 and 'name="number"' in page.get_data(as_text=True)
            and 'name="arrival"' in page.get_data(as_text=True))
    s.check("and is not indexed", 'content="noindex' in page.get_data(as_text=True))
    s.check("a number in the link is filled in for them",
            f'value="{NUM4}"' in anon.get(f"/booking-com/checkin?number={NUM4}").get_data(as_text=True))
    r = anon.post("/booking-com/checkin", data={"number": NUM4, "arrival": arrive.isoformat()})
    token = conn.execute("SELECT checkin_token FROM ota_reservations WHERE reservation_number = ?",
                         (NUM4,)).fetchone()["checkin_token"]
    s.check("the number and the day take them to their own check-in",
            r.status_code == 302 and token and r.headers.get("Location", "").endswith(
                f"/checkin/stay/{token}"), detail=r.headers.get("Location"))
    s.check("which opens", anon.get(f"/checkin/stay/{token}").status_code == 200)
    r = anon.post("/booking-com/checkin", data={"number": NUM4, "arrival": ""})
    s.check("the number alone finds nothing", r.status_code == 200
            and "/checkin/stay/" not in (r.headers.get("Location") or ""))
    r = anon.post("/booking-com/checkin",
                  data={"number": NUM4, "arrival": (arrive + timedelta(days=1)).isoformat()})
    s.check("nor the number with the wrong day", r.status_code == 200
            and "could not find" in visible_text(r.get_data(as_text=True)))
    r = anon.post("/booking-com/checkin", data={"number": NUM5, "arrival": arrive.isoformat()})
    s.check("nor a cancelled stay", r.status_code == 200)
    conn.execute("DELETE FROM submission_log WHERE action = 'find_channel_checkin'")
    conn.commit()
    for _ in range(m.BOOKING_RATE_LIMIT_PER_HOUR):
        anon.post("/booking-com/checkin", data={"number": "1", "arrival": arrive.isoformat()})
    r = anon.post("/booking-com/checkin", data={"number": NUM4, "arrival": arrive.isoformat()})
    s.check("and guessing is limited per connection, even the right answer once over",
            r.status_code == 200 and "Too many attempts" in visible_text(r.get_data(as_text=True)))
    conn.execute("DELETE FROM submission_log WHERE action = 'find_channel_checkin'")
    conn.commit()
    stays_page = oc.get("/admin/channel-stays").get_data(as_text=True)
    s.check("the stays page gives the owner the message to schedule in Booking.com, with the link",
            "7 days before check-in" in stays_page and "/booking-com/checkin" in stays_page)

    s.section("A Booking.com stay on nights already taken here")
    room, first = _free_room(conn, today + timedelta(days=200), 12)
    if not s.check("a room free for twelve nights (a precondition)", bool(room)):
        return
    cur = conn.execute("INSERT INTO ical_sources (room_id, label, url, last_synced_at) "
                       "VALUES (?, 'Booking.com', ?, ?)", (room["id"], FEED, _now().isoformat()))
    source = cur.lastrowid
    a, b = first + timedelta(days=2), first + timedelta(days=5)
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, ?, ?)", (room["id"], source, a.isoformat(), b.isoformat()))
    conn.commit()
    s.check("a Booking.com stay on free nights is no clash",
            not [c for c in m.channel_clashes(conn, today) if c["room_id"] == room["id"]])
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 'x@example.invalid', ?, ?, 2, 'confirmed', 500, ?)""",
        (room["id"], f"{TAG}-OWN", f"tok{TAG}own".lower(), f"{TAG} Own Guest",
         (a + timedelta(days=1)).isoformat(), (b + timedelta(days=2)).isoformat(), _now().isoformat()))
    conn.commit()
    mine = [c for c in m.channel_clashes(conn, today) if c["room_id"] == room["id"]]
    s.check("one overlapping the house's own booking in that room is a clash, naming it",
            len(mine) == 1 and f"{TAG}-OWN" in mine[0]["why"][0], detail=str(mine))
    problem = m.channel_clash_problem(conn, today)
    s.check("the owner is told what to do about it",
            problem and "moved or cancelled" in problem[1] and room["name"] in problem[1],
            detail=str(problem))
    with m.app.test_request_context("/"):
        home = m.owner_home_warnings(conn, today)
    s.check("as a blocker on the owner home", any(
        w["severity"] == "blocker" and "already taken here" in w["title"] for w in home))
    m.generate_watch_tasks(conn, today)
    task = conn.execute("SELECT * FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                        (m.WATCH_TASK_ORIGIN, "Move or cancel a Booking.com stay on nights already taken")
                        ).fetchone()
    s.check("and as a task", bool(task))
    conn.execute("UPDATE bookings SET arrival_date = ?, departure_date = ? WHERE reference_code = ?",
                 (a.isoformat(), b.isoformat(), f"{TAG}-OWN"))
    conn.commit()
    s.check("the same stay entered here as well, same room and dates, is no clash",
            not [c for c in m.channel_clashes(conn, today) if c["room_id"] == room["id"]])
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE reference_code = ?", (f"{TAG}-OWN",))
    conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at) "
                 "VALUES (?, ?, ?, 'Booking.com stay', ?)",
                 (room["id"], a.isoformat(), b.isoformat(), _now().isoformat()))
    conn.commit()
    s.check("nor a channel stay the house typed in as a closure",
            not [c for c in m.channel_clashes(conn, today) if c["room_id"] == room["id"]])
    conn.execute("DELETE FROM room_blocks WHERE room_id = ? AND reason = 'Booking.com stay' "
                 "AND start_date = ?", (room["id"], a.isoformat()))
    conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, note, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (room["id"], (a - timedelta(days=1)).isoformat(), (a + timedelta(days=1)).isoformat(),
                  f"{TAG} Painting", f"{TAG} painting", _now().isoformat()))
    conn.commit()
    mine = [c for c in m.channel_clashes(conn, today) if c["room_id"] == room["id"]]
    s.check("but a closure the house made is", len(mine) == 1 and "closure made here" in mine[0]["why"][0],
            detail=str(mine))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, note, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (room["id"], a.isoformat(), b.isoformat(), f"{TAG} Echo", f"{TAG} echo",
                  _now().isoformat()))
    conn.commit()
    s.check("and the house's own closure coming back from Booking.com is not",
            not [c for c in m.channel_clashes(conn, today) if c["room_id"] == room["id"]])
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()
    m.generate_watch_tasks(conn, today)
    gone = conn.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone() if task else None
    s.check("the task closes itself once nothing clashes", gone and gone["status"] == "done",
            detail=str(gone and dict(gone)))

    s.section("The house reading Booking.com")
    lines = lambda: m.channel_sync_problems(conn)  # noqa: E731
    mine = [p for p in lines() if room["name"] in p[2]]
    s.check("a calendar read a minute ago says nothing", not mine, detail=str(mine))
    conn.execute("UPDATE ical_sources SET last_synced_at = ?, last_sync_error = ? WHERE id = ?",
                 (_now().isoformat(), "HTTP Error 404: Not Found", source))
    conn.commit()
    mine = [p for p in lines() if room["name"] in p[2] and "cannot be read" in p[1]]
    s.check("one that cannot be read is a blocker, with the reason",
            mine and mine[0][0] == "blocker" and "404" in mine[0][2], detail=str(lines()))
    conn.execute("UPDATE ical_sources SET last_synced_at = ?, last_sync_error = NULL WHERE id = ?",
                 ((_now() - timedelta(hours=5)).isoformat(), source))
    conn.commit()
    mine = [p for p in lines() if room["name"] in p[2] and "not been read lately" in p[1]]
    s.check("one not read for five hours is late", mine and mine[0][0] == "warn", detail=str(lines()))
    conn.execute("UPDATE ical_sources SET last_synced_at = ? WHERE id = ?", (_now().isoformat(), source))
    conn.commit()
    saved_flag = conn.execute("SELECT value FROM app_settings WHERE key = 'automation_ical_sync_enabled'"
                              ).fetchone()
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('automation_ical_sync_enabled', '0')")
    conn.commit()
    off = [p for p in lines() if p[1] == "The Booking.com calendars are not being read"]
    s.check("the hourly sync switched off is a blocker", off and off[0][0] == "blocker")
    if saved_flag:
        conn.execute("UPDATE app_settings SET value = ? WHERE key = 'automation_ical_sync_enabled'",
                     (saved_flag["value"],))
    else:
        conn.execute("DELETE FROM app_settings WHERE key = 'automation_ical_sync_enabled'")
    conn.commit()

    s.section("Booking.com reading the house")
    conn.execute("DELETE FROM ics_feed_reads WHERE room_id = ?", (room["id"],))
    conn.commit()
    has_reads = conn.execute("SELECT MIN(first_read_at) FROM ics_feed_reads").fetchone()[0]
    if not has_reads:
        mine = [p for p in lines() if "not reading" in p[1] and room["name"] in p[2]]
        s.check("before any read has been noted at all, silence means nothing yet", not mine)
    r = m.app.test_client().get(f"/ics/{room['export_token']}.ics",
                                headers={"User-Agent": f"Booking.com iCal {TAG}"})
    row = conn.execute("SELECT * FROM ics_feed_reads WHERE room_id = ?", (room["id"],)).fetchone()
    s.check("a read of the feed from outside is noted, and who it was",
            r.status_code == 200 and row and row["reader"] == "booking.com" and row["reads"] == 1,
            detail=str(row and dict(row)))
    oc.get(f"/ics/{room['export_token']}.ics", headers={"User-Agent": f"Mozilla {TAG}"})
    rows = conn.execute("SELECT * FROM ics_feed_reads WHERE room_id = ?", (room["id"],)).fetchall()
    s.check("somebody signed in opening it does not count", len(rows) == 1 and rows[0]["reads"] == 1,
            detail=str([dict(x) for x in rows]))
    s.check("a room read just now says nothing",
            not [p for p in lines() if "not reading" in p[1] and room["name"] in p[2]])
    old = (_now() - timedelta(days=2)).isoformat()
    conn.execute("UPDATE ics_feed_reads SET first_read_at = ?, last_read_at = ? WHERE room_id = ?",
                 (old, old, room["id"]))
    conn.commit()
    mine = [p for p in lines() if "not reading" in p[1] and room["name"] in p[2]]
    s.check("one Booking.com has not read for two days is said, with when",
            mine and mine[0][0] == "warn" and "last read" in mine[0][2], detail=str(lines()))
    rooms_page = visible_text(oc.get("/admin/rooms").get_data(as_text=True))
    s.check("and the Rooms page says who last read each room's calendar",
            "Last read by Booking.com" in rooms_page)
    m.generate_watch_tasks(conn, today)
    s.check("a task says to put the sync right", conn.execute(
        "SELECT 1 FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
        (m.WATCH_TASK_ORIGIN, "Put the Booking.com calendar sync right")).fetchone() is not None)

    s.section("A guest answered by email from Outlook")
    asked = _now() - timedelta(hours=3)
    alias = f"{TAG.lower()}9@guest.booking.com"
    mid = _message(conn, 9, NUM, "Ana Exampleton", alias=alias, received=asked)
    other = _message(conn, 10, NUM2, "Typed By Hand", alias=f"{TAG.lower()}10@guest.booking.com",
                     received=asked)
    calls = []

    def sent_items(path, token, params=None, headers=None):
        calls.append(path)
        if "sentitems" not in path:
            return {"value": []}
        return {"value": [
            {"sentDateTime": (asked - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "toRecipients": [{"emailAddress": {"address": f"{TAG.lower()}10@guest.booking.com"}}]},
            {"sentDateTime": (asked + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "toRecipients": [{"emailAddress": {"address": alias.upper()}}]},
        ]}

    m.graph_get = sent_items
    # The Booking.com mailbox the house reads, as on the live site; this
    # copy may have none set, and with no mailbox there is nothing to read.
    saved_box = conn.execute("SELECT value FROM app_settings WHERE key = 'booking_com_mailbox'"
                             ).fetchone()
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) "
                 "VALUES ('booking_com_mailbox', 'bookingcom@example.invalid')")
    conn.commit()
    closed = m.booking_com_answered_by_email(conn, "stand-in token")
    s.check("the Booking.com mailbox's sent mail is what was read",
            any("/users/bookingcom@example.invalid/mailFolders/sentitems" in c for c in calls),
            detail=str(calls))
    row = conn.execute("SELECT handled_at, answered_from FROM ota_mail WHERE id = ?", (mid,)).fetchone()
    s.check("a reply sent to the guest's address after they wrote takes them off the list",
            mid in closed and row["handled_at"] and row["answered_from"], detail=str(dict(row)))
    s.check("one sent before they wrote does not",
            other not in closed and not conn.execute(
                "SELECT handled_at FROM ota_mail WHERE id = ?", (other,)).fetchone()["handled_at"])
    s.check("and the page says where it was answered from",
            "Answered by email from" in visible_text(oc.get("/management/booking-com").get_data(as_text=True)))
    conn.execute("UPDATE ota_mail SET handled_at = ? WHERE id = ?", (_now().isoformat(), other))
    conn.commit()
    calls.clear()
    m.booking_com_answered_by_email(conn, "stand-in token")
    waiting = [r["id"] for r in m.booking_com_waiting(conn)]
    if not waiting:
        s.check("with nobody waiting, Microsoft is not asked at all", not calls, detail=str(calls))
    if saved_box:
        conn.execute("UPDATE app_settings SET value = ? WHERE key = 'booking_com_mailbox'",
                     (saved_box["value"],))
    else:
        conn.execute("DELETE FROM app_settings WHERE key = 'booking_com_mailbox'")
    conn.commit()
