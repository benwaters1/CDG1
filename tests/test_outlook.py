"""Committed out against expected in — and the four ways it could lie.

The page is deliberately not a forecast and not a bank balance. It only counts
things somebody has already agreed to. That makes it useful, and it also makes
it easy to get quietly wrong in ways that all point the same direction:
flattering.

  1. Counting money already in the bank as money coming in. A guest who paid in
     full is not future income. Getting this wrong double-counts every deposit.
  2. Counting maybes. An unconfirmed enquiry is not money, and an outlook that
     includes them is a wish.
  3. Costing a salaried person per rostered shift. Putting the chef on one more
     Saturday costs the house nothing extra, and a forecast that says otherwise
     argues for the wrong roster. Conversely a salaried person with NO shifts
     rostered still has to be paid.
  4. Silently dropping somebody off the rota because their wage is unknown.
     That understates the cost, so this checks they are named instead.

Every check works on DELTAS rather than absolute figures: the scratch database
is shared with every other suite, several of which create bookings inside the
same window, so an absolute total here would be measuring their fixtures.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOUT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM shifts WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM wage_records WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _next_month_window():
    """A month far enough ahead to be inside the outlook and easy to reason about."""
    today = m.house_today()
    first = today.replace(day=1)
    y, mth = divmod(first.month - 1 + 2, 12)      # two months out
    return date(first.year + y, mth + 1, 15)      # the 15th, safely mid-month


def _outlook(months=6):
    conn = db()
    try:
        return m.cash_outlook(conn, months=months)
    finally:
        conn.close()


def _month_row(outlook, day):
    return next((r for r in outlook["rows"] if r["start"] <= day < r["end"]), None)


def _person(name):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _wage(user_id, basis, amount, effective_from="2020-01-01"):
    conn = db()
    conn.execute(
        """INSERT INTO wage_records (user_id, effective_from, basis, gross_amount, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, effective_from, basis, amount, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _shift(user_id, on_day, start="09:00", end="17:00"):
    conn = db()
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, created_at)
           VALUES (?,?,?,?,?)""",
        (user_id, on_day.isoformat(), start, end,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _booking(ref, arrival, total, paid, status="confirmed"):
    conn = db()
    room = _harness.ensure_room()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status, total_price,
           amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'out@example.invalid', ?, ?, 2, ?, ?, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}", f"{TAG} Guest {ref}",
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(), status,
         total, paid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def run():
    s = Suite("Outlook")
    _cleanup()
    oc, ec, owner, emp = clients()
    day = _next_month_window()

    s.section("Only the balance still owed counts as coming in")
    before = _month_row(_outlook(), day)
    _booking("A", day, total=1000.0, paid=300.0)
    after = _month_row(_outlook(), day)
    s.check("the outstanding 700 appears",
            abs((after["in_rooms"] - before["in_rooms"]) - 700.0) < 0.01,
            detail=f"{before['in_rooms']} -> {after['in_rooms']}")
    s.check("and it is in the month of arrival",
            abs((after["money_in"] - before["money_in"]) - 700.0) < 0.01)

    s.section("Money already in the bank is not money coming in")
    before = _month_row(_outlook(), day)
    _booking("B", day, total=800.0, paid=800.0)
    after = _month_row(_outlook(), day)
    s.check("a booking paid in full adds nothing",
            abs(after["in_rooms"] - before["in_rooms"]) < 0.01,
            detail=f"{before['in_rooms']} -> {after['in_rooms']} — deposits are "
                   "being counted twice")

    s.section("An overpayment is not negative income")
    before = _month_row(_outlook(), day)
    _booking("C", day, total=500.0, paid=650.0)
    after = _month_row(_outlook(), day)
    s.check("it contributes nothing rather than −150",
            abs(after["in_rooms"] - before["in_rooms"]) < 0.01,
            detail=f"{before['in_rooms']} -> {after['in_rooms']} — an overpaid "
                   "booking is reducing expected income")

    s.section("A maybe is not money")
    before = _month_row(_outlook(), day)
    _booking("D", day, total=2000.0, paid=0.0, status="pending")
    after = _month_row(_outlook(), day)
    s.check("an unconfirmed booking is left out",
            abs(after["in_rooms"] - before["in_rooms"]) < 0.01,
            detail=f"{before['in_rooms']} -> {after['in_rooms']}")

    s.section("A monthly cost lands in every month, an annual one lands once")
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, active, created_at)
           VALUES (?, 400, 'monthly', 1, ?)""", (TAG + " insurance", now))
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, next_due_date, active, created_at)
           VALUES (?, 1200, 'annual', ?, 1, ?)""",
        (TAG + " licence", day.isoformat(), now))
    conn.commit()
    conn.close()
    out = _outlook()
    with_annual = _month_row(out, day)
    others = [r for r in out["rows"] if r is not with_annual]
    s.check("the monthly cost is in every month",
            all(r["out_recurring"] >= 400 for r in out["rows"]),
            detail=f"{[r['out_recurring'] for r in out['rows']]}")
    s.check("the annual bill is flagged on its month", with_annual["out_annual"] == 1200,
            detail=f"{with_annual['out_annual']}")
    s.check("and is not in the others",
            all(r["out_annual"] == 0 for r in others),
            detail=f"{[r['out_annual'] for r in others]}")

    s.section("The rota is costed from the wage in force")
    hourly = _person("Hourly")
    _wage(hourly["id"], "hourly", 15.00)
    before = _month_row(_outlook(), day)
    _shift(hourly["id"], day, "09:00", "17:00")          # 8h
    _shift(hourly["id"], day + timedelta(days=1), "09:00", "14:00")   # 5h
    after = _month_row(_outlook(), day)
    s.check("13 rostered hours at 15.00 is 195",
            abs((after["out_labour"] - before["out_labour"]) - 195.0) < 0.01,
            detail=f"{before['out_labour']} -> {after['out_labour']}")
    s.check("and the hours are shown",
            abs((after["labour"]["hours"] - before["labour"]["hours"]) - 13.0) < 0.05,
            detail=f"{after['labour']['hours']}")

    s.section("A shift that runs past midnight is not negative")
    before = _month_row(_outlook(), day)
    _shift(hourly["id"], day + timedelta(days=2), "20:00", "02:00")   # 6h
    after = _month_row(_outlook(), day)
    s.check("20:00 to 02:00 is six hours, not minus eighteen",
            abs((after["out_labour"] - before["out_labour"]) - 90.0) < 0.01,
            detail=f"{before['out_labour']} -> {after['out_labour']}")
    s.check("_shift_hours agrees on its own", abs(m._shift_hours("20:00", "02:00") - 6.0) < 0.01,
            detail=f"{m._shift_hours('20:00', '02:00')}")

    s.section("A salary is not bought by the shift")
    # Putting the chef on one more Saturday costs nothing extra. A forecast that
    # says it does argues for the wrong roster.
    chef = _person("Chef")
    _wage(chef["id"], "monthly", 3000.00)
    with_salary = _month_row(_outlook(), day)
    _shift(chef["id"], day, "17:00", "23:00")
    _shift(chef["id"], day + timedelta(days=1), "17:00", "23:00")
    _shift(chef["id"], day + timedelta(days=2), "17:00", "23:00")
    after = _month_row(_outlook(), day)
    s.check("three more shifts change nothing",
            abs(after["out_labour"] - with_salary["out_labour"]) < 0.01,
            detail=f"{with_salary['out_labour']} -> {after['out_labour']}")
    s.check("and their hours are not added to the rota total",
            abs(after["labour"]["hours"] - with_salary["labour"]["hours"]) < 0.05,
            detail="salaried hours were counted as costed rota hours")

    s.section("But a salaried person with no shifts is still paid")
    quiet = _person("Quiet")
    before = _month_row(_outlook(), day)
    _wage(quiet["id"], "monthly", 2000.00)
    after = _month_row(_outlook(), day)
    s.check("the whole month's salary appears with nothing rostered",
            abs((after["out_labour"] - before["out_labour"]) - 2000.0) < 0.01,
            detail=f"{before['out_labour']} -> {after['out_labour']} — a "
                   "salaried person vanished because they had no shifts")

    s.section("Somebody unpriced is named, not dropped")
    ghost = _person("Ghost")
    _shift(ghost["id"], day, "09:00", "18:00")
    out = _outlook()
    s.check("they are named",
            any(TAG + " Ghost" in n for n in out["unpriced"]), detail=f"{out['unpriced']}")

    s.section("The arithmetic holds")
    out = _outlook()
    s.check("in minus out is the difference, every month",
            all(abs((r["money_in"] - r["money_out"]) - r["net"]) < 0.02 for r in out["rows"]),
            detail=f"{[(r['money_in'], r['money_out'], r['net']) for r in out['rows'][:2]]}")
    s.check("the income columns add to expected in",
            all(abs((r["in_rooms"] + r["in_ateliers"] + r["in_events"]) - r["money_in"]) < 0.02
                for r in out["rows"]))
    s.check("the cost columns add to committed out",
            all(abs((r["out_recurring"] + r["out_labour"]) - r["money_out"]) < 0.02
                for r in out["rows"]))
    s.check("and the months add to the totals",
            abs(sum(r["net"] for r in out["rows"]) - out["net"]) < 0.02)

    s.section("The window is bounded")
    s.check("three months means three rows", len(_outlook(3)["rows"]) == 3)
    s.check("and it starts with this month", _outlook(3)["rows"][0]["is_current"])
    conn = db()
    s.check("asking for none still gives one", len(m.cash_outlook(conn, months=0)["rows"]) == 1)
    conn.close()

    s.section("The page")
    page = oc.get("/management/outlook")
    html = page.get_data(as_text=True)
    s.check("it loads", page.status_code == 200, page)
    s.check("and says plainly it is not a bank balance",
            "not a bank balance" in html.lower(),
            detail="a page of money figures that reads as a balance is worse "
                   "than no page")
    s.check("it names who is not costed", TAG + " Ghost" in html)
    s.check("a junk months value does not break it",
            oc.get("/management/outlook?months=nonsense").status_code == 200)
    s.check("and an absurd one is clamped",
            len(_outlook(99)["rows"]) <= 12 or
            oc.get("/management/outlook?months=99").status_code == 200)

    s.section("Guards")
    s.check("an employee cannot see the outlook",
            ec.get("/management/outlook").status_code in (302, 403))

    _cleanup()
    return s
