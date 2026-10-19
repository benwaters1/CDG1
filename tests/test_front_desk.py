"""The bookings back office as a hotel's front desk (owner, 19 October 2026:
"it needs to be a proper hotel booking system back end not basic, bookings
are already coming in and it was hard to manage ... stored neatly, editable,
searchable, very organised and clear").

WHAT THIS PINS.
  - ONE LIST OF EVERY RESERVATION. Booking.com's stays are on it, read-only
    and badged, with their own chip; "staying tonight" and today's arrivals
    count them. A guest leaving today is still on the list that morning.
    Cancelled and declined stays are a chip away, not the first screen.
  - A PAYMENT TAKEN BY HAND IS RECORDED ON THE STAY, and the desk comes back
    to the stay.
  - THE NAME, THE ADDRESS AND THE ROOM CAN BE CHANGED. A room move checks the
    new room is free, prices the stay at its rate and goes in the history; a
    move into a taken room is refused.
  - THE CALENDAR IS A TIMELINE: every day the same width.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, free_window
import _harness

m = _harness.m
TAG = "ZZDESK"


def _cleanup(conn):
    ids = "(SELECT id FROM bookings WHERE guest_name LIKE 'ZZDESK%')"
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {ids}")
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE '7700%'")
    conn.commit()


def _stay(conn, room, a, nights, name, status="confirmed"):
    with m.app.test_request_context("/"):
        ref, _t = m.create_booking(conn, room, f"{TAG} {name}", f"zzdesk.{name.lower()}@example.invalid",
                                   "+33 6 00 00 00 00", a, a + timedelta(days=nights), 2, "", [],
                                   confirm_now=(status == "confirmed"), source="desk")
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (ref,)).fetchone()
    if status != "confirmed":
        conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, row["id"]))
        conn.commit()
    return conn.execute("SELECT * FROM bookings WHERE id = ?", (row["id"],)).fetchone()


def run():
    s = Suite("The bookings back office as a front desk")
    conn = db()
    oc, ec, _o, _e = clients()
    _cleanup(conn)
    try:
        _checks(s, conn, oc)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, conn, oc):
    today = m.house_today()
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    first = rooms[0]
    # A second room at another price, so a move that kept the old price shows.
    second = next((r for r in rooms[1:] if (r["price_per_night"] or 0) != (first["price_per_night"] or 0)),
                  rooms[1])
    now = datetime.now(timezone.utc).isoformat()

    s.section("One list of every reservation")
    leaving = _stay(conn, first, today - timedelta(days=2), 2, "Leaving")
    gone = _stay(conn, second, free_window(second["id"], 2, after_days=60), 2, "Called", status="cancelled")
    conn.execute("""INSERT INTO ota_reservations (channel, reservation_number, status, guest_name, arrival_date,
                    departure_date, room_label, room_id, guests, amount, currency, first_seen_at, updated_at)
                    VALUES ('booking.com', '7700111', 'confirmed', ?, ?, ?, ?, ?, 2, 410, 'EUR', ?, ?)""",
                 (f"{TAG} Channel Guest", today.isoformat(), (today + timedelta(days=2)).isoformat(),
                  second["name"], second["id"], now, now))
    conn.commit()
    page = oc.get("/admin/bookings", query_string={"q": TAG}).get_data(as_text=True)
    s.check("a Booking.com stay is on the list", f"{TAG} Channel Guest" in page)
    s.check("badged and with its own reference", "Booking.com 7700111" in page)
    s.check("opening its own page, not a house stay's",
            "/admin/channel-stays?q=7700111" in page)
    s.check("and paid to the channel, not owed to the house", "paid to Booking.com" in page)
    s.check("a guest leaving today is still on the list that morning",
            f"/admin/bookings/{leaving['id']}" in page)
    s.check("a cancelled stay is not on the first screen", f"/admin/bookings/{gone['id']}\"" not in page)
    off = oc.get("/admin/bookings", query_string={"q": TAG, "when": "Called off"}).get_data(as_text=True)
    s.check("it is a chip away", f"/admin/bookings/{gone['id']}" in off and "Cancelled or declined" in off)
    chip = oc.get("/admin/bookings", query_string={"q": TAG, "source": "Booking.com"}).get_data(as_text=True)
    chip = chip[chip.find('class="data-table bk-table"'):]   # the list, not today's panel above it
    s.check("the Came through chip filters to the channel's",
            f"{TAG} Channel Guest" in chip and f"/admin/bookings/{leaving['id']}\"" not in chip)
    today_chip = oc.get("/admin/bookings", query_string={"q": TAG, "stage": "Leaving today"}).get_data(as_text=True)
    today_chip = today_chip[today_chip.find('class="data-table bk-table"'):]
    s.check("'Leaving today' finds who leaves today", f"/admin/bookings/{leaving['id']}" in today_chip)
    arr = oc.get("/admin/bookings", query_string={"q": TAG, "stage": "Arriving today"}).get_data(as_text=True)
    s.check("and 'Arriving today' finds the channel's arrival too", f"{TAG} Channel Guest" in arr)
    whole = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("today's arrivals name the Booking.com guest", f"{TAG} Channel Guest</a>" in whole)

    s.section("A payment taken by hand, recorded on the stay")
    a = free_window(first["id"], 3, after_days=70)
    owes = _stay(conn, first, a, 3, "Owes")
    bill = m.booking_bill(conn, owes["id"])
    page = oc.get(f"/admin/bookings/{owes['id']}").get_data(as_text=True)
    s.check("the stay's page offers to record one", "Record a payment" in page
            and f"/management/outstanding/{owes['id']}/payment" in page)
    s.check("and the refund form is folded away", '<details class="detail-card bk-refund">' in page)
    r = oc.post(f"/management/outstanding/{owes['id']}/payment",
                data={"amount": "100", "method": "bank_transfer", "reference": "VIR 123", "back": "booking"})
    s.check("it comes back to the stay", r.headers.get("Location", "").endswith(f"/admin/bookings/{owes['id']}#money"),
            detail=r.headers.get("Location", ""))
    after = m.booking_bill(conn, owes["id"])
    s.check("and the bill has it", abs(after["paid"] - (bill["paid"] + 100)) < 0.01)

    s.section("The name, the address and the room can be changed")
    base = {"arrival_date": owes["arrival_date"], "departure_date": owes["departure_date"],
            "party_size": "2", "guests_under_18": "0", "guest_phone": owes["guest_phone"],
            "guest_email": owes["guest_email"]}
    oc.post(f"/admin/bookings/{owes['id']}/edit", data=dict(
        base, guest_name=f"{TAG} Owes Corrected", guest_address="1 Rue Haute", guest_city="Foix",
        guest_postcode="09000", guest_country="France"))
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (owes["id"],)).fetchone()
    s.check("a misspelt name is corrected", row["guest_name"] == f"{TAG} Owes Corrected")
    s.check("and the address kept field by field", (row["guest_address"], row["guest_city"],
            row["guest_postcode"], row["guest_country"]) == ("1 Rue Haute", "Foix", "09000", "France"))
    oc.post(f"/admin/bookings/{owes['id']}/edit", data=dict(base, guest_name=""))
    s.check("a name cannot be emptied", conn.execute("SELECT guest_name FROM bookings WHERE id = ?",
            (owes["id"],)).fetchone()[0] == f"{TAG} Owes Corrected")
    # Somebody already in the second room on these nights: the move is refused.
    blocker = _stay(conn, second, m.parse_date(owes["arrival_date"]), 3, "Blocker")
    r = oc.post(f"/admin/bookings/{owes['id']}/edit", data=dict(base, room_id=str(second["id"])),
                follow_redirects=True)
    s.check("a move into a room that is taken is refused, saying so",
            conn.execute("SELECT room_id FROM bookings WHERE id = ?", (owes["id"],)).fetchone()[0] == first["id"]
            and "is not free for those nights" in " ".join(flashes(r)), detail=" ".join(flashes(r)))
    conn.execute("DELETE FROM bookings WHERE id = ?", (blocker["id"],))
    conn.commit()
    oc.post(f"/admin/bookings/{owes['id']}/edit", data=dict(base, room_id=str(second["id"])))
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (owes["id"],)).fetchone()
    s.check("a move into a free room is made", row["room_id"] == second["id"])
    rack = m.compute_room_total(conn, second, m.parse_date(row["arrival_date"]), m.parse_date(row["departure_date"]))
    stamped = m.booking_bill(conn, owes["id"])
    room_line = round(sum(l["amount"] for l in stamped["lines"] if l.get("kind") != "city_tax"), 2)
    s.check("priced at the new room's rate", abs(room_line - rack) < 0.01, detail=f"{room_line} vs {rack}")
    trail = " ".join(r["details"] or "" for r in conn.execute(
        "SELECT details FROM audit_log WHERE target = ? AND action = 'booking_edited'", (row["reference_code"],)))
    s.check("and the move is in its history", f"room {first['name']} → {second['name']}" in trail, detail=trail[-200:])

    s.section("The calendar is a timeline")
    cal = oc.get("/admin/calendar").get_data(as_text=True)
    s.check("every day the same width", 'class="cal-table cal-fixed"' in cal and "cal-col-day" in cal)


if __name__ == "__main__":
    print(run().report())
