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
        # The files behind their documents -- a person added through the form
        # gets a draft agreement on the disk -- before the rows that name them.
        for row in conn.execute(f"SELECT filename FROM documents WHERE user_id IN ({marks})",
                                ids).fetchall():
            try:
                os.remove(os.path.join(m.UPLOAD_DIR, row["filename"]))
            except OSError:
                pass
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
    r = nc.get(f"/directory/{peer['id']}/edit")
    page = r.get_data(as_text=True)
    s.check("the peer's form opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("without the pay on it -- that is the owner's",
            'name="pay_rate"' not in page and 'name="pay_type"' not in page)
    s.check("or the notes the profile calls private to the owner",
            'name="notes"' not in page and "as found" not in page)
    r = nc.post(f"/directory/{peer['id']}/edit",
                data=_form_of(peer, skills=f"{TAG} first aid", pay_rate="€99/hour",
                              pay_type="Monthly", notes=f"{TAG} handover"),
                follow_redirects=True)
    after = _row(conn, peer["id"])
    s.check("the peer's skills changed", after["skills"] == f"{TAG} first aid", r)
    s.check("but pay and notes posted anyway were ignored",
            after["pay_rate"] == "€14/hour" and after["pay_type"] is None
            and after["notes"] == "as found",
            detail=f"{after['pay_rate']!r} / {after['pay_type']!r} / {after['notes']!r}")
    s.check("and no pay change was written down for them",
            not conn.execute("SELECT 1 FROM pay_rate_history WHERE user_id = ?",
                             (peer["id"],)).fetchone())
    s.check("on the Team page, with the confirmation -- not a 403 hiding whether it saved",
            r.status_code == 200 and _landed(r) == "/directory"
            and "Profile updated." in _said(r),
            detail=f"HTTP {r.status_code} at {_landed(r)}: {_said(r)}")
    r = oc.get(f"/directory/{peer['id']}/edit")
    page = r.get_data(as_text=True)
    s.check("the owner's form for the same person has both -- or the two above prove nothing",
            'name="pay_rate"' in page and 'name="notes"' in page and "as found" in page)
    oc.post(f"/directory/{peer['id']}/edit",
            data=_form_of(_row(conn, peer["id"]), notes=f"{TAG} owner's note"))
    s.check("and the owner changes the notes", _row(conn, peer["id"])["notes"] == f"{TAG} owner's note")

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

    sent = []
    was_mail, was_note = m.send_email, m.send_notification
    m.send_email = lambda *a, **kw: sent.append(a) or False
    m.send_notification = lambda *a, **kw: sent.append(a)
    try:
        _own_record(s, conn, oc, nc, fc, narrow, peer, full)
        _pay_history(s, conn, oc, nc, fc, wide, peer, small_owner)
        _rota(s, conn, oc, nc, _client(wide["id"]), narrow, peer, wide, full, small_owner)
        _wages(s, conn, oc, _client(wide["id"]), narrow, wide, full, small_owner)
    finally:
        m.send_email, m.send_notification = was_mail, was_note


def _said_own(r, sentence):
    """Refused because it is their own: the sentence, on a page they can read."""
    return r.status_code == 200 and sentence in _said(r)


def _own_record(s, conn, oc, nc, fc, narrow, peer, full):
    # ====================================================== their own record
    s.section("Nobody but an owner changes their own record")
    own = m.OWN_RECORD
    r = nc.get(f"/directory/{narrow['id']}/edit", follow_redirects=True)
    s.check("their own edit form is refused, readably", _said_own(r, own), r,
            detail=f"HTTP {r.status_code} at {_landed(r)}: {_said(r)}")
    s.check("and sends them to the page for the part that IS theirs",
            _landed(r) == "/my-contact-info", detail=_landed(r))
    s.check("without their pay, or the owner's notes about them, on it",
            'name="pay_rate"' not in r.get_data(as_text=True)
            and "as found" not in r.get_data(as_text=True))
    before = dict(_row(conn, narrow["id"]))
    r = nc.post(f"/directory/{narrow['id']}/edit",
                data=_form_of(narrow, pay_rate="€40/hour", status="inactive",
                              annual_leave_days="60", notes=f"{TAG} mine"),
                follow_redirects=True)
    after = dict(_row(conn, narrow["id"]))
    s.check("posting it anyway is refused", _said_own(r, own), r)
    s.check("their pay, status, leave and notes are as they were",
            {k: after[k] for k in ("pay_rate", "status", "annual_leave_days", "notes")}
            == {k: before[k] for k in ("pay_rate", "status", "annual_leave_days", "notes")},
            detail=str({k: after[k] for k in ("pay_rate", "status", "annual_leave_days", "notes")}))
    s.check("and no pay change was written down",
            not conn.execute("SELECT 1 FROM pay_rate_history WHERE user_id = ?",
                             (narrow["id"],)).fetchone())
    r = fc.post(f"/directory/{full['id']}/edit",
                data=_form_of(full, pay_rate="€40/hour"), follow_redirects=True)
    s.check("nor does full-access staff, who reaches every page",
            _said_own(r, own) and _row(conn, full["id"])["pay_rate"] == "€14/hour", r)

    s.section("Every route under /directory/<id>/ refuses the person it is about")
    item_ids = {}
    for table, col, extra in (("onboarding_items", "label", ", sort_order"),
                              ("offboarding_items", "label", ", sort_order"),
                              ("equipment_items", "label", ""),
                              ("check_in_notes", "body", "")):
        stamp = "issued_at" if table == "equipment_items" else "created_at"
        conn.execute(f"INSERT INTO {table} (user_id, {col}{extra}, {stamp}) "
                     f"VALUES (?, ?{', 0' if extra else ''}, ?)",
                     (narrow["id"], f"{TAG} own {table}", NOW))
        item_ids[table] = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    # Reading their own profile and adding their own document are theirs, and
    # are not decorated; everything that CHANGES the record is.
    theirs = {"profile", "upload_document"}
    rules = sorted((r for r in m.app.url_map.iter_rules()
                    if r.rule.startswith("/directory/<int:user_id>")
                    and r.endpoint not in theirs), key=lambda r: r.rule)
    s.check("the sweep finds the routes it is meant to",
            {"edit_employee", "set_quick_pin", "toggle_employee_status", "delete_employee",
             "regenerate_invite", "change_staff_email"} <= {r.endpoint for r in rules},
            detail=str(sorted(r.endpoint for r in rules)))
    before = dict(_row(conn, narrow["id"]))
    for rule in rules:
        url = rule.rule.replace("<int:user_id>", str(narrow["id"]))
        for seg, table in (("/onboarding/", "onboarding_items"),
                           ("/offboarding/", "offboarding_items"),
                           ("/equipment/", "equipment_items"), ("/notes/", "check_in_notes")):
            if seg in rule.rule:
                url = re.sub(r"<int:\w+>", str(item_ids[table]), url)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            r = nc.open(url, method=method, follow_redirects=True)
            # The 1:1 notes are in no area, so they answer 403 before asking.
            s.check(f"{method} {rule.rule}: the rota on themselves",
                    r.status_code == 403 or _said_own(r, own), r,
                    detail=f"HTTP {r.status_code} at {_landed(r)}: {_said(r)}")
    changed = {k for k, v in dict(_row(conn, narrow["id"])).items() if before[k] != v}
    s.check("and nothing on their own row moved", not changed, detail=str(sorted(changed)))
    for table, item in item_ids.items():
        s.check(f"their own {table} row is still there",
                conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (item,)).fetchone()
                is not None)
    oc.post(f"/directory/{narrow['id']}/set-pin", data={"pin": "5937"})
    s.check("while the owner still sets their PIN -- the refusal is not of everybody",
            bool(_row(conn, narrow["id"])["quick_pin_hash"]))

    s.section("Adding somebody: pay and notes are the owner's there too")
    for caller, by, email in ((nc, "the rota", f"{TAG}.newbie@example.com"),
                              (oc, "the owner", f"{TAG}.owners-newbie@example.com")):
        caller.post("/directory/new", data={
            "name": f"{TAG} New {by}", "email": email, "job_role": "Housekeeper",
            "pay_rate": "€30/hour", "pay_type": "Hourly", "notes": f"{TAG} secret"})
        made = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        s.check(f"{by} adds them", made is not None)
        if made is None:
            continue
        if caller is nc:
            s.check("with no pay and no notes, whatever was posted",
                    not made["pay_rate"] and not made["pay_type"] and not made["notes"],
                    detail=f"{made['pay_rate']!r} / {made['notes']!r}")
        else:
            s.check("with the pay and notes the owner gave",
                    made["pay_rate"] == "€30/hour" and made["notes"] == f"{TAG} secret")

    s.section("And their own HR record, on the HR page")
    hr_own = m.OWN_HR_RECORD

    def count(table, extra="1=1"):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND {extra}",
                            (narrow["id"],)).fetchone()[0]

    r = nc.post("/admin/hr/absences/new", data={
        "user_id": narrow["id"], "start_date": "2026-10-01", "kind": "sick"},
        follow_redirects=True)
    s.check("no absence recorded against themselves",
            _said_own(r, hr_own) and count("absences") == 0, r, detail=_said(r))
    r = nc.post("/admin/hr/certifications/new", data={
        "user_id": narrow["id"], "name": f"{TAG} own cert"}, follow_redirects=True)
    s.check("no certification of their own vouched for by themselves",
            _said_own(r, hr_own) and count("certifications") == 0, r)
    r = nc.post("/admin/hr/reviews/new", data={
        "user_id": narrow["id"], "review_date": "2026-10-15", "strengths": TAG},
        follow_redirects=True)
    s.check("no review of themselves", _said_own(r, hr_own) and count("performance_reviews") == 0, r)
    conn.execute("INSERT INTO certifications (user_id, name, required, created_at) "
                 "VALUES (?, ?, 1, ?)", (narrow["id"], f"{TAG} lapsed", NOW))
    conn.execute("INSERT INTO absences (user_id, start_date, end_date, kind, created_at) "
                 "VALUES (?, '2026-09-01', '2026-09-02', 'sick', ?)", (narrow["id"], NOW))
    conn.execute("INSERT INTO performance_reviews (user_id, reviewer_user_id, review_date, "
                 "status, created_at) VALUES (?, ?, '2026-10-15', 'draft', ?)",
                 (narrow["id"], peer["id"], NOW))
    conn.commit()
    cert = conn.execute("SELECT id FROM certifications WHERE user_id = ?",
                        (narrow["id"],)).fetchone()["id"]
    absence = conn.execute("SELECT id FROM absences WHERE user_id = ?",
                           (narrow["id"],)).fetchone()["id"]
    review = conn.execute("SELECT id FROM performance_reviews WHERE user_id = ?",
                          (narrow["id"],)).fetchone()["id"]
    r = nc.post(f"/admin/hr/certifications/{cert}/delete", follow_redirects=True)
    s.check("nor a lapsed one of their own quietly removed",
            _said_own(r, hr_own) and count("certifications") == 1, r)
    r = nc.post(f"/admin/hr/absences/{absence}/return-to-work",
                data={"return_to_work_note": TAG}, follow_redirects=True)
    s.check("nor their own return to work signed off",
            _said_own(r, hr_own) and count("absences", "return_to_work_done_at IS NOT NULL") == 0, r)
    r = nc.post(f"/admin/hr/reviews/{review}/share", follow_redirects=True)
    s.check("nor a review of them shared by them",
            _said_own(r, hr_own) and count("performance_reviews", "status = 'draft'") == 1, r)
    r = nc.post(f"/hr/absences/{absence}/note", follow_redirects=True)
    s.check("their own doctor's note IS theirs to hand in -- it asks for the file",
            "Choose a file first." in _said(r) and hr_own not in _said(r), r, detail=_said(r))


def _pay_history(s, conn, oc, nc, fc, wide, peer, small_owner):
    # ====================================================== pay history
    s.section("The pay history export is the owner's, as pay history on a profile is")
    url = "/directory/pay-history/export.csv"
    s.check("owner-only on purpose", "export_pay_history_csv" in m.OWNER_ONLY_ENDPOINTS)
    s.check("and in no area", "export_pay_history_csv" not in m.ENDPOINT_AREA)
    conn.execute("INSERT INTO pay_rate_history (user_id, old_pay_rate, new_pay_rate, "
                 "changed_by_user_id, changed_at) VALUES (?, '€14/hour', '€15/hour', ?, ?)",
                 (wide["id"], wide["id"], NOW))
    conn.commit()
    for caller, by in ((nc, "the rota (team)"), (_client(wide["id"]), "the manager (team and payroll)"),
                       (fc, "full-access staff")):
        r = caller.get(url)
        s.check(f"{by} cannot download it", r.status_code == 403,
                detail=f"HTTP {r.status_code}: {r.get_data(as_text=True)[:80]!r}")
    r = oc.get(url)
    body = r.get_data(as_text=True)
    s.check("the owner can, with the manager's change in it",
            r.status_code == 200 and wide["name"] in body and "€15/hour" in body,
            detail=f"HTTP {r.status_code}")

    page = oc.get("/directory").get_data(as_text=True)
    s.check("the owner's Team page links to it", url in page,
            detail="if this fails the check below proves nothing")
    conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (small_owner["id"],))
    conn.commit()
    try:
        r = _client(small_owner["id"]).get("/directory")
        page = r.get_data(as_text=True)
        s.check("an owner on a narrow preset gets the Team page's owner links "
                "without one that answers 403",
                r.status_code == 200 and "/directory/export.csv" in page and url not in page,
                detail=f"HTTP {r.status_code}")
    finally:
        conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (small_owner["id"],))
        conn.commit()


def _rota(s, conn, oc, nc, wc, narrow, peer, wide, full, small_owner):
    # ====================================================== time off
    s.section("Deciding time off is refused upwards, and for your own")
    asked = {}
    for person in (narrow, peer, wide, full, small_owner):
        conn.execute("INSERT INTO leave_requests (user_id, start_date, end_date, reason, "
                     "leave_type, status, requested_at) VALUES (?, '2026-12-01', '2026-12-03', "
                     "?, 'vacation', 'pending', ?)", (person["id"], TAG, NOW))
        asked[person["id"]] = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    def leave(person):
        return conn.execute("SELECT status FROM leave_requests WHERE id = ?",
                            (asked[person["id"]],)).fetchone()["status"]

    page = nc.get("/admin/leave", query_string={"who": peer["name"]}).get_data(as_text=True)
    s.check("the rota's leave page offers a peer's decision",
            f"/admin/leave/{asked[peer['id']]}/decide" in page,
            detail="if this fails the two below prove nothing")
    page = nc.get("/admin/leave", query_string={"who": wide["name"]}).get_data(as_text=True)
    s.check("but not the manager's, and says why",
            f"/admin/leave/{asked[wide['id']]}/decide" not in page
            and "can reach more of the app than you can" in html.unescape(page))
    page = nc.get("/admin/leave", query_string={"who": narrow["name"]}).get_data(as_text=True)
    s.check("nor their own", f"/admin/leave/{asked[narrow['id']]}/decide" not in page
            and "Your own, so the owner decides it." in html.unescape(page))

    for person, who in ((wide, "the manager"), (small_owner, "an owner"),
                        (full, "full-access staff")):
        r = nc.post(f"/admin/leave/{asked[person['id']]}/decide",
                    data={"status": "approved"}, follow_redirects=True)
        s.check(f"the rota cannot approve {who}'s time off",
                _refused(r) and leave(person) == "pending", r, detail=_said(r))
    s.check("and the refusal lands on the leave page",
            _landed(r) == "/admin/leave", detail=_landed(r))
    r = nc.post(f"/admin/leave/{asked[narrow['id']]}/decide",
                data={"status": "approved"}, follow_redirects=True)
    s.check("nor their own", _said_own(r, m.OWN_TIME_OFF) and leave(narrow) == "pending", r,
            detail=_said(r))
    nc.post(f"/admin/leave/{asked[peer['id']]}/decide", data={"status": "declined"})
    s.check("but a peer's, yes", leave(peer) == "declined")

    s.section("The bulk approve asks the same question, and says what it skipped")
    page = wc.get("/admin/approvals").get_data(as_text=True)
    s.check("the manager's queue has a tick box for the rota's time off",
            f'value="leave:{asked[narrow["id"]]}"' in page,
            detail="if this fails the check below proves nothing")
    s.check("and none for their own, an owner's, or full-access staff's",
            not any(f'value="leave:{asked[p["id"]]}"' in page for p in (wide, small_owner, full)))
    r = wc.post("/admin/approvals/bulk", data={"items": [
        f"leave:{asked[p['id']]}" for p in (narrow, wide, small_owner, full)]},
        follow_redirects=True)
    said = _said(r)
    s.check("the rota's is approved", leave(narrow) == "approved", r, detail=said)
    s.check("their own, the owner's and full-access staff's are not",
            leave(wide) == leave(small_owner) == leave(full) == "pending",
            detail=f"{leave(wide)} / {leave(small_owner)} / {leave(full)}")
    s.check("and the message names them, with why -- not a cheerful total",
            "Approved 1 of 4" in said and wide["name"] in said
            and "your own time off is the owner's to decide" in said
            and small_owner["name"] in said and full["name"] in said
            and "can reach more of the app than you can" in said, detail=said)
    oc.post(f"/admin/leave/{asked[wide['id']]}/decide", data={"status": "approved"})
    s.check("the owner approves the manager's", leave(wide) == "approved")

    # ====================================================== hours
    s.section("Correcting somebody's hours is refused upwards, and for your own")
    entry = {}
    for person in (narrow, peer, wide, small_owner):
        # Ends before it starts: the kind repair_time_entry exists to fix.
        conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) "
                     "VALUES (?, '2026-10-10T16:00:00+00:00', '2026-10-10T08:00:00+00:00', 0)",
                     (person["id"],))
        entry[person["id"]] = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    def clock_out(person):
        return conn.execute("SELECT clock_out_at FROM time_entries WHERE id = ?",
                            (entry[person["id"]],)).fetchone()["clock_out_at"]

    page = nc.get("/admin/timesheets", query_string={"who": peer["name"]}).get_data(as_text=True)
    s.check("the rota's timesheets offer to fix a peer's clocking",
            f"/admin/timesheets/{entry[peer['id']]}/repair" in page,
            detail="if this fails the two below prove nothing")
    page = nc.get("/admin/timesheets", query_string={"who": wide["name"]}).get_data(as_text=True)
    s.check("but not the manager's", f"/admin/timesheets/{entry[wide['id']]}/repair" not in page
            and "can reach more of the app than you can" in html.unescape(page))
    page = nc.get("/admin/timesheets", query_string={"who": narrow["name"]}).get_data(as_text=True)
    s.check("nor their own", f"/admin/timesheets/{entry[narrow['id']]}/repair" not in page
            and "Your own hours, so the owner corrects them." in html.unescape(page))

    for person, who in ((wide, "the manager"), (small_owner, "an owner")):
        r = nc.post(f"/admin/timesheets/{entry[person['id']]}/repair",
                    data={"action": "void"}, follow_redirects=True)
        s.check(f"the rota cannot repair {who}'s clocking",
                _refused(r) and clock_out(person) == "2026-10-10T08:00:00+00:00", r)
    r = nc.post(f"/admin/timesheets/{entry[narrow['id']]}/repair",
                data={"action": "void"}, follow_redirects=True)
    s.check("nor their own", _said_own(r, m.OWN_HOURS)
            and clock_out(narrow) == "2026-10-10T08:00:00+00:00", r, detail=_said(r))
    nc.post(f"/admin/timesheets/{entry[peer['id']]}/repair", data={"action": "void"})
    s.check("but a peer's, yes", clock_out(peer) == "2026-10-10T16:00:00+00:00")
    oc.post(f"/admin/timesheets/{entry[wide['id']]}/repair", data={"action": "void"})
    s.check("and the owner repairs the manager's", clock_out(wide) == "2026-10-10T16:00:00+00:00")

    s.section("And so is deciding a correction somebody asked for")
    asked_fix = {}
    for person in (narrow, peer, wide):
        for n in (1, 2):
            conn.execute("INSERT INTO timesheet_corrections (time_entry_id, user_id, note, "
                         "status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                         (entry[person["id"]], person["id"], f"{TAG} fix {n}", NOW))
            asked_fix[(person["id"], n)] = conn.execute(
                "SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    def fix(person, n):
        return conn.execute("SELECT status FROM timesheet_corrections WHERE id = ?",
                            (asked_fix[(person["id"], n)],)).fetchone()["status"]

    page = nc.get("/admin/timesheets/corrections").get_data(as_text=True)
    s.check("the corrections page offers a peer's",
            f"/admin/timesheets/corrections/{asked_fix[(peer['id'], 1)]}/resolve" in page,
            detail="if this fails the check below proves nothing")
    s.check("but not the manager's, nor their own",
            not any(f"/admin/timesheets/corrections/{asked_fix[(p['id'], n)]}/{verb}" in page
                    for p in (wide, narrow) for n in (1, 2) for verb in ("resolve", "dismiss")))
    for verb, n in (("resolve", 1), ("dismiss", 2)):
        r = nc.post(f"/admin/timesheets/corrections/{asked_fix[(wide['id'], n)]}/{verb}",
                    data={"clock_in_time": "09:00"}, follow_redirects=True)
        s.check(f"the rota cannot {verb} the manager's", _refused(r) and fix(wide, n) == "pending", r)
        r = nc.post(f"/admin/timesheets/corrections/{asked_fix[(narrow['id'], n)]}/{verb}",
                    data={"clock_in_time": "09:00"}, follow_redirects=True)
        s.check(f"nor {verb} their own", _said_own(r, m.OWN_HOURS)
                and fix(narrow, n) == "pending", r, detail=_said(r))
        nc.post(f"/admin/timesheets/corrections/{asked_fix[(peer['id'], n)]}/{verb}")
        s.check(f"but {verb} a peer's, yes",
                fix(peer, n) == ("resolved" if verb == "resolve" else "dismissed"))
    s.check("and the manager's clocking was never moved",
            conn.execute("SELECT clock_in_at FROM time_entries WHERE id = ?",
                         (entry[wide["id"]],)).fetchone()["clock_in_at"]
            == "2026-10-10T16:00:00+00:00")


def _wages(s, conn, oc, wc, narrow, wide, full, small_owner):
    # ====================================================== wages
    s.section("Recording a wage is refused upwards, and for your own")

    def wages(person):
        return conn.execute("SELECT COUNT(*) FROM wage_records WHERE user_id = ?",
                            (person["id"],)).fetchone()[0]

    def record(caller, person):
        return caller.post("/admin/payroll/wages/new", data={
            "user_id": person["id"], "basis": "hourly", "gross_amount": "15",
            "effective_from": "2026-10-01", "note": TAG}, follow_redirects=True)

    page = wc.get("/admin/payroll/wages").get_data(as_text=True)
    s.check("the manager's wage form offers the rota",
            f'<option value="{narrow["id"]}">' in page,
            detail="if this fails the check below proves nothing")
    s.check("but not themselves, or full-access staff",
            f'<option value="{wide["id"]}">' not in page
            and f'<option value="{full["id"]}">' not in page)
    r = record(wc, wide)
    s.check("the manager cannot record their own wage",
            _said_own(r, m.OWN_WAGE) and wages(wide) == 0, r, detail=_said(r))
    for person, who in ((full, "full-access staff"), (small_owner, "an owner")):
        r = record(wc, person)
        s.check(f"nor {who}'s", _refused(r) and wages(person) == 0, r, detail=_said(r))
    record(wc, narrow)
    s.check("but the rota's, yes", wages(narrow) == 1)
    record(oc, wide)
    s.check("and the owner records the manager's", wages(wide) == 1)


if __name__ == "__main__":
    print(run().report())
