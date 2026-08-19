"""Certifications, absences and equipment — the records that have to be right.

A certification is not a diary note. In French hospitality some of these are
legally required to be current for anyone near the kitchen, so an expiry that
passes unnoticed is somebody working a shift they are not permitted to work.
The interesting behaviour is not "was the row saved" but "does the château get
told before the date, and does it still get told when a rota is already booked
on the far side of it".

Absences carry a return-to-work step, which is the part people skip. Equipment
is the offboarding end: a key or a phone marked issued and never returned is
the thing nobody remembers three months later.

None of these routes had a test.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZHRR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM certifications WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM absences WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM equipment_items WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("HR records")
    oc, ec, owner, emp = clients()
    if not emp:
        s.check("an employee exists", False, detail="none in the database")
        return s
    _cleanup()
    today = m.datetime.now(m.timezone.utc).date()

    s.section("Recording a certification")
    expires = today + timedelta(days=10)
    oc.post("/admin/hr/certifications/new", data={
        "user_id": str(emp["id"]), "name": f"{TAG} Food hygiene",
        "issuer": "Préfecture", "expiry_date": expires.isoformat(), "required": "on",
    }, follow_redirects=True)
    conn = db()
    cert = conn.execute("SELECT * FROM certifications WHERE name = ?",
                        (f"{TAG} Food hygiene",)).fetchone()
    conn.close()
    s.check("it is saved", cert is not None)
    s.check("and marked as one they must hold", cert and cert["required"] == 1,
            detail=f"required={cert['required'] if cert else None}")

    # A certification with no employee, or no name, is a row nobody can act on.
    before = _count("certifications", "name LIKE ?", (TAG + "%",))
    oc.post("/admin/hr/certifications/new", data={"user_id": "", "name": f"{TAG} Nobody"},
            follow_redirects=True)
    oc.post("/admin/hr/certifications/new", data={"user_id": str(emp["id"]), "name": "  "},
            follow_redirects=True)
    s.check("one with no employee or no name is refused",
            _count("certifications", "name LIKE ?", (TAG + "%",)) == before,
            detail="a useless row was created")

    s.section("The château is warned before the date, not after")
    conn = db()
    expiring = m.expiring_certifications(conn, today)
    conn.close()
    mine = [e for e in expiring if e["name"] == f"{TAG} Food hygiene"]
    s.check("a certification expiring in ten days is surfaced", len(mine) == 1,
            detail=f"got {len(mine)}")
    s.check("with how long is left", mine and mine[0]["days_left"] == 10,
            detail=f"got {mine[0]['days_left'] if mine else None}")

    s.section("A certificate that runs out under a booked rota")
    # An expiry on its own is a diary note. An expiry with shifts already
    # booked past it is a rota that will not be legal, and nobody finds out
    # until the day.
    conn = db()
    for offset in (12, 15):
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at)
               VALUES (?, ?, '09:00', '17:00', ?, ?)""",
            (emp["id"], (today + timedelta(days=offset)).isoformat(),
             f"{TAG} cover", _harness.datetime_now()))
    conn.commit()
    expiring = m.expiring_certifications(conn, today)
    conn.close()
    mine = [e for e in expiring if e["name"] == f"{TAG} Food hygiene"]
    s.check("the shifts booked after it expires are counted",
            mine and mine[0].get("shifts_after", 0) == 2,
            detail=f"got {mine[0].get('shifts_after') if mine else None}")

    s.section("An expired one is worse than an expiring one")
    conn = db()
    conn.execute("UPDATE certifications SET expiry_date = ? WHERE name = ?",
                 ((today - timedelta(days=3)).isoformat(), f"{TAG} Food hygiene"))
    conn.commit()
    conn.close()
    s.check("expiry_status calls it expired",
            m.expiry_status((today - timedelta(days=3)).isoformat()) == "expired")
    s.check("and one due next week 'soon'",
            m.expiry_status((today + timedelta(days=7)).isoformat()) == "soon")
    s.check("while one a year out is neither",
            m.expiry_status((today + timedelta(days=365)).isoformat()) is None)

    s.section("Someone who has left stops being chased")
    conn = db()
    conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (emp["id"],))
    conn.commit()
    quiet = m.expiring_certifications(conn, today)
    conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (emp["id"],))
    conn.commit()
    conn.close()
    s.check("an inactive employee's certificate is not reported",
            not [e for e in quiet if e["name"] == f"{TAG} Food hygiene"],
            detail="still chasing somebody who has left")

    s.section("Recording an absence")
    start = today - timedelta(days=2)
    oc.post("/admin/hr/absences/new", data={
        "user_id": str(emp["id"]), "start_date": start.isoformat(),
        "end_date": today.isoformat(), "kind": "sick",
        "reason": f"{TAG} flu", "self_certified": "on",
    }, follow_redirects=True)
    conn = db()
    absence = conn.execute("SELECT * FROM absences WHERE reason = ?", (f"{TAG} flu",)).fetchone()
    conn.close()
    s.check("it is saved", absence is not None)
    s.check("as the kind chosen", absence and absence["kind"] == "sick",
            detail=f"got {absence['kind'] if absence else None}")
    s.check("and self-certified is recorded", absence and absence["self_certified"] == 1)
    s.check("with who recorded it, since it is somebody's employment record",
            absence and absence["recorded_by_user_id"] == owner["id"],
            detail=f"got {absence['recorded_by_user_id'] if absence else None}")

    s.section("An absence that ends before it starts is refused")
    before = _count("absences", "reason LIKE ?", (TAG + "%",))
    oc.post("/admin/hr/absences/new", data={
        "user_id": str(emp["id"]), "start_date": today.isoformat(),
        "end_date": (today - timedelta(days=5)).isoformat(),
        "kind": "sick", "reason": f"{TAG} backwards",
    }, follow_redirects=True)
    s.check("the impossible range is rejected",
            _count("absences", "reason LIKE ?", (TAG + "%",)) == before,
            detail="an absence ending before it began was saved")
    # A kind nobody defined must not end up in the record.
    oc.post("/admin/hr/absences/new", data={
        "user_id": str(emp["id"]), "start_date": today.isoformat(),
        "kind": "holiday-ish", "reason": f"{TAG} odd kind",
    }, follow_redirects=True)
    conn = db()
    odd = conn.execute("SELECT kind FROM absences WHERE reason = ?", (f"{TAG} odd kind",)).fetchone()
    conn.close()
    s.check("an unknown kind is filed as 'other' rather than stored as typed",
            odd and odd["kind"] == "other", detail=f"got {odd['kind'] if odd else None}")

    s.section("Return to work — the step people skip")
    s.check("it starts unrecorded", absence and not absence["return_to_work_done_at"])
    oc.post(f"/admin/hr/absences/{absence['id']}/return-to-work",
            data={"return_to_work_note": f"{TAG} fit to work, no adjustments"},
            follow_redirects=True)
    conn = db()
    after = conn.execute("SELECT * FROM absences WHERE id = ?", (absence["id"],)).fetchone()
    conn.close()
    s.check("the conversation is recorded", bool(after["return_to_work_done_at"]))
    s.check("along with what was said", TAG in (after["return_to_work_note"] or ""),
            detail=f"got {after['return_to_work_note']!r}")

    s.section("Equipment out, and equipment back")
    conn = db()
    cur = conn.execute(
        """INSERT INTO equipment_items (user_id, label, notes, issued_at)
           VALUES (?, ?, NULL, ?)""",
        (emp["id"], f"{TAG} Front door key", _harness.datetime_now()))
    item_id = cur.lastrowid
    conn.commit()
    conn.close()
    s.check("it starts as not returned",
            _one("equipment_items", "id = ?", (item_id,))["returned_at"] is None)
    oc.post(f"/directory/{emp['id']}/equipment/{item_id}/toggle-returned", follow_redirects=True)
    s.check("marking it back records when",
            _one("equipment_items", "id = ?", (item_id,))["returned_at"] is not None)
    # Toggling back matters: a key marked returned by mistake has to be
    # correctable, or the offboarding list lies.
    oc.post(f"/directory/{emp['id']}/equipment/{item_id}/toggle-returned", follow_redirects=True)
    s.check("and it can be un-marked again",
            _one("equipment_items", "id = ?", (item_id,))["returned_at"] is None)

    s.section("Only the owner keeps these records")
    r = ec.post("/admin/hr/certifications/new", data={
        "user_id": str(emp["id"]), "name": f"{TAG} Self-awarded"})
    s.check("an employee cannot add their own certification",
            r.status_code in (302, 403, 404)
            and not _one("certifications", "name = ?", (f"{TAG} Self-awarded",)),
            detail=f"HTTP {r.status_code}")
    r = ec.post(f"/directory/{emp['id']}/equipment/{item_id}/toggle-returned")
    s.check("nor mark their own key returned", r.status_code in (302, 403, 404),
            detail=f"HTTP {r.status_code}")

    _cleanup()
    return s


def _count(table, where, args):
    conn = db()
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", args).fetchone()["c"]
    finally:
        conn.close()


def _one(table, where, args):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE {where}", args).fetchone()
    finally:
        conn.close()
