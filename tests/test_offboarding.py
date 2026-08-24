"""Somebody leaves: the access they lose, and the keys that come back.

Marking an employee inactive is the whole of "revoke access" in this app, so
two things hang off one checkbox.

The first is the session already in their pocket. Checking status only at the
login form left a phone that was signed in yesterday still working today, for
somebody who had left or been asked to. current_user() refuses an inactive
account on every request instead, which is the behaviour worth pinning: it is
one `if`, it sits in the hottest function in the app, and anybody caching it
for speed would break revocation without breaking a single other test.

The second is the checklist. A generic "collect keys" line does not say WHICH
keys, so the one that never comes back is the one nobody remembered was issued
— the seed reads the access register and writes a line per item actually held,
and a code gets different wording because knowledge cannot be handed back.

Seasonal staff exposed a gap. Leave in September, come back in June, leave
again: the checklist was seeded once and only once, so the second departure
inherited the first one's ticks and read as already done. Nobody chases keys
that a screen says are already back.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZOFF"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE name LIKE ?", (TAG + "%",)).fetchall()]
    for uid in ids:
        conn.execute("DELETE FROM offboarding_items WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM onboarding_items WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM access_holdings WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM access_items WHERE label LIKE ?", (TAG + "%",))
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


def _issue(user_id, label, kind, location=None):
    """Put something from the access register in somebody's hands."""
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO access_items (label, kind, location, active, created_at)
           VALUES (?, ?, ?, 1, ?)""", (f"{TAG} {label}", kind, location, now))
    item_id = conn.execute(
        "SELECT id FROM access_items WHERE label = ?", (f"{TAG} {label}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO access_holdings (access_item_id, user_id, issued_at)
           VALUES (?, ?, ?)""", (item_id, user_id, now))
    conn.commit()
    conn.close()
    return item_id


def _items(user_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM offboarding_items WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    finally:
        conn.close()


def _as(user_id):
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def _status(user_id):
    conn = db()
    try:
        return conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()["status"]
    finally:
        conn.close()


def run():
    s = Suite("Offboarding")
    _cleanup()
    oc, ec, owner, emp = clients()

    person = _employee("Margaux")
    theirs = _as(person["id"])

    s.section("While they are still here, their session works")
    # /today rather than /, because / is the public front door and answers 200
    # to anybody. A staff-only page is the only thing that proves a session.
    s.check("they can open their own day", theirs.get("/today").status_code == 200,
            detail="an active employee cannot reach the app at all")

    s.section("Marking them inactive kills the session already signed in")
    # The one that matters. Checking status only at /login left the phone in
    # their pocket working until the cookie happened to expire.
    _issue(person["id"], "cellar key", "key", location="the office hook")
    _issue(person["id"], "gate code", "code")
    r = oc.post(f"/directory/{person['id']}/toggle-status", follow_redirects=True)
    s.check("they are marked inactive", _status(person["id"]) == "inactive",
            detail=f"status {_status(person['id'])!r}, flash {flashes(r)[:1]}")
    after = theirs.get("/today")
    s.check("the same signed-in browser is turned away",
            after.status_code in (302, 401, 403),
            detail=f"HTTP {after.status_code} — a session opened before they left still works")
    s.check("and it is sent to the login page",
            "login" in after.headers.get("Location", "").lower()
            or after.status_code in (401, 403),
            detail=f"went to {after.headers.get('Location')!r}")

    s.section("The checklist names the keys they are actually holding")
    labels = [i["label"] for i in _items(person["id"])]
    s.check("a checklist is started", len(labels) >= len(m.DEFAULT_OFFBOARDING_ITEMS),
            detail=f"{len(labels)} item(s)")
    s.check("the cellar key is named, not just 'collect keys'",
            any("cellar key" in l for l in labels),
            detail=f"{labels}")
    s.check("with where it is kept, so it can be checked back in",
            any("the office hook" in l for l in labels),
            detail="the register knows where the key lives but the checklist does not say")
    s.check("the code says to change it, because they already know it",
            any("gate code" in l and "hange" in l for l in labels),
            detail=f"a code cannot be handed back: {[l for l in labels if 'gate code' in l]}")
    s.check("and the code is not listed as something to recover",
            not any("gate code" in l and l.startswith("Recover") for l in labels))

    s.section("Reactivating gives access back")
    oc.post(f"/directory/{person['id']}/toggle-status", follow_redirects=True)
    s.check("they are active again", _status(person["id"]) == "active")
    s.check("and their session works once more",
            _as(person["id"]).get("/today").status_code == 200)

    s.section("A second departure is a real checklist, not last time's ticks")
    # Seasonal staff leave in September and come back in June. The seed is
    # guarded against running twice, which is right, but it meant the second
    # departure showed September's completed list — so nobody chased the keys.
    conn = db()
    conn.execute("UPDATE offboarding_items SET done = 1 WHERE user_id = ?", (person["id"],))
    conn.commit()
    conn.close()
    oc.post(f"/directory/{person['id']}/toggle-status", follow_redirects=True)
    second = _items(person["id"])
    s.check("the checklist is not duplicated",
            len([i for i in second if i["label"] == m.DEFAULT_OFFBOARDING_ITEMS[0]]) == 1,
            detail=f"{len(second)} items — the same list was seeded twice")
    s.check("but it is outstanding again, not still ticked from last season",
            any(not i["done"] for i in second),
            detail="every line reads as already done, so nothing gets chased")
    s.check("collecting the keys in particular is open again",
            any(i["label"] == "Collect keys / property access" and not i["done"]
                for i in second),
            detail=f"{[(i['label'], i['done']) for i in second][:3]}")

    s.section("Re-saving them while already inactive does not undo today's work")
    # The reopen must fire on a real departure only. Saving the edit form again
    # while somebody is already inactive would otherwise wipe the ticks of
    # whoever spent the morning collecting the keys.
    conn = db()
    conn.execute("UPDATE offboarding_items SET done = 1 WHERE user_id = ?", (person["id"],))
    conn.commit()
    conn.close()
    oc.post(f"/directory/{person['id']}/edit", data={
        "name": person["name"], "email": person["email"], "job_role": "Housekeeping",
        "status": "inactive",
    }, follow_redirects=True)
    s.check("the ticks survive a save that changes nothing",
            all(i["done"] for i in _items(person["id"])),
            detail=f"{[(i['label'], i['done']) for i in _items(person['id'])][:3]}")

    s.section("Adding and ticking items by hand")
    oc.post(f"/directory/{person['id']}/offboarding/new",
            data={"label": f"{TAG} hand back the van fob"}, follow_redirects=True)
    made = [i for i in _items(person["id"]) if i["label"].endswith("van fob")]
    s.check("a line can be added", len(made) == 1, detail=f"{len(made)} found")
    if made:
        oc.post(f"/directory/{person['id']}/offboarding/{made[0]['id']}/toggle",
                follow_redirects=True)
        s.check("ticking it sticks",
                [i for i in _items(person["id"]) if i["id"] == made[0]["id"]][0]["done"] == 1)
        oc.post(f"/directory/{person['id']}/offboarding/{made[0]['id']}/toggle",
                follow_redirects=True)
        s.check("and it can be un-ticked, because people mistake rows",
                [i for i in _items(person["id"]) if i["id"] == made[0]["id"]][0]["done"] == 0)

    s.section("An empty line is not added")
    n = len(_items(person["id"]))
    oc.post(f"/directory/{person['id']}/offboarding/new", data={"label": "   "},
            follow_redirects=True)
    s.check("whitespace does not become a checklist item", len(_items(person["id"])) == n)

    s.section("Somebody else's checklist is not reachable through your own URL")
    stranger = _employee("Thibault")
    oc.post(f"/directory/{stranger['id']}/toggle-status", follow_redirects=True)
    theirs_item = _items(person["id"])[0]
    cross = oc.post(f"/directory/{stranger['id']}/offboarding/{theirs_item['id']}/toggle")
    s.check("toggling one person's item under another's id is a 404",
            cross.status_code == 404, detail=f"HTTP {cross.status_code}")
    s.check("and the item is untouched",
            [i for i in _items(person["id"]) if i["id"] == theirs_item["id"]][0]["done"]
            == theirs_item["done"])

    s.section("Equipment they are still holding is named too")
    # The same reasoning as the keys, on a different table. "Return uniform /
    # equipment" does not say WHICH laptop, so the one that never comes back is
    # the one nobody remembered was issued. equipment_items was not consulted.
    holder = _employee("Amandine")
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO equipment_items (user_id, label, notes, issued_at)
                    VALUES (?, ?, ?, ?)""",
                 (holder["id"], f"{TAG} work laptop", "the silver one", now))
    conn.execute("""INSERT INTO equipment_items (user_id, label, notes, issued_at, returned_at)
                    VALUES (?, ?, NULL, ?, ?)""",
                 (holder["id"], f"{TAG} old phone", now, now))
    conn.commit()
    conn.close()
    oc.post(f"/directory/{holder['id']}/toggle-status", follow_redirects=True)
    kit = [i["label"] for i in _items(holder["id"])]
    s.check("the laptop they still have is on the checklist",
            any("work laptop" in l for l in kit),
            detail=f"only a generic line to chase real kit: {kit}")
    s.check("with the note that identifies which one",
            any("silver one" in l for l in kit),
            detail="the record says which laptop, the checklist does not")
    s.check("but kit already handed back is not chased again",
            not any("old phone" in l for l in kit),
            detail=f"asking for something already returned: {kit}")

    s.section("Marking somebody inactive is written down")
    conn = db()
    audited = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'employee_status_changed' "
        "AND target LIKE ?", (TAG + "%",)).fetchone()["c"]
    conn.close()
    s.check("the change of status is audited", audited >= 1, detail=f"{audited} entries")

    s.section("Guards")
    s.check("an employee cannot deactivate a colleague",
            ec.post(f"/directory/{person['id']}/toggle-status").status_code in (302, 403))
    s.check("an employee cannot tick somebody's offboarding item",
            ec.post(f"/directory/{person['id']}/offboarding/{theirs_item['id']}/toggle"
                    ).status_code in (302, 403))
    s.check("an employee cannot add a line to one",
            ec.post(f"/directory/{person['id']}/offboarding/new",
                    data={"label": "x"}).status_code in (302, 403))

    _cleanup()
    return s
