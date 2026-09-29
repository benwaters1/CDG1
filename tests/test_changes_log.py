"""The changes log: every change to a booking, a payment, a refund, a guest,
field by field, and who made it -- written down by the database itself.

What there was: the audit trail says what people did in a line of words, and
nothing said what the data did. A value before a change was gone, and so,
often, was who changed it.

What this holds:

  - A change is written down field by field -- only the fields that changed,
    before and after -- and a change that changes nothing is not.
  - It is put down to who made it: the owner on a page, the guest on their
    own page, a job (even inside the job runner's stand-in request for "/"),
    and a change made outside the app is never claimed by the next one the
    app makes.
  - A row added or deleted is written down with its values.
  - Never written down: a key, anything of Stripe's, a health note.
  - The triggers follow the tables: a new column is written down from its
    first change; off, nothing is.
  - The page lists it, chip by chip, searches values, opens each change; the
    export is a row per field; an employee sees none of it.
  - Two years; a guest's copy of what we hold includes what it says about
    them; erasing them takes them out of it and leaves the changes. The
    notice says so.
"""
import csv
import io
import json
from datetime import timedelta

from _harness import Suite, db, visible_text, clients
import _harness

m = _harness.m
TAG = "ZZCL"
WHO = f"{TAG.lower()}@example.invalid"
SECRET = f"{TAG}secretmanagetoken"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        conn.execute("DELETE FROM change_log WHERE old_values LIKE ? OR new_values LIKE ?",
                     (f"%{TAG}%", f"%{TAG}%"))
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        places = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM workshop_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                     "(SELECT id FROM workshops WHERE title LIKE ?)", like)
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", like)
        conn.execute("DELETE FROM consent_events WHERE guest_id IN "
                     "(SELECT id FROM guests WHERE name LIKE ?)", like)
        conn.execute("DELETE FROM guests WHERE name LIKE ?", like)
        conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("A guest asked%",))
        conn.execute("DELETE FROM change_log WHERE old_values LIKE ? OR new_values LIKE ?",
                     (f"%{TAG}%", f"%{TAG}%"))
        conn.commit()
    finally:
        conn.close()


def _entries(table, row_id):
    conn = db()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT change_log.*, users.name AS actor_name FROM change_log
                 LEFT JOIN users ON users.id = change_log.actor_user_id
                WHERE table_name = ? AND row_id = ? ORDER BY id""", (table, row_id)).fetchall()]
    finally:
        conn.close()


def _changed(entry):
    return {k: (b, a) for k, b, a in m.change_log_fields(entry)}


def run():
    s = Suite("The changes log")
    _cleanup()
    try:
        _run(s)
    finally:
        _cleanup()
    return s


def _run(s):
    oc, ec, owner, _emp = clients()
    now = _harness.datetime_now()
    room = _harness.ensure_room()
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Guest", WHO, now))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?", (WHO,)).fetchone()["id"]
    # Somebody else, whose name merely contains theirs.
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Guestroom Smith", f"{TAG.lower()}.bystander@example.invalid", now))
    conn.commit()
    conn.close()

    s.section("Field by field, and who made it")
    oc.post(f"/guests/{gid}/restrict", data={"on": "1", "how": f"{TAG} on the telephone"})
    staff = [e for e in _entries("guests", gid) if e["action"] == "changed"]
    fields = _changed(staff[-1]) if staff else {}
    s.check("the owner's change is written down, only the fields that changed, before and after",
            staff and set(fields) == {"restricted_at", "restricted_by_user_id", "restricted_how"}
            and fields["restricted_how"] == (None, f"{TAG} on the telephone"),
            detail=str(fields))
    s.check("and it is put down to them, and to the page",
            staff and staff[-1]["actor"] == "staff" and staff[-1]["actor_user_id"] == owner["id"]
            and staff[-1]["endpoint"] == "restrict_guest",
            detail=str({k: staff[-1][k] for k in ("actor", "actor_user_id", "endpoint")}) if staff else "")
    oc.post(f"/guests/{gid}/restrict", data={"on": "0", "how": "tidying up"})
    # An update that finds the row and leaves every value as it was -- the
    # trigger fires, and must write nothing.
    before = len(_entries("guests", gid))
    conn = db()
    conn.execute("UPDATE guests SET name = name, email = email, preferences = preferences "
                 "WHERE id = ?", (gid,))
    conn.commit()
    conn.close()
    s.check("a change that changes nothing is not written down",
            len(_entries("guests", gid)) == before)

    conn = db()
    with m.app.test_request_context("/"):
        token = m.guest_portal_token(conn, WHO)
    conn.commit()
    conn.close()
    m.app.test_client().post(f"/my/{token}/stop-using")
    guest = [e for e in _entries("guests", gid) if "restricted_at" in _changed(e)]
    s.check("the guest's own change is put down to the guest",
            guest and guest[-1]["actor"] == "guest" and guest[-1]["actor_user_id"] is None
            and guest[-1]["endpoint"] == "guest_portal_stop_using",
            detail=str({k: guest[-1][k] for k in ("actor", "endpoint")}) if guest else "")

    # A job, inside the stand-in request the job runner makes -- for "/", which
    # Flask matches to the home page like any visitor's.
    with m.app.test_request_context("/"):
        m.g.change_job = "zz_test_job"
        conn = m.get_db()
        conn.execute("UPDATE guests SET preferences = ? WHERE id = ?", (f"{TAG} a job's change", gid))
        conn.commit()
        conn.close()
    job = [e for e in _entries("guests", gid) if "preferences" in _changed(e)]
    s.check("a job's change is the job's, even inside a request for the home page",
            job and job[-1]["actor"] == "job" and job[-1]["endpoint"] == "zz_test_job",
            detail=str({k: job[-1][k] for k in ("actor", "endpoint")}) if job else "")

    # Outside the app: a plain connection, as a sqlite shell or a script would
    # open -- not db(), which is the app's own get_db().
    import sqlite3
    raw = sqlite3.connect(m.DB_PATH)
    raw.execute("UPDATE guests SET name_pronunciation = ? WHERE id = ?", (f"{TAG} outside", gid))
    raw.commit()
    raw.close()
    oc.post(f"/guests/{gid}/restrict", data={"on": "0", "how": "anything"})
    outside = [e for e in _entries("guests", gid) if "name_pronunciation" in _changed(e)]
    s.check("a change made outside the app is not claimed by the next the app makes",
            outside and outside[-1]["claimed"] == 0 and outside[-1]["actor"] is None
            and m.change_log_who(dict(outside[-1], actor_name=None)) == "Outside the app",
            detail=str({k: outside[-1][k] for k in ("claimed", "actor", "endpoint", "id")}) if outside else "")

    s.section("Added and deleted, with their values")
    conn = db()
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at) VALUES (?, ?, ?, ?, ?, '2029-07-01', '2029-07-03', 2, 'confirmed',
                    300, ?)""", (room["id"], f"{TAG}S", SECRET, f"{TAG} Guest", WHO, now))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()["id"]
    conn.commit()
    conn.close()
    with m.app.test_request_context("/"):
        m.g.change_job = "zz_test_job"
        conn = m.get_db()
        conn.execute("""INSERT INTO booking_payments (booking_id, amount, method,
                        stripe_payment_intent_id, created_at) VALUES (?, 175, 'bank_transfer',
                        'pi_zzclnever', ?)""", (bid, now))
        pid = conn.execute("SELECT id FROM booking_payments WHERE booking_id = ?", (bid,)).fetchone()["id"]
        conn.commit()
        conn.execute("DELETE FROM booking_payments WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
    pay = _entries("booking_payments", pid)
    s.check("a row added is written down with what it held",
            [e["action"] for e in pay] == ["added", "deleted"]
            and json.loads(pay[0]["new_values"]).get("amount") == 175,
            detail=str([(e["action"], e["new_values"]) for e in pay]))
    s.check("and a row deleted with what it held when it went",
            len(pay) == 2 and json.loads(pay[1]["old_values"]).get("method") == "bank_transfer")

    s.section("What is never written down")
    conn = db()
    conn.execute("INSERT INTO workshops (title, price_per_person, created_at) VALUES (?, 100, ?)",
                 (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, created_at)
                    VALUES (?, '2029-08-01', '2029-08-03', ?)""", (wid, now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                    guest_name, guest_email, party_size, status, total_price, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 100, ?)""",
                 (sid, f"{TAG}W", f"{TAG}wtoken".lower(), f"{TAG} Guest", WHO, now))
    wb = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?", (f"{TAG}W",)).fetchone()["id"]
    conn.commit()
    conn.close()
    with m.app.test_request_context("/"):
        m.g.change_job = "zz_test_job"
        conn = m.get_db()
        entries_before = len(_entries("workshop_bookings", wb))
        conn.execute("""UPDATE workshop_bookings SET medical_notes = ?, dietary_notes = ?,
                        manage_token = ?, stripe_customer_id = 'cus_zzclnever' WHERE id = ?""",
                     (f"{TAG} a heart condition", f"{TAG} no nuts", f"{TAG}newtoken", wb))
        conn.commit()
        conn.close()
    conn = db()
    everything = " ".join((r["old_values"] or "") + (r["new_values"] or "") for r in conn.execute(
        "SELECT old_values, new_values FROM change_log").fetchall())
    conn.close()
    s.check("a change to nothing but a health note, a key or a card is not written down at all",
            len(_entries("workshop_bookings", wb)) == entries_before)
    s.check("and no key, health note or Stripe reference is anywhere in the log",
            SECRET not in everything and "heart condition" not in everything
            and "no nuts" not in everything and "cus_zzclnever" not in everything
            and "pi_zzclnever" not in everything and "newtoken" not in everything)

    s.section("The triggers follow the tables")
    conn = db()
    conn.execute("ALTER TABLE promo_codes ADD COLUMN zzcl_extra TEXT")
    with m.app.test_request_context("/"):
        m.install_change_log_triggers(conn)
    conn.execute("""INSERT INTO promo_codes (code, discount_type, discount_value, created_at)
                    VALUES (?, 'percent', 5, ?)""", (f"{TAG}NEW", now))
    code_id = conn.execute("SELECT id FROM promo_codes WHERE code = ?", (f"{TAG}NEW",)).fetchone()["id"]
    conn.execute("UPDATE promo_codes SET zzcl_extra = 'x' WHERE id = ?", (code_id,))
    conn.commit()
    followed = [e for e in _entries("promo_codes", code_id) if "zzcl_extra" in _changed(e)]
    m.drop_change_log_triggers(conn)
    conn.execute("UPDATE promo_codes SET zzcl_extra = 'y' WHERE id = ?", (code_id,))
    conn.commit()
    off = [e for e in _entries("promo_codes", code_id) if _changed(e).get("zzcl_extra", (0, 0))[1] == "y"]
    conn.execute("DELETE FROM promo_codes WHERE id = ?", (code_id,))
    conn.execute("ALTER TABLE promo_codes DROP COLUMN zzcl_extra")
    m.install_change_log_triggers(conn)
    conn.commit()
    conn.close()
    s.check("a column a migration adds is written down from its first change", len(followed) == 1)
    s.check("and while the triggers are off, as they are for the migrations, nothing is",
            not off, detail=str(off))

    s.section("Startup and the job runner")
    import inspect
    start = inspect.getsource(m.init_db)
    s.check("startup turns the triggers off before the migrations and on after them",
            0 < start.index("drop_change_log_triggers(conn)") < start.index("CREATE TABLE IF NOT EXISTS users")
            and start.rindex("install_change_log_triggers(conn)") > start.index("seed_revenue_categories(conn)"))
    tick = inspect.getsource(m.automation_tick)
    s.check("the job runner names itself before its connection opens, and each job as it runs",
            'g.change_job = "automation"' in tick
            and tick.index('g.change_job = "automation"') < tick.index("conn = get_db()")
            and "g.change_job = job_name" in tick and 'conn.actor, conn.endpoint = "job", job_name' in tick)

    s.section("The page")
    page = oc.get(f"/admin/changes?q={TAG}").get_data(as_text=True)
    text = visible_text(page)
    s.check("the changes are listed, who made each and what it was",
            f"Guest profile {TAG} Guest" in text and "The guest" in text
            and "A scheduled job" in text and owner["name"] in text, detail=text[:400])
    conn = db()
    lv = m.change_log_list_view(conn, {"q": TAG, "what": "Guest profile"})
    found = m.change_log_list_view(conn, {"q": f"{TAG} a job's change"})
    conn.close()
    s.check("filtered to what it was, only that",
            lv["rows"] and {r["kind"] for r in lv["rows"]} == {"Guest profile"}, detail=str(len(lv["rows"])))
    s.check("found by a value before or after", [r["table_name"] for r in found["rows"]] == ["guests"],
            detail=str([(r["table_name"], r["id"]) for r in found["rows"]]))
    s.check("and each opens to its fields, before and after",
            "View 3 changes" in text and f"{TAG} on the telephone" in text)
    exported = oc.get(f"/admin/changes/export.csv?q={TAG}&what=Guest+profile").get_data(as_text=True)
    lines = list(csv.DictReader(io.StringIO(exported)))
    s.check("the export is a row for every field changed",
            len(lines) == sum(len(r["fields"]) or 1 for r in lv["rows"])
            and any(r["field"] == "Restricted how" and r["after"] == f"{TAG} on the telephone"
                    for r in lines), detail=f"{len(lines)} rows")
    s.check("an employee sees none of it",
            ec.get("/admin/changes").status_code in (302, 403)
            and ec.get("/admin/changes/export.csv").status_code in (302, 403))

    s.section("Two years, and the person comes out of it")
    conn = db()
    copy = m.guest_data_export(conn, WHO)
    s.check("a guest's copy of what we hold includes what the log says about them",
            any(WHO in (r.get("new_values") or "") + (r.get("old_values") or "")
                for r in copy["tables"].get("change_log", [])),
            detail=str(sorted(copy["tables"])))
    kept = conn.execute("SELECT COUNT(*) AS c FROM change_log WHERE table_name = 'guests' AND row_id = ?",
                        (gid,)).fetchone()["c"]
    m.guest_data_erase(conn, WHO)
    conn.commit()
    left = " ".join((r["old_values"] or "") + (r["new_values"] or "") for r in conn.execute(
        "SELECT old_values, new_values FROM change_log").fetchall())
    after = conn.execute("SELECT COUNT(*) AS c FROM change_log WHERE table_name = 'guests' AND row_id = ?",
                         (gid,)).fetchone()["c"]
    s.check("erasing them takes their address and name out of every value it kept",
            WHO not in left and f'"{TAG} Guest"' not in left)
    s.check("and leaves the changes: what, when, and who made them",
            after >= kept, detail=f"{kept} before, {after} after")
    s.check("and a bystander whose name merely contains theirs is left alone",
            f'"{TAG} Guestroom Smith"' in left)
    conn.execute("UPDATE change_log SET created_at = ? WHERE table_name = 'promo_codes' AND row_id = ?",
                 ((m.datetime.now(m.timezone.utc) - timedelta(days=800)).isoformat(), code_id))
    conn.commit()
    result = m.purge_change_log(conn)
    gone = not conn.execute("SELECT 1 FROM change_log WHERE table_name = 'promo_codes' AND row_id = ?",
                            (code_id,)).fetchone()
    conn.close()
    import inspect
    s.check("two years on, an entry goes, and the housekeeping job does it",
            gone and result.get("old entries in the changes log", 0) >= 1
            and "purge_change_log(conn)" in inspect.getsource(m.run_health_notes_purge_job),
            detail=str(result))

    s.section("The notice says so")
    words = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("what is written down, for how long, what never is, and what an erasure does",
            "What changed on your booking" in words and "Kept for two years" in words
            and "a health or access note" in words and "the fact of the change stays" in words)
