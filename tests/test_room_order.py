"""Which rooms the front page shows, and in whose order.

Two faults, and the second is why the first lasted.

`dashboard()` selected the rooms with `LIMIT 4`. There are five, so the page
advertised the house with one room missing — and because the cut was taken
after `ORDER BY sort_order`, the room dropped was whichever sat last in an
order nobody had chosen. It was the Suite with Mountain View, the dearest room
in the château at €450 and the one with the best bed in it.

Nothing in the admin could set `sort_order`. Not on the room form, not on the
rooms list, nowhere — so the only way to change what the front page led with
was to edit the database by hand. That is the actual bug: a decision the owner
should own was unreachable, so it went unmade for as long as the site has been
up.

Both halves are checked here, plus the thing that would undo it: a deploy
re-applying the one-time order correction over an order the owner has since
set by hand.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m


def _order(active_only=True):
    conn = db()
    try:
        where = "WHERE active = 1" if active_only else ""
        return [r["name"] for r in conn.execute(
            f"SELECT name FROM rooms {where} ORDER BY sort_order, price_per_night")]
    finally:
        conn.close()


def _ids():
    conn = db()
    try:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM rooms ORDER BY sort_order, price_per_night, id")]
    finally:
        conn.close()


def run():
    s = Suite("Room order")
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("The front page shows every room, not four of five")
    page = anon.get("/")
    html = page.get_data(as_text=True)
    s.check("the home page loads", page.status_code == 200, page)
    rooms = _order()
    missing = [r for r in rooms if r not in html]
    s.check(f"all {len(rooms)} rooms are on it", not missing,
            detail=f"absent: {missing} — the page is advertising the house "
                   "without one of its rooms")
    s.check("including the Suite, which is the one LIMIT 4 used to drop",
            "Suite with Mountain View" in html)

    s.section("The order is the one the owner asked for")
    # Suite, Double, King, Family Suite, Twin/Double — applied once as a
    # correction to the arbitrary order the rooms were seeded in.
    s.check("it matches HOME_ROOM_ORDER", rooms == m.HOME_ROOM_ORDER,
            detail=f"got {rooms}")
    positions = [html.index(name) for name in rooms if name in html]
    s.check("and the page renders them in that order",
            positions == sorted(positions),
            detail="the query order and the page order disagree")

    s.section("The owner can change it without touching the database")
    before = _order()
    first = _ids()[0]
    oc.post(f"/admin/rooms/{first}/move", data={"direction": "down"},
            follow_redirects=True)
    moved = _order()
    s.check("moving the top room down swaps it with the next",
            moved[0] == before[1] and moved[1] == before[0],
            detail=f"{before[:2]} -> {moved[:2]}")
    s.check("and nothing else shifts", moved[2:] == before[2:],
            detail=f"{before[2:]} -> {moved[2:]}")

    oc.post(f"/admin/rooms/{_ids()[1]}/move", data={"direction": "up"},
            follow_redirects=True)
    s.check("and moving it back restores the order", _order() == before,
            detail=f"got {_order()}")

    s.section("The ends do not fall off")
    top, bottom = _ids()[0], _ids()[-1]
    oc.post(f"/admin/rooms/{top}/move", data={"direction": "up"}, follow_redirects=True)
    s.check("moving the first room up changes nothing", _order() == before,
            detail=f"got {_order()}")
    oc.post(f"/admin/rooms/{bottom}/move", data={"direction": "down"}, follow_redirects=True)
    s.check("nor does moving the last one down", _order() == before,
            detail=f"got {_order()}")

    s.section("Positions stay contiguous, so the buttons keep working")
    # Seeded and hand-added rooms could share a sort_order, and swapping two
    # equal values does nothing — the button looks broken rather than being it.
    conn = db()
    conn.execute("UPDATE rooms SET sort_order = 7")
    conn.commit()
    conn.close()
    oc.post(f"/admin/rooms/{_ids()[0]}/move", data={"direction": "down"},
            follow_redirects=True)
    conn = db()
    orders = [r["sort_order"] for r in conn.execute(
        "SELECT sort_order FROM rooms ORDER BY sort_order")]
    conn.close()
    s.check("a move renumbers them 0..n-1", orders == list(range(len(orders))),
            detail=f"got {orders} — duplicates make the next move a no-op")

    # put the owner's order back for every suite that follows
    conn = db()
    for position, name in enumerate(m.HOME_ROOM_ORDER):
        conn.execute("UPDATE rooms SET sort_order = ? WHERE name = ?", (position, name))
    conn.commit()
    conn.close()

    s.section("A deploy does not overwrite an order somebody set")
    # The one-time correction is keyed in app_settings. If that key were ever
    # dropped, or the guard removed, the next restart would shuffle the front
    # page back and nobody would know why.
    conn = db()
    keyed = conn.execute("SELECT 1 FROM app_settings WHERE key = ?",
                         ("rooms_initial_order_set",)).fetchone()
    conn.close()
    s.check("the correction records that it has run", keyed is not None,
            detail="without this key the order is re-applied on every start")

    s.section("Guards")
    s.check("an employee cannot reorder the rooms",
            ec.post(f"/admin/rooms/{_ids()[0]}/move",
                    data={"direction": "down"}).status_code in (302, 403))
    s.check("and the order is untouched by the attempt", _order() == m.HOME_ROOM_ORDER,
            detail=f"got {_order()}")
    s.check("an invented direction is refused",
            oc.post(f"/admin/rooms/{_ids()[0]}/move",
                    data={"direction": "sideways"}).status_code == 400)
    s.check("a room that does not exist is a 404, not a 500",
            oc.post("/admin/rooms/999999/move",
                    data={"direction": "up"}).status_code == 404)
    s.check("and the order survived all of that", _order() == m.HOME_ROOM_ORDER,
            detail=f"got {_order()}")

    return s
