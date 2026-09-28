"""Booking.com's guests, where the house works.

Every list the house works from read `bookings`, and a Booking.com guest is not
in it -- so they were not anonymous on those lists, they were absent. The room
board called a room with a guest asleep in it "Ready"; breakfast counted
nobody; a same-day changeover between a Booking.com departure and the house's
own arrival was not a changeover; the morning digest did not mention them.

What the emails say about each stay is now in ota_reservations, and
channel_stays() hands those stays to each list in the shape the list already
reads. They are NOT copied into `bookings`, and this suite holds that line
too: a row there would be sent the house's letters, asked for a balance at the
house's own rates, counted as revenue and exported back to the channel.

WHAT IS HELD HERE

  EACH LIST NAMES THEM: Today (cards, arrivals, departures, the room board),
  the guests page, both calendars, the kitchen sheet and breakfast, the office
  display, changeovers, the owner's home, the morning digest, the arrivals
  sheet and the assistant.

  WHAT THE EMAIL DID NOT SAY IS SAID AS NOT KNOWN. A party size Booking.com
  left out is "not stated", not "party of 1" and not nobody.

  ONLY WHAT IS REAL: a cancelled stay is on none of them, and one the house
  entered by hand as well is on each of them once.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, visible_text
import _harness

m = _harness.m
TAG = "ZZBCS"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup(conn):
    # The turnover checklist first: when a stay is deleted its tasks keep going
    # with the link cleared, and a task that has lost it cannot be found here.
    # (Its fiches go with it, by the foreign key; deleted here all the same.)
    ours = """(SELECT id FROM ota_reservations
                WHERE guest_name LIKE ? OR reservation_number LIKE '98766%')"""
    conn.execute(f"DELETE FROM tasks WHERE ota_reservation_id IN {ours}", (f"%{TAG}%",))
    conn.execute(f"DELETE FROM channel_police_register WHERE ota_reservation_id IN {ours}",
                 (f"%{TAG}%",))
    conn.execute("DELETE FROM ota_reservations WHERE guest_name LIKE ? OR reservation_number LIKE '98766%'",
                 (f"%{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _free_rooms(conn, start, end, want=2):
    """Active rooms with nothing of the house's own on them over [start, end).

    Asked, not assumed: the database is a copy of a live one and other suites
    book rooms in the same run, and a board showing somebody else's guest in
    the room would make every check below about the wrong person.
    """
    free = []
    for room in conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY id").fetchall():
        clash = conn.execute(
            """SELECT 1 FROM bookings WHERE room_id = ? AND status IN ('pending', 'confirmed')
                AND arrival_date < ? AND departure_date > ? LIMIT 1""",
            (room["id"], end.isoformat(), start.isoformat())).fetchone()
        if not clash:
            free.append(room)
        if len(free) == want:
            break
    return free


def _stay(conn, number, guest, room_id, arrive, leave, guests, status="confirmed"):
    conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
               arrival_date, departure_date, room_label, room_id, guests,
               first_seen_at, last_event_at, updated_at)
           VALUES ('booking.com', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)""",
        (number, status, guest, arrive.isoformat() if arrive else None,
         leave.isoformat() if leave else None, room_id, guests, _now(), _now(), _now()))


def _room_section(sheet):
    """The sheet's "staying in the house" section -- which it leaves out when
    nobody is staying, so an empty house reads as none rather than a zero."""
    return next((x for x in sheet["sections"] if x["kind"] == "room"),
                {"covers": 0, "rows": [], "unknown_party": 0})


def run():
    s = Suite("Booking.com's guests, where the house works")
    oc, ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    try:
        _run(s, oc, ec, owner, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, ec, owner, conn):
    today = m.house_today()
    rooms = _free_rooms(conn, today - timedelta(days=4), today + timedelta(days=5))
    if not s.check("two rooms are free of the house's own guests around today (a precondition)",
                   len(rooms) == 2, detail=str([r["name"] for r in rooms])):
        return
    a, b = rooms
    iso = today.isoformat()
    tomorrow = today + timedelta(days=1)

    # Baselines, taken before any Booking.com stay exists, so each list's own
    # figure is compared with itself rather than with a number this suite
    # would have to guess.
    with m.app.test_request_context("/"):
        base_kitchen = _room_section(m.kitchen_sheet(conn, today))
        base_display = {x["key"]: x["value"] for x in m.build_office_display_stats(
            conn, today, m.guests_in_residence(conn, today))}
    base_breakfast = visible_text(oc.get("/breakfast").get_data(as_text=True))

    # In the house since yesterday; arriving today with no party size; leaving
    # today from the room the arrival goes into; arriving tomorrow with no room
    # named; and two that must appear nowhere.
    _stay(conn, "9876600001", f"Zoe {TAG} Hart", a["id"], today - timedelta(days=1), today + timedelta(days=2), 2)
    _stay(conn, "9876600002", f"Ines {TAG} Roca", b["id"], today, today + timedelta(days=3), None)
    _stay(conn, "9876600003", f"Tom {TAG} Vale", b["id"], today - timedelta(days=3), today, 3)
    _stay(conn, "9876600004", f"Ada {TAG} Lowe", None, tomorrow, tomorrow + timedelta(days=2), 2)
    _stay(conn, "9876600005", f"Cal {TAG} Gone", a["id"], today + timedelta(days=2),
          today + timedelta(days=4), 2, status="cancelled")
    _stay(conn, "9876600006", f"Dee {TAG} Undated", a["id"], None, None, 2)
    # One the house also entered by hand, as a booking of its own.
    far = today + timedelta(days=300)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, created_at, source)
           VALUES (?, ?, ?, ?, 'zzbcs.dup@example.invalid', ?, ?, 2, 'confirmed', ?, 'agent')""",
        (a["id"], TAG + "-DUP", TAG + "-duptoken", f"Eve {TAG} Twice", far.isoformat(),
         (far + timedelta(days=2)).isoformat(), _now()))
    _stay(conn, "9876600007", f"Eve {TAG} Twice", a["id"], far, far + timedelta(days=2), 2)
    conn.commit()

    s.section("Which stays count")
    stays = {st["guest_name"]: st for st in m.channel_stays(conn)}
    ours = {n for n in stays if TAG in n}
    s.check("confirmed, dated stays are the house's to know about",
            {f"Zoe {TAG} Hart", f"Ines {TAG} Roca", f"Tom {TAG} Vale", f"Ada {TAG} Lowe"} <= ours,
            detail=str(sorted(ours)))
    s.check("a cancelled one is not", f"Cal {TAG} Gone" not in ours)
    s.check("nor one with no dates to put it on", f"Dee {TAG} Undated" not in ours)
    s.check("and one the house entered by hand as well is counted once, as its own",
            f"Eve {TAG} Twice" not in ours)
    zoe = stays.get(f"Zoe {TAG} Hart") or {}
    s.check("each says where it came from, and its room", zoe.get("channel") == "Booking.com"
            and zoe.get("room_name") == a["name"] and zoe.get("reference_code") == "9876600001",
            detail=str(zoe))
    s.check("and none of it is a booking of the house's own",
            conn.execute("SELECT COUNT(*) FROM bookings WHERE guest_name LIKE ?",
                         (f"%{TAG}%",)).fetchone()[0] == 1,
            detail="only the one this suite entered by hand; a Booking.com stay in bookings "
                   "would be sent the house's letters and asked for a balance")

    here = {g["name"]: g for g in m.guests_in_residence(conn, today)}
    s.check("who is in residence includes Booking.com's guests",
            f"Zoe {TAG} Hart" in here and f"Ines {TAG} Roca" in here, detail=str(sorted(here))[:200])
    s.check("marked, and without a booking to open",
            here.get(f"Zoe {TAG} Hart", {}).get("channel") == "Booking.com"
            and here.get(f"Zoe {TAG} Hart", {}).get("booking_id") is None)
    s.check("a guest leaving today is not in residence tonight, as for the house's own",
            f"Tom {TAG} Vale" not in here)
    s.check("and the list can still be had without them, for what acts on bookings",
            not any(g["channel"] or g["name"] == f"Zoe {TAG} Hart"
                    for g in m.stays_with_status(conn, today, channels=False)))

    s.section("Today")
    page = oc.get("/today").get_data(as_text=True)
    text = visible_text(page)
    s.check("the Today page opens", "In the house" in text)
    s.check("with Booking.com's guest in the house, and where they booked",
            f"Zoe {TAG} Hart" in text and "booked through Booking.com" in text, detail=text[:300])
    arriving = re.search(r"Arriving today:(.*?)(?:Leaving today:|In the house)", text, re.S)
    leaving = re.search(r"Leaving today:(.{0,300})", text, re.S)
    s.check("today's arrivals name the Booking.com arrival",
            bool(arriving) and f"Ines {TAG} Roca" in arriving.group(1), detail=arriving.group(1)[:200] if arriving else "")
    s.check("and today's departures the Booking.com departure",
            bool(leaving) and f"Tom {TAG} Vale" in leaving.group(1), detail=leaving.group(1)[:200] if leaving else "")
    with m.app.test_request_context("/"):
        board = {r["room"]["id"]: r for r in m.room_board(conn, today)}
    s.check("the room with a Booking.com guest in it is Occupied, not Ready",
            board.get(a["id"], {}).get("state") == "occupied", detail=str(board.get(a["id"], {}).get("state")))
    s.check("the room a Booking.com guest arrives in today says who is coming",
            board.get(b["id"], {}).get("state") == "arriving"
            and (board[b["id"]]["arriving"] or {})["guest_name"] == f"Ines {TAG} Roca",
            detail=str(board.get(b["id"], {}).get("state")))
    s.check("and who is leaving it", (board.get(b["id"], {}).get("leaving") or {}).get("guest_name")
            == f"Tom {TAG} Vale")
    card = re.search(rf'{re.escape(a["name"])}(.{{0,400}}?)Zoe {TAG} Hart', text, re.S)
    s.check("the board on the page shows it", bool(card) and "Occupied" in card.group(1),
            detail=card.group(0)[:200] if card else "the room and guest are not on the board")
    s.check("staff see the same page", f"Zoe {TAG} Hart" in visible_text(ec.get("/today").get_data(as_text=True)))

    s.section("The other lists of who is here")
    text = visible_text(oc.get("/guests").get_data(as_text=True))
    s.check("the guests page has them in residence", f"Zoe {TAG} Hart" in text
            and "booked through Booking.com" in text)
    s.check("and arriving soon", f"Ada {TAG} Lowe" in text)
    s.check("the owner's home", f"Zoe {TAG} Hart" in visible_text(oc.get("/").get_data(as_text=True)))
    s.check("the office display", f"Zoe {TAG} Hart" in visible_text(oc.get("/admin/display").get_data(as_text=True)))
    s.check("the day sheet", f"Zoe {TAG} Hart" in visible_text(oc.get("/admin/today-sheet").get_data(as_text=True)))
    with m.app.test_request_context("/"):
        said = m.assistant_read_tool(conn, owner, "who_is_here", {})
    s.check("and the assistant, which fell over the moment anybody was staying",
            f"Zoe {TAG} Hart" in said and "(through Booking.com)" in said, detail=said[:200])
    with m.app.test_request_context("/"):
        m.photo_declines(conn, today)
    s.check("a guest with no profile or address is simply not among the photo refusals", True)

    s.section("Turning the room round after a Booking.com departure")
    # The owner's home was opened just above, and it makes the checklist the
    # same way it prepares arrivals.
    rid = {r["reservation_number"]: r["id"] for r in conn.execute(
        "SELECT id, reservation_number FROM ota_reservations WHERE reservation_number LIKE '98766%'")}
    tom_id, zoe_rid = rid["9876600003"], rid["9876600001"]

    def _turnover(reservation_id):
        return conn.execute("SELECT * FROM tasks WHERE ota_reservation_id = ? ORDER BY id",
                            (reservation_id,)).fetchall()
    work = _turnover(tom_id)
    s.check("the room a Booking.com guest leaves today gets its turnover checklist",
            len(work) == len(m.CHECKOUT_CHECKLIST), detail=f"{len(work)} tasks")
    s.check("the house's own checklist, on that room, due today",
            bool(work) and all(t["title"].startswith(f"{b['name']}: ") and t["due_date"] == iso
                               and t["origin"] == "checklist" for t in work)
            and {t["title"].split(": ", 1)[1] for t in work} == set(m.CHECKOUT_CHECKLIST),
            detail=str([t["title"] for t in work])[:200])
    s.check("saying who left and how they booked",
            bool(work) and work[0]["room_note"] == f"Tom {TAG} Vale leaving today, booked through "
                                                  "Booking.com, party of 3.",
            detail=work[0]["room_note"] if work else "")
    with m.app.test_request_context("/"):
        again = m.prep_channel_departures(conn, today)
    s.check("made once, however often it is asked", again == 0 and len(_turnover(tom_id)) == len(work))
    s.check("while the room a Booking.com guest is still in gets none", not _turnover(zoe_rid))
    _stay(conn, "9876600009", f"Nia {TAG} Kerr", None, today - timedelta(days=2), today, 2)
    _stay(conn, "9876600010", f"Yan {TAG} Past", a["id"], today - timedelta(days=4), today - timedelta(days=1), 2)
    conn.commit()
    with m.app.test_request_context("/"):
        made = m.prep_channel_departures(conn, today)
    extra = [r["id"] for r in conn.execute(
        "SELECT id FROM ota_reservations WHERE reservation_number IN ('9876600009', '9876600010')")]
    s.check("nor one whose room the email did not name, nor one that left yesterday",
            made == 0 and not any(_turnover(x) for x in extra))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number IN ('9876600009', '9876600010')")
    conn.commit()
    with m.app.test_request_context("/"):
        board = {r["room"]["id"]: r for r in m.room_board(conn, today)}
    s.check("and the room board says the room needs turning over, not Ready",
            board[b["id"]]["state"] == "turnover", detail=board[b["id"]]["state"])
    s.check("urgently, because somebody arrives in it today", board[b["id"]]["urgent"] is True)
    text = visible_text(oc.get("/today").get_data(as_text=True))
    s.check("on the Today page too", "Needs turning over" in text
            and "Somebody arrives today and this room is not done." in text)
    conn.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE ota_reservation_id = ?",
                 (_now(), tom_id))
    conn.commit()
    with m.app.test_request_context("/"):
        board = {r["room"]["id"]: r for r in m.room_board(conn, today)}
    s.check("ticked off, the room is ready for the guest coming in",
            board[b["id"]]["state"] == "arriving", detail=board[b["id"]]["state"])
    src = open(m.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    s.check("and the ten-minute housekeeping job makes it too, for a morning nobody opens the page",
            "prep_channel_departures(conn, today)" in src.split("def run_housekeeping_job")[1][:900],
            detail="a checklist only the owner's home makes is one that waits for the owner")

    s.section("Owner's home and the morning note")
    user = dict(owner) if not isinstance(owner, dict) else owner
    with m.app.test_request_context("/"):
        day = m.owner_home_day(conn, today, user)
        home_guests = m.owner_home_guests(conn, today)
    ines = [r for r in day if r["title"] == f"Ines {TAG} Roca arriving"]
    s.check("the day lists the Booking.com arrival", len(ines) == 1, detail=str([r["title"] for r in day])[:200])
    s.check("saying the party size was not given, rather than inventing one",
            bool(ines) and "party size not stated" in ines[0]["detail"] and "Booking.com" in ines[0]["detail"],
            detail=ines[0]["detail"] if ines else "")
    s.check("never ticked off -- nothing in the app records it done",
            bool(ines) and ines[0]["done"] is False)
    s.check("and the departure", any(r["title"] == f"Tom {TAG} Vale departing" for r in day))
    s.check("in residence, with where they booked",
            any(g["name"] == f"Zoe {TAG} Hart" and "Booking.com" in g["room"] for g in home_guests))
    with m.app.test_request_context("/"):
        _subject, body, _anything = m.morning_digest(conn, today)
    s.check("the morning note names the arrival, and that the party size is not stated",
            f"Ines {TAG} Roca (Booking.com), party size not stated" in body, detail=body[:400])
    s.check("and the departure", f"Tom {TAG} Vale (Booking.com)" in body)

    s.section("The calendars")
    with m.app.test_request_context("/"):
        rows = m.build_overview(conn, "week", today)["rows"]
    mine = [r for r in rows if TAG in (r["title"] or "")]
    s.check("the week's calendar has the Booking.com arrival and departure",
            any(r["title"].startswith(f"Ines {TAG} Roca arrives") for r in mine)
            and any(r["title"].startswith(f"Tom {TAG} Vale departs") for r in mine),
            detail=str([r["title"] for r in mine]))
    s.check("each linking to the Booking.com page, not to a booking that is not there",
            all("/management/booking-com" in (r["link"] or "") for r in mine) and bool(mine))
    s.check("and the cancelled stay is not on it", not any("Gone" in r["title"] for r in mine))
    text = visible_text(oc.get("/calendar").get_data(as_text=True))
    s.check("the calendar page shows them", f"Ines {TAG} Roca" in text, detail=text[:200])
    text = visible_text(oc.get(f"/admin/calendar?month={today.strftime('%Y-%m')}").get_data(as_text=True))
    s.check("the rooms calendar names the Booking.com stay in its room",
            f"Zoe {TAG} Hart · Booking.com" in text, detail=text[:300])

    s.section("Breakfast and the kitchen")
    with m.app.test_request_context("/"):
        kitchen = _room_section(m.kitchen_sheet(conn, today))
    s.check("the kitchen counts a Booking.com party that is staying tonight",
            kitchen["covers"] == base_kitchen["covers"] + 2,
            detail=f"{base_kitchen['covers']} -> {kitchen['covers']}: Zoe's 2, and not Tom, who has gone")
    s.check("and says how many rooms it cannot count, rather than counting them as nobody",
            kitchen.get("unknown_party") == 1
            and any(r["who"] == f"Ines {TAG} Roca" for r in kitchen["rows"]), detail=str(kitchen.get("unknown_party")))
    sheet_text = visible_text(oc.get(f"/kitchen/sheet?day={iso}").get_data(as_text=True))
    s.check("on the sheet itself", "1 more room whose number is not known" in sheet_text, detail=sheet_text[:300])

    def _counts(text):
        got = re.search(r"(\d+) guests? across (\d+) rooms? today", text)
        return (int(got.group(1)), int(got.group(2))) if got else (0, 0)
    before, after = _counts(base_breakfast), _counts(visible_text(oc.get("/breakfast").get_data(as_text=True)))
    s.check("breakfast counts them, and their rooms",
            after == (before[0] + 3, before[1] + 2),
            detail=f"{before} -> {after}: Zoe's party of 2 and Ines's unknown party as 1, in two rooms")

    s.section("The office display's figures agree with its list")
    with m.app.test_request_context("/"):
        display = {x["key"]: x["value"] for x in m.build_office_display_stats(
            conn, today, m.guests_in_residence(conn, today))}
    s.check("two more rooms occupied", display["occupancy"] == base_display["occupancy"] + 2,
            detail=f"{base_display['occupancy']} -> {display['occupancy']}")
    s.check("one more arrival and one more departure",
            display["arrivals"] == base_display["arrivals"] + 1
            and display["departures"] == base_display["departures"] + 1,
            detail=f"{base_display} -> {display}")
    s.check("and the guests in residence", display["guests"] == base_display["guests"] + 3)

    s.section("Changeovers")
    with m.app.test_request_context("/"):
        report = m.turnaround_report(conn, days=7)
    ours = [c for d in report["days"] for c in d["changeovers"]
            if TAG in (c["out_guest"] or "") or TAG in (c["in_guest"] or "")]
    s.check("a Booking.com guest leaving the room another arrives in is a changeover",
            any(c["out_guest"] == f"Tom {TAG} Vale" and c["in_guest"] == f"Ines {TAG} Roca"
                and c["day"] == today for c in ours), detail=str(ours))
    text = visible_text(oc.get("/admin/turnarounds").get_data(as_text=True))
    s.check("and the changeovers page shows it", f"Tom {TAG} Vale" in text, detail=text[:200])

    s.section("The arrivals sheet")
    with m.app.test_request_context("/"):
        sheet = m.arrivals_sheet(conn, tomorrow)
    ada = [st for st in sheet["channel_arriving"] if st["guest_name"] == f"Ada {TAG} Lowe"]
    s.check("tomorrow's sheet has the Booking.com arrival", len(ada) == 1)
    s.check("and the day is not called empty because of it", not sheet["nothing_doing"])
    page = oc.get(f"/admin/arrivals?date={tomorrow.isoformat()}").get_data(as_text=True)
    text = visible_text(page)
    s.check("on the page, with what the email did not say said as not stated",
            "Through Booking.com" in text and f"Ada {TAG} Lowe" in text and "not stated" in text,
            detail=text[:300])
    s.check("the owner can follow the reservation to its emails",
            'href="/management/booking-com?q=9876600004"' in page)
    staff_page = ec.get(f"/admin/arrivals?date={tomorrow.isoformat()}").get_data(as_text=True)
    s.check("somebody who cannot open that page sees the number, not a link to a refusal",
            "9876600004" in staff_page and 'href="/management/booking-com?q=9876600004"' not in staff_page)
    with m.app.test_request_context("/"):
        said = m.assistant_read_tool(conn, owner, "arrivals", {"day": tomorrow.isoformat()})
    s.check("and the assistant reads them out", f"Ada {TAG} Lowe" in said and "through Booking.com" in said,
            detail=said[:300])

    s.section("The police register")
    # The obligation is on the house, whoever took the booking: a Booking.com
    # guest who is not French needs a fiche like anybody else.
    with m.app.test_request_context("/"):
        missing = m.stays_missing_fiches(conn, today)
    owed = {x["booking"]["guest_name"]: x for x in missing if x.get("channel")}
    s.check("a Booking.com stay that has begun is waiting for its fiches",
            f"Zoe {TAG} Hart" in owed and owed[f"Zoe {TAG} Hart"]["outstanding"] == 2,
            detail=str({k: v["outstanding"] for k, v in owed.items()}))
    s.check("one whose email gave no number is owed one, and says the number is not known",
            f"Ines {TAG} Roca" in owed and owed[f"Ines {TAG} Roca"]["outstanding"] == 1
            and owed[f"Ines {TAG} Roca"]["party_known"] is False)
    s.check("and one arriving tomorrow is not late yet", f"Ada {TAG} Lowe" not in owed)
    zoe_id = owed[f"Zoe {TAG} Hart"]["booking"]["ota_reservation_id"] if f"Zoe {TAG} Hart" in owed else 0
    page = oc.get("/admin/register").get_data(as_text=True)
    s.check("the register page asks for them, through their own form",
            f"/admin/booking-com/stays/{zoe_id}/register" in page
            and "booked through Booking.com" in visible_text(page))
    s.check("and says so where the number is not known",
            "the booking did not say how many are staying" in visible_text(page))

    def _fiches():
        return conn.execute("SELECT * FROM channel_police_register WHERE ota_reservation_id = ? ORDER BY id",
                            (zoe_id,)).fetchall()
    r = oc.post(f"/admin/booking-com/stays/{zoe_id}/register",
                data={"surname": "Hart", "first_names": "Zoe", "nationality": "British"})
    s.check("a surname, first names and nationality go on the register",
            len(_fiches()) == 1, detail=f"{r.status_code} {len(_fiches())}")
    oc.post(f"/admin/booking-com/stays/{zoe_id}/register",
            data={"surname": "Hart", "first_names": "", "nationality": "British"})
    oc.post(f"/admin/booking-com/stays/{zoe_id}/register",
            data={"surname": "Hart", "first_names": "Sam", "nationality": "British",
                  "born_on": (today + timedelta(days=30)).isoformat()})
    s.check("without first names, or born next month, nothing is added -- the same rules as the house's own",
            len(_fiches()) == 1, detail=str(len(_fiches())))
    r = ec.post(f"/admin/booking-com/stays/{zoe_id}/register",
                data={"surname": "Hart", "first_names": "Kit", "nationality": "British"})
    s.check("an employee cannot write on it", r.status_code in (302, 403) and len(_fiches()) == 1)
    cancelled_id = conn.execute("SELECT id FROM ota_reservations WHERE reservation_number = '9876600005'"
                                ).fetchone()["id"]
    s.check("a cancelled stay takes no fiche",
            oc.post(f"/admin/booking-com/stays/{cancelled_id}/register",
                    data={"surname": "Gone", "first_names": "Cal", "nationality": "Irish"}).status_code == 404)
    oc.post(f"/admin/booking-com/stays/{zoe_id}/register",
            data={"surname": "Hart", "first_names": "Noah", "nationality": "française"})
    with m.app.test_request_context("/"):
        still = [x for x in m.stays_missing_fiches(conn, today)
                 if x.get("channel") and x["booking"]["guest_name"] == f"Zoe {TAG} Hart"]
    s.check("once the whole party is recorded, French or not, the stay is settled", not still)
    table = visible_text(oc.get(f"/admin/register?from={(today - timedelta(days=5)).isoformat()}"
                                f"&to={iso}").get_data(as_text=True))
    s.check("the register lists them with their room and dates, beside the house's own",
            "Hart, Zoe" in table and a["name"] in table
            and (today - timedelta(days=1)).isoformat() in table, detail=table[-400:])
    first = _fiches()[0]["id"]
    r = oc.post(f"/admin/register/channel/{first}/delete", follow_redirects=True)
    s.check("a mistyped one can be taken off", len(_fiches()) == 1
            and any("Removed from the register" in f for f in _harness.flashes(r)))
    s.check("and an employee cannot take one off",
            ec.post(f"/admin/register/channel/{_fiches()[0]['id']}/delete").status_code in (302, 403)
            and len(_fiches()) == 1)
    # Six months after the stay, like every other fiche.
    long_ago = today - timedelta(days=m.POLICE_REGISTER_KEEP_MONTHS * 31 + 10)
    _stay(conn, "9876600008", f"Old {TAG} Guest", a["id"], long_ago - timedelta(days=2), long_ago, 1)
    old_id = conn.execute("SELECT id FROM ota_reservations WHERE reservation_number = '9876600008'"
                          ).fetchone()["id"]
    conn.execute(
        """INSERT INTO channel_police_register (ota_reservation_id, surname, first_names,
               nationality, recorded_at) VALUES (?, 'Guest', 'Old', 'Belgian', ?)""",
        (old_id, _now()))
    conn.commit()
    with m.app.test_request_context("/"):
        cleared = m.purge_police_register(conn)
    conn.commit()
    s.check("and a Booking.com stay's fiche goes six months after it ended",
            not conn.execute("SELECT 1 FROM channel_police_register WHERE ota_reservation_id = ?",
                             (old_id,)).fetchone()
            and cleared.get("police register entries", 0) >= 1, detail=str(cleared))
    s.check("while a current one stays", len(_fiches()) == 1)

    s.section("A cancellation takes them off everything")
    conn.execute("UPDATE ota_reservations SET status = 'cancelled' WHERE reservation_number = '9876600001'")
    conn.commit()
    s.check("off who is here", f"Zoe {TAG} Hart" not in {g["name"] for g in m.guests_in_residence(conn, today)})
    with m.app.test_request_context("/"):
        board = {r["room"]["id"]: r for r in m.room_board(conn, today)}
    s.check("and the room is free again on the board", board.get(a["id"], {}).get("state") != "occupied")
    s.check("and off Today", f"Zoe {TAG} Hart" not in visible_text(oc.get("/today").get_data(as_text=True)))


if __name__ == "__main__":
    print(run().report())
