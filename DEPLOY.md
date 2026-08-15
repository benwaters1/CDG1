# Deploying the Château de Gudanes Staff HR App

This app is deliberately simple to host — one Python file, one small database
file, no complicated build process. Two realistic options below, easiest first.

---

## Option A — Railway (easiest, ~10 minutes, free tier works for this size)

Railway takes a folder of code and gives you a live URL. No server management.

1. Create a free account at railway.app
2. Push this folder to a **private** GitHub repository (Railway deploys from GitHub)
   - If you're not comfortable with GitHub yet, this is exactly the kind of
     step Claude Code can walk you through directly, or do for you, once
     you're set up there.
3. In Railway: **New Project → Deploy from GitHub repo** → select this repo
4. Railway will detect it's a Python app automatically. Add these
   **environment variables** in Railway's dashboard (Settings → Variables):
   - `FLASK_SECRET_KEY` — generate one by running this on your computer:
     `python3 -c "import secrets; print(secrets.token_hex(32))"`
     and paste the output in as the value
   - `FLASK_DEBUG` — leave unset (it now defaults to off). Never set this to
     `1` once it's live — debug mode activates an interactive Python console
     on unhandled errors, which is a real risk if left on in production.
   - **To turn on real email** (booking confirmations, owner notifications),
     pick one:
     - **Resend (recommended)** — sign up at resend.com, add your domain
       under Domains, and add the DNS records it gives you wherever your
       domain's DNS is managed (this is unrelated to who you registered
       the domain through — Crazy Domains, GoDaddy, etc. all let you edit
       DNS records the same way; check their dashboard for a "DNS" or
       "Nameservers" section). Once the domain shows "Verified" in Resend,
       set `RESEND_API_KEY` (from Resend → API Keys) and `RESEND_FROM`
       (e.g. `bookings@yourdomain.com`, using the domain you just verified).
     - **Plain SMTP (fallback)** — `SMTP_HOST`, `SMTP_PORT` (usually `587`),
       `SMTP_USERNAME`, `SMTP_PASSWORD`, and optionally `SMTP_FROM` if it
       should differ from `SMTP_USERNAME`. Any real mailbox works — your
       domain's email host, a Gmail app password, or a transactional
       service's SMTP relay.

     If both are set, Resend takes priority. Without either set, the app
     runs exactly as it does today: no emails sent, guests get their
     reference code/link on screen only.
   - **To turn on real payment collection at booking time**: `STRIPE_SECRET_KEY`
     and `STRIPE_PUBLISHABLE_KEY` from your Stripe Dashboard → Developers →
     API keys (use the test-mode keys first to try it safely, live keys once
     you're ready for real charges). Also add `STRIPE_WEBHOOK_SECRET` from
     Dashboard → Developers → Webhooks — point the webhook at
     `https://<your-domain>/webhooks/stripe` listening for the
     `checkout.session.completed` event; this is what reliably creates the
     booking even if a guest closes their browser right after paying, before
     they'd otherwise land back on your site. Without these set, booking stays
     the current request-only flow with no payment step.
   - **To turn on automatic calendar syncing** (pulling Airbnb/Booking.com/VRBO
     calendars every 1-3 hours instead of only when someone clicks "Sync all
     calendars" by hand): `ICAL_SYNC_TOKEN` — generate one the same way as the
     secret key: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
     See **Scheduling automatic calendar sync** below for how this gets used.
     Without it set, `/api/sync-ical` always returns 404 and syncing stays
     manual — nothing changes from how it works today.
   - **To turn on the Vault** (Management → Vault, for shared logins like
     supplier portals or banking): `VAULT_ENCRYPTION_KEY` — generate one with:
     `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
     Entries are encrypted with this key before they're stored — losing the
     key means losing everything in the vault permanently, so back it up
     somewhere separate from the database (a password manager's secure notes,
     for instance). Without it set, the Vault page just says it isn't
     configured yet.
5. Railway gives you a URL like `gudanes-hr.up.railway.app`
6. **Point your own domain at it** (optional but nicer): in Railway, add a
   custom domain like `staff.chateaugudanes.com`, then add the DNS record
   Railway gives you into your Squarespace domain settings
   (Squarespace: Settings → Domains → DNS Settings)

**Important — Railway's disk is not permanent by default.** The SQLite
database and uploaded files need to live on a *persistent volume*, or they
can vanish on redeploy. In Railway: **Settings → Volumes → Add a volume**,
mount it to cover both `uploads/` (documents, receipts, supplier invoices)
and `room_photos/` (public room images), and consider moving the database
file there too by setting an environment variable `DB_PATH` — ask Claude
Code to wire that up when you get there, it's a small change.

---

## Scheduling automatic calendar sync

Each room's iCal import (its Airbnb/Booking.com/VRBO calendars) only syncs
when someone clicks "Sync all calendars" or "Sync now" on the Rooms page —
unless you set `ICAL_SYNC_TOKEN` (see above) and point an external scheduler
at:

```
https://<your-domain>/api/sync-ical?token=<your ICAL_SYNC_TOKEN>
```

A GET or POST both work; it re-syncs every imported calendar and returns a
JSON summary (added/removed/unchanged per calendar). Wrong or missing token
returns a plain 404, same as any other unrecognized URL. Every run is
logged — the Rooms page shows each calendar's most recent added/removed
count next to it, useful for spotting a feed that's gone quiet or started
erroring.

Pick **one** of these to actually call that URL every 1-3 hours:

- **Railway Cron Job** (if you're hosting there): in your project, add a
  second service → **Cron Job**, same repo, schedule `0 */2 * * *` (every 2
  hours), command:
  `curl -fsS "https://<your-domain>/api/sync-ical?token=$ICAL_SYNC_TOKEN"`
  (add `ICAL_SYNC_TOKEN` as a variable on that service too).
- **A free external pinger** (simplest, works on any host): a service like
  cron-job.org — create a free account, add the URL above as a job, set it
  to run every 1-3 hours. No server-side setup at all.
- **Plain cron** (Option B / VPS): add a line to `crontab -e`:
  ```
  0 */2 * * * curl -fsS "https://<your-domain>/api/sync-ical?token=YOUR_TOKEN" >/dev/null
  ```

Whichever you pick, keep the token out of anything public (don't put it in
the repo, a public status page, etc.) — treat it like a password.

---

## Scheduling a daily owner summary email

Set `DIGEST_TOKEN` (generate it the same way as `ICAL_SYNC_TOKEN` above) and
point the same kind of scheduler at:

```
https://<your-domain>/api/owner-digest?token=<your DIGEST_TOKEN>
```

This emails the owner a short plain-text summary — pending approvals, who's
on shift, open room issues, leave starting in the next week — using whatever
`SMTP_*` settings are already configured for booking emails. Without
`SMTP_*` set, the request still returns `200 {"sent": false, ...}` rather
than erroring; without `DIGEST_TOKEN` set, the endpoint 404s and nothing is
sent, same as `/api/sync-ical`. Once a day (e.g. `0 7 * * *`, 7am) is enough
for this — it's a summary, not a live feed.

---

## Outlook add-in (guest lookup, send-time price check, AI reply drafts)

Set `GUEST_LOOKUP_TOKEN` (generate it the same way as `ICAL_SYNC_TOKEN`
above) — this one token gates all three add-in features (guest lookup,
the send-time pricing/availability check, and reply drafting), entered
once into the add-in itself rather than into a scheduler. Then log in as
the owner and go to Management → Outlook Add-in for the manifest link and
sideloading steps. Without `GUEST_LOOKUP_TOKEN` set, none of the add-in's
API calls work — they all 404 the same way `/api/guest-lookup` does.

Reply drafting additionally needs `ANTHROPIC_API_KEY` (your own Anthropic
account's key — https://console.anthropic.com). Without it, the "Draft
with AI" button shows an error instead of a draft; guest lookup and the
send-time check work regardless, since neither one calls Claude.

---

## Office TV / wallboard display

`/admin/tv` is a self-contained, auto-refreshing dashboard meant for a
permanent screen in a staff area — occupancy, who's on shift, today's
tasks, and anything needing a decision, reloading itself every 60 seconds.
Opening "Open TV Display" from the Home page while logged in as owner works
immediately, but a real login session expires after 12 hours — fine for a
quick check, not for a screen meant to run unattended for weeks.

For a permanent display, set `TV_DASHBOARD_TOKEN` (generate it the same way
as `ICAL_SYNC_TOKEN` above) and point the kiosk's browser at:

```
https://<your-domain>/admin/tv?token=<your TV_DASHBOARD_TOKEN>
```

That URL never needs a login and never expires, so it's safe to leave open
on a dedicated device indefinitely. Without `TV_DASHBOARD_TOKEN` set,
`/admin/tv` still works fine for a logged-in owner — it just has no
token-based alternative yet, so an unattended screen would eventually drop
to the login page.

---

## Microsoft Graph inbox monitoring (Inbox Flags)

Management → Inbox Flags scans one real mailbox and flags two things: emails
that haven't had a reply within a configurable window, and emails that
mention a price or date that doesn't match this app's real pricing or
availability data (a room, workshop, restaurant, or event quote). This is
a meaningfully bigger setup than the other integrations above — it needs
an Azure AD app registration with actual read access to a mailbox, not just
an API key.

You'll need whoever has **Global Administrator** (or Application
Administrator + Exchange Administrator) on your Microsoft 365 tenant to do
this — it can't be done from inside this app, and Claude Code can't do it
for you either, since it needs your own Microsoft 365 admin login.

1. Go to **portal.azure.com** → sign in with the admin account → search
   "**App registrations**" → **New registration**
   - Name: anything, e.g. "Gudanes HR Inbox Scan"
   - Supported account types: "Accounts in this organizational directory only"
   - Redirect URI: leave blank
   - Click **Register**
2. On the app's Overview page, copy two values:
   - **Application (client) ID** → this is `MS_GRAPH_CLIENT_ID`
   - **Directory (tenant) ID** → this is `MS_GRAPH_TENANT_ID`
3. **Certificates & secrets** → **New client secret** → any description/expiry
   → **Add** → copy the secret's **Value** immediately (this is
   `MS_GRAPH_CLIENT_SECRET` — it's shown once and never again)
4. **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Application permissions** → search "Mail" → check **Mail.Read** →
   **Add permissions**
5. Still on API permissions: click **"Grant admin consent for [your
   organization]"** → confirm. Skipping this is the single most common
   mistake — without it, every request this app makes to Graph fails with
   a permissions error, even though the permission is listed.
6. **Restrict it to one mailbox.** As granted above, this app can technically
   read *every* mailbox in your Microsoft 365 tenant, not just the one you
   want scanned. Lock that down with an Exchange Online **Application
   Access Policy** so it can only ever touch the château's own mailbox —
   this needs PowerShell (`Install-Module -Name ExchangeOnlineManagement`
   if you don't have it already):
   ```powershell
   Connect-ExchangeOnline
   New-DistributionGroup -Name "GudanesInboxScanMailboxes" -Type Security -Members accounts@chateaugudanes.com
   New-ApplicationAccessPolicy -AppId "<Application (client) ID from step 2>" `
     -PolicyScopeGroupId "GudanesInboxScanMailboxes" -AccessRight RestrictAccess `
     -Description "Gudanes HR inbox scan - restricted to accounts@ only"
   ```
   This is the step that turns "an app that can technically read anyone's
   mail" into "an app that can only ever read accounts@chateaugudanes.com" —
   worth doing even though it's the most technical step here. If PowerShell
   feels unfamiliar, this is a good one to do with Claude Code open next to
   you, or with Microsoft 365 support on a call.
7. Set these as environment variables wherever the app is hosted (Railway →
   Settings → Variables, same as the other integrations above):
   - `MS_GRAPH_TENANT_ID`
   - `MS_GRAPH_CLIENT_ID`
   - `MS_GRAPH_CLIENT_SECRET`
   - `MS_GRAPH_MAILBOX` — the mailbox to scan, e.g. `accounts@chateaugudanes.com`

Once all four are set, go to **Management → Automation** and confirm "Inbox
scan" is turned on (it defaults to on) — from then on it runs automatically
every ~15 minutes alongside the other background jobs, no external
scheduler needed. Flagged emails show up at **Management → Inbox Flags**.
Without these four variables set, that page just says it isn't connected
yet — nothing else in the app changes.

**What it can see and do:** read-only, and deliberately minimal. It never
sends, deletes, moves, or modifies anything in the mailbox — it only lists
recent Inbox and Sent Items messages to check whether an inbound email
already got a reply, and reads the subject line and a short preview to look
for a price or date worth comparing against real data. The full body of an
email is never fetched or stored — only the sender, subject, and a short
preview, and only for the messages that actually get flagged.

---

## Option B — A cheap VPS (more control, ~€5/month, ~30-45 minutes)

Providers: Hetzner, DigitalOcean, or similar. Any small/cheapest tier works
fine for a team this size.

1. Spin up a small Ubuntu server
2. SSH in, then:
   ```
   sudo apt update && sudo apt install python3-pip python3-venv nginx -y
   git clone <your repo> gudanes-hr
   cd gudanes-hr
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install gunicorn
   ```
3. Run it properly (not the dev server) with Gunicorn:
   ```
   FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
   gunicorn -w 2 -b 127.0.0.1:5000 app:app
   ```
4. Put Nginx in front of it as a reverse proxy, and use **Certbot** for a
   free HTTPS certificate — this part has a lot of small steps, and is
   genuinely the part I'd most recommend doing with Claude Code directly
   rather than by hand, since it can write the exact Nginx config and
   walk through Certbot for your specific domain.
5. Use `systemd` (or a tool like `supervisor`) to keep the app running and
   auto-restart if the server reboots — again, a good Claude Code task.

---

## Either way — do these before real staff data goes in

- [ ] Set a real `FLASK_SECRET_KEY` (not the auto-generated one that changes
      on every restart — that would log everyone out every time the app
      restarts)
- [ ] Confirm `FLASK_DEBUG` isn't set to `1` anywhere (it defaults to off)
- [ ] Check `LOCAL_TZ` (defaults to `Europe/Paris`) matches where the team
      actually is — it's what the timesheet's clock in/out times are
      displayed in. Only needs setting if that's ever not the case.
- [ ] Log in as the owner and **change the generated password immediately**
- [ ] Confirm HTTPS is working (padlock in the browser) before anyone logs
      in with a real password over the connection
- [ ] Set up **regular backups** of `gudanes_hr.db`, the `uploads/` folder,
      and the `room_photos/` folder — this is the one thing that's easy to
      forget and expensive to regret. `gudanes_hr.db` now holds bookings and
      guest contact details too, not just staff records. Even a simple daily
      copy to cloud storage is enough at this scale.

## Installing it as an app on staff phones

The staff portal is an installable PWA (manifest + service worker already
wired in) — a phone can "Add to Home Screen" and it opens full-screen, no
browser bar, with its own icon. This only works over **HTTPS** (or
`localhost`) — testing over a plain `http://` LAN address won't show the
install prompt, so this needs a real deployment (see above) before it's
installable. Once it's live: open the site in Chrome (Android) or Safari
(iOS) → menu → "Add to Home Screen" / "Install app".

## What I'd genuinely recommend

Take this whole folder to **Claude Code** (either the desktop app or in a
terminal) and say: "help me deploy this Flask app to Railway with a
persistent volume and my own domain." That single session will get this
properly live, with backups and HTTPS sorted, far faster and more reliably
than working through this document by hand.
