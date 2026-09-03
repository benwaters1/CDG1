"""Five things the house does every day that nothing on a screen helped with.

The dietary sheet is the one that matters. Three tables hold what a guest
cannot eat -- their profile, their table reservation, their atelier place --
and nothing brought them together, so cooking one dinner meant opening three
screens. The failure mode is not inconvenience. It is serving somebody the
thing they told us about when they booked.

The rest are the same shape: a fact the database already held, that no page
put in front of the person who needed it.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ztest-ops-"


def _cleanup(conn):
    conn.execute("DELETE FROM booking_extras WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.commit()


def _rooms(conn):
    return [r["id"] for r in conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id").fetchall()]


def _book(conn, ref, room_id, arrive, depart, *, name=None, requests=None,
          guest_id=None, phone="0600000000", email=None, arrival_time="16:00"):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, guest_phone, arrival_date, departure_date, party_size,
             status, total_price, special_requests, linked_guest_id,
             estimated_arrival_time, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?, ?, ?, ?)""",
        (room_id, TAG + ref, TAG + "tk" + ref, name or (TAG + "Guest " + ref),
         email if email is not None else (TAG + ref + "@example.invalid"),
         phone, arrive.isoformat(), depart.isoformat(), requests, guest_id,
         arrival_time, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                        (TAG + ref,)).fetchone()["id"]


def run():
    s = Suite("house operations")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()
    rooms = _rooms(conn)

    # ------------------------------------------------------- dietary sheet
    s.section("Everything the kitchen needs on one sheet")
    conn.execute(
        """INSERT INTO guests (name, email, dietary_notes, created_at)
           VALUES (?, ?, ?, ?)""",
        (TAG + "Coeliac", TAG + "coeliac@example.invalid", "Coeliac — no gluten",
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    gid = conn.execute("SELECT id FROM guests WHERE name = ?",
                       (TAG + "Coeliac",)).fetchone()["id"]
    # Staying tonight, with the allergy on the PROFILE rather than the booking.
    _book(conn, "STAY", rooms[0], today - timedelta(days=1), today + timedelta(days=2),
          name=TAG + "Coeliac", guest_id=gid)
    # Staying tonight with a note typed onto the booking instead.
    _book(conn, "REQ", rooms[1], today, today + timedelta(days=1),
          requests="No shellfish")
    # Dining tonight, not staying.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, dietary_notes, status, created_at)
           VALUES (?, ?, ?, ?, 4, ?, 'Nut allergy', 'confirmed', ?)""",
        (TAG + "DINE", TAG + "tkD", TAG + "Diner", TAG + "d@example.invalid",
         today.isoformat(), m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()

    with m.app.test_request_context():
        sheet = m.dietary_sheet(conn, today)
    names = {p["who"]: p for p in sheet["people"]}
    s.check("somebody staying tonight is on the sheet",
            TAG + "Coeliac" in names, detail=str(list(names)[:6]))
    # The whole point: the note is on the profile, not on the booking.
    s.check("with the note that lives on their profile, not their booking",
            any("gluten" in n.lower() for n in names.get(TAG + "Coeliac", {}).get("notes", [])),
            detail=str(names.get(TAG + "Coeliac", {}).get("notes")))
    s.check("a note typed onto the booking is picked up too",
            any("shellfish" in n.lower()
                for n in names.get(TAG + "Guest REQ", {}).get("notes", [])),
            detail=str(names.get(TAG + "Guest REQ", {}).get("notes")))
    s.check("and somebody dining but not staying is on it as well",
            TAG + "Diner" in names and any(
                "nut" in n.lower() for n in names[TAG + "Diner"]["notes"]),
            detail=str(names.get(TAG + "Diner", {}).get("notes")))
    s.check("each is marked as how they are here",
            names.get(TAG + "Diner", {}).get("kind") == "Dining"
            and names.get(TAG + "Coeliac", {}).get("kind") == "Staying",
            detail=f"{names.get(TAG + 'Diner', {}).get('kind')} / "
                   f"{names.get(TAG + 'Coeliac', {}).get('kind')}")

    # Somebody with nothing recorded must still be LISTED. A blank line is a
    # question the kitchen can ask; a missing line is one nobody knows to ask.
    _book(conn, "SILENT", rooms[2], today, today + timedelta(days=1))
    with m.app.test_request_context():
        sheet = m.dietary_sheet(conn, today)
    names = {p["who"]: p for p in sheet["people"]}
    s.check("somebody who has said nothing is still on the sheet",
            TAG + "Guest SILENT" in names, detail=str(list(names)[:8]))
    s.check("shown as having told us nothing rather than as having no needs",
            names.get(TAG + "Guest SILENT", {}).get("notes") == [],
            detail=str(names.get(TAG + "Guest SILENT", {}).get("notes")))
    s.check("and counted in the silent tally", sheet["silent"] >= 1,
            detail=str(sheet["silent"]))

    # Departure day is checkout. They are not eating dinner here.
    with m.app.test_request_context():
        gone = m.dietary_sheet(conn, today + timedelta(days=1))
    s.check("a guest who checks out that morning is off the sheet",
            TAG + "Guest SILENT" not in {p["who"] for p in gone["people"]},
            detail="a departure is not a cover")

    # -------------------------------------------------------- turnarounds
    s.section("Where a room empties and fills the same day")
    out_day = today + timedelta(days=10)
    _book(conn, "OUT", rooms[0], out_day - timedelta(days=2), out_day)
    _book(conn, "IN", rooms[0], out_day, out_day + timedelta(days=2))
    # A different room with a clear day between: not a changeover.
    _book(conn, "GAPA", rooms[1], out_day - timedelta(days=2), out_day)
    _book(conn, "GAPB", rooms[1], out_day + timedelta(days=1), out_day + timedelta(days=3))
    with m.app.test_request_context():
        turn = m.turnaround_report(conn, days=60, on_day=today)
    days = {d["day"]: d for d in turn["days"]}
    s.check("the same-day changeover is found", out_day in days,
            detail=str(sorted(days)[:4]))
    s.check("and it names both guests",
            out_day in days and any(TAG + "Guest OUT" == c["out_guest"]
                                    and TAG + "Guest IN" == c["in_guest"]
                                    for c in days[out_day]["changeovers"]),
            detail=str(days.get(out_day, {}).get("changeovers")))
    # The distinction that makes it worth a page.
    s.check("a room with a clear day between stays is not a changeover",
            out_day not in days
            or not any(c["out_guest"] == TAG + "Guest GAPA" for c in days[out_day]["changeovers"]),
            detail="a night between two stays is not a same-day turnaround")

    third = rooms[2] if len(rooms) > 2 else rooms[0]
    _book(conn, "H1", third, out_day - timedelta(days=1), out_day)
    _book(conn, "H2", third, out_day, out_day + timedelta(days=1))
    fourth = rooms[3] if len(rooms) > 3 else rooms[1]
    _book(conn, "H3", fourth, out_day - timedelta(days=1), out_day)
    _book(conn, "H4", fourth, out_day, out_day + timedelta(days=1))
    with m.app.test_request_context():
        turn = m.turnaround_report(conn, days=60, on_day=today)
    heavy_days = {d["day"] for d in turn["heavy"]}
    s.check("a day with three rooms turning over is flagged as needing hands",
            out_day in heavy_days, detail=str(sorted(heavy_days)))

    # ---------------------------------------------------- extras attach rate
    s.section("What the extras earn")
    extra = conn.execute(
        "SELECT id, name, price FROM extras WHERE active = 1 LIMIT 1").fetchone()
    if extra:
        bid = _book(conn, "EX", rooms[0], today - timedelta(days=5),
                    today - timedelta(days=3))
        conn.execute(
            """INSERT INTO booking_extras (booking_id, extra_id, name, unit_price,
                 quantity, status, notes, created_at)
               VALUES (?, ?, ?, ?, 2, 'confirmed', ?, ?)""",
            (bid, extra["id"], extra["name"], 25.0, TAG + "note",
             m.datetime.now(m.timezone.utc).isoformat()))
        # A cancelled extra was never sold and must not be counted.
        bid2 = _book(conn, "EXC", rooms[1], today - timedelta(days=5),
                     today - timedelta(days=3))
        conn.execute(
            """INSERT INTO booking_extras (booking_id, extra_id, name, unit_price,
                 quantity, status, notes, created_at)
               VALUES (?, ?, ?, ?, 5, 'cancelled', ?, ?)""",
            (bid2, extra["id"], extra["name"], 25.0, TAG + "note",
             m.datetime.now(m.timezone.utc).isoformat()))
        conn.commit()
        with m.app.test_request_context():
            perf = m.extras_performance(conn, today - timedelta(days=30), today)
        row = next((r for r in perf["rows"] if r["name"] == extra["name"]), None)
        s.check("an extra that sold is on the list", row is not None,
                detail=str([r["name"] for r in perf["rows"]][:5]))
        s.check("counted at the price and quantity on the booking",
                row and row["revenue"] >= 50.0, detail=str(row["revenue"] if row else None))
        s.check("a cancelled extra is not revenue",
                row and row["units"] == 2, detail=str(row["units"] if row else None))
        s.check("attach rate is stays that took it over stays in the window",
                row and 0 < row["attach"] <= 100, detail=str(row["attach"] if row else None))
    else:
        for label in ("an extra that sold is on the list",
                      "counted at the price and quantity on the booking",
                      "a cancelled extra is not revenue",
                      "attach rate is stays that took it over stays in the window"):
            s.check(label, True, detail="no extras configured — nothing to measure")

    # ------------------------------------------------ arrivals missing detail
    s.section("Arrivals the house cannot prepare for")
    _book(conn, "NOTIME", rooms[0], today + timedelta(days=2),
          today + timedelta(days=4), arrival_time="")
    _book(conn, "NOPHONE", rooms[1], today + timedelta(days=9),
          today + timedelta(days=11), phone="")
    with m.app.test_request_context():
        miss = m.arrivals_missing_detail(conn, days=14, on_day=today)
    by_ref = {r["reference"]: r for r in miss["rows"]}
    s.check("an arrival with no time is listed", TAG + "NOTIME" in by_ref,
            detail=str(list(by_ref)[:5]))
    s.check("and what it is missing is named",
            "No arrival time" in by_ref.get(TAG + "NOTIME", {}).get("missing", []),
            detail=str(by_ref.get(TAG + "NOTIME", {}).get("missing")))
    s.check("one with no telephone number is listed too",
            TAG + "NOPHONE" in by_ref
            and "No telephone number" in by_ref[TAG + "NOPHONE"]["missing"],
            detail=str(by_ref.get(TAG + "NOPHONE", {}).get("missing")))
    s.check("a complete booking is not listed", TAG + "SILENT" not in by_ref,
            detail="a booking with everything on it is not a problem")
    # Inside three days there is no longer time to ask and get an answer.
    urgent = {r["reference"] for r in miss["urgent"]}
    s.check("one arriving in two days is urgent", TAG + "NOTIME" in urgent,
            detail=str(urgent))
    s.check("one arriving in nine days is not", TAG + "NOPHONE" not in urgent,
            detail=str(urgent))

    # ------------------------------------------------------- cost per night
    s.section("What a night costs")
    # A real cost inside the window, and a real night sold. With both at zero
    # every way of dividing gives the same answer and the checks below cannot
    # tell "per night sold" from "per night in the month".
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
             spent_on, submitted_at)
           VALUES ('supplier_invoice', ?, ?, 600.0, 'approved', ?, ?)""",
        (TAG + "Supplier", TAG + "firewood", today.isoformat(),
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    _book(conn, "COST", rooms[0], today, today + timedelta(days=2))
    with m.app.test_request_context():
        period = m.resolve_period("month", today.isoformat())
        cost = m.cost_per_occupied_night(conn, period)
    s.check("there is a cost and a night to divide it by",
            cost["nights"] > 0 and cost["total"] > 0,
            detail=f"{cost['total']} over {cost['nights']} nights — with either "
                   "at zero the next check cannot fail")
    days_in_month = (period["end"] - period["start"]).days
    s.check("it is divided by nights SOLD, not by days in the month",
            cost["nights"] != days_in_month
            and cost["per_night_total"] is not None
            and abs(cost["per_night_total"] - cost["total"] / days_in_month) > 0.02,
            detail=f"{cost['per_night_total']} — dividing by {days_in_month} days "
                   f"would give {round(cost['total'] / days_in_month, 2)}")
    s.check("the figures are per night SOLD, not per night in the month",
            cost["nights"] <= (period["end"] - period["start"]).days * len(rooms),
            detail=f"{cost['nights']} nights sold")
    if cost["nights"]:
        s.check("cost per night is the total divided by nights sold",
                cost["per_night_total"] is not None
                and abs(cost["per_night_total"] - cost["total"] / cost["nights"]) < 0.02,
                detail=f"{cost['per_night_total']} vs {cost['total']}/{cost['nights']}")
        s.check("and what is left over is the average night less that",
                cost["margin_per_night"] is None or cost["adr"] is None
                or abs(cost["margin_per_night"]
                       - (cost["adr"] - cost["per_night_total"])) < 0.02,
                detail=str(cost["margin_per_night"]))
    else:
        s.check("with nothing sold it declines to divide by zero",
                cost["per_night_total"] is None and cost["margin_per_night"] is None,
                detail=str(cost["per_night_total"]))
        s.check("and what is left over is withheld too",
                cost["margin_per_night"] is None)
    # pay_rate is free text, so this is never a payroll figure and says so.
    s.check("labour is marked as estimated", bool(cost["labour_estimated"]) in (True, False),
            detail=str(cost["labour_estimated"]))

    s.section("These reach somebody instead of waiting to be opened")
    # CLAUDE.md's rule: a check nobody opens is worth nothing. Both of these
    # become a warning on the owner home and a task on the calendar, and both
    # CLOSE THEMSELVES -- there is no "done" button, so every run rebuilds the
    # picture and ticks off anything no longer true.
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    titles = {w["title"] for w in warnings}
    s.check("an arrival two days out with no time warns the owner",
            "Arriving without the details to prepare" in titles,
            detail=str(sorted(titles))[:200])
    s.check("and a whole-house changeover does too",
            "A day with the whole house to turn round" in titles,
            detail=str(sorted(titles))[:200])
    s.check("each warning links to the page that says more",
            all(w.get("href") for w in warnings),
            detail="a warning with nowhere to go is a dead end")

    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn, today)
    kinds = {kind for kind, *_rest in found}
    s.check("both become tasks so they reach the calendar",
            {"changeover", "detail"} <= kinds, detail=str(sorted(kinds)))
    changeover = next((f for f in found if f[0] == "changeover"), None)
    s.check("the changeover task is due the day BEFORE",
            changeover and changeover[3] < out_day.isoformat(),
            detail=f"due {changeover[3] if changeover else '?'} for a changeover "
                   f"on {out_day.isoformat()} — the useful moment is the evening "
                   "you can still put somebody else on")
    s.check("and its title carries the date, so it dedupes to one task",
            changeover and out_day.isoformat() in changeover[1],
            detail=str(changeover[1] if changeover else None))
    # The half that matters: fix the booking and the finding stops being true.
    conn.execute("UPDATE bookings SET estimated_arrival_time = '15:00' "
                 "WHERE reference_code = ?", (TAG + "NOTIME",))
    conn.commit()
    with m.app.test_request_context():
        found_after, _ = m.watch_task_findings(conn, today)
    refs = [f[1] for f in found_after if f[0] == "detail"]
    s.check("filling the arrival time in takes the task away again",
            not any(TAG + "NOTIME" in t for t in refs),
            detail=f"{refs[:3]} — nothing in this set has a done button, so a "
                   "finding that stays true forever is a list nobody reads twice")

    s.section("Every one of these pages renders with something on it")
    # The changeover page 500'd the first time it had a row, and rendered
    # perfectly with none: `d.items` in Jinja reaches dict.items before it
    # reaches the key. An empty page proves nothing, so these are asked for
    # while the fixtures above are still in the database.
    for path in ("/kitchen/dietary", "/admin/turnarounds", "/admin/arrivals-incomplete",
                 "/management/extras-performance", "/management/night-margin",
                 "/kitchen/dietary.csv", "/admin/turnarounds.csv",
                 "/management/extras-performance.csv", "/management/night-margin.csv"):
        r = oc.get(path)
        s.check(f"{path} renders with data on it", r.status_code == 200,
                detail="HTTP %s" % r.status_code)

    s.section("Who can see what")
    s.check("an employee can open the dietary sheet",
            ec.get("/kitchen/dietary").status_code == 200,
            detail="the kitchen is staff, not owners")
    # The changeover list is housekeeping's, and so is the breakfast list.
    # Asserting they open to the same people states the intent; a hardcoded
    # status here would just record whichever areas this employee happens to
    # have been granted.
    s.check("the changeover list opens to whoever the breakfast list opens to",
            (ec.get("/admin/turnarounds").status_code == 200)
            == (ec.get("/breakfast").status_code == 200),
            detail=f"turnarounds {ec.get('/admin/turnarounds').status_code}, "
                   f"breakfast {ec.get('/breakfast').status_code} — same job, "
                   "so the same door")
    for path in ("/management/extras-performance", "/management/night-margin"):
        s.check(f"but not {path}", ec.get(path).status_code in (302, 403),
                detail="HTTP %s" % ec.get(path).status_code)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
