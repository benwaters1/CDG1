"""Is the season selling faster than last year, at the same point?

The comparison is the whole feature, and it is the part that is easy to get
wrong in a way nobody notices. Against LAST YEAR'S FINAL, a house that ended
August full looks behind every March and the report says so every spring until
nobody reads it. Against THE SAME POINT LAST YEAR, the question is whether more
is sold now than had been sold by this date a year ago — which means counting
last year's bookings as they stood on that day, from created_at, and ignoring
everything booked after it.

So most of these checks are about time: a stay booked last year AFTER the
comparison date must not count, a stay across two months must count in each for
its own nights, and a house with no history must be told it has none rather than
shown a flattering percentage against zero.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPACE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, arrival, nights, booked_on, price=600.0, status="confirmed"):
    """A stay with a chosen created_at — which is the axis this report turns on."""
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, '', '', ?, ?, 2, ?, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         status, price, booked_on.isoformat() + "T10:00:00+00:00"))
    conn.commit()
    conn.close()


def _pace(months=3, today=None):
    conn = db()
    try:
        return m.booking_pace(conn, months=months, today=today)
    finally:
        conn.close()


def run():
    s = Suite("Pace")
    _cleanup()
    oc, ec, owner, emp = clients()

    # A fixed "today" so none of this depends on the day the suite runs.
    today = date(2027, 3, 15)
    this_year = date(2027, 6, 1)
    last_year = date(2026, 6, 1)

    s.section("What is on the books now")
    _stay("NOW-A", arrival=this_year + timedelta(days=4), nights=3,
          booked_on=date(2027, 2, 1), price=900)
    p = _pace(months=6, today=today)
    june = [r for r in p["rows"] if r["month"] == date(2027, 6, 1)]
    s.check("June is one of the months ahead", len(june) == 1,
            detail=f"{[r['month'].isoformat() for r in p['rows']]}")
    s.check("the three nights are counted", june and june[0]["now"]["nights"] == 3,
            detail=f"{june[0]['now'] if june else None}")

    s.section("Last year counts only what had been sold by the same date")
    # Booked in FEBRUARY last year: before 15 March, so it counts.
    _stay("THEN-EARLY", arrival=last_year + timedelta(days=4), nights=2,
          booked_on=date(2026, 2, 1), price=500)
    # Booked in MAY last year: after 15 March, so it must not.
    _stay("THEN-LATE", arrival=last_year + timedelta(days=10), nights=5,
          booked_on=date(2026, 5, 1), price=1200)
    p = _pace(months=6, today=today)
    june = [r for r in p["rows"] if r["month"] == date(2027, 6, 1)][0]
    s.check("the one booked before the date counts", june["then"]["nights"] == 2,
            detail=f"{june['then']} — expected the 2-night stay only; 7 means the "
                   "later booking was counted and the comparison is against last "
                   "year's final, not against the same point")
    s.check("so this year reads as ahead", june["nights_delta"] == 1,
            detail=f"{june['nights_delta']}")

    s.section("A stay across two months counts in each for its own nights")
    # 29 June to 3 July: two nights in June, two in July.
    _stay("SPLIT", arrival=date(2027, 6, 29), nights=4,
          booked_on=date(2027, 1, 5), price=800)
    p = _pace(months=6, today=today)
    june = [r for r in p["rows"] if r["month"] == date(2027, 6, 1)][0]
    july = [r for r in p["rows"] if r["month"] == date(2027, 7, 1)][0]
    s.check("June gains two", june["now"]["nights"] == 5,
            detail=f"{june['now']['nights']} — 3 from the first stay plus 2 here")
    s.check("and July gains two", july["now"]["nights"] == 2,
            detail=f"{july['now']['nights']} — a whole stay landing in one month "
                   "is how a month looks full that is not")
    s.check("the money is split with the nights",
            abs(june["now"]["revenue"] - (900 + 400)) < 1,
            detail=f"{june['now']['revenue']} — 900 plus half of 800")

    s.section("A cancelled stay is out of both sides")
    _stay("GONE", arrival=this_year + timedelta(days=8), nights=3,
          booked_on=date(2027, 1, 9), price=700, status="cancelled")
    _stay("GONE-LY", arrival=last_year + timedelta(days=8), nights=3,
          booked_on=date(2026, 1, 9), price=700, status="cancelled")
    p = _pace(months=6, today=today)
    june = [r for r in p["rows"] if r["month"] == date(2027, 6, 1)][0]
    s.check("this year is unchanged", june["now"]["nights"] == 5,
            detail=f"{june['now']['nights']}")
    s.check("and so is last year", june["then"]["nights"] == 2,
            detail=f"{june['then']['nights']} — counting a cancellation on the "
                   "older side only flatters this year by exactly the bookings "
                   "that fell through")

    s.section("A pending request counts, because it is what is on the books")
    _stay("PENDING", arrival=this_year + timedelta(days=12), nights=2,
          booked_on=date(2027, 3, 1), price=400, status="pending")
    p = _pace(months=6, today=today)
    june = [r for r in p["rows"] if r["month"] == date(2027, 6, 1)][0]
    s.check("it is included", june["now"]["nights"] == 7,
            detail=f"{june['now']['nights']} — a request the house has not yet "
                   "answered is still demand, and leaving it out understates the "
                   "season on exactly the days it is busiest")

    s.section("Nothing booked after today counts on this side either")
    _stay("FUTURE", arrival=this_year + timedelta(days=20), nights=4,
          booked_on=date(2027, 4, 1), price=900)
    p = _pace(months=6, today=today)
    june = [r for r in p["rows"] if r["month"] == date(2027, 6, 1)][0]
    s.check("a stay booked after the as-at date is out",
            june["now"]["nights"] == 7,
            detail=f"{june['now']['nights']} — the two sides have to be measured "
                   "the same way or the comparison means nothing")

    s.section("A house with no history is told so")
    _cleanup()
    _stay("ONLY", arrival=this_year + timedelta(days=3), nights=2,
          booked_on=date(2027, 2, 2), price=400)
    p = _pace(months=6, today=today)
    s.check("no baseline is admitted", not p["has_baseline"],
            detail="a first-year house shown 'up 100%' against nothing has been "
                   "told something false")
    s.check("and no percentage is invented", p["nights_pct"] is None,
            detail=f"{p['nights_pct']}")

    s.section("The 29th of February does not break it")
    # today.replace(year=...) has no counterpart in a non-leap year, and a
    # report that fails one day in four fails on a day nobody is watching.
    leap = date(2028, 2, 29)
    try:
        p = _pace(months=3, today=leap)
        ok = True
    except ValueError:
        ok = False
    s.check("it still runs", ok,
            detail="today.replace(year=year-1) raises on the 29th")
    s.check("and steps back to the 28th",
            ok and p["as_at_last_year"] == date(2027, 2, 28),
            detail=f"{p['as_at_last_year'] if ok else None}")

    s.section("The page")
    body = oc.get("/admin/reports/pace").get_data(as_text=True)
    s.check("it opens", "Pace" in body)
    s.check("and names the date it is comparing against",
            "same day a year ago" in body,
            detail="a comparison with no stated baseline is a number nobody can "
                   "check")
    s.check("with no period selector", "_period_selector" not in body
            and "period=month" not in body,
            detail="this answers what is ahead; picking a past window turns it "
                   "into the occupancy report with extra arithmetic")
    s.check("every table is wrapped for a phone",
            body.count("<table") == body.count('class="table-wrap"'))

    s.section("The percentage is shown with the number behind it")
    # "Up 18%" on its own is a figure nobody can check. Against 118 nights it is
    # one they can, and the difference matters most when the baseline is small.
    _cleanup()
    _stay("BASE", arrival=last_year + timedelta(days=2), nights=4,
          booked_on=date(2026, 1, 2), price=800)
    _stay("NOW", arrival=this_year + timedelta(days=2), nights=6,
          booked_on=date(2027, 1, 2), price=1200)
    p2 = _pace(months=6, today=today)
    s.check("last year's totals are carried, not just the percentage",
            p2["nights_last_year"] == 4 and p2["revenue_last_year"] > 0,
            detail=f"{p2['nights_last_year']}, {p2['revenue_last_year']}")
    s.check("and it is on the reports index",
            "pace" in oc.get("/admin/reports").get_data(as_text=True).lower())
    s.check("the CSV comes out",
            oc.get("/admin/reports/pace/export.csv").status_code == 200)

    s.section("Guards")
    s.check("an employee cannot read it",
            ec.get("/admin/reports/pace").status_code in (302, 403))

    _cleanup()
    return s
