"""A flag is not a request.

Marking a car dirty, dismissing somebody's timesheet correction, fixing a fault
they reported — each of these set a column and stopped. Nobody was told, no job
was created, and the person waiting found out by asking. These check that
something actually happens, and that it reaches the right person.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db, ensure_employee, ensure_room, flashes
import _harness

m = _harness.m
TAG = "looptest-"


def _now():
    return datetime.now(timezone.utc)


def _cleanup(conn):
    """Everything this suite makes, before it starts and after it ends.

    It used to leave all of it, and the suites share one database for the
    whole run. The one that cost something was the shift: seven hours this
    month for whichever employee ensure_employee() lends. On the local copy
    that is a real person with an hourly rate, so the hours priced and nothing
    showed. On a fresh database it is "Test Employee", who has no rate -- so
    the payroll pack carried a blocker for the rest of the run, every request
    for this month's export after this suite was refused, and the export's
    working branch dropped out of coverage without any check going red.

    The shift carries no tag of its own; its corrections do, so it is found
    through them.
    """
    entries = [r["time_entry_id"] for r in conn.execute(
        "SELECT DISTINCT time_entry_id FROM timesheet_corrections WHERE note LIKE ?",
        (TAG + "%",)).fetchall()]
    conn.execute("DELETE FROM timesheet_corrections WHERE note LIKE ?", (TAG + "%",))
    conn.executemany("DELETE FROM breaks WHERE time_entry_id = ?", [(e,) for e in entries])
    conn.executemany("DELETE FROM time_entries WHERE id = ?", [(e,) for e in entries])
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    vans = [r["id"] for r in conn.execute("SELECT id FROM vehicles WHERE name LIKE ?",
                                          (TAG + "%",)).fetchall()]
    for table in ("vehicle_transfers", "vehicle_maintenance", "vehicle_usage"):
        conn.executemany(f"DELETE FROM {table} WHERE vehicle_id = ?", [(v,) for v in vans])
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _notes_since(conn, user_id, since):
    return conn.execute(
        "SELECT * FROM notifications WHERE user_id = ? AND created_at >= ? ORDER BY id DESC",
        (user_id, since)).fetchall()


def run():
    s = Suite("Closing the loop")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    oc, _ec, owner, _emp = clients()
    staff = ensure_employee()
    room = ensure_room()
    now = _now()

    # ---------------------------------------------------------------- vehicles
    s.section("A car marked dirty or low becomes somebody's job")
    conn.execute(
        """INSERT INTO vehicles (name, vehicle_type, cleanliness, fuel_level, created_at)
           VALUES (?, 'car', 'clean', 'ok', ?)""", (TAG + "Van", now.isoformat()))
    conn.commit()
    van = conn.execute("SELECT * FROM vehicles WHERE name = ?", (TAG + "Van",)).fetchone()

    mark = _now().isoformat()
    r = oc.post(f"/management/vehicles/{van['id']}/toggle-clean", follow_redirects=True)
    task = conn.execute("SELECT * FROM tasks WHERE title LIKE ? ORDER BY id DESC LIMIT 1",
                        (TAG + "Van%",)).fetchone()
    s.check("marking a car dirty raises an open task", bool(task) and task["status"] == "open", r)
    s.check("and the owner is told", bool(_notes_since(conn, owner["id"], mark)))

    # ...and urgency depends on whether the car is needed
    conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction, scheduled_at, created_at)
           VALUES (?, ?, 'pickup', ?, ?)""",
        (van["id"], TAG + "Arrival", (now + timedelta(hours=6)).isoformat(), now.isoformat()))
    conn.commit()
    oc.post(f"/management/vehicles/{van['id']}/toggle-fuel", follow_redirects=True)
    fuel = conn.execute("SELECT * FROM tasks WHERE title = ? ORDER BY id DESC LIMIT 1",
                        (TAG + "Van needs fuel",)).fetchone()
    s.check("low fuel raises a task too", bool(fuel))
    # "Low on fuel" matters far more when there is an airport run at six.
    s.check("with a transfer six hours away it is high priority",
            bool(fuel) and fuel["priority"] == "high",
            detail=f"priority={fuel['priority'] if fuel else None!r}")
    s.check("and the task says why it is urgent",
            bool(fuel) and "booked out" in (fuel["notes"] or ""),
            detail=(fuel["notes"] if fuel else "")[:70])

    s.section("The fault you reported is fixed")
    conn.execute(
        """INSERT INTO vehicle_maintenance (vehicle_id, title, reported_by_user_id, status, created_at)
           VALUES (?, ?, ?, 'open', ?)""",
        (van["id"], TAG + "Wiper blade", staff["id"], now.isoformat()))
    conn.commit()
    item = conn.execute("SELECT * FROM vehicle_maintenance WHERE title = ?",
                        (TAG + "Wiper blade",)).fetchone()
    mark = _now().isoformat()
    oc.post(f"/management/vehicles/maintenance/{item['id']}/resolve", follow_redirects=True)
    got = _notes_since(conn, staff["id"], mark)
    s.check("whoever reported a vehicle fault hears it is fixed",
            bool(got) and "fixed" in got[0]["title"].lower(),
            detail=f"got {[g['title'] for g in got]}")

    # ------------------------------------------------------------ room issues
    s.section("Room issues, both directions")
    conn.execute(
        """INSERT INTO room_issues (room_id, reported_by_user_id, title, status, created_at)
           VALUES (?, ?, ?, 'open', ?)""",
        (room["id"], staff["id"], TAG + "Dripping tap", now.isoformat()))
    conn.commit()
    issue = conn.execute("SELECT * FROM room_issues WHERE title = ?",
                         (TAG + "Dripping tap",)).fetchone()

    mark = _now().isoformat()
    oc.post(f"/room-issues/{issue['id']}/resolve", follow_redirects=True)
    got = _notes_since(conn, staff["id"], mark)
    s.check("resolving tells the reporter", bool(got) and "Fixed" in got[0]["title"])

    mark = _now().isoformat()
    oc.post(f"/room-issues/{issue['id']}/reopen", follow_redirects=True)
    got = _notes_since(conn, staff["id"], mark)
    # Reopening matters MORE than resolving: they were told it was done.
    s.check("reopening tells them too", bool(got) and "Reopened" in got[0]["title"],
            detail=f"got {[g['title'] for g in got]}")

    # -------------------------------------------------------------- timesheets
    s.section("A decision about somebody's hours is not made in silence")
    cur = conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
        (staff["id"], (now - timedelta(hours=8)).isoformat(),
         (now - timedelta(hours=1)).isoformat()))
    entry_id = cur.lastrowid
    conn.execute(
        """INSERT INTO timesheet_corrections (time_entry_id, user_id, note, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (entry_id, staff["id"], TAG + "I worked till seven", now.isoformat()))
    conn.commit()
    corr = conn.execute("SELECT * FROM timesheet_corrections WHERE note = ?",
                        (TAG + "I worked till seven",)).fetchone()

    mark = _now().isoformat()
    r = oc.post(f"/admin/timesheets/corrections/{corr['id']}/dismiss",
                data={"reason": TAG + "the clock record matches the rota"}, follow_redirects=True)
    s.check("the dismissal saves",
            conn.execute("SELECT status FROM timesheet_corrections WHERE id = ?",
                         (corr["id"],)).fetchone()["status"] == "dismissed", r)
    got = _notes_since(conn, staff["id"], mark)
    # Declining in silence is worse than declining: they assume it is still
    # coming, and the next they know is a payslip that doesn't match.
    s.check("the employee is told it was declined", bool(got))
    s.check("and told why, not fobbed off with a generic line",
            bool(got) and TAG in (got[0]["body"] or ""),
            detail=(got[0]["body"] if got else "")[:60])

    mark = _now().isoformat()
    oc.post(f"/admin/timesheets/corrections/{corr['id']}/dismiss", follow_redirects=True)
    s.check("dismissing an already-dismissed one sends nothing further",
            not _notes_since(conn, staff["id"], mark))

    conn.execute(
        """INSERT INTO timesheet_corrections (time_entry_id, user_id, note, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (entry_id, staff["id"], TAG + "please fix my clock-out", now.isoformat()))
    conn.commit()
    corr2 = conn.execute("SELECT * FROM timesheet_corrections WHERE note = ?",
                         (TAG + "please fix my clock-out",)).fetchone()
    mark = _now().isoformat()
    r = oc.post(f"/admin/timesheets/corrections/{corr2['id']}/resolve",
                data={"clock_out_time": "19:00"}, follow_redirects=True)
    got = _notes_since(conn, staff["id"], mark)
    s.check("an applied correction tells them their hours changed",
            bool(got) and "corrected" in got[0]["title"].lower(), r,
            detail=f"got {[g['title'] for g in got]}")
    s.check("and says what the hours now are",
            bool(got) and "19:00" in (got[0]["body"] or ""),
            detail=(got[0]["body"] if got else "")[:60])

    # ---------------------------------------------------------------- no-shows
    s.section("A no-show follows the guest to their next booking")
    def dinner(ref, days_ago, no_show):
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
               guest_email, party_size, dinner_date, status, no_show_at, created_at)
               VALUES (?,?,?,?,2,?, 'confirmed', ?, ?)""",
            (ref, "tok-" + ref, "Loop Tester", "loop@example.invalid",
             (now - timedelta(days=days_ago)).date().isoformat(),
             (now - timedelta(days=days_ago)).isoformat() if no_show else None,
             now.isoformat()))
        conn.commit()

    dinner(TAG + "1", 60, True)
    dinner(TAG + "2", 30, True)
    dinner(TAG + "3", 1, False)
    live = conn.execute("SELECT id FROM restaurant_bookings WHERE reference_code = ?",
                        (TAG + "3",)).fetchone()

    counts = m.prior_no_shows(conn, ["LOOP@Example.invalid", "nobody@example.invalid"])
    s.check("prior no-shows are counted, case-insensitively",
            counts.get("loop@example.invalid") == 2, detail=str(counts))
    s.check("an address with none is simply absent", "nobody@example.invalid" not in counts)
    s.check("no addresses means no query, not a crash", m.prior_no_shows(conn, []) == {})

    mark = _now().isoformat()
    oc.post(f"/admin/restaurant/{live['id']}/no-show", follow_redirects=True)
    got = _notes_since(conn, owner["id"], mark)
    # A first no-show is bad luck. A third is a pattern.
    s.check("a third no-show is flagged to the owner",
            any("not turned up 3 times" in n["title"] for n in got),
            detail=f"got {[n['title'] for n in got]}")

    # 14 October 2026: "Mark no-show" was drawn on next week's tables too, and
    # the route took it, feeding the count above with dinners still to come.
    dinner(TAG + "4", -3, False)
    ahead = conn.execute("SELECT id FROM restaurant_bookings WHERE reference_code = ?",
                         (TAG + "4",)).fetchone()
    page = oc.get("/admin/restaurant").get_data(as_text=True)
    # Its Cancel button proves the card is drawn, so the absence below is
    # about the button and not about a list that left the dinner out.
    s.check("a confirmed dinner still to come is on the list",
            f"/admin/restaurant/{ahead['id']}/cancel" in page)
    s.check("and is not offered 'Mark no-show'",
            f"/admin/restaurant/{ahead['id']}/no-show" not in page)
    r = oc.post(f"/admin/restaurant/{ahead['id']}/no-show", follow_redirects=True)
    stamped = conn.execute("SELECT no_show_at FROM restaurant_bookings WHERE id = ?",
                           (ahead["id"],)).fetchone()["no_show_at"]
    s.check("and the route will not stamp one", stamped is None,
            detail=f"{stamped!r} {flashes(r)[:1]}")

    conn.execute("""UPDATE restaurant_bookings SET no_show_at = NULL, status = 'pending',
                    dinner_date = ? WHERE id = ?""",
                 ((now + timedelta(days=3)).date().isoformat(), live["id"]))
    conn.commit()
    page = oc.get("/admin/restaurant").get_data(as_text=True)
    # The point of recording it is to decide with it, so it has to be on screen
    # next to Confirm — not on a history page nobody opens mid-service.
    s.check("the reservations list warns before you confirm the table",
            "previous no-show" in page)
