"""The morning note, and reaching the house from the account page.

Two things the owner and the guest respectively had no way to do.

THE MORNING NOTE. Everything in it was already on a screen. The point is that
it arrives rather than waiting to be looked up — the difference between knowing
who is coming today and remembering to check. It reuses owner_home_warnings
rather than asking the same questions again, because two lists of what needs
attention eventually disagree and the one in the email is the one nobody
corrects.

It does NOT send on a genuinely empty day. That is the same lesson the warnings
panel carries: a note that arrives every morning saying nothing becomes
furniture, and furniture is what you stop reading on the morning it matters.

REACHING THE HOUSE. The account page listed every stay, dinner and workshop a
guest had and offered no way to say anything about any of it — the only message
box in the app is inside one specific booking, which is the wrong shape for a
question about the next stay or about two at once.

The address is taken from the session the link was issued to and never from the
form. Nothing about who is writing is on trust; only the words are.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTDIG"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_sessions WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE 'A guest wrote in%'")
    conn.execute("DELETE FROM audit_log WHERE action = 'guest_wrote_in' AND target LIKE ?",
                 (TAG.lower() + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'guest_account_message'")
    conn.commit()
    conn.close()


def _stay(ref, arrive_offset, depart_offset, name="Amelie Fontaine"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    today = datetime.now(m.LOCAL_TZ).date()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + ref, TAG.lower() + "tok" + ref, TAG + " " + name,
         f"{TAG.lower()}{ref.lower()}@example.invalid",
         (today + timedelta(days=arrive_offset)).isoformat(),
         (today + timedelta(days=depart_offset)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _digest(day=None):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.morning_digest(conn, day or datetime.now(m.LOCAL_TZ).date())
    finally:
        conn.close()


def run():
    s = Suite("The morning note, and asking us something")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    today = datetime.now(m.LOCAL_TZ).date()

    s.section("What today looks like")
    _stay("IN", 0, 3)                    # arriving today
    _stay("OUT", -3, 0, name="Bernard Roux")   # leaving today
    _stay("MID", -1, 2, name="Claire Weber")   # in the house, neither
    subject, body, anything = _digest()

    s.check("there is something to say", anything is True)
    s.check("the arrival is named", TAG + " Amelie Fontaine" in body, detail=body[:200])
    s.check("and the departure", TAG + " Bernard Roux" in body, detail=body[:250])
    # The one in the middle should be counted, not listed — they are not doing
    # anything today, and a list of everybody in the house is the calendar.
    s.check("somebody mid-stay is not listed as arriving or leaving",
            body.count(TAG + " Claire Weber") == 0, detail=body[:300])
    s.check("but is counted as in the house", "In the house tonight" in body,
            detail=body[:300])
    s.check("the subject says the shape of the day at a glance",
            "in" in subject and "out" in subject, detail=subject)

    s.section("A quiet day is not worth a message")
    # The warnings panel already carries this lesson: something that arrives
    # every morning saying nothing stops being read.
    conn = db()
    quiet_day = next(
        (today + timedelta(days=n) for n in range(400, 800)
         if not conn.execute(
             """SELECT 1 FROM bookings WHERE status = 'confirmed'
                AND (arrival_date = ? OR departure_date = ?)""",
             ((today + timedelta(days=n)).isoformat(),
              (today + timedelta(days=n)).isoformat())).fetchone()),
        None)
    conn.close()
    s.check("there is a day with nothing on to test against", quiet_day is not None)
    subject, body, anything = _digest(quiet_day)
    s.check("nothing is happening", anything is False, detail=subject)
    s.check("and the note says so in plain words",
            "Nothing arriving" in body, detail=body[:200])

    conn = db()
    with m.app.test_request_context():
        said = m.run_morning_digest_job(conn, quiet_day)
    conn.commit()
    conn.close()
    s.check("so nothing is sent", "nothing sent" in said, detail=str(said))

    conn = db()
    with m.app.test_request_context():
        said = m.run_morning_digest_job(conn, today)
    conn.commit()
    conn.close()
    s.check("while a day with something on does send", said.startswith("sent"),
            detail=str(said))

    s.section("What needs attention comes from one place")
    # Not asked twice. Two lists of what is wrong eventually disagree, and the
    # one in the email is the one nobody corrects.
    conn = db()
    with m.app.test_request_context():
        panel = [w["title"] for w in m.owner_home_warnings(conn, today)]
    conn.close()
    _subject, body, _a = _digest()
    missing = [t for t in panel if t not in body]
    s.check("everything the home page flags is in the note", not missing,
            detail=str(missing[:3]))

    s.section("A guest asking us something")
    conn = db()
    email = TAG.lower() + "in@example.invalid"
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO guest_sessions (email, token, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (email, TAG.lower() + "session", now.isoformat(),
         (now + timedelta(hours=48)).isoformat()))
    conn.commit()
    conn.close()

    page = anon.get(f"/my-account/{TAG.lower()}session")
    s.check("the account page opens", page.status_code == 200,
            detail=str(page.status_code))
    s.check("and now offers a way to write to us",
            "Ask us something" in page.get_data(as_text=True))

    r = anon.post(f"/my-account/{TAG.lower()}session",
                  data={"message": "we will be late on the Friday"},
                  follow_redirects=True)
    # posting to the page itself does nothing; the form has its own address
    r = anon.post(f"/my-account/{TAG.lower()}session/ask",
                  data={"message": TAG + " we will be late on the Friday"},
                  follow_redirects=True)
    conn = db()
    written = conn.execute(
        "SELECT * FROM email_outbox WHERE subject LIKE 'A guest wrote in%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    logged = conn.execute(
        "SELECT * FROM audit_log WHERE action = 'guest_wrote_in' AND target = ?",
        (email,)).fetchone()
    conn.close()
    s.check("it reaches the house", written is not None, detail=str(flashes(r)))
    s.check("carrying what they wrote",
            written and TAG + " we will be late" in (written["body"] or ""),
            detail=(written["body"] or "")[:80] if written else "")
    # The address comes from the session, never the form — otherwise the page
    # is a way to send mail from somebody else's address.
    s.check("and the address the link was issued to, not one they typed",
            written and email in (written["body"] or ""),
            detail=(written["body"] or "")[:120] if written else "")
    s.check("it is on the record too", logged is not None)
    s.check("and they are told it arrived",
            any("thank you" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    r = anon.post(f"/my-account/{TAG.lower()}session/ask", data={"message": "   "},
                  follow_redirects=True)
    s.check("an empty message is refused",
            any("write something" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and the page survives it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.check("a token that is not a token cannot write to us",
            anon.post("/my-account/not-a-real-token/ask",
                      data={"message": "hello"}).status_code == 404)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
