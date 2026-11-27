"""Inbox Flags that can be believed (brief of 26 Nov 2026).

  "ANSWERED" MISSES REPLIES SENT FROM A SHARED INBOX. Lou Steiner's wedding
  enquiry to experience@ stayed "Unanswered" with two replies to it sitting in
  the Sent Items of the owner's own accounts@: Exchange keeps what is sent AS
  a shared inbox in the sender's own mailbox. The scan read each inbox's Sent
  Items for that inbox alone, so 203 flags were open against 35 resolved.

    - every inbox's Sent Items are read, as ONE map for the scan: a sent email
      answers an inbox email when it shares the conversation, or its
      In-Reply-To / References carry the email's own Message-ID;
    - a forward between the house's own inboxes is not an answer;
    - an open flag older than the scan's window is closed by a reply too;
    - the first scan after this reads back to the oldest open flag, once,
      and the page says what it closed;
    - the page names the house's people whose own mailboxes are not read, and
      prints the one-off Exchange fix for each shared inbox, to run by hand.

  GUESTS FIRST, THE REST AFTER. Every flag says who wrote it: a guest or an
  enquiry, a partner or the press, or everything else. What the app knows
  first, then the address, then the words. The page opens on guests, oldest
  first; only guests are escalated, counted as waiting, or popped up.

Nothing reaches Microsoft: tests/_graph_double.py plays its part.
"""
from datetime import timedelta

from _graph_double import FakeGraph, message
from _harness import Suite, clients, db, flashes, keep_rows
import _harness

m = _harness.m
TAG = "ZZIR"
EXP = "zzir-experience@zzhouse.invalid"
ACC = "zzir-accounts@zzhouse.invalid"
PERSON = "zzir.karina@zzhouse.invalid"


def _when(hours_ago):
    return (m.datetime.now(m.timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cleanup(conn):
    conn.execute("DELETE FROM email_flags WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox IN (?, ?)", (EXP, ACC))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM guests WHERE email LIKE 'zzir.%'")
    conn.execute("DELETE FROM users WHERE email = ?", (PERSON,))
    conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)",
                 (m.INBOX_SCAN_STATE_KEY, m.INBOX_SENT_EVERYWHERE_KEY))
    conn.commit()


def _sent(sid, *, conversation, when, to, frm, refs=None, subject="Re: your email"):
    """One sent email as Graph lists a Sent Items folder: who it went to, who
    it was sent as, and (refs) the In-Reply-To / References a sent item keeps
    as extended properties."""
    out = {"id": f"{TAG}-{sid}", "conversationId": conversation, "sentDateTime": when,
           "subject": subject, "bodyPreview": "Thank you for writing.",
           "from": {"emailAddress": {"address": frm}},
           "toRecipients": [{"emailAddress": {"address": a}} for a in to]}
    if refs:
        out["singleValueExtendedProperties"] = [
            {"id": "String 0x1042", "value": refs[0]},
            {"id": "String 0x1039", "value": " ".join(refs)}]
    return out


def _scan(conn):
    with m.app.test_request_context("/"):
        return m.run_email_inbox_scan_job(conn)


def _flag(conn, sender):
    return conn.execute("SELECT * FROM email_flags WHERE from_address = ? ORDER BY id DESC",
                        (sender,)).fetchone()


def run():
    s = Suite("inbox replies from every inbox, and who wrote it")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    put_state_back = keep_rows(conn, "app_settings", "key IN (?, ?)",
                               (m.INBOX_SCAN_STATE_KEY, m.INBOX_SENT_EVERYWHERE_KEY))
    stamp = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute("""INSERT INTO mailbox_routing (mailbox, label, active, created_at)
                    VALUES (?, 'ZZIR Experience', 1, ?), (?, 'ZZIR Accounts', 1, ?)""",
                 (EXP, stamp, ACC, stamp))
    conn.commit()
    try:
        _replies(s, oc, conn, owner)
        _who_wrote(s, oc, conn)
    finally:
        _cleanup(conn)
        put_state_back()
        conn.close()
    return s


def _replies(s, oc, conn, owner):
    old = _when(30)
    lou = message(f"{TAG}-lou", frm="zzir.lou@guest.example", to=[EXP],
                  subject=f"{TAG} Wedding enquiry - late August 2027", when=old,
                  conversation=f"{TAG}-c-lou", internet_id=f"<{TAG}-lou@guest.example>")
    ref = message(f"{TAG}-ref", frm="zzir.ref@guest.example", to=[EXP],
                  subject=f"{TAG} Two rooms for a weekend in May", when=old,
                  conversation=f"{TAG}-c-ref-exp", internet_id=f"<{TAG}-ref@guest.example>")
    fwd = message(f"{TAG}-fwd", frm="zzir.fwd@guest.example", to=[EXP],
                  subject=f"{TAG} A question about the atelier dates", when=old,
                  conversation=f"{TAG}-c-fwd", internet_id=f"<{TAG}-fwd@guest.example>")
    alone = message(f"{TAG}-alone", frm="zzir.alone@guest.example", to=[EXP],
                    subject=f"{TAG} Are dogs allowed in the rooms?", when=old,
                    conversation=f"{TAG}-c-alone", internet_id=f"<{TAG}-alone@guest.example>")
    # In accounts@'s Sent Items, sent AS experience@ -- where Exchange keeps it.
    acc_sent = [
        _sent("r-lou", conversation=f"{TAG}-c-lou", when=_when(20), to=["zzir.lou@guest.example"],
              frm=EXP),
        # A reply with a conversation of its own (written to another copy),
        # joined to the email only by the Message-ID it answers.
        _sent("r-ref", conversation=f"{TAG}-c-ref-acc", when=_when(20), to=["zzir.ref@guest.example"],
              frm=EXP, refs=[f"<{TAG}-ref@guest.example>"]),
        # "Can you take this one?" -- to the house's own inbox only.
        _sent("r-fwd", conversation=f"{TAG}-c-fwd", when=_when(20), to=[EXP], frm=ACC,
              subject=f"Fw: {TAG} A question about the atelier dates"),
    ]

    s.section("A reply kept in another inbox's Sent Items answers the email")
    with FakeGraph(mailboxes=[EXP, ACC]) as fake:
        fake.inbox = {EXP: [lou, ref, fwd, alone], ACC: []}
        fake.sent = {EXP: [], ACC: acc_sent}
        report = _scan(conn)
        lou_flag, ref_flag = _flag(conn, "zzir.lou@guest.example"), _flag(conn, "zzir.ref@guest.example")
        s.check("the wedding enquiry answered from accounts@ as experience@ is not flagged",
                lou_flag is None or lou_flag["status"] != "open", detail=f"{lou_flag and dict(lou_flag)}; {report}")
        s.check("nor the one whose answer names it in In-Reply-To, in a conversation of its own",
                ref_flag is None or ref_flag["status"] != "open", detail=f"{ref_flag and dict(ref_flag)}; {report}")
        f = _flag(conn, "zzir.fwd@guest.example")
        s.check("a forward to the house's own inbox is not an answer: still flagged",
                f is not None and f["status"] == "open" and f["unanswered"] == 1, detail=report)
        a = _flag(conn, "zzir.alone@guest.example")
        s.check("and an email nobody answered anywhere is flagged", a is not None and a["status"] == "open")
        state = m.inbox_scan_state(conn)
        s.check("the scan keeps what it found sent as one inbox and kept in another",
                state and state.get("sent_as", {}).get(EXP, {}).get(ACC) == 2, detail=str(state))
        s.check("and says so in its report",
                f"2 as {EXP} in {ACC}" in report, detail=report)
        asked = [c for c in fake.calls if c[1].endswith("/mailFolders/sentitems/messages")]
        s.check("every inbox's Sent Items were asked for",
                {c[1].split("/")[2] for c in asked} == {EXP, ACC}, detail=str([c[1] for c in asked]))
        s.check("with the two headers a reply carries, as a sent item keeps them",
                all("String 0x1042" in c[2].get("$expand", "") and "String 0x1039" in c[2].get("$expand", "")
                    for c in asked), detail=str([c[2] for c in asked]))

        s.section("An open flag older than the window, answered since, is closed")
        conn.execute(
            """INSERT INTO email_flags (graph_message_id, conversation_id, internet_message_id, from_address,
                   from_name, subject, received_at, unanswered, status, mailbox, sender_kind, created_at, updated_at)
               VALUES (?, ?, ?, 'zzir.old@guest.example', 'Old Guest', ?, ?, 1, 'open', ?, 'guest', ?, ?)""",
            (f"{TAG}-old", f"{TAG}-c-old", f"<{TAG}-old@guest.example>", f"{TAG} A stay in the spring",
             _when(24 * 40), EXP, _when(24 * 40), _when(24 * 40)))
        conn.commit()
        fake.sent[ACC] = acc_sent + [_sent("r-old", conversation=f"{TAG}-c-other", when=_when(3),
                                           to=["zzir.old@guest.example"], frm=EXP,
                                           refs=[f"<{TAG}-old@guest.example>"])]
        report = _scan(conn)
        old_flag = _flag(conn, "zzir.old@guest.example")
        s.check("it is closed as answered, by the app",
                old_flag["status"] == "resolved" and old_flag["closed_by_app"] == "answered"
                and old_flag["first_reply_at"] and old_flag["resolved_by_user_id"] is None,
                detail=f"{dict(old_flag)}; {report}")

        s.section("The first scan reads back to the oldest open flag, once")
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.INBOX_SENT_EVERYWHERE_KEY,))
        conn.execute("UPDATE email_flags SET status = 'open', closed_by_app = NULL, unanswered = 1, "
                     "first_reply_at = NULL, resolved_at = NULL WHERE graph_message_id = ?", (f"{TAG}-old",))
        conn.commit()
        fake.calls.clear()
        _scan(conn)
        filters = [c[2].get("$filter", "") for c in fake.calls
                   if c[1].endswith("/mailFolders/sentitems/messages")]
        window = m.datetime.now(m.timezone.utc) - timedelta(days=30)
        def _since(f):
            return m.parse_datetime_iso(f.rsplit(" ", 1)[-1])
        s.check("its Sent Items go back past the window, to the oldest open flag",
                filters and all(_since(f) and _since(f) < window for f in filters), detail=str(filters))
        note = m.inbox_sent_everywhere_note(conn)
        s.check("what it closed is kept", note and note.get("answered", 0) >= 1, detail=str(note))
        page = oc.get("/admin/inbox-flags").get_data(as_text=True)
        s.check("and the page says it: closed N answered since",
                "the first scan to read every inbox" in page and f"closed {note['answered']} answered since" in page)
        fake.calls.clear()
        _scan(conn)
        filters = [c[2].get("$filter", "") for c in fake.calls
                   if c[1].endswith("/mailFolders/sentitems/messages")]
        s.check("the next scan reads the window only",
                filters and all(_since(f) and _since(f) > window for f in filters), detail=str(filters))

        s.section("Sent Items that cannot be read are named, never read as empty")
        fake.refuse_mailboxes[ACC] = 403
        report = _scan(conn)
        state = m.inbox_scan_state(conn)
        s.check("the scan names the mailbox and Microsoft's reason",
                f"could not read {ACC}" in report and "403" in report, detail=report)
        s.check("and keeps it for the owner home",
                state and any(u["mailbox"] == ACC and "403" in u["why"] for u in state["unread"]),
                detail=str(state))
        page = oc.get("/admin/inbox-flags").get_data(as_text=True)
        s.check("which the page says, so a quiet list is not taken for nothing waiting",
                "data-unread-mailboxes" in page and ACC in page)
        del fake.refuse_mailboxes[ACC]
        _scan(conn)
        page = oc.get("/admin/inbox-flags").get_data(as_text=True)
        s.check("and stops saying once it is read again", "data-unread-mailboxes" not in page)

    s.section("The house's own mailboxes the reader does not read, and the fix")
    conn.execute("INSERT INTO users (name, email, password_hash, role, status, created_at) "
                 "VALUES ('ZZIR Karina', ?, '!', 'employee', 'active', ?)", (PERSON, _when(1)))
    conn.commit()
    with FakeGraph(mailboxes=[EXP, ACC]) as fake:
        page = oc.get("/admin/inbox-flags").get_data(as_text=True)
        s.check("somebody at the house whose own mailbox is not read is named",
                "data-sent-as" in page and "ZZIR Karina" in page and PERSON in page)
        s.check("with the one-off Exchange fix for each shared inbox, to run by hand",
                f"Set-Mailbox {EXP} -MessageCopyForSentAsEnabled $true "
                "-MessageCopyForSendOnBehalfEnabled $true" in page)
        people = [p["address"] for p in m.inbox_people_not_read(conn, [EXP, ACC])]
        s.check("an inbox that is read is not named as unread", EXP not in people and ACC not in people)


def _who_wrote(s, oc, conn):
    old = _when(60)
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES ('ZZIR Known', 'zzir.known@zzcorp.example', ?)",
                 (_when(1),))
    conn.commit()
    msgs = [
        # Known to the app: a guest on file, writing a subject that says nothing.
        message(f"{TAG}-k1", frm="zzir.known@zzcorp.example", to=[EXP], subject=f"{TAG} Hello", when=old,
                conversation=f"{TAG}-k1"),
        # A receipt from a shop, no List-Unsubscribe for the rules to see.
        message(f"{TAG}-k2", frm="hello@zzir-boutique.example", to=[EXP],
                subject=f"{TAG} Votre facture n°48213", when=old, conversation=f"{TAG}-k2"),
        # A delivery from a shop the house knows by name.
        message(f"{TAG}-k3", frm="orders@temu.com", to=[EXP], subject=f"{TAG} Your package has shipped",
                when=old, conversation=f"{TAG}-k3"),
        # The press.
        message(f"{TAG}-k4", frm="jo@zzir-magazine.example", to=[EXP],
                subject=f"{TAG} A feature for our travel magazine", when=old, conversation=f"{TAG}-k4"),
        # A person writing from their own address, about a room.
        message(f"{TAG}-k5", frm="zzir.someone@gmail.com", to=[EXP],
                subject=f"{TAG} Do you have a room for two nights?", when=old, conversation=f"{TAG}-k5"),
    ]
    with FakeGraph(mailboxes=[EXP, ACC]) as fake:
        fake.inbox = {EXP: msgs, ACC: []}
        fake.sent = {EXP: [], ACC: []}
        conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
        conn.commit()
        _scan(conn)
    kinds = {r["from_address"]: (r["sender_kind"], r["sender_kind_why"]) for r in conn.execute(
        "SELECT from_address, sender_kind, sender_kind_why FROM email_flags WHERE subject LIKE ? "
        "AND status = 'open'", (f"{TAG}%",))}

    s.section("Who wrote it")
    s.check("a message from a known guest is Guest or enquiry, because the app knows them",
            kinds.get("zzir.known@zzcorp.example") == ("guest", "a guest on file"), detail=str(kinds))
    s.check("a receipt from a shop with no List-Unsubscribe is Everything else",
            (kinds.get("hello@zzir-boutique.example") or ("",))[0] == "other", detail=str(kinds))
    s.check("so is a delivery from a shop the house knows by name",
            (kinds.get("orders@temu.com") or ("",))[0] == "other", detail=str(kinds))
    s.check("the press is Partner or press",
            (kinds.get("jo@zzir-magazine.example") or ("",))[0] == "partner", detail=str(kinds))
    s.check("a person asking about a room is Guest or enquiry",
            (kinds.get("zzir.someone@gmail.com") or ("",))[0] == "guest", detail=str(kinds))
    pinged = [r["title"] for r in conn.execute("SELECT title FROM notifications WHERE title LIKE ?",
                                               (f"%{TAG}%",))]
    s.check("nobody is told of a new receipt or delivery",
            not any("facture" in t or "package" in t for t in pinged)
            and any("Hello" in t for t in pinged), detail=str(pinged))

    s.section("The page opens on guests, oldest first")
    page = oc.get("/admin/inbox-flags").get_data(as_text=True)
    s.check("the default view shows the guests",
            f"{TAG} Hello" in page and f"{TAG} Do you have a room" in page)
    s.check("and not the receipt, the delivery or the press",
            "Votre facture" not in page and "Your package" not in page and "travel magazine" not in page)
    s.check("waiting longest first",
            page.index(f"{TAG} Hello") < page.index(f"{TAG} Do you have a room")
            if f"{TAG} Hello" in page and f"{TAG} Do you have a room" in page else False)
    rest = oc.get("/admin/inbox-flags?from=other").get_data(as_text=True)
    s.check("everything else is a chip away", "Votre facture" in rest and "Your package" in rest)
    s.check("each says why", "a guest on file" in page and "reads as a receipt" in rest)

    s.section("Only guests are escalated or counted as waiting")
    conn.execute("UPDATE email_flags SET escalated_at = NULL, received_at = ? WHERE subject LIKE ?",
                 (_when(24 * 5), f"{TAG}%"))
    # Anybody else's open flags in this database are held out of the nudge,
    # and put back as they were.
    others = [r[0] for r in conn.execute("SELECT id FROM email_flags WHERE status = 'open' "
                                         "AND subject NOT LIKE ? AND escalated_at IS NULL", (f"{TAG}%",))]
    put_back = (keep_rows(conn, "email_flags", f"id IN ({','.join('?' * len(others))})", tuple(others))
                if others else (lambda: None))
    conn.execute(f"UPDATE email_flags SET escalated_at = ? WHERE id IN ({','.join('?' * len(others))})",
                 (_when(1), *others)) if others else None
    conn.commit()
    try:
        m.escalate_stale_flags(conn)
    finally:
        put_back()
    esc = {r["from_address"]: r["escalated_at"] for r in conn.execute(
        "SELECT from_address, escalated_at FROM email_flags WHERE subject LIKE ? AND status = 'open'",
        (f"{TAG}%",))}
    s.check("a Nespresso invoice is never escalated: the receipt and the delivery are not",
            not esc.get("hello@zzir-boutique.example") and not esc.get("orders@temu.com"), detail=str(esc))
    s.check("nor the press", not esc.get("jo@zzir-magazine.example"), detail=str(esc))
    s.check("a guest waiting five days is", bool(esc.get("zzir.known@zzcorp.example")), detail=str(esc))
    with m.app.test_request_context("/"):
        n_open = conn.execute("SELECT COUNT(*) FROM email_flags WHERE status = 'open' "
                              "AND COALESCE(sender_kind, 'guest') = 'guest'").fetchone()[0]
    tiles = page.split('class="stat-tile-value">')[1].split("<")[0]
    s.check("the tile counts the guests open, not the rest", tiles == str(n_open), detail=f"{tiles} vs {n_open}")

    s.section("A person can say who wrote it, and the scan keeps it")
    rec = _flag(conn, "hello@zzir-boutique.example")
    r = oc.post(f"/admin/inbox-flags/{rec['id']}/kind", data={"kind": "guest"}, follow_redirects=True)
    rec = _flag(conn, "hello@zzir-boutique.example")
    s.check("moved to Guest or enquiry, and who did it kept",
            rec["sender_kind"] == "guest" and rec["sender_kind_by_user_id"]
            and (rec["sender_kind_why"] or "").startswith("set by"), response=r, detail=dict(rec))
    mover = conn.execute("SELECT name FROM users WHERE id = ?", (rec["sender_kind_by_user_id"],)).fetchone()
    page = oc.get("/admin/inbox-flags").get_data(as_text=True)
    s.check("the page names who moved it, from their record",
            mover and f"set by hand ({mover['name']})" in page, detail=mover and mover["name"])
    with FakeGraph(mailboxes=[EXP, ACC]) as fake:
        fake.inbox = {EXP: msgs, ACC: []}
        fake.sent = {EXP: [], ACC: []}
        _scan(conn)
    rec = _flag(conn, "hello@zzir-boutique.example")
    s.check("and the next scan leaves it as the person said", rec["sender_kind"] == "guest", detail=dict(rec))
    r = oc.post(f"/admin/inbox-flags/{rec['id']}/kind", data={"kind": "nonsense"}, follow_redirects=True)
    s.check("a kind that is not one is refused, and nothing changes",
            any("not one of the kinds" in f for f in flashes(r))
            and _flag(conn, "hello@zzir-boutique.example")["sender_kind"] == "guest", response=r)
    s.check("a flag that does not exist is a 404",
            oc.post("/admin/inbox-flags/999999999/kind", data={"kind": "guest"}).status_code == 404)


if __name__ == "__main__":
    print(run().report())
