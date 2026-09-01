"""The things a member of staff taps during a shift.

Ticking a task off, accepting one somebody directed at them, going on a break
and coming back, marking a notification read, saying they have read the manual.
Small actions, all of them one tap, none of them tested.

Two reasons they are worth the file:

  BREAKS ARE MONEY. A break comes off paid hours, so start/end break is the
  only thing on this list that changes what somebody is owed. It is also the
  one with a real concurrency guard — a partial unique index rather than the
  `already_open` fast path, because two taps on a phone with a poor signal
  arrive together. If that guard ever goes, a shift accumulates overlapping
  breaks and the hours are wrong in the employer's favour, quietly.

  THE GUARDS ARE THE FEATURE. These routes take an id from the URL and act on
  it. complete_task must refuse a task that is not yours, accept_task must
  refuse one directed at somebody else, and read_notification must not mark
  another person's notification read. Each is a REFUSAL, which is the shape
  that most often passes for the wrong reason — a 403 that was really a 404, a
  no-op that was really a missing fixture. Every one here is checked by
  confirming the row did not change, not by trusting the status code.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZSHIFT"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM breaks WHERE time_entry_id IN
                    (SELECT id FROM time_entries WHERE user_id IN
                     (SELECT id FROM users WHERE name LIKE ?))""", (TAG + "%",))
    conn.execute("""DELETE FROM time_entries WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM notifications WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM manual_acknowledgments WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _person(name):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _as(user_id):
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def _task(assigned_to, title="a task", ack="pending", directed_by=None):
    conn = db()
    conn.execute(
        """INSERT INTO tasks (title, assigned_to_user_id, status, acknowledgment_status,
           directed_by_user_id, created_at)
           VALUES (?, ?, 'open', ?, ?, ?)""",
        (f"{TAG} {title}", assigned_to, ack, directed_by,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE title = ?", (f"{TAG} {title}",)).fetchone()
    conn.close()
    return row


def _read(table, row_id, column):
    conn = db()
    try:
        row = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return row[column] if row else None
    finally:
        conn.close()


def _clock_in(user_id):
    conn = db()
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
                 (user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    entry = conn.execute(
        "SELECT * FROM time_entries WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)).fetchone()
    conn.close()
    return entry


def _open_breaks(entry_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM breaks WHERE time_entry_id = ? AND end_at IS NULL",
            (entry_id,)).fetchone()["c"]
    finally:
        conn.close()


def run():
    s = Suite("Shift actions")
    _cleanup()
    oc, _ec, owner, _emp = clients()
    mine = _person("Mine")
    theirs = _person("Theirs")
    me, them = _as(mine["id"]), _as(theirs["id"])

    s.section("Ticking a task off, and unticking it")
    t = _task(mine["id"], "sweep the courtyard")
    me.post(f"/tasks/{t['id']}/complete")
    s.check("one tap means done", _read("tasks", t["id"], "status") == "done",
            detail=f"got {_read('tasks', t['id'], 'status')!r}")
    s.check("and it is stamped", _read("tasks", t["id"], "completed_at") is not None)
    me.post(f"/tasks/{t['id']}/complete")
    s.check("tapping again puts it back to open",
            _read("tasks", t["id"], "status") == "open",
            detail="untick left it stranded in in_progress, which the phone "
                   "checklist has no way to clear")

    s.section("Somebody else's task is not yours to tick")
    t2 = _task(mine["id"], "lay the fires")
    r = them.post(f"/tasks/{t2['id']}/complete")
    s.check("it is refused", r.status_code == 403, detail=f"HTTP {r.status_code}")
    s.check("and the task really did not move",
            _read("tasks", t2["id"], "status") == "open",
            detail="the status code said no and the row changed anyway")

    s.section("But the owner can")
    r = oc.post(f"/tasks/{t2['id']}/complete")
    s.check("the owner ticks it off", _read("tasks", t2["id"], "status") == "done",
            detail=f"HTTP {r.status_code} — somebody has to be able to close a "
                   "task for a person who has gone home")

    s.section("Accepting a task somebody directed at you")
    t3 = _task(mine["id"], "meet the 4pm arrival", ack="pending",
               directed_by=owner["id"])
    r = me.post(f"/tasks/{t3['id']}/accept")
    s.check("it is accepted", _read("tasks", t3["id"], "acknowledgment_status") == "accepted",
            detail=f"HTTP {r.status_code}")
    conn = db()
    notes = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND kind = 'task_response'",
        (owner["id"],)).fetchone()["c"]
    conn.close()
    s.check("and whoever asked is told", notes >= 1, detail=f"{notes}")

    s.section("Accepting it twice does not tell them twice")
    r2 = me.post(f"/tasks/{t3['id']}/accept")
    conn = db()
    after = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND kind = 'task_response'",
        (owner["id"],)).fetchone()["c"]
    conn.close()
    s.check("the second tap is refused", r2.status_code == 404,
            detail=f"HTTP {r2.status_code}")
    s.check("and no second notification goes out", after == notes,
            detail=f"{notes} -> {after} — a double tap on a phone pinged them twice")

    s.section("And you cannot accept a task aimed at somebody else")
    t4 = _task(mine["id"], "check the boiler", ack="pending", directed_by=owner["id"])
    r = them.post(f"/tasks/{t4['id']}/accept")
    s.check("refused", r.status_code == 404, detail=f"HTTP {r.status_code}")
    s.check("and it is still waiting on the right person",
            _read("tasks", t4["id"], "acknowledgment_status") == "pending",
            detail="somebody else accepted a task on your behalf")

    s.section("Going on a break")
    entry = _clock_in(mine["id"])
    r = me.post("/breaks/start")
    s.check("it starts", _open_breaks(entry["id"]) == 1,
            detail=f"HTTP {r.status_code}, {_open_breaks(entry['id'])} open")

    s.section("A second tap does not open a second break")
    # Two taps on a phone with a poor signal arrive together. Overlapping
    # breaks would take the same minutes off twice.
    r = me.post("/breaks/start")
    s.check("the second is refused", r.status_code == 400, detail=f"HTTP {r.status_code}")
    s.check("and there is still exactly one open", _open_breaks(entry["id"]) == 1,
            detail=f"{_open_breaks(entry['id'])} open breaks on one shift")

    s.section("Coming back")
    me.post("/breaks/end")
    s.check("the break is closed", _open_breaks(entry["id"]) == 0)
    r = me.post("/breaks/end")
    s.check("ending twice is refused, not a crash", r.status_code < 500,
            detail=f"HTTP {r.status_code}")

    s.section("A break cannot be started without being clocked in")
    fresh = _person("NotOn")
    r = _as(fresh["id"]).post("/breaks/start")
    s.check("it is refused", r.status_code == 400, detail=f"HTTP {r.status_code}")
    conn = db()
    stray = conn.execute(
        """SELECT COUNT(*) AS c FROM breaks WHERE time_entry_id IN
           (SELECT id FROM time_entries WHERE user_id = ?)""", (fresh["id"],)).fetchone()["c"]
    conn.close()
    s.check("and nothing was written", stray == 0, detail=f"{stray} break(s)")

    s.section("The break comes off what they are paid for")
    # The only action here that changes money. If a break stops being deducted
    # the hours are wrong in the employer's favour, and quietly.
    conn = db()
    conn.execute("UPDATE time_entries SET clock_in_at = ?, clock_out_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
                  datetime.now(timezone.utc).isoformat(), entry["id"]))
    br = conn.execute("SELECT id FROM breaks WHERE time_entry_id = ?", (entry["id"],)).fetchone()
    conn.execute("UPDATE breaks SET start_at = ?, end_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
                  (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(), br["id"]))
    conn.commit()
    start = (house_today() - timedelta(days=1)).isoformat()
    end = (house_today() + timedelta(days=1)).isoformat()
    rows = m.labour_hours_by_person(conn, start, end)
    conn.close()
    ours = next((r for r in rows if r["id"] == mine["id"]), None)
    s.check("an eight hour shift with an hour's break is seven",
            ours and abs(ours["hours"] - 7.0) < 0.05,
            detail=f"got {ours['hours'] if ours else None} — the break was not "
                   "taken off the hours somebody is paid for")

    s.section("Marking a notification read")
    conn = db()
    for who in (mine["id"], theirs["id"]):
        conn.execute(
            """INSERT INTO notifications (user_id, kind, title, created_at)
               VALUES (?, 'test', ?, ?)""",
            (who, TAG + " note", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    my_note = conn.execute(
        "SELECT id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (mine["id"],)).fetchone()["id"]
    their_note = conn.execute(
        "SELECT id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (theirs["id"],)).fetchone()["id"]
    conn.close()
    me.post(f"/notifications/{my_note}/read")
    s.check("my own is marked read", _read("notifications", my_note, "read_at") is not None)

    s.section("But not somebody else's")
    me.post(f"/notifications/{their_note}/read")
    s.check("theirs is untouched", _read("notifications", their_note, "read_at") is None,
            detail="one person could clear another's notifications")

    s.section("Saying you have read the manual")
    r = me.post("/manual/acknowledge", follow_redirects=True)
    conn = db()
    ack = conn.execute(
        "SELECT acknowledged_at FROM manual_acknowledgments WHERE user_id = ?",
        (mine["id"],)).fetchone()
    conn.close()
    s.check("it is recorded against them", ack is not None, detail=f"HTTP {r.status_code}")
    first = ack["acknowledged_at"] if ack else None
    me.post("/manual/acknowledge", follow_redirects=True)
    conn = db()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM manual_acknowledgments WHERE user_id = ?",
        (mine["id"],)).fetchone()["c"]
    conn.close()
    s.check("reading it again does not add a second row", n == 1, detail=f"{n} rows")

    s.section("Signed out, none of it works")
    anon = m.app.test_client()
    t5 = _task(mine["id"], "nobody's task")
    for label, url in (("tick a task off", f"/tasks/{t5['id']}/complete"),
                       ("accept one", f"/tasks/{t5['id']}/accept"),
                       ("start a break", "/breaks/start"),
                       ("mark a notification read", f"/notifications/{my_note}/read"),
                       ("acknowledge the manual", "/manual/acknowledge")):
        code = anon.post(url).status_code
        s.check(f"a logged-out browser cannot {label}", code in (302, 401, 403),
                detail=f"HTTP {code}")
    s.check("and the task is untouched by any of it",
            _read("tasks", t5["id"], "status") == "open")

    _cleanup()
    return s
