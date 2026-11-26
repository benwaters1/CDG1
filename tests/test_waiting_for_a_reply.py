""""Waiting for a reply" on the owner home (brief of 26 Nov 2026).

All three Microsoft 365 pieces were connected and running, and none of them
reached the owner home: 203 open flags on Inbox Flags, a Booking.com guest
waiting for an answer, and the page the owner opens every morning said
nothing about any of it.

  - Up to five guests or enquirers waiting longest, one line each: name, what
    they asked in a few words, which inbox, how long, Open in Outlook. Then
    "and N more" to Inbox Flags.
  - Booking.com's waiting guests, with the days left of Booking.com's 14.
  - Nothing from everything else: a receipt is never here.
  - An inbox the last scan could not read, named, so silence is never taken
    for "nothing waiting".
  - Nothing to tick: answered, a line leaves at the next scan.
  - And when there is nothing, no panel at all: a panel that can never be
    empty becomes furniture.
"""
import json
import re
from datetime import timedelta

from _graph_double import FakeGraph, message
from _harness import Suite, clients, db, keep_rows
import _harness

m = _harness.m
TAG = "ZZWR"
BOX = "zzwr-reservations@zzhouse.invalid"
OTHER_BOX = "zzwr-accounts@zzhouse.invalid"


def _when(hours_ago):
    return (m.datetime.now(m.timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flag(conn, key, sender, subject, *, kind="guest", hours=72, unanswered=1, name=None,
          conversation=None, internet_id=None):
    conn.execute(
        """INSERT INTO email_flags (graph_message_id, conversation_id, internet_message_id, from_name,
               from_address, subject, preview, web_link, received_at, unanswered, status, mailbox,
               sender_kind, sender_kind_why, escalated_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, 'test', ?, ?, ?)""",
        (f"{TAG}-{key}", conversation or f"{TAG}-c-{key}", internet_id, name, sender,
         f"{subject} {TAG}", "Bonjour, we were hoping to ask about the dates.",
         f"https://outlook.office.com/mail/{TAG}-{key}", _when(hours), unanswered, BOX, kind,
         _when(1), _when(hours), _when(hours)))


def _home(oc):
    return oc.get("/").get_data(as_text=True)


def _panel(page):
    if 'id="replies"' not in page:
        return ""
    return page.split('id="replies"', 1)[1].split("<!-- The day", 1)[0]


def run():
    s = Suite("waiting for a reply, on the owner home")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    # Anything else waiting in this database is held out, and put back after.
    keep = [
        keep_rows(conn, "email_flags", "status = 'open' AND subject NOT LIKE ?", (f"%{TAG}%",)),
        keep_rows(conn, "ota_mail", "kind = 'message' AND handled_at IS NULL"),
        keep_rows(conn, "app_settings", "key = ?", (m.INBOX_SCAN_STATE_KEY,)),
    ]
    conn.execute("DELETE FROM email_flags WHERE status = 'open'")
    conn.execute("DELETE FROM ota_mail WHERE kind = 'message' AND handled_at IS NULL")
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.INBOX_SCAN_STATE_KEY,))
    stamp = _when(1)
    conn.execute("INSERT OR IGNORE INTO mailbox_routing (mailbox, label, active, created_at) "
                 "VALUES (?, 'ZZWR Reservations', 1, ?), (?, 'ZZWR Accounts', 1, ?)",
                 (BOX, stamp, OTHER_BOX, stamp))
    conn.commit()
    try:
        _sections(s, oc, conn)
    finally:
        conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM ota_mail WHERE source_id LIKE ?", (f"{TAG}%",))
        conn.execute("DELETE FROM mailbox_routing WHERE mailbox IN (?, ?)", (BOX, OTHER_BOX))
        conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
        conn.commit()
        for put_back in keep:
            put_back()
        conn.close()
    return s


def _sections(s, oc, conn):
    s.section("Nothing waiting: no panel at all")
    page = _home(oc)
    s.check("the owner home draws no \"Waiting for a reply\" when there is nothing",
            "Waiting for a reply" not in page and 'id="replies"' not in page)
    s.check("and the data says empty", m.owner_home_replies(conn)["empty"])

    s.section("A guest waiting is there; a receipt is not")
    _flag(conn, "lou", "zzwr.lou@guest.example",
          "Re: Wedding enquiry – Late August / Early September 2027 for about eighty guests",
          name="ZZWR Lou Steiner", hours=24 * 10)
    _flag(conn, "receipt", "factures@zzwr-shop.example", "Votre facture n°4471", kind="other",
          name="ZZWR Shop", hours=24 * 12)
    conn.commit()
    page = _home(oc)
    panel = _panel(page)
    s.check("the panel is drawn, under its heading", "Waiting for a reply" in page and panel)
    s.check("the guest is named", "ZZWR Lou Steiner" in panel, detail=panel[:400])
    s.check("with what they asked, in a few words, without the Re:",
            "Wedding enquiry" in panel and "Re: " not in panel and "…" in panel, detail=panel[:600])
    s.check("which inbox", "ZZWR Reservations" in panel)
    s.check("how long", "10 days" in panel)
    s.check("and Open in Outlook, to the email itself",
            "Open in Outlook" in panel and f"https://outlook.office.com/mail/{TAG}-lou" in panel)
    s.check("the receipt is not there", "ZZWR Shop" not in panel and "facture" not in panel)
    s.check("the greeting says it too",
            "1 guest is waiting for a reply" in page, detail=page.split('class="oh-sub">', 1)[-1][:200])

    s.section("Five waiting longest, then and N more")
    for i in range(6):
        _flag(conn, f"g{i}", f"zzwr.g{i}@guest.example", f"A room for June, guest {i}",
              name=f"ZZWR Guest {i}", hours=24 * (2 + i))
    conn.commit()
    panel = _panel(_home(oc))
    shown = [n for n in ["ZZWR Lou Steiner"] + [f"ZZWR Guest {i}" for i in range(6)] if n in panel]
    s.check("five are shown", len(shown) == 5, detail=str(shown))
    s.check("the ones waiting longest", "ZZWR Lou Steiner" in shown and "ZZWR Guest 5" in shown
            and "ZZWR Guest 0" not in shown and "ZZWR Guest 1" not in shown, detail=str(shown))
    s.check("oldest first", panel.index("ZZWR Lou Steiner") < panel.index("ZZWR Guest 5")
            < panel.index("ZZWR Guest 2") if len(shown) == 5 else False)
    s.check("and N more, to Inbox Flags", "and 2 more waiting" in panel and "/admin/inbox-flags" in panel,
            detail=panel[-400:])
    conn.execute("DELETE FROM email_flags WHERE graph_message_id LIKE ?", (f"{TAG}-g%",))
    conn.commit()

    s.section("Booking.com's waiting guests, with the days left of its fourteen")
    conn.execute(
        """INSERT INTO ota_mail (channel, source, source_id, received_at, from_address, kind, subject,
               guest_name, message_text, from_booking_com, created_at)
           VALUES ('booking.com', 'test', ?, ?, 'zzwr@guest.booking.com', 'message',
                   'We received this message from ZZWR Booker', 'ZZWR Booker',
                   'Could we arrive at ten in the evening?', 1, ?)""",
        (f"{TAG}-bcom", _when(24 * 10 + 1), _when(24 * 10)))
    conn.commit()
    page = _home(oc)
    panel = _panel(page)
    s.check("the Booking.com guest is there", "ZZWR Booker" in panel and "Booking.com" in panel)
    s.check("with what they wrote, not Booking.com's own subject",
            "Could we arrive at ten" in panel and "We received this message" not in panel)
    s.check("with the days left: ten days gone of fourteen is four",
            re.search(r">\s*4 days left\s*<", panel) is not None, detail=panel[-600:])
    s.check("and Answer, to the Booking.com page", "/management/booking-com" in panel)
    nobody = page.split('id="nobody-told"', 1)[1].split("oh-next", 1)[0] if 'id="nobody-told"' in page else ""
    s.check("said once: not also under \"Nobody has told you this\"",
            "Booking.com guests waiting for an answer" not in nobody)
    s.check("the greeting counts both", "2 guests are waiting for a reply" in page)
    conn.execute("DELETE FROM ota_mail WHERE source_id = ?", (f"{TAG}-bcom",))
    conn.commit()

    s.section("An inbox the last scan could not read is named")
    with FakeGraph(mailboxes=[BOX, OTHER_BOX]) as fake:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                     (m.INBOX_SCAN_STATE_KEY, json.dumps({
                         "at": m.datetime.now(m.timezone.utc).isoformat(),
                         "unread": [{"mailbox": BOX, "folder": "Inbox",
                                     "why": "Microsoft answered 403 (ErrorAccessDenied: denied)"}],
                         "read": [OTHER_BOX], "sent_as": {}, "notes": []})))
        conn.commit()
        panel = _panel(_home(oc))
        s.check("the mailbox is named, with Microsoft's reason",
                f"{BOX} (Inbox) could not be read" in panel and "403" in panel, detail=panel[:600])
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                     (m.INBOX_SCAN_STATE_KEY, json.dumps({
                         "at": (m.datetime.now(m.timezone.utc) - timedelta(hours=5)).isoformat(),
                         "unread": [], "read": [BOX, OTHER_BOX], "sent_as": {}, "notes": []})))
        conn.commit()
        panel = _panel(_home(oc))
        s.check("and a scan that has stopped running is said too",
                "The inboxes are not being read" in panel and "has not run since" in panel, detail=panel[:600])

        s.section("Nothing to tick: answered, the guest leaves at the next scan")
        conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
        conn.commit()
        mine = message(f"{TAG}-tick", frm="zzwr.tick@guest.example", to=[BOX],
                       subject=f"{TAG} Two nights in March for our anniversary", when=_when(30),
                       conversation=f"{TAG}-c-tick", internet_id=f"<{TAG}-tick@guest.example>")
        fake.inbox = {BOX: [mine], OTHER_BOX: []}
        fake.sent = {BOX: [], OTHER_BOX: []}
        with m.app.test_request_context("/"):
            m.run_email_inbox_scan_job(conn)
        panel = _panel(_home(oc))
        s.check("waiting, the guest is on the panel", "Two nights in March" in panel, detail=panel[:400])
        fake.sent[OTHER_BOX] = [{
            "id": f"{TAG}-reply", "conversationId": f"{TAG}-c-tick", "sentDateTime": _when(2),
            "subject": "Re: Two nights in March", "bodyPreview": "With pleasure.",
            "from": {"emailAddress": {"address": BOX}},
            "toRecipients": [{"emailAddress": {"address": "zzwr.tick@guest.example"}}]}]
        with m.app.test_request_context("/"):
            m.run_email_inbox_scan_job(conn)
        page = _home(oc)
        s.check("answered from another inbox's Sent Items, the guest is gone without a tick",
                "Two nights in March" not in _panel(page), detail=_panel(page)[:400])
        s.check("and with nothing else waiting, so is the panel", 'id="replies"' not in page)


if __name__ == "__main__":
    print(run().report())
