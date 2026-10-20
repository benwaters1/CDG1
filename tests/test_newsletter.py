"""The newsletter box in the public menu panel.

The box shipped inert — onsubmit="return false", no action, no field name —
so a guest could type their address, watch it clear, and be thrown away. This
covers the wiring, and the things that make a public subscribe form safe
rather than merely working:

  - Subscribed as they press it, and thanked once (owner, 19 October 2026:
    "a subscription doesn't need to be confirmed, it's not an account, just
    say thank you for your subscription and maybe an advert for whatever
    workshops there are on"). The thank-you carries what is on and the way
    out, which the privacy notice says every letter does.
  - The same answer either way. If the box said "already subscribed" it would
    become a way to ask the château whether a given address is on its list --
    and typing a subscribed address again sends it nothing, or the box is a
    way to have the house mail somebody on demand.
  - An opt-out survives somebody re-typing the address, which is the failure
    that turns an unsubscribe into a suggestion.

Also checks a confirm link from the old double opt-in still works, for
anybody holding one.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "zznews"


def _sub(client, email):
    return client.post("/newsletter/subscribe", data={"email": email, **_harness.contact_form_fields()},
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

    sent = []
    was_send = m.send_email

    def capture(to, subject, body, *a, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "html": kw.get("html")})
        return True

    m.send_email = capture
    try:
        s.section("Signing up")
        r = _sub(pub, fresh)
        conn = db()
        row = conn.execute(
            "SELECT * FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
        consent = conn.execute(
            "SELECT * FROM consent_events WHERE email = ? AND channel = 'newsletter' "
            "ORDER BY id DESC LIMIT 1", (fresh,)).fetchone()
        conn.close()
        s.check("the address is recorded", row is not None, r)
        s.check("and is a subscriber at once — no confirm step",
                row is not None and row["confirmed_at"] is not None,
                detail=f"confirmed_at={row['confirmed_at'] if row else 'no row'}")
        s.check("it is on the mailing list", fresh in [x["email"] for x in _recipients()])
        s.check("the yes is recorded, with how it was given",
                consent is not None and consent["granted"] == 1
                and "on the site" in (consent["how"] or ""),
                detail=dict(consent) if consent else None)
        s.check("the page thanks them", "Thank you for subscribing" in r.get_data(as_text=True))

        mine = [x for x in sent if x["to"] == fresh]
        s.check("exactly one letter goes to them", len(mine) == 1,
                detail=[x["subject"] for x in mine])
        letter = mine[0] if mine else {"subject": "", "body": "", "html": ""}
        s.check("it is the thank-you, not a request to confirm",
                letter["subject"].startswith("Thank you for subscribing")
                and "confirm" not in letter["body"].lower(),
                detail=letter["subject"])
        s.check("the plain text carries the way out",
                f"/newsletter/unsubscribe/{row['token'] if row else ''}" in letter["body"],
                detail=letter["body"][-200:])
        s.check("the drawn letter carries it in its foot",
                "Unsubscribe" in (letter["html"] or "")
                and f"/newsletter/unsubscribe/{row['token'] if row else ''}" in (letter["html"] or ""))

        conn = db()
        on_offer = m.newsletter_workshops(conn)
        conn.close()
        if on_offer:
            first = on_offer[0]
            s.check("the workshops on offer are in it, as cards with their dates",
                    first["title"] in (letter["html"] or "") and first["when"] in (letter["html"] or "")
                    and "See the workshop" in (letter["html"] or ""))
            s.check("and listed in the plain text with their links",
                    first["title"] in letter["body"] and first["url"] in letter["body"])
        else:
            s.check("with nothing on, no empty 'Coming up' heading is printed",
                    "Coming up" not in letter["body"]
                    and "Coming up" not in (letter["html"] or ""))
        s.check("the workshops it advertises are only the ones the public page offers",
                all(w["url"].rstrip("/").split("/")[-1].isdigit() for w in on_offer))

        s.section("The form does not leak who is on the list")
        before = len(sent)
        known = _sub(pub, fresh).get_data(as_text=True)
        s.check("typing a subscribed address again sends it nothing",
                not [x for x in sent[before:] if x["to"] == fresh])
        unknown = _sub(pub, f"{TAG}.other@example.invalid").get_data(as_text=True)
        # Compare the flash wording, which is the only thing a stranger can read.
        s.check("an existing address and a new one get the same answer",
                ("Thank you for subscribing" in known) and ("Thank you for subscribing" in unknown))

        s.section("A confirm link from before still works")
        old = f"{TAG}.pending@example.invalid"
        conn = db()
        conn.execute("""INSERT INTO newsletter_subscribers (email, token, source, created_at)
                        VALUES (?, 'zznews-pending-token', 'site', '2026-10-01T10:00:00+00:00')""",
                     (old,))
        conn.commit()
        conn.close()
        r2 = pub.get("/newsletter/confirm/zznews-pending-token", follow_redirects=True)
        conn = db()
        after = conn.execute(
            "SELECT * FROM newsletter_subscribers WHERE email = ?", (old,)).fetchone()
        conn.close()
        s.check("an old link sets confirmed_at", after["confirmed_at"] is not None, r2)
        s.check("a bad token is a 404, not a crash",
                pub.get("/newsletter/confirm/not-a-real-token").status_code == 404)

        s.section("Unsubscribing sticks")
        token = row["token"]
        r3 = pub.post(f"/newsletter/unsubscribe/{token}", follow_redirects=True)
        conn = db()
        gone = conn.execute(
            "SELECT * FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
        conn.close()
        s.check("unsubscribing clears the subscription", gone["confirmed_at"] is None, r3)
        s.check("and it is off the mailing list", fresh not in [x["email"] for x in _recipients()])
        s.check("it is recorded on the shared opt-out list too", _opted_out(fresh))

        # The important one: re-typing the address must not quietly resurrect them.
        before = len(sent)
        _sub(pub, fresh)
        conn = db()
        again = conn.execute(
            "SELECT confirmed_at FROM newsletter_subscribers WHERE email = ?", (fresh,)).fetchone()
        conn.close()
        s.check("re-subscribing an opted-out address does NOT subscribe it again",
                again["confirmed_at"] is None)
        s.check("still off the mailing list", fresh not in [x["email"] for x in _recipients()])
        s.check("and nothing is sent to it", not [x for x in sent[before:] if x["to"] == fresh])

        s.section("Guards")
        r4 = _sub(pub, "not-an-email")
        conn = db()
        junk = conn.execute(
            "SELECT 1 FROM newsletter_subscribers WHERE email = 'not-an-email'").fetchone()
        conn.close()
        s.check("a malformed address is refused and stored nowhere", junk is None, r4)
        s.check("the confirm letter is retired, and so is its row",
                "newsletter_confirm" not in m.EMAIL_TEMPLATE_LABELS
                and _row_gone("newsletter_confirm"))
    finally:
        m.send_email = was_send

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


def _row_gone(key):
    conn = db()
    try:
        return conn.execute("SELECT 1 FROM email_templates WHERE template_key = ?",
                            (key,)).fetchone() is None
    finally:
        conn.close()
