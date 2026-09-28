"""The nights the house could have sold, and what each one earned.

Three figures nothing here produced, and one of them is a disagreement with an
existing report rather than an addition to it.

THE DENOMINATOR. report_occupancy divides by every active room times every
night. That is the right answer to "how full was the house" and the wrong one
to "how well did we sell", because a room taken off sale for plaster work and a
week held for an atelier both count as nights the house failed to fill -- so
the number gets WORSE the more restoration happens and better the less. This
counts a night only if a guest could actually have had it, asked of the same
function the booking calendar greys out with, so it cannot drift from what a
guest is really offered.

REVENUE PER AVAILABLE NIGHT. The one an empty night can make worse. An average
nightly rate rises when the house sells one expensive night and nothing else,
which is the exact week it did worst.

WHO IS WORTH MOST, ACROSS EVERYTHING. The house knew what a stay was worth,
what an atelier place cost and what a dinner came to, and had never added them
up for a person. The figure comes from guest_record, which is the ONLY
definition of what somebody has spent -- a ranking that added it up its own way
would be a second one, and the two would agree for a year and then stop.

AND WHAT MADE PEOPLE LOOK, which is not how the booking arrived. A guest who
saw the house on television and then booked direct is a direct booking and is
not a direct-marketing success.
"""
from _harness import Suite, clients, db

from datetime import timedelta

import _harness

m = _harness.m
TAG = "ZZEARN"


def _cleanup(conn):
    conn.execute("DELETE FROM guests WHERE email = ?", ("zzearn@example.invalid",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("what a night earns")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    room = _harness.ensure_room()
    room_id = room["id"]
    now = m.datetime.now(m.timezone.utc).isoformat()

    # A window far enough out that the seeded calendar is quiet.
    start = m.house_today() + timedelta(days=400)
    end = start + timedelta(days=10)

    s.section("A night counts only if a guest could have had it")

    base = m.sellable_nights(conn, start, end, room_id=room_id)["nights"]
    s.check("ten clear nights are ten sellable nights", base == 10, detail=str(base))

    # Off sale. NOT a night the house failed to fill.
    conn.execute(
        """INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (room_id, (start + timedelta(days=2)).isoformat(),
         (start + timedelta(days=4)).isoformat(), TAG + " plaster", now))
    conn.commit()
    after = m.sellable_nights(conn, start, end, room_id=room_id)["nights"]
    s.check("a room taken off sale stops counting against the house",
            after < base,
            detail="%d then %d — otherwise the figure gets worse the more "
                   "restoration happens" % (base, after))

    s.section("A night sold is still a night that was sellable")

    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, guest_phone, arrival_date, departure_date, party_size,
             status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, '', '', ?, ?, 2, 'confirmed', 400, 0, ?)""",
        (room_id, TAG + "-1", "tok" + TAG.lower() + "1", TAG + " Stay",
         start.isoformat(), (start + timedelta(days=2)).isoformat(), now))
    conn.commit()
    with_sale = m.sellable_nights(conn, start, end, room_id=room_id)["nights"]
    # THE ONE THAT INVERTS THE WHOLE REPORT. If a booking came out of the
    # denominator, selling a night would make occupancy fall.
    s.check("selling a night does not remove it from what could be sold",
            with_sale == after,
            detail="%d then %d — a booking is a night SOLD, not a night that "
                   "could not be sold; taking it out would mean every sale "
                   "lowered the figure" % (after, with_sale))

    sold = m.nights_sold(conn, start, end, room_id=room_id)
    s.check("and it is counted as sold", sold["nights"] == 2, detail=str(sold))
    s.check("with the money that came with it", sold["money"] == 400.0, detail=str(sold))

    # A request nobody has accepted. Counting hopes as occupancy is how a
    # house talks itself out of chasing them -- and it would make the revenue
    # figure count money it has not been promised, let alone paid.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, guest_phone, arrival_date, departure_date, party_size,
             status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, '', '', ?, ?, 2, 'pending', 999, 0, ?)""",
        (room_id, TAG + "-P", "tok" + TAG.lower() + "p", TAG + " Hoping",
         (start + timedelta(days=6)).isoformat(),
         (start + timedelta(days=8)).isoformat(), now))
    conn.commit()
    still = m.nights_sold(conn, start, end, room_id=room_id)
    s.check("a request nobody has accepted is not a night sold",
            still["nights"] == 2 and still["money"] == 400.0,
            detail="%s — a pending booking is a hope, and its 999 is money "
                   "nobody has promised" % still)

    s.section("Money is apportioned by night, not by booking")

    # A stay straddling the edge: four nights, two inside the window.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, guest_phone, arrival_date, departure_date, party_size,
             status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, '', '', ?, ?, 2, 'confirmed', 800, 0, ?)""",
        (room_id, TAG + "-2", "tok" + TAG.lower() + "2", TAG + " Straddle",
         (end - timedelta(days=2)).isoformat(),
         (end + timedelta(days=2)).isoformat(), now))
    conn.commit()
    sold = m.nights_sold(conn, start, end, room_id=room_id)
    s.check("only the nights inside the window are counted",
            sold["nights"] == 4, detail=str(sold["nights"]))
    s.check("and only that share of the money",
            abs(sold["money"] - 800.0) < 0.01,
            detail="%s — 400 whole, plus half of 800. Putting the whole total "
                   "on whichever side the arrival falls is how a month gets "
                   "credited with a stay it did not have" % sold["money"])

    s.section("The figure an empty night can make worse")

    rp = m.revenue_per_available_night(conn, start, end)
    s.check("it divides by everything that could have been sold",
            rp["sellable"] >= rp["sold"], detail=str(rp))
    s.check("per available is lower than per sold while anything is empty",
            rp["per_available"] is not None and rp["per_sold"] is not None
            and rp["per_available"] < rp["per_sold"],
            detail="%s vs %s — if these were equal the empty nights would not "
                   "be costing the figure anything, which is the whole reason "
                   "for it" % (rp["per_available"], rp["per_sold"]))

    s.section("What made them look is not how they arrived")

    conn.execute("UPDATE bookings SET heard_via = 'press', source = 'direct' "
                 "WHERE reference_code = ?", (TAG + "-1",))
    conn.commit()
    heard = m.referral_sources(conn, start, end + timedelta(days=5))
    press = [r for r in heard if r["key"] == "press"]
    s.check("a stay that came from the press is filed under the press",
            press and press[0]["stays"] == 1, detail=str(heard))
    s.check("even though the booking itself was direct",
            conn.execute("SELECT source FROM bookings WHERE reference_code = ?",
                         (TAG + "-1",)).fetchone()["source"] == "direct",
            detail="reading one as the other is how a house stops paying for "
                   "the thing that works")
    unknown = [r for r in heard if r["key"] is None]
    s.check("and one nobody asked is its own row, not folded in",
            unknown and unknown[0]["label"] == "Not recorded",
            detail="most stays predate the question; calling those 'other' "
                   "reports a number the house never collected")

    s.section("Who is worth most, by the one definition of spent")

    # Seeded, because the check that matters most here is the one comparing
    # this figure against guest_record's -- and on a database with no guest
    # who has spent anything it sits inside an `if` and never runs. A suite
    # that reports eighteen passes without exercising its own central claim is
    # the shape of green this file exists to avoid.
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (TAG + " Spender", "zzearn@example.invalid", now))
    guest_id = conn.execute("SELECT id FROM guests WHERE email = ?",
                            ("zzearn@example.invalid",)).fetchone()["id"]
    conn.execute("UPDATE bookings SET linked_guest_id = ?, guest_email = ? "
                 "WHERE reference_code IN (?, ?)",
                 (guest_id, "zzearn@example.invalid", TAG + "-1", TAG + "-2"))
    conn.commit()

    values = m.guest_values(conn, limit=10)
    s.check("it returns a ranking", isinstance(values, list))
    s.check("and the guest who spent something is on it",
            any(v["id"] == guest_id for v in values),
            detail="seeded 1200 across two stays; got %s"
                   % [(v["name"], v["spent"]) for v in values[:3]])
    mine = [v for v in values if v["id"] == guest_id]
    if mine:
        top = mine[0]
        record = m.guest_record(conn, top["id"])
        # THE GUARD AGAINST A SECOND DEFINITION OF MONEY.
        s.check("and every figure agrees with the guest's own record",
                abs(top["spent"] - record["spent"]) < 0.01,
                detail="%s vs %s — two definitions of what somebody has spent "
                       "agree for a year and then quietly stop"
                       % (top["spent"], record["spent"]))
        s.check("it is sorted by what they spent",
                all(values[i]["spent"] >= values[i + 1]["spent"]
                    for i in range(len(values) - 1)),
                detail=str([v["spent"] for v in values]))
        s.check("and says why they are there, not only how much",
                all(k in top for k in ("stays", "nights", "called_off", "last_seen")),
                detail="one long expensive stay and nine years of coming back "
                       "are the same number and are not the same guest")

    s.section("The page")

    r = oc.get("/reports/what-a-night-earns")
    s.check("the owner can open it", r.status_code == 200, r)
    body = r.get_data(as_text=True)
    s.check("it says it does not agree with the occupancy report, and why",
            "not meant to agree" in body,
            detail="two occupancy figures on one site with no explanation is "
                   "worse than one")
    s.check("the window can be changed",
            oc.get("/reports/what-a-night-earns?days=365").status_code == 200)
    s.check("and a nonsense window does not crash it",
            oc.get("/reports/what-a-night-earns?days=fish").status_code == 200)
    s.check("an employee cannot", ec.get("/reports/what-a-night-earns").status_code != 200)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
