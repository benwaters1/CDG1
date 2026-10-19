"""Add a booking: the desk page, rebuilt (owner, 19 October 2026: "it doesn't
look nice, it's difficult to use, it's not clear -- improve it all").

WHAT THIS PINS.
  - The three ways are on the page as a choice, and the summary says what
    pressing the button does for each.
  - The rooms answer for the dates AND the party: a room too small for it is
    out, saying why, and the taxe de sejour is worked out for that party --
    asked of the same functions the booking itself uses.
  - The adults box is what the server counts, so a page used without script
    cannot book a different party from the one on screen.
  - "Book it and send a payment link" needs an address, and refuses before
    anything is written.
  - Whatever was done, the desk lands on the booking itself.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZADDB"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Add a booking")
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn):
    owner, employee = clients()[0], clients()[1]
    conn.execute("""INSERT INTO rooms (name, export_token, active, max_occupancy, max_adults,
                    max_children, price_per_night, min_nights, sort_order)
                    VALUES (?, 'tok-zzaddb-small', 1, 2, 2, 0, 150, 1, 97)""", (f"{TAG} Small",))
    conn.execute("""INSERT INTO rooms (name, export_token, active, max_occupancy, max_adults,
                    max_children, price_per_night, min_nights, sort_order)
                    VALUES (?, 'tok-zzaddb-big', 1, 4, 2, 2, 210, 1, 98)""", (f"{TAG} Big",))
    conn.commit()
    small = conn.execute("SELECT * FROM rooms WHERE name = ?", (f"{TAG} Small",)).fetchone()
    big = conn.execute("SELECT * FROM rooms WHERE name = ?", (f"{TAG} Big",)).fetchone()
    a = house_today() + timedelta(days=240)
    while not all(m.is_range_available(conn, r["id"], a, a + timedelta(days=2))[0] for r in (small, big)):
        a += timedelta(days=3)
    b = a + timedelta(days=2)

    s.section("The page")
    page = owner.get(f"/admin/bookings/walk-in?arrival_date={a}&departure_date={b}").get_data(as_text=True)
    s.check("the three ways are a choice",
            all(f'name="mode" value="{v}"' in page for v in ("book", "paylink", "reserve")))
    s.check("in five steps, with a summary", page.count('class="nb-step"') == 5 and "When you press the button" in page)
    s.check("what each way sends is said before the button",
            "They are emailed a link to add their details and pay." in page
            and "their bill with a Pay now button" in page)
    s.check("staff without the owner's rights are turned away",
            employee.get("/admin/bookings/walk-in").status_code in (302, 403))

    s.section("The rooms answer for the dates and the party")
    q = owner.get(f"/admin/bookings/walk-in/options.json?arrival_date={a}&departure_date={b}"
                  "&party_size=3&guests_under_18=1").get_json() or {}
    by = {r["id"]: r for r in q.get("rooms", [])}
    s.check("two nights", q.get("nights") == 2)
    s.check("a room for two is out for three, saying so",
            small["id"] in by and by[small["id"]]["free"] and not by[small["id"]]["fits"]
            and "sleeps up to 2" in by[small["id"]]["why"], detail=str(by.get(small["id"])))
    s.check("a room for four takes two adults and a child",
            big["id"] in by and by[big["id"]]["fits"] and by[big["id"]]["free"])
    rack = m.compute_room_total(conn, big, a, b)
    s.check("priced as the booking would be", abs(by.get(big["id"], {}).get("price", 0) - rack) < 0.01)
    tax = m.compute_city_tax(conn, 3, 1, 2)[0]
    s.check("and the taxe de sejour is for two adults, not three guests", abs(q.get("tax", -1) - tax) < 0.01,
            detail=f"{q.get('tax')} vs {tax}")
    q3 = owner.get(f"/admin/bookings/walk-in/options.json?arrival_date={a}&departure_date={b}"
                   "&party_size=3&guests_under_18=0").get_json() or {}
    s.check("three adults fit neither", not any(r["fits"] for r in q3.get("rooms", [])
                                                if r["id"] in (small["id"], big["id"])))
    s.check("and nobody else can ask", employee.get(
        f"/admin/bookings/walk-in/options.json?arrival_date={a}&departure_date={b}").status_code in (302, 403))

    s.section("Booking it")
    r = owner.post("/admin/bookings/walk-in", data={
        "mode": "paylink", "room_id": str(big["id"]), "arrival_date": a.isoformat(),
        "departure_date": b.isoformat(), "guest_name": f"{TAG} Nomail", "adults": "2",
        "guests_under_18": "1", "party_size": "2"}, follow_redirects=True)
    s.check("a payment link with no address is refused before anything is written",
            not conn.execute("SELECT 1 FROM bookings WHERE guest_name = ?", (f"{TAG} Nomail",)).fetchone()
            and "needs their email address" in r.get_data(as_text=True))
    r = owner.post("/admin/bookings/walk-in", data={
        "mode": "book", "room_id": str(big["id"]), "arrival_date": a.isoformat(),
        "departure_date": b.isoformat(), "guest_name": f"{TAG} Family", "adults": "2",
        "guests_under_18": "1", "party_size": "2"})
    stay = conn.execute("SELECT * FROM bookings WHERE guest_name = ?", (f"{TAG} Family",)).fetchone()
    s.check("it is booked", stay is not None and stay["status"] == "confirmed")
    if stay:
        s.check("for the party in the boxes, not a stale total beside them",
                stay["party_size"] == 3 and stay["guests_under_18"] == 1,
                detail=f"{stay['party_size']} / {stay['guests_under_18']}")
        s.check("and the desk lands on the booking",
                r.headers.get("Location", "").endswith(f"/admin/bookings/{stay['id']}"),
                detail=r.headers.get("Location", ""))


if __name__ == "__main__":
    print(run().report())
