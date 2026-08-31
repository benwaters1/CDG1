"""What the person teaching the workshop takes into the room.

Everything an instructor needs was already recorded — dietary notes, medical
notes, rooms, roommate requests, and the answers to the workshop's own
registration questions — and all of it lived on
/admin/workshops/registrations, which is @owner_required. So the one person
who has to walk in knowing it could not open the page. What they had was a
notification per booking and a five-row roster on their own home page with
no custom answers on it.

Two things this suite holds in place, and they pull against each other:

  - THE INSTRUCTOR CAN SEE IT. That is the whole point.
  - AND CANNOT SEE THE MONEY. The registrations page carries balances,
    payments, refunds and a repricing form. This page does not load any of
    it, so no later edit to the template can put a guest's outstanding
    balance in front of a visiting instructor — which is a stronger promise
    than an {% if %} around each figure, because that promise has to be
    re-kept every time somebody adds a row.

An instructor sees their own workshops and no others, and a session that is
not theirs comes back 404 rather than 403: "that exists and you may not see
it" is a fact about the house they have no need for.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZWSHEET"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM workshop_custom_field_responses WHERE workshop_booking_id IN "
        "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_booking_guests WHERE workshop_booking_id IN "
        "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_custom_fields WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_sessions WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The instructor's running sheet")
    today = date.today()
    conn = db()
    oc, ec, _owner, emp = clients()
    _cleanup(conn)

    if not emp:
        s.section("Setup")
        s.check("an employee account exists to act as the instructor", False,
                detail="without one the permission checks below prove nothing, "
                       "so this is reported rather than skipped quietly")
        conn.close()
        return s

    now = m.datetime.now(m.timezone.utc).isoformat()

    # Theirs, and one that is not.
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, instructor_user_id,
                                  instructor_name, created_at)
           VALUES (?, '', 400, 12, 1, ?, ?, ?)""",
        (TAG + " Indigo Dyeing", emp["id"], emp["name"], now))
    mine = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', 400, 12, 1, ?)""",
        (TAG + " Someone Else's Stonework", now))
    theirs = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def add_session(workshop_id):
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                              capacity, created_at)
               VALUES (?, ?, ?, 12, ?)""",
            (workshop_id, (today + timedelta(days=20)).isoformat(),
             (today + timedelta(days=24)).isoformat(), now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    my_session = add_session(mine)
    other_session = add_session(theirs)

    # A registration question, which is the thing the instructor could never
    # see: somebody set it because the answer changes how it is taught.
    conn.execute(
        """INSERT INTO workshop_custom_fields (workshop_id, label, field_type,
                                               required, sort_order, created_at)
           VALUES (?, 'How much have you dyed before?', 'text', 0, 0, ?)""",
        (mine, now))
    field_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def register(session_id, name, status="confirmed", **kw):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                              party_size, status, reference_code,
                                              manage_token, dietary_notes,
                                              medical_notes, special_occasion,
                                              requested_roommate, total_price,
                                              created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, name, f"{name.lower()}@example.invalid",
             kw.get("party_size", 1), status,
             f"{TAG}{session_id}{name[:3].upper()}",
             f"tok-{TAG.lower()}-{session_id}-{name.lower()}",
             kw.get("dietary"), kw.get("medical"), kw.get("occasion"),
             kw.get("roommate"), 1234.56, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    aline = register(my_session, "Aline", dietary="Coeliac, strictly",
                     medical="Carries an EpiPen", occasion="Her sixtieth",
                     roommate="Margot")
    register(my_session, "Bruno", party_size=2)
    # Not paid for: should not be on a sheet of who is coming.
    register(my_session, "Chantal", status="pending",
             dietary="No shellfish at all")
    register(other_session, "Damien")

    conn.execute(
        """INSERT INTO workshop_custom_field_responses (workshop_booking_id,
                                                        custom_field_id, value,
                                                        created_at)
           VALUES (?, ?, 'Never — completely new to it', ?)""",
        (aline, field_id, now))
    conn.execute(
        """INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name,
                                                is_lead, created_at)
           VALUES (?, 'Bruno', 1, ?)""", (aline, now))
    conn.commit()

    s.section("The instructor can open the sheet for their own workshop")
    r = ec.get(f"/workshops/{my_session}/sheet")
    body = r.get_data(as_text=True)
    s.check("it opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("naming who is coming", "Aline" in body and "Bruno" in body)
    s.check("with the dietary note", "Coeliac, strictly" in body)
    s.check("and the medical one", "Carries an EpiPen" in body)
    s.check("and what they answered on the registration form",
            "completely new to it" in body,
            detail="the part that was owner-only, and the part that changes "
                   "how the workshop is taught")
    s.check("the question is named, not just the answer",
            "How much have you dyed before?" in body,
            detail="an answer with no question against it is unreadable")
    s.check("who asked to share with whom", "Margot" in body)
    s.check("and anything worth knowing", "Her sixtieth" in body)

    s.section("Somebody who has not paid is not on it")
    s.check("a pending registration is left off", "Chantal" not in body,
            detail="a sheet of who is coming that lists somebody who has not "
                   "booked is a sheet the instructor stops trusting")
    s.check("and so is their dietary note", "No shellfish at all" not in body,
            detail="the kitchen would cook around somebody who is not coming")

    s.section("And no money anywhere on it")
    # Not hidden — never loaded. The registrations page carries balances,
    # payments, refunds and a repricing form, and opening THAT to instructors
    # would mean a guard around each one and around every one added later.
    s.check("no figure from the registration appears", "1234.56" not in body,
            detail="every guest on this sheet has a total_price of 1234.56 "
                   "against them in the database")
    for word in ("Balance due", "Total charged", "Refund"):
        s.check(f"no {word.lower()}", word not in body)

    s.section("An instructor sees their own workshops and no others")
    r = ec.get(f"/workshops/{other_session}/sheet")
    s.check("a session they do not teach is refused", r.status_code == 404,
            detail=f"HTTP {r.status_code}")
    s.check("as not-found rather than forbidden", r.status_code != 403,
            detail="\"that exists and you may not see it\" is a fact about "
                   "the house an instructor has no need for")

    s.section("The owner can see any of them")
    r = oc.get(f"/workshops/{other_session}/sheet")
    s.check("including one they do not teach", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and it names that session's guest",
            "Damien" in r.get_data(as_text=True))

    s.section("A stranger cannot")
    anon = m.app.test_client()
    r = anon.get(f"/workshops/{my_session}/sheet", follow_redirects=False)
    s.check("logged out, it redirects to the login form",
            r.status_code in (302, 303) and "/login" in r.headers.get("Location", ""),
            detail=f"HTTP {r.status_code} to {r.headers.get('Location')}")

    s.section("It is reachable")
    # A page nobody can navigate to is a URL, not a feature.
    listing = oc.get("/admin/workshops").get_data(as_text=True)
    s.check("linked from the sessions list",
            f"/workshops/{my_session}/sheet" in listing)

    s.section("A session with nobody in it still opens")
    empty = add_session(mine)
    conn.commit()
    r = ec.get(f"/workshops/{empty}/sheet")
    body = r.get_data(as_text=True)
    s.check("it opens rather than erroring", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and says so plainly", "Nobody has confirmed a place yet" in body)
    s.check("without an empty kitchen table on it",
            "For the kitchen" not in body,
            detail="an empty section headed 'For the kitchen' reads as "
                   "'nothing to cook around', which is not the same as "
                   "'nobody has booked'")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
