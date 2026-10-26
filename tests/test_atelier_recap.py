"""The recap after an atelier: drafted by the app, sent only by the owner.

The ateliers are mostly how people come to know the house, so after one ends
its guests get a letter: the week's photographs, what whoever taught it left
for them, and the next dates (owner, 22 October 2026, "9 yes"). The same day
the guest texts were switched off for carrying sentences the app had made up,
so this letter was built the other way round.

What this holds:

  - A DRAFT, NEVER A SEND. The sitting's page assembles it once the atelier is
    over, and nothing sends it but the owner's button there: not opening the
    page, not the reminder, not the job that raises tasks. And the code agrees
    -- the one function that sends it is called from that one route.
  - NO WORDING, NO LETTER. It ships with none, the editor marks it "Not
    written yet", and the button is not drawn and the route refuses until the
    owner has written a subject and a body.
  - NOBODY'S FACE WITHOUT THEIR YES. A photograph with somebody in it who
    would rather not appear is left out of the letter, so is one nobody has
    said who is in, and the picture's link stops answering if a yes is
    withdrawn after the letter went.
  - ONCE, AND NEVER TO ANYBODY CANCELLED: a second press sends nothing, a
    place cancelled after the page was drawn is not written to, and nobody
    cancelled is on the list at all.
  - WHAT WHOEVER TAUGHT IT WROTE goes in, from their own teaching page.
  - THE RECORD: the sitting, the registration and the guest's own page each
    say the recap went, and when.

send_email is stood in for throughout: nothing reaches a mail provider.
"""
import ast
import io
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db, flashes, keep_rows, visible_text
import _harness

m = _harness.m
TAG = "ZRECAP"
DOMAIN = "zrecap.example"
NOTES = "ZRECAP brioche: 500g flour, 6 eggs, 250g butter. Rest it overnight."
SUBJECT = "{workshop_title} — the week"
BODY = ("Hi {guest_name},\n\nThe photographs:\n\n{photos}\n\n"
        "From {instructor_name}:\n\n{instructor_notes}\n\n"
        "The recipes:\n{notes_file_url}\n\n{next_dates}\n\n— Château de Gudanes")


def _jpeg(path, shade):
    from PIL import Image
    Image.new("RGB", (64, 48), (shade, 90, 120)).save(path, "JPEG")


def _cleanup(conn):
    ids = f"(SELECT id FROM workshop_bookings WHERE guest_email LIKE '%@{DOMAIN}')"
    conn.execute(f"DELETE FROM workshop_messages WHERE workshop_booking_id IN {ids}")
    conn.execute(f"DELETE FROM workshop_feedback WHERE workshop_booking_id IN {ids}")
    conn.execute(f"DELETE FROM atelier_recap_photo_people WHERE workshop_booking_id IN {ids}")
    sessions = f"(SELECT id FROM workshop_sessions WHERE notes = '{TAG}')"
    conn.execute(f"DELETE FROM atelier_recap_photos WHERE session_id IN {sessions}")
    conn.execute(f"DELETE FROM atelier_recaps WHERE session_id IN {sessions}")
    conn.execute(f"DELETE FROM workshop_bookings WHERE guest_email LIKE '%@{DOMAIN}'")
    conn.execute(f"DELETE FROM workshop_sessions WHERE notes = '{TAG}'")
    conn.execute(f"DELETE FROM workshops WHERE title LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM photo_inbox WHERE original_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM media_intake WHERE original_name LIKE '{TAG}%'")
    guests = f"(SELECT id FROM guests WHERE email LIKE '%@{DOMAIN}')"
    conn.execute(f"DELETE FROM consent_events WHERE guest_id IN {guests} "
                 f"OR email LIKE '%@{DOMAIN}'")
    conn.execute(f"DELETE FROM guest_profile_changes WHERE guest_id IN {guests}")
    conn.execute(f"DELETE FROM guests WHERE email LIKE '%@{DOMAIN}'")
    conn.execute(f"DELETE FROM tasks WHERE title LIKE '%{TAG}%'")
    conn.commit()


def _setup(conn):
    """A sitting that ended three days ago, a later one on sale, five places
    and five photographs of the week."""
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = m.house_today()
    cur = conn.execute(
        "INSERT INTO workshops (title, description, instructor_name, price_per_person, "
        "default_capacity, active, created_at) VALUES (?, '', ?, 900, 10, 1, ?)",
        (f"{TAG} Bread and the Oven", "Hélène Marty", now))
    workshop_id = cur.lastrowid
    first, last = today - timedelta(days=5), today - timedelta(days=3)
    session_id = conn.execute(
        "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, "
        "instructor_token, created_at) VALUES (?, ?, ?, 10, ?, ?, ?)",
        (workshop_id, first.isoformat(), last.isoformat(), TAG,
         "zrecap-teach-" + m.secrets.token_hex(6), now)).lastrowid
    later_id = conn.execute(
        "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, "
        "created_at) VALUES (?, ?, ?, 10, ?, ?)",
        (workshop_id, (today + timedelta(days=40)).isoformat(),
         (today + timedelta(days=43)).isoformat(), TAG, now)).lastrowid

    places = {}
    for key, status, extra in (("ann", "confirmed", {}), ("bob", "confirmed", {}),
                               ("cat", "cancelled", {}), ("dee", "confirmed",
                                                          {"do_not_email": 1})):
        places[key] = conn.execute(
            "INSERT INTO workshop_bookings (session_id, reference_code, manage_token, "
            "guest_name, guest_email, party_size, status, do_not_email, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (session_id, f"{TAG}-{key.upper()}", "zrecap-" + m.secrets.token_hex(8),
             f"{key.title()} {TAG}", f"{key}@{DOMAIN}", status,
             extra.get("do_not_email", 0), now)).lastrowid
    # Ann is happy to appear; Bob would rather not; Cat (cancelled) said yes.
    for key, answer in (("ann", "yes"), ("bob", "no"), ("cat", "yes")):
        conn.execute("INSERT INTO guests (name, email, photo_consent, photo_consent_at, "
                     "created_at) VALUES (?, ?, ?, ?, ?)",
                     (f"{key.title()} {TAG}", f"{key}@{DOMAIN}", answer, now, now))

    photos = {}
    for i, name in enumerate(("ann_alone", "ann_and_bob", "the_oven", "undecided",
                              "a_stranger")):
        filename = f"photo_{TAG.lower()}_{name}.jpg"
        _jpeg(os.path.join(m.UPLOAD_DIR, filename), 40 * i)
        photos[name] = conn.execute(
            "INSERT INTO photo_inbox (filename, original_name, sha256, taken_on, "
            "arrived_at, source) VALUES (?, ?, ?, ?, ?, 'camera')",
            (filename, f"{TAG}-{name}", m.secrets.token_hex(32),
             (first + timedelta(days=i % 3)).isoformat(), now)).lastrowid
    # One off the camera roll, which keeps its frames beside the room photos,
    # shot at ten in the morning of the second day, house time.
    roll = f"roll_{TAG.lower()}_frame.jpg"
    _jpeg(os.path.join(m.ROOM_PHOTO_DIR, roll), 120)
    shot = m.datetime(first.year, first.month, first.day, 10, tzinfo=m.LOCAL_TZ) \
        + timedelta(days=1)
    photos["camera"] = conn.execute(
        "INSERT INTO media_intake (sha256, filename, original_name, kind, taken_at, "
        "status, has_people, created_at) VALUES (?, ?, ?, 'photo', ?, 'held', 1, ?)",
        (m.secrets.token_hex(32), roll, f"{TAG}-frame",
         shot.astimezone(m.timezone.utc).isoformat(), now)).lastrowid
    # One from the week before: not this sitting's.
    stray = f"photo_{TAG.lower()}_last_week.jpg"
    _jpeg(os.path.join(m.UPLOAD_DIR, stray), 200)
    conn.execute(
        "INSERT INTO photo_inbox (filename, original_name, sha256, taken_on, arrived_at, "
        "source) VALUES (?, ?, ?, ?, ?, 'camera')",
        (stray, f"{TAG}-last-week", m.secrets.token_hex(32),
         (first - timedelta(days=7)).isoformat(), now))
    conn.commit()
    return session_id, later_id, places, photos


class Mail:
    """send_email, stood in for: every call kept, nothing sent."""

    def __init__(self):
        self.calls = []
        # Addresses whose letter is kept rather than sent, as the guest-mail
        # switch keeps one: False back, and nothing in `report` to say it
        # was refused for good.
        self.hold = set()

    def __call__(self, to, subject, body, *args, **kwargs):
        self.calls.append({"to": to, "subject": subject, "body": body,
                           "html": kwargs.get("html") or "",
                           "key": kwargs.get("template_key"), "about": kwargs.get("about")})
        return to.lower() not in self.hold

    def recaps(self):
        return [c for c in self.calls if c["key"] == m.RECAP_TEMPLATE_KEY]


def _set(conn, key, value):
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def _recap_titles(conn, today):
    found, _dropped = m.watch_task_findings(conn, today)
    return [t for kind, t, _n, _d, _p in found if kind == "recap" and TAG in t]


def _callers(name):
    """The app.py functions that call `name`, read off the source."""
    with io.open(m.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == name):
                    out.add(fn.name)
    return out


def run():
    s = Suite("The atelier recap")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    put_back_wording = keep_rows(conn, "email_templates", "template_key = ?",
                                 (m.RECAP_TEMPLATE_KEY,))
    put_back_switch = keep_rows(conn, "app_settings", "key IN (?, ?)",
                                (m.RECAP_REMINDER_SWITCH, "automation_watch_tasks_enabled"))
    mail = Mail()
    real = (m.send_email, m.PHOTO_DERIVED_DIR, m.ROOM_PHOTO_DIR)
    m.send_email = mail
    # The sizes made for the letter's pictures, and the camera roll's frame,
    # go with the run's uploads rather than into the house's own folders.
    m.PHOTO_DERIVED_DIR = os.path.join(m.UPLOAD_DIR, "zrecap_sizes")
    m.ROOM_PHOTO_DIR = os.path.join(m.UPLOAD_DIR, "zrecap_roll")
    os.makedirs(m.ROOM_PHOTO_DIR, exist_ok=True)
    try:
        _run(s, oc, ec, conn, mail)
    finally:
        m.send_email, m.PHOTO_DERIVED_DIR, m.ROOM_PHOTO_DIR = real
        put_back_wording()
        put_back_switch()
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, ec, conn, mail):
    today = m.house_today()
    session_id, later_id, places, photos = _setup(conn)
    # Each photograph by where it came in and its row there, as the page names it.
    key = {name: ("camera" if name == "camera" else "tray") + f":{pid}"
           for name, pid in photos.items()}
    page_url = f"/admin/workshops/sessions/{session_id}"
    conn.execute("UPDATE email_templates SET subject = '', body = '' WHERE template_key = ?",
                 (m.RECAP_TEMPLATE_KEY,))
    conn.commit()

    s.section("It ships with no wording, and says so")
    shipped = next((d for d in m.DEFAULT_EMAIL_TEMPLATES if d[0] == m.RECAP_TEMPLATE_KEY), None)
    s.check("the recap is a stored letter the owner edits", shipped is not None)
    s.check("and it ships with no subject and no body",
            shipped is not None and not shipped[2].strip() and not shipped[3].strip(),
            detail=str(shipped)[:200])
    row = conn.execute("SELECT * FROM email_templates WHERE template_key = ?",
                       (m.RECAP_TEMPLATE_KEY,)).fetchone()
    s.check("the editor marks it Not written yet",
            row is not None and m.template_state(row, {}) == "Not written yet")
    editor = oc.get("/management/email-templates")
    s.check("and lists it", m.EMAIL_TEMPLATE_LABELS[m.RECAP_TEMPLATE_KEY]
            in visible_text(editor.get_data(as_text=True)), editor)
    r = oc.post(f"/management/email-templates/{m.RECAP_TEMPLATE_KEY}/restore",
                follow_redirects=True)
    s.check("Restore original wording refuses, since there is none to restore",
            any("no wording" in f for f in flashes(r)), r, detail=str(flashes(r)))

    s.section("A draft is assembled, and nothing is sent unapproved")
    page = oc.get(page_url)
    html = page.get_data(as_text=True)
    s.check("the sitting's page opens", page.status_code == 200, page)
    s.check("with the recap drafted on it", 'id="recap"' in html and "The recap" in html)
    draft = m.recap_draft(conn, session_id)
    found = {p["key"]: p for p in draft["photos"]}
    s.check("the week's photographs are in the draft, from the tray and the camera roll",
            set(key.values()) <= set(found), detail=f"{sorted(found)}")
    s.check("with what the camera roll thought of its frame, which decides nothing",
            found.get(key["camera"], {}).get("saw_people") is True
            and not found.get(key["camera"], {}).get("goes", True))
    s.check("and the one from the week before is not",
            not any("last_week" in p["filename"] for p in draft["photos"]))
    s.check("the next date on sale is in it",
            [d["url"].endswith(f"/workshops/register/{later_id}") for d in draft["next_dates"]]
            == [True], detail=str(draft["next_dates"]))
    _set(conn, m.RECAP_REMINDER_SWITCH, "1")
    _set(conn, "automation_watch_tasks_enabled", "1")
    m.generate_watch_tasks(conn)
    s.check("no letter went from opening the page, or from the reminder",
            not mail.recaps(), detail=str(mail.recaps())[:200])
    stamped = conn.execute(
        "SELECT COUNT(*) AS n FROM workshop_bookings WHERE session_id = ? "
        "AND recap_sent_at IS NOT NULL", (session_id,)).fetchone()["n"]
    s.check("and nobody is marked as having had it", stamped == 0)
    s.check("the one function that sends it is called from the owner's button alone",
            _callers("send_recap") == {"send_atelier_recap"},
            detail=str(_callers("send_recap")))
    s.check("and the one-guest send only from there",
            _callers("send_recap_one") == {"send_recap"},
            detail=str(_callers("send_recap_one")))

    s.section("The reminder is a task, and only while its switch is on")
    _set(conn, m.RECAP_REMINDER_SWITCH, "0")
    s.check("off by default", m.AUTOMATION_SETTING_DEFAULTS[m.RECAP_REMINDER_SWITCH] == "0")
    s.check("off, it raises nothing", not _recap_titles(conn, today))
    _set(conn, m.RECAP_REMINDER_SWITCH, "1")
    titles = _recap_titles(conn, today)
    s.check("on, it raises one for the drafted recap", len(titles) == 1, detail=str(titles))
    m.generate_watch_tasks(conn)
    task = conn.execute("SELECT * FROM tasks WHERE origin = 'watch' AND status != 'done' "
                        "AND title LIKE ?", (f"%{TAG}%",)).fetchone()
    s.check("which is on the task list", task is not None)
    s.check("saying it has no wording yet",
            task is not None and "no wording yet" in (task["notes"] or ""))

    s.section("It cannot be sent before the owner has written the wording")
    s.check("no wording, nothing to send", m.recap_wording(conn) is None)
    s.check("the page draws no send button",
            f"/admin/workshops/sessions/{session_id}/recap/send" not in html)
    s.check("and says why", "no wording yet" in visible_text(html))
    r = oc.post(f"{page_url}/recap/send", data={"expect": "2"}, follow_redirects=True)
    s.check("the send refuses", any("no wording yet" in f for f in flashes(r)), r,
            detail=str(flashes(r)))
    s.check("and nothing went", not mail.recaps())
    conn.execute("UPDATE email_templates SET subject = ?, body = '' WHERE template_key = ?",
                 (SUBJECT, m.RECAP_TEMPLATE_KEY))
    conn.commit()
    s.check("a subject alone is not wording either", m.recap_wording(conn) is None)
    r = oc.post(f"/management/email-templates/{m.RECAP_TEMPLATE_KEY}/edit",
                data={"subject": SUBJECT, "body": BODY}, follow_redirects=True)
    s.check("the owner writes it in the editor",
            m.recap_wording(conn) == (SUBJECT, BODY), r, detail=str(flashes(r)))
    row = conn.execute("SELECT * FROM email_templates WHERE template_key = ?",
                       (m.RECAP_TEMPLATE_KEY,)).fetchone()
    s.check("and the editor stops calling it unwritten",
            m.template_state(row, {}) != "Not written yet")

    s.section("What whoever taught it wrote goes in, from their own page")
    token = conn.execute("SELECT instructor_token FROM workshop_sessions WHERE id = ?",
                         (session_id,)).fetchone()["instructor_token"]
    teacher = m.app.test_client()
    sheet = teacher.get(f"/workshops/teaching/{token}")
    s.check("their teaching page has a box for notes",
            sheet.status_code == 200 and 'name="notes"' in sheet.get_data(as_text=True), sheet)
    r = teacher.post(f"/workshops/teaching/{token}/notes",
                     data={"notes": NOTES,
                           "notes_file": (io.BytesIO(b"Brioche, the long way."), "brioche.txt")},
                     content_type="multipart/form-data", follow_redirects=True)
    s.check("they save notes and a file without signing in", r.status_code == 200, r)
    recap = m.recap_row(conn, session_id)
    s.check("the notes are kept against the sitting",
            recap is not None and recap["notes"] == NOTES, detail=str(dict(recap or {}))[:200])
    s.check("and the file", recap is not None and recap["notes_file_name"] == "brioche.txt")
    s.check("a wrong link saves nothing",
            teacher.post("/workshops/teaching/not-a-link/notes",
                         data={"notes": "x"}).status_code == 404)
    r = oc.post(f"{page_url}/recap/notes", data={"notes": NOTES + " Bake at 180."},
                content_type="multipart/form-data", follow_redirects=True)
    recap = m.recap_row(conn, session_id)
    s.check("the owner can put the notes right, the file kept",
            recap["notes"].endswith("Bake at 180.") and recap["notes_file"], r)

    s.section("A photograph goes only when everybody in it is happy to appear")
    form = {"photo": list(key.values())}
    for name in key:
        form[f"chosen-{key[name]}"] = "1"
    form[f"people-{key['ann_alone']}"] = "named"
    form[f"who-{key['ann_alone']}"] = [str(places["ann"])]
    form[f"people-{key['ann_and_bob']}"] = "named"
    form[f"who-{key['ann_and_bob']}"] = [str(places["ann"]), str(places["bob"])]
    form[f"people-{key['the_oven']}"] = "nobody"
    form[f"people-{key['camera']}"] = "nobody"
    form[f"people-{key['a_stranger']}"] = "other"
    # A tick for somebody cancelled is not taken.
    form[f"who-{key['a_stranger']}"] = [str(places["cat"])]
    r = oc.post(f"{page_url}/recap/photos", data=form, follow_redirects=True)
    s.check("the choices save", r.status_code == 200, r, detail=str(flashes(r)))
    draft = m.recap_draft(conn, session_id)
    verdict = {name: next(p for p in draft["photos"] if p["key"] == key[name])
               for name in key}
    s.check("a photograph of somebody happy to appear goes",
            verdict["ann_alone"]["goes"])
    s.check("one with somebody who would rather not appear is left out",
            not verdict["ann_and_bob"]["goes"]
            and "would rather not appear" in (verdict["ann_and_bob"]["why"] or ""),
            detail=str(verdict["ann_and_bob"]["why"]))
    s.check("one with nobody in it goes", verdict["the_oven"]["goes"])
    s.check("one nobody has said who is in is left out",
            not verdict["undecided"]["goes"]
            and "nobody has said" in verdict["undecided"]["why"])
    s.check("one with somebody not on the sitting is left out",
            not verdict["a_stranger"]["goes"])
    s.check("the letter carries the three that may go, and no other",
            {p["key"] for p in draft["in_letter"]}
            == {key["ann_alone"], key["the_oven"], key["camera"]},
            detail=str([p["key"] for p in draft["in_letter"]]))

    s.section("Who it goes to: never anybody cancelled")
    going = {p["id"] for p in draft["going"]}
    s.check("the confirmed places that can be written to",
            going == {places["ann"], places["bob"]}, detail=str(going))
    s.check("the cancelled one is not even on the list",
            all(p["id"] != places["cat"] for p in draft["participants"]))
    s.check("and the one who asked not to be emailed is named, left out",
            any(label.startswith("Dee") and why == m.GROUP_DO_NOT_EMAIL
                for label, why in draft["left_out"]), detail=str(draft["left_out"]))

    page = oc.get(page_url)
    html = page.get_data(as_text=True)
    s.check("the page shows the letter before it goes",
            'class="recap-preview"' in html and "Bake at 180." in html, page)
    s.check("with a button to send it to the two",
            f"/admin/workshops/sessions/{session_id}/recap/send" in html
            and "Send the recap to 2 guests" in html)

    s.section("A place cancelled after the page was drawn is not written to")
    stale = m.recap_draft(conn, session_id)
    bob = next(p for p in stale["going"] if p["id"] == places["bob"])
    conn.execute("UPDATE workshop_bookings SET status = 'cancelled' WHERE id = ?",
                 (places["bob"],))
    conn.commit()
    outcome, why = m.send_recap_one(conn, stale, bob, m.recap_letter_parts(stale))
    s.check("the one-guest send reads the place again, and refuses",
            outcome is None and "no longer has a place" in (why or ""), detail=str(why))
    s.check("and nothing went", not mail.recaps())
    conn.execute("UPDATE workshop_bookings SET status = 'confirmed' WHERE id = ?",
                 (places["bob"],))
    conn.commit()

    s.section("The owner sends it, once")
    r = oc.post(f"{page_url}/recap/send", data={"expect": "3"}, follow_redirects=True)
    s.check("a count that no longer matches is refused",
            any("list has changed" in f for f in flashes(r)) and not mail.recaps(), r)
    r = oc.post(f"{page_url}/recap/send", data={"expect": "2"}, follow_redirects=True)
    letters = mail.recaps()
    to = sorted(c["to"] for c in letters)
    s.check("it goes to the two, one letter each",
            to == [f"ann@{DOMAIN}", f"bob@{DOMAIN}"], r, detail=str(to))
    s.check("never to the cancelled place", f"cat@{DOMAIN}" not in to)
    s.check("and the one left out is named on the page",
            any("Dee" in f and "asked not to be emailed" in f for f in flashes(r)),
            detail=str(flashes(r)))
    ann = next((c for c in letters if c["to"] == f"ann@{DOMAIN}"), {"html": "", "body": ""})
    token = m.recap_row(conn, session_id)["share_token"]
    goes = {p["key"]: p["decision_id"] for p in m.recap_draft(conn, session_id)["photos"]}
    link = lambda name: f"/workshops/recap/{token}/photo/{goes[key[name]]}"
    s.check("the letter carries the cleared photographs, drawn",
            link("ann_alone") in ann["html"] and link("the_oven") in ann["html"],
            detail=ann["html"][:300])
    s.check("and not the one of somebody who would rather not appear",
            link("ann_and_bob") not in ann["html"] and link("ann_and_bob") not in ann["body"])
    s.check("the instructor's notes are in it", "Bake at 180." in ann["body"]
            and "Bake at 180." in ann["html"])
    s.check("with the file's link", f"/workshops/recap/{token}/notes" in ann["body"])
    s.check("and the next date, with its link",
            f"/workshops/register/{later_id}" in ann["body"]
            and f"/workshops/register/{later_id}" in ann["html"])
    s.check("in the owner's words", ann["subject"] == f"{TAG} Bread and the Oven — the week",
            detail=ann["subject"])
    s.check("each filed against its place",
            all(c["about"] == ("workshop", places[c["to"].split("@")[0]]) for c in letters))

    before = len(mail.recaps())
    # The count a second tab, drawn before the send, would post.
    r = oc.post(f"{page_url}/recap/send", data={"expect": "2"}, follow_redirects=True)
    s.check("pressed again from a page drawn before, it sends nothing",
            len(mail.recaps()) == before, r, detail=str(flashes(r)))
    draft = m.recap_draft(conn, session_id)
    ann_member = next(p for p in draft["participants"] if p["id"] == places["ann"])
    outcome, why = m.send_recap_one(conn, draft, dict(ann_member, also=[]),
                                    m.recap_letter_parts(draft))
    s.check("and the one-guest send refuses somebody who has it",
            outcome is None and why.startswith(m.RECAP_ALREADY)
            and len(mail.recaps()) == before, detail=str(why))

    s.section("The record says it went, and when")
    recap = m.recap_row(conn, session_id)
    s.check("on the sitting", recap["sent_at"] is not None and recap["sent_by_user_id"])
    stamps = {r_["id"]: r_["recap_sent_at"] for r_ in conn.execute(
        "SELECT id, recap_sent_at FROM workshop_bookings WHERE session_id = ?", (session_id,))}
    s.check("on each place it went to, and on no other",
            bool(stamps[places["ann"]]) and bool(stamps[places["bob"]])
            and not stamps[places["cat"]] and not stamps[places["dee"]], detail=str(stamps))
    logged = {r_["workshop_booking_id"]: r_["status"] for r_ in conn.execute(
        "SELECT workshop_booking_id, status FROM workshop_messages WHERE "
        "workshop_booking_id IN (?, ?, ?)", (places["ann"], places["dee"], places["cat"]))}
    s.check("in each guest's sent messages, the refusal included",
            logged.get(places["ann"]) == "sent"
            and (logged.get(places["dee"]) or "").startswith("skipped")
            and places["cat"] not in logged, detail=str(logged))
    html = oc.get(page_url).get_data(as_text=True)
    s.check("the sitting's page says it was sent", "Sent " in visible_text(html)
            and "2 of 3 on this sitting have it" in visible_text(html),
            detail=visible_text(html)[visible_text(html).find("The recap"):][:300])
    reg = oc.get(f"/admin/workshops/registrations?session_id={session_id}")
    s.check("the registration says when", "The recap went" in reg.get_data(as_text=True), reg)
    profile = conn.execute("SELECT id FROM guests WHERE email = ?",
                           (f"ann@{DOMAIN}",)).fetchone()["id"]
    gp = oc.get(f"/guests/{profile}")
    s.check("and so does the guest's own page", "recap sent" in gp.get_data(as_text=True), gp)
    s.check("the reminder closes itself once it has gone", not _recap_titles(conn, today))
    m.generate_watch_tasks(conn)
    s.check("and its task is ticked off",
            not conn.execute("SELECT 1 FROM tasks WHERE origin = 'watch' AND status != 'done' "
                             "AND title LIKE ?", (f"%{TAG}%",)).fetchone())

    s.section("The links in the letter, and a yes withdrawn")
    guest = m.app.test_client()
    r = guest.get(link("ann_alone"))
    s.check("a cleared photograph opens from the letter", r.status_code == 200
            and r.headers.get("Content-Type", "").startswith("image/"), r)
    s.check("and is not for search engines",
            "noindex" in (r.headers.get("X-Robots-Tag") or ""))
    s.check("the one left out does not",
            guest.get(link("ann_and_bob")).status_code == 404)
    s.check("nor does anything with a wrong link",
            guest.get(f"/workshops/recap/not-it/photo/{goes[key['ann_alone']]}")
            .status_code == 404)
    r = guest.get(f"/workshops/recap/{token}/notes")
    s.check("the file opens", r.status_code == 200 and b"Brioche" in r.data, r)
    conn.execute("UPDATE guests SET photo_consent = 'no' WHERE email = ?", (f"ann@{DOMAIN}",))
    conn.commit()
    s.check("Ann says no after all, and her picture is out of the letters already sent",
            guest.get(link("ann_alone")).status_code == 404)
    s.check("the one with nobody in it is still there",
            guest.get(link("the_oven")).status_code == 200)
    s.check("and so is the camera roll's, from beside the room photographs",
            guest.get(link("camera")).status_code == 200)
    # What the privacy notice says about this letter, held to the code.
    notice = visible_text(guest.get("/privacy").get_data(as_text=True))
    s.check("the privacy notice says a photograph needs a yes, and not being asked is not one",
            "only if you have told us you are happy to appear" in notice
            and "not having been asked is not a yes" in notice)
    dee = next(p for p in m.recap_participants(conn, session_id) if p["id"] == places["dee"])
    goes_, why_ = m.recap_photo_verdict({"chosen": True, "people": "named", "who": [dee],
                                         "gone": []})
    s.check("and it is true: somebody never asked keeps a photograph out",
            not goes_ and "has not been asked" in (why_ or ""), detail=str(why_))

    s.section("Answers written down on the recap reach the guest's profile")
    r = oc.post(f"{page_url}/recap/answers",
                data={f"answer-{places['bob']}": "yes", f"answer-{places['ann']}": "no"},
                follow_redirects=True)
    bob_now = conn.execute("SELECT photo_consent FROM guests WHERE email = ?",
                           (f"bob@{DOMAIN}",)).fetchone()["photo_consent"]
    s.check("Bob's yes is on his profile", bob_now == "yes", r, detail=str(flashes(r)))
    s.check("with how he was asked", conn.execute(
        "SELECT 1 FROM consent_events WHERE channel = 'photos' AND email = ? "
        "AND how LIKE '%atelier%'", (f"bob@{DOMAIN}",)).fetchone() is not None)

    s.section("Once it has gone, the notes are shut")
    sheet = teacher.get(f"/workshops/teaching/{token_for(conn, session_id)}")
    s.check("the teaching page says it went, with no box",
            "went to the guests" in sheet.get_data(as_text=True)
            and 'name="notes"' not in sheet.get_data(as_text=True), sheet)
    r = teacher.post(f"/workshops/teaching/{token_for(conn, session_id)}/notes",
                     data={"notes": "changed"}, follow_redirects=True)
    s.check("and a late change is refused",
            m.recap_row(conn, session_id)["notes"].endswith("Bake at 180."), r)

    s.section("Setting one aside")
    other = conn.execute(
        "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, "
        "created_at) SELECT workshop_id, ?, ?, 10, ?, created_at FROM workshop_sessions "
        "WHERE id = ?", ((today - timedelta(days=12)).isoformat(),
                         (today - timedelta(days=10)).isoformat(), TAG, session_id)).lastrowid
    conn.execute(
        "INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name, "
        "guest_email, party_size, status, created_at) VALUES (?, ?, ?, ?, ?, 1, 'confirmed', ?)",
        (other, f"{TAG}-EVE", "zrecap-" + m.secrets.token_hex(8), f"Eve {TAG}",
         f"eve@{DOMAIN}", m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    s.check("another sitting's recap is waiting", len(_recap_titles(conn, today)) == 1)
    oc.post(f"/admin/workshops/sessions/{other}/recap/set-aside", follow_redirects=True)
    s.check("set aside, its reminder goes", not _recap_titles(conn, today))
    r = oc.post(f"/admin/workshops/sessions/{other}/recap/send", data={"expect": "1"},
                follow_redirects=True)
    s.check("and it cannot be sent", any("set this recap aside" in f for f in flashes(r))
            and all(c["to"] != f"eve@{DOMAIN}" for c in mail.recaps()), r)
    oc.post(f"/admin/workshops/sessions/{other}/recap/set-aside",
            data={"bring_back": "1"}, follow_redirects=True)
    s.check("brought back, it is waiting again", len(_recap_titles(conn, today)) == 1)

    s.section("Two places from one address, a letter kept, and only the owner")
    conn.execute(
        "INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name, "
        "guest_email, party_size, status, created_at) VALUES (?, ?, ?, ?, ?, 1, 'confirmed', ?)",
        (other, f"{TAG}-EVAN", "zrecap-" + m.secrets.token_hex(8), f"Evan {TAG}",
         f"EVE@{DOMAIN}", m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    r = ec.post(f"/admin/workshops/sessions/{other}/recap/send", data={"expect": "1"})
    s.check("somebody who is not the owner cannot send it",
            r.status_code != 200 and all(c["to"].lower() != f"eve@{DOMAIN}"
                                         for c in mail.recaps()), r)
    mail.hold = {f"eve@{DOMAIN}"}
    r = oc.post(f"/admin/workshops/sessions/{other}/recap/send", data={"expect": "1"},
                follow_redirects=True)
    eve = [c for c in mail.recaps() if c["to"].lower() == f"eve@{DOMAIN}"]
    s.check("two places booked from one inbox get one letter", len(eve) == 1,
            r, detail=f"{len(eve)}; {flashes(r)}")
    both = [r_["recap_sent_at"] for r_ in conn.execute(
        "SELECT recap_sent_at FROM workshop_bookings WHERE session_id = ?", (other,))]
    s.check("and both are marked as having it, so neither is written to again",
            len(both) == 2 and all(both), detail=str(both))
    s.check("a letter the guest-mail switch keeps is said to be kept, not sent",
            any("kept" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("and its line on their record says held",
            conn.execute("SELECT status FROM workshop_messages WHERE workshop_booking_id IN "
                         "(SELECT id FROM workshop_bookings WHERE session_id = ?) "
                         "ORDER BY id DESC", (other,)).fetchone()["status"] == "held")
    mail.hold = set()
    r = oc.post(f"/admin/workshops/sessions/{other}/recap/send", data={"expect": "1"},
                follow_redirects=True)
    s.check("pressed again, nothing more goes to that inbox",
            len([c for c in mail.recaps() if c["to"].lower() == f"eve@{DOMAIN}"]) == 1, r)


def token_for(conn, session_id):
    return conn.execute("SELECT instructor_token FROM workshop_sessions WHERE id = ?",
                        (session_id,)).fetchone()["instructor_token"]


if __name__ == "__main__":
    print(run().report())
