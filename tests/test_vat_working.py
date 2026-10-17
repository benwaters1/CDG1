"""VAT collected in a period, on one page instead of four.

The rates were already configured — accommodation, food, alcohol, ateliers,
extras — and the till already sealed its VAT per rate into every closed day.
What did not exist was anywhere to read the total off for a quarter, so the
figure for a return was rebuilt by hand from four places, which is where errors
live.

What this is NOT is a return, and the reason is that the two halves do not rest
on the same thing. The till is exact: read from the hashed closures, split
properly between food and alcohol. Rooms, ateliers and extras are an estimate —
the configured rate applied to revenue by service date, when French VAT on
services is normally due on payment received. The page says so before it says
any number.

Two arithmetic traps, both checked below.

`end` is exclusive, like resolve_period and the payroll pack. An inclusive end
put the 1st of September in August's figures AND in September's — a day counted
twice across a quarter boundary, which is the one mistake a tax working paper
cannot make.

And prices here include VAT, so the tax is the gross less the net, not the gross
times the rate. Times-the-rate on a VAT-inclusive price overstates it by the
tax on the tax: on €1,100 at 10% that is €110 instead of €100.
"""
import json
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZVAT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM pos_closures WHERE period LIKE '2031-%'")
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM refunds WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _sealed_day(day, sold):
    """A day that took money at the till and was closed off by the till's own
    close, so its VAT is sealed in exactly the shape the app writes.

    `sold` is [(price, vat_rate), ...], VAT-inclusive. This used to write the
    closure by hand as a flat {rate: amount}, a shape pos_close_period has
    never written -- so the suite stayed green while every real closed day
    was missing from the working paper.
    """
    conn = db()
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, status, service_state,
           service_date, settled_total, payment_method, opened_at, closed_at)
           VALUES (?, 2, 'paid', 'bill', ?, ?, 'card_terminal', ?, ?)""",
        (f"{TAG}-sealed-{day}", day, sum(p for p, _ in sold),
         f"{day}T18:30:00+00:00", f"{day}T20:30:00+00:00"))
    oid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    for price, rate in sold:
        conn.execute(
            """INSERT INTO pos_order_lines (order_id, name, unit_price, quantity,
               course, state, vat_rate, created_at)
               VALUES (?, ?, ?, 1, 'main', 'served', ?, ?)""",
            (oid, f"{TAG} dish", price, rate, f"{day}T19:00:00+00:00"))
    closure, created = m.pos_close_period(conn, "day", day)
    conn.commit()
    conn.close()
    assert created, f"{day} was already closed"
    return closure


def _hand_written_closure(day, vat_json):
    """A closure row put in by hand, for the shapes the till never writes."""
    conn = db()
    conn.execute(
        """INSERT INTO pos_closures (kind, period, gross_total, discount_total,
           service_total, taken_total, vat_json, by_method_json, ticket_count,
           covers, perpetual_total, prev_hash, hash, closed_at)
           VALUES ('day', ?, 0, 0, 0, 0, ?, '{}', 1, 2, 0, 'x', ?, ?)""",
        (day, vat_json, f"{TAG}{day}", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _room_booking(arrival, total):
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'v@example.invalid', ?, ?, 2, 'confirmed', ?, 4.80, ?)""",
        (room, f"{TAG}-{arrival}", f"tok{TAG}{arrival}", f"{TAG} Guest", arrival,
         (date.fromisoformat(arrival) + timedelta(days=2)).isoformat(), total,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _working(start, end):
    conn = db()
    try:
        return m.vat_working(conn, start, end)
    finally:
        conn.close()


def _working_on(start, end, today):
    conn = db()
    try:
        return m.vat_working(conn, start, end, today=today)
    finally:
        conn.close()


def run():
    s = Suite("VAT working paper")
    _cleanup()
    oc, ec, owner, emp = clients()

    # A quiet future month, so nothing else in the database lands in it.
    start, end = date(2031, 3, 1), date(2031, 4, 1)
    nxt_start, nxt_end = date(2031, 4, 1), date(2031, 5, 1)

    s.section("The till figures are read from the sealed closures, per rate")
    # Closed by the till itself. VAT-inclusive: 264 at 10% is 240 + 24,
    # 48 at 20% is 40 + 8, 66 at 10% is 60 + 6.
    sealed = _sealed_day("2031-03-10", [(264.0, 10), (48.0, 20)])
    _sealed_day("2031-03-11", [(66.0, 10)])
    band = json.loads(sealed["vat_json"]).get("10.0")
    s.check("the till seals each rate as a band of gross, VAT and net",
            isinstance(band, dict) and set(band) == {"gross", "vat", "net"},
            detail=f"sealed {sealed['vat_json']} — the working paper must read "
                   "what the till writes, not what a test assumed")
    w = _working(start, end)
    till = {l["rate"]: l for l in w["lines"] if "till" in l["source"]}
    s.check("food and alcohol are kept apart", sorted(till) == [10.0, 20.0],
            detail=f"{sorted(till)} — every closed day was skipped, and the "
                   "page showed no till line at all")
    s.check("the 10% band adds up across days",
            abs(till.get(10.0, {}).get("vat", 0) - 30.0) < 0.01,
            detail=f"{till.get(10.0)}")
    s.check("and the 20% band", abs(till.get(20.0, {}).get("vat", 0) - 8.0) < 0.01,
            detail=f"{till.get(20.0)}")
    s.check("the sealed gross and net come with it, not a dash",
            till.get(10.0, {}).get("gross") == 330.0 and till.get(10.0, {}).get("net") == 300.0
            and till.get(20.0, {}).get("gross") == 48.0 and till.get(20.0, {}).get("net") == 40.0,
            detail=f"{till}")
    s.check("they are marked exact, not estimated",
            bool(till) and all(l["exact"] for l in till.values()))
    s.check("and nothing read cleanly is reported as unreadable",
            w["unreadable_days"] == [], detail=str(w["unreadable_days"]))
    page = oc.get("/admin/vat?period=month&date=2031-03-15").get_data(as_text=True)
    s.check("the till's lines are on the page",
            "Restaurant till — 10%" in page and "Restaurant till — 20%" in page,
            detail="the owner reads the page, not the dict")
    s.check("and no unreadable-day warning on a clean month",
            "could not be read" not in page)

    s.section("A day nobody closed is reported, not quietly missing")
    s.check("31 days in March", w["days_in_period"] == 31, detail=f"{w['days_in_period']}")
    s.check("two of them closed", w["days_closed"] == 2)
    # Never closed off means a day that is over, took money and was not
    # closed -- not every other day in the month. It counted the days still to
    # come and the nights the restaurant was shut: "30 days never closed off"
    # in an October twelve days old.
    conn = db()
    for day in ("2031-03-12", "2031-03-20", "2031-03-25"):
        conn.execute(
            """INSERT INTO pos_orders (table_label, covers, status, service_state,
               service_date, settled_total, payment_method, opened_at, closed_at)
               VALUES (?, 2, 'paid', 'bill', ?, 40, 'cash', ?, ?)""",
            (f"{TAG}-tab-{day}", day, f"{day}T19:00:00+00:00", f"{day}T20:00:00+00:00"))
    conn.commit()
    conn.close()
    mid = _working_on(start, end, date(2031, 3, 20))
    s.check("a past day that took money and was not closed is counted",
            mid["unclosed_days"] == ["2031-03-12"],
            detail=f"{mid['unclosed_days']} — a short figure that does not say "
                   "it is short is the worst kind")
    s.check("a day still to come is not, nor today while service may still be on, "
            "nor a night nothing was sold",
            mid["days_not_closed"] == 1, detail=str(mid["unclosed_days"]))
    after = _working_on(start, end, date(2031, 4, 2))
    s.check("once the month is over the later one is counted too",
            after["unclosed_days"] == ["2031-03-12", "2031-03-20", "2031-03-25"],
            detail=str(after["unclosed_days"]))
    conn = db()
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (f"{TAG}-tab-%",))
    conn.commit()
    conn.close()

    s.section("The boundary day belongs to one period, not both")
    # An inclusive end put 1 April in March AND in April.
    _sealed_day("2031-04-01", [(1100.0, 10)])
    march = _working(start, end)
    april = _working(nxt_start, nxt_end)
    march_vat = sum(l["vat"] for l in march["lines"] if "till" in l["source"])
    april_vat = sum(l["vat"] for l in april["lines"] if "till" in l["source"])
    s.check("1 April is not in March", abs(march_vat - 38.0) < 0.01,
            detail=f"March till VAT {march_vat} — 1 April leaked in")
    s.check("it is in April", abs(april_vat - 100.0) < 0.01, detail=f"{april_vat}")
    s.check("and each day is counted once across the boundary",
            abs((march_vat + april_vat) - 138.0) < 0.01,
            detail=f"{march_vat} + {april_vat}")

    s.section("A closed day that cannot be read is named, not skipped")
    # The old read caught the TypeError a band raised and moved on, so every
    # closed day vanished and the page said nothing. A shape the till does not
    # write must now be reported -- and still not guessed at.
    bands = m.closure_vat_bands
    s.check("the band shape the till writes reads back as bands",
            bands('{"10.0": {"gross": 64.0, "vat": 5.82, "net": 58.18}}')
            == {10.0: {"gross": 64.0, "vat": 5.82, "net": 58.18}})
    s.check("a day that sold nothing is a reading, not a fault", bands("{}") == {})
    for label, raw in (("a flat {rate: amount}", '{"10": 5.0}'),
                       ("a band missing its VAT", '{"10": {"gross": 11.0, "net": 10.0}}'),
                       ("a rate that is not a number", '{"food": {"gross": 1, "vat": 0, "net": 1}}'),
                       ("a figure that is text", '{"10": {"gross": "11", "vat": 1, "net": 10}}'),
                       ("NaN", '{"10": {"gross": 11, "vat": NaN, "net": 10}}'),
                       ("a list", "[1, 2]"), ("not JSON", "{oops"), ("null", "null")):
        s.check(f"{label} is unreadable", bands(raw) is None, detail=str(bands(raw)))

    may, june = date(2031, 5, 1), date(2031, 6, 1)
    _sealed_day("2031-05-14", [(110.0, 10)])
    _hand_written_closure("2031-05-15", json.dumps({"10": 5.0}))
    # A real close of a night nothing was sold: sealed as {}, and quiet.
    conn = db()
    m.pos_close_period(conn, "day", "2031-05-16")
    conn.commit()
    conn.close()
    w_may = _working(may, june)
    s.check("the unreadable day is named", w_may["unreadable_days"] == ["2031-05-15"],
            detail=f"{w_may['unreadable_days']} — a closure the page cannot read "
                   "must not look like a quiet night")
    s.check("a quiet night is not", "2031-05-16" not in w_may["unreadable_days"])
    s.check("it is still a closed day, not one 'never closed off'",
            w_may["days_closed"] == 3 and "2031-05-15" not in w_may["unclosed_days"],
            detail=f"closed {w_may['days_closed']}, unclosed {w_may['unclosed_days']}")
    s.check("its figure is left out rather than guessed at",
            abs(sum(l["vat"] for l in w_may["lines"] if l["exact"]) - 10.0) < 0.01,
            detail=f"{[l for l in w_may['lines'] if l['exact']]}")
    may_page = oc.get("/admin/vat?period=month&date=2031-05-15").get_data(as_text=True)
    s.check("the page says so", "closed day in this period could not be read" in may_page,
            detail="the warning is what stops a short figure going to the accountant")
    s.check("and links to the day's cash-up", "/pos/day?date=2031-05-15" in may_page)
    may_csv = oc.get("/admin/vat/export.csv?period=month&date=2031-05-15").get_data(as_text=True)
    s.check("and so does the export, which arrives without the page",
            "unreadable" in may_csv and "2031-05-15" in may_csv, detail=may_csv[-300:])

    s.section("VAT comes out of a gross price, not on top of it")
    # Prices here include VAT. times-the-rate would give 110 on 1100 at 10%.
    _room_booking("2031-03-05", 1100.0)
    rooms = next((l for l in _working(start, end)["lines"] if l["source"] == "Rooms"), None)
    s.check("the room line appears", rooms is not None)
    if rooms:
        s.check("gross is what the guest pays", abs(rooms["gross"] - 1100.0) < 0.01)
        s.check("VAT is the gross less the net, so 100 not 110",
                abs(rooms["vat"] - 100.0) < 0.01,
                detail=f"got {rooms['vat']} — times-the-rate charges tax on the tax")
        s.check("and the net is what is left", abs(rooms["net"] - 1000.0) < 0.01)
        s.check("marked as an estimate, because the basis is different",
                rooms["exact"] is False,
                detail="an estimate that does not say so gets filed as a return")

    s.section("Events are in the figure, not just in the rates table")
    # The rates table showed an Events rate while no line used it, which reads
    # as "included" and understated the total.
    conn = db()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, guest_count, status,
           quoted_price, created_at)
           VALUES (?, ?, 'wedding', ?, 'e@example.invalid', '2031-03-20', 40,
                   'confirmed', 2400, ?)""",
        (f"{TAG}-EV", f"tok{TAG}ev", f"{TAG} Wedding",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    ev = next((l for l in _working(start, end)["lines"] if l["source"] == "Events"), None)
    s.check("a confirmed event appears", ev is not None,
            detail="the page shows a rate for events that nothing uses")
    if ev:
        s.check("with VAT out of the quoted price", abs(ev["vat"] - 400.0) < 0.01,
                detail=f"got {ev['vat']} on 2400 at 20%")

    s.section("A refund takes its VAT back out")
    before_refund = _working(start, end)["total_vat"]
    # Money given back is money not earned, and the VAT on it is not owed. A
    # refund never changes a booking's total_price, so without this the figure
    # keeps the tax on a stay that was handed back.
    conn = db()
    booked = conn.execute("SELECT id FROM bookings WHERE guest_name LIKE ?",
                          (TAG + "%",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO refunds (category, booking_id, amount, reason, method, created_at)
           VALUES ('room', ?, 550, ?, 'bank_transfer', '2031-03-22T10:00:00+00:00')""",
        (booked, f"{TAG} guest cancelled"))
    conn.commit()
    conn.close()
    w3 = _working(start, end)
    refund_line = next((l for l in w3["lines"] if l["source"].startswith("Refunded")), None)
    s.check("the refund shows as its own line", refund_line is not None,
            detail=f"{[l['source'] for l in w3['lines']]}")
    if refund_line:
        s.check("as a negative, so it comes off the total", refund_line["vat"] < 0,
                detail=f"got {refund_line['vat']}")
        s.check("at the accommodation rate it was charged at",
                abs(refund_line["vat"] + 50.0) < 0.01,
                detail=f"got {refund_line['vat']} on 550 at 10%")
    # Compare the real before and after. The first version of this line read
    # `w3["total_vat"] < w3["total_vat"] + 50.0`, which is true of any number
    # and tested nothing.
    s.check("and the total drops by exactly that much",
            abs((before_refund - w3["total_vat"]) - 50.0) < 0.01,
            detail=f"{before_refund} -> {w3['total_vat']}")

    s.section("The totals separate what is sealed from what is estimated")
    w2 = _working(start, end)
    # Sealed stays at the till figure; estimated now carries rooms, events and
    # the refund, so assert the relationship rather than a frozen number.
    s.check("the sealed half is only the till", abs(w2["exact_vat"] - 38.0) < 0.01,
            detail=f"sealed {w2['exact_vat']}")
    s.check("and the estimated half is everything else",
            abs(w2["estimated_vat"] - round(sum(
                l["vat"] for l in w2["lines"] if not l["exact"]), 2)) < 0.01,
            detail=f"estimated {w2['estimated_vat']}")
    s.check("and the total is the two together",
            abs(w2["total_vat"] - (w2["exact_vat"] + w2["estimated_vat"])) < 0.01)

    s.section("Taxe de séjour is not on this page at all")
    # Collected for the commune, not earned by the château, and held apart from
    # the room price — so nothing here should touch it.
    s.check("no line mentions it",
            not any("séjour" in l["source"].lower() or "city tax" in l["source"].lower()
                    for l in w2["lines"]))

    s.section("The page says what it is before it says a number")
    page = oc.get("/admin/vat?period=month&date=2031-03-15")
    html = page.get_data(as_text=True)
    s.check("it loads", page.status_code == 200, page)
    s.check("and calls itself a working paper, not a return",
            "working paper" in html and "not your VAT return" in html,
            detail="a page that looks like a return gets filed like one")
    s.check("it names the basis of the estimated half",
            "payment is received" in html or "payment received" in html,
            detail="the accountant needs to know the dates differ")
    s.check("and shows the rates it used", "Accommodation" in html)

    s.section("The caveat travels with the export")
    # The month the fixtures are in, not today's — an empty export would pass
    # the "not a return" check and prove nothing about the line labels.
    csv = oc.get("/admin/vat/export.csv?period=month&date=2031-03-15")
    body = csv.get_data(as_text=True)
    s.check("the CSV downloads", "text/csv" in csv.headers.get("Content-Type", ""),
            detail=csv.headers.get("Content-Type"))
    s.check("with a total row", "TOTAL" in body)
    s.check("and says it is not a return, since a file arrives without the page",
            "not a return" in body, detail=body[-200:])
    s.check("and each estimated line is labelled in the file itself",
            "(estimate)" in body or "estimate" in body, detail=body[:200])

    s.section("Guards")
    s.check("an employee cannot see the VAT figures",
            ec.get("/admin/vat").status_code in (302, 403))
    s.check("nor export them",
            ec.get("/admin/vat/export.csv").status_code in (302, 403))

    _cleanup()
    return s
