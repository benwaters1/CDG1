"""Calling something off, by the person entitled to call it off.

Four more routes the suite reached and never got an answer out of. Two of
them carry a permission check that lives INSIDE the query rather than in a
decorator:

    SELECT * FROM leave_requests WHERE id = ? AND user_id = ?
    SELECT * FROM tasks         WHERE id = ? AND assigned_to_user_id = ?

Nothing above them says @owner_required, so the whole of "you may only cancel
your own time off" and "you may only reject a task given to you" is that
second clause. A decorator that goes missing is loud; a WHERE clause that
goes missing is a page that keeps working and quietly lets anybody cancel
anybody. Neither direction had been run: every test of these posted an id
that does not exist and read the 404.

The two admin cancellations are guarded the other way -- on the STATUS, so a
booking already called off cannot be called off twice, and a second submit
(or a refreshed tab, which is how this actually happens) gets a 404 rather
than a second decided_at and a second message to the guest.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZCAN"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.commit()


def run():
    s = Suite("calling it off, by the right person")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today()

    def rowid():
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def status_of(table, row_id):
        row = conn.execute(f"SELECT status FROM {table} WHERE id = ?",
                           (row_id,)).fetchone()
        return row["status"] if row else None

    # ------------------------------------------------------ admin cancels
    s.section("A table called off, and not called off twice")
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token,
                   guest_name, guest_email, dinner_date, party_size, status,
                   total_price, payment_status, created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'confirmed', 0, 'unpaid', ?)""",
        (TAG + "RES", (TAG + "res").lower(), TAG + " Diner",
         f"{TAG}.d@example.invalid".lower(),
         (today + timedelta(days=20)).isoformat(), now))
    res_id = rowid()
    conn.commit()
    oc.post(f"/admin/restaurant/{res_id}/cancel", follow_redirects=True)
    s.check("it is cancelled",
            status_of("restaurant_bookings", res_id) == "cancelled")
    r = oc.post(f"/admin/restaurant/{res_id}/cancel", follow_redirects=False)
    s.check("and a second submit is refused", r.status_code == 404,
            detail=f"status {r.status_code} — a refreshed tab must not send "
                   "the guest a second message")

    s.section("A workshop place called off, and not called off twice")
    ws = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()
    if ws:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code,
                       manage_token, guest_name, guest_email, party_size,
                       status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 0, ?)""",
            (ws["id"], TAG + "WSB", (TAG + "wsb").lower(), TAG + " Maker",
             f"{TAG}.m@example.invalid".lower(), now))
        wsb = rowid()
        conn.commit()
        oc.post(f"/admin/workshops/registrations/{wsb}/cancel",
                follow_redirects=True)
        s.check("it is cancelled",
                status_of("workshop_bookings", wsb) == "cancelled")
        r = oc.post(f"/admin/workshops/registrations/{wsb}/cancel",
                    follow_redirects=False)
        s.check("and a second submit is refused", r.status_code == 404,
                detail=f"status {r.status_code}")
    else:
        s.check("a workshop session exists", False,
                detail="reported rather than skipped")

    # ------------------------------------------------------------ own leave
    s.section("Time off is cancelled by the person who asked for it")

    def make_leave(user_id, tag):
        conn.execute(
            """INSERT INTO leave_requests (user_id, start_date, end_date,
                       leave_type, reason, status, requested_at)
               VALUES (?, ?, ?, 'holiday', ?, 'pending', ?)""",
            (user_id, (today + timedelta(days=60)).isoformat(),
             (today + timedelta(days=62)).isoformat(), TAG + tag, now))
        return rowid()

    mine = make_leave(emp["id"], " mine")
    theirs = make_leave(owner["id"], " theirs")
    conn.commit()

    ec.post(f"/leave/{mine}/cancel", follow_redirects=True)
    s.check("my own request is cancelled",
            status_of("leave_requests", mine) == "cancelled")

    r = ec.post(f"/leave/{theirs}/cancel", follow_redirects=False)
    s.check("somebody else's is refused", r.status_code == 404,
            detail=f"status {r.status_code}")
    s.check("and is left standing",
            status_of("leave_requests", theirs) == "pending",
            detail="the whole of that permission is the user_id clause in "
                   "one SELECT; nothing above the route says it")

    r = ec.post(f"/leave/{mine}/cancel", follow_redirects=False)
    s.check("and cancelling twice is refused too", r.status_code == 404,
            detail=f"status {r.status_code}")

    # ------------------------------------------------------------- own task
    s.section("A task is rejected by the person it was given to")

    def make_task(assigned, tag, directed_by=None):
        conn.execute(
            """INSERT INTO tasks (assigned_to_user_id, title, priority, status,
                       acknowledgment_status, directed_by_user_id, created_at)
               VALUES (?, ?, 'normal', 'open', 'pending', ?, ?)""",
            (assigned, TAG + tag, directed_by, now))
        return rowid()

    my_task = make_task(emp["id"], " my task", directed_by=owner["id"])
    their_task = make_task(owner["id"], " their task")
    conn.commit()

    r = ec.post(f"/tasks/{my_task}/reject")
    s.check("it is rejected", r.status_code == 200,
            detail=f"status {r.status_code} {r.get_data(as_text=True)[:60]}")
    s.check("and recorded as rejected",
            conn.execute(
                "SELECT acknowledgment_status FROM tasks WHERE id = ?",
                (my_task,)).fetchone()["acknowledgment_status"] == "rejected")
    s.check("whoever asked for it is told",
            conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? "
                "AND related_task_id = ?",
                (owner["id"], my_task)).fetchone()[0] == 1,
            detail="a task refused in silence is one the owner still thinks "
                   "is being done")

    r = ec.post(f"/tasks/{their_task}/reject")
    s.check("somebody else's task cannot be rejected", r.status_code == 404,
            detail=f"status {r.status_code}")
    s.check("and it is untouched",
            conn.execute(
                "SELECT acknowledgment_status FROM tasks WHERE id = ?",
                (their_task,)).fetchone()["acknowledgment_status"] == "pending")

    r = ec.post(f"/tasks/{my_task}/reject")
    s.check("and rejecting twice is refused", r.status_code == 404,
            detail=f"status {r.status_code}")
    s.check("with no second notification",
            conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? "
                "AND related_task_id = ?",
                (owner["id"], my_task)).fetchone()[0] == 1)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
