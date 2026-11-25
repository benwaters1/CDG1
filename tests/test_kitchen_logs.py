# -*- coding: utf-8 -*-
"""Fridge temperatures taken in one go, and the kitchen's cleaning kept.

The owner, 23 November 2026: "make it so fridge temps can be done really easy,
maybe do a kitchen maintenance like it records when the ovens were last
cleaned, the fridges cleaned, the dishwasher run through cleaning tablet cycle".

WHAT IS HELD HERE

  THE SWEEP. Every unit in use on one screen, one box each, one Save. A box
  left empty is a unit not read; one that is not a believable temperature is
  named and the rest still go in. "4,5", "−18" and "4.5°C" all read. A
  reading out of range with nothing said is recorded anyway, and named.

  SAYING WHAT WAS DONE, AFTERWARDS. A reading out of range can be answered
  once, by whoever dealt with it; until it is, it is a blocker on the owner
  home and a task, and both go when it is answered.

  A FRIDGE NOBODY READ. Two days without a reading is on the owner home and
  a task. A unit switched off for the winter is not: it leaves the sweep and
  the reminders, and keeps its readings.

  THE KITCHEN'S CLEANING. The ovens, the fridges and the dishwasher's
  cleaning-tablet cycle are on the kitchen's round from the start, every
  seven days. Done is pressed on a page of its own; every time is kept, with
  who; an earlier day entered late is history and leaves "last done" alone;
  a day in the future is not accepted. Each job is a task on the calendar the
  day it falls due and closes when it is done; one late is on the owner home.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZKLOG"
SEEDED = ("Clean the ovens", "Clean the fridges", "Run the dishwasher's cleaning-tablet cycle")


def _follow(client):
    """The client, posting and reading the page it lands on, flashes and all."""
    client.post_follow = lambda url, **kw: client.post(url, follow_redirects=True, **kw)
    return client


def _home(conn, today):
    with m.app.test_request_context("/"):
        return m.owner_home_warnings(conn, today)


def _task(conn, title):
    return conn.execute(
        "SELECT * FROM tasks WHERE origin = ? AND title = ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, title)).fetchone()


def _cleanup(conn):
    conn.execute("DELETE FROM fridge_readings WHERE unit_id IN "
                 "(SELECT id FROM fridge_units WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM fridge_units WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM cleaning_log WHERE round_id IN "
                 "(SELECT id FROM cleaning_rounds WHERE what LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM cleaning_rounds WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND (title LIKE 'Kitchen cleaning:%' "
                 "OR title LIKE 'Fridge temperatures%' OR title LIKE 'A fridge reading%')",
                 (m.WATCH_TASK_ORIGIN,))
    conn.commit()


def run():
    s = Suite("Fridge temperatures taken in one go, and the kitchen's cleaning kept")
    oc, ec, _owner, _emp = clients()
    _follow(oc), _follow(ec)
    conn = db()
    # The house's own units and rounds are live data. Switched off for the
    # run, so the figures here are this suite's, and put back as they were --
    # by UPDATE, because deleting a row would take its readings with it.
    units_held = [(r["id"], r["active"]) for r in conn.execute(
        "SELECT id, active FROM fridge_units WHERE name NOT LIKE ?", (TAG + "%",))]
    rounds_held = [dict(r) for r in conn.execute(
        "SELECT id, every_days, last_done_on, last_done_by_user_id, active FROM cleaning_rounds")]
    log_before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM cleaning_log").fetchone()[0]
    _cleanup(conn)
    try:
        conn.execute("UPDATE fridge_units SET active = 0 WHERE name NOT LIKE ?", (TAG + "%",))
        conn.commit()
        _fridges(s, oc, ec, conn)
        _cleaning(s, oc, ec, conn)
    finally:
        _cleanup(conn)
        conn.execute("DELETE FROM cleaning_log WHERE id > ?", (log_before,))
        for uid, active in units_held:
            conn.execute("UPDATE fridge_units SET active = ? WHERE id = ?", (active, uid))
        for r in rounds_held:
            conn.execute("""UPDATE cleaning_rounds SET every_days = ?, last_done_on = ?,
                                   last_done_by_user_id = ?, active = ? WHERE id = ?""",
                         (r["every_days"], r["last_done_on"], r["last_done_by_user_id"],
                          r["active"], r["id"]))
        conn.commit()
        conn.close()
    return s


def _unit(conn, name, lo, hi, *, days_old=0, active=1):
    made = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    cur = conn.execute(
        """INSERT INTO fridge_units (name, where_it_is, min_c, max_c, active, created_at)
           VALUES (?, 'Kitchen', ?, ?, ?, ?)""", (f"{TAG} {name}", lo, hi, active, made))
    conn.commit()
    return cur.lastrowid


def _readings(conn, uid):
    return conn.execute("SELECT * FROM fridge_readings WHERE unit_id = ? ORDER BY id",
                        (uid,)).fetchall()


def _fridges(s, oc, ec, conn):
    today = m.house_today()

    s.section("A temperature as somebody types it on a phone")
    for raw, want in (("4", 4.0), ("4,5", 4.5), ("4.5°C", 4.5), (" 3.2 c", 3.2),
                      ("-18", -18.0), ("−18", -18.0), ("45", None), ("abc", None),
                      ("", None)):
        s.check(f"“{raw}” reads as {want}", m.parse_celsius(raw) == want,
                detail=str(m.parse_celsius(raw)))

    fridge = _unit(conn, "Fridge", 0, 5, days_old=5)
    freezer = _unit(conn, "Freezer", -25, -15, days_old=5)
    spare = _unit(conn, "Spare", 0, 5, days_old=5, active=0)

    s.section("Every unit on one screen")
    page = ec.get("/kitchen/fridges").get_data(as_text=True)
    s.check("staff can take the readings", 'name="what" value="all"' in page)
    s.check("a box for each unit in use", f'name="c_{fridge}"' in page and f'name="c_{freezer}"' in page)
    s.check("not for one switched off", f'name="c_{spare}"' not in page)
    s.check("a freezer gets a minus key, a fridge does not",
            f'data-minus="c_{freezer}"' in page and f'data-minus="c_{fridge}"' not in page)
    s.check("and each says it has not been read today", page.count("not read today") >= 2)

    s.section("One press writes them all down")
    r = ec.post_follow("/kitchen/fridges", data={"what": "all", f"c_{fridge}": "4,5",
                                          f"c_{freezer}": "−18", f"c_{spare}": "9"})
    said = " ".join(flashes(r))
    s.check("both readings in", [x["celsius"] for x in _readings(conn, fridge)] == [4.5]
            and [x["celsius"] for x in _readings(conn, freezer)] == [-18.0], detail=said)
    s.check("and nothing for the unit switched off", not _readings(conn, spare))
    s.check("it says how many", "2 readings" in said, detail=said)
    s.check("by whoever took them", _readings(conn, fridge)[0]["read_by_user_id"] is not None)
    page = ec.get("/kitchen/fridges").get_data(as_text=True)
    s.check("the screen now says each was read today, and what it read",
            "read today at" in visible_text(page) and "(4.5°)" in visible_text(page),
            detail=visible_text(page)[:300])

    r = ec.post_follow("/kitchen/fridges", data={"what": "all", f"c_{fridge}": "", f"c_{freezer}": ""})
    s.check("every box empty: nothing written, and it says so",
            "every box was empty" in " ".join(flashes(r))
            and len(_readings(conn, fridge)) == 1)

    r = ec.post_follow("/kitchen/fridges", data={"what": "all", f"c_{fridge}": "45",
                                          f"c_{freezer}": "-19"})
    said = " ".join(flashes(r))
    s.check("a number no fridge reads is named, and the rest still go in",
            f"{TAG} Fridge" in said and "not a temperature" in said
            and len(_readings(conn, fridge)) == 1 and len(_readings(conn, freezer)) == 2,
            detail=said)

    s.section("Out of range, and what was done")
    r = ec.post_follow("/kitchen/fridges", data={"what": "all", f"c_{fridge}": "9"})
    said = " ".join(flashes(r))
    warm = _readings(conn, fridge)[-1]
    s.check("recorded even with nothing said", warm["celsius"] == 9.0 and not warm["action_taken"])
    s.check("and named as outside its range", f"{TAG} Fridge at 9°C" in said, detail=said)
    r = ec.post_follow("/kitchen/fridges", data={"what": "all", f"c_{freezer}": "-8",
                                          f"a_{freezer}": "Door was open, shut it, moved stock"})
    s.check("said at the time, it is kept with the reading",
            _readings(conn, freezer)[-1]["action_taken"] == "Door was open, shut it, moved stock")

    probs = m.fridge_problems(conn, today)
    s.check("the one with nothing said is a problem, the answered one is not",
            [(n, x["celsius"]) for n, x in probs["unanswered"]] == [(f"{TAG} Fridge", 9.0)],
            detail=str([(n, x["celsius"]) for n, x in probs["unanswered"]]))
    home = _home(conn, today)
    s.check("it is a blocker on the owner home", any(
        w["severity"] == "blocker" and "out of range with nothing said" in w["title"]
        and f"{TAG} Fridge: 9°C" in w["detail"] for w in home),
        detail=str([w["title"] for w in home]))
    m.generate_watch_tasks(conn, today)
    task = _task(conn, "A fridge reading out of range with nothing said")
    s.check("and a task, high", task is not None and task["priority"] == "high")
    page = ec.get("/kitchen/fridges").get_data(as_text=True)
    s.check("the reading has a box to say what was done",
            f'/kitchen/fridges/reading/{warm["id"]}/answer' in page)

    r = ec.post_follow(f"/kitchen/fridges/reading/{warm['id']}/answer", data={"action_taken": ""})
    s.check("an empty answer is not one", "Say what was done" in " ".join(flashes(r)))
    ec.post(f"/kitchen/fridges/reading/{warm['id']}/answer",
            data={"action_taken": "Seal replaced, food checked, nothing over 8°C for long"})
    warm = conn.execute("SELECT * FROM fridge_readings WHERE id = ?", (warm["id"],)).fetchone()
    s.check("answered, with who and when",
            warm["action_taken"].startswith("Seal replaced") and warm["answered_by_user_id"]
            and warm["answered_at"])
    r = ec.post_follow(f"/kitchen/fridges/reading/{warm['id']}/answer", data={"action_taken": "again"})
    s.check("and an answer once given is the record", "already written" in
            " ".join(flashes(r)) and warm["action_taken"].startswith("Seal"))
    s.check("no longer a problem", not m.fridge_problems(conn, today)["unanswered"])
    m.generate_watch_tasks(conn, today)
    s.check("the task has closed itself",
            _task(conn, "A fridge reading out of range with nothing said") is None)
    s.check("an answer to a reading that does not exist is a 404",
            ec.post("/kitchen/fridges/reading/999999999/answer",
                    data={"action_taken": "x"}).status_code == 404)

    s.section("A fridge nobody read")
    cold_room = _unit(conn, "Cold room", 0, 4, days_old=3)
    new_one = _unit(conn, "New", 0, 4, days_old=0)
    unread = [u["unit"]["name"] for u in m.fridge_problems(conn, today)["unread"]]
    s.check("not read for two days and more: on the list", f"{TAG} Cold room" in unread,
            detail=str(unread))
    s.check("added today: not late yet", f"{TAG} New" not in unread)
    s.check("read today: not on it", f"{TAG} Fridge" not in unread)
    home = _home(conn, today)
    s.check("a warning on the owner home", any(
        "not read for" in w["title"] and f"{TAG} Cold room" in w["detail"] for w in home))
    m.generate_watch_tasks(conn, today)
    s.check("and a task", _task(conn, "Fridge temperatures not taken") is not None)

    ec.post(f"/kitchen/fridges/unit/{cold_room}", data={"what": "off"})
    s.check("switched off, it is off the list",
            f"{TAG} Cold room" not in [u["unit"]["name"] for u in
                                       m.fridge_problems(conn, today)["unread"]])
    page = ec.get("/kitchen/fridges").get_data(as_text=True)
    s.check("and off the sweep, with a way back",
            f'name="c_{cold_room}"' not in page and "Back in use" in page)
    m.generate_watch_tasks(conn, today)
    s.check("the task closed itself", _task(conn, "Fridge temperatures not taken") is None)
    ec.post(f"/kitchen/fridges/unit/{cold_room}", data={"what": "on"})
    s.check("back in use, it is back on the sweep",
            f'name="c_{cold_room}"' in ec.get("/kitchen/fridges").get_data(as_text=True))
    conn.execute("UPDATE fridge_units SET active = 0 WHERE id IN (?, ?)", (cold_room, new_one))
    conn.commit()

    s.section("A unit's range")
    r = ec.post_follow(f"/kitchen/fridges/unit/{fridge}", data={"what": "range", "min_c": "6",
                                                          "max_c": "2"})
    s.check("colder than it is warm is refused", "Swap them over" in
            " ".join(flashes(r)))
    ec.post(f"/kitchen/fridges/unit/{fridge}", data={"what": "range", "min_c": "1",
                                                      "max_c": "4"})
    row = conn.execute("SELECT min_c, max_c FROM fridge_units WHERE id = ?", (fridge,)).fetchone()
    s.check("a range is saved", (row["min_c"], row["max_c"]) == (1.0, 4.0))
    s.check("an unknown change is refused",
            ec.post(f"/kitchen/fridges/unit/{fridge}", data={"what": "x"}).status_code == 400)


def _round(conn, what):
    return conn.execute(
        """SELECT cleaning_rounds.*, users.name AS last_done_by FROM cleaning_rounds
             LEFT JOIN users ON users.id = cleaning_rounds.last_done_by_user_id
            WHERE what = ?""", (what,)).fetchone()


def _cleaning(s, oc, ec, conn):
    today = m.house_today()

    s.section("The three jobs the owner named are on the kitchen's round")
    rows = {w: _round(conn, w) for w in SEEDED}
    s.check("all three are there, the kitchen's, every week",
            all(r is not None and r["area"] == "Kitchen" and r["every_days"] == 7
                for r in rows.values()), detail=str({w: bool(r) for w, r in rows.items()}))
    for r in rows.values():
        conn.execute("""UPDATE cleaning_rounds SET active = 1, last_done_on = NULL,
                               last_done_by_user_id = NULL, every_days = 7 WHERE id = ?""",
                     (r["id"],))
    conn.commit()
    ovens = rows["Clean the ovens"]["id"]
    dishwasher = rows["Run the dishwasher's cleaning-tablet cycle"]["id"]

    page = visible_text(ec.get("/kitchen/cleaning").get_data(as_text=True))
    s.check("staff open the kitchen's page and see them",
            all(w in page for w in SEEDED), detail=page[:300])
    s.check("never done reads as never recorded, due today",
            "Never recorded" in page and "due today" in page)
    s.check("the page is on the menu", "/kitchen/cleaning" in
            ec.get("/kitchen/fridges").get_data(as_text=True))

    m.generate_watch_tasks(conn, today)
    task = _task(conn, "Kitchen cleaning: Clean the ovens")
    s.check("a job due is a task on the calendar, on its day",
            task is not None and task["due_date"] == today.isoformat(), detail=str(task and dict(task)))

    s.section("Done, and every time kept")
    ec.post(f"/management/cleaning/{ovens}/done", data={"done_on": today.isoformat(),
                                                         "back": "kitchen"})
    r = _round(conn, "Clean the ovens")
    log = conn.execute("SELECT * FROM cleaning_log WHERE round_id = ? ORDER BY id", (ovens,)).fetchall()
    s.check("last done today, by whoever pressed it",
            r["last_done_on"] == today.isoformat() and r["last_done_by_user_id"] is not None)
    s.check("and the time is kept", len(log) == 1 and log[0]["done_on"] == today.isoformat())
    m.generate_watch_tasks(conn, today)
    s.check("the task closed itself", _task(conn, "Kitchen cleaning: Clean the ovens") is None)

    earlier = today - timedelta(days=4)
    r = ec.post(f"/management/cleaning/{ovens}/done",
                data={"done_on": earlier.isoformat(), "note": f"{TAG} forgot to press it",
                      "back": "kitchen"})
    s.check("pressed from the kitchen's page, it goes back there",
            "/kitchen/cleaning" in r.headers.get("Location", ""))
    r = _round(conn, "Clean the ovens")
    log = conn.execute("SELECT * FROM cleaning_log WHERE round_id = ? ORDER BY id", (ovens,)).fetchall()
    s.check("an earlier day entered late is kept, with its note",
            len(log) == 2 and log[1]["done_on"] == earlier.isoformat()
            and log[1]["note"] == f"{TAG} forgot to press it")
    s.check("and leaves last done where it was", r["last_done_on"] == today.isoformat())
    ec.post(f"/management/cleaning/{ovens}/done",
            data={"done_on": (today + timedelta(days=3)).isoformat()})
    s.check("a day in the future is taken as today",
            conn.execute("SELECT MAX(done_on) FROM cleaning_log WHERE round_id = ?",
                         (ovens,)).fetchone()[0] == today.isoformat())
    page = visible_text(ec.get("/kitchen/cleaning").get_data(as_text=True))
    s.check("the page shows when, by whom, and the note",
            f"{TAG} forgot to press it" in page and "Last done" in page, detail=page[:400])

    s.section("How often")
    r = ec.post_follow(f"/management/cleaning/{dishwasher}/every", data={"every_days": "0",
                                                                  "back": "kitchen"})
    s.check("nought days is refused", "A whole number" in " ".join(flashes(r)))
    ec.post(f"/management/cleaning/{dishwasher}/every", data={"every_days": "3"})
    s.check("three days is saved",
            _round(conn, "Run the dishwasher's cleaning-tablet cycle")["every_days"] == 3)

    s.section("A job behind")
    conn.execute("UPDATE cleaning_rounds SET last_done_on = ? WHERE id = ?",
                 ((today - timedelta(days=5)).isoformat(), dishwasher))
    conn.commit()
    home = _home(conn, today)
    s.check("is on the owner home, with how late", any(
        "kitchen cleaning job" in w["title"] and "cleaning-tablet cycle (2 days late)" in w["detail"]
        for w in home), detail=str([(w["title"], w["detail"]) for w in home
                                    if "kitchen" in w["title"]]))
    m.generate_watch_tasks(conn, today)
    task = _task(conn, "Kitchen cleaning: Run the dishwasher's cleaning-tablet cycle")
    s.check("and its task says so, due the day it fell due",
            task is not None and "2 days late" in task["notes"]
            and task["due_date"] == (today - timedelta(days=2)).isoformat(),
            detail=str(task and (task["notes"], task["due_date"])))

    s.section("A job of the kitchen's own")
    ec.post("/kitchen/cleaning", data={"what": f"{TAG} Degrease the hood", "every_days": "14"})
    r = _round(conn, f"{TAG} Degrease the hood")
    s.check("added from the kitchen's page, it is the kitchen's",
            r is not None and r["area"] == "Kitchen" and r["every_days"] == 14)
    s.check("staff cannot take a job off the round",
            ec.post(f"/management/cleaning/{r['id']}/stop").status_code in (302, 303, 403)
            and _round(conn, f"{TAG} Degrease the hood")["active"] == 1)
    page = oc.get("/kitchen/cleaning").get_data(as_text=True)
    s.check("the owner has the button", "Take it off the round" in page)
    oc.post(f"/management/cleaning/{r['id']}/stop", data={"back": "kitchen"})
    s.check("and taken off, it is off the page and its history kept",
            _round(conn, f"{TAG} Degrease the hood")["active"] == 0
            and f"{TAG} Degrease the hood" not in oc.get("/kitchen/cleaning").get_data(as_text=True))
    s.check("the whole round has the button too",
            "Take off" in oc.get("/management/cleaning").get_data(as_text=True))
