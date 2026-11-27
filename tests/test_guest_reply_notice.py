"""A notification when a guest replies, and nothing else (brief of 27 Nov 2026).

The owner, of the "Waiting for a reply" panel put on the owner home the day
before: "I don't want all the emails on there, it's not needed" -- and what he
does want is "just a notification of guest replies".

  - An email reaching any inbox the house reads, from somebody the app knows
    as a guest -- a booking not yet over, an enquiry still open -- is ONE
    notification to the owner: "[Name] replied", what they are booked for and
    when, and the subject, opening the email in Outlook.
  - One email in two inboxes is one notification; the same email read again
    by the next scan is not another; a second email is a second.
  - Nobody else: a shop's receipt, a stranger, a guest whose stay is over, a
    guest's newsletter, the house writing to itself -- nothing at all.
  - The first scan announces nothing already in the inboxes, and nothing that
    arrived while the switch was off is announced later.
  - A Booking.com guest's message is one too, opening the stay's page, and the
    owner is not told twice when Booking.com's mail is the owner's to handle.
  - The switch is "Tell me when a guest replies" on Automation, on by default.
  - The owner home holds nothing from the mailboxes.

Nothing reaches Microsoft: tests/_graph_double.py plays its part.
"""
import re
from datetime import timedelta

from _graph_double import FakeGraph, message
from _harness import Suite, clients, db, keep_rows
import _harness

m = _harness.m
TAG = "ZZGR"
BOX_A = "zzgr-reservations@zzhouse.invalid"
BOX_B = "zzgr-experience@zzhouse.invalid"
ANNA = "zzgr.anna@guest.example"         # a stay, under an occasion
WREN = "zzgr.wren@guest.example"         # a wedding enquiry, next September
ROSA = "zzgr.rosa@guest.example"         # a table at the restaurant
FINN = "zzgr.finn@guest.example"         # a stay that ended yesterday
SHOP = "factures@zzgr-shop.example"      # a receipt
STRANGER = "zzgr.stranger@gmail.example"
NUM_ON, NUM_OVER = "ZZGR0001", "ZZGR0002"


def _at(seconds):
    """A receivedDateTime, `seconds` from now (negative is the past)."""
    return (m.datetime.now(m.timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scan(conn):
    with m.app.test_request_context("/"):
        return m.run_email_inbox_scan_job(conn)


def _owner_id(conn):
    return conn.execute("SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").fetchone()["id"]


def _replied(conn, name=None):
    """The owner's guest-reply notifications this suite caused, oldest first:
    every subject here carries the tag, so a notice for a sender the app does
    not know -- titled by their address's own name -- is found too."""
    rows = conn.execute("SELECT * FROM notifications WHERE kind = 'guest_reply' "
                        "AND (title LIKE ? OR body LIKE ?) ORDER BY id",
                        (f"%{TAG}%", f"%{TAG}%")).fetchall()
    return [r for r in rows if name is None or r["title"].startswith(name)]


def _set(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _cleanup(conn):
    conn.execute("DELETE FROM notifications WHERE title LIKE ? OR body LIKE ?", (f"%{TAG}%", f"%{TAG}%"))
    conn.execute("DELETE FROM guest_reply_notices WHERE message_key LIKE ?", (f"<{TAG.lower()}-%",))
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM stay_occasions WHERE slug LIKE ?", (f"{TAG.lower()}-%",))
    conn.execute("DELETE FROM ota_mail WHERE source_id LIKE ?", (f"<{TAG.lower()}-%",))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox IN (?, ?)", (BOX_A, BOX_B))
    conn.commit()


def _seed(conn):
    """Who the app knows, and what each has on."""
    today = m.house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    room = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    # Beyond any occasion the house has on sale, so the one made below is the
    # only one these nights fall in; and in another year, so the year is said.
    arrive, leave = today + timedelta(days=430), today + timedelta(days=434)

    def stay(code, email, name, first, last, status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
                   arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 600, ?)""",
            (room["id"], f"{TAG}{code}", _harness.secrets_token(), name, email,
             first.isoformat(), last.isoformat(), status, now))

    stay("ANNA", ANNA, f"{TAG} Anna Fischer", arrive, leave)
    stay("FINN", FINN, f"{TAG} Finn Hale", today - timedelta(days=5), today - timedelta(days=1))
    # Somebody at the house made a booking under one of its own inboxes: mail
    # from that inbox is still the house writing to itself.
    stay("HOUSE", BOX_B, f"{TAG} House Test", arrive, leave)
    # The stay's nights are sold under an occasion, so that is what it is called.
    conn.execute(
        """INSERT INTO stay_occasions (name, slug, first_night, last_night, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'live', ?, ?)""",
        (f"{TAG} Noël at Gudanes", f"{TAG.lower()}-noel", arrive.isoformat(),
         (arrive + timedelta(days=3)).isoformat(), now, now))
    wedding = m.date(today.year + 1, 9, 12)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, guest_count, status, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, 80, 'new', ?)""",
        (f"{TAG}WREN", _harness.secrets_token(), f"{TAG} Wren Adair", WREN, wedding.isoformat(), now))
    dinner = today + timedelta(days=6)
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
               party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
        (f"{TAG}ROSA", _harness.secrets_token(), f"{TAG} Rosa Lind", ROSA, dinner.isoformat(), now))
    conn.commit()
    return {"room": room, "arrive": arrive, "leave": leave, "wedding": wedding, "dinner": dinner}


def run():
    s = Suite("a notification when a guest replies")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    keep = [keep_rows(conn, "app_settings", "key IN (?, ?, ?, ?, ?)",
                      (m.GUEST_REPLY_SINCE_KEY, m.GUEST_REPLY_SWITCH, m.INBOX_SCAN_STATE_KEY,
                       m.INBOX_SENT_EVERYWHERE_KEY, "booking_com_mailbox"))]
    # Booking.com's mail is the owner's to handle in this suite.
    conn.execute("DELETE FROM app_settings WHERE key = 'booking_com_mailbox'")
    stamp = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute("""INSERT INTO mailbox_routing (mailbox, label, active, created_at)
                    VALUES (?, 'ZZGR Reservations', 1, ?), (?, 'ZZGR Experience', 1, ?)""",
                 (BOX_A, stamp, BOX_B, stamp))
    conn.commit()
    try:
        seeded = _seed(conn)
        _inbox(s, conn, seeded)
        _booking_com(s, conn, seeded)
        _pages(s, oc, conn)
    finally:
        _cleanup(conn)
        for put_back in keep:
            put_back()
        conn.close()
    return s


def _inbox(s, conn, seeded):
    _set(conn, m.GUEST_REPLY_SWITCH, "1")
    _set(conn, m.GUEST_REPLY_SINCE_KEY, None)

    def mail(key, frm, subject, when, to=(BOX_A,), headers=None):
        return message(f"{TAG}-{key}", frm=frm, to=list(to), subject=subject, when=when,
                       conversation=f"{TAG}-c-{key}", internet_id=f"<{TAG.lower()}-{key}@guest.example>",
                       headers=headers)

    history = mail("history", ANNA, f"{TAG} Our dates in December", _at(-3 * 86400))

    s.section("The first scan announces nothing already in the inboxes")
    with FakeGraph(mailboxes=[BOX_A, BOX_B]) as fake:
        fake.inbox = {BOX_A: [history], BOX_B: []}
        fake.sent = {BOX_A: [], BOX_B: []}
        report = _scan(conn)
        s.check("a guest's email from three days ago is not news the day this comes in",
                not _replied(conn), detail=f"{[r['title'] for r in _replied(conn)]}; {report}")
        since = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                             (m.GUEST_REPLY_SINCE_KEY,)).fetchone()
        s.check("and from now on, new mail counts", since is not None and since["value"])
    # As though that first scan ran an hour ago.
    _set(conn, m.GUEST_REPLY_SINCE_KEY,
         (m.datetime.now(m.timezone.utc) - timedelta(hours=1)).isoformat())

    anna = mail("anna-1", ANNA, f"{TAG} Re: Arriving by train", _at(-600))
    # The same email, delivered to the second inbox too: its own Graph id there,
    # the same Message-ID.
    anna_copy = dict(anna, id=f"{TAG}-anna-1-copy", webLink="https://outlook.office.com/mail/copy")
    wren = mail("wren-1", WREN, f"{TAG} Re: Wedding in September", _at(-500), to=(BOX_B,))
    rosa = mail("rosa-1", ROSA, f"{TAG} Re: Our table", _at(-450))
    rosa_news = mail("rosa-news", ROSA, f"{TAG} Rosa's studio: November news", _at(-440),
                     headers={"List-Unsubscribe": "<mailto:unsubscribe@guest.example>"})
    receipt = mail("receipt", SHOP, f"{TAG} Votre facture n°4471", _at(-400))
    stranger = mail("stranger", STRANGER, f"{TAG} Re: Your rooms", _at(-350))
    finn = mail("finn", FINN, f"{TAG} Re: Thank you for a lovely stay", _at(-300))
    house = mail("house", BOX_B, f"{TAG} Re: Can you take this one?", _at(-250))

    s.section("A guest with a booking writes: the owner hears it once")
    with FakeGraph(mailboxes=[BOX_A, BOX_B]) as fake:
        fake.inbox = {BOX_A: [history, anna, rosa, rosa_news, receipt, stranger, finn, house],
                      BOX_B: [anna_copy, wren]}
        fake.sent = {BOX_A: [], BOX_B: []}
        report = _scan(conn)
        told = _replied(conn)
        titles = [r["title"] for r in told]
        annas = _replied(conn, f"{TAG} Anna Fischer")
        s.check("the guest with a stay is named: \"Anna Fischer replied\"",
                [r["title"] for r in annas] == [f"{TAG} Anna Fischer replied"], detail=f"{titles}; {report}")
        s.check("to the owner", annas and annas[0]["user_id"] == _owner_id(conn))
        body = annas[0]["body"] if annas else ""
        span = m._when_words(seeded["arrive"].isoformat(), seeded["leave"].isoformat())
        s.check("with what she is booked for -- the occasion her nights are sold under -- and when",
                body.startswith(f"{TAG} Noël at Gudanes, {span}"), detail=body)
        s.check("and the email's subject", f"{TAG} Re: Arriving by train" in body, detail=body)
        s.check("opening the email in Outlook",
                annas and annas[0]["link"] in (anna["webLink"], anna_copy["webLink"]),
                detail=annas[0]["link"] if annas else "")
        s.check("one email in two inboxes is one notification", len(annas) == 1, detail=str(titles))
        wrens = _replied(conn, f"{TAG} Wren Adair")
        s.check("an open wedding enquiry counts: \"Wedding enquiry\", the month and the year",
                len(wrens) == 1 and wrens[0]["body"].startswith(
                    f"Wedding enquiry, {seeded['wedding']:%b %Y} · "), detail=str([dict(r) for r in wrens]))
        rosas = _replied(conn, f"{TAG} Rosa Lind")
        s.check("so does a table at the restaurant",
                len(rosas) == 1 and rosas[0]["body"].startswith("Dinner for 2, "),
                detail=str([r["body"] for r in rosas]))
        s.check("and her newsletter does not: a mailing is never a guest replying",
                all("November news" not in (r["body"] or "") for r in rosas), detail=str(titles))
        s.check("a shop's receipt is nothing",
                not any("facture" in (r["body"] or "") for r in told), detail=str(titles))
        s.check("a stranger writing to reservations@ is nothing",
                not any("Your rooms" in (r["body"] or "") for r in told), detail=str(titles))
        s.check("a guest whose stay ended yesterday is nothing",
                not any("a lovely stay" in (r["body"] or "") for r in told), detail=str(titles))
        s.check("the house writing to itself is nothing, even under a booking's address",
                not any("take this one" in (r["body"] or "") for r in told), detail=str(titles))
        s.check("three told, and nobody else", len(told) == 3, detail=str(titles))
        s.check("the scan says so", "told you of 3 guest replies" in report, detail=report)
        s.check("an email from before the first scan is still not news",
                not any("Our dates in December" in (r["body"] or "") for r in told))

        s.section("Read again by the next scan, nothing new; a second email is a second")
        _scan(conn)
        s.check("the next scan tells nobody again", len(_replied(conn)) == 3,
                detail=str([r["title"] for r in _replied(conn)]))
        anna_2 = mail("anna-2", ANNA, f"{TAG} Re: Arriving by train, and a dog", _at(-60))
        fake.inbox[BOX_A].append(anna_2)
        _scan(conn)
        s.check("a guest who writes twice is told twice",
                len(_replied(conn, f"{TAG} Anna Fischer")) == 2,
                detail=str([r["title"] for r in _replied(conn)]))

        s.section("The switch off stops it, and what came while off stays untold")
        _set(conn, m.GUEST_REPLY_SWITCH, "0")
        while_off = mail("anna-off", ANNA, f"{TAG} Re: While it was off", _at(-30))
        fake.inbox[BOX_A].append(while_off)
        _scan(conn)
        s.check("off, a guest's email is not a notification",
                len(_replied(conn, f"{TAG} Anna Fischer")) == 2)
        _set(conn, m.GUEST_REPLY_SWITCH, "1")
        _scan(conn)
        s.check("switched on again, what arrived while it was off is not announced late",
                not any("While it was off" in (r["body"] or "") for r in _replied(conn)))
        after = mail("anna-after", ANNA, f"{TAG} Re: Back on", _at(5))
        fake.inbox[BOX_A].append(after)
        _scan(conn)
        s.check("and the next new one is",
                any("Back on" in (r["body"] or "") for r in _replied(conn, f"{TAG} Anna Fischer")),
                detail=str([r["body"] for r in _replied(conn)]))

    s.section("Inbox Flags is as it was")
    flagged = {r["subject"] for r in conn.execute(
        "SELECT subject FROM email_flags WHERE subject LIKE ? AND status = 'open'", (f"%{TAG}%",))}
    s.check("the only flag is the three-day-old email nobody answered, as it always would be",
            flagged == {f"{TAG} Our dates in December"}, detail=str(flagged))


def _booking_com(s, conn, seeded):
    today = m.house_today()
    now = m.datetime.now(m.timezone.utc)
    room = seeded["room"]

    def reservation(number, name, first, last):
        conn.execute(
            """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
                   arrival_date, departure_date, room_id, guests, first_seen_at, updated_at)
               VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, ?, 2, ?, ?)""",
            (number, name, first.isoformat(), last.isoformat(), room["id"], now.isoformat(),
             now.isoformat()))
        conn.commit()
        return conn.execute("SELECT id FROM ota_reservations WHERE reservation_number = ?",
                            (number,)).fetchone()["id"]

    def guest_message(key, number, name, text):
        got_at = (now - timedelta(minutes=5)).isoformat()
        cur = conn.execute(
            """INSERT INTO ota_mail (channel, source, source_id, mailbox, received_at, from_address,
                   from_name, subject, kind, reservation_number, guest_name, message_text,
                   from_booking_com, created_at)
               VALUES ('booking.com', 'test', ?, 'zzgr-bcom@zzhouse.invalid', ?, 'noreply@booking.com',
                       'Booking.com', ?, 'message', ?, ?, ?, 1, ?)""",
            (f"<{TAG.lower()}-{key}@booking.example>", got_at, f"We received this message from {name}",
             number, name, text, got_at))
        conn.commit()
        with m.app.test_request_context("/"):
            m._ota_notify(conn, cur.lastrowid, {"kind": "message", "guest_name": name,
                                                "message_text": text}, got_at)
        return cur.lastrowid

    def has_written(mail_id):
        return conn.execute("SELECT * FROM notifications WHERE kind = 'booking_com' AND link LIKE ?",
                            (f"%#m{mail_id}",)).fetchall()

    s.section("A Booking.com guest's message: the owner hears it once, opening the stay")
    on_id = reservation(NUM_ON, f"{TAG} Bea Booker", today + timedelta(days=10), today + timedelta(days=13))
    mail_id = guest_message("bcom-on", NUM_ON, f"{TAG} Bea Booker", "Could we bring a small dog with us?")
    beas = _replied(conn, f"{TAG} Bea Booker")
    s.check("\"Bea Booker replied\"", len(beas) == 1, detail=str([dict(r) for r in beas]))
    body = beas[0]["body"] if beas else ""
    s.check("with the room, Booking.com and the dates",
            body.startswith(f"{room['name']} through Booking.com, "), detail=body)
    s.check("and what she wrote, not Booking.com's own subject",
            "Could we bring a small dog" in body and "We received this message" not in body, detail=body)
    s.check("opening the stay's page", beas and beas[0]["link"] == f"/admin/channel-stays/{on_id}",
            detail=beas[0]["link"] if beas else "")
    s.check("and not a second time as \"has written\": Booking.com's mail is the owner's here",
            not has_written(mail_id), detail=str([r["title"] for r in has_written(mail_id)]))

    s.section("A Booking.com stay that is over, and the switch off")
    reservation(NUM_OVER, f"{TAG} Olle Over", today - timedelta(days=6), today - timedelta(days=2))
    over_id = guest_message("bcom-over", NUM_OVER, f"{TAG} Olle Over", "We left a scarf in the room.")
    s.check("a guest whose Booking.com stay is over is no guest reply",
            not _replied(conn, f"{TAG} Olle Over"))
    s.check("though Booking.com's own notice still goes, as it always did", len(has_written(over_id)) == 1)
    _set(conn, m.GUEST_REPLY_SWITCH, "0")
    off_id = guest_message("bcom-off", NUM_ON, f"{TAG} Bea Booker", "And a second question.")
    s.check("off, a Booking.com guest's message is no guest reply",
            len(_replied(conn, f"{TAG} Bea Booker")) == 1)
    s.check("and Booking.com's own notice goes instead", len(has_written(off_id)) == 1)
    _set(conn, m.GUEST_REPLY_SWITCH, "1")

    s.section("The same email met twice is told once")
    s.check("an email the scan already announced is not announced by the Booking.com reader",
            m.claim_guest_reply(conn, "<zzgr-claim@x>", "inbox", now.isoformat())
            and not m.claim_guest_reply(conn, "<zzgr-claim@x>", "booking.com", now.isoformat()))
    conn.execute("DELETE FROM guest_reply_notices WHERE message_key = '<zzgr-claim@x>'")
    conn.commit()


def _pages(s, oc, conn):
    s.section("The owner home holds nothing from the mailboxes")
    stamp = (m.datetime.now(m.timezone.utc) - timedelta(days=3)).isoformat()
    conn.execute(
        """INSERT INTO email_flags (graph_message_id, from_name, from_address, subject, preview, web_link,
               received_at, unanswered, status, mailbox, sender_kind, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'Bonjour', ?, ?, 1, 'open', ?, 'guest', ?, ?)""",
        (f"{TAG}-flag", f"{TAG} Lou Steiner", "zzgr.lou@guest.example",
         f"{TAG} Wedding enquiry for late August", "https://outlook.office.com/mail/zzgr-flag",
         stamp, BOX_A, stamp, stamp))
    conn.commit()
    page = oc.get("/").get_data(as_text=True)
    s.check("the owner home opens", "oh-h2" in page, detail=page[:200])
    s.check("with no \"Waiting for a reply\" panel", "Waiting for a reply" not in page
            and 'id="replies"' not in page and "data-replies" not in page)
    s.check("and no guest's email on it: not the sender, the subject or an Outlook button",
            f"{TAG} Lou Steiner" not in page and "Wedding enquiry for late August" not in page
            and "Open in Outlook" not in page)
    s.check("nor a count of them in the greeting",
            not re.search(r"\d+ guests? (is|are) waiting for a reply", page))
    s.check("and the panel's code is gone, not just hidden",
            not hasattr(m, "owner_home_replies"))

    s.section("The switch, and the bell")
    body = oc.get("/admin/automation").get_data(as_text=True)
    s.check("Automation has \"Tell me when a guest replies\"",
            "Tell me when a guest replies" in body
            and f'name="{m.GUEST_REPLY_SWITCH}"' in body)
    s.check("on by default", m.AUTOMATION_SETTING_DEFAULTS.get(m.GUEST_REPLY_SWITCH) == "1")
    s.check("the bell names the kind", m.notification_kind_label("guest_reply") == "Guest replies")
    page = oc.get("/notifications").get_data(as_text=True)
    link = "https://outlook.office.com/mail/"
    at = page.find(f'href="{link}')
    s.check("on the notifications page, a guest's email opens in Outlook in a tab of its own",
            at > 0 and 'target="_blank"' in page[at:at + 200], detail=page[at:at + 200] if at > 0 else "")


if __name__ == "__main__":
    print(run().report())
