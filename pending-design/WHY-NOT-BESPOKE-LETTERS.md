# The two drawn letters, and why they are not installed

`email_workshop_confirmed.html` and `email_thank_you.html` arrived in the
export of 2026-09-19, along with four macros to support them: `ecopy`,
`email_head`, `email_note`, `email_signoff`.

They are good, they render, and they are not in. The reason is a decision
already taken on this side and written down in `tests/test_letters_drawn.py`,
which this repeats rather than overturns.

## The decision

**There is one set of words, and it lives in `email_templates`.** All
twenty-one messages the house sends are rows in that table, edited by the
owner at Management → Email templates. `letter_html()` wraps whatever words it
is handed in a shell: a line that is only a link becomes a button, a sentence
with a link in the middle stays a sentence, and the plain text remains what
was actually sent and what the house keeps as its record.

The drawn version is an **addition** to the words. It is never a second copy
of them.

## Why a bespoke letter breaks that

A file like `email_workshop_confirmed.html` hard-codes prose. The owner then
edits the workshop confirmation on the templates page — where they are told to
edit it, and where they can now see it render as a guest reads it — and the
drawn letter still says the old words.

The guest gets the new wording in plain text and the old wording drawn
nicely, and nothing anywhere reports it. `test_letters_drawn.py` names this
exact failure and names these exact letters:

> the handover asked for four more written the same way — workshop confirmed,
> workshop received, restaurant confirmed, the pre-arrival note. Written that
> way it would have been four second copies of the same sentences.

That was written before this export arrived. Installing them would silently
undo it.

## `ecopy` makes it worse rather than better

The new `ecopy` macro reads email prose from **settings** keys, falling back
to the wording in the template. It is a sound idea in isolation and it names
eight keys to expose.

But the house would then have **three** places the wording of one email can
come from: the `email_templates` row, a settings key, and the template's own
default. Somebody edits one of the three and the other two keep their version.
Two is already the problem this avoids; three is worse.

## What would make these land

The letters are wanted — they are better looking than the generic shell. What
they need is to take their words from the same place everything else does.

Concretely: a bespoke letter that accepts the rendered subject and body from
`render_email_template` and arranges THOSE, rather than carrying its own
sentences. Then the owner edits in one place, the plain text and the drawn
version say the same thing by construction, and the design work is kept.

That is a real piece of work and a good one. It is not a handover install.
