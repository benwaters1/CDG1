"""Online check-in for guests who booked with the house (owner, 22 October
2026: "online check-in for direct guests, following the police rules").

Booking.com's guests had a check-in link since the morning; the people who
booked on the house's own site -- a room, or an atelier, whose sittings hold
the whole house -- had nothing. Now their booking page leads to one.

WHAT THE LAW ASKS, AND NO MORE (CESEDA R. 814-1 to R. 814-3, checked on
Légifrance 22 October 2026):

  - A FOREIGN GUEST'S FICHE: names, date and place of birth, nationality, usual
    home, mobile and email, and the stay's dates -- and nothing past that list.
  - A FRENCH GUEST IS NOT ASKED FOR ONE. The register still counts them, the
    way its own page tells the desk to: a name and "French", nothing else.
  - A CHILD UNDER 15 MAY GO ON AN ACCOMPANYING ADULT'S FICHE, so a family of
    four with both parents recorded is not two fiches short.
  - SIX MONTHS from the end of the stay, then deleted -- an atelier's from its
    last day.

AND THE LETTER: a new automation, off, sending a letter that ships with no
words. Nothing goes until the owner has written it; send_email is stood in
for throughout, so nothing here could reach anybody anyway.

Plus where it lands: the register, the arrivals sheet, the front desk and the
kitchen's lists -- what they cannot eat goes once they have left.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZDCI"
LETTERS = ("room_checkin_before", "workshop_checkin_before")


def _cleanup(conn):
    conn.execute("DELETE FROM police_register WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_police_register WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_booking_guests WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE action = 'direct_checkin_done' "
                 "AND target LIKE ?", (TAG + "%",))
    conn.commit()


def _room(conn):
    return conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()["id"]


def _stay(conn, ref, arrive_in=10, nights=2, party=2, status="confirmed",
          country="United Kingdom", **more):
    """A room stay of the house's own, written straight in: what is being
    checked is the check-in, not the booking form's availability rules."""
    arrive = house_today() + timedelta(days=arrive_in)
    cols = {"room_id": _room(conn), "reference_code": TAG + ref,
            "manage_token": (TAG + "tok" + ref).lower(), "guest_name": "Ada " + TAG + ref,
            "guest_email": f"{TAG.lower()}.{ref.lower()}@example.invalid",
            "guest_phone": "+44 7700 900123", "arrival_date": arrive.isoformat(),
            "departure_date": (arrive + timedelta(days=nights)).isoformat(),
            "party_size": party, "status": status, "total_price": 300,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "guest_address": "1 Mill Lane", "guest_city": "York", "guest_postcode": "YO1 7HH",
            "guest_country": country}
    cols.update(more)
    conn.execute(f"INSERT INTO bookings ({', '.join(cols)}) VALUES "
                 f"({', '.join('?' * len(cols))})", tuple(cols.values()))
    conn.commit()
    return conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                        (TAG + ref,)).fetchone()


def _place(conn, ref, start_in=10, days=4, party=2, names=()):
    """An atelier place on a sitting of its own."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
                       (f"{TAG} atelier {ref}", now))
    start = house_today() + timedelta(days=start_in)
    sid = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
               created_at) VALUES (?, ?, ?, 8, ?)""",
        (cur.lastrowid, start.isoformat(), (start + timedelta(days=days)).isoformat(),
         now)).lastrowid
    wid = conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
               guest_name, guest_email, party_size, status, created_at, guest_country)
           VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?, 'Netherlands')""",
        (sid, TAG + ref, (TAG + "wtok" + ref).lower(), "Anouk " + TAG + ref,
         f"{TAG.lower()}.w{ref.lower()}@example.invalid", party, now)).lastrowid
    for i, name in enumerate(names):
        conn.execute("""INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name,
                        is_lead, created_at) VALUES (?, ?, ?, ?)""", (wid, name, int(i == 0), now))
    conn.commit()
    return conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (wid,)).fetchone()


def _person(n, **fields):
    return {f"p{n}_{k}": v for k, v in fields.items()}


def _foreign(n, surname, nationality="British", **more):
    return _person(n, first_names="Ada", surname=surname, nationality=nationality,
                   born_on="1984-06-11", born_at="Leeds", home="1 Mill Lane, York",
                   phone="+44 7700 900123", email="ada@example.invalid", **more)


def _fiches(conn, table, column, stay_id):
    return conn.execute(f"SELECT * FROM {table} WHERE {column} = ? ORDER BY id",
                        (stay_id,)).fetchall()


def run():
    s = Suite("Online check-in for the house's own guests")
    conn = db()
    _cleanup(conn)
    sent = []
    real_send = m.send_email

    def fake_send(to, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body, "kw": kw})
        return True
    m.send_email = fake_send
    put_back_letters = _harness.keep_rows(
        conn, "email_templates", "template_key IN (?, ?)", LETTERS)
    try:
        _checks(s, conn, sent)
    finally:
        m.send_email = real_send
        put_back_letters()
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn, sent):
    oc, ec, owner, _emp = clients()
    guest = m.app.test_client()
    today = house_today()

    # ------------------------------------------------------------------
    s.section("The link, on the guest's own booking page")
    stay = _stay(conn, "LINK")
    page = guest.get(f"/book/manage/{stay['manage_token']}").get_data(as_text=True)
    url = f"/book/manage/{stay['manage_token']}/check-in"
    s.check("a confirmed stay's booking page leads to the check-in", url in page,
            detail="the form exists to be reached from here")
    form = guest.get(url)
    body = form.get_data(as_text=True)
    s.check("the check-in opens", form.status_code == 200 and "Check in online" in body,
            detail=str(form.status_code))
    s.check("and is never indexed", 'name="robots" content="noindex' in body)
    s.check("one block for each of the party", 'data-person="2"' in body
            and 'data-person="3"' not in body)
    s.check("nationality suggested from where they live", 'value="British"' in body)
    pending = _stay(conn, "PEND", status="pending")
    ppage = guest.get(f"/book/manage/{pending['manage_token']}").get_data(as_text=True)
    s.check("a booking still waiting for an answer has no link yet",
            f"/book/manage/{pending['manage_token']}/check-in" not in ppage)
    pform = guest.get(f"/book/manage/{pending['manage_token']}/check-in").get_data(as_text=True)
    s.check("and its check-in page says when it opens",
            "opens once your booking is confirmed" in pform)
    s.check("a link that is not one is a 404",
            guest.get("/book/manage/not-a-token/check-in").status_code == 404)

    # ------------------------------------------------------------------
    s.section("A foreign guest's fiche")
    refused = guest.post(url, data=dict(
        _person(1, first_names="Ada", surname="Lovelace", nationality="British",
                born_at="Leeds", home="1 Mill Lane, York"),
        **_person(2, first_names="Jean", surname="Martin", nationality="French"),
        agree="on"), follow_redirects=True)
    s.check("not French and no date of birth: refused, saying why",
            any("date of birth" in f for f in flashes(refused)), detail=str(flashes(refused)))
    s.check("and nothing is written by a refusal",
            not _fiches(conn, "police_register", "booking_id", stay["id"])
            and not conn.execute("SELECT checkin_done_at FROM bookings WHERE id = ?",
                                 (stay["id"],)).fetchone()[0])
    done = guest.post(url, data=dict(
        _foreign(1, "Lovelace"),
        # A French adult whose block carries birth details anyway -- typed, or
        # left by the browser. None of it is the house's to keep.
        **_person(2, first_names="Jean", surname="Martin", nationality="French",
                  born_on="1980-01-01", born_at="Lyon", home="2 rue X, Lyon",
                  phone="+33 6 00 00 00 00", email="jean@example.invalid"),
        estimated_arrival_time="4pm-5pm", dietary_notes="No shellfish", agree="on"),
        follow_redirects=True)
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["id"],)).fetchone()
    s.check("checked in", bool(row["checkin_done_at"]), detail=str(flashes(done)))
    s.check("with the arrival time and what they cannot eat, on the booking",
            row["estimated_arrival_time"] == "4pm-5pm" and row["dietary_notes"] == "No shellfish")
    fiches = _fiches(conn, "police_register", "booking_id", stay["id"])
    s.check("a fiche for each adult", len(fiches) == 2, detail=str(len(fiches)))
    brit = next((f for f in fiches if f["surname"] == "Lovelace"), None)
    s.check("the foreign guest's fiche has every field R. 814-2 lists",
            brit and brit["born_on"] == "1984-06-11" and brit["born_at"] == "Leeds"
            and brit["nationality"] == "British" and "York" in (brit["home_address"] or "")
            and brit["phone"] == "+44 7700 900123" and brit["email"] == "ada@example.invalid",
            detail=str(dict(brit) if brit else None))
    s.check("filled in by the guest, not by somebody at the house",
            brit and brit["recorded_by_user_id"] is None)
    s.check("the register's columns are still only the law's list",
            not {c[1] for c in conn.execute("PRAGMA table_info(police_register)")}
            & {"passport", "passport_number", "document_number", "id_number", "scan"})
    with m.app.test_request_context():
        state = m.police_register_needed(conn, stay["id"])
    s.check("and the stay's register is complete", state["outstanding"] == 0,
            detail=f"{state['recorded']} of {state['party']}")
    audit = conn.execute("SELECT * FROM audit_log WHERE action = 'direct_checkin_done' "
                         "AND target = ?", (stay["reference_code"],)).fetchone()
    s.check("the check-in is on the audit record, without the guest's name",
            audit and "Lovelace" not in ((audit["details"] or "") + (audit["target"] or "")))
    guest.post(url, data=dict(_foreign(1, "Twice"), agree="on"))
    s.check("sent again, it writes nothing more",
            len(_fiches(conn, "police_register", "booking_id", stay["id"])) == 2)
    s.check("and the page thanks them", "you are checked in" in guest.get(url).get_data(as_text=True))
    # The desk got there first: a fiche typed at the door, then the form.
    pre = _stay(conn, "PRE", arrive_in=-1, party=1)
    oc.post(f"/admin/bookings/{pre['id']}/register", data={
        "surname": "Byron", "first_names": "Ada", "nationality": "British",
        "born_on": "1984-06-11", "born_at": "Leeds"})
    guest.post(f"/book/manage/{pre['manage_token']}/check-in",
               data=dict(_foreign(1, "Byron"), dietary_notes="Vegetarian", agree="on"))
    s.check("a fiche written at the door is not doubled by the form",
            len(_fiches(conn, "police_register", "booking_id", pre["id"])) == 1
            and conn.execute("SELECT dietary_notes FROM bookings WHERE id = ?",
                             (pre["id"],)).fetchone()[0] == "Vegetarian")
    page = guest.get(f"/book/manage/{stay['manage_token']}").get_data(as_text=True)
    s.check("the booking page says it is done", "You checked in online on" in page)

    # ------------------------------------------------------------------
    s.section("A French guest is not asked for one")
    fr = _stay(conn, "FR", party=1, country="France")
    furl = f"/book/manage/{fr['manage_token']}/check-in"
    fbody = guest.get(furl).get_data(as_text=True)
    s.check("their nationality is suggested as French", 'value="French"' in fbody)
    s.check("and the form knows which words are French",
            "française" in fbody and "français" in fbody)
    guest.post(furl, data=dict(
        _person(1, first_names="Élodie", surname="Rouvière", nationality="Française"),
        agree="on"))
    ffiches = _fiches(conn, "police_register", "booking_id", fr["id"])
    s.check("checked in with no date of birth, place, address, mobile or email",
            bool(conn.execute("SELECT checkin_done_at FROM bookings WHERE id = ?",
                              (fr["id"],)).fetchone()[0]))
    s.check("on the register only as the desk is told to write one: a name and French",
            len(ffiches) == 1 and ffiches[0]["nationality"] == "Française"
            and not any(ffiches[0][k] for k in ("born_on", "born_at", "home_address",
                                                "phone", "email")),
            detail=str([dict(f) for f in ffiches]))
    martin = next((f for f in fiches if f["surname"] == "Martin"), None)
    s.check("and what a French adult's block carried anyway was not kept",
            martin and not any(martin[k] for k in ("born_on", "born_at", "home_address",
                                                    "phone", "email")),
            detail=str(dict(martin) if martin else None))
    s.check("the masculine spelling reads as French too",
            m.is_french_national("Français") and m.is_french_national("française"))

    # ------------------------------------------------------------------
    s.section("A child under 15 on an adult's fiche")
    fam = _stay(conn, "FAM", party=4, country="Germany")
    curl = f"/book/manage/{fam['manage_token']}/check-in"
    cbody = guest.get(curl).get_data(as_text=True)
    s.check("a party of four is asked how many are under 15",
            'name="children_under_15"' in cbody)
    r = guest.post(curl, data=dict(_foreign(1, "Weber", "German"), children_under_15="4",
                                   agree="on"), follow_redirects=True)
    s.check("a count that leaves no adult is refused",
            any("at least one" in f for f in flashes(r)), detail=str(flashes(r)))
    guest.post(curl, data=dict(
        _foreign(1, "Weber", "German"), **_foreign(2, "Weber-Klein", "German"),
        # The children's blocks, left with something in them: not read.
        **_person(3, first_names="Kind", surname="Weber", nationality="German"),
        children_under_15="2", agree="on"))
    kfiches = _fiches(conn, "police_register", "booking_id", fam["id"])
    s.check("two fiches for two adults, none for the children", len(kfiches) == 2,
            detail=str([f["surname"] for f in kfiches]))
    s.check("the two children ride on the first adult's",
            kfiches and kfiches[0]["children_under_15"] == 2
            and kfiches[-1]["children_under_15"] == 0,
            detail=str([f["children_under_15"] for f in kfiches]))
    with m.app.test_request_context():
        kstate = m.police_register_needed(conn, fam["id"])
    s.check("and the register counts the whole family as done",
            kstate["recorded"] == 4 and kstate["outstanding"] == 0,
            detail=f"{kstate['recorded']} of {kstate['party']}")
    mixed = _stay(conn, "MIX", party=3, country="France")
    guest.post(f"/book/manage/{mixed['manage_token']}/check-in", data=dict(
        _person(1, first_names="Paul", surname="Durand", nationality="French"),
        **_foreign(2, "Visser", "Dutch"), children_under_15="1", agree="on"))
    mfiches = {f["surname"]: f for f in _fiches(conn, "police_register", "booking_id", mixed["id"])}
    s.check("a child goes on the fiche of the adult who is not French",
            mfiches.get("Visser") and mfiches["Visser"]["children_under_15"] == 1
            and mfiches.get("Durand") and mfiches["Durand"]["children_under_15"] == 0,
            detail=str({k: v["children_under_15"] for k, v in mfiches.items()}))
    # And by hand, at the desk, where the register asks the same question.
    desk = _stay(conn, "DESK", arrive_in=-1, party=3, country="Germany")
    oc.post(f"/admin/bookings/{desk['id']}/register", data={
        "surname": "Fischer", "first_names": "Lena", "nationality": "German",
        "born_on": "1979-02-03", "born_at": "Köln", "children_under_15": "2"})
    dfiche = _fiches(conn, "police_register", "booking_id", desk["id"])
    s.check("the desk's own form takes the children too",
            dfiche and dfiche[0]["children_under_15"] == 2)
    regpage = oc.get("/admin/register").get_data(as_text=True)
    s.check("and the register says so beside the adult", "with 2 children under 15" in regpage)

    # ------------------------------------------------------------------
    s.section("An atelier guest, whose sitting holds the house")
    place = _place(conn, "ATL", start_in=6, party=2, names=("Anouk de Vries", "Sem Bakker"))
    wpage = guest.get(f"/workshops/manage/{place['manage_token']}").get_data(as_text=True)
    wurl = f"/workshops/manage/{place['manage_token']}/check-in"
    s.check("their registration page leads to the check-in", wurl in wpage)
    wbody = guest.get(wurl).get_data(as_text=True)
    s.check("the people the registration named are in the boxes",
            'value="Anouk de"' in wbody or 'value="Vries"' in wbody)
    s.check("and nobody is asked about children: ateliers are for adults",
            'name="children_under_15"' not in wbody)
    guest.post(wurl, data=dict(_foreign(1, "de Vries", "Dutch"), **_foreign(2, "Bakker", "Dutch"),
                               estimated_arrival_time="After 10pm", agree="on"))
    wf = _fiches(conn, "workshop_police_register", "workshop_booking_id", place["id"])
    s.check("their fiches go to the ateliers' register", len(wf) == 2
            and wf[0]["born_at"] == "Leeds", detail=str(len(wf)))
    s.check("and the arrival time to the place",
            conn.execute("SELECT estimated_arrival_time FROM workshop_bookings WHERE id = ?",
                         (place["id"],)).fetchone()[0] == "After 10pm")
    span = (today.isoformat(), (today + timedelta(days=30)).isoformat())
    reg = oc.get(f"/admin/register?from={span[0]}&to={span[1]}").get_data(as_text=True)
    s.check("the register lists them with everybody else", "de Vries" in reg and "Bakker" in reg)
    # A sitting that has begun with nobody recorded is chased like any stay.
    begun = _place(conn, "BEGUN", start_in=-1, party=1)
    with m.app.test_request_context():
        waiting = m.stays_missing_fiches(conn, today)
    ours = [x for x in waiting if x.get("kind") == "workshop"
            and x["booking"]["id"] == begun["id"]]
    s.check("an atelier under way with no fiche is waiting to be filled in", bool(ours))
    r = oc.post(f"/admin/workshops/registrations/{begun['id']}/register", data={
        "surname": "Jansen", "first_names": "Daan", "nationality": "Dutch",
        "born_on": "1990-05-05", "born_at": "Utrecht"}, follow_redirects=True)
    bf = _fiches(conn, "workshop_police_register", "workshop_booking_id", begun["id"])
    s.check("the desk can add one by hand", len(bf) == 1, detail=str(flashes(r)))
    s.check("an employee cannot",
            ec.post(f"/admin/workshops/registrations/{begun['id']}/register",
                    data={"surname": "X", "first_names": "Y", "nationality": "Dutch"})
            .status_code in (302, 303, 403)
            and len(_fiches(conn, "workshop_police_register", "workshop_booking_id",
                            begun["id"])) == 1)
    if bf:
        oc.post(f"/admin/register/workshop/{bf[0]['id']}/delete")
        s.check("and take a mistyped one off again",
                not _fiches(conn, "workshop_police_register", "workshop_booking_id", begun["id"]))

    # ------------------------------------------------------------------
    s.section("Six months after the stay ends, then deleted")
    gone = _stay(conn, "OLD", arrive_in=-200, nights=3, party=1)
    kept = _stay(conn, "KEPT", arrive_in=-33, nights=3, party=1)
    old_place = _place(conn, "WOLD", start_in=-204, days=4, party=1)
    new_place = _place(conn, "WNEW", start_in=-34, days=4, party=1)
    now = datetime.now(timezone.utc).isoformat()
    for st in (gone, kept):
        conn.execute("""INSERT INTO police_register (booking_id, surname, first_names,
                        nationality, recorded_at) VALUES (?, 'Old', 'A', 'Swiss', ?)""",
                     (st["id"], now))
    for pl in (old_place, new_place):
        conn.execute("""INSERT INTO workshop_police_register (workshop_booking_id, surname,
                        first_names, nationality, recorded_at) VALUES (?, 'Old', 'A', 'Swiss', ?)""",
                     (pl["id"], now))
    conn.commit()
    with m.app.test_request_context():
        m.purge_police_register(conn, today)
    conn.commit()
    s.check("a room's fiche goes six months after departure",
            not _fiches(conn, "police_register", "booking_id", gone["id"]))
    s.check("one from last month stays",
            bool(_fiches(conn, "police_register", "booking_id", kept["id"])))
    s.check("an atelier's goes six months after its last day",
            not _fiches(conn, "workshop_police_register", "workshop_booking_id", old_place["id"]))
    s.check("and a recent atelier's stays",
            bool(_fiches(conn, "workshop_police_register", "workshop_booking_id", new_place["id"])))
    # What they cannot eat goes once they have left, as the notice says.
    left = _stay(conn, "LEFT", arrive_in=-4, nights=3, party=1, dietary_notes="Coeliac")
    here = _stay(conn, "HERE", arrive_in=-1, nights=3, party=1, dietary_notes="Vegan")
    with m.app.test_request_context():
        m.purge_health_notes(conn, today)
    conn.commit()
    s.check("a stay's dietary note is deleted once it is over",
            conn.execute("SELECT dietary_notes FROM bookings WHERE id = ?",
                         (left["id"],)).fetchone()[0] is None)
    s.check("and kept while they are still here",
            conn.execute("SELECT dietary_notes FROM bookings WHERE id = ?",
                         (here["id"],)).fetchone()[0] == "Vegan")

    # ------------------------------------------------------------------
    s.section("Where it goes: arrivals, the front desk, the kitchen")
    today_stay = _stay(conn, "TODAY", arrive_in=0, nights=2, party=1, country="Spain")
    turl = f"/book/manage/{today_stay['manage_token']}/check-in"
    guest.post(turl, data=dict(_foreign(1, "Garcia", "Spanish"),
                               dietary_notes="Severe nut allergy",
                               estimated_arrival_time="6pm-7pm", agree="on"))
    sheet = m.arrivals_sheet(conn, today)
    row = next((r for r in sheet["arriving"]
                if r["booking"]["reference_code"] == TAG + "TODAY"), None)
    s.check("the arrivals sheet has what they cannot eat",
            row and "Severe nut allergy" in row["dietary"], detail=str(row and row["dietary"]))
    s.check("and that they checked in online", row and row["checked_in_online"])
    arr = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("the arrivals page says so", "checked in online" in arr)
    desk_page = oc.get("/admin/desk").get_data(as_text=True)
    s.check("so does the front desk, with the note",
            "checked in online" in desk_page and "Severe nut allergy" in desk_page)
    diet = m.dietary_sheet(conn, today)
    s.check("the kitchen's dietary list has it",
            any("Severe nut allergy" in " ".join(p["notes"]) for p in diet["people"]))
    ksheet = m.kitchen_sheet(conn, today)
    s.check("and the kitchen's day sheet",
            any("Severe nut allergy" in " ".join(r["notes"])
                for sec in ksheet["sections"] for r in sec["rows"]))
    admin = oc.get(f"/admin/bookings/{today_stay['id']}").get_data(as_text=True)
    s.check("the booking's own page says it is done, and what they cannot eat",
            "Online check-in" in admin and "Severe nut allergy" in admin)

    # ------------------------------------------------------------------
    s.section("The letter before arrival: off, and nothing in it until the owner writes it")
    shipped = {k: (subj, body) for k, _l, subj, body in m.DEFAULT_EMAIL_TEMPLATES}
    s.check("both letters ship with no words at all",
            all(shipped.get(k) == ("", "") for k in LETTERS), detail=str(
                {k: shipped.get(k) for k in LETTERS}))
    s.check("the automation ships switched off",
            m.AUTOMATION_SETTING_DEFAULTS["automation_checkin_letter_enabled"] == "0"
            and m.get_automation_settings(conn)["automation_checkin_letter_enabled"] == "0")
    job = next(j for j in m.AUTOMATION_JOBS if j[0] == "checkin_letter")
    s.check("and the job runs only on that switch",
            job[1] == "automation_checkin_letter_enabled")
    s.check("in the morning, as its row says",
            m.JOB_HOURS.get("checkin_letter") == (9, 12))
    for key in LETTERS:
        conn.execute("UPDATE email_templates SET subject = '', body = '' WHERE template_key = ?",
                     (key,))
    conn.commit()
    soon = _stay(conn, "SOON", arrive_in=2, party=1)
    later = _stay(conn, "LATER", arrive_in=20, party=1)
    # Inside the window, and already checked in online.
    early = _stay(conn, "EARLY", arrive_in=1, party=1)
    guest.post(f"/book/manage/{early['manage_token']}/check-in",
               data=dict(_foreign(1, "Early"), agree="on"))
    sent.clear()
    try:
        with m.app.test_request_context("/"):
            m.run_checkin_letter_job(conn, today)
        refused = None
    except m.JobNotSetUp as e:
        refused = str(e)
    s.check("unwritten, the job will not run, and says why",
            refused and "wording" in refused, detail=str(refused))
    s.check("nothing was sent", not sent, detail=str([x["to"] for x in sent]))
    s.check("and nobody was stamped as sent, so writing it later still reaches them",
            conn.execute("SELECT checkin_letter_sent_at FROM bookings WHERE id = ?",
                         (soon["id"],)).fetchone()[0] is None)
    with m.app.test_request_context("/"):
        ok, why = m.send_direct_checkin_letter(conn, "booking", soon["id"])
    s.check("nor will it go by hand", not ok and "no wording" in why, detail=why)
    autopage = oc.get("/admin/automation").get_data(as_text=True)
    s.check("the Automation page lists it", "Email the check-in link before arrival" in autopage)
    s.check("and says it has no wording yet", "No wording yet</strong> for" in autopage)
    tpage = oc.get("/management/email-templates").get_data(as_text=True)
    s.check("the email templates page lists both, as not written",
            "Online check-in link, before arrival" in tpage and "No wording yet" in tpage)
    r = oc.post("/management/email-templates/room_checkin_before/edit",
                data={"subject": "Check in", "body": "Hello {first_name}."},
                follow_redirects=True)
    s.check("written without the link, it is refused",
            any("checkin_url" in f for f in flashes(r)), detail=str(flashes(r)))
    oc.post("/management/email-templates/room_checkin_before/edit",
            data={"subject": "Before you arrive",
                  "body": "Hello {first_name},\n\nCheck in online:\n{checkin_url}"})
    sent.clear()
    with m.app.test_request_context("/"):
        out = m.run_checkin_letter_job(conn, today)
    to_soon = [x for x in sent if x["to"] == soon["guest_email"]]
    s.check("written, it goes to a stay arriving within the days set",
            len(to_soon) == 1, detail=out)
    s.check("carrying their own check-in link",
            to_soon and f"/book/manage/{soon['manage_token']}/check-in" in to_soon[0]["body"])
    s.check("not to a stay further off", not [x for x in sent if x["to"] == later["guest_email"]])
    s.check("nor to one that has checked in online already",
            conn.execute("SELECT checkin_done_at FROM bookings WHERE id = ?",
                         (early["id"],)).fetchone()[0]
            and not [x for x in sent if x["to"] == early["guest_email"]])
    s.check("and it says the atelier letter is still unwritten",
            "atelier letter has no wording yet" in out, detail=out)
    sent.clear()
    with m.app.test_request_context("/"):
        m.run_checkin_letter_job(conn, today)
    s.check("once: the next morning sends it again to nobody",
            not [x for x in sent if x["to"] == soon["guest_email"]])
    oc.post("/management/email-templates/room_checkin_before/restore")
    s.check("putting back the original wording withdraws it",
            m.letter_unwritten(conn, "room_checkin_before"))


if __name__ == "__main__":
    print(run().report())
