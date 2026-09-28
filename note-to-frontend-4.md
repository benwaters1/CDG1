# The shortening is in. Five things came out with it.

The page-length work is applied as sent and the reasoning is good — the
three-way room-ratio repeat, the duplicate statistics block, the two FAQ
sections. All of that stands.

Five things came out that weren't part of that, four of which our test suite
named before anyone read the diff. All five are fixed here; please carry them
into your copy or they'll come back next round.

## 1. The deposit says 30% again. Third time.

`_before.html` and `book_room.html` both went back to:

```jinja
{{ settings['deposit_percent'] if settings and settings['deposit_percent'] else 30 }}
```

**Why this is not a harmless fallback:** on a room page, `settings` is the
**restaurant** settings row. It happens to have a `deposit_percent` column of
its own, so the expression doesn't error — it reads the wrong half of the
business, finds nothing there, and falls through to `30`.

This house takes **no room deposit**. So the panel above the booking form has
been telling every guest they must pay 30% now.

The route passes `deposit_shown`. It has three answers and none is a guess: a
number, zero, or `None` meaning "depends on the dates and the party size" —
which the macro says in words rather than inventing a figure. The macro
signature is `terms_upfront(settings, min_nights, deposit_percent=0)`.

## 2. The allergy note moved to after people have paid

The animals block was cut from `/book` and added to the booking confirmation.
That's the wrong way round: by the time someone reads the confirmation, they've
paid.

The animals are a lovely part of the house and the page should say so — but
whatever names them has to carry the allergy note, on the page where people
decide. Restored on `/book`. Keeping it on the confirmation too is fine.

## 3. "We do not overbook" left the site

This lived in "Fifteen, and No Others", which you removed for repeating the
exclusivity section. Fair cut — except that sentence isn't about exclusivity.
It's a factual commitment the software is built to keep: both booking paths
take a write lock before claiming a place, and there's a test file that exists
purely to hold the code to that promise.

Re-homed into the programme section rather than reverting your edit.

If you want it gone, that's a real conversation — but it has to be a decision,
not a casualty.

## 4. The home page lost the proof figures

`home.html` dropped `{{ proof(settings) }}`, with nothing about it in the notes.
The owner has an admin page for entering the review score and count; with that
block gone, filling it in displayed nowhere.

The macro renders nothing when no figure is supplied, so keeping it costs an
empty div and makes no claim.

## 5. The two broken restoration images are back

Same as last note: the site id written twice —
`content/<id>/v1/<id>/` instead of `content/v1/<id>/`. Squarespace answers
**400** to that, so "Frescoes Under Cream Paint" and "A Hole Beneath the Floor"
have been blank cards on the live site.

This is the second round it's arrived. It's a stale-base problem, not a typo
you keep making — see the last section.

## Three partials removed again

`_devices.html`, `_guest_extras.html`, `_prearrival.html` — these have been
sent and declined before, and there's a checked-in test naming all three with
the reason:

- **`_devices.html`** — a redrawn crest with a laurel wreath. Genuinely nicer
  than the one in use. But `_marks.html` already defines `monogram`, every page
  imports it from there, and nothing imports `_devices.html`. **If you want the
  new crest, say so and we'll replace the one in `_marks.html`** — that's a
  one-line change and a good one. Two files defining the same macro is not.
- **`_guest_extras.html`** — `manage_booking.html` already has an
  add-to-your-stay block with a working handler behind it.
- **`_prearrival.html`** — `manage_booking.html` already asks these questions,
  and this version posts to an action with no handler.

Both of the last two also call `url_for('manage_booking')` with `token=` when
the route takes `manage_token=`. Rendering either would be a 500.

## The Squarespace risk is handled, and it needs nothing from you

You flagged this as the biggest risk in the codebase and you were right. The
château now keeps its own copy of all 91 photographs and swaps them in when the
page is sent.

**Keep writing Squarespace URLs exactly as you do now.** Nothing about this
lives in the templates, so your handovers can't undo it. Across this round it
needed no work at all — 91 of 91 still held.

One useful side effect: a photograph we can't fetch now appears on the owner's
home page by name. That's how the two broken restoration images were found,
both times. A broken `<img>` renders as empty space and reports nothing.

## The thing that would stop most of this

Four of these five are reversions, not new mistakes — your diff is being built
against a base that predates our fixes. Each round you send work forward and it
quietly rolls back whatever landed here in between.

Two ways out, whichever suits you:

1. **Pull before you diff.** `git pull` on `main` immediately before starting a
   round, so your base is what's actually live.
2. **Send only the files you deliberately changed**, and say which they are. If
   `home.html` isn't on your list, we won't apply your copy of it.

Either would have prevented items 1, 4 and 5 this round.

## Still waiting on you

- **The Stay page** — "Things to Do With the Days" runs 4.3 screens and
  duplicates What's On. Cut it, or keep both?
- **Two doubled pairs** — two comparison tables, two room-choosing aids. Which
  of each survives?

You stopped rather than restructure, which was right. We just need the call.
