"""What a paid room booking is recorded as costing.

A Stripe Checkout session is created with the price the guest is looking at.
The webhook that records the booking can fire seconds later or minutes later,
and it used to recompute the total from the room's current prices instead of
using the figures the card was actually charged against. Anything that moved
in between — a nightly rate edited in the admin, a promo code hitting its
redemption cap on somebody else's booking — silently stored a total the guest
never agreed to, against a completed charge for the old one.

The restaurant path was fixed for this; the room path had the identical shape
and was left. This drives the room path with the price deliberately changed
underneath it, which is the only way to tell a stored figure from a
recomputed one — they are identical until something moves.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZDRIFT"
SOON = (m.house_today() + timedelta(days=520)).isoformat()
SOON_END = (m.house_today() + timedelta(days=522)).isoformat()


def _session(room_id, quoted_total, discount="0.00"):
    """A completed Checkout Session as the webhook path receives it."""
    return {
        "id": f"cs_test_{TAG}_{secrets_suffix()}",
        "payment_intent": f"pi_test_{TAG}",
        "amount_total": int(round(float(quoted_total) * 100)),
        "metadata": {
            "room_id": str(room_id),
            "guest_name": f"{TAG} Guest",
            "guest_email": f"{TAG.lower()}@example.invalid",
            "guest_phone": "",
            "arrival_date": SOON,
            "departure_date": SOON_END,
            "party_size": "2",
            "special_requests": "",
            "extra_ids": "",
            "promo_code": "",
            "total_price": quoted_total,
            "discount_amount": discount,
        },
    }


_n = [0]


def secrets_suffix():
    _n[0] += 1
    return str(_n[0])


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Stripe price drift")
    _cleanup()
    room = ensure_room()

    conn = db()
    original = conn.execute(
        "SELECT price_per_night FROM rooms WHERE id = ?", (room["id"],)).fetchone()["price_per_night"]
    conn.close()

    s.section("The price the guest was charged is the price recorded")
    # Quote at the real price, then move the rate before the webhook fires.
    quoted = m.app.test_request_context()
    with quoted:
        conn = db()
        full_room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
        nights = 2
        quoted_total = round(m.compute_room_total(
            conn, full_room, m.parse_date(SOON), m.parse_date(SOON_END)), 2)
        conn.close()

        # The owner edits the nightly rate while the guest is on Stripe's page.
        conn = db()
        conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?",
                     (original + 500, room["id"]))
        conn.commit()
        conn.close()

        conn = db()
        m.create_booking_from_stripe_session(conn, _session(room["id"], f"{quoted_total:.2f}"))
        conn.commit()
        row = conn.execute(
            "SELECT total_price FROM bookings WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1",
            (TAG + "%",)).fetchone()
        conn.close()

    s.check("the stored total is what was quoted and charged",
            row is not None and abs(row["total_price"] - quoted_total) < 0.01,
            detail=f"stored {row['total_price'] if row else 'no row'}, quoted {quoted_total}")
    s.check("it is NOT the recomputed higher price",
            row is not None and row["total_price"] < quoted_total + 400,
            detail=f"stored {row['total_price'] if row else 'no row'}")

    # Restore the rate before the next case.
    conn = db()
    conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?", (original, room["id"]))
    conn.commit()
    conn.close()
    _cleanup()

    s.section("A discount is stored as charged, not re-validated")
    with m.app.test_request_context():
        conn = db()
        m.create_booking_from_stripe_session(
            conn, _session(room["id"], "300.00", discount="50.00"))
        conn.commit()
        row2 = conn.execute(
            """SELECT total_price, discount_amount FROM bookings
               WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1""", (TAG + "%",)).fetchone()
        conn.close()
    s.check("the charged total is stored", row2 and abs(row2["total_price"] - 300.00) < 0.01,
            detail=f"got {row2['total_price'] if row2 else 'no row'}")
    s.check("the discount it was charged with is stored too",
            row2 and abs((row2["discount_amount"] or 0) - 50.00) < 0.01,
            detail=f"got {row2['discount_amount'] if row2 else 'no row'}")

    s.section("Old sessions without the figures still work")
    with m.app.test_request_context():
        legacy = _session(room["id"], "0.00")
        del legacy["metadata"]["total_price"]
        del legacy["metadata"]["discount_amount"]
        conn = db()
        m.create_booking_from_stripe_session(conn, legacy)
        conn.commit()
        row3 = conn.execute(
            "SELECT total_price FROM bookings WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1",
            (TAG + "%",)).fetchone()
        conn.close()
    s.check("a pre-existing session falls back to recomputing, not to zero or a crash",
            row3 is not None and (row3["total_price"] or 0) > 0,
            detail=f"got {row3['total_price'] if row3 else 'no row'}")

    _cleanup()
    return s
