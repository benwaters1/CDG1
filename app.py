"""
Château de Gudanes — Staff & Estate Operations
A small, real, self-hosted app: real logins, a real database, real file uploads.
No JS framework, no build step — server-rendered Flask + a bit of vanilla JS.

WHAT'S IN HERE
Staff nav is grouped into Guests / Employee / Financial (owner) — employees
get a smaller version of the same groups.

- Staff HR: employee accounts (self-service onboarding links, an
  onboarding checklist, skills tags, emergency contacts), documents with
  optional expiry tracking, a staff manual, shared business contacts, and a
  shared shopping list.
- Timesheet: logging in clocks you in, logging out clocks you out — no
  separate punch clock. A break/pause toggle while clocked in subtracts
  from worked hours everywhere hours are shown. Owner gets a full report,
  CSV export, and a France-standard 35h/week awareness flag.
- Shift scheduling: a weekly rota (separate from tasks — this is who's
  *supposed* to be working, tasks are what needs doing), cross-checked
  against actual clock-ins to flag no-shows and late arrivals. "Copy last
  week" duplicates a repeating schedule forward.
- Tasks: per-employee assignment with a day/week/month view (drag-and-drop
  on the week view), priority, optional weekly repeat, and overdue
  highlighting.
- Leave/time-off: employees request, owner approves/declines (emails the
  employee if SMTP is configured), with warnings for shift or open-task
  conflicts during the requested dates.
- Guest operations: a Guests log staff can see, and a guest-facing public
  booking site (/book) with real availability checking (including an
  inline availability calendar), two-way iCal sync with
  Airbnb/Booking.com/VRBO so nothing double-books, room photos and
  amenities, and optional paid add-ons.
- Money paperwork: supplier invoice intake (via a shareable link, no
  supplier login needed) and staff expense claims (also shown on each
  employee's own profile), both with an approve/reject/paid workflow.
- Management (owner-only): company-wide documents (insurance, registration,
  bank details) and a Vault for shared logins, encrypted at rest — see
  ENABLING THE VAULT below.
- A printable daily ops sheet (today's shifts/tasks/who's off/guests) for
  posting somewhere non-digital, and a Recent Activity feed on the owner
  dashboard.
- Email and Stripe payment collection are both built in but *off by
  default* — see ENABLING EMAIL / ENABLING PAYMENTS below. Until you add
  the credentials, the app behaves exactly as if neither existed: booking
  is request-only, and guests get their reference code/link on screen only.
  Without email specifically, "forgot password" tells the employee to ask
  the owner instead of pretending to send something that would silently fail.

WHY THIS EXISTS
The earlier "Estate Ledger" tool ran entirely in a browser artifact with no
real backend — fine for a shared task board, not fine once pay, guest
payments-adjacent data, and personal staff data are involved. This is the
real thing: an actual server, an actual database, actual password hashing,
actual per-role access control.

ENABLING EMAIL (booking confirmations, owner alerts, decline/refund notices,
leave decisions, password resets)
Two ways to send, tried in this order:
1. Resend (recommended) — set RESEND_API_KEY and RESEND_FROM (a verified
   sender address on a domain you've added to Resend). Sign up at
   resend.com, add your domain, and add the DNS records Resend gives you
   at wherever your domain's DNS is managed — this works the same
   regardless of which registrar you bought the domain through (Crazy
   Domains, GoDaddy, etc.); DNS records aren't tied to the registrar.
2. Plain SMTP (fallback) — set SMTP_HOST, SMTP_PORT (587 is typical),
   SMTP_USERNAME, SMTP_PASSWORD, and optionally SMTP_FROM. Any real
   mailbox works.
See DEPLOY.md for where these go on Railway.

ENABLING PAYMENTS (guest pays the full total via Stripe Checkout when
requesting a booking; auto-refunded if the owner declines or cancels)
Set STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY from your Stripe
Dashboard → Developers → API keys (test-mode keys first). Also set
STRIPE_WEBHOOK_SECRET and point a webhook at /webhooks/stripe listening
for checkout.session.completed — that's what reliably creates the booking
even if a guest closes their browser right after paying. Card data never
touches this server; Stripe's hosted Checkout page handles it.

ENABLING SCHEDULED iCAL SYNC (pull Airbnb/Booking.com/VRBO calendars
automatically every 1-3 hours instead of only on a manual click)
Set ICAL_SYNC_TOKEN and point an external scheduler (Railway Cron, a free
pinger, plain crontab) at /api/sync-ical?token=... — see DEPLOY.md.

ENABLING THE VAULT (Management → Vault, for shared logins)
Set VAULT_ENCRYPTION_KEY — generate with:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Entries are encrypted before they touch the database. Losing this key loses
everything in the vault — back it up somewhere separate from the database.

LOCAL_TZ (optional, defaults to Europe/Paris)
Only affects how timesheet/shift times are *displayed* — everything is
stored in UTC regardless.

WHAT THIS DOES NOT DO (on purpose — this needs real professional
judgment this app can't provide)
- No payroll (payslips, URSSAF declarations, French labor law obligations).
  Pay rate/type is stored as a reference only, and the 35h/week flag is
  awareness only — the real process stays with your accountant / Pennylane.
- Auto-generated employment summaries are drafts for your lawyer to turn
  into the real signed contract, not binding documents themselves.

RUNNING IT LOCALLY (to try it before deploying anywhere)
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000 in a browser.
First run creates the database and one owner account — check the terminal
output for the generated owner password (change it after first login).

DEPLOYING IT FOR REAL
See DEPLOY.md in this folder for the two simplest options (Railway or a
cheap VPS). Dependencies are still deliberately minimal (Flask, Werkzeug,
stripe, cryptography for the vault, tzdata for correct local-time display
on Windows, pywebpush for browser push notifications) — no framework,
no build step, no background job runner.
"""

import os
import re
import io
import zipfile
import csv
import sqlite3
import hmac
import json
import secrets
import string
import smtplib
import ssl
import base64
import threading
import time
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta, date
from functools import wraps
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from calendar import monthrange

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, abort, jsonify
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import stripe
import anthropic
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import webpush, WebPushException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Overridable so the test suite can run against a throwaway copy instead of
# real staff and guest data, and so a deployment can put the file on a mounted
# volume — on Railway the repo directory is ephemeral and would lose the
# database on every deploy.
DB_PATH = os.environ.get("GUDANES_DB_PATH") or os.path.join(BASE_DIR, "gudanes_hr.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ROOM_PHOTO_DIR = os.path.join(BASE_DIR, "room_photos")
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "docx", "doc", "txt"}
# Not RFC-5322-exhaustive — just enough shape-checking to reject garbage
# and control characters (a public, unauthenticated form is the only place
# this matters; \s excludes the \r/\n that broke send_email() on a crafted
# submission before that was hardened separately).
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg"}
VIEWABLE_EXTENSIONS = IMAGE_EXTENSIONS | {"pdf"}  # types a browser can render inline, no download needed

CHECKOUT_CHECKLIST = [
    "Strip beds & collect linen",
    "Restock towels & toiletries",
    "Inspect for damage or missing items",
    "Clean & reset bathroom",
    "Final walkthrough",
]
ARRIVAL_PREP_CHECKLIST = [
    "Confirm room is guest-ready",
    "Wine bottle",
    "Water bottle for each guest ({n})",
    "Glasses for each guest ({n})",
    "Hand sanitiser",
    "Fresh towels",
    "Review special requests",
    "Confirm arrival time with guest",
]
MAX_UPLOAD_MB = 15
LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_MINUTES = 15
BOOKING_RATE_LIMIT_PER_HOUR = 5
STALE_PENDING_BOOKING_HOURS = 48
AUTOMATION_TICK_SECONDS = 300
VEHICLE_TRANSFER_BUFFER_HOURS = 2
AUTOMATION_SETTING_DEFAULTS = {
    "automation_housekeeping_enabled": "1",
    "automation_daily_digest_enabled": "1",
    "automation_ical_sync_enabled": "1",
    "automation_ical_sync_interval_hours": "6",
    "automation_workshop_balance_reminder_enabled": "1",
    "automation_workshop_balance_reminder_days_before": "7",
    "automation_waitlist_autonotify_enabled": "1",
    "automation_workshop_feedback_enabled": "1",
    "automation_email_scan_enabled": "1",
    "automation_hr_escalation_enabled": "1",
    "automation_campaign_triggers_enabled": "1",
    "automation_email_unanswered_hours": "24",
    "automation_email_scan_lookback_days": "14",
    "automation_stale_shift_enabled": "1",
    "automation_stale_shift_hours": "14",
}
EMPLOYER_LEGAL_NAME = "SCI Torrents"


_PLACEHOLDER_MARKERS = (
    "paste", "your_", "your-", "yourkey", "changeme", "change_me", "replace",
    "todo", "xxx", "placeholder", "_here", "example.com", "sk_test_xxx",
)


def _looks_like_placeholder(value):
    """True for the scaffold text people leave in a half-filled .env.

    Only used on .env values, never on real environment variables — a genuine
    secret that happened to contain one of these words would still be honoured
    if it came from the actual environment.
    """
    v = (value or "").strip().lower()
    if not v or v in ("<>", "()", "-", "none", "null"):
        return True
    if v.startswith("<") and v.endswith(">"):
        return True
    return any(marker in v for marker in _PLACEHOLDER_MARKERS)


def _load_dotenv():
    """Read a local .env file into the environment, if one exists.

    Every secret this app takes (Stripe, Resend/SMTP, Microsoft Graph,
    Anthropic, the vault key) came only from real environment variables,
    which on Windows means `setx` + restarting every shell, and the value
    then sits in the user registry readable by any process you run. A
    gitignored file next to the app is easier to manage and easier to
    revoke — delete the line, restart, gone.

    Deliberately does NOT overwrite anything already set, so a real
    environment variable (Railway, systemd) always wins over the file, and
    production behaviour is unchanged. No dependency: the format here is
    just KEY=value, one per line, # for comments.

    Placeholder values are skipped. Every `*_enabled()` check in this file is
    a plain truthiness test, so a scaffolded `STRIPE_SECRET_KEY=PASTE_YOUR_KEY`
    made the app believe Stripe was live: the booking page then offered card
    payment and the guest hit an error at checkout, instead of falling back to
    pay-on-arrival as it does when the key is genuinely absent.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    skipped = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if not key or key in os.environ:
                    continue
                if _looks_like_placeholder(value):
                    skipped.append(key)
                    continue
                os.environ[key] = value
        if skipped:
            # Named, not silent: "why is Stripe off when I filled in the .env"
            # is a miserable thing to debug from no output at all.
            print(f"[config] ignoring placeholder value(s) in .env: {', '.join(skipped)}")
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"[config] could not read .env: {e}")


_load_dotenv()

# Everything is stored in UTC; this is only for displaying clock in/out times
# in the château's own local time (handles the CET/CEST switch automatically).
LOCAL_TZ = ZoneInfo(os.environ.get("LOCAL_TZ", "Europe/Paris"))

DEFAULT_TERMS = """DRAFT — NOT YET REVIEWED BY A LAWYER. Replace this notice and finalize
this page with your own legal/insurance advisor before relying on it.

BOOKING TERMS & CONDITIONS — CHÂTEAU DE GUDANES

1. Booking Requests
Submitting a booking request does not guarantee a reservation. Every
request is reviewed by the château before it becomes a confirmed booking.
You will be notified by email once a decision has been made.

2. Payment
Where online payment is enabled, the total shown at checkout is charged
at the time you submit your request — before your booking is confirmed.
This does not itself guarantee availability; see "Booking Requests" above.

3. Cancellations & Refunds
- Bookings are non-refundable. Once your booking is confirmed and paid
  for, we do not offer a refund as a matter of course if you cancel or do
  not arrive.
- We do, however, look at every cancellation individually. If your
  circumstances change, please contact us and tell us what has happened.
  We would rather hear from you than not, and we will do what we
  reasonably can — including, at our discretion, a full or partial
  refund. Please treat any such refund as a gesture of goodwill rather
  than an entitlement.
- If the château declines your booking request, or cancels a confirmed
  booking, any payment already taken is refunded in full.
- Where you booked through Booking.com or another travel site, that
  site's own cancellation terms apply instead of these, and any refund is
  arranged through them rather than with us directly.
- We strongly recommend travel insurance that covers cancellation.

4. Your Information
We collect your name, email, phone number, and any notes you provide in
order to process your booking. It is stored securely and is not sold or
shared with third parties, other than the payment processor (Stripe)
where online payment is used.

5. Contact
For any question about a booking, contact the château directly.

Last updated: [add a date once this is finalized]"""

# Email — unset until you add either Resend or SMTP credentials as
# environment variables. Until then, every send_email() call is a no-op
# that logs to the console instead of failing; the on-screen reference
# code/link stays the guaranteed way a guest can find their booking.
# Resend is tried first if RESEND_API_KEY is set (see module docstring);
# SMTP is the fallback so existing deployments keep working unchanged.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM = os.environ.get("RESEND_FROM")
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

# Microsoft Graph — read-only inbox monitoring for the "Inbox Flags" admin
# page (unanswered-email + pricing/availability-conflict detection). Needs
# an Azure AD app registration with an Application-type Mail.Read
# permission, admin-consented, and scoped to just the mailboxes below via an
# Exchange Online ApplicationAccessPolicy — see DEPLOY.md. Unset by default,
# same no-op-until-configured pattern as Resend/SMTP above.
MS_GRAPH_TENANT_ID = os.environ.get("MS_GRAPH_TENANT_ID")
MS_GRAPH_CLIENT_ID = os.environ.get("MS_GRAPH_CLIENT_ID")
MS_GRAPH_CLIENT_SECRET = os.environ.get("MS_GRAPH_CLIENT_SECRET")

# The château runs one inbox per business area (restaurant, bookings,
# experience, ...), and that list grows. MS_GRAPH_MAILBOXES takes any number,
# comma-separated. MS_GRAPH_MAILBOX (singular) is still honoured so an
# existing single-mailbox deployment keeps working untouched.
def _parse_mailboxes(raw):
    return [m.strip().lower() for m in (raw or "").replace(";", ",").split(",") if m.strip()]


MS_GRAPH_MAILBOXES = _parse_mailboxes(
    os.environ.get("MS_GRAPH_MAILBOXES") or os.environ.get("MS_GRAPH_MAILBOX")
)
# Kept for anything still referring to "the" mailbox; it is simply the first.
MS_GRAPH_MAILBOX = MS_GRAPH_MAILBOXES[0] if MS_GRAPH_MAILBOXES else None
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USERNAME

# AI-assisted reply drafting in the Outlook add-in — unset by default, same
# no-op-until-configured pattern as everything else in this section. This
# key belongs to the château's own Anthropic account and is separate from
# whatever credential runs the assistant that built this app.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Payments — unset until you add a real Stripe account's keys as
# environment variables. Until then, booking stays a request-only flow
# with no payment step, exactly as it's worked so far.
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Lets an external scheduler (cron, Railway Cron, cron-job.org...) trigger an
# iCal sync-all without a logged-in session. Unset by default — until you set
# it, /api/sync-ical always 404s and calendars only sync when the owner
# clicks "Sync all calendars" by hand. See DEPLOY.md.
ICAL_SYNC_TOKEN = os.environ.get("ICAL_SYNC_TOKEN", "")

# Lets an external scheduler trigger a "what needs my attention" summary
# email to the owner without a logged-in session — same pattern as
# ICAL_SYNC_TOKEN above. Unset by default — until you set it,
# /api/owner-digest always 404s. See DEPLOY.md.
DIGEST_TOKEN = os.environ.get("DIGEST_TOKEN", "")

# Lets the Outlook add-in (see "Outlook add-in" section below) look up a
# guest's bookings by email address without a logged-in session — same
# no-login-but-token-gated pattern as ICAL_SYNC_TOKEN/DIGEST_TOKEN above.
# Unset by default — until you set it, /api/guest-lookup always 404s.
GUEST_LOOKUP_TOKEN = os.environ.get("GUEST_LOOKUP_TOKEN", "")

# Lets a permanent office/wall display keep showing /admin/display without ever
# being logged in as the owner. That page auto-reloads every 60 seconds
# forever, but a normal login session expires after PERMANENT_SESSION_LIFETIME
# (12h) — without this, any kiosk left running overnight silently reloads
# into the login screen instead of the dashboard. Same token-gated,
# no-login-needed pattern as ICAL_SYNC_TOKEN/DIGEST_TOKEN above; unlike those,
# this doesn't 404 without it — /admin/display still works normally for a logged-in
# owner, the token is only an alternative way in. See DEPLOY.md.
OFFICE_DISPLAY_TOKEN = os.environ.get("OFFICE_DISPLAY_TOKEN", "")

# The background automation thread (see "Automation engine" near the bottom
# of this file) runs with no incoming request, so any email it sends that
# links back to the site (e.g. a workshop balance reminder's "manage your
# registration" link) needs to be told what the site's public URL actually
# is — a real request has a Host header for this; a timer doesn't. Set this
# to your real domain (e.g. "https://gudanes-hr.up.railway.app") once you
# have one. Until then, those links fall back to "http://localhost".
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Encrypts Management > Vault entries at rest (Fernet/AES via the
# `cryptography` library — not hand-rolled). Unset by default — until you set
# it, the Vault page shows "not configured" instead of storing anything.
# Generate one with:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Losing this key permanently loses everything stored in the vault — back it
# up somewhere separate from the database itself. See DEPLOY.md.
VAULT_ENCRYPTION_KEY = os.environ.get("VAULT_ENCRYPTION_KEY", "")


def stripe_enabled():
    return bool(STRIPE_SECRET_KEY)


def sval(obj, key, default=None):
    """Read a field off a Stripe object.

    stripe-python's StripeObject does NOT implement .get() — its __getattr__
    turns a .get("x") call into a lookup for a FIELD named "get", which
    raises AttributeError. Every payment-completion path here used .get(),
    so both the webhook and the guest's success redirect raised a 500 the
    moment a real Stripe object reached them: the guest paid, saw an error,
    and no booking was recorded. Verified directly against stripe 15.4.0.

    Bracket access works on StripeObject and on plain dicts, so this is safe
    for both (webhook payloads arrive as StripeObject, some test paths as
    dicts).
    """
    try:
        value = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return default if value is None else value


def smeta(session):
    """A Stripe session's metadata as a PLAIN dict.

    metadata comes back as a nested StripeObject, which has the same missing
    .get() problem as the session itself — and every caller here treats it
    like a dict (meta.get("kind"), meta.get("promo_code")...). Converting
    once at the boundary keeps those call sites correct and readable.

    Neither dict(obj) nor dict(obj.items()) work on a StripeObject; only
    .to_dict() does. Falls back to dict() so plain-dict payloads (tests,
    older SDKs) still work.
    """
    meta = sval(session, "metadata") or {}
    if hasattr(meta, "to_dict"):
        try:
            return meta.to_dict()
        except Exception:
            pass
    try:
        return dict(meta)
    except Exception:
        return {}

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(ROOM_PHOTO_DIR, exist_ok=True)

app = Flask(__name__)
# IMPORTANT: this secret key is regenerated every time the app starts unless
# you set a real one via the FLASK_SECRET_KEY environment variable. Set that
# in your real deployment so logged-in sessions survive restarts. See
# DEPLOY.md for exactly how.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0") == "1"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure requires HTTPS to even set the cookie — fine in the real deployment
# (always behind Nginx + Certbot per DEPLOY.md) but would silently break
# login during local dev over plain http://, so it only turns on outside
# debug mode. Defaulting DEBUG_MODE itself to off (rather than on) matters
# beyond just this cookie flag: on, it also activates Werkzeug's interactive
# debugger, which hands anyone who triggers an unhandled exception a live
# Python console — a real remote-code-execution risk if a production deploy
# ever forgets to set FLASK_DEBUG=0 explicitly. Local dev needs FLASK_DEBUG=1
# set explicitly instead (the reverse of before) to get the debugger back and
# test over plain http://.
app.config["SESSION_COOKIE_SECURE"] = not DEBUG_MODE
# Idle timeout, not a fixed session length: Flask refreshes the cookie's
# expiry on every request by default, so this logs someone out only after
# this many hours with no activity — not mid-shift just because it's been
# a while since they logged in. Matters here since the session can reach
# bank details and the password vault on a front-desk computer other staff
# might walk up to.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    # Most common real-world cause: a form left open past the session
    # timeout, or a stale tab reopened well after the last visit — not an
    # actual attack in the overwhelming majority of cases. Send people back
    # to what they were doing instead of a raw 400 page.
    flash("Your session timed out — please try that again.", "error")
    return redirect(request.referrer or "/")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('owner','employee')),
            name TEXT NOT NULL,
            job_role TEXT,
            phone TEXT,
            start_date TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
            pay_rate TEXT,
            pay_type TEXT,
            notes TEXT,
            account_claimed INTEGER NOT NULL DEFAULT 1,
            invite_token TEXT,
            created_at TEXT NOT NULL,
            skills TEXT,
            emergency_contact_name TEXT,
            emergency_contact_phone TEXT,
            emergency_contact_relationship TEXT,
            reset_token TEXT,
            reset_token_expires_at TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            expiry_date TEXT
        );

        CREATE TABLE IF NOT EXISTS shopping_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            store TEXT,
            bought INTEGER NOT NULL DEFAULT 0,
            added_by_user_id INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            bought_at TEXT
        );

        CREATE TABLE IF NOT EXISTS breakfast_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            low_stock INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS breakfast_checklist_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES breakfast_items(id) ON DELETE CASCADE,
            checklist_date TEXT NOT NULL,
            checked_by_user_id INTEGER REFERENCES users(id),
            checked_at TEXT NOT NULL,
            UNIQUE(item_id, checklist_date)
        );

        CREATE TABLE IF NOT EXISTS login_throttle (
            ip_address TEXT PRIMARY KEY,
            failed_count INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT
        );

        CREATE TABLE IF NOT EXISTS submission_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            link TEXT,
            related_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
            read_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hr_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','handled')),
            created_at TEXT NOT NULL,
            handled_at TEXT,
            response TEXT,
            responded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS check_in_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pay_rate_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            old_pay_rate TEXT,
            new_pay_rate TEXT,
            old_pay_type TEXT,
            new_pay_type TEXT,
            changed_by_user_id INTEGER REFERENCES users(id),
            changed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS onboarding_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            role_applied TEXT,
            status TEXT NOT NULL DEFAULT 'new'
                CHECK(status IN ('new','interviewing','offered','hired','rejected')),
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS equipment_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            notes TEXT,
            issued_at TEXT NOT NULL,
            returned_at TEXT
        );

        CREATE TABLE IF NOT EXISTS offboarding_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            clock_in_at TEXT NOT NULL,
            clock_out_at TEXT,
            auto_closed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS breaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_entry_id INTEGER NOT NULL REFERENCES time_entries(id) ON DELETE CASCADE,
            start_at TEXT NOT NULL,
            end_at TEXT
        );

        CREATE TABLE IF NOT EXISTS company_info (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            legal_name TEXT,
            registration_number TEXT,
            vat_number TEXT,
            registered_address TEXT,
            incorporation_date TEXT,
            accountant_name TEXT,
            accountant_phone TEXT,
            accountant_email TEXT,
            insurance_broker_name TEXT,
            insurance_broker_phone TEXT,
            insurance_broker_email TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS bank_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            bank_name TEXT,
            account_holder TEXT,
            currency TEXT,
            notes TEXT,
            sensitive_encrypted TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS recurring_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            frequency TEXT NOT NULL DEFAULT 'monthly' CHECK(frequency IN ('monthly','annual')),
            category TEXT,
            next_due_date TEXT,
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS waitlist_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            desired_arrival TEXT,
            desired_departure TEXT,
            party_size INTEGER,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','contacted','booked','closed')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_booking_id INTEGER REFERENCES workshop_bookings(id) ON DELETE SET NULL,
            guest_name TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            featured INTEGER NOT NULL DEFAULT 0,
            submitted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS guest_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL,
            guest_name TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            submitted_at TEXT NOT NULL
        );

        -- Every refund ever issued, across all four booking types. The house
        -- policy is non-refundable, so refunds are always a deliberate,
        -- case-by-case decision by the owner -- which is exactly why each one
        -- needs a reason and an attributable name against it.
        --
        -- booking_id is deliberately not a foreign key: it points into one of
        -- four different tables depending on `category`, which SQLite can't
        -- express. Look it up via the category.
        CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL CHECK(category IN ('room','restaurant','workshop','event')),
            booking_id INTEGER NOT NULL,
            reference_code TEXT,
            guest_name TEXT,
            guest_email TEXT,
            amount REAL NOT NULL,
            reason TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'stripe'
                CHECK(method IN ('stripe','bank_transfer','cash','other')),
            stripe_refund_id TEXT,
            refunded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );

        -- Training, tickets and certificates a staff member holds. Separate
        -- from `documents` (which is a filing cabinet): these have an issuer
        -- and expire, and in French hospitality some of them are legally
        -- required to be current -- food hygiene for anyone near the kitchen,
        -- first aid, a licence for whoever drives the guest transfers.
        -- One row per monitored inbox. The château runs an inbox per business
        -- area and that list grows, so this is a table rather than a fixed
        -- list in code: the owner can add an inbox and point it at whoever
        -- handles that area, and flagged mail routes itself on arrival
        -- instead of waiting to be triaged by hand.
        -- How long each kind of HR item may sit before someone is chased, and
        -- how long before it goes over their head. One row per item type,
        -- editable by the owner -- an unsigned contract is not the same
        -- urgency as an expired food-hygiene certificate.
        CREATE TABLE IF NOT EXISTS hr_escalation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL UNIQUE,
            sla_days REAL NOT NULL DEFAULT 3,
            escalate_after_days REAL NOT NULL DEFAULT 7,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        -- The state of a single outstanding HR item as it ages. Separate from
        -- the source tables so nothing has to grow escalation columns, and so
        -- a reminder fires once rather than every time the job runs.
        CREATE TABLE IF NOT EXISTS hr_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            subject_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            summary TEXT,
            due_at TEXT,
            first_seen_at TEXT NOT NULL,
            reminded_at TEXT,
            escalated_at TEXT,
            resolved_at TEXT,
            UNIQUE(item_type, item_id)
        );

        CREATE TABLE IF NOT EXISTS mailbox_routing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mailbox TEXT NOT NULL UNIQUE,
            label TEXT,
            default_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            route_to_on_shift INTEGER NOT NULL DEFAULT 0,
            escalate_hours REAL NOT NULL DEFAULT 48,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS certifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            issuer TEXT,
            reference TEXT,
            issued_date TEXT,
            expiry_date TEXT,
            required INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        -- When someone is normally free to work. Recurring weekday pattern;
        -- one row per weekday they've said something about. Absence of a row
        -- means "no preference stated", not "unavailable".
        CREATE TABLE IF NOT EXISTS availability_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
            available INTEGER NOT NULL DEFAULT 1,
            from_time TEXT,
            to_time TEXT,
            note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, weekday)
        );

        -- One-off exceptions to the pattern above: "can't do the 14th",
        -- or "actually free that Sunday".
        CREATE TABLE IF NOT EXISTS availability_exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            on_date TEXT NOT NULL,
            available INTEGER NOT NULL DEFAULT 0,
            note TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, on_date)
        );

        -- Unplanned absence, deliberately NOT the same thing as booked leave:
        -- annual leave is requested and approved in advance, this is what
        -- actually happened on the day. Kept apart so a sickness pattern
        -- doesn't hide inside holiday.
        CREATE TABLE IF NOT EXISTS absences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'sick'
                CHECK(kind IN ('sick','emergency','unpaid','unauthorised','other')),
            reason TEXT,
            self_certified INTEGER NOT NULL DEFAULT 1,
            doctor_note_filename TEXT,
            return_to_work_note TEXT,
            return_to_work_done_at TEXT,
            recorded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );

        -- Structured review, building on the informal check-in notes. The
        -- acknowledgement step matters: it is the record that the employee
        -- actually saw what was written about them.
        CREATE TABLE IF NOT EXISTS performance_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reviewer_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            review_date TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            overall_rating INTEGER CHECK(overall_rating BETWEEN 1 AND 5),
            strengths TEXT,
            improvements TEXT,
            goals TEXT,
            employee_comments TEXT,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','shared','acknowledged')),
            shared_at TEXT,
            acknowledged_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL,
            target TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_person TEXT,
            phone TEXT,
            email TEXT,
            payment_terms TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS insurance_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            policy_number TEXT,
            coverage_type TEXT,
            premium REAL,
            premium_frequency TEXT DEFAULT 'annual' CHECK(premium_frequency IN ('monthly','annual')),
            expiry_date TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            vehicle_type TEXT,
            fuel_type TEXT,
            license_plate TEXT,
            cleanliness TEXT NOT NULL DEFAULT 'clean' CHECK(cleanliness IN ('clean','dirty')),
            fuel_level TEXT NOT NULL DEFAULT 'ok' CHECK(fuel_level IN ('ok','low')),
            next_service_due TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vehicle_maintenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            reported_by_user_id INTEGER REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS vehicle_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id),
            purpose TEXT,
            checked_out_at TEXT NOT NULL,
            checked_in_at TEXT
        );

        CREATE TABLE IF NOT EXISTS vehicle_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            guest_name TEXT,
            direction TEXT CHECK(direction IN ('pickup','dropoff')),
            scheduled_at TEXT NOT NULL,
            driver_user_id INTEGER REFERENCES users(id),
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS company_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            uploaded_by_user_id INTEGER REFERENCES users(id),
            uploaded_at TEXT NOT NULL,
            expiry_date TEXT
        );

        CREATE TABLE IF NOT EXISTS vault_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            username TEXT,
            url TEXT,
            secret_encrypted TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by_user_id INTEGER REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS manual_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS manual_acknowledgments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            acknowledged_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT,
            phone TEXT,
            notes TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posted_by_user_id INTEGER REFERENCES users(id),
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            expires_date TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS social_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'Instagram',
            caption TEXT NOT NULL DEFAULT '',
            post_type TEXT,
            scheduled_date TEXT,
            scheduled_time TEXT,
            status TEXT NOT NULL DEFAULT 'idea' CHECK(status IN ('idea','drafted','scheduled','posted')),
            assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            link TEXT,
            notes TEXT,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            posted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS room_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            reported_by_user_id INTEGER REFERENCES users(id),
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        -- A per-PERSON guest profile, NOT a per-stay record. Stay dates,
        -- party size and room live on `bookings`, which is the single source
        -- of truth for who is actually in residence; this table holds only
        -- what stays true between visits.
        CREATE TABLE IF NOT EXISTS guests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            dietary_notes TEXT,
            preferences TEXT,
            vip INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('supplier_invoice','staff_expense')),
            submitted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            vendor_name TEXT,
            description TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            filename TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','paid')),
            owner_note TEXT,
            submitted_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            instructor_name TEXT,
            instructor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            price_per_person REAL NOT NULL DEFAULT 0,
            default_capacity INTEGER NOT NULL DEFAULT 10,
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            photo_filename TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_id INTEGER NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            capacity INTEGER NOT NULL DEFAULT 10,
            notes TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES workshop_sessions(id) ON DELETE CASCADE,
            reference_code TEXT UNIQUE NOT NULL,
            manage_token TEXT UNIQUE NOT NULL,
            guest_name TEXT NOT NULL,
            guest_email TEXT NOT NULL,
            guest_phone TEXT,
            party_size INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','declined','cancelled')),
            total_price REAL,
            booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS workshop_booking_guests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_booking_id INTEGER NOT NULL REFERENCES workshop_bookings(id) ON DELETE CASCADE,
            guest_name TEXT NOT NULL,
            is_lead INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_booking_id INTEGER NOT NULL REFERENCES workshop_bookings(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('charge','discount','payment','refund')),
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_custom_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_id INTEGER NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            field_type TEXT NOT NULL DEFAULT 'text' CHECK(field_type IN ('text','textarea','select','checkbox')),
            options TEXT,
            required INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_custom_field_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_booking_id INTEGER NOT NULL REFERENCES workshop_bookings(id) ON DELETE CASCADE,
            custom_field_id INTEGER NOT NULL REFERENCES workshop_custom_fields(id) ON DELETE CASCADE,
            value TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS workshop_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workshop_booking_id INTEGER NOT NULL REFERENCES workshop_bookings(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS email_templates (
            template_key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            updated_at TEXT
        );

        -- Owner-written email templates for campaigns and announcements, as
        -- opposed to `email_templates` above which holds the fixed system
        -- messages (booking confirmed, balance due). These are created and
        -- named by the owner, grouped by area, and reused.
        CREATE TABLE IF NOT EXISTS campaign_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            area TEXT NOT NULL DEFAULT 'general',
            category TEXT,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            -- Optional automation: send this many days before/after a guest's
            -- arrival or departure instead of being sent by hand.
            trigger_event TEXT CHECK(trigger_event IN ('arrival','departure','workshop_start')),
            trigger_offset_days INTEGER,
            trigger_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );

        -- Every campaign send, one row per recipient. This is the record of
        -- who was emailed what, and the thing that stops an automated
        -- template mailing the same guest the same message twice.
        CREATE TABLE IF NOT EXISTS campaign_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER REFERENCES campaign_templates(id) ON DELETE SET NULL,
            template_name TEXT,
            recipient_email TEXT NOT NULL,
            recipient_name TEXT,
            subject TEXT,
            status TEXT NOT NULL DEFAULT 'sent' CHECK(status IN ('sent','failed','skipped','test')),
            detail TEXT,
            dedupe_key TEXT,
            sent_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );

        -- Guests who have asked not to receive campaign email. Checked on
        -- every bulk and automated send; transactional mail (their own
        -- booking confirmation) is unaffected.
        -- Workplace accidents and guest incidents in one register. A French
        -- employer must keep a record of workplace accidents including minor
        -- ones; guest incidents are kept alongside because the follow-up (and
        -- often the insurer) is the same. `kind` separates them for reporting.
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL DEFAULT 'workplace'
                CHECK(kind IN ('workplace','guest','property','near_miss')),
            occurred_at TEXT NOT NULL,
            location TEXT,
            summary TEXT NOT NULL,
            detail TEXT,
            severity TEXT NOT NULL DEFAULT 'minor'
                CHECK(severity IN ('near_miss','minor','significant','serious')),
            affected_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            affected_person TEXT,
            reported_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            witnesses TEXT,
            first_aid_given INTEGER NOT NULL DEFAULT 0,
            medical_attention INTEGER NOT NULL DEFAULT 0,
            work_days_lost INTEGER,
            insurance_policy_id INTEGER REFERENCES insurance_policies(id) ON DELETE SET NULL,
            reported_to_insurer_at TEXT,
            action_taken TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','actioned','closed')),
            closed_at TEXT,
            created_at TEXT NOT NULL
        );

        -- Who holds which key, gate code or alarm PIN. Offboarding needs to be
        -- able to ask "what does this person actually hold" rather than relying
        -- on a generic checklist item that says "return keys".
        CREATE TABLE IF NOT EXISTS access_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'key'
                CHECK(kind IN ('key','code','alarm','fob','vehicle_key','other')),
            location TEXT,
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        -- One row per issue. Returning sets returned_at rather than deleting,
        -- so "who had the cellar key in August" is still answerable later.
        CREATE TABLE IF NOT EXISTS access_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            access_item_id INTEGER NOT NULL REFERENCES access_items(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            holder_name TEXT,
            issued_at TEXT NOT NULL,
            issued_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            returned_at TEXT,
            notes TEXT
        );

        -- What a job role REQUIRES. Certifications and documents were tracked
        -- per person with no notion of what the role demands, so "who cannot
        -- legally work this week" was never answerable — only "what expires".
        CREATE TABLE IF NOT EXISTS role_requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_role TEXT NOT NULL,
            requirement TEXT NOT NULL,
            requirement_type TEXT NOT NULL DEFAULT 'certification'
                CHECK(requirement_type IN ('certification','document')),
            notes TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(job_role, requirement, requirement_type)
        );

        CREATE TABLE IF NOT EXISTS email_optouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            reason TEXT,
            created_at TEXT NOT NULL
        );

        -- Mail that could not be sent, kept so it can go out later.
        --
        -- Without this every undelivered message vanished into a console
        -- print: a guest could pay for a stay and receive nothing, and there
        -- was no record that a confirmation was ever owed. That is the worst
        -- shape a failure can take here, because the guest has already paid
        -- and neither side knows anything is missing.
        --
        -- Password resets and staff invitations are deliberately NOT stored
        -- (see send_email): their body is a working credential, and one that
        -- expires — retrying a stale reset link days later is useless, while
        -- keeping it in a table is a real exposure.
        CREATE TABLE IF NOT EXISTS email_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_address TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            ics_content TEXT,
            ics_filename TEXT,
            reason TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            sent_at TEXT
        );

        CREATE TABLE IF NOT EXISTS workshop_waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES workshop_sessions(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            party_size INTEGER,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','contacted','booked','closed')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_inquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_code TEXT UNIQUE NOT NULL,
            manage_token TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            contact_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            contact_phone TEXT,
            preferred_date TEXT,
            alternate_date TEXT,
            guest_count INTEGER,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','contacted','quoted','confirmed','declined','cancelled')),
            quoted_price REAL,
            owner_note TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS email_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graph_message_id TEXT UNIQUE NOT NULL,
            conversation_id TEXT,
            from_name TEXT,
            from_address TEXT,
            subject TEXT,
            preview TEXT,
            web_link TEXT,
            received_at TEXT NOT NULL,
            unanswered INTEGER NOT NULL DEFAULT 0,
            price_conflict INTEGER NOT NULL DEFAULT 0,
            availability_conflict INTEGER NOT NULL DEFAULT 0,
            conflict_category TEXT,
            extracted_price REAL,
            computed_price REAL,
            extracted_dates TEXT,
            detail_note TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
            resolved_at TEXT,
            resolved_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            description TEXT,
            discount_type TEXT NOT NULL DEFAULT 'percent' CHECK(discount_type IN ('percent','fixed')),
            discount_value REAL NOT NULL,
            max_discount_amount REAL,
            applies_to TEXT NOT NULL DEFAULT 'all' CHECK(applies_to IN ('all','room','restaurant','workshop')),
            min_spend REAL,
            max_redemptions INTEGER,
            redemption_count INTEGER NOT NULL DEFAULT 0,
            valid_from TEXT,
            valid_until TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS promo_code_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
            category TEXT NOT NULL CHECK(category IN ('room','restaurant','workshop')),
            booking_reference TEXT,
            guest_email TEXT,
            original_amount REAL,
            discount_amount REAL,
            final_amount REAL,
            redeemed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL DEFAULT 'main' CHECK(category IN ('starter','main','dessert','drink')),
            dietary_tags TEXT,
            price REAL,
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS restaurant_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            opening_date TEXT,
            dinner_time TEXT NOT NULL DEFAULT '19:30',
            capacity INTEGER NOT NULL DEFAULT 20,
            price_per_person REAL,
            lead_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            profit_share_percent REAL NOT NULL DEFAULT 50,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS restaurant_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            dinner_date TEXT NOT NULL,
            role_note TEXT,
            estimated_hours REAL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS restaurant_waitlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            desired_date TEXT,
            party_size INTEGER,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','contacted','booked','closed')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS restaurant_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference_code TEXT UNIQUE NOT NULL,
            manage_token TEXT UNIQUE NOT NULL,
            guest_name TEXT NOT NULL,
            guest_email TEXT NOT NULL,
            guest_phone TEXT,
            party_size INTEGER NOT NULL DEFAULT 2,
            dinner_date TEXT NOT NULL,
            dietary_notes TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','declined','cancelled')),
            total_price REAL,
            booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS extras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS automation_runs (
            job_name TEXT PRIMARY KEY,
            last_ran_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            notes TEXT,
            room_note TEXT,
            due_date TEXT,
            priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','done')),
            created_at TEXT NOT NULL,
            completed_at TEXT,
            repeat_weekly INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            max_occupancy INTEGER NOT NULL DEFAULT 2,
            price_per_night REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            export_token TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            photo_filename TEXT,
            amenities TEXT
        );

        CREATE TABLE IF NOT EXISTS room_rate_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            price_per_night REAL NOT NULL,
            label TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS deposit_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL CHECK(category IN ('restaurant','workshop')),
            start_date TEXT,
            end_date TEXT,
            min_party_size INTEGER,
            deposit_percent REAL NOT NULL,
            label TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS restaurant_rate_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            price_per_person REAL NOT NULL,
            label TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS room_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ical_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            url TEXT NOT NULL,
            last_synced_at TEXT,
            last_sync_error TEXT
        );

        CREATE TABLE IF NOT EXISTS blocked_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            ical_source_id INTEGER REFERENCES ical_sources(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            shift_date TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            role_note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS timesheet_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time_entry_id INTEGER NOT NULL REFERENCES time_entries(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            note TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','resolved','dismissed')),
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS shift_swaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            requested_by_user_id INTEGER NOT NULL REFERENCES users(id),
            offered_to_user_id INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','declined','approved','rejected')),
            note TEXT,
            requested_at TEXT NOT NULL,
            responded_at TEXT,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            reason TEXT,
            leave_type TEXT NOT NULL DEFAULT 'vacation',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined','cancelled')),
            owner_note TEXT,
            requested_at TEXT NOT NULL,
            decided_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ical_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ical_source_id INTEGER NOT NULL REFERENCES ical_sources(id) ON DELETE CASCADE,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            ran_at TEXT NOT NULL,
            success INTEGER NOT NULL,
            added INTEGER NOT NULL DEFAULT 0,
            removed INTEGER NOT NULL DEFAULT 0,
            unchanged INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS room_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            reference_code TEXT UNIQUE NOT NULL,
            manage_token TEXT UNIQUE NOT NULL,
            guest_name TEXT NOT NULL,
            guest_email TEXT NOT NULL,
            guest_phone TEXT,
            arrival_date TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            party_size INTEGER NOT NULL DEFAULT 1,
            special_requests TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed','declined','cancelled')),
            total_price REAL,
            owner_note TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            linked_guest_id INTEGER REFERENCES guests(id) ON DELETE SET NULL,
            checked_out_at TEXT,
            extras_summary TEXT,
            payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK(payment_status IN ('unpaid','paid','refunded')),
            stripe_session_id TEXT,
            stripe_payment_intent_id TEXT
        );
        """
    )
    conn.commit()

    # Migration for databases created before onboarding-link support existed.
    for column, ddl in (
        ("account_claimed", "ALTER TABLE users ADD COLUMN account_claimed INTEGER NOT NULL DEFAULT 1"),
        ("invite_token", "ALTER TABLE users ADD COLUMN invite_token TEXT"),
        ("photo_filename", "ALTER TABLE rooms ADD COLUMN photo_filename TEXT"),
        ("checked_out_at", "ALTER TABLE bookings ADD COLUMN checked_out_at TEXT"),
        ("amenities", "ALTER TABLE rooms ADD COLUMN amenities TEXT"),
        ("extras_summary", "ALTER TABLE bookings ADD COLUMN extras_summary TEXT"),
        ("payment_status", "ALTER TABLE bookings ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid'"),
        ("stripe_session_id", "ALTER TABLE bookings ADD COLUMN stripe_session_id TEXT"),
        ("stripe_payment_intent_id", "ALTER TABLE bookings ADD COLUMN stripe_payment_intent_id TEXT"),
        ("repeat_weekly", "ALTER TABLE tasks ADD COLUMN repeat_weekly INTEGER NOT NULL DEFAULT 0"),
        ("expiry_date_docs", "ALTER TABLE documents ADD COLUMN expiry_date TEXT"),
        ("expiry_date_company_docs", "ALTER TABLE company_documents ADD COLUMN expiry_date TEXT"),
        ("skills", "ALTER TABLE users ADD COLUMN skills TEXT"),
        ("emergency_contact_name", "ALTER TABLE users ADD COLUMN emergency_contact_name TEXT"),
        ("emergency_contact_phone", "ALTER TABLE users ADD COLUMN emergency_contact_phone TEXT"),
        ("emergency_contact_relationship", "ALTER TABLE users ADD COLUMN emergency_contact_relationship TEXT"),
        ("reset_token", "ALTER TABLE users ADD COLUMN reset_token TEXT"),
        ("reset_token_expires_at", "ALTER TABLE users ADD COLUMN reset_token_expires_at TEXT"),
        ("annual_leave_days", "ALTER TABLE users ADD COLUMN annual_leave_days INTEGER"),
        ("reason_for_leaving", "ALTER TABLE users ADD COLUMN reason_for_leaving TEXT"),
        ("arrival_prepped_at", "ALTER TABLE bookings ADD COLUMN arrival_prepped_at TEXT"),
        ("tasks_acknowledgment_status", "ALTER TABLE tasks ADD COLUMN acknowledgment_status TEXT"),
        ("tasks_directed_at", "ALTER TABLE tasks ADD COLUMN directed_at TEXT"),
        ("tasks_directed_by_user_id", "ALTER TABLE tasks ADD COLUMN directed_by_user_id INTEGER"),
        ("tasks_origin", "ALTER TABLE tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'"),
        ("insurance_vehicle_id", "ALTER TABLE insurance_policies ADD COLUMN vehicle_id INTEGER"),
        ("expenses_vehicle_id", "ALTER TABLE expenses ADD COLUMN vehicle_id INTEGER"),
        ("bookings_transfer_flight_number", "ALTER TABLE bookings ADD COLUMN transfer_flight_number TEXT"),
        ("bookings_transfer_arrival_time", "ALTER TABLE bookings ADD COLUMN transfer_arrival_time TEXT"),
        ("bookings_transfer_notes", "ALTER TABLE bookings ADD COLUMN transfer_notes TEXT"),
        ("bookings_estimated_arrival_time", "ALTER TABLE bookings ADD COLUMN estimated_arrival_time TEXT"),
        ("tasks_booking_id", "ALTER TABLE tasks ADD COLUMN booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL"),
        ("expenses_restaurant_related", "ALTER TABLE expenses ADD COLUMN restaurant_related INTEGER NOT NULL DEFAULT 0"),
        ("workshops_deposit_percent", "ALTER TABLE workshops ADD COLUMN deposit_percent INTEGER NOT NULL DEFAULT 30"),
        ("workshops_inclusions", "ALTER TABLE workshops ADD COLUMN inclusions TEXT"),
        ("workshop_bookings_occupancy_type", "ALTER TABLE workshop_bookings ADD COLUMN occupancy_type TEXT NOT NULL DEFAULT 'double'"),
        ("workshop_bookings_requested_roommate", "ALTER TABLE workshop_bookings ADD COLUMN requested_roommate TEXT"),
        ("workshop_bookings_dietary_notes", "ALTER TABLE workshop_bookings ADD COLUMN dietary_notes TEXT"),
        ("workshop_bookings_medical_notes", "ALTER TABLE workshop_bookings ADD COLUMN medical_notes TEXT"),
        ("workshop_bookings_special_occasion", "ALTER TABLE workshop_bookings ADD COLUMN special_occasion TEXT"),
        ("workshop_bookings_deposit_amount", "ALTER TABLE workshop_bookings ADD COLUMN deposit_amount REAL"),
        ("workshop_bookings_deposit_paid_at", "ALTER TABLE workshop_bookings ADD COLUMN deposit_paid_at TEXT"),
        ("workshop_bookings_deposit_stripe_session_id", "ALTER TABLE workshop_bookings ADD COLUMN deposit_stripe_session_id TEXT"),
        ("workshop_bookings_balance_amount", "ALTER TABLE workshop_bookings ADD COLUMN balance_amount REAL"),
        ("workshop_bookings_balance_due_date", "ALTER TABLE workshop_bookings ADD COLUMN balance_due_date TEXT"),
        ("workshop_bookings_balance_paid_at", "ALTER TABLE workshop_bookings ADD COLUMN balance_paid_at TEXT"),
        ("workshop_bookings_balance_stripe_session_id", "ALTER TABLE workshop_bookings ADD COLUMN balance_stripe_session_id TEXT"),
        ("workshop_bookings_assigned_room_id", "ALTER TABLE workshop_bookings ADD COLUMN assigned_room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL"),
        ("workshop_bookings_do_not_email", "ALTER TABLE workshop_bookings ADD COLUMN do_not_email INTEGER NOT NULL DEFAULT 0"),
        ("restaurant_bookings_payment_status", "ALTER TABLE restaurant_bookings ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid'"),
        ("restaurant_bookings_stripe_session_id", "ALTER TABLE restaurant_bookings ADD COLUMN stripe_session_id TEXT"),
        ("restaurant_bookings_stripe_payment_intent_id", "ALTER TABLE restaurant_bookings ADD COLUMN stripe_payment_intent_id TEXT"),
        ("workshop_bookings_balance_reminder_sent_at", "ALTER TABLE workshop_bookings ADD COLUMN balance_reminder_sent_at TEXT"),
        ("restaurant_bookings_no_show_at", "ALTER TABLE restaurant_bookings ADD COLUMN no_show_at TEXT"),
        ("workshop_bookings_feedback_requested_at", "ALTER TABLE workshop_bookings ADD COLUMN feedback_requested_at TEXT"),
        ("guest_feedback_featured", "ALTER TABLE guest_feedback ADD COLUMN featured INTEGER NOT NULL DEFAULT 0"),
        ("workshops_itinerary", "ALTER TABLE workshops ADD COLUMN itinerary TEXT"),
        ("rooms_min_nights", "ALTER TABLE rooms ADD COLUMN min_nights INTEGER NOT NULL DEFAULT 1"),
        ("bookings_promo_code_id", "ALTER TABLE bookings ADD COLUMN promo_code_id INTEGER REFERENCES promo_codes(id) ON DELETE SET NULL"),
        ("bookings_discount_amount", "ALTER TABLE bookings ADD COLUMN discount_amount REAL"),
        ("restaurant_bookings_promo_code_id", "ALTER TABLE restaurant_bookings ADD COLUMN promo_code_id INTEGER REFERENCES promo_codes(id) ON DELETE SET NULL"),
        ("restaurant_bookings_discount_amount", "ALTER TABLE restaurant_bookings ADD COLUMN discount_amount REAL"),
        ("workshop_bookings_promo_code_id", "ALTER TABLE workshop_bookings ADD COLUMN promo_code_id INTEGER REFERENCES promo_codes(id) ON DELETE SET NULL"),
        ("workshop_bookings_discount_amount", "ALTER TABLE workshop_bookings ADD COLUMN discount_amount REAL"),
        ("restaurant_settings_deposit_percent", "ALTER TABLE restaurant_settings ADD COLUMN deposit_percent REAL"),
        ("restaurant_bookings_deposit_amount", "ALTER TABLE restaurant_bookings ADD COLUMN deposit_amount REAL"),
        ("hr_notes_response", "ALTER TABLE hr_notes ADD COLUMN response TEXT"),
        ("hr_notes_responded_at", "ALTER TABLE hr_notes ADD COLUMN responded_at TEXT"),
        ("email_flags_assigned_to_user_id", "ALTER TABLE email_flags ADD COLUMN assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL"),
        ("email_flags_reply_price_conflict", "ALTER TABLE email_flags ADD COLUMN reply_price_conflict INTEGER NOT NULL DEFAULT 0"),
        ("email_flags_reply_availability_conflict", "ALTER TABLE email_flags ADD COLUMN reply_availability_conflict INTEGER NOT NULL DEFAULT 0"),
        ("email_flags_reply_detail_note", "ALTER TABLE email_flags ADD COLUMN reply_detail_note TEXT"),
        ("email_flags_last_reply_checked_id", "ALTER TABLE email_flags ADD COLUMN last_reply_checked_id TEXT"),
        ("email_flags_mailbox", "ALTER TABLE email_flags ADD COLUMN mailbox TEXT"),
        ("email_flags_first_reply_at", "ALTER TABLE email_flags ADD COLUMN first_reply_at TEXT"),
        ("mailbox_routing_on_shift", "ALTER TABLE mailbox_routing ADD COLUMN route_to_on_shift INTEGER NOT NULL DEFAULT 0"),
        ("email_flags_escalated_at", "ALTER TABLE email_flags ADD COLUMN escalated_at TEXT"),
        ("mailbox_routing_escalate_hours", "ALTER TABLE mailbox_routing ADD COLUMN escalate_hours REAL NOT NULL DEFAULT 48"),
        # `guests` became a per-person profile rather than a per-stay register
        # (see the drop-column migration below for why).
        ("guests_email", "ALTER TABLE guests ADD COLUMN email TEXT"),
        ("guests_phone", "ALTER TABLE guests ADD COLUMN phone TEXT"),
        ("guests_dietary_notes", "ALTER TABLE guests ADD COLUMN dietary_notes TEXT"),
        ("guests_preferences", "ALTER TABLE guests ADD COLUMN preferences TEXT"),
        ("guests_vip", "ALTER TABLE guests ADD COLUMN vip INTEGER NOT NULL DEFAULT 0"),
        # A wedding runs across days; preferred_date alone couldn't express that.
        ("event_end_date", "ALTER TABLE event_inquiries ADD COLUMN end_date TEXT"),
        ("event_spaces", "ALTER TABLE event_inquiries ADD COLUMN spaces TEXT"),
        # Per-send unsubscribe key. Stored on the send rather than derived from
        # the address so the link carries no email in the URL and can't be
        # guessed for someone else, and so it keeps working across restarts
        # (a secret-key HMAC would not, since FLASK_SECRET_KEY is random when
        # unset).
        ("campaign_sends_unsubscribe_token", "ALTER TABLE campaign_sends ADD COLUMN unsubscribe_token TEXT"),
        # Employment terms. `start_date` alone couldn't express a fixed-term
        # contract or a trial period, and both carry hard deadlines in France:
        # a trial period that lapses un-actioned confirms the employee, and a
        # CDD left running past its end date becomes a CDI.
        ("users_contract_type", "ALTER TABLE users ADD COLUMN contract_type TEXT"),
        ("users_contract_end_date", "ALTER TABLE users ADD COLUMN contract_end_date TEXT"),
        ("users_trial_end_date", "ALTER TABLE users ADD COLUMN trial_end_date TEXT"),
        ("users_notice_period_days", "ALTER TABLE users ADD COLUMN notice_period_days INTEGER"),
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    # `guests` used to be a per-STAY register carrying arrival/departure/party_size,
    # duplicating what `bookings` already owns. The two could never be kept in
    # agreement: confirming a booking created a guest row, but cancelling one
    # never removed it, so a cancelled guest showed as "in residence" forever.
    # `guests` is now a per-PERSON profile (name/email/phone/dietary/preferences/
    # VIP) and `bookings` is the single source of truth for who is actually here,
    # which makes that ghost structurally impossible rather than patched.
    #
    # Uses ALTER TABLE DROP COLUMN (SQLite 3.35+) rather than the rename/recreate/
    # copy/drop dance used for `tasks` below -- `bookings.linked_guest_id` has a
    # foreign key onto this table, and a rename is exactly what silently broke an
    # FK reference elsewhere in this schema.
    guest_columns = {row["name"] for row in conn.execute("PRAGMA table_info(guests)").fetchall()}
    if "arrival_date" in guest_columns:
        # Fold the retired stay fields into the profile note so nothing the owner
        # typed is silently destroyed, then drop them.
        for row in conn.execute(
            "SELECT id, notes, arrival_date, departure_date, party_size FROM guests"
        ).fetchall():
            if not (row["arrival_date"] or row["departure_date"] or row["party_size"]):
                continue
            stay = f"{row['arrival_date'] or '?'} to {row['departure_date'] or '?'}"
            if row["party_size"]:
                stay += f", party of {row['party_size']}"
            merged = ((row["notes"] or "").strip() + f"\n[Former register entry: {stay}]").strip()
            conn.execute("UPDATE guests SET notes = ? WHERE id = ?", (merged, row["id"]))
        # Carry each profile's email over from any booking already linked to it,
        # lower-cased so 'Marie@x.com' and 'marie@x.com' can never become two
        # profiles for one person.
        conn.execute(
            """UPDATE guests SET email = (
                   SELECT LOWER(TRIM(b.guest_email)) FROM bookings b
                   WHERE b.linked_guest_id = guests.id
                     AND b.guest_email IS NOT NULL AND TRIM(b.guest_email) != ''
                   ORDER BY b.id DESC LIMIT 1
               ) WHERE email IS NULL"""
        )
        for ddl in (
            "ALTER TABLE guests DROP COLUMN arrival_date",
            "ALTER TABLE guests DROP COLUMN departure_date",
            "ALTER TABLE guests DROP COLUMN party_size",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.commit()

    # Normalise every stored email before the unique index goes on, and treat a
    # blank string as "no email" so it can't masquerade as a distinct value.
    conn.execute("UPDATE guests SET email = LOWER(TRIM(email)) WHERE email IS NOT NULL")
    conn.execute("UPDATE guests SET email = NULL WHERE TRIM(COALESCE(email, '')) = ''")
    conn.commit()

    # Merge duplicate-email profiles before the unique index is created.
    # Under the old per-stay model a RETURNING guest had one row per visit, all
    # carrying the same email once backfilled -- i.e. duplicates are guaranteed
    # for exactly the people this refactor exists to serve. Creating the index
    # first raises IntegrityError (NOT OperationalError, so nothing below would
    # catch it), init_db() propagates, and the app fails to boot. It also could
    # not self-heal, because the DROP COLUMNs above commit first, so the next
    # start skips the whole block and fails identically forever.
    for dup in conn.execute(
        """SELECT email, MIN(id) AS keep_id, COUNT(*) AS c FROM guests
           WHERE email IS NOT NULL GROUP BY email HAVING c > 1"""
    ).fetchall():
        others = [r["id"] for r in conn.execute(
            "SELECT id FROM guests WHERE email = ? AND id != ?", (dup["email"], dup["keep_id"])
        ).fetchall()]
        for other_id in others:
            # Point that person's bookings at the surviving profile, and keep
            # any notes rather than dropping what the owner typed.
            conn.execute(
                "UPDATE bookings SET linked_guest_id = ? WHERE linked_guest_id = ?",
                (dup["keep_id"], other_id),
            )
            extra = conn.execute("SELECT notes FROM guests WHERE id = ?", (other_id,)).fetchone()
            if extra and (extra["notes"] or "").strip():
                conn.execute(
                    "UPDATE guests SET notes = TRIM(COALESCE(notes, '') || char(10) || ?) WHERE id = ?",
                    (extra["notes"].strip(), dup["keep_id"]),
                )
            conn.execute("DELETE FROM guests WHERE id = ?", (other_id,))
    conn.commit()

    # One profile per email address, so confirming a booking can find-or-create
    # rather than piling up a new row per stay. Partial index: profiles added by
    # hand may legitimately have no email yet. NOCASE belt-and-braces on top of
    # the normalisation above. Guarded so a startup can never be blocked by it.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_guests_email "
            "ON guests(email COLLATE NOCASE) WHERE email IS NOT NULL"
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"[init_db] could not create unique guest-email index: {e}")

    # One paid Stripe Checkout session must never produce two bookings.
    #
    # Both the guest's success redirect and Stripe's webhook create the
    # booking — by design, so the booking still lands if the guest closes the
    # browser. Each did SELECT-then-INSERT on stripe_session_id, which is a
    # race: the two fire at essentially the same moment, both can find nothing,
    # and both insert. Verified against this schema — two bookings sharing one
    # session id were accepted. That is one payment, two bookings, and a room
    # consumed twice. Stripe also retries webhooks, which widens the window.
    #
    # A partial unique index makes it impossible rather than unlikely; the
    # inserting code catches the IntegrityError and returns the row that won.
    # Workshops are already safe by construction — they UPDATE an existing row
    # guarded by "AND deposit_paid_at IS NULL", so a replayed webhook updates
    # zero rows. They get an index too, on their own column names, for the same
    # guarantee at the schema level.
    for table, column in (
        ("bookings", "stripe_session_id"),
        ("restaurant_bookings", "stripe_session_id"),
        ("workshop_bookings", "deposit_stripe_session_id"),
        ("workshop_bookings", "balance_stripe_session_id"),
    ):
        try:
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_{column} "
                f"ON {table}({column}) WHERE {column} IS NOT NULL"
            )
            conn.commit()
        except sqlite3.Error as e:
            # Pre-existing duplicates would block this — surface it rather than
            # failing startup, since the app is still usable without it.
            print(f"[init_db] could not create unique index {table}.{column}: {e}")

    # tasks.assigned_to_user_id used to be NOT NULL; SQLite can't relax a
    # column constraint in place, so databases from before priority/room_note
    # existed get a one-time rebuild (rename, recreate, copy, drop) rather
    # than a simple ALTER ADD COLUMN.
    task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "priority" not in task_columns:
        try:
            conn.execute("BEGIN")
            conn.execute("ALTER TABLE tasks RENAME TO tasks_old")
            conn.execute(
                """CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    notes TEXT,
                    room_note TEXT,
                    due_date TEXT,
                    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')),
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    repeat_weekly INTEGER NOT NULL DEFAULT 0
                )"""
            )
            conn.execute(
                """INSERT INTO tasks (id, assigned_to_user_id, title, notes, due_date, status, created_at, completed_at)
                   SELECT id, assigned_to_user_id, title, notes, due_date, status, created_at, completed_at FROM tasks_old"""
            )
            conn.execute("DROP TABLE tasks_old")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raise

    # tasks.status used to only allow 'open'/'done' — same rebuild-in-place
    # approach, adding 'in_progress' as a real third state rather than a
    # convention overloaded onto notes/title. Uses the table's *current*
    # column list (not a hardcoded one) since tasks has picked up several
    # ALTER-added columns since the priority migration above — copying a
    # stale column list here would silently drop their data.
    tasks_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone()
    if tasks_sql and "in_progress" not in tasks_sql["sql"]:
        try:
            conn.execute("BEGIN")
            current_columns = [row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
            conn.execute("ALTER TABLE tasks RENAME TO tasks_old")
            conn.execute(
                """CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assigned_to_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    notes TEXT,
                    room_note TEXT,
                    due_date TEXT,
                    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
                    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','done')),
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    repeat_weekly INTEGER NOT NULL DEFAULT 0,
                    acknowledgment_status TEXT,
                    directed_at TEXT,
                    directed_by_user_id INTEGER,
                    origin TEXT NOT NULL DEFAULT 'manual',
                    booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL
                )"""
            )
            col_list = ", ".join(current_columns)
            conn.execute(f"INSERT INTO tasks ({col_list}) SELECT {col_list} FROM tasks_old")
            conn.execute("DROP TABLE tasks_old")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raise

    # notifications.related_task_id's FK follows whatever the tasks rebuild
    # above just did to the *name* "tasks" — SQLite's ALTER TABLE RENAME
    # rewrites other tables' FK constraint text to track the rename, so
    # after tasks was renamed to tasks_old and a fresh tasks table created,
    # this column is left pointing at the now-dropped tasks_old rather than
    # the real table. Any statement touching notifications then fails with
    # "no such table: tasks_old", even one that never mentions tasks at all.
    notifications_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
    ).fetchone()
    if notifications_sql and "tasks_old" in notifications_sql["sql"]:
        try:
            conn.execute("BEGIN")
            notif_columns = [row["name"] for row in conn.execute("PRAGMA table_info(notifications)").fetchall()]
            conn.execute("ALTER TABLE notifications RENAME TO notifications_old")
            conn.execute(
                """CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT,
                    link TEXT,
                    related_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                    read_at TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            # related_task_id itself needs explicit handling, not just a
            # blind column copy: while the FK above pointed at the dropped
            # tasks_old, ON DELETE SET NULL never fired for tasks deleted
            # in the meantime, leaving some rows pointing at task ids that
            # no longer exist. Null those out now rather than let the
            # INSERT fail on a real FOREIGN KEY constraint violation.
            other_cols = [c for c in notif_columns if c != "related_task_id"]
            other_col_list = ", ".join(other_cols)
            conn.execute(
                f"""INSERT INTO notifications ({other_col_list}, related_task_id)
                    SELECT {other_col_list},
                           CASE WHEN related_task_id IN (SELECT id FROM tasks) THEN related_task_id ELSE NULL END
                    FROM notifications_old"""
            )
            conn.execute("DROP TABLE notifications_old")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raise

    # leave_requests.status used to reject 'cancelled', and leave_type didn't
    # exist — same rebuild-in-place approach as the tasks migration above,
    # since SQLite can't ALTER a CHECK constraint. Historical rows default
    # to leave_type='vacation' (the only kind that existed before this).
    leave_requests_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leave_requests'"
    ).fetchone()
    if leave_requests_sql and "cancelled" not in leave_requests_sql["sql"]:
        try:
            conn.execute("BEGIN")
            conn.execute("ALTER TABLE leave_requests RENAME TO leave_requests_old")
            conn.execute(
                """CREATE TABLE leave_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    reason TEXT,
                    leave_type TEXT NOT NULL DEFAULT 'vacation',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','declined','cancelled')),
                    owner_note TEXT,
                    requested_at TEXT NOT NULL,
                    decided_at TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO leave_requests (id, user_id, start_date, end_date, reason, status, owner_note, requested_at, decided_at)
                   SELECT id, user_id, start_date, end_date, reason, status, owner_note, requested_at, decided_at FROM leave_requests_old"""
            )
            conn.execute("DROP TABLE leave_requests_old")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raise

    if not conn.execute("SELECT 1 FROM app_settings WHERE key = 'supplier_upload_token'").fetchone():
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('supplier_upload_token', ?)",
            (secrets.token_urlsafe(24),),
        )
        conn.commit()

    if not conn.execute("SELECT 1 FROM app_settings WHERE key = 'vapid_private_key'").fetchone():
        # Generated once and kept forever, unlike the Flask secret key —
        # every browser's push subscription is bound to the public key it
        # subscribed with, so rotating this would silently break every
        # existing subscription until each device re-subscribes.
        vapid = Vapid()
        vapid.generate_keys()
        private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        public_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('vapid_private_key', ?)",
            (base64.urlsafe_b64encode(private_raw).rstrip(b"=").decode(),),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('vapid_public_key', ?)",
            (base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode(),),
        )
        conn.commit()

    # Indexes for the columns actually filtered/joined on throughout the
    # app (booking status/date-range lookups, shift/timesheet lookups by
    # date and user, task/leave/expense queues by status). Cheap on a small
    # property's dataset either way — this is about keeping query plans
    # sane as the data grows, not fixing a measured slowdown.
    for index_ddl in (
        "CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_arrival_date ON bookings(arrival_date)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_departure_date ON bookings(departure_date)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_room_id ON bookings(room_id)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_guest_email ON bookings(guest_email)",
        "CREATE INDEX IF NOT EXISTS idx_shifts_shift_date ON shifts(shift_date)",
        "CREATE INDEX IF NOT EXISTS idx_shifts_user_id ON shifts(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_time_entries_user_id ON time_entries(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_time_entries_clock_in_at ON time_entries(clock_in_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks(assigned_to_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date)",
        "CREATE INDEX IF NOT EXISTS idx_leave_requests_status ON leave_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_leave_requests_user_id ON leave_requests(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_expenses_status ON expenses(status)",
        "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_waitlist_entries_status ON waitlist_entries(status)",
        "CREATE INDEX IF NOT EXISTS idx_guest_feedback_submitted_at ON guest_feedback(submitted_at)",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_room_issues_status ON room_issues(status)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_maintenance_vehicle_id ON vehicle_maintenance(vehicle_id)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_maintenance_status ON vehicle_maintenance(status)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_usage_vehicle_id ON vehicle_usage(vehicle_id)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_transfers_vehicle_id ON vehicle_transfers(vehicle_id)",
        "CREATE INDEX IF NOT EXISTS idx_expenses_vehicle_id ON expenses(vehicle_id)",
        "CREATE INDEX IF NOT EXISTS idx_insurance_policies_vehicle_id ON insurance_policies(vehicle_id)",
        "CREATE INDEX IF NOT EXISTS idx_breakfast_checklist_log_date ON breakfast_checklist_log(checklist_date)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_guest_feedback_booking_id_unique ON guest_feedback(booking_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_workshop_feedback_booking_id_unique ON workshop_feedback(workshop_booking_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_usage_one_open_per_vehicle ON vehicle_usage(vehicle_id) WHERE checked_in_at IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_breaks_one_open_per_entry ON breaks(time_entry_id) WHERE end_at IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_time_entries_one_open_per_user ON time_entries(user_id) WHERE clock_out_at IS NULL",
        # break_minutes_for_entry() does one SELECT per time_entry, called
        # from net_hours() on every timesheet/payroll listing and up to 7x
        # per owner dashboard load (financial_month_summary's 6-month
        # trend) — an unindexed full scan of a table that only ever grows.
        "CREATE INDEX IF NOT EXISTS idx_breaks_time_entry_id ON breaks(time_entry_id)",
        "CREATE INDEX IF NOT EXISTS idx_submission_log_ip_action_time ON submission_log(ip_address, action, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_user_id_read ON notifications(user_id, read_at)",
        "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user_id ON push_subscriptions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_booking_id ON tasks(booking_id)",
        "CREATE INDEX IF NOT EXISTS idx_restaurant_bookings_date ON restaurant_bookings(dinner_date)",
        "CREATE INDEX IF NOT EXISTS idx_restaurant_bookings_booking_id ON restaurant_bookings(booking_id)",
        "CREATE INDEX IF NOT EXISTS idx_restaurant_bookings_status ON restaurant_bookings(status)",
        "CREATE INDEX IF NOT EXISTS idx_restaurant_waitlist_date ON restaurant_waitlist(desired_date)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_sessions_workshop_id ON workshop_sessions(workshop_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_sessions_start_date ON workshop_sessions(start_date)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_bookings_session_id ON workshop_bookings(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_bookings_status ON workshop_bookings(status)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_waitlist_session_id ON workshop_waitlist(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled_date ON social_posts(scheduled_date)",
        "CREATE INDEX IF NOT EXISTS idx_social_posts_status ON social_posts(status)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_bookings_balance_due_date ON workshop_bookings(balance_due_date)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_bookings_assigned_room_id ON workshop_bookings(assigned_room_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_booking_guests_booking_id ON workshop_booking_guests(workshop_booking_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_transactions_booking_id ON workshop_transactions(workshop_booking_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_custom_fields_workshop_id ON workshop_custom_fields(workshop_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_custom_field_responses_booking_id ON workshop_custom_field_responses(workshop_booking_id)",
        "CREATE INDEX IF NOT EXISTS idx_workshop_messages_booking_id ON workshop_messages(workshop_booking_id)",
        # refunded_so_far() runs this exact lookup once per booking row on every
        # bookings/restaurant/workshops page render — without it that's a full
        # table scan per row.
        "CREATE INDEX IF NOT EXISTS idx_refunds_category_booking ON refunds(category, booking_id)",
        "CREATE INDEX IF NOT EXISTS idx_refunds_created_at ON refunds(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_email_flags_mailbox ON email_flags(mailbox)",
        # campaign_sends gains a row per email sent, forever. The dedupe key is
        # looked up once PER RECIPIENT on every automated send, so without this
        # a 500-guest send is 500 full scans of an ever-growing table.
        "CREATE INDEX IF NOT EXISTS idx_campaign_sends_dedupe ON campaign_sends(dedupe_key)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_sends_template ON campaign_sends(template_id)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_sends_unsub ON campaign_sends(unsubscribe_token)",
        "CREATE INDEX IF NOT EXISTS idx_incidents_occurred ON incidents(occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)",
        "CREATE INDEX IF NOT EXISTS idx_incidents_affected ON incidents(affected_user_id)",
        # Offboarding asks "what does this person still hold" on every leaver.
        "CREATE INDEX IF NOT EXISTS idx_access_holdings_user ON access_holdings(user_id, returned_at)",
        "CREATE INDEX IF NOT EXISTS idx_access_holdings_item ON access_holdings(access_item_id)",
        "CREATE INDEX IF NOT EXISTS idx_role_requirements_role ON role_requirements(job_role)",
        "CREATE INDEX IF NOT EXISTS idx_users_contract_end ON users(contract_end_date)",
        "CREATE INDEX IF NOT EXISTS idx_users_trial_end ON users(trial_end_date)",
        "CREATE INDEX IF NOT EXISTS idx_certifications_user_id ON certifications(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_certifications_expiry ON certifications(expiry_date)",
        "CREATE INDEX IF NOT EXISTS idx_availability_rules_user_id ON availability_rules(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_availability_exceptions_user_date ON availability_exceptions(user_id, on_date)",
        "CREATE INDEX IF NOT EXISTS idx_absences_user_id ON absences(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_absences_start_date ON absences(start_date)",
        "CREATE INDEX IF NOT EXISTS idx_performance_reviews_user_id ON performance_reviews(user_id)",
    ):
        conn.execute(index_ddl)
    conn.commit()

    if not conn.execute("SELECT 1 FROM app_settings WHERE key = 'terms_and_conditions'").fetchone():
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('terms_and_conditions', ?)",
            (DEFAULT_TERMS,),
        )
        conn.commit()

    default_email_templates = [
        ("workshop_registration_received", "Workshop: Registration received",
         "Registration received — {workshop_title}",
         "Hi {guest_name},\n\nYour registration for {workshop_title} ({dates}), party of {party_size}, "
         "has been received and is awaiting confirmation.\n{price_block}\n"
         "Reference code: {reference_code}\n"
         "Manage your registration: {manage_url}\n\n"
         "— Château de Gudanes"),
        ("workshop_confirmed", "Workshop: Registration confirmed",
         "Registration confirmed — {workshop_title}",
         "Hi {guest_name},\n\nYour registration for {workshop_title} ({dates}) is confirmed. We look forward to "
         "welcoming you.\n\nReference code: {reference_code}\n"
         "Manage your registration: {manage_url}\n\n"
         "— Château de Gudanes"),
        ("workshop_declined", "Workshop: Registration declined",
         "Registration update — {workshop_title}",
         "Hi {guest_name},\n\nWe're sorry — we're unable to confirm your registration for {workshop_title} "
         "({dates}). Please get in touch or try another date.\n\n— Château de Gudanes"),
        ("workshop_cancelled", "Workshop: Registration cancelled",
         "Registration cancelled — {workshop_title}",
         "Hi {guest_name},\n\nYour registration for {workshop_title} ({dates}) has been cancelled. Get in touch "
         "if you'd like to rebook.\n\n— Château de Gudanes"),
        ("workshop_deposit_receipt", "Workshop: Deposit receipt",
         "Deposit received — {workshop_title}",
         "Hi {guest_name},\n\nWe've received your deposit of €{deposit_amount} for {workshop_title} ({dates}).\n"
         "{balance_line}\n"
         "Reference code: {reference_code}\n\n— Château de Gudanes"),
        ("workshop_balance_reminder", "Workshop: Balance due reminder",
         "Balance due soon — {workshop_title}",
         "Hi {guest_name},\n\nA friendly reminder that the balance of €{balance_amount} for {workshop_title} "
         "({dates}) is due by {balance_due_date}.\n"
         "Manage your registration and pay online: {manage_url}\n\n— Château de Gudanes"),
        ("workshop_feedback_request", "Workshop: Feedback request",
         "How was {workshop_title}?",
         "Hi {guest_name},\n\nWe hope you enjoyed {workshop_title}. If you have a moment, we'd love to hear how "
         "it went:\n{feedback_url}\n\n— Château de Gudanes"),
        ("restaurant_reservation_received", "Restaurant: Reservation received",
         "Dinner reservation received — Château de Gudanes",
         "Hi {guest_name},\n\nYour dinner reservation request for {dinner_date}, party of {party_size}, "
         "has been received and is awaiting confirmation.{dietary_line}{price_block}\n\n"
         "Reference code: {reference_code}\n"
         "Manage your reservation: {manage_url}\n\n"
         "— Château de Gudanes"),
        ("restaurant_confirmed", "Restaurant: Reservation confirmed",
         "Dinner reservation confirmed — Château de Gudanes",
         "Hi {guest_name},\n\nYour dinner reservation for {dinner_date}, party of {party_size}, is confirmed. "
         "We look forward to hosting you.\n\nReference code: {reference_code}\n\n— Château de Gudanes"),
        ("restaurant_declined", "Restaurant: Reservation declined",
         "Dinner reservation update — Château de Gudanes",
         "Hi {guest_name},\n\nWe're sorry — we're unable to seat your party of {party_size} on {dinner_date}."
         "{refund_note} Please get in touch or try another date.\n\n— Château de Gudanes"),
        ("restaurant_cancelled", "Restaurant: Reservation cancelled",
         "Dinner reservation cancelled — Château de Gudanes",
         "Hi {guest_name},\n\nYour dinner reservation for {dinner_date} has been cancelled.{refund_note} Get in "
         "touch if you'd like to rebook.\n\n— Château de Gudanes"),
        ("room_waitlist_opening", "Automation: Room waitlist opening",
         "A room may have opened up — Château de Gudanes",
         "Hi {name},\n\nA booking was just cancelled or declined that overlaps the dates you're interested in "
         "({desired_arrival} to {desired_departure}). If you'd still like to stay with us, book now before it's "
         "taken again:\n{book_url}\n\n— Château de Gudanes"),
        ("restaurant_waitlist_opening", "Automation: Restaurant waitlist opening",
         "A table just opened up — Château de Gudanes",
         "Hi {name},\n\nA table for {desired_date}, party of {party_size}, just became available. If you'd still "
         "like to join us, reserve it now:\n{book_url}\n\n— Château de Gudanes"),
        ("workshop_waitlist_opening", "Automation: Workshop waitlist opening",
         "A spot just opened up — {workshop_title}",
         "Hi {name},\n\nA spot for {workshop_title} ({dates}) just opened up. If you'd still like to join, "
         "register now:\n{register_url}\n\n— Château de Gudanes"),
        ("event_inquiry_received", "Events: Inquiry received",
         "Event inquiry received — Château de Gudanes",
         "Hi {contact_name},\n\nThank you for your interest in hosting a {event_type} at Château de Gudanes. "
         "Your inquiry has been received and we'll be in touch shortly to discuss availability and pricing.\n\n"
         "Reference code: {reference_code}\n"
         "Manage your inquiry: {manage_url}\n\n— Château de Gudanes"),
        ("event_inquiry_confirmed", "Events: Inquiry confirmed",
         "Your event is confirmed — Château de Gudanes",
         "Hi {contact_name},\n\nWe're delighted to confirm your {event_type} at Château de Gudanes.{price_block}\n\n"
         "Reference code: {reference_code}\n\n— Château de Gudanes"),
        ("event_inquiry_declined", "Events: Inquiry declined",
         "Event inquiry update — Château de Gudanes",
         "Hi {contact_name},\n\nWe're sorry — we're unable to host your {event_type} on the date requested. "
         "Please get in touch if you'd like to discuss other dates.\n\n— Château de Gudanes"),
    ]
    for template_key, label, subject, body in default_email_templates:
        if not conn.execute("SELECT 1 FROM email_templates WHERE template_key = ?", (template_key,)).fetchone():
            conn.execute(
                "INSERT INTO email_templates (template_key, label, subject, body, updated_at) VALUES (?, ?, ?, ?, ?)",
                (template_key, label, subject, body, None),
            )
    conn.commit()

    for key, default_value in AUTOMATION_SETTING_DEFAULTS.items():
        if not conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (key,)).fetchone():
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, default_value))
    conn.commit()

    if not conn.execute("SELECT 1 FROM restaurant_settings WHERE id = 1").fetchone():
        default_opening = (datetime.now(timezone.utc).date() + timedelta(days=42)).isoformat()
        conn.execute(
            """INSERT INTO restaurant_settings (id, opening_date, dinner_time, capacity, enabled, updated_at)
               VALUES (1, ?, '19:30', 20, 0, ?)""",
            (default_opening, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    if fresh:
        # Seed one owner account with a random password shown once in the
        # terminal — this is the standard, safe way to bootstrap the very
        # first login without a hardcoded default password sitting in code.
        owner_email = "owner@chateaugudanes.com"
        generated_password = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(12)
        )
        conn.execute(
            """INSERT INTO users
               (email, password_hash, role, name, job_role, status, created_at)
               VALUES (?, ?, 'owner', 'Owner', 'Owner', 'active', ?)""",
            (owner_email, generate_password_hash(generated_password),
             datetime.now(timezone.utc).isoformat()),
        )

        default_sections = [
            ("Welcome", "Welcome to Château de Gudanes. This handbook is a living document — add to it as the estate's routines settle into place.", 0),
            ("Daily Operations", "", 1),
            ("Guest Turnover & Housekeeping", "", 2),
            ("Safety & Emergency", "", 3),
        ]
        conn.executemany(
            "INSERT INTO manual_sections (title, body, sort_order) VALUES (?, ?, ?)",
            default_sections,
        )
        conn.commit()

        print("=" * 70)
        print("FIRST RUN — owner account created.")
        print(f"  Email:    {owner_email}")
        print(f"  Password: {generated_password}")
        print("  (change this after your first login — there is no email")
        print("   recovery configured yet, so store this somewhere safe)")
        print("=" * 70)

    conn.close()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def owner_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user["role"] != "owner":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Timesheet — logging in clocks you in, logging out clocks you out. An entry
# left open (no clock_out_at) when the same user logs in again means they
# forgot to log out last time; we close it then rather than leave a ghost
# "still clocked in from three days ago" entry, and mark it auto_closed so
# the owner can tell the difference from a real logout on the timesheet.
# ---------------------------------------------------------------------------

def clock_in(conn, user_id):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE time_entries SET clock_out_at = ?, auto_closed = 1 WHERE user_id = ? AND clock_out_at IS NULL",
        (now, user_id),
    )
    try:
        conn.execute(
            "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) VALUES (?, ?, NULL, 0)",
            (user_id, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Two logins racing (double-clicked submit) both closed the prior
        # entry and tried to open a new one — the partial unique index
        # keeps it to one open entry per user; the loser's own login still
        # succeeds against the entry the winner just created.
        conn.rollback()


def clock_out(conn, user_id):
    conn.execute(
        "UPDATE time_entries SET clock_out_at = ? WHERE user_id = ? AND clock_out_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), user_id),
    )
    conn.commit()


def open_shift(conn, user_id):
    return conn.execute(
        "SELECT * FROM time_entries WHERE user_id = ? AND clock_out_at IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def break_minutes_for_entry(conn, time_entry_id):
    row = conn.execute(
        """SELECT COALESCE(SUM((julianday(end_at) - julianday(start_at)) * 1440), 0) AS m
           FROM breaks WHERE time_entry_id = ? AND end_at IS NOT NULL""",
        (time_entry_id,),
    ).fetchone()
    return row["m"] or 0.0


def net_hours_for_entries(conn, entries):
    """{entry_id: worked hours minus breaks} for a whole list, in ONE query.

    The per-entry version costs a query each, and `net_hours()` called from a
    template (where it gets no connection) OPENS A NEW CONNECTION per row. A
    fortnight of timesheets was issuing 260+ queries and a connection per line
    just to print the hours column; five years of history would have been
    unusable. Any listing that shows hours for more than one entry should use
    this instead.
    """
    entries = [e for e in entries if e["clock_out_at"]]
    if not entries:
        return {}
    ids = [e["id"] for e in entries]
    minutes = {}
    # Chunked to stay under SQLite's variable limit on a long date range.
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT time_entry_id, COALESCE(SUM((julianday(end_at) - julianday(start_at)) * 1440), 0) AS m
                FROM breaks WHERE end_at IS NOT NULL AND time_entry_id IN ({marks})
                GROUP BY time_entry_id""", chunk,
        ).fetchall():
            minutes[r["time_entry_id"]] = r["m"] or 0.0
    return {
        e["id"]: max(0.0, round(hours_between(e["clock_in_at"], e["clock_out_at"])
                                - minutes.get(e["id"], 0.0) / 60, 2))
        for e in entries
    }


def net_hours(entry, conn=None):
    """Worked hours for a time_entries row, minus any completed breaks. Takes
    an existing connection when called from a route; opens a short-lived one
    when called as a Jinja global, since template globals don't carry the
    request's conn."""
    if not entry["clock_out_at"]:
        return 0.0
    gross = hours_between(entry["clock_in_at"], entry["clock_out_at"])
    owns_conn = conn is None
    if owns_conn:
        conn = get_db()
    break_minutes = break_minutes_for_entry(conn, entry["id"])
    if owns_conn:
        conn.close()
    return max(0.0, round(gross - break_minutes / 60, 2))


def labour_hours_by_person(conn, start_iso, end_iso):
    """Worked hours per employee in a window, minus completed breaks.

    THE one definition of "hours worked" for costing. It used to exist twice —
    financial_month_summary rounded each entry to 2dp and costed entry by
    entry, while the labour report rounded each person to 1dp and costed once —
    so the two put different numbers on the same shifts. Employees only: the
    owner's own clocked time is drawings, not a wage bill.
    """
    return conn.execute(
        """SELECT users.id, users.name, users.pay_rate, users.pay_type,
                  COALESCE(SUM(
                      (julianday(time_entries.clock_out_at)
                       - julianday(time_entries.clock_in_at)) * 24
                      - COALESCE((SELECT SUM((julianday(breaks.end_at)
                                              - julianday(breaks.start_at)) * 24)
                                  FROM breaks
                                  WHERE breaks.time_entry_id = time_entries.id
                                    AND breaks.end_at IS NOT NULL), 0)
                  ), 0) AS hours,
                  COUNT(time_entries.id) AS shifts
           FROM users LEFT JOIN time_entries
             ON time_entries.user_id = users.id
            AND time_entries.clock_out_at IS NOT NULL
            AND time_entries.clock_out_at > time_entries.clock_in_at
            AND time_entries.clock_in_at >= ? AND time_entries.clock_in_at < ?
           WHERE users.role = 'employee'
           GROUP BY users.id HAVING hours > 0
           ORDER BY hours DESC""",
        (start_iso, end_iso),
    ).fetchall()


def estimated_labour_cost(conn, start_iso, end_iso):
    """Estimated wage bill for a window. Returns (cost, hours, unpriced) where
    cost is None when nobody on the clock has a usable hourly rate — so the UI
    can say "not estimated" rather than showing a confident zero."""
    total_cost, total_hours, unpriced, priced_any = 0.0, 0.0, 0, False
    for r in labour_hours_by_person(conn, start_iso, end_iso):
        total_hours += r["hours"]
        cost = estimated_hourly_cost(r["hours"], r["pay_rate"], r["pay_type"])
        if cost is None:
            unpriced += 1
        else:
            total_cost += cost
            priced_any = True
    return (round(total_cost, 2) if priced_any else None), round(total_hours, 1), unpriced


def estimated_hourly_cost(hours, pay_rate, pay_type):
    """Best-effort hours x rate estimate from the free-text pay reference
    fields — returns None whenever the type isn't hourly or the rate text
    isn't a clean number. This is a rough estimate for the owner's own
    awareness only, never a payroll or payslip figure (see module docstring)."""
    type_text = (pay_type or "").lower()
    rate_text = (pay_rate or "").lower()
    if "hour" not in type_text and "/hr" not in rate_text and "/hour" not in rate_text and "per hour" not in rate_text:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", pay_rate or "")
    if not match:
        return None
    try:
        rate = float(match.group(0).replace(",", "."))
    except ValueError:
        return None
    return round(hours * rate, 2)


def hours_between(start_iso, end_iso):
    start = parse_datetime_iso(start_iso)
    end = parse_datetime_iso(end_iso)
    if not start or not end:
        return 0.0
    return round((end - start).total_seconds() / 3600, 2)


# ---------------------------------------------------------------------------
# Vault — encrypts secrets at rest with Fernet (AES-128-CBC + HMAC, from the
# well-audited `cryptography` library — not hand-rolled). Off until
# VAULT_ENCRYPTION_KEY is set; see the constant's comment above for why.
# ---------------------------------------------------------------------------

def vault_enabled():
    return bool(VAULT_ENCRYPTION_KEY)


def vault_encrypt(password, notes):
    payload = json.dumps({"password": password or "", "notes": notes or ""}).encode()
    return Fernet(VAULT_ENCRYPTION_KEY.encode()).encrypt(payload).decode()


def vault_decrypt(token):
    try:
        payload = Fernet(VAULT_ENCRYPTION_KEY.encode()).decrypt(token.encode())
        return json.loads(payload.decode())
    except (InvalidToken, ValueError):
        return {"password": None, "notes": None, "error": True}


def fernet_encrypt_json(data):
    """Same Fernet key as the Vault, generalized to any JSON-able dict —
    used for bank account numbers/IBANs, which need the same at-rest
    protection as vault passwords but don't fit the password/notes shape."""
    payload = json.dumps(data).encode()
    return Fernet(VAULT_ENCRYPTION_KEY.encode()).encrypt(payload).decode()


def fernet_decrypt_json(token):
    try:
        payload = Fernet(VAULT_ENCRYPTION_KEY.encode()).decrypt(token.encode())
        return json.loads(payload.decode())
    except (InvalidToken, ValueError):
        return None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(d, months):
    """Add a whole number of months to a date, clamping the day to the
    target month's length (e.g. Jan 31 + 1 month -> Feb 28/29)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def format_date_human(iso_str):
    """'2026-12-01' -> 'December 1, 2026'. Avoids the %-d strftime flag,
    which is Linux-only and crashes on Windows."""
    d = parse_date(iso_str)
    if not d:
        return iso_str
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def log_audit(conn, action, target=None, details=None):
    """Records who did what to something sensitive, and when — deletions,
    status changes, vault/bank-detail edits, backup downloads. Separate from
    recent_activity() (which is a general-interest feed for the dashboard);
    this is specifically for looking back at who touched something risky."""
    user = current_user()
    conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"] if user else None, action, target, details, datetime.now(timezone.utc).isoformat()),
    )


def recent_activity(conn, limit=15):
    """A single chronological feed pulled from several tables — read-only,
    doesn't need its own table since every source already has a timestamp."""
    events = []
    for t in conn.execute(
        """SELECT tasks.title, tasks.completed_at, users.name AS who FROM tasks
           LEFT JOIN users ON users.id = tasks.assigned_to_user_id
           WHERE tasks.status = 'done' AND tasks.completed_at IS NOT NULL
           ORDER BY tasks.completed_at DESC LIMIT ?""",
        (limit,),
    ).fetchall():
        events.append({"at": t["completed_at"], "text": f"{t['who'] or 'Someone'} completed “{t['title']}”"})
    for e in conn.execute(
        """SELECT expenses.kind, expenses.vendor_name, expenses.amount, expenses.submitted_at,
                  users.name AS who
           FROM expenses LEFT JOIN users ON users.id = expenses.submitted_by_user_id
           ORDER BY expenses.submitted_at DESC LIMIT ?""",
        (limit,),
    ).fetchall():
        label = "a supplier invoice" if e["kind"] == "supplier_invoice" else "an expense claim"
        who = e["who"] or e["vendor_name"] or "Someone"
        events.append({"at": e["submitted_at"], "text": f"{who} submitted {label} for €{e['amount']:.2f}"})
    for lr in conn.execute(
        """SELECT leave_requests.start_date, leave_requests.end_date, leave_requests.requested_at,
                  users.name AS who
           FROM leave_requests JOIN users ON users.id = leave_requests.user_id
           ORDER BY leave_requests.requested_at DESC LIMIT ?""",
        (limit,),
    ).fetchall():
        events.append({"at": lr["requested_at"], "text": f"{lr['who']} requested time off {lr['start_date']} → {lr['end_date']}"})
    for d in conn.execute(
        """SELECT documents.title, documents.uploaded_at, users.name AS who
           FROM documents JOIN users ON users.id = documents.user_id
           ORDER BY documents.uploaded_at DESC LIMIT ?""",
        (limit,),
    ).fetchall():
        events.append({"at": d["uploaded_at"], "text": f"Document uploaded for {d['who']}: {d['title']}"})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


def week_overtime(conn, today, threshold_hours=35):
    """Hours worked this Mon-Sun per employee, flagged past the French
    standard 35h week. Awareness only — not enforcement, not payroll."""
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)
    week_start_utc = datetime.combine(week_start, datetime.min.time(), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    week_end_utc = datetime.combine(week_end, datetime.min.time(), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    rows = conn.execute(
        """SELECT time_entries.*, users.name AS employee_name FROM time_entries
           JOIN users ON users.id = time_entries.user_id
           WHERE clock_in_at >= ? AND clock_in_at < ? AND clock_out_at IS NOT NULL""",
        (week_start_utc.isoformat(), week_end_utc.isoformat()),
    ).fetchall()
    hours_by_entry = net_hours_for_entries(conn, rows)
    totals = {}
    for r in rows:
        bucket = totals.setdefault(r["user_id"], {"name": r["employee_name"], "hours": 0.0})
        bucket["hours"] += hours_by_entry.get(r["id"], 0.0)
    return sorted(
        ({"name": v["name"], "hours": round(v["hours"], 2)} for v in totals.values() if v["hours"] > threshold_hours),
        key=lambda x: -x["hours"],
    )


def unstaffed_activity_days(conn, today, days_ahead=7):
    """Days in the next `days_ahead` with a confirmed arrival or departure
    but nobody on the shift schedule at all — a real risk that no one's
    there to do check-in or turnover, not a labour-law calculation."""
    end = today + timedelta(days=days_ahead)
    bookings = conn.execute(
        """SELECT arrival_date, departure_date FROM bookings
           WHERE status = 'confirmed' AND (
               (arrival_date >= ? AND arrival_date < ?) OR (departure_date >= ? AND departure_date < ?)
           )""",
        (today.isoformat(), end.isoformat(), today.isoformat(), end.isoformat()),
    ).fetchall()
    activity_by_day = {}
    for b in bookings:
        if today.isoformat() <= b["arrival_date"] < end.isoformat():
            bucket = activity_by_day.setdefault(b["arrival_date"], {"arrivals": 0, "departures": 0})
            bucket["arrivals"] += 1
        if today.isoformat() <= b["departure_date"] < end.isoformat():
            bucket = activity_by_day.setdefault(b["departure_date"], {"arrivals": 0, "departures": 0})
            bucket["departures"] += 1
    if not activity_by_day:
        return []
    shift_days = {
        r["shift_date"] for r in conn.execute(
            "SELECT DISTINCT shift_date FROM shifts WHERE shift_date >= ? AND shift_date < ?",
            (today.isoformat(), end.isoformat()),
        ).fetchall()
    }
    results = [
        {"date": parse_date(day), "arrivals": counts["arrivals"], "departures": counts["departures"]}
        for day, counts in activity_by_day.items()
        if day not in shift_days
    ]
    results.sort(key=lambda r: r["date"])
    return results


def occupied_rooms_by_date(conn, start, end):
    """Count of active rooms with a confirmed booking covering each date in
    [start, end) — the 'volume' figure the roster suggestion is based on."""
    counts = {}
    d = start
    while d < end:
        counts[d.isoformat()] = 0
        d += timedelta(days=1)
    bookings = conn.execute(
        """SELECT arrival_date, departure_date FROM bookings
           WHERE status = 'confirmed' AND room_id IN (SELECT id FROM rooms WHERE active = 1)
           AND arrival_date < ? AND departure_date > ?""",
        (end.isoformat(), start.isoformat()),
    ).fetchall()
    for b in bookings:
        b_start, b_end = parse_date(b["arrival_date"]), parse_date(b["departure_date"])
        if not b_start or not b_end:
            continue
        d = max(b_start, start)
        while d < min(b_end, end):
            counts[d.isoformat()] = counts.get(d.isoformat(), 0) + 1
            d += timedelta(days=1)
    return counts


ROOMS_PER_STAFF = 3  # rough rule of thumb, not a staffing policy — tune to taste


def rate_limited(conn, action, limit, window_hours=1):
    """True if this IP has hit `limit` submissions of `action` in the last
    `window_hours` — logs this attempt regardless of the outcome, so the
    window stays accurate even while rejecting. Guards public, unauthenticated
    forms (booking, etc.) against being spammed to exhaustion — e.g. the
    booking form specifically, since is_range_available() treats a pending
    booking as blocking those dates for everyone else by design, so a
    script submitting junk requests could otherwise lock real guests out of
    booking entirely."""
    ip = request.remote_addr or "unknown"
    window_start = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM submission_log WHERE ip_address = ? AND action = ? AND created_at >= ?",
        (ip, action, window_start),
    ).fetchone()["c"]
    conn.execute(
        "INSERT INTO submission_log (ip_address, action, created_at) VALUES (?, ?, ?)",
        (ip, action, datetime.now(timezone.utc).isoformat()),
    )
    return count >= limit


def expire_stale_pending_bookings(conn, hours=STALE_PENDING_BOOKING_HOURS):
    """Auto-declines pending, unpaid booking requests the owner never acted
    on within `hours` — otherwise a forgotten (or spammed) pending request
    blocks those dates for every other guest indefinitely, since
    is_range_available() treats pending as a blocker by design. Paid
    bookings are deliberately excluded: those need a human, not a silent
    auto-decline-and-refund just because nobody checked the site in time.
    Idempotent (only ever touches still-pending rows), safe to call on
    every dashboard load like auto_prep_upcoming_arrivals."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    stale = conn.execute(
        "SELECT id FROM bookings WHERE status = 'pending' AND payment_status != 'paid' AND created_at <= ?",
        (cutoff,),
    ).fetchall()
    count = 0
    for row in stale:
        declined, _refunded, _refund_error = decline_booking_by_id(conn, row["id"])
        if declined:
            count += 1
    if count:
        conn.commit()
    return count


def overdue_vehicle_checkouts(conn, hours=24):
    """Vehicles still checked out well past a normal errand/transfer length —
    usually just a forgotten check-in, but worth a nudge since it also hides
    the vehicle from anyone else who wants to use it."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return conn.execute(
        """SELECT vehicle_usage.*, vehicles.name AS vehicle_name, users.name AS user_name
           FROM vehicle_usage
           JOIN vehicles ON vehicles.id = vehicle_usage.vehicle_id
           LEFT JOIN users ON users.id = vehicle_usage.user_id
           WHERE vehicle_usage.checked_in_at IS NULL AND vehicle_usage.checked_out_at <= ?
           ORDER BY vehicle_usage.checked_out_at""",
        (cutoff,),
    ).fetchall()


def roster_vs_occupancy(conn, days):
    """For each date, occupied-room volume next to how many distinct
    employees are scheduled, plus a rough suggested headcount
    (ceil(occupied_rooms / ROOMS_PER_STAFF)). A nudge for planning the
    rota, not an authoritative staffing requirement."""
    start = days[0]
    end = days[-1] + timedelta(days=1)
    occupancy = occupied_rooms_by_date(conn, start, end)
    scheduled_counts = {}
    for row in conn.execute(
        "SELECT DISTINCT shift_date, user_id FROM shifts WHERE shift_date >= ? AND shift_date < ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall():
        scheduled_counts[row["shift_date"]] = scheduled_counts.get(row["shift_date"], 0) + 1
    results = []
    for d in days:
        iso = d.isoformat()
        occupied = occupancy.get(iso, 0)
        scheduled = scheduled_counts.get(iso, 0)
        suggested = -(-occupied // ROOMS_PER_STAFF) if occupied else 0  # ceil division
        results.append({
            "date": d, "occupied_rooms": occupied, "scheduled": scheduled, "suggested": suggested,
            "understaffed": occupied > 0 and scheduled < suggested,
        })
    return results


def timesheet_outliers(conn, today, lookback_days=14, early_hour=6, late_hour=22, max_hours=12):
    """Closed timesheet entries in the last `lookback_days` with a clock-in
    outside the château's normal hours, or an implausibly long shift — a
    nudge to double-check for a typo or a genuinely unusual day, not a
    labour-law judgement. There's no guest check-in/out *time* in this
    schema (bookings only carry dates), so this covers staff attendance,
    which is the closest real 'check in / check out time' data that exists."""
    since = datetime.combine(today - timedelta(days=lookback_days), datetime.min.time(), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    rows = conn.execute(
        """SELECT time_entries.*, users.name AS employee_name FROM time_entries
           JOIN users ON users.id = time_entries.user_id
           WHERE clock_out_at IS NOT NULL AND clock_in_at >= ?""",
        (since.isoformat(),),
    ).fetchall()
    hours_by_entry = net_hours_for_entries(conn, rows)
    results = []
    for r in rows:
        clock_in_dt = parse_datetime_iso(r["clock_in_at"])
        if not clock_in_dt:
            continue
        local_in = clock_in_dt.astimezone(LOCAL_TZ)
        hours = hours_by_entry.get(r["id"], 0.0)
        reasons = []
        if local_in.hour < early_hour:
            reasons.append(f"clocked in {local_in.strftime('%H:%M')} — unusually early")
        elif local_in.hour >= late_hour:
            reasons.append(f"clocked in {local_in.strftime('%H:%M')} — unusually late")
        if hours > max_hours:
            reasons.append(f"{hours}h shift — unusually long")
        if reasons:
            results.append({
                "employee_name": r["employee_name"], "date": local_in.date(),
                "clock_in": local_time_str(r["clock_in_at"]), "clock_out": local_time_str(r["clock_out_at"]),
                "hours": hours, "reasons": reasons,
            })
    results.sort(key=lambda x: x["date"], reverse=True)
    return results


def auto_prep_upcoming_arrivals(conn, today, days_ahead=2):
    """Silently generates the arrival-prep checklist for confirmed bookings
    arriving within `days_ahead` that haven't been prepped yet, assigning
    to whoever has a shift that day if anyone does (else left unassigned).
    Idempotent via arrival_prepped_at, so it's safe to call on every
    dashboard load rather than needing a scheduled job. Returns the number
    of bookings just prepped."""
    end = today + timedelta(days=days_ahead)
    bookings = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id
           WHERE bookings.status = 'confirmed' AND bookings.arrival_prepped_at IS NULL
           AND bookings.arrival_date >= ? AND bookings.arrival_date < ?""",
        (today.isoformat(), end.isoformat()),
    ).fetchall()
    now = datetime.now(timezone.utc)
    prepped_count = 0
    for booking in bookings:
        # Re-check per row, not just in the initial SELECT: two dashboard
        # loads racing (threaded=True, e.g. two owner tabs) could both
        # select the same unprepped bookings before either writes — this
        # conditional UPDATE is what actually keeps a booking from getting
        # a duplicated prep checklist.
        cur = conn.execute(
            "UPDATE bookings SET arrival_prepped_at = ? WHERE id = ? AND arrival_prepped_at IS NULL",
            (now.isoformat(), booking["id"]),
        )
        if cur.rowcount == 0:
            continue
        prepped_count += 1
        scheduled = conn.execute(
            "SELECT user_id FROM shifts WHERE shift_date = ? ORDER BY start_time LIMIT 1",
            (booking["arrival_date"],),
        ).fetchone()
        assigned_to = scheduled["user_id"] if scheduled else None
        room_note = f"{booking['guest_name']} arriving {booking['arrival_date']}, party of {booking['party_size']}."
        if booking["special_requests"]:
            room_note += f" Requests: {booking['special_requests']}"
        party_size = booking["party_size"] or 1
        for item in ARRIVAL_PREP_CHECKLIST:
            title = item.format(n=party_size) if "{n}" in item else item
            conn.execute(
                """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date, created_at, origin)
                   VALUES (?, ?, ?, 'high', ?, ?, 'checklist')""",
                (assigned_to, f"{booking['room_name']}: {title}", room_note, booking["arrival_date"], now.isoformat()),
            )
    if prepped_count:
        conn.commit()
    return prepped_count


def leave_balance(conn, user_id, entitlement, year=None):
    """Calendar days used from approved leave this year, against the
    employee's annual_leave_days entitlement. Counts each approved request
    whose start_date falls in the target year, inclusive of both end dates —
    a simple calendar-day count, not a business-day one, and a request
    spanning New Year's only counts toward the year it starts in. Sick leave
    is tracked but excluded from the count — it doesn't eat into the paid
    annual leave entitlement the way vacation/personal days do. Returns
    None for 'remaining' when no entitlement is set, so the UI can show
    "not set" instead of a misleading number."""
    year = year or datetime.now(timezone.utc).year
    rows = conn.execute(
        "SELECT start_date, end_date FROM leave_requests WHERE user_id = ? AND status = 'approved' AND leave_type != 'sick'",
        (user_id,),
    ).fetchall()
    used = 0
    for r in rows:
        start = parse_date(r["start_date"])
        end = parse_date(r["end_date"])
        if not start or not end or start.year != year:
            continue
        used += (end - start).days + 1
    remaining = (entitlement - used) if entitlement is not None else None
    return {"year": year, "entitlement": entitlement, "used": used, "remaining": remaining}


def financial_month_summary(conn, month_start, month_end):
    """Revenue (confirmed bookings by arrival date), expenses (approved/paid,
    both kinds), and an estimated labour cost for one calendar month.
    Same approximations as compute_month_stats/estimated_hourly_cost — good
    enough for the owner's own awareness, not an accounting-grade P&L."""
    room_revenue = conn.execute(
        """SELECT COALESCE(SUM(total_price), 0) AS total FROM bookings
           WHERE status = 'confirmed' AND arrival_date >= ? AND arrival_date < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    restaurant_revenue = conn.execute(
        """SELECT COALESCE(SUM(total_price), 0) AS total FROM restaurant_bookings
           WHERE status = 'confirmed' AND dinner_date >= ? AND dinner_date < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    workshop_revenue = conn.execute(
        """SELECT COALESCE(SUM(workshop_bookings.total_price), 0) AS total FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           WHERE workshop_bookings.status = 'confirmed'
             AND workshop_sessions.start_date >= ? AND workshop_sessions.start_date < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    # Events are bespoke/owner-quoted rather than self-service like the
    # other three, but a confirmed wedding or photoshoot is real revenue —
    # without this, it contributed nothing to any figure the owner sees.
    event_revenue = conn.execute(
        """SELECT COALESCE(SUM(quoted_price), 0) AS total FROM event_inquiries
           WHERE status = 'confirmed' AND quoted_price IS NOT NULL
             AND preferred_date >= ? AND preferred_date < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    # Money given back is money not earned. A refund never changes a booking's
    # status or total_price, so without this a refunded stay stayed in revenue
    # at full value forever -- overstating both revenue and net profit in every
    # figure the owner and the accountant see. Attributed to the month the
    # refund was ISSUED, which is when it actually left the business.
    refunds_by_category = {
        r["category"]: r["total"] for r in conn.execute(
            """SELECT category, COALESCE(SUM(amount), 0) AS total FROM refunds
               WHERE created_at >= ? AND created_at < ? GROUP BY category""",
            (month_start.isoformat(), month_end.isoformat()),
        ).fetchall()
    }
    room_refunds = refunds_by_category.get("room", 0)
    restaurant_refunds = refunds_by_category.get("restaurant", 0)
    workshop_refunds = refunds_by_category.get("workshop", 0)
    event_refunds = refunds_by_category.get("event", 0)
    refunds_total = room_refunds + restaurant_refunds + workshop_refunds + event_refunds

    # Kept before the refunds come off, so a report can show the honest
    # waterfall (took X, gave back Y, spent Z, left with N). Reporting only the
    # net figure NEXT TO a separate refunds line made the row look as though
    # refunds were still to be deducted, and the four numbers didn't add up.
    revenue_gross = room_revenue + restaurant_revenue + workshop_revenue + event_revenue

    room_revenue -= room_refunds
    restaurant_revenue -= restaurant_refunds
    workshop_revenue -= workshop_refunds
    event_revenue -= event_refunds

    revenue = room_revenue + restaurant_revenue + workshop_revenue + event_revenue
    staff_expenses = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM expenses
           WHERE status IN ('approved','paid') AND kind = 'staff_expense'
           AND submitted_at >= ? AND submitted_at < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    supplier_expenses = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM expenses
           WHERE status IN ('approved','paid') AND kind = 'supplier_invoice'
           AND submitted_at >= ? AND submitted_at < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]

    labour_cost, _labour_hours, _unpriced = estimated_labour_cost(
        conn, month_start.isoformat(), month_end.isoformat())

    expenses_total = round(staff_expenses + supplier_expenses, 2)
    net = round(revenue - expenses_total - (labour_cost or 0), 2)
    return {
        "month": month_start, "revenue": round(revenue, 2),
        "revenue_gross": round(revenue_gross, 2), "room_revenue": round(room_revenue, 2),
        "restaurant_revenue": round(restaurant_revenue, 2), "workshop_revenue": round(workshop_revenue, 2),
        "event_revenue": round(event_revenue, 2), "staff_expenses": round(staff_expenses, 2),
        "supplier_expenses": round(supplier_expenses, 2), "expenses_total": expenses_total,
        "labour_cost": labour_cost, "net": net,
        "refunds_total": round(refunds_total, 2),
    }


def expense_category_breakdown(conn, month_start, month_end):
    """Approved/paid expense spend for one calendar month, grouped by vendor
    for supplier invoices and lumped as 'Staff expenses' otherwise — the
    closest thing to a spend category this schema tracks."""
    rows = conn.execute(
        """SELECT CASE WHEN kind = 'staff_expense' THEN 'Staff expenses' ELSE COALESCE(NULLIF(vendor_name, ''), 'Other supplier') END AS category,
               COALESCE(SUM(amount), 0) AS total, COUNT(*) AS count
           FROM expenses
           WHERE status IN ('approved','paid') AND submitted_at >= ? AND submitted_at < ?
           GROUP BY category ORDER BY total DESC""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    return rows


def room_revenue_breakdown(conn, month_start, month_end):
    """Confirmed-booking revenue per active room for one calendar month,
    same attribution rule as financial_month_summary (by arrival date)."""
    return conn.execute(
        """SELECT rooms.name AS room_name, COALESCE(SUM(bookings.total_price), 0) AS revenue,
               COUNT(bookings.id) AS booking_count
           FROM rooms LEFT JOIN bookings ON bookings.room_id = rooms.id
               AND bookings.status = 'confirmed' AND bookings.arrival_date >= ? AND bookings.arrival_date < ?
           WHERE rooms.active = 1
           GROUP BY rooms.id ORDER BY revenue DESC""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()


def upcoming_anniversaries(conn, today, days_ahead=14):
    """Employees whose hire-date anniversary falls in the next `days_ahead`
    days, with years-of-service for display. Skips anyone without a
    start_date rather than guessing."""
    results = []
    for e in conn.execute("SELECT * FROM users WHERE role = 'employee' AND start_date IS NOT NULL").fetchall():
        started = parse_date(e["start_date"])
        if not started:
            continue
        try:
            this_year = started.replace(year=today.year)
        except ValueError:
            this_year = started.replace(year=today.year, day=28)  # Feb 29 on a non-leap year
        anniversary = this_year if this_year >= today else this_year.replace(year=today.year + 1)
        days_away = (anniversary - today).days
        if 0 <= days_away <= days_ahead:
            years = anniversary.year - started.year
            if years > 0:
                results.append({"employee": e, "date": anniversary, "days_away": days_away, "years": years})
    results.sort(key=lambda r: r["days_away"])
    return results


def probation_reviews_due(conn, today, probation_days=90, days_ahead=14):
    """Active employees approaching the end of a standard ~90-day probation
    period (France's 'période d'essai' for most roles) — a nudge to do the
    review before it lapses, not an enforcement of any specific contract
    term. Only looks forward from hire date once, at the probation mark
    itself, not every year like an anniversary."""
    results = []
    for e in conn.execute(
        "SELECT * FROM users WHERE role = 'employee' AND status = 'active' AND start_date IS NOT NULL"
    ).fetchall():
        started = parse_date(e["start_date"])
        if not started:
            continue
        mark = started + timedelta(days=probation_days)
        days_away = (mark - today).days
        if -7 <= days_away <= days_ahead:
            results.append({"employee": e, "date": mark, "days_away": days_away})
    results.sort(key=lambda r: r["days_away"])
    return results


def expiry_status(expiry_date_iso, soon_days=30):
    """None if no expiry set or it's far off; 'expired' or 'soon' otherwise
    — for a small warning badge next to a document, not an enforcement."""
    if not expiry_date_iso:
        return None
    d = parse_date(expiry_date_iso)
    if not d:
        return None
    days_left = (d - datetime.now(timezone.utc).date()).days
    if days_left < 0:
        return "expired"
    if days_left <= soon_days:
        return "soon"
    return None


def local_time_str(iso_str):
    """Timestamps are stored in UTC; this renders one in the château's local
    time (see LOCAL_TZ) as 'HH:MM', for display only."""
    dt = parse_datetime_iso(iso_str)
    if not dt:
        return "?"
    return dt.astimezone(LOCAL_TZ).strftime("%H:%M")


def local_datetime_str(iso_str):
    dt = parse_datetime_iso(iso_str)
    if not dt:
        return "?"
    local = dt.astimezone(LOCAL_TZ)
    return f"{format_date_human(local.date().isoformat())} {local.strftime('%H:%M')}"


def local_time_input_to_utc_iso(base_iso, hhmm):
    """Combines a local HH:MM string with the calendar date (in LOCAL_TZ) of
    an existing UTC timestamp, returning a new UTC ISO string. Used to turn
    an owner-entered correction time into a storable timestamp without
    touching the entry's date — corrections fix a time, not a day."""
    base_dt = parse_datetime_iso(base_iso)
    local_date = base_dt.astimezone(LOCAL_TZ).date()
    naive = datetime.strptime(f"{local_date.isoformat()} {hhmm}", "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()


def local_datetime_input_to_utc_iso(value):
    """Converts a <input type="datetime-local"> value (naive, entered in
    LOCAL_TZ) to a UTC ISO string for storage — every other timestamp in
    the app is stored in UTC (see local_time_input_to_utc_iso above)."""
    naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return naive.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()


def parse_datetime_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


PERIOD_CHOICES = ("day", "week", "month", "year")


def resolve_period(period=None, anchor=None, today=None):
    """The date window every dashboard and report works in.

    One helper so "this week" means the same thing everywhere — Monday-start,
    and the end is EXCLUSIVE so a query is always `>= start AND < end` with no
    off-by-one on the final day. Also hands back the previous/next anchors so
    a page can offer arrows without recomputing the calendar itself.
    """
    today = today or datetime.now(timezone.utc).date()
    period = (period or "month").lower()
    if period not in PERIOD_CHOICES:
        period = "month"
    anchor = (parse_date(anchor) if isinstance(anchor, str) else anchor) or today

    if period == "day":
        start = anchor
        end = start + timedelta(days=1)
        label = start.strftime("%A %d %B %Y")
        prev_anchor, next_anchor = start - timedelta(days=1), start + timedelta(days=1)
    elif period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
        label = f"Week of {start.strftime('%d %B %Y')}"
        prev_anchor, next_anchor = start - timedelta(days=7), start + timedelta(days=7)
    elif period == "year":
        start = date(anchor.year, 1, 1)
        end = date(anchor.year + 1, 1, 1)
        label = str(anchor.year)
        prev_anchor, next_anchor = date(anchor.year - 1, 1, 1), date(anchor.year + 1, 1, 1)
    else:
        start = anchor.replace(day=1)
        end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
        label = start.strftime("%B %Y")
        prev_anchor = (start - timedelta(days=1)).replace(day=1)
        next_anchor = end

    # The equivalent window immediately before this one, for "vs last period".
    span = (end - start)
    prev_start = start - span if period in ("day", "week") else prev_anchor
    prev_end = start
    return {
        "period": period, "start": start, "end": end, "label": label,
        "start_iso": start.isoformat(), "end_iso": end.isoformat(),
        "anchor": anchor, "anchor_iso": anchor.isoformat(),
        "prev_anchor": prev_anchor.isoformat(), "next_anchor": next_anchor.isoformat(),
        "prev_start_iso": prev_start.isoformat(), "prev_end_iso": prev_end.isoformat(),
        "is_current": start <= today < end,
    }


def period_from_request():
    """The window a page should show, taken from ?period= and ?date=."""
    return resolve_period(request.args.get("period"), request.args.get("date"))


# ---------------------------------------------------------------------------
# Section overviews.
#
# Every category in the nav now opens on a headline band instead of dropping
# straight into a sub-page, so the first thing on screen answers "how is this
# part of the house doing" before you go hunting for it. Each builder returns
# a plain list of cells so `_overview_band.html` can render any section without
# knowing anything about it, and every builder takes the same `period` dict so
# "this week" means the same thing in Guests as it does in Financial.
# ---------------------------------------------------------------------------

def overview_cell(label, value, sub=None, alert=False, hint=None, delta=None, endpoint=None):
    """One figure in an overview band.

    `alert` turns the cell red — reserve it for things that need a decision,
    not merely for large numbers. `delta` is the change against the previous
    equivalent window, already signed. `endpoint` is a route NAME, resolved by
    the template, so these builders stay plain functions that can be called
    (and tested) without a request context.
    """
    return {"label": label, "value": value, "sub": sub, "alert": bool(alert),
            "hint": hint, "delta": delta, "endpoint": endpoint}


def euro(value):
    """Money as it is written everywhere else in the app."""
    value = value or 0
    return f"{'−' if value < 0 else ''}€{abs(value):,.0f}"


def guests_overview(conn, period, today):
    """Rooms over the chosen window, plus who is physically here right now."""
    occ = report_occupancy(conn, period)
    iso = today.isoformat()
    in_house = conn.execute(
        """SELECT COALESCE(SUM(party_size), 0) AS c FROM bookings
           WHERE status = 'confirmed' AND arrival_date <= ? AND departure_date > ?""",
        (iso, iso),
    ).fetchone()["c"]
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE status = 'pending'"
    ).fetchone()["c"]
    return [
        overview_cell("In residence now", in_house, hint="people, not rooms"),
        overview_cell("Arrivals", occ["arrivals"]),
        overview_cell("Departures", occ["departures"]),
        overview_cell("Occupancy", occ["occupancy"], sub="%"),
        overview_cell("Room revenue", euro(occ["revenue"])),
        overview_cell("Awaiting a decision", pending, alert=pending),
    ]


def employee_overview(conn, period, today):
    """The team over the chosen window. Deliberately separate from the HR page,
    which answers the compliance questions; this answers "who is working"."""
    hours = conn.execute(
        """SELECT COALESCE(SUM((julianday(clock_out_at) - julianday(clock_in_at)) * 24), 0) AS h
           FROM time_entries WHERE clock_out_at IS NOT NULL AND clock_out_at > clock_in_at
             AND clock_in_at >= ? AND clock_in_at < ?""",
        (period["start_iso"], period["end_iso"]),
    ).fetchone()["h"]
    prev_hours = conn.execute(
        """SELECT COALESCE(SUM((julianday(clock_out_at) - julianday(clock_in_at)) * 24), 0) AS h
           FROM time_entries WHERE clock_out_at IS NOT NULL AND clock_out_at > clock_in_at
             AND clock_in_at >= ? AND clock_in_at < ?""",
        (period["prev_start_iso"], period["prev_end_iso"]),
    ).fetchone()["h"]
    active = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'employee' AND status = 'active'"
    ).fetchone()["c"]
    on_shift = conn.execute(
        "SELECT COUNT(*) AS c FROM time_entries WHERE clock_out_at IS NULL"
    ).fetchone()["c"]
    on_leave = conn.execute(
        """SELECT COUNT(*) AS c FROM leave_requests
           WHERE status = 'approved' AND start_date <= ? AND end_date >= ?""",
        (today.isoformat(), today.isoformat()),
    ).fetchone()["c"]
    unclaimed = conn.execute(
        """SELECT COUNT(*) AS c FROM users
           WHERE role = 'employee' AND status = 'active' AND NOT account_claimed"""
    ).fetchone()["c"]
    open_tasks = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE status != 'done'"
    ).fetchone()["c"]
    # Entries whose clock-out precedes their clock-in are excluded from the
    # hours above (they used to drag the total negative). Excluding them
    # quietly would just move the problem, so they are counted and shown.
    broken = conn.execute(
        """SELECT COUNT(*) AS c FROM time_entries
           WHERE clock_out_at IS NOT NULL AND clock_out_at < clock_in_at"""
    ).fetchone()["c"]
    delta = round(hours - prev_hours, 1)
    cells = [
        overview_cell("Active team", active),
        overview_cell("On shift now", on_shift),
        overview_cell("Hours worked", round(hours, 1), sub="h",
                      delta=delta if prev_hours else None),
        overview_cell("On leave today", on_leave),
        overview_cell("Open tasks", open_tasks),
        overview_cell("Accounts unclaimed", unclaimed, alert=unclaimed),
    ]
    if broken:
        cells.append(overview_cell(
            "Impossible shifts", broken, alert=True, hint="clocked out before in",
            endpoint="admin_timesheets"))
    return cells


def financial_overview(conn, period, today):
    """Money over the chosen window, and what is still waiting on the owner."""
    now = financial_month_summary(conn, period["start"], period["end"])
    prev = financial_month_summary(
        conn, parse_date(period["prev_start_iso"]), parse_date(period["prev_end_iso"]))
    pending = conn.execute(
        """SELECT (SELECT COUNT(*) FROM leave_requests WHERE status = 'pending')
                + (SELECT COUNT(*) FROM expenses WHERE status = 'pending')
                + (SELECT COUNT(*) FROM timesheet_corrections WHERE status = 'pending') AS c"""
    ).fetchone()["c"]
    rev_delta = _pct_change(now["revenue_gross"], prev["revenue_gross"])
    # Gross, so that revenue − refunds − expenses − labour reaches net on
    # screen. Showing the net-of-refunds figure beside a refunds line made it
    # look as though the refunds still had to come off.
    return [
        overview_cell("Revenue", euro(now["revenue_gross"]), hint="before refunds",
                      delta=f"{rev_delta:+g}%" if rev_delta is not None else None),
        overview_cell("Refunds issued", euro(now["refunds_total"]),
                      alert=now["refunds_total"]),
        overview_cell("Expenses", euro(now["expenses_total"])),
        overview_cell("Labour", euro(now["labour_cost"] or 0),
                      hint="estimate" if now["labour_cost"] is not None else "no rates on file"),
        overview_cell("Net", euro(now["net"]), alert=now["net"] < 0),
        overview_cell("Awaiting a decision", pending, alert=pending),
    ]


def restaurant_overview(conn, period, today):
    """Service over the chosen window — covers rather than reservations, since
    covers are what the kitchen actually has to cook for."""
    row = conn.execute(
        """SELECT COUNT(*) AS reservations,
                  COALESCE(SUM(party_size), 0) AS covers,
                  COALESCE(SUM(total_price), 0) AS revenue
           FROM restaurant_bookings
           WHERE status = 'confirmed' AND dinner_date >= ? AND dinner_date < ?""",
        (period["start_iso"], period["end_iso"]),
    ).fetchone()
    prev_covers = conn.execute(
        """SELECT COALESCE(SUM(party_size), 0) AS covers FROM restaurant_bookings
           WHERE status = 'confirmed' AND dinner_date >= ? AND dinner_date < ?""",
        (period["prev_start_iso"], period["prev_end_iso"]),
    ).fetchone()["covers"]
    refunded = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS t FROM refunds
           WHERE category = 'restaurant' AND created_at >= ? AND created_at < ?""",
        (period["start_iso"], period["end_iso"]),
    ).fetchone()["t"]
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status = 'pending'"
    ).fetchone()["c"]
    no_shows = conn.execute(
        """SELECT COUNT(*) AS c FROM restaurant_bookings
           WHERE no_show_at IS NOT NULL AND dinner_date >= ? AND dinner_date < ?""",
        (period["start_iso"], period["end_iso"]),
    ).fetchone()["c"]
    return [
        overview_cell("Covers", row["covers"],
                      delta=row["covers"] - prev_covers if prev_covers else None),
        overview_cell("Reservations", row["reservations"]),
        overview_cell("Revenue", euro((row["revenue"] or 0) - refunded),
                      hint="net of refunds"),
        overview_cell("Awaiting a decision", pending, alert=pending),
        overview_cell("No-shows", no_shows, alert=no_shows),
    ]


def workshops_overview(conn, period, today):
    """Sessions that run inside the window, and how full they are."""
    sessions = conn.execute(
        """SELECT id, capacity FROM workshop_sessions
           WHERE start_date < ? AND end_date >= ?""",
        (period["end_iso"], period["start_iso"]),
    ).fetchall()
    session_ids = [s["id"] for s in sessions]
    capacity = sum(s["capacity"] or 0 for s in sessions)
    seats, revenue = 0, 0.0
    if session_ids:
        marks = ",".join("?" * len(session_ids))
        row = conn.execute(
            f"""SELECT COALESCE(SUM(party_size), 0) AS seats,
                       COALESCE(SUM(total_price), 0) AS revenue
                FROM workshop_bookings
                WHERE status = 'confirmed' AND session_id IN ({marks})""",
            session_ids,
        ).fetchone()
        seats, revenue = row["seats"], row["revenue"] or 0.0
    refunded = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS t FROM refunds
           WHERE category = 'workshop' AND created_at >= ? AND created_at < ?""",
        (period["start_iso"], period["end_iso"]),
    ).fetchone()["t"]
    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM workshop_bookings WHERE status = 'pending'"
    ).fetchone()["c"]
    fill = round(seats / capacity * 100, 1) if capacity else 0
    return [
        overview_cell("Sessions running", len(sessions)),
        overview_cell("Seats sold", seats),
        overview_cell("Seats free", max(0, capacity - seats)),
        overview_cell("Full", fill, sub="%"),
        overview_cell("Revenue", euro(revenue - refunded), hint="net of refunds"),
        overview_cell("Awaiting a decision", pending, alert=pending),
    ]


def management_overview(conn, period, today):
    """Management holds records rather than transactions, so this band is
    condition-driven — what has expired or is about to — rather than a count of
    the window's activity, which would always read zero."""
    soon = (today + timedelta(days=60)).isoformat()
    iso = today.isoformat()
    insurance = conn.execute(
        """SELECT COUNT(*) AS c FROM insurance_policies
           WHERE expiry_date IS NOT NULL AND expiry_date < ?""", (soon,),
    ).fetchone()["c"]
    documents = conn.execute(
        """SELECT COUNT(*) AS c FROM company_documents
           WHERE expiry_date IS NOT NULL AND expiry_date < ?""", (soon,),
    ).fetchone()["c"]
    vehicles_due = conn.execute(
        """SELECT COUNT(*) AS c FROM vehicles
           WHERE (next_service_due IS NOT NULL AND next_service_due < ?)
              OR fuel_level = 'low'""", (soon,),
    ).fetchone()["c"]
    vehicle_faults = conn.execute(
        "SELECT COUNT(*) AS c FROM vehicle_maintenance WHERE status = 'open'"
    ).fetchone()["c"]
    flags = conn.execute(
        "SELECT COUNT(*) AS c FROM email_flags WHERE status = 'open'"
    ).fetchone()["c"]
    recurring = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN frequency = 'annual' THEN amount / 12.0"
        "                         ELSE amount END), 0) AS t"
        "  FROM recurring_costs WHERE active = 1"
    ).fetchone()["t"]
    return [
        overview_cell("Insurance expiring", insurance, alert=insurance,
                      hint="within 60 days"),
        overview_cell("Documents expiring", documents, alert=documents,
                      hint="within 60 days"),
        overview_cell("Vehicles needing attention", vehicles_due, alert=vehicles_due),
        overview_cell("Open vehicle faults", vehicle_faults, alert=vehicle_faults),
        overview_cell("Open inbox flags", flags, alert=flags),
        overview_cell("Recurring costs", euro(recurring), sub="/mo"),
    ]


def stays_with_status(conn, today, statuses=("confirmed",)):
    """Every stay, derived from `bookings` -- the single source of truth for who
    is physically at the château. Replaces the old `guests`-table register, which
    duplicated these dates and drifted out of sync (a cancelled booking left its
    guest row showing "in residence" indefinitely).

    Defaults to confirmed-only because that is what "someone is actually here"
    means; pending bookings hold a room but nobody has arrived, and they surface
    separately as an item awaiting a decision.

    Each row carries the guest's profile fields (notes/dietary/VIP) where one is
    linked, so callers can show standing preferences alongside the current stay.
    """
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT bookings.id AS booking_id, bookings.guest_name AS name,
                   bookings.guest_email AS email, bookings.arrival_date,
                   bookings.departure_date, bookings.party_size,
                   bookings.special_requests, bookings.reference_code,
                   bookings.status, rooms.name AS room_name,
                   guests.id AS profile_id, guests.notes AS profile_notes,
                   guests.dietary_notes, guests.vip
            FROM bookings
            JOIN rooms ON rooms.id = bookings.room_id
            LEFT JOIN guests ON guests.id = bookings.linked_guest_id
            WHERE bookings.status IN ({placeholders})
            ORDER BY bookings.arrival_date, bookings.guest_name""",
        tuple(statuses),
    ).fetchall()

    stays = []
    for r in rows:
        arrival, departure = parse_date(r["arrival_date"]), parse_date(r["departure_date"])
        if departure and departure <= today:
            status, label = "past", "Past stay"
        elif arrival and arrival > today:
            status, label = "upcoming", "Upcoming"
        else:
            status, label = "current", "In residence"
        # Anything the guest asked for on this booking, plus anything standing
        # on their profile -- staff shouldn't have to open two pages.
        #
        # The migration folded each retired register entry into the profile note
        # as "[Former register entry: <old dates>]". Those lines are history and
        # must not ride along here: shown beside the stay they'd put a second,
        # contradictory date range in front of staff with nothing marking which
        # is stale. They stay readable on the profile itself.
        profile_notes = "\n".join(
            line for line in (r["profile_notes"] or "").splitlines()
            if not line.strip().startswith("[Former register entry:")
        ).strip() or None
        note_parts = [p for p in (r["special_requests"], profile_notes, r["dietary_notes"]) if p]
        stays.append({
            "booking_id": r["booking_id"], "profile_id": r["profile_id"],
            "name": r["name"], "email": r["email"], "room_name": r["room_name"],
            "arrival_date": r["arrival_date"], "departure_date": r["departure_date"],
            "party_size": r["party_size"], "reference_code": r["reference_code"],
            "notes": " · ".join(note_parts) or None, "vip": bool(r["vip"]),
            "stay_status": status, "stay_status_label": label,
        })
    return stays


def guests_in_residence(conn, today):
    """Just the stays covering today -- the common case for every 'who's here'
    panel across the app."""
    return [s for s in stays_with_status(conn, today) if s["stay_status"] == "current"]


def build_task_sheet(conn, view, anchor):
    """Shared by the Home dashboard (week view) and the full /admin/tasks
    page (day/week/month) so both render identically."""
    if view == "day":
        range_start = anchor
        range_end = anchor + timedelta(days=1)
    elif view == "month":
        range_start = anchor.replace(day=1)
        range_end = date(range_start.year + 1, 1, 1) if range_start.month == 12 else date(range_start.year, range_start.month + 1, 1)
    else:
        view = "week"
        range_start = anchor - timedelta(days=anchor.weekday())
        range_end = range_start + timedelta(days=7)

    employees = conn.execute("SELECT * FROM users WHERE role = 'employee' ORDER BY name").fetchall()
    # LEFT JOIN, not JOIN — assigned_to_user_id can be NULL (unassigned task),
    # and an inner join would silently drop those rows from every view.
    ranged_tasks = conn.execute(
        """SELECT tasks.*, users.name AS employee_name FROM tasks
           LEFT JOIN users ON users.id = tasks.assigned_to_user_id
           WHERE due_date >= ? AND due_date < ? ORDER BY due_date, users.name""",
        (range_start.isoformat(), range_end.isoformat()),
    ).fetchall()
    undated_tasks = conn.execute(
        """SELECT tasks.*, users.name AS employee_name FROM tasks
           LEFT JOIN users ON users.id = tasks.assigned_to_user_id
           WHERE due_date IS NULL AND tasks.status != 'done' ORDER BY users.name"""
    ).fetchall()

    by_employee = {}
    unassigned_tasks = []
    for t in ranged_tasks:
        if t["assigned_to_user_id"] is None:
            unassigned_tasks.append(t)
            continue
        entry = by_employee.setdefault(t["assigned_to_user_id"], {"name": t["employee_name"], "tasks": []})
        entry["tasks"].append(t)

    month_grid = None
    if view == "month":
        days = [range_start + timedelta(days=i) for i in range((range_end - range_start).days)]
        counts = {}
        for t in ranged_tasks:
            per_day = counts.setdefault(t["assigned_to_user_id"], {})
            per_day[t["due_date"]] = per_day.get(t["due_date"], 0) + 1
        month_grid = {
            "days": days,
            "rows": [
                {"employee": e, "cells": [counts.get(e["id"], {}).get(d.isoformat(), 0) for d in days]}
                for e in employees
            ],
        }

    week_days = None
    if view == "week":
        week_days = []
        for i in range(7):
            d = range_start + timedelta(days=i)
            week_days.append({
                "date": d,
                "tasks": [t for t in ranged_tasks if t["due_date"] == d.isoformat()],
            })

    if view == "day":
        prev_anchor, next_anchor = anchor - timedelta(days=1), anchor + timedelta(days=1)
    elif view == "month":
        prev_anchor = (range_start - timedelta(days=1)).replace(day=1)
        next_anchor = range_end
    else:
        prev_anchor, next_anchor = anchor - timedelta(days=7), anchor + timedelta(days=7)

    return {
        "view": view, "anchor": anchor, "range_start": range_start,
        "range_end": range_end - timedelta(days=1), "by_employee": by_employee,
        "unassigned_tasks": unassigned_tasks, "undated_tasks": undated_tasks,
        "employees": employees, "month_grid": month_grid, "week_days": week_days,
        "prev_anchor": prev_anchor.isoformat(), "next_anchor": next_anchor.isoformat(),
    }


def _overview_task_range(conn, range_start, range_end):
    """The task query shared by build_overview() and the status-refresh
    endpoint — kept identical so the live-poll only ever touches rows the
    page actually rendered."""
    return conn.execute(
        """SELECT tasks.*, users.name AS employee_name FROM tasks
           LEFT JOIN users ON users.id = tasks.assigned_to_user_id
           WHERE (due_date >= ? AND due_date < ?) OR (due_date IS NULL AND tasks.status != 'done')
           ORDER BY due_date IS NULL, due_date, tasks.priority""",
        (range_start.isoformat(), range_end.isoformat()),
    ).fetchall()


def build_overview(conn, view, anchor, fetch_window=None):
    """The unified ops feed — bookings and staff tasks in one filterable
    list for the given date window. Lane/guest/origin/employee filters run
    client-side against data-attributes on each row (instant, no reload);
    only the date range itself is a server round-trip, matching the
    tasks/calendar pages elsewhere in the app.

    `fetch_window` widens ONLY the data query, leaving the reported range and
    prev/next anchors alone. The calendar page uses it so a month grid can
    also fill the leading/trailing days it borrows from the adjacent months —
    those cells would otherwise render deceptively empty rather than blank.
    """
    if view == "day":
        range_start = anchor
        range_end = anchor + timedelta(days=1)
    elif view == "month":
        range_start = anchor.replace(day=1)
        range_end = date(range_start.year + 1, 1, 1) if range_start.month == 12 else date(range_start.year, range_start.month + 1, 1)
    else:
        view = "week"
        range_start = anchor - timedelta(days=anchor.weekday())
        range_end = range_start + timedelta(days=7)

    # Everything below queries against the fetch window; the view's own
    # range_start/range_end still drive labelling and prev/next.
    if fetch_window:
        query_start, query_end = fetch_window
    else:
        query_start, query_end = range_start, range_end

    owner_row = conn.execute("SELECT id, name FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    owner_id = owner_row["id"] if owner_row else None
    owner_name = owner_row["name"] if owner_row else None
    employees = conn.execute("SELECT * FROM users WHERE role = 'employee' ORDER BY name").fetchall()

    rows = []
    for t in _overview_task_range(conn, query_start, query_end):
        is_owner_task = owner_id is not None and t["assigned_to_user_id"] == owner_id
        rows.append({
            "kind": "task",
            "lane": "owner" if is_owner_task else "task",
            "is_guest": bool(t["room_note"]),
            "origin": t["origin"] or "manual",
            "scheduled": (t["origin"] or "manual") in ("checklist", "recurring"),
            "status": t["status"],
            "acknowledgment_status": t["acknowledgment_status"],
            "priority": t["priority"],
            "repeat_weekly": t["repeat_weekly"],
            "date": t["due_date"],
            "title": t["title"], "short": t["title"],
            "detail": t["room_note"] or t["notes"] or "",
            "assignee_id": t["assigned_to_user_id"],
            "assignee_name": t["employee_name"],
            "id": t["id"], "link": None,
        })

    bookings = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id
           WHERE status IN ('pending','confirmed')
             AND ((arrival_date >= ? AND arrival_date < ?) OR (departure_date >= ? AND departure_date < ?))""",
        (query_start.isoformat(), query_end.isoformat(), query_start.isoformat(), query_end.isoformat()),
    ).fetchall()
    for b in bookings:
        booking_link = url_for("admin_bookings", q=b["reference_code"])
        if query_start.isoformat() <= b["arrival_date"] < query_end.isoformat():
            arrival_detail = f"Party of {b['party_size']}"
            if b["special_requests"]:
                arrival_detail += f" · {b['special_requests']}"
            if b["estimated_arrival_time"]:
                arrival_detail += f" · ETA {b['estimated_arrival_time']}"
            if b["transfer_flight_number"] or b["transfer_arrival_time"]:
                arrival_detail += f" · ✈ {b['transfer_flight_number'] or 'flight tbc'}" + (f" landing {b['transfer_arrival_time']}" if b["transfer_arrival_time"] else "")
            rows.append({
                "kind": "booking", "lane": "booking", "is_guest": True, "origin": "booking",
                "scheduled": True, "status": b["status"], "acknowledgment_status": None,
                "priority": None, "repeat_weekly": 0, "date": b["arrival_date"],
                "title": f"{b['guest_name']} arrives — {b['room_name']}",
                "short": f"↓ {b['guest_name']}",
                "detail": arrival_detail,
                "assignee_id": None, "assignee_name": None, "id": b["id"], "link": booking_link,
            })
        if query_start.isoformat() <= b["departure_date"] < query_end.isoformat():
            rows.append({
                "kind": "booking", "lane": "booking", "is_guest": True, "origin": "booking",
                "scheduled": True, "status": b["status"], "acknowledgment_status": None,
                "priority": None, "repeat_weekly": 0, "date": b["departure_date"],
                "title": f"{b['guest_name']} departs — {b['room_name']}",
                "short": f"↑ {b['guest_name']}",
                "detail": f"Party of {b['party_size']}",
                "assignee_id": None, "assignee_name": None, "id": b["id"], "link": booking_link,
            })

    dinners = conn.execute(
        """SELECT * FROM restaurant_bookings WHERE status IN ('pending', 'confirmed')
           AND dinner_date >= ? AND dinner_date < ?""",
        (query_start.isoformat(), query_end.isoformat()),
    ).fetchall()
    for d in dinners:
        rows.append({
            "kind": "dinner", "lane": "restaurant", "is_guest": True, "origin": "booking",
            "scheduled": True, "status": d["status"], "acknowledgment_status": None,
            "priority": None, "repeat_weekly": 0, "date": d["dinner_date"],
            "title": f"Dinner — {d['guest_name']}, party of {d['party_size']}",
            "short": f"{d['guest_name']} ×{d['party_size']}",
            "detail": d["dietary_notes"] or "",
            "assignee_id": None, "assignee_name": None, "id": d["id"],
            "link": url_for("admin_restaurant"),
        })

    workshop_regs = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshops.title FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.status IN ('pending', 'confirmed')
             AND workshop_sessions.start_date >= ? AND workshop_sessions.start_date < ?""",
        (query_start.isoformat(), query_end.isoformat()),
    ).fetchall()
    for r in workshop_regs:
        rows.append({
            "kind": "workshop", "lane": "workshop", "is_guest": True, "origin": "booking",
            "scheduled": True, "status": r["status"], "acknowledgment_status": None,
            "priority": None, "repeat_weekly": 0, "date": r["start_date"],
            "title": f"{r['title']} — {r['guest_name']}, party of {r['party_size']}",
            "short": r["title"],
            "detail": r["notes"] or "",
            "assignee_id": None, "assignee_name": None, "id": r["id"],
            "link": url_for("admin_workshop_registrations"),
        })

    rows.sort(key=lambda r: r["date"] or "9999-99-99")

    if view == "day":
        prev_anchor, next_anchor = anchor - timedelta(days=1), anchor + timedelta(days=1)
    elif view == "month":
        prev_anchor = (range_start - timedelta(days=1)).replace(day=1)
        next_anchor = range_end
    else:
        prev_anchor, next_anchor = anchor - timedelta(days=7), anchor + timedelta(days=7)

    return {
        "view": view, "anchor": anchor, "range_start": range_start,
        "range_end": range_end - timedelta(days=1), "rows": rows,
        "employees": employees, "owner_id": owner_id, "owner_name": owner_name,
        "prev_anchor": prev_anchor.isoformat(), "next_anchor": next_anchor.isoformat(),
    }


def build_calendar(conn, view, anchor, viewer=None):
    """The main ops calendar: every task, arrival, departure, dinner and
    workshop laid out on actual dates, in day / week / month.

    Built on build_overview so the calendar and the Overview list can never
    disagree about what's happening — same rows, same links, just arranged
    on a grid instead of in a feed. The dashboard's month widget stays a
    read-only glance; this is the one you work in.
    """
    if view not in ("day", "week", "month"):
        view = "week"


    if view == "day":
        span_start, span_end = anchor, anchor + timedelta(days=1)
        grid_start, grid_end = span_start, span_end
    elif view == "week":
        span_start = anchor - timedelta(days=anchor.weekday())
        span_end = span_start + timedelta(days=7)
        grid_start, grid_end = span_start, span_end
    else:
        span_start = anchor.replace(day=1)
        span_end = (date(span_start.year + 1, 1, 1) if span_start.month == 12
                    else date(span_start.year, span_start.month + 1, 1))
        # Pad out to whole Monday-start weeks so the grid is rectangular.
        grid_start = span_start - timedelta(days=span_start.weekday())
        trailing = (7 - span_end.weekday()) % 7
        grid_end = span_end + timedelta(days=trailing)

    sheet = build_overview(conn, view, anchor, fetch_window=(grid_start, grid_end))

    rows = sheet["rows"]
    # build_overview is an owner-scoped feed: it returns every task in the
    # window, including the owner's own private ones. This page is open to
    # employees too, so anyone who isn't the owner sees only the operational
    # picture -- guest arrivals/departures, dinners, workshops -- plus the
    # tasks actually assigned to them. Never the owner's personal lane.
    if viewer is not None and viewer["role"] != "owner":
        rows = [
            r for r in rows
            if r["lane"] != "owner"
            and (r["kind"] != "task" or r["assignee_id"] == viewer["id"])
        ]
        # Every link build_overview attaches points at an @owner_required page
        # (admin_bookings, admin_restaurant, admin_workshop_registrations), so
        # an employee clicking anything on their own calendar hit a 403 —
        # including their own tasks. Send tasks to the dashboard, where their
        # "What I Need To Do" list actually lives, and leave the rest as plain
        # information rather than links into pages they can't open.
        rows = [dict(r, link=(url_for("dashboard") if r["kind"] == "task" else None)) for r in rows]

    by_date = {}
    for row in rows:
        if row["date"]:
            by_date.setdefault(row["date"], []).append(row)

    today = datetime.now(timezone.utc).date()
    cells = []
    d = grid_start
    while d < grid_end:
        events = by_date.get(d.isoformat(), [])
        cells.append({
            "date": d,
            "iso": d.isoformat(),
            "in_span": span_start <= d < span_end,
            "is_today": d == today,
            "is_weekend": d.weekday() >= 5,
            "events": events,
            "open_count": sum(1 for e in events if e["kind"] != "task" or e["status"] != "done"),
        })
        d += timedelta(days=1)

    weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)] if view == "month" else [cells]

    return {
        "view": view, "anchor": anchor, "cells": cells, "weeks": weeks,
        "span_start": span_start, "span_end": span_end - timedelta(days=1),
        "prev_anchor": sheet["prev_anchor"], "next_anchor": sheet["next_anchor"],
        "employees": sheet["employees"], "owner_id": sheet["owner_id"],
        "total_events": sum(len(c["events"]) for c in cells if c["in_span"]),
    }


DEFAULT_ONBOARDING_ITEMS = [
    "Collect ID / right-to-work documents",
    "Send onboarding link (set password)",
    "Review and sign employment agreement",
    "Issue keys / property access",
    "Add to payroll",
    "Introduce to the rest of the team",
    "Show around the property",
]


def seed_onboarding_checklist(conn, user_id):
    now = datetime.now(timezone.utc).isoformat()
    for i, label in enumerate(DEFAULT_ONBOARDING_ITEMS):
        conn.execute(
            "INSERT INTO onboarding_items (user_id, label, sort_order, created_at) VALUES (?, ?, ?, ?)",
            (user_id, label, i, now),
        )


DEFAULT_OFFBOARDING_ITEMS = [
    "Collect keys / property access",
    "Revoke portal login (deactivate account)",
    "Return uniform / equipment",
    "Settle final pay / expenses",
    "Remove from shift schedule",
    "Exit conversation",
]


def seed_offboarding_checklist(conn, user_id):
    now = datetime.now(timezone.utc).isoformat()
    for i, label in enumerate(DEFAULT_OFFBOARDING_ITEMS):
        conn.execute(
            "INSERT INTO offboarding_items (user_id, label, sort_order, created_at) VALUES (?, ?, ?, ?)",
            (user_id, label, i, now),
        )


def maybe_seed_offboarding(conn, user_id, old_status, new_status):
    """Seeds the offboarding checklist the first time someone goes
    active -> inactive. Guarded so re-saving the form while already
    inactive (or flipping back and forth) never seeds it twice."""
    if old_status == "active" and new_status == "inactive":
        existing = conn.execute(
            "SELECT 1 FROM offboarding_items WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()
        if not existing:
            seed_offboarding_checklist(conn, user_id)
        # A generic "return keys" line doesn't say WHICH keys, so the one that
        # never comes back is the one nobody remembered was issued. Add a real
        # line per item the leaver is actually holding. Codes get different
        # wording: a code they already know can't be handed back, it has to be
        # changed.
        now_iso = datetime.now(timezone.utc).isoformat()
        for h in access_held_by(conn, user_id):
            label = (f"Change the {h['label']} code — they already know it"
                     if h["kind"] in ("code", "alarm")
                     else f"Recover: {h['label']}"
                          + (f" (kept in {h['location']})" if h["location"] else ""))
            already = conn.execute(
                "SELECT 1 FROM offboarding_items WHERE user_id = ? AND label = ?",
                (user_id, label),
            ).fetchone()
            if not already:
                conn.execute(
                    """INSERT INTO offboarding_items (user_id, label, done, sort_order, created_at)
                       VALUES (?, ?, 0, 0, ?)""",
                    (user_id, label, now_iso),
                )


def create_draft_agreement(conn, employee):
    """Writes a plain-text employment summary — reference only, NOT a binding
    contract. It exists so there's a starting point for your lawyer/accountant
    to turn into the real signed agreement, not to replace them."""
    lines = [
        f"DRAFT EMPLOYMENT SUMMARY — {EMPLOYER_LEGAL_NAME}",
        "=" * 60,
        "This is an internal reference draft only. It is NOT a legally",
        "binding employment contract and creates no obligations on its own.",
        "Have it reviewed and turned into a real signed agreement by your",
        "lawyer/accountant before it is relied on in any way.",
        "",
        f"Employer:     {EMPLOYER_LEGAL_NAME}",
        f"Employee:     {employee['name']}",
        f"Role:         {employee['job_role'] or '(not set)'}",
        f"Start date:   {employee['start_date'] or '(not set)'}",
        f"Pay rate:     {employee['pay_rate'] or '(not set)'}",
        f"Pay type:     {employee['pay_type'] or '(not set)'}",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat()} for internal reference only.",
    ]
    stored_name = f"{employee['id']}_{secrets.token_hex(6)}_draft_employment_summary.txt"
    with open(os.path.join(UPLOAD_DIR, stored_name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    conn.execute(
        "INSERT INTO documents (user_id, title, filename, uploaded_at) VALUES (?, ?, ?, ?)",
        (employee["id"], "Draft employment summary (needs lawyer review — not binding)",
         stored_name, datetime.now(timezone.utc).isoformat()),
    )


# ---------------------------------------------------------------------------
# Booking engine helpers
#
# No third-party iCal library — the app is deliberately dependency-light (see
# module docstring), and the subset of RFC 5545 that Airbnb/Booking.com/VRBO
# actually export (flat VEVENTs, no recurrence rules) is small enough to
# parse by hand.
# ---------------------------------------------------------------------------

def ical_unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    unfolded = []
    for line in text.split("\n"):
        if line.startswith(" ") or line.startswith("\t"):
            if unfolded:
                unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _ical_date(value):
    m = re.match(r"(\d{8})", value.strip())
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def parse_ical_ranges(text):
    """Returns a list of (start_date, end_date_exclusive) blocked ranges."""
    ranges = []
    in_event = False
    dtstart = dtend = None
    for line in ical_unfold(text):
        upper = line.upper()
        if upper.startswith("BEGIN:VEVENT"):
            in_event, dtstart, dtend = True, None, None
            continue
        if upper.startswith("END:VEVENT"):
            if dtstart and dtend:
                ranges.append((dtstart, dtend))
            elif dtstart:
                ranges.append((dtstart, dtstart + timedelta(days=1)))
            in_event = False
            continue
        if not in_event or ":" not in line:
            continue
        prop, _, value = line.partition(":")
        prop_name = prop.split(";", 1)[0].upper()
        if prop_name == "DTSTART":
            dtstart = _ical_date(value)
        elif prop_name == "DTEND":
            dtend = _ical_date(value)
    return ranges


def fetch_ical_ranges(url, timeout=8):
    """Best-effort fetch — network/format problems are reported, never raised,
    so a broken external feed can't take down the booking page."""
    try:
        req = Request(url, headers={"User-Agent": "GudanesHR-iCal-Sync/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return parse_ical_ranges(raw.decode("utf-8", errors="replace")), None
    except Exception as e:
        return None, str(e)


def sync_ical_source(conn, source):
    """Fetches the feed and replaces this source's blocked_dates wholesale.
    Replacing rather than merging is what makes re-imports safe to repeat:
    a range that's disappeared from the feed (cancelled on the other side)
    is simply not re-inserted, and a range that's still there never gets
    duplicated. We diff against the old rows first purely to log what moved,
    not to decide what to keep."""
    now = datetime.now(timezone.utc).isoformat()
    old_set = {
        (r["start_date"], r["end_date"])
        for r in conn.execute(
            "SELECT start_date, end_date FROM blocked_dates WHERE ical_source_id = ?",
            (source["id"],),
        ).fetchall()
    }

    ranges, error = fetch_ical_ranges(source["url"])
    if error:
        conn.execute(
            "UPDATE ical_sources SET last_synced_at = ?, last_sync_error = ? WHERE id = ?",
            (now, error, source["id"]),
        )
        conn.execute(
            """INSERT INTO ical_sync_log
               (ical_source_id, room_id, ran_at, success, added, removed, unchanged, error)
               VALUES (?, ?, ?, 0, 0, 0, 0, ?)""",
            (source["id"], source["room_id"], now, error),
        )
        conn.commit()
        return False

    new_set = {(start.isoformat(), end.isoformat()) for start, end in ranges}
    added = len(new_set - old_set)
    removed = len(old_set - new_set)
    unchanged = len(new_set & old_set)

    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id = ?", (source["id"],))
    for start_iso, end_iso in new_set:
        conn.execute(
            "INSERT INTO blocked_dates (room_id, ical_source_id, start_date, end_date) VALUES (?, ?, ?, ?)",
            (source["room_id"], source["id"], start_iso, end_iso),
        )
    conn.execute(
        "UPDATE ical_sources SET last_synced_at = ?, last_sync_error = NULL WHERE id = ?",
        (now, source["id"]),
    )
    conn.execute(
        """INSERT INTO ical_sync_log
           (ical_source_id, room_id, ran_at, success, added, removed, unchanged, error)
           VALUES (?, ?, ?, 1, ?, ?, ?, NULL)""",
        (source["id"], source["room_id"], now, added, removed, unchanged),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Promo codes — one code table shared across rooms, restaurant, and
# workshops (events are bespoke/owner-quoted, so a code doesn't fit that
# flow). Every call site recomputes the discount fresh from the code and
# the real current price rather than trusting a client-supplied amount —
# same "never trust the client, recompute server-side" discipline as
# compute_room_total below.
# ---------------------------------------------------------------------------

def find_promo_code(conn, code):
    if not code or not code.strip():
        return None
    return conn.execute("SELECT * FROM promo_codes WHERE code = ? COLLATE NOCASE", (code.strip(),)).fetchone()


def compute_promo_discount(subtotal, promo):
    """The euro amount a promo row knocks off subtotal — never negative,
    never more than subtotal itself, and capped by max_discount_amount for
    percent-based codes if one is set (e.g. '20% off, up to €200')."""
    if promo["discount_type"] == "percent":
        discount = subtotal * (promo["discount_value"] / 100)
    else:
        discount = promo["discount_value"]
    if promo["max_discount_amount"]:
        discount = min(discount, promo["max_discount_amount"])
    return round(max(0.0, min(discount, subtotal)), 2)


def validate_promo_code(conn, code, category, subtotal):
    """Checks a code against everything that could make it unusable right
    now — active, in-date, right category, under its redemption cap, meets
    any minimum spend — and returns (promo_row_or_None, discount_amount,
    error_message_or_None). category is 'room', 'restaurant', or
    'workshop'. Callers should treat a validation failure as 'no discount
    applied', not as a reason to block the booking itself — a promo code
    is a nice-to-have, not something that should stop a real booking going
    through if it's expired or mistyped."""
    promo = find_promo_code(conn, code)
    if not promo:
        return None, 0.0, "That code isn't recognized."
    if not promo["active"]:
        return None, 0.0, "That code is no longer active."
    today_iso = datetime.now(timezone.utc).date().isoformat()
    if promo["valid_from"] and today_iso < promo["valid_from"]:
        return None, 0.0, "That code isn't active yet."
    if promo["valid_until"] and today_iso > promo["valid_until"]:
        return None, 0.0, "That code has expired."
    if promo["applies_to"] != "all" and promo["applies_to"] != category:
        return None, 0.0, "That code doesn't apply to this kind of booking."
    if promo["max_redemptions"] is not None and promo["redemption_count"] >= promo["max_redemptions"]:
        return None, 0.0, "That code has already been fully redeemed."
    if promo["min_spend"] and subtotal < promo["min_spend"]:
        return None, 0.0, f"That code needs a minimum of €{promo['min_spend']:.2f}."
    return promo, compute_promo_discount(subtotal, promo), None


def record_promo_redemption(conn, promo, category, booking_reference, guest_email, original_amount, discount_amount):
    conn.execute(
        """INSERT INTO promo_code_redemptions
           (promo_code_id, category, booking_reference, guest_email, original_amount, discount_amount, final_amount, redeemed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (promo["id"], category, booking_reference, guest_email, original_amount, discount_amount,
         round(original_amount - discount_amount, 2), datetime.now(timezone.utc).isoformat()),
    )
    conn.execute("UPDATE promo_codes SET redemption_count = redemption_count + 1 WHERE id = ?", (promo["id"],))


def room_night_rate(conn, room, night_date):
    """The rate for one specific night — a date-range override (e.g. peak
    season) if one covers that night, otherwise the room's flat
    price_per_night. Overrides are matched by range, not exact date, so a
    single 'July-August' row prices a whole season without one row per
    night; the most recently created override wins if two ever overlap.
    end_date is inclusive (an override 'Aug 1 to Aug 31' covers the night
    of the 31st itself) — matching restaurant_night_rate's convention,
    since these describe calendar date ranges an owner picks in a date
    field, not a checkin/checkout pair where the departure day is
    exclusive by hotel convention."""
    override = conn.execute(
        """SELECT price_per_night FROM room_rate_overrides
           WHERE room_id = ? AND start_date <= ? AND end_date >= ? ORDER BY id DESC LIMIT 1""",
        (room["id"], night_date.isoformat(), night_date.isoformat()),
    ).fetchone()
    return override["price_per_night"] if override else room["price_per_night"]


def compute_room_total(conn, room, arrival, departure):
    """Sums the per-night rate across a stay. This is the single source of
    truth for a room-only total (excludes extras) — every code path that
    prices a stay (search results, the booking form, Stripe checkout,
    admin manual edits) calls this instead of doing
    room['price_per_night'] * nights directly, so a seasonal rate override
    is honoured everywhere a price is shown or charged."""
    if not room["price_per_night"]:
        return 0
    total = 0.0
    night = arrival
    while night < departure:
        total += room_night_rate(conn, room, night)
        night += timedelta(days=1)
    return round(total, 2)


def is_range_available(conn, room_id, arrival, departure, exclude_booking_id=None, include_pending=True):
    """arrival/departure are date objects; departure is the checkout day
    (exclusive), so a checkout and another guest's check-in on the same day
    do not count as an overlap — standard hotel convention.

    include_pending controls whether other still-pending requests count as
    blockers. It should stay True for the public booking flow (don't let a
    new guest request dates someone else already has an unconfirmed request
    on). It must be False when re-validating at *confirm* time, since two
    rival pending requests for the same dates is the normal case confirming
    is meant to resolve — the sibling pending request isn't a real conflict
    until something actually gets confirmed."""
    if departure <= arrival:
        return False, "Departure must be after arrival."

    statuses = "('pending','confirmed')" if include_pending else "('confirmed')"
    query = f"""SELECT id, arrival_date, departure_date FROM bookings
               WHERE room_id = ? AND status IN {statuses}"""
    params = [room_id]
    if exclude_booking_id:
        query += " AND id != ?"
        params.append(exclude_booking_id)
    for row in conn.execute(query, params).fetchall():
        b_start, b_end = parse_date(row["arrival_date"]), parse_date(row["departure_date"])
        if b_start and b_end and arrival < b_end and b_start < departure:
            return False, "Those dates overlap an existing booking."

    for row in conn.execute(
        "SELECT start_date, end_date FROM blocked_dates WHERE room_id = ?", (room_id,)
    ).fetchall():
        b_start, b_end = parse_date(row["start_date"]), parse_date(row["end_date"])
        if b_start and b_end and arrival < b_end and b_start < departure:
            return False, "Those dates are blocked on another booking channel."

    for row in conn.execute(
        "SELECT start_date, end_date FROM room_blocks WHERE room_id = ?", (room_id,)
    ).fetchall():
        b_start, b_end = parse_date(row["start_date"]), parse_date(row["end_date"])
        if b_start and b_end and arrival < b_end and b_start < departure:
            return False, "Those dates aren't available."

    # A workshop/retreat takes over the whole château for its run, not just
    # one room — so every room is unavailable for the duration of any
    # scheduled session, the same way a manual block would be.
    for row in conn.execute(
        "SELECT workshop_sessions.start_date, workshop_sessions.end_date, workshops.title "
        "FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id"
    ).fetchall():
        w_start, w_end = parse_date(row["start_date"]), parse_date(row["end_date"])
        if w_start and w_end and arrival <= w_end and w_start < departure:
            return False, f"Those dates are held for a workshop ({row['title']})."

    # A confirmed event (wedding, photoshoot, etc.) takes over the château
    # for that day the same way a workshop does — event_inquiries only ever
    # stores a single preferred_date, not a range, so this blocks just that
    # one day rather than a multi-day span.
    for row in conn.execute(
        "SELECT preferred_date, event_type FROM event_inquiries WHERE status = 'confirmed' AND preferred_date IS NOT NULL"
    ).fetchall():
        e_date = parse_date(row["preferred_date"])
        if e_date and arrival <= e_date < departure:
            return False, f"That date is held for a confirmed event ({row['event_type']})."

    return True, None


def matching_waitlist_entries(conn, arrival_iso, departure_iso):
    """Open/contacted waitlist entries whose desired range overlaps the
    given dates — not room-specific, since a waitlist request is for
    'anything available', so this is a nudge to go check, not a guarantee
    the freed room fits their party size."""
    arrival, departure = parse_date(arrival_iso), parse_date(departure_iso)
    if not arrival or not departure:
        return []
    matches = []
    for row in conn.execute(
        "SELECT * FROM waitlist_entries WHERE status IN ('open', 'contacted') "
        "AND desired_arrival IS NOT NULL AND desired_departure IS NOT NULL"
    ).fetchall():
        w_start, w_end = parse_date(row["desired_arrival"]), parse_date(row["desired_departure"])
        if w_start and w_end and arrival < w_end and w_start < departure:
            matches.append(row)
    return matches


def compute_month_stats(conn, today):
    """Occupancy and revenue for the calendar month `today` falls in.
    Revenue is attributed by arrival date (simple, not prorated across
    month boundaries for multi-month stays) — a reasonable approximation
    for a small property, not an accounting-grade figure."""
    month_start = today.replace(day=1)
    month_end = date(month_start.year + 1, 1, 1) if month_start.month == 12 else date(month_start.year, month_start.month + 1, 1)
    days_in_month = (month_end - month_start).days

    room_count = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]

    overlapping = conn.execute(
        """SELECT arrival_date, departure_date FROM bookings
           WHERE status = 'confirmed' AND room_id IN (SELECT id FROM rooms WHERE active = 1)
           AND arrival_date < ? AND departure_date > ?""",
        (month_end.isoformat(), month_start.isoformat()),
    ).fetchall()
    booked_nights = 0
    for b in overlapping:
        b_start, b_end = parse_date(b["arrival_date"]), parse_date(b["departure_date"])
        if not b_start or not b_end:
            continue
        overlap_start, overlap_end = max(b_start, month_start), min(b_end, month_end)
        booked_nights += max(0, (overlap_end - overlap_start).days)

    total_possible_nights = days_in_month * room_count
    occupancy_rate = round(booked_nights / total_possible_nights * 100) if total_possible_nights else 0

    revenue = conn.execute(
        """SELECT COALESCE(SUM(total_price), 0) AS total FROM bookings
           WHERE status = 'confirmed' AND arrival_date >= ? AND arrival_date < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    # Net off room refunds issued this month, so the dashboard headline agrees
    # with the financials page rather than quietly overstating the month.
    revenue -= conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM refunds
           WHERE category = 'room' AND created_at >= ? AND created_at < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["total"]
    revenue = round(revenue, 2)

    arrivals_30d = conn.execute(
        """SELECT COUNT(*) AS c FROM bookings WHERE status = 'confirmed'
           AND arrival_date >= ? AND arrival_date < ?""",
        (today.isoformat(), (today + timedelta(days=30)).isoformat()),
    ).fetchone()["c"]

    return {
        "month_name": month_start.strftime("%B"),
        "occupancy_rate": occupancy_rate,
        "revenue": revenue,
        "arrivals_30d": arrivals_30d,
    }


def build_dashboard_calendar(conn, today):
    """A month-grid summary (Monday-start weeks, like a real wall calendar)
    for the dashboard: per day, room arrivals/departures, dinner covers,
    and whether a workshop session is running — a higher-level 'what's
    happening this month' view, distinct from the per-room grid on the
    full Booking Calendar page. Leading/trailing None cells pad the first
    and last weeks out to a whole 7-day row."""
    month_start = today.replace(day=1)
    month_end = date(month_start.year + 1, 1, 1) if month_start.month == 12 else date(month_start.year, month_start.month + 1, 1)
    days_in_month = [month_start + timedelta(days=i) for i in range((month_end - month_start).days)]

    arrivals_by_date = {
        r["arrival_date"]: r["c"] for r in conn.execute(
            "SELECT arrival_date, COUNT(*) AS c FROM bookings WHERE status IN ('pending','confirmed') "
            "AND arrival_date >= ? AND arrival_date < ? GROUP BY arrival_date",
            (month_start.isoformat(), month_end.isoformat()),
        ).fetchall()
    }
    departures_by_date = {
        r["departure_date"]: r["c"] for r in conn.execute(
            "SELECT departure_date, COUNT(*) AS c FROM bookings WHERE status IN ('pending','confirmed') "
            "AND departure_date >= ? AND departure_date < ? GROUP BY departure_date",
            (month_start.isoformat(), month_end.isoformat()),
        ).fetchall()
    }
    dinner_covers_by_date = {
        r["dinner_date"]: r["covers"] for r in conn.execute(
            """SELECT dinner_date, COALESCE(SUM(party_size), 0) AS covers FROM restaurant_bookings
               WHERE status IN ('pending', 'confirmed') AND dinner_date >= ? AND dinner_date < ?
               GROUP BY dinner_date""",
            (month_start.isoformat(), month_end.isoformat()),
        ).fetchall()
    }
    workshop_dates = set()
    for s in conn.execute(
        """SELECT start_date, end_date FROM workshop_sessions
           WHERE start_date < ? AND end_date >= ?""",
        (month_end.isoformat(), month_start.isoformat()),
    ).fetchall():
        s_start, s_end = parse_date(s["start_date"]), parse_date(s["end_date"])
        d = max(s_start, month_start)
        while d <= min(s_end, month_end - timedelta(days=1)):
            workshop_dates.add(d.isoformat())
            d += timedelta(days=1)

    cells = []
    for d in days_in_month:
        key = d.isoformat()
        cells.append({
            "date": d,
            "arrivals": arrivals_by_date.get(key, 0),
            "departures": departures_by_date.get(key, 0),
            "dinner_covers": dinner_covers_by_date.get(key, 0),
            "workshop": key in workshop_dates,
        })

    # Pad to a Monday-start grid: leading blanks for days before the 1st,
    # trailing blanks to complete the final week.
    leading = month_start.weekday()  # Monday=0
    weeks = []
    week = [None] * leading + cells
    while len(week) % 7 != 0:
        week.append(None)
    for i in range(0, len(week), 7):
        weeks.append(week[i:i + 7])

    return {"weeks": weeks, "month_start": month_start}


def build_office_display_queues(conn, today):
    """Consolidated 'needs attention' items for the office TV kiosk display --
    one entry per source table, each with a live count and a short preview
    list, so nothing pending sits unseen on a page nobody happened to open."""
    recent_30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    queues = []

    leave = conn.execute(
        """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id
           WHERE leave_requests.status = 'pending'
           ORDER BY leave_requests.requested_at"""
    ).fetchall()
    queues.append({
        "key": "leave", "label": "Leave requests", "link": url_for("admin_approvals"),
        "count": len(leave),
        "preview": [f"{r['employee_name']} — {r['start_date']} to {r['end_date']}" for r in leave[:4]],
    })

    expenses = conn.execute(
        """SELECT expenses.*, users.name AS submitter_name FROM expenses
           LEFT JOIN users ON users.id = expenses.submitted_by_user_id
           WHERE expenses.status = 'pending'
           ORDER BY expenses.submitted_at"""
    ).fetchall()
    queues.append({
        "key": "expenses", "label": "Expenses", "link": url_for("admin_approvals"),
        "count": len(expenses),
        "preview": [f"{r['submitter_name'] or r['vendor_name'] or 'Unknown'} — €{r['amount']:.2f}" for r in expenses[:4]],
    })

    corrections = conn.execute(
        """SELECT timesheet_corrections.*, users.name AS employee_name FROM timesheet_corrections
           JOIN users ON users.id = timesheet_corrections.user_id
           WHERE timesheet_corrections.status = 'pending'
           ORDER BY timesheet_corrections.created_at"""
    ).fetchall()
    queues.append({
        "key": "timesheet", "label": "Timesheet corrections", "link": url_for("admin_timesheet_corrections"),
        "count": len(corrections),
        "preview": [f"{r['employee_name']} — {(r['note'] or '')[:40]}" for r in corrections[:4]],
    })

    pending_rooms = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id
           WHERE bookings.status = 'pending' ORDER BY bookings.arrival_date"""
    ).fetchall()
    queues.append({
        "key": "pending_rooms", "label": "Room bookings to confirm", "link": url_for("admin_bookings"),
        "count": len(pending_rooms),
        "preview": [f"{r['guest_name']} — {r['arrival_date']} · {r['room_name']}" for r in pending_rooms[:4]],
    })

    pending_dinners = conn.execute(
        "SELECT * FROM restaurant_bookings WHERE status = 'pending' ORDER BY dinner_date"
    ).fetchall()
    queues.append({
        "key": "pending_dinners", "label": "Dinners to confirm", "link": url_for("admin_restaurant"),
        "count": len(pending_dinners),
        "preview": [f"{r['guest_name']} — {r['dinner_date']} · party of {r['party_size']}" for r in pending_dinners[:4]],
    })

    pending_workshops = conn.execute(
        """SELECT workshop_bookings.*, workshops.title FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.status = 'pending' ORDER BY workshop_sessions.start_date"""
    ).fetchall()
    queues.append({
        "key": "pending_workshops", "label": "Workshop regs to confirm", "link": url_for("admin_workshop_registrations"),
        "count": len(pending_workshops),
        "preview": [f"{r['guest_name']} — {r['title']}" for r in pending_workshops[:4]],
    })

    # Vehicle condition/service/overdue-checkout alerts -- same four signals the
    # interactive dashboard surfaces, collapsed into one tile here.
    service_soon = (today + timedelta(days=30)).isoformat()
    vehicle_alerts = (
        [f"{v['name']} — dirty" for v in conn.execute(
            "SELECT name FROM vehicles WHERE cleanliness = 'dirty' ORDER BY name").fetchall()]
        + [f"{v['name']} — low fuel" for v in conn.execute(
            "SELECT name FROM vehicles WHERE fuel_level = 'low' ORDER BY name").fetchall()]
        + [f"{v['name']} — service due {v['next_service_due']}" for v in conn.execute(
            "SELECT name, next_service_due FROM vehicles WHERE next_service_due IS NOT NULL "
            "AND next_service_due <= ? ORDER BY next_service_due", (service_soon,)).fetchall()]
        + [f"{c['vehicle_name']} — out with {c['user_name'] or 'unknown'}"
           for c in overdue_vehicle_checkouts(conn)]
    )
    queues.append({
        "key": "vehicles", "label": "Vehicles", "link": url_for("management_vehicles"),
        "count": len(vehicle_alerts), "preview": vehicle_alerts[:4],
    })

    low_stock = conn.execute(
        "SELECT name FROM breakfast_items WHERE low_stock = 1 ORDER BY name"
    ).fetchall()
    queues.append({
        "key": "stock", "label": "Low stock", "link": url_for("breakfast"),
        "count": len(low_stock),
        "preview": [r["name"] for r in low_stock[:4]],
    })

    balances_due = conn.execute(
        """SELECT workshop_bookings.guest_name, workshop_bookings.balance_amount,
                  workshop_bookings.balance_due_date FROM workshop_bookings
           WHERE status = 'confirmed' AND balance_amount > 0 AND balance_paid_at IS NULL
             AND balance_due_date IS NOT NULL AND balance_due_date <= ?
           ORDER BY balance_due_date""",
        ((today + timedelta(days=7)).isoformat(),),
    ).fetchall()
    queues.append({
        "key": "balances", "label": "Balances due (7d)", "link": url_for("admin_workshop_registrations"),
        "count": len(balances_due),
        "preview": [f"{r['guest_name']} — €{r['balance_amount']:.2f} by {r['balance_due_date']}" for r in balances_due[:4]],
    })

    low_feedback = conn.execute(
        """SELECT guest_feedback.*, bookings.reference_code FROM guest_feedback
           LEFT JOIN bookings ON bookings.id = guest_feedback.booking_id
           WHERE rating <= 2 AND submitted_at >= ? ORDER BY submitted_at DESC""",
        (recent_30,),
    ).fetchall()
    queues.append({
        "key": "feedback", "label": "Low feedback (30d)", "link": url_for("admin_feedback"),
        "count": len(low_feedback),
        "preview": [f"{r['guest_name']} — {r['rating']}★" for r in low_feedback[:4]],
    })

    inbox = conn.execute(
        "SELECT subject, from_name FROM email_flags WHERE status = 'open' ORDER BY received_at DESC"
    ).fetchall()
    queues.append({
        "key": "inbox", "label": "Inbox flags", "link": url_for("admin_inbox_flags"),
        "count": len(inbox),
        "preview": [f"{r['from_name'] or 'Unknown'} — {r['subject'] or '(no subject)'}" for r in inbox[:4]],
    })

    events = conn.execute(
        "SELECT * FROM event_inquiries WHERE status = 'new' ORDER BY created_at"
    ).fetchall()
    queues.append({
        "key": "events", "label": "New event inquiries", "link": url_for("admin_events"),
        "count": len(events),
        "preview": [f"{r['contact_name']} — {r['preferred_date'] or 'date TBC'}" for r in events[:4]],
    })

    # Only hand back what's actually firing. A wallboard showing a dozen
    # permanent "All clear" boxes trains you to ignore the whole band -- the
    # empty state belongs on the section, not on every category.
    return [q for q in queues if q["count"]]


def build_office_display_stats(conn, today, who_is_here):
    """The always-on numbers for the kiosk's top strip -- today's occupancy and
    covers, as opposed to the Needs Attention band below it which only appears
    when something is actually wrong.

    `who_is_here` is passed in rather than recomputed so the headcount here is
    the exact same source as the Current Guests list further down the page --
    this app keeps a manual `guests` register separate from the `bookings`
    table, and deriving the two from different tables let them contradict each
    other on the same screen.
    """
    iso = today.isoformat()
    active = "('pending','confirmed')"

    # Confirmed-only and active-rooms-only, to match both `who_is_here` (which
    # is confirmed-only) and the denominator below (active-only). Previously
    # this counted pending bookings in ANY room against an active-rooms total,
    # so the board could read "1 room occupied / 0 guests in residence" for the
    # same day, and a booking in a deactivated room could push the numerator
    # above the total.
    occupied = conn.execute(
        """SELECT COUNT(*) AS c FROM bookings
           WHERE status = 'confirmed'
             AND room_id IN (SELECT id FROM rooms WHERE active = 1)
             AND arrival_date <= ? AND departure_date > ?""",
        (iso, iso),
    ).fetchone()["c"]
    rooms_total = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
    arrivals = conn.execute(
        f"SELECT COUNT(*) AS c FROM bookings WHERE status IN {active} AND arrival_date = ?", (iso,),
    ).fetchone()["c"]
    departures = conn.execute(
        f"SELECT COUNT(*) AS c FROM bookings WHERE status IN {active} AND departure_date = ?", (iso,),
    ).fetchone()["c"]
    # Named to avoid shadowing the module-level guests_in_residence() helper,
    # which would silently break any later call added inside this function.
    guest_headcount = sum((g["party_size"] or 1) for g in who_is_here)
    dinner_covers = conn.execute(
        f"SELECT COALESCE(SUM(party_size), 0) AS c FROM restaurant_bookings "
        f"WHERE status IN {active} AND dinner_date = ?", (iso,),
    ).fetchone()["c"]
    dinner_tables = conn.execute(
        f"SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status IN {active} AND dinner_date = ?", (iso,),
    ).fetchone()["c"]

    return [
        {"key": "occupancy", "label": "Rooms occupied", "value": occupied,
         "sub": f"of {rooms_total}" if rooms_total else None, "link": url_for("admin_calendar")},
        {"key": "guests", "label": "Guests in residence", "value": guest_headcount,
         "sub": f"{len(who_is_here)} part{'y' if len(who_is_here) == 1 else 'ies'}" if who_is_here else None,
         "link": url_for("guests")},
        {"key": "arrivals", "label": "Arrivals today", "value": arrivals,
         "sub": None, "link": url_for("admin_bookings")},
        {"key": "departures", "label": "Departures today", "value": departures,
         "sub": None, "link": url_for("admin_bookings")},
        {"key": "dinners", "label": "Dinner covers tonight", "value": dinner_covers,
         "sub": f"{dinner_tables} booking{'' if dinner_tables == 1 else 's'}" if dinner_tables else None,
         "link": url_for("admin_restaurant")},
    ]


# ---------------------------------------------------------------------------
# HR: certifications, availability, absence, working-time compliance, reviews
# ---------------------------------------------------------------------------

CERT_EXPIRY_WARNING_DAYS = 60

# French working-time defaults. Deliberately configurable constants rather than
# magic numbers buried in a query -- these are the numbers most likely to need
# changing for a different contract type or country, and the owner should be
# able to find them.
MIN_REST_HOURS_BETWEEN_SHIFTS = 11
MAX_CONSECUTIVE_DAYS_WORKED = 6
MAX_WEEKLY_HOURS = 48


# Every kind of HR item that can sit waiting on somebody, in one place.
#   actor  — who has to do something: "owner" (approve/act) or "subject" (the
#            employee themselves, e.g. acknowledging their own review)
#   sla    — days before that person is reminded
#   escalate — days before it goes over their head to the owner
#
# Deliberately data rather than nine hand-written checks: adding a tenth kind
# of HR item should be one entry here plus one query, not another bespoke
# reminder scattered through the codebase.
HR_ACTION_TYPES = {
    "leave_request":       {"label": "Leave request awaiting a decision", "actor": "owner",   "sla": 3,  "escalate": 7},
    "expense":             {"label": "Expense awaiting approval",          "actor": "owner",   "sla": 5,  "escalate": 14},
    "timesheet_correction":{"label": "Timesheet correction to review",     "actor": "owner",   "sla": 3,  "escalate": 7},
    "hr_note":             {"label": "Ask HR message unanswered",          "actor": "owner",   "sla": 2,  "escalate": 5},
    "review_unacknowledged":{"label": "Review shared but not acknowledged","actor": "subject", "sla": 7,  "escalate": 21},
    "review_overdue":      {"label": "No performance review in 12 months", "actor": "owner",   "sla": 30, "escalate": 60},
    "return_to_work":      {"label": "Return-to-work not completed",       "actor": "owner",   "sla": 5,  "escalate": 14},
    "certification_expiry":{"label": "Certification expiring or expired",  "actor": "owner",   "sla": 30, "escalate": 60},
    "document_expiry":     {"label": "Employee document expiring",         "actor": "owner",   "sla": 30, "escalate": 60},
    # Both of these confirm themselves by default if nobody acts, which is why
    # they are flagged well before the date rather than on it.
    "trial_period_ending": {"label": "Trial period ending — decide before it confirms",
                            "actor": "owner", "sla": 14, "escalate": 21},
    "contract_ending":     {"label": "Fixed-term contract ending — renew or end it",
                            "actor": "owner", "sla": 30, "escalate": 45},
}

# What an employment contract can be. CDI/CDD are the French default pair; the
# rest cover how this estate actually staffs itself across the season.
CONTRACT_TYPES = {
    "cdi": "CDI — permanent",
    "cdd": "CDD — fixed term",
    "seasonal": "Seasonal",
    "apprentice": "Apprenticeship",
    "freelance": "Freelance / service provider",
}

# How far ahead a trial period or a fixed term starts being flagged. Trial
# periods get the longer runway because letting one lapse is irreversible.
TRIAL_WARNING_DAYS = 21
CONTRACT_WARNING_DAYS = 45

INCIDENT_KINDS = {
    "workplace": "Workplace accident (staff)",
    "guest": "Guest incident",
    "property": "Damage to property",
    "near_miss": "Near miss",
}
INCIDENT_SEVERITIES = {
    "near_miss": "Near miss — nobody hurt",
    "minor": "Minor — first aid at most",
    "significant": "Significant — medical attention",
    "serious": "Serious — hospital or time off work",
}
ACCESS_KINDS = {
    "key": "Key",
    "code": "Gate / door code",
    "alarm": "Alarm code",
    "fob": "Fob or card",
    "vehicle_key": "Vehicle key",
    "other": "Other",
}


def role_compliance(conn, today):
    """Who does not hold what their job role requires.

    Certifications and documents were already tracked per person, but nothing
    said what a ROLE demands — so the system could answer "what expires soon"
    and never "who cannot legally work this week". Requirements are matched on
    name, case-insensitively, because that is how they are actually typed in.

    Each person gets one row per unmet requirement, with `state` one of
    missing / expired / expiring, worst first.
    """
    reqs = conn.execute("SELECT * FROM role_requirements ORDER BY job_role, requirement").fetchall()
    if not reqs:
        return []
    by_role = {}
    for r in reqs:
        by_role.setdefault((r["job_role"] or "").strip().lower(), []).append(r)

    people = conn.execute(
        "SELECT id, name, job_role FROM users WHERE role = 'employee' AND status = 'active'"
    ).fetchall()
    horizon = (today + timedelta(days=30)).isoformat()
    out = []
    for p in people:
        needed = by_role.get((p["job_role"] or "").strip().lower())
        if not needed:
            continue
        certs = {(" ".join((c["name"] or "").split())).lower(): c for c in conn.execute(
            "SELECT name, expiry_date FROM certifications WHERE user_id = ?", (p["id"],)).fetchall()}
        docs = {(" ".join((d["title"] or "").split())).lower(): d for d in conn.execute(
            "SELECT title, expiry_date FROM documents WHERE user_id = ?", (p["id"],)).fetchall()}
        for req in needed:
            pool = certs if req["requirement_type"] == "certification" else docs
            held = pool.get((" ".join((req["requirement"] or "").split())).lower())
            if not held:
                state, detail = "missing", "not on file"
            elif held["expiry_date"] and held["expiry_date"] < today.isoformat():
                state, detail = "expired", f"expired {held['expiry_date']}"
            elif held["expiry_date"] and held["expiry_date"] <= horizon:
                state, detail = "expiring", f"expires {held['expiry_date']}"
            else:
                continue
            out.append({"user_id": p["id"], "name": p["name"], "job_role": p["job_role"],
                        "requirement": req["requirement"],
                        "requirement_type": req["requirement_type"],
                        "state": state, "detail": detail})
    order = {"missing": 0, "expired": 1, "expiring": 2}
    return sorted(out, key=lambda x: (order[x["state"]], x["name"]))


def access_held_by(conn, user_id):
    """What one person currently holds — the real answer offboarding needs."""
    return conn.execute(
        """SELECT access_holdings.*, access_items.label, access_items.kind, access_items.location
           FROM access_holdings JOIN access_items ON access_items.id = access_holdings.access_item_id
           WHERE access_holdings.user_id = ? AND access_holdings.returned_at IS NULL
           ORDER BY access_items.kind, access_items.label""",
        (user_id,),
    ).fetchall()


def payroll_period_rows(conn, period):
    """Per-employee payroll figures for one window, plus what makes them unsafe
    to send.

    The labour estimate elsewhere is explicitly not payroll-grade. This is the
    hand-off to whoever actually runs payroll, so it carries its own blockers:
    an impossible shift or a missing hourly rate is reported per person and the
    export refuses rather than quietly sending a wrong number to the accountant.
    """
    start_iso, end_iso = period["start_iso"], period["end_iso"]
    rows = []
    for p in conn.execute(
        """SELECT id, name, job_role, pay_rate, pay_type, contract_type
           FROM users WHERE role = 'employee' AND status = 'active' ORDER BY name"""
    ).fetchall():
        hours = conn.execute(
            """SELECT COALESCE(SUM(
                   (julianday(clock_out_at) - julianday(clock_in_at)) * 24
                   - COALESCE((SELECT SUM((julianday(breaks.end_at) - julianday(breaks.start_at)) * 24)
                               FROM breaks WHERE breaks.time_entry_id = time_entries.id
                                 AND breaks.end_at IS NOT NULL), 0)), 0) AS h,
                  COUNT(*) AS shifts
               FROM time_entries
               WHERE user_id = ? AND clock_out_at IS NOT NULL AND clock_out_at > clock_in_at
                 AND clock_in_at >= ? AND clock_in_at < ?""",
            (p["id"], start_iso, end_iso),
        ).fetchone()
        broken = conn.execute(
            """SELECT COUNT(*) AS c FROM time_entries
               WHERE user_id = ? AND clock_out_at IS NOT NULL AND clock_out_at < clock_in_at
                 AND clock_in_at >= ? AND clock_in_at < ?""",
            (p["id"], start_iso, end_iso),
        ).fetchone()["c"]
        absence_days = conn.execute(
            """SELECT COALESCE(SUM(julianday(MIN(end_date, ?)) - julianday(MAX(start_date, ?)) + 1), 0) AS d
               FROM absences WHERE user_id = ? AND start_date < ? AND end_date >= ?""",
            (period["end_iso"], start_iso, p["id"], end_iso, start_iso),
        ).fetchone()["d"]
        leave_days = conn.execute(
            """SELECT COALESCE(SUM(julianday(MIN(end_date, ?)) - julianday(MAX(start_date, ?)) + 1), 0) AS d
               FROM leave_requests
               WHERE user_id = ? AND status = 'approved' AND start_date < ? AND end_date >= ?""",
            (period["end_iso"], start_iso, p["id"], end_iso, start_iso),
        ).fetchone()["d"]

        worked = round(hours["h"], 2)
        cost = estimated_hourly_cost(worked, p["pay_rate"], p["pay_type"])
        blockers = []
        if broken:
            blockers.append(f"{broken} impossible shift{'s' if broken != 1 else ''}")
        if worked > 0 and cost is None:
            blockers.append("no usable hourly rate on file")
        rows.append({
            "user_id": p["id"], "name": p["name"], "job_role": p["job_role"],
            "contract_type": CONTRACT_TYPES.get(p["contract_type"], p["contract_type"] or "—"),
            "hours": worked, "shifts": hours["shifts"],
            "absence_days": int(absence_days), "leave_days": int(leave_days),
            "pay_rate": p["pay_rate"], "cost": cost, "blockers": blockers,
        })
    return rows


def contract_fields_from_form():
    """The employment-terms fields, read and normalised in one place so the new
    and edit paths can never drift apart. Dates are stored only when they parse;
    a fixed-term end date on a permanent contract is dropped rather than kept as
    a deadline that would then be chased for no reason."""
    contract_type = request.form.get("contract_type", "").strip() or None
    if contract_type not in CONTRACT_TYPES:
        contract_type = None
    def _d(field):
        raw = request.form.get(field, "").strip()
        return raw if parse_date(raw) else None
    contract_end = _d("contract_end_date")
    if contract_type in (None, "cdi"):
        contract_end = None
    notice = request.form.get("notice_period_days", "").strip()
    return (contract_type, contract_end, _d("trial_end_date"),
            int(notice) if notice.isdigit() else None)


def contract_deadlines(conn, today):
    """Trial periods and fixed terms coming up (or already passed).

    Returns one row per person per deadline, with `days_left` negative once the
    date is behind us — a lapsed trial period is more urgent than an upcoming
    one, not less, so it must not be filtered out.
    """
    out = []
    rows = conn.execute(
        """SELECT id, name, job_role, contract_type, contract_end_date, trial_end_date
           FROM users WHERE role = 'employee' AND status = 'active'"""
    ).fetchall()
    for r in rows:
        trial = parse_date(r["trial_end_date"])
        if trial and (trial - today).days <= TRIAL_WARNING_DAYS:
            out.append({"kind": "trial", "user_id": r["id"], "name": r["name"],
                        "job_role": r["job_role"], "on_date": trial,
                        "days_left": (trial - today).days,
                        "contract_type": r["contract_type"]})
        end = parse_date(r["contract_end_date"])
        if end and (end - today).days <= CONTRACT_WARNING_DAYS:
            out.append({"kind": "contract", "user_id": r["id"], "name": r["name"],
                        "job_role": r["job_role"], "on_date": end,
                        "days_left": (end - today).days,
                        "contract_type": r["contract_type"]})
    return sorted(out, key=lambda x: x["on_date"])


def hr_escalation_rules(conn):
    """Per-type thresholds, seeded from the defaults above on first use so the
    owner can tune them without touching code."""
    now_iso = datetime.now(timezone.utc).isoformat()
    known = {r["item_type"] for r in conn.execute("SELECT item_type FROM hr_escalation_rules").fetchall()}
    for item_type, cfg in HR_ACTION_TYPES.items():
        if item_type not in known:
            conn.execute(
                """INSERT OR IGNORE INTO hr_escalation_rules
                   (item_type, sla_days, escalate_after_days, active, updated_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (item_type, cfg["sla"], cfg["escalate"], now_iso),
            )
    conn.commit()
    return {r["item_type"]: r for r in conn.execute("SELECT * FROM hr_escalation_rules").fetchall()}


def collect_hr_actions(conn, today):
    """Everything across HR currently waiting on somebody, in one shape:
    {type, item_id, subject_user_id, summary, since, link}.

    `since` is when the clock started — the date the thing became somebody's
    problem, not when it was noticed.
    """
    iso = today.isoformat()
    out = []

    def add(t, item_id, user_id, summary, since, link):
        out.append({"type": t, "item_id": str(item_id), "subject_user_id": user_id,
                    "summary": summary, "since": since, "link": link})

    for r in conn.execute(
        """SELECT leave_requests.*, users.name AS n FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id WHERE leave_requests.status = 'pending'"""
    ).fetchall():
        add("leave_request", r["id"], r["user_id"],
            f"{r['n']} — {r['start_date']} to {r['end_date']}", (r["requested_at"] or "")[:10], "/admin/approvals")

    for r in conn.execute(
        """SELECT expenses.*, users.name AS n FROM expenses
           LEFT JOIN users ON users.id = expenses.submitted_by_user_id WHERE expenses.status = 'pending'"""
    ).fetchall():
        add("expense", r["id"], r["submitted_by_user_id"],
            f"{r['n'] or r['vendor_name'] or 'Unknown'} — €{r['amount']:.2f}", (r["submitted_at"] or "")[:10], "/admin/approvals")

    for r in conn.execute(
        """SELECT timesheet_corrections.*, users.name AS n FROM timesheet_corrections
           JOIN users ON users.id = timesheet_corrections.user_id WHERE timesheet_corrections.status = 'pending'"""
    ).fetchall():
        add("timesheet_correction", r["id"], r["user_id"],
            f"{r['n']} — {(r['note'] or '')[:50]}", (r["created_at"] or "")[:10], "/admin/timesheet-corrections")

    try:
        for r in conn.execute(
            """SELECT hr_notes.*, users.name AS n FROM hr_notes
               JOIN users ON users.id = hr_notes.user_id WHERE hr_notes.status = 'open'"""
        ).fetchall():
            add("hr_note", r["id"], r["user_id"], f"{r['n']} — {(r['body'] or '')[:50]}",
                (r["created_at"] or "")[:10], "/admin/hr-notes")
    except sqlite3.OperationalError:
        pass

    for r in conn.execute(
        """SELECT performance_reviews.*, users.name AS n FROM performance_reviews
           JOIN users ON users.id = performance_reviews.user_id
           WHERE performance_reviews.status = 'shared'"""
    ).fetchall():
        add("review_unacknowledged", r["id"], r["user_id"], f"{r['n']} — review of {r['review_date']}",
            (r["shared_at"] or r["review_date"] or "")[:10], "/admin/hr")

    year_ago = (today - timedelta(days=365)).isoformat()
    for r in conn.execute(
        """SELECT users.id, users.name, users.start_date FROM users
           WHERE users.role = 'employee' AND users.status = 'active'
             AND users.id NOT IN (SELECT user_id FROM performance_reviews WHERE review_date >= ?)""",
        (year_ago,),
    ).fetchall():
        # Clock starts a year after they joined, not the day they joined.
        started = parse_date(r["start_date"]) if r["start_date"] else None
        since = ((started + timedelta(days=365)).isoformat() if started else year_ago)
        if since <= iso:
            add("review_overdue", r["id"], r["id"], r["name"], since, "/admin/hr")

    for r in conn.execute(
        """SELECT absences.*, users.name AS n FROM absences
           JOIN users ON users.id = absences.user_id
           WHERE absences.return_to_work_done_at IS NULL AND absences.end_date <= ?
             AND absences.kind IN ('sick','unauthorised')""",
        (iso,),
    ).fetchall():
        add("return_to_work", r["id"], r["user_id"], f"{r['n']} — back since {r['end_date']}",
            r["end_date"], "/admin/hr")

    horizon = (today + timedelta(days=CERT_EXPIRY_WARNING_DAYS)).isoformat()
    for r in conn.execute(
        """SELECT certifications.*, users.name AS n FROM certifications
           JOIN users ON users.id = certifications.user_id
           WHERE certifications.expiry_date IS NOT NULL AND certifications.expiry_date <= ?
             AND users.status = 'active'""",
        (horizon,),
    ).fetchall():
        add("certification_expiry", r["id"], r["user_id"],
            f"{r['n']} — {r['name']}{' (required)' if r['required'] else ''}, expires {r['expiry_date']}",
            r["expiry_date"], "/admin/hr")

    for r in conn.execute(
        """SELECT documents.*, users.name AS n FROM documents
           JOIN users ON users.id = documents.user_id
           WHERE documents.expiry_date IS NOT NULL AND documents.expiry_date <= ?
             AND users.status = 'active'""",
        (horizon,),
    ).fetchall():
        add("document_expiry", r["id"], r["user_id"],
            f"{r['n']} — {r['title']}, expires {r['expiry_date']}", r["expiry_date"], f"/directory/{r['user_id']}")

    # Contract deadlines. `since` is the date the warning window opened, not the
    # deadline itself — the escalation clock should start when there was first
    # something to do, otherwise a trial period only becomes "overdue" after it
    # has already confirmed, which is exactly too late to be useful.
    for d in contract_deadlines(conn, today):
        if d["kind"] == "trial":
            opened = d["on_date"] - timedelta(days=TRIAL_WARNING_DAYS)
            add("trial_period_ending", f"trial-{d['user_id']}", d["user_id"],
                f"{d['name']} — trial period ends {d['on_date'].isoformat()}"
                + (" (already passed)" if d["days_left"] < 0 else f" ({d['days_left']}d)"),
                max(opened, parse_date("2000-01-01")).isoformat(), f"/directory/{d['user_id']}")
        else:
            opened = d["on_date"] - timedelta(days=CONTRACT_WARNING_DAYS)
            add("contract_ending", f"contract-{d['user_id']}", d["user_id"],
                f"{d['name']} — {CONTRACT_TYPES.get(d['contract_type'], 'contract')} ends "
                f"{d['on_date'].isoformat()}"
                + (" (already passed)" if d["days_left"] < 0 else f" ({d['days_left']}d)"),
                max(opened, parse_date("2000-01-01")).isoformat(), f"/directory/{d['user_id']}")

    return out


def run_hr_escalation_job(conn):
    """Age every outstanding HR item, remind whoever owes the action, and
    escalate to the owner if it keeps sitting there.

    The point is that assigning something isn't the same as it being done.
    Reminders fire once and escalations fire once, so this is a prompt rather
    than a daily nag, and anything that gets dealt with is closed off
    automatically rather than needing to be dismissed by hand.
    """
    today = datetime.now(timezone.utc).date()
    now_iso = datetime.now(timezone.utc).isoformat()
    rules = hr_escalation_rules(conn)
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    if not owner_row:
        return "no owner account"
    owner_id = owner_row["id"]

    actions = collect_hr_actions(conn, today)
    live_keys = {(a["type"], a["item_id"]) for a in actions}

    # Anything previously outstanding that no longer is has been dealt with.
    closed = 0
    for row in conn.execute(
        "SELECT id, item_type, item_id FROM hr_escalations WHERE resolved_at IS NULL"
    ).fetchall():
        if (row["item_type"], row["item_id"]) not in live_keys:
            conn.execute("UPDATE hr_escalations SET resolved_at = ? WHERE id = ?", (now_iso, row["id"]))
            closed += 1

    reminded = escalated = 0
    for a in actions:
        cfg = HR_ACTION_TYPES.get(a["type"])
        rule = rules.get(a["type"])
        if not cfg or not rule or not rule["active"]:
            continue
        since = parse_date((a["since"] or "")[:10]) or today
        age_days = (today - since).days

        existing = conn.execute(
            "SELECT * FROM hr_escalations WHERE item_type = ? AND item_id = ?",
            (a["type"], a["item_id"]),
        ).fetchone()
        if not existing:
            conn.execute(
                """INSERT INTO hr_escalations (item_type, item_id, subject_user_id, summary,
                   due_at, first_seen_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (a["type"], a["item_id"], a["subject_user_id"], a["summary"],
                 (since + timedelta(days=rule["sla_days"])).isoformat(), now_iso),
            )
            existing = conn.execute(
                "SELECT * FROM hr_escalations WHERE item_type = ? AND item_id = ?",
                (a["type"], a["item_id"]),
            ).fetchone()
        elif existing["resolved_at"]:
            # came back (e.g. a certificate renewed then expired again)
            conn.execute("UPDATE hr_escalations SET resolved_at = NULL, reminded_at = NULL, "
                         "escalated_at = NULL WHERE id = ?", (existing["id"],))
            existing = conn.execute("SELECT * FROM hr_escalations WHERE id = ?", (existing["id"],)).fetchone()

        # who owes the action
        target = owner_id if cfg["actor"] == "owner" else (a["subject_user_id"] or owner_id)

        if age_days >= rule["escalate_after_days"] and not existing["escalated_at"]:
            send_notification(
                conn, owner_id, "hr_escalation",
                f"Overdue {age_days}d: {cfg['label']}",
                body=f"{a['summary']} — still outstanding after {age_days} days.", link=a["link"],
            )
            conn.execute("UPDATE hr_escalations SET escalated_at = ? WHERE id = ?", (now_iso, existing["id"]))
            escalated += 1
        elif age_days >= rule["sla_days"] and not existing["reminded_at"]:
            send_notification(
                conn, target, "hr_reminder",
                f"{cfg['label']} ({age_days}d)",
                body=a["summary"], link=a["link"],
            )
            conn.execute("UPDATE hr_escalations SET reminded_at = ? WHERE id = ?", (now_iso, existing["id"]))
            reminded += 1

    conn.commit()
    return (f"{len(actions)} HR item(s) outstanding, {reminded} reminded, "
            f"{escalated} escalated, {closed} closed")


def expiring_certifications(conn, today, within_days=CERT_EXPIRY_WARNING_DAYS):
    """Certifications already expired or expiring soon, worst first. `required`
    ones sort above the nice-to-haves because those are the ones that stop
    someone legally doing their job."""
    horizon = (today + timedelta(days=within_days)).isoformat()
    rows = conn.execute(
        """SELECT certifications.*, users.name AS employee_name, users.status AS employee_status
           FROM certifications JOIN users ON users.id = certifications.user_id
           WHERE certifications.expiry_date IS NOT NULL
             AND certifications.expiry_date <= ?
             AND users.status = 'active'
           ORDER BY certifications.required DESC, certifications.expiry_date""",
        (horizon,),
    ).fetchall()
    out = []
    for r in rows:
        expiry = parse_date(r["expiry_date"])
        days_left = (expiry - today).days if expiry else None
        out.append(dict(r, days_left=days_left, expired=days_left is not None and days_left < 0))
    return out


def availability_for(conn, user_id, on_date):
    """Is this person normally free on this date? Returns (available, note).

    A one-off exception always beats the weekly pattern -- that's the whole
    point of it. No rule at all means "nobody has said", which is treated as
    available rather than blocking the rota on missing data.
    """
    iso = on_date.isoformat() if hasattr(on_date, "isoformat") else str(on_date)
    exc = conn.execute(
        "SELECT available, note FROM availability_exceptions WHERE user_id = ? AND on_date = ?",
        (user_id, iso),
    ).fetchone()
    if exc:
        return bool(exc["available"]), exc["note"]
    weekday = (parse_date(iso) or on_date).weekday()
    rule = conn.execute(
        "SELECT available, note, from_time, to_time FROM availability_rules WHERE user_id = ? AND weekday = ?",
        (user_id, weekday),
    ).fetchone()
    if rule:
        note = rule["note"]
        if rule["available"] and (rule["from_time"] or rule["to_time"]):
            note = f"{rule['from_time'] or '?'}–{rule['to_time'] or '?'}" + (f" · {note}" if note else "")
        return bool(rule["available"]), note
    return True, None


def unavailable_assigned_shifts(conn, from_date, to_date):
    """Shifts assigned to someone who said they're not free that day. This is
    the whole point of collecting availability -- otherwise the rota is built
    blind and the clash only surfaces when nobody turns up."""
    rows = conn.execute(
        """SELECT shifts.*, users.name AS employee_name FROM shifts
           JOIN users ON users.id = shifts.user_id
           WHERE shifts.shift_date >= ? AND shifts.shift_date <= ?
           ORDER BY shifts.shift_date""",
        (from_date.isoformat(), to_date.isoformat()),
    ).fetchall()
    clashes = []
    for s in rows:
        ok, note = availability_for(conn, s["user_id"], s["shift_date"])
        if not ok:
            clashes.append(dict(s, unavailable_note=note))
    return clashes


def bradford_factor(conn, user_id, since_date):
    """Bradford Factor = spells² × total days. Weights *frequent short*
    absences far above one long illness, which is the pattern that actually
    disrupts a small team -- five separate one-day absences (125) scores far
    worse than one five-day illness (5). A screening prompt for a
    conversation, never a disciplinary trigger on its own.
    """
    rows = conn.execute(
        """SELECT start_date, end_date FROM absences
           WHERE user_id = ? AND kind IN ('sick','unauthorised') AND start_date >= ?""",
        (user_id, since_date.isoformat()),
    ).fetchall()
    spells = len(rows)
    days = 0
    for r in rows:
        s, e = parse_date(r["start_date"]), parse_date(r["end_date"])
        if s and e:
            days += max(1, (e - s).days + 1)
    return {"spells": spells, "days": days, "score": spells * spells * days}


def working_time_violations(conn, from_date, to_date):
    """Check worked time against the rest/consecutive-day/weekly-hours limits.

    Reads `time_entries` -- what people actually did, not what was scheduled --
    because that's what a labour inspector would look at. Returns one entry per
    problem found, each naming the person, the rule and the real figure.
    """
    rows = conn.execute(
        """SELECT time_entries.*, users.name AS employee_name, users.id AS uid
           FROM time_entries JOIN users ON users.id = time_entries.user_id
           WHERE time_entries.clock_out_at IS NOT NULL
             AND time_entries.clock_in_at >= ? AND time_entries.clock_in_at < ?
           ORDER BY users.id, time_entries.clock_in_at""",
        (from_date.isoformat(), (to_date + timedelta(days=1)).isoformat()),
    ).fetchall()

    by_user = {}
    for r in rows:
        by_user.setdefault((r["uid"], r["employee_name"]), []).append(r)

    # Break minutes for every entry in one query. The weekly-hours check below
    # used to call net_hours() per entry, so a month of compliance checking ran
    # ~170 break lookups on a page that already does plenty.
    hours_by_entry = net_hours_for_entries(conn, rows)

    violations = []
    for (uid, name), entries in by_user.items():
        # 1. rest between consecutive shifts
        for prev, nxt in zip(entries, entries[1:]):
            out_at = parse_datetime_iso(prev["clock_out_at"])
            in_at = parse_datetime_iso(nxt["clock_in_at"])
            if not (out_at and in_at):
                continue
            rest = (in_at - out_at).total_seconds() / 3600
            if 0 <= rest < MIN_REST_HOURS_BETWEEN_SHIFTS:
                violations.append({
                    "user_id": uid, "employee_name": name, "rule": "rest",
                    "detail": f"only {rest:.1f}h rest between shifts "
                              f"(minimum {MIN_REST_HOURS_BETWEEN_SHIFTS}h)",
                    "on_date": in_at.astimezone(LOCAL_TZ).date().isoformat(),
                })

        # 2. consecutive days worked, and 3. hours per ISO week
        days_worked = sorted({
            parse_datetime_iso(e["clock_in_at"]).astimezone(LOCAL_TZ).date()
            for e in entries if parse_datetime_iso(e["clock_in_at"])
        })
        run_start, run_len = None, 0
        for i, d in enumerate(days_worked):
            if i and (d - days_worked[i - 1]).days == 1:
                run_len += 1
            else:
                run_start, run_len = d, 1
            if run_len == MAX_CONSECUTIVE_DAYS_WORKED + 1:
                violations.append({
                    "user_id": uid, "employee_name": name, "rule": "consecutive_days",
                    "detail": f"{run_len} days worked in a row without a rest day "
                              f"(maximum {MAX_CONSECUTIVE_DAYS_WORKED})",
                    "on_date": d.isoformat(),
                })

        weekly = {}
        for e in entries:
            start = parse_datetime_iso(e["clock_in_at"])
            if not start:
                continue
            local_day = start.astimezone(LOCAL_TZ).date()
            iso_year, iso_week, _ = local_day.isocalendar()
            weekly.setdefault((iso_year, iso_week), []).append(e)
        for (iso_year, iso_week), week_entries in weekly.items():
            hours = sum(hours_by_entry.get(e["id"], 0.0) for e in week_entries)
            if hours > MAX_WEEKLY_HOURS:
                violations.append({
                    "user_id": uid, "employee_name": name, "rule": "weekly_hours",
                    "detail": f"{hours:.1f}h worked in week {iso_week} "
                              f"(maximum {MAX_WEEKLY_HOURS}h)",
                    "on_date": date.fromisocalendar(iso_year, iso_week, 1).isoformat(),
                })

    violations.sort(key=lambda v: (v["on_date"], v["employee_name"]))
    return violations


# ---------------------------------------------------------------------------
# Reports. Deliberately a small set: four that answer questions the owner
# actually asks, each honouring the shared period window, rather than a
# sprawl of variations nobody reads.
# ---------------------------------------------------------------------------

REPORT_TYPES = {
    "financial": {"label": "Financial", "blurb": "Revenue by area, refunds, expenses and what's left."},
    "occupancy": {"label": "Occupancy & bookings", "blurb": "Nights sold, how full the château was, and the average nightly rate."},
    "labour": {"label": "Labour", "blurb": "Hours and estimated cost per person, and labour as a share of revenue."},
    "guest": {"label": "Guests", "blurb": "New versus returning, who spends most, and how they rated the stay."},
}


def _pct_change(current, previous):
    """Percentage movement, or None when there's no baseline to compare to —
    'up 100%' from zero is noise, not information."""
    if not previous:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


def report_financial(conn, period):
    start, end = period["start"], period["end"]
    prev_start = parse_date(period["prev_start_iso"])
    prev_end = parse_date(period["prev_end_iso"])
    now = financial_month_summary(conn, start, end)
    prev = financial_month_summary(conn, prev_start, prev_end)
    rows = [
        ("Rooms", now["room_revenue"], prev["room_revenue"]),
        ("Restaurant", now["restaurant_revenue"], prev["restaurant_revenue"]),
        ("Workshops", now["workshop_revenue"], prev["workshop_revenue"]),
        ("Events", now["event_revenue"], prev["event_revenue"]),
    ]
    return {
        "summary": now, "previous": prev,
        "revenue_rows": [
            {"label": l, "value": v, "prev": p, "change": _pct_change(v, p)} for l, v, p in rows
        ],
        # A waterfall that actually reconciles: gross − refunds − expenses −
        # labour = net, exactly. The old row showed the NET-of-refunds revenue
        # beside a separate refunds line (so refunds read as deducted twice)
        # and omitted labour entirely, even though `net` subtracts it — the
        # four figures could not be added up to reach the fifth.
        "totals": [
            {"label": "Revenue", "value": now["revenue_gross"], "prev": prev["revenue_gross"],
             "change": _pct_change(now["revenue_gross"], prev["revenue_gross"])},
            {"label": "Refunds issued", "value": -now["refunds_total"], "prev": -prev["refunds_total"],
             "change": _pct_change(now["refunds_total"], prev["refunds_total"])},
            {"label": "Expenses", "value": -now["expenses_total"], "prev": -prev["expenses_total"],
             "change": _pct_change(now["expenses_total"], prev["expenses_total"])},
            {"label": "Labour (est.)", "value": -(now["labour_cost"] or 0),
             "prev": -(prev["labour_cost"] or 0),
             "change": _pct_change(now["labour_cost"] or 0, prev["labour_cost"] or 0),
             "estimated": now["labour_cost"] is None},
            {"label": "Net", "value": now["net"], "prev": prev["net"],
             "change": _pct_change(now["net"], prev["net"])},
        ],
        "csv": [{"metric": l, "amount": v, "previous": p} for l, v, p in rows],
    }


def report_occupancy(conn, period):
    start, end = period["start"], period["end"]
    nights = max(1, (end - start).days)
    rooms_total = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"] or 0

    by_date = occupied_rooms_by_date(conn, start, end)
    booked_nights = sum(by_date.values())
    capacity = rooms_total * nights
    occupancy = round(booked_nights / capacity * 100, 1) if capacity else 0

    stays = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id
           WHERE bookings.status = 'confirmed'
             AND bookings.arrival_date < ? AND bookings.departure_date >= ?""",
        (end.isoformat(), start.isoformat()),
    ).fetchall()
    room_nights = {}
    revenue = 0.0
    for b in stays:
        a, d = parse_date(b["arrival_date"]), parse_date(b["departure_date"])
        if not (a and d):
            continue
        overlap = (min(d, end) - max(a, start)).days
        if overlap <= 0:
            continue
        total_nights = max(1, (d - a).days)
        # Revenue apportioned across the nights that fall inside this window,
        # so a stay spanning a month boundary isn't counted twice.
        revenue += (b["total_price"] or 0) * overlap / total_nights
        room_nights[b["room_name"]] = room_nights.get(b["room_name"], 0) + overlap

    arrivals = conn.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE status='confirmed' AND arrival_date >= ? AND arrival_date < ?",
        (start.isoformat(), end.isoformat())).fetchone()["c"]
    departures = conn.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE status='confirmed' AND departure_date >= ? AND departure_date < ?",
        (start.isoformat(), end.isoformat())).fetchone()["c"]

    # Room refunds issued inside this window. Without this, the occupancy
    # report showed a room revenue the financial report contradicted for the
    # very same month — and the ADR was derived from the overstated figure.
    room_refunds = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS t FROM refunds
           WHERE category = 'room' AND created_at >= ? AND created_at < ?""",
        (start.isoformat(), end.isoformat())).fetchone()["t"]
    revenue_gross = revenue
    revenue = revenue - room_refunds

    return {
        "nights_in_period": nights, "rooms_total": rooms_total,
        "booked_nights": booked_nights, "capacity": capacity, "occupancy": occupancy,
        "revenue": round(revenue, 2), "revenue_gross": round(revenue_gross, 2),
        "refunds": round(room_refunds, 2),
        # ADR is what a night actually earned, so it follows the net figure.
        "adr": round(revenue / booked_nights, 2) if booked_nights else None,
        "arrivals": arrivals, "departures": departures,
        "by_room": sorted(
            [{"room": r, "nights": n, "occupancy": round(n / nights * 100, 1)}
             for r, n in room_nights.items()],
            key=lambda x: -x["nights"]),
        "csv": [{"room": r, "nights": n} for r, n in sorted(room_nights.items())],
    }


def report_labour(conn, period):
    # Same helper the financial summary costs labour with, so the two pages
    # cannot disagree about the same shifts.
    rows = labour_hours_by_person(conn, period["start_iso"], period["end_iso"])
    people = [
        {"name": r["name"], "hours": round(r["hours"], 1), "shifts": r["shifts"],
         "cost": (lambda c: round(c, 2) if c is not None else None)(
             estimated_hourly_cost(r["hours"], r["pay_rate"], r["pay_type"]))}
        for r in rows
    ]
    total_cost, total_hours, unpriced = estimated_labour_cost(
        conn, period["start_iso"], period["end_iso"])
    total_cost = total_cost or 0.0

    fin = financial_month_summary(conn, period["start"], period["end"])
    revenue = fin["revenue"]
    return {
        "people": people,
        "total_hours": total_hours,
        "total_cost": total_cost,
        "unpriced": unpriced,
        "revenue": revenue,
        "labour_pct": round(total_cost / revenue * 100, 1) if revenue > 0 else None,
        "csv": [{"employee": p["name"], "hours": p["hours"], "shifts": p["shifts"],
                 "estimated_cost": p["cost"]} for p in people],
    }


def report_guest(conn, period):
    start_iso, end_iso = period["start_iso"], period["end_iso"]
    bookings = conn.execute(
        """SELECT guest_email, guest_name, total_price, arrival_date FROM bookings
           WHERE status = 'confirmed' AND arrival_date >= ? AND arrival_date < ?""",
        (start_iso, end_iso),
    ).fetchall()

    # "Returning" means they had a confirmed stay that started before this one.
    new_count = returning_count = 0
    spend = {}
    for b in bookings:
        email = (b["guest_email"] or "").lower()
        prior = conn.execute(
            """SELECT COUNT(*) AS c FROM bookings
               WHERE LOWER(guest_email) = ? AND status = 'confirmed' AND arrival_date < ?""",
            (email, b["arrival_date"]),
        ).fetchone()["c"] if email else 0
        if prior:
            returning_count += 1
        else:
            new_count += 1
        if email:
            e = spend.setdefault(email, {"name": b["guest_name"], "total": 0.0, "stays": 0})
            e["total"] += b["total_price"] or 0
            e["stays"] += 1

    feedback = conn.execute(
        """SELECT COUNT(*) AS c, AVG(rating) AS avg_rating FROM guest_feedback
           WHERE submitted_at >= ? AND submitted_at < ?""",
        (start_iso, end_iso),
    ).fetchone()
    top = sorted(
        [{"email": k, "name": v["name"], "total": round(v["total"], 2), "stays": v["stays"]}
         for k, v in spend.items()],
        key=lambda x: -x["total"])[:10]

    return {
        "bookings": len(bookings), "new": new_count, "returning": returning_count,
        "returning_pct": round(returning_count / len(bookings) * 100, 1) if bookings else None,
        "feedback_count": feedback["c"] or 0,
        "feedback_avg": round(feedback["avg_rating"], 1) if feedback["avg_rating"] is not None else None,
        "top_guests": top,
        "csv": [{"guest": g["name"], "email": g["email"], "stays": g["stays"],
                 "total_spend": g["total"]} for g in top],
    }


REPORT_BUILDERS = {
    "financial": report_financial,
    "occupancy": report_occupancy,
    "labour": report_labour,
    "guest": report_guest,
}


CAMPAIGN_AREAS = ["general", "rooms", "restaurant", "workshops", "events", "marketing"]
CAMPAIGN_SEGMENTS = {
    "room": "Past room guests",
    "restaurant": "Past dinner guests",
    "workshop": "Past workshop guests",
    "profiles": "All guest profiles",
}


@app.route("/admin/emails")
@owner_required
def admin_emails():
    conn = get_db()
    templates = conn.execute(
        "SELECT * FROM campaign_templates ORDER BY area, name").fetchall()
    recent = conn.execute(
        """SELECT template_name, subject, COUNT(*) AS recipients,
                  SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS sent,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
                  MAX(created_at) AS at
           FROM campaign_sends WHERE status != 'test'
           GROUP BY template_name, subject, substr(created_at, 1, 16)
           ORDER BY at DESC LIMIT 25"""
    ).fetchall()
    optouts = conn.execute("SELECT COUNT(*) AS c FROM email_optouts").fetchone()["c"]
    held = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE sent_at IS NULL").fetchone()["c"]
    conn.close()
    return render_template("admin_emails.html", templates=templates, recent=recent,
                           areas=CAMPAIGN_AREAS, optouts=optouts, held=held,
                           email_configured=bool(RESEND_API_KEY or SMTP_HOST))


@app.route("/admin/emails/new", methods=["POST"])
@owner_required
def new_campaign_template():
    name = request.form.get("name", "").strip()
    subject = request.form.get("subject", "").strip()
    if not name or not subject:
        flash("A template needs a name and a subject.", "error")
        return redirect(url_for("admin_emails"))
    conn = get_db()
    conn.execute(
        """INSERT INTO campaign_templates (name, area, category, subject, body, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, request.form.get("area", "general"),
         request.form.get("category", "").strip() or None,
         subject, request.form.get("body", "").strip(),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    new_id = conn.execute("SELECT id FROM campaign_templates ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.close()
    flash(f"Template “{name}” created.", "success")
    return redirect(url_for("edit_campaign_template", template_id=new_id))


@app.route("/admin/emails/<int:template_id>", methods=["GET", "POST"])
@owner_required
def edit_campaign_template(template_id):
    conn = get_db()
    template = conn.execute(
        "SELECT * FROM campaign_templates WHERE id = ?", (template_id,)).fetchone()
    if not template:
        conn.close()
        abort(404)

    if request.method == "POST":
        offset_raw = request.form.get("trigger_offset_days", "").strip()
        trigger = request.form.get("trigger_event", "").strip() or None
        conn.execute(
            """UPDATE campaign_templates SET name=?, area=?, category=?, subject=?, body=?,
               trigger_event=?, trigger_offset_days=?, trigger_active=?, updated_at=?
               WHERE id=?""",
            (request.form.get("name", "").strip() or template["name"],
             request.form.get("area", "general"),
             request.form.get("category", "").strip() or None,
             request.form.get("subject", "").strip() or template["subject"],
             request.form.get("body", ""),
             trigger,
             int(offset_raw) if offset_raw.lstrip("-").isdigit() else None,
             1 if (request.form.get("trigger_active") and trigger) else 0,
             datetime.now(timezone.utc).isoformat(), template_id),
        )
        conn.commit()
        conn.close()
        flash("Template saved.", "success")
        return redirect(url_for("edit_campaign_template", template_id=template_id))

    # Preview against a real guest where possible, so merge tags are shown
    # filled with something recognisable rather than placeholder noise.
    sample = conn.execute(
        """SELECT guest_name AS n, guest_email AS e FROM bookings
           WHERE status='confirmed' AND guest_email IS NOT NULL ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    ctx = campaign_context_for(sample["n"] if sample else "Isabelle Fontaine",
                               sample["e"] if sample else "guest@example.com")
    preview_subject, preview_body = render_campaign(template["subject"], template["body"], ctx)

    audience_counts = {
        key: len(campaign_audience(conn, [key])) for key in CAMPAIGN_SEGMENTS
    }
    sends = conn.execute(
        """SELECT * FROM campaign_sends WHERE template_id = ?
           ORDER BY created_at DESC LIMIT 20""", (template_id,)).fetchall()
    conn.close()
    return render_template(
        "admin_email_edit.html", template=template, areas=CAMPAIGN_AREAS,
        segments=CAMPAIGN_SEGMENTS, audience_counts=audience_counts,
        merge_fields=CAMPAIGN_MERGE_FIELDS, preview_subject=preview_subject,
        preview_body=preview_body, sends=sends,
        email_configured=bool(RESEND_API_KEY or SMTP_HOST),
    )


@app.route("/admin/emails/<int:template_id>/delete", methods=["POST"])
@owner_required
def delete_campaign_template(template_id):
    conn = get_db()
    conn.execute("DELETE FROM campaign_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    flash("Template deleted. Its send history is kept.", "success")
    return redirect(url_for("admin_emails"))


@app.route("/admin/emails/<int:template_id>/send", methods=["POST"])
@owner_required
def send_campaign_template(template_id):
    """Send a campaign. Deliberately two-step: a test send goes only to the
    owner, and the real send needs the recipient count typed back, because
    this is the one action here that can't be undone."""
    conn = get_db()
    template = conn.execute(
        "SELECT * FROM campaign_templates WHERE id = ?", (template_id,)).fetchone()
    if not template:
        conn.close()
        abort(404)

    segments = request.form.getlist("segments")
    months_raw = request.form.get("since_months", "").strip()
    since_iso = None
    if months_raw.isdigit() and int(months_raw) > 0:
        since_iso = (datetime.now(timezone.utc).date()
                     - timedelta(days=30 * int(months_raw))).isoformat()

    user = current_user()
    if request.form.get("mode") == "test":
        owner_email = user["email"]
        result = send_campaign(conn, template, {owner_email: user["name"]}, user["id"], as_test=True)
        conn.close()
        flash(f"Test sent to {owner_email}." if result["sent"]
              else "Test send failed — check the email settings.",
              "success" if result["sent"] else "error")
        return redirect(url_for("edit_campaign_template", template_id=template_id))

    if not segments:
        conn.close()
        flash("Choose at least one audience.", "error")
        return redirect(url_for("edit_campaign_template", template_id=template_id))

    audience = campaign_audience(conn, segments, since_iso)
    typed = request.form.get("confirm_count", "").strip()
    if typed != str(len(audience)):
        conn.close()
        flash(f"Type {len(audience)} into the confirm box to send to {len(audience)} "
              f"recipient{'' if len(audience) == 1 else 's'}.", "error")
        return redirect(url_for("edit_campaign_template", template_id=template_id))

    result = send_campaign(conn, template, audience, user["id"])
    log_audit(conn, "campaign_sent", target=template["name"],
              details=f"{result['sent']} sent, {result['failed']} failed")
    conn.commit()
    conn.close()
    flash(f"Sent to {result['sent']} recipient(s)"
          + (f", {result['failed']} failed" if result["failed"] else "") + ".",
          "success" if not result["failed"] else "error")
    return redirect(url_for("edit_campaign_template", template_id=template_id))


@app.route("/unsubscribe/<token>", methods=["GET", "POST"])
@csrf.exempt
def campaign_unsubscribe(token):
    """The guest's own way out of campaign email.

    No login: the token IS the credential, and it's per-send and random, so it
    identifies one recipient without ever putting their address in the URL and
    can't be walked to reach anybody else. GET only shows the page — mail
    clients and link scanners routinely fetch every URL in a message, so
    unsubscribing on GET would silently opt people out who never clicked.
    The POST does the work.

    CSRF-exempt because the recipient arrives from their inbox with no session;
    the worst a forged POST can do is unsubscribe someone from marketing email,
    which is the safe direction and reversible by the owner.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT recipient_email, recipient_name FROM campaign_sends WHERE unsubscribe_token = ?",
        (token,),
    ).fetchone()
    if not row:
        conn.close()
        return render_template("unsubscribe.html", state="unknown"), 404

    email = (row["recipient_email"] or "").strip().lower()
    if request.method == "POST":
        conn.execute(
            "INSERT OR IGNORE INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
            (email, "Unsubscribed from a campaign email",
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        return render_template("unsubscribe.html", state="done", email=email)

    already = conn.execute(
        "SELECT 1 FROM email_optouts WHERE email = ?", (email,)
    ).fetchone() is not None
    conn.close()
    return render_template("unsubscribe.html",
                           state="already" if already else "confirm",
                           email=email, name=row["recipient_name"], token=token)


@app.route("/admin/emails/optout", methods=["POST"])
@owner_required
def add_email_optout():
    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("Enter an email address.", "error")
        return redirect(url_for("admin_emails"))
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
        (email, request.form.get("reason", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash(f"{email} will no longer receive campaign email.", "success")
    return redirect(url_for("admin_emails"))


@app.route("/admin/email-outbox")
@owner_required
def admin_email_outbox():
    """Mail that never went out, and the way to send it once it can."""
    conn = get_db()
    waiting = conn.execute(
        """SELECT * FROM email_outbox WHERE sent_at IS NULL
           ORDER BY created_at DESC LIMIT 200""").fetchall()
    waiting_total = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE sent_at IS NULL").fetchone()["c"]
    recovered = conn.execute(
        """SELECT * FROM email_outbox WHERE sent_at IS NOT NULL
           ORDER BY sent_at DESC LIMIT 25""").fetchall()
    conn.close()
    return render_template(
        "admin_email_outbox.html", waiting=waiting, waiting_total=waiting_total,
        recovered=recovered, can_send=email_enabled() or resend_enabled())


@app.route("/admin/email-outbox/send", methods=["POST"])
@owner_required
def send_email_outbox():
    """Try the held mail again.

    Sends one at a time and marks each as it goes, so a failure part-way
    through leaves the ones already sent marked as sent — a retry that loses
    track would deliver the same confirmation to a guest twice.
    """
    if not (email_enabled() or resend_enabled()):
        flash("No email provider is configured yet, so there is nothing to send with.", "error")
        return redirect(url_for("admin_email_outbox"))

    conn = get_db()
    only = request.form.get("id", "").strip()
    if only.isdigit():
        rows = conn.execute("SELECT * FROM email_outbox WHERE id = ? AND sent_at IS NULL",
                            (int(only),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM email_outbox WHERE sent_at IS NULL ORDER BY created_at LIMIT 100"
        ).fetchall()

    sent = failed = 0
    for row in rows:
        # keep=False: this row IS the queue entry. Re-queueing on failure
        # would add a duplicate every time the owner pressed the button.
        ok = send_email(row["to_address"], row["subject"], row["body"],
                        row["ics_content"], row["ics_filename"], keep=False)
        if ok:
            sent += 1
            conn.execute(
                "UPDATE email_outbox SET sent_at = ?, attempts = attempts + 1 WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"]))
        else:
            failed += 1
            conn.execute(
                "UPDATE email_outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                ("retry failed", row["id"]))
        conn.commit()
    if sent:
        log_audit(conn, "email_outbox_sent", details=f"{sent} sent, {failed} failed")
        conn.commit()
    conn.close()
    flash(f"{sent} sent" + (f", {failed} still waiting." if failed else "."),
          "success" if sent and not failed else "error" if failed else "success")
    return redirect(url_for("admin_email_outbox"))


@app.route("/admin/email-outbox/<int:outbox_id>/discard", methods=["POST"])
@owner_required
def discard_email_outbox(outbox_id):
    conn = get_db()
    row = conn.execute("SELECT to_address, subject FROM email_outbox WHERE id = ?",
                       (outbox_id,)).fetchone()
    conn.execute("DELETE FROM email_outbox WHERE id = ?", (outbox_id,))
    if row:
        log_audit(conn, "email_outbox_discarded", target=row["to_address"],
                  details=row["subject"])
    conn.commit()
    conn.close()
    flash("Discarded — that message will not be sent.", "success")
    return redirect(url_for("admin_email_outbox"))


@app.route("/admin/reports")
@owner_required
def admin_reports():
    period = period_from_request()
    conn = get_db()
    # A headline figure per report so the index is useful on its own rather
    # than being a list of links.
    fin = report_financial(conn, period)
    occ = report_occupancy(conn, period)
    lab = report_labour(conn, period)
    gue = report_guest(conn, period)
    conn.close()
    headlines = {
        "financial": f"€{fin['summary']['net']:,.0f} net",
        "occupancy": f"{occ['occupancy']}% full",
        "labour": (f"{lab['labour_pct']}% of revenue" if lab["labour_pct"] is not None
                   else f"{lab['total_hours']}h"),
        "guest": (f"{gue['returning_pct']}% returning" if gue["returning_pct"] is not None
                  else f"{gue['bookings']} bookings"),
    }
    return render_template("admin_reports.html", period=period,
                           report_types=REPORT_TYPES, headlines=headlines)


@app.route("/admin/reports/<slug>")
@owner_required
def admin_report(slug):
    if slug not in REPORT_BUILDERS:
        abort(404)
    period = period_from_request()
    conn = get_db()
    data = REPORT_BUILDERS[slug](conn, period)
    conn.close()
    return render_template(f"report_{slug}.html", period=period, data=data,
                           meta=REPORT_TYPES[slug], slug=slug, report_types=REPORT_TYPES)


@app.route("/admin/reports/<slug>/export.csv")
@owner_required
def export_report_csv(slug):
    if slug not in REPORT_BUILDERS:
        abort(404)
    period = period_from_request()
    conn = get_db()
    data = REPORT_BUILDERS[slug](conn, period)
    conn.close()
    rows = data.get("csv") or []
    if not rows:
        rows = [{"note": "no data in this period"}]
    return csv_response(list(rows[0].keys()), rows,
                        f"{slug}-{period['start_iso']}-to-{period['end_iso']}.csv")


def ical_escape(text):
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def generate_room_ics(conn, room):
    rows = conn.execute(
        """SELECT * FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
           ORDER BY arrival_date""",
        (room["id"],),
    ).fetchall()
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Chateau de Gudanes//Staff HR//EN", "CALSCALE:GREGORIAN",
    ]
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for b in rows:
        arrival = (b["arrival_date"] or "").replace("-", "")
        departure = (b["departure_date"] or "").replace("-", "")
        if not arrival or not departure:
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:booking-{b['id']}@gudanes-hr.local",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{arrival}",
            f"DTEND;VALUE=DATE:{departure}",
            f"SUMMARY:{ical_escape('Booked — ' + room['name'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def generate_booking_ics(booking, room_name):
    arrival = (booking["arrival_date"] or "").replace("-", "")
    departure = (booking["departure_date"] or "").replace("-", "")
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Chateau de Gudanes//Bookings//EN", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:booking-{booking['id']}@gudanes-hr.local",
        f"DTSTAMP:{now_stamp}",
        f"DTSTART;VALUE=DATE:{arrival}",
        f"DTEND;VALUE=DATE:{departure}",
        f"SUMMARY:{ical_escape('Château de Gudanes — ' + room_name)}",
        f"DESCRIPTION:{ical_escape('Reference: ' + booking['reference_code'])}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def make_reference_code():
    return "GUD-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


# ---------------------------------------------------------------------------
# Email — best-effort only. A broken or unconfigured mail server must never
# break a booking, a decline, or any other real action, so failures here are
# logged and swallowed rather than raised.
# ---------------------------------------------------------------------------

def resend_enabled():
    return bool(RESEND_API_KEY and RESEND_FROM)


def email_enabled():
    return resend_enabled() or bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)


def send_email_via_resend(to_address, subject, body, ics_content=None, ics_filename=None):
    """Resend's HTTP API — plain urllib, no extra dependency (matches how
    the rest of this file makes outbound HTTP calls, e.g. fetch_ical_ranges)."""
    payload = {"from": RESEND_FROM, "to": [to_address], "subject": subject, "text": body}
    if ics_content:
        payload["attachments"] = [{
            "filename": ics_filename or "booking.ics",
            "content": base64.b64encode(ics_content.encode("utf-8")).decode("ascii"),
        }]
    try:
        req = Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"[resend email failed] To: {to_address} | Subject: {subject} | Error: {e}")
        return False


def queue_undelivered(to_address, subject, body, ics_content, ics_filename, reason, error=None):
    """Keep a message that could not be sent, so it can go out later.

    Deliberately opens and closes its own connection and swallows everything:
    this runs on the failure path of a side effect, and the request that
    triggered it has real work to finish — recording a booking, deciding an
    expense. Failing to file a failure must not lose the thing that succeeded.
    """
    try:
        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO email_outbox (to_address, subject, body, ics_content,
                   ics_filename, reason, last_error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (to_address, subject, body, ics_content, ics_filename, reason, error,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                     # pragma: no cover - last resort
        print(f"[outbox write failed] To: {to_address} | Subject: {subject} | Error: {e}")


def send_email(to_address, subject, body, ics_content=None, ics_filename=None, keep=True):
    """Send one message, and if it cannot go out, keep it.

    `keep=False` for anything whose body is itself a credential — a password
    reset or a staff invitation. Those are short-lived by design, so a retry
    days later is useless, and storing one leaves a working key in a table.
    """
    if not to_address:
        return False
    if resend_enabled():
        if send_email_via_resend(to_address, subject, body, ics_content, ics_filename):
            return True
        if keep:
            queue_undelivered(to_address, subject, body, ics_content, ics_filename,
                              "provider rejected it", "Resend API call failed")
        return False
    if not email_enabled():
        print(f"[email held — no email provider configured] To: {to_address} | Subject: {subject}")
        if keep:
            queue_undelivered(to_address, subject, body, ics_content, ics_filename,
                              "no email provider configured")
        return False
    try:
        # Assigning headers can itself raise (e.g. a crafted guest_email
        # containing an embedded newline — Python's email lib rejects header
        # values with linefeeds), so this stays inside the try: a malformed
        # address should make this a no-op send, not crash whichever
        # request triggered it — that request has real work to finish
        # (recording a booking, deciding an expense) that email is just a
        # side effect of.
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_address
        msg.set_content(body)
        if ics_content:
            msg.add_attachment(
                ics_content.encode("utf-8"), maintype="text", subtype="calendar",
                filename=ics_filename or "booking.ics",
            )
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls(context=context)
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[email failed] To: {to_address} | Subject: {subject} | Error: {e}")
        if keep:
            queue_undelivered(to_address, subject, body, ics_content, ics_filename,
                              "send failed", str(e))
        return False


def owner_email(conn):
    row = conn.execute("SELECT email FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    return row["email"] if row else None


def get_vapid_private_key(conn):
    return conn.execute("SELECT value FROM app_settings WHERE key = 'vapid_private_key'").fetchone()["value"]


def get_vapid_public_key(conn):
    return conn.execute("SELECT value FROM app_settings WHERE key = 'vapid_public_key'").fetchone()["value"]


def send_notification(conn, user_id, kind, title, body=None, link=None, related_task_id=None):
    """Writes the in-app notification (what /notifications and the nav
    badge read) and best-effort pushes it to every browser that user has
    subscribed on. A push failure never blocks the in-app copy — the
    in-app center is the source of truth, push is just the 'get their
    attention right now' layer on top."""
    conn.execute(
        """INSERT INTO notifications (user_id, kind, title, body, link, related_task_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, kind, title, body, link, related_task_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    subs = conn.execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    if not subs:
        return
    payload = json.dumps({"title": title, "body": body or "", "link": link or "/notifications"})
    dead_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=get_vapid_private_key(conn),
                vapid_claims={"sub": f"mailto:{owner_email(conn) or 'owner@example.com'}"},
            )
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                # Browser says this subscription is gone for good (uninstalled,
                # permission revoked) — stop trying it on future notifications.
                dead_ids.append(sub["id"])
            else:
                print(f"[push failed] user={user_id} status={status} error={e}")
        except Exception as e:
            print(f"[push failed] user={user_id} error={e}")
    if dead_ids:
        conn.executemany("DELETE FROM push_subscriptions WHERE id = ?", [(i,) for i in dead_ids])
        conn.commit()


# ---------------------------------------------------------------------------
# Booking creation + payment — one place both the free (no-Stripe) path and
# the paid (Stripe) path funnel through, so a booking is only ever created
# once, with the same emails, regardless of which path got it there.
# ---------------------------------------------------------------------------

def create_booking(conn, room, guest_name, guest_email, guest_phone, arrival, departure,
                    party_size, special_requests, chosen_extras, payment_status="unpaid",
                    stripe_session_id=None, stripe_payment_intent_id=None, promo_code=None):
    nights = (departure - arrival).days
    room_total = compute_room_total(conn, room, arrival, departure)
    extras_total = sum(e["price"] for e in chosen_extras)

    promo, discount_amount = None, 0.0
    if promo_code:
        promo, discount_amount, _ = validate_promo_code(conn, promo_code, "room", room_total)
    total_price = (round(room_total - discount_amount, 2) + extras_total) or None
    extras_summary = ", ".join(f"{e['name']} (€{e['price']:.2f})" for e in chosen_extras) or None

    reference_code = make_reference_code()
    manage_token = secrets.token_urlsafe(24)
    conn.execute(
        """INSERT INTO bookings
           (room_id, reference_code, manage_token, guest_name, guest_email, guest_phone,
            arrival_date, departure_date, party_size, special_requests, total_price,
            extras_summary, payment_status, stripe_session_id, stripe_payment_intent_id, created_at,
            promo_code_id, discount_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room["id"], reference_code, manage_token, guest_name, guest_email, guest_phone,
         arrival.isoformat(), departure.isoformat(), party_size, special_requests or None,
         total_price, extras_summary, payment_status, stripe_session_id, stripe_payment_intent_id,
         datetime.now(timezone.utc).isoformat(),
         promo["id"] if promo else None, discount_amount or None),
    )
    # Recorded in the SAME transaction as the booking insert, not a
    # separate commit after — otherwise a crash between the two would
    # durably save the discounted booking while the redemption count
    # never increments, making max_redemptions silently bypassable.
    if promo:
        record_promo_redemption(conn, promo, "room", reference_code, guest_email, room_total, discount_amount)
    conn.commit()

    checkin_url = url_for("guest_checkin", manage_token=manage_token, _external=True)
    detail_lines = [
        f"Arrival: {format_date_human(arrival.isoformat())}",
        f"Departure: {format_date_human(departure.isoformat())}",
        f"Party size: {party_size}",
    ]
    if extras_summary:
        detail_lines.append(f"Add-ons: {extras_summary}")
    if promo and discount_amount:
        detail_lines.append(f"Discount applied ({promo['code']}): -€{discount_amount:.2f}")
    if total_price:
        detail_lines.append(f"Total: €{total_price:.2f}" + (" (paid)" if payment_status == "paid" else ""))
    send_email(
        guest_email,
        f"Booking request received — {room['name']}",
        f"Hi {guest_name},\n\n"
        f"Your request for {room['name']} has been received and is awaiting confirmation.\n\n"
        + "\n".join(detail_lines) +
        f"\n\nReference code: {reference_code}\n"
        f"Check in online, manage your booking, or send us a request: {checkin_url}\n\n"
        f"— Château de Gudanes",
    )
    owner_to = owner_email(conn)
    if owner_to:
        send_email(
            owner_to,
            f"New booking request — {room['name']} ({reference_code})",
            f"{guest_name} requested {room['name']}, {arrival.isoformat()} to {departure.isoformat()}, "
            f"party of {party_size}.\n"
            f"{'Payment already received.' if payment_status == 'paid' else 'No payment taken — review and confirm.'}\n\n"
            f"Review: {url_for('admin_bookings', _external=True)}",
        )
    return reference_code, manage_token


def create_booking_from_stripe_session(conn, session):
    """Rebuilds a booking from a completed Stripe Checkout Session's
    metadata. Used by both the success redirect and the webhook — whichever
    fires first creates it, the other finds it already exists via
    stripe_session_id and does nothing."""
    meta = smeta(session)
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (int(meta["room_id"]),)).fetchone()
    if not room:
        print(f"[stripe] room {meta.get('room_id')} missing for paid session {session['id']} — not creating booking")
        return None
    extra_ids = {int(i) for i in meta.get("extra_ids", "").split(",") if i}
    chosen_extras = []
    if extra_ids:
        placeholders = ",".join("?" * len(extra_ids))
        chosen_extras = conn.execute(
            f"SELECT * FROM extras WHERE id IN ({placeholders})", tuple(extra_ids)
        ).fetchall()
    arrival, departure = parse_date(meta["arrival_date"]), parse_date(meta["departure_date"])
    available, conflict_reason = is_range_available(conn, room["id"], arrival, departure, include_pending=False)
    reference_code, manage_token = create_booking(
        conn, room, meta["guest_name"], meta["guest_email"], meta.get("guest_phone", ""),
        arrival, departure, int(meta["party_size"]), meta.get("special_requests", ""),
        chosen_extras, payment_status="paid",
        stripe_session_id=session["id"], stripe_payment_intent_id=sval(session, "payment_intent"),
        promo_code=meta.get("promo_code") or None,
    )
    if not available:
        # Money has already changed hands, so the booking is still recorded —
        # but the room may now be double-booked (another booking was confirmed
        # during the gap between checkout starting and payment completing).
        # Flag it loudly for manual resolution rather than silently double-booking.
        log_audit(conn, "stripe_booking_date_conflict", target=reference_code, details=conflict_reason)
        conn.commit()
        send_email(
            owner_email(conn),
            f"URGENT: paid booking conflict — {room['name']}",
            f"A paid Stripe booking was just created for {room['name']} "
            f"({arrival.isoformat()} to {departure.isoformat()}) that conflicts with an existing booking: "
            f"{conflict_reason}\n\n"
            f"Guest: {meta['guest_name']} ({meta['guest_email']})\n"
            f"The guest has already paid — this needs manual review (contact the guest, move the "
            f"other booking, or issue a refund).",
        )
    return manage_token


REFUND_TABLES = {
    "room": "bookings",
    "restaurant": "restaurant_bookings",
    "workshop": "workshop_bookings",
}


def refunded_so_far(conn, category, booking_id):
    """Total already refunded against one booking. Partial refunds can stack,
    so this is what stops the second one over-refunding.

    For workshops this also counts refund rows entered by hand in the booking's
    own transaction ledger. The owner can record "gave them €500 back in cash"
    there directly, and if that didn't count against the ceiling the refund form
    would still offer the full amount -- paying the same guest twice.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM refunds WHERE category = ? AND booking_id = ?",
        (category, booking_id),
    ).fetchone()
    total = row["total"]
    if category == "workshop":
        # Only ledger rows NOT written by issue_refund itself -- those are
        # already counted above, and double-counting is the bug this whole
        # area keeps producing.
        manual = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS total FROM workshop_transactions
               WHERE workshop_booking_id = ? AND kind = 'refund'
                 AND COALESCE(description, '') NOT LIKE 'Refund — %'""",
            (booking_id,),
        ).fetchone()
        total += manual["total"]
    return round(total, 2)


def amount_paid_for(conn, category, booking):
    """GROSS amount the guest has handed over, ignoring anything already given
    back -- this is the ceiling that `refundable_amount` then subtracts refunds
    from. Differs per category: rooms pay the whole total up front, the
    restaurant may only have taken a deposit, and workshops track real payments
    in their own ledger.

    Must stay gross. `workshop_balance_due` reports paid NET of refund rows, and
    `issue_refund` writes a refund into that same ledger -- so using its figure
    here subtracted every refund twice, shrinking the ceiling on each refund
    until a guest could never be made whole.
    """
    if category == "workshop":
        row = conn.execute(
            """SELECT COALESCE(SUM(amount), 0) AS paid FROM workshop_transactions
               WHERE workshop_booking_id = ? AND kind = 'payment'""",
            (booking["id"],),
        ).fetchone()
        return round(row["paid"], 2)
    if category == "restaurant":
        keys = booking.keys()
        if "deposit_amount" in keys and booking["deposit_amount"]:
            return round(booking["deposit_amount"], 2)
    return round(booking["total_price"] or 0, 2)


def refundable_amount(conn, category, booking):
    return round(max(0.0, amount_paid_for(conn, category, booking)
                     - refunded_so_far(conn, category, booking["id"])), 2)


def issue_refund(conn, category, booking, amount, reason, method="stripe", user_id=None):
    """Issue (or record) a refund of `amount` against one booking.

    Partial refunds are the point here -- the old version could only ever hand
    back the entire payment, which made "give them half back because they
    cancelled late" impossible. `method` distinguishes a real Stripe refund
    from one settled outside the system (bank transfer, cash); the latter is
    recorded but obviously moves no money here.

    Returns (ok, error). Nothing is written unless the money side succeeded.
    """
    if not reason or not reason.strip():
        return False, "A reason is required for every refund."
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return False, "Enter a valid refund amount."
    if amount <= 0:
        return False, "Refund amount must be greater than zero."

    # Never "refund" something that was never paid -- that would invent money in
    # the record and flip the booking to 'refunded' from 'unpaid'.
    keys = booking.keys()
    if "payment_status" in keys and booking["payment_status"] == "unpaid":
        return False, "This booking hasn't been paid, so there's nothing to refund."

    ceiling = refundable_amount(conn, category, booking)
    if ceiling <= 0:
        return False, "There's nothing left to refund on this booking."
    if amount > ceiling + 0.005:  # tolerate float noise, not real over-refunds
        return False, f"That's more than the €{ceiling:.2f} still refundable on this booking."

    stripe_refund_id = None
    if method == "stripe":
        if not stripe_enabled():
            return False, "Stripe isn't configured, so a card refund can't be issued."
        intent = booking["stripe_payment_intent_id"] if "stripe_payment_intent_id" in booking.keys() else None
        if not intent:
            # Workshops in particular have no single payment intent -- deposit
            # and balance are taken as two separate Stripe sessions -- so a card
            # refund genuinely cannot be issued from here. Say so plainly rather
            # than nudging the owner to record a manual refund that moves no
            # money and then reads as already-paid in the log.
            return False, (
                "No single Stripe payment on record for this booking, so a card refund "
                "can't be issued here. Refund it from the Stripe dashboard, then record "
                "it below as 'Other' so the log matches."
            )
        try:
            # Stripe works in the smallest currency unit; euros -> cents.
            # The idempotency key makes a double-submit or a retry return the
            # SAME refund instead of creating a second one.
            refund = stripe.Refund.create(
                payment_intent=intent,
                amount=int(round(amount * 100)),
                idempotency_key=f"gudanes-{category}-{booking['id']}-{int(round(amount * 100))}-"
                                f"{refunded_so_far(conn, category, booking['id']):.2f}",
            )
            stripe_refund_id = getattr(refund, "id", None)
        except Exception as e:
            return False, str(e)

    conn.execute(
        """INSERT INTO refunds (category, booking_id, reference_code, guest_name, guest_email,
           amount, reason, method, stripe_refund_id, refunded_by_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (category, booking["id"],
         booking["reference_code"] if "reference_code" in booking.keys() else None,
         booking["guest_name"] if "guest_name" in booking.keys() else None,
         booking["guest_email"] if "guest_email" in booking.keys() else None,
         amount, reason.strip(), method, stripe_refund_id, user_id,
         datetime.now(timezone.utc).isoformat()),
    )

    # Only flip the booking to 'refunded' once nothing is left outstanding --
    # a partial refund leaves it 'paid', with the real figure living in the
    # refunds table. That avoids rewriting the payment_status CHECK constraint,
    # which would mean a full table rebuild on a table other rows point at.
    table = REFUND_TABLES.get(category)
    if table and refunded_so_far(conn, category, booking["id"]) >= amount_paid_for(conn, category, booking) - 0.005:
        try:
            conn.execute(f"UPDATE {table} SET payment_status = 'refunded' WHERE id = ?", (booking["id"],))
        except sqlite3.OperationalError:
            pass  # workshop_bookings tracks money in its ledger, not a status column

    if category == "workshop":
        add_workshop_transaction(conn, booking["id"], "refund", f"Refund — {reason.strip()}",
                                 amount, method=method, user_id=user_id)

    conn.commit()
    return True, None


def refund_booking(conn, booking, amount=None, reason="Cancelled by the château", user_id=None):
    """Back-compat wrapper for the automatic full refund on decline, where the
    château is the one calling the booking off and owes the money back."""
    if booking["payment_status"] != "paid":
        return False, "This booking was never marked paid."
    amount = refundable_amount(conn, "room", booking) if amount is None else amount
    if amount <= 0:
        return False, "There's nothing left to refund on this booking."
    return issue_refund(conn, "room", booking, amount, reason, method="stripe", user_id=user_id)


def is_viewable(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[-1].lower() in VIEWABLE_EXTENSIONS


@app.context_processor
def inject_user():
    user = current_user()
    pending_approvals_count = None
    open_hr_notes_count = None
    unread_notifications_count = None
    vapid_public_key = None
    pending_restaurant_count = None
    pending_workshop_count = None
    pending_events_count = None
    open_email_flags_count = None
    if user:
        conn = get_db()
        unread_notifications_count = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user["id"],)
        ).fetchone()[0]
        vapid_public_key = get_vapid_public_key(conn)
        if user["role"] == "owner":
            # Must match what /admin/approvals actually lists, or the sidebar
            # badge and the page disagree about how much is waiting.
            pending_approvals_count = conn.execute(
                "SELECT (SELECT COUNT(*) FROM leave_requests WHERE status = 'pending') "
                "+ (SELECT COUNT(*) FROM expenses WHERE status = 'pending') "
                "+ (SELECT COUNT(*) FROM timesheet_corrections WHERE status = 'pending')"
            ).fetchone()[0]
            open_hr_notes_count = conn.execute(
                "SELECT COUNT(*) FROM hr_notes WHERE status = 'open'"
            ).fetchone()[0]
            pending_restaurant_count = conn.execute(
                "SELECT COUNT(*) FROM restaurant_bookings WHERE status = 'pending'"
            ).fetchone()[0]
            pending_workshop_count = conn.execute(
                "SELECT COUNT(*) FROM workshop_bookings WHERE status = 'pending'"
            ).fetchone()[0]
            pending_events_count = conn.execute(
                "SELECT COUNT(*) FROM event_inquiries WHERE status = 'new'"
            ).fetchone()[0]
            open_email_flags_count = conn.execute(
                "SELECT COUNT(*) FROM email_flags WHERE status = 'open'"
            ).fetchone()[0]
        conn.close()
    return {
        "user": user, "is_viewable": is_viewable, "hours_between": hours_between,
        "local_time_str": local_time_str, "local_datetime_str": local_datetime_str,
        "expiry_status": expiry_status, "net_hours": net_hours,
        "pending_approvals_count": pending_approvals_count,
        "open_hr_notes_count": open_hr_notes_count,
        "unread_notifications_count": unread_notifications_count,
        "vapid_public_key": vapid_public_key,
        "pending_restaurant_count": pending_restaurant_count,
        "pending_workshop_count": pending_workshop_count,
        "pending_events_count": pending_events_count,
        "open_email_flags_count": open_email_flags_count,
        # Constants the forms need. Exposed here rather than passed through
        # every render_template call, so a new form can't quietly render an
        # empty dropdown because one route forgot to include them.
        "contract_types": CONTRACT_TYPES,
        "trial_warning_days": TRIAL_WARNING_DAYS,
        "incident_kinds": INCIDENT_KINDS,
        "incident_severities": INCIDENT_SEVERITIES,
        "access_kinds": ACCESS_KINDS,
    }


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        ip = request.remote_addr or "unknown"
        conn = get_db()
        throttle = conn.execute("SELECT * FROM login_throttle WHERE ip_address = ?", (ip,)).fetchone()
        if throttle and throttle["locked_until"] and parse_datetime_iso(throttle["locked_until"]) > datetime.now(timezone.utc):
            conn.close()
            flash("Too many failed sign-in attempts from this connection. Try again in a few minutes.", "error")
            return render_template("login.html")

        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if user["status"] == "inactive":
                conn.close()
                flash("This account is inactive. Contact the owner.", "error")
                return render_template("login.html")
            conn.execute("DELETE FROM login_throttle WHERE ip_address = ?", (ip,))
            session.permanent = True
            session["user_id"] = user["id"]
            conn.commit()
            conn.close()
            return redirect(url_for("dashboard"))

        new_count = (throttle["failed_count"] if throttle else 0) + 1
        locked_until = None
        if new_count >= LOGIN_LOCKOUT_THRESHOLD:
            locked_until = (datetime.now(timezone.utc) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat()
            log_audit(conn, "login_lockout", target=ip, details=f"{new_count} failed attempts")
        conn.execute(
            """INSERT INTO login_throttle (ip_address, failed_count, locked_until) VALUES (?, ?, ?)
               ON CONFLICT(ip_address) DO UPDATE SET failed_count = excluded.failed_count, locked_until = excluded.locked_until""",
            (ip, new_count, locked_until),
        )
        conn.commit()
        conn.close()
        flash("Incorrect email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/clock/in", methods=["POST"])
@login_required
def clock_in_action():
    user = current_user()
    conn = get_db()
    if open_shift(conn, user["id"]):
        flash("You're already clocked in.", "error")
    else:
        clock_in(conn, user["id"])
        flash(f"Clocked in at {local_time_str(datetime.now(timezone.utc).isoformat())}.", "success")
    conn.close()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/clock/out", methods=["POST"])
@login_required
def clock_out_action():
    user = current_user()
    conn = get_db()
    shift = open_shift(conn, user["id"])
    if not shift:
        flash("You're not currently clocked in.", "error")
    else:
        clock_out(conn, user["id"])
        hours = net_hours(conn.execute("SELECT * FROM time_entries WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone(), conn)
        flash(f"Clocked out — {hours:.2f}h worked this shift.", "success")
    conn.close()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if not email_enabled():
            flash("Email isn't set up for this site yet — ask the owner to reset your password directly.", "error")
            return render_template("forgot_password.html", email_enabled=email_enabled())
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        person = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if person:
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            conn.execute(
                "UPDATE users SET reset_token = ?, reset_token_expires_at = ? WHERE id = ?",
                (token, expires, person["id"]),
            )
            conn.commit()
            reset_url = url_for("reset_password", token=token, _external=True)
            send_email(
                person["email"], "Reset your password",
                f"Hi {person['name'].split(' ')[0]},\n\n"
                f"Click this link to set a new password (valid for 1 hour):\n{reset_url}\n\n"
                f"If you didn't request this, you can ignore this email.\n\n— Château de Gudanes",
                keep=False,   # the body is a live credential, and expires in an hour
            )
        conn.close()
        # Same message whether or not the email matched — don't reveal who has an account.
        flash("If that email has an account, a reset link is on its way.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html", email_enabled=email_enabled())


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db()
    person = conn.execute("SELECT * FROM users WHERE reset_token = ?", (token,)).fetchone()
    if not person or not person["reset_token_expires_at"] or parse_datetime_iso(person["reset_token_expires_at"]) < datetime.now(timezone.utc):
        conn.close()
        flash("That reset link is invalid or has expired — request a new one.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            conn.close()
            flash("Password must be at least 8 characters.", "error")
            return render_template("reset_password.html", token=token)
        if password != confirm:
            conn.close()
            flash("Passwords don't match.", "error")
            return render_template("reset_password.html", token=token)
        conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires_at = NULL WHERE id = ?",
            (generate_password_hash(password), person["id"]),
        )
        conn.commit()
        conn.close()
        flash("Password updated — sign in with your new password.", "success")
        return redirect(url_for("login"))

    conn.close()
    return render_template("reset_password.html", token=token)


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        user = current_user()
        if not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new != confirm:
            flash("New passwords don't match.", "error")
        else:
            conn = get_db()
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new), user["id"]),
            )
            log_audit(conn, "password_changed", target=user["name"])
            conn.commit()
            conn.close()
            flash("Password updated.", "success")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html")


@app.route("/my-contact-info", methods=["GET", "POST"])
@login_required
def edit_own_contact_info():
    """Self-service editing limited to phone + emergency contact — job role,
    pay, status, and skills stay owner-controlled via edit_employee."""
    user = current_user()
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        emergency_contact_name = request.form.get("emergency_contact_name", "").strip()
        emergency_contact_phone = request.form.get("emergency_contact_phone", "").strip()
        emergency_contact_relationship = request.form.get("emergency_contact_relationship", "").strip()
        conn = get_db()
        conn.execute(
            """UPDATE users SET phone = ?, emergency_contact_name = ?,
               emergency_contact_phone = ?, emergency_contact_relationship = ? WHERE id = ?""",
            (phone, emergency_contact_name, emergency_contact_phone,
             emergency_contact_relationship, user["id"]),
        )
        conn.commit()
        conn.close()
        flash("Contact info updated.", "success")
        return redirect(url_for("profile", user_id=user["id"]))
    return render_template("edit_own_contact_info.html", person=user)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    conn = get_db()
    stats = {}
    today = datetime.now(timezone.utc).date()
    briefing = None
    my_tasks = []

    who_is_here = guests_in_residence(conn, today)
    announcements_current = current_announcements(conn, today)

    on_shift_by_user = {
        r["user_id"]: r for r in conn.execute("SELECT * FROM time_entries WHERE clock_out_at IS NULL").fetchall()
    }
    my_shift = on_shift_by_user.get(user["id"])
    my_open_break = None
    if my_shift:
        my_open_break = conn.execute(
            "SELECT * FROM breaks WHERE time_entry_id = ? AND end_at IS NULL", (my_shift["id"],)
        ).fetchone()

    if user["role"] == "owner":
        auto_prepped_count = auto_prep_upcoming_arrivals(conn, today)
        if auto_prepped_count:
            flash(
                f"Auto-prepped {auto_prepped_count} arriving booking{'' if auto_prepped_count == 1 else 's'} "
                f"— room setup tasks assigned.", "success",
            )
        expired_count = expire_stale_pending_bookings(conn)
        if expired_count:
            flash(
                f"Auto-declined {expired_count} pending booking{'' if expired_count == 1 else 's'} "
                f"with no response after {STALE_PENDING_BOOKING_HOURS}h.", "success",
            )
        team = conn.execute(
            "SELECT * FROM users WHERE role = 'employee' ORDER BY status, name"
        ).fetchall()
        on_shift_now = conn.execute(
            """SELECT time_entries.*, users.name AS user_name, users.job_role AS user_job_role
               FROM time_entries JOIN users ON users.id = time_entries.user_id
               WHERE time_entries.clock_out_at IS NULL
               ORDER BY time_entries.clock_in_at"""
        ).fetchall()
        current_tasks_by_user = {}
        for t in conn.execute(
            """SELECT * FROM tasks WHERE status != 'done' AND assigned_to_user_id IS NOT NULL
               AND (due_date IS NULL OR due_date <= ?) ORDER BY (due_date IS NULL), due_date""",
            (today.isoformat(),),
        ).fetchall():
            current_tasks_by_user.setdefault(t["assigned_to_user_id"], []).append(t)
        all_stays = stays_with_status(conn, today)
        stats["guests_current"] = sum(1 for g in all_stays if g["stay_status"] == "current")
        stats["guests_upcoming"] = sum(1 for g in all_stays if g["stay_status"] == "upcoming")
        stats["rooms_total"] = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
        stats["bookings_pending"] = conn.execute(
            "SELECT COUNT(*) AS c FROM bookings WHERE status = 'pending'"
        ).fetchone()["c"]
        stats["expenses_pending"] = conn.execute(
            "SELECT COUNT(*) AS c FROM expenses WHERE status = 'pending'"
        ).fetchone()["c"]
        stats["leave_pending"] = conn.execute(
            "SELECT COUNT(*) AS c FROM leave_requests WHERE status = 'pending'"
        ).fetchone()["c"]
        who_is_off_today = conn.execute(
            """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
               JOIN users ON users.id = leave_requests.user_id
               WHERE leave_requests.status = 'approved'
                 AND leave_requests.start_date <= ? AND leave_requests.end_date >= ?
               ORDER BY users.name""",
            (today.isoformat(), today.isoformat()),
        ).fetchall()
        briefing = build_task_sheet(conn, "day", today)
        month_stats = compute_month_stats(conn, today)
        feedback_row = conn.execute("SELECT AVG(rating) AS avg_rating, COUNT(*) AS c FROM guest_feedback").fetchone()
        stats["feedback_avg"] = round(feedback_row["avg_rating"], 1) if feedback_row["avg_rating"] is not None else None
        stats["feedback_count"] = feedback_row["c"]
        recent_30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        stats["feedback_count_recent"] = conn.execute(
            "SELECT COUNT(*) AS c FROM guest_feedback WHERE submitted_at >= ?", (recent_30,)
        ).fetchone()["c"]
        stats["waitlist_open"] = conn.execute(
            "SELECT COUNT(*) AS c FROM waitlist_entries WHERE status IN ('open', 'contacted')"
        ).fetchone()["c"]
        last_backup = conn.execute(
            "SELECT created_at FROM audit_log WHERE action = 'backup_downloaded' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_backup_at = last_backup["created_at"] if last_backup else None
        backup_stale = (not last_backup_at) or (parse_date(last_backup_at[:10]) <= today - timedelta(days=30))
        current_month_financials = financial_month_summary(
            conn, today.replace(day=1),
            date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1),
        )
        financial_trend_months = []
        cursor_month = today.replace(day=1)
        for _ in range(6):
            cursor_end = date(cursor_month.year + 1, 1, 1) if cursor_month.month == 12 else date(cursor_month.year, cursor_month.month + 1, 1)
            financial_trend_months.append(financial_month_summary(conn, cursor_month, cursor_end))
            cursor_month = date(cursor_month.year - 1, 12, 1) if cursor_month.month == 1 else date(cursor_month.year, cursor_month.month - 1, 1)
        financial_trend_months.reverse()
        financial_trend_max = max(
            [m["revenue"] for m in financial_trend_months] + [m["expenses_total"] for m in financial_trend_months] + [1]
        )
        dashboard_calendar = build_dashboard_calendar(conn, today)
        recent_30_feedback = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        low_ratings = conn.execute(
            """SELECT guest_feedback.*, bookings.reference_code FROM guest_feedback
               LEFT JOIN bookings ON bookings.id = guest_feedback.booking_id
               WHERE rating <= 2 AND submitted_at >= ? ORDER BY submitted_at DESC""",
            (recent_30_feedback,),
        ).fetchall()
        anniversaries = upcoming_anniversaries(conn, today)
        probation_due = probation_reviews_due(conn, today)
        unstaffed_days = unstaffed_activity_days(conn, today)
        overtime = week_overtime(conn, today)
        activity = recent_activity(conn)
        soon = (today + timedelta(days=30)).isoformat()
        expiring_docs = (
            [dict(d, kind="Employee doc")
             for d in conn.execute(
                 """SELECT documents.*, users.name AS employee_name FROM documents
                    JOIN users ON users.id = documents.user_id
                    WHERE expiry_date IS NOT NULL AND expiry_date <= ? ORDER BY expiry_date""",
                 (soon,),
             ).fetchall()]
            + [dict(d, kind="Company doc", employee_name=None)
               for d in conn.execute(
                   "SELECT * FROM company_documents WHERE expiry_date IS NOT NULL AND expiry_date <= ? ORDER BY expiry_date",
                   (soon,),
               ).fetchall()]
            + [{"title": p["provider"] + (f" ({p['coverage_type']})" if p["coverage_type"] else ""),
                "kind": "Insurance", "employee_name": None, "expiry_date": p["expiry_date"]}
               for p in conn.execute(
                   "SELECT * FROM insurance_policies WHERE expiry_date IS NOT NULL AND expiry_date <= ? ORDER BY expiry_date",
                   (soon,),
               ).fetchall()]
            + [{"title": f"{c['label']} — €{c['amount']:.0f}", "kind": "Recurring cost",
                "employee_name": None, "expiry_date": c["next_due_date"]}
               for c in conn.execute(
                   """SELECT * FROM recurring_costs WHERE active = 1 AND next_due_date IS NOT NULL
                      AND next_due_date <= ? ORDER BY next_due_date""",
                   (soon,),
               ).fetchall()]
        )
        expiring_docs.sort(key=lambda d: d["expiry_date"])
        my_expiring_docs = []
        my_week_hours = None
        my_month_hours = None
        vehicle_alerts = (
            [{"title": v["name"], "detail": "dirty"} for v in conn.execute(
                "SELECT name FROM vehicles WHERE cleanliness = 'dirty' ORDER BY name"
            ).fetchall()]
            + [{"title": v["name"], "detail": "low fuel"} for v in conn.execute(
                "SELECT name FROM vehicles WHERE fuel_level = 'low' ORDER BY name"
            ).fetchall()]
            + [{"title": v["name"], "detail": f"service due {v['next_service_due']}"} for v in conn.execute(
                "SELECT name, next_service_due FROM vehicles WHERE next_service_due IS NOT NULL AND next_service_due <= ? ORDER BY next_service_due",
                (soon,),
            ).fetchall()]
            + [{"title": c["vehicle_name"], "detail": f"out since {c['checked_out_at'][:10]} with {c['user_name'] or 'unknown'}"}
               for c in overdue_vehicle_checkouts(conn)]
        )
        breakfast_low_stock = conn.execute(
            "SELECT name FROM breakfast_items WHERE low_stock = 1 ORDER BY name"
        ).fetchall()
        restaurant_alerts = []
        restaurant_settings_row = get_restaurant_settings(conn)
        if restaurant_settings_row:
            pending_dinners_count = conn.execute(
                "SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status = 'pending'"
            ).fetchone()["c"]
            if pending_dinners_count:
                restaurant_alerts.append({"title": "Dinner reservations awaiting a decision", "detail": str(pending_dinners_count), "link": url_for("admin_restaurant")})
            opening = parse_date(restaurant_settings_row["opening_date"]) if restaurant_settings_row["opening_date"] else None
            if opening and today < opening <= today + timedelta(days=14):
                restaurant_alerts.append({"title": "Restaurant opens soon", "detail": f"{(opening - today).days} day(s) — {opening.isoformat()}", "link": url_for("admin_restaurant")})
        pending_workshop_regs = conn.execute(
            "SELECT COUNT(*) AS c FROM workshop_bookings WHERE status = 'pending'"
        ).fetchone()["c"]
        if pending_workshop_regs:
            restaurant_alerts.append({"title": "Workshop registrations awaiting a decision", "detail": str(pending_workshop_regs), "link": url_for("admin_workshop_registrations")})
        balances_due_soon = conn.execute(
            """SELECT COUNT(*) AS c FROM workshop_bookings WHERE status = 'confirmed'
               AND balance_amount > 0 AND balance_paid_at IS NULL
               AND balance_due_date IS NOT NULL AND balance_due_date <= ?""",
            ((today + timedelta(days=7)).isoformat(),),
        ).fetchone()["c"]
        if balances_due_soon:
            restaurant_alerts.append({"title": "Workshop balances due within 7 days", "detail": str(balances_due_soon), "link": url_for("admin_workshop_registrations")})
        new_event_inquiries = conn.execute(
            "SELECT COUNT(*) AS c FROM event_inquiries WHERE status = 'new'"
        ).fetchone()["c"]
        if new_event_inquiries:
            restaurant_alerts.append({"title": "New event inquiries", "detail": str(new_event_inquiries), "link": url_for("admin_events")})
    else:
        month_stats = None
        team = []
        on_shift_now = []
        who_is_off_today = []
        last_backup_at = None
        backup_stale = False
        current_month_financials = None
        low_ratings = []
        anniversaries = []
        probation_due = []
        unstaffed_days = []
        expiring_docs = []
        overtime = []
        activity = []
        vehicle_alerts = []
        breakfast_low_stock = []
        restaurant_alerts = []
        current_tasks_by_user = {}
        financial_trend_months = []
        financial_trend_max = 1
        dashboard_calendar = {"weeks": [], "month_start": today}
        stats["my_expenses_pending"] = conn.execute(
            "SELECT COUNT(*) AS c FROM expenses WHERE submitted_by_user_id = ? AND status = 'pending'",
            (user["id"],),
        ).fetchone()["c"]
        soon = (today + timedelta(days=30)).isoformat()
        my_expiring_docs = conn.execute(
            "SELECT * FROM documents WHERE user_id = ? AND expiry_date IS NOT NULL AND expiry_date <= ? ORDER BY expiry_date",
            (user["id"], soon),
        ).fetchall()
        week_ago_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        month_ago_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        my_hours_entries = conn.execute(
            "SELECT * FROM time_entries WHERE user_id = ? AND clock_in_at >= ? AND clock_out_at IS NOT NULL",
            (user["id"], month_ago_iso),
        ).fetchall()
        my_hours_map = net_hours_for_entries(conn, my_hours_entries)
        my_week_hours = round(sum(my_hours_map.get(r["id"], 0.0) for r in my_hours_entries
                                  if r["clock_in_at"] >= week_ago_iso), 2)
        my_month_hours = round(sum(my_hours_map.values()), 2)
        # Today's view: what's due today, overdue, or has no date yet — never
        # a future date, so this never turns into a forward-looking calendar.
        my_tasks = conn.execute(
            """SELECT * FROM tasks WHERE assigned_to_user_id = ? AND status != 'done'
               AND (due_date IS NULL OR due_date <= ?)
               ORDER BY (due_date IS NULL), due_date""",
            (user["id"], today.isoformat()),
        ).fetchall()

    my_upcoming_shifts = []
    if user["role"] != "owner":
        my_upcoming_shifts = conn.execute(
            "SELECT * FROM shifts WHERE user_id = ? AND shift_date >= ? ORDER BY shift_date, start_time LIMIT 7",
            (user["id"], today.isoformat()),
        ).fetchall()

    # The chef/partner running the restaurant are regular employees with no
    # owner access — this is their only view into tonight's covers without
    # asking the owner or waiting for the printed ops sheet.
    my_tonights_dinners = []
    is_restaurant_lead = False
    if user["role"] != "owner":
        restaurant_settings_row = get_restaurant_settings(conn)
        is_restaurant_lead = bool(restaurant_settings_row and restaurant_settings_row["lead_user_id"] == user["id"])
        if is_restaurant_lead:
            my_tonights_dinners = conn.execute(
                "SELECT * FROM restaurant_bookings WHERE status = 'confirmed' AND dinner_date = ? ORDER BY guest_name",
                (today.isoformat(),),
            ).fetchall()

    # Any employee can be assigned a dinner-service shift, not just the
    # restaurant lead — this is their own view of it, same "what do I need
    # to know" scope as my_upcoming_shifts above.
    my_restaurant_shifts = []
    if user["role"] != "owner":
        my_restaurant_shifts = conn.execute(
            "SELECT * FROM restaurant_shifts WHERE user_id = ? AND dinner_date >= ? ORDER BY dinner_date LIMIT 5",
            (user["id"], today.isoformat()),
        ).fetchall()

    # Same idea for a workshop instructor who's on staff — their own view
    # into upcoming sessions they're teaching, without owner access.
    my_upcoming_sessions = []
    my_next_session = None
    my_next_session_roster = []
    if user["role"] != "owner":
        my_upcoming_sessions = conn.execute(
            """SELECT workshop_sessions.*, workshops.title,
                   (SELECT COALESCE(SUM(party_size), 0) FROM workshop_bookings
                    WHERE session_id = workshop_sessions.id AND status = 'confirmed') AS covers
               FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
               WHERE workshops.instructor_user_id = ? AND workshop_sessions.end_date >= ?
               ORDER BY workshop_sessions.start_date LIMIT 5""",
            (user["id"], today.isoformat()),
        ).fetchall()
        if my_upcoming_sessions:
            my_next_session = my_upcoming_sessions[0]
            my_next_session_roster = conn.execute(
                """SELECT workshop_bookings.*, rooms.name AS room_name
                   FROM workshop_bookings LEFT JOIN rooms ON rooms.id = workshop_bookings.assigned_room_id
                   WHERE workshop_bookings.session_id = ? AND workshop_bookings.status = 'confirmed'
                   ORDER BY workshop_bookings.guest_name""",
                (my_next_session["id"],),
            ).fetchall()

    conn.close()
    return render_template(
        "dashboard.html", team=team, stats=stats, briefing=briefing, my_tasks=my_tasks,
        who_is_here=who_is_here, today=today, month_stats=month_stats,
        my_upcoming_shifts=my_upcoming_shifts, who_is_off_today=who_is_off_today,
        on_shift_by_user=on_shift_by_user, on_shift_now=on_shift_now, my_shift=my_shift,
        anniversaries=anniversaries, probation_due=probation_due, unstaffed_days=unstaffed_days,
        expiring_docs=expiring_docs, overtime=overtime,
        my_open_break=my_open_break, activity=activity, announcements_current=announcements_current,
        my_expiring_docs=my_expiring_docs, my_week_hours=my_week_hours, my_month_hours=my_month_hours,
        backup_stale=backup_stale, last_backup_at=last_backup_at,
        current_month_financials=current_month_financials, low_ratings=low_ratings,
        vehicle_alerts=vehicle_alerts, breakfast_low_stock=breakfast_low_stock,
        restaurant_alerts=restaurant_alerts, is_restaurant_lead=is_restaurant_lead,
        my_tonights_dinners=my_tonights_dinners, my_restaurant_shifts=my_restaurant_shifts,
        my_upcoming_sessions=my_upcoming_sessions,
        my_next_session=my_next_session, my_next_session_roster=my_next_session_roster,
        current_tasks_by_user=current_tasks_by_user, financial_trend_months=financial_trend_months,
        financial_trend_max=financial_trend_max, dashboard_calendar=dashboard_calendar,
    )


@app.route("/admin/display")
def office_display():
    # A kiosk device authenticates with ?token=OFFICE_DISPLAY_TOKEN instead of a
    # login session, since it's meant to sit on a wall reloading itself
    # unattended for weeks. Anyone with a normal owner session still gets in
    # without one, same as before.
    supplied_token = request.args.get("token", "")
    token_ok = OFFICE_DISPLAY_TOKEN and hmac.compare_digest(supplied_token, OFFICE_DISPLAY_TOKEN)
    if not token_ok:
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user["role"] != "owner":
            abort(403)

    conn = get_db()
    today = datetime.now(timezone.utc).date()

    on_shift_now = [
        dict(r, initials="".join(w[0] for w in r["user_name"].split()[:2]).upper())
        for r in conn.execute(
            """SELECT time_entries.*, users.name AS user_name, users.job_role AS user_job_role
               FROM time_entries JOIN users ON users.id = time_entries.user_id
               WHERE time_entries.clock_out_at IS NULL
               ORDER BY time_entries.clock_in_at"""
        ).fetchall()
    ]
    current_tasks_by_user = {}
    for t in conn.execute(
        """SELECT * FROM tasks WHERE status != 'done' AND assigned_to_user_id IS NOT NULL
           AND (due_date IS NULL OR due_date <= ?) ORDER BY (due_date IS NULL), due_date""",
        (today.isoformat(),),
    ).fetchall():
        current_tasks_by_user.setdefault(t["assigned_to_user_id"], []).append(t)

    who_is_here = guests_in_residence(conn, today)

    overview_today = build_overview(conn, "day", today)
    dashboard_calendar = build_dashboard_calendar(conn, today)
    today_stats = build_office_display_stats(conn, today, who_is_here)
    queues = build_office_display_queues(conn, today)
    queues_total = sum(q["count"] for q in queues)

    conn.close()
    return render_template(
        "admin_office_display.html",
        today=today,
        on_shift_now=on_shift_now,
        current_tasks_by_user=current_tasks_by_user,
        who_is_here=who_is_here,
        overview_today=overview_today,
        dashboard_calendar=dashboard_calendar,
        today_stats=today_stats,
        queues=queues,
        queues_total=queues_total,
    )


@app.route("/search")
@owner_required
def search():
    q = request.args.get("q", "").strip()
    results = {"guests": [], "employees": [], "tasks": [], "rooms": [], "vendors": [],
               "recurring_costs": [], "company_info": [], "waitlist": [], "vehicles": [],
               "restaurant_bookings": [], "workshop_bookings": [], "social_posts": [],
               "event_inquiries": [], "promo_codes": []}
    if q:
        needle = f"%{q}%"
        conn = get_db()
        results["guests"] = conn.execute(
            """SELECT * FROM guests
               WHERE name LIKE ? OR notes LIKE ? OR email LIKE ? OR phone LIKE ?
                  OR preferences LIKE ? OR dietary_notes LIKE ?
               ORDER BY name LIMIT 20""",
            (needle,) * 6,
        ).fetchall()
        results["employees"] = conn.execute(
            "SELECT * FROM users WHERE role = 'employee' AND (name LIKE ? OR job_role LIKE ?) ORDER BY name LIMIT 20",
            (needle, needle),
        ).fetchall()
        results["tasks"] = conn.execute(
            """SELECT tasks.*, users.name AS assigned_to_name FROM tasks
               LEFT JOIN users ON users.id = tasks.assigned_to_user_id
               WHERE tasks.title LIKE ? OR tasks.notes LIKE ? ORDER BY tasks.due_date DESC LIMIT 20""",
            (needle, needle),
        ).fetchall()
        results["rooms"] = conn.execute(
            "SELECT * FROM rooms WHERE name LIKE ? ORDER BY sort_order LIMIT 20", (needle,)
        ).fetchall()
        results["vendors"] = conn.execute(
            "SELECT * FROM vendors WHERE name LIKE ? OR contact_person LIKE ? OR notes LIKE ? ORDER BY name LIMIT 20",
            (needle, needle, needle),
        ).fetchall()
        results["recurring_costs"] = conn.execute(
            "SELECT * FROM recurring_costs WHERE label LIKE ? OR category LIKE ? ORDER BY label LIMIT 20",
            (needle, needle),
        ).fetchall()
        results["waitlist"] = conn.execute(
            "SELECT * FROM waitlist_entries WHERE name LIKE ? OR email LIKE ? ORDER BY created_at DESC LIMIT 20",
            (needle, needle),
        ).fetchall()
        results["vehicles"] = conn.execute(
            "SELECT * FROM vehicles WHERE name LIKE ? OR vehicle_type LIKE ? OR license_plate LIKE ? ORDER BY name LIMIT 20",
            (needle, needle, needle),
        ).fetchall()
        results["restaurant_bookings"] = conn.execute(
            "SELECT * FROM restaurant_bookings WHERE guest_name LIKE ? OR reference_code LIKE ? OR guest_email LIKE ? "
            "ORDER BY dinner_date DESC LIMIT 20",
            (needle, needle, needle),
        ).fetchall()
        results["workshop_bookings"] = conn.execute(
            """SELECT workshop_bookings.*, workshops.title AS workshop_title FROM workshop_bookings
               JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
               JOIN workshops ON workshops.id = workshop_sessions.workshop_id
               WHERE workshop_bookings.guest_name LIKE ? OR workshop_bookings.reference_code LIKE ?
                  OR workshop_bookings.guest_email LIKE ? OR workshops.title LIKE ?
               ORDER BY workshop_bookings.created_at DESC LIMIT 20""",
            (needle, needle, needle, needle),
        ).fetchall()
        results["social_posts"] = conn.execute(
            "SELECT * FROM social_posts WHERE caption LIKE ? OR platform LIKE ? "
            "ORDER BY (scheduled_date IS NULL), scheduled_date DESC LIMIT 20",
            (needle, needle),
        ).fetchall()
        results["event_inquiries"] = conn.execute(
            "SELECT * FROM event_inquiries WHERE contact_name LIKE ? OR contact_email LIKE ? "
            "OR reference_code LIKE ? OR event_type LIKE ? ORDER BY created_at DESC LIMIT 20",
            (needle, needle, needle, needle),
        ).fetchall()
        results["promo_codes"] = conn.execute(
            "SELECT * FROM promo_codes WHERE code LIKE ? OR description LIKE ? ORDER BY created_at DESC LIMIT 20",
            (needle, needle),
        ).fetchall()
        info = conn.execute("SELECT * FROM company_info WHERE id = 1").fetchone()
        if info and any(
            q.lower() in (info[f] or "").lower()
            for f in ("legal_name", "registration_number", "vat_number", "registered_address",
                       "accountant_name", "insurance_broker_name")
        ):
            results["company_info"] = [info]
        conn.close()
    total = sum(len(v) for v in results.values())
    return render_template("search_results.html", q=q, results=results, total=total)


# ---------------------------------------------------------------------------
# Directory (owner: manage everyone / employee: view own profile)
# ---------------------------------------------------------------------------

@app.route("/directory")
@login_required
def directory():
    user = current_user()
    conn = get_db()
    status_filter = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()

    period = period_from_request()
    overview = None
    if user["role"] == "owner":
        overview = employee_overview(conn, period, datetime.now(timezone.utc).date())
        employees = conn.execute(
            "SELECT * FROM users WHERE role = 'employee' ORDER BY status, name"
        ).fetchall()
    else:
        # A colleague lookup, not an admin tool — active staff only, and
        # the query itself only asks for the fields safe to show a
        # coworker (no pay/notes/etc.), rather than relying on the
        # template to not render fields off a wider SELECT *.
        employees = conn.execute(
            "SELECT id, name, job_role, phone, status, account_claimed FROM users "
            "WHERE role = 'employee' AND status = 'active' ORDER BY name"
        ).fetchall()
    on_shift_ids = {
        r["user_id"] for r in conn.execute("SELECT user_id FROM time_entries WHERE clock_out_at IS NULL").fetchall()
    }
    conn.close()

    if status_filter:
        employees = [e for e in employees if e["status"] == status_filter]
    if q:
        needle = q.lower()
        employees = [
            e for e in employees
            if needle in e["name"].lower() or needle in (e["job_role"] or "").lower()
        ]

    return render_template(
        "directory.html", employees=employees, status_filter=status_filter, q=q, on_shift_ids=on_shift_ids,
        overview=overview, period=period,
    )


@app.route("/directory/export.csv")
@owner_required
def export_team_csv():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM users WHERE role = 'employee' ORDER BY status, name"
    ).fetchall()
    conn.close()
    fieldnames = ["name", "job_role", "phone", "email", "start_date", "status"]
    return csv_response(fieldnames, rows, "team.csv")


@app.route("/directory/pay-history/export.csv")
@owner_required
def export_pay_history_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT pay_rate_history.*, users.name AS employee_name, changer.name AS changed_by_name
           FROM pay_rate_history
           JOIN users ON users.id = pay_rate_history.user_id
           LEFT JOIN users AS changer ON changer.id = pay_rate_history.changed_by_user_id
           ORDER BY pay_rate_history.changed_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["employee_name", "old_pay_rate", "new_pay_rate", "old_pay_type", "new_pay_type",
                  "changed_by_name", "changed_at"]
    return csv_response(fieldnames, rows, "pay_rate_history.csv")


@app.route("/directory/new", methods=["GET", "POST"])
@owner_required
def new_employee():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_role = request.form.get("job_role", "").strip()
        phone = request.form.get("phone", "").strip()
        start_date = request.form.get("start_date", "").strip()
        pay_rate = request.form.get("pay_rate", "").strip()
        pay_type = request.form.get("pay_type", "").strip()
        notes = request.form.get("notes", "").strip()
        annual_leave_days = request.form.get("annual_leave_days", "").strip()
        annual_leave_days = int(annual_leave_days) if annual_leave_days.isdigit() else None
        contract_type, contract_end_date, trial_end_date, notice_period_days = contract_fields_from_form()

        if not name or not email:
            flash("Name and email are required.", "error")
            return render_template("employee_form.html", employee=None)

        invite_token = secrets.token_urlsafe(24)
        # Nobody knows this password — it's an unusable placeholder until the
        # employee claims their account via the onboarding link and sets
        # their own, at which point this row is overwritten.
        placeholder_hash = generate_password_hash(secrets.token_hex(32))
        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO users
                   (email, password_hash, role, name, job_role, phone, start_date,
                    status, pay_rate, pay_type, notes, account_claimed, invite_token, created_at,
                    annual_leave_days, contract_type, contract_end_date, trial_end_date,
                    notice_period_days)
                   VALUES (?, ?, 'employee', ?, ?, ?, ?, 'active', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
                (email, placeholder_hash, name, job_role,
                 phone, start_date, pay_rate, pay_type, notes, invite_token,
                 datetime.now(timezone.utc).isoformat(), annual_leave_days,
                 contract_type, contract_end_date, trial_end_date, notice_period_days),
            )
            conn.commit()
            new_employee_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            create_draft_agreement(conn, new_employee_row)
            seed_onboarding_checklist(conn, new_employee_row["id"])
            conn.commit()
            conn.close()
            onboarding_url = url_for("onboard", token=invite_token, _external=True)
            flash(
                f"{name} added. Send them this onboarding link so they can set up "
                f"their own login: {onboarding_url}",
                "success",
            )
            return redirect(url_for("profile", user_id=new_employee_row["id"]))
        except sqlite3.IntegrityError:
            conn.close()
            flash("An account with that email already exists.", "error")
            return render_template("employee_form.html", employee=None)

    return render_template(
        "employee_form.html", employee=None,
        prefill_name=request.args.get("name", ""), prefill_email=request.args.get("email", ""),
        prefill_phone=request.args.get("phone", ""), prefill_job_role=request.args.get("job_role", ""),
    )


@app.route("/directory/<int:user_id>")
@login_required
def profile(user_id):
    user = current_user()
    if user["role"] != "owner" and user["id"] != user_id:
        abort(403)
    conn = get_db()
    person = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not person:
        conn.close()
        abort(404)
    docs = conn.execute(
        "SELECT * FROM documents WHERE user_id = ? ORDER BY uploaded_at DESC", (user_id,)
    ).fetchall()
    recent_shifts = conn.execute(
        "SELECT * FROM time_entries WHERE user_id = ? ORDER BY clock_in_at DESC LIMIT 10", (user_id,)
    ).fetchall()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    _week_entries = conn.execute(
        "SELECT * FROM time_entries WHERE user_id = ? AND clock_in_at >= ? AND clock_out_at IS NOT NULL",
        (user_id, week_ago),
    ).fetchall()
    week_total_hours = sum(net_hours_for_entries(conn, _week_entries).values())
    expense_claims = conn.execute(
        """SELECT * FROM expenses WHERE kind = 'staff_expense' AND submitted_by_user_id = ?
           ORDER BY submitted_at DESC""",
        (user_id,),
    ).fetchall()
    today_for_stats = datetime.now(timezone.utc).date()
    week_ago_for_stats = (today_for_stats - timedelta(days=7)).isoformat()
    month_ago_for_stats = (today_for_stats - timedelta(days=30)).isoformat()
    task_stats = {
        "week": conn.execute(
            """SELECT COUNT(*) AS c FROM tasks WHERE assigned_to_user_id = ? AND status = 'done'
               AND completed_at >= ?""",
            (user_id, week_ago_for_stats),
        ).fetchone()["c"],
        "month": conn.execute(
            """SELECT COUNT(*) AS c FROM tasks WHERE assigned_to_user_id = ? AND status = 'done'
               AND completed_at >= ?""",
            (user_id, month_ago_for_stats),
        ).fetchone()["c"],
        "open": conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE assigned_to_user_id = ? AND status != 'done'",
            (user_id,),
        ).fetchone()["c"],
    }
    onboarding_items = []
    offboarding_items = []
    check_in_notes = []
    equipment_items = []
    pay_rate_history = []
    if user["role"] == "owner":
        offboarding_items = conn.execute(
            "SELECT * FROM offboarding_items WHERE user_id = ? ORDER BY sort_order, id", (user_id,)
        ).fetchall()
        check_in_notes = conn.execute(
            "SELECT * FROM check_in_notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        pay_rate_history = conn.execute(
            """SELECT pay_rate_history.*, users.name AS changed_by_name FROM pay_rate_history
               LEFT JOIN users ON users.id = pay_rate_history.changed_by_user_id
               WHERE pay_rate_history.user_id = ? ORDER BY changed_at DESC""",
            (user_id,),
        ).fetchall()
    # Onboarding steps and issued equipment are about the person whose
    # profile this is — worth showing them on their own page read-only,
    # unlike offboarding/check-in notes/pay history, which stay strictly
    # owner-only (private commentary or sensitive pay data).
    if user["role"] == "owner" or user_id == user["id"]:
        onboarding_items = conn.execute(
            "SELECT * FROM onboarding_items WHERE user_id = ? ORDER BY sort_order, id", (user_id,)
        ).fetchall()
        equipment_items = conn.execute(
            "SELECT * FROM equipment_items WHERE user_id = ? ORDER BY (returned_at IS NOT NULL), issued_at DESC", (user_id,)
        ).fetchall()
    leave = leave_balance(conn, user_id, person["annual_leave_days"]) if person["role"] == "employee" else None
    # What they hold, and where they fall short of what their role requires —
    # both owner-only, and both read BEFORE the connection closes.
    shift_hours = net_hours_for_entries(conn, recent_shifts)
    access_holdings = access_held_by(conn, user_id) if user["role"] == "owner" else []
    compliance_gaps = [
        g for g in role_compliance(conn, datetime.now(timezone.utc).date())
        if g["user_id"] == user_id
    ] if user["role"] == "owner" else []
    conn.close()
    onboarding_url = None
    if user["role"] == "owner" and not person["account_claimed"]:
        onboarding_url = url_for("onboard", token=person["invite_token"], _external=True)

    return render_template(
        "profile.html", person=person, docs=docs, onboarding_url=onboarding_url,
        recent_shifts=recent_shifts, week_total_hours=round(week_total_hours, 2),
        expense_claims=expense_claims, onboarding_items=onboarding_items, check_in_notes=check_in_notes,
        task_stats=task_stats, leave=leave, offboarding_items=offboarding_items,
        equipment_items=equipment_items, pay_rate_history=pay_rate_history,
        access_holdings=access_holdings, compliance_gaps=compliance_gaps,
        shift_hours=shift_hours,
    )


@app.route("/directory/<int:user_id>/edit", methods=["GET", "POST"])
@owner_required
def edit_employee(user_id):
    conn = get_db()
    person = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not person:
        conn.close()
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        job_role = request.form.get("job_role", "").strip()
        phone = request.form.get("phone", "").strip()
        start_date = request.form.get("start_date", "").strip()
        status = request.form.get("status", "active")
        pay_rate = request.form.get("pay_rate", "").strip()
        pay_type = request.form.get("pay_type", "").strip()
        notes = request.form.get("notes", "").strip()
        skills = request.form.get("skills", "").strip()
        emergency_contact_name = request.form.get("emergency_contact_name", "").strip()
        emergency_contact_phone = request.form.get("emergency_contact_phone", "").strip()
        emergency_contact_relationship = request.form.get("emergency_contact_relationship", "").strip()
        annual_leave_days = request.form.get("annual_leave_days", "").strip()
        annual_leave_days = int(annual_leave_days) if annual_leave_days.isdigit() else None
        reason_for_leaving = request.form.get("reason_for_leaving", "").strip()
        contract_type, contract_end_date, trial_end_date, notice_period_days = contract_fields_from_form()

        conn.execute(
            """UPDATE users SET name=?, job_role=?, phone=?, start_date=?,
               status=?, pay_rate=?, pay_type=?, notes=?, skills=?,
               emergency_contact_name=?, emergency_contact_phone=?, emergency_contact_relationship=?,
               annual_leave_days=?, reason_for_leaving=?,
               contract_type=?, contract_end_date=?, trial_end_date=?, notice_period_days=?
               WHERE id=?""",
            (name, job_role, phone, start_date, status, pay_rate, pay_type, notes, skills,
             emergency_contact_name, emergency_contact_phone, emergency_contact_relationship,
             annual_leave_days, reason_for_leaving or None,
             contract_type, contract_end_date, trial_end_date, notice_period_days, user_id),
        )
        maybe_seed_offboarding(conn, user_id, person["status"], status)
        if (person["pay_rate"] or None) != (pay_rate or None) or (person["pay_type"] or None) != (pay_type or None):
            conn.execute(
                """INSERT INTO pay_rate_history
                   (user_id, old_pay_rate, new_pay_rate, old_pay_type, new_pay_type, changed_by_user_id, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, person["pay_rate"], pay_rate or None, person["pay_type"], pay_type or None,
                 current_user()["id"], datetime.now(timezone.utc).isoformat()),
            )
            log_audit(
                conn, "pay_rate_changed", target=person["name"],
                details=f"{person['pay_rate'] or '(unset)'}/{person['pay_type'] or '(unset)'} -> {pay_rate or '(unset)'}/{pay_type or '(unset)'}",
            )
        conn.commit()
        conn.close()
        flash("Profile updated.", "success")
        return redirect(url_for("profile", user_id=user_id))

    conn.close()
    return render_template("employee_form.html", employee=person)


@app.route("/directory/<int:user_id>/toggle-status", methods=["POST"])
@owner_required
def toggle_employee_status(user_id):
    conn = get_db()
    person = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'employee'", (user_id,)).fetchone()
    if not person:
        conn.close()
        abort(404)
    new_status = "inactive" if person["status"] == "active" else "active"
    conn.execute("UPDATE users SET status = ? WHERE id = ?", (new_status, user_id))
    maybe_seed_offboarding(conn, user_id, person["status"], new_status)
    log_audit(conn, "employee_status_changed", target=person["name"], details=f"{person['status']} -> {new_status}")
    conn.commit()
    conn.close()
    flash(f"{person['name']} marked {new_status}.", "success")
    return redirect(request.referrer or url_for("directory"))


@app.route("/directory/<int:user_id>/delete", methods=["POST"])
@owner_required
def delete_employee(user_id):
    conn = get_db()
    # Mirrors delete_room/delete_promo_code's own guard against destroying
    # real history — worked hours and pay-rate changes are payroll records,
    # not disposable app data, and PRAGMA foreign_keys=ON means a hard
    # delete here cascades through them permanently with no undo.
    has_history = conn.execute(
        "SELECT (SELECT COUNT(*) FROM time_entries WHERE user_id = ?) "
        "+ (SELECT COUNT(*) FROM pay_rate_history WHERE user_id = ?)",
        (user_id, user_id),
    ).fetchone()[0]
    if has_history:
        conn.close()
        flash("Can't delete an employee with timesheet or pay history — mark them inactive instead.", "error")
        return redirect(url_for("directory"))
    person = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    docs = conn.execute("SELECT filename FROM documents WHERE user_id = ?", (user_id,)).fetchall()
    for d in docs:
        path = os.path.join(UPLOAD_DIR, d["filename"])
        if os.path.exists(path):
            os.remove(path)
    conn.execute("DELETE FROM users WHERE id = ? AND role = 'employee'", (user_id,))
    if person:
        log_audit(conn, "employee_deleted", target=person["name"])
    conn.commit()
    conn.close()
    flash("Employee removed.", "success")
    return redirect(url_for("directory"))


@app.route("/directory/<int:user_id>/regenerate-invite", methods=["POST"])
@owner_required
def regenerate_invite(user_id):
    conn = get_db()
    person = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'employee'", (user_id,)).fetchone()
    if not person:
        conn.close()
        abort(404)
    new_token = secrets.token_urlsafe(24)
    conn.execute(
        "UPDATE users SET invite_token = ?, account_claimed = 0, password_hash = ? WHERE id = ?",
        (new_token, generate_password_hash(secrets.token_hex(32)), user_id),
    )
    conn.commit()
    conn.close()
    onboarding_url = url_for("onboard", token=new_token, _external=True)
    flash(f"New onboarding link ready: {onboarding_url}", "success")
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/onboarding/<int:item_id>/toggle", methods=["POST"])
@owner_required
def toggle_onboarding_item(user_id, item_id):
    conn = get_db()
    item = conn.execute(
        "SELECT * FROM onboarding_items WHERE id = ? AND user_id = ?", (item_id, user_id)
    ).fetchone()
    if not item:
        conn.close()
        abort(404)
    conn.execute("UPDATE onboarding_items SET done = ? WHERE id = ?", (0 if item["done"] else 1, item_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/onboarding/new", methods=["POST"])
@owner_required
def new_onboarding_item(user_id):
    label = request.form.get("label", "").strip()
    if label:
        conn = get_db()
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM onboarding_items WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        conn.execute(
            "INSERT INTO onboarding_items (user_id, label, sort_order, created_at) VALUES (?, ?, ?, ?)",
            (user_id, label, max_order + 1, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/onboarding/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_onboarding_item(user_id, item_id):
    conn = get_db()
    conn.execute("DELETE FROM onboarding_items WHERE id = ? AND user_id = ?", (item_id, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/offboarding/<int:item_id>/toggle", methods=["POST"])
@owner_required
def toggle_offboarding_item(user_id, item_id):
    conn = get_db()
    item = conn.execute(
        "SELECT * FROM offboarding_items WHERE id = ? AND user_id = ?", (item_id, user_id)
    ).fetchone()
    if not item:
        conn.close()
        abort(404)
    conn.execute("UPDATE offboarding_items SET done = ? WHERE id = ?", (0 if item["done"] else 1, item_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/offboarding/new", methods=["POST"])
@owner_required
def new_offboarding_item(user_id):
    label = request.form.get("label", "").strip()
    if label:
        conn = get_db()
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM offboarding_items WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        conn.execute(
            "INSERT INTO offboarding_items (user_id, label, sort_order, created_at) VALUES (?, ?, ?, ?)",
            (user_id, label, max_order + 1, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/offboarding/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_offboarding_item(user_id, item_id):
    conn = get_db()
    conn.execute("DELETE FROM offboarding_items WHERE id = ? AND user_id = ?", (item_id, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/equipment/new", methods=["POST"])
@owner_required
def new_equipment_item(user_id):
    label = request.form.get("label", "").strip()
    notes = request.form.get("notes", "").strip()
    if label:
        conn = get_db()
        conn.execute(
            "INSERT INTO equipment_items (user_id, label, notes, issued_at) VALUES (?, ?, ?, ?)",
            (user_id, label, notes or None, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/equipment/<int:item_id>/toggle-returned", methods=["POST"])
@owner_required
def toggle_equipment_returned(user_id, item_id):
    conn = get_db()
    item = conn.execute(
        "SELECT * FROM equipment_items WHERE id = ? AND user_id = ?", (item_id, user_id)
    ).fetchone()
    if not item:
        conn.close()
        abort(404)
    new_value = None if item["returned_at"] else datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE equipment_items SET returned_at = ? WHERE id = ?", (new_value, item_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/equipment/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_equipment_item(user_id, item_id):
    conn = get_db()
    conn.execute("DELETE FROM equipment_items WHERE id = ? AND user_id = ?", (item_id, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/equipment")
@owner_required
def equipment_overview():
    conn = get_db()
    issued = conn.execute(
        """SELECT equipment_items.*, users.name AS employee_name FROM equipment_items
           JOIN users ON users.id = equipment_items.user_id
           WHERE returned_at IS NULL ORDER BY users.name, equipment_items.issued_at"""
    ).fetchall()
    conn.close()
    return render_template("equipment_overview.html", issued=issued)


@app.route("/equipment/export.csv")
@owner_required
def export_equipment_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT equipment_items.*, users.name AS employee_name FROM equipment_items
           JOIN users ON users.id = equipment_items.user_id
           ORDER BY (returned_at IS NOT NULL), users.name, equipment_items.issued_at"""
    ).fetchall()
    conn.close()
    fieldnames = ["employee_name", "label", "notes", "issued_at", "returned_at"]
    return csv_response(fieldnames, rows, "equipment.csv")


# ---------------------------------------------------------------------------
# Candidates — a lightweight pre-employment pipeline. Separate from `users`
# entirely; nothing here becomes a real account until the owner manually
# adds them via "Add employee" once hired.
# ---------------------------------------------------------------------------

@app.route("/candidates")
@owner_required
def candidates():
    status_filter = request.args.get("status", "").strip()
    conn = get_db()
    query = "SELECT * FROM candidates"
    params = ()
    if status_filter:
        query += " WHERE status = ?"
        params = (status_filter,)
    query += " ORDER BY (status IN ('hired','rejected')), created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return render_template("candidates.html", candidates=rows, status_filter=status_filter)


@app.route("/candidates/export.csv")
@owner_required
def export_candidates_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM candidates ORDER BY (status IN ('hired','rejected')), created_at DESC").fetchall()
    conn.close()
    fieldnames = ["name", "email", "phone", "role_applied", "status", "notes", "created_at"]
    return csv_response(fieldnames, rows, "candidates.csv")


@app.route("/candidates/new", methods=["POST"])
@owner_required
def new_candidate():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    role_applied = request.form.get("role_applied", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("candidates"))
    conn = get_db()
    conn.execute(
        """INSERT INTO candidates (name, email, phone, role_applied, status, notes, created_at)
           VALUES (?, ?, ?, ?, 'new', ?, ?)""",
        (name, email or None, phone or None, role_applied or None, notes or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash(f"{name} added to the pipeline.", "success")
    return redirect(url_for("candidates"))


@app.route("/candidates/<int:candidate_id>/edit", methods=["POST"])
@owner_required
def edit_candidate(candidate_id):
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    role_applied = request.form.get("role_applied", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("candidates"))
    conn = get_db()
    conn.execute(
        """UPDATE candidates SET name = ?, email = ?, phone = ?, role_applied = ?, notes = ?,
           updated_at = ? WHERE id = ?""",
        (name, email or None, phone or None, role_applied or None, notes or None,
         datetime.now(timezone.utc).isoformat(), candidate_id),
    )
    conn.commit()
    conn.close()
    flash("Candidate updated.", "success")
    return redirect(url_for("candidates"))


@app.route("/candidates/<int:candidate_id>/status", methods=["POST"])
@owner_required
def update_candidate_status(candidate_id):
    status = request.form.get("status", "")
    if status not in ("new", "interviewing", "offered", "hired", "rejected"):
        abort(400)
    conn = get_db()
    conn.execute(
        "UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?",
        (status, datetime.now(timezone.utc).isoformat(), candidate_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("candidates"))


@app.route("/candidates/<int:candidate_id>/delete", methods=["POST"])
@owner_required
def delete_candidate(candidate_id):
    conn = get_db()
    conn.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("candidates"))


@app.route("/directory/<int:user_id>/notes/new", methods=["POST"])
@owner_required
def new_check_in_note(user_id):
    body = request.form.get("body", "").strip()
    if body:
        conn = get_db()
        conn.execute(
            "INSERT INTO check_in_notes (user_id, body, created_at) VALUES (?, ?, ?)",
            (user_id, body, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    return redirect(url_for("profile", user_id=user_id))


@app.route("/directory/<int:user_id>/notes/<int:note_id>/delete", methods=["POST"])
@owner_required
def delete_check_in_note(user_id, note_id):
    conn = get_db()
    conn.execute("DELETE FROM check_in_notes WHERE id = ? AND user_id = ?", (note_id, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("profile", user_id=user_id))


# ---------------------------------------------------------------------------
# HR hub — certifications, availability, absence, working time, reviews.
# One page rather than five, because these are all "how is the team doing"
# questions the owner asks together, not separate errands.
# ---------------------------------------------------------------------------

@app.route("/admin/hr")
@owner_required
def admin_hr():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    period = period_from_request()
    employees = conn.execute(
        "SELECT * FROM users WHERE role = 'employee' AND status = 'active' ORDER BY name"
    ).fetchall()

    # Overview band: how the team looks over the chosen window, rather than
    # opening straight onto sub-sections.
    hours_row = conn.execute(
        """SELECT COALESCE(SUM((julianday(clock_out_at) - julianday(clock_in_at)) * 24), 0) AS h
           FROM time_entries WHERE clock_out_at IS NOT NULL AND clock_out_at > clock_in_at
             AND clock_in_at >= ? AND clock_in_at < ?""",
        (period["start_iso"], period["end_iso"]),
    ).fetchone()
    prev_hours_row = conn.execute(
        """SELECT COALESCE(SUM((julianday(clock_out_at) - julianday(clock_in_at)) * 24), 0) AS h
           FROM time_entries WHERE clock_out_at IS NOT NULL AND clock_out_at > clock_in_at
             AND clock_in_at >= ? AND clock_in_at < ?""",
        (period["prev_start_iso"], period["prev_end_iso"]),
    ).fetchone()
    absence_days = conn.execute(
        """SELECT COALESCE(SUM(julianday(MIN(end_date, ?)) - julianday(MAX(start_date, ?)) + 1), 0) AS d
           FROM absences WHERE start_date < ? AND end_date >= ?""",
        (period["end_iso"], period["start_iso"], period["end_iso"], period["start_iso"]),
    ).fetchone()
    leave_taken = conn.execute(
        """SELECT COUNT(*) AS c FROM leave_requests
           WHERE status = 'approved' AND start_date < ? AND end_date >= ?""",
        (period["end_iso"], period["start_iso"]),
    ).fetchone()["c"]
    on_shift_now = conn.execute(
        "SELECT COUNT(*) AS c FROM time_entries WHERE clock_out_at IS NULL"
    ).fetchone()["c"]

    hr_overview = {
        "headcount": len(employees),
        "on_shift": on_shift_now,
        "hours": round(hours_row["h"], 1),
        "hours_prev": round(prev_hours_row["h"], 1),
        "absence_days": int(absence_days["d"] or 0),
        "leave_periods": leave_taken,
    }

    certs_expiring = expiring_certifications(conn, today)
    all_certs = conn.execute(
        """SELECT certifications.*, users.name AS employee_name FROM certifications
           JOIN users ON users.id = certifications.user_id
           ORDER BY (certifications.expiry_date IS NULL), certifications.expiry_date"""
    ).fetchall()

    week_ahead = today + timedelta(days=14)
    availability_clashes = unavailable_assigned_shifts(conn, today, week_ahead)

    absences = conn.execute(
        """SELECT absences.*, users.name AS employee_name FROM absences
           JOIN users ON users.id = absences.user_id
           ORDER BY absences.start_date DESC LIMIT 50"""
    ).fetchall()
    year_ago = today - timedelta(days=365)
    bradford = []
    for e in employees:
        score = bradford_factor(conn, e["id"], year_ago)
        if score["spells"]:
            bradford.append(dict(score, employee_name=e["name"], user_id=e["id"]))
    bradford.sort(key=lambda b: b["score"], reverse=True)

    violations = working_time_violations(conn, today - timedelta(days=28), today)

    reviews = conn.execute(
        """SELECT performance_reviews.*, users.name AS employee_name,
                  reviewer.name AS reviewer_name
           FROM performance_reviews
           JOIN users ON users.id = performance_reviews.user_id
           LEFT JOIN users AS reviewer ON reviewer.id = performance_reviews.reviewer_user_id
           ORDER BY performance_reviews.review_date DESC LIMIT 50"""
    ).fetchall()
    reviewed_ids = {r["user_id"] for r in conn.execute(
        "SELECT DISTINCT user_id FROM performance_reviews WHERE review_date >= ?",
        ((today - timedelta(days=365)).isoformat(),),
    ).fetchall()}
    review_due = [e for e in employees if e["id"] not in reviewed_ids]

    # Live chase-up board: everything waiting on someone, oldest first, with
    # whatever state the escalation engine has recorded against it.
    rules = hr_escalation_rules(conn)
    tracked = {
        (r["item_type"], r["item_id"]): r for r in conn.execute(
            "SELECT * FROM hr_escalations WHERE resolved_at IS NULL").fetchall()
    }
    hr_actions = []
    for a in collect_hr_actions(conn, today):
        cfg = HR_ACTION_TYPES.get(a["type"], {})
        rule = rules.get(a["type"])
        since = parse_date((a["since"] or "")[:10]) or today
        age = (today - since).days
        state = tracked.get((a["type"], a["item_id"]))
        hr_actions.append({
            **a,
            "label": cfg.get("label", a["type"]),
            "actor": cfg.get("actor", "owner"),
            "age_days": age,
            "sla_days": rule["sla_days"] if rule else None,
            "overdue": bool(rule and age >= rule["sla_days"]),
            "escalated": bool(state and state["escalated_at"]),
            "reminded": bool(state and state["reminded_at"]),
        })
    hr_actions.sort(key=lambda x: -x["age_days"])
    hr_overdue_count = sum(1 for a in hr_actions if a["overdue"])
    # Computed BEFORE the connection closes — building these inside the
    # render_template() call ran them against a closed handle.
    deadlines = contract_deadlines(conn, today)
    compliance_gaps = role_compliance(conn, today)

    conn.close()
    return render_template(
        "admin_hr.html", today=today, employees=employees,
        period=period, hr_overview=hr_overview,
        hr_actions=hr_actions, hr_overdue_count=hr_overdue_count,
        hr_rules=sorted(rules.values(), key=lambda r: r["item_type"]),
        hr_action_types=HR_ACTION_TYPES,
        certs_expiring=certs_expiring, all_certs=all_certs,
        availability_clashes=availability_clashes, week_ahead=week_ahead,
        absences=absences, bradford=bradford, violations=violations,
        reviews=reviews, review_due=review_due,
        rest_hours=MIN_REST_HOURS_BETWEEN_SHIFTS,
        max_days=MAX_CONSECUTIVE_DAYS_WORKED, max_weekly=MAX_WEEKLY_HOURS,
        deadlines=deadlines, compliance_gaps=compliance_gaps,
    )


@app.route("/admin/hr/escalation-rules", methods=["POST"])
@owner_required
def update_hr_escalation_rules():
    """Tune how long each kind of HR item may sit before someone is chased,
    and how long before it goes over their head."""
    conn = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    for rule in conn.execute("SELECT id, item_type FROM hr_escalation_rules").fetchall():
        sla = _positive_float(request.form.get(f"sla_{rule['id']}"), None)
        esc = _positive_float(request.form.get(f"esc_{rule['id']}"), None)
        if sla is None or esc is None:
            continue
        # Escalating before the first reminder makes no sense; keep it sane
        # rather than silently producing an escalation nobody was warned about.
        if esc < sla:
            esc = sla
        conn.execute(
            """UPDATE hr_escalation_rules SET sla_days = ?, escalate_after_days = ?,
               active = ?, updated_at = ? WHERE id = ?""",
            (sla, esc, 1 if request.form.get(f"active_{rule['id']}") else 0, now_iso, rule["id"]),
        )
    conn.commit()
    conn.close()
    flash("Chase-up thresholds updated.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/run-escalation", methods=["POST"])
@owner_required
def run_hr_escalation_now():
    conn = get_db()
    result = run_hr_escalation_job(conn)
    conn.close()
    flash(f"Chase-up run: {result}", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/certifications/new", methods=["POST"])
@owner_required
def new_certification():
    user_id = request.form.get("user_id", "")
    name = request.form.get("name", "").strip()
    if not user_id.isdigit() or not name:
        flash("Choose an employee and give the certification a name.", "error")
        return redirect(url_for("admin_hr"))
    conn = get_db()
    conn.execute(
        """INSERT INTO certifications (user_id, name, issuer, reference, issued_date,
           expiry_date, required, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(user_id), name, request.form.get("issuer", "").strip() or None,
         request.form.get("reference", "").strip() or None,
         request.form.get("issued_date", "").strip() or None,
         request.form.get("expiry_date", "").strip() or None,
         1 if request.form.get("required") else 0,
         request.form.get("notes", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "certification_added", target=name)
    conn.commit()
    conn.close()
    flash(f"{name} recorded.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/certifications/<int:cert_id>/delete", methods=["POST"])
@owner_required
def delete_certification(cert_id):
    conn = get_db()
    conn.execute("DELETE FROM certifications WHERE id = ?", (cert_id,))
    conn.commit()
    conn.close()
    flash("Certification removed.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/absences/new", methods=["POST"])
@owner_required
def new_absence():
    user_id = request.form.get("user_id", "")
    start = request.form.get("start_date", "").strip()
    end = request.form.get("end_date", "").strip() or start
    kind = request.form.get("kind", "sick")
    if not user_id.isdigit() or not start:
        flash("Choose an employee and a start date.", "error")
        return redirect(url_for("admin_hr"))
    if kind not in ("sick", "emergency", "unpaid", "unauthorised", "other"):
        kind = "other"
    if parse_date(end) and parse_date(start) and parse_date(end) < parse_date(start):
        flash("The end date can't be before the start date.", "error")
        return redirect(url_for("admin_hr"))
    conn = get_db()
    conn.execute(
        """INSERT INTO absences (user_id, start_date, end_date, kind, reason,
           self_certified, recorded_by_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(user_id), start, end, kind, request.form.get("reason", "").strip() or None,
         1 if request.form.get("self_certified") else 0,
         current_user()["id"], datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "absence_recorded", target=f"user {user_id} {start}")
    conn.commit()
    conn.close()
    flash("Absence recorded.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/absences/<int:absence_id>/return-to-work", methods=["POST"])
@owner_required
def absence_return_to_work(absence_id):
    note = request.form.get("return_to_work_note", "").strip()
    conn = get_db()
    conn.execute(
        """UPDATE absences SET return_to_work_note = ?, return_to_work_done_at = ?
           WHERE id = ?""",
        (note or None, datetime.now(timezone.utc).isoformat(), absence_id),
    )
    conn.commit()
    conn.close()
    flash("Return-to-work recorded.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/reviews/new", methods=["POST"])
@owner_required
def new_performance_review():
    user_id = request.form.get("user_id", "")
    review_date = request.form.get("review_date", "").strip()
    if not user_id.isdigit() or not review_date:
        flash("Choose an employee and a review date.", "error")
        return redirect(url_for("admin_hr"))
    rating = request.form.get("overall_rating", "").strip()
    conn = get_db()
    conn.execute(
        """INSERT INTO performance_reviews (user_id, reviewer_user_id, review_date,
           period_start, period_end, overall_rating, strengths, improvements, goals,
           status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
        (int(user_id), current_user()["id"], review_date,
         request.form.get("period_start", "").strip() or None,
         request.form.get("period_end", "").strip() or None,
         int(rating) if rating.isdigit() and 1 <= int(rating) <= 5 else None,
         request.form.get("strengths", "").strip() or None,
         request.form.get("improvements", "").strip() or None,
         request.form.get("goals", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "performance_review_created", target=f"user {user_id}")
    conn.commit()
    conn.close()
    flash("Review saved as a draft — share it when you're ready.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/admin/hr/reviews/<int:review_id>/share", methods=["POST"])
@owner_required
def share_performance_review(review_id):
    conn = get_db()
    review = conn.execute(
        "SELECT * FROM performance_reviews WHERE id = ?", (review_id,)
    ).fetchone()
    if not review:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE performance_reviews SET status = 'shared', shared_at = ? WHERE id = ? AND status = 'draft'",
        (datetime.now(timezone.utc).isoformat(), review_id),
    )
    send_notification(
        conn, review["user_id"], "performance_review",
        "Your performance review is ready",
        body="Your manager has shared a review with you. Please read it and confirm you've seen it.",
        link="/my-reviews",
    )
    conn.commit()
    conn.close()
    flash("Review shared with the employee.", "success")
    return redirect(url_for("admin_hr"))


@app.route("/my-reviews")
@login_required
def my_reviews():
    """An employee's own reviews. Drafts are deliberately excluded -- a review
    is not theirs to see until the manager has finished writing it."""
    user = current_user()
    conn = get_db()
    reviews = conn.execute(
        """SELECT performance_reviews.*, reviewer.name AS reviewer_name
           FROM performance_reviews
           LEFT JOIN users AS reviewer ON reviewer.id = performance_reviews.reviewer_user_id
           WHERE performance_reviews.user_id = ? AND performance_reviews.status != 'draft'
           ORDER BY performance_reviews.review_date DESC""",
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("my_reviews.html", reviews=reviews)


@app.route("/my-reviews/<int:review_id>/acknowledge", methods=["POST"])
@login_required
def acknowledge_review(review_id):
    user = current_user()
    comments = request.form.get("employee_comments", "").strip()
    conn = get_db()
    # Scoped to this user's own review, so nobody can acknowledge someone else's.
    cur = conn.execute(
        """UPDATE performance_reviews
           SET status = 'acknowledged', acknowledged_at = ?, employee_comments = ?
           WHERE id = ? AND user_id = ? AND status = 'shared'""",
        (datetime.now(timezone.utc).isoformat(), comments or None, review_id, user["id"]),
    )
    if cur.rowcount:
        owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        if owner_row:
            send_notification(
                conn, owner_row["id"], "performance_review",
                f"{user['name']} acknowledged their review",
                body=comments[:200] if comments else None, link="/admin/hr",
            )
    conn.commit()
    conn.close()
    flash("Thanks — your review has been acknowledged." if cur.rowcount
          else "That review isn't awaiting your acknowledgement.",
          "success" if cur.rowcount else "error")
    return redirect(url_for("my_reviews"))


@app.route("/availability", methods=["GET", "POST"])
@login_required
def my_availability():
    """Employees keep their own normal weekly availability, plus one-off
    exceptions. The owner sees clashes on the HR page when a shift is assigned
    against it."""
    user = current_user()
    conn = get_db()
    if request.method == "POST":
        now_iso = datetime.now(timezone.utc).isoformat()
        for weekday in range(7):
            available = 1 if request.form.get(f"available_{weekday}") else 0
            from_time = request.form.get(f"from_{weekday}", "").strip() or None
            to_time = request.form.get(f"to_{weekday}", "").strip() or None
            conn.execute(
                """INSERT INTO availability_rules (user_id, weekday, available, from_time, to_time, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, weekday) DO UPDATE SET
                     available = excluded.available, from_time = excluded.from_time,
                     to_time = excluded.to_time, updated_at = excluded.updated_at""",
                (user["id"], weekday, available, from_time, to_time, now_iso),
            )
        conn.commit()
        conn.close()
        flash("Your availability has been saved.", "success")
        return redirect(url_for("my_availability"))

    rules = {r["weekday"]: r for r in conn.execute(
        "SELECT * FROM availability_rules WHERE user_id = ?", (user["id"],)
    ).fetchall()}
    today = datetime.now(timezone.utc).date()
    exceptions = conn.execute(
        "SELECT * FROM availability_exceptions WHERE user_id = ? AND on_date >= ? ORDER BY on_date",
        (user["id"], today.isoformat()),
    ).fetchall()
    conn.close()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return render_template("my_availability.html", rules=rules, exceptions=exceptions,
                           weekdays=weekdays, today=today)


@app.route("/availability/exception", methods=["POST"])
@login_required
def add_availability_exception():
    user = current_user()
    on_date = request.form.get("on_date", "").strip()
    if not on_date:
        flash("Pick a date.", "error")
        return redirect(url_for("my_availability"))
    conn = get_db()
    conn.execute(
        """INSERT INTO availability_exceptions (user_id, on_date, available, note, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id, on_date) DO UPDATE SET
             available = excluded.available, note = excluded.note""",
        (user["id"], on_date, 1 if request.form.get("available") else 0,
         request.form.get("note", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Saved.", "success")
    return redirect(url_for("my_availability"))


@app.route("/availability/exception/<int:exception_id>/delete", methods=["POST"])
@login_required
def delete_availability_exception(exception_id):
    user = current_user()
    conn = get_db()
    conn.execute(
        "DELETE FROM availability_exceptions WHERE id = ? AND user_id = ?",
        (exception_id, user["id"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("my_availability"))


# ---------------------------------------------------------------------------
# "Ask HR" — a private channel from employee to owner. The opposite
# direction from check_in_notes (owner writes, hidden from the employee):
# here the employee writes, and it's visible only to them and the owner —
# never to other employees.
# ---------------------------------------------------------------------------

@app.route("/hr/ask", methods=["GET", "POST"])
@login_required
def ask_hr():
    user = current_user()
    conn = get_db()
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if not body:
            flash("Write something first.", "error")
        else:
            conn.execute(
                "INSERT INTO hr_notes (user_id, body, status, created_at) VALUES (?, ?, 'open', ?)",
                (user["id"], body, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
            if owner_row:
                send_notification(
                    conn, owner_row["id"], "hr_note", f"{user['name']} asked HR a question",
                    body=body[:200], link="/admin/hr-notes",
                )
            flash("Sent — only the owner can see this.", "success")
        conn.close()
        return redirect(url_for("ask_hr"))

    my_notes = conn.execute(
        "SELECT * FROM hr_notes WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
    ).fetchall()
    conn.close()
    return render_template("ask_hr.html", my_notes=my_notes)


@app.route("/admin/hr-notes")
@owner_required
def admin_hr_notes():
    conn = get_db()
    notes = conn.execute(
        """SELECT hr_notes.*, users.name AS employee_name FROM hr_notes
           JOIN users ON users.id = hr_notes.user_id
           ORDER BY (hr_notes.status = 'handled'), hr_notes.created_at DESC"""
    ).fetchall()
    conn.close()
    return render_template("admin_hr_notes.html", notes=notes)


@app.route("/admin/hr-notes/<int:note_id>/handle", methods=["POST"])
@owner_required
def handle_hr_note(note_id):
    conn = get_db()
    note = conn.execute("SELECT * FROM hr_notes WHERE id = ?", (note_id,)).fetchone()
    if not note:
        conn.close()
        abort(404)
    response = request.form.get("response", "").strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    if response:
        # Writing a reply always marks it handled — re-opening (below) is
        # still available separately for "marked this by mistake".
        conn.execute(
            "UPDATE hr_notes SET status = 'handled', handled_at = ?, response = ?, responded_at = ? WHERE id = ?",
            (now_iso, response, now_iso, note_id),
        )
        conn.commit()
        send_notification(
            conn, note["user_id"], "hr_note_reply", "The owner replied to your question",
            body=response[:200], link="/hr/ask",
        )
        flash("Reply sent.", "success")
    else:
        new_status = "open" if note["status"] == "handled" else "handled"
        conn.execute(
            "UPDATE hr_notes SET status = ?, handled_at = ? WHERE id = ?",
            (new_status, now_iso if new_status == "handled" else None, note_id),
        )
        conn.commit()
    conn.close()
    return redirect(url_for("admin_hr_notes"))


# ---------------------------------------------------------------------------
# Timesheets — built entirely from time_entries rows created by the
# Clock In/Clock Out actions (see clock_in_action()/clock_out_action()).
# Nothing here writes a time_entries row itself; this is a read-only report
# over that log.
# ---------------------------------------------------------------------------

def timesheet_query(conn, employee_id, start, end):
    sql = """SELECT time_entries.*, users.name AS user_name
             FROM time_entries JOIN users ON users.id = time_entries.user_id
             WHERE clock_in_at >= ? AND clock_in_at < ?"""
    params = [start.isoformat(), (end + timedelta(days=1)).isoformat()]
    if employee_id:
        sql += " AND user_id = ?"
        params.append(employee_id)
    sql += " ORDER BY clock_in_at DESC"
    return conn.execute(sql, params).fetchall()


@app.route("/admin/timesheets")
@owner_required
def admin_timesheets():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    employee_id = request.args.get("employee_id", "").strip()
    start = parse_date(request.args.get("start", "")) or (today - timedelta(days=13))
    end = parse_date(request.args.get("end", "")) or today

    entries = timesheet_query(conn, employee_id, start, end)
    # One query for all the breaks, not one per row. The template used to call
    # net_hours() per line, which opens its OWN connection when it isn't given
    # one — a fortnight of timesheets cost 260+ queries and a connection per
    # row just to render the hours column.
    hours_by_entry = net_hours_for_entries(conn, entries)
    totals_by_user = {}
    for e in entries:
        hrs = hours_by_entry.get(e["id"], 0.0)
        bucket = totals_by_user.setdefault(e["user_id"], {"name": e["user_name"], "hours": 0.0, "shifts": 0})
        bucket["hours"] = round(bucket["hours"] + hrs, 2)
        bucket["shifts"] += 1

    pay_ref_by_user = {
        r["id"]: r for r in conn.execute("SELECT id, pay_rate, pay_type FROM users").fetchall()
    }
    total_estimated_cost = 0.0
    any_estimate = False
    for user_id, bucket in totals_by_user.items():
        person = pay_ref_by_user.get(user_id)
        pay_rate = person["pay_rate"] if person else None
        pay_type = person["pay_type"] if person else None
        bucket["pay_rate"] = pay_rate
        bucket["pay_type"] = pay_type
        bucket["estimated_cost"] = estimated_hourly_cost(bucket["hours"], pay_rate, pay_type)
        if bucket["estimated_cost"] is not None:
            total_estimated_cost += bucket["estimated_cost"]
            any_estimate = True

    employees = conn.execute(
        "SELECT id, name FROM users WHERE role = 'employee' ORDER BY name"
    ).fetchall()
    pending_corrections_count = conn.execute(
        "SELECT COUNT(*) AS c FROM timesheet_corrections WHERE status = 'pending'"
    ).fetchone()["c"]
    conn.close()

    return render_template(
        "admin_timesheets.html", entries=entries, totals_by_user=totals_by_user,
        hours_by_entry=hours_by_entry,
        employees=employees, employee_id=employee_id, start=start, end=end, today=today,
        total_estimated_cost=round(total_estimated_cost, 2) if any_estimate else None,
        pending_corrections_count=pending_corrections_count,
    )


@app.route("/admin/timesheets/export.csv")
@owner_required
def export_timesheets_csv():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    employee_id = request.args.get("employee_id", "").strip()
    start = parse_date(request.args.get("start", "")) or (today - timedelta(days=13))
    end = parse_date(request.args.get("end", "")) or today
    entries = timesheet_query(conn, employee_id, start, end)
    # Computed while the connection is still open. This used to call net_hours()
    # with no connection AFTER conn.close(), so exporting a long range opened
    # one fresh database connection per row.
    hours_by_entry = net_hours_for_entries(conn, entries)
    conn.close()

    rows = [
        {
            "employee": e["user_name"],
            "clock_in_at": e["clock_in_at"],
            "clock_out_at": e["clock_out_at"] or "",
            "hours": hours_by_entry.get(e["id"], "") if e["clock_out_at"] else "",
            "auto_closed": "yes" if e["auto_closed"] else "",
        }
        for e in entries
    ]
    fieldnames = ["employee", "clock_in_at", "clock_out_at", "hours", "auto_closed"]
    return csv_response(fieldnames, rows, f"timesheets_{start.isoformat()}_to_{end.isoformat()}.csv")


@app.route("/admin/timesheets/summary.csv")
@owner_required
def export_timesheets_summary_csv():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    employee_id = request.args.get("employee_id", "").strip()
    start = parse_date(request.args.get("start", "")) or (today - timedelta(days=13))
    end = parse_date(request.args.get("end", "")) or today
    entries = timesheet_query(conn, employee_id, start, end)
    hours_by_entry = net_hours_for_entries(conn, entries)
    totals_by_user = {}
    for e in entries:
        hrs = hours_by_entry.get(e["id"], 0.0)
        bucket = totals_by_user.setdefault(e["user_id"], {"name": e["user_name"], "hours": 0.0, "shifts": 0})
        bucket["hours"] = round(bucket["hours"] + hrs, 2)
        bucket["shifts"] += 1
    pay_ref_by_user = {
        r["id"]: r for r in conn.execute("SELECT id, pay_rate, pay_type FROM users").fetchall()
    }
    conn.close()

    rows = []
    for user_id, bucket in totals_by_user.items():
        person = pay_ref_by_user.get(user_id)
        pay_rate = person["pay_rate"] if person else None
        pay_type = person["pay_type"] if person else None
        cost = estimated_hourly_cost(bucket["hours"], pay_rate, pay_type)
        rows.append({
            "employee": bucket["name"],
            "shifts": bucket["shifts"],
            "hours": bucket["hours"],
            "pay_rate_reference": pay_rate or "",
            "pay_type": pay_type or "",
            "estimated_cost": cost if cost is not None else "",
        })
    fieldnames = ["employee", "shifts", "hours", "pay_rate_reference", "pay_type", "estimated_cost"]
    return csv_response(fieldnames, rows, f"labour_summary_{start.isoformat()}_to_{end.isoformat()}.csv")


@app.route("/timesheets/<int:entry_id>/flag", methods=["POST"])
@login_required
def flag_timesheet_entry(entry_id):
    user = current_user()
    note = request.form.get("note", "").strip()
    conn = get_db()
    entry = conn.execute(
        "SELECT * FROM time_entries WHERE id = ? AND user_id = ?", (entry_id, user["id"])
    ).fetchone()
    if not entry:
        conn.close()
        abort(404)
    if not note:
        flash("Say what looks wrong first.", "error")
    else:
        conn.execute(
            "INSERT INTO timesheet_corrections (time_entry_id, user_id, note, status, created_at) VALUES (?,?,?,'pending',?)",
            (entry_id, user["id"], note, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        flash("Sent to the owner for review.", "success")
    conn.close()
    return redirect(url_for("profile", user_id=user["id"]))


@app.route("/admin/timesheets/corrections")
@owner_required
def admin_timesheet_corrections():
    conn = get_db()
    corrections = conn.execute(
        """SELECT timesheet_corrections.*, time_entries.clock_in_at, time_entries.clock_out_at,
               time_entries.auto_closed, users.name AS employee_name
           FROM timesheet_corrections
           JOIN time_entries ON time_entries.id = timesheet_corrections.time_entry_id
           JOIN users ON users.id = timesheet_corrections.user_id
           ORDER BY (timesheet_corrections.status != 'pending'), timesheet_corrections.created_at DESC"""
    ).fetchall()
    conn.close()
    return render_template("admin_timesheet_corrections.html", corrections=corrections)


# ---------------------------------------------------------------------------
# Incident & accident register, role compliance, access register, payroll pack.
# ---------------------------------------------------------------------------

@app.route("/admin/incidents")
@owner_required
def admin_incidents():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    status_filter = request.args.get("status", "open")
    query = """SELECT incidents.*, u.name AS affected_name, r.name AS reporter_name,
                      insurance_policies.provider AS insurer
               FROM incidents
               LEFT JOIN users AS u ON u.id = incidents.affected_user_id
               LEFT JOIN users AS r ON r.id = incidents.reported_by_user_id
               LEFT JOIN insurance_policies ON insurance_policies.id = incidents.insurance_policy_id"""
    params = []
    if status_filter in ("open", "actioned", "closed"):
        query += " WHERE incidents.status = ?"
        params.append(status_filter)
    query += " ORDER BY incidents.occurred_at DESC"
    incidents = conn.execute(query, params).fetchall()

    year_start = date(today.year, 1, 1).isoformat()
    stats = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(kind = 'workplace'), 0) AS workplace,
                  COALESCE(SUM(severity IN ('significant','serious')), 0) AS serious,
                  COALESCE(SUM(work_days_lost), 0) AS days_lost
           FROM incidents WHERE occurred_at >= ?""", (year_start,),
    ).fetchone()
    open_count = conn.execute(
        "SELECT COUNT(*) AS c FROM incidents WHERE status = 'open'").fetchone()["c"]
    employees = conn.execute(
        "SELECT id, name FROM users WHERE status = 'active' ORDER BY name").fetchall()
    policies = conn.execute(
        "SELECT id, provider, coverage_type FROM insurance_policies ORDER BY provider").fetchall()
    conn.close()
    overview = [
        overview_cell("Open", open_count, alert=open_count),
        overview_cell("This year", stats["total"]),
        overview_cell("Workplace", stats["workplace"], hint="staff accidents"),
        overview_cell("Significant or worse", stats["serious"], alert=stats["serious"]),
        overview_cell("Work days lost", int(stats["days_lost"] or 0)),
    ]
    return render_template("admin_incidents.html", incidents=incidents, overview=overview,
                           employees=employees, policies=policies,
                           status_filter=status_filter, today=today)


@app.route("/admin/incidents/new", methods=["POST"])
@owner_required
def new_incident():
    occurred = request.form.get("occurred_at", "").strip()
    summary = request.form.get("summary", "").strip()
    if not summary or not parse_date(occurred[:10]):
        flash("An incident needs a date and a short summary.", "error")
        return redirect(url_for("admin_incidents"))
    kind = request.form.get("kind", "workplace")
    severity = request.form.get("severity", "minor")
    if kind not in INCIDENT_KINDS or severity not in INCIDENT_SEVERITIES:
        abort(400)
    affected_raw = request.form.get("affected_user_id", "").strip()
    policy_raw = request.form.get("insurance_policy_id", "").strip()
    days_raw = request.form.get("work_days_lost", "").strip()
    conn = get_db()
    conn.execute(
        """INSERT INTO incidents (kind, occurred_at, location, summary, detail, severity,
           affected_user_id, affected_person, reported_by_user_id, witnesses,
           first_aid_given, medical_attention, work_days_lost, insurance_policy_id,
           action_taken, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (kind, occurred, request.form.get("location", "").strip() or None, summary,
         request.form.get("detail", "").strip() or None, severity,
         int(affected_raw) if affected_raw.isdigit() else None,
         request.form.get("affected_person", "").strip() or None,
         (current_user() or {})["id"],
         request.form.get("witnesses", "").strip() or None,
         1 if request.form.get("first_aid_given") else 0,
         1 if request.form.get("medical_attention") else 0,
         int(days_raw) if days_raw.isdigit() else None,
         int(policy_raw) if policy_raw.isdigit() else None,
         request.form.get("action_taken", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "incident_recorded", summary[:80], f"{kind}, {severity}, {occurred}")
    conn.commit()
    conn.close()
    flash("Incident recorded.", "success")
    return redirect(url_for("admin_incidents"))


@app.route("/admin/incidents/<int:incident_id>/update", methods=["POST"])
@owner_required
def update_incident(incident_id):
    status = request.form.get("status", "")
    if status not in ("open", "actioned", "closed"):
        abort(400)
    conn = get_db()
    action = request.form.get("action_taken", "").strip()
    reported = request.form.get("reported_to_insurer") == "1"
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE incidents SET status = ?,
             action_taken = COALESCE(NULLIF(?, ''), action_taken),
             closed_at = CASE WHEN ? = 'closed' THEN ? ELSE NULL END,
             reported_to_insurer_at = CASE WHEN ? THEN COALESCE(reported_to_insurer_at, ?)
                                           ELSE reported_to_insurer_at END
           WHERE id = ?""",
        (status, action, status, now_iso, 1 if reported else 0, now_iso, incident_id),
    )
    log_audit(conn, "incident_updated", f"incident #{incident_id}", f"status -> {status}")
    conn.commit()
    conn.close()
    flash("Incident updated.", "success")
    return redirect(url_for("admin_incidents"))


@app.route("/admin/compliance")
@owner_required
def admin_compliance():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    gaps = role_compliance(conn, today)
    requirements = conn.execute(
        "SELECT * FROM role_requirements ORDER BY job_role, requirement_type, requirement").fetchall()
    by_role = {}
    for r in requirements:
        by_role.setdefault(r["job_role"], []).append(r)
    known_roles = [r["job_role"] for r in conn.execute(
        """SELECT DISTINCT job_role FROM users
           WHERE role = 'employee' AND status = 'active'
             AND job_role IS NOT NULL AND TRIM(job_role) != '' ORDER BY job_role""").fetchall()]
    people_affected = len({g["user_id"] for g in gaps})
    conn.close()
    counts = {s: sum(1 for g in gaps if g["state"] == s) for s in ("missing", "expired", "expiring")}
    overview = [
        overview_cell("People not compliant", people_affected, alert=people_affected),
        overview_cell("Missing", counts["missing"], alert=counts["missing"]),
        overview_cell("Expired", counts["expired"], alert=counts["expired"]),
        overview_cell("Expiring soon", counts["expiring"], hint="within 30 days"),
        overview_cell("Rules defined", len(requirements)),
    ]
    return render_template("admin_compliance.html", gaps=gaps, by_role=by_role,
                           known_roles=known_roles, overview=overview)


@app.route("/admin/compliance/new", methods=["POST"])
@owner_required
def new_role_requirement():
    job_role = request.form.get("job_role", "").strip()
    requirement = request.form.get("requirement", "").strip()
    rtype = request.form.get("requirement_type", "certification")
    if not job_role or not requirement or rtype not in ("certification", "document"):
        flash("Choose a role and name what it requires.", "error")
        return redirect(url_for("admin_compliance"))
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO role_requirements (job_role, requirement, requirement_type, notes, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (job_role, requirement, rtype, request.form.get("notes", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash(f"{job_role} now requires {requirement}.", "success")
    return redirect(url_for("admin_compliance"))


@app.route("/admin/compliance/<int:req_id>/delete", methods=["POST"])
@owner_required
def delete_role_requirement(req_id):
    conn = get_db()
    conn.execute("DELETE FROM role_requirements WHERE id = ?", (req_id,))
    conn.commit()
    conn.close()
    flash("Requirement removed.", "success")
    return redirect(url_for("admin_compliance"))


@app.route("/admin/access")
@owner_required
def admin_access():
    conn = get_db()
    items = conn.execute(
        """SELECT access_items.*,
                  (SELECT COUNT(*) FROM access_holdings
                   WHERE access_holdings.access_item_id = access_items.id
                     AND access_holdings.returned_at IS NULL) AS out_count
           FROM access_items WHERE active = 1 ORDER BY kind, label"""
    ).fetchall()
    holdings = conn.execute(
        """SELECT access_holdings.*, access_items.label, access_items.kind,
                  users.name AS holder, users.status AS holder_status
           FROM access_holdings
           JOIN access_items ON access_items.id = access_holdings.access_item_id
           LEFT JOIN users ON users.id = access_holdings.user_id
           WHERE access_holdings.returned_at IS NULL
           ORDER BY users.name, access_items.label"""
    ).fetchall()
    employees = conn.execute(
        "SELECT id, name FROM users WHERE status = 'active' ORDER BY name").fetchall()
    # The reason this register exists: someone who has left still holding a key.
    leavers_holding = [h for h in holdings if h["holder_status"] == "inactive"]
    conn.close()
    overview = [
        overview_cell("Items on the register", len(items)),
        overview_cell("Currently issued", len(holdings)),
        overview_cell("Held by leavers", len(leavers_holding), alert=len(leavers_holding),
                      hint="not returned"),
    ]
    return render_template("admin_access.html", items=items, holdings=holdings,
                           employees=employees, overview=overview,
                           leavers_holding=leavers_holding)


@app.route("/admin/access/items/new", methods=["POST"])
@owner_required
def new_access_item():
    label = request.form.get("label", "").strip()
    kind = request.form.get("kind", "key")
    if not label or kind not in ACCESS_KINDS:
        flash("Give the item a name.", "error")
        return redirect(url_for("admin_access"))
    conn = get_db()
    conn.execute(
        """INSERT INTO access_items (label, kind, location, notes, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (label, kind, request.form.get("location", "").strip() or None,
         request.form.get("notes", "").strip() or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash(f"{label} added to the register.", "success")
    return redirect(url_for("admin_access"))


@app.route("/admin/access/issue", methods=["POST"])
@owner_required
def issue_access_item():
    item_raw = request.form.get("access_item_id", "").strip()
    user_raw = request.form.get("user_id", "").strip()
    if not item_raw.isdigit() or not user_raw.isdigit():
        flash("Choose an item and who it goes to.", "error")
        return redirect(url_for("admin_access"))
    conn = get_db()
    already = conn.execute(
        """SELECT 1 FROM access_holdings WHERE access_item_id = ? AND user_id = ?
           AND returned_at IS NULL""", (int(item_raw), int(user_raw))).fetchone()
    if already:
        conn.close()
        flash("They already hold that one.", "error")
        return redirect(url_for("admin_access"))
    conn.execute(
        """INSERT INTO access_holdings (access_item_id, user_id, issued_at, issued_by_user_id, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (int(item_raw), int(user_raw), datetime.now(timezone.utc).isoformat(),
         (current_user() or {})["id"], request.form.get("notes", "").strip() or None),
    )
    log_audit(conn, "access_issued", f"item #{item_raw}", f"to user #{user_raw}")
    conn.commit()
    conn.close()
    flash("Issued.", "success")
    return redirect(url_for("admin_access"))


@app.route("/admin/access/<int:holding_id>/return", methods=["POST"])
@owner_required
def return_access_item(holding_id):
    conn = get_db()
    conn.execute("UPDATE access_holdings SET returned_at = ? WHERE id = ? AND returned_at IS NULL",
                 (datetime.now(timezone.utc).isoformat(), holding_id))
    log_audit(conn, "access_returned", f"holding #{holding_id}", None)
    conn.commit()
    conn.close()
    flash("Marked as returned.", "success")
    return redirect(url_for("admin_access"))


@app.route("/admin/payroll")
@owner_required
def admin_payroll():
    conn = get_db()
    period = period_from_request()
    rows = payroll_period_rows(conn, period)
    conn.close()
    blocked = [r for r in rows if r["blockers"]]
    total_hours = round(sum(r["hours"] for r in rows), 2)
    total_cost = round(sum(r["cost"] or 0 for r in rows), 2)
    overview = [
        overview_cell("People", len([r for r in rows if r["hours"] > 0])),
        overview_cell("Hours", total_hours, sub="h", hint="net of breaks"),
        overview_cell("Estimated pay", euro(total_cost)),
        overview_cell("Needs fixing first", len(blocked), alert=len(blocked)),
    ]
    return render_template("admin_payroll.html", rows=rows, period=period,
                           overview=overview, blocked=blocked)


@app.route("/admin/payroll/export.csv")
@owner_required
def export_payroll_csv():
    conn = get_db()
    period = period_from_request()
    rows = payroll_period_rows(conn, period)
    conn.close()
    blocked = [r for r in rows if r["blockers"]]
    if blocked:
        # Refusing is the point. A payroll file that silently omits an
        # impossible shift or prices someone at zero is worse than no file,
        # because the accountant has no way to know it was wrong.
        names = ", ".join(r["name"] for r in blocked[:4])
        flash(f"Fix these before exporting: {names}"
              f"{' and others' if len(blocked) > 4 else ''}.", "error")
        return redirect(url_for("admin_payroll", period=period["period"], date=period["anchor_iso"]))
    fieldnames = ["name", "job_role", "contract_type", "hours", "shifts",
                  "absence_days", "leave_days", "pay_rate", "estimated_cost"]
    payload = [{**{k: r[k] for k in fieldnames if k in r},
                "estimated_cost": r["cost"]} for r in rows]
    return csv_response(fieldnames, payload, f"payroll-{period['start_iso']}.csv")


@app.route("/admin/timesheets/<int:entry_id>/repair", methods=["POST"])
@owner_required
def repair_time_entry(entry_id):
    """Owner-side fix for a shift that ends before it starts.

    Every other path into `time_entries` requires the EMPLOYEE to raise a
    correction first, so an impossible entry could be reported on the Team
    overview and then not actually be fixable by the person being told about
    it — the alert pointed at a page with no way to act. This is the missing
    end of that loop: set the real clock-out, or void the entry outright when
    nobody can remember what happened.

    Voiding sets clock_out_at = clock_in_at rather than deleting the row: a
    zero-hour shift keeps the fact that someone clocked in (and the audit
    trail of the repair) while contributing nothing to any total. Payroll
    history is never silently destroyed.
    """
    conn = get_db()
    entry = conn.execute("SELECT * FROM time_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        abort(404)

    action = request.form.get("action", "")
    clock_in = parse_datetime_iso(entry["clock_in_at"])
    if action == "void":
        new_out = entry["clock_in_at"]
        note = "voided (zero hours)"
    else:
        raw = request.form.get("clock_out_at", "").strip()
        try:
            new_out = local_datetime_input_to_utc_iso(raw)
        except (ValueError, TypeError):
            conn.close()
            flash("Enter a valid clock-out date and time.", "error")
            return redirect(url_for("admin_timesheets"))
        if parse_datetime_iso(new_out) <= clock_in:
            conn.close()
            flash("The clock-out has to be after the clock-in.", "error")
            return redirect(url_for("admin_timesheets"))
        note = f"clock-out set to {local_datetime_str(new_out)}"

    conn.execute(
        "UPDATE time_entries SET clock_out_at = ?, auto_closed = 0 WHERE id = ?",
        (new_out, entry_id),
    )
    person = conn.execute("SELECT name FROM users WHERE id = ?", (entry["user_id"],)).fetchone()
    log_audit(conn, "timesheet_repaired", f"time_entry #{entry_id}",
              f"{person['name'] if person else 'Unknown'}: was {entry['clock_in_at']} → "
              f"{entry['clock_out_at']}; {note}")
    conn.commit()
    conn.close()
    flash("Timesheet entry corrected.", "success")
    return redirect(url_for("admin_timesheets"))


@app.route("/admin/timesheets/corrections/<int:correction_id>/resolve", methods=["POST"])
@owner_required
def resolve_timesheet_correction(correction_id):
    conn = get_db()
    correction = conn.execute(
        "SELECT * FROM timesheet_corrections WHERE id = ? AND status = 'pending'", (correction_id,)
    ).fetchone()
    if not correction:
        conn.close()
        abort(404)
    entry = conn.execute(
        "SELECT * FROM time_entries WHERE id = ?", (correction["time_entry_id"],)
    ).fetchone()
    clock_in_time = request.form.get("clock_in_time", "").strip()
    clock_out_time = request.form.get("clock_out_time", "").strip()

    # Work the new pair out in full before writing either, so a correction can
    # never leave the entry with a clock-out before its clock-in — that used to
    # be accepted silently and then showed up as NEGATIVE hours in every total
    # that summed this table.
    new_in = (local_time_input_to_utc_iso(entry["clock_in_at"], clock_in_time)
              if clock_in_time else entry["clock_in_at"])
    new_out = entry["clock_out_at"]
    if clock_out_time:
        new_out = local_time_input_to_utc_iso(entry["clock_out_at"] or entry["clock_in_at"], clock_out_time)
        # A clock-out time-of-day earlier than the clock-in almost always means
        # a shift that ran past midnight (22:00 → 02:00), not a mistake, so roll
        # it to the next day rather than refusing an ordinary night shift.
        if parse_datetime_iso(new_out) < parse_datetime_iso(new_in):
            new_out = (parse_datetime_iso(new_out) + timedelta(days=1)).isoformat()

    if new_out and parse_datetime_iso(new_out) < parse_datetime_iso(new_in):
        conn.close()
        flash("That would end the shift before it started — check the times.", "error")
        return redirect(url_for("admin_timesheet_corrections"))

    if clock_in_time:
        conn.execute("UPDATE time_entries SET clock_in_at = ? WHERE id = ?", (new_in, entry["id"]))
    if clock_out_time:
        conn.execute(
            "UPDATE time_entries SET clock_out_at = ?, auto_closed = 0 WHERE id = ?",
            (new_out, entry["id"]),
        )
    conn.execute(
        "UPDATE timesheet_corrections SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), correction_id),
    )
    conn.commit()
    conn.close()
    flash("Correction applied.", "success")
    return redirect(url_for("admin_timesheet_corrections"))


@app.route("/admin/timesheets/corrections/<int:correction_id>/dismiss", methods=["POST"])
@owner_required
def dismiss_timesheet_correction(correction_id):
    conn = get_db()
    conn.execute(
        "UPDATE timesheet_corrections SET status = 'dismissed', resolved_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc).isoformat(), correction_id),
    )
    conn.commit()
    conn.close()
    flash("Dismissed.", "success")
    return redirect(url_for("admin_timesheet_corrections"))


# ---------------------------------------------------------------------------
# Employee self-service onboarding (public — gated by a random invite token)
# ---------------------------------------------------------------------------

@app.route("/onboard/<token>", methods=["GET", "POST"])
def onboard(token):
    conn = get_db()
    person = conn.execute(
        "SELECT * FROM users WHERE invite_token = ? AND account_claimed = 0", (token,)
    ).fetchone()
    if not person:
        conn.close()
        abort(404)

    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            conn.close()
            return render_template("onboard.html", person=person)
        if password != confirm:
            flash("Passwords don't match.", "error")
            conn.close()
            return render_template("onboard.html", person=person)

        conn.execute(
            """UPDATE users SET password_hash = ?, phone = ?, account_claimed = 1,
               invite_token = NULL WHERE id = ?""",
            (generate_password_hash(password), phone or person["phone"], person["id"]),
        )
        conn.commit()
        session.permanent = True
        session["user_id"] = person["id"]
        conn.close()
        flash("Your account is set up. Welcome to the team.", "success")
        return redirect(url_for("dashboard"))

    conn.close()
    return render_template("onboard.html", person=person)


# ---------------------------------------------------------------------------
# Real document uploads
# ---------------------------------------------------------------------------

@app.route("/directory/<int:user_id>/upload", methods=["POST"])
@login_required
def upload_document(user_id):
    user = current_user()
    if user["role"] != "owner" and user["id"] != user_id:
        abort(403)

    file = request.files.get("document")
    title = request.form.get("title", "").strip()
    expiry_date = request.form.get("expiry_date", "").strip()

    if not file or file.filename == "":
        flash("Choose a file first.", "error")
        return redirect(url_for("profile", user_id=user_id))
    if not allowed_file(file.filename):
        flash(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}", "error")
        return redirect(url_for("profile", user_id=user_id))

    safe_name = secure_filename(file.filename)
    stored_name = f"{user_id}_{secrets.token_hex(6)}_{safe_name}"
    file.save(os.path.join(UPLOAD_DIR, stored_name))

    conn = get_db()
    conn.execute(
        "INSERT INTO documents (user_id, title, filename, uploaded_at, expiry_date) VALUES (?, ?, ?, ?, ?)",
        (user_id, title or safe_name, stored_name, datetime.now(timezone.utc).isoformat(), expiry_date or None),
    )
    conn.commit()
    conn.close()
    flash("Document uploaded.", "success")
    return redirect(url_for("profile", user_id=user_id))


@app.route("/documents/<int:doc_id>/download")
@login_required
def download_document(doc_id):
    user = current_user()
    conn = get_db()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not doc:
        abort(404)
    if user["role"] != "owner" and user["id"] != doc["user_id"]:
        abort(403)
    return send_from_directory(UPLOAD_DIR, doc["filename"], as_attachment=True,
                                download_name=doc["title"])


@app.route("/documents/<int:doc_id>/view")
@login_required
def view_document(doc_id):
    user = current_user()
    conn = get_db()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not doc:
        abort(404)
    if user["role"] != "owner" and user["id"] != doc["user_id"]:
        abort(403)
    ext = doc["filename"].rsplit(".", 1)[-1].lower() if "." in doc["filename"] else ""
    if ext not in VIEWABLE_EXTENSIONS:
        abort(404)
    return send_from_directory(UPLOAD_DIR, doc["filename"], as_attachment=False)


@app.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_document(doc_id):
    user = current_user()
    conn = get_db()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        conn.close()
        abort(404)
    if user["role"] != "owner" and user["id"] != doc["user_id"]:
        conn.close()
        abort(403)
    path = os.path.join(UPLOAD_DIR, doc["filename"])
    if os.path.exists(path):
        os.remove(path)
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    flash("Document deleted.", "success")
    return redirect(url_for("profile", user_id=doc["user_id"]))


@app.route("/documents/<int:doc_id>/edit", methods=["POST"])
@login_required
def edit_document(doc_id):
    user = current_user()
    conn = get_db()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        conn.close()
        abort(404)
    if user["role"] != "owner" and user["id"] != doc["user_id"]:
        conn.close()
        abort(403)
    title = request.form.get("title", "").strip()
    expiry_date = request.form.get("expiry_date", "").strip()
    if not title:
        conn.close()
        flash("A title is required.", "error")
        return redirect(url_for("profile", user_id=doc["user_id"]))
    conn.execute(
        "UPDATE documents SET title = ?, expiry_date = ? WHERE id = ?",
        (title, expiry_date or None, doc_id),
    )
    conn.commit()
    conn.close()
    flash("Document updated.", "success")
    return redirect(url_for("profile", user_id=doc["user_id"]))


# ---------------------------------------------------------------------------
# Employee manual
# ---------------------------------------------------------------------------

def touch_manual_updated(conn):
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('manual_last_updated', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (datetime.now(timezone.utc).isoformat(),),
    )


def manual_last_updated(conn):
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'manual_last_updated'").fetchone()
    return row["value"] if row else None


@app.route("/manual")
@login_required
def manual():
    user = current_user()
    conn = get_db()
    sections = conn.execute(
        "SELECT * FROM manual_sections ORDER BY sort_order, id"
    ).fetchall()
    last_updated = manual_last_updated(conn)
    my_ack = None
    compliance = []
    if user["role"] == "owner":
        rows = conn.execute(
            """SELECT users.id, users.name, manual_acknowledgments.acknowledged_at
               FROM users LEFT JOIN manual_acknowledgments ON manual_acknowledgments.user_id = users.id
               WHERE users.role = 'employee' AND users.status = 'active' ORDER BY users.name"""
        ).fetchall()
        for r in rows:
            current = bool(r["acknowledged_at"]) and (not last_updated or r["acknowledged_at"] >= last_updated)
            compliance.append({"name": r["name"], "acknowledged_at": r["acknowledged_at"], "current": current})
    else:
        row = conn.execute(
            "SELECT acknowledged_at FROM manual_acknowledgments WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if row:
            my_ack = {
                "acknowledged_at": row["acknowledged_at"],
                "current": not last_updated or row["acknowledged_at"] >= last_updated,
            }
    conn.close()
    return render_template(
        "manual.html", sections=sections, last_updated=last_updated, my_ack=my_ack, compliance=compliance,
    )


@app.route("/manual/acknowledge", methods=["POST"])
@login_required
def acknowledge_manual():
    user = current_user()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO manual_acknowledgments (user_id, acknowledged_at) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET acknowledged_at = excluded.acknowledged_at",
        (user["id"], now),
    )
    conn.commit()
    conn.close()
    flash("Thanks — marked as read.", "success")
    return redirect(url_for("manual"))


@app.route("/manual/<int:section_id>/edit", methods=["POST"])
@owner_required
def edit_manual_section(section_id):
    body = request.form.get("body", "")
    conn = get_db()
    conn.execute("UPDATE manual_sections SET body = ? WHERE id = ?", (body, section_id))
    touch_manual_updated(conn)
    conn.commit()
    conn.close()
    return redirect(url_for("manual"))


@app.route("/manual/new", methods=["POST"])
@owner_required
def new_manual_section():
    title = request.form.get("title", "").strip()
    if title:
        conn = get_db()
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM manual_sections"
        ).fetchone()["m"]
        conn.execute(
            "INSERT INTO manual_sections (title, body, sort_order) VALUES (?, '', ?)",
            (title, max_order + 1),
        )
        touch_manual_updated(conn)
        conn.commit()
        conn.close()
    return redirect(url_for("manual"))


@app.route("/manual/<int:section_id>/delete", methods=["POST"])
@owner_required
def delete_manual_section(section_id):
    conn = get_db()
    conn.execute("DELETE FROM manual_sections WHERE id = ?", (section_id,))
    touch_manual_updated(conn)
    conn.commit()
    conn.close()
    return redirect(url_for("manual"))


# ---------------------------------------------------------------------------
# Contacts — a shared reference list (plumber, electrician, insurance broker,
# emergency services...), same visibility split as the Manual: everyone can
# read it, only the owner edits it.
# ---------------------------------------------------------------------------

@app.route("/contacts")
@login_required
def contacts():
    conn = get_db()
    people = conn.execute("SELECT * FROM contacts ORDER BY sort_order, name").fetchall()
    conn.close()
    return render_template("contacts.html", people=people)


@app.route("/contacts/export.csv")
@login_required
def export_contacts_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM contacts ORDER BY sort_order, name").fetchall()
    conn.close()
    fieldnames = ["name", "role", "phone", "notes"]
    return csv_response(fieldnames, rows, "contacts.csv")


@app.route("/contacts/new", methods=["POST"])
@owner_required
def new_contact():
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip()
    phone = request.form.get("phone", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("contacts"))
    conn = get_db()
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM contacts").fetchone()["m"]
    conn.execute(
        "INSERT INTO contacts (name, role, phone, notes, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, role, phone, notes, max_order + 1, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:contact_id>/edit", methods=["POST"])
@owner_required
def edit_contact(contact_id):
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip()
    phone = request.form.get("phone", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("contacts"))
    conn = get_db()
    conn.execute(
        "UPDATE contacts SET name = ?, role = ?, phone = ?, notes = ? WHERE id = ?",
        (name, role, phone, notes, contact_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@owner_required
def delete_contact(contact_id):
    conn = get_db()
    conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("contacts"))


# ---------------------------------------------------------------------------
# Announcements — owner posts a notice, everyone sees it on the dashboard
# until it expires (or is removed).
# ---------------------------------------------------------------------------

def current_announcements(conn, today):
    return conn.execute(
        "SELECT announcements.*, users.name AS posted_by_name FROM announcements "
        "LEFT JOIN users ON users.id = announcements.posted_by_user_id "
        "WHERE expires_date IS NULL OR expires_date >= ? ORDER BY created_at DESC",
        (today.isoformat(),),
    ).fetchall()


@app.route("/announcements")
@login_required
def announcements():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    current = current_announcements(conn, today)
    past = []
    if current_user()["role"] == "owner":
        past = conn.execute(
            "SELECT announcements.*, users.name AS posted_by_name FROM announcements "
            "LEFT JOIN users ON users.id = announcements.posted_by_user_id "
            "WHERE expires_date IS NOT NULL AND expires_date < ? ORDER BY created_at DESC",
            (today.isoformat(),),
        ).fetchall()
    conn.close()
    return render_template("announcements.html", current=current, past=past)


@app.route("/announcements/new", methods=["POST"])
@owner_required
def new_announcement():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    expires_date = request.form.get("expires_date", "").strip() or None
    if not title:
        flash("A title is required.", "error")
        return redirect(url_for("announcements"))
    conn = get_db()
    conn.execute(
        "INSERT INTO announcements (posted_by_user_id, title, body, expires_date, created_at) VALUES (?,?,?,?,?)",
        (current_user()["id"], title, body, expires_date, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    for employee in conn.execute("SELECT id FROM users WHERE role = 'employee' AND status = 'active'").fetchall():
        send_notification(conn, employee["id"], "announcement", title, body=body[:200] or None, link="/announcements")
    conn.close()
    flash("Announcement posted.", "success")
    return redirect(url_for("announcements"))


@app.route("/announcements/<int:announcement_id>/edit", methods=["POST"])
@owner_required
def edit_announcement(announcement_id):
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    expires_date = request.form.get("expires_date", "").strip() or None
    if not title:
        flash("A title is required.", "error")
        return redirect(url_for("announcements"))
    conn = get_db()
    conn.execute(
        "UPDATE announcements SET title = ?, body = ?, expires_date = ? WHERE id = ?",
        (title, body, expires_date, announcement_id),
    )
    conn.commit()
    conn.close()
    flash("Announcement updated.", "success")
    return redirect(url_for("announcements"))


@app.route("/announcements/<int:announcement_id>/delete", methods=["POST"])
@owner_required
def delete_announcement(announcement_id):
    conn = get_db()
    conn.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("announcements"))


# ---------------------------------------------------------------------------
# Shopping list — a shared, everyone-can-edit list (unlike Contacts, which is
# owner-write/everyone-read): whoever notices something's needed adds it,
# whoever's out shopping checks it off. Grouped by category for browsing.
# ---------------------------------------------------------------------------

@app.route("/shopping")
@login_required
def shopping_list():
    conn = get_db()
    items = conn.execute(
        "SELECT * FROM shopping_items ORDER BY bought, COALESCE(category, 'zzz'), name"
    ).fetchall()
    conn.close()
    by_category = {}
    for i in items:
        by_category.setdefault(i["category"] or "Uncategorized", []).append(i)
    return render_template("shopping_list.html", by_category=by_category, item_count=len(items))


@app.route("/shopping/new", methods=["POST"])
@login_required
def new_shopping_item():
    user = current_user()
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    store = request.form.get("store", "").strip()
    if name:
        conn = get_db()
        conn.execute(
            """INSERT INTO shopping_items (name, category, store, added_by_user_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (name, category or None, store or None, user["id"], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    return redirect(request.referrer or url_for("shopping_list"))


@app.route("/shopping/<int:item_id>/toggle", methods=["POST"])
@login_required
def toggle_shopping_item(item_id):
    conn = get_db()
    item = conn.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify(error="not found"), 404
    new_bought = 0 if item["bought"] else 1
    conn.execute(
        "UPDATE shopping_items SET bought = ?, bought_at = ? WHERE id = ?",
        (new_bought, datetime.now(timezone.utc).isoformat() if new_bought else None, item_id),
    )
    conn.commit()
    conn.close()
    return jsonify(bought=bool(new_bought))


@app.route("/shopping/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_shopping_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("shopping_list"))


@app.route("/shopping/clear-bought", methods=["POST"])
@login_required
def clear_bought_items():
    conn = get_db()
    conn.execute("DELETE FROM shopping_items WHERE bought = 1")
    conn.commit()
    conn.close()
    flash("Bought items cleared.", "success")
    return redirect(url_for("shopping_list"))


@app.route("/breakfast")
@login_required
def breakfast():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    items = conn.execute(
        "SELECT * FROM breakfast_items ORDER BY COALESCE(category, 'zzz'), name"
    ).fetchall()
    checked_today = {
        row["item_id"] for row in conn.execute(
            "SELECT item_id FROM breakfast_checklist_log WHERE checklist_date = ?", (today.isoformat(),)
        ).fetchall()
    }
    guests_here = guests_in_residence(conn, today)
    # `or 1` so a booking with no party size still counts as one person here --
    # this used to be `or 0`, silently under-reporting the breakfast headcount.
    guest_count = sum(g["party_size"] or 1 for g in guests_here)
    guest_notes = [g for g in guests_here if g["notes"]]
    occupied_today = occupied_rooms_by_date(conn, today, today + timedelta(days=1)).get(today.isoformat(), 0)
    conn.close()
    by_category = {}
    for i in items:
        by_category.setdefault(i["category"] or "Other", []).append(i)
    return render_template(
        "breakfast.html", by_category=by_category, checked_today=checked_today,
        guest_count=guest_count, occupied_today=occupied_today, item_count=len(items), guest_notes=guest_notes,
    )


@app.route("/breakfast/<int:item_id>/toggle", methods=["POST"])
@login_required
def toggle_breakfast_item(item_id):
    user = current_user()
    conn = get_db()
    item = conn.execute("SELECT * FROM breakfast_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        return jsonify(error="not found"), 404
    today = datetime.now(timezone.utc).date().isoformat()
    existing = conn.execute(
        "SELECT id FROM breakfast_checklist_log WHERE item_id = ? AND checklist_date = ?",
        (item_id, today),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM breakfast_checklist_log WHERE id = ?", (existing["id"],))
        checked = False
    else:
        conn.execute(
            """INSERT INTO breakfast_checklist_log (item_id, checklist_date, checked_by_user_id, checked_at)
               VALUES (?, ?, ?, ?)""",
            (item_id, today, user["id"], datetime.now(timezone.utc).isoformat()),
        )
        checked = True
    conn.commit()
    conn.close()
    return jsonify(checked=checked)


@app.route("/breakfast/items/new", methods=["POST"])
@owner_required
def new_breakfast_item():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("breakfast"))
    conn = get_db()
    conn.execute(
        "INSERT INTO breakfast_items (name, category, created_at) VALUES (?, ?, ?)",
        (name, category or None, datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "breakfast_item_created", target=name)
    conn.commit()
    conn.close()
    flash("Item added.", "success")
    return redirect(url_for("breakfast"))


@app.route("/breakfast/items/<int:item_id>/edit", methods=["POST"])
@owner_required
def edit_breakfast_item(item_id):
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("breakfast"))
    conn = get_db()
    conn.execute(
        "UPDATE breakfast_items SET name = ?, category = ? WHERE id = ?",
        (name, category or None, item_id),
    )
    log_audit(conn, "breakfast_item_edited", target=name)
    conn.commit()
    conn.close()
    return redirect(url_for("breakfast"))


@app.route("/breakfast/items/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_breakfast_item(item_id):
    conn = get_db()
    item = conn.execute("SELECT * FROM breakfast_items WHERE id = ?", (item_id,)).fetchone()
    conn.execute("DELETE FROM breakfast_items WHERE id = ?", (item_id,))
    if item:
        log_audit(conn, "breakfast_item_deleted", target=item["name"])
    conn.commit()
    conn.close()
    flash("Item removed.", "success")
    return redirect(url_for("breakfast"))


@app.route("/breakfast/items/<int:item_id>/toggle-stock", methods=["POST"])
@login_required
def toggle_breakfast_stock(item_id):
    conn = get_db()
    item = conn.execute("SELECT * FROM breakfast_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)
    new_state = 0 if item["low_stock"] else 1
    conn.execute("UPDATE breakfast_items SET low_stock = ? WHERE id = ?", (new_state, item_id))
    conn.commit()
    conn.close()
    return redirect(url_for("breakfast"))


# ---------------------------------------------------------------------------
# Notifications — in-app center + browser push. The in-app table is the
# source of truth (what the badge count and /notifications read from);
# push is a best-effort layer on top so someone finds out even if the app
# isn't open in front of them.
# ---------------------------------------------------------------------------

@app.route("/notifications")
@login_required
def notifications_page():
    user = current_user()
    conn = get_db()
    items = conn.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 100", (user["id"],)
    ).fetchall()
    conn.execute(
        "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), user["id"]),
    )
    conn.commit()
    conn.close()
    return render_template("notifications.html", items=items)


@app.route("/notifications/unread-count")
@login_required
def notifications_unread_count():
    user = current_user()
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read_at IS NULL", (user["id"],)
    ).fetchone()["c"]
    conn.close()
    return jsonify(count=count)


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def read_notification(notification_id):
    user = current_user()
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ? AND read_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), notification_id, user["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.route("/notifications/subscribe", methods=["POST"])
@login_required
def subscribe_push():
    user = current_user()
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "")
    keys = data.get("keys", {})
    p256dh, auth = keys.get("p256dh", ""), keys.get("auth", "")
    if not endpoint or not p256dh or not auth:
        return jsonify(error="invalid subscription"), 400
    conn = get_db()
    conn.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth""",
        (user["id"], endpoint, p256dh, auth, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify(ok=True)


@app.route("/notifications/unsubscribe", methods=["POST"])
@login_required
def unsubscribe_push():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint", "")
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Terms & Conditions — public page, owner-editable. Ships with a draft that
# matches what this app actually does (see module docstring); the bracketed
# cancellation-window policy is a real business decision only the owner (or
# their lawyer) can set, so it's left as a placeholder rather than guessed.
# ---------------------------------------------------------------------------

@app.route("/terms")
def terms_page():
    conn = get_db()
    text = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()["value"]
    conn.close()
    return render_template("terms.html", text=text)


@app.route("/admin/terms", methods=["GET", "POST"])
@owner_required
def admin_terms():
    conn = get_db()
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        conn.execute("UPDATE app_settings SET value = ? WHERE key = 'terms_and_conditions'", (text,))
        conn.commit()
        conn.close()
        flash("Terms & Conditions updated.", "success")
        return redirect(url_for("admin_terms"))
    text = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()["value"]
    conn.close()
    return render_template("admin_terms.html", text=text)


# ---------------------------------------------------------------------------
# Guest information (everyone can view, owner keeps it up to date)
# ---------------------------------------------------------------------------

@app.route("/guests")
@login_required
def guests():
    """Two distinct things on one page: who is physically here right now (derived
    from bookings) and the standing guest profiles (this table). They used to be
    the same list, which is what let a cancelled booking leave a phantom guest."""
    q = request.args.get("q", "").strip()
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    period = period_from_request()
    # Owner only: the band carries revenue and occupancy, which is not a
    # colleague's business. Employees still get the who-is-here lists below.
    is_owner = (current_user() or {})["role"] == "owner"
    overview = guests_overview(conn, period, today) if is_owner else None

    stays = stays_with_status(conn, today)
    in_residence = [s for s in stays if s["stay_status"] == "current"]
    upcoming = [s for s in stays if s["stay_status"] == "upcoming"]

    if q:
        needle = f"%{q}%"
        profiles = conn.execute(
            """SELECT * FROM guests
               WHERE name LIKE ? OR email LIKE ? OR phone LIKE ?
                  OR notes LIKE ? OR preferences LIKE ? OR dietary_notes LIKE ?
               ORDER BY vip DESC, name""",
            (needle,) * 6,
        ).fetchall()
        lowered = q.lower()
        in_residence = [s for s in in_residence if lowered in (s["name"] or "").lower()]
        upcoming = [s for s in upcoming if lowered in (s["name"] or "").lower()]
    else:
        profiles = conn.execute("SELECT * FROM guests ORDER BY vip DESC, name").fetchall()

    # How many past stays each profile has, so the list conveys "returning guest"
    # at a glance rather than needing a click-through.
    stay_counts = {
        r["linked_guest_id"]: r["c"] for r in conn.execute(
            """SELECT linked_guest_id, COUNT(*) AS c FROM bookings
               WHERE linked_guest_id IS NOT NULL AND status = 'confirmed'
               GROUP BY linked_guest_id"""
        ).fetchall()
    }
    conn.close()
    return render_template(
        "guests.html", in_residence=in_residence, upcoming=upcoming,
        profiles=profiles, stay_counts=stay_counts, q=q,
        overview=overview, period=period,
    )


@app.route("/guests/new", methods=["GET", "POST"])
@owner_required
def new_guest():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        phone = request.form.get("phone", "").strip()
        dietary_notes = request.form.get("dietary_notes", "").strip()
        preferences = request.form.get("preferences", "").strip()
        vip = 1 if request.form.get("vip") else 0
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Guest name is required.", "error")
            return render_template("guest_form.html", guest=None)

        conn = get_db()
        if email and conn.execute(
            "SELECT id FROM guests WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone():
            conn.close()
            flash(f"A guest profile with the email {email} already exists.", "error")
            return render_template("guest_form.html", guest=None)
        conn.execute(
            """INSERT INTO guests (name, email, phone, dietary_notes, preferences, vip, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, email, phone or None, dietary_notes or None,
             preferences or None, vip, notes or None,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        flash(f"Guest profile created for {name}.", "success")
        return redirect(url_for("guests"))

    return render_template("guest_form.html", guest=None)


@app.route("/guests/<int:guest_id>/edit", methods=["GET", "POST"])
@owner_required
def edit_guest(guest_id):
    conn = get_db()
    guest = conn.execute("SELECT * FROM guests WHERE id = ?", (guest_id,)).fetchone()
    if not guest:
        conn.close()
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        phone = request.form.get("phone", "").strip()
        dietary_notes = request.form.get("dietary_notes", "").strip()
        preferences = request.form.get("preferences", "").strip()
        vip = 1 if request.form.get("vip") else 0
        notes = request.form.get("notes", "").strip()

        if email and conn.execute(
            "SELECT id FROM guests WHERE email = ? COLLATE NOCASE AND id != ?", (email, guest_id)
        ).fetchone():
            conn.close()
            flash(f"Another guest profile already uses the email {email}.", "error")
            return redirect(url_for("edit_guest", guest_id=guest_id))
        conn.execute(
            """UPDATE guests SET name=?, email=?, phone=?, dietary_notes=?,
               preferences=?, vip=?, notes=? WHERE id=?""",
            (name, email, phone or None, dietary_notes or None,
             preferences or None, vip, notes or None, guest_id),
        )
        conn.commit()
        conn.close()
        flash("Guest profile updated.", "success")
        return redirect(url_for("guests"))

    conn.close()
    return render_template("guest_form.html", guest=guest)


@app.route("/guests/<int:guest_id>/delete", methods=["POST"])
@owner_required
def delete_guest(guest_id):
    conn = get_db()
    conn.execute("DELETE FROM guests WHERE id = ?", (guest_id,))
    conn.commit()
    conn.close()
    flash("Guest removed.", "success")
    return redirect(url_for("guests"))


# ---------------------------------------------------------------------------
# Supplier invoices & staff expense claims
#
# Deliberately simple: document + description + amount, reviewed by the
# owner. No AI extraction and no accounting-software sync — those need real
# API credentials this app doesn't have. Approved/paid records are the
# reference your accountant works from, same as the pay-rate fields above.
# ---------------------------------------------------------------------------

def save_expense_file(file):
    if not file or file.filename == "":
        return None
    if not allowed_file(file.filename):
        return False
    safe_name = secure_filename(file.filename)
    stored_name = f"expense_{secrets.token_hex(6)}_{safe_name}"
    file.save(os.path.join(UPLOAD_DIR, stored_name))
    return stored_name


@app.route("/expenses")
@owner_required
def expenses():
    status_filter = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()

    conn = get_db()
    invoices = conn.execute(
        "SELECT * FROM expenses WHERE kind = 'supplier_invoice' ORDER BY submitted_at DESC"
    ).fetchall()
    claims = conn.execute(
        """SELECT expenses.*, users.name AS submitter_name
           FROM expenses LEFT JOIN users ON users.id = expenses.submitted_by_user_id
           WHERE kind = 'staff_expense' ORDER BY submitted_at DESC"""
    ).fetchall()
    supplier_token = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'supplier_upload_token'"
    ).fetchone()["value"]
    conn.close()

    # Totals are deliberately computed BEFORE the filters below, so the tiles
    # stay a stable "what's outstanding" readout rather than re-summing
    # whatever subset happens to be on screen.
    all_rows = list(invoices) + list(claims)
    totals = {
        "pending_count": sum(1 for r in all_rows if r["status"] == "pending"),
        "pending_value": sum(r["amount"] or 0 for r in all_rows if r["status"] == "pending"),
        "unpaid_value": sum(r["amount"] or 0 for r in all_rows if r["status"] == "approved"),
    }

    if status_filter:
        invoices = [i for i in invoices if i["status"] == status_filter]
        claims = [c for c in claims if c["status"] == status_filter]
    if q:
        needle = q.lower()
        invoices = [i for i in invoices if needle in (i["vendor_name"] or "").lower() or needle in i["description"].lower()]
        claims = [
            c for c in claims
            if needle in (c["submitter_name"] or "").lower()
            or needle in (c["vendor_name"] or "").lower()
            or needle in c["description"].lower()
        ]

    supplier_upload_url = url_for("supplier_invoice_submit", token=supplier_token, _external=True)
    return render_template(
        "expenses.html", invoices=invoices, claims=claims, supplier_upload_url=supplier_upload_url,
        status_filter=status_filter, q=q, totals=totals,
    )


@app.route("/my-expenses")
@login_required
def my_expenses():
    user = current_user()
    conn = get_db()
    claims = conn.execute(
        "SELECT * FROM expenses WHERE kind = 'staff_expense' AND submitted_by_user_id = ? ORDER BY submitted_at DESC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template("my_expenses.html", claims=claims)


def known_vendor_names():
    """Names from the internal Vendors directory, for a suggestion list on
    the (authenticated, staff-only) expense form — never exposed on the
    public no-login supplier invoice page."""
    conn = get_db()
    names = [r["name"] for r in conn.execute("SELECT name FROM vendors ORDER BY name").fetchall()]
    conn.close()
    return names


@app.route("/expenses/submit", methods=["GET", "POST"])
@login_required
def submit_expense():
    if request.method == "POST":
        user = current_user()
        vendor_name = request.form.get("vendor_name", "").strip()
        description = request.form.get("description", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        vehicle_id = request.form.get("vehicle_id", "").strip()
        restaurant_related = 1 if request.form.get("restaurant_related") else 0
        file = request.files.get("receipt")

        try:
            amount = float(amount_raw)
        except ValueError:
            amount = None

        if not description or amount is None or amount <= 0:
            flash("A description and a valid amount are required.", "error")
            return render_template(
        "expense_form.html", vendors=known_vendor_names(),
        prefill_vendor=request.args.get("vendor", ""), prefill_vehicle_id=vehicle_id,
    )

        stored_name = save_expense_file(file)
        if stored_name is False:
            flash(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}", "error")
            return render_template(
        "expense_form.html", vendors=known_vendor_names(),
        prefill_vendor=request.args.get("vendor", ""), prefill_vehicle_id=vehicle_id,
    )

        conn = get_db()
        conn.execute(
            """INSERT INTO expenses
               (kind, submitted_by_user_id, vendor_name, description, amount, filename, vehicle_id, restaurant_related, submitted_at)
               VALUES ('staff_expense', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], vendor_name or None, description, amount, stored_name,
             vehicle_id or None, restaurant_related, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        flash("Expense submitted for approval.", "success")
        return redirect(url_for("my_expenses"))

    return render_template(
        "expense_form.html", vendors=known_vendor_names(),
        prefill_vendor=request.args.get("vendor", ""), prefill_vehicle_id=request.args.get("vehicle_id", ""),
    )


@app.route("/expenses/<int:expense_id>/decide", methods=["POST"])
@owner_required
def decide_expense(expense_id):
    status = request.form.get("status", "")
    if status not in ("approved", "rejected", "paid"):
        abort(400)
    note = request.form.get("owner_note", "").strip()
    conn = get_db()
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    # Guard against the same button double-submitting (two clicks racing
    # each other) re-sending the decision email — status != ? rather than
    # a fixed prior-state check, since the workflow legitimately moves
    # pending -> approved -> paid across separate, deliberate actions.
    cur = conn.execute(
        "UPDATE expenses SET status = ?, owner_note = ?, decided_at = ? WHERE id = ? AND status != ?",
        (status, note or None, datetime.now(timezone.utc).isoformat(), expense_id, status),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        flash("Already updated.", "success")
        return redirect(url_for("expenses"))
    submitter = (
        conn.execute("SELECT * FROM users WHERE id = ?", (row["submitted_by_user_id"],)).fetchone()
        if row["submitted_by_user_id"] else None
    )
    if submitter:
        send_email(
            submitter["email"],
            f"Your expense has been {status}",
            f"Hi {submitter['name'].split(' ')[0]},\n\n"
            f"Your {row['kind'].replace('_', ' ')} — {row['description']} (€{row['amount']:.2f}) — has been {status}."
            + (f"\n\nNote: {note}" if note else "")
            + f"\n\n— Château de Gudanes",
        )
        send_notification(
            conn, submitter["id"], "expense_decided", f"Your expense has been {status}",
            body=f"{row['description']} — €{row['amount']:.2f}" + (f" · {note}" if note else ""),
            link="/my-expenses",
        )
    conn.close()
    flash("Updated.", "success")
    return redirect(url_for("expenses"))


@app.route("/expenses/<int:expense_id>/toggle-restaurant", methods=["POST"])
@owner_required
def toggle_expense_restaurant(expense_id):
    conn = get_db()
    row = conn.execute("SELECT restaurant_related FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE expenses SET restaurant_related = ? WHERE id = ?",
        (0 if row["restaurant_related"] else 1, expense_id),
    )
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("expenses"))


@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@owner_required
def delete_expense(expense_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    if row["filename"]:
        path = os.path.join(UPLOAD_DIR, row["filename"])
        if os.path.exists(path):
            os.remove(path)
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    flash("Removed.", "success")
    return redirect(url_for("expenses"))


@app.route("/expenses/<int:expense_id>/file")
@login_required
def download_expense_file(expense_id):
    user = current_user()
    conn = get_db()
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    conn.close()
    if not row or not row["filename"]:
        abort(404)
    if user["role"] != "owner" and row["submitted_by_user_id"] != user["id"]:
        abort(403)
    return send_from_directory(UPLOAD_DIR, row["filename"], as_attachment=True)


@app.route("/expenses/<int:expense_id>/view")
@login_required
def view_expense_file(expense_id):
    user = current_user()
    conn = get_db()
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    conn.close()
    if not row or not row["filename"]:
        abort(404)
    if user["role"] != "owner" and row["submitted_by_user_id"] != user["id"]:
        abort(403)
    ext = row["filename"].rsplit(".", 1)[-1].lower() if "." in row["filename"] else ""
    if ext not in VIEWABLE_EXTENSIONS:
        abort(404)
    return send_from_directory(UPLOAD_DIR, row["filename"], as_attachment=False)


@app.route("/expenses/regenerate-supplier-link", methods=["POST"])
@owner_required
def regenerate_supplier_link():
    new_token = secrets.token_urlsafe(24)
    conn = get_db()
    conn.execute(
        "UPDATE app_settings SET value = ? WHERE key = 'supplier_upload_token'", (new_token,)
    )
    conn.commit()
    conn.close()
    flash("New supplier invoice link generated — the old one no longer works.", "success")
    return redirect(url_for("expenses"))


@app.route("/supplier-invoices/submit/<token>", methods=["GET", "POST"])
def supplier_invoice_submit(token):
    conn = get_db()
    stored = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'supplier_upload_token'"
    ).fetchone()
    if not stored or not hmac.compare_digest(token, stored["value"]):
        conn.close()
        abort(404)

    if request.method == "POST":
        vendor_name = request.form.get("vendor_name", "").strip()
        description = request.form.get("description", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        file = request.files.get("invoice")

        try:
            amount = float(amount_raw)
        except ValueError:
            amount = None

        if not vendor_name or not description or amount is None or amount <= 0:
            flash("Company name, a description, and a valid amount are required.", "error")
            conn.close()
            return render_template("supplier_invoice_form.html")

        stored_name = save_expense_file(file)
        if stored_name is False:
            flash(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}", "error")
            conn.close()
            return render_template("supplier_invoice_form.html")

        conn.execute(
            """INSERT INTO expenses
               (kind, vendor_name, description, amount, filename, submitted_at)
               VALUES ('supplier_invoice', ?, ?, ?, ?, ?)""",
            (vendor_name, description, amount, stored_name, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        return render_template("supplier_invoice_submitted.html")

    conn.close()
    return render_template("supplier_invoice_form.html")


# ---------------------------------------------------------------------------
# Public booking site — replaces the third-party widget. No login required;
# guests manage their own booking with a reference code + email, or a saved
# link. No live payment is taken here (see module docstring's payment note)
# — requests land as 'pending' and lock the calendar immediately either way,
# so nothing can double-book while you decide.
# ---------------------------------------------------------------------------

def guest_availability_grid(conn, rooms, month_arg):
    """Read-only per-day free/unavailable grid for the guest booking page —
    same underlying data as /admin/calendar (bookings + iCal blocks + manual
    blocks), collapsed to two states so nothing about a specific guest, an
    external platform, or a block's reason is exposed publicly."""
    today = datetime.now(timezone.utc).date()
    try:
        year, month = map(int, month_arg.split("-"))
        first_day = date(year, month, 1)
    except (ValueError, AttributeError):
        first_day = today.replace(day=1)

    next_month = date(first_day.year + 1, 1, 1) if first_day.month == 12 else date(first_day.year, first_day.month + 1, 1)
    prev_month = date(first_day.year - 1, 12, 1) if first_day.month == 1 else date(first_day.year, first_day.month - 1, 1)
    days = [first_day + timedelta(days=i) for i in range((next_month - first_day).days)]

    room_rows = []
    for room in rooms:
        bookings = conn.execute(
            """SELECT arrival_date, departure_date FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
               AND arrival_date < ? AND departure_date > ?""",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()
        blocked = conn.execute(
            "SELECT start_date, end_date FROM blocked_dates WHERE room_id = ? AND start_date < ? AND end_date > ?",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()
        manual_blocks = conn.execute(
            "SELECT start_date, end_date FROM room_blocks WHERE room_id = ? AND start_date < ? AND end_date > ?",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()
        taken_ranges = (
            [(parse_date(b["arrival_date"]), parse_date(b["departure_date"])) for b in bookings]
            + [(parse_date(r["start_date"]), parse_date(r["end_date"])) for r in blocked]
            + [(parse_date(r["start_date"]), parse_date(r["end_date"])) for r in manual_blocks]
        )
        cells = []
        for d in days:
            unavailable = any(start <= d < end for start, end in taken_ranges)
            cells.append({"date": d, "status": "unavailable" if unavailable else "free"})
        room_rows.append({"room": room, "cells": cells})

    return {
        "days": days, "room_rows": room_rows, "first_day": first_day, "today": today,
        "prev_month": prev_month.strftime("%Y-%m"), "next_month": next_month.strftime("%Y-%m"),
    }


@app.route("/book")
def book_rooms():
    conn = get_db()
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, name").fetchall()

    arrival_raw = request.args.get("arrival", "").strip()
    departure_raw = request.args.get("departure", "").strip()
    arrival, departure = parse_date(arrival_raw), parse_date(departure_raw)

    availability = {}
    unavailable_reason = {}
    searched = bool(arrival and departure and departure > arrival)
    if searched:
        stay_nights = (departure - arrival).days
        for room in rooms:
            ok, _ = is_range_available(conn, room["id"], arrival, departure)
            too_short = stay_nights < room["min_nights"]
            availability[room["id"]] = ok and not too_short
            if too_short:
                unavailable_reason[room["id"]] = f"Requires a {room['min_nights']}-night minimum stay"
            elif not ok:
                unavailable_reason[room["id"]] = "Not available these dates"
    nothing_available = searched and rooms and not any(availability.values())

    grid = guest_availability_grid(conn, rooms, request.args.get("month", ""))
    gallery_photos_by_room = {}
    for room in rooms:
        gallery_photos_by_room[room["id"]] = conn.execute(
            "SELECT * FROM room_photos WHERE room_id = ? ORDER BY sort_order, id", (room["id"],)
        ).fetchall()
    featured_reviews = conn.execute(
        """SELECT guest_feedback.*, rooms.name AS room_name FROM guest_feedback
           LEFT JOIN bookings ON bookings.id = guest_feedback.booking_id
           LEFT JOIN rooms ON rooms.id = bookings.room_id
           WHERE guest_feedback.featured = 1
           ORDER BY guest_feedback.rating DESC, guest_feedback.submitted_at DESC LIMIT 6"""
    ).fetchall()
    conn.close()
    return render_template(
        "book_rooms.html", rooms=rooms, arrival=arrival_raw, departure=departure_raw,
        availability=availability, unavailable_reason=unavailable_reason, searched=searched,
        nothing_available=nothing_available,
        prefill_name=request.args.get("name", ""), prefill_email=request.args.get("email", ""),
        prefill_phone=request.args.get("phone", ""), prefill_party_size=request.args.get("party_size", ""),
        gallery_photos_by_room=gallery_photos_by_room, featured_reviews=featured_reviews,
        **grid,
    )


@app.route("/waitlist/join", methods=["POST"])
def join_waitlist():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    desired_arrival = request.form.get("desired_arrival", "").strip()
    desired_departure = request.form.get("desired_departure", "").strip()
    party_size_raw = request.form.get("party_size", "").strip()
    notes = request.form.get("notes", "").strip()

    conn = get_db()
    if rate_limited(conn, "join_waitlist", BOOKING_RATE_LIMIT_PER_HOUR):
        conn.commit()
        conn.close()
        flash("Too many attempts from this connection — please try again in a bit.", "error")
        return redirect(url_for("book_rooms", arrival=desired_arrival, departure=desired_departure))

    if not name or not email:
        conn.commit()
        conn.close()
        flash("Name and email are required.", "error")
        return redirect(url_for("book_rooms", arrival=desired_arrival, departure=desired_departure))
    if not EMAIL_RE.match(email):
        conn.commit()
        conn.close()
        flash("Enter a valid email address.", "error")
        return redirect(url_for("book_rooms", arrival=desired_arrival, departure=desired_departure))

    party_size = int(party_size_raw) if party_size_raw.isdigit() else None
    conn.execute(
        """INSERT INTO waitlist_entries
           (name, email, phone, desired_arrival, desired_departure, party_size, notes, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (name, email, phone or None, desired_arrival or None, desired_departure or None,
         party_size, notes or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    owner_to = owner_email(conn)
    if owner_to:
        send_email(
            owner_to, "New waitlist request",
            f"{name} ({email}) would like {desired_arrival or '?'} to {desired_departure or '?'}, "
            f"but nothing was available.\n\n{notes or ''}",
        )
    conn.close()
    flash("You're on the waitlist — we'll reach out if those dates open up.", "success")
    return redirect(url_for("book_rooms", arrival=desired_arrival, departure=desired_departure))


@app.route("/admin/waitlist")
@owner_required
def admin_waitlist():
    conn = get_db()
    entries = conn.execute(
        "SELECT * FROM waitlist_entries ORDER BY (status != 'open'), created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("admin_waitlist.html", entries=entries)


@app.route("/admin/waitlist/export.csv")
@owner_required
def export_waitlist_csv():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM waitlist_entries ORDER BY (status != 'open'), created_at DESC"
    ).fetchall()
    conn.close()
    fieldnames = ["name", "email", "phone", "desired_arrival", "desired_departure", "party_size",
                  "notes", "status", "created_at"]
    return csv_response(fieldnames, rows, "waitlist.csv")


@app.route("/admin/waitlist/<int:entry_id>/status", methods=["POST"])
@owner_required
def update_waitlist_status(entry_id):
    status = request.form.get("status", "")
    if status not in ("open", "contacted", "booked", "closed"):
        abort(400)
    conn = get_db()
    conn.execute("UPDATE waitlist_entries SET status = ? WHERE id = ?", (status, entry_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_waitlist"))


@app.route("/api/validate-promo-code", methods=["POST"])
def api_validate_promo_code():
    """Live preview for a promo code on any of the three guest booking
    forms — recomputes the real subtotal server-side from the same data
    every booking flow uses (never trusts a client-supplied amount), so
    the preview always matches what the guest is actually charged. Not a
    guarantee: the code is re-validated again for real at booking-creation
    time, since capacity/expiry/redemption limits can change in between."""
    conn = get_db()
    if rate_limited(conn, "validate_promo_code", 30):
        conn.commit()
        conn.close()
        return jsonify({"valid": False, "message": "Too many attempts — try again shortly."}), 429
    conn.commit()

    code = request.form.get("code", "").strip()
    category = request.form.get("category", "").strip()
    subtotal = 0.0
    if category == "room":
        room_id_raw = request.form.get("room_id", "").strip()
        arrival, departure = parse_date(request.form.get("arrival_date", "")), parse_date(request.form.get("departure_date", ""))
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id_raw,)).fetchone() if room_id_raw.isdigit() else None
        if not room or not arrival or not departure or departure <= arrival:
            conn.close()
            return jsonify({"valid": False, "message": "Choose your arrival and departure dates first."})
        subtotal = compute_room_total(conn, room, arrival, departure)
    elif category == "restaurant":
        party_size = int(request.form.get("party_size", "")) if request.form.get("party_size", "").isdigit() else 0
        settings = get_restaurant_settings(conn)
        if party_size < 1 or not settings or not settings["price_per_person"]:
            conn.close()
            return jsonify({"valid": False, "message": "Enter your party size first."})
        subtotal = settings["price_per_person"] * party_size
    elif category == "workshop":
        session_id_raw = request.form.get("session_id", "").strip()
        party_size = int(request.form.get("party_size", "")) if request.form.get("party_size", "").isdigit() else 0
        price_row = conn.execute(
            """SELECT workshops.price_per_person FROM workshop_sessions
               JOIN workshops ON workshops.id = workshop_sessions.workshop_id
               WHERE workshop_sessions.id = ?""",
            (session_id_raw,),
        ).fetchone() if session_id_raw.isdigit() else None
        if not price_row or party_size < 1 or not price_row["price_per_person"]:
            conn.close()
            return jsonify({"valid": False, "message": "Enter your party size first."})
        subtotal = price_row["price_per_person"] * party_size
    else:
        conn.close()
        return jsonify({"valid": False, "message": "Unknown booking type."}), 400

    promo, discount_amount, error = validate_promo_code(conn, code, category, subtotal)
    conn.close()
    if not promo:
        return jsonify({"valid": False, "message": error})
    return jsonify({
        "valid": True, "message": f"Code applied — €{discount_amount:.2f} off.",
        "subtotal": round(subtotal, 2), "discount_amount": discount_amount,
        "total": round(subtotal - discount_amount, 2),
    })


@app.route("/book/<int:room_id>", methods=["GET", "POST"])
def book_room(room_id):
    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ? AND active = 1", (room_id,)).fetchone()
    if not room:
        conn.close()
        abort(404)
    extras = conn.execute("SELECT * FROM extras WHERE active = 1 ORDER BY sort_order, name").fetchall()
    gallery_photos = conn.execute(
        "SELECT * FROM room_photos WHERE room_id = ? ORDER BY sort_order, id", (room_id,)
    ).fetchall()

    if request.method == "POST":
        if rate_limited(conn, "book_room", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many booking attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("book_room.html", room=room, arrival="", departure="", extras=extras, stripe_enabled=stripe_enabled(), gallery_photos=gallery_photos)
        arrival_raw = request.form.get("arrival_date", "").strip()
        departure_raw = request.form.get("departure_date", "").strip()
        guest_name = request.form.get("guest_name", "").strip()
        guest_email = request.form.get("guest_email", "").strip().lower()
        guest_phone = request.form.get("guest_phone", "").strip()
        party_size_raw = request.form.get("party_size", "").strip()
        special_requests = request.form.get("special_requests", "").strip()
        selected_extra_ids = {int(i) for i in request.form.getlist("extras") if i.isdigit()}
        agreed_to_terms = request.form.get("agree_terms") == "on"
        promo_code = request.form.get("promo_code", "").strip()

        arrival, departure = parse_date(arrival_raw), parse_date(departure_raw)
        party_size = int(party_size_raw) if party_size_raw.isdigit() else None

        error = None
        if not guest_name or not guest_email:
            error = "Name and email are required."
        elif not EMAIL_RE.match(guest_email):
            error = "Enter a valid email address."
        elif not arrival or not departure:
            error = "Choose valid arrival and departure dates."
        elif arrival < datetime.now(timezone.utc).date():
            # The public form had no past-date guard, so anyone could POST an
            # arrival months gone. Those rows then sat in the bookings table
            # skewing occupancy and revenue for a period already closed, and
            # nothing on the admin side flagged them as impossible. The
            # restaurant and workshop forms already refused this.
            error = "Choose an arrival date in the future."
        elif not party_size or party_size < 1:
            error = "Party size is required."
        elif party_size > room["max_occupancy"]:
            error = f"This room sleeps up to {room['max_occupancy']}."
        elif (departure - arrival).days < room["min_nights"]:
            error = f"This room requires a minimum stay of {room['min_nights']} night{'s' if room['min_nights'] != 1 else ''}."
        elif not agreed_to_terms:
            error = "Please confirm you agree to the Terms & Conditions."
        else:
            ok, reason = is_range_available(conn, room_id, arrival, departure)
            if not ok:
                error = reason

        if error:
            flash(error, "error")
            conn.commit()  # persist the rate-limit log entry even on a validation error
            conn.close()
            return render_template("book_room.html", room=room, arrival=arrival_raw, departure=departure_raw, extras=extras, stripe_enabled=stripe_enabled(), gallery_photos=gallery_photos)

        nights = (departure - arrival).days
        chosen_extras = [e for e in extras if e["id"] in selected_extra_ids]
        room_total = compute_room_total(conn, room, arrival, departure)

        discount_amount = 0.0
        if promo_code:
            promo, discount_amount, promo_error = validate_promo_code(conn, promo_code, "room", room_total)
            if not promo:
                flash(f"Promo code not applied: {promo_error}", "error")
        discounted_room_total = round(room_total - discount_amount, 2)
        grand_total = discounted_room_total + sum(e["price"] for e in chosen_extras)

        if stripe_enabled() and grand_total > 0:
            line_items = []
            if discounted_room_total:
                # One line item for the whole stay rather than
                # unit_amount * nights — a seasonal rate override can make
                # nights within the same stay worth different amounts, so
                # there's no single per-night unit price left to itemize.
                room_line_name = f"{room['name']} — {nights} night{'s' if nights != 1 else ''}"
                if discount_amount:
                    room_line_name += f" (promo code applied, -€{discount_amount:.2f})"
                line_items.append({
                    "price_data": {
                        "currency": "eur",
                        "product_data": {"name": room_line_name},
                        "unit_amount": int(round(discounted_room_total * 100)),
                    },
                    "quantity": 1,
                })
            for e in chosen_extras:
                line_items.append({
                    "price_data": {
                        "currency": "eur",
                        "product_data": {"name": e["name"]},
                        "unit_amount": int(round(e["price"] * 100)),
                    },
                    "quantity": 1,
                })
            try:
                checkout_session = stripe.checkout.Session.create(
                    mode="payment",
                    payment_method_types=["card"],
                    line_items=line_items,
                    customer_email=guest_email,
                    success_url=url_for("stripe_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=url_for("stripe_cancel", room_id=room_id, _external=True),
                    metadata={
                        "room_id": str(room_id),
                        "guest_name": guest_name,
                        "guest_email": guest_email,
                        "guest_phone": guest_phone,
                        "arrival_date": arrival.isoformat(),
                        "departure_date": departure.isoformat(),
                        "party_size": str(party_size),
                        "special_requests": special_requests[:490],
                        "extra_ids": ",".join(str(e["id"]) for e in chosen_extras),
                        "promo_code": promo_code if discount_amount else "",
                    },
                )
            except Exception as e:
                flash(f"Payment setup failed ({e}). Please try again.", "error")
                conn.commit()  # persist the rate-limit log entry even when Stripe setup fails
                conn.close()
                return render_template("book_room.html", room=room, arrival=arrival_raw, departure=departure_raw, extras=extras, stripe_enabled=stripe_enabled(), gallery_photos=gallery_photos)
            conn.commit()
            conn.close()
            return redirect(checkout_session.url, code=303)

        _, manage_token = create_booking(
            conn, room, guest_name, guest_email, guest_phone, arrival, departure,
            party_size, special_requests, chosen_extras, promo_code=promo_code or None,
        )
        conn.close()
        return redirect(url_for("booking_confirmation", manage_token=manage_token))

    arrival_raw = request.args.get("arrival", "")
    departure_raw = request.args.get("departure", "")
    prefill_name = request.args.get("name", "")
    prefill_email = request.args.get("email", "")
    prefill_phone = request.args.get("phone", "")
    prefill_party_size = request.args.get("party_size", "")
    conn.close()
    return render_template(
        "book_room.html", room=room, arrival=arrival_raw, departure=departure_raw, extras=extras,
        stripe_enabled=stripe_enabled(), prefill_name=prefill_name, prefill_email=prefill_email,
        prefill_phone=prefill_phone, prefill_party_size=prefill_party_size, gallery_photos=gallery_photos,
    )


@app.route("/book/confirmation/<manage_token>")
def booking_confirmation(manage_token):
    conn = get_db()
    booking = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id WHERE manage_token = ?""",
        (manage_token,),
    ).fetchone()
    conn.close()
    if not booking:
        abort(404)
    return render_template("booking_confirmation.html", booking=booking)


@app.route("/book/stripe-success")
def stripe_success():
    session_id = request.args.get("session_id", "").strip()
    if not stripe_enabled() or not session_id:
        abort(404)

    conn = get_db()
    existing = conn.execute(
        "SELECT manage_token FROM bookings WHERE stripe_session_id = ?", (session_id,)
    ).fetchone()
    if existing:
        conn.close()
        return redirect(url_for("manage_booking", manage_token=existing["manage_token"]))

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        conn.close()
        abort(404)

    if sval(session, "payment_status") != "paid":
        conn.close()
        flash("That payment wasn't completed, so no booking was made.", "error")
        return redirect(url_for("book_rooms"))

    try:
        manage_token = create_booking_from_stripe_session(conn, session)
    except sqlite3.IntegrityError:
        # The webhook won the race and created it a moment ago. Show the guest
        # their booking rather than an error — they have paid either way.
        row = conn.execute(
            "SELECT manage_token FROM bookings WHERE stripe_session_id = ?", (session_id,)
        ).fetchone()
        manage_token = row["manage_token"] if row else None
    conn.close()
    if not manage_token:
        flash("Payment went through, but we couldn't create the booking automatically — contact the château directly with your payment reference.", "error")
        return redirect(url_for("book_rooms"))
    return redirect(url_for("booking_confirmation", manage_token=manage_token))


@app.route("/book/stripe-cancel")
def stripe_cancel():
    """Where Stripe returns a guest who abandons checkout. This existed but
    nothing pointed at it — cancel_url went straight back to the booking
    form, so the guest got no word that nothing had been booked and could
    reasonably assume it had. Returns them to the room they were booking."""
    flash("Payment was cancelled — no booking was made.", "error")
    room_id = request.args.get("room_id", type=int)
    if room_id:
        return redirect(url_for("book_room", room_id=room_id))
    return redirect(url_for("book_rooms"))


@app.route("/webhooks/stripe", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        abort(404)
    try:
        event = stripe.Webhook.construct_event(
            request.data, request.headers.get("Stripe-Signature", ""), STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        abort(400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meta = smeta(session)
        conn = get_db()
        try:
            if meta.get("kind") in ("workshop_deposit", "workshop_balance"):
                if sval(session, "payment_status") == "paid":
                    mark_workshop_payment_paid(conn, session)
            elif meta.get("kind") == "restaurant":
                existing = conn.execute(
                    "SELECT id FROM restaurant_bookings WHERE stripe_session_id = ?", (session["id"],)
                ).fetchone()
                if not existing and sval(session, "payment_status") == "paid":
                    create_restaurant_booking_from_stripe_session(conn, session)
            else:
                existing = conn.execute(
                    "SELECT id FROM bookings WHERE stripe_session_id = ?", (session["id"],)
                ).fetchone()
                if not existing and sval(session, "payment_status") == "paid":
                    create_booking_from_stripe_session(conn, session)
        except sqlite3.IntegrityError:
            # The guest's success redirect got there first. Nothing to do —
            # the booking exists, and this is the outcome we want.
            pass
        except Exception as e:
            # Never leave the connection open on the way out: a leaked handle
            # made the next write fail with "database is locked". Re-raise so
            # Stripe sees a 500 and retries, which is the correct behaviour
            # for a payment we may not have recorded.
            print(f"[stripe webhook] {event['type']} failed: {e}")
            raise
        finally:
            conn.close()

    return jsonify(received=True), 200


@app.route("/book/manage", methods=["GET", "POST"])
def find_booking():
    if request.method == "POST":
        conn = get_db()
        if rate_limited(conn, "find_booking", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("find_booking.html")
        conn.commit()
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        booking = conn.execute(
            "SELECT manage_token FROM bookings WHERE reference_code = ? AND guest_email = ?",
            (reference_code, email),
        ).fetchone()
        conn.close()
        if booking:
            return redirect(url_for("manage_booking", manage_token=booking["manage_token"]))
        flash("No booking found with that reference and email.", "error")
    return render_template("find_booking.html")


@app.route("/checkin/<manage_token>")
def guest_checkin(manage_token):
    """Short, memorable alias for the guest link — same page as
    manage_booking, just a friendlier URL for check-in emails/texts."""
    return redirect(url_for("manage_booking", manage_token=manage_token))


def booking_has_transfer(booking):
    return "transfer" in (booking["extras_summary"] or "").lower()


@app.route("/book/manage/<manage_token>", methods=["GET", "POST"])
def manage_booking(manage_token):
    conn = get_db()
    booking = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id WHERE manage_token = ?""",
        (manage_token,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)

    action = request.form.get("action") if request.method == "POST" else None

    if action == "cancel":
        # Atomic UPDATE...WHERE status IN (...) + rowcount, not a separate
        # SELECT-then-UPDATE — a double-click or two tabs racing the old
        # check-then-write shape could both pass the status check before
        # either committed, sending the owner two cancellation emails.
        cur = conn.execute(
            "UPDATE bookings SET status = 'cancelled', decided_at = ? WHERE id = ? AND status IN ('pending', 'confirmed')",
            (datetime.now(timezone.utc).isoformat(), booking["id"]),
        )
        conn.commit()
        if cur.rowcount:
            owner_to = owner_email(conn)
            if owner_to:
                paid_note = " They had paid — refund it from the booking admin page if appropriate." if booking["payment_status"] == "paid" else ""
                send_email(
                    owner_to,
                    f"Guest cancelled — {booking['room_name']} ({booking['reference_code']})",
                    f"{booking['guest_name']} cancelled their own booking for {booking['room_name']}, "
                    f"{booking['arrival_date']} to {booking['departure_date']}.{paid_note}",
                )
            flash("Your booking has been cancelled.", "success")
        conn.close()
        return redirect(url_for("manage_booking", manage_token=manage_token))

    elif action == "request" and booking["status"] in ("pending", "confirmed"):
        message = request.form.get("message", "").strip()[:1000]
        if message:
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                """INSERT INTO tasks (title, notes, room_note, priority, due_date, created_at, origin, booking_id)
                   VALUES (?, ?, ?, 'normal', ?, ?, 'guest_request', ?)""",
                (message[:120], message, f"{booking['room_name']} — {booking['guest_name']}",
                 booking["arrival_date"], now, booking["id"]),
            )
            conn.commit()
            owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
            if owner:
                send_notification(
                    conn, owner["id"], "guest_request",
                    f"Request from {booking['guest_name']} ({booking['room_name']})",
                    body=message, link="/admin/overview", related_task_id=cur.lastrowid,
                )
            flash("Sent — we'll take care of it.", "success")
        conn.close()
        return redirect(url_for("manage_booking", manage_token=manage_token))

    elif action == "transfer" and booking["status"] in ("pending", "confirmed") and booking_has_transfer(booking):
        flight_number = request.form.get("flight_number", "").strip()[:60]
        arrival_time = request.form.get("arrival_time", "").strip()[:60]
        notes = request.form.get("notes", "").strip()[:500]
        conn.execute(
            "UPDATE bookings SET transfer_flight_number = ?, transfer_arrival_time = ?, transfer_notes = ? WHERE id = ?",
            (flight_number or None, arrival_time or None, notes or None, booking["id"]),
        )
        detail = ", ".join(filter(None, [
            f"flight {flight_number}" if flight_number else None,
            f"arriving {arrival_time}" if arrival_time else None,
            notes or None,
        ])) or "details updated"
        # Idempotent: editing the form again updates the same open task
        # rather than spawning a fresh one every time a guest tweaks it.
        existing = conn.execute(
            "SELECT id FROM tasks WHERE booking_id = ? AND title = 'Airport transfer' AND status != 'done'",
            (booking["id"],),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            conn.execute(
                "UPDATE tasks SET notes = ?, room_note = ?, due_date = ? WHERE id = ?",
                (detail, f"{booking['room_name']} — {booking['guest_name']}", booking["arrival_date"], existing["id"]),
            )
            task_id = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO tasks (title, notes, room_note, priority, due_date, created_at, origin, booking_id)
                   VALUES ('Airport transfer', ?, ?, 'high', ?, ?, 'guest_request', ?)""",
                (detail, f"{booking['room_name']} — {booking['guest_name']}", booking["arrival_date"], now, booking["id"]),
            )
            task_id = cur.lastrowid
        conn.commit()
        owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        if owner:
            send_notification(
                conn, owner["id"], "guest_request",
                f"Transfer details from {booking['guest_name']} ({booking['room_name']})",
                body=detail, link="/admin/overview", related_task_id=task_id,
            )
        flash("Transfer details saved.", "success")
        conn.close()
        return redirect(url_for("manage_booking", manage_token=manage_token))

    elif action == "arrival_time" and booking["status"] in ("pending", "confirmed"):
        estimated = request.form.get("estimated_arrival_time", "").strip()[:60]
        conn.execute(
            "UPDATE bookings SET estimated_arrival_time = ? WHERE id = ?",
            (estimated or None, booking["id"]),
        )
        conn.commit()
        flash("Thanks — we've noted your arrival time.", "success")
        conn.close()
        return redirect(url_for("manage_booking", manage_token=manage_token))

    elif action == "change_dates" and booking["status"] in ("pending", "confirmed"):
        new_arrival = parse_date(request.form.get("new_arrival_date", "").strip())
        new_departure = parse_date(request.form.get("new_departure_date", "").strip())
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (booking["room_id"],)).fetchone()

        if not new_arrival or not new_departure or new_departure <= new_arrival:
            flash("Choose valid new dates.", "error")
        elif (new_departure - new_arrival).days < room["min_nights"]:
            flash(f"This room requires a minimum stay of {room['min_nights']} night{'s' if room['min_nights'] != 1 else ''}.", "error")
        else:
            ok, reason = is_range_available(conn, booking["room_id"], new_arrival, new_departure, exclude_booking_id=booking["id"])
            if not ok:
                flash(f"Those dates aren't available: {reason}", "error")
            elif booking["status"] == "pending" and booking["payment_status"] != "paid":
                # Nothing's been committed or charged yet, so it's safe to
                # apply immediately rather than routing a still-pending
                # request through the owner.
                old_arrival, old_departure = parse_date(booking["arrival_date"]), parse_date(booking["departure_date"])
                old_room_portion = compute_room_total(conn, room, old_arrival, old_departure)
                extras_portion = (booking["total_price"] or 0) - old_room_portion
                new_total = compute_room_total(conn, room, new_arrival, new_departure) + extras_portion
                conn.execute(
                    "UPDATE bookings SET arrival_date = ?, departure_date = ?, total_price = ? WHERE id = ?",
                    (new_arrival.isoformat(), new_departure.isoformat(), new_total or None, booking["id"]),
                )
                conn.commit()
                owner_to = owner_email(conn)
                if owner_to:
                    send_email(
                        owner_to, f"Guest changed dates — {booking['reference_code']}",
                        f"{booking['guest_name']} changed their {booking['room_name']} booking to "
                        f"{new_arrival.isoformat()} → {new_departure.isoformat()} "
                        f"(was {booking['arrival_date']} → {booking['departure_date']}).",
                    )
                flash("Your dates have been updated.", "success")
            else:
                # Confirmed or already paid — a date change here could shift
                # what's owed, so it goes to the owner for a deliberate
                # decision rather than silently rewriting a commitment
                # that's already been made (and possibly paid for).
                note = (
                    f"{booking['guest_name']} would like to move their {booking['room_name']} booking from "
                    f"{booking['arrival_date']} → {booking['departure_date']} to "
                    f"{new_arrival.isoformat()} → {new_departure.isoformat()}. Those new dates are "
                    f"available — review and apply the change from the booking's edit page if you're happy with it."
                )
                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    """INSERT INTO tasks (title, notes, room_note, priority, due_date, created_at, origin, booking_id)
                       VALUES (?, ?, ?, 'normal', ?, ?, 'guest_request', ?)""",
                    (f"Date change request — {booking['reference_code']}", note,
                     f"{booking['room_name']} — {booking['guest_name']}", new_arrival.isoformat(), now, booking["id"]),
                )
                conn.commit()
                owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
                if owner:
                    send_notification(
                        conn, owner["id"], "guest_request", f"Date change request — {booking['guest_name']}",
                        body=note, link="/admin/overview", related_task_id=cur.lastrowid,
                    )
                flash("Your request has been sent — we'll confirm shortly.", "success")
        conn.close()
        return redirect(url_for("manage_booking", manage_token=manage_token))

    elif action == "book_dinner" and booking["status"] == "confirmed":
        restaurant_settings = get_restaurant_settings(conn)
        if not restaurant_settings or not restaurant_settings["enabled"]:
            flash("Dinner reservations aren't open yet.", "error")
            conn.close()
            return redirect(url_for("manage_booking", manage_token=manage_token))

        dinner_date = parse_date(request.form.get("dinner_date", ""))
        party_size_raw = request.form.get("dinner_party_size", "").strip()
        party_size = int(party_size_raw) if party_size_raw.isdigit() else None
        dietary_notes = request.form.get("dinner_dietary_notes", "").strip()[:500]
        stay_start, stay_end = parse_date(booking["arrival_date"]), parse_date(booking["departure_date"])
        min_date = parse_date(restaurant_settings["opening_date"]) if restaurant_settings["opening_date"] else stay_start

        error = None
        if not dinner_date or not (stay_start <= dinner_date < stay_end):
            error = "Choose a date within your stay."
        elif dinner_date < min_date:
            error = f"Dinner service isn't open until {format_date_human(min_date.isoformat())}."
        elif not party_size or party_size < 1:
            error = "Party size is required."
        elif party_size > restaurant_settings["capacity"]:
            error = f"We can seat a maximum of {restaurant_settings['capacity']} at once."
        elif restaurant_remaining_capacity(conn, dinner_date.isoformat()) < party_size:
            error = "That date is fully booked for dinner — please try another night."

        if error:
            flash(error, "error")
        else:
            create_restaurant_booking(
                conn, booking["guest_name"], booking["guest_email"], booking["guest_phone"],
                dinner_date.isoformat(), party_size, dietary_notes, booking_id=booking["id"],
            )
            flash("Dinner reservation requested — we'll confirm shortly.", "success")
        conn.close()
        return redirect(url_for("manage_booking", manage_token=manage_token))

    guest_requests = conn.execute(
        """SELECT * FROM tasks WHERE booking_id = ? AND origin = 'guest_request' AND title != 'Airport transfer'
           ORDER BY created_at DESC""",
        (booking["id"],),
    ).fetchall()
    dinner_bookings = conn.execute(
        "SELECT * FROM restaurant_bookings WHERE booking_id = ? ORDER BY dinner_date", (booking["id"],)
    ).fetchall()
    restaurant_settings = get_restaurant_settings(conn)
    dinner_min_date = dinner_max_date = None
    dinner_available = False
    if restaurant_settings and restaurant_settings["enabled"] and booking["status"] == "confirmed":
        stay_start, stay_end = parse_date(booking["arrival_date"]), parse_date(booking["departure_date"])
        opening = parse_date(restaurant_settings["opening_date"]) if restaurant_settings["opening_date"] else stay_start
        dinner_min_date = max(stay_start, opening).isoformat()
        dinner_max_date = (stay_end - timedelta(days=1)).isoformat()
        dinner_available = dinner_min_date <= dinner_max_date
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (booking["room_id"],)).fetchone()
    conn.close()
    return render_template(
        "manage_booking.html", booking=booking, guest_requests=guest_requests, room=room,
        has_transfer=booking_has_transfer(booking), dinner_bookings=dinner_bookings,
        restaurant_settings=restaurant_settings, dinner_min_date=dinner_min_date, dinner_max_date=dinner_max_date,
        dinner_available=dinner_available,
    )


@app.route("/book/<manage_token>/calendar.ics")
def booking_calendar_ics(manage_token):
    conn = get_db()
    booking = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id WHERE manage_token = ?""",
        (manage_token,),
    ).fetchone()
    conn.close()
    if not booking or booking["status"] not in ("pending", "confirmed"):
        abort(404)
    body = generate_booking_ics(booking, booking["room_name"])
    return app.response_class(
        body, mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{booking["reference_code"]}.ics"'},
    )


@app.route("/feedback/<token>", methods=["GET", "POST"])
def guest_feedback(token):
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE manage_token = ?", (token,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    today = datetime.now(timezone.utc).date()
    departure = parse_date(booking["departure_date"])
    too_early = not departure or departure > today
    existing = conn.execute(
        "SELECT 1 FROM guest_feedback WHERE booking_id = ?", (booking["id"],)
    ).fetchone()

    if request.method == "POST" and not too_early and not existing:
        rating_raw = request.form.get("rating", "")
        comment = request.form.get("comment", "").strip()
        try:
            rating = int(rating_raw)
        except ValueError:
            rating = 0
        if rating < 1 or rating > 5:
            flash("Choose a rating from 1 to 5.", "error")
        else:
            try:
                conn.execute(
                    "INSERT INTO guest_feedback (booking_id, guest_name, rating, comment, submitted_at) VALUES (?, ?, ?, ?, ?)",
                    (booking["id"], booking["guest_name"], rating, comment or None,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # A concurrent submission (double-click, two tabs) beat this
                # one to it — the unique index is the real guard, the
                # `existing` check above is just the common-case fast path.
                conn.rollback()
            conn.close()
            return render_template("guest_feedback_submitted.html")

    conn.close()
    return render_template(
        "guest_feedback_form.html", booking=booking, too_early=too_early, already_submitted=bool(existing),
    )


@app.route("/workshops/feedback/<token>", methods=["GET", "POST"])
def workshop_feedback(token):
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.manage_token = ?""",
        (token,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    today = datetime.now(timezone.utc).date()
    end_date = parse_date(booking["end_date"])
    too_early = not end_date or end_date > today
    existing = conn.execute(
        "SELECT 1 FROM workshop_feedback WHERE workshop_booking_id = ?", (booking["id"],)
    ).fetchone()

    if request.method == "POST" and not too_early and not existing:
        rating_raw = request.form.get("rating", "")
        comment = request.form.get("comment", "").strip()
        try:
            rating = int(rating_raw)
        except ValueError:
            rating = 0
        if rating < 1 or rating > 5:
            flash("Choose a rating from 1 to 5.", "error")
        else:
            try:
                conn.execute(
                    "INSERT INTO workshop_feedback (workshop_booking_id, guest_name, rating, comment, submitted_at) VALUES (?, ?, ?, ?, ?)",
                    (booking["id"], booking["guest_name"], rating, comment or None,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
            conn.close()
            return render_template("guest_feedback_submitted.html")

    conn.close()
    return render_template(
        "workshop_feedback_form.html", booking=booking, too_early=too_early, already_submitted=bool(existing),
    )


# ---------------------------------------------------------------------------
# Events — one-off venue-hire inquiries (weddings, photoshoots, corporate
# offsites...). Unlike rooms/restaurant/workshops, an event has no fixed
# catalog price or capacity to check against — every one is bespoke, so
# this is an inquiry-and-quote flow rather than an instant-book flow: a
# guest describes what they want, the owner follows up with availability
# and a price, and the inquiry moves new -> contacted -> quoted ->
# confirmed/declined. Reuses the same reference-code + manage-token
# self-service pattern as the other three booking engines.
# ---------------------------------------------------------------------------

# Presets only — the real list is owner-editable and lives in app_settings.
# Kept as the seed and as the fallback if the setting is ever emptied.
DEFAULT_EVENT_TYPES = ["wedding", "photoshoot", "corporate", "other"]


def event_types(conn):
    """The owner's current event-type list.

    Stored as a newline-separated string in app_settings rather than a table:
    it's a short, flat, ordered list with no other data hanging off it, and
    this keeps the public form, the owner form and validation reading from
    one place. Falls back to the presets if unset or emptied.
    """
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'event_types'").fetchone()
    if not row or not (row["value"] or "").strip():
        return list(DEFAULT_EVENT_TYPES)
    seen, out = set(), []
    for line in row["value"].splitlines():
        t = line.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out or list(DEFAULT_EVENT_TYPES)


def known_event_types(conn):
    """Current types PLUS any already used by existing records, so a type the
    owner has since removed still validates and still renders on old events
    rather than silently breaking them."""
    types = event_types(conn)
    used = conn.execute(
        "SELECT DISTINCT event_type FROM event_inquiries WHERE event_type IS NOT NULL"
    ).fetchall()
    return types + [r["event_type"] for r in used if r["event_type"] not in types]


def make_event_reference_code():
    return "EVT-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def event_email_context(inquiry):
    price_lines = []
    if inquiry["quoted_price"]:
        price_lines.append(f"Quoted price: €{inquiry['quoted_price']:.2f}")
    return {
        "contact_name": inquiry["contact_name"],
        "event_type": inquiry["event_type"],
        "reference_code": inquiry["reference_code"],
        "price_block": ("\n" + "\n".join(price_lines)) if price_lines else "",
        "manage_url": url_for("event_manage", manage_token=inquiry["manage_token"], _external=True),
    }


def send_event_email(conn, inquiry, template_key, context):
    subject, body = render_email_template(conn, template_key, context)
    if not subject:
        return
    send_email(inquiry["contact_email"], subject, body)


@app.route("/events")
def events_info():
    conn = get_db()
    types = event_types(conn)
    conn.close()
    return render_template("events_info.html", event_types=types)


@app.route("/events/inquire", methods=["POST"])
def submit_event_inquiry():
    event_type = request.form.get("event_type", "").strip()
    contact_name = request.form.get("contact_name", "").strip()
    contact_email = request.form.get("contact_email", "").strip().lower()
    contact_phone = request.form.get("contact_phone", "").strip()
    preferred_date = request.form.get("preferred_date", "").strip()
    alternate_date = request.form.get("alternate_date", "").strip()
    guest_count_raw = request.form.get("guest_count", "").strip()
    message = request.form.get("message", "").strip()[:2000]

    conn = get_db()
    if rate_limited(conn, "event_inquiry", BOOKING_RATE_LIMIT_PER_HOUR):
        conn.commit()
        conn.close()
        flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
        return redirect(url_for("events_info"))

    error = None
    if event_type not in event_types(conn):
        error = "Choose an event type."
    elif not contact_name or not contact_email:
        error = "Name and email are required."
    elif not EMAIL_RE.match(contact_email):
        error = "Enter a valid email address."
    # preferred_date/alternate_date used to be stored as whatever string was
    # posted — never parsed. A junk value then sat in a date column that the
    # financial and calendar queries compare with `>= ? AND < ?`, where it
    # silently matches nothing instead of erroring.
    for label, raw in (("preferred", preferred_date), ("alternate", alternate_date)):
        if error or not raw:
            continue
        parsed = parse_date(raw)
        if not parsed:
            error = f"Enter a valid {label} date, or leave it blank."
        elif parsed < datetime.now(timezone.utc).date():
            error = f"The {label} date has already passed."
    if error:
        conn.commit()  # persist the rate-limit log entry even on a validation error
        conn.close()
        flash(error, "error")
        return redirect(url_for("events_info"))

    guest_count = int(guest_count_raw) if guest_count_raw.isdigit() else None
    reference_code = make_event_reference_code()
    manage_token = secrets.token_urlsafe(24)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name, contact_email,
           contact_phone, preferred_date, alternate_date, guest_count, message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (reference_code, manage_token, event_type, contact_name, contact_email, contact_phone or None,
         preferred_date or None, alternate_date or None, guest_count, message or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    inquiry = conn.execute("SELECT * FROM event_inquiries WHERE manage_token = ?", (manage_token,)).fetchone()
    send_event_email(conn, inquiry, "event_inquiry_received", event_email_context(inquiry))

    owner_to = owner_email(conn)
    if owner_to:
        send_email(
            owner_to, f"New event inquiry — {event_type} ({reference_code})",
            f"{contact_name} ({contact_email}) inquired about hosting a {event_type}"
            f"{f', preferred date {preferred_date}' if preferred_date else ''}"
            f"{f', {guest_count} guests' if guest_count else ''}.\n\n{message or ''}\n\n"
            f"Review: {url_for('admin_events', _external=True)}",
        )
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    if owner_row:
        send_notification(
            conn, owner_row["id"], "event_inquiry", f"Event inquiry — {contact_name} ({event_type})",
            body=message, link="/admin/events",
        )
    conn.close()
    return redirect(url_for("event_confirmation", manage_token=manage_token))


@app.route("/events/confirmation/<manage_token>")
def event_confirmation(manage_token):
    conn = get_db()
    inquiry = conn.execute("SELECT * FROM event_inquiries WHERE manage_token = ?", (manage_token,)).fetchone()
    conn.close()
    if not inquiry:
        abort(404)
    return render_template("event_confirmation.html", inquiry=inquiry)


@app.route("/events/find", methods=["GET", "POST"])
def event_find():
    if request.method == "POST":
        conn = get_db()
        if rate_limited(conn, "event_find", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("event_find.html")
        conn.commit()
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        inquiry = conn.execute(
            "SELECT manage_token FROM event_inquiries WHERE reference_code = ? AND contact_email = ?",
            (reference_code, email),
        ).fetchone()
        conn.close()
        if inquiry:
            return redirect(url_for("event_manage", manage_token=inquiry["manage_token"]))
        flash("No inquiry found with that reference and email.", "error")
    return render_template("event_find.html")


@app.route("/events/manage/<manage_token>", methods=["GET", "POST"])
def event_manage(manage_token):
    conn = get_db()
    inquiry = conn.execute("SELECT * FROM event_inquiries WHERE manage_token = ?", (manage_token,)).fetchone()
    if not inquiry:
        conn.close()
        abort(404)

    if request.method == "POST" and request.form.get("action") == "cancel":
        cur = conn.execute(
            "UPDATE event_inquiries SET status = 'cancelled', decided_at = ? WHERE id = ? AND status NOT IN ('cancelled', 'declined')",
            (datetime.now(timezone.utc).isoformat(), inquiry["id"]),
        )
        conn.commit()
        if cur.rowcount:
            owner_to = owner_email(conn)
            if owner_to:
                send_email(
                    owner_to, f"Event inquiry cancelled — {inquiry['reference_code']}",
                    f"{inquiry['contact_name']} cancelled their {inquiry['event_type']} inquiry.",
                )
            flash("Your inquiry has been cancelled.", "success")
        conn.close()
        return redirect(url_for("event_manage", manage_token=manage_token))

    conn.close()
    return render_template("event_manage.html", inquiry=inquiry)


@app.route("/admin/events")
@owner_required
def admin_events():
    conn = get_db()
    status_filter = request.args.get("status", "")
    query = "SELECT * FROM event_inquiries"
    params = []
    if status_filter:
        query += " WHERE status = ?"
        params.append(status_filter)
    query += " ORDER BY (status = 'new') DESC, created_at DESC"
    inquiries = conn.execute(query, params).fetchall()
    new_count = conn.execute("SELECT COUNT(*) AS c FROM event_inquiries WHERE status = 'new'").fetchone()["c"]
    confirmed_count = conn.execute("SELECT COUNT(*) AS c FROM event_inquiries WHERE status = 'confirmed'").fetchone()["c"]
    types = event_types(conn)
    conn.close()

    # Split by whether the event has actually happened yet. Previously one
    # flat list mixed a wedding from two years ago in with next month's, and
    # because it sorted on created_at the oldest history could sit above the
    # work still to do. An enquiry with no date yet is still live work, so it
    # groups with upcoming rather than being treated as past.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    upcoming, past = [], []
    for r in inquiries:
        # A three-day wedding is still upcoming on day two, so judge by the
        # end date where one is set.
        day = r["end_date"] or r["preferred_date"] or ""
        (past if (day and day < today_iso) else upcoming).append(r)
    upcoming.sort(key=lambda r: (r["status"] != "new", r["preferred_date"] or "9999-99-99"))
    past.sort(key=lambda r: r["preferred_date"] or "", reverse=True)

    return render_template(
        "admin_events.html", inquiries=inquiries, upcoming=upcoming, past=past,
        status_filter=status_filter, today=datetime.now(timezone.utc).date(),
        new_count=new_count, confirmed_count=confirmed_count, event_types=types,
    )


@app.route("/admin/events/types", methods=["POST"])
@owner_required
def update_event_types():
    """Owner-editable event types. One per line, free text — the château runs
    things nobody thought to hardcode. Types already used by existing events
    are never invalidated (see known_event_types)."""
    raw = request.form.get("event_types", "")
    cleaned, seen = [], set()
    for line in raw.splitlines():
        t = line.strip().lower()[:40]
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    if not cleaned:
        flash("Keep at least one event type.", "error")
        return redirect(url_for("admin_events"))
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('event_types', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("\n".join(cleaned),),
        )
        conn.commit()
    finally:
        conn.close()
    flash(f"Event types updated ({len(cleaned)}).", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/new", methods=["POST"])
@owner_required
def new_event_inquiry():
    """Add an event the owner took by phone, email or in person.

    Until now the only INSERT into event_inquiries was the public /events
    form, so an enquiry that arrived any other way could only be recorded by
    filling in the guest-facing form pretending to be the guest. Most venue
    enquiries arrive by phone.
    """
    event_type = request.form.get("event_type", "").strip()
    contact_name = request.form.get("contact_name", "").strip()
    contact_email = request.form.get("contact_email", "").strip().lower()
    contact_phone = request.form.get("contact_phone", "").strip()
    preferred_date = request.form.get("preferred_date", "").strip()
    alternate_date = request.form.get("alternate_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    spaces = request.form.get("spaces", "").strip()[:300]
    guest_count_raw = request.form.get("guest_count", "").strip()
    message = request.form.get("message", "").strip()[:2000]
    owner_note = request.form.get("owner_note", "").strip()[:2000]
    status = request.form.get("status", "new").strip()
    quoted_price_raw = request.form.get("quoted_price", "").strip()

    conn = get_db()
    valid_types = known_event_types(conn)
    conn.close()
    if event_type not in valid_types:
        flash("Choose an event type.", "error")
        return redirect(url_for("admin_events"))
    if not contact_name:
        flash("A contact name is required.", "error")
        return redirect(url_for("admin_events"))
    # Email is optional here (a phone enquiry may not have one) but must be
    # valid if given, since the guest-facing emails key off it.
    if contact_email and not EMAIL_RE.match(contact_email):
        flash("That email address doesn't look right.", "error")
        return redirect(url_for("admin_events"))
    if status not in ("new", "contacted", "quoted", "confirmed", "declined", "cancelled"):
        status = "new"

    try:
        quoted_price = float(quoted_price_raw) if quoted_price_raw else None
    except ValueError:
        quoted_price = None

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, contact_phone, preferred_date, alternate_date, end_date, spaces,
               guest_count, message, status, quoted_price, owner_note, created_at, decided_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            # contact_email is NOT NULL, so a phone enquiry stores an empty
            # string rather than NULL. Deliberately not a placeholder address:
            # a fake email would pollute guest history and could be mailed.
            (make_event_reference_code(), secrets.token_urlsafe(24), event_type, contact_name,
             contact_email, contact_phone or None, preferred_date or None,
             alternate_date or None, end_date or None, spaces or None,
             int(guest_count_raw) if guest_count_raw.isdigit() else None,
             message or None, status, quoted_price, owner_note or None, now,
             now if status in ("confirmed", "declined") else None),
        )
        conn.commit()
    finally:
        # Without this, an insert that raises leaves the connection open and
        # the next write fails with "database is locked".
        conn.close()
    flash(f"{contact_name}'s {event_type} added.", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/<int:inquiry_id>/update", methods=["POST"])
@owner_required
def update_event_inquiry(inquiry_id):
    status = request.form.get("status", "").strip()
    quoted_price_raw = request.form.get("quoted_price", "").strip()
    owner_note = request.form.get("owner_note", "").strip()
    valid_statuses = ("new", "contacted", "quoted", "confirmed", "declined", "cancelled")
    if status not in valid_statuses:
        abort(400)

    conn = get_db()
    inquiry = conn.execute("SELECT * FROM event_inquiries WHERE id = ?", (inquiry_id,)).fetchone()
    if not inquiry:
        conn.close()
        abort(404)

    try:
        quoted_price = float(quoted_price_raw) if quoted_price_raw else inquiry["quoted_price"]
    except ValueError:
        quoted_price = inquiry["quoted_price"]

    status_changed_to_decided = status in ("confirmed", "declined") and inquiry["status"] != status
    decided_at = datetime.now(timezone.utc).isoformat() if status_changed_to_decided else inquiry["decided_at"]

    conn.execute(
        "UPDATE event_inquiries SET status = ?, quoted_price = ?, owner_note = ?, decided_at = ? WHERE id = ?",
        (status, quoted_price, owner_note or None, decided_at, inquiry_id),
    )
    log_audit(conn, "event_inquiry_updated", target=inquiry["reference_code"], details=status)
    conn.commit()

    inquiry = conn.execute("SELECT * FROM event_inquiries WHERE id = ?", (inquiry_id,)).fetchone()
    if status_changed_to_decided:
        if status == "confirmed":
            send_event_email(conn, inquiry, "event_inquiry_confirmed", event_email_context(inquiry))
        elif status == "declined":
            send_event_email(conn, inquiry, "event_inquiry_declined", event_email_context(inquiry))

    # Confirming holds this date the same way a workshop session does (see
    # is_range_available's event-blocking clause) — but nothing checked
    # the REVERSE direction until now: whether a room booking, workshop
    # session, or another confirmed event already sits on this date. Flag
    # it (don't block — the owner might already know, or be moving things
    # around) the same way new_workshop_session warns on its own overlaps.
    if status == "confirmed" and inquiry["preferred_date"]:
        event_date = inquiry["preferred_date"]
        clashing_bookings = conn.execute(
            """SELECT COUNT(*) AS c FROM bookings WHERE status IN ('pending','confirmed')
               AND arrival_date <= ? AND departure_date > ?""",
            (event_date, event_date),
        ).fetchone()["c"]
        clashing_sessions = conn.execute(
            "SELECT COUNT(*) AS c FROM workshop_sessions WHERE start_date <= ? AND end_date >= ?",
            (event_date, event_date),
        ).fetchone()["c"]
        clashing_events = conn.execute(
            "SELECT COUNT(*) AS c FROM event_inquiries WHERE status = 'confirmed' AND preferred_date = ? AND id != ?",
            (event_date, inquiry_id),
        ).fetchone()["c"]
        parts = []
        if clashing_bookings:
            parts.append(f"{clashing_bookings} room booking(s)")
        if clashing_sessions:
            parts.append(f"{clashing_sessions} workshop session(s)")
        if clashing_events:
            parts.append(f"{clashing_events} other confirmed event(s)")
        if parts:
            flash(f"Inquiry updated — heads up, {' and '.join(parts)} overlap {format_date_human(event_date)}.", "error")
            conn.close()
            return redirect(url_for("admin_events"))

    conn.close()
    flash("Inquiry updated.", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/export.csv")
@owner_required
def export_events_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM event_inquiries ORDER BY created_at DESC").fetchall()
    conn.close()
    fieldnames = ["reference_code", "event_type", "contact_name", "contact_email", "contact_phone",
                  "preferred_date", "alternate_date", "guest_count", "status", "quoted_price",
                  "owner_note", "created_at"]
    return csv_response(fieldnames, rows, "event_inquiries.csv")


# ---------------------------------------------------------------------------
# Restaurant — a small, separate booking engine for the dinner service.
# Mirrors the room-booking engine's reference-code + manage-token pattern so
# guests get the same self-service experience, but capacity is a single
# nightly headcount cap rather than per-room date ranges.
# ---------------------------------------------------------------------------

def make_restaurant_reference_code():
    return "DIN-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def get_restaurant_settings(conn):
    return conn.execute("SELECT * FROM restaurant_settings WHERE id = 1").fetchone()


def restaurant_night_rate(conn, dinner_date_iso):
    """The per-person rate for one specific dinner date — a date-range
    override (NYE, a holiday tasting menu) if one covers that date,
    otherwise the flat restaurant_settings.price_per_person. Mirrors
    room_night_rate's override-first lookup, but both ends are inclusive
    here since a restaurant override describes calendar dates directly,
    not a checkin/checkout range."""
    override = conn.execute(
        """SELECT price_per_person FROM restaurant_rate_overrides
           WHERE start_date <= ? AND end_date >= ? ORDER BY id DESC LIMIT 1""",
        (dinner_date_iso, dinner_date_iso),
    ).fetchone()
    if override:
        return override["price_per_person"]
    settings = get_restaurant_settings(conn)
    return settings["price_per_person"] if settings else None


def compute_restaurant_total(conn, dinner_date_iso, party_size):
    """Single source of truth for a dinner reservation's subtotal — every
    code path (booking form, Stripe checkout, admin totals) should call
    this instead of price_per_person * party_size directly, so a seasonal
    override is honoured everywhere a price is shown or charged."""
    rate = restaurant_night_rate(conn, dinner_date_iso)
    return round(rate * party_size, 2) if rate else 0


def resolve_deposit_percent(conn, category, date_iso, party_size, default_percent):
    """The deposit percentage that actually applies to this booking —
    checks deposit_rules for the most specific match (a rule scoped to
    both a date range AND a minimum party size beats one scoped to just
    one of those, which beats a blanket rule) and falls back to
    default_percent (the flat per-category setting) when nothing matches.
    Mirrors the room/restaurant rate-override pattern, but a deposit rule
    can vary by party size as well as date — a large group needing a
    bigger deposit than a couple is exactly what a single flat percentage
    can't express, and is a routine feature in older booking software."""
    best, best_score = None, -1
    for rule in conn.execute("SELECT * FROM deposit_rules WHERE category = ?", (category,)).fetchall():
        if rule["start_date"] and date_iso < rule["start_date"]:
            continue
        if rule["end_date"] and date_iso > rule["end_date"]:
            continue
        if rule["min_party_size"] and party_size < rule["min_party_size"]:
            continue
        score = (2 if (rule["start_date"] or rule["end_date"]) else 0) + (1 if rule["min_party_size"] else 0)
        if score > best_score:
            best_score, best = score, rule
    return best["deposit_percent"] if best else (default_percent or 0)


def compute_restaurant_deposit(total_price, deposit_percent):
    """(deposit_amount, balance_due_onsite). No deposit_percent configured
    means today's existing behaviour — the full amount is charged online
    (or nothing, if Stripe isn't in play) and nothing is left to collect
    in person."""
    if not deposit_percent or not total_price:
        return total_price, 0.0
    deposit = round(total_price * deposit_percent / 100, 2)
    return deposit, round(total_price - deposit, 2)


def restaurant_remaining_capacity(conn, dinner_date, exclude_id=None):
    settings = get_restaurant_settings(conn)
    capacity = settings["capacity"] if settings else 20
    query = """SELECT COALESCE(SUM(party_size), 0) AS total FROM restaurant_bookings
               WHERE dinner_date = ? AND status IN ('pending', 'confirmed')"""
    params = [dinner_date]
    if exclude_id:
        query += " AND id != ?"
        params.append(exclude_id)
    used = conn.execute(query, params).fetchone()["total"]
    return capacity - used


def restaurant_email_context(booking, refund_note=""):
    """Common merge-tag values available to every restaurant email template.
    Expects a restaurant_bookings row (or anything with the same columns)."""
    price_lines = []
    if booking["total_price"]:
        paid_suffix = " (paid)" if booking["payment_status"] == "paid" else ""
        price_lines.append(f"Estimated total: €{booking['total_price']:.2f}{paid_suffix}")
        if booking["deposit_amount"] and booking["deposit_amount"] < booking["total_price"]:
            price_lines.append(f"Deposit paid: €{booking['deposit_amount']:.2f}")
            price_lines.append(f"Balance due at the restaurant: €{booking['total_price'] - booking['deposit_amount']:.2f}")
    return {
        "guest_name": booking["guest_name"],
        "dinner_date": format_date_human(booking["dinner_date"]),
        "party_size": booking["party_size"],
        "reference_code": booking["reference_code"],
        "dietary_line": f"\nDietary notes: {booking['dietary_notes']}" if booking["dietary_notes"] else "",
        "price_block": ("\n" + "\n".join(price_lines)) if price_lines else "",
        "refund_note": refund_note,
        "manage_url": url_for("restaurant_manage", manage_token=booking["manage_token"], _external=True),
    }


def send_restaurant_email(conn, booking, template_key, context):
    subject, body = render_email_template(conn, template_key, context)
    if not subject:
        return
    send_email(booking["guest_email"], subject, body)


def refund_restaurant_booking(conn, booking, reason="Reservation cancelled by the château", user_id=None):
    """Automatic full refund when the château itself declines or cancels a
    dinner -- we called it off, so the deposit goes back.

    Goes through issue_refund() so it lands in the `refunds` record like every
    other refund. It previously called Stripe directly and wrote nothing, which
    meant the admin page still offered the whole deposit as refundable
    afterwards: a second, manual refund would pay the guest twice, and none of
    it appeared in the refunds log or the accountant's export.
    """
    if booking["payment_status"] != "paid":
        return False, "This reservation was never marked paid."
    amount = refundable_amount(conn, "restaurant", booking)
    if amount <= 0:
        return False, "There's nothing left to refund on this reservation."
    return issue_refund(conn, "restaurant", booking, amount, reason,
                        method="stripe", user_id=user_id)


def create_restaurant_booking(conn, guest_name, guest_email, guest_phone, dinner_date, party_size,
                               dietary_notes, booking_id=None, payment_status="unpaid",
                               stripe_session_id=None, stripe_payment_intent_id=None, promo_code=None,
                               deposit_amount=None, total_price_override=None, discount_amount_override=None):
    settings = get_restaurant_settings(conn)
    reference_code = make_restaurant_reference_code()
    manage_token = secrets.token_urlsafe(24)

    if total_price_override is not None:
        # Called from the Stripe webhook path: trust the price actually
        # quoted (and charged a deposit against) at checkout-creation time,
        # passed through via metadata, rather than recomputing subtotal
        # and re-validating the promo code fresh here. Recomputing would
        # let pricing drift between checkout creation and the webhook
        # firing (a seasonal rate changing, or the promo hitting its
        # redemption cap from a different booking in between) silently
        # change the stored total_price/discount_amount away from what
        # the guest actually agreed to and was charged a deposit against.
        total_price = total_price_override
        discount_amount = discount_amount_override or 0.0
        subtotal = round(total_price + discount_amount, 2)
        promo = find_promo_code(conn, promo_code) if promo_code else None
    else:
        subtotal = compute_restaurant_total(conn, dinner_date, party_size)
        promo, discount_amount = None, 0.0
        if promo_code and subtotal:
            promo, discount_amount, _ = validate_promo_code(conn, promo_code, "restaurant", subtotal)
        total_price = round(subtotal - discount_amount, 2) if subtotal else None
    deposit_amount = round(deposit_amount, 2) if deposit_amount else None
    conn.execute(
        """INSERT INTO restaurant_bookings
           (reference_code, manage_token, guest_name, guest_email, guest_phone, party_size,
            dinner_date, dietary_notes, total_price, booking_id, payment_status,
            stripe_session_id, stripe_payment_intent_id, created_at, promo_code_id, discount_amount, deposit_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (reference_code, manage_token, guest_name, guest_email, guest_phone, party_size,
         dinner_date, dietary_notes or None, total_price, booking_id, payment_status,
         stripe_session_id, stripe_payment_intent_id, datetime.now(timezone.utc).isoformat(),
         promo["id"] if promo else None, discount_amount or None, deposit_amount),
    )
    # Same transaction as the booking insert -- see the room-booking path
    # for why this must not be a separate commit.
    if promo:
        record_promo_redemption(conn, promo, "restaurant", reference_code, guest_email, subtotal, discount_amount)
    conn.commit()

    booking_row = conn.execute("SELECT * FROM restaurant_bookings WHERE manage_token = ?", (manage_token,)).fetchone()
    send_restaurant_email(conn, booking_row, "restaurant_reservation_received", restaurant_email_context(booking_row))

    notify_title = f"Dinner reservation — {guest_name}, party of {party_size} ({format_date_human(dinner_date)})"
    notified_ids = set()
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    if owner_row:
        send_notification(conn, owner_row["id"], "restaurant_booking", notify_title, body=dietary_notes, link="/admin/restaurant")
        notified_ids.add(owner_row["id"])
    if settings and settings["lead_user_id"] and settings["lead_user_id"] not in notified_ids:
        send_notification(conn, settings["lead_user_id"], "restaurant_booking", notify_title, body=dietary_notes, link="/admin/restaurant")

    return reference_code, manage_token


def create_restaurant_booking_from_stripe_session(conn, session):
    """Rebuilds a dinner reservation from a completed Stripe Checkout
    Session's metadata — used by both the success redirect and the webhook,
    whichever fires first. Mirrors create_booking_from_stripe_session's
    pay-then-create shape for rooms."""
    meta = smeta(session)
    dinner_date = meta["dinner_date"]
    party_size = int(meta["party_size"])
    remaining = restaurant_remaining_capacity(conn, dinner_date)
    reference_code, manage_token = create_restaurant_booking(
        conn, meta["guest_name"], meta["guest_email"], meta.get("guest_phone") or None,
        dinner_date, party_size, meta.get("dietary_notes", ""),
        payment_status="paid", stripe_session_id=session["id"],
        stripe_payment_intent_id=sval(session, "payment_intent"),
        total_price_override=float(meta["total_price"]) if meta.get("total_price") else None,
        discount_amount_override=float(meta["discount_amount"]) if meta.get("discount_amount") else None,
        promo_code=meta.get("promo_code") or None,
        # The real amount Stripe actually charged, straight from the session
        # — more trustworthy than recomputing from deposit_percent at
        # webhook time, since that setting could have changed in between.
        deposit_amount=(session["amount_total"] / 100) if sval(session, "amount_total") else None,
    )
    if remaining < party_size:
        # Money has already changed hands, so the reservation is still
        # recorded — but the night may now be overbooked (another
        # reservation was confirmed during the gap between checkout
        # starting and payment completing). Flag it for manual review
        # rather than silently overbooking the dining room.
        log_audit(conn, "stripe_restaurant_booking_capacity_conflict", target=reference_code)
        conn.commit()
        send_email(
            owner_email(conn),
            f"URGENT: paid dinner reservation over capacity — {reference_code}",
            f"A paid Stripe dinner reservation was just created for {format_date_human(dinner_date)} "
            f"that pushes that night over capacity.\n\n"
            f"Guest: {meta['guest_name']} ({meta['guest_email']}), party of {party_size}\n"
            f"This needs manual review — contact the guest, move another reservation, or issue a refund.",
        )
    return manage_token


@app.route("/restaurant")
def restaurant_info():
    conn = get_db()
    settings = get_restaurant_settings(conn)
    items = conn.execute("SELECT * FROM menu_items WHERE active = 1 ORDER BY category, sort_order, name").fetchall()
    conn.close()
    items_by_category = {}
    for item in items:
        items_by_category.setdefault(item["category"], []).append(item)
    opening_date = parse_date(settings["opening_date"]) if settings and settings["opening_date"] else None
    not_yet_open = bool(opening_date and opening_date > datetime.now(timezone.utc).date())
    return render_template(
        "restaurant_info.html", settings=settings, not_yet_open=not_yet_open,
        items_by_category=items_by_category, menu_categories=MENU_CATEGORIES,
    )


@app.route("/restaurant/book", methods=["GET", "POST"])
def restaurant_book():
    conn = get_db()
    settings = get_restaurant_settings(conn)
    if not settings or not settings["enabled"]:
        conn.close()
        abort(404)
    min_date = parse_date(settings["opening_date"]) if settings["opening_date"] else datetime.now(timezone.utc).date()

    if request.method == "POST":
        if rate_limited(conn, "book_restaurant", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("restaurant_book.html", settings=settings, min_date=min_date, prefill_name="", prefill_email="", prefill_phone="", prefill_party_size="", prefill_date="", fully_booked=False, stripe_enabled=stripe_enabled())

        guest_name = request.form.get("guest_name", "").strip()
        guest_email = request.form.get("guest_email", "").strip().lower()
        guest_phone = request.form.get("guest_phone", "").strip()
        dinner_date_raw = request.form.get("dinner_date", "").strip()
        dinner_date = parse_date(dinner_date_raw)
        dietary_notes = request.form.get("dietary_notes", "").strip()[:500]
        party_size_raw = request.form.get("party_size", "").strip()
        party_size = int(party_size_raw) if party_size_raw.isdigit() else None
        promo_code = request.form.get("promo_code", "").strip()

        error = None
        fully_booked = False
        if not guest_name or not guest_email:
            error = "Name and email are required."
        elif not EMAIL_RE.match(guest_email):
            error = "Enter a valid email address."
        elif not dinner_date:
            error = "Choose a valid date."
        elif dinner_date < min_date:
            error = f"We're not taking reservations before {format_date_human(min_date.isoformat())}."
        elif not party_size or party_size < 1:
            error = "Party size is required."
        elif party_size > settings["capacity"]:
            error = f"We can seat a maximum of {settings['capacity']} at once."
        elif restaurant_remaining_capacity(conn, dinner_date.isoformat()) < party_size:
            error = "That date is fully booked — join the waitlist below, or try another date."
            fully_booked = True

        if error:
            flash(error, "error")
            conn.commit()  # persist the rate-limit log entry even on a validation error
            conn.close()
            return render_template(
                "restaurant_book.html", settings=settings, min_date=min_date,
                prefill_name=guest_name, prefill_email=guest_email, prefill_phone=guest_phone,
                prefill_party_size=party_size_raw, prefill_date=dinner_date_raw, fully_booked=fully_booked,
                stripe_enabled=stripe_enabled(),
            )

        subtotal = compute_restaurant_total(conn, dinner_date.isoformat(), party_size)
        discount_amount = 0.0
        if promo_code and subtotal:
            promo, discount_amount, promo_error = validate_promo_code(conn, promo_code, "restaurant", subtotal)
            if not promo:
                flash(f"Promo code not applied: {promo_error}", "error")
        total_price = round(subtotal - discount_amount, 2)
        deposit_percent = resolve_deposit_percent(conn, "restaurant", dinner_date.isoformat(), party_size, settings["deposit_percent"])
        charge_amount, balance_due_onsite = compute_restaurant_deposit(total_price, deposit_percent)
        if stripe_enabled() and charge_amount > 0:
            line_name = f"Dinner reservation for {party_size} — {format_date_human(dinner_date.isoformat())}"
            if balance_due_onsite:
                line_name += f" — deposit (€{balance_due_onsite:.2f} due at the restaurant)"
            if discount_amount:
                line_name += f" (promo code applied, -€{discount_amount:.2f})"
            try:
                checkout_session = stripe.checkout.Session.create(
                    mode="payment",
                    payment_method_types=["card"],
                    line_items=[{
                        "price_data": {
                            "currency": "eur",
                            "product_data": {"name": line_name},
                            # One pre-multiplied line item (qty 1) rather than
                            # unit_amount=price_per_person, quantity=party_size —
                            # that per-person shape leaves no clean way to apply
                            # a promo-code discount without dividing it unevenly
                            # across guests. charge_amount is the full total
                            # unless a deposit_percent is configured, in which
                            # case it's just the deposit portion.
                            "unit_amount": int(round(charge_amount * 100)),
                        },
                        "quantity": 1,
                    }],
                    customer_email=guest_email,
                    success_url=url_for("restaurant_stripe_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=url_for("restaurant_stripe_cancel", _external=True),
                    metadata={
                        "kind": "restaurant",
                        "guest_name": guest_name,
                        "guest_email": guest_email,
                        "guest_phone": guest_phone,
                        "dinner_date": dinner_date.isoformat(),
                        "party_size": str(party_size),
                        "dietary_notes": dietary_notes[:490],
                        "promo_code": promo_code if discount_amount else "",
                        # So the webhook can trust the price actually quoted
                        # here rather than recomputing it fresh later, which
                        # could drift if pricing/promo state changes before
                        # the webhook fires — see create_restaurant_booking.
                        "total_price": str(total_price),
                        "discount_amount": str(discount_amount),
                    },
                )
            except Exception as e:
                flash(f"Payment setup failed ({e}). Please try again.", "error")
                conn.commit()  # persist the rate-limit log entry even when Stripe setup fails
                conn.close()
                return render_template(
                    "restaurant_book.html", settings=settings, min_date=min_date,
                    prefill_name=guest_name, prefill_email=guest_email, prefill_phone=guest_phone,
                    prefill_party_size=party_size_raw, prefill_date=dinner_date_raw, fully_booked=False,
                    stripe_enabled=stripe_enabled(),
                )
            conn.commit()
            conn.close()
            return redirect(checkout_session.url, code=303)

        reference_code, manage_token = create_restaurant_booking(
            conn, guest_name, guest_email, guest_phone or None, dinner_date.isoformat(), party_size, dietary_notes,
            promo_code=promo_code or None,
        )
        conn.close()
        return redirect(url_for("restaurant_confirmation", manage_token=manage_token))

    conn.close()
    return render_template(
        "restaurant_book.html", settings=settings, min_date=min_date,
        prefill_name=request.args.get("name", ""), prefill_email=request.args.get("email", ""),
        prefill_phone=request.args.get("phone", ""), prefill_party_size=request.args.get("party_size", ""),
        prefill_date=request.args.get("date", ""), fully_booked=False, stripe_enabled=stripe_enabled(),
    )


@app.route("/restaurant/confirmation/<manage_token>")
def restaurant_confirmation(manage_token):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE manage_token = ?", (manage_token,)).fetchone()
    conn.close()
    if not booking:
        abort(404)
    return render_template("restaurant_confirmation.html", booking=booking)


@app.route("/restaurant/stripe-success")
def restaurant_stripe_success():
    session_id = request.args.get("session_id", "").strip()
    if not stripe_enabled() or not session_id:
        abort(404)

    conn = get_db()
    existing = conn.execute(
        "SELECT manage_token FROM restaurant_bookings WHERE stripe_session_id = ?", (session_id,)
    ).fetchone()
    if existing:
        conn.close()
        return redirect(url_for("restaurant_manage", manage_token=existing["manage_token"]))

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        conn.close()
        abort(404)

    if sval(session, "payment_status") != "paid":
        conn.close()
        flash("That payment wasn't completed, so no reservation was made.", "error")
        return redirect(url_for("restaurant_book"))

    try:
        manage_token = create_restaurant_booking_from_stripe_session(conn, session)
    except sqlite3.IntegrityError:
        # Webhook won the race; show the guest the reservation it created.
        row = conn.execute(
            "SELECT manage_token FROM restaurant_bookings WHERE stripe_session_id = ?", (session_id,)
        ).fetchone()
        manage_token = row["manage_token"] if row else None
    conn.close()
    if not manage_token:
        flash("Payment went through, but we couldn't create the reservation automatically — contact the château directly with your payment reference.", "error")
        return redirect(url_for("restaurant_book"))
    return redirect(url_for("restaurant_confirmation", manage_token=manage_token))


@app.route("/restaurant/stripe-cancel")
def restaurant_stripe_cancel():
    flash("Payment was cancelled — no reservation was made.", "error")
    return redirect(url_for("restaurant_book"))


@app.route("/restaurant/find", methods=["GET", "POST"])
def restaurant_find():
    if request.method == "POST":
        conn = get_db()
        if rate_limited(conn, "restaurant_find", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("restaurant_find.html")
        conn.commit()
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        booking = conn.execute(
            "SELECT manage_token FROM restaurant_bookings WHERE reference_code = ? AND guest_email = ?",
            (reference_code, email),
        ).fetchone()
        conn.close()
        if booking:
            return redirect(url_for("restaurant_manage", manage_token=booking["manage_token"]))
        flash("No reservation found with that reference and email.", "error")
    return render_template("restaurant_find.html")


@app.route("/restaurant/manage/<manage_token>", methods=["GET", "POST"])
def restaurant_manage(manage_token):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE manage_token = ?", (manage_token,)).fetchone()
    if not booking:
        conn.close()
        abort(404)

    if request.method == "POST" and request.form.get("action") == "cancel":
        cur = conn.execute(
            "UPDATE restaurant_bookings SET status = 'cancelled', decided_at = ? WHERE id = ? AND status IN ('pending', 'confirmed')",
            (datetime.now(timezone.utc).isoformat(), booking["id"]),
        )
        conn.commit()
        if cur.rowcount:
            owner_to = owner_email(conn)
            if owner_to:
                paid_note = " They had paid — refund it from the restaurant admin page if appropriate." if booking["payment_status"] == "paid" else ""
                send_email(
                    owner_to,
                    f"Dinner reservation cancelled — {booking['reference_code']}",
                    f"{booking['guest_name']} cancelled their dinner reservation for "
                    f"{format_date_human(booking['dinner_date'])}, party of {booking['party_size']}.{paid_note}",
                )
            flash("Your reservation has been cancelled.", "success")
        conn.close()
        return redirect(url_for("restaurant_manage", manage_token=manage_token))

    conn.close()
    return render_template("restaurant_manage.html", booking=booking)


def make_workshop_reference_code():
    return "WRK-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def compute_workshop_payment_terms(total_price, deposit_percent, start_date):
    """A workshop registration is really a whole-château retreat package, so
    it follows the same deposit/balance convention as the real thing: a
    percentage deposit at booking, the remainder due 30 days before the stay
    — unless the booking itself happens inside that 30-day window, in which
    case the full amount is due immediately (mirrors 'full payment required
    for bookings within 30 days'). Returns (deposit_amount, balance_amount,
    balance_due_date_iso_or_None)."""
    if not total_price:
        return None, None, None
    today = datetime.now(timezone.utc).date()
    if (start_date - today).days < 30:
        return round(total_price, 2), 0.0, None
    deposit_amount = round(total_price * deposit_percent / 100, 2)
    balance_amount = round(total_price - deposit_amount, 2)
    balance_due_date = (start_date - timedelta(days=30)).isoformat()
    return deposit_amount, balance_amount, balance_due_date


def create_workshop_booking(conn, session_row, workshop, guest_name, guest_email, guest_phone, party_size,
                             notes, occupancy_type="double", requested_roommate=None, dietary_notes=None,
                             medical_notes=None, special_occasion=None, booking_id=None, promo_code=None):
    reference_code = make_workshop_reference_code()
    manage_token = secrets.token_urlsafe(24)
    subtotal = (workshop["price_per_person"] * party_size) if workshop["price_per_person"] else 0

    promo, discount_amount = None, 0.0
    if promo_code and subtotal:
        promo, discount_amount, _ = validate_promo_code(conn, promo_code, "workshop", subtotal)
    total_price = round(subtotal - discount_amount, 2) if subtotal else None
    # Deposit/balance are split from the already-discounted total, so the
    # discount flows through to every later Stripe checkout (deposit, then
    # balance) automatically — neither of those steps needs to know a promo
    # code was ever involved.
    deposit_percent = resolve_deposit_percent(conn, "workshop", session_row["start_date"], party_size, workshop["deposit_percent"])
    deposit_amount, balance_amount, balance_due_date = compute_workshop_payment_terms(
        total_price, deposit_percent, parse_date(session_row["start_date"])
    )
    conn.execute(
        """INSERT INTO workshop_bookings
           (session_id, reference_code, manage_token, guest_name, guest_email, guest_phone, party_size,
            notes, total_price, occupancy_type, requested_roommate, dietary_notes, medical_notes,
            special_occasion, deposit_amount, balance_amount, balance_due_date, booking_id, created_at,
            promo_code_id, discount_amount)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_row["id"], reference_code, manage_token, guest_name, guest_email, guest_phone or None,
         party_size, notes or None, total_price, occupancy_type, requested_roommate or None,
         dietary_notes or None, medical_notes or None, special_occasion or None,
         deposit_amount, balance_amount, balance_due_date, booking_id, datetime.now(timezone.utc).isoformat(),
         promo["id"] if promo else None, discount_amount or None),
    )
    booking_row_id = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?", (manage_token,)).fetchone()["id"]
    # Same transaction as the booking insert -- see the room-booking path
    # for why this must not be a separate commit.
    if promo:
        record_promo_redemption(conn, promo, "workshop", reference_code, guest_email, subtotal, discount_amount)
    conn.commit()

    date_line = format_date_human(session_row["start_date"])
    if session_row["end_date"] != session_row["start_date"]:
        date_line += f" to {format_date_human(session_row['end_date'])}"

    booking_row = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""",
        (booking_row_id,),
    ).fetchone()
    send_workshop_email(conn, booking_row, "workshop_registration_received", workshop_email_context(booking_row))
    conn.commit()

    notify_title = f"Workshop registration — {workshop['title']}, {guest_name} ({date_line})"
    notified_ids = set()
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    if owner_row:
        send_notification(conn, owner_row["id"], "workshop_booking", notify_title, body=notes, link="/admin/workshops/registrations")
        notified_ids.add(owner_row["id"])
    if workshop["instructor_user_id"] and workshop["instructor_user_id"] not in notified_ids:
        send_notification(conn, workshop["instructor_user_id"], "workshop_booking", notify_title, body=notes, link="/admin/workshops/registrations")

    return reference_code, manage_token, booking_row_id


def workshop_balance_due(conn, booking_id):
    """Running balance from the ledger, anchored on the booking's base
    total_price — charges/discounts adjust what's owed, payments/refunds
    adjust what's been paid. Returns (balance_due, total_charged, total_paid),
    all rounded to cents."""
    booking = conn.execute("SELECT total_price FROM workshop_bookings WHERE id = ?", (booking_id,)).fetchone()
    total = booking["total_price"] or 0
    paid = 0
    for row in conn.execute(
        "SELECT kind, amount FROM workshop_transactions WHERE workshop_booking_id = ?", (booking_id,)
    ).fetchall():
        if row["kind"] == "charge":
            total += row["amount"]
        elif row["kind"] == "discount":
            total -= row["amount"]
        elif row["kind"] == "payment":
            paid += row["amount"]
        elif row["kind"] == "refund":
            paid -= row["amount"]
    return round(total - paid, 2), round(total, 2), round(paid, 2)


def add_workshop_transaction(conn, booking_id, kind, description, amount, method=None, user_id=None):
    conn.execute(
        """INSERT INTO workshop_transactions
           (workshop_booking_id, kind, description, amount, method, created_by_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (booking_id, kind, description, amount, method, user_id, datetime.now(timezone.utc).isoformat()),
    )


def render_email_template(conn, template_key, context):
    """Merge-tag substitution against an admin-editable template. Falls back
    to the raw template text (tags left unreplaced) if the context is
    missing a key rather than crashing an email send over a typo."""
    row = conn.execute("SELECT subject, body FROM email_templates WHERE template_key = ?", (template_key,)).fetchone()
    if not row:
        return None, None
    try:
        subject = row["subject"].format(**context)
    except (KeyError, IndexError):
        subject = row["subject"]
    try:
        body = row["body"].format(**context)
    except (KeyError, IndexError):
        body = row["body"]
    return subject, body


# The fields a campaign template can use. Kept to things we can reliably fill
# for any recipient — a merge tag that silently comes out blank is worse than
# not offering it.
CAMPAIGN_MERGE_FIELDS = {
    "first_name": "The guest's first name",
    "full_name": "Their full name",
    "email": "Their email address",
    "chateau": "Château de Gudanes",
}


def render_campaign(subject, body, context):
    """Fill {{merge_tags}} in a campaign template.

    Uses {{double braces}} rather than Python's {single} formatting because
    campaign copy is full of ordinary punctuation and prose — a stray brace or
    a price like {50} shouldn't blow up a send to two hundred people. Unknown
    tags are left visible rather than silently emptied, so a typo is obvious
    in the preview instead of arriving as a blank in someone's inbox.
    """
    def fill(text):
        if not text:
            return ""
        def sub(match):
            key = match.group(1).strip()
            value = context.get(key)
            return str(value) if value not in (None, "") else match.group(0)
        return re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", sub, text)
    return fill(subject), fill(body)


def campaign_context_for(name, email):
    full = (name or "").strip()
    return {
        "first_name": full.split()[0] if full else "there",
        "full_name": full or email,
        "email": email,
        "chateau": "Château de Gudanes",
    }


def campaign_audience(conn, segments, since_date_iso=None, include_optouts=False):
    """Who a campaign would go to: {email: name}, de-duplicated.

    Builds on the existing segment logic, then removes anyone who has opted
    out. A guest with a stay AND a dinner is emailed once, not twice.
    """
    recipients = dict(promo_blast_recipients(conn, segments, since_date_iso))
    if "profiles" in segments:
        for row in conn.execute(
            "SELECT email, name FROM guests WHERE email IS NOT NULL AND TRIM(email) != ''"
        ).fetchall():
            recipients.setdefault(row["email"], row["name"])

    recipients = {(e or "").strip().lower(): n for e, n in recipients.items() if e and e.strip()}
    if not include_optouts:
        opted_out = {r["email"] for r in conn.execute("SELECT email FROM email_optouts").fetchall()}
        recipients = {e: n for e, n in recipients.items() if e not in opted_out}
    return recipients


def campaign_unsubscribe_footer(conn, token):
    """The unsubscribe line appended to every campaign message.

    Marketing mail to people in the EU has to offer a working way out and to
    identify the sender by postal address; a bulk send without either also
    lands in spam folders. Only campaign and automated mail gets this — a
    guest's own booking confirmation is transactional and must keep arriving
    whatever their marketing preference.

    The address comes from Company Info rather than a constant, so it is the
    one the owner actually maintains; it is simply omitted while unset rather
    than shipping a placeholder to guests.
    """
    try:
        link = url_for("campaign_unsubscribe", token=token, _external=True)
    except RuntimeError:
        # No request context — the trigger job runs from the scheduler.
        link = f"{PUBLIC_BASE_URL or ''}/unsubscribe/{token}"
    row = conn.execute(
        "SELECT legal_name, registered_address FROM company_info WHERE id = 1"
    ).fetchone()
    who = "Château de Gudanes"
    if row and (row["registered_address"] or "").strip():
        who += " · " + " ".join((row["registered_address"] or "").split())
    return (
        "\n\n—\n"
        f"{who}\n"
        f"Prefer not to receive these? Unsubscribe: {link}\n"
        "This only affects announcements and offers — you'll still get emails "
        "about your own bookings."
    )


def send_campaign(conn, template, recipients, user_id, dedupe_key=None, as_test=False):
    """Send one campaign to a set of recipients, logging every one.

    `dedupe_key` makes an automated send idempotent — the same template for the
    same guest and the same trigger date won't go twice however often the job
    runs. Failures are recorded rather than raised so one bad address doesn't
    abandon the rest of the send half-way through.

    `as_test` still really sends (a test email that doesn't arrive proves
    nothing) but logs the row as a test, so trying a template on yourself
    doesn't show up in the send history as if guests had been mailed.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    sent = failed = skipped = 0
    for email, name in recipients.items():
        key = f"{dedupe_key}:{email}" if dedupe_key else None
        if key and conn.execute(
            "SELECT 1 FROM campaign_sends WHERE dedupe_key = ?", (key,)
        ).fetchone():
            skipped += 1
            continue
        subject, body = render_campaign(template["subject"], template["body"],
                                        campaign_context_for(name, email))
        # Every marketing message carries its own unsubscribe key. Written
        # BEFORE the send so the link in the email is always one that resolves —
        # a footer pointing at a row that doesn't exist yet would 404 for anyone
        # quick off the mark.
        unsub_token = secrets.token_urlsafe(24)
        body = body + campaign_unsubscribe_footer(conn, unsub_token)
        try:
            ok = send_email(email, subject, body)
            status = ("test" if as_test else "sent") if ok is not False else "failed"
            detail = None if status != "failed" else "send_email reported a failure"
        except Exception as e:
            status, detail = "failed", str(e)[:200]
        conn.execute(
            """INSERT INTO campaign_sends (template_id, template_name, recipient_email,
               recipient_name, subject, status, detail, dedupe_key, sent_by_user_id,
               created_at, unsubscribe_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (template["id"], template["name"], email, name, subject, status, detail,
             key, user_id, now_iso, unsub_token),
        )
        if status in ("sent", "test"):
            sent += 1
        elif status == "failed":
            failed += 1
    conn.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped, "total": len(recipients)}


def run_campaign_triggers_job(conn):
    """Send any template set to fire relative to a guest's arrival, departure
    or workshop start. Idempotent via the dedupe key, so a guest gets each
    automated message exactly once no matter how often this runs."""
    today = datetime.now(timezone.utc).date()
    templates = conn.execute(
        """SELECT * FROM campaign_templates
           WHERE trigger_active = 1 AND trigger_event IS NOT NULL"""
    ).fetchall()
    if not templates:
        return "no active triggers"

    opted_out = {r["email"] for r in conn.execute("SELECT email FROM email_optouts").fetchall()}
    total = 0
    for t in templates:
        offset = t["trigger_offset_days"] or 0
        # Negative offset = before the event, positive = after.
        target = (today - timedelta(days=offset)).isoformat()
        if t["trigger_event"] == "arrival":
            rows = conn.execute(
                "SELECT guest_email AS e, guest_name AS n FROM bookings "
                "WHERE status='confirmed' AND arrival_date = ?", (target,)).fetchall()
        elif t["trigger_event"] == "departure":
            rows = conn.execute(
                "SELECT guest_email AS e, guest_name AS n FROM bookings "
                "WHERE status='confirmed' AND departure_date = ?", (target,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT workshop_bookings.guest_email AS e, workshop_bookings.guest_name AS n
                   FROM workshop_bookings JOIN workshop_sessions
                     ON workshop_sessions.id = workshop_bookings.session_id
                   WHERE workshop_bookings.status='confirmed'
                     AND workshop_bookings.do_not_email = 0
                     AND workshop_sessions.start_date = ?""", (target,)).fetchall()

        audience = {
            (r["e"] or "").strip().lower(): r["n"] for r in rows
            if r["e"] and (r["e"] or "").strip().lower() not in opted_out
        }
        if not audience:
            continue
        result = send_campaign(conn, t, audience, user_id=None,
                               dedupe_key=f"trigger:{t['id']}:{target}")
        total += result["sent"]
    return f"{total} automated email(s) sent"


def log_workshop_message(conn, booking_id, subject, recipient, status):
    conn.execute(
        "INSERT INTO workshop_messages (workshop_booking_id, subject, recipient, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (booking_id, subject, recipient, status, datetime.now(timezone.utc).isoformat()),
    )


def send_workshop_email(conn, booking, template_key, context):
    """Sends a lifecycle email built from the admin-editable template,
    honouring the guest's do-not-email opt-out and always leaving an
    auditable row in workshop_messages — sent, skipped, or failed — so the
    registration's message history is complete regardless of outcome."""
    subject, body = render_email_template(conn, template_key, context)
    if not subject:
        return
    if booking["do_not_email"]:
        log_workshop_message(conn, booking["id"], subject, booking["guest_email"], "skipped — opted out")
        return
    sent = send_email(booking["guest_email"], subject, body)
    log_workshop_message(conn, booking["id"], subject, booking["guest_email"], "sent" if sent else "failed")


def workshop_email_context(booking):
    """Common merge-tag values available to every workshop email template.
    Expects a workshop_bookings row joined with workshop_sessions (start_date,
    end_date) and workshops (title) — the same shape every admin/guest route
    already fetches."""
    date_line = format_date_human(booking["start_date"])
    if booking["end_date"] != booking["start_date"]:
        date_line += f" to {format_date_human(booking['end_date'])}"
    price_lines = []
    if booking["total_price"]:
        price_lines.append(f"Estimated total: €{booking['total_price']:.2f}")
    if booking["deposit_amount"]:
        price_lines.append(f"Deposit due now: €{booking['deposit_amount']:.2f}")
        if booking["balance_amount"]:
            price_lines.append(f"Balance of €{booking['balance_amount']:.2f} due {booking['balance_due_date'] or 'at check-in'}")
    return {
        "guest_name": booking["guest_name"],
        "workshop_title": booking["title"],
        "dates": date_line,
        "party_size": booking["party_size"],
        "reference_code": booking["reference_code"],
        "total_price": f"{booking['total_price']:.2f}" if booking["total_price"] else "0.00",
        "deposit_amount": f"{booking['deposit_amount']:.2f}" if booking["deposit_amount"] else "0.00",
        "balance_amount": f"{booking['balance_amount']:.2f}" if booking["balance_amount"] else "0.00",
        "balance_due_date": booking["balance_due_date"] or "at check-in",
        "balance_line": (f"Balance of €{booking['balance_amount']:.2f} due {booking['balance_due_date'] or 'at check-in'}."
                          if booking["balance_amount"] else "Paid in full."),
        "price_block": ("\n" + "\n".join(price_lines)) if price_lines else "",
        "manage_url": url_for("workshop_manage", manage_token=booking["manage_token"], _external=True),
    }


def start_workshop_stripe_payment(conn, booking_id, kind):
    """Builds a Stripe Checkout Session for a workshop registration's
    deposit or balance and returns its URL, or None if Stripe isn't
    configured, nothing is due, or that amount is already paid — callers
    fall back to the non-payment confirmation flow in that case, same as
    the room-booking path when Stripe is off."""
    if not stripe_enabled():
        return None
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshops.title FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""",
        (booking_id,),
    ).fetchone()
    if not booking or booking["status"] in ("declined", "cancelled"):
        return None
    if kind == "deposit":
        amount = booking["deposit_amount"]
        blocked = bool(booking["deposit_paid_at"])
        label = "Deposit"
    else:
        # The balance can move after the deposit stage if the owner adds a
        # discount, extra charge, or partial payment — pull the live figure
        # from the ledger rather than the value computed at registration time.
        amount, _, _ = workshop_balance_due(conn, booking_id)
        blocked = not booking["deposit_paid_at"]  # can't pay the balance before the deposit
        label = "Balance"
    if not amount or amount <= 0 or blocked:
        return None
    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": f"{label} — {booking['title']} ({booking['reference_code']})"},
                    "unit_amount": int(round(amount * 100)),
                },
                "quantity": 1,
            }],
            customer_email=booking["guest_email"],
            success_url=url_for("workshop_stripe_success", manage_token=booking["manage_token"], kind=kind, _external=True) + "&session_id={CHECKOUT_SESSION_ID}",
            cancel_url=url_for("workshop_stripe_cancel", manage_token=booking["manage_token"], _external=True),
            metadata={"workshop_booking_id": str(booking_id), "kind": f"workshop_{kind}"},
        )
    except Exception:
        return None
    return checkout_session.url


def mark_workshop_payment_paid(conn, session):
    """Shared by the success redirect and the webhook — whichever fires
    first wins via the WHERE ...IS NULL guard, so a race just means the
    second call's UPDATE affects zero rows. Also logs the payment to the
    ledger and, for a deposit, emails a receipt."""
    meta = smeta(session)
    kind = meta.get("kind", "")
    booking_id = meta.get("workshop_booking_id")
    if not booking_id or kind not in ("workshop_deposit", "workshop_balance"):
        return
    booking_id = int(booking_id)
    now = datetime.now(timezone.utc).isoformat()
    if kind == "workshop_deposit":
        cur = conn.execute(
            "UPDATE workshop_bookings SET deposit_paid_at = ?, deposit_stripe_session_id = ? "
            "WHERE id = ? AND deposit_paid_at IS NULL",
            (now, session["id"], booking_id),
        )
    else:
        cur = conn.execute(
            "UPDATE workshop_bookings SET balance_paid_at = ?, balance_stripe_session_id = ? "
            "WHERE id = ? AND balance_paid_at IS NULL",
            (now, session["id"], booking_id),
        )
    if cur.rowcount:
        label = "Deposit" if kind == "workshop_deposit" else "Balance"
        amount = (sval(session, "amount_total") or 0) / 100
        add_workshop_transaction(conn, booking_id, "payment", f"{label} — Stripe", amount, method="stripe")
        if kind == "workshop_deposit":
            booking = conn.execute(
                """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date, workshops.title
                   FROM workshop_bookings
                   JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
                   JOIN workshops ON workshops.id = workshop_sessions.workshop_id
                   WHERE workshop_bookings.id = ?""",
                (booking_id,),
            ).fetchone()
            send_workshop_email(conn, booking, "workshop_deposit_receipt", workshop_email_context(booking))
    conn.commit()


@app.route("/workshops/stripe-success")
def workshop_stripe_success():
    manage_token = request.args.get("manage_token", "")
    kind = request.args.get("kind", "")
    session_id = request.args.get("session_id", "").strip()
    if not stripe_enabled() or not session_id or not manage_token:
        abort(404)
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        abort(404)
    if sval(session, "payment_status") == "paid":
        conn = get_db()
        mark_workshop_payment_paid(conn, session)
        conn.close()
        flash("Payment received, thank you.", "success")
    else:
        flash("That payment wasn't completed.", "error")
    return redirect(url_for("workshop_manage", manage_token=manage_token))


@app.route("/workshops/stripe-cancel/<manage_token>")
def workshop_stripe_cancel(manage_token):
    flash("Payment was cancelled.", "error")
    return redirect(url_for("workshop_manage", manage_token=manage_token))


@app.route("/workshops/pay-deposit/<manage_token>")
def workshop_pay_deposit(manage_token):
    conn = get_db()
    booking = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?", (manage_token,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    checkout_url = start_workshop_stripe_payment(conn, booking["id"], "deposit")
    conn.close()
    if not checkout_url:
        flash("This deposit can't be paid online right now — contact the château directly.", "error")
        return redirect(url_for("workshop_manage", manage_token=manage_token))
    return redirect(checkout_url, code=303)


@app.route("/workshops/pay-balance/<manage_token>")
def workshop_pay_balance(manage_token):
    conn = get_db()
    booking = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?", (manage_token,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    checkout_url = start_workshop_stripe_payment(conn, booking["id"], "balance")
    conn.close()
    if not checkout_url:
        flash("This balance can't be paid online right now — contact the château directly.", "error")
        return redirect(url_for("workshop_manage", manage_token=manage_token))
    return redirect(checkout_url, code=303)


@app.route("/workshops")
def workshops_public():
    conn = get_db()
    today = datetime.now(timezone.utc).date()
    workshops = conn.execute("SELECT * FROM workshops WHERE active = 1 ORDER BY sort_order, title").fetchall()
    sessions_by_workshop = {}
    for w in workshops:
        sessions = conn.execute(
            "SELECT * FROM workshop_sessions WHERE workshop_id = ? AND start_date >= ? ORDER BY start_date",
            (w["id"], today.isoformat()),
        ).fetchall()
        sessions_by_workshop[w["id"]] = [
            {"session": s, "remaining": workshop_session_remaining_capacity(conn, s["id"])} for s in sessions
        ]
    featured_reviews = conn.execute(
        """SELECT workshop_feedback.*, workshops.title FROM workshop_feedback
           LEFT JOIN workshop_bookings ON workshop_bookings.id = workshop_feedback.workshop_booking_id
           LEFT JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           LEFT JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_feedback.featured = 1
           ORDER BY workshop_feedback.rating DESC, workshop_feedback.submitted_at DESC LIMIT 6"""
    ).fetchall()
    conn.close()
    return render_template(
        "workshops_public.html", workshops=workshops, sessions_by_workshop=sessions_by_workshop,
        featured_reviews=featured_reviews,
    )


@app.route("/workshops/<int:workshop_id>")
def workshop_detail(workshop_id):
    conn = get_db()
    workshop = conn.execute("SELECT * FROM workshops WHERE id = ? AND active = 1", (workshop_id,)).fetchone()
    if not workshop:
        conn.close()
        abort(404)
    today = datetime.now(timezone.utc).date()
    sessions = conn.execute(
        "SELECT * FROM workshop_sessions WHERE workshop_id = ? AND start_date >= ? ORDER BY start_date",
        (workshop_id, today.isoformat()),
    ).fetchall()
    session_rows = [{"session": s, "remaining": workshop_session_remaining_capacity(conn, s["id"])} for s in sessions]
    conn.close()
    return render_template("workshop_detail.html", workshop=workshop, session_rows=session_rows)


@app.route("/workshops/register/<int:session_id>", methods=["GET", "POST"])
def workshop_register(session_id):
    conn = get_db()
    session_row = conn.execute(
        """SELECT workshop_sessions.*, workshops.title, workshops.price_per_person, workshops.instructor_name,
               workshops.instructor_user_id, workshops.active, workshops.deposit_percent, workshops.inclusions
           FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_sessions.id = ?""",
        (session_id,),
    ).fetchone()
    if not session_row or not session_row["active"]:
        conn.close()
        abort(404)
    today = datetime.now(timezone.utc).date()
    start_date = parse_date(session_row["start_date"])
    if not start_date or start_date < today:
        conn.close()
        abort(404)
    custom_fields = conn.execute(
        "SELECT * FROM workshop_custom_fields WHERE workshop_id = ? ORDER BY sort_order",
        (session_row["workshop_id"],),
    ).fetchall()

    if request.method == "POST":
        if rate_limited(conn, "register_workshop", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("workshop_register.html", session=session_row, prefill_name="", prefill_email="", prefill_phone="", prefill_party_size="", fully_booked=False, custom_fields=custom_fields)

        guest_name = request.form.get("guest_name", "").strip()
        guest_email = request.form.get("guest_email", "").strip().lower()
        guest_phone = request.form.get("guest_phone", "").strip()
        notes = request.form.get("notes", "").strip()[:500]
        party_size_raw = request.form.get("party_size", "").strip()
        party_size = int(party_size_raw) if party_size_raw.isdigit() else None
        occupancy_type = request.form.get("occupancy_type", "double").strip()
        if occupancy_type not in ("solo", "double", "triple"):
            occupancy_type = "double"
        requested_roommate = request.form.get("requested_roommate", "").strip()[:200]
        dietary_notes = request.form.get("dietary_notes", "").strip()[:500]
        medical_notes = request.form.get("medical_notes", "").strip()[:500]
        special_occasion = request.form.get("special_occasion", "").strip()[:200]
        other_guest_names = [
            line.strip()[:200] for line in request.form.get("other_guest_names", "").splitlines() if line.strip()
        ][:19]  # party_size has no hard cap, but a sane ceiling keeps this from becoming a spam vector
        promo_code = request.form.get("promo_code", "").strip()

        error = None
        fully_booked = False
        if not guest_name or not guest_email:
            error = "Name and email are required."
        elif not EMAIL_RE.match(guest_email):
            error = "Enter a valid email address."
        elif not party_size or party_size < 1:
            error = "Party size is required."
        elif party_size > session_row["capacity"]:
            error = f"This session has {session_row['capacity']} spots total."
        elif workshop_session_remaining_capacity(conn, session_id) < party_size:
            error = "This session is fully booked — join the waitlist below, or choose another date."
            fully_booked = True

        custom_values = {}
        if not error:
            for f in custom_fields:
                raw = request.form.get(f"custom_{f['id']}", "").strip()
                if f["required"] and not raw:
                    error = f"'{f['label']}' is required."
                    break
                custom_values[f["id"]] = raw

        if error:
            flash(error, "error")
            conn.commit()  # persist the rate-limit log entry even on a validation error
            conn.close()
            return render_template(
                "workshop_register.html", session=session_row,
                prefill_name=guest_name, prefill_email=guest_email, prefill_phone=guest_phone,
                prefill_party_size=party_size_raw, fully_booked=fully_booked, custom_fields=custom_fields,
            )

        workshop = conn.execute("SELECT * FROM workshops WHERE id = ?", (session_row["workshop_id"],)).fetchone()
        if promo_code and workshop["price_per_person"]:
            promo_preview, _, promo_error = validate_promo_code(
                conn, promo_code, "workshop", workshop["price_per_person"] * party_size
            )
            if not promo_preview:
                flash(f"Promo code not applied: {promo_error}", "error")
        reference_code, manage_token, booking_row_id = create_workshop_booking(
            conn, session_row, workshop, guest_name, guest_email, guest_phone or None, party_size, notes,
            occupancy_type=occupancy_type, requested_roommate=requested_roommate, dietary_notes=dietary_notes,
            medical_notes=medical_notes, special_occasion=special_occasion, promo_code=promo_code or None,
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name, is_lead, created_at) VALUES (?, ?, 1, ?)",
            (booking_row_id, guest_name, now_iso),
        )
        for other_name in other_guest_names:
            conn.execute(
                "INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name, is_lead, created_at) VALUES (?, ?, 0, ?)",
                (booking_row_id, other_name, now_iso),
            )
        for field_id, value in custom_values.items():
            if value:
                conn.execute(
                    "INSERT INTO workshop_custom_field_responses (workshop_booking_id, custom_field_id, value, created_at) VALUES (?, ?, ?, ?)",
                    (booking_row_id, field_id, value, now_iso),
                )
        conn.commit()

        checkout_url = start_workshop_stripe_payment(conn, booking_row_id, "deposit")
        conn.close()
        if checkout_url:
            return redirect(checkout_url, code=303)
        return redirect(url_for("workshop_confirmation", manage_token=manage_token))

    conn.close()
    return render_template(
        "workshop_register.html", session=session_row,
        prefill_name=request.args.get("name", ""), prefill_email=request.args.get("email", ""),
        prefill_phone=request.args.get("phone", ""), prefill_party_size=request.args.get("party_size", ""),
        fully_booked=False, custom_fields=custom_fields,
    )


@app.route("/workshops/confirmation/<manage_token>")
def workshop_confirmation(manage_token):
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date,
               workshops.title FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.manage_token = ?""",
        (manage_token,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    guests = conn.execute(
        "SELECT * FROM workshop_booking_guests WHERE workshop_booking_id = ? ORDER BY is_lead DESC, id",
        (booking["id"],),
    ).fetchall()
    conn.close()
    return render_template("workshop_confirmation.html", booking=booking, guests=guests)


@app.route("/workshops/find", methods=["GET", "POST"])
def workshop_find():
    if request.method == "POST":
        conn = get_db()
        if rate_limited(conn, "workshop_find", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("workshop_find.html")
        conn.commit()
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        booking = conn.execute(
            "SELECT manage_token FROM workshop_bookings WHERE reference_code = ? AND guest_email = ?",
            (reference_code, email),
        ).fetchone()
        conn.close()
        if booking:
            return redirect(url_for("workshop_manage", manage_token=booking["manage_token"]))
        flash("No registration found with that reference and email.", "error")
    return render_template("workshop_find.html")


@app.route("/workshops/manage/<manage_token>", methods=["GET", "POST"])
def workshop_manage(manage_token):
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date,
               workshops.title, rooms.name AS assigned_room_name FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           LEFT JOIN rooms ON rooms.id = workshop_bookings.assigned_room_id
           WHERE workshop_bookings.manage_token = ?""",
        (manage_token,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)

    if request.method == "POST" and request.form.get("action") == "cancel":
        cur = conn.execute(
            "UPDATE workshop_bookings SET status = 'cancelled', decided_at = ? WHERE id = ? AND status IN ('pending', 'confirmed')",
            (datetime.now(timezone.utc).isoformat(), booking["id"]),
        )
        conn.commit()
        if cur.rowcount:
            owner_to = owner_email(conn)
            if owner_to:
                send_email(
                    owner_to,
                    f"Workshop registration cancelled — {booking['reference_code']}",
                    f"{booking['guest_name']} cancelled their registration for {booking['title']} "
                    f"({booking['start_date']}), party of {booking['party_size']}.",
                )
            flash("Your registration has been cancelled.", "success")
        conn.close()
        return redirect(url_for("workshop_manage", manage_token=manage_token))

    if request.method == "POST" and request.form.get("action") == "change_session":
        new_session_id_raw = request.form.get("new_session_id", "").strip()
        if booking["status"] not in ("pending", "confirmed"):
            flash("This registration can't be moved.", "error")
            conn.close()
            return redirect(url_for("workshop_manage", manage_token=manage_token))
        if not new_session_id_raw.isdigit() or int(new_session_id_raw) == booking["session_id"]:
            flash("Choose a different date to move to.", "error")
            conn.close()
            return redirect(url_for("workshop_manage", manage_token=manage_token))

        new_session_id = int(new_session_id_raw)
        current_session = conn.execute("SELECT workshop_id FROM workshop_sessions WHERE id = ?", (booking["session_id"],)).fetchone()
        new_session = conn.execute(
            "SELECT * FROM workshop_sessions WHERE id = ? AND workshop_id = ?",
            (new_session_id, current_session["workshop_id"]),
        ).fetchone()
        if not new_session:
            flash("That date isn't available for this workshop.", "error")
        elif workshop_session_remaining_capacity(conn, new_session_id) < booking["party_size"]:
            flash("That date doesn't have enough spots left for your party size.", "error")
        elif booking["status"] == "pending" and not booking["deposit_paid_at"]:
            # Nothing's been charged yet, so it's safe to move immediately
            # rather than routing a still-pending registration through the
            # owner — deposit/balance terms are recalculated for the new
            # date since the 30-day-out cutoff may now land differently.
            workshop_row = conn.execute("SELECT deposit_percent FROM workshops WHERE id = ?", (current_session["workshop_id"],)).fetchone()
            new_deposit_percent = resolve_deposit_percent(
                conn, "workshop", new_session["start_date"], booking["party_size"], workshop_row["deposit_percent"]
            )
            deposit_amount, balance_amount, balance_due_date = compute_workshop_payment_terms(
                booking["total_price"], new_deposit_percent, parse_date(new_session["start_date"]),
            )
            conn.execute(
                """UPDATE workshop_bookings SET session_id = ?, deposit_amount = ?, balance_amount = ?,
                   balance_due_date = ? WHERE id = ?""",
                (new_session_id, deposit_amount, balance_amount, balance_due_date, booking["id"]),
            )
            conn.commit()
            owner_to = owner_email(conn)
            if owner_to:
                send_email(
                    owner_to, f"Guest moved dates — {booking['reference_code']}",
                    f"{booking['guest_name']} moved their {booking['title']} registration from "
                    f"{booking['start_date']} to {new_session['start_date']}.",
                )
            flash("You've been moved to the new dates.", "success")
        else:
            # Confirmed or already paid — a date move here could shift the
            # balance due date or (if the new dates are inside the 30-day
            # window) demand full payment immediately, so it goes to the
            # owner for a deliberate decision rather than auto-applying.
            note = (
                f"{booking['guest_name']} would like to move their {booking['title']} registration from "
                f"{booking['start_date']} to {new_session['start_date']}. That date has room — review and "
                f"apply the change from the workshop registrations page if you're happy with it."
            )
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                """INSERT INTO tasks (title, notes, room_note, priority, due_date, created_at, origin)
                   VALUES (?, ?, ?, 'normal', ?, ?, 'guest_request')""",
                (f"Workshop date change request — {booking['reference_code']}", note,
                 f"{booking['title']} — {booking['guest_name']}", new_session["start_date"], now),
            )
            conn.commit()
            owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
            if owner:
                send_notification(
                    conn, owner["id"], "guest_request", f"Workshop date change request — {booking['guest_name']}",
                    body=note, link="/admin/workshops/registrations", related_task_id=cur.lastrowid,
                )
            flash("Your request has been sent — we'll confirm shortly.", "success")
        conn.close()
        return redirect(url_for("workshop_manage", manage_token=manage_token))

    guests = conn.execute(
        "SELECT * FROM workshop_booking_guests WHERE workshop_booking_id = ? ORDER BY is_lead DESC, id",
        (booking["id"],),
    ).fetchall()
    custom_fields = conn.execute(
        "SELECT * FROM workshop_custom_fields WHERE workshop_id = (SELECT workshop_id FROM workshop_sessions WHERE id = ?) ORDER BY sort_order",
        (booking["session_id"],),
    ).fetchall()
    custom_responses = {
        row["custom_field_id"]: row["value"] for row in conn.execute(
            "SELECT * FROM workshop_custom_field_responses WHERE workshop_booking_id = ?", (booking["id"],)
        ).fetchall()
    }
    balance_due, total_charged, total_paid = workshop_balance_due(conn, booking["id"])

    other_sessions = []
    if booking["status"] in ("pending", "confirmed"):
        today = datetime.now(timezone.utc).date()
        current_workshop_id = conn.execute(
            "SELECT workshop_id FROM workshop_sessions WHERE id = ?", (booking["session_id"],)
        ).fetchone()["workshop_id"]
        candidates = conn.execute(
            """SELECT * FROM workshop_sessions WHERE workshop_id = ? AND id != ? AND start_date >= ?
               ORDER BY start_date""",
            (current_workshop_id, booking["session_id"], today.isoformat()),
        ).fetchall()
        other_sessions = [
            s for s in candidates
            if workshop_session_remaining_capacity(conn, s["id"]) >= booking["party_size"]
        ]

    conn.close()
    return render_template(
        "workshop_manage.html", booking=booking, stripe_enabled=stripe_enabled(), guests=guests,
        custom_fields=custom_fields, custom_responses=custom_responses,
        balance_due=balance_due, total_charged=total_charged, total_paid=total_paid,
        other_sessions=other_sessions,
    )


@app.route("/ics/<token>.ics")
def room_ics_feed(token):
    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE export_token = ?", (token,)).fetchone()
    if not room:
        conn.close()
        abort(404)
    body = generate_room_ics(conn, room)
    conn.close()
    return app.response_class(body, mimetype="text/calendar")


@app.route("/room-photos/<filename>")
def room_photo(filename):
    return send_from_directory(ROOM_PHOTO_DIR, filename)


# ---------------------------------------------------------------------------
# Owner admin: rooms, iCal sync sources, and booking requests
# ---------------------------------------------------------------------------

@app.route("/admin/rooms")
@owner_required
def admin_rooms():
    conn = get_db()
    rooms = conn.execute("SELECT * FROM rooms ORDER BY sort_order, name").fetchall()
    sources_by_room, ics_urls, blocks_by_room = {}, {}, {}
    now = datetime.now(timezone.utc)
    for room in rooms:
        sources = conn.execute(
            "SELECT * FROM ical_sources WHERE room_id = ? ORDER BY id", (room["id"],)
        ).fetchall()
        stale, last_logs = [], []
        for s in sources:
            synced_at = parse_datetime_iso(s["last_synced_at"])
            stale.append(not synced_at or (now - synced_at) > timedelta(hours=24))
            last_logs.append(conn.execute(
                "SELECT * FROM ical_sync_log WHERE ical_source_id = ? ORDER BY id DESC LIMIT 1",
                (s["id"],),
            ).fetchone())
        sources_by_room[room["id"]] = list(zip(sources, stale, last_logs))
        ics_urls[room["id"]] = url_for("room_ics_feed", token=room["export_token"], _external=True)
        blocks_by_room[room["id"]] = conn.execute(
            "SELECT * FROM room_blocks WHERE room_id = ? ORDER BY start_date", (room["id"],)
        ).fetchall()
    conn.close()
    return render_template(
        "admin_rooms.html", rooms=rooms, sources_by_room=sources_by_room, ics_urls=ics_urls,
        blocks_by_room=blocks_by_room, today=now.date(),
    )


@app.route("/room-issues")
@login_required
def room_issues():
    conn = get_db()
    status_filter = request.args.get("status", "open")
    query = (
        """SELECT room_issues.*, rooms.name AS room_name, users.name AS reported_by_name
           FROM room_issues JOIN rooms ON rooms.id = room_issues.room_id
           LEFT JOIN users ON users.id = room_issues.reported_by_user_id"""
    )
    params = ()
    if status_filter in ("open", "resolved"):
        query += " WHERE room_issues.status = ?"
        params = (status_filter,)
    query += " ORDER BY room_issues.created_at DESC"
    issues = conn.execute(query, params).fetchall()
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, name").fetchall()
    employees = conn.execute("SELECT * FROM users WHERE role = 'employee' ORDER BY name").fetchall()
    # Counted separately rather than off `issues`, which the status filter
    # above has usually already narrowed (this page defaults to open-only).
    counts = {
        r["status"]: r["c"] for r in conn.execute(
            "SELECT status, COUNT(*) AS c FROM room_issues GROUP BY status"
        ).fetchall()
    }
    rooms_affected = conn.execute(
        "SELECT COUNT(DISTINCT room_id) AS c FROM room_issues WHERE status = 'open'"
    ).fetchone()["c"]
    conn.close()
    return render_template(
        "room_issues.html", issues=issues, rooms=rooms, status_filter=status_filter,
        employees=employees, counts=counts, rooms_affected=rooms_affected,
    )


@app.route("/room-issues/export.csv")
@login_required
def export_room_issues_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT room_issues.*, rooms.name AS room_name, users.name AS reported_by_name
           FROM room_issues JOIN rooms ON rooms.id = room_issues.room_id
           LEFT JOIN users ON users.id = room_issues.reported_by_user_id
           ORDER BY room_issues.created_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["room_name", "title", "description", "status", "reported_by_name", "created_at", "resolved_at"]
    return csv_response(fieldnames, rows, "room_issues.csv")


@app.route("/room-issues/new", methods=["POST"])
@login_required
def new_room_issue():
    user = current_user()
    room_id = request.form.get("room_id", "")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    assigned_to = request.form.get("assigned_to_user_id", "").strip()
    if not room_id or not title:
        flash("Room and a short title are required.", "error")
        return redirect(url_for("room_issues"))
    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO room_issues (room_id, reported_by_user_id, title, description, status, created_at) VALUES (?,?,?,?,'open',?)",
        (room_id, user["id"], title, description, now),
    )
    task_note = ""
    if assigned_to.isdigit() and room:
        conn.execute(
            """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date, created_at)
               VALUES (?, ?, ?, 'normal', ?, ?)""",
            (int(assigned_to), f"{room['name']}: {title}", description or None,
             datetime.now(timezone.utc).date().isoformat(), now),
        )
        task_note = " Task assigned."
    conn.commit()
    conn.close()
    flash("Issue reported." + task_note, "success")
    return redirect(url_for("room_issues"))


@app.route("/room-issues/<int:issue_id>/resolve", methods=["POST"])
@owner_required
def resolve_room_issue(issue_id):
    conn = get_db()
    conn.execute(
        "UPDATE room_issues SET status='resolved', resolved_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), issue_id),
    )
    conn.commit()
    conn.close()
    flash("Issue marked resolved.", "success")
    return redirect(url_for("room_issues"))


@app.route("/room-issues/<int:issue_id>/reopen", methods=["POST"])
@owner_required
def reopen_room_issue(issue_id):
    conn = get_db()
    conn.execute("UPDATE room_issues SET status='open', resolved_at=NULL WHERE id=?", (issue_id,))
    conn.commit()
    conn.close()
    flash("Issue reopened.", "success")
    return redirect(url_for("room_issues"))


@app.route("/room-issues/<int:issue_id>/delete", methods=["POST"])
@owner_required
def delete_room_issue(issue_id):
    conn = get_db()
    conn.execute("DELETE FROM room_issues WHERE id = ?", (issue_id,))
    conn.commit()
    conn.close()
    flash("Issue removed.", "success")
    return redirect(url_for("room_issues"))


@app.route("/admin/rooms/sync-all", methods=["POST"])
@owner_required
def sync_all_ical_sources():
    conn = get_db()
    sources = conn.execute("SELECT * FROM ical_sources").fetchall()
    ok_count = 0
    for source in sources:
        if sync_ical_source(conn, source):
            ok_count += 1
    conn.close()
    flash(f"Synced {ok_count} of {len(sources)} calendar{'s' if len(sources) != 1 else ''}.",
          "success" if ok_count == len(sources) else "error")
    return redirect(url_for("admin_rooms"))


@app.route("/api/sync-ical", methods=["GET", "POST"])
def api_sync_ical():
    """No-login sync trigger for an external scheduler. 404s (not 401/403,
    so a prober learns nothing) unless ICAL_SYNC_TOKEN is set and matches."""
    supplied = request.args.get("token", "")
    if not ICAL_SYNC_TOKEN or not hmac.compare_digest(supplied, ICAL_SYNC_TOKEN):
        abort(404)
    conn = get_db()
    sources = conn.execute("SELECT * FROM ical_sources").fetchall()
    results = []
    ok_count = 0
    for source in sources:
        ok = sync_ical_source(conn, source)
        ok_count += 1 if ok else 0
        log_row = conn.execute(
            "SELECT * FROM ical_sync_log WHERE ical_source_id = ? ORDER BY id DESC LIMIT 1",
            (source["id"],),
        ).fetchone()
        results.append({
            "source_id": source["id"],
            "label": source["label"],
            "room_id": source["room_id"],
            "success": ok,
            "added": log_row["added"] if log_row else 0,
            "removed": log_row["removed"] if log_row else 0,
            "unchanged": log_row["unchanged"] if log_row else 0,
            "error": log_row["error"] if log_row else None,
        })
    conn.close()
    return {"synced": ok_count, "total": len(sources), "sources": results}, 200


def build_owner_digest(conn):
    """Plain-text 'what needs your attention' summary — same facts as the
    Approvals queue and dashboard, just delivered by email for someone who
    isn't logging in every day."""
    today = datetime.now(timezone.utc).date()
    leave_pending = conn.execute("SELECT COUNT(*) AS c FROM leave_requests WHERE status = 'pending'").fetchone()["c"]
    expenses_pending = conn.execute("SELECT COUNT(*) AS c FROM expenses WHERE status = 'pending'").fetchone()["c"]
    room_issues_open = conn.execute("SELECT COUNT(*) AS c FROM room_issues WHERE status = 'open'").fetchone()["c"]
    on_shift = conn.execute(
        """SELECT users.name FROM time_entries JOIN users ON users.id = time_entries.user_id
           WHERE time_entries.clock_out_at IS NULL ORDER BY users.name"""
    ).fetchall()
    upcoming_leave = conn.execute(
        """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id
           WHERE leave_requests.status = 'approved' AND leave_requests.start_date >= ?
           AND leave_requests.start_date <= ? ORDER BY leave_requests.start_date""",
        (today.isoformat(), (today + timedelta(days=7)).isoformat()),
    ).fetchall()
    soon = (today + timedelta(days=30)).isoformat()
    due_costs = conn.execute(
        """SELECT label, amount, next_due_date FROM recurring_costs
           WHERE active = 1 AND next_due_date IS NOT NULL AND next_due_date <= ? ORDER BY next_due_date""",
        (soon,),
    ).fetchall()
    expiring_policies = conn.execute(
        """SELECT provider, expiry_date FROM insurance_policies
           WHERE expiry_date IS NOT NULL AND expiry_date <= ? ORDER BY expiry_date""",
        (soon,),
    ).fetchall()
    waitlist_open = conn.execute(
        "SELECT COUNT(*) AS c FROM waitlist_entries WHERE status IN ('open', 'contacted')"
    ).fetchone()["c"]
    last_backup = conn.execute(
        "SELECT created_at FROM audit_log WHERE action = 'backup_downloaded' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    last_backup_at = last_backup["created_at"] if last_backup else None
    backup_stale = (not last_backup_at) or (parse_date(last_backup_at[:10]) <= today - timedelta(days=30))
    understaffed_days = [
        s for s in roster_vs_occupancy(conn, [today + timedelta(days=i) for i in range(7)])
        if s["understaffed"]
    ]
    outliers = timesheet_outliers(conn, today)
    dirty_vehicles = conn.execute("SELECT name FROM vehicles WHERE cleanliness = 'dirty' ORDER BY name").fetchall()
    low_fuel_vehicles = conn.execute("SELECT name FROM vehicles WHERE fuel_level = 'low' ORDER BY name").fetchall()
    vehicles_service_due = conn.execute(
        "SELECT name, next_service_due FROM vehicles WHERE next_service_due IS NOT NULL AND next_service_due <= ? ORDER BY next_service_due",
        (soon,),
    ).fetchall()
    low_stock_breakfast = conn.execute("SELECT name FROM breakfast_items WHERE low_stock = 1 ORDER BY name").fetchall()
    overdue_checkouts = overdue_vehicle_checkouts(conn)

    lines = [f"Château de Gudanes — daily summary for {today.isoformat()}", ""]
    lines.append(f"Approvals waiting: {leave_pending + expenses_pending} ({leave_pending} time off, {expenses_pending} expenses)")
    lines.append(f"Open room issues: {room_issues_open}")
    if waitlist_open:
        lines.append(f"On the waitlist: {waitlist_open}")
    lines.append("")
    lines.append(f"On shift now: {', '.join(r['name'] for r in on_shift) if on_shift else 'nobody'}")
    lines.append("")
    if upcoming_leave:
        lines.append("Leave starting in the next 7 days:")
        for r in upcoming_leave:
            lines.append(f"  - {r['employee_name']}: {r['start_date']} → {r['end_date']}")
    else:
        lines.append("No leave starting in the next 7 days.")
    lines.append("")
    if due_costs:
        lines.append("Recurring costs due in the next 30 days:")
        for c in due_costs:
            lines.append(f"  - {c['label']}: €{c['amount']:.0f} due {c['next_due_date']}")
    if expiring_policies:
        lines.append("Insurance renewals due in the next 30 days:")
        for p in expiring_policies:
            lines.append(f"  - {p['provider']}: expires {p['expiry_date']}")
    if backup_stale:
        lines.append("")
        lines.append(f"Backup reminder: {'last downloaded ' + last_backup_at[:10] if last_backup_at else 'never downloaded'} — consider grabbing a fresh one.")
    if understaffed_days:
        lines.append("")
        lines.append("Looks short-staffed vs. booking volume (next 7 days):")
        for s in understaffed_days:
            lines.append(f"  - {s['date'].isoformat()}: {s['occupied_rooms']} rooms occupied, {s['scheduled']} scheduled (suggest {s['suggested']})")
    if outliers:
        lines.append("")
        lines.append(f"Timesheet outliers in the last 14 days: {len(outliers)} — check the Shifts page for details.")
    if dirty_vehicles or low_fuel_vehicles or vehicles_service_due or overdue_checkouts:
        lines.append("")
        lines.append("Vehicles needing attention:")
        for v in dirty_vehicles:
            lines.append(f"  - {v['name']}: dirty")
        for v in low_fuel_vehicles:
            lines.append(f"  - {v['name']}: low fuel")
        for v in vehicles_service_due:
            lines.append(f"  - {v['name']}: service due {v['next_service_due']}")
        for c in overdue_checkouts:
            lines.append(f"  - {c['vehicle_name']}: checked out by {c['user_name'] or 'unknown'} since {c['checked_out_at'][:10]}, not checked in")
    if low_stock_breakfast:
        lines.append("")
        lines.append("Breakfast items flagged low stock: " + ", ".join(i["name"] for i in low_stock_breakfast))

    restaurant_settings = get_restaurant_settings(conn)
    if restaurant_settings:
        pending_dinners = conn.execute(
            "SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status = 'pending'"
        ).fetchone()["c"]
        opening = parse_date(restaurant_settings["opening_date"]) if restaurant_settings["opening_date"] else None
        if opening and opening > today:
            lines.append("")
            lines.append(f"Restaurant opens in {(opening - today).days} day(s), on {opening.isoformat()}.")
        tonight_covers = conn.execute(
            "SELECT COALESCE(SUM(party_size), 0) AS c FROM restaurant_bookings WHERE status = 'confirmed' AND dinner_date = ?",
            (today.isoformat(),),
        ).fetchone()["c"]
        if tonight_covers or pending_dinners:
            lines.append("")
            lines.append(f"Restaurant: {tonight_covers} confirmed covers tonight, {pending_dinners} reservation(s) awaiting a decision.")

    pending_workshop_regs = conn.execute(
        "SELECT COUNT(*) AS c FROM workshop_bookings WHERE status = 'pending'"
    ).fetchone()["c"]
    sessions_this_week = conn.execute(
        """SELECT workshops.title, workshop_sessions.start_date,
                  COALESCE((SELECT SUM(party_size) FROM workshop_bookings
                            WHERE session_id = workshop_sessions.id AND status = 'confirmed'), 0) AS covers
           FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_sessions.start_date >= ? AND workshop_sessions.start_date <= ?
           ORDER BY workshop_sessions.start_date""",
        (today.isoformat(), (today + timedelta(days=7)).isoformat()),
    ).fetchall()
    balances_due_soon = conn.execute(
        """SELECT workshops.title, workshop_bookings.guest_name, workshop_bookings.balance_amount,
                  workshop_bookings.balance_due_date
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.status = 'confirmed' AND workshop_bookings.balance_amount > 0
             AND workshop_bookings.balance_paid_at IS NULL AND workshop_bookings.balance_due_date IS NOT NULL
             AND workshop_bookings.balance_due_date <= ?
           ORDER BY workshop_bookings.balance_due_date""",
        ((today + timedelta(days=7)).isoformat(),),
    ).fetchall()
    if pending_workshop_regs or sessions_this_week or balances_due_soon:
        lines.append("")
        lines.append(f"Workshops: {pending_workshop_regs} registration(s) awaiting a decision.")
        for s in sessions_this_week:
            lines.append(f"  - {s['title']}: starts {s['start_date']}, {s['covers']} confirmed")
        for b in balances_due_soon:
            lines.append(f"  - Balance due: {b['guest_name']} ({b['title']}) — €{b['balance_amount']:.2f} due {b['balance_due_date']}")
    return "\n".join(lines)


@app.route("/api/owner-digest", methods=["GET", "POST"])
def api_owner_digest():
    """No-login digest trigger for an external scheduler — same 404-not-403
    posture as /api/sync-ical, so an unset/wrong token teaches a prober
    nothing. See DEPLOY.md."""
    supplied = request.args.get("token", "")
    if not DIGEST_TOKEN or not hmac.compare_digest(supplied, DIGEST_TOKEN):
        abort(404)
    conn = get_db()
    to_address = owner_email(conn)
    body = build_owner_digest(conn)
    sent = send_email(to_address, "Your daily summary", body)
    conn.close()
    return {"sent": sent, "to": to_address}, 200


@app.route("/admin/calendar")
@owner_required
def admin_calendar():
    today = datetime.now(timezone.utc).date()
    try:
        year, month = map(int, request.args.get("month", "").split("-"))
        first_day = date(year, month, 1)
    except (ValueError, AttributeError):
        first_day = today.replace(day=1)

    next_month = date(first_day.year + 1, 1, 1) if first_day.month == 12 else date(first_day.year, first_day.month + 1, 1)
    prev_month = date(first_day.year - 1, 12, 1) if first_day.month == 1 else date(first_day.year, first_day.month - 1, 1)
    days = [first_day + timedelta(days=i) for i in range((next_month - first_day).days)]

    conn = get_db()
    rooms = conn.execute("SELECT * FROM rooms ORDER BY sort_order, name").fetchall()
    room_rows = []
    for room in rooms:
        bookings = conn.execute(
            """SELECT * FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
               AND arrival_date < ? AND departure_date > ?""",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()
        blocked = conn.execute(
            """SELECT blocked_dates.*, ical_sources.label AS source_label
               FROM blocked_dates LEFT JOIN ical_sources ON ical_sources.id = blocked_dates.ical_source_id
               WHERE blocked_dates.room_id = ? AND blocked_dates.start_date < ? AND blocked_dates.end_date > ?""",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()
        manual_blocks = conn.execute(
            "SELECT * FROM room_blocks WHERE room_id = ? AND start_date < ? AND end_date > ?",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()

        cells = []
        for d in days:
            status, label, key, link = "free", "", None, None
            for b in bookings:
                b_start, b_end = parse_date(b["arrival_date"]), parse_date(b["departure_date"])
                if b_start <= d < b_end:
                    status, label = b["status"], b["guest_name"]
                    key, link = f"b{b['id']}", url_for("edit_booking", booking_id=b["id"])
                    break
            if status == "free":
                for bl in blocked:
                    bl_start, bl_end = parse_date(bl["start_date"]), parse_date(bl["end_date"])
                    if bl_start <= d < bl_end:
                        status = "external"
                        label = bl["source_label"] or "Blocked on another platform"
                        key = f"x{bl['id']}"
                        break
            if status == "free":
                for rb in manual_blocks:
                    rb_start, rb_end = parse_date(rb["start_date"]), parse_date(rb["end_date"])
                    if rb_start <= d < rb_end:
                        status, label = "manual-block", rb["reason"] or "Blocked"
                        key = f"m{rb['id']}"
                        break
            cells.append({"date": d, "status": status, "label": label, "key": key, "link": link})

        # Collapse consecutive days of the same booking/block into one spanning
        # cell so the guest's name can actually be READ on the grid. Previously
        # every day was a separate blank square with the name only in a `title`
        # tooltip -- invisible on touch devices, and unreadable at a glance even
        # on desktop. Free days stay one-per-cell so each keeps its own
        # today-outline and weekend shading.
        segments = []
        for cell in cells:
            if segments and cell["key"] is not None and segments[-1]["key"] == cell["key"]:
                segments[-1]["span"] += 1
                segments[-1]["end_date"] = cell["date"]
            else:
                segments.append({
                    "status": cell["status"], "label": cell["label"], "key": cell["key"],
                    "link": cell["link"], "span": 1,
                    "date": cell["date"], "end_date": cell["date"],
                })
        for seg in segments:
            seg["has_today"] = seg["date"] <= today <= seg["end_date"]
            seg["weekend"] = seg["span"] == 1 and seg["date"].weekday() >= 5
        room_rows.append({"room": room, "cells": cells, "segments": segments})

    # Occupancy for the month being viewed, so the page answers "how full are we?"
    # without the owner counting coloured squares by eye.
    total_slots = len(rooms) * len(days)
    filled = sum(1 for row in room_rows for c in row["cells"] if c["status"] != "free")
    occupancy_rate = round(filled / total_slots * 100) if total_slots else 0
    confirmed_nights = sum(
        1 for row in room_rows for c in row["cells"] if c["status"] == "confirmed"
    )
    pending_nights = sum(
        1 for row in room_rows for c in row["cells"] if c["status"] == "pending"
    )
    conn.close()

    return render_template(
        "admin_calendar.html", days=days, room_rows=room_rows, first_day=first_day, today=today,
        prev_month=prev_month.strftime("%Y-%m"), next_month=next_month.strftime("%Y-%m"),
        this_month=today.strftime("%Y-%m"), occupancy_rate=occupancy_rate,
        confirmed_nights=confirmed_nights, pending_nights=pending_nights,
    )


@app.route("/admin/team-calendar")
@login_required
def team_calendar():
    today = datetime.now(timezone.utc).date()
    try:
        year, month = map(int, request.args.get("month", "").split("-"))
        first_day = date(year, month, 1)
    except (ValueError, AttributeError):
        first_day = today.replace(day=1)

    next_month = date(first_day.year + 1, 1, 1) if first_day.month == 12 else date(first_day.year, first_day.month + 1, 1)
    prev_month = date(first_day.year - 1, 12, 1) if first_day.month == 1 else date(first_day.year, first_day.month - 1, 1)
    days = [first_day + timedelta(days=i) for i in range((next_month - first_day).days)]

    conn = get_db()
    employees = conn.execute(
        "SELECT * FROM users WHERE role = 'employee' AND status = 'active' ORDER BY name"
    ).fetchall()
    employee_rows = []
    for emp in employees:
        shifts = conn.execute(
            "SELECT * FROM shifts WHERE user_id = ? AND shift_date >= ? AND shift_date < ?",
            (emp["id"], first_day.isoformat(), next_month.isoformat()),
        ).fetchall()
        shifts_by_date = {s["shift_date"]: s for s in shifts}
        leave_rows = conn.execute(
            """SELECT * FROM leave_requests WHERE user_id = ? AND status = 'approved'
               AND start_date < ? AND end_date >= ?""",
            (emp["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()

        cells = []
        for d in days:
            status, label = "free", ""
            on_leave = any(parse_date(lr["start_date"]) <= d <= parse_date(lr["end_date"]) for lr in leave_rows)
            shift = shifts_by_date.get(d.isoformat())
            if on_leave:
                status, label = "leave", "Approved time off"
            elif shift:
                status = "shift"
                label = f"{shift['start_time'] or '?'}–{shift['end_time'] or '?'}"
                if shift["role_note"]:
                    label += f" · {shift['role_note']}"
            cells.append({"date": d, "status": status, "label": label})
        # Project down to just id/name rather than passing the full row —
        # this is now shown to every employee, not just the owner, and the
        # users row otherwise carries pay_rate, notes, and other fields
        # that have no business being in this template's context.
        employee_rows.append({"employee": {"id": emp["id"], "name": emp["name"]}, "cells": cells})

    dinner_covers_by_date = {
        row["dinner_date"]: row["covers"] for row in conn.execute(
            """SELECT dinner_date, COALESCE(SUM(party_size), 0) AS covers FROM restaurant_bookings
               WHERE status IN ('pending', 'confirmed') AND dinner_date >= ? AND dinner_date < ?
               GROUP BY dinner_date""",
            (first_day.isoformat(), next_month.isoformat()),
        ).fetchall()
    }
    dinner_cells = [{"date": d, "covers": dinner_covers_by_date.get(d.isoformat(), 0)} for d in days]

    workshop_sessions_in_range = conn.execute(
        """SELECT workshop_sessions.start_date, workshop_sessions.end_date, workshops.title,
                  COALESCE((SELECT SUM(party_size) FROM workshop_bookings
                            WHERE session_id = workshop_sessions.id AND status IN ('pending', 'confirmed')), 0) AS covers
           FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_sessions.start_date < ? AND workshop_sessions.end_date >= ?""",
        (next_month.isoformat(), first_day.isoformat()),
    ).fetchall()
    workshop_covers_by_date = {}
    workshop_titles_by_date = {}
    for s in workshop_sessions_in_range:
        s_start, s_end = parse_date(s["start_date"]), parse_date(s["end_date"])
        for d in days:
            if s_start <= d <= s_end:
                key = d.isoformat()
                workshop_covers_by_date[key] = workshop_covers_by_date.get(key, 0) + s["covers"]
                workshop_titles_by_date.setdefault(key, []).append(s["title"])
    workshop_cells = [
        {"date": d, "covers": workshop_covers_by_date.get(d.isoformat(), 0),
         "titles": ", ".join(workshop_titles_by_date.get(d.isoformat(), []))}
        for d in days
    ]
    conn.close()

    return render_template(
        "team_calendar.html", days=days, employee_rows=employee_rows, first_day=first_day, today=today,
        prev_month=prev_month.strftime("%Y-%m"), next_month=next_month.strftime("%Y-%m"),
        dinner_cells=dinner_cells, has_dinner_covers=any(c["covers"] for c in dinner_cells),
        workshop_cells=workshop_cells, has_workshop_covers=any(c["covers"] for c in workshop_cells),
    )


def save_room_photo(file):
    """Returns the stored filename, None if no file was given, or False if
    the file type isn't an accepted image."""
    if not file or file.filename == "":
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in IMAGE_EXTENSIONS:
        return False
    safe_name = secure_filename(file.filename)
    stored_name = f"room_{secrets.token_hex(6)}_{safe_name}"
    file.save(os.path.join(ROOM_PHOTO_DIR, stored_name))
    return stored_name


def save_room_photos_multi(files):
    """Gallery counterpart to save_room_photo — saves every valid image in
    a multi-file upload and returns the stored filenames. Skips anything
    that isn't an accepted image type rather than rejecting the whole
    batch, since a gallery upload is a nice-to-have, not the room's one
    required cover photo."""
    stored = []
    for file in files:
        if not file or file.filename == "":
            continue
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext not in IMAGE_EXTENSIONS:
            continue
        safe_name = secure_filename(file.filename)
        stored_name = f"room_{secrets.token_hex(6)}_{safe_name}"
        file.save(os.path.join(ROOM_PHOTO_DIR, stored_name))
        stored.append(stored_name)
    return stored


@app.route("/admin/extras")
@owner_required
def admin_extras():
    conn = get_db()
    extras = conn.execute("SELECT * FROM extras ORDER BY sort_order, name").fetchall()
    conn.close()
    return render_template("admin_extras.html", extras=extras)


@app.route("/admin/extras/new", methods=["POST"])
@owner_required
def new_extra():
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "").strip()
    if not name:
        flash("Extra needs a name.", "error")
        return redirect(url_for("admin_extras"))
    conn = get_db()
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM extras").fetchone()["m"]
    conn.execute(
        "INSERT INTO extras (name, price, sort_order) VALUES (?, ?, ?)",
        (name, float(price) if price else 0, max_order + 1),
    )
    conn.commit()
    conn.close()
    flash(f"{name} added.", "success")
    return redirect(url_for("admin_extras"))


@app.route("/admin/extras/<int:extra_id>/edit", methods=["POST"])
@owner_required
def edit_extra(extra_id):
    name = request.form.get("name", "").strip()
    price = request.form.get("price", "").strip()
    if not name:
        flash("Extra needs a name.", "error")
        return redirect(url_for("admin_extras"))
    conn = get_db()
    conn.execute(
        "UPDATE extras SET name = ?, price = ? WHERE id = ?",
        (name, float(price) if price else 0, extra_id),
    )
    conn.commit()
    conn.close()
    flash(f"{name} updated.", "success")
    return redirect(url_for("admin_extras"))


@app.route("/admin/extras/<int:extra_id>/toggle", methods=["POST"])
@owner_required
def toggle_extra(extra_id):
    conn = get_db()
    extra = conn.execute("SELECT * FROM extras WHERE id = ?", (extra_id,)).fetchone()
    if not extra:
        conn.close()
        abort(404)
    conn.execute("UPDATE extras SET active = ? WHERE id = ?", (0 if extra["active"] else 1, extra_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_extras"))


@app.route("/admin/extras/<int:extra_id>/delete", methods=["POST"])
@owner_required
def delete_extra(extra_id):
    conn = get_db()
    conn.execute("DELETE FROM extras WHERE id = ?", (extra_id,))
    conn.commit()
    conn.close()
    flash("Extra removed.", "success")
    return redirect(url_for("admin_extras"))


@app.route("/admin/rooms/new", methods=["GET", "POST"])
@owner_required
def new_room():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        max_occupancy = request.form.get("max_occupancy", "").strip()
        price_per_night = request.form.get("price_per_night", "").strip()
        min_nights = request.form.get("min_nights", "").strip()
        amenities = request.form.get("amenities", "").strip()

        if not name:
            flash("Room name is required.", "error")
            return render_template("room_form.html", room=None)

        photo_filename = save_room_photo(request.files.get("photo"))
        if photo_filename is False:
            flash("Photo must be a PNG or JPEG.", "error")
            return render_template("room_form.html", room=None)

        conn = get_db()
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM rooms").fetchone()["m"]
        conn.execute(
            """INSERT INTO rooms (name, description, max_occupancy, price_per_night, min_nights, export_token,
               sort_order, photo_filename, amenities) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, description, int(max_occupancy) if max_occupancy.isdigit() else 2,
             float(price_per_night) if price_per_night else 0,
             int(min_nights) if min_nights.isdigit() and int(min_nights) > 0 else 1,
             secrets.token_urlsafe(20), max_order + 1, photo_filename, amenities or None),
        )
        conn.commit()
        conn.close()
        flash(f"{name} added.", "success")
        return redirect(url_for("admin_rooms"))

    return render_template("room_form.html", room=None)


@app.route("/admin/rooms/<int:room_id>/edit", methods=["GET", "POST"])
@owner_required
def edit_room(room_id):
    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room:
        conn.close()
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        max_occupancy = request.form.get("max_occupancy", "").strip()
        price_per_night = request.form.get("price_per_night", "").strip()
        min_nights = request.form.get("min_nights", "").strip()
        amenities = request.form.get("amenities", "").strip()
        active = 1 if request.form.get("active") == "on" else 0

        photo_filename = save_room_photo(request.files.get("photo"))
        if photo_filename is False:
            conn.close()
            flash("Photo must be a PNG or JPEG.", "error")
            return render_template("room_form.html", room=room)
        if photo_filename is None:
            photo_filename = room["photo_filename"]

        conn.execute(
            """UPDATE rooms SET name=?, description=?, max_occupancy=?, price_per_night=?, min_nights=?,
               active=?, photo_filename=?, amenities=? WHERE id=?""",
            (name, description, int(max_occupancy) if max_occupancy.isdigit() else room["max_occupancy"],
             float(price_per_night) if price_per_night else 0,
             int(min_nights) if min_nights.isdigit() and int(min_nights) > 0 else room["min_nights"],
             active, photo_filename, amenities or None, room_id),
        )

        gallery_names = save_room_photos_multi(request.files.getlist("gallery_photos"))
        if gallery_names:
            max_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) AS m FROM room_photos WHERE room_id = ?", (room_id,)
            ).fetchone()["m"]
            now = datetime.now(timezone.utc).isoformat()
            for i, filename in enumerate(gallery_names):
                conn.execute(
                    "INSERT INTO room_photos (room_id, filename, sort_order, created_at) VALUES (?, ?, ?, ?)",
                    (room_id, filename, max_order + 1 + i, now),
                )

        conn.commit()
        conn.close()
        flash("Room updated.", "success")
        return redirect(url_for("admin_rooms"))

    gallery_photos = conn.execute(
        "SELECT * FROM room_photos WHERE room_id = ? ORDER BY sort_order, id", (room_id,)
    ).fetchall()
    rate_overrides = conn.execute(
        "SELECT * FROM room_rate_overrides WHERE room_id = ? ORDER BY start_date", (room_id,)
    ).fetchall()
    conn.close()
    return render_template("room_form.html", room=room, gallery_photos=gallery_photos, rate_overrides=rate_overrides)


@app.route("/admin/rooms/<int:room_id>/rates/new", methods=["POST"])
@owner_required
def new_room_rate_override(room_id):
    start_raw = request.form.get("start_date", "").strip()
    end_raw = request.form.get("end_date", "").strip()
    price_raw = request.form.get("price_per_night", "").strip()
    label = request.form.get("label", "").strip()
    start, end = parse_date(start_raw), parse_date(end_raw)

    if not start or not end or end < start:
        flash("Choose a valid date range for the rate override.", "error")
        return redirect(url_for("edit_room", room_id=room_id))
    try:
        price = float(price_raw)
    except ValueError:
        price = None
    if price is None or price <= 0:
        flash("Enter a valid nightly rate.", "error")
        return redirect(url_for("edit_room", room_id=room_id))

    conn = get_db()
    room = conn.execute("SELECT id FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room:
        conn.close()
        abort(404)
    conn.execute(
        """INSERT INTO room_rate_overrides (room_id, start_date, end_date, price_per_night, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (room_id, start.isoformat(), end.isoformat(), price, label or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Rate override added.", "success")
    return redirect(url_for("edit_room", room_id=room_id))


@app.route("/admin/rooms/<int:room_id>/rates/<int:rate_id>/delete", methods=["POST"])
@owner_required
def delete_room_rate_override(room_id, rate_id):
    conn = get_db()
    conn.execute("DELETE FROM room_rate_overrides WHERE id = ? AND room_id = ?", (rate_id, room_id))
    conn.commit()
    conn.close()
    flash("Rate override removed.", "success")
    return redirect(url_for("edit_room", room_id=room_id))


@app.route("/admin/rooms/<int:room_id>/photos/<int:photo_id>/delete", methods=["POST"])
@owner_required
def delete_room_photo(room_id, photo_id):
    conn = get_db()
    photo = conn.execute(
        "SELECT * FROM room_photos WHERE id = ? AND room_id = ?", (photo_id, room_id)
    ).fetchone()
    if not photo:
        conn.close()
        abort(404)
    path = os.path.join(ROOM_PHOTO_DIR, photo["filename"])
    if os.path.exists(path):
        os.remove(path)
    conn.execute("DELETE FROM room_photos WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()
    flash("Photo removed.", "success")
    return redirect(url_for("edit_room", room_id=room_id))


@app.route("/admin/rooms/<int:room_id>/delete", methods=["POST"])
@owner_required
def delete_room(room_id):
    conn = get_db()
    in_use = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE room_id = ?", (room_id,)).fetchone()["c"]
    if in_use:
        conn.close()
        flash("Can't delete a room with existing bookings — deactivate it instead.", "error")
        return redirect(url_for("admin_rooms"))
    conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    conn.commit()
    conn.close()
    flash("Room removed.", "success")
    return redirect(url_for("admin_rooms"))


@app.route("/admin/rooms/<int:room_id>/ical-sources/new", methods=["POST"])
@owner_required
def new_ical_source(room_id):
    label = request.form.get("label", "").strip()
    url_value = request.form.get("url", "").strip()
    if not label or not url_value:
        flash("Both a label and a URL are required.", "error")
        return redirect(url_for("admin_rooms"))
    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room:
        conn.close()
        abort(404)
    cur = conn.execute("INSERT INTO ical_sources (room_id, label, url) VALUES (?, ?, ?)", (room_id, label, url_value))
    conn.commit()
    source = conn.execute("SELECT * FROM ical_sources WHERE id = ?", (cur.lastrowid,)).fetchone()
    ok = sync_ical_source(conn, source)
    conn.close()
    flash("Calendar added and synced." if ok else "Calendar added, but the first sync failed — check the URL.",
          "success" if ok else "error")
    return redirect(url_for("admin_rooms"))


@app.route("/admin/ical-sources/<int:source_id>/sync", methods=["POST"])
@owner_required
def sync_ical_source_now(source_id):
    conn = get_db()
    source = conn.execute("SELECT * FROM ical_sources WHERE id = ?", (source_id,)).fetchone()
    if not source:
        conn.close()
        abort(404)
    ok = sync_ical_source(conn, source)
    conn.close()
    flash("Synced." if ok else "Sync failed — check the URL.", "success" if ok else "error")
    return redirect(url_for("admin_rooms"))


@app.route("/admin/ical-sources/<int:source_id>/delete", methods=["POST"])
@owner_required
def delete_ical_source(source_id):
    conn = get_db()
    conn.execute("DELETE FROM ical_sources WHERE id = ?", (source_id,))
    conn.commit()
    conn.close()
    flash("Calendar removed.", "success")
    return redirect(url_for("admin_rooms"))


@app.route("/admin/rooms/<int:room_id>/blocks/new", methods=["POST"])
@owner_required
def new_room_block(room_id):
    start_raw = request.form.get("start_date", "").strip()
    end_raw = request.form.get("end_date", "").strip()
    reason = request.form.get("reason", "").strip()
    start, end = parse_date(start_raw), parse_date(end_raw)
    if not start or not end or end <= start:
        flash("Choose a valid date range to block.", "error")
        return redirect(url_for("admin_rooms"))

    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if not room:
        conn.close()
        abort(404)
    conflicting = conn.execute(
        """SELECT reference_code FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
           AND arrival_date < ? AND departure_date > ?""",
        (room_id, end.isoformat(), start.isoformat()),
    ).fetchone()
    if conflicting:
        conn.close()
        flash(f"Can't block those dates — booking {conflicting['reference_code']} already covers part of that range.", "error")
        return redirect(url_for("admin_rooms"))

    conn.execute(
        "INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (room_id, start.isoformat(), end.isoformat(), reason or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash(f"{room['name']} blocked {start.isoformat()} to {end.isoformat()}.", "success")
    return redirect(url_for("admin_rooms"))


@app.route("/admin/blocks/<int:block_id>/delete", methods=["POST"])
@owner_required
def delete_room_block(block_id):
    conn = get_db()
    conn.execute("DELETE FROM room_blocks WHERE id = ?", (block_id,))
    conn.commit()
    conn.close()
    flash("Block removed.", "success")
    return redirect(url_for("admin_rooms"))


@app.route("/admin/feedback")
@owner_required
def admin_feedback():
    conn = get_db()
    entries = conn.execute(
        """SELECT guest_feedback.*, bookings.reference_code, rooms.name AS room_name
           FROM guest_feedback
           LEFT JOIN bookings ON bookings.id = guest_feedback.booking_id
           LEFT JOIN rooms ON rooms.id = bookings.room_id
           ORDER BY guest_feedback.submitted_at DESC"""
    ).fetchall()
    avg_rating = conn.execute("SELECT AVG(rating) AS a FROM guest_feedback").fetchone()["a"]
    conn.close()
    return render_template(
        "admin_feedback.html", entries=entries,
        avg_rating=round(avg_rating, 1) if avg_rating is not None else None,
    )


@app.route("/admin/feedback/<int:feedback_id>/toggle-featured", methods=["POST"])
@owner_required
def toggle_feedback_featured(feedback_id):
    conn = get_db()
    row = conn.execute("SELECT featured FROM guest_feedback WHERE id = ?", (feedback_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute("UPDATE guest_feedback SET featured = ? WHERE id = ?", (0 if row["featured"] else 1, feedback_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_feedback"))


@app.route("/admin/feedback/export.csv")
@owner_required
def export_feedback_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT guest_feedback.*, bookings.reference_code, rooms.name AS room_name
           FROM guest_feedback
           LEFT JOIN bookings ON bookings.id = guest_feedback.booking_id
           LEFT JOIN rooms ON rooms.id = bookings.room_id
           ORDER BY guest_feedback.submitted_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["guest_name", "room_name", "reference_code", "rating", "comment", "submitted_at"]
    return csv_response(fieldnames, rows, "guest_feedback.csv")


@app.route("/admin/workshops/feedback")
@owner_required
def admin_workshop_feedback():
    conn = get_db()
    entries = conn.execute(
        """SELECT workshop_feedback.*, workshop_bookings.reference_code, workshops.title
           FROM workshop_feedback
           LEFT JOIN workshop_bookings ON workshop_bookings.id = workshop_feedback.workshop_booking_id
           LEFT JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           LEFT JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           ORDER BY workshop_feedback.submitted_at DESC"""
    ).fetchall()
    avg_rating = conn.execute("SELECT AVG(rating) AS a FROM workshop_feedback").fetchone()["a"]
    conn.close()
    return render_template(
        "admin_workshop_feedback.html", entries=entries,
        avg_rating=round(avg_rating, 1) if avg_rating is not None else None,
    )


@app.route("/admin/workshops/feedback/<int:feedback_id>/toggle-featured", methods=["POST"])
@owner_required
def toggle_workshop_feedback_featured(feedback_id):
    conn = get_db()
    row = conn.execute("SELECT featured FROM workshop_feedback WHERE id = ?", (feedback_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute("UPDATE workshop_feedback SET featured = ? WHERE id = ?", (0 if row["featured"] else 1, feedback_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_workshop_feedback"))


@app.route("/admin/workshops/feedback/export.csv")
@owner_required
def export_workshop_feedback_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT workshop_feedback.*, workshop_bookings.reference_code, workshops.title
           FROM workshop_feedback
           LEFT JOIN workshop_bookings ON workshop_bookings.id = workshop_feedback.workshop_booking_id
           LEFT JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           LEFT JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           ORDER BY workshop_feedback.submitted_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["guest_name", "title", "reference_code", "rating", "comment", "submitted_at"]
    return csv_response(fieldnames, rows, "workshop_feedback.csv")


@app.route("/admin/bookings")
@owner_required
def admin_bookings():
    status_filter = request.args.get("status", "").strip()
    room_filter = request.args.get("room_id", "").strip()
    q = request.args.get("q", "").strip()

    conn = get_db()
    rooms = conn.execute("SELECT * FROM rooms ORDER BY sort_order, name").fetchall()

    all_bookings = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id
           ORDER BY (bookings.status = 'pending') DESC,
                    (bookings.departure_date < date('now')) ASC,
                    bookings.arrival_date"""
    ).fetchall()

    bookings = all_bookings
    if status_filter:
        bookings = [b for b in bookings if b["status"] == status_filter]
    if room_filter.isdigit():
        bookings = [b for b in bookings if b["room_id"] == int(room_filter)]
    if q:
        needle = q.lower()
        bookings = [
            b for b in bookings
            if needle in (b["guest_name"] or "").lower()
            or needle in (b["guest_email"] or "").lower()
            or needle in (b["reference_code"] or "").lower()
        ]

    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    week_ahead_iso = (today + timedelta(days=7)).isoformat()
    active = [b for b in all_bookings if b["status"] in ("pending", "confirmed")]
    arriving_today = [b for b in active if b["arrival_date"] == today_iso]
    departing_today = [b for b in active if b["departure_date"] == today_iso]
    arriving_this_week = [b for b in active if today_iso < b["arrival_date"] <= week_ahead_iso]

    counts = {
        "pending": sum(1 for b in all_bookings if b["status"] == "pending"),
        "confirmed": sum(1 for b in all_bookings if b["status"] == "confirmed"),
        "total_rooms": len(rooms),
    }
    confirmed_counts_by_email = {}
    confirmed_spend_by_email = {}
    for b in all_bookings:
        if b["status"] == "confirmed" and b["guest_email"]:
            confirmed_counts_by_email[b["guest_email"]] = confirmed_counts_by_email.get(b["guest_email"], 0) + 1
            confirmed_spend_by_email[b["guest_email"]] = confirmed_spend_by_email.get(b["guest_email"], 0) + (b["total_price"] or 0)
    returning_emails = {email for email, count in confirmed_counts_by_email.items() if count > 1}
    employees = conn.execute("SELECT * FROM users WHERE role = 'employee' ORDER BY name").fetchall()
    scheduled_by_date = {}
    for row in conn.execute("SELECT shift_date, user_id FROM shifts WHERE shift_date >= ?", (today_iso,)).fetchall():
        scheduled_by_date.setdefault(row["shift_date"], set()).add(row["user_id"])
    # How much has already been handed back per booking, so the refund form can
    # show what's actually left rather than the original total.
    refunded_by_booking = {
        r["booking_id"]: round(r["total"], 2) for r in conn.execute(
            "SELECT booking_id, SUM(amount) AS total FROM refunds WHERE category = 'room' GROUP BY booking_id"
        ).fetchall()
    }
    conn.close()
    return render_template(
        "admin_bookings.html", bookings=bookings, counts=counts, rooms=rooms, employees=employees,
        status_filter=status_filter, room_filter=room_filter, q=q,
        arriving_today=arriving_today, departing_today=departing_today, arriving_this_week=arriving_this_week,
        returning_emails=returning_emails, confirmed_spend_by_email=confirmed_spend_by_email,
        scheduled_by_date=scheduled_by_date, today_iso=today_iso,
        refunded_by_booking=refunded_by_booking,
    )


@app.route("/admin/bookings/guest/<email>")
@owner_required
def guest_booking_history(email):
    conn = get_db()
    bookings = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id
           WHERE guest_email = ? ORDER BY arrival_date DESC""",
        (email,),
    ).fetchall()
    dinners = conn.execute(
        "SELECT * FROM restaurant_bookings WHERE guest_email = ? ORDER BY dinner_date DESC", (email,)
    ).fetchall()
    workshop_regs = conn.execute(
        """SELECT workshop_bookings.*, workshops.title AS workshop_title, workshop_sessions.start_date
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.guest_email = ? ORDER BY workshop_sessions.start_date DESC""",
        (email,),
    ).fetchall()
    events = conn.execute(
        "SELECT * FROM event_inquiries WHERE contact_email = ? ORDER BY created_at DESC", (email,)
    ).fetchall()
    promo_redemptions = conn.execute(
        """SELECT promo_code_redemptions.*, promo_codes.code
           FROM promo_code_redemptions JOIN promo_codes ON promo_codes.id = promo_code_redemptions.promo_code_id
           WHERE promo_code_redemptions.guest_email = ? ORDER BY promo_code_redemptions.redeemed_at DESC""",
        (email,),
    ).fetchall()
    profile = conn.execute("SELECT * FROM guests WHERE email = ?", (email,)).fetchone()
    conn.close()
    # A profile with no activity yet is still a legitimate page to open.
    if not bookings and not dinners and not workshop_regs and not events and not profile:
        abort(404)
    lifetime_spend = sum(b["total_price"] or 0 for b in bookings if b["status"] == "confirmed")
    lifetime_spend += sum(d["total_price"] or 0 for d in dinners if d["status"] == "confirmed")
    lifetime_spend += sum(w["total_price"] or 0 for w in workshop_regs if w["status"] == "confirmed")
    lifetime_spend += sum(e["quoted_price"] or 0 for e in events if e["status"] == "confirmed")
    return render_template(
        "guest_booking_history.html", email=email, profile=profile, bookings=bookings, dinners=dinners,
        workshop_regs=workshop_regs, events=events, promo_redemptions=promo_redemptions,
        lifetime_spend=lifetime_spend,
    )


def confirm_booking_by_id(conn, booking_id):
    """Core confirm logic shared by the single-booking and bulk-confirm
    routes. Only acts on a still-pending booking (so a stray double-submit
    or a stale bulk selection can't re-send the confirmation email), and
    re-validates the room is still actually free for those dates before
    committing to 'confirmed' — two overlapping pending requests for the
    same room can both exist (nothing blocks *requesting* the same dates
    twice), so this is the point where a double-booking would otherwise
    slip through un-noticed. Returns (ok, reason); reason is set on
    failure. Leaves commit/close to the caller."""
    booking = conn.execute("SELECT * FROM bookings WHERE id = ? AND status = 'pending'", (booking_id,)).fetchone()
    if not booking:
        return False, "not found or not pending"
    arrival, departure = parse_date(booking["arrival_date"]), parse_date(booking["departure_date"])
    available, conflict_reason = is_range_available(
        conn, booking["room_id"], arrival, departure, exclude_booking_id=booking_id, include_pending=False
    )
    if not available:
        return False, conflict_reason
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (booking["room_id"],)).fetchone()

    # Find-or-create the guest's standing profile, keyed on email. This used to
    # insert a fresh per-stay row every time, so a returning guest accumulated a
    # new "guest" per visit and their history was invisible. The profile holds
    # nothing stay-specific -- dates and party size stay on the booking.
    # guest_email is NOT NULL but '' satisfies that, and an empty string is not
    # an identity -- matching on it would either fail or, worse, mint a brand
    # new profile on every single confirm, reinstating the per-stay pile-up this
    # refactor removed. Lower-cased so casing can't split one person in two.
    guest_email = (booking["guest_email"] or "").strip().lower() or None
    guest_id = booking["linked_guest_id"]
    if not guest_id and guest_email:
        existing = conn.execute(
            "SELECT id FROM guests WHERE email = ? COLLATE NOCASE", (guest_email,)
        ).fetchone()
        guest_id = existing["id"] if existing else None
    if not guest_id:
        cur = conn.execute(
            """INSERT INTO guests (name, email, phone, notes, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (booking["guest_name"], guest_email,
             booking["guest_phone"] or None, None,
             datetime.now(timezone.utc).isoformat()),
        )
        guest_id = cur.lastrowid

    cur = conn.execute(
        "UPDATE bookings SET status='confirmed', decided_at=?, linked_guest_id=? WHERE id=? AND status='pending'",
        (datetime.now(timezone.utc).isoformat(), guest_id, booking_id),
    )
    if cur.rowcount == 0:
        # Lost a race with another confirm/decline for this same booking —
        # the guest record insert above is harmless to leave in place
        # (guest_id just goes unused), but the email below must not fire.
        return False, "not found or not pending"
    send_email(
        booking["guest_email"],
        f"Booking confirmed — {room['name']}",
        f"Hi {booking['guest_name']},\n\nYour booking for {room['name']} "
        f"({format_date_human(booking['arrival_date'])} to {format_date_human(booking['departure_date'])}) "
        f"is confirmed. We look forward to hosting you.\n\n"
        f"Reference code: {booking['reference_code']}\n"
        f"Check in online — confirm your arrival time, tell us about any requests"
        f"{' and your airport transfer details' if booking_has_transfer(booking) else ''}: "
        f"{url_for('guest_checkin', manage_token=booking['manage_token'], _external=True)}\n\n"
        f"— Château de Gudanes",
        ics_content=generate_booking_ics(booking, room["name"]),
        ics_filename=f"{booking['reference_code']}.ics",
    )
    return True, None


@app.route("/admin/bookings/<int:booking_id>/confirm", methods=["POST"])
@owner_required
def confirm_booking(booking_id):
    conn = get_db()
    ok, reason = confirm_booking_by_id(conn, booking_id)
    if not ok:
        conn.commit()
        conn.close()
        if reason == "not found or not pending":
            abort(404)
        flash(f"Could not confirm: {reason}", "error")
        return redirect(url_for("admin_bookings"))
    conn.commit()
    conn.close()
    flash("Booking confirmed and added to the guest list.", "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/bulk-confirm", methods=["POST"])
@owner_required
def bulk_confirm_bookings():
    booking_ids = [int(i) for i in request.form.getlist("booking_ids") if i.isdigit()]
    conn = get_db()
    confirmed = 0
    conflicts = 0
    for bid in booking_ids:
        ok, reason = confirm_booking_by_id(conn, bid)
        if ok:
            confirmed += 1
        elif reason != "not found or not pending":
            conflicts += 1
    conn.commit()
    conn.close()
    msg = f"Confirmed {confirmed} booking{'' if confirmed == 1 else 's'}."
    if conflicts:
        msg += f" Skipped {conflicts} due to a date conflict with another booking."
    flash(msg, "success" if not conflicts else "error")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/<int:booking_id>/checkout", methods=["POST"])
@owner_required
def checkout_booking(booking_id):
    assigned_to = request.form.get("assigned_to_user_id", "")
    if not assigned_to.isdigit():
        flash("Choose who the turnover task goes to.", "error")
        return redirect(url_for("admin_bookings"))

    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (booking["room_id"],)).fetchone()

    now = datetime.now(timezone.utc)
    # A single conditional UPDATE is the whole guard — no separate
    # check-then-write race window, since a double-click (or two staff
    # checking the same guest out at once) would otherwise duplicate the
    # entire turnover checklist and double-send the feedback-request email.
    cur = conn.execute(
        "UPDATE bookings SET checked_out_at = ? WHERE id = ? AND checked_out_at IS NULL",
        (now.isoformat(), booking_id),
    )
    if cur.rowcount == 0:
        conn.close()
        flash("This booking was already checked out.", "error")
        return redirect(url_for("admin_bookings"))
    room_note = f"{booking['guest_name']} checked out, party of {booking['party_size']}."
    for i, title in enumerate(CHECKOUT_CHECKLIST):
        conn.execute(
            """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date, created_at, origin)
               VALUES (?, ?, ?, 'high', ?, ?, 'checklist')""",
            (int(assigned_to), f"{room['name']}: {title}", room_note,
             now.date().isoformat(), now.isoformat()),
        )
    conn.commit()
    conn.close()
    if booking["guest_email"]:
        feedback_url = url_for("guest_feedback", token=booking["manage_token"], _external=True)
        send_email(
            booking["guest_email"], "How was your stay at Château de Gudanes?",
            f"Hi {booking['guest_name']},\n\n"
            f"We hope you enjoyed your stay. If you have a moment, we'd love to hear how it went:\n"
            f"{feedback_url}\n\n"
            f"— Château de Gudanes",
        )
    flash(f"Checked out. {len(CHECKOUT_CHECKLIST)} turnover tasks assigned for {room['name']}.", "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/<int:booking_id>/prepare-arrival", methods=["POST"])
@owner_required
def prepare_arrival(booking_id):
    assigned_to = request.form.get("assigned_to_user_id", "")
    if not assigned_to.isdigit():
        flash("Choose who the prep tasks go to.", "error")
        return redirect(url_for("admin_bookings"))

    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id = ? AND status = 'confirmed'", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (booking["room_id"],)).fetchone()

    now = datetime.now(timezone.utc)
    cur = conn.execute(
        "UPDATE bookings SET arrival_prepped_at = ? WHERE id = ? AND arrival_prepped_at IS NULL",
        (now.isoformat(), booking_id),
    )
    if cur.rowcount == 0:
        conn.close()
        flash("This booking was already prepped.", "error")
        return redirect(url_for("admin_bookings"))
    room_note = f"{booking['guest_name']} arriving {booking['arrival_date']}, party of {booking['party_size']}."
    if booking["special_requests"]:
        room_note += f" Requests: {booking['special_requests']}"
    party_size = booking["party_size"] or 1
    for item in ARRIVAL_PREP_CHECKLIST:
        title = item.format(n=party_size) if "{n}" in item else item
        conn.execute(
            """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date, created_at, origin)
               VALUES (?, ?, ?, 'high', ?, ?, 'checklist')""",
            (int(assigned_to), f"{room['name']}: {title}", room_note, booking["arrival_date"], now.isoformat()),
        )
    conn.commit()
    conn.close()
    flash(f"{len(ARRIVAL_PREP_CHECKLIST)} arrival prep tasks assigned for {room['name']}.", "success")
    return redirect(url_for("admin_bookings"))


def decline_booking_by_id(conn, booking_id):
    """Core decline logic shared by the single-booking and bulk-decline
    routes. Only acts on a still-pending booking, mirroring
    confirm_booking_by_id's guard against re-processing. Returns a
    (declined, refunded, refund_error) tuple; leaves commit/close to the
    caller."""
    booking = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ? AND bookings.status = 'pending'""",
        (booking_id,),
    ).fetchone()
    if not booking:
        return False, False, None
    cur = conn.execute(
        "UPDATE bookings SET status='declined', decided_at=? WHERE id=? AND status='pending'",
        (datetime.now(timezone.utc).isoformat(), booking_id),
    )
    if cur.rowcount == 0:
        # Lost a race with another confirm/decline — bail before attempting
        # a refund or sending an email a second time.
        return False, False, None

    was_paid = booking["payment_status"] == "paid"
    refunded, refund_error = refund_booking(conn, booking)
    refund_error = refund_error if was_paid else None
    refund_note = ""
    if refunded:
        refund_note = " Your payment has been refunded."
    elif booking["payment_status"] == "paid":
        refund_note = " We'll be in touch about your refund."

    send_email(
        booking["guest_email"],
        f"Booking request declined — {booking['room_name']}",
        f"Hi {booking['guest_name']},\n\nWe're not able to accommodate your request for {booking['room_name']} "
        f"({format_date_human(booking['arrival_date'])} to {format_date_human(booking['departure_date'])}).{refund_note}\n\n"
        f"Reference code: {booking['reference_code']}\n\n— Château de Gudanes",
    )
    return True, refunded, refund_error


@app.route("/admin/bookings/<int:booking_id>/decline", methods=["POST"])
@owner_required
def decline_booking(booking_id):
    conn = get_db()
    declined, refunded, refund_error = decline_booking_by_id(conn, booking_id)
    if not declined:
        conn.close()
        abort(404)
    conn.commit()
    booking = conn.execute("SELECT arrival_date, departure_date FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    notified = notify_room_waitlist_opening(conn, booking["arrival_date"], booking["departure_date"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_waitlist_entries(conn, booking["arrival_date"], booking["departure_date"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} overlapping dates — check the waitlist." if remaining else ""
    conn.close()
    flash("Booking declined." + (" Payment refunded." if refunded else (f" Refund failed: {refund_error}" if refund_error else "")) + waitlist_note, "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/bulk-decline", methods=["POST"])
@owner_required
def bulk_decline_bookings():
    booking_ids = [int(i) for i in request.form.getlist("booking_ids") if i.isdigit()]
    conn = get_db()
    declined_count = sum(1 for bid in booking_ids if decline_booking_by_id(conn, bid)[0])
    conn.commit()
    conn.close()
    flash(f"Declined {declined_count} booking{'' if declined_count == 1 else 's'}.", "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/bookings/<int:booking_id>/cancel", methods=["POST"])
@owner_required
def cancel_booking_admin(booking_id):
    conn = get_db()
    booking = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ?""",
        (booking_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE bookings SET status='cancelled', decided_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), booking_id),
    )
    conn.commit()

    # Cancelling deliberately does NOT refund. House terms are non-refundable,
    # and refunds here are a case-by-case decision — so the money only moves
    # when the owner explicitly says so on the refund form. This used to fire
    # a full Stripe refund automatically, which handed back every euro on a
    # mis-click and contradicted the stated policy.
    still_held = refundable_amount(conn, "room", booking) if booking["payment_status"] == "paid" else 0

    send_email(
        booking["guest_email"],
        f"Booking cancelled — {booking['room_name']}",
        f"Hi {booking['guest_name']},\n\nYour booking for {booking['room_name']} "
        f"({format_date_human(booking['arrival_date'])} to {format_date_human(booking['departure_date'])}) has been cancelled.\n\n"
        f"Reference code: {booking['reference_code']}\n\n— Château de Gudanes",
    )
    notified = notify_room_waitlist_opening(conn, booking["arrival_date"], booking["departure_date"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_waitlist_entries(conn, booking["arrival_date"], booking["departure_date"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} overlapping dates — check the waitlist." if remaining else ""
    conn.close()
    money_note = (f" €{still_held:.2f} is still held — issue a refund from the booking if you want to give any of it back."
                  if still_held > 0 else "")
    flash("Booking cancelled." + money_note + waitlist_note, "success")
    return redirect(url_for("admin_bookings"))


@app.route("/admin/refunds")
@owner_required
def admin_refunds():
    """Every refund ever issued, in one place. The house terms are
    non-refundable and each refund is a discretionary call, so this doubles as
    the record of why each exception was made."""
    conn = get_db()
    category = request.args.get("category", "").strip()
    query = """SELECT refunds.*, users.name AS refunded_by_name FROM refunds
               LEFT JOIN users ON users.id = refunds.refunded_by_user_id"""
    params = []
    if category in ("room", "restaurant", "workshop", "event"):
        query += " WHERE refunds.category = ?"
        params.append(category)
    query += " ORDER BY refunds.created_at DESC"
    refunds = conn.execute(query, params).fetchall()

    totals = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS all_time,
                  COALESCE(SUM(CASE WHEN created_at >= ? THEN amount END), 0) AS last_30,
                  COUNT(*) AS count FROM refunds""",
        ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),),
    ).fetchone()
    by_category = conn.execute(
        "SELECT category, COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM refunds GROUP BY category"
    ).fetchall()
    conn.close()
    return render_template("admin_refunds.html", refunds=refunds, totals=totals,
                           by_category=by_category, category=category)


@app.route("/admin/refunds/export.csv")
@owner_required
def export_refunds_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT refunds.*, users.name AS refunded_by_name FROM refunds
           LEFT JOIN users ON users.id = refunds.refunded_by_user_id
           ORDER BY refunds.created_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["created_at", "category", "reference_code", "guest_name", "guest_email",
                  "amount", "reason", "method", "stripe_refund_id", "refunded_by_name"]
    return csv_response(fieldnames, rows, "refunds.csv")


@app.route("/admin/bookings/<int:booking_id>/edit", methods=["GET", "POST"])
@owner_required
def edit_booking(booking_id):
    conn = get_db()
    booking = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name, rooms.price_per_night, rooms.max_occupancy
           FROM bookings JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ?""",
        (booking_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    if booking["status"] not in ("pending", "confirmed"):
        conn.close()
        flash("Only pending or confirmed bookings can be edited.", "error")
        return redirect(url_for("admin_bookings"))

    if request.method == "POST":
        arrival_raw = request.form.get("arrival_date", "").strip()
        departure_raw = request.form.get("departure_date", "").strip()
        party_size_raw = request.form.get("party_size", "").strip()
        guest_phone = request.form.get("guest_phone", "").strip()
        special_requests = request.form.get("special_requests", "").strip()

        arrival, departure = parse_date(arrival_raw), parse_date(departure_raw)
        party_size = int(party_size_raw) if party_size_raw.isdigit() else None

        error = None
        if not arrival or not departure:
            error = "Choose valid arrival and departure dates."
        elif not party_size or party_size < 1:
            error = "Party size is required."
        elif party_size > booking["max_occupancy"]:
            error = f"This room sleeps up to {booking['max_occupancy']}."
        else:
            ok, reason = is_range_available(conn, booking["room_id"], arrival, departure, exclude_booking_id=booking_id)
            if not ok:
                error = reason

        if error:
            flash(error, "error")
            conn.close()
            return render_template("edit_booking.html", booking=booking)

        room_for_pricing = conn.execute("SELECT * FROM rooms WHERE id = ?", (booking["room_id"],)).fetchone()
        old_arrival, old_departure = parse_date(booking["arrival_date"]), parse_date(booking["departure_date"])
        old_room_portion = compute_room_total(conn, room_for_pricing, old_arrival, old_departure) if old_arrival and old_departure else 0
        extras_portion = (booking["total_price"] or 0) - old_room_portion
        new_total = compute_room_total(conn, room_for_pricing, arrival, departure) + extras_portion
        new_total = new_total or None

        conn.execute(
            """UPDATE bookings SET arrival_date=?, departure_date=?, party_size=?, guest_phone=?,
               special_requests=?, total_price=? WHERE id=?""",
            (arrival.isoformat(), departure.isoformat(), party_size, guest_phone or None,
             special_requests or None, new_total, booking_id),
        )
        conn.commit()

        # No guest-row date sync any more: the booking IS the record of when
        # this stay is, so there is nothing left to keep in step.

        send_email(
            booking["guest_email"],
            f"Booking updated — {booking['room_name']}",
            f"Hi {booking['guest_name']},\n\nYour booking for {booking['room_name']} has been updated:\n\n"
            f"Arrival: {format_date_human(arrival.isoformat())}\n"
            f"Departure: {format_date_human(departure.isoformat())}\n"
            f"Party size: {party_size}\n\n"
            f"Reference code: {booking['reference_code']}\n"
            f"Check in / manage your booking: {url_for('guest_checkin', manage_token=booking['manage_token'], _external=True)}\n\n"
            f"— Château de Gudanes",
        )
        conn.close()
        flash("Booking updated.", "success")
        return redirect(url_for("admin_bookings"))

    conn.close()
    return render_template("edit_booking.html", booking=booking)


@app.route("/admin/bookings/<int:booking_id>/refund", methods=["POST"])
@owner_required
def refund_booking_admin(booking_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    amount = request.form.get("amount", "").strip()
    reason = request.form.get("reason", "").strip()
    method = request.form.get("method", "stripe").strip() or "stripe"
    # A blank amount means "all of it" — the common case, and it saves
    # re-typing a figure the page already shows.
    if not amount:
        amount = refundable_amount(conn, "room", booking)
    ok, error = issue_refund(conn, "room", booking, amount, reason, method=method,
                             user_id=current_user()["id"])
    if ok:
        log_audit(conn, "refund_issued", target=f"room booking {booking['reference_code']}",
                  details=f"€{float(amount):.2f} — {reason}")
        conn.commit()
    conn.close()
    flash(f"Refund of €{float(amount):.2f} recorded." if ok else f"Refund failed: {error}",
          "success" if ok else "error")
    return redirect(request.referrer or url_for("admin_bookings"))


@app.route("/admin/workshops/registrations/<int:registration_id>/refund", methods=["POST"])
@owner_required
def refund_workshop_admin(registration_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (registration_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    amount = request.form.get("amount", "").strip()
    reason = request.form.get("reason", "").strip()
    method = request.form.get("method", "stripe").strip() or "stripe"
    if not amount:
        amount = refundable_amount(conn, "workshop", booking)
    ok, error = issue_refund(conn, "workshop", booking, amount, reason, method=method,
                             user_id=current_user()["id"])
    if ok:
        log_audit(conn, "refund_issued", target=f"workshop booking {booking['reference_code']}",
                  details=f"€{float(amount):.2f} — {reason}")
        conn.commit()
    conn.close()
    flash(f"Refund of €{float(amount):.2f} recorded." if ok else f"Refund failed: {error}",
          "success" if ok else "error")
    return redirect(request.referrer or url_for("admin_workshop_registrations"))


# ---------------------------------------------------------------------------
# CSV export — for handing records to an accountant or keeping outside the
# app. Plain csv.DictWriter, nothing fancy.
# ---------------------------------------------------------------------------

CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe_cell(value):
    """Neutralizes CSV/Excel formula injection: a guest name or note field
    coming straight from a public, unauthenticated form (e.g. '=cmd|...')
    would otherwise execute as a formula the moment the owner opens the
    exported file in Excel. A leading apostrophe is the standard fix —
    forces Excel to treat the cell as text. Only strings are touched, so
    numeric columns are untouched."""
    if isinstance(value, str) and value.startswith(CSV_FORMULA_TRIGGERS):
        return "'" + value
    return value


def csv_response(fieldnames, rows, filename):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: csv_safe_cell(row[k]) for k in fieldnames})
    return app.response_class(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/admin/bookings/export.csv")
@owner_required
def export_bookings_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
           JOIN rooms ON rooms.id = bookings.room_id ORDER BY arrival_date"""
    ).fetchall()
    conn.close()
    fieldnames = ["reference_code", "room_name", "guest_name", "guest_email", "guest_phone",
                  "arrival_date", "departure_date", "party_size", "status", "payment_status",
                  "total_price", "extras_summary", "special_requests", "created_at"]
    return csv_response(fieldnames, rows, "bookings.csv")


# ---------------------------------------------------------------------------
# Restaurant admin — reservations queue + settings. Confirm/decline mirrors
# the room-booking admin flow; capacity is checked at request time (so two
# pending requests can't jointly overcommit past the nightly cap) and
# flagged rather than blocked at confirm time, since a human running a
# 20-seat room should have the final call, not the app.
# ---------------------------------------------------------------------------

def restaurant_labor_cost(conn, month_start, month_end):
    """Estimated labor cost for staff assigned to work a dinner service in
    the given range — same best-effort hours x rate logic as the general
    timesheet cost estimate (see estimated_hourly_cost), so salaried staff
    or anyone without a clean hourly rate contribute 0 to the total rather
    than blocking it. Returns (total_cost, count_of_shifts_not_estimated)
    so the profit-share view can flag when the number is incomplete."""
    rows = conn.execute(
        """SELECT restaurant_shifts.estimated_hours, users.pay_rate, users.pay_type
           FROM restaurant_shifts JOIN users ON users.id = restaurant_shifts.user_id
           WHERE restaurant_shifts.dinner_date >= ? AND restaurant_shifts.dinner_date < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    total = 0.0
    unestimated = 0
    for row in rows:
        cost = estimated_hourly_cost(row["estimated_hours"] or 0, row["pay_rate"], row["pay_type"])
        if cost is None:
            unestimated += 1
        else:
            total += cost
    return round(total, 2), unestimated


def restaurant_profit_share(conn, year, month):
    """Revenue (confirmed dinner reservations) minus costs — approved
    restaurant-tagged expenses plus estimated staffing labor cost — for a
    calendar month, split by the configured percentage. Expense costs use
    approved OR paid so a month's number doesn't jump the moment the owner
    marks something paid — both mean "the owner signed off on this cost"."""
    month_start = date(year, month, 1)
    month_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    revenue = conn.execute(
        "SELECT COALESCE(SUM(total_price), 0) AS t FROM restaurant_bookings "
        "WHERE status = 'confirmed' AND dinner_date >= ? AND dinner_date < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["t"]
    # Refunded dinners are not revenue, and this figure decides real money:
    # the chef is paid a percentage of it. Without this, refunding €1,500 of
    # dinners after (say) a kitchen closure still paid the chef their share of
    # that €1,500.
    dinner_refunds = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM refunds "
        "WHERE category = 'restaurant' AND created_at >= ? AND created_at < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["t"]
    revenue = round(revenue - dinner_refunds, 2)
    expense_costs = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM expenses WHERE restaurant_related = 1 "
        "AND status IN ('approved', 'paid') AND submitted_at >= ? AND submitted_at < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["t"]
    labor_cost, unestimated_labor_shifts = restaurant_labor_cost(conn, month_start, month_end)
    costs = expense_costs + labor_cost
    settings = get_restaurant_settings(conn)
    share_pct = settings["profit_share_percent"] if settings else 50
    profit = revenue - costs
    chef_share = profit * (share_pct / 100)
    owner_share = profit - chef_share
    return {
        "month_start": month_start, "revenue": revenue, "costs": costs, "expense_costs": expense_costs,
        "labor_cost": labor_cost, "unestimated_labor_shifts": unestimated_labor_shifts, "profit": profit,
        "share_pct": share_pct, "chef_share": chef_share, "owner_share": owner_share,
    }


@app.route("/admin/restaurant")
@owner_required
def admin_restaurant():
    conn = get_db()
    period = period_from_request()
    overview = restaurant_overview(conn, period, datetime.now(timezone.utc).date())
    status_filter = request.args.get("status", "")
    query = "SELECT * FROM restaurant_bookings"
    params = []
    if status_filter:
        query += " WHERE status = ?"
        params.append(status_filter)
    # Past dinners sorted to the top under a plain dinner_date ASC, so the
    # first thing on screen was the oldest history. Still-relevant service
    # first, history after it, each in sensible order.
    query += " ORDER BY (dinner_date < date('now')) ASC, dinner_date, created_at"
    reservations = conn.execute(query, params).fetchall()

    pending_count = conn.execute("SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status = 'pending'").fetchone()["c"]

    today = datetime.now(timezone.utc).date()
    no_show_count = conn.execute(
        "SELECT COUNT(*) AS c FROM restaurant_bookings WHERE no_show_at IS NOT NULL AND dinner_date >= ?",
        ((today - timedelta(days=30)).isoformat(),),
    ).fetchone()["c"]
    upcoming_covers = conn.execute(
        """SELECT dinner_date, COALESCE(SUM(party_size), 0) AS covers FROM restaurant_bookings
           WHERE status IN ('pending', 'confirmed') AND dinner_date >= ? AND dinner_date < ?
           GROUP BY dinner_date ORDER BY dinner_date""",
        (today.isoformat(), (today + timedelta(days=14)).isoformat()),
    ).fetchall()

    month_raw = request.args.get("month", "")
    try:
        year, month = map(int, month_raw.split("-"))
    except (ValueError, AttributeError):
        year, month = today.year, today.month
    profit = restaurant_profit_share(conn, year, month)
    prev_month = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    settings = get_restaurant_settings(conn)
    employees = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()

    upcoming_shifts = conn.execute(
        """SELECT restaurant_shifts.*, users.name AS employee_name FROM restaurant_shifts
           JOIN users ON users.id = restaurant_shifts.user_id
           WHERE dinner_date >= ? AND dinner_date < ? ORDER BY dinner_date, users.name""",
        (today.isoformat(), (today + timedelta(days=14)).isoformat()),
    ).fetchall()
    shifts_by_date = {}
    for s in upcoming_shifts:
        shifts_by_date.setdefault(s["dinner_date"], []).append(s)
    refunded_by_reservation = {
        r["booking_id"]: round(r["total"], 2) for r in conn.execute(
            "SELECT booking_id, SUM(amount) AS total FROM refunds WHERE category = 'restaurant' GROUP BY booking_id"
        ).fetchall()
    }
    conn.close()
    return render_template(
        "admin_restaurant.html", reservations=reservations, status_filter=status_filter,
        pending_count=pending_count, upcoming_covers=upcoming_covers, settings=settings,
        employees=employees, today=today, profit=profit, prev_month=prev_month, next_month=next_month,
        shifts_by_date=shifts_by_date, no_show_count=no_show_count,
        refunded_by_reservation=refunded_by_reservation,
        overview=overview, period=period,
    )


@app.route("/admin/restaurant/shifts/new", methods=["POST"])
@owner_required
def new_restaurant_shift():
    user_id_raw = request.form.get("user_id", "").strip()
    dinner_date_raw = request.form.get("dinner_date", "").strip()
    role_note = request.form.get("role_note", "").strip()
    hours_raw = request.form.get("estimated_hours", "").strip()

    if not user_id_raw.isdigit() or not parse_date(dinner_date_raw):
        flash("Choose an employee and a valid date.", "error")
        return redirect(url_for("admin_restaurant"))

    try:
        estimated_hours = float(hours_raw) if hours_raw else None
    except ValueError:
        estimated_hours = None

    conn = get_db()
    user = conn.execute("SELECT id, name FROM users WHERE id = ?", (int(user_id_raw),)).fetchone()
    if not user:
        conn.close()
        abort(404)
    conn.execute(
        """INSERT INTO restaurant_shifts (user_id, dinner_date, role_note, estimated_hours, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user["id"], dinner_date_raw, role_note or None, estimated_hours, datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "restaurant_shift_assigned", target=user["name"], details=dinner_date_raw)
    conn.commit()
    conn.close()
    flash(f"{user['name']} assigned to {dinner_date_raw}.", "success")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/shifts/<int:shift_id>/delete", methods=["POST"])
@owner_required
def delete_restaurant_shift(shift_id):
    conn = get_db()
    conn.execute("DELETE FROM restaurant_shifts WHERE id = ?", (shift_id,))
    conn.commit()
    conn.close()
    flash("Shift removed.", "success")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/<int:reservation_id>/confirm", methods=["POST"])
@owner_required
def confirm_restaurant_booking(reservation_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (reservation_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE restaurant_bookings SET status = 'confirmed', decided_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc).isoformat(), reservation_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    log_audit(conn, "restaurant_booking_confirmed", target=booking["reference_code"])
    conn.commit()

    confirmed_total = conn.execute(
        "SELECT COALESCE(SUM(party_size), 0) AS t FROM restaurant_bookings WHERE dinner_date = ? AND status = 'confirmed'",
        (booking["dinner_date"],),
    ).fetchone()["t"]
    settings = get_restaurant_settings(conn)
    capacity_note = ""
    if settings and confirmed_total > settings["capacity"]:
        capacity_note = f" Heads up — confirmed covers for {booking['dinner_date']} now total {confirmed_total}, over the {settings['capacity']}-seat cap."

    send_restaurant_email(conn, booking, "restaurant_confirmed", restaurant_email_context(booking))
    conn.close()
    flash("Reservation confirmed." + capacity_note, "success" if not capacity_note else "error")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/<int:reservation_id>/no-show", methods=["POST"])
@owner_required
def mark_restaurant_no_show(reservation_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (reservation_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE restaurant_bookings SET no_show_at = ? WHERE id = ? AND status = 'confirmed' AND no_show_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), reservation_id),
    )
    if cur.rowcount == 0:
        conn.close()
        flash("Only a confirmed reservation can be marked as a no-show.", "error")
        return redirect(url_for("admin_restaurant"))
    log_audit(conn, "restaurant_booking_no_show", target=booking["reference_code"])
    conn.commit()
    conn.close()
    flash("Marked as a no-show.", "success")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/<int:reservation_id>/undo-no-show", methods=["POST"])
@owner_required
def undo_restaurant_no_show(reservation_id):
    conn = get_db()
    conn.execute("UPDATE restaurant_bookings SET no_show_at = NULL WHERE id = ?", (reservation_id,))
    conn.commit()
    conn.close()
    flash("No-show flag removed.", "success")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/<int:reservation_id>/decline", methods=["POST"])
@owner_required
def decline_restaurant_booking(reservation_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (reservation_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE restaurant_bookings SET status = 'declined', decided_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc).isoformat(), reservation_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    log_audit(conn, "restaurant_booking_declined", target=booking["reference_code"])
    conn.commit()

    refunded, refund_error = refund_restaurant_booking(
        conn, booking, reason="Reservation declined by the château",
        user_id=current_user()["id"])
    refund_note = " Your payment has been refunded." if refunded else (" We'll be in touch about your refund." if booking["payment_status"] == "paid" else "")
    send_restaurant_email(conn, booking, "restaurant_declined", restaurant_email_context(booking, refund_note))

    notified = notify_restaurant_waitlist_opening(conn, booking["dinner_date"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_restaurant_waitlist_entries(conn, booking["dinner_date"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} that date — check the waitlist." if remaining else ""
    conn.close()
    refund_flash = " Payment refunded." if refunded else (f" Refund failed: {refund_error}" if refund_error and booking["payment_status"] == "paid" else "")
    flash("Reservation declined." + refund_flash + waitlist_note, "success")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/<int:reservation_id>/cancel", methods=["POST"])
@owner_required
def cancel_restaurant_booking_admin(reservation_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (reservation_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE restaurant_bookings SET status = 'cancelled', decided_at = ? WHERE id = ? AND status IN ('pending', 'confirmed')",
        (datetime.now(timezone.utc).isoformat(), reservation_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    log_audit(conn, "restaurant_booking_cancelled", target=booking["reference_code"])
    conn.commit()

    refunded, refund_error = refund_restaurant_booking(
        conn, booking, reason="Reservation cancelled by the château",
        user_id=current_user()["id"])
    refund_note = " Your payment has been refunded." if refunded else (" We'll be in touch about your refund." if booking["payment_status"] == "paid" else "")
    send_restaurant_email(conn, booking, "restaurant_cancelled", restaurant_email_context(booking, refund_note))

    notified = notify_restaurant_waitlist_opening(conn, booking["dinner_date"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_restaurant_waitlist_entries(conn, booking["dinner_date"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} that date — check the waitlist." if remaining else ""
    conn.close()
    refund_flash = " Payment refunded." if refunded else (f" Refund failed: {refund_error}" if refund_error and booking["payment_status"] == "paid" else "")
    flash("Reservation cancelled." + refund_flash + waitlist_note, "success")
    return redirect(url_for("admin_restaurant"))


@app.route("/admin/restaurant/<int:reservation_id>/refund", methods=["POST"])
@owner_required
def refund_restaurant_booking_admin(reservation_id):
    conn = get_db()
    booking = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (reservation_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    amount = request.form.get("amount", "").strip()
    reason = request.form.get("reason", "").strip()
    method = request.form.get("method", "stripe").strip() or "stripe"
    # Blank amount means the whole remaining balance — the usual case.
    if not amount:
        amount = refundable_amount(conn, "restaurant", booking)
    ok, error = issue_refund(conn, "restaurant", booking, amount, reason, method=method,
                             user_id=current_user()["id"])
    if ok:
        log_audit(conn, "refund_issued", target=f"dinner {booking['reference_code']}",
                  details=f"€{float(amount):.2f} — {reason}")
        conn.commit()
        flash(f"Refund of €{float(amount):.2f} recorded.", "success")
    else:
        flash(f"Refund failed: {error}", "error")
    conn.close()
    return redirect(request.referrer or url_for("admin_restaurant"))


@app.route("/admin/restaurant/export.csv")
@owner_required
def export_restaurant_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM restaurant_bookings ORDER BY dinner_date").fetchall()
    conn.close()
    fieldnames = ["reference_code", "guest_name", "guest_email", "guest_phone", "dinner_date",
                  "party_size", "status", "payment_status", "total_price", "dietary_notes", "created_at"]
    return csv_response(fieldnames, rows, "restaurant_reservations.csv")


@app.route("/admin/restaurant/settings", methods=["GET", "POST"])
@owner_required
def admin_restaurant_settings():
    conn = get_db()
    if request.method == "POST":
        opening_date = request.form.get("opening_date", "").strip()
        dinner_time = request.form.get("dinner_time", "").strip() or "19:30"
        capacity_raw = request.form.get("capacity", "").strip()
        price_raw = request.form.get("price_per_person", "").strip()
        lead_raw = request.form.get("lead_user_id", "").strip()
        profit_share_raw = request.form.get("profit_share_percent", "").strip()
        deposit_raw = request.form.get("deposit_percent", "").strip()
        enabled = 1 if request.form.get("enabled") else 0

        capacity = int(capacity_raw) if capacity_raw.isdigit() and int(capacity_raw) > 0 else 20
        try:
            price_per_person = float(price_raw) if price_raw else None
        except ValueError:
            price_per_person = None
        try:
            profit_share_percent = max(0, min(100, float(profit_share_raw))) if profit_share_raw else 50
        except ValueError:
            profit_share_percent = 50
        try:
            deposit_percent = max(0, min(100, float(deposit_raw))) if deposit_raw else None
        except ValueError:
            deposit_percent = None
        lead_user_id = int(lead_raw) if lead_raw.isdigit() else None

        conn.execute(
            """UPDATE restaurant_settings SET opening_date = ?, dinner_time = ?, capacity = ?,
               price_per_person = ?, lead_user_id = ?, profit_share_percent = ?, deposit_percent = ?,
               enabled = ?, updated_at = ? WHERE id = 1""",
            (opening_date or None, dinner_time, capacity, price_per_person, lead_user_id,
             profit_share_percent, deposit_percent, enabled, datetime.now(timezone.utc).isoformat()),
        )
        log_audit(conn, "restaurant_settings_updated")
        conn.commit()
        conn.close()
        flash("Restaurant settings updated.", "success")
        return redirect(url_for("admin_restaurant_settings"))

    settings = get_restaurant_settings(conn)
    employees = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()
    rate_overrides = conn.execute("SELECT * FROM restaurant_rate_overrides ORDER BY start_date").fetchall()
    conn.close()
    return render_template(
        "admin_restaurant_settings.html", settings=settings, employees=employees, rate_overrides=rate_overrides,
    )


@app.route("/admin/restaurant/rates/new", methods=["POST"])
@owner_required
def new_restaurant_rate_override():
    start_raw = request.form.get("start_date", "").strip()
    end_raw = request.form.get("end_date", "").strip()
    price_raw = request.form.get("price_per_person", "").strip()
    label = request.form.get("label", "").strip()
    start, end = parse_date(start_raw), parse_date(end_raw)

    if not start or not end or end < start:
        flash("Choose a valid date range for the rate override.", "error")
        return redirect(url_for("admin_restaurant_settings"))
    try:
        price = float(price_raw)
    except ValueError:
        price = None
    if price is None or price <= 0:
        flash("Enter a valid per-person rate.", "error")
        return redirect(url_for("admin_restaurant_settings"))

    conn = get_db()
    conn.execute(
        """INSERT INTO restaurant_rate_overrides (start_date, end_date, price_per_person, label, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (start.isoformat(), end.isoformat(), price, label or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Rate override added.", "success")
    return redirect(url_for("admin_restaurant_settings"))


@app.route("/admin/restaurant/rates/<int:rate_id>/delete", methods=["POST"])
@owner_required
def delete_restaurant_rate_override(rate_id):
    conn = get_db()
    conn.execute("DELETE FROM restaurant_rate_overrides WHERE id = ?", (rate_id,))
    conn.commit()
    conn.close()
    flash("Rate override removed.", "success")
    return redirect(url_for("admin_restaurant_settings"))


@app.route("/admin/deposit-rules")
@owner_required
def admin_deposit_rules():
    conn = get_db()
    rules = conn.execute("SELECT * FROM deposit_rules ORDER BY category, start_date IS NULL, start_date").fetchall()
    restaurant_settings = get_restaurant_settings(conn)
    workshops = conn.execute("SELECT id, title, deposit_percent FROM workshops WHERE active = 1 ORDER BY title").fetchall()
    conn.close()
    return render_template(
        "admin_deposit_rules.html", rules=rules, restaurant_settings=restaurant_settings, workshops=workshops,
    )


@app.route("/admin/deposit-rules/new", methods=["POST"])
@owner_required
def new_deposit_rule():
    category = request.form.get("category", "").strip()
    start_raw = request.form.get("start_date", "").strip()
    end_raw = request.form.get("end_date", "").strip()
    min_party_raw = request.form.get("min_party_size", "").strip()
    percent_raw = request.form.get("deposit_percent", "").strip()
    label = request.form.get("label", "").strip()

    if category not in ("restaurant", "workshop"):
        flash("Choose a valid category for the rule.", "error")
        return redirect(url_for("admin_deposit_rules"))
    start, end = parse_date(start_raw), parse_date(end_raw)
    if (start_raw and not start) or (end_raw and not end) or (start and end and end < start):
        flash("Choose a valid date range (or leave both blank to apply regardless of date).", "error")
        return redirect(url_for("admin_deposit_rules"))
    min_party_size = int(min_party_raw) if min_party_raw.isdigit() and int(min_party_raw) > 0 else None
    try:
        deposit_percent = max(0, min(100, float(percent_raw)))
    except ValueError:
        flash("Enter a valid deposit percentage.", "error")
        return redirect(url_for("admin_deposit_rules"))
    if not start and not end and not min_party_size:
        flash("A rule needs at least a date range or a minimum party size — otherwise just use the base deposit setting.", "error")
        return redirect(url_for("admin_deposit_rules"))

    conn = get_db()
    conn.execute(
        """INSERT INTO deposit_rules (category, start_date, end_date, min_party_size, deposit_percent, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (category, start.isoformat() if start else None, end.isoformat() if end else None,
         min_party_size, deposit_percent, label or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Deposit rule added.", "success")
    return redirect(url_for("admin_deposit_rules"))


@app.route("/admin/deposit-rules/<int:rule_id>/delete", methods=["POST"])
@owner_required
def delete_deposit_rule(rule_id):
    conn = get_db()
    conn.execute("DELETE FROM deposit_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    flash("Deposit rule removed.", "success")
    return redirect(url_for("admin_deposit_rules"))


MENU_CATEGORIES = ["starter", "main", "dessert", "drink"]


@app.route("/admin/restaurant/menu")
@owner_required
def admin_restaurant_menu():
    conn = get_db()
    items = conn.execute("SELECT * FROM menu_items ORDER BY category, sort_order, name").fetchall()
    items_by_category = {c: [] for c in MENU_CATEGORIES}
    for item in items:
        items_by_category.setdefault(item["category"], []).append(item)
    conn.close()
    return render_template("admin_restaurant_menu.html", items_by_category=items_by_category, categories=MENU_CATEGORIES)


@app.route("/admin/restaurant/menu/new", methods=["POST"])
@owner_required
def new_menu_item():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    dietary_tags = request.form.get("dietary_tags", "").strip()
    price_raw = request.form.get("price", "").strip()

    if not name:
        flash("Dish name is required.", "error")
        return redirect(url_for("admin_restaurant_menu"))
    if category not in MENU_CATEGORIES:
        category = "main"
    try:
        price = float(price_raw) if price_raw else None
    except ValueError:
        price = None

    conn = get_db()
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM menu_items WHERE category = ?", (category,)
    ).fetchone()["m"]
    conn.execute(
        """INSERT INTO menu_items (name, description, category, dietary_tags, price, sort_order, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, description or None, category, dietary_tags or None, price, max_order + 1, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash(f"{name} added to the menu.", "success")
    return redirect(url_for("admin_restaurant_menu"))


@app.route("/admin/restaurant/menu/<int:item_id>/edit", methods=["POST"])
@owner_required
def edit_menu_item(item_id):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    dietary_tags = request.form.get("dietary_tags", "").strip()
    price_raw = request.form.get("price", "").strip()

    conn = get_db()
    item = conn.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)
    if not name:
        conn.close()
        flash("Dish name is required.", "error")
        return redirect(url_for("admin_restaurant_menu"))
    if category not in MENU_CATEGORIES:
        category = item["category"]
    try:
        price = float(price_raw) if price_raw else None
    except ValueError:
        price = None

    conn.execute(
        "UPDATE menu_items SET name=?, description=?, category=?, dietary_tags=?, price=? WHERE id=?",
        (name, description or None, category, dietary_tags or None, price, item_id),
    )
    conn.commit()
    conn.close()
    flash("Menu item updated.", "success")
    return redirect(url_for("admin_restaurant_menu"))


@app.route("/admin/restaurant/menu/<int:item_id>/toggle", methods=["POST"])
@owner_required
def toggle_menu_item(item_id):
    conn = get_db()
    item = conn.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)
    conn.execute("UPDATE menu_items SET active = ? WHERE id = ?", (0 if item["active"] else 1, item_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_restaurant_menu"))


@app.route("/admin/restaurant/menu/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_menu_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM menu_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("Menu item removed.", "success")
    return redirect(url_for("admin_restaurant_menu"))


PROMO_APPLIES_TO = ["all", "room", "restaurant", "workshop"]


@app.route("/admin/promo-codes")
@owner_required
def admin_promo_codes():
    conn = get_db()
    codes = conn.execute("SELECT * FROM promo_codes ORDER BY active DESC, created_at DESC").fetchall()
    conn.close()
    return render_template("admin_promo_codes.html", codes=codes, applies_to_options=PROMO_APPLIES_TO)


def _parse_promo_form():
    """Shared parsing/validation for the new- and edit-promo-code forms —
    returns (fields_dict, None) on success or (None, error_message) on a
    bad submission."""
    code = request.form.get("code", "").strip().upper()
    description = request.form.get("description", "").strip()
    discount_type = request.form.get("discount_type", "percent").strip()
    applies_to = request.form.get("applies_to", "all").strip()
    if not code:
        return None, "A code is required."
    if discount_type not in ("percent", "fixed"):
        discount_type = "percent"
    if applies_to not in PROMO_APPLIES_TO:
        applies_to = "all"
    try:
        discount_value = float(request.form.get("discount_value", "").strip())
        if discount_value <= 0:
            raise ValueError
    except ValueError:
        return None, "Enter a discount value greater than 0."
    if discount_type == "percent" and discount_value > 100:
        return None, "A percent discount can't exceed 100."
    try:
        max_discount_amount = float(request.form.get("max_discount_amount", "").strip()) or None
    except ValueError:
        max_discount_amount = None
    try:
        min_spend = float(request.form.get("min_spend", "").strip()) or None
    except ValueError:
        min_spend = None
    max_redemptions_raw = request.form.get("max_redemptions", "").strip()
    max_redemptions = int(max_redemptions_raw) if max_redemptions_raw.isdigit() else None

    return {
        "code": code, "description": description or None, "discount_type": discount_type,
        "discount_value": discount_value, "max_discount_amount": max_discount_amount,
        "applies_to": applies_to, "min_spend": min_spend, "max_redemptions": max_redemptions,
        "valid_from": request.form.get("valid_from", "").strip() or None,
        "valid_until": request.form.get("valid_until", "").strip() or None,
    }, None


@app.route("/admin/promo-codes/new", methods=["POST"])
@owner_required
def new_promo_code():
    fields, error = _parse_promo_form()
    if error:
        flash(error, "error")
        return redirect(url_for("admin_promo_codes"))
    conn = get_db()
    user = current_user()
    try:
        conn.execute(
            """INSERT INTO promo_codes
               (code, description, discount_type, discount_value, max_discount_amount, applies_to,
                min_spend, max_redemptions, valid_from, valid_until, active, created_by_user_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (fields["code"], fields["description"], fields["discount_type"], fields["discount_value"],
             fields["max_discount_amount"], fields["applies_to"], fields["min_spend"], fields["max_redemptions"],
             fields["valid_from"], fields["valid_until"], user["id"], datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        flash(f"Promo code {fields['code']} created.", "success")
    except sqlite3.IntegrityError:
        flash(f"A code called {fields['code']} already exists.", "error")
    conn.close()
    return redirect(url_for("admin_promo_codes"))


@app.route("/admin/promo-codes/<int:code_id>/edit", methods=["POST"])
@owner_required
def edit_promo_code(code_id):
    conn = get_db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE id = ?", (code_id,)).fetchone()
    if not promo:
        conn.close()
        abort(404)
    fields, error = _parse_promo_form()
    if error:
        conn.close()
        flash(error, "error")
        return redirect(url_for("admin_promo_codes"))
    try:
        conn.execute(
            """UPDATE promo_codes SET code=?, description=?, discount_type=?, discount_value=?,
               max_discount_amount=?, applies_to=?, min_spend=?, max_redemptions=?, valid_from=?, valid_until=?
               WHERE id=?""",
            (fields["code"], fields["description"], fields["discount_type"], fields["discount_value"],
             fields["max_discount_amount"], fields["applies_to"], fields["min_spend"], fields["max_redemptions"],
             fields["valid_from"], fields["valid_until"], code_id),
        )
        conn.commit()
        flash("Promo code updated.", "success")
    except sqlite3.IntegrityError:
        flash(f"A code called {fields['code']} already exists.", "error")
    conn.close()
    return redirect(url_for("admin_promo_codes"))


@app.route("/admin/promo-codes/<int:code_id>/toggle", methods=["POST"])
@owner_required
def toggle_promo_code(code_id):
    conn = get_db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE id = ?", (code_id,)).fetchone()
    if not promo:
        conn.close()
        abort(404)
    conn.execute("UPDATE promo_codes SET active = ? WHERE id = ?", (0 if promo["active"] else 1, code_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_promo_codes"))


@app.route("/admin/promo-codes/<int:code_id>/delete", methods=["POST"])
@owner_required
def delete_promo_code(code_id):
    conn = get_db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE id = ?", (code_id,)).fetchone()
    if not promo:
        conn.close()
        abort(404)
    if promo["redemption_count"] > 0:
        conn.close()
        flash("Can't delete a code that's already been used — deactivate it instead so the redemption history stays intact.", "error")
        return redirect(url_for("admin_promo_codes"))
    conn.execute("DELETE FROM promo_codes WHERE id = ?", (code_id,))
    conn.commit()
    conn.close()
    flash("Promo code deleted.", "success")
    return redirect(url_for("admin_promo_codes"))


GUEST_BLAST_SEGMENTS = ["room", "restaurant", "workshop"]


def promo_blast_recipients(conn, segments, since_date_iso=None):
    """Distinct {email: name} across the selected segments — a guest with,
    say, both a room stay and a dinner only gets emailed once. Only a
    confirmed booking counts as a real past guest, and a workshop guest
    who ticked do_not_email is skipped, mirroring the balance-reminder
    job's existing respect for that flag. since_date_iso filters by the
    guest's actual visit date (arrival/dinner/session start), not when
    they booked, since 'stayed in the last N months' is what a marketing
    segment actually means."""
    recipients = {}
    if "room" in segments:
        query = "SELECT DISTINCT guest_email, guest_name FROM bookings WHERE status = 'confirmed'"
        params = []
        if since_date_iso:
            query += " AND arrival_date >= ?"
            params.append(since_date_iso)
        for row in conn.execute(query, params).fetchall():
            recipients.setdefault(row["guest_email"], row["guest_name"])
    if "restaurant" in segments:
        query = "SELECT DISTINCT guest_email, guest_name FROM restaurant_bookings WHERE status = 'confirmed'"
        params = []
        if since_date_iso:
            query += " AND dinner_date >= ?"
            params.append(since_date_iso)
        for row in conn.execute(query, params).fetchall():
            recipients.setdefault(row["guest_email"], row["guest_name"])
    if "workshop" in segments:
        query = """SELECT DISTINCT workshop_bookings.guest_email, workshop_bookings.guest_name
                   FROM workshop_bookings JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
                   WHERE workshop_bookings.status = 'confirmed' AND workshop_bookings.do_not_email = 0"""
        params = []
        if since_date_iso:
            query += " AND workshop_sessions.start_date >= ?"
            params.append(since_date_iso)
        for row in conn.execute(query, params).fetchall():
            recipients.setdefault(row["guest_email"], row["guest_name"])
    return recipients


@app.route("/admin/promo-codes/<int:code_id>/blast")
@owner_required
def promo_code_blast(code_id):
    conn = get_db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE id = ?", (code_id,)).fetchone()
    if not promo:
        conn.close()
        abort(404)
    segments = [s for s in request.args.getlist("segment") if s in GUEST_BLAST_SEGMENTS] or list(GUEST_BLAST_SEGMENTS)
    since_months_raw = request.args.get("since_months", "").strip()
    since_date_iso = None
    if since_months_raw.isdigit():
        since_date_iso = (datetime.now(timezone.utc).date() - timedelta(days=int(since_months_raw) * 30)).isoformat()
    recipients = promo_blast_recipients(conn, segments, since_date_iso)
    conn.close()
    return render_template(
        "admin_promo_blast.html", promo=promo, segments=segments, since_months=since_months_raw,
        recipient_count=len(recipients), all_segments=GUEST_BLAST_SEGMENTS,
    )


@app.route("/admin/promo-codes/<int:code_id>/blast/send", methods=["POST"])
@owner_required
def send_promo_code_blast(code_id):
    conn = get_db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE id = ?", (code_id,)).fetchone()
    if not promo:
        conn.close()
        abort(404)
    segments = [s for s in request.form.getlist("segment") if s in GUEST_BLAST_SEGMENTS] or list(GUEST_BLAST_SEGMENTS)
    since_months_raw = request.form.get("since_months", "").strip()
    since_date_iso = None
    if since_months_raw.isdigit():
        since_date_iso = (datetime.now(timezone.utc).date() - timedelta(days=int(since_months_raw) * 30)).isoformat()
    subject = request.form.get("subject", "").strip()
    body_template = request.form.get("body", "").strip()
    if not subject or not body_template:
        conn.close()
        flash("Subject and message are required.", "error")
        return redirect(url_for("promo_code_blast", code_id=code_id, segment=segments, since_months=since_months_raw))

    recipients = promo_blast_recipients(conn, segments, since_date_iso)
    sent = 0
    for email, name in recipients.items():
        personalized = body_template.replace("{guest_name}", name or "there").replace("{promo_code}", promo["code"])
        if send_email(email, subject, personalized):
            sent += 1
    conn.close()
    flash(f"Sent to {sent} of {len(recipients)} guest(s).", "success")
    return redirect(url_for("admin_promo_codes"))


def matching_restaurant_waitlist_entries(conn, dinner_date_iso):
    """Open/contacted waitlist entries for the given date — a nudge to go
    check when a reservation frees up a seat, not a guarantee of fit."""
    if not dinner_date_iso:
        return []
    return conn.execute(
        "SELECT * FROM restaurant_waitlist WHERE status IN ('open', 'contacted') AND desired_date = ?",
        (dinner_date_iso,),
    ).fetchall()


@app.route("/restaurant/waitlist/join", methods=["POST"])
def join_restaurant_waitlist():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    desired_date = request.form.get("desired_date", "").strip()
    party_size_raw = request.form.get("party_size", "").strip()
    notes = request.form.get("notes", "").strip()

    conn = get_db()
    if rate_limited(conn, "join_restaurant_waitlist", BOOKING_RATE_LIMIT_PER_HOUR):
        conn.commit()
        conn.close()
        flash("Too many attempts from this connection — please try again in a bit.", "error")
        return redirect(url_for("restaurant_book"))
    if not name or not email:
        conn.commit()
        conn.close()
        flash("Name and email are required.", "error")
        return redirect(url_for("restaurant_book"))
    if not EMAIL_RE.match(email):
        conn.commit()
        conn.close()
        flash("Enter a valid email address.", "error")
        return redirect(url_for("restaurant_book"))

    party_size = int(party_size_raw) if party_size_raw.isdigit() else None
    conn.execute(
        """INSERT INTO restaurant_waitlist (name, email, phone, desired_date, party_size, notes, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
        (name, email, phone or None, desired_date or None, party_size, notes or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    owner_to = owner_email(conn)
    if owner_to:
        send_email(
            owner_to, "New dinner waitlist request",
            f"{name} ({email}) would like {desired_date or 'a date'}, party of {party_size or '?'}, "
            f"but it's fully booked.\n\n{notes or ''}",
        )
    conn.close()
    flash("You're on the waitlist — we'll reach out if a table opens up.", "success")
    return redirect(url_for("restaurant_book", date=desired_date))


@app.route("/admin/restaurant/waitlist")
@owner_required
def admin_restaurant_waitlist():
    conn = get_db()
    entries = conn.execute(
        "SELECT * FROM restaurant_waitlist ORDER BY (status != 'open'), created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("admin_restaurant_waitlist.html", entries=entries)


@app.route("/admin/restaurant/waitlist/export.csv")
@owner_required
def export_restaurant_waitlist_csv():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM restaurant_waitlist ORDER BY (status != 'open'), created_at DESC"
    ).fetchall()
    conn.close()
    fieldnames = ["name", "email", "phone", "desired_date", "party_size", "notes", "status", "created_at"]
    return csv_response(fieldnames, rows, "restaurant_waitlist.csv")


@app.route("/admin/restaurant/waitlist/<int:entry_id>/status", methods=["POST"])
@owner_required
def update_restaurant_waitlist_status(entry_id):
    status = request.form.get("status", "")
    if status not in ("open", "contacted", "booked", "closed"):
        abort(400)
    conn = get_db()
    conn.execute("UPDATE restaurant_waitlist SET status = ? WHERE id = ?", (status, entry_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_restaurant_waitlist"))


# ---------------------------------------------------------------------------
# Workshops — the in-house replacement for RetreatGuru. A workshop is a
# catalog entry (title, instructor, price, description); a session is a
# specific scheduled running of it with its own dates and capacity — the
# same two-level shape RetreatGuru uses (a "program" with multiple
# "occurrences"). Registrations reuse the reference-code + manage-token
# pattern from the room and restaurant booking engines for a consistent
# guest self-service experience across all three.
# ---------------------------------------------------------------------------

def workshop_session_remaining_capacity(conn, session_id, exclude_id=None):
    session = conn.execute("SELECT * FROM workshop_sessions WHERE id = ?", (session_id,)).fetchone()
    if not session:
        return 0
    query = """SELECT COALESCE(SUM(party_size), 0) AS t FROM workshop_bookings
               WHERE session_id = ? AND status IN ('pending', 'confirmed')"""
    params = [session_id]
    if exclude_id:
        query += " AND id != ?"
        params.append(exclude_id)
    used = conn.execute(query, params).fetchone()["t"]
    return session["capacity"] - used


@app.route("/admin/workshops")
@owner_required
def admin_workshops():
    conn = get_db()
    workshops = conn.execute("SELECT * FROM workshops ORDER BY sort_order, title").fetchall()
    today = datetime.now(timezone.utc).date()
    period = period_from_request()
    overview = workshops_overview(conn, period, today)
    total_rooms = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
    # Sessions used to be filtered to end_date >= today, so a workshop that had
    # already run vanished from the admin page entirely — no way to see who
    # attended or what it took. Past sessions are now loaded too and split out
    # below, with attendance so the history is worth having. The PUBLIC
    # workshops page still shows upcoming only, which is correct there.
    sessions_by_workshop, past_by_workshop = {}, {}
    for w in workshops:
        sessions = conn.execute(
            "SELECT * FROM workshop_sessions WHERE workshop_id = ? ORDER BY start_date",
            (w["id"],),
        ).fetchall()
        rows, past_rows = [], []
        for s in sessions:
            rooms_assigned = conn.execute(
                """SELECT COUNT(DISTINCT assigned_room_id) AS c FROM workshop_bookings
                   WHERE session_id = ? AND status IN ('pending', 'confirmed') AND assigned_room_id IS NOT NULL""",
                (s["id"],),
            ).fetchone()["c"]
            entry = {
                "session": s, "remaining": workshop_session_remaining_capacity(conn, s["id"]),
                "rooms_assigned": rooms_assigned,
            }
            if (s["end_date"] or s["start_date"]) >= today.isoformat():
                rows.append(entry)
            else:
                entry["attended"] = conn.execute(
                    """SELECT COALESCE(SUM(party_size), 0) AS c FROM workshop_bookings
                       WHERE session_id = ? AND status = 'confirmed'""",
                    (s["id"],),
                ).fetchone()["c"]
                past_rows.append(entry)
        past_rows.reverse()  # most recent first
        sessions_by_workshop[w["id"]] = rows
        past_by_workshop[w["id"]] = past_rows
    instructors = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()
    custom_fields_by_workshop = {}
    for row in conn.execute("SELECT * FROM workshop_custom_fields ORDER BY sort_order").fetchall():
        custom_fields_by_workshop.setdefault(row["workshop_id"], []).append(row)
    # Totalled here rather than in the template: the old version accumulated
    # inside a Jinja {% for %}, where a {% set %} doesn't escape the loop, so
    # both figures always came out as zero however many sessions were listed.
    upcoming_rows = [r for rows in sessions_by_workshop.values() for r in rows]
    upcoming_count = len(upcoming_rows)
    spots_remaining = sum(r["remaining"] for r in upcoming_rows)
    conn.close()
    return render_template(
        "admin_workshops.html", workshops=workshops, sessions_by_workshop=sessions_by_workshop,
        past_by_workshop=past_by_workshop,
        instructors=instructors, today=today, total_rooms=total_rooms,
        custom_fields_by_workshop=custom_fields_by_workshop,
        overview=overview, period=period,
        upcoming_count=upcoming_count, spots_remaining=spots_remaining,
    )


@app.route("/admin/workshops/new", methods=["GET", "POST"])
@owner_required
def new_workshop():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        instructor_name = request.form.get("instructor_name", "").strip()
        instructor_user_id = request.form.get("instructor_user_id", "").strip()
        price_raw = request.form.get("price_per_person", "").strip()
        capacity_raw = request.form.get("default_capacity", "").strip()
        deposit_percent_raw = request.form.get("deposit_percent", "").strip()
        inclusions = request.form.get("inclusions", "").strip()
        itinerary = request.form.get("itinerary", "").strip()

        if not title:
            flash("Workshop title is required.", "error")
            return render_template("workshop_form.html", workshop=None, instructors=known_instructor_list())

        conn = get_db()
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM workshops").fetchone()["m"]
        conn.execute(
            """INSERT INTO workshops (title, description, instructor_name, instructor_user_id, price_per_person,
               default_capacity, sort_order, deposit_percent, inclusions, itinerary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, instructor_name or None,
             int(instructor_user_id) if instructor_user_id.isdigit() else None,
             float(price_raw) if price_raw else 0,
             int(capacity_raw) if capacity_raw.isdigit() and int(capacity_raw) > 0 else 10,
             max_order + 1,
             int(deposit_percent_raw) if deposit_percent_raw.isdigit() else 30,
             inclusions or None, itinerary or None, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        flash(f"{title} added.", "success")
        return redirect(url_for("admin_workshops"))

    return render_template("workshop_form.html", workshop=None, instructors=known_instructor_list())


def known_instructor_list():
    conn = get_db()
    instructors = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()
    conn.close()
    return instructors


@app.route("/admin/workshops/<int:workshop_id>/edit", methods=["GET", "POST"])
@owner_required
def edit_workshop(workshop_id):
    conn = get_db()
    workshop = conn.execute("SELECT * FROM workshops WHERE id = ?", (workshop_id,)).fetchone()
    if not workshop:
        conn.close()
        abort(404)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        instructor_name = request.form.get("instructor_name", "").strip()
        instructor_user_id = request.form.get("instructor_user_id", "").strip()
        price_raw = request.form.get("price_per_person", "").strip()
        capacity_raw = request.form.get("default_capacity", "").strip()
        deposit_percent_raw = request.form.get("deposit_percent", "").strip()
        inclusions = request.form.get("inclusions", "").strip()
        itinerary = request.form.get("itinerary", "").strip()
        active = 1 if request.form.get("active") == "on" else 0

        if not title:
            conn.close()
            flash("Workshop title is required.", "error")
            return redirect(url_for("edit_workshop", workshop_id=workshop_id))

        conn.execute(
            """UPDATE workshops SET title=?, description=?, instructor_name=?, instructor_user_id=?,
               price_per_person=?, default_capacity=?, deposit_percent=?, inclusions=?, itinerary=?, active=? WHERE id=?""",
            (title, description, instructor_name or None,
             int(instructor_user_id) if instructor_user_id.isdigit() else None,
             float(price_raw) if price_raw else 0,
             int(capacity_raw) if capacity_raw.isdigit() and int(capacity_raw) > 0 else workshop["default_capacity"],
             int(deposit_percent_raw) if deposit_percent_raw.isdigit() else workshop["deposit_percent"],
             inclusions or None, itinerary or None, active, workshop_id),
        )
        conn.commit()
        conn.close()
        flash("Workshop updated.", "success")
        return redirect(url_for("admin_workshops"))

    instructors = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()
    conn.close()
    return render_template("workshop_form.html", workshop=workshop, instructors=instructors)


@app.route("/admin/workshops/<int:workshop_id>/delete", methods=["POST"])
@owner_required
def delete_workshop(workshop_id):
    conn = get_db()
    in_use = conn.execute(
        """SELECT COUNT(*) AS c FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           WHERE workshop_sessions.workshop_id = ?""",
        (workshop_id,),
    ).fetchone()["c"]
    if in_use:
        conn.close()
        flash("Can't delete a workshop with existing registrations — deactivate it instead.", "error")
        return redirect(url_for("admin_workshops"))
    conn.execute("DELETE FROM workshops WHERE id = ?", (workshop_id,))
    conn.commit()
    conn.close()
    flash("Workshop removed.", "success")
    return redirect(url_for("admin_workshops"))


@app.route("/admin/workshops/<int:workshop_id>/sessions/new", methods=["POST"])
@owner_required
def new_workshop_session(workshop_id):
    conn = get_db()
    workshop = conn.execute("SELECT * FROM workshops WHERE id = ?", (workshop_id,)).fetchone()
    if not workshop:
        conn.close()
        abort(404)
    start_date = parse_date(request.form.get("start_date", ""))
    end_date = parse_date(request.form.get("end_date", "")) or start_date
    capacity_raw = request.form.get("capacity", "").strip()
    notes = request.form.get("notes", "").strip()

    if not start_date:
        conn.close()
        flash("Choose a valid start date.", "error")
        return redirect(url_for("admin_workshops"))
    if end_date < start_date:
        conn.close()
        flash("End date can't be before the start date.", "error")
        return redirect(url_for("admin_workshops"))

    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (workshop_id, start_date.isoformat(), end_date.isoformat(),
         int(capacity_raw) if capacity_raw.isdigit() and int(capacity_raw) > 0 else workshop["default_capacity"],
         notes or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    # A workshop session takes over the whole château, so flag it (don't
    # block it — the owner might be moving guests or already knows) if any
    # room booking already overlaps those dates.
    clashing = conn.execute(
        """SELECT COUNT(*) AS c FROM bookings WHERE status IN ('pending', 'confirmed')
           AND arrival_date < ? AND departure_date > ?""",
        ((end_date + timedelta(days=1)).isoformat(), start_date.isoformat()),
    ).fetchone()["c"]
    clashing_events = conn.execute(
        """SELECT COUNT(*) AS c FROM event_inquiries WHERE status = 'confirmed'
           AND preferred_date >= ? AND preferred_date <= ?""",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchone()["c"]
    conn.close()
    if clashing or clashing_events:
        parts = []
        if clashing:
            parts.append(f"{clashing} existing room booking(s)")
        if clashing_events:
            parts.append(f"{clashing_events} confirmed event(s)")
        flash(f"Session added — heads up, {' and '.join(parts)} overlap these dates.", "error")
    else:
        flash("Session added.", "success")
    return redirect(url_for("admin_workshops"))


@app.route("/admin/workshops/sessions/<int:session_id>/delete", methods=["POST"])
@owner_required
def delete_workshop_session(session_id):
    conn = get_db()
    in_use = conn.execute(
        "SELECT COUNT(*) AS c FROM workshop_bookings WHERE session_id = ?", (session_id,)
    ).fetchone()["c"]
    if in_use:
        conn.close()
        flash("Can't delete a session with existing registrations — cancel them first.", "error")
        return redirect(url_for("admin_workshops"))
    conn.execute("DELETE FROM workshop_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    flash("Session removed.", "success")
    return redirect(url_for("admin_workshops"))


@app.route("/admin/workshops/<int:workshop_id>/custom-fields/new", methods=["POST"])
@owner_required
def new_workshop_custom_field(workshop_id):
    label = request.form.get("label", "").strip()
    field_type = request.form.get("field_type", "text").strip()
    options = request.form.get("options", "").strip()
    required = 1 if request.form.get("required") == "on" else 0
    if not label:
        flash("A question label is required.", "error")
        return redirect(url_for("admin_workshops"))
    if field_type not in ("text", "textarea", "select", "checkbox"):
        field_type = "text"
    conn = get_db()
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS m FROM workshop_custom_fields WHERE workshop_id = ?", (workshop_id,)
    ).fetchone()["m"]
    conn.execute(
        """INSERT INTO workshop_custom_fields (workshop_id, label, field_type, options, required, sort_order, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (workshop_id, label, field_type, options or None, required, max_order + 1, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Question added.", "success")
    return redirect(url_for("admin_workshops"))


@app.route("/admin/workshops/custom-fields/<int:field_id>/delete", methods=["POST"])
@owner_required
def delete_workshop_custom_field(field_id):
    conn = get_db()
    conn.execute("DELETE FROM workshop_custom_fields WHERE id = ?", (field_id,))
    conn.commit()
    conn.close()
    flash("Question removed.", "success")
    return redirect(url_for("admin_workshops"))


def matching_workshop_waitlist_entries(conn, session_id):
    return conn.execute(
        "SELECT * FROM workshop_waitlist WHERE session_id = ? AND status IN ('open', 'contacted')",
        (session_id,),
    ).fetchall()


@app.route("/admin/workshops/registrations")
@owner_required
def admin_workshop_registrations():
    conn = get_db()
    status_filter = request.args.get("status", "")
    session_filter = request.args.get("session_id", "")
    query = """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date,
                   workshops.title, rooms.name AS assigned_room_name FROM workshop_bookings
               JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
               JOIN workshops ON workshops.id = workshop_sessions.workshop_id
               LEFT JOIN rooms ON rooms.id = workshop_bookings.assigned_room_id
               WHERE 1=1"""
    params = []
    if status_filter:
        query += " AND workshop_bookings.status = ?"
        params.append(status_filter)
    if session_filter.isdigit():
        query += " AND workshop_bookings.session_id = ?"
        params.append(int(session_filter))
    query += " ORDER BY workshop_sessions.start_date, workshop_bookings.created_at"
    registrations = conn.execute(query, params).fetchall()
    pending_count = conn.execute("SELECT COUNT(*) AS c FROM workshop_bookings WHERE status = 'pending'").fetchone()["c"]
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, name").fetchall()
    guests_by_booking = {}
    for row in conn.execute(
        "SELECT * FROM workshop_booking_guests ORDER BY is_lead DESC, id"
    ).fetchall():
        guests_by_booking.setdefault(row["workshop_booking_id"], []).append(row)
    custom_responses_by_booking = {}
    for row in conn.execute(
        """SELECT workshop_custom_field_responses.*, workshop_custom_fields.label
           FROM workshop_custom_field_responses
           JOIN workshop_custom_fields ON workshop_custom_fields.id = workshop_custom_field_responses.custom_field_id
           WHERE workshop_custom_field_responses.value IS NOT NULL AND workshop_custom_field_responses.value != ''
           ORDER BY workshop_custom_fields.sort_order"""
    ).fetchall():
        custom_responses_by_booking.setdefault(row["workshop_booking_id"], []).append(row)
    transactions_by_booking = {}
    balance_by_booking = {}
    for r in registrations:
        txns = conn.execute(
            "SELECT * FROM workshop_transactions WHERE workshop_booking_id = ? ORDER BY created_at", (r["id"],)
        ).fetchall()
        transactions_by_booking[r["id"]] = txns
        balance_by_booking[r["id"]] = workshop_balance_due(conn, r["id"])
    messages_by_booking = {}
    for row in conn.execute("SELECT * FROM workshop_messages ORDER BY created_at DESC").fetchall():
        messages_by_booking.setdefault(row["workshop_booking_id"], []).append(row)
    # What each registration has actually paid (from its ledger) and what has
    # already been handed back, so the refund form knows its ceiling.
    refunded_by_registration = {
        r["booking_id"]: round(r["total"], 2) for r in conn.execute(
            "SELECT booking_id, SUM(amount) AS total FROM refunds WHERE category = 'workshop' GROUP BY booking_id"
        ).fetchall()
    }
    # Gross payments per registration, in ONE grouped query rather than a
    # lookup per row. (Gross, not the net figure from balance_by_booking -- see
    # amount_paid_for() for why netting refunds off here double-counts them.)
    paid_by_registration = {
        r["workshop_booking_id"]: round(r["paid"], 2) for r in conn.execute(
            """SELECT workshop_booking_id, SUM(amount) AS paid FROM workshop_transactions
               WHERE kind = 'payment' GROUP BY workshop_booking_id"""
        ).fetchall()
    }
    conn.close()
    return render_template(
        "admin_workshop_registrations.html", registrations=registrations, status_filter=status_filter,
        session_filter=session_filter, pending_count=pending_count, rooms=rooms,
        guests_by_booking=guests_by_booking, custom_responses_by_booking=custom_responses_by_booking,
        transactions_by_booking=transactions_by_booking, balance_by_booking=balance_by_booking,
        messages_by_booking=messages_by_booking,
        refunded_by_registration=refunded_by_registration, paid_by_registration=paid_by_registration,
    )


@app.route("/admin/workshops/registrations/<int:registration_id>/confirm", methods=["POST"])
@owner_required
def confirm_workshop_registration(registration_id):
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date,
               workshop_sessions.capacity, workshops.title FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""",
        (registration_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE workshop_bookings SET status = 'confirmed', decided_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc).isoformat(), registration_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    log_audit(conn, "workshop_registration_confirmed", target=booking["reference_code"])
    conn.commit()

    confirmed_total = conn.execute(
        "SELECT COALESCE(SUM(party_size), 0) AS t FROM workshop_bookings WHERE session_id = ? AND status = 'confirmed'",
        (booking["session_id"],),
    ).fetchone()["t"]
    capacity_note = ""
    if confirmed_total > booking["capacity"]:
        capacity_note = f" Heads up — confirmed registrations for this session now total {confirmed_total}, over the {booking['capacity']}-spot cap."

    send_workshop_email(conn, booking, "workshop_confirmed", workshop_email_context(booking))
    conn.commit()
    conn.close()
    flash("Registration confirmed." + capacity_note, "success" if not capacity_note else "error")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/<int:registration_id>/decline", methods=["POST"])
@owner_required
def decline_workshop_registration(registration_id):
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""",
        (registration_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE workshop_bookings SET status = 'declined', decided_at = ? WHERE id = ? AND status = 'pending'",
        (datetime.now(timezone.utc).isoformat(), registration_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    log_audit(conn, "workshop_registration_declined", target=booking["reference_code"])
    conn.commit()
    send_workshop_email(conn, booking, "workshop_declined", workshop_email_context(booking))
    conn.commit()
    notified = notify_workshop_waitlist_opening(conn, booking["session_id"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_workshop_waitlist_entries(conn, booking["session_id"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} this session — check the waitlist." if remaining else ""
    conn.close()
    flash("Registration declined." + waitlist_note, "success")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/<int:registration_id>/cancel", methods=["POST"])
@owner_required
def cancel_workshop_registration_admin(registration_id):
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""",
        (registration_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE workshop_bookings SET status = 'cancelled', decided_at = ? WHERE id = ? AND status IN ('pending', 'confirmed')",
        (datetime.now(timezone.utc).isoformat(), registration_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    log_audit(conn, "workshop_registration_cancelled", target=booking["reference_code"])
    conn.commit()
    send_workshop_email(conn, booking, "workshop_cancelled", workshop_email_context(booking))
    conn.commit()
    notified = notify_workshop_waitlist_opening(conn, booking["session_id"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_workshop_waitlist_entries(conn, booking["session_id"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} this session — check the waitlist." if remaining else ""
    conn.close()
    flash("Registration cancelled." + waitlist_note, "success")
    return redirect(url_for("admin_workshop_registrations"))


def workshop_room_conflict(conn, room_id, session_start, session_end, session_id, exclude_registration_id=None):
    """Real conflicts for housing a workshop attendee in room_id for
    session_start..session_end (both inclusive nights). Deliberately does
    NOT reuse is_range_available — that function's workshop-blocks-every-
    room rule exists to protect attendees from guest bookings, which is
    backwards here: assigning a room *during the attendee's own session*
    is exactly when that room should be assignable. Checks only the two
    real risks — an actual guest booking in that room over those nights,
    and the room already handed to a different attendee in this session."""
    departure_exclusive = session_end + timedelta(days=1)
    overlapping_booking = conn.execute(
        """SELECT id FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
           AND arrival_date < ? AND departure_date > ?""",
        (room_id, departure_exclusive.isoformat(), session_start.isoformat()),
    ).fetchone()
    if overlapping_booking:
        return "That room already has a guest booking during this session."

    query = """SELECT id FROM workshop_bookings
               WHERE session_id = ? AND assigned_room_id = ? AND status IN ('pending','confirmed')"""
    params = [session_id, room_id]
    if exclude_registration_id:
        query += " AND id != ?"
        params.append(exclude_registration_id)
    if conn.execute(query, params).fetchone():
        return "That room is already assigned to another attendee in this session."
    return None


@app.route("/admin/workshops/registrations/<int:registration_id>/assign-room", methods=["POST"])
@owner_required
def assign_workshop_room(registration_id):
    room_id_raw = request.form.get("room_id", "").strip()
    room_id = int(room_id_raw) if room_id_raw.isdigit() else None
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date
           FROM workshop_bookings JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           WHERE workshop_bookings.id = ?""",
        (registration_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)

    if room_id:
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room:
            conn.close()
            abort(404)
        if room["max_occupancy"] < booking["party_size"]:
            conn.close()
            flash(f"{room['name']} sleeps up to {room['max_occupancy']} — this booking is a party of {booking['party_size']}.", "error")
            return redirect(url_for("admin_workshop_registrations"))
        conflict = workshop_room_conflict(
            conn, room_id, parse_date(booking["start_date"]), parse_date(booking["end_date"]),
            booking["session_id"], exclude_registration_id=registration_id,
        )
        if conflict:
            conn.close()
            flash(conflict, "error")
            return redirect(url_for("admin_workshop_registrations"))

    conn.execute("UPDATE workshop_bookings SET assigned_room_id = ? WHERE id = ?", (room_id, registration_id))
    conn.commit()
    conn.close()
    flash("Room assignment updated.", "success")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/<int:registration_id>/mark-deposit-paid", methods=["POST"])
@owner_required
def mark_workshop_deposit_paid(registration_id):
    """Manual fallback for deposits taken outside Stripe — bank transfer,
    cash, a card reader at the château — same idea as marking an expense
    paid by hand. Logs the payment to the ledger and sends a receipt."""
    method = request.form.get("method", "other").strip() or "other"
    conn = get_db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""",
        (registration_id,),
    ).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE workshop_bookings SET deposit_paid_at = ? WHERE id = ? AND deposit_paid_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), registration_id),
    )
    if cur.rowcount and booking["deposit_amount"]:
        add_workshop_transaction(conn, registration_id, "payment", "Deposit", booking["deposit_amount"],
                                  method=method, user_id=current_user()["id"])
        send_workshop_email(conn, booking, "workshop_deposit_receipt", workshop_email_context(booking))
    conn.commit()
    conn.close()
    flash("Deposit marked as paid.", "success")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/<int:registration_id>/mark-balance-paid", methods=["POST"])
@owner_required
def mark_workshop_balance_paid(registration_id):
    method = request.form.get("method", "other").strip() or "other"
    conn = get_db()
    booking = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (registration_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE workshop_bookings SET balance_paid_at = ? WHERE id = ? AND balance_paid_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), registration_id),
    )
    if cur.rowcount and booking["balance_amount"]:
        add_workshop_transaction(conn, registration_id, "payment", "Balance", booking["balance_amount"],
                                  method=method, user_id=current_user()["id"])
    conn.commit()
    conn.close()
    flash("Balance marked as paid.", "success")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/<int:registration_id>/add-transaction", methods=["POST"])
@owner_required
def add_workshop_transaction_route(registration_id):
    kind = request.form.get("kind", "").strip()
    description = request.form.get("description", "").strip()
    amount_raw = request.form.get("amount", "").strip()
    method = request.form.get("method", "").strip() or None
    if kind not in ("charge", "discount", "payment", "refund") or not description:
        flash("A description and a valid transaction type are required.", "error")
        return redirect(url_for("admin_workshop_registrations"))
    try:
        amount = float(amount_raw)
    except ValueError:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("admin_workshop_registrations"))
    if amount <= 0:
        flash("Amount must be greater than zero.", "error")
        return redirect(url_for("admin_workshop_registrations"))
    conn = get_db()
    booking = conn.execute("SELECT id FROM workshop_bookings WHERE id = ?", (registration_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    add_workshop_transaction(conn, registration_id, kind, description, round(amount, 2), method=method,
                              user_id=current_user()["id"])
    log_audit(conn, "workshop_transaction_added", target=f"{kind} €{amount:.2f}")
    conn.commit()
    conn.close()
    flash("Transaction recorded.", "success")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/<int:registration_id>/toggle-do-not-email", methods=["POST"])
@owner_required
def toggle_workshop_do_not_email(registration_id):
    conn = get_db()
    booking = conn.execute("SELECT do_not_email FROM workshop_bookings WHERE id = ?", (registration_id,)).fetchone()
    if not booking:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE workshop_bookings SET do_not_email = ? WHERE id = ?",
        (0 if booking["do_not_email"] else 1, registration_id),
    )
    conn.commit()
    conn.close()
    flash("Email preference updated.", "success")
    return redirect(url_for("admin_workshop_registrations"))


@app.route("/admin/workshops/registrations/export.csv")
@owner_required
def export_workshop_registrations_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date,
               workshops.title AS workshop_title, rooms.name AS assigned_room_name,
               (SELECT GROUP_CONCAT(guest_name, ', ') FROM workshop_booking_guests
                WHERE workshop_booking_id = workshop_bookings.id) AS party_names
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           LEFT JOIN rooms ON rooms.id = workshop_bookings.assigned_room_id
           ORDER BY workshop_sessions.start_date"""
    ).fetchall()
    conn.close()
    fieldnames = ["workshop_title", "reference_code", "guest_name", "party_names", "guest_email", "guest_phone",
                  "start_date", "end_date", "party_size", "occupancy_type", "assigned_room_name",
                  "requested_roommate", "dietary_notes", "medical_notes", "special_occasion", "status",
                  "total_price", "deposit_amount", "deposit_paid_at", "balance_amount", "balance_due_date",
                  "balance_paid_at", "notes", "created_at"]
    return csv_response(fieldnames, rows, "workshop_registrations.csv")


@app.route("/workshops/waitlist/join", methods=["POST"])
def join_workshop_waitlist():
    session_id_raw = request.form.get("session_id", "").strip()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", "").strip()
    party_size_raw = request.form.get("party_size", "").strip()
    notes = request.form.get("notes", "").strip()

    if not session_id_raw.isdigit():
        abort(404)
    session_id = int(session_id_raw)
    conn = get_db()
    if not conn.execute("SELECT 1 FROM workshop_sessions WHERE id = ?", (session_id,)).fetchone():
        conn.close()
        abort(404)

    if rate_limited(conn, "join_workshop_waitlist", BOOKING_RATE_LIMIT_PER_HOUR):
        conn.commit()
        conn.close()
        flash("Too many attempts from this connection — please try again in a bit.", "error")
        return redirect(url_for("workshop_register", session_id=session_id))
    if not name or not email:
        conn.commit()
        conn.close()
        flash("Name and email are required.", "error")
        return redirect(url_for("workshop_register", session_id=session_id))
    if not EMAIL_RE.match(email):
        conn.commit()
        conn.close()
        flash("Enter a valid email address.", "error")
        return redirect(url_for("workshop_register", session_id=session_id))

    party_size = int(party_size_raw) if party_size_raw.isdigit() else None
    conn.execute(
        """INSERT INTO workshop_waitlist (session_id, name, email, phone, party_size, notes, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
        (session_id, name, email, phone or None, party_size, notes or None, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    owner_to = owner_email(conn)
    if owner_to:
        send_email(
            owner_to, "New workshop waitlist request",
            f"{name} ({email}) would like a spot, party of {party_size or '?'}, but it's fully booked.\n\n{notes or ''}",
        )
    conn.close()
    flash("You're on the waitlist — we'll reach out if a spot opens up.", "success")
    return redirect(url_for("workshop_register", session_id=session_id))


@app.route("/admin/workshops/waitlist")
@owner_required
def admin_workshop_waitlist():
    conn = get_db()
    entries = conn.execute(
        """SELECT workshop_waitlist.*, workshop_sessions.start_date, workshop_sessions.end_date,
               workshops.title FROM workshop_waitlist
           JOIN workshop_sessions ON workshop_sessions.id = workshop_waitlist.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           ORDER BY (workshop_waitlist.status != 'open'), workshop_waitlist.created_at DESC"""
    ).fetchall()
    conn.close()
    return render_template("admin_workshop_waitlist.html", entries=entries)


@app.route("/admin/workshops/waitlist/export.csv")
@owner_required
def export_workshop_waitlist_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT workshop_waitlist.*, workshop_sessions.start_date, workshops.title FROM workshop_waitlist
           JOIN workshop_sessions ON workshop_sessions.id = workshop_waitlist.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           ORDER BY (workshop_waitlist.status != 'open'), workshop_waitlist.created_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["title", "start_date", "name", "email", "phone", "party_size", "notes", "status", "created_at"]
    return csv_response(fieldnames, rows, "workshop_waitlist.csv")


@app.route("/admin/workshops/waitlist/<int:entry_id>/status", methods=["POST"])
@owner_required
def update_workshop_waitlist_status(entry_id):
    status = request.form.get("status", "")
    if status not in ("open", "contacted", "booked", "closed"):
        abort(400)
    conn = get_db()
    conn.execute("UPDATE workshop_waitlist SET status = ? WHERE id = ?", (status, entry_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_workshop_waitlist"))


@app.route("/admin/expenses/export.csv")
@owner_required
def export_expenses_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT expenses.*, users.name AS submitter_name FROM expenses
           LEFT JOIN users ON users.id = expenses.submitted_by_user_id ORDER BY submitted_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["kind", "vendor_name", "submitter_name", "description", "amount", "status",
                  "restaurant_related", "submitted_at", "decided_at", "owner_note"]
    return csv_response(fieldnames, rows, "expenses.csv")


@app.route("/admin/guests/export.csv")
@owner_required
def export_guests_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM guests ORDER BY vip DESC, name").fetchall()
    conn.close()
    fieldnames = ["name", "email", "phone", "dietary_notes", "preferences", "vip", "notes", "created_at"]
    return csv_response(fieldnames, rows, "guests.csv")


# ---------------------------------------------------------------------------
# Management — owner-only business-control hub. Documents holds company-wide
# files (insurance, registration, bank details) as opposed to the per-person
# `documents` table above. Vault holds shared credentials, encrypted at rest;
# see the VAULT_ENCRYPTION_KEY comment near the top of the file.
# ---------------------------------------------------------------------------

@app.route("/management")
@owner_required
def management():
    conn = get_db()
    doc_count = conn.execute("SELECT COUNT(*) AS c FROM company_documents").fetchone()["c"]
    vault_count = conn.execute("SELECT COUNT(*) AS c FROM vault_entries").fetchone()["c"]
    bank_count = conn.execute("SELECT COUNT(*) AS c FROM bank_details").fetchone()["c"]
    recurring_count = conn.execute("SELECT COUNT(*) AS c FROM recurring_costs WHERE active = 1").fetchone()["c"]
    insurance_count = conn.execute("SELECT COUNT(*) AS c FROM insurance_policies").fetchone()["c"]
    vendor_count = conn.execute("SELECT COUNT(*) AS c FROM vendors").fetchone()["c"]
    vehicle_count = conn.execute("SELECT COUNT(*) AS c FROM vehicles").fetchone()["c"]
    company_info_set = conn.execute("SELECT 1 FROM company_info WHERE id = 1").fetchone() is not None
    today = datetime.now(timezone.utc).date()
    period = period_from_request()
    overview = management_overview(conn, period, today)
    current_financials = financial_month_summary(
        conn, today.replace(day=1),
        date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1),
    )
    restaurant_settings = get_restaurant_settings(conn)
    restaurant_pending_count = conn.execute(
        "SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status = 'pending'"
    ).fetchone()["c"]
    social_scheduled_count = conn.execute(
        "SELECT COUNT(*) AS c FROM social_posts WHERE status IN ('drafted', 'scheduled')"
    ).fetchone()["c"]
    conn.close()
    return render_template(
        "management.html", doc_count=doc_count, vault_count=vault_count, vault_enabled=vault_enabled(),
        bank_count=bank_count, recurring_count=recurring_count, insurance_count=insurance_count,
        company_info_set=company_info_set, current_financials=current_financials, vendor_count=vendor_count,
        vehicle_count=vehicle_count, restaurant_pending_count=restaurant_pending_count,
        restaurant_enabled=bool(restaurant_settings and restaurant_settings["enabled"]),
        social_scheduled_count=social_scheduled_count,
        overview=overview, period=period,
    )


@app.route("/management/financials")
@owner_required
def management_financials():
    today = datetime.now(timezone.utc).date()
    conn = get_db()
    months = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_end = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        summary = financial_month_summary(conn, cursor, month_end)
        summary["occupancy_rate"] = compute_month_stats(conn, cursor)["occupancy_rate"]
        months.append(summary)
        cursor = date(cursor.year - 1, 12, 1) if cursor.month == 1 else date(cursor.year, cursor.month - 1, 1)
    months.reverse()
    current = months[-1]
    month_end_for_current = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    room_revenue = room_revenue_breakdown(conn, today.replace(day=1), month_end_for_current)
    expense_breakdown = expense_category_breakdown(conn, today.replace(day=1), month_end_for_current)
    ytd = financial_month_summary(conn, date(today.year, 1, 1), today + timedelta(days=1))

    last_year_start = date(today.year - 1, today.month, 1)
    last_year_end = date(last_year_start.year + 1, 1, 1) if last_year_start.month == 12 else date(last_year_start.year, last_year_start.month + 1, 1)
    last_year = financial_month_summary(conn, last_year_start, last_year_end)
    last_year["occupancy_rate"] = compute_month_stats(conn, last_year_start)["occupancy_rate"]

    def pct_change(now_val, then_val):
        if not then_val:
            return None
        return round((now_val - then_val) / then_val * 100, 1)

    yoy = {
        "revenue_pct": pct_change(current["revenue"], last_year["revenue"]),
        "occupancy_pct": pct_change(current["occupancy_rate"], last_year["occupancy_rate"]),
    }
    conn.close()
    return render_template(
        "management_financials.html", months=months, current=current, today=today,
        room_revenue=room_revenue, ytd=ytd, expense_breakdown=expense_breakdown,
        last_year=last_year, last_year_start=last_year_start, yoy=yoy,
    )


@app.route("/management/financials/annual-summary")
@owner_required
def annual_summary():
    try:
        year = int(request.args.get("year", "") or datetime.now(timezone.utc).year)
    except ValueError:
        year = datetime.now(timezone.utc).year
    year_start = date(year, 1, 1)
    year_end = date(year + 1, 1, 1)

    conn = get_db()
    financials = financial_month_summary(conn, year_start, year_end)

    room_count = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
    days_in_year = (year_end - year_start).days
    overlapping = conn.execute(
        """SELECT arrival_date, departure_date FROM bookings
           WHERE status = 'confirmed' AND room_id IN (SELECT id FROM rooms WHERE active = 1)
           AND arrival_date < ? AND departure_date > ?""",
        (year_end.isoformat(), year_start.isoformat()),
    ).fetchall()
    booked_nights = 0
    for b in overlapping:
        b_start, b_end = parse_date(b["arrival_date"]), parse_date(b["departure_date"])
        if not b_start or not b_end:
            continue
        overlap_start, overlap_end = max(b_start, year_start), min(b_end, year_end)
        booked_nights += max(0, (overlap_end - overlap_start).days)
    total_possible_nights = days_in_year * room_count
    occupancy_rate = round(booked_nights / total_possible_nights * 100) if total_possible_nights else 0

    room_revenue = room_revenue_breakdown(conn, year_start, year_end)
    expense_breakdown = expense_category_breakdown(conn, year_start, year_end)
    top_vendors = conn.execute(
        """SELECT vendor_name, SUM(amount) AS total FROM expenses
           WHERE kind = 'supplier_invoice' AND status IN ('approved','paid') AND vendor_name IS NOT NULL
           AND submitted_at >= ? AND submitted_at < ? GROUP BY vendor_name ORDER BY total DESC LIMIT 10""",
        (year_start.isoformat(), year_end.isoformat()),
    ).fetchall()
    feedback_row = conn.execute(
        "SELECT AVG(rating) AS a, COUNT(*) AS c FROM guest_feedback WHERE submitted_at >= ? AND submitted_at < ?",
        (year_start.isoformat(), year_end.isoformat()),
    ).fetchone()
    booking_count = conn.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE status = 'confirmed' AND arrival_date >= ? AND arrival_date < ?",
        (year_start.isoformat(), year_end.isoformat()),
    ).fetchone()["c"]
    conn.close()

    return render_template(
        "annual_summary.html", year=year, financials=financials, occupancy_rate=occupancy_rate,
        room_revenue=room_revenue, expense_breakdown=expense_breakdown, top_vendors=top_vendors,
        feedback_avg=round(feedback_row["a"], 1) if feedback_row["a"] is not None else None,
        feedback_count=feedback_row["c"], booking_count=booking_count,
    )


@app.route("/management/financials/export.csv")
@owner_required
def export_financials_csv():
    today = datetime.now(timezone.utc).date()
    conn = get_db()
    months = []
    cursor = today.replace(day=1)
    for _ in range(12):
        month_end = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        months.append(financial_month_summary(conn, cursor, month_end))
        cursor = date(cursor.year - 1, 12, 1) if cursor.month == 1 else date(cursor.year, cursor.month - 1, 1)
    conn.close()
    months.reverse()
    rows = [
        {
            "month": m["month"].strftime("%Y-%m"),
            "revenue": m["revenue"],
            "staff_expenses": m["staff_expenses"],
            "supplier_expenses": m["supplier_expenses"],
            "expenses_total": m["expenses_total"],
            "estimated_labour_cost": m["labour_cost"] if m["labour_cost"] is not None else "",
            "net": m["net"],
        }
        for m in months
    ]
    fieldnames = ["month", "revenue", "staff_expenses", "supplier_expenses", "expenses_total",
                  "estimated_labour_cost", "net"]
    return csv_response(fieldnames, rows, f"financials_{today.isoformat()}.csv")


@app.route("/management/recurring-costs/export.csv")
@owner_required
def export_recurring_costs_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM recurring_costs ORDER BY (active = 0), category, label").fetchall()
    conn.close()
    fieldnames = ["label", "amount", "frequency", "category", "next_due_date", "active", "notes"]
    return csv_response(fieldnames, rows, "recurring_costs.csv")


@app.route("/management/insurance/export.csv")
@owner_required
def export_insurance_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM insurance_policies ORDER BY expiry_date").fetchall()
    conn.close()
    fieldnames = ["provider", "policy_number", "coverage_type", "premium", "premium_frequency",
                  "expiry_date", "notes"]
    return csv_response(fieldnames, rows, "insurance_policies.csv")


@app.route("/management/vendors/export.csv")
@owner_required
def export_vendors_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    conn.close()
    fieldnames = ["name", "contact_person", "phone", "email", "payment_terms", "notes"]
    return csv_response(fieldnames, rows, "vendors.csv")


@app.route("/management/recurring-costs")
@owner_required
def management_recurring_costs():
    conn = get_db()
    costs = conn.execute(
        "SELECT * FROM recurring_costs ORDER BY (active = 0), category, label"
    ).fetchall()
    conn.close()
    monthly_equivalent = round(sum(
        c["amount"] if c["frequency"] == "monthly" else c["amount"] / 12
        for c in costs if c["active"]
    ), 2)
    return render_template(
        "management_recurring_costs.html", costs=costs, monthly_equivalent=monthly_equivalent,
    )


@app.route("/management/recurring-costs/new", methods=["POST"])
@owner_required
def new_recurring_cost():
    label = request.form.get("label", "").strip()
    amount = request.form.get("amount", "").strip()
    frequency = request.form.get("frequency", "monthly")
    category = request.form.get("category", "").strip()
    next_due_date = request.form.get("next_due_date", "").strip()
    notes = request.form.get("notes", "").strip()
    if frequency not in ("monthly", "annual"):
        frequency = "monthly"
    try:
        amount_val = float(amount)
    except ValueError:
        amount_val = 0.0
    if not label:
        flash("A label is required.", "error")
        return redirect(url_for("management_recurring_costs"))
    conn = get_db()
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category, next_due_date, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (label, amount_val, frequency, category or None, next_due_date or None, notes or None,
         datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "recurring_cost_created", target=label)
    conn.commit()
    conn.close()
    flash("Recurring cost added.", "success")
    return redirect(url_for("management_recurring_costs"))


@app.route("/management/recurring-costs/<int:cost_id>/edit", methods=["POST"])
@owner_required
def edit_recurring_cost(cost_id):
    label = request.form.get("label", "").strip()
    amount = request.form.get("amount", "").strip()
    frequency = request.form.get("frequency", "monthly")
    category = request.form.get("category", "").strip()
    next_due_date = request.form.get("next_due_date", "").strip()
    notes = request.form.get("notes", "").strip()
    if frequency not in ("monthly", "annual"):
        frequency = "monthly"
    try:
        amount_val = float(amount)
    except ValueError:
        amount_val = 0.0
    if not label:
        flash("A label is required.", "error")
        return redirect(url_for("management_recurring_costs"))
    conn = get_db()
    conn.execute(
        """UPDATE recurring_costs SET label = ?, amount = ?, frequency = ?, category = ?,
           next_due_date = ?, notes = ? WHERE id = ?""",
        (label, amount_val, frequency, category or None, next_due_date or None, notes or None, cost_id),
    )
    log_audit(conn, "recurring_cost_edited", target=label)
    conn.commit()
    conn.close()
    flash("Recurring cost updated.", "success")
    return redirect(url_for("management_recurring_costs"))


@app.route("/management/recurring-costs/<int:cost_id>/toggle-active", methods=["POST"])
@owner_required
def toggle_recurring_cost(cost_id):
    conn = get_db()
    cost = conn.execute("SELECT * FROM recurring_costs WHERE id = ?", (cost_id,)).fetchone()
    if not cost:
        conn.close()
        abort(404)
    conn.execute("UPDATE recurring_costs SET active = ? WHERE id = ?", (0 if cost["active"] else 1, cost_id))
    conn.commit()
    conn.close()
    return redirect(url_for("management_recurring_costs"))


@app.route("/management/recurring-costs/<int:cost_id>/mark-paid", methods=["POST"])
@owner_required
def mark_recurring_cost_paid(cost_id):
    conn = get_db()
    cost = conn.execute("SELECT * FROM recurring_costs WHERE id = ?", (cost_id,)).fetchone()
    if not cost:
        conn.close()
        abort(404)
    current_due = parse_date(cost["next_due_date"]) or datetime.now(timezone.utc).date()
    new_due = add_months(current_due, 12 if cost["frequency"] == "annual" else 1)
    # Compare-and-swap on the due date we actually read — a double-click
    # racing this same handler would otherwise advance the date twice
    # (e.g. two months forward instead of one), not just double-log it.
    old_due = cost["next_due_date"]
    cur = conn.execute(
        "UPDATE recurring_costs SET next_due_date = ? WHERE id = ? AND (next_due_date = ? OR (next_due_date IS NULL AND ? IS NULL))",
        (new_due.isoformat(), cost_id, old_due, old_due),
    )
    if cur.rowcount == 0:
        conn.close()
        flash("Already marked paid.", "success")
        return redirect(url_for("management_recurring_costs"))
    log_audit(conn, "recurring_cost_paid", target=cost["label"], details=f"next due {new_due.isoformat()}")
    conn.commit()
    conn.close()
    flash(f"Marked paid — next due {new_due.isoformat()}.", "success")
    return redirect(url_for("management_recurring_costs"))


@app.route("/management/recurring-costs/<int:cost_id>/delete", methods=["POST"])
@owner_required
def delete_recurring_cost(cost_id):
    conn = get_db()
    cost = conn.execute("SELECT * FROM recurring_costs WHERE id = ?", (cost_id,)).fetchone()
    conn.execute("DELETE FROM recurring_costs WHERE id = ?", (cost_id,))
    if cost:
        log_audit(conn, "recurring_cost_deleted", target=cost["label"])
    conn.commit()
    conn.close()
    flash("Removed.", "success")
    return redirect(url_for("management_recurring_costs"))


@app.route("/management/insurance")
@owner_required
def management_insurance():
    conn = get_db()
    policies = conn.execute("SELECT * FROM insurance_policies ORDER BY expiry_date IS NULL, expiry_date").fetchall()
    conn.close()
    return render_template("management_insurance.html", policies=policies)


@app.route("/management/insurance/new", methods=["POST"])
@owner_required
def new_insurance_policy():
    provider = request.form.get("provider", "").strip()
    policy_number = request.form.get("policy_number", "").strip()
    coverage_type = request.form.get("coverage_type", "").strip()
    premium = request.form.get("premium", "").strip()
    premium_frequency = request.form.get("premium_frequency", "annual")
    expiry_date = request.form.get("expiry_date", "").strip()
    notes = request.form.get("notes", "").strip()
    vehicle_id = request.form.get("vehicle_id", "").strip()
    redirect_to = "management_vehicles" if vehicle_id else "management_insurance"
    if premium_frequency not in ("monthly", "annual"):
        premium_frequency = "annual"
    try:
        premium_val = float(premium) if premium else None
    except ValueError:
        premium_val = None
    if not provider:
        flash("A provider is required.", "error")
        return redirect(url_for(redirect_to))
    conn = get_db()
    conn.execute(
        """INSERT INTO insurance_policies (provider, policy_number, coverage_type, premium,
           premium_frequency, expiry_date, notes, vehicle_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (provider, policy_number or None, coverage_type or None, premium_val, premium_frequency,
         expiry_date or None, notes or None, vehicle_id or None, datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "insurance_policy_created", target=provider)
    conn.commit()
    conn.close()
    flash("Policy added.", "success")
    return redirect(url_for(redirect_to))


@app.route("/management/insurance/<int:policy_id>/edit", methods=["POST"])
@owner_required
def edit_insurance_policy(policy_id):
    provider = request.form.get("provider", "").strip()
    policy_number = request.form.get("policy_number", "").strip()
    coverage_type = request.form.get("coverage_type", "").strip()
    premium = request.form.get("premium", "").strip()
    premium_frequency = request.form.get("premium_frequency", "annual")
    expiry_date = request.form.get("expiry_date", "").strip()
    notes = request.form.get("notes", "").strip()
    if premium_frequency not in ("monthly", "annual"):
        premium_frequency = "annual"
    try:
        premium_val = float(premium) if premium else None
    except ValueError:
        premium_val = None
    if not provider:
        flash("A provider is required.", "error")
        return redirect(url_for("management_insurance"))
    conn = get_db()
    conn.execute(
        """UPDATE insurance_policies SET provider = ?, policy_number = ?, coverage_type = ?,
           premium = ?, premium_frequency = ?, expiry_date = ?, notes = ? WHERE id = ?""",
        (provider, policy_number or None, coverage_type or None, premium_val, premium_frequency,
         expiry_date or None, notes or None, policy_id),
    )
    log_audit(conn, "insurance_policy_edited", target=provider)
    conn.commit()
    conn.close()
    flash("Policy updated.", "success")
    return redirect(url_for("management_insurance"))


@app.route("/management/insurance/<int:policy_id>/renew", methods=["POST"])
@owner_required
def renew_insurance_policy(policy_id):
    conn = get_db()
    policy = conn.execute("SELECT * FROM insurance_policies WHERE id = ?", (policy_id,)).fetchone()
    if not policy:
        conn.close()
        abort(404)
    current_expiry = parse_date(policy["expiry_date"]) or datetime.now(timezone.utc).date()
    new_expiry = add_months(current_expiry, 12 if policy["premium_frequency"] != "monthly" else 1)
    old_expiry = policy["expiry_date"]
    cur = conn.execute(
        "UPDATE insurance_policies SET expiry_date = ? WHERE id = ? AND (expiry_date = ? OR (expiry_date IS NULL AND ? IS NULL))",
        (new_expiry.isoformat(), policy_id, old_expiry, old_expiry),
    )
    if cur.rowcount == 0:
        conn.close()
        flash("Already renewed.", "success")
        return redirect(url_for("management_insurance"))
    log_audit(conn, "insurance_policy_renewed", target=policy["provider"], details=f"new expiry {new_expiry.isoformat()}")
    conn.commit()
    conn.close()
    flash(f"Renewed — new expiry {new_expiry.isoformat()}.", "success")
    return redirect(url_for("management_insurance"))


@app.route("/management/insurance/<int:policy_id>/delete", methods=["POST"])
@owner_required
def delete_insurance_policy(policy_id):
    conn = get_db()
    policy = conn.execute("SELECT * FROM insurance_policies WHERE id = ?", (policy_id,)).fetchone()
    conn.execute("DELETE FROM insurance_policies WHERE id = ?", (policy_id,))
    if policy:
        log_audit(conn, "insurance_policy_deleted", target=policy["provider"])
    conn.commit()
    conn.close()
    flash("Removed.", "success")
    return redirect(url_for("management_insurance"))


@app.route("/management/vendors")
@owner_required
def vendors():
    conn = get_db()
    q = request.args.get("q", "").strip()
    rows = conn.execute("SELECT * FROM vendors ORDER BY name").fetchall()
    spend_by_vendor = {
        r["vendor_name"]: r["total"] for r in conn.execute(
            """SELECT vendor_name, SUM(amount) AS total FROM expenses
               WHERE kind = 'supplier_invoice' AND status IN ('approved','paid') AND vendor_name IS NOT NULL
               GROUP BY vendor_name"""
        ).fetchall()
    }
    conn.close()
    if q:
        needle = q.lower()
        rows = [
            v for v in rows
            if needle in v["name"].lower() or needle in (v["contact_person"] or "").lower()
        ]
    return render_template("vendors.html", vendors=rows, q=q, spend_by_vendor=spend_by_vendor)


@app.route("/management/vendors/new", methods=["POST"])
@owner_required
def new_vendor():
    name = request.form.get("name", "").strip()
    contact_person = request.form.get("contact_person", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    payment_terms = request.form.get("payment_terms", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("vendors"))
    conn = get_db()
    conn.execute(
        """INSERT INTO vendors (name, contact_person, phone, email, payment_terms, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, contact_person or None, phone or None, email or None, payment_terms or None,
         notes or None, datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "vendor_created", target=name)
    conn.commit()
    conn.close()
    flash("Vendor added.", "success")
    return redirect(url_for("vendors"))


@app.route("/management/vendors/<int:vendor_id>/edit", methods=["POST"])
@owner_required
def edit_vendor(vendor_id):
    name = request.form.get("name", "").strip()
    contact_person = request.form.get("contact_person", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    payment_terms = request.form.get("payment_terms", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("vendors"))
    conn = get_db()
    conn.execute(
        """UPDATE vendors SET name = ?, contact_person = ?, phone = ?, email = ?,
           payment_terms = ?, notes = ? WHERE id = ?""",
        (name, contact_person or None, phone or None, email or None, payment_terms or None,
         notes or None, vendor_id),
    )
    log_audit(conn, "vendor_edited", target=name)
    conn.commit()
    conn.close()
    return redirect(url_for("vendors"))


@app.route("/management/vendors/<int:vendor_id>/delete", methods=["POST"])
@owner_required
def delete_vendor(vendor_id):
    conn = get_db()
    vendor = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    conn.execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
    if vendor:
        log_audit(conn, "vendor_deleted", target=vendor["name"])
    conn.commit()
    conn.close()
    flash("Vendor removed.", "success")
    return redirect(url_for("vendors"))


# ---------------------------------------------------------------------------
# Social media content calendar
# ---------------------------------------------------------------------------

@app.route("/management/social")
@owner_required
def management_social():
    status_filter = request.args.get("status", "").strip()
    platform_filter = request.args.get("platform", "").strip()
    conn = get_db()
    query = """SELECT social_posts.*, users.name AS assignee_name FROM social_posts
               LEFT JOIN users ON users.id = social_posts.assigned_to_user_id WHERE 1=1"""
    params = []
    if status_filter:
        query += " AND social_posts.status = ?"
        params.append(status_filter)
    if platform_filter:
        query += " AND social_posts.platform = ?"
        params.append(platform_filter)
    query += " ORDER BY (social_posts.scheduled_date IS NULL), social_posts.scheduled_date, social_posts.created_at"
    posts = conn.execute(query, params).fetchall()
    platforms = [r["platform"] for r in conn.execute(
        "SELECT DISTINCT platform FROM social_posts ORDER BY platform"
    ).fetchall()]
    employees = conn.execute(
        "SELECT id, name FROM users WHERE status = 'active' ORDER BY name"
    ).fetchall()
    today = datetime.now(timezone.utc).date().isoformat()
    conn.close()
    return render_template(
        "management_social.html", posts=posts, platforms=platforms, employees=employees,
        status_filter=status_filter, platform_filter=platform_filter, today=today,
    )


@app.route("/management/social/export.csv")
@owner_required
def export_social_posts_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT social_posts.*, users.name AS assignee_name FROM social_posts
           LEFT JOIN users ON users.id = social_posts.assigned_to_user_id
           ORDER BY (social_posts.scheduled_date IS NULL), social_posts.scheduled_date"""
    ).fetchall()
    conn.close()
    fieldnames = ["platform", "post_type", "scheduled_date", "scheduled_time", "status",
                  "assignee_name", "caption", "link", "notes", "posted_at"]
    return csv_response(fieldnames, rows, "social_posts.csv")


@app.route("/management/social/new", methods=["POST"])
@owner_required
def new_social_post():
    platform = request.form.get("platform", "").strip() or "Instagram"
    caption = request.form.get("caption", "").strip()
    post_type = request.form.get("post_type", "").strip()
    scheduled_date = request.form.get("scheduled_date", "").strip()
    scheduled_time = request.form.get("scheduled_time", "").strip()
    assigned_to_raw = request.form.get("assigned_to_user_id", "").strip()
    link = request.form.get("link", "").strip()
    notes = request.form.get("notes", "").strip()
    if not caption:
        flash("A caption or content idea is required.", "error")
        return redirect(url_for("management_social"))
    conn = get_db()
    conn.execute(
        """INSERT INTO social_posts (platform, caption, post_type, scheduled_date, scheduled_time,
           assigned_to_user_id, link, notes, created_by_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (platform, caption, post_type or None, scheduled_date or None, scheduled_time or None,
         int(assigned_to_raw) if assigned_to_raw.isdigit() else None, link or None, notes or None,
         current_user()["id"], datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Post added to the schedule.", "success")
    return redirect(url_for("management_social"))


@app.route("/management/social/<int:post_id>/edit", methods=["POST"])
@owner_required
def edit_social_post(post_id):
    platform = request.form.get("platform", "").strip() or "Instagram"
    caption = request.form.get("caption", "").strip()
    post_type = request.form.get("post_type", "").strip()
    scheduled_date = request.form.get("scheduled_date", "").strip()
    scheduled_time = request.form.get("scheduled_time", "").strip()
    status = request.form.get("status", "").strip()
    assigned_to_raw = request.form.get("assigned_to_user_id", "").strip()
    link = request.form.get("link", "").strip()
    notes = request.form.get("notes", "").strip()
    if not caption:
        flash("A caption or content idea is required.", "error")
        return redirect(url_for("management_social"))
    conn = get_db()
    conn.execute(
        """UPDATE social_posts SET platform = ?, caption = ?, post_type = ?, scheduled_date = ?,
           scheduled_time = ?, status = ?, assigned_to_user_id = ?, link = ?, notes = ? WHERE id = ?""",
        (platform, caption, post_type or None, scheduled_date or None, scheduled_time or None,
         status if status in ("idea", "drafted", "scheduled", "posted") else "idea",
         int(assigned_to_raw) if assigned_to_raw.isdigit() else None, link or None, notes or None, post_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("management_social"))


@app.route("/management/social/<int:post_id>/mark-posted", methods=["POST"])
@owner_required
def mark_social_post_posted(post_id):
    conn = get_db()
    conn.execute(
        "UPDATE social_posts SET status = 'posted', posted_at = ? WHERE id = ? AND status != 'posted'",
        (datetime.now(timezone.utc).isoformat(), post_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("management_social"))


@app.route("/management/social/<int:post_id>/delete", methods=["POST"])
@owner_required
def delete_social_post(post_id):
    conn = get_db()
    conn.execute("DELETE FROM social_posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()
    flash("Post removed.", "success")
    return redirect(url_for("management_social"))


@app.route("/management/vehicles")
@owner_required
def management_vehicles():
    conn = get_db()
    vehicles = conn.execute("SELECT * FROM vehicles ORDER BY name").fetchall()

    maintenance_by_vehicle = {}
    for row in conn.execute(
        """SELECT vehicle_maintenance.*, users.name AS reported_by_name
           FROM vehicle_maintenance LEFT JOIN users ON users.id = vehicle_maintenance.reported_by_user_id
           WHERE vehicle_maintenance.status = 'open' ORDER BY vehicle_maintenance.created_at DESC"""
    ).fetchall():
        maintenance_by_vehicle.setdefault(row["vehicle_id"], []).append(row)

    current_usage = {}
    for row in conn.execute(
        """SELECT vehicle_usage.*, users.name AS user_name
           FROM vehicle_usage LEFT JOIN users ON users.id = vehicle_usage.user_id
           WHERE checked_in_at IS NULL"""
    ).fetchall():
        current_usage[row["vehicle_id"]] = row

    recent_transfers = {}
    for row in conn.execute(
        """SELECT vehicle_transfers.*, users.name AS driver_name
           FROM vehicle_transfers LEFT JOIN users ON users.id = vehicle_transfers.driver_user_id
           ORDER BY vehicle_transfers.scheduled_at DESC"""
    ).fetchall():
        recent_transfers.setdefault(row["vehicle_id"], []).append(row)

    next_transfer_by_vehicle = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in conn.execute(
        """SELECT vehicle_transfers.*, users.name AS driver_name
           FROM vehicle_transfers LEFT JOIN users ON users.id = vehicle_transfers.driver_user_id
           WHERE scheduled_at >= ? ORDER BY scheduled_at""",
        (now_iso,),
    ).fetchall():
        next_transfer_by_vehicle.setdefault(row["vehicle_id"], row)

    insurance_by_vehicle = {}
    for row in conn.execute(
        "SELECT * FROM insurance_policies WHERE vehicle_id IS NOT NULL ORDER BY expiry_date IS NULL, expiry_date"
    ).fetchall():
        insurance_by_vehicle.setdefault(row["vehicle_id"], []).append(row)

    spend_by_vehicle = {
        row["vehicle_id"]: row["total"] for row in conn.execute(
            """SELECT vehicle_id, SUM(amount) AS total FROM expenses
               WHERE vehicle_id IS NOT NULL AND status IN ('approved', 'paid')
               GROUP BY vehicle_id"""
        ).fetchall()
    }
    drivers = conn.execute("SELECT id, name FROM users WHERE status = 'active' ORDER BY name").fetchall()
    conn.close()
    return render_template(
        "management_vehicles.html", vehicles=vehicles,
        maintenance_by_vehicle=maintenance_by_vehicle, current_usage=current_usage,
        recent_transfers=recent_transfers, next_transfer_by_vehicle=next_transfer_by_vehicle,
        insurance_by_vehicle=insurance_by_vehicle,
        spend_by_vehicle=spend_by_vehicle, drivers=drivers,
    )


@app.route("/management/vehicles/export.csv")
@owner_required
def export_vehicles_csv():
    conn = get_db()
    rows = conn.execute("SELECT * FROM vehicles ORDER BY name").fetchall()
    conn.close()
    fieldnames = ["name", "vehicle_type", "fuel_type", "license_plate", "cleanliness",
                  "fuel_level", "next_service_due", "notes"]
    return csv_response(fieldnames, rows, "vehicles.csv")


@app.route("/management/vehicles/new", methods=["POST"])
@owner_required
def new_vehicle():
    name = request.form.get("name", "").strip()
    vehicle_type = request.form.get("vehicle_type", "").strip()
    fuel_type = request.form.get("fuel_type", "").strip()
    license_plate = request.form.get("license_plate", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("management_vehicles"))
    conn = get_db()
    conn.execute(
        """INSERT INTO vehicles (name, vehicle_type, fuel_type, license_plate, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, vehicle_type or None, fuel_type or None, license_plate or None, notes or None,
         datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "vehicle_created", target=name)
    conn.commit()
    conn.close()
    flash("Vehicle added.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/edit", methods=["POST"])
@owner_required
def edit_vehicle(vehicle_id):
    name = request.form.get("name", "").strip()
    vehicle_type = request.form.get("vehicle_type", "").strip()
    fuel_type = request.form.get("fuel_type", "").strip()
    license_plate = request.form.get("license_plate", "").strip()
    next_service_due = request.form.get("next_service_due", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("A name is required.", "error")
        return redirect(url_for("management_vehicles"))
    conn = get_db()
    conn.execute(
        """UPDATE vehicles SET name = ?, vehicle_type = ?, fuel_type = ?, license_plate = ?,
           next_service_due = ?, notes = ? WHERE id = ?""",
        (name, vehicle_type or None, fuel_type or None, license_plate or None,
         next_service_due or None, notes or None, vehicle_id),
    )
    log_audit(conn, "vehicle_edited", target=name)
    conn.commit()
    conn.close()
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/delete", methods=["POST"])
@owner_required
def delete_vehicle(vehicle_id):
    conn = get_db()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    conn.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
    if vehicle:
        log_audit(conn, "vehicle_deleted", target=vehicle["name"])
    conn.commit()
    conn.close()
    flash("Vehicle removed.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/toggle-clean", methods=["POST"])
@owner_required
def toggle_vehicle_cleanliness(vehicle_id):
    conn = get_db()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if not vehicle:
        conn.close()
        abort(404)
    new_state = "dirty" if vehicle["cleanliness"] == "clean" else "clean"
    conn.execute("UPDATE vehicles SET cleanliness = ? WHERE id = ?", (new_state, vehicle_id))
    conn.commit()
    conn.close()
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/toggle-fuel", methods=["POST"])
@owner_required
def toggle_vehicle_fuel(vehicle_id):
    conn = get_db()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if not vehicle:
        conn.close()
        abort(404)
    new_state = "low" if vehicle["fuel_level"] == "ok" else "ok"
    conn.execute("UPDATE vehicles SET fuel_level = ? WHERE id = ?", (new_state, vehicle_id))
    conn.commit()
    conn.close()
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/log-service", methods=["POST"])
@owner_required
def log_vehicle_service(vehicle_id):
    conn = get_db()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if not vehicle:
        conn.close()
        abort(404)
    new_due = add_months(datetime.now(timezone.utc).date(), 6)
    conn.execute("UPDATE vehicles SET next_service_due = ? WHERE id = ?", (new_due.isoformat(), vehicle_id))
    conn.execute(
        """INSERT INTO vehicle_maintenance (vehicle_id, title, status, reported_by_user_id, created_at, resolved_at)
           VALUES (?, 'Service completed', 'resolved', ?, ?, ?)""",
        (vehicle_id, current_user()["id"], datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "vehicle_service_logged", target=vehicle["name"], details=f"next due {new_due.isoformat()}")
    conn.commit()
    conn.close()
    flash(f"Service logged — next due {new_due.isoformat()}.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/maintenance/new", methods=["POST"])
@owner_required
def new_vehicle_maintenance(vehicle_id):
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not title:
        flash("A short title is required.", "error")
        return redirect(url_for("management_vehicles"))
    conn = get_db()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if not vehicle:
        conn.close()
        abort(404)
    conn.execute(
        """INSERT INTO vehicle_maintenance (vehicle_id, title, description, reported_by_user_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (vehicle_id, title, description or None, current_user()["id"], datetime.now(timezone.utc).isoformat()),
    )
    log_audit(conn, "vehicle_maintenance_reported", target=f"{vehicle['name']} — {title}")
    conn.commit()
    conn.close()
    flash("Maintenance item logged.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/maintenance/<int:item_id>/resolve", methods=["POST"])
@owner_required
def resolve_vehicle_maintenance(item_id):
    conn = get_db()
    conn.execute(
        "UPDATE vehicle_maintenance SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), item_id),
    )
    conn.commit()
    conn.close()
    flash("Maintenance item resolved.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/maintenance/<int:item_id>/delete", methods=["POST"])
@owner_required
def delete_vehicle_maintenance(item_id):
    conn = get_db()
    conn.execute("DELETE FROM vehicle_maintenance WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    flash("Maintenance item removed.", "success")
    return redirect(url_for("management_vehicles"))


def vehicle_transfer_conflict(conn, vehicle_id, for_user_id):
    """The nearest transfer within VEHICLE_TRANSFER_BUFFER_HOURS of now for
    this vehicle that this checkout would conflict with, or None. Checking
    the vehicle out to its own assigned driver is never a conflict — that's
    the point of the transfer."""
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=VEHICLE_TRANSFER_BUFFER_HOURS)).isoformat()
    window_end = (now + timedelta(hours=VEHICLE_TRANSFER_BUFFER_HOURS)).isoformat()
    return conn.execute(
        """SELECT vehicle_transfers.*, users.name AS driver_name
           FROM vehicle_transfers LEFT JOIN users ON users.id = vehicle_transfers.driver_user_id
           WHERE vehicle_id = ? AND scheduled_at BETWEEN ? AND ?
             AND (driver_user_id IS NULL OR driver_user_id != ?)
           ORDER BY scheduled_at LIMIT 1""",
        (vehicle_id, window_start, window_end, for_user_id),
    ).fetchone()


@app.route("/management/vehicles/<int:vehicle_id>/checkout", methods=["POST"])
@owner_required
def checkout_vehicle(vehicle_id):
    user_id = request.form.get("user_id", "")
    purpose = request.form.get("purpose", "").strip()
    if not user_id.isdigit():
        flash("Choose who's taking it.", "error")
        return redirect(url_for("management_vehicles"))
    conn = get_db()
    already_out = conn.execute(
        "SELECT id FROM vehicle_usage WHERE vehicle_id = ? AND checked_in_at IS NULL", (vehicle_id,)
    ).fetchone()
    if already_out:
        conn.close()
        flash("That vehicle is already checked out.", "error")
        return redirect(url_for("management_vehicles"))
    conflict = vehicle_transfer_conflict(conn, vehicle_id, int(user_id))
    if conflict:
        conn.close()
        when = local_datetime_str(conflict["scheduled_at"])
        who = f"assign it to {conflict['driver_name']}, or " if conflict["driver_name"] else ""
        flash(
            f"That vehicle is needed for a {conflict['direction']} "
            f"({conflict['guest_name'] or 'guest'}) at {when} — {who}"
            f"adjust the transfer first if it's no longer happening.",
            "error",
        )
        return redirect(url_for("management_vehicles"))
    try:
        conn.execute(
            "INSERT INTO vehicle_usage (vehicle_id, user_id, purpose, checked_out_at) VALUES (?, ?, ?, ?)",
            (vehicle_id, int(user_id), purpose or None, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # A second checkout submission (double-click) beat this one to
        # it — the partial unique index is the real guard, the
        # `already_out` check above is just the common-case fast path.
        conn.rollback()
        conn.close()
        flash("That vehicle is already checked out.", "error")
        return redirect(url_for("management_vehicles"))
    conn.close()
    flash("Checked out.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/management/vehicles/<int:vehicle_id>/checkin", methods=["POST"])
@owner_required
def checkin_vehicle(vehicle_id):
    conn = get_db()
    conn.execute(
        "UPDATE vehicle_usage SET checked_in_at = ? WHERE vehicle_id = ? AND checked_in_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), vehicle_id),
    )
    conn.commit()
    conn.close()
    flash("Checked in.", "success")
    return redirect(url_for("management_vehicles"))


@app.route("/transfers")
@login_required
def all_transfers():
    """Every scheduled pickup/dropoff across the whole fleet, on one page.

    Transfers were only ever visible per-vehicle, so answering "who's driving
    the airport run today" meant opening each vehicle in turn. Drivers are
    ordinary staff, so this is login_required rather than owner-only — an
    employee needs to see the run they've been assigned. Creating and deleting
    transfers stays owner-only on the per-vehicle page.
    """
    today = datetime.now(timezone.utc).date()
    conn = get_db()
    rows = conn.execute(
        """SELECT vehicle_transfers.*, vehicles.name AS vehicle_name,
                  users.name AS driver_name
           FROM vehicle_transfers
           JOIN vehicles ON vehicles.id = vehicle_transfers.vehicle_id
           LEFT JOIN users ON users.id = vehicle_transfers.driver_user_id
           ORDER BY vehicle_transfers.scheduled_at"""
    ).fetchall()
    conn.close()

    today_iso = today.isoformat()
    upcoming, todays, past = [], [], []
    for r in rows:
        day = (r["scheduled_at"] or "")[:10]
        if day == today_iso:
            todays.append(r)
        elif day > today_iso:
            upcoming.append(r)
        else:
            past.append(r)
    past.reverse()  # most recent first

    return render_template(
        "all_transfers.html", todays=todays, upcoming=upcoming, past=past[:20],
        today=today, unassigned=sum(1 for r in rows if not r["driver_user_id"]
                                    and (r["scheduled_at"] or "")[:10] >= today_iso),
    )


@app.route("/management/vehicles/<int:vehicle_id>/transfers")
@owner_required
def vehicle_transfers_page(vehicle_id):
    conn = get_db()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    if not vehicle:
        conn.close()
        abort(404)
    transfers = conn.execute(
        """SELECT vehicle_transfers.*, users.name AS driver_name
           FROM vehicle_transfers LEFT JOIN users ON users.id = vehicle_transfers.driver_user_id
           WHERE vehicle_id = ? ORDER BY scheduled_at DESC""",
        (vehicle_id,),
    ).fetchall()
    drivers = conn.execute("SELECT id, name FROM users WHERE status = 'active' ORDER BY name").fetchall()
    conn.close()
    return render_template("vehicle_transfers.html", vehicle=vehicle, transfers=transfers, drivers=drivers)


@app.route("/management/vehicles/<int:vehicle_id>/transfers/new", methods=["POST"])
@owner_required
def new_vehicle_transfer(vehicle_id):
    guest_name = request.form.get("guest_name", "").strip()
    direction = request.form.get("direction", "")
    scheduled_at = request.form.get("scheduled_at", "").strip()
    driver_user_id = request.form.get("driver_user_id", "")
    notes = request.form.get("notes", "").strip()
    if direction not in ("pickup", "dropoff") or not scheduled_at:
        flash("A direction and scheduled time are required.", "error")
        return redirect(url_for("vehicle_transfers_page", vehicle_id=vehicle_id))
    try:
        scheduled_at_utc = local_datetime_input_to_utc_iso(scheduled_at)
    except ValueError:
        flash("That scheduled time didn't look right — please try again.", "error")
        return redirect(url_for("vehicle_transfers_page", vehicle_id=vehicle_id))
    conn = get_db()
    conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction, scheduled_at, driver_user_id, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (vehicle_id, guest_name or None, direction, scheduled_at_utc,
         int(driver_user_id) if driver_user_id.isdigit() else None, notes or None,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    flash("Transfer logged.", "success")
    return redirect(url_for("vehicle_transfers_page", vehicle_id=vehicle_id))


@app.route("/management/vehicles/transfers/<int:transfer_id>/delete", methods=["POST"])
@owner_required
def delete_vehicle_transfer(transfer_id):
    conn = get_db()
    transfer = conn.execute("SELECT vehicle_id FROM vehicle_transfers WHERE id = ?", (transfer_id,)).fetchone()
    if not transfer:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM vehicle_transfers WHERE id = ?", (transfer_id,))
    conn.commit()
    conn.close()
    flash("Transfer removed.", "success")
    return redirect(url_for("vehicle_transfers_page", vehicle_id=transfer["vehicle_id"]))


@app.route("/management/documents")
@owner_required
def management_documents():
    conn = get_db()
    docs = conn.execute("SELECT * FROM company_documents ORDER BY uploaded_at DESC").fetchall()
    conn.close()
    return render_template("management_documents.html", docs=docs)


@app.route("/management/documents/upload", methods=["POST"])
@owner_required
def upload_company_document():
    user = current_user()
    file = request.files.get("document")
    title = request.form.get("title", "").strip()
    expiry_date = request.form.get("expiry_date", "").strip()

    if not file or file.filename == "" or not title:
        flash("A title and a file are both required.", "error")
        return redirect(url_for("management_documents"))
    if not allowed_file(file.filename):
        flash(f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}", "error")
        return redirect(url_for("management_documents"))

    safe_name = secure_filename(file.filename)
    stored_name = f"company_{secrets.token_hex(6)}_{safe_name}"
    file.save(os.path.join(UPLOAD_DIR, stored_name))

    conn = get_db()
    conn.execute(
        """INSERT INTO company_documents (title, filename, uploaded_by_user_id, uploaded_at, expiry_date)
           VALUES (?, ?, ?, ?, ?)""",
        (title, stored_name, user["id"], datetime.now(timezone.utc).isoformat(), expiry_date or None),
    )
    conn.commit()
    conn.close()
    flash("Document uploaded.", "success")
    return redirect(url_for("management_documents"))


@app.route("/management/documents/<int:doc_id>/edit", methods=["POST"])
@owner_required
def edit_company_document(doc_id):
    title = request.form.get("title", "").strip()
    expiry_date = request.form.get("expiry_date", "").strip()
    if not title:
        flash("A title is required.", "error")
        return redirect(url_for("management_documents"))
    conn = get_db()
    doc = conn.execute("SELECT * FROM company_documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE company_documents SET title = ?, expiry_date = ? WHERE id = ?",
        (title, expiry_date or None, doc_id),
    )
    conn.commit()
    conn.close()
    flash("Document updated.", "success")
    return redirect(url_for("management_documents"))


@app.route("/management/documents/<int:doc_id>/download")
@owner_required
def download_company_document(doc_id):
    conn = get_db()
    doc = conn.execute("SELECT * FROM company_documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not doc:
        abort(404)
    return send_from_directory(UPLOAD_DIR, doc["filename"], as_attachment=True)


@app.route("/management/documents/<int:doc_id>/view")
@owner_required
def view_company_document(doc_id):
    conn = get_db()
    doc = conn.execute("SELECT * FROM company_documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    if not doc or not is_viewable(doc["filename"]):
        abort(404)
    return send_from_directory(UPLOAD_DIR, doc["filename"], as_attachment=False)


@app.route("/management/documents/<int:doc_id>/delete", methods=["POST"])
@owner_required
def delete_company_document(doc_id):
    conn = get_db()
    doc = conn.execute("SELECT * FROM company_documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        conn.close()
        abort(404)
    path = os.path.join(UPLOAD_DIR, doc["filename"])
    if os.path.exists(path):
        os.remove(path)
    conn.execute("DELETE FROM company_documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    flash("Document removed.", "success")
    return redirect(url_for("management_documents"))


@app.route("/management/email-templates")
@owner_required
def management_email_templates():
    conn = get_db()
    templates = conn.execute("SELECT * FROM email_templates ORDER BY label").fetchall()
    conn.close()
    return render_template("management_email_templates.html", templates=templates)


@app.route("/management/email-templates/<template_key>/edit", methods=["POST"])
@owner_required
def edit_email_template(template_key):
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    if not subject or not body:
        flash("Subject and body are both required.", "error")
        return redirect(url_for("management_email_templates"))
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM email_templates WHERE template_key = ?", (template_key,)).fetchone()
    if not existing:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE email_templates SET subject = ?, body = ?, updated_at = ? WHERE template_key = ?",
        (subject, body, datetime.now(timezone.utc).isoformat(), template_key),
    )
    log_audit(conn, "email_template_edited", target=template_key)
    conn.commit()
    conn.close()
    flash("Template updated.", "success")
    return redirect(url_for("management_email_templates"))


@app.route("/management/company-info", methods=["GET", "POST"])
@owner_required
def management_company_info():
    conn = get_db()
    if request.method == "POST":
        fields = [
            "legal_name", "registration_number", "vat_number", "registered_address",
            "incorporation_date", "accountant_name", "accountant_phone", "accountant_email",
            "insurance_broker_name", "insurance_broker_phone", "insurance_broker_email",
        ]
        values = [request.form.get(f, "").strip() or None for f in fields]
        conn.execute(
            f"""INSERT INTO company_info (id, {', '.join(fields)}, updated_at)
                VALUES (1, {', '.join(['?'] * len(fields))}, ?)
                ON CONFLICT(id) DO UPDATE SET {', '.join(f'{f} = excluded.{f}' for f in fields)}, updated_at = excluded.updated_at""",
            (*values, datetime.now(timezone.utc).isoformat()),
        )
        log_audit(conn, "company_info_updated")
        conn.commit()
        conn.close()
        flash("Company info updated.", "success")
        return redirect(url_for("management_company_info"))
    info = conn.execute("SELECT * FROM company_info WHERE id = 1").fetchone()
    conn.close()
    return render_template("management_company_info.html", info=info)


@app.route("/management/bank-details")
@owner_required
def management_bank_details():
    conn = get_db()
    entries = conn.execute("SELECT * FROM bank_details ORDER BY label").fetchall() if vault_enabled() else None
    conn.close()
    return render_template("management_bank_details.html", entries=entries)


@app.route("/management/bank-details/new", methods=["POST"])
@owner_required
def new_bank_details():
    if not vault_enabled():
        abort(404)
    label = request.form.get("label", "").strip()
    bank_name = request.form.get("bank_name", "").strip()
    account_holder = request.form.get("account_holder", "").strip()
    currency = request.form.get("currency", "").strip()
    notes = request.form.get("notes", "").strip()
    account_number = request.form.get("account_number", "").strip()
    iban = request.form.get("iban", "").strip()
    swift_bic = request.form.get("swift_bic", "").strip()

    if not label:
        flash("A label is required (e.g. Main operating account).", "error")
        return redirect(url_for("management_bank_details"))

    now = datetime.now(timezone.utc).isoformat()
    sensitive = fernet_encrypt_json({"account_number": account_number, "iban": iban, "swift_bic": swift_bic})
    conn = get_db()
    conn.execute(
        """INSERT INTO bank_details (label, bank_name, account_holder, currency, notes,
           sensitive_encrypted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (label, bank_name or None, account_holder or None, currency or None, notes or None,
         sensitive, now, now),
    )
    log_audit(conn, "bank_details_created", target=label)
    conn.commit()
    conn.close()
    flash("Bank details saved.", "success")
    return redirect(url_for("management_bank_details"))


@app.route("/management/bank-details/<int:entry_id>/edit", methods=["POST"])
@owner_required
def edit_bank_details(entry_id):
    if not vault_enabled():
        abort(404)
    conn = get_db()
    entry = conn.execute("SELECT * FROM bank_details WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        abort(404)

    label = request.form.get("label", "").strip()
    bank_name = request.form.get("bank_name", "").strip()
    account_holder = request.form.get("account_holder", "").strip()
    currency = request.form.get("currency", "").strip()
    notes = request.form.get("notes", "").strip()
    account_number = request.form.get("account_number", "").strip()
    iban = request.form.get("iban", "").strip()
    swift_bic = request.form.get("swift_bic", "").strip()
    if not label:
        conn.close()
        flash("A label is required.", "error")
        return redirect(url_for("management_bank_details"))

    # Blank sensitive fields on the edit form means "leave unchanged" — same
    # convention as the Vault's edit form.
    if account_number or iban or swift_bic:
        sensitive = fernet_encrypt_json({"account_number": account_number, "iban": iban, "swift_bic": swift_bic})
    else:
        sensitive = entry["sensitive_encrypted"]

    conn.execute(
        """UPDATE bank_details SET label = ?, bank_name = ?, account_holder = ?, currency = ?,
           notes = ?, sensitive_encrypted = ?, updated_at = ? WHERE id = ?""",
        (label, bank_name or None, account_holder or None, currency or None, notes or None,
         sensitive, datetime.now(timezone.utc).isoformat(), entry_id),
    )
    log_audit(conn, "bank_details_edited", target=label)
    conn.commit()
    conn.close()
    flash("Bank details updated.", "success")
    return redirect(url_for("management_bank_details"))


@app.route("/management/bank-details/<int:entry_id>/delete", methods=["POST"])
@owner_required
def delete_bank_details(entry_id):
    conn = get_db()
    entry = conn.execute("SELECT label FROM bank_details WHERE id = ?", (entry_id,)).fetchone()
    conn.execute("DELETE FROM bank_details WHERE id = ?", (entry_id,))
    if entry:
        log_audit(conn, "bank_details_deleted", target=entry["label"])
    conn.commit()
    conn.close()
    flash("Bank details removed.", "success")
    return redirect(url_for("management_bank_details"))


@app.route("/management/bank-details/<int:entry_id>/reveal", methods=["POST"])
@owner_required
def reveal_bank_details(entry_id):
    if not vault_enabled():
        abort(404)
    conn = get_db()
    entry = conn.execute("SELECT * FROM bank_details WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        abort(404)
    log_audit(conn, "bank_details_revealed", target=entry["label"])
    conn.commit()
    conn.close()
    decrypted = fernet_decrypt_json(entry["sensitive_encrypted"]) or {}
    return {
        "account_number": decrypted.get("account_number") or "",
        "iban": decrypted.get("iban") or "",
        "swift_bic": decrypted.get("swift_bic") or "",
    }


@app.route("/management/vault")
@owner_required
def management_vault():
    if not vault_enabled():
        return render_template("management_vault.html", entries=None)
    conn = get_db()
    entries = conn.execute("SELECT * FROM vault_entries ORDER BY title").fetchall()
    conn.close()
    return render_template("management_vault.html", entries=entries)


@app.route("/management/vault/new", methods=["POST"])
@owner_required
def new_vault_entry():
    if not vault_enabled():
        abort(404)
    user = current_user()
    title = request.form.get("title", "").strip()
    username = request.form.get("username", "").strip()
    url_value = request.form.get("url", "").strip()
    password = request.form.get("password", "")
    notes = request.form.get("notes", "").strip()

    if not title:
        flash("A title is required.", "error")
        return redirect(url_for("management_vault"))

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO vault_entries (title, username, url, secret_encrypted, created_at, updated_at, updated_by_user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, username, url_value, vault_encrypt(password, notes), now, now, user["id"]),
    )
    log_audit(conn, "vault_entry_created", target=title)
    conn.commit()
    conn.close()
    flash(f'"{title}" added to the vault.', "success")
    return redirect(url_for("management_vault"))


@app.route("/management/vault/<int:entry_id>/edit", methods=["POST"])
@owner_required
def edit_vault_entry(entry_id):
    if not vault_enabled():
        abort(404)
    user = current_user()
    conn = get_db()
    entry = conn.execute("SELECT * FROM vault_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        abort(404)

    title = request.form.get("title", "").strip()
    username = request.form.get("username", "").strip()
    url_value = request.form.get("url", "").strip()
    password = request.form.get("password", "")
    notes = request.form.get("notes", "").strip()
    if not title:
        conn.close()
        flash("A title is required.", "error")
        return redirect(url_for("management_vault"))

    # Blank password/notes on the edit form means "leave unchanged" — decrypt
    # the existing secret and keep whichever fields were left blank.
    if not password and not notes:
        current = vault_decrypt(entry["secret_encrypted"])
        password = password or current.get("password") or ""
        notes = notes or current.get("notes") or ""

    conn.execute(
        """UPDATE vault_entries SET title = ?, username = ?, url = ?, secret_encrypted = ?,
           updated_at = ?, updated_by_user_id = ? WHERE id = ?""",
        (title, username, url_value, vault_encrypt(password, notes),
         datetime.now(timezone.utc).isoformat(), user["id"], entry_id),
    )
    log_audit(conn, "vault_entry_edited", target=title)
    conn.commit()
    conn.close()
    flash(f'"{title}" updated.', "success")
    return redirect(url_for("management_vault"))


@app.route("/management/vault/<int:entry_id>/delete", methods=["POST"])
@owner_required
def delete_vault_entry(entry_id):
    conn = get_db()
    entry = conn.execute("SELECT title FROM vault_entries WHERE id = ?", (entry_id,)).fetchone()
    conn.execute("DELETE FROM vault_entries WHERE id = ?", (entry_id,))
    if entry:
        log_audit(conn, "vault_entry_deleted", target=entry["title"])
    conn.commit()
    conn.close()
    flash("Vault entry removed.", "success")
    return redirect(url_for("management_vault"))


@app.route("/management/vault/<int:entry_id>/reveal", methods=["POST"])
@owner_required
def reveal_vault_entry(entry_id):
    if not vault_enabled():
        abort(404)
    conn = get_db()
    entry = conn.execute("SELECT * FROM vault_entries WHERE id = ?", (entry_id,)).fetchone()
    if not entry:
        conn.close()
        abort(404)
    log_audit(conn, "vault_entry_revealed", target=entry["title"])
    conn.commit()
    conn.close()
    decrypted = vault_decrypt(entry["secret_encrypted"])
    return {"password": decrypted.get("password") or "", "notes": decrypted.get("notes") or ""}


# ---------------------------------------------------------------------------
# Backup — the database now holds bookings and guest contact details, not
# just staff records, so this matters more than it used to. Uses SQLite's
# own backup API for a consistent snapshot rather than copying the raw file,
# which could in principle catch a write mid-flight.
# ---------------------------------------------------------------------------

@app.route("/admin/audit-log")
@owner_required
def audit_log():
    conn = get_db()
    entries = conn.execute(
        """SELECT audit_log.*, users.name AS actor_name FROM audit_log
           LEFT JOIN users ON users.id = audit_log.actor_user_id
           ORDER BY audit_log.created_at DESC LIMIT 200"""
    ).fetchall()
    conn.close()
    return render_template("audit_log.html", entries=entries)


@app.route("/admin/audit-log/export.csv")
@owner_required
def export_audit_log_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT audit_log.*, users.name AS actor_name FROM audit_log
           LEFT JOIN users ON users.id = audit_log.actor_user_id
           ORDER BY audit_log.created_at DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["created_at", "actor_name", "action", "target", "details"]
    return csv_response(fieldnames, rows, "audit_log.csv")


def readiness_checks(conn):
    """Everything that has to be true before this is somebody's real business
    system, and what is merely optional.

    Written as data rather than prose because the answer changes as env vars
    get set — a checklist in DEPLOY.md goes stale the moment something is
    configured, and can't tell you that the owner password is still the seeded
    one. Severity is 'blocker' (do not go live), 'warn' (works, but you will
    regret it) or 'info' (optional feature, off).
    """
    out = []

    def add(severity, area, label, ok, detail):
        out.append({"severity": severity, "area": area, "label": label,
                    "ok": ok, "detail": detail})

    secret_set = bool(os.environ.get("FLASK_SECRET_KEY"))
    add("blocker", "Core", "Session secret key", secret_set,
        "Set — logins survive a restart." if secret_set else
        "NOT set. A new key is generated every start, so everyone is logged out "
        "each time the app restarts, and 'remember me' never works.")

    add("blocker", "Core", "Debug mode off", not DEBUG_MODE,
        "Off." if not DEBUG_MODE else
        "ON. Anyone hitting an error gets an interactive Python console on your server.")

    add("warn", "Core", "Public web address", bool(PUBLIC_BASE_URL),
        f"{PUBLIC_BASE_URL}" if PUBLIC_BASE_URL else
        "Not set. Links in automated email (balance reminders, campaigns) will "
        "point at localhost and be useless to the recipient.")

    email_ok = email_enabled()
    held = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE sent_at IS NULL").fetchone()["c"]
    if email_ok:
        email_detail = ("Resend" if resend_enabled() else "SMTP") + " configured."
    else:
        email_detail = "Not configured. No booking confirmations, no reminders, no campaigns."
        if held:
            email_detail += (f" {held} message{'' if held == 1 else 's'} "
                             f"{'is' if held == 1 else 'are'} being held until this is set up.")
    add("blocker", "Email", "Outbound email", email_ok, email_detail)

    # Worth its own line: with a provider configured, anything still held is
    # mail that was actually rejected, which the email check above would
    # otherwise show as a clean pass.
    if held:
        add("warn" if email_ok else "info", "Email", "Held email", False,
            f"{held} message{'' if held == 1 else 's'} could not be sent and "
            f"{'is' if held == 1 else 'are'} waiting. Review them under Emails → Held email.")

    stripe_ok = stripe_enabled()
    add("warn", "Payments", "Stripe", stripe_ok,
        "Configured." if stripe_ok else
        "Not configured. Guests can still book; nobody can pay online.")
    if stripe_ok:
        add("blocker", "Payments", "Stripe webhook secret", bool(STRIPE_WEBHOOK_SECRET),
            "Set." if STRIPE_WEBHOOK_SECRET else
            "MISSING while Stripe is live. Payments will be taken and the booking "
            "never confirmed, because the confirmation arrives by webhook.")

    add("warn", "Security", "Vault encryption key", vault_enabled(),
        "Set." if vault_enabled() else
        "Not set. The Vault can't store anything, so codes and passwords have "
        "nowhere safe to live.")

    owner = conn.execute(
        "SELECT id, email, password_hash FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    default_pw = bool(owner) and check_password_hash(owner["password_hash"], "changeme")
    add("blocker", "Security", "Owner password changed", not default_pw,
        "Changed." if not default_pw else
        "STILL THE SEEDED PASSWORD. Anyone who has seen the setup notes can log "
        "in as you.")

    terms = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'terms_text'").fetchone()
    terms_draft = not terms or "DRAFT" in (terms["value"] or "")[:200]
    add("warn", "Legal", "Terms & conditions", not terms_draft,
        "Replaced." if not terms_draft else
        "Still the placeholder draft. Guests are agreeing to it at booking.")

    company = conn.execute(
        "SELECT registered_address FROM company_info WHERE id = 1").fetchone()
    has_address = bool(company and (company["registered_address"] or "").strip())
    add("warn", "Legal", "Registered address", has_address,
        "On file." if has_address else
        "Not set. Marketing email has to identify the sender by postal address; "
        "the footer currently omits it.")

    last_backup = conn.execute(
        "SELECT created_at FROM audit_log WHERE action = 'backup_downloaded' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    days = None
    if last_backup:
        d = parse_date((last_backup["created_at"] or "")[:10])
        days = (datetime.now(timezone.utc).date() - d).days if d else None
    add("warn", "Data", "Recent backup", days is not None and days <= 30,
        f"Last taken {days} day{'s' if days != 1 else ''} ago." if days is not None else
        "Never downloaded.")

    broken = conn.execute(
        """SELECT COUNT(*) AS c FROM time_entries
           WHERE clock_out_at IS NOT NULL AND clock_out_at < clock_in_at""").fetchone()["c"]
    add("warn", "Data", "Timesheets sane", broken == 0,
        "No impossible shifts." if not broken else
        f"{broken} shift{'s' if broken != 1 else ''} end before they start — "
        "payroll export is blocked until they're fixed.")

    for label, token, why in (
        ("Kiosk display token", OFFICE_DISPLAY_TOKEN,
         "the wall display has to be logged in by hand and drops out after 12 hours"),
        ("Calendar sync token", ICAL_SYNC_TOKEN, "iCal sync can't be triggered on a schedule"),
        ("Daily digest token", DIGEST_TOKEN, "the daily summary email can't be triggered"),
        ("Outlook add-in token", GUEST_LOOKUP_TOKEN, "the Outlook add-in can't look anything up"),
    ):
        add("info", "Optional", label, bool(token), "Set." if token else f"Not set — {why}.")

    add("info", "Optional", "Mailbox scanning (Microsoft Graph)", graph_enabled(),
        f"{len(MS_GRAPH_MAILBOXES)} mailbox(es)." if graph_enabled() else
        "Not connected — inbox flags and reply drafting are off.")
    add("info", "Optional", "Reply drafting (Claude)", claude_configured(),
        "Configured." if claude_configured() else "Not configured.")
    return out


@app.route("/admin/readiness")
@owner_required
def admin_readiness():
    conn = get_db()
    checks = readiness_checks(conn)
    conn.close()
    blockers = [c for c in checks if c["severity"] == "blocker" and not c["ok"]]
    warnings = [c for c in checks if c["severity"] == "warn" and not c["ok"]]
    by_area = {}
    for c in checks:
        by_area.setdefault(c["area"], []).append(c)
    overview = [
        overview_cell("Must fix", len(blockers), alert=len(blockers)),
        overview_cell("Worth fixing", len(warnings), alert=len(warnings)),
        overview_cell("Checks passing", sum(1 for c in checks if c["ok"]),
                      sub=f"/{len(checks)}"),
    ]
    return render_template("admin_readiness.html", by_area=by_area, overview=overview,
                           blockers=blockers, warnings=warnings)


@app.route("/admin/backup")
@owner_required
def download_backup():
    audit_conn = get_db()
    log_audit(audit_conn, "backup_downloaded")
    audit_conn.commit()
    audit_conn.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        tmp_db_path = os.path.join(BASE_DIR, f"_backup_tmp_{secrets.token_hex(6)}.db")
        try:
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(tmp_db_path)
            src.backup(dst)
            dst.close()
            src.close()
            zf.write(tmp_db_path, "gudanes_hr.db")
        finally:
            if os.path.exists(tmp_db_path):
                os.remove(tmp_db_path)

        for folder, arc_prefix in ((UPLOAD_DIR, "uploads"), (ROOM_PHOTO_DIR, "room_photos")):
            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, folder)
                    zf.write(full, os.path.join(arc_prefix, rel))

    buf.seek(0)
    filename = f"gudanes-backup-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.zip"
    return app.response_class(
        buf.getvalue(), mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Overview — the unified ops feed: bookings + staff tasks in one filterable,
# self-refreshing view. This is the owner's single "what's happening and
# what is everyone doing" screen; admin_calendar and admin_tasks remain for
# their focused day-to-day editing jobs.
# ---------------------------------------------------------------------------

@app.route("/calendar")
@login_required
def ops_calendar():
    """The main working calendar — day/week/month, every item clickable and
    filterable. Employees get it too (it's the "what's on" view), but the
    server only ever hands them rows build_overview already scopes."""
    view = request.args.get("view", "week")
    anchor = parse_date(request.args.get("date", "")) or datetime.now(timezone.utc).date()
    user = current_user()
    conn = get_db()
    cal = build_calendar(conn, view, anchor, viewer=user)
    conn.close()
    return render_template(
        "ops_calendar.html", cal=cal, today=datetime.now(timezone.utc).date(),
        this_month=datetime.now(timezone.utc).date().strftime("%Y-%m"),
    )


@app.route("/admin/overview")
@owner_required
def admin_overview():
    view = request.args.get("view", "week")
    anchor = parse_date(request.args.get("date", "")) or datetime.now(timezone.utc).date()
    conn = get_db()
    sheet = build_overview(conn, view, anchor)
    conn.close()
    return render_template("admin_overview.html", today=datetime.now(timezone.utc).date(), sheet=sheet)


@app.route("/admin/overview/status.json")
@owner_required
def admin_overview_status():
    """Polled every ~25s by the overview page so a task someone completes
    elsewhere in the app — or that the owner completes right here — shows
    up as done without a full reload or losing the active filters."""
    view = request.args.get("view", "week")
    anchor = parse_date(request.args.get("date", "")) or datetime.now(timezone.utc).date()
    conn = get_db()
    sheet = build_overview(conn, view, anchor)
    conn.close()
    return jsonify(tasks=[
        {"id": r["id"], "status": r["status"], "acknowledgment_status": r["acknowledgment_status"]}
        for r in sheet["rows"] if r["kind"] == "task"
    ])


# ---------------------------------------------------------------------------
# Tasks — a per-employee task sheet, day/week/month, that feeds the same
# calendar-grid pattern used for room bookings.
# ---------------------------------------------------------------------------

@app.route("/admin/tasks")
@owner_required
def admin_tasks():
    view = request.args.get("view", "week")
    anchor = parse_date(request.args.get("date", "")) or datetime.now(timezone.utc).date()
    conn = get_db()
    sheet = build_task_sheet(conn, view, anchor)
    conn.close()
    return render_template("admin_tasks.html", today=datetime.now(timezone.utc).date(), sheet=sheet)


@app.route("/admin/tasks/new", methods=["POST"])
@owner_required
def new_task():
    assigned_to = request.form.get("assigned_to_user_id", "")
    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date", "").strip()
    notes = request.form.get("notes", "").strip()
    room_note = request.form.get("room_note", "").strip()
    priority = request.form.get("priority", "normal")
    if priority not in ("low", "normal", "high"):
        priority = "normal"
    repeat_weekly = 1 if request.form.get("repeat_weekly") else 0
    origin = "guest_request" if request.form.get("guest_request") else "manual"

    if not assigned_to.isdigit() or not title:
        flash("Choose an employee and enter a task.", "error")
        return redirect(request.referrer or url_for("admin_tasks"))
    if repeat_weekly and not due_date:
        flash("A repeating task needs a due date to repeat from.", "error")
        return redirect(request.referrer or url_for("admin_tasks"))

    conn = get_db()
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, notes, room_note, priority, due_date, created_at, repeat_weekly, origin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(assigned_to), title, notes or None, room_note or None, priority, due_date or None,
         datetime.now(timezone.utc).isoformat(), repeat_weekly, origin),
    )
    conn.commit()
    conn.close()
    flash("Task added.", "success")
    return redirect(request.referrer or url_for("admin_tasks"))


@app.route("/tasks/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle_task(task_id):
    user = current_user()
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        return jsonify(error="not found"), 404
    if user["role"] != "owner" and task["assigned_to_user_id"] != user["id"]:
        conn.close()
        return jsonify(error="forbidden"), 403
    # Cycles forward through all three states on every call rather than a
    # binary flip — open -> in_progress -> done -> open. A plain "done if
    # open else open" would send an in_progress task backward to open
    # instead of forward to done, since it only ever checked for 'open'.
    new_status = {"open": "in_progress", "in_progress": "done", "done": "open"}[task["status"]]
    conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
        (new_status, datetime.now(timezone.utc).isoformat() if new_status == "done" else None, task_id),
    )
    # A directed task's accept/reject flow is a SEPARATE field from status
    # — without this, the assignee could cycle a still-'pending' directed
    # task straight to 'done' via the checkbox without ever resolving
    # accept/reject, leaving acknowledgment_status stuck at 'pending'
    # forever and the owner's accept/reject notification never firing.
    # Acting on the task via the checkbox is itself a clear signal they've
    # seen and taken it on, so treat that as an implicit accept.
    if task["acknowledgment_status"] == "pending" and task["assigned_to_user_id"] == user["id"]:
        conn.execute("UPDATE tasks SET acknowledgment_status = 'accepted' WHERE id = ?", (task_id,))
        if task["directed_by_user_id"]:
            send_notification(
                conn, task["directed_by_user_id"], "task_response",
                f"{user['name']} accepted: {task['title']}", link="/admin/tasks", related_task_id=task_id,
            )

    # Completing a repeating task queues up next week's occurrence — the
    # dedupe check keeps a rapid done/undone/done toggle from spawning
    # duplicates.
    if new_status == "done" and task["repeat_weekly"] and task["due_date"]:
        next_due = (parse_date(task["due_date"]) + timedelta(days=7)).isoformat()
        exists = conn.execute(
            """SELECT 1 FROM tasks WHERE assigned_to_user_id IS ? AND title = ? AND due_date = ?""",
            (task["assigned_to_user_id"], task["title"], next_due),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO tasks
                   (assigned_to_user_id, title, notes, room_note, priority, due_date, created_at, repeat_weekly, origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'recurring')""",
                (task["assigned_to_user_id"], task["title"], task["notes"], task["room_note"],
                 task["priority"], next_due, datetime.now(timezone.utc).isoformat()),
            )

    conn.commit()
    conn.close()
    return jsonify(status=new_status)


@app.route("/admin/tasks/<int:task_id>/direct", methods=["POST"])
@owner_required
def direct_task(task_id):
    """Pushes an existing task at an employee right now — an urgent
    directive, not just a due-date assignment. They see it highlighted on
    their dashboard and have to accept or reject it; you see which one
    happened. Optionally reassigns to a different employee in the same
    action, so this also covers 'give this specific task to someone else
    immediately' without a separate edit step."""
    owner = current_user()
    employee_id = request.form.get("employee_id", "").strip()
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        abort(404)
    target_id = int(employee_id) if employee_id.isdigit() else task["assigned_to_user_id"]
    if not target_id:
        conn.close()
        flash("Choose who this goes to.", "error")
        return redirect(url_for("admin_tasks"))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE tasks SET assigned_to_user_id = ?, acknowledgment_status = 'pending',
           directed_at = ?, directed_by_user_id = ? WHERE id = ?""",
        (target_id, now, owner["id"], task_id),
    )
    conn.commit()
    send_notification(
        conn, target_id, "task_directive",
        f"New task from {owner['name']}: {task['title']}",
        body=task["room_note"] or task["notes"] or None,
        link="/",
        related_task_id=task_id,
    )
    conn.close()
    flash("Sent — they'll need to accept or reject it.", "success")
    return redirect(url_for("admin_tasks"))


@app.route("/tasks/<int:task_id>/accept", methods=["POST"])
@login_required
def accept_task(task_id):
    user = current_user()
    conn = get_db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND assigned_to_user_id = ? AND acknowledgment_status = 'pending'",
        (task_id, user["id"]),
    ).fetchone()
    if not task:
        conn.close()
        return jsonify(error="not found"), 404
    cur = conn.execute(
        "UPDATE tasks SET acknowledgment_status = 'accepted' WHERE id = ? AND acknowledgment_status = 'pending'",
        (task_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="not found"), 404
    if task["directed_by_user_id"]:
        send_notification(
            conn, task["directed_by_user_id"], "task_response",
            f"{user['name']} accepted: {task['title']}", link="/admin/tasks", related_task_id=task_id,
        )
    conn.close()
    return jsonify(ok=True)


@app.route("/tasks/<int:task_id>/reject", methods=["POST"])
@login_required
def reject_task(task_id):
    user = current_user()
    conn = get_db()
    task = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND assigned_to_user_id = ? AND acknowledgment_status = 'pending'",
        (task_id, user["id"]),
    ).fetchone()
    if not task:
        conn.close()
        return jsonify(error="not found"), 404
    cur = conn.execute(
        "UPDATE tasks SET acknowledgment_status = 'rejected' WHERE id = ? AND acknowledgment_status = 'pending'",
        (task_id,),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return jsonify(error="not found"), 404
    if task["directed_by_user_id"]:
        send_notification(
            conn, task["directed_by_user_id"], "task_response",
            f"{user['name']} rejected: {task['title']} — needs reassigning", link="/admin/tasks",
            related_task_id=task_id,
        )
    conn.close()
    return jsonify(ok=True)


@app.route("/breaks/start", methods=["POST"])
@login_required
def start_break():
    user = current_user()
    conn = get_db()
    entry = open_shift(conn, user["id"])
    if not entry:
        conn.close()
        return jsonify(error="not clocked in"), 400
    already_open = conn.execute(
        "SELECT 1 FROM breaks WHERE time_entry_id = ? AND end_at IS NULL", (entry["id"],)
    ).fetchone()
    if already_open:
        conn.close()
        return jsonify(error="already on break"), 400
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("INSERT INTO breaks (time_entry_id, start_at) VALUES (?, ?)", (entry["id"], now))
        conn.commit()
    except sqlite3.IntegrityError:
        # Second "Start break" tap beat this one to it — the partial unique
        # index is the real guard, `already_open` above is the fast path.
        conn.rollback()
        conn.close()
        return jsonify(error="already on break"), 400
    conn.close()
    return jsonify(on_break=True, started_at=local_time_str(now))


@app.route("/breaks/end", methods=["POST"])
@login_required
def end_break():
    user = current_user()
    conn = get_db()
    entry = open_shift(conn, user["id"])
    if not entry:
        conn.close()
        return jsonify(error="not clocked in"), 400
    conn.execute(
        "UPDATE breaks SET end_at = ? WHERE time_entry_id = ? AND end_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), entry["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify(on_break=False)


@app.route("/admin/tasks/<int:task_id>/reschedule", methods=["POST"])
@owner_required
def reschedule_task(task_id):
    new_date = (request.get_json(silent=True) or request.form).get("due_date", "").strip()
    if not parse_date(new_date):
        return jsonify(error="invalid date"), 400
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        return jsonify(error="not found"), 404
    conn.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (new_date, task_id))
    conn.commit()
    conn.close()
    return jsonify(ok=True, due_date=new_date)


@app.route("/admin/tasks/<int:task_id>/delete", methods=["POST"])
@owner_required
def delete_task(task_id):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    flash("Task removed.", "success")
    return redirect(request.referrer or url_for("admin_tasks"))


@app.route("/admin/tasks/<int:task_id>/duplicate-next-week", methods=["POST"])
@owner_required
def duplicate_task_next_week(task_id):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        abort(404)
    new_due = None
    if task["due_date"]:
        d = parse_date(task["due_date"])
        if d:
            new_due = (d + timedelta(days=7)).isoformat()
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, notes, room_note, priority, due_date, created_at, origin)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (task["assigned_to_user_id"], task["title"], task["notes"], task["room_note"], task["priority"],
         new_due, datetime.now(timezone.utc).isoformat(), task["origin"]),
    )
    conn.commit()
    conn.close()
    flash("Task duplicated to next week.", "success")
    return redirect(request.referrer or url_for("admin_tasks"))


# ---------------------------------------------------------------------------
# Shift scheduling (rota) — separate from tasks: this is who's *supposed* to
# be working when, planned ahead, as opposed to tasks (what needs doing) or
# the timesheet (who actually clocked in). One employee row per week, one
# column per day — the standard rota shape — reusing the .cal-table grid
# already used for the booking calendar.
# ---------------------------------------------------------------------------

def shift_attendance(clock_ins_by_local_date, shift_date_iso, start_time, today):
    """Compares a shift against actual clock-ins (already bucketed by local
    calendar date — see build_shift_week). Only judges the past: a shift
    today or later is 'upcoming' rather than guessed at before it's over."""
    shift_date = parse_date(shift_date_iso)
    earliest = clock_ins_by_local_date.get(shift_date_iso)
    if earliest is None:
        return {"status": "upcoming"} if shift_date >= today else {"status": "no_show"}
    if not start_time:
        return {"status": "on_time", "clock_in": earliest}
    try:
        scheduled = datetime.strptime(start_time, "%H:%M")
        actual = datetime.strptime(earliest, "%H:%M")
    except ValueError:
        return {"status": "on_time", "clock_in": earliest}
    late_minutes = int((actual - scheduled).total_seconds() // 60)
    if late_minutes > 10:
        return {"status": "late", "clock_in": earliest, "minutes": late_minutes}
    return {"status": "on_time", "clock_in": earliest}


def build_shift_week(conn, anchor):
    range_start = anchor - timedelta(days=anchor.weekday())
    days = [range_start + timedelta(days=i) for i in range(7)]
    range_end = range_start + timedelta(days=7)
    today = datetime.now(timezone.utc).date()

    employees = conn.execute("SELECT * FROM users WHERE role = 'employee' ORDER BY name").fetchall()
    shifts = conn.execute(
        "SELECT * FROM shifts WHERE shift_date >= ? AND shift_date < ? ORDER BY shift_date, start_time",
        (range_start.isoformat(), range_end.isoformat()),
    ).fetchall()

    # Earliest clock-in per (user_id, local calendar date) for the week, so
    # attendance can be judged against the château's own clock, not UTC.
    range_start_utc = datetime.combine(range_start, datetime.min.time(), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    range_end_utc = datetime.combine(range_end, datetime.min.time(), tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    clock_ins = {}
    for row in conn.execute(
        "SELECT user_id, clock_in_at FROM time_entries WHERE clock_in_at >= ? AND clock_in_at < ?",
        (range_start_utc.isoformat(), range_end_utc.isoformat()),
    ).fetchall():
        local_dt = parse_datetime_iso(row["clock_in_at"]).astimezone(LOCAL_TZ)
        key = (row["user_id"], local_dt.date().isoformat())
        existing = clock_ins.get(key)
        stamp = local_dt.strftime("%H:%M")
        if existing is None or stamp < existing:
            clock_ins[key] = stamp

    by_cell = {}
    for s in shifts:
        attendance = shift_attendance(
            {d: t for (uid, d), t in clock_ins.items() if uid == s["user_id"]},
            s["shift_date"], s["start_time"], today,
        )
        by_cell.setdefault((s["user_id"], s["shift_date"]), []).append({"shift": s, "attendance": attendance})

    rows = [
        {"employee": e, "cells": [{"date": d, "shifts": by_cell.get((e["id"], d.isoformat()), [])} for d in days]}
        for e in employees
    ]

    return {
        "days": days, "rows": rows, "range_start": range_start, "range_end": range_end - timedelta(days=1),
        "prev_anchor": (range_start - timedelta(days=7)).isoformat(),
        "next_anchor": (range_start + timedelta(days=7)).isoformat(),
        "employees": employees,
    }


@app.route("/admin/shifts")
@owner_required
def admin_shifts():
    anchor = parse_date(request.args.get("date", "")) or datetime.now(timezone.utc).date()
    today = datetime.now(timezone.utc).date()
    conn = get_db()
    week = build_shift_week(conn, anchor)
    pending_swaps = conn.execute(
        """SELECT shift_swaps.*, shifts.shift_date, shifts.start_time, shifts.end_time,
               req.name AS requested_by_name, off.name AS offered_to_name
           FROM shift_swaps JOIN shifts ON shifts.id = shift_swaps.shift_id
           JOIN users AS req ON req.id = shift_swaps.requested_by_user_id
           JOIN users AS off ON off.id = shift_swaps.offered_to_user_id
           WHERE shift_swaps.status = 'accepted' ORDER BY shift_swaps.responded_at"""
    ).fetchall()
    staffing = roster_vs_occupancy(conn, week["days"])
    outliers = timesheet_outliers(conn, today)
    conn.close()
    return render_template(
        "admin_shifts.html", today=today, week=week, pending_swaps=pending_swaps,
        staffing=staffing, outliers=outliers,
    )


@app.route("/admin/shifts/copy-previous", methods=["POST"])
@owner_required
def copy_previous_week_shifts():
    anchor = parse_date(request.form.get("date", "")) or datetime.now(timezone.utc).date()
    this_week_start = anchor - timedelta(days=anchor.weekday())
    prev_week_start = this_week_start - timedelta(days=7)
    prev_week_end = this_week_start

    conn = get_db()
    prev_shifts = conn.execute(
        "SELECT * FROM shifts WHERE shift_date >= ? AND shift_date < ?",
        (prev_week_start.isoformat(), prev_week_end.isoformat()),
    ).fetchall()

    copied = 0
    for s in prev_shifts:
        new_date = (parse_date(s["shift_date"]) + timedelta(days=7)).isoformat()
        exists = conn.execute(
            "SELECT 1 FROM shifts WHERE user_id = ? AND shift_date = ? AND start_time IS ? AND end_time IS ?",
            (s["user_id"], new_date, s["start_time"], s["end_time"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (s["user_id"], new_date, s["start_time"], s["end_time"], s["role_note"],
             datetime.now(timezone.utc).isoformat()),
        )
        copied += 1
    conn.commit()
    conn.close()

    if not prev_shifts:
        flash("No shifts in the previous week to copy.", "error")
    else:
        flash(f"Copied {copied} shift{'s' if copied != 1 else ''} from last week"
              f"{' (' + str(len(prev_shifts) - copied) + ' already existed)' if copied < len(prev_shifts) else ''}.", "success")
    return redirect(url_for("admin_shifts", date=anchor.isoformat()))


@app.route("/shifts/mine", methods=["GET", "POST"])
@login_required
def my_shifts():
    user = current_user()
    conn = get_db()
    today = datetime.now(timezone.utc).date()

    if request.method == "POST":
        shift_id = request.form.get("shift_id", "")
        offered_to = request.form.get("offered_to_user_id", "")
        note = request.form.get("note", "").strip()
        shift = conn.execute(
            "SELECT * FROM shifts WHERE id = ? AND user_id = ? AND shift_date >= ?",
            (shift_id, user["id"], today.isoformat()),
        ).fetchone()
        if not shift or not offered_to.isdigit() or int(offered_to) == user["id"]:
            flash("Choose one of your upcoming shifts and a different colleague to offer it to.", "error")
        else:
            conn.execute(
                """INSERT INTO shift_swaps
                   (shift_id, requested_by_user_id, offered_to_user_id, status, note, requested_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (shift_id, user["id"], int(offered_to), note or None, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            send_notification(
                conn, int(offered_to), "shift_swap_offer",
                f"{user['name']} wants to swap a shift with you",
                body=f"{shift['shift_date']} {shift['start_time'] or ''}-{shift['end_time'] or ''}".strip(),
                link="/shifts/mine",
            )
            flash("Swap offered — waiting on your colleague to respond.", "success")
        conn.close()
        return redirect(url_for("my_shifts"))

    upcoming_shifts = conn.execute(
        "SELECT * FROM shifts WHERE user_id = ? AND shift_date >= ? ORDER BY shift_date, start_time",
        (user["id"], today.isoformat()),
    ).fetchall()
    colleagues = conn.execute(
        "SELECT id, name FROM users WHERE role = 'employee' AND status = 'active' AND id != ? ORDER BY name",
        (user["id"],),
    ).fetchall()
    my_swap_requests = conn.execute(
        """SELECT shift_swaps.*, shifts.shift_date, shifts.start_time, shifts.end_time,
               users.name AS offered_to_name
           FROM shift_swaps JOIN shifts ON shifts.id = shift_swaps.shift_id
           JOIN users ON users.id = shift_swaps.offered_to_user_id
           WHERE shift_swaps.requested_by_user_id = ? ORDER BY shift_swaps.requested_at DESC""",
        (user["id"],),
    ).fetchall()
    offered_to_me = conn.execute(
        """SELECT shift_swaps.*, shifts.shift_date, shifts.start_time, shifts.end_time,
               users.name AS requested_by_name
           FROM shift_swaps JOIN shifts ON shifts.id = shift_swaps.shift_id
           JOIN users ON users.id = shift_swaps.requested_by_user_id
           WHERE shift_swaps.offered_to_user_id = ? AND shift_swaps.status = 'pending'
           ORDER BY shift_swaps.requested_at""",
        (user["id"],),
    ).fetchall()
    conn.close()
    return render_template(
        "my_shifts.html", upcoming_shifts=upcoming_shifts, colleagues=colleagues,
        my_swap_requests=my_swap_requests, offered_to_me=offered_to_me,
    )


@app.route("/shifts/swaps/<int:swap_id>/respond", methods=["POST"])
@login_required
def respond_shift_swap(swap_id):
    user = current_user()
    status = request.form.get("status", "")
    if status not in ("accepted", "declined"):
        abort(400)
    conn = get_db()
    swap = conn.execute(
        "SELECT * FROM shift_swaps WHERE id = ? AND offered_to_user_id = ? AND status = 'pending'",
        (swap_id, user["id"]),
    ).fetchone()
    if not swap:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE shift_swaps SET status = ?, responded_at = ? WHERE id = ?",
        (status, datetime.now(timezone.utc).isoformat(), swap_id),
    )
    conn.commit()
    shift = conn.execute("SELECT shift_date FROM shifts WHERE id = ?", (swap["shift_id"],)).fetchone()
    shift_date = shift["shift_date"] if shift else "your shift"
    send_notification(
        conn, swap["requested_by_user_id"], "shift_swap_response",
        f"{user['name']} {status} your shift swap request",
        body=shift_date, link="/shifts/mine",
    )
    if status == "accepted":
        owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        if owner_row:
            send_notification(
                conn, owner_row["id"], "shift_swap_needs_approval",
                f"Shift swap needs your approval — {shift_date}",
                body=f"{user['name']} accepted covering this shift.", link="/admin/shifts",
            )
    conn.close()
    flash(f"Swap {status}." + (" The owner still needs to approve it." if status == "accepted" else ""), "success")
    return redirect(url_for("my_shifts"))


@app.route("/admin/shifts/swaps/<int:swap_id>/decide", methods=["POST"])
@owner_required
def decide_shift_swap(swap_id):
    status = request.form.get("status", "")
    if status not in ("approved", "rejected"):
        abort(400)
    conn = get_db()
    swap = conn.execute(
        "SELECT * FROM shift_swaps WHERE id = ? AND status = 'accepted'", (swap_id,)
    ).fetchone()
    if not swap:
        conn.close()
        abort(404)
    # Gate on the shift_swaps row first — only the request that actually
    # wins this atomic transition gets to reassign the shift or send mail,
    # closing the window where two racing decisions both saw 'accepted'.
    cur = conn.execute(
        "UPDATE shift_swaps SET status = ?, decided_at = ? WHERE id = ? AND status = 'accepted'",
        (status, datetime.now(timezone.utc).isoformat(), swap_id),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)
    if status == "approved":
        conn.execute(
            "UPDATE shifts SET user_id = ? WHERE id = ?",
            (swap["offered_to_user_id"], swap["shift_id"]),
        )
    conn.commit()
    shift = conn.execute("SELECT * FROM shifts WHERE id = ?", (swap["shift_id"],)).fetchone()
    requester = conn.execute("SELECT * FROM users WHERE id = ?", (swap["requested_by_user_id"],)).fetchone()
    covering = conn.execute("SELECT * FROM users WHERE id = ?", (swap["offered_to_user_id"],)).fetchone()
    conn.close()
    shift_date = shift["shift_date"] if shift else "your shift"
    conn = get_db()
    for person in (requester, covering):
        if person:
            send_email(
                person["email"],
                f"Shift swap {status} — {shift_date}",
                f"Hi {person['name'].split(' ')[0]},\n\n"
                f"The shift swap for {shift_date} between {requester['name'] if requester else '?'} and "
                f"{covering['name'] if covering else '?'} has been {status}.\n\n"
                f"— Château de Gudanes",
            )
            send_notification(
                conn, person["id"], "shift_swap_decided", f"Shift swap {status} — {shift_date}",
                link="/shifts/mine",
            )
    conn.close()
    flash(f"Swap {status}.", "success")
    return redirect(url_for("admin_shifts"))


@app.route("/admin/shifts/export.csv")
@owner_required
def export_shifts_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT shifts.*, users.name AS employee_name FROM shifts
           JOIN users ON users.id = shifts.user_id ORDER BY shift_date, start_time"""
    ).fetchall()
    conn.close()
    fieldnames = ["employee_name", "shift_date", "start_time", "end_time", "role_note"]
    return csv_response(fieldnames, rows, "shifts.csv")


@app.route("/admin/shifts/new", methods=["POST"])
@owner_required
def new_shift():
    user_ids = [uid for uid in request.form.getlist("user_ids") if uid.isdigit()]
    shift_date = request.form.get("shift_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    role_note = request.form.get("role_note", "").strip()

    if not user_ids or not parse_date(shift_date):
        flash("Choose at least one employee and a valid date.", "error")
        return redirect(request.referrer or url_for("admin_shifts"))

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    for uid in user_ids:
        conn.execute(
            "INSERT INTO shifts (user_id, shift_date, start_time, end_time, role_note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(uid), shift_date, start_time or None, end_time or None, role_note or None, now),
        )
    conn.commit()
    conn.close()
    flash(f"Shift added for {len(user_ids)} employee{'s' if len(user_ids) != 1 else ''}.", "success")
    return redirect(request.referrer or url_for("admin_shifts"))


@app.route("/admin/shifts/<int:shift_id>/delete", methods=["POST"])
@owner_required
def delete_shift(shift_id):
    conn = get_db()
    conn.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
    conn.commit()
    conn.close()
    flash("Shift removed.", "success")
    return redirect(request.referrer or url_for("admin_shifts"))


# ---------------------------------------------------------------------------
# Leave / time-off requests — employees request, owner approves/declines.
# Same shape as the expense approve/reject workflow above, deliberately, so
# it behaves the way this app already behaves everywhere else.
# ---------------------------------------------------------------------------

@app.route("/leave", methods=["GET", "POST"])
@login_required
def my_leave():
    user = current_user()
    conn = get_db()
    if request.method == "POST":
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        reason = request.form.get("reason", "").strip()
        leave_type = request.form.get("leave_type", "vacation")
        if leave_type not in ("vacation", "sick", "personal", "other"):
            leave_type = "vacation"
        start, end = parse_date(start_date), parse_date(end_date)
        if not start or not end or end < start:
            flash("Enter a valid date range.", "error")
        else:
            conn.execute(
                """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type, status, requested_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (user["id"], start.isoformat(), end.isoformat(), reason or None, leave_type,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            flash("Time off requested.", "success")
        conn.close()
        return redirect(url_for("my_leave"))

    requests_ = conn.execute(
        "SELECT * FROM leave_requests WHERE user_id = ? ORDER BY start_date DESC", (user["id"],)
    ).fetchall()
    leave = leave_balance(conn, user["id"], user["annual_leave_days"])
    conn.close()
    return render_template("my_leave.html", requests=requests_, leave=leave)


@app.route("/leave/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_leave_request(request_id):
    user = current_user()
    conn = get_db()
    req = conn.execute(
        "SELECT * FROM leave_requests WHERE id = ? AND user_id = ? AND status IN ('pending','approved')",
        (request_id, user["id"]),
    ).fetchone()
    if not req:
        conn.close()
        abort(404)
    conn.execute(
        "UPDATE leave_requests SET status = 'cancelled', decided_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), request_id),
    )
    conn.commit()
    conn.close()
    flash("Time off request cancelled.", "success")
    return redirect(url_for("my_leave"))


@app.route("/admin/approvals")
@owner_required
def admin_approvals():
    conn = get_db()
    period = period_from_request()
    overview = financial_overview(conn, period, datetime.now(timezone.utc).date())
    leave = conn.execute(
        """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id
           WHERE leave_requests.status = 'pending'"""
    ).fetchall()
    expenses = conn.execute(
        """SELECT expenses.*, users.name AS submitter_name FROM expenses
           LEFT JOIN users ON users.id = expenses.submitted_by_user_id
           WHERE expenses.status = 'pending'"""
    ).fetchall()
    # Timesheet corrections are a third thing an employee submits and waits on
    # a decision for, but they lived in their own page, so "Approvals" wasn't
    # actually the list of everything awaiting the owner.
    corrections = conn.execute(
        """SELECT timesheet_corrections.*, users.name AS employee_name,
                  time_entries.clock_in_at, time_entries.clock_out_at
           FROM timesheet_corrections
           JOIN users ON users.id = timesheet_corrections.user_id
           LEFT JOIN time_entries ON time_entries.id = timesheet_corrections.time_entry_id
           WHERE timesheet_corrections.status = 'pending'"""
    ).fetchall()
    conn.close()
    queue = (
        [{"kind": "leave", "sort_at": r["requested_at"], "row": r} for r in leave]
        + [{"kind": "expense", "sort_at": r["submitted_at"], "row": r} for r in expenses]
        + [{"kind": "correction", "sort_at": r["created_at"], "row": r} for r in corrections]
    )
    queue.sort(key=lambda item: item["sort_at"] or "")
    return render_template("admin_approvals.html", queue=queue,
                           overview=overview, period=period)


@app.route("/admin/approvals/bulk", methods=["POST"])
@owner_required
def bulk_approve_queue():
    items = request.form.getlist("items")
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    leave_count, expense_count = 0, 0
    to_notify = []
    for item in items:
        kind, _, raw_id = item.partition(":")
        if not raw_id.isdigit():
            continue
        item_id = int(raw_id)
        if kind == "leave":
            req = conn.execute(
                "SELECT * FROM leave_requests WHERE id = ? AND status = 'pending'", (item_id,)
            ).fetchone()
            if not req:
                continue
            conn.execute(
                "UPDATE leave_requests SET status = 'approved', decided_at = ? WHERE id = ?",
                (now, item_id),
            )
            leave_count += 1
            employee = conn.execute("SELECT * FROM users WHERE id = ?", (req["user_id"],)).fetchone()
            if employee:
                to_notify.append((employee["email"], employee["name"], req["start_date"], req["end_date"]))
        elif kind == "expense":
            row = conn.execute(
                "SELECT id FROM expenses WHERE id = ? AND status = 'pending'", (item_id,)
            ).fetchone()
            if not row:
                continue
            conn.execute(
                "UPDATE expenses SET status = 'approved', decided_at = ? WHERE id = ?",
                (now, item_id),
            )
            expense_count += 1
    conn.commit()
    conn.close()
    for email, name, start_date, end_date in to_notify:
        send_email(
            email, "Your time off request has been approved",
            f"Hi {name.split(' ')[0]},\n\n"
            f"Your time off request for {start_date} to {end_date} has been approved.\n\n"
            f"— Château de Gudanes",
        )
    flash(f"Approved {leave_count} time off request(s) and {expense_count} expense(s).", "success")
    return redirect(url_for("admin_approvals"))


@app.route("/admin/leave")
@owner_required
def admin_leave():
    conn = get_db()
    requests_ = conn.execute(
        """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id
           ORDER BY (leave_requests.status = 'pending') DESC, start_date DESC"""
    ).fetchall()
    # Shifts and open tasks already scheduled during a requested range —
    # surfaced so the owner sees the clash before approving, not after.
    conflicts = {}
    task_conflicts = {}
    for r in requests_:
        clashing = conn.execute(
            "SELECT * FROM shifts WHERE user_id = ? AND shift_date >= ? AND shift_date <= ? ORDER BY shift_date",
            (r["user_id"], r["start_date"], r["end_date"]),
        ).fetchall()
        if clashing:
            conflicts[r["id"]] = clashing
        clashing_tasks = conn.execute(
            """SELECT * FROM tasks WHERE assigned_to_user_id = ? AND status != 'done'
               AND due_date >= ? AND due_date <= ? ORDER BY due_date""",
            (r["user_id"], r["start_date"], r["end_date"]),
        ).fetchall()
        if clashing_tasks:
            task_conflicts[r["id"]] = clashing_tasks
    balances = {
        u["id"]: leave_balance(conn, u["id"], u["annual_leave_days"])
        for u in conn.execute("SELECT id, annual_leave_days FROM users WHERE role = 'employee'").fetchall()
    }
    conn.close()
    return render_template(
        "admin_leave.html", requests=requests_, conflicts=conflicts, task_conflicts=task_conflicts,
        balances=balances,
    )


@app.route("/admin/leave/export.csv")
@owner_required
def export_leave_csv():
    conn = get_db()
    rows = conn.execute(
        """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id
           ORDER BY (leave_requests.status = 'pending') DESC, start_date DESC"""
    ).fetchall()
    conn.close()
    fieldnames = ["employee_name", "leave_type", "start_date", "end_date", "status", "reason", "requested_at"]
    return csv_response(fieldnames, rows, "leave_requests.csv")


@app.route("/admin/leave/<int:request_id>/decide", methods=["POST"])
@owner_required
def decide_leave(request_id):
    status = request.form.get("status", "")
    if status not in ("approved", "declined"):
        abort(400)
    conn = get_db()
    req = conn.execute("SELECT * FROM leave_requests WHERE id = ?", (request_id,)).fetchone()
    if not req:
        conn.close()
        abort(404)
    cur = conn.execute(
        "UPDATE leave_requests SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
        (status, datetime.now(timezone.utc).isoformat(), request_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        flash("Already decided.", "success")
        return redirect(url_for("admin_leave"))
    employee = conn.execute("SELECT * FROM users WHERE id = ?", (req["user_id"],)).fetchone()
    if employee:
        send_email(
            employee["email"],
            f"Your time off request has been {status}",
            f"Hi {employee['name'].split(' ')[0]},\n\n"
            f"Your time off request for {req['start_date']} to {req['end_date']} has been {status}.\n\n"
            f"— Château de Gudanes",
        )
        send_notification(
            conn, employee["id"], "leave_decided", f"Your time off request has been {status}",
            body=f"{req['start_date']} to {req['end_date']}", link="/leave",
        )
    conn.close()
    flash(f"Request {status}.", "success")
    return redirect(url_for("admin_leave"))


# ---------------------------------------------------------------------------
# Printable daily ops sheet — today's tasks, shifts, who's off, who's here,
# in one page meant to be printed and posted, not clicked through.
# ---------------------------------------------------------------------------

@app.route("/admin/today-sheet")
@owner_required
def today_sheet():
    today = datetime.now(timezone.utc).date()
    conn = get_db()
    open_tasks = conn.execute(
        """SELECT tasks.*, users.name AS employee_name FROM tasks
           LEFT JOIN users ON users.id = tasks.assigned_to_user_id
           WHERE tasks.status != 'done' AND (due_date IS NULL OR due_date <= ?)
           ORDER BY (due_date IS NULL), due_date, users.name""",
        (today.isoformat(),),
    ).fetchall()
    todays_shifts = conn.execute(
        """SELECT shifts.*, users.name AS employee_name FROM shifts
           JOIN users ON users.id = shifts.user_id
           WHERE shift_date = ? ORDER BY start_time""",
        (today.isoformat(),),
    ).fetchall()
    off_today = conn.execute(
        """SELECT leave_requests.*, users.name AS employee_name FROM leave_requests
           JOIN users ON users.id = leave_requests.user_id
           WHERE leave_requests.status = 'approved'
             AND leave_requests.start_date <= ? AND leave_requests.end_date >= ?
           ORDER BY users.name""",
        (today.isoformat(), today.isoformat()),
    ).fetchall()
    guests_here = guests_in_residence(conn, today)
    breakfast_items = conn.execute(
        "SELECT * FROM breakfast_items ORDER BY COALESCE(category, 'zzz'), name"
    ).fetchall()
    breakfast_checked_today = {
        row["item_id"] for row in conn.execute(
            "SELECT item_id FROM breakfast_checklist_log WHERE checklist_date = ?", (today.isoformat(),)
        ).fetchall()
    }
    vehicles = conn.execute("SELECT * FROM vehicles ORDER BY name").fetchall()
    vehicle_usage_by_id = {
        row["vehicle_id"]: row["user_name"] for row in conn.execute(
            """SELECT vehicle_usage.vehicle_id, users.name AS user_name FROM vehicle_usage
               LEFT JOIN users ON users.id = vehicle_usage.user_id
               WHERE checked_in_at IS NULL"""
        ).fetchall()
    }
    tonights_dinners = conn.execute(
        "SELECT * FROM restaurant_bookings WHERE status = 'confirmed' AND dinner_date = ? ORDER BY guest_name",
        (today.isoformat(),),
    ).fetchall()
    dinner_covers = sum(d["party_size"] for d in tonights_dinners)
    restaurant_settings = get_restaurant_settings(conn)
    tonights_restaurant_staff = conn.execute(
        """SELECT restaurant_shifts.*, users.name AS employee_name FROM restaurant_shifts
           JOIN users ON users.id = restaurant_shifts.user_id
           WHERE dinner_date = ? ORDER BY users.name""",
        (today.isoformat(),),
    ).fetchall()
    todays_workshop_sessions = conn.execute(
        """SELECT workshop_sessions.*, workshops.title, workshops.instructor_name,
                  COALESCE((SELECT SUM(party_size) FROM workshop_bookings
                            WHERE session_id = workshop_sessions.id AND status = 'confirmed'), 0) AS covers
           FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_sessions.start_date <= ? AND workshop_sessions.end_date >= ?
           ORDER BY workshop_sessions.start_date""",
        (today.isoformat(), today.isoformat()),
    ).fetchall()
    conn.close()
    return render_template(
        "today_sheet.html", today=today, open_tasks=open_tasks, todays_shifts=todays_shifts,
        off_today=off_today, guests_here=guests_here, breakfast_items=breakfast_items,
        breakfast_checked_today=breakfast_checked_today, vehicles=vehicles,
        vehicle_usage_by_id=vehicle_usage_by_id, tonights_dinners=tonights_dinners,
        dinner_covers=dinner_covers, restaurant_settings=restaurant_settings,
        tonights_restaurant_staff=tonights_restaurant_staff,
        todays_workshop_sessions=todays_workshop_sessions,
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Microsoft Graph — read-only inbox monitoring for the "Inbox Flags" admin
# page. Uses the OAuth2 client-credentials flow (an app-only token, no
# signed-in user) over plain urllib, matching how send_email_via_resend
# calls out to Resend elsewhere in this file — no extra dependency. Every
# call is best-effort: a Graph error degrades to "scan skipped this tick",
# never breaks the automation loop or any guest-facing flow.
# ---------------------------------------------------------------------------

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_graph_token_cache = {"token": None, "expires_at": 0.0}


def graph_enabled():
    return bool(MS_GRAPH_TENANT_ID and MS_GRAPH_CLIENT_ID and MS_GRAPH_CLIENT_SECRET and MS_GRAPH_MAILBOXES)


def get_graph_token():
    """App-only access token for Microsoft Graph, cached in memory until
    shortly before it expires (normally valid ~1 hour). Returns None on any
    failure — callers treat that as 'scan skipped this tick', not an error
    worth surfacing anywhere a guest or normal admin page would see it."""
    if not graph_enabled():
        return None
    now = time.time()
    if _graph_token_cache["token"] and now < _graph_token_cache["expires_at"]:
        return _graph_token_cache["token"]
    try:
        data = urlencode({
            "client_id": MS_GRAPH_CLIENT_ID,
            "client_secret": MS_GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode("utf-8")
        req = Request(
            f"https://login.microsoftonline.com/{MS_GRAPH_TENANT_ID}/oauth2/v2.0/token",
            data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        token = payload.get("access_token")
        if not token:
            return None
        _graph_token_cache["token"] = token
        _graph_token_cache["expires_at"] = now + max(60, int(payload.get("expires_in", 3600)) - 120)
        return token
    except Exception as e:
        print(f"[graph] token request failed: {e}")
        return None


def graph_get(path, token, params=None):
    """GET against Graph, path already including the leading slash after
    /v1.0 (e.g. '/users/{mailbox}/mailFolders/inbox/messages'). Returns the
    parsed JSON body, or None on any failure."""
    url = f"{GRAPH_BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params)
    try:
        req = Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[graph] GET {path} failed: {e}")
        return None


def fetch_graph_messages(token, folder, since_iso, top=100, mailbox=None):
    """Every message in the given well-known folder ('inbox' or 'sentitems')
    of one mailbox, on/after since_iso, newest first. Only pulls the fields
    the scan actually needs — Graph counts request size against throttling
    limits, so no point asking for the full HTML body of every message in a
    multi-week window.

    `mailbox` defaults to the first configured one purely so existing
    single-mailbox callers keep working; the scan passes it explicitly.
    """
    mailbox = mailbox or MS_GRAPH_MAILBOX
    if not mailbox:
        return []
    date_field = "sentDateTime" if folder == "sentitems" else "receivedDateTime"
    body = graph_get(
        f"/users/{mailbox}/mailFolders/{folder}/messages", token,
        params={
            "$select": "id,conversationId,receivedDateTime,sentDateTime,from,subject,bodyPreview,webLink",
            "$filter": f"{date_field} ge {since_iso}",
            "$orderby": f"{date_field} desc",
            "$top": str(top),
        },
    )
    return body.get("value", []) if body else []


def _positive_float(raw, fallback):
    """A positive number from a form field, or `fallback` if it's blank or
    nonsense. Callers pass fallback=None when they need to tell the two
    apart rather than silently substituting a default."""
    try:
        value = float((raw or "").strip())
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def resolve_inbox_owner(conn, mb):
    """Who should pick this one up right now.

    An inbox can be pinned to a person, or set to follow whoever is actually
    on shift — which is what you want for something like restaurant@, where
    "the person handling it" is whoever is working tonight rather than a fixed
    name. Falls back through: someone on shift -> the inbox's named owner ->
    nobody (which the caller turns into the château owner).

    Also skips anyone who is on approved leave or recorded absent today, so
    mail doesn't get assigned into a void while they're away.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    away = {
        r["user_id"] for r in conn.execute(
            """SELECT user_id FROM leave_requests
               WHERE status = 'approved' AND start_date <= ? AND end_date >= ?
               UNION
               SELECT user_id FROM absences WHERE start_date <= ? AND end_date >= ?""",
            (today, today, today, today),
        ).fetchall()
    }

    if mb["route_to_on_shift"]:
        on_shift = conn.execute(
            """SELECT time_entries.user_id FROM time_entries
               JOIN users ON users.id = time_entries.user_id
               WHERE time_entries.clock_out_at IS NULL AND users.status = 'active'
               ORDER BY time_entries.clock_in_at"""
        ).fetchall()
        for row in on_shift:
            if row["user_id"] not in away:
                return row["user_id"]

    default_id = mb["default_user_id"]
    if default_id and default_id not in away:
        return default_id
    return None


def guest_context_for_emails(conn, addresses):
    """Who each sender is, for a batch of email addresses at once.

    Whoever picks up a flagged email should see "4th stay, allergic to
    shellfish, in the Rose Room until Thursday" without going hunting for it.
    Batched deliberately: doing this per flag would be a query per row on a
    page that can list dozens.
    """
    wanted = {(a or "").strip().lower() for a in addresses if a and a.strip()}
    if not wanted:
        return {}
    today = datetime.now(timezone.utc).date().isoformat()
    placeholders = ",".join("?" * len(wanted))
    args = list(wanted)

    out = {}
    for r in conn.execute(
        f"""SELECT * FROM guests WHERE LOWER(email) IN ({placeholders})""", args
    ).fetchall():
        out[r["email"].lower()] = {
            "profile_id": r["id"], "name": r["name"], "vip": bool(r["vip"]),
            "dietary_notes": r["dietary_notes"], "preferences": r["preferences"],
            "stays": 0, "current": None, "upcoming": None, "lifetime": 0.0,
        }

    # Confirmed stays: how many, what they're in now, what's next, total spend.
    for r in conn.execute(
        f"""SELECT bookings.guest_email AS email, bookings.arrival_date, bookings.departure_date,
                   bookings.total_price, bookings.status, rooms.name AS room_name
            FROM bookings LEFT JOIN rooms ON rooms.id = bookings.room_id
            WHERE LOWER(bookings.guest_email) IN ({placeholders})
              AND bookings.status = 'confirmed'
            ORDER BY bookings.arrival_date""", args
    ).fetchall():
        key = (r["email"] or "").lower()
        ctx = out.setdefault(key, {
            "profile_id": None, "name": None, "vip": False, "dietary_notes": None,
            "preferences": None, "stays": 0, "current": None, "upcoming": None, "lifetime": 0.0,
        })
        ctx["stays"] += 1
        ctx["lifetime"] += r["total_price"] or 0
        if r["arrival_date"] <= today < r["departure_date"]:
            ctx["current"] = f"{r['room_name'] or 'room'} until {r['departure_date']}"
        elif r["arrival_date"] > today and not ctx["upcoming"]:
            ctx["upcoming"] = f"{r['room_name'] or 'room'} from {r['arrival_date']}"
    return out


def escalate_stale_flags(conn):
    """Nudge the owner when a flagged email has sat unresolved past its
    inbox's threshold.

    Assigning something isn't the same as it being handled -- without this,
    a flag can sit open indefinitely and the system only *records* that
    something was missed rather than preventing it. Fires once per flag
    (escalated_at), so it's a prompt, not a nag.
    """
    now = datetime.now(timezone.utc)
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    if not owner_row:
        return 0
    thresholds = {
        r["mailbox"]: (r["escalate_hours"] or 48)
        for r in conn.execute("SELECT mailbox, escalate_hours FROM mailbox_routing").fetchall()
    }
    escalated = 0
    for f in conn.execute(
        """SELECT email_flags.*, users.name AS assigned_to_name FROM email_flags
           LEFT JOIN users ON users.id = email_flags.assigned_to_user_id
           WHERE email_flags.status = 'open' AND email_flags.escalated_at IS NULL"""
    ).fetchall():
        hours = thresholds.get(f["mailbox"], 48)
        received = parse_datetime_iso(f["received_at"])
        if not received or (now - received) < timedelta(hours=hours):
            continue
        who = f["assigned_to_name"] or "nobody"
        send_notification(
            conn, owner_row["id"], "inbox_flag_escalated",
            f"Still unhandled after {int(hours)}h: {f['subject'] or '(no subject)'}",
            body=f"In {f['mailbox'] or 'the inbox'}, with {who}. "
                 f"From {f['from_name'] or f['from_address'] or 'unknown sender'}.",
            link="/admin/inbox-flags",
        )
        conn.execute("UPDATE email_flags SET escalated_at = ? WHERE id = ?",
                     (now.isoformat(), f["id"]))
        escalated += 1
    if escalated:
        conn.commit()
    return escalated


def cross_inbox_duplicates(conn):
    """Senders who have open flags in more than one inbox.

    The failure this catches: a guest emails bookings@ and experience@ about
    the same trip, two different people answer separately, and the guest gets
    two different answers. Nobody notices because each inbox looks fine on its
    own.
    """
    rows = conn.execute(
        """SELECT LOWER(from_address) AS sender,
                  COUNT(DISTINCT mailbox) AS inbox_count,
                  COUNT(*) AS flag_count,
                  GROUP_CONCAT(DISTINCT mailbox) AS inboxes,
                  MAX(from_name) AS from_name
           FROM email_flags
           WHERE status = 'open' AND mailbox IS NOT NULL
             AND from_address IS NOT NULL AND from_address != ''
           GROUP BY LOWER(from_address)
           HAVING inbox_count > 1
           ORDER BY inbox_count DESC, flag_count DESC"""
    ).fetchall()
    return [dict(r, inboxes=(r["inboxes"] or "").split(",")) for r in rows]


def inbox_response_stats(conn, since_days=30):
    """Average and worst first-response time per inbox, plus how many are
    still waiting. Measured from when the message arrived to when a reply
    actually went out, so it reflects the guest's experience rather than how
    quickly a flag was ticked off."""
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    rows = conn.execute(
        """SELECT mailbox, received_at, first_reply_at, status
           FROM email_flags
           WHERE mailbox IS NOT NULL AND received_at >= ?""",
        (since,),
    ).fetchall()
    by_box = {}
    for r in rows:
        stat = by_box.setdefault(r["mailbox"], {"mailbox": r["mailbox"], "hours": [], "waiting": 0})
        if r["first_reply_at"]:
            start, end = parse_datetime_iso(r["received_at"]), parse_datetime_iso(r["first_reply_at"])
            if start and end and end > start:
                stat["hours"].append((end - start).total_seconds() / 3600)
        elif r["status"] == "open":
            stat["waiting"] += 1
    out = []
    for stat in by_box.values():
        hours = stat["hours"]
        out.append({
            "mailbox": stat["mailbox"],
            "replied": len(hours),
            "waiting": stat["waiting"],
            "avg_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "worst_hours": round(max(hours), 1) if hours else None,
        })
    out.sort(key=lambda s: (s["avg_hours"] is None, -(s["avg_hours"] or 0)))
    return out


def monitored_mailboxes(conn):
    """Which inboxes to scan, and who owns each. Seeds itself from the
    MS_GRAPH_MAILBOXES env var so adding an inbox is a config change, then
    lets the owner set a default assignee per inbox in the admin UI.

    Returns [{mailbox, label, default_user_id}] for active inboxes only.
    """
    known = {r["mailbox"] for r in conn.execute("SELECT mailbox FROM mailbox_routing").fetchall()}
    now_iso = datetime.now(timezone.utc).isoformat()
    for mb in MS_GRAPH_MAILBOXES:
        if mb not in known:
            # Label defaults to the local part -- "restaurant@..." -> "Restaurant"
            label = mb.split("@")[0].replace(".", " ").replace("-", " ").title()
            conn.execute(
                "INSERT OR IGNORE INTO mailbox_routing (mailbox, label, active, created_at) VALUES (?, ?, 1, ?)",
                (mb, label, now_iso),
            )
    if MS_GRAPH_MAILBOXES:
        conn.commit()
    return conn.execute(
        """SELECT mailbox_routing.*, users.name AS default_user_name
           FROM mailbox_routing LEFT JOIN users ON users.id = mailbox_routing.default_user_id
           WHERE mailbox_routing.active = 1 ORDER BY mailbox_routing.mailbox"""
    ).fetchall()


_MONEY_RE = re.compile(
    r"(?:€|EUR)\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)"
    r"|(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s?(?:€|EUR\b|euros?\b)",
    re.IGNORECASE,
)
_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DMY_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_DATE_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTH_NAMES) + r")\b", re.IGNORECASE,
)
_DATE_MONTH_DAY_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE,
)
EVENT_INQUIRY_KEYWORDS = (
    "wedding", "photoshoot", "photo shoot", "elopement", "engagement shoot",
    "corporate event", "corporate retreat", "film shoot",
)


def extract_money_amounts(text):
    """Every €/EUR/euros-tagged amount mentioned in text, as floats, in the
    order they appear. Deliberately requires a currency marker — a bare
    number ('room 12', 'the 5th') is not treated as a price."""
    amounts = []
    for m in _MONEY_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        try:
            amounts.append(float(raw.replace(".", "").replace(",", ".") if "," in raw and raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")))
        except ValueError:
            continue
    return amounts


def extract_dates(text, received_at_iso):
    """Every date-like mention in text, resolved to date objects. Day-only
    mentions ('12 August') have no year in the text, so they're anchored
    to the email's received year, rolling forward a year if that would
    otherwise land more than a month in the past (an email about 'March 3rd'
    received in November almost certainly means next March)."""
    text = text or ""
    received_date = parse_date((received_at_iso or "")[:10]) or datetime.now(timezone.utc).date()
    found = []
    for m in _DATE_ISO_RE.finditer(text):
        d = parse_date(m.group(0))
        if d:
            found.append(d)
    for m in _DATE_DMY_RE.finditer(text):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.append(date(year, month, day))
        except ValueError:
            continue
    for m in _DATE_DAY_MONTH_RE.finditer(text):
        day, month = int(m.group(1)), _MONTH_NAMES[m.group(2).lower()]
        found.append(_anchor_year(day, month, received_date))
    for m in _DATE_MONTH_DAY_RE.finditer(text):
        month, day = _MONTH_NAMES[m.group(1).lower()], int(m.group(2))
        found.append(_anchor_year(day, month, received_date))
    return found


def _anchor_year(day, month, received_date):
    try:
        candidate = date(received_date.year, month, day)
    except ValueError:
        return received_date
    if candidate < received_date - timedelta(days=30):
        candidate = date(received_date.year + 1, month, day)
    return candidate


def guess_email_conflict(conn, from_address, subject, body_text, received_at_iso):
    """Best-effort cross-reference of a price/date mentioned in an email
    against this app's real pricing and availability data. Only returns a
    result when there's something concrete to show a human — a category
    match with no price or conflict returns None rather than flagging
    every email that happens to name a room or workshop."""
    text = f"{subject or ''}\n{body_text or ''}"
    lowered = text.lower()
    amounts = extract_money_amounts(text)
    dates = extract_dates(text, received_at_iso)

    category = None
    computed_price = None
    availability_conflict = False
    note = None

    room = next((r for r in conn.execute("SELECT * FROM rooms WHERE active = 1").fetchall() if r["name"].lower() in lowered), None)
    if room:
        category = f"Room: {room['name']}"
        if len(dates) >= 2:
            arrival, departure = sorted(dates)[0], sorted(dates)[-1]
            if departure > arrival:
                computed_price = compute_room_total(conn, room, arrival, departure)
                available, _ = is_range_available(conn, room["id"], arrival, departure)
                if not available:
                    availability_conflict = True
                    note = f"{arrival.isoformat()} to {departure.isoformat()} is no longer available for {room['name']}."

    if not category:
        workshop = next((w for w in conn.execute("SELECT * FROM workshops WHERE active = 1").fetchall() if w["title"].lower() in lowered), None)
        if workshop:
            category = f"Workshop: {workshop['title']}"
            computed_price = workshop["price_per_person"] or None

    if not category and any(k in lowered for k in ("dinner", "restaurant reservation", "table for", "book a table")):
        settings = get_restaurant_settings(conn)
        if settings and settings["price_per_person"]:
            category = "Restaurant"
            computed_price = settings["price_per_person"]
            if dates:
                remaining = restaurant_remaining_capacity(conn, dates[0].isoformat())
                if remaining is not None and remaining <= 0:
                    availability_conflict = True
                    note = f"{dates[0].isoformat()} is fully booked for dinner."

    if not category and any(k in lowered for k in EVENT_INQUIRY_KEYWORDS):
        category = "Event"
        inquiry = conn.execute(
            "SELECT quoted_price FROM event_inquiries WHERE contact_email = ? AND quoted_price IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1", (from_address,),
        ).fetchone()
        if inquiry:
            computed_price = inquiry["quoted_price"]

    extracted_price = amounts[0] if amounts else None
    price_conflict = False
    if computed_price is not None and extracted_price is not None:
        if abs(computed_price - extracted_price) > max(1.0, computed_price * 0.02):
            price_conflict = True
            note = note or f"Email mentions €{extracted_price:.2f}; actual price is €{computed_price:.2f}."

    if not category and extracted_price is None:
        return None
    if not (price_conflict or availability_conflict or extracted_price is not None):
        return None

    return {
        "category": category, "extracted_price": extracted_price, "computed_price": computed_price,
        "extracted_dates": ", ".join(d.isoformat() for d in dates) if dates else None,
        "price_conflict": price_conflict, "availability_conflict": availability_conflict, "note": note,
    }


# Automation engine — a background thread that runs the housekeeping jobs
# that used to only fire opportunistically on a dashboard visit (stale
# booking expiry, arrival prep), plus new periodic jobs (owner digest, iCal
# sync, workshop balance reminders) that used to require an external cron
# service hitting a token-gated URL. Each job is gated by claim_job_run's
# atomic UPDATE-then-INSERT lock, so running two gunicorn workers (see
# DEPLOY.md's `gunicorn -w 2` instructions) can't double-send anything —
# whichever worker's tick wins the row update is the one that runs it.
# ---------------------------------------------------------------------------

def get_automation_settings(conn):
    rows = conn.execute("SELECT key, value FROM app_settings WHERE key LIKE 'automation_%'").fetchall()
    values = {r["key"]: r["value"] for r in rows}
    return {k: values.get(k, default) for k, default in AUTOMATION_SETTING_DEFAULTS.items()}


def claim_job_run(conn, job_name, cooldown_seconds):
    """True if this call may run job_name now, i.e. it either has never run
    or last ran more than cooldown_seconds ago. The UPDATE...WHERE is the
    lock, not a prior SELECT — two workers ticking at the same moment can't
    both claim the same run, since only one UPDATE can actually change the
    row (or win the INSERT race on first-ever run)."""
    now = datetime.now(timezone.utc)
    cutoff_iso = (now - timedelta(seconds=cooldown_seconds)).isoformat()
    cur = conn.execute(
        "UPDATE automation_runs SET last_ran_at = ? WHERE job_name = ? AND last_ran_at < ?",
        (now.isoformat(), job_name, cutoff_iso),
    )
    if cur.rowcount:
        conn.commit()
        return True
    try:
        conn.execute("INSERT INTO automation_runs (job_name, last_ran_at) VALUES (?, ?)", (job_name, now.isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def run_housekeeping_job(conn):
    today = datetime.now(timezone.utc).date()
    expired = expire_stale_pending_bookings(conn)
    prepped = auto_prep_upcoming_arrivals(conn, today)
    return f"expired {expired} stale booking(s), prepped {prepped} arrival(s)"


def run_daily_digest_job(conn):
    to_address = owner_email(conn)
    if not to_address:
        return "no owner email configured"
    body = build_owner_digest(conn)
    sent = send_email(to_address, "Your daily summary", body)
    return f"sent to {to_address}" if sent else "send failed or email not configured"


def run_ical_sync_job(conn):
    sources = conn.execute("SELECT * FROM ical_sources").fetchall()
    ok_count = sum(1 for source in sources if sync_ical_source(conn, source))
    return f"synced {ok_count}/{len(sources)} source(s)"


def run_workshop_balance_reminder_job(conn, days_before):
    cutoff = (datetime.now(timezone.utc).date() + timedelta(days=days_before)).isoformat()
    due = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.status = 'confirmed' AND workshop_bookings.balance_amount > 0
             AND workshop_bookings.balance_paid_at IS NULL AND workshop_bookings.balance_reminder_sent_at IS NULL
             AND workshop_bookings.balance_due_date IS NOT NULL AND workshop_bookings.balance_due_date <= ?""",
        (cutoff,),
    ).fetchall()
    sent = 0
    for booking in due:
        send_workshop_email(conn, booking, "workshop_balance_reminder", workshop_email_context(booking))
        conn.execute(
            "UPDATE workshop_bookings SET balance_reminder_sent_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), booking["id"]),
        )
        sent += 1
    if sent:
        conn.commit()
    return f"reminded {sent} of {len(due)} due booking(s)"


def run_workshop_feedback_request_job(conn):
    """Emails a feedback request once per confirmed registration, a day
    after the session ends — mirrors the room booking's checkout-time
    'How was your stay?' email, but workshops have no checkout step to
    hang the trigger off, so this runs on a delay from the session's
    end_date instead."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    due = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.status = 'confirmed' AND workshop_bookings.feedback_requested_at IS NULL
             AND workshop_sessions.end_date <= ?""",
        (cutoff,),
    ).fetchall()
    sent = 0
    for booking in due:
        subject, body = render_email_template(conn, "workshop_feedback_request", {
            "guest_name": booking["guest_name"], "workshop_title": booking["title"],
            "feedback_url": url_for("workshop_feedback", token=booking["manage_token"], _external=True),
        })
        if subject and not booking["do_not_email"]:
            send_email(booking["guest_email"], subject, body)
        conn.execute(
            "UPDATE workshop_bookings SET feedback_requested_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), booking["id"]),
        )
        sent += 1
    if sent:
        conn.commit()
    return f"requested feedback for {sent} booking(s)"


def notify_room_waitlist_opening(conn, arrival_iso, departure_iso):
    """Emails every 'open' waitlist entry whose desired range overlaps the
    dates that just freed up, then marks them 'contacted' so the next
    cancellation on an unrelated date doesn't re-notify someone who's
    already been pointed at the booking page. Returns the entries notified
    (an empty list if the automation setting is off — callers fall back to
    the old 'go check the waitlist' flash note in that case)."""
    settings = get_automation_settings(conn)
    if settings["automation_waitlist_autonotify_enabled"] != "1":
        return []
    matches = [e for e in matching_waitlist_entries(conn, arrival_iso, departure_iso) if e["status"] == "open"]
    book_url = url_for("book_rooms", _external=True)
    notified = []
    for entry in matches:
        context = {
            "name": entry["name"], "desired_arrival": entry["desired_arrival"],
            "desired_departure": entry["desired_departure"], "book_url": book_url,
        }
        subject, body = render_email_template(conn, "room_waitlist_opening", context)
        if subject and send_email(entry["email"], subject, body):
            conn.execute("UPDATE waitlist_entries SET status = 'contacted' WHERE id = ?", (entry["id"],))
            notified.append(entry)
    if notified:
        conn.commit()
    return notified


def notify_restaurant_waitlist_opening(conn, dinner_date_iso):
    settings = get_automation_settings(conn)
    if settings["automation_waitlist_autonotify_enabled"] != "1":
        return []
    matches = [e for e in matching_restaurant_waitlist_entries(conn, dinner_date_iso) if e["status"] == "open"]
    book_url = url_for("restaurant_book", _external=True, date=dinner_date_iso)
    notified = []
    for entry in matches:
        context = {
            "name": entry["name"], "desired_date": format_date_human(dinner_date_iso),
            "party_size": entry["party_size"] or "?", "book_url": book_url,
        }
        subject, body = render_email_template(conn, "restaurant_waitlist_opening", context)
        if subject and send_email(entry["email"], subject, body):
            conn.execute("UPDATE restaurant_waitlist SET status = 'contacted' WHERE id = ?", (entry["id"],))
            notified.append(entry)
    if notified:
        conn.commit()
    return notified


def notify_workshop_waitlist_opening(conn, session_id):
    settings = get_automation_settings(conn)
    if settings["automation_waitlist_autonotify_enabled"] != "1":
        return []
    matches = [e for e in matching_workshop_waitlist_entries(conn, session_id) if e["status"] == "open"]
    if not matches:
        return []
    session_row = conn.execute(
        """SELECT workshop_sessions.*, workshops.title FROM workshop_sessions
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id WHERE workshop_sessions.id = ?""",
        (session_id,),
    ).fetchone()
    date_line = format_date_human(session_row["start_date"])
    if session_row["end_date"] != session_row["start_date"]:
        date_line += f" to {format_date_human(session_row['end_date'])}"
    register_url = url_for("workshop_register", session_id=session_id, _external=True)
    notified = []
    for entry in matches:
        context = {
            "name": entry["name"], "workshop_title": session_row["title"], "dates": date_line,
            "register_url": register_url,
        }
        subject, body = render_email_template(conn, "workshop_waitlist_opening", context)
        if subject and send_email(entry["email"], subject, body):
            conn.execute("UPDATE workshop_waitlist SET status = 'contacted' WHERE id = ?", (entry["id"],))
            notified.append(entry)
    if notified:
        conn.commit()
    return notified


def run_email_inbox_scan_job(conn):
    """Scans MS_GRAPH_MAILBOX's inbox for messages that either haven't
    been replied to within the configured window, mention a price/date
    that conflicts with real pricing/availability data, or whose actual
    sent reply does — upserts each into email_flags for the Inbox Flags
    admin page. A no-op (not an error) when Graph isn't configured, same
    as every other optional integration here."""
    if not graph_enabled():
        return "Microsoft Graph not configured"
    token = get_graph_token()
    if not token:
        return "could not get a Graph token"

    settings = get_automation_settings(conn)
    try:
        unanswered_hours = float(settings["automation_email_unanswered_hours"])
    except (TypeError, ValueError):
        unanswered_hours = 24
    try:
        lookback_days = max(1, int(settings["automation_email_scan_lookback_days"]))
    except (TypeError, ValueError):
        lookback_days = 14

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    since_iso = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    mailboxes = monitored_mailboxes(conn)
    if not mailboxes:
        return "no mailboxes configured"
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    seen = flagged = 0

    # One inbox per business area, so scan each in turn. Everything below is
    # per-mailbox: a conversation in restaurant@ is unrelated to one in
    # bookings@, so the sent-items reply map must not be shared between them
    # or a reply in one inbox would silently mark another's email answered.
    for mb in mailboxes:
        mailbox = mb["mailbox"]
        # Resolved per scan, not per config: for an inbox set to follow the
        # rota this is whoever is clocked in right now, skipping anyone on
        # leave or recorded absent.
        default_user_id = resolve_inbox_owner(conn, mb)
        # conversationId -> latest sent message, for both the "was this answered
        # in time" check and re-running the conflict guesser against what the
        # reply itself actually says (a reply can quote a stale price even when
        # the guest's own message never mentioned one).
        reply_map = {}
        reply_by_conversation = {}
        for sent in fetch_graph_messages(token, "sentitems", since_iso, top=200, mailbox=mailbox):
            conv_id, sent_at = sent.get("conversationId"), sent.get("sentDateTime")
            if conv_id and sent_at and (conv_id not in reply_map or sent_at > reply_map[conv_id]):
                reply_map[conv_id] = sent_at
                reply_by_conversation[conv_id] = sent
        inbox_messages = fetch_graph_messages(token, "inbox", since_iso, top=200, mailbox=mailbox)
        for msg in inbox_messages:
            message_id = msg.get("id")
            received_at = msg.get("receivedDateTime")
            if not message_id or not received_at:
                continue
            seen += 1

            existing = conn.execute(
                "SELECT id, status, last_reply_checked_id, reply_price_conflict, reply_availability_conflict, "
                "reply_detail_note, first_reply_at FROM email_flags WHERE graph_message_id = ?", (message_id,)
            ).fetchone()
            if existing and existing["status"] != "open":
                continue  # a human already resolved/dismissed this one — don't resurrect it

            conv_id = msg.get("conversationId")
            last_reply = reply_map.get(conv_id)
            # When a reply went out AFTER this message arrived, that's the
            # first response -- recorded once so response-time stats reflect
            # the guest's actual wait, not how fast a flag was ticked off.
            first_reply_at = existing["first_reply_at"] if (existing and existing["first_reply_at"]) else None
            if not first_reply_at and last_reply and last_reply > received_at:
                first_reply_at = last_reply
            received_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            unanswered = bool((not last_reply or last_reply < received_at) and (now - received_dt) > timedelta(hours=unanswered_hours))

            from_info = (msg.get("from") or {}).get("emailAddress") or {}
            from_address = from_info.get("address", "")
            from_name = from_info.get("name", "")
            subject = msg.get("subject") or ""
            preview = msg.get("bodyPreview") or ""
            conflict = guess_email_conflict(conn, from_address, subject, preview, received_at)

            # Only re-run the conflict check when a genuinely new reply has gone
            # out since the last tick (tracked by the reply's own Graph id) —
            # otherwise the same reply gets re-scored every tick forever. But
            # "nothing new to check" must not be confused with "no conflict":
            # carry the previous verdict forward so a real finding doesn't get
            # silently cleared on the very next scan just because there was
            # nothing new to re-derive it from.
            reply = reply_by_conversation.get(conv_id)
            already_checked_reply_id = existing["last_reply_checked_id"] if existing else None
            if reply and reply.get("id") != already_checked_reply_id:
                reply_conflict = guess_email_conflict(
                    conn, from_address, reply.get("subject") or subject,
                    reply.get("bodyPreview") or "", reply.get("sentDateTime") or now_iso,
                )
                reply_price_conflict_val = bool(reply_conflict and reply_conflict["price_conflict"])
                reply_availability_conflict_val = bool(reply_conflict and reply_conflict["availability_conflict"])
                reply_note_val = reply_conflict["note"] if reply_conflict else None
            elif existing:
                reply_price_conflict_val = bool(existing["reply_price_conflict"])
                reply_availability_conflict_val = bool(existing["reply_availability_conflict"])
                reply_note_val = existing["reply_detail_note"]
            else:
                reply_price_conflict_val = reply_availability_conflict_val = False
                reply_note_val = None
            checked_reply_id = reply["id"] if reply else already_checked_reply_id

            if not unanswered and not conflict and not reply_price_conflict_val and not reply_availability_conflict_val:
                if existing:
                    # a real reply went out (or a conflict resolved itself) since the last
                    # scan — clear the flag automatically rather than leaving it stale.
                    conn.execute(
                        "UPDATE email_flags SET unanswered=0, price_conflict=0, availability_conflict=0, "
                        "reply_price_conflict=0, reply_availability_conflict=0, last_reply_checked_id=?, "
                        "first_reply_at=COALESCE(first_reply_at, ?), updated_at=? WHERE id=?",
                        (checked_reply_id, first_reply_at, now_iso, existing["id"]),
                    )
                continue
            flagged += 1

            common = (
                conv_id, from_name, from_address, subject, preview[:500], msg.get("webLink"), received_at,
                int(unanswered),
                int(bool(conflict and conflict["price_conflict"])),
                int(bool(conflict and conflict["availability_conflict"])),
                conflict["category"] if conflict else None,
                conflict["extracted_price"] if conflict else None,
                conflict["computed_price"] if conflict else None,
                conflict["extracted_dates"] if conflict else None,
                conflict["note"] if conflict else None,
                int(reply_price_conflict_val),
                int(reply_availability_conflict_val),
                reply_note_val,
                checked_reply_id,
                mailbox,
                first_reply_at,
            )
            if existing:
                conn.execute(
                    """UPDATE email_flags SET conversation_id=?, from_name=?, from_address=?, subject=?, preview=?,
                       web_link=?, received_at=?, unanswered=?, price_conflict=?, availability_conflict=?,
                       conflict_category=?, extracted_price=?, computed_price=?, extracted_dates=?, detail_note=?,
                       reply_price_conflict=?, reply_availability_conflict=?, reply_detail_note=?, last_reply_checked_id=?,
                       mailbox=?, first_reply_at=?, updated_at=? WHERE id=?""",
                    common + (now_iso, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO email_flags (graph_message_id, conversation_id, from_name, from_address, subject,
                       preview, web_link, received_at, unanswered, price_conflict, availability_conflict,
                       conflict_category, extracted_price, computed_price, extracted_dates, detail_note,
                       reply_price_conflict, reply_availability_conflict, reply_detail_note, last_reply_checked_id,
                       mailbox, first_reply_at, assigned_to_user_id, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                    (message_id,) + common + (default_user_id, now_iso, now_iso),
                )
                # A brand-new flag, not just an update to one already on the board —
                # this is the "pops up on your screen" half of the triage board,
                # reusing the same notification/web-push path task assignments use.
                # Route it to whoever owns that inbox; fall back to the owner
                # when an inbox has nobody assigned to it yet.
                notify_user_id = default_user_id or (owner_row["id"] if owner_row else None)
                if notify_user_id:
                    what = "reply" if (reply_price_conflict_val or reply_availability_conflict_val) else ("email" if conflict else "unanswered email")
                    send_notification(
                        conn, notify_user_id, "inbox_flag_new",
                        f"[{mb['label'] or mailbox}] Flagged {what}: {subject or '(no subject)'}",
                        body=f"From {from_name or from_address or 'unknown sender'}", link="/admin/inbox-flags",
                    )
    conn.commit()
    escalated = escalate_stale_flags(conn)
    tail = f", {escalated} escalated" if escalated else ""
    return f"scanned {seen} message(s) across {len(mailboxes)} mailbox(es), {flagged} flagged{tail}"


def run_stale_shift_cleanup_job(conn, hours_threshold):
    """Closes any time_entries row that's been open longer than
    hours_threshold — the safety net for 'forgot to clock out' now that
    clocking out is a deliberate action rather than a side effect of
    logging out. Caps the recorded clock-out at clock_in + hours_threshold
    (not 'whenever this job happens to notice'), since that's the most
    defensible estimate of when a real shift would have ended — using the
    job's run time instead would inflate hours further the longer it goes
    unnoticed. Notifies both the employee and the owner so it's visible
    and correctable, not just a silent database change."""
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=hours_threshold)).isoformat()
    stale = conn.execute(
        "SELECT * FROM time_entries WHERE clock_out_at IS NULL AND clock_in_at <= ?", (cutoff_iso,)
    ).fetchall()
    owner_row = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    for entry in stale:
        clock_out_at = (parse_datetime_iso(entry["clock_in_at"]) + timedelta(hours=hours_threshold)).isoformat()
        conn.execute(
            "UPDATE time_entries SET clock_out_at = ?, auto_closed = 1 WHERE id = ?",
            (clock_out_at, entry["id"]),
        )
        employee = conn.execute("SELECT name FROM users WHERE id = ?", (entry["user_id"],)).fetchone()
        title = f"A shift was auto-closed after {hours_threshold}h — check it's right"
        body = f"{employee['name'] if employee else 'Someone'} clocked in at {local_datetime_str(entry['clock_in_at'])} and never clocked out, so it was closed automatically."
        # /profile is not a route — the employee's own page is /directory/<id>,
        # so this notification 404'd for the person being told to check it.
        send_notification(
            conn, entry["user_id"], "shift_auto_closed", "Your last shift was auto-closed",
            body=body, link=f"/directory/{entry['user_id']}",
        )
        if owner_row:
            send_notification(conn, owner_row["id"], "shift_auto_closed", title, body=body, link=f"/directory/{entry['user_id']}")
    if stale:
        conn.commit()
    return f"auto-closed {len(stale)} stale shift(s)"


AUTOMATION_JOBS = [
    ("housekeeping", "automation_housekeeping_enabled", None, 600, run_housekeeping_job),
    ("daily_digest", "automation_daily_digest_enabled", None, 24 * 3600, run_daily_digest_job),
    ("ical_sync", "automation_ical_sync_enabled", "automation_ical_sync_interval_hours", None, run_ical_sync_job),
    ("workshop_feedback_request", "automation_workshop_feedback_enabled", None, 24 * 3600, run_workshop_feedback_request_job),
    ("email_inbox_scan", "automation_email_scan_enabled", None, 900, run_email_inbox_scan_job),
    ("hr_escalation", "automation_hr_escalation_enabled", None, 21600, run_hr_escalation_job),
    ("campaign_triggers", "automation_campaign_triggers_enabled", None, 21600, run_campaign_triggers_job),
]


def automation_tick():
    """One pass over every periodic job — called on a timer from the
    background thread, and also directly by the admin 'run now' buttons
    (which bypass the cooldown by deleting that job's automation_runs row
    first). Each job gets its own connection/commit so one failing job
    can't roll back another's progress.

    Runs inside a fake request context (base_url=PUBLIC_BASE_URL) because
    the background thread has no real incoming request — without this,
    any job that builds an absolute link (e.g. the balance-reminder email's
    "manage your registration" URL) would raise RuntimeError the first time
    it actually has something to send, instead of failing in testing."""
    with app.test_request_context(base_url=PUBLIC_BASE_URL or None):
        conn = get_db()
        settings = get_automation_settings(conn)
        for job_name, enabled_key, interval_key, fixed_cooldown, job_fn in AUTOMATION_JOBS:
            if settings[enabled_key] != "1":
                continue
            cooldown = fixed_cooldown
            if interval_key:
                try:
                    cooldown = max(1, float(settings[interval_key])) * 3600
                except (TypeError, ValueError):
                    cooldown = 6 * 3600
            if not claim_job_run(conn, job_name, cooldown):
                continue
            try:
                result = job_fn(conn)
                print(f"[automation] {job_name}: {result}")
            except Exception as e:
                print(f"[automation] {job_name} failed: {e}")

        if settings["automation_workshop_balance_reminder_enabled"] == "1":
            try:
                days_before = int(settings["automation_workshop_balance_reminder_days_before"])
            except (TypeError, ValueError):
                days_before = 7
            if claim_job_run(conn, "workshop_balance_reminder", 24 * 3600):
                try:
                    result = run_workshop_balance_reminder_job(conn, days_before)
                    print(f"[automation] workshop_balance_reminder: {result}")
                except Exception as e:
                    print(f"[automation] workshop_balance_reminder failed: {e}")

        if settings["automation_stale_shift_enabled"] == "1":
            try:
                stale_hours = float(settings["automation_stale_shift_hours"])
            except (TypeError, ValueError):
                stale_hours = 14
            if claim_job_run(conn, "stale_shift_cleanup", 3600):
                try:
                    result = run_stale_shift_cleanup_job(conn, stale_hours)
                    print(f"[automation] stale_shift_cleanup: {result}")
                except Exception as e:
                    print(f"[automation] stale_shift_cleanup failed: {e}")
        conn.close()


def automation_loop():
    while True:
        try:
            automation_tick()
        except Exception as e:
            print(f"[automation] tick failed: {e}")
        time.sleep(AUTOMATION_TICK_SECONDS)


AUTOMATION_JOB_LABELS = {
    "housekeeping": "Housekeeping (expire stale bookings, prep arrivals)",
    "daily_digest": "Daily owner digest email",
    "ical_sync": "iCal sync",
    "workshop_balance_reminder": "Workshop balance-due reminders",
    "workshop_feedback_request": "Workshop feedback requests",
    "email_inbox_scan": "Inbox scan (unanswered + pricing/availability flags)",
    "stale_shift_cleanup": "Stale shift cleanup (forgot to clock out)",
    "hr_escalation": "HR chase-ups (overdue approvals, reviews, certificates)",
    "campaign_triggers": "Automated guest emails (before arrival, after departure)",
}


@app.route("/admin/automation")
@owner_required
def admin_automation():
    conn = get_db()
    settings = get_automation_settings(conn)
    last_runs = {r["job_name"]: r["last_ran_at"] for r in conn.execute("SELECT job_name, last_ran_at FROM automation_runs").fetchall()}
    conn.close()
    return render_template(
        "admin_automation.html", settings=settings, last_runs=last_runs, job_labels=AUTOMATION_JOB_LABELS,
        graph_enabled=graph_enabled(),
    )


@app.route("/admin/automation/settings", methods=["POST"])
@owner_required
def update_automation_settings():
    conn = get_db()
    updates = {
        "automation_housekeeping_enabled": "1" if request.form.get("automation_housekeeping_enabled") else "0",
        "automation_daily_digest_enabled": "1" if request.form.get("automation_daily_digest_enabled") else "0",
        "automation_ical_sync_enabled": "1" if request.form.get("automation_ical_sync_enabled") else "0",
        "automation_workshop_balance_reminder_enabled": "1" if request.form.get("automation_workshop_balance_reminder_enabled") else "0",
        "automation_waitlist_autonotify_enabled": "1" if request.form.get("automation_waitlist_autonotify_enabled") else "0",
        "automation_workshop_feedback_enabled": "1" if request.form.get("automation_workshop_feedback_enabled") else "0",
        "automation_email_scan_enabled": "1" if request.form.get("automation_email_scan_enabled") else "0",
        "automation_hr_escalation_enabled": "1" if request.form.get("automation_hr_escalation_enabled") else "0",
        "automation_campaign_triggers_enabled": "1" if request.form.get("automation_campaign_triggers_enabled") else "0",
        "automation_stale_shift_enabled": "1" if request.form.get("automation_stale_shift_enabled") else "0",
    }
    interval_raw = request.form.get("automation_ical_sync_interval_hours", "").strip()
    try:
        updates["automation_ical_sync_interval_hours"] = str(max(1, float(interval_raw)))
    except ValueError:
        updates["automation_ical_sync_interval_hours"] = AUTOMATION_SETTING_DEFAULTS["automation_ical_sync_interval_hours"]
    days_raw = request.form.get("automation_workshop_balance_reminder_days_before", "").strip()
    updates["automation_workshop_balance_reminder_days_before"] = (
        str(int(days_raw)) if days_raw.isdigit() else AUTOMATION_SETTING_DEFAULTS["automation_workshop_balance_reminder_days_before"]
    )
    unanswered_hours_raw = request.form.get("automation_email_unanswered_hours", "").strip()
    try:
        updates["automation_email_unanswered_hours"] = str(max(1, float(unanswered_hours_raw)))
    except ValueError:
        updates["automation_email_unanswered_hours"] = AUTOMATION_SETTING_DEFAULTS["automation_email_unanswered_hours"]
    lookback_days_raw = request.form.get("automation_email_scan_lookback_days", "").strip()
    updates["automation_email_scan_lookback_days"] = (
        str(int(lookback_days_raw)) if lookback_days_raw.isdigit() else AUTOMATION_SETTING_DEFAULTS["automation_email_scan_lookback_days"]
    )
    stale_hours_raw = request.form.get("automation_stale_shift_hours", "").strip()
    try:
        updates["automation_stale_shift_hours"] = str(max(1, float(stale_hours_raw)))
    except ValueError:
        updates["automation_stale_shift_hours"] = AUTOMATION_SETTING_DEFAULTS["automation_stale_shift_hours"]

    for key, value in updates.items():
        conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", (value, key))
    log_audit(conn, "automation_settings_updated")
    conn.commit()
    conn.close()
    flash("Automation settings updated.", "success")
    return redirect(url_for("admin_automation"))


@app.route("/admin/automation/run/<job_name>", methods=["POST"])
@owner_required
def run_automation_job_now(job_name):
    conn = get_db()
    settings = get_automation_settings(conn)
    try:
        if job_name == "housekeeping":
            result = run_housekeeping_job(conn)
        elif job_name == "daily_digest":
            result = run_daily_digest_job(conn)
        elif job_name == "ical_sync":
            result = run_ical_sync_job(conn)
        elif job_name == "workshop_balance_reminder":
            try:
                days_before = int(settings["automation_workshop_balance_reminder_days_before"])
            except (TypeError, ValueError):
                days_before = 7
            result = run_workshop_balance_reminder_job(conn, days_before)
        elif job_name == "workshop_feedback_request":
            result = run_workshop_feedback_request_job(conn)
        elif job_name == "email_inbox_scan":
            result = run_email_inbox_scan_job(conn)
        elif job_name == "stale_shift_cleanup":
            try:
                stale_hours = float(settings["automation_stale_shift_hours"])
            except (TypeError, ValueError):
                stale_hours = 14
            result = run_stale_shift_cleanup_job(conn, stale_hours)
        else:
            conn.close()
            abort(404)
    except Exception as e:
        conn.close()
        flash(f"Job failed: {e}", "error")
        return redirect(url_for("admin_automation"))

    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("UPDATE automation_runs SET last_ran_at = ? WHERE job_name = ?", (now_iso, job_name))
    if cur.rowcount == 0:
        conn.execute("INSERT INTO automation_runs (job_name, last_ran_at) VALUES (?, ?)", (job_name, now_iso))
    log_audit(conn, "automation_job_run_manually", target=job_name)
    conn.commit()
    conn.close()
    flash(f"Ran now — {result}", "success")
    return redirect(url_for("admin_automation"))


@app.route("/admin/inbox-flags")
@owner_required
def admin_inbox_flags():
    conn = get_db()
    status_filter = request.args.get("status", "open")
    kind_filter = request.args.get("kind", "all")
    mailbox_filter = request.args.get("mailbox", "all")
    query = (
        "SELECT email_flags.*, users.name AS assigned_to_name, "
        "mailbox_routing.label AS mailbox_label FROM email_flags "
        "LEFT JOIN users ON users.id = email_flags.assigned_to_user_id "
        "LEFT JOIN mailbox_routing ON mailbox_routing.mailbox = email_flags.mailbox WHERE 1=1"
    )
    params = []
    if status_filter != "all":
        query += " AND email_flags.status = ?"
        params.append(status_filter)
    if mailbox_filter != "all":
        query += " AND email_flags.mailbox = ?"
        params.append(mailbox_filter)
    if kind_filter == "unanswered":
        query += " AND email_flags.unanswered = 1"
    elif kind_filter == "conflict":
        query += " AND (email_flags.price_conflict = 1 OR email_flags.availability_conflict = 1 OR email_flags.reply_price_conflict = 1 OR email_flags.reply_availability_conflict = 1)"
    query += " ORDER BY email_flags.received_at DESC"
    flags = conn.execute(query, params).fetchall()
    counts = conn.execute(
        """SELECT
             SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
             SUM(CASE WHEN status = 'open' AND unanswered = 1 THEN 1 ELSE 0 END) AS unanswered_count,
             SUM(CASE WHEN status = 'open' AND (price_conflict = 1 OR availability_conflict = 1
                 OR reply_price_conflict = 1 OR reply_availability_conflict = 1) THEN 1 ELSE 0 END) AS conflict_count
           FROM email_flags"""
    ).fetchone()
    employees = conn.execute("SELECT id, name FROM users WHERE status = 'active' ORDER BY name").fetchall()
    mailboxes = monitored_mailboxes(conn)
    # Open flags per inbox, so it's obvious at a glance which area is behind.
    per_mailbox = {
        r["mailbox"]: r["c"] for r in conn.execute(
            "SELECT mailbox, COUNT(*) AS c FROM email_flags WHERE status = 'open' AND mailbox IS NOT NULL GROUP BY mailbox"
        ).fetchall()
    }
    duplicates = cross_inbox_duplicates(conn)
    response_stats = inbox_response_stats(conn)
    guest_context = guest_context_for_emails(conn, [f["from_address"] for f in flags])
    conn.close()
    return render_template(
        "admin_inbox_flags.html", flags=flags, status_filter=status_filter, kind_filter=kind_filter,
        counts=counts, graph_enabled=graph_enabled(), employees=employees,
        mailboxes=mailboxes, mailbox_filter=mailbox_filter, per_mailbox=per_mailbox,
        duplicates=duplicates, response_stats=response_stats, guest_context=guest_context,
        mailbox=MS_GRAPH_MAILBOX,
    )


@app.route("/admin/inbox-flags/routing", methods=["POST"])
@owner_required
def update_mailbox_routing():
    """Set who owns each inbox. New flags in that inbox are assigned to them
    automatically, which is the point of having an inbox per area."""
    conn = get_db()
    for mb in conn.execute("SELECT id, mailbox FROM mailbox_routing").fetchall():
        raw = request.form.get(f"default_user_{mb['id']}", "").strip()
        label = request.form.get(f"label_{mb['id']}", "").strip() or None
        conn.execute(
            "UPDATE mailbox_routing SET default_user_id = ?, label = ?, route_to_on_shift = ?, "
            "escalate_hours = ? WHERE id = ?",
            (int(raw) if raw.isdigit() else None, label,
             1 if request.form.get(f"on_shift_{mb['id']}") else 0,
             _positive_float(request.form.get(f"escalate_{mb['id']}"), 48), mb["id"]),
        )
    conn.commit()
    conn.close()
    flash("Inbox routing updated.", "success")
    return redirect(url_for("admin_inbox_flags"))


@app.route("/admin/inbox-flags/status.json")
@owner_required
def admin_inbox_flags_status():
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS open_count, MAX(id) AS latest_id FROM email_flags WHERE status = 'open'"
    ).fetchone()
    conn.close()
    return jsonify(open_count=row["open_count"] or 0, latest_id=row["latest_id"] or 0)


@app.route("/admin/inbox-flags/<int:flag_id>/assign", methods=["POST"])
@owner_required
def assign_email_flag(flag_id):
    conn = get_db()
    flag = conn.execute("SELECT * FROM email_flags WHERE id = ?", (flag_id,)).fetchone()
    if not flag:
        conn.close()
        abort(404)
    user_id = request.form.get("user_id", "")
    assignee_id = int(user_id) if user_id.isdigit() else None
    conn.execute(
        "UPDATE email_flags SET assigned_to_user_id = ?, updated_at = ? WHERE id = ?",
        (assignee_id, datetime.now(timezone.utc).isoformat(), flag_id),
    )
    if assignee_id:
        send_notification(
            conn, assignee_id, "inbox_flag_assigned",
            f"You've been assigned an email: {flag['subject'] or '(no subject)'}",
            body=f"From {flag['from_name'] or flag['from_address'] or 'unknown sender'}",
            link="/admin/inbox-flags",
        )
    conn.commit()
    conn.close()
    flash("Assigned." if assignee_id else "Unassigned.", "success")
    return redirect(url_for("admin_inbox_flags", status=request.form.get("return_status", "open")))


@app.route("/admin/inbox-flags/<int:flag_id>/resolve", methods=["POST"])
@owner_required
def resolve_email_flag(flag_id):
    conn = get_db()
    user = current_user()
    conn.execute(
        "UPDATE email_flags SET status = 'resolved', resolved_at = ?, resolved_by_user_id = ?, updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user["id"], datetime.now(timezone.utc).isoformat(), flag_id),
    )
    conn.commit()
    conn.close()
    flash("Marked resolved.", "success")
    return redirect(url_for("admin_inbox_flags", status=request.form.get("return_status", "open")))


@app.route("/admin/inbox-flags/<int:flag_id>/dismiss", methods=["POST"])
@owner_required
def dismiss_email_flag(flag_id):
    conn = get_db()
    user = current_user()
    conn.execute(
        "UPDATE email_flags SET status = 'dismissed', resolved_at = ?, resolved_by_user_id = ?, updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user["id"], datetime.now(timezone.utc).isoformat(), flag_id),
    )
    conn.commit()
    conn.close()
    flash("Dismissed.", "success")
    return redirect(url_for("admin_inbox_flags", status=request.form.get("return_status", "open")))


# ---------------------------------------------------------------------------
# Outlook add-in — a small taskpane shown when reading an email, so opening
# a message from a past or prospective guest surfaces their room/dinner/
# workshop bookings without switching to the app. Works in Outlook desktop,
# Outlook on the web, and Outlook on mobile — "on the web" is what covers
# Safari, since Office Add-ins don't have a separate Safari-specific build.
# The manifest and taskpane are public URLs (Outlook's own client fetches
# them directly, unauthenticated, the same way a browser fetches any page)
# but the actual guest data endpoint is gated by GUEST_LOOKUP_TOKEN, entered
# once into the taskpane and stored in the user's own Outlook profile —
# never baked into the page source.
# ---------------------------------------------------------------------------

OUTLOOK_ADDIN_GUID = "31db9023-97b7-4519-8829-c0b1e3bed58e"


def guest_lookup_by_email(conn, email):
    rooms = conn.execute(
        """SELECT bookings.reference_code, rooms.name AS room_name, bookings.arrival_date,
                  bookings.departure_date, bookings.party_size, bookings.status,
                  bookings.payment_status, bookings.total_price, bookings.special_requests
           FROM bookings JOIN rooms ON rooms.id = bookings.room_id
           WHERE bookings.guest_email = ? ORDER BY bookings.arrival_date DESC LIMIT 10""",
        (email,),
    ).fetchall()
    restaurant = conn.execute(
        """SELECT reference_code, dinner_date, party_size, status, payment_status, dietary_notes
           FROM restaurant_bookings WHERE guest_email = ? ORDER BY dinner_date DESC LIMIT 10""",
        (email,),
    ).fetchall()
    workshops = conn.execute(
        """SELECT workshop_bookings.reference_code, workshops.title, workshop_sessions.start_date,
                  workshop_sessions.end_date, workshop_bookings.party_size, workshop_bookings.status
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.guest_email = ? ORDER BY workshop_sessions.start_date DESC LIMIT 10""",
        (email,),
    ).fetchall()
    return {
        "email": email,
        "rooms": [dict(r) for r in rooms],
        "restaurant": [dict(r) for r in restaurant],
        "workshops": [dict(r) for r in workshops],
    }


def claude_configured():
    return bool(ANTHROPIC_API_KEY)


def current_offerings_snapshot(conn):
    """A short, current snapshot of what's actually on offer right now --
    the same kind of ground truth guess_email_conflict cross-references,
    just not tied to matching one specific category. Deliberately narrow to
    guest-facing pricing/availability and policy text -- nothing from HR,
    financials, or the vault belongs in a prompt like this."""
    today = datetime.now(timezone.utc).date().isoformat()
    rooms = conn.execute(
        "SELECT name, price_per_night, max_occupancy FROM rooms WHERE active = 1 ORDER BY name"
    ).fetchall()
    workshop_sessions = conn.execute(
        """SELECT workshops.title, workshops.price_per_person, workshop_sessions.start_date, workshop_sessions.end_date
           FROM workshop_sessions JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshops.active = 1 AND workshop_sessions.start_date >= ?
           ORDER BY workshop_sessions.start_date LIMIT 10""",
        (today,),
    ).fetchall()
    restaurant_settings = get_restaurant_settings(conn)
    terms_row = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'").fetchone()
    return {
        "active_rooms": [dict(r) for r in rooms],
        "upcoming_workshop_sessions": [dict(w) for w in workshop_sessions],
        "restaurant": dict(restaurant_settings) if restaurant_settings else None,
        "terms_and_conditions": terms_row["value"] if terms_row else "",
    }


def gather_reply_context(conn, recipient_email):
    return {
        "guest_history": guest_lookup_by_email(conn, recipient_email) if recipient_email else None,
        "current_offerings": current_offerings_snapshot(conn),
    }


def draft_reply_with_claude(context, compose_text):
    """Drafts a reply grounded in the recipient's real booking history and
    today's real pricing/availability/terms, so the draft starts accurate
    rather than relying on the send-time gate to catch a stale figure
    after the fact. Returns None on any failure or safety refusal -- the
    add-in treats that the same as 'nothing to insert', never as license
    to fall back to a guess."""
    if not claude_configured():
        return None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = (
        "You draft email replies for the front desk of Chateau de Gudanes, a small "
        "chateau offering room stays, private dinners, and workshops. Write a warm, "
        "professional reply, in the guest's own language if the message you're "
        "replying to makes that evident, otherwise in English. Use ONLY the facts "
        "given below for prices, dates, and availability -- never invent or round a "
        "figure. If a question isn't covered by the facts given, say the team will "
        "confirm it rather than guessing. Output only the reply body text: no subject "
        "line, no email signature block, and do not repeat the quoted message back."
    )
    user_content = (
        f"Guest and business context (JSON):\n{json.dumps(context, default=str)}\n\n"
        "The compose window's current content, including the quoted message being "
        f"replied to:\n{compose_text}\n\nDraft the reply."
    )
    try:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        print(f"[claude] draft_reply request failed: {e}")
        return None
    if response.stop_reason == "refusal":
        return None
    text = "".join(block.text for block in response.content if block.type == "text")
    return text.strip() or None


@app.route("/api/guest-lookup", methods=["GET", "POST"])
@csrf.exempt
def api_guest_lookup():
    """No-login guest lookup for the Outlook add-in — same 404-not-403
    posture as /api/sync-ical and /api/owner-digest, so an unset/wrong
    token teaches a prober nothing. POST with the token in the body, not a
    GET query string — a GET here would put the token in plain text in
    every server/proxy access log line on every single lookup, effectively
    handing out a permanent, unrevoked read-any-guest credential to anyone
    who can ever read those logs.

    GET is accepted only so it can be 404'd by the same guard. Left to Flask,
    a GET here returned 405 Method Not Allowed, which confirms the endpoint
    exists to anyone poking at URLs — exactly what the 404 posture above is
    meant to avoid. The token is still read from the body only, so a GET can
    never carry a valid one.
    """
    supplied = request.form.get("token", "")
    if not GUEST_LOOKUP_TOKEN or not hmac.compare_digest(supplied, GUEST_LOOKUP_TOKEN):
        abort(404)
    conn = get_db()
    if rate_limited(conn, "guest_lookup", 60):
        conn.commit()
        conn.close()
        return jsonify(error="too many requests"), 429
    conn.commit()
    email = request.form.get("email", "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        conn.close()
        return jsonify(error="invalid email"), 400
    result = guest_lookup_by_email(conn, email)
    conn.close()
    return jsonify(result)


# GET is accepted only so the token guard can 404 it — a bare 405 from
# Flask would confirm the endpoint exists, which is what the 404
# posture below is meant to avoid. The token is read from the body
# only, so a GET can never carry a valid one.
@app.route("/api/check-send-conflict", methods=["GET", "POST"])
@csrf.exempt
def api_check_send_conflict():
    """Send-time safety check for the Outlook add-in's Smart Alerts hook —
    same 404-not-403 posture as /api/guest-lookup. Runs the same
    price/date conflict guesser already used on inbound mail against the
    draft a staff member is about to send, so a stale quote gets caught
    before it leaves rather than a minute or two later via the Sent Items
    scan. Rate limit is generous (this fires on every outgoing email from
    a shared mailbox, not just once from an anonymous visitor) and any
    failure here must never be the reason a legitimate email can't send —
    the add-in side is responsible for treating an error the same as 'no
    conflict found', not as 'block'."""
    supplied = request.form.get("token", "")
    if not GUEST_LOOKUP_TOKEN or not hmac.compare_digest(supplied, GUEST_LOOKUP_TOKEN):
        abort(404)
    conn = get_db()
    if rate_limited(conn, "check_send_conflict", 300):
        conn.commit()
        conn.close()
        return jsonify(error="too many requests"), 429
    conn.commit()
    recipient = request.form.get("recipient_email", "").strip().lower()
    subject = request.form.get("subject", "")
    body_text = request.form.get("body", "")
    conflict = guess_email_conflict(conn, recipient, subject, body_text, datetime.now(timezone.utc).isoformat())
    conn.close()
    if not conflict or not (conflict["price_conflict"] or conflict["availability_conflict"]):
        return jsonify(conflict=False)
    return jsonify(
        conflict=True,
        note=conflict["note"] or "This message may not match current pricing or availability.",
        category=conflict["category"], extracted_price=conflict["extracted_price"],
        computed_price=conflict["computed_price"],
    )


# GET is accepted only so the token guard can 404 it — a bare 405 from
# Flask would confirm the endpoint exists, which is what the 404
# posture below is meant to avoid. The token is read from the body
# only, so a GET can never carry a valid one.
@app.route("/api/draft-reply", methods=["GET", "POST"])
@csrf.exempt
def api_draft_reply():
    """Compose-time reply-drafting assist for the Outlook add-in — same
    404-not-403 token posture as the other add-in endpoints. Manually
    triggered by a person clicking a button (unlike the send-time check,
    which fires on every email), so a normal per-hour rate limit is enough
    here. Returns a draft grounded in real guest history and real current
    pricing/terms; the add-in only ever offers it as something to review
    and edit, never something that sends itself."""
    supplied = request.form.get("token", "")
    if not GUEST_LOOKUP_TOKEN or not hmac.compare_digest(supplied, GUEST_LOOKUP_TOKEN):
        abort(404)
    conn = get_db()
    if rate_limited(conn, "draft_reply", 60):
        conn.commit()
        conn.close()
        return jsonify(error="too many requests"), 429
    conn.commit()
    if not claude_configured():
        conn.close()
        return jsonify(error="not configured"), 503
    recipient = request.form.get("recipient_email", "").strip().lower()
    compose_text = request.form.get("body", "")
    context = gather_reply_context(conn, recipient)
    conn.close()
    draft = draft_reply_with_claude(context, compose_text)
    if draft is None:
        return jsonify(error="could not draft a reply"), 502
    return jsonify(draft=draft)


@app.route("/outlook-addin/manifest.xml")
def outlook_addin_manifest():
    icon_url = url_for("static", filename="icon-192.png", _external=True)
    taskpane_url = url_for("outlook_addin_taskpane", _external=True)
    xml = render_template(
        "outlook_addin_manifest.xml", guid=OUTLOOK_ADDIN_GUID, icon_url=icon_url,
        taskpane_url=taskpane_url, support_url=url_for("book_rooms", _external=True),
        app_domain=request.host_url.rstrip("/"),
        launchevent_html_url=url_for("outlook_addin_launchevent_html", _external=True),
        launchevent_js_url=url_for("outlook_addin_launchevent_js", _external=True),
        compose_taskpane_url=url_for("outlook_addin_compose", _external=True),
    )
    return app.response_class(xml, mimetype="application/xml")


@app.route("/outlook-addin/taskpane")
def outlook_addin_taskpane():
    return render_template("outlook_addin_taskpane.html", lookup_url=url_for("api_guest_lookup", _external=True))


@app.route("/outlook-addin/compose")
def outlook_addin_compose():
    return render_template("outlook_addin_compose.html", draft_url=url_for("api_draft_reply", _external=True))


@app.route("/outlook-addin/launchevent.html")
def outlook_addin_launchevent_html():
    return render_template(
        "outlook_addin_launchevent.html",
        launchevent_js_url=url_for("outlook_addin_launchevent_js", _external=True),
    )


@app.route("/outlook-addin/launchevent.js")
def outlook_addin_launchevent_js():
    js = render_template(
        "outlook_addin_launchevent.js",
        check_url=url_for("api_check_send_conflict", _external=True),
    )
    return app.response_class(js, mimetype="application/javascript")


@app.route("/admin/outlook-addin")
@owner_required
def admin_outlook_addin():
    return render_template(
        "admin_outlook_addin.html", manifest_url=url_for("outlook_addin_manifest", _external=True),
        token_configured=bool(GUEST_LOOKUP_TOKEN),
    )


if __name__ == "__main__":
    init_db()
    # Running this file directly is the local-development path (production
    # serves the `app` object through gunicorn and never reaches here), so
    # pick up template edits without needing a restart. Jinja otherwise
    # caches compiled templates for the process lifetime whenever debug is
    # off, which silently serves the old page after every edit. This only
    # re-stats template files; it does NOT enable the debugger.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    # Without FLASK_SECRET_KEY the key above is regenerated per process, so
    # every local restart silently signs everyone out mid-task. Persist one
    # for development only: this branch never runs under gunicorn, and the
    # file is gitignored, so production still depends on the real env var.
    if not os.environ.get("FLASK_SECRET_KEY"):
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dev_secret_key")
        try:
            if os.path.exists(key_path):
                with open(key_path) as f:
                    app.secret_key = f.read().strip()
            else:
                app.secret_key = secrets.token_hex(32)
                with open(key_path, "w") as f:
                    f.write(app.secret_key)
                os.chmod(key_path, 0o600)
        except OSError:
            pass  # unwritable disk: fall back to the per-process key
    # Auto-restart on Python edits, WITHOUT the interactive debugger.
    # Templates already hot-reload above, but a change to app.py needed a
    # manual restart — and because base.html is shared, a newly-added route
    # meant EVERY page 500'd with a BuildError until you remembered to do it.
    # use_reloader is independent of debug: this gives the restart without
    # exposing the werkzeug console. Opt out with FLASK_NO_RELOAD=1.
    use_reloader = os.environ.get("FLASK_NO_RELOAD", "0") != "1"

    # Werkzeug's reloader re-executes this module in a child process with
    # WERKZEUG_RUN_MAIN set — starting the thread only there (or when the
    # reloader isn't in play at all) keeps a single automation loop per
    # running server instead of one per reloader generation.
    if not use_reloader or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=automation_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG_MODE, use_reloader=use_reloader, threaded=True)
