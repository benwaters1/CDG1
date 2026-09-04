"""A guest booking a room, using only what the pages actually give them.

Every other booking test in this suite knows the answer before it starts. They
post to /book/<id> with the field names the ROUTE expects -- guest_name,
arrival_date, agree_terms -- which is exactly right for testing the route, and
proves nothing at all about the form.

    pub.post(f"/book/{room['id']}", data={"guest_name": ..., "arrival_date": ...})

Rename a field in the template, or drop a required one, and every one of those
tests still passes while the form is broken for every guest who opens it. The
route is fine. The page in front of the person is not, and nothing here would
have said so.

So this file never types a URL or a field name. It starts at the front door,
reads the links off the page it is given, finds the booking form in the HTML,
fills in the fields THE FORM RENDERS, and posts to the action THE FORM NAMES.
If the template and the route ever disagree about what a field is called, the
booking simply fails and this goes red -- which is what a guest would get.

The parser is stdlib html.parser, because this app has no build step and no
third-party HTTP or HTML library, and one test is not a reason to acquire one.
"""
from _harness import Suite, db, ensure_room

import re
from datetime import timedelta
from html.parser import HTMLParser

import _harness

m = _harness.m
TAG = "ZZJOURNEY"
EMAIL = "zzjourney@example.invalid"
# Its own connection. The booking form is rate limited per remote address, and
# every test client in this suite is 127.0.0.1 -- so by the time this file runs
# in a full pass, the suites before it have already spent the hour's budget and
# the guest is turned away at the door. Run alone it passed; run with the
# others it did not, which is the worst kind of test to leave lying about.
# A documentation-range address, so it is obviously not a real one.
GUEST_IP = "203.0.113.7"


class FormReader(HTMLParser):
    """Every <form> on a page, with the fields it actually renders.

    Deliberately forgiving about malformed markup: the job is to see the page
    the way a browser roughly would, not to validate it. test_links and the
    template checks own correctness of the markup itself.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self._open = None
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._open = {"action": a.get("action"), "method": (a.get("method") or "get").lower(),
                          "id": a.get("id"), "fields": []}
            self.forms.append(self._open)
        elif self._open is not None and tag in ("input", "select", "textarea"):
            if not a.get("name"):
                return
            self._open["fields"].append({
                "tag": tag, "name": a["name"],
                "type": (a.get("type") or ("select" if tag == "select" else "text")).lower(),
                "value": a.get("value", ""), "required": "required" in a,
                "options": [],
            })
            if tag == "textarea":
                self._textarea = self._open["fields"][-1]
        elif self._open is not None and tag == "option" and self._open["fields"]:
            self._open["fields"][-1]["options"].append(a.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "form":
            self._open = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea["value"] = (self._textarea["value"] or "") + data


def forms_on(html):
    p = FormReader()
    p.feed(html)
    return p.forms


def links_on(html, pattern):
    """Every href on the page matching a pattern, in document order."""
    return [h for h in re.findall(r'href="([^"]+)"', html) if re.match(pattern, h)]


def fill(form, answers):
    """What a browser would submit for this form, given answers by field name.

    Only fields the form RENDERS are sent. That is the whole point: if the
    route needs something the template never draws, nothing supplies it here
    either, and the booking fails the way it would for a guest.
    """
    data = {}
    for f in form["fields"]:
        name = f["name"]
        if f["type"] in ("submit", "button", "image", "reset"):
            continue
        if f["type"] in ("checkbox", "radio"):
            if name in answers:                        # unchecked boxes send nothing
                data[name] = answers[name]
            continue
        if name in answers:
            data[name] = answers[name]
        elif f["type"] == "select":
            picks = [o for o in f["options"] if o]
            if picks:
                data[name] = picks[0]
        elif f["value"]:
            data[name] = f["value"]                    # hidden fields, prefills
        elif f["required"]:
            data[name] = ""                            # rendered, required, unanswered
    return data


def _clean(conn):
    conn.execute("DELETE FROM bookings WHERE guest_email = ?", (EMAIL,))
    conn.execute("DELETE FROM guests WHERE email = ?", (EMAIL,))
    conn.commit()


def run():
    s = Suite("booking journey")
    conn = db()
    _clean(conn)
    room = ensure_room(min_occupancy=2)
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    guest = m.app.test_client()
    guest.environ_base["REMOTE_ADDR"] = GUEST_IP

    # Far enough out that nothing real is in the way, and a Tuesday-to-Friday
    # shape so a two-night minimum cannot refuse it.
    arrive = m.house_today() + timedelta(days=200)
    arrive += timedelta(days=(1 - arrive.weekday()) % 7)
    leave = arrive + timedelta(days=3)

    s.section("From the front door to a room, following the page")

    home = guest.get("/")
    s.check("the front page opens", home.status_code == 200, home)
    to_rooms = links_on(home.get_data(as_text=True), r"/book$")
    s.check("it offers a way to the rooms", bool(to_rooms),
            detail="a guest cannot start; every other test starts halfway in")

    # No fallback URL anywhere in this file. Reaching for a hardcoded /book
    # when the page offers no link is exactly the "knows the answer before it
    # starts" habit this exists to avoid: the journey would carry on and only
    # one check would notice, when in truth a guest is already stuck.
    if not to_rooms:
        conn.close()
        return s
    rooms = guest.get(to_rooms[0])
    s.check("the rooms page opens", rooms.status_code == 200, rooms)
    rooms_html = rooms.get_data(as_text=True)
    to_room = links_on(rooms_html, r"/book/\d+")
    s.check("at least one room can be opened from it", bool(to_room),
            detail="the rooms are listed but not one of them is reachable")
    if not to_room:
        conn.close()
        return s

    room_url = to_room[0]
    page = guest.get(room_url)
    s.check("the room page opens", page.status_code == 200, page)
    html = page.get_data(as_text=True)

    s.section("The form on the page, not the one the route expects")

    booking_forms = [f for f in forms_on(html)
                     if f["method"] == "post"
                     and any(x["name"] == "guest_email" for x in f["fields"])]
    s.check("there is a booking form on the room page", bool(booking_forms),
            detail="found %d form(s), none of them asking for an email"
                   % len(forms_on(html)))
    if not booking_forms:
        conn.close()
        return s
    form = booking_forms[0]

    names = {f["name"] for f in form["fields"]}
    s.check("it asks for the guest's name", "guest_name" in names, detail=str(sorted(names)))
    s.check("it asks for an arrival and a departure",
            "arrival_date" in names and "departure_date" in names)
    s.check("and it carries a terms box", "agree_terms" in names)

    # No action means "post to this URL", which is what a browser does.
    action = form["action"] or room_url
    s.check("its action resolves to a real route",
            m.app.url_map.bind("localhost").test(action.split("?")[0], "POST"),
            detail=action)

    s.section("Filling in what it renders, and nothing else")

    answers = {
        "arrival_date": arrive.isoformat(),
        "departure_date": leave.isoformat(),
        "guest_name": TAG + " Guest",
        "guest_email": EMAIL,
        "guest_phone": "+33 6 00 00 00 00",
        "adults": "2",
        "party_size": "2",
        "agree_terms": "on",
        "special_requests": "",
        "promo_code": "",
    }
    data = fill(form, answers)

    # Anything the form renders as REQUIRED that this test has no answer for
    # would be posted empty. Said out loud rather than left to fail obscurely
    # three checks later.
    unanswered = sorted(f["name"] for f in form["fields"]
                        if f["required"] and f["type"] not in ("checkbox", "radio")
                        and not data.get(f["name"]))
    s.check("every required field on the form has something to put in it",
            not unanswered, detail=str(unanswered))

    sent = guest.post(action, data=data, follow_redirects=True)
    s.check("the form submits", sent.status_code == 200, sent)

    booked = conn.execute(
        "SELECT * FROM bookings WHERE guest_email = ? ORDER BY id DESC LIMIT 1",
        (EMAIL,)).fetchone()
    s.check("a booking exists afterwards", booked is not None,
            detail="; ".join(_harness.flashes(sent)[:2])
                   or "no booking row and the page said nothing")
    if booked is None:
        _clean(conn)
        conn.close()
        return s

    s.section("What was submitted is what was stored")

    s.check("the arrival is the date that was typed",
            str(booked["arrival_date"])[:10] == arrive.isoformat(),
            detail=str(booked["arrival_date"]))
    s.check("and the departure is too",
            str(booked["departure_date"])[:10] == leave.isoformat(),
            detail=str(booked["departure_date"]))
    s.check("the name went in", (booked["guest_name"] or "").startswith(TAG),
            detail=str(booked["guest_name"]))
    s.check("the room is the one whose page was open",
            str(booked["room_id"]) == room_url.split("?")[0].rsplit("/", 1)[-1],
            detail="%s vs %s" % (booked["room_id"], room_url))

    s.section("And the guest is given a way back to it")

    body = sent.get_data(as_text=True)
    s.check("the page after booking names the guest",
            TAG in body, detail="a confirmation that does not say whose it is")
    s.check("it shows the reference",
            (booked["reference_code"] or "") in body,
            detail=str(booked["reference_code"]))

    token = booked["manage_token"]
    s.check("a manage token was issued", bool(token))
    if token:
        manage = guest.get("/book/confirmation/%s" % token)
        s.check("the confirmation link works", manage.status_code == 200, manage)
        s.check("and shows the same stay",
                (booked["reference_code"] or "") in manage.get_data(as_text=True))
        # Somebody else's token must not open it, and a truncated one is the
        # cheapest thing an idle person would try.
        s.check("a token that is not theirs does not",
                guest.get("/book/confirmation/%s" % (token[:-4] + "zzzz")).status_code == 404)

    s.section("And the door is not held open for a script")

    # Proved rather than assumed, because this file gave itself a fresh address
    # to get past it -- and a test that steps around a guard owes the reader a
    # demonstration that the guard is still there.
    limit = 0
    for i in range(12):
        r = guest.post(action, data=dict(data, guest_email="zzflood%d@example.invalid" % i),
                       follow_redirects=True)
        if "Too many booking attempts" in r.get_data(as_text=True):
            limit = i + 1
            break
    s.check("the booking form stops taking submissions from one connection",
            limit > 0, detail="twelve went through unchallenged")
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE 'zzflood%@example.invalid'")
    conn.execute("DELETE FROM guests WHERE email LIKE 'zzflood%@example.invalid'")
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()

    _clean(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
