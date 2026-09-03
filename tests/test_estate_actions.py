"""Five estate actions the suite had only ever been refused by.

Every one was reached with an id that does not exist, or as somebody not
allowed, and the 404 or 403 was read as coverage. What none of them ran was
the action, and three of them make a promise while doing it:

  "Off the round. When it was last done is kept."  -- the flash on taking a
  cleaning round off the list. Deactivating by writing over last_done_on
  would satisfy the page and lose the only record of when that room was
  last cleaned.

  "If they know when it was last done but not when it is next owed, work it
  out rather than making them do the arithmetic."  -- a comment above the
  maintenance field parser, and a thing the owner relies on when adding a
  boiler service with a date in one box and nothing in the other.

  And the certificate route is keyed on the VISIT rather than the filename,
  its own docstring says, because "a route that takes a filename is a route
  somebody can walk out of the uploads directory with". That is a security
  property, and a security property nothing exercises is a comment.

The mileage decision is here for a different reason: it takes a state off a
form and writes it into the row, and the guard that keeps that list to three
words is the only thing between a dropdown and an arbitrary status.
"""
import os

from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZEST"


def _cleanup(conn):
    conn.execute("DELETE FROM cleaning_rounds WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mileage_claims WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM maintenance_visits WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM maintenance_schedules WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM drink_packages WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("estate actions that had only ever been refused")
    oc, _ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today()

    def rowid():
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # ------------------------------------------------------------- cleaning
    s.section("Off the round, and when it was last done is kept")
    last_done = (today - timedelta(days=9)).isoformat()
    conn.execute(
        """INSERT INTO cleaning_rounds (what, area, every_days, last_done_on,
                   active, created_at)
           VALUES (?, 'east wing', 7, ?, 1, ?)""",
        (TAG + " windows", last_done, now))
    round_id = rowid()
    conn.commit()
    oc.post(f"/management/cleaning/{round_id}/stop", follow_redirects=True)
    row = conn.execute("SELECT active, last_done_on FROM cleaning_rounds "
                       "WHERE id = ?", (round_id,)).fetchone()
    s.check("it is off the round", row and row["active"] == 0)
    s.check("and when it was last done is still there",
            row and row["last_done_on"] == last_done,
            detail="the page promises this in the words it flashes; clearing "
                   "the date would satisfy the page and lose the only record "
                   "of when that room was last cleaned")

    # -------------------------------------------------------------- mileage
    s.section("A mileage claim decided, and only in words the app knows")
    conn.execute(
        """INSERT INTO mileage_claims (user_id, travelled_on, from_place,
                   to_place, reason, kilometres, rate, amount, status,
                   created_at)
           VALUES (?, ?, 'Chateau', 'Foix', ?, 40, 0.5, 20, 'pending', ?)""",
        (emp["id"], today.isoformat(), TAG + " parts run", now))
    claim = rowid()
    conn.commit()

    r = oc.post(f"/mileage/{claim}/decide",
                data={"state": "nonsense", "owner_note": "x"},
                follow_redirects=True)
    said = " ".join(flashes(r))
    after = conn.execute("SELECT status, decided_at FROM mileage_claims "
                         "WHERE id = ?", (claim,)).fetchone()
    s.check("a state the app does not know is refused",
            "Approve it, decline it or mark it paid" in said,
            detail=said or "nothing was said")
    s.check("and nothing is written", after["status"] == "pending"
            and not after["decided_at"],
            detail="the three-word list is the whole of what keeps a form "
                   "field out of the status column")

    r = oc.post(f"/mileage/{claim}/decide",
                data={"state": "approved", "owner_note": TAG + " ok"},
                follow_redirects=True)
    after = conn.execute("SELECT * FROM mileage_claims WHERE id = ?",
                         (claim,)).fetchone()
    s.check("approving it is recorded", after["status"] == "approved")
    s.check("with who decided and when",
            bool(after["decided_at"]) and after["decided_by_user_id"],
            detail="a decision on somebody's money with no name on it is one "
                   "nobody can be asked about")
    s.check("and the note is kept", (after["owner_note"] or "").endswith(" ok"))

    # ---------------------------------------------------------- maintenance
    s.section("A maintenance schedule saved, with the arithmetic done for them")
    conn.execute(
        """INSERT INTO maintenance_schedules (name, category, every_months,
                   lead_days, active, created_at)
           VALUES (?, 'building', 12, 21, 1, ?)""", (TAG + " boiler", now))
    sched = rowid()
    conn.commit()
    served = (today - timedelta(days=5)).isoformat()
    oc.post(f"/management/maintenance/{sched}/edit",
            data={"name": TAG + " boiler service", "category": "building",
                  "every_months": "6", "lead_days": "14",
                  "last_done_on": served},
            follow_redirects=True)
    row = conn.execute("SELECT * FROM maintenance_schedules WHERE id = ?",
                       (sched,)).fetchone()
    s.check("the change is saved", row["name"] == TAG + " boiler service")
    s.check("and every six months now", row["every_months"] == 6)
    s.check("with the next one worked out for them",
            bool(row["next_due_on"]) and row["next_due_on"] > served,
            detail=f"last done {served}, next due {row['next_due_on']} — "
                   "the owner typed one date, not two")

    s.section("A certificate is served by its visit, and cannot climb out")
    cert = TAG + "-cert.txt"
    with open(os.path.join(m.UPLOAD_DIR, cert), "w", encoding="utf-8") as fh:
        fh.write("gas safe certificate")
    conn.execute(
        """INSERT INTO maintenance_visits (schedule_id, done_on, cost,
                   certificate_filename, notes, created_at)
           VALUES (?, ?, 120, ?, ?, ?)""",
        (sched, served, cert, TAG + " visit", now))
    visit = rowid()
    # A row whose filename tries to leave the folder. The route's docstring
    # says it is keyed on the visit so that cannot happen; this is that claim,
    # checked rather than believed.
    conn.execute(
        """INSERT INTO maintenance_visits (schedule_id, done_on, cost,
                   certificate_filename, notes, created_at)
           VALUES (?, ?, 0, ?, ?, ?)""",
        (sched, served, "../../app.py", TAG + " climber", now))
    climber = rowid()
    conn.commit()

    r = oc.get(f"/management/maintenance/certificate/{visit}")
    s.check("the real certificate is served", r.status_code == 200,
            detail=f"status {r.status_code}")
    s.check("and it is the file, not a page about it",
            b"gas safe" in r.get_data(),
            detail=r.get_data()[:60])

    r = oc.get(f"/management/maintenance/certificate/{climber}")
    s.check("a filename that climbs out is refused",
            r.status_code in (400, 403, 404),
            detail=f"status {r.status_code} — the route is keyed on the visit "
                   "precisely so this cannot serve app.py")
    s.check("and nothing of the file comes back",
            b"Flask" not in r.get_data() and b"app.route" not in r.get_data())

    r = oc.get(f"/management/maintenance/certificate/{visit + 100000}")
    s.check("a visit that does not exist is refused", r.status_code == 404,
            detail=f"status {r.status_code}")

    # ------------------------------------------------------------- the bar
    s.section("A drink package taken off the list and put back")
    conn.execute(
        "INSERT INTO drink_packages (name, kind, price, active, created_at) "
        "VALUES (?, 'fixed', 35, 1, ?)", (TAG + " pairing", now))
    pkg = rowid()
    conn.commit()
    oc.post(f"/admin/restaurant/packages/{pkg}/toggle", follow_redirects=True)
    s.check("it comes off",
            conn.execute("SELECT active FROM drink_packages WHERE id = ?",
                         (pkg,)).fetchone()["active"] == 0)
    oc.post(f"/admin/restaurant/packages/{pkg}/toggle", follow_redirects=True)
    s.check("and goes back on",
            conn.execute("SELECT active FROM drink_packages WHERE id = ?",
                         (pkg,)).fetchone()["active"] == 1,
            detail="a toggle that only goes one way is a delete with a "
                   "friendlier label")

    try:
        os.remove(os.path.join(m.UPLOAD_DIR, cert))
    except OSError:
        pass
    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
