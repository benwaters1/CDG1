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
Set these environment variables: SMTP_HOST, SMTP_PORT (587 is typical),
SMTP_USERNAME, SMTP_PASSWORD, and optionally SMTP_FROM. Any real mailbox
works. See DEPLOY.md for where these go on Railway.

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
from zoneinfo import ZoneInfo
from calendar import monthrange

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_from_directory, abort, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import stripe
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import webpush, WebPushException

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "gudanes_hr.db")
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
AUTOMATION_SETTING_DEFAULTS = {
    "automation_housekeeping_enabled": "1",
    "automation_daily_digest_enabled": "1",
    "automation_ical_sync_enabled": "1",
    "automation_ical_sync_interval_hours": "6",
    "automation_workshop_balance_reminder_enabled": "1",
    "automation_workshop_balance_reminder_days_before": "7",
    "automation_waitlist_autonotify_enabled": "1",
}
EMPLOYER_LEGAL_NAME = "SCI Torrents"
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
- If the château declines or cancels your booking, any payment already
  taken is refunded automatically.
- If you cancel a booking yourself, this does not automatically refund
  any payment already taken — contact the château directly to arrange it.
- [Owner: add your actual cancellation window / partial-refund policy
  here, e.g. "Cancellations more than 14 days before arrival receive a
  full refund; within 14 days, no refund."]

4. Your Information
We collect your name, email, phone number, and any notes you provide in
order to process your booking. It is stored securely and is not sold or
shared with third parties, other than the payment processor (Stripe)
where online payment is used.

5. Contact
For any question about a booking, contact the château directly.

Last updated: [add a date once this is finalized]"""

# Email — unset until you add real SMTP credentials as environment
# variables. Until then, every send_email() call is a no-op that logs to
# the console instead of failing; the on-screen reference code/link stays
# the guaranteed way a guest can find their booking.
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USERNAME

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

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(ROOM_PHOTO_DIR, exist_ok=True)

app = Flask(__name__)
# IMPORTANT: this secret key is regenerated every time the app starts unless
# you set a real one via the FLASK_SECRET_KEY environment variable. Set that
# in your real deployment so logged-in sessions survive restarts. See
# DEPLOY.md for exactly how.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
DEBUG_MODE = os.environ.get("FLASK_DEBUG", "1") == "1"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure requires HTTPS to even set the cookie — fine in the real deployment
# (always behind Nginx + Certbot per DEPLOY.md) but would silently break
# login during local dev over plain http://, so it only turns on outside
# debug mode.
app.config["SESSION_COOKIE_SECURE"] = not DEBUG_MODE
# Idle timeout, not a fixed session length: Flask refreshes the cookie's
# expiry on every request by default, so this logs someone out only after
# this many hours with no activity — not mid-shift just because it's been
# a while since they logged in. Matters here since the session can reach
# bank details and the password vault on a front-desk computer other staff
# might walk up to.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)


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
            handled_at TEXT
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

        CREATE TABLE IF NOT EXISTS guest_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL,
            guest_name TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            submitted_at TEXT NOT NULL
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

        CREATE TABLE IF NOT EXISTS guests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            arrival_date TEXT,
            departure_date TEXT,
            party_size INTEGER,
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
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done')),
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
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

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
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_usage_one_open_per_vehicle ON vehicle_usage(vehicle_id) WHERE checked_in_at IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_breaks_one_open_per_entry ON breaks(time_entry_id) WHERE end_at IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_time_entries_one_open_per_user ON time_entries(user_id) WHERE clock_out_at IS NULL",
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
    totals = {}
    for r in rows:
        bucket = totals.setdefault(r["user_id"], {"name": r["employee_name"], "hours": 0.0})
        bucket["hours"] += net_hours(r, conn)
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
    results = []
    for r in rows:
        clock_in_dt = parse_datetime_iso(r["clock_in_at"])
        if not clock_in_dt:
            continue
        local_in = clock_in_dt.astimezone(LOCAL_TZ)
        hours = net_hours(r, conn)
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
    revenue = room_revenue + restaurant_revenue + workshop_revenue
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

    entries = conn.execute(
        """SELECT time_entries.*, users.pay_rate, users.pay_type FROM time_entries
           JOIN users ON users.id = time_entries.user_id
           WHERE time_entries.clock_out_at IS NOT NULL
           AND time_entries.clock_in_at >= ? AND time_entries.clock_in_at < ?""",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchall()
    labour_cost = 0.0
    any_labour_estimate = False
    for e in entries:
        cost = estimated_hourly_cost(net_hours(e, conn), e["pay_rate"], e["pay_type"])
        if cost is not None:
            labour_cost += cost
            any_labour_estimate = True
    labour_cost = round(labour_cost, 2) if any_labour_estimate else None

    expenses_total = round(staff_expenses + supplier_expenses, 2)
    net = round(revenue - expenses_total - (labour_cost or 0), 2)
    return {
        "month": month_start, "revenue": round(revenue, 2), "room_revenue": round(room_revenue, 2),
        "restaurant_revenue": round(restaurant_revenue, 2), "workshop_revenue": round(workshop_revenue, 2),
        "staff_expenses": round(staff_expenses, 2),
        "supplier_expenses": round(supplier_expenses, 2), "expenses_total": expenses_total,
        "labour_cost": labour_cost, "net": net,
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


def parse_datetime_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def guest_with_status(g, today):
    arrival = parse_date(g["arrival_date"])
    departure = parse_date(g["departure_date"])
    if departure and departure < today:
        status, label = "past", "Past stay"
    elif arrival and arrival > today:
        status, label = "upcoming", "Upcoming"
    else:
        status, label = "current", "In residence"
    return {
        "id": g["id"], "name": g["name"], "arrival_date": g["arrival_date"],
        "departure_date": g["departure_date"], "party_size": g["party_size"],
        "notes": g["notes"], "stay_status": status, "stay_status_label": label,
    }


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
           WHERE due_date IS NULL AND tasks.status = 'open' ORDER BY users.name"""
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
           WHERE (due_date >= ? AND due_date < ?) OR (due_date IS NULL AND tasks.status = 'open')
           ORDER BY due_date IS NULL, due_date, tasks.priority""",
        (range_start.isoformat(), range_end.isoformat()),
    ).fetchall()


def build_overview(conn, view, anchor):
    """The unified ops feed — bookings and staff tasks in one filterable
    list for the given date window. Lane/guest/origin/employee filters run
    client-side against data-attributes on each row (instant, no reload);
    only the date range itself is a server round-trip, matching the
    tasks/calendar pages elsewhere in the app."""
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

    owner_row = conn.execute("SELECT id, name FROM users WHERE role = 'owner' LIMIT 1").fetchone()
    owner_id = owner_row["id"] if owner_row else None
    owner_name = owner_row["name"] if owner_row else None
    employees = conn.execute("SELECT * FROM users WHERE role = 'employee' ORDER BY name").fetchall()

    rows = []
    for t in _overview_task_range(conn, range_start, range_end):
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
            "title": t["title"],
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
        (range_start.isoformat(), range_end.isoformat(), range_start.isoformat(), range_end.isoformat()),
    ).fetchall()
    for b in bookings:
        booking_link = url_for("admin_bookings", q=b["reference_code"])
        if range_start.isoformat() <= b["arrival_date"] < range_end.isoformat():
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
                "detail": arrival_detail,
                "assignee_id": None, "assignee_name": None, "id": b["id"], "link": booking_link,
            })
        if range_start.isoformat() <= b["departure_date"] < range_end.isoformat():
            rows.append({
                "kind": "booking", "lane": "booking", "is_guest": True, "origin": "booking",
                "scheduled": True, "status": b["status"], "acknowledgment_status": None,
                "priority": None, "repeat_weekly": 0, "date": b["departure_date"],
                "title": f"{b['guest_name']} departs — {b['room_name']}",
                "detail": f"Party of {b['party_size']}",
                "assignee_id": None, "assignee_name": None, "id": b["id"], "link": booking_link,
            })

    dinners = conn.execute(
        """SELECT * FROM restaurant_bookings WHERE status IN ('pending', 'confirmed')
           AND dinner_date >= ? AND dinner_date < ?""",
        (range_start.isoformat(), range_end.isoformat()),
    ).fetchall()
    for d in dinners:
        rows.append({
            "kind": "dinner", "lane": "restaurant", "is_guest": True, "origin": "booking",
            "scheduled": True, "status": d["status"], "acknowledgment_status": None,
            "priority": None, "repeat_weekly": 0, "date": d["dinner_date"],
            "title": f"Dinner — {d['guest_name']}, party of {d['party_size']}",
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
        (range_start.isoformat(), range_end.isoformat()),
    ).fetchall()
    for r in workshop_regs:
        rows.append({
            "kind": "workshop", "lane": "workshop", "is_guest": True, "origin": "booking",
            "scheduled": True, "status": r["status"], "acknowledgment_status": None,
            "priority": None, "repeat_weekly": 0, "date": r["start_date"],
            "title": f"{r['title']} — {r['guest_name']}, party of {r['party_size']}",
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

def email_enabled():
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)


def send_email(to_address, subject, body, ics_content=None, ics_filename=None):
    if not to_address:
        return False
    if not email_enabled():
        print(f"[email skipped — SMTP not configured] To: {to_address} | Subject: {subject}")
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
                    stripe_session_id=None, stripe_payment_intent_id=None):
    nights = (departure - arrival).days
    room_total = room["price_per_night"] * nights if room["price_per_night"] else 0
    extras_total = sum(e["price"] for e in chosen_extras)
    total_price = (room_total + extras_total) or None
    extras_summary = ", ".join(f"{e['name']} (€{e['price']:.2f})" for e in chosen_extras) or None

    reference_code = make_reference_code()
    manage_token = secrets.token_urlsafe(24)
    conn.execute(
        """INSERT INTO bookings
           (room_id, reference_code, manage_token, guest_name, guest_email, guest_phone,
            arrival_date, departure_date, party_size, special_requests, total_price,
            extras_summary, payment_status, stripe_session_id, stripe_payment_intent_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room["id"], reference_code, manage_token, guest_name, guest_email, guest_phone,
         arrival.isoformat(), departure.isoformat(), party_size, special_requests or None,
         total_price, extras_summary, payment_status, stripe_session_id, stripe_payment_intent_id,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    checkin_url = url_for("guest_checkin", manage_token=manage_token, _external=True)
    detail_lines = [
        f"Arrival: {format_date_human(arrival.isoformat())}",
        f"Departure: {format_date_human(departure.isoformat())}",
        f"Party size: {party_size}",
    ]
    if extras_summary:
        detail_lines.append(f"Add-ons: {extras_summary}")
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
    meta = session["metadata"]
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
        stripe_session_id=session["id"], stripe_payment_intent_id=session.get("payment_intent"),
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


def refund_booking(conn, booking):
    if booking["payment_status"] != "paid":
        return False, "This booking was never marked paid."
    if not booking["stripe_payment_intent_id"] or not stripe_enabled():
        return False, "No Stripe payment on record for this booking."
    try:
        stripe.Refund.create(payment_intent=booking["stripe_payment_intent_id"])
    except Exception as e:
        return False, str(e)
    conn.execute("UPDATE bookings SET payment_status = 'refunded' WHERE id = ?", (booking["id"],))
    conn.commit()
    return True, None


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
    if user:
        conn = get_db()
        unread_notifications_count = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user["id"],)
        ).fetchone()[0]
        vapid_public_key = get_vapid_public_key(conn)
        if user["role"] == "owner":
            pending_approvals_count = conn.execute(
                "SELECT (SELECT COUNT(*) FROM leave_requests WHERE status = 'pending') "
                "+ (SELECT COUNT(*) FROM expenses WHERE status = 'pending')"
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
            clock_in(conn, user["id"])
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
    user = current_user()
    if user:
        conn = get_db()
        clock_out(conn, user["id"])
        conn.close()
    session.clear()
    return redirect(url_for("login"))


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

    who_is_here = [
        g for g in [guest_with_status(g, today) for g in conn.execute("SELECT * FROM guests").fetchall()]
        if g["stay_status"] == "current"
    ]
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
        all_guests = [guest_with_status(g, today) for g in conn.execute("SELECT * FROM guests").fetchall()]
        stats["guests_current"] = sum(1 for g in all_guests if g["stay_status"] == "current")
        stats["guests_upcoming"] = sum(1 for g in all_guests if g["stay_status"] == "upcoming")
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
        my_week_hours = round(sum(net_hours(r, conn) for r in my_hours_entries if r["clock_in_at"] >= week_ago_iso), 2)
        my_month_hours = round(sum(net_hours(r, conn) for r in my_hours_entries), 2)
        # Today's view: what's due today, overdue, or has no date yet — never
        # a future date, so this never turns into a forward-looking calendar.
        my_tasks = conn.execute(
            """SELECT * FROM tasks WHERE assigned_to_user_id = ? AND status = 'open'
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

    # Same idea for a workshop instructor who's on staff — their own view
    # into upcoming sessions they're teaching, without owner access.
    my_upcoming_sessions = []
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
        my_tonights_dinners=my_tonights_dinners, my_upcoming_sessions=my_upcoming_sessions,
    )


@app.route("/search")
@owner_required
def search():
    q = request.args.get("q", "").strip()
    results = {"guests": [], "employees": [], "tasks": [], "rooms": [], "vendors": [],
               "recurring_costs": [], "company_info": [], "waitlist": [], "vehicles": [],
               "restaurant_bookings": [], "workshop_bookings": [], "social_posts": []}
    if q:
        needle = f"%{q}%"
        conn = get_db()
        results["guests"] = conn.execute(
            "SELECT * FROM guests WHERE name LIKE ? OR notes LIKE ? ORDER BY name LIMIT 20",
            (needle, needle),
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
    if user["role"] == "owner":
        status_filter = request.args.get("status", "").strip()
        q = request.args.get("q", "").strip()

        employees = conn.execute(
            "SELECT * FROM users WHERE role = 'employee' ORDER BY status, name"
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
        )
    else:
        conn.close()
        return redirect(url_for("profile", user_id=user["id"]))


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
                    annual_leave_days)
                   VALUES (?, ?, 'employee', ?, ?, ?, ?, 'active', ?, ?, ?, 0, ?, ?, ?)""",
                (email, placeholder_hash, name, job_role,
                 phone, start_date, pay_rate, pay_type, notes, invite_token,
                 datetime.now(timezone.utc).isoformat(), annual_leave_days),
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
    week_total_hours = sum(
        net_hours(r, conn)
        for r in conn.execute(
            "SELECT * FROM time_entries WHERE user_id = ? AND clock_in_at >= ? AND clock_out_at IS NOT NULL",
            (user_id, week_ago),
        ).fetchall()
    )
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
            "SELECT COUNT(*) AS c FROM tasks WHERE assigned_to_user_id = ? AND status = 'open'",
            (user_id,),
        ).fetchone()["c"],
    }
    onboarding_items = []
    offboarding_items = []
    check_in_notes = []
    equipment_items = []
    pay_rate_history = []
    if user["role"] == "owner":
        onboarding_items = conn.execute(
            "SELECT * FROM onboarding_items WHERE user_id = ? ORDER BY sort_order, id", (user_id,)
        ).fetchall()
        offboarding_items = conn.execute(
            "SELECT * FROM offboarding_items WHERE user_id = ? ORDER BY sort_order, id", (user_id,)
        ).fetchall()
        check_in_notes = conn.execute(
            "SELECT * FROM check_in_notes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        equipment_items = conn.execute(
            "SELECT * FROM equipment_items WHERE user_id = ? ORDER BY (returned_at IS NOT NULL), issued_at DESC", (user_id,)
        ).fetchall()
        pay_rate_history = conn.execute(
            """SELECT pay_rate_history.*, users.name AS changed_by_name FROM pay_rate_history
               LEFT JOIN users ON users.id = pay_rate_history.changed_by_user_id
               WHERE pay_rate_history.user_id = ? ORDER BY changed_at DESC""",
            (user_id,),
        ).fetchall()
    leave = leave_balance(conn, user_id, person["annual_leave_days"]) if person["role"] == "employee" else None
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

        conn.execute(
            """UPDATE users SET name=?, job_role=?, phone=?, start_date=?,
               status=?, pay_rate=?, pay_type=?, notes=?, skills=?,
               emergency_contact_name=?, emergency_contact_phone=?, emergency_contact_relationship=?,
               annual_leave_days=?, reason_for_leaving=?
               WHERE id=?""",
            (name, job_role, phone, start_date, status, pay_rate, pay_type, notes, skills,
             emergency_contact_name, emergency_contact_phone, emergency_contact_relationship,
             annual_leave_days, reason_for_leaving or None, user_id),
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
    new_status = "open" if note["status"] == "handled" else "handled"
    conn.execute(
        "UPDATE hr_notes SET status = ?, handled_at = ? WHERE id = ?",
        (new_status, datetime.now(timezone.utc).isoformat() if new_status == "handled" else None, note_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_hr_notes"))


# ---------------------------------------------------------------------------
# Timesheets — built entirely from time_entries rows created by clock_in()/
# clock_out() at login/logout. Nothing here writes a time_entries row itself;
# this is a read-only report over that log.
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
    totals_by_user = {}
    for e in entries:
        hrs = net_hours(e, conn) if e["clock_out_at"] else 0.0
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
    conn.close()

    rows = [
        {
            "employee": e["user_name"],
            "clock_in_at": e["clock_in_at"],
            "clock_out_at": e["clock_out_at"] or "",
            "hours": net_hours(e) if e["clock_out_at"] else "",
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
    totals_by_user = {}
    for e in entries:
        hrs = net_hours(e, conn) if e["clock_out_at"] else 0.0
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
    if clock_in_time:
        conn.execute(
            "UPDATE time_entries SET clock_in_at = ? WHERE id = ?",
            (local_time_input_to_utc_iso(entry["clock_in_at"], clock_in_time), entry["id"]),
        )
    if clock_out_time:
        base_for_out = entry["clock_out_at"] or entry["clock_in_at"]
        conn.execute(
            "UPDATE time_entries SET clock_out_at = ?, auto_closed = 0 WHERE id = ?",
            (local_time_input_to_utc_iso(base_for_out, clock_out_time), entry["id"]),
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
        clock_in(conn, person["id"])
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
    guests_here = [
        g for g in [guest_with_status(g, today) for g in conn.execute("SELECT * FROM guests").fetchall()]
        if g["stay_status"] == "current"
    ]
    guest_count = sum(g["party_size"] or 0 for g in guests_here)
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
    q = request.args.get("q", "").strip()
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM guests ORDER BY (arrival_date IS NULL), arrival_date, name"
    ).fetchall()
    conn.close()
    today = datetime.now(timezone.utc).date()
    all_guests = [guest_with_status(g, today) for g in rows]
    if q:
        needle = q.lower()
        all_guests = [
            g for g in all_guests
            if needle in g["name"].lower() or needle in (g["notes"] or "").lower()
        ]
    current_upcoming = [g for g in all_guests if g["stay_status"] != "past"]
    past = [g for g in all_guests if g["stay_status"] == "past"]
    return render_template("guests.html", current_upcoming=current_upcoming, past=past, q=q)


@app.route("/guests/new", methods=["GET", "POST"])
@owner_required
def new_guest():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        arrival_date = request.form.get("arrival_date", "").strip()
        departure_date = request.form.get("departure_date", "").strip()
        party_size = request.form.get("party_size", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name:
            flash("Guest name is required.", "error")
            return render_template("guest_form.html", guest=None)

        conn = get_db()
        conn.execute(
            """INSERT INTO guests (name, arrival_date, departure_date, party_size, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, arrival_date or None, departure_date or None,
             int(party_size) if party_size.isdigit() else None, notes,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        flash(f"{name} added to the guest list.", "success")
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
        arrival_date = request.form.get("arrival_date", "").strip()
        departure_date = request.form.get("departure_date", "").strip()
        party_size = request.form.get("party_size", "").strip()
        notes = request.form.get("notes", "").strip()

        conn.execute(
            """UPDATE guests SET name=?, arrival_date=?, departure_date=?, party_size=?, notes=?
               WHERE id=?""",
            (name, arrival_date or None, departure_date or None,
             int(party_size) if party_size.isdigit() else None, notes, guest_id),
        )
        conn.commit()
        conn.close()
        flash("Guest updated.", "success")
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
        status_filter=status_filter, q=q,
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
    conn.close()
    if submitter:
        send_email(
            submitter["email"],
            f"Your expense has been {status}",
            f"Hi {submitter['name'].split(' ')[0]},\n\n"
            f"Your {row['kind'].replace('_', ' ')} — {row['description']} (€{row['amount']:.2f}) — has been {status}."
            + (f"\n\nNote: {note}" if note else "")
            + f"\n\n— Château de Gudanes",
        )
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
    searched = bool(arrival and departure and departure > arrival)
    if searched:
        for room in rooms:
            ok, _ = is_range_available(conn, room["id"], arrival, departure)
            availability[room["id"]] = ok
    nothing_available = searched and rooms and not any(availability.values())

    grid = guest_availability_grid(conn, rooms, request.args.get("month", ""))
    conn.close()
    return render_template(
        "book_rooms.html", rooms=rooms, arrival=arrival_raw, departure=departure_raw,
        availability=availability, searched=searched, nothing_available=nothing_available,
        prefill_name=request.args.get("name", ""), prefill_email=request.args.get("email", ""),
        prefill_phone=request.args.get("phone", ""), prefill_party_size=request.args.get("party_size", ""),
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


@app.route("/book/<int:room_id>", methods=["GET", "POST"])
def book_room(room_id):
    conn = get_db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ? AND active = 1", (room_id,)).fetchone()
    if not room:
        conn.close()
        abort(404)
    extras = conn.execute("SELECT * FROM extras WHERE active = 1 ORDER BY sort_order, name").fetchall()

    if request.method == "POST":
        if rate_limited(conn, "book_room", BOOKING_RATE_LIMIT_PER_HOUR):
            conn.commit()
            conn.close()
            flash("Too many booking attempts from this connection — please try again in a bit, or contact the château directly.", "error")
            return render_template("book_room.html", room=room, arrival="", departure="", extras=extras, stripe_enabled=stripe_enabled())
        arrival_raw = request.form.get("arrival_date", "").strip()
        departure_raw = request.form.get("departure_date", "").strip()
        guest_name = request.form.get("guest_name", "").strip()
        guest_email = request.form.get("guest_email", "").strip().lower()
        guest_phone = request.form.get("guest_phone", "").strip()
        party_size_raw = request.form.get("party_size", "").strip()
        special_requests = request.form.get("special_requests", "").strip()
        selected_extra_ids = {int(i) for i in request.form.getlist("extras") if i.isdigit()}
        agreed_to_terms = request.form.get("agree_terms") == "on"

        arrival, departure = parse_date(arrival_raw), parse_date(departure_raw)
        party_size = int(party_size_raw) if party_size_raw.isdigit() else None

        error = None
        if not guest_name or not guest_email:
            error = "Name and email are required."
        elif not EMAIL_RE.match(guest_email):
            error = "Enter a valid email address."
        elif not arrival or not departure:
            error = "Choose valid arrival and departure dates."
        elif not party_size or party_size < 1:
            error = "Party size is required."
        elif party_size > room["max_occupancy"]:
            error = f"This room sleeps up to {room['max_occupancy']}."
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
            return render_template("book_room.html", room=room, arrival=arrival_raw, departure=departure_raw, extras=extras, stripe_enabled=stripe_enabled())

        nights = (departure - arrival).days
        chosen_extras = [e for e in extras if e["id"] in selected_extra_ids]
        room_total = room["price_per_night"] * nights if room["price_per_night"] else 0
        grand_total = room_total + sum(e["price"] for e in chosen_extras)

        if stripe_enabled() and grand_total > 0:
            line_items = []
            if room["price_per_night"]:
                line_items.append({
                    "price_data": {
                        "currency": "eur",
                        "product_data": {"name": f"{room['name']} — {nights} night{'s' if nights != 1 else ''}"},
                        "unit_amount": int(round(room["price_per_night"] * 100)),
                    },
                    "quantity": nights,
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
                    cancel_url=url_for("book_room", room_id=room_id, _external=True),
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
                    },
                )
            except Exception as e:
                flash(f"Payment setup failed ({e}). Please try again.", "error")
                conn.commit()  # persist the rate-limit log entry even when Stripe setup fails
                conn.close()
                return render_template("book_room.html", room=room, arrival=arrival_raw, departure=departure_raw, extras=extras, stripe_enabled=stripe_enabled())
            conn.commit()
            conn.close()
            return redirect(checkout_session.url, code=303)

        _, manage_token = create_booking(
            conn, room, guest_name, guest_email, guest_phone, arrival, departure,
            party_size, special_requests, chosen_extras,
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
        prefill_phone=prefill_phone, prefill_party_size=prefill_party_size,
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

    if session.get("payment_status") != "paid":
        conn.close()
        flash("That payment wasn't completed, so no booking was made.", "error")
        return redirect(url_for("book_rooms"))

    manage_token = create_booking_from_stripe_session(conn, session)
    conn.close()
    if not manage_token:
        flash("Payment went through, but we couldn't create the booking automatically — contact the château directly with your payment reference.", "error")
        return redirect(url_for("book_rooms"))
    return redirect(url_for("booking_confirmation", manage_token=manage_token))


@app.route("/book/stripe-cancel")
def stripe_cancel():
    flash("Payment was cancelled — no booking was made.", "error")
    return redirect(url_for("book_rooms"))


@app.route("/webhooks/stripe", methods=["POST"])
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
        meta = session.get("metadata") or {}
        conn = get_db()
        if meta.get("kind") in ("workshop_deposit", "workshop_balance"):
            if session.get("payment_status") == "paid":
                mark_workshop_payment_paid(conn, session)
        elif meta.get("kind") == "restaurant":
            existing = conn.execute(
                "SELECT id FROM restaurant_bookings WHERE stripe_session_id = ?", (session["id"],)
            ).fetchone()
            if not existing and session.get("payment_status") == "paid":
                create_restaurant_booking_from_stripe_session(conn, session)
        else:
            existing = conn.execute(
                "SELECT id FROM bookings WHERE stripe_session_id = ?", (session["id"],)
            ).fetchone()
            if not existing and session.get("payment_status") == "paid":
                create_booking_from_stripe_session(conn, session)
        conn.close()

    return jsonify(received=True), 200


@app.route("/book/manage", methods=["GET", "POST"])
def find_booking():
    if request.method == "POST":
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
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
            "SELECT id FROM tasks WHERE booking_id = ? AND title = 'Airport transfer' AND status = 'open'",
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
    conn.close()
    return render_template(
        "manage_booking.html", booking=booking, guest_requests=guest_requests,
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


def refund_restaurant_booking(conn, booking):
    if booking["payment_status"] != "paid":
        return False, "This reservation was never marked paid."
    if not booking["stripe_payment_intent_id"] or not stripe_enabled():
        return False, "No Stripe payment on record for this reservation."
    try:
        stripe.Refund.create(payment_intent=booking["stripe_payment_intent_id"])
    except Exception as e:
        return False, str(e)
    conn.execute("UPDATE restaurant_bookings SET payment_status = 'refunded' WHERE id = ?", (booking["id"],))
    conn.commit()
    return True, None


def create_restaurant_booking(conn, guest_name, guest_email, guest_phone, dinner_date, party_size,
                               dietary_notes, booking_id=None, payment_status="unpaid",
                               stripe_session_id=None, stripe_payment_intent_id=None):
    settings = get_restaurant_settings(conn)
    reference_code = make_restaurant_reference_code()
    manage_token = secrets.token_urlsafe(24)
    total_price = (settings["price_per_person"] * party_size) if settings and settings["price_per_person"] else None
    conn.execute(
        """INSERT INTO restaurant_bookings
           (reference_code, manage_token, guest_name, guest_email, guest_phone, party_size,
            dinner_date, dietary_notes, total_price, booking_id, payment_status,
            stripe_session_id, stripe_payment_intent_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (reference_code, manage_token, guest_name, guest_email, guest_phone, party_size,
         dinner_date, dietary_notes or None, total_price, booking_id, payment_status,
         stripe_session_id, stripe_payment_intent_id, datetime.now(timezone.utc).isoformat()),
    )
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
    meta = session["metadata"]
    dinner_date = meta["dinner_date"]
    party_size = int(meta["party_size"])
    remaining = restaurant_remaining_capacity(conn, dinner_date)
    reference_code, manage_token = create_restaurant_booking(
        conn, meta["guest_name"], meta["guest_email"], meta.get("guest_phone") or None,
        dinner_date, party_size, meta.get("dietary_notes", ""),
        payment_status="paid", stripe_session_id=session["id"],
        stripe_payment_intent_id=session.get("payment_intent"),
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
    conn.close()
    opening_date = parse_date(settings["opening_date"]) if settings and settings["opening_date"] else None
    not_yet_open = bool(opening_date and opening_date > datetime.now(timezone.utc).date())
    return render_template("restaurant_info.html", settings=settings, not_yet_open=not_yet_open)


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

        total_price = (settings["price_per_person"] * party_size) if settings["price_per_person"] else 0
        if stripe_enabled() and total_price > 0:
            try:
                checkout_session = stripe.checkout.Session.create(
                    mode="payment",
                    payment_method_types=["card"],
                    line_items=[{
                        "price_data": {
                            "currency": "eur",
                            "product_data": {"name": f"Dinner reservation — {format_date_human(dinner_date.isoformat())}"},
                            "unit_amount": int(round(settings["price_per_person"] * 100)),
                        },
                        "quantity": party_size,
                    }],
                    customer_email=guest_email,
                    success_url=url_for("restaurant_stripe_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=url_for("restaurant_book", _external=True),
                    metadata={
                        "kind": "restaurant",
                        "guest_name": guest_name,
                        "guest_email": guest_email,
                        "guest_phone": guest_phone,
                        "dinner_date": dinner_date.isoformat(),
                        "party_size": str(party_size),
                        "dietary_notes": dietary_notes[:490],
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

    if session.get("payment_status") != "paid":
        conn.close()
        flash("That payment wasn't completed, so no reservation was made.", "error")
        return redirect(url_for("restaurant_book"))

    manage_token = create_restaurant_booking_from_stripe_session(conn, session)
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
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
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
                             medical_notes=None, special_occasion=None, booking_id=None):
    reference_code = make_workshop_reference_code()
    manage_token = secrets.token_urlsafe(24)
    total_price = (workshop["price_per_person"] * party_size) if workshop["price_per_person"] else None
    deposit_amount, balance_amount, balance_due_date = compute_workshop_payment_terms(
        total_price, workshop["deposit_percent"], parse_date(session_row["start_date"])
    )
    conn.execute(
        """INSERT INTO workshop_bookings
           (session_id, reference_code, manage_token, guest_name, guest_email, guest_phone, party_size,
            notes, total_price, occupancy_type, requested_roommate, dietary_notes, medical_notes,
            special_occasion, deposit_amount, balance_amount, balance_due_date, booking_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_row["id"], reference_code, manage_token, guest_name, guest_email, guest_phone or None,
         party_size, notes or None, total_price, occupancy_type, requested_roommate or None,
         dietary_notes or None, medical_notes or None, special_occasion or None,
         deposit_amount, balance_amount, balance_due_date, booking_id, datetime.now(timezone.utc).isoformat()),
    )
    booking_row_id = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?", (manage_token,)).fetchone()["id"]
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
            cancel_url=url_for("workshop_manage", manage_token=booking["manage_token"], _external=True),
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
    meta = session.get("metadata") or {}
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
        amount = (session.get("amount_total") or 0) / 100
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
    if session.get("payment_status") == "paid":
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
    conn.close()
    return render_template("workshops_public.html", workshops=workshops, sessions_by_workshop=sessions_by_workshop)


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
        reference_code, manage_token, booking_row_id = create_workshop_booking(
            conn, session_row, workshop, guest_name, guest_email, guest_phone or None, party_size, notes,
            occupancy_type=occupancy_type, requested_roommate=requested_roommate, dietary_notes=dietary_notes,
            medical_notes=medical_notes, special_occasion=special_occasion,
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
        reference_code = request.form.get("reference_code", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
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
    conn.close()
    return render_template(
        "workshop_manage.html", booking=booking, stripe_enabled=stripe_enabled(), guests=guests,
        custom_fields=custom_fields, custom_responses=custom_responses,
        balance_due=balance_due, total_charged=total_charged, total_paid=total_paid,
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
    conn.close()
    return render_template("room_issues.html", issues=issues, rooms=rooms, status_filter=status_filter, employees=employees)


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
            "SELECT * FROM blocked_dates WHERE room_id = ? AND start_date < ? AND end_date > ?",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()
        manual_blocks = conn.execute(
            "SELECT * FROM room_blocks WHERE room_id = ? AND start_date < ? AND end_date > ?",
            (room["id"], next_month.isoformat(), first_day.isoformat()),
        ).fetchall()

        cells = []
        for d in days:
            status, label = "free", ""
            for b in bookings:
                b_start, b_end = parse_date(b["arrival_date"]), parse_date(b["departure_date"])
                if b_start <= d < b_end:
                    status, label = b["status"], f"{b['guest_name']} ({b['status']})"
                    break
            if status == "free":
                for bl in blocked:
                    bl_start, bl_end = parse_date(bl["start_date"]), parse_date(bl["end_date"])
                    if bl_start <= d < bl_end:
                        status, label = "external", "Blocked on another platform"
                        break
            if status == "free":
                for rb in manual_blocks:
                    rb_start, rb_end = parse_date(rb["start_date"]), parse_date(rb["end_date"])
                    if rb_start <= d < rb_end:
                        status, label = "manual-block", rb["reason"] or "Blocked"
                        break
            cells.append({"date": d, "status": status, "label": label})
        room_rows.append({"room": room, "cells": cells})
    conn.close()

    return render_template(
        "admin_calendar.html", days=days, room_rows=room_rows, first_day=first_day, today=today,
        prev_month=prev_month.strftime("%Y-%m"), next_month=next_month.strftime("%Y-%m"),
    )


@app.route("/admin/team-calendar")
@owner_required
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
        employee_rows.append({"employee": emp, "cells": cells})

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
            """INSERT INTO rooms (name, description, max_occupancy, price_per_night, export_token,
               sort_order, photo_filename, amenities) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, description, int(max_occupancy) if max_occupancy.isdigit() else 2,
             float(price_per_night) if price_per_night else 0,
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
            """UPDATE rooms SET name=?, description=?, max_occupancy=?, price_per_night=?, active=?,
               photo_filename=?, amenities=? WHERE id=?""",
            (name, description, int(max_occupancy) if max_occupancy.isdigit() else room["max_occupancy"],
             float(price_per_night) if price_per_night else 0, active, photo_filename, amenities or None, room_id),
        )
        conn.commit()
        conn.close()
        flash("Room updated.", "success")
        return redirect(url_for("admin_rooms"))

    conn.close()
    return render_template("room_form.html", room=room)


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
           ORDER BY (bookings.status = 'pending') DESC, bookings.arrival_date"""
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
    conn.close()
    return render_template(
        "admin_bookings.html", bookings=bookings, counts=counts, rooms=rooms, employees=employees,
        status_filter=status_filter, room_filter=room_filter, q=q,
        arriving_today=arriving_today, departing_today=departing_today, arriving_this_week=arriving_this_week,
        returning_emails=returning_emails, confirmed_spend_by_email=confirmed_spend_by_email,
        scheduled_by_date=scheduled_by_date, today_iso=today_iso,
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
    conn.close()
    if not bookings and not dinners and not workshop_regs:
        abort(404)
    lifetime_spend = sum(b["total_price"] or 0 for b in bookings if b["status"] == "confirmed")
    lifetime_spend += sum(d["total_price"] or 0 for d in dinners if d["status"] == "confirmed")
    lifetime_spend += sum(w["total_price"] or 0 for w in workshop_regs if w["status"] == "confirmed")
    return render_template(
        "guest_booking_history.html", email=email, bookings=bookings, dinners=dinners,
        workshop_regs=workshop_regs, lifetime_spend=lifetime_spend,
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

    guest_id = booking["linked_guest_id"]
    if not guest_id:
        notes = f"Booking {booking['reference_code']} — {room['name']}."
        if booking["special_requests"]:
            notes += f" Notes: {booking['special_requests']}"
        cur = conn.execute(
            """INSERT INTO guests (name, arrival_date, departure_date, party_size, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (booking["guest_name"], booking["arrival_date"], booking["departure_date"],
             booking["party_size"], notes, datetime.now(timezone.utc).isoformat()),
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

    refunded, refund_error = refund_booking(conn, booking)
    refund_note = " Your payment has been refunded." if refunded else (" We'll be in touch about your refund." if booking["payment_status"] == "paid" else "")

    send_email(
        booking["guest_email"],
        f"Booking cancelled — {booking['room_name']}",
        f"Hi {booking['guest_name']},\n\nYour booking for {booking['room_name']} "
        f"({format_date_human(booking['arrival_date'])} to {format_date_human(booking['departure_date'])}) has been cancelled.{refund_note}\n\n"
        f"Reference code: {booking['reference_code']}\n\n— Château de Gudanes",
    )
    notified = notify_room_waitlist_opening(conn, booking["arrival_date"], booking["departure_date"])
    if notified:
        waitlist_note = f" Notified {len(notified)} waitlist guest{'s' if len(notified) != 1 else ''} automatically."
    else:
        remaining = matching_waitlist_entries(conn, booking["arrival_date"], booking["departure_date"])
        waitlist_note = f" {len(remaining)} waitlist entr{'y wants' if len(remaining) == 1 else 'ies want'} overlapping dates — check the waitlist." if remaining else ""
    conn.close()
    flash("Booking cancelled." + (" Payment refunded." if refunded else (f" Refund failed: {refund_error}" if refund_error and booking["payment_status"] == "paid" else "")) + waitlist_note, "success")
    return redirect(url_for("admin_bookings"))


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

        old_arrival, old_departure = parse_date(booking["arrival_date"]), parse_date(booking["departure_date"])
        old_nights = (old_departure - old_arrival).days if old_arrival and old_departure else 0
        old_room_portion = (booking["price_per_night"] or 0) * old_nights
        extras_portion = (booking["total_price"] or 0) - old_room_portion
        new_nights = (departure - arrival).days
        new_total = (booking["price_per_night"] or 0) * new_nights + extras_portion
        new_total = new_total or None

        conn.execute(
            """UPDATE bookings SET arrival_date=?, departure_date=?, party_size=?, guest_phone=?,
               special_requests=?, total_price=? WHERE id=?""",
            (arrival.isoformat(), departure.isoformat(), party_size, guest_phone or None,
             special_requests or None, new_total, booking_id),
        )
        conn.commit()

        if booking["linked_guest_id"]:
            conn.execute(
                "UPDATE guests SET arrival_date=?, departure_date=?, party_size=? WHERE id=?",
                (arrival.isoformat(), departure.isoformat(), party_size, booking["linked_guest_id"]),
            )
            conn.commit()

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
    refunded, refund_error = refund_booking(conn, booking)
    conn.close()
    flash("Refund issued." if refunded else f"Refund failed: {refund_error}", "success" if refunded else "error")
    return redirect(url_for("admin_bookings"))


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

def restaurant_profit_share(conn, year, month):
    """Revenue (confirmed dinner reservations) minus costs (approved expenses
    tagged restaurant-related) for a calendar month, split by the configured
    percentage. Costs use approved OR paid so a month's number doesn't jump
    the moment the owner marks something paid — both mean "the owner signed
    off on this cost"."""
    month_start = date(year, month, 1)
    month_end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    revenue = conn.execute(
        "SELECT COALESCE(SUM(total_price), 0) AS t FROM restaurant_bookings "
        "WHERE status = 'confirmed' AND dinner_date >= ? AND dinner_date < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["t"]
    costs = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM expenses WHERE restaurant_related = 1 "
        "AND status IN ('approved', 'paid') AND submitted_at >= ? AND submitted_at < ?",
        (month_start.isoformat(), month_end.isoformat()),
    ).fetchone()["t"]
    settings = get_restaurant_settings(conn)
    share_pct = settings["profit_share_percent"] if settings else 50
    profit = revenue - costs
    chef_share = profit * (share_pct / 100)
    owner_share = profit - chef_share
    return {
        "month_start": month_start, "revenue": revenue, "costs": costs, "profit": profit,
        "share_pct": share_pct, "chef_share": chef_share, "owner_share": owner_share,
    }


@app.route("/admin/restaurant")
@owner_required
def admin_restaurant():
    conn = get_db()
    status_filter = request.args.get("status", "")
    query = "SELECT * FROM restaurant_bookings"
    params = []
    if status_filter:
        query += " WHERE status = ?"
        params.append(status_filter)
    query += " ORDER BY dinner_date, created_at"
    reservations = conn.execute(query, params).fetchall()

    pending_count = conn.execute("SELECT COUNT(*) AS c FROM restaurant_bookings WHERE status = 'pending'").fetchone()["c"]

    today = datetime.now(timezone.utc).date()
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
    conn.close()
    return render_template(
        "admin_restaurant.html", reservations=reservations, status_filter=status_filter,
        pending_count=pending_count, upcoming_covers=upcoming_covers, settings=settings,
        employees=employees, today=today, profit=profit, prev_month=prev_month, next_month=next_month,
    )


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

    refunded, refund_error = refund_restaurant_booking(conn, booking)
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

    refunded, refund_error = refund_restaurant_booking(conn, booking)
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
    refunded, refund_error = refund_restaurant_booking(conn, booking)
    if refunded:
        log_audit(conn, "restaurant_booking_refunded", target=booking["reference_code"])
        flash("Reservation refunded.", "success")
    else:
        flash(f"Refund failed: {refund_error}", "error")
    conn.close()
    return redirect(url_for("admin_restaurant"))


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
        lead_user_id = int(lead_raw) if lead_raw.isdigit() else None

        conn.execute(
            """UPDATE restaurant_settings SET opening_date = ?, dinner_time = ?, capacity = ?,
               price_per_person = ?, lead_user_id = ?, profit_share_percent = ?, enabled = ?, updated_at = ?
               WHERE id = 1""",
            (opening_date or None, dinner_time, capacity, price_per_person, lead_user_id,
             profit_share_percent, enabled, datetime.now(timezone.utc).isoformat()),
        )
        log_audit(conn, "restaurant_settings_updated")
        conn.commit()
        conn.close()
        flash("Restaurant settings updated.", "success")
        return redirect(url_for("admin_restaurant_settings"))

    settings = get_restaurant_settings(conn)
    employees = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()
    conn.close()
    return render_template("admin_restaurant_settings.html", settings=settings, employees=employees)


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
    total_rooms = conn.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
    sessions_by_workshop = {}
    for w in workshops:
        sessions = conn.execute(
            "SELECT * FROM workshop_sessions WHERE workshop_id = ? AND end_date >= ? ORDER BY start_date",
            (w["id"], today.isoformat()),
        ).fetchall()
        rows = []
        for s in sessions:
            rooms_assigned = conn.execute(
                """SELECT COUNT(DISTINCT assigned_room_id) AS c FROM workshop_bookings
                   WHERE session_id = ? AND status IN ('pending', 'confirmed') AND assigned_room_id IS NOT NULL""",
                (s["id"],),
            ).fetchone()["c"]
            rows.append({
                "session": s, "remaining": workshop_session_remaining_capacity(conn, s["id"]),
                "rooms_assigned": rooms_assigned,
            })
        sessions_by_workshop[w["id"]] = rows
    instructors = conn.execute("SELECT * FROM users WHERE role IN ('owner', 'employee') ORDER BY role DESC, name").fetchall()
    custom_fields_by_workshop = {}
    for row in conn.execute("SELECT * FROM workshop_custom_fields ORDER BY sort_order").fetchall():
        custom_fields_by_workshop.setdefault(row["workshop_id"], []).append(row)
    conn.close()
    return render_template(
        "admin_workshops.html", workshops=workshops, sessions_by_workshop=sessions_by_workshop,
        instructors=instructors, today=today, total_rooms=total_rooms,
        custom_fields_by_workshop=custom_fields_by_workshop,
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

        if not title:
            flash("Workshop title is required.", "error")
            return render_template("workshop_form.html", workshop=None, instructors=known_instructor_list())

        conn = get_db()
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM workshops").fetchone()["m"]
        conn.execute(
            """INSERT INTO workshops (title, description, instructor_name, instructor_user_id, price_per_person,
               default_capacity, sort_order, deposit_percent, inclusions, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, instructor_name or None,
             int(instructor_user_id) if instructor_user_id.isdigit() else None,
             float(price_raw) if price_raw else 0,
             int(capacity_raw) if capacity_raw.isdigit() and int(capacity_raw) > 0 else 10,
             max_order + 1,
             int(deposit_percent_raw) if deposit_percent_raw.isdigit() else 30,
             inclusions or None, datetime.now(timezone.utc).isoformat()),
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
        active = 1 if request.form.get("active") == "on" else 0

        if not title:
            conn.close()
            flash("Workshop title is required.", "error")
            return redirect(url_for("edit_workshop", workshop_id=workshop_id))

        conn.execute(
            """UPDATE workshops SET title=?, description=?, instructor_name=?, instructor_user_id=?,
               price_per_person=?, default_capacity=?, deposit_percent=?, inclusions=?, active=? WHERE id=?""",
            (title, description, instructor_name or None,
             int(instructor_user_id) if instructor_user_id.isdigit() else None,
             float(price_raw) if price_raw else 0,
             int(capacity_raw) if capacity_raw.isdigit() and int(capacity_raw) > 0 else workshop["default_capacity"],
             int(deposit_percent_raw) if deposit_percent_raw.isdigit() else workshop["deposit_percent"],
             inclusions or None, active, workshop_id),
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
    conn.close()
    if clashing:
        flash(f"Session added — heads up, {clashing} existing room booking(s) overlap these dates.", "error")
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
    conn.close()
    return render_template(
        "admin_workshop_registrations.html", registrations=registrations, status_filter=status_filter,
        session_filter=session_filter, pending_count=pending_count, rooms=rooms,
        guests_by_booking=guests_by_booking, custom_responses_by_booking=custom_responses_by_booking,
        transactions_by_booking=transactions_by_booking, balance_by_booking=balance_by_booking,
        messages_by_booking=messages_by_booking,
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


@app.route("/admin/workshops/registrations/<int:registration_id>/assign-room", methods=["POST"])
@owner_required
def assign_workshop_room(registration_id):
    room_id_raw = request.form.get("room_id", "").strip()
    conn = get_db()
    conn.execute(
        "UPDATE workshop_bookings SET assigned_room_id = ? WHERE id = ?",
        (int(room_id_raw) if room_id_raw.isdigit() else None, registration_id),
    )
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
    rows = conn.execute("SELECT * FROM guests ORDER BY arrival_date").fetchall()
    conn.close()
    fieldnames = ["name", "arrival_date", "departure_date", "party_size", "notes", "created_at"]
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
        recent_transfers=recent_transfers, insurance_by_vehicle=insurance_by_vehicle,
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
    conn = get_db()
    conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction, scheduled_at, driver_user_id, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (vehicle_id, guest_name or None, direction, scheduled_at,
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
    new_status = "done" if task["status"] == "open" else "open"
    conn.execute(
        "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
        (new_status, datetime.now(timezone.utc).isoformat() if new_status == "done" else None, task_id),
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
    conn.close()
    queue = (
        [{"kind": "leave", "sort_at": r["requested_at"], "row": r} for r in leave]
        + [{"kind": "expense", "sort_at": r["submitted_at"], "row": r} for r in expenses]
    )
    queue.sort(key=lambda item: item["sort_at"] or "")
    return render_template("admin_approvals.html", queue=queue)


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
            """SELECT * FROM tasks WHERE assigned_to_user_id = ? AND status = 'open'
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
    conn.close()
    if employee:
        send_email(
            employee["email"],
            f"Your time off request has been {status}",
            f"Hi {employee['name'].split(' ')[0]},\n\n"
            f"Your time off request for {req['start_date']} to {req['end_date']} has been {status}.\n\n"
            f"— Château de Gudanes",
        )
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
           WHERE tasks.status = 'open' AND (due_date IS NULL OR due_date <= ?)
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
    guests_here = [
        g for g in [guest_with_status(g, today) for g in conn.execute("SELECT * FROM guests").fetchall()]
        if g["stay_status"] == "current"
    ]
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
        todays_workshop_sessions=todays_workshop_sessions,
    )


# ---------------------------------------------------------------------------
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


AUTOMATION_JOBS = [
    ("housekeeping", "automation_housekeeping_enabled", None, 600, run_housekeeping_job),
    ("daily_digest", "automation_daily_digest_enabled", None, 24 * 3600, run_daily_digest_job),
    ("ical_sync", "automation_ical_sync_enabled", "automation_ical_sync_interval_hours", None, run_ical_sync_job),
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


if __name__ == "__main__":
    init_db()
    # Werkzeug's reloader (debug=True) re-executes this module in a child
    # process with WERKZEUG_RUN_MAIN set — starting the thread only there
    # (or when the reloader isn't in play at all) keeps a single automation
    # loop per running server instead of one per reloader generation.
    if not DEBUG_MODE or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=automation_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=DEBUG_MODE, threaded=True)
