# Deploying the Château de Gudanes Staff HR App

This app is deliberately simple to host — one Python file, one small database
file, no complicated build process. Two realistic options below, easiest first.

---

## Go live: the short version

The full detail is further down; this is the order to do it in. Steps 1–6 get
you a working private URL. Everything after that is optional and can wait.

1. **Create a Railway account** — <https://railway.app> → *Login with GitHub*.
2. **New Project → Deploy from GitHub repo** → pick `benwaters1/CDG1`, branch
   `main`. It reads `Procfile` and `requirements.txt` and needs no
   configuration.
3. **Add a volume before the first real use** — *Settings → Volumes → New
   Volume*, mount path `/data`. Without it the database is wiped on every
   deploy.
4. **Add two variables** — *Settings → Variables*:
   - `GUDANES_DB_PATH` = `/data/gudanes_hr.db` — puts the database, uploads
     and room photos on the volume.
   - `FLASK_SECRET_KEY` = a 64-character random string. Generate one with
     `python -c "import secrets; print(secrets.token_hex(32))"`. Without it
     everyone is signed out on every restart.
5. **Get the first password out of the logs.** On its first boot the app
   creates the owner account and prints a generated password to the deploy log
   — *Deployments → View logs*, look for `FIRST RUN`. There is no email
   recovery configured yet, so this is the only way in. Save it, log in,
   change it immediately.
6. **Set `PUBLIC_BASE_URL`** to the address Railway gives you (e.g.
   `https://cdg1-production-9186.up.railway.app`, no trailing slash). Links inside
   automated email have nothing to point at until you do.
7. **Then, in whatever order suits you**: real email (`RESEND_API_KEY`),
   live payments (`STRIPE_*`), a custom domain, the Vault key, the scheduled
   jobs. Each is independent — the app runs without any of them, it just does
   less. `/admin/readiness` tells you what is still missing.

Two things are yours rather than the app's, and a guest sees both at booking:
the terms & conditions are still placeholder text, and no registered postal
address is set (marketing email legally has to carry one). Fill those in under
Management before the URL goes anywhere public.

---

## Moving chateaugudanes.com over from Squarespace

Two DNS records change and nothing else. Email is untouched as long as the
rest are left exactly as they are.

**The day before:** lower the TTL on the two records below to 300 seconds, so
the change takes minutes rather than hours. Have the readiness page
(`/admin/readiness`) showing nothing red.

**Where the DNS actually is (checked 15 October 2026).** Not on Crazy Domains'
own "DNS Settings" page, which is out of date and unused: it is in the cPanel of
the **"Unlimited Email"** product (Control Panel → Zone Editor), behind the
nameservers ns1/ns2.syrahost.com, about 50 records. **That product renews on 25
October; if it lapses, the DNS and the email go with it.** Its zone editor has
no ALIAS record, so the bare domain cannot point at Railway from there: either
move the DNS to Cloudflare (free; every record copied across first, email ones
checked before the nameservers change), or keep it here and forward the bare
domain to www.

1. **In Railway** (*CDG1 → Settings → Networking → Custom domain*):
   `www.chateaugudanes.com` was added on 15 October, port 8080, www only. It
   waits for these two records:
   - `CNAME` `www` → `sssix12f.up.railway.app`
   - `TXT` `_railway-verify.www` → `railway-verify=d20d6aae856d7e3be01a7085ea4966a923dc8d3e23fba926f8ad7785a3638a02`
2. **At the DNS (the cPanel zone editor, or Cloudflare)**, change only these:
   - `www` — the CNAME `ext.squarespace.com` becomes `sssix12f.up.railway.app`,
     and the TXT above is added.
   - `chateaugudanes.com` itself — the four Squarespace `A` records
     (`198.49.23.144/.145`, `198.185.159.144/.145`) go. A bare domain cannot
     hold a CNAME: with Cloudflare, a flattened CNAME to Railway; from the
     cPanel, a redirect of the bare domain to `https://www.chateaugudanes.com`.
   - In the SPF record, take out `+a`. Once the bare domain points at Railway,
     `+a` would let any server behind Railway's addresses send mail as the
     house.
   - **Leave alone:** the MX record (Outlook), autodiscover, the two Microsoft
     DKIM records (`selector1` and `selector2._domainkey`), the two `MS=`
     records and `gck97xs2wdxyttyjdjtz`, the rest of the SPF, `_dmarc`, and
     everything on `send.` (`resend._domainkey.send`, `send.send`,
     `rsend.send`, Resend). Those are the email.
3. **Wait until Railway shows both domains with a certificate** and
   `https://www.chateaugudanes.com` opens the new site.
4. **Only then, in Railway's variables:** `PUBLIC_BASE_URL` =
   `https://www.chateaugudanes.com` (the address the house uses — www or the
   bare domain, whichever was chosen above) and `SITE_IS_LIVE` = `1`.
   The order matters. From that moment every public page asked for on any
   other address — the Railway one, or the bare domain — is sent permanently
   to that one, so search engines see one site rather than three copies of
   it. Set it before the DNS has moved and the Railway address sends people
   to Squarespace. The app refuses to treat a Railway address as the house's,
   and the readiness page goes red if `SITE_IS_LIVE` is on without a proper
   one.
5. **Then:** add the real domain to the Outlook add-in's sign-in redirect (see
   the add-in section); tick *Write to guests*; submit `/sitemap.xml` in Google
   Search Console. The Stripe and Resend webhooks stay on the Railway address,
   which keeps answering and is never redirected (webhooks, the calendar feeds
   and `/outlook-addin/*` are not moved, by design).

### Stripe, test keys to live

The account (Chateau de Gudanes SASU) is shared with other live integrations
(RetreatGuru until the switch, BNBForms, webkul), and its default API version is
2014-07-22. So the site gets **its own restricted key**, and its webhook is
created by `tools/stripe_webhook.py` at the API version the app's library pins
(`stripe.api_version`, 2026-07-29.dahlia), because the dashboard offers only
2014-07-22 or the newest.

1. **Restricted key**, in the live dashboard (Developers → API keys → Create
   restricted key), with only what the app calls:
   - Checkout Sessions: **Write** (create, retrieve, list)
   - PaymentIntents: **Write** (create, retrieve, cancel: the till and the card reader)
   - Charges: **Read** (the charge behind a payment, for the fee and the refunds)
   - Refunds: **Write** (the refund desk; the owner decides every refund)
   - Customers: **Write** (one is made for a saved card)
   - Terminal → Readers: **Write**, only if the card reader is used
   - Webhook Endpoints: **Write**, only while running step 2, then set back to None
   - everything else: None

   Make the same restricted key in **test** mode first and do the end-to-end
   test booking with it: a permission that is missing fails there, with
   Stripe naming it, instead of in front of a guest.
2. **The webhook**, from this folder in a terminal, with the restricted live key
   set for that one command (never pasted anywhere else):
   `$env:STRIPE_SECRET_KEY = "rk_live_..."; python tools/stripe_webhook.py --create`
   It refuses if the site's endpoint already exists, and writes the signing
   secret to `.env.stripe-webhook-live` (gitignored). `--list` shows every
   endpoint on the account with its API version; run it with the test key to
   see which version the test endpoint is on.
3. **Railway → Variables**, all three at once (one deploy):
   `STRIPE_SECRET_KEY` = the `rk_live_` key, `STRIPE_PUBLISHABLE_KEY` = the
   `pk_live_` key, `STRIPE_WEBHOOK_SECRET` = the value in
   `.env.stripe-webhook-live`. Then delete that file, and check the Go-live
   checklist says "Live keys".
4. **Later, not on switch day:** roll the shared `sk_live_` (the PC incident)
   once RetreatGuru is retired and BNBForms and webkul are confirmed unused;
   then upgrade the account's default API version. The site is unaffected by
   either, since it no longer uses that key and its webhook is pinned.

The old Squarespace addresses (`/chateau-ateliers`, `/nightly-reservation`,
the celebrations portfolio, the terms pages and the rest) are redirected by
`LEGACY_PATHS` in app.py, checked against Squarespace's own sitemap by
`tests/test_old_addresses.py`.

---

## Local secrets: the `.env` file

Every secret (Stripe, email, Microsoft Graph, Anthropic, the vault key) can go
in a `.env` file next to `app.py`, one `KEY=value` per line. It's gitignored,
so it never reaches GitHub. Real environment variables always win over the
file, so hosting platforms are unaffected.

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
```

Restart the app after editing it.

---

## Rotating the keys (after a leak, or a computer that can't be trusted)

Written after the 5 October 2026 incident, when a fake "verify you're human"
command ran on the PC that holds this folder. Assume anything that PC could
read was copied: the `.env` file, and any website the browser was signed into.

**Nobody types a key into a chat, an email or a ticket.** Provider keys are
copied from the provider's page straight into Railway. Keys the app makes up
itself come from `python tools/new_keys.py`, which writes them into
`.env.new-keys` (gitignored, never read by the app) and prints only their
names. Delete that file once the values are in Railway.

### 0. First, the accounts that can read the keys

A new key is only as safe as the logins that can look at it. Change the
password and sign out every session on: Microsoft 365, Railway, GitHub,
Stripe, Resend, Pennylane and the Anthropic console. Then look for anything
left behind that survives a password change:

- **GitHub** — Settings → SSH keys, Personal access tokens, Applications,
  Sessions; and the repository's Settings → Collaborators, Deploy keys,
  Webhooks. A push to `main` deploys to production, so a stranger's key here
  is a way into the live site.
- **Railway** — Account → Tokens, and the project's Members.
- **Stripe** — Developers → API keys: delete any restricted key you don't
  recognise.

### 1. Take out the variables that should not be there at all

- `OWNER_TEMP_PASSWORD` — **every deploy resets the owner password to it.**
  Rotating keys means deploying, so sign in, change your password, then
  delete this variable before anything else.
- `OWNER_RECOVERY_TOKEN` — meant to be set, used once, and deleted.

### 2. New keys from each provider — make the new one, don't revoke the old yet

| Railway variable | Where the new one comes from |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe → Developers → API keys → Secret key → ⋯ → **Roll key**. Let the old one expire in 1 hour, so the site keeps working while you paste. Do it in test mode **and** live mode — the live key isn't used yet, so rolling it now costs nothing. |
| `STRIPE_WEBHOOK_SECRET` | Stripe → Developers → Webhooks → the endpoint → **Roll secret**. |
| `STRIPE_PUBLISHABLE_KEY` | Public by design (it is in every checkout page), so nothing to do. |
| `RESEND_API_KEY` | Resend → API Keys → Create (sending access, domain `send.chateaugudanes.com`). |
| `RESEND_WEBHOOK_SECRET` | Resend → Webhooks → the endpoint. If it offers no way to renew the secret, delete the endpoint and add it again (same URL, same four events). |
| `PENNYLANE_API_TOKEN` | Pennylane → Settings → Developers (API tokens): a new token with the same permissions. **This one is live.** |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys → Create. Look at Usage since 5 October while you're there — spending you didn't do is the tell of a stolen key. |
| `MS_GRAPH_CLIENT_SECRET` | entra.microsoft.com → App registrations → *Chateau de Gudanes - inbox reader* → Certificates & secrets → New client secret. Copy the **Value**, not the Secret ID. |
| `SMTP_PASSWORD`, `SMS_PROVIDER_TOKEN`, `TUYA_ACCESS_SECRET` | Only if set. Each from its own provider. |

### 3. New keys the app makes itself

Run `python tools/new_keys.py` and paste from `.env.new-keys`. What each
change does the moment it deploys:

| Railway variable | What changing it does |
|---|---|
| `FLASK_SECRET_KEY` | Signs **everyone** out — staff phones, the till, the owner, guests' accounts. A form that is open at that moment fails once. Nothing stored is signed with it, so no link or booking breaks. Do it at a quiet hour and warn the staff. |
| `GUDANES_INGEST_KEY` | Put the same value in `tools/receipts.ini` on the château PC and in the camera-roll uploader, or receipts and photos stop arriving. |
| `ICAL_SYNC_TOKEN`, `DIGEST_TOKEN` | Only if set: update whatever calls `/api/sync-ical` or `/api/owner-digest`. |
| `GUEST_LOOKUP_TOKEN` | Only if set: enter it again in the Outlook add-in. |
| `OFFICE_DISPLAY_TOKEN` | Only if set: change the wallboard's address to the new token. |
| `VAULT_ENCRYPTION_KEY` | **Not in this round.** See "Changing the vault key" below — done wrongly, every Vault password and bank detail becomes unreadable for good. |

### 4. One deploy, then check, then revoke

1. In Railway → Variables, paste every new value. Railway holds the edits
   until you press **Deploy**, so all of them go out in one restart.
2. After it deploys, open Admin → Readiness. Then prove each one works: the
   test-send button on Admin → Email outbox (it mails only you), a Pennylane
   scan, one Stripe test payment, and the mailbox line on Management →
   Booking.com.
3. Only now revoke the old keys on each provider's page, and delete the old
   client secret in Entra (the one with the earlier date).
4. Delete `.env.new-keys`.

### 5. This PC's own `.env`

It held `FLASK_SECRET_KEY`, `GUDANES_INGEST_KEY`, `PENNYLANE_API_TOKEN` and
the two Stripe test keys. The test suite needs none of them — it switches
Stripe and Pennylane off itself. Delete the Pennylane and Stripe lines, and
put a Stripe test key back only on a day you are testing payments here.
Never copy the production `FLASK_SECRET_KEY` into it: without one, the app
makes its own local key in `.dev_secret_key`.

### Changing the vault key

`VAULT_ENCRYPTION_KEY` locks the Vault's passwords and the bank details. The
app can open things locked with an older key while you change it:

1. In Railway, add `VAULT_ENCRYPTION_KEY_PREVIOUS` holding the **current**
   vault key, and set `VAULT_ENCRYPTION_KEY` to the new one from
   `.env.new-keys`. Deploy once, with both changes.
2. Management → Vault now says how many secrets are still locked with the
   previous key and names them. Press **Lock them with the new key**. It says
   which it moved, and names any it couldn't open.
3. When the page says everything is on the new key, delete
   `VAULT_ENCRYPTION_KEY_PREVIOUS` and deploy.
4. Replace the copy of the key you keep outside Railway with the new one.

If something shows as unreadable, it was locked with a key the app was never
given — put that old key into `VAULT_ENCRYPTION_KEY_PREVIOUS` and it comes
back. Until then, editing that entry with the password left blank is refused,
because saving it would replace the password with nothing.

A new key protects what is stored from now on. If the old key **and** a copy
of the database could both have been taken — an emailed backup in a mailbox
that was read, or a Railway login that was used — then the passwords inside
the Vault are what was exposed. Change those at their own sites as well.

---

## Testing Stripe locally (before going live)

Use **test-mode** keys (`sk_test_…`) — no real money moves. With those set,
booking switches from request-only to a real Stripe Checkout page.

To test what happens *after* payment, Stripe needs a way to reach your laptop.
Install the CLI (`winget install --id Stripe.StripeCli -e --source winget`),
then:

```
stripe login
stripe listen --forward-to localhost:5000/webhooks/stripe
```

That prints a signing secret (`whsec_…`). **It is regenerated every time you
run `stripe listen`**, so don't bother saving it in `.env` — pass it to the
app for that session instead:

```
STRIPE_WEBHOOK_SECRET=whsec_... python app.py
```

Leave `stripe listen` running in its own window, then pay with test card
`4242 4242 4242 4242`, any future expiry, any CVC.

**In production the webhook secret is different and stable**: create the
endpoint at Dashboard → Developers → Webhooks pointing at
`https://<your-domain>/webhooks/stripe`, and use the secret it gives you. That
one *does* belong in your hosting platform's environment variables. Send it
these events:

- `checkout.session.completed` and `checkout.session.async_payment_succeeded`
  / `checkout.session.async_payment_failed` — payments arriving
- `charge.refunded`, `charge.refund.updated`, `refund.created`,
  `refund.updated` and `refund.failed` — a refund made in Stripe's own
  dashboard reaches the books, and one that fails after Stripe accepted it is
  booked back
- `charge.dispute.created`, `charge.dispute.updated` and
  `charge.dispute.closed` — a card dispute becomes a task with its deadline,
  and a lost one a refund in the record
- `payment_intent.succeeded` — a tap on the restaurant's card reader reaches
  the books even when no till page is open to notice it. Without it the till
  only learns of a tap by asking, from a tab left open on a screen. Every
  payment on the account fires this event; the app acts only on the till's own
  reader charges and ignores the rest.

Without the refund and dispute events nothing breaks, but money that leaves
through Stripe other than by the refund page goes unseen here.

**An endpoint made before 17 Oct 2026 does not send `payment_intent.succeeded`.**
Add it to the existing one: Dashboard → Developers → Webhooks, open the
endpoint, edit it and add the event. Then run `python tools/stripe_webhook.py
--list` the way step 2 above runs `--create` (the key needs Webhook Endpoints:
Read for it) and check the endpoint still shows version 2026-07-29.dahlia and
one more event than before.

Why the webhook matters: the booking is created both when the guest returns
from Stripe *and* by the webhook, so a guest who pays and immediately closes
the browser still gets their booking. A unique index on the Stripe session id
means those two paths can never produce a duplicate.

---

> **THE LIVE SITE IS `cdg1-production-9186.up.railway.app`.**
>
> There are two Railway services deploying this repo and their names
> differ by four digits. `cdg1-production.up.railway.app` — no 9186 —
> is the other one: it has **no volume**, so its database is wiped on
> every deploy, and it can be running old code while the real site is
> current. Checking the wrong one has now cost two afternoons; the
> giveaway is `/status`, which answers on the real one and 404s on a
> stale build.

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
     `python -c "import secrets; print(secrets.token_hex(32))"` (on Windows; `python3` on Mac/Linux)
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

     **Put these on Railway, not in the `.env` on a development machine.**
     The local database is a copy of the real one, with real guest addresses
     in it, and the nightly jobs send — balance reminders, review
     invitations, the daily summary. A key in a local `.env` means the next
     `python app.py` on a laptop writes to actual guests from the château's
     verified domain. (The test suite is safe either way: `tests/_harness.py`
     blanks the key, replaces the transport with one that raises, and asserts
     both before a single test runs.)

     Once it is set, prove it before it carries anything: **Emails → Held
     email → "Send a test message to myself"**. It goes to your own address
     and nowhere else, and if the provider refuses it the page shows the
     reason Resend gave — "the domain is not verified", "the from address is
     not on the domain" — rather than a log line nobody reads.
   - **To turn on real payment collection at booking time**: `STRIPE_SECRET_KEY`
     and `STRIPE_PUBLISHABLE_KEY` from your Stripe Dashboard → Developers →
     API keys (use the test-mode keys first to try it safely, live keys once
     you're ready for real charges). Also add `STRIPE_WEBHOOK_SECRET` from
     Dashboard → Developers → Webhooks — point the webhook at
     `https://<your-domain>/webhooks/stripe` listening for the events listed
     under "In production" above; this is what reliably creates the
     booking even if a guest closes their browser right after paying, before
     they'd otherwise land back on your site. Without these set, booking stays
     the current request-only flow with no payment step.
   - **To turn on automatic calendar syncing** (pulling Airbnb/Booking.com/VRBO
     calendars every 1-3 hours instead of only when someone clicks "Sync all
     calendars" by hand): `ICAL_SYNC_TOKEN` — generate one the same way as the
     secret key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
     See **Scheduling automatic calendar sync** below for how this gets used.
     Without it set, `/api/sync-ical` always returns 404 and syncing stays
     manual — nothing changes from how it works today.
   - **To turn on the Vault** (Management → Vault, for shared logins like
     supplier portals or banking): `VAULT_ENCRYPTION_KEY` — generate one with:
     `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
     Entries are encrypted with this key before they're stored — losing the
     key means losing everything in the vault permanently, so back it up
     somewhere separate from the database (a password manager's secure notes,
     for instance). Without it set, the Vault page just says it isn't
     configured yet.
5. Railway gives you a URL like `cdg1-production-9186.up.railway.app`
6. **Point your own domain at it** (optional but nicer): in Railway, add a
   custom domain like `staff.chateaugudanes.com`, then add the DNS record
   Railway gives you into your Squarespace domain settings
   (Squarespace: Settings → Domains → DNS Settings)

**Important — Railway's disk is not permanent.** Everything in the repo
directory is rebuilt on each deploy, so the database, the uploaded documents
and the room photos must live on a *persistent volume* or they are destroyed
on the next push. Do this **before** anyone puts real data in.

In Railway: **Settings → Volumes → New Volume**, mount path `/data`. Then set
one variable:

```
GUDANES_DB_PATH=/data/gudanes_hr.db
```

That single setting moves all of them: uploads, room photos and the resized
copies of photographs all default to sitting beside the database, so they
follow it onto the volume. (They can be split out with `GUDANES_UPLOAD_DIR`,
`GUDANES_ROOM_PHOTO_DIR` and `GUDANES_PHOTO_SIZE_DIR` if you ever want them
elsewhere, but there's no reason to.)

The resized copies are the one folder here that can be safely deleted: every
size is made from the master on first ask and kept, so losing them costs one
slow page load each and nothing else. They are on the volume because making
them again on every view is wasteful, not because they are precious.

**Photographs from the camera.** The GH5 sends over Wi-Fi to a shared folder
on a machine at the house — Wi-Fi Function, New Connection, *Send Images
Stored in the Camera*, then PC, so only the frames picked on the camera's own
screen are sent. Nothing about that can reach Railway, so `tools/photo_watcher.py`
runs on that machine and forwards what lands to `/api/photographs`. It is
stdlib only, so it runs on whatever Python is already there.

Its token is generated on first boot and lives in `app_settings` under
`camera_ingest_token`. Read it out of the database and paste it into
`tools/photo_watcher.ini` beside the script — never into chat or email:

```
[watcher]
folder = C:/LumixDrop
url = https://<the château>/api/photographs
token = <from app_settings>
every = 20
```

`--once` does a single pass, which is the way to test the setup. The watcher
never deletes anything: the drop folder belongs to the camera, and tidying it
is a person's decision.

**Publishing to Instagram and the Page.** No App Review, and nobody at Meta
approves anything: the house posts to its own accounts, which Standard Access
covers. What it needs:

1. The Instagram account set to **Business** and linked to the Facebook Page.
2. A Meta app at developers.facebook.com, of type Business. Its **App ID** and
   **App secret** are under *App settings → Basic*.
3. A token from the **Graph API Explorer**: pick the app, *Get User Access
   Token*, and tick the permissions the connect page lists. They are kept in
   one place, `META_PERMISSIONS_TO_TICK` in `app.py`, so the page and this
   note cannot disagree.
4. The Page ID, the Instagram user ID, the token, the App ID and the App
   secret go into the connect page (`/management/social/connect`, linked from
   the social list). Save.

Saving asks Meta about the token straight away and swaps it for the Page's own
token, which Meta's documentation says has no expiration date. It has to be
straight away: the Explorer's token lasts about an hour. After that the
`meta_token` job asks Meta about it every day. A token Meta says is dead is a
blocker on the owner home, and a day Meta cannot be reached is recorded as a
failed run, so two in a row become a task.

Then approve one post and press **Publish now**, which proves the whole chain,
and only then switch publishing on under Automation. It stays off until
somebody does.

The one thing that still needs a person: Meta's data access lapses 90 days
after whoever made the token last authorised the app, and Instagram's two
permissions are not on Meta's list of those that survive it (the Page's are).
The owner home says so two weeks ahead, by Meta's own date. The fix is step 3
again and a paste; the app does the rest.

The token and the secret are held in the database, shown only by their last
four characters and never written to the audit trail. Being in the database,
they are in every backup of it, so keep the backups as safe as the keys.

**How production actually runs.** `python app.py` is the development server
and is not used here. Railway reads `Procfile`, which starts gunicorn against
`wsgi.py`. That file exists because creating the database and starting the
automation loop happen in `app.py`'s `if __name__ == "__main__":` block, which
a WSGI server never executes — pointed straight at `app:app`, the app would
boot with no tables and 500 on every page, and would never send a balance
reminder or daily digest.

It runs **one worker with eight threads**, deliberately. The automation loop
must exist exactly once per deployment: two workers would mean two loops, and
every guest would get each automated email twice. Scale with threads, not
workers. (`GUDANES_NO_AUTOMATION=1` disables the loop on an instance, if a
second one is ever added purely to serve traffic.)

**Getting in the first time.** On its first boot against an empty volume the
app creates the owner account and prints a generated password into the deploy
log:

```
======================================================================
FIRST RUN — owner account created.
  Email:    owner@chateaugudanes.com
  Password: <generated>
======================================================================
```

Read it under **Deployments → View logs**. With no email provider configured
there is no password-reset route yet, so this is the only way in. Log in and
change it straight away.

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

## Outlook add-in (the whole guest beside their email, replies drafted from it)

Owner page: Settings → Outlook add-in (`/admin/outlook-addin`). It shows what
is set up, what works on which device, who is signed in, and the steps below
with this deployment's own addresses filled in.

**What it does.** A panel beside a guest's email — on Windows, Mac, the web,
iPhone and Android — with everything about them: warnings first (a caution,
a request to stop using their details, a review nobody answered, an email
waiting, money owed), every booking under any address they are known by,
their profile, notes and follow-ups, every email with them across all the
inboxes the mail reader can see, and for the owner their account. "Draft with
AI" writes a reply from all of it plus today's real prices; the check on Send
(and a "Check before sending" button) catches a wrong price or date.

**On phones**, Outlook opens add-ins only while *reading*: no compose panel,
and Microsoft does not run send checks there. So on a phone the reply is
drafted from the read panel and copied in, "Check this reply" is a button
there, and a note about the guest appears at the top of a reply as soon as
they are in To. The Sent Items scan (Inbox Flags) checks every reply sent
from a phone within fifteen minutes with the same engine.

### 1. Sign-in with Microsoft 365 (`ADDIN_CLIENT_ID`)

Nobody pastes a shared key any more. In the Entra admin centre:

1. App registrations → New registration → "Gudanes Outlook add-in", this
   organisation only.
2. Authentication → Add a platform → **Single-page application**, redirect
   URI `brk-multihub://<the site's host>` (e.g.
   `brk-multihub://web-production-xxxx.up.railway.app`; add the real domain
   too at switchover).
3. Expose an API → set the Application ID URI to `api://<client id>` → add a
   scope `access_as_user` (admins and users can consent).
4. API permissions → Add → My APIs → this app → `access_as_user` → **Grant
   admin consent**.
5. Railway: `ADDIN_CLIENT_ID=<the client id>`. The tenant is
   `MS_GRAPH_TENANT_ID` (set `ADDIN_TENANT_ID` only if it differs). No secret.

Then on the add-in page, link anybody whose Microsoft 365 address is not the
one they log in to the dashboard with (the owner signs in to Outlook as
benw@). The owner sees money; everybody else does not. Each Outlook a person
signs in from is listed and can be signed out.

`GUEST_LOOKUP_TOKEN` still works as a fallback for an Outlook too old to sign
in this way — as staff, never with money — until the owner unticks it on the
add-in page.

### 2. Put it in everybody's Outlook

Microsoft 365 admin centre → Settings → **Integrated apps** → Upload custom
apps → Office Add-in → "Provide link to manifest file" →
`https://<host>/outlook-addin/manifest.xml` → assign (everyone, or the people
who answer guests) → Fixed → Deploy. It appears within hours (Microsoft says
up to 24). When the add-in page shows a new manifest version, choose
**Update** on it in Integrated apps.

### 3. Email history and drafting

- Email history uses the mail reader already set up (`MS_GRAPH_*`,
  "Microsoft Graph inbox monitoring" below) — every mailbox in
  `MS_GRAPH_MAILBOXES`. Nothing more to set. accounts@ is the owner's alone
  by default; the add-in page changes that per inbox.
- Drafting needs `ANTHROPIC_API_KEY`. It uses Claude Opus 5.5, with
  Anthropic's server-side refusal fallback on.
- Who signs a draft from each inbox (reservations@ = The Château Team,
  experience@ = Jasmine, ariege@ = Karina, accounts@ = Accounts) and what the
  drafter should know that the website does not say are on the add-in page.

### 4. The check on Send

Every email going outside the house is checked when Send is pressed, on
Windows, Mac and the web (Microsoft runs no send checks on phones; the Sent
Items scan covers those). Mail only to @chateaugudanes.com goes at once.

- **PromptUser, never SoftBlock or Block.** Under SoftBlock, Outlook's own
  "taking longer than expected" dialog offers only Don't Send. On 14 October
  2026 that stopped every email in Outlook on the web: the event page never
  called `Office.onReady()`, so office.js never started and no handler ran.
  With PromptUser, Send Anyway is on every one of Outlook's dialogs, whatever
  the add-in does. A wrong price still stops the email, with the right one
  named, and the person has to choose to send it anyway.
- **Three seconds** (`ADDIN_SEND_BUDGET_MS`), counted from the handler's first
  line. If it cannot check in time, it says so and the person decides.
- **It signs itself in** through Microsoft 365 (nested app authentication),
  using `ADDIN_CLIENT_ID`. It does this when an email is opened, so Send
  rarely has to wait. It no longer needs a panel to have been opened first.
  Microsoft's sign-in library travels inside the event script, because
  classic Outlook on Windows loads that one file and nothing beside it. It is
  kept at `static/addin/`, held to the pinned fingerprint.
- **No dashboard login needed for the check.** An @chateaugudanes.com account
  that nobody has linked to a person gets a key for the check on Send and the
  note in To, and nothing else. Microsoft 365 vouches for the account. The
  guest panel still needs a dashboard login linked on the add-in page. These
  keys are listed under "Checked on Send, without a dashboard login" there,
  each with Sign out. An account at another domain, such as a guest account
  in the house's Microsoft 365, is told it is not one of the house's own.
- **Classic Outlook on Windows** may only call the app because
  `/.well-known/microsoft-officeaddins-allowed.json` names the event script.
  Without that file every request it made was refused before it left the
  machine.
- **Every check is logged** with its time. Any check that could not run is
  listed on the owner home and becomes a task. Both go away once that
  Outlook's next email is checked. The add-in page's "Checks on Send"
  section has the details.

What is sent to Anthropic, and what is not, is in the privacy notice
(`templates/privacy.html`, "Who Else Is Involved"), and `test_privacy`
checks the code against it.

---

## The house calendar (Microsoft 365)

Owner page: Settings → House calendar (`/admin/house-calendar`).

One Microsoft 365 calendar — the house mailbox's own default calendar, listed
as "House" in Outlook's directory — that the app writes
every thirty seconds — stays, dinners, ateliers, events, tasks and the rota,
exactly as `/calendar` shows them — and everybody reads in Outlook on their
computer and phone. Names, rooms, dates and extras only: a stay's event lists
its nights and what the house is to have ready, never a price or a note. Each
event's "Open in the app" link opens `/calendar` on that day with the event's
card showing (`#ev-<card>`). Checked against Microsoft's
copy each night between 02:00 and 06:00. Failures show on the owner home and
become a task.

It is written by its **own** app registration, because an Exchange
application access policy limits an app to a set of mailboxes for every
permission it holds: giving the mail reader `Calendars.ReadWrite` would let
it write into every inbox it reads.

1. A mailbox for it: a shared mailbox such as `house@chateaugudanes.com`
   (no licence needed).
2. Entra → App registrations → New → "Gudanes House Calendar". API
   permissions → Microsoft Graph → **Application** → `Calendars.ReadWrite`,
   and `MailboxSettings.ReadWrite` for the area colours. Grant admin consent.
   Certificates & secrets → new client secret.
3. Limit it to that one mailbox (Exchange Online PowerShell):

   ```powershell
   Connect-ExchangeOnline
   New-DistributionGroup -Name "GudanesHouseCalendar" -Type Security -Members house@chateaugudanes.com
   New-ApplicationAccessPolicy -AppId <calendar app client id> -PolicyScopeGroupId GudanesHouseCalendar@chateaugudanes.com -AccessRight RestrictAccess -Description "House calendar app: the house mailbox only"
   Test-ApplicationAccessPolicy -Identity house@chateaugudanes.com -AppId <client id>     # Granted
   Test-ApplicationAccessPolicy -Identity accounts@chateaugudanes.com -AppId <client id>  # Denied
   ```

   It can take up to an hour to apply; until then Graph answers 403.
4. Railway: `MS_CALENDAR_CLIENT_ID`, `MS_CALENDAR_CLIENT_SECRET`,
   `HOUSE_CALENDAR_MAILBOX`. Redeploy.
5. On the house calendar page, press **Share with everybody, read only**.
   If Microsoft lists no organisation-wide entry, run the line the page
   gives instead:
   `Set-MailboxFolderPermission -Identity "house@chateaugudanes.com:\Calendar" -User Default -AccessRights Reviewer`
   Each person adds it once: Outlook → Calendar → Add calendar → From
   directory → House → Add. It then appears in Outlook on their phone too.

The app writes into the mailbox's **default** calendar, never a second one:
From directory adds a mailbox's default calendar and nothing else, and a
shared mailbox's other calendars can only be handed out by an invitation sent
from inside it. Until 16 October 2026 it wrote into a second calendar,
"Château de Gudanes — House", which nobody could add. When the calendar's id
changes, the next pass makes each event once in the new calendar and takes the
app's events out of the old one; the old calendar itself is left for the owner
to delete, and the page says so.

The same page makes a token-protected `.ics` link (`/calendar/house.ics`,
and one per area) for a phone without Microsoft 365, and can replace the
token, which stops every old link. The per-room feeds Booking.com reads are
unchanged and carry no guest names.

---

## Office display (wallboard)

`/admin/display` is a self-contained, auto-refreshing dashboard meant for a
permanent screen in a staff area — occupancy, who's on shift, today's
tasks, and anything needing a decision, reloading itself every 60 seconds.
Opening "Open Office Display" from the Home page while logged in as owner works
immediately, but a real login session expires after 12 hours — fine for a
quick check, not for a screen meant to run unattended for weeks.

For a permanent display, set `OFFICE_DISPLAY_TOKEN` (generate it the same way
as `ICAL_SYNC_TOKEN` above) and point the kiosk's browser at:

```
https://<your-domain>/admin/display?token=<your OFFICE_DISPLAY_TOKEN>
```

That URL never needs a login and never expires, so it's safe to leave open
on a dedicated device indefinitely. Without `OFFICE_DISPLAY_TOKEN` set,
`/admin/display` still works fine for a logged-in owner — it just has no
token-based alternative yet, so an unattended screen would eventually drop
to the login page.

---

## Resend delivery reports (bounces and spam complaints)

Without this, "Sent" in the mail log means only that Resend accepted a letter.
With it, each letter's line says Delivered, Bounced, Marked as spam or Delayed,
and an address that bounces for good (or whose owner marks a letter as spam) is
put on the stop list on Guest email, so the house stops writing to it until
somebody chooses "Write to them again".

1. In Resend: **Webhooks → Add endpoint**. URL:
   `https://<your site>/webhooks/resend`. Events: `email.delivered`,
   `email.bounced`, `email.complained`, `email.delivery_delayed`.
2. Copy the endpoint's **signing secret** (it starts `whsec_`).
3. Set it on Railway as `RESEND_WEBHOOK_SECRET`, and redeploy.
4. Guest email → "Is mail going out?" says **Reporting**, and shows when the
   last report arrived once a letter has gone.

Calls without a valid signature, or more than five minutes old, are refused.

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
6. **Restrict it to just the mailboxes you want scanned.** As granted above,
   this app can technically read *every* mailbox in your Microsoft 365
   tenant. Lock that down with an Exchange Online **Application Access
   Policy** — this needs PowerShell (`Install-Module -Name
   ExchangeOnlineManagement` if you don't have it already).

   The policy points at a **security group**, not at a fixed list of
   addresses. That matters: you create the group once, and adding an inbox
   later is a one-line command — the policy itself never needs rebuilding.
   List every inbox you run here (restaurant, bookings, experience, ariège,
   accounts, and any others):
   ```powershell
   Connect-ExchangeOnline
   New-DistributionGroup -Name "GudanesInboxScanMailboxes" -Type Security -Members `
     accounts@chateaugudanes.com, ariege@chateaugudanes.com, experience@chateaugudanes.com, `
     bookings@chateaugudanes.com, restaurant@chateaugudanes.com
   New-ApplicationAccessPolicy -AppId "<Application (client) ID from step 2>" `
     -PolicyScopeGroupId "GudanesInboxScanMailboxes" -AccessRight RestrictAccess `
     -Description "Gudanes HR inbox scan - restricted to the listed mailboxes"
   ```
   **To add another inbox later** (no policy changes needed):
   ```powershell
   Add-DistributionGroupMember -Identity "GudanesInboxScanMailboxes" -Member newinbox@chateaugudanes.com
   ```
   This is the step that turns "an app that can technically read anyone's
   mail" into "an app that can only ever read the château's own inboxes" —
   worth doing even though it's the most technical step here. If PowerShell
   feels unfamiliar, this is a good one to do with Claude Code open next to
   you, or with Microsoft 365 support on a call.
7. Set these as environment variables wherever the app is hosted (Railway →
   Settings → Variables, same as the other integrations above):
   - `MS_GRAPH_TENANT_ID`
   - `MS_GRAPH_CLIENT_ID`
   - `MS_GRAPH_CLIENT_SECRET`
   - `MS_GRAPH_MAILBOXES` — every inbox to scan, comma-separated. Any number:
     `accounts@chateaugudanes.com,ariege@chateaugudanes.com,experience@chateaugudanes.com,bookings@chateaugudanes.com,restaurant@chateaugudanes.com`
     (The older singular `MS_GRAPH_MAILBOX` still works if you only run one inbox.)
     Each inbox appears on Management → Inbox Flags, where you set who handles it —
     new flags are then assigned to that person automatically.

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
