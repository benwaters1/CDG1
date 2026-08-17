# Front-end guide — Château de Gudanes staff app

Everything you need to restyle or rebuild the UI. Read this before touching
`static/style.css` or anything in `templates/`.

---

## 1. What this app is (and isn't)

- **Server-rendered Flask + Jinja2.** 119 endpoints render HTML; ~19 return
  JSON, and those are small AJAX helpers (toggle a task, poll a badge count),
  **not** a data API.
- **No build step. No JS framework.** No `package.json`, no bundler, no React.
  Plain CSS in one file, plain `<script>` blocks in templates.
- **123 templates, 241 CSS classes, 36 design tokens per shell.**

**So:** do not introduce React/Vue/Tailwind/a bundler. There is no API to hang
an SPA off, and adding one means re-implementing session auth, CSRF and
per-role permissions across ~119 endpoints. Restyle in place instead — edit
`static/style.css` and the Jinja templates.

---

## 2. The two shells

Two independent palettes, same variable names. **Every component below
inherits whichever shell it renders inside — never hardcode a colour.**

| Shell | Body class | Base template | Who sees it |
|---|---|---|---|
| Staff/owner portal | `body.staff-shell` | `templates/base.html` | logged-in staff |
| Public booking site | *(none — `:root`)* | `templates/public_base.html` | guests |

The public site keeps the château palette (dark chestnut + gold) and matches
real chateaugudanes.com branding — **be conservative there.** The staff shell
was deliberately re-themed to a light, airy dashboard look; that's where
design work is wanted.

---

## 3. Design tokens

Defined twice: `:root` (public) and `body.staff-shell` (staff). Staff values:

```
--surface        #FFFFFF    card / panel background
--paper          #F7F3EA    subtle inset background
--parchment-line #E7E0D0    all hairlines and borders
--ink            #2B2620    body text
--ink-soft       #716858    secondary text  (4.6:1 min — see §7)
--gold           #B8853E    primary accent: fills, borders, active states
--gold-soft      #85602D    gold used as TEXT on light backgrounds
--gold-on-warn   #85602D    gold used as text/icon on --status-warn-bg
--steel          #2E7BA6    links
--danger         #B3271E    destructive
--success        #2C7742    positive
--shadow         0 2px 14px rgba(43,38,32,0.07)
--focus-ring     rgba(184,133,62,0.30)

--status-success-bg  #E1EFE5     --status-danger-bg   #F5E2DF
--status-info-bg     #DFEAF2     --status-warn-bg     #F3E8CB
--status-neutral-bg  #F0EBDF
```

Three golds exist on purpose: `--gold` is tuned for fills and borders and only
reaches ~3.2:1 as text on white. Use `--gold-soft` for gold text on light, and
`--gold-on-warn` for gold on the warn background. Getting this wrong is how the
"pending" badge ended up at 2.66:1.

---

## 4. Components (ranked by how much of the app they carry)

Restyling the top six changes almost every page.

| Class | In N templates | What it is |
|---|---|---|
| `.upload-hint` | 83 | small muted secondary text |
| `.page-head` | 78 | page title row + right-aligned actions |
| `.btn-mini` | 75 | standard button |
| `.field-label` | 70 | uppercase form label |
| `.empty-state` | 65 | "nothing here yet" panel |
| `.mini-status` | 64 | status pill (see variants below) |
| `.manual-body` | 54 | body copy / description text |
| `.btn-primary` | 52 | the one main action on a page |
| `.detail-card` | 50 | titled panel |
| `.expense-card` | 32 | **the main list-item card** |
| `.search-bar` | 31 | filter/toolbar row (also used for inline forms) |
| `.mini-row` | 31 | compact list row |
| `.narrow-card` | 23 | centred form card (login, change password) |
| `.section-heading` | 21 | `<h2>` section divider |
| `.stat-tile` | 20 | KPI tile — `.stat-tiles` grid wraps them |
| `.btn-mini-danger` | 15 | destructive button |
| `.btn-ghost` | 7 | utility/tertiary action |

**Button hierarchy** (please preserve): `.btn-primary` = the one main action ·
`.btn-mini` = normal · `.btn-ghost` = utility (exports, cross-links) ·
`.btn-mini-danger` = destructive.

**Status pills** — `.mini-status` plus one of:
`status-active status-approved status-available status-cancelled
status-contacted status-current status-inactive status-new status-onshift
status-paid status-past status-pending status-refunded status-rejected
status-unavailable status-unpaid status-upcoming`

**Calendar** has its own system: `.cal-*` (room timeline, `admin_calendar.html`)
and `.cal-ev`/`.cal-day`/`.cal-month` (ops calendar, `ops_calendar.html`).
Stays/events are floating rounded objects on a track — **not** filled table
cells. Don't reintroduce a bordered grid; that was deliberately removed.

---

## 5. Jinja patterns you must keep

```jinja
{% extends "base.html" %}              {# or public_base.html #}
{% from "_icons.html" import icon %}
{% block title %}Page name{% endblock %}
{% block content %} ... {% endblock %}

{{ icon('calendar', 19) }}             {# inline SVG, currentColor #}
{{ url_for('endpoint_name', arg=x) }}  {# never hardcode a URL #}
```

**Every POST form needs a CSRF token** or it 302s back:

```jinja
<form method="post" action="{{ url_for('...') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
```

`fetch()` calls get the header automatically (wrapper in `base.html`).

Available icons: `home guests employee financial bookings rooms wrench tasks
shifts calendar leave manual contacts shopping approvals receipt management
search repeat building shield truck folder lock car coffee bell eye eye-off
restaurant workshop megaphone star`

Role-gate with `{% if user['role'] == 'owner' %}`. The employee side is
deliberately simple — "what do I do today, who's here, what's coming up" —
don't push admin complexity into it.

---

## 6. Layout

`base.html` = topbar + left sidebar (`.sidebar`, 220px) + `<main class="page">`.
Below **860px** the sidebar becomes an off-canvas drawer (`.sidebar.open`,
hamburger `.sidebar-toggle`, `.sidebar-backdrop`). Keep that working.

Wide content (tables, calendars) must scroll inside its own
`overflow-x:auto` container — the page body must never scroll sideways.

---

## 7. Non-negotiables

These are all real bugs that were found and fixed here. Please don't undo them.

1. **Contrast ≥ 4.5:1 for text.** The light re-theme silently broke five pairs;
   worst was the "pending" badge at 2.66:1. If you change a colour, check it.
2. **Destructive ≠ normal.** `.btn-mini-danger` must out-specify
   `body.staff-shell .btn-mini` (0,2,1) or every Delete/Decline repaints gold
   and looks identical to Confirm. Use `body.staff-shell .btn-mini.btn-mini-danger`.
3. **Never signal state by colour alone** — the filter chips also fade and
   strike through when off.
4. **Don't ellipsis away the meaning.** Labels that clip to "CONFIRMED ROOM…"
   or "(Demo…" are worse than wrapping to two lines.
5. **Only real links get hover-lift.** A static row that lifts implies a click
   target that isn't there.
6. **Empty ≠ error.** Empty states use a quiet filled panel, not a dashed
   outline.

---

## 8. Running it

```bash
python app.py     # http://localhost:5000
```

Templates hot-reload; Python edits auto-restart. Sessions survive restarts
(`.dev_secret_key`). After changing CSS, hard-refresh — the browser caches it
for the page's lifetime.

**Sanity check before handing work back:** log in as owner *and* as an
employee, and confirm both sidebars still work and nothing 403s or 500s.
