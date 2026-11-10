"""One calendar event refused 64,668 times (brief of 10 November 2026).

/admin/house-calendar, "Not on the calendar": a photoshoot for 8 guests on
22 November 2026 -- Microsoft answered 400 "At least one property
failed validation", tries 64668, sent again every thirty seconds for ever.

What it had that the other eighty events did not: it was saved as "22
November 2026 to 26 October 2026". The + Add event form took a last day
before the first, the house calendar sent an all-day event ending before it
began, and Microsoft refuses that every time. This holds the app to:

  NOTHING ENDS BEFORE IT BEGINS. An end before the start is read as the
  start, as the app's own calendar and the availability check already read
  it, so the event lands on its first day -- tested with that event's shape
  against a stand-in that refuses what Microsoft refuses.

  WHAT WAS REFUSED IS WRITTEN DOWN, because Microsoft's words name no
  property: the subject, the times, the categories, the location, the body's
  length and the app's key, in the log and on the page.

  A REFUSAL BACKS OFF. Five tries at once, then hourly for a day, then once a
  night with the full check; a changed event starts again, and Sync now tries
  everything.

  A REFUSAL OLDER THAN A DAY reaches the owner home and becomes a task, and
  both go when the event lands.

  AND THE FORM THAT LET IT IN refuses a last day before the first.

Nothing reaches Microsoft: tests/_graph_double.py plays its part.
"""
import contextlib
import io
from datetime import timedelta

from _graph_double import FakeGraph
from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZHR"
MAILBOX = "house@example.invalid"


def _cleanup(conn):
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title LIKE ?",
                 (m.WATCH_TASK_ORIGIN, f"%{TAG}%"))
    conn.execute("DELETE FROM house_calendar_events")
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'house_calendar_%'")
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE 'house_calendar%'")
    conn.commit()


def _row(conn, key):
    return conn.execute("SELECT * FROM house_calendar_events WHERE key = ?", (key,)).fetchone()


def _sends(fake, key):
    """The writes this pass made for one event, by the app's own key."""
    out = []
    for method, _path, body, _h in fake.writes():
        props = (body or {}).get("singleValueExtendedProperties") or []
        if any(p.get("value") == key for p in props):
            out.append(method)
    return out


def run():
    s = Suite("an event Microsoft refuses")
    oc, _ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    # The photoshoot as it was saved: twelve days ahead, "to" a day fifteen
    # days ago.
    first_day = today + timedelta(days=12)
    shoot = conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
             contact_email, preferred_date, end_date, guest_count, status, created_at)
           VALUES (?, ?, 'photoshoot', ?, '', ?, ?, 8, 'confirmed', ?)""",
        (f"{TAG}E1", f"{TAG}tok", f"{TAG} Photoshoot Client", first_day.isoformat(),
         (today - timedelta(days=15)).isoformat(), now.isoformat())).lastrowid
    conn.commit()
    key = f"event:{shoot}"

    s.section("Nothing ends before it begins")
    start, end = m.house_calendar_window()
    with m.app.test_request_context("/"):
        items = {i["key"]: i for i in m.house_calendar_items(conn, start, end)}
        body = m.graph_event_body(items[key]) if key in items else None
    item = items.get(key)
    s.check("the photoshoot is on the house calendar's list", item is not None,
            detail=str([k for k in items if k.startswith("event:")]))
    s.check("on its first day, all day",
            item and item["all_day"] and item["start"] == f"{first_day.isoformat()}T00:00:00",
            detail=item and item["start"])
    s.check("and over the morning after, not fifteen days before it began",
            item and item["end"] == f"{(first_day + timedelta(days=1)).isoformat()}T00:00:00",
            detail=item and item["end"])
    s.check("which is how the app's own calendar has it: on that one day",
            body and not m.graph_event_doubts(body), detail=body and m.graph_event_doubts(body))
    s.check("and the check names what Microsoft refuses, for the next event like it",
            m.graph_event_doubts({"start": {"dateTime": "2026-11-22T00:00:00"},
                                  "end": {"dateTime": "2026-10-27T00:00:00"}, "isAllDay": True})
            == ["it ends before it begins"])

    with FakeGraph(calendar_mailbox=MAILBOX) as fake:
        fake.check_times = True
        with m.app.test_request_context("/"):
            first = m.sync_house_calendar(conn)
        sent = [b for meth, _p, b, _h in fake.writes("POST")
                if any(p.get("value") == key for p in (b or {}).get("singleValueExtendedProperties") or [])]
        row = _row(conn, key)
        s.check("a stand-in that refuses what Microsoft refuses takes it",
                row and row["graph_id"] and not row["last_error"],
                detail=row and f"{row['last_error']} {first}")
        s.check("sent as an all-day event that ends after it begins",
                sent and sent[0]["end"]["dateTime"] > sent[0]["start"]["dateTime"],
                detail=sent and f"{sent[0]['start']} {sent[0]['end']}")

        s.section("What was refused is written down")
        task = conn.execute(
            """INSERT INTO tasks (title, due_date, status, assigned_to_user_id, created_at)
               VALUES (?, ?, 'open', ?, ?)""",
            (f"{TAG} hang the lanterns REFUSEME", (today + timedelta(days=4)).isoformat(),
             emp["id"], now.isoformat())).lastrowid
        conn.commit()
        tkey = f"task:{task}"
        fake.refuse_subjects.add("REFUSEME")
        said = io.StringIO()
        with contextlib.redirect_stdout(said), m.app.test_request_context("/"):
            bad = m.sync_house_calendar(conn)
        row = _row(conn, tkey)
        log = said.getvalue()
        s.check("the refusal is counted", bad["failed"] == 1, detail=str(bad))
        s.check("Microsoft's words are on the row", row and "refused by the double" in (row["last_error"] or ""),
                detail=row and row["last_error"])
        what = (row["refused_body"] or "") if row else ""
        s.check("and what was sent: the subject, the times, the categories, the location, "
                "the body's length and the key",
                all(bit in what for bit in ("REFUSEME", "all day", "from ", " to ",
                                            "categories Gudanes · Tasks", "location ",
                                            "body ", f"key {tkey}")), detail=what)
        s.check("never the body's own words, which can name a guest's extras",
                "For " + emp["name"] not in what, detail=what)
        s.check("the same goes to the log", "Microsoft refused " + tkey in log and "Sent: subject" in log,
                detail=log[-300:])
        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("and the page shows it under Not on the calendar, with the next try",
                "REFUSEME" in page and "Sent: subject" in page and "on the next pass" in page)

        s.section("A refusal backs off")
        for _ in range(m.HOUSE_CALENDAR_QUICK_TRIES - 1):
            with m.app.test_request_context("/"):
                m.sync_house_calendar(conn)
        row = _row(conn, tkey)
        s.check("five tries at the pass's own pace", row["attempts"] == m.HOUSE_CALENDAR_QUICK_TRIES,
                detail=str(row["attempts"]))
        fake.calls.clear()
        with m.app.test_request_context("/"):
            held = m.sync_house_calendar(conn)
        s.check("then the same body is not sent on the next pass",
                not _sends(fake, tkey) and held["held"] == 1, detail=f"{held} {_sends(fake, tkey)}")
        s.check("and the count stays where it was", _row(conn, tkey)["attempts"] == m.HOUSE_CALENDAR_QUICK_TRIES)
        s.check("and the page says when it is next tried",
                "within the hour" in oc.get("/admin/house-calendar").get_data(as_text=True))
        conn.execute("UPDATE house_calendar_events SET failed_at = ? WHERE key = ?",
                     ((now - timedelta(minutes=61)).isoformat(), tkey))
        conn.commit()
        fake.calls.clear()
        with m.app.test_request_context("/"):
            m.sync_house_calendar(conn)
        s.check("an hour after the last refusal it is tried again", _sends(fake, tkey) == ["POST"],
                detail=str(_sends(fake, tkey)))
        conn.execute("UPDATE house_calendar_events SET failed_at = ?, first_failed_at = ? WHERE key = ?",
                     ((now - timedelta(minutes=61)).isoformat(), (now - timedelta(hours=25)).isoformat(),
                      tkey))
        conn.commit()
        fake.calls.clear()
        with m.app.test_request_context("/"):
            m.sync_house_calendar(conn)
        s.check("refused for a day, the thirty-second pass leaves it alone", not _sends(fake, tkey),
                detail=str(_sends(fake, tkey)))
        s.check("the page says tonight",
                "tonight, with the check against Microsoft" in oc.get("/admin/house-calendar").get_data(as_text=True))
        fake.calls.clear()
        with m.app.test_request_context("/"):
            m.sync_house_calendar(conn, full=True)
        s.check("and the nightly check tries it", _sends(fake, tkey) == ["POST"],
                detail=str(_sends(fake, tkey)))
        fake.calls.clear()
        r = oc.post("/admin/house-calendar/sync", follow_redirects=True)
        s.check("Sync now, pressed by a person, tries it too", _sends(fake, tkey) == ["POST"],
                response=r, detail=str(_sends(fake, tkey)))
        attempts_before = _row(conn, tkey)["attempts"]
        conn.execute("UPDATE tasks SET title = ? WHERE id = ?",
                     (f"{TAG} hang the lanterns, both REFUSEME", task))
        conn.commit()
        fake.calls.clear()
        with m.app.test_request_context("/"):
            m.sync_house_calendar(conn)
        row = _row(conn, tkey)
        s.check("a changed event is a new body, sent at once", _sends(fake, tkey) == ["POST"],
                detail=str(_sends(fake, tkey)))
        s.check("and its count starts again from one", row["attempts"] == 1 and attempts_before > 1,
                detail=f"{attempts_before} -> {row['attempts']}")
        s.check("as does when its refusals began",
                m.parse_datetime_iso(row["first_failed_at"]) > now - timedelta(minutes=5),
                detail=row["first_failed_at"])

        s.section("Refused for more than a day: the owner home and a task")
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
        s.check("refused since this morning, the owner home says nothing",
                not [w for w in warned if "house calendar" in w["title"]],
                detail=str([w["title"] for w in warned])[:200])
        s.check("and the calendar is not called broken for one event",
                not m._hc_get(conn, "last_error"), detail=m._hc_get(conn, "last_error"))
        conn.execute("UPDATE house_calendar_events SET first_failed_at = ? WHERE key = ?",
                     ((now - timedelta(hours=26)).isoformat(), tkey))
        conn.commit()
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
        line = [w for w in warned if "house calendar" in w["title"]]
        s.check("refused for a day, it is on the owner home, named",
                line and "REFUSEME" in line[0]["detail"] and line[0]["severity"] != "blocker",
                detail=str(line)[:300])
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        watch = conn.execute("SELECT * FROM tasks WHERE origin = ? AND title LIKE ? AND status != 'done'",
                             (m.WATCH_TASK_ORIGIN, "%REFUSEME%on the house calendar%")).fetchall()
        s.check("and a task to put it right", len(watch) == 1, detail=str([w["title"] for w in watch]))
        s.check("which says what Microsoft said and where to look",
                watch and "refused by the double" in watch[0]["notes"]
                and "House calendar" in watch[0]["notes"], detail=watch and watch[0]["notes"])
        s.check("and it is a kind the owner can route",
                "house_calendar" in m.WATCH_TASK_KINDS)
        fake.refuse_subjects.clear()
        r = oc.post("/admin/house-calendar/sync", follow_redirects=True)
        with m.app.test_request_context("/"):
            warned = m.owner_home_warnings(conn, today)
            m.generate_watch_tasks(conn)
        row = _row(conn, tkey)
        s.check("once it lands", row and row["graph_id"] and not row["last_error"]
                and not row["refused_body"], response=r)
        s.check("the line on the owner home goes by itself",
                not [w for w in warned if "house calendar" in w["title"]],
                detail=str([w["title"] for w in warned])[:200])
        s.check("and so does the task",
                not conn.execute("SELECT 1 FROM tasks WHERE origin = ? AND title LIKE ? AND status != 'done'",
                                 (m.WATCH_TASK_ORIGIN, "%REFUSEME%on the house calendar%")).fetchone())

    s.section("The form that let it in")
    before = conn.execute("SELECT COUNT(*) FROM event_inquiries").fetchone()[0]
    r = oc.post("/admin/events/new", data={
        "event_type": "photoshoot", "contact_name": f"{TAG} Second Client",
        "preferred_date": "2026-11-22", "end_date": "2026-10-26", "guest_count": "8",
        "status": "confirmed"}, follow_redirects=True)
    after = conn.execute("SELECT COUNT(*) FROM event_inquiries").fetchone()[0]
    s.check("+ Add event refuses a last day before the first",
            after == before and any("last day is before the first" in f for f in flashes(r)),
            response=r, detail=f"{before} -> {after}")
    r = oc.post("/admin/events/new", data={
        "event_type": "photoshoot", "contact_name": f"{TAG} Third Client",
        "preferred_date": "2026-11-22", "end_date": "2026-11-23", "guest_count": "8",
        "status": "new"}, follow_redirects=True)
    s.check("and still takes one that ends after it begins",
            conn.execute("SELECT 1 FROM event_inquiries WHERE contact_name = ?",
                         (f"{TAG} Third Client",)).fetchone() is not None, response=r)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
