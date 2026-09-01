"""The staff Today screen.

Two things here are easy to get wrong in ways nobody notices.

A guest checking out today is classed "past" by the occupancy logic, because
the departure night was never sold. Correct for revenue, wrong for staff — that
guest still wants breakfast and their room still needs turning over. If they
vanish from this screen, the person who needed to know is the one who doesn't.

And one tap has to mean done. The planning page cycles open -> in_progress ->
done, which took three taps to tick something off a checklist.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZTODAY"


def run():
    s = Suite("Staff Today")
    oc, ec, owner, emp = clients()
    today = m.house_today()
    now = datetime.now(timezone.utc).isoformat()

    room = ensure_room()["id"]
    conn = db()

    conn.execute("""INSERT INTO guests (name, email, created_at, dietary_notes,
                    preferences, vip, name_pronunciation) VALUES (?,?,?,?,?,?,?)""",
                 (f"{TAG} Aoife", f"{TAG.lower()}@example.invalid", now,
                  "Coeliac", "Corner table at dinner", 1, "EE-fa"))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?",
                       (f"{TAG.lower()}@example.invalid",)).fetchone()["id"]

    def booking(arrival, departure, ref, guest=f"{TAG} Aoife",
                email=f"{TAG.lower()}@example.invalid", link=None, requests=None):
        conn.execute(
            """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
               departure_date, party_size, status, reference_code, manage_token,
               created_at, linked_guest_id, special_requests)
               VALUES (?,?,?,?,?,?,'confirmed',?,?,?,?,?)""",
            (room, guest, email, arrival.isoformat(), departure.isoformat(), 2, ref,
             f"tok{ref}", now, link, requests))

    booking(today - timedelta(days=300), today - timedelta(days=297), f"{TAG}A", link=gid)
    booking(today - timedelta(days=1), today + timedelta(days=3), f"{TAG}NOW", link=gid,
            requests="Late check-out")
    booking(today - timedelta(days=4), today, f"{TAG}OUT", guest=f"{TAG} Departing",
            email=f"{TAG.lower()}out@example.invalid")
    conn.commit()

    cards = m.guest_recognition_cards(conn, today)
    conn.close()
    mine = {c["name"]: c for c in cards if str(c["name"]).startswith(TAG)}

    s.section("Who counts as being in the house")
    s.check("a guest mid-stay appears", f"{TAG} Aoife" in mine)
    # The whole point of the separate query.
    s.check("a guest departing TODAY still appears", f"{TAG} Departing" in mine,
            detail="they are still in the building and need checking out")
    if f"{TAG} Departing" in mine:
        s.check("and is flagged as leaving today",
                mine[f"{TAG} Departing"]["when"] == "Leaving today",
                detail=f"got {mine[f'{TAG} Departing']['when']!r}")

    s.section("Recognition details")
    if f"{TAG} Aoife" in mine:
        card = mine[f"{TAG} Aoife"]
        s.check("nights left is phrased in words, not dates",
                card["when"] == "3 more nights", detail=f"got {card['when']!r}")
        # Counted across confirmed bookings on the same email — this is what
        # lets staff say "welcome back" rather than "welcome".
        s.check("a returning guest is counted", card["stay_number"] == 2,
                detail=f"got stay_number={card['stay_number']}")
        s.check("pronunciation is carried through", card["pronunciation"] == "EE-fa")
        s.check("VIP is flagged", card["vip"] is True)
        # Preference over dietary: it's what you'd mention at the door.
        s.check("the one-line highlight prefers a preference",
                card["highlight"] == "Corner table at dinner",
                detail=f"got {card['highlight']!r}")
        s.check("dietary is kept for the detail panel, not the summary",
                card["detail"]["dietary"] == "Coeliac"
                and "Coeliac" not in (card["highlight"] or ""))

    s.section("The page renders for both roles")
    for label, client in (("owner", oc), ("employee", ec)):
        r = client.get("/today")
        s.check(f"/today renders for the {label}", r.status_code == 200,
                detail=f"HTTP {r.status_code}")

    s.section("One tap completes a task")
    conn = db()
    conn.execute("""INSERT INTO tasks (title, assigned_to_user_id, due_date, priority,
                    status, created_at) VALUES (?,?,?,?,'open',?)""",
                 (f"{TAG} sweep the terrace", emp["id"], today.isoformat(), "normal", now))
    conn.commit()          # without this, close() rolls it back and the id is stale
    tid = conn.execute("SELECT id FROM tasks WHERE title = ?",
                       (f"{TAG} sweep the terrace",)).fetchone()["id"]
    conn.close()

    r = ec.post(f"/tasks/{tid}/complete")
    conn = db()
    row = conn.execute("SELECT status, completed_at FROM tasks WHERE id = ?", (tid,)).fetchone()
    conn.close()
    s.check("one tap marks it done", row["status"] == "done", r, detail=f"got {row['status']}")
    s.check("and stamps completed_at", row["completed_at"] is not None)

    ec.post(f"/tasks/{tid}/complete")
    conn = db()
    row = conn.execute("SELECT status, completed_at FROM tasks WHERE id = ?", (tid,)).fetchone()
    conn.close()
    # Back to open, never stranded in in_progress.
    s.check("tapping again un-ticks it to open", row["status"] == "open",
            detail=f"got {row['status']}")
    s.check("and clears completed_at", row["completed_at"] is None)

    s.section("Permissions")
    conn = db()
    conn.execute("""INSERT INTO tasks (title, assigned_to_user_id, due_date, priority,
                    status, created_at) VALUES (?,?,?,?,'open',?)""",
                 (f"{TAG} owner only", owner["id"], today.isoformat(), "normal", now))
    conn.commit()
    other = conn.execute("SELECT id FROM tasks WHERE title = ?",
                         (f"{TAG} owner only",)).fetchone()["id"]
    conn.close()
    r = ec.post(f"/tasks/{other}/complete")
    conn = db()
    still = conn.execute("SELECT status FROM tasks WHERE id = ?", (other,)).fetchone()["status"]
    conn.close()
    s.check("an employee cannot complete someone else's task",
            r.status_code == 403 and still == "open",
            detail=f"HTTP {r.status_code}, status={still}")

    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
