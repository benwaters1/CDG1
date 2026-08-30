"""Resetting a password with a code instead of a link.

A link was the wrong shape for this. Mail scanners follow links — Outlook Safe
Links and its equivalents fetch every URL in a message before anybody reads it
— so a one-time reset link that a scanner opens is spent: the person clicks it,
is told it has expired, and has no way to tell why. A code is not a URL, so
nothing consumes it on the way, and it can be read on a phone and typed on the
laptop that asked.

What a code needs that a link does not, and what this holds:

  - AN ATTEMPT CAP. Six digits is a million combinations, which is a lot to a
    person and a few seconds to a script. The cap is the whole reason the
    length is safe, and it KILLS the code rather than slowing it down — a
    throttle can be waited out, a dead code cannot.
  - THE CODE STORED HASHED. The column is a verifier, not a credential, so a
    copy of this database is not a set of live resets waiting to be used. It is
    also what makes the comparison constant-time; == on a short secret leaks
    its prefix to anybody who can measure.
  - ONE ANSWER FOR EVERY FAILURE. No account, no code issued, wrong code,
    expired code, too many guesses — one sentence. Anything more specific turns
    the page into a way to ask the château who works there.

A wrong password after a RIGHT code must not spend the code. They have already
proved who they are; making them start again over a typo would be the app
punishing the wrong mistake.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztreset"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG + "%",))
    for action in ("forgot_password", "reset_password_attempt"):
        conn.execute("DELETE FROM submission_log WHERE action = ?", (action,))
    conn.commit()
    conn.close()


def _person(name="Renee"):
    conn = db()
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, ?, 'employee', ?, 'General', 'active', ?)""",
        (f"{TAG}{name.lower()}@example.invalid",
         m.generate_password_hash("the-old-password"), TAG + " " + name,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE email = ?",
                       (f"{TAG}{name.lower()}@example.invalid",)).fetchone()
    conn.close()
    return row


def _row(user_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def _clear_limits():
    conn = db()
    for action in ("forgot_password", "reset_password_attempt"):
        conn.execute("DELETE FROM submission_log WHERE action = ?", (action,))
    conn.commit()
    conn.close()


def _ask(client, person):
    """Request a code, catching the message on its way out.

    It cannot be read from the outbox, because the code is sent with
    keep=False and so is deliberately never queued — the body IS the
    credential, and a queue of live reset codes waiting for the day a provider
    is configured would be worse than losing them. Standing in for the send is
    the only place the code exists in the clear.
    """
    caught = {}

    def _capture(to_address, subject, body, *a, **kw):
        caught["body"] = body
        return True

    _clear_limits()
    # email_enabled() has to be stood in for as well. The route refuses to
    # issue a code at all when there is no way to send one — correct, and it
    # means the whole flow is unreachable in this database until it is stood
    # in for. Without this the suite would exercise the refusal and report on
    # a reset that never happened.
    real_send, real_enabled = m.send_email, m.email_enabled
    m.send_email, m.email_enabled = _capture, (lambda: True)
    try:
        client.post("/forgot-password", data={"email": person["email"]},
                    follow_redirects=True)
    finally:
        m.send_email, m.email_enabled = real_send, real_enabled
    found = re.search(r"\b(\d{6})\b", caught.get("body") or "")
    return found.group(1) if found else None


def run():
    s = Suite("Password reset by code")
    _cleanup()
    _oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()

    s.section("With no way to send, nothing is issued")
    # Real behaviour and worth holding: a code nobody can receive is worse than
    # an honest refusal, because the person waits for it.
    gated = _person("Gated")
    r = anon.post("/forgot-password", data={"email": gated["email"]},
                  follow_redirects=True)
    # Matched on a fragment with no apostrophe in it: the flash is rendered
    # HTML-escaped, so "isn't" arrives as "isn&#39;t" and a literal match on
    # the sentence as written in app.py never fires.
    s.check("the page says so instead",
            any("set up for this site" in f for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and no code is stored for them",
            _row(gated["id"])["reset_code"] is None,
            detail="a code that cannot be delivered is a lock with no key")

    s.section("Asking for one")
    renee = _person("Renee")
    code = _ask(anon, renee)
    s.check("a code is sent", code is not None and len(code) == 6, detail=str(code))
    s.check("it is six digits", code and code.isdigit(), detail=str(code))

    after = _row(renee["id"])
    s.check("and something is stored against them", bool(after["reset_code"]))
    # The column must be a verifier, not the code. A database copy is not a
    # set of live resets.
    s.check("but NOT the code itself", after["reset_code"] != code,
            detail="a copy of this database would otherwise be a list of "
                   "passwords waiting to be set")
    s.check("it is a hash that verifies the code",
            m.check_password_hash(after["reset_code"], code))
    s.check("the old link token is cleared", after["reset_token"] is None)
    s.check("and it expires in minutes, not hours",
            after["reset_token_expires_at"] and
            m.parse_datetime_iso(after["reset_token_expires_at"])
            < datetime.now(timezone.utc) + timedelta(minutes=m.RESET_CODE_MINUTES + 1),
            detail=str(after["reset_token_expires_at"]))

    s.section("Using it")
    _clear_limits()
    r = anon.post("/reset-password", data={
        "email": renee["email"], "code": code,
        "password": "a-brand-new-password", "confirm_password": "a-brand-new-password",
    }, follow_redirects=True)
    fresh = _row(renee["id"])
    s.check("the password is changed",
            m.check_password_hash(fresh["password_hash"], "a-brand-new-password"))
    s.check("and the old one no longer works",
            not m.check_password_hash(fresh["password_hash"], "the-old-password"))
    s.check("the code is spent", fresh["reset_code"] is None)
    s.check("and they are told to sign in",
            any("sign in" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    # Once. Replaying the same code must do nothing.
    _clear_limits()
    anon.post("/reset-password", data={
        "email": renee["email"], "code": code,
        "password": "third-password-attempt", "confirm_password": "third-password-attempt",
    }, follow_redirects=True)
    s.check("the same code cannot be used twice",
            not m.check_password_hash(_row(renee["id"])["password_hash"],
                                      "third-password-attempt"),
            detail="a spent code that still works is a permanent back door")

    s.section("Guessing at it")
    # The check that makes six digits defensible.
    guessy = _person("Gaston")
    real = _ask(anon, guessy)
    wrong = "000000" if real != "000000" else "111111"
    for attempt in range(m.RESET_CODE_MAX_ATTEMPTS):
        _clear_limits()
        anon.post("/reset-password", data={
            "email": guessy["email"], "code": wrong,
            "password": "guessed-my-way-in", "confirm_password": "guessed-my-way-in",
        }, follow_redirects=True)
    s.check("wrong codes never change the password",
            not m.check_password_hash(_row(guessy["id"])["password_hash"],
                                      "guessed-my-way-in"))
    s.check("and after enough of them the code is dead, not merely slowed",
            _row(guessy["id"])["reset_code"] is None,
            detail="a throttle can be waited out; a dead code cannot")

    # ...and the real code no longer works either, which is the point: the
    # attacker's guessing has to cost the attacker, not just be ignored.
    _clear_limits()
    anon.post("/reset-password", data={
        "email": guessy["email"], "code": real,
        "password": "too-late-now", "confirm_password": "too-late-now",
    }, follow_redirects=True)
    s.check("even the right code is refused once it has been guessed at",
            not m.check_password_hash(_row(guessy["id"])["password_hash"],
                                      "too-late-now"))

    s.section("An expired one")
    late = _person("Lucien")
    late_code = _ask(anon, late)
    conn = db()
    conn.execute("UPDATE users SET reset_token_expires_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                  late["id"]))
    conn.commit()
    conn.close()
    _clear_limits()
    r = anon.post("/reset-password", data={
        "email": late["email"], "code": late_code,
        "password": "expired-code-password", "confirm_password": "expired-code-password",
    }, follow_redirects=True)
    s.check("an expired code changes nothing",
            not m.check_password_hash(_row(late["id"])["password_hash"],
                                      "expired-code-password"))
    s.check("and it is cleared rather than left lying about",
            _row(late["id"])["reset_code"] is None)
    s.check("the page still works rather than erroring", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.section("Every failure reads the same")
    # Otherwise the page answers "does this person work here?"
    answers = set()
    unknown = _person("Known")
    known_code = _ask(anon, unknown)
    for label, data in (
            ("an address with no account",
             {"email": TAG + "nobody@example.invalid", "code": "123456"}),
            ("an address that never asked",
             {"email": _person("Never")["email"], "code": "123456"}),
            ("a wrong code for a real request",
             {"email": unknown["email"], "code": "999999"})):
        _clear_limits()
        r = anon.post("/reset-password", data={
            **data, "password": "some-new-password",
            "confirm_password": "some-new-password"}, follow_redirects=True)
        answers.add(" ".join(flashes(r)))
    s.check("all three say the same thing", len(answers) == 1, detail=str(answers))
    s.check("and it gives nothing away",
            answers == {m.RESET_CODE_REFUSED}, detail=str(answers))

    s.section("A typo in the password does not cost the code")
    # They have proved who they are. Making them start again over a mistyped
    # confirmation would punish the wrong mistake.
    tidy = _person("Therese")
    tidy_code = _ask(anon, tidy)
    _clear_limits()
    r = anon.post("/reset-password", data={
        "email": tidy["email"], "code": tidy_code,
        "password": "short", "confirm_password": "short",
    }, follow_redirects=True)
    s.check("too short is refused",
            any("8 characters" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the code survives it", _row(tidy["id"])["reset_code"] is not None)

    _clear_limits()
    r = anon.post("/reset-password", data={
        "email": tidy["email"], "code": tidy_code,
        "password": "one-long-password", "confirm_password": "a-different-one",
    }, follow_redirects=True)
    s.check("a mismatch is refused",
            any("match" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the code still survives", _row(tidy["id"])["reset_code"] is not None)

    _clear_limits()
    anon.post("/reset-password", data={
        "email": tidy["email"], "code": tidy_code,
        "password": "finally-a-good-one", "confirm_password": "finally-a-good-one",
    }, follow_redirects=True)
    s.check("and it still works on the third go",
            m.check_password_hash(_row(tidy["id"])["password_hash"],
                                  "finally-a-good-one"),
            detail="two typos must not send somebody back to the start")

    s.section("The message carries the code and is never kept")
    # This one must let the REAL send_email run. _ask stands it in, so nothing
    # would reach the outbox whatever keep= said — the check would pass on a
    # stand-in rather than on the behaviour, and it did until a control that
    # flipped keep=False to keep=True changed nothing.
    #
    # keep=False is deliberate: the body IS the credential. With no provider
    # the message is dropped rather than queued, which is right — a queue of
    # live reset codes waiting for the day a provider is switched on would be
    # worse than losing them.
    keeper = _person("Keeper")
    _clear_limits()
    real_enabled = m.email_enabled
    m.email_enabled = lambda: True
    try:
        anon.post("/forgot-password", data={"email": keeper["email"]},
                  follow_redirects=True)
    finally:
        m.email_enabled = real_enabled
    conn = db()
    held = conn.execute(
        "SELECT * FROM email_outbox WHERE to_address = ?", (keeper["email"],)).fetchall()
    conn.close()
    s.check("a code was actually issued", _row(keeper["id"])["reset_code"] is not None,
            detail="otherwise the outbox is empty because nothing was sent")
    s.check("and it is never left sitting in the outbox", len(held) == 0,
            detail=f"{len(held)} live credential(s) queued for later")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
