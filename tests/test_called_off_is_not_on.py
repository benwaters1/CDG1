"""A called-off atelier is not on.

call_off_session cancels every place, writes to everybody, closes the waiting
list and stamps the sitting cancelled_at -- and keeps the row, on purpose: why
an atelier did not run is worth knowing when deciding whether to put it on
again. test_called_off_frees_the_house covers the readers that hold the house
for an atelier. This file covers everything else that asks what is on.

They all read workshop_sessions for themselves, and none of them asked whether
the sitting was still running. Because the call-off cancels every place, a
called-off sitting read as the EMPTIEST sitting there was:

  - on the public pages it showed as wide open, and the register link under
    it took a deposit for an atelier that was not running. The waiting list
    took names for it and the guest's own page offered it as a date to move to
  - on the owner's home it sat at nought confirmed against its minimum, and
    as a task that could never close itself, until its start date
  - on the staff views it was a day with an atelier on: the calendar, the
    today sheet, the cover-gaps page asking for people, the leave warning
  - on the guest's itinerary it was "on at the chateau while you are here"

Nothing errored. Every page rendered perfectly, describing an atelier that was
not going to happen.

Four things carry this file.

  CALLED OFF THROUGH THE REAL PATH. The owner's button, not an UPDATE written
  here: a test that stamped cancelled_at itself would pass against a call-off
  that stamped something else.

  ASKED BEFORE AND AFTER. Every reader is asked first, while the sittings are
  running, and must show them. A check that a reader leaves a sitting out is
  worth nothing if the reader could never have shown it -- wrong dates, a
  LIMIT it never reached, a page that does not list ateliers at all.

  A LIVE SITTING IS STILL SHOWN. The cheapest way to pass the rest is to stop
  showing ateliers, so every reader is asked about a running sitting as well,
  and one atelier has a running date beside a called-off one, so leaving the
  whole atelier out is not a way through either.

  SAID, NOT DROPPED, WHERE THAT IS THE POINT. The person teaching a sitting is
  the one who would otherwise turn up, and the call-off writes to the guests,
  not to them. Their list keeps it and says it is off; their roster is for the
  next one that is running. The owner's admin pages already keep called-off
  sittings with a badge and are not changed here.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room, flashes, free_window
import _harness

m = _harness.m
TAG = "ZZCOP"
OFF = f"{TAG} Called"       # an atelier whose every date is called off
LIVE = f"{TAG} Running"     # an atelier still running, with one date called off


def _our_sessions(conn):
    return [r["id"] for r in conn.execute(
        """SELECT ws.id FROM workshop_sessions ws
             JOIN workshops w ON w.id = ws.workshop_id
            WHERE w.title LIKE ?""", (TAG + "%",)).fetchall()]


def _cleanup(since=None):
    """Everything this file writes, and everything the app writes because of it.

    The database is shared by every suite in a run. A sitting left behind here
    holds the house and sits on the owner's warnings for somebody else's test,
    which is a red belonging to another suite -- the kind that teaches people
    to ignore failures.
    """
    conn = db()
    ids = _our_sessions(conn)
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM workshop_bookings WHERE session_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM workshop_waitlist WHERE session_id IN ({marks})", ids)
        conn.execute(
            f"""DELETE FROM audit_log WHERE action = 'workshop_session_called_off'
                  AND target IN ({marks})""", [str(i) for i in ids])
        conn.execute(f"DELETE FROM workshop_sessions WHERE id IN ({marks})", ids)
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_materials WHERE workshop_id IN
                    (SELECT id FROM workshops WHERE title LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM stock_movements WHERE stock_item_id IN
                    (SELECT id FROM stock_items WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    if since:
        # The one public form here that is meant to succeed logs its attempt
        # against the rate limit, and every test client shares an address.
        conn.execute(
            """DELETE FROM submission_log
                WHERE action IN ('join_workshop_waitlist', 'register_workshop')
                  AND created_at >= ?""", (since,))
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atelier(title, instructor_id):
    conn = db()
    try:
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, active,
               instructor_name, instructor_user_id, created_at)
               VALUES (?, '', 400, 1, 'A Gilder', ?, ?)""",
            (title, instructor_id, _now()))
        conn.commit()
        return conn.execute("SELECT id FROM workshops WHERE title = ?",
                            (title,)).fetchone()["id"]
    finally:
        conn.close()


def _sitting(workshop_id, start, end, minimum=0):
    conn = db()
    try:
        cur = conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
               capacity, min_participants, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
            (workshop_id, start.isoformat(), end.isoformat(), minimum, _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _place(session_id, ref, status="confirmed"):
    conn = db()
    try:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
               guest_name, guest_email, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, 2, ?, 800, ?)""",
            (session_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
             f"zzcop.{ref}@example.invalid".lower(), status, _now()))
        conn.commit()
    finally:
        conn.close()


def _materials(workshop_ids):
    """Something every sitting of these ateliers needs and the shelf has none of."""
    conn = db()
    try:
        cur = conn.execute(
            """INSERT INTO stock_items (name, category, unit, reorder_level,
                                        unit_cost, active, created_at)
               VALUES (?, 'other', 'book', 0, 12.0, 1, ?)""",
            (f"{TAG} Gold leaf", _now()))
        for wid in workshop_ids:
            conn.execute(
                """INSERT INTO workshop_materials (workshop_id, stock_item_id,
                   qty_per_person, qty_per_session, created_at) VALUES (?, ?, 0, 5, ?)""",
                (wid, cur.lastrowid, _now()))
        conn.commit()
    finally:
        conn.close()


def _one(sql, args=()):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _ask(fn, *args, **kwargs):
    """One question, on its own connection and in a request of its own."""
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return fn(conn, *args, **kwargs)
    finally:
        conn.close()


def _days(first, last):
    return [(first + timedelta(days=i)) for i in range((last - first).days + 1)]


def _page(client, url, **kw):
    r = client.get(url, **kw)
    return r.status_code, r.get_data(as_text=True)


def _public():
    """A visitor with nothing in their session -- no flash from the last page
    carrying an atelier's name onto this one."""
    return m.app.test_client()


def _register_link(sid):
    return f'/workshops/register/{sid}"'


def _calendar_marks(when, days):
    """Whether the dashboard calendar marks each of these days as an atelier day."""
    cal = _ask(m.build_dashboard_calendar, when)
    cells = {c["date"]: c["workshop"] for w in cal["weeks"] for c in w if c}
    return {d: cells[d] for d in days if d in cells}


def _markers(when, days):
    """The owner home's marker for each of these days, in `when`'s month."""
    cells, _lead, _avg = _ask(m.owner_home_occupancy, when)
    by_day = {c["day"]: c["marker"] for c in cells}
    return {d: by_day[d.day] for d in days if d.month == when.month}


def _sessions_running(first):
    """The Workshops band's "Sessions running" for the week holding `first`.

    The window is resolve_period's, built from ?period=week&date= as the page
    builds it, not written out here. A hand-built one handed the band's refunds
    query bare dates where the page hands it moments, and it would miss any
    key the band starts reading later. A week is wide enough to hold a sitting
    and narrow enough to leave out the other window's, twelve days on.
    """
    period = m.resolve_period("week", first.isoformat())
    cells = _ask(m.workshops_overview, period, m.house_today())
    return next(c["value"] for c in cells if c["label"] == "Sessions running")


def _warning_count(fragment):
    rows = _ask(m.owner_home_warnings, m.house_today())
    hit = [w for w in rows if fragment in w["title"]]
    return (hit[0]["count"] if hit else 0), " ".join(
        f"{w['title']} {w['detail']}" for w in rows)


def _itinerary_ateliers(arrive, leave):
    booking = {"arrival_date": arrive.isoformat(), "departure_date": leave.isoformat(),
               "guest_email": "zzcop.stay@example.invalid",
               "estimated_arrival_time": "", "room_name": "A room",
               "extras_summary": "", "transfer_flight_number": "",
               "transfer_arrival_time": ""}
    days = _ask(m.stay_itinerary, booking)
    return [e["what"] for d in days for e in d["entries"]
            if e["kind"] in ("atelier", "atelier_yours")]


def _teaching_rows(html):
    """The instructor's list as (atelier, what the line says) pairs."""
    if "Workshops You're Teaching" not in html:
        return []
    block = html.split("Workshops You're Teaching", 1)[1].split("<h2", 1)[0]
    out = []
    for row in re.findall(r'<div class="mini-row">(.*?)</div>', block, re.S):
        name = re.search(r'<span class="mini-name">(.*?)</span>', row, re.S)
        role = re.search(r'<span class="mini-role">(.*)</span>', row, re.S)
        if name and name.group(1).startswith(TAG):
            out.append((name.group(1).strip(), re.sub(r"<[^>]+>", "", role.group(1)).strip()
                        if role else ""))
    return out


def _watch_tasks(fragment):
    conn = db()
    try:
        return {r["title"]: r["status"] for r in conn.execute(
            "SELECT title, status FROM tasks WHERE origin = ? AND title LIKE ?",
            (m.WATCH_TASK_ORIGIN, f"%{fragment}%")).fetchall()}
    finally:
        conn.close()


def run():
    s = Suite("A called-off atelier is not on")
    started = _now()
    _cleanup()
    oc, ec, _owner, emp = clients()
    sent = []
    was = (m.send_email, m.send_workshop_email, m.send_event_email)
    m.send_email = lambda to, subj, body, **k: (sent.append((to, subj)), True)[1]
    m.send_workshop_email = lambda conn, b, key, ctx, **k: sent.append(
        (b["guest_email"], key)) or True
    m.send_event_email = lambda conn, inquiry, key, ctx, **k: True

    # generate_watch_tasks rebuilds the whole house's watch list, so what it
    # makes and closes for OTHER findings is put back exactly afterwards.
    conn = db()
    task_high = conn.execute("SELECT COALESCE(MAX(id), 0) AS c FROM tasks").fetchone()["c"]
    task_rows = [dict(r) for r in conn.execute(
        """SELECT id, status, completed_at, notes, priority, due_date, assigned_to_user_id
             FROM tasks WHERE origin = ?""", (m.WATCH_TASK_ORIGIN,)).fetchall()]
    conn.close()

    try:
        room = ensure_room()
        today = m.house_today()
        # Two quiet windows well ahead for the readers that ask about a span,
        # and today itself for the ones that only ever ask about now.
        w_off = free_window(room["id"], 10, after_days=40, clear_of_ateliers=True)
        w_live = free_window(room["id"], 10, after_days=(w_off - today).days + 12,
                             clear_of_ateliers=True)
        now_start, now_end = today, today + timedelta(days=3)
        off_start, off_end = w_off + timedelta(days=2), w_off + timedelta(days=5)
        live_start, live_end = w_live + timedelta(days=2), w_live + timedelta(days=5)
        off_days, live_days = _days(off_start, off_end), _days(live_start, live_end)

        # What the span-readers say with no atelier of ours at all. The
        # call-off has to put them back to exactly this.
        base_cal_off = _calendar_marks(off_start, off_days)
        base_mark_off = _markers(off_start, off_days)
        base_mark_live = _markers(live_start, live_days)
        base_running_off = _sessions_running(off_start)
        base_running_live = _sessions_running(live_start)

        w_called = _atelier(OFF, emp["id"])
        w_running = _atelier(LIVE, emp["id"])
        # Created in this order so that, reading them in the order they were
        # made, the called-off one comes first wherever two share a date.
        off_now = _sitting(w_called, now_start, now_end, minimum=6)
        live_now = _sitting(w_running, now_start, now_end, minimum=6)
        off_far = _sitting(w_called, off_start, off_end)
        gone = _sitting(w_running, off_start, off_end)       # a running atelier's called-off date
        live_far = _sitting(w_running, live_start, live_end)
        for sid, ref in ((off_now, "OFFNOW"), (live_now, "LIVENOW"), (off_far, "OFFFAR"),
                         (gone, "GONE"), (live_far, "LIVEFAR")):
            _place(sid, ref)
        _place(live_far, "MOVER", status="pending")         # somebody who may want to move
        _materials([w_called, w_running])
        mover = f"tok{TAG}MOVER".lower()

        # ------------------------------------------------------------------
        s.section("Before anything is called off, every reader shows them")
        # The precondition. If one of these fails, the check after the
        # call-off is asking a reader that could never have said yes.
        code, body = _page(_public(), "/workshops")
        s.check("the public list shows the atelier", code == 200 and OFF in body,
                detail=f"HTTP {code}")
        s.check("with every date's register link",
                all(_register_link(i) in body for i in (off_now, live_now, gone, live_far)))
        code, body = _page(_public(), f"/workshops/{w_running}")
        s.check("the atelier's own page lists the date that will be called off",
                code == 200 and _register_link(gone) in body, detail=f"HTTP {code}")
        code, _b = _page(_public(), f"/workshops/register/{off_now}")
        s.check("its register form opens", code == 200, detail=f"HTTP {code}")
        code, body = _page(_public(), "/sitemap.xml")
        s.check("the sitemap lists the atelier", f"/workshops/{w_called}</loc>" in body)
        code, body = _page(_public(), "/")
        s.check("the front page's next dates show it", OFF in body, detail=f"HTTP {code}")
        code, body = _page(_public(), f"/workshops/manage/{mover}")
        s.check("the guest's own page offers it as a date to move to",
                f'value="{gone}"' in body, detail=f"HTTP {code}")

        risky = {w["session"]["id"] for w in _ask(m.sessions_at_risk, today)}
        s.check("the owner is told both are short of their number",
                {off_now, live_now} <= risky, detail=f"{risky}")
        short = {p["session"]["id"] for p in _ask(m.sessions_short_of_materials, today)}
        s.check("and short of materials", {off_now, live_now} <= short, detail=f"{short}")
        risk_before, said = _warning_count("short of the number to run")
        mats_before, _said = _warning_count("without the materials to run")
        s.check("on the owner's home", risk_before >= 2 and mats_before >= 2,
                detail=f"{risk_before} short, {mats_before} without materials: {said}")
        _ask(m.generate_watch_tasks, today)
        raised = _watch_tasks(OFF)
        s.check("and as tasks, one for each",
                sum(1 for st in raised.values() if st != "done") == 2, detail=f"{raised}")

        s.check("the dashboard calendar marks its days",
                all(_calendar_marks(off_start, off_days).values()),
                detail=f"{_calendar_marks(off_start, off_days)}")
        quiet = [d for d, mk in base_mark_off.items() if mk == "none"]
        s.check("there is a day on the owner's month with nothing else to mark", bool(quiet),
                detail=f"{base_mark_off} — every day already has an arrival or a "
                       "departure, so the marker could never show the atelier")
        marks = _markers(off_start, off_days)
        s.check("and the owner's month marks it", all(marks[d] == "workshop" for d in quiet),
                detail=f"{marks}")
        s.check("the overview band counts it running",
                _sessions_running(off_start) == base_running_off + 2,
                detail=f"{_sessions_running(off_start)} against "
                       f"{base_running_off} without it")
        s.check("the week's activity names it",
                OFF in _ask(m.week_activity, off_start, off_end))
        gaps = _ask(m.cover_gaps, off_start, off_end)
        s.check("cover gaps count it as work",
                any(OFF in d["workshops"] for d in gaps), detail=f"{gaps}")
        s.check("the leave warning names it", any(
            OFF in n for n in _ask(m.leave_impact, off_start.isoformat(),
                                   off_end.isoformat())["notes"]))
        s.check("a guest staying then sees it on their itinerary",
                OFF in _itinerary_ateliers(off_start - timedelta(days=1),
                                           off_end + timedelta(days=1)))
        margins = {r["session_id"] for r in _ask(
            m.workshop_margins, today - timedelta(days=1), live_end + timedelta(days=30))["rows"]}
        s.check("the margins page has a row for it", {off_now, off_far} <= margins)
        s.check("what is on offer, as a reply is drafted, includes it",
                OFF in {w["title"] for w in
                        _ask(m.current_offerings_snapshot)["upcoming_workshop_sessions"]})
        code, body = _page(oc, "/admin/today-sheet")
        s.check("the today sheet lists it", code == 200 and OFF in body, detail=f"HTTP {code}")
        code, body = _page(oc, f"/admin/team-calendar?month={today:%Y-%m}")
        s.check("the team calendar names it", code == 200 and OFF in body, detail=f"HTTP {code}")
        s.check("the owner's digest lists it", f"{OFF}: starts" in _ask(
            lambda conn: m.build_owner_digest(conn)))
        code, body = _page(ec, "/")
        s.check("the instructor's list has it", code == 200 and any(
            n == OFF for n, _w in _teaching_rows(body)), detail=f"{_teaching_rows(body)}")

        # ------------------------------------------------------------------
        s.section("Calling three dates off, through the owner's button")
        for sid in (off_now, off_far, gone):
            said = " ".join(flashes(oc.post(f"/admin/workshops/session/{sid}/call-off",
                                            data={"reason": "Not enough takers."},
                                            follow_redirects=True)))
            s.check(f"sitting {sid} is marked off",
                    _one("SELECT cancelled_at FROM workshop_sessions WHERE id = ?",
                         (sid,))["cancelled_at"] is not None, detail=said)
        s.check("the running dates are untouched", all(
            _one("SELECT cancelled_at FROM workshop_sessions WHERE id = ?",
                 (sid,))["cancelled_at"] is None for sid in (live_now, live_far)))

        # ------------------------------------------------------------------
        s.section("The public pages: nothing to book")
        code, body = _page(_public(), "/workshops")
        s.check("an atelier with every date called off is off the list",
                code == 200 and OFF not in body,
                detail="its places were all cancelled, so it read as wide open")
        s.check("a called-off date's register link is gone",
                not any(_register_link(i) in body for i in (off_now, gone)))
        s.check("while the running atelier stays, with its running dates",
                LIVE in body and _register_link(live_now) in body
                and _register_link(live_far) in body)
        code, body = _page(_public(), f"/workshops/{w_running}")
        s.check("its own page drops the called-off date",
                code == 200 and _register_link(gone) not in body, detail=f"HTTP {code}")
        s.check("and keeps the ones running",
                _register_link(live_now) in body and _register_link(live_far) in body)
        r = _public().get(f"/workshops/{w_called}", follow_redirects=True)
        said = " ".join(flashes(r))
        s.check("the called-off atelier's page sends the visitor to what is on",
                r.request.path == "/workshops", detail=f"landed on {r.request.path}")
        s.check("without saying it has finished, which it never did",
                "no dates coming up" in said and "has finished" not in said, detail=said)

        # The worst of it: a deposit taken for an atelier that is not running.
        code, _b = _page(_public(), f"/workshops/register/{off_now}")
        s.check("the register form for a called-off date does not open", code == 302,
                detail=f"HTTP {code}")
        before = _one("SELECT COUNT(*) AS c FROM workshop_bookings WHERE session_id = ?",
                      (off_now,))["c"]
        r = _public().post(f"/workshops/register/{off_now}", data={
            "guest_name": "ZZCOP Walk-in", "guest_email": "zzcop.walkin@example.invalid",
            "party_size": "1", "occupancy_type": "double"}, follow_redirects=True)
        said = " ".join(flashes(r))
        after = _one("SELECT COUNT(*) AS c FROM workshop_bookings WHERE session_id = ?",
                     (off_now,))["c"]
        s.check("posting it anyway registers nobody", after == before,
                detail=f"{after - before} registration(s) made on a called-off date")
        s.check("and says why", "called off" in said, detail=said)
        r = _public().get(f"/workshops/register/{gone}", follow_redirects=True)
        body = r.get_data(as_text=True)
        s.check("an old link to a running atelier's called-off date lands on its running dates",
                r.request.path == f"/workshops/{w_running}"
                and _register_link(live_now) in body, detail=f"landed on {r.request.path}")
        code, _b = _page(_public(), f"/workshops/register/{live_now}")
        s.check("a running date's register form still opens", code == 200,
                detail=f"HTTP {code}")

        r = _public().post("/workshops/waitlist/join", data={
            "session_id": str(off_now), "name": "ZZCOP Waiter",
            "email": "zzcop.waiter@example.invalid", "party_size": "1"},
            follow_redirects=True)
        said = " ".join(flashes(r))
        s.check("its waiting list takes nobody", not _one(
            "SELECT 1 FROM workshop_waitlist WHERE session_id = ?", (off_now,)),
            detail=said)
        s.check("and says why", "called off" in said, detail=said)
        _public().post("/workshops/waitlist/join", data={
            "session_id": str(live_now), "name": "ZZCOP Waiter",
            "email": "zzcop.waiter@example.invalid", "party_size": "1"})
        s.check("a running date's waiting list still does", bool(_one(
            "SELECT 1 FROM workshop_waitlist WHERE session_id = ? AND status = 'open'",
            (live_now,))))

        code, body = _page(_public(), f"/workshops/manage/{mover}")
        s.check("the guest's own page no longer offers it to move to",
                code == 200 and f'value="{gone}"' not in body, detail=f"HTTP {code}")
        s.check("but still offers the date that is running", f'value="{live_now}"' in body)
        r = _public().post(f"/workshops/manage/{mover}", data={
            "action": "change_session", "new_session_id": str(gone)}, follow_redirects=True)
        said = " ".join(flashes(r))
        s.check("and a guest who posts it anyway is not moved onto it",
                _one("SELECT session_id FROM workshop_bookings WHERE reference_code = ?",
                     (f"{TAG}-MOVER",))["session_id"] == live_far, detail=said)
        code, body = _page(_public(), "/sitemap.xml")
        s.check("the sitemap drops the called-off atelier",
                f"/workshops/{w_called}</loc>" not in body)
        s.check("and keeps the running one", f"/workshops/{w_running}</loc>" in body)
        code, body = _page(_public(), "/")
        s.check("the front page's next dates drop it", OFF not in body, detail=f"HTTP {code}")
        s.check("and keep the running one", LIVE in body)

        # ------------------------------------------------------------------
        s.section("The owner: dealt with, so nothing left to warn about")
        risky = {w["session"]["id"] for w in _ask(m.sessions_at_risk, today)}
        s.check("a called-off date is not at risk -- calling it off was the decision",
                off_now not in risky, detail=f"{risky}")
        s.check("the running one short of its number still is", live_now in risky)
        short = {p["session"]["id"] for p in _ask(m.sessions_short_of_materials, today)}
        s.check("nor short of materials it will never use", off_now not in short,
                detail=f"{short}")
        s.check("the running one short of materials still is", live_now in short)
        risk_after, said = _warning_count("short of the number to run")
        s.check("the owner's home counts one fewer short of its number",
                risk_after == risk_before - 1, detail=f"{risk_after} against {risk_before}")
        mats_after, said_m = _warning_count("without the materials to run")
        s.check("and one fewer without materials", mats_after == mats_before - 1,
                detail=f"{mats_after} against {mats_before}")
        s.check("naming the called-off atelier nowhere", OFF not in said + said_m,
                detail=said + said_m)
        _ask(m.generate_watch_tasks, today)
        tasks = _watch_tasks(OFF)
        # Every one CLOSED, not just the ones raised before. With nought
        # confirmed after the call-off, a reader that still counted it would
        # close "2 of 6 needed" and open "0 of 6 needed" -- a task that looks
        # like it closed itself and then comes straight back.
        s.check("its tasks close themselves, and none comes back in their place",
                tasks and all(st == "done" for st in tasks.values()), detail=f"{tasks}")
        s.check("while the running one's tasks stay open", sum(
            1 for st in _watch_tasks(LIVE).values() if st != "done") == 2,
            detail=f"{_watch_tasks(LIVE)}")
        s.check("the dashboard calendar is back to how it was",
                _calendar_marks(off_start, off_days) == base_cal_off,
                detail=f"{_calendar_marks(off_start, off_days)} against {base_cal_off}")
        s.check("and still marks the running one's days",
                all(_calendar_marks(live_start, live_days).values()),
                detail=f"{_calendar_marks(live_start, live_days)}")
        s.check("the owner's month is back to how it was",
                _markers(off_start, off_days) == base_mark_off,
                detail=f"{_markers(off_start, off_days)} against {base_mark_off}")
        live_marks = _markers(live_start, live_days)
        s.check("and still marks the running one", all(
            live_marks[d] == "workshop" for d, mk in base_mark_live.items() if mk == "none")
            and any(mk == "none" for mk in base_mark_live.values()), detail=f"{live_marks}")
        s.check("the overview band no longer counts it running",
                _sessions_running(off_start) == base_running_off,
                detail=f"{_sessions_running(off_start)} against {base_running_off}")
        s.check("and still counts the running one",
                _sessions_running(live_start) == base_running_live + 1)
        margins = {r["session_id"] for r in _ask(
            m.workshop_margins, today - timedelta(days=1), live_end + timedelta(days=30))["rows"]}
        s.check("the margins page shows no loss for an atelier that never ran",
                not {off_now, off_far, gone} & margins, detail=f"{margins}")
        s.check("and still has the running ones", {live_now, live_far} <= margins)
        code, body = _page(oc, "/admin/today-sheet")
        s.check("the today sheet does not list it as running today",
                code == 200 and OFF not in body, detail=f"HTTP {code}")
        s.check("and still lists the one that is", LIVE in body)
        digest = _ask(lambda conn: m.build_owner_digest(conn))
        s.check("the owner's digest does not report it starting this week",
                OFF not in digest, detail=digest[-400:])
        s.check("and still reports the running one", f"{LIVE}: starts" in digest)
        offered = {w["title"] for w in
                   _ask(m.current_offerings_snapshot)["upcoming_workshop_sessions"]}
        s.check("a reply drafted from what is on offer cannot offer it",
                OFF not in offered, detail=f"{offered}")
        s.check("and can still offer the running one", LIVE in offered)

        # ------------------------------------------------------------------
        s.section("The staff and the guests: not a day with an atelier on")
        code, body = _page(oc, f"/admin/team-calendar?month={today:%Y-%m}")
        s.check("the team calendar does not name it", code == 200 and OFF not in body,
                detail=f"HTTP {code}")
        s.check("and still names the running one", LIVE in body)
        gaps = _ask(m.cover_gaps, off_start, off_end)
        s.check("cover gaps ask nobody to work it",
                not any(TAG in t for d in gaps for t in d["workshops"]), detail=f"{gaps}")
        s.check("and still count the running one", any(
            LIVE in d["workshops"] for d in _ask(m.cover_gaps, live_start, live_end)))
        notes = _ask(m.leave_impact, off_start.isoformat(), off_end.isoformat())["notes"]
        s.check("the leave warning does not argue against leave with it",
                not any(TAG in n for n in notes), detail=f"{notes}")
        s.check("and still names the running one", any(LIVE in n for n in _ask(
            m.leave_impact, live_start.isoformat(), live_end.isoformat())["notes"]))
        said = _ask(m.week_activity, off_start, off_end)
        s.check("the week's activity does not offer it as the reason for a long week",
                TAG not in said, detail=said)
        s.check("and still names the running one",
                LIVE in _ask(m.week_activity, live_start, live_end))
        seen = _itinerary_ateliers(off_start - timedelta(days=1), off_end + timedelta(days=1))
        s.check("a guest staying then is not told it is on", not any(TAG in w for w in seen),
                detail=f"{seen}")
        s.check("a guest staying for the running one still is", LIVE in _itinerary_ateliers(
            live_start - timedelta(days=1), live_end + timedelta(days=1)))

        # ------------------------------------------------------------------
        s.section("Whoever is teaching it is told, not left to find out")
        code, body = _page(ec, "/")
        rows = _teaching_rows(body)
        s.check("their dashboard opens", code == 200, detail=f"HTTP {code}")
        called = [w for n, w in rows if n == OFF]
        s.check("the called-off sitting stays on their list", len(called) == 2,
                detail=f"{rows}")
        s.check("saying it is called off", called and all("called off" in w for w in called),
                detail=f"{rows}")
        gone_line = [w for n, w in rows if n == LIVE and w.startswith(off_start.isoformat())]
        s.check("including the running atelier's called-off date",
                gone_line and "called off" in gone_line[0], detail=f"{rows}")
        running = [w for n, w in rows if n == LIVE and not w.startswith(off_start.isoformat())]
        s.check("the running ones read as running",
                len(running) == 2 and all("confirmed" in w and "called off" not in w
                                          for w in running), detail=f"{rows}")
        s.check("and the roster is for the next one that is running",
                f"{LIVE} — Roster" in body and f"{OFF} — Roster" not in body,
                detail="a called-off sitting's roster is everybody cancelled")
    finally:
        m.send_email, m.send_workshop_email, m.send_event_email = was
        conn = db()
        conn.execute("DELETE FROM tasks WHERE id > ?", (task_high,))
        for row in task_rows:
            conn.execute(
                """UPDATE tasks SET status = ?, completed_at = ?, notes = ?, priority = ?,
                     due_date = ?, assigned_to_user_id = ? WHERE id = ?""",
                (row["status"], row["completed_at"], row["notes"], row["priority"],
                 row["due_date"], row["assigned_to_user_id"], row["id"]))
        conn.commit()
        conn.close()
        _cleanup(since=started)
    return s


if __name__ == "__main__":
    print(run().report())
