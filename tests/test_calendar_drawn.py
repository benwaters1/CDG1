"""The calendar as drawn: what lasts as a bar, the day's things under headings,
and a way to add to it from the day you are looking at.

The owner, 19 October 2026: the calendar was "difficult to read, difficult to
add items". Each day was a heap of look-alike chips in no order -- every night
of every stay, every person on the rota, every dinner and every job -- so a
week with five rooms let showed thirty "in residence" chips and the one job
that mattered was somewhere among them. And nothing could be added from it at
all: a job went on through the Tasks page, a date through the House calendar
page, a shift through the rota, and none of them knew which day had been
looked at.

The rows underneath are the same rows as ever (test_calendar_every_day holds
them to every day they cover); this holds the drawing:

  - a stay is ONE bar, from the afternoon it arrives to the morning it leaves,
    on a grid of fourteen half-days; an event, a sitting and a date for the
    house cover whole days; a closure ends the day before it is back on sale;
  - what starts before the week or runs past it says so;
  - each day's one-day things are under a heading -- the restaurant with its
    covers, extras, jobs, the rota -- and the day says who arrives and leaves;
  - in a month the dinners and the rota are counted rather than listed;
  - the day view lists the stays as arriving, leaving and staying on;
  - the Add dialog is on every page with a "+" on every day, posting to the
    pages that have always made those things and coming back to the calendar;
  - staff may add a job of their own, and nothing else.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZDRAW"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM house_dates WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _quiet_monday(conn):
    """A Monday a year or more ahead whose week has nothing else in it."""
    day = house_today() + timedelta(days=400)
    day -= timedelta(days=day.weekday())
    for _ in range(80):
        a, z = day.isoformat(), (day + timedelta(days=7)).isoformat()
        busy = (conn.execute("""SELECT 1 FROM bookings WHERE status IN ('pending','confirmed')
                                AND arrival_date < ? AND departure_date > ?""", (z, a)).fetchone()
                or m.sessions_holding_the_house(conn, a, z)
                or conn.execute("SELECT 1 FROM room_blocks WHERE start_date < ? AND end_date > ?",
                                (z, a)).fetchone())
        if not busy:
            return day
        day += timedelta(days=7)
    return day


def _stay(conn, room_id, key, arrive, leave, name):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, ?)""",
        (room_id, f"{TAG}-{key}", f"tok{TAG}{key}".lower(), name, f"{TAG.lower()}@example.invalid",
         arrive.isoformat(), leave.isoformat(), _now()))
    return conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}-{key}",)).fetchone()["id"]


def run():
    s = Suite("the calendar drawn: bars, headings, and adding from a day")
    oc, ec, owner, emp = clients()
    _cleanup()
    try:
        conn = db()
        mon = _quiet_monday(conn)
        tue, wed, thu, fri, sun = (mon + timedelta(days=d) for d in (1, 2, 3, 4, 6))
        rooms = [r["id"] for r in conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 3")]
        mid = _stay(conn, rooms[0], "MID", tue, fri, f"{TAG} Middle Guest")
        before = _stay(conn, rooms[1], "BEF", mon - timedelta(days=2), wed, f"{TAG} Early Guest")
        after = _stay(conn, rooms[2], "AFT", sun, sun + timedelta(days=3), f"{TAG} Late Guest")
        for i, party in enumerate((2, 6)):
            conn.execute(
                """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
                   party_size, dinner_date, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?)""",
                (f"{TAG}R{i}", f"tok{TAG}r{i}", f"{TAG} Diner {i}", f"{TAG.lower()}@example.invalid",
                 party, wed.isoformat(), _now()))
        for i in range(5):
            conn.execute("""INSERT INTO tasks (assigned_to_user_id, title, due_date, priority, status,
                            created_at) VALUES (?, ?, ?, ?, 'open', ?)""",
                         (emp["id"], f"{TAG} job {i}", wed.isoformat(), "high" if i == 4 else "normal", _now()))
        conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at)
                        VALUES (?, ?, '08:00', '14:00', ?, ?)""", (emp["id"], wed.isoformat(), f"{TAG} rooms", _now()))
        conn.execute("""INSERT INTO house_dates (title, start_date, end_date, created_by, created_at)
                        VALUES (?, ?, ?, ?, ?)""", (f"{TAG} Market days", wed.isoformat(), thu.isoformat(),
                                                    owner["id"], _now()))
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, guest_count, status, created_at)
               VALUES (?, ?, 'wedding', ?, ?, ?, 40, 'confirmed', ?)""",
            (f"{TAG}-EV", f"tok{TAG}ev", f"{TAG} Couple", f"{TAG.lower()}@example.invalid",
             thu.isoformat(), _now()))
        conn.commit()
        as_owner = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
        with m.app.test_request_context("/"):
            week = m.build_calendar(conn, "week", mon, viewer=as_owner)
            month = m.build_calendar(conn, "month", mon, viewer=as_owner)
            day = m.build_calendar(conn, "day", wed, viewer=as_owner)
        conn.close()

        s.section("What lasts is one bar")
        bars = {b["uid"]: b for b in week["rows_of_days"][0]["bars"]}
        b = bars.get(f"booking-{mid}")
        s.check("a stay Tuesday to Friday runs from Tuesday afternoon to Friday morning",
                b and (b["col_start"], b["col_end"]) == (4, 10) and b["half"],
                detail=str(b and (b["col_start"], b["col_end"])))
        s.check("saying whose, which room and three nights",
                b and b["label"] == f"{TAG} Middle Guest" and "3 nights" in b["title"], detail=str(b and b["title"]))
        b = bars.get(f"booking-{before}")
        s.check("one that began before the week starts at its edge and says so",
                b and b["col_start"] == 1 and b["from_before"] and b["col_end"] == 6,
                detail=str(b and (b["col_start"], b["col_end"], b["from_before"])))
        b = bars.get(f"booking-{after}")
        s.check("one arriving on the Sunday runs off the end and says so",
                b and (b["col_start"], b["col_end"]) == (14, 15) and b["goes_on"],
                detail=str(b and (b["col_start"], b["col_end"])))
        ev = next((b for b in bars.values() if b["kind"] == "event"), None)
        s.check("a one-day wedding covers its whole day", ev and (ev["col_start"], ev["col_end"]) == (7, 9),
                detail=str(ev and (ev["col_start"], ev["col_end"])))
        dt = next((b for b in bars.values() if b["kind"] == "date"), None)
        s.check("a date for the house covers Wednesday and Thursday",
                dt and (dt["col_start"], dt["col_end"]) == (5, 9), detail=str(dt and (dt["col_start"], dt["col_end"])))
        s.check("events come before stays, so the big things sit on top",
                list(bars.values()).index(ev) < list(bars.values()).index(bars[f"booking-{mid}"]))
        page = oc.get(f"/calendar?view=week&date={mon.isoformat()}").get_data(as_text=True)
        s.check("the page draws the stay once, as a bar across its days",
                page.count(f'data-card="booking-{mid}"') == 1
                and 'style="grid-column:4 / 10;"' in page)
        s.check("it no longer draws a chip for each night in residence",
                "in residence" not in page.split('id="cal-cards"')[0])

        s.section("Each day's things under headings")
        cell = {c["iso"]: c for c in week["cells"]}[wed.isoformat()]
        keys = [sec["key"] for sec in cell["sections"]]
        s.check("the restaurant, the jobs and the rota, in that order",
                keys == ["restaurant", "task", "shift"], detail=str(keys))
        rest = cell["sections"][0]
        s.check("the restaurant counts its covers", rest["count"] == "8 covers", detail=rest["count"])
        jobs = next(sec for sec in cell["sections"] if sec["key"] == "task")
        s.check("the urgent job comes first", jobs["things"][0]["title"] == f"{TAG} job 4",
                detail=str([e["title"] for e in jobs["things"]]))
        s.check("and the day says who arrives, who leaves and the rooms let",
                cell["leave"] == 1 and cell["let"] == 1, detail=f"{cell['arrive']} {cell['leave']} {cell['let']}")
        s.check("the page heads the groups", "Restaurant" in page and "To do" in page and "On the rota" in page)
        mpage = oc.get(f"/calendar?view=month&date={mon.isoformat()}").get_data(as_text=True)
        s.check("a month counts the dinners rather than listing them",
                '<div class="cal2-folded">' in mpage and "8 covers" in mpage)
        s.check("and lists three jobs a day, with the rest a click away",
                "+2 more" in mpage)

        s.section("The day view")
        dcell = day["cells"][0]
        groups = {g["label"]: [e["title"] for e in g["things"]] for g in dcell["house"]}
        s.check("lists the stays as leaving and staying on, by name",
                any(f"{TAG} Early Guest" in t for t in groups.get("Leaving", []))
                and any(f"{TAG} Middle Guest" in t for t in groups.get("Staying on", [])),
                detail=str(groups))
        dpage = oc.get(f"/calendar?view=day&date={wed.isoformat()}").get_data(as_text=True)
        s.check("under its headings", "Leaving" in dpage and "Staying on" in dpage)

        s.section("Adding from the calendar")
        s.check("there is a + on every day of the week",
                all(f'data-add-date="{(mon + timedelta(days=i)).isoformat()}"' in page for i in range(7)))
        s.check("the owner's dialog posts a job, a date and a shift to their own pages",
                'action="/admin/tasks/new"' in page and 'action="/admin/house-calendar/dates"' in page
                and 'action="/admin/shifts/new"' in page and 'name="back_calendar" value="week"' in page)
        s.check("and leads to a stay, an event and a table for the day",
                "/admin/bookings/walk-in?arrival_date={day}" in page.replace("&amp;", "&")
                and "/admin/events?add=1&date={day}" in page.replace("&amp;", "&"))
        r = oc.post("/admin/tasks/new", data={"title": f"{TAG} from the calendar", "assigned_to_user_id": str(emp["id"]),
                                              "due_date": wed.isoformat(), "priority": "normal"},
                    headers={"Referer": f"/calendar?view=week&date={mon.isoformat()}"})
        conn = db()
        made = conn.execute("SELECT * FROM tasks WHERE title = ?", (f"{TAG} from the calendar",)).fetchone()
        conn.close()
        s.check("a job added there is on the day, and the page comes back to the calendar",
                made and made["due_date"] == wed.isoformat() and "/calendar" in r.headers.get("Location", ""),
                detail=r.headers.get("Location"))
        r = oc.post("/admin/house-calendar/dates", data={"title": f"{TAG} Fête", "start_date": thu.isoformat(),
                                                         "back_calendar": "week"})
        s.check("a date added there comes back to the calendar on its day",
                r.headers.get("Location", "").endswith(f"/calendar?view=week&date={thu.isoformat()}")
                or r.headers.get("Location", "").endswith(f"/calendar?date={thu.isoformat()}&view=week"),
                detail=r.headers.get("Location"))
        r = oc.post("/admin/house-calendar/dates", data={"title": "", "start_date": thu.isoformat(),
                                                         "back_calendar": "week"})
        s.check("and one refused comes back there too", "/calendar" in r.headers.get("Location", ""),
                detail=r.headers.get("Location"))
        r = oc.post("/admin/house-calendar/dates", data={"title": f"{TAG} Elsewhere", "start_date": thu.isoformat()})
        s.check("from the House calendar page it still goes back to that page",
                "/admin/house-calendar" in r.headers.get("Location", ""), detail=r.headers.get("Location"))

        s.section("Staff may add a job of their own, and nothing else")
        spage = ec.get(f"/calendar?view=week&date={mon.isoformat()}").get_data(as_text=True)
        s.check("their dialog is the one form, for themselves",
                'action="/calendar/my-task"' in spage and 'action="/admin/tasks/new"' not in spage
                and 'action="/admin/shifts/new"' not in spage)
        r = ec.post("/calendar/my-task", data={"title": f"{TAG} mine", "due_date": thu.isoformat(), "view": "week"})
        conn = db()
        mine = conn.execute("SELECT * FROM tasks WHERE title = ?", (f"{TAG} mine",)).fetchone()
        conn.close()
        s.check("it is theirs, on the day, and back to the calendar",
                mine and mine["assigned_to_user_id"] == emp["id"] and mine["due_date"] == thu.isoformat()
                and "/calendar" in r.headers.get("Location", ""), detail=r.headers.get("Location"))
        said = " ".join(flashes(ec.post("/calendar/my-task", data={"title": "", "due_date": thu.isoformat()},
                                        follow_redirects=True)))
        s.check("one with no words is refused", "Say what it is" in said, detail=said)
        anon = m.app.test_client()
        r = anon.post("/calendar/my-task", data={"title": f"{TAG} nobody", "due_date": thu.isoformat()})
        s.check("and nobody logged out may add one", r.status_code in (302, 401, 403)
                and not db().execute("SELECT 1 FROM tasks WHERE title = ?", (f"{TAG} nobody",)).fetchone())
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
