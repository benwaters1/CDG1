"""What is unsold, and which of it sits together.

money_ahead answers what is coming in. Nothing answered what is not. The app
has only ever had an occupancy RATE for months already gone — a percentage,
after the fact — and no way at all to see that eleven nights in October are
still free, or which of them sit beside each other.

THREE THINGS THIS FILE IS ACTUALLY ABOUT.

RUNS, NOT A COUNT. Eleven scattered single nights and one eleven-night gap are
the same number and completely different problems. Nobody books a Tuesday on
its own; a week between two bookings is something a guest can take. And a run
shorter than the room's own minimum stay cannot be sold as it stands, which is
a third thing again — a gap to notice rather than to market.

WHAT THE FIGURE IS NOT. It is what these nights come to at today's rates,
not lost money. A house is never full, and a number labelled "lost" gets
subtracted from a plan that was never real. The wording is checked here for
the same reason the money figures elsewhere state gross or net: a figure whose
meaning is ambiguous gets used as though it meant the worse thing.

AND A NIGHT HELD FOR THE WHOLE HOUSE IS NOT AN EMPTY ONE. An atelier, a
confirmed event or a live provisional hold takes every room, and the booking
check refuses all of them on it, last day included. The page used to list
those nights as empty in every room, value them at today's rates and offer
them as gaps to fill from the waitlist. They are now out of the count, the
value, the runs and the nights the rooms could have sold, and named on their
own so the owner can see why a week is missing. A hold that was released or
has lapsed holds nothing, and that is checked by itself: if the picture got
it wrong, the page and the booking check would agree with each other and
both be wrong.

WHERE, NOT WHEN. Every section works on a room of its own, in a window the
booking check itself says is clear -- found, not an offset from today. The
fresh database seeds ateliers on real published dates, and on 28 September
2026 one sat across today+25 to today+29. Once house-wide nights came out of
the runs, "the first 30 days are one unbroken run" was true or false by the
calendar, which is a check about the date it ran on and not about the code.
"""
import re
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZEMPTY"
ONE = timedelta(days=1)


def _cleanup():
    """Every row this suite writes carries the tag, or sits on a room that does."""
    conn = db()
    rooms = [r["id"] for r in conn.execute(
        "SELECT id FROM rooms WHERE name LIKE ?", (TAG + "%",)).fetchall()]
    if rooms:
        marks = ",".join("?" * len(rooms))
        # blocked_dates carries no label, so ours is found by the room it sits
        # on, which is ours alone.
        conn.execute(f"DELETE FROM blocked_dates WHERE room_id IN ({marks})", rooms)
        conn.execute(f"DELETE FROM room_blocks WHERE room_id IN ({marks})", rooms)
        conn.execute(f"DELETE FROM bookings WHERE room_id IN ({marks})", rooms)
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_holds WHERE note LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM event_holds WHERE event_id IN
                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_sessions WHERE workshop_id IN
                    (SELECT id FROM workshops WHERE title LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _leftovers(room_ids):
    """Every row this suite wrote, counted by its tag or by the room it was on."""
    conn = db()
    try:
        like = TAG + "%"
        count = lambda sql, args: conn.execute(sql, args).fetchone()[0]
        out = {
            "rooms": count("SELECT COUNT(*) FROM rooms WHERE name LIKE ?", (like,)),
            "bookings": count("SELECT COUNT(*) FROM bookings WHERE reference_code LIKE ?",
                              (like,)),
            "events": count("SELECT COUNT(*) FROM event_inquiries WHERE contact_name LIKE ?",
                            (like,)),
            "holds": count("SELECT COUNT(*) FROM event_holds WHERE note LIKE ?", (like,)),
            "workshops": count("SELECT COUNT(*) FROM workshops WHERE title LIKE ?", (like,)),
            "sessions": count("SELECT COUNT(*) FROM workshop_sessions WHERE notes LIKE ?",
                              (like,)),
        }
        if room_ids:
            marks = ",".join("?" * len(room_ids))
            for table in ("blocked_dates", "room_blocks", "bookings"):
                out[f"{table} on our rooms"] = count(
                    f"SELECT COUNT(*) FROM {table} WHERE room_id IN ({marks})", room_ids)
        return {k: v for k, v in out.items() if v}
    finally:
        conn.close()


def _room(conn, suffix, rate, sort_order):
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy,
           price_per_night, sort_order, min_nights) VALUES (?, ?, 1, 2, ?, ?, 1)""",
        (f"{TAG} {suffix}", _harness.secrets_token(), rate, sort_order))
    conn.commit()
    return conn.execute("SELECT * FROM rooms WHERE name = ?",
                        (f"{TAG} {suffix}",)).fetchone()


def _listed(data, room_id):
    """The nights of one room that the page lists as empty."""
    out = set()
    for r in data["runs"]:
        if r["room"]["id"] != room_id:
            continue
        night, to = date.fromisoformat(r["from"]), date.fromisoformat(r["to"])
        while night <= to:
            out.add(night)
            night += ONE
    return out


def _stay(ref, room_id, start, nights, status="confirmed"):
    conn = db()
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


def _empty(days, today):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.empty_nights(conn, days=days, today=today)
    finally:
        conn.close()


def run():
    s = Suite("Which nights are empty")
    _cleanup()
    mine = []
    try:
        _run(s, mine)
    finally:
        # After a failure too. The database is shared by every suite in the
        # run, and a confirmed wedding left behind closes the whole house for
        # somebody else's test.
        _cleanup()
    left = _leftovers(mine)
    s.check("nothing this suite wrote is left behind", not left, detail=str(left))
    return s


def _run(s, mine):
    oc, ec, owner, emp = clients()
    today = _harness.house_today()

    conn = db()
    room = _room(conn, "Room", 180.0, 994)
    # A second room of ours, booked under the atelier further down, for the
    # one case where a night held for the house is ALSO a night already sold.
    booked = _room(conn, "Booked", 220.0, 995)
    conn.close()
    mine += [room["id"], booked["id"]]
    rate = float(room["price_per_night"])

    # Asked of the booking check, not an offset: the first 100 nights from
    # today that it would sell this room -- no booking, no block, and nothing
    # held for the whole house. Everything below happens inside it.
    base = _harness.free_window(room["id"], 100, after_days=0)

    def d(offset):
        return base + offset * ONE

    s.section("An empty window is empty")
    first = _empty(30, base)
    mine_runs = [r for r in first["runs"] if r["room"]["id"] == room["id"]]
    s.check("nothing in the window is held for the whole house",
            not first["house"] and first["house_nights"] == 0,
            detail=f"{first['house']} -- the window was found clear, so this is "
                   "the page disagreeing with the booking check")
    s.check("the room shows one unbroken run", len(mine_runs) == 1,
            detail=f"{len(mine_runs)} runs, from {base.isoformat()}")
    s.check("of the whole window", mine_runs and mine_runs[0]["nights"] == 30,
            detail=str(mine_runs[0]["nights"]) if mine_runs else "")
    s.check("valued at the room's own rate",
            mine_runs and abs(mine_runs[0]["value"] - rate * 30) < 0.01,
            detail=f"{mine_runs[0]['value']} vs {rate * 30}" if mine_runs else "")

    s.section("A booking splits it into two")
    _stay("MID", room["id"], d(10), 4)
    after = _empty(30, base)
    mine_runs = [r for r in after["runs"] if r["room"]["id"] == room["id"]]
    s.check("there are now two runs", len(mine_runs) == 2, detail=f"{len(mine_runs)}")
    s.check("with the booked nights gone from the count",
            after["free_nights"] == first["free_nights"] - 4,
            detail=f"{first['free_nights']} -> {after['free_nights']}")
    s.check("and the occupancy moves", after["occupancy"] > first["occupancy"],
            detail=f"{first['occupancy']}% -> {after['occupancy']}%")

    s.section("A request nobody has answered still holds the night")
    # Pending counts. The calendar is already holding it, and showing it as
    # free is how the same night gets offered to two people.
    before_pending = _empty(30, base)["free_nights"]
    _stay("ASKED", room["id"], d(20), 2, status="pending")
    now_free = _empty(30, base)["free_nights"]
    s.check("a pending booking takes its nights out too",
            now_free == before_pending - 2,
            detail=f"{before_pending} -> {now_free}")

    _stay("GONE", room["id"], d(25), 2, status="cancelled")
    now_free = _empty(30, base)["free_nights"]
    s.check("but a cancelled one does not",
            now_free == before_pending - 2,
            detail=f"{before_pending} -> {now_free}: a cancelled booking is a free "
                   "night, which is the whole reason to look at this page")

    s.section("Runs are ordered by what can actually be sold")
    runs = _empty(30, base)["runs"]
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
    _stay("A", room["id"], d(40), 3)
    _stay("B", room["id"], d(44), 3)
    data = _empty(60, base)
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
        (room["id"], d(50).isoformat(), d(53).isoformat()))
    conn.commit()
    conn.close()
    blocked = _empty(60, base)
    s.check("blocked nights come out of the free count",
            blocked["free_nights"] == data["free_nights"] - 3,
            detail=f"{data['free_nights']} -> {blocked['free_nights']} — a stay "
                   "on another channel is not a night to market")

    s.section("And nor is a night the owner has blocked")
    # room_blocks: the owner's own, per room -- the renovation, the family
    # visit. blocked_dates above is the channel's. This page used to read only
    # the channel's, so a room the owner had taken off sale was listed as an
    # empty night and its rate counted in the unsold figure. It ends on the
    # checkout morning, like a booking.
    held = [d(55), d(56), d(57)]
    free_after = d(58)
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
        owners = _empty(60, base)
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

    _house(s, oc, today, base, d, room, booked)
    _agree(s, today, base)

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


def _house(s, oc, today, base, d, room, booked):
    """Ateliers, confirmed events and live holds: every room, last day included."""
    s.section("A night held for the whole house is not an empty one")
    stamp = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)
    conn = db()
    try:
        # Booked BEFORE anything is held: a night already sold stays sold when
        # the house is held over it, as sellable_nights counts it.
        _stay("UNDER", booked["id"], d(62), 3)
        before = _empty(100, base)
        listed = _listed(before, room["id"])
        clear = [n for n in range(61, 96) if d(n) not in listed]
        if not s.check("the nights about to be held are empty before anything holds them",
                       not clear and not before["house"],
                       detail=f"not listed: {[d(n).isoformat() for n in clear][:4]}, "
                              f"held: {before['house'][:2]}"):
            return

        def event(ref, start, end, status="confirmed", kind="wedding"):
            conn.execute(
                """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                   contact_name, contact_email, preferred_date, end_date, status,
                   created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"{TAG}-{ref}", f"tok{TAG}{ref}", kind, f"{TAG} {ref}",
                 f"{TAG.lower()}@example.invalid", start.isoformat(),
                 end.isoformat() if end else None, status, stamp))
            conn.commit()
            return conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                                (f"{TAG}-{ref}",)).fetchone()["id"]

        def hold(event_id, start, end, *, expires, released=None):
            conn.execute(
                """INSERT INTO event_holds (event_id, start_date, end_date, expires_at,
                   note, created_at, released_at, released_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, start.isoformat(), end.isoformat(), expires.isoformat(),
                 TAG, stamp, released.isoformat() if released else None,
                 "decided" if released else None))
            conn.commit()

        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person,
               default_capacity, active, sort_order, created_at)
               VALUES (?, '', 100, 10, 1, 90, ?)""", (f"{TAG} Atelier", stamp))
        wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                           (f"{TAG} Atelier",)).fetchone()["id"]
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
               capacity, notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
            (wid, d(62).isoformat(), d(64).isoformat(), TAG, stamp))
        conn.commit()
        event("WED", d(68), d(69))
        # An enquiry nobody has confirmed holds nothing of its own; the hold on
        # it does, while it is live. A confirmed dinner overlaps the hold's
        # FIRST night, so that one night is held twice -- not its last, which
        # has to be the hold's alone for "its last day too" to be able to fail.
        asking = event("ASK", d(93), None, status="new", kind="photoshoot")
        hold(asking, d(76), d(78), expires=now + timedelta(days=7))
        event("DINNER", d(75), d(76), kind="dinner")
        hold(asking, d(84), d(86), expires=now + timedelta(days=7),
             released=now - timedelta(hours=1))
        hold(asking, d(88), d(90), expires=now - timedelta(hours=1))
    finally:
        conn.close()

    held = [62, 63, 64, 68, 69, 75, 76, 77, 78]
    after = _empty(100, base)
    rooms = {r["room"]["id"]: r["room"] for r in before["runs"] + after["runs"]}

    def listed_anywhere(offsets):
        return [f"{rooms[rid]['name']} {d(o).isoformat()}" for rid in rooms
                for o in offsets if d(o) in _listed(after, rid)]

    def not_listed(offsets):
        now_listed = _listed(after, room["id"])
        return [d(o).isoformat() for o in offsets if d(o) not in now_listed]

    s.check("every night of an atelier is out of every room's runs, its last day too",
            not listed_anywhere([62, 63, 64]),
            detail=f"still listed: {listed_anywhere([62, 63, 64])[:3]} -- the booking "
                   "check refuses every room on these, and the page offered them")
    s.check("while the night before and the morning after are empty",
            not not_listed([61, 65]), detail=str(not_listed([61, 65])))
    s.check("both days of a confirmed event are out",
            not listed_anywhere([68, 69]) and not not_listed([67, 70]),
            detail=f"listed {listed_anywhere([68, 69])[:3]}, "
                   f"missing {not_listed([67, 70])}")
    s.check("and every night of a live hold, its last day too",
            not listed_anywhere([76, 77, 78]) and not not_listed([79]),
            detail=f"listed {listed_anywhere([76, 77, 78])[:3]}, "
                   f"missing {not_listed([79])}")
    s.check("and of the dinner overlapping it, with the night before it empty",
            not listed_anywhere([75]) and not not_listed([74]),
            detail=f"listed {listed_anywhere([75])[:3]}, missing {not_listed([74])}")
    # On their own, not left to the sweep: a picture that forgot released_at
    # would take these out of the page AND refuse them at the gate, and two
    # wrong answers agree perfectly.
    s.check("a hold somebody has released holds nothing",
            not not_listed([84, 85, 86]), detail=str(not_listed([84, 85, 86])))
    s.check("nor one whose time has run out",
            not not_listed([88, 89, 90]), detail=str(not_listed([88, 89, 90])))
    s.check("nor an enquiry nobody has confirmed", not not_listed([93]),
            detail=str(not_listed([93])))

    # What was empty under the holds, room by room, read off the page as it
    # stood before them. Our rooms are clear there by construction; any other
    # room is whatever the database holds, so it is counted rather than
    # assumed.
    was_free = {rid: sum(1 for o in held if d(o) in _listed(before, rid)) for rid in rooms}
    freed = sum(was_free.values())
    value = sum(was_free[rid] * float(rooms[rid]["price_per_night"] or 0) for rid in rooms)
    s.check("our own room had all nine held nights empty, and the booked one six",
            was_free.get(room["id"]) == 9 and was_free.get(booked["id"]) == 6,
            detail=f"{was_free.get(room['id'])}, {was_free.get(booked['id'])}")
    s.check("the free count drops by exactly the room-nights that were empty under them",
            before["free_nights"] - after["free_nights"] == freed,
            detail=f"{before['free_nights']} -> {after['free_nights']}, wanted "
                   f"{freed} fewer")
    s.check("and their value comes out of the unsold figure",
            abs(before["value_at_rate"] - after["value_at_rate"] - value) < 0.01,
            detail=f"{before['value_at_rate']} -> {after['value_at_rate']}, wanted "
                   f"{value} less -- a night never for sale is not money unsold")
    s.check("and out of the nights the rooms could have sold",
            before["possible_nights"] - after["possible_nights"] == freed,
            detail=f"{before['possible_nights']} -> {after['possible_nights']}, "
                   f"wanted {freed} fewer -- left in, an atelier week reads as a "
                   "good week for the rooms")
    sold_before = before["possible_nights"] - before["free_nights"]
    sold_after = after["possible_nights"] - after["free_nights"]
    s.check("while a night already sold under them is still a night sold",
            sold_before == sold_after,
            detail=f"sold or blocked: {sold_before} -> {sold_after}")
    s.check("a night held twice is one night, not two",
            after["house_nights"] == len(held),
            detail=f"{after['house_nights']} held, wanted {len(held)} -- the dinner"
                   "'s last night is the hold's first")

    s.section("And each is named, so a missing week has its reason")
    want = [(d(62), d(64), 3, f"Held for {TAG} Atelier"),
            (d(68), d(69), 2, "Held for a private wedding"),
            (d(75), d(76), 2, "Held for a private dinner"),
            (d(76), d(78), 3, "Provisionally held for a private photoshoot")]
    got = [(date.fromisoformat(h["from"]), date.fromisoformat(h["to"]), h["nights"],
            h["label"]) for h in after["house"]]
    s.check("every live hold, in date order, in the calendar's words",
            got == want, detail=f"got {[(a.isoformat(), b.isoformat(), n, w) for a, b, n, w in got]}")
    edge = _empty(5, d(63))
    s.check("one that runs in from before the window starts where the window does",
            [(h["from"], h["to"], h["nights"]) for h in edge["house"]]
            == [(d(63).isoformat(), d(64).isoformat(), 2)],
            detail=str(edge["house"]))
    edge = _empty(2, d(61))
    s.check("and one that runs past its end stops at its last night",
            [(h["from"], h["to"], h["nights"]) for h in edge["house"]]
            == [(d(62).isoformat(), d(62).isoformat(), 1)] and edge["house_nights"] == 1,
            detail=str(edge["house"]))

    # Through the pages. They look from today, so the window is stretched to
    # reach ours; both routes cap it, and a clear window found further out than
    # that is said rather than skipped.
    reach = (base - today).days + 100
    if s.check("the pages can see as far as the held nights",
               reach <= 365, detail=f"{reach} days out"):
        body = oc.get(f"/management/empty-nights?days={reach}").get_data(as_text=True)
        s.check("the empty-nights page lists what holds the house",
                "Held for the whole house" in body and f"Held for {TAG} Atelier" in body,
                detail="the owner sees a week missing and nothing saying why")
        s.check("and its band says the held nights are left out of the count",
                "held for the whole house" in body.split("Gaps worth filling")[0],
                detail="the band is what gets read")
        body = oc.get(f"/management/fill-a-gap?days={reach}").get_data(as_text=True)
        offered = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in re.findall(
            r"from=(\d{4}-\d{2}-\d{2})&(?:amp;)?to=(\d{4}-\d{2}-\d{2})", body)]
        bad = [f"{a.isoformat()}..{b.isoformat()}" for a, b in offered
               if any(a <= d(o) <= b for o in held)]
        s.check("fill-a-gap offers runs", bool(offered), detail="no run links on the page")
        s.check("and none of them crosses a night held for the house", not bad,
                detail=f"{bad[:3]} -- offered from the waitlist, then refused at the gate")
        s.check("and it says what it left out", "held for the whole house" in body)

        # A window the house holds from end to end has nothing a room could
        # have sold, and "0%" in red would call that a failure to sell. The
        # page looks from today, so today is moved for the one request, as
        # test_receipt_sequence does. Another room booked on the wedding would
        # leave something sellable, so the answer wanted is worked out first.
        whole = _empty(2, d(68))
        was_today = m.house_today
        m.house_today = lambda: d(68)
        try:
            body = oc.get("/management/empty-nights?days=2").get_data(as_text=True)
        finally:
            m.house_today = was_today
        cell = next((c for c in body.split('class="overview-cell')
                     if 'overview-label">Occupancy<' in c), "")
        shown = re.search(r'overview-value">\s*([^<]*?)\s*<', cell)
        shown = shown.group(1) if shown else None
        alert = "overview-cell-alert" in cell.split(">", 1)[0]
        if whole["possible_nights"] == 0:
            ok = shown == "—" and not alert
        else:
            ok = shown == f"{whole['occupancy']}%"
        s.check("a window held end to end shows no occupancy, rather than 0% in red",
                ok, detail=f"{whole['possible_nights']} sellable, shown {shown!r}, "
                           f"alert={alert}")


def _agree(s, today, base):
    s.section("Nothing listed empty is a night the booking check refuses the room")
    # Asked of the real gate, room by room and night by night, rather than
    # reasoned about. A night it refuses -- for this room or for the whole
    # house -- must not be offered here as a gap to fill, and a night it would
    # sell must be. Twice: over the suite's own window, with everything above
    # still in it, and over the next sixty days as the database has them.
    conn = db()
    try:
        house = {why for _a, _b, why in m._availability_picture(conn)["house"]}
        rooms = conn.execute("SELECT id, name FROM rooms WHERE active = 1").fetchall()
        wrong, house_refused = [], 0
        for start, days in ((base, 100), (today, 60)):
            page = _empty(days, start)
            for r in rooms:
                listed = _listed(page, r["id"])
                for n in range(days):
                    night = start + n * ONE
                    ok, why = m.is_range_available(conn, r["id"], night, night + ONE)
                    if not ok and why in house:
                        house_refused += 1
                    if ok and night not in listed:
                        wrong.append(f"{r['name']} {night.isoformat()}: sellable, not listed")
                    elif not ok and night in listed:
                        wrong.append(f"{r['name']} {night.isoformat()}: listed, refused: {why}")
    finally:
        conn.close()
    s.check(f"over 160 nights and {len(rooms)} rooms, the page and the gate agree",
            not wrong, detail=" | ".join(wrong[:3]))
    # A sweep that never met a house-wide night proves nothing about them. Nine
    # is our own room under the holds above; the seeded ateliers add more on
    # the days one falls inside the next sixty, and are not counted on.
    s.check("and the sweep met nights the whole house is held",
            house_refused >= 9,
            detail=f"{house_refused} room-nights refused for the house")


if __name__ == "__main__":
    print(run().report())
