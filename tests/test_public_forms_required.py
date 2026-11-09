"""What a public form's page requires, the route behind it requires.

9 November 2026. "Private event. Test" kept reaching the house from the
Contact page with no name, though the page will not send without one: a
browser enforces `required`, so a post without the field came from something
posting the fields itself, and the route had never asked. The owner: "a test
could read every public form and check that each field the page requires is
also checked by the site -- yes, definitely."

So this reads the forms, not a list of field names. For every form a stranger
can post to and have something kept, it takes the fields the TEMPLATE marks
`required` -- a handover that adds one is checked from that moment -- and:

  - posts the form whole, which must be taken (else the rest proves nothing);
  - posts it once per required field with that field empty, which must not
    be: nothing kept, or, for the Contact page, kept only as Spam.

What it found when first run, all fixed with it: the room, atelier and dinner
forms took a booking with no telephone; the dinner and atelier waiting lists
took a place with no party size, and carried no bot guard at all; and the
Contact page took a post with no topic.

And every POST form on a public page is either checked here or named in
NOT_HERE with the reason, so a new one cannot arrive unnoticed.
"""
import os
import re
from datetime import timedelta
from html.parser import HTMLParser

from _harness import Suite, db, house_today
import _harness

m = _harness.m
TAG = "zzreq"
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(m.__file__)), "templates")
PHONE = "+33 6 12 34 56 78"

# Public forms not checked here, and why. A form behind a link from a letter
# (a token in its address) cannot be reached by a stranger; one that looks a
# booking up keeps nothing.
NOT_HERE = {
    "_cancel.html": "a guest's own booking, behind the link in their letter",
    "booking_finish.html": "a guest's own booking, behind the link in their letter",
    "event_manage.html": "a guest's own event, behind the link in their letter",
    "event_quote.html": "a quote sent to one guest, behind its link",
    "guest_account.html": "a guest's own account, behind the link in their letter",
    "guest_feedback_form.html": "feedback for one stay, behind the link in its letter",
    "guest_portal.html": "a guest's own profile, behind the link in their letter",
    "guest_statement.html": "a guest's own bill, behind the link in their letter",
    "instructor_sheet.html": "the instructor's sheet, behind its link",
    "manage_booking.html": "a guest's own booking, behind the link in their letter",
    "pay_share.html": "one share of a bill, behind its link",
    "restaurant_manage.html": "a guest's own table, behind the link in their letter",
    "workshop_feedback_form.html": "feedback for one atelier, behind the link in its letter",
    "workshop_manage.html": "a guest's own atelier booking, behind the link in their letter",
    "stay_checkin.html": "a guest's own check-in, behind the link in their letter",
    "channel_checkin.html": "a Booking.com guest's check-in, behind the link in their letter",
    "unsubscribe.html": "one address's unsubscribe, behind the link in its letter",
    "door.html": "a guest's door code, behind the link in their letter",
    "find_booking.html": "looks a booking up; keeps nothing",
    "event_find.html": "looks an event up; keeps nothing",
    "restaurant_find.html": "looks a table up; keeps nothing",
    "workshop_find.html": "looks an atelier booking up; keeps nothing",
    "voucher_check.html": "looks a voucher up; keeps nothing",
    "channel_checkin_find.html": "looks a Booking.com stay up and sends the guest to its check-in; keeps nothing",
    "recover.html": "the owner's recovery, which writes only to the owner",
    "guest_account_request.html": "sends a link only to an address already on a booking",
    "_waitlist.html": "a macro no page draws",
}


def _source(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


def _forms(src):
    """(endpoint or None, body) for each POST form in a template's source."""
    out = []
    for f in re.finditer(r"<form\b([^>]*)>(.*?)</form>", src, re.S | re.I):
        attrs, body = f.group(1), f.group(2)
        if not re.search(r'method="post"', attrs, re.I):
            continue
        action = re.search(r'action="([^"]*)"', attrs)
        ep = re.search(r"url_for\('([^']+)'", action.group(1)) if action else None
        out.append((ep.group(1) if ep else (None if not action else action.group(1)), body))
    return out


class _Drawn(HTMLParser):
    """The POST forms on a page as served: (action, [required field names])."""

    def __init__(self):
        super().__init__()
        self.forms, self.cur = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and (a.get("method") or "").lower() == "post":
            self.cur = (a.get("action"), [])
            self.forms.append(self.cur)
        elif self.cur is not None and tag in ("input", "select", "textarea")                 and a.get("name") and "required" in a:
            self.cur[1].append(a["name"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.cur = None


def drawn_required(page, url, endpoint):
    """The required fields of the form posting to `url` (or to the page
    itself, for endpoint None) on `page` as it is served, or None when the
    page does not draw that form today -- a waiting list appears only when
    the date is full. Read as served because most of these fields are drawn
    by macros (party_fields, phone_field, the address block)."""
    p = _Drawn()
    p.feed(m.app.test_client().get(page).get_data(as_text=True))
    for action, names in p.forms:
        if (endpoint is None and not action) or (endpoint is not None and action == url):
            return names
    return None


def required_fields(template, endpoint):
    """The fields the template's form for `endpoint` marks required, read
    from its source: for a form the page draws only sometimes.

    A bare `required` on the tag, not one written by an expression
    ({{ 'required' if ... }}: an atelier's own questions, which the route
    already checks one by one), plus every phone_field(), which always
    draws its box required."""
    found = [body for ep, body in _forms(_source(template)) if ep == endpoint]
    assert len(found) == 1, (template, endpoint, len(found))
    body = found[0]
    names = []
    for tag in re.finditer(r"<(?:input|select|textarea)\b[^>]*>", body, re.S):
        text = tag.group(0)
        bare = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", text, flags=re.S)
        name = re.search(r'\bname="([^"{]+)"', text)
        if name and re.search(r"\brequired\b", bare):
            names.append(name.group(1))
    names += re.findall(r"phone_field\(\s*'[^']*'\s*,\s*'([^']+)'", body)
    return names


def _clear_limits():
    conn = db()
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()


def _one(sql, args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    like = TAG + ".%"
    for table, col in (("waitlist_entries", "email"), ("restaurant_waitlist", "email"),
                       ("workshop_waitlist", "email"), ("event_inquiries", "contact_email"),
                       ("newsletter_subscribers", "email"), ("consent_events", "email")):
        conn.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", (like,))
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR notes LIKE ?", (f"%{TAG}.%", f"%{TAG}.%"))
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_email LIKE ?)", (like,))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG + " sitting",))
    conn.execute("DELETE FROM workshops WHERE title = ?", (TAG + " atelier",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (like,))
    conn.commit()
    conn.close()


def _atelier():
    conn = db()
    now = _harness.datetime_now()
    wid = conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 800, 8, 1, 97, ?, 30)""", (TAG + " atelier", now)).lastrowid
    start = house_today() + timedelta(days=230)
    sid = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
           created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=2)).isoformat(),
         TAG + " sitting", now)).lastrowid
    conn.commit()
    conn.close()
    return sid


def _room_and_nights():
    conn = db()
    try:
        room = conn.execute("SELECT * FROM rooms WHERE active = 1 AND max_occupancy >= 2 "
                            "ORDER BY id LIMIT 1").fetchone()
        day = house_today() + timedelta(days=75)
        for _ in range(400):
            with m.app.test_request_context():
                ok, _why = m.is_range_available(conn, room["id"], day, day + timedelta(days=3))
            if ok:
                return room, day
            day += timedelta(days=1)
    finally:
        conn.close()
    raise AssertionError("no three free nights for the room form")


def _dinner_day():
    conn = db()
    try:
        day = house_today() + timedelta(days=20)
        for _ in range(120):
            if not m.restaurant_closed_evening(conn, day):
                return day
            day += timedelta(days=1)
    finally:
        conn.close()
    raise AssertionError("no evening La Table serves")


def run():
    s = Suite("What a public form requires, its route requires")
    _cleanup()
    conn = db()
    dining = conn.execute("SELECT * FROM restaurant_settings WHERE id = 1").fetchone()
    conn.execute("UPDATE restaurant_settings SET enabled = 1, opening_date = NULL, "
                 "capacity = 20, deposit_percent = NULL WHERE id = 1")
    conn.commit()
    conn.close()
    real_send = m.send_email
    m.send_email = lambda *a, **k: True
    try:
        _run(s)
    finally:
        m.send_email = real_send
        if dining:
            conn = db()
            conn.execute("UPDATE restaurant_settings SET enabled = ?, opening_date = ?, "
                         "capacity = ?, deposit_percent = ? WHERE id = 1",
                         (dining["enabled"], dining["opening_date"], dining["capacity"],
                          dining["deposit_percent"]))
            conn.commit()
            conn.close()
        _cleanup()
    return s


def _run(s):
    sid = _atelier()
    room, arrive = _room_and_nights()
    dinner = _dinner_day()
    later = house_today() + timedelta(days=40)

    def contact_kept(email):
        e = _one("SELECT spam_at FROM waitlist_entries WHERE email = ?", (email,))
        return bool(e) and not e["spam_at"]

    # label, template, endpoint the form posts to (None: the page itself),
    # address to post, a person's answers, fields the page's script fills from
    # a required one (blank together), and whether a post was taken.
    FORMS = [
        ("Write to Us on Contact", "_flexible.html", "contact_page", "/contact",
         lambda e: {"action": "flexible", "topic": "private_event", "name": "Zz Person",
                    "email": e, "message": "A dinner for forty in June, if you can."},
         {}, contact_kept),
        ("Tell me when a month opens", "_notify.html", "contact_page", "/contact",
         lambda e: {"action": "notify", "notify_month": "June",
                    "notify_year": str(house_today().year + 1), "notify_email": e},
         {}, lambda e: bool(_one("SELECT 1 FROM waitlist_entries WHERE email = ?", (e,)))),
        ("The newsletter", "_newsletter.html", "newsletter_subscribe", "/newsletter/subscribe",
         lambda e: {"email": e},
         {}, lambda e: bool(_one("SELECT 1 FROM newsletter_subscribers WHERE email = ?", (e,)))),
        ("The newsletter, on the booking page", "book_rooms.html", "newsletter_subscribe",
         "/newsletter/subscribe",
         lambda e: {"email": e},
         {}, lambda e: bool(_one("SELECT 1 FROM newsletter_subscribers WHERE email = ?", (e,)))),
        ("An event enquiry", "events_info.html", "submit_event_inquiry", "/events/inquire",
         lambda e: {"event_type": "wedding", "contact_name": "Zz Person", "contact_email": e,
                    "guest_count": "40", "message": "June"},
         {}, lambda e: bool(_one("SELECT 1 FROM event_inquiries WHERE contact_email = ?", (e,)))),
        ("A room", "book_room.html", None, f"/book/{room['id']}",
         lambda e: {"arrival_date": arrive.isoformat(),
                    "departure_date": (arrive + timedelta(days=3)).isoformat(),
                    "adults": "2", "party_size": "2", "guests_under_18": "0",
                    "guest_name": "Zz Person", "guest_email": e, "agree_terms": "on",
                    **_harness.BOOKING_DETAILS, "guest_phone": PHONE},
         {"adults": ["party_size"]},
         lambda e: bool(_one("SELECT 1 FROM bookings WHERE guest_email = ?", (e,)))),
        ("The room waiting list", "book_rooms.html", "join_waitlist", "/waitlist/join",
         lambda e: {"name": "Zz Person", "email": e, "party_size": "2",
                    "desired_arrival": later.isoformat(),
                    "desired_departure": (later + timedelta(days=2)).isoformat()},
         {}, lambda e: bool(_one("SELECT 1 FROM waitlist_entries WHERE email = ?", (e,)))),
        ("A table at La Table", "restaurant_book.html", None, "/restaurant/book",
         lambda e: {"dinner_date": dinner.isoformat(), "party_size": "2",
                    "guest_name": "Zz Person", "guest_email": e, "guest_phone": PHONE},
         {}, lambda e: bool(_one("SELECT 1 FROM restaurant_bookings WHERE guest_email = ?", (e,)))),
        ("The dinner waiting list", "restaurant_book.html", "join_restaurant_waitlist",
         "/restaurant/waitlist/join",
         lambda e: {"name": "Zz Person", "email": e, "party_size": "2",
                    "desired_date": dinner.isoformat()},
         {}, lambda e: bool(_one("SELECT 1 FROM restaurant_waitlist WHERE email = ?", (e,)))),
        ("A place on an atelier", "workshop_register.html", None, f"/workshops/register/{sid}",
         lambda e: {"party_size": "1", "occupancy_type": "double", "guest_name": "Zz Person",
                    "guest_email": e, **_harness.WORKSHOP_DETAILS, "guest_phone": PHONE},
         {}, lambda e: bool(_one("SELECT 1 FROM workshop_bookings WHERE guest_email = ?", (e,)))),
        ("The atelier waiting list", "workshop_register.html", "join_workshop_waitlist",
         "/workshops/waitlist/join",
         lambda e: {"session_id": str(sid), "name": "Zz Person", "email": e, "party_size": "2"},
         {}, lambda e: bool(_one("SELECT 1 FROM workshop_waitlist WHERE email = ?", (e,)))),
    ]

    PAGES = {
        "Write to Us on Contact": "/contact",
        "The newsletter": "/",
        "The newsletter, on the booking page": "/book",
        "An event enquiry": "/events",
        "A room": f"/book/{room['id']}",
        "A table at La Table": "/restaurant/book",
        "A place on an atelier": f"/workshops/register/{sid}",
    }

    s.section("Every public form that keeps something is checked")
    checked = {(t, ep) for _l, t, ep, *_r in FORMS}
    public = []
    for name in sorted(os.listdir(TEMPLATES)):
        if not name.endswith(".html"):
            continue
        src = _source(name)
        base = re.search(r"""extends\s+["']([^"']+)""", src)
        if base and base.group(1) != "public_base.html":
            continue
        for ep, _body in _forms(src):
            if ep and m.is_staff_page(ep):
                continue
            if not base and not ep:
                continue          # a staff macro posting to its page
            if not base and ep and ep not in {e for _t, e in checked} and name not in NOT_HERE:
                # A macro: public only if it posts somewhere a stranger may.
                rule = next((r for r in m.app.url_map.iter_rules() if r.endpoint == ep), None)
                if rule is None or any("token" in a for a in rule.arguments):
                    continue
            public.append((name, ep))
    unaccounted = sorted({(n, ep) for n, ep in public
                          if (n, ep) not in checked and n not in NOT_HERE})
    s.check("each one is checked below, or named with the reason it is not",
            not unaccounted, detail="; ".join(f"{n} -> {ep}" for n, ep in unaccounted))
    missing = [label for label, t, e, *_r in FORMS
               if not any(found == e for found, _b in _forms(_source(t)))]
    s.check("and every form named as checked is still on its page", not missing,
            detail=", ".join(missing))

    for label, template, endpoint, url, data, together, taken in FORMS:
        s.section(label)
        required = drawn_required(PAGES[label], url, endpoint) if PAGES.get(label) else None
        if required is None:
            required = required_fields(template, endpoint)
        s.check("the page requires something", bool(required), detail=template)
        guard = _harness.contact_form_fields()
        whole = f"{TAG}.{re.sub(r'[^a-z]', '', label.lower())[:20]}.whole@example.invalid"
        _clear_limits()
        r = m.app.test_client().post(url, data={**data(whole), **guard})
        s.check("filled in as a person would, it is taken", taken(whole),
                detail=f"HTTP {r.status_code}; " + "; ".join(_harness.flashes(r)[:2]))
        if template == "book_room.html":
            _cleanup_room(whole)
        for field in required:
            email = f"{TAG}.{re.sub(r'[^a-z]', '', label.lower())[:20]}.{field.replace('_', '')}@example.invalid"
            posted = {**data(email), **guard}
            for name in [field] + together.get(field, []):
                posted[name] = ""
            if field in ("email", "guest_email", "contact_email", "notify_email"):
                email = None
            _clear_limits()
            r = m.app.test_client().post(url, data=posted)
            kept = taken(email) if email else _any_kept(label)
            s.check(f"without {field}, it is not", not kept and r.status_code < 500,
                    detail=f"HTTP {r.status_code}; page requires {required}")
            if template == "book_room.html" and email:
                _cleanup_room(email)


def _cleanup_room(email):
    conn = db()
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_email = ?)", (email,))
    conn.execute("DELETE FROM bookings WHERE guest_email = ?", (email,))
    conn.commit()
    conn.close()


def _any_kept(label):
    """With the address itself left blank there is nothing to look it up by,
    so: did anything arrive with no address at all."""
    checks = {
        "Write to Us on Contact": "SELECT 1 FROM waitlist_entries WHERE email = '' AND spam_at IS NULL",
        "Tell me when a month opens": "SELECT 1 FROM waitlist_entries WHERE email = ''",
        "The newsletter": "SELECT 1 FROM newsletter_subscribers WHERE email = ''",
        "The newsletter, on the booking page": "SELECT 1 FROM newsletter_subscribers WHERE email = ''",
        "An event enquiry": "SELECT 1 FROM event_inquiries WHERE contact_email = ''",
        "A room": "SELECT 1 FROM bookings WHERE guest_email = '' AND guest_name = 'Zz Person'",
        "The room waiting list": "SELECT 1 FROM waitlist_entries WHERE email = ''",
        "A table at La Table": "SELECT 1 FROM restaurant_bookings WHERE guest_email = ''",
        "The dinner waiting list": "SELECT 1 FROM restaurant_waitlist WHERE email = ''",
        "A place on an atelier": "SELECT 1 FROM workshop_bookings WHERE guest_email = ''",
        "The atelier waiting list": "SELECT 1 FROM workshop_waitlist WHERE email = ''",
    }
    return bool(_one(checks[label], ()))


if __name__ == "__main__":
    print(run().report())
