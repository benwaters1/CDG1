# -*- coding: utf-8 -*-
"""Booking.com's reservations statement, uploaded once a week.

The owner, 9 November 2026: Booking.com's emails say "you have a booking" and
nothing else -- no name, no price -- and a channel manager to be told the
rest is something else to pay for and something else to use. The extranet's
Reservations page downloads every stay as a spreadsheet: name, dates, rooms,
party, price and commission. One upload a week brings it in.

WHAT IS HELD HERE

  THE FILE AS BOOKING.COM GIVES IT. A real Excel 97 .xls (checked on the
  house's own download), read with xlrd; the same columns as .csv too.
  Columns are found by name. A file that is not the statement is refused
  with a sentence, never a crash. The fixture is made up, with the real
  file's 26 columns: no guest of the house is in this repository.

  NEVER OVERRULING THE HOUSE QUIETLY. Money is Booking.com's to state and is
  always taken. A name, a head count, a country fill a gap only. A stay the
  statement lists as cancelled is cancelled here, and named. Dates that
  differ are reported, not changed: the room's calendar pairs a stay with
  its nights. A cancelled stay's booked price is not money unless Booking.com
  charged for it. Nothing older than the house keeps a stay comes back.

  WHAT IT IS FOR. The page about what each channel costs now has Booking.com
  on it, with its own figures, and says when stays are waiting for a
  statement rather than calling them free. And a self-closing task asks for
  the statement when the last one is a week old.
"""
import csv
import io
import os
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "ZZBCS"
FIXTURE = os.path.join(_harness.ROOT, "tests", "fixtures", "booking_com_statement.xls")
HEAD = ["Book number", "Booked by", "Guest name(s)", "Check-in", "Check-out", "Booked on",
        "Status", "Rooms", "Persons", "Adults", "Children", "Children's age(s)", "Price",
        "Commission %", "Commission amount", "Payment status",
        "Payment method (Payment Provider)", "Remarks", "Booker country", "Travel purpose",
        "Device", "Unit type", "Duration (nights)", "Cancellation date", "Address",
        "Phone number"]


def _now():
    return datetime.now(timezone.utc)


def _cleanup(conn, before_statement):
    conn.execute("DELETE FROM ota_mail WHERE source_id LIKE ?", (f"<{TAG}%",))
    ours = "(SELECT id FROM ota_reservations WHERE reservation_number LIKE '9744%')"
    conn.execute(f"DELETE FROM ota_reservation_rooms WHERE ota_reservation_id IN {ours}")
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '9744%'")
    conn.execute("DELETE FROM booking_com_statements WHERE id > ?", (before_statement,))
    conn.execute("DELETE FROM ical_sources WHERE url LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title = ?",
                 (m.WATCH_TASK_ORIGIN, "Upload Booking.com's reservations statement"))
    conn.commit()


def _csv(rows, delimiter=","):
    out = io.StringIO()
    w = csv.writer(out, delimiter=delimiter)
    w.writerow(HEAD)
    for r in rows:
        w.writerow(r)
    return out.getvalue().encode("utf-8")


def _row(number, name, arrive, leave, status="ok", price="400 EUR", commission="60 EUR",
         unit="", rooms=1, persons=2, adults=2, children=0, country="gb"):
    return [number, name, name, arrive, leave, "2026-01-02 10:00:00", status, rooms, persons,
            adults, children, "", price, "15", commission, "Payment via Booking.com", "", "",
            country, "", "Desktop", unit, 1, "", "", ""]


def _stay(conn, number):
    return conn.execute("SELECT * FROM ota_reservations WHERE reservation_number = ?",
                        (number,)).fetchone()


def run():
    s = Suite("Booking.com's reservations statement, uploaded once a week")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM booking_com_statements").fetchone()[0]
    _cleanup(conn, before)
    try:
        _run(s, oc, conn)
    finally:
        _cleanup(conn, before)
        conn.close()
    return s


def _run(s, oc, conn):
    today = m.house_today()

    s.section("The file as Booking.com gives it")
    rows, problem = m.read_booking_com_statement(open(FIXTURE, "rb").read())
    s.check("the .xls the extranet downloads is read", problem is None and len(rows) == 3,
            detail=str(problem))
    one = rows[0] if rows else {}
    s.check("its booking number as Booking.com writes it, not as a spreadsheet number",
            one.get("number") == "9744400001", detail=str(one.get("number")))
    s.check("the price and the commission, in euros",
            one.get("price") == 960.0 and one.get("commission_amount") == 144.0
            and one.get("commission_pct") == 15.0 and one.get("currency") == "EUR", detail=str(one))
    s.check("the dates, the party and the booker's country",
            one.get("arrival") == "2031-07-16" and one.get("departure") == "2031-07-19"
            and one.get("adults") == 2 and one.get("children") == 0 and one.get("country") == "fi")
    s.check("and a stay for two rooms says so",
            len(rows) == 3 and rows[2]["rooms"] == 2 and "King Suite" in rows[2]["unit_type"])
    got, problem = m.read_booking_com_statement(_csv([_row("9744400010", "Csv Guest",
                                                            "2031-05-01", "2031-05-03")], ";"))
    s.check("the same columns as a .csv, semicolons and all, read the same",
            problem is None and got and got[0]["number"] == "9744400010" and got[0]["price"] == 400.0,
            detail=str(problem))
    _rows, problem = m.read_booking_com_statement(b"Name,Email\nA,b@example.invalid\n")
    s.check("a file that is not the statement is refused with a reason",
            _rows == [] and problem and "not Booking.com's reservations statement" in problem,
            detail=str(problem))
    _rows, problem = m.read_booking_com_statement(m._XLS_MAGIC + b"\x00" * 600)
    s.check("and a damaged spreadsheet is refused, not a crash",
            _rows == [] and problem and "could not be read" in problem, detail=str(problem))

    s.section("Brought into line, never overruled quietly")
    room = conn.execute("SELECT id, name, channel_name FROM rooms WHERE active = 1 "
                        "ORDER BY sort_order, id LIMIT 1").fetchone()
    unit = room["channel_name"] or room["name"]
    far = today + timedelta(days=700)
    a, d = far.isoformat(), (far + timedelta(days=2)).isoformat()
    # A stay the house already knows from Booking.com's email: number and day only.
    m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}1@booking.com>", received_at=_now().isoformat(),
        from_address="noreply@booking.com", from_name="Booking.com",
        subject=f"Booking.com - New booking! (9744400020, {far.strftime('%A, %d %B %Y')})",
        notify=False)
    # One whose name somebody typed by hand.
    m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}2@booking.com>", received_at=_now().isoformat(),
        from_address="noreply@booking.com", from_name="Booking.com",
        subject=f"Booking.com - New booking! (9744400021, {far.strftime('%A, %d %B %Y')})",
        notify=False)
    conn.execute("UPDATE ota_reservations SET guest_name = 'Typed Here' WHERE reservation_number = '9744400021'")
    # One the statement will cancel, and one whose dates it disagrees with.
    for n, number in ((3, "9744400022"), (4, "9744400023")):
        m.ingest_booking_com_email(
            conn, source="graph", source_id=f"<{TAG}{n}@booking.com>", received_at=_now().isoformat(),
            from_address="noreply@booking.com", from_name="Booking.com",
            subject=f"Booking.com - New booking! ({number}, {far.strftime('%A, %d %B %Y')})",
            notify=False)
    conn.commit()
    statement = [
        _row("9744400020", "Known Guest", a, d, unit=unit, country="fi", persons=3, adults=2, children=1),
        _row("9744400021", "Statement Name", a, d),
        _row("9744400022", "Cancelled Guest", a, d, status="cancelled_by_guest", commission=""),
        _row("9744400023", "Moved Guest", (far + timedelta(days=5)).isoformat(),
             (far + timedelta(days=7)).isoformat()),
        _row("9744400024", "New Guest", a, d, unit=unit, price="1,215.50 EUR", commission="182.33 EUR"),
        _row("9744400025", "Gone Guest", a, d, status="cancelled_by_guest", commission=""),
        _row("9744400026", "Old Guest", (today - timedelta(days=900)).isoformat(),
             (today - timedelta(days=898)).isoformat()),
        _row("", "No Number", a, d),
    ]
    rows, problem = m.read_booking_com_statement(_csv(statement))
    with m.app.test_request_context("/"):
        report = m.apply_booking_com_statement(conn, rows, filename="test.csv")
    known = _stay(conn, "9744400020")
    s.check("a stay known from the email takes Booking.com's price and commission",
            known["amount"] == 400.0 and known["commission_amount"] == 60.0
            and known["commission_pct"] == 15.0 and known["statement_at"], detail=str(dict(known)))
    s.check("and its gaps: the name, the party, the country",
            known["guest_name"] == "Known Guest" and known["guests"] == 3 and known["adults"] == 2
            and known["children"] == 1 and known["guest_country"] == "Finland", detail=str(dict(known)))
    s.check("a name somebody typed is kept",
            _stay(conn, "9744400021")["guest_name"] == "Typed Here")
    cancelled = _stay(conn, "9744400022")
    s.check("a stay the statement lists as cancelled is cancelled here, and named",
            cancelled["status"] == "cancelled" and any("9744400022" in x for x in report["cancelled"]),
            detail=str(report["cancelled"]))
    s.check("and its booked price is not taken as money",
            cancelled["amount"] is None, detail=str(cancelled["amount"]))
    moved = _stay(conn, "9744400023")
    s.check("dates that differ are reported, and not changed",
            moved["arrival_date"] == a
            and any("9744400023" in label and "dates are" in why for label, why in report["differs"]),
            detail=str(report["differs"]))
    new = _stay(conn, "9744400024")
    s.check("a stay the house had never heard of is added, with its room when it names one",
            new and new["status"] == "confirmed" and new["room_id"] == room["id"]
            and new["amount"] == 1215.5 and new["commission_amount"] == 182.33,
            detail=str(new and dict(new)))
    gone = _stay(conn, "9744400025")
    s.check("a new one already cancelled is kept as cancelled, with no money",
            gone and gone["status"] == "cancelled" and gone["amount"] is None)
    s.check("nothing older than the house keeps a stay comes back",
            _stay(conn, "9744400026") is None
            and any("9744400026" in label for label, _w in report["skipped"]))
    s.check("and a row with no number is skipped, by name",
            any("No Number" in label for label, _w in report["skipped"]))
    count = conn.execute("SELECT COUNT(*) FROM ota_reservations WHERE reservation_number LIKE '9744%'"
                         ).fetchone()[0]
    with m.app.test_request_context("/"):
        again = m.apply_booking_com_statement(conn, rows, filename="test.csv")
    s.check("the same statement uploaded twice adds nothing",
            not again["new"] and conn.execute(
                "SELECT COUNT(*) FROM ota_reservations WHERE reservation_number LIKE '9744%'"
            ).fetchone()[0] == count)

    s.section("What a Booking.com stay costs, on the page about what channels cost")
    start, end = far - timedelta(days=1), far + timedelta(days=30)
    with m.app.test_request_context("/"):
        data = m.channel_cost(conn, start, end)
    row = next((r for r in data["rows"] if r.get("from_statement")), None)
    s.check("Booking.com is on it, with the statement's own figures",
            row and abs(row["revenue"] - (400 + 400 + 1215.5 + 400)) < 0.01
            and abs(row["cost"] - (60 + 60 + 182.33 + 60)) < 0.01,
            detail=str(row))
    s.check("cancelled stays it did not charge for are not in it",
            row and row["bookings"] == 4, detail=str(row and row["bookings"]))
    m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}5@booking.com>", received_at=_now().isoformat(),
        from_address="noreply@booking.com", from_name="Booking.com",
        subject=f"Booking.com - New booking! (9744400030, {(far + timedelta(days=3)).strftime('%A, %d %B %Y')})",
        notify=False)
    conn.commit()
    with m.app.test_request_context("/"):
        data = m.channel_cost(conn, start, end)
    row = next((r for r in data["rows"] if r.get("from_statement")), None)
    s.check("a stay no statement has covered makes the commission 'not known', not free",
            data["statement_waiting"] == 1 and row["cost"] is None and data["net"] is None
            and not data["every_rate"], detail=str(row))
    page = visible_text(oc.get(f"/management/channels?from={start.isoformat()}&to={end.isoformat()}")
                        .get_data(as_text=True))
    s.check("and the page says so, and where to fix it",
            "without Booking.com's figures yet" in page and "Booking.com (sold there)" in page)

    s.section("The weekly upload, and its reminder")
    # The copy's own Booking.com stays that no statement has covered count as
    # covered here, as they do in the next section, and are put back with
    # them. The clock is pinned to the morning of PINNED_DAY, so a stay the
    # house first saw after that day is "new since the upload" whatever is
    # uploaded: the fixture does not list it, and the upload is stamped
    # before it was seen. With the real clock the house cannot have seen a
    # stay after now, so this is the pin's doing, not the reminder's.
    real_waiting = [r["id"] for r in conn.execute(
        "SELECT id FROM ota_reservations WHERE statement_at IS NULL "
        "AND reservation_number NOT LIKE '9744%'")]
    if real_waiting:
        conn.execute(f"UPDATE ota_reservations SET statement_at = ? WHERE id IN "
                     f"({','.join('?' * len(real_waiting))})", [_now().isoformat()] + real_waiting)
    cur = conn.execute("INSERT INTO ical_sources (room_id, label, url, last_synced_at) "
                       "VALUES (?, 'Booking.com', ?, ?)",
                       (room["id"], f"https://ical.booking.com/v1/export?t={TAG}", _now().isoformat()))
    conn.execute("UPDATE booking_com_statements SET uploaded_at = ?",
                 ((_now() - timedelta(days=9)).isoformat(),))
    conn.commit()
    due = m.booking_com_statement_due(conn)
    s.check("a week after the last statement, it is asked for", due and "Upload" in due[0], detail=str(due))
    m.generate_watch_tasks(conn, today)
    task = conn.execute("SELECT * FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                        (m.WATCH_TASK_ORIGIN, "Upload Booking.com's reservations statement")).fetchone()
    s.check("as a task", bool(task))
    with m.app.test_request_context("/"):
        home = m.owner_home_warnings(conn, today)
    s.check("and on the owner home", any("reservations statement" in w["title"] for w in home))
    with open(FIXTURE, "rb") as fh:
        r = oc.post("/admin/channel-stays/statement",
                    data={"statement": (fh, "Check-in 2031.xls")}, content_type="multipart/form-data")
    said = " ".join(flashes(oc.get("/admin/channel-stays"))) if r.status_code == 302 else ""
    s.check("the upload reads the file and says what it did, naming nothing it skipped",
            r.status_code == 302 and "Read 3 reservations" in said, detail=said)
    s.check("after which nothing is asked", m.booking_com_statement_due(conn) is None)
    m.generate_watch_tasks(conn, today)
    s.check("and the task has closed itself",
            conn.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()["status"] == "done"
            if task else False)
    page = visible_text(oc.get("/admin/channel-stays").get_data(as_text=True))
    s.check("the stays page says when it was last uploaded, and the commission beside the price",
            "last uploaded" in page and "commission 144.00" in page)
    r = oc.post("/admin/channel-stays/statement",
                data={"statement": (io.BytesIO(b"Name,Email\n"), "wrong.csv")},
                content_type="multipart/form-data")
    said = " ".join(flashes(oc.get("/admin/channel-stays")))
    s.check("a wrong file is refused with the reason", "could not be used" in said, detail=said)
    conn.execute("DELETE FROM ical_sources WHERE id = ?", (cur.lastrowid,))
    conn.commit()

    s.section("On the calendar ahead, and at once for a new booking")
    # Every stay the statement has not covered counts as covered for now, so
    # only what this section adds is "new" -- on a copy of a live house there
    # are real ones. Put back at the end.
    uncovered = [r["id"] for r in conn.execute(
        "SELECT id FROM ota_reservations WHERE statement_at IS NULL")]
    conn.execute("UPDATE ota_reservations SET statement_at = ? WHERE statement_at IS NULL",
                 (_now().isoformat(),))
    cur = conn.execute("INSERT INTO ical_sources (room_id, label, url, last_synced_at) "
                       "VALUES (?, 'Booking.com', ?, ?)",
                       (room["id"], f"https://ical.booking.com/v1/export?t={TAG}2", _now().isoformat()))
    six_days = _now() - timedelta(days=6)
    conn.execute("UPDATE booking_com_statements SET uploaded_at = ?", (six_days.isoformat(),))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title = ?",
                 (m.WATCH_TASK_ORIGIN, m.BOOKING_COM_STATEMENT_TASK))
    conn.commit()
    due = m.booking_com_statement_due(conn)
    week_up = m.house_date(six_days + timedelta(days=7)).isoformat()
    s.check("two days before the week is up, it is raised, dated the day it is due",
            due and due[2] == week_up and due[3] == "normal", detail=str(due))
    with m.app.test_request_context("/"):
        home = m.owner_home_warnings(conn, today)
    s.check("not yet on the owner home, which is for what is due now",
            not any(w["title"] == m.BOOKING_COM_STATEMENT_TASK for w in home))
    m.generate_watch_tasks(conn, today)
    task = conn.execute("SELECT * FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                        (m.WATCH_TASK_ORIGIN, m.BOOKING_COM_STATEMENT_TASK)).fetchone()
    s.check("its task is due that day", task and task["due_date"] == week_up,
            detail=str(task and dict(task)))
    week_page = visible_text(oc.get(f"/calendar?view=week&date={week_up}").get_data(as_text=True))
    s.check("and is on the calendar", m.BOOKING_COM_STATEMENT_TASK in week_page)
    conn.execute("UPDATE booking_com_statements SET uploaded_at = ?",
                 ((_now() - timedelta(days=2)).isoformat(),))
    conn.commit()
    s.check("two days after an upload, nothing is asked", m.booking_com_statement_due(conn) is None)

    soon = today + timedelta(days=2)
    m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}6@booking.com>", received_at=_now().isoformat(),
        from_address="noreply@booking.com", from_name="Booking.com",
        subject=f"Booking.com - New booking! (9744400040, {soon.strftime('%A, %d %B %Y')})",
        notify=False)
    conn.commit()
    s.check("the mail job raises it as a booking comes in, not at the next daily pass",
            "refresh_booking_com_statement_task(conn)" in
            "\n".join(l.split("#")[0] for l in
                      __import__("inspect").getsource(m.after_booking_com_mail).splitlines()))
    m.refresh_booking_com_statement_task(conn)
    task = conn.execute("SELECT * FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                        (m.WATCH_TASK_ORIGIN, m.BOOKING_COM_STATEMENT_TASK)).fetchone()
    s.check("a booking since the last upload makes it due today, naming the booking",
            task and task["due_date"] == today.isoformat() and "9744400040" in (task["notes"] or ""),
            detail=str(task and dict(task)))
    s.check("and high priority when the guest arrives within three days",
            task and task["priority"] == "high", detail=str(task and task["priority"]))
    with m.app.test_request_context("/"):
        home = m.owner_home_warnings(conn, today)
    s.check("and it is on the owner home now",
            any(w["title"] == m.BOOKING_COM_STATEMENT_TASK for w in home))
    conn.execute("UPDATE ota_reservations SET arrival_date = ?, departure_date = ? "
                 "WHERE reservation_number = '9744400040'",
                 ((today + timedelta(days=40)).isoformat(), (today + timedelta(days=42)).isoformat()))
    conn.commit()
    m.refresh_booking_com_statement_task(conn)
    task = conn.execute("SELECT * FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                        (m.WATCH_TASK_ORIGIN, m.BOOKING_COM_STATEMENT_TASK)).fetchone()
    s.check("one arriving in weeks is due today, at normal priority",
            task and task["due_date"] == today.isoformat() and task["priority"] == "normal",
            detail=str(task and dict(task)))
    covering = _csv([_row("9744400040", "Soon Guest", (today + timedelta(days=40)).isoformat(),
                          (today + timedelta(days=42)).isoformat())])
    oc.post("/admin/channel-stays/statement",
            data={"statement": (io.BytesIO(covering), "this-week.csv")},
            content_type="multipart/form-data")
    closed = conn.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone() if task else None
    s.check("the upload closes it there and then",
            closed and closed["status"] == "done", detail=str(closed and dict(closed)))
    uncovered += real_waiting
    if uncovered:
        conn.execute(f"UPDATE ota_reservations SET statement_at = NULL WHERE id IN "
                     f"({','.join('?' * len(uncovered))})", uncovered)
    conn.execute("DELETE FROM ical_sources WHERE id = ?", (cur.lastrowid,))
    conn.commit()

    s.section("Cancelled after the statement: not money any more")
    # Chiodi's, 12 November 2026: in the statement while it stood, then
    # cancelled by email, and still counted at EUR 5,530.
    late = today + timedelta(days=500)
    la, ld = late.isoformat(), (late + timedelta(days=7)).isoformat()
    rows, _p = m.read_booking_com_statement(_csv([_row("9744400050", "Late Cancel", la, ld,
                                                       price="5530 EUR", commission="829.50 EUR")]))
    with m.app.test_request_context("/"):
        m.apply_booking_com_statement(conn, rows, filename="before.csv")
    window = (late - timedelta(days=1), late + timedelta(days=30))
    with m.app.test_request_context("/"):
        before = next((r for r in m.channel_cost(conn, *window)["rows"] if r.get("from_statement")), None)
    s.check("while it stands it is counted (a precondition)",
            before and abs(before["revenue"] - 5530) < 0.01, detail=str(before))
    m.ingest_booking_com_email(
        conn, source="graph", source_id=f"<{TAG}7@booking.com>",
        received_at=(_now() + timedelta(minutes=1)).isoformat(),
        from_address="noreply@booking.com", from_name="Booking.com",
        subject=f"Booking.com - Cancelled booking! (9744400050, {late.strftime('%A, %d %B %Y')})",
        notify=False)
    conn.commit()
    s.check("the cancellation email cancels it (a precondition)",
            _stay(conn, "9744400050")["status"] == "cancelled")
    with m.app.test_request_context("/"):
        after = next((r for r in m.channel_cost(conn, *window)["rows"] if r.get("from_statement")), None)
    s.check("and from then it is not counted as money, though the statement's figures are still on it",
            after is None or abs(after["revenue"]) < 0.01, detail=str(after))
    rows, _p = m.read_booking_com_statement(_csv([_row("9744400050", "Late Cancel", la, ld,
                                                       status="cancelled_by_guest",
                                                       price="5530 EUR", commission="")]))
    with m.app.test_request_context("/"):
        m.apply_booking_com_statement(conn, rows, filename="after.csv")
    gone = _stay(conn, "9744400050")
    s.check("and the next statement, showing it uncharged, takes the figures off it",
            gone["amount"] is None and gone["commission_amount"] is None, detail=str(dict(gone)))

    s.section("The privacy notice says it")
    notice = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("that the house takes Booking.com's list of stays, and what it keeps from it",
            "About once a week we also take from Booking.com its list of the stays" in notice
            and "what Booking.com charges us for your stay" in notice)
