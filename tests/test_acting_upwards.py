"""Nobody changes the record of somebody who can reach more than they can.

Resetting a sign-in and changing a sign-in address were refused upwards
(access_covers). Everything else on a person's record was not, and on 15 Oct
2026 that turned out to include the worst of it:

EDITING SOMEBODY. /directory/<id>/edit loaded and saved ANY users row for
anybody with `team` -- the owner's included. Status is on that form, and login
turns an inactive account away, so a member of staff on the karina preset
(team, rota, estate) could mark the owner inactive and lock them out of their
own house; or change the manager's pay rate, notes or contract.

THE ARRIVAL PIN. /directory/<id>/set-pin is in `rota`, and /arrive signs
whoever's PIN it is straight in. So anybody with the rota could set the owner's
PIN and become the owner. The same takeover resetting a sign-in was guarded
against, by a door nobody had looked at.

THE REST of the record -- marking somebody inactive, removing them, their
checklists and kit, their absences, certifications and reviews -- had the same
gap with smaller teeth. And deleting "an employee" pointed at the owner's id
took the owner's document files off the disk before noticing it was not
allowed to delete the row.

What this holds, through the pages and not just the function: a narrower
preset is refused on GET and POST against a wider one and against the owner,
with a sentence on a page it can read; a member of staff on the full-access
preset is refused against the owner too; the owner still may, so the
refusals are not merely nothing working; a peer on the same preset still may,
so it is refused upwards and not to everybody; the last active owner cannot be
marked inactive by anybody; and every route under /directory/<id>/ refuses,
including any written after this.

The doctor's note rides along: its docstring said owner-only from the start
and the sweep that gave every page an area put it in `team`.
"""
import html
import os
import re
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztupward"
NOW = datetime.now(timezone.utc).isoformat()

# The edit form, as employee_form.html posts it.
FORM_FIELDS = (
    "name", "job_role", "phone", "start_date", "status", "pay_rate", "pay_type",
    "notes", "skills", "emergency_contact_name", "emergency_contact_phone",
    "emergency_contact_relationship", "annual_leave_days", "reason_for_leaving",
    "contract_type", "contract_end_date", "trial_end_date", "notice_period_days",
)

WIDE_AREAS = "guests,team,rota,estate,comms,financial,payroll,restaurant,workshops,till,events"


def _row(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _form_of(row, **changes):
    data = {k: ("" if row[k] is None else str(row[k])) for k in FORM_FIELDS}
    data.update(changes)
    return data


def _said(r):
    return html.unescape(" ".join(flashes(r)))


def _refused(r):
    """A refusal somebody can read: the Team page (or wherever they were sent
    back to) carrying the sentence -- or a flat 403 where the page was never
    theirs to open at all."""
    if r.status_code == 403:
        return True
    return r.status_code == 200 and "owner's to do" in _said(r)


def _landed(r):
    req = getattr(r, "request", None)
    return req.path if req is not None else "?"


def _client(user_id):
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user_id
    return c


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (TAG + "%", TAG + "%"))]
    if ids:
        marks = ",".join("?" * len(ids))
        # Every column anywhere that points at users, cascading or not: the
        # audit log keeps its actors, and a review keeps its reviewer.
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        for t in tables:
            for fk in conn.execute(f"PRAGMA foreign_key_list({t})").fetchall():
                if fk["table"] == "users":
                    conn.execute(f"DELETE FROM {t} WHERE {fk['from']} IN ({marks})", ids)
        conn.execute(f"DELETE FROM audit_log WHERE actor_user_id IN ({marks})", ids)
        for uid in ids:
            conn.execute("DELETE FROM submission_log WHERE action = ?",
                         (f"arrive_pin_fail:{uid}",))
        conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    # Rows this suite hung on the real owner, by their label.
    for t, col in (("onboarding_items", "label"), ("offboarding_items", "label"),
                   ("equipment_items", "label"), ("check_in_notes", "body"),
                   ("documents", "title")):
        conn.execute(f"DELETE FROM {t} WHERE {col} LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM access_presets WHERE slug LIKE ?", (TAG + "-%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    conn.commit()
    for name in os.listdir(m.UPLOAD_DIR):
        if name.startswith(TAG):
            try:
                os.remove(os.path.join(m.UPLOAD_DIR, name))
            except OSError:
                pass


def run():
    s = Suite("acting_upwards")
    conn = db()
    _cleanup(conn)
    owner_id = None
    owner_before = None
    try:
        oc, _ec, owner, _emp = clients()
        owner_id = owner["id"]
        owner_before = dict(_row(conn, owner_id))
        _checks(s, conn, oc, owner_id)
    finally:
        if owner_before is not None:
            # Whatever happened above, the owner the rest of the run signs in
            # as goes back exactly as they were.
            cols = ("status", "quick_pin_hash") + FORM_FIELDS
            conn.execute(
                f"UPDATE users SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                tuple(owner_before[c] for c in cols) + (owner_id,))
            conn.commit()
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn, oc, owner_id):
    for slug, areas, full in ((f"{TAG}-narrow", "team,rota,estate", 0),
                              (f"{TAG}-wide", WIDE_AREAS, 0),
                              (f"{TAG}-full", "*", 1)):
        conn.execute("INSERT INTO access_presets (slug, name, areas, is_full_access, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (slug, slug, areas, full, NOW))

    def add(name, preset, role="employee", status="active"):
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role, status,
                   access_preset, account_claimed, pay_rate, notes, created_at)
               VALUES (?, 'x', ?, ?, 'General', ?, ?, 1, '€14/hour', 'as found', ?)""",
            (f"{TAG}.{name.lower().replace(' ', '-')}@example.invalid", role,
             f"{TAG} {name}", status, preset, NOW))
        return conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()

    narrow = add("Rota", f"{TAG}-narrow")          # Karina's shape: team, rota, estate
    peer = add("Peer", f"{TAG}-narrow")
    wide = add("Manager", f"{TAG}-wide")
    full = add("Full Staff", f"{TAG}-full")         # every page, but not the owner
    # An owner given a narrow preset -- areas no wider than the rota's own.
    small_owner = add("Small Owner", f"{TAG}-narrow", role="owner", status="inactive")
    conn.commit()
    owner = _row(conn, owner_id)
    nc, fc = _client(narrow["id"]), _client(full["id"])

    # ====================================================== the rule itself
    s.section("access_covers: upwards is refused, sideways and down are not")
    s.check("a narrow preset does not cover a wide one", not m.access_covers(narrow, wide))
    s.check("a wide one covers a narrow one", m.access_covers(wide, narrow))
    s.check("an equal preset covers its peer", m.access_covers(narrow, peer))
    s.check("nobody but an owner covers an owner on a NARROW preset",
            not m.access_covers(narrow, small_owner),
            detail="the same areas, so a subset test alone says yes -- and the owner "
                   "role carries profiles, 1:1 notes and pay history the areas do not")
    s.check("not even staff on the full-access preset cover an owner",
            not m.access_covers(full, owner),
            detail="reaching every page is not the same as being the house")
    s.check("an owner covers another owner", m.access_covers(owner, small_owner))
    s.check("and a full-access member of staff still covers the manager",
            m.access_covers(full, wide))

    # ====================================================== editing somebody
    s.section("The edit page refuses a narrower preset, on the form as well as the save")
    r = nc.get(f"/directory/{wide['id']}/edit", follow_redirects=True)
    s.check("GET against a wider preset is refused, readably", _refused(r), r,
            detail=f"HTTP {r.status_code} at {_landed(r)}")
    s.check("on the Team page, not a profile they cannot open",
            _landed(r) == "/directory", detail=_landed(r))
    s.check("naming who and why",
            wide["name"] in _said(r) and "can reach more of the app than you can" in _said(r),
            detail=_said(r))
    s.check("and the form, with the manager's pay on it, is not drawn",
            'name="pay_rate"' not in r.get_data(as_text=True))

    r = nc.post(f"/directory/{wide['id']}/edit",
                data=_form_of(wide, status="inactive", pay_rate="€99/hour",
                              notes=f"{TAG} rewritten"), follow_redirects=True)
    after = _row(conn, wide["id"])
    s.check("POST against a wider preset is refused", _refused(r), r)
    s.check("the manager is still active", after["status"] == "active")
    s.check("their pay and notes are as they were",
            after["pay_rate"] == "€14/hour" and after["notes"] == "as found",
            detail=f"{after['pay_rate']!r} / {after['notes']!r}")
    s.check("and no pay change was written down",
            not conn.execute("SELECT 1 FROM pay_rate_history WHERE user_id = ?",
                             (wide["id"],)).fetchone())

    s.section("And against the owner, which is the one that locks the house")
    r = nc.get(f"/directory/{owner_id}/edit", follow_redirects=True)
    s.check("GET on the owner is refused", _refused(r), r, detail=f"HTTP {r.status_code}")
    r = nc.post(f"/directory/{owner_id}/edit",
                data=_form_of(owner, status="inactive", notes=f"{TAG} owner"),
                follow_redirects=True)
    s.check("POST on the owner is refused", _refused(r), r)
    s.check("the owner is still active -- login would have turned them away",
            _row(conn, owner_id)["status"] == "active")
    r = fc.post(f"/directory/{owner_id}/edit",
                data=_form_of(owner, status="inactive"), follow_redirects=True)
    s.check("a full-access member of staff is refused on the owner too",
            _refused(r) and _row(conn, owner_id)["status"] == "active", r)
    r = nc.post(f"/directory/{small_owner['id']}/edit",
                data=_form_of(small_owner, notes=f"{TAG} small"), follow_redirects=True)
    s.check("and an owner on a narrow preset is still the owner's to edit",
            _refused(r) and _row(conn, small_owner["id"])["notes"] == "as found", r)

    s.section("The owner still may -- or the refusals above prove nothing")
    r = oc.get(f"/directory/{wide['id']}/edit")
    s.check("the owner opens the manager's form",
            r.status_code == 200 and 'name="pay_rate"' in r.get_data(as_text=True),
            detail=f"HTTP {r.status_code}")
    r = oc.post(f"/directory/{wide['id']}/edit",
                data=_form_of(wide, pay_rate="€16/hour"), follow_redirects=True)
    s.check("and changes their pay", _row(conn, wide["id"])["pay_rate"] == "€16/hour", r)

    s.section("A peer on the same preset may, and lands somewhere they can read")
    r = nc.post(f"/directory/{peer['id']}/edit",
                data=_form_of(peer, notes=f"{TAG} handover"), follow_redirects=True)
    s.check("the peer's notes changed", _row(conn, peer["id"])["notes"] == f"{TAG} handover", r)
    s.check("on the Team page, with the confirmation -- not a 403 hiding whether it saved",
            r.status_code == 200 and _landed(r) == "/directory"
            and "Profile updated." in _said(r),
            detail=f"HTTP {r.status_code} at {_landed(r)}: {_said(r)}")

    # ====================================================== the last owner
    s.section("The last active owner cannot be marked inactive, even by themselves")
    others = [x["id"] for x in conn.execute(
        "SELECT id FROM users WHERE role = 'owner' AND status = 'active' AND id != ?",
        (owner_id,))]
    marks = ",".join("?" * len(others))
    if others:
        conn.execute(f"UPDATE users SET status = 'inactive' WHERE id IN ({marks})", others)
        conn.commit()
    try:
        r = oc.post(f"/directory/{owner_id}/edit",
                    data=_form_of(owner, status="inactive", skills=f"{TAG} skills"),
                    follow_redirects=True)
        after = _row(conn, owner_id)
        s.check("still active", after["status"] == "active",
                detail="one wrong option in a select, and nobody left who can sign in to undo it")
        s.check("the rest of the form was saved", after["skills"] == f"{TAG} skills")
        s.check("and it says which part was not",
                "Saved, except the status" in _said(r), r, detail=_said(r))

        conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (small_owner["id"],))
        conn.commit()
        r = oc.post(f"/directory/{small_owner['id']}/edit",
                    data=_form_of(_row(conn, small_owner["id"]), status="inactive"),
                    follow_redirects=True)
        s.check("with another owner still active, an owner may be marked inactive",
                _row(conn, small_owner["id"])["status"] == "inactive", r,
                detail="refusing this too would leave a departed partner signed in")
    finally:
        if others:
            conn.execute(f"UPDATE users SET status = 'active' WHERE id IN ({marks})", others)
            conn.commit()

    # ====================================================== status, removal
    s.section("Marking somebody inactive is refused upwards")
    r = nc.post(f"/directory/{wide['id']}/toggle-status", follow_redirects=True)
    s.check("the manager is still active",
            _refused(r) and _row(conn, wide["id"])["status"] == "active", r)
    r = nc.post(f"/directory/{peer['id']}/toggle-status", follow_redirects=True)
    s.check("a peer can be", _row(conn, peer["id"])["status"] == "inactive", r)
    nc.post(f"/directory/{peer['id']}/toggle-status")
    s.check("and back", _row(conn, peer["id"])["status"] == "active")
    oc.post(f"/directory/{wide['id']}/toggle-status")
    s.check("the owner can mark the manager inactive",
            _row(conn, wide["id"])["status"] == "inactive")
    oc.post(f"/directory/{wide['id']}/toggle-status")
    s.check("and active again", _row(conn, wide["id"])["status"] == "active")

    s.section("Removing somebody is refused upwards, and never touches an owner")
    r = nc.post(f"/directory/{wide['id']}/delete", follow_redirects=True)
    s.check("the manager is still there", _refused(r) and _row(conn, wide["id"]) is not None, r)

    # An owner with no hours on record, which is the case that bit: with hours,
    # the history guard answered first and hid it. The stand-in owner has none,
    # so the real owner's timesheet is never touched.
    kept = f"{TAG}_owner_contract.pdf"
    with open(os.path.join(m.UPLOAD_DIR, kept), "wb") as fh:
        fh.write(b"%PDF-1.4 the owner's own document")
    conn.execute("INSERT INTO documents (user_id, title, filename, uploaded_at) "
                 "VALUES (?, ?, ?, ?)", (small_owner["id"], f"{TAG} contract", kept, NOW))
    conn.commit()
    r = oc.post(f"/directory/{small_owner['id']}/delete")
    s.check("pointed at an owner it answers not-found, even for an owner",
            r.status_code == 404, detail=f"HTTP {r.status_code}")
    s.check("the owner's row is still there", _row(conn, small_owner["id"]) is not None)
    s.check("and their files are still on the disk",
            os.path.exists(os.path.join(m.UPLOAD_DIR, kept)),
            detail="the files went first, then the role-scoped delete did nothing, "
                   "and the page said 'Employee removed.'")

    gone = add("Leaver", f"{TAG}-narrow")
    conn.commit()
    r = oc.post(f"/directory/{gone['id']}/delete", follow_redirects=True)
    s.check("the owner can still remove an employee with no history",
            _row(conn, gone["id"]) is None and "Employee removed." in _said(r), r)

    # ====================================================== the arrival PIN
    s.section("Setting an arrival PIN is refused upwards -- it is a way in")
    pin_before = _row(conn, owner_id)["quick_pin_hash"]
    r = nc.post(f"/directory/{owner_id}/set-pin", data={"pin": "4826"}, follow_redirects=True)
    s.check("the rota cannot set the owner's PIN",
            _refused(r) and _row(conn, owner_id)["quick_pin_hash"] == pin_before, r,
            detail="/arrive signs whoever's PIN it is straight in, as them")
    r = nc.post(f"/directory/{wide['id']}/set-pin", data={"pin": "4826"}, follow_redirects=True)
    s.check("nor the manager's",
            _refused(r) and not _row(conn, wide["id"])["quick_pin_hash"], r)
    nc.post(f"/directory/{peer['id']}/set-pin", data={"pin": "4826"})
    s.check("but a peer's, yes", bool(_row(conn, peer["id"])["quick_pin_hash"]))
    oc.post(f"/directory/{wide['id']}/set-pin", data={"pin": "4826"})
    s.check("and the owner sets anybody's", bool(_row(conn, wide["id"])["quick_pin_hash"]))

    # ====================================================== the checklists
    s.section("The checklists still work for the owner, and for a peer")
    # Through the routes, both ways, so a refusal that caught everybody would
    # show here -- and so removing an item is driven for real, not only by a
    # sweep posting to somebody who does not exist.
    for kind in ("onboarding", "offboarding"):
        table = f"{kind}_items"
        for caller, by, person in ((oc, "the owner", wide), (nc, "the rota", peer)):
            label = f"{TAG} {kind} by {by}"
            caller.post(f"/directory/{person['id']}/{kind}/new", data={"label": label})
            made = conn.execute(f"SELECT id, done FROM {table} WHERE user_id = ? AND label = ?",
                                (person["id"], label)).fetchone()
            s.check(f"{by} adds to {person['name']}'s {kind} list", made is not None)
            if not made:
                continue
            caller.post(f"/directory/{person['id']}/{kind}/{made['id']}/toggle")
            s.check("and ticks it",
                    conn.execute(f"SELECT done FROM {table} WHERE id = ?",
                                 (made["id"],)).fetchone()["done"] == 1)
            r = caller.post(f"/directory/{person['id']}/{kind}/{made['id']}/delete")
            s.check("and removes it",
                    r.status_code == 302 and not conn.execute(
                        f"SELECT 1 FROM {table} WHERE id = ?", (made["id"],)).fetchone(),
                    detail=f"HTTP {r.status_code}")

    # ====================================================== the HR page
    s.section("The HR page's forms are refused upwards too")

    def count(table, user_id, extra="1=1", params=()):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND {extra}",
                            (user_id,) + params).fetchone()[0]

    r = nc.post("/admin/hr/absences/new", data={
        "user_id": wide["id"], "start_date": "2026-10-01", "kind": "unauthorised"},
        follow_redirects=True)
    s.check("no absence recorded against the manager",
            _refused(r) and count("absences", wide["id"]) == 0, r)
    s.check("and the refusal lands on the HR page", _landed(r) == "/admin/hr", detail=_landed(r))
    nc.post("/admin/hr/absences/new", data={
        "user_id": peer["id"], "start_date": "2026-10-01", "kind": "sick"})
    s.check("one recorded against a peer", count("absences", peer["id"]) == 1)

    r = nc.post("/admin/hr/certifications/new", data={
        "user_id": wide["id"], "name": f"{TAG} cert"}, follow_redirects=True)
    s.check("no certification added to the manager",
            _refused(r) and count("certifications", wide["id"]) == 0, r)
    conn.execute("INSERT INTO certifications (user_id, name, required, created_at) "
                 "VALUES (?, ?, 1, ?)", (wide["id"], f"{TAG} first aid", NOW))
    conn.commit()
    cert = conn.execute("SELECT id FROM certifications WHERE user_id = ?",
                        (wide["id"],)).fetchone()["id"]
    r = nc.post(f"/admin/hr/certifications/{cert}/delete", follow_redirects=True)
    s.check("nor one of theirs removed", _refused(r) and count("certifications", wide["id"]) == 1, r)
    oc.post(f"/admin/hr/certifications/{cert}/delete")
    s.check("which the owner may", count("certifications", wide["id"]) == 0)

    conn.execute("INSERT INTO absences (user_id, start_date, end_date, kind, created_at) "
                 "VALUES (?, '2026-09-01', '2026-09-02', 'sick', ?)", (wide["id"], NOW))
    conn.commit()
    absence = conn.execute("SELECT id FROM absences WHERE user_id = ?",
                           (wide["id"],)).fetchone()["id"]
    r = nc.post(f"/admin/hr/absences/{absence}/return-to-work",
                data={"return_to_work_note": f"{TAG} fine"}, follow_redirects=True)
    s.check("no return-to-work written on the manager's absence",
            _refused(r) and count("absences", wide["id"],
                                  "return_to_work_done_at IS NOT NULL") == 0, r)
    r = nc.post(f"/hr/absences/{absence}/note", follow_redirects=True)
    s.check("nor a doctor's note attached to it",
            _refused(r) and count("absences", wide["id"],
                                  "doctor_note_filename IS NOT NULL") == 0, r)

    r = nc.post("/admin/hr/reviews/new", data={
        "user_id": wide["id"], "review_date": "2026-10-15", "improvements": f"{TAG}"},
        follow_redirects=True)
    s.check("no review written of the manager",
            _refused(r) and count("performance_reviews", wide["id"]) == 0, r)
    nc.post("/admin/hr/reviews/new", data={"user_id": peer["id"], "review_date": "2026-10-15"})
    s.check("a peer's, yes", count("performance_reviews", peer["id"]) == 1)
    conn.execute("INSERT INTO performance_reviews (user_id, reviewer_user_id, review_date, "
                 "status, created_at) VALUES (?, ?, '2026-10-15', 'draft', ?)",
                 (wide["id"], owner_id, NOW))
    conn.commit()
    review = conn.execute("SELECT id FROM performance_reviews WHERE user_id = ?",
                          (wide["id"],)).fetchone()["id"]
    told = []
    was = m.send_notification
    m.send_notification = lambda *a, **kw: told.append(a)
    try:
        r = nc.post(f"/admin/hr/reviews/{review}/share", follow_redirects=True)
        s.check("the owner's review of the manager is not shared by the rota",
                _refused(r) and count("performance_reviews", wide["id"],
                                      "status = 'draft'") == 1 and not told, r)
        oc.post(f"/admin/hr/reviews/{review}/share")
        s.check("the owner shares it", count("performance_reviews", wide["id"],
                                             "status = 'shared'") == 1 and len(told) == 1)
    finally:
        m.send_notification = was

    # ====================================================== the doctor's note
    s.section("A doctor's note is the owner's to open, as its docstring always said")
    s.check("owner-only on purpose", "view_absence_note" in m.OWNER_ONLY_ENDPOINTS)
    s.check("and in no area", "view_absence_note" not in m.ENDPOINT_AREA)
    s.check("so the rota cannot reach it", not m.can_reach(narrow, "view_absence_note"))
    note = f"{TAG}_sick_note.pdf"
    with open(os.path.join(m.UPLOAD_DIR, note), "wb") as fh:
        fh.write(b"%PDF-1.4 a medical certificate")
    conn.execute("INSERT INTO absences (user_id, start_date, end_date, kind, "
                 "self_certified, doctor_note_filename, created_at) "
                 "VALUES (?, '2026-10-05', '2026-10-06', 'sick', 0, ?, ?)",
                 (peer["id"], note, NOW))
    conn.commit()
    sick = conn.execute("SELECT id FROM absences WHERE doctor_note_filename = ?",
                        (note,)).fetchone()["id"]
    r = nc.get(f"/hr/absences/{sick}/note/view")
    s.check("not even a peer's", r.status_code == 403, detail=f"HTTP {r.status_code}")
    r = oc.get(f"/hr/absences/{sick}/note/view")
    s.check("the owner opens it", r.status_code == 200, detail=f"HTTP {r.status_code}")
    link = f"/hr/absences/{sick}/note/view"
    owner_hr = oc.get("/admin/hr").get_data(as_text=True)
    rota_hr = nc.get("/admin/hr").get_data(as_text=True)
    s.check("the owner's HR page links to it", link in owner_hr,
            detail="if this fails the check below proves nothing")
    s.check("the rota's says it is there without a link that answers 403",
            link not in rota_hr and "certificate attached" in rota_hr)

    # ====================================================== every route
    s.section("Every route under /directory/<id>/ refuses upwards, including new ones")
    # Real ids for the rows each route names, so a route without the guard
    # SUCCEEDS here rather than 404ing on a made-up id and passing by accident.
    item_ids = {}
    for target in (wide["id"], owner_id):
        for table, col, extra in (("onboarding_items", "label", ", sort_order"),
                                  ("offboarding_items", "label", ", sort_order"),
                                  ("equipment_items", "label", ""),
                                  ("check_in_notes", "body", "")):
            stamp = "issued_at" if table == "equipment_items" else "created_at"
            conn.execute(f"INSERT INTO {table} (user_id, {col}{extra}, {stamp}) "
                         f"VALUES (?, ?{', 0' if extra else ''}, ?)",
                         (target, f"{TAG} {table}", NOW))
            item_ids[(target, table)] = conn.execute(
                "SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    def which_table(rule):
        for seg, table in (("/onboarding/", "onboarding_items"),
                           ("/offboarding/", "offboarding_items"),
                           ("/equipment/", "equipment_items"),
                           ("/notes/", "check_in_notes")):
            if seg in rule:
                return table
        return None

    rules = sorted((r for r in m.app.url_map.iter_rules()
                    if r.rule.startswith("/directory/<int:user_id>")), key=lambda r: r.rule)
    s.check("the sweep finds the routes it is meant to",
            {"edit_employee", "set_quick_pin", "toggle_employee_status", "delete_employee",
             "regenerate_invite", "change_staff_email"} <= {r.endpoint for r in rules},
            detail=str(sorted(r.endpoint for r in rules)))
    for caller, by, target, who in ((nc, "the rota", wide["id"], "the manager"),
                                    (nc, "the rota", owner_id, "the owner"),
                                    (fc, "full-access staff", owner_id, "the owner")):
        before = dict(_row(conn, target))
        for rule in rules:
            table = which_table(rule.rule)
            url = rule.rule.replace("<int:user_id>", str(target))
            if table:
                url = re.sub(r"<int:\w+>", str(item_ids[(target, table)]), url)
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                r = caller.open(url, method=method, follow_redirects=True)
                s.check(f"{method} {rule.rule}: {by} on {who}", _refused(r), r,
                        detail=f"HTTP {r.status_code} at {_landed(r)}: {_said(r)}")
        changed = {k for k, v in dict(_row(conn, target)).items() if before[k] != v}
        s.check(f"and nothing on {who}'s row moved ({by})", not changed,
                detail=str(sorted(changed)))
        for table in ("onboarding_items", "offboarding_items", "equipment_items",
                      "check_in_notes"):
            s.check(f"{who}'s {table} row is still there",
                    conn.execute(f"SELECT 1 FROM {table} WHERE id = ?",
                                 (item_ids[(target, table)],)).fetchone() is not None)


if __name__ == "__main__":
    print(run().report())
