"""Dates for the house: the school holidays on the house calendar.

On 16 October 2026 the owner asked for the French school holidays in the house
calendar. Nobody could put them there: the House calendar in Outlook is the
house mailbox's own, which only the app can write, and the app only wrote
bookings, dinners, ateliers, events, tasks and the rota. There was no way to put
anything else on it -- not in Outlook, not in the app.

What this holds (nothing reaches Microsoft; Graph is the stand-in):

  A DATE IS ADDED ON THE HOUSE CALENDAR PAGE, with a name, a first day, an
  optional last day and a note, and refused in words when it makes no sense.

  IT IS ON THE APP'S CALENDAR on every day it covers, and on the overview, and
  an employee sees it without a link into an owner's page.

  IT GOES INTO THE HOUSE CALENDAR as one all-day event from its first day to
  the morning after its last, coloured as Dates, and leaves when it is removed.

  A NEW AREA IS NOT LOST TO AN OLD CHOICE. A choice of areas saved before Dates
  existed carries Dates; once somebody unticks it, it stays off.
"""
from _graph_double import FakeGraph
from _harness import Suite, clients, db
import _harness

m = _harness.m
MAILBOX = "house@example.invalid"
TAG = "ZZHDT"


def _cleanup(conn):
    conn.execute("DELETE FROM house_dates WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM house_calendar_events")
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'house_calendar_%'")
    conn.execute("DELETE FROM automation_runs WHERE job_name LIKE 'house_calendar%'")
    conn.commit()


def _settle(conn):
    for _ in range(30):
        counts = m.sync_house_calendar(conn)
        if not counts["waiting"]:
            return counts
    raise AssertionError("the house calendar never settled")


def _row(conn, title):
    return conn.execute("SELECT * FROM house_dates WHERE title = ?", (title,)).fetchone()


def run():
    s = Suite("dates for the house: the school holidays on the house calendar")
    oc, ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)
    today = m.house_today()
    first = today + m.timedelta(days=10)
    last = today + m.timedelta(days=13)
    title = f"{TAG} Vacances de la Toussaint (Zone C)"

    s.section("Adding a date on the House calendar page")
    r = oc.post("/admin/house-calendar/dates", data={
        "title": f"  {title}  ", "start_date": first.isoformat(),
        "end_date": last.isoformat(), "note": "Classes resume the Monday after"},
        follow_redirects=True)
    row = _row(conn, title)
    s.check("the date is saved, its name tidied", row is not None
            and row["start_date"] == first.isoformat() and row["end_date"] == last.isoformat(),
            response=r, detail=str(dict(row) if row else None))
    s.check("and the owner is told it is on the calendar",
            "Added " + title in r.get_data(as_text=True), response=r)
    page = oc.get("/admin/house-calendar").get_data(as_text=True)
    s.check("the page lists it, with an anchor to land on",
            title in page and f'id="date-{row["id"]}"' in page if row else False)

    one_day = f"{TAG} Marché de Noël"
    oc.post("/admin/house-calendar/dates", data={"title": one_day,
                                                 "start_date": first.isoformat()})
    single = _row(conn, one_day)
    s.check("with no last day it is one day", single is not None
            and single["end_date"] == single["start_date"] == first.isoformat(),
            detail=str(dict(single) if single else None))

    s.section("Refused in words when it makes no sense")
    for label, form, words in (
            ("a last day before the first", {"title": f"{TAG} backwards",
                                             "start_date": last.isoformat(),
                                             "end_date": first.isoformat()},
             "the last day is before the first"),
            ("no name", {"title": "   ", "start_date": first.isoformat()}, "give it a name"),
            ("no first day", {"title": f"{TAG} undated", "start_date": ""},
             "choose the first day"),
            ("a last day that is not a date", {"title": f"{TAG} garbled",
                                               "start_date": first.isoformat(),
                                               "end_date": "next Tuesday"},
             "the last day is not a date"),
            ("more than a year", {"title": f"{TAG} forever", "start_date": first.isoformat(),
                                  "end_date": (first + m.timedelta(days=400)).isoformat()},
             "under 366 days")):
        before = conn.execute("SELECT COUNT(*) FROM house_dates").fetchone()[0]
        r = oc.post("/admin/house-calendar/dates", data=form, follow_redirects=True)
        after = conn.execute("SELECT COUNT(*) FROM house_dates").fetchone()[0]
        s.check(f"{label}: nothing saved, and it says why",
                after == before and words in r.get_data(as_text=True), response=r,
                detail=f"{before} -> {after}")
    r = ec.post("/admin/house-calendar/dates", data={"title": f"{TAG} by staff",
                                                     "start_date": first.isoformat()})
    s.check("an employee cannot add one", _row(conn, f"{TAG} by staff") is None
            and r.status_code in (302, 403), response=r)

    s.section("On the app's calendar, on every day it covers")
    with m.app.test_request_context("/"):
        cal = m.build_calendar(conn, "month", first)
        days = {c["iso"]: c for c in cal["cells"]}
        covered = []
        d = first
        while d <= last:
            cell = days.get(d.isoformat()) or {}
            covered.append(any(title in (e.get("title") or "") for e in cell.get("events", [])))
            d += m.timedelta(days=1)
        s.check("each of its four days carries it", covered and all(covered),
                detail=str(covered))
        after_cell = days.get((last + m.timedelta(days=1)).isoformat()) or {}
        s.check("and the day after does not",
                not any(title in (e.get("title") or "") for e in after_cell.get("events", [])))
        staff_cal = m.build_calendar(conn, "month", first, viewer=dict(emp, role="employee"))
        staff_rows = [e for c in staff_cal["cells"] for e in c.get("events", [])
                      if title in (e.get("title") or "")]
        s.check("an employee sees it, with no link into the owner's page",
                staff_rows and all(e.get("link") is None for e in staff_rows),
                detail=str([e.get("link") for e in staff_rows])[:200])
    r = oc.get(f"/calendar?view=month&date={first.isoformat()}")
    s.check("the calendar page shows it, with a Dates chip to filter by",
            title in r.get_data(as_text=True) and 'data-filter-lane="date"' in r.get_data(as_text=True),
            response=r)
    r = oc.get(f"/admin/overview?view=month&date={first.isoformat()}")
    s.check("and the overview lists it as a date", title in r.get_data(as_text=True)
            and 'data-kind="date"' in r.get_data(as_text=True), response=r)

    s.section("Into the house calendar in Outlook")
    fake = FakeGraph(calendar_mailbox=MAILBOX)
    with fake, m.app.test_request_context("/"):
        items = {i["key"]: i for i in m.house_calendar_items(
            conn, *m.house_calendar_window(), m.house_calendar_areas(conn))}
        it = items.get(f"date:{row['id']}") if row else None
        s.check("it is one all-day event, from its first day to the morning after its last",
                it is not None and it["all_day"] and it["area"] == "dates"
                and it["start"] == f"{first.isoformat()}T00:00:00"
                and it["end"] == f"{(last + m.timedelta(days=1)).isoformat()}T00:00:00",
                detail=str(it))
        s.check("with the Dates mark and its note", it is not None
                and it["subject"].startswith("📅 ") and title in it["subject"]
                and it["body"] == "Classes resume the Monday after", detail=str(it))
        one = items.get(f"date:{single['id']}") if single else None
        s.check("a one-day date ends the next morning", one is not None
                and one["end"] == f"{(first + m.timedelta(days=1)).isoformat()}T00:00:00",
                detail=str(one))

        _settle(conn)
        cal_id = fake.default_calendar
        written = [e for e in fake.calendars[cal_id]["events"].values()
                   if title in (e.get("subject") or "")]
        s.check("the sync writes it to Microsoft once", len(written) == 1,
                detail=str(written)[:300])
        s.check("as an all-day event in the Dates colour",
                written and written[0].get("isAllDay") is True
                and written[0].get("categories") == ["Gudanes · Dates"],
                detail=str(written)[:300])
        s.check("and the Dates colour is made in the mailbox",
                "Gudanes · Dates" in fake.categories, detail=str(fake.categories))

        s.section("Removing it")
        r = oc.post(f"/admin/house-calendar/dates/{row['id']}/delete", follow_redirects=True)
        s.check("it is taken off", _row(conn, title) is None
                and "Removed " + title in r.get_data(as_text=True), response=r)
        _settle(conn)
        s.check("and leaves the house calendar on the next pass",
                not [e for e in fake.calendars[cal_id]["events"].values()
                     if title in (e.get("subject") or "")])
        r = oc.post(f"/admin/house-calendar/dates/{row['id']}/delete", follow_redirects=True)
        s.check("removing it twice says nothing was removed",
                "nothing was removed" in r.get_data(as_text=True), response=r)

    s.section("A new area is not lost to an old choice")
    # The state a house that chose its areas before Dates existed is in.
    m._hc_set(conn, "areas", "rooms,events")
    conn.execute("DELETE FROM app_settings WHERE key = 'house_calendar_areas_known'")
    conn.commit()
    s.check("a choice saved before Dates existed carries Dates",
            m.house_calendar_areas(conn) == ["rooms", "events", "dates"],
            detail=str(m.house_calendar_areas(conn)))
    oc.post("/admin/house-calendar/areas", data={"areas": ["rooms", "events"]})
    s.check("unticked once it exists, it stays off",
            m.house_calendar_areas(conn) == ["rooms", "events"],
            detail=str(m.house_calendar_areas(conn)))
    page = oc.get("/admin/house-calendar").get_data(as_text=True)
    s.check("and the page says the dates are on the app's calendar only",
            "on the app's calendar only" in page)
    oc.post("/admin/house-calendar/areas", data={"areas": ["rooms", "dates"]})
    s.check("ticked again, it is carried", "dates" in m.house_calendar_areas(conn),
            detail=str(m.house_calendar_areas(conn)))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
