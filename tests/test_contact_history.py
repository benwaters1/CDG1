"""Every word to and from a guest, kept against them, and one history of it.

What there was: a letter was kept only if its address matched a ROOM booking,
so anything written to somebody who had booked an atelier, a table or an event
went nowhere; the guest's record listed the failure queue, which holds only
what did NOT go, so every letter that reached somebody was missing from it; a
letter held one morning and sent that afternoon read "did not go" for ever; a
WhatsApp was filed as a text; a telephone call left no trace at all; the owner
could write to a guest only by mailto:, which the house never saw again; and
"what happened with this guest" meant opening eleven pages.

What this holds:

  - A letter is kept against the person and what it was about, whatever they
    booked; mail to somebody who is not a guest still is not.
  - It says which template it was.
  - A held letter sent later reads as sent; a retry that fails keeps the
    provider's reason; one taken out of the queue says it never went.
  - A WhatsApp is a WhatsApp, sent or waiting.
  - The record lists what was delivered, what only the queues know about,
    and each thing once; a letter held before any of this reads as sent once
    it has gone.
  - A conversation can be written down, with a follow-up on the calendar.
  - The owner can write to them from the record, and the letter is kept.
  - What they ask from their account page is kept in full, and not in the
    audit trail.
  - One history of it all, which a colleague sees without the money and the
    letters.
  - None of it outlives the two years the notice promises.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZCH"


def _cleanup():
    conn = db()
    try:
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
        conn.execute("DELETE FROM guest_messages WHERE LOWER(to_address) LIKE ?",
                     (TAG.lower() + "%",))
        conn.execute("DELETE FROM guest_messages WHERE guest_id IN "
                     "(SELECT id FROM guests WHERE name LIKE ?)", (TAG + "%",))
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
        # Kept when its stay goes (the link is set to nothing), so by its name.
        conn.execute("DELETE FROM guest_feedback WHERE guest_name LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("""DELETE FROM workshop_bookings WHERE reference_code LIKE ?""",
                     (TAG + "%",))
        conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM sms_outbox WHERE body LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM guest_sessions WHERE email LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
        ids = "(SELECT id FROM guests WHERE name LIKE ?)"
        conn.execute(f"DELETE FROM guest_contacts WHERE guest_id IN {ids}", (TAG + "%",))
        conn.execute(f"DELETE FROM guest_notes WHERE guest_id IN {ids}", (TAG + "%",))
        conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
    finally:
        conn.close()


def _guest(name, email, phone=None):
    conn = db()
    conn.execute("INSERT INTO guests (name, email, phone, created_at) VALUES (?, ?, ?, ?)",
                 (f"{TAG} {name}", email, phone, _harness.datetime_now()))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def _rows(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _filed(address):
    return _rows("SELECT * FROM guest_messages WHERE to_address = ? ORDER BY id", address)


def _provider(ok=True, why=None):
    """Stand a mail provider in for as long as the caller needs one."""
    saved = m.resend_enabled, m.send_email_via_resend
    m.resend_enabled = lambda: True
    m.send_email_via_resend = lambda *a, **k: (ok, why)
    return saved


def _put_back(saved):
    m.resend_enabled, m.send_email_via_resend = saved


def run():
    s = Suite("Contact history")
    oc, ec, owner, emp = clients()
    _cleanup()
    try:
        _run(s, oc, ec, owner, emp)
    finally:
        _cleanup()
    return s


def _run(s, oc, ec, owner, emp):
    s.section("A letter is kept against the person, whatever they booked")
    maker = f"{TAG.lower()}.maker@example.invalid"
    gid = _guest("Maker", maker, phone="+33 6 11 77 22 33")
    conn = db()
    now = _harness.datetime_now()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                    active, sort_order, created_at, deposit_percent)
                    VALUES (?, '', 900, 10, 1, 98, ?, 10)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=60)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
                    notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), f"{TAG} S", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} S",)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
                    status, reference_code, manage_token, created_at, total_price)
                    VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, 900)""",
                 (sid, f"{TAG} Maker", maker, f"{TAG}W", f"tokw{TAG}".lower(), now))
    rid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}W",)).fetchone()["id"]
    conn.commit()
    conn.close()
    with m.app.test_request_context("/"):
        m.send_email(maker, f"{TAG} About your atelier", "The tutor will meet you at ten.",
                     about=("workshop", rid))
    rows = _filed(maker)
    s.check("a letter to somebody with only an atelier is kept",
            len(rows) == 1 and "meet you at ten" in rows[0]["body"], detail=f"{len(rows)}")
    s.check("against them, and against the atelier it was about",
            rows and rows[0]["guest_id"] == gid and rows[0]["about_category"] == "workshop"
            and rows[0]["about_id"] == rid and rows[0]["booking_id"] is None,
            detail=str(dict(rows[0]) if rows else None))
    s.check("marked held, with why", rows and not rows[0]["delivered"]
            and "no email provider" in (rows[0]["failure"] or "") and rows[0]["outbox_id"],
            detail=str(dict(rows[0]) if rows else None))
    stranger = f"{TAG.lower()}.accountant@example.invalid"
    with m.app.test_request_context("/"):
        m.send_email(stranger, "Your invoice", "Attached.")
    s.check("while mail to somebody who is not a guest is still not kept", not _filed(stranger))
    # The address they used before two profiles were made one.
    before = f"{TAG.lower()}.before@example.invalid"
    old_id = _guest("Before", before)
    conn = db()
    conn.execute("UPDATE guests SET merged_into_id = ? WHERE id = ?", (gid, old_id))
    conn.execute("""INSERT INTO guest_messages (booking_id, channel, to_address, subject, body,
                    delivered, created_at)
                    VALUES (NULL, 'email', ?, ?, 'Before the merge.', 1, ?)""",
                 (before, f"{TAG} Filed before the merge", _harness.datetime_now()))
    conn.commit()
    conn.close()
    with m.app.test_request_context("/"):
        m.send_email(before, f"{TAG} To the old address", "It still reaches you.")
    s.check("a letter to the address from before a merge is filed as theirs",
            [r["guest_id"] for r in _filed(before)
             if r["subject"] == f"{TAG} To the old address"] == [gid],
            detail=str([dict(r) for r in _filed(before)]))
    s.check("and one filed under it before the merge is on their record",
            f"{TAG} Filed before the merge" in [x["subject"] for x in _listed(gid)])
    with m.app.test_request_context("/"):
        conn = db()
        subject, body, _letter = m.render_email_template(conn, "workshop_confirmed", {
            "guest_name": "Maker", "workshop_title": "Atelier", "dates": "soon", "party_size": 1,
            "reference_code": f"{TAG}W", "total_price": "900.00", "deposit_amount": "90.00",
            "balance_amount": "810.00", "balance_line": "", "manage_url": "x"})
        conn.close()
        m.send_email(maker, subject, body)
    s.check("and it says which letter it was",
            any(r["template_key"] == "workshop_confirmed" for r in _filed(maker)),
            detail=str([r["template_key"] for r in _filed(maker)]))

    s.section("A held letter sent later reads as sent")
    held = [r for r in _filed(maker) if r["subject"] == f"{TAG} About your atelier"][0]
    saved = _provider(ok=True)
    try:
        oc.post("/admin/email-outbox/send", data={"id": str(held["outbox_id"])},
                follow_redirects=True)
    finally:
        _put_back(saved)
    after = _rows("SELECT * FROM guest_messages WHERE id = ?", held["id"])[0]
    s.check("sent from the outbox, its record says it went", after["delivered"] == 1
            and after["delivered_at"] and not after["failure"], detail=str(dict(after)))
    with m.app.test_request_context("/"):
        m.send_email(maker, f"{TAG} Second", "Again.")
    second = [r for r in _filed(maker) if r["subject"] == f"{TAG} Second"][0]
    saved = _provider(ok=False, why="the domain is not verified")
    try:
        oc.post("/admin/email-outbox/send", data={"id": str(second["outbox_id"])})
    finally:
        _put_back(saved)
    reason = _rows("SELECT last_error FROM email_outbox WHERE id = ?", second["outbox_id"])[0][0]
    s.check("a retry that fails keeps the provider's reason, not 'retry failed'",
            reason == "the domain is not verified", detail=str(reason))
    with m.app.test_request_context("/"):
        m.send_email(maker, f"{TAG} Thought better of it", "Never mind.")
    withdrawn = [r for r in _filed(maker) if r["subject"] == f"{TAG} Thought better of it"][0]
    oc.post(f"/admin/email-outbox/{withdrawn['outbox_id']}/discard")
    after = _rows("SELECT * FROM guest_messages WHERE id = ?", withdrawn["id"])[0]
    s.check("one taken out of the queue says it never went, not that it is waiting",
            not after["delivered"] and "never sent" in (after["failure"] or ""),
            detail=str(dict(after)))
    # The pile of old ones, the same: only this one, so nothing else in the
    # copy of the house's queue is thrown away to prove it.
    with m.app.test_request_context("/"):
        m.send_email(maker, f"{TAG} Long held", "Too late now.")
    stale = [r for r in _filed(maker) if r["subject"] == f"{TAG} Long held"][0]
    split = m.held_mail_split
    m.held_mail_split = lambda conn, limit=None: (
        [], conn.execute("SELECT * FROM email_outbox WHERE id = ?",
                         (stale["outbox_id"],)).fetchall())
    try:
        oc.post("/admin/email-outbox/discard-stale")
    finally:
        m.held_mail_split = split
    after = _rows("SELECT * FROM guest_messages WHERE id = ?", stale["id"])[0]
    s.check("and so do the old ones thrown away together",
            not _rows("SELECT id FROM email_outbox WHERE id = ?", stale["outbox_id"])
            and "never sent" in (after["failure"] or ""), detail=str(dict(after)))

    s.section("Texts, by the channel they went on")
    conn = db()
    m.file_guest_text(conn, "+33 6 11 77 22 33", "Your table is ready.", delivered=True,
                      channel="whatsapp")
    conn.commit()
    conn.close()
    texts = _rows("SELECT * FROM guest_messages WHERE body = 'Your table is ready.'")
    s.check("a WhatsApp is filed as a WhatsApp, against the person",
            [(t["channel"], t["guest_id"]) for t in texts] == [("whatsapp", gid)],
            detail=str([(t["channel"], t["guest_id"]) for t in texts]))
    # Through the door every text goes through, not only the filing helper:
    # the helper took the channel and the sender never passed it.
    conn = db()
    m.send_sms(conn, "+33 6 11 77 22 33", f"{TAG} your room is ready.", channel="whatsapp")
    conn.commit()
    conn.close()
    waiting = _rows("SELECT * FROM guest_messages WHERE body = ?", f"{TAG} your room is ready.")
    s.check("and one sent on WhatsApp is filed as one, while it waits too",
            [(t["channel"], t["delivered"], t["guest_id"]) for t in waiting]
            == [("whatsapp", 0, gid)], detail=str([dict(t) for t in waiting]))
    # And one that goes: a provider stood in, so nothing leaves the machine.
    was = m.whatsapp_enabled, m.sms_provider_send
    m.whatsapp_enabled = lambda: True
    m.sms_provider_send = lambda *a, **k: (True, "SMzzch")
    try:
        conn = db()
        m.send_sms(conn, "+33 6 11 77 22 33", f"{TAG} your table is at eight.",
                   channel="whatsapp")
        conn.commit()
        conn.close()
    finally:
        m.whatsapp_enabled, m.sms_provider_send = was
    went = _rows("SELECT * FROM guest_messages WHERE body = ?", f"{TAG} your table is at eight.")
    s.check("and when it goes",
            [(t["channel"], t["delivered"]) for t in went] == [("whatsapp", 1)],
            detail=str([dict(t) for t in went]))

    s.section("The record lists what was delivered")
    saved = _provider(ok=True)
    try:
        with m.app.test_request_context("/"):
            m.send_email(maker, f"{TAG} Delivered letter", "This one reached them.")
    finally:
        _put_back(saved)
    page = visible_text(oc.get(f"/guests/{gid}").get_data(as_text=True))
    # In the list of what was sent, marked as sent -- not merely somewhere on
    # the page, which carries the history as well and lists it there too.
    went = [x["subject"] for x in _listed(gid) if x["sent"]]
    s.check("a letter that reached them is on their record",
            f"{TAG} Delivered letter" in went and f"{TAG} Delivered letter" in page,
            detail="the record read the failure queue, which holds only what did not go: "
                   + str(went[:6]))
    number = m.normalise_phone("+33 6 11 77 22 33")
    conn = db()
    conn.execute("""INSERT INTO email_outbox (to_address, subject, body, reason, created_at)
                    VALUES (?, ?, 'Only the queue has this.', 'no provider', ?)""",
                 (maker, f"{TAG} Held before it was filed", _harness.datetime_now()))
    conn.execute("""INSERT INTO sms_outbox (phone, body, purpose, reason, created_at)
                    VALUES (?, ?, 'transactional', 'no provider', ?)""",
                 (number, f"{TAG} a text only the queue has", _harness.datetime_now()))
    conn.commit()
    conn.close()
    listed = _listed(gid)
    subjects = [x["subject"] for x in listed]
    bodies = [x["body"] for x in listed]
    s.check("a letter only the queue knows about is still on it",
            f"{TAG} Held before it was filed" in subjects, detail=str(subjects))
    s.check("and so is a text only the queue knows about",
            f"{TAG} a text only the queue has" in bodies, detail=str(bodies))
    s.check("each letter once, though it is both filed and queued",
            subjects.count(f"{TAG} About your atelier") == 1, detail=str(subjects))
    s.check("and each text once", bodies.count(f"{TAG} your room is ready.") == 1,
            detail=str(bodies))

    s.section("Letters held before any of this, and sent since")
    # Filed as they used to be -- against the address, with no place in the
    # queue -- and sent from the queue afterwards. On a house that has never
    # had a way to send mail, that is every letter it has written. The same
    # letter twice, because a reminder goes more than once.
    conn = db()
    then = _harness.datetime_now()
    for _ in range(2):
        conn.execute("""INSERT INTO guest_messages (booking_id, channel, to_address, subject,
                        body, delivered, created_at)
                        VALUES (NULL, 'email', ?, ?, 'From before.', 0, ?)""",
                     (maker.upper(), f"{TAG} From before", then))
        conn.execute("""INSERT INTO email_outbox (to_address, subject, body, reason, created_at,
                        sent_at) VALUES (?, ?, 'From before.', 'no provider', ?, ?)""",
                     (maker, f"{TAG} From before", then, _harness.datetime_now()))
    conn.commit()
    conn.close()
    m.init_db()
    old = _rows("SELECT * FROM guest_messages WHERE subject = ? ORDER BY id", f"{TAG} From before")
    s.check("each is joined to its own held copy, and reads as sent",
            len(old) == 2 and len({x["outbox_id"] for x in old}) == 2
            and all(x["outbox_id"] and x["delivered"] == 1 and x["delivered_at"] for x in old),
            detail=str([dict(x) for x in old]))
    s.check("and the record lists the two, not three",
            [x["subject"] for x in _listed(gid)].count(f"{TAG} From before") == 2,
            detail=str([x["subject"] for x in _listed(gid)]))

    s.section("A conversation, written down")
    follow = (house_today() + timedelta(days=3)).isoformat()
    r = ec.post(f"/guests/{gid}/contact",
                data={"channel": "phone", "direction": "in", "summary": f"{TAG} asked about parking",
                      "follow_up_on": follow}, follow_redirects=True)
    spoken = _rows("SELECT * FROM guest_contacts WHERE guest_id = ?", gid)
    s.check("a colleague can write down a call", len(spoken) == 1
            and spoken[0]["channel"] == "phone" and "parking" in spoken[0]["summary"],
            detail=str(flashes(r)))
    task = _rows("SELECT * FROM tasks WHERE id = ?", spoken[0]["task_id"]) if spoken else []
    s.check("and a date to follow up on is a task with that date",
            task and task[0]["due_date"] == follow and TAG in task[0]["title"],
            detail=str([dict(t) for t in task]))
    ec.post(f"/guests/{gid}/contact", data={"channel": "phone", "summary": "  "})
    s.check("a conversation with nothing said is not written down",
            len(_rows("SELECT id FROM guest_contacts WHERE guest_id = ?", gid)) == 1)
    s.check("and their history names who noted the conversation",
            any(x["kind"] == "Conversation" and emp["name"] in x["title"]
                for x in _rows_of(m.guest_timeline, gid)),
            detail=str([x["title"] for x in _rows_of(m.guest_timeline, gid)
                        if x["kind"] == "Conversation"]))

    s.section("The owner writes to them from the record")
    saved = _provider(ok=True)
    try:
        oc.post(f"/guests/{gid}/write", data={"subject": f"{TAG} A note from the house",
                                              "body": "We have kept the blue room for you.",
                                              "area": "rooms"})
        ec.post(f"/guests/{gid}/write", data={"subject": f"{TAG} From staff", "body": "x"})
    finally:
        _put_back(saved)
    letter = [x for x in _filed(maker) if x["subject"] == f"{TAG} A note from the house"]
    s.check("it is sent and kept, with who wrote it",
            letter and letter[0]["delivered"] and letter[0]["sent_by_user_id"],
            detail=str([dict(x) for x in letter]))
    s.check("and a colleague cannot write as the house",
            not [x for x in _filed(maker) if x["subject"] == f"{TAG} From staff"])
    s.check("and their history names who wrote the letter",
            any(f"{TAG} A note from the house" in x["title"] and owner["name"] in x["title"]
                for x in _rows_of(m.guest_timeline, gid)),
            detail=str([x["title"] for x in _rows_of(m.guest_timeline, gid)
                        if x["kind"] == "Letter"][:4]))

    s.section("What they ask from their account page")
    conn = db()
    now = datetime.now(timezone.utc)
    conn.execute("""INSERT INTO guest_sessions (email, token, created_at, expires_at)
                    VALUES (?, ?, ?, ?)""",
                 (maker, TAG.lower() + "session", now.isoformat(),
                  (now + timedelta(hours=48)).isoformat()))
    conn.commit()
    conn.close()
    words = f"{TAG} could we bring the dog, a small one, very quiet, who sleeps all day " * 4
    m.app.test_client().post(f"/my-account/{TAG.lower()}session/ask", data={"message": words})
    asked = [x for x in _filed(maker) if x["direction"] == "in"]
    s.check("is kept in full, as theirs to us",
            asked and asked[0]["body"] == words.strip()[:2000] and asked[0]["guest_id"] == gid,
            detail=str([dict(x) for x in asked]))
    # By their profile: the trail no longer writes the address down.
    audit = _rows("SELECT details FROM audit_log WHERE action = 'guest_wrote_in' AND target = ? "
                  "ORDER BY id DESC LIMIT 1", f"guest {gid}")
    s.check("and not copied into the audit trail, which is kept for ever",
            audit and "dog" not in (audit[0][0] or ""), detail=str([tuple(a) for a in audit]))

    s.section("One history of it all")
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=30)
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    amount_paid, created_at, decided_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, 400, ?, ?)""",
                 (room, f"{TAG}S", f"toks{TAG}".lower(), f"{TAG} Maker", maker,
                  arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
                  _harness.datetime_now(), _harness.datetime_now()))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()["id"]
    conn.execute("INSERT INTO booking_payments (booking_id, amount, method, created_at) "
                 "VALUES (?, 400, 'cash', ?)", (bid, _harness.datetime_now()))
    conn.execute("INSERT INTO guest_notes (guest_id, body, created_at) VALUES (?, ?, ?)",
                 (gid, f"{TAG} prefers the room at the back", _harness.datetime_now()))
    conn.execute("""INSERT INTO guest_feedback (booking_id, guest_name, rating, comment,
                    submitted_at) VALUES (?, ?, 5, ?, ?)""",
                 (bid, f"{TAG} Maker", f"{TAG} the view from the tower", _harness.datetime_now()))
    conn.commit()
    conn.close()
    oc.post("/admin/emails/optout", data={"email": maker})
    items = _rows_of(m.guest_timeline, gid)
    kinds = {x["kind"] for x in items}
    s.check("what they said about their stay is on it, in their words",
            any(x["kind"] == "Feedback" and "the view from the tower" in x["detail"] for x in items),
            detail=str([(x["title"], x["detail"]) for x in items if x["kind"] == "Feedback"]))
    s.check("letters, texts, conversations, notes, bookings, money and consent are all on it",
            {"Letter", "Text", "They wrote", "Conversation", "Note", "Booking", "Money",
             "Consent"} <= kinds, detail=str(sorted(kinds)))
    s.check("newest first", [x["at"] for x in items] == sorted((x["at"] for x in items), reverse=True))
    page = oc.get(f"/guests/{gid}/history").get_data(as_text=True)
    s.check("its page opens, with every kind of thing on it",
            "History" in page and "parking" in page and "prefers the room at the back" in page)
    only = visible_text(oc.get(f"/guests/{gid}/history?kind=Conversation").get_data(as_text=True))
    s.check("and narrows to one kind of thing", "parking" in only
            and "prefers the room at the back" not in only)
    theirs = visible_text(ec.get(f"/guests/{gid}/history").get_data(as_text=True))
    s.check("a colleague sees the conversations and the notes",
            "parking" in theirs and "prefers the room at the back" in theirs)
    s.check("but not the money or the letters, which are the owner's",
            "A note from the house" not in theirs and "Paid, cash" not in theirs)
    record = visible_text(oc.get(f"/guests/{gid}").get_data(as_text=True))
    s.check("and the record carries the latest of it", "History" in record and "parking" in record)
    colleague = visible_text(ec.get(f"/guests/{gid}").get_data(as_text=True))
    s.check("a colleague's copy of the record keeps to the same line",
            "parking" in colleague and "A note from the house" not in colleague
            and "Paid, cash" not in colleague)

    s.section("None of it outlives the two years")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()
    conn = db()
    conn.execute("""INSERT INTO guest_messages (booking_id, channel, to_address, subject, body,
                    delivered, created_at, guest_id) VALUES (NULL, 'email', ?, ?, 'old', 1, ?, ?)""",
                 (maker, f"{TAG} Long ago", long_ago, gid))
    conn.execute("""INSERT INTO guest_contacts (guest_id, channel, direction, summary, created_at)
                    VALUES (?, 'phone', 'in', ?, ?)""", (gid, f"{TAG} a call long ago", long_ago))
    conn.commit()
    m.purge_guest_messages(conn)
    conn.close()
    s.check("a letter about no stay goes two years after it was written",
            not [x for x in _filed(maker) if x["subject"] == f"{TAG} Long ago"]
            and [x for x in _filed(maker) if x["subject"] == f"{TAG} A note from the house"])
    s.check("and so does a conversation",
            not _rows("SELECT id FROM guest_contacts WHERE summary = ?", f"{TAG} a call long ago")
            and _rows("SELECT id FROM guest_contacts WHERE guest_id = ?", gid))
    notice = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("and the notice says so", "When we speak to you" in notice
            and "two years after we wrote" in notice)

    s.section("A request for their data carries all of it, and erasing it takes it")
    conn = db()
    export = m.guest_data_export(conn, maker)
    conn.close()
    held = (export or {}).get("tables", {})
    s.check("the export carries the conversations written down with them",
            any("parking" in (r.get("summary") or "") for r in held.get("guest_contacts", [])),
            detail=str(sorted(held)))
    s.check("and the texts, which are filed by the number rather than the address",
            any((r.get("body") or "") == "Your table is ready." for r in held.get("guest_messages", [])),
            detail=str([r.get("to_address") for r in held.get("guest_messages", [])][:8]))
    conn = db()
    m.guest_data_erase(conn, maker)
    conn.commit()
    conn.close()
    s.check("erasing them takes the texts filed by the number too",
            not _rows("SELECT id FROM guest_messages WHERE body = 'Your table is ready.'")
            and not _rows("SELECT id FROM guest_contacts WHERE guest_id = ?", gid))


def _listed(gid):
    """What the guest's record lists as sent to them."""
    conn = db()
    try:
        guest = conn.execute("SELECT * FROM guests WHERE id = ?", (gid,)).fetchone()
        with m.app.test_request_context("/"):
            return m.guest_messages(conn, guest)
    finally:
        conn.close()


def _rows_of(fn, *args):
    conn = db()
    try:
        return fn(conn, *args)
    finally:
        conn.close()
