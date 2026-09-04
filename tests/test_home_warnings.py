"""The checks reach the owner without being remembered.

Six pages each answer a real question, and each of them has to be opened.
A check nobody looks at is worth nothing — "we built a page for that" is
not the same as anybody finding out.

The tests that matter here are the negative ones. A warnings panel that
cannot be empty is noise, and a panel that fires on things nobody can act
on trains the owner to scroll past the one that counts.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-hw-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _warnings(conn, today):
    # url_for needs a request context. In the app this only ever runs inside
    # one (it builds the owner home), so this mirrors reality rather than
    # working around anything.
    with m.app.test_request_context():
        return m.owner_home_warnings(conn, today)


def _titles(conn, today):
    return [w["title"] for w in _warnings(conn, today)]


def run():
    s = Suite("home warnings")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(m.LOCAL_TZ).date()

    # The panel carries a line about photographs the site loads from a server
    # the house does not control, and under test the copies live in a fresh
    # empty directory -- so it would be on every list this suite builds and the
    # panel could never be empty, which is the one thing "The panel can be
    # empty" below exists to prove. Silenced by saying the site has no such
    # photograph, which is the end state that feature is working towards
    # anyway. Put back at the end: this is a module global and every suite in
    # the run shares it.
    real_hotlinked = m.hotlinked_urls
    m.hotlinked_urls = lambda: []
    try:
        return _run(s, oc, conn, now, today)
    finally:
        m.hotlinked_urls = real_hotlinked


def _run(s, oc, conn, now, today):

    s.section("A clash reaches the front page")
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', 'employee', 'Rostered', 'General', 'active', ?)""",
        (TAG + "a@example.invalid", now))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email = ?",
                       (TAG + "a@example.invalid",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
           leave_type, status, requested_at)
           VALUES (?, ?, ?, ?, 'annual', 'approved', ?)""",
        (uid, _iso(3), _iso(5), TAG + "off", now))
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
           created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (uid, _iso(4), TAG + "shift", now))
    conn.commit()

    titles = _titles(conn, today)
    s.check("being rostered while on leave is surfaced",
            "Rostered while away" in titles, detail=str(titles))
    warn = next(w for w in _warnings(conn, today)
                if w["title"] == "Rostered while away")
    s.check("it names the person rather than only a count",
            "Rostered" in warn["detail"], detail=warn["detail"])
    s.check("and links to the page that explains it",
            warn["href"].endswith("/rota-clashes"), detail=warn["href"])

    s.section("A clash outside the fortnight is not shouted about")
    # Real, but not urgent. A homepage listing everything gets scrolled past.
    conn.execute("UPDATE shifts SET shift_date = ? WHERE role_note = ?",
                 (_iso(40), TAG + "shift"))
    conn.execute("UPDATE leave_requests SET start_date = ?, end_date = ? WHERE reason = ?",
                 (_iso(39), _iso(41), TAG + "off"))
    conn.commit()
    s.check("a clash five weeks out stays off the front page",
            "Rostered while away" not in _titles(conn, today))
    # ...but it is still on the page that lists them, so it is not lost.
    s.check("it is still on the clashes page itself",
            any(c["user_id"] == uid for c in m.rota_conflicts(conn, today, _iso(60))))

    s.section("A guest already in a faulty room outranks one arriving later")
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO room_issues (room_id, title, status, created_at)
           VALUES (?, ?, 'open', ?)""", (room, TAG + "broken shutter", now))
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Inroom', 'g@example.invalid', ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "NOW", TAG + "tk", _iso(-1), _iso(2), now))
    conn.commit()
    faults = [w for w in _warnings(conn, today)
              if w["title"] == "Rooms sold with an open fault"]
    s.check("the fault is surfaced", bool(faults))
    s.check("it is a blocker while somebody is in the room",
            faults and faults[0]["severity"] == "blocker")
    s.check("and it says the guest is in there now",
            faults and "now" in faults[0]["detail"], detail=str(faults[0]["detail"]))

    s.section("The panel can be empty")
    # The check that stops this becoming furniture. If it can never be empty,
    # it stops meaning anything and the owner learns to skip it.
    _cleanup(conn)
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target, created_at) "
        "VALUES (NULL, 'backup_auto_sent', 'test', ?)", (now,))
    # An unanswered poor review is one of the things the panel carries, and it
    # IS something wrong — so "nothing is wrong" has to include none of those
    # outstanding. Settled rather than deleted: those rows belong to whichever
    # suite made them, and reaching in to remove them would be this test
    # tidying somebody else's fixtures to make its own claim true.
    conn.execute(
        "UPDATE guest_feedback SET acknowledged_at = ? WHERE acknowledged_at IS NULL",
        (now,))
    conn.execute(
        "UPDATE workshop_feedback SET acknowledged_at = ? WHERE acknowledged_at IS NULL",
        (now,))
    conn.commit()
    left = _titles(conn, today)
    s.check("with nothing wrong, nothing is listed", left == [], detail=str(left))

    s.section("A missing backup is the loudest thing on it")
    conn.execute("DELETE FROM audit_log WHERE action IN "
                 "('backup_auto_sent', 'backup_downloaded')")
    conn.commit()
    ws = _warnings(conn, today)
    s.check("no backup arriving is surfaced",
            any(w["title"] == "No backup is arriving" for w in ws), detail=str(_titles(conn, today)))
    s.check("and it is a blocker",
            all(w["severity"] == "blocker" for w in ws if w["title"] == "No backup is arriving"))

    s.section("The page renders it")
    page = oc.get("/").get_data(as_text=True)
    s.check("the panel is on the owner home", "Nobody has told you this" in page)
    s.check("and the hero line says something needs looking at",
            "needs looking at" in page or "need looking at" in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
