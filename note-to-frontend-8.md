# The house takes bookings now, and the site still calls them requests

**What changed in the app today:** a room booking and an atelier registration
are **confirmed the moment the guest makes them**. No owner approval step. The
guest gets the real confirmation — the drawn letter, with the calendar invite
and their check-in link — straight away.

**Restaurant is unchanged.** A table is still a request the château reviews and
confirms. Leave every word of that alone.

The site does not say any of this yet. In several places it tells the guest the
opposite, in their own language, on the page where they are deciding.

---

## The rule this is an instance of

The site's copy is not decoration — it is a set of **claims about what the
software does**. When the software changes and the words do not, the site is
lying to a guest, and the lie is load-bearing: somebody reads "we confirm every
booking by hand, usually the same day", so they wait for an email that has
already arrived, or they do not book at all because they wanted certainty
tonight.

This is the same category as a confirmation letter saying "confirmed" over a
booking that was not. There is a test in this repo (`tests/test_booking_email`)
that exists solely because that happened once.

---

## What I already changed (please keep — do not revert)

I edited these because they were actively false, not because I wanted to touch
your files. Byte-for-byte, so you can see them in a diff:

### `templates/booking_confirmation.html`

The page asked `payment_status == 'paid'` to decide whether to say "Your stay is
booked". That was the right question when paying was the only way to be sure of
a room. It is the wrong question now — somebody who books without paying is
just as booked. **The column that answers "is this booked?" is `status`.**

| was | now |
| --- | --- |
| `{% if booking['payment_status'] == 'paid' %}` | `{% if booking['status'] == 'confirmed' %}` |
| `{%- if booking['payment_status'] == 'paid' %}` | `{%- if booking['status'] == 'confirmed' %}` |
| "We confirm every booking by hand, usually the same day." | "We will be in touch shortly." |

That last line is the *fallback* branch — see "the cases that still wait" below.

### `templates/book_room.html`

| was | now |
| --- | --- |
| `Pay &amp; request to book` | `Pay &amp; book` |
| "Added to your request. The château confirms availability and the final total." | "Added to your booking. The total below is what you pay." |
| "Every request is read by hand, usually the same day" | "Confirmed straight away, with your dates held" |

---

## What still needs you

I have deliberately not touched these — they are yours, and several need a
rewrite rather than a find-and-replace.

### 1. `templates/workshop_detail.html` — twice, and it is explicit

Line ~180 and line ~288 both say:

> **"Registration is a request, not an instant confirmation. We will confirm
> your place by email."**

That is now exactly backwards. It is an instant confirmation.

### 2. `templates/workshop_register.html` — line ~176

> **"This is a registration request, not an instant confirmation — the château
> will review and confirm it."**

Same. Also worth reconsidering the submit button's wording while you are there.

### 3. `templates/workshop_confirmation.html` — line ~24

> **"We will confirm your place by email."**

They have already had that email by the time this page renders.

### 4. `templates/book_room.html` — two leftovers I did not want to guess at

- Line ~237: the panel heading **"Your request"**. Probably "Your stay" or
  "Your booking", but it is a design label and the panel is yours.
- Line ~598 (a comment, so guests never see it, but it now describes a process
  that does not exist): *"the château reads every request, a decline refunds
  automatically"*.

### 5. Anywhere else selling the old promise

Search your copy for the *idea*, not the word — "we'll confirm", "by hand",
"usually the same day", "await", "review your request". I found the ones above;
you know where the prose lives.

**One thing worth adding, if it fits the voice:** the strongest thing this
change gives you is certainty at the moment of deciding. "Your dates are held
the moment you book" is now a true sentence and it was not before.

---

## The cases that still wait, and why the fallback branches must stay

**Do not delete the "awaiting confirmation" branches.** They are still reached,
just rarely, and when they are reached they are true. A booking stays pending
and lands on the owner's desk in two cases:

1. **The guest is under a standing instruction not to accept them.** The house
   can record that against a person; confirming refuses it. (I added the same
   check to ateliers today — it only covered rooms before, so somebody the
   house had declined could not book a bed but could book a week of lessons.)
2. **The room or the places went in the seconds while they were typing.**

Both are precisely when a person should look. So the branch stays, the guest is
told it is awaiting confirmation, and that is accurate for them.

The owner's own notification now says **"Booked —"** or **"Needs you —"** in the
subject line, so the ones that want a human stand out from the ones that do not.

---

## How to check your work

Nothing here needs the app running. The question for each line is the same one:

> If a guest read this sentence and then watched what actually happens, would
> they feel misled?

For rooms and ateliers the answer is now yes on every line quoted above. For
the restaurant it is still no — which is why the restaurant copy is correct as
it stands and should not be swept up in a find-and-replace for the word
"request".
