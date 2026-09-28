# For whoever works on the till, stock and supplier invoices: nine more, found by measuring

This follows `note-to-frontend-10.md`. It is the same mistake in a quieter
spelling, and nothing reading the code can see it:

```sql
WHERE created_at >= ?        -- with '2026-09-01' bound to the ?
```

A stored `*_at` value is an instant in UTC. A bare date compared with it
means **midnight UTC**, which is 01:00 or 02:00 at the house. Whatever
happened in that first hour or two lands on the wrong side of the line.

It can't be found by reading, because the date is nearly always in a
variable. So it is now **measured on every full test run**. `tests/_harness.py`
watches each statement the app runs, with its values filled in, and records
any that compare a `*_at` column with a bare date under the function that
asked. `tests/run.py` names them at the end as **MOMENTS** against
`BARE_DATE_KNOWN`. A new one fails the run, and so does one that is mended
but left on the list.

The first measured run found 49. Forty were the house's and are fixed. These
nine are yours:

| Function | Column | |
|---|---|---|
| `delivery_shortfalls` | `stock_movements.created_at` | stock |
| `night_cost` | `stock_movements.created_at` | stock |
| `price_changes` | `stock_movements.created_at` | stock |
| `waste_log` | `stock_movements.created_at` | stock |
| `fridge_log` | `fridge_readings.read_at` | kitchen |
| `what_sells` | `pos_order_lines.created_at` | till |
| `service_times` | `sent_at` | till (also in note 10, for `DATE(sent_at)`) |
| `spend_by_vendor` | `submitted_at` | supplier invoices |
| `supplier_statement` | `submitted_at` | supplier invoices |

## The fix is one call

```python
house_moment(day)            # the instant the house's day begins, as a stored string
```

`created_at >= house_moment(d)` means "on or after d, here", and
`< house_moment(d + timedelta(days=1))` means "before the day after". It
accepts a date or an ISO date string. Anything already a moment is handed
back unchanged, so wrapping a value that was right does no harm. For the
till's day, which runs to 05:00, use `service_day_window(day)` as before.

Report windows from `resolve_period()` now carry `start_at`, `end_at`,
`prev_start_at` and `prev_end_at` beside the `_iso` dates, for exactly this.

Take each one off `BARE_DATE_KNOWN` in `tests/run.py` in the same commit that
mends it.
