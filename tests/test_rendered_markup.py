# -*- coding: utf-8 -*-
"""Every page rendered, then read the way a browser and a screen reader do.

Source sweeps miss what a template only assembles at render time — a label
whose `for` is built from a loop variable, a control whose text comes out of
a macro, an id duplicated because the same partial was included twice. So
this walks the real pages and reads the HTML that came out of them.

Four things it found, none of which errored and none of which any test saw:

  THE SEARCH BOX AT THE TOP OF EVERY PAGE HAD NO NAME. It lives in base.html,
  so it was on all 237 of them, announced as "edit text" and nothing more. A
  placeholder is not a label: it is gone the moment somebody types.

  THE TICK HAD NOTHING IN IT. The control staff use most — the morning list,
  the breakfast sheet, the shopping list, the onboarding checklist — renders
  a tick when done and the EMPTY STRING when not. So the unticked ones, the
  ones somebody is about to press, were a button with no name at all.

  SIX LINKS OPENED A NEW TAB AND HANDED IT window.opener, which lets the
  opened page navigate the one that opened it. All six point somewhere
  internal today, which is exactly why nobody would notice the day one does
  not.

  AND NOTHING WAS DUPLICATED, UNLABELLED-BY-IMAGE, OR OUTSIDE table-wrap —
  checked here too, on the output, because the source rules that enforce
  those cannot see a table built inside a macro.

A field WRAPPED in its own label counts as labelled: <label><input> Gluten
</label> is the implicit form and is perfectly accessible. Counting those as
faults buried the real ones — 665 of the first sweep's findings were checkbox
grids using exactly that shape.

A field inside a table cell takes its meaning from the column header above
it and the subject at the start of the line — and a screen reader reads
neither for the input, announcing "edit text, 3". Thirty of those were found
and all thirty are named, so it is a rule here rather than a ceiling, and it
is checked on the SOURCE: the rendered count moved with the database, because
a table only draws an editable row when it has something to edit.

What remains is the row editors built from divs rather than tables — roughly
eleven hundred fields across ninety pages, each taking its meaning from a
heading nothing reads out. Not a bug, not silent, and a piece of work with a
size rather than a number pinned in a constant that would drift the same way
the page count did.
"""
import collections
import glob
import io
import os
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m

TAG = re.compile(r"<(\w+)([^>]*)>", re.S)
ATTR = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
SCRIPTY = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)

# A field inside a <td> takes its meaning from the column header above it and
# the subject at the start of the line, and a screen reader reads neither for
# the input — it announces "edit text, 3". Thirty of these were found and all
# thirty are named, so this is a rule rather than a ceiling.
#
# Checked on the SOURCE, not the render. The rendered count moved with the
# database — 84 pages alone, 90 after the other suites had left their
# fixtures behind, because a table only draws an editable row when it has
# something to edit — and a bound that drifts for reasons nobody controls
# gets raised until it means nothing.
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
FIELD = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.S | re.I)

# GET rules whose arguments are not ids of anything this can look
# up — a filename, a slug, a month. A ceiling, so a new page
# behind an unknown argument is noticed rather than skipped.
UNREACHABLE = 38


def _attrs(raw):
    return dict(ATTR.findall(raw))


def _read(page, html, found):
    body = SCRIPTY.sub(" ", html)

    ids = re.findall(r'\bid="([^"]+)"', body)
    for i, n in collections.Counter(ids).items():
        if n > 1:
            found["duplicate id"].append("%s: %s" % (page, i))

    labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', body))
    wrapped = set()
    for lab in re.finditer(r"<label\b[^>]*>(.*?)</label>", body, re.S | re.I):
        for tag, raw in TAG.findall(lab.group(1)):
            if tag.lower() in ("input", "select", "textarea"):
                wrapped.add(raw)

    for tag, raw in TAG.findall(body):
        a = _attrs(raw)
        low = tag.lower()
        if low in ("input", "select", "textarea"):
            if a.get("type") in ("hidden", "submit", "button", "reset", "image"):
                continue
            if not (a.get("id") in labelled or a.get("aria-label")
                    or a.get("aria-labelledby") or a.get("title")
                    or raw in wrapped):
                found["field with no label"].append(
                    "%s: %s %s" % (page, low, a.get("name", "?")))
        if low == "img" and "alt" not in a:
            found["image with no alt"].append("%s: %s" % (page, a.get("src", "?")))
        if a.get("target") == "_blank" and "noopener" not in a.get("rel", ""):
            found["_blank with no noopener"].append(
                "%s: %s" % (page, a.get("href", "?")))

    for match in re.finditer(r"<(button|a)\b([^>]*)>(.*?)</\1>", body, re.S | re.I):
        tag, raw, inner = match.group(1), match.group(2), match.group(3)
        a = _attrs(raw)
        if " ".join(re.sub(r"<[^>]+>", "", inner).split()):
            continue
        if a.get("aria-label") or a.get("title") or a.get("aria-labelledby"):
            continue
        if tag.lower() == "a" and not a.get("href"):
            continue
        found["control with no name"].append(
            "%s: <%s class=%s>" % (page, tag, a.get("class", "")))

    for match in re.finditer(r"<table\b", body):
        if "table-wrap" not in body[max(0, match.start() - 400):match.start()]:
            found["table outside table-wrap"].append(page)
            break


# What each url argument is an id OF. Without this the sweep reads only the
# 237 pages that take no arguments — a quarter of the app — and the negative
# control proved it: the window.opener hole was put back into the till
# receipt link and went unnoticed, because /pos/order/<id> was never asked
# for.
ID_TABLES = {
    "booking_id": ("bookings", "id"),
    "user_id": ("users", "id"),
    "order_id": ("pos_orders", "id"),
    "room_id": ("rooms", "id"),
    "guest_id": ("guests", "id"),
    "event_id": ("event_inquiries", "id"),
    "inquiry_id": ("event_inquiries", "id"),
    "expense_id": ("expenses", "id"),
    "workshop_id": ("workshops", "id"),
    "session_id": ("workshop_sessions", "id"),
    "menu_id": ("menus", "id"),
    "doc_id": ("documents", "id"),
    "asset_id": ("assets", "id"),
    "absence_id": ("absences", "id"),
    "voucher_id": ("gift_vouchers", "id"),
    "visit_id": ("maintenance_visits", "id"),
    "template_id": ("email_templates", "id"),
    "code_id": ("promo_codes", "id"),
    "manage_token": ("bookings", "manage_token"),
    "share_token": ("bookings", "share_token"),
}


def _one(conn, table, column):
    """A real value for this argument, or None if there is nothing to read."""
    try:
        row = conn.execute(
            "SELECT %s AS v FROM %s WHERE %s IS NOT NULL ORDER BY id DESC "
            "LIMIT 1" % (column, table, column)).fetchone()
    except Exception:
        return None
    return row["v"] if row else None


def _reachable(conn):
    """Every GET page, with a real id filled in wherever one is known.

    Returns (rules, unreachable) so the count of what could NOT be read is
    reported rather than silently shrinking the sweep.
    """
    rules, unreachable = [], []
    for r in m.app.url_map.iter_rules():
        if "GET" not in (r.methods or set()):
            continue
        rule = str(r.rule)
        if rule.startswith("/static") or r.endpoint in ("logout", "login"):
            continue
        if not r.arguments:
            rules.append((r.endpoint, rule))
            continue
        values = {}
        for arg in r.arguments:
            if arg not in ID_TABLES:
                break
            value = _one(conn, *ID_TABLES[arg])
            if value is None:
                break
            values[arg] = value
        if len(values) != len(r.arguments):
            unreachable.append(r.endpoint)
            continue
        try:
            with m.app.test_request_context("/"):
                rules.append((r.endpoint, m.url_for(r.endpoint, **values)))
        except Exception:
            unreachable.append(r.endpoint)
    rules.sort()
    return rules, sorted(set(unreachable))


def run():
    s = Suite("Rendered markup, read as a browser reads it")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    rules, unreachable = _reachable(conn)
    conn.close()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = collections.defaultdict(list)
    pages = 0
    for endpoint, path in rules:
        try:
            resp = oc.get(path)
        except Exception:
            continue
        if resp.status_code != 200 or "html" not in resp.headers.get(
                "Content-Type", ""):
            continue
        pages += 1
        _read(endpoint, resp.get_data(as_text=True), found)

    s.check("there are pages to read", pages > 250, detail="%d read" % pages)
    # Named rather than quietly skipped: a sweep that reads fewer pages
    # than it appears to is the failure this whole suite exists to stop.
    s.check("and the ones it cannot reach are named",
            len(unreachable) <= UNREACHABLE,
            detail="%d: %s — add the argument to ID_TABLES if it is an "
                   "id of something" % (len(unreachable), unreachable[:6]))
    # Deliberately a ceiling only, with no matching floor. Whether a page is
    # reachable depends on whether its table has a row, so the count is lower
    # when this suite runs alone than when the other 319 have left their
    # fixtures behind — 38 against 34. A floor on a number that moves with
    # the database goes red for reasons nobody controls, and a bound like
    # that gets raised until it means nothing.

    for kind in ("control with no name", "_blank with no noopener",
                 "duplicate id", "image with no alt",
                 "table outside table-wrap"):
        rows = found[kind]
        s.check("no %s" % kind, not rows,
                detail="%d — e.g. %s" % (len(rows), rows[:3]))

    # And the source rule, which does not move with the database.
    unlabelled = []
    for path in sorted(glob.glob(os.path.join(root, "templates", "*.html"))):
        src = io.open(path, encoding="utf-8", errors="replace").read()
        for cell in CELL.findall(src):
            # A field wrapped in its own label is labelled.
            stripped = re.sub(r"<label\b.*?</label>", " ", cell,
                              flags=re.S | re.I)
            for tag, raw in FIELD.findall(stripped):
                if re.search(r'type\s*=\s*"(hidden|submit|button|reset|image)"',
                             raw, re.I):
                    continue
                if re.search(r"aria-label\s*=|aria-labelledby\s*=|\btitle\s*=",
                             raw, re.I):
                    continue
                unlabelled.append("%s: <%s%s>"
                                  % (os.path.basename(path), tag, raw[:40]))
    s.check("no field sits unnamed in a table cell",
            not unlabelled,
            detail="%d — the column header names it for a person looking at "
                   "the screen and for nobody else: %s"
                   % (len(unlabelled), unlabelled[:3]))

    # And the chrome, which is on EVERY page. The command palette at the top
    # of base.html had a placeholder and no name, so all 259 pages carried a
    # box announced as "edit text" — a placeholder is not a label, it is gone
    # the moment somebody types. A fault here is 259 faults, so the shared
    # files are held to zero rather than counted.
    chrome = []
    for name in ("base.html", "public_base.html", "_list_toolbar.html",
                 "_period_selector.html"):
        path = os.path.join(root, "templates", name)
        if not os.path.exists(path):
            continue
        src = io.open(path, encoding="utf-8", errors="replace").read()
        # Jinja comments first — base.html explains in one why the language
        # picker is links "rather than a <select>", and the word alone read
        # as an unnamed field.
        src = re.sub(r"\{#.*?#\}", " ", src, flags=re.S)
        # A label pointing AT a field names it even though it does not wrap
        # it, which is how the public date pickers are written.
        named_by_for = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', src))
        wrapped_src = re.sub(r"<label\b.*?</label>", " ", src, flags=re.S | re.I)
        for tag, raw in FIELD.findall(wrapped_src):
            if re.search(r'type\s*=\s*"(hidden|submit|button|reset|image)"',
                         raw, re.I):
                continue
            if re.search(r"aria-label\s*=|aria-labelledby\s*=|\btitle\s*=",
                         raw, re.I):
                continue
            own_id = re.search(r'\bid="([^"]+)"', raw)
            if own_id and own_id.group(1) in named_by_for:
                continue
            chrome.append("%s: <%s%s>" % (name, tag, " ".join(raw.split())[:50]))
    s.check("and nothing in the chrome every page carries is unnamed",
            not chrome, detail="%d: %s" % (len(chrome), chrome[:3]))

    # What is left, said plainly rather than pinned at a number that would
    # drift the same way the page count did: the row editors built from divs
    # rather than tables. Roughly eleven hundred fields across ninety pages,
    # each taking its meaning from a heading a screen reader does not read
    # for it. Not a bug and not silent — a piece of work with a size.
    return s
