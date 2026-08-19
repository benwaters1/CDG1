# Dates on guest-facing pages

Workshop pages currently show raw ISO dates — `2027-07-10 → 2027-07-17` — on a
€4,800 product. `format_date_human()` exists at line 3923 but is never
registered as a Jinja filter, so templates cannot reach it.

## 1. Register two filters

Put this next to `format_date_human`:

```python
def format_date_short(iso_str):
    """'2027-07-10' -> '10 July 2027'. Day-first, which is how a French
    château's guests read a date. Avoids the %-d strftime flag, which is
    Linux-only and crashes on Windows."""
    d = parse_date(iso_str)
    if not d:
        return iso_str
    return f"{d.day} {d.strftime('%B')} {d.year}"


def format_date_range(start_iso, end_iso):
    """'10 – 17 July 2027' when the month and year match, otherwise both in
    full. A range written twice over is harder to read than one written once."""
    a, b = parse_date(start_iso), parse_date(end_iso)
    if not a or not b:
        return start_iso or ""
    if a == b:
        return format_date_short(start_iso)
    if a.year == b.year and a.month == b.month:
        return f"{a.day} – {b.day} {a.strftime('%B')} {a.year}"
    if a.year == b.year:
        return f"{a.day} {a.strftime('%B')} – {b.day} {b.strftime('%B')} {a.year}"
    return f"{format_date_short(start_iso)} – {format_date_short(end_iso)}"


app.jinja_env.filters["date_short"] = format_date_short
app.jinja_env.filters["date_human"] = format_date_human
app.jinja_env.globals["date_range"] = format_date_range
```

## 2. Use them

Anywhere a template prints `session['start_date']` or `booking['arrival_date']`
raw. In the workshop templates specifically:

```jinja
{{ date_range(s.session['start_date'], s.session['end_date']) }}
{{ session['start_date']|date_short }}
```

`workshops_public.html`, `workshop_detail.html` and `workshop_register.html` are
already written to call these — they will render ISO strings until the filters
are registered, then correct themselves with no template change.

## Why day-first

`format_date_human` gives "July 10, 2027" — American order. The château is in
France and most guests are European or Australian, so `date_short` is day-first.
`date_human` is left registered unchanged in case anything relies on it.
