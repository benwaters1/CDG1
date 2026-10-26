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
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
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
    """The page from the first week on: the days, not the calendar above them."""
    at = page.find('class="arr-week"')
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
    s.check("said as their usual, not as this stay's",
            "usually arrives 16:00" in body,
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

    s.section("On paper: every arrival's extras, time, telephone and asks")
    page = oc.get(f"/admin/arrivals?date={w2.isoformat()}").get_data(as_text=True)
    days = _days(page)
    text = _harness.visible_text(days)
    s.check("the calendar is on the page, in a box that scrolls",
            'class="data-table arr-cal__grid"' in page
            and page.find('class="table-wrap"') < page.find("arr-cal__grid"))
    s.check("the arrival time in words, not a bare figure", "arriving 17:45" in text,
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

    conn = db()
    with m.app.test_request_context("/"):
        quiet_but_wanted = m.arrivals_sheet(conn, w2 + timedelta(days=1))
        begins = m.arrivals_sheet(conn, w2 + timedelta(days=8))
        ends = m.arrivals_sheet(conn, w2 + timedelta(days=11))
    conn.close()
    s.check("a day with only an extra for somebody already here is not quiet",
            not quiet_but_wanted["nothing_doing"]
            and [x["name"] for x in quiet_but_wanted["extras_here"]] == [f"{TAG} Late checkout"],
            detail=str([x["name"] for x in quiet_but_wanted["extras_here"]]))
    s.check("and it is on the page under the guests already here",
            "Extras for guests already here" in text and f"{TAG} Late checkout" in text)
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
