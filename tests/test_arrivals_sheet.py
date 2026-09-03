"""One day's arrivals on one page, and what was given to get in.

The arrival card is per booking and the incomplete list says only what is
MISSING. Neither is the thing somebody wants at breakfast: who is coming, when,
which room, what they cannot eat, whether anybody is meeting a plane, and what
still has no answer. It was assembled by opening six pages, so in practice it
was assembled from memory.

Two things carry this file.

  IT DEFAULTS TO TOMORROW. By the time today's arrivals are arriving it is too
  late to act on anything the sheet would have told you. A page that opens on
  today is a page that reports rather than prepares.

  A DOOR CODE IS CLEARED WHEN THE STAY ENDS. Late arrivals in the valley are
  normal and the code was living in whichever telephone sent it. Recording it
  means the next person on can answer at eleven at night — but a code kept
  after the guest has gone is not a record, it is a way in. The ROW stays,
  because who was given what and whether a key came back is worth having; the
  value is blanked.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZAR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_access_codes WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(ref, *, room_id, arrival, nights=2, **extra):
    conn = db()
    cols = dict(room_id=room_id, reference_code=f"{TAG}-{ref}",
                manage_token=f"tok{TAG}{ref}".lower(), guest_name=f"{TAG} {ref}",
                guest_email=f"zzar.{ref}@example.invalid".lower(), guest_phone="",
                arrival_date=arrival.isoformat(),
                departure_date=(arrival + timedelta(days=nights)).isoformat(),
                party_size=2, status="confirmed", payment_status="unpaid",
                total_price=400, amount_paid=0,
                created_at=datetime.now(timezone.utc).isoformat())
    cols.update(extra)
    conn.execute("INSERT INTO bookings (%s) VALUES (%s)"
                 % (", ".join(cols), ", ".join("?" * len(cols))), list(cols.values()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("The arrivals sheet")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    tomorrow = m.house_today() + timedelta(days=1)

    s.section("It opens on tomorrow, not today")
    _booking("TOM", room_id=room["id"], arrival=tomorrow,
             estimated_arrival_time="18:30", special_requests="no shellfish")
    _booking("TODAY", room_id=room["id"], arrival=m.house_today())
    body = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("tomorrow's guest is on it", f"{TAG} TOM" in body,
            detail="a page that opens on today reports rather than prepares")
    s.check("and today's is not", f"{TAG} TODAY" not in body,
            detail="by the time today's arrivals are arriving it is too late "
                   "to act on anything this would have said")
    s.check("a named day can still be asked for",
            f"{TAG} TODAY" in oc.get(
                "/admin/arrivals?date=" + m.house_today().isoformat()
            ).get_data(as_text=True))

    s.section("Everything about the arrival is on the row")
    s.check("the room", room["name"] in body)
    s.check("when they said they would come", "18:30" in body,
            detail="the one question that decides whether somebody waits up")
    s.check("and what they cannot eat", "no shellfish" in body)

    s.section("What is still unanswered is said here, not at the door")
    # No telephone and no arrival time, which are the two questions somebody
    # would otherwise ask on the doorstep.
    _booking("GAPS", room_id=room["id"], arrival=tomorrow, guest_phone="")
    body = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("a booking with no telephone says so", "no telephone" in body,
            detail="the questions somebody would otherwise ask at the door")
    s.check("and one with no arrival time too", "no arrival time" in body)

    s.section("The profile answers what the booking does not")
    conn = db()
    conn.execute(
        """INSERT INTO guests (name, email, usual_arrival_time, dietary_notes,
           access_needs, created_at) VALUES (?, ?, '16:00', 'coeliac',
           'cannot manage stairs', ?)""",
        (f"{TAG} Known", "zzar.known@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    _booking("KNOWN", room_id=room["id"], arrival=tomorrow,
             guest_email="zzar.known@example.invalid")
    body = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("their usual arrival time is used", "16:00" in body,
            detail="a guest who always arrives at six should not be asked "
                   "every time")
    s.check("what they cannot eat comes with them", "coeliac" in body)
    # THE ONE THAT MATTERS in a house with eighteenth-century stairs: the
    # ground-floor decision is made before they arrive or not at all.
    s.check("and what they need from the house is on the sheet",
            "cannot manage stairs" in body,
            detail="it was on the profile and nothing carried it to the "
                   "morning somebody had to act on it")

    s.section("Recording what was given to get in")
    b = _booking("CODE", room_id=room["id"], arrival=tomorrow)
    oc.post(f"/admin/bookings/{b['id']}/access-code",
            data={"kind": "code", "value": "4417", "issued_to": "their son"},
            follow_redirects=True)
    body = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("it is on the sheet", "4417" in body,
            detail="it was living in whichever telephone sent it")
    s.check("with who it went to", "their son" in body)
    r = oc.post(f"/admin/bookings/{b['id']}/access-code",
                data={"kind": "code", "value": ""}, follow_redirects=True)
    s.check("and nothing is recorded from an empty box",
            "Nothing to record" in " ".join(flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("A code is cleared when the stay is over")
    old = _booking("OLD", room_id=room["id"],
                   arrival=m.house_today() - timedelta(days=40), nights=2)
    oc.post(f"/admin/bookings/{old['id']}/access-code",
            data={"kind": "code", "value": "9911"}, follow_redirects=True)
    conn = db()
    m.purge_spent_access_codes(conn)
    row = conn.execute(
        "SELECT * FROM booking_access_codes WHERE booking_id = ?", (old["id"],)).fetchone()
    conn.close()
    s.check("the digits are gone", (row["value"] or "") == "",
            detail=f"{row['value']!r} — a code kept after the guest has gone is "
                   "not a record, it is a way in")
    s.check("but the row stays", row is not None and row["issued_at"],
            detail="who was given something and whether it came back is worth "
                   "keeping; the digits are not")
    # And a stay still running keeps its code, or nobody can let the guest in.
    conn = db()
    still = conn.execute(
        """SELECT value FROM booking_access_codes WHERE booking_id = ?""",
        (b["id"],)).fetchone()
    conn.close()
    s.check("a stay still to come keeps its code", still["value"] == "4417",
            detail="clearing on a schedule rather than on the stay would lock "
                   "out the guest it was issued for")

    s.section("And a key that came back is marked once")
    # The other half of the row. Issuing is covered above; nothing exercised
    # the return, which is the half that says whether the château still has a
    # key out on a stay that ended a fortnight ago.
    conn = db()
    code_row = conn.execute(
        "SELECT id, returned_at FROM booking_access_codes WHERE booking_id = ?",
        (b["id"],)).fetchone()
    conn.close()
    s.check("it is out to begin with", code_row["returned_at"] is None)

    oc.post(f"/admin/access-code/{code_row['id']}/back", follow_redirects=True)
    conn = db()
    back = conn.execute(
        "SELECT returned_at FROM booking_access_codes WHERE id = ?",
        (code_row["id"],)).fetchone()["returned_at"]
    conn.close()
    s.check("marking it back is recorded", bool(back))

    r = oc.post(f"/admin/access-code/{code_row['id']}/back",
                follow_redirects=True)
    s.check("and pressing it again says so",
            "Already marked as back" in " ".join(flashes(r)),
            detail=f"{flashes(r)[:1]}")
    conn = db()
    again = conn.execute(
        "SELECT returned_at FROM booking_access_codes WHERE id = ?",
        (code_row["id"],)).fetchone()["returned_at"]
    conn.close()
    s.check("without moving when it came back", again == back,
            detail="the guard is `AND returned_at IS NULL`, and without it a "
                   "second press rewrites the one fact the row is for")

    s.section("Guards")
    s.check("an employee can read the sheet",
            ec.get("/admin/arrivals").status_code == 200,
            detail="whoever is on the door needs it more than the owner does")
    # By EFFECT, not by status. A refusal redirects to the login page and a
    # success redirects back to the sheet, and both are a 302 — so the status
    # on its own accepts the thing it is meant to refuse.
    ec.post(f"/admin/bookings/{b['id']}/access-code", data={"value": "0000"})
    conn = db()
    sneaked = conn.execute(
        "SELECT COUNT(*) AS c FROM booking_access_codes WHERE value = '0000'"
    ).fetchone()["c"]
    conn.close()
    s.check("but cannot record a code", sneaked == 0,
            detail="an employee reads the sheet and does not decide who gets "
                   "a way in")

    # Same again for marking one back, and checked the same way: refusing and
    # succeeding both redirect, so what settles it is the row.
    conn = db()
    fresh = conn.execute(
        "SELECT id FROM booking_access_codes WHERE booking_id = ? "
        "AND returned_at IS NULL", (old["id"],)).fetchone()
    conn.close()
    if fresh:
        ec.post(f"/admin/access-code/{fresh['id']}/back")
        conn = db()
        moved = conn.execute(
            "SELECT returned_at FROM booking_access_codes WHERE id = ?",
            (fresh["id"],)).fetchone()["returned_at"]
        conn.close()
        s.check("nor mark a key as back", moved is None,
                detail="whether a key is still out is the owner's record of "
                       "what the château has lent")
    else:
        s.check("a code is out to try this on", False,
                detail="reported rather than skipped")

    _cleanup()
    return s
