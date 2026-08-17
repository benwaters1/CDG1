"""Shared test setup. Import this BEFORE anything imports app.

Two things matter here.

First, safety: these tests write rows. They must never touch the real
database, which holds staff records, guest addresses and password hashes.
So the harness copies the live file to a throwaway one and points the app at
that via GUDANES_DB_PATH. The copy uses SQLite's backup API rather than a
file copy because the dev server may well have the database open, and a
plain copy taken mid-transaction can be corrupt.

Second, isolation: CSRF and Stripe are pinned off. Real Stripe keys in .env
would otherwise mean a test run creates live sandbox objects, and a test that
depends on whether .env happens to exist is a test that fails on a colleague's
machine for no reason.
"""
import atexit
import os
import re
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REAL_DB = os.path.join(ROOT, "gudanes_hr.db")


def _scratch_db():
    fd, path = tempfile.mkstemp(prefix="gudanes_test_", suffix=".db")
    os.close(fd)
    os.remove(path)          # let the app create and seed it if there's no source
    if os.path.exists(REAL_DB):
        src = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)  # consistent even while the dev server holds it open
        finally:
            src.close()
            dst.close()
    return path


SCRATCH_DB = _scratch_db()
os.environ["GUDANES_DB_PATH"] = SCRATCH_DB
# Never let a test reach the payment provider, whatever is in .env.
for _k in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
    os.environ.pop(_k, None)


@atexit.register
def _cleanup():
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.remove(SCRATCH_DB + suffix)
        except OSError:
            pass


import app as m  # noqa: E402  — must follow the env setup above

m.app.config["WTF_CSRF_ENABLED"] = False
m.STRIPE_SECRET_KEY = ""

assert m.DB_PATH == SCRATCH_DB, (
    f"tests would have run against {m.DB_PATH} — refusing. "
    "The GUDANES_DB_PATH override in app.py is missing or was overwritten."
)


def db():
    return m.get_db()


def flashes(response):
    """The user-facing messages on a rendered page.

    Worth reading on failure: it tells apart 'the app broke' from 'the app
    correctly refused', which look identical from a status code alone.
    """
    html = response.get_data(as_text=True)
    return [" ".join(x.split())
            for x in re.findall(r'class="flash flash-\w+">(.*?)</div>', html, re.S)]


def clients():
    """Logged-in test clients: (owner, employee)."""
    conn = db()
    owner = conn.execute("SELECT id FROM users WHERE role='owner' LIMIT 1").fetchone()
    emp = conn.execute("SELECT id FROM users WHERE role='employee' LIMIT 1").fetchone()
    conn.close()
    if not owner:
        raise RuntimeError("no owner in the test database")
    oc, ec = m.app.test_client(), m.app.test_client()
    with oc.session_transaction() as s:
        s["user_id"] = owner["id"]
    if emp:
        with ec.session_transaction() as s:
            s["user_id"] = emp["id"]
    return oc, ec, owner, emp


class Suite:
    """Collects results so one failure doesn't hide the rest of the run."""

    def __init__(self, name):
        self.name = name
        self.results = []

    def section(self, title):
        print(f"\n  -- {title}")

    def check(self, name, ok, response=None, detail=""):
        self.results.append((name, bool(ok)))
        line = f"    {'PASS' if ok else 'FAIL'}  {name}"
        if not ok:
            if detail:
                line += f"   {detail}"
            if response is not None and flashes(response):
                line += f"   app said: {flashes(response)[:1]}"
        print(line)
        return bool(ok)

    @property
    def passed(self):
        return sum(1 for _, ok in self.results if ok)

    @property
    def failed(self):
        return [n for n, ok in self.results if not ok]
