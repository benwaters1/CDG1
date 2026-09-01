"""The link a guest gets to their own account, and what it opens.

`/my-account/<token>` is scoped to an EMAIL, not to one booking. A single token
therefore opens every stay, every dinner and every atelier that address has
ever had with the château — which is the most any one URL in this app can
reach. The manage token beside it opens one booking's bill.

That makes three properties worth more than the rest, and all three are
refusals, which is the shape that most often passes for the wrong reason. Each
is checked by looking at what the page actually contains, never at a status
code alone:

  IT OPENS ONE ADDRESS'S HISTORY AND NO OTHER. A token issued to one guest must
  not show another's booking, and the two are separated only by an email match
  in SQL. This is the check that would catch a WHERE clause being loosened.

  IT EXPIRES. The link goes out by email, and email is forwarded, quoted and
  left open on shared machines. An expired token has to stop working — and
  first use must NOT expire it, because a guest opens the link, wanders off and
  comes back, and single-use would be a support call rather than a security
  feature.

  ASKING FOR A LINK CANNOT BE USED TO ASK WHO HAS STAYED HERE. The request form
  gives the same answer for an address with bookings and one without. Otherwise
  it answers "has this person been to the château", which is not the app's to
  answer about anybody.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZACCT"
MINE = f"{TAG.lower()}.mine@example.invalid"
THEIRS = f"{TAG.lower()}.theirs@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM guest_sessions WHERE email LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()


def _stay(email, ref, days_out=45, total=900.0, paid=200.0, status="confirmed"):
    conn = db()
    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=days_out)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, special_requests, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, ?, ?, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         email, arrival.isoformat(), (arrival + timedelta(days=3)).isoformat(),
         status, total, paid, f"{TAG} a quiet room please",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _session(email, hours=24, used=None):
    """A magic-link session row, as guest_account_request would write it."""
    conn = db()
    token = f"tok{TAG}{email.split('.')[1][:6]}{hours}".lower()
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM guest_sessions WHERE token = ?", (token,))
    conn.execute(
        """INSERT INTO guest_sessions (email, token, created_at, expires_at, used_at)
           VALUES (?, ?, ?, ?, ?)""",
        (email, token, now.isoformat(),
         (now + timedelta(hours=hours)).isoformat(), used))
    conn.commit()
    conn.close()
    return token


def _used_at(token):
    conn = db()
    try:
        row = conn.execute("SELECT used_at FROM guest_sessions WHERE token = ?",
                           (token,)).fetchone()
        return row["used_at"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("Guest account")
    _cleanup()
    clients()
    anon = m.app.test_client()

    mine = _stay(MINE, "MINE")
    theirs = _stay(THEIRS, "THEIRS")
    token = _session(MINE)

    s.section("The link opens that address's own history")
    r = anon.get(f"/my-account/{token}")
    body = r.get_data(as_text=True)
    s.check("it opens", r.status_code == 200, r)
    s.check("and shows their stay", f"{TAG}-MINE" in body,
            detail="the guest cannot see the booking the link was issued for")

    s.section("And nobody else's")
    # The two are separated only by an email match in SQL. This is the check
    # that catches that WHERE clause being loosened.
    s.check("the other guest's reference is absent", f"{TAG}-THEIRS" not in body,
            detail="one guest's link showed another guest's booking")
    s.check("their name is not on the page", f"{TAG} THEIRS" not in body)
    s.check("nor their email", THEIRS not in body,
            detail="an address was disclosed to somebody who is not it")

    s.section("Opening it does not use it up")
    # A guest opens the link, wanders off, and comes back. Single-use would be
    # a support call rather than a protection.
    s.check("first use is recorded", _used_at(token) is not None,
            detail="nothing marks that the link has been opened")
    again = anon.get(f"/my-account/{token}")
    s.check("and it still works afterwards", again.status_code == 200,
            detail=f"HTTP {again.status_code} — the guest came back to a dead link")
    s.check("still showing their booking", f"{TAG}-MINE" in again.get_data(as_text=True))

    s.section("But it does expire")
    # It travels by email, and email is forwarded, quoted, and left open.
    dead = _session(MINE, hours=-1)
    r = anon.get(f"/my-account/{dead}")
    body = r.get_data(as_text=True)
    s.check("an expired link is refused", r.status_code == 404,
            detail=f"HTTP {r.status_code}")
    s.check("and refuses by explaining, not by 500ing",
            "expire" in body.lower() or "link" in body.lower(),
            detail="a guest with an old link gets an error page and no idea why")
    s.check("with none of their booking on it", f"{TAG}-MINE" not in body,
            detail="the expired page still rendered the account behind it")

    s.section("An invented token opens nothing")
    r = anon.get("/my-account/not-a-real-token")
    s.check("refused", r.status_code == 404, detail=f"HTTP {r.status_code}")
    s.check("and shows no booking at all",
            f"{TAG}-" not in r.get_data(as_text=True))

    s.section("Asking for a link says the same thing either way")
    # Otherwise the form answers "has this person stayed at the château",
    # which is not ours to answer about anybody.
    sent = []
    was = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]
    try:
        conn = db()
        conn.execute("DELETE FROM submission_log")
        conn.commit()
        conn.close()
        known = anon.post("/my-account", data={"email": MINE}, follow_redirects=True)
        stranger = anon.post("/my-account", data={"email": f"{TAG.lower()}.nobody@example.invalid"},
                             follow_redirects=True)
        k_body = known.get_data(as_text=True)
        s_body = stranger.get_data(as_text=True)
        s.check("both are accepted", known.status_code == 200 and stranger.status_code == 200)
        s.check("and neither says whether the address is known",
                ("no bookings" not in k_body.lower() and "no bookings" not in s_body.lower()),
                detail="the page tells a stranger whether somebody has stayed here")
        s.check("the flashes match",
                set(flashes(known)) == set(flashes(stranger)),
                detail=f"{flashes(known)[:1]} vs {flashes(stranger)[:1]}")
    finally:
        m.send_email = was

    s.section("But only the address that exists is actually written to")
    s.check("one link went out, to the guest who has a booking",
            sent == [MINE], detail=f"{sent}")

    s.section("The bill is per booking, not per address")
    r = anon.get(f"/booking/{mine['manage_token']}/statement")
    body = r.get_data(as_text=True)
    s.check("it opens with the booking's own token", r.status_code == 200, r)
    s.check("and is about that stay", f"{TAG}-MINE" in body)
    s.check("with the other guest's stay nowhere on it", f"{TAG}-THEIRS" not in body,
            detail="a statement carried a stay belonging to somebody else")
    s.check("it shows what was paid", "200" in body,
            detail="a statement a guest needs for their VAT has no figures on it")

    s.section("A statement is not something a search engine should hold")
    s.check("the page asks not to be indexed", "noindex" in body,
            detail="a bill reachable by URL was left indexable — robots.txt "
                   "does not help, a referrer is enough")

    s.section("And a token nobody issued has no bill")
    s.check("refused", anon.get("/booking/nope/statement").status_code == 404)

    s.section("The check-in link is the same page under a friendlier name")
    r = anon.get(f"/checkin/{mine['manage_token']}")
    s.check("it redirects rather than duplicating the page",
            r.status_code in (301, 302), detail=f"HTTP {r.status_code}")
    s.check("to that booking's own page",
            mine["manage_token"] in (r.headers.get("Location") or ""),
            detail=r.headers.get("Location"))
    followed = anon.get(f"/checkin/{mine['manage_token']}", follow_redirects=True)
    s.check("and it lands somewhere real", followed.status_code == 200)

    _cleanup()
    return s
