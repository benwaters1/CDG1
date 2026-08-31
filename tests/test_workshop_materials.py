"""What a workshop uses up, and taking it out of stock when it runs.

The stock ledger is good — genuinely append-only, never a stored counter —
and it knew nothing whatever about workshops. Zero mentions either way
across the whole file. So nobody could answer "what does the fourteenth
need?", and a session that got through forty kilos of clay left the stock
figures insisting it was all still on the shelf.

The four things this holds in place:

  - TWO QUANTITIES. Per session (eight aprons, one firing) does not scale;
    per person (two kilos of clay each) does. A list with only one of them
    forces every entry into the wrong shape and whoever fills it in rounds,
    which is how a list stops being worth reading.

  - COUNTED ON CONFIRMED HEADS, the same figure the minimum-to-run uses and
    for the same reason: somebody who has not paid a deposit does not get
    through two kilos of clay.

  - TAKING IT OUT IS IDEMPOTENT. Pressing the button twice must not deplete
    twice. Proved by the ledger rather than by a flag on the session,
    because a flag is a second place the truth lives and the two disagree
    the first time anything goes wrong.

  - THE LEDGER STAYS A LEDGER. Consumption is appended as movements, never
    written back onto the item, and removing a material afterwards does not
    unwind what already happened.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ZZWMAT"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM stock_movements WHERE stock_item_id IN "
        "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_materials WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_sessions WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("What a workshop uses up")
    today = date.today()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    def stock_item(name, unit, opening, cost=None):
        conn.execute(
            """INSERT INTO stock_items (name, category, unit, reorder_level,
                                        unit_cost, active, created_at)
               VALUES (?, 'other', ?, 0, ?, 1, ?)""",
            (TAG + " " + name, unit, cost, now))
        iid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        m.record_stock_movement(conn, iid, opening, "opening", note="test")
        return iid

    clay = stock_item("Clay", "kg", 30, cost=4.0)
    aprons = stock_item("Aprons", "each", 20, cost=9.0)
    glaze = stock_item("Glaze", "litre", 1, cost=25.0)

    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', 300, 12, 1, ?)""", (TAG + " Throwing", now))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def add_session(start):
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                              capacity, created_at)
               VALUES (?, ?, ?, 12, ?)""",
            (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def register(session_id, name, status="confirmed", party=1):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                              party_size, status, reference_code,
                                              manage_token, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, name, f"{name.lower()}@example.invalid", party, status,
             f"{TAG}{session_id}{name[:3].upper()}",
             f"tok-{TAG.lower()}-{session_id}-{name.lower()}", now))

    sid = add_session(today + timedelta(days=6))
    register(sid, "Aline", party=2)
    register(sid, "Bruno", party=2)
    # Has not paid: uses nothing, because they are not coming.
    register(sid, "Chantal", status="pending", party=5)
    conn.commit()

    s.section("Setting what it uses")
    r = oc.post(f"/admin/workshops/{wid}/materials",
                data={"stock_item_id": str(clay), "qty_per_person": "2"},
                follow_redirects=True)
    s.check("a per-person material is recorded",
            conn.execute("SELECT COUNT(*) AS c FROM workshop_materials "
                         "WHERE workshop_id = ?", (wid,)).fetchone()["c"] == 1,
            detail=str(flashes(r)))
    oc.post(f"/admin/workshops/{wid}/materials",
            data={"stock_item_id": str(aprons), "qty_per_session": "8"},
            follow_redirects=True)
    oc.post(f"/admin/workshops/{wid}/materials",
            data={"stock_item_id": str(glaze), "qty_per_session": "3"},
            follow_redirects=True)

    before = conn.execute("SELECT COUNT(*) AS c FROM workshop_materials "
                          "WHERE workshop_id = ?", (wid,)).fetchone()["c"]
    r = oc.post(f"/admin/workshops/{wid}/materials",
                data={"stock_item_id": str(clay), "qty_per_person": "0",
                      "qty_per_session": "0"}, follow_redirects=True)
    s.check("something that uses nothing is refused",
            any("how much" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)) + " — a line that means nothing will be "
                                     "read every time the workshop runs")
    s.check("and nothing was written",
            conn.execute("SELECT COUNT(*) AS c FROM workshop_materials "
                         "WHERE workshop_id = ?", (wid,)).fetchone()["c"] == before)

    # The same item twice edits rather than doubling.
    oc.post(f"/admin/workshops/{wid}/materials",
            data={"stock_item_id": str(clay), "qty_per_person": "3"},
            follow_redirects=True)
    rows = conn.execute("SELECT * FROM workshop_materials WHERE workshop_id = ? "
                        "AND stock_item_id = ?", (wid, clay)).fetchall()
    s.check("adding the same item again edits it rather than adding a second row",
            len(rows) == 1 and rows[0]["qty_per_person"] == 3,
            detail=f"{len(rows)} row(s) — two rows for one item both look "
                   "right and the total is silently doubled")
    # back to 2 for the arithmetic below
    oc.post(f"/admin/workshops/{wid}/materials",
            data={"stock_item_id": str(clay), "qty_per_person": "2"},
            follow_redirects=True)

    s.section("What this running of it needs")
    plan = m.session_materials(conn, sid)
    s.check("it counts confirmed places only", plan["heads"] == 4,
            detail=f"{plan['heads']} — Chantal's party of five has not paid")
    by_name = {l["name"]: l for l in plan["lines"]}
    clay_line = by_name[TAG + " Clay"]
    apron_line = by_name[TAG + " Aprons"]
    s.check("a per-person quantity scales with who is coming",
            clay_line["need"] == 8, detail=f"{clay_line['need']} kg for 4")
    s.check("a per-session one does not", apron_line["need"] == 8,
            detail=f"{apron_line['need']} — eight aprons is eight aprons "
                   "whether four come or ten")
    s.check("what is on the shelf is read from the ledger",
            clay_line["have"] == 30, detail=str(clay_line["have"]))
    s.check("and enough clay is not a shortfall", clay_line["shortfall"] == 0)

    glaze_line = by_name[TAG + " Glaze"]
    s.check("three litres wanted against one held is short two",
            glaze_line["shortfall"] == 2, detail=str(glaze_line["shortfall"]))
    s.check("which is what the session reports",
            [l["name"] for l in plan["short"]] == [TAG + " Glaze"],
            detail=str([l["name"] for l in plan["short"]]))
    s.check("costed at what the stock says it costs",
            plan["cost"] == round(8 * 4.0 + 8 * 9.0 + 3 * 25.0, 2),
            detail=str(plan["cost"]))

    s.section("It reaches the owner rather than sitting in a function")
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    mine = [w for w in warnings if "without the materials to run" in w["title"]]
    s.check("a session it cannot run appears on the home page", bool(mine),
            detail=str([w["title"] for w in warnings][:4]))
    s.check("naming what is short",
            bool(mine) and "Glaze" in mine[0]["detail"], detail=str(mine))

    found, _d = m.watch_task_findings(conn, today)
    ours = [f for f in found if f[0] == "materials" and TAG in f[1]]
    s.check("and becomes a task", bool(ours),
            detail=f"kinds: {sorted({f[0] for f in found})}")

    s.section("A session further off than anything can be ordered for")
    far = add_session(today + timedelta(days=90))
    register(far, "Damien", party=4)
    conn.commit()
    ids = [p["session"]["id"] for p in m.sessions_short_of_materials(conn, today)]
    s.check("is not on the list yet", far not in ids,
            detail="ten days is the last point at which anything can be "
                   "ordered and arrive; a warning three months out is noise")
    s.check("but the near one is", sid in ids, detail=str(ids))

    s.section("Taking it out of stock")
    have_before = m.stock_levels(conn, [clay])[clay]
    r = oc.post(f"/admin/workshops/sessions/{sid}/consume", follow_redirects=True)
    have_after = m.stock_levels(conn, [clay])[clay]
    s.check("the clay comes off the shelf", have_after == have_before - 8,
            detail=f"{have_before} -> {have_after}")
    s.check("as a movement rather than a rewritten figure",
            conn.execute(
                """SELECT COUNT(*) AS c FROM stock_movements
                    WHERE workshop_session_id = ?""", (sid,)).fetchone()["c"] == 3,
            detail="the level is never stored on the item; a stored counter "
                   "and a ledger disagree the first time anything goes wrong")
    s.check("written against the session that used it",
            conn.execute(
                """SELECT COUNT(*) AS c FROM stock_movements
                    WHERE workshop_session_id = ? AND delta < 0""",
                (sid,)).fetchone()["c"] == 3,
            detail="so the ledger can answer where forty kilos of clay went")

    s.section("Pressing it twice does not take it out twice")
    r = oc.post(f"/admin/workshops/sessions/{sid}/consume", follow_redirects=True)
    s.check("the second press is refused",
            any("already" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the shelf is unchanged", m.stock_levels(conn, [clay])[clay] == have_after,
            detail=f"{m.stock_levels(conn, [clay])[clay]} against {have_after}")

    s.section("A shelf already emptied by the workshop is not a shortfall")
    after_plan = m.session_materials(conn, sid)
    s.check("the session knows it has been taken out", after_plan["taken_out"])
    ids = [p["session"]["id"] for p in m.sessions_short_of_materials(conn, today)]
    s.check("so it stops warning about it", sid not in ids,
            detail="the shelf is low BECAUSE the workshop has had it, which "
                   "is not the same as not being able to run it")

    s.section("Removing a material does not unwind what already happened")
    mat = conn.execute("SELECT id FROM workshop_materials WHERE workshop_id = ? "
                       "AND stock_item_id = ?", (wid, clay)).fetchone()
    oc.post(f"/admin/workshops/materials/{mat['id']}/remove", follow_redirects=True)
    s.check("the stock stays taken out",
            m.stock_levels(conn, [clay])[clay] == have_after,
            detail="the ledger records what happened, and what happened does "
                   "not change because somebody edited a list afterwards")

    s.section("A workshop with no materials costs nothing to plan")
    conn.execute("DELETE FROM workshop_materials WHERE workshop_id = ?", (wid,))
    conn.commit()
    bare = m.session_materials(conn, sid)
    s.check("it reports an empty plan rather than erroring", bare["lines"] == [])
    s.check("and nothing short", bare["short"] == [])

    s.section("An employee cannot set materials or move stock")
    r = ec.post(f"/admin/workshops/{wid}/materials",
                data={"stock_item_id": str(clay), "qty_per_person": "1"},
                follow_redirects=False)
    s.check("setting is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = ec.post(f"/admin/workshops/sessions/{sid}/consume", follow_redirects=False)
    s.check("and so is taking stock out", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
