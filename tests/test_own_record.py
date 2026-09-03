"""One record that follows a guest, kept by the guest.

Dietary needs were entered per record — once for a stay, again for an atelier,
again for a table — so a coeliac writes it out four times and gets it wrong
once. The house could already write them on a profile; the guest could not,
and the guest is the one who knows.

THE HARD PART IS NOT THE FORM, IT IS THE PROMISE. templates/privacy.html said
"Dietary and medical notes: deleted once the stay is over. There is no reason
to keep them and good reason not to." That is true of a note attached to a
booking, and purge_health_notes makes it true. It would have become a lie the
moment a guest could keep the same information on their own record for years.

Two things came out of taking that seriously, and both are checked below.

FIRST, guests.dietary_notes was ALREADY outside the promise. It has existed
since the profile table did, staff have always been able to write it, and
nothing has ever cleared it — purge_health_notes clears the copies on a
restaurant booking and an atelier booking, and stops. A guest reading the
notice would have believed their dietary note was gone while it sat on a
profile indefinitely. It now falls under the same twelve-month rule as access
needs, whoever wrote it, and the notice says so.

SECOND, NOTHING MEDICAL IS ON THIS FORM. The sketch offered a medical box
"for the person leading the atelier". An atelier medical note is written for
one session, read by one instructor, and deleted when the session ends. Moving
it onto a profile turns a note for one afternoon into a standing medical
record, and no form on a website should be where somebody's health condition
is filed indefinitely. It stays asked per atelier.

Emptying a box empties it. That is the difference between a record you keep
and data somebody holds about you, and a form that can only ever add is the
second kind.
"""
from datetime import timedelta

from _harness import Suite, db, house_today

import _harness

m = _harness.m
TAG = "ZZOWN"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_sessions WHERE email LIKE ?",
                 ("%" + TAG.lower() + "%",))
    conn.commit()


def run():
    s = Suite("the guest's own record")
    today = house_today()
    now = m.datetime.now(m.timezone.utc)
    conn = db()
    _cleanup(conn)

    email = f"{TAG.lower()}.odile@example.invalid"
    conn.execute(
        """INSERT INTO guests (name, email, phone, created_at)
           VALUES (?, ?, '+33 1 23', ?)""",
        (TAG + " Odile", email, now.isoformat()))
    gid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    s.section("Reading a record that exists, and one that does not")
    s.check("it is found by address",
            m.guest_own_record(conn, email.upper()) is not None,
            detail="a guest has an email, not a guest id, and asking them to "
                   "quote one is asking them to be a record")
    s.check("an address with no profile has none",
            m.guest_own_record(conn, "nobody@example.invalid") is None)
    # A profile with no email on it, which the guest form allows: without one
    # a blank lookup matches NOTHING anyway and the guard looks redundant.
    # With one it is the difference between "no record" and a stranger's.
    conn.execute(
        """INSERT INTO guests (name, email, dietary_notes, created_at)
           VALUES (?, '', 'Nothing with nuts', ?)""",
        (TAG + " Walk-in", now.isoformat()))
    conn.commit()
    s.check("and a blank address is not a wildcard",
            m.guest_own_record(conn, "  ") is None,
            detail="there IS a profile with an empty email, so without the "
                   "guard a blank lookup returns somebody else's record")

    s.section("Writing it")
    ok = m.save_guest_own_record(
        conn, email, dietary="Coeliac, strictly",
        access="A few steps at most", arrival="16:30")
    conn.commit()
    s.check("it saves", ok)
    row = m.guest_own_record(conn, email)
    s.check("the kitchen note", row["dietary_notes"] == "Coeliac, strictly")
    s.check("what they need from the house",
            row["access_needs"] == "A few steps at most")
    s.check("and when they usually arrive", row["usual_arrival_time"] == "16:30")
    s.check("stamped, so the retention pass can see it",
            row["own_notes_updated_at"] is not None)

    s.section("Emptying it empties it")
    # Half the feature. A form that can only ever add is data somebody holds
    # about you; one you can clear is a record you keep.
    m.save_guest_own_record(conn, email, dietary="", access="", arrival="")
    conn.commit()
    row = m.guest_own_record(conn, email)
    s.check("the kitchen note goes", row["dietary_notes"] is None)
    s.check("the access note goes", row["access_needs"] is None)
    s.check("the arrival time goes", row["usual_arrival_time"] is None)
    s.check("and the stamp goes with them", row["own_notes_updated_at"] is None,
            detail="a date left on emptied fields is still a record that "
                   "somebody told us something")

    s.section("A telephone number is not emptied by leaving it blank")
    # It is not a note; it is how the house reaches somebody about a booking
    # already made, and blanking it because a box was left alone would break
    # the arrival call.
    m.save_guest_own_record(conn, email, dietary="Coeliac", phone="")
    conn.commit()
    s.check("the number stands",
            m.guest_own_record(conn, email)["phone"] == "+33 1 23",
            detail="clearing it because a box was untouched is how the "
                   "arrival telephone call stops happening")

    s.section("Saving against an address with no profile does nothing")
    s.check("it says so rather than creating one",
            m.save_guest_own_record(conn, "ghost@example.invalid",
                                    dietary="x") is False,
            detail="a profile minted from a form post is a profile with no "
                   "stay behind it")

    # -------------------------------------------------- the promise kept
    s.section("A profile dietary note was outside the promise, and is not now")
    # Nothing has ever cleared guests.dietary_notes. purge_health_notes
    # clears the copies on a restaurant booking and an atelier booking and
    # stops there, while the notice says dietary notes go once the stay is
    # over -- so a guest reading it believed theirs was gone.
    import inspect
    s.check("purge_health_notes really does not touch the profile",
            "UPDATE guests" not in inspect.getsource(m.purge_health_notes),
            detail="if it does now, this whole section is about the wrong "
                   "function and wants rewriting")
    m.save_guest_own_record(conn, email, dietary="Coeliac, strictly")
    conn.commit()
    m.purge_stale_access_needs(conn, today=today)
    conn.commit()
    s.check("with no stay at all it is cleared",
            m.guest_own_record(conn, email)["dietary_notes"] is None,
            detail="held indefinitely before this")

    # The one the guest never touched. Written by STAFF on the profile form,
    # so there is no own-notes stamp and no access note to catch it -- which
    # is every profile dietary note that existed before today.
    staff_email = f"{TAG.lower()}.written-by-staff@example.invalid"
    conn.execute(
        """INSERT INTO guests (name, email, dietary_notes, created_at)
           VALUES (?, ?, 'No shellfish at all', ?)""",
        (TAG + " Bernard", staff_email, now.isoformat()))
    conn.commit()
    before = m.guest_own_record(conn, staff_email)
    s.check("a staff-written note has no guest stamp on it",
            before["own_notes_updated_at"] is None
            and before["access_needs"] is None,
            detail="if it had one, this check would pass on the wrong reason")
    m.purge_stale_access_needs(conn, today=today)
    conn.commit()
    s.check("and it is cleared too",
            m.guest_own_record(conn, staff_email)["dietary_notes"] is None,
            detail="a guest reading the notice cannot tell who typed it and "
                   "should not have to")

    s.section("But not while they are still coming")
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, arrival_date, departure_date,
                   party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room["id"], TAG + "SOON", TAG.lower() + "-soon", TAG + " Odile",
         email, (today + timedelta(days=20)).isoformat(),
         (today + timedelta(days=22)).isoformat(), now.isoformat()))
    conn.commit()
    m.save_guest_own_record(conn, email, dietary="Coeliac, strictly")
    conn.commit()
    m.purge_stale_access_needs(conn, today=today)
    conn.commit()
    s.check("somebody with a stay ahead keeps theirs",
            m.guest_own_record(conn, email)["dietary_notes"] is not None,
            detail="forgetting it the week before they arrive is worse than "
                   "never having asked")

    s.section("Thirteen months after their last stay, it goes")
    m.purge_stale_access_needs(conn, today=today + timedelta(days=420))
    conn.commit()
    row = m.guest_own_record(conn, email)
    s.check("the kitchen note", row["dietary_notes"] is None)
    s.check("the arrival time", row["usual_arrival_time"] is None)
    s.check("and the stamp", row["own_notes_updated_at"] is None)

    s.section("And through the form a guest actually uses")
    # The helper is not the feature; the page is. A route that nobody
    # exercises is a route that works until the day somebody renames a field.
    token = TAG.lower() + "-session"
    conn.execute(
        """INSERT INTO guest_sessions (email, token, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (email, token, now.isoformat(),
         (now + timedelta(days=7)).isoformat()))
    conn.commit()
    guest = m.app.test_client()
    page = guest.get(f"/my-account/{token}").get_data(as_text=True)
    s.check("the form is on their account page",
            'name="dietary_notes"' in page and "Your details, kept once" in page)
    guest.post(f"/my-account/{token}/details",
               data={"dietary_notes": "Coeliac, and no shellfish",
                     "access_needs": "Ground floor if you have it",
                     "usual_arrival_time": "17:00"},
               follow_redirects=True)
    row = m.guest_own_record(conn, email)
    s.check("what they typed is kept",
            row["dietary_notes"] == "Coeliac, and no shellfish",
            detail=str(row["dietary_notes"]))
    s.check("and offered back on the page",
            "Coeliac, and no shellfish" in
            guest.get(f"/my-account/{token}").get_data(as_text=True),
            detail="the whole point is not writing it out again")
    guest.post(f"/my-account/{token}/details",
               data={"dietary_notes": "", "access_needs": "",
                     "usual_arrival_time": ""},
               follow_redirects=True)
    s.check("and emptying it through the form empties it",
            m.guest_own_record(conn, email)["dietary_notes"] is None)

    s.section("An expired link saves nothing")
    conn.execute("UPDATE guest_sessions SET expires_at = ? WHERE token = ?",
                 ((now - timedelta(days=1)).isoformat(), token))
    conn.commit()
    r = guest.post(f"/my-account/{token}/details",
                   data={"dietary_notes": "written by a stranger"})
    s.check("it is refused", r.status_code == 404, detail=f"HTTP {r.status_code}")
    s.check("and nothing was written",
            m.guest_own_record(conn, email)["dietary_notes"] is None,
            detail="a link that stops letting somebody READ but goes on "
                   "letting them WRITE is the worse half left open")

    s.section("The notice says all of this")
    # templates/privacy.html is a set of claims about this code. Holding a
    # dietary note on a profile for years while the notice says it goes once
    # the stay is over is the exact failure this file is about.
    notice = m.app.test_client().get("/privacy").get_data(as_text=True)
    s.check("a booking's note still goes when the booking is over",
            "deleted once that booking is over" in notice)
    s.check("and what a guest keeps is described separately",
            "What you keep on your own record" in notice,
            detail="a guest cannot tell the two apart and should not have to")
    # Within the own-record entry, not anywhere on the page. The access-needs
    # entry says the same twelve months, so a whole-page search was satisfied
    # by a different promise about different data.
    at = notice.find("What you keep on your own record")
    entry = notice[at:notice.find("</div>", at)] if at >= 0 else ""
    s.check("the entry is there to read", bool(entry.strip()))
    s.check("with the same twelve months",
            "twelve months after your last stay" in entry,
            detail="a guest reading only this line has to be told how long")
    s.check("and that they can empty it themselves",
            "change or empty it whenever you like" in entry,
            detail="the difference between a record you keep and data "
                   "somebody holds about you")
    s.check("and it says nothing medical goes there",
            "Nothing medical goes here" in notice)
    s.check("the window in the code matches the number in the notice",
            m.ACCESS_NEEDS_RETENTION_MONTHS == 12,
            detail=str(m.ACCESS_NEEDS_RETENTION_MONTHS))

    s.section("And the form does not ask for anything medical")
    import io as _io
    import os as _os
    tpl = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "templates", "_guest_profile.html"),
        encoding="utf-8").read()
    s.check("no medical field", 'name="medical_notes"' not in tpl,
            detail="an atelier medical note is written for one session and "
                   "deleted when it ends; on a profile it becomes a standing "
                   "medical record")
    s.check("and it says why not, where a guest reads it",
            "Nothing medical belongs here" in tpl)
    s.check("with a link to what is kept and for how long",
            "privacy_page" in tpl)

    s.section("It renders nothing when there is no profile to write to")
    s.check("the macro is guarded", "{% if p %}" in tpl,
            detail="a form that saves nowhere is worse than no form")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
