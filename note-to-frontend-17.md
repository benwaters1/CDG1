# For whoever hands over the public templates: the two statement pages can be paid from

19 October 2026. A guest booked by telephone was emailed their statement, whose
words say "the full statement, and the way to settle it, are on your statement
page". The page had Email and Print, and no way to pay. Three public files
changed here; please start the next handover from these copies, not your own.

**1. `guest_statement.html`: a Pay button with the other two.** In
`.statement-actions`, before the Email form:

    {% if v and v.owed > 0.005 and v.stands %}
    <a href="{{ url_for('booking_pay', manage_token=booking['manage_token']) }}"
       class="g-btn g-btn--sm g-btn--solid">Pay €{{ '{:,.2f}'.format(v.owed) }} now</a>
    {% endif %}

It is in `.no-print`, so it never prints onto the guest's own bill.

**2. `workshop_statement.html`: "Pay online" beside Print.** Same condition,
linking to `workshop_manage`, because the registration page knows whether the
deposit or the balance comes next.

**3. `_statement_charges.html`: the rows add up to the line under them.** It
loops over `v.added` (the charges) and then lists `v.taken_off` (each
reduction, by name) under the subtotal. It used to loop over `v.charges`, which
holds both, so a discount appeared among the charges AND came off again as
"Discounts and reductions", and the rows above the subtotal summed to less
than the subtotal said. Please keep `v.added` / `v.taken_off`.

Nothing else on these pages changed. The letters themselves (`_email.html`,
`email_letter.html`, `email_booking_confirmed.html`, `_email_statement.html`)
are drawn on this side and are not part of the public templates.
