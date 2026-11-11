"""Inbox Flags: 700 open, nobody assigned, so nobody read it (brief of 10 Nov 2026).

621 "unanswered", 69 "mentions a price, nothing to check" and 10 conflicts.
Page one was Nespresso, Basic-Fit, Cdiscount, wine offers, La Poste, iCloud,
a newsletter, Stripe payouts, RetreatGuru's own receipts and the app's own
"Booked —" alerts; the few real people were buried. A panel that can never
be empty is furniture.

  NEVER FLAGGED: a mailing (List-Unsubscribe, Precedence: bulk or list),
  anything a machine sent (Auto-Submitted), the app's own mail, the house
  writing to itself, and the senders on a never-flag list the owner keeps on
  the page. A fixture inbox of thirty messages with three real guests in it
  raises exactly three flags.

  "MENTIONS A PRICE, NOTHING TO CHECK" IS NOT A FLAG.

  ONE EMAIL IS ONE FLAG, however many inboxes it reached, and it shows them
  all. "Same sender in more than one inbox" ignores the house and the senders
  above.

  ON DEPLOY, every open flag the new rules would not have raised is closed,
  through bulk_message, by kind and count.

Nothing reaches Microsoft: tests/_graph_double.py plays its part.
"""
from datetime import timedelta

from _graph_double import FakeGraph, message
from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZIF"
RES = "zzif-reservations@zzhouse.invalid"
EXP = "zzif-experience@zzhouse.invalid"


def _cleanup(conn):
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox IN (?, ?)", (RES, EXP))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.INBOX_NEVER_FLAG_KEY,))
    conn.commit()


def _when(hours_ago):
    return (m.datetime.now(m.timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture():
    """Thirty messages in two inboxes: everything that buried the real people
    on 10 November, and three real guests -- one of them written to both."""
    old = _when(30)
    unsub = {"List-Unsubscribe": "<https://example.invalid/unsubscribe>"}
    res, exp = [], []
    n = 0

    def add(box, frm, subject, *, headers=None, when=old, preview="", internet_id=None):
        nonlocal n
        n += 1
        box.append(message(f"{TAG}-m{n:02d}", frm=frm, to=[RES], subject=f"{TAG} {subject}",
                           when=when, preview=preview or subject, conversation=f"{TAG}-c{n:02d}",
                           headers=headers, internet_id=internet_id))

    # Newsletters and offers, which say so in List-Unsubscribe and nowhere else.
    for frm, subject in (("offres@nespresso-mail.example", "Nos nouvelles capsules d'hiver"),
                         ("club@basic-fit-mail.example", "Votre séance vous attend"),
                         ("bonsplans@cdiscount-mail.example", "-40 % sur le petit électroménager"),
                         ("contact@luminaire-mail.example", "Appliques murales : la sélection"),
                         ("vins@lalande-moreau.example", "Offre primeurs 2025, 6 bouteilles €89"),
                         ("bonjour@zoomalia-mail.example", "Croquettes : livraison offerte"),
                         ("team@claude-mail.example", "What's new this month")):
        add(res, frm, subject, headers=unsub)
    # Mailings that say so in Precedence.
    add(res, "info@laposte-mail.example", "Votre colis est en route", headers={"Precedence": "bulk"})
    add(res, "liste@ariege-tourisme.example", "Lettre des prestataires", headers={"Precedence": "list"})
    # A machine: an out-of-office reply.
    add(res, "someone.away@example.org", "Réponse automatique : absent",
        headers={"Auto-Submitted": "auto-replied"})
    # The senders on the never-flag list.
    for frm, subject in (("notifications@stripe.com", "Your payout of €1,234.00 is on its way"),
                         ("noreply@retreat.guru", "Receipt for registration #4471"),
                         ("no_reply@email.apple.com", "Your iCloud storage is almost full"),
                         ("noreply@wise.com", "You received EUR 400"),
                         ("noreply@booking.com", "New message from a guest"),
                         ("news@some-brand.example", "Autumn news"),
                         ("newsletter@another-brand.example", "The November letter"),
                         ("mailer-daemon@mx.example.net", "Undeliverable: your message"),
                         ("no-reply@accounts.example.net", "Security alert"),
                         ("donotreply@bank.example.net", "Your statement is ready")):
        add(res, frm, subject)
    # The app's own mail, and the house writing to itself.
    add(res, "reservations@send.chateaugudanes.com", "Booked — The Twin Room (GUD-ZZIF01)")
    add(res, "accounts@send.chateaugudanes.com", "Payment received for GUD-ZZIF01")
    add(res, EXP, "Fw: a guest's question about the atelier")
    add(res, "owner@zzhouse.invalid", "Fw: supplier invoice")
    # A real person quoting a price nothing disagrees with, written an hour
    # ago: no wait yet, nothing to check, so nothing to flag.
    add(res, "zzif.mentions@guest.example", "Is €150 a night right for the Twin Room?", when=_when(1))
    # A real person who was answered.
    add(res, "zzif.answered@guest.example", "Do you have parking?")
    # The three real guests, unanswered for thirty hours. Guest A wrote to
    # both inboxes at once: one email, delivered twice.
    add(res, "zzif.guesta@guest.example", "A return visit in the spring",
        internet_id=f"<{TAG}-guest-a@guest.example>")
    add(exp, "zzif.guesta@guest.example", "A return visit in the spring",
        internet_id=f"<{TAG}-guest-a@guest.example>")
    add(res, "zzif.guestb@guest.example", "Photographs for our anniversary")
    add(res, "zzif.guestc@guest.example", "A question about your workshop dates")
    answered = next(msg for msg in res if "parking" in msg["subject"])
    sent = [{"id": f"{TAG}-sent-1", "conversationId": answered["conversationId"],
             "sentDateTime": _when(29), "subject": "Re: " + answered["subject"],
             "bodyPreview": "Yes, in the courtyard."}]
    return res, exp, sent


def _scan(conn, fake):
    with m.app.test_request_context("/"):
        return m.run_email_inbox_scan_job(conn)


def _open(conn):
    return conn.execute("SELECT * FROM email_flags WHERE subject LIKE ? AND status = 'open' ORDER BY id",
                        (f"{TAG}%",)).fetchall()


def run():
    s = Suite("inbox flags that can be empty")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    stamp = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute("""INSERT INTO mailbox_routing (mailbox, label, active, created_at)
                    VALUES (?, 'ZZIF Reservations', 1, ?), (?, 'ZZIF Experience', 1, ?)""",
                 (RES, stamp, EXP, stamp))
    conn.commit()
    res, exp, sent = _fixture()

    s.section("Thirty messages, three real guests, three flags")
    s.check("the fixture is thirty messages", len(res) + len(exp) == 30, detail=str(len(res) + len(exp)))
    with FakeGraph(mailboxes=[RES, EXP]) as fake:
        fake.inbox = {RES: res, EXP: exp}
        fake.sent = {RES: sent}
        report = _scan(conn, fake)
        flags = _open(conn)
        senders = sorted(f["from_address"] for f in flags)
        s.check("exactly three flags", len(flags) == 3, detail=f"{senders}; {report}")
        s.check("the three real guests",
                senders == ["zzif.guesta@guest.example", "zzif.guestb@guest.example",
                            "zzif.guestc@guest.example"], detail=str(senders))
        s.check("each for the wait", all(f["unanswered"] for f in flags))
        s.check("the report counts what it raised", "3 flagged" in report, detail=report)

        s.section("One email, one flag, every inbox it reached")
        guest_a = [f for f in flags if f["from_address"] == "zzif.guesta@guest.example"]
        s.check("guest A's email, delivered to both inboxes, is one flag", len(guest_a) == 1,
                detail=str(len(guest_a)))
        s.check("and it names both inboxes",
                guest_a and sorted(m.inbox_list(guest_a[0]["mailboxes"])) == sorted([RES, EXP]),
                detail=guest_a and guest_a[0]["mailboxes"])
        page = oc.get("/admin/inbox-flags").get_data(as_text=True)
        s.check("the page shows both on the one card",
                "ZZIF Reservations" in page and "ZZIF Experience" in page and "one email, 2 inboxes" in page)
        note = conn.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE ?",
                            (f"%{TAG} A return visit%",)).fetchone()[0]
        s.check("and whoever has it hears of it once", note == 1, detail=str(note))

        s.section("A copy that turns up a scan later joins the flag it already has")
        late = message(f"{TAG}-late-res", frm="zzif.guestd@guest.example", to=[RES, EXP],
                       subject=f"{TAG} Two nights in March", when=_when(30),
                       conversation=f"{TAG}-late-c1", internet_id=f"<{TAG}-guest-d@guest.example>")
        res.append(late)
        _scan(conn, fake)
        # The first copy is no longer in its inbox's listing (past the scan's
        # window, or that inbox unreadable today) when the second arrives:
        # only the email's own Message-ID can join them.
        res.remove(late)
        exp.append(message(f"{TAG}-late-exp", frm="zzif.guestd@guest.example", to=[RES, EXP],
                           subject=f"{TAG} Two nights in March", when=_when(30),
                           conversation=f"{TAG}-late-c2", internet_id=f"<{TAG}-guest-d@guest.example>"))
        _scan(conn, fake)
        d_flags = conn.execute("SELECT * FROM email_flags WHERE from_address = 'zzif.guestd@guest.example' "
                               "AND status = 'open'").fetchall()
        s.check("still one flag, by the email's own Message-ID",
                len(d_flags) == 1 and sorted(m.inbox_list(d_flags[0]["mailboxes"])) == sorted([RES, EXP]),
                detail=str([(f["graph_message_id"], f["mailboxes"]) for f in d_flags]))
        # And a pair from before, one flag per inbox, closes into one.
        conn.execute("DELETE FROM email_flags WHERE graph_message_id = ?", (f"{TAG}-late-exp",))
        conn.execute("UPDATE email_flags SET internet_message_id = NULL WHERE from_address = "
                     "'zzif.guestd@guest.example'")
        conn.execute(
            """INSERT INTO email_flags (graph_message_id, from_address, subject, received_at, unanswered,
                 status, mailbox, created_at, updated_at)
               VALUES (?, 'zzif.guestd@guest.example', ?, ?, 1, 'open', ?, ?, ?)""",
            (f"{TAG}-late-exp", f"{TAG} Two nights in March", _when(30), EXP, stamp, stamp))
        conn.commit()
        res.append(late)
        _scan(conn, fake)
        d_flags = conn.execute("SELECT * FROM email_flags WHERE from_address = 'zzif.guestd@guest.example' "
                               "ORDER BY id").fetchall()
        s.check("a pair from before, one per inbox, becomes one",
                [f["status"] for f in d_flags] == ["open", "dismissed"]
                and d_flags[1]["closed_by_app"] == "duplicate",
                detail=str([(f["graph_message_id"], f["status"], f["closed_by_app"]) for f in d_flags]))
        res.remove(late)
        exp[:] = [msg for msg in exp if msg["id"] != f"{TAG}-late-exp"]
        conn.execute("DELETE FROM email_flags WHERE from_address = 'zzif.guestd@guest.example'")
        conn.commit()

        s.section("A price with nothing wrong about it is not a flag")
        s.check("there is no such kind any more", "mentions" not in m.INBOX_FLAG_KINDS,
                detail=str(m.INBOX_FLAG_KINDS))
        s.check("and the email that only quoted a price raised nothing",
                not conn.execute("SELECT 1 FROM email_flags WHERE from_address = 'zzif.mentions@guest.example'")
                .fetchone())

        s.section("The same scan again changes nothing")
        again = _scan(conn, fake)
        s.check("still three", len(_open(conn)) == 3, detail=again)

        s.section("Answered from either inbox is answered")
        a_copy = next(msg for msg in exp if msg["from"]["emailAddress"]["address"] == "zzif.guesta@guest.example")
        fake.sent = {RES: sent, EXP: [{"id": f"{TAG}-sent-2", "conversationId": a_copy["conversationId"],
                                       "sentDateTime": _when(2), "subject": "Re: " + a_copy["subject"],
                                       "bodyPreview": "We'd love to have you back."}]}
        report = _scan(conn, fake)
        row = conn.execute("SELECT * FROM email_flags WHERE id = ?", (guest_a[0]["id"],)).fetchone()
        s.check("experience@ answered guest A, so the one flag closes",
                row["status"] == "resolved" and row["closed_by_app"] == "answered" and row["first_reply_at"],
                detail=f"{row['status']} {row['closed_by_app']}; {report}")
        s.check("by the app, not by a person", row["resolved_by_user_id"] is None)
        s.check("and the report says what it closed, by kind",
                "Closed 1 flag" in report and "answered since" in report, detail=report)
        s.check("two left", len(_open(conn)) == 2)

        s.section("The never-flag list is the owner's")
        page = oc.get("/admin/inbox-flags").get_data(as_text=True)
        s.check("it is on the page, as the app came with it",
                "Never flag these senders" in page and "stripe.com" in page and "noreply@" in page)
        r = oc.post("/admin/inbox-flags/never-flag",
                    data={"never_flag": m.INBOX_NEVER_FLAG_DEFAULT + "zzif.guestb@guest.example\n"},
                    follow_redirects=True)
        b_flag = conn.execute("SELECT * FROM email_flags WHERE from_address = 'zzif.guestb@guest.example'").fetchone()
        s.check("a sender added is saved", "zzif.guestb@guest.example" in m.inbox_never_flag_text(conn),
                response=r)
        s.check("and their open flag closes at once, saying why",
                b_flag["status"] == "dismissed" and b_flag["closed_by_app"] == "listed", detail=dict(b_flag))
        s.check("which the page says, by kind and count",
                any("Closed 1 flag" in f and "never-flag list" in f for f in flashes(r)), response=r)
        r = oc.post("/admin/inbox-flags/never-flag", data={"never_flag": "noreply@\nnot a sender at all\n"},
                    follow_redirects=True)
        s.check("a line that is not a sender is refused and named, and nothing saved",
                any("not a sender at all" in f and "Nothing was saved" in f for f in flashes(r))
                and "zzif.guestb@guest.example" in m.inbox_never_flag_text(conn), response=r)
        r = oc.post("/admin/inbox-flags/never-flag", data={"restore": "1"}, follow_redirects=True)
        s.check("the list the app came with can be put back",
                m.inbox_never_flag_text(conn) == m.INBOX_NEVER_FLAG_DEFAULT, response=r)
        _scan(conn, fake)
        b_flag = conn.execute("SELECT * FROM email_flags WHERE id = ?", (b_flag["id"],)).fetchone()
        s.check("taken off the list, a guest still waiting is flagged again",
                b_flag["status"] == "open" and not b_flag["closed_by_app"], detail=dict(b_flag))
        oc.post(f"/admin/inbox-flags/{b_flag['id']}/dismiss")
        _scan(conn, fake)
        b_flag = conn.execute("SELECT * FROM email_flags WHERE id = ?", (b_flag["id"],)).fetchone()
        s.check("but one a person dismissed stays dismissed", b_flag["status"] == "dismissed",
                detail=dict(b_flag))

        s.section("Same sender in more than one inbox: guests only")
        for box in (RES, EXP):
            for frm in ("notifications@stripe.com", EXP, "reservations@send.chateaugudanes.com",
                        "zzif.twice@guest.example"):
                conn.execute(
                    """INSERT INTO email_flags (graph_message_id, from_address, subject, received_at,
                         unanswered, status, mailbox, rules_checked_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 1, 'open', ?, ?, ?, ?)""",
                    (f"{TAG}-dup-{box}-{frm}", frm, f"{TAG} twice {frm}", _when(40), box, stamp,
                     stamp, stamp))
        conn.commit()
        dups = [d["sender"] for d in m.cross_inbox_duplicates(conn)]
        s.check("a guest written to by two inboxes is named", "zzif.twice@guest.example" in dups,
                detail=str(dups))
        s.check("Stripe, the house and the app's own mail are not",
                not ({"notifications@stripe.com", EXP, "reservations@send.chateaugudanes.com"} & set(dups)),
                detail=str(dups))

    s.section("On deploy: every open flag the new rules would not have raised")
    # The house's inboxes are the ones configured, as on the live site.
    house = FakeGraph(mailboxes=[RES, EXP]).__enter__()
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    room = rooms[0]

    def old_flag(key, frm, subject, mailbox=RES, received=None, **cols):
        fields = {"graph_message_id": f"{TAG}-old-{key}", "from_address": frm,
                  "subject": f"{TAG} {subject}", "preview": cols.pop("preview", subject),
                  "received_at": received or _when(100), "mailbox": mailbox, "status": "open",
                  "created_at": stamp, "updated_at": stamp}
        fields.update(cols)
        conn.execute(f"INSERT INTO email_flags ({', '.join(fields)}) VALUES "
                     f"({', '.join('?' for _ in fields)})", tuple(fields.values()))

    same_time = _when(120)
    old_flag("stripe", "notifications@stripe.com", "Your payout", unanswered=1)
    old_flag("own", "reservations@send.chateaugudanes.com", "Booked — a room", unanswered=1)
    old_flag("house", EXP, "Fw: a question", unanswered=1)
    old_flag("mentions", "zzif.price@guest.example", "€150 a night?", extracted_price=150)
    old_flag("answered", "zzif.replied@guest.example", "Parking?", first_reply_at=_when(90))
    old_flag("dup1", "zzif.both@guest.example", "To both of you", mailbox=RES, received=same_time,
             unanswered=1)
    old_flag("dup2", "zzif.both@guest.example", "To both of you", mailbox=EXP, received=same_time,
             unanswered=1)
    old_flag("waiting", "zzif.waiting@guest.example", "Still waiting", unanswered=1)
    conn.commit()
    with m.app.test_request_context("/"):
        closed, words = m.close_flags_the_rules_would_not_raise(conn)
    left = sorted(f["graph_message_id"] for f in _open(conn))
    s.check("the newsletter, the app's own, the house's, the price-only, the answered and the copy close",
            left == [f"{TAG}-old-dup1", f"{TAG}-old-waiting"], detail=str(left))
    s.check("counted by kind",
            closed == {"listed": 1, "own": 1, "internal": 1, "mentions": 1, "answered": 1, "duplicate": 1},
            detail=str(closed))
    s.check("and said through bulk_message, by kind and count",
            words.startswith("Closed 6 flags (") and "1 that only mentioned a price" in words
            and "1 already flagged in another inbox" in words and "1 of the app's own mail" in words,
            detail=words)
    keep = conn.execute("SELECT * FROM email_flags WHERE graph_message_id = ?", (f"{TAG}-old-dup1",)).fetchone()
    s.check("the copy that stays names both inboxes", m.inbox_list(keep["mailboxes"]) == [RES, EXP],
            detail=keep["mailboxes"])
    conn.execute("DELETE FROM app_settings WHERE key IN (?, 'inbox_flags_tidy_note')", (m.INBOX_TIDY_DONE_KEY,))
    old_flag("later", "noreply@retreat.guru", "Receipt", unanswered=1)
    conn.commit()
    with m.app.test_request_context("/"):
        said = m.tidy_inbox_flags_once(conn)
        twice = m.tidy_inbox_flags_once(conn)
    s.check("it runs on deploy, once", said and "Closed 1 flag" in said and twice is None,
            detail=f"{said!r} then {twice!r}")
    page = oc.get("/admin/inbox-flags").get_data(as_text=True)
    s.check("and the page says what it closed", "when these rules came in" in page and "Closed 1 flag" in page)
    house.__exit__(None, None, None)

    s.section("When the listing comes without headers, each message is asked once")
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.commit()
    res, exp, sent = _fixture()
    with FakeGraph(mailboxes=[RES, EXP]) as fake:
        fake.inbox = {RES: res, EXP: exp}
        fake.sent = {RES: sent}
        fake.list_headers = False
        report = _scan(conn, fake)
        asked = [c for c in fake.calls if c[0] == "GET" and "/messages/" in c[1]]
        senders = sorted(f["from_address"] for f in _open(conn))
        s.check("still exactly the three real guests",
                senders == ["zzif.guesta@guest.example", "zzif.guestb@guest.example",
                            "zzif.guestc@guest.example"], detail=f"{senders}; {report}")
        pinged = conn.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE ? AND title LIKE ?",
                              (f"%{TAG}%", "%capsules d'hiver%")).fetchone()[0]
        s.check("a newsletter is never raised, so nobody is notified of it first", pinged == 0,
                detail=str(pinged))
        s.check("the scan says it read headers one at a time",
                "headers read one message at a time" in report, detail=report)
        s.check("and says how many came with the listing, and how many were mailings",
                "headers came with 0 of 30 listed, 7 with List-Unsubscribe" in report,
                detail=report)
        s.check("and asked only the messages that would otherwise wait: not the never-flag "
                "senders, the house, the answered or the new",
                0 < len(asked) <= 14, detail=str([c[1].rsplit("/", 1)[-1] for c in asked]))
        fake.calls.clear()
        report = _scan(conn, fake)
        asked = [c for c in fake.calls if c[0] == "GET" and "/messages/" in c[1]]
        s.check("the next scan asks none of them again", not asked and len(_open(conn)) == 3,
                detail=f"{len(asked)} asked; {report}")
    s.check("a name with a * before the @ takes any name starting so (no-reply-support@)",
            m.sender_on_list("no-reply-support@notifications.pennylane.com",
                             m.inbox_never_flag_entries(conn)) == "no-reply*@")
    s.check("and only those", m.sender_on_list("noreen@guest.example", ["no-reply*@"]) is None)

    s.section("If Microsoft will not send the headers, the inboxes are still read")
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.commit()
    res, exp, sent = _fixture()
    with FakeGraph(mailboxes=[RES, EXP]) as fake:
        fake.inbox = {RES: res, EXP: exp}
        fake.sent = {RES: sent}
        plain_get = fake.get

        def refuse_headers(path, token, params=None, headers=None):
            if "internetMessageHeaders" in (params or {}).get("$select", ""):
                return fake._fail(504, "ErrorTimeout", "The request timed out")
            return plain_get(path, token, params, headers)
        m.graph_get = refuse_headers
        report = _scan(conn, fake)
    senders = sorted(f["from_address"] for f in _open(conn))
    s.check("both inboxes are still read", "across 2 mailbox(es)" in report and "could not read" not in report,
            detail=report)
    s.check("and the scan says which were read without their headers, and why",
            "judged by sender only" in report and "ErrorTimeout" in report, detail=report)
    s.check("the never-flag list and the house's own mail still hold without them",
            not {"notifications@stripe.com", "reservations@send.chateaugudanes.com", EXP} & set(senders)
            and {"zzif.guesta@guest.example", "zzif.guestb@guest.example",
                 "zzif.guestc@guest.example"} <= set(senders), detail=str(senders))

    s.section("Older flags are read again for their headers, a few a scan")
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    old_flag("letter", "hello@a-shop.example", "Our winter catalogue", unanswered=1)
    old_flag("person", "zzif.person@guest.example", "A real question", unanswered=1)
    conn.commit()
    with FakeGraph(mailboxes=[RES]) as fake:
        fake.inbox = {RES: [
            message(f"{TAG}-old-letter", frm="hello@a-shop.example", to=[RES],
                    subject=f"{TAG} Our winter catalogue", when=_when(400),
                    headers={"List-Unsubscribe": "<mailto:stop@a-shop.example>"}),
            message(f"{TAG}-old-person", frm="zzif.person@guest.example", to=[RES],
                    subject=f"{TAG} A real question", when=_when(400))]}
        # Older than the scan's window, so the list call does not return them.
        fake_inbox, fake.inbox = fake.inbox, {}
        original_get = fake.get

        def get(path, token, params=None, headers=None):
            if "/messages/" in path and "internetMessageHeaders" in (params or {}).get("$select", ""):
                fake.inbox = fake_inbox
                try:
                    return original_get(path, token, params, headers)
                finally:
                    fake.inbox = {}
            return original_get(path, token, params, headers)
        m.graph_get = get
        report = _scan(conn, fake)
    letter = conn.execute("SELECT * FROM email_flags WHERE graph_message_id = ?", (f"{TAG}-old-letter",)).fetchone()
    person = conn.execute("SELECT * FROM email_flags WHERE graph_message_id = ?", (f"{TAG}-old-person",)).fetchone()
    s.check("an older flag that turns out to be a mailing is closed",
            letter["status"] == "dismissed" and letter["closed_by_app"] == "mailing", detail=f"{dict(letter)}; {report}")
    s.check("a person's stays open, and is not read again",
            person["status"] == "open" and person["rules_checked_at"], detail=dict(person))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
