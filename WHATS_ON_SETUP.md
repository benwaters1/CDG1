# What's On — setup

Three parts: a table, two routes, and an admin page. Everything below is
literal — paste it in.

## 1. Table

```sql
CREATE TABLE IF NOT EXISTS whats_on (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  title         TEXT    NOT NULL,
  description   TEXT,
  location      TEXT,
  distance      TEXT,              -- "3 minutes", "25 minutes"
  -- Recurrence: either a weekday rule OR a fixed date.
  weekday       INTEGER,           -- 0=Mon .. 6=Sun. NULL for one-off.
  event_date    TEXT,              -- 'YYYY-MM-DD'. NULL for recurring.
  start_time    TEXT,              -- '08:00'
  end_time      TEXT,              -- '13:00'
  season_from   TEXT,              -- optional 'MM-DD', e.g. '12-01' for winter only
  season_to     TEXT,              -- optional 'MM-DD', e.g. '03-31'
  is_active     INTEGER NOT NULL DEFAULT 1,
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

## 2. Public route

```python
from datetime import date, timedelta

DAY_NAMES = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']

def _in_season(row, d):
    """Optional season window, e.g. skiing only Dec-Mar. Handles wrap-around."""
    f, t = row['season_from'], row['season_to']
    if not f or not t:
        return True
    md = d.strftime('%m-%d')
    return (f <= md <= t) if f <= t else (md >= f or md <= t)

def _time_label(row):
    if row['start_time'] and row['end_time']:
        return f"{row['start_time']}–{row['end_time']}"
    return row['start_time'] or ''

@app.route("/whats-on")
def whats_on():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM whats_on WHERE is_active = 1 ORDER BY sort_order, id"
    ).fetchall()

    today = date.today()
    this_week, upcoming = [], []

    # Next 7 days, resolving recurring weekday rules against real dates.
    for offset in range(7):
        d = today + timedelta(days=offset)
        for r in rows:
            if r['weekday'] is None or not _in_season(r, d):
                continue
            if r['weekday'] != d.weekday():
                continue
            this_week.append({
                'title': r['title'], 'description': r['description'],
                'location': r['location'], 'distance': r['distance'],
                'day_label': 'Today' if offset == 0 else DAY_NAMES[d.weekday()],
                'time_label': _time_label(r),
                'is_today': offset == 0,
            })

    # One-off dated events: this week vs later.
    for r in rows:
        if not r['event_date']:
            continue
        try:
            ed = date.fromisoformat(r['event_date'])
        except ValueError:
            continue
        if ed < today:
            continue
        item = {
            'title': r['title'], 'description': r['description'],
            'location': r['location'], 'distance': r['distance'],
            'time_label': _time_label(r),
            'date_label': ed.strftime('%-d %B'),
            'day_label': DAY_NAMES[ed.weekday()],
            'is_today': ed == today,
        }
        (this_week if ed <= today + timedelta(days=6) else upcoming).append(item)

    # The standing weekly rhythm — the same recurring rules, but shown as
    # "every Sunday" rather than resolved against actual dates. One entry per
    # rule, so a market that runs twice a week appears twice, in day order.
    standing = []
    for r in rows:
        if r['weekday'] is None or not _in_season(r, today):
            continue
        standing.append({
            'title': r['title'], 'description': r['description'],
            'location': r['location'], 'distance': r['distance'],
            'day_label': DAY_NAMES[r['weekday']],
            'time_label': _time_label(r),
            'weekday': r['weekday'],
        })
    standing.sort(key=lambda e: e['weekday'])

    week_label = f"{today.strftime('%-d %B')} – {(today + timedelta(days=6)).strftime('%-d %B')}"
    return render_template("whats_on.html",
                           this_week=this_week, upcoming=upcoming,
                           standing=standing, week_label=week_label)
```

## 3. Admin routes

```python
@app.route("/admin/whats-on")
@login_required
def admin_whats_on():
    conn = get_db()
    events = conn.execute(
        "SELECT * FROM whats_on ORDER BY sort_order, id"
    ).fetchall()
    return render_template("admin_whats_on.html", events=events)


@app.route("/admin/whats-on/save", methods=["POST"])
@login_required
def admin_whats_on_save():
    f = request.form
    conn = get_db()
    weekday = f.get('weekday')
    weekday = int(weekday) if weekday not in (None, '', 'none') else None
    vals = (
        f.get('title','').strip(), f.get('description','').strip() or None,
        f.get('location','').strip() or None, f.get('distance','').strip() or None,
        weekday, f.get('event_date') or None,
        f.get('start_time') or None, f.get('end_time') or None,
        f.get('season_from') or None, f.get('season_to') or None,
        1 if f.get('is_active') else 0, int(f.get('sort_order') or 0),
    )
    if f.get('id'):
        conn.execute("""UPDATE whats_on SET title=?, description=?, location=?, distance=?,
                        weekday=?, event_date=?, start_time=?, end_time=?,
                        season_from=?, season_to=?, is_active=?, sort_order=?
                        WHERE id=?""", vals + (int(f['id']),))
    else:
        conn.execute("""INSERT INTO whats_on
                        (title, description, location, distance, weekday, event_date,
                         start_time, end_time, season_from, season_to, is_active, sort_order)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
    conn.commit()
    flash("Saved.", "success")
    return redirect(url_for('admin_whats_on'))


@app.route("/admin/whats-on/<int:event_id>/delete", methods=["POST"])
@login_required
def admin_whats_on_delete(event_id):
    conn = get_db()
    conn.execute("DELETE FROM whats_on WHERE id = ?", (event_id,))
    conn.commit()
    flash("Removed.", "success")
    return redirect(url_for('admin_whats_on'))
```

## 4. Seed the standing markets

**Run this.** The standing-week section on the public page is now driven
entirely from this table — with no rows, the section does not render at all.

```sql
INSERT INTO whats_on (title, description, location, distance, weekday, start_time, end_time, sort_order) VALUES
('Les Cabannes Market', 'Fruit, vegetables, dairy, meat and artisanal producers. The closest market to the gates.', 'Place des Platanes', '3 minutes', 6, '08:00', '13:00', 1),
('Tarascon-sur-Ariège Market', 'The larger of the two. Saturday is the producersّ market.', 'Place Jean-Jaurès', '15 minutes', 2, '08:00', '13:00', 2),
('Tarascon-sur-Ariège Producers'' Market', 'Cheese from the mountain farms, charcuterie, and whatever the season has decided.', 'Place du 19 mars', '15 minutes', 5, '08:00', '13:00', 3),
('Ax-les-Thermes Market', 'Mountain produce, and the thermal baths a short walk away afterwards.', 'Place Roussel', '25 minutes', 6, '08:00', '13:00', 4);
```

Note: weekday is 0=Mon..6=Sun, so 2=Wednesday, 5=Saturday, 6=Sunday.

## A caveat worth checking locally

Sources disagree on the Les Cabannes market day — the Ariège tourism board
says Sunday morning on Place des Platanes; one directory says Friday. Worth
confirming in the village before this goes live.
