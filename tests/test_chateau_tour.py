# -*- coding: utf-8 -*-
"""The château tour as an extra, and what "at most one" has to mean.

From the 1 October launch checklist: "A tour of the château", €50, per
booking, at most one. Every workshop already includes the tour; this is the
same tour sold with a stay.

What is checked, and why:

  IT GOES IN ONCE. It arrives through ADDED_EXTRAS, the mechanism for adding
  to the catalogue of a house already running, so the live site gets it on
  its next start -- and one the owner reprices or deletes stays as they left
  it. test_extras_at_booking proves the mechanism; this proves the entry.

  PER BOOKING MEANS PER BOOKING. max_qty was checked against one request at a
  time: the manage page refused "two tours" in one click and accepted a
  second tour on a booking that already had one. Ticked when booking and
  added again afterwards, that is two tours on one bill, for a tour sold at
  one per booking. So the manage page now counts what the booking holds,
  stops offering what it holds enough of, and refuses one posted anyway.

  A CANCELLED LINE IS NOT HELD. A guest whose tour was cancelled can book it
  again; and an extra with no limit is exactly as it was.

Runs on the throwaway copy; mail and Stripe are stood down by the harness.
"""
import re
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from _harness import Suite, clients, db, flashes, free_window
import _harness

m = _harness.m
GUEST_IP = "203.0.113.58"
EMAIL = "ztest-tour-{}@example.test"
TOUR = "A tour of the château"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM bookings WHERE guest_email LIKE 'ztest-tour-%'").fetchall()]
    for bid in ids:
        conn.execute("DELETE FROM stock_movements WHERE booking_extra_id IN "
                     "(SELECT id FROM booking_extras WHERE category = 'room' "
                     "AND booking_id = ?)", (bid,))
        conn.execute("DELETE FROM booking_extras WHERE category = 'room' AND booking_id = ?",
                     (bid,))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE 'ztest-tour-%'")
    conn.execute("DELETE FROM extras WHERE name LIKE 'ztest-tour-%'")
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    conn.close()


def _one(sql, args=()):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _guest():
    client = m.app.test_client()
    client.environ_base["REMOTE_ADDR"] = GUEST_IP
    return client


def _book(room, arrival, departure, tag, extras=(), **form):
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()
    conn.close()
    data = {"arrival_date": arrival.isoformat(), "departure_date": departure.isoformat(),
            "guest_name": "Ztest Tour", "guest_email": EMAIL.format(tag),
            "party_size": "2", "agree_terms": "on"}
    data.update(form)
    data = MultiDict(list(data.items()) + [("extras", str(i)) for i in extras])
    return _guest().post(f"/book/{room['id']}", data=data, follow_redirects=True)


def _booking(tag):
    return _one("SELECT * FROM bookings WHERE guest_email = ? ORDER BY id DESC LIMIT 1",
                (EMAIL.format(tag),))


def _held(booking_id, extra_id):
    row = _one("""SELECT COALESCE(SUM(quantity), 0) AS n FROM booking_extras
                   WHERE category = 'room' AND booking_id = ? AND extra_id = ?
                     AND status != 'cancelled'""", (booking_id, extra_id))
    return row["n"]


def _add(token, extra_id, quantity=1, **extra):
    data = {"action": "add_extra", "extra_id": str(extra_id), "quantity": str(quantity)}
    data.update(extra)
    return _guest().post(f"/book/manage/{token}", data=data, follow_redirects=True)


def _offered(token, extra_id):
    page = _guest().get(f"/book/manage/{token}").get_data(as_text=True)
    return f'name="extra_id" value="{extra_id}"' in page


def run():
    s = Suite("The château tour, at most one per booking")
    _cleanup()
    clients()
    room = _one("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1")

    s.section("The tour is in the catalogue, once")
    # Tested as a house WITHOUT a tour receives it, which is the live site's
    # case. The copy of the database this runs on may already carry a tour
    # somebody made by hand; seed_added_extras rightly leaves a same-named row
    # alone, so that row is set aside for the length of the suite and put back.
    conn = db()
    set_aside = conn.execute("SELECT id FROM extras WHERE name = ?", (TOUR,)).fetchone()
    had_key = conn.execute("SELECT value FROM app_settings "
                           "WHERE key = 'added_extra_chateau_tour'").fetchone()
    if set_aside:
        conn.execute("UPDATE extras SET name = 'zaside the hand-made tour' WHERE id = ?",
                     (set_aside["id"],))
    conn.execute("DELETE FROM app_settings WHERE key = 'added_extra_chateau_tour'")
    conn.commit()
    added = m.seed_added_extras(conn)
    conn.commit()
    conn.close()
    try:
        _the_rest(s, room, added)
    finally:
        conn = db()
        conn.execute("DELETE FROM booking_extras WHERE extra_id IN "
                     "(SELECT id FROM extras WHERE name = ?)", (TOUR,))
        conn.execute("DELETE FROM extras WHERE name = ?", (TOUR,))
        if set_aside:
            conn.execute("UPDATE extras SET name = ? WHERE id = ?", (TOUR, set_aside["id"]))
        if had_key:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO NOTHING",
                         ("added_extra_chateau_tour", had_key["value"]))
        conn.commit()
        conn.close()
        _cleanup()
    return s


def _the_rest(s, room, added):
    tour = _one("SELECT * FROM extras WHERE name = ?", (TOUR,))
    s.check("it goes into a house that has none", added == 1 and tour is not None,
            detail=f"seed_added_extras added {added}")
    s.check("at €50, at most one",
            tour and tour["price"] == 50.0 and tour["max_qty"] == 1,
            detail=str(dict(tour)) if tour else "")
    s.check("for guests to add, as an activity, asking which day",
            tour and tour["guest_bookable"] == 1 and tour["active"] == 1
            and tour["category"] == "activity" and tour["ask_when"] == 1)
    s.check("and it says the price is for the booking, not each person",
            tour and "One price for the booking" in (tour["description"] or ""))
    key = _one("SELECT 1 FROM app_settings WHERE key = 'added_extra_chateau_tour'")
    s.check("remembered by its key, so a delete or a reprice sticks", key is not None)
    if not tour:
        return

    s.section("On the booking form")
    arrival = free_window(room["id"], nights=3, after_days=60,
                          clear_of_ateliers=True, clear_of_rate_overrides=True)
    departure = arrival + timedelta(days=3)
    page = _guest().get(f"/book/{room['id']}?arrival={arrival.isoformat()}"
                        f"&departure={departure.isoformat()}").get_data(as_text=True)
    s.check("it is offered, at its price",
            "A tour of the château" in page and "€50.00" in page)
    s.check("and asks which day", f'name="extra_when_{tour["id"]}"' in page)

    s.section("Booked with a stay, it is on the booking once")
    second_day = arrival + timedelta(days=1)
    _book(room, arrival, departure, "first", extras=[tour["id"]],
          **{f"extra_when_{tour['id']}": second_day.isoformat()})
    b = _booking("first")
    s.check("the booking is made", b is not None)
    if b is None:
        return
    line = _one("""SELECT * FROM booking_extras WHERE category = 'room'
                    AND booking_id = ? AND extra_id = ?""", (b["id"], tour["id"]))
    s.check("one tour on it, for the day chosen",
            line is not None and line["quantity"] == 1
            and line["scheduled_for"] == second_day.isoformat(),
            detail=str(dict(line)) if line else "no line")

    s.section("The manage page does not sell it twice")
    s.check("it is no longer offered", not _offered(b["manage_token"], tour["id"]),
            detail="a booking holding one tour was still being offered another")
    r = _add(b["manage_token"], tour["id"],
             extra_when=(arrival + timedelta(days=2)).isoformat())
    s.check("one posted anyway is refused", _held(b["id"], tour["id"]) == 1,
            detail=f"{_held(b['id'], tour['id'])} tours on the booking")
    s.check("saying it is already on the booking",
            any("already on this booking" in f for f in flashes(r)),
            detail=str(flashes(r)))

    s.section("A cancelled tour is not held")
    conn = db()
    conn.execute("UPDATE booking_extras SET status = 'cancelled' WHERE id = ?", (line["id"],))
    conn.commit()
    conn.close()
    s.check("once cancelled, it is offered again", _offered(b["manage_token"], tour["id"]))
    _add(b["manage_token"], tour["id"], extra_when=second_day.isoformat())
    s.check("and can be booked again", _held(b["id"], tour["id"]) == 1,
            detail=f"{_held(b['id'], tour['id'])} live tours")

    s.section("A limit above one counts what is held, and no limit is no limit")
    conn = db()
    two = conn.execute(
        "INSERT INTO extras (name, price, category, guest_bookable, active, max_qty) "
        "VALUES ('ztest-tour-two-max', 10, 'other', 1, 1, 2)").lastrowid
    free = conn.execute(
        "INSERT INTO extras (name, price, category, guest_bookable, active) "
        "VALUES ('ztest-tour-no-max', 10, 'other', 1, 1)").lastrowid
    conn.commit()
    conn.close()
    _add(b["manage_token"], two, 1)
    r = _add(b["manage_token"], two, 2)
    s.check("one held and two more asked for is refused", _held(b["id"], two) == 1,
            detail=f"{_held(b['id'], two)} held")
    s.check("naming the most it can do",
            any("2 is the most we can do" in f for f in flashes(r)), detail=str(flashes(r)))
    _add(b["manage_token"], two, 1)
    s.check("but the second, within the limit, is taken", _held(b["id"], two) == 2)
    _add(b["manage_token"], free, 1)
    _add(b["manage_token"], free, 1)
    s.check("an extra with no limit can be added as often as asked",
            _held(b["id"], free) == 2, detail=f"{_held(b['id'], free)} held")


if __name__ == "__main__":
    print(run().report())
