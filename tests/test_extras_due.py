"""Something the house owes a guest can be ticked off, and seen before it is late.

booking_extras.status has allowed 'delivered' since the extras were written, and
nothing ever set it: a line went in as 'confirmed' and could only become
'cancelled'. So a guest ordered a hamper for Tuesday, or a transfer to the
station, and there was no list of what was outstanding and no way to say it had
been done. On a busy morning that is how somebody gets billed for a car nobody
arranged.

Two things are load-bearing and are checked hardest:

  - TICKING OFF CHANGES NO MONEY. booking_bill counts every line that is not
    cancelled, so a delivered extra is owed exactly as before. Handing over a
    hamper is a reason to charge for it, not to stop.
  - IT TAKES NOTHING MORE OUT OF STOCK. The sale depleted the cellar when the
    line was created; a second movement on delivery takes the same bottle twice.
"""
import io
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZDUE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM stock_movements WHERE booking_extra_id IN "
                 "(SELECT id FROM booking_extras WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, arrive_offset=-1, nights=4):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    arrival = m.service_day() + timedelta(days=arrive_offset)
    departure = arrival + timedelta(days=nights)
    priced = m.compute_room_total(conn, room, arrival, departure)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}@example.invalid", arrival.isoformat(),
         departure.isoformat(), priced, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _extra(booking, name, *, price=60.0, qty=1, scheduled=None, notes=None):
    conn = db()
    line_id = m.add_booking_extra(
        conn, "room", booking["id"], f"{TAG} {name}", qty, unit_price=price,
        notes=notes, scheduled_for=scheduled.isoformat() if scheduled else None)
    conn.commit()
    row = conn.execute("SELECT * FROM booking_extras WHERE id = ?", (line_id,)).fetchone()
    conn.close()
    return row


def _line(line_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM booking_extras WHERE id = ?",
                            (line_id,)).fetchone()
    finally:
        conn.close()


def _due():
    conn = db()
    try:
        return m.extras_due(conn)
    finally:
        conn.close()


def _mine(due, key="rows"):
    return [r for r in due[key] if TAG in (r["line"]["name"] or "")]


def _owed(booking_id):
    conn = db()
    try:
        bill = m.booking_bill(conn, booking_id)
        return bill["owed"] if bill else 0
    finally:
        conn.close()


def run():
    s = Suite("What we owe guests")
    _cleanup()
    oc, ec, owner, emp = clients()

    # THE SERVICE DAY, not the calendar day. The house's day runs past midnight
    # to 05:00, so a transfer at two in the morning is still the previous day's
    # job -- and extras_due uses service_day() for exactly that reason. A fixture
    # built on date.today() disagrees with the feature for five hours a night,
    # which is when this list matters most.
    today = m.service_day()
    stay = _stay("A")
    hamper = _extra(stay, "hamper", price=60, scheduled=today)
    transfer = _extra(stay, "transfer", price=90, scheduled=today - timedelta(days=1))
    flowers = _extra(stay, "flowers", price=40)          # no date: during the stay

    s.section("The list shows what is outstanding")
    due = _due()
    s.check("all three are on it", len(_mine(due)) == 3,
            detail=f"{[r['line']['name'] for r in _mine(due)]}")
    s.check("the one from yesterday is called overdue",
            [r["line"]["id"] for r in _mine(due, "overdue")] == [transfer["id"]],
            detail="a transfer that was for yesterday is a different problem from "
                   "one that is for this afternoon, and it is the one nobody "
                   "notices on a list sorted by date alone")
    s.check("today's is marked today",
            [r["line"]["id"] for r in _mine(due, "today")] == [hamper["id"]],
            detail=f"{[r['line']['name'] for r in _mine(due, 'today')]}")
    s.check("and one with no date is still listed",
            any(r["line"]["id"] == flowers["id"] and r["when"] is None
                for r in _mine(due)),
            detail="an unscheduled extra fell off the list entirely")
    s.check("the guest is named on the row",
            all(TAG in (r["line"]["guest_name"] or "") for r in _mine(due)),
            detail="a job with no guest against it cannot be done")

    s.section("Ticking one off")
    r = ec.post(f"/extras/{hamper['id']}/delivered", follow_redirects=True)
    s.check("an employee can do it", r.status_code == 200,
            detail=f"HTTP {r.status_code} — the person who carried it is the one "
                   "who knows it was handed over")
    s.check("the status says delivered",
            _line(hamper["id"])["status"] == "delivered",
            detail=f"{_line(hamper['id'])['status']}")
    s.check("with who did it",
            _line(hamper["id"])["delivered_by_user_id"] is not None,
            detail="a flag with nobody behind it cannot answer a guest who says "
                   "the hamper never arrived")
    s.check("and when", bool(_line(hamper["id"])["delivered_at"]))
    s.check("it drops off the list", len(_mine(_due())) == 2,
            detail=f"{[r['line']['name'] for r in _mine(_due())]}")

    s.section("And it changes nothing about money")
    before = _owed(stay["id"])
    ec.post(f"/extras/{flowers['id']}/delivered", follow_redirects=True)
    s.check("the stay owes exactly the same",
            abs(_owed(stay["id"]) - before) < 0.005,
            detail=f"{before} -> {_owed(stay['id'])} — handing over a hamper is a "
                   "reason to charge for it, not to stop")
    conn = db()
    bill = m.booking_bill(conn, stay["id"])
    conn.close()
    s.check("a delivered line is still on the bill",
            any(TAG in (l["label"] or "") and "hamper" in (l["label"] or "")
                for l in bill["lines"]),
            detail=f"{[l['label'] for l in bill['lines']]}")

    s.section("And takes nothing more out of stock")
    conn = db()
    moves = conn.execute(
        "SELECT COUNT(*) c FROM stock_movements WHERE booking_extra_id = ?",
        (hamper["id"],)).fetchone()["c"]
    conn.close()
    s.check("no second movement was written", moves <= 1,
            detail=f"{moves} movements — the sale already depleted the cellar; a "
                   "second on delivery takes the same bottle twice")

    s.section("Undoing, because a tick in the wrong row happens")
    r = ec.post(f"/extras/{hamper['id']}/delivered", data={"undo": "1"},
                follow_redirects=True)
    s.check("it goes back to outstanding",
            _line(hamper["id"])["status"] == "confirmed",
            detail=f"{_line(hamper['id'])['status']}")
    s.check("and the record of who is cleared with it",
            _line(hamper["id"])["delivered_by_user_id"] is None,
            detail="somebody is left credited with handing over something that is "
                   "still on the shelf")
    s.check("it is back on the list", len(_mine(_due())) == 2,
            detail=f"{[r['line']['name'] for r in _mine(_due())]}")

    s.section("Ticking the same thing twice does nothing")
    ec.post(f"/extras/{hamper['id']}/delivered", follow_redirects=True)
    r = ec.post(f"/extras/{hamper['id']}/delivered", follow_redirects=True)
    s.check("it is refused rather than restamped",
            any("already" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")
    first_at = _line(hamper["id"])["delivered_at"]
    ec.post(f"/extras/{hamper['id']}/delivered", follow_redirects=True)
    s.check("and the time it was handed over does not move",
            _line(hamper["id"])["delivered_at"] == first_at,
            detail="the record of when says whenever somebody last pressed the "
                   "button")

    s.section("A cancelled line cannot be delivered")
    doomed = _extra(stay, "cancelled thing", price=25, scheduled=today)
    conn = db()
    m.cancel_booking_extra(conn, doomed["id"])
    conn.commit()
    conn.close()
    r = ec.post(f"/extras/{doomed['id']}/delivered", follow_redirects=True)
    s.check("it stays cancelled", _line(doomed["id"])["status"] == "cancelled",
            detail=f"{_line(doomed['id'])['status']} — a cancelled line brought "
                   "back to life is money back on a guest's bill")
    s.check("and it is not on the list",
            doomed["id"] not in [r["line"]["id"] for r in _mine(_due())])

    s.section("A stay that has gone is not still owed things")
    past = _stay("PAST", arrive_offset=-30, nights=2)
    _extra(past, "old hamper", price=30, scheduled=today - timedelta(days=28))
    s.check("its extras are off the list",
            "old hamper" not in " ".join(r["line"]["name"] for r in _mine(_due())),
            detail="a list that keeps every undelivered thing the house has ever "
                   "sold is a list nobody reads twice")

    s.section("The page itself")
    # One of each, on purpose. By this point everything else has been delivered
    # or cancelled, so the "still to hand over" table was empty and every check
    # below was exercising the overdue table alone -- which is how a price column
    # added to the other one went unnoticed.
    upcoming = _extra(stay, "picnic", price=45, scheduled=today + timedelta(days=1))
    body = ec.get("/extras/due").get_data(as_text=True)
    s.check("both tables are on the page",
            "Overdue" in body and "Still to hand over" in body,
            detail="a page check that only ever renders one of two tables says "
                   "nothing about the other")
    s.check("it opens for an employee", f"{TAG} transfer" in body,
            detail="this is a list of jobs, and jobs belong on the employee side")
    s.check("overdue has its own heading", "Overdue" in body)
    s.check("and nothing appears in both tables",
            body.count(f"{TAG} transfer") == 1,
            detail="somebody ticks the same job off twice and wonders why")
    # ANY price, not two particular figures. Looking for "60.00" and "90.00"
    # passed with a price column added, because by this point in the suite
    # neither of those lines is in the table being rendered -- the check was
    # asserting something about the fixture rather than about the page.
    s.check("no prices anywhere on it", "€" not in body,
            detail=f"{body.count(chr(0x20ac))} price marks — somebody carrying a "
                   "hamper upstairs does not need to know what the guest paid, "
                   "and this list gets left on tables")
    s.check("every table is wrapped for a phone",
            body.count("<table") == body.count('class="table-wrap"'))

    s.section("It can be empty")
    # Enforced on the SOURCE, like test_table_overflow. Rendering and hoping the
    # database is quiet made this pass or fail on which suites had run first --
    # other suites leave undelivered extras behind, so on a full run the page was
    # never empty and the check was reporting on them rather than on this page.
    src = io.open("templates/extras_due.html", encoding="utf-8").read()
    s.check("the page has an empty branch", "{% if not due.rows %}" in src,
            detail="a panel that can never be empty becomes furniture")
    s.check("and says something rather than rendering blank",
            "Nothing outstanding" in src,
            detail="an empty page with no words on it reads as broken")
    conn = db()
    due = m.extras_due(conn)
    conn.close()
    s.check("and the count is a real count, not a constant",
            due["count"] == len(due["rows"]), detail=f"{due['count']}")

    s.section("Guards")
    stay2 = _stay("G")
    line = _extra(stay2, "guarded", price=10, scheduled=today)
    anon = m.app.test_client()
    s.check("a stranger cannot tick anything off",
            anon.post(f"/extras/{line['id']}/delivered",
                      follow_redirects=False).status_code in (302, 401, 403))
    s.check("and nothing changed", _line(line["id"])["status"] == "confirmed")
    s.check("a line that does not exist is refused, not a 500",
            ec.post("/extras/999999/delivered",
                    follow_redirects=True).status_code == 200)

    _cleanup()
    return s
