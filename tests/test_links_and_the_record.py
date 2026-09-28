"""The record itself: a leaked link replaced, every field shown, a caution read
at every door, a profile for everybody who books, a delete that cannot lose a
history, and a merge that keeps everything.

What there was: the privacy notice tells guests to treat their booking link
like a password and, if it goes astray, to tell us "and we will issue a new
one" -- and nothing could issue one. The record left out the language
somebody reads in, their access needs and when they usually arrive, and a
profile merged into another said nothing of it. A table and an event never
read a standing instruction not to accept somebody. A table, an atelier or an
event made no profile, so somebody who only ever dined had no record and no
link of their own. Deleting a profile deleted whatever it held, with nothing
on the audit trail, while the page said "their bookings are kept". And a
merge dropped the access needs, the language, the answer about photographs and
the VIP flag.

What this holds:

  - A new link for a stay, an atelier place, a table or an event: the old one
    stops working, the new one works, the guest is sent it, the letter is
    neither queued nor filed, it is on the booking's history, and the page
    that shows it is never cached. The same for somebody's own link. A
    colleague cannot; a letter that cannot go says so.
  - The record shows the language, the access needs and the usual arrival, and
    a merged-away profile says where it went.
  - A standing instruction stops a table and an event being confirmed.
  - Confirming a table, an atelier or an event makes a profile where there is
    none, and never a second.
  - A profile that holds anything cannot be deleted, and says what it holds;
    an empty one can, and that is on the audit trail.
  - A merge keeps the access needs, the language, photographs and VIP, and the
    survivor's history says what came across.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZLR"
WHO = f"{TAG.lower()}@example.invalid"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        for table in ("guest_messages", "guest_profile_changes", "consent_events",
                      "guest_notes", "guest_contacts"):
            conn.execute(f"DELETE FROM {table} WHERE guest_id IN "
                         "(SELECT id FROM guests WHERE name LIKE ?)", like)
        conn.execute("DELETE FROM guest_messages WHERE LOWER(to_address) LIKE ?",
                     (TAG.lower() + "%",))
        conn.execute("DELETE FROM email_outbox WHERE LOWER(to_address) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", like)
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", like)
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", like)
        # This suite's lines: its bookings' references, and its profiles' ids.
        conn.execute("DELETE FROM audit_log WHERE target LIKE ? OR target IN "
                     "(SELECT 'guest ' || id FROM guests WHERE name LIKE ?)",
                     (f"% {TAG}%", TAG + "%"))
        conn.execute("UPDATE guests SET merged_into_id = NULL WHERE name LIKE ?", like)
        conn.execute("DELETE FROM guests WHERE name LIKE ? OR LOWER(email) LIKE ?",
                     (TAG + "%", TAG.lower() + "%"))
        conn.commit()
    finally:
        conn.close()


def _one(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _guest(name, email, **fields):
    conn = db()
    cols = ["name", "email", "created_at"] + list(fields)
    conn.execute(f"INSERT INTO guests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 (f"{TAG} {name}", email, _harness.datetime_now(), *fields.values()))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def _lines(kind, record_id):
    conn = db()
    try:
        return [e["action"] for e in m.history_for(conn, kind, record_id)["entries"]]
    finally:
        conn.close()


def _capture(sent):
    """A stand-in for sending that also notes whether the letter was to be kept."""
    return lambda to, subject, body, *a, **k: sent.append(
        (to, subject, body, k.get("keep", True))) or True


def run():
    s = Suite("New links and the whole record")
    oc, ec, owner, _emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = _capture(sent)
    try:
        _run(s, oc, ec, owner, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, ec, owner, sent):
    now = _harness.datetime_now()
    gid = _guest("Guest", WHO, phone="+33 6 12 12 12 12")
    room = _harness.ensure_room()
    arrive = house_today() + timedelta(days=700)
    conn = db()
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at, linked_guest_id) VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?, ?)""",
                 (room["id"], f"{TAG}S", f"toks{TAG}".lower(), f"{TAG} Guest", WHO,
                  arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(), now, gid))
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                    active, sort_order, created_at, deposit_percent)
                    VALUES (?, '', 300, 10, 1, 96, ?, 10)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=90)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
                    notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=2)).isoformat(), f"{TAG} S", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} S",)).fetchone()["id"]
    places = {}
    for ref, email in (("W", WHO), ("W2", f"{TAG.lower()}.maker@example.invalid")):
        conn.execute("""INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                        party_size, status, reference_code, manage_token, created_at, total_price)
                        VALUES (?, ?, ?, 1, 'pending', ?, ?, ?, 300)""",
                     (sid, f"{TAG} Maker", email, f"{TAG}{ref}", f"tok{ref}{TAG}".lower(), now))
        places[ref] = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                                   (f"{TAG}{ref}",)).fetchone()["id"]
    tables = {}
    for ref, email in (("T", WHO), ("T2", f"{TAG.lower()}.diner@example.invalid"),
                       ("T3", f"{TAG.lower()}.diner@example.invalid")):
        conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                        guest_email, dinner_date, party_size, status, total_price, payment_status,
                        created_at) VALUES (?, ?, ?, ?, ?, 2, 'pending', 0, 'unpaid', ?)""",
                     (f"{TAG}{ref}", f"tok{ref}{TAG}".lower(), f"{TAG} Diner", email,
                      (house_today() + timedelta(days=15)).isoformat(), now))
        tables[ref] = conn.execute("SELECT id FROM restaurant_bookings WHERE reference_code = ?",
                                   (f"{TAG}{ref}",)).fetchone()["id"]
    events = {}
    for ref, email in (("E", WHO), ("E2", f"{TAG.lower()}.couple@example.invalid")):
        conn.execute("""INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                        contact_name, contact_email, contact_phone, preferred_date, guest_count,
                        message, status, quoted_price, amount_paid, created_at)
                        VALUES (?, ?, 'wedding', ?, ?, '', ?, 40, 'ZZ', 'contacted', 9000, 0, ?)""",
                     (f"{TAG}{ref}", f"tok{ref}{TAG}".lower(), f"{TAG} Couple", email,
                      (house_today() + timedelta(days=300)).isoformat(), now))
        events[ref] = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                                   (f"{TAG}{ref}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    bid = _one("SELECT id FROM bookings WHERE reference_code = ?", f"{TAG}S")["id"]

    s.section("A new link, when the old one has gone astray")
    guest = m.app.test_client()
    for category, table, kind, record_id, page in (
            ("room", "bookings", "booking", bid, "/book/manage/"),
            ("workshop", "workshop_bookings", "workshop_booking", places["W"], "/workshops/manage/"),
            ("restaurant", "restaurant_bookings", "restaurant_booking", tables["T"],
             "/restaurant/manage/"),
            ("event", "event_inquiries", "event", events["E"], "/events/manage/")):
        old = _one(f"SELECT manage_token FROM {table} WHERE id = ?", record_id)[0]
        s.check(f"the {category} link opens before", guest.get(page + old).status_code == 200)
        sent.clear()
        r = oc.post(f"/admin/new-link/{category}/{record_id}", data={"send": "1", "guest_id": str(gid)})
        new = _one(f"SELECT manage_token FROM {table} WHERE id = ?", record_id)[0]
        s.check(f"and after a new one, the old {category} link opens nothing",
                new != old and guest.get(page + old).status_code == 404)
        s.check(f"while the new {category} link opens it", guest.get(page + new).status_code == 200)
        s.check(f"the {category} guest is sent the new link",
                any(to == WHO and new in body for to, _s, body, _k in sent), detail=str(sent)[:300])
        s.check(f"and the {category} letter is neither queued nor filed, being itself a key",
                all(kept is False for to, _s, body, kept in sent if new in body)
                and not _one("SELECT id FROM email_outbox WHERE body LIKE ?", f"%{new}%")
                and not _one("SELECT id FROM guest_messages WHERE body LIKE ?", f"%{new}%"),
                detail=str([k for *_x, k in sent]))
        s.check(f"it is on the {category}'s own history",
                "booking_link_reissued" in _lines(kind, record_id))
        s.check(f"and the page that shows the {category} link is never cached",
                r.headers.get("Cache-Control") == "no-store" and new in r.get_data(as_text=True))
    old = _one("SELECT portal_token FROM guests WHERE id = ?", gid)[0]
    if not old:
        conn = db()
        with m.app.test_request_context("/"):
            old = m.guest_portal_token(conn, WHO)
        conn.commit()
        conn.close()
    sent.clear()
    oc.post(f"/guests/{gid}/new-link", data={"send": "1"})
    new = _one("SELECT portal_token FROM guests WHERE id = ?", gid)[0]
    s.check("their own link: the old one opens nothing, the new one opens their account",
            new != old and guest.get(f"/my/{old}").status_code == 404
            and guest.get(f"/my/{new}").status_code == 200)
    s.check("and they are sent it", any(new in body for _t, _s, body, _k in sent))
    s.check("which is on their record's history", "guest_link_reissued" in _lines("guest", gid))
    before = _one("SELECT manage_token FROM bookings WHERE id = ?", bid)[0]
    r = ec.post(f"/admin/new-link/room/{bid}", data={"send": "1"})
    s.check("a colleague cannot issue one",
            r.status_code in (302, 403)
            and _one("SELECT manage_token FROM bookings WHERE id = ?", bid)[0] == before)
    m.send_email = lambda to, subject, body, *a, **k: (k.get("report", {}).update(
        why="the domain is not verified"), False)[1]
    r = oc.post(f"/admin/new-link/room/{bid}", data={"send": "1"})
    s.check("a letter that cannot go says so, and why",
            "Not sent" in r.get_data(as_text=True)
            and "the domain is not verified" in r.get_data(as_text=True))
    m.send_email = _capture(sent)
    sent.clear()
    r = oc.post(f"/admin/new-link/room/{bid}", data={})
    s.check("and one nobody asked to send is not sent, and says so",
            not sent and "Nothing was sent" in r.get_data(as_text=True))
    notice = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("which is what the notice promises", "we will issue a new one" in notice)

    s.section("The record shows all of it")
    conn = db()
    conn.execute("""UPDATE guests SET language = 'fr', access_needs = ?, access_needs_updated_at = ?,
                    usual_arrival_time = 'after nine at night' WHERE id = ?""",
                 (f"{TAG} cannot manage stairs easily", now, gid))
    conn.commit()
    conn.close()
    for who, client in (("the owner", oc), ("a colleague", ec)):
        page = visible_text(client.get(f"/guests/{gid}").get_data(as_text=True))
        s.check(f"{who} sees the language, the access needs and the usual arrival",
                "Français" in page and "cannot manage stairs easily" in page
                and "after nine at night" in page)
    page = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("and the owner sees their own link, to pass on or replace", new in page)
    colleague = ec.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("which a colleague does not", new not in colleague)
    s.check("each booking on the record leads to its history and a new link",
            f"/admin/history/booking/{bid}" in page and f"/admin/new-link/room/{bid}" in page
            and f"/admin/new-link/restaurant/{tables['T']}" in page)
    other = _guest("Second profile", f"{TAG.lower()}.second@example.invalid")
    oc.post(f"/guests/{gid}/merge", data={"merge_id": str(other)})
    away = visible_text(oc.get(f"/guests/{other}").get_data(as_text=True))
    s.check("a profile merged into another says where it went",
            "was merged into" in away and f"{TAG} Guest" in away)

    s.section("A standing instruction, read at every door")
    oc.post(f"/guests/{gid}/caution", data={"caution_level": "refuse", "caution": "not again"})
    r = oc.post(f"/admin/restaurant/{tables['T']}/confirm", follow_redirects=True)
    s.check("a table for somebody the house will not accept is not confirmed",
            _one("SELECT status FROM restaurant_bookings WHERE id = ?", tables["T"])[0] == "pending"
            and "standing instruction" in " ".join(flashes(r)), detail=str(flashes(r)))
    r = oc.post(f"/admin/events/{events['E']}/update", data={"status": "confirmed",
                                                             "quoted_price": "9000"},
                follow_redirects=True)
    s.check("nor is an event",
            _one("SELECT status FROM event_inquiries WHERE id = ?", events["E"])[0] == "contacted"
            and "standing instruction" in " ".join(flashes(r)), detail=str(flashes(r)))
    oc.post(f"/guests/{gid}/caution", data={"caution_level": "care", "caution": "be kind"})
    oc.post(f"/admin/restaurant/{tables['T']}/confirm")
    s.check("while a lesser caution is read and does not stop it",
            _one("SELECT status FROM restaurant_bookings WHERE id = ?", tables["T"])[0] == "confirmed")

    s.section("A profile for everybody who books")
    diner = f"{TAG.lower()}.diner@example.invalid"
    oc.post(f"/admin/restaurant/{tables['T2']}/confirm")
    s.check("confirming a table makes a profile where there was none",
            _one("SELECT COUNT(*) FROM guests WHERE LOWER(email) = ?", diner)[0] == 1)
    oc.post(f"/admin/restaurant/{tables['T3']}/confirm")
    s.check("and a second table is confirmed without making a second profile",
            _one("SELECT status FROM restaurant_bookings WHERE id = ?", tables["T3"])[0] == "confirmed"
            and _one("SELECT COUNT(*) FROM guests WHERE LOWER(email) = ?", diner)[0] == 1,
            detail="the address is unique, so a second profile is a confirmation that fails")
    oc.post(f"/admin/workshops/registrations/{places['W2']}/confirm")
    s.check("so does an atelier place",
            _one("SELECT COUNT(*) FROM guests WHERE LOWER(email) = ?",
                 f"{TAG.lower()}.maker@example.invalid")[0] == 1)
    oc.post(f"/admin/events/{events['E2']}/update", data={"status": "confirmed",
                                                          "quoted_price": "9000"})
    s.check("and an event",
            _one("SELECT COUNT(*) FROM guests WHERE LOWER(email) = ?",
                 f"{TAG.lower()}.couple@example.invalid")[0] == 1)

    s.section("A delete that cannot lose a history")
    r = oc.post(f"/guests/{gid}/delete", follow_redirects=True)
    s.check("a profile that holds anything is not deleted, and says what it holds",
            _one("SELECT id FROM guests WHERE id = ?", gid) is not None
            and "stay" in " ".join(flashes(r)) and "Not deleted" in " ".join(flashes(r)),
            detail=str(flashes(r)))
    form = oc.get(f"/guests/{gid}/edit").get_data(as_text=True)
    s.check("and its edit page says so, rather than offering a button that refuses",
            "cannot be deleted" in form and f"/guests/{gid}/delete" not in form)
    empty = _guest("Typing mistake", None)
    oc.post(f"/guests/{empty}/delete")
    s.check("an empty one is deleted",
            _one("SELECT id FROM guests WHERE id = ?", empty) is None)
    s.check("and that is on the audit trail",
            _one("SELECT id FROM audit_log WHERE action = 'guest_deleted' AND target = ?",
                 f"guest {empty}") is not None)

    s.section("A merge that keeps everything")
    keep = _guest("Survivor", f"{TAG.lower()}.survivor@example.invalid")
    gone = _guest("Absorbed", f"{TAG.lower()}.absorbed@example.invalid", vip=1,
                  access_needs=f"{TAG} needs a chair in the shower", language="es",
                  photo_consent="no", photo_consent_at=now, usual_arrival_time="late morning")
    oc.post(f"/guests/{keep}/merge", data={"merge_id": str(gone)})
    kept = _one("SELECT * FROM guests WHERE id = ?", keep)
    s.check("the access needs, language, photographs, VIP and usual arrival come across",
            kept["access_needs"] == f"{TAG} needs a chair in the shower" and kept["language"] == "es"
            and kept["photo_consent"] == "no" and kept["vip"] == 1
            and kept["usual_arrival_time"] == "late morning", detail=str(dict(kept)))
    conn = db()
    lines = [x["title"] for x in m.guest_timeline(conn, keep) if x["kind"] == "Profile"]
    conn.close()
    s.check("and the survivor's history says what came across",
            any(t.startswith("VIP: no → yes") for t in lines)
            and any(t.startswith("Access needs changed") for t in lines)
            and not any("chair in the shower" in t for t in lines), detail=str(lines))
