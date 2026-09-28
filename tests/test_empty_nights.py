"""What is unsold, and which of it sits together.

money_ahead answers what is coming in. Nothing answered what is not. The app
has only ever had an occupancy RATE for months already gone — a percentage,
after the fact — and no way at all to see that eleven nights in October are
still free, or which of them sit beside each other.

TWO THINGS THIS FILE IS ACTUALLY ABOUT.

RUNS, NOT A COUNT. Eleven scattered single nights and one eleven-night gap are
the same number and completely different problems. Nobody books a Tuesday on
its own; a week between two bookings is something a guest can take. And a run
shorter than the room's own minimum stay cannot be sold as it stands, which is
a third thing again — a gap to notice rather than to market.

AND WHAT THE FIGURE IS NOT. It is what these nights come to at today's rates,
not lost money. A house is never full, and a number labelled "lost" gets
subtracted from a plan that was never real. The wording is checked here for
the same reason the money figures elsewhere state gross or net: a figure whose
meaning is ambiguous gets used as though it meant the worse thing.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTEMP"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    # blocked_dates carries no label, so ours is found by the window it
    # occupies rather than by a name it cannot hold.
    conn.execute("DELETE FROM blocked_dates WHERE ical_source_id IS NULL "
                 "AND start_date = ?",
                 ((datetime.now(m.LOCAL_TZ).date() + timedelta(days=50)).isoformat(),))
    conn.execute("DELETE FROM room_blocks WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _listed(data, room_id):
    """The nights of one room that the page lists as empty."""
    out = set()
    for r in data["runs"]:
        if r["room"]["id"] != room_id:
            continue
        night, to = date.fromisoformat(r["from"]), date.fromisoformat(r["to"])
        while night <= to:
            out.add(night)
            night += timedelta(days=1)
    return out


def _stay(ref, room_id, start_offset, nights, status="confirmed"):
    conn = db()
    today = datetime.now(m.LOCAL_TZ).date()
    start = today + timedelta(days=start_offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 400, ?)""",
        (room_id, TAG + ref, TAG.lower() + "tok" + ref, TAG + " " + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid", start.isoformat(),
         (start + timedelta(days=nights)).isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return start


def _empty(days=60, today=None):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.empty_nights(conn, days=days,
                                  today=today or datetime.now(m.LOCAL_TZ).date())
    finally:
        conn.close()


def run():
    s = Suite("Which nights are empty")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    conn = db()
    room = conn.execute(
        "SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, name LIMIT 1").fetchone()
    conn.close()
    rate = float(room["price_per_night"] or 0)
    s.check("the room has a rate, so the money below means something", rate > 0,
            detail=str(rate))

    s.section("An empty window is empty")
    base = _empty(days=30)
    mine = [r for r in base["runs"] if r["room"]["id"] == room["id"]]
    s.check("the room shows one unbroken run", len(mine) == 1,
            detail=f"{len(mine)} runs")
    s.check("of the whole window", mine and mine[0]["nights"] == 30,
            detail=str(mine[0]["nights"]) if mine else "")
    s.check("valued at the room's own rate",
            mine and abs(mine[0]["value"] - rate * 30) < 0.01,
            detail=f"{mine[0]['value']} vs {rate * 30}" if mine else "")

    s.section("A booking splits it into two")
    _stay("MID", room["id"], 10, 4)
    after = _empty(days=30)
    mine = [r for r in after["runs"] if r["room"]["id"] == room["id"]]
    s.check("there are now two runs", len(mine) == 2, detail=f"{len(mine)}")
    s.check("with the booked nights gone from the count",
            after["free_nights"] == base["free_nights"] - 4,
            detail=f"{base['free_nights']} -> {after['free_nights']}")
    s.check("and the occupancy moves", after["occupancy"] > base["occupancy"],
            detail=f"{base['occupancy']}% -> {after['occupancy']}%")

    s.section("A request nobody has answered still holds the night")
    # Pending counts. The calendar is already holding it, and showing it as
    # free is how the same night gets offered to two people.
    before_pending = _empty(days=30)["free_nights"]
    _stay("ASKED", room["id"], 20, 2, status="pending")
    s.check("a pending booking takes its nights out too",
            _empty(days=30)["free_nights"] == before_pending - 2,
            detail=f"{before_pending} -> {_empty(days=30)['free_nights']}")

    _stay("GONE", room["id"], 25, 2, status="cancelled")
    s.check("but a cancelled one does not",
            _empty(days=30)["free_nights"] == before_pending - 2,
            detail="a cancelled booking is a free night, which is the whole "
                   "reason to look at this page")

    s.section("Runs are ordered by what can actually be sold")
    runs = _empty(days=30)["runs"]
    s.check("the longest comes first",
            runs and runs[0]["nights"] >= runs[-1]["nights"],
            detail=f"{[r['nights'] for r in runs][:4]}")
    s.check("because nobody books a Tuesday on its own",
            all(runs[i]["nights"] >= runs[i + 1]["nights"] for i in range(len(runs) - 1)),
            detail="eleven scattered nights and one eleven-night gap are the "
                   "same number and different problems")

    s.section("A gap too short to sell is called that")
    conn = db()
    conn.execute("UPDATE rooms SET min_nights = 3 WHERE id = ?", (room["id"],))
    conn.commit()
    conn.close()
    # Two bookings with a single night between them.
    _stay("A", room["id"], 40, 3)
    _stay("B", room["id"], 44, 3)
    data = _empty(days=60)
    stub = [r for r in data["runs"] if r["room"]["id"] == room["id"]
            and r["nights"] == 1]
    s.check("the one-night hole is found", bool(stub),
            detail=str([r["nights"] for r in data["runs"] if r["room"]["id"] == room["id"]]))
    s.check("and marked as below the minimum", stub and stub[0]["below_minimum"],
            detail="a one-night hole between two bookings is usually the shape "
                   "of a booking that could have been moved")
    s.check("so it is not in the list of gaps worth filling",
            not any(r["nights"] == 1 and r["room"]["id"] == room["id"]
                    for r in data["sellable"]),
            detail="marketing a gap nobody can book wastes the send")
    s.check("while a long one still is",
            any(r["nights"] >= 3 and r["room"]["id"] == room["id"]
                for r in data["sellable"]))

    s.section("A blocked night is not an empty one")
    conn = db()
    conn.execute(
        """INSERT INTO blocked_dates (room_id, start_date, end_date)
           VALUES (?, ?, ?)""",
        (room["id"], (today + timedelta(days=50)).isoformat(),
         (today + timedelta(days=53)).isoformat()))
    conn.commit()
    conn.close()
    blocked = _empty(days=60)
    s.check("blocked nights come out of the free count",
            blocked["free_nights"] < data["free_nights"],
            detail=f"{data['free_nights']} -> {blocked['free_nights']} — a room "
                   "being replastered is not a room to market")

    s.section("And nor is a night the owner has blocked")
    # room_blocks: the owner's own, per room -- the renovation, the family
    # visit. blocked_dates above is the channel's. This page used to read only
    # the channel's, so a room the owner had taken off sale was listed as an
    # empty night and its rate counted in the unsold figure. It ends on the
    # checkout morning, like a booking.
    held = [today + timedelta(days=n) for n in (55, 56, 57)]
    free_after = today + timedelta(days=58)
    listed = _listed(blocked, room["id"])
    if s.check("the three nights are listed as empty before the block",
               all(n in listed for n in held + [free_after]),
               detail=str([n.isoformat() for n in held + [free_after] if n not in listed])):
        conn = db()
        conn.execute(
            """INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (room["id"], held[0].isoformat(), free_after.isoformat(), TAG + " family",
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        owners = _empty(days=60)
        listed = _listed(owners, room["id"])
        s.check("its nights come out of the free count",
                owners["free_nights"] == blocked["free_nights"] - 3,
                detail=f"{blocked['free_nights']} -> {owners['free_nights']} — "
                       "a room the owner took off sale is not a night to fill")
        s.check("and their value out of the unsold figure",
                abs(blocked["value_at_rate"] - owners["value_at_rate"] - 3 * rate) < 0.01,
                detail=f"{blocked['value_at_rate']} -> {owners['value_at_rate']}, "
                       f"wanted {3 * rate} less")
        s.check("no run of the room covers a night it is blocked",
                not any(n in listed for n in held),
                detail=str([n.isoformat() for n in held if n in listed]))
        s.check("while the morning the block ends is empty again",
                free_after in listed,
                detail="a block ends on the checkout morning, like a booking")

    s.section("Nothing listed empty is a night the booking check refuses the room")
    # Asked of the real gate, room by room and night by night, rather than
    # reasoned about. A night it refuses for THIS room -- a booking, a channel
    # stay, the owner's block -- must not be offered here as a gap to fill,
    # and a night it would sell must be. A night refused for the whole house
    # (an atelier, an event, a hold) is not a claim this page makes either
    # way, so it is left out rather than pinned.
    conn = db()
    try:
        house = {why for _a, _b, why in m._availability_picture(conn)["house"]}
        rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1").fetchall()
        final = _empty(days=60)
        wrong = []
        for r in rooms:
            listed = _listed(final, r["id"])
            for n in range(60):
                night = today + timedelta(days=n)
                ok, why = m.is_range_available(conn, r["id"], night,
                                               night + timedelta(days=1))
                if ok and night not in listed:
                    wrong.append(f"{r['name']} {night.isoformat()}: sellable, not listed")
                elif not ok and why not in house and night in listed:
                    wrong.append(f"{r['name']} {night.isoformat()}: listed, refused: {why}")
    finally:
        conn.close()
    s.check(f"over 60 nights and {len(rooms)} rooms, the page and the gate agree",
            not wrong, detail=" | ".join(wrong[:3]))

    s.section("The figure says what it is, and what it is not")
    r = oc.get("/management/empty-nights?days=60")
    body = r.get_data(as_text=True)
    s.check("the page opens", r.status_code == 200, detail=str(r.status_code))
    s.check("it shows what is unsold", "unsold" in body.lower())
    # The wording matters as much as the number. "Lost revenue" gets
    # subtracted from a plan that was never real.
    s.check("it says the money is at today's rates",
            "today's rate" in body.lower(), detail="a figure whose meaning is "
                                                   "ambiguous gets read as the worse one")
    # The PARAGRAPH, not just the words somewhere on the page. The band's own
    # hint says "not lost money" too, so a loose search stays green while the
    # explanation underneath is gutted -- which is what the control found.
    s.check("and the paragraph explains why it is not lost money",
            "gets subtracted from a plan that was never real" in body,
            detail="a house is never full; the reasoning is the part that "
                   "stops the number being misread")
    s.check("the band says it too, in the space it has",
            "not lost money" in body.lower(),
            detail="both say it because the figure is read in both places")
    s.check("it points at the waitlist, which is the warmest list there is",
            "waitlist" in body.lower())

    s.section("Who may see it")
    r = ec.get("/management/empty-nights", follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    r = m.app.test_client().get("/management/empty-nights", follow_redirects=False)
    s.check("nor a stranger", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")

    conn = db()
    conn.execute("UPDATE rooms SET min_nights = ? WHERE id = ?",
                 (room["min_nights"], room["id"]))
    conn.commit()
    conn.close()
    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
