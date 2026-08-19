"""Which nights the booking calendar shows as unavailable.

The server has always refused a stay that runs through a workshop or someone
else's booking — but the guest's date boxes were plain <input type="date">
with no idea any of that existed, so every date was selectable and the refusal
only arrived after the whole form was filled in.

unavailable_nights() is what the calendar greys out. It has to agree with
is_range_available(), which is still the actual gate, and the two do NOT share
one boundary convention:

  - a booking holds arrival..departure-1, because the checkout morning is free
  - a workshop holds start..end INCLUSIVE, because the guests are still in the
    house on the end date

Getting either boundary wrong is invisible on the page and expensive: one way
sells a night that is taken, the other refuses a night that is free.
"""
from datetime import date, timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZCAL"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _room():
    conn = db()
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night,
           sort_order, min_nights) VALUES (?, ?, 1, 4, 200.0, 990, 1)""",
        (f"{TAG} Room", _harness.secrets_token()))
    conn.commit()
    row = conn.execute("SELECT id FROM rooms WHERE name = ?", (f"{TAG} Room",)).fetchone()
    conn.close()
    return row["id"]


def _clear_base(room_id, span=40):
    """A start date with `span` days of genuinely free nights after it.

    A hardcoded offset does not survive contact with the calendar: the real
    seeded ateliers move, and every day that passes slides a fixed offset
    along until it lands on one. This test previously picked today+300 and
    went red the morning that reached Cooking in the Cuisine 2027 — the same
    trap that had already been fixed once in test_workflows.
    """
    conn = db()
    try:
        for offset in range(200, 900, 10):
            start = date.today() + timedelta(days=offset)
            taken = m.unavailable_nights(conn, room_id, start - timedelta(days=15),
                                         start + timedelta(days=span + 15))
            if not taken:
                return start
    finally:
        conn.close()
    raise RuntimeError("no clear window in the next two and a half years")


def run():
    s = Suite("Availability calendar")
    _cleanup()
    room_id = _room()
    # Asked for rather than assumed — see _clear_base.
    base = _clear_base(room_id)
    window_start, window_end = base - timedelta(days=10), base + timedelta(days=40)

    s.section("A booking holds its nights, but frees the checkout morning")
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?)""",
        (room_id, f"{TAG}B", f"tok{TAG}B", f"{TAG} guest",
         base.isoformat(), (base + timedelta(days=3)).isoformat(), _harness.datetime_now()))
    conn.commit()
    nights = m.unavailable_nights(conn, room_id, window_start, window_end)
    conn.close()
    held = [base + timedelta(days=i) for i in range(3)]
    s.check("the three nights of a 3-night stay are held",
            all(d.isoformat() in nights for d in held),
            detail=f"got {[d.isoformat() for d in held if d.isoformat() not in nights]} missing")
    s.check("the departure day is NOT held — the next guest can arrive that morning",
            (base + timedelta(days=3)).isoformat() not in nights,
            detail=f"got {nights.get((base + timedelta(days=3)).isoformat())!r}")
    s.check("the night before arrival is free",
            (base - timedelta(days=1)).isoformat() not in nights)

    s.section("A workshop holds the whole house, end date included")
    _cleanup()
    room_id = _room()
    w_start, w_end = base + timedelta(days=10), base + timedelta(days=14)
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at) VALUES (?, '', 100, 10, 1, 90, ?)""",
        (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, w_start.isoformat(), w_end.isoformat(), f"{TAG} session", now))
    conn.commit()
    nights = m.unavailable_nights(conn, room_id, window_start, window_end)
    conn.close()
    span = [(w_start + timedelta(days=i)) for i in range((w_end - w_start).days + 1)]
    s.check(f"all {len(span)} days of the session are held, end date included",
            all(d.isoformat() in nights for d in span),
            detail=f"missing {[d.isoformat() for d in span if d.isoformat() not in nights]}")
    s.check("the reason names the atelier, so the guest knows why",
            "Atelier" in nights.get(w_start.isoformat(), ""),
            detail=f"got {nights.get(w_start.isoformat())!r}")
    s.check("the day before the session is still free",
            (w_start - timedelta(days=1)).isoformat() not in nights)

    s.section("A held night can still be a checkout day")
    # Found by clicking the real calendar: the server allows departing on the
    # morning an atelier starts (that night is theirs, not ours), but the
    # calendar had the day disabled outright, so it could not be picked — one
    # bookable night quietly lost in front of every session. A night being
    # unavailable and a DAY being an invalid departure are different claims.
    conn = db()
    ok_checkout, _ = m.is_range_available(conn, room_id, w_start - timedelta(days=2), w_start)
    ok_arrive, _ = m.is_range_available(conn, room_id, w_start, w_start + timedelta(days=2))
    conn.close()
    s.check("the session's first day is held as a night", w_start.isoformat() in nights)
    s.check("yet leaving on that morning is allowed", ok_checkout,
            detail="the server refused a checkout on the session's start date")
    s.check("while arriving that day is not", not ok_arrive)

    s.section("The calendar and the booking gate agree")
    # The check that matters: anything the calendar leaves clickable must
    # actually be bookable, and anything it greys out must actually be refused.
    conn = db()
    disagreements = []
    for offset in range(0, 30):
        day = base + timedelta(days=offset)
        greyed = day.isoformat() in nights
        ok, _ = m.is_range_available(conn, room_id, day, day + timedelta(days=1))
        if greyed == ok:                      # greyed but bookable, or free but refused
            disagreements.append(f"{day.isoformat()} greyed={greyed} bookable={ok}")
    conn.close()
    s.check("over 30 nights, every greyed-out night is refused and every clickable one is free",
            not disagreements, detail=" | ".join(disagreements[:4]))

    s.section("A full house is unavailable even in an untouched room")
    _cleanup()
    room_id = _room()
    conn = db()
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night,
           sort_order, min_nights) VALUES (?, ?, 1, 20, 200.0, 991, 1)""",
        (f"{TAG} Other", _harness.secrets_token()))
    conn.commit()
    other = conn.execute("SELECT id FROM rooms WHERE name = ?", (f"{TAG} Other",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 15, 'confirmed', ?)""",
        (other, f"{TAG}Full", f"tok{TAG}Full", f"{TAG} full house", base.isoformat(),
         (base + timedelta(days=2)).isoformat(), _harness.datetime_now()))
    conn.commit()
    nights = m.unavailable_nights(conn, room_id, window_start, window_end)
    conn.close()
    s.check("15 guests in another room makes those nights unavailable here too",
            base.isoformat() in nights, detail=f"got {nights.get(base.isoformat())!r}")
    s.check("and it says the château is full rather than blaming this room",
            "full" in (nights.get(base.isoformat(), "")).lower(),
            detail=f"got {nights.get(base.isoformat())!r}")

    s.section("The endpoint the calendar actually calls")
    pub = m.app.test_client()
    r = pub.get(f"/api/availability/{room_id}?months=6")
    data = r.get_json() if r.status_code == 200 else {}
    s.check("it answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("with the nights to grey out", isinstance(data.get("unavailable"), dict),
            detail=f"got {type(data.get('unavailable')).__name__}")
    s.check("and the room's minimum stay, so the form can enforce it",
            data.get("min_nights") is not None)
    # Against the app's own "today", which is UTC everywhere in this codebase
    # (see the past-arrival guard in book_room). Comparing to a local date
    # fails on any machine east of Greenwich for part of each day.
    app_today = m.datetime.now(m.timezone.utc).date().isoformat()
    s.check("it never offers the past", data.get("first", "") >= app_today,
            detail=f"got {data.get('first')!r}, app's today is {app_today}")
    r404 = pub.get("/api/availability/99999")
    s.check("an unknown room is a clean 404", r404.status_code == 404,
            detail=f"HTTP {r404.status_code}")

    _cleanup()
    return s
