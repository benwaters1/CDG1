# Routes still to add

Two one-liners. Both templates are in this zip and will 404 until these exist.

```python
@app.route("/contact")
def contact():
    return render_template("contact.html")
```

`/facilities` and `/whats-on` are already live — confirmed against the running
app. `/contact` is new.

## Also worth running, if not already

- `WHATS_ON_SETUP.md` — the table, routes and the four standing markets.
  The public page's standing-week section renders nothing without the seed.
- `WORKSHOP_FIELDS.md` — three optional columns plus the five ateliers with
  their real 2026–27 dates and prices from the .com.
- `DATE_FILTERS.md` — registers `date_short` and `date_range`. **Without
  these the workshop pages print raw ISO dates** like `2027-07-10`.


---

# The homepage is passed no data

`dashboard()` calls `render_template("home.html")` with nothing. That is why the
front page reads as a menu of links rather than a page: it cannot show a room or
a workshop, because it has never been given one.

Comparable houses show the actual rooms on the homepage — named, priced,
described — instead of a category tile that links away.

```python
@app.route("/")
def dashboard():
    # Two audiences, one address. A visitor gets the château's front page; a
    # signed-in member of staff gets their dashboard.
    if not current_user():
        conn = get_db()
        # The front page shows real rooms and real workshop dates rather than
        # category tiles — a visitor should see what is actually for sale
        # without having to click through first.
        rooms = conn.execute(
            """SELECT * FROM rooms WHERE active = 1
                ORDER BY sort_order, price_per_night LIMIT 4"""
        ).fetchall()

        today = datetime.now(timezone.utc).date().isoformat()
        upcoming = conn.execute(
            """SELECT ws.*, w.title, w.price_per_person, w.nights_label,
                      w.id AS workshop_id
                 FROM workshop_sessions ws
                 JOIN workshops w ON w.id = ws.workshop_id
                WHERE w.active = 1 AND ws.start_date >= ?
                ORDER BY ws.start_date LIMIT 3""",
            (today,),
        ).fetchall()
        conn.close()
        return render_template("home.html", rooms=rooms, upcoming=upcoming)
    return staff_dashboard()
```

**Check the column names against your own `rooms` table** — if it has no
`sort_order` or `active`, drop those from the query. The template treats both
lists as optional and renders nothing when they are empty, so the route change
is safe to apply before the data is perfect.
