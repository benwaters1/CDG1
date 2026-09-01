"""Five money questions the house could not answer.

Each of these is a figure that existed in the database and nowhere on a
screen, and each is one an accountant, a bank or a landlord asks:

  Debtor ageing      what we are owed, by how old it is. payables_ageing has
                     answered this for money going OUT since the supplier
                     invoices got due dates; nothing answered it coming in.
  Cash banking       counted out of the drawer versus paid into the bank. The
                     closure says whether a night balanced. It never said
                     whether the money is still here.
  On the books       revenue already sold for months that have not happened,
                     in the month it is EARNED. Money Ahead answers the cash
                     question about the same euros and they must not be added.
  Break-even         what a month costs before anybody arrives, in room-nights
                     at the rate the rooms actually achieve.
  Cost of taking     what the processor keeps. Every other page treats a
                     payment as the amount that arrived.

The checks that matter most are the refusals: a figure that quietly includes
the wrong thing is worse than no figure, because it gets believed and then
disagreed with by the ledger. So each section has at least one check that the
number LEAVES something out on purpose and says so.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ztest-fin-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM cash_bankings WHERE reference LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_closures WHERE period LIKE '20991%'")
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key IN ('card_fee_percent', 'card_fee_fixed')")
    conn.commit()


def _booking(conn, ref, *, arrive, depart, price, status="confirmed", paid=0.0):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, created_at)
           VALUES ((SELECT id FROM rooms LIMIT 1), ?, ?, ?, ?, ?, ?, 2, ?, ?, ?)""",
        (ref, ref + "-tok", TAG + "guest", TAG + "g@example.invalid",
         arrive, depart, status, price, now))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (ref,)).fetchone()["id"]
    if paid:
        conn.execute(
            """INSERT INTO booking_payments (booking_id, amount, method, created_at)
               VALUES (?, ?, 'stripe', ?)""", (bid, paid, now))
    conn.commit()
    return bid


def run():
    s = Suite("finance functions")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now_iso = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ 1
    s.section("Debtor ageing: what we are owed, by how old")
    with m.app.test_request_context():
        base = m.debtor_ageing(conn)["total"]
    _booking(conn, TAG + "OLD", arrive=_iso(-200), depart=_iso(-190), price=1000.0, paid=100.0)
    _booking(conn, TAG + "NEW", arrive=_iso(20), depart=_iso(22), price=400.0)
    with m.app.test_request_context():
        ageing = m.debtor_ageing(conn)
    s.check("a debt that left owing lands in an old bucket",
            ageing["totals"]["older"] >= 900.0, detail=str(ageing["totals"]))
    s.check("a stay still to come is not called late",
            any(i["reference"] == TAG + "NEW" and i["days_late"] == 0
                for i in ageing["items"]), detail=str(ageing["totals"]))
    s.check("the buckets add up to the total",
            abs(sum(ageing["totals"].values()) - ageing["total"]) < 0.005,
            detail=f"{sum(ageing['totals'].values())} vs {ageing['total']}")
    # Asked of booking_bill rather than assumed from total_price: the stay is
    # priced from the room's nightly rate, and a test that invents its own
    # arithmetic is testing the test.
    owed = sum(m.booking_bill(conn, conn.execute(
        "SELECT id FROM bookings WHERE reference_code = ?", (ref,)).fetchone()["id"])["owed"]
        for ref in (TAG + "OLD", TAG + "NEW"))
    s.check("and the total moved by exactly what those two owe",
            abs(ageing["total"] - base - owed) < 0.005,
            detail=f"{base} + {owed:.2f} vs {ageing['total']}")

    # Ateliers have balances too. Rooms alone is quietly short, and a short
    # receivables figure is the kind that gets believed and then disagreed
    # with by the ledger. Proved by putting one there, not by reading the code.
    conn.execute("INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
                 (TAG + "atelier", now_iso))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (TAG + "atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 6, ?, ?)""", (wid, _iso(-40), _iso(-38), TAG + "sess", now_iso))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (TAG + "sess",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
             guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 250, ?)""",
        (sid, TAG + "WS", TAG + "wstok", TAG + "potter", TAG + "p@example.invalid", now_iso))
    conn.commit()
    with m.app.test_request_context():
        with_ws = m.debtor_ageing(conn)
    s.check("an unpaid atelier place is in the receivables too",
            any(i["kind"] == "workshop" and i["reference"] == TAG + "WS"
                for i in with_ws["items"]),
            detail=str([i["kind"] for i in with_ws["items"]][:6]))
    s.check("and it is aged from the session, not from today",
            any(i["reference"] == TAG + "WS" and i["days_late"] >= 30
                for i in with_ws["items"]),
            detail=str([(i["reference"], i["days_late"]) for i in with_ws["items"]][:4]))

    # ------------------------------------------------------------------ 2
    s.section("Cash banking: counted out, paid in, still here")
    now = now_iso

    def _closure(day_iso, expected_cash, counted):
        """A sealed till day, with every column the real thing requires."""
        conn.execute(
            """INSERT INTO pos_closures (kind, period, gross_total, taken_total,
                 vat_json, by_method_json, ticket_count, covers, perpetual_total,
                 prev_hash, hash, opening_float, counted_cash, closed_at)
               VALUES ('day', ?, 0, 0, '{}', ?, 0, 0, 0, '', ?, 0, ?, ?)""",
            (day_iso, '{"cash": %s}' % expected_cash, TAG + day_iso, counted, now))

    _closure("2099-12-01", 100.0, 100.0)
    _closure("2099-12-02", 50.0, 45.0)
    # A third day closed without anybody counting the drawer.
    _closure("2099-12-03", 70.0, None)
    conn.commit()
    try:
        pos = m.cash_position(conn, date(2099, 12, 1), date(2099, 12, 31))
    except Exception as exc:
        pos = {"counted": -1, "variance": -1, "on_hand": -1, "uncounted_days": [],
               "broke": f"{type(exc).__name__}: {exc}"}
    s.check("it survives a day nobody counted", "broke" not in pos,
            detail=pos.get("broke", ""))
    s.check("it counts what was in the drawer, not what the till expected",
            abs(pos["counted"] - 145.0) < 0.005, detail=str(pos["counted"]))
    s.check("and reports the difference between them",
            abs(pos["variance"] + 5.0) < 0.005, detail=str(pos["variance"]))
    s.check("with nothing banked, all of it should be in the safe",
            abs(pos["on_hand"] - 145.0) < 0.005, detail=str(pos["on_hand"]))
    # A night nobody counted is not a night with no cash in it.
    s.check("an uncounted day is named rather than totalled as zero",
            "2099-12-03" in pos["uncounted_days"], detail=str(pos["uncounted_days"]))
    s.check("and adds nothing to what the safe should hold",
            abs(pos["counted"] - 145.0) < 0.005, detail=str(pos["counted"]))

    r = oc.post("/management/cash-banking/new", data={
        "banked_on": "2099-12-04", "amount": "100.00", "reference": TAG + "slip"},
        follow_redirects=True)
    s.check("a paying-in can be recorded", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    pos2 = m.cash_position(conn, date(2099, 12, 1), date(2099, 12, 31))
    s.check("and it comes off what should be in the safe",
            abs(pos2["on_hand"] - 45.0) < 0.005, detail=str(pos2["on_hand"]))
    bad = oc.post("/management/cash-banking/new",
                  data={"banked_on": "2099-12-04", "amount": "0"}, follow_redirects=True)
    s.check("a paying-in of nothing is refused",
            any("amount" in f.lower() for f in flashes(bad)), detail=str(flashes(bad)))

    # ------------------------------------------------------------------ 3
    s.section("On the books: sold, not yet earned")
    with m.app.test_request_context():
        books = m.revenue_on_the_books(conn)
    future = next((r for r in books["rows"]
                   if r["month"] == (datetime.now(m.LOCAL_TZ).date()
                                     + timedelta(days=21)).strftime("%Y-%m")), None)
    s.check("a confirmed stay ahead of us is on the books",
            books["total"] >= 400.0, detail=str(books["total"]))
    s.check("counted in the month it is earned",
            future is not None and future["rooms"] > 0,
            detail=str([r for r in books["rows"] if r["total"]][:3]))
    # A stay running from before today until after it must contribute only the
    # nights still to come. Counting the nights already slept would put earned
    # revenue back on the books and flatter every month it touches.
    _booking(conn, TAG + "SPAN", arrive=_iso(-10), depart=_iso(5), price=1500.0)
    with m.app.test_request_context():
        spanning = m.revenue_on_the_books(conn)
    added = round(spanning["total"] - books["total"], 2)
    s.check("a stay straddling today counts only the nights still to come",
            abs(added - 500.0) < 1.0,
            detail=f"added {added:.2f}; 5 of 15 nights at 100 a night is 500")
    s.check("and no month before this one carries revenue",
            not any(r["month"] < datetime.now(m.LOCAL_TZ).date().strftime("%Y-%m")
                    for r in spanning["rows"] if r["total"]),
            detail="past months carry revenue")
    books = spanning
    _booking(conn, TAG + "HELD", arrive=_iso(40), depart=_iso(42), price=999.0, status="pending")
    with m.app.test_request_context():
        books2 = m.revenue_on_the_books(conn)
    s.check("a held booking is counted apart", books2["pending"] >= 999.0,
            detail=str(books2["pending"]))
    s.check("and never folded into the confirmed total",
            abs(books2["total"] - books["total"]) < 0.005,
            detail=f"{books['total']} -> {books2['total']}")

    # ------------------------------------------------------------------ 4
    s.section("Break-even: what the month costs before anybody arrives")
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, active, created_at)
           VALUES (?, 1200, 'annual', 1, ?)""", (TAG + "insurance", now))
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, active, created_at)
           VALUES (?, 500, 'monthly', 1, ?)""", (TAG + "heating", now))
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, active, created_at)
           VALUES (?, 9999, 'monthly', 0, ?)""", (TAG + "cancelled", now))
    conn.commit()
    be = m.break_even_month(conn)
    labels = {c["label"]: c["monthly"] for c in be["costs"]}
    s.check("an annual cost is spread over twelve months",
            abs(labels.get(TAG + "insurance", 0) - 100.0) < 0.005,
            detail=str(labels.get(TAG + "insurance")))
    s.check("a monthly cost is taken as it stands",
            abs(labels.get(TAG + "heating", 0) - 500.0) < 0.005,
            detail=str(labels.get(TAG + "heating")))
    # Switched off is a decision the owner made; charging for it anyway would
    # raise the break-even against a bill nobody is paying.
    s.check("a cost switched off is left out", TAG + "cancelled" not in labels,
            detail=str(sorted(labels)))
    # An hourly wage is a cost of opening, not of existing. Folding it in
    # would raise break-even by an amount that only exists once the nights
    # are sold. Proved by adding one and watching the fixed cost not move.
    emp_id = conn.execute("SELECT id FROM users WHERE role = 'employee' LIMIT 1").fetchone()["id"]
    before_fixed = m.break_even_month(conn)["fixed"]
    conn.execute(
        """INSERT INTO wage_records (user_id, effective_from, basis, gross_amount, created_at)
           VALUES (?, ?, 'hourly', 25, ?)""", (emp_id, _iso(-5), now_iso))
    conn.commit()
    s.check("an hourly wage does not move the fixed cost",
            abs(m.break_even_month(conn)["fixed"] - before_fixed) < 0.005,
            detail=f"{before_fixed} -> {m.break_even_month(conn)['fixed']}")
    conn.execute(
        """INSERT INTO wage_records (user_id, effective_from, basis, gross_amount, created_at)
           VALUES (?, ?, 'monthly', 2000, ?)""", (emp_id, _iso(-4), now_iso))
    conn.commit()
    s.check("a salary does", abs(m.break_even_month(conn)["fixed"] - before_fixed - 2000) < 0.005,
            detail=f"{before_fixed} -> {m.break_even_month(conn)['fixed']}")
    conn.execute("DELETE FROM wage_records WHERE user_id = ? AND effective_from >= ?",
                 (emp_id, _iso(-5)))
    conn.commit()
    be = m.break_even_month(conn)
    if be["rate"]:
        s.check("nights needed is the fixed cost over the achieved rate",
                abs(be["nights_needed"] - round(be["fixed"] / be["rate"], 1)) < 0.05,
                detail=f"{be['fixed']} / {be['rate']}")
    else:
        s.check("with no nights sold it declines to invent a rate",
                be["nights_needed"] is None, detail=str(be["nights_needed"]))

    # ------------------------------------------------------------------ 5
    s.section("Cost of taking money: what the processor keeps")
    start, end = house_today() - timedelta(days=1), house_today() + timedelta(days=1)
    cost = m.cost_of_taking_money(conn, start, end)
    s.check("with no rate typed it refuses to compute one",
            cost["configured"] is False and cost["fee"] is None,
            detail=str(cost["fee"]))
    s.check("but still says what was taken", cost["taken"] >= 100.0,
            detail=str(cost["taken"]))
    r = oc.post("/management/cost-of-taking-money/settings",
                data={"card_fee_percent": "1.5", "card_fee_fixed": "0.25"},
                follow_redirects=True)
    s.check("the owner can type the real rate", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    cost2 = m.cost_of_taking_money(conn, start, end)
    s.check("and then the fee is worked out", cost2["configured"] and cost2["fee"] > 0,
            detail=str(cost2["fee"]))
    card = next((x for x in cost2["rows"] if x["method"] == "stripe"), None)
    s.check("a card payment is charged for",
            card is not None and card["fee"] and card["fee"] > 0,
            detail=str(card))
    cash = next((x for x in cost2["rows"] if x["method"] == "cash"), None)
    s.check("cash costs nothing to take",
            cash is None or cash["fee"] is None or cash["fee"] == 0,
            detail=str(cash))
    # Charging a fee on a room charge would bill it twice: it is settled later
    # by whatever method the stay uses, and that payment is already counted.
    s.check("a charge moved to a room is not charged a fee",
            "room" not in m.CARD_METHODS, detail=str(m.CARD_METHODS))

    s.section("None of it is reachable by an employee")
    for path in ("/management/debtors", "/management/cash-banking",
                 "/management/on-the-books", "/management/break-even",
                 "/management/cost-of-taking-money"):
        got = ec.get(path)
        s.check(f"{path} refuses an employee", got.status_code in (302, 403),
                detail=f"HTTP {got.status_code}")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
