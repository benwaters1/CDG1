"""One sheet with every dietary note in the house for one day.

The notes existed. They were spread across four tables and five pages -- a
guest's profile, a restaurant reservation, an atelier registration, an event's
message -- so a chef planning tomorrow opened all of them and held the answer in
their head. That is how a nut allergy gets missed on the one night somebody is
busy.

Two things beyond "does it list them" are checked here, because both are ways a
sheet like this quietly becomes wrong:

  - THE DATE BOUNDARIES. A stay covers the night of arrival up to but not
    including the morning of departure, so a guest leaving on the day is not
    eating dinner on it. An atelier is inclusive at both ends, because somebody
    is here on the last day of a retreat and eats on it. Get either wrong and
    the sheet is confidently short by one party.
  - THE COVERS. A sheet listing three allergies and not the forty people eating
    is half a sheet, so heads are counted whether or not there is a note.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZKITCH"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", ("zzkitch%",))
    conn.commit()
    conn.close()


DAY = house_today() + timedelta(days=21)


def _stay(ref, *, arrive_offset, nights, party=2, requests=None, profile=None):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 AND max_occupancy >= ? "
                        "ORDER BY id LIMIT 1", (party,)).fetchone()
    arrival = DAY + timedelta(days=arrive_offset)
    email = f"zzkitch.{ref}@example.invalid".lower()
    if profile:
        conn.execute(
            "INSERT INTO guests (name, email, dietary_notes, created_at) VALUES (?, ?, ?, ?)",
            (f"{TAG} {ref}", email, profile, datetime.now(timezone.utc).isoformat()))
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, special_requests, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 'confirmed', 100, 0, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         email, arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         party, requests, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _dinner(ref, *, day_offset=0, party=2, dietary=None):
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, guest_phone, party_size, dinner_date, dietary_notes,
           status, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, ?, 'confirmed', ?)""",
        (f"{TAG}-R{ref}", f"tok{TAG}r{ref}".lower(), f"{TAG} {ref}",
         f"zzkitch.r{ref}@example.invalid", party,
         (DAY + timedelta(days=day_offset)).isoformat(), dietary,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _atelier(ref, *, start_offset, end_offset, party=1, dietary=None, medical=None):
    conn = db()
    workshop = conn.execute("SELECT id FROM workshops ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
           capacity, notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (workshop["id"], (DAY + timedelta(days=start_offset)).isoformat(),
         (DAY + timedelta(days=end_offset)).isoformat(), f"{TAG} session",
         datetime.now(timezone.utc).isoformat()))
    session_id = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ? "
                              "ORDER BY id DESC LIMIT 1", (f"{TAG} session",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, guest_phone, party_size, status,
           dietary_notes, medical_notes, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, 'confirmed', ?, ?, ?)""",
        (session_id, f"{TAG}-W{ref}", f"tok{TAG}w{ref}".lower(), f"{TAG} {ref}",
         f"zzkitch.w{ref}@example.invalid", party, dietary, medical,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _event(ref, *, day_offset=0, guests=60, message=None, owner_note=None):
    conn = db()
    kinds = m.known_event_types(conn)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, contact_phone, preferred_date, guest_count,
           message, owner_note, status, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, 'confirmed', ?)""",
        (f"{TAG}-E{ref}", f"tok{TAG}e{ref}".lower(), (kinds or ["wedding"])[0],
         f"{TAG} {ref}", f"zzkitch.e{ref}@example.invalid",
         (DAY + timedelta(days=day_offset)).isoformat(), guests, message,
         owner_note, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _sheet(day=None):
    conn = db()
    try:
        return m.kitchen_sheet(conn, day or DAY)
    finally:
        conn.close()


def _all_rows(sheet):
    return [r for sec in sheet["sections"] for r in sec["rows"]
            if TAG in (r["who"] or "")]


def _notes_for(sheet, who):
    for r in _all_rows(sheet):
        if r["who"] == who:
            return " | ".join(r["notes"])
    return None


def run():
    s = Suite("Kitchen sheet")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("It gathers notes from all four streams")
    _stay("STAY", arrive_offset=-1, nights=3, party=3, requests="No shellfish")
    _dinner("DIN", day_offset=0, party=4, dietary="One coeliac")
    _atelier("ATL", start_offset=-2, end_offset=2, party=2,
             dietary="Vegan", medical="Carries an EpiPen")
    _event("EVT", day_offset=0, guests=60, message="Two nut allergies in the party",
           owner_note="ZZ push the higher package")

    sheet = _sheet()
    s.check("the stay's note is on it", _notes_for(sheet, f"{TAG} STAY") is not None,
            detail="a guest's request sat on their booking and nowhere a chef looks")
    s.check("the dinner reservation's is too",
            "coeliac" in (_notes_for(sheet, f"{TAG} DIN") or ""),
            detail=f"{_notes_for(sheet, f'{TAG} DIN')!r}")
    s.check("the atelier's dietary note",
            "Vegan" in (_notes_for(sheet, f"{TAG} ATL") or ""),
            detail=f"{_notes_for(sheet, f'{TAG} ATL')!r}")
    s.check("and its medical note, which is the one that matters most",
            "EpiPen" in (_notes_for(sheet, f"{TAG} ATL") or ""),
            detail="a note about an EpiPen that the kitchen never sees")
    s.check("the event's message", "nut" in (_notes_for(sheet, f"{TAG} EVT") or ""),
            detail=f"{_notes_for(sheet, f'{TAG} EVT')!r}")

    s.section("But not the owner's private note")
    s.check("a commercial note is not printed in a kitchen",
            "push the higher package" not in (_notes_for(sheet, f"{TAG} EVT") or ""),
            detail="owner_note is where the owner writes what to push for and "
                   "what a competitor quoted; this sheet gets pinned up")

    s.section("A note on the guest's profile reaches the sheet too")
    _stay("PROFILE", arrive_offset=0, nights=2, party=2,
          profile="Lactose intolerant")
    s.check("the profile note is picked up",
            "Lactose" in (_notes_for(_sheet(), f"{TAG} PROFILE") or ""),
            detail="a preference recorded once, on the guest, and asked for again "
                   "every visit")

    s.section("The same note twice is not printed twice")
    _stay("DOUBLE", arrive_offset=0, nights=2, party=2,
          requests="No shellfish", profile="no shellfish")
    row = [r for r in _all_rows(_sheet()) if r["who"] == f"{TAG} DOUBLE"]
    s.check("it appears once", row and len(row[0]["notes"]) == 1,
            detail=f"{row[0]['notes'] if row else None} — a sheet that repeats "
                   "itself teaches people to skim it")

    s.section("Covers are counted whether or not anything is noted")
    # Measured as a DELTA. "covers >= 4" passed on whatever other suites had left
    # in the database, so it stayed green with the quiet party uncounted -- it was
    # asserting that the house had four people in it, not that this feature works.
    def _house_covers():
        for sec in _sheet()["sections"]:
            if sec["kind"] == "room":
                return sec["covers"]
        return 0

    before_covers = _house_covers()
    _stay("QUIET", arrive_offset=0, nights=2, party=4)
    sheet = _sheet()
    s.check("the quiet party of four is added to the covers",
            _house_covers() == before_covers + 4,
            detail=f"{before_covers} -> {_house_covers()} — three allergies and "
                   "not the forty people eating is half a sheet")
    s.check("and not in the flagged rows",
            f"{TAG} QUIET" not in [r["who"] for r in _all_rows(sheet)],
            detail="a row with nothing in the notes column is noise")
    s.check("the totals are the sum of the sections",
            sheet["covers"] == sum(sec["covers"] for sec in sheet["sections"]),
            detail=f"{sheet['covers']}")

    s.section("A guest leaving that morning is not eating that night")
    _cleanup()
    _stay("LEAVING", arrive_offset=-2, nights=2, party=2, requests="ZZ leaving")
    s.check("they are not on the sheet",
            not _all_rows(_sheet()),
            detail="a stay ending on the day was counted for dinner, so the "
                   "kitchen cooked for two people who had gone")
    _cleanup()
    _stay("ARRIVING", arrive_offset=0, nights=2, party=2, requests="ZZ arriving")
    s.check("but a guest arriving that day is",
            len(_all_rows(_sheet())) == 1,
            detail="a stay starting on the day was missed, so nobody cooked for "
                   "the two people who had just walked in")

    s.section("An atelier is inclusive at both ends")
    _cleanup()
    _atelier("LAST", start_offset=-3, end_offset=0, party=2, dietary="ZZ last day")
    s.check("somebody on the closing day is fed",
            len(_all_rows(_sheet())) == 1,
            detail="the last day of a retreat was treated as over, and the people "
                   "still in the house went hungry")
    _cleanup()
    _atelier("FIRST", start_offset=0, end_offset=3, party=2, dietary="ZZ first day")
    s.check("and so is somebody on the opening day",
            len(_all_rows(_sheet())) == 1)
    _cleanup()
    _atelier("AFTER", start_offset=5, end_offset=8, party=2, dietary="ZZ later")
    s.check("an atelier next week is not on today's sheet",
            not _all_rows(_sheet()))

    s.section("Only what is actually confirmed")
    _cleanup()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, special_requests, created_at)
           VALUES (?, ?, ?, ?, '', '', ?, ?, 2, 'pending', 100, 0, ?, ?)""",
        (room["id"], f"{TAG}-PEND", f"tok{TAG}pend", f"{TAG} PENDING",
         DAY.isoformat(), (DAY + timedelta(days=2)).isoformat(),
         "ZZ not confirmed", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    s.check("an unconfirmed enquiry is not cooked for",
            not _all_rows(_sheet()),
            detail="the kitchen bought for a booking nobody had accepted")

    s.section("The page itself")
    _cleanup()
    _stay("PAGE", arrive_offset=0, nights=2, party=2, requests="ZZ no shellfish")
    body = oc.get(f"/kitchen/sheet?day={DAY.isoformat()}").get_data(as_text=True)
    s.check("it opens on the day asked for", "ZZ no shellfish" in body,
            detail="the day parameter is ignored")
    s.check("the covers lead", "eating in the house" in body,
            detail="a chef needs the two numbers before any detail")
    s.check("there is a day-before and day-after link", "Day before" in body,
            detail="a sheet for one day with no way to reach tomorrow's")
    s.check("the furniture is marked not to print", "no-print" in body)
    s.check("with a rule behind it, or it prints anyway",
            "no-print{ display:none" in body,
            detail="the class is on the markup and does nothing")
    s.check("every table is wrapped for a phone",
            body.count("<table") == body.count('class="table-wrap"'),
            detail="a wide table drags the whole document sideways")

    s.section("A day with nobody on it says so")
    body = oc.get(f"/kitchen/sheet?day={(DAY + timedelta(days=400)).isoformat()}"
                  ).get_data(as_text=True)
    s.check("it can be empty", "Nobody is booked in" in body,
            detail="a page that can never be empty becomes furniture")

    s.section("Guards")
    s.check("an employee cannot read the house's medical notes",
            ec.get("/kitchen/sheet").status_code in (302, 403),
            detail="dietary and medical notes are the most sensitive thing the "
                   "app holds about a guest")
    s.check("a rubbish date does not 500",
            oc.get("/kitchen/sheet?day=not-a-date").status_code == 200)

    s.section("Nothing is stored, so the purge stays honest")
    # The privacy notice says dietary and medical notes are deleted once the
    # event is over. A sheet that cached them would make the notice false.
    conn = db()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()]
    conn.close()
    s.check("the sheet has no table of its own",
            not any("kitchen_sheet" in t for t in tables),
            detail="a cached copy outlives run_health_notes_purge_job and the "
                   "privacy notice stops being true")

    _cleanup()
    return s
