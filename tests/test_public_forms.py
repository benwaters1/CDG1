"""The forms a stranger can post to without an account.

Three of them: the two waitlists and the workshop feedback page. They are
grouped because of what they have in common — no login, a public URL, and a
row written into the house's database from whatever arrives.

The one that had a real fault is the restaurant waitlist. `desired_date` was
stored as whatever string was posted and never parsed, which is the same fault
submit_event_inquiry had and fixed. It matters more here, because
matching_restaurant_waitlist_entries finds people with `desired_date = ?` and a
real ISO date: "next Friday please" goes into a date column, the guest is on
the waitlist, the page thanks them, and the one job that exists to reach them
can never see them again. Nothing errors at any point.

The workshop waitlist does not have it, because it keys on a session_id that is
checked for being a number and for existing. Worth stating as a check rather
than as a coincidence.

What the rest of this holds:

  - Both waitlists are rate limited, and the limit is logged even when the
    submission is refused, or the window drifts and the limit stops meaning
    anything.
  - A refusal is a refusal and not a crash. Everything here is reachable by
    anybody, so a 500 is the cheapest denial of service in the app.
  - Workshop feedback, like the room version, cannot be left before the
    session has finished or twice for the same booking.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZTPUB"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM restaurant_waitlist WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_waitlist WHERE name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_feedback WHERE workshop_booking_id IN
                    (SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", ("%waitlist%",))
    for action in ("join_restaurant_waitlist", "join_workshop_waitlist"):
        conn.execute("DELETE FROM submission_log WHERE action = ?", (action,))
    conn.commit()
    conn.close()


def _clear_limit(action):
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = ?", (action,))
    conn.commit()
    conn.close()


def _waitlist(name):
    conn = db()
    try:
        return conn.execute("SELECT * FROM restaurant_waitlist WHERE name = ?",
                            (TAG + " " + name,)).fetchone()
    finally:
        conn.close()


def _session_id():
    conn = db()
    try:
        row = conn.execute(
            """SELECT ws.id AS sid FROM workshop_sessions ws
               JOIN workshops w ON w.id = ws.workshop_id LIMIT 1""").fetchone()
        return row["sid"] if row else None
    finally:
        conn.close()


def _workshop_booking(ref, ended_days_ago=5):
    """A confirmed workshop booking whose session has already finished."""
    conn = db()
    end = house_today() - timedelta(days=ended_days_ago)
    ws = conn.execute("SELECT id FROM workshops LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
           capacity, created_at) VALUES (?, ?, ?, 8, ?)""",
        (ws, (end - timedelta(days=2)).isoformat(), end.isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, 'Maker', 'maker@example.invalid', 1, 'confirmed', 300, ?)""",
        (sid, TAG + ref, TAG + "wtok" + ref, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Forms a stranger can post to")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    soon = (house_today() + timedelta(days=21)).isoformat()

    s.section("The dinner waitlist keeps a date it can use")
    # Every refusal here redirects to /restaurant/book, which 404s while the
    # restaurant is switched off in this database — so following the redirect
    # measures the gate rather than the route, and "it did not fall over" reads
    # as a 404 whatever the route did. Switch it on for the section and put the
    # setting back, exactly as test_form_prefill does.
    conn = db()
    was_enabled = conn.execute(
        "SELECT enabled FROM restaurant_settings LIMIT 1").fetchone()["enabled"]
    conn.execute("UPDATE restaurant_settings SET enabled = 1")
    conn.commit()
    conn.close()
    s.check("the page these forms return to is reachable",
            anon.get("/restaurant/book").status_code == 200,
            detail="otherwise every refusal below reads as a 404")
    _clear_limit("join_restaurant_waitlist")
    anon.post("/restaurant/waitlist/join", data={
        "name": TAG + " Wanted", "email": "wanted@example.invalid",
        "phone": "+33 6 00 00 00 00", "desired_date": soon, "party_size": "4",
        "notes": "anniversary",
    }, follow_redirects=True)
    row = _waitlist("Wanted")
    s.check("a request is recorded", row is not None)
    s.check("with the date they asked for", row and row["desired_date"] == soon,
            detail=str(row["desired_date"]) if row else "")
    s.check("and it starts open", row and row["status"] == "open")

    # The check this section exists for. A date the app cannot read must not
    # reach the column, because the notifier matches on it exactly.
    r = anon.post("/restaurant/waitlist/join", data={
        "name": TAG + " Vague", "email": "vague@example.invalid",
        "desired_date": "next Friday please", "party_size": "2",
    }, follow_redirects=True)
    s.check("a date the app cannot read is refused", _waitlist("Vague") is None,
            detail="it would sit in a date column and match nothing for ever")
    s.check("and the page says so rather than falling over", r.status_code == 200,
            detail=f"HTTP {r.status_code} — a 500 writes nothing either")
    s.check("telling them what to do instead",
            any("date" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    # ...and the consequence, stated directly: the guest who gave a real date
    # is findable by the job that reaches them.
    conn = db()
    found = [e["name"] for e in m.matching_restaurant_waitlist_entries(conn, soon)]
    conn.close()
    s.check("the one with a real date is found when a table frees up",
            TAG + " Wanted" in found, detail=str(found[:4]))

    # No date at all is fine — they are on the list for whenever.
    anon.post("/restaurant/waitlist/join", data={
        "name": TAG + " Anytime", "email": "anytime@example.invalid",
    }, follow_redirects=True)
    s.check("leaving the date blank is still allowed", _waitlist("Anytime") is not None,
            detail="refusing a blank would turn a fix into a worse bug")

    s.section("And will not take a request it cannot answer")
    # "There was a flash" is not a refusal: the success message is a flash too,
    # and a first version of this asserted only that SOME flash came back —
    # which stayed green with the required-fields guard removed entirely. So
    # each case counts the rows and reads the wording.
    def _rows():
        conn = db()
        try:
            return conn.execute("SELECT COUNT(*) c FROM restaurant_waitlist").fetchone()["c"]
        finally:
            conn.close()

    for label, data, wanted in (
            ("no name", {"email": "x@example.invalid"}, "required"),
            ("no email", {"name": TAG + " Nameless"}, "required"),
            ("an email that is not one",
             {"name": TAG + " Bad", "email": "not-an-email"}, "valid")):
        _clear_limit("join_restaurant_waitlist")
        before_rows = _rows()
        r = anon.post("/restaurant/waitlist/join", data=data, follow_redirects=True)
        s.check(f"a request with {label} writes nothing", _rows() == before_rows,
                detail=f"{_rows() - before_rows} row(s) written")
        s.check(f"and is told why, not thanked, on {label}",
                any(wanted in f.lower() for f in flashes(r)), detail=str(flashes(r)))
        s.check(f"and does not fall over on {label}", r.status_code == 200,
                detail=f"HTTP {r.status_code}")

    conn = db()
    conn.execute("UPDATE restaurant_settings SET enabled = ?", (was_enabled,))
    conn.commit()
    conn.close()

    s.section("The workshop waitlist keys on a session, not a date")
    sid = _session_id()
    _clear_limit("join_workshop_waitlist")
    anon.post("/workshops/waitlist/join", data={
        "session_id": str(sid), "name": TAG + " Maker",
        "email": "maker@example.invalid", "party_size": "2",
    }, follow_redirects=True)
    conn = db()
    w = conn.execute("SELECT * FROM workshop_waitlist WHERE name = ?",
                     (TAG + " Maker",)).fetchone()
    conn.close()
    s.check("a request is recorded against the session", w and w["session_id"] == sid,
            detail=str(w["session_id"]) if w else "no row")
    # This is why it has no equivalent of the date fault: the id is checked
    # for being a number AND for existing, so nothing unusable reaches the row.
    s.check("a session that is not a number is a 404",
            anon.post("/workshops/waitlist/join",
                      data={"session_id": "tuesday", "name": TAG + " X",
                            "email": "x@example.invalid"}).status_code == 404)
    s.check("and one that does not exist is too",
            anon.post("/workshops/waitlist/join",
                      data={"session_id": "99999999", "name": TAG + " Y",
                            "email": "y@example.invalid"}).status_code == 404)
    conn = db()
    ghosts = conn.execute(
        "SELECT COUNT(*) c FROM workshop_waitlist WHERE name IN (?, ?)",
        (TAG + " X", TAG + " Y")).fetchone()["c"]
    conn.close()
    s.check("neither wrote a row", ghosts == 0, detail=str(ghosts))

    s.section("Neither can be submitted without limit")
    # Public, unauthenticated and writing rows: without a limit these are a
    # way to fill the house's database from a laptop.
    _clear_limit("join_restaurant_waitlist")
    limit = m.BOOKING_RATE_LIMIT_PER_HOUR
    for i in range(limit + 2):
        anon.post("/restaurant/waitlist/join", data={
            "name": f"{TAG} Flood{i}", "email": f"flood{i}@example.invalid",
        }, follow_redirects=True)
    conn = db()
    got = conn.execute("SELECT COUNT(*) c FROM restaurant_waitlist WHERE name LIKE ?",
                       (TAG + " Flood%",)).fetchone()["c"]
    conn.close()
    s.check("the limit stops the flood", got <= limit,
            detail=f"{got} rows written for {limit + 2} attempts, limit {limit}")
    s.check("and it let the honest ones through first", got > 0,
            detail="a limit that refuses everybody is not a limit")

    # The refused attempts still have to be logged, or the window slides and
    # the limit stops meaning anything.
    conn = db()
    logged = conn.execute(
        "SELECT COUNT(*) c FROM submission_log WHERE action = 'join_restaurant_waitlist'"
    ).fetchone()["c"]
    conn.close()
    s.check("every attempt is logged, refused ones included",
            logged >= limit + 2, detail=f"{logged} logged for {limit + 2} attempts")

    s.section("Workshop feedback, after the session and only once")
    wb = _workshop_booking("FEEDBACK")
    r = anon.get(f"/workshops/feedback/{wb['manage_token']}")
    s.check("the form opens with the booking's own token", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("a token that is not a token is a 404",
            anon.get("/workshops/feedback/not-a-real-token").status_code == 404)
    s.check("and the page is not for search engines",
            "noindex" in r.get_data(as_text=True))

    anon.post(f"/workshops/feedback/{wb['manage_token']}",
              data={"rating": "5", "comment": TAG + " wonderful"}, follow_redirects=True)
    conn = db()
    rows = conn.execute(
        "SELECT * FROM workshop_feedback WHERE workshop_booking_id = ?",
        (wb["id"],)).fetchall()
    conn.close()
    s.check("a guest whose session has finished can leave it", len(rows) == 1,
            detail=str(len(rows)))
    s.check("with the rating they gave", rows and rows[0]["rating"] == 5,
            detail=str(rows[0]["rating"]) if rows else "")

    second = anon.post(f"/workshops/feedback/{wb['manage_token']}",
                       data={"rating": "1", "comment": TAG + " second thoughts"},
                       follow_redirects=True)
    conn = db()
    rows = conn.execute(
        "SELECT * FROM workshop_feedback WHERE workshop_booking_id = ?",
        (wb["id"],)).fetchall()
    conn.close()
    # Two guards that mean the same thing: the route checks for an existing
    # review and a unique index on workshop_booking_id enforces it underneath,
    # exactly as the room version does. Removing the route's check leaves the
    # count right, so its control passes — the invariant holds and only the
    # message degrades. That is the benign shape.
    s.check("and cannot leave a second", len(rows) == 1, detail=str(len(rows)))
    s.check("the first one stands", rows and rows[0]["rating"] == 5,
            detail=str(rows[0]["rating"]) if rows else "")
    s.check("and the second attempt is declined rather than thrown",
            second.status_code == 200, detail=f"HTTP {second.status_code}")

    future = _workshop_booking("TOOSOON", ended_days_ago=-14)
    anon.post(f"/workshops/feedback/{future['manage_token']}",
              data={"rating": "5", "comment": TAG + " too early"}, follow_redirects=True)
    conn = db()
    early = conn.execute(
        "SELECT COUNT(*) c FROM workshop_feedback WHERE workshop_booking_id = ?",
        (future["id"],)).fetchone()["c"]
    conn.close()
    s.check("nothing is recorded before the session has happened", early == 0,
            detail=str(early))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
