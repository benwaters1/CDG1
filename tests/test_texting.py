"""Who the house may text, and what the notice promises about it.

A text is not an email, and the two kinds of message are not the same question.

A message about a stay somebody booked is theirs. They gave the number for it,
and telling them where to collect the key is the thing they wanted. That needs
no separate consent — only a way to stop it.

A marketing text is the opposite. Nobody hands over a number at a booking form
expecting an offer, and a phone is more intrusive than an inbox: it goes off at
the table. So marketing needs an explicit yes on file, and the absence of a no
is not one. That distinction is the whole design, and it is the one thing here
that would be easy to lose by treating the two lists as interchangeable.

The privacy notice now makes four claims about this, and each is a claim about
code rather than a sentiment:

  - we text you about your booking and nothing else;
  - you can stop it, and the number then stays on a do-not-text list;
  - we never send an offer by text unless you separately said yes, and leaving
    a number on a booking form is not saying yes;
  - we do not text landlines, and a number we cannot read we do not use.

All four are checked here. If the code stops making them true, the notice is
the thing that has become a lie, and it is the notice this suite defends.

Nothing can actually leave the building: the harness clears the provider
settings and stands in for sms_provider_send, which is the only function that
opens a connection. That block went in before there were any credentials to
leak rather than after, which is the opposite of how Stripe was handled.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTSMS"
MOBILE = "+33612345678"
OTHER = "+33698765432"
LANDLINE = "+33161020304"


def _cleanup():
    conn = db()
    for number in (MOBILE, OTHER, LANDLINE):
        conn.execute("DELETE FROM sms_optouts WHERE phone = ?", (number,))
        conn.execute("DELETE FROM sms_consents WHERE phone = ?", (number,))
        conn.execute("DELETE FROM sms_outbox WHERE phone = ?", (number,))
    conn.commit()
    conn.close()


def _held(number):
    conn = db()
    try:
        return conn.execute("SELECT * FROM sms_outbox WHERE phone = ? ORDER BY id",
                            (number,)).fetchall()
    finally:
        conn.close()


def _count_consents():
    conn = db()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM sms_consents").fetchone()["c"]
    finally:
        conn.close()


def run():
    s = Suite("Texting")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    conn = db()

    s.section("Nothing can reach a provider from here")
    # First, because every message costs money and the suite exercises the
    # code that would send them.
    s.check("no provider is configured under test", m.sms_enabled() is False)
    raised = False
    try:
        m.sms_provider_send(MOBILE, "probe")
    except Exception:
        raised = True
    s.check("and the one function that sends refuses to run", raised,
            detail="sms_provider_send is the only thing that opens a "
                   "connection, so it is the only thing that has to be blocked")

    s.section("A message about your own booking needs no permission")
    number, refusal = m.can_text(conn, "06 12 34 56 78", "transactional")
    s.check("a mobile given at booking can be texted about it", refusal is None,
            detail=str(refusal))
    s.check("and the gate hands back the number in sendable form",
            number == MOBILE, detail=str(number))

    s.section("But an offer needs a yes that was actually given")
    _n, refusal = m.can_text(conn, "06 12 34 56 78", "marketing")
    s.check("the same number cannot be sent an offer", refusal is not None,
            detail=str(refusal))
    s.check("and the reason is the missing consent, not the number",
            refusal and "consent" in refusal, detail=str(refusal))

    m.record_sms_consent(conn, "06 12 34 56 78", source="asked at the desk")
    conn.commit()
    _n, refusal = m.can_text(conn, MOBILE, "marketing")
    s.check("once they say yes, it can", refusal is None, detail=str(refusal))

    s.section("Asking to stop stops everything")
    m.record_sms_optout(conn, MOBILE, reason="asked in person")
    conn.commit()
    _n, marketing = m.can_text(conn, MOBILE, "marketing")
    _n2, transactional = m.can_text(conn, MOBILE, "transactional")
    s.check("no more offers", marketing is not None, detail=str(marketing))
    # The one that matters. A stop is a stop, not "stop the marketing".
    s.check("and no more messages about a booking either",
            transactional is not None, detail=str(transactional))
    s.check("the earlier yes is withdrawn with it",
            conn.execute("SELECT 1 FROM sms_consents WHERE phone = ?",
                         (MOBILE,)).fetchone() is None,
            detail="a consent left behind would read as still true the next "
                   "time somebody looked")

    m.record_sms_consent(conn, MOBILE, source="asked to come back")
    conn.commit()
    _n, refusal = m.can_text(conn, MOBILE, "marketing")
    s.check("and somebody who asks to come back can", refusal is None,
            detail=str(refusal))
    s.check("which lifts the do-not-text entry",
            conn.execute("SELECT 1 FROM sms_optouts WHERE phone = ?",
                         (MOBILE,)).fetchone() is None)

    s.section("What cannot be texted at all")
    _n, refusal = m.can_text(conn, LANDLINE, "transactional")
    s.check("a landline is refused", refusal is not None, detail=str(refusal))
    s.check("and named as one", refusal and "landline" in refusal, detail=str(refusal))
    _n, refusal = m.can_text(conn, "ask my wife", "transactional")
    s.check("a number the app cannot read is refused", refusal is not None,
            detail=str(refusal))
    _n, refusal = m.can_text(conn, MOBILE, "an-invented-purpose")
    s.check("and a purpose the app does not know is refused outright",
            refusal is not None, detail=str(refusal))

    s.section("With no way to send, a message is kept rather than lost")
    conn.execute("DELETE FROM sms_outbox WHERE phone = ?", (OTHER,))
    conn.commit()
    sent, refusal = m.send_sms(conn, OTHER, "Your key is with Marie at the gate.",
                               purpose="transactional")
    conn.commit()
    s.check("it does not claim to have sent", sent is False)
    s.check("and it is not a refusal either", refusal is None,
            detail=f"{refusal} — held and refused are different answers")
    held = _held(OTHER)
    s.check("the message is waiting", len(held) == 1, detail=str(len(held)))
    s.check("with what it was for", held and held[0]["purpose"] == "transactional")
    s.check("and why it did not go", held and "provider" in (held[0]["reason"] or ""),
            detail=str(held[0]["reason"]) if held else "")

    # A refusal must never be queued: it will not become sendable by waiting.
    m.record_sms_optout(conn, OTHER, reason="stop")
    conn.commit()
    before = len(_held(OTHER))
    sent, refusal = m.send_sms(conn, OTHER, "Another one.", purpose="transactional")
    conn.commit()
    s.check("a refused message is not queued for later", len(_held(OTHER)) == before,
            detail=f"{len(_held(OTHER)) - before} queued — waiting will not "
                   "make somebody want it")
    s.check("and the caller is told why", refusal is not None, detail=str(refusal))

    s.section("Some messages must not be kept at all")
    # The mirror of keep=False on the mail side. "A room has come free for your
    # dates" is only true while it is free; held and delivered the week a
    # provider is finally connected, it arrives about dates resold a month ago
    # — on a phone, in a pocket.
    conn.execute("DELETE FROM sms_optouts WHERE phone = ?", (OTHER,))
    conn.execute("DELETE FROM sms_outbox WHERE phone = ?", (OTHER,))
    conn.commit()
    sent, refusal = m.send_sms(conn, OTHER, "A room has come free.",
                               purpose="transactional", hold=False)
    conn.commit()
    s.check("it does not claim to have sent", sent is False)
    s.check("and it is refused rather than queued", refusal is not None,
            detail=str(refusal))
    s.check("with nothing waiting behind it", len(_held(OTHER)) == 0,
            detail=f"{len(_held(OTHER))} stale message(s) waiting to surprise "
                   "somebody")
    # ...while an ordinary message still waits, or hold=False would be the
    # default by accident.
    sent, refusal = m.send_sms(conn, OTHER, "Your key is with Marie.",
                               purpose="transactional")
    conn.commit()
    s.check("an ordinary message is still held", len(_held(OTHER)) == 1,
            detail=str(len(_held(OTHER))))

    s.section("Somebody says yes to offers")
    # can_text refuses a marketing message to a number with no consent on
    # file, and record_sms_consent was the only thing that could write one --
    # called by nothing. The layer was safe and unusable at once: the page
    # counted consents, the sender looked for them, and nothing could create
    # one.
    keen = "+33698765432"
    conn = db()
    conn.execute("DELETE FROM sms_consents WHERE phone = ?", (keen,))
    conn.execute("DELETE FROM sms_optouts WHERE phone = ?", (keen,))
    conn.commit()
    with m.app.test_request_context():
        _n, refusal = m.can_text(conn, keen, "marketing")
    conn.close()
    s.check("before any yes, an offer is refused", bool(refusal),
            detail=str(refusal))

    r = oc.post("/management/texting/consent",
                data={"phone": "06 98 76 54 32", "source": "said so at the desk"},
                follow_redirects=True)
    conn = db()
    have = conn.execute("SELECT * FROM sms_consents WHERE phone = ?", (keen,)).fetchone()
    with m.app.test_request_context():
        number, refusal = m.can_text(conn, keen, "marketing")
    conn.close()
    s.check("the yes is recorded", have is not None, detail=str(flashes(r)))
    s.check("against the number as the app writes numbers, not as it was typed",
            have and have["phone"] == keen,
            detail=str(have["phone"]) if have else "06 98 76 54 32 was typed")
    s.check("with where they said it", have and "desk" in (have["source"] or ""),
            detail=str(have["source"]) if have else "")
    s.check("and now an offer is allowed", not refusal and number == keen,
            detail=str(refusal))

    s.section("A yes lifts an earlier no, because coming back is theirs to decide")
    conn = db()
    m.record_sms_optout(conn, keen, reason="asked to stop")
    conn.commit()
    with m.app.test_request_context():
        _n, refused_again = m.can_text(conn, keen, "marketing")
    conn.close()
    s.check("after they ask to stop, offers are refused again", bool(refused_again),
            detail=str(refused_again))

    oc.post("/management/texting/consent",
            data={"phone": keen, "source": "rang back"}, follow_redirects=True)
    conn = db()
    with m.app.test_request_context():
        _n, after_return = m.can_text(conn, keen, "marketing")
    conn.close()
    s.check("saying yes again lifts it", not after_return,
            detail=f"{after_return} -- the mirror of the newsletter's confirm "
                   "step: coming back is a deliberate act by the person")

    s.section("What it will not take as a yes")
    before = _count_consents()
    r = oc.post("/management/texting/consent",
                data={"phone": "ask my wife", "source": "x"}, follow_redirects=True)
    s.check("a number nobody can read is refused",
            any("could not be read" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    r = oc.post("/management/texting/consent",
                data={"phone": "01 61 02 03 04", "source": "x"}, follow_redirects=True)
    s.check("and a landline is refused",
            any("landline" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("neither was written down", _count_consents() == before,
            detail="a consent recorded against a number that cannot receive a "
                   "text is a consent that will never be acted on and never "
                   "be reviewed")

    r = _ec.post("/management/texting/consent",
                data={"phone": "06 98 76 54 30", "source": "employee"},
                follow_redirects=False)
    s.check("an employee cannot record a consent",
            r.status_code in (302, 303, 403), detail=f"HTTP {r.status_code}")
    s.check("and none was recorded", _count_consents() == before)

    conn = db()
    conn.execute("DELETE FROM sms_consents WHERE phone = ?", (keen,))
    conn.execute("DELETE FROM sms_optouts WHERE phone = ?", (keen,))
    conn.commit()
    conn.close()

    s.section("The notice says exactly this")
    # Four claims, each a claim about code. If the code stops making them true
    # the notice is what has become a lie.
    page = m.app.test_client().get("/privacy").get_data(as_text=True)
    for claim, needle in (
            # Short fragments only: the template wraps, so a phrase that spans
            # a line break in the source never matches the rendered page.
            ("it only texts you about your booking", "and nothing else"),
            ("you can stop it", "do-not-text list"),
            ("an offer needs a separate yes", "unless you have separately"),
            ("a booking form is not consent", "is not saying yes"),
            ("and landlines are left alone", "do not text landlines")):
        s.check(f"the notice says {claim}", needle in page, detail=needle)

    conn.close()
    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
