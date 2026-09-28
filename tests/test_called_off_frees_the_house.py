"""Calling an atelier off gives the house back.

call_off_session cancels every place, writes to everybody and stamps the
sitting cancelled_at -- and keeps the row, on purpose: why an atelier did not
run is the thing worth knowing when deciding whether to put it on again.

But the readers that close the whole chateau for an atelier read every
sitting there had ever been. The room gate, the room page's calendar and the
public date picker each had their own query, and none of them asked whether
the sitting was still on. So once the owner called an atelier off, every room
still refused its dates with "Those dates are held for a workshop", the picker
still struck the nights out, and the house could not be sold for a week with
nothing in it. Nothing errored. Every page rendered perfectly, saying no.

Three things carry this file.

  CALLED OFF THROUGH THE REAL PATH. The owner's button, not an UPDATE written
  here: a test that stamped cancelled_at itself would pass against a call-off
  that stamped something else.

  EVERY READER, NOT ONE. The gate (is_range_available), the picker
  (nights_already_taken), the room page's calendar (unavailable_nights), the
  "free again from" line, the front page's next free nights, the legal head
  count and the owner's event clash warning. They had separate queries and
  could disagree with each other; now they share one definition, and each of
  them is asked here anyway.

  A LIVE ATELIER STILL HOLDS THE HOUSE. The cheapest way to make the first
  half pass is to stop ateliers holding anything, which would sell rooms under
  a running atelier. So a second sitting that is NOT called off is asked every
  question the first one is, and must still say no.
"""
import inspect
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room, flashes, free_window
import _harness

m = _harness.m
TAG = "ZZCOH"


def _our_sessions(conn):
    return [r["id"] for r in conn.execute(
        """SELECT ws.id FROM workshop_sessions ws
             JOIN workshops w ON w.id = ws.workshop_id
            WHERE w.title LIKE ?""", (TAG + "%",)).fetchall()]


def _cleanup():
    """Everything this file writes, and everything the app writes because of it.

    The database is shared by every suite in a run, so a sitting left behind
    here closes the house for somebody else's test -- which is exactly the
    kind of red that belongs to another suite and teaches people to ignore
    failures.
    """
    conn = db()
    ids = _our_sessions(conn)
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM workshop_bookings WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM workshop_waitlist WHERE session_id IN ({marks})", ids)
        conn.execute(
            f"""DELETE FROM audit_log WHERE action = 'workshop_session_called_off'
                  AND target IN ({marks})""", [str(i) for i in ids])
        conn.execute(f"DELETE FROM workshop_sessions WHERE id IN ({marks})", ids)
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM event_holds WHERE event_id IN
                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _sitting(ref, start, end):
    conn = db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, active,
               instructor_name, created_at) VALUES (?, '', 400, 1, 'A Gilder', ?)""",
            (f"{TAG} {ref}", now))
        wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                           (f"{TAG} {ref}",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
               capacity, created_at) VALUES (?, ?, ?, 10, ?)""",
            (wid, start.isoformat(), end.isoformat(), now))
        conn.commit()
        return conn.execute("SELECT * FROM workshop_sessions WHERE workshop_id = ?",
                            (wid,)).fetchone()
    finally:
        conn.close()


def _place(session_id, ref, status="confirmed"):
    conn = db()
    try:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
               guest_name, guest_email, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, 2, ?, 800, ?)""",
            (session_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
             f"zzcoh.{ref}@example.invalid".lower(), status,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()


def _event(ref, day):
    conn = db()
    try:
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
               contact_name, contact_email, preferred_date, status, created_at)
               VALUES (?, ?, 'wedding', ?, 'zzcoh.event@example.invalid', ?, 'quoted', ?)""",
            (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", day.isoformat(),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                            (f"{TAG}-{ref}",)).fetchone()["id"]
    finally:
        conn.close()


def _sess(session_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM workshop_sessions WHERE id = ?",
                            (session_id,)).fetchone()
    finally:
        conn.close()


def _status(ref):
    conn = db()
    try:
        return conn.execute("SELECT status FROM workshop_bookings WHERE reference_code = ?",
                            (f"{TAG}-{ref}",)).fetchone()["status"]
    finally:
        conn.close()


def _ask(fn, *args, **kwargs):
    """One question, in a request of its own.

    The availability picture is held on `g` for the life of a request, so
    asking before and after the call-off inside one request would answer the
    second question from the first one's picture and prove nothing.
    """
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return fn(conn, *args, **kwargs)
    finally:
        conn.close()


def _days(first, last):
    """ISO dates from first to last, both included."""
    return [(first + timedelta(days=i)).isoformat()
            for i in range((last - first).days + 1)]


def _quiet_window(room_id, span, after_days):
    """A run of `span` nights the room is free on AND the picker shows open.

    free_window asks the room gate, which is one room. The picker strikes out
    a night for the whole house (every room booked, or any room blocked), so a
    window the gate calls free can still be struck on the picker for a reason
    that has nothing to do with ateliers -- and a check that says "the
    atelier's nights are open again" needs nights nothing else is closing.
    """
    taken = set(_ask(m.nights_already_taken))
    horizon = m.house_today() + timedelta(days=m.PUBLIC_CALENDAR_DAYS)
    while True:
        day = free_window(room_id, span, after_days=after_days, clear_of_ateliers=True)
        if day + timedelta(days=span) > horizon:
            raise AssertionError("no quiet %d-night window inside the picker's horizon" % span)
        if not set(_days(day, day + timedelta(days=span - 1))) & taken:
            return day
        after_days = (day - m.house_today()).days + 1


def _next_free_from(room_id, first_night):
    """Where next_free_nights starts this room's first opening, asked as if
    today were NEXT_FREE_LEAD_DAYS before first_night."""
    today = first_night - timedelta(days=m.NEXT_FREE_LEAD_DAYS)
    found = _ask(m.next_free_nights, limit=10000, today=today)
    ours = [f for f in found if f["room_id"] == room_id]
    return ours[0]["date_iso"] if ours else None


def run():
    s = Suite("Calling an atelier off gives the house back")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    sent = []
    was = (m.send_email, m.send_workshop_email, m.send_event_email)
    m.send_email = lambda to, subj, body, **k: (sent.append((to, subj)), True)[1]
    m.send_workshop_email = lambda conn, b, key, ctx, **k: sent.append(
        (b["guest_email"], key)) or True
    m.send_event_email = lambda conn, inquiry, key, ctx, **k: True

    try:
        room = ensure_room()
        conn = db()
        need = max(int(conn.execute("SELECT min_nights FROM rooms WHERE id = ?",
                                    (room["id"],)).fetchone()["min_nights"] or 1), 1)
        conn.close()
        # Two nights of margin either side of a four-day sitting, and enough
        # after it for the room's own minimum stay to fit where the atelier was.
        span = max(10, need + 6)
        w_off = _quiet_window(room["id"], span, 20)
        w_live = _quiet_window(room["id"], span,
                               (w_off - m.house_today()).days + span + 2)
        off_start, off_end = w_off + timedelta(days=2), w_off + timedelta(days=5)
        live_start, live_end = w_live + timedelta(days=2), w_live + timedelta(days=5)
        off_days = _days(off_start, off_end)
        live_days = _days(live_start, live_end)

        # What the room and the house look like with no atelier at all. The
        # call-off has to put things back to exactly this.
        base_nights_off = _ask(m.unavailable_nights, room["id"], w_off,
                               w_off + timedelta(days=span))
        base_nights_live = _ask(m.unavailable_nights, room["id"], w_live,
                                w_live + timedelta(days=span))
        base_peak_off = _ask(m.peak_guests_in_house, off_start, off_end + timedelta(days=1))
        base_peak_live = _ask(m.peak_guests_in_house, live_start, live_end + timedelta(days=1))

        off = _sitting("CALLED", off_start, off_end)
        live = _sitting("LIVE", live_start, live_end)
        _place(off["id"], "OFFA")
        _place(live["id"], "LIVEA")

        s.section("Before anything is called off, both ateliers hold the house")
        # The precondition. If these fail, the checks after the call-off are
        # asking questions that could never have said no.
        ok, why = _ask(m.is_range_available, room["id"], off_start, off_end + timedelta(days=1))
        s.check("the room gate refuses the atelier's dates", not ok, detail=f"{ok} {why!r}")
        s.check("and says which atelier", f"{TAG} CALLED" in (why or ""), detail=f"{why!r}")
        ok, why = _ask(m.is_range_available, room["id"], off_end, off_end + timedelta(days=2))
        s.check("including an arrival on its last day", not ok,
                detail=f"{ok} {why!r} — the atelier's guests are still in the "
                       "house that morning")
        taken = set(_ask(m.nights_already_taken))
        s.check("the picker strikes its nights out", set(off_days) <= taken,
                detail=f"missing {sorted(set(off_days) - taken)}")
        nights = _ask(m.unavailable_nights, room["id"], w_off, w_off + timedelta(days=span))
        s.check("the room page's calendar greys them out",
                all(f"{TAG} CALLED" in (nights.get(d) or "") for d in off_days),
                detail=f"{ {d: nights.get(d) for d in off_days} }")
        held = _ask(m.atelier_holding_the_house, off_start, off_start + timedelta(days=1))
        s.check("and names it as what is holding the house",
                held == {"start_date": off_start.isoformat(), "end_date": off_end.isoformat()},
                detail=f"{held}")
        s.check("next free nights look past it",
                _next_free_from(room["id"], off_start) != off_start.isoformat(),
                detail=f"{_next_free_from(room['id'], off_start)}")
        peak = _ask(m.peak_guests_in_house, off_start, off_end + timedelta(days=1))
        s.check("and its two guests count towards the legal ceiling",
                peak == base_peak_off + 2, detail=f"{peak} against {base_peak_off} without it")

        s.section("Calling it off, through the owner's button")
        sent.clear()
        said = " ".join(flashes(oc.post(f"/admin/workshops/session/{off['id']}/call-off",
                                        data={"reason": "Not enough takers."},
                                        follow_redirects=True)))
        s.check("the sitting is marked off", _sess(off["id"])["cancelled_at"] is not None,
                detail=said)
        s.check("and kept, not deleted", _sess(off["id"]) is not None)
        s.check("its place is cancelled", _status("OFFA") == "cancelled",
                detail=_status("OFFA"))
        s.check("the other atelier is untouched", _sess(live["id"])["cancelled_at"] is None
                and _status("LIVEA") == "confirmed")

        s.section("A called-off atelier holds nothing")
        ok, why = _ask(m.is_range_available, room["id"], off_start, off_end + timedelta(days=1))
        s.check("the room can be sold for the whole run", ok and why is None,
                detail=f"{ok} {why!r} — this is the booking form's own gate, so a "
                       "refusal here is a guest turned away from an empty house")
        ok, why = _ask(m.is_range_available, room["id"], off_end, off_end + timedelta(days=2))
        s.check("including an arrival on what was its last day", ok, detail=f"{why!r}")
        taken = set(_ask(m.nights_already_taken))
        s.check("the picker shows its nights open again", not set(off_days) & taken,
                detail=f"still struck: {sorted(set(off_days) & taken)} — a struck "
                       "night reads to a guest as sold")
        nights = _ask(m.unavailable_nights, room["id"], w_off, w_off + timedelta(days=span))
        s.check("the room page's calendar is back to how it was", nights == base_nights_off,
                detail=f"{nights} against {base_nights_off}")
        held = _ask(m.atelier_holding_the_house, off_start, off_start + timedelta(days=1))
        s.check("nothing is named as holding the house", held is None, detail=f"{held}")
        s.check("next free nights offer the dates it had",
                _next_free_from(room["id"], off_start) == off_start.isoformat(),
                detail=f"{_next_free_from(room['id'], off_start)} against {off_start}")
        peak = _ask(m.peak_guests_in_house, off_start, off_end + timedelta(days=1))
        s.check("and nobody from it is counted in the house", peak == base_peak_off,
                detail=f"{peak} against {base_peak_off}")
        # However it got there. A place on a sitting that is not running puts
        # nobody in the house, and the legal ceiling is what stops the next
        # guest booking -- so counting one would turn somebody away for a
        # person who is not coming.
        _place(off["id"], "LATE", status="pending")
        peak = _ask(m.peak_guests_in_house, off_start, off_end + timedelta(days=1))
        s.check("not even a place that turns up on it afterwards", peak == base_peak_off,
                detail=f"{peak} against {base_peak_off}")

        s.section("A live atelier still holds the house")
        ok, why = _ask(m.is_range_available, room["id"], live_start, live_start + timedelta(days=2))
        s.check("the room gate still refuses its dates", not ok, detail=f"{ok} {why!r}")
        s.check("naming it", f"{TAG} LIVE" in (why or ""), detail=f"{why!r}")
        taken = set(_ask(m.nights_already_taken))
        s.check("the picker still strikes its nights", set(live_days) <= taken,
                detail=f"missing {sorted(set(live_days) - taken)}")
        nights = _ask(m.unavailable_nights, room["id"], w_live, w_live + timedelta(days=span))
        s.check("the room page still greys them out",
                all(f"{TAG} LIVE" in (nights.get(d) or "") for d in live_days)
                and all(nights.get(d) == base_nights_live.get(d)
                        for d in nights if d not in live_days),
                detail=f"{nights}")
        held = _ask(m.atelier_holding_the_house, live_start, live_start + timedelta(days=1))
        s.check("and it is still named as holding the house",
                held == {"start_date": live_start.isoformat(), "end_date": live_end.isoformat()},
                detail=f"{held}")
        peak = _ask(m.peak_guests_in_house, live_start, live_end + timedelta(days=1))
        s.check("with its guests still counted", peak == base_peak_live + 2,
                detail=f"{peak} against {base_peak_live} without it")

        s.section("The owner's clash warning agrees")
        # Confirming an event warns if an atelier already sits on the date. A
        # warning about an atelier that is not running sends the owner off to
        # untangle a clash that does not exist.
        said = " ".join(flashes(oc.post(
            f"/admin/events/{_event('EVOFF', off_start + timedelta(days=1))}/update",
            data={"status": "confirmed"}, follow_redirects=True)))
        s.check("no warning about the called-off atelier", "workshop session" not in said,
                detail=said)
        said = " ".join(flashes(oc.post(
            f"/admin/events/{_event('EVLIVE', live_start + timedelta(days=1))}/update",
            data={"status": "confirmed"}, follow_redirects=True)))
        s.check("but still one about the live atelier", "1 workshop session" in said,
                detail=said)

        s.section("One definition, asked by every reader")
        # Three readers had three queries and all three forgot the same thing.
        # A fourth written the same way would forget it again, so the readers
        # are held to asking the shared one -- directly, or through the
        # picture, which is itself held to asking it.
        src = inspect.getsource(m._availability_picture)
        s.check("the gate's picture asks sessions_holding_the_house",
                "sessions_holding_the_house(" in src and "FROM workshop_sessions" not in src,
                detail="reading workshop_sessions directly is how the called-off "
                       "filter went missing in the first place")
        for fn in (m.unavailable_nights, m.nights_already_taken):
            src = inspect.getsource(fn)
            s.check(f"{fn.__name__} asks it too, or the picture",
                    ("sessions_holding_the_house(" in src or "_availability_picture(" in src)
                    and "FROM workshop_sessions" not in src,
                    detail="reading workshop_sessions directly is how the called-off "
                           "filter went missing in the first place")
    finally:
        m.send_email, m.send_workshop_email, m.send_event_email = was
        _cleanup()
    return s
