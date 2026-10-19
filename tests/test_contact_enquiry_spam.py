"""The Contact page's enquiries: a pitch is set aside, and every task can be opened and settled.

18 October 2026, the owner: "where is this enquiry thing from, I can't click
it or anything or see where it's to or delete it or mark it as done. It looks
like a spam enquiry, there's been a few already, I don't want them to pop up."

The "tell us roughly when" form on Contact saves an enquiry on the room
waitlist, makes an "Answer an enquiry from ..." task for the day and writes
to the house. What came through it was an SEO pitch, and its task sat on the
owner home's day as plain text: no link, no tick, no delete.

Held here:
  - a real enquiry still makes its entry, a task tied to it, and the letter;
  - a pitch is kept as Spam with no task and no letter, and the visitor is
    thanked the same either way;
  - a sender marked Spam is set aside the next time, whatever they write;
  - the day's list opens an enquiry's task at its enquiry;
  - Contacted/Booked/Closed tick the task, Spam removes it, Open reopens it;
  - enquiries made before any of this are tied to their tasks, and an open
    pitch among them is set aside, by tidy_contact_enquiries;
  - the waitlist hides Spam unless asked, and labels an enquiry as one.
"""
from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZCES"
PITCH = ("Hello, I wanted to reach out to see if you're open to exploring ways to grow "
         "your website traffic and boost online performance. We offer customized SEO "
         "services. Once you share your target keywords I'll send a full proposal.")


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG.lower() + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _send(anon, who, message, name=None):
    sent = []
    real = m.send_email
    m.send_email = lambda to, subject, body, *a, **kw: sent.append((to, subject, body)) or True
    try:
        r = anon.post("/contact", data={
            "action": "flexible", "email": f"{TAG.lower()}.{who}@example.invalid",
            "name": name or f"{TAG} {who}", "rough_month": "", "rough_nights": "0",
            "rough_guests": "2", "flexibility": "any", "message": message,
            **_harness.contact_form_fields()},
            follow_redirects=True)
    finally:
        m.send_email = real
    conn = db()
    entry = conn.execute("SELECT * FROM waitlist_entries WHERE email = ? ORDER BY id DESC",
                         (f"{TAG.lower()}.{who}@example.invalid",)).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE waitlist_entry_id = ?",
                        (entry["id"],)).fetchone() if entry else None
    conn.close()
    return r, entry, task, [x for x in sent if x[1] == "Someone asked about staying"]


def _task(task_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Contact enquiries and spam")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    try:
        s.section("A real enquiry")
        r, entry, task, letters = _send(anon, "guest", "Two of us, a week in June, any chance?")
        s.check("is saved as an enquiry from the Contact page",
                entry is not None and entry["status"] == "enquiry" and entry["source"] == "contact",
                detail=str(entry and dict(entry)))
        s.check("with a task tied to it", task is not None and task["status"] == "open")
        s.check("and the house is written to", len(letters) == 1, detail=str(len(letters)))
        thanks = " ".join(flashes(r))

        s.section("A sales pitch")
        r, spam, spam_task, letters = _send(anon, "pitch", PITCH)
        s.check("is kept, as Spam", spam is not None and spam["spam_at"]
                and spam["status"] == "closed", detail=str(spam and dict(spam)))
        s.check("with no task on the day", spam_task is None)
        s.check("and no letter to the house", not letters, detail=str(len(letters)))
        s.check("and the visitor is thanked the same", " ".join(flashes(r)) == thanks,
                detail=" ".join(flashes(r)))

        s.section("The day's list opens the task, and the enquiry settles it")
        conn = db()
        with m.app.test_request_context("/"):
            owner = conn.execute("SELECT * FROM users WHERE role = 'owner' ORDER BY id").fetchone()
            day = m.owner_home_day(conn, m.house_today(), owner)
        conn.close()
        row = next((x for x in day if x["title"] == task["title"]), None)
        s.check("the task is on today's list", row is not None)
        s.check("and opens its enquiry", row is not None and row.get("href", "").endswith(f"#entry-{entry['id']}"),
                detail=str(row and row.get("href")))
        s.check("every row on the day opens something", all(x.get("href") for x in day),
                detail=str([x["title"] for x in day if not x.get("href")][:3]))
        # The assistant reads the same day outside any request, where url_for
        # raises. d222f51 broke it the moment a task was due; a full run found
        # it, depending on what an earlier suite had left on the day.
        conn = db()
        try:
            bare = m.owner_home_day(conn, m.house_today(), owner)
            err = None
        except RuntimeError as e:
            bare, err = [], str(e)
        conn.close()
        s.check("and the assistant can still read the day, outside a request",
                err is None and any(x["title"] == task["title"] for x in bare), detail=err)
        page = oc.get(row["href"].split("#")[0]).get_data(as_text=True) if row else ""
        s.check("where the enquiry is, labelled as one",
                f'id="entry-{entry["id"]}"' in page and "Enquiry from the Contact page" in page)

        for status, want in (("contacted", "done"), ("open", "open"), ("closed", "done")):
            oc.post(f"/admin/waitlist/{entry['id']}/status", data={"status": status})
            s.check(f"marked {status}, the task is {want}", _task(task["id"])["status"] == want,
                    detail=_task(task["id"])["status"])
        oc.post(f"/admin/waitlist/{entry['id']}/status", data={"status": "open"})
        r = oc.post(f"/admin/waitlist/{entry['id']}/status", data={"status": "spam"},
                    follow_redirects=True)
        s.check("marked spam, the task is gone", _task(task["id"]) is None)
        s.check("and the owner is told so", "spam" in " ".join(flashes(r)).lower())
        s.check("a status that is not one is refused",
                oc.post(f"/admin/waitlist/{entry['id']}/status", data={"status": "bin"}).status_code == 400)

        s.section("The sender is set aside next time, whatever they write")
        r, again, again_task, letters = _send(anon, "guest", "Just checking you got my note!")
        s.check("kept as Spam", again is not None and bool(again["spam_at"]))
        s.check("with no task and no letter", again_task is None and not letters)

        s.section("The waitlist keeps Spam out of sight unless asked")
        current = oc.get("/admin/waitlist").get_data(as_text=True)
        s.check("the default view does not show it", f'id="entry-{spam["id"]}"' not in current)
        spammed = oc.get("/admin/waitlist?when=Spam").get_data(as_text=True)
        s.check("and the Spam chip does", f'id="entry-{spam["id"]}"' in spammed)

        s.section("Enquiries made before this")
        conn = db()
        now = _harness.datetime_now()
        old = conn.execute(
            """INSERT INTO waitlist_entries (name, email, phone, party_size, notes, status, created_at)
               VALUES (?, ?, '', 2, ?, 'open', ?)""",
            ("Anshika " + TAG, f"{TAG.lower()}.old@example.invalid", "length not decided. " + PITCH,
             now)).lastrowid
        old_task = conn.execute(
            """INSERT INTO tasks (title, notes, priority, due_date, status, origin, created_at)
               VALUES (?, '', 'normal', ?, 'open', 'enquiry', ?)""",
            (f"Answer an enquiry from Anshika {TAG} ({TAG.lower()}.old@example.invalid)",
             m.house_today_iso(), now)).lastrowid
        real = conn.execute(
            """INSERT INTO waitlist_entries (name, email, phone, party_size, notes, status, created_at)
               VALUES (?, ?, '', 2, 'June, a week', 'open', ?)""",
            ("Real " + TAG, f"{TAG.lower()}.real@example.invalid", now)).lastrowid
        real_task = conn.execute(
            """INSERT INTO tasks (title, notes, priority, due_date, status, origin, created_at)
               VALUES (?, '', 'normal', ?, 'open', 'enquiry', ?)""",
            (f"Answer an enquiry from Real {TAG} ({TAG.lower()}.real@example.invalid)",
             m.house_today_iso(), now)).lastrowid
        conn.commit()
        m.tidy_contact_enquiries(conn)
        s.check("a real one is tied to its task, and stays open",
                tuple(conn.execute("SELECT waitlist_entry_id, status FROM tasks WHERE id = ?",
                             (real_task,)).fetchone()) == (real, "open"))
        s.check("a pitch is set aside and its task taken off the day",
                bool(conn.execute("SELECT spam_at FROM waitlist_entries WHERE id = ?", (old,)).fetchone()["spam_at"])
                and conn.execute("SELECT 1 FROM tasks WHERE id = ?", (old_task,)).fetchone() is None)
        s.check("and a second run changes nothing", m.tidy_contact_enquiries(conn) == (0, 0))
        conn.close()

        s.section("The words, both ways")
        for text in (PITCH, "We can get you on the first page of Google",
                     "Backlinks and guest posts for your site"):
            s.check(f"a pitch: {text[:40]}", bool(m.ENQUIRY_PITCH.search(text)))
        for text in ("Two of us in September, flexible on dates.",
                     "Is the château suitable for a wedding of sixty?",
                     "Our anniversary - can we book dinner too? We love the old towns."):
            s.check(f"a guest: {text[:40]}", not m.ENQUIRY_PITCH.search(text))
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
