"""What a stay costs is what was agreed for those nights, not today's rate card.

booking_bill worked the room line out from the rate card every time it was
asked. So putting the summer rate up moved what a guest who had already PAID
appeared to owe — measured before the fix at €300 outstanding on a €400 stay
that was settled in full — and dropping the rate turned another guest into an
overpayment nobody had made.

Nothing errored. It is worse than a wrong number on one page, because
everything that asks a guest for money reads booking_bill: their own page, the
balance-due reminder that goes out before they travel, the debtors list, the
party statement, the split-bill page, and the Pay button, which would have
taken the difference.

Two things carry this file.

  THE ROOM LINE IS STAMPED, WITH THE NIGHTS IT WAS QUOTED FOR. A figure without
  its dates can be read against a stay it was never quoted for, so the two are
  written together and only used when they still match.

  A DATE CHANGE STILL REPRICES. That was the reason the recompute was there and
  it was a good one — somebody who moves to different nights buys those nights
  at what they cost. Every path that moves a stay re-stamps: the guest changing
  their own dates, the guest adding nights on the end, and the owner editing
  the booking.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPA"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_extras WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_rate_overrides WHERE created_at LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _override(conn, room_id, start, end, price):
    conn.execute(
        """INSERT INTO room_rate_overrides (room_id, start_date, end_date,
           price_per_night, created_at) VALUES (?, ?, ?, ?, ?)""",
        (room_id, start.isoformat(), end.isoformat(), price, TAG + "-stamp"))
    conn.commit()


def _clear_overrides(conn, room_id):
    conn.execute("DELETE FROM room_rate_overrides WHERE room_id = ? AND created_at LIKE ?",
                 (room_id, TAG + "%"))
    conn.commit()


def _bill(booking_id):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.booking_bill(conn, booking_id)
    finally:
        conn.close()


def _row(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("The price that was agreed")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    # ensure_room hands back id and name only, and this suite needs the rate.
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    was_rate = room["price_per_night"]
    conn.execute("UPDATE rooms SET price_per_night = 200 WHERE id = ?", (room["id"],))
    _clear_overrides(conn, room["id"])
    conn.commit()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()

    arrival = m.house_today() + timedelta(days=90)
    departure = arrival + timedelta(days=2)
    try:
        with m.app.test_request_context("/"):
            ref, token = m.create_booking(
                conn, room, f"{TAG} Guest", "zzpa@example.invalid", "",
                arrival, departure, 2, "", [], payment_status="unpaid")
        conn.commit()
        booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                               (ref,)).fetchone()
        conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?",
                     (booking["id"],))
        conn.commit()
        bid = booking["id"]

        s.section("What was agreed is written down")
        after = _row(bid)
        s.check("the room line is stamped",
                after["room_total_quoted"] and abs(after["room_total_quoted"] - 400) < 0.01,
                detail=f"{after['room_total_quoted']} for two nights at 200")
        s.check("and so are the nights it was quoted for",
                after["room_total_quoted_for"] ==
                f"{arrival.isoformat()}|{departure.isoformat()}",
                detail="a figure without its dates can be read against a stay "
                       "it was never quoted for")

        s.section("The house puts its rates up, months later")
        before = _bill(bid)
        conn = db()
        _override(conn, room["id"], arrival, departure + timedelta(days=60), 350)
        conn.close()
        after_bill = _bill(bid)
        # THE MEASUREMENT THAT PROMPTED ALL OF THIS.
        s.check("what the guest owes does not move",
                abs(after_bill["total"] - before["total"]) < 0.01,
                detail=f"{before['total']} then {after_bill['total']} — before "
                       "this a paid-up guest was shown owing 300 euros and the "
                       "balance reminder would have chased them for it")

        s.section("And when it drops them")
        conn = db()
        _clear_overrides(conn, room["id"])
        _override(conn, room["id"], arrival, departure + timedelta(days=60), 120)
        conn.close()
        cheaper = _bill(bid)
        s.check("it does not move that way either",
                abs(cheaper["total"] - before["total"]) < 0.01,
                detail=f"{cheaper['total']} — an overpayment the guest never "
                       "made is a refund conversation nobody asked for")
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.close()

        s.section("A guest who moves to other nights pays for those nights")
        # The reason the recompute was there, and it was a good one.
        moved_arrival = arrival + timedelta(days=120)
        moved_departure = moved_arrival + timedelta(days=2)
        conn = db()
        _override(conn, room["id"], moved_arrival, moved_departure, 350)
        conn.close()
        oc.post(f"/admin/bookings/{bid}/edit", data={
            "arrival_date": moved_arrival.isoformat(),
            "departure_date": moved_departure.isoformat(),
            "party_size": "2", "guest_phone": "", "special_requests": "",
            "source": "direct", "reference_code": ref,
        }, follow_redirects=True)
        moved = _bill(bid)
        s.check("the new nights are priced at what they cost",
                abs(moved["total"] - before["total"]) > 1,
                detail=f"{moved['total']} against {before['total']} — moving "
                       "into a peak week has to cost peak money")
        s.check("and the stamp follows them",
                _row(bid)["room_total_quoted_for"] ==
                f"{moved_arrival.isoformat()}|{moved_departure.isoformat()}",
                detail=f"{_row(bid)['room_total_quoted_for']}")
        s.check("so the new price holds too",
                abs(_bill(bid)["total"] - moved["total"]) < 0.01)
        # And a rate change after the move must not move it again.
        conn = db()
        _clear_overrides(conn, room["id"])
        _override(conn, room["id"], moved_arrival, moved_departure, 500)
        conn.close()
        s.check("a later rate change still does not",
                abs(_bill(bid)["total"] - moved["total"]) < 0.01,
                detail=f"{_bill(bid)['total']} against {moved['total']}")
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.close()

        s.section("A guest adding nights on the end")
        conn = db()
        stay = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
        conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (bid,))
        conn.commit()
        conn.close()
        held = _bill(bid)["total"]
        resp = oc.post(f"/book/manage/{stay['manage_token']}",
                       data={"action": "add_nights", "nights": "1"},
                       follow_redirects=True)
        extended = _row(bid)
        s.check("the extra night is added to the stay",
                extended["departure_date"] > stay["departure_date"],
                detail=f"{stay['departure_date']} to {extended['departure_date']}"
                       f" (HTTP {resp.status_code})")
        s.check("and the stamp covers the longer stay",
                extended["room_total_quoted_for"] ==
                f"{extended['arrival_date']}|{extended['departure_date']}",
                detail=f"{extended['room_total_quoted_for']} — a stamp left on "
                       "the old departure date does not match, and a stamp "
                       "that does not match falls back to the rate card")
        longer = _bill(bid)["total"]
        s.check("the guest is charged for it",
                longer > held + 1, detail=f"{held} then {longer}")
        conn = db()
        _override(conn, room["id"], moved_arrival, moved_departure + timedelta(days=5), 500)
        conn.close()
        s.check("and the longer stay holds its price too",
                abs(_bill(bid)["total"] - longer) < 0.01,
                detail=f"{_bill(bid)['total']} against {longer}")
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.close()

        s.section("An extra survives a date change over a moved rate")
        # The other half of the same fault, and the one that eats money
        # quietly. Both date-change paths work the extras out by subtracting
        # the room portion from the total. Subtract a FRESHLY QUOTED room
        # portion and a rate that has moved since is taken straight out of the
        # extras, so a 90 euro transfer silently becomes -210 and the guest is
        # undercharged by the difference.
        conn = db()
        with m.app.test_request_context("/"):
            ref2, _t2 = m.create_booking(
                conn, room, f"{TAG} Extras", "zzpa.extras@example.invalid", "",
                arrival, departure, 2, "", [], payment_status="unpaid")
        conn.commit()
        eid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (ref2,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO booking_extras (booking_id, category, name, unit_price,
               quantity, status, created_at) VALUES (?, 'room', 'Airport transfer',
               90, 1, 'confirmed', ?)""",
            (eid, datetime.now(timezone.utc).isoformat()))
        conn.execute("UPDATE bookings SET status = 'confirmed', total_price = ? WHERE id = ?",
                     (490, eid))
        # The rate over their ORIGINAL nights moves after they booked.
        _override(conn, room["id"], arrival, departure, 350)
        conn.commit()
        conn.close()
        # Somewhere cheaper, so the figure after the move is unmistakably the
        # new nights and not a leftover.
        elsewhere = arrival + timedelta(days=200)
        conn = db()
        _override(conn, room["id"], elsewhere, elsewhere + timedelta(days=2), 100)
        conn.close()
        oc.post(f"/admin/bookings/{eid}/edit", data={
            "arrival_date": elsewhere.isoformat(),
            "departure_date": (elsewhere + timedelta(days=2)).isoformat(),
            "party_size": "2", "guest_phone": "", "special_requests": "",
            "source": "direct", "reference_code": ref2,
        }, follow_redirects=True)
        after_move = _bill(eid)
        moved_row = _row(eid)
        s.check("the stay actually moved",
                moved_row["arrival_date"] == elsewhere.isoformat(),
                detail=f"{moved_row['arrival_date']}")
        transfer = [l for l in after_move["lines"] if "transfer" in l["label"].lower()]
        s.check("the extra is still on the bill", transfer,
                detail=f"{[l['label'] for l in after_move['lines']]}")
        # ON total_price, NOT on the bill. booking_bill builds itself from the
        # stamp and the extras rows, so it cannot see this: the damage is to
        # the stored total, which is what the payment schedule and the card
        # page charge from.
        s.check("and the stored total is the new nights plus that extra",
                abs((moved_row["total_price"] or 0) - 290) < 0.01,
                detail=f"{moved_row['total_price']} — 200 for two nights at "
                       "100, plus a 90 euro transfer. Backing the extras out "
                       "of a re-quoted room portion charges the raised rate "
                       "against the extra and gives -10")
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.close()

        s.section("And when the guest moves their own dates")
        # The same arithmetic lives on the guest side of the manage page, for
        # a request nobody has confirmed yet. Two copies of a sum is two
        # chances for one of them to be missed, which is why both are checked.
        conn = db()
        with m.app.test_request_context("/"):
            ref3, tok3 = m.create_booking(
                conn, room, f"{TAG} Own", "zzpa.own@example.invalid", "",
                arrival, departure, 2, "", [], payment_status="unpaid")
        conn.commit()
        oid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (ref3,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO booking_extras (booking_id, category, name, unit_price,
               quantity, status, created_at) VALUES (?, 'room', 'Airport transfer',
               90, 1, 'confirmed', ?)""",
            (oid, datetime.now(timezone.utc).isoformat()))
        conn.execute("UPDATE bookings SET total_price = ? WHERE id = ?", (490, oid))
        _override(conn, room["id"], arrival, departure, 350)
        conn.commit()
        conn.close()
        their_dates = arrival + timedelta(days=250)
        conn = db()
        _override(conn, room["id"], their_dates, their_dates + timedelta(days=2), 100)
        conn.close()
        oc.post(f"/book/manage/{tok3}", data={
            "action": "change_dates",
            "new_arrival_date": their_dates.isoformat(),
            "new_departure_date": (their_dates + timedelta(days=2)).isoformat(),
        }, follow_redirects=True)
        own = _row(oid)
        s.check("their stay moves", own["arrival_date"] == their_dates.isoformat(),
                detail=f"{own['arrival_date']}")
        s.check("and their total is the new nights plus the extra",
                abs((own["total_price"] or 0) - 290) < 0.01,
                detail=f"{own['total_price']} — the same sum as the owner side, "
                       "and the same way of getting it wrong")
        s.check("with the stamp moved to match",
                own["room_total_quoted_for"] ==
                f"{their_dates.isoformat()}|{(their_dates + timedelta(days=2)).isoformat()}",
                detail=f"{own['room_total_quoted_for']}")
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.close()

        s.section("A stay taken before any of this existed")
        # The backfill. Everything already on the books was exposed, and
        # total_price is what those guests agreed to.
        conn = db()
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
               guest_name, guest_email, guest_phone, arrival_date, departure_date,
               party_size, status, payment_status, total_price, amount_paid,
               created_at) VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed',
               'paid', 400, 400, ?)""",
            (room["id"], f"{TAG}-OLD", f"tok{TAG}old".lower(), f"{TAG} Old Guest",
             "zzpa.old@example.invalid", arrival.isoformat(), departure.isoformat(),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        old_id = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                              (f"{TAG}-OLD",)).fetchone()["id"]
        # Exactly the statement the migration runs.
        conn.execute(
            """UPDATE bookings SET
                 room_total_quoted = ROUND(COALESCE(total_price, 0)
                     + COALESCE(discount_amount, 0), 2),
                 room_total_quoted_for = arrival_date || '|' || departure_date
               WHERE id = ? AND room_total_quoted IS NULL""", (old_id,))
        conn.commit()
        conn.close()
        s.check("it is given the price it was sold at",
                abs(_row(old_id)["room_total_quoted"] - 400) < 0.01,
                detail=f"{_row(old_id)['room_total_quoted']}")
        held_old = _bill(old_id)["total"]
        conn = db()
        _override(conn, room["id"], arrival, departure, 350)
        conn.close()
        s.check("and it holds against a rate change like any other",
                abs(_bill(old_id)["total"] - held_old) < 0.01,
                detail=f"{_bill(old_id)['total']} against {held_old}")
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.close()

        s.section("A stamp for other nights is not trusted")
        conn = db()
        conn.execute(
            """UPDATE bookings SET room_total_quoted = 99999,
               room_total_quoted_for = '1999-01-01|1999-01-02' WHERE id = ?""",
            (old_id,))
        conn.commit()
        conn.close()
        s.check("it falls back to the rate card rather than quoting it",
                _bill(old_id)["total"] < 9999,
                detail=f"{_bill(old_id)['total']} — a figure quoted for other "
                       "nights is not a price for these ones")
    finally:
        conn = db()
        _clear_overrides(conn, room["id"])
        conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?",
                     (was_rate, room["id"]))
        conn.commit()
        conn.close()
        _cleanup()
    return s
