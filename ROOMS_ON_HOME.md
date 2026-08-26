# The homepage drops your best room — one-line fix

`dashboard()` reads:

```sql
SELECT * FROM rooms WHERE active = 1
ORDER BY sort_order, price_per_night LIMIT 4
```

Two problems. `LIMIT 4` shows four of five, and `price_per_night` ascending
means the one dropped is always the **most expensive** — the Suite with
Mountain View at €450, which has the most impressive bed in the château. The
front page is advertising the cheapest four rooms and hiding the best one.

Change to:

```sql
SELECT * FROM rooms WHERE active = 1
ORDER BY sort_order, price_per_night DESC
```

No limit — there are only five, and five fits the grid as cleanly as four.
`DESC` leads with the Suite. `sort_order` still wins, so you can override the
order by hand in the admin at any time.

If you would rather keep a cap for when more rooms open, use `LIMIT 6` — it
holds two rows of three and will not silently drop a room the moment a sixth
is added.
