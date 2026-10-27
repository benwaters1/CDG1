# -*- coding: utf-8 -*-
"""Guest emails: off (owner, 26 October 2026).

"Turn off every automatic email and text to guests (cancel, refund, booking,
changes, reminders) ... but don't turn off booking confirmations." Until the
owner has read each letter, the house writes nothing to a guest by itself
except a booking confirmation; everything else is held in Held email to be
read, and goes only once its wording is approved AND the switch is on.

WHAT THIS PINS, against the REAL gate (the harness lets everything through
for every other suite, so this one puts the real one back):
  - Off by default, with no setting written: the deploy itself switches it off.
  - Off: a cancellation, a refund, a reminder is held, kept, and not sent; a
    booking confirmation goes; a letter a person wrote at the time goes.
  - On: a letter goes only once approved; changing its wording lapses the
    approval; one with no wording cannot be approved; the page names who
    approved it.
  - Texts: an automatic one is held, one typed on a booking is not.
  - A letter with no name at all is held, never guessed into going.
  - "Send all" on Held email never sends one held by this switch.
  - The page lists every stored letter with what sends it and its full text.
"""
from markupsafe import escape

from _harness import Suite, clients, db
import _harness

m = _harness.m
OUTSIDE = "zzge.guest@example.invalid"
PHONE = "+33612340077"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE 'ZZGE%'")
    conn.execute("DELETE FROM guest_messages WHERE subject LIKE 'ZZGE%'")
    conn.execute("DELETE FROM mail_log WHERE subject LIKE 'ZZGE%'")
    conn.execute("DELETE FROM sms_outbox WHERE body LIKE 'ZZGE%'")
    conn.execute("DELETE FROM guest_messages WHERE body LIKE 'ZZGE%'")
    conn.commit()
    conn.close()


def _setting(key, value):
    conn = db()
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()
    conn.close()


def _held(subject):
    conn = db()
    row = conn.execute("SELECT * FROM email_outbox WHERE subject = ? AND sent_at IS NULL",
                       (subject,)).fetchone()
    conn.close()
    return row


def _send(key, subject):
    with m.app.test_request_context("/"):
        return m.send_email(OUTSIDE, subject, "ZZGE body.", template_key=key)


def run():
    s = Suite("Guest emails: off")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    s.check("there is an owner with a name to look for", bool(_owner["name"]))
    sent = []
    conn = db()
    keep = {k: (r["value"] if r else None) for k in ("guest_mail_live", "guest_emails_on")
            for r in [conn.execute("SELECT value FROM app_settings WHERE key = ?",
                                   (k,)).fetchone()]}
    approvals = [dict(r) for r in conn.execute("SELECT * FROM guest_letter_approvals")]
    cancelled = conn.execute("SELECT * FROM email_templates WHERE template_key = 'room_cancelled'"
                             ).fetchone()
    conn.close()
    was = (m.send_email_via_resend, m.resend_enabled, m.guest_letter_held_why,
           m.send_held_letter)
    m.resend_enabled = lambda: True
    m.send_email_via_resend = (
        lambda to, subj, body, ics=None, name=None, html=None, reply_to=None:
        (sent.append({"to": to, "subject": subj}), (True, None))[1])
    m.guest_letter_held_why = _harness.REAL_GUEST_LETTER_HELD_WHY
    try:
        _checks(s, oc, sent, _owner["name"])
    finally:
        (m.send_email_via_resend, m.resend_enabled, m.guest_letter_held_why,
         m.send_held_letter) = was
        for k, v in keep.items():
            _setting(k, v)
        conn = db()
        conn.execute("DELETE FROM guest_letter_approvals")
        for a in approvals:
            conn.execute("INSERT INTO guest_letter_approvals (letter_key, wording_hash, "
                         "approved_at, approved_by_user_id) VALUES (?, ?, ?, ?)",
                         (a["letter_key"], a["wording_hash"], a["approved_at"],
                          a["approved_by_user_id"]))
        if cancelled:
            conn.execute("UPDATE email_templates SET subject = ?, body = ? WHERE template_key = "
                         "'room_cancelled'", (cancelled["subject"], cancelled["body"]))
        conn.commit()
        conn.close()
        _cleanup()
    return s


def _checks(s, oc, sent, owner_name):
    _setting("guest_mail_live", "1")

    s.section("Off by default: the deploy switches it off")
    _setting("guest_emails_on", None)
    with m.app.test_request_context("/"):
        s.check("with no setting written, guest emails are off", m.guest_emails_on() is False)

    s.section("Off: only booking confirmations, and words a person wrote, go")
    for key in ("room_cancelled", "refund_issued", "room_balance_before", "room_updated",
                "workshop_balance_reminder", "code:send_autocharge_taken_email",
                "campaign:ZZGE", "workshop_recap"):
        del sent[:]
        went = _send(key, f"ZZGE {key}")
        row = _held(f"ZZGE {key}")
        s.check(f"{key}: held, not sent", went is False and not sent, detail=str(sent))
        s.check(f"{key}: kept to be read, with the reason",
                row is not None and "guest emails are off" in (row["reason"] or ""),
                detail=repr(row["reason"] if row else None))
    for key in sorted(m.CONFIRMATION_LETTERS):
        del sent[:]
        _send(key, f"ZZGE {key}")
        s.check(f"booking confirmation {key}: sent", bool(sent) and not _held(f"ZZGE {key}"))
    for key in ("code:reply_to_feedback", "code:reply_booking_com_message", "group:room"):
        del sent[:]
        _send(key, f"ZZGE {key}")
        s.check(f"written by a person, {key}: sent", bool(sent))
    del sent[:]
    _send(None, "ZZGE nameless")
    s.check("a letter with no name at all is held, never guessed into going",
            not sent and _held("ZZGE nameless") is not None)
    s.check("a held letter keeps its words, to be read",
            (_held("ZZGE room_cancelled") or {"body": ""})["body"] == "ZZGE body.")

    s.section("Texts: the automatic ones held, one typed by staff not")
    conn = db()
    went, refusal = m.send_sms(conn, PHONE, "ZZGE automatic text", letter="text:checkin")
    conn.commit()
    row = conn.execute("SELECT reason FROM sms_outbox WHERE body = 'ZZGE automatic text'"
                       ).fetchone()
    s.check("an automatic text is held by Guest emails",
            went is False and refusal is None and row is not None
            and "guest emails are off" in row["reason"], detail=repr(row and row["reason"]))
    m.send_sms(conn, PHONE, "ZZGE typed text", letter="text:by_hand")
    conn.commit()
    row = conn.execute("SELECT reason FROM sms_outbox WHERE body = 'ZZGE typed text'").fetchone()
    s.check("a text typed on a booking is not held by it (only by having no provider)",
            row is None or "guest emails" not in (row["reason"] or ""),
            detail=repr(row and row["reason"]))
    went, refusal = m.send_sms(conn, PHONE, "ZZGE stale text", hold=False,
                               letter="text:room_waitlist")
    s.check("a text only true now is refused rather than kept",
            went is False and refusal and "guest emails are off" in refusal, detail=str(refusal))
    conn.close()

    s.section("On: a letter goes only once its wording is approved")
    r = oc.post("/admin/guest-emails/switch", data={"on": "1"})
    with m.app.test_request_context("/"):
        s.check("the switch turns it on", r.status_code == 302 and m.guest_emails_on())
    del sent[:]
    _send("room_cancelled", "ZZGE on unapproved")
    row = _held("ZZGE on unapproved")
    s.check("on, but not approved: still held", not sent and row is not None
            and "not approved yet" in row["reason"], detail=repr(row and row["reason"]))
    r = oc.post("/admin/guest-emails/approve", data={"key": "room_cancelled"})
    s.check("approving it", r.status_code == 302)
    # Scoped to this letter's own card: the owner's name is in the header of
    # every staff page, so a check reading the whole page would pass with the
    # approver never shown.
    listed = oc.get("/admin/guest-emails").get_data(as_text=True)
    at = listed.find('id="l-room_cancelled"')
    card = listed[at:listed.find('class="detail-card"', at + 1)] if at >= 0 else ""
    s.check("the page names who approved it",
            "by " + str(escape(owner_name)) in card,
            detail=" ".join(card.split())[-240:] + " -- a letter cleared to go to every "
                   "guest with nobody's name against it is a decision nobody can be asked about")
    del sent[:]
    _send("room_cancelled", "ZZGE on approved")
    s.check("approved and on: it goes", bool(sent) and not _held("ZZGE on approved"))
    conn = db()
    conn.execute("UPDATE email_templates SET body = body || ' (changed)' "
                 "WHERE template_key = 'room_cancelled'")
    if not conn.total_changes:
        conn.execute("INSERT INTO email_templates (template_key, label, subject, body) "
                     "VALUES ('room_cancelled', 'x', 'x', 'changed')")
    conn.commit()
    conn.close()
    del sent[:]
    _send("room_cancelled", "ZZGE on changed")
    s.check("its wording changed: the approval lapses, and it is held again",
            not sent and _held("ZZGE on changed") is not None)
    unwritten = sorted(k for k in m.UNWRITTEN_LETTERS)
    if unwritten:
        oc.post("/admin/guest-emails/approve", data={"key": unwritten[0]})
        conn = db()
        got = conn.execute("SELECT 1 FROM guest_letter_approvals WHERE letter_key = ?",
                           (unwritten[0],)).fetchone()
        conn.close()
        s.check("a letter with no wording cannot be approved", got is None, detail=unwritten[0])
    r = oc.post("/admin/guest-emails/approve", data={"key": "room_confirmed"})
    s.check("a booking confirmation is not something to approve here",
            r.status_code == 302)
    oc.post("/admin/guest-emails/switch", data={"on": "0"})
    with m.app.test_request_context("/"):
        s.check("and the switch turns it off again", m.guest_emails_on() is False)

    s.section("Held email: Send all never sends one held by Guest emails")
    tried = []
    m.send_held_letter = lambda conn, row: (tried.append(row["subject"]), True)[1]
    oc.post("/admin/email-outbox/send", data={})
    s.check("the batch passes over every letter held by Guest emails",
            not [t for t in tried if t.startswith("ZZGE")], detail=str(tried))

    s.section("The page: every letter, what sends it, and its full text")
    page = oc.get("/admin/guest-emails")
    body = page.get_data(as_text=True)
    s.check("the owner can open it", page.status_code == 200, response=page)
    s.check("it says the switch is off", "Guest emails: off" in body)
    for key, label, subject, _b in m.DEFAULT_EMAIL_TEMPLATES:
        s.check(f"lists {key}", f'id="l-{key}"' in body)
    with m.app.test_request_context("/"):
        rows = m.guest_letter_rows(m.get_db())
    missing = [r["key"] for r in rows if not r["trigger"]]
    s.check("every one says what sends it", not missing, detail=str(missing))
    s.check("texts are on it too", any(r["key"] == "text:checkin" for r in rows))
    s.check("staff cannot open it", _ec_get(s) != 200)


def _ec_get(s):
    _oc, ec, _o, _e = clients()
    return ec.get("/admin/guest-emails").status_code


if __name__ == "__main__":
    print(run().report())
