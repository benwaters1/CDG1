"""The fleet: who has a car, when it comes back, and the airport run.

A guest landing at Toulouse and finding nobody there is the failure this
guards against. The rule that matters is not "is the car free" but "is it
needed" — a car standing on the drive can still be spoken for, because a
transfer is booked against it in an hour.

That check has a deliberate exception: the driver assigned to the transfer can
of course take the car, since that is the whole point of the assignment. Both
halves are pinned here, because the exception is what makes the rule usable
and dropping it would block the very person meant to drive.

Servicing is the other half — a next-service date that has passed is a car
that should not be on the road, and it has to be visible before somebody takes
it rather than after.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZVEH"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM vehicle_usage WHERE vehicle_id IN "
                 "(SELECT id FROM vehicles WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM vehicle_transfers WHERE vehicle_id IN "
                 "(SELECT id FROM vehicles WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _vehicle(name="Estate Car", service_due=None):
    conn = db()
    cur = conn.execute(
        """INSERT INTO vehicles (name, vehicle_type, fuel_type, license_plate,
           cleanliness, fuel_level, next_service_due, created_at)
           VALUES (?, 'car', 'diesel', ?, 'clean', 'ok', ?, ?)""",
        (f"{TAG} {name}", f"{TAG}-01", service_due, _harness.datetime_now()))
    vid = cur.lastrowid
    conn.commit()
    conn.close()
    return vid


def _transfer(vehicle_id, when, driver_user_id=None, guest="Arriving Guest"):
    conn = db()
    cur = conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction,
           scheduled_at, driver_user_id, created_at)
           VALUES (?, ?, 'pickup', ?, ?, ?)""",
        (vehicle_id, f"{TAG} {guest}", when.isoformat(), driver_user_id,
         _harness.datetime_now()))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


def _out(vehicle_id):
    """Whoever currently has it, or None."""
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM vehicle_usage WHERE vehicle_id = ? AND checked_in_at IS NULL",
            (vehicle_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Vehicles")
    oc, ec, owner, emp = clients()
    if not emp:
        s.check("an employee exists to drive", False, detail="none in the database")
        return s
    _cleanup()
    now = datetime.now(timezone.utc)

    s.section("Taking a car out and bringing it back")
    vid = _vehicle()
    oc.post(f"/management/vehicles/{vid}/checkout",
            data={"user_id": str(emp["id"]), "purpose": f"{TAG} market run"},
            follow_redirects=True)
    row = _out(vid)
    s.check("it is recorded as out", row is not None)
    s.check("against the person who took it", row and row["user_id"] == emp["id"],
            detail=f"got user {row['user_id'] if row else None}")
    s.check("with what it is for, so the board reads usefully",
            row and TAG in (row["purpose"] or ""), detail=f"got {row['purpose'] if row else None}")

    oc.post(f"/management/vehicles/{vid}/checkin", follow_redirects=True)
    s.check("checking in clears it", _out(vid) is None)
    conn = db()
    history = conn.execute(
        "SELECT COUNT(*) AS c FROM vehicle_usage WHERE vehicle_id = ?", (vid,)).fetchone()["c"]
    conn.close()
    s.check("and the trip stays on the record rather than being deleted",
            history == 1, detail=f"got {history} rows")

    s.section("Two people cannot have the same car")
    oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(emp["id"])},
            follow_redirects=True)
    r = oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(owner["id"])},
                follow_redirects=True)
    row = _out(vid)
    s.check("the second person is refused", row and row["user_id"] == emp["id"],
            detail=f"the car is now with user {row['user_id'] if row else None}")
    s.check("and told why",
            any("already checked out" in f.lower() for f in _harness.flashes(r)),
            detail=f"flashes: {_harness.flashes(r)}")
    oc.post(f"/management/vehicles/{vid}/checkin", follow_redirects=True)

    s.section("Somebody must be named")
    r = oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": ""},
                follow_redirects=True)
    s.check("a checkout with nobody attached is refused", _out(vid) is None,
            detail="the car went out to nobody")

    s.section("A car needed for an airport run is not free to take")
    # The failure this prevents: a guest lands at Toulouse and the car that was
    # meant to collect them is halfway to the market.
    _cleanup()
    vid = _vehicle()
    _transfer(vid, now + timedelta(hours=1))
    r = oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(emp["id"])},
                follow_redirects=True)
    s.check("taking it is refused while a transfer is imminent", _out(vid) is None,
            detail="the car left with a pickup booked")
    s.check("and the refusal says when and for whom",
            any("transfer" in f.lower() or "pickup" in f.lower()
                for f in _harness.flashes(r)),
            detail=f"flashes: {_harness.flashes(r)}")

    s.section("But the assigned driver can take it — that is the point")
    # Without this exception the rule blocks the very person meant to drive.
    _cleanup()
    vid = _vehicle()
    _transfer(vid, now + timedelta(hours=1), driver_user_id=emp["id"])
    oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(emp["id"])},
            follow_redirects=True)
    row = _out(vid)
    s.check("the driver on the transfer is let through", row is not None,
            detail="the assigned driver was blocked from their own run")
    # ...and somebody else still is not.
    oc.post(f"/management/vehicles/{vid}/checkin", follow_redirects=True)
    r = oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(owner["id"])},
                follow_redirects=True)
    s.check("while anybody else is still refused", _out(vid) is None,
            detail="someone other than the driver took the transfer car")

    s.section("A transfer far away does not block anything")
    _cleanup()
    vid = _vehicle()
    _transfer(vid, now + timedelta(days=3))
    oc.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(emp["id"])},
            follow_redirects=True)
    s.check("a run in three days leaves the car usable today", _out(vid) is not None,
            detail="a distant transfer blocked an ordinary trip")
    oc.post(f"/management/vehicles/{vid}/checkin", follow_redirects=True)

    s.section("Servicing is visible before somebody drives it")
    _cleanup()
    today = m.datetime.now(m.timezone.utc).date()
    overdue = _vehicle("Overdue Van", service_due=(today - timedelta(days=10)).isoformat())
    soon = _vehicle("Due Soon", service_due=(today + timedelta(days=7)).isoformat())
    fine = _vehicle("Fine", service_due=(today + timedelta(days=300)).isoformat())
    s.check("a service date that has passed reads as expired",
            m.expiry_status((today - timedelta(days=10)).isoformat()) == "expired")
    s.check("one due next week reads as soon",
            m.expiry_status((today + timedelta(days=7)).isoformat()) == "soon")
    s.check("and one months away reads as neither",
            m.expiry_status((today + timedelta(days=300)).isoformat()) is None)
    page = oc.get("/management/vehicles").get_data(as_text=True)
    s.check("the fleet page lists them", f"{TAG} Overdue Van" in page and f"{TAG} Fine" in page)

    s.section("The transfers page answers 'who is driving today'")
    _cleanup()
    vid = _vehicle()
    _transfer(vid, now + timedelta(hours=2), driver_user_id=emp["id"])
    page = ec.get("/transfers").get_data(as_text=True)
    s.check("an employee can see the run they are on",
            f"{TAG} Arriving Guest" in page,
            detail="a driver cannot see their own airport run")

    s.section("Only the owner moves the fleet about")
    _cleanup()
    vid = _vehicle()
    r = ec.post(f"/management/vehicles/{vid}/checkout", data={"user_id": str(emp["id"])})
    s.check("an employee cannot check a vehicle out",
            _out(vid) is None and r.status_code in (302, 403, 404),
            detail=f"HTTP {r.status_code}")

    _cleanup()
    return s
