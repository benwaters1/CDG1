"""A backup nobody has restored is a file.

test_backup_alert proves a backup ARRIVES — that the nightly job runs and
that its silence gets noticed. Nothing has ever proved one can be opened,
that what is inside it is the database, that nothing was dropped on the way
in, or that the application will run against it. Those are four different
claims and the house is relying on all of them.

The order here is deliberate, because each step is worthless without the one
before it:

  1. The zip opens and there is a database in it.
  2. That database is not corrupt. src.backup() is the right call and takes a
     consistent copy of a live file; a plain file copy would not, and would
     fail here rather than on the morning it was needed.
  3. Every table is present with the same number of rows. "It opened" is not
     "it is all there" — a truncated download opens perfectly.
  4. THE APP SERVES PAGES AGAINST IT. This is the actual drill. A database
     that satisfies 1-3 and then 500s on every page because a migration was
     never applied to it is exactly the situation a restore is for.
  5. Every file the database points at, and which is still on disk, is inside
     the zip. A backup carrying rows that reference documents it did not
     include restores an app full of broken links.

And one thing that is NOT a failure: the nightly email drops media when the
zip is too large to send. That is the right call — a smaller backup that
arrives beats a bigger one a mail provider drops — but it must say so, or
the owner has a database-only copy and does not know it.
"""
import io
import os
import shutil
import sqlite3
import tempfile
import zipfile

from _harness import Suite, clients, db
import _harness

m = _harness.m

# Columns that genuinely name a file stored in the uploads directory. Left off
# on purpose: email_outbox.ics_filename (a suggested attachment name, never
# written to disk), menus.source_file (what it was imported from), and
# pos_orders.receipt_number, which is not a file at all and only matched a
# search for "receipt".
FILE_COLUMNS = [
    ("documents", "filename"),
    ("expenses", "filename"),
    ("company_documents", "filename"),
    ("rooms", "photo_filename"),
    ("workshops", "photo_filename"),
    ("assets", "photo_filename"),
    ("gallery_photos", "filename"),
    ("absences", "doctor_note_filename"),
]


def _tables(conn):
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()}


def _counts(conn, tables):
    out = {}
    for t in sorted(tables):
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        except sqlite3.Error as exc:
            out[t] = f"unreadable: {exc}"
    return out


def _referenced_files(conn):
    """Every filename the database points at, that is also still on disk.

    A row naming a file that has already gone from disk is a different fault
    and not this one's to report: the backup cannot include what is not there,
    and failing on it would make this test go red for a reason no change to
    the backup could fix.
    """
    wanted = set()
    for table, column in FILE_COLUMNS:
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {column} AS f FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} != ''").fetchall()
        except sqlite3.Error:
            continue                       # table not in this database
        for r in rows:
            name = (r["f"] or "").strip()
            if name and os.path.exists(os.path.join(m.UPLOAD_DIR, name)):
                wanted.add(name)
    return wanted


def run():
    s = Suite("Restoring from a backup")
    oc, _ec, _owner, _emp = clients()

    s.section("The zip opens, and there is a database in it")
    # Written immediately before the backup and never checkpointed. In WAL mode
    # a committed transaction lives in the -wal file until SQLite folds it back
    # into the database, so copying the .db on its own silently loses the most
    # recent work — the very thing a backup is for. src.backup() reads through
    # the log and sees it. Nothing else in this file can tell the two apart.
    marker = "ZTRESTORE-" + m.secrets.token_hex(6)
    seeded_name = f"ZTRESTORE_{m.secrets.token_hex(5)}_receipt.txt"
    seeded_path = os.path.join(m.UPLOAD_DIR, seeded_name)
    live2 = db()
    live2.execute("INSERT INTO audit_log (action, target, created_at) VALUES (?, ?, ?)",
                  ("restore_marker", marker,
                   m.datetime.now(m.timezone.utc).isoformat()))
    # A real document, on disk, that a row actually points at. Written here
    # rather than hoped for: asking whether this database happens to reference
    # a file that happens to still be present is a check that proves nothing on
    # the days it finds none, and this database referenced none at all.
    os.makedirs(m.UPLOAD_DIR, exist_ok=True)
    with open(seeded_path, "w", encoding="utf-8") as fh:
        fh.write("a receipt the backup has to carry\n")
    live2.execute(
        """INSERT INTO expenses (kind, description, amount, filename, status, submitted_at)
           VALUES ('staff_expense', ?, 1.0, ?, 'pending', ?)""",
        ("ZTRESTORE drill receipt", seeded_name,
         m.datetime.now(m.timezone.utc).isoformat()))
    live2.commit()

    # The census AFTER everything this suite writes, so the counts are of the
    # same database the backup is about to be taken from.
    live = db()
    live_tables = _tables(live)
    live_counts = _counts(live, live_tables)
    wanted_files = _referenced_files(live)
    live.close()

    # live2 is STILL OPEN, and that is the point. Closing it checkpoints the
    # write-ahead log back into the database file, at which point a plain copy
    # of that file finds the marker too and proves nothing. Held open, the
    # committed row exists only in the -wal: a flat copy sees none of it and
    # src.backup(), which reads through the log, sees all of it.
    blob = m.build_backup_zip(include_media=True)
    live2.close()
    s.check("a backup was produced at all", bool(blob), detail=f"{len(blob)} bytes")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = zf.namelist()
        bad = zf.testzip()
    except zipfile.BadZipFile as exc:
        s.check("the zip opens", False, detail=str(exc))
        return s
    s.check("the zip is not corrupt", bad is None, detail=str(bad))
    has_db = "gudanes_hr.db" in names
    s.check("and the database is inside it", has_db, detail=", ".join(names[:5]))
    if not has_db:
        # Everything below reads that file. Crashing here would hide the rest
        # of the report behind a traceback about a missing zip member.
        return s

    workdir = tempfile.mkdtemp(prefix="zzrestore_")
    restored = os.path.join(workdir, "gudanes_hr.db")
    try:
        with open(restored, "wb") as fh:
            fh.write(zf.read("gudanes_hr.db"))

        s.section("What came out is a working database, not just a file")
        rc = sqlite3.connect(restored)
        rc.row_factory = sqlite3.Row
        integrity = rc.execute("PRAGMA integrity_check").fetchone()[0]
        s.check("SQLite says it is intact", integrity == "ok", detail=str(integrity))
        # src.backup() takes a consistent copy of a file being written to. A
        # plain shutil.copy of a live database would usually pass integrity_check
        # and still be missing the last transaction, so this is checked against
        # the row counts below rather than trusted on its own.
        fk = rc.execute("PRAGMA foreign_key_check").fetchall()
        s.check("and no foreign key is left dangling", not fk,
                detail=f"{len(fk)} broken reference(s): {[tuple(r) for r in fk[:3]]}")

        restored_tables = _tables(rc)
        missing = sorted(live_tables - restored_tables)
        s.check(f"all {len(live_tables)} tables are there", not missing,
                detail="missing: " + ", ".join(missing[:6]) if missing else "")

        s.section("Nothing was dropped on the way in")
        # "It opened" is not "it is all there" — a truncated download opens.
        restored_counts = _counts(rc, live_tables & restored_tables)
        rc.close()
        differing = {t: (live_counts[t], restored_counts[t])
                     for t in restored_counts
                     if live_counts.get(t) != restored_counts[t]}
        s.check("every table has the same number of rows", not differing,
                detail="; ".join(f"{t}: live {a} vs restored {b}"
                                 for t, (a, b) in list(differing.items())[:4]))
        total = sum(v for v in restored_counts.values() if isinstance(v, int))
        s.check("and there is real data in it, not an empty schema", total > 0,
                detail=f"{total} rows across {len(restored_counts)} tables")
        rc2 = sqlite3.connect(restored)
        found = rc2.execute(
            "SELECT COUNT(*) FROM audit_log WHERE target = ?", (marker,)).fetchone()[0]
        rc2.close()
        s.check("including what was written a moment before it was taken",
                found == 1,
                detail="a plain copy of the database file leaves the most recent "
                       "commits behind in the write-ahead log")

        s.section("The application runs against it")
        # The drill itself. A database that satisfies everything above and then
        # 500s on every page is the exact situation a restore exists for.
        was = m.DB_PATH
        try:
            m.DB_PATH = restored
            client = m.app.test_client()
            with client.session_transaction() as sess:
                live2 = sqlite3.connect(restored)
                live2.row_factory = sqlite3.Row
                owner_row = live2.execute(
                    "SELECT id FROM users WHERE role = 'owner' AND status = 'active' "
                    "LIMIT 1").fetchone()
                live2.close()
                if owner_row:
                    sess["user_id"] = owner_row["id"]
            s.check("there is an owner in the restored copy to sign in as",
                    bool(owner_row))

            pages = ["/", "/owner", "/calendar", "/admin/bookings", "/admin/guests",
                     "/book", "/restaurant", "/workshops"]
            broken = []
            for path in pages:
                try:
                    r = client.get(path, follow_redirects=True)
                    if r.status_code >= 500:
                        broken.append(f"{path} -> {r.status_code}")
                except Exception as exc:                       # noqa: BLE001
                    broken.append(f"{path} -> {type(exc).__name__}: {exc}")
            s.check(f"all {len(pages)} pages serve from the restored database",
                    not broken, detail="; ".join(broken[:4]))

            # Reading is half of it. If the schema restored without a migration
            # the write path is where it shows.
            wc = m.get_db()
            try:
                # The app's own writer, not hand-written SQL. Hand-written SQL
                # here tests whether this file knows the schema; log_audit tests
                # whether the application can write to what came back.
                with m.app.test_request_context():
                    m.log_audit(wc, "restore_drill", target="drill")
                wc.commit()
                wrote = wc.execute(
                    "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'restore_drill'"
                ).fetchone()["c"] > 0
                err = "" if wrote else "the insert reported no error and wrote nothing"
            except Exception as exc:                           # noqa: BLE001
                wrote, err = False, f"{type(exc).__name__}: {exc}"
            finally:
                wc.close()
            s.check("and it can be written to, not only read", wrote, detail=err)
        finally:
            m.DB_PATH = was
        s.check("the live database is what the app points at again",
                m.DB_PATH == was, detail=str(m.DB_PATH))

        s.section("The documents came too")
        # A backup carrying rows that point at files it did not include
        # restores an app full of broken links.
        held = {n.split("/", 1)[1] for n in names if n.startswith("uploads/")}
        held |= {n.split("/", 1)[1] for n in names if n.startswith("room_photos/")}
        # First: is there anything to check? Without this the check below is
        # true of the empty set, and dropping every document from the backup
        # passes it. That is exactly what it did.
        on_disk = 0
        for folder in (m.UPLOAD_DIR, m.ROOM_PHOTO_DIR):
            for _root, _dirs, files in os.walk(folder):
                on_disk += len(files)
        s.check("there are documents on disk for a backup to carry", on_disk > 0,
                detail=f"{on_disk} file(s)")
        s.check("and the full backup carries them", len(held) == on_disk,
                detail=f"{len(held)} of {on_disk} in the zip")
        s.check("the document written for this drill is referenced by a row",
                seeded_name in wanted_files,
                detail=f"{len(wanted_files)} referenced file(s) exist on disk")
        s.check("and the backup carried it", seeded_name in held,
                detail="this is the check that fails when media is dropped")
        absent = sorted(wanted_files - held)
        s.check("every file the database points at is in the backup", not absent,
                detail=f"{len(absent)} missing, e.g. {absent[:3]}" if absent else
                       f"{len(wanted_files)} file(s) checked")

        s.section("A database-only backup says that is what it is")
        # Not a failure. Dropping media to get under a mail provider's limit is
        # the right call — but an owner holding a database-only copy and not
        # knowing it is how a restore goes wrong months later.
        small = m.build_backup_zip(include_media=False)
        snames = zipfile.ZipFile(io.BytesIO(small)).namelist()
        s.check("it still carries the database", "gudanes_hr.db" in snames)
        s.check("and nothing else", len(snames) == 1, detail=", ".join(snames[:4]))
        s.check("it is smaller than the full one", len(small) <= len(blob),
                detail=f"{len(small)} vs {len(blob)} bytes")
        note = getattr(m, "BACKUP_MEDIA_OMITTED_NOTE", "")
        s.check("there is a note for a database-only backup", bool(note))
        # Named, not implied. "A backup is attached" is true and useless.
        s.check("and it says what is not in it",
                all(w in note.lower() for w in ("document", "photo")),
                detail=note[:90])
        s.check("and where to get the rest", "backup" in note.lower() and
                ("admin" in note.lower() or "download" in note.lower()),
                detail=note[:90])
        import inspect
        s.check("the job actually uses it",
                "BACKUP_MEDIA_OMITTED_NOTE" in inspect.getsource(m.run_backup_email_job),
                detail="a note nothing sends is not a note")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if os.path.exists(seeded_path):
            os.remove(seeded_path)
        cleanup = db()
        cleanup.execute("DELETE FROM expenses WHERE filename = ?", (seeded_name,))
        cleanup.execute("DELETE FROM audit_log WHERE target = ?", (marker,))
        cleanup.execute("DELETE FROM audit_log WHERE action = 'restore_drill'")
        cleanup.commit()
        cleanup.close()

    return s


if __name__ == "__main__":
    print(run().report())
