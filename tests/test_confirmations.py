"""The four pages a guest lands on straight after paying.

Room, restaurant, workshop and event each have one, each rendered from a
manage_token, and until now only the room one was ever exercised — the other
three tables had no rows carrying a token, so a handover could restyle all
four and three of them would go out having never been loaded once.

They are the worst pages in the app to get wrong. The guest has already paid;
a 500 here is a guest who has been charged and shown an error, with a
reference code they never saw.

The specific failure worth guarding: these templates read joined columns —
room_name, title, start_date — that are not on the base table. Dropping a JOIN
from the query does NOT raise; Jinja swallows the lookup and the page still
returns 200 with the room's name simply absent. Checked by removing the rooms
JOIN: status stayed 200 and only the name check caught it.

That is why the checks below look for the actual values rather than the status
code. A confirmation page that loads but has lost the guest's room name is
exactly the kind of failure a status check waves through.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "confirm-"


def _cleanup(conn):
    for table in ("restaurant_bookings", "workshop_bookings", "event_inquiries"):
        conn.execute(f"DELETE FROM {table} WHERE manage_token LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The confirmation pages")
    _oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()
    soon = (m.house_today() + timedelta(days=30)).isoformat()

    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, total_price, created_at)
           VALUES (?, ?, 'Test Diner', 'diner@example.com', 2, ?, 'confirmed', 130.0, ?)""",
        (TAG + "R1", TAG + "rest", soon, now))
    session = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY start_date DESC LIMIT 1").fetchone()
    if session:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                 guest_name, guest_email, status, created_at)
               VALUES (?, ?, ?, 'Test Guest', 'guest@example.com', 'confirmed', ?)""",
            (session["id"], TAG + "W1", TAG + "ws", now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
             contact_name, contact_email, created_at)
           VALUES (?, ?, 'wedding', 'Test Host', 'host@example.com', ?)""",
        (TAG + "E1", TAG + "event", now))
    conn.commit()

    s.section("Each one loads")
    room = conn.execute(
        "SELECT manage_token FROM bookings WHERE manage_token IS NOT NULL LIMIT 1").fetchone()
    pages = [
        ("the room booking", f"/book/confirmation/{room['manage_token']}" if room else None),
        ("the restaurant booking", f"/restaurant/confirmation/{TAG}rest"),
        ("the workshop booking", f"/workshops/confirmation/{TAG}ws" if session else None),
        ("the event enquiry", f"/events/confirmation/{TAG}event"),
    ]
    bodies = {}
    for label, url in pages:
        if not url:
            continue
        r = anon.get(url)
        s.check(f"{label} confirms", r.status_code == 200, detail=str(r.status_code))
        bodies[label] = r.get_data(as_text=True)

    s.section("Each shows the guest what they need to come back")
    # The reference code is the thing a guest writes down. A confirmation page
    # that does not show it has failed at its one job.
    for label, code in [("the restaurant booking", TAG + "R1"),
                        ("the workshop booking", TAG + "W1"),
                        ("the event enquiry", TAG + "E1")]:
        if label not in bodies:
            continue
        s.check(f"{label} shows its reference code", code in bodies[label],
                detail=f"{code} missing from the page")

    s.section("Joined columns the base table does not have")
    # booking_confirmation reads room_name, workshop_confirmation reads title
    # and the session dates. None of those live on the booking row itself.
    if room and "the room booking" in bodies:
        name = conn.execute(
            """SELECT rooms.name FROM bookings JOIN rooms ON rooms.id = bookings.room_id
               WHERE bookings.manage_token = ?""", (room["manage_token"],)).fetchone()
        s.check("the room's name is on its confirmation",
                name and name["name"] in bodies["the room booking"],
                detail=f"expected {name['name'] if name else '?'!r}")
    if session and "the workshop booking" in bodies:
        w = conn.execute(
            """SELECT workshops.title FROM workshop_sessions
                 JOIN workshops ON workshops.id = workshop_sessions.workshop_id
                WHERE workshop_sessions.id = ?""", (session["id"],)).fetchone()
        s.check("the workshop's title is on its confirmation",
                w and w["title"] in bodies["the workshop booking"],
                detail=f"expected {w['title'] if w else '?'!r}")

    s.section("A token nobody issued is a 404, not a 500")
    for label, url in [("room", "/book/confirmation/nope"),
                       ("restaurant", "/restaurant/confirmation/nope"),
                       ("workshop", "/workshops/confirmation/nope"),
                       ("event", "/events/confirmation/nope")]:
        s.check(f"an unknown {label} token is refused cleanly",
                anon.get(url).status_code == 404,
                detail=str(anon.get(url).status_code))

    s.section("They are public — the guest is not signed in")
    # A confirmation page behind a login is a confirmation page nobody reads.
    s.check("no login is required", anon.get(f"/restaurant/confirmation/{TAG}rest")
            .status_code == 200)

    _cleanup(conn)
    conn.close()
    return s
