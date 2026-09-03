"""The château's legal ceiling on how many guests may be in the house at once.

15 guests, in any mix of room bookings and an atelier — not per room, not per
session. Room bookings and workshop registrations are stored in different
tables with different column names, so nothing before this summed them
together, and a workshop and a handful of rooms filling up on the same dates
could quietly add up to more people than the license allows.

Only the public booking forms are capped. A member of staff confirming a
request, editing one, or entering a booking by hand is trusted to know why a
given night is going over — the law binds what the site offers a stranger,
not what the owner's own staff can do.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZHC"


def _set_capacity(value):
    conn = db()
    conn.execute(
        """INSERT INTO app_settings (key, value) VALUES ('house_guest_capacity', ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (str(value),))
    conn.commit()
    conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))  # cascades any leftover booking
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE '%" + TAG + "%'")
    conn.commit()
    _set_capacity(15)  # restore the real default, whatever a check left it at
    conn.close()


def _make_second_room():
    """A room distinct from the one _harness.ensure_room() returns, so a test
    can put guests in the house without occupying the same room the public
    form is being posted to — otherwise a room-overlap refusal and a
    capacity refusal would be indistinguishable."""
    conn = db()
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night, sort_order)
           VALUES (?, ?, 1, 14, 250.0, 999)""", (f"{TAG} Room B", _harness.secrets_token()))
    conn.commit()
    row = conn.execute("SELECT id, name FROM rooms WHERE name = ?", (f"{TAG} Room B",)).fetchone()
    conn.close()
    return row


def _make_room_booking(name, arrival, departure, party_size, status="confirmed", room=None):
    conn = db()
    room = room or _harness.ensure_room()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           guest_phone, arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)""",
        (room["id"], f"{TAG}{name}", f"tok{TAG}{name}", name, f"{name.lower()}@example.invalid",
         arrival.isoformat(), departure.isoformat(), party_size, status, _harness.datetime_now()))
    conn.commit()
    conn.close()


def _make_workshop_and_booking(name, start, end, party_size, capacity=15, status="confirmed"):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at) VALUES (?, 'Test', 500, ?, 1, 50, ?)""",
        (f"{TAG} {name}", capacity, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (wid, start.isoformat(), end.isoformat(), capacity, f"{TAG} {name}", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} {name}", f"{TAG.lower()}{name.lower()}@example.invalid", party_size, status,
         f"{TAG}{name}", f"tok{TAG}{name}", now))
    conn.commit()
    conn.close()
    return sid, wid


def run():
    s = Suite("House capacity")
    clients()
    _cleanup()
    start = house_today() + timedelta(days=200)  # clear of the real ateliers

    s.section("Room bookings alone")
    _make_room_booking(f"{TAG}A", start, start + timedelta(days=3), 10)
    conn = db()
    peak = m.peak_guests_in_house(conn, start, start + timedelta(days=3))
    conn.close()
    s.check("ten guests in one room booking counts as ten", peak == 10, detail=f"got {peak}")

    s.section("Rooms and an atelier together, the case nothing summed before")
    _make_workshop_and_booking("Retreat", start, start + timedelta(days=2), 8)
    conn = db()
    peak = m.peak_guests_in_house(conn, start, start + timedelta(days=3))
    conn.close()
    s.check("10 room guests + 8 atelier guests peak at 18, not 10 or 8",
            peak == 18, detail=f"got {peak}")

    s.section("Nights outside either booking are unaffected")
    conn = db()
    before = m.peak_guests_in_house(conn, start - timedelta(days=5), start - timedelta(days=2))
    after = m.peak_guests_in_house(conn, start + timedelta(days=10), start + timedelta(days=12))
    conn.close()
    s.check("a night nobody is booked shows nobody in the house",
            before == 0 and after == 0, detail=f"got {before}, {after}")

    s.section("Pending counts the same as confirmed")
    _cleanup()
    _make_room_booking(f"{TAG}Pend", start, start + timedelta(days=2), 6, status="pending")
    conn = db()
    peak = m.peak_guests_in_house(conn, start, start + timedelta(days=2))
    conn.close()
    s.check("a pending request already claims the capacity", peak == 6, detail=f"got {peak}")

    s.section("Declined and cancelled do not count")
    _cleanup()
    _make_room_booking(f"{TAG}Dead", start, start + timedelta(days=2), 12, status="declined")
    conn = db()
    peak = m.peak_guests_in_house(conn, start, start + timedelta(days=2))
    conn.close()
    s.check("a declined booking has released whatever it held", peak == 0, detail=f"got {peak}")

    s.section("house_capacity_error, the guest-facing gate")
    _cleanup()
    _set_capacity(15)
    _make_room_booking(f"{TAG}Base", start, start + timedelta(days=3), 10)
    conn = db()
    fits = m.house_capacity_error(conn, start, start + timedelta(days=3), 5)
    over = m.house_capacity_error(conn, start, start + timedelta(days=3), 6)
    conn.close()
    s.check("10 already in, 5 more exactly fills 15", fits is None, detail=f"got {fits!r}")
    s.check("10 already in, 6 more is refused", over is not None, detail=f"got {over!r}")
    s.check("the refusal names the real numbers", over and "10" in over and "15" in over,
            detail=f"got {over!r}")

    s.section("The setting is respected, not the number 15 hardcoded")
    _set_capacity(4)
    conn = db()
    tight = m.house_capacity_error(conn, start + timedelta(days=50), start + timedelta(days=52), 5)
    conn.close()
    s.check("a lowered cap is enforced", tight is not None, detail=f"got {tight!r}")
    _set_capacity(15)

    s.section("The public room-booking form refuses over the cap")
    _cleanup()
    _set_capacity(15)
    # The existing 12 are put in a second room, not the one the form below
    # posts to — otherwise a same-room date overlap would refuse the request
    # too, and the test couldn't tell which reason actually fired.
    room_b = _make_second_room()
    _make_room_booking(f"{TAG}Full", start, start + timedelta(days=3), 12, room=room_b)
    # Asked for by size rather than taken on trust. The room's own limit is
    # not what is tested here, so it has to be big enough to stay out of the
    # way -- and the first active room is live catalogue data that has already
    # changed once under this line.
    room = _harness.ensure_room(min_occupancy=4)
    pub = m.app.test_client()
    r = pub.post(f"/book/{room['id']}", data={
        "guest_name": f"{TAG} overflow", "guest_email": f"{TAG.lower()}o@example.invalid",
        "arrival_date": start.isoformat(), "departure_date": (start + timedelta(days=3)).isoformat(),
        "party_size": "4", "agree_terms": "on",
    })
    conn = db()
    created = conn.execute("SELECT 1 FROM bookings WHERE guest_name = ?", (f"{TAG} overflow",)).fetchone()
    conn.close()
    s.check("12 already in the house, 4 more (16 total) is refused before a row is written",
            created is None, detail=f"HTTP {r.status_code}, {_harness.flashes(r)[:1]}")
    r2 = pub.post(f"/book/{room['id']}", data={
        "guest_name": f"{TAG} fits", "guest_email": f"{TAG.lower()}i@example.invalid",
        "arrival_date": start.isoformat(), "departure_date": (start + timedelta(days=3)).isoformat(),
        "party_size": "3", "agree_terms": "on",
    })
    conn = db()
    fits_row = conn.execute("SELECT 1 FROM bookings WHERE guest_name = ?", (f"{TAG} fits",)).fetchone()
    conn.close()
    s.check("12 already in, 3 more (15 total) fits exactly and goes through",
            fits_row is not None, detail=f"HTTP {r2.status_code}, {_harness.flashes(r2)[:1]}")

    s.section("The public workshop-registration form refuses over the cap")
    _cleanup()
    _set_capacity(15)
    sid, wid = _make_workshop_and_booking("Full", start, start + timedelta(days=2), 12)
    r = pub.post(f"/workshops/register/{sid}", data={
        "guest_name": f"{TAG} ws overflow", "guest_email": f"{TAG.lower()}wso@example.invalid",
        "party_size": "5", "notes": "",
    })
    conn = db()
    created = conn.execute(
        "SELECT 1 FROM workshop_bookings WHERE guest_name = ?", (f"{TAG} ws overflow",)).fetchone()
    conn.close()
    s.check("12 already registered, 5 more is refused before a row is written",
            created is None, detail=f"HTTP {r.status_code}, {_harness.flashes(r)[:1]}")

    s.section("A room booking and an atelier registration are summed on the same dates")
    _cleanup()
    _set_capacity(15)
    _make_room_booking(f"{TAG}Mix", start, start + timedelta(days=2), 8)
    sid, wid = _make_workshop_and_booking("Mix", start, start + timedelta(days=1), 4, capacity=20)
    r = pub.post(f"/workshops/register/{sid}", data={
        "guest_name": f"{TAG} mix overflow", "guest_email": f"{TAG.lower()}mo@example.invalid",
        "party_size": "4", "notes": "",
    })
    conn = db()
    created = conn.execute(
        "SELECT 1 FROM workshop_bookings WHERE guest_name = ?", (f"{TAG} mix overflow",)).fetchone()
    conn.close()
    # 8 in a room + 4 already registered = 12; the session's own capacity is 20
    # and would happily allow 4 more, but the house-wide 15 must not.
    s.check("blocked by the house total even though the session has room left",
            created is None, detail=f"HTTP {r.status_code}, {_harness.flashes(r)[:1]}")

    s.section("Staff are not capped — confirming, editing, or entering by hand")
    _cleanup()
    _set_capacity(15)
    oc, ec, owner, emp = clients()
    _make_room_booking(f"{TAG}StaffA", start, start + timedelta(days=2), 14, status="pending")
    conn = db()
    pending_id = conn.execute(
        "SELECT id FROM bookings WHERE guest_name = ?", (f"{TAG}StaffA",)).fetchone()["id"]
    conn.close()
    # A second pending request for the same dates, well over the cap on its own,
    # that only a human confirming should be able to wave through.
    _make_room_booking(f"{TAG}StaffB", start, start + timedelta(days=2), 10, status="pending")
    conn = db()
    over_id = conn.execute(
        "SELECT id FROM bookings WHERE guest_name = ?", (f"{TAG}StaffB",)).fetchone()["id"]
    conn.close()
    r = oc.post(f"/admin/bookings/{over_id}/confirm", follow_redirects=True)
    conn = db()
    status = conn.execute("SELECT status FROM bookings WHERE id = ?", (over_id,)).fetchone()["status"]
    conn.close()
    s.check("staff can confirm a booking that pushes the house over the legal cap",
            status == "confirmed", detail=f"got status={status!r}, HTTP {r.status_code}")

    _cleanup()
    return s
