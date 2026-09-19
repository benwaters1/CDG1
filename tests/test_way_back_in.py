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

    s.section("Switched off again, it is gone")
    s.check("the page disappears with the token",
            anon.get("/recover/" + TOKEN).status_code == 404)

    conn.close()
    return s
