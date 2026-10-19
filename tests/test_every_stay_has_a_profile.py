"""Every confirmed stay has a guest profile (owner, 19 October 2026: "it
should have guest profile").

A stay could be confirmed without one -- made before profiles existed,
imported, or a desk booking whose confirmation was refused and then written
as confirmed anyway -- and it was then nowhere on the guest list.

WHAT THIS PINS.
  - link_stays_to_profiles gives each such stay its profile: the one with its
    address if there is one, else a new one with its name; and links it.
  - It is safe to run again: nothing changes, nothing is duplicated.
  - A stay whose profile was deleted is given one again.
  - A walk-in written as confirmed has its profile.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, free_window
import _harness

m = _harness.m
TAG = "ZZPROF"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ? OR email LIKE 'zzprof.%'", (TAG + "%",))
    conn.commit()


def _bare_stay(conn, room, a, name, email):
    """A confirmed stay written straight in, with no profile: as an import or
    an old row would be."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?)""",
        (room["id"], f"{TAG}{name.upper().replace(' ', '')}", f"tok-zzprof-{name}", f"{TAG} {name}",
         email, a.isoformat(), (a + timedelta(days=2)).isoformat(), now))
    conn.commit()
    return cur.lastrowid


def run():
    s = Suite("Every confirmed stay has a guest profile")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id LIMIT 1").fetchone()
    a = free_window(room["id"], 2, after_days=120)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, 'zzprof.known@example.invalid', ?)",
                 (f"{TAG} Known Person", now))
    conn.commit()
    known = conn.execute("SELECT id FROM guests WHERE email = 'zzprof.known@example.invalid'").fetchone()["id"]
    new_id = _bare_stay(conn, room, a, "New", "zzprof.new@example.invalid")
    match_id = _bare_stay(conn, room, a + timedelta(days=5), "Known", "ZZProf.Known@example.invalid")
    walkin_id = _bare_stay(conn, room, a + timedelta(days=10), "No Email", "")

    s.section("The repair")
    with m.app.test_request_context("/"):
        n = m.link_stays_to_profiles(conn)
    s.check("it links the three", n >= 3, detail=str(n))
    row = {r["id"]: r for r in conn.execute(
        "SELECT id, linked_guest_id FROM bookings WHERE id IN (?, ?, ?)", (new_id, match_id, walkin_id))}
    s.check("every one now has a profile", all(row[i]["linked_guest_id"] for i in (new_id, match_id, walkin_id)))
    s.check("an address already known joins its profile, whatever its capitals",
            row[match_id]["linked_guest_id"] == known)
    newp = conn.execute("SELECT * FROM guests WHERE id = ?", (row[new_id]["linked_guest_id"],)).fetchone()
    s.check("an unknown one gets a profile of its own, with its name and address",
            newp and newp["name"] == f"{TAG} New" and newp["email"] == "zzprof.new@example.invalid")
    nop = conn.execute("SELECT * FROM guests WHERE id = ?", (row[walkin_id]["linked_guest_id"],)).fetchone()
    s.check("so does one with no email, by its name", nop and nop["name"] == f"{TAG} No Email")

    s.section("Run again, it changes nothing")
    count = conn.execute("SELECT COUNT(*) FROM guests WHERE name LIKE ?", (TAG + "%",)).fetchone()[0]
    with m.app.test_request_context("/"):
        again = m.link_stays_to_profiles(conn)
    s.check("nothing to link", again == 0, detail=str(again))
    s.check("and no profile made twice", conn.execute(
        "SELECT COUNT(*) FROM guests WHERE name LIKE ?", (TAG + "%",)).fetchone()[0] == count)

    s.section("A stay whose profile was deleted is given one again")
    conn.execute("DELETE FROM guests WHERE id = ?", (row[new_id]["linked_guest_id"],))
    conn.commit()
    with m.app.test_request_context("/"):
        m.link_stays_to_profiles(conn)
    relinked = conn.execute("SELECT linked_guest_id FROM bookings WHERE id = ?", (new_id,)).fetchone()[0]
    s.check("relinked to a profile that exists", bool(conn.execute(
        "SELECT 1 FROM guests WHERE id = ?", (relinked,)).fetchone()))

    s.section("On the guest list")
    page = clients()[0].get("/guests", query_string={"q": TAG}).get_data(as_text=True)
    s.check("each of them is a row", all(f"{TAG} {n}" in page for n in ("New", "Known Person", "No Email")))


if __name__ == "__main__":
    print(run().report())
