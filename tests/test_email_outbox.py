"""Held email.

The failure this guards against is the quiet one: a guest pays for a stay,
the confirmation cannot be sent, and nobody — guest or owner — ever learns
that anything was owed. So the checks are that an undeliverable message is
kept, that retrying it does not send it twice, and that a password reset is
never written down, because its body is a working credential.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
MAIL = "zzoutbox.guest@example.invalid"


def _held(address=MAIL):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM email_outbox WHERE to_address = ? ORDER BY id", (address,)).fetchall()
    conn.close()
    return rows


def _clear():
    conn = db()
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE '%@example.invalid'")
    conn.commit()
    conn.close()


def run():
    s = Suite("Held email")
    oc, ec, _owner, _emp = clients()
    _clear()

    # The harness clears the provider keys, so this is the state the app is
    # actually in right now: no provider, every send undeliverable.
    s.section("An undeliverable message is kept, not lost")
    ok = m.send_email(MAIL, "Your booking is confirmed", "Dear guest, we look forward to it.")
    rows = _held()
    s.check("send reports failure honestly", ok is False, detail=f"returned {ok!r}")
    s.check("the message is held", len(rows) == 1, detail=f"{len(rows)} rows")
    if rows:
        s.check("the body is kept so it can actually be sent later",
                "look forward" in (rows[0]["body"] or ""))
        s.check("the reason is recorded", bool(rows[0]["reason"]),
                detail=f"reason={rows[0]['reason']!r}")
        s.check("it is not marked sent", rows[0]["sent_at"] is None)

    s.section("A credential is never written down")
    m.send_email(MAIL, "Reset your password",
                 "Click this link to set a new password:\nhttps://example.invalid/reset-password/SECRET",
                 keep=False)
    rows = _held()
    leaked = [r for r in rows if "SECRET" in (r["body"] or "")]
    s.check("a keep=False message is not stored", not leaked,
            detail=f"{len(leaked)} rows contain the token")

    s.section("The page shows it")
    r = oc.get("/admin/email-outbox")
    body = r.get_data(as_text=True)
    s.check("outbox page renders", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("the held message is listed", MAIL in body)
    s.check("it says why nothing can be sent yet",
            "isn't connected" in body or "no email provider" in body.lower())

    s.section("Retrying with no provider changes nothing")
    before = len(_held())
    r = oc.post("/admin/email-outbox/send", follow_redirects=True)
    after = _held()
    s.check("retry is refused rather than pretending", r.status_code == 200)
    s.check("nothing is marked sent", all(x["sent_at"] is None for x in after))
    s.check("retrying does not duplicate the queue", len(after) == before,
            detail=f"{before} -> {len(after)}")

    s.section("A working provider drains it exactly once")
    # Stand in for a provider that accepts everything, so the retry path is
    # exercised without reaching a real one.
    sent_to = []
    real_send = m.send_email

    def fake_send(to, subject, body, ics=None, ics_name=None, keep=True):
        sent_to.append(to)
        return True

    m.send_email = fake_send
    # email_enabled() wants all three, so setting only the host leaves the
    # route correctly refusing and the retry path untested.
    m.SMTP_HOST, m.SMTP_USERNAME, m.SMTP_PASSWORD = ("smtp.example.invalid", "u", "p")
    try:
        r = oc.post("/admin/email-outbox/send", follow_redirects=True)
        rows = _held()
        s.check("the held message went out", len(sent_to) >= 1, r,
                detail=f"sent {len(sent_to)}")
        s.check("it is marked sent", all(x["sent_at"] for x in rows),
                detail=f"{sum(1 for x in rows if not x['sent_at'])} still unsent")

        # A second press must not re-send: a guest getting the same
        # confirmation twice is the failure this guards.
        again = len(sent_to)
        oc.post("/admin/email-outbox/send", follow_redirects=True)
        s.check("a second retry sends nothing again", len(sent_to) == again,
                detail=f"{again} -> {len(sent_to)}")
    finally:
        m.send_email = real_send
        m.SMTP_HOST = m.SMTP_USERNAME = m.SMTP_PASSWORD = None

    s.section("Permissions")
    for path in ["/admin/email-outbox"]:
        rr = ec.get(path)
        s.check(f"employee blocked from {path}", rr.status_code in (302, 403),
                detail=f"HTTP {rr.status_code}")

    _clear()
    return s
