"""stamp[:10] on a UTC timestamp, and the airport run it hid.

house_date() has said why since it was written: "Slicing an ISO stamp with
[:10] reads UTC, so anything recorded between midnight and 02:00 local carries
yesterday's date all day. Anywhere that date is shown to a person, or bucketed
as 'Today', it has to be converted first rather than truncated."

Fifteen places still truncated. Most showed a date and were a day out for two
hours of every day, which is a small wrong. One was not small.

THE AIRPORT RUN. all_transfers buckets a transfer into today, upcoming or past
by slicing scheduled_at — which is stored in UTC by
local_datetime_input_to_utc_iso — and comparing it against the house's day. A
pickup at half past one in the morning, which is what a delayed flight from
anywhere gives you, is stored under the PREVIOUS day. So it is wrong twice:

  - the day before, it appears on TODAY's list, for a run that is not today;
  - and on the morning it is actually due, it has slipped into PAST.

Either way the run most likely to be missed is exactly the one the arithmetic
hides, on the list somebody checks before leaving. The fixture below is a
pickup at 01:30, not a clock reading, so it fails at any hour rather than only
in the two-hour window — and the section asserts BOTH: that it is counted as
upcoming rather than today, and that it is not under past.

THE SWEEP is the second half, and its list is PROVED rather than asserted:
each column named as holding a UTC timestamp is checked against the database
to still hold one, so the list cannot quietly rot into a list of date columns
that would be harmless to slice.

WHAT A VALUE HOLDS, NOT WHAT IT IS CALLED is the third half. The template
sweep finds a slice by a name ending _at, and two walked straight past it:
the manual printed `last_updated[:10]` and the guest account
`expires[:10]|date_short`, both stored moments, both a day early after
midnight. So _harness now watches every slice a template makes, with the
value in it, and run.py names any that cut a moment to ten characters (DAY
CUTS, beside MOMENTS). The sections below prove the watcher sees what it
should and nothing it should not, and that both pages now print the house's
day.

AND THE BROWSER, which has a clock of its own. Every public date picker took
its "today" from `new Date().toISOString().slice(0, 10)`, UTC's day, and the
site-wide rule that a departure follows its arrival counted the day after in
local time and then read it back in UTC -- which, anywhere east of Greenwich,
is the arrival day itself. So for anybody booking from France the earliest
departure on every form was the day they arrived, all day, every day. The
last section runs the real pages' scripts in headless Chrome at a frozen
00:30 in the Ariege and reads what the pickers allow.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZSLICE"


def _cleanup(conn):
    conn.execute("DELETE FROM vehicle_transfers WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("a UTC stamp read as a day")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    oc, _ec, _owner, _emp = clients()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO vehicles (name, license_plate, created_at)
           VALUES (?, 'ZZ-000-ZZ', ?)""", (TAG + " Land Rover", now))
    vehicle = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # Half past one in the morning, tomorrow, at the house. In UTC that is
    # 23:30 TODAY -- so a slice files it under today and the bucketing calls
    # it "past" on the morning it is due.
    pickup_local = m.datetime.combine(
        today + timedelta(days=1),
        m.datetime.min.time().replace(hour=1, minute=30)
    ).replace(tzinfo=m.LOCAL_TZ)
    pickup_utc = pickup_local.astimezone(m.timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction,
                   scheduled_at, notes, created_at)
           VALUES (?, ?, 'pickup', ?, 'Toulouse, delayed flight', ?)""",
        (vehicle, TAG + " Beauchamp", pickup_utc, now))
    conn.commit()

    s.section("The fixture really is in the window")
    # Without this the section proves nothing at most hours of the day: if
    # UTC and the house agree about which day 01:30 falls on, every check
    # below passes on a slice as readily as on a conversion.
    s.check("UTC and the house disagree about this pickup's day",
            pickup_utc[:10] != m.house_date_iso(pickup_utc),
            detail=f"{pickup_utc[:10]} sliced, "
                   f"{m.house_date_iso(pickup_utc)} at the house")
    s.check("and the house's day is the one the guest means",
            m.house_date_iso(pickup_utc) == (today + timedelta(days=1)).isoformat(),
            detail=m.house_date_iso(pickup_utc))

    s.section("The driver's list files it under the right day")
    body = oc.get("/transfers").get_data(as_text=True)
    s.check("the page opens", TAG + " Beauchamp" in body,
            detail="the transfer is not on the page at all")
    # By POSITION, because the page renders Today, then Upcoming, then Past,
    # and anything before the Past summary is one of the first two.
    #
    # My first version of this looked backwards from the name for the nearest
    # of several heading words, and every row carries the CSS class
    # "status-upcoming" -- so it found "Upcoming" against a past row and
    # passed on the bug it was written for. It is worth saying out loud: a
    # check that reads a class name instead of a heading proves nothing and
    # looks exactly like one that does.
    at_name = body.find(TAG + " Beauchamp")
    at_past = body.find("Past (")
    s.check("it is not filed under a past run",
            at_past < 0 or at_name < at_past,
            detail="a pickup due tomorrow morning, sitting under Past on the "
                   "list somebody checks before leaving. This is the half "
                   "that bites TOMORROW; the tile check below is the half "
                   "that bites today.")

    s.section("And it is counted as still to come, not already gone")
    # The three tiles at the top are built from the same buckets, so the one
    # that says how many are coming has to have moved too.
    def tile(label):
        # The tile's own label, not the first "Today" on the page -- the nav
        # carries one, and looking back from it finds no tile at all.
        i = body.find('stat-tile-label">' + label + "<")
        if i < 0:
            return None
        chunk = body[max(0, i - 220):i]
        j = chunk.rfind("stat-tile-value")
        return chunk[j:].split(">", 1)[1].split("<", 1)[0].strip() if j >= 0 else None

    s.check("the tiles are readable", tile("Today") is not None
            and tile("Upcoming") is not None, detail=str(tile("Today")))
    s.check("a pickup tomorrow is counted as upcoming",
            (tile("Upcoming") or "0").isdigit() and int(tile("Upcoming")) >= 1,
            detail=f"Today {tile('Today')}, Upcoming {tile('Upcoming')} — "
                   "the tiles are built from the same buckets, so a run "
                   "filed under past disappears from both")

    # ------------------------------------------------------------- sweep
    s.section("Nothing slices a UTC timestamp any more")
    # The list below is PROVED, not asserted: every column named as holding a
    # UTC timestamp is checked against the database to still hold one, so it
    # cannot rot into a list of date columns that would be harmless anyway.
    STAMPS = {
        "vehicle_transfers": ["scheduled_at", "created_at"],
        "pos_orders": ["opened_at"],
        "leave_requests": ["requested_at"],
        "guests": ["created_at"],
        "guest_feedback": ["submitted_at"],
        "vehicle_usage": ["checked_out_at"],
    }
    checked = 0
    for table, cols in sorted(STAMPS.items()):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col not in have:
                s.check(f"{table}.{col} still exists to check", False,
                        detail="the sweep's list names a column that is gone")
                continue
            sample = conn.execute(
                f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL "
                f"AND TRIM({col}) != '' LIMIT 1").fetchone()
            if sample is None:
                continue
            checked += 1
            s.check(f"{table}.{col} really holds a timestamp",
                    "T" in str(sample[0]),
                    detail=f"{sample[0]!r} — if this became a date column "
                           "the entry belongs off the list, not left to pass "
                           "for free")
    s.check("at least a few columns had data to check against",
            checked >= 3, detail=f"{checked} columns had a value")

    import io as _io
    import os as _os
    import re as _re
    src = _io.open(_os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "app.py"), encoding="utf-8").read()
    named = sorted({c for cols in STAMPS.values() for c in cols})
    for col in named:
        # Both spellings a slice takes: row["col"][:10] and (row["col"] or "")[:10].
        hits = _re.findall(
            r"""\[["']%s["']\]\s*(?:or\s*["'"']*\s*\)?\s*)?\[:10\]""" % col, src)
        s.check(f"nothing slices {col}", not hits,
                detail=f"{len(hits)} place(s) — house_date_iso() is the "
                       "one answer, and two spellings for one idea is how "
                       "the last one of these survived 125 times")

    s.section("And the templates, which the sweep never used to open")
    # The reason this half exists: app.py was swept and the templates were
    # not, so the same mistake lived on in sixty-eight of them. A moment's
    # column ends _at by this schema's own convention, which is what makes
    # them findable.
    tpl_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "templates")
    found = []
    for root, _dirs, files in _os.walk(tpl_dir):
        for fn in sorted(files):
            if not fn.endswith(".html"):
                continue
            path = _os.path.join(root, fn)
            body = _re.sub(r"\{#.*?#\}", "",
                           _io.open(path, encoding="utf-8").read(), flags=_re.S)
            for i, line in enumerate(body.splitlines(), 1):
                # A slice that follows ")" is of what a function handed back,
                # which is not this check's to judge. It was once said to be
                # safe after local_datetime_str; that returns words, and
                # [:10] of them printed "September ". test_stamp_times has it.
                for mo in _re.finditer(
                        r"([A-Za-z0-9_]*_at)['\"]?\s*\]?\s*\[\s*0?:10\s*\]",
                        line):
                    at = mo.start()
                    before = line[:at + len(mo.group(0))]
                    if _re.search(r"\)\s*\[\s*0?:10\s*\]$", before):
                        continue
                    found.append("%s:%d %s" % (fn, i, mo.group(1)))
    s.check("no template slices a stored moment to get a day",
            not found,
            detail="; ".join(found[:6]) + (" +%d more" % (len(found) - 6)
                                           if len(found) > 6 else ""))
    s.check("there were templates to read at all",
            _os.path.isdir(tpl_dir) and any(
                f.endswith(".html") for _r, _d, fs in _os.walk(tpl_dir)
                for f in fs),
            detail="a sweep that found no files to sweep passes for free")

    s.section("And a template can reach the right answer at all")
    # Most of why the templates went their own way: house_date_iso was not
    # reachable from one, so [:10] was the only thing to hand.
    s.check("house_day is a filter templates can use",
            "house_day" in m.app.jinja_env.filters,
            detail=str(sorted(k for k in m.app.jinja_env.filters
                              if "day" in k or "date" in k)))
    s.check("and it converts rather than truncating",
            m.app.jinja_env.filters["house_day"](pickup_utc) != pickup_utc[:10],
            detail="on this fixture, at least, they differ")

    s.section("And a page really does show the house's day, not UTC's")
    # Source-level checks above prove nothing is slicing. This proves the
    # thing the slicing got wrong: 23:30 UTC is half past one the NEXT
    # morning in the Ariege, and that is the day the house calls it.
    late = "2026-09-03T23:30:00+00:00"
    conn.execute(
        "INSERT INTO announcements (title, body, created_at) VALUES (?, ?, ?)",
        (TAG + " late notice", "posted after midnight", late))
    conn.commit()
    body = oc.get("/announcements").get_data(as_text=True)
    at = body.find(TAG + " late notice")
    near = body[max(0, at - 900):at + 900] if at >= 0 else ""
    s.check("the announcement is on the page", at >= 0)
    s.check("it is dated the day the house was in", "2026-09-04" in near,
            detail=" ".join(near.split())[:130])
    s.check("and not the day UTC was in", "2026-09-03" not in near,
            detail="half past eleven at night in London is half past one the "
                   "next morning here, and the house goes by the house")
    conn.execute("DELETE FROM announcements WHERE title LIKE ?", (TAG + "%",))
    conn.commit()

    s.section("And the helper is what everything uses instead")
    s.check("house_date_iso exists", callable(getattr(m, "house_date_iso", None)))
    s.check("it converts rather than truncates",
            m.house_date_iso(pickup_utc) != pickup_utc[:10],
            detail="on this fixture, at least, they differ")
    s.check("and nothing at all is a safe answer for nothing at all",
            m.house_date_iso("") == "" and m.house_date_iso(None) == "",
            detail="a blank stamp must not become today")
    s.check("nor is rubbish", m.house_date_iso("not a stamp") == "")

    s.section("And a date formatter handed a moment prints the house's day")
    # date_short, date_human and date_range read a DATE and handed anything
    # else back untouched, so a stored moment reached the page as the raw
    # stamp. A full run found three templates doing it -- Started on My Hours,
    # a code's last use on what discounts cost, a supplier's last invoice --
    # and reading found a fourth, the day a room fault was reported. Mended in
    # the formatter, so the next template written this way is right as well.
    f = m.app.jinja_env.filters
    date_range = m.app.jinja_env.globals["date_range"]
    after_midnight = "2026-09-04T22:30:00+00:00"   # 00:30 on the 5th, here
    s.check("date_short gives the house's day, not the stamp",
            f["date_short"](after_midnight) == m.format_date_short("2026-09-05"),
            detail=f"{f['date_short'](after_midnight)!r}")
    s.check("date_human too",
            f["date_human"](after_midnight) == m.format_date_human("2026-09-05"),
            detail=f"{f['date_human'](after_midnight)!r}")
    s.check("and date_range, at both ends",
            date_range(after_midnight, "2026-09-07T22:30:00+00:00")
            == date_range("2026-09-05", "2026-09-08"),
            detail=f"{date_range(after_midnight, '2026-09-07T22:30:00+00:00')!r}")
    s.check("a stamp stored without a zone is read as UTC, as house_date reads it",
            f["date_short"]("2026-09-04 22:30:00") == m.format_date_short("2026-09-05"),
            detail=f"{f['date_short']('2026-09-04 22:30:00')!r} -- SQLite's own "
                   "datetime('now') writes it this way")
    s.check("a date is still that date",
            f["date_short"]("2026-09-04") == "4 September 2026",
            detail=f"{f['date_short']('2026-09-04')!r}")
    s.check("nothing is still nothing",
            f["date_short"]("") == "" and f["date_short"](None) is None)
    s.check("and rubbish is handed back rather than turned into a day",
            f["date_short"]("soon") == "soon", detail=f"{f['date_short']('soon')!r}")

    s.section("And a page that hands one over shows the day")
    # Rooms sold with a fault: "reported" is room_issues.created_at, a moment,
    # and the page printed it raw. A fault logged at half past midnight is the
    # house's today, not UTC's yesterday.
    room = _harness.ensure_room()
    logged = m.datetime.combine(today, m.datetime.min.time().replace(minute=30)
                                ).replace(tzinfo=m.LOCAL_TZ).astimezone(m.timezone.utc)
    conn.execute(
        """INSERT INTO room_issues (room_id, title, description, status, created_at)
           VALUES (?, ?, 'found on the turnaround', 'open', ?)""",
        (room["id"], TAG + " shutter jammed", logged.isoformat()))
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, 'g@example.invalid', ?, ?, 2, 'confirmed', 600, ?)""",
        (room["id"], TAG + "FAULT", TAG + "faulttok", TAG + " Arriving",
         (today + timedelta(days=5)).isoformat(), (today + timedelta(days=7)).isoformat(),
         now))
    conn.commit()
    try:
        page = oc.get("/admin/room-faults")
        text = _harness.visible_text(page.get_data(as_text=True))
        # The row in the table, which is where "reported" is written. The
        # title is named once already, in the warning above it.
        at = text.find(TAG + " shutter jammed found on the turnaround")
        near = text[at:at + 220] if at >= 0 else ""
        s.check("the fault is on the page", at >= 0, page)
        s.check("reported on the house's day, as a day",
                ("reported " + m.format_date_short(today.isoformat())) in near,
                detail=near[:160])
        s.check("not as the stamp it was stored as",
                logged.isoformat()[:10] + "T" not in near and "+00:00" not in near,
                detail=near[:160])
    finally:
        conn.execute("DELETE FROM bookings WHERE reference_code = ?", (TAG + "FAULT",))
        conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
        conn.commit()

    _watcher_section(s)
    _named_otherwise_section(s, conn, oc, _ec)
    _browser_source_section(s)
    _browser_section(s)

    _cleanup(conn)
    conn.close()
    return s


# The moment every section below stands on: half past midnight on the 30th at
# the house, which is still the 29th in UTC.
AFTER_MIDNIGHT = "2026-09-29T22:30:00+00:00"


def _watcher_section(s):
    s.section("A template slicing a moment is seen, whatever the value is called")
    # The harness asserts the watcher at import. This checks it the way run.py
    # relies on it: the record, keyed by template and line, and only for a
    # moment. A watcher that recorded every slice would drown the real ones;
    # one that recorded none would report a clean run over everything.
    env = m.app.jinja_env
    seen = _harness.DAY_CUTS_SEEN
    before = dict(seen)
    tpl = env.from_string("{{ anything[:10] }}\n{{ (other or '')[0:10] }}")

    def recorded(**values):
        seen.clear()
        seen.update(before)
        out = tpl.render(**values)
        new = {k: v for k, v in seen.items() if before.get(k) != v}
        seen.clear()
        seen.update(before)
        return out, sorted(line for (_name, line) in new)

    out, lines = recorded(anything=AFTER_MIDNIGHT, other=None)
    s.check("a moment cut to ten characters is recorded, under any name",
            lines == [1], detail=f"lines {lines}")
    s.check("and the slice still gives exactly what it gave before",
            out.splitlines()[0] == AFTER_MIDNIGHT[:10], detail=repr(out))
    out, lines = recorded(anything="nothing here", other="2026-09-04 22:30:00")
    s.check("a stamp with no zone is a moment too, as house_date reads it",
            lines == [2], detail=f"lines {lines} -- SQLite's datetime('now') "
                                 "writes UTC this way")
    _out, lines = recorded(anything="2026-09-30", other="a sentence of words")
    s.check("a plain date is not a moment, and slicing one is not recorded",
            lines == [], detail=f"lines {lines}")
    _out, lines = recorded(anything="2026-09-30T00:30:00+02:00", other=None)
    s.check("nor is a moment already in the house's own offset",
            lines == [], detail=f"lines {lines} -- its first ten characters "
                                "are the house's day")
    other_cuts = env.from_string("{{ a[:16] }}|{{ a[11:16] }}|{{ l[:10]|length }}")
    seen.clear()
    seen.update(before)
    shown = other_cuts.render(a=AFTER_MIDNIGHT, l=list(range(20)))
    stray = {k: v for k, v in seen.items() if before.get(k) != v}
    seen.clear()
    seen.update(before)
    s.check("only a day cut: a time's slices and a list's are left alone",
            not stray and shown == "2026-09-29T22:30|22:30|10",
            detail=f"{shown!r} {stray} -- test_stamp_times has the times")
    compiled = env.compile("{{ x[:10] }}", raw=True)
    s.check("the app's own environment compiles a slice as the watched call",
            "watched_slice" in compiled,
            detail="Jinja writes a slice as plain Python slicing, past "
                   "environment.getitem, so nothing else can see it")


def _named_otherwise_section(s, conn, oc, ec):
    house_day = m.format_date_short("2026-09-30")
    utc_day = "2026-09-29"
    seen = _harness.DAY_CUTS_SEEN

    s.section("The manual's date is the house's day")
    # last_updated is app_settings 'manual_last_updated', written by
    # touch_manual_updated as a UTC moment. Written the way it writes it.
    held = conn.execute("SELECT value FROM app_settings WHERE key = "
                        "'manual_last_updated'").fetchone()
    conn.execute("INSERT INTO app_settings (key, value) VALUES "
                 "('manual_last_updated', ?) ON CONFLICT(key) DO UPDATE SET "
                 "value = excluded.value", (AFTER_MIDNIGHT,))
    conn.commit()
    try:
        s.check("the fixture is a moment as the app stores one",
                m.manual_last_updated(conn) == AFTER_MIDNIGHT
                and m.house_date_iso(AFTER_MIDNIGHT) == "2026-09-30")
        for who, client in (("the owner", oc), ("a member of staff", ec)):
            page = client.get("/manual")
            text = _harness.visible_text(page.get_data(as_text=True))
            at = text.find("Last changed")
            near = text[at:at + 40] if at >= 0 else ""
            s.check(f"{who} sees it changed on the house's day",
                    ("Last changed " + house_day) in near, page,
                    detail=near or text[:120])
            s.check(f"and not UTC's", utc_day not in near, detail=near)
        s.check("and the watcher saw no day cut on the manual",
                not any(name == "manual.html" for name, _line in seen),
                detail=str(sorted(k for k in seen if k[0] == "manual.html")))
    finally:
        if held is None:
            conn.execute("DELETE FROM app_settings WHERE key = 'manual_last_updated'")
        else:
            conn.execute("UPDATE app_settings SET value = ? WHERE key = "
                         "'manual_last_updated'", (held["value"],))
        conn.commit()

    s.section("A guest's account link expires on the house's day")
    # expires is guest_sessions.expires_at, a UTC moment. The template cut it
    # to UTC's day BEFORE date_short could convert it, so a link running out
    # at half past midnight here was said to run out the day before.
    # 23:30 UTC, which is past midnight here in winter (00:30) and summer
    # (01:30) alike, so the fixture is in the window whatever the date.
    expires = (m.datetime.now(m.timezone.utc) + timedelta(days=400)).replace(
        hour=23, minute=30, second=0, microsecond=0)
    local_day = m.house_date_iso(expires.isoformat())
    token = TAG.lower() + _harness.secrets_token()
    conn.execute(
        """INSERT INTO guest_sessions (email, token, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        ("zzslice@example.invalid", token,
         m.datetime.now(m.timezone.utc).isoformat(), expires.isoformat()))
    conn.commit()
    try:
        s.check("the fixture's two days differ",
                local_day != expires.isoformat()[:10],
                detail=f"{expires.isoformat()} is {local_day} at the house")
        page = m.app.test_client().get(f"/my-account/{token}")
        text = _harness.visible_text(page.get_data(as_text=True))
        at = text.find("expires on")
        near = text[at:at + 40] if at >= 0 else ""
        s.check("the page says the link runs out on the house's day",
                ("expires on " + m.format_date_short(local_day)) in near, page,
                detail=near or "no expiry sentence on the page")
        s.check("and not on UTC's",
                m.format_date_short(expires.isoformat()[:10]) not in near,
                detail=near)
        s.check("and the watcher saw no day cut on it",
                not any(name == "guest_account.html" for name, _line in seen),
                detail=str(sorted(k for k in seen if k[0] == "guest_account.html")))
    finally:
        conn.execute("DELETE FROM guest_sessions WHERE token = ?", (token,))
        conn.commit()


# A day cut out of the browser's clock. toISOString() is UTC, so its first
# ten characters are UTC's day -- the same fault as stamp[:10], in JavaScript.
_JS_UTC_DAY = r"""toISOString\(\)\s*\.\s*(?:slice|substring|substr)\(\s*0\s*,\s*10\s*\)|toISOString\(\)\s*\.\s*split\(\s*['"]T['"]\s*\)\s*\[\s*0\s*\]"""
# And "today" from the browser's own clock, which is the guest's zone.
_JS_LOCAL_TODAY = r"""new\s+Date\(\s*\)\s*;\s*\w+\s*\.\s*setHours\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)"""


def _browser_source_section(s):
    import io
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    s.section("No page's script takes a day from the browser's clock")
    utc_cuts, local_todays, uses = [], [], []
    read = 0
    for folder, suffix in (("templates", ".html"), ("static", ".js")):
        for base, _dirs, files in os.walk(os.path.join(root, folder)):
            for fn in sorted(files):
                if not fn.endswith(suffix):
                    continue
                path = os.path.join(base, fn)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                text = io.open(path, encoding="utf-8").read()
                read += 1
                for rx, into in ((_JS_UTC_DAY, utc_cuts),
                                 (_JS_LOCAL_TODAY, local_todays)):
                    for mo in re.finditer(rx, text):
                        into.append("%s:%d" % (rel, text.count("\n", 0, mo.start()) + 1))
                if "houseDay." in text and fn != "public_base.html":
                    uses.append((rel, text))
    s.check("there were scripts to read", read > 50, detail=f"{read} files")
    s.check("nothing cuts UTC's day out of toISOString()", not utc_cuts,
            detail="; ".join(utc_cuts) + " -- houseDay.today() is the house's "
                   "day, houseDay.after(iso) the day after one")
    s.check("nor takes today from the browser's own clock", not local_todays,
            detail="; ".join(local_todays) + " -- houseDay.date() is the "
                   "house's today as a local Date, for the calendars")

    base = io.open(os.path.join(root, "templates", "public_base.html"),
                   encoding="utf-8").read()
    head = base.split("</head>", 1)[0]
    s.check("public_base defines houseDay in its head, before any page's script",
            "window.houseDay" in head,
            detail="every public picker calls it; without it they throw and "
                   "stop doing anything at all")
    s.check("and asks for the house's zone by name, not the browser's",
            "timeZone: zone" in head and "house_tz" in head)
    strays = []
    for rel, text in uses:
        ext = re.search(r"""\{%\s*extends\s+["']([^"']+)""", text)
        included = rel.split("/")[-1].startswith("_")
        if not included and not (ext and ext.group(1) == "public_base.html"):
            strays.append(rel)
    s.check("every page that calls houseDay is drawn on public_base",
            not strays, detail="; ".join(strays))


# Put at the top of <head>: every Date the page makes with no argument is
# 22:30 UTC on 29 September, which is half past midnight on the 30th here.
_FROZEN_CLOCK = """<script>(function () {
  var fixed = Date.UTC(2026, 8, 29, 22, 30), Real = Date;
  function Frozen() {
    if (!(this instanceof Frozen)) return new Real(fixed).toString();
    var args = arguments.length ? Array.prototype.slice.call(arguments) : [fixed];
    return new (Function.prototype.bind.apply(Real, [null].concat(args)))();
  }
  Frozen.prototype = Real.prototype;
  Frozen.now = function () { return fixed; };
  Frozen.UTC = Real.UTC; Frozen.parse = Real.parse;
  window.Date = Frozen;
})();</script>"""

# Put at the end of <body>: pick arrivals the way a guest does, and write down
# what each departure is then allowed to be.
_PROBE = """<script>(function () {
  var out = {};
  function field(a, b) { return document.getElementById(a) || document.querySelector('[name="' + a + '"]'); }
  try {
    out.zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    out.house_today = window.houseDay ? houseDay.today() : null;
    out.house_date = window.houseDay ? houseDay.date().getDate() : null;
    var a = field(%(a)s), d = field(%(d)s);
    out.arrival_min = a ? a.min : null;
    out.departures = {};
    %(arrivals)s.forEach(function (day) {
      a.value = day;
      a.dispatchEvent(new Event('change', {bubbles: true}));
      out.departures[day] = d.min;
    });
  } catch (e) { out.error = String(e); }
  var pre = document.createElement('pre');
  pre.id = 'gudanes-probe';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();</script>"""

# The ordinary day after, the end of a month, and the Sunday the clocks go
# forward -- the room page's own sync counted that one on the local clock and
# gave the arrival day back.
_ARRIVALS = {"2026-10-05": "2026-10-06", "2026-10-31": "2026-11-01",
             "2027-03-28": "2027-03-29", "2026-12-31": "2027-01-01"}


def _probe_page(chrome, html, a, d, workdir, label):
    import html as htmllib
    import json
    import os
    import re
    import subprocess
    import sys
    page = html.replace("<head>", "<head>" + _FROZEN_CLOCK, 1)
    page = page.replace("</body>", _PROBE % {
        "a": json.dumps(a), "d": json.dumps(d),
        "arrivals": json.dumps(sorted(_ARRIVALS))} + "</body>", 1)
    path = os.path.join(workdir, label + ".html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    # The flags test_staff_header_on_a_phone launches with, and for the same
    # reason: the pages carry web fonts and photographs from other hosts, and
    # a test run never touches the network, so every hostname goes nowhere.
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--user-data-dir=" + os.path.join(workdir, "profile-" + label),
           "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=5000", "--dump-dom",
           "file:///" + path.replace(os.sep, "/").lstrip("/")]
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=90)
    mo = re.search(r'<pre id="gudanes-probe">(.*?)</pre>', proc.stdout, re.S)
    return json.loads(htmllib.unescape(mo.group(1))) if mo else None


def _browser_section(s):
    import shutil
    import tempfile
    s.section("And in a browser, at half past midnight here, the pickers "
              "offer the house's day")
    # One way of finding a browser, the phone suite's, rather than two.
    from test_staff_header_on_a_phone import find_browser
    chrome = find_browser()
    s.check("Chrome is on this machine to run the pages' own scripts",
            chrome is not None,
            detail="install Chrome or Chromium, or set GUDANES_CHROME to one "
                   "-- a check that could not run is not a pass")
    if not chrome:
        return
    room = _harness.ensure_room()
    anon = m.app.test_client()
    pages = (("the room page", f"/book/{room['id']}", "arrival_date", "departure_date"),
             ("the event enquiry", "/events", "preferred_date",
              "alternate_date"))
    workdir = tempfile.mkdtemp(prefix="gudanes-picker-")
    try:
        for label, url, a, d in pages:
            r = anon.get(url)
            html = r.get_data(as_text=True)
            s.check(f"{label} opens", r.status_code == 200 and f'"{a}"' in html,
                    r)
            out = _probe_page(chrome, html, a, d, workdir, label.split()[-1])
            s.check(f"{label}: the browser ran its scripts", bool(out)
                    and not out.get("error"), detail=str(out))
            if not out or out.get("error"):
                continue
            s.check(f"{label}: houseDay says the house's day, not UTC's",
                    out.get("house_today") == "2026-09-30"
                    and out.get("house_date") == 30,
                    detail=f"{out.get('house_today')} in a browser on "
                           f"{out.get('zone')}")
            s.check(f"{label}: the earliest arrival is the house's today",
                    out.get("arrival_min") == "2026-09-30",
                    detail=f"{out.get('arrival_min')!r} -- UTC's day at "
                           "00:30 here is the 29th, and the form refuses it")
            wrong = {day: got for day, got in (out.get("departures") or {}).items()
                     if got != _ARRIVALS.get(day)}
            s.check(f"{label}: the earliest departure is the day after arrival",
                    not wrong and len(out.get("departures") or {}) == len(_ARRIVALS),
                    detail=f"{wrong} in {out.get('zone')} -- the arrival day "
                           "itself is a stay of no nights, which the form refuses")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    print(run().report())
