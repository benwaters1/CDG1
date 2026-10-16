# -*- coding: utf-8 -*-
"""The night a workshop ends, on this site and on the channels.

Two rules from the owner, 16 October 2026, which pull in opposite directions
and so are checked together:

  ON THIS SITE a workshop holds its first night to the night BEFORE it ends.
  Its guests leave on the end date, so that night is the house's to sell --
  and the people most likely to want it are the workshop's own guests, who
  were being turned away with "Those dates are held for a workshop". The
  manage page offers them "A night before" and "A night after", and those
  links have to land on nights the rooms page will actually sell.

  ON THE CHANNELS (the /ics/<token>.ics feed Booking.com reads per room) a
  workshop goes out with a day EITHER SIDE, and so do the things that only
  ever closed the house here: confirmed events, live holds. Before this the
  feed carried nightly bookings and closures only, so Booking.com went on
  selling a room in a house given over to a wedding. It also carries one
  block a year out to three years out, so nothing is sold further ahead than
  the house has set rates for.

The buffer is the channels' only. Letting it leak into the site would turn
the workshop's own guests away again; letting the site's rule leak into the
feed would sell a turnaround night on Booking.com.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, free_window, ensure_room
import _harness

m = _harness.m
TAG = "ZZWSN"


def _cleanup():
    conn = db()
    try:
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                     "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM event_holds WHERE event_id IN "
                     "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)", (TAG + "%",))
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM room_blocks WHERE reason = ?", (TAG,))
        conn.commit()
    finally:
        conn.close()


def _ask(fn, *args, **kwargs):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return fn(conn, *args, **kwargs)
    finally:
        conn.close()


def _events(body):
    """[(uid, start, end, summary)] from an .ics body, dates as ISO."""
    out = []
    for block in body.split("BEGIN:VEVENT")[1:]:
        f = dict(re.findall(r"^([A-Z]+)(?:;[^:]*)?:(.*?)\r?$", block, re.M))
        iso = lambda v: f"{v[:4]}-{v[4:6]}-{v[6:8]}" if v else None
        out.append((f.get("UID"), iso(f.get("DTSTART")), iso(f.get("DTEND")), f.get("SUMMARY")))
    return out


def run():
    s = Suite("The night a workshop ends, here and on the channels")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    now = datetime.now(timezone.utc).isoformat()
    room = ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    conn.close()

    # Eight free nights, clear of the seeded ateliers, with the workshop in
    # the middle: arriving 2 nights in, leaving 6 nights in.
    w0 = free_window(room["id"], 9, after_days=60, clear_of_ateliers=True)
    start, end = w0 + timedelta(days=2), w0 + timedelta(days=6)

    conn = db()
    try:
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, active, created_at)
               VALUES (?, '', 400, 1, ?)""", (f"{TAG} Gilding", now))
        wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                           (f"{TAG} Gilding",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
               VALUES (?, ?, ?, 10, ?)""", (wid, start.isoformat(), end.isoformat(), now))
        sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?",
                           (wid,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, 'zzwsn@example.invalid', 2, 'confirmed', 800, ?)""",
            (sid, f"{TAG}-A", f"tok{TAG}a".lower(), f"{TAG} Guest", now))
        conn.commit()
    finally:
        conn.close()

    s.section("On this site, the night a workshop ends is for sale")
    ok, why = _ask(m.is_range_available, room["id"], start, start + timedelta(days=1))
    s.check("its first night is held", not ok and "workshop" in (why or ""), detail=f"{why!r}")
    ok, why = _ask(m.is_range_available, room["id"], end - timedelta(days=1), end)
    s.check("and the night before it ends", not ok, detail=f"{ok} {why!r}")
    ok, why = _ask(m.is_range_available, room["id"], end, end + timedelta(days=1))
    s.check("but the night it ends can be booked", ok and why is None, detail=f"{why!r}")
    ok, why = _ask(m.is_range_available, room["id"], start - timedelta(days=1), start)
    s.check("and so can the night before it starts", ok and why is None, detail=f"{why!r}")
    nights = _ask(m.unavailable_nights, room["id"], w0, w0 + timedelta(days=9))
    s.check("the room page's calendar agrees, night by night",
            all((start + timedelta(days=i)).isoformat() in nights for i in range(4))
            and end.isoformat() not in nights
            and (start - timedelta(days=1)).isoformat() not in nights,
            detail=str(nights))
    taken = set(_ask(m.nights_already_taken))
    s.check("and the public date picker does not strike the night it ends",
            end.isoformat() not in taken and (end - timedelta(days=1)).isoformat() in taken,
            detail=f"end {end.isoformat() in taken}, night before {(end - timedelta(days=1)).isoformat() in taken}")

    # One-day sittings keep their day: there is no night "before it ends".
    conn = db()
    try:
        day = w0 + timedelta(days=8)
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
               VALUES (?, ?, ?, 10, ?)""", (wid, day.isoformat(), day.isoformat(), now))
        conn.commit()
        one_day_id = conn.execute(
            "SELECT id FROM workshop_sessions WHERE workshop_id = ? AND start_date = ?",
            (wid, day.isoformat())).fetchone()["id"]
    finally:
        conn.close()
    ok, why = _ask(m.is_range_available, room["id"], day, day + timedelta(days=1))
    s.check("a one-day sitting still holds its day", not ok, detail=f"{ok} {why!r}")
    conn = db()
    conn.execute("DELETE FROM workshop_sessions WHERE id = ?", (one_day_id,))
    conn.commit()
    conn.close()

    s.section("The manage page offers the two nights, and they are sellable")
    page = anon.get(f"/workshops/manage/tok{TAG}a".lower())
    body = page.get_data(as_text=True)
    s.check("the manage page opens", page.status_code == 200, detail=str(page.status_code))
    before = re.search(r'href="[^"]*arrival=([\d-]+)&(?:amp;)?departure=([\d-]+)[^"]*">A night before', body)
    after = re.search(r'href="[^"]*arrival=([\d-]+)&(?:amp;)?departure=([\d-]+)[^"]*">A night after', body)
    s.check("it offers a night before, the night before the workshop starts",
            before and before.groups() == ((start - timedelta(days=1)).isoformat(), start.isoformat()),
            detail=str(before and before.groups()))
    s.check("and a night after, the night it ends",
            after and after.groups() == (end.isoformat(), (end + timedelta(days=1)).isoformat()),
            detail=str(after and after.groups()))
    s.check("not the fallback that leaves the guest to find the dates",
            "Choose your nights" not in body)
    if after:
        rooms_page = anon.get(f"/book?arrival={after.group(1)}&departure={after.group(2)}&party_size=2")
        text = rooms_page.get_data(as_text=True)
        s.check("and the rooms page, opened on the night after, does not refuse it for the workshop",
                rooms_page.status_code == 200 and "a workshop holds the whole" not in text,
                detail=str(rooms_page.status_code))

    s.section("On the channels, the workshop goes out with a day either side")
    conn = db()
    try:
        # A confirmed event, a live hold, a released hold and this room's own
        # closure, all clear of the workshop.
        e_day = w0 + timedelta(days=30)
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, end_date, status, created_at)
               VALUES (?, ?, 'wedding', ?, 'zzwsn.e@example.invalid', ?, ?, 'confirmed', ?)""",
            (f"{TAG}-WED", f"tok{TAG}wed".lower(), f"{TAG} Wed", e_day.isoformat(),
             (e_day + timedelta(days=1)).isoformat(), now))
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, status, created_at)
               VALUES (?, ?, 'photoshoot', ?, 'zzwsn.h@example.invalid', ?, 'new', ?)""",
            (f"{TAG}-ASK", f"tok{TAG}ask".lower(), f"{TAG} Ask", (e_day + timedelta(days=40)).isoformat(), now))
        ask_id = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                              (f"{TAG}-ASK",)).fetchone()["id"]
        h_day = w0 + timedelta(days=40)
        later = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute(
            """INSERT INTO event_holds (event_id, start_date, end_date, expires_at, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ask_id, h_day.isoformat(), (h_day + timedelta(days=2)).isoformat(), later, TAG, now))
        r_day = w0 + timedelta(days=50)
        conn.execute(
            """INSERT INTO event_holds (event_id, start_date, end_date, expires_at, note, created_at,
               released_at, released_reason) VALUES (?, ?, ?, ?, ?, ?, ?, 'decided')""",
            (ask_id, r_day.isoformat(), (r_day + timedelta(days=2)).isoformat(), later, TAG, now, now))
        c_day = w0 + timedelta(days=60)
        conn.execute(
            """INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (room["id"], c_day.isoformat(), (c_day + timedelta(days=3)).isoformat(), TAG, now))
        conn.commit()
    finally:
        conn.close()

    feed = anon.get(f"/ics/{room['export_token']}.ics")
    body = feed.get_data(as_text=True)
    events = _events(body)
    s.check("the feed answers", feed.status_code == 200, detail=str(feed.status_code))
    ws = [e for e in events if e[0] == f"ws-{sid}@gudanes-hr.local"]
    s.check("the workshop is in it, from the night before it starts to the morning after it ends",
            [(e[1], e[2]) for e in ws]
            == [((start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat())],
            detail=str(ws))
    s.check("closing the night it ends there, which this site sells",
            ws and ws[0][2] > end.isoformat(), detail=str(ws))
    s.check("and saying nothing of the workshop or its guests",
            ws and ws[0][3] == m.ical_escape("Closed — " + room["name"])
            and f"{TAG} Guest" not in body and "Gilding" not in body,
            detail=str(ws))
    house = [(e[1], e[2]) for e in events if (e[0] or "").startswith("house-")]
    s.check("a confirmed event closes both its days",
            (e_day.isoformat(), (e_day + timedelta(days=2)).isoformat()) in house, detail=str(house))
    s.check("a live hold closes all its days",
            (h_day.isoformat(), (h_day + timedelta(days=3)).isoformat()) in house, detail=str(house))
    s.check("a released hold closes nothing",
            not any(a <= r_day.isoformat() < b for a, b in house), detail=str(house))
    s.check("and an event or hold goes out without the workshop's buffer",
            not any(a == (e_day - timedelta(days=1)).isoformat() for a, b in house))
    s.check("the workshop is not sent twice, once with the buffer and once without",
            not any(a == start.isoformat() for a, b in house), detail=str(house))
    s.check("this room's own closure is still there",
            any(a == c_day.isoformat() and b == (c_day + timedelta(days=3)).isoformat()
                for _u, a, b, _s in events if (_u or "").startswith("closure-")),
            detail=str([e for e in events if (e[0] or "").startswith("closure-")]))

    s.section("And nothing more than a year ahead is for sale")
    today = m.house_today()
    win = [e for e in events if e[0] == "window@gudanes-hr.local"]
    s.check("one block from a year out to three years out",
            [(e[1], e[2], e[3]) for e in win]
            == [((today + timedelta(days=365)).isoformat(),
                 today.replace(year=today.year + 3).isoformat()
                 if not (today.month == 2 and today.day == 29)
                 else today.replace(year=today.year + 3, day=28).isoformat(),
                 "Not yet open")],
            detail=str(win))
    real_today = m.house_today
    try:
        m.house_today = lambda: today + timedelta(days=1)
        moved = _events(anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True))
    finally:
        m.house_today = real_today
    moved_win = [e for e in moved if e[0] == "window@gudanes-hr.local"]
    s.check("rebuilt on every request, so it moves forward with the days",
            moved_win and moved_win[0][1] == (today + timedelta(days=366)).isoformat(),
            detail=str(moved_win))

    s.section("Every room's feed carries the house-wide closures")
    conn = db()
    others = conn.execute("SELECT * FROM rooms WHERE id != ? AND export_token IS NOT NULL",
                          (room["id"],)).fetchall()
    conn.close()
    missing = []
    for other in others:
        ev = _events(anon.get(f"/ics/{other['export_token']}.ics").get_data(as_text=True))
        uids = {e[0] for e in ev}
        if f"ws-{sid}@gudanes-hr.local" not in uids or "window@gudanes-hr.local" not in uids:
            missing.append(other["name"])
        if any(a == c_day.isoformat() for u, a, _b, _s in ev if (u or "").startswith("closure-")):
            missing.append(other["name"] + " (has another room's closure)")
    s.check("the workshop and the window go out on every room, and closures stay with their room",
            others and not missing, detail=f"{missing} of {len(others)}")

    _cleanup()
    return s
