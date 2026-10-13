"""The figures on the Team, HR, Payroll, Rota and Estate pages count the right things.

WHAT WENT WRONG. A read-through of the owner's pages on 13 October found
figures that were sums of the wrong thing, none of which errored:
- Leave was added up per REQUEST, so the same days asked for twice were taken
  twice -- a balance of "taken 153 · -143 left" -- and nothing stopped
  anybody asking twice. The payroll pack and HR also clipped a request to the
  day after their window and added one, so leave running past the end of a
  month gained a day. Those figures go into the payroll CSV.
- "On leave" on HR and Team counted requests, not people.
- Team and HR counted hours gross of breaks and included the owner's own
  clock-ins, so the same month read differently there and on Payroll.
- The insured value counted a policy that had lapsed as cover.
- Breakages netted every recovery against every cost, so a vase charged above
  its price paid for somebody else's glass, and undecided ones were absorbed.
- Rota against clock reported the owner's days as unplanned team hours and
  counted a guessed clock-out in full; the staffing strip counted leavers and
  people on leave as cover; room faults counted stay-and-fault pairs; a review
  was overdue from a year after joining, whatever had happened since; a
  certificate still to expire read as waiting minus seven days; and the
  Payroll "People" tile counted only those with hours.

WHAT THIS PINS each of those, with fixtures far enough ahead (three years) or
specific enough that the copy of the real database cannot crowd them.
"""
import re
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ztest-tf-"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE email LIKE ?", (TAG + "%",)).fetchall()]
    marks = ",".join("?" * len(ids)) or "NULL"
    for table in ("leave_requests", "absences", "shifts", "certifications",
                  "performance_reviews"):
        conn.execute(f"DELETE FROM {table} WHERE user_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM breaks WHERE time_entry_id IN (SELECT id FROM time_entries "
                 f"WHERE user_id IN ({marks}))", ids)
    conn.execute(f"DELETE FROM time_entries WHERE user_id IN ({marks})", ids)
    # The owner's entries are not a fixture user's: they are stamped at :37
    # seconds exactly, which a real clock-in (with microseconds) never is.
    conn.execute("DELETE FROM time_entries WHERE clock_in_at LIKE '%:37+00:00'")
    conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    conn.execute("DELETE FROM assets WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM insurance_policies WHERE provider LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM breakages WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG.upper() + "%",))
    conn.commit()
    conn.close()


def _person(conn, name, *, status="active", start_date=None, created_at=None):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           start_date, created_at) VALUES (?, 'x', 'employee', ?, 'General', ?, ?, ?)""",
        (f"{TAG}{name.lower()}@example.invalid", f"{TAG}{name}", status, start_date,
         created_at or _now()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name.lower()}@example.invalid",)).fetchone()["id"]


def _leave(conn, uid, a, b, status="approved"):
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type,
           status, requested_at) VALUES (?, ?, ?, ?, 'vacation', ?, ?)""",
        (uid, a.isoformat(), b.isoformat(), TAG + "leave", status, _now()))
    conn.commit()


def _absence(conn, uid, a, b):
    conn.execute(
        """INSERT INTO absences (user_id, start_date, end_date, kind, reason, created_at)
           VALUES (?, ?, ?, 'sick', ?, ?)""",
        (uid, a.isoformat(), b.isoformat(), TAG + "absence", _now()))
    conn.commit()


def _clock(conn, uid, day, hours=8, *, at_hour=9, auto_closed=0, break_hours=0):
    start = datetime(day.year, day.month, day.day, at_hour, 0, 37, tzinfo=m.LOCAL_TZ)
    end = start + timedelta(hours=hours)
    cur = conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) "
        "VALUES (?, ?, ?, ?)",
        (uid, start.astimezone(timezone.utc).isoformat(),
         end.astimezone(timezone.utc).isoformat(), auto_closed))
    if break_hours:
        b0 = start + timedelta(hours=3)
        conn.execute("INSERT INTO breaks (time_entry_id, start_at, end_at) VALUES (?, ?, ?)",
                     (cur.lastrowid, b0.astimezone(timezone.utc).isoformat(),
                      (b0 + timedelta(hours=break_hours)).astimezone(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid


def _shift(conn, uid, day):
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at)
           VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (uid, day.isoformat(), TAG + "shift", _now()))
    conn.commit()


def _tiles(html):
    """{label: (value, hint)} read off an overview band as the browser shows it."""
    out = {}
    for mt in re.finditer(
            r'<div class="overview-value">(.*?)</div>\s*<div class="overview-label">(.*?)</div>'
            r'(?:\s*<div class="overview-hint">(.*?)</div>)?', html, re.S):
        value = re.sub(r"<[^>]+>", " ", mt.group(1))
        value = re.split(r"\s+", value.strip())[0] if value.strip() else ""
        out[re.sub(r"\s+", " ", mt.group(2)).strip()] = (value, (mt.group(3) or "").strip())
    return out


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("€", ""))
    except ValueError:
        return None


def run():
    s = Suite("Team figures")
    _cleanup()
    oc, _ec, owner, _emp = clients()
    today = house_today()
    y = today.year + 3
    march = m.resolve_period("month", f"{y}-03-01")
    conn = db()
    ann = _person(conn, "Ann")
    bea = _person(conn, "Bea")
    left = _person(conn, "Gone", status="inactive")

    # Ann asks for the same days twice, overlapping, and for a week that
    # runs over the end of March. Bea takes one day and two sick.
    _leave(conn, ann, date(y, 3, 10), date(y, 3, 12))
    _leave(conn, ann, date(y, 3, 11), date(y, 3, 13))
    _leave(conn, ann, date(y, 3, 31), date(y, 4, 2))
    _leave(conn, bea, date(y, 3, 15), date(y, 3, 15))
    _absence(conn, bea, date(y, 3, 20), date(y, 3, 21))
    _absence(conn, ann, date(y, 3, 31), date(y, 4, 1))

    s.section("A day off is counted once")
    days = m.distinct_days([{"start_date": f"{y}-03-10", "end_date": f"{y}-03-12"},
                            {"start_date": f"{y}-03-11", "end_date": f"{y}-03-13"}],
                           date(y, 3, 1), date(y, 3, 31))
    s.check("two requests sharing two days cover four days, not six", len(days) == 4,
            detail=str(sorted(days)))
    taken = m.leave_days_taken(conn, ann, date(y, 1, 1), date(y, 12, 31))
    s.check("the leave balance takes Ann's overlapping requests once (7 days, not 9)",
            taken == 7, detail=str(taken))

    s.section("The payroll pack counts days inside its month")
    rows = {r["user_id"]: r for r in m.payroll_period_rows(conn, march)}
    s.check("leave that runs past the end of March counts one March day, and the "
            "overlap once: Ann 5, not 8",
            rows.get(ann, {}).get("leave_days") == 5, detail=str(rows.get(ann)))
    s.check("an absence running into April counts one March day, not two",
            rows.get(ann, {}).get("absence_days") == 1, detail=str(rows.get(ann)))
    s.check("Bea's one day of leave and two sick are one and two",
            rows.get(bea, {}).get("leave_days") == 1 and rows.get(bea, {}).get("absence_days") == 2,
            detail=str(rows.get(bea)))

    s.section("HR counts people and days, not requests")
    hr = _tiles(oc.get(f"/admin/hr?period=month&date={y}-03-01").get_data(as_text=True))
    s.check("On leave is two people, not four requests",
            _num(hr.get("On leave", ("",))[0]) == 2, detail=str(hr.get("On leave")))
    s.check("Absence days inside March are three, not four",
            _num(hr.get("Absence days", ("",))[0]) == 3, detail=str(hr.get("Absence days")))

    s.section("Hours are one figure: the team's, net of breaks")
    _clock(conn, ann, date(y, 3, 5), 8, break_hours=1)
    _clock(conn, owner["id"], date(y, 3, 5), 8)
    s.check("team_hours leaves out the owner and takes off the break: 7, not 16",
            round(m.team_hours(conn, march["start_at"], march["end_at"]), 2) == 7.0,
            detail=str(m.team_hours(conn, march["start_at"], march["end_at"])))
    hr = _tiles(oc.get(f"/admin/hr?period=month&date={y}-03-01").get_data(as_text=True))
    s.check("HR's Hours worked says the same", _num(hr.get("Hours worked", ("",))[0]) == 7.0,
            detail=str(hr.get("Hours worked")))
    cells = {c["label"]: c for c in m.employee_overview(conn, march, today)}
    s.check("and so does Team's", _num(cells.get("Hours worked", {}).get("value")) == 7.0,
            detail=str(cells.get("Hours worked")))
    pay = _tiles(oc.get(f"/admin/payroll?period=month&date={y}-03-01").get_data(as_text=True))
    s.check("and so does the payroll pack", _num(pay.get("Hours", ("",))[0]) == 7.0,
            detail=str(pay.get("Hours")))
    people = len(m.payroll_period_rows(conn, march))
    s.check("Payroll's People is everybody on the table, with how many worked",
            _num(pay.get("People", ("",))[0]) == people
            and pay.get("People", ("", ""))[1] == "1 with hours",
            detail=f"{pay.get('People')} for {people} rows")

    s.section("On leave today is people, not requests")
    before = {c["label"]: c for c in m.employee_overview(conn, march, today)}["On leave today"]["value"]
    cat = _person(conn, "Cat")
    _leave(conn, cat, today - timedelta(days=1), today + timedelta(days=1))
    _leave(conn, cat, today, today + timedelta(days=2))
    after = {c["label"]: c for c in m.employee_overview(conn, march, today)}["On leave today"]["value"]
    s.check("one person with two requests over today adds one", after == before + 1,
            detail=f"{before} -> {after}")

    s.section("Nobody can ask for the same days twice")
    me = m.app.test_client()
    with me.session_transaction() as sess:
        sess["user_id"] = ann
    me.post("/leave", data={"start_date": f"{y}-06-01", "end_date": f"{y}-06-05",
                            "reason": TAG + "june", "leave_type": "vacation"})
    second = me.post("/leave", data={"start_date": f"{y}-06-03", "end_date": f"{y}-06-08",
                                     "reason": TAG + "june again", "leave_type": "vacation"},
                     follow_redirects=True).get_data(as_text=True)
    me.post("/leave", data={"start_date": f"{y}-06-10", "end_date": f"{y}-06-12",
                            "reason": TAG + "later", "leave_type": "vacation"})
    june = conn.execute(
        """SELECT start_date FROM leave_requests WHERE user_id = ? AND start_date LIKE ?
            ORDER BY start_date""", (ann, f"{y}-06-%")).fetchall()
    s.check("the overlapping request is refused, the separate one is not",
            [r["start_date"] for r in june] == [f"{y}-06-01", f"{y}-06-10"],
            detail=str([r["start_date"] for r in june]))
    s.check("and the refusal says which request it clashes with",
            "already asked for time off" in second and f"1 June {y}" in second)

    s.section("Insured means a policy in force")
    base = m.asset_summary(conn, today)
    for provider, expiry in ((TAG + "Lapsed", today - timedelta(days=30)),
                             (TAG + "Current", today + timedelta(days=300))):
        conn.execute("INSERT INTO insurance_policies (provider, expiry_date, created_at) "
                     "VALUES (?, ?, ?)", (provider, expiry.isoformat(), _now()))
    lapsed, current = (conn.execute("SELECT id FROM insurance_policies WHERE provider = ?",
                                    (TAG + w,)).fetchone()["id"] for w in ("Lapsed", "Current"))
    for name, value, policy in ((TAG + "Commode", 12000, lapsed), (TAG + "Lamp", 500, current)):
        conn.execute("INSERT INTO assets (name, estimated_value, insurance_policy_id, status, "
                     "created_at) VALUES (?, ?, ?, 'held', ?)", (name, value, policy, _now()))
    conn.commit()
    now_ = m.asset_summary(conn, today)
    s.check("a €12,000 piece on a lapsed policy adds nothing to the insured value",
            round(now_["insured"] - base["insured"], 2) == 500,
            detail=f"{base['insured']} -> {now_['insured']}")
    s.check("and is named among the valuable things without cover",
            any(r["name"] == TAG + "Commode" for r in now_["uninsured_significant"]))
    reg = oc.get("/admin/assets").get_data(as_text=True)
    s.check("the register has a Cover lapsed chip and says so on the piece",
            "Cover lapsed" in reg and "cover lapsed</strong>" in reg)

    s.section("Breakages: what the house lost on each, not one net figure")
    base = m.breakage_summary(conn, today=today)
    found = (today - timedelta(days=10)).isoformat()
    for what, cost, charged, decision in ((TAG + "Vase", 50, 100, "charged"),
                                          (TAG + "Glass", 80, None, "let_it_go"),
                                          (TAG + "Mirror", 200, None, "undecided")):
        conn.execute(
            """INSERT INTO breakages (what, found_on, replacement_cost, charged_amount,
               charge_decision, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (what, found, cost, charged, decision, _now()))
    conn.commit()
    now_ = m.breakage_summary(conn, today=today)
    s.check("a vase charged above its cost does not pay for the glass; the undecided "
            "mirror is not absorbed: +80, not +230",
            round(now_["absorbed"] - base["absorbed"], 2) == 80,
            detail=f"{base['absorbed']} -> {now_['absorbed']}")
    s.check("and the undecided cost is its own figure",
            round(now_["undecided_cost"] - base["undecided_cost"], 2) == 200)

    s.section("Rota against the clock is the team's clock")
    rvc_start, rvc_end = (today - timedelta(days=30)).isoformat(), today.isoformat()
    owner_day = today - timedelta(days=2)
    _clock(conn, owner["id"], owner_day, 8)
    rob = _person(conn, "Rob")
    _clock(conn, rob, today - timedelta(days=4), 9, auto_closed=1)
    rows = m.rota_vs_clock(conn, rvc_start, rvc_end)
    s.check("the owner's own day is not unplanned team hours",
            not any(r["user_id"] == owner["id"] and r["date"] == owner_day.isoformat()
                    for r in rows))
    guess = next((r for r in rows if r["user_id"] == rob), None)
    s.check("a guessed clock-out counts no hours, and says why",
            guess is not None and guess["hours"] == 0 and "guessed" in guess["detail"],
            detail=str(guess))

    s.section("The staffing strip counts people who can come")
    _shift(conn, ann, date(y, 3, 5))
    _shift(conn, left, date(y, 3, 5))
    _shift(conn, bea, date(y, 3, 15))
    strip = {r["date"]: r for r in m.roster_vs_occupancy(
        conn, [date(y, 3, 1) + timedelta(days=i) for i in range(31)])}
    s.check("a leaver's leftover shift is not cover",
            strip[date(y, 3, 5)]["scheduled"] == 1, detail=str(strip[date(y, 3, 5)]))
    s.check("nor is somebody rostered on a day of approved leave",
            strip[date(y, 3, 15)]["scheduled"] == 0, detail=str(strip[date(y, 3, 15)]))

    s.section("Room faults count stays")
    room = conn.execute(
        """SELECT id FROM rooms WHERE active = 1 AND id NOT IN
             (SELECT room_id FROM room_issues WHERE status = 'open') ORDER BY id LIMIT 1""").fetchone()
    if room:
        ref = TAG.upper() + "STAY"
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, ?)""",
            (room["id"], ref, (TAG + "tok").lower(), TAG + "Guest", "tf@example.invalid",
             (today - timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat(),
             _now()))
        conn.commit()

        def in_room():
            v = _tiles(oc.get("/admin/room-faults").get_data(as_text=True)).get(
                "Guest already in the room", ("",))[0]
            return _num(v)

        none_yet = in_room()
        for title in ("shutter", "tap"):
            conn.execute("INSERT INTO room_issues (room_id, title, status, created_at) "
                         "VALUES (?, ?, 'open', ?)", (room["id"], TAG + title, _now()))
            conn.commit()
            if title == "shutter":
                one = in_room()
        two = in_room()
        s.check("a guest in a room with a fault is one more", one == (none_yet or 0) + 1,
                detail=f"{none_yet} -> {one}")
        s.check("and a second fault in the same room is still one guest", two == one,
                detail=f"{one} -> {two}")
    else:
        s.check("a room without an open fault to test with", False)

    s.section("A review is due a year after the last one")
    old_hand = _person(conn, "Old", start_date=(today - timedelta(days=5 * 365)).isoformat())
    conn.execute("INSERT INTO performance_reviews (user_id, review_date, created_at) VALUES (?, ?, ?)",
                 (old_hand, (today - timedelta(days=400)).isoformat(), _now()))
    no_start = _person(conn, "New", created_at=(datetime.now(timezone.utc)
                                                - timedelta(days=400)).isoformat())
    conn.commit()
    due = {a["subject_user_id"]: a for a in m.collect_hr_actions(conn, today)
           if a["type"] == "review_overdue"}
    s.check("reviewed 400 days ago: overdue 35 days, not four years",
            due.get(old_hand, {}).get("since") == (today - timedelta(days=35)).isoformat(),
            detail=str(due.get(old_hand)))
    s.check("no start date: due a year after the account was made, not always a year ago",
            due.get(no_start, {}).get("since") == (today - timedelta(days=35)).isoformat(),
            detail=str(due.get(no_start)))

    s.section("A certificate still to expire is due in so many days")
    conn.execute("INSERT INTO certifications (user_id, name, expiry_date, created_at) "
                 "VALUES (?, ?, ?, ?)",
                 (ann, TAG + "First aid", (today + timedelta(days=7)).isoformat(), _now()))
    conn.commit()
    page = oc.get("/admin/hr").get_data(as_text=True)
    row = re.search(re.escape(TAG + "First aid") + r".*?</tr>", page, re.S)
    s.check("the HR list says due in 7 days, not -7d",
            row is not None and "due in 7 days" in row.group(0) and "-7d" not in row.group(0),
            detail=row.group(0)[-300:] if row else "no row")
    s.check("and its expiry is a date in words, not 2026-10-20",
            row is not None and m.format_date_short((today + timedelta(days=7)).isoformat()) in row.group(0)
            and (today + timedelta(days=7)).isoformat() not in row.group(0),
            detail=row.group(0)[-300:] if row else "no row")

    conn.close()
    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
