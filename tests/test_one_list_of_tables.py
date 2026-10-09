"""One list of tables: the till's floor plan is the list the reservations use.

WHAT WENT WRONG. There were two lists of restaurant tables and nothing joined
them. The till's floor plan (restaurant_tables) held the house's ten tables --
1 to 5, T1 to T3, B1 and B2 -- and the till opened tabs on them. The
reservations' table plan read a second list (dining_tables) that the floor plan
never saw. Found on 9 October 2026 by reading every owner page: the Floor plan
showed ten tables while the Table plan said "No tables laid out yet", no booking
could be seated at any of the ten, and "How full the room was" divided covers
by zero seats and declined to give a figure. Nothing errored; every page drew
exactly what its own list held.

WHAT THIS PINS.
  - The table plan offers the floor plan's tables, and seating a party writes a
    floor-plan table to the booking.
  - The room's seats are the floor plan's.
  - A table that is not on the floor plan is refused with a sentence.
  - The floor plan retires, rather than deletes, a table a party was seated at,
    as it already did for one a tab was opened on.
  - The old list is carried across ONCE per row: under its own name, or meeting
    the floor-plan table of that name; seated parties follow; and a table the
    owner later removes is not put back on the next start.
  - Nothing else reads the old list.
"""
import inspect
import re

from _harness import Suite, clients, db, flashes, forms_on
import _harness

m = _harness.m
TAG = "ZZONELIST"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM dining_tables WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_tables WHERE label LIKE ?", (TAG + "%",))
    conn.commit()


def _booking(conn, name, party, day, **cols):
    keys = ["reference_code", "manage_token", "guest_name", "guest_email", "party_size",
            "dinner_date", "status", "created_at"] + list(cols)
    values = [f"{TAG}-{name}", f"tok{TAG}{name}".lower(), f"{TAG} {name}",
              f"{TAG}.{name}@example.invalid".lower(), party, day, "confirmed",
              _harness.datetime_now()] + list(cols.values())
    conn.execute(f"INSERT INTO restaurant_bookings ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' for _ in keys)})", values)
    conn.commit()
    return conn.execute("SELECT id FROM restaurant_bookings WHERE guest_name = ?",
                        (f"{TAG} {name}",)).fetchone()["id"]


def _floor_table(conn, label, seats=4, area="salle"):
    conn.execute("""INSERT INTO restaurant_tables (label, area, seats, sort_order, active,
                    created_at) VALUES (?, ?, ?, 900, 1, ?)""",
                 (label, area, seats, _harness.datetime_now()))
    conn.commit()
    return conn.execute("SELECT id FROM restaurant_tables WHERE label = ?",
                        (label,)).fetchone()["id"]


def _old_table(conn, name, seats=4, area=None):
    conn.execute("""INSERT INTO dining_tables (name, seats, area, active, sort_order,
                    created_at) VALUES (?, ?, ?, 1, 0, ?)""",
                 (name, seats, area, _harness.datetime_now()))
    conn.commit()
    return conn.execute("SELECT id FROM dining_tables WHERE name = ?", (name,)).fetchone()["id"]


def run():
    s = Suite("one list of tables")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    day = (m.house_today()).isoformat()
    try:
        s.section("The table plan uses the floor plan's tables")
        window = _floor_table(conn, TAG + " Window", seats=4)
        aline = _booking(conn, "Aline", 3, day)
        page = ec.get(f"/restaurant/tables?date={day}")
        html = page.get_data(as_text=True)
        s.check("the table plan opens for the team", page.status_code == 200, page)
        floor = [r["label"] for r in m.dining_tables(conn)]
        seat_forms = [f for f in forms_on(html) if "/restaurant/tables/seat/" in (f["action"] or "")]
        offered = set()
        for f in seat_forms:
            for field in f["fields"]:
                if field["name"] == "table_id":
                    offered |= {o for o in field["options"] if o}
        floor_ids = {str(r["id"]) for r in m.dining_tables(conn)}
        s.check("a party can be seated at any table on the floor plan",
                seat_forms and offered == floor_ids,
                detail=f"offered {sorted(offered)}, floor plan {sorted(floor_ids)}")
        s.check("and the tables are listed by their floor-plan names",
                all(label in html for label in floor), detail=", ".join(floor))
        s.check("it no longer keeps a list of its own",
                not [f for f in forms_on(html) if f["method"] == "post" and
                     any(x["name"] == "seats" for x in f["fields"])],
                detail="an add-a-table form on the table plan writes a second list")

        r = ec.post(f"/restaurant/tables/seat/{aline}", data={"table_id": str(window)},
                    follow_redirects=True)
        got = conn.execute("SELECT table_id, dining_table_id FROM restaurant_bookings "
                           "WHERE id = ?", (aline,)).fetchone()
        s.check("seating a party puts them at the floor-plan table",
                got["table_id"] == window and got["dining_table_id"] is None, r,
                detail=f"table_id={got['table_id']}, dining_table_id={got['dining_table_id']}")
        bruno = _booking(conn, "Bruno", 3, day)
        r = ec.post(f"/restaurant/tables/seat/{bruno}", data={"table_id": str(window)},
                    follow_redirects=True)
        said = " ".join(flashes(r))
        s.check("an overfull table still warns, naming the table and who is on it",
                "Window seats 4" in said and "would have 6" in said and "Aline" in said,
                detail=said)
        r = ec.post(f"/restaurant/tables/seat/{bruno}", data={"table_id": "99999999"},
                    follow_redirects=True)
        s.check("a table that is not on the floor plan is refused in words",
                r.status_code == 200 and "not on the floor plan" in " ".join(flashes(r)), r,
                detail=f"status {r.status_code}")
        ec.post(f"/restaurant/tables/seat/{bruno}", data={"table_id": ""},
                follow_redirects=True)
        s.check("and a party can be taken off a table",
                conn.execute("SELECT table_id FROM restaurant_bookings WHERE id = ?",
                             (bruno,)).fetchone()["table_id"] is None)

        s.section("The room's seats are the floor plan's")
        with m.app.test_request_context("/"):
            util = m.table_utilisation(conn)
        floor_seats = conn.execute("SELECT COALESCE(SUM(seats), 0) AS s FROM restaurant_tables "
                                   "WHERE active = 1").fetchone()["s"]
        s.check("how full the room was is measured against the floor plan's seats",
                util["seats"] == floor_seats and floor_seats > 0,
                detail=f"{util['seats']} against {floor_seats}")

        s.section("The floor plan keeps a table somebody was seated at")
        oc.post(f"/admin/restaurant/tables/{window}/retire", follow_redirects=True)
        kept = conn.execute("SELECT active FROM restaurant_tables WHERE id = ?",
                            (window,)).fetchone()
        s.check("it is taken off the floor, not deleted, so the party stays seated",
                kept is not None and kept["active"] == 0,
                detail="deleted" if kept is None else f"active={kept['active']}")

        s.section("The old list is carried across, once")
        old = _old_table(conn, TAG + " Orangery", seats=6, area="Terrasse")
        carla = _booking(conn, "Carla", 2, day, dining_table_id=old)
        moved = m.merge_old_dining_tables(conn)
        new = conn.execute("SELECT * FROM restaurant_tables WHERE label = ?",
                           (TAG + " Orangery",)).fetchone()
        s.check("an old table joins the floor plan under its own name",
                moved >= 1 and new is not None and new["seats"] == 6 and new["active"] == 1,
                detail=str(dict(new)) if new else "not on the floor plan")
        s.check("in the floor plan's area for what it was written as",
                new is not None and new["area"] == "terrace",
                detail=new["area"] if new else "")
        b = conn.execute("SELECT table_id, dining_table_id FROM restaurant_bookings "
                         "WHERE id = ?", (carla,)).fetchone()
        s.check("a party seated at it is seated at its floor-plan table",
                new is not None and b["table_id"] == new["id"] and b["dining_table_id"] is None,
                detail=f"{dict(b)}")
        s.check("and the old row says where it went",
                new is not None and conn.execute(
                    "SELECT merged_into FROM dining_tables WHERE id = ?",
                    (old,)).fetchone()["merged_into"] == new["id"])
        s.check("running it again changes nothing",
                m.merge_old_dining_tables(conn) == 0 and conn.execute(
                    "SELECT COUNT(*) AS c FROM restaurant_tables WHERE label = ?",
                    (TAG + " Orangery",)).fetchone()["c"] == 1)

        same = _floor_table(conn, TAG + " Alcove", seats=2)
        dup = _old_table(conn, TAG + " alcove ", seats=8)
        m.merge_old_dining_tables(conn)
        s.check("an old table with a floor-plan table's name meets it, rather than "
                "making a second", conn.execute(
                    "SELECT COUNT(*) AS c FROM restaurant_tables WHERE lower(trim(label)) = ?",
                    ((TAG + " alcove").lower(),)).fetchone()["c"] == 1 and conn.execute(
                    "SELECT merged_into FROM dining_tables WHERE id = ?",
                    (dup,)).fetchone()["merged_into"] == same)

        gone = _old_table(conn, TAG + " Spare")
        m.merge_old_dining_tables(conn)
        conn.execute("DELETE FROM restaurant_tables WHERE label = ?", (TAG + " Spare",))
        conn.commit()
        m.merge_old_dining_tables(conn)
        s.check("a table the owner removes from the floor plan is not put back on the "
                "next start", conn.execute(
                    "SELECT COUNT(*) AS c FROM restaurant_tables WHERE label = ?",
                    (TAG + " Spare",)).fetchone()["c"] == 0, detail=f"old row {gone}")

        s.section("Nothing else reads the old list")
        merge_src = inspect.getsource(m.merge_old_dining_tables)
        with open(m.__file__, encoding="utf-8") as fh:
            src = fh.read()
        uses = [mt.group(0) for mt in re.finditer(
            r"(?:FROM|JOIN|INTO|UPDATE)\s+dining_tables\b", src)]
        inside = len(re.findall(r"(?:FROM|JOIN|INTO|UPDATE)\s+dining_tables\b", merge_src))
        s.check("only the merge reads or writes dining_tables", len(uses) == inside,
                detail=f"{len(uses)} uses in app.py, {inside} of them in the merge")
    finally:
        _cleanup(conn)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
