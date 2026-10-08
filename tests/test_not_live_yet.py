# -*- coding: utf-8 -*-
"""One switch for "do not write to anybody outside the house yet".

The owner was handed a list of nine automations to untick by hand. That is the
wrong answer twice: it is tedious, and it is a hand-kept list beside a real one
that grows — the same failure this page already had once, when ten jobs ran
with no switch anywhere because the form was written out by hand while the
registry kept growing.

So it is held at the ONE DOOR. send_email makes this argument itself for
MAIL_REDIRECT_TO: there are two transports, they are chosen further down, and a
rule applied inside one of them stops applying the day somebody configures the
other. A guard there cannot be escaped by a job nobody remembered to add to a
list — including one written next month.

WHAT HAS TO HOLD, and every one of these fails quietly:

  NOTHING IS DROPPED. A held letter waits in Held Email with its reason, so
  going live sends nothing by surprise and nothing was lost meanwhile. A mute
  that discards is how a guest never hears about the booking they made.

  THE HOUSE CAN STILL WRITE TO ITSELF. The owner's digest goes to
  accounts@chateaugudanes.com while mail goes OUT as send.chateaugudanes.com.
  Treating the bare domain as a stranger would hold the owner's own post, and
  it would look exactly like the switch working.

  CREDENTIALS STILL GO. A password reset or a staff invitation is keep=False,
  short-lived by design and addressed to the house's own people. Holding one
  locks somebody out and helps nobody.

  AND IT SAYS SO, LOUDLY. A global mute nobody notices is how a house silently
  stops confirming bookings. The switch names how many letters it is holding.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zznl"
OUTSIDE = "zznl.guest@example.invalid"
INSIDE = "accounts@chateaugudanes.com"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG + ".%",))
    conn.execute("DELETE FROM guest_messages WHERE to_address LIKE ?", (TAG + ".%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", ("ZZNL%",))
    conn.execute("DELETE FROM guest_messages WHERE subject LIKE ?", ("ZZNL%",))
    conn.commit()
    conn.close()


def _switch(live):
    conn = db()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('guest_mail_live', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if live else "0",))
    conn.commit()
    conn.close()


def _held(subject):
    conn = db()
    row = conn.execute(
        "SELECT to_address, reason FROM email_outbox WHERE subject = ? "
        "AND sent_at IS NULL", (subject,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Not live yet: one switch for writing to guests")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    sent = []
    was_send, was_enabled = m.send_email_via_resend, m.resend_enabled
    was_setting = None
    conn = db()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'guest_mail_live'").fetchone()
    was_setting = row["value"] if row else None
    conn.close()

    # The REAL send_email, with only the transport under it replaced: the whole
    # claim is about what send_email does BEFORE it chooses one.
    m.resend_enabled = lambda: True
    m.send_email_via_resend = (
        lambda to, subj, body, ics=None, name=None, html=None, reply_to=None:
        (sent.append({"to": to, "subject": subj}), (True, None))[1])
    try:
        s.section("Switched on, the post goes as it always did")
        _switch(True)
        del sent[:]
        with m.app.test_request_context("/"):
            m.send_email(OUTSIDE, "ZZNL live", "Body.")
        s.check("a guest is written to", bool(sent) and sent[0]["to"] == OUTSIDE,
                detail=str(sent))
        s.check("and nothing is held", _held("ZZNL live") is None)

        s.section("Switched off, a letter to a guest waits rather than goes")
        _switch(False)
        del sent[:]
        with m.app.test_request_context("/"):
            went = m.send_email(OUTSIDE, "ZZNL held", "Body.")
        s.check("the guest is not written to", not sent, detail=str(sent))
        s.check("and the caller is told it did not go", went is False, detail=repr(went))
        waiting = _held("ZZNL held")
        s.check("the letter is kept, not thrown away", waiting is not None,
                detail="a mute that discards is how a guest never hears about "
                       "the booking they made")
        s.check("with the reason in words somebody can act on",
                waiting is not None and "not writing to guests" in (waiting["reason"] or ""),
                detail=repr(waiting["reason"] if waiting else None))

        s.section("But the house can still write to itself")
        # The owner reads accounts@chateaugudanes.com while mail goes OUT as
        # send.chateaugudanes.com. Treating the bare domain as a stranger would
        # hold the owner's own digest, and would look exactly like this working.
        del sent[:]
        with m.app.test_request_context("/"):
            m.send_email(INSIDE, "ZZNL inside", "Body.")
        s.check("the owner's own post is not held",
                bool(sent) and sent[0]["to"] == INSIDE, detail=str(sent))
        s.check("because the bare domain is the house, not a stranger",
                m.writes_outside_the_house(INSIDE) is False)
        s.check("and the send-only subdomain is too",
                m.writes_outside_the_house("x@send.chateaugudanes.com") is False)
        # THE CLAUSE THAT ACTUALLY CARRIES IT, which the two checks above do
        # not reach: PUBLIC_CONTACT already names the bare domain here, so
        # they pass with that clause deleted -- a control found that. With
        # ONLY the send-only subdomain known, which is all RESEND_FROM gives,
        # the address the owner reads still has to count as the house.
        was = (m.PUBLIC_CONTACT, m.MS_GRAPH_MAILBOXES, m.RESEND_REPLY_TO,
               m.SMTP_FROM, m.RESEND_FROM)
        try:
            m.PUBLIC_CONTACT, m.MS_GRAPH_MAILBOXES = {}, []
            m.RESEND_REPLY_TO, m.SMTP_FROM = "", ""
            m.RESEND_FROM = "Gudanes <reservations@send.chateaugudanes.com>"
            s.check("with only the send subdomain known, the bare domain is still ours",
                    m.writes_outside_the_house(INSIDE) is False,
                    detail="house knows %s" % sorted(m.house_mail_domains()))
            s.check("and a stranger is still a stranger",
                    m.writes_outside_the_house(OUTSIDE) is True)
        finally:
            (m.PUBLIC_CONTACT, m.MS_GRAPH_MAILBOXES, m.RESEND_REPLY_TO,
             m.SMTP_FROM, m.RESEND_FROM) = was
        s.check("while anybody else is outside it",
                m.writes_outside_the_house(OUTSIDE) is True)
        s.check("and so is an address that is not one",
                m.writes_outside_the_house("") is True
                and m.writes_outside_the_house(None) is True)

        s.section("And a credential still goes, because holding one locks somebody out")
        del sent[:]
        with m.app.test_request_context("/"):
            m.send_email(OUTSIDE, "ZZNL password", "Your code.", keep=False)
        s.check("a password reset is sent while the switch is off",
                bool(sent) and sent[0]["subject"] == "ZZNL password",
                detail="keep=False is short-lived by design and useless held")

        s.section("The switch says what it is doing")
        page = oc.get("/admin/automation").get_data(as_text=True)
        s.check("it is on the page somebody went looking on",
                'name="guest_mail_live"' in page)
        s.check("and says plainly that nothing is going out",
                "nothing is being written to guests" in page.lower(),
                detail="a global mute nobody notices is how a house silently "
                       "stops confirming bookings")
        s.check("and names how many it is holding",
                "waiting because of this" in page,
                detail="a control whose effect is invisible is one somebody "
                       "leaves on by accident")

        s.section("Turning it back on sends nothing by surprise")
        _switch(True)
        del sent[:]
        still = _held("ZZNL held")
        s.check("what was held is still held, for somebody to decide on",
                still is not None,
                detail="going live must not empty a fortnight of letters at "
                       "people who have moved on")
        with m.app.test_request_context("/"):
            m.send_email(OUTSIDE, "ZZNL again", "Body.")
        s.check("and new post goes again", bool(sent) and sent[0]["to"] == OUTSIDE)

        s.section("Read rather than run: the guard is at the one door")
        # Every check above replaces the transport, so a guard moved inside one
        # of them would pass all of them and stop applying the day somebody
        # configures the other. test_mail_redirect reads the same function for
        # the same reason.
        src = open("app.py", encoding="utf-8").read()
        door = src.split("def send_email(")[1].split("\ndef ")[0]
        s.check("send_email is where it is kept",
                "guest_mail_live()" in door and "writes_outside_the_house(" in door,
                detail="there are two transports and which one is configured is "
                       "an environment variable somebody set months ago")
        s.check("and it holds rather than drops",
                "queue_undelivered(" in door.split("guest_mail_live()")[0][-600:]
                or "queue_undelivered(" in door.split("not guest_mail_live()")[1][:600],
                detail="held, so going live loses nothing")
    finally:
        m.send_email_via_resend, m.resend_enabled = was_send, was_enabled
        conn = db()
        if was_setting is None:
            conn.execute("DELETE FROM app_settings WHERE key = 'guest_mail_live'")
        else:
            conn.execute("UPDATE app_settings SET value = ? WHERE key = 'guest_mail_live'",
                         (was_setting,))
        conn.commit()
        conn.close()
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
