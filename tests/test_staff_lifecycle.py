"""Deactivating somebody, and handing a shift to somebody else.

Four routes on the thinnest-covered side of the app, none of them tested:
marking an employee inactive, an employee answering a swap offer, the owner
deciding it, and copying last week's rota forward.

The one that matters most is deactivation, because it is the closest thing
this app has to revoking access. Somebody who has left — or been asked to
leave — should stop being able to open the app, and "stop" has to mean the
phone already in their pocket, not just the next time they try to sign in.

The swap checks are about who is allowed to move a shift. A shift is who is
expected at the château on a given day, so a swap that could be answered by
the wrong person, or approved twice, moves real cover.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZLIFE"
# Far enough out that no real rota can occupy these days.
BASE = date(2031, 4, 7)          # a Monday


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM shift_swaps WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE shift_date >= ? AND shift_date < ?",
                 (BASE.isoformat(), (BASE + timedelta(days=21)).isoformat()))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _employee(suffix):
    """A second employee, so a swap has somebody to be offered to."""
    from werkzeug.security import generate_password_hash
    conn = db()
    email = f"{TAG.lower()}{suffix}@example.invalid"
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, ?, 'employee', ?, 'General', 'active', ?)""",
        (email, generate_password_hash("not-used-for-login"), f"{TAG} {suffix}",
         _harness.datetime_now()))
    conn.commit()
    row = conn.execute("SELECT id, name FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row


def _shift(user_id, day):
    conn = db()
    cur = conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at)
           VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (user_id, day.isoformat(), f"{TAG} cover", _harness.datetime_now()))
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def _swap(shift_id, from_user, to_user, status="pending"):
    conn = db()
    cur = conn.execute(
        """INSERT INTO shift_swaps (shift_id, requested_by_user_id, offered_to_user_id,
           status, note, requested_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (shift_id, from_user, to_user, status, f"{TAG} please cover", _harness.datetime_now()))
    swap_id = cur.lastrowid
    conn.commit()
    conn.close()
    return swap_id


def run():
    s = Suite("Staff lifecycle")
    oc, ec, owner, emp = clients()
    if not emp:
        s.check("an employee exists", False, detail="none in the database")
        return s
    _cleanup()
    second = _employee("b")

    s.section("Marking somebody inactive")
    r = oc.post(f"/directory/{second['id']}/toggle-status", follow_redirects=True)
    conn = db()
    row = conn.execute("SELECT status FROM users WHERE id = ?", (second["id"],)).fetchone()
    conn.close()
    s.check("they are marked inactive", row["status"] == "inactive", detail=f"got {row['status']}")
    # Leaving is a process, not a flag — the checklist is what actually gets
    # keys and logins back.
    conn = db()
    items = conn.execute("SELECT COUNT(*) AS c FROM offboarding_items WHERE user_id = ?",
                         (second["id"],)).fetchone()["c"]
    conn.close()
    s.check("an offboarding checklist is started for them", items > 0, detail=f"got {items}")

    s.section("Their session stops working, not just their next login")
    # A phone already signed in is the case that matters: somebody who has been
    # asked to leave still has the app open in their pocket.
    gone = m.app.test_client()
    with gone.session_transaction() as sess:
        sess["user_id"] = second["id"]
    r = gone.get("/today")
    s.check("an already-signed-in session is turned away",
            r.status_code in (301, 302) and "/login" in r.headers.get("Location", ""),
            detail=f"HTTP {r.status_code} → {r.headers.get('Location')!r}")
    s.check("and current_user no longer resolves them",
            _resolves_to_none(second["id"]),
            detail="an inactive account still resolves to a user")

    s.section("And back again")
    oc.post(f"/directory/{second['id']}/toggle-status", follow_redirects=True)
    conn = db()
    row = conn.execute("SELECT status FROM users WHERE id = ?", (second["id"],)).fetchone()
    conn.close()
    s.check("reactivating restores them", row["status"] == "active", detail=f"got {row['status']}")
    back = m.app.test_client()
    with back.session_transaction() as sess:
        sess["user_id"] = second["id"]
    s.check("and they can use the app again", back.get("/today").status_code == 200)

    s.section("Only an owner can do it, and only to an employee")
    r = ec.post(f"/directory/{second['id']}/toggle-status")
    conn = db()
    unchanged = conn.execute("SELECT status FROM users WHERE id = ?",
                             (second["id"],)).fetchone()["status"]
    conn.close()
    s.check("an employee cannot deactivate a colleague",
            unchanged == "active", detail=f"got {unchanged}")
    r = oc.post(f"/directory/{owner['id']}/toggle-status")
    conn = db()
    owner_status = conn.execute("SELECT status FROM users WHERE id = ?",
                                (owner['id'],)).fetchone()["status"]
    conn.close()
    # The route only matches role='employee'; locking out the only owner would
    # leave nobody able to let anyone back in.
    s.check("and the owner cannot be deactivated through it",
            r.status_code == 404 and owner_status == "active",
            detail=f"HTTP {r.status_code}, status {owner_status}")

    s.section("Answering a swap offer")
    shift_id = _shift(emp["id"], BASE + timedelta(days=2))
    swap_id = _swap(shift_id, emp["id"], second["id"])
    # The offer is to `second`, so the original employee must not be able to
    # accept on their behalf.
    r = ec.post(f"/shifts/swaps/{swap_id}/respond", data={"status": "accepted"})
    conn = db()
    still = conn.execute("SELECT status FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()["status"]
    conn.close()
    s.check("somebody the shift was not offered to cannot answer it",
            r.status_code == 404 and still == "pending",
            detail=f"HTTP {r.status_code}, status {still}")

    other = m.app.test_client()
    with other.session_transaction() as sess:
        sess["user_id"] = second["id"]
    r = other.post(f"/shifts/swaps/{swap_id}/respond", data={"status": "accepted"},
                   follow_redirects=True)
    conn = db()
    swap = conn.execute("SELECT * FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()
    conn.close()
    s.check("the person it was offered to can accept", swap["status"] == "accepted",
            detail=f"got {swap['status']}")
    s.check("and when they answered is recorded", bool(swap["responded_at"]))
    # Answering twice would let a declined offer be flipped after the fact.
    r = other.post(f"/shifts/swaps/{swap_id}/respond", data={"status": "declined"})
    conn = db()
    after = conn.execute("SELECT status FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()["status"]
    conn.close()
    s.check("answering a second time is refused", after == "accepted", detail=f"got {after}")
    r = other.post(f"/shifts/swaps/{swap_id}/respond", data={"status": "maybe"})
    s.check("and a status nobody defined is rejected", r.status_code == 400,
            detail=f"HTTP {r.status_code}")

    s.section("The owner decides, and the shift actually moves")
    r = oc.post(f"/admin/shifts/swaps/{swap_id}/decide", data={"status": "approved"},
                follow_redirects=True)
    conn = db()
    swap = conn.execute("SELECT * FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()
    owner_of_shift = conn.execute("SELECT user_id FROM shifts WHERE id = ?",
                                  (shift_id,)).fetchone()["user_id"]
    conn.close()
    s.check("the swap is approved", swap["status"] == "approved", detail=f"got {swap['status']}")
    s.check("and the shift now belongs to the person who took it",
            owner_of_shift == second["id"],
            detail=f"shift is on user {owner_of_shift}, expected {second['id']}")

    # Deciding twice is the race the route guards with its rowcount check —
    # two approvals would reassign an already-reassigned shift.
    r = oc.post(f"/admin/shifts/swaps/{swap_id}/decide", data={"status": "rejected"})
    conn = db()
    final = conn.execute("SELECT status FROM shift_swaps WHERE id = ?", (swap_id,)).fetchone()["status"]
    conn.close()
    s.check("a second decision is refused", r.status_code == 404 and final == "approved",
            detail=f"HTTP {r.status_code}, status {final}")

    s.section("A swap nobody accepted cannot be approved")
    pending_shift = _shift(emp["id"], BASE + timedelta(days=3))
    pending_swap = _swap(pending_shift, emp["id"], second["id"])
    r = oc.post(f"/admin/shifts/swaps/{pending_swap}/decide", data={"status": "approved"})
    conn = db()
    who = conn.execute("SELECT user_id FROM shifts WHERE id = ?", (pending_shift,)).fetchone()["user_id"]
    conn.close()
    s.check("approving straight past the employee is refused",
            r.status_code == 404 and who == emp["id"],
            detail=f"HTTP {r.status_code}, shift on user {who}")

    s.section("Copying last week's rota forward")
    _cleanup()
    second = _employee("b")
    for offset in (0, 1, 4):
        _shift(second["id"], BASE + timedelta(days=offset))
    next_week = BASE + timedelta(days=7)
    oc.post("/admin/shifts/copy-previous", data={"date": next_week.isoformat()},
            follow_redirects=True)
    conn = db()
    copied = conn.execute(
        "SELECT COUNT(*) AS c FROM shifts WHERE shift_date >= ? AND shift_date < ?",
        (next_week.isoformat(), (next_week + timedelta(days=7)).isoformat())).fetchone()["c"]
    conn.close()
    s.check("all three shifts land in the new week", copied == 3, detail=f"got {copied}")

    # Running it twice is the realistic accident — a second click, or two
    # people doing it. Double cover on every day would be quietly expensive.
    oc.post("/admin/shifts/copy-previous", data={"date": next_week.isoformat()},
            follow_redirects=True)
    conn = db()
    again = conn.execute(
        "SELECT COUNT(*) AS c FROM shifts WHERE shift_date >= ? AND shift_date < ?",
        (next_week.isoformat(), (next_week + timedelta(days=7)).isoformat())).fetchone()["c"]
    conn.close()
    s.check("copying a second time adds nothing", again == 3, detail=f"got {again}")

    conn = db()
    weekday_kept = conn.execute(
        "SELECT shift_date FROM shifts WHERE shift_date >= ? AND shift_date < ? ORDER BY shift_date",
        (next_week.isoformat(), (next_week + timedelta(days=7)).isoformat())).fetchall()
    conn.close()
    s.check("each shift keeps its day of the week",
            [r["shift_date"] for r in weekday_kept] ==
            [(next_week + timedelta(days=o)).isoformat() for o in (0, 1, 4)],
            detail=f"got {[r['shift_date'] for r in weekday_kept]}")

    _cleanup()
    return s


def _resolves_to_none(user_id):
    """Whether current_user() refuses an inactive account."""
    with m.app.test_request_context():
        from flask import session as flask_session
        flask_session["user_id"] = user_id
        return m.current_user() is None
