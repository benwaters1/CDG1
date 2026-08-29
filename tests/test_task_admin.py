"""Moving a task to another day, copying it to next week, editing a candidate.

Three owner-side routes with no tests. Two of them had a bug, and both are the
same shape: an action that reports success without having done anything, or
having done something it should not.

THE ONE THAT MATTERS is duplicating a watch task.

Watch tasks are the findings the house raises about itself — an uncovered
shift, a certificate about to lapse — and they are self-healing on purpose:
nothing in that set has a "done" action of its own, so every run rebuilds the
picture and ticks off whatever has stopped being true. That is the half that
stops the list becoming a record of every problem the château has ever had,
which nobody reads twice, including the morning it lists a real one.

`generate_watch_tasks` keys the open ones on TITLE. Duplicating one produced a
second row with the same title and the same `watch` origin, so the dict
collapsed the pair to one entry and only that one was ever closed. The other
could not be reached by the pass that closes them and had no tick of its own,
so it stayed on the list for good — the exact failure the self-healing half
exists to prevent.

Fixed at both ends: a copy made by hand is a manual task whatever it was
copied from, and the close is keyed on title rather than on whichever id
survived the dict, so any pair already in a live database is cleared too.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZTADM"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM candidates WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _task(title, due=None, origin="manual", assigned=None, notes=None, priority="normal"):
    conn = db()
    conn.execute(
        """INSERT INTO tasks (title, notes, priority, due_date, status, origin,
           assigned_to_user_id, created_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?, ?)""",
        (f"{TAG} {title}", notes, priority, due, origin, assigned,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE title = ?", (f"{TAG} {title}",)).fetchone()
    conn.close()
    return row


def _rows(title):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE title = ? ORDER BY id", (f"{TAG} {title}",)).fetchall()
    finally:
        conn.close()


def _candidate(name):
    conn = db()
    conn.execute(
        """INSERT INTO candidates (name, email, role_applied, status, created_at)
           VALUES (?, ?, 'Housekeeper', 'new', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM candidates WHERE name = ?",
                       (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Task admin")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("Moving a task to another day")
    t = _task("water the orangery", due="2027-03-01")
    r = oc.post(f"/admin/tasks/{t['id']}/reschedule", json={"due_date": "2027-03-08"})
    s.check("it accepts the new date", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and the task moved", _rows("water the orangery")[0]["due_date"] == "2027-03-08",
            detail=f"got {_rows('water the orangery')[0]['due_date']}")

    s.section("A date that is not one is refused")
    r = oc.post(f"/admin/tasks/{t['id']}/reschedule", json={"due_date": "next Tuesday"})
    s.check("400, not a crash", r.status_code == 400, detail=f"HTTP {r.status_code}")
    s.check("and the task did not move",
            _rows("water the orangery")[0]["due_date"] == "2027-03-08",
            detail="the status code said no and the row changed anyway")
    r = oc.post(f"/admin/tasks/{t['id']}/reschedule", json={"due_date": ""})
    s.check("an empty date too", r.status_code == 400)

    s.section("A task that is not there is a 404, not a 500")
    r = oc.post("/admin/tasks/999999/reschedule", json={"due_date": "2027-04-01"})
    s.check("404", r.status_code == 404, detail=f"HTTP {r.status_code}")

    s.section("Copying a task to next week")
    src = _task("change the bed linen", due="2027-03-02", notes="ZZ the blue set",
                priority="high", assigned=owner["id"])
    oc.post(f"/admin/tasks/{src['id']}/duplicate-next-week", follow_redirects=True)
    made = _rows("change the bed linen")
    s.check("there are two now", len(made) == 2, detail=f"{len(made)}")
    if len(made) == 2:
        copy = made[1]
        s.check("the copy is due seven days later", copy["due_date"] == "2027-03-09",
                detail=f"got {copy['due_date']}")
        s.check("it keeps the notes", copy["notes"] == "ZZ the blue set")
        s.check("and the person it is for", copy["assigned_to_user_id"] == owner["id"])
        s.check("and the priority", copy["priority"] == "high")
        s.check("but starts open, not carrying the original's state",
                copy["status"] == "open")

    s.section("A task with no date copies without inventing one")
    undated = _task("polish the hall floor")
    oc.post(f"/admin/tasks/{undated['id']}/duplicate-next-week", follow_redirects=True)
    both = _rows("polish the hall floor")
    s.check("the copy exists", len(both) == 2, detail=f"{len(both)}")
    s.check("and has no due date rather than today plus seven",
            both[1]["due_date"] is None, detail=f"got {both[1]['due_date']}")

    s.section("A copy of a self-healing finding is NOT self-healing")
    # The whole bug. Two rows sharing a title and the watch origin collapse in
    # generate_watch_tasks, which keys on title — so one of them could never be
    # closed and had no tick of its own either.
    watched = _task("a finding the house raised", due="2027-05-01",
                    origin=m.WATCH_TASK_ORIGIN)
    oc.post(f"/admin/tasks/{watched['id']}/duplicate-next-week", follow_redirects=True)
    pair = _rows("a finding the house raised")
    s.check("the copy was made", len(pair) == 2, detail=f"{len(pair)}")
    if len(pair) == 2:
        s.check("the original is still a watch task",
                pair[0]["origin"] == m.WATCH_TASK_ORIGIN)
        s.check("but the copy is an ordinary one",
                pair[1]["origin"] == "manual",
                detail=f"origin={pair[1]['origin']!r} — a hand-made copy joined "
                       "the set that closes itself, and could not be reached "
                       "by the pass that closes it")

    s.section("And the closing pass clears a pair that already exists")
    # Any database that ran the old code has them. Closing on title rather than
    # on whichever id survived the dict is what reaches both.
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(2):
        conn.execute(
            """INSERT INTO tasks (title, status, origin, due_date, created_at)
               VALUES (?, 'open', ?, '2027-06-01', ?)""",
            (f"{TAG} a finding nobody has now", m.WATCH_TASK_ORIGIN, now))
    conn.commit()
    still_open = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE title = ? AND status != 'done'",
        (f"{TAG} a finding nobody has now",)).fetchone()["c"]
    conn.close()
    s.check("two are open to begin with", still_open == 2, detail=f"{still_open}")
    conn = db()
    with m.app.test_request_context("/"):
        m.generate_watch_tasks(conn, date.today())
    conn.commit()
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE title = ? AND status != 'done'",
        (f"{TAG} a finding nobody has now",)).fetchone()["c"]
    conn.close()
    s.check("the pass closes both", left == 0,
            detail=f"{left} still open — one of them can never be closed by "
                   "anything, and a watch task has no tick of its own")

    s.section("Editing a candidate")
    c = _candidate("Claudine Applicant")
    oc.post(f"/candidates/{c['id']}/edit",
            data={"name": f"{TAG} Claudine Applicant", "email": "new@example.invalid",
                  "phone": "+33 1 23 45 67 89", "role_applied": "Chef de partie",
                  "notes": "ZZ strong references"}, follow_redirects=True)
    conn = db()
    after = conn.execute("SELECT * FROM candidates WHERE id = ?", (c["id"],)).fetchone()
    conn.close()
    s.check("the email changes", after["email"] == "new@example.invalid")
    s.check("and the role", after["role_applied"] == "Chef de partie")
    s.check("and it is stamped", after["updated_at"] is not None)

    s.section("A blank name is refused")
    oc.post(f"/candidates/{c['id']}/edit", data={"name": "   "}, follow_redirects=True)
    conn = db()
    kept = conn.execute("SELECT name FROM candidates WHERE id = ?", (c["id"],)).fetchone()
    conn.close()
    s.check("the name they had is kept", kept["name"] == f"{TAG} Claudine Applicant",
            detail=f"got {kept['name']!r}")

    s.section("Editing a candidate somebody else deleted says so")
    # It used to say "Candidate updated." for an id that does not exist, so
    # whoever typed the change had no reason to look again.
    r = oc.post("/candidates/999999/edit",
                data={"name": f"{TAG} Ghost"}, follow_redirects=True)
    said = " ".join(flashes(r)).lower()
    s.check("it does not claim to have updated anything",
            "updated" not in said,
            detail=f"{flashes(r)[:1]} — a change nobody saved was reported as saved")
    s.check("it says what happened instead",
            "no longer" in said or "removed" in said, detail=f"{flashes(r)[:1]}")
    conn = db()
    n = conn.execute("SELECT COUNT(*) AS c FROM candidates WHERE name LIKE ?",
                     (TAG + " Ghost%",)).fetchone()["c"]
    conn.close()
    s.check("and nothing was created by it", n == 0, detail=f"{n} row(s)")

    s.section("Guards")
    guarded = _task("not for an employee", due="2027-07-01")
    for label, url, kw in (
        ("reschedule", f"/admin/tasks/{guarded['id']}/reschedule", {"json": {"due_date": "2027-08-01"}}),
        ("duplicate", f"/admin/tasks/{guarded['id']}/duplicate-next-week", {}),
        ("edit a candidate", f"/candidates/{c['id']}/edit", {"data": {"name": "x"}}),
    ):
        code = ec.post(url, **kw).status_code
        s.check(f"an employee cannot {label}", code in (302, 403), detail=f"HTTP {code}")
    s.check("the task did not move",
            _rows("not for an employee")[0]["due_date"] == "2027-07-01")
    s.check("and was not copied", len(_rows("not for an employee")) == 1)
    conn = db()
    name_now = conn.execute("SELECT name FROM candidates WHERE id = ?", (c["id"],)).fetchone()
    conn.close()
    s.check("and the candidate is untouched",
            name_now["name"] == f"{TAG} Claudine Applicant")

    _cleanup()
    return s
