"""The Stay list prices the nights somebody searched for.

The owner, 15 October 2026, after the live one-euro test booking (handover
z17, A): the room page priced the chosen dates and the Stay list did not.
Each card multiplied the room's standard rate by the number of nights, so
a search for a night the rate card sold at one euro read "265 for 1 night"
-- and, the case that matters with real guests, a stay that runs into a
seasonal rate was shown at the wrong total on the page where somebody
chooses a room. compute_room_total is the one definition of a room's price
for a stay; the card now reads it.
"""
import re
from datetime import timedelta

from _harness import Suite, db, ensure_room, free_window, visible_text
import _harness

m = _harness.m
TAG = "ZZLISTPRICE"


def _card(html, room_id):
    """The price line on one room's card, as words."""
    at = html.find(f'id="room-{room_id}"')
    if at < 0:
        return ""
    rate = re.search(r'<p class="g-card__rate">(.*?)</p>', html[at:], re.S)
    return visible_text(rate.group(1)) if rate else ""


def _euros(x):
    return "€" + ("{:,.0f}".format(x) if float(x) == int(x) else "{:,.2f}".format(x))


def run():
    s = Suite("The Stay list prices the dates searched")
    room = ensure_room()
    conn = db()
    full = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    rate = full["price_per_night"]
    day = free_window(room["id"], 3, after_days=610, clear_of_rate_overrides=True)
    guest = m.app.test_client()

    def search(nights):
        r = guest.get("/book", query_string={
            "arrival": day.isoformat(),
            "departure": (day + timedelta(days=nights)).isoformat(), "party_size": 1})
        return _card(r.get_data(as_text=True), room["id"])

    try:
        s.section("Nights the rate card prices as standard")
        plain = search(2)
        s.check("two ordinary nights are the standard rate twice",
                _euros(rate * 2) in plain and "for 2 nights" in plain, detail=plain)
        s.check("and the nightly figure beneath is the standard rate",
                f"{_euros(rate)} a night" in plain and "average" not in plain, detail=plain)

        s.section("A night the rate card sells for one euro")
        conn.execute(
            """INSERT INTO room_rate_overrides (room_id, start_date, end_date, price_per_night,
                   label, created_at) VALUES (?, ?, ?, 1, ?, ?)""",
            (room["id"], day.isoformat(), day.isoformat(), TAG, _harness.datetime_now()))
        conn.commit()
        one = search(1)
        s.check("the card says one euro for the night", one.startswith("€1 ") and "for 1 night" in one,
                detail=one)
        s.check("not the standard rate", _euros(rate) not in one, detail=one)

        s.section("A stay that runs from that night into an ordinary one")
        mixed = search(2)
        with m.app.test_request_context("/"):
            total = m.compute_room_total(conn, full, day, day + timedelta(days=2))
        s.check("the total is the rate card's, night by night",
                total == round(1 + rate, 2) and _euros(total) in mixed, detail=f"{total}: {mixed}")
        s.check("and the nightly figure says it is an average, because the nights differ",
                f"{_euros(round(total / 2, 2))} a night on average" in mixed, detail=mixed)
    finally:
        conn.execute("DELETE FROM room_rate_overrides WHERE label = ?", (TAG,))
        conn.commit()
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
