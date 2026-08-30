"""The four writes a stranger can reach without logging in.

Everything else that writes to this database is behind @login_required or
@owner_required, and is exercised by the suite that owns its feature. These
four are not: anyone who can reach the site can POST to them, and until now
nothing checked what they do with what they are sent.

The interesting cases are all the refusals. A public form that accepts junk
does not error and does not look broken -- it writes a row that reads fine on
the admin page and is wrong in a way nobody finds until the day it matters.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztest-pw-"


def _iso(days):
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_waitlist WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_waitlist WHERE email LIKE ?", (TAG + "%",))
    conn.execute(
        """DELETE FROM workshop_feedback WHERE workshop_booking_id IN
             (SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)""",
        (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'join_%'")
    conn.commit()


def _unthrottle(conn):
    """The limiter is five an hour per IP and every case below posts as the
    same IP, so without this the later checks would be testing the rate limit
    instead of the thing they name."""
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'join_%'")
    conn.commit()


def _count(conn, table, email):
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE email = ?",
                        (email,)).fetchone()["c"]


def run():
    s = Suite("public writes")
    clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()
    anon = m.app.test_client()

    s.section("Joining the restaurant waitlist")
    good = TAG + "diner@example.invalid"
    _unthrottle(conn)
    r = anon.post("/restaurant/waitlist/join", data={
        "name": "A Diner", "email": good, "desired_date": _iso(20),
        "party_size": "4"}, follow_redirects=False)
    # Asserted on the redirect rather than on the page it lands on. The form
    # lives on /restaurant/book, which correctly 404s while online dinner
    # booking is switched off in settings -- so following the redirect would
    # be testing a config flag, not this handler.
    s.check("a good request is accepted and sent back to the booking page",
            r.status_code == 302 and "/restaurant/book" in (r.headers.get("Location") or ""),
            detail=f"HTTP {r.status_code} -> {r.headers.get('Location')}")
    row = conn.execute("SELECT * FROM restaurant_waitlist WHERE email = ?", (good,)).fetchone()
    s.check("and lands on the waitlist", row is not None)
    s.check("open, so the job that reaches people can see it",
            row is not None and row["status"] == "open",
            detail=None if row is None else row["status"])
    s.check("with the party size it was given",
            row is not None and row["party_size"] == 4,
            detail=None if row is None else str(row["party_size"]))

    # The documented one. A date that never parsed used to be stored as typed,
    # which put "next Friday please" in a date column: the guest is on the
    # waitlist, the page thanks them, and the one job that exists to reach
    # them matches on `desired_date = ?` and never will.
    junk = TAG + "vague@example.invalid"
    _unthrottle(conn)
    r = anon.post("/restaurant/waitlist/join", data={
        "name": "Vague", "email": junk, "desired_date": "next Friday please"},
        follow_redirects=True)
    s.check("a date nobody can parse is refused", _count(conn, "restaurant_waitlist", junk) == 0,
            detail="row written with an unparseable date")
    s.check("and the guest is told why, not thanked",
            any("valid date" in f for f in flashes(r)), detail=str(flashes(r)))

    bad = TAG + "notanemail"
    _unthrottle(conn)
    anon.post("/restaurant/waitlist/join", data={"name": "Typo", "email": bad},
              follow_redirects=True)
    s.check("an address that is not one is refused",
            _count(conn, "restaurant_waitlist", bad) == 0)

    _unthrottle(conn)
    nameless = TAG + "nameless@example.invalid"
    anon.post("/restaurant/waitlist/join", data={"name": "", "email": nameless},
              follow_redirects=True)
    s.check("and so is a request with no name",
            _count(conn, "restaurant_waitlist", nameless) == 0)

    s.section("It cannot be used to fill the table")
    # A public form that writes a row is a public form that can be scripted.
    _unthrottle(conn)
    flood = TAG + "flood@example.invalid"
    for _ in range(9):
        anon.post("/restaurant/waitlist/join", data={
            "name": "Flood", "email": flood}, follow_redirects=True)
    got = _count(conn, "restaurant_waitlist", flood)
    s.check("the limiter stops it well short of nine", got < 9, detail=f"{got} rows written")
    s.check("but let the honest ones through first", got > 0, detail=f"{got} rows written")

    s.section("Joining a workshop waitlist")
    conn.execute(
        "INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
        (TAG + "Pottery", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (TAG + "Pottery",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 6, ?, ?)""",
        (wid, _iso(30), _iso(33), TAG + "sess", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (TAG + "sess",)).fetchone()["id"]
    conn.commit()

    joiner = TAG + "potter@example.invalid"
    _unthrottle(conn)
    anon.post("/workshops/waitlist/join", data={
        "session_id": str(sid), "name": "A Potter", "email": joiner, "party_size": "2"},
        follow_redirects=True)
    s.check("a good request joins the session's list",
            _count(conn, "workshop_waitlist", joiner) == 1)

    # A session id is a number in a URL-shaped form field, so it is the first
    # thing anybody pokes at.
    _unthrottle(conn)
    r = anon.post("/workshops/waitlist/join", data={
        "session_id": "999999", "name": "Nobody", "email": TAG + "no@example.invalid"})
    s.check("a session that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")
    _unthrottle(conn)
    r = anon.post("/workshops/waitlist/join", data={
        "session_id": "' OR 1=1 --", "name": "Nobody", "email": TAG + "no@example.invalid"})
    s.check("and so is a session id that is not a number", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    _unthrottle(conn)
    wbad = TAG + "alsonotanemail"
    anon.post("/workshops/waitlist/join", data={
        "session_id": str(sid), "name": "Typo", "email": wbad}, follow_redirects=True)
    s.check("an address that is not one is refused here too",
            _count(conn, "workshop_waitlist", wbad) == 0)

    s.section("Leaving feedback on a workshop")
    # The token is the only credential, so what it does with a wrong one, and
    # with a right one at the wrong time, is the whole of the check.
    def _booking(token, ref, end_days):
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
               VALUES (?, ?, ?, 6, ?, ?)""",
            (wid, _iso(end_days - 3), _iso(end_days), TAG + "sess", now))
        s2 = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ? ORDER BY id DESC",
                          (TAG + "sess",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                 guest_name, guest_email, party_size, status, created_at)
               VALUES (?, ?, ?, 'A Guest', ?, 1, 'confirmed', ?)""",
            (s2, ref, token, TAG + "g@example.invalid", now))
        conn.commit()
        return conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                            (ref,)).fetchone()["id"]

    done_id = _booking(TAG + "tok-done", TAG + "DONE", -5)
    early_id = _booking(TAG + "tok-early", TAG + "EARLY", 40)

    r = anon.get("/workshops/feedback/" + TAG + "nosuchtoken")
    s.check("an unknown token is a 404, not a form", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    def _fb(bid):
        return conn.execute(
            "SELECT COUNT(*) AS c FROM workshop_feedback WHERE workshop_booking_id = ?",
            (bid,)).fetchone()["c"]

    anon.post("/workshops/feedback/" + TAG + "tok-early",
              data={"rating": "5", "comment": "loved it"}, follow_redirects=True)
    s.check("feedback on a workshop that has not happened is not stored",
            _fb(early_id) == 0, detail=f"{_fb(early_id)} rows")

    anon.post("/workshops/feedback/" + TAG + "tok-done",
              data={"rating": "9", "comment": "out of range"}, follow_redirects=True)
    s.check("a rating outside 1-5 is refused", _fb(done_id) == 0, detail=f"{_fb(done_id)} rows")

    anon.post("/workshops/feedback/" + TAG + "tok-done",
              data={"rating": "4", "comment": "very good"}, follow_redirects=True)
    s.check("a real rating is stored", _fb(done_id) == 1, detail=f"{_fb(done_id)} rows")

    anon.post("/workshops/feedback/" + TAG + "tok-done",
              data={"rating": "1", "comment": "changed my mind"}, follow_redirects=True)
    s.check("and the same guest cannot rate it twice", _fb(done_id) == 1,
            detail=f"{_fb(done_id)} rows")
    kept = conn.execute(
        "SELECT rating FROM workshop_feedback WHERE workshop_booking_id = ?",
        (done_id,)).fetchone()
    s.check("the first one is the one kept, not the last",
            kept is not None and kept["rating"] == 4,
            detail=None if kept is None else str(kept["rating"]))

    s.section("Backing out of a workshop payment")
    r = anon.get("/workshops/stripe-cancel/" + TAG + "tok-done", follow_redirects=False)
    s.check("it sends the guest back to their own booking",
            r.status_code == 302 and (TAG + "tok-done") in (r.headers.get("Location") or ""),
            detail=f"HTTP {r.status_code} -> {r.headers.get('Location')}")
    # Nothing was paid, so nothing may be recorded as paid.
    paid = conn.execute(
        "SELECT deposit_paid_at, balance_paid_at FROM workshop_bookings WHERE id = ?",
        (done_id,)).fetchone()
    s.check("and a cancelled payment marks nothing as paid",
            paid is not None and not paid["deposit_paid_at"] and not paid["balance_paid_at"],
            detail=str(tuple(paid)) if paid else "no booking")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
