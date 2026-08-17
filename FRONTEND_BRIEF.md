# Guest-facing front end — brief for a redesign

Paste this whole file into a new Claude conversation, and attach the template
files listed at the bottom. It contains everything needed to redesign the
public pages **without breaking the booking system behind them.**

---

## What this is

Château de Gudanes (Ariège, France) — a restored 18th-century château that takes
room bookings, restaurant reservations, workshop registrations and event
enquiries. The public pages are served by a single Python/Flask app; there is no
React, no build step, no bundler. Templates are **Jinja2**, styles are one
hand-written CSS file. Keep it that way unless asked otherwise: the whole thing
has to be maintainable by one person.

The audience is affluent, international, mostly booking on a phone, and often
discovering the château through Instagram. The current pages work correctly but
look plain — the goal is for them to look like the building.

---

## THE CONTRACT — do not change any of this

A redesign may change layout, typography, colour, copy and structure freely.
It must **not** change the following, or bookings stop working silently — the
form will submit, the guest will see a success page, and no booking will exist.

### Every form must keep

1. **The `action` URL and `method`** exactly as listed below.
2. **Every `name=""` attribute** exactly as listed. These are read server-side
   by name. Renaming `guest_email` to `email` loses the booking.
3. **The CSRF token.** Every POST form must contain, inside the `<form>`:
   ```html
   <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
   ```
   Without it the POST is rejected outright.
4. **`enctype="multipart/form-data"`** on any form with a file input.

### The forms

| Page | Method + action | Field names (`name=`) |
|---|---|---|
| Availability / room list | `GET /book` | `arrival`, `departure` (query string) |
| **Book a room** | `POST /book/<room_id>` | `arrival_date`, `departure_date`, `guest_name`, `guest_email`, `guest_phone`, `party_size`, `special_requests`, `extras` (checkbox, repeated), `promo_code`, `agree_terms` (checkbox, required) |
| Join the waitlist | `POST /waitlist/join` | `name`, `email`, `phone`, `desired_arrival`, `desired_departure`, `party_size`, `notes` |
| **Restaurant** | `POST /restaurant/book` | `dinner_date`, `guest_name`, `guest_email`, `guest_phone`, `party_size`, `dietary_notes`, `promo_code` |
| Restaurant waitlist | `POST /restaurant/waitlist/join` | `name`, `email`, `phone`, `desired_date`, `party_size`, `notes` |
| **Workshop** | `POST /workshops/register/<session_id>` | `guest_name`, `guest_email`, `guest_phone`, `party_size`, `occupancy_type`, `other_guest_names`, `requested_roommate`, `dietary_notes`, `medical_notes`, `special_occasion`, `notes`, `promo_code` |
| Workshop waitlist | `POST /workshops/waitlist/join` | `name`, `email`, `phone`, `session_id`, `party_size`, `notes` |
| **Event enquiry** | `POST /events/inquire` | `contact_name`, `contact_email`, `contact_phone`, `event_type`, `preferred_date`, `alternate_date`, `guest_count`, `message` |
| Find my booking | `POST /book/manage` | `reference_code`, `email` |
| Find my table | `POST /restaurant/find` | `reference_code`, `email` |
| Find my workshop | `POST /workshops/find` | `reference_code`, `email` |
| Find my event | `POST /events/find` | `reference_code`, `email` |
| Leave feedback | `POST /feedback/<token>` | `rating`, `comment` |

### Live promo-code check (optional to keep)

`POST /api/validate-promo-code` with `code` plus the same booking fields
(`category`, `room_id` / `session_id`, `arrival_date`, `departure_date`,
`party_size`). Returns JSON. The server re-validates the code again for real at
booking time, so this is only a preview — it can be dropped without breaking
anything.

### Server-side rules the design should respect

These are enforced whatever the form does, so the UI should make them obvious
rather than let a guest hit them:

- Departure must be **after** arrival. Same-day is rejected.
- Party size must be ≥ 1 and **≤ the room's `max_occupancy`** (passed to the
  template as `room.max_occupancy`).
- Some rooms have a **minimum stay** (`room.min_nights`).
- **Terms must be ticked.** The checkbox is genuinely required.
- Dates that overlap an existing booking are rejected — the availability
  calendar on `/book` shows what's free.
- **Prices are always computed server-side.** Never send a price from the
  browser; anything you post as a total is ignored.
- Booking requests are **requests**, not instant confirmations. The château
  reviews them. Copy should not promise instant confirmation.
- If online payment is on, submitting redirects to **Stripe Checkout**. The
  booking is created by Stripe's webhook, not by the form.

---

## Design direction

**The house should be the loudest thing on the page.** It is a genuinely
beautiful, half-ruined, half-restored 18th-century château in the Pyrenees.
The current design is competent but generic; it could be any small hotel.

- Photography-led. Big, uncropped images with room to breathe.
- Restrained typography. It already uses **Playfair Display** (headings) and
  **Inter** (body), with **IBM Plex Mono** for figures and reference codes.
  Keep or replace deliberately, not by accident.
- Calm, warm, slightly austere. Not a luxury-hotel cliché — no gold gradients,
  no stock "LUXURY" language.
- **Mobile first.** Most guests arrive from a phone.

### Existing palette (CSS custom properties, already defined)

```
--chestnut #4A2E1F    --chestnut-deep #2A1A12    --gold #C9A15B
--ivory #F3ECDD       --parchment #FBF8F1        --parchment-line #DED0AE
--steel #3D6C8D       --ink #2A1A12              --ink-soft #6B5B4B
--danger #a33         --success #3d6b3d          --surface #fff
```

Change these if the design calls for it, but change them **in one place** —
they are used across ~1,400 lines of CSS and the staff-facing app shares the
file. Safest is to give the public pages their own scope.

### Constraints that are not negotiable

- **No build step.** Plain CSS and plain JS only. No Tailwind, no npm, no JSX.
- **No external JS libraries** unless there is a strong reason — the site must
  work on a poor mountain connection.
- **Accessible.** Real labels on every field, visible focus states, sensible
  contrast. Guests will include people using screen readers.
- **Inputs must be ≥16px font on mobile** or iOS zooms the page on focus.
- **Tap targets ≥44px.** This was measured and fixed recently; don't undo it.
- Images must be `max-width:100%` and the page must never scroll sideways.

---

## What to attach alongside this file

From `templates/`:

- `public_base.html` — the shared public layout (header, footer, `<head>`)
- `book_rooms.html` — availability calendar + room list
- `book_room.html` — **the booking form** (most important)
- `restaurant_info.html`, `restaurant_book.html`
- `workshops_public.html`, `workshop_detail.html`, `workshop_register.html`
- `events_info.html`
- `booking_confirmation.html` — what a guest sees after booking
- `terms.html`

And `static/style.css` (the whole thing — the public and staff sides share it).

Photographs of the château are worth attaching too; the design should be built
around real images rather than placeholders.

---

---

## The guest's "my account" — read this before designing it

There is deliberately **no guest login**. A guest never creates a password.
Instead every booking carries a long random `manage_token`, and that token IS
the account: it goes in their confirmation email, and the pages below open
straight from it.

That decision is worth keeping. A château takes a few hundred bookings a year;
asking each guest to invent a password to look at one booking is friction that
buys nothing, and a forgotten password becomes a phone call to the house.

### The pages a guest can already reach

| Page | URL | What it's for |
|---|---|---|
| Confirmation | `/book/confirmation/<manage_token>` | Straight after booking |
| **Manage my booking** | `/book/manage/<manage_token>` | The closest thing to "my account" |
| **My bill** | `/booking/<manage_token>/statement` | Itemised, with VAT and taxe de séjour |
| Add to calendar | `/book/<manage_token>/calendar.ics` | |
| Check in | `/checkin/<manage_token>` | |
| Find my booking | `POST /book/manage` | `reference_code` + `email` — the way back in |

Restaurant, workshops and events each have the same shape:
`/restaurant/manage/<token>`, `/workshops/manage/<token>`, `/events/manage/<token>`.

### What the bill contains

Legally and practically this is the most important guest page after booking:

- Nights, rate, and every extra
- **VAT broken out by rate** — accommodation and food are 10% in France,
  alcohol 20%, so a single figure is wrong
- **Taxe de séjour on its own line**, outside VAT, showing the arithmetic:
  adults × nights × rate, with under-18s named as exempt
- What's been paid and what's outstanding

A business guest cannot reclaim VAT without it. Design it to print cleanly —
there's already a `@media print` block that hides the site chrome.

### If you want a fuller "my account"

The sensible version is **one page per booking, reached by token** — not a
logged-in area. If a guest should see *all* their stays at once, that needs a
new "find everything for this email" flow, and the honest trade-off is that
emailing a magic link is then the only safe way to do it. Ask before building
it; it's a real decision, not a styling one.

## A good opening prompt

> These are the public booking pages for Château de Gudanes, a restored
> 18th-century château in the French Pyrenees. They work correctly but look
> generic. Redesign them so they feel like the building — photography-led, calm,
> restrained, mobile-first.
>
> The attached brief lists form actions and field names that must not change:
> the server reads those by name, and renaming one silently breaks bookings.
> Keep every `name=""`, every form `action`, and the `csrf_token` hidden input.
>
> Plain Jinja2 templates and plain CSS — no build step, no frameworks.
> Start with `book_room.html`, the page that actually takes the money.

---

## After the redesign

Bring the files back here and I will verify, against the running app, that:

- every form still posts the fields the server expects,
- a booking created through the new pages actually appears,
- nothing overflows or zooms on a phone,
- and the terms checkbox is still genuinely required.

That check is worth doing before anything goes live — a redesign that looks
perfect and quietly drops `guest_email` is the expensive kind of mistake.
