"""The owner's room line-up of 24 September 2026, and that it lands once.

Five rooms renamed and repriced, every bathroom private. The danger was never
the first run -- it was every run after. The migration list in init_db runs
on EVERY boot, so a plain UPDATE there would have put these prices back on
every deploy, over whatever the owner set in the room admin since. So the
line-up is keyed on the OLD names: once a room carries its new one, nothing
matches it again. This holds both halves: that it lands, and that it then
leaves the owner's own edits alone.

Runs on the scratch copy, where init_db has already applied it to whatever
the copied database held.
"""
from _harness import Suite, db
import _harness

m = _harness.m


def _rooms(conn):
    return {r["name"]: r for r in conn.execute("SELECT * FROM rooms WHERE active = 1")}


TOUCHED = ("name", "price_per_night", "bathroom", "max_occupancy", "bed_setup",
           "sort_order", "description")


def run():
    s = Suite("The room line-up")
    conn = db()
    rooms = _rooms(conn)

    s.section("init_db renamed the five")
    # Names only, here: other suites in a full run reprice and resize these
    # rooms for their own purposes, so the figures are checked below, on rows
    # this suite puts back to their old selves and watches being changed.
    for old, new, *_rest in m.ROOM_LINEUP:
        s.check(f"{new} exists", new in rooms,
                detail=f"no room called {new}; {old} was not renamed")
    s.check("no room is left with its old name",
            not [old for old, *_rest in m.ROOM_LINEUP if old in rooms],
            detail=str([old for old, *_rest in m.ROOM_LINEUP if old in rooms]))

    s.section("The five rooms become the owner's five")
    before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM rooms")}
    ids = {row["name"]: rid for rid, row in before.items()}
    try:
        # Each row back to its old name, with values the line-up must replace.
        for old, new, *_rest in m.ROOM_LINEUP:
            if new in ids:
                conn.execute(
                    """UPDATE rooms SET name = ?, price_per_night = 1, bathroom = 'shared',
                       max_occupancy = 9, bed_setup = 'x', sort_order = 99,
                       description = 'sharing a bathroom downstairs' WHERE id = ?""",
                    (old, ids[new]))
        conn.commit()
        changed = m.apply_room_lineup(conn)
        s.check("all five are changed", changed == len(m.ROOM_LINEUP), detail=str(changed))
        after = {r["id"]: r for r in conn.execute("SELECT * FROM rooms")}
        for old, new, price, sleeps, beds, order in m.ROOM_LINEUP:
            r = after.get(ids.get(new))
            if not r:
                continue
            s.check(f"{old} is now {new}", r["name"] == new, detail=r["name"])
            s.check(f"{new} is {price} a night", float(r["price_per_night"] or 0) == price,
                    detail=f"{r['price_per_night']}")
            s.check(f"{new} has its own bathroom", r["bathroom"] == "private",
                    detail=f"{r['bathroom']}")
            s.check(f"{new} sleeps {sleeps}: {beds}",
                    r["max_occupancy"] == sleeps and r["bed_setup"] == beds,
                    detail=f"{r['max_occupancy']}, {r['bed_setup']!r}")
            s.check(f"{new} is described as the page describes it",
                    r["description"] == m.ROOM_LINEUP_WORDS[new],
                    detail="the stored description is the fallback behind "
                           "_room_copy.html, and it said 'shared' for two rooms")
        listed = [(r["name"], float(r["price_per_night"] or 0)) for r in conn.execute(
            "SELECT name, price_per_night FROM rooms WHERE active = 1 "
            "AND name IN (%s) ORDER BY sort_order, name"
            % ",".join("?" * len(m.ROOM_LINEUP)), [n for _o, n, *_r in m.ROOM_LINEUP])]
        s.check("and the site lists them by price",
                [p for _n, p in listed] == sorted(p for _n, p in listed), detail=str(listed))
    finally:
        # Back to exactly what the other suites had left, whatever happened.
        for rid, row in before.items():
            conn.execute("UPDATE rooms SET %s WHERE id = ?" % ", ".join(
                f"{col} = ?" for col in TOUCHED), [row[col] for col in TOUCHED] + [rid])
        conn.commit()
    rooms = _rooms(conn)

    s.section("Once, and never again over the owner's own edits")
    queen = rooms.get("The Queen Room")
    if queen:
        was = queen["price_per_night"]
        conn.execute("UPDATE rooms SET price_per_night = 310 WHERE id = ?", (queen["id"],))
        conn.commit()
        changed = m.apply_room_lineup(conn)
        after = conn.execute("SELECT price_per_night FROM rooms WHERE id = ?",
                             (queen["id"],)).fetchone()["price_per_night"]
        s.check("running it again changes nothing", changed == 0, detail=str(changed))
        s.check("so a price set in the admin after it stays set", float(after) == 310.0,
                detail=f"{after} -- the next deploy would have put it back to 280")
        conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?",
                     (was, queen["id"]))
        conn.commit()

    s.section("It renames the row it finds, and only that one")
    # An old name that comes back -- a room re-added by hand -- is renamed and
    # repriced like the others; the row keeps its id, so its bookings follow.
    probe = conn.execute(
        "INSERT INTO rooms (name, price_per_night, active, bathroom, max_occupancy, "
        "export_token) VALUES ('Chambre Cerise', 999, 0, 'shared', 4, 'zz-lineup-probe')")
    probe_id = probe.lastrowid
    queen_row = rooms.get("The Queen Room")
    conn.commit()
    try:
        changed = m.apply_room_lineup(conn)
        row = conn.execute("SELECT * FROM rooms WHERE id = ?", (probe_id,)).fetchone()
        s.check("a second room is never given a name one already has",
                changed == 0 and row["name"] == "Chambre Cerise",
                detail=f"{changed} changed; the probe is now called {row['name']!r}"
                       " -- two rooms answering to one name")
        if queen_row:
            conn.execute("DELETE FROM rooms WHERE id = ?", (probe_id,))
            conn.execute("UPDATE rooms SET name = 'Chambre Cerise' WHERE id = ?",
                         (queen_row["id"],))
            conn.commit()
            changed = m.apply_room_lineup(conn)
            back = conn.execute("SELECT * FROM rooms WHERE id = ?",
                                (queen_row["id"],)).fetchone()
            s.check("an old name is renamed in place, keeping its row",
                    changed == 1 and back["name"] == "The Queen Room"
                    and back["bathroom"] == "private",
                    detail=f"{changed} changed; {back['name']!r} {back['bathroom']}")
            conn.execute("""UPDATE rooms SET price_per_night = ?, sort_order = ?,
                            description = ? WHERE id = ?""",
                         (queen_row["price_per_night"], queen_row["sort_order"],
                          queen_row["description"], queen_row["id"]))
            conn.commit()
    finally:
        conn.execute("DELETE FROM rooms WHERE id = ?", (probe_id,))
        conn.commit()
        conn.close()

    s.section("On a fresh database it lands too")
    # The rooms are seeded under their listing names and renamed twice: to the
    # French names by ROOM_IDENTITY, then to these. The line-up looks for the
    # French names, so it has to run AFTER that rename -- it was first put
    # before it, where on a fresh database it would find nothing and do
    # nothing, and the house would open with last year's prices.
    import io
    import os
    src = io.open(os.path.join(_harness.ROOT, "app.py"), encoding="utf-8").read()
    body = src[src.find("def init_db():"):]
    body = body[:body.find("\ndef ")]
    renamed_at = body.find('"rooms_named_2026_09"')
    lineup_at = body.find("apply_room_lineup(conn)")
    s.check("init_db applies the line-up after the rename it follows on",
            0 <= renamed_at < lineup_at, detail=f"rename at {renamed_at}, line-up at {lineup_at}")
    s.check("the rooms it renames from are the names that rename gives",
            {old for old, *_r in m.ROOM_LINEUP} <= {new for _o, new, *_r in m.ROOM_IDENTITY},
            detail="a line-up keyed on a name nothing produces lands nowhere")

    s.section("And the room page says what the row says")
    anon = m.app.test_client()
    conn = db()
    gold = conn.execute("SELECT * FROM rooms WHERE name = 'The Gold Suite'").fetchone()
    conn.close()
    if gold:
        page = _harness.visible_text(anon.get(f"/book/{gold['id']}").get_data(as_text=True))
        s.check("the room page is headed with its new name", "The Gold Suite" in page)
        s.check("and prices it as the row does", "480" in page)
        s.check("and does not say its bathroom is shared",
                "shared bathroom" not in page.lower() and "Shared bathroom" not in page)
    return s
