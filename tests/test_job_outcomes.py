"""A job that fails says so, somewhere the owner can still read it on Tuesday.

`automation_runs` recorded only that a job ran, never how it went, so a backup
that failed to send and one that arrived left the identical row behind. The
failure went to `print()` -- a Railway log line that scrolls away -- and the
Job status table showed "last ran" either way. /admin/readiness diagnosed it in
its own words: "It is failing silently -- the job only records a success."

The other half is the Run now button. It dispatched from a hand-written
if/elif chain sitting next to the job registry, and the two had drifted: six
jobs had a row in the table and a button that could never run them.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ztest-job-"


def _cleanup(conn):
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM automation_runs WHERE job_name IN ('ical_sync', 'backup_email')")
    conn.execute("DELETE FROM tasks WHERE origin = 'watch'")
    conn.commit()


def _row(conn, job_name):
    return conn.execute("SELECT * FROM automation_runs WHERE job_name = ?",
                        (job_name,)).fetchone()


def run():
    s = Suite("job outcomes")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    job = TAG + "probe"

    s.section("A run records how it went, not just that it happened")
    m.record_job_run(conn, job, True, "sent to owner (412KB)")
    r = _row(conn, job)
    s.check("a successful run is marked ok", r is not None and r["last_status"] == "ok",
            detail="no row" if r is None else r["last_status"])
    s.check("it keeps what the job said",
            r is not None and "412KB" in (r["last_message"] or ""),
            detail=None if r is None else r["last_message"])
    s.check("and stamps when it last actually worked", r is not None and bool(r["last_ok_at"]))
    first_ok = r["last_ok_at"] if r else None

    m.record_job_run(conn, job, False, "send failed: SMTP auth rejected")
    r = _row(conn, job)
    s.check("a failed run is marked failed", r is not None and r["last_status"] == "failed",
            detail=None if r is None else r["last_status"])
    s.check("with the reason attached",
            r is not None and "SMTP auth rejected" in (r["last_message"] or ""),
            detail=None if r is None else r["last_message"])
    # The distinction the old single-timestamp row could not hold: "it tried at
    # 03:00" and "it last worked nine days ago" are different facts. Asserted as
    # "failures never advance last_ok_at" rather than by comparing the two
    # stamps -- the Windows clock is coarse enough that two calls this close
    # return the same microsecond, which would make a real invariant flaky.
    m.record_job_run(conn, job, False, "still failing")
    m.record_job_run(conn, job, False, "still failing")
    r = _row(conn, job)
    s.check("no number of failures moves when it last worked",
            r is not None and r["last_ok_at"] == first_ok,
            detail=None if r is None else f"{first_ok} -> {r['last_ok_at']}")
    s.check("and it never claims to have worked after it last ran",
            r is not None and r["last_ok_at"] <= r["last_ran_at"],
            detail=None if r is None else f"ok={r['last_ok_at']} ran={r['last_ran_at']}")

    # The row-creating path, reached only when a job fails on its very first
    # run. Getting this wrong prints "last worked: today" for a job that has
    # never once worked, which is the most confident kind of wrong.
    virgin = TAG + "neverworked"
    m.record_job_run(conn, virgin, False, "failed first time out")
    v = _row(conn, virgin)
    s.check("a job whose first ever run fails has never worked",
            v is not None and v["last_ok_at"] is None,
            detail="no row" if v is None else str(v["last_ok_at"]))
    s.check("and is counted as failing from the first run",
            v is not None and v["consecutive_failures"] == 1,
            detail=None if v is None else str(v["consecutive_failures"]))

    s.section("A streak is what separates a bad morning from nobody coming")
    s.check("failures in a row are counted", r is not None and r["consecutive_failures"] == 3,
            detail=None if r is None else str(r["consecutive_failures"]))
    m.record_job_run(conn, job, False, "send failed again")
    r = _row(conn, job)
    s.check("and keep counting", r is not None and r["consecutive_failures"] == 4,
            detail=None if r is None else str(r["consecutive_failures"]))
    m.record_job_run(conn, job, True, "sent")
    r = _row(conn, job)
    s.check("and a success clears it", r is not None and r["consecutive_failures"] == 0,
            detail=None if r is None else str(r["consecutive_failures"]))

    s.section("A job that keeps failing reaches somebody")
    # Two in a row, on jobs that mostly run daily, is nobody coming.
    m.record_job_run(conn, "ical_sync", False, "403 from the Airbnb feed")
    m.record_job_run(conn, "ical_sync", False, "403 from the Airbnb feed")
    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn)
    titles = [t for _k, t, _n, _d, _p in found]
    s.check("it becomes a blocking finding",
            any("ical_sync" in t for t in titles), detail=str(titles))
    note = next((n for k, _t, n, _d, _p in found if k == "job"), "")
    s.check("the note says what it reports", "403 from the Airbnb feed" in note,
            detail=note[:120])
    s.check("and how long it has been broken", "2 runs in a row" in note, detail=note[:120])

    # It routes like any other blocking finding, so it can land on the person
    # who administers the thing rather than only on the owner's list.
    s.check("'job' is a routable kind", "job" in m.WATCH_TASK_KINDS,
            detail=str(sorted(m.WATCH_TASK_KINDS)))

    m.record_job_run(conn, "ical_sync", True, "synced 3 rooms")
    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn)
    s.check("and it stops being one once the job works again",
            not any(k == "job" and "ical_sync" in t for k, t, _n, _d, _p in found),
            detail=str([t for k, t, _n, _d, _p in found if k == "job"]))

    s.section("The backup task carries the reason, not just the age")
    # One task saying everything known beats the owner reading the age on one
    # page and the reason on another.
    # Twice, so it clears JOB_FAILURE_STREAK. With only one failure the
    # duplicate check below could never fail, which would make it decoration.
    m.record_job_run(conn, "backup_email", False, "SMTP auth rejected")
    m.record_job_run(conn, "backup_email", False, "SMTP auth rejected")
    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn)
    backup_note = next((n for k, _t, n, _d, _p in found if k == "backup"), "")
    s.check("the backup task quotes the failure",
            "SMTP auth rejected" in backup_note, detail=backup_note[:160])
    # ...and does not also raise a second, worse-worded task about the same thing.
    job_titles = [t for k, t, _n, _d, _p in found if k == "job"]
    s.check("and no duplicate job task is raised for it",
            not any("backup_email" in t for t in job_titles), detail=str(job_titles))

    s.section("Every job in the registry can actually be run by hand")
    # The chain and the registry were two lists of the same thing, and only one
    # was kept up to date.
    registry = {n for n, _e, _i, _c, _f in m.AUTOMATION_JOBS} | set(m.PARAMETERISED_JOBS)
    labelled = set(m.AUTOMATION_JOB_LABELS)
    s.check("every job shown in Job status is runnable",
            labelled - registry == set(), detail=str(sorted(labelled - registry)))
    s.check("and every runnable job is shown",
            registry - labelled == set(), detail=str(sorted(registry - labelled)))

    before = _row(conn, "watch_tasks")
    resp = oc.post("/admin/automation/run/watch_tasks", follow_redirects=True)
    s.check("a job the old chain could not reach now runs", resp.status_code == 200,
            detail=f"HTTP {resp.status_code}")
    msgs = " ".join(flashes(resp))
    s.check("and reports what it did rather than a 404",
            "Ran now" in msgs and "404" not in msgs, detail=msgs[:120])
    after = _row(conn, "watch_tasks")
    s.check("the run is recorded with its outcome",
            after is not None and after["last_status"] == "ok"
            and (before is None or after["last_ran_at"] != before["last_ran_at"]),
            detail="no row" if after is None else after["last_status"])

    s.section("An unknown job is still a 404, not a failed job")
    # abort() raises, so an unknown name caught by the failure handler would be
    # recorded as that job having run and failed.
    resp = oc.post("/admin/automation/run/" + TAG + "nosuch", follow_redirects=False)
    s.check("it 404s", resp.status_code == 404, detail=f"HTTP {resp.status_code}")
    s.check("and no run was recorded against it", _row(conn, TAG + "nosuch") is None)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
