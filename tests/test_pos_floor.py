"""The room, and seating somebody in it.

The till knew what a tab was and nothing about what a restaurant is. There was
no table anywhere in the schema — `table_label` was free text typed onto the
order — and three things followed from that:

  - the service screen listed OPEN tabs only, so an empty restaurant showed an
    empty page. You could not see your own room.
  - seating meant typing a table name on a tablet in the middle of service.
  - free text meant "Terrace", "terrace" and "Table 4" became four different
    tables inside a week, and then the floor view and the takings could not be
    read.

So: tables are a thing now, the floor shows every one of them, and seating a
walk-in is one touch with the covers taken from what the table seats. The order
keeps its own frozen `table_label` for the same reason it freezes settled
totals — renaming a table must not rewrite what last August's tabs say.

Cash is here too. The till recorded that cash had been taken and left the
arithmetic to whoever was holding the notes.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZFLOOR"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",)).fetchall()]
    for oid in ids:
        conn.execute("DELETE FROM pos_order_lines WHERE order_id = ?", (oid,))
        conn.execute("DELETE FROM pos_orders WHERE id = ?", (oid,))
    conn.execute("DELETE FROM restaurant_tables WHERE label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _table(label, area="salle", seats=2):
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_tables (label, area, seats, sort_order, active, created_at)
           VALUES (?, ?, ?, 90, 1, ?)""",
        (f"{TAG}{label}", area, seats, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM restaurant_tables WHERE label = ?",
                       (f"{TAG}{label}",)).fetchone()
    conn.close()
    return row


def _latest_order():
    conn = db()
    try:
        return conn.execute("SELECT * FROM pos_orders ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()


def _open_count():
    conn = db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM pos_orders WHERE status = 'open'").fetchone()["c"]
    finally:
        conn.close()


def run():
    s = Suite("Till floor")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("A room exists without anybody laying one out")
    conn = db()
    seeded = conn.execute("SELECT COUNT(*) AS c FROM restaurant_tables").fetchone()["c"]
    conn.close()
    s.check("the restaurant starts with a floor plan", seeded > 0,
            detail="a fresh install shows an empty service screen and a text box")

    s.section("The service screen shows the room, not just the busy bits")
    page = oc.get("/pos")
    html = page.get_data(as_text=True)
    s.check("it loads", page.status_code == 200, page)
    s.check("free tables are on it", "is-free" in html,
            detail="the floor shows occupied tables only, so an empty restaurant "
                   "shows an empty page")
    s.check("and they are grouped by where they are",
            "Salle" in html or "Terrace" in html, detail="no areas rendered")

    s.section("The till is not a page in the admin app")
    # It used to extend base.html, so a waitress on the floor got the whole
    # management shell: a sidebar with Payroll Pack and Role Compliance in it, a
    # search box, a notifications bell, language links. None of it belongs on a
    # screen used between carrying plates, and on an iPad it ate the width the
    # room is read in.
    conn = db()
    seat_me = conn.execute(
        "SELECT id FROM restaurant_tables WHERE active = 1 ORDER BY id LIMIT 1").fetchone()["id"]
    conn.close()
    oc.post("/pos/open", data={"table_id": str(seat_me)}, follow_redirects=True)
    a_tab = _latest_order()["id"]
    for path in ("/pos", f"/pos/{a_tab}", "/pos/kitchen"):
        body = oc.get(path).get_data(as_text=True)
        s.check(f"{path} runs on the till shell", "till-shell" in body,
                detail="it is still rendering inside the admin layout")
        s.check(f"{path} has no admin sidebar", "app-sidebar" not in body,
                detail="Payroll Pack is in the menu of a screen a waitress uses")
        s.check(f"{path} has no search box", "topbar-search" not in body)
    oc.post(f"/pos/{a_tab}/pay", data={"method": "comp"}, follow_redirects=True)

    s.section("Seating a walk-in is one touch")
    four = _table("-4", area="salle", seats=4)
    r = oc.post("/pos/open", data={"table_id": str(four["id"])}, follow_redirects=True)
    o = _latest_order()
    s.check("the tab opens", o is not None and o["table_id"] == four["id"], r)
    s.check("named after the table, with nothing typed", o["table_label"] == four["label"],
            detail=f"got {o['table_label']!r}")
    s.check("and the covers come from what the table seats", o["covers"] == 4,
            detail=f"got {o['covers']}")

    s.section("Tapping an occupied table goes to its tab, it does not open a second")
    before = _open_count()
    r2 = oc.post("/pos/open", data={"table_id": str(four["id"])}, follow_redirects=True)
    s.check("no second tab on the same table", _open_count() == before,
            detail=f"{before} -> {_open_count()} open tabs")
    s.check("and it says why", any("already open" in x for x in flashes(r2)),
            detail=f"{flashes(r2)[:1]}")

    s.section("A table that is not on the plan is refused")
    n = _open_count()
    r3 = oc.post("/pos/open", data={"table_id": "999999"}, follow_redirects=True)
    s.check("nothing opens", _open_count() == n)
    s.check("and it says so", any("floor plan" in x for x in flashes(r3)),
            detail=f"{flashes(r3)[:1]}")

    s.section("An occupied table shows what the section needs to know")
    oc.post(f"/pos/{o['id']}/add",
            data={"name": f"{TAG} Carafe", "unit_price": "12.50", "quantity": "2"},
            follow_redirects=True)
    floor = oc.get("/pos").get_data(as_text=True)
    s.check("the table is no longer offered as free",
            f'value="{four["id"]}"' not in floor.split("is-free")[-1]
            if "is-free" in floor else True)
    s.check("its total is on the tile", "25.00" in floor,
            detail="the tile does not show what the table owes")
    s.check("and that something has not gone to the kitchen",
            "not sent to the kitchen" in floor,
            detail="a tile that does not flag unsent food is a tile nobody reads")

    s.section("Off-plan tabs are still visible")
    # Two terrace tables pushed together for nine. It has no table_id, and a tab
    # nobody can see is a tab nobody settles.
    oc.post("/pos/open", data={"table_label": f"{TAG} two together", "covers": "9"},
            follow_redirects=True)
    off = _latest_order()
    s.check("it opens with no table", off["table_id"] is None)
    s.check("and appears under its own heading",
            f"{TAG} two together" in oc.get("/pos").get_data(as_text=True),
            detail="an off-plan tab has vanished from the floor screen")

    s.section("A tab opened before the floor plan existed lands on its table")
    # How this actually looked on the real till: the band said three tables were
    # open and six covers were sitting, and every table in the room said TAP TO
    # SEAT, because those tabs predate table_id. The counters and the room
    # disagreed, and the tabs themselves were under a heading below the fold.
    legacy = _table("-L", area="salle", seats=2)
    conn = db()
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state,
           service_date, opened_at)
           VALUES (?, 2, 'open', 'seated', date('now'), datetime('now'))""",
        (legacy["label"],))
    conn.commit()
    legacy_order = conn.execute(
        "SELECT id FROM pos_orders ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()
    page = oc.get("/pos").get_data(as_text=True)
    import re as _re
    free_now = _re.findall(
        r'class="floor-tile is-free">\s*<span class="floor-table">([^<]+)', page)
    s.check("its table is not still offered as free",
            legacy["label"] not in free_now,
            detail=f"a tab is running on {legacy['label']} and the floor offers it "
                   "as empty — the counters and the room disagree")
    s.check("and seating it again is refused rather than opening a second tab",
            any("already open" in x for x in flashes(
                oc.post("/pos/open", data={"table_id": str(legacy["id"])},
                        follow_redirects=True))),
            detail="the same table can be sat twice")
    conn = db()
    conn.execute("DELETE FROM pos_orders WHERE id = ?", (legacy_order,))
    conn.commit()
    conn.close()

    s.section("Cash: what was handed over, and what goes back")
    bar = _table("-B", area="bar", seats=2)
    oc.post("/pos/open", data={"table_id": str(bar["id"])}, follow_redirects=True)
    tab = _latest_order()
    oc.post(f"/pos/{tab['id']}/add",
            data={"name": f"{TAG} Menu", "unit_price": "42.50", "quantity": "1"},
            follow_redirects=True)
    short = oc.post(f"/pos/{tab['id']}/pay",
                    data={"method": "cash", "cash_received": "20"}, follow_redirects=True)
    s.check("less than the bill is refused",
            any("short of" in x for x in flashes(short)), detail=f"{flashes(short)[:1]}")
    conn = db()
    still = conn.execute("SELECT status FROM pos_orders WHERE id = ?",
                         (tab["id"],)).fetchone()["status"]
    conn.close()
    s.check("and the tab stays open", still == "open", detail=f"status {still!r}")

    junk = oc.post(f"/pos/{tab['id']}/pay",
                   data={"method": "cash", "cash_received": "abc"}, follow_redirects=True)
    s.check("nonsense is refused rather than treated as zero",
            any("as a number" in x for x in flashes(junk)), detail=f"{flashes(junk)[:1]}")

    paid = oc.post(f"/pos/{tab['id']}/pay",
                   data={"method": "cash", "cash_received": "50"}, follow_redirects=True)
    said = flashes(paid)
    s.check("a fifty on a 42.50 bill gives the change", any("7.50" in x for x in said),
            detail=f"{said[:2]}")
    s.check("and the change is the first thing said, not buried",
            said and "Change" in said[0], detail=f"{said[:2]}")
    conn = db()
    ref = conn.execute(
        """SELECT reference FROM pos_payments WHERE order_id = ?
           ORDER BY id DESC LIMIT 1""", (tab["id"],)).fetchone()
    conn.close()
    s.check("what was tendered is recorded, not just the bill",
            ref is not None and "tendered" in (ref["reference"] or ""),
            detail=f"{dict(ref) if ref else None}")

    s.section("Exact change needs nothing typed")
    two = _table("-2", area="salle", seats=2)
    oc.post("/pos/open", data={"table_id": str(two["id"])}, follow_redirects=True)
    exact = _latest_order()
    oc.post(f"/pos/{exact['id']}/add",
            data={"name": f"{TAG} Coffee", "unit_price": "3.00", "quantity": "1"},
            follow_redirects=True)
    done = oc.post(f"/pos/{exact['id']}/pay", data={"method": "cash"}, follow_redirects=True)
    s.check("it settles with the field left blank",
            any("Settled" in x for x in flashes(done)), detail=f"{flashes(done)[:1]}")
    s.check("and no change is announced",
            not any("Change" in x for x in flashes(done)),
            detail="a tab paid to the penny reported change")

    s.section("Laying out the room")
    page = oc.get("/admin/restaurant/tables")
    s.check("the floor plan page loads", page.status_code == 200, page)
    oc.post("/admin/restaurant/tables/new",
            data={"label": f"{TAG}-NEW", "area": "terrace", "seats": "6"},
            follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT * FROM restaurant_tables WHERE label = ?",
                        (f"{TAG}-NEW",)).fetchone()
    conn.close()
    s.check("a table can be added", made is not None)
    s.check("in the right part of the room", made["area"] == "terrace")
    s.check("with its size", made["seats"] == 6)

    dup = oc.post("/admin/restaurant/tables/new",
                  data={"label": f"{TAG}-NEW", "area": "salle", "seats": "2"},
                  follow_redirects=True)
    conn = db()
    count = conn.execute("SELECT COUNT(*) AS c FROM restaurant_tables WHERE label = ?",
                         (f"{TAG}-NEW",)).fetchone()["c"]
    conn.close()
    s.check("two tables cannot share a name", count == 1,
            detail="a duplicate label makes the floor unreadable")
    s.check("and it says why", any("already a table" in x for x in flashes(dup)))

    s.section("A table nobody has sat at is just a typo")
    oc.post(f"/admin/restaurant/tables/{made['id']}/retire", follow_redirects=True)
    conn = db()
    gone = conn.execute("SELECT * FROM restaurant_tables WHERE id = ?",
                        (made["id"],)).fetchone()
    conn.close()
    s.check("it is deleted outright", gone is None,
            detail="an unused table was only retired, cluttering the plan")

    s.section("A table with history is retired, not deleted")
    s.check("the one with a tab on it cannot be removed while it is open",
            any("tab open" in x for x in flashes(
                oc.post(f"/admin/restaurant/tables/{four['id']}/retire",
                        follow_redirects=True))),
            detail="a table was taken off the floor mid-service")
    oc.post(f"/pos/{o['id']}/pay", data={"method": "comp"}, follow_redirects=True)
    oc.post(f"/admin/restaurant/tables/{four['id']}/retire", follow_redirects=True)
    conn = db()
    kept = conn.execute("SELECT * FROM restaurant_tables WHERE id = ?", (four["id"],)).fetchone()
    conn.close()
    s.check("it survives as a retired table", kept is not None and kept["active"] == 0,
            detail="deleting it would orphan the tabs that reference it")
    s.check("and its closed tab still says which table it was",
            _order_label(o["id"]) == four["label"],
            detail="renaming or retiring a table rewrote history")
    oc.post(f"/admin/restaurant/tables/{four['id']}/restore", follow_redirects=True)
    conn = db()
    back = conn.execute("SELECT active FROM restaurant_tables WHERE id = ?",
                        (four["id"],)).fetchone()["active"]
    conn.close()
    s.check("and it can be put back", back == 1)

    s.section("Guards")
    s.check("an employee cannot lay out the room",
            ec.get("/admin/restaurant/tables").status_code in (302, 403))
    s.check("nor add a table",
            ec.post("/admin/restaurant/tables/new",
                    data={"label": "x"}).status_code in (302, 403))
    s.check("nor retire one",
            ec.post(f"/admin/restaurant/tables/{four['id']}/retire").status_code in (302, 403))
    s.check("retiring one that does not exist is a 404",
            oc.post("/admin/restaurant/tables/999999/retire").status_code == 404)

    _cleanup()
    return s


def _order_label(order_id):
    conn = db()
    try:
        return conn.execute("SELECT table_label FROM pos_orders WHERE id = ?",
                            (order_id,)).fetchone()["table_label"]
    finally:
        conn.close()
