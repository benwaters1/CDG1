"""Which rooms can be walked into, on a morning with three out and three in.

Checking a guest out assigns a five-item turnover checklist. Prepping an
arrival assigns eight more. Nothing anywhere said THE CHAMBRE BLEUE IS READY —
the only way to know was to find the tasks and see whether they were ticked.
Vehicles have carried how clean they are since they were built; rooms carried
nothing.

THE STATE IS DERIVED FROM THE WORK, and that is the point of this file. A
room_status column would be a second thing to keep in agreement with the
tasks, and the two would part company the first time somebody ticked the last
item and forgot the dropdown. So the checks below are mostly about the two
being the same fact: tick the work, the room turns ready, with nothing else
touched.

It needed the link tasks has always had and the two places that make these
tasks never set — booking_id. Without it the room could only be guessed from a
prefix on the task's title.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTBRD"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM tasks WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, room_id, arrive_offset, depart_offset):
    conn = db()
    today = datetime.now(m.LOCAL_TZ).date()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room_id, TAG + ref, TAG.lower() + "tok" + ref, TAG + " Guest " + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid",
         (today + timedelta(days=arrive_offset)).isoformat(),
         (today + timedelta(days=depart_offset)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _board(today=None):
    conn = db()
    try:
        with m.app.test_request_context():
            return {r["room"]["id"]: r
                    for r in m.room_board(conn, today or datetime.now(m.LOCAL_TZ).date())}
    finally:
        conn.close()


def run():
    s = Suite("Which rooms are ready")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    conn = db()
    rooms = conn.execute(
        "SELECT id, name FROM rooms WHERE active = 1 ORDER BY sort_order, name LIMIT 4"
    ).fetchall()
    conn.close()
    s.check("there are enough rooms to tell the states apart", len(rooms) >= 4,
            detail=f"{len(rooms)}")
    if len(rooms) < 4:
        return s
    occupied_room, turnover_room, arriving_room, spare_room = [r["id"] for r in rooms[:4]]

    s.section("A room with somebody in it")
    _stay("STAY", occupied_room, -1, 3)
    b = _board()
    s.check("reads as occupied", b[occupied_room]["state"] == "occupied",
            detail=b[occupied_room]["state"])
    s.check("and names who is in it",
            b[occupied_room]["in_house"]["guest_name"].startswith(TAG))

    s.section("A room nobody is in and nobody is due")
    s.check("reads as ready", b[spare_room]["state"] == "ready",
            detail=b[spare_room]["state"])
    s.check("with nothing outstanding", b[spare_room]["outstanding"] == 0)

    s.section("A room somebody has just left")
    left = _stay("LEFT", turnover_room, -3, 0)
    r = oc.post(f"/admin/bookings/{left['id']}/checkout",
                data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    b = _board()
    s.check("the turnover checklist was raised",
            b[turnover_room]["outstanding"] >= 5, detail=str(flashes(r)))
    s.check("and the room reads as needing turning over",
            b[turnover_room]["state"] == "turnover",
            detail=b[turnover_room]["state"])
    s.check("the work is tied to the stay, not guessed from the title",
            all(t["booking_id"] == left["id"] for t in b[turnover_room]["open_work"]),
            detail="booking_id has been on the tasks table the whole time and "
                   "neither checklist set it")

    s.section("Ticking the work is what makes it ready")
    # The whole reason there is no status column: there is one fact, so there
    # is nothing to keep in step.
    conn = db()
    conn.execute("UPDATE tasks SET status = 'done' WHERE booking_id = ? AND origin = 'checklist'",
                 (left["id"],))
    conn.commit()
    conn.close()
    b = _board()
    s.check("the room turns ready with nothing else touched",
            b[turnover_room]["state"] == "ready",
            detail=b[turnover_room]["state"] + " — no second field was set, "
                   "because there is no second field")
    s.check("and nothing is outstanding", b[turnover_room]["outstanding"] == 0)

    s.section("A room with somebody coming today")
    _stay("SOON", arriving_room, 0, 3)
    b = _board()
    s.check("reads as ready with a guest today",
            b[arriving_room]["state"] == "arriving", detail=b[arriving_room]["state"])
    s.check("naming who is coming",
            b[arriving_room]["arriving"]["guest_name"].startswith(TAG))
    s.check("and it is not urgent, because the room is done",
            not b[arriving_room]["urgent"])

    s.section("The one that means somebody has to run")
    # A guest arriving today into a room with work still open. This is the
    # only line on the board that changes what anybody does in the next hour.
    gone = _stay("BOTH", arriving_room, -4, 0)
    oc.post(f"/admin/bookings/{gone['id']}/checkout",
            data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    b = _board()
    s.check("the room reads as needing turning over",
            b[arriving_room]["state"] == "turnover", detail=b[arriving_room]["state"])
    s.check("and it is flagged urgent", b[arriving_room]["urgent"],
            detail="somebody arrives today and the room is not done")
    s.check("while an ordinary turnover with nobody coming is not",
            not _board()[turnover_room]["urgent"],
            detail="if everything were urgent the flag would mean nothing")

    s.section("It is on the page staff actually open")
    r = ec.get("/today")
    body = r.get_data(as_text=True)
    s.check("the staff page opens", r.status_code == 200, detail=str(r.status_code))
    s.check("the board is on it", "Rooms" in body,
            detail="a board nobody sees is a query")
    s.check("naming a room", rooms[0]["name"] in body)
    s.check("and saying what is still to do",
            "still to do" in body or "Needs turning over" in body,
            detail=body[:200] if "still to do" not in body else "")

    s.section("An employee sees it, which is the point")
    # This is the employee side of the two-tier split: tasks, who is here, and
    # now which rooms are ready. It is not owner-only.
    s.check("an employee gets the board", "Rooms" in body,
            detail="the gardener walking past a room needs this more than the "
                   "owner does")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
