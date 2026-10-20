"""Scripts are kept off the public booking forms; people are not.

CSRF and an hourly limit per address were all the public forms had, and
neither stops a script that fetches the page, fills every field with random
letters and posts it once. That is how a confirmed registration made of
random letters came to hold two places on an atelier (18 October 2026), and
spam was arriving through the Contact page's enquiry form.

Each guarded form now carries two fields (bot_guard): one nobody can see,
which a person leaves empty, and the moment the form was drawn, signed, since
a person takes longer than PUBLIC_FORM_MIN_SECONDS to fill in a booking form.
A post that fails either is refused quietly: nothing written, nobody told,
the page back as after any mistake.

Checked here: every guarded form carries the fields (a handover that redraws
a page must not drop them), each kind of refusal writes nothing on every
guarded form, and a person -- a stamp old enough, or a page drawn before the
check existed -- goes straight through.
"""
import time
from datetime import timedelta

from _harness import Suite, db, flashes, house_today, WORKSHOP_DETAILS
import _harness

m = _harness.m
TAG = "ZZBOT"
_ip = [0]


def _client():
    _ip[0] += 1
    return m.app.test_client(), {"REMOTE_ADDR": f"203.0.113.{_ip[0]}"}


def _stamp(age_seconds):
    ts = str(int(time.time() - age_seconds))
    return f"{ts}.{m._form_stamp_signature(ts)}"


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}",
                     (TAG.lower() + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR notes LIKE ?",
                 (f"%{TAG.lower()}%", f"%{TAG.lower()}%"))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM submission_log WHERE ip_address LIKE '203.0.113.%'")
    conn.commit()
    conn.close()


def _session():
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 800, 8, 1, 98, ?, 30)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=210)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
           created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=2)).isoformat(), f"{TAG} s", now))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} s",)).fetchone()["id"]
    conn.close()
    return sid


def _count(sql, args):
    conn = db()
    n = conn.execute(sql, args).fetchone()[0]
    conn.close()
    return n


def _bot_notes(action):
    return _count("SELECT COUNT(*) FROM submission_log WHERE action = ? "
                  "AND ip_address LIKE '203.0.113.%'", (f"{action}:bot",))


def run():
    s = Suite("Scripts kept off the public forms, people let through")
    # The harness sets the minimum to nothing for every other suite; this one
    # checks it, so it runs on the figure app.py actually ships with.
    import os
    import re
    with open(os.path.abspath(m.__file__), encoding="utf-8") as f:
        shipped = int(re.search(r"^PUBLIC_FORM_MIN_SECONDS = (\d+)", f.read(), re.M).group(1))
    held = m.PUBLIC_FORM_MIN_SECONDS
    m.PUBLIC_FORM_MIN_SECONDS = shipped
    try:
        return _run(s)
    finally:
        m.PUBLIC_FORM_MIN_SECONDS = held
        _cleanup()


def _run(s):
    _cleanup()
    sid = _session()
    room = _harness.ensure_room()

    s.section("The check itself")
    hp, st = m.BOT_HONEYPOT_FIELD, m.BOT_STAMP_FIELD
    s.check("an empty hidden field and an old stamp pass",
            m.bot_guard_refusal({hp: "", st: _stamp(60)}) is None)
    s.check("a filled hidden field is a script", bool(m.bot_guard_refusal({hp: "x", st: _stamp(60)})))
    s.check("a form sent within the minimum is a script",
            bool(m.bot_guard_refusal({hp: "", st: _stamp(0)})))
    s.check("a stamp that was made up is a script",
            bool(m.bot_guard_refusal({hp: "", st: f"{int(time.time()) - 60}.deadbeef"})))
    s.check("and so is one moved back in time with the old signature",
            bool(m.bot_guard_refusal({hp: "", st: str(int(time.time()) - 60) + "."
                                      + _stamp(0).split(".")[1]})))
    s.check("a form drawn before the check existed is let through",
            m.bot_guard_refusal({}) is None)
    s.check("the minimum is a few seconds, not long enough to trouble a person",
            2 <= m.PUBLIC_FORM_MIN_SECONDS <= 10, detail=str(m.PUBLIC_FORM_MIN_SECONDS))

    s.section("Every guarded form carries both fields")
    pages = {
        "the atelier registration": f"/workshops/register/{sid}",
        "a room's booking form": f"/book/{room['id']}",
        "the events enquiry": "/events",
        "the Contact page's rough-dates enquiry": "/contact",
    }
    for label, url in pages.items():
        body = m.app.test_client().get(url).get_data(as_text=True)
        s.check(f"{label} has the hidden field and the stamp",
                f'name="{hp}"' in body and f'name="{st}"' in body,
                detail=f"{body.count(hp)} / {body.count(st)}")
        s.check(f"and hides the field from people on {label}",
                'tabindex="-1"' in body and 'autocomplete="off"' in body)
    # Both Contact forms are macros, drawn on more than one page and only
    # when there is something to offer, so their source is read as well.
    import os
    here = os.path.join(os.path.dirname(os.path.abspath(m.__file__)), "templates")
    for name in ("_flexible.html", "_notify.html"):
        with open(os.path.join(here, name), encoding="utf-8") as f:
            src = f.read()
        s.check(f"{name}, which posts to the Contact page, carries them",
                "{{ bot_guard() }}" in src)

    def _workshop_post(who, guard):
        c, env = _client()
        data = {"guest_name": f"{TAG} {who}", "guest_email": f"{TAG.lower()}.{who}@example.invalid",
                "party_size": "1", "occupancy_type": "double", **WORKSHOP_DETAILS, **guard}
        r = c.post(f"/workshops/register/{sid}", data=data, environ_base=env)
        return r, _count("SELECT COUNT(*) FROM workshop_bookings WHERE guest_email = ?",
                         (data["guest_email"],))

    s.section("The atelier form refuses a script quietly")
    r, n = _workshop_post("honey", {hp: "http://spam.example", st: _stamp(60)})
    s.check("a filled hidden field writes nothing", n == 0)
    s.check("and the page comes back as after any mistake, not as an error",
            r.status_code == 200 and any("did not go through" in f for f in flashes(r)),
            detail=f"{r.status_code} {flashes(r)}")
    s.check("the refusal is noted where the hourly limit keeps its own",
            _bot_notes("register_workshop") >= 1)
    r, n = _workshop_post("quick", {hp: "", st: _stamp(0)})
    s.check("a form sent too fast writes nothing", n == 0)
    r, n = _workshop_post("forged", {hp: "", st: "123.abc"})
    s.check("a made-up stamp writes nothing", n == 0)
    s.check("and nobody was written to",
            _count("SELECT COUNT(*) FROM email_outbox WHERE to_address LIKE ?",
                   (f"{TAG.lower()}.%",)) == 0)

    s.section("A person gets through the atelier form")
    r, n = _workshop_post("person", {hp: "", st: _stamp(30)})
    s.check("filled in at a person's pace, the registration is taken", n == 1,
            detail=f"{r.status_code} {flashes(r)}")
    r, n = _workshop_post("oldpage", {})
    s.check("and from a page drawn before the check, too", n == 1,
            detail=f"{r.status_code} {flashes(r)}")

    s.section("The room booking form refuses a script quietly")
    c, env = _client()
    arrive = house_today() + timedelta(days=300)
    r = c.post(f"/book/{room['id']}", environ_base=env, data={
        "guest_name": f"{TAG} Room", "guest_email": f"{TAG.lower()}.room@example.invalid",
        "party_size": "2", "agree_terms": "on", "arrival_date": arrive.isoformat(),
        "departure_date": (arrive + timedelta(days=2)).isoformat(),
        hp: "x", st: _stamp(60)})
    s.check("nothing is written", _count("SELECT COUNT(*) FROM bookings WHERE guest_email = ?",
                                         (f"{TAG.lower()}.room@example.invalid",)) == 0)
    s.check("the page comes back with the ordinary sentence",
            r.status_code == 200 and any("did not go through" in f for f in flashes(r)),
            detail=f"{r.status_code} {flashes(r)}")
    c, env = _client()
    r = c.post(f"/book/{room['id']}", environ_base=env, data={
        "guest_name": f"{TAG} Room", "guest_email": "not-an-email", "party_size": "2",
        "arrival_date": arrive.isoformat(), hp: "", st: _stamp(30)})
    s.check("a person's post goes on to the form's own checks",
            not any("did not go through" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("The events enquiry refuses a script quietly")
    enquiry = {"event_type": "wedding", "contact_name": f"{TAG} Wed",
               "contact_email": f"{TAG.lower()}.wed@example.invalid", "contact_phone": "",
               "preferred_date": (house_today() + timedelta(days=330)).isoformat(),
               "guest_count": "60", "message": "ZZ"}
    c, env = _client()
    r = c.post("/events/inquire", environ_base=env, data={**enquiry, hp: "", st: _stamp(1)})
    s.check("nothing is written", _count("SELECT COUNT(*) FROM event_inquiries WHERE contact_email = ?",
                                         (enquiry["contact_email"],)) == 0)
    s.check("and the page comes back with the ordinary sentence",
            any("did not go through" in f for f in flashes(r)), detail=str(flashes(r)))
    c, env = _client()
    c.post("/events/inquire", environ_base=env, data={**enquiry, hp: "", st: _stamp(30)})
    s.check("a person's enquiry is taken",
            _count("SELECT COUNT(*) FROM event_inquiries WHERE contact_email = ?",
                   (enquiry["contact_email"],)) == 1)

    s.section("The Contact page's enquiries refuse a script quietly")
    rough = {"action": "flexible", "email": f"{TAG.lower()}.rough@example.invalid",
             "name": f"{TAG} Rough", "rough_month": "", "rough_nights": "0",
             "rough_guests": "2", "flexibility": "any", "message": "SEO for your site"}
    c, env = _client()
    r = c.post("/contact", environ_base=env, data={**rough, hp: "https://seo.example", st: _stamp(60)},
               follow_redirects=True)
    s.check("the rough-dates enquiry writes nothing",
            _count("SELECT COUNT(*) FROM waitlist_entries WHERE email = ?", (rough["email"],)) == 0)
    s.check("and makes no task", _count("SELECT COUNT(*) FROM tasks WHERE title LIKE ? OR notes LIKE ?",
                                         (f"%{rough['email']}%", f"%{rough['email']}%")) == 0)
    # Not "did not go through": the Contact page thanks a script exactly as it
    # thanks a guest (contact_post_refusal), so the reply says nothing at all.
    s.check("and thanks it as it would a guest",
            any("write back" in f.lower() for f in flashes(r))
            and not any("did not go through" in f for f in flashes(r)), detail=str(flashes(r)))
    notify = {"action": "notify", "notify_email": f"{TAG.lower()}.notify@example.invalid",
              "notify_month": "September", "notify_year": str(house_today().year + 1)}
    c, env = _client()
    c.post("/contact", environ_base=env, data={**notify, hp: "", st: _stamp(0)})
    s.check("the tell-me-when form, sent too fast, writes nothing",
            _count("SELECT COUNT(*) FROM waitlist_entries WHERE email = ?",
                   (notify["notify_email"],)) == 0)
    c, env = _client()
    c.post("/contact", environ_base=env, data={**rough, hp: "", st: _stamp(30)})
    s.check("a person's enquiry is taken",
            _count("SELECT COUNT(*) FROM waitlist_entries WHERE email = ?", (rough["email"],)) == 1)
    s.check("every refusal was noted by form",
            all(_bot_notes(a) >= 1 for a in ("book_room", "event_inquiry", "contact_enquiry")),
            detail=str({a: _bot_notes(a) for a in ("book_room", "event_inquiry", "contact_enquiry")}))

    return s
