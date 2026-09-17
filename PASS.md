# The pass — what the backend needs

`pass.html` is finished. Almost all of the data already exists; what is
missing is one query that joins it and three small POST routes.

---

## What you already have

  - **`guests`** — `name`, `dietary_notes`, `preferences`, `vip`, `notes`
  - **`guest_notes`** — the running record on a guest
  - **`bookings`** — arrival, departure, room
  - **`repeat_guests(conn, ...)`** at line 12065 — already written
  - **`stock_items`** and **`stock_movements`** — name, category, unit,
    `reorder_level`, location
  - **`restaurant_bookings`** — who is dining without staying

Nothing new is needed for guests or stock. One screen, one query.

---

## 1. The guest list

Everyone eating tonight, whether they are staying or not:

```sql
-- staying, and dining
SELECT g.id, g.name, g.dietary_notes, g.preferences, g.vip, g.notes,
       b.arrival_date AS arrive, b.departure_date AS depart,
       r.name AS room_name,
       julianday(b.departure_date) - julianday(b.arrival_date) AS nights,
       1 AS staying,
       (SELECT COUNT(*) FROM bookings b2
         WHERE b2.guest_id = g.id AND b2.status = 'confirmed') AS stay_count
  FROM bookings b
  JOIN guests g ON g.id = b.guest_id
  LEFT JOIN rooms r ON r.id = b.room_id
 WHERE ? BETWEEN b.arrival_date AND b.departure_date
   AND b.status = 'confirmed'
```

Union the same shape from `restaurant_bookings` with `staying = 0`, so a
local dining without a room appears in the same list. **The chef does not care
which table someone came from.**

Two derived fields the template uses:

  - `last_night` — `departure_date = tomorrow`. It is on the screen because
    the last dinner is the one worth getting right
  - `history` — what they ate on previous visits, if you record it. Optional;
    the block simply does not render without it

---

## 2. The store

```sql
SELECT si.name, si.category, si.unit, si.reorder_level, si.location,
       COALESCE(SUM(sm.quantity), 0) AS on_hand
  FROM stock_items si
  LEFT JOIN stock_movements sm ON sm.stock_item_id = si.id
 GROUP BY si.id
 ORDER BY si.category, si.name
```

The template flags anything at or below its reorder level. Filtering is in the
browser, so tapping "Running out" is instant and works with no connection.

---

## 3. Three POST routes

```python
@app.post("/pass/note")      # pass_guest_note   → INSERT INTO guest_notes
@app.post("/pass/stock")     # pass_stock_ask    → a task, or an email to you
@app.post("/pass/message")   # pass_message      → see below
```

`pass_guest_note` is the valuable one. **A chef writing "did not touch the
cheese" the moment they notice is worth more than any system**, and it appears
on that guest's profile the next time they book.

---

## 4. Messaging — the only genuinely new thing

There is no `messages` table. The smallest honest version:

```sql
CREATE TABLE IF NOT EXISTS staff_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    to_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- NULL = everyone
    body          TEXT NOT NULL,
    urgent        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    read_at       TEXT
);
```

**Consider not building this.** The kitchen has WhatsApp and it works. The
argument for having it here is that a message about a guest belongs beside
that guest rather than in a thread that scrolls away — and that "urgent"
ought to reach you rather than a phone on silent. If it does not do that
better than WhatsApp, it will not get used, and an unused inbox in a kitchen
is worse than none.

If you do build it: `urgent` should email or text you. A flag nobody sees is
not a flag.

---

## Setting up the iPad

  - **Guided Access** (Settings → Accessibility) locks it to this one page. A
    chef cannot accidentally leave it, and nobody can browse on it
  - **Add to Home Screen** from Safari, so it runs without browser chrome
  - **Auto-Lock: Never**, and leave it on a charger
  - the screen calls `wall_mode(120)`, so it **refreshes every two minutes and
    stamps the time it last succeeded**. If the connection drops the stamp
    turns red and says how old the page is
  - a wipe-clean case. It will be touched with the back of a knuckle

---

## What the screen is built to do

Answer, without anyone typing, from three metres:

  - **who is eating tonight**, staying or not
  - **what they cannot eat** — white on red, the loudest thing on the page,
    and repeated inside the profile so it cannot be missed either way
  - **who has been here before**, how many times, and when last
  - **how long they are staying**, in which room, and whether tonight is
    their last
  - **what is in the store** and what is running out
  - **a way to say something** to you or to the team without leaving the pass

The allergy line is deliberately not a tag among tags. It is a red band across
the guest's row, because it is the one thing on this screen that cannot be
got wrong.
