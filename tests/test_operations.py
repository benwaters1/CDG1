"""The rest of the day-to-day modules, driven end to end.

Workshops, shifts, the timesheet, the shopping list, contacts,
announcements, candidates, guest profiles, vehicles and the waitlist.

Everything created is tagged ZZOPS and deleted at the end.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOPS"
TODAY = datetime.now(timezone.utc).date()
FAR = (TODAY + timedelta(days=340)).isoformat()
FAR2 = (TODAY + timedelta(days=342)).isoformat()


def run():
    s = Suite("Operations")
    oc, ec, owner, emp = clients()
    pub = m.app.test_client()

    conn = db()
    vehicle = conn.execute("SELECT id FROM vehicles LIMIT 1").fetchone()
    # Build our own workshop and session rather than borrowing one. The
    # database had none at all when this was written — the seeded workshops
    # had been deleted during earlier testing — so a suite that depends on
    # finding one silently tests nothing exactly when it matters.
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO workshops (title, description, instructor_name, price_per_person,
           default_capacity, active, sort_order, created_at, deposit_percent)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (f"{TAG} Watercolour week", "A test workshop.", "A. Tutor", 900.0, 8, 1, 99, now, 30))
    workshop_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
           notes, created_at) VALUES (?,?,?,?,?,?)""",
        (workshop_id, FAR, FAR2, 8, f"{TAG} session", now))
    session = {"id": cur.lastrowid}
    conn.commit()
    conn.close()

    s.section("Workshops: public registration, owner confirms")
    if session:
        r = pub.post(f"/workshops/register/{session['id']}", data={
            "guest_name": f"{TAG} Attendee", "guest_email": f"{TAG.lower()}@example.invalid",
            "party_size": "1", "occupancy_type": "shared", "agree_terms": "on",
        }, follow_redirects=True)
        conn = db()
        booking = conn.execute("SELECT * FROM workshop_bookings WHERE guest_name LIKE ?",
                               (TAG + "%",)).fetchone()
        conn.close()
        s.check("a public registration creates a booking", booking is not None, r)
        if booking:
            oc.post(f"/admin/workshops/registrations/{booking['id']}/confirm",
                    follow_redirects=True)
            conn = db()
            st = conn.execute("SELECT status FROM workshop_bookings WHERE id=?",
                              (booking["id"],)).fetchone()["status"]
            conn.close()
            s.check("confirming flips it to confirmed", st == "confirmed", detail=f"got {st}")
    else:                                          # pragma: no cover
        print("    ....  skipped: could not create a workshop session")

    s.section("Shifts")
    r = oc.post("/admin/shifts/new", data={
        "user_ids": str(emp["id"]), "shift_date": FAR, "start_time": "09:00",
        "end_time": "17:00", "role_note": f"{TAG} cover",
    }, follow_redirects=True)
    conn = db()
    shift = conn.execute("SELECT * FROM shifts WHERE role_note LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can schedule a shift", shift is not None, r)
    if shift:
        oc.post(f"/admin/shifts/{shift['id']}/delete", follow_redirects=True)
        conn = db()
        n = conn.execute("SELECT COUNT(*) c FROM shifts WHERE id=?", (shift["id"],)).fetchone()["c"]
        conn.close()
        s.check("owner can delete a shift", n == 0, detail=f"{n} rows remain")

    s.section("Timesheet")
    # Counted rather than asserted absolute: an employee may legitimately
    # already be clocked in, and this must not read as a failure.
    def open_entries():
        conn = db()
        n = conn.execute(
            "SELECT COUNT(*) c FROM time_entries WHERE user_id=? AND clock_out_at IS NULL",
            (emp["id"],)).fetchone()["c"]
        conn.close()
        return n

    before = open_entries()
    r1 = ec.post("/clock/in", follow_redirects=True)
    s.check("employee can clock in", open_entries() >= 1, r1,
            detail=f"open entries={open_entries()}")
    r2 = ec.post("/clock/out", follow_redirects=True)
    s.check("employee can clock out", open_entries() == before, r2,
            detail=f"open entries={open_entries()}, was {before}")

    s.section("Shopping list")
    r = ec.post("/shopping/new", data={"name": f"{TAG} olive oil", "category": "Kitchen"},
                follow_redirects=True)
    conn = db()
    item = conn.execute("SELECT * FROM shopping_items WHERE name LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("staff can add a shopping item", item is not None, r)
    if item:
        ec.post(f"/shopping/{item['id']}/toggle")
        conn = db()
        bought = conn.execute("SELECT bought FROM shopping_items WHERE id=?",
                              (item["id"],)).fetchone()["bought"]
        conn.close()
        s.check("toggling marks it bought", bought == 1, detail=f"bought={bought}")

    s.section("Contacts")
    # View for all, edit owner-only: all three mutations are @owner_required.
    r = oc.post("/contacts/new", data={"name": f"{TAG} Plumber", "phone": "0600",
                                       "role": "Trades"}, follow_redirects=True)
    conn = db()
    contact = conn.execute("SELECT * FROM contacts WHERE name LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can add a contact", contact is not None, r)
    denied = ec.post("/contacts/new", data={"name": f"{TAG} Sneaky", "phone": "1"})
    conn = db()
    leaked = conn.execute("SELECT COUNT(*) c FROM contacts WHERE name LIKE ?",
                          (TAG + " Sneaky%",)).fetchone()["c"]
    conn.close()
    s.check("employee cannot add a contact",
            denied.status_code in (302, 403) and leaked == 0,
            detail=f"status={denied.status_code} rows={leaked}")

    s.section("Announcements")
    r = oc.post("/announcements/new", data={"title": f"{TAG} notice", "body": "test",
                                            "starts_on": TODAY.isoformat()},
                follow_redirects=True)
    conn = db()
    ann = conn.execute("SELECT * FROM announcements WHERE title LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can post an announcement", ann is not None, r)

    s.section("Candidates")
    r = oc.post("/candidates/new", data={"name": f"{TAG} Applicant",
                                         "role_applied": "Housekeeper"}, follow_redirects=True)
    conn = db()
    cand = conn.execute("SELECT * FROM candidates WHERE name LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can add a candidate", cand is not None, r)
    if cand:
        oc.post(f"/candidates/{cand['id']}/status", data={"status": "interviewing"},
                follow_redirects=True)
        conn = db()
        st = conn.execute("SELECT status FROM candidates WHERE id=?",
                          (cand["id"],)).fetchone()["status"]
        conn.close()
        s.check("candidate status advances", st == "interviewing", detail=f"got {st}")

    s.section("Guest profiles")
    r = oc.post("/guests/new", data={"name": f"{TAG} Profile",
                                     "email": f"{TAG.lower()}p@example.invalid", "vip": "1"},
                follow_redirects=True)
    conn = db()
    guest = conn.execute("SELECT * FROM guests WHERE name LIKE ?", (TAG + "%",)).fetchone()
    conn.close()
    s.check("owner can create a guest profile", guest is not None, r)
    if guest:
        oc.post(f"/guests/{guest['id']}/edit",
                data={"name": f"{TAG} Profile", "email": f"{TAG.lower()}p@example.invalid",
                      "dietary_notes": "coeliac"}, follow_redirects=True)
        conn = db()
        notes = conn.execute("SELECT dietary_notes FROM guests WHERE id=?",
                             (guest["id"],)).fetchone()["dietary_notes"]
        conn.close()
        s.check("editing saves dietary notes", notes == "coeliac", detail=f"got {notes!r}")

    s.section("Vehicles")
    if vehicle:
        r = oc.post(f"/management/vehicles/{vehicle['id']}/checkout",
                    data={"user_id": str(emp["id"]), "purpose": f"{TAG} airport run"},
                    follow_redirects=True)
        conn = db()
        usage = conn.execute("SELECT * FROM vehicle_usage WHERE purpose LIKE ?",
                             (TAG + "%",)).fetchone()
        conn.close()
        s.check("a vehicle can be checked out", usage is not None, r)
        if usage:
            oc.post(f"/management/vehicles/{vehicle['id']}/checkin", follow_redirects=True)
            conn = db()
            back = conn.execute("SELECT checked_in_at FROM vehicle_usage WHERE id=?",
                                (usage["id"],)).fetchone()["checked_in_at"]
            conn.close()
            s.check("and checked back in", back is not None)
    else:
        print("    ....  skipped: no vehicle on file")

    s.section("Waitlist")
    r = pub.post("/waitlist/join", data={
        "name": f"{TAG} Hopeful", "email": f"{TAG.lower()}w@example.invalid",
        "desired_arrival": FAR, "desired_departure": FAR2, "party_size": "2",
    }, follow_redirects=True)
    conn = db()
    entry = conn.execute("SELECT * FROM waitlist_entries WHERE name LIKE ?",
                         (TAG + "%",)).fetchone()
    conn.close()
    s.check("a guest can join the room waitlist", entry is not None, r)

    conn = db()
    for table, column in [
        ("workshop_bookings", "guest_name"), ("shifts", "role_note"),
        ("shopping_items", "name"), ("contacts", "name"), ("announcements", "title"),
        ("candidates", "name"), ("guests", "name"), ("vehicle_usage", "purpose"),
        ("waitlist_entries", "name"),
    ]:
        try:
            conn.execute(f"DELETE FROM {table} WHERE {column} LIKE ?", (TAG + "%",))
        except Exception:
            pass
    try:
        conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    except Exception:
        pass
    # Sessions before workshops — the child row references the parent.
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
