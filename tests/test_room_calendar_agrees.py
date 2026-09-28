"""The room page's calendar greys exactly what the booking check refuses.

unavailable_nights() is the calendar on the room page: it greys out the
nights a guest cannot have, one at a time, so they are not left to fill in
the whole form and be refused at the end. is_range_available() is the gate
that actually refuses. The calendar used to keep its own list of what closes
a night, and two entries on it had gone stale while the gate moved on:

  A CONFIRMED EVENT HELD ITS FIRST DAY ONLY. Events grew an end_date, and the
  gate holds preferred_date..end_date INCLUSIVE, reading an end before the
  start as the single start day. The calendar still held preferred_date on
  its own, so a two-day wedding greyed day one and left day two clickable.

  A PROVISIONAL HOLD WAS INVISIBLE. The gate refuses dates promised while
  somebody decides (released_at IS NULL and expires_at still ahead). The
  calendar had never heard of event_holds, so it greyed nothing under one.

Both were refused only when the guest submitted the form. The calendar now
reads the same picture the gate does, so the checks here ask the two the
same question night by night rather than trusting that they share code.

What carries this file:

  EVERY SOURCE, ITS OWN BOUNDARY. A booking, a channel stay and the owner's
  block end on the checkout morning; a workshop, an event and a hold hold
  their last day too. Each is laid down side by side with the night after
  it, which must stay free.

  A HOLD THAT HAS ENDED HOLDS NOTHING. Released or lapsed, it must not grey
  a night. If it did, the sweep below would still agree -- both would be
  wrong together -- so these are checked on their own.

  THE CALENDAR'S WORDS STAY THE CALENDAR'S. "Already booked", "Held for a
  private wedding": the guest reads these in a tooltip, and a rewrite that
  handed them the gate's full sentence instead would pass every agreement
  check while changing what they are told.

  AND THE HOUSE CEILING IS STILL ADDED ON TOP, because the gate leaves that
  to the booking forms and the calendar is where a guest learns of it first.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZAGREE"
ONE = timedelta(days=1)


def _cleanup():
    conn = db()
    rooms = [r["id"] for r in conn.execute(
        "SELECT id FROM rooms WHERE name LIKE ?", (TAG + "%",)).fetchall()]
    if rooms:
        marks = ",".join("?" * len(rooms))
        conn.execute(f"DELETE FROM blocked_dates WHERE room_id IN ({marks})", rooms)
        conn.execute(f"DELETE FROM room_blocks WHERE room_id IN ({marks})", rooms)
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM event_holds WHERE event_id IN
                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_sessions WHERE workshop_id IN
                    (SELECT id FROM workshops WHERE title LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _leftovers():
    """Every row this suite writes, counted by the tag it carries."""
    conn = db()
    try:
        like = TAG + "%"
        return {
            "rooms": conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE name LIKE ?",
                                  (like,)).fetchone()["c"],
            "bookings": conn.execute("SELECT COUNT(*) AS c FROM bookings "
                                     "WHERE guest_name LIKE ?", (like,)).fetchone()["c"],
            "events": conn.execute("SELECT COUNT(*) AS c FROM event_inquiries "
                                   "WHERE contact_name LIKE ?", (like,)).fetchone()["c"],
            "holds": conn.execute("""SELECT COUNT(*) AS c FROM event_holds
                                      WHERE note LIKE ?""", (like,)).fetchone()["c"],
            "workshops": conn.execute("SELECT COUNT(*) AS c FROM workshops "
                                      "WHERE title LIKE ?", (like,)).fetchone()["c"],
            "room_blocks": conn.execute("SELECT COUNT(*) AS c FROM room_blocks "
                                        "WHERE reason LIKE ?", (like,)).fetchone()["c"],
        }
    finally:
        conn.close()


def _room(conn, suffix, occupancy=4, sort_order=992):
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy,
           price_per_night, sort_order, min_nights) VALUES (?, ?, 1, ?, 200.0, ?, 1)""",
        (f"{TAG} {suffix}", _harness.secrets_token(), occupancy, sort_order))
    conn.commit()
    return conn.execute("SELECT id FROM rooms WHERE name = ?",
                        (f"{TAG} {suffix}",)).fetchone()["id"]


def run():
    s = Suite("The room calendar greys what the booking check refuses")
    _cleanup()
    try:
        _run(s)
    finally:
        # Tagged and gone, after a failure too: the database is shared by every
        # suite in the run, and a confirmed wedding left behind closes the
        # whole house for somebody else's test.
        _cleanup()
    left = {k: v for k, v in _leftovers().items() if v}
    s.check("nothing this suite wrote is left behind", not left, detail=str(left))
    return s


def _run(s):
    conn = db()
    try:
        _checks(s, conn)
    finally:
        conn.close()


def _checks(s, conn):
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    room_id = _room(conn, "Room")

    # A window the gate itself says is clear for this room -- asked, not an
    # offset, because the seeded ateliers sit on real dates and move with the
    # calendar. Two nights of margin either side, so the "night before"
    # checks have somewhere free to look.
    base = _harness.free_window(room_id, 60, after_days=200,
                                clear_of_ateliers=True) + 2 * ONE
    first, last = base - 2 * ONE, base + 56 * ONE

    def d(offset):
        return base + offset * ONE

    def nights():
        return m.unavailable_nights(conn, room_id, first, last)

    if not s.check("the window starts with nothing greyed", not nights(),
                   detail=str(sorted(nights().items())[:4])):
        return

    counter = [0]

    def event(start, end, status="confirmed", kind="wedding"):
        counter[0] += 1
        k = counter[0]
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
               contact_name, contact_email, preferred_date, end_date, status, created_at)
               VALUES (?, ?, ?, ?, 'zzagree@example.invalid', ?, ?, ?, ?)""",
            (f"{TAG}-EV{k}", f"tok{TAG}EV{k}", kind, f"{TAG} Event {k}",
             start.isoformat(), end.isoformat() if end else None, status, stamp))
        conn.commit()
        return conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                            (f"{TAG}-EV{k}",)).fetchone()["id"]

    def hold(event_id, start, end, *, expires, released=None):
        conn.execute(
            """INSERT INTO event_holds (event_id, start_date, end_date, expires_at,
               note, created_at, released_at, released_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, start.isoformat(), end.isoformat(), expires.isoformat(),
             TAG, stamp, released.isoformat() if released else None,
             "decided" if released else None))
        conn.commit()

    # -- every source, laid down side by side ---------------------------------
    # Each is followed by the night that must stay free, so a boundary that
    # slips by one day in either direction has a night to show it on.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?)""",
        (room_id, f"{TAG}-B", f"tok{TAG}B", f"{TAG} Guest", d(2).isoformat(),
         d(4).isoformat(), stamp))
    conn.execute("INSERT INTO blocked_dates (room_id, start_date, end_date) VALUES (?, ?, ?)",
                 (room_id, d(6).isoformat(), d(8).isoformat()))
    conn.execute(
        """INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (room_id, d(10).isoformat(), d(12).isoformat(), TAG + " plaster", stamp))
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at) VALUES (?, '', 100, 10, 1, 90, ?)""",
        (f"{TAG} Atelier", stamp))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (f"{TAG} Atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
           notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, d(14).isoformat(), d(16).isoformat(), TAG, stamp))
    conn.commit()

    event(d(20), d(21))                         # a two-day wedding
    event(d(26), d(24))                         # an end typed before its start
    event(d(30), None)                          # no end at all
    asking = event(d(34), None, status="new", kind="photoshoot")
    hold(asking, d(34), d(36), expires=now + timedelta(days=7))
    hold(asking, d(40), d(42), expires=now + timedelta(days=7),
         released=now - timedelta(hours=1))
    hold(asking, d(44), d(46), expires=now - timedelta(hours=1))

    got = nights()

    def greyed(*offsets):
        return [d(o).isoformat() for o in offsets if d(o).isoformat() not in got]

    def clear(*offsets):
        return [f"{d(o).isoformat()}={got[d(o).isoformat()]!r}"
                for o in offsets if d(o).isoformat() in got]

    s.section("A confirmed event is greyed for every day it runs")
    s.check("both days of a two-day wedding are greyed", not greyed(20, 21),
            detail=f"not greyed: {greyed(20, 21)} -- the second day was clickable, "
                   "and the guest found out only when the form was refused")
    s.check("and neither the day before nor the day after", not clear(19, 22),
            detail=str(clear(19, 22)))
    s.check("an end typed before the start is read as the single start day",
            not greyed(26) and not clear(24, 25, 27),
            detail=f"greyed missing {greyed(26)}, wrongly greyed {clear(24, 25, 27)}")
    s.check("and an event with no end holds its one day", not greyed(30)
            and not clear(29, 31), detail=f"{greyed(30)} {clear(29, 31)}")

    s.section("A provisional hold is greyed while it is live")
    s.check("every night of a live hold is greyed, its last day included",
            not greyed(34, 35, 36),
            detail=f"not greyed: {greyed(34, 35, 36)} -- the gate refuses these "
                   "and the calendar offered them")
    s.check("and the morning after it is free", not clear(37), detail=str(clear(37)))
    # Checked on their own, not left to the sweep below: a picture that forgot
    # released_at would grey these in the calendar AND refuse them at the
    # gate, and two wrong answers agree perfectly.
    s.check("a hold somebody has released greys nothing", not clear(40, 41, 42),
            detail=str(clear(40, 41, 42)))
    s.check("nor one whose time has run out", not clear(44, 45, 46),
            detail=str(clear(44, 45, 46)))

    s.section("Every other source keeps its own boundary")
    s.check("a booking holds its nights and frees the checkout morning",
            not greyed(2, 3) and not clear(4), detail=f"{greyed(2, 3)} {clear(4)}")
    s.check("so does a stay on another channel",
            not greyed(6, 7) and not clear(8), detail=f"{greyed(6, 7)} {clear(8)}")
    s.check("and the owner's own block on the room",
            not greyed(10, 11) and not clear(12), detail=f"{greyed(10, 11)} {clear(12)}")
    s.check("a workshop holds its last day as well",
            not greyed(14, 15, 16) and not clear(13, 17),
            detail=f"{greyed(14, 15, 16)} {clear(13, 17)}")

    s.section("The calendar keeps its own words")
    words = {
        2: "Already booked",
        6: "Booked on another channel",
        10: "Not available",
        14: f"Held for {TAG} Atelier",
        21: "Held for a private wedding",
        26: "Held for a private wedding",
        35: "Provisionally held for a private photoshoot",
    }
    wrong = [f"{d(o).isoformat()}: {got.get(d(o).isoformat())!r} (wanted {w!r})"
             for o, w in words.items() if got.get(d(o).isoformat()) != w]
    s.check("each night says why in the calendar's words, not the gate's sentence",
            not wrong, detail=" | ".join(wrong[:3]))

    s.section("The calendar and the booking check agree, night by night")
    # The check that matters. Anything the calendar leaves clickable must be
    # bookable, and anything it greys must be refused -- asked of the real
    # gate for every night in the window, not reasoned about.
    disagree = []
    day = first
    while day < last:
        ok, why = m.is_range_available(conn, room_id, day, day + ONE)
        if (day.isoformat() in got) == ok:
            disagree.append(f"{day.isoformat()} calendar={got.get(day.isoformat(), 'clickable')!r}"
                            f" gate={'free' if ok else why!r}")
        day += ONE
    s.check(f"over {(last - first).days} nights, the calendar greys exactly the nights "
            "the gate refuses", not disagree, detail=" | ".join(disagree[:3]))

    # What the owner's reports make of it. sellable_nights takes a night out
    # of what could have been sold when the calendar says a guest could not
    # have had it, so the stale calendar counted day two of a wedding, and a
    # held week, as nights the house failed to fill.
    s.check("the two days of the wedding are not counted as nights the house could sell",
            m.sellable_nights(conn, d(20), d(22), room_id=room_id)["nights"] == 0,
            detail=str(m.sellable_nights(conn, d(20), d(22), room_id=room_id)))
    s.check("nor are the nights under a live hold",
            m.sellable_nights(conn, d(34), d(37), room_id=room_id)["nights"] == 0,
            detail=str(m.sellable_nights(conn, d(34), d(37), room_id=room_id)))

    s.section("The page gets it through the endpoint it actually calls")
    since = datetime.now(timezone.utc).isoformat()
    r = m.app.test_client().get(
        f"/api/availability/{room_id}?from={first.isoformat()}&months=2")
    # The rate limiter logs every call. That row is ours too.
    conn.execute("DELETE FROM submission_log WHERE action = 'api_availability' "
                 "AND created_at >= ?", (since,))
    conn.commit()
    served = (r.get_json() or {}).get("unavailable", {}) if r.status_code == 200 else {}
    s.check("it answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("with the wedding's second day and the held nights in it",
            all(d(o).isoformat() in served for o in (21, 34, 35, 36)),
            detail=str([d(o).isoformat() for o in (21, 34, 35, 36)
                        if d(o).isoformat() not in served]))
    s.check("and without the released hold",
            not any(d(o).isoformat() in served for o in (40, 41, 42)))

    s.section("A full house is still greyed, on top of all that")
    # The ceiling is not the gate's -- is_range_available leaves it to the
    # booking forms -- so it is laid down only after the sweep, and it is the
    # one thing the calendar adds of its own.
    cap = m.house_guest_capacity(conn)
    other = _room(conn, "Hall", occupancy=max(cap, 4), sort_order=993)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, ?, 'confirmed', ?)""",
        (other, f"{TAG}-FULL", f"tok{TAG}FULL", f"{TAG} Full House",
         d(50).isoformat(), d(51).isoformat(), cap, stamp))
    conn.commit()
    got = nights()
    s.check(f"{cap} guests in another room greys the night in this one",
            got.get(d(50).isoformat()) == "The château is full",
            detail=f"got {got.get(d(50).isoformat())!r}")
    s.check("and only that night", not clear(49, 51), detail=str(clear(49, 51)))
