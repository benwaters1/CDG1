"""The rota against the clock — two tables that had never been compared.

The rota is what somebody intended; the clock is what payroll pays from.
Both directions cost something. A shift with no clock entry is either a
service that went uncovered or hours that will never be paid. A clock entry
with no shift is labour nobody planned, which is the half that quietly moves
the wage bill.

The most important checks here are the ones that stop it being unfair or
noisy: somebody on approved leave is not a no-show, a future shift is not
anything yet, and the wording never says a person failed to turn up — only
that two records disagree.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-rvc-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM absences WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG + "%",))
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


def _clock(conn, uid, day, hours=8, at_hour=9):
    # Local wall-clock time converted to UTC, which is how the app stores it —
    # bucketing on the raw UTC date would put an evening shift on the wrong day.
    d = m.parse_date(day)
    start = datetime(d.year, d.month, d.day, at_hour, tzinfo=m.LOCAL_TZ)
    conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
        (uid, start.astimezone(timezone.utc).isoformat(),
         (start + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()))
    conn.commit()


def _find(rows, name, kind):
    return next((r for r in rows if r["employee_name"] == name and r["kind"] == kind), None)


def _all(conn):
    return m.rota_vs_clock(conn, _iso(-30), _iso(0))


def run():
    s = Suite("rota vs clock")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    s.section("Rostered, with no clock entry")
    ghost = _person(conn, "Ghost")
    _shift(conn, ghost, _iso(-3))
    rows = _all(conn)
    hit = _find(rows, "Ghost", "no_clock")
    s.check("the day is flagged", hit is not None)
    s.check("it says what they were down for", hit and "09:00" in hit["planned"])
    # The wording is the point: a missing clock-in is as likely to be a
    # forgotten button as an absence, and saying otherwise would be unfair.
    s.check("it does not accuse them of not turning up",
            hit and "no entry" in hit["detail"] and "not turn up" not in hit["detail"],
            detail=hit["detail"] if hit else "")

    s.section("Clocking in closes it")
    _clock(conn, ghost, _iso(-3))
    s.check("with a clock entry the day is clean",
            _find(_all(conn), "Ghost", "no_clock") is None)

    s.section("Worked with nothing on the rota")
    extra = _person(conn, "Extra")
    _clock(conn, extra, _iso(-4), hours=6)
    hit = _find(_all(conn), "Extra", "no_shift")
    s.check("the day is flagged", hit is not None)
    s.check("and the hours are counted", hit and abs(hit["hours"] - 6) < 0.05,
            detail=str(hit["hours"]) if hit else "")

    s.section("Approved leave is not a no-show")
    # It is a rota clash, reported on its own page. Saying it here too in
    # different words would train everybody to ignore both.
    away = _person(conn, "Away")
    _shift(conn, away, _iso(-5))
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
           leave_type, status, requested_at)
           VALUES (?, ?, ?, ?, 'annual', 'approved', ?)""",
        (away, _iso(-6), _iso(-4), TAG + "hols", now))
    conn.commit()
    s.check("somebody on approved leave is not listed here",
            _find(_all(conn), "Away", "no_clock") is None)
    s.check("but it IS a rota clash", any(
        c["user_id"] == away for c in m.rota_conflicts(conn, _iso(-10), _iso(0))))

    s.section("A recorded absence is not a no-show either")
    ill = _person(conn, "Ill")
    _shift(conn, ill, _iso(-5))
    conn.execute(
        """INSERT INTO absences (user_id, start_date, end_date, kind, reason,
           self_certified, created_at) VALUES (?, ?, ?, 'sick', ?, 1, ?)""",
        (ill, _iso(-5), _iso(-5), TAG + "flu", now))
    conn.commit()
    s.check("a recorded absence is not flagged",
            _find(_all(conn), "Ill", "no_clock") is None)

    s.section("A shift that starts after midnight lands on its own day")
    # Paris runs ahead of UTC, so 00:30 local is 22:30 UTC the previous day.
    # Bucketing the clock on the raw UTC date would move a night worker's
    # entry back a day: their real shift would read as a no-show, and the day
    # before as unrostered work. Two wrong rows from one timezone slip.
    night = _person(conn, "Night")
    _shift(conn, night, _iso(-2))
    _clock(conn, night, _iso(-2), hours=5, at_hour=0)      # 00:30-ish local
    rows = _all(conn)
    s.check("the night shift is matched to the day it was rostered",
            _find(rows, "Night", "no_clock") is None,
            detail="a UTC-bucketed clock entry would read this as a no-show")
    s.check("and does not appear as unrostered work the day before",
            _find(rows, "Night", "no_shift") is None,
            detail=str([r["date"] for r in rows if r["employee_name"] == "Night"]))

    s.section("A night shift on the FIRST day of the window is not lost")
    # The bug an audit found: the fetch bound was a bare local date compared
    # against UTC-stored timestamps. 00:30 local in Paris is 22:30 UTC the day
    # before, and '...T22:30+00:00' >= '2026-07-28' is False as a string — so
    # the entry was never fetched and a night worker who DID clock in read as a
    # no-show. Only ever on day one of the window, which rolls forward daily.
    edge = _person(conn, "Edge")
    first_day = _iso(-30)
    _shift(conn, edge, first_day)
    _clock(conn, edge, first_day, hours=5, at_hour=0)
    rows = m.rota_vs_clock(conn, first_day, _iso(0))
    s.check("their clock entry is found on the first day of the window",
            _find(rows, "Edge", "no_clock") is None,
            detail="a bare-date fetch bound drops it and calls them a no-show")
    s.check("and it is not reported as unrostered work either",
            _find(rows, "Edge", "no_shift") is None,
            detail=str([r["date"] for r in rows if r["employee_name"] == "Edge"]))

    s.section("Tomorrow is not a no-show")
    future = _person(conn, "Future")
    _shift(conn, future, _iso(3))
    _shift(conn, future, _iso(0))          # today, still in progress
    rows = m.rota_vs_clock(conn, _iso(-30), _iso(30))
    s.check("a future shift is not judged", _find(rows, "Future", "no_clock") is None,
            detail="only days that have finished")

    s.section("The page")
    page = oc.get("/admin/rota-vs-clock?days=30").get_data(as_text=True)
    s.check("it renders", "Rota vs clock" in page)
    s.check("the unrostered day is on it", "Extra" in page)
    s.check("and it states plainly that this is not an accusation",
            "not that anybody did anything wrong" in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
