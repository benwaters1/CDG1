"""The register France requires a guest house to keep, and did not have.

An établissement d'hébergement must complete a fiche individuelle de police
for every guest who is not a French national — name, first names, date and
place of birth, nationality, home address — hold it for six months, and
produce it if asked. The obligation is on the house, not on the guest, and
the château had nowhere to record any of it.

FOUR THINGS THIS FILE IS ACTUALLY ABOUT, and none of them is "does the form
save":

  - ONLY WHAT THE LAW LISTS. No passport number, no scan. Collecting more
    because a form is already open is how a guest register becomes a data
    breach with extra steps, and the check for it reads the table's columns.
  - UNKNOWN IS NOT FRENCH. The obligation is on the house, so a missing
    nationality has to read as "this fiche is still needed". The safe default
    and the lazy default point in opposite directions here.
  - IT DELETES ITSELF, six months after the stay ENDS rather than six months
    after the fiche was written — otherwise a fiche filled in on arrival for a
    fortnight's stay goes a fortnight early.
  - AND THE PRIVACY NOTICE SAYS SO. The notice is a set of testable claims
    about this code. Collecting a date of birth and a home address without
    saying so would have made it untrue the moment it shipped, which is worse
    than having no notice at all.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTPOL"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM police_register WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE action LIKE 'police_fiche%'")
    conn.commit()
    conn.close()


def _stay(ref, arrive_offset=-1, nights=3, party=2):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    today = datetime.now(m.LOCAL_TZ).date()
    arrive = today + timedelta(days=arrive_offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 400, ?)""",
        (room, TAG + ref, TAG.lower() + "tok" + ref, TAG + " Guest " + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid", arrive.isoformat(),
         (arrive + timedelta(days=nights)).isoformat(), party,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("The police register")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    today = datetime.now(m.LOCAL_TZ).date()

    s.section("Only the fields the law lists")
    # Read off the table rather than trusted. The temptation on a form that is
    # already open is one more field, and the one more field here is a
    # passport number.
    conn = db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(police_register)")}
    conn.close()
    for want in ("surname", "first_names", "born_on", "born_at", "nationality",
                 "home_address"):
        s.check(f"the register holds {want.replace('_', ' ')}", want in cols)
    for must_not in ("passport_number", "passport", "document_number", "id_number",
                     "photo_filename", "scan"):
        s.check(f"and does not hold a {must_not.replace('_', ' ')}", must_not not in cols,
                detail="the arrêté lists the fields; anything past the list is "
                       "collected because a form was open, not because it is needed")

    s.section("Unknown nationality is not French")
    for text, want in (("French", True), ("française", True), ("FR", True),
                       ("Australian", False), ("", False), (None, False)):
        s.check(f"{text!r} reads as {'French' if want else 'not French'}",
                m.is_french_national(text) is want,
                detail="the obligation is on the house, so an unrecorded "
                       "nationality has to mean the fiche is still needed")

    s.section("A stay that has begun with nobody recorded")
    stay = _stay("ONE", arrive_offset=-1, party=2)
    conn = db()
    with m.app.test_request_context():
        missing = m.stays_missing_fiches(conn, today)
    conn.close()
    ours = [x for x in missing if x["booking"]["reference_code"] == TAG + "ONE"]
    s.check("it is listed as outstanding", bool(ours), detail=str(len(missing)))
    s.check("counting everybody on the booking, not just one",
            ours and ours[0]["outstanding"] == 2,
            detail=f"{ours[0]['outstanding'] if ours else '?'} of a party of 2 — "
                   "the register is per person, so a family of four is four fiches")

    # A stay in three weeks is not late; it has not happened.
    _stay("LATER", arrive_offset=21, party=2)
    conn = db()
    with m.app.test_request_context():
        missing = m.stays_missing_fiches(conn, today)
    conn.close()
    s.check("a stay that has not started yet is not chased",
            not [x for x in missing if x["booking"]["reference_code"] == TAG + "LATER"],
            detail="the fiche is filled in on arrival")

    s.section("Recording one")
    r = oc.post(f"/admin/bookings/{stay['id']}/register",
                data={"surname": "Fontaine", "first_names": "Amelie Claire",
                      "nationality": "Australian", "born_on": "1984-06-11",
                      "born_at": "Perth", "home_address": "12 Rue Nowhere, Perth"},
                follow_redirects=True)
    fiche = _one("SELECT * FROM police_register WHERE booking_id = ?", (stay["id"],))
    s.check("it is on the register", fiche is not None, detail=str(flashes(r)))
    s.check("with the name as written", fiche and fiche["surname"] == "Fontaine"
            and fiche["first_names"] == "Amelie Claire")
    s.check("the nationality", fiche and fiche["nationality"] == "Australian")
    s.check("where and when they were born",
            fiche and fiche["born_on"] == "1984-06-11" and fiche["born_at"] == "Perth")
    s.check("and their home address", fiche and "Perth" in (fiche["home_address"] or ""))
    s.check("stamped with who recorded it",
            fiche and fiche["recorded_by_user_id"] == owner["id"])
    s.check("and it is on the audit record",
            _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'police_fiche_recorded' "
                 "AND target = ?", (TAG + "ONE",))["c"] == 1)
    # The guest's name must NOT be in the audit line: it is a second table that
    # is not purged with the first, so copying it there keeps the name after
    # the fiche has gone.
    audit = _one("SELECT * FROM audit_log WHERE action = 'police_fiche_recorded' "
                 "AND target = ? ORDER BY id DESC LIMIT 1", (TAG + "ONE",))
    s.check("but the guest's name is not copied into it",
            audit and "Fontaine" not in ((audit["target"] or "") + (audit["details"] or "")),
            detail="audit rows are not purged with the register, so a name there "
                   "outlives the six months")

    conn = db()
    with m.app.test_request_context():
        state = m.police_register_needed(conn, stay["id"])
    conn.close()
    s.check("the count comes down", state["outstanding"] == 1,
            detail=str(state["outstanding"]))

    s.section("What it will not take")
    before = _one("SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                  (stay["id"],))["c"]
    r = oc.post(f"/admin/bookings/{stay['id']}/register",
                data={"surname": "", "first_names": "Nobody", "nationality": "Dutch"},
                follow_redirects=True)
    s.check("a fiche with no surname is refused",
            any("surname" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    r = oc.post(f"/admin/bookings/{stay['id']}/register",
                data={"surname": "Roux", "first_names": "Bernard", "nationality": ""},
                follow_redirects=True)
    s.check("and one with no nationality is refused",
            any("nationality" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    r = oc.post(f"/admin/bookings/{stay['id']}/register",
                data={"surname": "Roux", "first_names": "Bernard", "nationality": "Belgian",
                      "born_on": (today + timedelta(days=2)).isoformat()},
                follow_redirects=True)
    s.check("a date of birth in the future is refused",
            any("future" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and none of the three was stored",
            _one("SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                 (stay["id"],))["c"] == before,
            detail="a refusal that still writes the row is not a refusal")

    s.section("It deletes itself, six months after the stay ENDS")
    old = _stay("OLD", arrive_offset=-400, nights=14, party=1)
    conn = db()
    conn.execute(
        """INSERT INTO police_register (booking_id, surname, first_names, nationality,
           recorded_at) VALUES (?, 'Ancien', 'Jean', 'Swiss', ?)""",
        (old["id"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    kept = _stay("RECENT", arrive_offset=-20, nights=3, party=1)
    conn.execute(
        """INSERT INTO police_register (booking_id, surname, first_names, nationality,
           recorded_at) VALUES (?, 'Recent', 'Marie', 'Italian', ?)""",
        (kept["id"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    with m.app.test_request_context():
        purged = m.purge_police_register(conn, today)
    conn.commit()
    conn.close()
    s.check("the old one goes",
            _one("SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                 (old["id"],))["c"] == 0,
            detail=str(purged))
    s.check("the recent one stays",
            _one("SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                 (kept["id"],))["c"] == 1,
            detail="a register purged too eagerly cannot be produced when it is "
                   "asked for, which is the whole reason it is kept")
    s.check("and it says what it cleared", "police register" in str(purged).lower(),
            detail=str(purged))

    # Dated from the DEPARTURE. A fiche written on arrival for a long stay
    # would otherwise be deleted while the guest was still in the house.
    conn = db()
    edge = _stay("EDGE", arrive_offset=-(6 * 31 + 5), nights=20, party=1)
    conn.execute(
        """INSERT INTO police_register (booking_id, surname, first_names, nationality,
           recorded_at) VALUES (?, 'Edge', 'Case', 'German', ?)""",
        (edge["id"], (datetime.now(timezone.utc) - timedelta(days=6 * 31 + 5)).isoformat()))
    conn.commit()
    with m.app.test_request_context():
        m.purge_police_register(conn, today)
    conn.commit()
    conn.close()
    s.check("a long stay is measured from when it ended, not when it began",
            _one("SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                 (edge["id"],))["c"] == 1,
            detail="arrived over six months ago, left inside it — the arrival "
                   "date would have deleted this one early")

    s.section("The nightly job keeps the promise")
    # The notice can be checked against one function rather than hunted for
    # across the app, so the purge has to be IN that function.
    import inspect
    src = inspect.getsource(m.run_health_notes_purge_job)
    s.check("the retention job runs it", "purge_police_register" in src,
            detail="a purge nothing calls is a promise nothing keeps")

    s.section("And the privacy notice says so")
    page = anon.get("/privacy")
    body = page.get_data(as_text=True).lower()
    s.check("the notice opens", page.status_code == 200)
    # Two separate claims, checked separately. "police" alone appears in both
    # sections, so a single search for it stays green while either half is
    # gutted -- which is exactly what the control found.
    s.check("it says what is collected and why",
            "date and place of birth" in body and "home address" in body,
            detail="collecting a date of birth without saying so makes the "
                   "notice untrue the moment it ships")
    s.check("and separately, how long it is kept",
            "the police register" in body and "six months after the stay ends" in body,
            detail="the retention section is its own claim; a reader checking "
                   "how long you hold it will not find it in the collection list")
    s.check("and that nothing beyond the listed fields is taken",
            "passport" in body and "no copy of any" in body,
            detail="the notice names what is NOT collected, because that is the "
                   "part a guest cannot verify for themselves")

    s.section("Who may see the register")
    r = ec.get("/admin/register", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = anon.get("/admin/register", follow_redirects=False)
    s.check("nor a stranger", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")
    r = oc.get("/admin/register")
    s.check("the owner can", r.status_code == 200, detail=str(r.status_code))
    s.check("and it is on the page", "Fontaine" in r.get_data(as_text=True),
            detail="a register nobody can produce is not a register")

    r = ec.post(f"/admin/bookings/{stay['id']}/register",
                data={"surname": "Sneaky", "first_names": "X", "nationality": "Dutch"},
                follow_redirects=False)
    s.check("an employee cannot add to it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and nothing was added",
            _one("SELECT COUNT(*) AS c FROM police_register WHERE surname = 'Sneaky'")["c"] == 0)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
