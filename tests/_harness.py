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
from flask import request  # noqa: E402

m.app.config["WTF_CSRF_ENABLED"] = False
m.STRIPE_SECRET_KEY = ""

# On a machine with no database — a fresh clone, or CI — there is nothing to
# copy, and importing app.py does not create the schema: init_db() only runs
# under `python app.py` or wsgi.py. Without this, eight of nine suites died on
# "no such table: users", which would make the suite useless as a gate on
# exactly the checkout a deploy is built from.
#
# Nothing above may open a connection first. init_db() decides whether to seed
# the owner account from whether the file exists, and sqlite3.connect CREATES
# it — so a single probe query beforehand makes a fresh database look
# established, and the suite then dies on "no owner in the test database".
# Idempotent either way, and on a copied database it usefully applies any
# migration that copy predates.
m.init_db()

assert m.DB_PATH == SCRATCH_DB, (
    f"tests would have run against {m.DB_PATH} — refusing. "
    "The GUDANES_DB_PATH override in app.py is missing or was overwritten."
)


# Which endpoints the suite actually reaches. Measured rather than asserted,
# because "we have tests" is not the same claim as "this page is tested", and
# only one of them is checkable. run.py prints the gap at the end.
EXERCISED = set()


@m.app.before_request
def _record_endpoint():          # pragma: no cover - bookkeeping, not behaviour
    if request.endpoint:
        EXERCISED.add(request.endpoint)


def coverage_report():
    """(exercised, untested, by_area) for every page a person can open.

    Excludes the machinery nobody navigates to — static files, webhooks, the
    JSON polled by the nav badge — because counting those as untested pages
    would make the number less honest, not more.
    """
    skip_exact = {"static", "notifications_unread_count", "stripe_webhook"}
    pages = {}
    for rule in m.app.url_map.iter_rules():
        ep = rule.endpoint
        if ep in skip_exact or ep.startswith("static"):
            continue
        if "webhook" in ep:
            continue
        pages[ep] = str(rule)

    area_of = getattr(m, "ENDPOINT_AREA", {})
    by_area = {}
    for ep in sorted(pages):
        area = area_of.get(ep) or ("public/other")
        hit = ep in EXERCISED
        entry = by_area.setdefault(area, {"hit": [], "miss": []})
        entry["hit" if hit else "miss"].append(ep)
    return set(pages) & EXERCISED, set(pages) - EXERCISED, by_area


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


def ensure_owner():
    """An owner to act as, created if the database has none.

    app.py decides whether to bootstrap the first owner with
    `fresh = not os.path.exists(DB_PATH)`. The scratch copy always exists by
    the time the app imports, so a SOURCE database that has a schema but no
    owner produces a test database with no owner either -- and every suite
    then dies on `clients()` with "no owner", which says nothing about why.

    That is not hypothetical. A single `python -c "import app"` run outside
    this harness creates an empty gudanes_hr.db in the working tree, and from
    then on every test run in that clone copies it and fails the same way.
    Rather than depend on nobody ever doing that, make the owner a thing the
    harness guarantees, exactly as it already does for the employee below.
    """
    conn = db()
    try:
        row = conn.execute(
            "SELECT id, name FROM users WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
        if row:
            return row
        from werkzeug.security import generate_password_hash
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
               VALUES (?, ?, 'owner', 'Test Owner', 'Owner', 'active', ?)""",
            ("test.owner@example.invalid",
             generate_password_hash(secrets_token()), datetime_now()),
        )
        conn.commit()
        return conn.execute(
            "SELECT id, name FROM users WHERE email = 'test.owner@example.invalid'"
        ).fetchone()
    finally:
        conn.close()


def ensure_employee():
    """An employee to act as, created if the database has none.

    A fresh database seeds only the owner, so on CI — or any clean clone —
    every suite that needs a second person had nothing to use and crashed.
    Borrowing whatever employee happens to exist is also how suites end up
    depending on each other's leftovers.
    """
    conn = db()
    try:
        row = conn.execute(
            "SELECT id, name FROM users WHERE role='employee' ORDER BY id LIMIT 1").fetchone()
        if row:
            return row
        from werkzeug.security import generate_password_hash
        conn.execute(
            """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
               VALUES (?, ?, 'employee', 'Test Employee', 'General', 'active', ?)""",
            ("test.employee@example.invalid",
             generate_password_hash(secrets_token()), datetime_now()),
        )
        conn.commit()
        return conn.execute(
            "SELECT id, name FROM users WHERE email = 'test.employee@example.invalid'"
        ).fetchone()
    finally:
        conn.close()


def ensure_room():
    """A bookable room, created if the database has none."""
    conn = db()
    try:
        row = conn.execute(
            "SELECT id, name FROM rooms WHERE active=1 ORDER BY id LIMIT 1").fetchone()
        if row:
            return row
        # rooms has no created_at — it is not a log, it is a catalogue.
        conn.execute(
            """INSERT INTO rooms (name, export_token, active, max_occupancy,
               price_per_night, sort_order) VALUES (?, ?, 1, 4, 250.0, 99)""",
            ("Test Room", secrets_token()),
        )
        conn.commit()
        return conn.execute("SELECT id, name FROM rooms WHERE name='Test Room'").fetchone()
    finally:
        conn.close()


def secrets_token():
    import secrets
    return secrets.token_urlsafe(16)


def datetime_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def clients():
    """Logged-in test clients: (owner, employee)."""
    ensure_owner()
    ensure_employee()
    conn = db()
    owner = conn.execute("SELECT id, name FROM users WHERE role='owner' LIMIT 1").fetchone()
    emp = conn.execute("SELECT id, name FROM users WHERE role='employee' LIMIT 1").fetchone()
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


def _print_safely(line):
    """Print a result line even when it quotes the app's own copy.

    The console here is cp1252, and user-facing strings are full of characters
    it cannot encode -- the arrow in "Admin > Backup", the em dashes everywhere.
    A detail containing one raised UnicodeEncodeError from inside check(),
    which crashed the suite and took every check after it down with it. That
    happened only on failure, i.e. only ever at the moment a suite most needed
    to report, so it read as "the tests broke" rather than "the code did".
    """
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, "replace").decode(enc, "replace"))


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
        _print_safely(line)
        return bool(ok)

    @property
    def passed(self):
        return sum(1 for _, ok in self.results if ok)

    @property
    def failed(self):
        return [n for n, ok in self.results if not ok]
