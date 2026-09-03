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
import re

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
    s.check("including the dearest room, which is the one LIMIT 4 used to drop",
            rooms and rooms[0] in html, detail=f"looked for {rooms[0]!r}")

    # The rename only helps if it is the same everywhere a guest looks. A
    # front page advertising Chambre Emeraude and a booking page offering
    # Suite with Mountain View is two rooms as far as the guest knows.
    #
    # Read out of the HEADING, not looked for anywhere on the page. Written
    # the loose way first, and it did not notice the heading being swapped
    # back to the database name — because the alt text beside it still
    # carried the French one, so the string was "on the page" either way.
    headings = re.findall(r'<h3 class="g-homeroom__name">(.*?)</h3>', html, re.S)
    headings = [h.strip() for h in headings]
    s.check("the room cards are headed by name at all", headings,
            detail="no room headings found — the selector this reads has moved, "
                   "and the two checks below would pass on an empty list")
    wrong = [h for h in headings if h not in rooms]
    s.check("every room card is headed by the name a guest is shown",
            not wrong, detail=f"{wrong} — the front page is using a name the "
                              "rest of the site does not")
    # The facts the old names carried. The rooms used to be called things
    # like "Twin/Double with Shared Bathroom", and the booking page read that
    # back by looking for the word "shared" in the name. Now they have names
    # of their own, those facts have to be in the columns or they are simply
    # gone -- and the page would go quiet about the bathroom on the room that
    # shares one.
    conn = db()
    try:
        thin = [r["name"] for r in conn.execute(
            "SELECT name, bathroom, bed_setup FROM rooms WHERE active = 1")
            if not (r["bathroom"] or "").strip() or not (r["bed_setup"] or "").strip()]
    finally:
        conn.close()
    # And the English gloss. A French name is a better name and a worse
    # description: "Chambre Tilleul" does not tell a guest it has the shared
    # bathroom. A room missing from room_note() loses that line silently and
    # reads as the one room nobody wrote anything about.
    gloss = m.app.jinja_env.get_template("_room_copy.html").module.room_note
    unglossed = [r for r in _order() if not str(gloss(r)).strip()]
    s.check("and every room has its English gloss", not unglossed,
            detail=f"{unglossed} — not in room_note(), so the line that says "
                   "which room shares a bathroom is simply absent")

    s.check("every room says what its bed and bathroom are",
            not thin,
            detail=f"{thin} — a room with no bathroom recorded says nothing "
                   "about it on the booking page, which is the honest failure; "
                   "the old code guessed 'private' from the name")

    listing = anon.get("/book").get_data(as_text=True)
    absent = [h for h in headings if h not in listing]
    s.check("and the booking page calls them the same thing", not absent,
            detail=f"{absent} — named one way on the front page and another "
                   "where the guest goes to book")

    s.section("No page works out what a room is from what it is called")
    # This is the ratchet, and it is the reason the rooms could not simply be
    # renamed. The booking page decided whether to promise a private bathroom
    # by looking for the word "shared" in the room's NAME, and the bed, the
    # size and the view the same way. So the name was load-bearing and nothing
    # said so: renaming a room in the admin -- which the owner can do, from a
    # form, with no warning -- silently changed "Shared bathroom" to "Private
    # bathroom" on a room that shares one. The facts live in columns now.
    import glob as _glob
    import os as _os
    import re as _re
    guessers = []
    for path_ in _glob.glob(_os.path.join(_harness.ROOT, "templates", "*.html")):
        html = open(path_, encoding="utf-8").read()
        # A name lowercased into a variable, then that variable tested for a
        # word. Emblems are exempt: they fall back to a fleuron, so the worst
        # case is the wrong flourish rather than the wrong bathroom.
        if _os.path.basename(path_) == "_emblems.html":
            continue
        for var in _re.findall(r"set\s+(\w+)\s*=\s*\(\s*\w+\[.name.\]\s*or\s*..\)\|lower", html):
            for word in _re.findall(r"'(\w+)' in %s(?!\w)" % var, html):
                guessers.append(f"{_os.path.basename(path_)}: '{word}' in the room name")
    s.check("no template reads a room's facts out of its name", not guessers,
            detail="; ".join(guessers[:4]) + " — put it in a column: "
                   "bathroom, bed_setup, outlook, size_sqm, floor")

    s.section("The room picker sends you to the room it named")
    # It shipped with the rooms written out by hand, ids and all -- 5, 4, 2,
    # 1, 3 against real ids of 8, 6, 4, 5, 7. So it recommended the Emeraude
    # and opened Les Deux Chambres, recommended the Levant and 404'd, and did
    # that for all five. A copy of the database is a copy that goes wrong
    # quietly, and this one went wrong in front of a guest choosing a room.
    picker = anon.get("/book").get_data(as_text=True)
    s.check("the picker is on the booking page", "var R=[" in picker,
            detail="if this moves, the two checks below prove nothing")
    if "var R=[" in picker:
        block = picker[picker.index("var R=["):]
        block = block[:block.index("];")]
        pairs = _re.findall(r'n:"([^"]+)".*?url:"([^"]+)"', block, _re.S)
        conn2 = db()
        try:
            by_id = {str(r["id"]): r["name"] for r in
                     conn2.execute("SELECT id, name FROM rooms").fetchall()}
        finally:
            conn2.close()
        s.check("it offers a room for every room the house lets",
                len(pairs) == len(_order()), detail=f"{len(pairs)} offered")
        wrong = []
        for name, url in pairs:
            named = name.encode().decode("unicode_escape")
            landed = by_id.get(url.rstrip("/").split("/")[-1])
            if landed != named:
                wrong.append(f"{named} -> {url} ({landed or 'nothing'})")
        s.check("and each link opens the room it just recommended", not wrong,
                detail="; ".join(wrong)[:200])

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
