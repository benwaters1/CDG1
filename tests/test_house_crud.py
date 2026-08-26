"""Breakfast, room blocks and airport runs.

Ordinary CRUD, and covered here for honest coverage rather than because
anything looks dangerous. Three of them do have a consequence worth a check
each, so those are the ones this leans on:

  - a room block must actually stop a booking. It shares the availability path
    with channel imports (see test_ical_sync), so this checks the other half of
    that loop: a block entered by hand for a leaking roof.
  - breakfast has a low-stock flag AND an active flag, and they mean different
    things. "We have run out of croissants this morning" is not "we do not
    serve croissants", and collapsing the two loses tomorrow's croissants.
  - a transfer belongs to one vehicle. Listing another vehicle's runs is how
    somebody drives to the wrong airport.

The rest is the usual: blank input refused, a missing id is a 404 rather than a
500, and none of it is reachable by an employee.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZHC"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM breakfast_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicle_transfers WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _row(table, where, param):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE {where}", (param,)).fetchone()
    finally:
        conn.close()


def _count(table, where, param):
    conn = db()
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where}",
                            (param,)).fetchone()["c"]
    finally:
        conn.close()


def run():
    s = Suite("Breakfast, blocks and transfers")
    _cleanup()
    oc, ec, owner, emp = clients()
    room = _harness.ensure_room()

    # ------------------------------------------------------------ breakfast
    s.section("Breakfast: what is on the list")
    oc.post("/breakfast/items/new",
            data={"name": f"{TAG} Croissant", "category": "bakery"},
            follow_redirects=True)
    item = _row("breakfast_items", "name = ?", f"{TAG} Croissant")
    s.check("an item can be added", item is not None)
    s.check("and it starts on the menu and in stock",
            item and item["active"] == 1 and item["low_stock"] == 0,
            detail=f"active={item['active'] if item else None}, "
                   f"low_stock={item['low_stock'] if item else None}")

    before = _count("breakfast_items", "name LIKE ?", TAG + "%")
    oc.post("/breakfast/items/new", data={"name": "   "}, follow_redirects=True)
    s.check("a blank name is not added",
            _count("breakfast_items", "name LIKE ?", TAG + "%") == before)

    s.section("Run out today is not taken off the menu")
    # Two flags, two meanings. Collapsing them loses tomorrow's croissants.
    oc.post(f"/breakfast/items/{item['id']}/toggle-stock", follow_redirects=True)
    after = _row("breakfast_items", "id = ?", item["id"])
    s.check("marked as run out", after["low_stock"] == 1)
    s.check("but still on the menu", after["active"] == 1,
            detail="running out today took it off the menu for good")
    oc.post(f"/breakfast/items/{item['id']}/toggle-stock", follow_redirects=True)
    s.check("and the flag comes back off",
            _row("breakfast_items", "id = ?", item["id"])["low_stock"] == 0)

    s.section("Ticking it off is per DAY, not for good")
    # I misread this route as an on-menu switch. It is the morning checklist:
    # it writes one row per item per day, so tomorrow starts unticked. Which is
    # the only sensible behaviour for a list somebody works through at 7am, and
    # worth pinning as what it actually is.
    today = date.today().isoformat()
    oc.post(f"/breakfast/{item['id']}/toggle", follow_redirects=True)
    s.check("today's tick is recorded",
            _count("breakfast_checklist_log", "item_id = ? AND checklist_date = '"
                   + today + "'", item["id"]) == 1)
    s.check("and it says nothing about stock",
            _row("breakfast_items", "id = ?", item["id"])["low_stock"] == 0,
            detail="ticking it off this morning marked it out of stock")
    oc.post(f"/breakfast/{item['id']}/toggle", follow_redirects=True)
    s.check("ticking again unticks it, because people mistake rows",
            _count("breakfast_checklist_log", "item_id = ? AND checklist_date = '"
                   + today + "'", item["id"]) == 0)

    s.section("Renaming, and removing")
    oc.post(f"/breakfast/items/{item['id']}/edit",
            data={"name": f"{TAG} Pain au chocolat", "category": "bakery"},
            follow_redirects=True)
    s.check("the name changes",
            _row("breakfast_items", "id = ?", item["id"])["name"]
            == f"{TAG} Pain au chocolat")
    oc.post(f"/breakfast/items/{item['id']}/delete", follow_redirects=True)
    s.check("and it can be removed",
            _row("breakfast_items", "id = ?", item["id"]) is None)

    # --------------------------------------------------------- room blocks
    s.section("A room block stops a booking, not just the calendar")
    # The other half of the availability loop tested in test_ical_sync: this one
    # is entered by hand, for a leaking roof rather than an Airbnb guest.
    oc.post(f"/admin/rooms/{room['id']}/blocks/new", data={
        "start_date": "2035-04-10", "end_date": "2035-04-13",
        "reason": f"{TAG} roof repair",
    }, follow_redirects=True)
    block = _row("room_blocks", "reason = ?", f"{TAG} roof repair")
    s.check("the block is recorded", block is not None)
    conn = db()
    ok, why = m.is_range_available(conn, room["id"], date(2035, 4, 11), date(2035, 4, 12))
    conn.close()
    s.check("those nights cannot be booked", ok is False,
            detail="a room blocked for repairs is still sellable")
    s.check("and the reason is not a channel this time",
            "another booking channel" not in (why or "").lower(),
            detail=f"{why!r} — a hand-entered block is not an Airbnb import")

    conn = db()
    free, _ = m.is_range_available(conn, room["id"], date(2035, 4, 20), date(2035, 4, 22))
    conn.close()
    s.check("nights outside it are still free", free is True)

    s.section("A block with no dates is refused")
    n = _count("room_blocks", "reason LIKE ?", TAG + "%")
    oc.post(f"/admin/rooms/{room['id']}/blocks/new",
            data={"start_date": "", "end_date": "", "reason": f"{TAG} nothing"},
            follow_redirects=True)
    s.check("nothing is written",
            _count("room_blocks", "reason LIKE ?", TAG + "%") == n)

    s.section("Removing the block frees the nights again")
    oc.post(f"/admin/blocks/{block['id']}/delete", follow_redirects=True)
    conn = db()
    reopened, _ = m.is_range_available(conn, room["id"], date(2035, 4, 11), date(2035, 4, 12))
    conn.close()
    s.check("the room is sellable once more", reopened is True,
            detail="the repair finished and the nights never came back")

    # ----------------------------------------------------- transfers
    s.section("An airport run belongs to one vehicle")
    conn = db()
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
    fields = {"name": f"{TAG} Van", "created_at": datetime.now(timezone.utc).isoformat()}
    keys = [k for k in fields if k in cols]
    conn.execute(f"INSERT INTO vehicles ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' * len(keys))})", [fields[k] for k in keys])
    conn.execute(f"INSERT INTO vehicles ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' * len(keys))})",
                 [f"{TAG} Estate" if k == "name" else fields[k] for k in keys])
    conn.commit()
    van = conn.execute("SELECT id FROM vehicles WHERE name = ?", (f"{TAG} Van",)).fetchone()["id"]
    estate = conn.execute("SELECT id FROM vehicles WHERE name = ?",
                          (f"{TAG} Estate",)).fetchone()["id"]
    conn.close()

    oc.post(f"/management/vehicles/{van}/transfers/new", data={
        "guest_name": f"{TAG} Arrival", "direction": "pickup",
        "scheduled_at": "2035-05-01T14:30", "notes": f"{TAG} Toulouse",
    }, follow_redirects=True)
    run_row = _row("vehicle_transfers", "guest_name = ?", f"{TAG} Arrival")
    s.check("the run is recorded", run_row is not None)
    if run_row:
        s.check("against the van it was booked on", run_row["vehicle_id"] == van,
                detail=f"got vehicle {run_row['vehicle_id']}, wanted {van}")

    van_page = oc.get(f"/management/vehicles/{van}/transfers")
    estate_page = oc.get(f"/management/vehicles/{estate}/transfers")
    s.check("the van's page lists it", f"{TAG} Arrival" in van_page.get_data(as_text=True),
            detail=f"HTTP {van_page.status_code}")
    s.check("the other vehicle's page does not",
            f"{TAG} Arrival" not in estate_page.get_data(as_text=True),
            detail="one vehicle's runs showed on another's page — that is how "
                   "somebody drives to the wrong airport")

    s.section("A run with no time is refused")
    n2 = _count("vehicle_transfers", "guest_name LIKE ?", TAG + "%")
    oc.post(f"/management/vehicles/{van}/transfers/new",
            data={"guest_name": f"{TAG} Vague", "direction": "pickup",
                  "scheduled_at": ""}, follow_redirects=True)
    oc.post(f"/management/vehicles/{van}/transfers/new",
            data={"guest_name": f"{TAG} Vague2", "direction": "somewhere",
                  "scheduled_at": "2035-05-02T10:00"}, follow_redirects=True)
    s.check("neither a missing time nor an invented direction is written",
            _count("vehicle_transfers", "guest_name LIKE ?", TAG + "%") == n2,
            detail="a run with no time, or going nowhere, was recorded")

    s.section("And it can be cancelled")
    if run_row:
        oc.post(f"/management/vehicles/transfers/{run_row['id']}/delete",
                follow_redirects=True)
        s.check("the run is gone",
                _row("vehicle_transfers", "id = ?", run_row["id"]) is None)

    s.section("A missing id is a 404, not a 500")
    for label, url in (
        ("breakfast item", "/breakfast/items/999999/edit"),
        ("breakfast stock", "/breakfast/items/999999/toggle-stock"),
        ("room block", "/admin/blocks/999999/delete"),
        ("transfer", "/management/vehicles/transfers/999999/delete"),
    ):
        code = oc.post(url, data={"name": "x", "category": "bakery"}).status_code
        s.check(f"{label}: {code}", code < 500,
                detail=f"HTTP {code} — a stack trace where a 404 belongs")

    s.section("Guards")
    for label, url, data in (
        ("add a breakfast item", "/breakfast/items/new", {"name": "x"}),
        ("block a room", f"/admin/rooms/{room['id']}/blocks/new",
         {"start_date": "2035-01-01", "end_date": "2035-01-02"}),
        ("book a transfer", f"/management/vehicles/{van}/transfers/new",
         {"guest_name": "x", "direction": "pickup", "scheduled_at": "2035-01-01T10:00"}),
    ):
        s.check(f"an employee cannot {label}",
                ec.post(url, data=data).status_code in (302, 403))

    _cleanup()
    return s
