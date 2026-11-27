"""No copy of a guest's dietary note outlives the booking it was for (owner, 27 November 2026).

The privacy notice says a note on a booking "is deleted once that booking is over", and
purge_health_notes deletes it from the booking. But three things kept copies:

  - THE GUEST'S DINNER LETTERS (received, confirmed, declined, cancelled) quote the note,
    which is right while the dinner is coming, and are kept two years in their
    correspondence and, while held, the outbox. Once the dinner is over the purge now takes
    the note out of those copies (scrub_dinner_letters) and leaves the rest of the letter.
    Letters write the note on one line, so its end is the line's end; a note of several
    lines from before that, and one the guest changed after the letter went, come out too.
  - THE MORNING SUMMARY quotes each dinner's note and is kept two years in the mail log.
    Every summary from a day before today loses the notes (scrub_morning_summaries), and
    keeps who is dining and how many.
  - THE ASSISTANT sent "CANNOT EAT: ..." to Anthropic, which the notice says never happens
    to anything about anybody's health. It now says that somebody has a note and links the
    kitchen's list, and its earlier answers that quoted notes, which went back to Anthropic
    with every new thing said, are replaced at startup (scrub_assistant_health_answers).
    test_assistant holds the lookups themselves.
"""
import inspect
from datetime import datetime, time, timedelta, timezone

import _harness
from _harness import Suite, clients, db, house_today

m = _harness.m
TAG = "ZZHC"

# Words found nowhere else in the app, so finding them anywhere is unambiguous.
NOTE = "Zzhc no sesame at all\nZzhc one of us is coeliac"
CHANGED = "Zzhc words from before the guest changed them"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM restaurant_bookings WHERE reference_code LIKE ?", (f"{TAG}-%",))]
    for bid in ids:
        conn.execute("DELETE FROM email_outbox WHERE id IN (SELECT outbox_id FROM guest_messages "
                     "WHERE about_category = 'restaurant' AND about_id = ?)", (bid,))
        conn.execute("DELETE FROM guest_messages WHERE about_category = 'restaurant' "
                     "AND about_id = ?", (bid,))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (f"{TAG}-%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM mail_log WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM assistant_messages WHERE tool_call_id LIKE ?", (f"{TAG}%",))
    conn.commit()


def _dinner(conn, ref, day, note):
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
               party_size, dinner_date, status, total_price, dietary_notes, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', 130, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"Anne {TAG}{ref}",
         f"{TAG.lower()}{ref.lower()}@example.invalid", day.isoformat(), note,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT * FROM restaurant_bookings WHERE reference_code = ?",
                        (f"{TAG}-{ref}",)).fetchone()


def _kept_letter(conn, booking, body, held=True):
    """A letter filed as send_email files a guest's letter about a table."""
    now = datetime.now(timezone.utc).isoformat()
    outbox_id = conn.execute(
        "INSERT INTO email_outbox (to_address, subject, body, reason, created_at) "
        "VALUES (?, ?, ?, 'held', ?)",
        (booking["guest_email"], f"Your table {TAG}", body, now)).lastrowid if held else None
    gm = conn.execute(
        """INSERT INTO guest_messages (to_address, subject, body, created_at, about_category,
               about_id, template_key, outbox_id)
           VALUES (?, ?, ?, ?, 'restaurant', ?, 'restaurant_reservation_received', ?)""",
        (booking["guest_email"], f"Your table {TAG}", body, now, booking["id"],
         outbox_id)).lastrowid
    conn.commit()
    return gm, outbox_id


def _body(conn, table, row_id):
    return conn.execute(f"SELECT body FROM {table} WHERE id = ?", (row_id,)).fetchone()["body"]


def _purge(conn, today):
    """As the automation tick runs it: inside a stand-in request, so log_audit can resolve
    who (nobody) triggered it."""
    with m.app.test_request_context():
        return m.purge_health_notes(conn, today=today)


def _summary(conn, day, body):
    stamp = datetime.combine(day, time(9, 0), tzinfo=timezone.utc).isoformat()
    return conn.execute(
        "INSERT INTO mail_log (to_address, subject, status, body, kept, template_key, created_at) "
        "VALUES ('tech@example.invalid', ?, 'sent', ?, 'here', 'code:run_morning_digest_job', ?)",
        (f"The morning {TAG} {day.isoformat()}", body, stamp)).lastrowid


def run():
    s = Suite("No copy of a guest's dietary note outlives the booking it was for")
    _oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    try:
        _letters(s, conn)
        _summaries(s, conn)
        _assistant(s, conn, owner)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _letters(s, conn):
    s.section("A dinner letter quotes the note on one line")
    day = house_today() + timedelta(days=60)
    b = _dinner(conn, "A", day, NOTE)
    with m.app.test_request_context():
        ctx = m.restaurant_email_context(b)
        _subject, body, _letter = m.render_email_template(
            conn, "restaurant_reservation_received", ctx)
    one = "Zzhc no sesame at all; Zzhc one of us is coeliac"
    s.check("the guest is still told what we have", ctx["dietary_line"] == f"\nDietary notes: {one}",
            detail=repr(ctx["dietary_line"]))
    s.check("and the letter carries it", one in (body or ""), detail=(body or "")[:400])

    s.section("Once the dinner is over, the copies lose it")
    multi = ("Dear Anne,\n\nYour table is booked.\nDietary notes: " + NOTE
             + "\n\nDeposit paid: €40.00\n\nWith our best wishes")
    changed = "Dear Anne,\nDietary notes: " + CHANGED + "\n\nDeposit paid: €40.00"
    gm_new, box_new = _kept_letter(conn, b, body or "")
    gm_multi, box_multi = _kept_letter(conn, b, multi)
    gm_changed, _ = _kept_letter(conn, b, changed, held=False)
    later = _dinner(conn, "LATER", day + timedelta(days=10), NOTE)
    gm_later, _ = _kept_letter(conn, later, "Dietary notes: " + m.note_on_one_line(NOTE), held=False)

    _purge(conn, day)
    s.check("on the day of the dinner nothing is touched",
            one in _body(conn, "guest_messages", gm_new) and NOTE in _body(conn, "guest_messages", gm_multi))

    cleared = _purge(conn, day + timedelta(days=1))
    s.check("the day after, the purge says what it took", cleared.get("dinner_letters") == 5,
            detail=str(cleared))
    for label, table, rid in (("the letter as letters are written now", "guest_messages", gm_new),
                              ("and its held copy in the outbox", "email_outbox", box_new),
                              ("a letter from before, with the note over two lines", "guest_messages", gm_multi),
                              ("and its outbox copy", "email_outbox", box_multi),
                              ("a letter whose note the guest changed afterwards", "guest_messages", gm_changed)):
        kept = _body(conn, table, rid)
        s.check(f"{label}: no word of the note is left",
                "Zzhc" not in kept and "sesame" not in kept and "coeliac" not in kept, detail=kept)
        s.check(f"{label}: it says the note went, and why",
                m.LETTER_NOTE_KEPT_AS in kept, detail=kept)
    multi_kept = _body(conn, "guest_messages", gm_multi)
    s.check("the rest of the letter is as it was",
            multi_kept.startswith("Dear Anne,\n\nYour table is booked.\n")
            and "Deposit paid: €40.00\n\nWith our best wishes" in multi_kept, detail=multi_kept)
    s.check("the note is gone from the booking too", conn.execute(
        "SELECT dietary_notes FROM restaurant_bookings WHERE id = ?", (b["id"],)).fetchone()[0] is None)
    s.check("a dinner still to come keeps its letter whole",
            "Zzhc no sesame" in _body(conn, "guest_messages", gm_later))
    s.check("run again, it finds nothing",
            _purge(conn, day + timedelta(days=1)).get("dinner_letters") == 0)


def _summaries(s, conn):
    s.section("The morning summary writes each dinner on one line")
    day = house_today() + timedelta(days=90)
    _dinner(conn, "M", day, NOTE)
    with m.app.test_request_context():
        _subject, body, _has = m.morning_digest(conn, day)
    line = f"  Anne {TAG}M, 2 — Zzhc no sesame at all; Zzhc one of us is coeliac"
    s.check("the note is there on the day, on its dinner's line", line in body, detail=body)

    s.section("Summaries from before today lose the notes")
    old_style = ("Good morning.\n\nDinner (2 booking(s), 4 cover(s)):\n"
                 f"  Anne {TAG}M, 2 — Zzhc no sesame at all\nZzhc one of us is coeliac\n"
                 f"  Bob {TAG}, 2\n\nWants looking at:\n- the boiler — Zzkeep this line")
    sent = _summary(conn, day, body)
    old = _summary(conn, day, old_style)
    today_one = _summary(conn, day + timedelta(days=1), body)
    conn.commit()
    cleared = _purge(conn, day + timedelta(days=1))
    s.check("the purge says what it took", cleared.get("morning_summaries") == 2, detail=str(cleared))
    for label, rid in (("a summary as they are written now", sent),
                       ("one from before, with a note over two lines", old)):
        kept = _body(conn, "mail_log", rid)
        s.check(f"{label}: no word of a note is left",
                "Zzhc" not in kept and "sesame" not in kept and "coeliac" not in kept, detail=kept)
        s.check(f"{label}: who was dining stays, and that they had a note",
                f"  Anne {TAG}M, 2{m.SUMMARY_NOTE_KEPT_AS}" in kept, detail=kept)
    kept = _body(conn, "mail_log", old)
    s.check("a dinner with no note keeps its line as it was", f"  Bob {TAG}, 2\n" in kept, detail=kept)
    s.check("and everything after the dinners is untouched",
            "Wants looking at:\n- the boiler — Zzkeep this line" in kept, detail=kept)
    s.check("today's summary keeps its notes: the dinner has not happened",
            "Zzhc no sesame" in _body(conn, "mail_log", today_one))
    s.check("run again, it finds nothing",
            _purge(conn, day + timedelta(days=1)).get("morning_summaries") == 0)


def _assistant(s, conn, owner):
    s.section("The assistant's earlier answers lose the notes")
    now = datetime.now(timezone.utc).isoformat()
    rows = {
        "kitchen": "2026-11-27: 4 cover(s).\nAnne (dinner, table, 2 cover(s)): Zzhc sesame | Zzhc nuts",
        "arrivals": "ARRIVING Anne — The King Room, party of 2 · CANNOT EAT: Zzhc sesame",
        "find_guest": "Anne · 2 stay(s) made · CANNOT EAT: Zzhc coeliac",
        "get_today": "Today: nothing Zzkeep",
    }
    ids = {}
    for i, (tool, content) in enumerate(rows.items()):
        ids[tool] = conn.execute(
            "INSERT INTO assistant_messages (user_id, role, content, tool_name, tool_call_id, created_at) "
            "VALUES (?, 'tool', ?, ?, ?, ?)", (owner["id"], content, tool, f"{TAG}{i}", now)).lastrowid
    new_style = conn.execute(
        "INSERT INTO assistant_messages (user_id, role, content, tool_name, tool_call_id, created_at) "
        "VALUES (?, 'tool', ?, 'kitchen', ?, ?)",
        (owner["id"], "2026-11-27: 2 cover(s).\nAnne (dinner, table, 2 cover(s)): has a note",
         f"{TAG}new", now)).lastrowid
    conn.commit()
    n = m.scrub_assistant_health_answers(conn)
    said = {t: conn.execute("SELECT content FROM assistant_messages WHERE id = ?", (i,)).fetchone()[0]
            for t, i in ids.items()}
    s.check("the three answers that quoted a note are replaced", n == 3, detail=f"replaced {n}")
    s.check("each says why it was taken out",
            all(said[t] == m.ASSISTANT_ANSWER_TAKEN_OUT for t in ("kitchen", "arrivals", "find_guest")),
            detail=str(said))
    s.check("an answer with no note in it is left alone", "Zzkeep" in said["get_today"])
    s.check("and so is one written the new way", conn.execute(
        "SELECT content FROM assistant_messages WHERE id = ?", (new_style,)).fetchone()[0].endswith("has a note"))
    history = m.assistant_messages_for_model(m.assistant_history(conn, owner["id"]))
    sent = " ".join(str(h.get("content")) for h in history)
    s.check("nothing quoting a note goes back to the model with the conversation",
            "Zzhc" not in sent, detail=sent[-300:])
    s.check("run again, it finds nothing", m.scrub_assistant_health_answers(conn) == 0)
    s.check("it runs at every start",
            "scrub_assistant_health_answers(conn)" in inspect.getsource(m.init_db))
    tool_src = inspect.getsource(m.assistant_read_tool)
    s.check("no lookup quotes a dietary note any more",
            "CANNOT EAT" not in tool_src and "VERBATIM" not in tool_src)
