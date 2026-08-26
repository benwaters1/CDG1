"""Blocking findings become tasks, and stop being tasks when they stop being true.

The warnings panel put these where the owner looks. A task puts them on
somebody's list and on the calendar, because the person who reads the panel
and the person who can move a shift are usually not the same human.

The half worth testing hardest is the closing. Every other task generator in
this app closes when somebody does the work and says so; nothing here has a
"done" action of its own, so if these did not close themselves the list would
become a record of every problem the house has ever had — which nobody reads
twice, including the morning it lists a real one.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-wt-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM tasks WHERE origin = 'watch'")
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM certifications WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM role_requirements WHERE requirement LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def _run(conn):
    with m.app.test_request_context():
        return m.generate_watch_tasks(conn)


def _open_titles(conn):
    return [r["title"] for r in conn.execute(
        "SELECT title FROM tasks WHERE origin = 'watch' AND status != 'done'").fetchall()]


def run():
    s = Suite("watch tasks")
    _oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    s.section("A blocking finding becomes a task")
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', 'employee', 'Lapsed', ?, 'active', ?)""",
        (TAG + "a@example.invalid", TAG + "chef", now))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email = ?",
                       (TAG + "a@example.invalid",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO role_requirements (job_role, requirement, requirement_type,
           created_at) VALUES (?, ?, 'certification', ?)""",
        (TAG + "chef", TAG + "Hygiene", now))
    conn.execute(
        """INSERT INTO certifications (user_id, name, expiry_date, created_at)
           VALUES (?, ?, ?, ?)""", (uid, TAG + "Hygiene", _iso(-1), now))
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
           created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (uid, _iso(3), TAG + "shift", now))
    conn.commit()

    r = _run(conn)
    s.check("a task was raised", r["made"] == 1, detail=str(r))
    titles = _open_titles(conn)
    s.check("it names the person and the day",
            any("Lapsed" in t and _iso(3) in t for t in titles), detail=str(titles))
    task = conn.execute(
        "SELECT * FROM tasks WHERE origin = 'watch' AND status != 'done' LIMIT 1").fetchone()
    s.check("it is due on the day of the shift, not today",
            task["due_date"] == _iso(3), detail=str(task["due_date"]))
    s.check("and it is high priority", task["priority"] == "high")
    s.check("the note says what to actually do",
            "Move the shift" in (task["notes"] or ""), detail=str(task["notes"])[:80])

    s.section("Running again does not duplicate it")
    # The dedupe key is the title, so an unstable title would grow the list by
    # one every morning and nobody would notice until it was unusable.
    r2 = _run(conn)
    s.check("nothing new is raised", r2["made"] == 0, detail=str(r2))
    s.check("still exactly one open", len(_open_titles(conn)) == 1)

    s.section("It closes itself when it stops being true")
    # Nothing here has a 'done' button — the fix is to move the shift, which
    # happens somewhere else entirely.
    conn.execute("DELETE FROM shifts WHERE role_note = ?", (TAG + "shift",))
    conn.commit()
    r3 = _run(conn)
    s.check("the task was ticked off", r3["closed"] == 1, detail=str(r3))
    s.check("nothing is left open", _open_titles(conn) == [])
    done = conn.execute(
        "SELECT completed_at FROM tasks WHERE origin = 'watch' LIMIT 1").fetchone()
    s.check("and it is marked complete, not deleted", bool(done["completed_at"]))

    s.section("Only blocking things become tasks")
    # A guest arriving in three weeks to a room with a fault is worth seeing on
    # a page. It is not worth a job on somebody's list today.
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO room_issues (room_id, title, status, created_at)
           VALUES (?, ?, 'open', ?)""", (room, TAG + "shutter", now))
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Later', 'g@example.invalid', ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "FAR", TAG + "tk1", _iso(12), _iso(14), now))
    conn.commit()
    _run(conn)
    # Filtered to the fault: the same booking legitimately raises a cover-gap
    # task too (guests in the house, nobody rostered), and asserting on the
    # whole list would be testing the wrong thing.
    faults = [t for t in _open_titles(conn) if "Open fault" in t]
    s.check("a fault twelve days out raises nothing", faults == [], detail=str(faults))

    conn.execute("UPDATE bookings SET arrival_date = ?, departure_date = ? "
                 "WHERE reference_code = ?", (_iso(2), _iso(4), TAG + "FAR"))
    conn.commit()
    _run(conn)
    faults = [t for t in _open_titles(conn) if "Open fault" in t]
    s.check("the same fault two days out does", len(faults) == 1, detail=str(faults))
    s.check("and it names the room", faults and "King Room" in faults[0],
            detail=str(faults))

    s.section("The list is capped, and says so")
    for i in range(8):
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role, status,
               created_at) VALUES (?, 'x', 'employee', ?, ?, 'active', ?)""",
            (f"{TAG}b{i}@example.invalid", f"Many{i}", TAG + "chef", now))
    conn.commit()
    for i in range(8):
        u = conn.execute("SELECT id FROM users WHERE email = ?",
                         (f"{TAG}b{i}@example.invalid",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO certifications (user_id, name, expiry_date, created_at)
               VALUES (?, ?, ?, ?)""", (u, TAG + "Hygiene", _iso(-1), now))
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
               created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
            (u, _iso(5), TAG + "shift", now))
    conn.commit()
    r4 = _run(conn)
    s.check("it stops at the cap rather than raising eight",
            r4["dropped"].get("certification") == 3, detail=str(r4["dropped"]))
    with m.app.test_request_context():
        line = m.run_watch_tasks_job(conn)
    # A capped list that says nothing reads as a complete one.
    s.check("and the job line says what it left out", "not raised" in line,
            detail=line)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
