"""Asking for another room, without inventing a payment path.

A family decides on a second room three weeks after booking, and that decision
travelled by email. This is the last of the nine unlanded guest macros, and it
was deliberately left until the end because it is the one that touches money.

THE SKETCH'S COPY SAID "availability is checked when you submit; if the room
has gone since you looked, nothing is charged" — which implies a card is
otherwise taken. Building a Stripe flow into the manage page because a
placeholder hinted at one is how a payment path gets built by accident.

So it ASKS. The room becomes a pending request on the same party, priced by
the same create_booking every other room goes through, and the house confirms
it in the normal queue. Nothing here takes a card, and the copy says so
plainly instead of hinting otherwise. That is the first thing checked below,
because it is the thing that would be quietly wrong.

IT TAKES THE LOCK. claim_range, the same guard the booking form uses, because
this is a check followed by a write — and a guest browsing free rooms is more
likely than most to be looking at one somebody else is mid-way through taking.

AND IT SAYS WHAT ELSE IS FREE when the room has gone. That is the one promise
in the sketch's copy worth keeping: being told no with nothing to do next is
the email this exists to stop.
"""
from datetime import timedelta

from _harness import Suite, db, house_today

import _harness

m = _harness.m
TAG = "ZZADD"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM booking_parties WHERE id IN "
        "(SELECT party_id FROM bookings WHERE reference_code LIKE ? "
        " AND party_id IS NOT NULL)", (TAG + "%",))
    conn.execute(
        "DELETE FROM bookings WHERE reference_code LIKE ? "
        "OR special_requests LIKE ?", (TAG + "%", "%" + TAG + "%"))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("asking for another room")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    _cleanup(conn)

    def add_room_row(name, price):
        conn.execute(
            """INSERT INTO rooms (name, description, max_occupancy, max_adults,
                       price_per_night, min_nights, active, sort_order,
                       export_token)
               VALUES (?, '', 4, 4, ?, 1, 1, 96, ?)""",
            (TAG + " " + name, price,
             f"tok-{TAG}-{name}".lower().replace(" ", "-")))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    theirs = add_room_row("Rose", 200)
    spare = add_room_row("Blue", 150)
    taken = add_room_row("Gone", 300)
    # A room kept back and used by nothing else, so the cancelled-booking
    # check is refused by the status guard rather than by the room being gone.
    held = add_room_row("Held", 175)

    # A window where these rooms really are free, found rather than guessed.
    # The house holds every room for a workshop at several points in the year,
    # so a fixed number of days out is a fixture that breaks the day somebody
    # adds an atelier -- and breaks by silently offering nothing, which reads
    # as this feature being broken rather than as a bad fixture.
    conn.commit()
    arrival = departure = None
    for offset in range(60, 900, 10):
        a = today + timedelta(days=offset)
        d = a + timedelta(days=3)
        free = {r["id"] for r in m.rooms_free_for(conn, a, d)}
        if {theirs, spare, taken, held} <= free:
            arrival, departure = a, d
            break
    if not arrival:
        s.section("Setup")
        s.check("a window exists where all three rooms are free", False,
                detail="every window in the next two and a half years has one "
                       "of them held. Reported rather than skipped: the rest "
                       "of this file would pass on an empty list.")
        _cleanup(conn)
        conn.close()
        return s
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, guest_phone, arrival_date,
                   departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, '+33 1 23', ?, ?, 2, 'confirmed', 600, ?)""",
        (theirs, TAG + "MAIN", TAG.lower() + "-main", TAG + " Beauchamp",
         f"{TAG}.b@example.invalid".lower(), arrival.isoformat(),
         departure.isoformat(), now))
    main_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    # Somebody else already has the third room for those nights.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token,
                   guest_name, guest_email, arrival_date, departure_date,
                   party_size, status, total_price, created_at)
           VALUES (?, ?, ?, 'Someone Else', ?, ?, ?, 2, 'confirmed', 900, ?)""",
        (taken, TAG + "OTHER", TAG.lower() + "-other",
         f"{TAG}.o@example.invalid".lower(), arrival.isoformat(),
         departure.isoformat(), now))
    conn.commit()

    guest = m.app.test_client()
    manage = f"/book/manage/{TAG.lower()}-main"

    s.section("Only the rooms actually free are offered")
    s.check("the window really is clear to begin with",
            len(m.rooms_free_for(conn, arrival, departure)) >= 3,
            detail=f"{arrival} to {departure}")
    free = m.rooms_free_for(conn, arrival, departure, {theirs})
    names = {r["name"] for r in free}
    s.check("the spare room is offered", TAG + " Blue" in names)
    s.check("the one somebody else has is not", TAG + " Gone" not in names,
            detail="offering it produces a guest who picks it and is refused")
    s.check("and their own is not offered back to them",
            TAG + " Rose" not in names,
            detail="they are already in it")

    body = guest.get(manage).get_data(as_text=True)
    s.check("the form is on their page", "Add another room" in body)
    s.check("with the free room on it", TAG + " Blue" in body)
    s.check("and not the taken one", TAG + " Gone" not in body)

    s.section("It says plainly that nothing is charged")
    # The thing that would be quietly wrong. The sketch's copy implied a card
    # was taken; none is, and a guest reading the page has to know which.
    s.check("the page says so", "Nothing is charged here" in body,
            detail="a guest who thinks they have paid for a second room and "
                   "has not is the worst version of this feature")
    # Scoped to the block, not the rest of the page: every public page carries
    # a footer note about who handles card payments, and splitting on the
    # heading swept that in.
    at = body.find("Add another room")
    block = body[at:body.find("</form>", at)] if at >= 0 else ""
    s.check("the block is there to check", bool(block.strip()))
    s.check("and no card form appears with it",
            "stripe" not in block.lower() and "card" not in block.lower(),
            detail="the manage page is not where a payment path should appear "
                   "because a placeholder hinted at one")

    s.section("Asking for it")
    guest.post(manage, data={"action": "add_room", "room_id": str(spare),
                             "guest_name": TAG + " Sister", "party_size": "2"},
               follow_redirects=True)
    added = conn.execute(
        "SELECT * FROM bookings WHERE guest_name = ?",
        (TAG + " Sister",)).fetchone()
    s.check("a booking is created", added is not None)
    s.check("as a REQUEST, not a confirmed stay",
            added and added["status"] == "pending",
            detail="the house confirms it in the normal queue, the same as "
                   "any other room")
    s.check("nothing is recorded as paid",
            added and (added["amount_paid"] or 0) == 0
            and added["payment_status"] != "paid",
            detail="the page told them nothing was charged")
    s.check("it is priced like any other room",
            added and (added["total_price"] or 0) > 0,
            detail="a request with no price is one nobody can confirm")
    s.check("on the same dates",
            added and added["arrival_date"] == arrival.isoformat()
            and added["departure_date"] == departure.isoformat())
    s.check("carrying the guest's own contact details",
            added and added["guest_email"] == f"{TAG}.b@example.invalid".lower(),
            detail="the house writes to whoever booked, not to a name typed "
                   "into a box")
    s.check("and it says where it came from",
            added and TAG + "MAIN" in (added["special_requests"] or ""),
            detail="an unexplained second booking on the same dates is a "
                   "telephone call")

    s.section("And it is one arrival, not two")
    group = {g["reference_code"] for g in m.booking_group(conn, main_id)}
    s.check("both are in one party", group == {TAG + "MAIN", added["reference_code"]},
            detail=str(group))
    s.check("on the house's own mechanism",
            conn.execute("SELECT party_id FROM bookings WHERE id = ?",
                         (main_id,)).fetchone()["party_id"] is not None)

    s.section("A room that has gone says what else is free")
    r = guest.post(manage, data={"action": "add_room", "room_id": str(taken),
                                 "guest_name": TAG + " Cousin",
                                 "party_size": "2"},
                   follow_redirects=True)
    page = r.get_data(as_text=True)
    s.check("it is refused",
            conn.execute("SELECT COUNT(*) FROM bookings WHERE guest_name = ?",
                         (TAG + " Cousin",)).fetchone()[0] == 0)
    s.check("and says the room has gone",
            "has gone since you looked" in page)
    s.check("naming something that IS free",
            "Still free on your dates" in page,
            detail="being told no with nothing to do next is the email this "
                   "exists to stop")

    s.section("It takes the write lock, like the booking form")
    import inspect
    src = inspect.getsource(m.app.view_functions["manage_booking"])
    block = src[src.find('action == "add_room"'):]
    block = block[:block.find('if action == "cancel"')]
    code = chr(10).join(l.split("#")[0] for l in block.splitlines())
    s.check("the submit claims rather than merely checking",
            "claim_range" in code,
            detail="check-then-write with nothing joining them is the bug "
                   "claim_range was written for")
    # Docstring stripped: rooms_free_for explains in words that it uses
    # is_range_available and NOT claim_range, and reading that as code made
    # this fail on exactly the thing it says.
    free_src = inspect.getsource(m.rooms_free_for)
    free_code = free_src[free_src.find('"""', free_src.find('"""') + 3) + 3:]
    s.check("and drawing the page does not",
            "claim_range" not in free_code,
            detail="taking the write lock to draw a list would put every "
                   "reader behind whoever is mid-booking")

    s.section("A cancelled booking is offered nothing")
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?",
                 (main_id,))
    conn.commit()
    body = guest.get(manage).get_data(as_text=True)
    s.check("no form at all", "Add another room" not in body,
            detail="a list of rooms to add to a cancelled stay is an "
                   "invitation to a conversation nobody wants")
    # Withheld TWICE, and the two guards are not the same guard. The template
    # already hides this whole region on a dead booking, so removing the
    # route's own check left the page correct and a control passed on it.
    # What the route's check is actually for is not DOING the work: the scan
    # asks every active room whether it is free, and a cancelled stay should
    # not pay for that on every page load. So count the calls.
    real = m.is_range_available
    calls = []

    def counted(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    m.is_range_available = counted
    try:
        guest.get(manage)
    finally:
        m.is_range_available = real
    s.check("and no availability scan is run for it", not calls,
            detail=f"{len(calls)} rooms were checked for a stay that cannot "
                   "have one added; the template hiding the form is not the "
                   "same as the page not doing the work")
    r = guest.post(manage, data={"action": "add_room", "room_id": str(held),
                                 "guest_name": TAG + " Nobody"},
                   follow_redirects=True)
    s.check("and posting anyway is refused",
            conn.execute("SELECT COUNT(*) FROM bookings WHERE guest_name = ?",
                         (TAG + " Nobody",)).fetchone()[0] == 0,
            detail="the form being absent is not the same as the handler "
                   "refusing, and only one of those survives a copied URL")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
