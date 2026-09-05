# -*- coding: utf-8 -*-
"""Sentences that state a number, and whether they read it or typed it.

A sweep of every staff-facing template found twenty-two hard-typed numbers
with units -- "last 30 days", "within 60 days", "It works for 48 hours".
Every one of them agreed with its query at the time, which is the best case
and still only luck: nothing anywhere would have said so if a constant had
moved and the sentence had not.

That is not hypothetical here. It is how the workshop deposit came to state
a percentage the code no longer charged, and how "from EUR220" came to be
typed onto ten public pages after the cheapest room went up.

One of the twenty-two was already wrong when it was found. The employee
dashboard promised "Nothing scheduled in the next 7 days" over a query with
no date ceiling at all -- LIMIT 7 was the next seven SHIFTS, so a single
shift a fortnight out appeared under a heading promising a week, and the
eighth shift of a busy week was silently dropped. Both halves are checked
below.
"""
import glob
import html
import io
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m


PUBLIC = {
    "home", "book_rooms", "book_room", "workshops_public", "restoration",
    "facilities", "events_weddings", "events_private", "events_photoshoots",
    "restaurant_info", "events_info", "contact", "press", "story", "social",
    "public_base", "privacy", "terms", "gallery", "find_booking", "error",
    "booking_confirmation", "whats_on", "workshop_detail",
}
TAG = re.compile(r"<[^>]+>")
JINJA = re.compile(r"\{[%{#].*?[%}#]\}", re.S)
SCRIPTY = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
# A number with a unit attached is a claim about behaviour. A bare number is
# a count, a price or a column width, and none of those drift with a window.
NUM = re.compile(
    r"(?<![\w.])(\d{1,4})\s*(%|per cent|days?|hours?|minutes?|months?|weeks?"
    r"|years?|nights?|covers?|people|guests?|times?)\b", re.I)


def _visible(path):
    """The prose a person actually reads, with markup and Jinja removed.

    Jinja goes first and deliberately: a number that came out of
    {{ windows.cert_warning_days }} is exactly what this suite wants to stop
    seeing, so stripping the expression leaves nothing to match, while
    stripping tags first would leave the braces behind as text.
    """
    raw = io.open(path, encoding="utf-8", errors="replace").read()
    raw = SCRIPTY.sub(" ", raw)
    raw = JINJA.sub(" ", raw)
    raw = TAG.sub("\n", raw)
    for line in html.unescape(raw).splitlines():
        line = " ".join(line.split())
        if len(line) >= 20:
            yield line


def run():
    s = Suite("Windows a page states, and whether it read them")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ---- 1. no staff-facing page types a window in by hand ----------------
    typed = []
    for path in sorted(glob.glob(os.path.join(root, "templates", "*.html"))):
        name = os.path.basename(path)[:-5]
        if name in PUBLIC or name.startswith("_"):
            continue
        for line in _visible(path):
            for number, unit in NUM.findall(line):
                typed.append("%s: '%s %s' in %r" % (name, number, unit,
                                                    line[:60]))
    s.check("no staff page states a window it typed rather than read",
            not typed,
            detail="%s — a number the code no longer uses reads as fact; "
                   "print windows.<name> so the sentence and the query "
                   "cannot disagree" % typed[:4])

    # ---- 2. the windows are actually reachable from a template ------------
    windows = m.house_windows()
    s.check("every window has a name a page can print",
            windows and all(isinstance(v, int) and v > 0
                            for v in windows.values()),
            detail=str(windows))

    # Each one is the value its own constant holds — a dict that quietly
    # drifted from the constants would satisfy the sweep above and still put
    # the wrong number on the page.
    s.check("and each is the constant itself, not a copy of it",
            windows["cert_warning_days"] == m.CERT_EXPIRY_WARNING_DAYS
            and windows["guest_session_hours"] == m.GUEST_SESSION_HOURS
            and windows["workshop_balance_days"] == m.WORKSHOP_BALANCE_DAYS
            and windows["my_shifts_days"] == m.MY_SHIFTS_AHEAD_DAYS)

    # ---- 3. the shifts list covers the window it claims -------------------
    #
    # Asked of the PAGE, not of a SELECT written here. A check that issues its
    # own query with the ceiling already in it proves the test's SQL and
    # nothing about the view — put LIMIT 7 back and it would stay green.
    _oc, ec, _owner, emp = clients()
    conn = db()
    today = m.house_today()
    tag = "windows-suite"
    if not emp:
        s.check("a member of staff exists to schedule", False)
        conn.close()
        return s
    uid = emp["id"]
    window = m.MY_SHIFTS_AHEAD_DAYS

    def wipe():
        conn.execute("DELETE FROM shifts WHERE role_note = ?", (tag,))
        conn.commit()

    def shift(day):
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time,
                                   end_time, role_note, created_at)
               VALUES (?, ?, '09:00', '17:00', ?, ?)""",
            (uid, day.isoformat(), tag,
             m.datetime.now(m.timezone.utc).isoformat()))
        conn.commit()

    def drawn():
        """How many of our shifts the dashboard actually rendered.

        Counted by the marker written into role_note, which the page prints
        beside each shift — anything the view dropped simply is not there.
        """
        page = ec.get("/").get_data(as_text=True)
        # Only the section under the heading that states the window. The
        # restaurant list above it prints role_note too, off a different
        # table — counting the whole page would measure the wrong list.
        head = "Your Upcoming Shifts"
        section = page[page.index(head):] if head in page else ""
        return section.count(tag), section

    try:
        wipe()
        # Eight shifts inside the stated week: one a day, and a double on the
        # first. The old LIMIT 7 returned seven rows and stopped, so the
        # person working the eighth was never shown it.
        for i in range(window):
            shift(today + timedelta(days=i))
        shift(today)
        count, _page = drawn()
        s.check("a busy week shows every shift in it, not the first seven",
                count == window + 1,
                detail="the page drew %d of %d — LIMIT 7 dropped the rest "
                       "and said nothing" % (count, window + 1))

        # The other half of the same bug: a shift beyond the window must not
        # appear under a heading that states the window.
        wipe()
        far = today + timedelta(days=window + 7)
        shift(far)
        count, page = drawn()
        s.check("and a shift beyond it is not listed as though inside",
                count == 0,
                detail="the page drew %d — the heading states %d days; a "
                       "shift %d days out is not in it"
                       % (count, window, window + 7))

        # An empty week is the normal case for casual staff, and "nothing
        # scheduled" is not the answer they came for. It says when the next
        # one is — read off the same page, in the words a person would see.
        s.check("an empty week still says when the next shift is",
                "Your next shift is" in page
                and m.format_date_human(far.isoformat()) in page,
                detail="expected %r on the page"
                       % m.format_date_human(far.isoformat()))
    finally:
        wipe()
        conn.close()
    return s
