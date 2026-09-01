"""How many are likely at the table, from the three places that knew.

The restaurant page says the kitchen buys for the table it is cooking for.
Every input for that existed and nothing multiplied them: reservations on one
screen, arrivals on another, atelier residents on a third. So Saturday's order
was a guess made on Tuesday.

The design of this is mostly about NOT adding things together. A reservation
is a number. An atelier place includes dinner, so it is also a number. A guest
asleep upstairs is neither — most of them eat in and "most" is not a booking.
Folding that third figure into the first two would produce one confident
number that is wrong every night, which is worse than three honest ones.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-cov-"


def _iso(days):
    return (m.service_day() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _forecast(conn, days):
    """The forecast, or an empty one carrying the error.

    A divide-by-zero in the take-up rate would otherwise raise out of the
    suite, and a crashed suite reports none of the checks after it — which is
    how the one break that matters most hides the four that would have named
    it.
    """
    try:
        with m.app.test_request_context():
            return m.dinner_covers_forecast(conn, days=days)
    except Exception as exc:
        return {"rows": [], "rate": None, "rate_basis": "",
                "certain_total": 0, "most_total": 0,
                "blew_up": "%s: %s" % (type(exc).__name__, exc)}


def _row(data, day_offset):
    want = _iso(day_offset)
    return next((r for r in data["rows"] if r["iso"] == want), None)


def run():
    s = Suite("dinner covers")
    clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    # A dinner reservation for four, three nights out.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, created_at)
           VALUES (?, ?, 'A Diner', ?, 4, ?, 'confirmed', ?)""",
        (TAG + "R1", TAG + "t1", TAG + "d@example.invalid", _iso(3), now))
    # A no-show that has been marked: real once, not a cover now.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, no_show_at, created_at)
           VALUES (?, ?, 'Absent', ?, 6, ?, 'confirmed', ?, ?)""",
        (TAG + "R2", TAG + "t2", TAG + "n@example.invalid", _iso(3), now, now))
    # A stay of two, arriving day 3 and leaving day 5.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, created_at)
           VALUES ((SELECT id FROM rooms LIMIT 1), ?, ?, 'A Guest', ?, ?, ?, 2,
                   'confirmed', 400, ?)""",
        (TAG + "B1", TAG + "t3", TAG + "g@example.invalid", _iso(3), _iso(5), now))
    conn.commit()

    data = _forecast(conn, 14)

    s.check("the forecast was produced at all", "blew_up" not in data,
            detail=data.get("blew_up", ""))

    s.section("Each source is counted, and counted once")
    d3 = _row(data, 3)
    s.check("a reservation is a booked cover", d3 and d3["booked"] == 4,
            detail=None if not d3 else str(d3["booked"]))
    # Marked no-shows are the reason "confirmed" is not enough on its own.
    s.check("a marked no-show is not", d3 and d3["booked"] == 4,
            detail="6 more would mean the no-show is still being cooked for")
    s.check("a guest sleeping here is in-house, not booked",
            d3 and d3["in_house"] == 2 and d3["booked"] == 4,
            detail=None if not d3 else f"{d3['in_house']}/{d3['booked']}")

    s.section("A stay covers the nights it sleeps, not the day it leaves")
    d4 = _row(data, 4)
    d5 = _row(data, 5)
    s.check("the night between counts", d4 and d4["in_house"] == 2,
            detail=None if not d4 else str(d4["in_house"]))
    # Somebody leaving on the 5th does not eat here on the 5th.
    s.check("departure day does not", d5 and d5["in_house"] == 0,
            detail=None if not d5 else str(d5["in_house"]))

    s.section("An atelier eats here, so it is certain")
    conn.execute("INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
                 (TAG + "atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (TAG + "atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 15, ?, ?)""", (wid, _iso(7), _iso(9), TAG + "sess", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (TAG + "sess",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
             guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, 'A Potter', ?, 3, 'confirmed', 100, ?)""",
        (sid, TAG + "W1", TAG + "t4", TAG + "p@example.invalid", now))
    conn.commit()
    data = _forecast(conn, 14)
    d7, d8, d9 = _row(data, 7), _row(data, 8), _row(data, 9)
    s.check("the nights the atelier runs carry its heads",
            d7 and d8 and d7["atelier"] == 3 and d8["atelier"] == 3,
            detail=f"{d7['atelier'] if d7 else '?'}/{d8['atelier'] if d8 else '?'}")
    s.check("and they are certain, not estimated",
            d7 and d7["certain"] == d7["booked"] + d7["atelier"],
            detail=None if not d7 else str(d7["certain"]))
    # An atelier's end_date is NOT a stay's departure_date, and the app had
    # already decided which: the workshop-versus-room clash check asks for
    # `arrival_date < end_date + 1 day`, so a session holds its rooms through
    # end_date and the guests are here that night. They eat. A stay is the
    # other way — departure day is checkout — and the two conventions look
    # alike enough that writing this test the wrong way round was the obvious
    # mistake to make.
    s.check("the last day of an atelier is still a night here",
            d9 and d9["atelier"] == 3, detail=None if not d9 else str(d9["atelier"]))
    d10 = _row(data, 10)
    s.check("but the day after it is not", d10 and d10["atelier"] == 0,
            detail=None if not d10 else str(d10["atelier"]))

    s.section("The three are never silently added up")
    s.check("certain is booked plus atelier only",
            all(r["certain"] == r["booked"] + r["atelier"] for r in data["rows"]),
            detail="in-house has been folded into the certain figure")
    s.check("and the outside case adds every guest staying",
            all(r["most"] == r["booked"] + r["atelier"] + r["in_house"]
                for r in data["rows"]))
    s.check("so the two differ exactly by who is upstairs",
            all(r["most"] - r["certain"] == r["in_house"] for r in data["rows"]))

    s.section("With no history it declines to project")
    # A take-up rate built on one week is a number that gets ordered against.
    s.check("the rate is withheld rather than guessed",
            data["rate"] is None or isinstance(data["rate"], float),
            detail=str(data["rate"]))
    if data["rate"] is None:
        s.check("and it says why", "guest-night" in (data["rate_basis"] or ""),
                detail=str(data["rate_basis"]))
        s.check("leaving the likely column empty rather than zero",
                all(r["likely"] is None for r in data["rows"]),
                detail="an empty projection reads as none, a zero reads as nobody")
    else:
        s.check("and says what it was measured from",
                "guest-nights" in (data["rate_basis"] or ""), detail=str(data["rate_basis"]))
        s.check("the projection never exceeds everyone staying",
                all(r["likely"] <= r["most"] for r in data["rows"]))

    s.section("The window is the window")
    short = _forecast(conn, 7)
    s.check("seven nights means seven rows", len(short["rows"]) == 7,
            detail=str(len(short["rows"])))
    s.check("and the atelier nine days out is not in it",
            all(r["atelier"] == 0 for r in short["rows"]),
            detail="something outside the window was counted")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
