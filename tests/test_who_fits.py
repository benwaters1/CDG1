"""Who fits a room: the most guests, the most adults, the most children.

18 October 2026, the owner, with Booking.com's own room set-up on screen: "a
guest can do 2 adults 1 children in a room but not 3 adults ... a room overall
total ... they can add 1 adult 1 child but its maximum guests". Three limits,
each its own. max_occupancy had been the adults and children added up, so a
room for three that takes two adults and two children could not be said, and
three adults could book it.

WHAT THIS PINS.
  - room_party_problem: 2 + 1 fits a 3/2/2 room; 3 adults does not; 2 + 2
    does not; a room that takes no children refuses one; a child alone is
    refused.
  - The quote, the booking form's POST and the desk page all refuse what it
    refuses, and say which limit.
  - The room form saves the most guests as its own number, never more than
    the two limits allow and never fewer than the adults.
  - The booking form draws the three limits for the script that holds the
    fields in, and the hint says them.
  - The six rooms carry Booking.com's figures, once.
"""
from datetime import timedelta

from _harness import BOOKING_DETAILS, Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZFITS"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ? OR room_id IN "
                 "(SELECT id FROM rooms WHERE name LIKE ?)", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _room(conn, name, guests, adults, children):
    conn.execute("""INSERT INTO rooms (name, export_token, active, max_occupancy, max_adults,
                    max_children, price_per_night, min_nights, sort_order)
                    VALUES (?, ?, 1, ?, ?, ?, 200, 1, 95)""",
                 (f"{TAG} {name}", f"tok-{TAG.lower()}-{name}", guests, adults, children))
    conn.commit()
    return conn.execute("SELECT * FROM rooms WHERE name = ?", (f"{TAG} {name}",)).fetchone()


def run():
    s = Suite("Who fits a room")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    twin = _room(conn, "Ground", 3, 2, 2)      # The Twin Room (Ground Floor)
    suite = _room(conn, "Suite", 2, 2, 1)      # The King Suite
    none = _room(conn, "Adults", 2, 2, 0)

    s.section("The three limits, each its own")
    fit = m.room_party_problem
    s.check("two adults and a child fit a room for three taking two adults", fit(twin, 3, 1) is None)
    s.check("three adults do not", (fit(twin, 3, 0) or "").startswith("This room takes up to 2 adults"),
            detail=str(fit(twin, 3, 0)))
    s.check("two adults and two children do not: the room's total", fit(twin, 4, 2) == "This room sleeps up to 3.",
            detail=str(fit(twin, 4, 2)))
    s.check("one adult and two children do", fit(twin, 3, 2) is None)
    s.check("one adult and one child fit a room for two taking one child", fit(suite, 2, 1) is None)
    s.check("but not two adults and a child: most guests", fit(suite, 3, 1) is not None)
    s.check("a room with no children refuses one", fit(none, 2, 1) == "This room does not take children.",
            detail=str(fit(none, 2, 1)))
    s.check("a child alone is refused", fit(twin, 1, 1) is not None)

    s.section("The quote and the booking say the same")
    a = house_today() + timedelta(days=200)
    while not m.is_range_available(conn, twin["id"], a, a + timedelta(days=2))[0]:
        a += timedelta(days=7)
    b = a + timedelta(days=2)
    anon = m.app.test_client()

    def quote(party, under):
        return anon.get(f"/api/quote?room_id={twin['id']}&arrival={a.isoformat()}&departure="
                        f"{b.isoformat()}&party_size={party}&guests_under_18={under}").get_json() or {}
    s.check("the quote takes two adults and a child", quote(3, 1).get("available") is True,
            detail=str(quote(3, 1).get("reason")))
    q = quote(3, 0)
    s.check("and refuses three adults, saying so",
            q.get("available") is False and "2 adults" in (q.get("reason") or ""), detail=str(q.get("reason")))

    def book(name, party, under):
        anon.post(f"/book/{twin['id']}", data={
            "arrival_date": a.isoformat(), "departure_date": b.isoformat(),
            "guest_name": f"{TAG} {name}", "guest_email": f"zzfits.{name.lower()}@example.invalid",
            "guest_phone": "+33 6 12 34 56 78", "party_size": str(party), "adults": str(party - under),
            "guests_under_18": str(under), "special_requests": "", "agree_terms": "on",
            **BOOKING_DETAILS})
        return conn.execute("SELECT COUNT(*) FROM bookings WHERE guest_name = ?",
                            (f"{TAG} {name}",)).fetchone()[0]
    s.check("the booking form refuses three adults", book("Three", 3, 0) == 0)
    s.check("and takes two adults and a child", book("Family", 3, 1) == 1)

    owner = clients()[0]
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    r = owner.post("/admin/bookings/walk-in", data={
        "mode": "book", "room_id": str(twin["id"]), "arrival_date": a.isoformat(),
        "departure_date": b.isoformat(), "guest_name": f"{TAG} Desk", "party_size": "3",
        "guests_under_18": "0"}, follow_redirects=True)
    s.check("the desk is held to it too",
            conn.execute("SELECT COUNT(*) FROM bookings WHERE guest_name = ?",
                         (f"{TAG} Desk",)).fetchone()[0] == 0
            and "2 adults" in r.get_data(as_text=True))

    s.section("The room form keeps the most guests as its own number")
    page = owner.get(f"/admin/rooms/{twin['id']}/edit")
    s.check("the form asks for it", page.status_code == 200
            and 'name="max_occupancy"' in page.get_data(as_text=True))
    form = {"name": twin["name"], "max_adults": "2", "max_children": "2", "max_occupancy": "3",
            "price_per_night": "200", "min_nights": "1", "active": "1"}
    owner.post(f"/admin/rooms/{twin['id']}/edit", data=form)
    row = conn.execute("SELECT max_occupancy, max_adults, max_children FROM rooms WHERE id = ?",
                       (twin["id"],)).fetchone()
    s.check("3, 2 and 2 are saved as 3, 2 and 2, not 4", tuple(row) == (3, 2, 2), detail=str(tuple(row)))
    owner.post(f"/admin/rooms/{twin['id']}/edit", data=dict(form, max_occupancy="9"))
    row = conn.execute("SELECT max_occupancy FROM rooms WHERE id = ?", (twin["id"],)).fetchone()
    s.check("more than the two limits allow comes down to them", row[0] == 4, detail=str(row[0]))
    owner.post(f"/admin/rooms/{twin['id']}/edit", data=dict(form, max_occupancy="1"))
    row = conn.execute("SELECT max_occupancy FROM rooms WHERE id = ?", (twin["id"],)).fetchone()
    s.check("and fewer than the adults goes up to them", row[0] == 2, detail=str(row[0]))
    conn.execute("UPDATE rooms SET max_occupancy = 3, max_adults = 2, max_children = 2, active = 1 "
                 "WHERE id = ?", (twin["id"],))
    conn.commit()

    s.section("The booking form holds the fields in")
    page = anon.get(f"/book/{twin['id']}").get_data(as_text=True)
    s.check("it carries the three limits for the script",
            'data-max-guests="3" data-max-adults="2" data-max-children="2"' in page)
    s.check("and says them", "Up to 3 guests, with no more than 2 adults and no more than 2 children" in page)
    page = anon.get(f"/book/{none['id']}").get_data(as_text=True)
    s.check("a room that takes no children asks only for adults",
            'name="guests_under_18" value="0" data-party-children' in page
            and "no children in this room" in page)

    s.section("Booking.com's figures, on the six rooms")
    s.check("as the extranet has them", m.ROOM_OCCUPANCY_2026_10_18 == {
        "The King Suite": (2, 2, 1), "The Marquis Suite": (2, 2, 1),
        "The Family Suite": (5, 2, 3), "The King Room": (2, 2, 1),
        "The Twin Room": (2, 2, 1), "The Twin Room (Ground Floor)": (3, 2, 2)})
    s.check("applied once", conn.execute(
        "SELECT 1 FROM app_settings WHERE key = 'room_occupancy_2026_10_18'").fetchone() is not None)


if __name__ == "__main__":
    print(run().report())
