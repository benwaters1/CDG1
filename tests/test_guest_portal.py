"""One link that shows a guest everything of theirs.

Every other guest link here is per-booking: a stay, an atelier registration and
a dinner each carry their own manage token. Somebody who has been three times
was holding three unrelated URLs with no way to see themselves, which is what
the owner asked for — an account reachable from a link in an email.

Two things need proving. That it gathers the right guest's things, and that a
token reaches nothing else: it is the only credential, so a token that leaked
across guests would hand one person another's bookings and contact details.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZPORT"
MINE = f"{TAG.lower()}.mine@example.invalid"
THEIRS = f"{TAG.lower()}.theirs@example.invalid"


def _cleanup():
    conn = db()
    for email in (MINE, THEIRS):
        conn.execute("DELETE FROM bookings WHERE guest_email = ?", (email,))
        conn.execute("DELETE FROM workshop_bookings WHERE guest_email = ?", (email,))
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email = ?", (email,))
        conn.execute("DELETE FROM guests WHERE email = ?", (email,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _guest(email, name):
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (name, email, _harness.datetime_now()))
    conn.commit()
    conn.close()


def _room():
    conn = db()
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night, sort_order)
           VALUES (?, ?, 1, 4, 200.0, 996)""", (f"{TAG} Room", _harness.secrets_token()))
    conn.commit()
    rid = conn.execute("SELECT id FROM rooms WHERE name = ?", (f"{TAG} Room",)).fetchone()["id"]
    conn.close()
    return rid


def _stay(room_id, email, ref, status="confirmed", days_out=260):
    conn = db()
    arrival = house_today() + timedelta(days=days_out)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?)""",
        (room_id, ref, f"tok{ref}", f"{TAG} guest", email, arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(), status, _harness.datetime_now()))
    conn.commit()
    conn.close()


def _atelier(email, ref):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at) VALUES (?, '', 500, 10, 1, 95, ?)""",
        (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=310)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), f"{TAG} session", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} session",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at)
           VALUES (?, ?, ?, 2, 'confirmed', ?, ?, ?)""",
        (sid, f"{TAG} guest", email, ref, f"tok{ref}", now))
    conn.commit()
    conn.close()


def run():
    s = Suite("Guest portal")
    _cleanup()
    room_id = _room()
    _guest(MINE, f"{TAG} Mine")
    _guest(THEIRS, f"{TAG} Theirs")
    _stay(room_id, MINE, f"{TAG}S1")
    _atelier(MINE, f"{TAG}W1")
    _stay(room_id, THEIRS, f"{TAG}S2", days_out=500)

    s.section("A guest gets one standing link")
    conn = db()
    token = m.guest_portal_token(conn, MINE)
    conn.commit()
    again = m.guest_portal_token(conn, MINE)
    conn.commit()
    conn.close()
    s.check("a token is minted", bool(token), detail=f"got {token!r}")
    s.check("and it is the same one next time — the link in an old email keeps working",
            token == again, detail=f"{token!r} vs {again!r}")

    conn = db()
    unknown = m.guest_portal_token(conn, "nobody.here@example.invalid")
    blank = m.guest_portal_token(conn, "")
    conn.close()
    s.check("someone with no profile gets no link", unknown is None, detail=f"got {unknown!r}")
    s.check("and neither does an empty address", blank is None, detail=f"got {blank!r}")

    s.section("It shows them their own things")
    pub = m.app.test_client()
    r = pub.get(f"/my/{token}")
    body = r.get_data(as_text=True)
    s.check("the page opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("their stay is listed", f"{TAG}S1" in body)
    s.check("their atelier is listed", f"{TAG} Atelier" in body)
    s.check("with a way into each one",
            f"tok{TAG}S1" in body and f"tok{TAG}W1" in body,
            detail="a manage link is missing")

    s.section("And nothing of anybody else's")
    # The token is the only credential, so this is the check that matters.
    s.check("another guest's booking reference is absent", f"{TAG}S2" not in body)
    s.check("another guest's email address is absent", THEIRS not in body)
    conn = db()
    other_token = m.guest_portal_token(conn, THEIRS)
    conn.commit()
    conn.close()
    other_body = pub.get(f"/my/{other_token}").get_data(as_text=True)
    s.check("their link shows theirs and not mine",
            f"{TAG}S2" in other_body and f"{TAG}S1" not in other_body)
    s.check("the two links differ", token != other_token)

    s.section("A bad link is a clean 404, not a hint")
    for bad, label in (("not-a-real-token", "an invented token"),
                       ("", "an empty token")):
        r = pub.get(f"/my/{bad}")
        s.check(f"{label} is refused", r.status_code == 404, detail=f"HTTP {r.status_code}")

    s.section("Cancelled things drop off")
    conn = db()
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE reference_code = ?", (f"{TAG}S1",))
    conn.commit()
    conn.close()
    body = pub.get(f"/my/{token}").get_data(as_text=True)
    s.check("a cancelled stay is no longer shown", f"{TAG}S1" not in body)
    s.check("but the atelier still is", f"{TAG} Atelier" in body)

    s.section("The link actually reaches the guest")
    # Built by hand rather than from a template, so confirming a booking is the
    # one place a first-time guest is certain to be given it. Without this the
    # portal only exists for somebody who already opened a per-booking link —
    # which is precisely the problem it was built to solve.
    conn = db()
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE 'Booking confirmed%'")
    conn.execute("UPDATE bookings SET status = 'pending' WHERE reference_code = ?", (f"{TAG}S1",))
    conn.commit()
    pending_id = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                              (f"{TAG}S1",)).fetchone()["id"]
    conn.close()
    oc, ec, owner, emp = clients()
    oc.post(f"/admin/bookings/{pending_id}/confirm", follow_redirects=True)
    conn = db()
    sent = conn.execute(
        "SELECT body FROM email_outbox WHERE subject LIKE 'Booking confirmed%' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    s.check("a confirmation email was produced", sent is not None)
    if sent:
        s.check("and it carries their account link", f"/my/{token}" in sent["body"],
                detail="no portal link in the confirmation email")

    s.section("A guest with nothing booked still has a page")
    conn = db()
    conn.execute("UPDATE workshop_bookings SET status = 'cancelled' WHERE reference_code = ?",
                 (f"{TAG}W1",))
    # The confirm above put the stay back to 'confirmed' — cancel it again, or
    # this section is testing a guest who still has one.
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE reference_code = ?", (f"{TAG}S1",))
    conn.commit()
    conn.close()
    r = pub.get(f"/my/{token}")
    s.check("it opens rather than 404s", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and says so plainly", "Nothing booked" in r.get_data(as_text=True))

    _cleanup()
    return s
