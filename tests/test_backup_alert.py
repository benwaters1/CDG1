"""The backup that stops arriving has to say so.

run_backup_email_job records an audit line only on success. When the mail
account is missing it returns a string into the automation log and stops:
no exception, no flag, nothing red on any page. That is the failure this
guards — not a backup that errors, but one that quietly never happens.

The dashboard's own backup_stale flag is 30 days, which is a month of
silence on a job that runs nightly. These checks are about readiness, where
it should show up within two cycles.
"""
from datetime import datetime, timedelta, timezone

import _harness
from _harness import Suite

import app as m


def _readiness(conn):
    """The backup row out of the readiness checklist, or None."""
    for row in m.readiness_checks(conn):
        if row["label"] == "Backups arriving":
            return row
    return None


def _set_backup(conn, hours_ago=None, enabled=True, interval="24"):
    conn.execute("DELETE FROM audit_log WHERE action IN ('backup_downloaded', 'backup_auto_sent')")
    for key, value in (("automation_backup_email_enabled", "1" if enabled else "0"),
                       ("automation_backup_interval_hours", interval)):
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    if hours_ago is not None:
        stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        conn.execute("INSERT INTO audit_log (actor_user_id, action, target, created_at) "
                     "VALUES (NULL, 'backup_auto_sent', 'test', ?)", (stamp,))
    conn.commit()


def _set_last_run(conn, status=None, message="", fails=0):
    """What the scheduled job recorded about its own last attempt."""
    conn.execute("DELETE FROM automation_runs WHERE job_name = 'backup_email'")
    if status is not None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO automation_runs (job_name, last_ran_at, last_status,
                 last_message, consecutive_failures)
               VALUES ('backup_email', ?, ?, ?, ?)""", (now, status, message, fails))
    conn.commit()


def run():
    s = Suite("backup_alert")
    conn = m.get_db()

    # The check has to exist at all. A readiness page that never mentions
    # backups reads as "nothing wrong with them".
    _set_backup(conn, hours_ago=2)
    row = _readiness(conn)
    s.check("readiness has a backup check", row is not None)
    if row is None:
        return s

    s.check("a backup 2h ago is fine", row["ok"] is True)
    s.check("the healthy line says when", "2h ago" in row["detail"] or "0h ago" in row["detail"])

    # One missed night is not an alarm — the job runs on a cooldown, a
    # restart or a slow send can push it past 24h honestly.
    _set_backup(conn, hours_ago=30)
    s.check("one missed cycle is tolerated", _readiness(conn)["ok"] is True)

    # Two is. This is the real case: nothing errored, nothing was logged,
    # the volume is now the only copy of the business.
    _set_backup(conn, hours_ago=80)
    stale = _readiness(conn)
    s.check("two missed cycles is flagged", stale["ok"] is False)

    # It used to say the job was failing silently, which was true when that was
    # written: the job recorded only successes. It records every outcome now,
    # so the page can say WHY instead of guessing, and the three causes are
    # three different jobs for the owner.
    _set_last_run(conn, "failed", "send failed: SMTP auth rejected", 3)
    failed = _readiness(conn)
    s.check("a recorded failure is quoted, not guessed at",
            "SMTP auth rejected" in failed["detail"], detail=failed["detail"][:150])
    s.check("with how long it has been failing",
            "3 run(s) in a row" in failed["detail"], detail=failed["detail"][:150])

    # The worst of the three, and the one a human would never guess: the job
    # believes it worked, so a copy is being made and is not arriving.
    _set_last_run(conn, "ok", "sent to owner@example.invalid (412KB)")
    lying = _readiness(conn)
    s.check("a job that reports success but no backup arrived says so",
            "reports success" in lying["detail"], detail=lying["detail"][:150])
    s.check("and points at the address rather than the job",
            "address" in lying["detail"], detail=lying["detail"][:150])

    _set_last_run(conn, None)
    never_ran = _readiness(conn)
    s.check("a job that has never run says that instead",
            "never run" in never_ran["detail"], detail=never_ran["detail"][:150])
    s.check("it still points at where to look", "Automation" in never_ran["detail"],
            detail=never_ran["detail"][:150])

    # Never backed up at all is worse, not better, than an old one — the
    # empty case must not read as healthy just because nothing is stale.
    _set_backup(conn, hours_ago=None)
    never = _readiness(conn)
    s.check("never backed up is flagged", never["ok"] is False)
    s.check("never backed up says so", "ever been taken" in never["detail"])

    # Switched off is a decision, but the page still shouldn't imply cover.
    _set_backup(conn, hours_ago=2, enabled=False)
    off = _readiness(conn)
    s.check("backups switched off is flagged", off["ok"] is False)
    s.check("switched off says nothing is being copied", "switched off" in off["detail"])

    # The window follows the configured interval. Someone who moves the job
    # to weekly should not get a permanent red light for doing so.
    _set_backup(conn, hours_ago=80, interval="168")
    s.check("a weekly schedule tolerates 80h", _readiness(conn)["ok"] is True)
    _set_backup(conn, hours_ago=400, interval="168")
    s.check("a weekly schedule still catches 400h", _readiness(conn)["ok"] is False)

    # Leave the copy as we found it.
    _set_backup(conn, hours_ago=2)
    return s


if __name__ == "__main__":
    print(run().report())
