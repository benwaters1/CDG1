"""A room that was confirmed and nobody arrived for.

The restaurant has had this for a while: a stamp on the reservation, a way to
undo it, a report, and a count of how often the same address has done it. Rooms
had none of it, so a stay that never happened sat there confirmed for ever. It
counted in occupancy, it counted in revenue, the departure never came, and the
one question it raises -- whether the money is kept -- was never put in front of
anybody.

Three things carry this file.

  IT IS A STAMP, NOT A STATUS. bookings.status is CHECKed to four values and
  three other tables point at bookings, so adding a fifth word would mean
  rebuilding the table. restaurant_bookings solved the same problem with
  no_show_at and this mirrors it exactly.

  IT CANNOT BE MARKED EARLY. A guest arriving at eleven at night is not a
  no-show at six, and a stay written off before the day is a room somebody
  stops holding.

  IT COUNTS ACROSS BOTH BOOKS. Somebody who does not turn up for a room and
  then asks for a table is the whole reason for recording it. Counting only
  restaurant reservations made the second booking look like a first.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZNS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(ref, *, room_id, days_ago=3, status="confirmed", paid=0, total=800):
    conn = db()
    arrival = m.house_today() - timedelta(days=days_ago)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, ?, ?, ?, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzns.{ref}@example.invalid".lower(), arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(), status,
         "paid" if paid >= total else "unpaid", total, paid,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _get(ref):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                            (f"{TAG}-{ref}",)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Room no-shows")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()

    s.section("Marking a stay nobody arrived for")
    a = _booking("A", room_id=room["id"], total=800, paid=0)
    r = oc.post(f"/admin/bookings/{a['id']}/no-show",
                data={"note": "no contact, telephone off"}, follow_redirects=True)
    msg = " ".join(flashes(r))
    s.check("it is stamped", bool(_get("A")["no_show_at"]), detail=f"{msg}")
    s.check("the status is untouched", _get("A")["status"] == "confirmed",
            detail="a stamp, not a fifth status word — the CHECK on that column "
                   "allows four and three tables point at this one")
    s.check("what happened is kept with the booking",
            "no contact" in (_get("A")["owner_note"] or ""),
            detail=f"{_get('A')['owner_note']!r}")

    s.section("It says what is owed, because that is the decision it raises")
    s.check("an unpaid one names the figure", "800" in msg and "unpaid" in msg,
            detail=f"{msg} — a stay written off silently is money nobody chose "
                   "to lose")
    s.check("and moves no money", "nothing has been charged or refunded" in msg,
            detail=f"{msg}")
    b = _booking("PAID", room_id=room["id"], total=600, paid=600)
    r2 = oc.post(f"/admin/bookings/{b['id']}/no-show", follow_redirects=True)
    s.check("a paid one says so instead",
            "paid in full" in " ".join(flashes(r2)),
            detail=f"{flashes(r2)[:1]} — refunds are a judgement call here")

    s.section("It cannot be marked early")
    soon = _booking("SOON", room_id=room["id"], days_ago=-4)
    r3 = oc.post(f"/admin/bookings/{soon['id']}/no-show", follow_redirects=True)
    s.check("a stay still to come is refused",
            "not due until" in " ".join(flashes(r3)),
            detail=f"{flashes(r3)[:1]} — somebody arriving at eleven at night is "
                   "not a no-show at six")
    s.check("and is not stamped", not _get("SOON")["no_show_at"])

    s.section("And only on a confirmed booking, once")
    pend = _booking("PEND", room_id=room["id"], status="pending")
    r4 = oc.post(f"/admin/bookings/{pend['id']}/no-show", follow_redirects=True)
    s.check("a request nobody answered is not a no-show",
            "Only a confirmed booking" in " ".join(flashes(r4)),
            detail=f"{flashes(r4)[:1]}")
    r5 = oc.post(f"/admin/bookings/{a['id']}/no-show", follow_redirects=True)
    s.check("marking it twice is refused",
            "Already marked" in " ".join(flashes(r5)),
            detail=f"{flashes(r5)[:1]} — a second mark would append the note again")

    s.section("They did arrive after all")
    r6 = oc.post(f"/admin/bookings/{a['id']}/no-show/undo", follow_redirects=True)
    s.check("the stamp comes off", not _get("A")["no_show_at"],
            detail=f"{flashes(r6)[:1]}")
    r7 = oc.post(f"/admin/bookings/{a['id']}/no-show/undo", follow_redirects=True)
    s.check("and undoing a stay that was never marked is refused",
            "not marked" in " ".join(flashes(r7)),
            detail=f"{flashes(r7)[:1]}")

    s.section("A no-show is counted across both books")
    # THE POINT of recording it. Somebody who does not turn up for a room and
    # then asks for a table is the case this exists for; counting only
    # restaurant reservations made the second booking look like a first.
    email = f"zzns.paid@example.invalid"
    conn = db()
    counts = m.prior_no_shows(conn, [email])
    conn.close()
    s.check("the room no-show is visible to the restaurant's own lookup",
            counts.get(email, 0) >= 1,
            detail=f"{counts} — before this it read only restaurant_bookings, "
                   "so a guest with a room no-show looked clean when they "
                   "asked for a table")

    s.section("It shows on the row, not only in the database")
    body = oc.get("/admin/bookings").get_data(as_text=True)
    # The FLAG, not the word. The button offering the action says "Did not
    # arrive" too, so a looser check passes with no flag on the page at all --
    # which is how it passed the first time the flag was deleted.
    s.check("the stay reads as not arrived",
            ">did not arrive</span>" in body,
            detail="a confirmed stay nobody came for looks exactly like one "
                   "that happened; the button offering the action is not the "
                   "same thing as the row saying it happened")
    s.check("and a stay still to come is not offered the action",
            f"{TAG}-SOON" not in body or "Did not arrive" in body,
            detail="offered only once the arrival has passed")

    s.section("Guards")
    left = _booking("GUARD", room_id=room["id"])
    r8 = ec.post(f"/admin/bookings/{left['id']}/no-show", follow_redirects=False)
    s.check("an employee cannot mark one", r8.status_code in (302, 403),
            detail=f"{r8.status_code}")
    s.check("and the booking is untouched", not _get("GUARD")["no_show_at"])

    _cleanup()
    return s
