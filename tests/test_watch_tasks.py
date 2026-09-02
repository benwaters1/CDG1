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
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'watch_task_assignee_%'")
    conn.execute("DELETE FROM audit_log WHERE target = ?", (TAG + "backup",))
    conn.commit()


def _run(conn):
    with m.app.test_request_context():
        return m.generate_watch_tasks(conn)


def _open_titles(conn):
    return [r["title"] for r in conn.execute(
        "SELECT title FROM tasks WHERE origin = 'watch' AND status != 'done'").fetchall()]


def _of_kind(conn, needle):
    """Open watch tasks whose title contains needle.

    Every count in this file goes through here rather than over the whole list.
    A scratch database has never had a backup taken, so the backup finding is
    legitimately raised on every run — a bare "exactly one open" would be
    asserting on that as much as on the thing under test, and would break again
    the next time a kind is added.
    """
    return [t for t in _open_titles(conn) if needle in t]


def _watch_task(conn, like):
    """The one open watch task whose title starts with `like`, or None.

    Every check below goes through here. Subscripting a bare fetchone() is what
    turns "this stopped working" into CRASH, and a crashed suite reports none of
    the checks after it — which is how a broken thing hides behind the failure
    it caused.
    """
    return conn.execute(
        """SELECT * FROM tasks WHERE origin = 'watch' AND status != 'done'
             AND title LIKE ? LIMIT 1""", (like,)).fetchone()


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
    s.check("a task was raised", len(_of_kind(conn, "Not qualified")) == 1, detail=str(r))
    titles = _open_titles(conn)
    s.check("it names the person and the day",
            any("Lapsed" in t and _iso(3) in t for t in titles), detail=str(titles))
    task = conn.execute(
        """SELECT * FROM tasks WHERE origin = 'watch' AND status != 'done'
             AND title LIKE 'Not qualified%' LIMIT 1""").fetchone()
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
    s.check("still exactly one open", len(_of_kind(conn, "Not qualified")) == 1)

    s.section("It closes itself when it stops being true")
    # Nothing here has a 'done' button — the fix is to move the shift, which
    # happens somewhere else entirely.
    conn.execute("DELETE FROM shifts WHERE role_note = ?", (TAG + "shift",))
    conn.commit()
    r3 = _run(conn)
    s.check("the task was ticked off", r3["closed"] == 1, detail=str(r3))
    s.check("nothing is left open", _of_kind(conn, "Not qualified") == [])
    done = conn.execute(
        """SELECT completed_at FROM tasks WHERE origin = 'watch'
             AND title LIKE 'Not qualified%' LIMIT 1""").fetchone()
    s.check("and it is marked complete, not deleted", bool(done["completed_at"]))

    s.section("Only blocking things become tasks")
    # A guest arriving in three weeks to a room with a fault is worth seeing on
    # a page. It is not worth a job on somebody's list today.
    _room = conn.execute("SELECT id, name FROM rooms LIMIT 1").fetchone()
    room, room_label = _room["id"], _room["name"]
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
    faults = _of_kind(conn, "Open fault")
    s.check("a fault twelve days out raises nothing", faults == [], detail=str(faults))

    conn.execute("UPDATE bookings SET arrival_date = ?, departure_date = ? "
                 "WHERE reference_code = ?", (_iso(2), _iso(4), TAG + "FAR"))
    conn.commit()
    _run(conn)
    faults = _of_kind(conn, "Open fault")
    s.check("the same fault two days out does", len(faults) == 1, detail=str(faults))
    # Asked of the room this test actually used. A literal name here passes
    # until the house renames a room, and then fails for a reason that has
    # nothing to do with watch tasks.
    s.check("and it names the room", faults and room_label in faults[0],
            detail=str(faults))

    s.section("A finding goes to the person who can end it")
    # The point of raising a task at all. The owner already had these on the
    # home page; a task is only different if it reaches somebody else.
    route_to = _emp["id"]
    before = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'automation_%'").fetchall()}
    form = {"watch_task_assignee_certification": str(route_to)}
    form.update({k: v for k, v in before.items() if v == "1"})
    posted = _oc.post("/admin/automation/settings", data=form, follow_redirects=True)
    s.check("the owner can set the route from the automation page",
            posted.status_code == 200, detail=f"HTTP {posted.status_code}")
    # That POST rewrites every automation toggle from the form. Put them back,
    # or this suite quietly changes the app for every suite that runs after it.
    for k, v in before.items():
        conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", (v, k))
    conn.commit()
    stored = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'watch_task_assignee_certification'"
    ).fetchone()
    s.check("and it is stored", stored is not None and stored["value"] == str(route_to),
            detail="no row saved" if stored is None else stored["value"])

    # Re-raise the certification finding that the closing section removed.
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
           created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (uid, _iso(3), TAG + "shift", now))
    conn.commit()
    _run(conn)
    cert = _watch_task(conn, "Not qualified%")
    s.check("the task is assigned to them, not to nobody",
            cert is not None and cert["assigned_to_user_id"] == route_to,
            detail="no such task" if cert is None else str(cert["assigned_to_user_id"]))
    unrouted = _watch_task(conn, "No backup%")
    s.check("a kind with no route is still left unassigned",
            unrouted is not None and unrouted["assigned_to_user_id"] is None,
            detail="no such task" if unrouted is None else str(unrouted["assigned_to_user_id"]))

    # The claim being tested is "on their calendar, on the day it matters".
    # An employee's calendar shows only tasks assigned to them, so before the
    # route existed this was on nobody's but the owner's.
    viewer = conn.execute("SELECT * FROM users WHERE id = ?", (route_to,)).fetchone()
    with m.app.test_request_context():
        cal = m.build_calendar(conn, "month",
                               datetime.now(m.LOCAL_TZ).date() + timedelta(days=3),
                               viewer=viewer)
    on_day = [e["title"] for c in cal["cells"] if c["iso"] == _iso(3)
              for e in c["events"]]
    s.check("and it shows on that employee's own calendar, on the day",
            any("Not qualified" in t for t in on_day), detail=str(on_day))

    s.section("Changing the route moves the task that is already open")
    owner_id = _owner["id"]
    conn.execute("UPDATE app_settings SET value = ? WHERE key = ?",
                 (str(owner_id), "watch_task_assignee_certification"))
    conn.commit()
    r_moved = _run(conn)
    moved = _watch_task(conn, "Not qualified%")
    s.check("it is reassigned",
            moved is not None and moved["assigned_to_user_id"] == owner_id,
            detail="no such task" if moved is None else str(moved["assigned_to_user_id"]))
    s.check("the job line reports it", r_moved["moved"] >= 1, detail=str(r_moved))
    # Rewritten in place, not closed and re-raised: a new row would read as the
    # problem having been fixed and come back, which is a different story.
    s.check("the same task, not a fresh one",
            cert is not None and moved is not None and moved["id"] == cert["id"],
            detail="%s -> %s" % (cert["id"] if cert else None,
                                 moved["id"] if moved else None))
    s.check("and nothing was closed to do it", r_moved["closed"] == 0, detail=str(r_moved))

    s.section("A route to somebody who has left hands itself back")
    conn.execute("UPDATE app_settings SET value = ? WHERE key = ?",
                 (str(route_to), "watch_task_assignee_certification"))
    conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (route_to,))
    conn.commit()
    _run(conn)
    orphan = _watch_task(conn, "Not qualified%")
    # Unassigned is worse than routed but better than routed to an account
    # nobody opens: it is still on the owner's list.
    s.check("the task falls back to nobody rather than to a closed account",
            orphan is not None and orphan["assigned_to_user_id"] is None,
            detail="no such task" if orphan is None else str(orphan["assigned_to_user_id"]))
    conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (route_to,))
    conn.commit()

    s.section("Ticking one off does not settle it")
    # Routing gave these a tick box for the first time: they now sit on an
    # employee's own list, which has one. Ticking is not blocked, because the
    # honest answer is not to refuse the click but to raise the thing again —
    # the shift is still uncovered whatever the list says about it.
    ticked = _watch_task(conn, "Not qualified%")
    conn.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
                 (now, ticked["id"] if ticked else 0))
    conn.commit()
    s.check("it leaves the list when ticked", _of_kind(conn, "Not qualified") == [])
    _run(conn)
    back = _watch_task(conn, "Not qualified%")
    s.check("and the next run raises it again, because it is still true",
            back is not None and (ticked is None or back["id"] != ticked["id"]),
            detail="not re-raised" if back is None else "id %s" % back["id"])

    s.section("The backup nobody is taking is a job, not just a warning")
    # It was a blocker on the home page from the day the panel existed and the
    # only one that never became a task. It is also the one nobody trips over:
    # a broken radiator announces itself, a backup that stopped four months ago
    # announces itself once, on the morning the database is gone.
    s.check("no backup on record raises a task",
            len(_of_kind(conn, "No backup")) == 1,
            detail=str(_of_kind(conn, "No backup")))
    conn.execute(
        """INSERT INTO audit_log (action, target, created_at)
           VALUES ('backup_auto_sent', ?, ?)""", (TAG + "backup", now))
    conn.commit()
    _run(conn)
    s.check("and a backup arriving closes it again",
            _of_kind(conn, "No backup") == [],
            detail=str(_of_kind(conn, "No backup")))
    conn.execute("DELETE FROM audit_log WHERE target = ?", (TAG + "backup",))
    # The section below counts on exactly the eight clashes it makes itself.
    conn.execute("DELETE FROM shifts WHERE role_note = ?", (TAG + "shift",))
    conn.commit()

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
