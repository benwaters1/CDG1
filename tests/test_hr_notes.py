"""Ask HR — the promise on the button, and whether the app keeps it.

When somebody sends a note, the app tells them: "Sent — only the owner can see
this." That sentence is the entire value of the feature. It is where a grievance
about a manager goes, and the only reason anybody would type one honestly.

It was not true. /admin/hr-notes sat in the `team` area, and `owner_required`
does not mean owner — it means "full access, or a preset granting this page's
area". Three of the presets in the live config grant `team`, including one
described as "The team and the house: staff, rotas, estate". So a note about a
manager was readable by that manager, under a sentence promising otherwise.

Private HR notes now live in `management`, the area no non-owner preset grants
— the same place the vault and the audit log are held, and the same reasoning
that already keeps the payroll export out of `team`.

Moving it exposed a second, wider problem. The Team menu drew its links inside
one `may('team')` check, without asking whether the viewer could open each
target. Somebody with team-but-not-payroll access was already being shown a
Payroll Pack link that 403s on click — the "visible in the menu but forbidden
when clicked" case NAV_AREAS' own comment calls the worst of both. The nav asks
per endpoint now, so a link cannot drift from the permission behind it.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZHRN"
PRIVATE = "I need to raise something about how I am being spoken to on shift"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM hr_notes WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM notifications WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _employee(name, preset=None):
    conn = db()
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(users)").fetchall()}
    fields = {
        "name": f"{TAG} {name}", "email": f"{TAG.lower()}.{name.lower()}@example.invalid",
        "role": "employee", "status": "active", "job_role": "Housekeeping",
        "password_hash": "x", "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if preset is not None and "access_preset" in cols:
        fields["access_preset"] = preset
    keys = [k for k in fields if k in cols]
    conn.execute(f"INSERT INTO users ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' * len(keys))})", [fields[k] for k in keys])
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _as(user_id):
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def _notes_of(user_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM hr_notes WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Ask HR")
    _cleanup()
    oc, ec, owner, emp = clients()

    person = _employee("Sylvie")
    theirs = _as(person["id"])

    s.section("Sending a note")
    r = theirs.post("/hr/ask", data={"body": PRIVATE}, follow_redirects=True)
    notes = _notes_of(person["id"])
    s.check("it is stored", len(notes) == 1, r)
    if not notes:
        _cleanup()
        return s
    note = notes[0]
    s.check("as open, waiting on somebody", note["status"] == "open")
    s.check("the owner is notified", _notified(owner["id"]) >= 1)

    s.section("An empty note is not stored")
    theirs.post("/hr/ask", data={"body": "   "}, follow_redirects=True)
    s.check("whitespace does not become a note", len(_notes_of(person["id"])) == 1)

    s.section("They can see their own, and only their own")
    other = _employee("Gaspard")
    _as(other["id"]).post("/hr/ask", data={"body": f"{TAG} unrelated question"},
                          follow_redirects=True)
    mine = theirs.get("/hr/ask").get_data(as_text=True)
    s.check("their own note is on their page", PRIVATE in mine)
    s.check("a colleague's note is not", "unrelated question" not in mine,
            detail="one employee can read another's private note")

    s.section("The promise on the button: only the owner")
    # owner_required does not mean owner. It means full access, or a preset
    # granting this page's area -- and this page used to sit in `team`, which
    # three presets in the live config grant.
    said = " ".join(flashes(r)).lower()
    s.check("the app does promise confidentiality", "only the owner" in said,
            detail=f"flash was {said!r}")
    manager = _employee("Karine", preset="karina")     # team, rota, estate
    mc = _as(manager["id"])
    s.check("somebody with team access can still run the team pages",
            mc.get("/directory").status_code == 200,
            detail="the preset under test does not actually grant team access, "
                   "so the next check would pass for the wrong reason")
    seen = mc.get("/admin/hr-notes")
    s.check("but they cannot read private HR notes", seen.status_code in (302, 403),
            detail=f"HTTP {seen.status_code} — a note about a manager is readable "
                   "by that manager, under a sentence saying only the owner can see it")
    if seen.status_code == 200:
        s.check("and the note body is certainly not on their screen",
                PRIVATE not in seen.get_data(as_text=True),
                detail="the private text itself was served to a manager")
    # By state, not status code: a successful handle_hr_note redirects, so
    # checking for 302 accepts the very thing it is meant to forbid.
    mc.post(f"/admin/hr-notes/{note['id']}/handle",
            data={"response": f"{TAG} manager reply"}, follow_redirects=True)
    after_mgr = _notes_of(person["id"])[0]
    s.check("nor can they reply to one", (after_mgr["response"] or "") == "",
            detail=f"a manager's reply was written: {after_mgr['response']!r}")
    s.check("and the note is still open, not closed by them",
            after_mgr["status"] == "open", detail=f"status {after_mgr['status']!r}")

    s.section("The menu does not offer a door that is locked")
    # NAV_AREAS' own comment: a page visible in the menu but forbidden when
    # clicked is the worst of both. The Team menu was drawn from one may('team')
    # check, so team-without-payroll saw a Payroll Pack link that 403s.
    nav = mc.get("/directory").get_data(as_text=True)
    offered = [name for name, path in (("Payroll Pack", "/admin/payroll"),
                                       ("Ask HR", "/admin/hr-notes"))
               if path in nav]
    refused = [name for name, path in (("Payroll Pack", "/admin/payroll"),
                                       ("Ask HR", "/admin/hr-notes"))
               if path in nav and mc.get(path).status_code != 200]
    s.check("every link in the menu opens for the person seeing it", not refused,
            detail=f"offered {offered}, but {refused} give 403")

    s.section("The owner reads it and replies")
    page = oc.get("/admin/hr-notes")
    s.check("the owner's list loads", page.status_code == 200, page)
    s.check("with the note on it", PRIVATE in page.get_data(as_text=True))
    before = _notified(person["id"])
    oc.post(f"/admin/hr-notes/{note['id']}/handle",
            data={"response": "Let's talk on Thursday — I want to hear it properly."},
            follow_redirects=True)
    replied = _notes_of(person["id"])[0]
    s.check("the reply is saved", (replied["response"] or "").startswith("Let's talk"))
    s.check("the note is marked handled", replied["status"] == "handled")
    s.check("and the time of the reply is recorded", replied["responded_at"] is not None)
    s.check("the employee is told", _notified(person["id"]) == before + 1,
            detail=f"{before} -> {_notified(person['id'])}")

    s.section("The employee can read the reply")
    s.check("it is on their page", "Thursday" in theirs.get("/hr/ask").get_data(as_text=True),
            detail="the owner replied and the employee cannot see it")

    s.section("A note marked handled by mistake can be reopened")
    oc.post(f"/admin/hr-notes/{note['id']}/handle", data={"response": ""},
            follow_redirects=True)
    s.check("it goes back to open", _notes_of(person["id"])[0]["status"] == "open")
    s.check("and the reply is not thrown away",
            (_notes_of(person["id"])[0]["response"] or "").startswith("Let's talk"),
            detail="reopening destroyed what the owner had already written")

    s.section("Guards")
    s.check("an ordinary employee cannot open the list",
            ec.get("/admin/hr-notes").status_code in (302, 403))
    s.check("nor handle a note",
            ec.post(f"/admin/hr-notes/{note['id']}/handle",
                    data={"response": "x"}).status_code in (302, 403))
    s.check("handling one that does not exist is a 404",
            oc.post("/admin/hr-notes/999999/handle", data={"response": "x"}).status_code == 404)

    _cleanup()
    return s


def _notified(user_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? "
            "AND kind LIKE 'hr_note%'", (user_id,)).fetchone()["c"]
    finally:
        conn.close()
