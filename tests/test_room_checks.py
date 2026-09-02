"""Somebody walks the room before the guest does.

The house had room_issues — a fault somebody happened to notice and bothered
to write down — and cleaning_rounds, the deep clean on a cycle. Neither is
the thing that actually happens at four o'clock, which is somebody walking
into a room that has just been turned round and looking at it against a list
before a guest opens the door on it.

Four things this holds, and they are the four that decide whether a check is
worth doing at all:

  - A FAULT FOUND IS A FAULT REPORTED. Every failed line becomes a room
    issue, because the person who can fix a dripping tap does not read a
    quality log. A checklist that only records is the one that gets quietly
    abandoned, and this is the check that would notice it happening.
  - UNTICKED MEANS NOT RIGHT, not "not looked at". A half-filled form comes
    out as a room with faults rather than a clean one, which is the safer way
    round: somebody who stopped halfway has not said the rest was fine.
  - UNCHECKED IS NOT PASSED. A room nobody has walked reads as unchecked, not
    as fine. Same rule as a fridge with no range set.
  - AND THE CHECK IS AGAINST THE ARRIVAL, not the room. A pass from three
    weeks ago says nothing about the room somebody opens the door on tonight.

Retiring a line from the standard leaves every past check alone. A check is a
record of what somebody actually looked at on the day, and rewriting the list
must not rewrite that.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZWALK"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM room_check_items WHERE check_id IN "
        "(SELECT id FROM room_checks WHERE room_id IN "
        " (SELECT id FROM rooms WHERE name LIKE ?))", (TAG + "%",))
    conn.execute(
        "DELETE FROM room_checks WHERE room_id IN "
        "(SELECT id FROM rooms WHERE name LIKE ?)", (TAG + "%",))
    conn.execute(
        "DELETE FROM room_issues WHERE room_id IN "
        "(SELECT id FROM rooms WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_standards WHERE what LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("walking the room")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    oc, ec, _owner, emp = clients()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO rooms (name, description, max_occupancy, max_adults,
                   price_per_night, active, sort_order, export_token)
           VALUES (?, '', 2, 2, 200, 1, 95, ?)""",
        (TAG + " Rose Room", "tok-" + TAG.lower()))
    room = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def add_booking(ref, arrival, status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 400, ?)""",
            (room, TAG + ref, f"tok-{TAG}-{ref}".lower(), TAG + " " + ref.title(),
             f"{TAG}.{ref}@example.invalid".lower(), arrival.isoformat(),
             (arrival + timedelta(days=2)).isoformat(), status, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    tomorrow = add_booking("TOMORROW", today + timedelta(days=1))
    later = add_booking("LATER", today + timedelta(days=5))
    add_booking("PENDING", today + timedelta(days=1), status="pending")

    for i, (what, area) in enumerate((("Bed made", "Bed"),
                                      ("Hot water runs hot", "Bathroom"),
                                      ("Every light works", "The room"))):
        conn.execute(
            "INSERT INTO room_standards (what, area, sort_order, active, created_at) "
            "VALUES (?, ?, ?, 1, ?)", (TAG + " " + what, area, 900 + i, now))
    conn.commit()
    mine = [st for st in m.room_standards(conn) if st["what"].startswith(TAG)]

    s.section("A room nobody has walked reads as unchecked, not fine")
    rows = {a["booking"]["reference_code"]: a
            for a in m.arrivals_needing_check(conn, days=2, today=today)}
    s.check("tomorrow's arrival is on the list", TAG + "TOMORROW" in rows)
    s.check("and its state is unchecked",
            rows[TAG + "TOMORROW"]["state"] == "unchecked",
            detail="blank on a page reads as nothing to worry about, and "
                   "'nobody has looked' is not that")
    s.check("an arrival five days out is not in a two-day window",
            TAG + "LATER" not in rows)
    s.check("and an unconfirmed booking is not walked for",
            TAG + "PENDING" not in rows,
            detail="turning a room round for somebody who has not booked is "
                   "work nobody asked for")

    s.section("Walking it, and finding something")
    before_issues = conn.execute(
        "SELECT COUNT(*) FROM room_issues WHERE room_id = ?", (room,)).fetchone()[0]
    check_id, raised = m.record_room_check(
        conn, room_id=room, booking_id=tomorrow, for_date=today + timedelta(days=1),
        user_id=emp["id"] if emp else None,
        results=[(mine[0]["id"], mine[0]["what"], True, ""),
                 (mine[1]["id"], mine[1]["what"], False, "Runs cold for a minute"),
                 (mine[2]["id"], mine[2]["what"], False, "Left bedside bulb gone")],
        note=TAG + " walked at four")
    conn.commit()

    s.check("the walk is recorded", check_id is not None)
    s.check("it says two things were found", raised == 2, detail=str(raised))

    s.section("A fault found is a fault REPORTED")
    # The one that matters. A checklist that only records is the one that
    # gets quietly abandoned, because nothing ever comes of filling it in.
    issues = conn.execute(
        "SELECT * FROM room_issues WHERE room_id = ? ORDER BY id", (room,)).fetchall()
    s.check("two room issues were raised",
            len(issues) == before_issues + 2, detail=str(len(issues)))
    titles = {i["title"] for i in issues}
    s.check("named after what was being looked at",
            any("Hot water" in t for t in titles), detail=str(titles))
    s.check("and what the person wrote is the description",
            any("Runs cold for a minute" in (i["description"] or "")
                for i in issues),
            detail=str([i["description"] for i in issues]))
    s.check("they are open, so somebody has to close them",
            all(i["status"] == "open" for i in issues))
    s.check("the check line points back at the issue it raised",
            conn.execute(
                "SELECT COUNT(*) FROM room_check_items "
                "WHERE check_id = ? AND passed = 0 AND room_issue_id IS NOT NULL",
                (check_id,)).fetchone()[0] == 2,
            detail="without the link the two records drift apart and nobody "
                   "can tell which fault came from which walk")
    s.check("and nothing was raised for the line that passed",
            conn.execute(
                "SELECT COUNT(*) FROM room_check_items "
                "WHERE check_id = ? AND passed = 1 AND room_issue_id IS NOT NULL",
                (check_id,)).fetchone()[0] == 0)

    s.section("The wording is kept as it was on the day")
    conn.execute("UPDATE room_standards SET what = ? WHERE id = ?",
                 (TAG + " Hot water runs hot AND STAYS HOT", mine[1]["id"]))
    conn.commit()
    kept = conn.execute(
        "SELECT what FROM room_check_items WHERE check_id = ? AND standard_id = ?",
        (check_id, mine[1]["id"])).fetchone()["what"]
    s.check("rewording the standard does not rewrite the past check",
            "AND STAYS HOT" not in kept, detail=kept)
    s.check("it still says what was actually looked at",
            kept == TAG + " Hot water runs hot", detail=kept)

    s.section("And the state changes to 'something found'")
    rows = {a["booking"]["reference_code"]: a
            for a in m.arrivals_needing_check(conn, days=2, today=today)}
    s.check("it is no longer unchecked",
            rows[TAG + "TOMORROW"]["state"] == "failed",
            detail=rows[TAG + "TOMORROW"]["state"])
    s.check("with both faults against it",
            len(rows[TAG + "TOMORROW"]["failed"]) == 2)

    s.section("A clean walk reads as passed")
    m.record_room_check(
        conn, room_id=room, booking_id=tomorrow, for_date=today + timedelta(days=1),
        user_id=emp["id"] if emp else None,
        results=[(st["id"], st["what"], True, "") for st in mine])
    conn.commit()
    rows = {a["booking"]["reference_code"]: a
            for a in m.arrivals_needing_check(conn, days=2, today=today)}
    # Both walks can carry the same timestamp -- somebody changes a bulb and
    # goes straight back round -- and ordering on the timestamp alone left
    # SQLite to pick between them. It picked the older one, and the page
    # reported faults that had been dealt with five minutes earlier. The id
    # is the tiebreak now, and this is the check that found it.
    s.check("the most recent walk is the one that counts",
            rows[TAG + "TOMORROW"]["state"] == "passed",
            detail="a room walked again after the bulb was changed is a room "
                   "that passed, and the earlier walk is still on record")
    s.check("and the earlier walk is still there",
            conn.execute(
                "SELECT COUNT(*) FROM room_checks WHERE booking_id = ?",
                (tomorrow,)).fetchone()[0] == 2)

    s.section("A check belongs to the arrival, not the room")
    # Somebody else arriving into the same room next week has an unwalked
    # room, whatever last week's walk said.
    rows7 = {a["booking"]["reference_code"]: a
             for a in m.arrivals_needing_check(conn, days=7, today=today)}
    s.check("the later arrival into the same room is unchecked",
            rows7[TAG + "LATER"]["state"] == "unchecked",
            detail="a pass from last week is not a statement about the room "
                   "somebody opens the door on tonight")

    # ------------------------------------------------------------ the page
    s.section("The page")
    body = ec.get("/admin/room-checks?days=7").get_data(as_text=True)
    s.check("an employee can open it", TAG + " Rose Room" in body,
            detail="the person who walks the room is not the owner")
    s.check("it says plainly when nobody has walked one",
            "Nobody has walked it" in body)
    s.check("and the standard is editable on it",
            TAG + " Bed made" in body)

    s.section("Walking it through the form")
    r = ec.get(f"/admin/room-checks/{later}")
    s.check("the walk page opens", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("with a box for each line on the list",
            all(f'name="pass_{st["id"]}"' in r.get_data(as_text=True)
                for st in mine))

    before = conn.execute(
        "SELECT COUNT(*) FROM room_issues WHERE room_id = ?", (room,)).fetchone()[0]
    # Only the first ticked. The other two are LEFT ALONE, which is how a
    # half-filled form arrives, and it has to come out as faults.
    # Against the whole live list, not just this suite's three lines. The
    # house is seeded with a dozen of its own, and a check that assumed only
    # its own fixtures would have been arithmetic about the test rather than
    # about the code.
    active = len(m.room_standards(conn))
    ec.post(f"/admin/room-checks/{later}",
            data={f"pass_{mine[0]['id']}": "1",
                  f"note_{mine[2]['id']}": TAG + " through the form"},
            follow_redirects=True)
    after = conn.execute(
        "SELECT COUNT(*) FROM room_issues WHERE room_id = ?", (room,)).fetchone()[0]
    s.check("every unticked line becomes a fault, not a pass",
            after == before + (active - 1),
            detail=f"{before} then {after}, with {active} lines on the list "
                   "and one ticked")
    s.check("and what was typed against it becomes the description",
            conn.execute(
                "SELECT COUNT(*) FROM room_issues WHERE room_id = ? "
                "AND description LIKE ?", (room, "%through the form%")
            ).fetchone()[0] == 1)
    s.check("a line with nothing typed still says where it came from",
            conn.execute(
                "SELECT COUNT(*) FROM room_issues WHERE room_id = ? "
                "AND description LIKE ?",
                (room, "%room check before an arrival%")).fetchone()[0] >= 1,
            detail="an issue with a blank description is one nobody can act on")

    s.section("Retiring a line leaves the past alone")
    kept_items = conn.execute(
        "SELECT COUNT(*) FROM room_check_items WHERE standard_id = ?",
        (mine[0]["id"],)).fetchone()[0]
    oc.post("/admin/room-checks", data={"standard_id": str(mine[0]["id"])},
            follow_redirects=True)
    s.check("it comes off the list",
            mine[0]["what"] not in
            {st["what"] for st in m.room_standards(conn)})
    s.check("and every past check that used it is untouched",
            conn.execute(
                "SELECT COUNT(*) FROM room_check_items WHERE standard_id = ?",
                (mine[0]["id"],)).fetchone()[0] == kept_items,
            detail="a check is a record of what somebody actually looked at, "
                   "and rewriting the list must not rewrite that")

    s.section("There is a list to walk in the first place")
    # A blank standard is the state where this feature does nothing at all,
    # so the house starts with one rather than with a decision to make.
    s.check("the house is seeded with a starting list",
            len(m.DEFAULT_ROOM_STANDARDS) >= 8,
            detail=str(len(m.DEFAULT_ROOM_STANDARDS)))
    s.check("and seeding is a no-op once anything is there",
            m.seed_room_standards(conn) == 0,
            detail="running it again must not put back a line the house "
                   "deleted, nor duplicate the ones it kept")

    s.section("It is reachable")
    nav = ec.get("/").get_data(as_text=True)
    nav = nav[:nav.find("</nav>")] if "</nav>" in nav else nav
    s.check("in the employee's nav", "/admin/room-checks" in nav,
            detail="the person who walks the room cannot use the search box")
    s.check("and in the palette",
            "room_checks_page" in {e for _l, e, _k in m.PALETTE_PAGES})

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
