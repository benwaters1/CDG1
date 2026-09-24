"""The page where the owner writes what guests are sent, and whether it covers them.

It covered twenty-one letters and said it covered every step. The room
confirmation -- the most-opened message the house sends -- and the room
cancellation, the change of dates, the statement, the balance letters, the
share of a bill, an atelier being called off, an event quote and the
newsletter's confirmation were all sentences in code. The page could not
change a word a room guest received, and nothing said so.

So this holds three things:

  - COVERAGE. Every function that sends mail either goes through a template
    or is on a named list below with the reason it may not -- an owner's
    notice, a security letter, a letter the owner writes each time. The list
    is checked in both directions: a new letter written in code reds the
    run, and so does an entry left behind for one that is now a template.
  - ONE SET OF WORDS. The confirmation keeps its own design, and its words
    now come from the template -- so an edit reaches the drawn letter most
    guests see, not only the plain text few do.
  - THE EDITOR'S OWN PROMISES. A refused save keeps what was typed; every
    change is kept and can be put back; "send me a test" sends the draft to
    the person signed in and nobody else; a stray brace, a dotted tag or a
    required link taken out is refused at the save and never reaches a send.
"""
import io
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZTPLED"

# Functions that send mail WITHOUT a template, and why each may. Anything that
# sends and is not here, and asks no template, is a letter the owner cannot
# edit -- which is the fault this file exists for.
MAY_WRITE_IN_CODE = {
    # the house's own notices, read by the owner and staff, not guests
    "create_booking": "the owner's 'booked' / 'needs you' notices (the guest's letter is a template)",
    "create_booking_from_stripe_session": "an urgent notice to the owner",
    "create_restaurant_booking_from_stripe_session": "an urgent notice to the owner",
    "manage_booking": "notices to the owner when a guest changes their own booking",
    "restaurant_manage": "notices to the owner",
    "workshop_manage": "notices to the owner",
    "event_manage": "notices to the owner",
    "submit_event_inquiry": "a notice to the owner",
    "event_quote": "a notice to the owner that a quote was accepted",
    "join_waitlist": "a notice to the owner",
    "join_restaurant_waitlist": "a notice to the owner",
    "join_workshop_waitlist": "a notice to the owner",
    "guest_account_message": "a guest's message passed to the owner",
    "tell_the_house_about_a_review": "a notice to the owner",
    "lapse_event_holds": "a notice to the owner",
    "contact_page": "an enquiry passed to the house",
    "pass_message": "the kitchen calling the floor",
    "run_morning_digest_job": "the owner's morning summary",
    "run_daily_digest_job": "the owner's daily summary",
    "api_owner_digest": "the owner's daily summary",
    "send_autocharge_failed_email": "the guest's letter and the owner's notice; "
                                    "being reworked with workshop balances",
    "send_autocharge_taken_email": "being reworked with workshop balances",
    # staff, not guests
    "apply_expense_decision": "to a member of staff",
    "decide_shift_swap": "to a member of staff",
    "bulk_approve_queue": "to a member of staff",
    "apply_leave_decision": "to a member of staff",
    # keys to an account: an edit that dropped the code or link locks them out
    "forgot_password": "carries a one-time sign-in code",
    "change_email": "tells the OLD address its sign-in moved",
    "guest_account_request": "the link is the sign-in itself",
    # written by a person each time, or already rendered elsewhere
    "reply_to_feedback": "the owner writes the reply",
    "reply_booking_com_message": "the owner writes the reply, to the guest's Booking.com address",
    "send_campaign": "a campaign the owner wrote on the campaigns page",
    "send_email_outbox": "re-sends letters already written and held",
    "test_email_provider": "a test of the provider, to the owner",
    "test_email_template": "sends a template's own draft, to the owner",
    "write_about_stay": "the helper every stay letter goes through",
}
TEMPLATE_CALLS = ("render_email_template(", "email_template_text(",
                  "balance_request_email(", "booking_confirmation_html(",
                  "ask_room_feedback(", "send_workshop_email(",
                  "send_restaurant_email(", "send_event_email(")


def _senders():
    """{function: calls a template?} for every function that sends mail.

    A function's body is its own indented lines and nothing after -- module
    code that follows a function is not the function -- with comments and
    docstrings taken out, because "every send_email() call is a no-op" in a
    comment is not a letter.
    """
    path = os.path.join(_harness.ROOT, "app.py")
    lines = io.open(path, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    out = {}
    for i, line in enumerate(lines):
        name = re.match(r"def (\w+)\(", line)
        if not name or name.group(1) in ("send_email", "send_email_via_resend",
                                         "send_email_via_smtp"):
            continue
        body = []
        for later in lines[i + 1:]:
            if later.strip() and not later[:1].isspace():
                break
            body.append(later)
        code = re.sub(r'"""[\s\S]*?"""', "", "\n".join(body))
        code = re.sub(r"#[^\n]*", "", code)
        if re.search(r"(?<![\w.])(?:send_email|write_about_stay)\(", code):
            out[name.group(1)] = any(t in code for t in TEMPLATE_CALLS)
    return out


def _row(key):
    conn = db()
    try:
        return conn.execute("SELECT * FROM email_templates WHERE template_key = ?",
                            (key,)).fetchone()
    finally:
        conn.close()


def _put(key, subject, body):
    conn = db()
    conn.execute("UPDATE email_templates SET subject = ?, body = ? WHERE template_key = ?",
                 (subject, body, key))
    conn.commit()
    conn.close()


def _revisions(key):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM email_template_revisions WHERE template_key = ? ORDER BY id",
            (key,)).fetchall()
    finally:
        conn.close()


def _capture():
    sent = []

    def fake(to, subject, body, ics_content=None, ics_filename=None, keep=True, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "keep": keep,
                     "html": kw.get("html") or "", "ics": ics_content})
        return True
    return sent, fake


def run():
    s = Suite("The email template editor")
    oc, ec, owner, _emp = clients()
    shipped = {k: (label, subj, body) for k, label, subj, body in m.DEFAULT_EMAIL_TEMPLATES}

    s.section("Every letter a guest gets is on the page, or named with a reason")
    senders = _senders()
    in_code = sorted(f for f, templated in senders.items()
                     if not templated and f not in MAY_WRITE_IN_CODE)
    s.check("no function writes a letter the owner cannot edit", not in_code,
            detail="these send mail and ask no template -- make it a template, or "
                   "name it in MAY_WRITE_IN_CODE with the reason: " + ", ".join(in_code))
    # Listed and sending nothing any more. A function that sends a template to
    # the guest and a notice in code to the owner stays listed -- the notice
    # is the reason -- so "uses a template" is not the same as "stale".
    stale = sorted(f for f in MAY_WRITE_IN_CODE if f not in senders)
    s.check("and nothing is excused that no longer sends at all", not stale,
            detail="these are on the list but send nothing -- take them off: "
                   + ", ".join(stale))
    s.check("the reader found the senders", len(senders) >= 40,
            detail=f"{len(senders)} -- a reader that finds nothing excuses everything")
    for key in ("room_confirmed", "room_cancelled", "room_updated", "room_declined",
                "room_statement", "room_balance_before", "room_balance_after",
                "room_share_request", "room_payment_received", "event_quote",
                "workshop_not_running", "newsletter_confirm"):
        s.check(f"{key} is a template", key in shipped and _row(key) is not None)

    s.section("Each one says when it goes and to whom")
    info = m.EMAIL_TEMPLATE_INFO
    s.check("every template has its line", not [k for k in shipped if k not in info],
            detail=str([k for k in shipped if k not in info]))
    s.check("and no line is left for a template that has gone",
            not [k for k in info if k not in shipped],
            detail=str([k for k in info if k not in shipped]))

    s.section("The shipped wording passes the rules it will be held to")
    bad = {k: m.wording_problems(k, subj, body) for k, (_l, subj, body) in shipped.items()}
    bad = {k: v for k, v in bad.items() if v}
    s.check("no shipped letter would be refused by its own editor", not bad,
            detail=str(bad)[:300])
    orphans = [(k, t) for k, need in m.REQUIRED_MERGE_TAGS.items()
               for t in need if t not in m.EMAIL_TEMPLATE_TAGS.get(k, ())]
    s.check("every required tag is one the letter can fill", not orphans,
            detail=str(orphans))
    unshipped = [k for k in m.REQUIRED_MERGE_TAGS if k not in shipped]
    s.check("and belongs to a letter that exists", not unshipped, detail=str(unshipped))

    s.section("The page covers them all and says what it does not")
    page = oc.get("/management/email-templates")
    text = visible_text(page.get_data(as_text=True))
    s.check("it opens", page.status_code == 200, page)
    missing = [label for label, _s, _b in shipped.values() if label not in text]
    s.check("every template is listed", not missing, detail=str(missing[:5]))
    s.check("with when it goes", "Goes " in text and "The moment a room booking is confirmed" in text)
    s.check("and the letters kept out of it, and why",
            "Not editable here, on purpose" in text and "Password reset code" in text)

    s.section("The preview draws the letter, not only the text")
    conn = db()
    rows = {r["template_key"]: r for r in conn.execute("SELECT * FROM email_templates")}
    conn.close()
    undrawn = []
    for key in shipped:
        r = oc.post(f"/management/email-templates/{key}/preview",
                    json={"subject": rows[key]["subject"], "body": rows[key]["body"]})
        d = r.get_json() or {}
        if not (d.get("html") or "").lstrip().lower().startswith("<!doctype"):
            undrawn.append(key)
    s.check("every template previews as the drawn letter", not undrawn, detail=str(undrawn))
    conf = oc.post("/management/email-templates/room_confirmed/preview",
                   json={"subject": shipped["room_confirmed"][1],
                         "body": shipped["room_confirmed"][2]}).get_json() or {}
    s.check("the confirmation keeps its own design",
            "C'est confirm" in (conf.get("html") or "")
            and "GUD-4417" in (conf.get("html") or ""),
            detail="the card, with the reference, between the words")
    s.check("with the words the template holds",
            "Your room is held, Marie" in (conf.get("html") or ""))
    s.check("and the plain text writes the card out",
            "Reference code: GUD-4417" in (conf.get("body") or "")
            and "{stay_details}" not in (conf.get("body") or ""))
    moved = oc.post("/management/email-templates/room_updated/preview",
                    json={"subject": shipped["room_updated"][1],
                          "body": shipped["room_updated"][2]}).get_json() or {}
    s.check("a list of facts is drawn a line each, as it was typed",
            re.search(r"Arrival: 14 October 2026\s*<br>\s*Departure: 17 October 2026",
                      moved.get("html") or "") is not None,
            detail="joined, it read 'Arrival: 14 October 2026 Departure: 17 October "
                   "2026 Party size: 2' -- one run-on sentence")

    key = "room_cancelled"
    before = _row(key)
    # Put back whatever happens: if the refusal below ever stops refusing, the
    # confirmation's wording would otherwise stay wrecked for every suite after.
    confirmed_before = _row("room_confirmed")
    try:
        s.section("A save that is refused keeps what was typed")
        typed = "Hi {guest_name}, the booking {reference_code} is off { sorry"
        r = oc.post(f"/management/email-templates/{key}/edit",
                    data={"subject": "Cancelled — {room_name}", "body": typed})
        html = r.get_data(as_text=True)
        s.check("a stray brace is refused", _row(key)["body"] == before["body"],
                detail="format() raises on it, and the send it was part of goes down")
        s.check("and says why, in words",
                any("on its own" in f for f in flashes(r)), detail=str(flashes(r)[:1]))
        s.check("what was typed is still in the editor",
                "the booking {reference_code} is off { sorry" in html)
        s.check("with the editor open, not hidden behind Edit",
                re.search(r'id="edit-tpl-room_cancelled"\s*>', html) is not None)

        r = oc.post(f"/management/email-templates/{key}/edit",
                    data={"subject": "Cancelled", "body": "Dear {guest_name.title}, gone."})
        s.check("a tag with a dot in it is refused",
                _row(key)["body"] == before["body"], detail=str(flashes(r)[:1]))

        r = oc.post("/management/email-templates/room_confirmed/edit",
                    data={"subject": "Confirmed", "body": "Hello {first_name}, see you."})
        s.check("taking out a link the letter needs is refused",
                "{stay_details}" in (_row("room_confirmed")["body"] or ""),
                detail=str(flashes(r)[:1]))
        s.check("and the reason is the one the owner is owed",
                any("stay_details" in f and "their booking" in f for f in flashes(r)),
                detail=str(flashes(r)[:1]))

        s.section("If one gets in anyway, the send survives it")
        _put(key, "Cancelled", "Hi {guest_name}, it is off { sorry. {reference_code}")
        conn = db()
        with m.app.test_request_context("/"):
            subject, body, drawn = m.render_email_template(conn, key, {
                "guest_name": "Marie", "room_name": "Blue", "arrival_date": "1 May",
                "departure_date": "3 May", "reference_code": "ZZ1"})
        conn.close()
        s.check("a lone brace does not take the send down", bool(subject and body))
        s.check("the shipped wording goes, filled in", "ZZ1" in (body or "")
                and "{ sorry" not in (body or ""), detail=repr((body or "")[:80]))
        s.check("and the page marks it as needing fixing",
                "needs fixing" in oc.get("/management/email-templates").get_data(as_text=True))
        _put(key, before["subject"], before["body"])

        # No row at all -- a database seeded before the letter existed -- used
        # to mean no letter, silently. A stay cancelled with nobody told.
        conn = db()
        conn.execute("DELETE FROM email_templates WHERE template_key = ?", (key,))
        conn.commit()
        with m.app.test_request_context("/"):
            subject, body, drawn = m.render_email_template(conn, key, {
                "guest_name": "Marie", "room_name": "Blue", "arrival_date": "1 May",
                "departure_date": "3 May", "reference_code": "ZZ2"})
        conn.execute("INSERT INTO email_templates (template_key, label, subject, body, "
                     "updated_at) VALUES (?, ?, ?, ?, ?)",
                     (key, before["label"], before["subject"], before["body"],
                      before["updated_at"]))
        conn.commit()
        conn.close()
        s.check("a letter with no saved wording still goes, in the shipped words",
                bool(subject) and "ZZ2" in (body or "")
                and drawn.lstrip().lower().startswith("<!doctype"),
                detail=repr(subject))

        s.section("Every change is kept, and any one can be put back")
        had = len(_revisions(key))
        oc.post(f"/management/email-templates/{key}/edit",
                data={"subject": before["subject"], "body": before["body"]})
        s.check("a save that changes nothing keeps nothing", len(_revisions(key)) == had)
        first = "Hi {guest_name}, {room_name} is cancelled. {reference_code}"
        oc.post(f"/management/email-templates/{key}/edit",
                data={"subject": before["subject"], "body": first})
        revs = _revisions(key)
        s.check("a real edit keeps what it replaced",
                len(revs) == had + 1 and revs[-1]["body"] == before["body"],
                detail=f"{len(revs)} kept")
        s.check("with who made the change",
                revs and (revs[-1]["replaced_by"] == owner["id"]),
                detail=str(revs[-1]["replaced_by"] if revs else None))
        page = oc.get("/management/email-templates").get_data(as_text=True)
        s.check("the page shows the earlier version", "Earlier versions" in page
                and "Put this version back" in page)
        r = oc.post(f"/management/email-templates/{key}/history/{revs[-1]['id']}/restore",
                    follow_redirects=True)
        s.check("putting it back brings the old wording back",
                _row(key)["body"] == before["body"], r)
        s.check("and the one it replaced is kept too",
                _revisions(key)[-1]["body"] == first)
        r = oc.post(f"/management/email-templates/{key}/history/999999/restore")
        s.check("a version that does not exist is a 404", r.status_code == 404)
        r = ec.post(f"/management/email-templates/{key}/history/{revs[-1]['id']}/restore")
        s.check("an employee cannot put one back", r.status_code in (302, 403))

        s.section("Send me a test: the draft, to the person signed in, and nobody else")
        sent, fake = _capture()
        was = m.send_email
        m.send_email = fake
        try:
            draft = "Hi {guest_name}, ZZ-UNSAVED {room_name} is off. {reference_code}"
            r = oc.post(f"/management/email-templates/{key}/test",
                        json={"subject": "Off — {room_name}", "body": draft})
            d = r.get_json() or {}
        finally:
            m.send_email = was
        conn = db()
        address = conn.execute("SELECT email FROM users WHERE id = ?",
                               (owner["id"],)).fetchone()["email"]
        conn.close()
        s.check("one letter went", len(sent) == 1, detail=str(len(sent)))
        s.check("to the person signed in", sent and sent[0]["to"] == address,
                detail=str(sent[0]["to"] if sent else None))
        s.check("marked as a test in the subject",
                sent and sent[0]["subject"].startswith("[Test] "))
        s.check("carrying the unsaved draft, filled in",
                sent and "ZZ-UNSAVED" in sent[0]["body"] and "Marie" in sent[0]["body"])
        s.check("drawn, as a guest would get it",
                sent and sent[0]["html"].lstrip().lower().startswith("<!doctype"))
        s.check("and never filed as a letter to a guest", sent and sent[0]["keep"] is False)
        s.check("the page is told where it went", d.get("sent") is True and d.get("to") == address)
        s.check("and the draft was not saved by testing it",
                "ZZ-UNSAVED" not in (_row(key)["body"] or ""))
        r = ec.post(f"/management/email-templates/{key}/test", json={"subject": "x", "body": "y"})
        s.check("an employee cannot send one", r.status_code in (302, 403))
    finally:
        _put(key, before["subject"], before["body"])
        _put("room_confirmed", confirmed_before["subject"], confirmed_before["body"])
        conn = db()
        conn.execute("DELETE FROM email_template_revisions WHERE template_key = ?", (key,))
        conn.commit()
        conn.close()

    s.section("The confirmation is one set of words, in both halves")
    room = _harness.ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    conf_before = _row("room_confirmed")
    arrival = _harness.free_window(room["id"], 3, after_days=330)
    sent, fake = _capture()
    was = m.send_email
    try:
        _put("room_confirmed", conf_before["subject"],
             conf_before["body"].replace("Your room is held, {first_name}",
                                         "ZZ the room is yours, {first_name}"))
        m.send_email = fake
        with m.app.test_request_context("/"):
            ref, _token = m.create_booking(
                conn, room, TAG + " Guest", "zztpled@example.invalid", "",
                arrival, arrival + timedelta(days=3), 2, "", [],
                payment_status="unpaid")
            bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                               (ref,)).fetchone()["id"]
            if conn.execute("SELECT status FROM bookings WHERE id = ?",
                            (bid,)).fetchone()["status"] != "confirmed":
                m.confirm_booking_by_id(conn, bid)
    finally:
        m.send_email = was
        _put("room_confirmed", conf_before["subject"], conf_before["body"])
    letter = next((x for x in sent if x["subject"].startswith("Booking confirmed")), None)
    s.check("the confirmation went", letter is not None,
            detail=str([x["subject"] for x in sent]))
    if letter:
        s.check("the edited words are in the plain text",
                "ZZ the room is yours" in letter["body"])
        s.check("and in the drawn letter too -- the half most guests read",
                "ZZ the room is yours" in letter["html"],
                detail="the design used to carry its own sentences, so an edit "
                       "reached the plain text and nobody else")
        s.check("with the card still in it", ref in letter["html"] and ref in letter["body"])
        s.check("and the calendar invite", bool(letter["ics"]))

    s.section("Cancelling tells everybody the booking concerns")
    copy = "zztpled.copy@example.invalid"
    conn.execute("UPDATE bookings SET second_contact_name = ?, second_contact_email = ? "
                 "WHERE id = ?", (TAG + " Assistant", copy, bid))
    conn.commit()
    sent, fake = _capture()
    m.send_email = fake
    try:
        oc.post(f"/admin/bookings/{bid}/cancel", data={}, follow_redirects=True)
    finally:
        m.send_email = was
    told = sorted({x["to"] for x in sent if x["subject"].startswith("Booking cancelled")})
    s.check("the guest and the copied party are both told",
            told == sorted(["zztpled@example.invalid", copy]),
            detail=f"{told} -- the copy was told it was confirmed and never that "
                   "it was not")

    s.section("A stay is asked how it was once, whichever way it ends")
    arrival2 = _harness.free_window(room["id"], 2, after_days=360)
    with m.app.test_request_context("/"):
        ref2, _t2 = m.create_booking(
            conn, room, TAG + " Leaver", "zztpled.leaver@example.invalid", "",
            arrival2, arrival2 + timedelta(days=2), 2, "", [], payment_status="unpaid")
    bid2 = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                        (ref2,)).fetchone()["id"]
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (bid2,))
    conn.commit()
    staff = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    sent, fake = _capture()
    m.send_email = fake
    try:
        oc.post(f"/admin/bookings/{bid2}/checkout", data={"assigned_to_user_id": str(staff)})
        departed = conn.execute("SELECT departure_date FROM bookings WHERE id = ?",
                                (bid2,)).fetchone()["departure_date"]
        days = (house_today() - m.parse_date(departed)).days
        with m.app.test_request_context("/"):
            m.run_room_feedback_job(conn, days_after=days)
    finally:
        m.send_email = was
    asked = [x for x in sent if x["to"] == "zztpled.leaver@example.invalid"]
    s.check("checking out asks, and the morning job does not ask again",
            len(asked) == 1, detail=f"{len(asked)} requests to one guest")
    s.check("in the template's words, which the owner can change",
            asked and asked[0]["subject"] == m.merge_email_text(
                _row("room_feedback_request")["subject"], {"room_name": room["name"],
                                                           "guest_name": "x"}).strip(),
            detail=str(asked[0]["subject"] if asked else None))

    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
