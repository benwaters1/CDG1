"""Arriving: the checklist, and the kit that has to come back one day.

The other half of the loop tested in test_offboarding. Somebody starts, gets a
checklist and a set of things — a laptop, a uniform, a van fob — and the
register of what they hold is what the leaving checklist is later built from.

So the interesting check is not that the forms work. It is that the two ends
join up: issue a laptop on the day somebody starts, mark them inactive months
later, and the line asking for that laptop back appears by itself. If the
register is the only place the laptop is written down, the loop is only as good
as the join.

Marking kit returned is a toggle rather than a one-way door, because somebody
will tick the wrong row, and a laptop wrongly recorded as returned is worse
than one wrongly outstanding: the first is invisible, the second is a question
somebody asks.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZKIT"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE name LIKE ?", (TAG + "%",)).fetchall()]
    for uid in ids:
        for t in ("equipment_items", "onboarding_items", "offboarding_items"):
            conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _employee(name):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, role, status, job_role, password_hash, created_at)
           VALUES (?, ?, 'employee', 'active', 'Housekeeping', 'x', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _rows(table, user_id):
    conn = db()
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    finally:
        conn.close()


def _as(user_id):
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def run():
    s = Suite("Onboarding and kit")
    _cleanup()
    oc, ec, owner, emp = clients()
    person = _employee("Théo")

    s.section("The onboarding checklist")
    oc.post(f"/directory/{person['id']}/onboarding/new",
            data={"label": f"{TAG} show them the boiler"}, follow_redirects=True)
    items = _rows("onboarding_items", person["id"])
    s.check("a line can be added", len(items) == 1, detail=f"{len(items)} item(s)")
    if not items:
        _cleanup()
        return s
    first = items[0]
    s.check("it starts unticked, because it is a thing to do", not first["done"])

    oc.post(f"/directory/{person['id']}/onboarding/new", data={"label": "   "},
            follow_redirects=True)
    s.check("an empty line is not added", len(_rows("onboarding_items", person["id"])) == 1)

    oc.post(f"/directory/{person['id']}/onboarding/{first['id']}/toggle",
            follow_redirects=True)
    s.check("ticking it sticks",
            _rows("onboarding_items", person["id"])[0]["done"] == 1)
    oc.post(f"/directory/{person['id']}/onboarding/{first['id']}/toggle",
            follow_redirects=True)
    s.check("and it can be un-ticked, because people mistake rows",
            _rows("onboarding_items", person["id"])[0]["done"] == 0)

    s.section("One person's checklist is not reachable through another's URL")
    stranger = _employee("Manon")
    cross = oc.post(f"/directory/{stranger['id']}/onboarding/{first['id']}/toggle")
    s.check("toggling it under somebody else's id is a 404", cross.status_code == 404,
            detail=f"HTTP {cross.status_code}")
    s.check("and the item is untouched",
            _rows("onboarding_items", person["id"])[0]["done"] == 0)

    s.section("Issuing kit")
    oc.post(f"/directory/{person['id']}/equipment/new",
            data={"label": f"{TAG} van fob", "notes": "the one with the red tag"},
            follow_redirects=True)
    kit = _rows("equipment_items", person["id"])
    s.check("it is recorded against them", len(kit) == 1, detail=f"{len(kit)} item(s)")
    if not kit:
        _cleanup()
        return s
    fob = kit[0]
    s.check("with the note that says which one", (fob["notes"] or "") != "")
    s.check("and it counts as outstanding until it comes back",
            fob["returned_at"] is None)
    oc.post(f"/directory/{person['id']}/equipment/new", data={"label": "  ", "notes": ""},
            follow_redirects=True)
    s.check("kit with no label is not recorded",
            len(_rows("equipment_items", person["id"])) == 1)

    s.section("It shows up on the outstanding list")
    over = oc.get("/equipment")
    s.check("the overview loads", over.status_code == 200, over)
    s.check("and names the fob", "van fob" in over.get_data(as_text=True))

    s.section("Marking it back, and correcting that")
    oc.post(f"/directory/{person['id']}/equipment/{fob['id']}/toggle-returned",
            follow_redirects=True)
    s.check("it is recorded as returned",
            _rows("equipment_items", person["id"])[0]["returned_at"] is not None)
    s.check("and drops off the outstanding list",
            "van fob" not in oc.get("/equipment").get_data(as_text=True),
            detail="something already back is still being chased")
    oc.post(f"/directory/{person['id']}/equipment/{fob['id']}/toggle-returned",
            follow_redirects=True)
    s.check("a wrong tick can be undone — invisible is worse than outstanding",
            _rows("equipment_items", person["id"])[0]["returned_at"] is None)

    s.section("The two ends join up")
    # The point of the register. Issue a laptop on somebody's first day, and
    # months later the line asking for it back should write itself.
    leaver = _employee("Corinne")
    oc.post(f"/directory/{leaver['id']}/equipment/new",
            data={"label": f"{TAG} work laptop", "notes": "silver, chipped lid"},
            follow_redirects=True)
    oc.post(f"/directory/{leaver['id']}/equipment/new",
            data={"label": f"{TAG} spare uniform", "notes": ""}, follow_redirects=True)
    handed_back = _rows("equipment_items", leaver["id"])[1]
    oc.post(f"/directory/{leaver['id']}/equipment/{handed_back['id']}/toggle-returned",
            follow_redirects=True)
    oc.post(f"/directory/{leaver['id']}/toggle-status", follow_redirects=True)
    leaving = [i["label"] for i in _rows("offboarding_items", leaver["id"])]
    s.check("the laptop they still hold is asked for by name",
            any("work laptop" in l for l in leaving), detail=f"{leaving}")
    s.check("with the detail that identifies it",
            any("chipped lid" in l for l in leaving),
            detail="the register says which laptop, the checklist does not")
    s.check("the uniform already returned is not asked for",
            not any("spare uniform" in l for l in leaving),
            detail=f"chasing something already back: {leaving}")

    s.section("Deleting a record of kit")
    oc.post(f"/directory/{person['id']}/equipment/{fob['id']}/delete", follow_redirects=True)
    s.check("it is removed", not _rows("equipment_items", person["id"]))

    s.section("Guards")
    s.check("an employee cannot issue kit to themselves",
            ec.post(f"/directory/{emp['id']}/equipment/new",
                    data={"label": "a van"}).status_code in (302, 403))
    s.check("nor mark somebody's kit returned",
            _as(person["id"]).post(
                f"/directory/{leaver['id']}/equipment/1/toggle-returned"
            ).status_code in (302, 403, 404))
    s.check("nor add a line to an onboarding checklist",
            ec.post(f"/directory/{person['id']}/onboarding/new",
                    data={"label": "x"}).status_code in (302, 403))
    s.check("an employee cannot open the equipment overview",
            ec.get("/equipment").status_code in (302, 403))

    _cleanup()
    return s
