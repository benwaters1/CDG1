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
import os
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZAR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_access_codes WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = 'guest_request' AND title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM room_blocks WHERE reason = ?", (TAG,))
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


def _days(page):
    """The days: below the calendar above them, and above the list of what
    is booked after the window, which both name guests too."""
    at = page.find('class="arr-week"')
    end = page.find('id="later"')
    return page[at:end if end > at else len(page)] if at >= 0 else ""


def _later(page):
    """The list of what is booked after the window."""
    at = page.find('id="later"')
    return page[at:] if at >= 0 else ""


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
    # Read from the days, below the rooms-by-night calendar (26 October
    # 2026), which names every guest first; and below the menu, which has a
    # "Today" of its own.
    sheet = _days(body)
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
            0 < _days(body).find(">Next week") < _days(body).find(f"{TAG} GOES"),
            detail="a room is not ready for an arrival until the departure has gone")
    s.check("the Monday after is not one of the days", f"{TAG} BEYOND" not in _days(body),
            detail="the window ends with next week")
    s.check("but it is listed as booked further ahead",
            f"{TAG} BEYOND" in _later(body) and f"{TAG} LAST" not in _later(body),
            detail="printed 'from tomorrow onwards', a stay after the fortnight was not "
                   "on the paper at all (26 October 2026)")
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
    s.check("and one neither starting nor ending in the window is not on any day",
            f"{TAG} ACROSS" not in _days(page),
            detail="it is in the calendar above the days, asleep in its room")
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
    s.check("and one with no arrival time says so in words", "Arrival time not given" in body,
            detail="a blank reads as no arrival, and a 15:00 filled in reads as theirs")

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
    s.check("said as their usual, not as this stay's",
            "Usually arrives about 16:00" in body and "from their past stays" in body,
            detail="on paper a time read as promised is a time somebody waits up for")
    s.check("what they cannot eat comes with them", "coeliac" in body)
    # THE ONE THAT MATTERS in a house with eighteenth-century stairs: the
    # ground-floor decision is made before they arrive or not at all.
    s.check("and what they need from the house is on the sheet",
            "cannot manage stairs" in body,
            detail="it was on the profile and nothing carried it to the "
                   "morning somebody had to act on it")

    s.section("A clash in one room is drawn as one, not as either guest")
    # ACROSS and MID were written straight to the table into the same room
    # on the same nights. The paper is where somebody would otherwise find it.
    conn = db()
    with m.app.test_request_context("/"):
        cal = m.arrivals_calendar(conn, monday, monday + timedelta(days=13))
    conn.close()
    mine = next((r for r in cal["rows"] if r["room"] == room["name"]), None)
    shape_a = [(c["kind"], c["first"], c["span"]) for c in (mine or {}).get("cells", [])]
    s.check("the two nights both are in it are one clash",
            shape_a == [("stay", 0, 2), ("clash", 2, 2), ("stay", 4, 10)],
            detail=str(shape_a))
    s.check("and the stay already here when the window opens says so",
            mine and mine["cells"][0]["from_before"] and mine["cells"][-1]["runs_on"],
            detail="‹ arrived before, › stays on after")

    s.section("On paper: the rooms night by night (owner, 26 October 2026)")
    # "print out the bookings from tomorrow onwards ... the extras, the
    # requested check in times, the details, a calendar". A fortnight of its
    # own, asked of the calendar, so nothing else of this suite's is in it.
    w2 = _harness.free_window(room["id"], 21, after_days=700, clear_of_ateliers=True)
    w2 = w2 + timedelta(days=(7 - w2.weekday()) % 7)
    now = datetime.now(timezone.utc).isoformat()
    stayer = _booking("STAYER", room_id=room["id"], arrival=w2 - timedelta(days=1), nights=3,
                      party_size=1)
    rich = _booking("RICH", room_id=room["id"], arrival=w2 + timedelta(days=2), nights=3,
                    guest_phone="+33 6 12 34 56 78", estimated_arrival_time="17:45",
                    special_requests="a cot by the window", dietary_notes="no pork",
                    total_price=900)
    _booking("WAITS", room_id=room["id"], arrival=w2 + timedelta(days=5), nights=2,
             status="pending")
    conn = db()
    for bid, name, status, when, notes in (
            (rich["id"], f"{TAG} Champagne", "requested", w2 + timedelta(days=3), "in the room by six"),
            (rich["id"], f"{TAG} Picnic", "confirmed", None, ""),
            (rich["id"], f"{TAG} Withdrawn", "cancelled", w2 + timedelta(days=3), ""),
            (stayer["id"], f"{TAG} Late checkout", "confirmed", w2 + timedelta(days=1), "till two")):
        conn.execute(
            """INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity,
               notes, status, scheduled_for, created_at) VALUES ('room', ?, ?, 40, 1, ?, ?, ?, ?)""",
            (bid, name, notes or None, status, when.isoformat() if when else None, now))
    # Two boards from a catalogue line that needs three days' notice: one
    # arranged, one not -- the Extras page's own rule, read by the sheet.
    board = conn.execute(
        "INSERT INTO extras (name, price, active, sort_order, lead_time_days) VALUES (?, 80, 0, 999, 3)",
        (f"{TAG} Board",)).lastrowid
    for name, arranged in ((f"{TAG} Board unarranged", None), (f"{TAG} Board arranged", now)):
        conn.execute(
            """INSERT INTO booking_extras (category, booking_id, extra_id, name, unit_price, quantity,
               status, scheduled_for, created_at, arranged_at)
               VALUES ('room', ?, ?, ?, 80, 1, 'confirmed', ?, ?, ?)""",
            (rich["id"], board, name, (w2 + timedelta(days=2)).isoformat(), now, arranged))
    # What the guest wrote from their booking page -- a task on the stay --
    # one still to do and one done; and a dinner at La Table for each of them.
    for words, status in ((f"{TAG} a note for Jane's anniversary on arrival", "open"),
                          (f"{TAG} towels for the pool", "done")):
        conn.execute(
            """INSERT INTO tasks (title, notes, priority, due_date, status, created_at, origin,
               booking_id) VALUES (?, ?, 'normal', ?, ?, ?, 'guest_request', ?)""",
            (words[:120], words, (w2 + timedelta(days=2)).isoformat(), status, now, rich["id"]))
    for bid, ref, day_, status in ((rich["id"], "RD1", 3, "pending"), (stayer["id"], "RD2", 1, "confirmed")):
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
               party_size, dinner_date, dietary_notes, status, booking_id, created_at)
               VALUES (?, ?, ?, 'zzar.dinner@example.invalid', 2, ?, ?, ?, ?, ?)""",
            (f"{TAG}-{ref}", f"tok{TAG}{ref}r".lower(), f"{TAG} Diner {ref}",
             (w2 + timedelta(days=day_)).isoformat(), "no pork at dinner" if ref == "RD1" else None,
             status, bid, now))
    # The owner's closure over RICH's nights, as the house closes a room over a
    # Booking.com stay so the site cannot sell it twice: holding the room for
    # the guest, not a second booking. And one over two empty nights.
    for a_, z_ in ((2, 5), (12, 14)):
        conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (room["id"], (w2 + timedelta(days=a_)).isoformat(),
                      (w2 + timedelta(days=z_)).isoformat(), TAG, now))
    # A Booking.com guest who told us their time and number at online
    # check-in, in no room yet; and an atelier with one place roomed and one not.
    conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
           arrival_date, departure_date, room_id, guests, first_seen_at, updated_at,
           estimated_arrival_time, guest_phone, special_requests)
           VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, NULL, NULL, ?, ?, '19:30',
                   '+44 7700 900123', 'late check-in please')""",
        (f"{TAG}BK1", f"{TAG} Channel", (w2 + timedelta(days=4)).isoformat(),
         (w2 + timedelta(days=6)).isoformat(), now, now))
    workshop = conn.execute("SELECT id, title FROM workshops ORDER BY id LIMIT 1").fetchone()
    session_id = None
    if workshop:
        session_id = conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
               notes, created_at) VALUES (?, ?, ?, 12, ?, ?)""",
            (workshop["id"], (w2 + timedelta(days=8)).isoformat(),
             (w2 + timedelta(days=11)).isoformat(), TAG, now)).lastrowid
        for ref, party, room_id, eta in (("WA", 2, room["id"], "15:00"), ("WB", 1, None, None)):
            conn.execute(
                """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                   guest_name, guest_email, party_size, status, created_at,
                   assigned_room_id, estimated_arrival_time)
                   VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?)""",
                (session_id, f"{TAG}-{ref}", f"tok{TAG}{ref}w".lower(), f"{TAG} Place {ref}",
                 f"zzar.{ref}@example.invalid".lower(), party, now, room_id, eta))
    conn.commit()
    with m.app.test_request_context("/"):
        cal = m.arrivals_calendar(conn, w2, w2 + timedelta(days=13))
    conn.close()
    s.check("an atelier to try this on", bool(workshop), detail="reported rather than skipped")
    mine = next((r for r in cal["rows"] if r["room"] == room["name"]), None)
    shape = [(c["kind"], c["first"], c["span"]) for c in (mine or {}).get("cells", [])]
    s.check("a room's fortnight: here already, a stay, one to confirm, a free night, an atelier",
            shape == [("stay", 0, 2), ("stay", 2, 3), ("pending", 5, 2), ("free", 7, 1),
                      ("atelier", 8, 3), ("free", 11, 1), ("closed", 12, 2)], detail=str(shape))
    s.check("a closure under a guest is the room held for them, not a clash",
            ("stay", 2, 3) in shape and "clash" not in [k for k, _f, _n in shape],
            detail="the owner closes a room over a Booking.com stay so the site cannot sell it")
    s.check("each stay a cell across its nights, under its guest's name",
            mine and mine["cells"][1]["label"] == f"{TAG} RICH"
            and mine["cells"][0]["from_before"] and not mine["cells"][1]["from_before"],
            detail=str(mine and [c["label"] for c in mine["cells"]]))
    unmatched = next((r for r in cal["rows"] if r["room"] == "Booking.com, room not matched"), None)
    s.check("a Booking.com stay with no room yet has a row of its own",
            unmatched and any(c["kind"] == "channel" and c["first"] == 4 and c["span"] == 2
                              for c in unmatched["cells"]),
            detail=str(unmatched and [(c["kind"], c["first"], c["span"]) for c in unmatched["cells"]]))
    band = [(c["span"], [a["title"] for a in c["on"]]) for c in cal["ateliers"]]
    s.check("the atelier is a band across the nights it holds",
            workshop and any(span == 3 and workshop["title"] in t for span, t in band),
            detail=str(band))
    # THE TOTALS ARE THE GRID'S. Counted again here from the cells, for every
    # night, so a total that drifts from the rows above it is caught whatever
    # else is booked in the house that fortnight.
    drift = []
    for i, t in enumerate(cal["totals"]):
        cells = [c for r in cal["rows"] for c in r["cells"]
                 if c["first"] <= i < c["first"] + c["span"] and c["kind"] not in ("free", "closed")]
        if t["rooms"] != len(cells):
            drift.append(f"night {i}: {t['rooms']} rooms for {len(cells)} cells")
        if t["guests"] < sum(c["party"] for c in cells):
            drift.append(f"night {i}: {t['guests']} guests under {sum(c['party'] for c in cells)}")
    s.check("rooms let each night is the cells in that column", not drift, detail="; ".join(drift[:3]))
    s.check("and the guests asleep count the atelier place with no room",
            cal["totals"][8]["guests"] - sum(c["party"] for r in cal["rows"] for c in r["cells"]
                                             if c["first"] <= 8 < c["first"] + c["span"]
                                             and c["kind"] not in ("free", "closed")) == 1,
            detail=f"{cal['totals'][8]}")
    s.check("and a Booking.com stay that did not say how many is marked, not counted as none",
            cal["totals"][4]["unknown"] and not cal["totals"][3]["unknown"],
            detail=str([t["unknown"] for t in cal["totals"][:7]]))
    s.check("a named Booking.com guest goes by surname in a narrow cell, like everyone else",
            unmatched and any(c["short"] == "Channel" for c in unmatched["cells"] if c["kind"] == "channel"),
            detail=str(unmatched and [c["short"] for c in unmatched["cells"]]))

    s.section("On paper: every arrival's extras, time, telephone and asks")
    page = oc.get(f"/admin/arrivals?date={w2.isoformat()}").get_data(as_text=True)
    days = _days(page)
    text = _harness.visible_text(days)
    s.check("the calendar is on the page, in a box that scrolls",
            'class="data-table arr-cal__grid"' in page
            and page.find('class="table-wrap"') < page.find("arr-cal__grid"))
    s.check("the arrival time in words, not a bare figure", "Arriving about 17:45 (they told us)" in text,
            detail="on paper '17:45' beside a room could be anything")
    s.check("the telephone to ring", "+33 6 12 34 56 78" in text)
    s.check("the extras, each with its day and what the guest wrote",
            f"{TAG} Champagne" in text and "in the room by six" in text
            and f"{TAG} Picnic" in text,
            detail="they were on the Extras page and not on the paper the door works from")
    champagne = text[text.find(f"{TAG} Champagne"):][:120]
    s.check("one still to confirm says so", "to confirm" in champagne.lower(), detail=champagne)
    s.check("and one withdrawn is not there", f"{TAG} Withdrawn" not in page)
    eat = text[text.find("Cannot eat"):text.find("They asked")]
    s.check("what they cannot eat under Cannot eat, and nothing else",
            "no pork" in eat and "a cot by the window" not in eat, detail=eat[:120])
    s.check("what they asked for under its own heading",
            "a cot by the window" in text[text.find("They asked"):][:80])
    s.check("what is still owed, for the owner", "to collect" in text)
    emp = ec.get(f"/admin/arrivals?date={w2.isoformat()}")
    emp_text = _harness.visible_text(emp.get_data(as_text=True))
    s.check("and not for an employee, who still sees the rest",
            emp.status_code == 200 and "to collect" not in emp_text
            and f"{TAG} Champagne" in emp_text and "+33 6 12 34 56 78" in emp_text)
    s.check("a Booking.com guest's time, telephone and ask, from their check-in",
            "19:30" in text and "+44 7700 900123" in text and "late check-in please" in text,
            detail="channel_stays() answers None for all three")
    unarranged = text[text.find(f"{TAG} Board unarranged"):][:90]
    arranged = text[text.find(f"{TAG} Board arranged"):][:60]
    s.check("an extra that needs arranging ahead and has not been says so",
            "not arranged yet" in unarranged.lower(), detail=unarranged)
    s.check("and one that has been does not",
            f"{TAG} Board arranged" in text and "not arranged" not in arranged.lower(), detail=arranged)
    s.check("and one that needs no arranging is not called unarranged",
            "not arranged" not in text[text.find(f"{TAG} Picnic"):][:40].lower())
    asked = text[text.find("They asked"):][:300]
    s.check("what they asked from their booking page, in their words",
            f"{TAG} a note for Jane's anniversary on arrival" in asked
            and "to do" in asked.lower(),
            detail="a task on the stay, which the stay's page showed and the sheet did not")
    s.check("and one done is marked done",
            f"{TAG} towels for the pool" in asked and "done" in asked[asked.find("towels"):].lower())
    card = text[text.find(f"{TAG} RICH"):]
    dinner = card[card.find("Dinner at La Table"):][:160]
    s.check("their dinner at La Table, with what they cannot eat and that it is to confirm",
            "no pork at dinner" in dinner and "to confirm" in dinner.lower()
            and "2 people" in dinner, detail=dinner)

    conn = db()
    with m.app.test_request_context("/"):
        quiet_but_wanted = m.arrivals_sheet(conn, w2 + timedelta(days=1))
        begins = m.arrivals_sheet(conn, w2 + timedelta(days=8))
        ends = m.arrivals_sheet(conn, w2 + timedelta(days=11))
    conn.close()
    # Read off the room lines the page draws, not a list of its own: the
    # extras used to have a section, and the key it was drawn from outlived it.
    ticked = sorted(t["label"] for c in quiet_but_wanted["checklist"] for t in c["ticks"])
    s.check("a day with only an extra for somebody already here is not quiet",
            not quiet_but_wanted["nothing_doing"]
            and ticked == ["Dinner at La Table, 2", f"{TAG} Late checkout"],
            detail=str(ticked))
    s.check("and it is on the page once, on its room's line, with its note",
            text.count(f"{TAG} Late checkout") == 1 and "till two" in text
            and "Extras for guests already here" not in text,
            detail="it had a table of its own as well as the line to tick: on paper "
                   "twice is a box ticked in one place and not the other")
    s.check("an atelier's first day has its guests coming, counted with the rest",
            [a["begins"] for a in begins["ateliers"]] == [True]
            and begins["arriving_count"] == 2 and begins["beds"] == 3,
            detail=f"{begins['arriving_count']} arriving, {begins['beds']} guests")
    s.check("with the time each said they would come", f"{TAG} Place WA" in text and "15:00" in text)
    s.check("and its last day has them going",
            [a["begins"] for a in ends["ateliers"]] == [False] and ends["leaving_count"] == 2,
            detail=f"{ends['leaving_count']} leaving")

    s.section("And it prints")
    s.check("a Print button", "window.print()" in page)
    s.check("a line on the paper saying which days and when it was printed",
            "arr-print-head" in page and "printed" in page)
    s.check("the forms and links for clicking are marked not to print",
            'class="arr-code no-print"' in page and 'class="upload-hint arr-window no-print"' in page)
    s.check("and from tomorrow is one click",
            f'date={(m.house_today() + timedelta(days=1)).isoformat()}"' in page)

    s.section("On paper: boxes to tick (owner, 27 October 2026)")
    # "a spot for ticks for a checklist ... not complicated, just sheets
    # washed, room ready, the extras". One delivered extra, to be printed ticked.
    conn = db()
    conn.execute(
        """INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity,
           status, scheduled_for, created_at) VALUES ('room', ?, ?, 30, 2, 'delivered', ?, ?)""",
        (rich["id"], f"{TAG} Flowers", (w2 + timedelta(days=2)).isoformat(), now))
    conn.commit()
    with m.app.test_request_context("/"):
        on = {n: m.arrivals_sheet(conn, w2 + timedelta(days=n))["checklist"] for n in (2, 3, 4, 5, 8)}
    conn.close()

    def line(n, guest):
        """The line a guest is on that day, coming, going or in the house."""
        name = f"{TAG} {guest}"
        return next((c for c in on[n]
                     if name in [a["guest"] for a in c["arriving"]] + c["leaving"] + c["staying"]), None)

    def labels(c):
        return [t["label"] for t in (c or {}).get("ticks", [])]

    arrives = line(2, "RICH")
    s.check("the day they come: their room, to make up, with the time they come",
            arrives and arrives["turnover"] and arrives["room"] == room["name"]
            and arrives["arriving"] == [{"guest": f"{TAG} RICH", "time": "17:45"}], detail=str(arrives))
    s.check("and the guest leaving it that morning is on the same line: one room, one job",
            arrives and arrives["leaving"] == [f"{TAG} STAYER"]
            and sum(1 for c in on[2] if c["room"] == room["name"]) == 1, detail=str(on[2]))
    gone = line(5, "RICH")
    s.check("a room left with nobody coming still has its sheets to wash",
            gone and gone["turnover"] and gone["leaving"] == [f"{TAG} RICH"] and not gone["arriving"],
            detail="the first printed run had no box at all for a room left empty")
    s.check("with that day's extras, and those with no day",
            arrives and {f"{TAG} Board unarranged", f"{TAG} Board arranged", f"{TAG} Picnic",
                         f"2 × {TAG} Flowers"} <= set(labels(arrives)), detail=str(labels(arrives)))
    s.check("but not the champagne for their second night, nor the one withdrawn",
            arrives and f"{TAG} Champagne" not in labels(arrives)
            and f"{TAG} Withdrawn" not in labels(arrives), detail=str(labels(arrives)))
    s.check("one already delivered is ticked, and only that one",
            arrives and [t["label"] for t in arrives["ticks"] if t["done"]] == [f"2 × {TAG} Flowers"],
            detail=str(arrives and arrives["ticks"]))
    second = line(3, "RICH")
    s.check("the next day they are in the house, with the champagne and their dinner, and no room to make up",
            second and not second["turnover"] and second["staying"] == [f"{TAG} RICH"]
            and sorted(labels(second)) == sorted([f"{TAG} Champagne", "Dinner at La Table, 2"]),
            detail=str(second))
    bubbles = next((t for t in (second or {}).get("ticks", []) if t["label"] == f"{TAG} Champagne"), {})
    s.check("with what the guest wrote beside it, and that it is still to confirm",
            bubbles.get("note") == "in the room by six" and bubbles.get("state") == "to confirm",
            detail=str(bubbles))
    channel = line(4, "Channel")
    s.check("a Booking.com guest's room is on the list too, with their time",
            channel and channel["turnover"] and channel["room"] == "Room not stated"
            and channel["arriving"] == [{"guest": f"{TAG} Channel", "time": "19:30"}], detail=str(on[4]))
    if workshop:
        s.check("and an atelier guest with a room, but not one with none to make up",
                line(8, "Place WA") and line(8, "Place WB") is None, detail=str(on[8]))

    page = oc.get(f"/admin/arrivals?date={w2.isoformat()}").get_data(as_text=True)
    day2 = page[page.find(f'id="day-{(w2 + timedelta(days=2)).strftime("%Y%m%d")}"'):]
    ready = day2[day2.find("To get ready"):day2.find("Arriving</h4>")]
    s.check("on the page each day starts with what to get ready, before the guests",
            "To get ready" in day2 and 0 < day2.find("To get ready") < day2.find("Arriving</h4>"))
    s.check("a column to tick for sheets washed and one for room ready",
            "Sheets washed" in ready and "Room ready" in ready
            and ready.count('class="tick-box"') >= 2, detail=_harness.visible_text(ready)[:200])
    s.check("and a box beside each extra",
            f'aria-hidden="true"></span><span>{TAG} Picnic' in ready,
            detail=ready[ready.find("Picnic") - 120:][:160])
    s.check("drawn boxes for a pen, not checkboxes that save nothing",
            'type="checkbox"' not in ready)
    s.check("the delivered one printed ticked", 'tick-box is-done' in ready and "✓" in ready)
    s.check("in a box that scrolls on a phone",
            '<div class="table-wrap arr-ready-wrap">' in ready)

    s.section("And it is fit for paper")
    css = open(os.path.join(m.BASE_DIR, "static", "style.css"), encoding="utf-8").read()
    printing = " ".join(re.findall(r"@media print\s*\{(.*?)\n\}", css, re.S))
    s.check("a day's list is never cut in two across pages",
            re.search(r"\.arr-ready-wrap\s*\{[^}]*break-inside:\s*avoid", printing) is not None,
            detail="the first printed run had Friday's list half on one page and half on the next")
    s.check("the offer of notifications is kept off the paper",
            re.search(r"#push-prompt[^{]*\{[^}]*display:\s*none\s*!important", printing) is not None,
            detail="a script shows it as an inline style, outside .flash-wrap, so only "
                   "!important in the print rules keeps it off")
    s.check("and a guest's card is set at a size for paper",
            re.search(r"\.arr-card\s*\{[^}]*font-size", printing) is not None,
            detail="at the screen's size each card ran half a page")

    s.section("Easy to read: the time, the occasion, the requests (owner, 27 October 2026)")
    # "some of it isn't very clear, the anniversary, the check in time etc on
    # both the booking and the print sheet".
    conn = db()
    conn.execute("UPDATE bookings SET special_occasion = ? WHERE id = ?",
                 ("Our tenth anniversary", rich["id"]))
    conn.commit()
    conn.close()
    page = oc.get(f"/admin/arrivals?date={w2.isoformat()}").get_data(as_text=True)
    at = page.find(f"{TAG} RICH</strong>")
    card = _harness.visible_text(page[at:page.find("expense-card arr-card", at)])
    order = [card.find(x) for x in ("Arriving about 17:45", "Celebrating Our tenth anniversary",
                                    "Cannot eat", "They asked")]
    s.check("on the card: the time they come, then the occasion and what they cannot eat, then the rest",
            at > 0 and -1 not in order and order == sorted(order), detail=f"{order} {card[:300]}")
    s.check("the time said beside the house's own, never in its place",
            f"check-in from {m.CHECK_IN_TIME}" in card, detail=card[:200])
    s.check("what they asked is under one heading, not two",
            "Asked from their booking page" not in page and card.count("They asked") == 1)

    bp = oc.get(f"/admin/bookings/{rich['id']}").get_data(as_text=True)
    head = _harness.visible_text(bp[:bp.find('class="bk-tabs"')])
    s.check("the booking: the time they come under the name, said as theirs",
            "Arriving about 17:45 (they told us)" in head and f"check-in from {m.CHECK_IN_TIME}" in head,
            detail=head[-400:])
    s.check("and the occasion beside it, with how far it is prepared",
            "Celebrating Our tenth anniversary" in head and "preparation not started" in head,
            detail=head[-400:])
    s.check("and what they cannot eat, with when it is deleted",
            "Cannot eat no pork" in head and "deleted once the stay is over" in head)
    box = _harness.visible_text(bp[bp.find("Waiting on the house"):bp.find('class="stat-tiles"')])
    s.check("a request is said once, not twice", box.count("Jane's anniversary") == 1, detail=box)
    s.check("with the day it is for in words", "for the day they arrive" in box, detail=box)
    stay = _harness.visible_text(bp[bp.find("<h3>The stay</h3>"):bp.find('id="extras"')])
    s.check("the stay says when they leave by",
            f"check-out by {m.CHECK_OUT_TIME}" in stay, detail=stay[:300])
    s.check("and their dinners at La Table, which it never showed",
            "Dinner at La Table" in stay and "no pork at dinner" in stay, detail=stay[-300:])
    s.check("and does not say the occasion or the diet a second time",
            "Celebrating" not in stay and "Cannot eat" not in stay, detail=stay[:300])

    if workshop:
        s.section("And the atelier pages say it the same way")
        conn = db()
        conn.execute("UPDATE workshop_bookings SET occupancy_type = 'solo', special_occasion = ?, "
                     "dietary_notes = ? WHERE reference_code = ?",
                     ("Her fiftieth birthday", "no dairy", f"{TAG}-WB"))
        conn.commit()
        conn.close()
        sp = _harness.visible_text(oc.get(f"/admin/workshops/sessions/{session_id}").get_data(as_text=True))
        s.check("the sitting's page says when each comes, or that they have not said",
                "Arriving about 15:00 (they told us)" in sp and "Arrival time not given" in sp,
                detail="it was only ever inside the checked-in-online chip")
        wb = sp[sp.find(f"{TAG} Place WB"):][:400]
        s.check("a room to themselves is said as that, not as sharing",
                "a room to themselves" in wb and "sharing" not in wb,
                detail=f"the page tested for 'single'; the private room is 'solo': {wb}")
        s.check("with what they are celebrating and cannot eat, in words",
                "Celebrating: Her fiftieth birthday" in wb and "Cannot eat: no dairy" in wb, detail=wb)
        rp = _harness.visible_text(oc.get(f"/admin/workshops/registrations?session_id={session_id}")
                                   .get_data(as_text=True))
        s.check("the registrations say it as the arrivals sheet does",
                "Arriving about 15:00 (they told us)" in rp and "Celebrating Her fiftieth birthday" in rp
                and "Cannot eat no dairy" in rp, detail=rp[:400])
        raw = oc.get(f"/admin/workshops/registrations?session_id={session_id}").get_data(as_text=True)
        chips = re.findall(r'class="mini-status ([\w-]+)">Paid in full<', raw)
        s.check("paid in full is drawn as good news, never in the danger colour",
                chips and all(c == "status-paid" for c in chips),
                detail=f"{chips} -- it was red on every registration")
        ws = _harness.visible_text(oc.get(f"/workshops/{session_id}/sheet").get_data(as_text=True))
        s.check("and the instructor's sheet says when they come and who is celebrating",
                "Arriving about 15:00 (they told us)" in ws and "Celebrating" in ws
                and "Worth knowing" not in ws, detail=ws[:400])

    s.section("Booking.com's guests: arriving in their own table, leaving with everybody")
    table = page[page.find("Arriving through Booking.com"):]
    table = table[:table.find("</table>")]
    heads = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', table[:table.find("</thead>")])
    s.check("every column of it has a heading",
            heads and all(h.strip() for h in heads),
            detail=f"{heads} -- one said 'arriving' or 'leaving' under no heading at all")
    s.check("and its time is said as theirs, about", "about 19:30" in _harness.visible_text(table),
            detail=_harness.visible_text(table)[:300])
    gone = page[page.find(f'id="day-{(w2 + timedelta(days=6)).strftime("%Y%m%d")}"'):]
    gone = gone[:gone.find("</section>")]
    leaving = _harness.visible_text(gone[gone.find("Leaving</h4>"):])
    s.check("a Booking.com guest leaving is in the one list of who leaves",
            f"{TAG} Channel" in leaving and "paid to Booking.com" in leaving,
            detail=leaving[:300])
    s.check("not in a table of its own with three empty cells",
            "Through Booking.com" not in gone and "Arriving through Booking.com" not in gone)
    css = open(os.path.join(m.BASE_DIR, "static", "style.css"), encoding="utf-8").read()
    s.check("tables that turn into cards on a phone stay tables on paper",
            "@media screen and (max-width: 760px){\n  .bk-stack, .phone-cards{ min-width:0; }" in css,
            detail="A4 is narrower than 760px: the extras due sheet printed one extra to a card")

    s.section("One day printed on its own")
    # The morning's list is what housekeeping takes round the house, and Print
    # gave them the whole fortnight (27 October 2026).
    days = re.findall(r'<section class="arr-day" id="(day-\d+)"', page)
    buttons = re.findall(r'class="small-link no-print arr-print-day" data-day="(day-\d+)"', page)
    s.check("every day has its own Print this day, for that day",
            days and buttons == days, detail=f"{len(days)} days, buttons for {buttons[:3]}")
    s.check("printed alone, a day says whose house, which day and when it was printed",
            page.count('class="arr-print-head arr-print-head--day"') == len(days))
    printing = " ".join(re.findall(r"@media print\s*\{(.*?)\n\}", css, re.S))
    hidden = re.search(r"([^{}]*)\{\s*display:none !important;\s*\}",
                       printing[printing.find("body[data-print-day] .arr-cal"):])
    s.check("and everything else stays off that paper: the fortnight, the other days, what is booked later",
            hidden is not None and all(x in hidden.group(1) for x in (
                ".arr-cal", ".arr-day:not(.is-printing)", "#later", ".arr-print-head--window")),
            detail=hidden.group(1) if hidden else "no rule")

    s.section("The front desk and the bookings list say it the same way")
    desk = oc.get("/admin/desk").get_data(as_text=True)
    desk = desk[desk.find('id="arriving"'):]
    row = _harness.visible_text(desk[desk.find(f"{TAG} TODAY"):desk.find("</tr>", desk.find(f"{TAG} TODAY"))])
    s.check("an arrival with no time says so in words, not with a dash",
            "not given" in row and "—" not in row, detail=row)
    listed = _harness.visible_text(oc.get("/admin/bookings").get_data(as_text=True))
    s.check("the bookings list says a time as theirs, about",
            "arriving about 18:30" in listed, detail="it said 'arriving 18:30', as if promised")

    s.section("On a phone the list to tick is a card a room")
    css = open(os.path.join(m.BASE_DIR, "static", "style.css"), encoding="utf-8").read()
    phone = re.search(r"@media screen and \(max-width: 640px\)\{(.*?)\n\}", css, re.S)
    s.check("its rows stack, screen only, so the paper keeps its columns",
            phone is not None and ".arr-ready tr{ display:grid" in phone.group(1),
            detail="as a table it scrolled sideways and hid Room ready and the extras")
    s.check("and each box says what it is for",
            'data-label="Sheets washed"' in page and 'data-label="Room ready"' in page)

    s.section("Where Printable sheet goes")
    with m.app.test_request_context("/"):
        sheet_url = m.url_for("arrivals_sheet_page")
    home = oc.get("/").get_data(as_text=True)
    at = home.find(">Printable sheet<")
    s.check("the owner's home has the button", at > 0)
    s.check("and it opens this sheet, not the Today page",
            f'href="{sheet_url}"' in home[home.rfind("<a ", 0, at):at], detail=home[max(at - 160, 0):at])
    s.check("and an unarranged extra says so beside its box",
            "not arranged yet" in _harness.visible_text(ready).lower(),
            detail="the box alone would hide the one thing worth knowing")
    for q in ("printable sheet", "print", "checklist"):
        res = oc.get("/api/palette", query_string={"q": q}).get_json()["results"]
        s.check(f'"{q}" in the search box finds it', sheet_url in {x["url"] for x in res},
                detail=str([x["label"] for x in res]))

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
