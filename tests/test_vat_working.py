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
    conn.execute("DELETE FROM refunds WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _sealed_day(day, vat_by_rate, taken=0.0):
    """A closed day with its VAT already sealed, as the till leaves it."""
    conn = db()
    conn.execute(
        """INSERT INTO pos_closures (kind, period, gross_total, discount_total,
           service_total, taken_total, vat_json, by_method_json, ticket_count,
           covers, perpetual_total, prev_hash, hash, closed_at)
           VALUES ('day', ?, ?, 0, 0, ?, ?, '{}', 1, 2, 0, 'x', ?, ?)""",
        (day, taken, taken, json.dumps(vat_by_rate), f"{TAG}{day}",
         datetime.now(timezone.utc).isoformat()))
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


def run():
    s = Suite("VAT working paper")
    _cleanup()
    oc, ec, owner, emp = clients()

    # A quiet future month, so nothing else in the database lands in it.
    start, end = date(2031, 3, 1), date(2031, 4, 1)
    nxt_start, nxt_end = date(2031, 4, 1), date(2031, 5, 1)

    s.section("The till figures are read from the sealed closures, per rate")
    _sealed_day("2031-03-10", {"10": 24.0, "20": 8.0}, taken=300.0)
    _sealed_day("2031-03-11", {"10": 6.0}, taken=70.0)
    w = _working(start, end)
    rates = {l["source"]: l["vat"] for l in w["lines"] if "till" in l["source"]}
    s.check("food and alcohol are kept apart", len(rates) == 2, detail=f"{rates}")
    s.check("the 10% band adds up across days",
            any(abs(v - 30.0) < 0.01 for v in rates.values()), detail=f"{rates}")
    s.check("and the 20% band", any(abs(v - 8.0) < 0.01 for v in rates.values()),
            detail=f"{rates}")
    s.check("they are marked exact, not estimated",
            all(l["exact"] for l in w["lines"] if "till" in l["source"]))

    s.section("A day nobody closed is reported, not quietly missing")
    s.check("31 days in March", w["days_in_period"] == 31, detail=f"{w['days_in_period']}")
    s.check("two of them closed", w["days_closed"] == 2)
    s.check("and the other 29 are counted as not closed", w["days_not_closed"] == 29,
            detail=f"{w['days_not_closed']} — a short figure that does not say "
                   "it is short is the worst kind")

    s.section("The boundary day belongs to one period, not both")
    # An inclusive end put 1 April in March AND in April.
    _sealed_day("2031-04-01", {"10": 100.0}, taken=1100.0)
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
