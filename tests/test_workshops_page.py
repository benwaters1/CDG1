"""The workshops pages, overhauled (owner, 19 October 2026: "the workshop page
needs full overhaul").

The list was every workshop as a long card holding its sittings, its
materials form, its questions form and every action on every sitting, so
finding which sitting needed a telephone call meant reading all of it.

WHAT THIS PINS.
  - Each sitting says how it stands -- Needs you, Filling, Full, Called off,
    Over -- and, when it needs somebody, in words why: below its minimum,
    waiting on a decision, a deposit not paid, guests close to the date with
    no room.
  - The list opens on what needs somebody, then every coming sitting in one
    list with counted chips, then the ateliers as cards.
  - Setting a workshop up is on its own page: its sittings, what it uses up,
    what it asks. Running a sitting is on the sitting's: dates, places, the
    minimum, the teaching link, calling it off. Every form there goes back to
    the page it was pressed on.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWSPG"


def _cleanup():
    conn = db()
    for r in conn.execute("SELECT wb.id FROM workshop_bookings wb JOIN workshop_sessions ws "
                          "ON ws.id = wb.session_id JOIN workshops w ON w.id = ws.workshop_id "
                          "WHERE w.title LIKE ?", (TAG + "%",)).fetchall():
        conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id = ?", (r["id"],))
        conn.execute("DELETE FROM workshop_bookings WHERE id = ?", (r["id"],))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _session(conn, wid, start, days=3, capacity=4, minimum=None, cancelled=False):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
               min_participants, decide_by_days, cancelled_at, created_at)
           VALUES (?, ?, ?, ?, ?, 21, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=days)).isoformat(), capacity,
         minimum or 0, now if cancelled else None, now))
    return cur.lastrowid


def _reg(conn, sid, key, status="confirmed", deposit_paid=True):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, party_size, status, total_price, created_at, occupancy_type,
               deposit_amount, deposit_paid_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, 1000, ?, 'double', 300, ?)""",
        (sid, f"WRK-{TAG}{key.upper()}", f"tok{TAG}{key}", f"{TAG} {key}",
         f"zz.wspg.{key}@example.invalid", status, now, now if deposit_paid else None))


def run():
    s = Suite("The workshops pages")
    _cleanup()
    oc, ec, _o, _e = clients()
    today = m.house_today()
    try:
        _checks(s, oc, ec, today)
    finally:
        _cleanup()
    return s


def _checks(s, oc, ec, today):
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                        active, created_at) VALUES (?, 'x', 1000, 4, 1, ?)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    short = _session(conn, wid, today + timedelta(days=25), minimum=3)        # one in, three needed
    _reg(conn, short, "short1")
    _reg(conn, short, "short2", status="pending", deposit_paid=False)
    full = _session(conn, wid, today + timedelta(days=200), capacity=2)
    _reg(conn, full, "full1")
    _reg(conn, full, "full2")
    conn.execute("UPDATE workshop_bookings SET assigned_room_id = (SELECT id FROM rooms LIMIT 1) "
                 "WHERE session_id = ?", (full,))
    filling = _session(conn, wid, today + timedelta(days=260), capacity=6)
    off = _session(conn, wid, today + timedelta(days=300), cancelled=True)
    over = _session(conn, wid, today - timedelta(days=40))
    _reg(conn, over, "over1")
    conn.commit()

    s.section("How each sitting stands")
    with m.app.test_request_context():
        board = {b["session"]["id"]: b for b in m.workshop_sessions_board(conn, today)}
    s.check("below its minimum, it needs you", board[short]["state"] == "Needs you")
    needs = " | ".join(board[short]["needs"])
    s.check("and says why, in words",
            "1 of 3 needed to run" in needs and "waiting on you to confirm" in needs
            and "deposit" in needs and "without a room" in needs, detail=needs)
    s.check("full", board[full]["state"] == "Full", detail=str(board[full]["needs"]))
    s.check("filling", board[filling]["state"] == "Filling")
    s.check("called off", board[off]["state"] == "Called off" and not board[off]["needs"])
    s.check("over", board[over]["state"] == "Over" and board[over]["past"])
    s.check("a full sitting's bar is full", board[full]["fill"] == 100)

    s.section("The list")
    page = oc.get("/admin/workshops").get_data(as_text=True)
    s.check("opens on what needs somebody", "Needs you</h2>" in page
            and page.find("Needs you</h2>") < page.find('id="coming"'))
    s.check("with the sitting named and why", "1 of 3 needed to run" in page)
    s.check("then every coming sitting, with counted chips", 'id="coming"' in page
            and "How it stands" in page)
    s.check("the ateliers as cards, each opening its own page",
            f"/admin/workshops/{wid}/edit" in page and 'class="ws-card"' in page)
    s.check("the setting-up forms are no longer on the list",
            'name="qty_per_person"' not in page and 'name="field_type"' not in page)
    chip = oc.get("/admin/workshops", query_string={"state": "Full"}).get_data(as_text=True)
    s.check("a chip filters to its sittings", f"/admin/workshops/sessions/{full}" in chip
            and f"/admin/workshops/sessions/{filling}\"" not in chip)
    s.check("and the past is one click away", f"/admin/workshops/sessions/{over}" in page
            and 'id="past"' in page)
    s.check("staff without the owner's rights are turned away",
            ec.get("/admin/workshops").status_code in (302, 403))

    s.section("The workshop's own page: setting it up")
    setup = oc.get(f"/admin/workshops/{wid}/edit").get_data(as_text=True)
    s.check("its sittings, what it uses up, what it asks, then its details",
            all(x in setup for x in ('id="sittings"', 'id="materials"', 'id="questions"', 'id="details"')))
    s.check("each coming sitting with how it stands",
            f"/admin/workshops/sessions/{short}" in setup and "needs you" in setup)
    r = oc.post(f"/admin/workshops/{wid}/sessions/new", data={
        "start_date": (today + timedelta(days=400)).isoformat(), "capacity": "5",
        "back": f"workshop:{wid}"})
    s.check("adding a sitting there comes back there",
            r.headers.get("Location", "").endswith(f"/admin/workshops/{wid}/edit"),
            detail=r.headers.get("Location", ""))
    r = oc.post(f"/admin/workshops/{wid}/edit", data={"title": f"{TAG} Atelier", "price_per_person": "1000",
                                                     "default_capacity": "4", "active": "on"})
    s.check("and so does saving its details",
            r.headers.get("Location", "").endswith(f"/admin/workshops/{wid}/edit"))

    s.section("The sitting's own page: running it")
    url = f"/admin/workshops/sessions/{short}"
    sess = oc.get(url).get_data(as_text=True)
    s.check("says what it needs", "1 of 3 needed to run" in sess)
    s.check("and carries its dates, places, minimum and calling off",
            'id="manage"' in sess and 'name="min_participants"' in sess and "Call it off" in sess)
    row = conn.execute("SELECT * FROM workshop_sessions WHERE id = ?", (short,)).fetchone()
    r = oc.post(f"/admin/workshops/sessions/{short}/edit", data={
        "start_date": row["start_date"], "end_date": row["end_date"], "capacity": "5",
        "min_participants": "2", "decide_by_days": "14", "notes": "Moved to the barn",
        "back": f"session:{short}"})
    s.check("a change there comes back there", r.headers.get("Location", "").endswith(url),
            detail=r.headers.get("Location", ""))
    row = conn.execute("SELECT * FROM workshop_sessions WHERE id = ?", (short,)).fetchone()
    s.check("with the places, the minimum and the deciding days saved",
            row["capacity"] == 5 and row["min_participants"] == 2 and row["decide_by_days"] == 14
            and row["notes"] == "Moved to the barn")
    oc.post(f"/admin/workshops/sessions/{short}/edit", data={
        "start_date": row["start_date"], "end_date": row["end_date"], "capacity": "5"})
    row = conn.execute("SELECT * FROM workshop_sessions WHERE id = ?", (short,)).fetchone()
    s.check("a form that does not send the minimum leaves it alone", row["min_participants"] == 2)
    r = oc.post(f"/admin/workshops/session/{short}/call-off", data={
        "reason": "Not enough for the kiln", "back": f"session:{short}"})
    s.check("calling it off there comes back there", r.headers.get("Location", "").endswith(url))
    conn.close()


if __name__ == "__main__":
    print(run().report())
