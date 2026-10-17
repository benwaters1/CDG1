"""A thing that lasts several days is on the calendar on every one of them.

The owner, 15 October 2026, the day the RetreatGuru registrations came across:
the ateliers showed on their first day and nowhere after. A four-day atelier
was a Monday with names on it and three empty days. They thought stays might
do the same, and they did: a stay was an arrival and a departure with nothing
on the nights between, and a guest who arrived before the week and left after
it was not on that week at all.

Nothing to do with the import. An imported place is a row on a session like
any other, with the session's end date to go by -- and the calendar never
asked for it. build_overview gave every row ONE date and build_calendar put it
in that one cell. So the cause is fixed there, and this holds every view that
reads those rows to it:

  - an atelier running Monday to Thursday is on all four days -- its places,
    imported and booked here alike, by name on the Monday, and one line for
    the sitting on each day after, saying which day it is;
  - a stay of 9 to 12 March is on the 9th (arrives), the 10th and 11th (in
    residence, night 2 and 3 of 3) and the 12th (departs). The 12th is not a
    night: the room grid fills the 9th to the 11th, and so does this;
  - a guest staying right through a week is on every day of it;
  - the day view, the week, the month, the owner's list, the staff calendar,
    the office screen, and the house calendar in Outlook, which counts the
    places on a sitting and must not count the sitting's own row as another.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, visible_text
import _harness

m = _harness.m
TAG = "ZZEVD"
LOW = TAG.lower()
REG_HEADER = ("first_name,last_name,submitted,cancelled,booking_window,reg_form_complete,phone,"
              "payment,optional_items,roommate,discount_code,travel_phone_,items_discounts_total,"
              "payments_refunds_total,balance_due,scheduled_payments_total,"
              "balance_after_scheduled,registration_amount")


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject))
        return True


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", (f"{LOW}%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (f"{LOW}%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (f"{LOW}%",))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
    conn.commit()
    conn.close()


def _now():
    return m.datetime.now(m.timezone.utc).isoformat()


def _free_monday(conn, after_days):
    """A Monday no session starts on, so the import has exactly one to find."""
    day = m.house_today() + timedelta(days=after_days)
    day += timedelta(days=(7 - day.weekday()) % 7)
    while conn.execute("SELECT 1 FROM workshop_sessions WHERE start_date = ?",
                       (day.isoformat(),)).fetchone():
        day += timedelta(days=7)
    return day


def _import_one(conn, start, end, owner_id):
    """One registration brought across by the importer's own code, as on live."""
    submitted = f"{(start - timedelta(days=60)).isoformat()} 10:00:00"
    regs = REG_HEADER + "\n" + ",".join([
        "Ida", f"{TAG}Imported", submitted, "", "60", "x", "", "", "", "", "", "",
        "1200.00", "0.00", "1200.00", "0", "1200.00", "1200.00"]) + "\n"
    people = ("first_name,last_name,person_id,email,num_programs,overall_balance\n"
              f"Ida,{TAG}Imported,1,{LOW}.ida@example.invalid,1,0\n")
    programmes = ("program_id,title,date_start,date_end\n"
                  f"1,{TAG} Retreat,{start.isoformat()},{end.isoformat()}\n")
    with m.app.test_request_context("/"):
        found, _counts = m.parse_rg_registrations(regs, m.house_today())
        plan = m.plan_rg_import(conn, found, m.parse_rg_programmes(programmes),
                                m.parse_rg_people(people)[1], [], due_days=30,
                                today=m.house_today(), stripe_given=False)
        keys = {line["key"] for line in plan["payers"] if not line["blocking"]}
        made, _kept, problems = m.run_rg_import(conn, plan, keys, m.parse_rg_people(people)[0],
                                                user_id=owner_id)
    conn.commit()
    return made, problems, plan


def _stay(conn, room, ref, arrive, leave, guest, party=2):
    return conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 600, ?)""",
        (room["id"], f"{TAG}{ref}", f"{TAG}tok{ref}", guest, f"{LOW}.{ref.lower()}@example.invalid",
         arrive.isoformat(), leave.isoformat(), party, _now())).lastrowid


def _titles(cell):
    return [e["title"] for e in cell["events"]]


def _mine(cell, words):
    return [e for e in cell["events"] if words in (e["title"] or "")]


def run():
    s = Suite("A thing that lasts several days is on every one of them")
    _cleanup()
    mail = Recorder()
    real_send = m.send_email
    m.send_email = mail
    conn = db()
    try:
        _run(s, mail, conn)
    finally:
        m.send_email = real_send
        conn.rollback()
        conn.close()
        _cleanup()
    return s


def _run(s, mail, conn):
    oc, ec, owner, emp = clients()
    owner = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()

    # ---- An atelier, Monday to Thursday, one place imported, one booked here.
    mon = _free_monday(conn, 150)
    thu = mon + timedelta(days=3)
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
             active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 1200, 10, 1, 93, ?, 30)""", (f"{TAG} Plaster", _now()))
    workshop = conn.execute("SELECT * FROM workshops WHERE title = ?", (f"{TAG} Plaster",)).fetchone()
    session_id = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
             created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (workshop["id"], mon.isoformat(), thu.isoformat(), TAG, _now())).lastrowid
    conn.commit()
    session = conn.execute("SELECT * FROM workshop_sessions WHERE id = ?", (session_id,)).fetchone()

    made, problems, plan = _import_one(conn, mon, thu, owner["id"])
    with m.app.test_request_context("/"):
        m.create_workshop_booking(conn, session, workshop, f"Abe {TAG}Here",
                                  f"{LOW}.abe@example.invalid", "", 1, "", confirm_now=True)
    conn.commit()

    s.section("Imported and booked here are the same shape")
    s.check("the importer brought the one registration across", made == [f"Ida {TAG}Imported"],
            detail=f"{made} {problems} {[l['blocking'] for l in plan['payers']]}")
    places = conn.execute(
        """SELECT wb.*, ws.start_date, ws.end_date FROM workshop_bookings wb
             JOIN workshop_sessions ws ON ws.id = wb.session_id
            WHERE wb.guest_email LIKE ? ORDER BY wb.id""", (f"{LOW}%",)).fetchall()
    imported = next((p for p in places if p["import_key"]), None)
    here = next((p for p in places if not p["import_key"]), None)
    s.check("both are on the session, which ends on the Thursday",
            imported and here and imported["session_id"] == here["session_id"] == session_id
            and imported["end_date"] == here["end_date"] == thu.isoformat(),
            detail=str([dict(p) for p in places])[:300])
    s.check("the imported one is confirmed, with emails off, as on live",
            imported and imported["status"] == "confirmed" and imported["do_not_email"] == 1)
    s.check("and nothing was sent for it", not any("ida" in (to or "") for to, _ in mail.calls),
            detail=str(mail.calls))

    s.section("An atelier Monday to Thursday is on all four days")
    with m.app.test_request_context("/"):
        week = m.build_calendar(conn, "week", mon, viewer=owner)
        month = m.build_calendar(conn, "month", mon, viewer=owner)
        days = {d: m.build_calendar(conn, "day", mon + timedelta(days=d), viewer=owner)
                for d in range(-1, 5)}
    cells = {c["date"]: c for c in week["cells"]}
    # One chip for the places on the first day, in a week or a month (handover
    # z27: eight identical chips read as duplicates); the guests by name are
    # on the day view and on the chip's card.
    s.check("Monday has the atelier once, saying how many arrive",
            [e["title"] for e in _mine(cells[mon], f"{TAG} Plaster")]
            == [f"{TAG} Plaster – 2 guests arrive"],
            detail=str(_titles(cells[mon]))[:300])
    monday = days[0]["cells"][0]
    s.check("Monday's day view names both places, the imported one too",
            _mine(monday, f"Ida {TAG}Imported") and _mine(monday, f"Abe {TAG}Here"),
            detail=str(_titles(monday))[:300])
    card = next((c for c in week["cards"]
                 if c["uid"] == _mine(cells[mon], f"{TAG} Plaster")[0]["card"]), None)
    s.check("and so does the chip's card",
            card and any(f"Ida {TAG}Imported" in n for n in card["names"])
            and any(f"Abe {TAG}Here" in n for n in card["names"]),
            detail=str(card and card["names"]))
    for k in (2, 3, 4):
        day = mon + timedelta(days=k - 1)
        on = _mine(cells[day], f"{TAG} Plaster")
        s.check(f"{day.strftime('%A')} has the atelier, as day {k} of 4, once",
                len(on) == 1 and f"day {k} of 4" in on[0]["title"]
                and "2 bookings, 2 people" in on[0]["title"],
                detail=str(_titles(cells[day]))[:300])
    s.check("not the Sunday before, nor the Friday after",
            not _mine(cells[mon + timedelta(days=4)], f"{TAG} Plaster")
            and not _mine(days[-1]["cells"][0], f"{TAG} Plaster"))
    month_cells = {c["date"]: c for c in month["cells"]}
    s.check("the month has it on all four days",
            all(_mine(month_cells[mon + timedelta(days=i)], f"{TAG} Plaster") for i in range(4)),
            detail=str([len(_mine(month_cells[mon + timedelta(days=i)], f"{TAG} Plaster"))
                        for i in range(4)]))
    s.check("and says which day it is in a month cell too",
            [e["short"] for e in _mine(month_cells[mon + timedelta(days=2)], f"{TAG} Plaster")]
            == [f"{TAG} Plaster · day 3"],
            detail=str([e["short"] for e in month_cells[mon + timedelta(days=2)]["events"]]))
    s.check("the day view of each of the four days has it",
            all(_mine(days[i]["cells"][0], f"{TAG} Plaster") for i in range(4)),
            detail=str([len(_mine(days[i]["cells"][0], f"{TAG} Plaster")) for i in range(4)]))
    s.check("a day view of the Wednesday -- a window the atelier began before -- has it",
            any("day 3 of 4" in e["title"] for e in _mine(days[2]["cells"][0], f"{TAG} Plaster")))
    s.check("the week counts the sitting as one thing, not one per day",
            week["total_events"] < sum(len(c["events"]) for c in week["cells"]),
            detail=f"{week['total_events']} things, {sum(len(c['events']) for c in week['cells'])} chips")

    s.section("The pages themselves")
    text = visible_text(oc.get(f"/calendar?view=week&date={mon.isoformat()}").get_data(as_text=True))
    s.check("the owner's week page shows day 2, 3 and 4",
            all(f"{TAG} Plaster — 2 bookings, 2 people · day {k} of 4" in text for k in (2, 3, 4)),
            detail=text[:300])
    text = visible_text(oc.get(f"/calendar?view=month&date={mon.isoformat()}").get_data(as_text=True))
    s.check("the month page shows it on its later days", f"{TAG} Plaster · day 4" in text)
    text = visible_text(ec.get(f"/calendar?view=day&date={thu.isoformat()}").get_data(as_text=True))
    s.check("the staff calendar has it on the Thursday",
            f"{TAG} Plaster — 2 bookings, 2 people · day 4 of 4" in text, detail=text[:300])
    text = visible_text(oc.get(f"/admin/overview?view=day&date={(mon + timedelta(days=2)).isoformat()}")
                        .get_data(as_text=True))
    s.check("the owner's list for the Wednesday has it", "day 3 of 4" in text and f"{TAG} Plaster" in text)
    text = visible_text(oc.get(f"/admin/overview?view=week&date={mon.isoformat()}").get_data(as_text=True))
    s.check("and the week's list has it once, for its days after the first",
            text.count(f"{TAG} Plaster — 2 bookings") == 1 and "days 2–4 of 4" in text,
            detail=text[text.find(TAG):][:300])

    s.section("A stay of 9 to 12 March")
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    year = m.house_today().year + 1
    nine, twelve = date(year, 3, 9), date(year, 3, 12)
    stay = _stay(conn, room, "MAR", nine, twelve, f"Mae {TAG}March")
    short = _stay(conn, room, "ONE", date(year, 3, 20), date(year, 3, 21), f"Una {TAG}One")
    conn.commit()
    with m.app.test_request_context("/"):
        march = m.build_calendar(conn, "month", nine, viewer=owner)
        rooms_days, room_rows = m.room_month_rows(conn, date(year, 3, 1))
    mc = {c["date"]: c for c in march["cells"]}
    guest = f"Mae {TAG}March"
    s.check("the 9th: arrives", any(t.startswith(f"{guest} arrives") for t in _titles(mc[nine])),
            detail=str(_titles(mc[nine]))[:200])
    s.check("the 10th: in residence, night 2 of 3",
            any(t.startswith(f"{guest} in residence") and "night 2 of 3" in t
                for t in _titles(mc[nine + timedelta(days=1)])),
            detail=str(_titles(mc[nine + timedelta(days=1)]))[:200])
    s.check("the 11th: in residence, night 3 of 3",
            any(t.startswith(f"{guest} in residence") and "night 3 of 3" in t
                for t in _titles(mc[nine + timedelta(days=2)])))
    s.check("the 12th: departs, and is not called a night",
            any(t.startswith(f"{guest} departs") for t in _titles(mc[twelve]))
            and not any("in residence" in t for t in _titles(mc[twelve]) if guest in t),
            detail=str([t for t in _titles(mc[twelve]) if guest in t]))
    s.check("nothing of it on the 8th or the 13th",
            not _mine(mc[nine - timedelta(days=1)], guest)
            and not _mine(mc[twelve + timedelta(days=1)], guest))
    s.check("each day once", all(len(_mine(mc[nine + timedelta(days=i)], guest)) == 1
                                 for i in range(4)))
    grid = next(r for r in room_rows if r["room"]["id"] == room["id"])
    nights = {c["date"] for c in grid["cells"] if c["key"] == f"b{stay}"}
    staying = {d for d, c in mc.items() if d.month == 3 and any(
        e["title"].startswith(f"{guest} arrives") or e["title"].startswith(f"{guest} in residence")
        for e in c["events"])}
    s.check("the nights it shows are the nights the room grid fills",
            nights == staying == {nine, nine + timedelta(days=1), nine + timedelta(days=2)},
            detail=f"grid {sorted(nights)} calendar {sorted(staying)}")
    s.check("a one-night stay is its arrival and its departure, nothing between",
            [len(_mine(mc[date(year, 3, d)], f"Una {TAG}One")) for d in (19, 20, 21, 22)]
            == [0, 1, 1, 0]
            and not any("in residence" in e["title"] for c in mc.values()
                        for e in _mine(c, f"Una {TAG}One")))
    text = visible_text(oc.get(f"/calendar?view=week&date={(nine + timedelta(days=1)).isoformat()}")
                        .get_data(as_text=True))
    s.check("the week page says so", f"{guest} in residence — {room['name']} · night 2 of 3" in text,
            detail=text[:300])
    text = visible_text(ec.get(f"/calendar?view=day&date={(nine + timedelta(days=2)).isoformat()}")
                        .get_data(as_text=True))
    s.check("and so does the staff calendar's day", f"{guest} in residence" in text)

    s.section("Staying right through a week")
    wk = _free_monday(conn, 400)
    long_id = _stay(conn, room, "LONG", wk - timedelta(days=3), wk + timedelta(days=10),
                    f"Lev {TAG}Long")
    conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
             arrival_date, departure_date, room_label, room_id, guests,
             first_seen_at, last_event_at, updated_at)
           VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, NULL, NULL, 2, ?, ?, ?)""",
        (f"{TAG}OTA", f"Ola {TAG}Channel", (wk - timedelta(days=1)).isoformat(),
         (wk + timedelta(days=9)).isoformat(), _now(), _now(), _now()))
    conn.commit()
    with m.app.test_request_context("/"):
        through = m.build_calendar(conn, "week", wk, viewer=owner)
        listed = m.build_overview(conn, "week", wk)["rows"]
    s.check("a guest who came before the week and leaves after it is on all seven days",
            all(any(e["title"].startswith(f"Lev {TAG}Long in residence") for e in c["events"])
                for c in through["cells"]),
            detail=str([len(_mine(c, f"Lev {TAG}Long")) for c in through["cells"]]))
    s.check("a Booking.com guest the same",
            all(any(" · in residence" in e["title"] for e in _mine(c, f"Ola {TAG}Channel · "))
                for c in through["cells"]),
            detail=str([len(_mine(c, f"Ola {TAG}Channel")) for c in through["cells"]]))
    lev = [r for r in listed if r["title"].startswith(f"Lev {TAG}Long")]
    s.check("the week's list has them once, not once a night",
            len(lev) == 1 and lev[0]["date"] == wk.isoformat()
            and "nights 4–10 of 13" in lev[0]["title"], detail=str([r["title"] for r in lev]))
    ola = [r for r in listed if f"Ola {TAG}Channel" in r["title"]]
    s.check("linking to the Booking.com page like its arrival and departure",
            ola and all("/management/booking-com" in (r["link"] or "") for r in ola))
    s.check("the arrival before the week is not put in it as an arrival",
            not any("arrives" in r["title"] for r in lev + ola))

    s.section("The office screen and the house calendar")
    today = m.house_today()
    here_now = _stay(conn, room, "NOW", today - timedelta(days=1), today + timedelta(days=2),
                     f"Nia {TAG}Now")
    conn.commit()
    page = oc.get("/admin/display").get_data(as_text=True)

    def section(start, end):
        at = page.find(start)
        return page[at:page.find(end, at)] if at >= 0 else ""
    guests = section('<h2 class="od-subheading">Current Guests', "</ul>")
    today_panel = section('<section class="od-panel od-today">', "</section>")
    with m.app.test_request_context("/"):
        todays = [r["title"] for r in m.build_overview(conn, "day", today)["rows"]]
    s.check("the owner's list for today has the guest in residence",
            any(t.startswith(f"Nia {TAG}Now in residence") for t in todays))
    s.check("the screen's guest panel has them", f"Nia {TAG}Now" in guests, detail=guests[:300])
    s.check("and its Today list does not have them a second time",
            bool(today_panel) and f"Nia {TAG}Now" not in today_panel, detail=today_panel[:300])
    with m.app.test_request_context("/"):
        whole = {i["key"]: i for i in m.house_calendar_items(conn, mon - timedelta(days=7),
                                                             mon + timedelta(days=30))}
        later = {i["key"]: i for i in m.house_calendar_items(conn, mon + timedelta(days=2),
                                                             mon + timedelta(days=30))}
    ses = whole.get(f"session:{session_id}")
    s.check("the house calendar counts the places, not the sitting's own row as a third",
            ses and "2 booked, 2 people" in ses["subject"], detail=ses and ses["subject"])
    ses = later.get(f"session:{session_id}")
    s.check("a sitting that began before the window is still on it, counted",
            ses and "2 booked, 2 people" in ses["subject"]
            and ses["end"].startswith((thu + timedelta(days=1)).isoformat()),
            detail=ses and (ses["subject"], ses["end"]))
    s.check("a stay is still one event, however many rows it has",
            sum(1 for k in whole if k == f"stay:{here_now}") <= 1
            and len([k for k in later if k.startswith("stay:")]) == len(set(
                k for k in later if k.startswith("stay:"))))
    del long_id

    s.section("Negative control: one date per row, as it was")
    real = m._spanning_row

    def one_day(row, run, first_day, last_day, lo, hi):
        made = real(row, run, first_day, last_day, lo, hi)
        return made and dict(made, until=made["date"], run=None)
    m._spanning_row = one_day
    try:
        with m.app.test_request_context("/"):
            broken = {c["date"]: c for c in m.build_calendar(conn, "week", mon, viewer=owner)["cells"]}
    finally:
        m._spanning_row = real
    s.check("(negative control: with one date per row, Wednesday and Thursday lose the atelier)",
            not _mine(broken[mon + timedelta(days=2)], f"{TAG} Plaster")
            and not _mine(broken[thu], f"{TAG} Plaster"),
            detail=str(_titles(broken[thu]))[:200])


if __name__ == "__main__":
    print(run().report())
