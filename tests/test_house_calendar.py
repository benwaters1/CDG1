"""The house calendar: one Microsoft 365 calendar the app writes and the whole
house reads, on desks and phones.

What it promises, each checked here against a stand-in for Microsoft Graph
(tests/_graph_double.py) -- nothing reaches Microsoft:

  IT IS THE APP'S OWN CALENDAR. Built from build_overview, the rows /calendar
  draws, so the two cannot disagree: one event per stay rather than an
  arrival and a departure, a dinner at dinner time, an atelier sitting as one
  event, a wedding on its days, the rota, the tasks -- and not the owner's own
  tasks, a cancelled stay or a finished task.

  IT HOLDS NAMES, ROOMS AND DATES. No address, no telephone, no money, no
  dietary or special-request text: the calendar is shared with everybody who
  works here. The privacy notice says so, and this is what makes it true.

  IT WRITES ONLY WHAT CHANGED, keyed on the app's own id, so an edit patches
  the same event, a cancellation removes it, and a pass with nothing new
  sends Microsoft nothing at all.

  A REFUSAL IS SAID, on the event, on the page and on the owner home -- and
  the line closes itself once the next pass writes cleanly.

  THE NIGHTLY RECONCILE puts back an event somebody deleted by hand, removes
  a stray, and undoes a hand edit.
"""
from datetime import timedelta

from _graph_double import FakeGraph
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZHC"
MAILBOX = "house@example.invalid"


def _cleanup(conn):
    conn.execute("DELETE FROM event_holds WHERE event_id IN (SELECT id FROM event_inquiries "
                 "WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM house_calendar_events")
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'house_calendar_%'")
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE 'house_calendar%'")
    conn.commit()


def _iso(d):
    return d.isoformat()


def run():
    s = Suite("the house calendar")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = m.house_today()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()

    def stay(ref, status, arrive, nights, party=2):
        cur = conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
                 guest_phone, arrival_date, departure_date, party_size, status, special_requests,
                 total_price, created_at)
               VALUES (?, ?, ?, ?, ?, '+33 6 12 34 56 78', ?, ?, ?, ?, 'allergic to feathers',
                       999, ?)""",
            (room["id"], f"{TAG}{ref}", f"{TAG}tok{ref}", f"{TAG} Guest {ref}",
             f"zzhc.{ref.lower()}@example.invalid", _iso(today + timedelta(days=arrive)),
             _iso(today + timedelta(days=arrive + nights)), party, status, now))
        return cur.lastrowid

    stay1 = stay("S1", "confirmed", 10, 3)
    stay2 = stay("S2", "pending", 12, 2)
    stay("S3", "cancelled", 14, 2)
    dinner = conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
             party_size, dinner_date, status, dietary_notes, total_price, created_at)
           VALUES (?, ?, ?, 'zzhc.dinner@example.invalid', 4, ?, 'confirmed', 'coeliac', 240, ?)""",
        (f"{TAG}D1", f"{TAG}dtok", f"{TAG} Diner", _iso(today + timedelta(days=11)), now)).lastrowid
    workshop = conn.execute("SELECT id, title FROM workshops ORDER BY id LIMIT 1").fetchone()
    session = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
             created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (workshop["id"], _iso(today + timedelta(days=20)), _iso(today + timedelta(days=24)),
         TAG, now)).lastrowid
    for ref, status, party in (("W1", "confirmed", 1), ("W2", "pending", 2)):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
                 guest_email, party_size, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session, f"{TAG}{ref}", f"{TAG}wtok{ref}", f"{TAG} Maker {ref}",
             f"zzhc.{ref.lower()}@example.invalid", party, status, now))
    wedding = conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
             contact_email, contact_phone, preferred_date, end_date, guest_count, status,
             quoted_price, created_at)
           VALUES (?, ?, 'wedding', ?, 'zzhc.bride@example.invalid', '0612345678', ?, ?, 80,
                   'confirmed', 18000, ?)""",
        (f"{TAG}E1", f"{TAG}etok", f"{TAG} Bride", _iso(today + timedelta(days=30)),
         _iso(today + timedelta(days=31)), now)).lastrowid
    held_event = conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
             contact_email, guest_count, status, created_at)
           VALUES (?, ?, 'private_party', ?, 'zzhc.party@example.invalid', 30, 'quoted', ?)""",
        (f"{TAG}E2", f"{TAG}etok2", f"{TAG} Host", now)).lastrowid
    hold = conn.execute(
        """INSERT INTO event_holds (event_id, start_date, end_date, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (held_event, _iso(today + timedelta(days=40)), _iso(today + timedelta(days=41)),
         (m.datetime.now(m.timezone.utc) + timedelta(days=7)).isoformat(), now)).lastrowid
    task_open = conn.execute(
        """INSERT INTO tasks (title, due_date, status, assigned_to_user_id, created_at)
           VALUES (?, ?, 'open', ?, ?)""",
        (f"{TAG} polish the brass", _iso(today + timedelta(days=5)), emp["id"], now)).lastrowid
    task_done = conn.execute(
        """INSERT INTO tasks (title, due_date, status, assigned_to_user_id, created_at)
           VALUES (?, ?, 'done', ?, ?)""",
        (f"{TAG} already done", _iso(today + timedelta(days=5)), emp["id"], now)).lastrowid
    task_owner = conn.execute(
        """INSERT INTO tasks (title, due_date, status, assigned_to_user_id, created_at)
           VALUES (?, ?, 'open', ?, ?)""",
        (f"{TAG} the owner's own", _iso(today + timedelta(days=5)), owner["id"], now)).lastrowid
    shift_day = conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at)
           VALUES (?, ?, '08:00', '16:00', ?, ?)""",
        (emp["id"], _iso(today + timedelta(days=6)), f"{TAG} breakfast", now)).lastrowid
    shift_night = conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at)
           VALUES (?, ?, '22:00', '06:00', ?, ?)""",
        (owner["id"], _iso(today + timedelta(days=7)), f"{TAG} night porter", now)).lastrowid
    conn.commit()

    start, end = m.house_calendar_window()
    with m.app.test_request_context("/"):
        items = m.house_calendar_items(conn, start, end)
    by_key = {i["key"]: i for i in items}

    s.section("Built from the app calendar's own rows")
    s1 = by_key.get(f"stay:{stay1}")
    s.check("a stay is one event, not an arrival and a departure",
            s1 is not None and sum(1 for i in items if i["key"] == f"stay:{stay1}") == 1,
            detail=str([k for k in by_key if k.startswith("stay:")])[:200])
    s.check("from check-in on the day they arrive",
            s1 and s1["start"] == f"{_iso(today + timedelta(days=10))}T16:00:00",
            detail=s1 and s1["start"])
    s.check("to check-out on the day they leave",
            s1 and s1["end"] == f"{_iso(today + timedelta(days=13))}T11:00:00",
            detail=s1 and s1["end"])
    s.check("with the guest, how many, and the room",
            s1 and f"{TAG} Guest S1" in s1["subject"] and "×2" in s1["subject"]
            and room["name"] in s1["subject"], detail=s1 and s1["subject"])
    s2 = by_key.get(f"stay:{stay2}")
    s.check("a request not yet confirmed says so",
            s2 and "to confirm" in s2["subject"], detail=s2 and s2["subject"])
    s.check("a cancelled stay is not on it",
            not any(f"{TAG} Guest S3" in i["subject"] for i in items))
    dn = by_key.get(f"dinner:{dinner}")
    settings = m.get_restaurant_settings(conn)
    at = m._clock(settings["dinner_time"] if settings else "") or "19:30"
    s.check("a dinner is at dinner time, not all day",
            dn and not dn["all_day"] and dn["start"].endswith(f"T{at}:00"),
            detail=dn and dn["start"])
    ses = by_key.get(f"session:{session}")
    s.check("an atelier sitting is one event, with who is coming counted",
            ses and "2 booked, 3 people" in ses["subject"] and "1 to confirm" in ses["subject"],
            detail=ses and ses["subject"])
    s.check("over its days, the last one included",
            ses and ses["end"] == f"{_iso(today + timedelta(days=25))}T00:00:00",
            detail=ses and ses["end"])
    wd = by_key.get(f"event:{wedding}")
    s.check("a confirmed wedding is on its days, all day",
            wd and wd["all_day"] and wd["start"].startswith(_iso(today + timedelta(days=30)))
            and wd["end"].startswith(_iso(today + timedelta(days=32))),
            detail=wd and (wd["start"], wd["end"]))
    hd = by_key.get(f"hold:{hold}")
    s.check("dates held for an enquiry show, marked as held",
            hd and hd["subject"].startswith("🥂 Held:"), detail=hd and hd["subject"])
    s.check("an open task is on it", f"task:{task_open}" in by_key)
    s.check("a finished one is not", f"task:{task_done}" not in by_key)
    s.check("and the owner's own tasks are not",
            f"task:{task_owner}" not in by_key,
            detail="the calendar is shared with everybody; the owner's lane is theirs")
    sd = by_key.get(f"shift:{shift_day}")
    s.check("a shift is at its hours",
            sd and sd["start"].endswith("T08:00:00") and sd["end"].endswith("T16:00:00"),
            detail=sd and (sd["start"], sd["end"]))
    sn = by_key.get(f"shift:{shift_night}")
    s.check("a shift past midnight ends the next morning",
            sn and sn["end"] == f"{_iso(today + timedelta(days=8))}T06:00:00",
            detail=sn and sn["end"])

    s.section("Names, rooms and dates — nothing else about a guest")
    mine = [i for i in items if TAG in i["subject"] or TAG in (i["body"] or "")]
    text = "\n".join(f"{i['subject']}\n{i['body']}\n{i.get('location', '')}" for i in mine)
    for leak, what in (("@example.invalid", "an email address"), ("12 34 56", "a telephone"),
                       ("0612345678", "a telephone"), ("feathers", "a special request"),
                       ("coeliac", "a dietary note"), ("999", "a price"),
                       ("18000", "a quote"), ("€", "money")):
        s.check(f"no {what}", leak not in text, detail=f"{leak!r} is in the calendar text")
    with m.app.test_request_context("/"):
        bodies = [m.graph_event_body(i) for i in mine]
    s.check("and what Microsoft is sent is that same text",
            not any(leak in str(b) for b in bodies
                    for leak in ("@example.invalid", "feathers", "coeliac")))

    s.section("Written to Microsoft 365, and only what changed")
    fake = FakeGraph(calendar_mailbox=MAILBOX)
    with fake, m.app.test_request_context("/"):
        first = m.sync_house_calendar(conn)
        made = [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/calendars")]
        s.check("the calendar is made, under the house's name",
                len(made) == 1 and made[0][2]["name"] == m.HOUSE_CALENDAR_NAME,
                detail=str(made)[:200])
        cal_id = m._hc_get(conn, "id")
        events = fake.calendars[cal_id]["events"] if cal_id in fake.calendars else {}
        s.check("every event the app calendar has is written",
                first["created"] == len(items) and len(events) == len(items),
                detail=f"{first} for {len(items)} item(s)")
        ev = next((e for e in events.values() if f"{TAG} Guest S1" in e["subject"]), None)
        s.check("in the house's time zone",
                ev and ev["start"]["timeZone"] == m.graph_house_timezone() == "Romance Standard Time",
                detail=ev and ev["start"])
        s.check("carrying the app's own key, for the nightly check",
                ev and ev["singleValueExtendedProperties"][0]["value"] == f"stay:{stay1}")
        s.check("filed under its area",
                ev and ev["categories"] == ["Gudanes · Rooms"], detail=ev and ev["categories"])
        s.check("and not blocking anybody's own diary", ev and ev["showAs"] == "free")
        s.check("the area colours are put into the mailbox",
                set(fake.categories) >= {m.house_category(a[0]) for a in m.HOUSE_CALENDAR_AREAS},
                detail=str(fake.categories))

        fake.calls.clear()
        again = m.sync_house_calendar(conn)
        s.check("a second pass with nothing new sends Microsoft nothing",
                not fake.writes() and again["unchanged"] == len(items),
                detail=f"{again}; {len(fake.writes())} write(s)")

        conn.execute("UPDATE bookings SET party_size = 3 WHERE id = ?", (stay1,))
        conn.commit()
        fake.calls.clear()
        changed = m.sync_house_calendar(conn)
        patches = fake.writes("PATCH")
        s.check("an edit patches the same event, once",
                changed["updated"] == 1 and len(patches) == 1 and not fake.writes("POST"),
                detail=f"{changed}")
        s.check("with the new detail",
                patches and "×3" in patches[0][2]["subject"], detail=patches and patches[0][2]["subject"])

        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (stay2,))
        conn.commit()
        fake.calls.clear()
        gone = m.sync_house_calendar(conn)
        s.check("a cancellation removes the event",
                gone["removed"] == 1 and len(fake.writes("DELETE")) == 1,
                detail=f"{gone}")
        s.check("and the app forgets it",
                not conn.execute("SELECT 1 FROM house_calendar_events WHERE key = ?",
                                 (f"stay:{stay2}",)).fetchone())

        s.section("A refusal is said, and closes itself")
        conn.execute("UPDATE tasks SET title = ? WHERE id = ?",
                     (f"{TAG} polish the brass REFUSEME", task_open))
        conn.commit()
        fake.refuse_subjects.add("REFUSEME")
        bad = m.sync_house_calendar(conn)
        row = conn.execute("SELECT * FROM house_calendar_events WHERE key = ?",
                           (f"task:{task_open}",)).fetchone()
        s.check("the refused event is counted", bad["failed"] == 1, detail=f"{bad}")
        s.check("with Microsoft's own words on it",
                row and "refused by the double" in (row["last_error"] or ""),
                detail=row and row["last_error"])
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
        line = [w for w in warned if "house calendar" in w["title"]]
        s.check("the owner home says so",
                line and "not on the house calendar" in line[0]["title"], detail=str(line)[:200])
        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("and the calendar page lists it, with the reason",
                "REFUSEME" in page and "refused by the double" in page)
        fake.refuse_subjects.clear()
        ok = m.sync_house_calendar(conn)
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
        s.check("the next clean pass writes it",
                ok["updated"] + ok["created"] >= 1 and not ok["failed"], detail=f"{ok}")
        s.check("and the line on the owner home goes by itself",
                not [w for w in warned if "house calendar" in w["title"]],
                detail=str([w["title"] for w in warned])[:200])

        s.section("The nightly check against Microsoft's copy")
        events = fake.calendars[cal_id]["events"]
        victim = next(eid for eid, e in events.items() if f"{TAG} Bride" in e["subject"])
        del events[victim]
        edited = next(eid for eid, e in events.items() if f"{TAG} Guest S1" in e["subject"])
        events[edited]["subject"] = "Changed by hand in Outlook"
        events["stray-1"] = {"subject": "a duplicate from a retried create",
                             "start": {"dateTime": "2026-01-01T00:00:00"},
                             "end": {"dateTime": "2026-01-02T00:00:00"},
                             "singleValueExtendedProperties": [
                                 {"id": m.HOUSE_EVENT_KEY_PROP, "value": "stay:999999"}]}
        events["by-hand"] = {"subject": "Somebody's own note", "start": {"dateTime": "2026-01-01T00:00:00"},
                             "end": {"dateTime": "2026-01-02T00:00:00"}}
        full = m.sync_house_calendar(conn, full=True)
        s.check("an event deleted by hand is put back",
                any(f"{TAG} Bride" in e["subject"] for e in events.values()), detail=f"{full}")
        s.check("a hand edit is undone",
                f"{TAG} Guest S1" in events.get(edited, {}).get("subject", ""),
                detail=events.get(edited, {}).get("subject"))
        s.check("a stray of the app's own is removed", "stray-1" not in events)
        s.check("and somebody's own event is left alone", "by-hand" in events,
                detail="only events carrying the app's key are the app's to remove")
        s.check("and the check is recorded", bool(m._hc_get(conn, "last_reconcile")))

        s.section("When Microsoft refuses the calendar itself")
        m._hc_set(conn, "id", "")
        conn.commit()
        fake.refuse_mailboxes[MAILBOX] = 403
        try:
            m.sync_house_calendar(conn)
            raised = None
        except m.HouseCalendarError as e:
            raised = str(e)
        s.check("it raises Microsoft's own words", raised and "Access to OData" in raised,
                detail=str(raised))
        r = oc.post("/admin/house-calendar/sync", follow_redirects=True)
        s.check("Sync now says so", "Microsoft refused" in r.get_data(as_text=True), response=r)
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
        s.check("and the owner home has it as a blocker",
                any(w["title"] == "The house calendar is not updating" and w["severity"] == "blocker"
                    for w in warned), detail=str([w["title"] for w in warned])[:200])
        del fake.refuse_mailboxes[MAILBOX]
        m._hc_set(conn, "id", cal_id)
        conn.commit()
        r = oc.post("/admin/house-calendar/sync", follow_redirects=True)
        s.check("Sync now runs", "Synced" in r.get_data(as_text=True), response=r)
        r = oc.post("/admin/house-calendar/sync", data={"full": "1"}, follow_redirects=True)
        s.check("and the check against Microsoft's copy can be run by hand",
                "Checked against Microsoft" in r.get_data(as_text=True), response=r)
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
        s.check("the blocker goes once a pass works",
                not any("house calendar" in w["title"] for w in warned))

        s.section("The thirty-second pass")
        conn.execute("DELETE FROM automation_runs WHERE job_name = 'house_calendar_sync'")
        conn.commit()
        counts = m.house_calendar_tick()
        s.check("it runs on its own clock", counts is not None and not counts["failed"],
                detail=str(counts))
        s.check("and not twice inside its interval", m.house_calendar_tick() is None)
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES "
                     "('automation_house_calendar_enabled', '0')")
        conn.execute("DELETE FROM automation_runs WHERE job_name = 'house_calendar_sync'")
        conn.commit()
        s.check("switched off under Automation, it does nothing", m.house_calendar_tick() is None)
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES "
                     "('automation_house_calendar_enabled', '1')")
        conn.commit()

        s.section("Choosing what it carries")
        r = oc.post("/admin/house-calendar/areas", data={"areas": ["rooms", "events"]},
                    follow_redirects=True)
        s.check("the choice is saved", "now carries: Rooms, Events" in r.get_data(as_text=True),
                response=r)
        trimmed = m.sync_house_calendar(conn)
        left = {e["categories"][0] for e in fake.calendars[cal_id]["events"].values()
                if e.get("categories")}
        s.check("and the other areas leave the calendar on the next pass",
                left <= {"Gudanes · Rooms", "Gudanes · Events"} and trimmed["removed"] > 0,
                detail=f"{left}; {trimmed}")
        r = oc.post("/admin/house-calendar/areas", data={}, follow_redirects=True)
        s.check("an empty choice is refused rather than emptying the calendar",
                "at least one area" in r.get_data(as_text=True))
        oc.post("/admin/house-calendar/areas", data={"areas": m.HOUSE_CALENDAR_AREA_KEYS})

        s.section("Sharing it with everybody")
        r = oc.post("/admin/house-calendar/share", follow_redirects=True)
        perm = fake.permissions[cal_id][0]
        s.check("the organisation's entry becomes read", perm["role"] == "read",
                detail=str(perm))
        s.check("and the page says so", "everybody in the organisation can now read"
                in r.get_data(as_text=True), response=r)
        r = oc.post("/admin/house-calendar/share", follow_redirects=True)
        s.check("a second press says it already is",
                "already shared" in r.get_data(as_text=True), response=r)

        s.section("Sharing a calendar Microsoft lists no organisation entry for")
        # A shared mailbox, as the house's is: Graph listed only the owner,
        # and the button failed with nothing anybody could do about it.
        import html as _html
        owner_entry = {"id": "owner", "role": "write", "isRemovable": False,
                       "isInsideOrganization": True,
                       "emailAddress": {"name": "House", "address": MAILBOX}}
        m._hc_set(conn, "shared", "")
        m._hc_set(conn, "shared_by_hand", "")
        conn.commit()
        fake.permissions[cal_id] = [dict(owner_entry)]
        # The message, not the page: the page carries the same line in its
        # folded help, so reading the page would pass with the message bare.
        said = _html.unescape(" ".join(_harness.flashes(
            oc.post("/admin/house-calendar/share", follow_redirects=True))))
        s.check("the button says what to run instead",
                "Set-MailboxFolderPermission" in said
                and "-User Default -AccessRights Reviewer" in said, detail=said[-600:])
        s.check("naming this calendar in this mailbox",
                f'"{MAILBOX}:' + chr(92) + "Calendar" + chr(92) + m.HOUSE_CALENDAR_NAME + '"' in said)
        s.check("and the owner's own entry is not taken for the organisation's",
                fake.permissions[cal_id][0]["role"] == "write")
        r = oc.post("/admin/house-calendar/shared-by-hand", follow_redirects=True)
        s.check("saying it was done by hand is recorded",
                "shared with everybody, by hand" in r.get_data(as_text=True), response=r)
        r = oc.post("/admin/house-calendar/share", follow_redirects=True)
        told = " ".join(_harness.flashes(r))
        s.check("and Share then says it is shared, rather than failing",
                "shared by hand in Exchange" in told
                and "lists no organisation-wide entry" not in told, detail=told)
        s.check("the page's Shared row says by hand, and by whom",
                f"by hand in Exchange (Default: Reviewer), recorded by {owner['name']}"
                in _html.unescape(oc.get("/admin/house-calendar").get_data(as_text=True)))
        # A mailbox set to French names the entry "Mon organisation".
        m._hc_set(conn, "shared_by_hand", "")
        conn.commit()
        fake.permissions[cal_id] = [dict(owner_entry), {
            "id": "orgfr", "role": "freeBusyRead", "isRemovable": False,
            "isInsideOrganization": True, "emailAddress": {"name": "Mon organisation"}}]
        oc.post("/admin/house-calendar/share")
        s.check("an organisation entry named in another language is still found, and made read",
                fake.permissions[cal_id][1]["role"] == "read"
                and fake.permissions[cal_id][0]["role"] == "write",
                detail=str(fake.permissions[cal_id]))

        s.section("A sync says what the app had to give it")
        s.check("nothing in range is said as that, not as 'nothing to change'",
                "nothing in range" in m.house_calendar_counts_words({"in_range": 0}))
        s.check("and everything already there is said as that",
                m.house_calendar_counts_words({"in_range": 3, "unchanged": 3})
                == "nothing to change (3 up to date)")
        with m.app.test_request_context("/"):
            had = len(m.house_calendar_items(conn, *m.house_calendar_window(),
                                             m.house_calendar_areas(conn)))
        m.sync_house_calendar(conn)
        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("the count is kept on each pass", m._hc_get(conn, "in_range") == str(had),
                detail=f"{m._hc_get(conn, 'in_range')} vs {had}")
        s.check("and the page says it beside what is on the calendar",
                had > 0 and f"The app had {had} events for it on the last pass" in page)

    s.section("Not set up, it says what is missing")
    r = oc.post("/admin/house-calendar/sync", follow_redirects=True)
    s.check("Sync now names the missing settings",
            "MS_CALENDAR_CLIENT_ID" in r.get_data(as_text=True), response=r)
    r = oc.post("/admin/house-calendar/share", follow_redirects=True)
    s.check("and so does Share", "Set the calendar up first" in r.get_data(as_text=True))
    page = oc.get("/admin/house-calendar")
    s.check("the page renders, with the setup steps",
            page.status_code == 200 and "New-ApplicationAccessPolicy" in page.get_data(as_text=True))
    s.check("and is the owner's", ec.get("/admin/house-calendar").status_code in (302, 403))

    s.section("The .ics link")
    anon = m.app.test_client()
    s.check("with no key made, there is nothing there",
            anon.get("/calendar/house.ics?token=anything").status_code == 404)
    r = oc.post("/admin/house-calendar/feed-link", follow_redirects=True)
    s.check("the owner makes the links", "New links made" in r.get_data(as_text=True), response=r)
    key = m._hc_get(conn, "ics_token")
    r = anon.get(f"/calendar/house.ics?token={key}")
    body = r.get_data(as_text=True)
    s.check("the feed answers as a calendar",
            r.status_code == 200 and r.content_type.startswith("text/calendar"),
            detail=f"{r.status_code} {r.content_type}")
    s.check("with the stay in it", f"{TAG} Guest S1" in body)
    s.check("every line folded to 75 octets",
            all(len(line.encode("utf-8")) <= 75 for line in body.split("\r\n")))
    rota = anon.get(f"/calendar/house.ics?token={key}&area=rota").get_data(as_text=True)
    s.check("one area on its own", "breakfast" in rota and f"{TAG} Guest S1" not in rota)
    s.check("the wrong key is a 404", anon.get("/calendar/house.ics?token=wrong").status_code == 404)
    oc.post("/admin/house-calendar/feed-link")
    s.check("and a new key stops the old link",
            anon.get(f"/calendar/house.ics?token={key}").status_code == 404)

    s.section("The app's own calendar shows the same things")
    page = oc.get(f"/calendar?view=month&date={_iso(today + timedelta(days=30))}").get_data(as_text=True)
    s.check("the wedding is on /calendar", f"{TAG} Bride" in page)
    s.check("with its own filter", 'data-filter-lane="event"' in page)
    page = oc.get(f"/calendar?view=week&date={_iso(today + timedelta(days=6))}").get_data(as_text=True)
    s.check("and the rota", f"{emp['name']} on 08:00–16:00" in page and 'data-filter-lane="shift"' in page)
    mine_page = ec.get(f"/calendar?view=week&date={_iso(today + timedelta(days=7))}").get_data(as_text=True)
    s.check("a colleague sees their own shifts and not the owner's",
            f"{owner['name']} on 22:00" not in mine_page)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
