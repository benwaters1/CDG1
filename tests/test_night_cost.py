"""What a night actually costs to sell.

room_economics said how many nights each room sold, what it earned and how
full it was. Nothing anywhere said what a night COST — and this app is the
only thing that could, because it alone holds the clocked hours, the stock
consumed and the standing costs together. Pennylane has the invoices and no
idea how many rooms were occupied on the eleventh.

Without it, "should I take this at 180?" gets answered by feel.

THE SPLIT IS THE FEATURE, not the total, because the three kinds behave
completely differently when the house fills up:

  - STANDING happens whether anybody comes.
  - LABOUR is rostered, so it moves with expected trade rather than with
    each arrival.
  - CONSUMED is the only genuinely per-night part.

A single blended figure would answer the pricing question WRONG, and
expensively: it says a night below the average loses money, when in fact
everything above what was consumed leaves the house better off than an
empty room. Getting that backwards means turning away business all winter.

And a window with no nights sold gives None, not zero and not a division by
nothing — a cost per night when nothing sold is a question nobody asked.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZNIGHT"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("What a night costs")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    # Everything else in the database is measured too, so the checks below
    # measure the CONTRIBUTION of what this suite adds rather than the
    # house's own totals — which is the lesson from a day of fixture
    # collisions.
    before = m.night_cost(conn, months=3)

    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()
    if not room:
        s.check("a room exists to sell", False,
                detail="no rooms in the database, so nothing could be sold")
        conn.close()
        return s

    # Ten nights sold inside the window.
    arrival = today - timedelta(days=20)
    conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
                                 departure_date, party_size, status, manage_token,
                                 reference_code, created_at)
           VALUES (?, ?, 'n@example.invalid', ?, ?, 2, 'confirmed', ?, ?, ?)""",
        (room["id"], TAG + " Guest", arrival.isoformat(),
         (arrival + timedelta(days=10)).isoformat(),
         "tok-" + TAG.lower(), TAG + "REF", now))

    # A standing cost, and something consumed.
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category,
                                        active, created_at)
           VALUES (?, 300, 'monthly', 'other', 1, ?)""", (TAG + " Insurance", now))
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level,
                                    unit_cost, active, created_at)
           VALUES (?, 'other', 'each', 0, 5.0, 1, ?)""", (TAG + " Soap", now))
    soap = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    m.record_stock_movement(conn, soap, 100, "opening", note="test")
    m.record_stock_movement(conn, soap, -20, "sale", unit_cost=5.0, note="test")
    conn.commit()

    after = m.night_cost(conn, months=3)

    s.section("Nights are counted inside the window")
    s.check("ten nights sold shows as ten more",
            after["nights"] - before["nights"] == 10,
            detail=f"{before['nights']} -> {after['nights']}")

    s.section("The three kinds are kept apart")
    s.check("a standing cost lands in standing",
            after["standing"] > before["standing"],
            detail=f"{before['standing']} -> {after['standing']}")
    s.check("and stock that went out of the door lands in consumed",
            round(after["consumed"] - before["consumed"], 2) == 100.0,
            detail=f"twenty at five euros; {before['consumed']} -> "
                   f"{after['consumed']}")
    s.check("the insurance did not land in consumed",
            round(after["consumed"] - before["consumed"], 2) == 100.0,
            detail="a standing cost in the per-night figure would answer the "
                   "pricing question wrong")
    s.check("and the three add up to the total",
            round(after["standing"] + after["labour"] + after["consumed"], 2)
            == after["total"],
            detail=f"{after['standing']} + {after['labour']} + "
                   f"{after['consumed']} against {after['total']}")

    s.section("Only what was consumed is avoidable by not selling")
    # The check that stops the whole thing being a blended average, which
    # would say a cheap night loses money when it does not.
    s.check("the avoidable figure is the consumed one",
            after["avoidable_per_night"] == after["consumed_per_night"],
            detail=f"avoidable {after['avoidable_per_night']}, consumed "
                   f"{after['consumed_per_night']}")
    s.check("and it is smaller than the whole cost of a night",
            after["avoidable_per_night"] < after["per_night"],
            detail=f"{after['avoidable_per_night']} against "
                   f"{after['per_night']} — treating the larger figure as the "
                   "floor is how a house turns away business all winter")

    s.section("Stock bought in is not stock consumed")
    # A delivery is a movement too. Counting it would make buying look like
    # spending twice and make a well-stocked month look expensive.
    consumed_before = m.night_cost(conn, months=3)["consumed"]
    m.record_stock_movement(conn, soap, 50, "purchase", unit_cost=5.0, note="test")
    conn.commit()
    s.check("a delivery does not raise the cost of a night",
            m.night_cost(conn, months=3)["consumed"] == consumed_before,
            detail="only movements OUT count")

    s.section("Stock that came back comes off the cost")
    # cancel_booking_extra puts stock back as a POSITIVE 'correction'
    # rather than by editing the sale, because the ledger is append-only.
    # Counting the sale and ignoring the reversal left a cancelled case of
    # wine in the cost of a night forever.
    before_reversal = m.night_cost(conn, months=3)["consumed"]
    m.record_stock_movement(conn, soap, 20, "correction", unit_cost=5.0,
                            note="cancelled: test")
    conn.commit()
    s.check("a reversal takes it back off",
            round(before_reversal - m.night_cost(conn, months=3)["consumed"], 2)
            == 100.0,
            detail=f"{before_reversal} -> "
                   f"{m.night_cost(conn, months=3)['consumed']}")

    s.section("A window with nothing sold divides by nothing")
    # A named window a decade before the app existed, rather than "the last
    # month with my booking deleted". The second relied on the whole
    # database being quiet, which is true when this suite runs alone and
    # false when another suite's fixtures are alive.
    empty = m.night_cost(conn, start=date(2015, 1, 1), end=date(2015, 2, 1))
    s.check("no nights were sold in it", empty["nights"] == 0,
            detail=str(empty["nights"]))
    s.check("so the per-night figures are absent, not zero",
            empty["per_night"] is None and empty["avoidable_per_night"] is None,
            detail=f"{empty['per_night']} — zero would read as free, and a "
                   "house that sold nothing did not have a free month")
    s.check("but the costs are still reported",
            empty["standing"] >= 0 and empty["total"] >= 0,
            detail="they happened whether anybody came")

    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()

    s.section("The page")
    r = oc.get("/management/night-cost")
    body = r.get_data(as_text=True)
    s.check("it opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    # A fragment short enough that the template cannot wrap it. Matching
    # prose across a line break is a check that fails for a reason nothing
    # to do with the feature, which is how this one failed first time.
    s.check("and says which part is actually avoidable",
            "Not selling a room saves" in body or "nothing to divide by" in body,
            detail="the sentence the whole page exists to be able to say")
    s.check("a window can be chosen",
            oc.get("/management/night-cost?months=12").status_code == 200)
    s.check("and junk in it does not break the page",
            oc.get("/management/night-cost?months=abc").status_code == 200)

    r = ec.get("/management/night-cost", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
