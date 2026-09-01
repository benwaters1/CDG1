"""One action across every ticked task.

The case this is for is somebody ringing in sick. Their nine jobs have to
become somebody else's before the morning, and doing that one job at a time
was nine page loads through a menu — so in practice it was done badly or not
at all, and the work stayed in the name of the person who was not coming.

Two things carry this file.

  A REFUSAL MUST NOT HALF-APPLY. Every action here reads what it needs and
  checks it BEFORE it writes anything: a missing date, or a person who is not
  on the staff list, turns the whole thing away. A bulk action that wrote four
  rows and then complained would leave the sheet in a state nobody chose, and
  the owner with no way to know which four.

  THE PAGE MUST CARRY THE TICK BOXES. The route reads task_ids and action out
  of a form. It works perfectly well when posted to directly, which is what a
  test does — so a handover that dropped the checkboxes would leave every
  check in this file green and the feature entirely unreachable. That is not
  hypothetical: it is exactly how the promo code box on the event enquiry form
  went missing and stayed missing through a full green run.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_employee, flashes, secrets_token
import _harness

m = _harness.m
TAG = "ZZBT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", ("%" + TAG + "%",))
    conn.commit()
    conn.close()


def _second_employee():
    """A second person to hand work to, made rather than borrowed.

    Borrowing whichever employee happens to exist is how suites start
    depending on each other's leftovers — and this one needs two DIFFERENT
    people, so it cannot rely on the house having more than one.
    """
    email = TAG.lower() + ".cover@example.invalid"
    conn = db()
    try:
        row = conn.execute("SELECT id, name FROM users WHERE email = ?",
                           (email,)).fetchone()
        if row:
            return row
        from werkzeug.security import generate_password_hash
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role,
               status, created_at) VALUES (?, ?, 'employee', ?, 'General',
               'active', ?)""",
            (email, generate_password_hash(secrets_token()),
             TAG + " Cover", _harness.datetime_now()))
        conn.commit()
        return conn.execute("SELECT id, name FROM users WHERE email = ?",
                            (email,)).fetchone()
    finally:
        conn.close()


def _task(ref, *, who, due=None, status="open"):
    conn = db()
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, due_date, status,
           priority, created_at) VALUES (?, ?, ?, ?, 'normal', ?)""",
        (who, f"{TAG} {ref}", due, status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE title = ?",
                       (f"{TAG} {ref}",)).fetchone()
    conn.close()
    return row


def _get(ref):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE title = ?", (f"{TAG} {ref}",)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Tasks in bulk")
    _cleanup()
    oc, ec, _owner, emp = clients()
    cover = _second_employee()
    # The employee the harness signs in as, so "already theirs" has a real
    # owner and the handover has somewhere to go FROM.
    conn = db()
    mine = conn.execute("SELECT id, name FROM users WHERE role = 'employee' "
                        "AND id != ? ORDER BY id LIMIT 1", (cover["id"],)).fetchone()
    conn.close()
    if not mine:
        s.check("two people to hand work between", False,
                detail="the fixture makes one and needs one that already exists")
        return s

    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())

    s.section("The page carries the tick boxes and the bar")
    # Created BEFORE the page is asked for. With nothing due this week the grid
    # renders no rows, so every check below passed or failed on whether the
    # database happened to have work in it — which is not what any of them are
    # about.
    a = _task("A", who=mine["id"], due=monday.isoformat())
    b = _task("B", who=mine["id"], due=monday.isoformat())
    body = oc.get("/admin/tasks").get_data(as_text=True)
    s.check("the page opens", "Tasks" in body)
    # The rendered control, not the word. The script at the foot of the page
    # names both of these classes in its selectors, so a looser check reads its
    # own JavaScript back and passes with no checkbox on the page at all.
    s.check("rows can be ticked", 'class="task-pick" value=' in body,
            detail="the route works when posted to directly, so a page that "
                   "lost its checkboxes leaves every other check in this file "
                   "green and the feature unreachable")
    # The literal path, not url_for: that needs an application context and
    # this is checking what the browser was actually sent.
    s.check("and the bar posts to the bulk route",
            '/admin/tasks/bulk' in body,
            detail="a set of tick boxes with nothing to press is furniture")
    s.check("with a whole-day tick", 'class="task-pick-all">' in body,
            detail="the sick-cover case is the reason this exists — ticking "
                   "nine boxes by hand is barely better than nine page loads")
    # The day and week views are DIFFERENT templates, so patching one proves
    # nothing about the other.
    day_body = oc.get("/admin/tasks?view=day&date=" + monday.isoformat()
                      ).get_data(as_text=True)
    s.check("the day sheet has them too", 'class="task-pick" value=' in day_body,
            detail="the week grid and the day sheet are separate files and only "
                   "one of them was the one being looked at")
    # Positional, and deliberately so: asking whether the page contains a
    # select-all ANYWHERE is satisfied by the Unassigned card at the bottom,
    # which is present in almost any database. What matters is that it sits on
    # the card belonging to the person going off sick.
    # Their card HEADING, not their name: the bulk bar lists every employee in
    # its hand-to dropdown, so the first mention of anybody on this page is in
    # a <select> with no tick box anywhere near it.
    at = day_body.find(f"<strong>{mine['name']}</strong>")
    s.check("on the card of the person whose work it is",
            at != -1 and 'class="task-pick-all">' in day_body[at:at + 500],
            detail="the sick-cover case is a person, not a Tuesday")

    s.section("Handing somebody's work to somebody else")
    notes = []
    was_notify = m.send_notification
    m.send_notification = lambda conn, uid, kind, title, **k: notes.append((uid, title))
    try:
        r = oc.post("/admin/tasks/bulk",
                    data={"action": "reassign", "task_ids": [str(a["id"]), str(b["id"])],
                          "employee_id": str(cover["id"]), "acknowledge": "on",
                          "view": "week", "date": monday.isoformat()},
                    follow_redirects=True)
        msg = " ".join(flashes(r))
        s.check("both move", _get("A")["assigned_to_user_id"] == cover["id"]
                and _get("B")["assigned_to_user_id"] == cover["id"],
                detail=f"{msg}")
        s.check("the message names who has them now",
                cover["name"] in msg and "Moved 2 tasks" in msg, detail=f"{msg}")
        s.check("they are asked to confirm",
                _get("A")["acknowledgment_status"] == "pending",
                detail="work moved quietly onto somebody who never looks at it "
                       "is not covered, it only says it is")
        # ONE notice for the lot. Nine jobs arriving is one piece of news; nine
        # notices is a reason to stop reading notices.
        s.check("and told once, not once per task", len(notes) == 1,
                detail=f"{notes}")
        s.check("the notice says how many", notes and "2 tasks" in notes[0][1],
                detail=f"{notes}")
    finally:
        m.send_notification = was_notify

    s.section("Handing them to the person who already has them")
    r = oc.post("/admin/tasks/bulk",
                data={"action": "reassign", "task_ids": [str(a["id"])],
                      "employee_id": str(cover["id"])}, follow_redirects=True)
    msg = " ".join(flashes(r))
    s.check("is refused, and says so", "Nothing was moved" in msg,
            detail=f"{msg} — silently counting it as done is how a bulk action "
                   "comes to mean nothing")
    s.check("naming the task and the reason",
            f"{TAG} A" in msg and "already" in msg, detail=f"{msg}")

    s.section("Moving a set of tasks to another day")
    thursday = (monday + timedelta(days=3)).isoformat()
    r = oc.post("/admin/tasks/bulk",
                data={"action": "reschedule", "task_ids": [str(a["id"]), str(b["id"])],
                      "due_date": thursday}, follow_redirects=True)
    msg = " ".join(flashes(r))
    s.check("both land on the new day",
            _get("A")["due_date"] == thursday and _get("B")["due_date"] == thursday,
            detail=f"{msg}")
    s.check("and the day is named back", "Moved 2 tasks" in msg, detail=f"{msg}")
    r = oc.post("/admin/tasks/bulk",
                data={"action": "reschedule", "task_ids": [str(a["id"])],
                      "due_date": thursday}, follow_redirects=True)
    s.check("moving one to the day it is already on is refused",
            "Nothing was moved" in " ".join(flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("Ticking a set off")
    r = oc.post("/admin/tasks/bulk",
                data={"action": "complete", "task_ids": [str(a["id"]), str(b["id"])]},
                follow_redirects=True)
    s.check("both are done", _get("A")["status"] == "done"
            and _get("B")["status"] == "done", detail=f"{flashes(r)[:1]}")
    s.check("and stamped with when", bool(_get("A")["completed_at"]),
            detail="an hours figure with no completion time behind it is a guess")
    r = oc.post("/admin/tasks/bulk",
                data={"action": "complete", "task_ids": [str(a["id"])]},
                follow_redirects=True)
    msg = " ".join(flashes(r))
    s.check("doing it twice is refused rather than counted",
            "Nothing was marked done" in msg and "already done" in msg,
            detail=f"{msg}")

    s.section("A refusal must not half-apply")
    c = _task("C", who=mine["id"], due=monday.isoformat())
    d = _task("D", who=mine["id"], due=monday.isoformat())
    r = oc.post("/admin/tasks/bulk",
                data={"action": "reassign", "task_ids": [str(c["id"]), str(d["id"])],
                      "employee_id": ""}, follow_redirects=True)
    s.check("no person chosen turns the whole thing away",
            "Choose who the tasks go to" in " ".join(flashes(r)),
            detail=f"{flashes(r)[:1]}")
    s.check("and nothing moved",
            _get("C")["assigned_to_user_id"] == mine["id"]
            and _get("D")["assigned_to_user_id"] == mine["id"],
            detail="four rows written and then a complaint leaves the sheet in "
                   "a state nobody chose")
    r = oc.post("/admin/tasks/bulk",
                data={"action": "reschedule", "task_ids": [str(c["id"]), str(d["id"])],
                      "due_date": "not-a-day"}, follow_redirects=True)
    s.check("a date that is not a date turns it away",
            "Give the day" in " ".join(flashes(r)), detail=f"{flashes(r)[:1]}")
    s.check("and nothing moved", _get("C")["due_date"] == monday.isoformat(),
            detail=f"{_get('C')['due_date']}")
    r = oc.post("/admin/tasks/bulk",
                data={"action": "sell", "task_ids": [str(c["id"])]},
                follow_redirects=True)
    s.check("an action that does not exist does nothing",
            _get("C") is not None
            and "not something that can be done" in " ".join(flashes(r)),
            detail=f"{flashes(r)[:1]}")
    r = oc.post("/admin/tasks/bulk", data={"action": "delete"},
                follow_redirects=True)
    s.check("and nothing ticked is refused too",
            "Check at least one task" in " ".join(flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("A task somebody else has already dealt with")
    gone = _task("GONE", who=mine["id"], due=monday.isoformat())
    gone_id = gone["id"]
    conn = db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (gone_id,))
    conn.commit()
    conn.close()
    r = oc.post("/admin/tasks/bulk",
                data={"action": "complete", "task_ids": [str(gone_id), str(c["id"])]},
                follow_redirects=True)
    msg = " ".join(flashes(r))
    s.check("is reported, not swallowed",
            "1 of 2 tasks" in msg and "no longer there" in msg,
            detail=f"{msg} — two people working the same list is the ordinary "
                   "case, not the strange one")

    s.section("Removing a set")
    e1 = _task("E1", who=mine["id"], due=monday.isoformat())
    r = oc.post("/admin/tasks/bulk",
                data={"action": "delete", "task_ids": [str(e1["id"])]},
                follow_redirects=True)
    s.check("it goes", _get("E1") is None, detail=f"{flashes(r)[:1]}")
    conn = db()
    audit = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'tasks_bulk_deleted' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    # The one action here with no way back, so what went has to be readable
    # afterwards. A count in the flash is gone the moment the page reloads.
    s.check("and is written down with what it was",
            audit is not None and f"{TAG} E1" in (audit["details"] or ""),
            detail=f"{dict(audit) if audit else None}")

    s.section("Guards")
    left = _task("GUARD", who=mine["id"], due=monday.isoformat())
    r = ec.post("/admin/tasks/bulk",
                data={"action": "delete", "task_ids": [str(left["id"])]},
                follow_redirects=False)
    s.check("an employee cannot use it", r.status_code in (302, 403),
            detail=f"{r.status_code}")
    s.check("and the task is still there", _get("GUARD") is not None,
            detail="the guard returned a redirect and did the work anyway")

    _cleanup()
    return s
