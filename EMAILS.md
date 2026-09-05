# The emails — what is built and what your dev must do

## The finding

**Every message the château sends is plain text.** `send_email_via_resend`
posts only `"text": body`. So there are no buttons, no tappable directions,
and the manage link arrives as a bare URL a guest has to copy.

That is why the emails cannot market anything today: there is nowhere to put
an offer that someone can act on.

## What is built

  · **`_email.html`** — the wrapper, plus `email_button`, `email_fact`,
    `email_directions` and `email_extras`
  · **`email_booking_confirmed.html`** — the confirmation, as the worked
    example to copy for the rest

Written for MAIL CLIENTS, which is a different discipline from the website:

  · **tables, not flexbox or grid** — Outlook uses Word's rendering engine
    and supports neither. Verified: zero flex or grid in the output
  · **every style inline** — Gmail strips `<style>` blocks on some clients
  · **no web fonts** — Georgia and Helvetica are on every machine; the site's
    Cormorant would not load
  · **buttons are table cells** with a padded `<a>` inside. A styled
    `<button>` does not exist in email
  · fluid to 600px. Tested at 320, 390, 600 and 1024 — fits, no sideways
    scroll, every tap target 45px

## Directions

`email_directions` gives Google Maps **and** Apple Maps buttons, plus driving,
train and after-dark notes.

**The pin uses coordinates, not the address.** There is no street number, and
an address search drops people in the village square rather than at the gates
— which is exactly how guests end up in the wrong valley on a dark road. The
email says so in as many words.

Set `lat` and `lng` in settings; it falls back to 42.7847 / 1.6564. **Check
those against the actual gates** before this goes out.

## Marketing, where it actually works

The confirmation is the most-opened message you send, and today it ends after
the reference number — so the guest who is most willing to add a dinner is
given no way to.

`email_extras` puts one offer in: **dinner and the transfer, in the guest's
own words, with a button.** Two links, not five. It is a confirmation with an
offer in it, not a catalogue — and the copy explains that the same link
changes dates, flags an allergy or cancels, with no password.

## What your dev needs to do

1. **Send HTML alongside the text.** Resend takes both — add `"html": html`
   to the payload. A client that refuses HTML gets exactly what it gets now
2. **Render the template and pass it in.** `render_template('email_booking_confirmed.html', ...)`
3. **Copy the pattern** for workshop confirmed, workshop received, restaurant
   confirmed and the pre-arrival note
4. **Send yourself one** to Gmail, Outlook and Apple Mail before it goes live.
   Email rendering cannot be trusted to a headless browser, mine included

## One thing worth knowing

My fixture passed `nights=4` with dates three nights apart, and the email
printed "4 nights" without complaint. **A confirmation that contradicts its
own dates is worse than one that says less**, so the sentence no longer
asserts a count it cannot check. If you do want the count, derive it from the
dates in the route rather than passing it.
