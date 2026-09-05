"""The code that runs at three in the morning, on a box nobody is watching.

Line coverage across the whole suite says how little of it has ever run:

    claim_job_run                    1 of 17 lines
    automation_loop                 28 of 46
    run_email_inbox_scan_job         1 of 106
    run_backup_email_job             1 of 24
    run_campaign_triggers_job        1 of 21
    run_stale_shift_cleanup_job      2 of 26
    run_health_notes_purge_job       1 of 14
    run_social_schedule_job          2 of 9
    run_daily_digest_job             1 of 7

The page coverage the suite already reports is the right measure for routes
and says nothing about code no route reaches — which is exactly what a
nightly job is. So these are the least-seen lines in the file, and they touch
money, mail, backups and guest data.

WHAT THIS SUITE IS ACTUALLY GUARDING.

  THE COOLDOWN IS A LOCK, NOT A CHECK. `claim_job_run` uses UPDATE...WHERE as
  the lock rather than a SELECT then an UPDATE, so two workers ticking in the
  same second cannot both claim one run. Get that wrong and either every job
  runs on every tick — five minutes apart, so the balance-reminder emails go
  out 288 times a day — or none ever runs again.

  A FAILING JOB MUST NOT TAKE THE OTHERS WITH IT. Each gets its own
  connection and its own commit for that reason. If one exception could end
  the tick, the jobs after it in the list stop running and nothing says so.

  AND THE LOOP MUST SURVIVE ITS OWN JOBS. `automation_loop` catches
  everything, because a thread that dies takes the whole nightly system with
  it silently — the site keeps serving pages and nothing runs after 3am ever
  again.

  A SWITCH THAT IS OFF MUST MEAN OFF. Ten jobs once ran with no switch at
  all, including two that message guests and one that deletes medical notes.
"""
import sqlite3
from datetime import timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "nightly-"


def _cleanup(conn):
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("The machinery that runs at three in the morning")
    _oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    # ------------------------------------------------- the cooldown lock
    s.section("A job claims its run, and cannot claim it twice")
    first = m.claim_job_run(conn, TAG + "once", 3600)
    second = m.claim_job_run(conn, TAG + "once", 3600)
    s.check("the first call may run it", first is True)
    s.check("and the second may not", second is False,
            detail="a cooldown that lets every tick through sends the "
                   "balance reminders 288 times a day, five minutes apart")

    s.check("a job that has never run may always run",
            m.claim_job_run(conn, TAG + "brand-new", 3600) is True,
            detail="the first-ever run has no row to update, so it is the "
                   "INSERT that has to win")

    s.section("And may run again once the cooldown has passed")
    conn.execute(
        "UPDATE automation_runs SET last_ran_at = ? WHERE job_name = ?",
        ((m.datetime.now(m.timezone.utc) - timedelta(hours=2)).isoformat(),
         TAG + "once"))
    conn.commit()
    s.check("two hours later it runs again",
            m.claim_job_run(conn, TAG + "once", 3600) is True)
    s.check("and is locked again straight away",
            m.claim_job_run(conn, TAG + "once", 3600) is False)

    s.section("A zero cooldown still lets it through")
    conn.execute(
        "UPDATE automation_runs SET last_ran_at = ? WHERE job_name = ?",
        ((m.datetime.now(m.timezone.utc) - timedelta(seconds=1)).isoformat(),
         TAG + "once"))
    conn.commit()
    s.check("because the admin run-now button asks for exactly that",
            m.claim_job_run(conn, TAG + "once", 0) is True,
            detail="a second ago is more than nought seconds ago, and the "
                   "page's 'run it now' depends on that being true")

    s.section("It is the UPDATE that locks, not a look-then-write")
    import inspect
    src = inspect.getsource(m.claim_job_run)
    s.check("there is no SELECT before the claim",
            "SELECT" not in src.upper().split("UPDATE")[0],
            detail="a SELECT then an UPDATE is two workers both seeing an "
                   "old timestamp and both running the job")
    s.check("and a losing INSERT is caught rather than raised",
            "IntegrityError" in src,
            detail="two first-ever runs racing: one INSERT wins, the other "
                   "must return False rather than take the tick down")

    # ------------------------------------------------- one job, one failure
    s.section("A job that throws does not take the others with it")
    ran = []

    def good_one(conn_):
        ran.append("good")
        return "did something"

    def bad_one(conn_):
        ran.append("bad")
        raise RuntimeError("deliberate")

    def after_the_bad_one(conn_):
        ran.append("after")
        return "still ran"

    original = m.AUTOMATION_JOBS
    was_settings = m.get_automation_settings
    try:
        m.AUTOMATION_JOBS = [
            (TAG + "good", TAG + "good_enabled", None, 0, good_one),
            (TAG + "bad", TAG + "bad_enabled", None, 0, bad_one),
            (TAG + "after", TAG + "after_enabled", None, 0, after_the_bad_one),
        ]
        base = was_settings(db())

        def all_on(_conn):
            out = dict(base)
            out.update({TAG + "good_enabled": "1", TAG + "bad_enabled": "1",
                        TAG + "after_enabled": "1"})
            return out

        m.get_automation_settings = all_on
        m.automation_tick()
    finally:
        m.AUTOMATION_JOBS = original
        m.get_automation_settings = was_settings

    s.check("the job before the failure ran", "good" in ran, detail=str(ran))
    s.check("the failing one was reached", "bad" in ran, detail=str(ran))
    s.check("and the one after it ran too", "after" in ran,
            detail=f"{ran} — one exception ending the tick means every job "
                   "after it in the list silently stops, and the list is "
                   "ordered by nothing in particular")

    s.section("And the failure is recorded, not only printed")
    row = conn.execute(
        "SELECT last_status, last_message, consecutive_failures "
        "FROM automation_runs WHERE job_name = ?", (TAG + "bad",)).fetchone()
    s.check("the failed job has a row", row is not None)
    s.check("marked failed", row and row["last_status"] == "failed",
            detail=str(dict(row) if row else None))
    s.check("with what went wrong on it",
            row and "deliberate" in (row["last_message"] or ""),
            detail="a Railway log line scrolls away; this is the same fact "
                   "somewhere the owner can still read it next Tuesday")
    s.check("and a failure streak of one",
            row and row["consecutive_failures"] == 1,
            detail="the streak is what separates a hiccup from nothing "
                   "having left the building since the ninth")
    ok_row = conn.execute(
        "SELECT last_status FROM automation_runs WHERE job_name = ?",
        (TAG + "good",)).fetchone()
    s.check("while the one that worked is marked ok",
            ok_row and ok_row["last_status"] == "ok",
            detail=str(dict(ok_row) if ok_row else None))

    s.section("A streak clears the moment it works again")
    m.record_job_run(conn, TAG + "bad", False, "again")
    twice = conn.execute(
        "SELECT consecutive_failures FROM automation_runs WHERE job_name = ?",
        (TAG + "bad",)).fetchone()["consecutive_failures"]
    m.record_job_run(conn, TAG + "bad", True, "recovered")
    after = conn.execute(
        "SELECT consecutive_failures, last_ok_at FROM automation_runs "
        "WHERE job_name = ?", (TAG + "bad",)).fetchone()
    s.check("two failures running is a streak of two", twice == 2,
            detail=str(twice))
    s.check("and one success clears it", after["consecutive_failures"] == 0,
            detail=str(dict(after)))
    s.check("stamping when it last worked", bool(after["last_ok_at"]),
            detail="'nothing has worked since the ninth' is a different "
                   "sentence from 'it failed this morning'")

    # --------------------------------------------------- switches mean off
    s.section("A switch that is off means off")
    ran.clear()
    try:
        m.AUTOMATION_JOBS = [
            (TAG + "gated", TAG + "gated_enabled", None, 0, good_one),
        ]
        base = was_settings(db())

        def gated_off(_conn):
            out = dict(base)
            out[TAG + "gated_enabled"] = "0"
            return out

        m.get_automation_settings = gated_off
        m.automation_tick()
    finally:
        m.AUTOMATION_JOBS = original
        m.get_automation_settings = was_settings
    s.check("a job whose switch is off does not run", ran == [],
            detail=f"{ran} — ten jobs once ran with no switch at all, "
                   "including two that message guests and one that deletes "
                   "medical notes")

    s.section("And every job has a switch to turn off")
    without = [name for name, enabled, _i, _c, _fn in m.AUTOMATION_JOBS
               if not enabled]
    s.check("no job runs unstoppably", not without, detail=str(without))
    settings = m.get_automation_settings(conn)
    missing = [enabled for _n, enabled, _i, _c, _fn in m.AUTOMATION_JOBS
               if enabled not in settings]
    s.check("and every switch has a value behind it", not missing,
            detail=f"{missing} — a switch the settings do not know about "
                   "reads as off, which is a job silently not running")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
