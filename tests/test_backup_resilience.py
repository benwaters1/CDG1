"""A backup that cannot read one photo still has to be a backup.

build_backup_zip lists the media folders with os.walk and writes them
afterwards. Anything deleted, locked or renamed in between raised
FileNotFoundError out of the whole function, so the owner's backup download
answered 500 and the scheduled job failed — over one file, while the database
and every other document were sitting there ready to be written.

That is not hypothetical: it happened during a suite run on this machine and
took five checks in an unrelated suite down with it, and another agent works
in this tree while backups run. In production the same shape is a document
deleted while the nightly backup is building.

So media is skipped rather than raised on. main does that, and its own suite
checks the zip comes with FILES_SKIPPED.txt naming what went. This suite is
the other half: a note inside the zip only reaches whoever opens the zip, and
nobody opens a backup until the day they need it. So the skip is handed back
to the caller as well — written to the audit log by the manual download, and
into the line recorded against the scheduled run.

The database is the exception and is never skipped. A zip without it is not a
backup, and there is a check for that too.
"""
import io
import os
import zipfile

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-bkp-"


def _cleanup(conn):
    conn.execute("DELETE FROM audit_log WHERE action IN "
                 "('backup_files_skipped', 'backup_downloaded')")
    conn.commit()


class _VanishingFile:
    """A file that exists for os.walk and is gone by the time it is written.

    Patching zipfile is the only honest way to reproduce this: the real race
    needs another process to delete the file inside a window a few
    microseconds wide, which a test cannot arrange and should not try to.
    """

    def __init__(self, victim):
        self.victim = victim
        self.real = zipfile.ZipFile.write

    def __enter__(self):
        victim, real = self.victim, self.real

        def write(zf, filename, arcname=None, *a, **kw):
            if os.path.basename(filename) == victim:
                raise FileNotFoundError(2, "No such file or directory", filename)
            return real(zf, filename, arcname, *a, **kw)

        zipfile.ZipFile.write = write
        return self

    def __exit__(self, *exc):
        zipfile.ZipFile.write = self.real
        return False


def _seed_media():
    """Two real files in the upload folder, so there is something to lose."""
    os.makedirs(m.UPLOAD_DIR, exist_ok=True)
    for stale in os.listdir(m.UPLOAD_DIR):
        if stale.startswith(TAG):
            try:
                os.remove(os.path.join(m.UPLOAD_DIR, stale))
            except OSError:
                pass
    names = [TAG + "keep.txt", TAG + "vanish.txt"]
    for n in names:
        with open(os.path.join(m.UPLOAD_DIR, n), "w", encoding="utf-8") as fh:
            fh.write("x")
    return names


def _unseed(names):
    for n in names:
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, n))
        except OSError:
            pass


def run():
    s = Suite("backup resilience")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    keep, vanish = _seed_media()

    s.section("A file that disappears mid-zip does not cost the backup")
    skipped, blob, blew_up = [], b"", None
    try:
        with _VanishingFile(vanish):
            blob = m.build_backup_zip(include_media=True, skipped_out=skipped)
    except Exception as e:
        blew_up = f"{type(e).__name__}: {e}"
    s.check("a backup was still produced", bool(blob) and not blew_up,
            detail=blew_up or f"{len(blob)} bytes")
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist() if blob else []
    s.check("the database is in it", "gudanes_hr.db" in names, detail=str(names[:6]))
    s.check("the file that was still there was written",
            any(keep in n for n in names), detail=str(names[:6]))
    s.check("and the one that vanished is not",
            not any(vanish in n for n in names), detail=str(names[:6]))

    s.section("The caller is handed the list, not just the zip")
    # The zip says what was left out, and test_restore_drill checks that. It
    # says it only to whoever opens it, though, which is nobody until the day
    # of a restore. This is the part that can reach the owner today.
    s.check("the caller is told which file went",
            len(skipped) == 1 and vanish in skipped[0], detail=str(skipped))
    s.check("and told the reason, not just the name",
            skipped and ":" in skipped[0], detail=str(skipped))

    s.section("A backup with nothing wrong hands back nothing")
    clean = []
    blob2 = m.build_backup_zip(include_media=True, skipped_out=clean)
    s.check("nothing was skipped", clean == [], detail=str(clean))
    s.check("and a backup was still produced", bool(blob2), detail=f"{len(blob2)} bytes")

    s.section("The owner can find out, from the page and from the job")
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'backup_files_skipped'"
    ).fetchone()["c"]
    with _VanishingFile(vanish):
        r = oc.get("/admin/backup")
    s.check("the download still succeeds", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and it is still a zip",
            r.headers.get("Content-Type", "").startswith("application/zip"),
            detail=r.headers.get("Content-Type"))
    after = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'backup_files_skipped'"
    ).fetchone()["c"]
    # A file download has nowhere to put a message, so the audit log is the
    # only place this could ever reach the owner.
    s.check("the skip is written to the audit log", after == before + 1,
            detail=f"{before} -> {after}")
    row = conn.execute(
        """SELECT details FROM audit_log WHERE action = 'backup_files_skipped'
             ORDER BY id DESC LIMIT 1""").fetchone()
    s.check("naming the file", row is not None and vanish in (row["details"] or ""),
            detail=None if row is None else row["details"])

    s.section("The database is never skipped")
    # Losing a photo is survivable. A zip without the database is not a backup,
    # and must fail loudly rather than arrive looking like one.
    real = zipfile.ZipFile.write

    def refuse_db(zf, filename, arcname=None, *a, **kw):
        if str(arcname) == "gudanes_hr.db":
            raise OSError(5, "Input/output error", filename)
        return real(zf, filename, arcname, *a, **kw)

    zipfile.ZipFile.write = refuse_db
    try:
        raised = False
        try:
            m.build_backup_zip(include_media=True)
        except OSError:
            raised = True
        s.check("a database that cannot be copied raises", raised,
                detail="a backup was returned without the database in it")
    finally:
        zipfile.ZipFile.write = real

    _unseed([keep, vanish])
    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
