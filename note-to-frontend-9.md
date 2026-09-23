# Some extras now ask the guest when, and two of your templates carry it

**What changed in the app:** an extra can be set to **ask the guest when they
would like it**. The first one is a crémant and charcuterie board, 80 euros,
three days' notice. A guest ticks it and chooses a day of their stay
("On arrival" or any later day), with an optional note for a time or a place.
The day is what puts it on the house's list of what it owes, and on the
calendar, on the right morning.

Two public templates carry this. **Take a fresh export
(`tools/export_for_design.py`) before your next round**, or the next zip will
take it back out. `tools/check_handover.py` will name it if it does, but the
export is what stops it happening.

## `templates/book_room.html`

Inside each extra's `<li>`, after its `<label class="g-check">`:

- **For an extra that asks** (`e['ask_when']`): a wrapper
  `<div class="g-extra-when" data-extra-when="{{ id }}">` holding
  - a `<select name="extra_when_{{ id }}">` whose first option is
    `value="arrival"`. The script fills in the other days from the chosen
    dates. It keeps `data-picked="…"` so a choice survives a form that comes
    back with an error.
  - an `<input name="extra_note_{{ id }}" maxlength="120">`.

  The names are what the server reads, so keep them. The wrapper is visible
  in the markup and the script hides it while its extra is unticked. That way
  somebody without JavaScript can still say when.
- **For an extra that needs notice** (`e['lead_time_days']`): a hint
  `<span id="extra_too_soon_{{ id }}">Too soon for these dates…</span>`,
  hidden unless the dates are too soon. When they are, the checkbox is
  `disabled`. This is driven by `too_soon` in the `/api/quote` answer, and
  on first draw by `initial_quote`. Before this, the form showed "3 days'
  notice" and then took the booking for tomorrow; the server now refuses it
  too.
- The script adds `fillWhen()`, called on start-up, on every tick and from
  `refreshQuote()`. It also adds a block at the top of the quote callback
  that shuts whatever `too_soon` names.

The styling is yours. `.g-extra-when` has no rules of its own: it sits in
`g-field` paragraphs and would welcome a little indent under its extra.

## `templates/manage_booking.html`

In the "Add to your stay" form, for an extra that asks: a
`<select name="extra_when">` of the days still to come, and an
`<input name="extra_note">`. The quantity box's id is now
`mb_quantity_{{ e['id'] }}`, one per extra. It was `mb_quantity` on every
row, which is a label pointing at whichever input the browser finds first.

## Also, and it changes nothing you draw

The booking form now offers **only extras marked for guests**
(`guest_bookable`). An item kept for the till was being offered to anybody
booking a room.
