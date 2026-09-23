"""Reading `settings` in a template, whatever `settings` happens to be.

`settings` is not one kind of thing. On most public pages it is the allowlisted
site object; where a route passes its own it is a sqlite3.Row of
restaurant_settings or a workshop; in the mail code it is a plain dict; and
sometimes there is none. `settings.get(...)` works on two of those and RAISES
on a Row, because a Row has no .get.

That took the public dining page down twice, in two consecutive handovers,
each time from a line in the base template that worked on every page but the
ones whose routes pass their own settings. Four partials still did it and
worked only because every one of their callers happened to hand them a dict.

And looking at them found a second fault that had been live the whole time.
Four manage pages end their cancel section with "Write to us first", pointing
at mailto:{{ settings.get('email','') }}. Nothing anywhere holds a setting
called `email`. The one sentence a guest reads before cancelling opened a
blank message to nobody.

So: `setting(settings, key, default)` reads any of the four kinds, no template
calls .get on settings, and each manage page names the inbox for its kind of
booking.
"""
import glob
import io
import os
import re
import sqlite3

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZSET"


def _row(**cols):
    """A real sqlite3.Row, the kind of object that broke the dining page."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    names = ", ".join("? AS %s" % k for k in cols) or "1 AS nothing"
    return conn.execute("SELECT " + names, list(cols.values())).fetchone()


def run():
    s = Suite("Reading settings whatever settings is")

    s.section("One accessor for every kind of settings")
    row = _row(cancel_free_days=None, lat="42.8")
    lazy = m.LazyPublicSettings()
    cases = [
        ("a dict", {"lat": "42.8"}, "lat", "42.8"),
        ("a dict missing the key", {}, "lat", None),
        ("a sqlite3.Row", row, "lat", "42.8"),
        ("a Row missing the column", row, "phone", None),
        ("a Row with the column NULL", row, "cancel_free_days", None),
        ("the site object, a key it does not allow", lazy, "email", None),
        ("nothing at all", None, "lat", None),
    ]
    for label, obj, key, want in cases:
        try:
            got = m.read_setting(obj, key)
            ok = got == want
        except Exception as e:
            got, ok = "RAISED %s" % type(e).__name__, False
        s.check(f"{label} gives {want!r}", ok, detail=f"got {got!r}")
    s.check("and a default is given back when there is nothing",
            m.read_setting(row, "phone", "x") == "x"
            and m.read_setting(None, "phone", "x") == "x")

    s.section("The partials render when handed a Row")
    # The shape that failed, done on purpose: each macro called with a
    # sqlite3.Row as its settings, which is what a route passing its own
    # settings would give it.
    env = m.app.jinja_env
    renders = [
        ("the approach diagram",
         "{% from '_approach.html' import approach_map %}{{ approach_map(s) }}"),
        ("the directions in an email",
         "{% from '_email.html' import email_directions %}{{ email_directions(s) }}"),
    ]
    for label, src in renders:
        try:
            with m.app.test_request_context("/"):
                out = env.from_string(src).render(s=_row(lat=None, lng=None))
            ok, detail = bool(out.strip()), "%d characters" % len(out)
        except Exception as e:
            ok, detail = False, "%s: %s" % (type(e).__name__, str(e)[:90])
        s.check(f"{label} draws with a Row", ok, detail=detail)

    s.section("No template calls .get on settings")
    root = os.path.join(_harness.ROOT, "templates")
    offenders = []
    for path in sorted(glob.glob(os.path.join(root, "*.html"))):
        src = io.open(path, encoding="utf-8", errors="replace").read()
        # Blanked rather than removed, keeping the newlines, so the line
        # reported is the line in the file.
        src = re.sub(r"\{#.*?#\}", lambda c: "\n" * c.group(0).count("\n"),
                     src, flags=re.S)
        for mo in re.finditer(r"\bsettings\.get\(", src):
            offenders.append("%s:%d" % (os.path.basename(path),
                                        src[:mo.start()].count("\n") + 1))
    s.check("not one", not offenders,
            detail=f"{offenders} — use setting(settings, 'key', default); .get "
                   "raises on a sqlite3.Row, which is what the dining page is "
                   "handed")

    s.section("\"Write to us first\" writes to somebody")
    for area in ("rooms", "restaurant", "workshops", "events"):
        addr = m.contact_address(area)
        s.check(f"the {area} pages have an address to give",
                bool(addr) and "@" in addr, detail=repr(addr))
    s.check("ateliers and events go to the inbox that answers them",
            m.contact_address("workshops").startswith("experience@")
            and m.contact_address("events").startswith("experience@"),
            detail=f"{m.contact_address('workshops')}, {m.contact_address('events')}")

    # And rendered, on a real page, because the address has to reach the link.
    conn = db()
    token = "zzset" + m.secrets.token_hex(6)
    conn.execute(
        """INSERT INTO event_inquiries (event_type, contact_name, contact_email,
             preferred_date, status, reference_code, manage_token, created_at)
           VALUES ('Wedding', ?, 'zzset@example.invalid', ?, 'new', ?, ?, ?)""",
        (TAG + " couple", (m.house_today() + m.timedelta(days=300)).isoformat(),
         m.make_event_reference_code(), token,
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    page = m.app.test_client().get("/events/manage/%s" % token)
    links = re.findall(r'href="mailto:([^"?]*)', page.get_data(as_text=True))
    s.check("the event page opens", page.status_code == 200,
            detail=f"HTTP {page.status_code}")
    s.check("and its mailto links all have an address in them",
            links and all("@" in l for l in links),
            detail=f"{links} — mailto: with nothing after it opens a blank "
                   "message to nobody, on the page somebody reads before "
                   "cancelling")
    conn = db()
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s
