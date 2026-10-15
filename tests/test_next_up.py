"""The owner home's "Next up" card.

On 15 October 2026 it said "Tomorrow -- Tess Hoffman arriving" for a stay on
12 November: with nothing left today it falls back to the next confirmed
arrival, and labelled that "Tomorrow" whatever its date. The same card read
the times on today's rows -- the house's clock -- as UTC, so its countdown ran
an hour or two out.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZNEXTUP"


def _arrival(conn, day):
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    ref = TAG + day.isoformat()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 'zz@example.invalid', ?, ?, 1, 'confirmed', 100, ?)""",
        (room["id"], ref, ref + "tok", f"{TAG} Guest", day.isoformat(),
         (day + timedelta(days=1)).isoformat(), datetime.now(timezone.utc).isoformat()))


def run():
    s = Suite("Next up on the owner home")
    conn = db()
    today = m.house_today()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    # Nothing confirmed ahead but ours, so the card cannot pick somebody else.
    held = conn.execute(
        """SELECT id, status FROM bookings WHERE status = 'confirmed' AND arrival_date > ?""",
        (today.isoformat(),)).fetchall()
    conn.executemany("UPDATE bookings SET status = 'pending' WHERE id = ?", [(r["id"],) for r in held])
    try:
        s.section("The next arrival is labelled with its own day")
        _arrival(conn, today + timedelta(days=28))
        conn.commit()
        later = m.owner_home_next_up(conn, today, [])
        s.check("one four weeks away is not 'Tomorrow'",
                later and later["tomorrow"] is False, detail=str(later))
        s.check("it gives the day instead",
                later and later["countdown"] == m.format_date_human(
                    (today + timedelta(days=28)).isoformat()), detail=str(later))

        conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
        _arrival(conn, today + timedelta(days=1))
        conn.commit()
        soon = m.owner_home_next_up(conn, today, [])
        s.check("one arriving tomorrow is 'Tomorrow'", soon and soon["tomorrow"] is True,
                detail=str(soon))

        s.section("Today's times are the house's clock")
        local_now = datetime.now(m.LOCAL_TZ)
        if local_now.hour < 23:
            in_half_an_hour = (local_now + timedelta(minutes=30)).strftime("%H:%M")
            card = m.owner_home_next_up(conn, local_now.date(), [
                {"time": in_half_an_hour, "done": False, "title": "Dinner", "detail": "Salon"}])
            s.check("an event half an hour away counts down from half an hour",
                    card and card["countdown"] in ("in 29m", "in 30m"),
                    detail=f"{card} — read as UTC it is an hour or two out")
        else:
            s.check("(late evening: the half-hour check needs the same day)", True)
    finally:
        conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
        conn.executemany("UPDATE bookings SET status = ? WHERE id = ?",
                         [(r["status"], r["id"]) for r in held])
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
