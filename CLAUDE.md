# Château de Gudanes — working notes

Instructions for any Claude session working in this repo. This file is in
git deliberately: it travels with the code, so moving the folder or cloning
it somewhere new doesn't lose the rules. Anything path-keyed (session
memory) does not survive a move — this does.

## What this is

A single-file Flask app (`app.py`, ~33k lines) running a French château:
HR for staff, guest bookings for rooms/restaurant/workshops/events, a POS,
and the accounting that ties them together. Two-tier permissions — the
employee side stays simple (tasks, who's here, calendar), the owner side is
full business control.

Deployed to Railway from `main`. **Pushing to `main` deploys to production.**

## Rules that cause real damage if ignored

**Another agent edits this same tree.** A second Claude works on POS, stock
and invoices via zips the owner downloads and applies here.
- **Never `git add -A`, never `git commit -a`, never `git checkout -- .`**
  Stage explicit file paths only. A whole-tree checkout destroyed hours of
  the other agent's work once already.
- Handover zips are **literal overwrites, never merges**. "Replace
  byte-for-byte" means exactly that — do not reconcile with what's here.
- Show `git log -1 --stat` after every push so the owner can see what landed.

**Secrets.**
- `.env` is not in git and has no other copy. Don't print its contents.
- Generate secret values into files; never put them in chat or into a web
  form. Hand them to the owner to paste themselves.
- **The Pennylane token is live.** `test_pennylane_scan.py` replaces
  `_pennylane_request` with a function that raises. Keep it that way.
- Never issue a real Stripe refund. Building the capability is the job;
  moving actual money is the owner's.

**Tests.**
- `python tests/run.py` — whole suite. `python tests/run.py <substring>` for one.
- Runs against a throwaway copy of the database. It must never touch the
  real one, send real mail, or call Stripe. Mock `send_email` and Stripe.
- The suite has a **positive control** — a deliberately failing check that
  proves the harness can report failure. If it stops failing, the run is
  unproven, not clean.
- A green suite over half the app is the failure this guards against, so the
  run prints coverage. Adding a page without a check lowers it on purpose.
- **Write the negative control.** After a test passes, break the code
  deliberately and confirm the test catches it. A test that cannot fail is
  worse than no test — it reads as cover.

## Conventions the code already commits to

- **Stdlib only for HTTP.** No `requests`. Everything uses `urllib.request`.
- **Raw `sqlite3`, no ORM.** Migrations are `(key, ddl)` tuples wrapped in
  `try/except sqlite3.OperationalError: pass` so they're safe to re-run.
- **Service day.** `service_day()` winds local time back past
  `POS_SERVICE_ROLLOVER_HOUR` (05:00) — 01:30 Wednesday is still Tuesday's
  service. Never stamp a UTC calendar date where a service day belongs;
  that bug has been fixed twice. `service_day_window(day)` gives the UTC
  instant pair (23h/25h on clock-change nights).
- **Hours.** A clock-out before its clock-in poisons any bare `SUM` of
  hours. Always guard it. Never loop `net_hours` — batch it.
- **Money.** Gross vs net is stated on every figure, there is one definition
  of labour cost, and rows must add up to their total. `pay_rate`/`pay_type`
  are free text, so `estimated_hourly_cost()` is never a payroll figure —
  wages are a typed number the owner sets, not one the app infers.
- **Anything that becomes a task appears on the calendar automatically.**
  `build_overview` / `build_calendar` cells carry `events`, not `rows`.
- **List pages.** Every list gets `list_view()` — search, counted chips,
  sort. Never add another one-off search box.
- **The dev database holds live config.** Email templates and settings are
  data, not code. Editing them here persists and would ship to guests.
- **Guests are profiles; bookings are truth.** Stay dates never go back on
  the guests table.
- **Refunds are a manual call.** Non-refundable as standard with
  discretionary exceptions. A policy engine was considered and rejected.

## Tone

Commit messages and user-facing copy say what changed and why it mattered,
in plain English, without jargon or hype. The owner's standing bar is
"nothing basic, all advanced" — build the real thing, not a stub. Summaries
back to them in dot points.
