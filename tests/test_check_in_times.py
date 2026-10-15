"""When a guest may arrive and must leave: one value, and the terms held to it.

The booking pages, the printed stay and the guest timeline said check-in from
2pm and check-out by 11am, and so does Booking.com. The booking terms -- the
thing a guest actually agrees to -- said "Arrival is from 4pm". 2pm is right.
Each page had "2pm" typed into it, seven times over, so nothing could notice
one of them disagreeing.

What this holds:
  - the times are ONE value in code, and the pages read it: change it and the
    room page changes, which a typed "2pm" cannot do;
  - no template types a check-in or check-out time, so the next handover that
    puts "2pm" back is named here, line by line;
  - the stored terms are READ against that value, never rewritten (they are
    the owner's words and live config), and the owner home says when they
    disagree -- and stops saying it once they agree;
  - the shipped terms agree with the value.
"""
import glob
import os
import re

from _harness import Suite, clients, db, keep_rows
import _harness

m = _harness.m
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A time as a page would type it: 2pm, 11 am, 2.30pm, 14:00.
TYPED_TIME = re.compile(r"\b\d{1,2}(?:[:.]\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b", re.I)
# What makes it a check-in or check-out time: said shortly before it.
ABOUT_STAYING = re.compile(r"check-?\s?in|check-?\s?out|checkout|arriv|depart", re.I)


def typed_stay_times(text):
    """Every time a template types within a sentence about arriving or leaving.

    Taken out first, line breaks kept so the line numbers stay true: a time
    inside {# #} is a note about the page, not a claim it makes; a script's
    times are a date sum ("T12:00:00"); and a placeholder or aria-label is an
    example of what a guest might type ("e.g. around 3pm"), not a time the
    house sets. Then any time with check-in, check-out, arrival or departure
    in the sixty characters before it.
    """
    def blank(x):
        return "\n" * x.group(0).count("\n")
    text = re.sub(r"\{#.*?#\}", blank, text, flags=re.S)
    text = re.sub(r"<script\b.*?</script>", blank, text, flags=re.S | re.I)
    text = re.sub(r'\b(?:placeholder|aria-label)="[^"]*"', blank, text)
    out = []
    for found in TYPED_TIME.finditer(text):
        before = text[max(0, found.start() - 60):found.start()]
        if ABOUT_STAYING.search(before):
            line = text.count("\n", 0, found.start()) + 1
            out.append((line, found.group(0)))
    return out


def _home(conn):
    with m.app.test_request_context("/"):
        return [w for w in m.owner_home_warnings(conn, m.house_today())
                if "booking terms" in w["title"]]


def _tasks(conn):
    with m.app.test_request_context("/"):
        m.generate_watch_tasks(conn)
    return [r["title"] for r in conn.execute(
        "SELECT title FROM tasks WHERE origin = ? AND status != 'done' AND title LIKE ?",
        (m.WATCH_TASK_ORIGIN, "The booking terms give%"))]


def _set_terms(conn, text):
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('terms_and_conditions', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (text,))
    conn.commit()


def run():
    s = Suite("check_in_times")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = keep_rows(conn, "app_settings", "key = 'terms_and_conditions'")
    try:
        _checks(s, oc, conn)
    finally:
        put_back()
        # The watch list as the real terms leave it, not as this suite did.
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        conn.close()
    return s


def _checks(s, oc, conn):
    s.section("One value, in code")
    s.check("check-in is 2pm", m.CHECK_IN_TIME == "14:00" and m.CHECK_IN_WORDS == "2pm",
            detail=f"{m.CHECK_IN_TIME} / {m.CHECK_IN_WORDS}")
    s.check("check-out is 11am", m.CHECK_OUT_TIME == "11:00" and m.CHECK_OUT_WORDS == "11am",
            detail=f"{m.CHECK_OUT_TIME} / {m.CHECK_OUT_WORDS}")
    s.check("written the way the house writes a time",
            [m.clock_words(x) for x in ("14:00", "11:00", "16:30", "12:00", "00:00", 21)]
            == ["2pm", "11am", "4.30pm", "12pm", "12am", "9pm"])
    s.check("and the shipped terms agree with it",
            m.terms_time_mismatches(m.DEFAULT_TERMS) == [],
            detail=str(m.terms_time_mismatches(m.DEFAULT_TERMS)))

    s.section("No template types the time")
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "templates", "**", "*.html"), recursive=True)):
        text = open(path, encoding="utf-8").read()
        for line, said in typed_stay_times(text):
            offenders.append(f"{os.path.relpath(path, ROOT)}:{line} {said!r}")
    s.check("every check-in and check-out time on a page is read, not typed",
            offenders == [], detail="; ".join(offenders[:12])
            + " -- use {{ check_in_at }} / {{ check_out_at }}")
    # The scanner has to be able to fail, or the check above proves nothing.
    s.check("the scan finds a typed time when there is one",
            typed_stay_times("<dd>Check-in from 2pm &middot; by 11am</dd>") == [(1, "2pm"), (1, "11am")]
            and typed_stay_times("{# check-in from 2pm #}<p>Dinner at 8pm</p>") == []
            and typed_stay_times('<input name="arrival_time" placeholder="e.g. 3pm">') == [])

    s.section("The pages read it")
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    s.check("there is a room page to read", room is not None)
    if room is not None:
        url = f"/book/{room['id']}"
        page = oc.get(url).get_data(as_text=True)
        s.check("the room page says from 2pm, by 11am",
                "From 2pm &middot; by 11am" in page or "From 2pm · by 11am" in page,
                detail=url)
        s.check("and offers early check-in before 2pm",
                "Before 2pm (early check-in requested)" in page and 'value="2pm-3pm"' in page)
        was = (m.app.jinja_env.globals["check_in_at"], m.app.jinja_env.globals["check_in_hour"])
        m.app.jinja_env.globals["check_in_at"], m.app.jinja_env.globals["check_in_hour"] = "3pm", 15
        try:
            moved = oc.get(url).get_data(as_text=True)
        finally:
            m.app.jinja_env.globals["check_in_at"], m.app.jinja_env.globals["check_in_hour"] = was
        s.check("change the value and the page follows",
                ("From 3pm" in moved) and 'value="3pm-4pm"' in moved
                and 'value="2pm-3pm"' not in moved and "Check-in from 3pm" in moved,
                detail="a page that typed 2pm would still say 2pm")

    s.section("Terms that say 4pm are named on the owner home")
    four = m.DEFAULT_TERMS.replace("Arrival is from 2pm", "Arrival is from 4pm")
    s.check("the test text really says 4pm", four != m.DEFAULT_TERMS)
    _set_terms(conn, four)
    home = _home(conn)
    s.check("one line", len(home) == 1, detail=str(home))
    s.check("naming both times",
            home and "arrival from 4pm" in home[0]["detail"]
            and "arrival from 2pm" in home[0]["detail"], detail=str(home))
    s.check("and not the departure, which agrees",
            home and "departure" not in home[0]["title"], detail=str(home))
    s.check("linking to the Terms page", home and home[0]["href"] == "/admin/terms",
            detail=str(home and home[0]["href"]))
    s.check("raised as a task too, so it reaches somebody",
            _tasks(conn) == ["The booking terms give a different arrival time"],
            detail=str(_tasks(conn)))
    stored = conn.execute("SELECT value FROM app_settings WHERE key = 'terms_and_conditions'"
                          ).fetchone()["value"]
    s.check("and nothing rewrote the terms", stored == four)

    s.section("Both wrong, both named")
    _set_terms(conn, four.replace("departure by 11am", "departure by 10am"))
    home = _home(conn)
    s.check("arrival and departure", home and home[0]["title"]
            == "The booking terms give a different arrival and departure times",
            detail=str(home))

    s.section("Terms that say 2pm: the line goes, and so does the task")
    _set_terms(conn, m.DEFAULT_TERMS)
    s.check("no line on the owner home", _home(conn) == [], detail=str(_home(conn)))
    s.check("and the task has closed itself", _tasks(conn) == [], detail=str(_tasks(conn)))
    _set_terms(conn, "Rooms are let by the night. Payment is taken at booking.")
    s.check("terms that name no time say nothing", _home(conn) == [])

    s.section("Reading the terms: what counts as a time")
    said = m.terms_time_mismatches
    s.check("4 p.m., across a line break", said("Arrival is\nfrom 4 p.m.") == [("arrival", "4pm", "2pm")])
    s.check("16:00 and 16h", said("Check-in from 16:00") == [("arrival", "4pm", "2pm")]
            and said("Check-in from 16h") == [("arrival", "4pm", "2pm")])
    s.check("a departure written 10.30am", said("departure by 10.30am")
            == [("departure", "10.30am", "11am")])
    s.check("the right times in other spellings are not a clash",
            said("Check-in from 14h00, check-out until 11:00") == [])
    s.check("a bare number is not a time",
            said("If you leave before 3 nights are up, we charge for them.") == [])
