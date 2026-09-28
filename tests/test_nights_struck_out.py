"""The nights the public calendars strike out: counted by ROOM, and read from
the same picture the booking check refuses from.

nights_already_taken builds `booked_dates` -- the list the date picker in
public_base strikes out, the availability calendar greys, and the room search
draws. It kept its own list of what closes a night, and the list was wrong
twice, both times in front of a guest:

  A BLOCK ON ONE ROOM CLOSED THE WHOLE HOUSE. room_blocks belong to a room,
  like bookings, but every row went straight into the taken set -- so the
  owner blocking one room for a family visit struck the night out for all of
  them, and every other room's guests were told the house was full.

  A ROOM LET ON ANOTHER CHANNEL WAS NEVER COUNTED. The iCal sync writes
  blocked_dates, and the tally counted bookings only, so a night with some
  rooms booked here and the rest on Booking.com showed as free. The guest
  picked it, filled in the form, and was refused at the end.

What carries this file:

  COUNT ROOMS, NOT ROWS. A booking, a channel block and a manual block on the
  same room are one room gone. Summing rows closes a night with a room free.

  ONLY ROOMS FOR SALE. A workshop-only room is active = 0; nothing on it opens
  or closes a night for a guest.

  THE PICKER CANNOT DISAGREE WITH THE BOOKING CHECK. Asked night by night
  across the whole public window: a night is struck out exactly when
  is_range_available refuses it for every room for sale.

  AND THE CALENDAR STRIKES THE NIGHT THAT IS TAKEN. _availcal.html keyed each
  square by toISOString, which is UTC. A square drawn at midnight in France is
  still the day before in UTC, so every taken night was greyed on the day
  after it.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import re
import secrets

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZSTRUCK"
ONE = timedelta(days=1)


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_blocks WHERE reason = ?", (TAG,))
    conn.execute("""DELETE FROM blocked_dates WHERE ical_source_id IN
                    (SELECT id FROM ical_sources WHERE label LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM ical_sources WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _taken(conn):
    with m.app.test_request_context("/"):
        return set(m.nights_already_taken(conn))


def run():
    s = Suite("The nights the public calendars strike out")
    _cleanup()
    conn = db()
    try:
        _run(s, conn)
    finally:
        conn.close()
        # Everything here is tagged and goes, including after a failure: the
        # database is shared by every suite in the run, and a room left
        # blocked or a stray room left behind would be somebody else's red.
        _cleanup()
    return s


def _run(s, conn):
    today = m.house_today()
    now = datetime.now(timezone.utc).isoformat()
    for_sale = [r["id"] for r in conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id").fetchall()]
    if not s.check("there are rooms for sale to count", len(for_sale) >= 2,
                   detail=f"{len(for_sale)} active room(s): with one room, one "
                          "room blocked IS a full house, and nothing here can "
                          "tell the difference"):
        return

    # Runs of nights on which every room for sale is free, by the booking
    # check itself -- not merely "not a full house", which is all the
    # picker's list can say. A night with three rooms already gone is not on
    # that list, and every check below needs to know exactly which rooms are
    # gone. The morning after each run is included and kept free, so the
    # checks about departure days have a night to look at.
    cursor = [today + timedelta(days=120)]

    def free_run(nights):
        day = cursor[0]
        for _ in range(m.PUBLIC_CALENDAR_DAYS):
            run_days = [day + timedelta(days=n) for n in range(nights + 1)]
            if all(m.is_range_available(conn, rid, d, d + ONE)[0]
                   for d in run_days for rid in for_sale):
                cursor[0] = run_days[-1] + ONE
                return day
            day += ONE
        raise AssertionError(f"no {nights} free night(s) from {cursor[0]} on")

    counter = [0]

    def book(room_id, night, nights=1, status="confirmed"):
        counter[0] += 1
        k = counter[0]
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status,
               total_price, amount_paid, city_tax, created_at)
               VALUES (?, ?, ?, ?, '', ?, ?, 2, ?, 100, 0, 0, ?)""",
            (room_id, f"{TAG}-{k}", f"tok{TAG}{k}", f"{TAG} Guest {k}",
             night.isoformat(), (night + timedelta(days=nights)).isoformat(),
             status, now))

    def pending(room_id, night, nights=1):
        book(room_id, night, nights, status="pending")

    def block(room_id, night, nights=1):
        conn.execute(
            """INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (room_id, night.isoformat(), (night + timedelta(days=nights)).isoformat(),
             TAG, now))

    sources = {}

    def channel(room_id, night, nights=1):
        # As the iCal sync writes a Booking.com or Airbnb stay: one source per
        # room, the start night inclusive, the end the checkout day.
        if room_id not in sources:
            cur = conn.execute(
                "INSERT INTO ical_sources (room_id, label, url) VALUES (?, ?, ?)",
                (room_id, f"{TAG} Booking.com", f"https://example.invalid/{TAG}.ics"))
            sources[room_id] = cur.lastrowid
        conn.execute(
            """INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date)
               VALUES (?, ?, ?, ?)""",
            (room_id, sources[room_id], night.isoformat(),
             (night + timedelta(days=nights)).isoformat()))

    first, last = for_sale[0], for_sale[-1]

    s.section("A block on one room is not a full house")
    one_blocked = free_run(3)
    block(first, one_blocked, nights=3)
    one_let = free_run(1)
    channel(first, one_let)
    conn.commit()
    taken = _taken(conn)
    s.check("a room the owner blocked leaves the night on sale",
            one_blocked.isoformat() not in taken,
            detail=f"{one_blocked}: one room of {len(for_sale)} blocked struck "
                   "the night out for the whole house")
    s.check("on every night of a longer block",
            not any((one_blocked + timedelta(days=n)).isoformat() in taken
                    for n in range(3)))
    s.check("and so does a room let on another channel",
            one_let.isoformat() not in taken, detail=str(one_let))

    s.section("Rooms let here and on Booking.com fill the house together")
    split = free_run(1)
    for i, rid in enumerate(for_sale):
        (book if i % 2 == 0 else channel)(rid, split)
    conn.commit()
    taken = _taken(conn)
    s.check("the night is struck out", split.isoformat() in taken,
            detail=f"{split}: {(len(for_sale) + 1) // 2} booked here, "
                   f"{len(for_sale) // 2} on a channel -- the tally counted "
                   "bookings only, so this showed as free and the guest was "
                   "refused on submitting the form")
    s.check("and the morning they leave is on sale again",
            (split + ONE).isoformat() not in taken,
            detail="a channel stay ends on its checkout day, like a booking")

    s.section("Every kind of gone at once, counted once per room")
    mixed = free_run(1)
    kinds = (pending, block, channel, book)
    for i, rid in enumerate(for_sale):
        kinds[i % len(kinds)](rid, mixed)
    # One room, three rows: a booking, a block and a channel stay on the
    # same night. The last room is free, so the house is not full -- however
    # many rows there are.
    rows_not_rooms = free_run(1)
    book(first, rows_not_rooms)
    block(first, rows_not_rooms)
    channel(first, rows_not_rooms)
    for rid in for_sale[1:-1]:
        book(rid, rows_not_rooms)
    conn.commit()
    taken = _taken(conn)
    s.check("a pending request, a block, a channel stay and a booking close it",
            mixed.isoformat() in taken, detail=str(mixed))
    s.check("and each of them ends on the morning the room is free again",
            (mixed + ONE).isoformat() not in taken,
            detail="a block's end date is the day it lifts, as the booking "
                   "check and both calendars read it")
    s.check("rooms are counted, not rows",
            rows_not_rooms.isoformat() not in taken,
            detail=f"{rows_not_rooms}: {len(for_sale) + 1} rows on "
                   f"{len(for_sale) - 1} rooms, and room {last} free")

    s.section("Only the rooms the house sells by the night")
    cur = conn.execute(
        "INSERT INTO rooms (name, export_token, active, sort_order) VALUES (?, ?, 0, 999)",
        (f"{TAG} Studio", secrets.token_hex(12)))
    studio = cur.lastrowid
    all_blocked = free_run(1)
    for rid in for_sale:
        block(rid, all_blocked)
    studio_booked = free_run(1)
    for rid in for_sale[:-1]:
        book(rid, studio_booked)
    book(studio, studio_booked)
    conn.commit()
    taken = _taken(conn)
    s.check("every room for sale blocked closes the night",
            all_blocked.isoformat() in taken,
            detail="a workshop-only room standing empty is no room for a guest")
    s.check("and a booking on a room that is not for sale fills nothing",
            studio_booked.isoformat() not in taken,
            detail=f"{studio_booked}: {len(for_sale) - 1} rooms booked and the "
                   "studio -- room "
                   f"{last} is still free")

    s.section("A page drawn after this request's own booking shows it")
    written = free_run(1)
    with m.app.test_request_context("/"):
        # The picture is now held on `g`, as it is once a room search has run.
        m.is_range_available(conn, first, written, written + ONE)
        for rid in for_sale:
            book(rid, written)
        after = set(m.nights_already_taken(conn))
    conn.commit()
    s.check("the night just filled is struck out", written.isoformat() in after,
            detail="answered from the picture held on g, which was read "
                   "before the booking was written")

    s.section("The picker and the booking check agree, night by night")
    # A confirmed event entered with its end before its start. The booking
    # check reads it as its first day alone; the old list turned the range
    # round and closed three nights the form would happily have sold.
    backwards = free_run(4)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, end_date, status, created_at)
           VALUES (?, ?, 'wedding', ?, '', ?, ?, 'confirmed', ?)""",
        (f"{TAG}-EV", f"tok{TAG}ev", f"{TAG} Wedding",
         (backwards + timedelta(days=3)).isoformat(), backwards.isoformat(), now))
    conn.commit()
    taken = _taken(conn)
    disagree = []
    with m.app.test_request_context("/"):
        for n in range(m.PUBLIC_CALENDAR_DAYS + 1):
            night = today + timedelta(days=n)
            refused = all(not m.is_range_available(conn, rid, night, night + ONE)[0]
                          for rid in for_sale)
            struck = night.isoformat() in taken
            if refused != struck:
                disagree.append(f"{night} {'refused' if refused else 'sellable'}"
                                f" at the form but {'struck' if struck else 'offered'}")
    s.check("a night is struck out exactly when every room refuses it",
            not disagree,
            detail=f"{len(disagree)} of {m.PUBLIC_CALENDAR_DAYS + 1} nights "
                   f"disagree: {disagree[:4]}")
    # Not vacuous: the window this compared holds both kinds of night.
    s.check("over a window with nights of both kinds in it",
            0 < len(taken) < m.PUBLIC_CALENDAR_DAYS + 1,
            detail=f"{len(taken)} struck out")
    s.check("the backwards event holds its own day",
            (backwards + timedelta(days=3)).isoformat() in taken)

    s.section("The page is handed the same list")
    body = m.app.test_client().get("/book").get_data(as_text=True)
    found = re.search(r'id="booked-dates">(.*?)</script>', body, re.S)
    picker = set(json.loads(found.group(1))) if found else set()
    s.check("the room search carries the full night",
            mixed.isoformat() in picker and split.isoformat() in picker,
            detail=f"{len(picker)} date(s) on the page")
    s.check("and not the night with one room blocked",
            one_blocked.isoformat() not in picker)

    s.section("The availability calendar strikes the night that is taken")
    with open(os.path.join(_harness.ROOT, "templates", "_availcal.html"),
              encoding="utf-8") as fh:
        src = fh.read()
    # The code, not the comments: the comment beside the fix names the trap.
    code = re.sub(r"/\*.*?\*/|\{#.*?#\}", "", src, flags=re.S)
    s.check("it still reads the picker's list", "booked-dates" in code)
    s.check("and keys each night by its local date, not UTC",
            "toISOString" not in code,
            detail="a square drawn at midnight in France is the day before "
                   "in UTC, so every taken night was greyed on the day after")


if __name__ == "__main__":
    print(run().report())
