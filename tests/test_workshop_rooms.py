"""Putting atelier attendees into rooms without double-booking one.

workshop_room_conflict() is the only thing standing between two attendees and
the same bed, and it had no test. It is also the one place that deliberately
does NOT reuse is_range_available: that function makes a workshop block every
room, which is there to keep guest bookings off an atelier's dates and would,
if reused here, refuse to house the atelier's own attendees during their own
session. That inversion is exactly the kind of rule someone "simplifies" later.

So this pins both halves: the conflicts it must catch, and the assignment it
must keep allowing.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _room(label, sleeps=2):
    conn = db()
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night, sort_order)
           VALUES (?, ?, 1, ?, 200.0, 997)""",
        (f"{TAG} {label}", _harness.secrets_token(), sleeps))
    conn.commit()
    rid = conn.execute("SELECT id FROM rooms WHERE name = ?", (f"{TAG} {label}",)).fetchone()["id"]
    conn.close()
    return rid


def _session(start, end):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at) VALUES (?, '', 500, 10, 1, 97, ?)""",
        (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), end.isoformat(), f"{TAG} session", now))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} session",)).fetchone()["id"]
    conn.close()
    return sid


def _attendee(session_id, ref, party=2, room_id=None):
    conn = db()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           occupancy_type, reference_code, manage_token, assigned_room_id, created_at)
           VALUES (?, ?, ?, ?, 'confirmed', 'double', ?, ?, ?, ?)""",
        (session_id, f"{TAG} {ref}", f"{TAG.lower()}{ref}@example.invalid", party,
         f"{TAG}{ref}", f"tok{TAG}{ref}", room_id, _harness.datetime_now()))
    conn.commit()
    rid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    conn.close()
    return rid


def run():
    s = Suite("Workshop rooms")
    _cleanup()
    start = date.today() + timedelta(days=340)
    end = start + timedelta(days=4)
    sid = _session(start, end)
    room_a, room_b = _room("Room A"), _room("Room B")

    s.section("A free room can be assigned")
    first = _attendee(sid, "A1")
    conn = db()
    clash = m.workshop_room_conflict(conn, room_a, start, end, sid)
    conn.close()
    s.check("nothing objects to an unused room", clash is None, detail=f"got {clash!r}")

    s.section("The same room cannot go to two attendees")
    conn = db()
    conn.execute("UPDATE workshop_bookings SET assigned_room_id = ? WHERE id = ?", (room_a, first))
    conn.commit()
    clash = m.workshop_room_conflict(conn, room_a, start, end, sid)
    other_free = m.workshop_room_conflict(conn, room_b, start, end, sid)
    conn.close()
    s.check("a room already given out is refused", clash is not None, detail=f"got {clash!r}")
    s.check("and it says which kind of clash it is",
            clash and "another attendee" in clash, detail=f"got {clash!r}")
    s.check("a different room is still free", other_free is None, detail=f"got {other_free!r}")

    s.section("Re-saving the same attendee's own room is not a clash with itself")
    # Without the exclusion, editing anything else about a registration would
    # report the room they are already in as taken.
    conn = db()
    self_clash = m.workshop_room_conflict(conn, room_a, start, end, sid,
                                          exclude_registration_id=first)
    conn.close()
    s.check("their own assignment is excluded", self_clash is None, detail=f"got {self_clash!r}")

    s.section("A real guest booking in that room does block it")
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?)""",
        (room_b, f"{TAG}GB", f"tok{TAG}GB", f"{TAG} outside guest",
         (start + timedelta(days=1)).isoformat(), (start + timedelta(days=3)).isoformat(),
         _harness.datetime_now()))
    conn.commit()
    clash = m.workshop_room_conflict(conn, room_b, start, end, sid)
    conn.close()
    s.check("a guest booking over those nights blocks the room", clash is not None,
            detail=f"got {clash!r}")
    s.check("and says so", clash and "guest booking" in clash, detail=f"got {clash!r}")

    s.section("The session's own dates are assignable — the inversion that matters")
    # is_range_available makes a workshop block every room, to keep guest
    # bookings off its dates. Reusing it here would refuse to house the
    # atelier's own attendees for the whole of their own session.
    conn = db()
    blocked_for_guests, _ = m.is_range_available(conn, room_a, start, end)
    assignable = m.workshop_room_conflict(conn, room_a, start, end, sid,
                                          exclude_registration_id=first)
    conn.close()
    s.check("a guest cannot book that room over the session", not blocked_for_guests)
    s.check("but an attendee of that session can be put in it", assignable is None,
            detail=f"got {assignable!r}")

    s.section("Boundaries: the day either side")
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    # A stay that checks out on the session's first morning does not overlap it.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?)""",
        (room_b, f"{TAG}GB2", f"tok{TAG}GB2", f"{TAG} early guest",
         (start - timedelta(days=2)).isoformat(), start.isoformat(), _harness.datetime_now()))
    conn.commit()
    leaves_that_morning = m.workshop_room_conflict(conn, room_b, start, end, sid)
    conn.close()
    s.check("a stay ending the morning the session starts does not block the room",
            leaves_that_morning is None, detail=f"got {leaves_that_morning!r}")

    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    # ...but one arriving on the session's last day does: the attendees are
    # still in the house that night.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?)""",
        (room_b, f"{TAG}GB3", f"tok{TAG}GB3", f"{TAG} late guest",
         end.isoformat(), (end + timedelta(days=2)).isoformat(), _harness.datetime_now()))
    conn.commit()
    arrives_last_day = m.workshop_room_conflict(conn, room_b, start, end, sid)
    conn.close()
    s.check("a stay starting on the session's last day does block it",
            arrives_last_day is not None, detail=f"got {arrives_last_day!r}")

    s.section("Through the actual admin form")
    oc, ec, owner, emp = clients()
    _cleanup()
    sid = _session(start, end)
    room_a, room_b = _room("Room A"), _room("Room B", sleeps=2)
    one = _attendee(sid, "F1", party=2)
    two = _attendee(sid, "F2", party=2)
    oc.post(f"/admin/workshops/registrations/{one}/assign-room",
            data={"room_id": str(room_a)}, follow_redirects=True)
    r = oc.post(f"/admin/workshops/registrations/{two}/assign-room",
                data={"room_id": str(room_a)}, follow_redirects=True)
    conn = db()
    got_one = conn.execute("SELECT assigned_room_id FROM workshop_bookings WHERE id = ?",
                           (one,)).fetchone()["assigned_room_id"]
    got_two = conn.execute("SELECT assigned_room_id FROM workshop_bookings WHERE id = ?",
                           (two,)).fetchone()["assigned_room_id"]
    conn.close()
    s.check("the first attendee gets the room", got_one == room_a, detail=f"got {got_one!r}")
    s.check("the second is refused it rather than sharing a bed", got_two is None,
            detail=f"got {got_two!r}, flashes {_harness.flashes(r)[:1]}")

    s.section("A room too small is refused")
    big_party = _attendee(sid, "F3", party=4)     # rooms above sleep 2
    r = oc.post(f"/admin/workshops/registrations/{big_party}/assign-room",
                data={"room_id": str(room_b)}, follow_redirects=True)
    conn = db()
    got = conn.execute("SELECT assigned_room_id FROM workshop_bookings WHERE id = ?",
                       (big_party,)).fetchone()["assigned_room_id"]
    conn.close()
    s.check("a party of four is not put in a room that sleeps two", got is None,
            detail=f"got {got!r}, flashes {_harness.flashes(r)[:1]}")

    s.section("Clearing an assignment always works")
    r = oc.post(f"/admin/workshops/registrations/{one}/assign-room",
                data={"room_id": ""}, follow_redirects=True)
    conn = db()
    cleared = conn.execute("SELECT assigned_room_id FROM workshop_bookings WHERE id = ?",
                           (one,)).fetchone()["assigned_room_id"]
    conn.close()
    s.check("the room can be taken back off them", cleared is None, detail=f"got {cleared!r}")

    _cleanup()
    return s
