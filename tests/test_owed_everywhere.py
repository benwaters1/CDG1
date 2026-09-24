"""What a guest owes, the same everywhere somebody looks for it.

The owner asked whether it is easy to see the balance due. It was, on one page,
What we're owed; everywhere else it was either missing or worked out afresh,
each way differently:

  - A guest's record counted declined and unconfirmed requests as money spent
    and money owed, and left ateliers and events out of what was owed, so its
    statement charged for things it then neither received nor chased -- the
    three lines did not add up.
  - The cash outlook took a stay's discount off a second time and left out
    its extras and tax, and counted an event's whole quote however much of it
    had been paid. Money due read an atelier's balance from the figure fixed
    the day they booked.
  - The bookings list showed a price and a paid/unpaid chip, so a stay with an
    extra added since read as settled; and nothing on the owner home said that
    money was owed and late.

Every figure here now comes from one definition per kind -- booking_bill, the
atelier ledger, event_bill -- and this checks each place reads it.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZOW"
EMAIL = "zzow.guest@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_payments WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, arrive_in, total, paid, status="confirmed", nights=2, email=EMAIL,
          due_in=None):
    """A stamped stay, the way create_booking leaves one."""
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    arrival = house_today() + timedelta(days=arrive_in)
    departure = arrival + timedelta(days=nights)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, amount_paid, city_tax,
           created_at, room_total_quoted, room_total_quoted_for, balance_due_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, 0, ?, ?, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", email,
         arrival.isoformat(), departure.isoformat(), status, total, paid,
         datetime.now(timezone.utc).isoformat(), total,
         f"{arrival.isoformat()}|{departure.isoformat()}",
         (house_today() + timedelta(days=due_in)).isoformat() if due_in is not None else None))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()["id"]
    conn.close()
    return bid


def _atelier(ref, *, status="confirmed", total=2000.0, paid=1100.0, due_in=20):
    conn = db()
    now = _harness.datetime_now()
    if not conn.execute("SELECT 1 FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone():
        conn.execute("INSERT INTO workshops (title, description, price_per_person, default_capacity, "
                     "active, sort_order, created_at, deposit_percent) "
                     "VALUES (?, '', ?, 10, 1, 91, ?, 30)", (f"{TAG} Atelier", total, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=due_in + 30)
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, "
                 "created_at) VALUES (?, ?, ?, 10, ?, ?)",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), f"{TAG} {ref}", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} {ref}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           deposit_paid_at, balance_due_date)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 600, 1400, ?, ?)""",
        (sid, f"{TAG} {ref}", EMAIL, status, f"{TAG}{ref}", f"tok{TAG}w{ref}", now, total, now,
         (house_today() + timedelta(days=due_in)).isoformat()))
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Paid", paid, method="stripe")
    conn.commit()
    conn.close()
    return bid


def _event(ref, *, status="confirmed", quote=3000.0, paid=1000.0, due_in=30):
    conn = db()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
           contact_email, contact_phone, preferred_date, guest_count, message, status,
           quoted_price, amount_paid, balance_due_date, created_at)
           VALUES (?, ?, 'wedding', ?, ?, '', ?, 60, 'ZZ test', ?, ?, ?, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}e{ref}".lower(), f"{TAG} {ref}", EMAIL,
         (house_today() + timedelta(days=due_in + 30)).isoformat(), status, quote, paid,
         (house_today() + timedelta(days=due_in)).isoformat(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    eid = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()["id"]
    conn.close()
    return eid


def run():
    s = Suite("Owed, everywhere")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    s.section("A guest's record counts what is real, of every kind, and adds up")
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Guest", EMAIL, _harness.datetime_now()))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} Guest",)).fetchone()["id"]
    conn.commit()
    conn.close()
    _stay("Real", arrive_in=15, total=800, paid=600)            # 200 owed
    _stay("Declined", arrive_in=40, total=500, paid=0, status="declined")
    _stay("Asked", arrive_in=50, total=450, paid=0, status="pending")
    _atelier("Ws", paid=1100.0)                                  # 900 owed
    _atelier("WsGone", status="cancelled", paid=600.0)
    _event("Wed", quote=3000.0, paid=1000.0)                     # 2000 owed
    conn = db()
    rec = m.guest_record(conn, gid)
    conn.close()
    s.check("owed is the stay, the atelier and the event -- and nothing declined or unconfirmed",
            abs(rec["owed"] - 3100.0) < 0.01, detail=f"owed {rec['owed']}")
    s.check("spent is what was charged for those three",
            abs(rec["spent"] - (800 + 2000 + 3000)) < 0.01, detail=f"spent {rec['spent']}")
    s.check("and the three lines add up", abs(rec["spent"] - rec["paid"] - rec["owed"]) < 0.01,
            detail=f"{rec['spent']} - {rec['paid']} != {rec['owed']}")
    page = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("the record says it", "€3,100" in page, detail="Still owed on the record")
    s.check("the atelier's row carries its money", "€900.00" in page)
    s.check("and so does the event's", "€2000.00" in page or "€2,000.00" in page)
    s.check("the book-them-again box is there for the owner",
            f"/guests/{gid}/rebook" in page,
            detail="it asked the record for an id and an address it was never given")
    stmt = oc.get(f"/guests/{gid}/statement").get_data(as_text=True)
    s.check("the statement lists the event", "Events" in stmt and "wedding" in stmt)
    s.check("and its outstanding line is the same figure", "€3100.00" in stmt,
            detail="it charged ateliers and received and owed for stays alone")
    s.check("a declined stay is not on it", f"{TAG}-Declined" not in stmt)

    s.section("More received than charged is in credit, not a minus owed")
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Credit", "zzow.credit@example.invalid", _harness.datetime_now()))
    cid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} Credit",)).fetchone()["id"]
    conn.commit()
    conn.close()
    _stay("Over", arrive_in=25, total=300, paid=350, email="zzow.credit@example.invalid")
    page = oc.get(f"/guests/{cid}").get_data(as_text=True)
    s.check("the record says in credit", "In credit" in page)

    s.section("The forecasts read the same bills")
    conn = db()
    items = [x for x in m.expected_money_in(conn) if x["ref"].startswith(TAG)]
    conn.close()
    by = {x["ref"]: x for x in items}
    s.check("the stay is what its bill says is owed, not total less a discount again",
            abs(by.get(f"{TAG}-Real", {}).get("amount", 0) - 200.0) < 0.01, detail=f"{by.get(f'{TAG}-Real')}")
    s.check("the atelier is what its ledger says is left",
            abs(by.get(f"{TAG}Ws", {}).get("amount", 0) - 900.0) < 0.01,
            detail=f"{by.get(f'{TAG}Ws')} -- the figure fixed on booking day was 1400")
    s.check("the event is what is left of its quote",
            abs(by.get(f"{TAG}-Wed", {}).get("amount", 0) - 2000.0) < 0.01,
            detail=f"{by.get(f'{TAG}-Wed')} -- the whole quote was counted however much was paid")
    s.check("nothing declined, unconfirmed or cancelled",
            not ({f"{TAG}-Declined", f"{TAG}-Asked", f"{TAG}WsGone"} & set(by)))
    conn = db()
    due = m.money_due(conn, weeks=12)
    conn.close()
    atelier_due = [i for i in due["csv"] if i["who"] == f"{TAG} Ws"]
    s.check("money due has the atelier at the ledger's figure",
            atelier_due and abs(atelier_due[0]["amount"] - 900.0) < 0.01, detail=f"{atelier_due}")
    event_due = [i for i in due["csv"] if i["who"] == f"{TAG} Wed"]
    s.check("and the event at what is left", event_due and abs(event_due[0]["amount"] - 2000.0) < 0.01,
            detail=f"{event_due}")
    # Measured as a difference, so whatever else the database holds does not
    # move the answer: one more event, quoted 5000 with 1500 paid, must add
    # 3500 to what the outlook expects in, not 5000.
    conn = db()
    before = sum(r["in_events"] for r in m.cash_outlook(conn, months=4)["rows"])
    conn.close()
    _event("Quote", quote=5000.0, paid=1500.0, due_in=40)
    conn = db()
    after = sum(r["in_events"] for r in m.cash_outlook(conn, months=4)["rows"])
    conn.close()
    s.check("the cash outlook counts what is left of an event, not its quote",
            abs((after - before) - 3500.0) < 0.01,
            detail=f"added {after - before} -- the whole quote was counted however much was paid")

    # And a stay with a discount: total_price is already net of it, and the
    # outlook took it off again. 1000 net of 100 off, 300 paid, is 700 owed.
    conn = db()
    before = sum(r["in_rooms"] for r in m.cash_outlook(conn, months=4)["rows"])
    conn.close()
    _stay("Disc", arrive_in=35, total=1000, paid=300)
    conn = db()
    # As create_booking leaves a discounted stay: the room stamped at its price
    # BEFORE the discount, total_price after it.
    conn.execute("UPDATE bookings SET discount_amount = 100, room_total_quoted = 1100 "
                 "WHERE reference_code = ?", (f"{TAG}-Disc",))
    conn.commit()
    after = sum(r["in_rooms"] for r in m.cash_outlook(conn, months=4)["rows"])
    conn.close()
    s.check("and a discounted stay adds what its bill says, not the discount off twice",
            abs((after - before) - 700.0) < 0.01, detail=f"added {after - before}")

    s.section("The bookings list says what a stay still owes")
    page = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the stay shows its €200 owed", "€200.00 still owed" in page)
    money = oc.get("/admin/bookings?money=Owes+money").get_data(as_text=True)
    s.check("and 'owes money' is a chip", f"{TAG}-Real" in money and f"{TAG}-Over" not in money)

    s.section("The owner home says when money owed is late")
    _stay("Gone", arrive_in=-6, total=500, paid=100)             # left owing 400
    conn = db()
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, house_today())
    conn.close()
    line = next((w for w in warnings if "and late" in w["title"]), None)
    s.check("there is a line for it", line is not None, detail=f"{[w['title'] for w in warnings]}")
    s.check("naming the longest", line is not None and f"{TAG} Gone" in line["detail"],
            detail=f"{line['detail'] if line else None}")
    s.check("which goes to What we're owed", line is not None and line["href"].endswith("/management/outstanding"))
    conn = db()
    conn.execute("UPDATE bookings SET amount_paid = 500 WHERE reference_code = ?", (f"{TAG}-Gone",))
    conn.commit()
    with m.app.test_request_context("/"):
        after = m.owner_home_warnings(conn, house_today())
    conn.close()
    still = next((w for w in after if "and late" in w["title"]), None)
    s.check("and it closes itself once paid",
            still is None or f"{TAG} Gone" not in still["detail"], detail=f"{still}")

    _cleanup()
    return s
