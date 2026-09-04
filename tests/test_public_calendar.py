"""The dates the public calendars ask for, which nothing was supplying.

`booked_dates` is read in three places — the date picker's JSON block in
public_base, the availability calendar, and the room search — and was supplied
by nothing at all. All three read it as `booked_dates or []`, so the picker
struck out no nights and the calendar drew an empty grid, on pages that
rendered perfectly. `dinner_dates` is the new half the design side asked for.

Three things carry this file.

  A NIGHT IS TAKEN IF THE HOUSE CANNOT SELL IT, whatever the reason. Not just
  bookings: a workshop takes the whole château for its run, a confirmed event
  takes its own days, a provisional hold takes dates somebody has been
  promised, and a block takes dates the owner has closed. A guest offered a
  night the desk would then refuse has been told something untrue by the
  calendar that exists to stop exactly that.

  A NIGHT IS ONLY GONE WHEN EVERY ROOM IS GONE. One booking out of five rooms
  is not a full house, and striking it out would turn away four rooms' worth
  of guests.

  THE PUBLISHED MENU IS THE DINNER SCHEDULE. `menus` already carries a service
  date and a published status and the kitchen already works from it, so a
  night opens when the menu is published. A second "open nights" table would
  have drifted out of step with the kitchen inside a month.
"""
from datetime import datetime, timedelta, timezone
import json
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPC"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM room_blocks WHERE reason = ?", (TAG,))
    conn.execute("""DELETE FROM event_holds WHERE event_id IN
                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _taken():
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return set(m.nights_already_taken(conn))
    finally:
        conn.close()


def _dinners():
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return list(m.nights_la_table_is_cooking(conn))
    finally:
        conn.close()


def run():
    s = Suite("The public calendars")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    conn = db()
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id").fetchall()
    now = datetime.now(timezone.utc).isoformat()
    before = _taken()

    # Dates verified free, one at a time. Picking them by arithmetic from a
    # single free day is not enough: the seeded ateliers close whole weeks, so
    # base+60 landed inside one and a check about a lapsed hold failed on a
    # date that was never free in the first place.
    def _clear(from_day):
        day = from_day
        while any((day + timedelta(days=n)).isoformat() in before for n in range(4)):
            day += timedelta(days=1)
        return day

    base = _clear(m.house_today() + timedelta(days=250))

    s.section("One room of five taken is not a full house")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 100, 0, 0, ?)""",
        (rooms[0]["id"], f"{TAG}-1", f"tok{TAG}1", f"{TAG} One", base.isoformat(),
         (base + timedelta(days=1)).isoformat(), now))
    conn.commit()
    s.check("that night is still on sale", base.isoformat() not in _taken(),
            detail=f"{len(rooms)} rooms active — striking it out turns away "
                   "four rooms' worth of guests")

    s.section("Every room taken is a night the house cannot sell")
    for i, room in enumerate(rooms[1:], start=2):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status,
               total_price, amount_paid, city_tax, created_at)
               VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 100, 0, 0, ?)""",
            (room["id"], f"{TAG}-{i}", f"tok{TAG}{i}", f"{TAG} Room{i}",
             base.isoformat(), (base + timedelta(days=1)).isoformat(), now))
    conn.commit()
    taken = _taken()
    s.check("now it is gone", base.isoformat() in taken,
            detail=f"{len(rooms)} of {len(rooms)} rooms booked")
    # A DEPARTURE DAY IS SELLABLE AGAIN. Striking it out loses a night per
    # booking across the whole calendar.
    s.check("but the day they all leave is not",
            (base + timedelta(days=1)).isoformat() not in taken,
            detail="a departure day is somebody else's arrival day")

    s.section("And everything else that closes a night")
    ws_day = _clear(base + timedelta(days=20))
    workshop = conn.execute(
        "SELECT id FROM workshops WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
           capacity, created_at) VALUES (?, ?, ?, 10, ?)""",
        (workshop["id"], ws_day.isoformat(),
         (ws_day + timedelta(days=2)).isoformat(), now))
    ev_day = _clear(base + timedelta(days=40))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, end_date, status, created_at)
           VALUES (?, ?, 'wedding', ?, '', ?, ?, 'confirmed', ?)""",
        (f"{TAG}-EV", f"tok{TAG}ev", f"{TAG} Wedding", ev_day.isoformat(),
         (ev_day + timedelta(days=1)).isoformat(), now))
    hold_day = _clear(base + timedelta(days=60))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, status, created_at)
           VALUES (?, ?, 'wedding', ?, '', ?, 'new', ?)""",
        (f"{TAG}-HELD", f"tok{TAG}held", f"{TAG} Maybe", hold_day.isoformat(), now))
    conn.commit()
    maybe = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                         (f"{TAG}-HELD",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO event_holds (event_id, start_date, end_date, expires_at,
           created_at) VALUES (?, ?, ?, ?, ?)""",
        (maybe, hold_day.isoformat(), hold_day.isoformat(),
         (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(), now))
    block_day = _clear(base + timedelta(days=80))
    conn.execute(
        """INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (rooms[0]["id"], block_day.isoformat(),
         (block_day + timedelta(days=2)).isoformat(), TAG, now))
    conn.commit()
    taken = _taken()
    s.check("an atelier closes its whole run",
            all((ws_day + timedelta(days=d)).isoformat() in taken for d in (0, 1, 2)),
            detail="a workshop takes the château, not one room")
    s.check("a confirmed wedding closes its days",
            ev_day.isoformat() in taken
            and (ev_day + timedelta(days=1)).isoformat() in taken,
            detail="both days of a two-day event")
    s.check("a date somebody has been promised is closed too",
            hold_day.isoformat() in taken,
            detail="a guest offered a night the desk would then refuse has "
                   "been told something untrue")
    s.check("and dates the owner has blocked",
            block_day.isoformat() in taken)

    s.section("A hold that has run out stops closing anything")
    conn.execute(
        "UPDATE event_holds SET expires_at = ? WHERE event_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), maybe))
    conn.commit()
    s.check("the date comes back", hold_day.isoformat() not in _taken(),
            detail="the same rule the booking side reads, so the calendar and "
                   "the desk cannot disagree")

    s.section("Dinner comes from the published menu, and nothing else")
    cook = _clear(base + timedelta(days=5))
    for offset, service, status in ((0, "dinner", "published"),
                                    (1, "dinner", "draft"),
                                    (2, "lunch", "published"),
                                    (-300, "dinner", "published")):
        day = cook + timedelta(days=offset)
        conn.execute(
            """INSERT INTO menus (service_date, service, status, source, notes,
               created_at) VALUES (?, ?, ?, 'manual', ?, ?)""",
            (day.isoformat(), service, status, TAG, now))
    conn.commit()
    conn.close()
    dinners = _dinners()
    s.check("a published dinner is an open night", cook.isoformat() in dinners,
            detail=f"{dinners[:4]}")
    s.check("a draft is not",
            (cook + timedelta(days=1)).isoformat() not in dinners,
            detail="the night opens when the kitchen publishes, which is the "
                   "one schedule anybody keeps up to date")
    s.check("nor a lunch", (cook + timedelta(days=2)).isoformat() not in dinners)
    s.check("nor a night long past",
            (cook - timedelta(days=300)).isoformat() not in dinners,
            detail="a guest cannot book it and it is not context either")

    s.section("The pages actually carry them")
    body = m.app.test_client().get("/book").get_data(as_text=True)
    block = re.search(r'id="booked-dates">(.*?)</script>', body, re.S)
    s.check("the picker is given the taken nights", block is not None)
    if block:
        picker = json.loads(block.group(1))
        s.check("with real dates in it", base.isoformat() in picker,
                detail=f"{len(picker)} date(s) — this list has been empty the "
                       "whole time the picker has existed")
    s.check("and the calendar names the dinner nights in its key",
            "Dinner at La Table" in body,
            detail="the design side's third key item, which only appears when "
                   "dates are supplied")

    s.section("The back end does not pay for a calendar it does not draw")
    admin = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("no date lists on an admin page", 'id="booked-dates"' not in admin,
            detail="480-odd back-end pages have no calendar to draw")
    # ON THE RULE, not on the rendered page. Reading the HTML proved only that
    # admin pages do not extend public_base, which they would not do whatever
    # this rule said -- so the check could not fail.
    s.check("and the rule itself says so",
            not m.page_draws_a_calendar("admin_bookings")
            and not m.page_draws_a_calendar("management_financials"),
            detail="asked of the app's own map of back-end pages")
    s.check("while a public page is asked for them",
            m.page_draws_a_calendar("book_rooms")
            and m.page_draws_a_calendar("home"),
            detail="the whole point is not having to remember it on the next "
                   "public page somebody adds")

    s.section("With nothing to show, it shows nothing")
    conn = db()
    with m.app.test_request_context("/"):
        none_at_all = m.nights_la_table_is_cooking(conn, days=0)
    conn.close()
    s.check("no dinner dates in a zero-day window", none_at_all == [],
            detail="both calendars degrade to exactly how they behaved before")

    _cleanup()
    return s
