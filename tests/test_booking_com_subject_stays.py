"""Booking.com's booking emails become stays, and every Booking.com stay is drawn.

Handover z27, items 23, 22 and 19. In October 2026 Booking.com's "New booking!"
email stopped saying anything in its body. All it carries is its subject:

    Booking.com - New booking! (5979778151, Thursday, 29 October 2026)

That gives no guest name, no departure and no room. So reservation 5979778151
was filed as "not matched" and "been and gone" before it had begun. It was
on no calendar, no arrivals sheet and no Today, although the room's Booking.com
calendar had blocked the night and the guest was coming.

WHAT IS HELD HERE

  THE SUBJECT'S DAY IS THE ARRIVAL, for new, changed and cancelled emails
  alike. A date in the body that is not a stay (the day it was booked) does
  not move it.

  THE CALENDAR GIVES THE ROOM AND THE NIGHTS. The room's Booking.com block
  that starts that day is the stay. If two rooms have one starting that day,
  it is left unmatched and the page names both rooms. A guess would send
  the house to make the wrong bed.

  SYNC NOW. A booking email reads the Booking.com calendars at once: the
  room's own when the email names it, every room's when it does not. It does
  not wait for the hourly sync.

  ONE LETTER TO RESERVATIONS@ per email, and only once the stay is matched:
  "Booked on Booking.com – The Twin Room, 29–30 Oct, reservation 5979778151".
  A change and a cancellation are told too. A stay that has moved is paired
  with its new block. A cancelled one is off every list.

  NO NAME IS SAID AS SUCH. "Guest name: in the Booking.com extranet", with the
  email's link to it, and the stay is "Still to come" while it is.

  EVERY BOOKING.COM BLOCK IS A STAY. A night only the calendar knows about
  (booked before the mailbox was read) is on the calendar, the arrivals and
  the morning note, labelled "Booking.com – The Twin Room – 29–30 Oct" with
  a "B." on the chip. A block lying over the house's own closure is not a
  second guest.

  BY HAND. The owner can add a stay (number, room, dates, name), which fills
  in one that could not be matched. A stay typed by mistake can be taken
  back. A stay Booking.com has written about cannot be.

  AN ATELIER'S FIRST DAY IS ONE CHIP in a week or a month, "Title – 4
  guests arrive", where it used to be one chip per place, which read as
  duplicates.

Nothing here reaches the network. fetch_ical_ranges and send_email are stood
in for, and urlopen raises underneath them.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZBSS"
NUM = "9753100001"          # matched to one room
NUM2 = "9753100002"         # two rooms start the same day
NUM3 = "9753100003"         # names its room in the body
NUM4 = "9753100004"         # typed in by hand
NUM5 = "9753100005"         # read before the subject's day counted
FEED = "https://ical.booking.com/v1/export?t=" + TAG


def _now():
    return datetime.now(timezone.utc)


def _cleanup(conn):
    conn.execute("DELETE FROM ota_mail WHERE source_id LIKE ?", (f"<{TAG}%",))
    ours = "(SELECT id FROM ota_reservations WHERE reservation_number LIKE '97531%')"
    conn.execute(f"UPDATE tasks SET ota_reservation_id = NULL WHERE ota_reservation_id IN {ours}")
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '97531%'")
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    conn.execute("DELETE FROM ical_sync_log WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    conn.execute("DELETE FROM ical_sources WHERE url LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM room_blocks WHERE reason = ?", (TAG,))
    conn.commit()


def _window(conn):
    """Two active rooms with nothing on them for 60 nights, and the first day.

    Asked, not assumed: the suite runs on a copy of a live database, where
    any room may have a stay, a closure or a channel's block on any night.
    """
    rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY id").fetchall()
    today = m.house_today()
    for offset in range(200, 900, 15):
        start = today + timedelta(days=offset)
        lo, hi = start.isoformat(), (start + timedelta(days=60)).isoformat()
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
        if len(free) >= 2:
            return free[0], free[1], start
    raise RuntimeError("no two rooms free for sixty nights in the next two years")


class _StandIns:
    """The calendars and the mail, stood in for; the network refuses underneath."""

    NAMES = ("fetch_ical_ranges", "send_email", "urlopen")

    def __init__(self, feeds, fetched, sent):
        self.feeds, self.fetched, self.sent = feeds, fetched, sent

    def __enter__(self):
        self.saved = {n: getattr(m, n) for n in self.NAMES}
        m.urlopen = _harness._refuse("the network", "a stand-in missed a path")

        def fetch(url, timeout=8):
            self.fetched.append(url)
            return list(self.feeds.get(url, [])), None

        def send(to, subject, body, *a, **kw):
            self.sent.append({"to": to, "subject": subject, "body": body})
            return True

        m.fetch_ical_ranges, m.send_email = fetch, send
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(m, name, value)
        return False


def _subject(kind, number, day):
    return f"Booking.com - {kind}! ({number}, {day.strftime('%A, %d %B %Y')})"


def _mail(conn, n, subject, body_html="", received=None):
    """One Booking.com email read as the mailbox job reads it. Its row id."""
    mail_id, _kind, fresh = m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}{n}@booking.com>",
        received_at=(received or _now()).isoformat(), from_address="noreply@booking.com",
        from_name="Booking.com", subject=subject, html=body_html)
    assert fresh
    return mail_id


def _stay(conn, number):
    return conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = ?",
                        (number,)).fetchone()


def _letters(sent):
    return [x for x in sent if "on Booking.com" in x["subject"]]


def run():
    s = Suite("Booking.com's booking emails become stays, and every one is drawn")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    try:
        _run(s, oc, ec, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, ec, conn):
    one, two, base = _window(conn)
    # The parser and its subject.
    s.section("The subject says the number and the day they arrive")
    day = base + timedelta(days=3)
    got = m.parse_booking_com_email(_subject("New booking", NUM, day), "noreply@booking.com",
                                    "Booking.com", "", "", [])
    s.check("a new booking, with its number", got["kind"] == "reservation_new"
            and got["reservation_number"] == NUM, detail=str(got))
    s.check("arriving on the subject's day", got["arrival_date"] == day.isoformat(),
            detail=str(got["arrival_date"]))
    s.check("and nothing made up about when they leave", got["departure_date"] is None)
    for kind, want in (("Cancelled booking", "reservation_cancelled"),
                       ("Modified booking", "reservation_changed")):
        got = m.parse_booking_com_email(_subject(kind, NUM, day), "noreply@booking.com",
                                        "", "", "", [])
        s.check(f"'{kind}' is read the same way", got["kind"] == want
                and got["arrival_date"] == day.isoformat(), detail=str(got))
    booked = (m.house_today() - timedelta(days=2)).strftime("%d %B %Y")
    got = m.parse_booking_com_email(_subject("New booking", NUM, day), "noreply@booking.com",
                                    "", "", f"Booked on {booked}.", [])
    s.check("a date in the body that is not the stay does not move the arrival",
            got["arrival_date"] == day.isoformat() and got["departure_date"] is None,
            detail=str(got))
    links = [{"text": "x", "href": "https://booking.com.evil.example/manage?res_id=" + NUM},
             {"text": "Help", "href": "https://partner.booking.com/en-gb/help"},
             {"text": "View", "href": "https://admin.booking.com/hotel/manage/booking.html?res_id=" + NUM}]
    s.check("the extranet link is Booking.com's own, to this reservation",
            m.booking_com_extranet_link(links, NUM) == links[2]["href"])
    s.check("and a link that only pretends to be Booking.com's is not",
            m.booking_com_extranet_link(links[:1], NUM) is None)

    # ------------------------------------------------------------------
    s.section("Paired with the room's Booking.com calendar: one room, one block")
    sources = {}
    for room in (one, two):
        cur = conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, 'Booking.com', ?)",
                           (room["id"], f"{FEED}-{room['id']}"))
        sources[room["id"]] = f"{FEED}-{room['id']}"
    conn.commit()
    a = base + timedelta(days=3)
    feeds = {sources[one["id"]]: [(a, a + timedelta(days=1))], sources[two["id"]]: []}
    fetched, sent = [], []
    span = m.stay_span_short(a.isoformat(), (a + timedelta(days=1)).isoformat())
    extranet = "https://admin.booking.com/hotel/hoteladmin/extranet_ng/manage/booking.html?res_id=" + NUM
    with _StandIns(feeds, fetched, sent):
        mail_id = _mail(conn, 1, _subject("New booking", NUM, a),
                        f'<p>You have a new booking.</p><a href="{extranet}">View booking</a>')
        before = _stay(conn, NUM)
        s.check("before the calendars are read, the stay has its day and no room",
                before["arrival_date"] == a.isoformat() and before["room_id"] is None)
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
        stay = _stay(conn, NUM)
        s.check("SYNC NOW: both rooms' Booking.com calendars were read at once, "
                "the email not saying which room",
                sorted(fetched) == sorted(sources.values()), detail=str(fetched))
        s.check("the block starting that day gave the room",
                stay["room_id"] == one["id"] and stay["matched_by"] == "ical", detail=str(dict(stay)))
        s.check("and the departure", stay["departure_date"] == (a + timedelta(days=1)).isoformat())
        s.check("the extranet link is kept with it", stay["extranet_url"] == extranet)
        letters = _letters(sent)
        s.check("the house is told, once, in the words the house uses",
                [x["subject"] for x in letters]
                == [f"Booked on Booking.com – {one['name']}, {span}, reservation {NUM}"],
                detail=str([x["subject"] for x in sent]))
        s.check("at the rooms inbox, not the owner's",
                letters and letters[0]["to"] == m.house_address_for(conn, "rooms"))
        s.check("saying where the guest's name is, with the link",
                letters and "Guest name: in the Booking.com extranet" in letters[0]["body"]
                and extranet in letters[0]["body"], detail=letters and letters[0]["body"])
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [])
            m.run_ical_sync_job(conn)
        s.check("reading again does not tell the house twice", len(_letters(sent)) == 1,
                detail=str([x["subject"] for x in sent]))

    listed = [st for st in m.channel_stays(conn) if st["reference_code"] == NUM]
    s.check("it is a stay on every list now, in its room, not named",
            len(listed) == 1 and listed[0]["room_id"] == one["id"] and not listed[0]["named"],
            detail=str(listed))
    s.check("and its own block is not drawn a second time as a stay nobody named",
            not [st for st in m.channel_stays(conn) if st["from_calendar"]
                 and st["room_id"] == one["id"] and st["arrival_date"] == a.isoformat()])
    with m.app.test_request_context("/"):
        rows = m.build_overview(conn, "week", a)["rows"]
        arrivals = m.arrivals_sheet(conn, a)
        cal = m.build_calendar(conn, "month", a)
    mine = [r for r in rows if NUM in r["title"]]
    s.check("the calendar labels it 'Booking.com – room – dates', with the reservation",
            any(r["title"].startswith(f"Booking.com – {one['name']} – {span} · {NUM} · arrives")
                for r in mine), detail=str([r["title"] for r in rows if "Booking" in r["title"]]))
    cell = next(c for c in cal["cells"] if c["iso"] == a.isoformat())
    chips = [e for e in cell["events"] if NUM in e["title"]]
    s.check("with a B. on the chip, which a month cell's ellipsis cannot take",
            chips and chips[0]["short"] == f"B. ↓ {one['name']}", detail=str([e["short"] for e in chips]))
    card = next((c for c in cal["cards"] if chips and c["uid"] == chips[0]["card"]), None)
    s.check("its card says where the name is, and opens the extranet",
            card and ("Guest name", "in the Booking.com extranet") in card["facts"]
            and ("Booking.com extranet", extranet) in card["actions"], detail=str(card))
    s.check("the arrivals sheet counts it", any(st["reference_code"] == NUM
                                                for st in arrivals["channel_arriving"]))
    page = oc.get("/admin/channel-stays?q=" + NUM).get_data(as_text=True)
    text = visible_text(page)
    s.check("the Booking.com page says it is still to come", "Still to come" in text,
            detail=text[:400])
    s.check("and where its guest's name is, with the link",
            "Guest name: in the Booking.com extranet" in text and extranet in page)
    month = visible_text(oc.get(f"/calendar?view=month&date={a.isoformat()}").get_data(as_text=True))
    # Drawn as a bar over its nights since 19 October 2026, labelled with the
    # channel's letter and, the guest being unnamed, the room.
    s.check("the calendar page draws it", f"B. {one['name']}" in month)

    # ------------------------------------------------------------------
    s.section("Two rooms with a Booking.com stay starting the same day: not guessed")
    b = base + timedelta(days=10)
    feeds[sources[one["id"]]].append((b, b + timedelta(days=2)))
    feeds[sources[two["id"]]].append((b, b + timedelta(days=3)))
    fetched.clear()
    with _StandIns(feeds, fetched, sent):
        mail_id = _mail(conn, 2, _subject("New booking", NUM2, b))
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    stay2 = _stay(conn, NUM2)
    s.check("left without a room", stay2["room_id"] is None and stay2["departure_date"] is None,
            detail=str(dict(stay2)))
    s.check("and the note names both rooms",
            stay2["match_note"] and one["name"] in stay2["match_note"]
            and two["name"] in stay2["match_note"], detail=stay2["match_note"])
    s.check("nobody is told of a room that might be the wrong one",
            not any(NUM2 in x["subject"] for x in sent))
    text = visible_text(oc.get("/admin/channel-stays?q=" + NUM2).get_data(as_text=True))
    s.check("the page says so, and offers to fill it in by hand",
            "Choose the room by hand" in text and "Fill in by hand" in text, detail=text[:500])
    unnamed = [st for st in m.channel_stays(conn)
               if st["from_calendar"] and st["arrival_date"] == b.isoformat()
               and st["room_id"] in (one["id"], two["id"])]
    s.check("both rooms' nights are still on every list, as Booking.com stays",
            len(unnamed) == 2, detail=str(unnamed))

    s.section("By hand")
    r = oc.post("/admin/channel-stays/add", data={
        "reservation_number": NUM2, "room_id": two["id"], "arrival_date": b.isoformat(),
        "departure_date": (b + timedelta(days=3)).isoformat(), "guest_name": f"Ana {TAG} Ruiz"},
        follow_redirects=True)
    stay2 = _stay(conn, NUM2)
    s.check("the same number fills in the stay that was waiting",
            stay2["room_id"] == two["id"] and stay2["matched_by"] == "hand"
            and stay2["guest_name"] == f"Ana {TAG} Ruiz" and stay2["match_note"] is None,
            response=r, detail=str(dict(stay2)))
    s.check("and says what it did", any(NUM2 in f for f in flashes(r)), detail=str(flashes(r)))
    with _StandIns(feeds, fetched, sent), m.app.test_request_context("/"):
        m.after_booking_com_mail(conn, [])
    s.check("the house is told once a room is chosen",
            any(x["subject"].startswith(f"Booked on Booking.com – {two['name']},")
                and NUM2 in x["subject"] for x in sent), detail=str([x["subject"] for x in sent]))
    m.pair_booking_com_stays(conn)
    s.check("and pairing never moves what the owner typed", _stay(conn, NUM2)["room_id"] == two["id"])
    left = [st for st in m.channel_stays(conn)
            if st["from_calendar"] and st["arrival_date"] == b.isoformat()
            and st["room_id"] in (one["id"], two["id"])]
    s.check("the other room's block is still a stay nobody has named",
            [st["room_id"] for st in left] == [one["id"]], detail=str(left))
    c = base + timedelta(days=20)
    r = oc.post("/admin/channel-stays/add", data={
        "reservation_number": NUM4, "room_id": one["id"], "arrival_date": c.isoformat(),
        "departure_date": c.isoformat()}, follow_redirects=True)
    s.check("a stay that leaves the day it arrives is refused, and says why",
            _stay(conn, NUM4) is None and any("after the day they arrive" in f for f in flashes(r)),
            detail=str(flashes(r)))
    r = oc.post("/admin/channel-stays/add", data={
        "reservation_number": NUM4, "room_id": one["id"], "arrival_date": c.isoformat(),
        "departure_date": (c + timedelta(days=2)).isoformat(), "guests": "2"},
        follow_redirects=True)
    stay4 = _stay(conn, NUM4)
    s.check("one that came before the mailbox was read is added",
            stay4 and stay4["status"] == "confirmed" and stay4["guests"] == 2, response=r)
    r = ec.post("/admin/channel-stays/add", data={
        "reservation_number": "9753100009", "room_id": one["id"], "arrival_date": c.isoformat(),
        "departure_date": (c + timedelta(days=2)).isoformat()})
    s.check("an employee cannot add one", r.status_code in (302, 403)
            and _stay(conn, "9753100009") is None, response=r)
    r = oc.post(f"/admin/channel-stays/{stay2['id']}/remove", follow_redirects=True)
    s.check("one Booking.com has written about cannot be taken back",
            _stay(conn, NUM2) is not None and any("to cancel" in f for f in flashes(r)),
            detail=str(flashes(r)))
    r = oc.post(f"/admin/channel-stays/{stay4['id']}/remove", follow_redirects=True)
    s.check("one typed by mistake can", _stay(conn, NUM4) is None, response=r)

    # ------------------------------------------------------------------
    s.section("A change moves it; a cancellation takes it off")
    a2 = base + timedelta(days=30)
    feeds[sources[one["id"]]] = [blk for blk in feeds[sources[one["id"]]] if blk[0] != a]
    feeds[sources[one["id"]]].append((a2, a2 + timedelta(days=2)))
    fetched.clear()
    later = _now() + timedelta(minutes=5)
    with _StandIns(feeds, fetched, sent):
        mail_id = _mail(conn, 3, _subject("Modified booking", NUM, a2), received=later)
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    stay = _stay(conn, NUM)
    s.check("a changed stay is paired with the block on its new day",
            stay["arrival_date"] == a2.isoformat()
            and stay["departure_date"] == (a2 + timedelta(days=2)).isoformat()
            and stay["room_id"] == one["id"], detail=str(dict(stay)))
    s.check("with every calendar read, since a change may move the room",
            sorted(fetched) == sorted(sources.values()), detail=str(fetched))
    s.check("and the house is told of the change",
            any(x["subject"] == f"Changed on Booking.com – {one['name']}, "
                f"{m.stay_span_short(a2.isoformat(), (a2 + timedelta(days=2)).isoformat())}, "
                f"reservation {NUM}" for x in sent), detail=str([x["subject"] for x in sent]))
    with _StandIns(feeds, fetched, sent):
        mail_id = _mail(conn, 4, _subject("Cancelled booking", NUM, a2),
                        received=later + timedelta(minutes=5))
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    s.check("a cancellation cancels it", _stay(conn, NUM)["status"] == "cancelled")
    s.check("and takes it off every list, its block with it while the calendar catches up",
            not [st for st in m.channel_stays(conn) if st["room_id"] == one["id"]
                 and st["arrival_date"] == a2.isoformat()])
    s.check("and the house is told",
            any(x["subject"].startswith("Cancelled on Booking.com") and NUM in x["subject"]
                for x in sent))

    # ------------------------------------------------------------------
    s.section("Sync now goes to the room the email names")
    room_row = conn.execute("SELECT channel_name FROM rooms WHERE id = ?", (two["id"],)).fetchone()
    d = base + timedelta(days=40)
    feeds[sources[two["id"]]].append((d, d + timedelta(days=2)))
    fetched.clear()
    with _StandIns(feeds, fetched, sent):
        mail_id = _mail(conn, 5, _subject("New booking", NUM3, d),
                        f"<p>Room: {two['name']}</p>")
        with m.app.test_request_context("/"):
            m.after_booking_com_mail(conn, [mail_id])
    s.check("only that room's calendar is read", fetched == [sources[two["id"]]],
            detail=str(fetched))
    stay3 = _stay(conn, NUM3)
    s.check("and its block gives the nights, in the room the email named",
            stay3["room_id"] == two["id"] and stay3["departure_date"] == (d + timedelta(days=2)).isoformat(),
            detail=str(dict(stay3)))
    del room_row
    fetched.clear()
    with _StandIns(feeds, fetched, sent):
        r = oc.post("/admin/channel-stays/sync", follow_redirects=True)
    s.check("the page's button reads every Booking.com calendar now",
            set(sources.values()) <= set(fetched)
            and any("Booking.com calendar" in f for f in flashes(r)),
            response=r, detail=str(flashes(r)))
    r = ec.post("/admin/channel-stays/sync")
    s.check("and an employee cannot press it", r.status_code in (302, 403), response=r)

    # ------------------------------------------------------------------
    s.section("A stay whose email was read before this is read again")
    # As on live the evening this shipped: the email for 5979778151 had been
    # read that morning, the stay kept no day, and an email is never read
    # twice -- so the fix never reached it.
    f_day = base + timedelta(days=45)
    src2 = conn.execute("SELECT id FROM ical_sources WHERE url = ?", (sources[one["id"]],)).fetchone()
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, ?, ?)", (one["id"], src2["id"], f_day.isoformat(),
                                         (f_day + timedelta(days=1)).isoformat()))
    then = (_now() - timedelta(days=1)).isoformat()
    cur = conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, first_seen_at,
               last_event_at, updated_at)
           VALUES ('booking.com', ?, 'confirmed', ?, ?, ?)""", (NUM5, then, then, then))
    early = "https://admin.booking.com/hotel/manage/booking.html?res_id=" + NUM5
    conn.execute(
        """INSERT INTO ota_mail (channel, source, source_id, received_at, from_address,
               from_name, reply_to, subject, kind, reservation_number, body_text, links,
               from_booking_com, created_at, ota_reservation_id, house_told_at)
           VALUES ('booking.com', 'graph', ?, ?, 'noreply@booking.com', 'Booking.com', '', ?,
                   'reservation_new', ?, 'You have a new booking.', ?, 1, ?, ?, ?)""",
        (f"<{TAG}early@booking.com>", then, _subject("New booking", NUM5, f_day), NUM5,
         m.json.dumps([{"text": "View booking", "href": early}]), then, cur.lastrowid, then))
    conn.commit()
    s.check("set up: it has no day, as it was saved", _stay(conn, NUM5)["arrival_date"] is None)
    m.pair_booking_com_stays(conn)
    stay5 = _stay(conn, NUM5)
    s.check("its kept email is read again for the day it arrives",
            stay5["arrival_date"] == f_day.isoformat(), detail=str(dict(stay5)))
    s.check("and it is matched to its room and nights like any other",
            stay5["room_id"] == one["id"]
            and stay5["departure_date"] == (f_day + timedelta(days=1)).isoformat())
    s.check("with the extranet link from that email", stay5["extranet_url"] == early)
    text = visible_text(oc.get("/admin/channel-stays?q=" + NUM5).get_data(as_text=True))
    s.check("and the page has it still to come, not been and gone",
            "Still to come" in text and "Been and gone" not in text, detail=text[:600])

    # ------------------------------------------------------------------
    s.section("A stay only the calendar knows about is a stay")
    e = base + timedelta(days=50)
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IN "
                 "(SELECT id FROM ical_sources WHERE url LIKE ?)", (f"%{TAG}%",))
    src = conn.execute("SELECT id FROM ical_sources WHERE url = ?", (sources[two["id"]],)).fetchone()
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, ?, ?)", (two["id"], src["id"], e.isoformat(),
                                         (e + timedelta(days=2)).isoformat()))
    src1 = conn.execute("SELECT id FROM ical_sources WHERE url = ?", (sources[one["id"]],)).fetchone()
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, ?, ?)", (one["id"], src1["id"], e.isoformat(),
                                         (e + timedelta(days=2)).isoformat()))
    conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at) "
                 "VALUES (?, ?, ?, ?, ?)", (one["id"], e.isoformat(),
                                            (e + timedelta(days=5)).isoformat(), TAG, _now().isoformat()))
    conn.commit()
    only = [st for st in m.channel_stays(conn) if st["from_calendar"]
            and st["arrival_date"] == e.isoformat() and st["room_id"] in (one["id"], two["id"])]
    s.check("on the lists, as an unnamed Booking.com stay in its room",
            [st["room_id"] for st in only] == [two["id"]]
            and only[0]["guest_name"] == "A Booking.com guest", detail=str(only))
    s.check("not where the house has closed the room itself: that is an echo, not a guest",
            not any(st["room_id"] == one["id"] for st in only))
    s.check("and left out where a list needs the reservation itself",
            not any(st["from_calendar"] for st in m.channel_stays(conn, blocks=False)))
    with m.app.test_request_context("/"):
        rows = m.build_overview(conn, "week", e)["rows"]
        sheet = m.arrivals_sheet(conn, e)
        _subj, digest, _x = m.morning_digest(conn, e)
    span_e = m.stay_span_short(e.isoformat(), (e + timedelta(days=2)).isoformat())
    s.check("the calendar draws it as 'Booking.com – room – dates'",
            any(r["title"].startswith(f"Booking.com – {two['name']} – {span_e} · arrives")
                for r in rows), detail=str([r["title"] for r in rows if "Booking" in r["title"]]))
    s.check("the arrivals sheet counts it, saying where it came from",
            any(st["room_id"] == two["id"] and st["from_calendar"]
                for st in sheet["channel_arriving"]))
    s.check("and so does the morning note", "A Booking.com guest (Booking.com)" in digest,
            detail=digest[:500])
    page = visible_text(oc.get("/admin/channel-stays").get_data(as_text=True))
    s.check("the Booking.com page lists it under Only on the calendar, to be named",
            "Only on the calendar" in page and f"Booking.com – {two['name']}" in page
            and "Add the guest" in page, detail=page[-900:])
    filled = oc.get(f"/admin/channel-stays?room={two['id']}&arrive={e.isoformat()}"
                    f"&leave={(e + timedelta(days=2)).isoformat()}").get_data(as_text=True)
    s.check("whose link fills in the room and the dates",
            f'value="{e.isoformat()}"' in filled and f'value="{two["id"]}" selected' in filled)

    # ------------------------------------------------------------------
    s.section("An atelier's first day is one chip in a week or a month")
    place = {"kind": "workshop", "lane": "workshop", "status": "confirmed", "date": "2030-01-07",
             "short": "Plaster", "title": "x", "id": 1, "card": "workshop-s9",
             "facts": {"session": 9, "workshop": "Plaster", "party": 2}}
    rows = [place, dict(place, id=2, status="pending", facts=dict(place["facts"], party=1)),
            dict(place, id=3, facts=dict(place["facts"], party=None)),
            dict(place, id=4, date="2030-01-08", facts=dict(place["facts"], running=True)),
            {"kind": "task", "date": "2030-01-07", "title": "t", "id": 5}]
    merged = m.one_chip_per_workshop_day(rows)
    first = [r for r in merged if r["kind"] == "workshop" and not r["facts"].get("running")]
    s.check("three places are one chip saying how many guests arrive",
            [r["short"] for r in first] == ["Plaster – 4 guests arrive"], detail=str(first))
    s.check("with the count in its badge, out of the month cell's ellipsis",
            [r["badge"] for r in first] == ["4 arrive"])
    s.check("which still opens the sitting's card, where the names are",
            first and first[0]["card"] == "workshop-s9")
    s.check("the later day and everything else are left as they were",
            len(merged) == 3 and merged[-1]["kind"] == "task")
