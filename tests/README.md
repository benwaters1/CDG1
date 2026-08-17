# Tests

```bash
python tests/run.py
```

That is the whole setup. No pytest, nothing to install, no separate test
database to create. It prints a PASS or FAIL line per check and exits
non-zero if anything failed.

To run one suite:

```bash
python tests/run.py design
```

## It cannot touch real data

The database holds staff records, guest addresses and password hashes, and
these tests write rows. So `_harness.py` copies the live database to a
throwaway file and points the app at that through `GUDANES_DB_PATH`. The copy
uses SQLite's backup API rather than a file copy, because the dev server may
have the database open and a copy taken mid-transaction can be corrupt. The
temporary file is deleted when the run ends.

The harness asserts the override actually took effect and refuses to run if
it did not, so this cannot fail open. Stripe keys are cleared from the
environment for the same reason: a test run must never reach the payment
provider, and a test that behaves differently depending on whether `.env`
happens to exist is a test that fails on someone else's machine for no reason.

## What each suite covers

| Suite | What it is for |
|---|---|
| `test_routes` | Renders all 132 owner-visible pages. The cheapest regression net there is — it catches template and context errors app-wide, including on pages with no seed data, which is where "works on my machine" 500s come from. Pages that take an id are loaded with a real record, because a nav link to `/management/vehicles/<id>/transfers` once 500'd the dashboard and nothing without a real row would have caught it. |
| `test_hr_compliance` | Incidents, role requirements, the access register, the payroll pack, and that employees are locked out of all four. |
| `test_campaign_email` | The guards, not the happy path: the typed-count confirmation that stands between a mis-click and mailing every guest; that a GET on an unsubscribe link does **not** opt anyone out, because mail clients pre-fetch every URL in a message; and that an opted-out guest is actually dropped from the next send. |
| `test_design` | Two faults that have each shipped here more than once — see below. |

## Why there is a design suite

Both faults it looks for are invisible rather than broken, which is why
clicking around never found them.

**A variant class the shell rule beats.** `.btn-mini-danger` is one class
(0,1,0) and `body.staff-shell .btn-mini` is a class plus an element plus a
class (0,2,1), so the base rule wins and the variant renders identically to
an ordinary button. That hid every delete button, and then separately hid the
selected state of every filter row, so nothing on the page showed which
filter was applied. The check knows a restoring rule at higher specificity is
a valid fix, but does not accept a `:hover` one — restoring the colour under
the cursor and nowhere else is the bug, not the fix.

**Text below WCAG AA.** Contrast is arithmetic, so it needs no browser. Two
of the failures here came from an `opacity` on a rule whose colours were
fine, which no colour-pair check can see. Genuinely decorative glyphs are
exempt but have to say so with `aria-hidden` — an element carrying no
information for a screen reader is not carrying any for a sighted reader
either, so it is allowed to recede.

It also checks that layout classes in the markup have a rule at all.
`.table-wrap` was used by thirteen templates with no CSS whatsoever, so wide
tables pushed the whole page sideways on a phone instead of scrolling inside
their wrapper.

None of this replaces looking at the page. It catches the class of fault that
looking at the page has repeatedly missed.

## Adding a suite

Write `tests/test_<name>.py` with a `run()` returning a `Suite`, and add the
module name to `SUITES` in `run.py`:

```python
from _harness import Suite, clients, db

def run():
    s = Suite("My thing")
    oc, ec, owner, emp = clients()
    r = oc.get("/admin/thing")
    s.check("the page renders", r.status_code == 200, r)
    return s
```

Two conventions worth keeping:

- **Tag rows and delete them at the end.** Suites must not depend on running
  in a particular order, or on each other's leftovers.
- **Pass the response to `check`.** On failure it prints the app's own flash
  message, which is what tells apart "the app broke" from "the app correctly
  refused" — the payroll export deliberately refuses when a shift is
  impossible or someone has no pay rate, and from a status code alone that
  looks identical to a fault.

`run.py` starts with a deliberately failing check as a positive control. A
suite that can only print PASS is worthless, and a broken assertion helper is
invisible precisely because everything looks green.
