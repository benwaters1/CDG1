"""Confirming a screen full of booking requests at once.

Nothing stops two people REQUESTING the same room for the same nights — the
booking form deliberately does not block that, because a pending request is not
a reservation. Which means the confirm step is the only thing standing between
two pending requests and two families arriving at one door. Bulk confirm is
where that would happen, because it is the one place somebody ticks a column of
checkboxes and stops reading.

Three properties carry this file:

  THE SECOND ONE MUST BE REFUSED. is_range_available is re-checked per booking
  with include_pending=False, so the first confirm succeeds and the second is
  turned away by the first — not by the fact that both were pending when the
  page was drawn. Selecting both and pressing the button once has to end with
  one confirmed booking, not two.

  A DOUBLE SUBMIT MUST NOT RE-SEND. Every confirm emails the guest. The guard
  is `AND status = 'pending'` on the UPDATE, so a stale selection or a second
  click acts on nothing. Without it, pressing confirm twice on twelve bookings
  is twenty-four emails to twelve people.

  THE COUNT MUST BE TRUE. The flash says how many were confirmed and how many
  were skipped for a clash. An owner reads that line instead of re-checking the
  list, so a number that overstates is worse than no number.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZBULK"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _free(nights=3, after=300, room_id=None):
    """A start date on which the room is genuinely free, found not assumed.

    The seeded ateliers hold EVERY room for their dates — a workshop takes over
    the whole château — so a hand-picked "far future" date lands on one often
    enough. Picking a date and hoping is how this suite first reported that
    bulk confirm was refusing everything, when the fixture was at fault.
    """
    conn = db()
    try:
        room_id = room_id or _harness.ensure_room()["id"]
        day = date.today() + timedelta(days=after)
        for _ in range(400):
            ok, _why = m.is_range_available(
                conn, room_id, day, day + timedelta(days=nights), include_pending=False)
            if ok:
                return day
            day += timedelta(days=1)
        raise AssertionError("no free date within 400 days of the start point")
    finally:
        conn.close()


def _request(ref, arrival, nights=3, room_id=None, status="pending", email=None):
    """A pending booking request, as the public form would leave it."""
    conn = db()
    room_id = room_id or _harness.ensure_room()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 900, 0, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         email or f"{TAG.lower()}.{ref.lower()}@example.invalid",
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _status(ref):
    conn = db()
    try:
        row = conn.execute("SELECT status FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-{ref}",)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("Bulk confirm")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    sent = []
    was = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]
    try:
        s.section("Confirming a selection confirms exactly that selection")
        first_day = _free(after=300)
        a = _request("A", first_day)
        b = _request("B", _free(after=(first_day - date.today()).days + 20))
        untouched = _request("C", _free(after=(first_day - date.today()).days + 60))
        sent.clear()
        r = oc.post("/admin/bookings/bulk-confirm",
                    data={"booking_ids": [str(a["id"]), str(b["id"])]},
                    follow_redirects=True)
        s.check("the two chosen are confirmed",
                _status("A") == "confirmed" and _status("B") == "confirmed",
                detail=f"A={_status('A')}, B={_status('B')}")
        s.check("the one not chosen is left pending", _status("C") == "pending",
                detail=f"C={_status('C')} — the bulk action reached past the "
                       "boxes that were ticked")
        s.check("each guest is written to once", len(sent) == 2, detail=f"{sent}")
        s.check("and the message says it confirmed two",
                "Confirmed 2" in " ".join(flashes(r)),
                detail=f"{flashes(r)[:1]} — a bare '2' also matches "
                       "'Skipped 2', which is the opposite outcome")

        s.section("Pressing it again does nothing and tells nobody")
        # Every confirm emails the guest. Without the pending guard, a second
        # click on twelve bookings is twenty-four emails to twelve people.
        sent.clear()
        r2 = oc.post("/admin/bookings/bulk-confirm",
                     data={"booking_ids": [str(a["id"]), str(b["id"])]},
                     follow_redirects=True)
        s.check("no second email goes out", not sent, detail=f"{sent}")
        s.check("they are still just confirmed",
                _status("A") == "confirmed" and _status("B") == "confirmed")
        s.check("and it does not claim to have confirmed them again",
                "0" in " ".join(flashes(r2)) or "Confirmed 0" in " ".join(flashes(r2)),
                detail=f"{flashes(r2)[:1]} — the owner is told work happened "
                       "that did not")

        s.section("Two requests for the same room and nights")
        # Nothing stops both being REQUESTED. This is the only place that
        # stands between them and two families at one door.
        _cleanup()
        room = _harness.ensure_room()
        clash_day = _free(after=400, nights=5, room_id=room["id"])
        first = _request("FIRST", clash_day, room_id=room["id"])
        second = _request("SECOND", clash_day + timedelta(days=1), room_id=room["id"])
        sent.clear()
        r3 = oc.post("/admin/bookings/bulk-confirm",
                     data={"booking_ids": [str(first["id"]), str(second["id"])]},
                     follow_redirects=True)
        confirmed = [ref for ref in ("FIRST", "SECOND") if _status(ref) == "confirmed"]
        s.check("only one of them is confirmed", len(confirmed) == 1,
                detail=f"confirmed {confirmed} — two overlapping stays were "
                       "both accepted for one room")
        s.check("the other is still pending, not lost",
                sorted([_status("FIRST"), _status("SECOND")]) == ["confirmed", "pending"],
                detail=f"FIRST={_status('FIRST')}, SECOND={_status('SECOND')}")
        s.check("only the confirmed guest is written to", len(sent) == 1,
                detail=f"{sent} — the guest who was refused was told they were "
                       "confirmed")
        s.check("and the owner is told one was skipped",
                any("skip" in f.lower() or "conflict" in f.lower() for f in flashes(r3)),
                detail=f"{flashes(r3)[:1]} — a silent skip reads as success and "
                       "the request sits there")

        s.section("A clash is reported differently from a stale tick")
        # "Skipped for a date conflict" needs looking at; a booking somebody
        # else already handled does not.
        _cleanup()
        gone = _request("GONE", _free(after=500), status="cancelled")
        sent.clear()
        r4 = oc.post("/admin/bookings/bulk-confirm",
                     data={"booking_ids": [str(gone["id"])]}, follow_redirects=True)
        s.check("a cancelled booking is not confirmed", _status("GONE") == "cancelled",
                detail="the bulk action revived a cancelled booking")
        s.check("nobody is emailed about it", not sent, detail=f"{sent}")
        s.check("and it is not reported as a date conflict",
                not any("conflict" in f.lower() for f in flashes(r4)),
                detail=f"{flashes(r4)[:1]} — an already-handled booking was "
                       "reported as a clash worth investigating")

        s.section("Rubbish in the form does not break it")
        _cleanup()
        ok = _request("OK", _free(after=600))
        sent.clear()
        r5 = oc.post("/admin/bookings/bulk-confirm",
                     data={"booking_ids": [str(ok["id"]), "999999", "not-a-number", ""]},
                     follow_redirects=True)
        s.check("it does not 500", r5.status_code < 500, detail=f"HTTP {r5.status_code}")
        s.check("the real one is still confirmed", _status("OK") == "confirmed")
        s.check("and one email went out", len(sent) == 1, detail=f"{sent}")

        s.section("An empty selection is a no-op, not an error")
        sent.clear()
        r6 = oc.post("/admin/bookings/bulk-confirm", data={}, follow_redirects=True)
        s.check("it comes back cleanly", r6.status_code < 500)
        s.check("and writes to nobody", not sent, detail=f"{sent}")

        s.section("A returning guest does not become a second guest")
        # confirm_booking_by_id finds-or-creates the standing profile on email.
        # It used to insert one per stay, so a regular accumulated a new
        # identity every visit and their history was invisible.
        _cleanup()
        same = f"{TAG.lower()}.regular@example.invalid"
        one = _request("R1", _free(after=700), email=same)
        two = _request("R2", _free(after=760), email=same)
        oc.post("/admin/bookings/bulk-confirm",
                data={"booking_ids": [str(one["id"]), str(two["id"])]},
                follow_redirects=True)
        conn = db()
        profiles = conn.execute(
            "SELECT COUNT(*) AS c FROM guests WHERE email = ? COLLATE NOCASE",
            (same,)).fetchone()["c"]
        linked = conn.execute(
            """SELECT COUNT(DISTINCT linked_guest_id) AS c FROM bookings
               WHERE reference_code LIKE ?""", (TAG + "-R%",)).fetchone()["c"]
        conn.close()
        s.check("two stays make one profile", profiles == 1, detail=f"{profiles} profiles")
        s.check("and both stays point at it", linked == 1,
                detail=f"{linked} distinct guest ids — the same person is two "
                       "people and neither has a history")

        s.section("Guards")
        _cleanup()
        theirs = _request("GUARD", _free(after=820))
        sent.clear()
        code = ec.post("/admin/bookings/bulk-confirm",
                       data={"booking_ids": [str(theirs["id"])]}).status_code
        s.check("an employee cannot bulk confirm", code in (302, 403),
                detail=f"HTTP {code}")
        s.check("and the booking really did not move", _status("GUARD") == "pending",
                detail="the status code said no and the row changed anyway")
        s.check("nobody was emailed by the attempt", not sent, detail=f"{sent}")
    finally:
        m.send_email = was
        _cleanup()
    return s
