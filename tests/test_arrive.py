"""Signing in with a PIN on a shared device, and clocking on by doing it.

This writes to the timesheet, which feeds payroll, so the checks are about
whether somebody can be put on the clock who shouldn't be: a guessable PIN, a
PIN readable from the database, a staff member setting their own, or one person
tapping in as another.

The lockout is counted per person rather than per device on purpose. Everyone
shares the château's IP on a kiosk, so an IP-based limit would let one fumbled
PIN stop the whole kitchen clocking in.
"""
from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZARR"
PIN = "4821"


def _open_shifts(user_id):
    conn = db()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM time_entries WHERE user_id = ? AND clock_out_at IS NULL",
        (user_id,)).fetchone()["c"]
    conn.close()
    return n


def _clear_lockout(user_id):
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action LIKE ?", (f"arrive_pin_fail:{user_id}",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Arrivals")
    oc, ec, owner, emp = clients()

    # Their own person, so this cannot disturb another suite's employee.
    conn = db()
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, 'x', 'employee', ?, 'Housekeeping', 'active', ?)""",
        (f"{TAG.lower()}@example.invalid", f"{TAG} Amélie", _harness.datetime_now()))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email = ?",
                       (f"{TAG.lower()}@example.invalid",)).fetchone()["id"]
    conn.execute("DELETE FROM time_entries WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    s.section("The screen is reachable without signing in")
    # It has to be: nobody is signed in yet when they walk through the door.
    kiosk = m.app.test_client()
    r = kiosk.get("/arrive")
    s.check("the arrivals screen is public", r.status_code == 200, detail=f"HTTP {r.status_code}")
    body = r.get_data(as_text=True)
    s.check("the new person is listed", f"{TAG} Amélie" in body)
    s.check("and is flagged as having no PIN", "no PIN set" in body)

    s.section("Only the owner sets PINs")
    # If staff could set their own they could set someone else's, and clock in
    # as them.
    r = ec.post(f"/directory/{uid}/set-pin", data={"pin": PIN})
    conn = db()
    unset = conn.execute("SELECT quick_pin_hash FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    s.check("an employee cannot set a PIN",
            r.status_code in (302, 403) and unset["quick_pin_hash"] is None,
            detail=f"HTTP {r.status_code}")

    r = oc.post(f"/directory/{uid}/set-pin", data={"pin": "12"}, follow_redirects=True)
    s.check("a two-digit PIN is refused", any("4 to 8" in f for f in flashes(r)), r)
    r = oc.post(f"/directory/{uid}/set-pin", data={"pin": "abcd"}, follow_redirects=True)
    s.check("a non-numeric PIN is refused", any("4 to 8" in f for f in flashes(r)), r)

    r = oc.post(f"/directory/{uid}/set-pin", data={"pin": PIN}, follow_redirects=True)
    s.check("the owner can set one", any("PIN set" in f for f in flashes(r)), r)

    s.section("The PIN is not readable from the database")
    conn = db()
    stored = conn.execute("SELECT quick_pin_hash FROM users WHERE id = ?", (uid,)).fetchone()[0]
    conn.close()
    # A readable PIN is a PIN that lets anyone clock in as anyone.
    #
    # Asserted as three properties rather than as "the PIN is not a substring of
    # the stored value". That proxy failed about one run in seventy: a scrypt
    # record is a salt plus a hundred and twenty-eight hex characters, and a
    # four-digit decimal PIN turns up inside that by chance often enough to
    # train somebody to re-run the suite instead of reading it. It is also the
    # wrong question — a reversible encoding of the PIN would pass it.
    from werkzeug.security import check_password_hash
    s.check("something is stored", bool(stored), detail=f"{stored!r}")
    s.check("it is not the PIN itself", stored != PIN, detail=f"{stored!r}")
    s.check("it is a recognised password hash",
            bool(stored) and "$" in stored and stored.split("$")[0].split(":")[0]
            in ("scrypt", "pbkdf2", "argon2"),
            detail=f"{(stored or '')[:40]!r} — a format nothing can verify is not "
                   "a hash, it is a string somebody hopes is one")
    s.check("and it verifies against the PIN, so it really is that PIN hashed",
            bool(stored) and check_password_hash(stored, PIN),
            detail="stored, but not a hash of what was typed")
    s.check("while refusing a different one",
            bool(stored) and not check_password_hash(stored, "0000"),
            detail="it verifies anything, so it verifies nothing")

    s.section("Arriving signs them in and starts their shift")
    s.check("they are not on the clock yet", _open_shifts(uid) == 0)
    door = m.app.test_client()
    r = door.post(f"/arrive/{uid}", data={"pin": PIN}, follow_redirects=True)
    s.check("the right PIN gets them in", any("clocked in" in f for f in flashes(r)), r)
    with door.session_transaction() as sess:
        s.check("and they are signed in as themselves", sess.get("user_id") == uid,
                detail=f"session holds {sess.get('user_id')!r}")
    s.check("a shift is now running", _open_shifts(uid) == 1, detail=f"{_open_shifts(uid)} open")

    s.section("Tapping in twice does not open a second shift")
    # Two overlapping entries would double their hours, and an impossible shift
    # blocks the payroll export for everybody.
    again = m.app.test_client()
    again.post(f"/arrive/{uid}", data={"pin": PIN}, follow_redirects=True)
    s.check("still exactly one open shift", _open_shifts(uid) == 1,
            detail=f"{_open_shifts(uid)} open")

    s.section("Guessing is stopped, per person")
    _clear_lockout(uid)
    guess = m.app.test_client()
    blocked_at = None
    for attempt in range(1, 9):
        rr = guess.post(f"/arrive/{uid}", data={"pin": f"000{attempt}"}, follow_redirects=True)
        if any("Too many" in f for f in flashes(rr)):
            blocked_at = attempt
            break
    s.check("repeated wrong PINs lock the person out", blocked_at is not None,
            detail="eight guesses went through unthrottled")
    if blocked_at:
        print(f"       (locked after {blocked_at} attempts)")
        # The correct PIN must also be refused while locked, or the lockout is
        # decoration.
        r = guess.post(f"/arrive/{uid}", data={"pin": PIN}, follow_redirects=True)
        s.check("and the right PIN is refused while locked out",
                any("Too many" in f for f in flashes(r)), r)

        # One person's lockout must not stop anyone else clocking in — the whole
        # reason this is counted per person and not per device.
        conn = db()
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, status, created_at)
               VALUES (?, 'x', 'employee', ?, 'active', ?)""",
            (f"{TAG.lower()}2@example.invalid", f"{TAG} Other", _harness.datetime_now()))
        conn.commit()
        other = conn.execute("SELECT id FROM users WHERE email = ?",
                             (f"{TAG.lower()}2@example.invalid",)).fetchone()["id"]
        conn.close()
        oc.post(f"/directory/{other}/set-pin", data={"pin": "9753"}, follow_redirects=True)
        r = guess.post(f"/arrive/{other}", data={"pin": "9753"}, follow_redirects=True)
        s.check("somebody else can still arrive on the same device",
                any("clocked in" in f for f in flashes(r)), r,
                detail="one person's lockout blocked the whole kiosk")

    s.section("Resetting the PIN clears the lockout")
    oc.post(f"/directory/{uid}/set-pin", data={"pin": "5566"}, follow_redirects=True)
    fresh = m.app.test_client()
    r = fresh.post(f"/arrive/{uid}", data={"pin": "5566"}, follow_redirects=True)
    s.check("a forgotten PIN can be reset and used at once",
            any("clocked in" in f or "already clocked in" in f for f in flashes(r)), r)

    s.section("Someone with no PIN cannot be clocked in")
    oc.post(f"/directory/{uid}/set-pin", data={"pin": ""}, follow_redirects=True)
    r = m.app.test_client().post(f"/arrive/{uid}", data={"pin": ""}, follow_redirects=True)
    s.check("an empty PIN is refused", not any("clocked in" in f for f in flashes(r)), r,
            detail=f"{flashes(r)[:1]}")

    conn = db()
    ids = [r["id"] for r in conn.execute("SELECT id FROM users WHERE name LIKE ?",
                                         (TAG + "%",)).fetchall()]
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM time_entries WHERE user_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM notifications WHERE user_id IN ({marks})", ids)
        # Signing in writes an audit entry naming the actor, which is a foreign
        # key onto users. Detach it rather than deleting the entry — an audit log
        # records what happened, and tidying up after a test is not a reason to
        # erase that.
        conn.execute(f"UPDATE audit_log SET actor_user_id = NULL "
                     f"WHERE actor_user_id IN ({marks})", ids)
        conn.execute("DELETE FROM submission_log")
        conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    conn.commit()
    conn.close()
    return s
