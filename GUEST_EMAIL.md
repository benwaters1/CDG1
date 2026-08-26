# The confirmation email tells a paying guest their booking is unconfirmed

`create_booking()` sends every guest the same line:

```
Your request for {room} has been received and is awaiting confirmation.
```

But `book_room()` charges the **full amount** to the card before the booking is
created. So a guest who has just paid €450 is told their booking is *awaiting
confirmation* — which reads as "we might still say no, and we have your money".
That is the single worst sentence on the whole customer journey, and it arrives
at the moment a guest is most alert to it.

The row already knows: `payment_status` is `'paid'` when Stripe completed.

## The fix

In `create_booking()`, replace the fixed opening line with one that reflects
what actually happened:

```python
    paid = payment_status == "paid"

    if paid:
        opening = (
            f"Thank you — your stay in {room['name']} is booked and paid in full.\n\n"
            "There is nothing further to do. We will write again a few days "
            "before you travel with directions and arrival times.\n\n"
        )
        subject = f"Your stay is booked — {room['name']}"
    else:
        opening = (
            f"Your request for {room['name']} has been received.\n\n"
            "We confirm every booking by hand, usually the same day. You will "
            "hear from us shortly.\n\n"
        )
        subject = f"Booking request received — {room['name']}"

    send_email(
        guest_email,
        subject,
        f"Hi {guest_name},\n\n"
        + opening
        + "\n".join(detail_lines)
        + f"\n\nReference code: {reference_code}\n"
        f"See or change your booking any time: {checkin_url}\n\n"
        "— Château de Gudanes",
    )
```

Two things beyond the wording:

**The subject line matters as much as the body.** "Booking request received"
in an inbox, after a card has been charged, reads as a problem. "Your stay is
booked" does not.

**"Check in online, manage your booking, or send us a request" is three verbs
for one link.** A guest scanning an email needs one. "See or change your
booking any time" says what the link does.

## Only the room email is wrong

Three places say "awaiting confirmation". The other two — the atelier
registration and the dinner reservation — are correct as they stand and are
editable in the admin anyway: an atelier holds with a *deposit*, and a dinner
genuinely is reviewed before it is confirmed. Both really do await a decision.

A room does not. It is charged in full at checkout, which is why this one line
needs to change and the other two do not.

## Also worth doing

The owner copy of the same email uses `arrival.isoformat()` — raw `2026-10-14`.
Staff read these all day; `format_date_human()` is already imported and used
three lines above for the guest. Change both to match.

## The calendar file is attached to the wrong email

`generate_booking_ics()` is attached to the **"Booking confirmed"** mail sent
when staff approve a booking (line ~24139), but not to the confirmation the
guest receives immediately after paying.

That is backwards. The moment a guest adds a stay to their calendar is the
moment they finish booking it — not days later when an approval mail arrives,
by which time the first email has been archived.

Attach it to the paid confirmation as well:

```python
    send_email(
        guest_email,
        subject,
        body,
        ics_content=generate_booking_ics(
            {"arrival_date": arrival.isoformat(),
             "departure_date": departure.isoformat()},
            room["name"],
        ) if paid else None,
        ics_filename=f"gudanes-{reference_code}.ics",
    )
```

Only when `paid` — attaching a calendar entry to a booking that might still be
declined would put a stay in someone's diary that may not happen.

Verified: `generate_booking_ics()` reads only `arrival_date` and
`departure_date` from the row, so the two-key dict above is sufficient.

## Everything else is plain text

`send_email_via_resend` sends `{"text": body}` with no HTML part. That is a
defensible choice — plain text always renders, never trips spam filters, and
suits the house voice better than a templated banner would. I would leave it,
but it is worth knowing that the confirmation a guest gets looks nothing like
the site they just booked on.
