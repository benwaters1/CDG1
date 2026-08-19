# Workshops: drop the occupancy question, cap on people not rooms

Two small edits. **Neither touches nightly bookings**, which are room-specific
and priced per room and must keep working exactly as they do.

## Why

Workshops take over the whole château — `is_range_available()` already blocks
every room for a session's dates, so nothing else is sold alongside one. The
real constraint on a workshop is therefore the **legal maximum of 15 people**,
not how many nightly rooms exist.

But `session_room_error()` counts against `bookable_room_count()` — the five
or six rooms listed for nightly stays — when the house has more bedrooms than
that. A party can be refused for want of rooms that exist.

And `occupancy_type` asks the guest to choose between "double" and "triple",
both included in the price, neither changing what they get. The château
allocates rooms on the day regardless. It is a decision with no consequence in
the middle of a €3,800 booking.

## Edit 1 — remove the selector from the public form

In `templates/workshop_register.html`, delete the whole `.g-field` block
containing `<select name="occupancy_type">`, and replace it with the honest
sentence:

```html
<p class="g-hint">
  Rooms are arranged for two, with a third bed available on request — tell us
  below and we will arrange it. Solo travellers are paired thoughtfully with a
  like-minded guest, or given a room to themselves where one is free.
</p>
```

**Safe to remove.** Line 20667 reads
`request.form.get("occupancy_type", "double")`, so an absent field defaults to
`"double"`, and the column is `NOT NULL DEFAULT 'double'`. Staff can still set
it per booking in the admin, which is where "three on request" belongs.

## Edit 2 — cap on people, not rooms

Replace the body of `session_room_error()` so it enforces the legal headcount
instead of the nightly room count:

```python
MAX_CHATEAU_OCCUPANCY = 15   # legal maximum for the whole house

def session_room_error(conn, session_id, occupancy_type, party_size, exclude_id=None):
    """None if this party still fits, else why not.

    A workshop takes over the whole château — is_range_available() blocks every
    room for its dates — so the binding limit is the house's legal occupancy of
    15 people, not the handful of rooms listed for nightly stays. Counting
    against bookable_room_count() refused parties for want of rooms that exist.

    The session's own capacity still applies and is checked separately; this is
    the ceiling above it.
    """
    party = max(int(party_size or 0), 0)
    if not party:
        return None

    booked = conn.execute(
        """SELECT COALESCE(SUM(party_size), 0) AS n FROM workshop_bookings
            WHERE session_id = ? AND status IN ('pending', 'confirmed')"""
        + (" AND id != ?" if exclude_id else ""),
        (session_id, exclude_id) if exclude_id else (session_id,),
    ).fetchone()["n"]

    if booked + party <= MAX_CHATEAU_OCCUPANCY:
        return None
    left = max(MAX_CHATEAU_OCCUPANCY - booked, 0)
    if left == 0:
        return "These dates are full. Join the waitlist and we will write if a place opens."
    return (f"Only {left} place{'' if left == 1 else 's'} left on these dates, "
            f"and you are booking for {party}. Contact the château and we will see "
            f"what we can do.")
```

`rooms_needed()`, `session_rooms_used()` and `bookable_room_count()` stay as
they are — **`bookable_room_count()` is used by the nightly side too**, so do
not delete it.

## Do not change

- `is_range_available()` — already blocks nightly rooms for workshop dates
  correctly, including confirmed events. Leave it alone.
- Anything on the nightly path. Rooms there are genuinely room-specific, with
  their own prices and per-room availability.
- `occupancy_type` as a column — staff still set it, and it drives the
  admin-side room planning.

## Worth confirming

Is 15 the cap for the **whole house at once**? If so it is right here, since
nothing else is sold during a workshop. If some workshops should take fewer —
an artisan class with one tutor probably should — set the lower number on the
session's own `capacity`, which is checked independently of this.
