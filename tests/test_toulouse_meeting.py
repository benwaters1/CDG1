"""The Toulouse meeting: who is coming, how, and the car.

The owner, 24 November 2026, of five back-end ideas: "1 only" -- the first:
an atelier with transfers starts at midday on its first day at the Grand Hôtel
de l'Opéra, Place du Capitole, and the house drives everybody up. So it has to
know, for each place, whether they are meeting it there or making their own
way, and with a train or a plane, when they get in.

What is checked:
  - THE GUEST SAYS IT on their own booking page, only for an atelier that
    starts in Toulouse, only before it starts, and only as one of the two
    answers; the house can put down what somebody said on the telephone.
  - THE RUN SHEET counts confirmed places only, each under what they said,
    with the ones still waiting on a decision counted apart.
  - THE CAR is booked from it once, at midday on the first day at the hotel
    (and back on the last if a time is given), onto the vehicle's own list.
  - A WEEK AHEAD, anybody who has said nothing is asked once (off as shipped,
    nothing while Guest emails would hold the letter), the owner's home page
    names the sitting, and the run sheet is a task due the day before. All
    three close themselves.

send_email is replaced for every send; nothing here can reach a guest.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today, keep_rows
import _harness

m = _harness.m
TAG = "ZZTLS"
SETTINGS = ("automation_workshop_travel_ask_enabled", "guest_mail_live", "guest_emails_on")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup(conn):
    sids = [r["id"] for r in conn.execute(
        "SELECT workshop_sessions.id FROM workshop_sessions JOIN workshops "
        "ON workshops.id = workshop_sessions.workshop_id WHERE workshops.title LIKE ?",
        (TAG + "%",))]
    if sids:
        marks = ",".join("?" * len(sids))
        conn.execute(f"DELETE FROM vehicle_transfers WHERE workshop_session_id IN ({marks})", sids)
        conn.execute(f"DELETE FROM workshop_booking_guests WHERE workshop_booking_id IN "
                     f"(SELECT id FROM workshop_bookings WHERE session_id IN ({marks}))", sids)
        conn.execute(f"DELETE FROM workshop_bookings WHERE session_id IN ({marks})", sids)
        conn.execute(f"DELETE FROM workshop_sessions WHERE id IN ({marks})", sids)
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.commit()


def _set(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def _approve(conn, key, on=True):
    conn.execute("DELETE FROM guest_letter_approvals WHERE letter_key = ?", (key,))
    if on:
        conn.execute("INSERT INTO guest_letter_approvals (letter_key, wording_hash, approved_at) "
                     "VALUES (?, ?, ?)", (key, m.letter_wording_hash(conn, key), _now()))
    conn.commit()


def _atelier(conn, name, start, days, transfers):
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active,
               sort_order, transfers_included, created_at)
           VALUES (?, '', 3000, 12, 1, 97, ?, ?)""", (f"{TAG} {name}", 1 if transfers else 0, _now()))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 12, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=days)).isoformat(), TAG, _now()))
    conn.commit()
    return conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]


def _place(conn, sid, ref, *, party=1, status="confirmed", email=True, do_not_email=0, phone=None):
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, guest_phone, party_size, status, do_not_email, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG}-{ref}", f"tok{TAG}{ref}{_harness.secrets_token()}".lower(), f"Ines {TAG}{ref}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid" if email else "", phone, party, status,
         do_not_email, _now()))
    conn.commit()
    return conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                        (f"{TAG}-{ref}",)).fetchone()


def _row(conn, ref):
    return conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                        (f"{TAG}-{ref}",)).fetchone()


def _ask(today):
    """Run the week-ahead job with a stand-in post office: (said, letters, refusal)."""
    letters = []

    def post(to, subject, body, **opts):
        letters.append({"to": to, "subject": subject, "body": body,
                        "key": opts.get("template_key")})
        return True

    was, gate = m.send_email, m.guest_letter_held_why
    m.send_email = post
    m.guest_letter_held_why = _harness.REAL_GUEST_LETTER_HELD_WHY
    conn = db()
    try:
        with m.app.test_request_context("/", base_url="https://www.chateaugudanes.com"):
            try:
                return m.run_workshop_travel_ask_job(conn, today=today), letters, None
            except m.JobNotSetUp as e:
                return None, letters, str(e)
    finally:
        conn.close()
        m.send_email, m.guest_letter_held_why = was, gate


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def run():
    s = Suite("The Toulouse meeting: who is coming, how, and the car")
    oc, ec, owner, _emp = clients()
    conn = db()
    put_settings = keep_rows(conn, "app_settings",
                             "key IN (%s)" % ",".join("?" * len(SETTINGS)), SETTINGS)
    put_approvals = keep_rows(conn, "guest_letter_approvals", "letter_key = ?", (m.TRAVEL_ASK_KEY,))
    _cleanup(conn)
    was = m.send_email
    m.send_email = lambda *a, **kw: True
    try:
        _checks(s, oc, ec, owner, conn)
    finally:
        m.send_email = was
        _cleanup(conn)
        put_approvals()
        put_settings()
        conn.close()
    return s


def _checks(s, oc, ec, owner, conn):
    today = house_today()
    start = today + timedelta(days=5)
    sid = _atelier(conn, "Retreat", start, 4, transfers=True)
    far = _atelier(conn, "Later", today + timedelta(days=20), 4, transfers=True)
    home = _atelier(conn, "At home", start, 4, transfers=False)
    meet = _place(conn, sid, "MEET", party=2, phone="+33 6 00 00 00 01")
    conn.execute("INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name, is_lead, "
                 "created_at) VALUES (?, ?, 0, ?)", (meet["id"], f"Paul {TAG}", _now()))
    conn.commit()
    own = _place(conn, sid, "OWN")
    quiet = _place(conn, sid, "QUIET", party=3)
    _place(conn, sid, "NOMAIL", do_not_email=1)
    _place(conn, sid, "WAIT", party=2, status="pending")
    _place(conn, sid, "GONE", status="cancelled")
    _place(conn, far, "FAR")
    athome = _place(conn, home, "HOME")
    anon = m.app.test_client()

    s.section("The guest says it on their own booking page")
    page = anon.get(f"/workshops/manage/{meet['manage_token']}").get_data(as_text=True)
    s.check("an atelier that starts in Toulouse asks how they are getting there",
            'id="getting-here"' in page and "Grand Hôtel de l&#39;Opéra" in page
            or ('id="getting-here"' in page and "Grand Hôtel de l'Opéra" in page))
    s.check("one that starts at the château does not",
            'id="getting-here"' not in anon.get(f"/workshops/manage/{athome['manage_token']}")
            .get_data(as_text=True))
    r = anon.post(f"/workshops/manage/{meet['manage_token']}",
                  data={"action": "travel", "travel_how": "meet",
                        "travel_detail": "  The train into   Matabiau at 10:40 "})
    row = _row(conn, "MEET")
    s.check("meeting us, with the train and its time, is written down as theirs",
            row["travel_how"] == "meet" and row["travel_detail"] == "The train into Matabiau at 10:40"
            and row["travel_told_by"] == "the guest" and row["travel_told_at"],
            detail=str(dict(row)) if row else "")
    s.check("and they go back to the same card", r.status_code == 302
            and r.headers["Location"].endswith("#getting-here"))
    s.check("the page then says what they told us",
            "meeting us at the grand hôtel de l'opéra" in _text(
                anon.get(f"/workshops/manage/{meet['manage_token']}").get_data(as_text=True))
            .replace("&#39;", "'").lower())
    anon.post(f"/workshops/manage/{own['manage_token']}",
              data={"action": "travel", "travel_how": "by balloon"})
    s.check("anything but the two answers is refused, and nothing is written",
            _row(conn, "OWN")["travel_how"] is None)
    anon.post(f"/workshops/manage/{athome['manage_token']}",
              data={"action": "travel", "travel_how": "meet"})
    s.check("nor is it taken for an atelier with no meeting", _row(conn, "HOME")["travel_how"] is None)
    anon.post(f"/workshops/manage/{own['manage_token']}",
              data={"action": "travel", "travel_how": "direct", "travel_detail": "Driving, about 4pm"})
    s.check("making their own way is written down too", _row(conn, "OWN")["travel_how"] == "direct")
    late = m.travel_card(dict(_row(conn, "QUIET"), transfers_included=1,
                              start_date=start.isoformat()), today=start + timedelta(days=1))
    s.check("once it has begun, the page no longer asks", late is None)

    s.section("The run sheet")
    sheet = oc.get(f"/admin/workshops/sessions/{sid}/toulouse")
    html = sheet.get_data(as_text=True)
    tm = m.toulouse_meeting(conn, sid)
    s.check("the owner opens it", sheet.status_code == 200)
    s.check("meeting us: the party of two, their phone and their train",
            [x["booking"]["reference_code"] for x in tm["groups"]["meet"]] == [f"{TAG}-MEET"]
            and "+33 6 00 00 00 01" in html and "Matabiau at 10:40" in html
            and f"Paul {TAG}" in html, detail=str(tm["count"]))
    s.check("own way and not said, each under its own heading",
            [x["booking"]["reference_code"] for x in tm["groups"]["direct"]] == [f"{TAG}-OWN"]
            and {x["booking"]["reference_code"] for x in tm["groups"]["unsaid"]}
            == {f"{TAG}-QUIET", f"{TAG}-NOMAIL"})
    s.check("counted in people, not bookings: 2 meeting, 1 own way, 4 not said",
            tm["count"] == {"meet": 2, "direct": 1, "unsaid": 4}, detail=str(tm["count"]))
    s.check("a place still waiting on a decision is counted apart, a cancelled one not at all",
            tm["waiting"] == 2 and f"{TAG}-GONE" not in html, detail=str(tm["waiting"]))
    s.check("an atelier with no meeting has no run sheet",
            oc.get(f"/admin/workshops/sessions/{home}/toulouse").status_code == 404)
    s.check("and it is the owner's page", ec.get(f"/admin/workshops/sessions/{sid}/toulouse")
            .status_code in (302, 403))
    s.check("the sitting's own page leads to it",
            f"/admin/workshops/sessions/{sid}/toulouse" in
            oc.get(f"/admin/workshops/sessions/{sid}").get_data(as_text=True))
    s.check("and only where there is a meeting",
            f"/admin/workshops/sessions/{home}/toulouse" not in
            oc.get(f"/admin/workshops/sessions/{home}").get_data(as_text=True))

    s.section("Nothing while the week-ahead letter cannot go")
    _set(conn, "guest_mail_live", "1")
    _set(conn, "guest_emails_on", "1")
    _approve(conn, m.TRAVEL_ASK_KEY)
    s.check("its switch ships off",
            m.AUTOMATION_SETTING_DEFAULTS.get(m.TRAVEL_ASK_SWITCH) == "0")
    _set(conn, m.TRAVEL_ASK_SWITCH, None)
    said, letters, refused = _ask(today)
    s.check("switched off, it says so and asks nobody", refused and "switched off" in refused
            and not letters, detail=str(refused))
    _set(conn, m.TRAVEL_ASK_SWITCH, "1")
    _approve(conn, m.TRAVEL_ASK_KEY, on=False)
    said, letters, refused = _ask(today)
    s.check("not approved at Guest emails, it says so, asks nobody and stamps nobody",
            refused and "Guest emails" in refused and not letters
            and _row(conn, "QUIET")["travel_asked_at"] is None, detail=str(refused))
    _approve(conn, m.TRAVEL_ASK_KEY)

    s.section("A week ahead, anybody who has not said is asked once")
    said, letters, refused = _ask(today)
    to = {x["to"] for x in letters}
    quiet_letter = [x for x in letters if x["to"] == f"{TAG.lower()}.quiet@example.invalid"]
    s.check("the guest who has said nothing is asked", len(quiet_letter) == 1,
            detail=f"{said} / {refused}")
    body = quiet_letter[0]["body"] if quiet_letter else ""
    s.check("told where and when, with the way to answer",
            "Grand Hôtel de l'Opéra" in body and "midday" in body
            and f"/workshops/manage/{quiet['manage_token']}#getting-here" in body, detail=body[:300])
    s.check("as this letter, so Guest emails can tell it apart",
            quiet_letter and quiet_letter[0]["key"] == m.TRAVEL_ASK_KEY)
    s.check("not those who have said, nor a place not confirmed, nor a sitting three weeks off",
            not to & {f"{TAG.lower()}.{r}@example.invalid" for r in ("meet", "own", "wait", "far")},
            detail=str(to))
    s.check("one who asked not to be emailed is not, and the owner is told to ring",
            f"{TAG.lower()}.nomail@example.invalid" not in to and said and "ring them" in said,
            detail=str(said))
    said, again, refused = _ask(today)
    s.check("run again, nobody is asked twice", not again, detail=str(said))

    s.section("The owner's home page, until everybody has said and a car is booked")
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, today)
    warn = [w for w in warnings if f"{TAG} Retreat" in w["title"]]
    s.check("the sitting is named, with who has not said and that no car is booked",
            len(warn) == 1 and "4 guests not said" in warn[0]["detail"]
            and "no car booked" in warn[0]["detail"]
            and warn[0]["href"].endswith(f"/admin/workshops/sessions/{sid}/toulouse"),
            detail=str(warn))
    s.check("not a sitting three weeks off",
            not any(f"{TAG} Later" in w["title"] for w in warnings))
    found, _dropped = m.watch_task_findings(conn, today)
    task = [f for f in found if f[0] == "toulouse" and f"{TAG} Retreat" in f[1]]
    s.check("the run sheet is a task due the day before, on the calendar",
            len(task) == 1 and task[0][3] == (start - timedelta(days=1)).isoformat()
            and task[0][4] == "high", detail=str(task))
    s.check("saying who has not answered, and that there is no car",
            task and f"Ines {TAG}QUIET" in task[0][2] and "No car booked yet" in task[0][2])

    s.section("Somebody who rang is put down from the run sheet")
    r = oc.post(f"/admin/workshops/sessions/{sid}/toulouse/travel",
                data={"booking_id": quiet["id"], "travel_how": "meet",
                      "travel_detail": "Flight from Bristol, lands 10:15"})
    row = _row(conn, "QUIET")
    s.check("written down, with who took it", row["travel_how"] == "meet"
            and row["travel_told_by"] == owner["name"], detail=str(dict(row)))
    oc.post(f"/admin/workshops/sessions/{sid}/toulouse/travel",
            data={"booking_id": athome["id"], "travel_how": "meet"})
    s.check("a place on another sitting cannot be changed from this one",
            _row(conn, "HOME")["travel_how"] is None)
    oc.post(f"/admin/workshops/sessions/{sid}/toulouse/travel",
            data={"booking_id": _row(conn, "NOMAIL")["id"], "travel_how": "direct"})

    s.section("The car")
    conn.execute("INSERT INTO vehicles (name, created_at) VALUES (?, ?)", (f"{TAG} Van", _now()))
    conn.commit()
    van = conn.execute("SELECT id FROM vehicles WHERE name = ?", (f"{TAG} Van",)).fetchone()["id"]
    oc.post(f"/admin/workshops/sessions/{sid}/toulouse/book",
            data={"vehicle_id": van, "return_time": "half ten"})
    s.check("a drive back at a time that cannot be read books nothing",
            not m.toulouse_meeting(conn, sid)["drives"])
    oc.post(f"/admin/workshops/sessions/{sid}/toulouse/book",
            data={"vehicle_id": van, "driver_user_id": owner["id"], "return_time": "10:30"})
    tm = m.toulouse_meeting(conn, sid)
    up = tm["pickup"]
    down = next((d for d in tm["drives"] if d["direction"] == "dropoff"), None)
    s.check("the drive up is booked at midday on the first day, by the house's clock",
            up and m.house_date_iso(up["scheduled_at"]) == start.isoformat()
            and m.local_time_str(up["scheduled_at"]) == "12:00", detail=str(up and dict(up)))
    s.check("for the people meeting us, as an atelier's run on the van's own list",
            up and up["vehicle_id"] == van and up["transfer_type"] == "atelier"
            # The party of two who said so online, and the party of three who rang.
            and "5 guests meeting in Toulouse" in up["guest_name"], detail=str(up and dict(up)))
    s.check("and back on the last day at the time given",
            down and m.house_date_iso(down["scheduled_at"]) == tm["end"].isoformat()
            and m.local_time_str(down["scheduled_at"]) == "10:30")
    oc.post(f"/admin/workshops/sessions/{sid}/toulouse/book", data={"vehicle_id": van})
    s.check("pressed twice, it is booked once", len(m.toulouse_meeting(conn, sid)["drives"]) == 2)
    s.check("and All transfers shows it", f"{TAG} Retreat" in oc.get("/transfers").get_data(as_text=True))

    s.section("All of it closes itself")
    with m.app.test_request_context("/"):
        warn = [w for w in m.owner_home_warnings(conn, today) if f"{TAG} Retreat" in w["title"]]
    s.check("everybody has said and a car is booked: off the home page", not warn, detail=str(warn))
    found, _dropped = m.watch_task_findings(conn, today)
    task = [f for f in found if f[0] == "toulouse" and f"{TAG} Retreat" in f[1]]
    s.check("the task stays, as the run sheet, at ordinary priority",
            len(task) == 1 and task[0][4] == "normal" and f"Car: {TAG} Van" in task[0][2],
            detail=str(task))
    found, _dropped = m.watch_task_findings(conn, start + timedelta(days=1))
    s.check("and once the day has gone, it goes",
            not [f for f in found if f[0] == "toulouse" and f"{TAG} Retreat" in f[1]])

    s.section("The letter")
    shipped = {k: (sj, b) for k, _l, sj, b in m.DEFAULT_EMAIL_TEMPLATES}
    s.check("it cannot be saved without the link to answer on",
            "manage_url" in m.REQUIRED_MERGE_TAGS.get(m.TRAVEL_ASK_KEY, {})
            and m.wording_problems(m.TRAVEL_ASK_KEY, shipped[m.TRAVEL_ASK_KEY][0],
                                   shipped[m.TRAVEL_ASK_KEY][1].replace("{manage_url}", "")))
    s.check("and goes as shipped", not m.wording_problems(m.TRAVEL_ASK_KEY, *shipped[m.TRAVEL_ASK_KEY]))


if __name__ == "__main__":
    print(run().report())
