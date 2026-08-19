"""Fixing a broken shift, and what payroll does while it is broken.

This is the loop that decides what people get paid: an employee spots that
their hours are wrong and flags it, the owner sees the flag and repairs the
entry. Neither end was tested.

The specific fault it guards is an entry whose clock-out is before its
clock-in. A bare SUM over such a row produces negative hours, which quietly
subtracts from somebody's pay rather than erroring — so the interesting checks
here are not "does the form work" but "what does the total say while the entry
is impossible, and after it is put right".

Voiding sets clock_out to equal clock_in rather than deleting the row. A
zero-hour shift keeps the fact that somebody clocked in, and the audit trail
of the repair, while contributing nothing. Payroll history is never silently
destroyed, and that is worth pinning too.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZTS"
# time_entries has no notes column to tag, so the test's own rows are marked by
# living on a date nothing real could: far enough out that a copied dev or live
# database cannot contain a genuine shift there.
TEST_DAY = "2031-02-17"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM timesheet_corrections WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM breaks WHERE time_entry_id IN "
                 "(SELECT id FROM time_entries WHERE clock_in_at LIKE ?)", (TEST_DAY + "%",))
    conn.execute("DELETE FROM time_entries WHERE clock_in_at LIKE ?", (TEST_DAY + "%",))
    conn.commit()
    conn.close()


def _entry(user_id, start, end):
    """A shift, possibly an impossible one."""
    conn = db()
    cur = conn.execute(
        """INSERT INTO time_entries (user_id, clock_in_at, clock_out_at)
           VALUES (?, ?, ?)""",
        (user_id, start.isoformat(), end.isoformat() if end else None))
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    return eid


def _local_input(dt_utc):
    """A UTC datetime as the form's datetime-local field would carry it.

    The route parses this as naive local time, so handing it a UTC string
    would set a clock-out an hour or two off and make the assertions lie.
    """
    return dt_utc.astimezone(m.LOCAL_TZ).strftime("%Y-%m-%dT%H:%M")


def run():
    s = Suite("Timesheet repair")
    oc, ec, owner, emp = clients()
    if not emp:
        s.check("an employee exists to own the shift", False, detail="none in the database")
        return s
    _cleanup()

    base = datetime(2031, 2, 17, 9, 0, tzinfo=timezone.utc)

    s.section("A shift that ends before it starts")
    broken = _entry(emp["id"], base, base - timedelta(hours=2))
    conn = db()
    row = conn.execute("SELECT * FROM time_entries WHERE id = ?", (broken,)).fetchone()
    hours = m.net_hours(row, conn)
    conn.close()
    s.check("the entry is stored as written, not silently corrected",
            row["clock_out_at"] < row["clock_in_at"])
    # The whole point: a bare SUM would go negative and quietly reduce somebody's
    # pay. net_hours has to refuse to return a negative number.
    s.check("its hours are never negative", (hours or 0) >= 0, detail=f"got {hours}")

    s.section("Payroll notices rather than paying it")
    conn = db()
    flagged = conn.execute(
        """SELECT COUNT(*) AS c FROM time_entries
           WHERE clock_out_at IS NOT NULL AND clock_out_at < clock_in_at""").fetchone()["c"]
    checks = m.readiness_checks(conn)
    conn.close()
    s.check("the impossible shift is counted", flagged >= 1, detail=f"got {flagged}")
    sane = [c for c in checks if c["label"] == "Timesheets sane"]
    s.check("and readiness says so rather than staying green",
            sane and not sane[0]["ok"],
            detail=f"got {sane[0]['detail'] if sane else 'no such check'}")

    s.section("The employee can say something is wrong")
    r = ec.post(f"/timesheets/{broken}/flag", data={"note": f"{TAG} this can't be right"},
                follow_redirects=True)
    conn = db()
    corrections = conn.execute(
        "SELECT * FROM timesheet_corrections WHERE note LIKE ?", (TAG + "%",)).fetchall()
    conn.close()
    s.check("the flag is recorded", len(corrections) == 1, detail=f"got {len(corrections)}")
    s.check("as pending, so it lands in somebody's queue",
            corrections and corrections[0]["status"] == "pending")
    # An empty note would make a correction nobody can act on.
    ec.post(f"/timesheets/{broken}/flag", data={"note": "   "}, follow_redirects=True)
    conn = db()
    after = conn.execute("SELECT COUNT(*) AS c FROM timesheet_corrections "
                         "WHERE time_entry_id = ?", (broken,)).fetchone()["c"]
    conn.close()
    s.check("an empty note is refused", after == 1, detail=f"got {after}")

    s.section("Only your own hours")
    # Flagging somebody else's entry would let one employee open corrections
    # against another's pay.
    other = _entry(owner["id"], base, base + timedelta(hours=3))
    r = ec.post(f"/timesheets/{other}/flag", data={"note": f"{TAG} not mine"})
    conn = db()
    leaked = conn.execute("SELECT COUNT(*) AS c FROM timesheet_corrections "
                          "WHERE time_entry_id = ?", (other,)).fetchone()["c"]
    conn.close()
    s.check("flagging another person's shift is refused",
            r.status_code == 404 and leaked == 0,
            detail=f"HTTP {r.status_code}, {leaked} corrections")

    s.section("The owner repairs it")
    good_out = base + timedelta(hours=7)
    r = oc.post(f"/admin/timesheets/{broken}/repair", data={
        "action": "set",
        "clock_out_at": _local_input(good_out),
    }, follow_redirects=True)
    conn = db()
    fixed = conn.execute("SELECT * FROM time_entries WHERE id = ?", (broken,)).fetchone()
    hours = m.net_hours(fixed, conn)
    conn.close()
    s.check("the clock-out is now after the clock-in",
            fixed["clock_out_at"] > fixed["clock_in_at"],
            detail=f"{fixed['clock_in_at']} → {fixed['clock_out_at']}")
    s.check("and it is worth real hours", (hours or 0) > 0, detail=f"got {hours}")

    s.section("A repair that would recreate the fault is refused")
    r = oc.post(f"/admin/timesheets/{broken}/repair", data={
        "action": "set",
        "clock_out_at": _local_input(base - timedelta(hours=1)),
    }, follow_redirects=True)
    conn = db()
    still = conn.execute("SELECT clock_out_at FROM time_entries WHERE id = ?",
                         (broken,)).fetchone()["clock_out_at"]
    conn.close()
    s.check("setting the clock-out before the clock-in is rejected",
            still > fixed["clock_in_at"], detail=f"got {still}")
    r = oc.post(f"/admin/timesheets/{broken}/repair", data={
        "action": "set", "clock_out_at": "not a date"}, follow_redirects=True)
    conn = db()
    unchanged = conn.execute("SELECT clock_out_at FROM time_entries WHERE id = ?",
                             (broken,)).fetchone()["clock_out_at"]
    conn.close()
    s.check("and so is a date that isn't one", unchanged == still, detail=f"got {unchanged}")

    s.section("Voiding keeps the row and zeroes the hours")
    # Deleting would destroy the evidence that somebody clocked in at all.
    void_me = _entry(emp["id"], base, base - timedelta(hours=4))
    oc.post(f"/admin/timesheets/{void_me}/repair", data={"action": "void"},
            follow_redirects=True)
    conn = db()
    voided = conn.execute("SELECT * FROM time_entries WHERE id = ?", (void_me,)).fetchone()
    hours = m.net_hours(voided, conn) if voided else None
    conn.close()
    s.check("the row still exists", voided is not None)
    s.check("clocked in and out at the same moment", voided
            and voided["clock_out_at"] == voided["clock_in_at"])
    s.check("so it contributes nothing to any total", (hours or 0) == 0, detail=f"got {hours}")

    s.section("Once repaired, payroll is clean again")
    conn = db()
    remaining = conn.execute(
        """SELECT COUNT(*) AS c FROM time_entries
           WHERE clock_out_at IS NOT NULL AND clock_out_at < clock_in_at""").fetchone()["c"]
    conn.close()
    s.check("no impossible shift is left behind", remaining == 0, detail=f"got {remaining}")

    s.section("Only the owner can repair")
    another = _entry(emp["id"], base, base - timedelta(hours=1))
    r = ec.post(f"/admin/timesheets/{another}/repair", data={"action": "void"})
    s.check("an employee cannot repair their own hours",
            r.status_code in (302, 403, 404), detail=f"HTTP {r.status_code}")
    conn = db()
    still_broken = conn.execute(
        "SELECT clock_out_at < clock_in_at AS bad FROM time_entries WHERE id = ?",
        (another,)).fetchone()["bad"]
    conn.close()
    s.check("and the entry is left as it was", bool(still_broken))

    _cleanup()
    return s
