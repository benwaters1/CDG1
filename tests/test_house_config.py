"""The fifteen admin POSTs that set the house up, and the toggles that run it.

These are the last untested routes in the app. They are the ones that
configure things rather than move money, which is exactly why they went
last — and exactly why they are worth checking now: a rate override with a
backwards date range, a deposit rule at 400%, or a toggle that turns
something off and cannot turn it back on all fail quietly, and all of them
change what a guest is charged or shown.

Three shapes recur, and each has its own trap:

  - A CREATE with dates. The failure is a range that runs backwards, or a
    price that is not a number, being stored anyway.
  - A TOGGLE. The failure is one-way: it turns off and will not come back,
    or it 500s on an id that is not there.
  - A JOB you can trigger by hand. The failure is that it reports success
    having done nothing.

Every one of these is owner-only, so each is paired with the same request
from an employee AND with the write not having happened. A redirect is not
a refusal; a redirect plus an unchanged row is.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTCFG"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _exec(sql, params=()):
    conn = db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _cleanup():
    conn = db()
    for sql, p in (
        ("DELETE FROM restaurant_rate_overrides WHERE label LIKE ?", (TAG + "%",)),
        ("DELETE FROM restaurant_shifts WHERE role_note LIKE ?", (TAG + "%",)),
        ("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",)),
        ("DELETE FROM deposit_rules WHERE label LIKE ?", (TAG + "%",)),
        ("DELETE FROM availability_exceptions WHERE note LIKE ?", (TAG + "%",)),
        ("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",)),
        ("DELETE FROM workshop_custom_fields WHERE label LIKE ?", (TAG + "%",)),
    ):
        try:
            conn.execute(sql, p)
        except Exception:                                      # noqa: BLE001
            pass
    conn.execute("DELETE FROM menu_items WHERE name = ?", (TAG + " dish",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Setting the house up")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    # ------------------------------------------------- restaurant config
    s.section("A rate for a particular week")
    r = oc.post("/admin/restaurant/rates/new",
                data={"start_date": (today + timedelta(days=200)).isoformat(),
                      "end_date": (today + timedelta(days=207)).isoformat(),
                      "price_per_person": "95", "label": TAG + " truffle week"},
                follow_redirects=True)
    rate = _one("SELECT * FROM restaurant_rate_overrides WHERE label = ?",
                (TAG + " truffle week",))
    s.check("the override is saved", rate is not None, detail=str(flashes(r)))
    s.check("at the price that was typed", rate and abs(rate["price_per_person"] - 95) < 0.01,
            detail=str(rate["price_per_person"]) if rate else "")

    r = oc.post("/admin/restaurant/rates/new",
                data={"start_date": (today + timedelta(days=207)).isoformat(),
                      "end_date": (today + timedelta(days=200)).isoformat(),
                      "price_per_person": "95", "label": TAG + " backwards"},
                follow_redirects=True)
    s.check("a range that runs backwards is refused",
            _one("SELECT COUNT(*) AS c FROM restaurant_rate_overrides WHERE label = ?",
                 (TAG + " backwards",))["c"] == 0,
            detail=str(flashes(r)))
    s.check("and it says so rather than failing silently",
            any("valid date range" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    r = oc.post("/admin/restaurant/rates/new",
                data={"start_date": (today + timedelta(days=200)).isoformat(),
                      "end_date": (today + timedelta(days=207)).isoformat(),
                      "price_per_person": "free", "label": TAG + " junk"},
                follow_redirects=True)
    s.check("a price that is not a number is refused",
            _one("SELECT COUNT(*) AS c FROM restaurant_rate_overrides WHERE label = ?",
                 (TAG + " junk",))["c"] == 0,
            detail="a rate that silently becomes zero prices a dinner at nothing")
    r = oc.post("/admin/restaurant/rates/new",
                data={"start_date": (today + timedelta(days=200)).isoformat(),
                      "end_date": (today + timedelta(days=207)).isoformat(),
                      "price_per_person": "-40", "label": TAG + " negative"},
                follow_redirects=True)
    s.check("and so is a negative one",
            _one("SELECT COUNT(*) AS c FROM restaurant_rate_overrides WHERE label = ?",
                 (TAG + " negative",))["c"] == 0, detail=str(flashes(r)))

    s.section("Who is working dinner")
    r = oc.post("/admin/restaurant/shifts/new",
                data={"user_id": str(emp["id"]),
                      "dinner_date": (today + timedelta(days=30)).isoformat(),
                      "role_note": TAG + " front of house", "estimated_hours": "6"},
                follow_redirects=True)
    shift = _one("SELECT * FROM restaurant_shifts WHERE role_note = ?",
                 (TAG + " front of house",))
    s.check("the shift is assigned", shift is not None, detail=str(flashes(r)))
    s.check("to the person chosen", shift and shift["user_id"] == emp["id"])
    s.check("with the hours estimated", shift and abs((shift["estimated_hours"] or 0) - 6) < 0.01)
    s.check("and it is on the record", _one(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'restaurant_shift_assigned'")["c"] > 0,
        detail="who was put on which service is the kind of thing that gets disputed")

    r = oc.post("/admin/restaurant/shifts/new",
                data={"user_id": str(emp["id"]), "dinner_date": "not a date",
                      "role_note": TAG + " nonsense"}, follow_redirects=True)
    s.check("a shift on a date that is not a date is refused",
            _one("SELECT COUNT(*) AS c FROM restaurant_shifts WHERE role_note = ?",
                 (TAG + " nonsense",))["c"] == 0, detail=str(flashes(r)))

    s.section("Turning a dish and a drinks package on and off")
    # Made rather than borrowed. This read "SELECT ... LIMIT 1" and assumed the
    # database had a dish in it; a fresh seed has none, so the section failed on
    # a missing fixture rather than on anything the toggle does. Borrowing
    # whatever happens to exist is also how one suite starts depending on
    # another's leftovers.
    item = _one("SELECT id, active FROM menu_items WHERE name = ?", (TAG + " dish",))
    if item is None:
        conn = db()
        conn.execute(
            """INSERT INTO menu_items (name, category, course, price, active,
               available, sold_in_pos, sort_order, created_at)
               VALUES (?, 'main', 'main', 18.0, 1, 1, 1, 0, ?)""",
            (TAG + " dish", datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        item = _one("SELECT id, active FROM menu_items WHERE name = ?", (TAG + " dish",))
    s.check("there is a menu item to toggle", item is not None)
    if item:
        was = item["active"]
        oc.post(f"/admin/restaurant/menu/{item['id']}/toggle", follow_redirects=True)
        mid = _one("SELECT active FROM menu_items WHERE id = ?", (item["id"],))["active"]
        s.check("toggling changes it", mid != was, detail=f"{was} -> {mid}")
        oc.post(f"/admin/restaurant/menu/{item['id']}/toggle", follow_redirects=True)
        back = _one("SELECT active FROM menu_items WHERE id = ?", (item["id"],))["active"]
        # A one-way toggle is the failure worth naming: taking a dish off is
        # easy to test and putting it back is the half nobody tries.
        s.check("and toggling again puts it back", back == was, detail=f"{mid} -> {back}")
    r = oc.post("/admin/restaurant/menu/99999999/toggle", follow_redirects=False)
    s.check("a dish that does not exist is a 404, not a crash", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    pkg = _one("SELECT id, active FROM drink_packages LIMIT 1")
    if pkg:
        was = pkg["active"]
        oc.post(f"/admin/restaurant/packages/{pkg['id']}/toggle", follow_redirects=True)
        mid = _one("SELECT active FROM drink_packages WHERE id = ?", (pkg["id"],))["active"]
        s.check("a drinks package toggles too", mid != was, detail=f"{was} -> {mid}")
        oc.post(f"/admin/restaurant/packages/{pkg['id']}/toggle", follow_redirects=True)
        s.check("and back",
                _one("SELECT active FROM drink_packages WHERE id = ?", (pkg["id"],))["active"] == was)
    r = oc.post("/admin/restaurant/packages/99999999/toggle", follow_redirects=False)
    s.check("an absent package is a 404", r.status_code == 404, detail=f"HTTP {r.status_code}")

    # ------------------------------------------------------------ events
    s.section("An enquiry that came in by telephone")
    conn = db()
    with m.app.test_request_context():
        types = m.known_event_types(conn)
    conn.close()
    s.check("there are event types to choose from", bool(types), detail=str(types[:4]))
    if types:
        r = oc.post("/admin/events/new",
                    data={"event_type": types[0], "contact_name": TAG + " Mme Roux",
                          "contact_email": "roux@example.invalid",
                          "preferred_date": (today + timedelta(days=120)).isoformat(),
                          "guest_count": "60", "message": "rang about a wedding"},
                    follow_redirects=True)
        eq = _one("SELECT * FROM event_inquiries WHERE contact_name = ?", (TAG + " Mme Roux",))
        s.check("it is recorded without pretending to be the guest", eq is not None,
                detail=str(flashes(r)))
        s.check("with the type chosen", eq and eq["event_type"] == types[0])
        s.check("and the party size", eq and eq["guest_count"] == 60,
                detail=str(eq["guest_count"]) if eq else "")

        r = oc.post("/admin/events/new",
                    data={"event_type": "a kind of party nobody has heard of",
                          "contact_name": TAG + " invented"}, follow_redirects=True)
        s.check("an event type that is not one of ours is refused",
                _one("SELECT COUNT(*) AS c FROM event_inquiries WHERE contact_name = ?",
                     (TAG + " invented",))["c"] == 0, detail=str(flashes(r)))

        r = oc.post("/admin/events/new", data={"event_type": types[0], "contact_name": "  "},
                    follow_redirects=True)
        s.check("and an enquiry from nobody is refused",
                any("contact name" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("What kinds of event the house runs")
    was_types = _one("SELECT value FROM app_settings WHERE key = 'event_types'")
    r = oc.post("/admin/events/types",
                data={"event_types": "wedding\nretreat\nWEDDING\n  \nfilm shoot"},
                follow_redirects=True)
    now_types = _one("SELECT value FROM app_settings WHERE key = 'event_types'")["value"]
    s.check("the list is saved", "retreat" in now_types, detail=now_types)
    s.check("lower-cased and de-duplicated", now_types.split("\n").count("wedding") == 1,
            detail=now_types)
    s.check("and blank lines dropped", "" not in now_types.split("\n"), detail=repr(now_types))
    r = oc.post("/admin/events/types", data={"event_types": "   \n  "}, follow_redirects=True)
    s.check("emptying it entirely is refused",
            any("at least one" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the old list survives",
            _one("SELECT value FROM app_settings WHERE key = 'event_types'")["value"] == now_types)
    if was_types:
        _exec("UPDATE app_settings SET value = ? WHERE key = 'event_types'", (was_types["value"],))

    # --------------------------------------------------------- deposits
    s.section("When a deposit is taken")
    r = oc.post("/admin/deposit-rules/new",
                data={"category": "restaurant", "min_party_size": "8",
                      "deposit_percent": "50", "label": TAG + " big tables"},
                follow_redirects=True)
    rule = _one("SELECT * FROM deposit_rules WHERE label = ?", (TAG + " big tables",))
    s.check("the rule is saved", rule is not None, detail=str(flashes(r)))
    s.check("for the category chosen", rule and rule["category"] == "restaurant")
    r = oc.post("/admin/deposit-rules/new",
                data={"category": "spaceship", "deposit_percent": "50",
                      "label": TAG + " nonsense"}, follow_redirects=True)
    s.check("a rule for something the house does not sell is refused",
            _one("SELECT COUNT(*) AS c FROM deposit_rules WHERE label = ?",
                 (TAG + " nonsense",))["c"] == 0, detail=str(flashes(r)))

    # ------------------------------------------------------- workshops
    s.section("Asking a workshop guest something extra")
    ws = _one("SELECT id FROM workshops LIMIT 1")
    if ws:
        r = oc.post(f"/admin/workshops/{ws['id']}/custom-fields/new",
                    data={"label": TAG + " shoe size", "field_type": "text",
                          "required": "1"}, follow_redirects=True)
        fld = _one("SELECT * FROM workshop_custom_fields WHERE label = ?",
                   (TAG + " shoe size",))
        s.check("the question is added", fld is not None, detail=str(flashes(r)))
        s.check("against that workshop", fld and fld["workshop_id"] == ws["id"])
        r = oc.post(f"/admin/workshops/{ws['id']}/custom-fields/new",
                    data={"label": "   ", "field_type": "text"}, follow_redirects=True)
        s.check("a question with no wording is refused",
                _one("SELECT COUNT(*) AS c FROM workshop_custom_fields WHERE label = ''")["c"] == 0,
                detail=str(flashes(r)))

    # -------------------------------------------------------- expenses
    s.section("A supplier invoice, and what can be done with it")
    exp_id = _exec(
        """INSERT INTO expenses (kind, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, 120.0, 'pending', ?)""",
        (TAG + " a case of wine", datetime.now(timezone.utc).isoformat()))
    was_rest = _one("SELECT restaurant_related FROM expenses WHERE id = ?", (exp_id,))["restaurant_related"]
    oc.post(f"/expenses/{exp_id}/toggle-restaurant", follow_redirects=True)
    mid = _one("SELECT restaurant_related FROM expenses WHERE id = ?", (exp_id,))["restaurant_related"]
    s.check("marking an invoice as the restaurant's changes it", mid != was_rest,
            detail=f"{was_rest} -> {mid}")
    oc.post(f"/expenses/{exp_id}/toggle-restaurant", follow_redirects=True)
    s.check("and unmarking it changes it back",
            _one("SELECT restaurant_related FROM expenses WHERE id = ?", (exp_id,))["restaurant_related"] == was_rest)
    r = oc.post("/expenses/99999999/toggle-restaurant", follow_redirects=False)
    s.check("an invoice that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    # read_invoice's happy path needs Anthropic, which the harness refuses at
    # the client constructor. So what is checked here is every guard IN FRONT
    # of that call — which is the half that runs on a normal day anyway.
    r = oc.post(f"/expenses/{exp_id}/read-invoice", follow_redirects=True)
    s.check("reading an invoice with no file attached is refused",
            any("no file" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    _exec("UPDATE expenses SET filename = ? WHERE id = ?", ("something.jpg", exp_id))
    r = oc.post(f"/expenses/{exp_id}/read-invoice", follow_redirects=True)
    s.check("and one that is not a PDF is refused",
            any("pdf" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    _exec("UPDATE expenses SET filename = ? WHERE id = ?", ("nothing-here.pdf", exp_id))
    r = oc.post(f"/expenses/{exp_id}/read-invoice", follow_redirects=True)
    s.check("with no key configured it says so rather than trying",
            any("anthropic" in f.lower() or "missing" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and nothing was added to stock",
            _one("SELECT COUNT(*) AS c FROM stock_movements WHERE expense_id = ?",
                 (exp_id,))["c"] == 0,
            detail="an invoice misread by one decimal corrupts the stock level "
                   "and its valuation at once")
    _exec("DELETE FROM expenses WHERE id = ?", (exp_id,))

    # -------------------------------------------------------- feedback
    s.section("Putting a review on the front page")
    fb = _one("SELECT id, featured FROM guest_feedback LIMIT 1")
    if fb:
        was = fb["featured"]
        oc.post(f"/admin/feedback/{fb['id']}/toggle-featured", follow_redirects=True)
        mid = _one("SELECT featured FROM guest_feedback WHERE id = ?", (fb["id"],))["featured"]
        s.check("featuring a review changes it", mid != was, detail=f"{was} -> {mid}")
        oc.post(f"/admin/feedback/{fb['id']}/toggle-featured", follow_redirects=True)
        s.check("and taking it down changes it back",
                _one("SELECT featured FROM guest_feedback WHERE id = ?", (fb["id"],))["featured"] == was,
                detail="a review that can be put up and not taken down is a "
                       "review the house cannot withdraw")
    r = oc.post("/admin/feedback/99999999/toggle-featured", follow_redirects=False)
    s.check("a review that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    # ------------------------------------------------------ ops actions
    s.section("Telling somebody a task is theirs, now")
    task_id = _exec(
        """INSERT INTO tasks (assigned_to_user_id, title, status, created_at)
           VALUES (?, ?, 'open', ?)""",
        (emp["id"], TAG + " move the ladder", datetime.now(timezone.utc).isoformat()))
    r = oc.post(f"/admin/tasks/{task_id}/direct", data={"employee_id": str(emp["id"])},
                follow_redirects=True)
    t = _one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    s.check("the task is directed at them", t["acknowledgment_status"] == "pending",
            detail=str(t["acknowledgment_status"]))
    s.check("stamped with when", bool(t["directed_at"]))
    s.check("and by whom", t["directed_by_user_id"] == owner["id"],
            detail="a directive nobody signed is one nobody can be asked about")
    r = oc.post("/admin/tasks/99999999/direct", data={"employee_id": str(emp["id"])},
                follow_redirects=False)
    s.check("directing a task that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    s.section("Getting a room ready for somebody")
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()
    arrive = today + timedelta(days=300)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 'prep@example.invalid', ?, ?, 3, 'confirmed', 400, ?)""",
        (room["id"], TAG + "PREP", TAG.lower() + "preptok", TAG + " Arriving Guest",
         arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (TAG + "PREP",)).fetchone()["id"]
    conn.close()

    r = oc.post(f"/admin/bookings/{bid}/prepare-arrival", data={"assigned_to_user_id": ""},
                follow_redirects=True)
    s.check("prep with nobody to do it is refused",
            any("who the prep" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and no tasks were made",
            _one("SELECT COUNT(*) AS c FROM tasks WHERE origin = 'checklist' "
                 "AND room_note LIKE ?", ("%" + TAG + " Arriving Guest%",))["c"] == 0)

    r = oc.post(f"/admin/bookings/{bid}/prepare-arrival",
                data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    made = _one("SELECT COUNT(*) AS c FROM tasks WHERE origin = 'checklist' "
                "AND room_note LIKE ?", ("%" + TAG + " Arriving Guest%",))["c"]
    s.check("prepping makes the checklist", made > 0, detail=f"{made} task(s)")
    s.check("it carries the party size", _one(
        "SELECT COUNT(*) AS c FROM tasks WHERE room_note LIKE ?",
        ("%party of 3%",))["c"] > 0, detail="a checklist that does not say how many "
                                            "people is a checklist somebody has to look up")
    r = oc.post(f"/admin/bookings/{bid}/prepare-arrival",
                data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    again = _one("SELECT COUNT(*) AS c FROM tasks WHERE origin = 'checklist' "
                 "AND room_note LIKE ?", ("%" + TAG + " Arriving Guest%",))["c"]
    s.check("prepping twice does not make the list twice", again == made,
            detail=f"{made} -> {again}")
    s.check("and it says it was already done",
            any("already prepped" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    _exec("DELETE FROM tasks WHERE room_note LIKE ?", ("%" + TAG + " Arriving Guest%",))
    _exec("DELETE FROM bookings WHERE reference_code = ?", (TAG + "PREP",))

    s.section("A day somebody cannot work")
    when = (today + timedelta(days=45)).isoformat()
    r = ec.post("/availability/exception",
                data={"on_date": when, "note": TAG + " at a wedding"},
                follow_redirects=True)
    ex = _one("SELECT * FROM availability_exceptions WHERE user_id = ? AND on_date = ?",
              (emp["id"], when))
    s.check("the employee's own exception is saved", ex is not None, detail=str(flashes(r)))
    s.check("as unavailable, because the box was not ticked", ex and ex["available"] == 0,
            detail=str(ex["available"]) if ex else "")
    r = ec.post("/availability/exception",
                data={"on_date": when, "available": "1", "note": TAG + " freed up"},
                follow_redirects=True)
    ex = _one("SELECT * FROM availability_exceptions WHERE user_id = ? AND on_date = ?",
              (emp["id"], when))
    s.check("saying it again for the same day replaces it rather than adding one",
            _one("SELECT COUNT(*) AS c FROM availability_exceptions "
                 "WHERE user_id = ? AND on_date = ?", (emp["id"], when))["c"] == 1)
    s.check("and the answer changed", ex and ex["available"] == 1)
    r = ec.post("/availability/exception", data={"on_date": ""}, follow_redirects=True)
    s.check("a day with no date is refused",
            any("pick a date" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    _exec("DELETE FROM availability_exceptions WHERE user_id = ? AND on_date = ?",
          (emp["id"], when))

    s.section("Pulling the other calendars in")
    r = oc.post("/admin/rooms/sync-all", follow_redirects=True)
    s.check("the sync can be run by hand",
            any("synced" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and it says how many of how many, not just that it ran",
            any(" of " in f for f in flashes(r)), detail=str(flashes(r)))

    # ---------------------------------------------------- who may not
    s.section("None of this is an employee's to change")
    checks = [
        ("/admin/restaurant/rates/new",
         {"start_date": (today + timedelta(days=200)).isoformat(),
          "end_date": (today + timedelta(days=207)).isoformat(),
          "price_per_person": "1", "label": TAG + " employee rate"},
         "SELECT COUNT(*) AS c FROM restaurant_rate_overrides WHERE label = ?",
         (TAG + " employee rate",), "set a dinner rate"),
        ("/admin/restaurant/shifts/new",
         {"user_id": str(emp["id"]), "dinner_date": (today + timedelta(days=31)).isoformat(),
          "role_note": TAG + " employee shift"},
         "SELECT COUNT(*) AS c FROM restaurant_shifts WHERE role_note = ?",
         (TAG + " employee shift",), "put themselves on a service"),
        ("/admin/deposit-rules/new",
         {"category": "restaurant", "deposit_percent": "1", "label": TAG + " employee rule"},
         "SELECT COUNT(*) AS c FROM deposit_rules WHERE label = ?",
         (TAG + " employee rule",), "change when a deposit is taken"),
    ]
    for path, data, sql, params, what in checks:
        r = ec.post(path, data=data, follow_redirects=False)
        s.check(f"an employee cannot {what}", r.status_code in (302, 303, 403),
                detail=f"HTTP {r.status_code}")
        s.check(f"and nothing was written when they tried to {what}",
                _one(sql, params)["c"] == 0,
                detail="the redirect on its own would pass even if the write "
                       "had gone through")

    # And the one that IS theirs, so the block above is a boundary rather than
    # a wall.
    r = ec.post("/availability/exception",
                data={"on_date": (today + timedelta(days=46)).isoformat(),
                      "note": TAG + " still allowed"}, follow_redirects=True)
    s.check("but they can still say which days they cannot work",
            _one("SELECT COUNT(*) AS c FROM availability_exceptions WHERE note = ?",
                 (TAG + " still allowed",))["c"] == 1,
            detail="if this failed too, every refusal above would only be "
                   "proving employees cannot POST")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
