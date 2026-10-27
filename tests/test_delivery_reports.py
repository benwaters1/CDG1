"""What became of a letter after it left, and the addresses the house stops writing to.

WHAT WAS MISSING. "Sent" in the mail log meant Resend had accepted a letter,
and nothing ever came back: a confirmation to a mistyped address looked exactly
like one the guest read, and the house went on writing to dead mailboxes --
which is how a sender's mail ends up in everybody's spam folder.

WHAT THIS PINS.
  - Resend's reports are believed only when signed (Svix's scheme) and fresh.
  - Each report lands on its own letter's line, by the id Resend gave it.
  - A bounce that will not recover, and a spam complaint, stop the address at
    send_email's one door, until somebody chooses to write to it again; a
    temporary bounce does not.
  - A campaign tells kept from failed and from stopped, one send is one line,
    and the send's own page names who got it and who did not.
  - Guest email says whether mail is going out, in words; the mail log names
    its buckets honestly and never shows a bare id; the wording page says
    whether each letter is actually going out.

Nothing here reaches Resend: the network call is stood in for.
"""
import base64
import hashlib
import hmac
import json
import time

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZDR"
SECRET = "whsec_" + base64.b64encode(b"a test signing key, not a real one").decode()


def _sign(body, secret=SECRET, stamp=None, msg_id="msg_test"):
    stamp = str(int(stamp if stamp is not None else time.time()))
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + body,
                                    hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": f"v1,{sig}"}


def _event(kind, provider_id, **data):
    return json.dumps({"type": kind, "created_at": "2026-10-12T10:00:00Z",
                       "data": dict(data, email_id=provider_id)}).encode()


def _line(provider_id, to):
    conn = db()
    conn.execute(
        """INSERT INTO mail_log (to_address, subject, status, kept, provider_id, created_at,
           delivered_at) VALUES (?, 'A letter', 'sent', 'here', ?, ?, ?)""",
        (to, provider_id, _harness.datetime_now(), _harness.datetime_now()))
    conn.commit()
    row = conn.execute("SELECT * FROM mail_log WHERE provider_id = ?", (provider_id,)).fetchone()
    conn.close()
    return row


def _row(provider_id):
    conn = db()
    row = conn.execute("SELECT * FROM mail_log WHERE provider_id = ?", (provider_id,)).fetchone()
    conn.close()
    return row


def _stopped(address):
    conn = db()
    row = conn.execute("SELECT * FROM email_suppressions WHERE email = ? AND cleared_at IS NULL",
                       (address,)).fetchone()
    conn.close()
    return row


def _cleanup():
    conn = db()
    like = TAG.lower() + "%"
    conn.execute("DELETE FROM mail_log WHERE to_address LIKE ? OR provider_id LIKE ?",
                 (like, TAG + "%"))
    conn.execute("DELETE FROM email_suppressions WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE ?", (like,))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (like,))
    conn.commit()
    conn.close()


class _Reports:
    """Delivery reports switched on for the block, and off again after."""
    def __enter__(self):
        self.real = m.RESEND_WEBHOOK_SECRET
        m.RESEND_WEBHOOK_SECRET = SECRET
        return self

    def __exit__(self, *exc):
        m.RESEND_WEBHOOK_SECRET = self.real
        return False


def run():
    s = Suite("Delivery reports and the stop list")
    _cleanup()
    oc, ec, _o, _e = clients()
    anon = m.app.test_client()

    s.section("A report is believed only when Resend signed it, just now")
    body = _event("email.delivered", f"{TAG}x")
    s.check("a good signature passes", m.verify_resend_signature(SECRET, _sign(body), body))
    s.check("one made with another secret does not",
            not m.verify_resend_signature(SECRET, _sign(body, secret="whsec_" + base64.b64encode(
                b"somebody else").decode()), body))
    s.check("nor one for a different body",
            not m.verify_resend_signature(SECRET, _sign(body), body + b" "))
    s.check("nor one ten minutes old, which could be a replay",
            not m.verify_resend_signature(SECRET, _sign(body, stamp=time.time() - 600), body))
    s.check("nor a call with no signature at all", not m.verify_resend_signature(SECRET, {}, body))
    many = _sign(body)
    many["svix-signature"] = "v1,bm90IGl0 " + many["svix-signature"]
    s.check("and a header carrying several, one of them right, passes",
            m.verify_resend_signature(SECRET, many, body))

    s.section("The webhook")
    real_secret = m.RESEND_WEBHOOK_SECRET
    m.RESEND_WEBHOOK_SECRET = ""
    s.check("is not there at all until it is set up",
            anon.post("/webhooks/resend", data=body, headers=_sign(body)).status_code == 404)
    m.RESEND_WEBHOOK_SECRET = real_secret
    with _Reports():
        s.check("refuses a call that is not signed",
                anon.post("/webhooks/resend", data=body).status_code == 400)
        to = f"{TAG.lower()}guest@example.invalid"
        _line(f"{TAG}1", to)
        r = anon.post("/webhooks/resend", data=_event("email.delivered", f"{TAG}1"),
                      headers=_sign(_event("email.delivered", f"{TAG}1")))
        s.check("takes a signed one", r.status_code == 204, detail=f"HTTP {r.status_code}")
        s.check("and puts it on the letter's own line",
                (_row(f"{TAG}1") or {})["delivery"] == "delivered")

        late = _event("email.delivery_delayed", f"{TAG}1")
        anon.post("/webhooks/resend", data=late, headers=_sign(late))
        s.check("a late 'delayed' does not undo a delivery",
                _row(f"{TAG}1")["delivery"] == "delivered")

        s.section("A bounce for good stops the address; a temporary one does not")
        soft_to = f"{TAG.lower()}full@example.invalid"
        _line(f"{TAG}2", soft_to)
        soft = _event("email.bounced", f"{TAG}2",
                      bounce={"type": "Transient", "message": "Mailbox full"})
        anon.post("/webhooks/resend", data=soft, headers=_sign(soft))
        s.check("a temporary bounce is a delay, said why",
                _row(f"{TAG}2")["delivery"] == "delayed"
                and "Mailbox full" in (_row(f"{TAG}2")["delivery_detail"] or ""))
        s.check("and the address is still written to", _stopped(soft_to) is None)

        dead_to = f"{TAG.lower()}nobody@example.invalid"
        _line(f"{TAG}3", dead_to)
        hard = _event("email.bounced", f"{TAG}3",
                      bounce={"type": "Permanent", "message": "No such user"})
        anon.post("/webhooks/resend", data=hard, headers=_sign(hard))
        s.check("a permanent bounce is marked on its line",
                _row(f"{TAG}3")["delivery"] == "bounced")
        s.check("and the address goes on the stop list, with why",
                _stopped(dead_to) is not None and _stopped(dead_to)["reason"] == "bounced")

        spam_to = f"{TAG.lower()}cross@example.invalid"
        _line(f"{TAG}4", spam_to)
        spam = _event("email.complained", f"{TAG}4")
        anon.post("/webhooks/resend", data=spam, headers=_sign(spam))
        s.check("a spam complaint stops the address too",
                _stopped(spam_to) is not None and _stopped(spam_to)["reason"] == "complained")

    s.section("The one door refuses an address on the stop list")
    report = {}
    with m.app.app_context():
        went = m.send_email(dead_to, "Hello again", "Body", report=report)
    # With no provider here nothing goes anyway; what shows the door turned it
    # away is that it was not kept to try again, and the reason it gives.
    s.check("it does not go", went is False and report.get("stopped") is True,
            detail=str(report))
    s.check("and says why, in words", report.get("stopped")
            and "bounced" in (report.get("why") or ""), detail=str(report))
    conn = db()
    line = conn.execute("SELECT * FROM mail_log WHERE to_address = ? AND subject = 'Hello again'",
                        (dead_to,)).fetchone()
    held = conn.execute("SELECT 1 FROM email_outbox WHERE to_address = ?", (dead_to,)).fetchone()
    conn.close()
    s.check("the mail log has the letter, as not sent, with the reason",
            line is not None and line["status"] == "failed"
            and "bounced" in (line["failure"] or ""), detail=str(dict(line) if line else None))
    s.check("and it is not kept to go later: it would not go then either", held is None)

    s.section("A campaign tells kept from stopped from failed")
    real_send = m.send_email

    def kept(to, *a, **kw):
        """send_email when it keeps a letter to go later: False, and why."""
        if kw.get("report") is not None:
            kw["report"]["why"] = "held for the test"
        return False

    m.send_email = kept
    try:
        conn = db()
        result = m.send_campaign(conn, {"id": None, "name": f"{TAG} Autumn", "area": "general",
                                        "subject": "s", "body": "b"},
                                 {f"{TAG.lower()}k1@example.invalid": "K", spam_to: "S"}, None)
        conn.close()
    finally:
        m.send_email = real_send
    s.check("a letter send_email kept is counted as kept, not failed",
            result.get("held") == 2 and result["failed"] == 0, detail=str(result))
    s.check("and the send has one id", bool(result.get("send_id")))
    page = oc.get(f"/admin/emails/sends/{result['send_id']}").get_data(as_text=True)
    s.check("its page names each letter and what became of it",
            "Kept, goes when mail can" in page and f"{TAG.lower()}k1@example.invalid" in page)
    hub = oc.get("/admin/emails").get_data(as_text=True)
    s.check("and Guest email lists it as one send, linked",
            f"/admin/emails/sends/{result['send_id']}" in hub)
    # Send now queues and the letters go after it, so what a send did is said
    # on its own page rather than in a line when Send is pressed.
    s.check("what the send says is not a success when nobody has it yet",
            '<div class="overview-label">Sent</div>' not in page)

    s.section("Guest email: is mail going out, in words")
    s.check("the page asks the question", "Is mail going out?" in hub)
    s.check("without the server's setting names in plain view",
            hub.count("RESEND_API_KEY") == 1 and "For whoever set the site up" in hub)
    s.check("with no reports connected, it says what 'Sent' means", "No reports" in hub)
    with _Reports():
        s.check("connected, it says so", "Reporting" in oc.get("/admin/emails").get_data(as_text=True))
    s.check("and says where replies to each kind of letter land",
            "Where replies go" in hub and m.reply_to_header_for("workshops") in hub)

    s.section("Not written to: one list, each with its own way back")
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'asked', ?)",
                 (f"{TAG.lower()}quiet@example.invalid", _harness.datetime_now()))
    conn.commit()
    sup = conn.execute("SELECT id FROM email_suppressions WHERE email = ?", (dead_to,)).fetchone()[0]
    opt = conn.execute("SELECT id FROM email_optouts WHERE email = ?",
                       (f"{TAG.lower()}quiet@example.invalid",)).fetchone()[0]
    conn.close()
    hub = oc.get(f"/admin/emails?q={TAG.lower()}").get_data(as_text=True)
    s.check("the bounced address is listed, as stopping every letter",
            dead_to in hub and "Every letter" in hub)
    s.check("the unsubscribed one, as stopping campaigns only",
            f"{TAG.lower()}quiet@example.invalid" in hub and "Campaigns only" in hub)
    r = oc.post(f"/admin/emails/stopped/{sup}/allow", follow_redirects=True)
    s.check("'Write to them again' takes the address off the stop list",
            _stopped(dead_to) is None and any("written to again" in x for x in flashes(r)))
    oc.post(f"/admin/emails/optout/{opt}/remove")
    conn = db()
    gone = conn.execute("SELECT 1 FROM email_optouts WHERE id = ?", (opt,)).fetchone() is None
    conn.close()
    s.check("and 'They asked to hear again' takes them off the unsubscribe list", gone)
    with _Reports():
        again = _event("email.bounced", f"{TAG}3", bounce={"type": "Permanent"})
        anon.post("/webhooks/resend", data=again, headers=_sign(again))
    s.check("an address allowed again that bounces again goes straight back on",
            _stopped(dead_to) is not None)

    s.section("The mail log: honest buckets, what the provider said, no bare ids")
    from datetime import datetime, timedelta, timezone
    conn = db()
    conn.execute(
        """INSERT INTO mail_log (to_address, subject, status, kept, about_category, about_id,
           created_at) VALUES (?, 'x', 'sent', 'here', 'room', 999999, ?)""",
        (f"{TAG.lower()}gone@example.invalid", _harness.datetime_now()))
    # One from three days ago, so the stretch between today and a week ago is drawn.
    conn.execute(
        """INSERT INTO mail_log (to_address, subject, status, kept, created_at)
           VALUES (?, 'x', 'sent', 'here', ?)""",
        (f"{TAG.lower()}earlier@example.invalid",
         (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()))
    conn.commit()
    conn.close()
    log = oc.get(f"/admin/mail-log?q={TAG.lower()}").get_data(as_text=True)
    s.check("a delivered letter says Delivered", ">Delivered<" in log)
    s.check("a bounced one says Bounced", ">Bounced<" in log)
    s.check("the stretches of time do not overlap and are not named as if they did",
            "Earlier this week" in log and "Last 7 days" not in log and "Today" in log)
    s.check("a booking deleted since is said to be, not shown as a number",
            "#999999" not in log and "since deleted" in log)
    one = _row(f"{TAG}3")
    letter = oc.get(f"/admin/mail-log/{one['id']}").get_data(as_text=True)
    s.check("one letter opens on its own, with the report on it",
            "Bounced" in letter and "Reported" in letter)

    s.section("Email wording says whether each letter is going out")
    words = oc.get("/management/email-templates").get_data(as_text=True)
    s.check("each letter says how often it went lately",
            "in the last 90 days" in words and "Lately" in words)
    s.check("and the list can be narrowed to the ones that did not go out",
            "Did not go out" in words)

    s.section("The go-live checklist knows about delivery reports")
    real_enabled = m.resend_enabled
    m.resend_enabled = lambda: True
    try:
        conn = db()
        names = [c["label"] for c in m.readiness_checks(conn, include_slow=False)]
        conn.close()
    finally:
        m.resend_enabled = real_enabled
    s.check("a line for them, once Resend is the provider", "Delivery reports" in names,
            detail=str(names[:6]))

    s.section("An employee sees none of it")
    for url in ("/admin/emails", f"/admin/emails/sends/{result['send_id']}"):
        s.check(f"{url} is not theirs", ec.get(url).status_code in (302, 403))
    s.check("nor can they take an address off the stop list",
            ec.post(f"/admin/emails/stopped/{sup}/allow").status_code in (302, 403))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
