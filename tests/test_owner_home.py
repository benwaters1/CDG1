"""The owner's Home screen, and that every figure on it is real.

The redesign's risk is not that it looks wrong — it is that a dashboard is the
easiest place in an app to ship numbers nobody checks. The handoff came with
invented sample data (guest names, €64,180 of revenue, a 78% occupancy month),
and the whole point of wiring it was that none of that ships.

So these checks are mostly arithmetic: put a known booking, expense and task
in, and assert the figures, the queue, the day list and the occupancy cell for
today all move to match. A figure that cannot be moved by changing the data
underneath it is decoration.

Also checks the split by role, because an employee opening Home must still get
their own screen rather than the owner's decision queue.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOH"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Owner home")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc).isoformat()

    s.section("It loads, and only for the owner")
    r = oc.get("/")
    html = r.get_data(as_text=True)
    s.check("the owner gets the new Home", r.status_code == 200 and "oh-hero" in html, r)
    s.check("with all five figures", html.count("oh-fig__value") == 5,
            detail=f"{html.count('oh-fig__value')} figures")
    emp_html = ec.get("/").get_data(as_text=True)
    s.check("an employee still gets their own dashboard, not this one",
            "oh-hero" not in emp_html)

    s.section("The figures follow the data")
    before = html
    conn = db()
    room = conn.execute("SELECT id, name FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, total_price,
           estimated_arrival_time, created_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 900, '16:00', ?)""",
        (room["id"], f"{TAG}1", f"tok{TAG}1", f"{TAG} Rousseau", today.isoformat(),
         (today + timedelta(days=3)).isoformat(), now))
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, 'rewiring', 1284.0, 'pending', ?)""",
        (f"{TAG} Electricite", now))
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, 'tea towels', 42.0, 'pending', ?)""",
        (f"{TAG} Sundries", now))
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date,
           created_at, origin, status)
           VALUES (?, ?, 'before they arrive', 'high', ?, ?, 'manual', 'open')""",
        (owner["id"], f"{TAG} Turn down the Blue Room", today.isoformat(), now))
    conn.commit()
    conn.close()

    after = oc.get("/").get_data(as_text=True)
    s.check("the arriving guest reaches the day list", f"{TAG} Rousseau" in after)
    s.check("and the task too", f"{TAG} Turn down the Blue Room" in after)
    s.check("both pending invoices reach the queue",
            f"{TAG} Electricite" in after and f"{TAG} Sundries" in after)
    s.check("the amount is shown as money, not a bare number", "1,284.00" in after)
    s.check("the page changed at all when the data did", after != before)

    # The owner's own task must be badged as theirs rather than by initials.
    s.check("a task assigned to the owner is badged as theirs", "oh-av--mine" in after)

    s.section("The queue arithmetic")
    conn = db()
    with m.app.test_request_context():
        queue = m.owner_home_queue(conn)
        n, value, stale = m.owner_queue_totals(conn, today)
    conn.close()
    mine = [q for q in queue if TAG in q["title"]]
    s.check("both invoices are in the queue", len(mine) == 2, detail=f"{len(mine)} found")
    s.check("the total counts them", n >= 2, detail=f"n={n}")
    s.check("and sums their money", value >= 1326, detail=f"value={value}")
    small = [q for q in mine if q["amount"] == "€42.00"]
    big = [q for q in mine if q["amount"] == "€1,284.00"]
    s.check("the small one is offered for bulk approval",
            small and small[0]["bulk_eligible"])
    s.check("the large one is NOT", big and not big[0]["bulk_eligible"])
    s.check("nothing in the queue is bulk-eligible unless it is money",
            all(q["tone"] == "money" for q in queue if q["bulk_eligible"]))

    s.section("Sparklines are seven real points, not decoration")
    conn = db()
    with m.app.test_request_context():
        figures = m.owner_home_figures(conn, today)
    conn.close()
    s.check("every figure carries exactly seven", all(len(f["trend"]) == 7 for f in figures),
            detail=str([len(f["trend"]) for f in figures]))
    s.check("today's arrival lifts the last point above the first",
            figures[1]["trend"][-1] > figures[1]["trend"][0],
            detail=f"arrivals trend {figures[1]['trend']}")
    rooms = figures[0]
    s.check("the occupied-rooms figure counts the booking", rooms["value"] >= 1,
            detail=f"value={rooms['value']}")

    s.section("Occupancy month")
    conn = db()
    with m.app.test_request_context():
        cells, lead, avg = m.owner_home_occupancy(conn, today)
    conn.close()
    import calendar
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    s.check("one cell per day of this month", len(cells) == days_in_month,
            detail=f"{len(cells)} cells for a {days_in_month}-day month")
    s.check("leading blanks match the weekday the 1st falls on",
            lead == today.replace(day=1).weekday(), detail=f"lead={lead}")
    s.check("exactly one cell is marked today",
            sum(1 for c in cells if c["today"]) == 1)
    s.check("today's cell shows the booking as occupancy",
            next(c["occupancy_0_1"] for c in cells if c["today"]) > 0)

    s.section("Revenue is the app's own figure")
    conn = db()
    with m.app.test_request_context():
        revenue, budget, pct = m.owner_home_revenue(conn, today)
    conn.close()
    s.check("six months are charted", len(revenue) == 6, detail=f"{len(revenue)} months")
    s.check("this month includes the new booking",
            revenue[-1]["value"] >= 900, detail=f"{revenue[-1]}")
    s.check("no budget line is drawn when no target is set", pct is None or budget > 0)

    # With a target set, the line appears and is clamped into the chart.
    conn = db()
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                 (m.REVENUE_BUDGET_SETTING, "500"))
    conn.commit()
    with m.app.test_request_context():
        _, budget2, pct2 = m.owner_home_revenue(conn, today)
    conn.execute("DELETE FROM app_settings WHERE key = ?", (m.REVENUE_BUDGET_SETTING,))
    conn.commit()
    conn.close()
    s.check("a target the owner sets is read back", budget2 == 500.0, detail=str(budget2))
    s.check("and its line sits inside the chart", pct2 is not None and 0 < pct2 <= 100,
            detail=f"pct={pct2}")

    s.section("Empty states")
    _cleanup()
    empty = oc.get("/").get_data(as_text=True)
    s.check("with nothing pending it says so, and keeps the section",
            "Nothing waiting on you." in empty and "Waiting on you" in empty)
    s.check("the page still renders its figures", empty.count("oh-fig__value") == 5)

    # The gold button is the page's one loud element. At zero it was the
    # loudest thing on screen and the only one that did nothing.
    s.check("no gold Review button when there is nothing to review",
            "Review 0 decision" not in empty)
    s.check("the quiet actions are still there", "Office display" in empty)
    # "Everything on today is done" with an empty diary reads as an achievement
    # rather than as an empty day. Which branch is correct depends on whether
    # this database actually has anything dated today — seeded tasks often do —
    # so the check asserts they agree rather than assuming the day is empty.
    day_is_empty = "Nothing on today." in empty
    if day_is_empty:
        s.check("an empty day is described as empty, not as finished",
                "nothing is scheduled for today" in empty and
                "everything on today is done" not in empty, detail=_sub_line(empty))
    else:
        s.check("a day with things on it never claims to be empty",
                "nothing is scheduled for today" not in empty, detail=_sub_line(empty))

    s.section("The hero line is grammatical at one")
    conn = db()
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, 'x', 500.0, 'pending', ?)""",
        (f"{TAG} One", now))
    conn.commit()
    conn.close()
    one = oc.get("/").get_data(as_text=True)
    s.check("one thing NEEDS a decision, not need", "1 thing needs a decision" in one,
            detail=_sub_line(one))
    s.check("and the button comes back", "Review 1 decision<" in one)

    _cleanup()
    return s


def _sub_line(html):
    """The hero sentence, for a failure message worth reading."""
    import re as _re
    hit = _re.search(r'class="oh-sub">([^<]*)', html)
    return hit.group(1).strip() if hit else "(no hero line)"

    _cleanup()
    return s
