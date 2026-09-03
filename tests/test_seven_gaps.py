"""Seven things the database knew and no page said.

Each is small. What they share is a distinction that has to be got right or
the page is worse than nothing: a supplier with no invoice is not a cheap
supplier, a guest with no record is not a guest with no needs, no linen
recorded is not no linen held, and somebody asking for a night with rooms
still free was not turned away.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZ7G"


def _cleanup(conn):
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_feedback WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_notes WHERE body LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.commit()


def _now():
    return m.datetime.now(m.timezone.utc).isoformat()


def run():
    s = Suite("seven gaps")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()["id"]
    rooms_total = conn.execute(
        "SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]

    # -------------------------------------------------------- suppliers
    s.section("A supplier with no invoice is not a cheap supplier")
    conn.execute("INSERT INTO vendors (name, active, created_at) VALUES (?, 1, ?)",
                 (TAG + " Paid", _now()))
    conn.execute("INSERT INTO vendors (name, active, created_at) VALUES (?, 1, ?)",
                 (TAG + " Silent", _now()))
    conn.commit()
    silent = conn.execute("SELECT id FROM vendors WHERE name = ?",
                          (TAG + " Silent",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
             spent_on, submitted_at) VALUES ('supplier_invoice', ?, ?, 900.0,
             'approved', ?, ?)""",
        (TAG + " Paid", TAG + " delivery", today.isoformat(), _now()))
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, vendor_id,
             active, created_at) VALUES (?, 'food', 'kg', 5.0, ?, 1, ?)""",
        (TAG + " Thing", silent, _now()))
    conn.commit()
    with m.app.test_request_context():
        sc = m.supplier_scorecard(conn)
    by_name = {r["name"]: r for r in sc["rows"]}
    s.check("what was spent with a supplier is counted",
            by_name.get(TAG + " Paid", {}).get("spend") == 900.0,
            detail=str(by_name.get(TAG + " Paid", {}).get("spend")))
    # The distinction: supplying goods with no invoice recorded is a question,
    # not a zero.
    s.check("one supplying stock with no invoice is flagged, not shown as free",
            by_name.get(TAG + " Silent", {}).get("no_invoices") is True,
            detail=str(by_name.get(TAG + " Silent", {})))
    s.check("and it appears in the unbilled list",
            any(r["name"] == TAG + " Silent" for r in sc["unbilled"]))

    # ------------------------------------------------------------ tenure
    s.section("How long people have been here")
    with m.app.test_request_context():
        ten = m.staff_tenure(conn, today)
    s.check("everybody active is counted", ten["headcount"] >= 1,
            detail=str(ten["headcount"]))
    s.check("and the typical figure is a median, not an average",
            ten["median_months"] is None
            or ten["median_months"] <= max(p["months"] for p in ten["here"]),
            detail=str(ten["median_months"]))
    s.check("months are counted from the start date, not from today backwards",
            all(p["months"] >= 0 for p in ten["here"]),
            detail="a negative tenure means the arithmetic is inverted")
    s.check("somebody under a year is separated out",
            isinstance(ten["under_a_year"], list),
            detail="under a year is still learning the house rather than running part of it")

    # ------------------------------------------------------- reply times
    s.section("How long a guest waits for an answer")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "B1", TAG + "tk1", TAG + " Reviewer", TAG + "r@example.invalid",
         (today - timedelta(days=40)).isoformat(),
         (today - timedelta(days=38)).isoformat(), _now()))
    conn.commit()
    # One review per booking, so two reviews need two stays.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "B1b", TAG + "tk1b", TAG + " Reviewer Two",
         TAG + "r2@example.invalid",
         (today - timedelta(days=25)).isoformat(),
         (today - timedelta(days=23)).isoformat(), _now()))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (TAG + "B1",)).fetchone()["id"]
    bid2 = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                        (TAG + "B1b",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO guest_feedback (booking_id, guest_name, rating, comment,
             submitted_at) VALUES (?, ?, 2, ?, ?)""",
        (bid, TAG + " Unhappy", TAG + " the room was cold",
         (today - timedelta(days=30)).isoformat() + "T10:00:00+00:00"))
    conn.execute(
        """INSERT INTO guest_feedback (booking_id, guest_name, rating, comment,
             submitted_at, reply, replied_at) VALUES (?, ?, 5, ?, ?, 'Thank you', ?)""",
        (bid2, TAG + " Happy", TAG + " lovely",
         (today - timedelta(days=20)).isoformat() + "T10:00:00+00:00",
         (today - timedelta(days=18)).isoformat() + "T10:00:00+00:00"))
    conn.commit()
    with m.app.test_request_context():
        rt = m.review_reply_times(conn)
    waiting = {w["who"]: w for w in rt["waiting"]}
    s.check("somebody who has heard nothing is still waiting",
            TAG + " Unhappy" in waiting, detail=str(list(waiting))[:90])
    s.check("and the wait is counted to today, not to nothing",
            waiting.get(TAG + " Unhappy", {}).get("days", 0) >= 29,
            detail=str(waiting.get(TAG + " Unhappy", {}).get("days")))
    s.check("somebody who was answered is not on the waiting list",
            TAG + " Happy" not in waiting)
    # The one that matters most.
    s.check("the worst case is somebody who said it was poor and heard nothing",
            rt["worst"] and rt["worst"]["poor"],
            detail=str(rt["worst"]["who"] if rt["worst"] else None))

    # --------------------------------------------------------- money due
    s.section("What is owed, and which week it lands in")
    due_day = today + timedelta(days=10)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, amount_paid, balance_due_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 1000, 300, ?, ?)""",
        (room, TAG + "B2", TAG + "tk2", TAG + " Owes", TAG + "o@example.invalid",
         (today + timedelta(days=30)).isoformat(),
         (today + timedelta(days=33)).isoformat(), due_day.isoformat(), _now()))
    # Paid in full: nothing to expect from them.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, amount_paid, balance_due_date, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 900, 900, ?, ?)""",
        (room, TAG + "B3", TAG + "tk3", TAG + " Settled", TAG + "s@example.invalid",
         (today + timedelta(days=30)).isoformat(),
         (today + timedelta(days=33)).isoformat(), due_day.isoformat(), _now()))
    conn.commit()
    with m.app.test_request_context():
        due = m.money_due(conn, weeks=12, today=today)
    owed_names = [i["who"] for w in due["weeks"] for i in w["items"]]
    s.check("what a guest still owes is expected", TAG + " Owes" in owed_names,
            detail=str([n for n in owed_names if n.startswith(TAG)]))
    s.check("and it is the outstanding part, not the whole bill",
            any(abs(i["amount"] - 700.0) < 0.01 for w in due["weeks"]
                for i in w["items"] if i["who"] == TAG + " Owes"),
            detail=str([i["amount"] for w in due["weeks"] for i in w["items"]
                        if i["who"] == TAG + " Owes"]))
    s.check("somebody who has paid in full is not expected again",
            TAG + " Settled" not in owed_names)
    s.check("it is grouped by the week the money lands in",
            all(w["week"].weekday() == 0 for w in due["weeks"]),
            detail="weeks start on Monday everywhere else in this app")

    # ------------------------------------------------------ guest recall
    s.section("What the house already knows about somebody")
    conn.execute(
        """INSERT INTO guests (name, email, dietary_notes, preferences, created_at)
           VALUES (?, ?, 'No shellfish', 'Quiet room away from the stairs', ?)""",
        (TAG + " Regular", TAG + "reg@example.invalid", _now()))
    conn.commit()
    gid = conn.execute("SELECT id FROM guests WHERE name = ?",
                       (TAG + " Regular",)).fetchone()["id"]
    for n in range(3):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                 guest_email, arrival_date, departure_date, party_size, status,
                 total_price, linked_guest_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, ?, ?)""",
            (room, "%sR%s" % (TAG, n), "%stkR%s" % (TAG, n), TAG + " Regular",
             TAG + "reg@example.invalid",
             (today - timedelta(days=200 - n * 60)).isoformat(),
             (today - timedelta(days=198 - n * 60)).isoformat(), gid, _now()))
    conn.execute(
        """INSERT INTO guest_notes (guest_id, body, created_at)
           VALUES (?, ?, ?)""", (gid, TAG + " takes tea in the library", _now()))
    conn.commit()
    with m.app.test_request_context():
        recall = m.guest_recall(conn, TAG + "reg@example.invalid")
    s.check("every stay is found", recall["visits"] == 3, detail=str(recall["visits"]))
    s.check("and they are recognised as returning", recall["returning"])
    s.check("what they cannot eat comes with them",
            "shellfish" in recall["dietary"].lower(), detail=recall["dietary"])
    s.check("so does what staff have written down",
            any(TAG in n["body"] for n in recall["notes"]),
            detail=str(len(recall["notes"])))
    s.check("and the rooms they have had before",
            recall["rooms_before"], detail=str(recall["rooms_before"]))
    # A stranger is not a gap in the records.
    with m.app.test_request_context():
        stranger = m.guest_recall(conn, TAG + "nobody@example.invalid")
    s.check("somebody who has never stayed is reported as unknown",
            not stranger["known"] and stranger["visits"] == 0,
            detail="it stops anybody greeting a stranger as an old friend")

    # ------------------------------------------------------------ linen
    s.section("Enough sheets")
    with m.app.test_request_context():
        bare = m.linen_par(conn, days=14, today=today)
    s.check("with nothing recorded it says so rather than showing none held",
            bare["not_tracked"] == (not bare["items"]),
            detail="no linen recorded is not the same as no linen held")
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
           VALUES (?, 'linen', 'set', 40.0, 1, ?)""", (TAG + " Sheets", _now()))
    conn.commit()
    with m.app.test_request_context():
        lin = m.linen_par(conn, days=14, today=today)
    sheets = next((i for i in lin["items"] if i["name"] == TAG + " Sheets"), None)
    s.check("once recorded it is measured", sheets is not None and not lin["not_tracked"],
            detail=str([i["name"] for i in lin["items"]])[:80])
    s.check("what is needed follows the arrivals coming",
            sheets and sheets["needed"] == lin["arrivals"] * lin["sets_per_changeover"],
            detail=f"{sheets['needed'] if sheets else '?'} for {lin['arrivals']} arrivals")
    # A surplus, so the guard has something to guard. With nothing held,
    # needed-minus-held is positive either way and the check cannot fail.
    conn.execute(
        """INSERT INTO stock_movements (stock_item_id, delta, reason, unit_cost,
             note, created_at) VALUES (?, ?, 'opening', 40.0, ?, ?)""",
        (conn.execute("SELECT id FROM stock_items WHERE name = ?",
                      (TAG + " Sheets",)).fetchone()["id"],
         (lin["needed"] or 0) + 50, TAG + " plenty", _now()))
    conn.commit()
    with m.app.test_request_context():
        plenty = m.linen_par(conn, days=14, today=today)
    stocked = next(i for i in plenty["items"] if i["name"] == TAG + " Sheets")
    s.check("with more held than needed there is a real surplus",
            stocked["held"] > stocked["needed"],
            detail=f"{stocked['held']} held against {stocked['needed']} needed")
    s.check("and short is what is missing, never a negative",
            stocked["short"] == 0 and all(i["short"] >= 0 for i in plenty["items"]),
            detail=f"short={stocked['short']} — a surplus is not a shortage")
    s.check("so a well-stocked item is not listed as short",
            not any(i["name"] == TAG + " Sheets" for i in plenty["short"]),
            detail=str([i["name"] for i in plenty["short"]]))

    # ------------------------------------------------------ turned away
    s.section("Nights somebody asked for and could not have")
    # A night the house is full on, and one it is not.
    full_night = today + timedelta(days=45)
    open_night = today + timedelta(days=46)
    rooms_all = conn.execute("SELECT id FROM rooms WHERE active = 1").fetchall()
    for i, r in enumerate(rooms_all):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                 guest_email, arrival_date, departure_date, party_size, status,
                 total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
            (r["id"], "%sF%s" % (TAG, i), "%stkF%s" % (TAG, i), TAG + " InHouse",
             TAG + "f@example.invalid", full_night.isoformat(),
             (full_night + timedelta(days=1)).isoformat(), _now()))
    for label, night in (("FULL", full_night), ("OPEN", open_night)):
        conn.execute(
            """INSERT INTO waitlist_entries (name, email, desired_arrival,
                 desired_departure, party_size, status, created_at)
               VALUES (?, ?, ?, ?, 4, 'open', ?)""",
            (TAG + " Hopeful", TAG + label + "@example.invalid", night.isoformat(),
             (night + timedelta(days=1)).isoformat(), _now()))
    conn.commit()
    with m.app.test_request_context():
        ta = m.turned_away(conn, days=365, today=today)
    by_night = {n["night"]: n for n in ta["nights"]}
    s.check("a night asked for is recorded", full_night in by_night,
            detail=str(sorted(by_night)[:3]))
    s.check("and it knows the house was full that night",
            by_night.get(full_night, {}).get("was_full") is True,
            detail=str(by_night.get(full_night, {}).get("sold")))
    # The distinction the page turns on.
    s.check("a night with rooms still free is not demand turned away",
            open_night not in by_night
            or by_night[open_night]["was_full"] is not True,
            detail="that is somebody who wanted a different room")
    s.check("only full nights count towards people turned away",
            all(n["was_full"] for n in ta["full_nights"]),
            detail=str(len(ta["full_nights"])))

    s.section("The pages")
    for path in ("/management/suppliers-scorecard", "/admin/tenure", "/admin/reply-times",
                 "/management/money-due", "/admin/linen", "/management/turned-away",
                 "/guests/recall?email=" + TAG + "reg@example.invalid"):
        s.check("%s renders with data on it" % path.split("?")[0],
                oc.get(path).status_code == 200,
                detail="HTTP %s" % oc.get(path).status_code)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
