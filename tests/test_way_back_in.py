"""The door for when the password is gone and email is not set up yet.

The reset route needs a mail provider, and a house that has not finished its
DNS has none — so the owner who forgets their password had no route at all,
and the only answer on offer was hunting the first deploy's logs for a
password printed months ago.

What is worth asserting here is almost entirely about the door being SHUT.
Unset is the normal state, and in that state the page must not merely refuse:
it must not appear to exist at all, because a 403 tells somebody scanning that
there is something here worth coming back for.
"""
import hmac

from _harness import Suite, clients, db
import _harness

m = _harness.m
TOKEN = "zzrecover-" + "x" * 30


def run():
    s = Suite("A way back in")
    # Brings the owner account into being. The route has nobody to
    # recover without one, and rightly 404s — which is what this test
    # was accidentally proving before it created anybody.
    clients()
    anon = m.app.test_client()
    conn = db()

    s.section("With no token set, it does not exist")
    # The harness leaves it unset, which is the shipping state.
    s.check("the app has no recovery token by default",
            not m.OWNER_RECOVERY_TOKEN,
            detail="unset is the normal state; this is switched on for minutes")
    s.check("and the page is a 404, not a refusal",
            anon.get("/recover/anything").status_code == 404,
            detail="403 would tell somebody scanning that there is a door here")
    s.check("and posting to it is too",
            anon.post("/recover/anything", data={"password": "x" * 12,
                                                 "password_again": "x" * 12}
                      ).status_code == 404)

    before = m.OWNER_RECOVERY_TOKEN
    m.OWNER_RECOVERY_TOKEN = TOKEN
    try:
        s.section("With the token set, only the right one opens it")
        s.check("a wrong token is still a 404",
                anon.get("/recover/" + "y" * 40).status_code == 404)
        s.check("a near-miss is too",
                anon.get("/recover/" + TOKEN[:-1]).status_code == 404,
                detail="compared with compare_digest, not ==")
        s.check("the right one opens the page",
                anon.get("/recover/" + TOKEN).status_code == 200)

        owner = conn.execute(
            "SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").fetchone()
        page = anon.get("/recover/" + TOKEN).get_data(as_text=True)
        s.check("which says whose password it is about",
                owner["email"] in page, detail="so nobody resets the wrong account")
        # It shows one person's address to whoever holds the token, and a
        # search engine has no business keeping it.
        s.check("and is not indexable", "noindex" in page)

        s.section("It sets a password rather than showing one")
        was = owner["password_hash"]
        # Refuses the careless cases first.
        anon.post("/recover/" + TOKEN,
                  data={"password": "short", "password_again": "short"},
                  follow_redirects=True)
        s.check("a short password is refused",
                conn.execute("SELECT password_hash FROM users WHERE id = ?",
                             (owner["id"],)).fetchone()["password_hash"] == was)
        anon.post("/recover/" + TOKEN,
                  data={"password": "abcdefghijkl", "password_again": "mnopqrstuvwx"},
                  follow_redirects=True)
        s.check("and two that do not match are refused",
                conn.execute("SELECT password_hash FROM users WHERE id = ?",
                             (owner["id"],)).fetchone()["password_hash"] == was)

        fresh = "a-new-one-entirely-9134"
        done = anon.post("/recover/" + TOKEN,
                         data={"password": fresh, "password_again": fresh},
                         follow_redirects=True)
        s.check("a good one is accepted", done.status_code == 200)
        now = conn.execute("SELECT * FROM users WHERE id = ?",
                           (owner["id"],)).fetchone()
        s.check("the password actually changed", now["password_hash"] != was)
        # The point of the whole exercise: it has to let them IN.
        s.check("and it is the one that was typed",
                m.check_password_hash(now["password_hash"], fresh),
                detail="a recovery that does not let you log in is not one")
        s.check("the page never shows a password back",
                fresh not in done.get_data(as_text=True),
                detail="this is a door, not a window")
        s.check("and it tells them to remove the token",
                "OWNER_RECOVERY_TOKEN" in done.get_data(as_text=True),
                detail="while it is set, the door is open to anybody holding it")

        s.section("And it is written down")
        # A password changed outside the ordinary route is exactly the event
        # somebody needs to be able to find afterwards.
        entry = conn.execute(
            """SELECT * FROM audit_log WHERE action = 'owner_password_recovered'
               ORDER BY id DESC LIMIT 1""").fetchone()
        s.check("there is an audit line", entry is not None)
        s.check("naming the account", entry and owner["email"] in (entry["target"] or ""))
        s.check("and saying it came through the token",
                entry and "token" in (entry["details"] or ""),
                detail=repr(entry["details"] if entry else None))

        # Put the owner's password back so the rest of the run can log in.
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (was, owner["id"]))
        conn.commit()
    finally:
        m.OWNER_RECOVERY_TOKEN = before

    s.section("The simpler way in: a password set from the deployment")
    # The route above works and was still not usable: getting in through it
    # means a token copied without losing a character, and a mismatch answers
    # with a 404 that deliberately says nothing. This is the same authority —
    # control of the deployment — with nothing to mistype.
    owner = conn.execute(
        "SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").fetchone()
    was = owner["password_hash"]
    before_pw = m.OWNER_TEMP_PASSWORD
    # With a trailing space on purpose: an invisible one copied out of a
    # variable box is the likeliest reason the token kept failing, so this
    # must not be able to bite the same way twice.
    m.OWNER_TEMP_PASSWORD = "a-temporary-one-2026  ".strip()
    # A locked-out IP, because that is the state the owner is actually in by
    # the time they reach for this: five wrong attempts locks them for fifteen
    # minutes, and the person making five wrong attempts is the one who cannot
    # get in. The lock is a row on the volume, so redeploying does not shift
    # it — the recovery was being defeated by the lockout it had caused.
    conn.execute(
        """INSERT INTO login_throttle (ip_address, failed_count, locked_until)
           VALUES ('203.0.113.9', 5, ?)
           ON CONFLICT(ip_address) DO UPDATE SET failed_count = 5,
           locked_until = excluded.locked_until""",
        ((m.datetime.now(m.timezone.utc) + m.timedelta(hours=2)).isoformat(),))
    conn.commit()
    try:
        conn2 = db()
        m.init_db()
        conn2.close()
        now = conn.execute("SELECT * FROM users WHERE id = ?",
                           (owner["id"],)).fetchone()
        s.check("the password becomes the one set on the deployment",
                m.check_password_hash(now["password_hash"], "a-temporary-one-2026"),
                detail="set OWNER_TEMP_PASSWORD, redeploy, sign in")
        s.check("and the lockout it caused is lifted with it",
                conn.execute("SELECT COUNT(*) AS c FROM login_throttle"
                             ).fetchone()["c"] == 0,
                detail="a recovery that leaves you locked out is not one")
        s.check("and it is written down",
                conn.execute(
                    """SELECT COUNT(*) AS c FROM audit_log
                       WHERE action = 'owner_password_set_from_environment'"""
                ).fetchone()["c"] >= 1,
                detail="a password changed outside the ordinary route has to "
                       "be findable afterwards")

        # WHICH ACCOUNT. The password was set correctly and on an account
        # nobody had been told the name of, so it read as not working at all.
        armed = m.app.test_client().get("/status").get_json()
        s.check("and the status page says which account to sign in as",
                armed.get("recovery", {}).get("sign_in_as") == owner["email"],
                detail="the password worked the whole time; the address was "
                       "the thing nobody had said out loud")
        s.check("and that the variable actually landed",
                armed.get("recovery", {}).get("temp_password_applied") is True,
                detail="set-but-not-applied is a real state and looked "
                       "identical from outside")
        # THE ONE THAT SETTLES IT. "Applied" reads an audit line, which says a
        # password was written at some point — not that the value in the
        # variable right now opens the account right now. Those two sat side
        # by side with "it didn't work" and neither could settle it.
        s.check("and that the variable as it stands opens that account",
                armed.get("recovery", {}).get(
                    "temp_password_opens_the_account") is True,
                detail="hashed against the stored hash the way login does")
        s.check("and that the account is not inactive",
                armed.get("recovery", {}).get("account_is_active") is True,
                detail="login refuses an inactive account with a message most "
                       "people read as a wrong password")
        # Asserting True against a value that is already True cannot tell a
        # real read from a hardcoded one, so make it say False.
        conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?",
                     (owner["id"],))
        conn.commit()
        try:
            s.check("and notices when it IS inactive",
                    m.app.test_client().get("/status").get_json()["recovery"][
                        "account_is_active"] is False,
                    detail="a correct password on an inactive account is "
                           "refused, and the refusal reads like a wrong one")
        finally:
            conn.execute("UPDATE users SET status = 'active' WHERE id = ?",
                         (owner["id"],))
            conn.commit()
        s.check("and how many owner accounts there are to be confused by",
                armed.get("recovery", {}).get("owner_accounts", 0) >= 1,
                detail="the recovery writes to the lowest id; a second owner "
                       "row is not the one being opened")

        # And it must be able to say NO. A check that only ever reports
        # success is the thing this whole page exists to stop.
        wrong = m.OWNER_TEMP_PASSWORD
        m.OWNER_TEMP_PASSWORD = "not-the-one-that-was-set-9912"
        try:
            s.check("and says so plainly when the variable does NOT open it",
                    m.app.test_client().get("/status").get_json()["recovery"][
                        "temp_password_opens_the_account"] is False,
                    detail="otherwise it is a light that is always green")
        finally:
            m.OWNER_TEMP_PASSWORD = wrong

        # A locked connection is its own refusal with its own fix, and from
        # the outside it reads exactly like the password being wrong again.
        conn.execute(
            """INSERT INTO login_throttle (ip_address, failed_count, locked_until)
               VALUES ('203.0.113.11', 5, ?)
               ON CONFLICT(ip_address) DO UPDATE SET locked_until = excluded.locked_until""",
            ((m.datetime.now(m.timezone.utc) + m.timedelta(hours=2)).isoformat(),))
        conn.commit()
        s.check("and it counts connections currently locked out",
                m.app.test_client().get("/status").get_json()["recovery"][
                    "connections_locked_out"] >= 1,
                detail="a lockout refuses a correct password and says the "
                       "same thing a wrong one does")
        conn.execute("DELETE FROM login_throttle")
        conn.commit()
    finally:
        m.OWNER_TEMP_PASSWORD = before_pw
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (was, owner["id"]))
        conn.commit()


    s.section("Switched off again, it is gone")
    s.check("the page disappears with the token",
            anon.get("/recover/" + TOKEN).status_code == 404)

    s.section("And a page that says what the deployment is doing")
    # The afternoon this came out of went on guesswork: is the new build live,
    # did the variable take, is this the instance with the data, is the code
    # even running. None of it was visible from outside, so every answer meant
    # asking the one person who could not log in to go and read something back.
    r = m.app.test_client().get("/status")
    s.check("it opens without a login", r.status_code == 200,
            detail="the situation it is for is being unable to log in")
    facts = r.get_json()
    s.check("and it is readable as data", isinstance(facts, dict))
    s.check("it says whether a way back in is armed",
            "temp_password_set" in facts.get("recovery", {})
            and "recovery_token_set" in facts.get("recovery", {}),
            detail="the question that took an afternoon")
    s.check("and whether the database survives a deploy",
            "on_a_volume" in facts.get("database", {}),
            detail="two deployments of one repository, one without a volume, "
                   "are otherwise identical from outside")

    # WHAT IT MUST NEVER SAY. Configuration is reported as yes or no, never as
    # a value, and the page is public.
    before = m.OWNER_TEMP_PASSWORD
    m.OWNER_TEMP_PASSWORD = "a-secret-nobody-should-see"
    try:
        body = m.app.test_client().get("/status").get_data(as_text=True)
        s.check("it never prints a secret it was given",
                "a-secret-nobody-should-see" not in body,
                detail="a status page that leaks the thing it reports on is "
                       "worse than no status page")
    finally:
        m.OWNER_TEMP_PASSWORD = before
    # And the address goes away with the variable. Naming the login account on
    # a public page forever is a standing gift to whoever is scanning; naming
    # it during a window the owner opened on purpose, by putting a password
    # into their own deployment config, costs nothing that window did not
    # already cost. The harness leaves the variable unset, which is shipped
    # state, so this is the state the world sees.
    shut = m.app.test_client().get("/status").get_json().get("recovery", {})
    s.check("but with no temp password set, it names no account",
            not shut.get("sign_in_as"),
            detail="published only for the minutes the door is open anyway")
    s.check("and says nothing about whether a password opens it",
            shut.get("temp_password_opens_the_account") is None,
            detail="a public oracle answering yes or no about a live "
                   "credential is a thing to guess at, however slowly")
    s.check("and it does not count the guests for a stranger",
            "has_data" in facts.get("database", {})
            and not any(k in facts.get("database", {})
                        for k in ("guests", "bookings")),
            detail="a yes or no tells two deployments apart; a number is the "
                   "owner's business")

    conn.close()
    return s
