# For whoever hands over the public templates: what z22 lost on the way in, and what is done

z22 (16 October) is installed, as a literal overwrite of the files in the zip.
Five things in it went backwards against the live site, because your copies of
those files are older than ours, and two needed finishing. All seven are done here. Please start the next
handover from these files, not from your own copies of them.

**1. `_room_copy.html`: the Twin and Queen labels, again.** z22 still said
"a twin room" and "a queen bed". They read, as `note-to-frontend-15.md` asked:

    'The Twin Room':    'two double beds, with a private bathroom',
    'The Queen Room':   'a king bed, with a private bathroom',

**2. `book_rooms.html`: the price for the chosen dates.** Your FOR-CODE item 5
is done, and has been since z17's requests were answered. With dates chosen,
each card shows what those nights cost, from `room_quotes`
(`{room_id: {"total", "per_night", "varies"}}`), and says "a night on average"
when the nights are priced differently. z22's copy went back to the standard
rate times the nights, so a one-euro night showed €265. Keep the
`{%- if _n > 0 %}` branch as it is in this file.

**3. `book_room.html`: check-in and check-out are read, never typed.** z22 typed
"3pm" and "10am" into the room page and the arrival-time choices. Those places
read `check_in_at`, `check_out_at` and `check_in_hour` (set once in the app as
15:00 and 10:00), so the times can never disagree with the terms. z22 changed
nothing else in that file, so it is the live copy unchanged.

**4. `contact.html`: "The experience: all meals and drinks, and the
activities".** The line was set with the owner on 16 October: no "full
experience", but the owner's own word, experience, stays. z22's copy said "All
meals and drinks, and the activities".

**5. `_cancel.html`: "Subject to what is available".** z22 said "what is free".
The owner, 15 October: "free" reads as "no charge". The repair tool changes it
on the way in, but please say "available".

**6. `workshop_register.html`: the deposit the server will charge.** Your new
amounts panel is in, with two changes so the page can never show one figure and
charge another:
- The percent comes from `deposit_by_party` (the route passes the percent for
  every party size), not from `session['deposit_percent']`. A deposit rule can
  ask a bigger party for more, and the charge follows the rule.
- A 0% deposit means nothing is paid today, as the server does it (only inside
  the 30 days is everything paid now). The panel says so and the button reads
  "Register". z22 treated 0% as "everything today".
Keep both in the next copy.

**7. `facilities.html` and `story.html`: the allergy line goes with Bruce.**
Bruce is now in the Estate grounds gallery and on the Family page. Wherever the
animals are named, the house's allergy line goes with them (`animals_note()`
from `_animals.html`), so it now follows each of those galleries. Your
`bruce_card()` already carries it, which is why Stay was fine.

## Done in the app (your FOR-CODE)

- 13, the History page: `/history` is a route (`history_page`). It is no longer
  an old address sent to the Restoration page, and it is in the sitemap.
- 8, translations: "Eight centuries at Gudanes", "The fortress", "The château,
  by Gabriel" and "The History" in French and Spanish ("The restoration" was
  already there), and the top bar's new "What's On" (Que faire / Qué hacer).
- 12, the first part: the route hands back `prefill_occupancy_type`, so the tick
  survives a validation error once the room-to-myself choice is switched on.
- 2 to 7 were already done before z22 arrived: never "free" to a guest, "five
  guest rooms" in the letters (and in the one stored on the live site), check-in
  15:00 / check-out 10:00 everywhere, the Stay list priced for the dates, Stripe
  payment descriptions, and the room form keeping what it does not show.
- C8, disputes: the site never asks Stripe about a dispute; it records what the
  webhook sends. The restricted key needs no Disputes permission.
- 14, Bruce's photographs: please do not copy them into `static/img/` or change
  their addresses. The site keeps its own copy of every Squarespace photograph
  and serves that instead, at the response layer, so a handover cannot undo it.
  The five new ones are picked up within the hour of going live.

## Not done yet (waiting on the owner)

- 12, a room to yourself online (`solo_bookable`): it moves money, so it waits
  for the owner's go-ahead. Until then the page offers sharing only, as you built it.
- 15, the Booking.com feed (workshops with a day either side, blocks, the
  one-year window) and 16, extending a workshop stay with nightly nights: both
  change what the house sells, so each is its own piece of work. Until 16 lands,
  "Stay a little longer" shows "Choose your nights", and the night a workshop
  ends is still held by the workshop on this site.
- C9, "Karina" in stored text: nothing guest-facing in the copy of the database
  held here. The live database is not checked.
