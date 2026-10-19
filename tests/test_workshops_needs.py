"""The workshops page says what needs somebody, and its list can be searched.

19 October 2026: the page was rebuilt to lead with every sitting by date. Two
things it still left to the reader: which sittings need a person -- a deposit
not paid, a balance past due, guests a month away with no room -- were only
found by opening each one; and its list was the one in the house without the
search and counted chips every list has (list_view). And the date for
deciding was written 2026-10-23, the database's way.

WHAT THIS PINS.
  - A "Needs you" panel above the list naming each sitting and, in words,
    what it needs. A sitting that needs nothing is not on it.
  - Each sitting's state -- Needs you, Filling, Full, Called off -- as
    counted chips that filter the list, and a search.
  - The decide-by date written for people.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZWSNEED"


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


def _session(conn, wid, start, capacity=4, minimum=0, cancelled=False):
    now = datetime.now(timezone.utc).isoformat()
    return conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
               min_participants, decide_by_days, cancelled_at, created_at)
           VALUES (?, ?, ?, ?, ?, 21, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), capacity, minimum,
         now if cancelled else None, now)).lastrowid


def _reg(conn, sid, key, status="confirmed", deposit_paid=True, room=False):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, party_size, status, total_price, created_at, occupancy_type,
               deposit_amount, deposit_paid_at, assigned_room_id)
           VALUES (?, ?, ?, ?, ?, 1, ?, 1000, ?, 'double', 300, ?,
                   CASE WHEN ? THEN (SELECT id FROM rooms ORDER BY id LIMIT 1) END)""",
        (sid, f"WRK-{TAG}{key.upper()}", f"tok{TAG}{key}", f"{TAG} {key}",
         f"zz.wsneed.{key}@example.invalid", status, now, now if deposit_paid else None,
         1 if room else 0))


def run():
    s = Suite("The workshops page says what needs somebody")
    _cleanup()
    oc = clients()[0]
    today = m.house_today()
    conn = db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                            active, created_at) VALUES (?, 'x', 1000, 4, 1, ?)""", (f"{TAG} Atelier", now))
        wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
        short = _session(conn, wid, today + timedelta(days=25), minimum=3)
        _reg(conn, short, "a", deposit_paid=False)
        _reg(conn, short, "b", status="pending", deposit_paid=False)
        full = _session(conn, wid, today + timedelta(days=200), capacity=2)
        _reg(conn, full, "c", room=True)
        _reg(conn, full, "d", room=True)
        quiet = _session(conn, wid, today + timedelta(days=260), capacity=6)
        off = _session(conn, wid, today + timedelta(days=300), cancelled=True)
        conn.commit()

        s.section("What each sitting needs")
        with m.app.test_request_context():
            w = conn.execute("SELECT * FROM workshops WHERE id = ?", (wid,)).fetchone()
            rows = {r["session"]["id"]: r for r in
                    m.workshop_sitting_rows(conn, [w], today)["upcoming"][wid]}
        needs = " | ".join(rows[short]["needs"])
        s.check("below its minimum, said with the date for deciding in words",
                "1 of 3 needed to run" in needs and not re.search(r"\d{4}-\d{2}-\d{2}", needs),
                detail=needs)
        s.check("with a deposit not paid, one to decide and guests a month off with no room",
                bool(re.search(r"deposits? not paid", needs)) and "to decide" in needs and "without a room" in needs,
                detail=needs)
        s.check("so it needs you", rows[short]["state"] == "Needs you")
        s.check("a full sitting with its rooms given out needs nothing",
                rows[full]["needs"] == [] and rows[full]["state"] == "Full", detail=str(rows[full]["needs"]))
        s.check("one filling quietly", rows[quiet]["state"] == "Filling")
        s.check("one called off needs nothing", rows[off]["needs"] == [] and rows[off]["state"] == "Called off")

        s.section("On the page")
        page = oc.get("/admin/workshops").get_data(as_text=True)
        panel = page[page.find("Needs you</h2>"):page.find('id="coming"')]
        s.check("a Needs you panel above the list", "Needs you</h2>" in page and bool(panel))
        s.check("naming the sitting that needs somebody, and why",
                f"/admin/workshops/sessions/{short}" in panel and bool(re.search(r"deposits? not paid", panel)))
        s.check("and not the ones that do not", f"/admin/workshops/sessions/{full}\"" not in panel)
        s.check("no decide-by date written the database's way",
                not re.search(r"[Dd]ecide by \d{4}-\d{2}-\d{2}", page))
        s.check("the list has the house's search and chips", "How it stands" in page
                and 'name="q"' in page)
        chip = oc.get("/admin/workshops", query_string={"state": "Full"}).get_data(as_text=True)
        listed = chip[chip.find('id="coming"'):]
        s.check("a chip filters the list", f"/admin/workshops/sessions/{full}" in listed
                and f"/admin/workshops/sessions/{quiet}\"" not in listed)
        found = oc.get("/admin/workshops", query_string={"q": TAG}).get_data(as_text=True)
        s.check("and a search finds by workshop", f"/admin/workshops/sessions/{quiet}" in found)

        s.section("The sitting's own page says the same")
        # Opened, a sitting below its minimum read as fine: the needs were on
        # the list and nowhere on the sitting.
        sit = oc.get(f"/admin/workshops/sessions/{short}").get_data(as_text=True)
        s.check("what it needs, in the list's words", "1 of 3 needed to run" in sit
                and bool(re.search(r"deposits? not paid", sit)) and "Nobody is waiting." in sit)
        s.check("and with people registered it offers calling off, not removing",
                f"/admin/workshops/sessions/{short}/delete" not in sit and "call it off instead" in sit)
        empty = oc.get(f"/admin/workshops/sessions/{quiet}").get_data(as_text=True)
        s.check("a sitting nobody has registered on can still be removed",
                f"/admin/workshops/sessions/{quiet}/delete" in empty)
        s.check("and one that needs nothing says nothing", "Needs you" not in empty.split("Who is coming")[0])
    finally:
        conn.close()
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
