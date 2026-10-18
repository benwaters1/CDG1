"""The house's own inboxes: counted as the house, and heard from when they stop.

TWO FAULTS FOUND ON 17 OCT 2026.

  THE MAIL LOG CALLED THE HOUSE "A GUEST". "Booked -- The King Suite" to
  reservations@ is filed against the booking, and "Your daily summary" to
  accounts@ went to an address with a guest profile (a booking the owner made
  to try the site). The guest test came first, so both were counted as letters
  to "A guest". The house's inboxes are now read from where they are
  configured -- the addresses guests are given, each area's notice inbox, the
  owner, the technical address -- and the house test comes first.

  A HOUSE INBOX WAS SUPPRESSED AND NOTHING SAID SO. On 11 Oct a letter to
  reservations@ bounced mid-migration; Resend put it on its suppression list,
  and every later letter was accepted, logged Sent, and never delivered.
  Resend reports that as email.suppressed, which the webhook ignored. Now:
    - a signed email.suppressed lands on its letter's line, in words;
    - a guest address it names stops being written to, as a bounce would;
    - one of the house's OWN inboxes is never put on the stop list (that would
      hold every booking notice the house sends itself) and instead goes on the
      owner's home and the watch tasks at once, named, with the step in Resend;
    - and that closes by itself when a later letter to the inbox is delivered.

Nothing reaches Resend: reports are posted to the app's own webhook, signed
with a test key.
"""
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZHID"
SECRET = "whsec_" + base64.b64encode(b"house inbox test key, not a real one").decode()
INBOX = "reservations@chateaugudanes.com"
GUEST = "zzhid-guest@example.invalid"
TECH = "zzhid-website@example-tech.invalid"
_n = [0]


def _sign(body):
    stamp = str(int(time.time()))
    key = base64.b64decode(SECRET.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"msg_{TAG}.{stamp}.".encode() + body,
                                    hashlib.sha256).digest()).decode()
    return {"svix-id": f"msg_{TAG}", "svix-timestamp": stamp, "svix-signature": f"v1,{sig}"}


def _line(to, subject="A letter", **cols):
    _n[0] += 1
    pid = f"{TAG}-{_n[0]}"
    conn = db()
    fields = {"to_address": to, "subject": f"{TAG} {subject}", "status": "sent", "kept": "here",
              "provider_id": pid, "created_at": datetime.now(timezone.utc).isoformat()}
    fields.update(cols)
    conn.execute(f"INSERT INTO mail_log ({', '.join(fields)}) VALUES "
                 f"({', '.join('?' * len(fields))})", list(fields.values()))
    conn.commit()
    conn.close()
    return pid


def _report(client, kind, pid, **data):
    body = json.dumps({"type": kind, "created_at": "2026-10-17T11:50:00Z",
                       "data": dict(data, email_id=pid)}).encode()
    return client.post("/webhooks/resend", data=body, headers=_sign(body))


def _row(pid):
    conn = db()
    row = conn.execute("SELECT * FROM mail_log WHERE provider_id = ?", (pid,)).fetchone()
    conn.close()
    return row


def _stopped(address):
    conn = db()
    row = conn.execute("SELECT * FROM email_suppressions WHERE email = ? AND cleared_at IS NULL",
                       (address,)).fetchone()
    conn.close()
    return row


def _warnings():
    conn = db()
    with m.app.test_request_context("/"):
        out = m.owner_home_warnings(conn, m.house_today())
    found, _dropped = m.watch_task_findings(conn, m.house_today())
    conn.close()
    return ([w for w in out if INBOX in w["title"]],
            [f for f in found if f[0] == "inbox" and INBOX in f[1]])


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM mail_log WHERE provider_id LIKE ? OR subject LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM email_suppressions WHERE email IN (?, ?)", (GUEST, INBOX))
    conn.commit()
    conn.close()


def run():
    s = Suite("house_inbox_delivery")
    _cleanup()
    real = m.RESEND_WEBHOOK_SECRET
    m.RESEND_WEBHOOK_SECRET = SECRET
    held = db()
    put_back = _harness.keep_rows(held, "app_settings", "key = ?", (m.TECH_EMAIL_KEY,))
    try:
        _delivery(s)
        _mail_log(s)
    finally:
        m.RESEND_WEBHOOK_SECRET = real
        _cleanup()
        put_back()
        held.close()
    return s


def _delivery(s):
    anon = m.app.test_client()

    s.section("Resend's 'suppressed' report is recorded, not dropped")
    pid = _line(GUEST)
    r = _report(anon, "email.suppressed", pid, to=[GUEST],
                suppressed={"message": "Recipient is on the suppression list",
                            "type": "OnAccountSuppressionList"})
    s.check("the signed report is taken", r.status_code == 204, detail=f"HTTP {r.status_code}")
    row = _row(pid)
    s.check("and lands on the letter's own line as suppressed",
            row["delivery"] == "suppressed", detail=str(row["delivery"]))
    s.check("with Resend's words as the detail",
            "suppression list" in (row["delivery_detail"] or ""), detail=str(row["delivery_detail"]))
    s.check("a guest address Resend will not deliver to is not written to again",
            _stopped(GUEST) is not None and _stopped(GUEST)["reason"] == "bounced")
    _report(anon, "email.delivery_delayed", pid)
    s.check("a late 'delayed' does not overwrite it", _row(pid)["delivery"] == "suppressed")
    conn = db()
    words = m.mail_log_rows(conn, line_id=_row(pid)["id"])[0]["status_words"]
    conn.close()
    s.check("the mail log says so in words, not 'Sent'",
            words == "Refused by the provider", detail=words)

    s.section("A guest's bounce is not the house's fault")
    pid = _line(GUEST, "Guest bounce")
    _report(anon, "email.bounced", pid, bounce={"type": "Permanent", "message": "no such user"})
    shown, tasks = _warnings()
    s.check("nothing about a guest address on the owner's home",
            not any(GUEST in w["title"] for w in shown))

    s.section("One of the house's own inboxes, suppressed")
    shown, tasks = _warnings()
    s.check("before: nothing on the owner's home about it", not shown and not tasks,
            detail=str([w["title"] for w in shown]))
    pid = _line(INBOX, "Booked — The King Suite (GUD-TEST)", about_category="room",
                kept="correspondence")
    _report(anon, "email.suppressed", pid, to=[INBOX],
            suppressed={"message": "Recipient is on the suppression list",
                        "type": "OnAccountSuppressionList"})
    s.check("the house's inbox is NOT put on the stop list",
            _stopped(INBOX) is None, detail=str(dict(_stopped(INBOX) or {})))
    shown, tasks = _warnings()
    s.check("it is on the owner's home at once, named",
            len(shown) == 1 and shown[0]["severity"] == "blocker",
            detail=str([w["title"] for w in shown]))
    detail = shown[0]["detail"] if shown else ""
    s.check("saying which letter, and the step to clear it in Resend",
            "Booked" in detail and "Suppressions" in detail and "remove it" in detail,
            detail=detail)
    s.check("and the line opens the mail log on that address",
            bool(shown) and "mail-log" in shown[0]["href"] and "reservations" in shown[0]["href"],
            detail=shown[0]["href"] if shown else "")
    s.check("it is raised as a task too", len(tasks) == 1, detail=str(tasks))

    s.section("It closes by itself")
    pid = _line(INBOX, "Your daily summary")
    _report(anon, "email.delivered", pid)
    shown, tasks = _warnings()
    s.check("a later letter to the inbox delivered takes it off the owner's home",
            not shown, detail=str([w["title"] for w in shown]))
    s.check("and out of the tasks", not tasks, detail=str(tasks))

    s.section("A house inbox that bounces is treated the same way")
    pid = _line(INBOX, "Bounced notice")
    _report(anon, "email.bounced", pid, bounce={"type": "Permanent", "message": "mailbox moved"})
    shown, _tasks = _warnings()
    s.check("on the owner's home", len(shown) == 1, detail=str([w["title"] for w in shown]))
    s.check("and not on the stop list", _stopped(INBOX) is None)


def _mail_log(s):
    s.section("The mail log counts the house's own inboxes as the house")
    conn = db()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (m.TECH_EMAIL_KEY, TECH))
    owner_to = (m.owner_email(conn) or "").lower()
    had_profile = conn.execute("SELECT 1 FROM guests WHERE LOWER(email) = ?",
                               (owner_to,)).fetchone()
    if not had_profile:
        conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                     (f"{TAG} owner trying the site", owner_to, _harness.datetime_now()))
    conn.commit()
    conn.close()
    try:
        _line(INBOX, "Booked notice", about_category="room", kept="correspondence")
        _line(owner_to, "Daily summary to the owner")
        _line(TECH, "Backup")
        _line(GUEST, "Your stay", about_category="room", kept="correspondence")
        conn = db()
        rows = {r["subject"]: r["audience"]
                for r in m.mail_log_list_view(conn, {"q": TAG})["rows"]}
        conn.close()
        s.check("a booking notice to reservations@, filed against the booking: the house",
                rows.get(f"{TAG} Booked notice") == "The house", detail=str(rows))
        s.check("the daily summary to the owner, whose address has a guest profile: the house",
                rows.get(f"{TAG} Daily summary to the owner") == "The house", detail=str(rows))
        s.check("the technical address, read from its setting, off the house's domain: "
                "the house", rows.get(f"{TAG} Backup") == "The house", detail=str(rows))
        s.check("a guest's own letter is still a guest's",
                rows.get(f"{TAG} Your stay") == "A guest", detail=str(rows))
    finally:
        conn = db()
        conn.execute("DELETE FROM guests WHERE name = ?", (f"{TAG} owner trying the site",))
        conn.commit()
        conn.close()
