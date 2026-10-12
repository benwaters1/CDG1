"""Writing to everybody on one session, party or night, from the app.

WHAT IT REPLACES. "Email everyone" on a session opened Outlook with every
address in the blind copy. Nothing in the app saw it afterwards: no line on
anybody's history, no way to tell who was left out, and an address that had
asked not to be written to went in with the rest.

WHAT THIS PINS.
  - The count is shown before anything goes, and nothing goes on a page view
    or a preview.
  - A send whose list has moved since the count is refused.
  - Left out, and named with the reason: no address, asked not to be emailed,
    asked the house to stop using their details. One letter per address.
  - It is transactional: no unsubscribe line. Replies reach the inbox of the
    kind of group it is (workshops, rooms, restaurant).
  - Statements go only to whoever owes something, through the same function
    as the button on one registration or stay -- bulk is the single action
    done many times (CLAUDE.md), and what it did not do is named.
  - A campaign's replies, and a promo blast's, reach its own area's inbox.

send_email is stood in for throughout. Nothing here sends mail.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today, free_window, ensure_room
import _harness

m = _harness.m
TAG = "ZZGM"


class Recorder:
    """send_email, stood in for: every call kept, every one 'delivered'."""
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *args, **kw):
        self.calls.append({"to": to_address, "subject": subject, "body": body, **kw})
        return True


class _Mail:
    def __enter__(self):
        self.real = m.send_email
        self.rec = Recorder()
        m.send_email = self.rec
        return self.rec

    def __exit__(self, *exc):
        m.send_email = self.real
        return False


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute(f"DELETE FROM workshop_messages WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _session():
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active,
           sort_order, created_at, deposit_percent) VALUES (?, '', 2000, 30, 1, 95, ?, 30)""",
        (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()[0]
    start = house_today() + timedelta(days=60)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 30, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} s", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} s",)).fetchone()[0]
    conn.commit()
    conn.close()
    return sid


def _reg(sid, ref, *, email="auto", status="confirmed", do_not_email=0, paid=600.0, total=2000.0):
    conn = db()
    now = _harness.datetime_now()
    address = f"{TAG.lower()}{ref.lower()}@example.invalid" if email == "auto" else email
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           do_not_email) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 600, ?, ?)""",
        (sid, f"{TAG} {ref}", address, status, f"{TAG}{ref}", f"tok{TAG}{ref}", now, total,
         total - 600, do_not_email))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()[0]
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", paid, method="stripe")
        conn.commit()
    conn.close()
    return bid


def _party():
    """Two stays tied together: one owes, one is paid."""
    room = ensure_room()
    a = free_window(room["id"], 2, after_days=90)
    ids = []
    with m.app.test_request_context("/"):
        conn = db()
        full = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
        for n, (who, offset) in enumerate((("Owes", 0), ("Paid", 3))):
            arrive = a + timedelta(days=offset)
            ref, _tok = m.create_booking(
                conn, full, f"{TAG} {who}", f"{TAG.lower()}party{n}@example.invalid", "",
                arrive, arrive + timedelta(days=2), 2, "", [], confirm_now=True)
            conn.commit()
            ids.append(conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                                    (ref,)).fetchone()[0])
        conn.execute("INSERT INTO booking_parties (name, lead_booking_id, created_at) VALUES (?, ?, ?)",
                     (f"{TAG} Family", ids[0], _harness.datetime_now()))
        pid = conn.execute("SELECT id FROM booking_parties WHERE name = ?", (f"{TAG} Family",)).fetchone()[0]
        conn.execute(f"UPDATE bookings SET party_id = ? WHERE id IN ({ids[0]}, {ids[1]})", (pid,))
        bill = m.booking_bill(conn, ids[1])
        m.record_booking_payment(conn, ids[1], bill["owed"])
        conn.commit()
        conn.close()
    return pid, ids


def run():
    s = Suite("Writing to a whole session, party or night")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    sid = _session()
    a = _reg(sid, "A")                                  # owes, emailable
    b = _reg(sid, "B", status="pending")                # owes, emailable
    c = _reg(sid, "C", do_not_email=1)                  # asked not to be emailed
    d = _reg(sid, "D", email="")                        # no address
    _reg(sid, "E", status="cancelled")                  # not on the session
    f = _reg(sid, "F", email=f"{TAG.lower()}a@example.invalid")   # A's address
    g = _reg(sid, "G")                                  # asked us to stop using details
    h = _reg(sid, "H", paid=2000.0)                     # nothing owing
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at, restricted_at) VALUES (?, ?, ?, ?)",
                 (f"{TAG} G", f"{TAG.lower()}g@example.invalid", _harness.datetime_now(),
                  _harness.datetime_now()))
    conn.commit()
    conn.close()

    with m.app.test_request_context():
        email_url = m.url_for("workshop_session_email", session_id=sid)
        statements_url = m.url_for("workshop_session_statements", session_id=sid)
        register_url = m.url_for("admin_workshop_registrations", session_id=sid)

    s.section("The session's register offers both, from the app")
    page = oc.get(register_url).get_data(as_text=True)
    s.check("'Email everyone on this session' opens the app's page, not Outlook",
            email_url in page and "mailto:?bcc=" not in page)
    s.check("and 'Send statements to everyone' is beside it", statements_url in page)
    with m.app.test_request_context():
        one_url = m.url_for("send_workshop_registration_statement", booking_id=a)
    s.check("and each registration that owes has its own 'Send their statement'", one_url in page)

    s.section("The count comes first, and looking sends nothing")
    with _Mail() as rec:
        r = oc.get(email_url)
        html = r.get_data(as_text=True)
        s.check("the page opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
        s.check("it says how many it goes to: A, B and H (F shares A's address)",
                "Goes to 3 addresses" in html, detail=_harness.page_text(html)[:300]
                if hasattr(_harness, "page_text") else "")
        s.check("the one who asked not to be emailed is named, with why",
                f"{TAG} C: {m.GROUP_DO_NOT_EMAIL}" in html)
        s.check("so is the one with no address", f"{TAG} D: {m.GROUP_NO_EMAIL}" in html)
        s.check("and the one who asked us to stop using their details",
                f"{TAG} G: {m.GROUP_RESTRICTED}" in html)
        s.check("a cancelled registration is not on it at all", f"{TAG} E" not in html)
        s.check("and the second registration at one address is covered by one letter",
                "one letter also covers" in html and f"{TAG} F" in html)
        oc.post(email_url, data={"subject": "About {{workshop_title}}",
                                 "body": "Dear {{first_name}}, see you on {{dates}}.",
                                 "action": "preview"})
        s.check("neither the page nor a preview sends anything", not rec.calls,
                detail=f"{len(rec.calls)} sent")

    s.section("A list that has moved since the count is refused")
    with _Mail() as rec:
        r = oc.post(email_url, data={"subject": "Hello", "body": "Body", "action": "send",
                                     "expect": "4"}, follow_redirects=True)
        s.check("nothing goes", not rec.calls, detail=f"{len(rec.calls)} sent")
        s.check("and the page says the list has changed, with the count it is now",
                any("it is 3 now" in x for x in flashes(r)), detail=str(flashes(r)))

    s.section("Sent: one letter per address, filled in, transactional, to the atelier inbox")
    with _Mail() as rec:
        r = oc.post(email_url, data={"subject": "About {{workshop_title}}",
                                     "body": "Dear {{first_name}}, see you on {{dates}}.",
                                     "action": "send", "expect": "3"}, follow_redirects=True)
        to = sorted(c_["to"] for c_ in rec.calls)
        s.check("three letters, one per address",
                to == sorted([f"{TAG.lower()}a@example.invalid", f"{TAG.lower()}b@example.invalid",
                              f"{TAG.lower()}h@example.invalid"]), detail=str(to))
        s.check("each filled in for its reader",
                all(c_["subject"] == f"About {TAG} Atelier" and "{{" not in c_["body"]
                    for c_ in rec.calls), detail=str([c_["subject"] for c_ in rec.calls]))
        s.check("with no unsubscribe line: it is about what they booked",
                not any("nsubscribe" in c_["body"] for c_ in rec.calls))
        s.check("and replies reach the workshop inbox",
                all(c_.get("area") == "workshops" for c_ in rec.calls),
                detail=str({c_.get("area") for c_ in rec.calls}))
        s.check("filed against each registration",
                all(c_.get("about") and c_["about"][0] == "workshop" for c_ in rec.calls))
        said = " ".join(flashes(r))
        s.check("the message says what it did not do, by name",
                "Sent 3 of 6 letters" in said and f"{TAG} C" in said and f"{TAG} D" in said,
                detail=said)
    conn = db()
    logged = {row["workshop_booking_id"]: row["status"] for row in conn.execute(
        "SELECT * FROM workshop_messages WHERE workshop_booking_id IN (?, ?, ?)", (a, c, d))}
    conn.close()
    s.check("and each registration's history says what happened",
            logged.get(a) == "sent" and (logged.get(c) or "").startswith("skipped"),
            detail=str(logged))

    s.section("Statements: only whoever owes, through the one function")
    with _Mail() as rec:
        html = oc.get(statements_url).get_data(as_text=True)
        s.check("the count comes first", "4 statements to send" in html,
                detail="A, B, F and G owe and can be written to")
        s.check("whoever owes nothing is left out, and says so",
                f"{TAG} H: {m.GROUP_NOTHING_OWING}" in html)
        s.check("and nothing went by looking", not rec.calls)

        counted = []
        real = m.send_statement

        def counting(conn, member, **kw):
            counted.append(member["id"])
            return real(conn, member, **kw)

        m.send_statement = counting
        try:
            r = oc.post(statements_url, data={"action": "send", "expect": "4"},
                        follow_redirects=True)
            bulk = list(counted)
            oc.post(one_url, follow_redirects=True)
            single = counted[len(bulk):]
        finally:
            m.send_statement = real
        s.check("the session-wide send asks send_statement about every registration on it, "
                "the ones it refuses included",
                sorted(bulk) == sorted([a, b, c, d, f, g, h]), detail=str(bulk))
        s.check("and so does the button on one registration", single == [a], detail=str(single))
        statements = [c_ for c_ in rec.calls if "statement" in c_["subject"].lower()]
        s.check("each is the statement letter, with the statement beneath and what is owed",
                len(statements) == 5 and all("Still to pay" in c_["body"] for c_ in statements),
                detail=str([c_["subject"] for c_ in rec.calls]))
        s.check("from the atelier inbox", all(c_.get("area") == "workshops" for c_ in statements))
        said = " ".join(flashes(r))
        s.check("and the reasons for the rest are named",
                f"{TAG} H" in said and m.GROUP_NOTHING_OWING in said
                and f"{TAG} C" in said, detail=said)

    s.section("A party of rooms: the same two, from the party's page")
    pid, (owes, paid) = _party()
    with m.app.test_request_context():
        party_url = m.url_for("party_statement", party_id=pid)
        p_email = m.url_for("party_email", party_id=pid)
        p_statements = m.url_for("party_statements", party_id=pid)
    page = oc.get(party_url).get_data(as_text=True)
    s.check("the party's page offers both", p_email in page and p_statements in page)
    with _Mail() as rec:
        oc.post(p_email, data={"subject": "Your stay", "body": "See you, {{first_name}}.",
                               "action": "send", "expect": "2"})
        s.check("both stays are written to, from the rooms inbox",
                len(rec.calls) == 2 and all(c_.get("area") == "rooms" for c_ in rec.calls),
                detail=str([(c_["to"], c_.get("area")) for c_ in rec.calls]))
    with _Mail() as rec:
        html = oc.get(p_statements).get_data(as_text=True)
        s.check("statements would go only to the stay that owes", "1 statement to send" in html)
        oc.post(p_statements, data={"action": "send", "expect": "1"})
        s.check("and do: the room statement, to whoever pays",
                len(rec.calls) == 1 and rec.calls[0]["to"] == f"{TAG.lower()}party0@example.invalid",
                detail=str([c_["to"] for c_ in rec.calls]))
    with m.app.test_request_context():
        one_stay = m.url_for("send_stay_statement", booking_id=owes)
        paid_stay = m.url_for("send_stay_statement", booking_id=paid)
    s.check("each stay that owes has its own button on the party's page",
            one_stay in page and paid_stay not in page)
    with _Mail() as rec:
        r = oc.post(one_stay, follow_redirects=True)
        s.check("which sends that stay's statement, the same letter",
                len(rec.calls) == 1 and "statement" in rec.calls[0]["subject"].lower()
                and any("Statement sent" in x for x in flashes(r)),
                detail=f"{[c_['subject'] for c_ in rec.calls]} {flashes(r)}")
        r = oc.post(paid_stay, follow_redirects=True)
        s.check("and a stay that owes nothing is told so, not sent one",
                len(rec.calls) == 1 and any(m.GROUP_NOTHING_OWING in x for x in flashes(r)),
                detail=str(flashes(r)))

    s.section("A night's dinner: a word to everybody dining, from the restaurant inbox")
    day = (house_today() + timedelta(days=45)).isoformat()
    conn = db()
    for n, name in enumerate(("Uno", "Dos")):
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
               party_size, dinner_date, status, created_at) VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
            (f"{TAG}R{n}", f"tok{TAG}R{n}", f"{TAG} {name}", f"{TAG.lower()}r{n}@example.invalid",
             day, _harness.datetime_now()))
    conn.commit()
    conn.close()
    with m.app.test_request_context():
        d_email = m.url_for("dinner_email", day=day)
        tables = m.url_for("restaurant_tables", date=day)
    s.check("the table plan for that night offers it", d_email in oc.get(tables).get_data(as_text=True))
    with _Mail() as rec:
        oc.post(d_email, data={"subject": "Tonight", "body": "Hello {{first_name}}",
                               "action": "send", "expect": "2"})
        s.check("both tables are written to, from the restaurant inbox",
                len(rec.calls) == 2 and all(c_.get("area") == "restaurant" for c_ in rec.calls),
                detail=str([c_.get("area") for c_ in rec.calls]))

    s.section("Starting from a campaign template")
    conn = db()
    conn.execute(
        """INSERT INTO campaign_templates (name, area, subject, body, created_at)
           VALUES (?, 'workshops', 'Packing for {{workshop_title}}', 'Bring boots.', ?)""",
        (f"{TAG} Packing", _harness.datetime_now()))
    tid = conn.execute("SELECT id FROM campaign_templates WHERE name = ?", (f"{TAG} Packing",)).fetchone()[0]
    conn.commit()
    conn.close()
    html = oc.get(email_url + f"?template_id={tid}").get_data(as_text=True)
    s.check("picking one fills in its wording", "Packing for {{workshop_title}}" in html
            and "Bring boots." in html)

    s.section("A campaign's replies reach its own area's inbox")
    with _Mail() as rec:
        conn = db()
        m.send_campaign(conn, {"id": None, "name": "x", "area": "workshops",
                               "subject": "s", "body": "b"},
                        {f"{TAG.lower()}camp@example.invalid": "Camp"}, None)
        m.send_campaign(conn, {"id": None, "name": "y", "area": "general",
                               "subject": "s", "body": "b"},
                        {f"{TAG.lower()}camp2@example.invalid": "Camp"}, None)
        conn.close()
        areas = [c_.get("area") for c_ in rec.calls]
        s.check("a workshop campaign carries the workshop inbox", areas[:1] == ["workshops"],
                detail=str(areas))
        s.check("a general one has no inbox of its own", areas[1:] == [None], detail=str(areas))
        s.check("and a campaign still carries its unsubscribe line",
                all("nsubscribe" in c_["body"] for c_ in rec.calls))
    s.check("a promo code for ateliers replies to the atelier inbox",
            m.PROMO_REPLY_AREAS.get("workshop") == "workshops"
            and m.PROMO_REPLY_AREAS.get("all") is None)

    s.section("An employee sees none of it")
    for url in (email_url, statements_url, p_email, d_email):
        s.check(f"{url} is not theirs", ec.get(url).status_code in (302, 403))
    s.check("nor can they send one statement", ec.post(one_url).status_code in (302, 403))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
