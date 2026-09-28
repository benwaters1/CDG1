"""Days with work happening and nobody on.

The rota-clash page checks whether the people rostered can work their shift.
It says in its own footer that it does not check whether anybody is on at
all — shifts was never joined to arrivals, dinners or ateliers.

The case worth the most here is the one that reads as fine: somebody IS on
the rota, and every one of them is on approved leave. The rota looks staffed,
counting shifts says it is staffed, and nobody is coming.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-cover-"
WINDOW = 30  # the days from today that every cover_gaps call below reads


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def _person(conn, name):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           created_at) VALUES (?, 'x', 'employee', ?, 'General', 'active', ?)""",
        (f"{TAG}{name}@example.invalid", name, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE email = ?",
                        (f"{TAG}{name}@example.invalid",)).fetchone()["id"]


def _shift(conn, uid, day):
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note,
           created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
        (uid, day, TAG + "shift", datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _day(rows, iso):
    return next((r for r in rows if r["date"] == iso), None)


def _nothing_on(conn, iso):
    """Whether the house has nothing at all on a day, asked of the tables
    rather than of cover_gaps, which is the thing under test. The same four
    kinds of work it counts: a confirmed stay from arrival to departure, a
    confirmed dinner, an atelier sitting, a confirmed event. Each is asked a
    little more widely than the builder asks, never less, so a day this calls
    empty is empty."""
    asks = (
        ("SELECT 1 FROM bookings WHERE status = 'confirmed' "
         "AND arrival_date <= ? AND departure_date >= ?", (iso, iso)),
        ("SELECT 1 FROM restaurant_bookings WHERE status = 'confirmed' "
         "AND dinner_date = ?", (iso,)),
        ("SELECT 1 FROM workshop_sessions WHERE start_date <= ? "
         "AND COALESCE(end_date, start_date) >= ?", (iso, iso)),
        ("SELECT 1 FROM event_inquiries WHERE status = 'confirmed' "
         "AND preferred_date IS NOT NULL "
         "AND MIN(preferred_date, COALESCE(end_date, preferred_date)) <= ? "
         "AND MAX(preferred_date, COALESCE(end_date, preferred_date)) >= ?", (iso, iso)),
    )
    return not any(conn.execute(sql, args).fetchone() for sql, args in asks)


def _listed_as(iso, row):
    if row is None:
        return ""
    if row["work"]:
        return (f"{iso} came back with work={row['work']}: cover_gaps counts "
                "something here that _nothing_on does not ask about")
    return f"{iso} came back with work=0: a day with no work was listed"


def run():
    s = Suite("cover gaps")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]

    # A stay spanning three nights, with nobody rostered for any of it.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Stayer', 'g@example.invalid', ?, ?, 2, 'confirmed', 600, ?)""",
        (room, TAG + "STAY", TAG + "tok", _iso(10), _iso(13), now))
    conn.commit()

    s.section("A stay with nobody on")
    rows = m.cover_gaps(conn, _iso(0), _iso(WINDOW))
    arrival = _day(rows, _iso(10))
    s.check("the arrival day is listed as having work", arrival is not None)
    s.check("and as uncovered", arrival and arrival["uncovered"] is True)

    # The important one: the middle of a stay. A page built on arrivals alone
    # sees nothing here, and this is when somebody is actually in the building.
    middle = _day(rows, _iso(11))
    s.check("a night in the middle of the stay counts as work",
            middle is not None, detail="a list built on arrivals misses this")
    s.check("with the guests counted", middle and middle["in_house"] == 2,
            detail=str(middle["in_house"]) if middle else "")
    s.check("and it is uncovered too", middle and middle["uncovered"] is True)

    # Departure day is work as well — somebody has to turn the room round.
    s.check("the departure day is work", _day(rows, _iso(13)) is not None)

    s.section("Rostering somebody closes it")
    who = _person(conn, "Onduty")
    _shift(conn, who, _iso(11))
    rows = m.cover_gaps(conn, _iso(0), _iso(WINDOW))
    middle = _day(rows, _iso(11))
    s.check("the day now has a person", middle and middle["people_count"] == 1)
    s.check("and is no longer a gap", middle and middle["uncovered"] is False)
    s.check("but the untouched days still are",
            _day(rows, _iso(12))["uncovered"] is True)

    s.section("Rostered but unable to work it is not cover")
    # The whole reason this cannot just count shifts.
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
           leave_type, status, requested_at)
           VALUES (?, ?, ?, ?, 'annual', 'approved', ?)""",
        (who, _iso(11), _iso(11), TAG + "off", now))
    conn.commit()
    rows = m.cover_gaps(conn, _iso(0), _iso(WINDOW))
    middle = _day(rows, _iso(11))
    s.check("the shift is still on the rota", middle and middle["people_count"] == 1)
    s.check("but nobody can work it", middle and middle["effective"] == 0,
            detail=str(middle["effective"]) if middle else "")
    s.check("so the day is a gap again", middle and middle["uncovered"] is True)
    s.check("and it says how many cannot", middle and middle["blocked_count"] == 1)

    s.section("Somebody who has left is not cover")
    # Deactivating a person does not delete their future shifts, so a leftover
    # one was counted and a day with guests in the house and nobody actually
    # employed on it read as staffed. They cannot be flagged as blocked either:
    # role_compliance only iterates active employees.
    left = _person(conn, "Departed")
    conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (left,))
    conn.commit()
    _shift(conn, left, _iso(12))
    rows = m.cover_gaps(conn, _iso(0), _iso(WINDOW))
    day12 = _day(rows, _iso(12))
    s.check("their shift does not count as a person on",
            day12 and day12["people_count"] == 0,
            detail=str(day12["people_count"]) if day12 else "no row")
    s.check("so the day is still reported as uncovered",
            day12 and day12["uncovered"] is True,
            detail="a departed employee's leftover shift read as cover")

    s.section("A day with nothing on is not a gap")
    # Found, not assumed. This used to take day 25 as empty without looking,
    # and the fresh database seeds the house's real atelier dates: from 28
    # September "Immersive Artisan Workshops" (23 to 27 October) sat on day
    # 25, cover_gaps rightly listed it as work with nobody on, and the check
    # went red with nothing wrong. So the tables are asked for the first day
    # of the window the calls above read that has nothing on at all.
    quiet = next((_iso(n) for n in range(WINDOW + 1) if _nothing_on(conn, _iso(n))),
                 None)
    s.check("the window has a day with nothing on to ask about", quiet is not None,
            detail=f"every day from {_iso(0)} to {_iso(WINDOW)} has work on it, so "
                   "the checks below have nothing to hold cover_gaps to")
    listed = _day(rows, quiet) if quiet else None
    s.check("an empty day is not listed at all", quiet is not None and listed is None,
            detail=_listed_as(quiet, listed) or "no empty day was found to ask about")

    # A day nothing has touched never enters cover_gaps' working set, so the
    # check above only goes red if the builder starts walking every date.
    # Losing the one line that drops days without work would slip straight
    # past it. A shift brings a day into the set without being work, so
    # somebody is rostered on the quiet day and it is asked about again.
    if quiet:
        _shift(conn, who, quiet)
    rows = m.cover_gaps(conn, _iso(0), _iso(WINDOW))
    listed = _day(rows, quiet) if quiet else None
    s.check("nor once somebody is rostered on it", quiet is not None and listed is None,
            detail=_listed_as(quiet, listed) or "no empty day was found to ask about")

    s.section("The page")
    page = oc.get("/admin/cover?days=60").get_data(as_text=True)
    s.check("it renders", "Nobody on" in page)
    s.check("the hollow day is called out separately",
            "Looks staffed on the rota" in page)
    s.check("and it links to the other half",
            "/admin/rota-clashes" in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
