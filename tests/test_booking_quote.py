"""The price a guest is shown before booking.

The form used to say "€250 / night" and nothing else — no total, even on the
Stripe path whose own wording promised "pay the total now". A guest committing
to four nights had to work out €1,000 for themselves.

The check that matters most here is the last one: what is quoted must equal what
is charged. A quote computed separately from the charge will eventually disagree
with it, and the guest will be right.
"""
from datetime import date, timedelta
from urllib.parse import urlencode

from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZQUOTE"


def run():
    s = Suite("Booking quote")
    clients()                       # ensures the database is seeded
    room = ensure_room()
    pub = m.app.test_client()

    conn = db()
    conn.execute("""UPDATE rooms SET price_per_night = 250, min_nights = 2,
                    max_occupancy = 4 WHERE id = ?""", (room["id"],))
    conn.execute("""INSERT INTO extras (name, price, active, sort_order, category)
                    VALUES (?, 120, 1, 1, 'room')""", (f"{TAG} transfer",))
    conn.commit()
    extra_id = conn.execute("SELECT id FROM extras WHERE name = ?",
                            (f"{TAG} transfer",)).fetchone()["id"]
    conn.close()

    arrival = date.today() + timedelta(days=400)
    departure = arrival + timedelta(days=4)

    def quote(**over):
        params = {"room_id": room["id"], "arrival": arrival.isoformat(),
                  "departure": departure.isoformat()}
        params.update(over)
        r = pub.get("/api/quote?" + urlencode(params, doseq=True))
        return r.status_code, (r.get_json() or {})

    s.section("A stay is priced")
    code, q = quote()
    s.check("the endpoint answers", code == 200, detail=f"HTTP {code}")
    s.check("four nights at 250 is 1000", q.get("total") == 1000.0,
            detail=f"got {q.get('total')}")
    s.check("nights are counted", q.get("nights") == 4, detail=f"got {q.get('nights')}")
    s.check("an average per night is given", q.get("per_night") == 250.0,
            detail=f"got {q.get('per_night')}")
    s.check("the room is an itemised line", len(q.get("lines", [])) == 1)

    s.section("Add-ons are priced")
    code, q = quote(extras=[extra_id])
    s.check("the extra is added", q.get("total") == 1120.0, detail=f"got {q.get('total')}")
    s.check("and shown as its own line", len(q.get("lines", [])) == 2)

    s.section("It says no before the form is filled in, not after")
    code, q = quote(departure=(arrival + timedelta(days=1)).isoformat())
    s.check("a stay under the minimum is refused", q.get("available") is False)
    s.check("and says why", "2-night minimum" in (q.get("reason") or ""),
            detail=f"got {q.get('reason')!r}")

    code, q = quote(party_size=9)
    s.check("too large a party is refused", q.get("available") is False)
    s.check("and says the capacity", "sleeps up to 4" in (q.get("reason") or ""),
            detail=f"got {q.get('reason')!r}")

    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
           departure_date, party_size, status, reference_code, manage_token, created_at)
           VALUES (?,?,?,?,?,2,'confirmed',?,?,datetime('now'))""",
        (room["id"], f"{TAG} blocker", f"{TAG.lower()}block@example.invalid",
         arrival.isoformat(), departure.isoformat(), f"{TAG}B", f"tok{TAG}B"))
    conn.commit()
    conn.close()
    code, q = quote()
    s.check("dates already taken are refused", q.get("available") is False,
            detail=f"got {q.get('reason')!r}")
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code = ?", (f"{TAG}B",))
    conn.commit()
    conn.close()

    s.section("Quoting cannot spend a promo code")
    conn = db()
    try:
        conn.execute(
            """INSERT INTO promo_codes (code, discount_type, discount_value, applies_to,
               active, max_redemptions, redemption_count, created_at)
               VALUES (?, 'percent', 10, 'room', 1, 1, 0, datetime('now'))""",
            (f"{TAG}10",))
        conn.commit()
        seeded = True
    except Exception as e:
        seeded = False
        print(f"       (could not seed a promo code: {e})")
    conn.close()
    if seeded:
        for _ in range(3):
            quote(promo=f"{TAG}10")
        conn = db()
        used = conn.execute(
            "SELECT redemption_count FROM promo_codes WHERE code = ?",
            (f"{TAG}10",)).fetchone()["redemption_count"]
        conn.close()
        # A single-use code must survive being quoted, or looking at the price
        # costs the guest their discount.
        s.check("three quotes redeem it zero times", used == 0, detail=f"{used} redemptions")

    s.section("What is quoted is what is charged")
    later = date.today() + timedelta(days=430)
    code, q = quote(arrival=later.isoformat(),
                    departure=(later + timedelta(days=3)).isoformat(),
                    party_size=2, extras=[extra_id])
    pub.post(f"/book/{room['id']}", data={
        "guest_name": f"{TAG} guest", "guest_email": f"{TAG.lower()}@example.invalid",
        "arrival_date": later.isoformat(),
        "departure_date": (later + timedelta(days=3)).isoformat(),
        "party_size": "2", "agree_terms": "on", "extras": str(extra_id),
    }, follow_redirects=True)
    conn = db()
    booking = conn.execute(
        "SELECT total_price, extras_summary FROM bookings WHERE guest_email = ?",
        (f"{TAG.lower()}@example.invalid",)).fetchone()
    conn.close()
    s.check("a booking was created", booking is not None)
    if booking:
        s.check("the charge equals the quote",
                abs((booking["total_price"] or 0) - q.get("total", 0)) < 0.01,
                detail=f"quoted {q.get('total')}, charged {booking['total_price']}")
        s.check("the add-on reached the booking",
                TAG in (booking["extras_summary"] or ""),
                detail=f"got {booking['extras_summary']!r}")

    s.section("The form shows a price without JavaScript")
    html = pub.get(f"/book/{room['id']}?arrival={arrival.isoformat()}"
                   f"&departure={departure.isoformat()}").get_data(as_text=True)
    s.check("a total is rendered server-side", "€1000.00" in html or "Total" in html)

    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    try:
        conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    except Exception:
        pass
    conn.commit()
    conn.close()
    return s
