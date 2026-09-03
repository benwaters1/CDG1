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
import shutil
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

# UPLOADS GET THE SAME TREATMENT AS THE DATABASE, and for the same reason.
#
# app.py falls back to DATA_DIR/uploads when GUDANES_UPLOAD_DIR is unset, and
# nothing here was setting it -- so every run wrote its test documents into one
# shared directory and never cleared it. Seven thousand files later, suites
# started tripping over each other: the backup drill would find a file on disk
# that no row referenced, or a row whose file another suite had removed, and
# WHICH of those you got depended on the order the suites happened to run in.
# Two runs of the same code disagreed, which makes the whole suite worth less
# than it looks.
#
# A fresh directory per run, torn down at exit like the database copy. It also
# stops the tests littering a directory the app itself uses.
SCRATCH_UPLOADS = tempfile.mkdtemp(prefix="gudanes_test_uploads_")
os.environ["GUDANES_UPLOAD_DIR"] = SCRATCH_UPLOADS
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
    # The run's uploads go with it. Best-effort: a file the app still has open
    # on Windows is not worth failing a finished run over, and the directory is
    # under the system temp folder either way.
    shutil.rmtree(SCRATCH_UPLOADS, ignore_errors=True)


import app as m  # noqa: E402  — must follow the env setup above
from flask import request  # noqa: E402

m.app.config["WTF_CSRF_ENABLED"] = False

# ---------------------------------------------------------------------------
# Outbound isolation.
#
# Stripping the keys out of os.environ above is NOT enough on its own, and for
# a long time this file claimed it was. Importing app runs _load_dotenv(),
# which reads .env and puts every one of those keys straight back — so by the
# time app.py reaches `if STRIPE_SECRET_KEY: stripe.api_key = STRIPE_SECRET_KEY`
# the real credential is there, and it gets wired into the client library.
# Blanking the app's own flag afterwards does not unwire it.
#
# That left `stripe_enabled()` returning False as the ONLY thing between this
# suite and the real Stripe account, checked separately at every call site. A
# deliberately broken control that removed one of those checks reached the
# network and came back with a genuine Stripe request id.
#
# The same shape applies to the other two outbound channels, and they are
# worse: the Pennylane token is live rather than test-mode, and it was
# protected by one suite patching _pennylane_request for itself. Email is
# inert only while no provider is configured — the moment one is, a test run
# would send real messages to the real guest addresses in the copied database.
#
# So neutralise the credentials themselves and make the transports raise.
# Anything that genuinely needs one stands its own in and puts it back
# afterwards (see test_autocharge and test_refunds).
# ---------------------------------------------------------------------------
m.STRIPE_SECRET_KEY = ""
m.STRIPE_PUBLISHABLE_KEY = ""
m.STRIPE_WEBHOOK_SECRET = ""
m.stripe.api_key = None          # no key -> the library refuses before any request

m.PENNYLANE_API_TOKEN = None
m.RESEND_API_KEY = None
m.SMTP_HOST = m.SMTP_USERNAME = m.SMTP_PASSWORD = None
m.MS_GRAPH_CLIENT_SECRET = m.MS_GRAPH_TENANT_ID = m.MS_GRAPH_CLIENT_ID = None


def _refuse(what, remedy):
    def _blocked(*_a, **_kw):
        raise AssertionError(
            f"a test tried to reach {what}. The suite must never do that — "
            f"{remedy}"
        )
    return _blocked


# Kept before it is replaced, because the "Pennylane isn't connected" guard
# lives INSIDE this function -- so blocking it wholesale means the branch every
# press of that button takes today cannot be reached, and the route 500s in the
# harness where the owner would see a sentence. A suite that wants to test the
# guard puts this back for a few lines and blocks urlopen underneath it, which
# is the only arrangement where the guard is the thing answering. The module
# global stays the raiser, so nothing reaches Pennylane by accident.
REAL_PENNYLANE_REQUEST = m._pennylane_request

m._pennylane_request = _refuse(
    "Pennylane", "the token is live; stand in for _pennylane_request in the test")

# Texting, blocked before there is anything to leak rather than after. There
# are no credentials for it yet, so nothing could go out today — which is
# exactly why this belongs here now: the Stripe hole existed because the block
# was added once the key already worked, and every run in between was covered
# by nothing but a conditional.
m.SMS_PROVIDER_SID = m.SMS_PROVIDER_TOKEN = m.SMS_FROM_NUMBER = None
m.sms_provider_send = _refuse(
    "the SMS provider",
    "every message costs money; stand in for sms_provider_send in the test")
m.send_email_via_resend = _refuse(
    "Resend", "mock send_email, or let it fall through to the held outbox")

# Open-Meteo. It needs no key and costs nothing, which is exactly why it would
# have been left out -- and a suite that reaches the network is a suite that
# fails on an aeroplane and passes on a desk, which is worse than one that
# fails everywhere. The page reads a cached value and never calls this, so
# nothing but the job should ever reach it.
m.fetch_weather = _refuse(
    "Open-Meteo",
    "the page reads a cached reading; stand in for fetch_weather in the test")

# Anthropic, for the same reason texting is here and one the file already
# learned the hard way. Three routes build a real client - reading a supplier
# invoice, reading a menu, drafting a reply - and each is guarded only by
# `if not claude_configured()`, which is a conditional at the call site with a
# live module global behind it. That is the exact shape of the Stripe hole:
# stripe_enabled() returning False was doing all the work while stripe.api_key
# stayed real through every run.
#
# ANTHROPIC_API_KEY is not set today, so nothing could go out this morning.
# That is the argument for doing it now rather than against it - it is on the
# owner's list to set, and the moment it is, a run that reaches any of those
# three would spend money and hand a supplier's invoice, or a guest's email, to
# a third party. Blocked before there is anything to leak.
m.ANTHROPIC_API_KEY = None

# Browser push, for the same reason and at the same stage as the two above.
# A staff member who turns notifications on has handed us an endpoint at a
# browser vendor's push service and a key to sign for it, and notify_user
# reads those straight out of push_subscriptions — which, in here, is a copy
# of the REAL table. There are no rows in it today; that is the argument for
# blocking it now rather than the argument against, because the first person
# to enable it on their phone would otherwise start receiving a test run.
m.webpush = _refuse(
    "the browser push service",
    "stand in for notify_user in the test; a push goes to somebody's real phone")
m.anthropic.Anthropic = _refuse(
    "the Anthropic API",
    "every call costs money and ships the document off this machine; stand in "
    "for the helper the route calls, not for claude_configured")

# Proof, rather than the assumption this file used to make. Each of these was
# true only by accident of what happens to be in .env on one machine.
assert not m.stripe_enabled(), "Stripe is still enabled under test"
assert not getattr(m.stripe, "api_key", None), (
    "stripe.api_key still holds a real key — app.py wired it in at import and "
    "blanking STRIPE_SECRET_KEY afterwards does not undo that")
assert not m.PENNYLANE_API_TOKEN, "the live Pennylane token is still set under test"
assert not m.sms_enabled(), "a texting provider is configured under test"
assert m.fetch_weather.__name__ == "_blocked", (
    "the weather fetch is not blocked under test — it needs no key and costs "
    "nothing, which is why it is the one that gets forgotten")
assert m.webpush.__name__ == "_blocked", (
    "the browser push send is not blocked under test")
assert not m.claude_configured(), (
    "ANTHROPIC_API_KEY is still set under test — app.py read it into a module "
    "global at import, and _load_dotenv puts the environment variable back, so "
    "clearing os.environ before the import does not undo it")
assert not (m.email_enabled() or m.resend_enabled()), (
    "an email provider is configured under test — a run would send real mail "
    "to the real guest addresses in the copied database")

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

assert m.UPLOAD_DIR == SCRATCH_UPLOADS, (
    f"tests would have written uploads into {m.UPLOAD_DIR} — refusing. "
    "The GUDANES_UPLOAD_DIR override in app.py is missing or was overwritten."
)


# Which endpoints the suite actually reaches. Measured rather than asserted,
# because "we have tests" is not the same claim as "this page is tested", and
# only one of them is checkable. run.py prints the gap at the end.
EXERCISED = set()

# And which of those actually ANSWERED. before_request fires before the view
# runs, so EXERCISED alone counts the knock rather than the reply: a page a
# test only ever got a 403 or a login redirect from would be counted as
# covered. RENDERED is filled after the view has produced something.
RENDERED = set()

# What each endpoint actually replied, kept so the report can say WHY a page
# counts as unanswered. A name on its own does not tell a missing test from a
# mistake in the measure.
ANSWERS = {}

# Turned away at the door. A plain 302 is not on this list -- post-redirect-get
# is how most forms here answer, and calling that a refusal would erase real
# coverage. A redirect to the login page is a refusal, and it is checked by
# where it points rather than by its code.
_REFUSAL_CODES = {401, 403, 404, 405, 500, 502, 503}


@m.app.before_request
def _record_endpoint():          # pragma: no cover - bookkeeping, not behaviour
    if request.endpoint:
        EXERCISED.add(request.endpoint)


@m.app.after_request
def _record_answer(response):    # pragma: no cover - bookkeeping, not behaviour
    if not request.endpoint:
        return response
    code = response.status_code
    where = (response.headers.get("Location") or "").split("?")[0]
    ANSWERS.setdefault(request.endpoint, set()).add(
        (request.method, code, where[-40:]))

    refused = code in _REFUSAL_CODES
    if code in (301, 302, 303, 307, 308):
        trimmed = where.rstrip("/")
        refused = trimmed.endswith("/login") or trimmed == "/login"
    # logout is the one endpoint whose SUCCESS is a redirect to the login
    # page, so the rule above would call the only thing it does a refusal.
    # Named rather than handled by loosening the rule, which would excuse
    # every page that quietly sends people to log in.
    if request.endpoint == "logout":
        refused = False

    if not refused:
        RENDERED.add(request.endpoint)
    return response


def coverage_report():
    """(exercised, untested, by_area) for every page a person can open.

    Excludes the machinery nobody navigates to — static files, webhooks, the
    JSON polled by the nav badge — because counting those as untested pages
    would make the number less honest, not more.

    EXERCISED IS NOT THE MEASURE. A page counts here only if the view actually
    answered: a test that got a login redirect, a 403, or a 405 on a POST-only
    route reached the endpoint without testing anything, and counting that is
    how a coverage figure starts overstating. Anything reached but never
    answered is returned separately so run.py can name it rather than quietly
    fold it into either column.
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
        hit = ep in RENDERED
        entry = by_area.setdefault(area, {"hit": [], "miss": []})
        entry["hit" if hit else "miss"].append(ep)
    return set(pages) & RENDERED, set(pages) - RENDERED, by_area


def coverage_knocked_only():
    """Endpoints a test reached but never got an answer out of.

    Every one is a page the old figure counted as covered. Named rather than
    counted: "three pages are only ever refused" is a number nobody acts on,
    and the names say which.
    """
    skip_exact = {"static", "notifications_unread_count", "stripe_webhook"}
    pages = set()
    for rule in m.app.url_map.iter_rules():
        ep = rule.endpoint
        if ep in skip_exact or ep.startswith("static") or "webhook" in ep:
            continue
        pages.add(ep)
    return [(ep, sorted(ANSWERS.get(ep, ())))
            for ep in sorted((pages & EXERCISED) - RENDERED)]


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


def house_today():
    """What day it is AT THE HOUSE.

    Not date.today(), which is the day on whatever machine is running the
    tests. The Ariege and a developer's laptop disagree for part of every
    day, and two suites went red at midnight because of it -- asking about
    "today" while the app answered about a different one.

    Every date this app cares about is a French calendar date. A suite
    built on the local clock is testing where the developer is sitting.

    Delegates rather than repeating the expression: app.py now has one
    definition of what day it is here, and a suite that spelled it out again
    could drift from the thing it is meant to be checking. It still answers
    to a frozen clock, because house_today() reads m.datetime too.
    """
    return m.house_today()


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

    def report(self):
        """The last line when a suite is run on its own.

        Every suite file ends `print(run().report())` and this did not exist,
        so running one directly printed its checks and then raised. The checks
        have already printed themselves by the time this is called; all that
        is left to say is the count, and which ones failed.
        """
        total = len(self.results)
        head = f"\n{self.passed}/{total} passed in {self.name!r}"
        if not self.failed:
            return head
        return head + "\n  failed:\n" + "\n".join(
            f"    {n}" for n in self.failed)
