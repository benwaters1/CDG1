"""One day's arrivals on one page, and what was given to get in.

The arrival card is per booking and the incomplete list says only what is
MISSING. Neither is the thing somebody wants at breakfast: who is coming, when,
which room, what they cannot eat, whether anybody is meeting a plane, and what
still has no answer. It was assembled by opening six pages, so in practice it
was assembled from memory.

Two things carry this file.

  IT SHOWS THIS WEEK AND NEXT, day by day (owner, 22 October 2026: "it should
  be weekly and next"). It opened on tomorrow alone, then on today and
  tomorrow (19 October); on a quiet Thursday that was two empty panels and
  nothing about the weekend. Today still comes first, to receive them, and
  tomorrow second, to get ready. A run of days with nobody coming or going is
  ONE line, or a fortnight of empty panels buries the days that matter.

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

    s.section("Today first, to receive them; tomorrow next, to get ready")
    _booking("TOM", room_id=room["id"], arrival=tomorrow,
             estimated_arrival_time="18:30", special_requests="no shellfish")
    _booking("TODAY", room_id=room["id"], arrival=m.house_today())
    body = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("tomorrow's guest is on it, to get ready for", f"{TAG} TOM" in body)
    s.check("and so is today's, to receive", f"{TAG} TODAY" in body)
    # Read from the page's own heading: the menu has a "Today" of its own.
    sheet = body[body.find("<h1>Arrivals</h1>"):]
    s.check("today first", sheet.find(f"{TAG} TODAY") < sheet.find(f"{TAG} TOM")
            and 0 < sheet.find(">Today") < sheet.find(f"{TAG} TODAY")
            < sheet.find(">Tomorrow") < sheet.find(f"{TAG} TOM"),
            detail="each under its own heading")
    s.check("a named day can still be asked for",
            f"{TAG} TODAY" in oc.get(
                "/admin/arrivals?date=" + m.house_today().isoformat()
            ).get_data(as_text=True))

    s.section("This week and next, day by day (owner, 22 October 2026)")
    # The window is the calendar's, not an offset somebody chose: from today
    # to the Sunday of next week. The bookings here are written straight to
    # the table, so no atelier hold can refuse one.
    today = m.house_today()
    last = today - timedelta(days=today.weekday()) + timedelta(days=13)
    _booking("LAST", room_id=room["id"], arrival=last, nights=1)
    _booking("BEYOND", room_id=room["id"], arrival=last + timedelta(days=1), nights=1)
    # Here before the window opens and leaving the day before it closes: on
    # the sheet as a departure only.
    _booking("GOES", room_id=room["id"], arrival=today - timedelta(days=3),
             nights=(last - today).days + 2)
    body = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("this week, then next week",
            0 < body.find("<h1>Arrivals</h1>") < body.find(">This week") < body.find(">Next week"),
            detail="the two headings the owner asked for, in that order")
    s.check("the last day of next week is on it", f"{TAG} LAST" in body,
            detail=f"{TAG} LAST arrives {last.isoformat()}, the Sunday of next week")
    s.check("and a guest leaving next week, under next week",
            body.find(">Next week") < body.find(f"{TAG} GOES"),
            detail="a room is not ready for an arrival until the departure has gone")
    s.check("the Monday after is not", f"{TAG} BEYOND" not in body,
            detail="the window ends with next week")
    s.check("and the fortnight after is one click on",
            f"date={(last + timedelta(days=1)).isoformat()}" in body)

    conn = db()
    with m.app.test_request_context("/"):
        window = m.arrivals_weeks(conn)
    conn.close()
    weeks = window["weeks"]
    s.check("from today to the Sunday of next week",
            window["start"] == today and window["end"] == last,
            detail=f"{window['start']} to {window['end']}, wanted {today} to {last}")
    s.check("as this week and next",
            [w["label"] for w in weeks] == ["This week", "Next week"],
            detail=str([w["label"] for w in weeks]))

    def covered(w):
        """Every day the window's entries stand for, in order."""
        out = []
        for wk in w["weeks"]:
            for e in wk["entries"]:
                a, z = (e["first"], e["last"]) if e["quiet"] else (e["sheet"]["day"],) * 2
                while a <= z:
                    out.append(a)
                    a += timedelta(days=1)
        return out

    wanted = [today + timedelta(days=i) for i in range((last - today).days + 1)]
    s.check("every day of it accounted for, once and in order", covered(window) == wanted,
            detail=f"{len(covered(window))} days for {len(wanted)}")
    s.check("and quiet days are never two entries side by side",
            not any(a["quiet"] and b["quiet"]
                    for wk in weeks for a, b in zip(wk["entries"], wk["entries"][1:])),
            detail="a run of days with nobody is one line")
    s.check("the departure is counted in next week's total",
            weeks[1]["leaving"] >= 1 and weeks[1]["arriving"] >= 1,
            detail=f"{weeks[1]['arriving']} arriving, {weeks[1]['leaving']} leaving")

    s.section("A run of quiet days is one line, not a panel a day")
    # A fortnight nobody has booked, ASKED of the calendar rather than counted
    # off today, starting on its first Monday.
    far = _harness.free_window(room["id"], 21, after_days=600, clear_of_ateliers=True)
    monday = far + timedelta(days=(7 - far.weekday()) % 7)
    conn = db()
    with m.app.test_request_context("/"):
        empty = m.arrivals_weeks(conn, monday)
    conn.close()
    bare = [e for wk in empty["weeks"] for e in wk["entries"]]
    s.check("a fortnight with nobody in it to try this on",
            len(bare) == 2 and all(e["quiet"] for e in bare),
            detail=f"{len(bare)} entries from {monday} — reported rather than skipped")
    # One stay in the middle of the first week, and one that began before
    # the window and ends after it, which is neither coming nor going.
    _booking("MID", room_id=room["id"], arrival=monday + timedelta(days=2), nights=2)
    _booking("ACROSS", room_id=room["id"], arrival=monday - timedelta(days=1), nights=21)
    conn = db()
    with m.app.test_request_context("/"):
        picked = m.arrivals_weeks(conn, monday)
    conn.close()

    def shape(w):
        return [[("quiet", (e["first"] - monday).days, (e["last"] - monday).days)
                 if e["quiet"] else ("day", (e["sheet"]["day"] - monday).days)
                 for e in wk["entries"]] for wk in w["weeks"]]

    s.check("a picked day starts the window, which runs to the end of the week after",
            picked["start"] == monday and picked["end"] == monday + timedelta(days=13),
            detail=f"{picked['start']} to {picked['end']}")
    s.check("arrival and departure days stand alone; the days between are runs",
            shape(picked) == [[("quiet", 0, 1), ("day", 2), ("quiet", 3, 3), ("day", 4),
                               ("quiet", 5, 6)], [("quiet", 7, 13)]],
            detail=str(shape(picked)))
    page = oc.get(f"/admin/arrivals?date={monday.isoformat()}").get_data(as_text=True)
    quiet_lines = page.count('class="arr-quiet"')
    s.check("on the page, four quiet lines for twelve quiet days", quiet_lines == 4,
            detail=f"{quiet_lines} quiet lines")
    s.check("and no empty panel for any of them",
            "Nobody is arriving or leaving on this day" not in page)
    s.check("the stay is there", f"{TAG} MID" in page)
    s.check("and one neither starting nor ending in the window is not",
            f"{TAG} ACROSS" not in page)
    s.check("weeks that are neither this nor next are named by their Monday",
            f"Week of {m.format_date_short(monday.isoformat())}" in page)
    conn = db()
    with m.app.test_request_context("/"):
        midweek = m.arrivals_weeks(conn, monday + timedelta(days=3))
    conn.close()
    s.check("a day picked mid-week still runs to the end of the week after",
            midweek["end"] == monday + timedelta(days=13)
            and midweek["weeks"][0]["first"] == monday + timedelta(days=3),
            detail=f"{midweek['start']} to {midweek['end']}")

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
