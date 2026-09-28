"""Jobs that claim a time of day, and whether they keep one.

Every periodic job is gated by claim_job_run, which asks only whether N
seconds have passed. That makes a job daily. It does not make it MORNING --
the clock it keeps is the clock of whenever the process last started, and
Railway restarts on every deploy, so the hour wanders all year while the
comment above the job goes on saying "Daily, early".

Nothing about that is visible. The digest is still useful in the afternoon and
the run log says it ran. It only becomes a fault when the message is true at
one time of day and not another, which is exactly what the arrival note became
the day it moved to the morning of.

THE UPPER BOUND IS THE HALF WORTH TESTING. A start hour alone is the obvious
implementation and the broken one: a process down all morning fires the
arrival note the moment it comes back, so a guest arriving at four gets
"we look forward to seeing you today" at half past midnight, about a day that
is over. Past the window the right answer is to skip the day.
"""
from datetime import timedelta

from _harness import Suite, db

import _harness

m = _harness.m
JOB = "zz_window_probe"


def _clear(conn):
    conn.execute("DELETE FROM automation_runs WHERE job_name = ?", (JOB,))
    conn.commit()


def _ran_at(conn, stamp):
    conn.execute("DELETE FROM automation_runs WHERE job_name = ?", (JOB,))
    conn.execute(
        "INSERT INTO automation_runs (job_name, last_ran_at) VALUES (?, ?)",
        (JOB, stamp))
    conn.commit()


def run():
    s = Suite("jobs that keep an hour")
    conn = db()
    _clear(conn)

    # Windows are chosen relative to the hour it is NOW, so this suite does
    # not need the clock moved and cannot pass only between two and three in
    # the morning.
    now = m.datetime.now(m.LOCAL_TZ)
    h = now.hour

    s.section("The window is on the house's clock")
    s.check("it is open inside the window",
            m.job_window_open(conn, JOB, (h, h + 1)) is True,
            detail=f"house hour is {h}")
    s.check("shut before it",
            m.job_window_open(conn, JOB, (h + 1, h + 2)) is False,
            detail="a morning job must not go at four in the morning")
    # The half a start-hour-only implementation gets wrong, and the reason the
    # arrival note can say "today" at all.
    s.check("and shut after it",
            m.job_window_open(conn, JOB, (0, h)) is False,
            detail="a process down all morning must skip the day, not fire "
                   "the morning note at midnight")

    s.section("Once a day, in the house's day")
    _ran_at(conn, m.datetime.now(m.timezone.utc).isoformat())
    s.check("a job that has run today does not run again",
            m.job_window_open(conn, JOB, (h, h + 1)) is False,
            detail="the window reopening every tick would send five")
    _ran_at(conn, (m.datetime.now(m.timezone.utc) - timedelta(days=2)).isoformat())
    s.check("one that ran two days ago does",
            m.job_window_open(conn, JOB, (h, h + 1)) is True)

    # THE UTC TRAP, which is the one this repo keeps paying for. A run at
    # 00:30 house time is stamped with YESTERDAY's UTC date, so reading the
    # stamp with [:10] says it has not run today and the job goes twice.
    half_past_midnight = m.datetime.combine(
        m.house_today(), m.datetime.min.time(),
        tzinfo=m.LOCAL_TZ) + timedelta(minutes=30)
    _ran_at(conn, half_past_midnight.astimezone(m.timezone.utc).isoformat())
    s.check("a run at half past midnight counts as today",
            m.job_window_open(conn, JOB, (h, h + 1)) is False,
            detail="its UTC date is yesterday's, so anything slicing the "
                   "stamp would let it run a second time")
    _clear(conn)

    s.section("The jobs that actually claim an hour")
    # Named rather than counted: the value is that these three are anchored,
    # and a fourth being added is a decision somebody should make on purpose.
    for job, why in (("morning_digest", "its comment has always said 'Daily, early'"),
                     ("checkin_text", "it says 'today' to somebody arriving today"),
                     ("checkout_text", "it is the evening before, not the morning after")):
        s.check("%s is anchored" % job, job in m.JOB_HOURS, detail=why)
    for job, window in m.JOB_HOURS.items():
        s.check("%s has a sane window" % job,
                isinstance(window, tuple) and len(window) == 2
                and 0 <= window[0] < window[1] <= 24,
                detail=repr(window))

    s.section("And the tick actually consults them")
    # The gap a negative control found: every check above proves the gate
    # answers correctly, and not one of them proves anything ASKS it. A
    # perfect gate nobody calls is the same shape as the cancelled-stay form
    # that was hidden by the template while the route still did the work.
    import inspect
    tick = inspect.getsource(m.automation_tick)
    code = chr(10).join(l.split("#")[0] for l in tick.splitlines())
    s.check("the tick reads JOB_HOURS", "JOB_HOURS" in code,
            detail="a window nothing consults is a comment")
    s.check("and skips a job whose window is shut",
            "job_window_open" in code and "continue" in code,
            detail="reading the window and running anyway would be worse "
                   "than not having one")
    # And the cooldown has to drop with it, or an anchored job creeps out of
    # its own window: a run at 07:02 is not eligible at 07:00 tomorrow.
    s.check("and drops the cooldown for anchored jobs",
            "cooldown = 3600" in code,
            detail="a 24-hour cooldown under a 7am window walks the job later "
                   "every day until it falls out of the window entirely")

    s.section("And the arrival note is sent on the day")
    s.check("the arrival text counts zero days back",
            m.GUEST_TEXTS["checkin"]["days_before"] == 0,
            detail="the morning of, which is why the window above has to hold")
    s.check("and its wording says today rather than tomorrow",
            "today" in m.GUEST_TEXTS["checkin"]["default"]
            and "tomorrow" not in m.GUEST_TEXTS["checkin"]["default"],
            detail="a note saying 'tomorrow' sent on the day is worse than "
                   "either version on its own")
    s.check("while the departure note still counts one day back",
            m.GUEST_TEXTS["checkout"]["days_before"] == 1,
            detail="'checkout is at eleven' read at ten past ten is too late "
                   "to act on")

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
