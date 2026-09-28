"""The atelier, in the guest's own calendar, ending on the day it ends.

Rooms have offered this since they were built. Ateliers could not, which the
design side flagged: the confirmation had nowhere to point an "Add to
calendar" button. So there is a route now — and one thing in it is worth the
whole file.

DTEND IS EXCLUSIVE AND end_date IS NOT. RFC 5545 says an all-day event ends
the MORNING of its DTEND, while a session's end_date is a day the atelier
runs: every query in the app matches a session to a day with `end_date >=
day`, and the guest is shown "10 – 17 July" with the 17th at the workbench.
Copy the end date straight into DTEND and the atelier finishes a day early in
their calendar — on the day they would then book the drive home.

Nothing about that shows up here. The file downloads, the calendar accepts it,
the event appears, and it is one day short. The guest finds out on the
seventeenth.

The stay version next door has no such adjustment and is right not to: a
booking's departure_date IS the morning they leave. Two conventions, one file
format, and the difference is a day of somebody's holiday.
"""
from datetime import timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZICS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(days_long=7, status="confirmed"):
    """One atelier and one place on it, with dates we control."""
    conn = db()
    start = m.house_today() + timedelta(days=60)
    end = start + timedelta(days=days_long - 1)     # INCLUSIVE, like the app's
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, active,
             created_at) VALUES (?, 'x', 100, 1, ?)""",
        (TAG + " Cooking in the Cuisine", m.datetime.now(m.timezone.utc).isoformat()))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
             capacity, notes, created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (wid, start.isoformat(), end.isoformat(), TAG,
         m.datetime.now(m.timezone.utc).isoformat()))
    sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    token = "zzics" + m.secrets.token_hex(8)
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
             guest_name, guest_email, party_size, status, created_at)
           VALUES (?, ?, ?, ?, 'zzics@example.invalid', 1, ?, ?)""",
        (sid, m.make_workshop_reference_code(), token, TAG + " Guest", status,
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return token, start, end


def _fields(body):
    out = {}
    for line in body.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.split(";")[0]] = value
    return out


def run():
    s = Suite("An atelier in the guest's calendar")
    _cleanup()
    anon = m.app.test_client()

    token, start, end = _booking(days_long=7)
    r = anon.get("/workshops/%s/calendar.ics" % token)
    body = r.get_data(as_text=True)

    s.section("It downloads at all")
    s.check("the route answers", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("as a calendar, not a web page",
            "text/calendar" in (r.headers.get("Content-Type") or ""),
            detail=r.headers.get("Content-Type"))
    s.check("named after the reference, so it is findable in Downloads",
            ".ics" in (r.headers.get("Content-Disposition") or "")
            and "WRK-" in (r.headers.get("Content-Disposition") or ""),
            detail=r.headers.get("Content-Disposition"))
    s.check("and it is a calendar a reader would accept",
            body.startswith("BEGIN:VCALENDAR")
            and body.rstrip().endswith("END:VCALENDAR")
            and "BEGIN:VEVENT" in body,
            detail=body[:60])

    s.section("The day it ends")
    f = _fields(body)
    s.check("it starts on the first day",
            f.get("DTSTART") == start.strftime("%Y%m%d"),
            detail=f"{f.get('DTSTART')} against {start}")
    # THE ONE THAT MATTERS. DTEND is exclusive; end_date is not.
    s.check("and ends the morning AFTER the last day, so the last day is in it",
            f.get("DTEND") == (end + timedelta(days=1)).strftime("%Y%m%d"),
            detail=f"{f.get('DTEND')} for an atelier whose last day is "
                   f"{end} — the end date copied straight in reads as "
                   f"{end.strftime('%Y%m%d')} and drops the last day, which is "
                   "the day they would book the drive home on")
    s.check("so the event covers every day of it",
            _days(f) == 7,
            detail=f"{_days(f)} days for a seven-day atelier")

    s.section("A single day is a single day")
    # The off-by-one is at its worst here: a one-day atelier with DTEND equal
    # to DTSTART is a zero-length event, which some calendars drop entirely.
    token1, start1, end1 = _booking(days_long=1)
    one = _fields(anon.get("/workshops/%s/calendar.ics" % token1).get_data(as_text=True))
    s.check("it still lasts a day", _days(one) == 1,
            detail=f"{one.get('DTSTART')} to {one.get('DTEND')} — a zero-length "
                   "event is one some calendars simply do not draw")

    s.section("What it says")
    s.check("the atelier is named", "Cooking in the Cuisine" in body,
            detail=f.get("SUMMARY"))
    s.check("with the château in front of it, for a crowded calendar",
            "Gudanes" in (f.get("SUMMARY") or ""), detail=f.get("SUMMARY"))
    s.check("the reference travels with it",
            "WRK-" in (f.get("DESCRIPTION") or ""), detail=f.get("DESCRIPTION"))
    s.check("and where to come", "Gudanes" in (f.get("LOCATION") or ""),
            detail=f.get("LOCATION"))

    s.section("Who may fetch one")
    s.check("a token nobody issued gets nothing",
            anon.get("/workshops/zzicsnope00000/calendar.ics").status_code == 404)
    cancelled, _s2, _e2 = _booking(status="cancelled")
    s.check("and a place somebody withdrew from is not still in the diary",
            anon.get("/workshops/%s/calendar.ics" % cancelled).status_code == 404,
            detail="an atelier they pulled out of should not keep reappearing")

    s.section("The pages that offer it")
    conn = db()
    conn.execute("UPDATE workshop_bookings SET status = 'confirmed' "
                 "WHERE manage_token = ?", (token,))
    conn.commit()
    conn.close()
    manage = anon.get("/workshop/manage/%s" % token)
    if manage.status_code != 200:
        manage = anon.get("/workshops/manage/%s" % token)
    s.check("the guest's own page links to it",
            manage.status_code == 200 and "calendar.ics" in manage.get_data(as_text=True),
            detail=f"HTTP {manage.status_code}")

    _cleanup()
    return s


def _days(fields):
    """How many days the event actually covers."""
    start, end = fields.get("DTSTART"), fields.get("DTEND")
    if not (start and end):
        return None
    a = m.datetime.strptime(start, "%Y%m%d").date()
    b = m.datetime.strptime(end, "%Y%m%d").date()
    return (b - a).days
