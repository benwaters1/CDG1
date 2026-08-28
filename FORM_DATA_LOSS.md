# A failed booking wipes almost everything the guest typed

`book_room()` validates thoroughly. But when validation fails it re-renders
the form with only the dates:

```python
return render_template("book_room.html", room=room,
    arrival=arrival_raw, departure=departure_raw,
    extras=extras, stripe_enabled=stripe_enabled(),
    gallery_photos=gallery_photos)
```

Everything else the guest typed is gone. They tick the wrong box or mistype an
email and have to re-enter:

| Field | Returned? |
|---|---|
| arrival_date | yes |
| departure_date | yes |
| guest_name | **no** |
| guest_email | **no** |
| guest_phone | **no** |
| party_size | **no** |
| special_requests | **no** — the longest field on the form |
| extras (checkboxes) | **no** |
| promo_code | **no** |
| agree_terms | **no** |

This is the single largest abandonment risk in the flow. A guest who has
already chosen a room, entered a party size and written a paragraph of
requests is being asked to do it all again because of one typo.

## The fix

Both lines are already in scope in that function — the variables exist. Pass
them back:

```python
return render_template("book_room.html", room=room,
    arrival=arrival_raw, departure=departure_raw,
    extras=extras, stripe_enabled=stripe_enabled(),
    gallery_photos=gallery_photos,
    # everything the guest already typed
    prefill_name=guest_name, prefill_email=guest_email,
    prefill_phone=guest_phone, prefill_party=party_size_raw,
    prefill_requests=special_requests, prefill_promo=promo_code,
    prefill_extras=selected_extra_ids, prefill_terms=agreed_to_terms)
```

The template side is already updated in this package — the fields read those
values and fall back to empty, so this change is safe to make before or after
deploying the templates.

## The same fault elsewhere

Check the equivalent `render_template` on error in:

- `restaurant_book()` — 8 fields
- `workshop_register()` — 12 fields, the longest form on the site
- `events_info()` — 9 fields

`book_rooms()` already does this correctly (`prefill_name`, `prefill_email`,
`prefill_phone` are passed), so the pattern to copy is in the same file.
