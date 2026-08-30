"""The text a guest gets the day before they arrive, and the page behind it.

This is the message the whole texting layer exists for. They are travelling,
away from a computer, and "where do I actually go" is the only question left.

Three things it has to get right, and each has cost somebody something in this
app before:

  - IT MUST NOT SEND TWICE. A text is billed per message, so a job that relies
    on the scheduler running exactly once is a job that eventually bills the
    house twice and annoys the guest. The stamp is written and committed per
    booking, not after the loop — the mail side lost a whole class of messages
    to exactly that shape, where the stamp held open blocks the NEXT guest's
    message from being written at all.
  - IT MUST STAMP WHAT IT HELD, not only what it sent. With no provider the
    message goes to the outbox, and a job that stamped only on a real send
    would text everybody a second time the day one is switched on.
  - A GUEST IT MAY NOT TEXT IS NOT AN ERROR. A landline, an unreadable number,
    somebody who asked to be left alone — none of those is a failure worth
    waking anybody for, and all of them are counted and named rather than
    silently skipped.

Nothing can reach a provider: the harness stands in for sms_provider_send,
which is the only function that opens a connection.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTCHK"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM sms_outbox WHERE phone IN
                    (SELECT guest_phone FROM bookings WHERE reference_code LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    for number in ("+33611111111", "+33622222222", "+33633333333"):
        conn.execute("DELETE FROM sms_outbox WHERE phone = ?", (number,))
        conn.execute("DELETE FROM sms_optouts WHERE phone = ?", (number,))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.CHECKIN_TEXT_SETTING,))
    conn.commit()
    conn.close()


def _arrival(ref, phone, days=1, status="confirmed"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    when = datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, created_at)
           VALUES (?, ?, ?, 'Amelie Fontaine', 'a@example.invalid', ?, ?, ?, 2, ?, 400, ?)""",
        (room, TAG + ref, TAG + "tok" + ref, m.normalise_phone(phone) or phone,
         when.isoformat(), (when + timedelta(days=2)).isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _outbox(number):
    conn = db()
    try:
        return conn.execute("SELECT * FROM sms_outbox WHERE phone = ? ORDER BY id",
                            (number,)).fetchall()
    finally:
        conn.close()


def _stamp(ref):
    conn = db()
    try:
        return conn.execute(
            "SELECT checkin_text_sent_at FROM bookings WHERE reference_code = ?",
            (TAG + ref,)).fetchone()["checkin_text_sent_at"]
    finally:
        conn.close()


def _run():
    conn = db()
    try:
        return m.run_checkin_text_job(conn)
    finally:
        conn.commit()
        conn.close()


def run():
    s = Suite("The arrival text")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("Tomorrow's arrivals, and only those")
    good = _arrival("GOOD", "06 11 11 11 11")
    _arrival("LATER", "06 11 11 11 11", days=6)          # not yet
    _arrival("PENDING", "06 11 11 11 11", status="pending")   # not confirmed
    said = _run()
    held = _outbox("+33611111111")
    s.check("the guest arriving tomorrow is written to", len(held) == 1,
            detail=f"{len(held)} — a stay six days out and an unconfirmed one "
                   "must not be in this list")
    s.check("and it is stamped against the booking", bool(_stamp("GOOD")))
    s.check("the one six days away is not", not _stamp("LATER"))
    s.check("nor the unconfirmed one", not _stamp("PENDING"))

    s.section("What the message actually says")
    body = held[0]["body"] if held else ""
    s.check("it uses their first name, not the whole one", "Amelie" in body,
            detail=body[:70])
    s.check("and not their surname, which reads as a form letter",
            "Fontaine" not in body, detail=body[:70])
    s.check("it carries a link to their booking", "/booking/" in body or "/manage" in body,
            detail=body[:90])
    s.check("no merge tag is left showing", "{" not in body,
            detail="a guest receiving a literal {guest_name} is the one thing "
                   "worse than not texting them at all")
    s.check("and it fits in one billed message", m.sms_segments(body) == 1,
            detail=f"{m.sms_segments(body)} parts, {len(body)} characters")

    s.section("It never sends twice")
    # The expensive one: a text is billed per message.
    again = _run()
    s.check("a second run sends nothing more", len(_outbox("+33611111111")) == 1,
            detail=f"{len(_outbox('+33611111111'))} messages for one arrival")
    s.check("and says so plainly", "nobody" in again or "no number" in again,
            detail=str(again))

    s.section("A held message is stamped too")
    # With no provider the message waits in the outbox. Stamping only on a real
    # send would text everybody again the day one is connected.
    s.check("the message is waiting rather than sent",
            held and held[0]["sent_at"] is None)
    s.check("but the booking is stamped all the same", bool(_stamp("GOOD")),
            detail="otherwise connecting a provider re-sends to everybody who "
                   "was ever held")

    s.section("A guest it may not text is not a failure")
    _arrival("LAND", "01 61 02 03 04")
    _arrival("JUNK", "ask my wife")
    _arrival("NONE", "")
    stopped = _arrival("STOP", "06 22 22 22 22")
    conn = db()
    m.record_sms_optout(conn, "+33622222222", reason="asked")
    conn.commit()
    conn.close()
    said = _run()
    s.check("a landline is skipped", not _stamp("LAND"))
    s.check("an unreadable number is skipped", not _stamp("JUNK"))
    s.check("no number at all is skipped", not _stamp("NONE"))
    s.check("and somebody who asked to be left alone is skipped", not _stamp("STOP"))
    # Two gates, and they mean the same thing: the job asks can_text before
    # trying, and send_sms asks it again before writing anything. Removing the
    # job's call changes nothing observable — the guest is still not texted,
    # only the reason is counted later. Benign redundancy, and the checks here
    # are on the invariant rather than on which gate did the refusing.
    s.check("none of them is queued to be tried later",
            len(_outbox("+33622222222")) == 0,
            detail="waiting will not make somebody want it")
    s.check("the job reports how many it could not reach",
            "could not use" in said or "no number" in said, detail=str(said))
    s.check("and does not call any of it an error",
            "error" not in said.lower() and "fail" not in said.lower(),
            detail=str(said))

    s.section("The owner's page")
    page = oc.get("/management/texting")
    html = page.get_data(as_text=True)
    s.check("it opens", page.status_code == 200, detail=str(page.status_code))
    s.check("it says no provider is connected", "No provider is connected" in html)
    s.check("it counts the numbers that cannot be used", "cannot use" in html)
    s.check("and shows what is waiting", "+33611111111" in html)
    s.check("with the person who asked to stop", "+33622222222" in html)

    s.section("Changing the message says what it will cost")
    long_one = ("Bonjour {guest_name}, we look forward to welcoming you tomorrow. " * 4)
    r = oc.post("/management/texting/template", data={"template": long_one},
                follow_redirects=True)
    s.check("a longer message is saved",
            any("Saved" in f for f in flashes(r)), detail=str(flashes(r)))
    # The cost is per guest per arrival, so a sentence added here is charged
    # every time somebody comes.
    s.check("and it says how many messages that now is",
            any("messages per guest" in f for f in flashes(r)), detail=str(flashes(r)))

    r = oc.post("/management/texting/template", data={"template": "   "},
                follow_redirects=True)
    s.check("an empty message is refused",
            any("cannot be empty" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("Adding and lifting a stop, from the page")
    oc.post("/management/texting/stop",
            data={"phone": "06 33 33 33 33", "reason": "said so on the phone"},
            follow_redirects=True)
    conn = db()
    added = conn.execute("SELECT * FROM sms_optouts WHERE phone = ?",
                         ("+33633333333",)).fetchone()
    conn.close()
    s.check("a number typed the ordinary way is normalised onto the list",
            added is not None,
            detail="typed as 06 33 33 33 33 and stored as +33633333333, or the "
                   "sender would never match it")
    s.check("with why", added and added["reason"] == "said so on the phone")

    r = oc.post("/management/texting/stop", data={"phone": "not a number"},
                follow_redirects=True)
    s.check("something that is not a number is refused",
            any("not a number I can read" in f for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and the page survives it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    oc.post(f"/management/texting/allow/{added['id']}", follow_redirects=True)
    conn = db()
    lifted = conn.execute("SELECT 1 FROM sms_optouts WHERE phone = ?",
                          ("+33633333333",)).fetchone()
    conn.close()
    s.check("it can be lifted when they ask to come back", lifted is None)

    s.section("None of it is the employees'")
    conn = db()
    m.record_sms_optout(conn, "+33633333333", reason="guard test")
    conn.commit()
    guard = conn.execute("SELECT * FROM sms_optouts WHERE phone = ?",
                         ("+33633333333",)).fetchone()
    conn.close()
    ec.post(f"/management/texting/allow/{guard['id']}")
    ec.post("/management/texting/stop", data={"phone": "06 44 44 44 44"})
    ec.post("/management/texting/run-checkin")
    conn = db()
    still_stopped = conn.execute("SELECT 1 FROM sms_optouts WHERE phone = ?",
                                 ("+33633333333",)).fetchone()
    rogue = conn.execute("SELECT 1 FROM sms_optouts WHERE phone = ?",
                         ("+33644444444",)).fetchone()
    conn.close()
    s.check("an employee cannot lift somebody's stop", still_stopped is not None,
            detail="the one action on this page that undoes a person's choice")
    s.check("nor add one", rogue is None)
    s.check("nor set the whole house texting", ec.get("/management/texting").status_code
            in (302, 403))
    s.check("while the owner can", oc.get("/management/texting").status_code == 200)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
