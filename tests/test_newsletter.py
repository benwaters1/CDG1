"""The newsletter box in the public menu panel.

The box shipped inert — onsubmit="return false", no action, no field name —
so a guest could type their address, watch it clear, and be thrown away. This
covers the wiring, and specifically the two things that make a public
subscribe form safe rather than merely working:

  - Double opt-in. Anyone can type anyone's address into a public form, so a
    row is not a subscriber until the address itself confirms.
  - The same answer either way. If the box said "already subscribed" it would
    become a way to ask the château whether a given address is on its list.

Also checks an opt-out survives somebody re-typing the address, which is the
failure that turns an unsubscribe into a suggestion.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zznews"


def _sub(client, email):
    return client.post("/newsletter/subscribe", data={"email": email},
                       follow_redirects=True)


def run():
    s = Suite("Newsletter")
    pub = m.app.test_client()
    oc, ec, owner, emp = clients()

    fresh = f"{TAG}.fresh@example.invalid"
    conn = db()
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()
    conn.close()

    s.section("Signing up")
    r = _sub(pub, fresh)
    conn = db()
    row = conn.execute(
        "SELECT * FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
    conn.close()
    s.check("the address is recorded", row is not None, r)
    s.check("but NOT yet a subscriber — confirmed_at is null",
            row is not None and row["confirmed_at"] is None,
            detail=f"confirmed_at={row['confirmed_at'] if row else 'no row'}")
    s.check("it is not on the mailing list yet", row is not None and
            fresh not in [x["email"] for x in _recipients()])

    s.section("Confirming")
    token = row["token"]
    r2 = pub.get(f"/newsletter/confirm/{token}", follow_redirects=True)
    conn = db()
    after = conn.execute(
        "SELECT * FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
    conn.close()
    s.check("confirming sets confirmed_at", after["confirmed_at"] is not None, r2)
    s.check("now it IS on the mailing list", fresh in [x["email"] for x in _recipients()])
    s.check("a bad token is a 404, not a crash",
            pub.get("/newsletter/confirm/not-a-real-token").status_code == 404)

    s.section("The form does not leak who is on the list")
    known = _sub(pub, fresh).get_data(as_text=True)
    unknown = _sub(pub, f"{TAG}.other@example.invalid").get_data(as_text=True)
    # Compare the flash wording, which is the only thing a stranger can read.
    s.check("an existing address and a new one get the same answer",
            ("check your email" in known) and ("check your email" in unknown))

    s.section("Unsubscribing sticks")
    r3 = pub.post(f"/newsletter/unsubscribe/{token}", follow_redirects=True)
    conn = db()
    gone = conn.execute(
        "SELECT * FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
    conn.close()
    s.check("unsubscribing clears the confirmation", gone["confirmed_at"] is None, r3)
    s.check("and it is off the mailing list", fresh not in [x["email"] for x in _recipients()])
    s.check("it is recorded on the shared opt-out list too", _opted_out(fresh))

    # The important one: re-typing the address must not quietly resurrect them.
    _sub(pub, fresh)
    conn = db()
    again = conn.execute(
        "SELECT confirmed_at FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
    conn.close()
    s.check("re-subscribing an opted-out address does NOT re-confirm it",
            again["confirmed_at"] is None)
    s.check("still off the mailing list", fresh not in [x["email"] for x in _recipients()])

    s.section("Guards")
    r4 = _sub(pub, "not-an-email")
    conn = db()
    junk = conn.execute(
        "SELECT 1 FROM newsletter_subscribers WHERE email = 'not-an-email'").fetchone()
    conn.close()
    s.check("a malformed address is refused and stored nowhere", junk is None, r4)

    conn = db()
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'newsletter_subscribe'")
    conn.commit()
    conn.close()
    return s


def _recipients():
    conn = db()
    try:
        return m.newsletter_recipients(conn)
    finally:
        conn.close()


def _opted_out(email):
    conn = db()
    try:
        return conn.execute(
            "SELECT 1 FROM email_optouts WHERE email = ?", (email,)).fetchone() is not None
    finally:
        conn.close()
