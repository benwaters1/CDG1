"""The pages a member of staff opens first, on a day with people in it.

WHAT WENT WRONG. Read on a seeded Thursday in the back-end review of 27
November 2026, every staff page as the owner and as an employee:

  Home (/)        the page an employee lands on after signing in offered
                  "Clock in" to somebody who had clocked in at 07:56 --
                  staff_dashboard worked out the open shift and never handed it
                  to the page. And it listed a room's checklist as eight rows,
                  each repeating "The Okafor family arriving 22 October 2026,
                  party of 4. Requests: ...": sixteen rows for two rooms. Today
                  and Tasks had been grouped on 22 October; Home was missed.
  Today           "Home screen" and "My list · 19" ran together, a dash comment
                  between the links having eaten the space. Everybody due today
                  read "Arrived today" from first thing in the morning, and a
                  stay's request was printed twice on its card.
  Hours           printed as decimals of an hour: 8h 07m was "8.12h", read as
                  8h 12m, and one week came out as 24.35 h, 24.4 hours and
                  24.36h on two pages. Rounded per shift to 0.01 of an hour, so
                  three shifts of 8h 07m added up to 24h 22m.
  My Hours        flagged the shift the person was standing in as "needs a
                  look -- tell whoever runs the rota".
  Time Off        offered Cancel on every approved holiday back to the summer,
                  and the route would have wiped taken leave off the record.
  Not linked      "When you cannot work" from anything an employee can open;
                  the fire roll call from anything but the owner's reports index.
  Dates           2026-10-22 on breakages, mileage, the restaurant waitlist,
                  cleaning rounds and the owner's queue of time off.
  Phone           the row's own button past the right edge on Cleaning Rounds,
                  Lost Property, Visitors, My Availability and Arrivals we
                  cannot prepare for.

Every check below was broken on purpose once and seen to fail.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZSTART"
ISO = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def _now(delta_h=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).isoformat()


def _main(html):
    hit = re.search(r"<main\b.*?</main>", html, re.S)
    return hit.group(0) if hit else html


def _cleanup(made):
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR room_note LIKE ?",
                 (f"%{TAG}%", f"%{TAG}%"))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM mileage_claims WHERE reason LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM breakages WHERE what LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM restaurant_waitlist WHERE name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM cleaning_rounds WHERE what LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM lost_property WHERE what LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM site_visitors WHERE name LIKE ?", (f"{TAG}%",))
    for entry_id in made.get("entries", []):
        conn.execute("DELETE FROM time_entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()


def _stay(conn, room, arrive, nights, name, requests=None, eta=None, checked_in=False):
    cur = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, total_price,
           created_at, source, special_requests, estimated_arrival_time,
           arrival_prepped_at, checked_in_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 600, ?, 'direct', ?, ?, ?, ?)""",
        (room["id"], f"{TAG}{_harness.secrets_token()[:6].upper()}", _harness.secrets_token(),
         name, f"{name.split()[-1].lower()}@example.invalid", arrive.isoformat(),
         (arrive + timedelta(days=nights)).isoformat(), _now(-24 * 9), requests, eta,
         _now(-24), _now(-1) if checked_in else None))
    return cur.lastrowid


def run():
    s = Suite("The pages staff open first")
    made = {}
    _cleanup(made)
    oc, ec, owner, emp = clients()
    today = house_today()
    room = ensure_room()
    with m.app.test_request_context("/"):
        clock_in, clock_out = m.url_for("clock_in_action"), m.url_for("clock_out_action")
        cannot = m.url_for("cannot_work")
        roll = m.url_for("roll_call")
    try:
        conn = db()
        # Clocked in at eight this morning, house time.
        start = datetime(today.year, today.month, today.day, 8, 0, tzinfo=m.LOCAL_TZ)
        cur = conn.execute("INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
                           (emp["id"], start.astimezone(timezone.utc).isoformat()))
        made.setdefault("entries", []).append(cur.lastrowid)
        # Three finished shifts of 07:58 to 16:05: 8h 07m each, 24h 21m together.
        for back in (1, 2, 3):
            day = today - timedelta(days=back)
            a = datetime(day.year, day.month, day.day, 7, 58, tzinfo=m.LOCAL_TZ)
            b = datetime(day.year, day.month, day.day, 16, 5, tzinfo=m.LOCAL_TZ)
            cur = conn.execute(
                "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
                (emp["id"], a.astimezone(timezone.utc).isoformat(),
                 b.astimezone(timezone.utc).isoformat()))
            made["entries"].append(cur.lastrowid)
        # Due today, not checked in, with a request; another due today and in.
        sentence = f"{TAG} Fairweather family arriving today, party of 4."
        due = _stay(conn, room, today, 3, f"{TAG} Fairweather family",
                    requests=f"{TAG} a travel cot please", eta="16:30")
        _stay(conn, room, today, 2, f"{TAG} Quennell", checked_in=True)
        for item in ("Wine bottle", "Fresh towels", "Hand sanitiser"):
            conn.execute(
                """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date,
                   status, created_at, origin, booking_id)
                   VALUES (?, ?, ?, 'high', ?, 'open', ?, 'checklist', ?)""",
                (emp["id"], f"{room['name']}: {item}", sentence, today.isoformat(), _now(-2),
                 due))
        conn.commit()
        conn.close()

        s.section("Home: the page an employee lands on")
        home = _main(ec.get("/").get_data(as_text=True))
        s.check("somebody clocked in is offered Clock out",
                f'action="{clock_out}"' in home, detail="no clock-out form on Home")
        s.check("and not Clock in", f'action="{clock_in}"' not in home)
        text = visible_text(home)
        s.check("a room's checklist is one heading",
                f"{room['name']}: get it ready" in text, detail=text[:300])
        s.check("its arrival sentence said once, not once a task",
                text.count(sentence) == 1, detail=f"{text.count(sentence)} times")
        s.check("weekly hours in hours and minutes", not re.search(r"\d+\.\d+h\b", text),
                detail=re.findall(r"\d+\.\d+h\b", text)[:3])

        s.section("Today")
        page = ec.get("/today").get_data(as_text=True)
        head = re.search(r'<div class="page-head">.*?</div>\s*</div>', page, re.S).group(0)
        # On the markup, not the text: visible_text puts a space at every tag,
        # so it reads "Home screen My list" even when a browser would not.
        s.check("the two links in the head are two words apart",
                re.search(r"Home screen</a>\s+<a ", head) is not None,
                detail=re.sub(r"\s+", " ", head)[:300])
        text = visible_text(_main(page))
        s.check("due today and not checked in: arriving, and when",
                f"{TAG} Fairweather family Arriving today · about 16:30" in text,
                detail=text[text.find("Fairweather"):][:120])
        s.check("checked in: arrived", f"{TAG} Quennell Arrived today" in text,
                detail=text[text.find("Quennell"):][:80])
        s.check("the request on the card is not printed again under it",
                text.count(f"{TAG} a travel cot please") == 1,
                detail=f"{text.count(TAG + ' a travel cot please')} times")

        s.section("Hours as people count them")
        hw = m.hours_words
        s.check("8.1167 is 8h 07m, not 8.12", hw(8 + 7 / 60) == "8h 07m", detail=hw(8 + 7 / 60))
        s.check("whole hours, minutes alone, nothing",
                (hw(8), hw(0.5), hw(None)) == ("8h", "30m", "—"),
                detail=str((hw(8), hw(0.5), hw(None))))
        s.check("a shift is stored in whole minutes",
                m.to_whole_minutes(8 + 7 / 60) * 60 == 487)
        mine = visible_text(_main(ec.get("/hours/mine?view=month").get_data(as_text=True)))
        s.check("My Hours: rows of 8h 07m", mine.count("8h 07m") >= 3, detail=mine[-400:])
        s.check("and no decimal of an hour on the page",
                not re.search(r"\b\d+\.\d+ ?(h|hours)\b", mine),
                detail=re.findall(r"\b\d+\.\d+ ?(?:h|hours)\b", mine)[:3])
        s.check("the shift in progress is on shift now", "on shift now" in mine)
        s.check("and not a clocking that needs a look",
                "needs a look." not in mine and "need a look." not in mine,
                detail=mine[mine.find("Needs a look") - 10:][:80])
        profile = visible_text(_main(oc.get(f"/directory/{emp['id']}").get_data(as_text=True)))
        s.check("the profile's week adds up to its rows: 24h 21m over three of 8h 07m",
                "24h 21m in the last" in profile,
                detail=profile[profile.find("Built from"):][:90])

        s.section("A clocking that really was left open is still a clocking that needs a look")
        conn = db()
        try:
            # One open clocking a person: today's goes, yesterday's never-closed one comes.
            conn.execute("DELETE FROM time_entries WHERE id = ?", (made["entries"][0],))
            old = datetime.now(timezone.utc) - timedelta(days=1, hours=2)
            cur = conn.execute("INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
                               (emp["id"], old.isoformat()))
            made["entries"].append(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        mine = visible_text(_main(ec.get("/hours/mine?view=month").get_data(as_text=True)))
        s.check("yesterday's open clocking: never clocked out, needs a look",
                "never clocked out" in mine and "a look." in mine,
                detail=mine[mine.find("Needs a look") - 10:][:80])

        s.section("Time Off: taken is a record")
        conn = db()
        past = conn.execute(
            """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type,
               status, requested_at) VALUES (?, ?, ?, ?, 'vacation', 'approved', ?)""",
            (emp["id"], (today - timedelta(days=40)).isoformat(),
             (today - timedelta(days=38)).isoformat(), f"{TAG} summer", _now(-24 * 60))).lastrowid
        ahead = conn.execute(
            """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type,
               status, requested_at) VALUES (?, ?, ?, ?, 'vacation', 'approved', ?)""",
            (emp["id"], (today + timedelta(days=40)).isoformat(),
             (today + timedelta(days=42)).isoformat(), f"{TAG} winter", _now(-24))).lastrowid
        conn.commit()
        conn.close()
        with m.app.test_request_context("/"):
            past_url = m.url_for("cancel_leave_request", request_id=past)
            ahead_url = m.url_for("cancel_leave_request", request_id=ahead)
        page = ec.get("/leave").get_data(as_text=True)
        s.check("no Cancel on time off already taken", f'action="{past_url}"' not in page)
        s.check("Cancel on time off still to come", f'action="{ahead_url}"' in page)
        s.check("what is still to come is listed before what is over",
                0 <= page.find(f"{TAG} winter") < page.find(f"{TAG} summer"))
        ec.post(past_url)
        conn = db()
        state = conn.execute("SELECT status FROM leave_requests WHERE id = ?",
                             (past,)).fetchone()["status"]
        conn.close()
        s.check("and the route will not cancel it either", state == "approved", detail=state)
        ec.post(ahead_url)
        conn = db()
        state = conn.execute("SELECT status FROM leave_requests WHERE id = ?",
                             (ahead,)).fetchone()["status"]
        conn.close()
        s.check("time off still to come can be called off", state == "cancelled", detail=state)

        s.section("Pages an employee could not reach")
        for path in ("/shifts/mine", "/availability", "/leave"):
            s.check(f"{path} links to When you cannot work",
                    f'href="{cannot}"' in ec.get(path).get_data(as_text=True))
        for who, client in (("an employee", ec), ("the owner", oc)):
            nav = re.search(r'<aside class="sidebar".*?</aside>',
                            client.get("/today").get_data(as_text=True), re.S).group(0)
            s.check(f"the fire roll call is in the sidebar for {who}", f'href="{roll}"' in nav)

        s.section("Dates in words where they only show with something in them")
        conn = db()
        conn.execute("INSERT INTO mileage_claims (user_id, travelled_on, from_place, to_place, "
                     "reason, kilometres, rate, amount, status, created_at) "
                     "VALUES (?, ?, 'Gudanes', 'Foix', ?, 64, 0.35, 22.4, 'pending', ?)",
                     (emp["id"], (today - timedelta(days=2)).isoformat(), f"{TAG} market",
                      _now()))
        conn.execute("INSERT INTO breakages (room_id, what, found_on, found_by_user_id, "
                     "replacement_cost, created_at) VALUES (?, ?, ?, ?, 12, ?)",
                     (room["id"], f"{TAG} wine glass", today.isoformat(), emp["id"], _now()))
        conn.execute("INSERT INTO restaurant_waitlist (name, email, desired_date, party_size, "
                     "status, created_at) VALUES (?, 'zzstart@example.invalid', ?, 3, 'open', ?)",
                     (f"{TAG} Garnier", today.isoformat(), _now()))
        conn.execute("INSERT INTO cleaning_rounds (what, area, every_days, last_done_on, active, "
                     "created_at) VALUES (?, 'Hall', 7, ?, 1, ?)",
                     (f"{TAG} sweep the stair", (today - timedelta(days=3)).isoformat(), _now()))
        conn.execute("INSERT INTO leave_requests (user_id, start_date, end_date, reason, "
                     "leave_type, status, requested_at) VALUES (?, ?, ?, ?, 'vacation', "
                     "'pending', ?)", (emp["id"], (today + timedelta(days=18)).isoformat(),
                                       (today + timedelta(days=22)).isoformat(),
                                       f"{TAG} wedding", _now()))
        conn.commit()
        conn.close()
        for label, path, client, near in (
                ("mileage", "/mileage", ec, f"{TAG} market"),
                ("breakages", "/management/breakages", oc, f"{TAG} wine glass"),
                ("restaurant waitlist", "/admin/restaurant/waitlist", oc, f"{TAG} Garnier"),
                ("cleaning rounds", "/management/cleaning", ec, f"{TAG} sweep the stair")):
            text = visible_text(_main(client.get(path).get_data(as_text=True)))
            at = text.find(near)
            window = text[max(0, at - 120):at + 160] if at >= 0 else ""
            s.check(f"{label}: no date as a stamp", at >= 0 and not ISO.search(window),
                    detail=window or "row not found")
        home = visible_text(_main(oc.get("/").get_data(as_text=True)))
        s.check("the owner's queue of time off in words",
                not ISO.search(home), detail=str(ISO.findall(home)[:3]))

        s.section("On a phone, the row's button is on the card")
        conn = db()
        conn.execute("INSERT INTO lost_property (what, found_on, found_where, found_by_user_id, "
                     "status, created_at) VALUES (?, ?, 'Under the bed', ?, 'held', ?)",
                     (f"{TAG} a blue cardigan", today.isoformat(), emp["id"], _now()))
        conn.execute("INSERT INTO site_visitors (name, company, reason, host_user_id, "
                     "signed_in_at, signed_in_by_user_id, created_at) "
                     "VALUES (?, 'Girard Chauffage', 'Boiler service', ?, ?, ?, ?)",
                     (f"{TAG} Paul Girard", emp["id"], _now(-1), emp["id"], _now(-1)))
        conn.commit()
        conn.close()
        for path, client in (("/management/cleaning", ec), ("/management/lost-property", ec),
                             ("/management/visitors", ec), ("/availability", ec),
                             ("/admin/arrivals-incomplete", oc)):
            page = _main(client.get(path).get_data(as_text=True))
            tables = re.findall(r'<table class="([^"]*)"', page)
            s.check(f"{path}: its table becomes cards on a phone",
                    any("phone-cards" in c for c in tables), detail=str(tables))
    finally:
        _cleanup(made)
    return s
