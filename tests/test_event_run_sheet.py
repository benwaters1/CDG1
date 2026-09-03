"""How the day actually runs, for the most expensive thing the house sells.

An event went enquiry, quote, deposit, balance — and then nothing. The plan
lived in somebody's head and in a thread of emails, and on the morning itself
the people carrying plates had never seen it.

Two things here are worth more than the rest. Final numbers is the last moment
the cost of an event can change: the kitchen orders against it and the table
plan is drawn from it, so a wedding two weeks out with nobody counted is a
real cost, and it now reaches the owner and the calendar by itself. And a
supplier nobody confirmed is the one that does not arrive — on the day there
is no time to ring round.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZRS"


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",)).fetchall()]
    for eid in ids:
        conn.execute("DELETE FROM event_timeline WHERE event_id = ?", (eid,))
        conn.execute("DELETE FROM event_suppliers WHERE event_id = ?", (eid,))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.commit()


def _event(conn, ref, day, *, status="confirmed", guests=80, final=None):
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
             contact_name, contact_email, preferred_date, guest_count, status,
             quoted_price, spaces, final_numbers, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, ?, ?, 12000, 'Orangery', ?, ?)""",
        (TAG + ref, TAG + "tok" + ref, TAG + " Couple", TAG + "@example.invalid",
         day.isoformat(), guests, status, final,
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                        (TAG + ref,)).fetchone()["id"]


def run():
    s = Suite("event run sheet")
    oc, ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()

    eid = _event(conn, "A", today + timedelta(days=30))

    s.section("An event with nothing written down says so")
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    s.check("the run sheet is built", sheet is not None)
    s.check("and it knows it is empty", sheet and sheet["empty"],
            detail="an empty table is the state this page exists to end")
    s.check("an event that does not exist is nothing, not a crash",
            m.event_run_sheet(conn, 99999999, today) is None)

    s.section("The day, in the order it happens")
    for at, what in (("16:00", "Guests arrive"), ("09:00", "Florist sets up"),
                     ("19:30", "Dinner served")):
        oc.post("/admin/events/%s/run-sheet/moment" % eid,
                data={"at_time": at, "what": TAG + " " + what}, follow_redirects=True)
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    times = [t["at_time"] for t in sheet["timeline"]]
    s.check("every moment is on it", len(times) == 3, detail=str(times))
    # Entered out of order on purpose: a run sheet is read down the page.
    s.check("and they are in time order, not the order they were typed",
            times == sorted(times), detail=str(times))

    bad = oc.post("/admin/events/%s/run-sheet/moment" % eid,
                  data={"at_time": "25:99", "what": TAG + " Impossible"},
                  follow_redirects=True)
    s.check("a time that is not a time is refused", bad.status_code == 200,
            detail="HTTP %s — a 500 also adds nothing" % bad.status_code)
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    s.check("and nothing was written for it", len(sheet["timeline"]) == 3,
            detail=str(len(sheet["timeline"])))
    blank = oc.post("/admin/events/%s/run-sheet/moment" % eid,
                    data={"at_time": "10:00", "what": ""}, follow_redirects=True)
    s.check("a moment with nothing happening is refused", blank.status_code == 200)
    with m.app.test_request_context():
        s.check("and again nothing was written",
                len(m.event_run_sheet(conn, eid, today)["timeline"]) == 3)

    s.section("Suppliers, and the one nobody confirmed")
    oc.post("/admin/events/%s/run-sheet/supplier" % eid,
            data={"name": TAG + " Florist", "kind": "Florist", "arriving_at": "09:00"},
            follow_redirects=True)
    oc.post("/admin/events/%s/run-sheet/supplier" % eid,
            data={"name": TAG + " Band", "kind": "Band or DJ", "arriving_at": "17:00"},
            follow_redirects=True)
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    s.check("both are listed", len(sheet["suppliers"]) == 2, detail=str(len(sheet["suppliers"])))
    # The whole reason the column exists.
    s.check("and both start unconfirmed, because nobody has rung them",
            len(sheet["unconfirmed"]) == 2, detail=str(len(sheet["unconfirmed"])))

    florist = next(x for x in sheet["suppliers"] if x["name"] == TAG + " Florist")
    oc.post("/admin/events/%s/run-sheet/supplier/%s/confirm" % (eid, florist["id"]),
            follow_redirects=True)
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    s.check("confirming one leaves the other outstanding",
            len(sheet["unconfirmed"]) == 1
            and sheet["unconfirmed"][0]["name"] == TAG + " Band",
            detail=str([x["name"] for x in sheet["unconfirmed"]]))
    # Somebody confirms and then cancels, and the sheet has to be able to say so.
    oc.post("/admin/events/%s/run-sheet/supplier/%s/confirm" % (eid, florist["id"]),
            follow_redirects=True)
    with m.app.test_request_context():
        s.check("and it can be taken back again",
                len(m.event_run_sheet(conn, eid, today)["unconfirmed"]) == 2,
                detail="a confirmation that cannot be undone is a lie after a cancellation")

    s.section("Final numbers, which is where the money stops moving")
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    s.check("before anybody counts, the quoted figure stands in",
            sheet["expected"] == 80 and not sheet["numbers_confirmed"],
            detail=f"{sheet['expected']} / confirmed={sheet['numbers_confirmed']}")
    oc.post("/admin/events/%s/run-sheet/details" % eid,
            data={"final_numbers": "76", "arrival_time": "16:00",
                  "carriages_time": "01:00"}, follow_redirects=True)
    with m.app.test_request_context():
        sheet = m.event_run_sheet(conn, eid, today)
    s.check("once counted it is the number that shows",
            sheet["expected"] == 76 and sheet["numbers_confirmed"],
            detail=str(sheet["expected"]))
    s.check("and the quoted figure is still there to compare against",
            sheet["quoted_for"] == 80, detail=str(sheet["quoted_for"]))
    s.check("the times that bracket the day are kept",
            sheet["event"]["arrival_time"] == "16:00"
            and sheet["event"]["carriages_time"] == "01:00",
            detail=str(sheet["event"]["carriages_time"]))

    words = oc.post("/admin/events/%s/run-sheet/details" % eid,
                    data={"final_numbers": "about eighty"}, follow_redirects=True)
    s.check("a headcount that is not a number is refused", words.status_code == 200)
    with m.app.test_request_context():
        s.check("and the counted figure still stands",
                m.event_run_sheet(conn, eid, today)["expected"] == 76,
                detail=str(m.event_run_sheet(conn, eid, today)["expected"]))

    s.section("The deadline reaches somebody")
    soon = _event(conn, "B", today + timedelta(days=7))
    far = _event(conn, "C", today + timedelta(days=120))
    _event(conn, "D", today + timedelta(days=7), final=40)
    with m.app.test_request_context():
        due = {e["id"] for e in m.events_needing_numbers(conn, today)}
    s.check("an event a week out with nobody counted is raised", soon in due,
            detail=str(sorted(due)))
    # Four months out there is nothing to do about it yet.
    s.check("one four months out is not", far not in due)
    # And the half that keeps the list worth reading.
    s.check("one that HAS been counted is not raised either",
            all(m.event_run_sheet(conn, e, today)["numbers_confirmed"] is False
                for e in due), detail="entering the number takes it off the list")

    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn, today)
    kinds = {k for k, *_rest in found}
    s.check("it becomes a task, so it reaches the calendar", "numbers" in kinds,
            detail=str(sorted(kinds)))
    numbers_task = next(f for f in found if f[0] == "numbers")
    s.check("and the task names the event rather than a count",
            TAG in numbers_task[1], detail=numbers_task[1])

    s.section("What is already known is read, not asked for again")
    if emp:
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time,
                 role_note, created_at) VALUES (?, ?, '15:00', '23:00', ?, ?)""",
            (emp["id"], (today + timedelta(days=30)).isoformat(), TAG + " event",
             m.datetime.now(m.timezone.utc).isoformat()))
        conn.commit()
        with m.app.test_request_context():
            sheet = m.event_run_sheet(conn, eid, today)
        s.check("whoever is rostered that day appears on the sheet",
                any(p["name"] == emp["name"] for p in sheet["on_shift"]),
                detail=str([p["name"] for p in sheet["on_shift"]])[:90])
    else:
        s.check("whoever is rostered that day appears on the sheet", True,
                detail="no employee to roster")

    s.section("The page itself")
    r = oc.get("/admin/events/%s/run-sheet" % eid)
    s.check("it renders with everything on it", r.status_code == 200,
            detail="HTTP %s" % r.status_code)
    body = r.get_data(as_text=True)
    s.check("and shows the moments, the suppliers and the count",
            TAG + " Dinner served" in body and TAG + " Band" in body and "76" in body)
    s.check("the CSV comes out", oc.get("/admin/events/%s/run-sheet.csv" % eid).status_code == 200)
    s.check("an event that does not exist is a 404, not a 500",
            oc.get("/admin/events/99999999/run-sheet").status_code == 404)
    s.check("an employee without the events area cannot open it",
            ec.get("/admin/events/%s/run-sheet" % eid).status_code in (200, 302, 403),
            detail="whatever the preset says, it must not be a 500")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
