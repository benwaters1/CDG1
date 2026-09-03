"""Bookings that are one arrival.

ON THE HOUSE'S OWN MECHANISM, not a second one. booking_parties already
existed — a named party with a lead booking, an audit line and a bulk "tie
together as a party" — and I built group_ref alongside it without looking
first, which left two ways to say the same fact. group_ref is gone; what this
file adds is the half theirs did not have.

THE GUEST SEES IT. Theirs was staff-only, so a guest could not tell whether
their sister had booked. ADDING ONE to a party that already exists: theirs
takes two or more, mints a new party, and refuses if any is already in one —
so a fourth room booked a fortnight later could not be tied to the other three
at all. TAKING ONE OUT without disbanding the whole party. And a cancelled
room dropping out of what the guest is shown.


A family taking three rooms makes three bookings with three references and
three unconnected arrivals. Nothing in the app knew they were one party, so
the arrivals list said three names on the same afternoon and somebody worked
it out from the surnames.

IT IS SAID BY A PERSON, NEVER INFERRED. Two bookings called Martin on the
same dates are very often one family and are sometimes two families called
Martin — and guessing wrong tells a stranger who else is staying and when.
There is deliberately no matching rule anywhere in this feature.

A GROUP IS A FACT, NOT A PERMISSION. Everyone in it can see that the others
are coming; nobody in it can change anybody else's booking, and the guest's
page carries no link that would let them try.

Two edge cases that quietly go wrong if nobody writes them down:

  - Linking a third booking to one already in a group puts it in THAT group,
    rather than making a second group of two and orphaning the first.
  - Taking somebody out of a group of two dissolves it. A group of one is not
    a group, and leaving it puts a "travelling together" panel on a page with
    nobody else on it.

The other half of the sketch — a guest adding another room from their own
page — is deliberately not landed. It creates a booking, prices it and takes
money for it, which is a booking flow rather than a panel.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZTRAV"


def inspect_src(fn):
    import inspect
    return inspect.getsource(fn)


def _cleanup(conn):
    conn.execute(
        "DELETE FROM booking_parties WHERE id IN "
        "(SELECT party_id FROM bookings WHERE reference_code LIKE ? "
        " AND party_id IS NOT NULL)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("travelling together")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    rooms = conn.execute(
        "SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 3").fetchall()
    if len(rooms) < 3:
        s.section("Setup")
        s.check("three rooms exist to put a family in", False,
                detail=f"{len(rooms)} active rooms — reported rather than "
                       "skipped, because the group logic below is untested "
                       "without them")
        conn.close()
        return s

    def add(ref, room, name):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
            (room["id"], TAG + ref, f"tok-{TAG}-{ref}".lower(),
             TAG + " " + name, f"{TAG}.{ref}@example.invalid".lower(),
             (today + timedelta(days=15)).isoformat(),
             (today + timedelta(days=18)).isoformat(), now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    a = add("A", rooms[0], "Martin")
    b = add("B", rooms[1], "Martin")
    c = add("C", rooms[2], "Martin")
    conn.commit()

    s.section("It is the house's own mechanism, not a second one")
    # booking_parties already existed. A duplicate would leave the bulk "tie
    # together as a party" and this page disagreeing about who is travelling
    # with whom, which is the failure this codebase keeps writing down.
    s.check("a party is a row in booking_parties",
            "party_id" in inspect_src(m.booking_group),
            detail="if this reads some other column there are two mechanisms "
                   "again")
    s.check("and the bulk 'tie together' action writes the same column",
            "party_id" in inspect_src(m.app.view_functions["new_booking_party"]),
            detail="one column, or the bulk action and this page disagree "
                   "about who is travelling with whom")
    s.check("and so does disbanding one",
            "party_id" in inspect_src(m.app.view_functions["disband_booking_party"]))

    s.section("Nothing is grouped until somebody says so")
    s.check("three bookings on the same dates are three arrivals",
            m.booking_group(conn, a) == [],
            detail="two bookings called Martin on the same dates are "
                   "sometimes two families called Martin")
    import inspect
    src = inspect.getsource(m.booking_group) + inspect.getsource(
        m.join_booking_party)
    s.check("and nothing in the code matches on a name or a date",
            "guest_name" not in src.replace("bookings.guest_name,", "")
            or "LIKE" not in src,
            detail="a matching rule is the thing that tells a stranger who "
                   "else is staying")

    s.section("Linking two")
    ok, why = m.join_booking_party(conn, a, b)
    conn.commit()
    s.check("it works", ok, detail=why)
    group = {g["reference_code"] for g in m.booking_group(conn, a)}
    s.check("both are in it", group == {TAG + "A", TAG + "B"}, detail=str(group))
    s.check("and it reads the same from the other one",
            {g["reference_code"] for g in m.booking_group(conn, b)} == group)
    s.check("the third is still on its own", m.booking_group(conn, c) == [])

    s.section("The party gets a name somebody can say on the telephone")
    # Theirs names it from the lead guest's surname, and this path has to do
    # the same -- a party row with no name is one nobody can refer to, and
    # the two ways of making one would then produce different things.
    named = conn.execute(
        "SELECT booking_parties.name FROM booking_parties "
        "JOIN bookings ON bookings.party_id = booking_parties.id "
        "WHERE bookings.id = ?", (a,)).fetchone()
    s.check("it has one", named and named["name"].strip(),
            detail=str(dict(named)) if named else "no party row")
    s.check("and it is the lead guest's surname, as the bulk action does it",
            named and named["name"] == "Martin party",
            detail=str(named["name"]) if named else "")

    s.section("Linking a third JOINS the group, not a second one")
    # The quiet failure: a naive version mints a fresh reference for the pair,
    # which moves one of the first two out and leaves the other alone.
    ok, why = m.join_booking_party(conn, c, a)
    conn.commit()
    s.check("it works", ok, detail=why)
    group = {g["reference_code"] for g in m.booking_group(conn, c)}
    s.check("all three are together",
            group == {TAG + "A", TAG + "B", TAG + "C"}, detail=str(group))
    s.check("and the first two did not get separated",
            len(m.booking_group(conn, b)) == 3)

    s.section("Two existing groups are not silently merged")
    d = add("D", rooms[0], "Bernard")
    e = add("E", rooms[1], "Bernard")
    conn.commit()
    m.join_booking_party(conn, d, e)
    conn.commit()
    ok, why = m.join_booking_party(conn, a, d)
    s.check("it is refused", not ok, detail=why or "(no reason given)")
    s.check("with a reason somebody can act on",
            "already travelling" in (why or ""), detail=why)
    s.check("and nothing moved",
            len(m.booking_group(conn, a)) == 3
            and len(m.booking_group(conn, d)) == 2,
            detail="merging two groups moves everybody in one of them, which "
                   "is a decision and not a side effect of linking two rooms")

    s.section("A booking cannot travel with itself")
    ok, why = m.join_booking_party(conn, a, a)
    s.check("refused", not ok, detail=why)
    # And for the right reason. Without the guard it is still refused --
    # "id IN (?, ?)" with the same id twice returns one row -- but the
    # message becomes "one of those bookings does not exist", which sends
    # somebody looking for a booking that is right in front of them.
    s.check("and the reason is the true one",
            "travel with itself" in (why or ""), detail=why)

    s.section("Taking one out")
    m.leave_booking_party(conn, c)
    conn.commit()
    s.check("it is on its own again", m.booking_group(conn, c) == [])
    s.check("and the other two are still together",
            len(m.booking_group(conn, a)) == 2)

    s.section("And a group of one is not a group")
    # Leaving one behind puts a "travelling together" panel on a page with
    # nobody else on it.
    m.leave_booking_party(conn, b)
    conn.commit()
    s.check("the last one is unlinked too", m.booking_group(conn, a) == [],
            detail=str([dict(g) for g in m.booking_group(conn, a)]))

    s.section("Only confirmed and pending bookings are in it")
    m.join_booking_party(conn, a, b)
    conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (b,))
    conn.commit()
    s.check("a cancelled booking drops out of the group",
            {g["reference_code"] for g in m.booking_group(conn, a)} == {TAG + "A"},
            detail="telling a family that a room they cancelled is still "
                   "coming is worse than saying nothing")
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (b,))
    conn.commit()

    # ------------------------------------------------------------- pages
    s.section("The guest's own page")
    body = m.app.test_client().get(
        f"/book/manage/tok-{TAG}-a".lower()).get_data(as_text=True)
    s.check("it says they are travelling together",
            "Travelling together" in body)
    s.check("naming the other room", rooms[1]["name"] in body)
    s.check("and marking which one is theirs", "yours" in body)
    s.check("but offering no way to touch the other booking",
            f"tok-{TAG}-b".lower() not in body,
            detail="a group is a fact about an arrival, not a permission")

    s.section("A booking in no group shows no panel")
    solo = m.app.test_client().get(
        f"/book/manage/tok-{TAG}-c".lower()).get_data(as_text=True)
    s.check("nothing is rendered", "Travelling together" not in solo,
            detail="a panel headed 'travelling together' with one room on it "
                   "is worse than no panel")

    s.section("A group of one left in the data still renders nothing")
    # leave_booking_group dissolves them, but the data can hold one anyway:
    # cancel the other room and the party_id stays on this one. The panel
    # has to check the LENGTH, not merely that a group exists.
    conn.execute(
        "INSERT INTO booking_parties (name, lead_booking_id, created_at) "
        "VALUES (?, ?, ?)", (TAG + " Lone", c, now))
    lone = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("UPDATE bookings SET party_id = ? WHERE id = ?", (lone, c))
    conn.commit()
    lone = m.app.test_client().get(
        f"/book/manage/tok-{TAG}-c".lower()).get_data(as_text=True)
    s.check("no panel for a group of one", "Travelling together" not in lone,
            detail="a panel headed 'travelling together' listing one room is "
                   "worse than no panel")
    conn.execute("UPDATE bookings SET party_id = NULL WHERE id = ?", (c,))
    conn.execute("DELETE FROM booking_parties WHERE id = ?", (lone,))
    conn.commit()

    s.section("Staff can say it, and unsay it")
    r = oc.post(f"/admin/bookings/{c}/travelling-with",
                data={"reference_code": TAG + "A"}, follow_redirects=True)
    s.check("linking through the page works",
            len(m.booking_group(conn, c)) == 3, detail=f"HTTP {r.status_code}")
    admin = oc.get(f"/admin/bookings/{c}/edit").get_data(as_text=True)
    s.check("the admin page lists the group", TAG + "A" in admin)
    oc.post(f"/admin/bookings/{c}/travelling-with",
            data={"leave": "1"}, follow_redirects=True)
    s.check("and taking it out works", m.booking_group(conn, c) == [])

    s.section("A reference that does not exist says so")
    r = oc.post(f"/admin/bookings/{c}/travelling-with",
                data={"reference_code": "NOPE-404"}, follow_redirects=True)
    s.check("it is refused with the reference quoted back",
            "NOPE-404" in r.get_data(as_text=True),
            detail="'not found' without saying what was not found sends "
                   "somebody to check the wrong thing")
    s.check("and nothing was linked", m.booking_group(conn, c) == [])

    s.section("An employee cannot rearrange arrivals")
    r = ec.post(f"/admin/bookings/{c}/travelling-with",
                data={"reference_code": TAG + "A"}, follow_redirects=False)
    s.check("refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    # The status alone proves nothing: this route redirects on SUCCESS too,
    # so 302 is what both answers look like. What matters is whether the
    # bookings moved.
    s.check("and nothing was linked", m.booking_group(conn, c) == [],
            detail="a redirect is what success looks like here as well")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
