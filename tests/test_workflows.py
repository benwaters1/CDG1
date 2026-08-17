"""The mutation paths, not just page rendering.

The route sweep proves pages load. This drives each module's real state
machine end to end and asserts the database actually changed — a page can
render perfectly while the button on it does nothing.

Everything created is tagged ZZWF and deleted at the end.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWF"
TODAY = datetime.now(timezone.utc).date()
# Deliberately clear of the seeded workshop sessions and bookings. The app
# correctly refuses dates held for a workshop, which is right behaviour and
# would read here as a failure.
SOON = (TODAY + timedelta(days=330)).isoformat()
SOON_END = (TODAY + timedelta(days=332)).isoformat()


def run():
    s = Suite("Workflows")
    oc, ec, owner, emp = clients()
    pub = m.app.test_client()

    conn = db()
    room = conn.execute("SELECT id, name FROM rooms WHERE active=1 LIMIT 1").fetchone()
    conn.close()
    if not room or not emp:
        s.check("a room and an employee exist to test with", False,
                detail="test database has no active room or no employee")
        return s

    s.section("Room booking: public request, owner confirms")
    pub.post(f"/book/{room['id']}", data={
        "guest_name": f"{TAG} Guest", "guest_email": f"{TAG.lower()}@example.invalid",
        "arrival_date": SOON, "departure_date": SOON_END, "party_size": "2",
        "agree_terms": "on",
    }, follow_redirects=True)
    conn = db()
    bk = conn.execute("SELECT * FROM bookings WHERE guest_name LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("a public request creates a pending booking", bk and bk["status"] == "pending",
            detail=f"got {bk['status'] if bk else 'no row'}")
    if bk:
        oc.post(f"/admin/bookings/{bk['id']}/confirm", follow_redirects=True)
        conn = db()
        after = conn.execute("SELECT status, linked_guest_id FROM bookings WHERE id=?",
                             (bk["id"],)).fetchone()
        conn.close()
        s.check("confirming flips it to confirmed", after["status"] == "confirmed",
                detail=f"got {after['status']}")
        # guests is a profile table, bookings hold the stay — confirming should
        # attach the stay to a person rather than duplicating them.
        s.check("confirming links or creates a guest profile",
                after["linked_guest_id"] is not None)

    s.section("Restaurant: public booking, owner confirms")
    conn = db()
    settings = conn.execute("SELECT enabled FROM restaurant_settings LIMIT 1").fetchone()
    was_enabled = settings["enabled"] if settings else 0
    if not was_enabled:
        conn.execute("UPDATE restaurant_settings SET enabled = 1")
        conn.commit()
    conn.close()
    try:
        pub.post("/restaurant/book", data={
            "guest_name": f"{TAG} Diner", "guest_email": f"{TAG.lower()}d@example.invalid",
            "dinner_date": SOON, "party_size": "2",
        }, follow_redirects=True)
        conn = db()
        dinner = conn.execute("SELECT * FROM restaurant_bookings WHERE guest_name LIKE ?",
                              (TAG + "%",)).fetchone()
        conn.close()
        s.check("a public dinner request creates a row", dinner is not None)
        if dinner:
            oc.post(f"/admin/restaurant/{dinner['id']}/confirm", follow_redirects=True)
            conn = db()
            st = conn.execute("SELECT status FROM restaurant_bookings WHERE id=?",
                              (dinner["id"],)).fetchone()["status"]
            conn.close()
            s.check("confirming flips the dinner to confirmed", st == "confirmed",
                    detail=f"got {st}")
    finally:
        if not was_enabled:
            conn = db()
            conn.execute("UPDATE restaurant_settings SET enabled = 0")
            conn.commit()
            conn.close()

    s.section("Tasks: create and cycle")
    oc.post("/admin/tasks/new", data={
        "title": f"{TAG} task", "assigned_to_user_id": str(emp["id"]),
        "due_date": TODAY.isoformat(), "priority": "normal",
    }, follow_redirects=True)
    conn = db()
    task = conn.execute("SELECT * FROM tasks WHERE title LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can create a task", task is not None)
    if task:
        seen = []
        for _ in range(3):
            ec.post(f"/tasks/{task['id']}/toggle")
            conn = db()
            seen.append(conn.execute("SELECT status FROM tasks WHERE id=?",
                                     (task["id"],)).fetchone()["status"])
            conn.close()
        s.check("it cycles open -> in_progress -> done -> open",
                seen == ["in_progress", "done", "open"], detail=f"got {seen}")

    s.section("Leave: employee requests, owner approves")
    ec.post("/leave", data={"start_date": SOON, "end_date": SOON_END,
                            "reason": f"{TAG} holiday"}, follow_redirects=True)
    conn = db()
    leave = conn.execute("SELECT * FROM leave_requests WHERE reason LIKE ?",
                         (TAG + "%",)).fetchone()
    conn.close()
    s.check("employee can request leave", leave is not None)
    if leave:
        oc.post(f"/admin/leave/{leave['id']}/decide", data={"status": "approved"},
                follow_redirects=True)
        conn = db()
        st = conn.execute("SELECT status FROM leave_requests WHERE id=?",
                          (leave["id"],)).fetchone()["status"]
        conn.close()
        s.check("the approval is recorded", st == "approved", detail=f"got {st}")

    s.section("Expenses: employee submits, owner approves")
    ec.post("/expenses/submit", data={"description": f"{TAG} taxi", "amount": "42.50",
                                      "vendor_name": "Taxi Co"}, follow_redirects=True)
    conn = db()
    expense = conn.execute("SELECT * FROM expenses WHERE description LIKE ?",
                           (TAG + "%",)).fetchone()
    conn.close()
    s.check("employee can submit an expense", expense is not None)
    if expense:
        oc.post(f"/expenses/{expense['id']}/decide", data={"status": "approved"},
                follow_redirects=True)
        conn = db()
        st = conn.execute("SELECT status FROM expenses WHERE id=?",
                          (expense["id"],)).fetchone()["status"]
        conn.close()
        s.check("the approval is recorded", st == "approved", detail=f"got {st}")

    s.section("Room issues: report and resolve")
    ec.post("/room-issues/new", data={"room_id": str(room["id"]),
                                      "title": f"{TAG} dripping tap",
                                      "description": "drips overnight"}, follow_redirects=True)
    conn = db()
    issue = conn.execute("SELECT * FROM room_issues WHERE title LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("staff can report a room issue", issue is not None)
    if issue:
        oc.post(f"/room-issues/{issue['id']}/resolve", follow_redirects=True)
        conn = db()
        st = conn.execute("SELECT status FROM room_issues WHERE id=?",
                          (issue["id"],)).fetchone()["status"]
        conn.close()
        s.check("resolving it sticks", st == "resolved", detail=f"got {st}")

    s.section("An employee cannot reach an owner mutation")
    if task:
        resp = ec.post(f"/admin/tasks/{task['id']}/delete")
        conn = db()
        still = conn.execute("SELECT COUNT(*) c FROM tasks WHERE id=?",
                             (task["id"],)).fetchone()["c"]
        conn.close()
        # Both halves matter: a 403 that still deleted the row would pass on
        # status alone, and so would a redirect that quietly performed it.
        s.check("employee cannot delete a task, and the row survives",
                still == 1 and resp.status_code in (302, 403),
                detail=f"status={resp.status_code} rows={still}")

    conn = db()
    for sql in [
        "DELETE FROM bookings WHERE guest_name LIKE ?",
        "DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
        "DELETE FROM tasks WHERE title LIKE ?",
        "DELETE FROM leave_requests WHERE reason LIKE ?",
        "DELETE FROM expenses WHERE description LIKE ?",
        "DELETE FROM room_issues WHERE title LIKE ?",
        "DELETE FROM guests WHERE name LIKE ?",
    ]:
        try:
            conn.execute(sql, (TAG + "%",))
        except Exception:
            pass
    try:
        conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    except Exception:
        pass
    conn.commit()
    conn.close()
    return s
