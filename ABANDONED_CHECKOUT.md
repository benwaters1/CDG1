# Abandoning checkout throws away everything the guest typed

`stripe_cancel()` returns a guest to the booking form with a clear message —
that part is right. But the `cancel_url` carries only `room_id`:

```python
cancel_url=url_for("stripe_cancel", room_id=room_id, _external=True),
```

So a guest who reaches Stripe, hesitates, and comes back finds an **empty
form**. Dates, name, email, phone, party size — all gone, and they have to type
it again to try a different card or think about it for five minutes.

That is the most expensive moment on the site to make someone start over.
People abandon checkout constantly and come back; the ones who have to re-enter
everything largely do not.

**Verified: `book_room()` already reads all six** — `arrival`, `departure`,
`name`, `email`, `phone`, `party_size` — straight off `request.args`. The
receiving end needs no change whatsoever; only the `cancel_url` and the
redirect in `stripe_cancel()`.

## The fix

```python
                    cancel_url=url_for(
                        "stripe_cancel", room_id=room_id, _external=True,
                        # Carry the guest's own details back with them. Without
                        # these the form is empty on return and they retype
                        # everything — at the one moment they are most likely
                        # to give up instead.
                        arrival=arrival.isoformat(),
                        departure=departure.isoformat(),
                        name=guest_name,
                        email=guest_email,
                        phone=guest_phone,
                        party_size=party_size,
                    ),
```

and in `stripe_cancel()`, pass them through:

```python
def stripe_cancel():
    flash("Payment was cancelled — nothing has been booked, and your details "
          "are still here.", "error")
    room_id = request.args.get("room_id", type=int)
    # Everything the guest typed comes back on the query string; book_room
    # already reads these names.
    keep = {k: request.args.get(k, "") for k in
            ("arrival", "departure", "name", "email", "phone", "party_size")}
    keep = {k: v for k, v in keep.items() if v}
    if room_id:
        return redirect(url_for("book_room", room_id=room_id, **keep))
    return redirect(url_for("book_rooms", **keep))
```

The flash wording matters too. "No booking was made" tells them what did *not*
happen; "your details are still here" tells them what they need to know to
carry on.

## One caution

This puts a name, an email and a phone number in a URL, and URLs end up in
browser history and referrer headers. That is a real trade — but the same
values are already in the Stripe session metadata and the form POST, and the
alternative is losing bookings. If you would rather not, the safer version is
to stash the details in the Flask session before redirecting to Stripe and read
them back in `stripe_cancel()`. Slightly more code, nothing sensitive in a URL.

**I would use the session version.** Same result, no personal data in a link
someone might share or a log might keep.
