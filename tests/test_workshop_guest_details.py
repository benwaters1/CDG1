"""A retreat guest changes their own details on their own page (owner, 27 November 2026).

"why isnt there an edit button on these" -- of the registration page, which showed who the
guest was travelling with, their dietary and medical notes and what they were celebrating,
and let them change none of it. A new allergy or a misspelt roommate meant writing in and
waiting for somebody to retype it. Now:

  - THE PAGE OFFERS IT, with what they said already in the boxes, until the retreat begins.
  - SAVED IS SAVED: the four go on the registration, an empty box takes a note off (NULL,
    not an empty string), and saving it unchanged changes nothing and tells nobody.
  - THE HOUSE IS TOLD what changed, by email and on the bell, the way the restaurant page
    tells it. The roommate and the occasion are quoted with what they were; a dietary or
    medical note is NAMED, NEVER QUOTED. The house keeps its own letters two years and a
    push goes through a browser company, while the privacy notice promises those notes go
    once the retreat is over. The audit line and the change log keep that they changed,
    not to what.
  - IT IS THE SAME SAVE AS THE STAFF EDIT (write_workshop_details), so what follows a
    change follows whoever made it: the occasion's task is raised for a new occasion and
    closed when the guest takes it off.
  - IT CLOSES when the retreat begins, and for a place that is not coming. The form is
    not drawn and a post is refused, the registration untouched.
"""
from datetime import timedelta

import _harness
from _harness import Suite, clients, db, ensure_room, flashes, forms_on, visible_text

m = _harness.m
TAG = "ZZGD"
FIELDS = ("requested_roommate", "dietary_notes", "medical_notes", "special_occasion")

# What the guest types. The health notes are words that appear nowhere else in the app,
# so finding them anywhere they should not be is unambiguous.
MEDICAL = "Zzgd carries an adrenaline pen for wasp stings"
DIETARY = "Zzgd cannot eat sesame, at all"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject, body))
        return True


def _row(conn, reg_id):
    return conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (reg_id,)).fetchone()


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM workshop_bookings WHERE reference_code LIKE ?", (f"{TAG}-%",))]
    for reg_id in ids:
        conn.execute("DELETE FROM tasks WHERE id IN (SELECT task_id FROM occasion_preps "
                     "WHERE workshop_booking_id = ?)", (reg_id,))
        conn.execute("DELETE FROM occasion_preps WHERE workshop_booking_id = ?", (reg_id,))
        conn.execute("DELETE FROM change_log WHERE table_name = 'workshop_bookings' AND row_id = ?",
                     (reg_id,))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (f"{TAG}-%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (f"{TAG}%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (f"{TAG}-%",))
    conn.commit()


def _place(conn, wid, start, ref, status="confirmed", room_id=None, **said):
    now = _harness.datetime_now()
    sid = conn.execute(
        "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
        "VALUES (?, ?, ?, 8, ?)",
        (wid, start.isoformat(), (start + timedelta(days=5)).isoformat(), now)).lastrowid
    reg_id = conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, party_size, status, total_price, occupancy_type, assigned_room_id,
               requested_roommate, dietary_notes, medical_notes, special_occasion, created_at)
           VALUES (?, ?, ?, ?, ?, 2, ?, 2000, 'double', ?, ?, ?, ?, ?, ?)""",
        (sid, ref, f"{ref.lower()}-token", f"Belinda {TAG}", f"{ref.lower()}@example.invalid",
         status, room_id, said.get("requested_roommate"), said.get("dietary_notes"),
         said.get("medical_notes"), said.get("special_occasion"), now)).lastrowid
    conn.commit()
    return reg_id, f"{ref.lower()}-token"


def _details_form(page):
    for f in forms_on(page):
        names = {x["name"]: x for x in f["fields"]}
        if names.get("action", {}).get("value") == "details":
            return names
    return None


def _post(client, token, **typed):
    data = {"action": "details"}
    data.update({k: typed.get(k, "") for k in FIELDS})
    return client.post(f"/workshops/manage/{token}", data=data, follow_redirects=True)


def run():
    s = Suite("A retreat guest changes their own details (owner, 27 November)")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    mail = Recorder()
    real_send = m.send_email
    m.send_email = mail
    try:
        _run(s, oc, owner, conn, mail)
    finally:
        m.send_email = real_send
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, owner, conn, mail):
    today = m.house_today()
    wid = conn.execute(
        "INSERT INTO workshops (title, description, price_per_person, active, created_at) "
        "VALUES (?, '', 1000, 1, ?)", (f"{TAG} Painting Week", _harness.datetime_now())).lastrowid
    conn.commit()
    room = ensure_room()
    # Two days ahead: still to begin, and inside the time the house prepares an occasion.
    start = today + timedelta(days=2)
    s.check("the house prepares an occasion at least two days ahead, so this place is inside it",
            m.occasion_prep_days(conn) >= 2, detail=str(m.occasion_prep_days(conn)))
    reg_id, token = _place(conn, wid, start, f"{TAG}-A", room_id=room["id"],
                           requested_roommate="Helen Wilkes", special_occasion="Life")
    guest = m.app.test_client()

    s.section("The page offers it, with what they said in the boxes")
    r = guest.get(f"/workshops/manage/{token}")
    page = r.get_data(as_text=True)
    form = _details_form(page)
    s.check("the page opens", r.status_code == 200, r)
    s.check("with a form that changes their details", form is not None)
    if not form:
        return
    s.check("asking for all four",
            all(k in form for k in FIELDS), detail=str(sorted(form)))
    s.check("with the roommate and the occasion already in their boxes",
            form["requested_roommate"]["value"] == "Helen Wilkes"
            and form["special_occasion"]["value"] == "Life",
            detail=str({k: form[k]["value"] for k in FIELDS}))
    s.check("behind a link saying what it does", "Change these details" in visible_text(page))

    s.section("Saved is saved, and the house is told")
    mail.calls.clear()
    before_notes = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    r = _post(guest, token, requested_roommate="Helen Wilks", dietary_notes=DIETARY,
              medical_notes=MEDICAL, special_occasion="Our wedding anniversary")
    row = _row(conn, reg_id)
    s.check("it says so", any("Saved" in f for f in flashes(r)), r)
    s.check("and all four are on the registration",
            (row["requested_roommate"], row["dietary_notes"], row["medical_notes"],
             row["special_occasion"]) == ("Helen Wilks", DIETARY, MEDICAL, "Our wedding anniversary"),
            detail=str({k: row[k] for k in FIELDS}))
    s.check("which the page now shows", "Our wedding anniversary" in visible_text(r.get_data(as_text=True)))

    house = m.house_address_for(conn, "workshops")
    to_house = [c for c in mail.calls if c[0] == house]
    s.check("one email goes to the house's retreat inbox", len(to_house) == 1,
            detail=str([(c[0], c[1]) for c in mail.calls]))
    body = to_house[0][2] if to_house else ""
    s.check("naming the registration", to_house and f"{TAG}-A" in to_house[0][1],
            detail=to_house[0][1] if to_house else "")
    s.check("quoting the roommate with what it was",
            'Travelling with: "Helen Wilks" (was "Helen Wilkes")' in body, detail=body[:400])
    s.check("and the occasion", 'Celebrating: "Our wedding anniversary" (was "Life")' in body,
            detail=body[:400])
    s.check("saying the room is already settled, so the rooming wants a look",
            "already settled" in body, detail=body[:400])
    s.check("naming the health notes as added",
            "Dietary notes: added" in body and "Medical notes: added" in body, detail=body[:400])
    s.check("and never quoting them", MEDICAL not in body and DIETARY not in body
            and "adrenaline" not in body and "sesame" not in body, detail=body[:400])
    s.check("nothing goes to the guest", all(c[0] == house for c in mail.calls),
            detail=str([c[0] for c in mail.calls]))

    bell = conn.execute("SELECT * FROM notifications WHERE user_id = ? AND title LIKE ? "
                        "ORDER BY id DESC LIMIT 1", (owner["id"], f"%{TAG}%")).fetchone()
    s.check("the owner's bell rings", bell is not None and
            conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] > before_notes)
    words = f"{bell['title']} {bell['body']}" if bell else ""
    s.check("saying what changed, without the notes themselves",
            bell and "Medical notes" in words and MEDICAL not in words and DIETARY not in words,
            detail=words)

    audit = conn.execute("SELECT * FROM audit_log WHERE target = ? AND action = ? "
                         "ORDER BY id DESC LIMIT 1",
                         (f"{TAG}-A", "workshop_details_changed_by_guest")).fetchone()
    s.check("the audit says the guest changed them",
            audit is not None and "medical notes" in (audit["details"] or ""),
            detail=str(dict(audit)) if audit else "no line")
    s.check("and not what they now say",
            audit and MEDICAL not in (audit["details"] or "") and DIETARY not in (audit["details"] or ""))
    logged = " ".join(str(v) for r_ in conn.execute(
        "SELECT * FROM change_log WHERE table_name = 'workshop_bookings' AND row_id = ?",
        (reg_id,)) for v in dict(r_).values())
    s.check("the change log has the roommate's change", "Helen Wilks" in logged, detail=logged[:300])
    s.check("and never the health notes", MEDICAL not in logged and DIETARY not in logged)

    s.section("The occasion's task follows, as it does from the staff edit")
    task = conn.execute("SELECT tasks.* FROM tasks JOIN occasion_preps p ON p.task_id = tasks.id "
                        "WHERE p.workshop_booking_id = ?", (reg_id,)).fetchone()
    s.check("a task is raised for the occasion they told us about",
            task is not None and task["status"] != "done"
            and "anniversary" in (task["title"] + " " + (task["notes"] or "")).lower(),
            detail=str(dict(task)) if task else "no task")

    s.section("Unchanged changes nothing; emptied takes it off")
    mail.calls.clear()
    r = _post(guest, token, requested_roommate="Helen Wilks", dietary_notes=DIETARY,
              medical_notes=MEDICAL, special_occasion="Our wedding anniversary")
    s.check("saved as it was, it says nothing had changed",
            any("Nothing had changed" in f for f in flashes(r)), r)
    s.check("and tells nobody", not mail.calls, detail=str([c[1] for c in mail.calls]))

    r = _post(guest, token, requested_roommate="Helen Wilks", dietary_notes=DIETARY,
              medical_notes="", special_occasion="")
    row = _row(conn, reg_id)
    s.check("an emptied box takes the note off, as nothing rather than an empty string",
            row["medical_notes"] is None and row["special_occasion"] is None,
            detail=repr((row["medical_notes"], row["special_occasion"])))
    body = mail.calls[-1][2] if mail.calls else ""
    s.check("the house hears the medical notes were taken off",
            "Medical notes: taken off" in body, detail=body[:300])
    s.check("and the occasion, with what it was",
            'Celebrating: taken off (was "Our wedding anniversary")' in body, detail=body[:300])
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone() if task else None
    s.check("the occasion's task closes", task is not None and task["status"] == "done",
            detail=str(dict(task)) if task else "no task")

    s.section("It closes once the retreat begins")
    begun_id, begun = _place(conn, wid, today, f"{TAG}-B", medical_notes=MEDICAL)
    page = guest.get(f"/workshops/manage/{begun}").get_data(as_text=True)
    s.check("on its first day the form is not drawn", _details_form(page) is None)
    mail.calls.clear()
    r = _post(guest, begun, medical_notes="Something new")
    s.check("and a post is refused, saying why",
            any("can no longer be changed here" in f for f in flashes(r)), r)
    s.check("the registration untouched", _row(conn, begun_id)["medical_notes"] == MEDICAL)
    s.check("and nobody told", not mail.calls)

    s.section("And for a place that is not coming")
    gone_id, gone = _place(conn, wid, today + timedelta(days=20), f"{TAG}-C", status="cancelled",
                           special_occasion="Retirement")
    page = guest.get(f"/workshops/manage/{gone}").get_data(as_text=True)
    s.check("a cancelled place draws no form", _details_form(page) is None)
    r = _post(guest, gone, special_occasion="Something else")
    s.check("and a post is refused", any("can no longer be changed here" in f for f in flashes(r)), r)
    s.check("the registration untouched", _row(conn, gone_id)["special_occasion"] == "Retirement")

    s.section("One save for the guest and the staff edit")
    import inspect
    # Only the details action: the page's cancel follows the occasion for its own reason.
    guest_src = inspect.getsource(m.workshop_manage)
    start_at = guest_src.find('== "details"')
    guest_src = guest_src[start_at:guest_src.find('if request.method == "POST"', start_at + 1)]
    staff_src = inspect.getsource(m.edit_workshop_registration)
    s.check("the guest's page saves through write_workshop_details",
            start_at > 0 and "write_workshop_details(" in guest_src, detail=guest_src[:200])
    s.check("and so does the staff edit", "write_workshop_details(" in staff_src)
    s.check("which is where the occasion's task follows from, not either route",
            "follow_occasion_of(" in inspect.getsource(m.write_workshop_details)
            and "follow_occasion_of(" not in guest_src and "follow_occasion_of(" not in staff_src)
    s.check("the guest's change is read as the guest's in the audit",
            "workshop_details_changed_by_guest" in m.GUEST_AUDIT_ACTIONS)


if __name__ == "__main__":
    print(run().report())
