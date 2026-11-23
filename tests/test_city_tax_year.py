# -*- coding: utf-8 -*-
"""The taxe de séjour by the council's year, totalled, and a reminder to declare it.

The owner, 23 November 2026: "make it total it up for me so i know what to
declare". The Haute-Ariège council's year runs 1 October to 30 September and
is declared once, by 15 October, on its own site. The page showed a week, a
month or a calendar year, none of which is the year declared.

WHAT IS HELD HERE

  THE COUNCIL'S YEAR. 30 September is the old year, 1 October the new one. A
  stay across the boundary owes each year its own nights.

  WHAT IS DECLARED IS NIGHTS AND PEOPLE. The figure is the chargeable
  adult-nights at the rate on the rates page, whatever the bills charged.
  Booking.com's guests are in it, and a stay with no statement yet says so.

  ONCE THE YEAR IS OVER. A warning on the owner home and a task due on
  15 October; after that day a blocker and a task marked high. Both go when
  the owner presses Declared, which keeps the figures shown at the time and
  can be taken back. A year not over cannot be declared; a year nobody stayed
  in asks for nothing.
"""
import csv
import io
from datetime import date, datetime, timezone

from _harness import Suite, clients, db, ensure_room, visible_text
import _harness

m = _harness.m
TAG = "ZZCTY"
NUM = "97466"
YEAR = 2031                      # the council year 1 Oct 2031 to 30 Sep 2032
LABEL = "1 October 2031 to 30 September 2032"
TASK = f"Declare the taxe de séjour for {LABEL}"


def _label(year_from):
    return f"1 October {year_from} to 30 September {year_from + 1}"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute(f"DELETE FROM ota_reservations WHERE reservation_number LIKE '{NUM}%'")
    conn.execute("DELETE FROM city_tax_declarations WHERE year_from IN (?, ?)", (YEAR, YEAR + 9))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title = ?", (m.WATCH_TASK_ORIGIN, TASK))
    conn.commit()


def _booking(conn, room_id, ref, arrive, leave, party, under18, tax=0.0):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, guest_phone, arrival_date, departure_date, party_size,
               guests_under_18, status, total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'zzcty@example.invalid', '', ?, ?, ?, ?, 'confirmed', 500, 500, ?, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", arrive, leave,
         party, under18, tax, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _ota(conn, number, room_id, arrive, leave, adults, children):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
               arrival_date, departure_date, room_id, guests, amount, currency, adults,
               children, statement_at, first_seen_at, last_event_at, updated_at)
           VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, ?, ?, 600, 'EUR', ?, ?, ?, ?, ?, ?)""",
        (number, f"{TAG} {number[-3:]}", arrive, leave, room_id, adults + children, adults,
         children, now, now, now, now))
    conn.commit()


def _home(conn, today, label=LABEL):
    with m.app.test_request_context("/"):
        return [w for w in m.owner_home_warnings(conn, today) if label in w["title"]]


def _task(conn, title=TASK):
    return conn.execute("SELECT * FROM tasks WHERE origin = ? AND title = ? AND status != 'done'",
                        (m.WATCH_TASK_ORIGIN, title)).fetchone()


def run():
    s = Suite("The taxe de séjour by the council's year, and a reminder to declare it")
    oc, ec, _owner, _emp = clients()
    conn = db()
    # The year that has really ended, which the Declared button works on.
    past = m.city_tax_year_of(m.house_today()) - 1
    put_back = _harness.keep_rows(conn, "city_tax_declarations", "year_from = ?", (past,))
    _cleanup(conn)
    try:
        conn.execute("DELETE FROM city_tax_declarations WHERE year_from = ?", (past,))
        conn.commit()
        _run(s, oc, ec, conn, past)
    finally:
        _cleanup(conn)
        conn.execute("DELETE FROM tasks WHERE origin = ? AND title = ?",
                     (m.WATCH_TASK_ORIGIN, f"Declare the taxe de séjour for {_label(past)}"))
        conn.commit()
        put_back()
        conn.close()
    return s


def _run(s, oc, ec, conn, past):
    rid = ensure_room()["id"]
    rate = m.tax_rate(conn, "city_tax_per_adult_per_night")

    s.section("The council's year")
    s.check("30 September is the old year", m.city_tax_year_of(date(2032, 9, 30)) == 2031)
    s.check("1 October the new one", m.city_tax_year_of(date(2032, 10, 1)) == 2032)

    before = m.city_tax_declaration(conn, YEAR, today=date(2032, 11, 1))
    # Two adults and a child, three nights in the year (28-30 September 2032)
    # and two in the next: the year owes its own nights only.
    _booking(conn, rid, "EDGE", "2032-09-28", "2032-10-03", party=3, under18=1, tax=7.50)
    _booking(conn, rid, "JAN", "2032-01-10", "2032-01-12", party=2, under18=0, tax=3.20)
    _ota(conn, f"{NUM}00001", rid, "2032-02-01", "2032-02-04", adults=2, children=0)
    year = m.city_tax_declaration(conn, YEAR, today=date(2032, 11, 1))
    added = year["adult_nights"] - before["adult_nights"]
    s.check("a stay across 1 October gives the year only its own nights",
            added == 2 * 3 + 2 * 2 + 2 * 3, detail=f"{added} adult-nights added")
    s.check("and the under-18 nights apart",
            year["exempt_nights"] - before["exempt_nights"] == 3)
    s.check("Booking.com's guests are in it", year["booking_com"]["stays"]
            - before["booking_com"]["stays"] == 1)
    s.check("the figure is the adult-nights at the rate, not what the bills charged",
            abs(year["to_declare"] - round(year["adult_nights"] * rate, 2)) < 0.01
            and abs((year["to_declare"] - before["to_declare"]) - round(16 * rate, 2)) < 0.02,
            detail=f"{year['to_declare']} at {rate}")
    nxt = m.city_tax_declaration(conn, YEAR + 1, today=date(2032, 11, 1))
    s.check("the next year has the other two nights", nxt["adult_nights"] >= 4 and not nxt["over"])

    s.section("Once the year is over: a warning, then a blocker")
    early = date(2032, 10, 5)
    owed = m.city_tax_to_declare(conn, early)
    s.check("on 5 October it is the year to declare, not late yet",
            owed is not None and owed["year_from"] == YEAR and not owed["late"])
    home = _home(conn, early)
    s.check("a warning on the owner home, with the figure",
            len(home) == 1 and home[0]["severity"] == "warn"
            and m.money(owed["to_declare"]) in home[0]["title"], detail=str(home))
    m.generate_watch_tasks(conn, early)
    task = _task(conn)
    s.check("and a task due on 15 October",
            task is not None and task["due_date"] == "2032-10-15" and task["priority"] == "normal"
            and "adult-night" in task["notes"] and "taxe.3douest.com" in task["notes"],
            detail=str(task and dict(task)))
    late = date(2032, 11, 1)
    home = _home(conn, late)
    s.check("after 15 October it is a blocker", len(home) == 1 and home[0]["severity"] == "blocker")
    m.generate_watch_tasks(conn, late)
    high = _task(conn)
    s.check("and the task is high", high is not None and high["priority"] == "high")
    s.check("before the year is over there is nothing to declare yet",
            m.city_tax_to_declare(conn, date(2032, 9, 15)) is None
            or m.city_tax_to_declare(conn, date(2032, 9, 15))["year_from"] != YEAR)

    s.section("Declared")
    r = oc.post(f"/admin/city-tax/declared/{YEAR + 1}", data={}, follow_redirects=True)
    s.check("a year not over cannot be declared",
            "is not over yet" in " ".join(_harness.flashes(r))
            and not conn.execute("SELECT 1 FROM city_tax_declarations WHERE year_from = ?",
                                 (YEAR + 1,)).fetchone())
    s.check("staff cannot mark it", ec.post(f"/admin/city-tax/declared/{YEAR}",
                                            data={}).status_code in (302, 303, 403)
            and not conn.execute("SELECT 1 FROM city_tax_declarations WHERE year_from = ?",
                                 (YEAR,)).fetchone())
    # The year that has really ended, with a stay of its own in it.
    today = m.house_today()
    title = f"Declare the taxe de séjour for {_label(past)}"
    _booking(conn, rid, "PAST", f"{past + 1}-08-10", f"{past + 1}-08-12", party=2, under18=0)
    s.check("the year just ended is to declare", (m.city_tax_to_declare(conn, today) or {}).get(
        "year_from") == past)
    m.generate_watch_tasks(conn, today)
    s.check("with its task", _task(conn, title) is not None)
    oc.post(f"/admin/city-tax/declared/{past}", data={"note": f"{TAG} ref 123"})
    row = conn.execute("SELECT * FROM city_tax_declarations WHERE year_from = ?",
                       (past,)).fetchone()
    s.check("the owner marks it, and the figures are kept with it",
            row is not None and row["declared_by_user_id"] and row["note"] == f"{TAG} ref 123"
            and row["adult_nights"] == m.city_tax_declaration(conn, past)["adult_nights"])
    s.check("off the owner home", not _home(conn, today, _label(past)))
    m.generate_watch_tasks(conn, today)
    s.check("and the task closed itself", _task(conn, title) is None)
    page = visible_text(oc.get("/admin/city-tax").get_data(as_text=True))
    s.check("the page says when, and by whom", "Declared" in page and f"{TAG} ref 123" in page)
    oc.post(f"/admin/city-tax/declared/{past}", data={"what": "undo"})
    s.check("taken back, it is to declare again",
            (m.city_tax_to_declare(conn, today) or {}).get("year_from") == past)

    s.section("A year nobody stayed in asks for nothing")
    s.check("no stays, no reminder",
            m.city_tax_to_declare(conn, date(YEAR + 10, 11, 1)) is None)

    s.section("The page and the file")
    page = visible_text(oc.get("/admin/city-tax").get_data(as_text=True))
    s.check("the page opens with what to declare", "What to declare to the council" in page
            and "1 October" in page and "30 September" in page, detail=page[:300])
    page = visible_text(oc.get(f"/admin/city-tax?council_year={YEAR}").get_data(as_text=True))
    s.check("every stay of a council year, by its own address",
            f"{TAG} EDGE" in page and f"council's year {LABEL}" in page, detail=page[:400])
    body = oc.get(f"/admin/city-tax/export.csv?council_year={YEAR}")
    rows = {r["reference"]: r for r in csv.DictReader(io.StringIO(body.get_data(as_text=True)))}
    s.check("the year as a file, the stay across 1 October with its three nights",
            rows.get(f"{TAG}-EDGE", {}).get("nights_in_period") == "3"
            and f"taxe-de-sejour-{YEAR}-{YEAR + 1}.csv" in body.headers.get("Content-Disposition", ""),
            detail=str(rows.get(f"{TAG}-EDGE")))
