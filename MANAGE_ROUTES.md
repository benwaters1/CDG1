# Dinner and event self-service — two branches to add

A guest can currently only **cancel** a dinner or an event enquiry. Changing
party size, dietary notes or a guest count means an email — which is the
commonest reason someone contacts you about a booking they could have fixed
themselves in ten seconds.

Both tables already carry the columns. These branches go inside the existing
routes, immediately after the `cancel` branch and before the `render_template`.

## In `restaurant_manage`

```python
    if request.method == "POST" and request.form.get("action") == "update":
        # Party size and dietary notes are the only two things worth changing
        # on a dinner, and both are things the kitchen needs to know EARLY —
        # which is exactly why they should not require an email.
        if booking["status"] not in ("pending", "confirmed"):
            flash("This reservation can no longer be changed.", "error")
            conn.close()
            return redirect(url_for("restaurant_manage", manage_token=manage_token))

        try:
            new_size = int(request.form.get("party_size", booking["party_size"]))
        except (TypeError, ValueError):
            new_size = booking["party_size"]
        new_size = max(1, min(new_size, 20))
        new_notes = (request.form.get("dietary_notes") or "").strip()[:800]

        size_changed = new_size != booking["party_size"]
        notes_changed = new_notes != (booking["dietary_notes"] or "")

        conn.execute(
            "UPDATE restaurant_bookings SET party_size = ?, dietary_notes = ? WHERE id = ?",
            (new_size, new_notes, inquiry["id"]),
        )
        conn.commit()

        # Only tell the kitchen when something actually changed — a mail for
        # every save trains staff to stop reading them.
        if size_changed or notes_changed:
            owner_to = owner_email(conn)
            if owner_to:
                bits = []
                if size_changed:
                    bits.append(f"party size {booking['party_size']} → {new_size}")
                if notes_changed:
                    bits.append(f"dietary notes now: {new_notes or '(none)'}")
                send_email(
                    owner_to,
                    f"Dinner reservation changed — {inquiry['reference_code']}",
                    f"{booking['guest_name']}, {format_date_human(booking['dinner_date'])}: "
                    + "; ".join(bits),
                )
            flash("Your reservation has been updated.", "success")
        else:
            flash("Nothing to change.", "info")
        conn.close()
        return redirect(url_for("restaurant_manage", manage_token=manage_token))
```

## In `event_manage`

```python
    if request.method == "POST" and request.form.get("action") == "update":
        # NOTE: this route names the row `inquiry`, not `booking`.
        # An enquiry is a conversation, not a booking — the guest count and
        # the message are the two things that genuinely move while it is open.
        if inquiry["status"] not in ("pending", "new", "open"):
            flash("This enquiry can no longer be changed.", "error")
            conn.close()
            return redirect(url_for("event_manage", manage_token=manage_token))

        try:
            new_count = int(request.form.get("guest_count") or 0) or None
        except (TypeError, ValueError):
            new_count = inquiry["guest_count"]
        new_msg = (request.form.get("message") or "").strip()[:2000]

        conn.execute(
            "UPDATE event_inquiries SET guest_count = ?, message = ? WHERE id = ?",
            (new_count, new_msg, inquiry["id"]),
        )
        conn.commit()
        owner_to = owner_email(conn)
        if owner_to:
            send_email(
                owner_to,
                f"Event enquiry updated — {inquiry['reference_code']}",
                f"{inquiry['contact_name']} updated their enquiry. "
                f"Guests: {new_count or 'not stated'}.\n\n{new_msg}",
            )
        flash("Your enquiry has been updated.", "success")
        conn.close()
        return redirect(url_for("event_manage", manage_token=manage_token))
```

**Check the status values before running the event one.** I have used
`('pending', 'new', 'open')` because the enquiry table's status vocabulary is
not constrained in the schema the way `bookings.status` is. If yours uses
something else, that guard silently blocks every edit.

The templates in this drop already render both forms.
