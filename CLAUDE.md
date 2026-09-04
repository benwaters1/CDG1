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
- **Check the name before writing a new file, especially in `tests/`.** 263
  suites is more than anyone holds in their head, and a new one written
  straight to `tests/test_<topic>.py` will silently replace an existing suite
  covering the same ground. It happened: a route-level iCal suite landed on
  top of `test_ical_sync`, which already held the parser, the wholesale
  replace and the fail-safe — the whole file, gone, and the run stayed green
  because the replacement passed. `git status` showed `M` rather than `A`,
  which is the only thing that gave it away. Read that letter.

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
- **Coverage counts the ANSWER, not the request.** It used to be recorded in
  `before_request`, so a page counted as covered if a test knocked and was
  turned away — a login redirect, a 403, or a 405 on a POST-only route. It
  read 100% while 38 routes had never once run their working branch: twelve
  deletes only ever refused, three declines only ever 403, the Stripe return
  pages only ever 404. A page now counts only if a view answered, and any
  page reached without answering is NAMED at the end of the run. The ones
  outstanding are listed in `COVERAGE_KNOWN_GAPS` in `tests/run.py` with the
  reason; the list is checked in both directions, so a new one reds the run
  and so does one that starts answering and is left on it.
  - `logout` is the single exemption, because its success IS a redirect to
    the login page. Anything else that sends people to log in is a refusal.
- **Write the negative control.** After a test passes, break the code
  deliberately and confirm the test catches it. A test that cannot fail is
  worse than no test — it reads as cover.

## Conventions the code already commits to

- **Stdlib only for HTTP.** No `requests`. Everything uses `urllib.request`.
- **Raw `sqlite3`, no ORM.** Migrations are `(key, ddl)` tuples wrapped in
  `try/except sqlite3.OperationalError: pass` so they're safe to re-run.
- **What day it is. Three right answers, and the return type picks.**
  A *moment* is a datetime and is stored in UTC. A *day* is a date and
  belongs to the house: `house_today()` / `house_today_iso()`. The
  restaurant's day ends at 05:00, so the till asks `service_day()`, which
  winds local time back past `POS_SERVICE_ROLLOVER_HOUR` — 01:30 Wednesday
  is still Tuesday's service. `service_day_window(day)` gives the UTC
  instant pair (23h/25h on clock-change nights).
  - `datetime.now(timezone.utc).date()` is never right. Between midnight
    and 02:00 in the Ariège it is yesterday. It was written 125 times, and
    95 other sites spelled the correct answer out longhand — which is why
    it kept coming back: both spellings were common, so neither looked
    wrong. There is now one definition and `test_house_day` fails on a
    second, in either spelling.
  - **Never `stamp[:10]` to get a day.** That reads UTC too, so anything
    recorded after midnight local carries yesterday's date all of the next
    day — filed under the wrong heading, aged a day out, invoiced on the
    wrong date. Use `house_date()` / `house_date_iso()`.
  - The cost of getting it wrong is not cosmetic. It cashed the till out on
    the wrong day, ticked breakfast onto yesterday's list, and made the
    3am cash-up impossible: the close route demanded a confirmation box the
    page had already decided not to draw.
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

## Things that break quietly, so check them

These all failed silently once. Nothing errored, no test went red, and the
page rendered perfectly while doing the wrong thing.

- **`{% block robots %}`.** `public_base.html` defines it; 25 templates that
  show one person's booking, bill or account override it with `noindex`.
  If the base ever loses the block, all 25 overrides become dead markup and
  guests' pages quietly become indexable. `robots.txt` does not cover this —
  it asks crawlers not to FETCH a path, while a URL that leaks into a
  referrer or a forwarded email can be indexed without ever being crawled.
  Guarded by `test_noindex_meta`, which also checks the public pages are
  NOT noindex — that mistake is the more expensive one.

- **The privacy notice is a set of testable claims about this code**, not
  marketing copy. It says dietary and medical notes are deleted once the
  event is over, and that dead enquiries go after twelve months. Both are
  true because `run_health_notes_purge_job` makes them true. If you change
  what the app does with guest data, change `templates/privacy.html` in the
  same commit — and if you change the notice, make the code match. Shipping
  a notice that overstates what the software does is worse than having none.
  `test_privacy` checks the claims, not just that the page renders.

- **Every `<table>` goes in `<div class="table-wrap">`.** A wide table drags
  the whole document sideways on a phone and takes the header and nav with
  it. Fifteen pages missed this, six of them written in one sitting, which
  is what a convention nothing enforces gets you. `test_table_overflow` now
  enforces it on the source.

- **A check nobody opens is worth nothing.** Findings surface on the owner
  home (`owner_home_warnings`, a fortnight's window) and become tasks
  (`generate_watch_tasks`) so they reach the calendar. Those tasks CLOSE
  THEMSELVES: nothing in that set has a "done" action of its own, so every
  run rebuilds the picture and ticks off anything no longer true. Remove
  that half and the list becomes a record of every problem the house has
  ever had, which nobody reads twice — including the morning it lists a
  real one.

- **A bulk action must be the same action, done many times.** Every one of
  them started as a loop over the core helper — and the behaviour that lived
  in the single-item ROUTE therefore never happened in bulk. Declining ten
  bookings one at a time worked the room waitlist ten times; declining the
  same ten together worked it not at all, and a refund that failed at Stripe
  was reported to nobody. Nothing errored. If you add anything after a
  single-item helper call, put it in a function both paths call
  (`decline_one_and_follow_up`) rather than in the route.

- **And it must say what it did NOT do.** The shape is `if not row: continue`
  followed by a cheerful total: ten ticked, six done, "Approved 6", and the
  four are found weeks later. Worse is guessing — bulk confirm called every
  refusal a date conflict, so a standing instruction not to accept a guest
  was announced as a clash with another booking. `bulk_message()` is the one
  reporter: it names items rather than counting them, groups a shared reason,
  and is an error the moment anything is skipped. Use it for any new one.

- **The warnings panel must be able to be empty.** If it can never be empty
  it becomes furniture. There is a test for exactly that.

- **The site's photographs are not the site's.** 256 `<img>` tags across 40
  templates — the logo on every page included — point at a Squarespace CDN
  belonging to an account the house no longer publishes from. The day it
  lapses, every picture and the masthead go together. So the house keeps its
  own copy and **swaps it in at the response layer**, in
  `serve_our_own_photographs` — never by editing the tags. The public
  templates arrive from the design side as whole-file replacements, so 256
  edited tags would survive exactly one handover; done to the response it
  needs nothing from anybody and cannot be reverted by accident. Copies live
  on the volume (`MIRROR_DIR`, beside the database and the room photos), not
  in git. `hotlinked_urls()` reads the templates rather than a kept list,
  because a list goes stale the first time a handover adds a photograph — and
  going stale quietly is the whole failure. Coverage is on the owner home and
  at `/admin/photo-mirror`; `tools/mirror_images.py` does the bulk fetch.
  Guarded by `test_photo_mirror`, which never touches the network:
  `_harness.py` stands `fetch_one_image` down at import.
  - Two things this found on the way in, both of which had been live for
    months and neither of which errored: three URLs written flush against
    `{% endblock %}` in an `og_image` block, and two on the restoration page
    with the site id **doubled** — `content/<id>/v1/<id>/` — which 400 for
    everybody, so those two photographs were simply missing from a public
    page. A broken `<img>` renders as nothing and reports nothing.

- **Stripping a secret out of `os.environ` does not keep it out.** Importing
  `app` runs `_load_dotenv()`, which reads `.env` and puts every popped key
  straight back — so anything the harness clears before the import is live
  again by the time app.py wires it into a client library. That is how
  `stripe.api_key` stayed real through every test run while `_harness.py`
  said payments were pinned off: `stripe_enabled()` returning False was doing
  all the work, checked separately at every call site. Neutralise the module
  global AFTER importing app, and assert it. `_harness.py` now clears Stripe,
  Pennylane and the two mail transports and asserts all four at import; if you
  add a third-party call, add it there too. The suite runs against a copy of
  the REAL database, so "it only sends to test addresses" is never true here.

## Tone

Commit messages and user-facing copy say what changed and why it mattered,
in plain English, without jargon or hype. The owner's standing bar is
"nothing basic, all advanced" — build the real thing, not a stub. Summaries
back to them in dot points.
