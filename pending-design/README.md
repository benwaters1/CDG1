# Four partials held back, and why each one is not live

> **Second export, 2026-09-18 17:21.** A fresh zip arrived forty-five minutes
> after the first. `base.html` in it is still the initial-commit version — 0
> `url_for` calls, no CSRF meta tag — so it was cut from the same stale tree
> and nothing from it was installed either. Everything held below is
> byte-identical between the two exports. The one thing the second one carried
> that was worth keeping is `new-components.css`, described at the bottom.

All four arrived in the full export of 2026-09-18. None of them is broken and
none of them was rejected on taste. Each is held for a reason a person has to
resolve, and each reason is written out below so that resolving it is a small
job rather than a conversation.

**The export they came in was generated from a tree several months old.** The
design side's container was reset and the working directory restored from an
old zip — `tools/fullreview.py` says so in its own docstring. Installing it
would have reverted 107 commits and 4,097 lines, including `templates/base.html`
back to the initial commit: 161 `url_for` calls to zero, the CSRF meta tag
gone, and the staff navigation with it. Nothing from that export was installed
except these four files, `RESTORATION.md` and `tools/fullreview.py`, which are
new and cost nothing.

That staleness is also why two of the four are held: they were written against
a version of this app that no longer exists, and their stated reason for
existing is no longer true.

---

## `_summary.html` — `what_it_comes_to(booking, settings)`

**Held because its premise is false, and because it cannot render.**

Its own comment opens: *"The confirmation page shows the reference, the dates
and the guest count — and then nothing about money at all. No room name, no
rate, no total, no balance."*

`templates/booking_confirmation.html` line 40 prints the total. It has for
months. The sentence was true of the copy in their tree, which is the whole
problem with that export.

It also reads ten fields off `booking` and the database has four of them:

| the macro asks for | the column is |
|---|---|
| `booking.total` | `total_price` |
| `booking.paid` | `amount_paid` |
| `booking.balance` | `balance_amount` |
| `booking.tourist_tax` | `city_tax` |
| `booking.nights`, `rate_per_night`, `rooms_total`, `extras`, `manage_url` | derived, not stored |

None of those would raise. Jinja resolves a missing key to Undefined and
renders empty — so the panel would draw its headings and its rule lines with
no money in it, on the page a guest checks before they travel. That is worse
than the page as it stands.

**To land it:** the figures already exist, correctly, in `booking_bill(conn,
booking_id)` — which is the one definition of what a stay costs and knows
about extras, deposits, part-payments and the taxe de séjour. Feed the macro
from that rather than off the booking row, and decide what it adds beyond the
total that is already there.

## `_interactive.html` — `the_reveal(pairs)` and `the_ninety_four()`

**`the_reveal` is held because the photographs do not exist.** There is no
`static/reveal/` directory. The macro is right to render nothing rather than
reach for a placeholder, and `RESTORATION.md` sets out what a usable pair
needs — same position, same aspect ratio, three pairs rather than ten. That is
a morning with a camera, not a code change. When the pairs exist this goes
straight onto the restoration page; the design side already wired it there in
their own tree.

**`the_ninety_four` is held because nothing imports it**, in their tree or
ours, so there is no statement of where it belongs.

## `_glance.html` — `atelier_glance(w)`

**Held because it reads a column that does not exist.** It wants
`w.duration_label`; `workshops` has `nights_label`. That may simply be the
same idea under the older name — but renaming somebody else's field on a guess
is how a page ends up quietly blank, and the answer takes one line from them.

## `_explorer.html` — `restoration_explorer(...)`

**Held because nothing imports it.** Same as `the_ninety_four`: real work with
no statement of which page it is for.

---

## And one file that was not kept

`templates/bnbforms-widget-styled_1.html` — a script tag loading
`cdn.bnbforms.com` and drawing a "Book Your Stay" button that sends guests to
an external booking service. Nothing references it, in their tree or ours, so
it looks like a file that was in the folder when the export was made rather
than anything anybody meant to send.

It was not installed. Putting a third-party booking widget on the public site
routes guests away from the booking system this app exists to be, and hands a
CDN a script tag on every page — which is not a design decision to make by
accident. **If it is wanted, it is wanted deliberately, and it is the owner's
call rather than a handover's.**

---

## `tools/fullreview.py`

Kept as sent, unedited. It is the design side's own review harness — it renders
every template against a fixture so the pages can be measured in a browser.

**It will not run from this repository root as it stands:** it reads
`ALL/templates` and `ALL/static/gudanes.css`, which is their working layout, not
ours. Point `SRC` and `CSS` at `templates/` and `static/gudanes.css` to use it
here.

Worth reading either way. Its `Quiet` undefined class — a missing value renders
as nothing rather than raising — is exactly why `what_it_comes_to` would have
drawn an empty money panel rather than failing loudly, and is the reason that
macro is parked rather than live.

---

## `new-components.css`

The 977 lines of stylesheet the two exports carried for the five parked
macros — `.g-rex`, `.g-94`, `.g-reveal`, `.g-recap`, `.g-gl` — lifted out of
their `static/gudanes.css` rather than merged into ours.

**Why not merged.** Their stylesheet is 997 lines ahead on these components and
91 lines behind on everything else, and those 91 lines are: the road-notice
rules, their own phone variant of the approach map, and every
`@media (max-width: 34rem)` and `(max-width: 26rem)` condition — which is the
custom-property-in-a-media-query bug for the **fourth** time. Taking the whole
file would have traded three live fixes for styling that renders nothing,
because the components it styles are all parked.

`tools/repair_handover.py` now rewrites those conditions automatically, so the
fourth occurrence would have been repaired rather than shipped. It is still not
a reason to install a stylesheet that deletes working rules.

**How to use it.** When a partial is wired up, move its block from here into
`static/gudanes.css`. The file is brace-balanced and carries no custom property
in any media condition, so a block can be dropped in as written. Nothing in it
has been edited except the run boundaries, which were widened until each rule
closes — a diff boundary does not respect a rule boundary.
