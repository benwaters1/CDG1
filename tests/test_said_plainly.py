"""Small things the staff pages said wrong, each said plainly now.

Found by reading every staff page on a seeded busy day (back-end review,
27 November 2026), each confirmed against the code:

  Here now      said from the arrival date, so a family whose flight had not
                landed was HERE NOW at ten in the morning, and a guest at
                breakfast on her last day was not.
  Breakfast     this morning's table and tomorrow's count, each said as itself.
  Cash up       the open-tab check read the UTC day of a stored moment.
  × buttons     a bare × with no name, on contacts, announcements, the profile,
                vendors, breakfast, documents, filings and tonight's card.
  Words         "Siret, tva number" (capitalize() lowered the rest); optional
                and "Writes to nobody" in the danger red; "oldest due
                2026-11-19"; "1.23457e+06" on a meter; "pending" on mileage;
                "1 children"; "1 breakage have"; "the the couple"; "They are
                written to to say so"; "Joined" on an enquiry; "pickup"; the
                calendar's "Requests" button opening Bookings; "The list as
                data"; "Arranged" as a button; "[Former register entry:
                2026-08-10…]" under To know; a phone number you could not tap;
                Mark inactive at one tap; the manual's only "I've read this"
                above the manual.

Every check below was broken on purpose once and seen to fail.
"""
import glob
import inspect
import os
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZB4"


def _now(delta_h=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).isoformat()


def _main(html):
    hit = re.search(r"<main\b.*?</main>", html, re.S)
    return hit.group(0) if hit else html


def _template(name):
    return open(os.path.join(_harness.ROOT, "templates", name), encoding="utf-8").read()


def _cleanup(made):
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM mileage_claims WHERE reason LIKE ?", (f"{TAG}%",))
    for table, ids in made.items():
        for i in ids:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (i,))
    conn.commit()
    conn.close()


def run():
    s = Suite("Said plainly")
    made = {}
    _cleanup(made)
    oc, ec, owner, emp = clients()
    today = house_today()
    iso = today.isoformat()
    room = ensure_room()
    try:
        s.section("Here now means in the house")
        y, t = (today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()
        here = m.stay_is_here
        s.check("due today and not checked in: not here yet",
                not here({"arrival_date": iso, "departure_date": t, "checked_in_at": None}, iso))
        s.check("due today and checked in: here",
                here({"arrival_date": iso, "departure_date": t, "checked_in_at": _now()}, iso))
        s.check("leaving today and not checked out: still here",
                here({"arrival_date": y, "departure_date": iso, "checked_in_at": None,
                      "checked_out_at": None}, iso))
        s.check("checked out: gone",
                not here({"arrival_date": y, "departure_date": iso, "checked_out_at": _now()}, iso))
        s.check("a Booking.com stay, with no check-in to read, goes by its dates",
                here({"arrival_date": iso, "departure_date": t}, iso))
        conn = db()
        bid = conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status, total_price,
               created_at, source) VALUES (?, ?, ?, ?, 'zzb4@example.invalid', ?, ?, 2,
               'confirmed', 600, ?, 'direct')""",
            (room["id"], f"{TAG}A", _harness.secrets_token(), f"{TAG} Arriving Later",
             iso, t, _now(-24 * 9))).lastrowid
        conn.commit()
        conn.close()
        page = visible_text(_main(oc.get(f"/admin/bookings/{bid}").get_data(as_text=True)))
        s.check("the booking page says Arriving today, not Here now",
                "Arriving today" in page and "Here now" not in page, detail=page[:240])

        s.section("Breakfast: this morning, and tomorrow")
        conn = db()
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status, total_price,
               created_at, source) VALUES (?, ?, ?, ?, 'zzb4.stay@example.invalid', ?, ?, 2,
               'confirmed', 600, ?, 'direct')""",
            (room["id"], f"{TAG}S", _harness.secrets_token(), f"{TAG} Staying On", y, t,
             _now(-24 * 9)))
        conn.commit()
        conn.close()
        text = visible_text(_main(ec.get("/breakfast").get_data(as_text=True)))
        s.check("this morning's table and tomorrow's count are each said as themselves",
                "This morning:" in text and "Tomorrow:" in text
                and "from tonight's stays" in text, detail=text[:300])

        s.section("Cash up places a tab by its service day, not a UTC day")
        source = inspect.getsource(m.pos_tabs_open_through)
        s.check("no date() of a stored moment in it", "date(opened_at)" not in source)
        conn = db()
        start, end = m.service_day_window(today)
        legacy = conn.execute(
            "INSERT INTO pos_orders (table_label, covers, status, opened_at) "
            "VALUES ('ZZB4', 2, 'open', ?)", (start,)).lastrowid
        made.setdefault("pos_orders", []).append(legacy)
        conn.commit()
        found = [r["id"] for r in m.pos_tabs_open_through(conn, today)]
        conn.close()
        s.check("a tab from before service_date was stored is still found by its moment",
                legacy in found, detail=str(found)[:120])

        s.section("A button says what it does, with a name")
        bare, unnamed = [], []
        for path in glob.glob(os.path.join(_harness.ROOT, "templates", "*.html")):
            src = open(path, encoding="utf-8").read()
            if 'class="doc-delete">×<' in src:
                bare.append(os.path.basename(path))
            for tag in re.findall(r'<button[^>]*class="doc-delete"[^>]*>', src):
                if "aria-label" not in tag:
                    unnamed.append(os.path.basename(path))
        s.check("no bare × delete button is left", not bare, detail=str(bare))
        s.check("and every one has a name a screen reader can say", not unnamed,
                detail=str(sorted(set(unnamed))))

        s.section("Words")
        s.check("SIRET and TVA keep their capitals",
                m.first_upper("SIRET, TVA number, legal form") == "SIRET, TVA number, legal form")
        s.check("optional is not drawn in the danger red",
                "else 'status-inactive') }}\">" not in _template("admin_readiness.html"))
        s.check("nor is a switch that writes to nobody",
                'status-inactive">Writes to nobody' not in _template("admin_automation.html"))
        s.check("an overdue supplier's date in words",
                "oldest due {{ money.soonest }}" not in _template("vendors.html"))
        s.check("an exit interview's date in words",
                "{{ r['held_on'] }}" not in _template("exit_interviews.html"))
        s.check("a meter reading is a number, never 1.23457e+06",
                "'%g'|format" not in _template("management_meters.html")
                and "e+" not in m.quantity(1234567.0, 3))
        conn = db()
        conn.execute("INSERT INTO mileage_claims (user_id, travelled_on, from_place, to_place, "
                     "reason, kilometres, rate, amount, status, created_at) VALUES (?, ?, 'A', "
                     "'B', ?, 10, 0.35, 3.5, 'approved', ?)",
                     (emp["id"], iso, f"{TAG} errand", _now()))
        conn.commit()
        conn.close()
        mileage = visible_text(_main(ec.get("/mileage").get_data(as_text=True)))
        s.check("a mileage claim's state in words",
                "Approved" in mileage and " approved " not in mileage.replace("Approved", ""),
                detail=mileage[mileage.find(TAG) - 40:][:140])
        s.check("one child, not one children",
                "children)" not in _template("admin_rooms.html").replace("'children') }})", ""))
        s.check("one breakage has", "breakage{{ '' if data.undecided == 1 else 's' }} have"
                not in _template("management_breakage_recovery.html"))
        s.check("one stay was booked",
                "{{ working.booking_com.stays }} of these stays were" not in _template("admin_city_tax.html"))
        s.check("the couple, not the the couple",
                "the front of house and the\n    {{ hosts }}" not in _template("admin_event_run_sheet.html"))
        s.check("we will write to tell them", "written to to say" not in _template("admin_event.html"))
        s.check("an enquiry was asked, not joined",
                "Joined {{" not in _template("admin_waitlist.html"))
        s.check("a transfer says Pickup, not pickup",
                "— {{ t['direction'] }}</span>" not in _template("vehicle_transfers.html"))
        s.check("the calendar's button is called what it opens",
                'class="btn-mini">Requests</a>' not in _template("admin_calendar.html"))
        s.check("the prices list downloads, in words",
                "The list as data" not in _template("channel_booking_prices.html"))
        s.check("the extra's button says what it does",
                "<summary>Arranged</summary>" not in _template("extras_due.html"))
        s.check("a profile note leaves out the old register lines",
                m.without_register_lines("Likes tea\n[Former register entry: 2026-08-10 to "
                                         "2026-08-17, party of 2]") == "Likes tea")

        s.section("Things to tap")
        directory = ec.get("/directory").get_data(as_text=True)
        s.check("a colleague's number can be tapped to call", 'href="tel:' in directory
                or "phone" not in visible_text(_main(directory)).lower())
        s.check("Mark inactive asks first",
                "inactive? They can no longer sign in" in _template("directory.html"))
        manual = _template("manual.html")
        s.check("the manual's I've read this is also at the end, where the reading stops",
                manual.count("acknowledge_manual") >= 3)
    finally:
        _cleanup(made)
    return s
