"""The owner's own Outlook diary on "The day" (brief of 26 Nov 2026).

"The day" on the owner home was the house's day -- arrivals, dinner, tasks --
and not the owner's own meetings. Now today's events from one Outlook
calendar (accounts@ unless another is named) go among it by time:

  - read by the MAIL READER's app, once it has Calendars.Read; the house
    calendar's own app is not touched;
  - TIME AND SUBJECT ONLY: never who is invited or what the event says, and
    a private appointment shows only as one; a cancelled one not at all;
  - OFF when the permission is missing, and the House calendar page says how
    to add it, beside the existing steps;
  - not counted in "N of M done": a meeting is not something the house does.

Nothing reaches Microsoft: tests/_graph_double.py plays its part, and this
suite answers the calendar read itself.
"""
from datetime import datetime, time as dtime, timedelta, timezone

from _graph_double import FakeGraph
from _harness import Suite, clients, db, flashes, keep_rows
import _harness

m = _harness.m
TAG = "ZZOD"
ACC = "zzod-accounts@zzhouse.invalid"


def _at(today, hh, mm):
    local = datetime.combine(today, dtime(hh, mm), tzinfo=m.LOCAL_TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000")


def _events(today):
    return [
        {"subject": f"{TAG} Call with the notaire", "isAllDay": False, "isCancelled": False,
         "sensitivity": "normal",
         "start": {"dateTime": _at(today, 9, 30), "timeZone": "UTC"},
         "end": {"dateTime": _at(today, 10, 15), "timeZone": "UTC"},
         # What a real event carries and the owner home must never show.
         "attendees": [{"emailAddress": {"name": "ZZOD Maître Durand", "address": "durand@notaire.example"}}],
         "body": {"content": "ZZOD the sale of the lower meadow"}},
        {"subject": f"{TAG} Dentist", "isAllDay": False, "isCancelled": False, "sensitivity": "private",
         "start": {"dateTime": _at(today, 14, 0), "timeZone": "UTC"},
         "end": {"dateTime": _at(today, 15, 0), "timeZone": "UTC"}},
        {"subject": f"{TAG} Called off", "isAllDay": False, "isCancelled": True, "sensitivity": "normal",
         "start": {"dateTime": _at(today, 16, 0), "timeZone": "UTC"},
         "end": {"dateTime": _at(today, 17, 0), "timeZone": "UTC"}},
    ]


def run():
    s = Suite("the owner's own diary on The day")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = keep_rows(conn, "app_settings", "key IN (?, ?)", (m.OWNER_DIARY_KEY, m.OWNER_DIARY_CACHE_KEY))
    today = m.house_today()
    try:
        _sections(s, oc, conn, today)
    finally:
        conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)", (m.OWNER_DIARY_KEY, m.OWNER_DIARY_CACHE_KEY))
        conn.commit()
        put_back()
        conn.close()
    return s


def _sections(s, oc, conn, today):
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (m.OWNER_DIARY_KEY, ACC))
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.OWNER_DIARY_CACHE_KEY,))
    conn.commit()
    with FakeGraph(mailboxes=[ACC]) as fake:
        asked = []
        allowed = {"yes": False}
        plain = fake.get

        def graph_get(path, token, params=None, headers=None):
            if path.endswith("/calendarView"):
                asked.append((path, dict(params or {}), dict(headers or {})))
                if not allowed["yes"]:
                    m._GRAPH_ANSWER.error = ("Microsoft answered 403 (ErrorAccessDenied: Access is denied. "
                                             "Check credentials and try again.)")
                    return None
                m._GRAPH_ANSWER.error = None
                return {"value": _events(today)}
            return plain(path, token, params, headers)
        m.graph_get = graph_get

        s.section("Without Calendars.Read: off, and the House calendar page says how")
        try:
            m.run_owner_diary_job(conn)
            refused = None
        except m.JobNotSetUp as e:
            refused = str(e)
        s.check("the job says it could not run, and why", refused and "Calendars.Read" in refused,
                detail=str(refused))
        home = oc.get("/").get_data(as_text=True)
        s.check("nothing from the diary is on the owner home", "data-diary" not in home and TAG not in home)
        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("the House calendar page says Microsoft refused",
                "data-diary-denied" in page and "403" in page)
        s.check("and gives the steps beside the existing ones: Calendars.Read on the mail reader's app",
                "Calendars.Read" in page and "the mail reader's app" in page and "Grant admin consent" in page
                and "Setting it up in Microsoft 365" in page)

        s.section("With it: today's events on The day, time and subject only")
        allowed["yes"] = True
        said = m.run_owner_diary_job(conn)
        s.check("the job reads them", "2 events today" in said, detail=said)
        path, params, headers = asked[-1]
        s.check("from the named mailbox's calendar, today only",
                path == f"/users/{ACC}/calendarView" and params.get("startDateTime") and params.get("endDateTime"),
                detail=str((path, params)))
        s.check("asking for no attendees and no body",
                "attendees" not in params.get("$select", "") and "body" not in params.get("$select", ""),
                detail=params.get("$select"))
        home = oc.get("/").get_data(as_text=True)
        day = home.split("The day", 1)[-1].split("oh-col--rail", 1)[0]
        s.check("the meeting is on The day, with its time", "09:30" in day and f"{TAG} Call with the notaire" in day,
                detail=day[:800])
        s.check("and until when", "Until 10:15" in day)
        s.check("never who is invited, nor what it says",
                "Durand" not in home and "lower meadow" not in home)
        s.check("a private appointment shows only as one",
                "Private appointment" in day and f"{TAG} Dentist" not in home)
        s.check("a cancelled one not at all", "Called off" not in home)
        s.check("each opens Outlook, in a new tab", "data-diary" in day and 'target="_blank"' in day
                and m.OWNER_DIARY_CALENDAR_LINK in day)
        s.check("and the stored copy holds nothing more than time and subject",
                all(set(e) <= {"start", "end", "all_day", "subject"} for e in m.owner_diary_state(conn)["events"]))
        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("the House calendar page says it is reading", "data-diary-ok" in page and ACC in page)

        s.section("Not counted among what the house has to do")
        with m.app.test_request_context("/"):
            owner_row = conn.execute("SELECT * FROM users WHERE role = 'owner' LIMIT 1").fetchone()
            house_rows = m.owner_home_day(conn, today, owner_row)
        counted = home.split('class="oh-mono">', 1)[-1].split("</span>", 1)[0] if "done</span>" in home else ""
        s.check("\"N of M done\" counts the house's day only",
                f"of {len(house_rows)} done" in counted, detail=counted)

        s.section("Which calendar is the owner's to say")
        r = oc.post("/admin/house-calendar/diary", data={"mailbox": "not an address"}, follow_redirects=True)
        s.check("an address that is not one is refused, and nothing changed",
                any("not an email address" in f for f in flashes(r)) and m.owner_diary_mailbox(conn) == ACC,
                response=r)
        r = oc.post("/admin/house-calendar/diary", data={"mailbox": ""}, follow_redirects=True)
        home = oc.get("/").get_data(as_text=True)
        s.check("blank takes the diary off the owner home",
                m.owner_diary_mailbox(conn) == "" and "data-diary" not in home
                and any("off the owner home" in f for f in flashes(r)), response=r)
        n = len(asked)
        try:
            m.run_owner_diary_job(conn)
        except m.JobNotSetUp:
            pass
        s.check("and nothing is read while it is off", len(asked) == n)
        r = oc.post("/admin/house-calendar/diary", data={"mailbox": ACC.upper()}, follow_redirects=True)
        s.check("named again, it is read at once, and says what it found",
                m.owner_diary_mailbox(conn) == ACC and any("2 events today" in f for f in flashes(r))
                and len(asked) == n + 1, response=r)

    s.section("A day's read is that day's only")
    state = m.owner_diary_state(conn)
    state["day"] = (today - timedelta(days=1)).isoformat()
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                 (m.OWNER_DIARY_CACHE_KEY, m.json.dumps(state)))
    conn.commit()
    s.check("yesterday's events are not today's", m.owner_home_diary(conn, today) == [])
    s.check("the job runs by itself, every quarter of an hour",
            any(j[0] == "owner_diary" and j[3] == 900 and j[4] is m.run_owner_diary_job
                for j in m.AUTOMATION_JOBS))


if __name__ == "__main__":
    print(run().report())
