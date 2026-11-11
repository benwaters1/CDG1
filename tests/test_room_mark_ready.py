"""Mark a room ready in one tap, from the Today page (owner, 10 Nov 2026).

"i want to easily be able to mark these myself as done, until we fully
implement the system". A room reads "Needs turning over" while its checklist
has open items (room_board); the button ticks off exactly those items, the
same way one tap each would (mark_task_done), with complete_task's rule on who
may: the owner, or whoever an item is assigned to. Held here:
  - the button is on a room with work outstanding, and not on a ready one
  - the owner's tap ticks every item off, and the room reads ready
  - only that room's work: the room next door is left as it was
  - somebody else's items are not ticked, and are named, as an error
  - a second tap says there was nothing left
  - it is a POST from somebody signed in, and nothing else
"""
import html
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTMRK"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM tasks WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _left_today(ref, room_id):
    """A stay whose guest left this morning: checked out, so its turnover checklist is raised."""
    conn = db()
    today = datetime.now(m.LOCAL_TZ).date()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room_id, TAG + ref, TAG.lower() + "tok" + ref, TAG + " Guest " + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid",
         (today - timedelta(days=2)).isoformat(), today.isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (TAG + ref,)).fetchone()
    conn.close()
    return row


def _board():
    conn = db()
    try:
        with m.app.test_request_context():
            return {r["room"]["id"]: r for r in m.room_board(conn, datetime.now(m.LOCAL_TZ).date())}
    finally:
        conn.close()


def _open(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT COUNT(*) FROM tasks WHERE booking_id = ? AND origin = 'checklist' "
                            "AND status != 'done'", (booking_id,)).fetchone()[0]
    finally:
        conn.close()


def run():
    s = Suite("Mark a room ready")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    conn = db()
    rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY sort_order, name LIMIT 3").fetchall()
    conn.close()
    if not s.check("there are rooms to turn over", len(rooms) >= 3, detail=str(len(rooms))):
        return s
    (first, first_name), (second, _n2), (spare, _n3) = [(r["id"], r["name"]) for r in rooms]
    try:
        stay_one, stay_two = _left_today("ONE", first), _left_today("TWO", second)
        for stay in (stay_one, stay_two):
            oc.post(f"/admin/bookings/{stay['id']}/checkout", data={"assigned_to_user_id": str(emp["id"])})
        b = _board()
        s.check("two rooms read as needing turning over",
                b[first]["state"] == "turnover" and b[second]["state"] == "turnover",
                detail=f"{b[first]['state']} {b[second]['state']}")

        s.section("The button")
        page = oc.get("/today").get_data(as_text=True)
        s.check("a room with work outstanding has it", f'action="/rooms/{first}/ready"' in page)
        s.check("a ready room does not", b[spare]["outstanding"] == 0 and f'action="/rooms/{spare}/ready"' not in page,
                detail=str(b[spare]["outstanding"]))
        s.check("and it reads in French and Spanish too",
                m.translations.FR.get("Mark ready") == "Marquer prête"
                and m.translations.ES.get("Mark ready") == "Marcar lista")

        s.section("Somebody else's items are not theirs to tick")
        conn = db()
        conn.execute("UPDATE tasks SET assigned_to_user_id = ? WHERE booking_id = ? AND origin = 'checklist'",
                     (owner["id"], stay_one["id"]))
        conn.commit()
        conn.close()
        before = _open(stay_one["id"])
        r = ec.post(f"/rooms/{first}/ready", follow_redirects=True)
        said = html.unescape(" ".join(flashes(r)))
        s.check("an employee's tap on the owner's items ticks none of them", _open(stay_one["id"]) == before > 0,
                detail=f"{before} -> {_open(stay_one['id'])}")
        s.check("and says so, naming them", "Nothing was ticked off" in said and "somebody else's to tick" in said,
                detail=said[:200])

        s.section("The owner's tap")
        r = oc.post(f"/rooms/{first}/ready", follow_redirects=True)
        said = html.unescape(" ".join(flashes(r)))
        b = _board()
        s.check("ticks every item off", _open(stay_one["id"]) == 0, detail=str(_open(stay_one["id"])))
        s.check("and the room reads ready", b[first]["state"] == "ready", detail=b[first]["state"])
        s.check("says how many, by the room's name", said.startswith(f"{first_name}: Ticked off {before} item"),
                detail=said[:160])
        conn = db()
        stamped = conn.execute("SELECT COUNT(*) FROM tasks WHERE booking_id = ? AND origin = 'checklist' "
                               "AND status = 'done' AND completed_at IS NOT NULL", (stay_one["id"],)).fetchone()[0]
        conn.close()
        s.check("each ticked as one tap would: done, with the time it was done", stamped == before,
                detail=f"{stamped} of {before}")
        s.check("only that room: the room next door still needs turning over",
                _open(stay_two["id"]) > 0 and b[second]["state"] == "turnover", detail=b[second]["state"])

        s.section("The employee's own items")
        r = ec.post(f"/rooms/{second}/ready", follow_redirects=True)
        s.check("an employee ticks off the items assigned to them", _open(stay_two["id"]) == 0
                and _board()[second]["state"] == "ready", detail=" ".join(flashes(r))[:160])

        s.section("A second tap, and the wrong way in")
        r = oc.post(f"/rooms/{first}/ready", follow_redirects=True)
        s.check("a second tap says there was nothing left", "nothing was left to do" in " ".join(flashes(r)),
                detail=" ".join(flashes(r))[:160])
        s.check("it is not a link: a GET is refused", oc.get(f"/rooms/{first}/ready").status_code == 405)
        _left_today("THREE", spare)
        conn = db()
        three = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (TAG + "THREE",)).fetchone()["id"]
        conn.close()
        oc.post(f"/admin/bookings/{three}/checkout", data={"assigned_to_user_id": str(emp["id"])})
        open_before = _open(three)
        r = anon.post(f"/rooms/{spare}/ready")
        s.check("nobody signed in ticks nothing", r.status_code in (302, 303) and _open(three) == open_before > 0,
                detail=f"{r.status_code}, {open_before} -> {_open(three)}")
        s.check("a room that does not exist is a 404", oc.post("/rooms/987654/ready").status_code == 404)
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
