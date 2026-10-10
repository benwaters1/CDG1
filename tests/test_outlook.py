"""What Money ahead counts as coming in and going out -- and the ways it could lie.

This was the Outlook page's suite. Outlook counted the same money promised in
and out as Money ahead, but priced wages from the rota, which was the only
reason it was a page of its own. On 10 October 2026 the owner settled it --
their monthly figure first, the rota's estimate when there is none -- and the
two became one page. Every rule this suite held still holds, asked of Money
ahead and of rostered_labour_cost, which prices the rota:

  1. Money already in the bank is not money coming in. A guest who paid in
     full is not future income; getting this wrong double-counts deposits.
  2. A maybe is not money. An unconfirmed booking is left out.
  3. A salaried person is not bought by the shift -- putting the chef on one
     more Saturday costs nothing extra -- and a salaried person with no shifts
     is still paid.
  4. Somebody on the rota with no wage on file is named, not counted as free.
  5. A monthly cost lands in every month, an annual one once -- and a monthly
     one with no due date still lands, which Money ahead used to drop.

Every check works on DELTAS: the scratch database is shared with every other
suite, several of which create bookings inside the same window.
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


def _ahead(days=120):
    conn = db()
    try:
        return m.money_ahead(conn, days=days)
    finally:
        conn.close()


def _in_for(ahead, ref):
    return round(sum(i["amount"] for i in ahead["incoming"] if i.get("ref") == f"{TAG}-{ref}"), 2)


def _month(day):
    """The first of day's month and of the next, as rostered_labour_cost wants them."""
    first = day.replace(day=1)
    nxt = date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)
    return first.isoformat(), nxt.isoformat()


def _labour(day, typed_only=False):
    conn = db()
    try:
        return m.rostered_labour_cost(conn, *_month(day), typed_only=typed_only)
    finally:
        conn.close()


def _staff_figure(value):
    """Set or clear the owner's monthly wage figure; returns what was there."""
    conn = db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (m.MONTHLY_STAFF_COST_SETTING,)).fetchone()
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (m.MONTHLY_STAFF_COST_SETTING,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (m.MONTHLY_STAFF_COST_SETTING, str(value)))
    conn.commit()
    conn.close()
    return row["value"] if row else None


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
    # Stamped, as create_booking stamps every real booking. The outlook reads
    # booking_bill now, which trusts the stamp and falls back to the rate card
    # without one -- so an unstamped fixture's total_price was read as whatever
    # the card says those nights cost.
    conn.execute("UPDATE bookings SET room_total_quoted = total_price, "
                 "room_total_quoted_for = arrival_date || '|' || departure_date "
                 "WHERE reference_code = ?", (f"{TAG}-{ref}",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Money ahead: what is coming in and going out")
    _cleanup()
    oc, ec, owner, emp = clients()
    day = _next_month_window()
    kept_figure = _staff_figure(None)        # the rota's estimate is what is tested first
    try:
        _checks(s, oc, ec, day)
    finally:
        _staff_figure(kept_figure)
        _cleanup()
    return s


def _checks(s, oc, ec, day):
    s.section("Only the balance still owed counts as coming in")
    _booking("A", day, total=1000.0, paid=300.0)
    s.check("the outstanding 700 appears", abs(_in_for(_ahead(), "A") - 700.0) < 0.01,
            detail=f"{_in_for(_ahead(), 'A')}")

    s.section("Money already in the bank is not money coming in")
    _booking("B", day, total=800.0, paid=800.0)
    s.check("a booking paid in full adds nothing", _in_for(_ahead(), "B") == 0,
            detail=f"{_in_for(_ahead(), 'B')} -- deposits are being counted twice")

    s.section("An overpayment is not negative income")
    _booking("C", day, total=500.0, paid=650.0)
    s.check("it contributes nothing rather than -150", _in_for(_ahead(), "C") == 0,
            detail=f"{_in_for(_ahead(), 'C')}")

    s.section("A maybe is not money")
    _booking("D", day, total=2000.0, paid=0.0, status="pending")
    s.check("an unconfirmed booking is left out", _in_for(_ahead(), "D") == 0,
            detail=f"{_in_for(_ahead(), 'D')}")

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
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, active, created_at)
           VALUES (?, 900, 'annual', 1, ?)""", (TAG + " dated nowhere", now))
    conn.commit()
    conn.close()
    ahead = _ahead()
    months = [mo["start"][:7] for mo in ahead["months"]]
    monthly = [o["date"][:7] for o in ahead["outgoing"] if o["label"] == TAG + " insurance"]
    s.check("a monthly cost with no due date still lands, in every month",
            sorted(monthly) == sorted(months),
            detail=f"{monthly} against {months} -- it was dropped altogether when it had no date")
    annual = [o for o in ahead["outgoing"] if o["label"] == TAG + " licence"]
    s.check("the annual bill lands once, on its day",
            [o["date"] for o in annual] == [day.isoformat()], detail=str(annual))
    s.check("and an annual one with no date is named, not counted as nothing",
            TAG + " dated nowhere" in ahead["undated_costs"]
            and not any(o["label"] == TAG + " dated nowhere" for o in ahead["outgoing"]),
            detail=str(ahead["undated_costs"]))

    s.section("The rota is costed from the wage in force")
    hourly = _person("Hourly")
    _wage(hourly["id"], "hourly", 15.00)
    before = _labour(day)
    _shift(hourly["id"], day, "09:00", "17:00")          # 8h
    _shift(hourly["id"], day + timedelta(days=1), "09:00", "14:00")   # 5h
    after = _labour(day)
    s.check("13 rostered hours at 15.00 is 195",
            abs((after["gross"] - before["gross"]) - 195.0) < 0.01,
            detail=f"{before['gross']} -> {after['gross']}")
    s.check("and the hours are shown", abs((after["hours"] - before["hours"]) - 13.0) < 0.05,
            detail=f"{after['hours']}")

    s.section("A shift that runs past midnight is not negative")
    before = _labour(day)
    _shift(hourly["id"], day + timedelta(days=2), "20:00", "02:00")   # 6h
    after = _labour(day)
    s.check("20:00 to 02:00 is six hours, not minus eighteen",
            abs((after["gross"] - before["gross"]) - 90.0) < 0.01,
            detail=f"{before['gross']} -> {after['gross']}")
    s.check("_shift_hours agrees on its own", abs(m._shift_hours("20:00", "02:00") - 6.0) < 0.01)

    s.section("A salary is not bought by the shift")
    chef = _person("Chef")
    _wage(chef["id"], "monthly", 3000.00)
    with_salary = _labour(day)
    for k in range(3):
        _shift(chef["id"], day + timedelta(days=k), "17:00", "23:00")
    after = _labour(day)
    s.check("three more shifts change nothing", abs(after["total"] - with_salary["total"]) < 0.01,
            detail=f"{with_salary['total']} -> {after['total']}")
    s.check("and their hours are not added to the rota total",
            abs(after["hours"] - with_salary["hours"]) < 0.05)

    s.section("But a salaried person with no shifts is still paid")
    quiet = _person("Quiet")
    before = _labour(day)
    _wage(quiet["id"], "monthly", 2000.00)
    after = _labour(day)
    s.check("the whole month's salary appears with nothing rostered",
            abs((after["gross"] - before["gross"]) - 2000.0) < 0.01,
            detail=f"{before['gross']} -> {after['gross']} -- a salaried person vanished "
                   "because they had no shifts")

    s.section("Somebody unpriced is named, not dropped")
    ghost = _person("Ghost")
    _shift(ghost["id"], day, "09:00", "18:00")
    s.check("they are named", any(TAG + " Ghost" in n for n in _labour(day)["unpriced"]),
            detail=f"{_labour(day)['unpriced']}")

    s.section("A pay note is never a wage")
    # Somebody with "15 an hour" typed into the free-text pay note and no wage
    # set on their record. The rota costing can guess from the note; Money
    # ahead must not -- estimated_hourly_cost is never a pay figure, and a
    # page somebody plans on cannot carry a guess as money going out.
    noted = _person("Noted")
    conn = db()
    conn.execute("UPDATE users SET pay_rate = '15 per hour', pay_type = 'hourly' WHERE id = ?",
                 (noted["id"],))
    conn.commit()
    conn.close()
    _shift(noted["id"], day, "09:00", "17:00")
    conn = db()
    guessed = m.rostered_labour_cost(conn, *_month(day))
    typed = m.rostered_labour_cost(conn, *_month(day), typed_only=True)
    conn.close()
    s.check("the guess exists, so there is something to refuse",
            TAG + " Noted" not in guessed["unpriced"],
            detail="without a guessable note the next check proves nothing")
    s.check("asked for typed wages only, they are named, not priced from the note",
            TAG + " Noted" in typed["unpriced"]
            and abs(guessed["gross"] - typed["gross"] - 120.0) < 0.01,
            detail=f"guessed {guessed['gross']}, typed {typed['gross']}")
    s.check("and Money ahead names them too",
            any(TAG + " Noted" in n for n in _ahead()["wages_unpriced"]))

    s.section("With no monthly figure, the wages are the rota's, said to be an estimate")
    ahead = _ahead()
    payday = date(day.year, day.month, m.monthrange(day.year, day.month)[1]).isoformat()
    est = [o for o in ahead["outgoing"] if o["kind"] == "Wages, estimated" and o["date"] == payday]
    typed = _labour(day, typed_only=True)
    s.check("the month's wages are on its last day, at what the rota costs from typed wages",
            len(est) == 1 and abs(est[0]["amount"] - typed["total"]) < 0.01,
            detail=f"{est} against {typed['total']}")
    s.check("the page knows it is an estimate", ahead["wages_basis"] == "rota",
            detail=str(ahead["wages_basis"]))
    s.check("and names who it could not price",
            any(TAG + " Ghost" in n for n in ahead["wages_unpriced"]))
    html = oc.get("/management/money-ahead?days=180").get_data(as_text=True)
    s.check("the page says so, and names them",
            "estimated from the rota" in html and "no wage on" in html
            and TAG + " Ghost" in html,
            detail="the wages box must say the figure is the rota's and who it left out")

    s.section("A monthly figure, once set, replaces the estimate")
    _staff_figure(6500)
    ahead = _ahead()
    s.check("the month's wages are the figure",
            any(o["kind"] == "Wages" and o["date"] == payday and abs(o["amount"] - 6500) < 0.01
                for o in ahead["outgoing"]))
    s.check("and nothing is estimated",
            not any(o["kind"] == "Wages, estimated" for o in ahead["outgoing"])
            and ahead["wages_basis"] == "set")
    _staff_figure(None)

    s.section("The arithmetic holds")
    ahead = _ahead()
    s.check("in minus out is the difference, every month",
            all(abs((mo["in"] - mo["out"]) - mo["net"]) < 0.02 for mo in ahead["months"]))
    s.check("and the months add to the totals",
            abs(sum(mo["in"] for mo in ahead["months"]) - ahead["total_in"]) < 0.02
            and abs(sum(mo["out"] for mo in ahead["months"]) - ahead["total_out"]) < 0.02,
            detail=f"{sum(mo['in'] for mo in ahead['months'])} / {ahead['total_in']}, "
                   f"{sum(mo['out'] for mo in ahead['months'])} / {ahead['total_out']}")

    s.section("The window is bounded, and the old page lands on it")
    s.check("asking for none still looks a day ahead", _ahead(0)["days"] >= 1)
    s.check("and an absurd one is clamped", _ahead(99999)["days"] <= 730)
    s.check("a junk window does not break the page",
            oc.get("/management/money-ahead?days=nonsense").status_code == 200)
    for months, days in (("3", 90), ("6", 180), ("12", 365), ("nonsense", 180)):
        r = oc.get(f"/management/outlook?months={months}")
        s.check(f"/management/outlook?months={months} lands on {days} days of Money ahead",
                r.status_code == 302
                and r.headers.get("Location", "").endswith(f"/management/money-ahead?days={days}"),
                detail=f"{r.status_code} {r.headers.get('Location')}")

    s.section("Guards")
    s.check("an employee cannot see Money ahead",
            ec.get("/management/money-ahead").status_code in (302, 403))
    s.check("nor the old address", ec.get("/management/outlook").status_code in (302, 403))


if __name__ == "__main__":
    print(run().report())
