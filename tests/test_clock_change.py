"""The two nights a year the clocks change, and what reads a day differently.

Twice a year the house has a day that is not twenty-four hours long, and three
separate things in this app measure a day: the till's service day, which runs
to five in the morning; the timesheet, which works from UTC instants; and the
rota, which is wall-clock strings a person typed. On three hundred and
sixty-three nights all three agree. On two they do not, and none of them is
wrong -- which is precisely why nobody notices until the payslip.

  THE DATES ARE READ OUT OF THE ZONE. "The last Sunday in March" is EU law as
  it stands, has been amended before, and the EU has already voted once to
  abolish the change. Hard-coding it means that the year it stops, the app
  keeps announcing a change that is not coming.

  THE TILL'S DAY IS 23 OR 25 HOURS, AND THE WINDOW SAYS SO. The service window
  is built from local wall-clock times exactly so that it stretches. This pins
  it, because a window rebuilt from UTC arithmetic would look identical in
  code review and be an hour wrong twice a year.

  THE ROLLOVER HOUR IS NOT ONE OF THE TWO BAD ONES. It comes from an
  environment variable. Set it to 2 and the service day starts at a wall-clock
  time that does not exist one night in March; zoneinfo moves it silently, so
  nothing errors and the takings are summed over the wrong window.

  AND SOMEBODY IS TOLD BEFORE THE NIGHT. A shift written 22:00 to 06:00 is
  nine real hours on one of these nights and seven on the other. The timesheet
  will record the real figure and be right; the argument is about whether that
  is what the house meant to pay, and that is a conversation to have in
  advance, not on the twenty-eighth.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZCC"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM shifts WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("The two clock-change nights")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("The house knows which two nights they are")
    # 2026: 29 March forward, 25 October back. 2027: 28 March, 31 October.
    # Written out rather than computed, so that a wrong rule cannot agree with
    # itself -- deriving the expected answer the same way as the code would
    # test nothing at all.
    for year, expected in ((2026, [(date(2026, 3, 29), "forward"),
                                   (date(2026, 10, 25), "back")]),
                           (2027, [(date(2027, 3, 28), "forward"),
                                   (date(2027, 10, 31), "back")]),
                           (2030, [(date(2030, 3, 31), "forward"),
                                   (date(2030, 10, 27), "back")])):
        s.check(f"{year} is {expected[0][0].isoformat()} and {expected[1][0].isoformat()}",
                m.clock_change_days(year) == expected,
                detail=str(m.clock_change_days(year)))
    s.check("and it is two nights, not one and not three",
            all(len(m.clock_change_days(y)) == 2 for y in (2024, 2025, 2026, 2027)))

    s.section("The till's service day stretches with it")
    # THE ONE THAT COSTS MONEY. The window is built from local wall-clock
    # times so that it stretches; rebuilt from UTC arithmetic it would look
    # identical in review and be an hour wrong twice a year. The service day
    # that stretches is the one BEFORE the change, because it runs to five in
    # the morning of the change day.
    s.check("the Saturday before the March change is a 23-hour service",
            m.service_day_length_hours(date(2026, 3, 28)) == 23.0,
            detail=str(m.service_day_length_hours(date(2026, 3, 28))))
    s.check("and the one before the October change is 25",
            m.service_day_length_hours(date(2026, 10, 24)) == 25.0,
            detail=str(m.service_day_length_hours(date(2026, 10, 24))))
    s.check("an ordinary service is 24",
            m.service_day_length_hours(date(2026, 6, 15)) == 24.0)
    start, end = m.service_day_window(date(2026, 10, 24))
    s.check("and the window itself is what stretched, not a second opinion",
            start.startswith("2026-10-24T03:00") and end.startswith("2026-10-25T04:00"),
            detail=f"{start} .. {end}")

    s.section("The rollover hour is not one of the two that go wrong")
    s.check("five in the morning is safe on both nights",
            m.rollover_hour_is_safe(),
            detail="POS_SERVICE_ROLLOVER_HOUR is currently "
                   + str(m.POS_SERVICE_ROLLOVER_HOUR))
    # And the guard is a real one: two and three are the hours that go missing
    # and happen twice, and it has to say so.
    was = m.POS_SERVICE_ROLLOVER_HOUR
    try:
        m.POS_SERVICE_ROLLOVER_HOUR = 2
        s.check("two in the morning is not",
                not m.rollover_hour_is_safe(),
                detail="02:00 does not exist on the March night; zoneinfo "
                       "moves it forward without complaining")
        m.POS_SERVICE_ROLLOVER_HOUR = 3
        s.check("but three is, and the guard has to know the difference",
                m.rollover_hour_is_safe(),
                detail="in Paris the missing hour and the doubled hour are "
                       "both 02:00-02:59; three exists exactly once on both "
                       "nights, and a guard that refused it would be refusing "
                       "a setting that works")
        m.POS_SERVICE_ROLLOVER_HOUR = 0
        s.check("and midnight is, which is what most tills use",
                m.rollover_hour_is_safe())
    finally:
        m.POS_SERVICE_ROLLOVER_HOUR = was
    s.check("two o'clock is refused on the March night, where it does not exist",
            not m.rollover_hour_is_safe(hour=2, day=date(2026, 3, 29)),
            detail="the hour between two and three is skipped entirely")
    s.check("and on the October night, where it happens twice",
            not m.rollover_hour_is_safe(hour=2, day=date(2026, 10, 25)),
            detail="02:30 has two right answers that night, which is not a "
                   "question a takings window can be asked to decide")
    s.check("and five is fine on both",
            m.rollover_hour_is_safe(hour=5, day=date(2026, 3, 29))
            and m.rollover_hour_is_safe(hour=5, day=date(2026, 10, 25)))

    s.section("A night shift across the change is not the hours on the roster")
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + " Night Porter", "zzcc.porter@example.invalid",
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE name = ?",
                       (TAG + " Night Porter",)).fetchone()["id"]
    # The night the clocks go back, and the night they go forward, in whichever
    # year is next -- so this suite does not expire.
    today = m.house_today()
    upcoming = []
    for year in (today.year, today.year + 1):
        for day, direction in m.clock_change_days(year):
            if day >= today and len(upcoming) < 2:
                upcoming.append((day, direction))
    for day, _direction in upcoming:
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time, created_at)
               VALUES (?, ?, '22:00', '06:00', ?)""",
            (uid, (day - timedelta(days=1)).isoformat(),
             m.datetime.now(m.timezone.utc).isoformat()))
        # And a day shift on the very same night, which does NOT cross the
        # change. Without it, "every shift on those two dates" and "the shifts
        # that cross" are the same list and cannot be told apart.
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time, created_at)
               VALUES (?, ?, '09:00', '17:00', ?)""",
            (uid, (day - timedelta(days=1)).isoformat(),
             m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()

    try:
        first_day, first_dir = upcoming[0]
        # Asked from the day before the change, so the window always contains it.
        as_of = first_day - timedelta(days=3)
        crossing = m.shifts_across_clock_change(conn, today=as_of, within_days=14)
        s.check("the shift is found",
                len(crossing) == 1, detail=str(len(crossing)))
        expected = 9.0 if first_dir == "back" else 7.0
        s.check(f"and it is really {expected:g} hours, not the eight on the roster",
                crossing and crossing[0]["worked"] == expected
                and crossing[0]["rostered"] == 8.0,
                detail=str(crossing[0] if crossing else None))
        s.check("a day shift on the same date is not on it",
                all("22:00" in (c["shift"]["start_time"] or "") for c in crossing),
                detail="the 09:00 shift below is on the same night and does "
                       "not cross the change: " +
                       str([(c["shift"]["start_time"], c["worked"]) for c in crossing]))
        s.check("and an ordinary night is not on the list at all",
                m.shifts_across_clock_change(
                    conn, today=first_day + timedelta(days=1),
                    within_days=1) == [],
                detail="a list that always has something on it is furniture")

        s.section("And it stays off the panel that has to be able to be empty")
        # DELIBERATELY NOT ON THE OWNER HOME. Ten of the eighteen watch-task
        # kinds are tasks with no homepage line, and this belongs with them:
        # that panel's whole discipline is that it can be empty, and a line
        # that appears for a fortnight twice a year whether or not anything
        # needs doing is exactly what turns it into furniture.
        with m.app.test_request_context("/"):
            warnings = m.owner_home_warnings(conn, as_of)
        s.check("nothing about the clocks is on the owner's home page",
                not [w for w in warnings if "clock" in w["title"].lower()],
                detail=str([w["title"] for w in warnings]))

        s.section("It becomes a task, so it reaches a person and the calendar")
        with m.app.test_request_context("/"):
            found, _dropped = m.watch_task_findings(conn, as_of)
        mine = [f for f in found if f[0] == "clocks"]
        s.check("a task is raised for the person on that night",
                len(mine) == 1, detail=str([f[1] for f in found if f[0] == 'clocks']))
        s.check("named for them, not for the difference",
                mine and TAG in mine[0][1] and "hour" not in mine[0][1],
                detail="the title is the dedupe key, so anything that moves "
                       "raises a fresh task every morning: " +
                       (mine[0][1] if mine else ""))
        s.check("and the note says what the till's night is too",
                mine and ("-hour service" in mine[0][2]),
                detail="the person cashing up is the person who needs that: "
                       + (mine[0][2] if mine else ""))
        s.check("and the note says what they will actually be paid for",
                mine and ("9 " in mine[0][2] or "7 " in mine[0][2]),
                detail=mine[0][2][:160] if mine else "")
        s.check("and it closes itself once the night has gone",
                not [f for f in m.watch_task_findings(
                    conn, first_day + timedelta(days=1))[0] if f[0] == "clocks"],
                detail="nothing here has a done action of its own, so a run "
                       "after the night must stop finding it")
    finally:
        conn.close()
        _cleanup()
    return s
