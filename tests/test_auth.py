"""Signing in, out, and the things that must not work.

This had almost no coverage, which is the wrong place to have none: every other
protection in the app is downstream of "is this really them". The checks are
mostly negative — a wrong password must fail, an inactive account must fail,
guessing must get slower, and a logged-out browser must reach nothing.

CSRF is force-enabled for one section here. The harness turns it off so suites
can post without juggling tokens, which means nothing else in the suite would
notice if protection disappeared entirely.
"""
import re

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZAUTH"
PASSWORD = "correct horse battery staple"


def _make_user(email, password=PASSWORD, role="employee", status="active"):
    from werkzeug.security import generate_password_hash
    conn = db()
    conn.execute("DELETE FROM users WHERE email = ?", (email,))
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, ?, ?, ?, 'General', ?, ?)""",
        (email, generate_password_hash(password), role, f"{TAG} Person", status,
         _harness.datetime_now()))
    conn.commit()
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row["id"]


def run():
    s = Suite("Authentication")
    clients()
    email = f"{TAG.lower()}@example.invalid"
    user_id = _make_user(email)

    s.section("Signing in")
    c = m.app.test_client()
    r = c.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=True)
    s.check("the right password gets in", r.status_code == 200 and "/login" not in r.request.path,
            r, detail=f"landed on {r.request.path}")
    with c.session_transaction() as sess:
        s.check("and a session is set", sess.get("user_id") == user_id,
                detail=f"got {sess.get('user_id')!r}")

    s.section("Things that must not work")
    bad = m.app.test_client()
    r = bad.post("/login", data={"email": email, "password": "wrong"}, follow_redirects=True)
    with bad.session_transaction() as sess:
        s.check("a wrong password does not sign in", sess.get("user_id") is None,
                detail=f"session had {sess.get('user_id')!r}")
    # The message must not reveal whether the address exists.
    r2 = bad.post("/login", data={"email": "nobody@example.invalid", "password": "wrong"},
                  follow_redirects=True)
    same = set(flashes(r)) == set(flashes(r2))
    s.check("a wrong password and an unknown address say the same thing", same,
            detail=f"{flashes(r)[:1]} vs {flashes(r2)[:1]}")

    inactive_email = f"{TAG.lower()}.inactive@example.invalid"
    _make_user(inactive_email, status="inactive")
    ic = m.app.test_client()
    ic.post("/login", data={"email": inactive_email, "password": PASSWORD}, follow_redirects=True)
    with ic.session_transaction() as sess:
        s.check("an inactive account cannot sign in", sess.get("user_id") is None,
                detail=f"session had {sess.get('user_id')!r}")

    s.section("Guessing gets stopped")
    def clear_login_limits():
        # Two mechanisms: submission_log is the generic per-IP budget, and
        # login_throttle is a real 15-minute lockout after repeated failures.
        # Clearing only the first leaves the account locked and the next check
        # fails for the wrong reason.
        conn = db()
        conn.execute("DELETE FROM submission_log")
        conn.execute("DELETE FROM login_throttle")
        conn.commit()
        conn.close()

    clear_login_limits()
    guess = m.app.test_client()
    blocked_at = None
    for attempt in range(1, 16):
        rr = guess.post("/login", data={"email": email, "password": f"guess{attempt}"},
                        follow_redirects=True)
        if any("Too many" in f for f in flashes(rr)):
            blocked_at = attempt
            break
    s.check("repeated wrong passwords are rate-limited", blocked_at is not None,
            detail="fifteen guesses went through unthrottled")
    if blocked_at:
        print(f"       (throttled after {blocked_at} attempts)")
        # A positive control: the limiter must be refusing *because* of the
        # guessing, not refusing everything — otherwise this check passes on a
        # broken app that rejects valid logins too.
        clear_login_limits()
        fresh = m.app.test_client()
        fresh.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=True)
        with fresh.session_transaction() as sess:
            s.check("and a correct password still works afterwards",
                    sess.get("user_id") == user_id)

    s.section("Signing out")
    out = m.app.test_client()
    out.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=True)
    out.get("/logout", follow_redirects=True)
    with out.session_transaction() as sess:
        s.check("logout clears the session", sess.get("user_id") is None)
    r = out.get("/today", follow_redirects=False)
    s.check("and a logged-out browser is sent to the login page",
            r.status_code == 302 and "login" in (r.headers.get("Location") or ""),
            detail=f"HTTP {r.status_code} -> {r.headers.get('Location')}")

    s.section("Nothing private is reachable without signing in")
    anon = m.app.test_client()
    leaks = []
    for path in ("/today", "/chat", "/guests", "/admin/payroll", "/management/vault",
                 "/admin/access-levels", "/directory", "/admin/bookings", "/expenses"):
        code = anon.get(path).status_code
        if code == 200:
            leaks.append(f"{path} -> 200")
    s.check("every private page redirects or refuses", not leaks, detail="; ".join(leaks))

    s.section("Changing a password")
    cp = m.app.test_client()
    cp.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=True)
    r = cp.post("/password", data={"current_password": "not it",
                                  "new_password": "a-new-one-entirely",
                                  "confirm_password": "a-new-one-entirely"},
                follow_redirects=True)
    conn = db()
    unchanged = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    from werkzeug.security import check_password_hash
    s.check("the wrong current password does not change it",
            check_password_hash(unchanged["password_hash"], PASSWORD), r)

    s.section("CSRF protection is really on")
    # The harness disables it so suites can post freely, so without forcing it
    # back on here nothing in the suite would notice if it were removed.
    m.app.config["WTF_CSRF_ENABLED"] = True
    try:
        conn = db()
        conn.execute("""INSERT INTO tasks (title, assigned_to_user_id, due_date, priority,
                        status, created_at) VALUES (?, ?, date('now'), 'normal', 'open', ?)""",
                     (f"{TAG} csrf target", user_id, _harness.datetime_now()))
        conn.commit()
        task_id = conn.execute("SELECT id FROM tasks WHERE title = ?",
                               (f"{TAG} csrf target",)).fetchone()["id"]
        conn.close()

        guard = m.app.test_client()
        with guard.session_transaction() as sess:
            sess["user_id"] = user_id
        r = guard.post(f"/tasks/{task_id}/complete")
        conn = db()
        after = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        # handle_csrf_error redirects with "session timed out" rather than a raw
        # 400 — a stale tab is the usual cause, not an attack. So what matters
        # is that the task did NOT change, not which code came back.
        s.check("a POST with no token changes nothing",
                after["status"] == "open" and r.status_code != 200,
                detail=f"HTTP {r.status_code}, task is now {after['status']}")
    finally:
        m.app.config["WTF_CSRF_ENABLED"] = False

    s.section("Password reset does not leak who has an account")
    fp = m.app.test_client()
    known = fp.post("/forgot-password", data={"email": email}, follow_redirects=True)
    unknown = fp.post("/forgot-password", data={"email": "ghost@example.invalid"},
                      follow_redirects=True)
    s.check("both addresses get the same answer",
            set(flashes(known)) == set(flashes(unknown)),
            detail=f"{flashes(known)[:1]} vs {flashes(unknown)[:1]}")
    r = fp.get("/reset-password/not-a-real-token", follow_redirects=True)
    s.check("a bogus reset token is refused",
            r.status_code in (200, 302, 404) and "new password" not in
            r.get_data(as_text=True).lower(),
            detail=f"HTTP {r.status_code}")

    conn = db()
    conn.execute("DELETE FROM users WHERE email LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM submission_log")
    conn.execute("DELETE FROM login_throttle")
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
