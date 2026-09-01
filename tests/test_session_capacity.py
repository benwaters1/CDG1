"""How many places an atelier has, and being able to change it.

Capacity could only be set at the moment a session was created. There was no
edit route at all — only delete — so the only way to change the number was to
remove the session and make another, which throws away every registration
attached to it. Nobody would do that on purpose, so in practice the figure was
fixed forever at whatever the default happened to be the day it was made.

The rule worth testing hardest is the refusal. Places are sold against this
number, so a capacity set below the heads already coming does not un-sell
them: it makes every "spots left" figure on the public site negative and reads
as overbooked when it is only mis-typed.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztest-cap-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("""DELETE FROM workshop_bookings WHERE reference_code LIKE ?""", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE action = 'workshop_session_edited' AND target = ?",
                 ("ztest",))
    conn.commit()


def run():
    s = Suite("session capacity")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
                 (TAG + "atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (TAG + "atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""", (wid, _iso(60), _iso(63), TAG + "sess", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (TAG + "sess",)).fetchone()["id"]
    conn.commit()

    def cap():
        row = conn.execute("SELECT capacity FROM workshop_sessions WHERE id = ?", (sid,)).fetchone()
        return row["capacity"] if row else None

    s.section("The number can be changed at all")
    r = oc.post(f"/admin/workshops/sessions/{sid}/edit",
                data={"capacity": "15"}, follow_redirects=True)
    s.check("the owner can set the places on a session", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and it is stored", cap() == 15, detail=str(cap()))
    # Deleting and remaking was the only way before, and it takes the
    # registrations with it.
    s.check("the session itself survives the edit",
            conn.execute("SELECT 1 FROM workshop_sessions WHERE id = ?", (sid,)).fetchone()
            is not None)
    logged = conn.execute(
        """SELECT details FROM audit_log WHERE action = 'workshop_session_edited'
             AND target = ? ORDER BY id DESC LIMIT 1""", (str(sid),)).fetchone()
    # What places are sold against is not a settings tweak.
    s.check("the change is recorded, with what it was",
            logged is not None and "10" in (logged["details"] or "")
            and "15" in (logged["details"] or ""),
            detail=None if logged is None else logged["details"])

    s.section("It cannot be set below what is already booked")
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
             guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, 6, 'confirmed', 100, ?)""",
        (sid, TAG + "R1", TAG + "t1", TAG + "six", TAG + "a@example.invalid", now))
    conn.commit()
    r = oc.post(f"/admin/workshops/sessions/{sid}/edit",
                data={"capacity": "4"}, follow_redirects=True)
    s.check("a capacity under the heads already coming is refused",
            cap() == 15, detail=str(cap()))
    s.check("and it says how many are booked",
            any("6" in f for f in flashes(r)), detail=str(flashes(r)))
    # Down to exactly what is booked is fine — that is a closed session, not a
    # broken one.
    r = oc.post(f"/admin/workshops/sessions/{sid}/edit",
                data={"capacity": "6"}, follow_redirects=True)
    s.check("but down to exactly what is booked is allowed", cap() == 6, detail=str(cap()))

    s.section("Nonsense is refused rather than stored")
    codes = {}
    for bad in ("0", "-3", "", "twelve", "9.5", " "):
        got = oc.post(f"/admin/workshops/sessions/{sid}/edit",
                      data={"capacity": bad}, follow_redirects=True)
        codes[bad or "(blank)"] = got.status_code
    s.check("zero, negative, blank and words all leave it alone", cap() == 6,
            detail=str(cap()))
    # A 500 also leaves it alone. Refusing and falling over are not the same
    # thing, and only one of them tells the owner what to type instead.
    s.check("and none of them breaks the page",
            all(c == 200 for c in codes.values()), detail=str(codes))

    s.section("A session that does not exist is a 404")
    r = oc.post("/admin/workshops/sessions/99999999/edit",
                data={"capacity": "15"}, follow_redirects=False)
    s.check("it 404s", r.status_code == 404, detail=f"HTTP {r.status_code}")

    s.section("An employee cannot change what the house sells")
    r = ec.post(f"/admin/workshops/sessions/{sid}/edit",
                data={"capacity": "99"}, follow_redirects=False)
    s.check("an employee is refused", r.status_code in (302, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and nothing moved", cap() == 6, detail=str(cap()))

    s.section("Fifteen is what a new one starts at")
    # Ten was the seeded default and was never a decision anybody made.
    made = oc.post("/admin/workshops/new", data={"title": TAG + "fresh"},
                   follow_redirects=True)
    fresh = conn.execute("SELECT default_capacity FROM workshops WHERE title = ?",
                         (TAG + "fresh",)).fetchone()
    s.check("a workshop made with no capacity given starts at fifteen",
            fresh is not None and fresh["default_capacity"] == 15,
            detail=("not created (HTTP %s)" % made.status_code) if fresh is None
                   else str(fresh["default_capacity"]))
    s.check("and the code says so rather than the column",
            m.DEFAULT_WORKSHOP_CAPACITY == 15,
            detail="an existing database keeps its old column default")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
