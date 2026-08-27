"""What each room earns.

bookings was never grouped by room, so which rooms carry the house and which
sit empty had no answer anywhere in the app.

The checks that matter are all about what the figure contains.
bookings.total_price is room + extras - discount and excludes city tax, so
using it whole would credit a bedroom with a hamper and a taxi. And a stay
spanning the window edge must contribute only the nights it actually used,
or a long January stay lands whole in whichever month the report starts.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-re-"


def _cleanup(conn):
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def _rooms(conn):
    return conn.execute("SELECT id, name FROM rooms ORDER BY id LIMIT 3").fetchall()


def _stay(conn, ref, room_id, arrive, nights, total, extras=0.0, status="confirmed"):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'G', 'g@example.invalid', ?, ?, 2, ?, ?, ?)""",
        (room_id, TAG + ref, TAG + ref + "tok", arrive.isoformat(),
         (arrive + timedelta(days=nights)).isoformat(), status, total,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    if extras:
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (TAG + ref,)).fetchone()["id"]
        conn.execute(
            """INSERT INTO booking_extras (category, booking_id, name, unit_price,
               quantity, created_at) VALUES ('room', ?, 'Hamper', ?, 1, ?)""",
            (bid, extras, datetime.now(timezone.utc).isoformat()))
        conn.commit()


def _room(data, room_id):
    return next((r for r in data["rooms"] if r["id"] == room_id), None)


def run():
    s = Suite("room economics")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = datetime.now(m.LOCAL_TZ).date()
    rooms = _rooms(conn)
    window_start = m.parse_date(m.room_economics(conn, months=12)["window"][0])

    s.section("The room's own earnings, not the whole bill")
    # Measured as a DELTA against whatever is already in the database. Absolute
    # totals only hold when nothing else has booked these rooms, which is true
    # running this suite alone and false in a full run — the assertions passed
    # in isolation and failed together, which reads as a flaky test rather
    # than the brittle one it was.
    before = _room(m.room_economics(conn, months=12), rooms[0]["id"])
    base_room = before["room_revenue"] if before else 0.0
    base_extras = before["extras_revenue"] if before else 0.0
    base_nights = before["nights"] if before else 0

    # 4 nights at 400 = 1600 for the room, plus a 200 hamper. The hamper is
    # not the bedroom earning money.
    _stay(conn, "A", rooms[0]["id"], today - timedelta(days=30), 4, 1800.0, extras=200.0)
    data = m.room_economics(conn, months=12)
    r = _room(data, rooms[0]["id"])
    s.check("extras are kept out of the room figure",
            r and abs((r["room_revenue"] - base_room) - 1600.0) < 0.01,
            detail=str(r["room_revenue"] - base_room) if r else "")
    s.check("and reported separately rather than dropped",
            r and abs((r["extras_revenue"] - base_extras) - 200.0) < 0.01,
            detail=str(r["extras_revenue"] - base_extras) if r else "")
    s.check("the room earned 400 a night on it",
            r and abs((r["room_revenue"] - base_room) / 4 - 400.0) < 0.01,
            detail=str(r["nightly"]) if r else "")
    s.check("nights are counted", r and r["nights"] - base_nights == 4,
            detail=str(r["nights"] - base_nights) if r else "")

    s.section("A stay across the window edge counts only its nights inside")
    # Arrives four nights before the window opens, stays ten: six inside.
    was = _room(m.room_economics(conn, months=12), rooms[1]["id"])
    was_nights = was["nights"] if was else 0
    was_rev = was["room_revenue"] if was else 0.0
    _stay(conn, "B", rooms[1]["id"], window_start - timedelta(days=4), 10, 1000.0)
    data = m.room_economics(conn, months=12)
    r = _room(data, rooms[1]["id"])
    s.check("only the nights inside are counted", r and r["nights"] - was_nights == 6,
            detail=str(r["nights"] - was_nights) if r else "")
    s.check("and the money is pro-rated with them",
            r and abs((r["room_revenue"] - was_rev) - 600.0) < 0.01,
            detail=str(r["room_revenue"] - was_rev) if r else "")

    s.section("Only confirmed stays")
    pend = _room(m.room_economics(conn, months=12), rooms[2]["id"])
    pend_nights = pend["nights"] if pend else 0
    _stay(conn, "C", rooms[2]["id"], today - timedelta(days=10), 3, 900.0,
          status="pending")
    data = m.room_economics(conn, months=12)
    r = _room(data, rooms[2]["id"])
    pend_before = _room(data, rooms[2]["id"])
    s.check("a pending booking earns nothing yet",
            pend_before is not None and pend_before["nights"] == pend_nights,
            detail=str(pend_before["nights"]) if pend_before else "")
    s.check("so it adds nothing to that room's earnings",
            r and abs(r["room_revenue"] - (pend["room_revenue"] if pend else 0.0)) < 0.01,
            detail=str(r["room_revenue"]) if r else "")

    s.section("The totals add up to the rows")
    # The invariant that makes the table trustworthy.
    s.check("room revenue totals the rows",
            abs(sum(x["room_revenue"] for x in data["rooms"]) - data["room_revenue"]) < 0.01)
    s.check("extras total the rows",
            abs(sum(x["extras_revenue"] for x in data["rooms"]) - data["extras_revenue"]) < 0.01)
    s.check("nights total the rows",
            sum(x["nights"] for x in data["rooms"]) == data["nights"])

    s.section("Open faults are shown against the room")
    conn.execute(
        """INSERT INTO room_issues (room_id, title, status, created_at)
           VALUES (?, ?, 'open', ?)""",
        (rooms[0]["id"], TAG + "shutter", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    r = _room(m.room_economics(conn, months=12), rooms[0]["id"])
    s.check("a fault in the best-earning room is visible here",
            r and r["open_faults"] >= 1, detail=str(r["open_faults"]) if r else "")

    s.section("The page")
    page = oc.get("/management/rooms-economics?months=12").get_data(as_text=True)
    s.check("it renders", "What each room earns" in page)
    # The three honesty statements, each of which stops a real misreading.
    # Short fragments only. A phrase long enough to wrap in the template
    # tests where the line breaks, not what the page says — this is the third
    # time that has caught me today.
    s.check("it says extras are not the bedroom earning money",
            "not the bedroom" in page)
    s.check("it says city tax is never counted", "not the house" in page)
    s.check("and admits it does not know what a room costs",
            "is not the same as" in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
