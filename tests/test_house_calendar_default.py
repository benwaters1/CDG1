"""The house calendar lives in the house mailbox's DEFAULT calendar.

On 16 October 2026 the owner added "House" in Outlook (Add calendar -> From
directory -> House) and saw an empty calendar. The app had been writing into a
second calendar inside house@, "Château de Gudanes — House", and From directory
adds a mailbox's default calendar and nothing else. A shared mailbox's other
calendars can only be handed out by an invitation sent from inside it, so the
only fix that reaches everybody is to write where Outlook looks.

What this holds, against the stand-in for Microsoft Graph (nothing reaches
Microsoft):

  THE APP ASKS FOR THE DEFAULT CALENDAR and never makes one.

  THE MOVE HAPPENS ONCE. On the first pass after the change every event is
  made in the default calendar exactly once, and the app's events are taken out
  of the old one -- without touching anything a person put there by hand, and
  without deleting the old calendar itself, which is the owner's to delete.
  Without the move, the app would go on patching the old calendar's events by
  their ids (an event's address is the mailbox's, not the calendar's) and the
  default calendar would stay empty.

  A LARGE OLD CALENDAR EMPTIES OVER SEVERAL PASSES and says how many are left.

  A CHANGED MAILBOX is noticed: the stored id no longer matches, and the
  events are made again in the new one.

  SHARING NAMES THE DEFAULT CALENDAR: house@...:\\Calendar, no sub-folder; and
  sharing recorded for the old calendar is not taken as sharing of this one.
"""
from _graph_double import FakeGraph
from _harness import Suite, clients, db
import _harness

m = _harness.m
MAILBOX = "house@example.invalid"
OLD = "calOldNamed"
TAG = "ZZHD"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    _forget(conn)


def _forget(conn):
    """Everything the app knows about Microsoft's copy, and nothing else."""
    conn.execute("DELETE FROM house_calendar_events")
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'house_calendar_%'")
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE 'house_calendar%'")
    conn.commit()


def _settle(conn, full=False):
    """Sync until nothing is waiting for a later pass; the summed counts."""
    total = {}
    for _ in range(30):
        counts = m.sync_house_calendar(conn, full=full)
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v if k != "in_range" else v
        if not counts["waiting"]:
            return total
    raise AssertionError("the house calendar never settled")


def _keys(cal):
    return [(ev.get("singleValueExtendedProperties") or [{}])[0].get("value")
            for ev in cal["events"].values()]


def run():
    s = Suite("the house calendar: the mailbox's default calendar")
    oc, _ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)

    def fixtures():
        # Of its own, so the suite proves the same on a fresh seed as on a
        # copy of the house's database.
        now = m.datetime.now(m.timezone.utc).isoformat()
        today = m.house_today()
        room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
        for n, arrive in ((1, 9), (2, 15)):
            conn.execute(
                """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                     guest_email, arrival_date, departure_date, party_size, status, total_price,
                     created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 0, ?)""",
                (room["id"], f"{TAG}{n}", f"{TAG}tok{n}", f"{TAG} Guest {n}",
                 f"zzhd.{n}@example.invalid", (today + m.timedelta(days=arrive)).isoformat(),
                 (today + m.timedelta(days=arrive + 2)).isoformat(), now))
        conn.execute("INSERT INTO tasks (title, due_date, status, assigned_to_user_id, created_at) "
                     "VALUES (?, ?, 'open', ?, ?)",
                     (f"{TAG} wax the floors", (today + m.timedelta(days=4)).isoformat(),
                      emp["id"], now))
        conn.commit()

    fixtures()
    fake = FakeGraph(calendar_mailbox=MAILBOX)
    with fake, m.app.test_request_context("/"):
        desired = {i["key"] for i in m.house_calendar_items(
            conn, *m.house_calendar_window(), m.house_calendar_areas(conn))}
        s.check("there is something to write (or this suite proves nothing)",
                {k for k in desired if k.startswith(("stay:", "task:"))} and len(desired) >= 3,
                detail=str(sorted(desired))[:300])

        s.section("It asks Microsoft for the default calendar, and makes none")
        cal_id = m.ensure_house_calendar(conn, "fake-calendar-token")
        asked = [c[1] for c in fake.calls if c[0] == "GET"]
        s.check("it asks for /users/{mailbox}/calendar",
                f"/users/{MAILBOX}/calendar" in asked, detail=str(asked)[:300])
        s.check("and does not go looking for one by name",
                not any(p.endswith("/calendars") for p in asked), detail=str(asked)[:300])
        s.check("the id it keeps is the default calendar's", cal_id == fake.default_calendar
                and m._hc_get(conn, "id") == fake.default_calendar, detail=cal_id)
        s.check("it never makes a calendar",
                not [c for c in fake.writes("POST") if c[1].endswith("/calendars")],
                detail=str(fake.writes("POST"))[:300])

        s.section("The move off the named calendar happens once")
        # The state production was in: everything written into a second
        # calendar named for the house, and the owner's by-hand sharing
        # recorded against it.
        _forget(conn)
        fake.calendars[OLD] = {"name": m.HOUSE_CALENDAR_NAME, "events": {}}
        fake.default_calendar, real_default = OLD, fake.default_calendar
        _settle(conn)
        fake.default_calendar = real_default
        fake.calendars[OLD]["events"]["by-hand"] = {
            "subject": "Somebody's own note", "start": {"dateTime": "2026-11-01T09:00:00"},
            "end": {"dateTime": "2026-11-01T10:00:00"}}
        m._hc_set(conn, "shared_by_hand", m.datetime.now(m.timezone.utc).isoformat())
        m._hc_set(conn, "shared", "Everybody in the organisation can read it: shared by hand.")
        conn.commit()
        s.check("(set up: the old calendar holds the app's events, the default none)",
                len(fake.calendars[OLD]["events"]) == len(desired) + 1
                and not fake.calendars[real_default]["events"],
                detail=f"{len(fake.calendars[OLD]['events'])} old, "
                       f"{len(fake.calendars[real_default]['events'])} default")

        fake.calls.clear()
        moved = _settle(conn)
        new_keys = _keys(fake.calendars[real_default])
        s.check("every event is made in the default calendar",
                set(new_keys) == desired, detail=f"{len(set(new_keys))} of {len(desired)}")
        s.check("each exactly once", len(new_keys) == len(set(new_keys)) == moved["created"],
                detail=f"{len(new_keys)} event(s), {moved['created']} created")
        s.check("and none is patched in the old calendar instead",
                not fake.writes("PATCH"), detail=str(fake.writes("PATCH"))[:300])
        s.check("the app's events are taken out of the old calendar",
                not [k for k in _keys(fake.calendars[OLD]) if k],
                detail=f"{moved.get('moved_out')} taken out; left: {_keys(fake.calendars[OLD])}")
        s.check("somebody's own event there is left alone", "by-hand" in fake.calendars[OLD]["events"])
        s.check("and the old calendar itself is not deleted",
                OLD in fake.calendars
                and not [c for c in fake.writes("DELETE") if "/calendars/" in c[1]])
        s.check("the pass says what it did", "taken out of the old calendar"
                in m.house_calendar_counts_words(moved), detail=m.house_calendar_counts_words(moved))
        note = m._hc_get(conn, "retired_note")
        s.check("the page is told the old calendar's name, and that it is the owner's to delete",
                m.HOUSE_CALENDAR_NAME in note and "Delete it" in note, detail=note)
        s.check("sharing recorded for the old calendar is not taken for this one",
                not m._hc_get(conn, "shared_by_hand") and not m._hc_get(conn, "shared"))

        fake.calls.clear()
        again = m.sync_house_calendar(conn)
        s.check("the next pass sends Microsoft nothing",
                not fake.writes() and again["unchanged"] == len(desired),
                detail=f"{again}; {fake.writes()[:3]}")
        s.check("nor looks at the old calendar again, once it is empty",
                not any(OLD in c[1] for c in fake.calls), detail=str(fake.calls)[:300])
        full = _settle(conn, full=True)
        s.check("and the nightly check finds no doubles to clear",
                not full["strays"] and not full["created"]
                and len(_keys(fake.calendars[real_default])) == len(desired), detail=str(full))

        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("the page shows the line about the old calendar",
                "The old calendar" in page and "Delete it when nobody needs it" in page)
        del fake.calendars[OLD]
        _settle(conn, full=True)
        s.check("and drops it the night after the owner deletes it",
                not m._hc_get(conn, "retired") and not m._hc_get(conn, "retired_note"))

        s.section("A large old calendar empties over several passes")
        _forget(conn)
        fake.calendars = {real_default: {"name": "Calendar", "events": {}}}
        fake.calendars[OLD] = {"name": m.HOUSE_CALENDAR_NAME, "events": {}}
        for n in range(12):
            fake.calendars[OLD]["events"][f"old-{n}"] = {
                "subject": f"old {n}", "start": {"dateTime": "2026-11-01T09:00:00"},
                "end": {"dateTime": "2026-11-01T10:00:00"},
                "singleValueExtendedProperties": [
                    {"id": m.HOUSE_EVENT_KEY_PROP, "value": f"stay:old{n}"}]}
        m._hc_set(conn, "id", OLD)
        conn.commit()
        cap = m.HOUSE_CALENDAR_MAX_WRITES
        m.HOUSE_CALENDAR_MAX_WRITES = len(desired) + 5
        try:
            first = m.sync_house_calendar(conn)
            s.check("this calendar is filled first, the old one with what the pass has left",
                    first["created"] == len(desired) and first["moved_out"] == 5
                    and first["waiting"] == 7, detail=str(first))
            s.check("and the page says how many are left",
                    "7 of the app's old events still to take out" in m._hc_get(conn, "retired_note"),
                    detail=m._hc_get(conn, "retired_note"))
            _settle(conn)
            s.check("the passes after finish it",
                    not [k for k in _keys(fake.calendars[OLD]) if k]
                    and m._hc_get(conn, "retired_empty") == "1", detail=str(_keys(fake.calendars[OLD])))
        finally:
            m.HOUSE_CALENDAR_MAX_WRITES = cap

        s.section("A changed mailbox is noticed")
        _forget(conn)
        fake.calendars = {real_default: {"name": "Calendar", "events": {}}}
        m._hc_set(conn, "id", "calInAnotherMailbox")
        conn.commit()
        changed = _settle(conn)
        s.check("the events are made in this mailbox's calendar, once each",
                sorted(_keys(fake.calendars[real_default])) == sorted(desired), detail=str(changed))
        s.check("and the calendar it can no longer see is let go without complaint",
                not m._hc_get(conn, "retired") and not m._hc_get(conn, "retired_note")
                and not changed["failed"])

        s.section("Sharing names the default calendar")
        cmd = m.house_calendar_share_command()
        s.check("the Exchange line names the mailbox's Calendar folder, with nothing under it",
                cmd == f'Set-MailboxFolderPermission -Identity "{MAILBOX}:' + chr(92)
                + 'Calendar" -User Default -AccessRights Reviewer', detail=cmd)
        oc.post("/admin/house-calendar/share")
        s.check("and Share makes the default calendar's organisation entry read",
                fake.permissions[real_default][0]["role"] == "read",
                detail=str(fake.permissions.get(real_default)))

        s.section("The page says what Outlook will show")
        page = oc.get("/admin/house-calendar").get_data(as_text=True)
        s.check("From directory, House, and nothing more to pick",
                "From directory → <em>House</em> → Add" in page, detail="the setup step")
        s.check("not the old calendar's name", m.HOUSE_CALENDAR_NAME not in page)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
