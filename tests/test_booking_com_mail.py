"""Booking.com's email, read into the site.

Booking.com tells the house what happens by email -- a booking, a change, a
cancellation, a guest's message, a review, an invoice -- and all of it used to
land in somebody's inbox and stay there. The site knew a Booking.com stay only
as the dates the calendar sync had blocked: no name, no party, no price, and no
word that the guest had written and was waiting for an answer.

Booking.com does not publish the formats, so the samples here are laid out the
way its emails are -- a table of labels and values, the reservation number in
the subject -- and the reading is judged on what it must never get wrong rather
than on matching one template to the letter:

  EVERYTHING IS KEPT, even what is not understood. An email of a kind nobody
  has seen yet arrives on the page as "something else", whole.

  ONLY BOOKING.COM'S OWN MAIL IS BELIEVED. Anybody can write to the address and
  sign it Booking.com. That email is kept and marked; it raises no
  notification, waits for no answer, gets no reply from the house, and its
  links are shown but cannot be clicked.

  A SECURITY EMAIL'S TEXT IS NEVER KEPT. A sign-in code is a key, and the
  privacy notice says keys are never stored.

  THE BOOKING FOLLOWS THE NEWEST EMAIL, whatever order they are read in -- a
  backlog uploaded in a pile must not leave a cancelled stay "confirmed".

  A GUEST WAITING IS SEEN: on the owner's home, as a task that closes itself,
  and as a notification when the message arrives -- but not for a fortnight-old
  email read on the first morning.

  AND IT GOES WHEN THE NOTICE SAYS: two years after the stay.

Nothing here reaches Microsoft Graph or sends mail. graph_get, get_graph_token,
fetch_graph_messages and send_email are stood in for, and urlopen raises while
they are, so a stand-in that missed a path fails loudly instead of going out.
"""
import io
import json
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "zzbc"
MAILBOX = "bookingcom@test.invalid"
NUM_EN, NUM_FR, NUM_LATE, NUM_UP = "9876500001", "9876500002", "9876500003", "9876500004"
ROOM_LABEL = "ZZBC Mountain View Double"
EXTRANET = "https://admin.booking.com/hotel/hoteladmin/extranet_ng/manage/booking.html"
MESSAGES = "https://admin.booking.com/hotel/hoteladmin/extranet_ng/manage/messaging/inbox.html"
PHISH = "https://booking-com-verify.example/login"
ALIAS = "jane.doe.482@guest.booking.com"
FOOTER = "<p>Booking.com B.V., Herengracht 597, 1017 CE Amsterdam</p>"


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _mid(n):
    return f"<{TAG}-{n}@test.invalid>"


def new_booking_html(number=NUM_EN, guest="Jane Doe", arrive="Saturday, 10 October 2026",
                     leave="Monday, 12 October 2026", room=ROOM_LABEL, guests="2 adults",
                     total="€ 1,234.56", heading="You have a new booking!"):
    return f"""<html><head><style>td{{font-family:Arial}}</style><title>Booking.com</title></head>
<body><table>
<tr><td><h2>{heading}</h2></td></tr>
<tr><td>Booking number:</td><td>{number}</td></tr>
<tr><td>Guest name:</td><td>{guest}</td></tr>
<tr><td>Check-in:</td><td>{arrive}</td></tr>
<tr><td>Check-out:</td><td>{leave}</td></tr>
<tr><td>Room:</td><td>{room}</td></tr>
<tr><td>Number of guests:</td><td>{guests}</td></tr>
<tr><td>Total price:</td><td>{total}</td></tr>
</table>
<p><a href="{EXTRANET}">View this booking</a></p>
{FOOTER}
</body></html>"""


FR_BOOKING = f"""<html><body><table>
<tr><td>Numéro de réservation :</td><td>{NUM_FR}</td></tr>
<tr><td>Nom du client :</td><td>Pierre Martin</td></tr>
<tr><td>Arrivée :</td><td>samedi 17 octobre 2026</td></tr>
<tr><td>Départ :</td><td>lundi 19 octobre 2026</td></tr>
<tr><td>Chambre :</td><td>{ROOM_LABEL}</td></tr>
<tr><td>Nombre de personnes :</td><td>3</td></tr>
<tr><td>Prix total :</td><td>1 234,56 €</td></tr>
</table><p><a href="{EXTRANET}">Voir la réservation</a></p>{FOOTER}</body></html>"""


def message_html(guest="Jane Doe", lines=("Hello! We will arrive around 9pm, is that all right?",
                                          "And is there parking for two cars?"),
                 number=NUM_EN):
    paras = "".join(f"<p>{line}</p>" for line in lines)
    return f"""<html><body>
<p>Reservation number: {number}</p>
<p>{guest} wrote:</p>
{paras}
<p><a href="{MESSAGES}">Reply</a></p>
{FOOTER}
</body></html>"""


def _eml(subject, html="", text="", sender="Booking.com <noreply@booking.com>",
         reply_to="", when=None, message_id=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = MAILBOX
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when or datetime.now(timezone.utc))
    if reply_to:
        msg["Reply-To"] = reply_to
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(text or "This email is best viewed as HTML.")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


def _ingest(conn, n, subject, html="", text="", sender="noreply@booking.com",
            name="Booking.com", reply_to="", received_at=None):
    with m.app.test_request_context("/"):
        return m.ingest_booking_com_email(
            conn, source="test", source_id=_mid(n), received_at=received_at or _ago(minutes=5),
            from_address=sender, from_name=name, reply_to=reply_to, subject=subject,
            html=html, text=text, mailbox=MAILBOX)


def _row(conn, mail_id):
    return conn.execute("SELECT * FROM ota_mail WHERE id = ?", (mail_id,)).fetchone()


def _told(conn, mail_id):
    """The notifications raised for one email."""
    return conn.execute(
        "SELECT * FROM notifications WHERE kind = 'booking_com' AND link LIKE ?",
        (f"%#m{mail_id}",)).fetchall()


def _card_count(html):
    return len(re.findall(r'class="detail-card bc-mail', html))


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        """SELECT id FROM ota_mail WHERE source_id LIKE ? OR subject LIKE ?
                  OR reservation_number LIKE '98765%'""", (f"<{TAG}-%", "%ZZBC%"))]
    for i in ids:
        conn.execute("DELETE FROM notifications WHERE kind = 'booking_com' AND link LIKE ?",
                     (f"%#m{i}",))
        conn.execute("DELETE FROM ota_mail WHERE id = ?", (i,))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '98765%'")
    conn.execute("""DELETE FROM blocked_dates WHERE ical_source_id IN
                    (SELECT id FROM ical_sources WHERE label LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM ical_sources WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox LIKE '%@test.invalid'")
    for user in conn.execute("SELECT id FROM users WHERE email LIKE ?",
                             (f"{TAG}.%@example.invalid",)).fetchall():
        conn.execute("DELETE FROM notifications WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    conn.commit()


class _StandIns:
    """Graph and mail stood in for, with the network refusing underneath.

    Put back on the way out whatever happens, so a failing check cannot leave
    a later suite talking to a fake Graph -- or, worse, to a real one.
    """

    NAMES = ("graph_enabled", "get_graph_token", "graph_get", "fetch_graph_messages",
             "send_email", "urlopen", "MS_GRAPH_MAILBOXES")

    def __init__(self, **replacements):
        self.replacements = replacements

    def __enter__(self):
        self.saved = {n: getattr(m, n) for n in self.NAMES}
        m.urlopen = _harness._refuse("the network", "a stand-in missed a path")
        for name, value in self.replacements.items():
            setattr(m, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(m, name, value)
        return False


def run():
    s = Suite("Booking.com's email, read into the site")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    kept_setting = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'booking_com_mailbox'").fetchone()
    conn.execute("""INSERT INTO app_settings (key, value) VALUES ('booking_com_mailbox', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (MAILBOX,))
    room = conn.execute("SELECT id, name, channel_name FROM rooms WHERE active = 1 "
                        "ORDER BY id LIMIT 1").fetchone()
    conn.execute("UPDATE rooms SET channel_name = ? WHERE id = ?", (ROOM_LABEL, room["id"]))
    conn.commit()
    try:
        _run(s, oc, ec, owner, emp, conn, room)
    finally:
        conn.execute("UPDATE rooms SET channel_name = ? WHERE id = ?",
                     (room["channel_name"], room["id"]))
        if kept_setting:
            conn.execute("UPDATE app_settings SET value = ? WHERE key = 'booking_com_mailbox'",
                         (kept_setting["value"],))
        else:
            conn.execute("DELETE FROM app_settings WHERE key = 'booking_com_mailbox'")
        conn.commit()
        _cleanup(conn)
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        conn.commit()
        conn.close()
    return s


def _run(s, oc, ec, owner, emp, conn, room):
    # ------------------------------------------------------------------
    s.section("Whose mail it is: only Booking.com's own address, only its own links")
    for address, want in (("noreply@booking.com", True), (ALIAS, True),
                          ("booking.com@evil.example", False), ("x@notbooking.com", False),
                          ("x@booking.com.evil.example", False), ("", False)):
        s.check(f"{address or 'no address'} is {'' if want else 'not '}Booking.com",
                m.is_booking_com_address(address) is want)
    for href, want in ((EXTRANET, True), ("https://booking.com/", True),
                       ("https://booking.com.evil.example/x", False),
                       ("https://evil.example/?next=booking.com", False),
                       ("javascript:alert(1)", False)):
        s.check(f"a link to {href[:40]} is {'' if want else 'not '}safe to open",
                m.is_booking_com_link(href) is want)

    text, links = m.mail_html_to_text(new_booking_html())
    s.check("an email's HTML is read as the text a person sees",
            "Guest name: Jane Doe" in text and "Check-in: Saturday, 10 October 2026" in text,
            detail=text[:200])
    s.check("without its styles or its title", "font-family" not in text, detail=text[:120])
    s.check("and its links are kept, with their words",
            {"text": "View this booking", "href": EXTRANET} in links, detail=str(links))

    # ------------------------------------------------------------------
    s.section("Every kind is sorted from its subject")
    for subject, reply_to, want in (
            (f"Booking.com - New booking! ({NUM_EN}, Saturday, 10 October 2026)", "", "reservation_new"),
            (f"Booking.com - Modified booking ({NUM_EN}, Saturday, 10 October 2026)", "", "reservation_changed"),
            (f"Booking.com - Cancelled booking ({NUM_EN}, Saturday, 10 October 2026)", "", "reservation_cancelled"),
            (f"Booking.com - Nouvelle réservation ! ({NUM_FR}, samedi 17 octobre 2026)", "", "reservation_new"),
            (f"Booking.com - Réservation annulée ({NUM_FR}, samedi 17 octobre 2026)", "", "reservation_cancelled"),
            ("We received this message from Jane Doe", "", "message"),
            ("Nouveau message de Pierre Martin", "", "message"),
            ("Re: your stay in October", ALIAS, "message"),
            ("A guest has made a special request", "", "request"),
            ("You've received a new guest review", "", "review"),
            ("Your Booking.com invoice for September 2026", "", "invoice"),
            ("We've sent your payout", "", "payout"),
            ("Your Booking.com verification code", "", "account"),
            ("Boost your bookings this winter with Genius", "", "promotion"),
            ("Important information about your property", "", "other")):
        got = m.classify_booking_com_email(subject, "noreply@booking.com", reply_to)
        s.check(f"'{subject[:48]}' is {m.OTA_KINDS[want].lower()}", got == want, detail=got)

    # ------------------------------------------------------------------
    s.section("What an email says is read out of it, where it says it plainly")
    rooms = [(501, "The King Room", None), (502, "The Family Suite", ROOM_LABEL)]
    subject = f"Booking.com - New booking! ({NUM_EN}, Saturday, 10 October 2026)"
    body, links = m.mail_html_to_text(new_booking_html())
    got = m.parse_booking_com_email(subject, "noreply@booking.com", "Booking.com", "", body,
                                    links, rooms)
    for key, want in (("reservation_number", NUM_EN), ("guest_name", "Jane Doe"),
                      ("arrival_date", "2026-10-10"), ("departure_date", "2026-10-12"),
                      ("room_id", 502), ("guests", 2), ("amount", 1234.56), ("currency", "EUR")):
        s.check(f"a new booking gives its {key.replace('_', ' ')}", got[key] == want,
                detail=f"{got[key]!r}")

    body, links = m.mail_html_to_text(FR_BOOKING)
    got = m.parse_booking_com_email(
        f"Booking.com - Nouvelle réservation ! ({NUM_FR}, samedi 17 octobre 2026)",
        "noreply@booking.com", "Booking.com", "", body, links, rooms)
    for key, want in (("reservation_number", NUM_FR), ("guest_name", "Pierre Martin"),
                      ("arrival_date", "2026-10-17"), ("departure_date", "2026-10-19"),
                      ("guests", 3), ("amount", 1234.56)):
        s.check(f"and in French, its {key.replace('_', ' ')}", got[key] == want,
                detail=f"{got[key]!r}")

    for written, want in (("October 10, 2026", "2026-10-10"), ("10/10/2026", "2026-10-10"),
                          ("2026-10-10", "2026-10-10"), ("10th October 2026", "2026-10-10"),
                          ("3 févr. 2027", "2027-02-03")):
        found = [iso for _pos, iso in m.ota_dates(f"Check-in: {written}")]
        s.check(f"a date written '{written}' is read", found == [want], detail=str(found))

    got = m.parse_booking_com_email("Booking.com - Modified booking", "noreply@booking.com", "",
                                    "", "Booking number: " + NUM_EN + "\nNights: 2\n460 €", [])
    s.check("a figure does not run on from the line above it",
            got["amount"] == 460.0, detail=f"{got['amount']!r} -- 'Nights: 2' and '460' "
                                              "are two lines, not 2460")

    got = m.parse_booking_com_email("Room types", "noreply@booking.com", "", "",
                                    "Booking number: " + NUM_EN + "\nRoom: Deluxe King Room", [],
                                    [(501, "The King Room", None), (502, "The Family Suite", None)])
    s.check("the house's 'The King Room' answers to Booking.com's 'King Room'",
            got["room_id"] == 501, detail=f"{got['room_id']!r}")
    got = m.parse_booking_com_email("New booking", "noreply@booking.com", "", "",
                                    "Booking number: " + NUM_EN + "\nRoom: Family Suite", [],
                                    [(601, "Suite", None), (602, "Family Suite", None)])
    s.check("and the longest name that fits wins, so 'Suite' does not claim the Family Suite",
            got["room_id"] == 602, detail=f"{got['room_id']!r}")

    got = m.parse_booking_com_email("We received this message from Jane Doe", "noreply@booking.com",
                                    "Booking.com", ALIAS,
                                    "Jane Doe wrote:\nCould you call me on 0612345678?\nReply", [])
    s.check("a French telephone number is not taken for a reservation number",
            got["reservation_number"] is None, detail=f"{got['reservation_number']!r}")
    got = m.parse_booking_com_email("Your Booking.com invoice for September 2026",
                                    "noreply@booking.com", "", "",
                                    "Invoice number 1234567890\nAmount due: EUR 312.40", [])
    s.check("nor is an invoice's own number", got["reservation_number"] is None,
            detail=f"{got['reservation_number']!r}")
    s.check("and the invoice's amount is read", got["amount"] == 312.4, detail=f"{got['amount']!r}")
    got = m.parse_booking_com_email("Changes to our Terms and Conditions", "noreply@booking.com",
                                    "", "", "We have changed our terms. Read them online.", [])
    s.check("a change to Booking.com's terms is not a changed booking",
            got["kind"] == "other", detail=got["kind"])

    body, links = m.mail_html_to_text(message_html())
    got = m.parse_booking_com_email("We received this message from Jane Doe", "noreply@booking.com",
                                    "Booking.com", ALIAS, body, links)
    s.check("a guest's message is lifted out of the email around it",
            (got["message_text"] or "").startswith("Hello! We will arrive around 9pm")
            and "parking for two cars" in (got["message_text"] or ""),
            detail=repr(got["message_text"]))
    s.check("without the reply button or Booking.com's address",
            "Reply" not in (got["message_text"] or "")
            and "Herengracht" not in (got["message_text"] or ""),
            detail=repr(got["message_text"]))
    s.check("and the guest is named from the subject", got["guest_name"] == "Jane Doe",
            detail=repr(got["guest_name"]))
    got = m.parse_booking_com_email("Re: your stay", ALIAS, "Jane Doe via Booking.com", "",
                                    "See you soon", [])
    s.check("or from the sender, without the 'via Booking.com'", got["guest_name"] == "Jane Doe",
            detail=repr(got["guest_name"]))
    got = m.parse_booking_com_email("New message", "noreply@booking.com", "Booking.com", "",
                                    "You have a new message.", [])
    s.check("but Booking.com is never taken for the guest's name", got["guest_name"] is None,
            detail=repr(got["guest_name"]))
    for said in ("You have a new message from a guest", "Nouveau message d'un voyageur",
                 "New message from your guest"):
        got = m.parse_booking_com_email(said, "noreply@booking.com", "Booking.com", "", "", [])
        s.check(f"nor is '{said[-18:]}' -- it names nobody", got["guest_name"] is None,
                detail=repr(got["guest_name"]))

    # ------------------------------------------------------------------
    s.section("Kept, once, and the booking it describes kept up to date")
    new_id, kind, fresh = _ingest(conn, "new", subject, html=new_booking_html(),
                                  received_at=_ago(hours=3))
    row = _row(conn, new_id)
    s.check("a new booking is kept", fresh and kind == "reservation_new", detail=f"{kind} {fresh}")
    s.check("marked as Booking.com's own mail", row["from_booking_com"] == 1)
    s.check("with the room it is for, from the name Booking.com uses for it",
            row["room_id"] == room["id"], detail=f"{row['room_id']} vs {room['id']}")
    s.check("and its whole text and links", "Total price" in (row["body_text"] or "")
            and EXTRANET in (row["links"] or ""))
    res = conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = ?",
                       (NUM_EN,)).fetchone()
    s.check("the booking it describes is on record", res is not None and res["status"] == "confirmed",
            detail=str(dict(res)) if res else "no reservation row")
    s.check("with its guest, stay, party and price",
            res is not None and (res["guest_name"], res["arrival_date"], res["departure_date"],
                                 res["guests"], res["amount"])
            == ("Jane Doe", "2026-10-10", "2026-10-12", 2, 1234.56),
            detail=str(dict(res)) if res else "")
    s.check("and the email points at it", row["ota_reservation_id"] == (res["id"] if res else -1))
    told = _told(conn, new_id)
    s.check("whoever handles Booking.com is told", len(told) == 1
            and told[0]["user_id"] == owner["id"], detail=str([dict(t) for t in told]))
    s.check("in words that say what happened",
            bool(told) and "new booking" in told[0]["title"] and "Jane Doe" in told[0]["title"],
            detail=told[0]["title"] if told else "")
    s.check("linking straight to that email on the page",
            bool(told) and told[0]["link"].endswith(f"/management/booking-com#m{new_id}"),
            detail=told[0]["link"] if told else "")

    again = _ingest(conn, "new", subject, html=new_booking_html(), received_at=_ago(hours=3))
    s.check("the same email read twice is kept once",
            again == (new_id, "reservation_new", False)
            and conn.execute("SELECT COUNT(*) FROM ota_mail WHERE source_id = ?",
                             (_mid("new"),)).fetchone()[0] == 1, detail=str(again))
    s.check("and nobody is told twice", len(_told(conn, new_id)) == 1)

    cancel_id, kind, _ = _ingest(
        conn, "cancel", f"Booking.com - Cancelled booking ({NUM_EN}, Saturday, 10 October 2026)",
        text=f"This booking has been cancelled by the guest.\nBooking number: {NUM_EN}\n"
             "Guest name: Jane Doe\nCheck-in: Saturday, 10 October 2026\n"
             "Check-out: Monday, 12 October 2026", received_at=_ago(hours=1))
    status = conn.execute("SELECT status FROM ota_reservations WHERE reservation_number = ?",
                          (NUM_EN,)).fetchone()["status"]
    s.check("a cancellation cancels the booking", status == "cancelled", detail=status)
    s.check("and says so", any("cancelled" in t["title"] for t in _told(conn, cancel_id)))

    # Read out of order: the cancellation first, then the older booking.
    _ingest(conn, "late-cancel", f"Booking.com - Cancelled booking ({NUM_LATE}, Friday, 6 November 2026)",
            text=f"Booking number: {NUM_LATE}\nCheck-in: Friday, 6 November 2026\n"
                 "Check-out: Sunday, 8 November 2026", received_at=_ago(hours=2))
    _ingest(conn, "late-new", f"Booking.com - New booking! ({NUM_LATE}, Friday, 6 November 2026)",
            html=new_booking_html(number=NUM_LATE, guest="Anna Berg",
                                  arrive="Friday, 6 November 2026", leave="Sunday, 8 November 2026",
                                  guests="4 adults"),
            received_at=_ago(days=3))
    late = conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = ?",
                        (NUM_LATE,)).fetchone()
    s.check("an older email read after a newer one does not undo it",
            late["status"] == "cancelled", detail=late["status"])
    s.check("though it still fills in what the newer one left out",
            late["guests"] == 4 and late["guest_name"] == "Anna Berg",
            detail=f"{late['guests']} {late['guest_name']}")

    fr_id, _k, _f = _ingest(conn, "fr", f"Booking.com - Nouvelle réservation ! ({NUM_FR}, samedi 17 octobre 2026)",
                            html=FR_BOOKING, received_at=_ago(hours=4))

    account_id, kind, _ = _ingest(conn, "account", "Your Booking.com verification code",
                                  text="Your verification code is 482913. It expires in 10 minutes.")
    row = _row(conn, account_id)
    s.check("a security email is recorded as having come", kind == "account" and row is not None)
    s.check("but what it said is not kept anywhere",
            "482913" not in json.dumps({k: row[k] for k in row.keys()}),
            detail="a sign-in code is a key, and the privacy notice says keys are never kept")
    s.check("and it raises no notification", not _told(conn, account_id))

    spoof_id, kind, _ = _ingest(
        conn, "spoof", "New message from a guest",
        html=f'<p>Your account is on hold. <a href="{PHISH}">Confirm your details</a></p>',
        sender="security@booking-com-verify.example", name="Booking.com", reply_to=ALIAS)
    row = _row(conn, spoof_id)
    s.check("an email claiming to be Booking.com is kept, and marked as not from it",
            row is not None and row["from_booking_com"] == 0)
    s.check("nobody is told about it", not _told(conn, spoof_id))

    # ------------------------------------------------------------------
    s.section("A guest who has written is waiting, and somebody hears")
    msg_id, kind, _ = _ingest(conn, "msg", "We received this message from Jane Doe",
                              html=message_html(), reply_to=ALIAS, received_at=_ago(minutes=20))
    told = _told(conn, msg_id)
    s.check("a guest's message is a notification as it arrives",
            kind == "message" and len(told) == 1 and "Jane Doe has written" in told[0]["title"],
            detail=str([t["title"] for t in told]))
    s.check("carrying the start of what they wrote",
            bool(told) and (told[0]["body"] or "").startswith("Hello! We will arrive"),
            detail=(told[0]["body"] or "")[:60] if told else "")
    old_id, _k, _f = _ingest(conn, "msg-old", "We received this message from Tom Hale",
                             html=message_html(guest="Tom Hale"), reply_to="tom.h@guest.booking.com",
                             received_at=_ago(days=5))
    s.check("an old one read for the first time is not news -- no notification",
            not _told(conn, old_id))
    gone_id, _k, _f = _ingest(conn, "msg-gone", "We received this message from Ada Lowe",
                              html=message_html(guest="Ada Lowe"), reply_to="ada.l@guest.booking.com",
                              received_at=_ago(days=20))
    bare_id, _k, _f = _ingest(conn, "msg-bare", "New message about reservation " + NUM_EN,
                              text="Jane Doe wrote:\nIs breakfast included?\nReply",
                              received_at=_ago(minutes=40))
    waiting = {r["id"] for r in m.booking_com_waiting(conn)}
    s.check("the guests inside the reply window are waiting",
            {msg_id, old_id, bare_id} <= waiting, detail=str(sorted(waiting)))
    s.check("one past the fortnight Booking.com allows is not", gone_id not in waiting)
    s.check("nor is an email that only claims to be Booking.com", spoof_id not in waiting)

    # Routed: the Booking.com inbox pinned to somebody else. Somebody made for
    # it, because the copied database's first employee may be on leave today,
    # and the router rightly skips a person who is away.
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, 'x', 'employee', 'ZZBC Front Desk', 'Reception', 'active', ?)""",
        (f"{TAG}.desk@example.invalid", _ago(seconds=1)))
    desk = conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}.desk@example.invalid",)).fetchone()["id"]
    conn.execute("""INSERT INTO mailbox_routing (mailbox, label, default_user_id, active, created_at)
                    VALUES (?, 'Booking.com', ?, 1, ?)""", (MAILBOX, desk, _ago(seconds=1)))
    conn.commit()
    routed_id, _k, _f = _ingest(conn, "msg-routed", "We received this message from Lea Voss",
                                html=message_html(guest="Lea Voss"), reply_to="lea.v@guest.booking.com")
    told = _told(conn, routed_id)
    s.check("and the notification goes to whoever the Booking.com inbox is routed to",
            len(told) == 1 and told[0]["user_id"] == desk,
            detail=str([t["user_id"] for t in told]))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox = ?", (MAILBOX,))
    conn.commit()

    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, m.house_today())
        found, _dropped = m.watch_task_findings(conn)
    ours = [w for w in warnings if w["title"] == "Booking.com guests waiting for an answer"]
    waiting_now = m.booking_com_waiting(conn)
    s.check("the owner's home says guests are waiting, and how many",
            len(ours) == 1 and ours[0]["count"] == len(waiting_now),
            detail=str(ours) + f" vs {len(waiting_now)} waiting")
    s.check("linking to the page that answers them",
            bool(ours) and ours[0]["href"].endswith("/management/booking-com"))
    task = [f for f in found if f[0] == "booking_com"]
    s.check("and it is a task", len(task) == 1
            and task[0][1] == "Answer the guests who wrote through Booking.com", detail=str(task))
    s.check("due the day the longest-waiting guest wrote",
            bool(task) and task[0][3] == m.house_date_iso(waiting_now[0]["received_at"]),
            detail=f"{task[0][3] if task else ''!r}")
    with m.app.test_request_context("/"):
        m.generate_watch_tasks(conn)
    conn.commit()
    open_task = conn.execute(
        "SELECT id FROM tasks WHERE origin = ? AND title = ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, "Answer the guests who wrote through Booking.com")).fetchone()
    s.check("which reaches the task list", open_task is not None)

    # ------------------------------------------------------------------
    s.section("The page")
    r = oc.get("/management/booking-com")
    page = r.get_data(as_text=True)
    s.check("the owner can open it", r.status_code == 200, detail=str(r.status_code))
    s.check("it lists every email, sorted", _card_count(page) >= 10 and "Guest message" in page
            and "New booking" in page and "Booking cancelled" in page, detail=str(_card_count(page)))
    s.check("and says guests are waiting", "waiting for an answer." in visible_text(page))
    s.check("with each guest's message shown, not buried in the email",
            'class="bc-message">Hello! We will arrive around 9pm' in page)
    s.check("the security email says it was not kept, and shows nothing of it",
            "A security email. Its text is not kept here" in page and "482913" not in page)
    s.check("the spoofed email is marked as not from Booking.com",
            "not from booking.com" in page)
    s.check("and its link cannot be opened from here", f'href="{PHISH}' not in page
            and PHISH in page, detail="a link anybody could have sent must not be one click away")
    s.check("while Booking.com's own links can", f'href="{EXTRANET}"' in page)
    s.check("a guest who gave Booking.com's reply address can be answered from here",
            f'/management/booking-com/{msg_id}/reply' in page)
    s.check("the spoof cannot, whatever address it gave",
            f'/management/booking-com/{spoof_id}/reply' not in page)
    s.check("nor a message that came with no address to answer",
            f'/management/booking-com/{bare_id}/reply' not in page)
    s.check("there is no chip for everything that is not a message", "Not a message" not in page)
    card = re.search(rf'id="m{msg_id}"(.*?)(?=class="detail-card bc-mail|$)', page, re.S)
    card = visible_text(card.group(1)) if card else ""
    s.check("a guest's message shows the stay it is about, from the booking on record",
            "Stay 10 – 12 October 2026" in card and f"Room {room['name']}" in card,
            detail=card[:200])
    s.check("and says when that booking has since been cancelled", "Booking cancelled" in card,
            detail=card[:200])

    def fr_row(html):
        got = re.search(rf"<tr>\s*<td>{NUM_FR}</td>(.*?)</tr>", html, re.S)
        return got.group(1) if got else ""
    s.check("a Booking.com stay the calendar has not blocked says so",
            "Not yet" in fr_row(page), detail=visible_text(fr_row(page)))
    conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, ?, ?)",
                 (room["id"], TAG + " feed", "https://example.invalid/zzbc.ics"))
    source_id = conn.execute("SELECT id FROM ical_sources WHERE label = ?",
                             (TAG + " feed",)).fetchone()["id"]
    conn.execute("INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) "
                 "VALUES (?, ?, '2026-10-17', '2026-10-19')", (room["id"], source_id))
    conn.commit()
    page = oc.get("/management/booking-com").get_data(as_text=True)
    s.check("and one it has, says that", "Yes" in fr_row(page) and "Not yet" not in fr_row(page),
            detail=visible_text(fr_row(page)))

    page = oc.get(f"/management/booking-com?q={NUM_FR}").get_data(as_text=True)
    s.check("it can be searched by reservation number", _card_count(page) == 1
            and f'id="m{fr_id}"' in page, detail=str(_card_count(page)))
    page = oc.get("/management/booking-com?kind=Guest message").get_data(as_text=True)
    s.check("and narrowed to one kind", f'id="m{msg_id}"' in page and f'id="m{new_id}"' not in page)
    page = oc.get("/management/booking-com?answer=Waiting for an answer").get_data(as_text=True)
    s.check("or to the guests still waiting", f'id="m{msg_id}"' in page
            and f'id="m{gone_id}"' not in page and f'id="m{new_id}"' not in page)
    r = ec.get("/management/booking-com")
    s.check("an employee cannot read it", r.status_code in (302, 403), detail=str(r.status_code))

    # ------------------------------------------------------------------
    s.section("Answering")
    sent = []

    def _send(to, subject, body, *a, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "area": kw.get("area")})
        return True

    with _StandIns(send_email=_send):
        r = oc.post(f"/management/booking-com/{msg_id}/reply", data={"reply": "  "},
                    follow_redirects=True)
        s.check("an empty reply is refused, and says so",
                not sent and any("nothing in the reply" in f for f in flashes(r)),
                detail=f"{sent} {flashes(r)}")
        r = oc.post(f"/management/booking-com/{msg_id}/reply",
                    data={"reply": "Of course -- there is parking in the courtyard."},
                    follow_redirects=True)
        s.check("a reply goes to the address Booking.com gave for the guest",
                len(sent) == 1 and sent[0]["to"] == ALIAS, detail=str(sent))
        s.check("as an answer to what they wrote",
                bool(sent) and sent[0]["subject"] == "Re: We received this message from Jane Doe",
                detail=sent[0]["subject"] if sent else "")
        s.check("and the page says it went", any("Sent to the guest" in f for f in flashes(r)),
                detail=str(flashes(r)))
        row = _row(conn, msg_id)
        s.check("the reply is kept with the message", (row["reply_text"] or "").startswith("Of course")
                and row["replied_at"] and row["handled_at"])
        s.check("which is no longer waiting",
                msg_id not in {w["id"] for w in m.booking_com_waiting(conn)})

        before = len(sent)
        r = oc.post(f"/management/booking-com/{spoof_id}/reply", data={"reply": "Hello"},
                    follow_redirects=True)
        s.check("an email that only claims to be Booking.com gets no reply from the house",
                len(sent) == before, detail=str(sent[before:]))
        r = oc.post(f"/management/booking-com/{bare_id}/reply", data={"reply": "Hello"},
                    follow_redirects=True)
        s.check("nor does one with no address to answer at, and it says why",
                len(sent) == before and any("extranet" in f for f in flashes(r)),
                detail=str(flashes(r)))
        s.check("a booking is not a message to reply to",
                oc.post(f"/management/booking-com/{new_id}/reply",
                        data={"reply": "x"}).status_code == 404 and len(sent) == before)
        r = ec.post(f"/management/booking-com/{old_id}/reply", data={"reply": "x"})
        s.check("an employee cannot reply", r.status_code in (302, 403) and len(sent) == before)

    r = ec.post(f"/management/booking-com/{bare_id}/answered")
    s.check("an employee cannot mark one answered", r.status_code in (302, 403)
            and not _row(conn, bare_id)["handled_at"])
    s.check("a booking cannot be marked answered",
            oc.post(f"/management/booking-com/{new_id}/answered").status_code == 404)
    for waiting_id in [w["id"] for w in m.booking_com_waiting(conn)]:
        r = oc.post(f"/management/booking-com/{waiting_id}/answered", follow_redirects=True)
    s.check("answered in Booking.com, one is off the list",
            not m.booking_com_waiting(conn) and _row(conn, bare_id)["handled_at"],
            response=r)
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, m.house_today())
        m.generate_watch_tasks(conn)
    conn.commit()
    s.check("and the owner's home says nothing more about it",
            not [w for w in warnings if w["title"] == "Booking.com guests waiting for an answer"])
    closed = conn.execute("SELECT status FROM tasks WHERE id = ?",
                          (open_task["id"] if open_task else -1,)).fetchone()
    s.check("and the task closed itself", closed is not None and closed["status"] == "done",
            detail=str(dict(closed)) if closed else "no task")

    # ------------------------------------------------------------------
    s.section("Adding emails by hand")
    first = _eml(f"Booking.com - New booking! ({NUM_UP}, Friday, 4 December 2026)",
                 html=new_booking_html(number=NUM_UP, guest="Ines Roca",
                                       arrive="Friday, 4 December 2026",
                                       leave="Sunday, 6 December 2026"),
                 when=datetime.now(timezone.utc) - timedelta(days=5), message_id=_mid("up-1"))
    pasted = ("From: Booking.com <noreply@booking.com>\n"
              "Subject: ZZBC You've received a new guest review\n"
              f"Date: {format_datetime(datetime.now(timezone.utc) - timedelta(days=4))}\n\n"
              "A guest has reviewed their stay. Review score: 9.6\n")
    r = oc.post("/management/booking-com/upload", data={
        "emails": [(io.BytesIO(first), "booking.eml"), (io.BytesIO(first), "again.eml"),
                   (io.BytesIO(b"just some words"), "notes.txt"),
                   (io.BytesIO(b"x" * (m.BOOKING_COM_UPLOAD_MAX_BYTES + 1)), "huge.eml")],
        "source": pasted}, content_type="multipart/form-data", follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("saved emails and pasted source are both read", "Kept 2 of 5 emails" in said, detail=said)
    s.check("and what was left is named, with why",
            "again.eml: it is already here" in said and "notes.txt: it is not an email" in said
            and "huge.eml: it is larger than 3 MB" in said, detail=said)
    up = conn.execute("SELECT * FROM ota_mail WHERE source_id = ?", (_mid("up-1"),)).fetchone()
    s.check("an uploaded booking is read like any other",
            up is not None and up["kind"] == "reservation_new" and up["guest_name"] == "Ines Roca"
            and up["arrival_date"] == "2026-12-04", detail=str(dict(up)) if up else "")
    arrived = m.parse_datetime_iso(up["received_at"]) if up else None
    s.check("dated when it arrived, not when it was uploaded",
            arrived is not None and abs(datetime.now(timezone.utc) - timedelta(days=5) - arrived)
            < timedelta(minutes=5), detail=up["received_at"] if up else "")
    s.check("and an old one uploaded buzzes nobody's phone", up is not None and not _told(conn, up["id"]))
    review = conn.execute("SELECT kind, source_id FROM ota_mail WHERE subject LIKE 'ZZBC%'").fetchone()
    s.check("pasted source with no Message-ID is still kept, as a review",
            review is not None and review["kind"] == "review"
            and review["source_id"].startswith("sha1:"), detail=str(dict(review)) if review else "")
    r = oc.post("/management/booking-com/upload", data={"source": pasted},
                content_type="multipart/form-data", follow_redirects=True)
    s.check("and pasted twice, it is kept once", "already here" in " ".join(flashes(r)),
            detail=str(flashes(r)))

    # ------------------------------------------------------------------
    s.section("The mailbox, and reading it")
    r = oc.post("/management/booking-com/mailbox", data={"mailbox": "not an address"},
                follow_redirects=True)
    s.check("a mailbox that is not an address is refused",
            m.booking_com_mailbox(conn) == MAILBOX and any("does not look like" in f for f in flashes(r)),
            detail=str(flashes(r)))
    r = oc.post("/management/booking-com/mailbox", data={"mailbox": "  BookingCom@Test.invalid "},
                follow_redirects=True)
    s.check("the mailbox can be set", m.booking_com_mailbox(conn) == MAILBOX, response=r)
    r = ec.post("/management/booking-com/mailbox", data={"mailbox": "x@example.invalid"})
    s.check("not by an employee", r.status_code in (302, 403) and m.booking_com_mailbox(conn) == MAILBOX)

    conn.execute("UPDATE app_settings SET value = '' WHERE key = 'booking_com_mailbox'")
    conn.commit()
    with _StandIns(MS_GRAPH_MAILBOXES=["bookings@x.invalid", "bookingcom@x.invalid"]):
        s.check("with none set, a mailbox called bookingcom@ is taken to be it",
                m.booking_com_mailbox(conn) == "bookingcom@x.invalid", detail=m.booking_com_mailbox(conn))
    with _StandIns(MS_GRAPH_MAILBOXES=["bookings@x.invalid"]):
        s.check("and with neither, the job does nothing and says why",
                m.run_booking_com_mail_job(conn) == "no Booking.com mailbox set")
    conn.execute("UPDATE app_settings SET value = ? WHERE key = 'booking_com_mailbox'", (MAILBOX,))
    conn.commit()
    s.check("without Microsoft 365 connected, it says that instead",
            m.run_booking_com_mail_job(conn) == "Microsoft Graph not configured")

    asked = []
    page_of_mail = {"value": [
        {"id": "g1", "internetMessageId": _mid("g1"), "receivedDateTime": _ago(minutes=3)[:19] + "Z",
         "from": {"emailAddress": {"name": "Booking.com", "address": "noreply@booking.com"}},
         "replyTo": [{"emailAddress": {"address": "sam.k@guest.booking.com"}}],
         "subject": "We received this message from Sam Kerr",
         "body": {"contentType": "html", "content": message_html(guest="Sam Kerr")}},
        {"id": "g2", "internetMessageId": _mid("g2"), "receivedDateTime": _ago(minutes=2)[:19] + "Z",
         "from": {"emailAddress": {"name": "Booking.com", "address": "noreply@booking.com"}},
         "replyTo": [], "subject": "Booking.com - New booking! (9876500005, Friday, 11 December 2026)",
         "body": {"contentType": "text", "content": "Booking number: 9876500005\n"
                  "Check-in: Friday, 11 December 2026\nCheck-out: Sunday, 13 December 2026"}},
    ]}

    def _graph_get(path, token, params=None):
        asked.append((path, token, dict(params or {})))
        return page_of_mail

    with _StandIns(graph_enabled=lambda: True, get_graph_token=lambda: "tok", graph_get=_graph_get):
        said = m.run_booking_com_mail_job(conn)
        s.check("the job keeps what is new, and says what it kept",
                said == "kept 1 × guest message, 1 × new booking", detail=said)
        s.check("reading the Booking.com mailbox's inbox, oldest first",
                bool(asked) and asked[0][0] == f"/users/{MAILBOX}/mailFolders/inbox/messages"
                and asked[0][2].get("$orderby") == "receivedDateTime asc", detail=str(asked[:1]))
        s.check("asking for the body and the reply address",
                bool(asked) and "body" in asked[0][2].get("$select", "")
                and "replyTo" in asked[0][2].get("$select", ""))
        g1 = conn.execute("SELECT * FROM ota_mail WHERE source_id = ?", (_mid("g1"),)).fetchone()
        s.check("a message read from the mailbox keeps the guest's reply address",
                g1 is not None and g1["reply_to"] == "sam.k@guest.booking.com"
                and g1["mailbox"] == MAILBOX, detail=str(dict(g1)) if g1 else "")
        s.check("with its time in the one spelling everything else uses",
                g1 is not None and g1["received_at"].endswith("+00:00"), detail=g1["received_at"] if g1 else "")
        told = _told(conn, g1["id"]) if g1 else []
        s.check("and the notification links to the page even outside a request",
                len(told) == 1 and told[0]["link"] == f"/management/booking-com#m{g1['id']}",
                detail=str([t["link"] for t in told]))
        again = m.run_booking_com_mail_job(conn)
        s.check("read again, nothing is kept twice", again == f"nothing new in {MAILBOX}", detail=again)
    with _StandIns(graph_enabled=lambda: True, get_graph_token=lambda: "tok",
                   graph_get=lambda *a, **k: None):
        s.check("a mailbox that cannot be read says so",
                m.run_booking_com_mail_job(conn) == f"could not read {MAILBOX}")

    scanned = []

    def _fetch(token, folder, since_iso, top=100, mailbox=None):
        scanned.append(mailbox)
        return []

    conn.execute("""INSERT INTO mailbox_routing (mailbox, label, active, created_at)
                    VALUES (?, 'Booking.com', 1, ?), ('zzbc-front@test.invalid', 'Front', 1, ?)""",
                 (MAILBOX, _ago(seconds=1), _ago(seconds=1)))
    conn.commit()
    with _StandIns(graph_enabled=lambda: True, get_graph_token=lambda: "tok",
                   fetch_graph_messages=_fetch, MS_GRAPH_MAILBOXES=[]):
        m.run_email_inbox_scan_job(conn)
    s.check("the unanswered-email scan still reads the other inboxes",
            "zzbc-front@test.invalid" in scanned, detail=str(scanned))
    s.check("but leaves Booking.com's to its own job -- a notice is not an email awaiting a reply",
            MAILBOX not in scanned, detail=str(scanned))

    s.check("the job runs by itself, every ten minutes",
            any(j[0] == "booking_com_mail" and j[3] == 600 and j[4] is m.run_booking_com_mail_job
                for j in m.AUTOMATION_JOBS))
    s.check("switched on by default, since it does nothing until it is set up",
            m.AUTOMATION_SETTING_DEFAULTS.get("automation_booking_com_mail_enabled") == "1")

    # ------------------------------------------------------------------
    s.section("And it goes when the privacy notice says")
    today = m.house_today()

    def _aged(n, **fields):
        cols = {"channel": "booking.com", "source": "test", "source_id": _mid(n),
                "received_at": _ago(days=10), "kind": "review", "subject": "Old",
                "created_at": _ago(days=10)}
        cols.update(fields)
        conn.execute(f"INSERT INTO ota_mail ({', '.join(cols)}) VALUES "
                     f"({', '.join('?' for _ in cols)})", tuple(cols.values()))
    _aged("old-stay", kind="reservation_new", departure_date=(today - timedelta(days=760)).isoformat())
    _aged("recent-stay", kind="reservation_new", departure_date=(today - timedelta(days=700)).isoformat())
    _aged("old-review", received_at=_ago(days=760))
    _aged("new-review", received_at=_ago(days=30))
    conn.commit()
    with m.app.test_request_context("/"):
        cleared = m.purge_booking_com_mail(conn)

    def _there(n):
        return conn.execute("SELECT 1 FROM ota_mail WHERE source_id = ?", (_mid(n),)).fetchone() is not None
    s.check("an email about a stay over two years gone is deleted", not _there("old-stay"))
    s.check("one about a stay under two years ago is kept", _there("recent-stay"))
    s.check("an email with no stay goes two years after it arrived", not _there("old-review"))
    s.check("and a recent one is kept", _there("new-review"))
    s.check("it says what it cleared", cleared.get("old Booking.com email", 0) >= 2, detail=str(cleared))
    src = open(m.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    s.check("the daily retention pass runs it",
            "purge_booking_com_mail(conn)" in src.split("def run_health_notes_purge_job")[1][:1500],
            detail="a purge nothing runs is a promise nothing keeps")
    notice = oc.get("/privacy").get_data(as_text=True)
    s.check("the notice tells a Booking.com guest what is kept",
            "If you booked through Booking.com" in notice)
    s.check("for as long as the code keeps it", "Kept for two years after the stay" in notice
            and m.GUEST_MESSAGE_KEEP_MONTHS == 24, detail=f"{m.GUEST_MESSAGE_KEEP_MONTHS} months")
    s.check("and that a security email is never kept",
            "such as a sign-in code, is never kept at all" in notice)


if __name__ == "__main__":
    print(run().report())
