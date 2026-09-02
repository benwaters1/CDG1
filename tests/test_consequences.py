"""Facts that only matter because of another fact.

The app already knew a room had a broken shower AND that a guest arrives into
it on Friday. It held both and connected neither, so the fault sat in a list
looking like every other fault. Same shape as the vehicle that is low on fuel
with an airport run at six: the flag is not the point, the collision is.

Each check below sets up the collision AND the harmless version of the same
thing, because a rule that fires on everything is no better than one that
fires on nothing.
"""
from datetime import date, datetime, timezone, timedelta

from _harness import Suite, clients, db, datetime_now, ensure_employee, ensure_room
import _harness

m = _harness.m
TAG = "conseq-"


def _iso(days):
    return (m.house_today() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR title LIKE ?", (TAG + "%", "%" + TAG + "%"))
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM certifications WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Consequences")
    oc, _ec, owner, _e = clients()
    staff = ensure_employee()
    conn = db()
    now = datetime_now()
    _cleanup(conn)

    rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 2").fetchall()
    if len(rooms) < 2:
        ensure_room()
        conn.execute("INSERT INTO rooms (name, export_token, active, max_occupancy, "
                     "price_per_night, sort_order) VALUES (?, ?, 1, 2, 200.0, 98)",
                     (TAG + "Spare room", TAG + "tok"))
        conn.commit()
        rooms = conn.execute(
            "SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 2").fetchall()
    busy_room, quiet_room = rooms[0], rooms[1]

    # ------------------------------------------------------------ 1. room fault
    s.section("A fault in a room somebody is arriving into")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, 'Madame Arrival', 'arr@example.invalid', ?, ?, 2, 'confirmed', ?)""",
        (busy_room["id"], TAG + "SOON", TAG + "tok1", _iso(4), _iso(7), now))
    conn.commit()

    coming = m.room_arrivals_soon(conn, busy_room["id"])
    s.check("the arrival is found", bool(coming) and coming[0]["guest_name"] == "Madame Arrival",
            detail=str([c["guest_name"] for c in coming]))
    s.check("a room with nobody coming reports nothing",
            m.room_arrivals_soon(conn, quiet_room["id"]) == [],
            detail=str([c["guest_name"] for c in m.room_arrivals_soon(conn, quiet_room["id"])]))
    # An arrival in March is not a reason to drop everything today.
    s.check("an arrival beyond the horizon is not counted",
            m.room_arrivals_soon(conn, busy_room["id"], within_days=2) == [])

    r = oc.post("/room-issues/new", data={
        "room_id": busy_room["id"], "title": TAG + "no hot water",
        "description": "shower runs cold", "assigned_to_user_id": staff["id"],
    }, follow_redirects=True)
    task = conn.execute("SELECT * FROM tasks WHERE title LIKE ? ORDER BY id DESC LIMIT 1",
                        ("%" + TAG + "no hot water",)).fetchone()
    s.check("the job is raised high, not normal", bool(task) and task["priority"] == "high", r,
            detail=f"priority={task['priority'] if task else None!r}")
    # Due "today" on a fault that has to be fixed before Friday is a date
    # picked without looking at anything.
    s.check("it is due by the day they arrive", bool(task) and task["due_date"] == _iso(4),
            detail=f"due={task['due_date'] if task else None!r}")
    s.check("and the task says who is coming and when",
            bool(task) and "Madame Arrival" in (task["notes"] or ""),
            detail=str(task["notes"] if task else None)[:80])

    # ...and the harmless version stays harmless.
    oc.post("/room-issues/new", data={
        "room_id": quiet_room["id"], "title": TAG + "bulb gone",
        "description": "", "assigned_to_user_id": staff["id"],
    }, follow_redirects=True)
    quiet_task = conn.execute("SELECT * FROM tasks WHERE title LIKE ? ORDER BY id DESC LIMIT 1",
                              ("%" + TAG + "bulb gone",)).fetchone()
    s.check("a fault in an empty room stays normal priority",
            bool(quiet_task) and quiet_task["priority"] == "normal",
            detail=f"priority={quiet_task['priority'] if quiet_task else None!r}")

    page = oc.get("/room-issues").get_data(as_text=True)
    s.check("the list says who is arriving, before you have to ask",
            "Madame Arrival arrives" in page)

    # ------------------------------------------------------------- 2. stock
    s.section("Stock against what has already been promised")
    cur = conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level, unit_cost,
           location, active, created_at) VALUES (?, 'drinks', 'bottle', 2, 48.0, 'Cellar', 1, ?)""",
        (TAG + "champagne", now))
    item_id = cur.lastrowid
    conn.execute("""INSERT INTO stock_movements (stock_item_id, delta, reason, created_at)
                    VALUES (?, 4, 'opening', ?)""", (item_id, now))
    cur = conn.execute(
        """INSERT INTO extras (name, price, active, sort_order, category, stock_item_id,
           stock_qty_per_unit, guest_bookable, sold_in_pos)
           VALUES (?, 90, 1, 0, 'drinks', ?, 1, 1, 1)""", (TAG + "champagne on arrival", item_id))
    extra_id = cur.lastrowid
    booking = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (TAG + "SOON",)).fetchone()
    for qty, status, when in [(3, "confirmed", _iso(3)), (2, "confirmed", _iso(6)),
                              (9, "cancelled", _iso(4))]:
        conn.execute(
            """INSERT INTO booking_extras (category, booking_id, extra_id, name, unit_price,
               quantity, status, scheduled_for, created_at)
               VALUES ('room', ?, ?, ?, 90, ?, ?, ?, ?)""",
            (booking["id"], extra_id, TAG + "champagne on arrival", qty, status, when, now))
    conn.commit()

    promised = m.committed_stock(conn, [item_id])
    s.check("promises are added up across bookings", promised.get(item_id) == 5,
            detail=str(promised))
    # A cancelled extra is not a promise. Counting it would send you shopping
    # for bottles nobody ordered.
    s.check("a cancelled line is not a promise", promised.get(item_id) != 14)
    s.check("promises beyond the horizon are not counted",
            m.committed_stock(conn, [item_id], within_days=4).get(item_id) == 3,
            detail=str(m.committed_stock(conn, [item_id], within_days=4)))

    page = oc.get("/admin/stock").get_data(as_text=True)
    # Four bottles is above the reorder level of two, so "low stock" alone
    # would have said nothing at all — and five are already sold.
    s.check("four in stock against five promised is flagged",
            "Already promised more than we hold" in page)
    s.check("and it says how short", "1 short" in page or "1.0 short" in page)
    short = oc.get("/admin/stock?state=Short+of+what%27s+promised").get_data(as_text=True)
    s.check("it can be filtered to just those", TAG + "champagne" in short)

    # ------------------------------------------------------------- 3. leave
    s.section("What the house is doing in the week somebody wants off")
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, created_at)
           VALUES (?, ?, 'Dinner Party', 'd@example.invalid', 20, ?, 'confirmed', ?)""",
        (TAG + "DIN", TAG + "tokd", _iso(21), now))
    conn.commit()

    busy = m.leave_impact(conn, _iso(19), _iso(23))
    # A window that is quiet by construction, not by luck. This used to be
    # today + 300 days, which was empty when it was written and stopped being
    # empty the morning "today" drifted onto a seeded atelier in June 2027 --
    # the app was right and the test was wrong, which is the worst way round.
    # leave_impact reads bookings, restaurant_bookings, workshop_sessions and
    # leave_requests, so start after the last date any of them knows about.
    last = max([r[0] for r in [
        conn.execute("SELECT MAX(departure_date) FROM bookings").fetchone(),
        conn.execute("SELECT MAX(dinner_date) FROM restaurant_bookings").fetchone(),
        conn.execute("SELECT MAX(end_date) FROM workshop_sessions").fetchone(),
        conn.execute("SELECT MAX(end_date) FROM leave_requests").fetchone(),
    ] if r and r[0]] + [_iso(0)])
    quiet_from = (date.fromisoformat(last[:10]) + timedelta(days=30)).isoformat()
    quiet_to = (date.fromisoformat(last[:10]) + timedelta(days=33)).isoformat()
    quiet = m.leave_impact(conn, quiet_from, quiet_to)
    s.check("a booked dinner shows up in that week",
            any("20 covers" in n for n in busy["notes"]), detail=str(busy["notes"]))
    s.check("a quiet week says so rather than inventing something",
            quiet["notes"] == [],
            detail=f"{quiet['notes']} in {quiet_from}..{quiet_to}, which is 30 days "
                   f"past the last date anything is booked ({last[:10]})")

    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type,
           status, requested_at) VALUES (?, ?, ?, ?, 'annual', 'pending', ?)""",
        (staff["id"], _iso(19), _iso(23), TAG + "skiing", now))
    conn.commit()
    page = oc.get("/admin/leave").get_data(as_text=True)
    s.check("it is on screen next to the Approve button",
            "That week:" in page and "20 covers" in page)

    # ------------------------------------------------------- 4. away on the day
    s.section("A task given to somebody who will not be there")
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type,
           status, requested_at, decided_at) VALUES (?, ?, ?, ?, 'annual', 'approved', ?, ?)""",
        (staff["id"], _iso(40), _iso(45), TAG + "approved trip", now, now))
    conn.commit()
    s.check("their leave is found for a day inside it",
            bool(m.away_on(conn, staff["id"], _iso(42))),
            detail=str(m.away_on(conn, staff["id"], _iso(42))))
    s.check("and not for a day outside it", m.away_on(conn, staff["id"], _iso(60)) is None)
    s.check("the reason says when they are back",
            "until" in (m.away_on(conn, staff["id"], _iso(42)) or ""))

    r = oc.post("/admin/tasks/new", data={
        "assigned_to_user_id": staff["id"], "title": TAG + "polish the hall",
        "due_date": _iso(42),
    }, follow_redirects=True)
    body = r.get_data(as_text=True)
    s.check("assigning into their leave says so", "may need somebody else" in body, r)
    # Said, not blocked — sometimes you assign it knowing they are back by then.
    s.check("but the task is still created",
            bool(conn.execute("SELECT 1 FROM tasks WHERE title = ?",
                              (TAG + "polish the hall",)).fetchone()))
    r = oc.post("/admin/tasks/new", data={
        "assigned_to_user_id": staff["id"], "title": TAG + "sweep the yard",
        "due_date": _iso(60),
    }, follow_redirects=True)
    s.check("a task on a day they are here says nothing",
            "may need somebody else" not in r.get_data(as_text=True))

    # ------------------------------------------------- 5. certificate vs rota
    s.section("A certificate that runs out under a rota already booked")
    conn.execute(
        """INSERT INTO certifications (user_id, name, expiry_date, required, created_at)
           VALUES (?, ?, ?, 1, ?)""", (staff["id"], TAG + "first aid", _iso(20), now))
    conn.execute(
        """INSERT INTO certifications (user_id, name, expiry_date, required, created_at)
           VALUES (?, ?, ?, 1, ?)""", (staff["id"], TAG + "food hygiene", _iso(25), now))
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time,
                    role_note, created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
                 (staff["id"], _iso(30), TAG + "rostered", now))
    conn.commit()

    certs = {c["name"]: c for c in
             m.expiring_certifications(conn, m.house_today())}
    first_aid = certs.get(TAG + "first aid")
    s.check("shifts booked after the expiry are counted",
            bool(first_aid) and first_aid["shifts_after"] >= 1,
            detail=str(first_aid["shifts_after"] if first_aid else None))
    s.check("and it says which day the problem starts",
            bool(first_aid) and first_aid["first_shift_after"] == _iso(30),
            detail=str(first_aid["first_shift_after"] if first_aid else None))

    page = oc.get("/admin/hr").get_data(as_text=True)
    s.check("the HR page raises it above the ordinary expiries",
            "run out before shifts already booked" in page
            or "runs out before shifts already booked" in page)
    s.check("and each row says how many shifts", "shift booked after it" in page
            or "shifts booked after it" in page)

    _cleanup(conn)
    conn.close()
    return s
