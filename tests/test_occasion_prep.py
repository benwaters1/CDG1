"""A guest's occasion, prepared for rather than only recorded.

Guests write what they are celebrating when they book, and staff can change it
-- and until 22 October 2026 nothing acted on it. The owner asked for it to be
prepared for: a few days before they arrive (the owner says how many) the stay
or atelier place gets a task, "Prepare for the Martins' anniversary — arriving
Fri 24 Oct, Chambre Bleue", with the guest's own words and a checklist the
owner keeps. What this file holds to:

  IT APPEARS N DAYS BEFORE, AND NOT SOONER. N is live config, not a constant:
  changed on the page, the stays it newly reaches are raised there and then.

  IT CARRIES THEIR WORDS, and only in the notes. The title is what the house
  calendar sends to everybody's Outlook.

  IT CLOSES ITSELF only when there is nothing left to prepare for: the stay
  cancelled, or the occasion taken off. Ticked off by staff, it stays ticked.

  A CHANGED OCCASION REWRITES IT. One task per stay, never a second -- and a
  task already ticked off for the old words comes back, because a card that
  says happy anniversary is not ready for a birthday.

  THE CRÉMANT IS A BUTTON. Raising the task, opening the page and ticking the
  checklist put nothing on the guest's bill. Pressing it puts one on, through
  the same path the stay's own page uses, on the day chosen.

  IT SHOWS AT THE DOOR: on the arrivals sheet and the front desk for that day.

Stays edited through the stay's own form are put where nothing holds the house
(free_window): the form checks the nights again on every save, and an atelier
on them would refuse the edit for a reason that has nothing to do with this.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, keep_rows
import _harness

m = _harness.m
TAG = "ZZOC"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE origin = 'occasion' AND (booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?) OR room_note LIKE ?)",
                 (TAG + "%", "%" + TAG + "%"))
    conn.execute("DELETE FROM occasion_preps WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM occasion_preps WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note = ?", (TAG,))
    conn.commit()
    conn.close()


def _booking(ref, *, room_id, arrival, nights=2, occasion=None, **extra):
    conn = db()
    cols = dict(room_id=room_id, reference_code=f"{TAG}-{ref}",
                manage_token=f"tok{TAG}{ref}".lower(), guest_name=f"{TAG} {ref} Martin",
                guest_email=f"zzoc.{ref}@example.invalid".lower(), guest_phone="",
                arrival_date=arrival.isoformat(),
                departure_date=(arrival + timedelta(days=nights)).isoformat(),
                party_size=2, status="confirmed", payment_status="unpaid",
                total_price=400, amount_paid=0, special_occasion=occasion,
                created_at=datetime.now(timezone.utc).isoformat())
    cols.update(extra)
    conn.execute("INSERT INTO bookings (%s) VALUES (%s)"
                 % (", ".join(cols), ", ".join("?" * len(cols))), list(cols.values()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _one(sql, *params):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _all(sql, *params):
    conn = db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _tasks(booking_id):
    return _all("SELECT * FROM tasks WHERE origin = 'occasion' AND booking_id = ?", booking_id)


def _prep(booking_id):
    return _one("SELECT * FROM occasion_preps WHERE booking_id = ?", booking_id)


def _sweep(today=None):
    conn = db()
    try:
        return m.generate_occasion_tasks(conn, today)
    finally:
        conn.close()


def _days():
    conn = db()
    try:
        return m.occasion_prep_days(conn)
    finally:
        conn.close()


def _offered():
    conn = db()
    try:
        e = m.occasion_extra(conn)
        return e["id"] if e else None
    finally:
        conn.close()


def _edit_form(bid, **changes):
    """The stay's edit form as it stands, with `changes` made."""
    b = _one("SELECT * FROM bookings WHERE id = ?", bid)
    form = {"arrival_date": b["arrival_date"], "departure_date": b["departure_date"],
            "party_size": str(b["party_size"]), "guests_under_18": "0",
            "guest_phone": "", "special_requests": "", "guest_email": b["guest_email"],
            "estimated_arrival_time": b["estimated_arrival_time"] or "",
            "special_occasion": b["special_occasion"] or ""}
    form.update(changes)
    return form


def _set(key, value):
    conn = db()
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()
    conn.close()


def _card(page, needle, until="expense-card"):
    """The piece of a page from `needle` to the next card."""
    at = page.find(needle)
    if at < 0:
        return ""
    end = page.find(until, at + len(needle))
    return page[at:end if end > 0 else len(page)]


CHECKLIST = "Flowers in the room\nA card in the room\nThe room dressed"


def run():
    s = Suite("Occasions prepared for")
    _cleanup()
    oc, ec, _owner, emp = clients()
    room = _harness.ensure_room()
    today = m.house_today()
    keep = db()
    put_back = keep_rows(keep, "app_settings", "key LIKE 'occasion_%'")
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, *a, **k: sent.append((to, subject)) or True
    try:
        _run(s, oc, ec, emp, room, today)
    finally:
        m.send_email = real_send
        put_back()
        keep.close()
        _cleanup()
    return s


def _run(s, oc, ec, emp, room, today):
    _set("occasion_prep_days", "3")
    _set("occasion_checklist", CHECKLIST)
    _set("occasion_extra_id", "")
    rooms = _all("SELECT id, name FROM rooms WHERE active = 1 ORDER BY sort_order, id")
    other = next((r for r in rooms if r["id"] != room["id"]), room)

    s.section("The task appears N days before arrival, and not sooner")
    # Whoever is first on the rota on the arrival day gets it.
    s_day = today + timedelta(days=3)
    conn = db()
    conn.execute("INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, "
                 "created_at) VALUES (?, ?, '00:05', '16:00', ?, ?)",
                 (emp["id"], s_day.isoformat(), TAG, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    st = _booking("S", room_id=room["id"], arrival=s_day,
                  occasion="Our 25th   wedding anniversary!")
    four = _booking("T", room_id=other["id"], arrival=today + timedelta(days=4),
                    occasion="Her birthday")
    plain = _booking("C", room_id=room["id"], arrival=today + timedelta(days=2), nights=1)
    pending = _booking("D", room_id=other["id"], arrival=today + timedelta(days=2), nights=1,
                       occasion="Honeymoon", status="pending")
    got = _sweep(today)
    s.check("a stay three days out with an occasion gets a task", len(_tasks(st["id"])) == 1,
            detail=f"{got}")
    s.check("one four days out does not, yet", not _tasks(four["id"]))
    s.check("a stay with no occasion gets none", not _tasks(plain["id"]))
    s.check("nor does one not yet confirmed", not _tasks(pending["id"]))
    task = (_tasks(st["id"]) or [None])[0]
    if not task:
        return
    day = m.occasion_day_words(st["arrival_date"])
    s.check("titled for whose occasion, what, when and where",
            task["title"] == f"Prepare for the Martins' anniversary — arriving {day}, {room['name']}",
            detail=task["title"])
    s.check("with the guest's own words in the notes",
            "“Our 25th wedding anniversary!”" in (task["notes"] or ""), detail=task["notes"])
    s.check("and not in the title, which goes to everybody's Outlook",
            "25th" not in task["title"])
    s.check("due the day they arrive", task["due_date"] == st["arrival_date"])
    s.check("for whoever is first on the rota that day", task["assigned_to_user_id"] == emp["id"])
    items = _all("SELECT label FROM occasion_prep_items WHERE prep_id = ? ORDER BY position",
                 _prep(st["id"])["id"])
    s.check("with the owner's checklist on it, in order",
            [i["label"] for i in items] == CHECKLIST.split("\n"),
            detail=f"{[tuple(i) for i in items]}")
    again = _sweep(today)
    s.check("a second sweep raises nothing more",
            len(_tasks(st["id"])) == 1 and not again["made"], detail=f"{again}")

    s.section("On the calendar and on the staff list, from the day it is raised")
    cal = oc.get(f"/calendar?view=week&date={st['arrival_date']}").get_data(as_text=True)
    s.check("on the calendar", "Prepare for the Martins&#39; anniversary" in cal
            or "Prepare for the Martins' anniversary" in cal)
    home = ec.get("/").get_data(as_text=True)
    s.check("on the staff member's list three days ahead", "anniversary — arriving" in home)
    s.check("opening its checklist", f"/occasions#task-{task['id']}" in home)
    s.check("and on their Today page",
            f"#task-{task['id']}" in ec.get("/today").get_data(as_text=True))

    s.section("N is the owner's, and a longer notice reaches stays at once")
    r = oc.post("/occasions/settings", data={"days": "4", "checklist": CHECKLIST, "extra_id": ""},
                follow_redirects=True)
    s.check("saved", r.status_code == 200 and any("4 days before" in f for f in flashes(r)),
            r, detail=f"{flashes(r)}")
    s.check("and the stay four days out is raised there and then", len(_tasks(four["id"])) == 1)
    s.check("said so", any("1 raised now" in f for f in flashes(r)), r)
    r = oc.post("/occasions/settings", data={"days": "99", "checklist": "", "extra_id": ""},
                follow_redirects=True)
    s.check("a notice past thirty days is refused",
            _days() == 4 and any("from 0 to" in f for f in flashes(r)), r)

    s.section("A changed occasion rewrites the task, and never adds a second")
    a_day = _harness.free_window(room["id"], 3, after_days=30, clear_of_ateliers=True)
    a = _booking("A", room_id=room["id"], arrival=a_day, occasion="Our anniversary")
    _sweep(a_day - timedelta(days=4))
    s.check("raised four days before, for a stay a month away", len(_tasks(a["id"])) == 1)
    task = (_tasks(a["id"]) or [None])[0]
    if not task:
        return
    r = oc.post(f"/admin/bookings/{a['id']}/edit",
                data=_edit_form(a["id"], special_occasion="Claire's 50th birthday"),
                follow_redirects=True)
    rows = _tasks(a["id"])
    s.check("still one task", len(rows) == 1, r, detail=f"{len(rows)}")
    s.check("the same task", rows and rows[0]["id"] == task["id"])
    s.check("now titled a birthday", rows and "Martins' birthday" in rows[0]["title"],
            detail=rows[0]["title"] if rows else "")
    s.check("with the new words", rows and "“Claire's 50th birthday”" in rows[0]["notes"])
    s.check("on the stay's history, by name only",
            _one("SELECT 1 FROM audit_log WHERE action = 'booking_edited' AND target = ? "
                 "AND details LIKE '%occasion changed%'", a["reference_code"]) is not None)
    oc.post(f"/occasions/{_prep(a['id'])['id']}/done")
    s.check("ticked off by staff", _tasks(a["id"])[0]["status"] == "done")
    _sweep(a_day - timedelta(days=4))
    s.check("stays ticked off through the next sweep", _tasks(a["id"])[0]["status"] == "done")
    oc.post(f"/admin/bookings/{a['id']}/edit",
            data=_edit_form(a["id"], special_occasion="A surprise proposal"))
    rows = _tasks(a["id"])
    s.check("but new words put it back on the list, still one",
            len(rows) == 1 and rows[0]["status"] == "open" and "engagement" in rows[0]["title"],
            detail=f"{[(t['status'], t['title']) for t in rows]}")

    s.section("The checklist, ticked by whoever is on shift")
    prep = _prep(a["id"])
    items = _all("SELECT * FROM occasion_prep_items WHERE prep_id = ? ORDER BY position",
                 prep["id"])
    r = ec.post(f"/occasions/items/{items[0]['id']}/tick", follow_redirects=True)
    one = _one("SELECT * FROM occasion_prep_items WHERE id = ?", items[0]["id"])
    s.check("a member of staff ticks one", r.status_code == 200 and one["done_at"], r)
    s.check("with their name against it", one["done_by_user_id"] == emp["id"])
    r = oc.post(f"/occasions/{prep['id']}/items/add", data={"label": "  Cake  from the  village "},
                follow_redirects=True)
    added = _one("SELECT * FROM occasion_prep_items WHERE prep_id = ? ORDER BY position DESC "
                 "LIMIT 1", prep["id"])
    s.check("the owner adds one for this occasion", added["label"] == "Cake from the village",
            r, detail=added["label"])
    r = oc.post(f"/occasions/items/{added['id']}/remove", follow_redirects=True)
    s.check("and takes it off again", r.status_code == 200 and not _one(
        "SELECT 1 FROM occasion_prep_items WHERE id = ?", added["id"]), r)
    for i in items[1:]:
        ec.post(f"/occasions/items/{i['id']}/tick")
    s.check("the last one ticked ticks the task off", _tasks(a["id"])[0]["status"] == "done")
    r = ec.post(f"/occasions/items/{items[1]['id']}/tick", data={"undo": "1"},
                follow_redirects=True)
    s.check("one put back puts the task back", _tasks(a["id"])[0]["status"] == "open", r)

    s.section("The crémant is a button: on the bill only when pressed")
    conn = db()
    conn.execute("INSERT INTO extras (name, price, active, sort_order, ask_when) "
                 "VALUES (?, 80, 1, 0, 1)", (TAG + " Crémant & charcuterie",))
    conn.commit()
    cremant = conn.execute("SELECT * FROM extras WHERE name = ?",
                           (TAG + " Crémant & charcuterie",)).fetchone()
    conn.close()
    r = oc.post("/occasions/settings", data={"days": "4", "checklist": CHECKLIST,
                                             "extra_id": str(cremant["id"])},
                follow_redirects=True)
    s.check("the owner chooses which extra is offered", _offered() == cremant["id"], r)

    def lines():
        return _all("SELECT * FROM booking_extras WHERE booking_id = ? AND category = 'room'",
                    a["id"])

    page = oc.get("/occasions").get_data(as_text=True)
    s.check("offered beside the stay", f"/occasions/stay/{a['id']}/extra" in page)
    s.check("and nothing is on the bill for raising, opening or ticking", not lines(),
            detail=f"{len(lines())} line(s)")
    staff_page = ec.get("/occasions").get_data(as_text=True)
    s.check("the shift sees the occasions", "A surprise proposal" in staff_page)
    s.check("but not the button or its price",
            f"/occasions/stay/{a['id']}/extra" not in staff_page and "€80" not in staff_page)
    r = oc.post(f"/occasions/stay/{a['id']}/extra",
                data={"extra_id": "999999", "extra_when": "arrival"}, follow_redirects=True)
    s.check("a press for an extra no longer offered adds nothing", not lines(), r)
    r = oc.post(f"/occasions/stay/{a['id']}/extra",
                data={"extra_id": str(cremant["id"]), "extra_when": "arrival"},
                follow_redirects=True)
    got = lines()
    s.check("pressed, one goes on the stay", len(got) == 1, r, detail=f"{flashes(r)}")
    if got:
        s.check("the one offered", got[0]["extra_id"] == cremant["id"])
        s.check("on the day chosen", got[0]["scheduled_for"] == a["arrival_date"])
        s.check("saying what it is for", "On arrival" in (got[0]["notes"] or "")
                and "A surprise proposal" in (got[0]["notes"] or ""), detail=got[0]["notes"])
        s.check("in the stay's history", _one(
            "SELECT 1 FROM audit_log WHERE action = 'booking_extra_added' AND target = ?",
            a["reference_code"]) is not None)
    page = oc.get("/occasions").get_data(as_text=True)
    s.check("and the button gives way to saying so",
            f"/occasions/stay/{a['id']}/extra" not in page and "is on their stay" in page)

    s.section("The occasion at the door: arrivals sheet and front desk")
    h = _booking("H", room_id=room["id"], arrival=today, nights=1, occasion="Honeymoon",
                 guest_name=f"{TAG} H Dupont")
    _booking("Q", room_id=other["id"], arrival=today, nights=1, guest_name=f"{TAG} Q Quiet")
    _sweep(today)
    sheet = oc.get("/admin/arrivals").get_data(as_text=True)
    card = _card(sheet, f"{TAG} H Dupont")
    s.check("the arrivals sheet says what they are celebrating",
            "Celebrating" in card and "Honeymoon" in card, detail=card[:200])
    h_task = (_tasks(h["id"]) or [{"id": -1}])[0]
    s.check("and how far its preparation has got",
            "0 of 3 done" in card and f"#task-{h_task['id']}" in card)
    s.check("a guest with no occasion has no such line",
            "Celebrating" not in _card(sheet, f"{TAG} Q Quiet"))
    desk = oc.get("/admin/desk").get_data(as_text=True)
    # The table of who arrives, not the room tiles above it, which name them too.
    desk = desk[desk.find('id="arriving"'):]
    row = _card(desk, f"{TAG} H Dupont", until="</tr>")
    s.check("so does the front desk", "Celebrating:" in row and "Honeymoon" in row,
            detail=row[:200])

    s.section("It closes itself when there is nothing left to prepare for")
    r = oc.post(f"/admin/bookings/{h['id']}/cancel", follow_redirects=True)
    rows = _tasks(h["id"])
    s.check("cancelled from the stay's page, closed at once",
            rows and rows[0]["status"] == "done", r)
    s.check("saying why", rows and (_prep(h["id"])["closed_reason"] or "") == "cancelled")
    b_day = _harness.free_window(other["id"], 3, after_days=(a_day - today).days + 10,
                                 clear_of_ateliers=True)
    b = _booking("B", room_id=other["id"], arrival=b_day, occasion="Her birthday")
    _sweep(b_day - timedelta(days=3))
    oc.post(f"/admin/bookings/{b['id']}/edit", data=_edit_form(b["id"], special_occasion=""))
    rows = _tasks(b["id"])
    s.check("an occasion taken off closes it", rows and rows[0]["status"] == "done"
            and _prep(b["id"])["closed_reason"] == "cleared")
    oc.post(f"/admin/bookings/{b['id']}/edit",
            data=_edit_form(b["id"], special_occasion="Her birthday after all"))
    rows = _tasks(b["id"])
    s.check("put back, the same task reopens", len(rows) == 1 and rows[0]["status"] == "open"
            and not _prep(b["id"])["closed_reason"])
    # Cancelled by a path that knows nothing of occasions: the sweep finds it.
    conn = db()
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (b["id"],))
    conn.commit()
    conn.close()
    _sweep(today)
    s.check("cancelled any other way, the sweep closes it",
            _tasks(b["id"])[0]["status"] == "done")
    s.check("and the extra already sold is not touched by any of it", len(lines()) == 1)

    s.section("An atelier place is prepared for the same way")
    conn = db()
    ws = conn.execute("SELECT id, title FROM workshops ORDER BY id LIMIT 1").fetchone()
    if not ws:
        conn.close()
        s.check("there is an atelier to sit in", False)
        return
    start = today + timedelta(days=2)
    cur = conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, "
                       "notes, created_at) VALUES (?, ?, ?, 8, ?, ?)",
                       (ws["id"], start.isoformat(), (start + timedelta(days=3)).isoformat(), TAG,
                        datetime.now(timezone.utc).isoformat()))
    cur = conn.execute("INSERT INTO workshop_bookings (session_id, reference_code, manage_token, "
                       "guest_name, guest_email, party_size, status, special_occasion, created_at) "
                       "VALUES (?, ?, ?, ?, ?, 1, 'confirmed', ?, ?)",
                       (cur.lastrowid, f"{TAG}-W", f"tok{TAG}w".lower(), f"{TAG} W Dubois",
                        "zzoc.w@example.invalid", "My retirement",
                        datetime.now(timezone.utc).isoformat()))
    place = cur.lastrowid
    conn.commit()
    conn.close()
    _sweep(today)
    t = _one("SELECT tasks.* FROM tasks JOIN occasion_preps p ON p.task_id = tasks.id "
             "WHERE p.workshop_booking_id = ?", place)
    s.check("raised for the place", t is not None)
    s.check("titled for the atelier",
            t and t["title"] == (f"Prepare for {TAG} W Dubois's retirement — arriving "
                                 f"{m.occasion_day_words(start.isoformat())} for {ws['title']}"),
            detail=t["title"] if t else "")
    r = oc.post(f"/admin/workshops/registrations/{place}/edit",
                data={"guest_name": f"{TAG} W Dubois", "guest_email": "zzoc.w@example.invalid",
                      "party_size": "1", "special_occasion": ""}, follow_redirects=True)
    t = _one("SELECT * FROM tasks WHERE id = ?", t["id"]) if t else None
    s.check("its occasion taken off closes it", t and t["status"] == "done", r)

    s.section("The page")
    page = oc.get("/occasions").get_data(as_text=True)
    s.check("opens for the owner", "<h1>Occasions</h1>" in page)
    s.check("with how they are prepared", 'id="settings"' in page)
    s.check("and closed occasions are not listed", "Her birthday after all" not in page)
    r = ec.get("/occasions")
    s.check("opens for the shift", r.status_code == 200)
    s.check("without the owner's settings", 'id="settings"' not in r.get_data(as_text=True))


if __name__ == "__main__":
    print(run().report())
