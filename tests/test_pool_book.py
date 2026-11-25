# -*- coding: utf-8 -*-
"""The pool book: the water's checks, kept the way the fridges are.

Suggested 22 October 2026 as a back-end job, and agreed by the owner on
23 November 2026. A pool guests use is expected to keep a record of its
water -- free chlorine, pH, whether it is clear, how many swam -- and what
was done whenever a reading was out of range; an inspector asks to see it.

WHAT IS HELD HERE

  READINGS AS TYPED. "1,2", "7.4" and "26°" read; any of them may be left
  out of a check, but not all; a number the water cannot give is refused.

  THE OWNER'S RANGES, NOT THE APP'S. Until a range is set nothing is judged
  and the page says so. Out of range, or cloudy water, with nothing said is
  recorded anyway, named, a blocker on the owner home and a task, and goes
  when somebody says what was done -- once, with who.

  THE SEASON. The same two days every year, over the new year if need be.
  In season, the water not checked for two days is a warning and a task;
  out of season, or with no season set, nothing nags.

  THE FILE. A year of checks, for an inspector.
"""
import csv
import io
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes, keep_rows, visible_text
import _harness

m = _harness.m
TAG = "ZZPOOL"


def _home(conn, today):
    with m.app.test_request_context("/"):
        return m.owner_home_warnings(conn, today)


def _task(conn, title):
    return conn.execute(
        "SELECT * FROM tasks WHERE origin = ? AND title = ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, title)).fetchone()


def _said(r):
    return " ".join(flashes(r))


def _set(conn, **values):
    for key, value in values.items():
        conn.execute("""INSERT INTO app_settings (key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, value))
    conn.commit()


def run():
    s = Suite("The pool book: the water's checks, kept the way the fridges are")
    oc, ec, _owner, _emp = clients()
    conn = db()
    put_back = keep_rows(conn, "app_settings", "key LIKE 'pool_%'")
    before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM pool_readings").fetchone()[0]
    try:
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'pool_%'")
        conn.commit()
        _run(s, oc, ec, conn, before)
    finally:
        conn.execute("DELETE FROM pool_readings WHERE id > ?", (before,))
        conn.execute("DELETE FROM tasks WHERE origin = ? AND title IN (?, ?)",
                     (m.WATCH_TASK_ORIGIN, "A pool check out of range with nothing said",
                      "Check the pool water"))
        conn.commit()
        put_back()
        conn.close()
    return s


def _mine(conn, before):
    return conn.execute("SELECT * FROM pool_readings WHERE id > ? ORDER BY id",
                        (before,)).fetchall()


def _run(s, oc, ec, conn, before):
    today = m.house_today()

    s.section("Readings as typed")
    for raw, want in (("1,2", 1.2), ("7.4", 7.4), ("26°", 26.0), ("", None), ("x", None)):
        s.check(f"“{raw}” reads as {want}", m.pool_number(raw) == want)

    s.section("The season, the same days every year")
    summer = dict(m.POOL_SETTING_DEFAULTS, pool_open_from="06-01", pool_open_to="09-30")
    s.check("July is in a June-to-September season, opened on 1 June",
            m.pool_season_start(summer, date(2027, 7, 10)) == date(2027, 6, 1))
    s.check("November is not", m.pool_season_start(summer, date(2027, 11, 1)) is None)
    winter = dict(m.POOL_SETTING_DEFAULTS, pool_open_from="11-01", pool_open_to="02-28")
    s.check("a season over the new year opened the year before",
            m.pool_season_start(winter, date(2027, 1, 15)) == date(2026, 11, 1))
    s.check("and is not open in June", m.pool_season_start(winter, date(2027, 6, 1)) is None)
    s.check("no season set: never in season",
            m.pool_season_start(dict(m.POOL_SETTING_DEFAULTS), today) is None)

    s.section("Nothing checked since the season opened")
    if not conn.execute("SELECT 1 FROM pool_readings LIMIT 1").fetchone():
        _set(conn, pool_open_from="01-01", pool_open_to="12-31")
        later = date(today.year, 1, 5)
        unread = m.pool_problems(conn, later)["unread"]
        s.check("a season four days old with no check is a check missed",
                unread and unread["never"] and unread["since"] == date(today.year, 1, 1),
                detail=str(unread))
        s.check("one day old is not yet",
                m.pool_problems(conn, date(today.year, 1, 2))["unread"] is None)
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'pool_%'")
        conn.commit()

    s.section("The page, before any range is set")
    page = visible_text(ec.get("/pool").get_data(as_text=True))
    s.check("staff open it and can write down a check", "Check the water" in page)
    s.check("it says nothing is being judged", "No ranges are set" in page, detail=page[:300])
    s.check("staff do not get the season and the ranges", "The season and the ranges" not in page)
    s.check("and cannot save them", ec.post("/pool", data={"what": "settings",
                                                           "pool_ph_min": "1"}).status_code == 403)

    r = ec.post("/pool", data={"free_chlorine": "0,1", "ph": "9", "water_c": "24",
                               "clear": "yes", "swimmers": "6", "note": TAG},
                follow_redirects=True)
    mine = _mine(conn, before)
    s.check("a check is written down, with who", len(mine) == 1 and mine[0]["free_chlorine"] == 0.1
            and mine[0]["ph"] == 9.0 and mine[0]["clear"] == 1 and mine[0]["swimmers"] == 6
            and mine[0]["read_by_user_id"], detail=_said(r))
    s.check("with no range set, nothing in it is out of range",
            "Written down." in _said(r) and not m.pool_problems(conn, today)["unanswered"])

    r = ec.post("/pool", data={"free_chlorine": "", "ph": "", "water_c": ""}, follow_redirects=True)
    s.check("a check with nothing in it is refused",
            "Nothing was read" in _said(r) and len(_mine(conn, before)) == 1)
    r = ec.post("/pool", data={"ph": "20"}, follow_redirects=True)
    s.check("a pH of 20 is not a reading", "not a reading the water could give" in _said(r)
            and len(_mine(conn, before)) == 1)

    s.section("The owner sets the season and the ranges")
    r = oc.post("/pool", data={"what": "settings", "pool_open_from": "2026-06-01",
                               "pool_open_to": "2026-09-30", "pool_chlorine_min": "2",
                               "pool_chlorine_max": "1", "pool_ph_min": "6.9",
                               "pool_ph_max": "7.7"}, follow_redirects=True)
    s.check("lowest above highest is refused", "Swap them over" in _said(r)
            and not m.pool_settings(conn)["pool_chlorine_min"])
    oc.post("/pool", data={"what": "settings", "pool_open_from": "2026-01-01",
                           "pool_open_to": "2026-12-31", "pool_chlorine_min": "0,4",
                           "pool_chlorine_max": "1.4", "pool_ph_min": "6.9",
                           "pool_ph_max": "7.7", "pool_checks_a_day": "2"})
    st = m.pool_settings(conn)
    s.check("saved, the season as the days of the year only",
            st["pool_open_from"] == "01-01" and st["pool_open_to"] == "12-31"
            and st["pool_chlorine_min"] == "0.4" and st["pool_ph_max"] == "7.7", detail=str(st))
    page = oc.get("/pool").get_data(as_text=True)
    s.check("the owner's page shows them back", 'value="0.4"' in page and "The season and the ranges"
            in visible_text(page))
    s.check("the first check, read again, is now out of range",
            len(m.pool_problems(conn, today)["unanswered"]) == 1)
    first = _mine(conn, before)[0]
    ec.post(f"/pool/reading/{first['id']}/answer", data={"action_taken": f"{TAG} test check"})

    s.section("Out of range, and what was done")
    r = ec.post("/pool", data={"free_chlorine": "0.2", "ph": "7.2", "clear": "no"},
                follow_redirects=True)
    said = _said(r)
    low = _mine(conn, before)[-1]
    s.check("recorded even with nothing said", low["free_chlorine"] == 0.2 and not low["action_taken"])
    s.check("and named: the chlorine, and the water not clear",
            "free chlorine 0.2 mg/L, below 0.4" in said and "the water was not clear" in said,
            detail=said)
    probs = m.pool_problems(conn, today)["unanswered"]
    s.check("it is the one problem", [x["row"]["id"] for x in probs] == [low["id"]])
    home = _home(conn, today)
    s.check("a blocker on the owner home", any(
        w["severity"] == "blocker" and "pool check" in w["title"] for w in home),
        detail=str([w["title"] for w in home]))
    m.generate_watch_tasks(conn, today)
    task = _task(conn, "A pool check out of range with nothing said")
    s.check("and a task, high", task is not None and task["priority"] == "high")
    page = ec.get("/pool").get_data(as_text=True)
    s.check("the check has a box to say what was done", f"/pool/reading/{low['id']}/answer" in page)

    r = ec.post(f"/pool/reading/{low['id']}/answer", data={"action_taken": ""},
                follow_redirects=True)
    s.check("an empty answer is not one", "Say what was done" in _said(r))
    ec.post(f"/pool/reading/{low['id']}/answer",
            data={"action_taken": "Shock dose, pool closed until it read 0.8"})
    low = conn.execute("SELECT * FROM pool_readings WHERE id = ?", (low["id"],)).fetchone()
    s.check("answered, with who and when", low["action_taken"].startswith("Shock dose")
            and low["answered_by_user_id"] and low["answered_at"])
    r = ec.post(f"/pool/reading/{low['id']}/answer", data={"action_taken": "again"},
                follow_redirects=True)
    s.check("once given, the answer is the record", "already written" in _said(r))
    m.generate_watch_tasks(conn, today)
    s.check("the task has closed itself",
            _task(conn, "A pool check out of range with nothing said") is None)
    s.check("an answer to no check is a 404",
            ec.post("/pool/reading/999999999/answer", data={"action_taken": "x"}).status_code == 404)

    r = ec.post("/pool", data={"free_chlorine": "2.5", "action_taken": "Left the cover off, diluted"},
                follow_redirects=True)
    s.check("said at the time, it is kept and nothing is asked",
            _mine(conn, before)[-1]["action_taken"] == "Left the cover off, diluted"
            and not m.pool_problems(conn, today)["unanswered"], detail=_said(r))

    s.section("In season, the water not checked")
    s.check("checked today: nothing missed", m.pool_problems(conn, today)["unread"] is None)
    later = today + timedelta(days=3)
    if later.year == today.year:
        unread = m.pool_problems(conn, later)["unread"]
        s.check("three days on with no check, it is missed, since today",
                unread and not unread["never"] and unread["since"] == today, detail=str(unread))
        home = _home(conn, later)
        s.check("a warning on the owner home",
                any(w["title"] == "The pool water has not been checked" for w in home))
        m.generate_watch_tasks(conn, later)
        s.check("and a task", _task(conn, "Check the pool water") is not None)
        m.generate_watch_tasks(conn, today)
        s.check("which closes itself once there is a check",
                _task(conn, "Check the pool water") is None)
    _set(conn, pool_open_from="06-01", pool_open_to="06-02")
    s.check("out of season, nothing nags",
            m.pool_problems(conn, date(today.year, 9, 1))["unread"] is None)

    s.section("The file, for an inspector")
    body = ec.get("/pool/book.csv").get_data(as_text=True)
    rows = list(csv.DictReader(io.StringIO(body)))
    s.check("every check of the year, oldest first, with what was done",
            len(rows) >= 3 and any(r["what_was_done"].startswith("Shock dose")
                                   and "below 0.4" in r["out_of_range"] for r in rows),
            detail=body[:300])
    s.check("on the menu", "/pool" in ec.get("/management/meters").get_data(as_text=True))
