"""The doctor's note the schema expected and nothing ever attached.

absences.doctor_note_filename existed in the table and appeared nowhere else
in the app. Meanwhile the form has a "self-certified" tick and the HR page
prints "(certified)" when it is off — so an absence could claim a doctor
signed it off while there was no way to attach the certificate, and no way
to notice none was there.

Access is deliberately narrower than an ordinary staff document: those open
for the owner OR the person they belong to, and this is health data, so it is
owner-only. The employee already has their own copy.
"""
import io
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-note-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM absences WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def _person(conn, name):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', 'employee', ?, 'General', 'active', ?)""",
        (f"{TAG}{name}@example.invalid", name, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name}@example.invalid",)).fetchone()["id"]


def _absence(conn, uid, reason, days_ago, self_certified):
    conn.execute(
        """INSERT INTO absences (user_id, start_date, end_date, kind, reason,
           self_certified, created_at) VALUES (?, ?, ?, 'sick', ?, ?, ?)""",
        (uid, _iso(-days_ago), _iso(-days_ago + 1), TAG + reason,
         1 if self_certified else 0, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM absences WHERE reason = ?",
                        (TAG + reason,)).fetchone()["id"]


def run():
    s = Suite("sick note")
    oc, ec, _owner, employee = clients()
    conn = db()
    _cleanup(conn)
    uid = _person(conn, "Poorly")

    s.section("Certified with nothing behind it")
    certified = _absence(conn, uid, "fluA", 5, self_certified=False)
    selfcert = _absence(conn, uid, "fluB", 6, self_certified=True)
    missing = m.absences_missing_certificate(conn)
    ids = {a["id"] for a in missing}
    s.check("an absence marked certified with no note is flagged", certified in ids)
    # A self-certified absence has no certificate BY DEFINITION. Flagging it
    # would make the list meaningless within a week.
    s.check("a self-certified one is not flagged", selfcert not in ids)

    s.section("Attaching the certificate")
    r = oc.post(f"/hr/absences/{certified}/note",
                data={"note": (io.BytesIO(b"%PDF-1.4 test"), "arret.pdf")},
                content_type="multipart/form-data", follow_redirects=True)
    s.check("the upload is accepted", r.status_code == 200, detail=str(r.status_code))
    row = conn.execute("SELECT doctor_note_filename FROM absences WHERE id = ?",
                       (certified,)).fetchone()
    s.check("the filename is stored", bool(row["doctor_note_filename"]),
            detail=str(row["doctor_note_filename"]))
    s.check("and it drops off the missing list",
            certified not in {a["id"] for a in m.absences_missing_certificate(conn)})

    s.section("Only sensible file types")
    other = _absence(conn, uid, "fluC", 7, self_certified=False)
    r = oc.post(f"/hr/absences/{other}/note",
                data={"note": (io.BytesIO(b"MZ..."), "note.exe")},
                content_type="multipart/form-data", follow_redirects=True)
    row = conn.execute("SELECT doctor_note_filename FROM absences WHERE id = ?",
                       (other,)).fetchone()
    s.check("an executable is refused", not row["doctor_note_filename"],
            detail=str(row["doctor_note_filename"]))

    s.section("Who may read it")
    # Narrower than an ordinary staff document on purpose: health data, and
    # the employee already holds their own copy.
    s.check("the owner can open it",
            oc.get(f"/hr/absences/{certified}/note/view").status_code == 200)
    s.check("an employee cannot",
            ec.get(f"/hr/absences/{certified}/note/view").status_code in (302, 403),
            detail=str(ec.get(f"/hr/absences/{certified}/note/view").status_code))
    s.check("and an absence with no note 404s rather than erroring",
            oc.get(f"/hr/absences/{selfcert}/note/view").status_code == 404)

    s.section("The medical filename never reaches the audit log")
    leaked = conn.execute(
        """SELECT COUNT(*) AS c FROM audit_log
            WHERE COALESCE(details, '') LIKE '%arret%'
               OR COALESCE(target, '') LIKE '%arret%'""").fetchone()["c"]
    s.check("the log records that a note was attached, not which file",
            leaked == 0, detail=str(leaked))

    s.section("The page")
    page = oc.get("/admin/hr").get_data(as_text=True)
    s.check("the banner names the gap", "no certificate attached" in page)
    s.check("and an attached one is offered as a link",
            "certificate attached" in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
