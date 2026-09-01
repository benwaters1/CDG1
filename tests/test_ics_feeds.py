"""The calendars that leave the building.

`/ics/<token>.ics` is pasted into Airbnb, Booking.com and VRBO so they can see
what is booked here and stop selling the same night twice. It is the only URL
in the app whose whole purpose is to be read by a third party, and it is
reachable by anyone holding the token. That gives it two failure modes, and
they pull in opposite directions:

  TOO MUCH and a guest's name, email or requests are handed to a channel that
  never needed them, on a URL that is in somebody's calendar subscription for
  years. The feed says "Booked — <room>" and nothing else. Nothing enforced
  that; adding b['guest_name'] to the SUMMARY is a one-word change that looks
  like an improvement and would leak every guest on the property.

  TOO LITTLE and a date goes unblocked, the channel sells it, and two parties
  arrive for one room. Cancelled bookings must drop out; pending ones must
  not, because a pending request has already staked a claim.

And one arithmetic trap that has bitten this codebase before: DTEND on an
all-day VEVENT is EXCLUSIVE. A stay arriving the 10th and leaving the 14th is
DTSTART 10, DTEND 14 — the 14th is free and sellable. Writing departure + 1
blocks a night the château could have let; writing departure - 1 sells a night
somebody is still in.

`/book/<token>/calendar.ics` is the opposite case: it goes to the guest, about
their own stay, so it may carry their reference code — but it is checked here
that it does not carry anybody else's.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZICS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(room_id, ref, arrival, nights=4, status="confirmed", who="Amelie Secret"):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, special_requests, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, 900, ?, 0, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {who}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid", "+33 6 11 22 33 44",
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(), status,
         "allergic to peanuts and needs a ground floor room",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _rooms():
    conn = db()
    try:
        return conn.execute(
            "SELECT id, name, export_token FROM rooms WHERE active = 1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Calendar feeds")
    _cleanup()
    clients()
    anon = m.app.test_client()
    rooms = _rooms()
    room = rooms[0]
    other = rooms[1] if len(rooms) > 1 else None
    arrival = house_today() + timedelta(days=40)
    booked = _booking(room["id"], "A", arrival)

    s.section("The room feed is reachable with its token")
    r = anon.get(f"/ics/{room['export_token']}.ics")
    body = r.get_data(as_text=True)
    s.check("it serves", r.status_code == 200, r)
    s.check("as a calendar, not a web page",
            r.mimetype == "text/calendar", detail=f"{r.mimetype}")
    s.check("and the stay is in it", f"booking-{booked['id']}@" in body,
            detail="the channel would not know the room was taken")

    s.section("It says the room is taken and nothing else about who")
    # The one URL in the app whose purpose is to be read by a third party.
    for label, leak in (("the guest's name", "Amelie Secret"),
                        ("their email", f"{TAG.lower()}.a@example.invalid"),
                        ("their phone", "+33 6 11 22 33 44"),
                        ("their requests", "allergic to peanuts"),
                        ("the reference code", f"{TAG}-A")):
        s.check(f"{label} is not in the feed", leak not in body,
                detail=f"{leak!r} is being handed to Airbnb, Booking.com and "
                       "anyone else holding this URL")
    s.check("what it does say is that it is booked", "Booked" in body,
            detail="a channel with no summary at all cannot show the block")
    s.check("and which room", room["name"].split()[0] in body)

    s.section("DTEND is exclusive, so departure day is sellable")
    # The trap. departure + 1 blocks a night the château could let; departure - 1
    # sells a night somebody is still sleeping in.
    want_start = arrival.strftime("%Y%m%d")
    want_end = (arrival + timedelta(days=4)).strftime("%Y%m%d")
    s.check("DTSTART is the arrival date", f"DTSTART;VALUE=DATE:{want_start}" in body,
            detail=f"wanted {want_start}")
    s.check("DTEND is the departure date itself", f"DTEND;VALUE=DATE:{want_end}" in body,
            detail=f"wanted {want_end} — one day either way is a night sold "
                   "twice or a night lost")

    s.section("A cancelled stay stops blocking the date")
    conn = db()
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booked["id"],))
    conn.commit()
    conn.close()
    after = anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True)
    s.check("it drops out of the feed", f"booking-{booked['id']}@" not in after,
            detail="the channel keeps the date blocked and the room goes unsold")

    s.section("But a pending request still does")
    # A pending request has already staked a claim — is_range_available treats
    # it as blocking, and the feed has to agree or the two disagree.
    conn = db()
    conn.execute("UPDATE bookings SET status = 'pending' WHERE id = ?", (booked["id"],))
    conn.commit()
    conn.close()
    pending = anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True)
    s.check("a pending stay is in the feed", f"booking-{booked['id']}@" in pending,
            detail="a channel could sell a date somebody has already asked for")
    conn = db()
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (booked["id"],))
    conn.commit()
    conn.close()

    s.section("One room's token shows only that room")
    if other:
        theirs = _booking(other["id"], "B", arrival + timedelta(days=90), who="Bruno Other")
        mine = anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True)
        s.check("the other room's stay is not in this feed",
                f"booking-{theirs['id']}@" not in mine,
                detail="one channel listing would block dates on every room")
        s.check("and its own is", f"booking-{booked['id']}@" in mine)

    s.section("A token nobody issued gets nothing")
    s.check("an invented token is a 404",
            anon.get("/ics/not-a-real-token.ics").status_code == 404)
    s.check("and an empty one too",
            anon.get("/ics/.ics").status_code in (404, 308))

    s.section("It is a calendar a parser will accept")
    body = anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True)
    s.check("it opens and closes",
            body.startswith("BEGIN:VCALENDAR") and body.rstrip().endswith("END:VCALENDAR"))
    s.check("every event is closed",
            body.count("BEGIN:VEVENT") == body.count("END:VEVENT"),
            detail=f"{body.count('BEGIN:VEVENT')} begins, {body.count('END:VEVENT')} ends")
    s.check("lines end CRLF, as the format requires",
            "\r\n" in body and "\n" not in body.replace("\r\n", ""),
            detail="a bare LF makes some parsers reject the whole feed")
    s.check("it carries a version and a product id",
            "VERSION:2.0" in body and "PRODID:" in body)

    s.section("The guest's own copy is about their stay")
    guest = anon.get(f"/book/{booked['manage_token']}/calendar.ics")
    gbody = guest.get_data(as_text=True)
    s.check("it serves", guest.status_code == 200, guest)
    s.check("it names the house", "Gudanes" in gbody)
    s.check("and carries their reference so they can find it again",
            f"{TAG}-A" in gbody, detail="the guest cannot match it to their booking")
    if other:
        s.check("but nobody else's booking is in it",
                f"booking-{theirs['id']}@" not in gbody,
                detail="one guest's calendar file carried another's stay")

    s.section("And is refused once the stay is not live")
    conn = db()
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booked["id"],))
    conn.commit()
    conn.close()
    s.check("a cancelled booking's calendar is a 404",
            anon.get(f"/book/{booked['manage_token']}/calendar.ics").status_code == 404)
    s.check("and an invented token is too",
            anon.get("/book/nope/calendar.ics").status_code == 404)

    s.section("The sync trigger tells a prober nothing")
    # 404 rather than 401/403 on purpose: a wrong token must not confirm that
    # the endpoint exists.
    s.check("no token is a 404", anon.get("/api/sync-ical").status_code == 404)
    s.check("a wrong token is the same 404",
            anon.get("/api/sync-ical?token=guess").status_code == 404,
            detail="a different code here tells somebody the endpoint is real")
    s.check("and it is not open to a POST either",
            anon.post("/api/sync-ical", data={"token": "guess"}).status_code == 404)

    _cleanup()
    return s
