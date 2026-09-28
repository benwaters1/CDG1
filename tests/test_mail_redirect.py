"""A test deployment that cannot reach a guest.

Testing email means connecting a real provider, and a real provider connected
to this app sends real confirmations to real people the moment somebody
confirms a booking — against a database that is a copy of the live one, full
of real addresses. "I will be careful" is not a control. The house already has
several hundred messages waiting in the outbox, and one wrong click is a year
of old letters going out to people who have long since been and gone.

MAIL_REDIRECT_TO sends every letter to one address instead, whoever it was
addressed to. Four things have to hold, and each of them fails silently:

  IT HAS TO BE THE ONE DOOR. There are two transports and they are chosen
  further down; a rule applied inside one of them is a rule that does not
  apply when the other is configured — and which one is configured is an
  environment variable somebody set months ago.

  IT HAS TO SAY WHO IT WAS FOR. A test inbox with forty letters in it is
  unreadable otherwise: "which of these was the one to the Dutch couple" is
  the actual question you have when checking.

  IT MUST NOT BE FILED AS CORRESPONDENCE. booking_correspondence is the
  record of what the house has SAID to a guest. A letter the guest never
  received has no business in it — and worse, it would be filed against
  whichever booking happens to own the redirect address.

  AND THE PAGE HAS TO SHOUT. This is the only feature here whose failure mode
  is silence: left on in production, not one guest hears anything and every
  page looks healthy. No error, no bounce, no held mail, because the letters
  went out successfully — to the wrong person. Nothing else would ever say so.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZMR"
ME = "zzmr.owner@example.invalid"
GUEST = "zzmr.guest@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM guest_messages WHERE to_address LIKE 'zzmr.%'")
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("A test deployment that cannot reach a guest")
    _cleanup()
    oc, _ec, _owner, _emp = clients()

    stay = m.house_today() + timedelta(days=250)
    step = timedelta(days=2)
    sent = []
    was_send = m.send_email_via_resend
    was_enabled = m.resend_enabled
    was_redirect = m.MAIL_REDIRECT_TO
    # The REAL send_email, with only the transport under it replaced — the
    # whole claim is about what send_email does before it chooses one.
    m.resend_enabled = lambda: True
    # (ok, why) since app.py started reporting WHY Resend refused a message.
    # send_email unpacks it, and a bare True cannot be unpacked.
    m.send_email_via_resend = lambda to, subj, body, ics=None, name=None, html=None, reply_to=None: (
        sent.append({"to": to, "subject": subj, "body": body}), (True, None))[1]
    try:
        s.section("With it off, nothing changes")
        m.MAIL_REDIRECT_TO = ""
        del sent[:]
        with m.app.test_request_context("/"):
            m.send_email(GUEST, "Booking confirmed", "Your room is held.",
                         keep=False)
        s.check("the guest is written to", sent and sent[0]["to"] == GUEST,
                detail=str(sent))
        s.check("and the subject is left alone",
                sent and sent[0]["subject"] == "Booking confirmed")

        s.section("With it on, the guest is not written to at all")
        m.MAIL_REDIRECT_TO = ME
        del sent[:]
        with m.app.test_request_context("/"):
            m.send_email(GUEST, "Booking confirmed", "Your room is held.",
                         keep=False)
        s.check("exactly one letter went", len(sent) == 1, detail=str(len(sent)))
        s.check("and it went to the test address", sent and sent[0]["to"] == ME,
                detail=str(sent[0]["to"]) if sent else "")
        # THE POINT. Not "the guest also got one" — the guest got NOTHING.
        s.check("the guest's address appears nowhere as a recipient",
                not any(x["to"] == GUEST for x in sent),
                detail="a copy is not the same as a diversion: a test that "
                       "also writes to the guest has tested nothing and "
                       "emailed somebody's customer")

        s.section("But it still says who it was for")
        s.check("the intended recipient is in the subject",
                sent and GUEST in sent[0]["subject"],
                detail=sent[0]["subject"] if sent else "")
        s.check("with the real subject still on it",
                sent and "Booking confirmed" in sent[0]["subject"])
        s.check("and the body says plainly that nobody received it",
                sent and "No guest received it" in sent[0]["body"],
                detail="somebody forwards one of these by accident; it has to "
                       "read as a test on its own")
        s.check("and the real letter is still underneath",
                sent and "Your room is held." in sent[0]["body"])

        s.section("It is not filed as something the house said")
        conn = db()
        room = _harness.ensure_room()
        room = conn.execute("SELECT * FROM rooms WHERE id = ?",
                            (room["id"],)).fetchone()
        with m.app.test_request_context("/"):
            for _try in range(40):
                if m.is_range_available(conn, room["id"], stay, stay + step):
                    break
                stay += step
            ref, _tok = m.create_booking(
                conn, room, TAG + " Owner Staying", ME, "", stay, stay + step,
                2, "", [], payment_status="unpaid")
        conn.commit()
        filed = conn.execute(
            """SELECT COUNT(*) AS c FROM guest_messages
                WHERE to_address IN (?, ?)""", (ME, GUEST)).fetchone()["c"]
        conn.close()
        del sent[:]
        with m.app.test_request_context("/"):
            m.send_email(GUEST, "Filed?", "Body.")   # keep defaults to True
            m.close_open_connections(None)
        conn = db()
        try:
            after = conn.execute(
                """SELECT COUNT(*) AS c FROM guest_messages
                    WHERE to_address IN (?, ?)""", (ME, GUEST)).fetchone()["c"]
        finally:
            conn.close()
        s.check("a diverted letter is not correspondence",
                after == filed,
                detail="what the house SAID to a guest is a record somebody "
                       "answers the telephone from; a letter they never got "
                       "would be filed against whoever owns the test address")

        s.section("And the page says so, in red")
        conn = db()
        try:
            with m.app.test_request_context("/"):
                rows = m.readiness_checks(conn, include_slow=False)
        finally:
            conn.close()
        diverted = [r for r in rows if "diverted" in (r.get("label") or "").lower()
                    or "diverted" in (r.get("name") or "").lower()]
        s.check("there is a line about it at all", bool(diverted),
                detail="left on in production nothing reaches a guest and "
                       "every other page looks healthy: " +
                       str([r.get("label") or r.get("name") for r in rows][:8]))
        s.check("it is a blocker, not a note",
                diverted and diverted[0].get("severity") == "blocker",
                detail=str(diverted[0]) if diverted else "")
        s.check("it is failing while the switch is on",
                diverted and not diverted[0].get("ok"),
                detail=str(diverted[0]) if diverted else "")
        s.check("and it names who is getting the guests' mail",
                diverted and ME in (diverted[0].get("detail") or ""),
                detail="'email is diverted' without an address is a sentence "
                       "nobody can act on")

        m.MAIL_REDIRECT_TO = ""
        conn = db()
        try:
            with m.app.test_request_context("/"):
                rows = m.readiness_checks(conn, include_slow=False)
        finally:
            conn.close()
        s.check("and it is gone once the switch is off",
                not [r for r in rows
                     if "diverted" in ((r.get("label") or "")
                                       + (r.get("name") or "")).lower()],
                detail="a warning that is always there is furniture")
    finally:
        m.MAIL_REDIRECT_TO = was_redirect
        m.send_email_via_resend, m.resend_enabled = was_send, was_enabled
        _cleanup()
    return s
