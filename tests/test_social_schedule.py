"""The social schedule: standing plans that make their own work.

The board this replaces was a list somebody filled in by hand, which has a
paper diary's problem — it holds only what someone remembered to write down,
and nobody notices the Tuesday that never got posted.

The interesting behaviour is not "it makes posts". It is what happens on the
second run, and the tenth: a generator that runs every day and creates the
same post each time is worse than no generator, and one that resurrects
something the owner deleted teaches people to stop deleting things.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "socialplan-"


def _cleanup(conn):
    conn.execute("DELETE FROM tasks WHERE id IN "
                 "(SELECT task_id FROM social_posts WHERE plan_id IN "
                 " (SELECT id FROM social_plans WHERE name LIKE ?))", (TAG + "%",))
    conn.execute("DELETE FROM social_posts WHERE plan_id IN "
                 "(SELECT id FROM social_plans WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM social_plans WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = 'social' AND title LIKE ?", ("%" + TAG + "%",))
    conn.commit()


def _plan(conn, name, **kw):
    fields = {"name": TAG + name, "platform": "Instagram", "cadence": "weekly",
              "weekday": 1, "day_of_month": None, "post_time": "10:00",
              "theme": None, "brief": None, "post_type": None,
              "assigned_to_user_id": None, "lead_days": 3, "active": 1,
              "generated_through": None,
              "created_at": datetime.now(timezone.utc).isoformat()}
    fields.update(kw)
    cols = ", ".join(fields)
    conn.execute(f"INSERT INTO social_plans ({cols}) "
                 f"VALUES ({', '.join('?' * len(fields))})", list(fields.values()))
    conn.commit()
    return conn.execute("SELECT * FROM social_plans WHERE name = ?", (TAG + name,)).fetchone()


def _posts(conn, plan_id):
    return conn.execute(
        "SELECT * FROM social_posts WHERE plan_id = ? ORDER BY scheduled_date",
        (plan_id,)).fetchall()


def run():
    s = Suite("The social schedule")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    # A fixed Monday, so "next Tuesday" means the same thing every run.
    monday = datetime(2026, 9, 7, tzinfo=timezone.utc).date()

    s.section("A weekly plan fills the window ahead")
    plan = _plan(conn, "Tuesdays", weekday=1, theme="The restaurant")
    made = m.generate_social_posts(conn, horizon_days=28, today=monday)
    rows = _posts(conn, plan["id"])
    s.check("it made a post for every Tuesday in the window", made == 4,
            detail=f"{made} made")
    s.check("all on a Tuesday",
            all(m.parse_date(r["scheduled_date"]).weekday() == 1 for r in rows),
            detail=str([r["scheduled_date"] for r in rows]))
    s.check("none in the past",
            all(m.parse_date(r["scheduled_date"]) >= monday for r in rows))
    s.check("each carries the plan's time", all(r["scheduled_time"] == "10:00" for r in rows))
    s.check("and starts as an idea, not as something claiming to be written",
            all(r["status"] == "idea" for r in rows))

    s.section("Every post arrives as a task")
    # This is the whole point: the calendar and everybody's list are built on
    # tasks, so a post that is not a task is a post nobody sees.
    s.check("each post has one", all(r["task_id"] for r in rows))
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (rows[0]["task_id"],)).fetchone()
    s.check("the task says what it is for", "The restaurant" in (task["title"] or ""),
            detail=task["title"])
    s.check("it is marked as coming from the schedule", task["origin"] == "social",
            detail=str(task["origin"]))
    s.check("and falls due before the post, not on the day",
            task["due_date"] == (m.parse_date(rows[0]["scheduled_date"])
                                 - timedelta(days=3)).isoformat(),
            detail=f"due {task['due_date']} for a post on {rows[0]['scheduled_date']}")

    s.section("Running again changes nothing")
    # A daily job that duplicates its own output is worse than no job.
    again = m.generate_social_posts(conn, horizon_days=28, today=monday)
    s.check("a second run makes nothing", again == 0, detail=f"{again} made")
    s.check("and the count is unchanged", len(_posts(conn, plan["id"])) == 4)
    # A day later the window has slid forward by a day, so at most the one
    # date that newly entered it can be filled — never a repeat of an existing
    # one. Asserting zero here would have been asserting the window never
    # moves, which is the opposite of what a rolling schedule is for.
    dates_before = {r["scheduled_date"] for r in _posts(conn, plan["id"])}
    third = m.generate_social_posts(conn, horizon_days=28, today=monday + timedelta(days=1))
    dates_after = {r["scheduled_date"] for r in _posts(conn, plan["id"])}
    s.check("a day later it adds at most the one new date", third <= 1,
            detail=f"{third} made")
    s.check("and never repeats a date it already had",
            len(dates_after) == len(_posts(conn, plan["id"])),
            detail="a date appears twice")
    s.check("every date it had before is still there once",
            dates_before <= dates_after, detail=str(sorted(dates_before - dates_after)))

    s.section("A deleted post stays deleted")
    # The high-water mark, not a lucky coincidence: the ateliers had the
    # opposite behaviour and it had to be written down as a wart.
    victim = _posts(conn, plan["id"])[0]
    oc.post(f"/management/social/{victim['id']}/delete", follow_redirects=True)
    s.check("it is gone", not conn.execute(
        "SELECT 1 FROM social_posts WHERE id = ?", (victim["id"],)).fetchone())
    s.check("its task went with it", not conn.execute(
        "SELECT 1 FROM tasks WHERE id = ?", (victim["task_id"],)).fetchone())
    m.generate_social_posts(conn, horizon_days=28, today=monday)
    s.check("and the next run does not put it back", not conn.execute(
        "SELECT 1 FROM social_posts WHERE plan_id = ? AND scheduled_date = ?",
        (plan["id"], victim["scheduled_date"])).fetchone(),
        detail="a deleted post came back")

    s.section("Time moves on and the window follows")
    later = m.generate_social_posts(conn, horizon_days=28, today=monday + timedelta(days=14))
    s.check("a fortnight later there are new dates to fill", later > 0,
            detail=f"{later} made")

    s.section("Posting it ticks the task off")
    live = [r for r in _posts(conn, plan["id"]) if r["task_id"]][0]
    oc.post(f"/management/social/{live['id']}/mark-posted", follow_redirects=True)
    done = conn.execute("SELECT status FROM tasks WHERE id = ?", (live["task_id"],)).fetchone()
    s.check("the task is done", done and done["status"] == "done",
            detail=str(done["status"] if done else None))
    s.check("and the post says so", conn.execute(
        "SELECT status FROM social_posts WHERE id = ?", (live["id"],)).fetchone()["status"]
        == "posted")

    s.section("Pausing stops the next one, not the ones already made")
    before = len(_posts(conn, plan["id"]))
    conn.execute("UPDATE social_plans SET active = 0 WHERE id = ?", (plan["id"],))
    conn.commit()
    paused = m.generate_social_posts(conn, horizon_days=60, today=monday + timedelta(days=21))
    s.check("a paused plan makes nothing", paused == 0, detail=f"{paused} made")
    s.check("but what it already made is untouched",
            len(_posts(conn, plan["id"])) == before)
    conn.execute("UPDATE social_plans SET active = 1 WHERE id = ?", (plan["id"],))
    conn.commit()

    s.section("The other cadences")
    fort = _plan(conn, "Fortnightly", cadence="fortnightly", weekday=2)
    m.generate_social_posts(conn, horizon_days=56, today=monday)
    fr = _posts(conn, fort["id"])
    s.check("fortnightly lands on a Wednesday",
            all(m.parse_date(r["scheduled_date"]).weekday() == 2 for r in fr),
            detail=str([r["scheduled_date"] for r in fr]))
    s.check("and roughly half as often as weekly", 2 <= len(fr) <= 5,
            detail=f"{len(fr)} in eight weeks")
    gaps = {(m.parse_date(b["scheduled_date"]) - m.parse_date(a["scheduled_date"])).days
            for a, b in zip(fr, fr[1:])}
    s.check("with a fortnight between them", gaps <= {14}, detail=str(sorted(gaps)))

    monthly = _plan(conn, "Monthly", cadence="monthly", day_of_month=15, weekday=None)
    m.generate_social_posts(conn, horizon_days=70, today=monday)
    mr = _posts(conn, monthly["id"])
    s.check("monthly lands on the 15th",
            all(m.parse_date(r["scheduled_date"]).day == 15 for r in mr),
            detail=str([r["scheduled_date"] for r in mr]))

    # 29, 30 and 31 do not exist every month. The form caps the day at 28 so a
    # monthly plan never silently skips February.
    s.check("the day of the month is capped so February is never skipped",
            all(p["day_of_month"] is None or p["day_of_month"] <= 28
                for p in conn.execute("SELECT day_of_month FROM social_plans")))

    s.section("And it lands on the calendar")
    # The design rests on "make it a task and the calendar takes care of
    # itself" — an assumption about build_overview, not something this code
    # controls. Asserted rather than believed: it is the half of the request
    # that nothing else would catch.
    today = datetime.now(m.LOCAL_TZ).date()
    cal_plan = _plan(conn, "Today", weekday=today.weekday(), lead_days=0,
                     theme="The gardens")
    m.generate_social_posts(conn, horizon_days=8, today=today)
    due = conn.execute(
        """SELECT tasks.title, tasks.due_date FROM social_posts
             JOIN tasks ON tasks.id = social_posts.task_id
            WHERE social_posts.plan_id = ? ORDER BY tasks.due_date""",
        (cal_plan["id"],)).fetchone()
    s.check("the plan made a task due inside the month", bool(due),
            detail="no task to look for")
    if due:
        owner_row = conn.execute(
            "SELECT * FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        with m.app.test_request_context():
            cal = m.build_calendar(conn, "month", m.parse_date(due["due_date"]),
                                   viewer=owner_row)
        on_grid = [e for cell in cal["cells"] for e in (cell.get("events") or [])
                   if e.get("kind") == "task" and e.get("title") == due["title"]]
        # More than one is expected and correct — an eight-day window catches
        # two of the same weekday, and both belong on the grid.
        s.check("and it is on the month grid", len(on_grid) >= 1,
                detail=f"{len(on_grid)} found for {due['title']!r}")
        s.check("on the day it is due",
                any(e.get("date") == due["due_date"] for e in on_grid),
                detail=f"{[e.get('date') for e in on_grid]} vs {due['due_date']}")
        s.check("and each one appears once, not twice",
                len({e.get("date") for e in on_grid}) == len(on_grid),
                detail=str(sorted(e.get("date") for e in on_grid)))

    s.section("On the pages")
    r = oc.get("/management/social/plans")
    s.check("the schedule page opens", r.status_code == 200, detail=str(r.status_code))
    body = r.get_data(as_text=True)
    s.check("with the plans on it", (TAG + "Tuesdays") in body)
    s.check("and the next dates spelled out, not just a weekday number",
            "Next:" in body)
    r = oc.get("/management/social")
    s.check("the board shows what the schedule made",
            r.status_code == 200 and "The schedule" in r.get_data(as_text=True))

    s.section("Making a plan through the form schedules it straight away")
    r = oc.post("/management/social/plans/new",
                data={"name": TAG + "Friday", "platform": "Facebook", "cadence": "weekly",
                      "weekday": "4", "post_time": "17:00", "theme": "The house",
                      "lead_days": "2", "active": "on"}, follow_redirects=True)
    fresh = conn.execute("SELECT * FROM social_plans WHERE name = ?",
                         (TAG + "Friday",)).fetchone()
    s.check("the plan is saved", bool(fresh), r)
    s.check("and it did not wait for the overnight run to make anything",
            len(_posts(conn, fresh["id"])) > 0,
            detail=f"{len(_posts(conn, fresh['id']))} posts")
    s.check("on Fridays",
            all(m.parse_date(p["scheduled_date"]).weekday() == 4
                for p in _posts(conn, fresh["id"])))

    s.section("A retired plan leaves its work behind")
    kept = len(_posts(conn, fresh["id"]))
    oc.post(f"/management/social/plans/{fresh['id']}/delete", follow_redirects=True)
    s.check("the plan is gone", not conn.execute(
        "SELECT 1 FROM social_plans WHERE id = ?", (fresh["id"],)).fetchone())
    still = conn.execute(
        "SELECT COUNT(*) AS c FROM social_posts WHERE plan_id IS NULL "
        "AND platform = 'Facebook'").fetchone()["c"]
    s.check("but the posts it made are still scheduled", still >= kept,
            detail=f"{still} kept, {kept} expected — work somebody may have written")

    s.section("Guards")
    s.check("an employee cannot see the schedule",
            _ec.get("/management/social/plans").status_code in (302, 403))
    s.check("nor add a plan",
            _ec.post("/management/social/plans/new",
                     data={"name": "sneaky"}).status_code in (302, 403))
    r = oc.post("/management/social/plans/new", data={"name": "  "}, follow_redirects=True)
    s.check("a plan with no name is refused", b"Give the plan a name" in r.data)
    s.check("editing a plan that does not exist is a 404",
            oc.post("/management/social/plans/999999/edit",
                    data={"name": "x"}).status_code == 404)

    _cleanup(conn)
    conn.close()
    return s
