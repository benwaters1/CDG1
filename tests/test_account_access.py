"""How a member of staff gets a login, and what happens when they lose it.

None of this was tested, and it has a hole that only shows up in the state this
site is actually in.

/forgot-password needs an email provider. There isn't one configured, so it
refuses and tells the employee to "ask the owner to reset your password
directly". The owner's only control that resets a password is the invite
regenerator — and the profile page only rendered it while the invitation was
still pending. The moment somebody claimed their account, the button vanished.
So the app told a locked-out employee to ask the owner, and gave the owner
nothing to press. No email, no reset, no way back in.

Regenerating also wasn't audited, though it overwrites a password hash with
random bytes. Deactivating somebody is recorded; destroying their credential
was not.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZACC"
GOOD = "a-decent-password-9"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _invited(name):
    """An employee who has been created but has not claimed their account."""
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, role, status, job_role, password_hash,
           invite_token, account_claimed, created_at)
           VALUES (?, ?, 'employee', 'active', 'Gardening', 'x', ?, 0, ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         f"tok-{TAG}-{name}", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _row(user_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Account access")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("Claiming an account with the invitation link")
    person = _invited("Odile")
    anon = m.app.test_client()
    s.check("the link opens", anon.get(f"/onboard/{person['invite_token']}").status_code == 200)

    s.section("A weak or mistyped password does not claim it")
    anon.post(f"/onboard/{person['invite_token']}",
              data={"password": "short", "confirm_password": "short"})
    s.check("under eight characters is refused", _row(person["id"])["account_claimed"] == 0)
    anon.post(f"/onboard/{person['invite_token']}",
              data={"password": GOOD, "confirm_password": GOOD + "x"})
    s.check("a mismatched confirmation is refused",
            _row(person["id"])["account_claimed"] == 0)
    s.check("and the invitation still works afterwards",
            anon.get(f"/onboard/{person['invite_token']}").status_code == 200,
            detail="a failed attempt burned the link")

    s.section("A good password claims it, and signs them in")
    token = person["invite_token"]
    anon.post(f"/onboard/{token}",
              data={"password": GOOD, "confirm_password": GOOD, "phone": "0600000000"},
              follow_redirects=True)
    claimed = _row(person["id"])
    s.check("the account is claimed", claimed["account_claimed"] == 1)
    s.check("their phone number is kept", claimed["phone"] == "0600000000")
    with anon.session_transaction() as sess:
        s.check("and they are signed in without a second step",
                sess.get("user_id") == person["id"])
    s.check("they can reach a staff page", anon.get("/today").status_code == 200)

    s.section("The invitation is single use")
    s.check("the token is cleared from the account", claimed["invite_token"] is None)
    s.check("and the same link no longer opens",
            m.app.test_client().get(f"/onboard/{token}").status_code == 404,
            detail="a claimed invitation link still works — anybody holding it "
                   "could set the password again")
    s.check("an invented token is a 404",
            m.app.test_client().get("/onboard/not-a-real-token").status_code == 404)

    s.section("The new password actually signs them in")
    fresh = m.app.test_client()
    fresh.post("/login", data={"email": person["email"], "password": GOOD},
               follow_redirects=True)
    with fresh.session_transaction() as sess:
        s.check("they can log in with what they chose",
                sess.get("user_id") == person["id"], detail=f"got {sess.get('user_id')!r}")

    s.section("Forgetting it, with no email provider configured")
    # The state this site is in: nothing sends. The page must say so rather
    # than claim a link is on its way.
    r = m.app.test_client().post("/forgot-password", data={"email": person["email"]},
                                 follow_redirects=True)
    said = " ".join(flashes(r)).lower()
    if not m.email_enabled():
        s.check("it does not pretend a reset link was sent",
                "on its way" not in said, detail=f"flash was {said!r}")
        s.check("it points them at the owner instead", "owner" in said,
                detail=f"flash was {said!r}")

    s.section("So the owner must have something to press")
    # The hole. With no email, this is the only route back in for somebody
    # locked out — and the control was hidden as soon as they claimed.
    page = oc.get(f"/directory/{person['id']}")
    html = page.get_data(as_text=True)
    s.check("their profile loads", page.status_code == 200, page)
    s.check("a claimed account still offers a way to reset their sign-in",
            "regenerate-invite" in html,
            detail="the owner is told to reset it directly and given no control to do it")
    s.check("it asks before it voids a working password",
            "return confirm(" in html and "current password" in html,
            detail="the most destructive control on the page submits on one click")
    s.check("and it says the password will stop working, not just 'regenerate'",
            "no longer works" in html.lower() or "voids the password" in html.lower(),
            detail="the copy does not say what pressing it costs")

    s.section("Resetting it voids the old password and issues a new link")
    before_hash = _row(person["id"])["password_hash"]
    r2 = oc.post(f"/directory/{person['id']}/regenerate-invite", follow_redirects=True)
    after = _row(person["id"])
    s.check("a new invitation token is issued", (after["invite_token"] or "") != "")
    s.check("the account is back to unclaimed", after["account_claimed"] == 0)
    s.check("the old password no longer works",
            after["password_hash"] != before_hash)
    stale = m.app.test_client()
    stale.post("/login", data={"email": person["email"], "password": GOOD},
               follow_redirects=True)
    with stale.session_transaction() as sess:
        s.check("and it is genuinely refused at the login form",
                sess.get("user_id") is None, detail=f"got {sess.get('user_id')!r}")
    s.check("the owner is shown the new link", "/onboard/" in " ".join(flashes(r2)),
            detail=f"{flashes(r2)}")

    s.section("Destroying a credential is written down")
    conn = db()
    audited = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'invite_regenerated' "
        "AND target LIKE ?", (TAG + "%",)).fetchone()["c"]
    conn.close()
    s.check("there is a record of who reset the sign-in", audited >= 1,
            detail="deactivating somebody is audited, overwriting their password was not")

    s.section("They can claim it again with the new link")
    again = m.app.test_client()
    again.post(f"/onboard/{after['invite_token']}",
               data={"password": GOOD + "2", "confirm_password": GOOD + "2"},
               follow_redirects=True)
    s.check("the second claim works", _row(person["id"])["account_claimed"] == 1)

    s.section("Guards")
    s.check("an employee cannot regenerate a colleague's invitation",
            ec.post(f"/directory/{person['id']}/regenerate-invite").status_code in (302, 403))
    s.check("regenerating for somebody who does not exist is a 404",
            oc.post("/directory/999999/regenerate-invite").status_code == 404)
    s.check("the owner's own account cannot be reset this way",
            oc.post(f"/directory/{owner['id']}/regenerate-invite").status_code == 404,
            detail="the route is meant for employees only")

    _cleanup()
    return s
