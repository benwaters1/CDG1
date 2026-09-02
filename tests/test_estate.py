"""The building's own register: what is in it, what drives, what to do.

Ten routes with nothing on them. The asset register is the one that would be
read out to an insurer after a fire, the vehicle rows are what say whether the
van is due a service, and the manual is what somebody opens when they do not
know how the boiler works.

Two things here are load-bearing and neither is obvious from the page:

  - `asset_photo` serves by ASSET id and looks the filename up from the row.
    Its docstring says why: a filename taken from the URL would let anyone
    with a session walk the uploads directory, which also holds contracts,
    payslips and receipts. That is a property of one line and it is worth a
    check that fails if the line changes.
  - A photo that is not a recognised type is skipped, and the asset is still
    recorded. Losing a picture is a nuisance; losing the record of a
    seventeenth-century commode because the photo was a .heic is not.

Logging a service is the other one worth pinning. It moves the next-due date
six months on AND writes a maintenance row, and it is the date that decides
whether the vehicle shows up as overdue anywhere else.
"""
import os
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZTEST8"


def _cleanup():
    conn = db()
    for r in conn.execute("SELECT photo_filename FROM assets WHERE name LIKE ?",
                          (TAG + "%",)).fetchall():
        p = os.path.join(m.UPLOAD_DIR, r["photo_filename"] or "")
        if r["photo_filename"] and os.path.exists(p):
            os.remove(p)
    conn.execute("DELETE FROM assets WHERE name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM vehicle_maintenance WHERE vehicle_id IN
                    (SELECT id FROM vehicles WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM manual_sections WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM contacts WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _row(table, column, value):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (value,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("The estate register")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("Adding something to the register")
    oc.post("/admin/assets/new", data={
        "name": TAG + " Commode", "category": "antique", "location": "Blue room",
        "description": "Walnut, three drawers", "purchase_price": "1200,50",
        "estimated_value": "4000", "value_source": "valuation",
        "serial_number": "N/A", "condition": "good",
    }, content_type="multipart/form-data", follow_redirects=True)
    a = _row("assets", "name", TAG + " Commode")
    s.check("it is recorded", a is not None)
    s.check("a comma decimal is read as money, not dropped",
            a and float(a["purchase_price"]) == 1200.50,
            detail=str(a["purchase_price"]) if a else "")
    s.check("and where it is, which is the point of a register",
            a and a["location"] == "Blue room")

    r = oc.post("/admin/assets/new", data={"name": "  "}, follow_redirects=True)
    s.check("one with no name is refused",
            any("name" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("a category the app does not know is rejected outright",
            oc.post("/admin/assets/new",
                    data={"name": TAG + " Bogus", "category": "spaceship"}
                    ).status_code == 400)
    s.check("and nothing was written for it", _row("assets", "name", TAG + " Bogus") is None)

    s.section("The photograph")
    oc.post("/admin/assets/new", data={
        "name": TAG + " Clock", "category": "antique",
        "photo": (BytesIO(b"\x89PNG\r\n\x1a\n fake"), "clock.png"),
    }, content_type="multipart/form-data", follow_redirects=True)
    clock = _row("assets", "name", TAG + " Clock")
    s.check("a photo is kept with the asset", clock and clock["photo_filename"],
            detail=str(clock["photo_filename"]) if clock else "")
    s.check("under a name the app chose",
            clock and clock["photo_filename"] != "clock.png")
    s.check("and it can be fetched back",
            oc.get(f"/admin/assets/{clock['id']}/photo").status_code == 200)

    # The important one. Serving by asset id rather than by filename is what
    # stops a session walking the uploads directory, which also holds
    # contracts, payslips and receipts.
    # The property, tested by behaviour rather than by URL shape. Asking for a
    # different file alongside the id must change nothing: the name comes from
    # the row. An earlier version of this check asserted that
    # /admin/assets/../../app.py/photo was refused, which Flask's <int:>
    # converter does on its own — it passed with the lookup replaced by
    # request.args and so proved nothing at all.
    served = oc.get(f"/admin/assets/{clock['id']}/photo?f=../app.py").get_data()
    s.check("naming another file alongside the id is ignored",
            served.startswith(bytes([0x89]) + b"PNG"),
            detail=f"got {served[:24]!r} — the filename must come from the row, "
                   "or a session can walk the uploads directory")
    import ast as _ast
    src = open(os.path.join(_harness.ROOT, "app.py"), encoding="utf-8").read()
    fn = next(n for n in _ast.walk(_ast.parse(src))
              if isinstance(n, _ast.FunctionDef) and n.name == "asset_photo")
    s.check("and the handler never reads the request at all",
            "request" not in {getattr(x, "id", None) for x in _ast.walk(fn)},
            detail="the only input this route may take is the asset id")
    s.check("an asset with no photo is a 404, not a blank file",
            oc.get(f"/admin/assets/{a['id']}/photo").status_code == 404)
    s.check("and one that does not exist too",
            oc.get("/admin/assets/99999999/photo").status_code == 404)

    # A bad photo must not cost the record.
    r = oc.post("/admin/assets/new", data={
        "name": TAG + " Tapestry", "category": "art",
        "photo": (BytesIO(b"not really"), "tapestry.heic"),
    }, content_type="multipart/form-data", follow_redirects=True)
    tap = _row("assets", "name", TAG + " Tapestry")
    s.check("an unrecognised photo does not lose the asset", tap is not None,
            detail="losing the record matters more than losing the picture")
    s.check("no photo is attached to it", tap and not tap["photo_filename"])
    s.check("and it says the photo was skipped",
            any("skipped" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("Updating one")
    oc.post(f"/admin/assets/{a['id']}/update", data={
        "name": TAG + " Commode", "category": "antique", "location": "Long gallery",
        "estimated_value": "5500", "value_source": "valuation",
        "valued_on": house_today().isoformat(), "condition": "good", "status": "held",
    }, content_type="multipart/form-data", follow_redirects=True)
    after = _row("assets", "id", a["id"])
    s.check("it moves room", after["location"] == "Long gallery",
            detail=str(after["location"]))
    s.check("and the revaluation sticks", float(after["estimated_value"]) == 5500.0,
            detail=str(after["estimated_value"]))

    s.section("Vehicles and when they are next due")
    oc.post("/management/vehicles/new", data={
        "name": TAG + " Van", "vehicle_type": "van", "fuel_type": "diesel",
        "license_plate": "AA-123-BB", "next_service_due": "2026-01-01",
    }, follow_redirects=True)
    v = _row("vehicles", "name", TAG + " Van")
    s.check("a vehicle is added", v is not None)
    s.check("with its plate", v and v["license_plate"] == "AA-123-BB")

    oc.post(f"/management/vehicles/{v['id']}/edit", data={
        "name": TAG + " Van", "vehicle_type": "van", "fuel_type": "diesel",
        "license_plate": "BB-456-CC", "cleanliness": "clean", "fuel_level": "half",
        "next_service_due": "2026-02-01", "notes": "wing mirror replaced",
    }, follow_redirects=True)
    edited = _row("vehicles", "id", v["id"])
    s.check("its plate can be corrected", edited["license_plate"] == "BB-456-CC",
            detail=str(edited["license_plate"]))
    s.check("and the service date with it", edited["next_service_due"] == "2026-02-01",
            detail=str(edited["next_service_due"]))

    oc.post(f"/management/vehicles/{v['id']}/log-service", follow_redirects=True)
    serviced = _row("vehicles", "id", v["id"])
    expected = m.add_months(m.house_today(), 6).isoformat()
    s.check("logging a service moves the next date six months on",
            serviced["next_service_due"] == expected,
            detail=f"{serviced['next_service_due']} (expected {expected})")
    conn = db()
    logged = conn.execute(
        """SELECT * FROM vehicle_maintenance WHERE vehicle_id = ?
           AND title = 'Service completed'""", (v["id"],)).fetchall()
    conn.close()
    s.check("and leaves a record that it happened", len(logged) == 1,
            detail=f"{len(logged)} row(s) — a moved date with no history "
                   "cannot be questioned later")
    s.check("marked resolved rather than left open",
            logged and logged[0]["status"] == "resolved")

    oc.post(f"/management/vehicles/{v['id']}/maintenance/new", data={
        "title": TAG + " Brake noise", "description": "grinding on the left front",
    }, follow_redirects=True)
    conn = db()
    faults = conn.execute(
        "SELECT * FROM vehicle_maintenance WHERE vehicle_id = ? AND title LIKE ?",
        (v["id"], TAG + "%")).fetchall()
    conn.close()
    s.check("a fault can be raised against it", len(faults) == 1, detail=str(len(faults)))
    s.check("and starts open, not resolved",
            faults and faults[0]["status"] != "resolved",
            detail=str(faults[0]["status"]) if faults else "")

    s.check("a vehicle that does not exist is a 404",
            oc.post("/management/vehicles/99999999/log-service").status_code == 404)

    s.section("The house manual")
    oc.post("/manual/new", data={"title": TAG + " The boiler"}, follow_redirects=True)
    sec = _row("manual_sections", "title", TAG + " The boiler")
    s.check("a section is added", sec is not None)
    oc.post("/manual/new", data={"title": TAG + " The well"}, follow_redirects=True)
    sec2 = _row("manual_sections", "title", TAG + " The well")
    s.check("a second one goes after the first, not on top of it",
            sec2 and sec2["sort_order"] > sec["sort_order"],
            detail=f"{sec['sort_order']} then {sec2['sort_order'] if sec2 else None}")

    oc.post(f"/manual/{sec['id']}/edit",
            data={"title": TAG + " The boiler", "body": "Pilot light is behind the panel."},
            follow_redirects=True)
    s.check("it can be written",
            "Pilot light" in (_row("manual_sections", "id", sec["id"])["body"] or ""))
    s.check("an employee can read the manual",
            ec.get("/manual").status_code == 200,
            detail="it is what somebody opens when they do not know how "
                   "the boiler works")
    s.check("but cannot rewrite it",
            ec.post(f"/manual/{sec['id']}/edit",
                    data={"title": "x", "body": "y"}).status_code in (302, 403))
    s.check("and the text is untouched by the attempt",
            "Pilot light" in (_row("manual_sections", "id", sec["id"])["body"] or ""))

    s.section("Contacts")
    conn = db()
    conn.execute(
        """INSERT INTO contacts (name, role, phone, sort_order, created_at)
           VALUES (?, 'Plumber', '+33 5 61 11 11 11', 0, ?)""",
        (TAG + " Plombier", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    ct = _row("contacts", "name", TAG + " Plombier")
    oc.post(f"/contacts/{ct['id']}/edit", data={
        "name": TAG + " Plombier", "role": "Plumber",
        "phone": "+33 5 61 22 22 22", "notes": "out of hours too",
    }, follow_redirects=True)
    s.check("a number can be corrected",
            _row("contacts", "id", ct["id"])["phone"] == "+33 5 61 22 22 22",
            detail=str(_row("contacts", "id", ct["id"])["phone"]))

    s.section("None of it is the employees' to change")
    guards = [
        ("add an asset", ec.post("/admin/assets/new", data={"name": TAG + " Rogue"},
                                 content_type="multipart/form-data")),
        ("add a vehicle", ec.post("/management/vehicles/new", data={"name": TAG + " Rogue van"})),
        ("log a service", ec.post(f"/management/vehicles/{v['id']}/log-service")),
        ("add a manual section", ec.post("/manual/new", data={"title": TAG + " Rogue page"})),
        ("edit a contact", ec.post(f"/contacts/{ct['id']}/edit", data={"name": "Rogue"})),
    ]
    for label, r in guards:
        s.check(f"an employee cannot {label}", r.status_code in (302, 403),
                detail=str(r.status_code))
    # ...and the money check behind the status codes, because these routes
    # redirect on success too.
    s.check("and nothing of theirs landed",
            not (_row("assets", "name", TAG + " Rogue")
                 or _row("vehicles", "name", TAG + " Rogue van")
                 or _row("manual_sections", "title", TAG + " Rogue page")),
            detail="an employee wrote to the register")
    s.check("the contact still has its real name",
            _row("contacts", "id", ct["id"])["name"] == TAG + " Plombier")
    s.check("somebody with no account cannot read the asset register",
            anon.get("/admin/assets").status_code in (302, 401, 403))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
