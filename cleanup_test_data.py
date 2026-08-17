"""Remove test data left behind in the database by development.

    python cleanup_test_data.py            # show what would go, change nothing
    python cleanup_test_data.py --delete   # actually remove it

Why this exists: the database is gitignored, so test rows created through the
UI never show up in a diff or in `git status`. They accumulate silently and
then appear in the app as real records — the booking list has held two stays
for a guest called "Date Change Test", and the workshop catalogue two entries
called "Session Change Test". Anyone shown the app sees those.

It takes a timestamped backup before touching anything, so a bad call here is
recoverable.

The audit log is deliberately left alone. It is an append-only record of who
did what, and editing it is the wrong habit even when the rows are test noise.
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gudanes_hr.db")

# Each entry: (label, table, WHERE clause). Kept as data so a dry run and a
# real run cannot drift apart.
RULES = [
    ("bookings from date-change testing", "bookings",
     "guest_name LIKE '%Date Change Test%'"),
    ("tasks about those bookings", "tasks",
     "room_note LIKE '%Date Change Test%' OR notes LIKE '%Date Change Test%'"),
    ("workshops created while testing", "workshops",
     "title = 'Session Change Test'"),
    ("test notifications", "notifications",
     "title LIKE 'ZZ%' OR title LIKE '%TEST%' OR title LIKE '%Resttest%' "
     "OR body = 'testing'"),
    ("test guest profiles", "guests",
     "email LIKE '%example.invalid' OR email LIKE '%@x.com' OR name LIKE 'ZZ%'"),
    ("test waitlist entries", "waitlist_entries",
     "name LIKE 'ZZ%' OR name LIKE '%Test%'"),
    ("held email to test addresses", "email_outbox",
     "to_address LIKE '%example.invalid' OR to_address LIKE '%@x.com'"),
]


def backup():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"{DB.rsplit('.', 1)[0]}.backup-{stamp}.db"
    src, dst = sqlite3.connect(DB), sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return path


def main(argv):
    if not os.path.exists(DB):
        print(f"No database at {DB}")
        return 1
    delete = "--delete" in argv

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    total = 0
    print("Test data found:\n" if not delete else "Removing:\n")
    for label, table, where in RULES:
        try:
            rows = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}" WHERE {where}').fetchone()["c"]
        except sqlite3.Error as e:
            print(f"   --   {label}: skipped ({e})")
            continue
        if not rows:
            continue
        total += rows
        print(f"   {rows:>4}  {label}")
        if not delete:
            for r in conn.execute(
                    f'SELECT * FROM "{table}" WHERE {where} LIMIT 2'):
                keys = [k for k in r.keys() if k in
                        ("guest_name", "title", "name", "to_address", "subject")]
                if keys:
                    print(f"          e.g. {r[keys[0]]}")

    if not total:
        print("   nothing — the database is clean.")
        conn.close()
        return 0

    if not delete:
        conn.close()
        print(f"\n{total} rows would be removed. Nothing has changed.")
        print("Run again with --delete to remove them (a backup is taken first).")
        return 0

    path = backup()
    print(f"\nBacked up to {os.path.basename(path)}")
    removed = 0
    for _label, table, where in RULES:
        try:
            removed += conn.execute(f'DELETE FROM "{table}" WHERE {where}').rowcount
        except sqlite3.Error:
            pass
    conn.commit()
    conn.close()
    print(f"Removed {removed} rows.")
    print("The audit log was left as it is — it records who did what, "
          "and should not be edited.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
