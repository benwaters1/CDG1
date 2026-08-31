"""What is coming in and going out, on one timeline.

Every figure already existed on some screen: a room booking knows what it is
worth and what has been paid, a workshop knows its balance and the date that
balance falls due, a standing cost knows when it next goes out. Nobody had
ever put them together, so the question a house funding a restoration actually
asks — which month is tight — had no answer in the app at all.

The two things worth testing hardest are both about not lying. It must count
only money somebody has committed to, never an average or a guess; and it must
not present a change as a balance, because the app has never known what is in
the bank.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "money-"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM insurance_policies WHERE provider LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM maintenance_visits WHERE schedule_id IN "
                 "(SELECT id FROM maintenance_schedules WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM maintenance_schedules WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key IN (?, ?)",
                 (m.OPENING_BALANCE_SETTING, m.MONTHLY_STAFF_COST_SETTING))
    conn.commit()


def _labels(items):
    return [i["label"] for i in items]


def run():
    s = Suite("Money ahead")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = m.service_day()
    now = datetime.now(timezone.utc).isoformat()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()

    def d(n):
        return (today + timedelta(days=n)).isoformat()

    s.section("A guest who still owes something is money to come")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, 'x@example.com', ?, ?, 2, 'confirmed', 1000, 300, ?)""",
        (room["id"], TAG + "R1", TAG + "tok1", TAG + "Owing", d(20), d(23), now))
    ahead = m.money_ahead(conn, days=90, today=today)
    owed = [i for i in ahead["incoming"] if TAG + "Owing" in i["label"]]
    s.check("it is listed once", len(owed) == 1, detail=str(_labels(ahead["incoming"])))
    s.check("for what is left, not the whole price",
            owed and owed[0]["amount"] == 700.0,
            detail=str(owed[0]["amount"] if owed else None))
    s.check("dated to their arrival, when they settle",
            owed and owed[0]["date"] == d(20), detail=owed[0]["date"] if owed else "?")

    s.section("A guest who has paid in full is not")
    # Counting money already banked as money still to come is the fastest way
    # to make the page useless.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, 'x@example.com', ?, ?, 2, 'confirmed', 800, 800, ?)""",
        (room["id"], TAG + "R2", TAG + "tok2", TAG + "Settled", d(25), d(27), now))
    conn.commit()
    ahead = m.money_ahead(conn, days=90, today=today)
    s.check("a settled booking is left out",
            not any(TAG + "Settled" in l for l in _labels(ahead["incoming"])))

    s.section("Nor is a booking nobody has confirmed")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, 'x@example.com', ?, ?, 2, 'pending', 900, 0, ?)""",
        (room["id"], TAG + "R3", TAG + "tok3", TAG + "Maybe", d(30), d(32), now))
    conn.commit()
    ahead = m.money_ahead(conn, days=90, today=today)
    s.check("a pending request is not counted as income",
            not any(TAG + "Maybe" in l for l in _labels(ahead["incoming"])),
            detail="money nobody has agreed to pay")

    s.section("A workshop balance lands on its due date")
    session = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY start_date DESC LIMIT 1").fetchone()
    if session:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                 guest_name, guest_email, status, total_price, deposit_amount,
                 deposit_paid_at, balance_amount, balance_due_date, created_at)
               VALUES (?, ?, ?, ?, 'x@example.com', 'confirmed', 4800, 1440, ?, 3360, ?, ?)""",
            (session["id"], TAG + "W1", TAG + "wtok", TAG + "Atelier", now, d(45), now))
        conn.commit()
        ahead = m.money_ahead(conn, days=90, today=today)
        bal = [i for i in ahead["incoming"] if TAG + "Atelier" in i["label"]]
        s.check("the balance is there", len(bal) == 1, detail=str(_labels(ahead["incoming"])))
        s.check("only the balance — the deposit is already paid",
                bal and bal[0]["amount"] == 3360.0,
                detail=str(bal[0]["amount"] if bal else None))
        s.check("on the date it falls due, not the day of the workshop",
                bal and bal[0]["date"] == d(45), detail=bal[0]["date"] if bal else "?")

    s.section("Standing costs go out on their own dates, monthly and annually")
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category,
             next_due_date, active, created_at)
           VALUES (?, 400, 'monthly', 'utilities', ?, 1, ?)""",
        (TAG + "Electricity", d(8), now))
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category,
             next_due_date, active, created_at)
           VALUES (?, 2400, 'annual', 'other', ?, 1, ?)""",
        (TAG + "Taxe fonciere", d(40), now))
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category,
             next_due_date, active, created_at)
           VALUES (?, 999, 'monthly', 'other', ?, 0, ?)""",
        (TAG + "Cancelled", d(9), now))
    conn.commit()
    ahead = m.money_ahead(conn, days=90, today=today)
    elec = [o for o in ahead["outgoing"] if TAG + "Electricity" in o["label"]]
    s.check("a monthly cost repeats across the window", len(elec) == 3,
            detail=f"{len(elec)} occurrences: {[e['date'] for e in elec]}")
    s.check("keeping the same day of the month",
            len({m.parse_date(e["date"]).day for e in elec}) == 1,
            detail=str([e["date"] for e in elec]))
    annual = [o for o in ahead["outgoing"] if TAG + "Taxe" in o["label"]]
    s.check("an annual one appears once in ninety days", len(annual) == 1,
            detail=f"{len(annual)}")
    s.check("and a cost switched off does not appear at all",
            not any(TAG + "Cancelled" in l for l in _labels(ahead["outgoing"])))

    s.section("An invoice waiting on a decision is owed now")
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
             submitted_at) VALUES ('supplier_invoice', ?, 'roof', 3400, 'pending', ?)""",
        (TAG + "Roofer", now))
    conn.commit()
    ahead = m.money_ahead(conn, days=90, today=today)
    inv = [o for o in ahead["outgoing"] if TAG + "Roofer" in o["label"]]
    s.check("it is on the list", len(inv) == 1)
    s.check("dated today rather than a date nobody set",
            inv and inv[0]["date"] == today.isoformat(),
            detail=inv[0]["date"] if inv else "?")

    s.section("The months add up to the totals")
    ahead = m.money_ahead(conn, days=90, today=today)
    s.check("in", round(sum(mo["in"] for mo in ahead["months"]), 2) == ahead["total_in"],
            detail=f"{sum(mo['in'] for mo in ahead['months'])} vs {ahead['total_in']}")
    s.check("out", round(sum(mo["out"] for mo in ahead["months"]), 2) == ahead["total_out"],
            detail=f"{sum(mo['out'] for mo in ahead['months'])} vs {ahead['total_out']}")
    s.check("and the difference is in minus out",
            ahead["net"] == round(ahead["total_in"] - ahead["total_out"], 2))
    s.check("every item sits inside the window",
            all(ahead["from"] <= i["date"] <= ahead["to"]
                for i in ahead["incoming"] + ahead["outgoing"]),
            detail="something fell outside the dates it claims to cover")

    s.section("A change is not a balance")
    # The most important thing on the page. With no opening figure the running
    # column is a change, and calling it a balance would invent the number the
    # owner most needs to trust.
    s.check("with nothing entered there is no closing balance",
            ahead["opening"] is None and ahead["closing"] is None,
            detail=f"opening {ahead['opening']}, closing {ahead['closing']}")
    s.check("and no month claims to be below zero",
            not any(mo["short"] for mo in ahead["months"]),
            detail="a shortfall was claimed without knowing the balance")

    oc.post("/management/money-ahead/opening", data={"opening": "50000"},
            follow_redirects=True)
    ahead = m.money_ahead(conn, days=90, today=today)
    s.check("once the bank figure is given it is used",
            ahead["opening"] == 50000.0, detail=str(ahead["opening"]))
    s.check("and the closing figure follows from it",
            ahead["closing"] == round(50000 + ahead["net"], 2),
            detail=str(ahead["closing"]))
    s.check("the running line starts from it too",
            ahead["months"] and ahead["months"][0]["running"] == round(
                50000 + ahead["months"][0]["net"], 2),
            detail=str(ahead["months"][0]["running"] if ahead["months"] else None))

    oc.post("/management/money-ahead/opening", data={"opening": "  "},
            follow_redirects=True)
    s.check("clearing it goes back to showing change only",
            m.money_ahead(conn, days=90, today=today)["opening"] is None)
    r = oc.post("/management/money-ahead/opening", data={"opening": "not a number"},
                follow_redirects=True)
    s.check("and nonsense is refused rather than stored as zero",
            b"not a number" in r.data.lower()
            or m.money_ahead(conn, days=90, today=today)["opening"] is None)

    s.section("Wages are the owner's figure, and only theirs")
    # The one big number this app will not derive. Pay rates are free text,
    # estimated_hourly_cost() says in its own docstring that it is never a
    # payroll figure, and most people on the books have no usable rate — so a
    # derived wage bill would be a guess on the page that promises otherwise.
    ahead = m.money_ahead(conn, days=90, today=today)
    s.check("nothing is assumed until it is set",
            ahead["monthly_staff"] is None
            and not any(o["kind"] == "Wages" for o in ahead["outgoing"]),
            detail="a wage figure appeared that nobody entered")

    oc.post("/management/money-ahead/staff-cost", data={"monthly_staff": "6500"},
            follow_redirects=True)
    ahead = m.money_ahead(conn, days=90, today=today)
    wages = [o for o in ahead["outgoing"] if o["kind"] == "Wages"]
    s.check("once set it is used", ahead["monthly_staff"] == 6500.0,
            detail=str(ahead["monthly_staff"]))
    s.check("once a month, not once per anything else", len(wages) == 3,
            detail=f"{len(wages)} over 90 days: {[w['date'] for w in wages]}")
    s.check("at the end of each month, when salaries are paid",
            all(m.parse_date(w["date"]).day >= 28 for w in wages),
            detail=str([w["date"] for w in wages]))
    s.check("and it says whose figure it is",
            all("you set" in w["label"] for w in wages),
            detail=str(wages[0]["label"]) if wages else "")
    s.check("the total went up by three months of it",
            ahead["total_out"] >= 3 * 6500, detail=str(ahead["total_out"]))

    # A part month at either end must not carry a full month of wages — that
    # is a figure nobody could reconcile against a bank statement.
    short = m.money_ahead(conn, days=20, today=today)
    short_wages = [o for o in short["outgoing"] if o["kind"] == "Wages"]
    s.check("a window shorter than a month carries at most one payday",
            len(short_wages) <= 1, detail=f"{len(short_wages)} in 20 days")
    s.check("and any it does carry falls inside the window",
            all(short["from"] <= w["date"] <= short["to"] for w in short_wages))

    oc.post("/management/money-ahead/staff-cost", data={"monthly_staff": ""},
            follow_redirects=True)
    ahead = m.money_ahead(conn, days=90, today=today)
    s.check("clearing it takes wages back out",
            ahead["monthly_staff"] is None
            and not any(o["kind"] == "Wages" for o in ahead["outgoing"]))

    s.section("Scheduled upkeep is shown, but never counted")
    # The distinction the whole page rests on. Everything above is money
    # somebody has committed to PAYING. This is money the house has committed
    # to SPENDING, priced from what the same job cost last time — a projection,
    # which is the one thing the figures promise not to be. So it is listed and
    # excluded, and the page says which.
    conn.execute(
        """INSERT INTO maintenance_schedules (name, category, every_months,
             next_due_on, lead_days, required_by, active, created_at)
           VALUES (?, 'heating', 12, ?, 21, 'insurance', 1, ?)""",
        (TAG + "Ramonage", d(35), now))
    sched_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO maintenance_schedules (name, category, every_months,
             next_due_on, lead_days, required_by, active, created_at)
           VALUES (?, 'building', 12, ?, 21, 'none', 1, ?)""",
        (TAG + "Gutters", d(50), now))
    conn.execute(
        """INSERT INTO maintenance_visits (schedule_id, done_on, done_by, cost, created_at)
           VALUES (?, ?, 'Le ramoneur', 180.0, ?)""", (sched_id, d(-330), now))
    conn.commit()

    before = m.money_ahead(conn, days=90, today=today)
    work = {w["label"]: w for w in before["upcoming_work"]}
    s.check("both jobs are listed", len(work) == 2, detail=str(list(work)))
    s.check("the one with a history carries last time's price",
            work.get(TAG + "Ramonage", {}).get("amount") == 180.0,
            detail=str(work.get(TAG + "Ramonage")))
    s.check("and says when that price is from",
            work.get(TAG + "Ramonage", {}).get("since") == d(-330))
    s.check("the one never costed is listed without a number rather than a guess",
            work.get(TAG + "Gutters", {}).get("amount") is None,
            detail=str(work.get(TAG + "Gutters")))
    s.check("and it is counted as unpriced", before["work_unpriced"] == 1,
            detail=str(before["work_unpriced"]))
    s.check("the subtotal is only what has a price", before["work_total"] == 180.0,
            detail=str(before["work_total"]))
    s.check("one is marked as expected of you", work.get(TAG + "Ramonage", {}).get("required"))
    s.check("the other is not", not work.get(TAG + "Gutters", {}).get("required"))

    # The property that matters most.
    s.check("none of it reaches the outgoing total",
            not any(TAG + "Ramonage" in o["label"] for o in before["outgoing"]),
            detail="scheduled work leaked into committed spend")
    s.check("nor the months", round(sum(mo["out"] for mo in before["months"]), 2)
            == before["total_out"], detail="the months stopped adding up")
    s.check("and the net is untouched by it",
            before["net"] == round(before["total_in"] - before["total_out"], 2))

    s.section("A retired schedule stops appearing")
    conn.execute("UPDATE maintenance_schedules SET active = 0 WHERE id = ?", (sched_id,))
    conn.commit()
    s.check("it is gone from the list",
            not any(TAG + "Ramonage" in w["label"]
                    for w in m.money_ahead(conn, days=90, today=today)["upcoming_work"]))
    conn.execute("DELETE FROM maintenance_visits WHERE schedule_id = ?", (sched_id,))
    conn.execute("DELETE FROM maintenance_schedules WHERE name LIKE ?", (TAG + "%",))
    conn.commit()

    s.section("Approving a bill does not make the money look better")
    # The hole this section exists for. Only 'pending' expenses were counted,
    # so a supplier invoice the owner had APPROVED — a debt agreed, the most
    # certain outflow in the whole forecast — disappeared from it, and the
    # figure improved the moment somebody said yes to a bill.
    ac = db()
    _room = ac.execute("SELECT id FROM rooms LIMIT 1").fetchone()
    due = (datetime.now(m.LOCAL_TZ).date() + timedelta(days=20)).isoformat()
    ac.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
           invoice_number, invoice_date, due_date, submitted_at)
           VALUES ('supplier_invoice', ?, ?, 640.0, 'pending', 'AH-1', ?, ?, ?)""",
        ("ZTAHEAD Charpentier", "ZTAHEAD roof timbers",
         datetime.now(m.LOCAL_TZ).date().isoformat(), due,
         datetime.now(timezone.utc).isoformat()))
    ac.commit()
    eid = ac.execute("SELECT id FROM expenses WHERE description = ?",
                       ("ZTAHEAD roof timbers",)).fetchone()["id"]
    with m.app.test_request_context():
        pending_view = m.money_ahead(ac, days=90)
    ac.execute("UPDATE expenses SET status = 'approved' WHERE id = ?", (eid,))
    ac.commit()
    with m.app.test_request_context():
        approved_view = m.money_ahead(ac, days=90)
    ac.close()

    s.check("a bill awaiting a decision is in the forecast",
            any("ZTAHEAD" in o["label"] for o in pending_view["outgoing"]),
            detail="money the house is being asked for")
    s.check("and approving it does not remove it",
            any("ZTAHEAD" in o["label"] for o in approved_view["outgoing"]),
            detail="it used to vanish, so saying yes to a bill improved the "
                   "money ahead")
    s.check("the total does not fall when a bill is agreed",
            approved_view["total_out"] >= pending_view["total_out"] - 0.01,
            detail=f"{pending_view['total_out']} -> {approved_view['total_out']}")

    agreed = [o for o in approved_view["outgoing"] if "ZTAHEAD" in o["label"]]
    s.check("an agreed invoice sits on the day it falls due, not on today",
            agreed and agreed[0]["date"] == due,
            detail=f"{agreed[0]['date'] if agreed else '?'}, expected {due} — "
                   "everything used to pile onto today because there was no "
                   "due date to put it on")
    s.check("and it carries the invoice number, so it can be found",
            agreed and agreed[0]["ref"] == "AH-1",
            detail=str(agreed[0]["ref"]) if agreed else "")

    s.check("agreed and undecided are reported apart",
            approved_view["agreed_out"] + approved_view["undecided_out"]
            <= approved_view["total_out"] + 0.01,
            detail=f"agreed {approved_view['agreed_out']}, "
                   f"undecided {approved_view['undecided_out']}, "
                   f"total {approved_view['total_out']}")
    s.check("and the agreed side is not empty now there is an agreed bill",
            approved_view["agreed_out"] >= 640.0,
            detail=str(approved_view["agreed_out"]))

    s.section("What we owe our own people is owed now, not in thirty days")
    ac = db()
    ac.execute(
        """INSERT INTO expenses (kind, submitted_by_user_id, description, amount,
           status, due_date, submitted_at)
           VALUES ('staff_expense', ?, ?, 55.0, 'approved', ?, ?)""",
        (_emp["id"], "ZTAHEAD their own money",
         (datetime.now(m.LOCAL_TZ).date() + timedelta(days=60)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    ac.commit()
    with m.app.test_request_context():
        with_claim = m.money_ahead(ac, days=90)
    ac.execute("DELETE FROM expenses WHERE description LIKE 'ZTAHEAD%'")
    ac.commit()
    ac.close()
    claim = [o for o in with_claim["outgoing"] if "own people" in o["label"]]
    s.check("a reimbursement is in the forecast", bool(claim),
            detail=str([o["kind"] for o in with_claim["outgoing"]])[:110])
    s.check("dated today, whatever due date happens to be on the row",
            claim and claim[0]["date"] == datetime.now(m.LOCAL_TZ).date().isoformat(),
            detail=f"{claim[0]['date'] if claim else '?'} — somebody who spent "
                   "their own money is not a supplier on thirty-day terms")

    s.section("On the page")
    r = oc.get("/management/money-ahead")
    s.check("it opens", r.status_code == 200, detail=str(r.status_code))
    body = r.get_data(as_text=True)
    s.check("with the guest who owes on it", (TAG + "Owing") in body)
    s.check("and the standing cost", (TAG + "Electricity") in body)
    s.check("it says plainly that it is not a balance",
            "not a balance" in body or "change" in body.lower(),
            detail="the running column could be read as a bank balance")
    s.check("a shorter window is a different page",
            oc.get("/management/money-ahead?days=30").status_code == 200)

    s.section("Guards")
    s.check("an employee cannot see it",
            ec.get("/management/money-ahead").status_code in (302, 403))
    s.check("nor set the opening balance",
            ec.post("/management/money-ahead/opening",
                    data={"opening": "1"}).status_code in (302, 403))
    s.check("nor the wage figure",
            ec.post("/management/money-ahead/staff-cost",
                    data={"monthly_staff": "1"}).status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s
