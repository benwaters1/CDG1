"""The next free nights, so nobody has to guess two dates.

"Check availability" asks a guest to pick two dates and find out they were
wrong. This shows the stretches that are actually open, on the booking page,
before anybody has typed anything.

Three decisions carry the whole thing, and each is a way of being wrong that
would look fine on the page:

  - IT MUST BE BOOKABLE. A room with a three-night minimum and two free
    nights is not an opening. Offering it produces a guest who picks those
    dates, is refused, and trusts the page less than if it had never been
    there. The run has to reach the room's own minimum.
  - ONE PER ROOM. A naive scan finds the same room open in six consecutive
    weeks and fills the whole list with it, which says nothing at all.
  - AND IT MUST NOT LEAD WITH TONIGHT. A page offering tomorrow reads as an
    empty house, which is the wrong thing to say about a château — and for a
    guest who has to fly, it is not a stay they could reach.

Availability comes from is_range_available, the same function the booking
flow refuses on. A second query of my own would agree with it right up until
the day it did not, and that day would be a guest turned away from dates the
front page had offered them.
"""
from datetime import timedelta

from _harness import Suite, db, house_today

import _harness

m = _harness.m
TAG = "ZZFREE"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM blocked_dates WHERE room_id IN "
        "(SELECT id FROM rooms WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the next free nights")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    _cleanup(conn)

    def add_room(name, min_nights, price=200):
        conn.execute(
            """INSERT INTO rooms (name, description, max_occupancy, max_adults,
                       price_per_night, min_nights, active, sort_order,
                       export_token)
               VALUES (?, '', 2, 2, ?, ?, 1, 98, ?)""",
            (TAG + " " + name, price, min_nights,
             f"tok-{TAG}-{name}".lower().replace(" ", "-")))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def book(room_id, ref, start_in, nights):
        start = today + timedelta(days=start_in)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, created_at)
               VALUES (?, ?, ?, 'Someone', 'x@example.invalid', ?, ?, 2,
                       'confirmed', 100, ?)""",
            (room_id, TAG + ref, f"tok-{TAG}-{ref}".lower(), start.isoformat(),
             (start + timedelta(days=nights)).isoformat(), now))

    # A room free from the lead day onward.
    easy = add_room("Easy", 1)
    # Three-night minimum, and only a two-night gap early on: that gap must
    # NOT be offered. Booked solid until day 40 apart from that gap.
    fussy = add_room("Fussy", 3)
    book(fussy, "F1", m.NEXT_FREE_LEAD_DAYS, 2)      # lead .. lead+2
    book(fussy, "F2", m.NEXT_FREE_LEAD_DAYS + 4, 36)  # lead+4 .. lead+40
    # Booked for the whole horizon: it simply has nothing to offer.
    full = add_room("Full", 1)
    book(full, "FU", 0, m.NEXT_FREE_HORIZON_DAYS + 10)
    conn.commit()

    runs = {r["room_name"]: r for r in
            m.next_free_nights(conn, limit=50, today=today)}

    s.section("It offers a stay somebody can actually book")
    s.check("the easy room is offered", TAG + " Easy" in runs)
    s.check("and not before the lead time",
            runs[TAG + " Easy"]["date_iso"] ==
            (today + timedelta(days=m.NEXT_FREE_LEAD_DAYS)).isoformat(),
            detail="a page leading with tonight reads as an empty house, and "
                   "a guest who has to fly cannot take it anyway")

    s.section("A gap shorter than the minimum stay is not an opening")
    fussy_run = runs.get(TAG + " Fussy")
    s.check("the fussy room is offered something", fussy_run is not None,
            detail="it is free after day 40")
    s.check("but NOT the two-night gap it cannot sell",
            fussy_run and fussy_run["date_iso"] !=
            (today + timedelta(days=m.NEXT_FREE_LEAD_DAYS)).isoformat(),
            detail="offering it produces a guest who picks those dates, is "
                   "refused, and trusts the page less than if it had never "
                   "been there")
    s.check("it offers the first date it can really take",
            fussy_run and fussy_run["date_iso"] ==
            (today + timedelta(days=m.NEXT_FREE_LEAD_DAYS + 40)).isoformat(),
            detail=str(fussy_run["date_iso"]) if fussy_run else "")
    s.check("and at least its own minimum",
            fussy_run and fussy_run["nights"] >= 3,
            detail=str(fussy_run["nights"]) if fussy_run else "")

    s.section("A room with nothing free is simply absent")
    s.check("the fully booked room is not on the list",
            TAG + " Full" not in runs,
            detail="an empty answer for one room is an answer, not an error")

    s.section("One entry per room")
    # A naive scan finds the easy room open again next week, and the week
    # after, and fills the list with one room saying nothing.
    all_runs = m.next_free_nights(conn, limit=50, today=today)
    names = [r["room_name"] for r in all_runs]
    s.check("no room appears twice", len(names) == len(set(names)),
            detail=", ".join(sorted(n for n in names if names.count(n) > 1)))

    s.section("The run says how long it really is")
    s.check("the easy room's run is longer than one night",
            runs[TAG + " Easy"]["nights"] > 1,
            detail="repeating the minimum back at somebody tells them nothing")

    s.section("And it agrees with what the booking flow will accept")
    # The check that matters. A second availability query of my own would
    # agree until the day it did not, and that day is a guest refused the
    # dates the front page offered them.
    for r in all_runs:
        if not r["room_name"].startswith(TAG):
            continue
        start = m.parse_date(r["date_iso"])
        ok, why = m.is_range_available(
            conn, r["room_id"], start, start + timedelta(days=r["nights"]))
        s.check(f"{r['room_name']}: the dates offered really are available",
                ok, detail=str(why))

    s.section("On the page")
    anon = m.app.test_client()
    body = anon.get("/book").get_data(as_text=True)
    s.check("the strip is on the booking page", "What Is Open Next" in body)
    s.check("with a room on it", TAG + " Easy" in body)
    s.check("and a link that carries the date",
            f'arrival={runs[TAG + " Easy"]["date_iso"]}' in body,
            detail="a list of dates you then have to retype is the guess "
                   "again with extra steps")

    s.section("Once somebody has searched, it goes")
    searched = anon.get(
        "/book?arrival=" + (today + timedelta(days=60)).isoformat()
        + "&departure=" + (today + timedelta(days=62)).isoformat()
    ).get_data(as_text=True)
    s.check("it is not on a page of search results",
            "What Is Open Next" not in searched,
            detail="somebody just told their dates are taken does not want a "
                   "second list of dates that are not theirs; they want to "
                   "change one of their own")

    s.section("The date reads the same on every machine")
    # "%-d" is not portable and "%d" pads, so the platform decided whether a
    # guest read "5 September" or "05 September" -- and this is written on
    # Windows while production is Linux, which is the way round that never
    # shows up until it is live.
    s.check("no leading zero on the day",
            not any(r["date"].startswith("0") for r in all_runs),
            detail=str([r["date"] for r in all_runs][:3]))
    s.check("and the month is named, not numbered",
            all(any(c.isalpha() for c in r["date"]) for r in all_runs),
            detail=str([r["date"] for r in all_runs][:3]))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
