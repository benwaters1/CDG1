"""The rota against everything else on record.

Three things the system already knew and never compared: approved leave, a
recorded absence, and whether the person is currently qualified for the role
they are down for. None of them fails loudly — the rota renders, the shift
looks staffed, and the first anyone knows is the morning nobody arrives.

Plus the fourth working-time rule. The compliance checker already read break
minutes in order to subtract them from paid hours, and never asked whether a
break that the law requires actually happened.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-rota-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM absences WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM certifications WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM role_requirements WHERE requirement LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM breaks WHERE time_entry_id IN "
                 "(SELECT id FROM time_entries WHERE user_id IN "
                 " (SELECT id FROM users WHERE email LIKE ?))", (TAG + "%",))
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def _person(conn, name, job_role):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', 'employee', ?, ?, 'active', ?)""",
        (f"{TAG}{name}@example.invalid", name, job_role,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name}@example.invalid",)).fetchone()["id"]


def _shift(conn, uid, day, start="09:00", end="17:00"):
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
           created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (uid, day, start, end, TAG + "shift", datetime.now(timezone.utc).isoformat()))
    conn.commit()


def run():
    s = Suite("rota clashes")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------- 1. approved leave
    s.section("Rostered on leave that was already approved")
    away = _person(conn, "Away", "Housekeeping")
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
           leave_type, status, requested_at) VALUES (?, ?, ?, ?, 'annual', 'approved', ?)""",
        (away, _iso(5), _iso(9), TAG + "holiday", now))
    conn.commit()
    _shift(conn, away, _iso(7))

    rows = m.rota_conflicts(conn, _iso(0), _iso(30))
    leave_hits = [r for r in rows if r["kind"] == "leave" and r["user_id"] == away]
    s.check("the clash is found", len(leave_hits) == 1, detail=str(len(leave_hits)))
    s.check("it names the person", leave_hits and leave_hits[0]["employee_name"] == "Away")
    s.check("and says when the leave runs",
            leave_hits and _iso(9) in leave_hits[0]["detail"],
            detail=leave_hits[0]["detail"] if leave_hits else "")

    # A shift the day AFTER leave ends is not a clash. An off-by-one here would
    # flag half the rota and train everybody to ignore the page.
    _shift(conn, away, _iso(10))
    rows = m.rota_conflicts(conn, _iso(0), _iso(30))
    s.check("the day after leave ends is not flagged",
            len([r for r in rows if r["kind"] == "leave" and r["user_id"] == away]) == 1)

    # Pending leave is deliberately not a clash — declining it is a fine answer.
    maybe = _person(conn, "Maybe", "Housekeeping")
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
           leave_type, status, requested_at) VALUES (?, ?, ?, ?, 'annual', 'pending', ?)""",
        (maybe, _iso(5), _iso(9), TAG + "maybe", now))
    conn.commit()
    _shift(conn, maybe, _iso(7))
    rows = m.rota_conflicts(conn, _iso(0), _iso(30))
    s.check("a pending request is not treated as a clash",
            not [r for r in rows if r["user_id"] == maybe])

    # ------------------------------------------------- 2. recorded absence
    s.section("Rostered while recorded absent")
    ill = _person(conn, "Ill", "Kitchen")
    conn.execute(
        """INSERT INTO absences (user_id, start_date, end_date, kind, reason,
           self_certified, created_at) VALUES (?, ?, ?, 'sick', ?, 1, ?)""",
        (ill, _iso(2), _iso(4), TAG + "flu", now))
    conn.commit()
    _shift(conn, ill, _iso(3))
    rows = m.rota_conflicts(conn, _iso(0), _iso(30))
    hits = [r for r in rows if r["kind"] == "absence" and r["user_id"] == ill]
    s.check("the clash is found", len(hits) == 1, detail=str(len(hits)))
    s.check("and the absence is named, not just flagged",
            hits and "sick" in hits[0]["detail"], detail=hits[0]["detail"] if hits else "")

    # ------------------------------------------- 3. lapsed certification
    s.section("Rostered to work something they are not qualified for")
    cook = _person(conn, "Cook", TAG + "chef")
    conn.execute(
        """INSERT INTO role_requirements (job_role, requirement, requirement_type,
           created_at) VALUES (?, ?, 'certification', ?)""",
        (TAG + "chef", TAG + "Food hygiene", now))
    # Valid today, expired by the time the shift runs. This is the case a
    # "who is compliant right now" check cannot see.
    conn.execute(
        """INSERT INTO certifications (user_id, name, expiry_date, created_at)
           VALUES (?, ?, ?, ?)""",
        (cook, TAG + "Food hygiene", _iso(3), now))
    conn.commit()

    _shift(conn, cook, _iso(1))
    rows = m.rota_conflicts(conn, _iso(0), _iso(30))
    s.check("a shift while the certificate is still valid is fine",
            not [r for r in rows if r["kind"] == "certification" and r["user_id"] == cook])

    _shift(conn, cook, _iso(10))
    rows = m.rota_conflicts(conn, _iso(0), _iso(30))
    late = [r for r in rows if r["kind"] == "certification" and r["user_id"] == cook]
    s.check("a shift after it expires is caught", len(late) == 1, detail=str(len(late)))
    s.check("it names the requirement, not just 'non-compliant'",
            bool(late) and "Food hygiene" in late[0]["detail"],
            detail=late[0]["detail"] if late else "")
    s.check("and it is the blocking kind",
            late and late[0]["severity"] == "blocker")

    # --------------------------------------------------------- the page
    s.section("The page itself")
    page = oc.get("/admin/rota-clashes?days=30").get_data(as_text=True)
    s.check("it renders", "Rota clashes" in page)
    s.check("the leave clash is on it", "Away" in page)
    s.check("the absence clash is on it", "Ill" in page)
    s.check("the certificate clash is on it", "Cook" in page)
    s.check("and the certificate one is called out separately",
            "not currently qualified" in page)

    # --------------------------------------------- 4. the break rule
    s.section("The break the law requires")
    grafter = _person(conn, "Grafter", "Kitchen")
    start = datetime.now(timezone.utc) - timedelta(days=2)
    conn.execute(
        """INSERT INTO time_entries (user_id, clock_in_at, clock_out_at)
           VALUES (?, ?, ?)""",
        (grafter, start.isoformat(), (start + timedelta(hours=9)).isoformat()))
    conn.commit()
    entry = conn.execute(
        "SELECT id FROM time_entries WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (grafter,)).fetchone()["id"]

    today = datetime.now(m.LOCAL_TZ).date()
    v = m.working_time_violations(conn, today - timedelta(days=7), today)
    breaks = [x for x in v if x["rule"] == "break" and x["user_id"] == grafter]
    s.check("a nine-hour shift with no break is caught", len(breaks) == 1,
            detail=str(len(breaks)))
    s.check("and it says what was recorded, not just that a rule broke",
            breaks and "no break" in breaks[0]["detail"],
            detail=breaks[0]["detail"] if breaks else "")

    # Ten minutes is not twenty. A rule that accepts any break at all would
    # pass every shift where somebody stepped out for a cigarette.
    conn.execute(
        "INSERT INTO breaks (time_entry_id, start_at, end_at) VALUES (?, ?, ?)",
        (entry, (start + timedelta(hours=4)).isoformat(),
         (start + timedelta(hours=4, minutes=10)).isoformat()))
    conn.commit()
    v = m.working_time_violations(conn, today - timedelta(days=7), today)
    short = [x for x in v if x["rule"] == "break" and x["user_id"] == grafter]
    s.check("a ten-minute break is still short", len(short) == 1)
    s.check("and it says how much was actually taken",
            short and "10 min" in short[0]["detail"],
            detail=short[0]["detail"] if short else "")

    conn.execute(
        "UPDATE breaks SET end_at = ? WHERE time_entry_id = ?",
        ((start + timedelta(hours=4, minutes=25)).isoformat(), entry))
    conn.commit()
    v = m.working_time_violations(conn, today - timedelta(days=7), today)
    s.check("twenty-five minutes clears it",
            not [x for x in v if x["rule"] == "break" and x["user_id"] == grafter])

    # A short shift needs no break at all.
    quick = _person(conn, "Quick", "Kitchen")
    conn.execute(
        """INSERT INTO time_entries (user_id, clock_in_at, clock_out_at)
           VALUES (?, ?, ?)""",
        (quick, start.isoformat(), (start + timedelta(hours=4)).isoformat()))
    conn.commit()
    v = m.working_time_violations(conn, today - timedelta(days=7), today)
    s.check("a four-hour shift is not asked for a break",
            not [x for x in v if x["rule"] == "break" and x["user_id"] == quick])

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
