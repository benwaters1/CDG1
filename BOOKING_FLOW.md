# Booking flow — one real bug, and the messages a guest sees

I walked `book_room()` end to end. The validation is genuinely thorough:
past dates, occupancy, minimum nights, valid email, terms, house capacity,
promo codes and Stripe failure are all handled. Two things to fix.

## 1. Reversed dates give the wrong message

If a guest picks a departure BEFORE their arrival, `(departure - arrival).days`
is negative, which is less than `min_nights` — so they fall into the
minimum-stay branch and are told:

> *"This room requires a minimum stay of 2 nights."*

Which is true, but it is not their problem, and it does not tell them what to
do. They will try again with the same reversed dates.

**Fix** — add one branch before the min-nights check in `book_room()`:

```python
elif departure <= arrival:
    error = "Your departure date needs to be after your arrival date."
```

Place it directly after the `arrival < today` check. The same branch is worth
adding to the dinner and workshop routes if they compute nights the same way.

## 2. "Choose valid arrival and departure dates" is vague

It fires when a date is missing or unparseable, and the guest cannot tell
which. Two clearer messages:

```python
elif not arrival:
    error = "Please choose your arrival date."
elif not departure:
    error = "Please choose your departure date."
```

## What is already correct — do not change

| Guard | Message |
|---|---|
| Missing name or email | Name and email are required. |
| Bad email | Enter a valid email address. |
| Arrival in the past | Choose an arrival date in the future. |
| Party too large | This room sleeps up to N. |
| Terms not ticked | Please confirm you agree to the Terms & Conditions. |
| House over capacity | (computed per-date, correctly) |

All of these are specific and actionable, which is the standard the two
above should meet.
