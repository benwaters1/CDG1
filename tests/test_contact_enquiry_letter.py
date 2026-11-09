"""The letter the house gets for a Contact enquiry, and the ones that say nothing.

9 November 2026, the owner, holding up a letter that read in full "Private
event. Test / They left this address: marcel_edith@lacoe.edu": "why are there
so many of these, what are they? are they spam, there's no info, you haven't
built it properly".

Two faults. A script was trying the form -- a different real address each
time, no name, no month, no guests, the message "Test" -- and it waits out the
stamp, so the bot guard let it in and every one became a task and a letter.
And the letter itself was the notes run together: no name, nothing to say
which questions were left blank, no way from it to the enquiry.

Held here:
  - the letter puts each answer on its own line, "not said" where it was left
    blank, the message under its own heading, and ends with the link that
    opens the enquiry on the waitlist;
  - a named enquiry says who in the subject;
  - a message that is only "Test", "hello" or a row of keys is kept as Spam,
    with no task and no letter, and the visitor is thanked the same;
  - a message that merely contains the word ("a test of our cooking") is not;
  - the ones already received are set aside by tidy_contact_enquiries, and a
    real one beside them is left alone.
"""
from urllib.parse import urlsplit

from _harness import Suite, db, flashes
import _harness

m = _harness.m
TAG = "zzcel"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR notes LIKE ?",
                 ("%" + TAG + "%", "%" + TAG + "%"))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()


def _post(who, **fields):
    sent = []
    real = m.send_email
    m.send_email = lambda to, subject, body, *a, **kw: sent.append((to, subject, body)) or True
    data = {"action": "flexible", "email": f"{TAG}.{who}@example.invalid",
            "name": "", "rough_month": "", "rough_guests": "", "topic": "private_event",
            "message": "Test"}
    data.update(_harness.contact_form_fields())
    data.update(fields)
    try:
        r = m.app.test_client().post("/contact", data=data, follow_redirects=True)
    finally:
        m.send_email = real
    conn = db()
    entry = conn.execute("SELECT * FROM waitlist_entries WHERE email = ? ORDER BY id DESC",
                         (data["email"],)).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE waitlist_entry_id = ?",
                        (entry["id"],)).fetchone() if entry else None
    conn.execute("DELETE FROM submission_log WHERE action = 'contact_enquiry'")
    conn.commit()
    conn.close()
    return r, entry, task, [x for x in sent if x[1].startswith("Someone wrote to us")]


def _month_ahead(months):
    today = m.house_today()
    y, mo = divmod(today.month - 1 + months, 12)
    return "%04d-%02d" % (today.year + y, mo + 1)


def run():
    s = Suite("The Contact enquiry letter, and messages that say nothing")
    _cleanup()
    try:
        s.section("A real enquiry, every question answered")
        month = _month_ahead(3)
        r, entry, task, letters = _post(
            "full", name="Zzcel Marchetti", rough_month=month, rough_guests="40",
            message="A birthday dinner for my mother, forty of us, a Saturday if possible.")
        thanks = flashes(r)
        s.check("is kept, with a task", entry is not None and task is not None)
        s.check("and one letter", len(letters) == 1, detail=str(len(letters)))
        _to, subject, body = letters[0] if letters else ("", "", "")
        s.check("the subject says what and who",
                subject == "Someone wrote to us: private event — Zzcel Marchetti", detail=subject)
        lines = body.split("\n")
        for want in ("Name: Zzcel Marchetti", f"Email: {TAG}.full@example.invalid",
                     "About: Private event", "When: %s" % m.month_words(month), "Guests: 40",
                     "Their message:",
                     "A birthday dinner for my mother, forty of us, a Saturday if possible."):
            s.check(f"a line of its own: {want}", want in lines, detail=body)
        link = next((ln for ln in lines if "/admin/waitlist" in ln), "")
        s.check("it ends with the link that opens the enquiry",
                link.startswith("http") and f"q={TAG}.full@example.invalid" in link
                and link.endswith(f"#entry-{entry['id']}" if entry else "#"), detail=link)
        parts = urlsplit(link)
        page = _harness.clients()[0].get(parts.path + "?" + parts.query).get_data(as_text=True)
        s.check("and that link opens it for the owner",
                entry is not None and f'id="entry-{entry["id"]}"' in page, detail=link)
        s.check("and the old run-together line is gone", "They left this address" not in body)

        s.section("A real enquiry with the questions left blank")
        r, entry, task, letters = _post("blank", message="Is the house free for a wedding next spring?")
        body = letters[0][2] if letters else ""
        lines = body.split("\n")
        for want in ("Name: not said", "When: not said", "Guests: not said"):
            s.check(f"says so: {want}", want in lines, detail=body)
        s.check("and the subject names nobody",
                bool(letters) and letters[0][1] == "Someone wrote to us: private event",
                detail=letters and letters[0][1])

        s.section("A message that says nothing")
        for who, message in (("test", "Test"), ("testing", "testing."), ("hello", "Hello!"),
                             ("keys", "asdfgh"), ("digits", "123"), ("this", "This is a test")):
            r, entry, task, letters = _post(who, message=message)
            s.check(f"{message!r} is kept as Spam", entry is not None and bool(entry["spam_at"]),
                    detail=str(entry and dict(entry)))
            s.check(f"{message!r} makes no task and no letter", task is None and not letters,
                    detail=f"task={task is not None} letters={len(letters)}")
            s.check(f"{message!r} is thanked like anybody", flashes(r) == thanks,
                    detail=str(flashes(r)))
        r, entry, task, letters = _post("cook", message="A test of our team's cooking, for twelve?")
        s.check("a message that only contains the word is a real one",
                entry is not None and not entry["spam_at"] and task is not None and len(letters) == 1)

        s.section("The ones already received")
        conn = db()
        now = _harness.datetime_now()
        ids = {}
        for who, notes in (("oldtest", "Private event. Test"),
                           ("oldwed", "Wedding. Around June 2027. 80 guests. Test"),
                           ("oldreal", "Private event. 40 guests. Hello, a birthday dinner for forty?")):
            ids[who] = conn.execute(
                """INSERT INTO waitlist_entries (name, email, phone, party_size, notes, status,
                     created_at, source) VALUES ('', ?, '', NULL, ?, 'enquiry', ?, 'contact')""",
                (f"{TAG}.{who}@example.invalid", notes, now)).lastrowid
            conn.execute(
                """INSERT INTO tasks (title, notes, priority, due_date, status, origin,
                     created_at, waitlist_entry_id)
                   VALUES (?, ?, 'normal', ?, 'open', 'enquiry', ?, ?)""",
                (f"Answer an enquiry from {TAG}.{who}@example.invalid: private event",
                 notes, m.house_today_iso(), now, ids[who]))
        conn.commit()
        m.tidy_contact_enquiries(conn)
        rows = {who: conn.execute("SELECT spam_at FROM waitlist_entries WHERE id = ?",
                                  (i,)).fetchone()["spam_at"] for who, i in ids.items()}
        tasks = {who: conn.execute("SELECT COUNT(*) FROM tasks WHERE waitlist_entry_id = ?",
                                   (i,)).fetchone()[0] for who, i in ids.items()}
        conn.close()
        s.check("\"Private event. Test\" is set aside, its task gone",
                bool(rows["oldtest"]) and tasks["oldtest"] == 0, detail=f"{rows} {tasks}")
        s.check("so is one with a month and guests in front of its \"Test\"",
                bool(rows["oldwed"]) and tasks["oldwed"] == 0, detail=f"{rows} {tasks}")
        s.check("a real one beside them is left alone, task and all",
                not rows["oldreal"] and tasks["oldreal"] == 1, detail=f"{rows} {tasks}")
    finally:
        _cleanup()
    return s
