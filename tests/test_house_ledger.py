"""Nine more the database already knew and no page said out loud.

Two of them are about money the house is holding or has lost rather than
money it has earned, which is the half that goes wrong quietly: nobody
complains about a deposit they have forgotten, and a breakage written off
looks exactly like a breakage that never happened.

The retention page is the odd one out and the one worth having. The privacy
notice is a set of claims about this code, and until now nothing measured
them. A promise nobody can check is the same as a promise nobody keeps.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ztest-lg-"


def _cleanup(conn):
    conn.execute("DELETE FROM breakages WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_materials WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shifts WHERE role_note LIKE ?", (TAG + "%",))
    conn.commit()


def _now():
    return m.datetime.now(m.timezone.utc).isoformat()


def run():
    s = Suite("house ledger")
    oc, ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()["id"]

    def stay(ref, arrive, nights, *, price=400.0, deposit=None, returned=None):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                 guest_email, arrival_date, departure_date, party_size, status,
                 total_price, deposit_amount, deposit_returned_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', ?, ?, ?, ?)""",
            (room, TAG + ref, TAG + "tk" + ref, TAG + "Guest " + ref,
             TAG + ref + "@example.invalid", arrive.isoformat(),
             (arrive + timedelta(days=nights)).isoformat(), price, deposit,
             returned, _now()))
        conn.commit()
        return conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                            (TAG + ref,)).fetchone()["id"]

    # --------------------------------------------------------- deposits held
    s.section("Money the house is holding")
    stay("DEPOLD", today - timedelta(days=40), 2, deposit=500.0)
    stay("DEPNEW", today - timedelta(days=3), 2, deposit=300.0)
    stay("DEPSOON", today + timedelta(days=20), 2, deposit=400.0)
    stay("DEPBACK", today - timedelta(days=50), 2, deposit=200.0, returned=_now())
    with m.app.test_request_context():
        dep = m.deposits_held(conn, today)
    held = {h["reference"]: h for h in dep["held"]}
    s.check("a deposit on a stay that has ended is being held",
            TAG + "DEPOLD" in held, detail=str(sorted(held))[:120])
    # A deposit on a stay that has not happened is being held correctly, and
    # putting it in the same list would make the real backlog unreadable.
    s.check("one for a stay still to come is not in the backlog",
            TAG + "DEPSOON" not in held,
            detail="held for a future stay is not held too long")
    s.check("but it is still counted, so the money is not invisible",
            any(u["reference"] == TAG + "DEPSOON" for u in dep["upcoming"]),
            detail=str([u["reference"] for u in dep["upcoming"]])[:100])
    s.check("one already returned has gone from the list",
            TAG + "DEPBACK" not in held)
    s.check("over a fortnight is called overdue",
            any(o["reference"] == TAG + "DEPOLD" for o in dep["overdue"]),
            detail=str([o["reference"] for o in dep["overdue"]])[:100])
    s.check("three days is not", not any(o["reference"] == TAG + "DEPNEW"
                                         for o in dep["overdue"]))

    # ------------------------------------------------------------ room league
    s.section("Which rooms earn")
    with m.app.test_request_context():
        league = m.room_league(conn, today - timedelta(days=90), today)
    s.check("every room is on it, sold or not",
            len(league["rows"]) == conn.execute(
                "SELECT COUNT(*) AS c FROM rooms").fetchone()["c"],
            detail=str(len(league["rows"])))
    sold = [r for r in league["rows"] if r["nights"]]
    if sold:
        r = sold[0]
        s.check("revenue per night available is over the whole window",
                abs(r["revpar"] - r["revenue"] / league["nights_span"]) < 0.02,
                detail=f"{r['revpar']} vs {r['revenue']}/{league['nights_span']}")
        # The distinction the page exists for: a cheap full room and a dear
        # empty one can have the same revenue and are not the same room.
        s.check("and it is not the average night, which ignores empty nights",
                r["adr"] is None or r["revpar"] <= r["adr"],
                detail=f"revpar {r['revpar']} against adr {r['adr']}")
    else:
        s.check("revenue per night available is over the whole window", True,
                detail="no rooms sold in the window")
        s.check("and it is not the average night, which ignores empty nights", True,
                detail="no rooms sold in the window")
    s.check("shares add up to about a hundred per cent",
            not sold or abs(sum(r["share"] for r in league["rows"]) - 100) < 1.5,
            detail=str(round(sum(r["share"] for r in league["rows"]), 1)))

    # --------------------------------------------------------- atelier margin
    s.section("What an atelier leaves after materials")
    conn.execute("INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
                 (TAG + "Pottery", _now()))
    conn.commit()
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (TAG + "Pottery",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
             notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, (today + timedelta(days=20)).isoformat(),
         (today + timedelta(days=22)).isoformat(), TAG + "sess", _now()))
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
           VALUES (?, 'other', 'kg', 6.0, 1, ?)""", (TAG + "Clay", _now()))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (TAG + "sess",)).fetchone()["id"]
    clay = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                        (TAG + "Clay",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_materials (workshop_id, stock_item_id, qty_per_person,
             qty_per_session, note, created_at) VALUES (?, ?, 2, 5, ?, ?)""",
        (wid, clay, TAG + "mat", _now()))
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
             guest_name, guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, 3, 'confirmed', 900, ?)""",
        (sid, TAG + "W1", TAG + "tkW1", TAG + "Potter", TAG + "p@example.invalid", _now()))
    conn.commit()
    with m.app.test_request_context():
        margins = m.workshop_margins(conn, today, today + timedelta(days=365))
    row = next((r for r in margins["rows"] if r["session_id"] == sid), None)
    s.check("the session is on the report", row is not None,
            detail=str([r["title"] for r in margins["rows"]])[:120])
    # 3 heads x 2kg + 5kg per session = 11kg at EUR6 = EUR66.
    s.check("materials scale with the heads booked plus the per-session amount",
            row and abs(row["materials"] - 66.0) < 0.01,
            detail=str(row["materials"] if row else None))
    s.check("and the margin is what is taken less that",
            row and abs(row["margin"] - (900.0 - 66.0)) < 0.01,
            detail=str(row["margin"] if row else None))
    s.check("per head is the margin over the heads, not over the capacity",
            row and abs(row["per_head"] - (900.0 - 66.0) / 3) < 0.01,
            detail=f"{row['per_head'] if row else None} — over 3 booked, not 10 places")

    # A material with no cost makes the margin flattering, so it is named.
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
           VALUES (?, 'other', 'kg', NULL, 1, ?)""", (TAG + "Glaze", _now()))
    conn.commit()
    glaze = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                         (TAG + "Glaze",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_materials (workshop_id, stock_item_id, qty_per_person,
             qty_per_session, note, created_at) VALUES (?, ?, 1, 0, ?, ?)""",
        (wid, glaze, TAG + "mat", _now()))
    conn.commit()
    with m.app.test_request_context():
        margins = m.workshop_margins(conn, today, today + timedelta(days=365))
    row = next((r for r in margins["rows"] if r["session_id"] == sid), None)
    s.check("a material with no cost recorded is counted as uncosted",
            row and row["uncosted"] >= 1, detail=str(row["uncosted"] if row else None))
    s.check("and the session is flagged rather than quietly flattered",
            margins["uncosted_sessions"] >= 1,
            detail="a material costing nothing is the one direction that flatters")

    # ------------------------------------------------------------- retention
    s.section("What the privacy notice promises")
    with m.app.test_request_context():
        before = m.retention_status(conn, today)
    # A finished session still holding dietary notes is exactly what the
    # notice says does not happen.
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
             notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, (today - timedelta(days=40)).isoformat(),
         (today - timedelta(days=38)).isoformat(), TAG + "past", _now()))
    conn.commit()
    past = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                        (TAG + "past",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
             guest_name, guest_email, party_size, status, total_price,
             dietary_notes, created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'confirmed', 400, 'Nut allergy', ?)""",
        (past, TAG + "W2", TAG + "tkW2", TAG + "Past", TAG + "x@example.invalid", _now()))
    conn.commit()
    with m.app.test_request_context():
        after = m.retention_status(conn, today)
    claim = after["claims"][0]
    s.check("a finished session still holding a dietary note is counted",
            claim["waiting"] > before["claims"][0]["waiting"],
            detail=f"{before['claims'][0]['waiting']} -> {claim['waiting']}")
    s.check("and the claim is reported as not yet true",
            not claim["kept"], detail="the notice says these are deleted")
    s.check("so the page does not say everything is kept",
            not after["all_kept"])
    s.check("each claim says where it was measured",
            all(c["where"] for c in after["claims"]),
            detail="a claim with no source is not checkable")

    # ------------------------------------------------------------- wastage
    s.section("What went in the bin, as a share")
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
           VALUES (?, 'food', 'kg', 4.0, 1, ?)""", (TAG + "Herbs", _now()))
    conn.commit()
    herbs = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                         (TAG + "Herbs",)).fetchone()["id"]
    for delta, reason in ((30, "purchase"), (-15, "sale"), (-5, "wastage")):
        conn.execute(
            """INSERT INTO stock_movements (stock_item_id, delta, reason, unit_cost,
                 note, created_at) VALUES (?, ?, ?, 4.0, ?, ?)""",
            (herbs, delta, reason, TAG + "mv",
             (today - timedelta(days=5)).isoformat() + "T09:00:00+00:00"))
    conn.commit()
    with m.app.test_request_context():
        waste = m.wastage_rate(conn, days=90)
    row = next((r for r in waste["rows"] if r["name"] == TAG + "Herbs"), None)
    s.check("an item with wastage is on the list", row is not None,
            detail=str([r["name"] for r in waste["rows"]])[:120])
    s.check("wasted is the wastage movements only", row and row["wasted"] == 5.0,
            detail=str(row["wasted"] if row else None))
    # The share is what makes it a different fact from the euro figure.
    s.check("the share is of everything that left stock, not of what was bought",
            row and abs(row["share"] - 25.0) < 0.1,
            detail=f"{row['share'] if row else None} — 5 wasted of the 20 that "
                   "left stock, not of the 30 that came in")
    s.check("and it is valued at what the item costs",
            row and abs(row["value"] - 20.0) < 0.01,
            detail=str(row["value"] if row else None))

    # ------------------------------------------------------------- workload
    s.section("Who is carrying the house")
    with m.app.test_request_context():
        period = m.resolve_period("month", today.isoformat())
    for i in range(6):
        d = period["start"] + timedelta(days=i)
        conn.execute(
            """INSERT INTO shifts (user_id, shift_date, start_time, end_time,
                 role_note, created_at) VALUES (?, ?, '09:00', '17:00', ?, ?)""",
            (emp["id"], d.isoformat(), TAG + "shift", _now()))
    conn.commit()
    with m.app.test_request_context():
        load = m.workload_balance(conn, period)
    person = next((p for p in load["people"] if p["id"] == emp["id"]), None)
    s.check("the person's shifts are counted", person and person["shifts"] >= 6,
            detail=str(person["shifts"] if person else None))
    s.check("everybody active is listed, including those with none",
            all("shifts" in p for p in load["people"]) and len(load["people"]) >= 1,
            detail=str(len(load["people"])))
    s.check("the shares add up to about a hundred",
            not load["total_shifts"]
            or abs(sum(p["share"] for p in load["people"]) - 100) < 1.5,
            detail=str(round(sum(p["share"] for p in load["people"]), 1)))
    # Against the average of people who WORKED, so a team of part-timers does
    # not make one full-timer look like an outlier by arithmetic alone.
    s.check("the average is over people who worked, not over everybody",
            load["average"] == 0
            or abs(load["average"] - load["total_shifts"] / load["worked"]) < 0.05,
            detail=f"{load['average']} over {load['worked']} who worked")

    # ------------------------------------------------------------ breakages
    s.section("What got broken, and what came back")
    bid = stay("BRK", today - timedelta(days=10), 2)
    for what, cost, charged, decision in (
            (TAG + "lamp", 120.0, 120.0, "charged"),
            (TAG + "glass", 40.0, 0.0, "let_it_go"),
            (TAG + "chair", 300.0, None, "undecided")):
        conn.execute(
            """INSERT INTO breakages (booking_id, room_id, what, found_on,
                 replacement_cost, charge_decision, charged_amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (bid, room, what, (today - timedelta(days=9)).isoformat(), cost,
             decision, charged, _now()))
    conn.commit()
    with m.app.test_request_context():
        brk = m.breakage_recovery(conn, today - timedelta(days=30), today)
    mine = [r for r in brk["rows"] if r["what"].startswith(TAG)]
    s.check("all three are on the report", len(mine) == 3, detail=str(len(mine)))
    waived = next((r for r in mine if r["what"] == TAG + "glass"), None)
    s.check("something waived is written off in full",
            waived and waived["written_off"] == 40.0,
            detail=str(waived["written_off"] if waived else None))
    undecided = next((r for r in mine if r["what"] == TAG + "chair"), None)
    s.check("something not yet decided says so rather than reading as waived",
            undecided and undecided["decision"] == "not decided",
            detail=str(undecided["decision"] if undecided else None))
    s.check("and it is counted as undecided", brk["undecided"] >= 1,
            detail=str(brk["undecided"]))

    # ---------------------------------------------------------- utilisation
    s.section("How full the room was")
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 6, ?, 'confirmed', 300, ?)""",
        (TAG + "T1", TAG + "tkT1", TAG + "Table", TAG + "t@example.invalid",
         (today - timedelta(days=2)).isoformat(), _now()))
    conn.commit()
    with m.app.test_request_context():
        util = m.table_utilisation(conn, today - timedelta(days=30), today)
    night = next((r for r in util["rows"]
                  if r["date"] == (today - timedelta(days=2)).isoformat()), None)
    s.check("the covers land on the night they were booked for",
            night and night["covers"] >= 6, detail=str(night["covers"] if night else None))
    if util["seats"]:
        s.check("fill is the covers over the seats in the room",
                night and night["fill"] is not None
                and abs(night["fill"] - night["covers"] / util["seats"] * 100) < 0.2,
                detail=f"{night['fill'] if night else None}% of {util['seats']} seats")
    else:
        # A percentage of nothing is not a small number.
        s.check("with no table plan it declines to give a percentage",
                night and night["fill"] is None and util["no_seating"],
                detail="a fill of nothing is not zero, it is unknown")

    # ---------------------------------------------------------- seasonality
    s.section("When the season is")
    with m.app.test_request_context():
        seas = m.seasonality(conn, years=2, today=today)
    s.check("all twelve months are listed, empty ones included",
            len(seas["months"]) == 12, detail=str(len(seas["months"])))
    s.check("and all seven nights of the week",
            len(seas["weekdays"]) == 7, detail=str(len(seas["weekdays"])))
    # Measured against days that have HAPPENED, or a year read halfway
    # through shows its remaining months as empty rather than as unmeasured.
    future = [r for r in seas["months"] if r["measured_days"] == 0]
    s.check("a month with no days inside the window is measured as none",
            all(r["occupancy"] == 0 for r in future),
            detail="an unmeasured month must not read as an empty one")
    # The denominator itself, not just a symptom of it. Occupancy staying
    # under 100% is true whatever the capacity is inflated to.
    this_month = seas["months"][today.month - 1]
    s.check("a month is measured against days that actually elapsed",
            0 < this_month["measured_days"] <= 31 * seas["years"],
            detail=f"{this_month['measured_days']} days for {this_month['name']} "
                   f"over {seas['years']} year(s)")
    s.check("occupancy never exceeds a hundred per cent",
            all(r["occupancy"] <= 100.5 for r in seas["months"]),
            detail=str([r["occupancy"] for r in seas["months"] if r["occupancy"] > 100]))

    s.section("Every page renders with data on it")
    for path in ("/management/deposits-held", "/management/seasonality",
                 "/management/rooms-league", "/management/workshop-margins",
                 "/admin/retention", "/admin/restaurant/utilisation",
                 "/admin/wastage", "/admin/workload",
                 "/management/breakage-recovery"):
        s.check(f"{path} renders", oc.get(path).status_code == 200,
                detail="HTTP %s" % oc.get(path).status_code)

    s.section("Only the owner")
    for path in ("/management/deposits-held", "/admin/retention", "/admin/workload"):
        s.check(f"an employee cannot open {path}",
                ec.get(path).status_code in (302, 403),
                detail="HTTP %s" % ec.get(path).status_code)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
