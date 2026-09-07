"""One guest, all the way through, holding only what they were given.

test_booking_journey proves the FORM: it starts at the front door, reads the
fields the template renders and posts to the action it names. It stops one step
after the booking exists, and it finds the way back into the booking by reading
`manage_token` out of the database.

That last detail is the gap this file exists for. Three hundred and thirty-six
suites check the steps, and every one of them is handed whatever it needs —
a token, an id, a URL — straight out of a table. So each step can pass while
the road between two of them is broken, and the most expensive place for that
to happen is the FIRST joint: the manage token reaches a real guest in exactly
one way, printed inside the letter create_booking sends. If that letter goes
out with no link, or a link built from the wrong host, the guest is holding a
reference code and nothing else — and not one existing check can see it,
because not one of them gets the token the way the guest does.

THE RULE HERE: nothing is read out of the database that the guest was not
given. The token comes out of the captured letter body. Every page after it is
opened with that token and no session. Where the walk needs a figure to compare
against it asks the app's own definition, never a number of its own.

What it walks, in order:

  the booking made through the page -> the letter -> the link inside it ->
  their own page, with no login -> when they are arriving -> asking for
  something, which has to reach the house -> adding something, which moves
  the bill -> the two money documents agreeing -> the pay button when there
  is no card processor -> what is on while they are here -> the house
  confirming, which writes again with the same link

Findings are reported against the STEP, because "the bill is wrong" and "the
guest could never reach the bill" are different problems with different fixes.
"""
import re
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, forms_on, links_on, fill
import _harness

m = _harness.m
TAG = "ZZWHOLE"
EMAIL = "zzwhole@example.invalid"
# Its own address. The booking form is rate limited per remote address and
# every test client here is 127.0.0.1, so in a full run the suites before this
# one have already spent the hour's budget and the guest is turned away at the
# door — green alone, red in the run, which is the worst kind to leave about.
# A documentation-range address, so it is obviously not a real one.
GUEST_IP = "203.0.113.11"


def _clean(conn):
    conn.execute("""DELETE FROM booking_extras WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_email = ?)""", (EMAIL,))
    conn.execute("DELETE FROM tasks WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_email = ?)", (EMAIL,))
    conn.execute("DELETE FROM bookings WHERE guest_email = ?", (EMAIL,))
    conn.execute("DELETE FROM guests WHERE email = ?", (EMAIL,))
    conn.execute("DELETE FROM guest_messages WHERE to_address = ?", (EMAIL,))
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()


def run():
    s = Suite("the whole way through")
    conn = db()
    _clean(conn)
    room = ensure_room(min_occupancy=2)
    oc, _ec, _owner, _emp = clients()
    guest = m.app.test_client()          # no login, ever — this is a stranger
    guest.environ_base["REMOTE_ADDR"] = GUEST_IP

    # Asked, not counted. A date counted forward from today lands on whatever
    # the live catalogue holds, and this walk has to get through the front door
    # before it can test anything at all. Tuesday-to-Friday so a two-night
    # minimum cannot refuse it.
    arrive = m.house_today() + timedelta(days=200)
    arrive += timedelta(days=(1 - arrive.weekday()) % 7)
    leave = arrive + timedelta(days=3)

    letters = []
    was_send = m.send_email

    def catch(to, subject, body, **rest):
        letters.append({"to": to, "subject": subject, "body": body,
                        "html": rest.get("html") or "",
                        "ics": rest.get("ics_content") or ""})
        return True

    m.send_email = catch
    try:
        # ---- the starting line ------------------------------------------
        # Made through the page rather than by calling create_booking, because
        # a walk that starts with a row it inserted itself is not a walk. The
        # form's own field names are test_booking_journey's job and are not
        # re-checked here; this only has to arrive at a real booking the way a
        # real guest does.
        s.section("A stranger books a room off the page")
        rooms = guest.get("/book")
        s.check("the rooms page opens", rooms.status_code == 200, rooms)
        to_room = links_on(rooms.get_data(as_text=True), r"/book/\d+")
        s.check("and offers a room to open", bool(to_room),
                detail="a room the page will not link to is a room nobody can "
                       "book, whatever the catalogue says")
        if not to_room:
            conn.close()
            return s
        page = guest.get(to_room[0])
        forms = [f for f in forms_on(page.get_data(as_text=True))
                 if f["method"] == "post"
                 and any(x["name"] == "guest_email" for x in f["fields"])]
        s.check("the room page carries a booking form", bool(forms))
        if not forms:
            conn.close()
            return s
        form = forms[0]
        del letters[:]
        sent = guest.post(form["action"] or to_room[0], data=fill(form, {
            "arrival_date": arrive.isoformat(),
            "departure_date": leave.isoformat(),
            "guest_name": TAG + " Eleanor", "guest_email": EMAIL,
            "guest_phone": "+33 6 00 00 00 00",
            "adults": "2", "party_size": "2", "agree_terms": "on",
            "special_requests": "", "promo_code": "",
        }), follow_redirects=True)
        s.check("it takes the booking", sent.status_code == 200, sent)
        row = conn.execute("SELECT * FROM bookings WHERE guest_email = ? "
                           "ORDER BY id DESC LIMIT 1", (EMAIL,)).fetchone()
        s.check("and there is a stay to walk", row is not None,
                detail="; ".join(_harness.flashes(sent)[:2])
                       or "no booking row and the page said nothing")
        if row is None:
            _clean(conn)
            conn.close()
            return s

        # ---- the letter, which is all they hold -------------------------
        s.section("The letter is the only thing they are given")
        theirs = [l for l in letters if l["to"] == EMAIL]
        s.check("a letter goes to the guest", bool(theirs),
                detail="letters written: " + str([l["to"] for l in letters]))
        body = theirs[0]["body"] if theirs else ""
        s.check("naming the reference they will quote on the telephone",
                (row["reference_code"] or "") in body, detail=body[:120])
        # THE HINGE. Everything below is opened with what this finds and
        # nothing else. A letter that goes out with no link, or with a link
        # built from the wrong host, ends the journey here — and no per-step
        # test can see that, because every one of them looks the token up.
        links = re.findall(r"https?://[^\s<>\"']+", body)
        s.check("carrying an address they can follow",
                bool(links),
                detail="the letter is the only place a guest is ever given "
                       "their own address: " + body[:160])
        token = None
        for link in links:
            found = re.search(r"/(?:checkin|book/manage|book/confirmation)/"
                              r"([A-Za-z0-9_-]{8,})", link)
            if found:
                token = found.group(1)
                break
        s.check("pointing at their own booking", token is not None,
                detail="links found: " + str(links[:4]))
        # ABSOLUTE, not a path. Four other letter-writing sites in this app
        # build their link as f"{PUBLIC_BASE_URL or ''}/booking/..." — with the
        # variable unset that produces "/booking/abc", which is not a link at
        # all in a mail client, just underlined text that does nothing. Checked
        # here rather than for a hostname, because the host itself is whatever
        # the house is served from and a test has no business asserting it.
        s.check("as a whole address rather than a path",
                all(re.match(r"https?://[^/]+/", l) for l in links),
                detail=str(links[:2]))
        # And on the same house they were just browsing. A letter that sends a
        # guest to a different host mid-journey is one they should not trust,
        # and one that will not carry their token.
        s.check("on the same site they booked from",
                all("//localhost" in l for l in links),
                detail="booked at localhost, letter says: "
                       + str(links[:2]))
        if not token:
            _clean(conn)
            conn.close()
            return s

        # ---- following it -----------------------------------------------
        s.section("They follow it")
        seen = guest.get("/book/manage/%s" % token)
        s.check("their page opens with no login", seen.status_code == 200, seen)
        mine = seen.get_data(as_text=True)
        s.check("and it is their stay, not somebody else's",
                (row["reference_code"] or "") in mine and TAG in mine)
        # This page shows one person's stay to anybody holding the link, and
        # robots.txt does not cover a URL that leaks through a forward.
        s.check("and it is not indexable", "noindex" in mine)
        s.check("the short check-in address reaches the same page",
                guest.get("/checkin/%s" % token,
                          follow_redirects=True).status_code == 200,
                detail="the letters use it, so it has to work")

        # ---- telling the house when they arrive -------------------------
        s.section("They say when they are coming")
        guest.post("/book/manage/%s" % token,
                   data={"action": "arrival_time",
                         "estimated_arrival_time": "around 6pm"},
                   follow_redirects=True)
        s.check("the house has the arrival time",
                (conn.execute("SELECT estimated_arrival_time FROM bookings "
                              "WHERE id = ?", (row["id"],)).fetchone()
                 ["estimated_arrival_time"] or "") == "around 6pm")
        s.check("and it is on the page when they come back",
                "around 6pm" in guest.get("/book/manage/%s" % token)
                .get_data(as_text=True),
                detail="a time the guest cannot see again is a time they "
                       "cannot correct")

        # ---- asking for something ---------------------------------------
        s.section("They ask for something, and somebody has to see it")
        asked = guest.post("/book/manage/%s" % token,
                           data={"action": "request",
                                 "message": TAG + " a cot, and no feathers"},
                           follow_redirects=True)
        s.check("they are told it was sent",
                any("take care" in f.lower() or "sent" in f.lower()
                    for f in _harness.flashes(asked)),
                detail=str(_harness.flashes(asked)))
        task = conn.execute(
            """SELECT * FROM tasks WHERE booking_id = ?
               AND origin = 'guest_request' ORDER BY id DESC LIMIT 1""",
            (row["id"],)).fetchone()
        s.check("it becomes a job on the house's list", task is not None,
                detail="a request that lands nowhere is a request nobody acts "
                       "on, and the guest has been told it is in hand")
        s.check("with the guest's own words in it",
                task and "no feathers" in (task["notes"] or ""),
                detail=repr(task["notes"] if task else None))
        # AND SOMEBODY IS TOLD NOW. The task is due on the arrival date, which
        # for this stay is two hundred days out, so the week sheet is the wrong
        # place to look for it and will rightly not show it until the week it
        # matters. The notification is what reaches the owner today.
        s.check("and the owner is told about it now",
                task and conn.execute(
                    """SELECT COUNT(*) AS n FROM notifications
                       WHERE kind = 'guest_request' AND related_task_id = ?""",
                    (task["id"],)).fetchone()["n"] == 1,
                detail="a job on a list nobody is pointed at is how a promise "
                       "made to a guest is found weeks later")
        # And it is on the sheet for the week it is due, which is the house's
        # rule that anything becoming a task reaches the calendar. Anchored on
        # the arrival date rather than today, because that is when somebody
        # has to have done it.
        s.check("and on the calendar for the week they arrive",
                task and str(task["title"]) in oc.get(
                    "/admin/tasks?view=week&date=" + str(row["arrival_date"])[:10]
                ).get_data(as_text=True),
                detail="due %s" % (task["due_date"] if task else None))

        # ---- adding something, which moves the bill ---------------------
        s.section("They add something to the stay")
        offered = re.findall(r'name="extra_id" value="(\d+)"',
                             guest.get("/book/manage/%s" % token)
                             .get_data(as_text=True))
        s.check("the page offers something to add", bool(offered),
                detail="the account page invites a guest to ask for a "
                       "transfer, so the offer has to be here")
        before = m.booking_bill(conn, row["id"])["total"]
        added = guest.post("/book/manage/%s" % token,
                           data={"action": "add_extra",
                                 "extra_id": offered[0] if offered else "0",
                                 "quantity": "1"}, follow_redirects=True)
        told = _harness.flashes(added)
        after = m.booking_bill(conn, row["id"])["total"]
        # Either answer is right — some things need notice and refusing is
        # better than promising. What is not right is silence, or a bill that
        # moved without the guest being told, or one that did not move after
        # they were told it had.
        s.check("and says what happened either way", bool(told),
                detail="silence is the one wrong answer here")
        s.check("the bill moves if it was added, and not if it was not",
                (after > before + 0.004) ==
                any("added" in t.lower() or "booked" in t.lower() for t in told),
                detail="%.2f -> %.2f, said: %s" % (before, after, told))

        # ---- the two money documents ------------------------------------
        s.section("What they owe, in the two places they can read it")
        bill_page = guest.get("/booking/%s/statement" % token)
        s.check("the statement opens", bill_page.status_code == 200, bill_page)
        statement = bill_page.get_data(as_text=True)
        s.check("and it is not indexable either", "noindex" in statement)
        fresh = conn.execute("SELECT * FROM bookings WHERE id = ?",
                             (row["id"],)).fetchone()
        bill = m.booking_bill(conn, row["id"])
        with m.app.test_request_context("/"):
            doc = m.guest_statement(conn, fresh)
        # TWO FUNCTIONS, ONE STAY. booking_bill recomputes the room line from
        # what was stamped and reads the discount; guest_statement reads the
        # price that was charged. Everything that asks the guest for money
        # reads the first — their page, the balance chase, the debtors list,
        # the Pay button — and the second is the document they keep for their
        # VAT. Two documents disagreeing about what somebody owes is the fault
        # test_walk_in was written for, and this is the only place both are
        # read for the same stay after a guest has changed it.
        s.check("the bill and the statement agree on the total",
                abs(bill["total"] - doc["total"]) < 0.005,
                detail="bill %.2f, statement %.2f" % (bill["total"], doc["total"]))
        s.check("and on what is still outstanding",
                abs(bill["owed"] - max(doc["balance"], 0.0)) < 0.005,
                detail="bill %.2f, statement %.2f"
                       % (bill["owed"], doc["balance"]))
        # THE ROW THE GUESTS WHO OWE MONEY NEED. It used to sit inside the
        # template's {% if s.paid %}, which was invisible while every stay was
        # reported as paid in full -- the block always drew and always said
        # "Settled". With an honest figure an unpaid stay skipped it, and the
        # bill went out with a total and no bottom line at all.
        s.check("and what is still to pay is on it",
                ("%.2f" % doc["balance"]) in statement.replace(",", "")
                or (doc["balance"] <= 0.005 and "Settled" in statement),
                detail="balance %.2f" % doc["balance"])
        s.check("the figure on the page is that figure",
                ("%.2f" % doc["total"]) in statement.replace(",", "")
                or ("%.2f" % doc["total"]).replace(".", ",") in statement,
                detail="page should carry %.2f" % doc["total"])
        s.check("their own page quotes the same amount owing",
                ("%.2f" % bill["owed"]) in
                guest.get("/book/manage/%s" % token).get_data(as_text=True)
                .replace(",", "") or bill["owed"] <= 0.005,
                detail="owed %.2f" % bill["owed"])

        # ---- pressing pay with no card processor ------------------------
        s.section("They press pay")
        # Stripe is pinned off in the harness and moving real money is the
        # owner's decision, not a test's. So what is proved here is the
        # answer a guest gets when the house has not turned card payments on:
        # a sentence and their own page back. Not an error page, and not a
        # blank one — neither of those is the guest's mistake.
        pressed = guest.get("/book/pay/%s" % token, follow_redirects=True)
        s.check("they are not shown an error page",
                pressed.status_code == 200, pressed)
        s.check("they are told why, in a sentence",
                any("contact the ch" in f.lower() or "nothing to pay" in f.lower()
                    for f in _harness.flashes(pressed)),
                detail=str(_harness.flashes(pressed)))
        s.check("and they land back on their own booking",
                (row["reference_code"] or "") in pressed.get_data(as_text=True),
                detail="a dead end at the card page loses the guest entirely")

        # ---- what is on while they are here -----------------------------
        s.section("They look at what is on")
        itin = guest.get("/book/manage/%s/itinerary" % token)
        s.check("the itinerary opens with the same token",
                itin.status_code == 200, itin)

        # ---- and the house writes again ---------------------------------
        s.section("The house confirms, and writes again")
        del letters[:]
        ok = oc.post("/admin/bookings/%d/confirm" % row["id"],
                     follow_redirects=True)
        s.check("the owner can confirm it", ok.status_code == 200, ok)
        s.check("and it is confirmed",
                (conn.execute("SELECT status FROM bookings WHERE id = ?",
                              (row["id"],)).fetchone()["status"]) == "confirmed")
        second = [l for l in letters if l["to"] == EMAIL]
        s.check("a second letter goes to the guest", bool(second),
                detail=str([l["to"] for l in letters]))
        if second:
            s.check("drawn, not only written",
                    second[0]["html"].lstrip().lower().startswith("<!doctype"),
                    detail="the confirmation is the most opened letter this "
                           "house sends")
            s.check("with the dates attached for their calendar",
                    "BEGIN:VCALENDAR" in second[0]["ics"],
                    detail="a guest who cannot add it to a calendar writes it "
                           "on paper, and then arrives on the wrong day")
            # A guest keeps the first letter. If the second sends them
            # somewhere else, they have two addresses and no way to know which
            # is theirs.
            s.check("carrying the same address as the first",
                    token in second[0]["body"],
                    detail="the second letter must not hand them a different "
                           "way in")
        s.check("and that address still opens their page",
                guest.get("/book/manage/%s" % token).status_code == 200,
                detail="confirming must not invalidate the link the guest is "
                       "already holding")
    finally:
        m.send_email = was_send
        _clean(conn)
        conn.close()
    return s
