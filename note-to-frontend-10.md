# For whoever works on the till, stock and supplier invoices: twelve queries that read the day in UTC

**The short version:** twelve queries in your areas ask SQLite what day a
stored moment falls on. SQLite answers in UTC, because it has no idea where
the house is. Between midnight and 02:00 in the Ariège (01:00 in winter) that
answer is **yesterday**. So anything recorded in that hour or two is filed
under the day before, and on the till, whose day runs to 05:00, the gap is
wider.

I have not changed any of them. They are yours, and a fix made underneath
somebody else's work in progress is a merge nobody asked for. They are listed
in `UTC_DAY_SQL_KNOWN` in `tests/test_house_day.py`, which now fails the run
if a new one appears and if one of these is mended and left on the list. When
you fix one, take it off the list in the same commit.

## The mistake, in its three spellings

```sql
DATE(created_at)                  -- the UTC day
strftime('%Y', occurred_at)       -- the UTC year, or month
SUBSTR(submitted_at, 1, 10)       -- the UTC day again, as a string slice
```

## The right answer, which the till already uses in places

Compare the stored string against the instants a day begins and ends:

- **`service_day_window(day)`**: the till's day, which turns over at 05:00.
  `pos_day` already uses this for its event list; see the comment above
  `win_from, win_to = service_day_window(on)`.
- **`house_day_window(first, last=None)`**: new. The house's calendar days,
  midnight to midnight, as a half-open pair of UTC instants. It is 23 or 25
  hours on the nights the clocks change.

```python
start, end = service_day_window(day)
conn.execute("... WHERE occurred_at >= ? AND occurred_at < ?", (start, end))
```

A stored moment compared with those strings is exact. SQLite never has to
know the timezone.

## The twelve

| Function | Spelling | What it gets wrong |
|---|---|---|
| `pos_close_period` (×2) | `date(occurred_at)` | the closure's first and last journal sequence: events 02:00–05:00 fall outside the night they belong to |
| `pos_close_day` | `date(opened_at) <= ?` | a tab opened at 03:00 is not counted as open when that night is closed |
| `pos_archive_bundle` | `date(occurred_at)` | the yearly archive: the first hour of 1 January goes into the previous year |
| `pos_archive` | `strftime('%Y', occurred_at)` | the list of archive years, same boundary |
| `menu_engineering` | `DATE(pos_order_lines.created_at)` | lines sold after midnight count against the previous night's window edge |
| `service_times` (×2) | `DATE(sent_at)` | a night's service times, split at UTC midnight instead of the till's 05:00 |
| `committed_stock` | `date(booking_extras.created_at)` | an unscheduled extra sold after midnight misses today's commitment |
| `supplier_price_changes` | `DATE(stock_movements.created_at)` | movements just after midnight are yesterday's |
| `wastage_rate` | `DATE(stock_movements.created_at)` | the same |
| `supplier_scorecard` (×2) | `DATE(submitted_at)` | the fallback when an invoice has no date of its own |
| `find_duplicate_invoice` | `SUBSTR(submitted_at, 1, 10)` | the same fallback; harmless inside a window of days, but the same spelling |

The sites counted twice appear twice in the same function, and the test
counts them that way.

One more in `pos_close_period`, `date(created_at) BETWEEN date(?, '-1 day')
AND date(?, '+1 day')`, is **right**: it widens the range by a day each way
and then files each payment by `service_day_iso` in Python. It is on the list
so the test knows it is deliberate.

## What I fixed on the house side, for reference

`leave_impact`, `booking_pace`, `occupancy_pace`, `vat_working`,
`financial_trend` and `financial_month_summary` (which must agree with each
other, and now do in the house's months), `cost_per_occupied_night`,
`review_reply_times`, `turned_away`, `owner_queue_totals`, `staff_tenure`,
and the privacy purge in `purge_dead_enquiries`.
