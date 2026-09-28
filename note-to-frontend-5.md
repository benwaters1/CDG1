# Good round. Three things left, and two of them aren't your doing.

The ten pages are in. Taking `_before.html` and `book_room.html` from main
wholesale was exactly right — that's the deposit fixed for good, and it's the
first round where it didn't need touching afterwards.

The proof block on home is better where you put it (after the stay panel) than
where we'd restored it. Kept yours. Same for animals-before-the-booking-action
on Stay.

Credit where it's due on `cutsec.py` raising on a miss rather than returning
`None` — that's the change that stopped the next two mistakes, and it's worth
keeping that shape anywhere else you cut.

## Two files arrived byte-identical to last round's zip

`restoration.html` and `workshops_public.html` in this zip are **exactly the
same bytes** as in the previous one. Not similar — identical. So they carried
no change of yours this round, but they did carry back the two things we'd
already fixed:

- **`restoration.html`** — the doubled site id, third round now. After we fixed
  it, the file matched what was already committed *exactly*, which is the proof
  there was nothing else new in it.
- **`workshops_public.html`** — "we do not overbook", second round.

Neither is a mistake you made twice. Your "changed-only" is changed relative to
**your** tree, and these two are stale in it. Worth checking what else in your
working copy hasn't been refreshed since — anything byte-identical across two
zips is a file that's carrying old state forward.

## The three partials, third time

`_devices.html`, `_guest_extras.html`, `_prearrival.html` — byte-identical
again, removed again. There's a checked-in test naming all three with reasons.

**On the crest specifically: we want it.** The laurel-wreath drawing in
`_devices.html` is better than the one in use. What's being refused is two
files defining `monogram` — `_marks.html` already does, and every page imports
it from there.

**Send it as a replacement for the `monogram` macro inside `_marks.html`** and
it goes straight in. One file, one definition, better drawing.

The other two are genuinely covered: `manage_booking.html` already has an
add-to-your-stay block with a working handler, and already asks the pre-arrival
questions. Both of your versions also call `url_for('manage_booking')` with
`token=` where the route takes `manage_token=`, so rendering either would be a
500.

## The desktop/mobile thing is the most useful thing in your notes

Eleven of twenty-two pages over twelve screens on a phone, and every number
quoted all session was desktop. That reframes the whole exercise — Stay at 42.3
screens is a different problem from Stay at 28.4.

Reporting rather than failing in the audit is the right call while eleven pages
would trip it.

## Still outstanding

- **The Stay page** — "Things to Do With the Days", 4.3 screens, duplicates
  What's On. Cut, or keep both?
- **Two doubled pairs** — two comparison tables, two room-choosing aids. Which
  of each survives?

You've now fixed the cutter that made the "Side By Side" mistake, so these
should be safe to act on.
