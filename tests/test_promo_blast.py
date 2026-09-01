"""Emailing a discount code to every past guest.

This is the only route in the app that sends unsolicited mail to a list. It
respected `email_optouts` on the way in, which is the half that is easy to
notice, and offered nothing on the way out: the message it composed was the
owner's text with two placeholders filled and not one word more. So a guest
who had only ever been sent promo blasts could not get off the list, because
nothing they were sent told them how. Marketing mail has to carry a working
way out and the sender's postal address, and the campaign path next door had
already built both.

The fix routes this through `send_campaign` rather than growing a second copy
of it here, which is why most of what follows checks the JOIN rather than the
parts: the audience is still this route's, and the sending, the per-recipient
unsubscribe key and the record of what went out are the campaign's.

WHAT THIS FILE IS REALLY FOR is the last check in the third section. A footer
containing the word "unsubscribe" satisfies nothing - a link is only a way out
if following it puts the guest on the list this route filters against. So the
token is pulled out of the body that was actually sent, POSTed the way a
guest's browser would, and a second blast is then run to prove they are gone.

THE OTHER CLAIM, which predates the unsubscribe work and is kept here intact:
the count the owner is shown before pressing send has to be the set that
actually gets mailed. A preview that over-counts is its own bug, because it is
the number somebody decides on. It is checked with a guest opted out, so a
preview that ignored opt-outs would differ from the send rather than coincide
with it.

The migration hazard is the fourth section. This form's placeholders are
single-braced and predate the campaign's {{merge tags}}; a body already typed
into the textarea had to keep working, or the first send after the change
would have mailed a literal "{guest_name}" to the entire list.
"""
from datetime import date, datetime, timedelta, timezone
import re

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZBLAST"
ADDR_A = "zzblast.anna@example.invalid"
ADDR_B = "zzblast.bruno@example.invalid"
ADDR_C = "zzblast.chloe@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE 'zzblast.%'")
    conn.execute("DELETE FROM email_optouts WHERE email LIKE 'zzblast.%'")
    conn.execute("DELETE FROM email_optouts WHERE email LIKE 'ZZBLAST.%'")
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _promo(code):
    conn = db()
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
           applies_to, active, created_at) VALUES (?, 'ZZ test', 'percent', 10, 'all', 1, ?)""",
        (code, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM promo_codes WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row


def _guest(ref, email, name, status="confirmed"):
    conn = db()
    room = _harness.ensure_room()
    arrival = house_today() - timedelta(days=30)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 400, 400, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {name}", email,
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _audience(segments=("room",), since=None):
    conn = db()
    try:
        return m.promo_blast_recipients(conn, list(segments), since)
    finally:
        conn.close()


def _sends():
    conn = db()
    try:
        return conn.execute(
            """SELECT * FROM campaign_sends WHERE recipient_email LIKE 'zzblast.%'
               ORDER BY id""").fetchall()
    finally:
        conn.close()


def _optouts():
    conn = db()
    try:
        return {r["email"] for r in conn.execute(
            "SELECT email FROM email_optouts WHERE email LIKE 'zzblast.%'").fetchall()}
    finally:
        conn.close()


def _clear_optouts():
    conn = db()
    conn.execute("DELETE FROM email_optouts WHERE email LIKE 'zzblast.%'")
    conn.execute("DELETE FROM email_optouts WHERE email LIKE 'ZZBLAST.%'")
    conn.commit()
    conn.close()


def _clear_sends():
    conn = db()
    conn.execute("DELETE FROM campaign_sends WHERE recipient_email LIKE 'zzblast.%'")
    conn.commit()
    conn.close()


def run():
    s = Suite("Promo code blast")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    promo = _promo(TAG + "10")
    send_url = f"/admin/promo-codes/{promo['id']}/blast/send"

    _guest("A", ADDR_A, "Anna Past")
    _guest("B", ADDR_B, "Bruno Past")
    _guest("C", ADDR_C, "Chloe Enquiry", status="pending")

    s.section("Who a blast goes to")
    # Asserted against the tagged addresses only: the real database has its own
    # confirmed bookings, so a total would be a fixture detail, not a claim.
    aud = _audience()
    s.check("a past guest is in it", ADDR_A in aud, detail=f"{sorted(aud)[:3]}")
    s.check("and is named, so the greeting has something to use",
            aud.get(ADDR_A) == TAG + " Anna Past", detail=f"got {aud.get(ADDR_A)!r}")
    s.check("a booking never confirmed is not a past guest", ADDR_C not in aud,
            detail="an enquiry that came to nothing was sent marketing mail")

    s.section("Somebody who has unsubscribed stays unsubscribed")
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'ZZ', ?)",
                 (ADDR_B, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    aud = _audience()
    s.check("they are out of the audience", ADDR_B not in aud, detail=f"{sorted(aud)[:3]}")
    s.check("and everybody else is still in", ADDR_A in aud)
    # The address the owner types into the opt-out box by hand is the one that
    # would silently fail to match, and that is the case that matters.
    _clear_optouts()
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'ZZ', ?)",
                 (ADDR_B.upper(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    s.check("however it was capitalised when it was written down",
            ADDR_B not in _audience(),
            detail=f"{ADDR_B.upper()} in the opt-out table did not suppress {ADDR_B}")
    _clear_optouts()

    # From here the route runs against a stand-in audience, so that a test does
    # not mail the whole seeded database. The stand-in records its arguments,
    # which is also how the segment choice is checked to reach it.
    real_audience = m.promo_blast_recipients
    real_send = m.send_email
    asked = []
    sent_mail = []

    def only_ours(conn, segments, since_date_iso=None):
        asked.append((list(segments), since_date_iso))
        full = real_audience(conn, segments, since_date_iso)
        return {e: n for e, n in full.items() if e.startswith("zzblast.")}

    def capture(to_address, subject, body, *a, **kw):
        sent_mail.append((to_address, subject, body))
        return True

    try:
        m.promo_blast_recipients = only_ours
        m.send_email = capture

        s.section("Every message carries a way out")
        r = oc.post(send_url,
                    data={"segment": "room", "subject": "A little something",
                          "body": "Hi {guest_name}, use {promo_code}."},
                    follow_redirects=True)
        s.check("the send goes through", r.status_code == 200, detail=f"HTTP {r.status_code}")
        s.check("the chosen segment reached the audience",
                bool(asked) and asked[-1][0] == ["room"], detail=f"{asked[-1:]}")
        rows = _sends()
        s.check("there is a record of every recipient", len(rows) == 2,
                detail=f"{len(rows)} row(s) - nothing afterwards can say what was sent")
        s.check("recorded against the code it was for",
                bool(rows) and all((row["template_name"] or "").endswith(promo["code"])
                                   for row in rows),
                detail=f"{[row['template_name'] for row in rows]}")
        s.check("and against the person who sent it",
                bool(rows) and all(row["sent_by_user_id"] == owner["id"] for row in rows),
                detail=f"{[row['sent_by_user_id'] for row in rows]}")
        tokens = [row["unsubscribe_token"] for row in rows]
        s.check("each has an unsubscribe key", bool(tokens) and all(tokens),
                detail=f"{tokens}")
        s.check("and no two share one", len(set(tokens)) == len(tokens),
                detail=f"{tokens} - one guest could unsubscribe another")

        by_addr = {to: body for to, _subj, body in sent_mail}
        s.check("both guests were actually mailed", len(by_addr) == 2,
                detail=f"{list(by_addr)}")
        mine = [row for row in rows if row["recipient_email"] == ADDR_A]
        body_a = by_addr.get(ADDR_A, "")
        s.check("the message carries that guest's own key",
                bool(mine) and mine[0]["unsubscribe_token"] in body_a,
                detail="the footer's link points at somebody else's row, or at none")
        s.check("and says who is writing to them", "Gudanes" in body_a,
                detail=f"...{body_a[-160:]!r}")

        # The check this file exists for. A link is only a way out if following
        # it puts them on the list the audience is filtered against.
        found = re.search(r"/unsubscribe/([A-Za-z0-9_-]+)", body_a)
        s.check("the footer carries a followable link", bool(found),
                detail=f"...{body_a[-200:]!r}")
        if found and mine:
            token = found.group(1)
            s.check("which is this guest's key", token == mine[0]["unsubscribe_token"],
                    detail="the link in the message is not the key in the record")
            g = anon.get(f"/unsubscribe/{token}")
            s.check("opening it shows a page rather than acting", g.status_code == 200,
                    detail=f"HTTP {g.status_code}")
            s.check("and merely opening it opts nobody out", ADDR_A not in _optouts(),
                    detail="a mail client that fetches every link would unsubscribe them")
            p = anon.post(f"/unsubscribe/{token}")
            s.check("confirming it does", p.status_code == 200 and ADDR_A in _optouts(),
                    detail=f"HTTP {p.status_code}, optouts={_optouts()}")

            s.section("And the next blast leaves them alone")
            sent_mail.clear()
            oc.post(send_url, data={"segment": "room", "subject": "Another offer",
                                    "body": "Hi {guest_name}."}, follow_redirects=True)
            mailed = {to for to, _s, _b in sent_mail}
            s.check("they are not mailed again", ADDR_A not in mailed,
                    detail=f"{sorted(mailed)} - the unsubscribe link resolved "
                           "and changed nothing")
            s.check("and the guest who did not unsubscribe still is", ADDR_B in mailed,
                    detail=f"{sorted(mailed)} - one unsubscribe silenced the whole list")

        s.section("Bodies already typed into this form still work")
        _clear_optouts()
        sent_mail.clear()
        oc.post(send_url,
                data={"segment": "room", "subject": "Code {promo_code} for you",
                      "body": "Hi {guest_name}, {{guest_name}} too, code {promo_code}."},
                follow_redirects=True)
        one = [(to, sub, b) for to, sub, b in sent_mail if to == ADDR_A]
        s.check("the guest was mailed", bool(one),
                detail=f"{[t for t, _s, _b in sent_mail]}")
        if one:
            _to, subj, body = one[0]
            s.check("the old single-braced name is filled", TAG + " Anna Past" in body,
                    detail=f"{body[:90]!r}")
            s.check("and the double-braced form works too",
                    body.count(TAG + " Anna Past") == 2, detail=f"{body[:130]!r}")
            s.check("the code is filled", promo["code"] in body, detail=f"{body[:90]!r}")
            s.check("in the subject line as well", promo["code"] in subj, detail=f"{subj!r}")
            s.check("and no unfilled placeholder is left in the message",
                    "{guest_name}" not in body and "{promo_code}" not in body,
                    detail=f"{body[:130]!r} - a literal placeholder reached a guest")

        s.section("The number the owner presses send on is the number that goes")
        # The preview and the send are two calls to the same function, and the
        # count on the button is what somebody decides on. A preview that
        # over-counts is its own bug: the decision was made on a number that
        # was never real. Checked with one guest opted out, so a preview that
        # ignored opt-outs would differ from the send rather than coincide.
        _clear_optouts()
        conn = db()
        conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'ZZ', ?)",
                     (ADDR_B, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        page = oc.get(f"/admin/promo-codes/{promo['id']}/blast?segment=room")
        html = page.get_data(as_text=True)
        s.check("the preview page loads", page.status_code == 200,
                detail=f"HTTP {page.status_code}")
        s.check("an opted-out guest is not named on it", ADDR_B not in html,
                detail="the owner is looking at somebody who will not be mailed")
        shown = re.search(r"<strong>(\d+)</strong>\s*guest", html)
        s.check("it shows a count", bool(shown), detail="no count to decide on")
        sent_mail.clear()
        oc.post(send_url, data={"segment": "room", "subject": "Offer",
                                "body": "Hi {guest_name}."}, follow_redirects=True)
        if shown:
            s.check("and it is exactly how many are mailed",
                    int(shown.group(1)) == len(sent_mail),
                    detail=f"the page said {shown.group(1)}, {len(sent_mail)} went")
        s.check("the opted-out guest is not among them",
                ADDR_B not in {to for to, _s, _b in sent_mail},
                detail=f"{[to for to, _s, _b in sent_mail]}")
        _clear_optouts()

        s.section("A send that fails is recorded, not lost")
        _clear_sends()
        m.send_email = lambda *a, **kw: False
        r = oc.post(send_url, data={"segment": "room", "subject": "Will not arrive",
                                    "body": "Hi {guest_name}."}, follow_redirects=True)
        rows = _sends()
        s.check("a row is still written for each", len(rows) == 2, detail=f"{len(rows)}")
        s.check("marked failed rather than sent",
                bool(rows) and all(row["status"] == "failed" for row in rows),
                detail=f"{[row['status'] for row in rows]}")
        said = " ".join(flashes(r)).lower()
        s.check("and the owner is told, not shown a success", "fail" in said,
                detail=f"{flashes(r)[:1]} - a blast nobody received was reported as sent")
        m.send_email = capture

        s.section("Nothing goes without a subject and a message")
        _clear_sends()
        sent_mail.clear()
        for label, data in (("no subject", {"segment": "room", "subject": "  ", "body": "Hi"}),
                            ("no message", {"segment": "room", "subject": "Hi", "body": "  "})):
            oc.post(send_url, data=data, follow_redirects=True)
            s.check(f"{label}: nobody is mailed", not sent_mail, detail=f"{sent_mail[:1]}")
        s.check("and nothing is recorded as having gone", not _sends(),
                detail=f"{len(_sends())} row(s)")

        s.section("Guards")
        sent_mail.clear()
        code = ec.post(send_url, data={"segment": "room", "subject": "Hi",
                                       "body": "Hi"}).status_code
        s.check("an employee cannot send one", code in (302, 403), detail=f"HTTP {code}")
        s.check("and none went", not sent_mail, detail=f"{sent_mail[:1]}")
        r = oc.post("/admin/promo-codes/999999/blast/send",
                    data={"segment": "room", "subject": "Hi", "body": "Hi"})
        s.check("a code that does not exist is a 404, not a 500", r.status_code == 404,
                detail=f"HTTP {r.status_code}")
        s.check("still none went", not sent_mail, detail=f"{sent_mail[:1]}")
        # The page that composes it is guarded on its own. Reaching it leaks
        # the audience — who has stayed here, and how many of them.
        code = ec.get(f"/admin/promo-codes/{promo['id']}/blast").status_code
        s.check("an employee cannot open the compose page either",
                code in (302, 403), detail=f"HTTP {code}")
        s.check("and an unknown code is a 404 there too",
                oc.get("/admin/promo-codes/999999/blast").status_code == 404)
    finally:
        m.promo_blast_recipients = real_audience
        m.send_email = real_send

    s.section("And the stand-ins are gone for whoever runs next")
    s.check("the real audience function is back",
            m.promo_blast_recipients is real_audience,
            detail="a stand-in outlived this suite")
    s.check("and the real sender is too", m.send_email is real_send)

    _cleanup()
    return s
