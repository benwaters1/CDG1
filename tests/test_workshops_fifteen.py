"""Every atelier takes fifteen (owner, 18 October 2026: "all workshops are 15 guests").

Sessions were made at ten, the old default. Held here: init_db raised every
session still to come and every workshop's default to fifteen; once, so a
session set by hand afterwards keeps its number; never lowering one set
higher; leaving a past session's number alone, since that is a record; and
a new session made from a workshop starts at fifteen.
"""
from datetime import timedelta

from _harness import Suite, db, keep_rows
import _harness

m = _harness.m
FLAG = "workshops_fifteen_2026_10_18"
TAG = "ZZW15"


def run():
    s = Suite("Fifteen to an atelier")
    conn = db()
    today = m.house_today_iso()

    s.section("init_db raised them")
    # Only sessions that were there when it ran: other suites make sessions
    # of their own, at whatever capacity they are testing, after it.
    ran = (conn.execute("SELECT value FROM app_settings WHERE key = ?", (FLAG,)).fetchone()
           or {"value": ""})["value"]
    low = conn.execute(
        "SELECT COUNT(*) AS n FROM workshop_sessions WHERE capacity < ? "
        "AND COALESCE(end_date, start_date) >= ? AND created_at < ?",
        (m.WORKSHOP_GUESTS, today, ran)).fetchone()["n"]
    s.check("no atelier still to come takes fewer than fifteen", low == 0, detail=f"{low} session(s)")
    s.check("no workshop starts a new session at fewer",
            not conn.execute("SELECT 1 FROM workshops WHERE COALESCE(default_capacity, 0) < ?",
                             (m.WORKSHOP_GUESTS,)).fetchone())
    s.check("and it records that it has run",
            conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (FLAG,)).fetchone() is not None)

    s.section("Which sessions it moves, and that it moves them once")
    wid = conn.execute(
        "INSERT INTO workshops (title, description, price_per_person, active, created_at, default_capacity) "
        "VALUES (?, 'x', 100, 1, ?, 10)", (TAG + " atelier", _harness.datetime_now())).lastrowid
    soon = (m.house_today() + timedelta(days=200)).isoformat()
    gone = (m.house_today() - timedelta(days=200)).isoformat()

    def session(start, cap):
        return conn.execute(
            "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (wid, start, start, cap, TAG, _harness.datetime_now())).lastrowid

    to_come, past, bigger = session(soon, 10), session(gone, 10), session(soon, 20)
    put_flag = keep_rows(conn, "app_settings", "key = ?", (FLAG,))
    try:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (FLAG,))
        conn.commit()
        m.apply_workshops_fifteen_2026_10_18(conn)
        cap = lambda sid: conn.execute("SELECT capacity FROM workshop_sessions WHERE id = ?",
                                       (sid,)).fetchone()["capacity"]
        s.check("one still to come goes from ten to fifteen", cap(to_come) == 15, detail=str(cap(to_come)))
        s.check("a past one keeps its ten", cap(past) == 10, detail=str(cap(past)))
        s.check("one set at twenty keeps its twenty", cap(bigger) == 20, detail=str(cap(bigger)))
        s.check("and the workshop's default is fifteen",
                conn.execute("SELECT default_capacity FROM workshops WHERE id = ?",
                             (wid,)).fetchone()["default_capacity"] == 15)
        conn.execute("UPDATE workshop_sessions SET capacity = 12 WHERE id = ?", (to_come,))
        conn.commit()
        s.check("a second run leaves a number set since",
                m.apply_workshops_fifteen_2026_10_18(conn) == (0, 0) and cap(to_come) == 12)
    finally:
        conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
        conn.execute("DELETE FROM workshops WHERE id = ?", (wid,))
        conn.commit()
        put_flag()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
