"""Asking a room guest how it was.

Workshop guests have been asked since the beginning. Room guests never were —
and rooms are most of what the house sells. The form, the table and the page
have existed the whole time; nothing pointed anybody at them, so the feedback
on the site is entirely from ateliers.

What the job has to get right is mostly about who it does NOT ask:

  - somebody who has already left feedback, or the ask reads as not having
    been read;
  - somebody on the do-not-write list, because a review request is still a
    message they said they did not want;
  - a stay that was never confirmed;
  - and nobody twice, which is the same stamp-and-commit-per-row shape the
    texting jobs use and for the same two reasons.

None of those is an error. A job that treated an unaskable guest as a failure
would report one every morning, and a report that always says something wrong
is a report nobody reads.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTFB"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM guest_feedback WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _stay(ref, left_days_ago=3, status="confirmed"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    left = datetime.now(m.LOCAL_TZ).date() - timedelta(days=left_days_ago)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Amelie Fontaine', ?, ?, ?, 2, ?, 400, ?)""",
        (room, TAG + ref, TAG.lower() + "tok" + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid",
         (left - timedelta(days=2)).isoformat(), left.isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _asked(ref):
    conn = db()
    try:
        return conn.execute(
            "SELECT feedback_requested_at FROM bookings WHERE reference_code = ?",
            (TAG + ref,)).fetchone()["feedback_requested_at"]
    finally:
        conn.close()


def _held(ref):
    conn = db()
    try:
        return conn.execute("SELECT * FROM email_outbox WHERE to_address = ?",
                            (f"{TAG.lower()}{ref.lower()}@example.invalid",)).fetchall()
    finally:
        conn.close()


def _run(days_after=None):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.run_room_feedback_job(conn, days_after)
    finally:
        conn.commit()
        conn.close()


def run():
    s = Suite("How was your stay")
    _cleanup()
    oc, _ec, _owner, _emp = clients()

    s.section("A guest who has just been")
    _stay("HAPPY")
    _stay("TOOSOON", left_days_ago=0)          # left today
    _stay("LONGAGO", left_days_ago=30)         # too late to ask about
    _stay("PENDING", status="pending")         # never actually stayed
    said = _run()
    s.check("they are asked", bool(_asked("HAPPY")), detail=str(said))
    s.check("and the message is written to them", len(_held("HAPPY")) == 1,
            detail=str(len(_held("HAPPY"))))
    body = _held("HAPPY")[0]["body"] if _held("HAPPY") else ""
    s.check("it links to the form they can actually fill in",
            "/feedback/" in body, detail=body[:90])
    s.check("using their first name", "Amelie" in body and "Fontaine" not in body,
            detail=body[:70])
    s.check("somebody who left today is not asked yet", not _asked("TOOSOON"))
    s.check("nor somebody who left a month ago", not _asked("LONGAGO"),
            detail="the window is a day, not everything before it — otherwise "
                   "switching this on writes to every guest in the history")
    s.check("and a stay that never happened is not asked at all",
            not _asked("PENDING"))

    s.section("Nobody twice")
    before = len(_held("HAPPY"))
    _run()
    s.check("a second run writes nothing more", len(_held("HAPPY")) == before,
            detail=f"{len(_held('HAPPY')) - before} extra")

    s.section("And nobody who has already told us")
    told = _stay("TOLD")
    conn = db()
    conn.execute(
        """INSERT INTO guest_feedback (booking_id, guest_name, rating, comment,
           submitted_at) VALUES (?, 'Amelie', 5, 'Lovely', ?)""",
        (told["id"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    _run()
    s.check("a guest who already left a review is not asked for one",
            not _asked("TOLD"),
            detail="asking after they have written reads as not having read it")

    s.section("Nor anybody who asked not to be written to")
    quiet = _stay("QUIET")
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
                 (quiet["guest_email"], "unsubscribed",
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    _run()
    s.check("they are not asked", not _asked("QUIET"),
            detail="a review request is still a message they said they did "
                   "not want")
    s.check("and nothing was written to them", len(_held("QUIET")) == 0)

    s.section("An empty morning is not a problem")
    said = _run(days_after=17)
    s.check("it says so plainly", "nobody" in said.lower(), detail=str(said))
    s.check("and does not call it an error",
            "error" not in said.lower() and "fail" not in said.lower(),
            detail=str(said))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
