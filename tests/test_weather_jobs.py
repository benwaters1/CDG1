"""The forecast, turned into the day's outdoor jobs.

Asked for on 22 October 2026. The house is at 900 metres in the Ariège, and
three kinds of weather each come with jobs that only count if they are done
BEFORE it arrives: the taps drained the evening before a frost, the terrace
furniture tied down before the wind, the beds watered on a hot day. The owner
sets a line for each and writes the list; when the forecast crosses a line,
each job becomes a task for the day it needs doing, saying what was forecast.

WHAT THIS PINS, every part of it against a forecast written straight into the
cache and a clock frozen at a chosen instant -- nothing reaches Open-Meteo
(_harness.py stands fetch_forecast down and asserts it at import):

  - A FROST RAISES THE EVENING'S JOBS: a −3°C dawn on Wednesday is Tuesday
    evening's work, on the calendar and on the Today list of whoever the owner
    named, with the forecast in the title.
  - ONCE PER EVENT: the same night fetched again is the same tasks, and a
    deeper frost rewrites them rather than adding two more.
  - THEY CLOSE THEMSELVES when the forecast moves clear, saying why in the
    task, and come back as the SAME task if the frost does. A tick is a
    person's and is never undone. A forecast wobbling round the line does not
    close a job; the end of a forecast is not the weather going away; and a
    forecast too old to trust does nothing at all.
  - THE OWNER'S LINE: moving it raises or withdraws at once, and a line that
    is a typing mistake is refused with the old one kept.
  - THE HOUSE'S DAY: at half past midnight -- the house on the new day, UTC
    still on the old one -- yesterday's jobs are yesterday's, and tonight's are
    filed under the house's today. Winter and summer both.
"""
import json
from datetime import date, datetime as real_datetime, time as real_time, timedelta, timezone

from _harness import Suite, clients, db, flashes, keep_rows

import _harness

m = _harness.m
PARIS = m.LOCAL_TZ

TUESDAY = date(2026, 11, 10)                 # winter: the house is UTC+1
NOW = real_datetime(2026, 11, 10, 14, 0, tzinfo=PARIS)
TAPS = "Drain and cover the outdoor taps"
POTS = "Bring in the pots"


class _FrozenDatetime(m.datetime):
    """datetime.now() pinned to one instant, as test_house_day pins it."""

    _at = None

    @classmethod
    def now(cls, tz=None):
        assert cls._at is not None, "nothing pinned"
        return cls._at.astimezone(tz) if tz else cls._at.replace(tzinfo=None)


def _at(instant, fn):
    """Run fn() as if it were `instant`, then put the real clock back."""
    real = m.datetime
    _FrozenDatetime._at = instant
    m.datetime = _FrozenDatetime
    try:
        return fn()
    finally:
        m.datetime = real


def hours(first_day, weather, days=8):
    """Hour by hour from midnight at the house on first_day, as the cache keeps
    them: [UTC moment, °C, gust km/h, code]. Stepped in UTC, so a clock-change
    night has the hours it really has."""
    out = []
    t = real_datetime.combine(first_day, real_time(0), tzinfo=PARIS).astimezone(timezone.utc)
    end = real_datetime.combine(first_day + timedelta(days=days), real_time(0),
                                tzinfo=PARIS).astimezone(timezone.utc)
    while t < end:
        temp, gust, code = weather(t.astimezone(PARIS))
        out.append([t.isoformat(), temp, gust, code])
        t += timedelta(hours=1)
    return out


def mild(local):
    return 8.0, 20, 3


def frosty(low, night=date(2026, 11, 11)):
    """A frost falling before dawn on `night`, coldest at 06:00."""
    def weather(local):
        if local.date() == night and 2 <= local.hour <= 7:
            return (low if local.hour == 6 else low + 1), 10, 0
        return mild(local)
    return weather


def put_forecast(conn, hrs, at):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (m.FORECAST_SETTING, json.dumps({"at": at.astimezone(timezone.utc).isoformat(),
                                         "hours": hrs})))
    conn.commit()


def jobs(conn, kind=None, day=None):
    sql = """SELECT weather_jobs.*, tasks.id AS tid, tasks.title, tasks.notes, tasks.status,
                    tasks.due_date, tasks.origin, tasks.assigned_to_user_id AS who
               FROM weather_jobs LEFT JOIN tasks ON tasks.id = weather_jobs.task_id
              WHERE 1 = 1"""
    args = []
    if kind:
        sql += " AND weather_jobs.kind = ?"
        args.append(kind)
    if day:
        sql += " AND weather_jobs.job_day = ?"
        args.append(day.isoformat())
    return conn.execute(sql + " ORDER BY weather_jobs.id", args).fetchall()


def reset(conn):
    """Forget every job this suite raised, and its tasks."""
    ids = [r["task_id"] for r in conn.execute(
        "SELECT task_id FROM weather_jobs WHERE task_id IS NOT NULL")]
    conn.execute("DELETE FROM weather_jobs")
    for tid in ids:
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'weather_jobs_%'")
    conn.commit()


def form(**changes):
    """The owner's form as the page sends it, with the house's defaults."""
    data = {"form": "weather_jobs", "days_ahead": "2",
            "frost_on": "1", "frost_line": "1", "frost_list": f"{TAPS}\n{POTS}",
            "storm_on": "1", "storm_line": "70", "storm_thunder": "1",
            "storm_list": "Stack or tie down the terrace furniture\nClose the shutters",
            "heat_on": "1", "heat_line": "30", "heat_list": "Water the beds\nPut out the parasols"}
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def run():
    s = Suite("the forecast as the day's outdoor jobs")
    oc, ec, _owner, emp = clients()
    conn = db()
    # Put back in the reverse of this order: the tasks before the jobs that
    # point at them, or the foreign key refuses the jobs.
    put_back = [keep_rows(conn, "app_settings",
                          "key LIKE 'weather_jobs_%' OR key IN (?, ?)",
                          (m.FORECAST_SETTING, "automation_weather_jobs_enabled")),
                keep_rows(conn, "weather_jobs", "1 = 1")]
    held_tasks = [r["task_id"] for r in conn.execute(
        "SELECT task_id FROM weather_jobs WHERE task_id IS NOT NULL")]
    if held_tasks:
        put_back.append(keep_rows(conn, "tasks", "id IN (%s)" % ",".join("?" * len(held_tasks)),
                                  tuple(held_tasks)))
    real_fetch, real_urlopen = m.fetch_forecast, m.urlopen
    try:
        _sections(s, conn, oc, ec, emp, real_fetch, real_urlopen)
    finally:
        m.fetch_forecast, m.urlopen = real_fetch, real_urlopen
        reset(conn)
        for undo in reversed(put_back):
            undo()
        conn.close()
    return s


def _sections(s, conn, oc, ec, emp, blocked_fetch, real_urlopen):
    def gen(at=NOW):
        return _at(at, lambda: m.generate_weather_jobs(conn))

    reset(conn)

    s.section("Nothing here reaches the network")
    s.check("the forecast fetch is blocked under test",
            m.fetch_forecast.__name__ == "_blocked",
            detail="it needs no key and costs nothing, which is why it is the "
                   "one that gets forgotten")

    # ------------------------------------------------------------------
    s.section("A frost raises the evening's jobs")
    put_forecast(conn, hours(TUESDAY, frosty(-3)), NOW - timedelta(minutes=10))
    r = gen()
    raised = jobs(conn, "frost")
    s.check("one task for each job on the frost list", r["made"] == 2 and len(raised) == 2,
            detail=f"{r} / {len(raised)}")
    s.check("on Tuesday, the evening before -- not Wednesday, when the frost falls",
            all(j["due_date"] == "2026-11-10" for j in raised),
            detail=str([j["due_date"] for j in raised]))
    titles = sorted(j["title"] for j in raised)
    s.check("each named for the forecast that caused it",
            titles == sorted([f"{POTS} — −3°C forecast tonight",
                              f"{TAPS} — −3°C forecast tonight"]),
            detail=str(titles))
    s.check("with the hour and the night in the notes",
            all("around 06:00 on Wednesday 11 November" in j["notes"] for j in raised),
            detail=raised[0]["notes"] if raised else "")
    s.check("open, and marked as the forecast's", all(
        j["status"] == "open" and j["origin"] == "weather" for j in raised))
    s.check("and nothing for wind or heat on a calm, cool week",
            not jobs(conn, "storm") and not jobs(conn, "heat"))

    cal = _at(NOW, lambda: oc.get("/calendar?view=day&date=2026-11-10"))
    page = cal.get_data(as_text=True)
    s.check("on the calendar, on its day", cal.status_code == 200 and TAPS in page
            and "−3°C forecast tonight" in page, cal)

    s.section("And on the Today list of whoever the owner names")
    r = _at(NOW, lambda: oc.post("/management/weather-jobs",
                                 data=form(frost_who=str(emp["id"]))))
    s.check("the owner's page takes the name", r.status_code in (302, 303), r)
    s.check("the open jobs move to that person's list rather than being raised twice",
            all(j["who"] == emp["id"] for j in jobs(conn, "frost")) and len(jobs(conn, "frost")) == 2,
            detail=str([(j["who"], j["tid"]) for j in jobs(conn, "frost")]))
    today = _at(NOW, lambda: ec.get("/today"))
    page = today.get_data(as_text=True)
    s.check("and they are on that person's Today", today.status_code == 200
            and TAPS in page and POTS in page, today)

    # ------------------------------------------------------------------
    s.section("Once per event")
    before = {j["tid"] for j in jobs(conn, "frost")}
    r = gen(NOW + timedelta(minutes=30))
    s.check("the same night fetched again raises nothing", r["made"] == 0, detail=str(r))
    s.check("and leaves the same two tasks", {j["tid"] for j in jobs(conn, "frost")} == before)

    # Through the real job: the fetch stood in for, an hour later, a frost
    # that has deepened. That is the refetch the house makes every hour.
    later = NOW + timedelta(hours=1)
    m.fetch_forecast = lambda *a, **k: {"at": later.astimezone(timezone.utc).isoformat(),
                                        "hours": hours(TUESDAY, frosty(-5))}
    try:
        said = _at(later, lambda: m.run_weather_jobs_job(conn))
    finally:
        m.fetch_forecast = blocked_fetch
    after = jobs(conn, "frost")
    s.check("a deeper frost on the next fetch is still two tasks",
            len(after) == 2 and {j["tid"] for j in after} == before,
            detail=f"{said} / {[(j['tid'], j['title']) for j in after]}")
    s.check("rewritten in place to say so",
            all("−5°C forecast tonight" in j["title"] for j in after),
            detail=str([j["title"] for j in after]))
    s.check("and the job says what it did", "rewritten" in said and "raised" not in said,
            detail=said)
    count = conn.execute("SELECT COUNT(*) FROM tasks WHERE origin = 'weather' "
                         "AND due_date = '2026-11-10' AND status != 'done'").fetchone()[0]
    s.check("no stray copy anywhere in the task list", count == 2, detail=str(count))

    # ------------------------------------------------------------------
    s.section("A forecast that wobbles round the line does not close them")
    put_forecast(conn, hours(TUESDAY, frosty(1.8)), NOW)
    r = gen()
    s.check("1.8°C under a line of 1°C is inside the margin, so the jobs stay",
            r["withdrawn"] == 0 and all(j["status"] == "open" for j in jobs(conn, "frost")),
            detail=str(r))

    s.section("A changed forecast closes them, and says why")
    put_forecast(conn, hours(TUESDAY, frosty(6)), NOW)
    r = gen()
    gone = jobs(conn, "frost")
    s.check("both withdrawn when the frost is no longer coming",
            r["withdrawn"] == 2 and all(j["status"] == "done" for j in gone), detail=str(r))
    s.check("each saying why, in the task itself",
            all("Closed on its own" in j["notes"] and "6°C at the coldest" in j["notes"]
                for j in gone), detail=gone[0]["notes"] if gone else "")
    s.check("and the house knows it closed them rather than somebody doing them",
            all(j["state"] == "withdrawn" for j in gone))
    page = _at(NOW, lambda: oc.get("/management/weather-jobs?stands=Withdrawn")).get_data(as_text=True)
    s.check("which the owner's page shows as withdrawn, with the reason",
            "Withdrawn" in page and "at the coldest" in page)

    s.section("And the frost coming back is the same job, not a new one")
    put_forecast(conn, hours(TUESDAY, frosty(-2)), NOW)
    r = gen()
    back = jobs(conn, "frost")
    s.check("reopened", r["reopened"] == 2 and all(j["status"] == "open" for j in back),
            detail=str(r))
    s.check("the same two tasks", {j["tid"] for j in back} == before)

    s.section("A tick is a person's, and nothing here undoes it")
    taps = next(j for j in back if j["job"] == TAPS)
    conn.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
                 (NOW.isoformat(), taps["tid"]))
    conn.commit()
    put_forecast(conn, hours(TUESDAY, frosty(7)), NOW)
    gen()
    ticked = conn.execute("SELECT * FROM tasks WHERE id = ?", (taps["tid"],)).fetchone()
    s.check("a ticked job the forecast stops asking for is left as ticked",
            ticked["status"] == "done" and "Closed on its own" not in (ticked["notes"] or ""),
            detail=ticked["notes"])
    put_forecast(conn, hours(TUESDAY, frosty(-4)), NOW)
    gen()
    ticked = conn.execute("SELECT status FROM tasks WHERE id = ?", (taps["tid"],)).fetchone()
    s.check("and the frost coming back does not reopen it", ticked["status"] == "done")

    s.section("A task somebody deleted stays deleted")
    pots = next(j for j in jobs(conn, "frost") if j["job"] == POTS)
    conn.execute("DELETE FROM tasks WHERE id = ?", (pots["tid"],))
    conn.commit()
    r = gen()
    s.check("it is not raised again", r["made"] == 0 and not any(
        j["tid"] for j in jobs(conn, "frost") if j["job"] == POTS), detail=str(r))

    # ------------------------------------------------------------------
    s.section("The end of a forecast is not the weather going away")
    reset(conn)
    put_forecast(conn, hours(TUESDAY, frosty(-3, night=date(2026, 11, 12))), NOW)
    gen()
    wed = jobs(conn, "frost", date(2026, 11, 11))
    s.check("Wednesday night's frost raises Wednesday's jobs", len(wed) == 2)
    put_forecast(conn, hours(TUESDAY, mild, days=1), NOW)
    r = gen()
    s.check("a forecast that stops before Wednesday withdraws nothing",
            r["withdrawn"] == 0 and all(j["status"] == "open"
                                        for j in jobs(conn, "frost", date(2026, 11, 11))),
            detail=str(r))

    s.section("A forecast too old to trust does nothing")
    put_forecast(conn, hours(TUESDAY, mild), NOW - timedelta(hours=13))
    r = gen()
    s.check("nothing raised or withdrawn from it", r["withdrawn"] == 0 and r["made"] == 0
            and r["skipped"], detail=str(r))
    page = _at(NOW, lambda: oc.get("/management/weather-jobs")).get_data(as_text=True)
    s.check("and the owner's page says why", "Older than 12 hours" in page)

    # ------------------------------------------------------------------
    s.section("The line is the owner's")
    reset(conn)
    put_forecast(conn, hours(TUESDAY, frosty(2)), NOW)
    r = gen()
    s.check("2°C is no frost under the house's line of 1°C", r["made"] == 0, detail=str(r))
    resp = _at(NOW, lambda: oc.post("/management/weather-jobs", data=form(frost_line="3")))
    s.check("the owner raises the line to 3°C", resp.status_code in (302, 303), resp)
    s.check("and it is kept as live config",
            m.weather_job_settings(conn)["kinds"]["frost"]["line"] == 3.0)
    raised = jobs(conn, "frost")
    s.check("the jobs are raised at once, from the forecast already held",
            len(raised) == 2 and all(j["status"] == "open" for j in raised),
            detail=str([(j["title"], j["status"]) for j in raised]))
    s.check("saying 2°C", all("2°C forecast tonight" in j["title"] for j in raised),
            detail=str([j["title"] for j in raised]))
    resp = _at(NOW, lambda: oc.post("/management/weather-jobs", data=form(frost_line="-1")))
    gone = jobs(conn, "frost")
    s.check("lowering it again withdraws them at once -- a decision, not a wobble",
            all(j["status"] == "done" for j in gone), detail=str([j["status"] for j in gone]))
    s.check("saying the line moved", all("line was moved to −1°C" in j["notes"]
                                         for j in gone),
            detail=gone[0]["notes"] if gone else "")
    _at(NOW, lambda: oc.post("/management/weather-jobs", data=form(frost_line="50")))
    said = flashes(_at(NOW, lambda: oc.get("/management/weather-jobs")))
    s.check("a line that is a typing mistake is refused, saying so",
            any("Kept as it was: the frost line" in f for f in said), detail=str(said))
    s.check("and the line already set is kept",
            m.weather_job_settings(conn)["kinds"]["frost"]["line"] == -1.0)

    reset(conn)

    def gale(local):
        if local.date() == date(2026, 11, 11) and local.hour == 15:
            return 8.0, 65, 3
        return mild(local)
    put_forecast(conn, hours(TUESDAY, gale), NOW)
    s.check("gusts of 65 km/h are no storm under 70", gen()["made"] == 0)
    _at(NOW, lambda: oc.post("/management/weather-jobs", data=form(storm_line="60")))
    storm = jobs(conn, "storm")
    s.check("under a line of 60 they raise the storm jobs, for the day the wind comes",
            len(storm) == 2 and all(j["due_date"] == "2026-11-11" for j in storm),
            detail=str([(j["due_date"], j["title"]) for j in storm]))
    s.check("named for the gusts",
            all("gusts of 65 km/h forecast this afternoon" in j["title"] for j in storm),
            detail=str([j["title"] for j in storm]))

    s.section("The owner's list and switches")
    _at(NOW, lambda: oc.post("/management/weather-jobs",
                             data=form(storm_line="60", storm_list="Close the shutters")))
    left = {j["job"]: j for j in jobs(conn, "storm")}
    s.check("a job taken off the list is withdrawn, saying so",
            left["Stack or tie down the terrace furniture"]["status"] == "done"
            and "no longer on the storm list" in left["Stack or tie down the terrace furniture"]["notes"])
    s.check("and the one still listed stays", left["Close the shutters"]["status"] == "open")
    _at(NOW, lambda: oc.post("/management/weather-jobs",
                             data=form(storm_line="60", storm_on=None)))
    shut = next(j for j in jobs(conn, "storm") if j["job"] == "Close the shutters")
    s.check("switching the kind off withdraws its open jobs, saying so",
            shut["status"] == "done" and "storm jobs have been switched off" in shut["notes"],
            detail=shut["notes"])
    before = m.weather_job_settings(conn)
    resp = _at(NOW, lambda: oc.post("/management/weather-jobs", data={}))
    s.check("an empty post switches nothing off and empties no list",
            m.weather_job_settings(conn) == before, resp)

    # ------------------------------------------------------------------
    s.section("The day a job is for")
    reset(conn)

    def morning_gale(local):
        if local.date() == date(2026, 11, 12) and local.hour in (4, 10):
            return 8.0, 90, 3
        return mild(local)
    put_forecast(conn, hours(TUESDAY, morning_gale), NOW)
    gen()
    days = sorted({j["due_date"] for j in jobs(conn, "storm")})
    # 04:00 Thursday is Wednesday evening's job; 10:00 Thursday is Thursday's.
    # 10:00 at the house is 09:00 UTC in November, so reading the hour in UTC
    # files the second one under Wednesday as well.
    s.check("a gale at four in the morning is prepared for the evening before, and one "
            "at ten that morning is that day's", days == ["2026-11-11", "2026-11-12"],
            detail=str(days))
    wed = jobs(conn, "storm", date(2026, 11, 11))
    s.check("and the evening before says tonight",
            all("forecast tonight" in j["title"] for j in wed), detail=str([j["title"] for j in wed]))

    # ------------------------------------------------------------------
    for label, now, night_before in (
            ("in winter, at 00:30", real_datetime(2026, 11, 11, 0, 30, tzinfo=PARIS), TUESDAY),
            ("in summer, at 00:30", real_datetime(2026, 7, 16, 0, 30, tzinfo=PARIS),
             date(2026, 7, 15))):
        s.section("The house's day, " + label)
        house_day = now.date()
        utc_day = now.astimezone(timezone.utc).date()
        s.check("the two clocks really do disagree at this instant", house_day != utc_day,
                detail=f"{house_day} at the house, {utc_day} in UTC -- if these ever "
                       "match, this section is proving nothing")
        reset(conn)
        # Yesterday evening's jobs, raised in the afternoon as the house would.
        afternoon = real_datetime.combine(night_before, real_time(15), tzinfo=PARIS)
        tonight = house_day + timedelta(days=1)
        put_forecast(conn, hours(night_before, frosty(-3, night=house_day)), afternoon)
        _at(afternoon, lambda: m.generate_weather_jobs(conn))
        s.check("yesterday evening's jobs were raised for yesterday",
                len(jobs(conn, "frost", night_before)) == 2)
        # Half past midnight: Open-Meteo's hours now start at the house's
        # midnight, with another frost tonight and a hot or gusty afternoon.
        def weather(local):
            if local.date() == tonight and 2 <= local.hour <= 7:
                return -4.0, 10, 0
            if local.date() == house_day and local.hour == 15:
                return 33.0, 20, 0
            return mild(local)
        put_forecast(conn, hours(house_day, weather), now - timedelta(minutes=5))
        r = _at(now, lambda: m.generate_weather_jobs(conn))
        old = jobs(conn, "frost", night_before)
        s.check("yesterday's are closed: its day has gone by at the house",
                r["passed"] == 2 and all(j["status"] == "done" and j["state"] == "passed"
                                         for j in old),
                detail=f"{r} -- by UTC it is still {utc_day}, and they would stay open")
        s.check("saying nobody ticked them",
                all("nobody ticked it" in j["notes"] for j in old))
        new = jobs(conn, "frost", house_day)
        s.check("tonight's frost is filed under the house's today",
                len(new) == 2 and all(j["due_date"] == house_day.isoformat() for j in new),
                detail=str([j["due_date"] for j in jobs(conn, "frost")]))
        s.check("and nothing new is filed under the UTC day",
                not [j for j in jobs(conn) if j["job_day"] == utc_day.isoformat()
                     and j["status"] != "done"])
        hot = jobs(conn, "heat", house_day)
        s.check("the afternoon's heat is today's job too",
                len(hot) == 2 and all("33°C forecast this afternoon" in j["title"]
                                      for j in hot),
                detail=str([j["title"] for j in hot]))

    # ------------------------------------------------------------------
    s.section("The real parser reads Open-Meteo's reply")
    first = int(real_datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc).timestamp())

    class _Reply:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    canned = json.dumps({"hourly": {"time": [first, first + 3600, first + 7200],
                                    "temperature_2m": [-2.46, None, -1.0],
                                    "wind_gusts_10m": [12.6, None, 30],
                                    "weather_code": [0, None, 95]}}).encode()
    asked = []
    m.urlopen = lambda req, timeout=None: (asked.append(req.full_url), _Reply(canned))[1]
    try:
        got = _harness.REAL_FETCH_FORECAST()
    finally:
        m.urlopen = real_urlopen
    s.check("it asked for hours, as Unix time", asked and "hourly=" in asked[0]
            and "timeformat=unixtime" in asked[0], detail=str(asked))
    s.check("an hour with nothing in it is dropped", len(got["hours"]) == 2,
            detail=str(got["hours"]))
    s.check("and the times are kept as UTC moments",
            got["hours"][0][0] == "2026-11-10T23:00:00+00:00", detail=str(got["hours"][0]))
    s.check("so midnight on the 11th at the house is the evening of the 10th's job",
            m.weather_job_day(real_datetime.fromisoformat(got["hours"][0][0]).astimezone(PARIS))
            == date(2026, 11, 10),
            detail="23:00 UTC is midnight in Paris in November, which is before ten")

    # ------------------------------------------------------------------
    s.section("It is a registered job, so it can be turned off")
    names = {j[0] for j in m.AUTOMATION_JOBS}
    s.check("weather jobs is in the registry", "weather_jobs" in names)
    s.check("with a switch of its own, apart from the reading on the pages",
            "automation_weather_jobs_enabled" in m.AUTOMATION_SETTING_DEFAULTS)
    s.check("and a name on the job-status page", "weather_jobs" in m.AUTOMATION_JOB_LABELS)

    s.section("The page is the owner's")
    page = _at(NOW, lambda: oc.get("/management/weather-jobs"))
    s.check("it opens", page.status_code == 200, page)
    s.check("with the lines and the lists", "When the night's low is below" in
            page.get_data(as_text=True) and TAPS in page.get_data(as_text=True))
    r = ec.get("/management/weather-jobs")
    s.check("a member of staff cannot open it", r.status_code in (302, 403),
            detail=str(r.status_code))
    before = m.weather_job_settings(conn)
    r = ec.post("/management/weather-jobs", data=form(frost_line="5"))
    s.check("or change a line", r.status_code in (302, 403)
            and m.weather_job_settings(conn) == before, detail=str(r.status_code))


if __name__ == "__main__":
    print(run().report())
