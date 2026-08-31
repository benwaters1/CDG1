"""Putting a workshop's guests into rooms.

Everything needed was recorded and displayed and none of it was used
together: occupancy_type, requested_roommate, party_size, and the rooms
with their own capacities. Assigning meant picking from a dropdown one
registration at a time and holding the rest of it in your head — which is
also why half the problems here were invisible. Two people who asked for
each other and ended up apart is not something a per-registration dropdown
can show you.

The two rules the proposal must never break, because breaking either is
worse than proposing nothing at all:

  - A PAID SINGLE IS A PAID SINGLE. Somebody who chose solo paid a
    supplement for a room to themselves. No arrangement that puts anybody
    in with them is an improvement, however neatly it packs.

  - A MUTUAL REQUEST IS HONOURED; A CHAIN IS NOT RESOLVED. Aline asks for
    Bruno and Bruno asks for Aline: two people have decided that, and the
    tool does it. Aline asks for Bruno and Bruno asks for Chantal: nobody
    should resolve that quietly, because whoever ends up disappointed
    should be disappointed by a person who knows about it.

And it never moves anybody already placed. Somebody put them there on
purpose and the tool does not know why.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ZZWROOM"


def _cleanup(conn):
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_sessions WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Putting a workshop into rooms")
    today = date.today()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    # This suite's own rooms. The house's real ones are shared with every
    # other suite, and a proposal that packed them would be measuring
    # somebody else's data.
    #
    # Which ones were on is recorded rather than assumed: switching them all
    # back on at the end would turn on any room deliberately switched off,
    # which invents a state rather than restoring one. The restore is in a
    # finally, because a raise halfway through would otherwise leave every
    # later suite in this run looking at a château with no rooms.
    was_active_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM rooms WHERE active = 1").fetchall()]
    try:
        conn.execute("UPDATE rooms SET active = 0 WHERE active = 1")
        made_rooms = []
        for name, cap, order in (("Attic", 1, 1), ("Blue", 2, 2), ("Court", 2, 3),
                                 ("Dovecote", 4, 4)):
            conn.execute(
                """INSERT INTO rooms (name, description, max_occupancy,
                                      price_per_night, active, export_token, sort_order)
                   VALUES (?, '', ?, 100, 1, ?, ?)""",
                (f"{TAG} {name}", cap, f"tok-{TAG}-{name}", order))
            made_rooms.append(
                conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        attic, blue, court, dovecote = made_rooms

        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person,
                                      default_capacity, active, created_at)
               VALUES (?, '', 400, 12, 1, ?)""", (TAG + " Weaving", now))
        wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                              capacity, created_at)
               VALUES (?, ?, ?, 12, ?)""",
            (wid, (today + timedelta(days=30)).isoformat(),
             (today + timedelta(days=34)).isoformat(), now))
        sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        def register(name, occupancy="double", party=1, roommate=None, room=None,
                     status="confirmed"):
            conn.execute(
                """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                                  party_size, status, occupancy_type,
                                                  requested_roommate, assigned_room_id,
                                                  reference_code, manage_token, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, name, f"{name.lower()}@example.invalid", party, status,
                 occupancy, roommate, room, # The whole name, not the first four letters: Extra0 and
                 # Extra1 both truncate to EXTR and the second
                 # one collides on a UNIQUE index.
                 f"{TAG}{name.upper()}",
                 f"tok-{TAG.lower()}-{name.lower()}", now))
            return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        # Aline and Bruno ask for each other. Chantal wants Aline, who does not
        # want her. Emile paid to be alone. Fabienne is a party of three.
        register("Aline", roommate="Bruno")
        register("Bruno", roommate="Aline")
        register("Chantal", roommate="Aline")
        register("Emile", occupancy="solo")
        register("Fabienne", party=3)
        conn.commit()

        s.section("What it can see that a dropdown could not")
        plan = m.rooming_plan(conn, sid)
        kinds = sorted(p["kind"] for p in plan["problems"])
        s.check("a one-sided request is flagged", "one_sided" in kinds,
                detail=str(kinds) + " — Chantal asked for Aline, who asked for Bruno")
        s.check("and it names who they actually asked for",
                any("Bruno instead" in p["detail"]
                    for p in plan["problems"] if p["kind"] == "one_sided"),
                detail=str([p["detail"] for p in plan["problems"]]))
        s.check("everybody starts unplaced", len(plan["unassigned"]) == 5,
                detail=str(len(plan["unassigned"])))
        s.check("and the beds are counted", plan["beds"] == 9,
                detail=f"{plan['beds']} — 1 + 2 + 2 + 4")

        s.section("The proposal")
        prop = m.propose_rooming(conn, sid)
        where = {mv["booking"]["guest_name"]: mv["room"]["id"] for mv in prop["moves"]}
        s.check("everybody is placed", len(prop["moves"]) == 5,
                detail=f"{len(prop['moves'])} placed, "
                       f"{len(prop['unplaced'])} not")

        s.check("two people who asked for each other share",
                where.get("Aline") == where.get("Bruno") and where.get("Aline"),
                detail=str(where))
        s.check("and the reason is given",
                any(mv["why"] == "they asked for each other"
                    for mv in prop["moves"] if mv["booking"]["guest_name"] == "Aline"),
                detail=str([(mv["booking"]["guest_name"], mv["why"])
                            for mv in prop["moves"]]))

        s.section("A paid single stays single")
        emile_room = where.get("Emile")
        sharing_with_emile = [n for n, r in where.items()
                              if r == emile_room and n != "Emile"]
        s.check("nobody is put in with somebody who paid to be alone",
                not sharing_with_emile,
                detail=f"{sharing_with_emile} in with Emile — no arrangement "
                       "that does this is an improvement, however neatly it packs")

        s.section("A party of three needs a room that holds three")
        fab_room = where.get("Fabienne")
        caps = {r["id"]: r["max_occupancy"] for r in plan["rooms"]}
        s.check("Fabienne's party of three is in a room that sleeps three or more",
                fab_room and caps.get(fab_room, 0) >= 3,
                detail=f"room sleeps {caps.get(fab_room)}")
        s.check("and no room is over its capacity",
                all(sum(1 for n, r in where.items() if r == rid) <= caps[rid]
                    for rid in caps),
                detail=str(where))

        s.section("A request nobody returned is not treated as agreed")
        # Its own session, and Adele sorts first: in the fixture above,
        # Aline is reached before Chantal and pairs with Bruno, so by the
        # time Chantal's one-sided request is considered Aline is already
        # placed and skipped for a different reason entirely. The rule was
        # never exercised — the suite passed on the order names sort in.
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                              capacity, created_at)
               VALUES (?, ?, ?, 12, ?)""",
            (wid, (today + timedelta(days=45)).isoformat(),
             (today + timedelta(days=49)).isoformat(), now))
        lonely = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for name, wants in (("Adele", "Zacharie"), ("Zacharie", None)):
            conn.execute(
                """INSERT INTO workshop_bookings (session_id, guest_name,
                           guest_email, party_size, status, occupancy_type,
                           requested_roommate, reference_code, manage_token,
                           created_at)
                   VALUES (?, ?, ?, 1, 'confirmed', 'double', ?, ?, ?, ?)""",
                (lonely, name, f"{name.lower()}@example.invalid", wants,
                 f"{TAG}L{name.upper()}", f"tok-{TAG.lower()}-l-{name.lower()}",
                 now))
        conn.commit()

        lone = m.propose_rooming(conn, lonely)
        reasons = {mv["booking"]["guest_name"]: mv["why"] for mv in lone["moves"]}
        s.check("she is not placed as though he had agreed",
                reasons.get("Adele") != "they asked for each other",
                detail=f"{reasons} — checked on the REASON, because two "
                       "people can share a twin by coincidence and that "
                       "would make a who-is-where check pass for the wrong "
                       "reason")
        s.check("and the one-sided request is reported instead",
                any(p["kind"] == "one_sided"
                    and p["who"]["guest_name"] == "Adele"
                    for p in m.rooming_plan(conn, lonely)["problems"]),
                detail=str([(p["kind"], p["who"]["guest_name"])
                            for p in m.rooming_plan(conn, lonely)["problems"]]))

        s.section("Nobody already placed is moved")
        conn.execute("UPDATE workshop_bookings SET assigned_room_id = ? "
                     "WHERE session_id = ? AND guest_name = 'Chantal'", (attic, sid))
        conn.commit()
        prop2 = m.propose_rooming(conn, sid)
        s.check("somebody assigned by hand is left where they are",
                "Chantal" not in [mv["booking"]["guest_name"] for mv in prop2["moves"]],
                detail="somebody put them there on purpose and this does not "
                       "know why")
        s.check("and the room they are in is counted as taken",
                not any(mv["room"]["id"] == attic for mv in prop2["moves"]),
                detail="the Attic sleeps one and Chantal is in it")

        s.section("Applying it")
        r = oc.post(f"/admin/workshops/sessions/{sid}/rooming/apply",
                    follow_redirects=True)
        rows = conn.execute(
            """SELECT guest_name, assigned_room_id FROM workshop_bookings
                WHERE session_id = ?""", (sid,)).fetchall()
        assigned = {r["guest_name"]: r["assigned_room_id"] for r in rows}
        s.check("everybody now has a room",
                all(v for v in assigned.values()), detail=str(assigned))
        s.check("Chantal is still where she was put", assigned["Chantal"] == attic,
                detail=str(assigned["Chantal"]))
        s.check("Aline and Bruno are together",
                assigned["Aline"] == assigned["Bruno"], detail=str(assigned))
        s.check("and Emile is alone",
                sum(1 for v in assigned.values() if v == assigned["Emile"]) == 1,
                detail=str(assigned))

        s.section("Applying twice does nothing the second time")
        r = oc.post(f"/admin/workshops/sessions/{sid}/rooming/apply",
                    follow_redirects=True)
        s.check("it says there is nothing to place",
                any("already" in f.lower() or "nothing" in f.lower()
                    for f in flashes(r)), detail=str(flashes(r)))
        after = {r["guest_name"]: r["assigned_room_id"] for r in conn.execute(
            "SELECT guest_name, assigned_room_id FROM workshop_bookings "
            "WHERE session_id = ?", (sid,)).fetchall()}
        s.check("and nobody moved", after == assigned, detail=str(after))

        s.section("Two people who asked for each other but are apart")
        conn.execute("UPDATE workshop_bookings SET assigned_room_id = ? "
                     "WHERE session_id = ? AND guest_name = 'Bruno'", (dovecote, sid))
        conn.commit()
        plan = m.rooming_plan(conn, sid)
        s.check("is reported", any(p["kind"] == "split_pair" for p in plan["problems"]),
                detail=str(sorted(p["kind"] for p in plan["problems"])))

        s.section("Somebody who paid for a single, sharing")
        conn.execute("UPDATE workshop_bookings SET assigned_room_id = ? "
                     "WHERE session_id = ? AND guest_name = 'Emile'", (dovecote, sid))
        conn.commit()
        plan = m.rooming_plan(conn, sid)
        s.check("is reported", any(p["kind"] == "solo_shared" for p in plan["problems"]),
                detail=str(sorted(p["kind"] for p in plan["problems"])))

        s.section("A roommate nobody has heard of")
        register("Gustave", roommate="Somebody Not Booked")
        conn.commit()
        plan = m.rooming_plan(conn, sid)
        s.check("is reported rather than guessed at",
                any(p["kind"] == "unknown_roommate" for p in plan["problems"]),
                detail=str(sorted(p["kind"] for p in plan["problems"])))

        s.section("More people than beds")
        for i in range(6):
            register(f"Extra{i}", party=2)
        conn.commit()
        prop = m.propose_rooming(conn, sid)
        s.check("the ones who do not fit are named rather than dropped",
                bool(prop["unplaced"]),
                detail=f"{len(prop['unplaced'])} unplaced against "
                       f"{plan['beds']} beds")

        s.section("The page opens and an employee cannot")
        r = oc.get(f"/admin/workshops/sessions/{sid}/rooming")
        body = r.get_data(as_text=True)
        s.check("the owner can open it", r.status_code == 200,
                detail=f"HTTP {r.status_code}")
        s.check("and it names the problems", "Worth a look" in body)
        r = ec.get(f"/admin/workshops/sessions/{sid}/rooming", follow_redirects=False)
        s.check("an employee cannot", r.status_code in (302, 303, 403),
                detail=f"HTTP {r.status_code}")
        r = ec.post(f"/admin/workshops/sessions/{sid}/rooming/apply",
                    follow_redirects=False)
        s.check("nor apply one", r.status_code in (302, 303, 403),
                detail=f"HTTP {r.status_code}")

        s.section("A session nobody has booked")
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                              capacity, created_at)
               VALUES (?, ?, ?, 12, ?)""",
            (wid, (today + timedelta(days=60)).isoformat(),
             (today + timedelta(days=64)).isoformat(), now))
        empty = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.commit()
        r = oc.get(f"/admin/workshops/sessions/{empty}/rooming")
        s.check("opens rather than erroring", r.status_code == 200,
                detail=f"HTTP {r.status_code}")
        s.check("and says so", "Nobody is booked on this session yet"
                in r.get_data(as_text=True))
    finally:
        # Exactly what was on before, and nothing else.
        _cleanup(conn)
        conn.execute("UPDATE rooms SET active = 0")
        if was_active_ids:
            marks = ",".join("?" * len(was_active_ids))
            conn.execute(
                f"UPDATE rooms SET active = 1 WHERE id IN ({marks})",
                was_active_ids)
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
