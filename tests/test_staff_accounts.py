"""Staff accounts the owner can actually manage.

Three gaps came up adding Karina and Jasmine as dashboard staff on 15 Oct 2026:

  THE OWNER COULD NOT CHANGE A STAFF EMAIL. Only /change-email could, for the
  person signed in, with their password. A typo at creation was permanent: the
  invitation went to an address nobody reads, and an account nobody has
  claimed cannot sign in to fix itself.

  NOTHING WAS EMAILED WHEN AN ACCOUNT WAS MADE. The link was flashed and the
  owner copied it into a letter by hand.

  RESETTING SOMEBODY'S PASSWORD WAS HARD TO FIND. It showed only for a claimed
  account, on the profile, and the owner went looking on the Team page.

What this holds: the address change follows login's rules and the guards on
resetting a sign-in; the invitation and the reset are emailed, exactly once,
with the link still shown in case the mail fails; the controls are where the
owner looks; and /forgot-password works end to end through Resend.

Every send is a stand-in. Nothing reaches a provider.
"""
import html
import re
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztstaff"
GOOD = "a-decent-password-9"


class Mail:
    """send_email stood in, with a provider that says yes or no."""

    def __init__(self, ok=True, why="the provider refused it"):
        self.ok, self.why, self.sent = ok, why, []

    def __enter__(self):
        self._was = (m.send_email, m.email_enabled)

        def fake(to, subject, body, *a, **kw):
            self.sent.append({"to": to, "subject": subject, "body": body, "kw": kw})
            if not self.ok and kw.get("report") is not None:
                kw["report"]["why"] = self.why
            return self.ok
        m.send_email, m.email_enabled = fake, (lambda: True)
        return self

    def __exit__(self, *exc):
        m.send_email, m.email_enabled = self._was


def _row(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _by_name(conn, name):
    return conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()


def _said(r):
    return html.unescape(" ".join(flashes(r)))


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (TAG + "%", TAG + "%"))]
    if ids:
        marks = ",".join("?" * len(ids))
        # What they did as themselves -- the rota changing a peer's address --
        # is written under their id, and the audit log keeps its actors.
        conn.execute(f"DELETE FROM audit_log WHERE actor_user_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    conn.execute("DELETE FROM access_presets WHERE slug LIKE ?", (TAG + "-%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    for action in ("forgot_password", "reset_password_attempt"):
        conn.execute("DELETE FROM submission_log WHERE action = ?", (action,))
    conn.commit()


def run():
    s = Suite("staff_accounts")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    oc, ec, owner, _emp = clients()

    # ====================================================== creating
    s.section("Creating an employee emails them their invitation, once")
    with Mail() as mail:
        r = oc.post("/directory/new", data={
            "name": f"{TAG} Karina", "email": f"{TAG}.karina@example.invalud",
            "job_role": "Housekeeping"}, follow_redirects=True)
    karina = _by_name(conn, f"{TAG} Karina")
    s.check("the account is made", karina is not None, r)
    s.check("exactly one letter went", len(mail.sent) == 1,
            detail=str([x["to"] for x in mail.sent]))
    letter = mail.sent[0] if mail.sent else {"to": "", "body": "", "subject": "", "kw": {}}
    s.check("to them", letter["to"] == f"{TAG}.karina@example.invalud", detail=letter["to"])
    s.check("carrying their own link",
            karina is not None and f"/onboard/{karina['invite_token']}" in letter["body"],
            detail=letter["body"][:300])
    s.check("saying what the dashboard is and that they choose the password",
            "dashboard" in letter["body"] and "password" in letter["body"])
    s.check("and who to ask", "ask" in letter["body"].lower())
    s.check("never queued: the letter is the key",
            letter["kw"].get("keep") is False and bool(letter["kw"].get("despite_switch")),
            detail=str(letter["kw"]))
    s.check("the owner is told it went, and still shown the link",
            "Emailed" in _said(r) and "/onboard/" in _said(r), detail=_said(r))

    s.section("If the mail fails, the account is still made and the link shown")
    with Mail(ok=False, why="the provider refused it") as mail:
        r = oc.post("/directory/new", data={
            "name": f"{TAG} Jasmine", "email": f"{TAG}.jasmine@example.invalid",
            "job_role": "Experiences"}, follow_redirects=True)
    jasmine = _by_name(conn, f"{TAG} Jasmine")
    s.check("the account is made", jasmine is not None, r)
    s.check("one attempt was made", len(mail.sent) == 1)
    s.check("it says plainly the invitation was NOT emailed, and why",
            "NOT emailed" in _said(r) and "the provider refused it" in _said(r),
            detail=_said(r))
    s.check("and gives the link to send by hand",
            jasmine is not None and f"/onboard/{jasmine['invite_token']}" in _said(r))

    s.section("An address that cannot receive an invitation is refused at the door")
    with Mail() as mail:
        r = oc.post("/directory/new", data={"name": f"{TAG} Nobody",
                                            "email": "nobody at the house"},
                    follow_redirects=True)
    s.check("no account", _by_name(conn, f"{TAG} Nobody") is None)
    s.check("no letter", not mail.sent)
    s.check("and it says why", "look like an email address" in _said(r), detail=_said(r))

    # ====================================================== changing the address
    s.section("The owner corrects a typo on an account nobody has claimed")
    old_token = karina["invite_token"]
    old_address = karina["email"]
    conn.execute("UPDATE users SET m365_upn = ? WHERE id = ?", (old_address, karina["id"]))
    conn.commit()
    edit = oc.get(f"/directory/{karina['id']}/edit").get_data(as_text=True)
    s.check("the Edit page has the form", f"/directory/{karina['id']}/email" in edit
            and 'name="confirm_email"' in edit)
    with Mail() as mail:
        r = oc.post(f"/directory/{karina['id']}/email", data={
            "new_email": f"  {TAG.upper()}.Karina@Example.INVALID ",
            "confirm_email": f"{TAG}.karina@example.invalid"}, follow_redirects=True)
    after = _row(conn, karina["id"])
    s.check("the address is changed, normalised as login normalises",
            after["email"] == f"{TAG}.karina@example.invalid", detail=after["email"])
    s.check("a fresh invitation goes to the NEW address, once",
            len(mail.sent) == 1 and mail.sent[0]["to"] == f"{TAG}.karina@example.invalid",
            detail=str([x["to"] for x in mail.sent]))
    s.check("carrying a new link", bool(after["invite_token"])
            and after["invite_token"] != old_token
            and mail.sent and f"/onboard/{after['invite_token']}" in mail.sent[0]["body"])
    anon = m.app.test_client()
    s.check("the link that went to the typo no longer opens",
            anon.get(f"/onboard/{old_token}").status_code == 404,
            detail="whoever holds the misspelt mailbox could claim the account")
    s.check("the new one does", anon.get(f"/onboard/{after['invite_token']}").status_code == 200)
    s.check("the Outlook add-in link that repeated the old address follows it",
            not (after["m365_upn"] or "").strip(), detail=repr(after["m365_upn"]))
    audit = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'user_email_changed' AND target = ? "
        "ORDER BY id DESC LIMIT 1", (karina["name"],)).fetchone()
    s.check("the change is audited with the old and the new",
            audit is not None and old_address in audit["details"]
            and f"{TAG}.karina@example.invalid" in audit["details"],
            detail=audit["details"] if audit else "no audit row")
    s.check("the owner is told it worked and that the invitation went",
            "now signs in with" in _said(r) and "Emailed" in _said(r), detail=_said(r))

    s.section("Refused: an address somebody else signs in with, and it says whose")
    before = _row(conn, karina["id"])["email"]
    with Mail() as mail:
        r = oc.post(f"/directory/{karina['id']}/email", data={
            "new_email": jasmine["email"], "confirm_email": jasmine["email"]},
            follow_redirects=True)
    s.check("unchanged", _row(conn, karina["id"])["email"] == before)
    s.check("naming who holds it", jasmine["name"] in _said(r), detail=_said(r))
    s.check("nothing sent", not mail.sent)

    s.section("Refused: an address linked to somebody else in the Outlook add-in")
    conn.execute("UPDATE users SET m365_upn = ? WHERE id = ?",
                 (f"{TAG}.linked@example.invalid", jasmine["id"]))
    conn.commit()
    r = oc.post(f"/directory/{karina['id']}/email", data={
        "new_email": f"{TAG}.linked@example.invalid",
        "confirm_email": f"{TAG}.linked@example.invalid"}, follow_redirects=True)
    s.check("unchanged", _row(conn, karina["id"])["email"] == before)
    s.check("naming who it is linked to", jasmine["name"] in _said(r), detail=_said(r))

    s.section("Refused: a confirmation that does not match, and a non-address")
    r = oc.post(f"/directory/{karina['id']}/email", data={
        "new_email": f"{TAG}.k1@example.invalid", "confirm_email": f"{TAG}.k2@example.invalid"},
        follow_redirects=True)
    s.check("mismatch: unchanged", _row(conn, karina["id"])["email"] == before)
    s.check("mismatch: says so", "don't match" in _said(r), detail=_said(r))
    r = oc.post(f"/directory/{karina['id']}/email", data={
        "new_email": "karina", "confirm_email": "karina"}, follow_redirects=True)
    s.check("not an address: unchanged", _row(conn, karina["id"])["email"] == before)

    s.section("Refused: the owner's own row, which stays behind their password")
    owner_before = _row(conn, owner["id"])["email"]
    r = oc.post(f"/directory/{owner['id']}/email", data={
        "new_email": f"{TAG}.owner@example.invalid",
        "confirm_email": f"{TAG}.owner@example.invalid"}, follow_redirects=True)
    s.check("the owner's address is unchanged", _row(conn, owner["id"])["email"] == owner_before)
    s.check("pointing at Change email", "Change email" in _said(r), detail=_said(r))
    own_edit = oc.get(f"/directory/{owner['id']}/edit").get_data(as_text=True)
    s.check("and the owner's Edit page offers no box for it",
            f"/directory/{owner['id']}/email" not in own_edit and "/change-email" in own_edit)

    s.section("Refused upwards, as resetting a sign-in is -- but not refused to everybody")
    now = datetime.now(timezone.utc).isoformat()
    for slug, areas in ((f"{TAG}-team", "team"), (f"{TAG}-wide", "team,guests")):
        conn.execute("INSERT INTO access_presets (slug, name, areas, created_at) "
                     "VALUES (?, ?, ?, ?)", (slug, slug, areas, now))

    def add(name, preset):
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role, status,
                   access_preset, account_claimed, created_at)
               VALUES (?, 'x', 'employee', ?, 'General', 'active', ?, 1, ?)""",
            (f"{TAG}.{name.lower()}@example.invalid", f"{TAG} {name}", preset, now))
        return _by_name(conn, f"{TAG} {name}")
    rota = add("Rota", f"{TAG}-team")
    wide = add("Manager", f"{TAG}-wide")
    peer = add("Peer", f"{TAG}-team")
    conn.commit()
    rc = m.app.test_client()
    with rc.session_transaction() as sess:
        sess["user_id"] = rota["id"]
    with Mail() as mail:
        r = rc.post(f"/directory/{wide['id']}/email", data={
            "new_email": f"{TAG}.taken-over@example.invalid",
            "confirm_email": f"{TAG}.taken-over@example.invalid"}, follow_redirects=True)
    s.check("a narrower preset cannot change a wider one's address",
            _row(conn, wide["id"])["email"] == wide["email"], r)
    s.check("and is told why, on a page they can read",
            r.status_code == 200 and "owner's to do" in _said(r), detail=_said(r))
    s.check("nothing was sent", not mail.sent)
    with Mail():
        r = rc.post(f"/directory/{peer['id']}/email", data={
            "new_email": f"{TAG}.peer2@example.invalid",
            "confirm_email": f"{TAG}.peer2@example.invalid"})
    s.check("but whoever runs the rota can correct a peer's",
            _row(conn, peer["id"])["email"] == f"{TAG}.peer2@example.invalid",
            detail="refused to everybody would stop the rota fixing a housekeeper's typo")
    s.check("and lands on the Team page, not a profile they cannot open",
            r.status_code == 302 and r.headers.get("Location", "").endswith("/directory"),
            detail=r.headers.get("Location"))
    r = ec.post(f"/directory/{karina['id']}/email", data={
        "new_email": f"{TAG}.x@example.invalid", "confirm_email": f"{TAG}.x@example.invalid"})
    s.check("an employee with no preset is turned away", r.status_code in (302, 403), r)
    s.check("unchanged", _row(conn, karina["id"])["email"] == before)

    # ====================================================== a claimed account
    s.section("A claimed account: the old address is told, the password still works")
    anon.post(f"/onboard/{jasmine['invite_token']}",
              data={"password": GOOD, "confirm_password": GOOD})
    s.check("Jasmine has claimed her account", _row(conn, jasmine["id"])["account_claimed"] == 1)
    with Mail() as mail:
        r = oc.post(f"/directory/{jasmine['id']}/email", data={
            "new_email": f"{TAG}.experience@example.invalid",
            "confirm_email": f"{TAG}.experience@example.invalid"}, follow_redirects=True)
    s.check("the OLD address is told, and only it",
            [x["to"] for x in mail.sent] == [jasmine["email"]],
            detail=str([x["to"] for x in mail.sent]))
    s.check("no invitation, because she has a password",
            _row(conn, jasmine["id"])["account_claimed"] == 1
            and not any("/onboard/" in x["body"] for x in mail.sent))
    s.check("a link linked separately in the add-in is left alone",
            _row(conn, jasmine["id"])["m365_upn"] == f"{TAG}.linked@example.invalid")
    fresh = m.app.test_client()
    fresh.post("/login", data={"email": f"{TAG}.experience@example.invalid", "password": GOOD})
    with fresh.session_transaction() as sess:
        s.check("she signs in with the new address and her own password",
                sess.get("user_id") == jasmine["id"], detail=str(sess.get("user_id")))

    # ====================================================== resetting
    s.section("Resetting a claimed account emails a link and kills the old password")
    with Mail() as mail:
        r = oc.post(f"/directory/{jasmine['id']}/regenerate-invite", follow_redirects=True)
    after = _row(conn, jasmine["id"])
    s.check("one letter, to her",
            [x["to"] for x in mail.sent] == [f"{TAG}.experience@example.invalid"],
            detail=str([x["to"] for x in mail.sent]))
    s.check("carrying the new link",
            mail.sent and f"/onboard/{after['invite_token']}" in mail.sent[0]["body"])
    s.check("saying it is a new password, not an invitation",
            mail.sent and "new password" in mail.sent[0]["subject"].lower(),
            detail=mail.sent[0]["subject"] if mail.sent else "")
    stale = m.app.test_client()
    stale.post("/login", data={"email": f"{TAG}.experience@example.invalid", "password": GOOD})
    with stale.session_transaction() as sess:
        s.check("the old password no longer signs her in", sess.get("user_id") is None)
    s.check("the owner still sees the link on screen", "/onboard/" in _said(r), detail=_said(r))

    s.section("And from the Team page, it goes back to the Team page")
    with Mail() as mail:
        r = oc.post(f"/directory/{karina['id']}/regenerate-invite", data={"back": "directory"})
    s.check("back to the directory",
            r.status_code == 302 and r.headers.get("Location", "").endswith("/directory"),
            detail=r.headers.get("Location"))
    s.check("having emailed her a fresh invitation", len(mail.sent) == 1
            and "/onboard/" in mail.sent[0]["body"])

    # ====================================================== where the controls are
    s.section("The controls are where the owner looks")
    team = oc.get("/directory?q=" + TAG).get_data(as_text=True)
    s.check("the Team page has a reset on each active row",
            f"/directory/{karina['id']}/regenerate-invite" in team
            and f"/directory/{jasmine['id']}/regenerate-invite" in team)
    anon.post(f"/onboard/{_row(conn, jasmine['id'])['invite_token']}",
              data={"password": GOOD, "confirm_password": GOOD})
    claimed = oc.get(f"/directory/{jasmine['id']}").get_data(as_text=True)
    head = claimed.split('class="profile-layout"')[0]
    s.check("a claimed profile has Reset their password at the top",
            "Reset their password" in head and "regenerate-invite" in head)
    s.check("asking first, because it stops a working password",
            "return confirm(" in head and "current password" in head)
    pending = oc.get(f"/directory/{karina['id']}").get_data(as_text=True)
    head = pending.split('class="profile-layout"')[0]
    s.check("an unclaimed profile has Send the invite again at the top",
            "Send the invite again" in head and "regenerate-invite" in head)
    s.check("and still shows the link, in case the email went astray",
            f"/onboard/{_row(conn, karina['id'])['invite_token']}" in pending)
    s.check("an employee is not shown these controls on the Team page",
            "regenerate-invite" not in ec.get("/directory").get_data(as_text=True))

    # ====================================================== the owner's own reset
    s.section("The owner's own password: /forgot-password through Resend, end to end")
    login = m.app.test_client().get("/login").get_data(as_text=True)
    s.check("the sign-in page links to it",
            'href="/forgot-password"' in login and "Forgot your password?" in login)
    s.check("straight under the button, not below the small print",
            login.find("Forgot your password?") < login.find("shared iPad"))

    me = _row(conn, owner["id"])
    keep_hash = me["password_hash"]
    caught = []
    was = (m.RESEND_API_KEY, m.RESEND_FROM, m.send_email_via_resend, m.MAIL_REDIRECT_TO)

    def resend(to, subject, body, *a, **kw):
        caught.append({"to": to, "subject": subject, "body": body})
        return True, None
    try:
        m.RESEND_API_KEY, m.RESEND_FROM = "re_test_not_a_key", "Gudanes <noreply@send.example.invalid>"
        m.send_email_via_resend, m.MAIL_REDIRECT_TO = resend, ""
        s.check("with Resend configured, mail counts as working", m.email_enabled())
        anon = m.app.test_client()
        r = anon.post("/forgot-password", data={"email": me["email"]}, follow_redirects=True)
        code = re.search(r"\b(\d{6})\b", caught[0]["body"]).group(1) if caught else None
        s.check("a code reaches Resend, addressed to the owner",
                bool(code) and caught[0]["to"] == me["email"], detail=str(caught)[:300])
        s.check("and the page goes where the code is typed",
                "/reset-password" in (r.request.path if hasattr(r, "request") else "")
                or 'name="code"' in r.get_data(as_text=True))
        new_password = "zz-owner-new-password-1"
        anon.post("/reset-password", data={"email": me["email"], "code": code or "",
                                           "password": new_password,
                                           "confirm_password": new_password})
        back_in = m.app.test_client()
        back_in.post("/login", data={"email": me["email"], "password": new_password})
        with back_in.session_transaction() as sess:
            s.check("the new password signs the owner in",
                    sess.get("user_id") == owner["id"], detail=str(sess.get("user_id")))
    finally:
        (m.RESEND_API_KEY, m.RESEND_FROM, m.send_email_via_resend, m.MAIL_REDIRECT_TO) = was
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (keep_hash, owner["id"]))
        conn.commit()
