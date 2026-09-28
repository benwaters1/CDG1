"""The door for when the password is gone and email is not set up yet.

The reset route needs a mail provider, and a house that has not finished its
DNS has none — so the owner who forgets their password had no route at all,
and the only answer on offer was hunting the first deploy's logs for a
password printed months ago.

What is worth asserting here is almost entirely about the door being SHUT.
Unset is the normal state, and in that state the page must not merely refuse:
it must not appear to exist at all, because a 403 tells somebody scanning that
there is something here worth coming back for.

The last section is the one that stops all of this being needed twice. The
owner account is seeded by the app itself as owner@chateaugudanes.com, which
is nobody's mailbox, and until now no route on this site could change a user's
email — so the day mail is configured, "forgot password" posts a code to a dead
address and the lockout above comes straight back. Changing the sign-in
address is the fix, and because the sign-in address IS the account, the checks
for it belong with the rest of the recovery story rather than in a file of
their own.
"""
import hmac

from _harness import Suite, clients, db, fill, forms_on
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

    # ------------------------------------------------------------------
    # The third door, and the one that means the other two are never needed:
    # changing the address you sign in with.
    #
    # No route could. Every UPDATE on users set something else, so the owner
    # account kept the address the app invented for it on first run —
    # owner@chateaugudanes.com, which nobody reads. The moment a mail provider
    # is configured, "forgot password" starts posting a six digit code there,
    # and the way back in is a deployment variable again. This is that lockout
    # closed off at the source.
    #
    # On a person of its own rather than the owner row. The sections above
    # borrow the owner and put the password back afterwards; this one changes
    # an identity, and doing that to the account every later suite logs in as
    # is how one suite breaks forty.
    # ------------------------------------------------------------------
    s.section("Changing the address you sign in with")
    PASSWORD = "the-one-they-know-8821"
    OLD = "zzchange.old@example.invalid"
    NEW = "zzchange.new@example.invalid"
    conn.execute("DELETE FROM users WHERE email LIKE 'zzchange.%'")
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role,
           status, created_at) VALUES (?, ?, 'employee', 'Zz Changer',
           'General', 'active', ?)""",
        (OLD, m.generate_password_hash(PASSWORD),
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    mover = conn.execute("SELECT * FROM users WHERE email = ?", (OLD,)).fetchone()
    mc = m.app.test_client()
    with mc.session_transaction() as sess:
        sess["user_id"] = mover["id"]

    def address_now():
        return conn.execute("SELECT email FROM users WHERE id = ?",
                            (mover["id"],)).fetchone()["email"]

    s.check("the page opens for the person signed in",
            mc.get("/change-email").status_code == 200)
    # WHAT THE PAGE PROMISES HAS TO BE WHAT THE CODE DOES. The notice to the
    # old address only goes when a provider is configured, and the harness —
    # like the live site until its DNS is finished — has none. A page that
    # promises the letter anyway is how somebody comes to believe a hijack
    # would have been noticed. Same rule as the privacy notice: the copy is a
    # claim about this code, so it is checked like one.
    was_enabled = m.email_enabled
    try:
        m.email_enabled = lambda: False
        quiet_page = mc.get("/change-email").get_data(as_text=True)
        s.check("with no mail configured it promises no letter",
                "tell the old address" not in quiet_page,
                detail="the page would be describing something it will not do")
        s.check("and says so rather than staying silent",
                "no email provider" in quiet_page.lower())
        m.email_enabled = lambda: True
        loud_page = mc.get("/change-email").get_data(as_text=True)
        s.check("and with a provider it says the letter is coming",
                "tell the old address" in loud_page)
    finally:
        m.email_enabled = was_enabled
    s.check("and not for anybody who is not",
            m.app.test_client().get("/change-email",
                                    follow_redirects=False).status_code in (302, 401, 403),
            detail="the login IS the thing being changed")

    s.section("It refuses everything it should")
    # THE PASSWORD IS THE POINT. A borrowed session — a laptop left open, a
    # tab on a shared machine — must not be enough to walk off with somebody's
    # login, which is what this would be without it.
    mc.post("/change-email", data={"new_email": NEW, "confirm_email": NEW,
                                   "current_password": "not-the-password"})
    s.check("a wrong current password changes nothing", address_now() == OLD)
    mc.post("/change-email", data={"new_email": "not-an-address",
                                   "confirm_email": "not-an-address",
                                   "current_password": PASSWORD})
    s.check("and something that is not an address is refused",
            address_now() == OLD)
    # The typo case, which is this feature's own way of causing the lockout it
    # exists to cure: a wrong address saved successfully is a dead mailbox
    # again, only now chosen on purpose.
    mc.post("/change-email", data={"new_email": NEW,
                                   "confirm_email": "zzchange.nwe@example.invalid",
                                   "current_password": PASSWORD})
    s.check("and two that do not match are refused", address_now() == OLD)
    # Checked on the ANSWER, not on the row. "It is still OLD afterwards" is
    # true whether the route refused or cheerfully wrote OLD back over OLD,
    # so on its own it proves nothing: a refusal re-renders the form, and an
    # acceptance redirects to the profile.
    same = mc.post("/change-email", data={"new_email": OLD, "confirm_email": OLD,
                                          "current_password": PASSWORD})
    s.check("and the address it already has is refused",
            same.status_code == 200 and address_now() == OLD,
            detail=f"HTTP {same.status_code} — 302 means it was taken as a change")

    # Somebody else's address. The column is UNIQUE, so without this check the
    # answer is a 500 rather than a sentence — and the UNIQUE index is case
    # SENSITIVE, so the capitalised spelling is the one that gets through it.
    taken = conn.execute(
        "SELECT email FROM users WHERE id != ? ORDER BY id LIMIT 1",
        (mover["id"],)).fetchone()["email"]
    r = mc.post("/change-email", data={"new_email": taken, "confirm_email": taken,
                                       "current_password": PASSWORD})
    s.check("an address another account holds is refused", address_now() == OLD,
            response=r)
    # AND ONE HELD IN CAPITALS. Typing it in capitals proves nothing — the
    # route lowercases what it is given before it looks, so the two spellings
    # are the same string by then. What the unique index cannot see is a row
    # ALREADY STORED with a capital: it is case sensitive, so Zz@… and zz@…
    # are two different addresses to it and both may exist. Login is not case
    # sensitive — it lowercases and matches — so the moment both exist the
    # capitalised account stops being reachable by anybody, silently, and the
    # person who lost it did nothing. Nothing in this app writes such a row;
    # an import or a hand-edited database does. COLLATE NOCASE on the
    # duplicate check is the only thing standing in front of it.
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role,
           status, created_at) VALUES (?, ?, 'employee', 'Zz Shouty',
           'General', 'active', ?)""",
        ("ZzChange.Shouty@Example.Invalid", m.generate_password_hash("x" * 14),
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    quiet_spelling = "zzchange.shouty@example.invalid"
    mc.post("/change-email", data={"new_email": quiet_spelling,
                                   "confirm_email": quiet_spelling,
                                   "current_password": PASSWORD})
    s.check("and so is one another account holds in capitals",
            address_now() == OLD,
            detail="the unique index is case sensitive, so it would have let "
                   "this through and orphaned the other account")

    s.section("A good one lands, normalised the way login reads it")
    # Typed the way somebody actually types an address into a form on a phone:
    # a capital on the front and a space on the end from the autocomplete.
    # login does .strip().lower() and then matches with a plain `=`, so an
    # address saved any other way is one that can never be typed back in.
    # SUBMITTED THROUGH THE FORM THE PAGE DRAWS, not through the field names
    # the route happens to read. Every post above types those names in by
    # hand, which is right for testing the route and proves nothing about the
    # template: a renamed box, or one somebody forgot to draw, passes all of
    # them and is broken for every person who opens the page.
    posts = [f for f in forms_on(mc.get("/change-email").get_data(as_text=True))
             if f["method"] == "post"]
    form = max(posts, key=lambda f: len(f["fields"])) if posts else {"fields": []}
    drawn = {x["name"] for x in form["fields"]}
    s.check("the page draws every box the route reads",
            {"new_email", "confirm_email", "current_password"} <= drawn,
            detail=f"the form sends {sorted(drawn)}")
    # Typed the way somebody types an address into a form on a phone: a
    # capital on the front, and a space on the end from the autocomplete.
    messy = "  ZzChange.New@Example.Invalid  "
    done = mc.post("/change-email",
                   data=fill(form, {"new_email": messy, "confirm_email": messy,
                                    "current_password": PASSWORD}),
                   follow_redirects=True)
    s.check("it is accepted", done.status_code == 200, response=done)
    s.check("and stored exactly as login would look it up", address_now() == NEW,
            detail=repr(address_now()))

    # THE WHOLE POINT, and the thing every check above is only scaffolding
    # for: it has to let them in at the new address and stop letting them in
    # at the old one.
    conn.execute("DELETE FROM login_throttle")
    conn.commit()
    fresh_in = m.app.test_client()
    fresh_in.post("/login", data={"email": NEW, "password": PASSWORD})
    with fresh_in.session_transaction() as sess:
        signed_in = sess.get("user_id")
    s.check("the new address signs in", signed_in == mover["id"],
            detail="a change that does not let you log in is not one")
    stale = m.app.test_client()
    stale.post("/login", data={"email": OLD, "password": PASSWORD})
    with stale.session_transaction() as sess:
        old_still_works = sess.get("user_id")
    s.check("and the old one does not", old_still_works is None)
    conn.execute("DELETE FROM login_throttle")
    conn.commit()

    s.section("A reset already in flight dies with the address")
    # A code was posted to the old mailbox. The login it opens has just become
    # a different address, so leaving it live means whoever reads the old mail
    # can take the account straight back — the change would look done and
    # would not be.
    code = "424242"
    conn.execute(
        """UPDATE users SET reset_code = ?, reset_token = 'a-live-token',
           reset_token_expires_at = ?, reset_code_attempts = 0 WHERE id = ?""",
        (m.generate_password_hash(code),
         (m.datetime.now(m.timezone.utc) + m.timedelta(minutes=9)).isoformat(),
         mover["id"]))
    conn.commit()
    back = "zzchange.back@example.invalid"
    mc.post("/change-email", data={"new_email": back, "confirm_email": back,
                                   "current_password": PASSWORD})
    row = conn.execute("SELECT * FROM users WHERE id = ?", (mover["id"],)).fetchone()
    s.check("the address moved again", row["email"] == back)
    s.check("the code is gone", row["reset_code"] is None)
    s.check("the token with it", row["reset_token"] is None)
    s.check("and the expiry that kept it alive",
            row["reset_token_expires_at"] is None)
    # Not merely NULL in a column — actually unusable. This is the claim in a
    # form somebody can check without reading the schema.
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    was_hash = row["password_hash"]
    m.app.test_client().post(
        "/reset-password",
        data={"email": back, "code": code, "password": "taken-over-12345",
              "confirm_password": "taken-over-12345"}, follow_redirects=True)
    s.check("and the code cannot be redeemed against the new address",
            conn.execute("SELECT password_hash FROM users WHERE id = ?",
                         (mover["id"],)).fetchone()["password_hash"] == was_hash,
            detail="the old mailbox would still own the account")

    s.section("It is written down, naming both addresses")
    # A login identity changing is exactly the event somebody goes looking for
    # afterwards — "this account is not the one I set up" is asked weeks late,
    # by which point the only record of what happened is this line.
    entry = conn.execute(
        """SELECT * FROM audit_log WHERE action = 'user_email_changed'
           ORDER BY id DESC LIMIT 1""").fetchone()
    s.check("there is an audit line", entry is not None)
    s.check("under the person's name, so it is on their record",
            entry and (entry["target"] or "") == mover["name"],
            detail=repr(entry["target"] if entry else None))
    s.check("saying what it was", entry and NEW in (entry["details"] or ""))
    s.check("and what it became", entry and back in (entry["details"] or ""),
            detail=repr(entry["details"] if entry else None))

    s.section("And the address losing the account is told")
    # The hijack case: somebody changes the login on a session that is not
    # theirs and the rightful owner learns nothing at all — unless the address
    # that used to work gets a letter about it.
    sent = []
    was_enabled, was_send = m.email_enabled, m.send_email
    m.email_enabled = lambda: True
    m.send_email = lambda to, subject, body, **kw: (
        sent.append((to, subject, body)), True)[1]
    try:
        onward = "zzchange.onward@example.invalid"
        mc.post("/change-email", data={"new_email": onward,
                                       "confirm_email": onward,
                                       "current_password": PASSWORD})
        s.check("one letter goes out", len(sent) == 1, detail=f"{len(sent)} sent")
        s.check("to the address that just stopped working",
                sent and sent[0][0] == back,
                detail=repr(sent[0][0] if sent else None))
        body = sent[0][2] if sent else ""
        s.check("naming the address it was", back in body)
        s.check("and the one it now is", onward in body,
                detail="somebody reading this has to be able to see where "
                       "their account went")
        s.check("and it never carries a password",
                PASSWORD not in body, detail="this is a notice, not a key")
    finally:
        m.email_enabled, m.send_email = was_enabled, was_send

    # WITH NO PROVIDER, WHICH IS THE STATE THIS HOUSE IS IN. The notice is a
    # courtesy; the change is the job. A house with no mail configured must
    # still be able to fix its own login, and must not have a letter nobody
    # will ever send queued up behind it.
    sent.clear()
    was_enabled, was_send = m.email_enabled, m.send_email
    m.email_enabled = lambda: False
    m.send_email = lambda to, subject, body, **kw: (
        sent.append((to, subject, body)), True)[1]
    try:
        final = "zzchange.final@example.invalid"
        quiet = mc.post("/change-email", data={"new_email": final,
                                               "confirm_email": final,
                                               "current_password": PASSWORD},
                        follow_redirects=True)
        s.check("with no mail configured the change still lands",
                address_now() == final, response=quiet)
        s.check("and nothing is sent or queued", sent == [],
                detail=f"{sent} — skipped silently, not held for a retry that "
                       "is never coming")
    finally:
        m.email_enabled, m.send_email = was_enabled, was_send

    conn.execute("DELETE FROM audit_log WHERE action = 'user_email_changed'")
    conn.execute("DELETE FROM users WHERE email LIKE 'zzchange.%'")
    conn.execute("DELETE FROM submission_log")
    conn.execute("DELETE FROM login_throttle")
    conn.commit()

    conn.close()
    return s
