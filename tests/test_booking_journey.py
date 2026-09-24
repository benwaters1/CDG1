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

The form reader lives in _harness, because test_funnel_forms needs it too and
two copies of a parser is two things to keep in step. It is stdlib
html.parser: this app has no build step and no third-party HTML library.
"""
from _harness import (Suite, db, ensure_room, forms_on, links_on, fill,
                      flashes, free_window, house_today, visible_text)

import os
import re
from datetime import date, timedelta

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

TPL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "templates")

# The house refunds nothing a guest cancels -- the terms call any refund a
# goodwill gesture, never an entitlement -- so these are the phrases that
# would promise otherwise. The terms page is deliberately NOT read with them:
# it says a stay the CHATEAU cancels is refunded in full, which is true, and is
# the house's promise rather than the guest's.
REFUND_PROMISES = re.compile(
    r"free\s+cancell?ation|cancel(?:led|ling)?\s+(?:for\s+free|free\s+of\s+charge)"
    r"|cancell?ation\s+is\s+free|free\s+to\s+cancel|cancel\s+more\s+than"
    r"|refunded\s+in\s+full|\bfull(?:y)?\s+refund|money\s+back"
    r"|(?<!non-)\brefundable\s+(?:up\s+to|until|if)", re.I)


def _guest_templates():
    """Every template a guest can be shown, with its Jinja comments taken out.

    That is everything but the staff side: the pages that extend base.html,
    and any partial that only those pages use -- the staff refund form offers
    "Fully refund" as a button, and that is the house deciding, not a promise.
    A partial nothing uses yet stays IN: the ones awaiting wiring are guest
    designs, and one of them is how the last wrong promise was found.

    The comments go because they are where the history of a wrong promise
    gets written down, and nobody outside this repo ever reads one.
    """
    sources = {}
    for name in sorted(os.listdir(TPL)):
        if name.endswith(".html"):
            with open(os.path.join(TPL, name), encoding="utf-8") as f:
                sources[name] = re.sub(r"(?s)\{#.*?#\}", " ", f.read())
    used_by = {name: set() for name in sources}
    for name, src in sources.items():
        for ref in re.findall(r"""\{%-?\s*(?:include|import|from|extends)\s+["']([^"']+)["']""", src):
            if ref in used_by:
                used_by[ref].add(name)
    # admin_ pages are the owner's whatever they extend: two of them borrow
    # the public layout, one to show photographs and one for the office wall.
    staff = ({"base.html"} | used_by.get("base.html", set())
             | {n for n in sources if n.startswith("admin_")})
    grew = True
    while grew:
        grew = False
        for name, users in used_by.items():
            if name not in staff and users and users <= staff:
                staff.add(name)
                grew = True
    for name in sorted(sources):
        if name not in staff:
            yield name, sources[name]


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

    s.section("A house that refunds nothing never promises a refund")

    # The room page said "Cancellation: Non-refundable" in one panel and "Free
    # cancellation: up to 30 days before arrival" in the panel above the form,
    # on the page where the guest decides and pays. The second read a
    # free_cancel_days setting nothing has ever written, so it always fell
    # back to 30. The confirmation carried a branch of the same kind, dormant
    # only because ITS setting, cancel_free_days, had never been allowlisted.
    # So both switches are thrown before anything is read: a promise that is
    # off because nobody has typed a number yet is still a promise.
    s.check("the pattern sees the line it was written for, and not the policy",
            bool(REFUND_PROMISES.search("Free cancellation Up to 30 days before arrival"))
            and not REFUND_PROMISES.search(
                "Non-refundable. Write to us and we can sometimes move your dates instead"))
    switches = ("free_cancel_days", "cancel_free_days")
    was_public = m.PUBLIC_SETTINGS
    m.PUBLIC_SETTINGS = tuple(was_public) + switches
    for key in switches:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, '14')",
                     (key,))
    conn.commit()
    try:
        shown = {"the room page": visible_text(guest.get(room_url).get_data(as_text=True))}
        if token:
            shown["the confirmation"] = visible_text(
                guest.get("/book/confirmation/%s" % token).get_data(as_text=True))
            shown["the guest's booking page"] = visible_text(
                guest.get("/book/manage/%s" % token).get_data(as_text=True))
    finally:
        m.PUBLIC_SETTINGS = was_public
        conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)", switches)
        conn.commit()
    s.check("the room page says what the policy is",
            "non-refundable" in shown["the room page"].lower(),
            detail="everything below rests on this. If the house changes its "
                   "policy, the terms, the pages and this change together")
    for where, text in shown.items():
        found = REFUND_PROMISES.search(text)
        s.check("%s promises no refund" % where, not found,
                detail=found and text[max(0, found.start() - 80):found.end() + 80])

    # And in the source, because a partial nothing renders YET is where the
    # next one comes from: _cancel.html, awaiting wiring, refunded "the
    # deposit in full" on a house that takes no deposit and refunds nothing.
    promising = ["%s: %r" % (name, hit.group(0))
                 for name, src in _guest_templates()
                 for hit in [REFUND_PROMISES.search(src)] if hit]
    s.check("no template a guest can be shown promises one either",
            not promising, detail="; ".join(promising))

    s.section("And a guest who pays at once is told so")

    # "Nothing is taken now: you are sending a request. We confirm it, and
    # only then does anything leave your account." Both halves were false: the
    # house confirms at once, and with card payments on it charges the card
    # before the confirmation is drawn. Read with card payments ON, as the
    # house runs -- a GET of the room page never calls Stripe.
    real_stripe = m.stripe_enabled
    m.stripe_enabled = lambda: True
    try:
        paying = visible_text(guest.get(room_url).get_data(as_text=True)).lower()
    finally:
        m.stripe_enabled = real_stripe
    s.check("the page a paying guest sees offers to take payment",
            "pay & book" in paying,
            detail="so the checks below read the page the house actually serves")
    for claim in ("nothing is taken now", "sending a request",
                  "only then does anything leave"):
        s.check("it does not say %r" % claim, claim not in paying)

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

    s.section("The search says no at the search, not at the end of the form")
    # ALL OF THIS WAS ALREADY REFUSED -- on submitting the booking, after the
    # guest had chosen a room and typed their name, email, phone and terms.
    # Nothing bad could be stored. What it cost was the guest's time and their
    # belief that the house knows what it is doing: rooms and prices offered
    # for dates that were never possible, and the refusal arriving last.
    pub = m.app.test_client()
    today = house_today()

    def search(arrival, departure, party="2"):
        return pub.get("/book", query_string={
            "arrival": str(arrival), "departure": str(departure),
            "party_size": str(party)})

    def offers_a_room(response):
        return bool(re.search(r'href="/book/\d+\?[^"]*arrival=',
                              response.get_data(as_text=True)))

    past = search(today - timedelta(days=30), today - timedelta(days=28))
    s.check("a stay in the past offers nothing", not offers_a_room(past))
    s.check("and says why, in the words the booking form uses",
            "Choose an arrival date in the future." in " ".join(flashes(past)),
            detail="%s -- being told two different things about one mistake "
                   "is worse than being told once" % (flashes(past)[:1],))

    backwards = search(today + timedelta(days=40), today + timedelta(days=38))
    s.check("dates the wrong way round are explained",
            not offers_a_room(backwards)
            and any("after the arrival" in f for f in flashes(backwards)),
            detail="%s -- this used to render the page as though nobody had "
                   "searched at all" % (flashes(backwards)[:1],))

    half = pub.get("/book", query_string={"arrival": str(today + timedelta(days=40))})
    s.check("and so is one date without the other",
            any("both an arrival and a departure" in f for f in flashes(half)),
            detail=str(flashes(half)[:1]))

    s.section("A room too small for the party is not offered as available")
    # THE ONE THAT IS NOT AN EDGE CASE. The rooms sleep two, two, two, three
    # and five. A family of four was shown all five as available, picked one,
    # filled in the whole form, and was told it sleeps two.
    sizes_conn = db()
    sizes = sorted(r["max_occupancy"] or 0 for r in sizes_conn.execute(
        "SELECT max_occupancy FROM rooms WHERE active = 1"))
    widest = sizes_conn.execute(
        """SELECT id FROM rooms WHERE active = 1
           ORDER BY max_occupancy DESC, id LIMIT 1""").fetchone()["id"]
    sizes_conn.close()
    s.check("there are rooms of more than one size to tell apart",
            len(set(sizes)) > 1, detail=str(sizes))
    biggest = max(sizes)
    smallest = min(sizes)

    # Asked of the calendar rather than counted off today: free_window
    # returns an arrival with the nights genuinely free for that room,
    # which a fixed offset does not, because the seeded ateliers move
    # with the calendar.
    start = free_window(widest, 2)
    when = (start, start + timedelta(days=2))
    family = search(when[0], when[1], biggest)
    body = family.get_data(as_text=True)
    s.check("a party only the largest room can take is still offered one",
            offers_a_room(family),
            detail="party of %d, largest room sleeps %d" % (biggest, biggest))
    s.check("and the rooms that cannot hold them say so",
            "Sleeps up to %d" % smallest in body,
            detail="the reason has to name the size, or it reads as a date "
                   "clash and they go and try other dates")

    too_many = search(when[0], when[1], biggest + 1)
    s.check("a party no room can take is offered nothing",
            not offers_a_room(too_many),
            detail="party of %d" % (biggest + 1))

    s.section("The number of nights is the number of nights")
    # Worked out in the template, on the ISO strings, with every month treated
    # as thirty days. Three nights read as two across the end of August, four
    # as six across February, and a five-night New Year stay as MINUS three
    # hundred and fifty-six -- which the positive-only guard then hid, so the
    # booking somebody plans a year ahead showed no night count at all.
    def nights_shown(arrival, departure):
        body = search(arrival, departure).get_data(as_text=True)
        found = re.search(r"·\s*(\d+)\s*night", body)
        return int(found.group(1)) if found else None

    for label, arrival, departure in [
        ("inside one month", today + timedelta(days=40), today + timedelta(days=43)),
        ("across a month end", date(today.year + 1, 8, 30), date(today.year + 1, 9, 2)),
        ("across February", date(today.year + 1, 2, 26), date(today.year + 1, 3, 2)),
        ("across New Year", date(today.year + 1, 12, 28), date(today.year + 2, 1, 2)),
    ]:
        want = (departure - arrival).days
        s.check("%s reads %d night(s)" % (label, want),
                nights_shown(arrival, departure) == want,
                detail="page said %s" % nights_shown(arrival, departure))

    s.section("The date picker writes into the form it was opened from")
    # A LIVE BOOKING BUG, found by the design side and confirmed here.
    #
    # The picker bound its two date inputs with document.querySelector,
    # which returns the FIRST match in the document. The homepage carries two
    # forms with arrival and departure: the quick-book widget in the nav
    # drawer, which comes first and uses native inputs, and the hero search,
    # which is the one with the calendar. So the hero picker wrote the
    # guest's dates into the widget's hidden inputs and the hero form posted
    # empty -- /book?arrival=&departure=. The calendar accepted the dates and
    # the results page had never been given any.
    #
    # Checked on the SOURCE because this is browser behaviour and the suite
    # runs no JavaScript. Two things have to hold: the page really does carry
    # more than one such form (or the bug is unreachable and this proves
    # nothing), and the picker no longer resolves those inputs document-wide.
    home = pub.get("/").get_data(as_text=True)
    pairs = [f for f in forms_on(home)
             if any(x["name"] == "arrival" for x in f["fields"])
             and any(x["name"] == "departure" for x in f["fields"])]
    s.check("the home page carries more than one dated form",
            len(pairs) > 1,
            detail="%d found; with one the picker cannot pick wrong and the "
                   "check below is vacuous" % len(pairs))
    base = open(os.path.join(_harness.ROOT, "templates", "public_base.html"),
                encoding="utf-8").read()
    s.check("the picker does not bind its dates document-wide",
            "document.querySelector('input[type=date][name=arrival]')"
            not in base,
            detail="document.querySelector returns the first match on the "
                   "page, and there is more than one")
    s.check("it scopes them to the form instead",
            "closest('form')" in base,
            detail="resolved from the button outwards, on every open")

    s.section("What the page says about a bathroom is what the room has")
    # A CLAIM MADE AT THE POINT OF PAYMENT, and it was wrong for two of the
    # five rooms. The card decided shared-versus-private by testing whether
    # the room NAME contained "Shared" -- no room is named that way, so every
    # room said "Private, downstairs", including the two that share one.
    # A guest paid for a private bathroom and would have found out on
    # arrival. The rooms table has carried a `bathroom` column the whole
    # time, and _roompick.html was already reading it, so the right answer
    # was one column away in a file alongside.
    truth = db()
    rooms_now = truth.execute(
        "SELECT name, bathroom FROM rooms WHERE active = 1").fetchall()
    truth.close()
    shared = [r for r in rooms_now if (r["bathroom"] or "") == "shared"]
    s.check("some rooms share a bathroom and some do not",
            shared and len(shared) < len(rooms_now),
            detail="%d of %d share; with none or all the check below cannot "
                   "tell a right answer from a lucky one"
                   % (len(shared), len(rooms_now)))
    page = pub.get("/book").get_data(as_text=True)
    # COUNTED ON THE ROW, not on one sentence. This counted the exact words
    # "Shared with one other room", and the eleventh handover rewrote them to
    # "Shared downstairs with the Chambre Tilleul" -- more specific and just as
    # true -- so the check read zero shared rooms on a page that named both.
    # What must hold is that each room's Bathroom row starts with the word its
    # own column says.
    said_shared = len(re.findall(r"<dt>Bathroom</dt><dd>\s*Shared", page))
    said_private = len(re.findall(r"<dt>Bathroom</dt><dd>\s*Private", page))
    s.check("the page says shared exactly as often as a room shares",
            said_shared == len(shared),
            detail="page says shared %d time(s), %d room(s) do"
            % (said_shared, len(shared)))
    s.check("and never calls a shared bathroom private",
            said_private == len(rooms_now) - len(shared),
            detail="page says private %d time(s), %d room(s) are"
            % (said_private, len(rooms_now) - len(shared)))

    s.section("Nobody is told a room has no stairs unless it truly has none")
    # The room picker asks "how are you with stairs?" and, for somebody who
    # says they are difficult, answers with "it is on the ground floor, so
    # no staircase". That sentence is generated from rooms.floor, and one
    # room carried 'ground' when every bedroom in this house is upstairs --
    # so the app made a promise about a staircase to the one guest who most
    # needed it to be true.
    #
    # Checked as BEHAVIOUR rather than as a fact about the building: a room
    # may legitimately become a ground-floor room one day, and this must not
    # go red when it does. What must hold is that the flag follows the
    # column, and that a room with no floor recorded is never called
    # step-free -- not knowing is not the same as knowing there are none.
    floors = db()
    recorded = {r["name"]: (r["floor"] or "").strip().lower()
                for r in floors.execute(
                    "SELECT name, floor FROM rooms WHERE active = 1")}
    floors.close()
    picker = pub.get("/book").get_data(as_text=True)
    flags = re.findall(r'stairs:"(\w+)"', picker)
    s.check("the picker publishes a stairs flag for every room",
            len(flags) == len(recorded),
            detail="%d flags for %d rooms" % (len(flags), len(recorded)))
    s.check("and calls step-free exactly the rooms recorded as ground floor",
            flags.count("ground")
            == sum(1 for f in recorded.values() if f == "ground"),
            detail="%d called step-free, %d recorded as ground"
            % (flags.count("ground"),
               sum(1 for f in recorded.values() if f == "ground")))
    s.check("so a room with no floor recorded is never called step-free",
            not [n for n, f in recorded.items() if not f]
            or flags.count("ground") < len(recorded),
            detail="an unrecorded floor must read as stairs, not as none")

    _clean(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
