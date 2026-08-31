"""Whether a guest can legally be put in the car.

The vehicles model tracked how clean each one was and how much fuel was in
it, and not whether it was legal to drive. In France a car over four years
old needs a contrôle technique every two years, and the app had ZERO mentions
of it in thirty-four thousand lines.

The fine is not the point. An insurer can decline a claim on a vehicle with
no valid CT, so a car driving guests to the market with an expired one is a
car being driven uninsured — and the house would find that out at the worst
possible moment.

Insurance is not duplicated for this. insurance_policies has carried an
expiry_date and a vehicle_id since it was built; nothing had ever read the
two together, so a policy could lapse on a car still in daily use and the
only trace was a row in a table nobody opens.

WHAT IS DELIBERATELY NOT ENFORCED. Nothing stops the car being used. The app
does not know who is driving or why, and a house that cannot take a guest to
the airport because a form is out of date is a worse app than one that says
so loudly. It says so loudly.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTVEH"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM insurance_policies WHERE vehicle_id IN
                    (SELECT id FROM vehicles WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _vehicle(name, ct=None, off_road=0):
    conn = db()
    conn.execute(
        """INSERT INTO vehicles (name, vehicle_type, license_plate, ct_expires_on,
           off_road, created_at) VALUES (?, 'Car', 'AA-000-AA', ?, ?, ?)""",
        (TAG + " " + name, ct, off_road, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM vehicles WHERE name = ?",
                       (TAG + " " + name,)).fetchone()
    conn.close()
    return row


def _insure(vehicle_id, expires):
    conn = db()
    conn.execute(
        """INSERT INTO insurance_policies (provider, policy_number, coverage_type,
           premium, premium_frequency, expiry_date, vehicle_id, created_at)
           VALUES (?, 'P-1', 'vehicle', 400, 'annual', ?, ?, ?)""",
        (TAG + " Assureur", expires, vehicle_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _papers(today=None):
    conn = db()
    try:
        with m.app.test_request_context():
            return {p["vehicle"]["name"]: p
                    for p in m.vehicle_papers(conn, today or datetime.now(m.LOCAL_TZ).date())}
    finally:
        conn.close()


def run():
    s = Suite("Is the car legal")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    s.section("A car with both papers in date")
    good = _vehicle("Berline", ct=(today + timedelta(days=400)).isoformat())
    _insure(good["id"], (today + timedelta(days=200)).isoformat())
    p = _papers()[TAG + " Berline"]
    s.check("it reads as fine", p["state"] == "ok", detail=str(p["state"]))
    s.check("and nobody is chased about it", not p["chase"])

    s.section("A car whose contrôle technique has run out")
    expired = _vehicle("Break", ct=(today - timedelta(days=10)).isoformat())
    _insure(expired["id"], (today + timedelta(days=200)).isoformat())
    p = _papers()[TAG + " Break"]
    s.check("it reads as expired", p["state"] == "expired", detail=str(p["state"]))
    s.check("naming the contrôle technique rather than the insurance",
            "technique" in (p["worst_what"] or ""), detail=str(p["worst_what"]))
    s.check("and it is chased", p["chase"])

    s.section("A car whose insurance has lapsed")
    lapsed = _vehicle("Camionnette", ct=(today + timedelta(days=300)).isoformat())
    _insure(lapsed["id"], (today - timedelta(days=3)).isoformat())
    p = _papers()[TAG + " Camionnette"]
    s.check("it reads as expired too", p["state"] == "expired")
    s.check("naming the insurance", "insurance" in (p["worst_what"] or ""),
            detail=str(p["worst_what"]))

    s.section("Expired outranks expiring")
    # "Expires in three weeks" printed above "expired last month" is how the
    # urgent one gets skimmed past.
    both = _vehicle("Utilitaire", ct=(today - timedelta(days=1)).isoformat())
    _insure(both["id"], (today + timedelta(days=5)).isoformat())
    p = _papers()[TAG + " Utilitaire"]
    s.check("the expired one is what is reported", p["state"] == "expired",
            detail=str(p["state"]))
    s.check("and both are still in the list underneath",
            len([x for x in p["problems"] if x[0] in ("expired", "due")]) == 2,
            detail=str(p["problems"]))

    s.section("A car with no dates recorded at all")
    blank = _vehicle("Inconnue")
    p = _papers()[TAG + " Inconnue"]
    s.check("it is not reported as fine", p["state"] != "ok", detail=str(p["state"]))
    s.check("it says the papers are not on file", p["state"] == "unknown")
    # Not chased. An unrecorded date is somebody not having typed it in, which
    # is a different job from a date that has passed, and mixing the two makes
    # the list of expired cars unreadable.
    s.check("but nobody is chased for a date that was never entered",
            not p["chase"],
            detail="a missing date and a passed date are different jobs")

    s.section("A car deliberately off the road")
    parked = _vehicle("Tracteur", ct=(today - timedelta(days=200)).isoformat(), off_road=1)
    p = _papers()[TAG + " Tracteur"]
    s.check("it is still reported as expired", p["state"] == "expired")
    s.check("but not chased", not p["chase"],
            detail="a car up on blocks with no CT is somebody's decision, not "
                   "an oversight")

    s.section("It becomes a task that closes itself")
    conn = db()
    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn, today)
    conn.close()
    ours = [f for f in found if f[0] == "vehicle" and TAG in f[1]]
    s.check("the expired cars are findings", len(ours) >= 2,
            detail=f"{len(ours)}: {[f[1] for f in ours][:2]}")
    s.check("the title names the vehicle and the document",
            ours and TAG + " Break" in " ".join(f[1] for f in ours)
            and "technique" in " ".join(f[1] for f in ours),
            detail=str([f[1] for f in ours])[:120])
    s.check("and no date is in the title, which is the dedupe key",
            all(str(today.year) not in f[1] for f in ours),
            detail="a countdown in the title raises a fresh task every morning")
    s.check("the note explains why it is not just a fine",
            any("uninsured" in f[2] for f in ours),
            detail=str([f[2][:60] for f in ours])[:130])
    s.check("the off-road one is not among them",
            not any("Tracteur" in f[1] for f in ours),
            detail=str([f[1] for f in ours])[:120])

    conn = db()
    with m.app.test_request_context():
        m.generate_watch_tasks(conn, today)
    conn.commit()
    made = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE origin = ? AND title LIKE ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, TAG + "%")).fetchone()["c"]
    conn.close()
    s.check("tasks are raised", made >= 2, detail=str(made))

    # Fix the car; the task should go without anybody closing it.
    conn = db()
    conn.execute("UPDATE vehicles SET ct_expires_on = ? WHERE id = ?",
                 ((today + timedelta(days=400)).isoformat(), expired["id"]))
    conn.commit()
    with m.app.test_request_context():
        m.generate_watch_tasks(conn, today)
    conn.commit()
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE origin = ? AND title LIKE ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, TAG + " Break%")).fetchone()["c"]
    conn.close()
    s.check("and booking the test closes its own task", left == 0,
            detail=f"{left} still open — nothing in this set has a done action "
                   "of its own, so the run has to tick it off")

    s.section("An expired one is on the owner's home page")
    conn = db()
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    conn.close()
    veh = [w for w in warnings if "not legal to drive" in w["title"]]
    s.check("it is there", bool(veh), detail=str([w["title"] for w in warnings])[:120])
    s.check("as a blocker rather than a note",
            veh and veh[0]["severity"] == "blocker", detail=str(veh[0]["severity"]) if veh else "")
    s.check("and it says why it matters",
            veh and "uninsured" in veh[0]["detail"],
            detail=str(veh[0]["detail"])[:110] if veh else "")

    s.section("Entering the dates")
    r = oc.post(f"/management/vehicles/{good['id']}/edit",
                data={"name": good["name"], "ct_expires_on": (today + timedelta(days=500)).isoformat(),
                      "odometer_km": "142000"}, follow_redirects=True)
    after = _one("SELECT * FROM vehicles WHERE id = ?", (good["id"],))
    s.check("the contrôle technique date is saved",
            after["ct_expires_on"] == (today + timedelta(days=500)).isoformat(),
            detail=str(after["ct_expires_on"]))
    s.check("and the odometer", after["odometer_km"] == 142000)
    s.check("stamped with when it was read", bool(after["odometer_read_at"]))

    was_read = after["odometer_read_at"]
    oc.post(f"/management/vehicles/{good['id']}/edit",
            data={"name": good["name"], "ct_expires_on": after["ct_expires_on"],
                  "odometer_km": "142000", "notes": "changed something else"},
            follow_redirects=True)
    s.check("an edit that does not touch the odometer does not claim it was read",
            _one("SELECT odometer_read_at FROM vehicles WHERE id = ?",
                 (good["id"],))["odometer_read_at"] == was_read,
            detail="otherwise every edit says somebody walked out and looked")

    r = oc.post(f"/management/vehicles/{good['id']}/edit",
                data={"name": good["name"], "ct_expires_on": "next spring"},
                follow_redirects=True)
    s.check("a date that is not a date is refused",
            any("could not be read" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the old one survives",
            _one("SELECT ct_expires_on FROM vehicles WHERE id = ?",
                 (good["id"],))["ct_expires_on"] == (today + timedelta(days=500)).isoformat(),
            detail="storing junk would make the car look settled while nothing "
                   "had been checked")

    s.section("Who may change it")
    r = ec.post(f"/management/vehicles/{good['id']}/edit",
                data={"name": good["name"], "ct_expires_on": (today + timedelta(days=9)).isoformat()},
                follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and the date did not move",
            _one("SELECT ct_expires_on FROM vehicles WHERE id = ?",
                 (good["id"],))["ct_expires_on"] == (today + timedelta(days=500)).isoformat())

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
