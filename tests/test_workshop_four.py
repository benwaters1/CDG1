"""Up to four to a room on a workshop.

The owner, 26 November 2026: "for the workshops allow up to 4 in a room, we
have rooms that accomodate that - it is different than nightly stays and
groups like to stay together". It came from three friends on Cooking in the
Cuisine who booked separately and asked to share: the room assignment refused
anybody into a room that already had one registration in it, so they could not
be put together at all.

What is checked:
  - A room takes registrations up to what it sleeps ON A WORKSHOP: its own
    figure where the owner set one, else its nightly one, never more than four.
    The nightly figure is not touched, because a nightly stay is not a group.
  - Three who booked separately go into one room that sleeps four; a fourth
    party that would make five is refused, and told who is in it.
  - Nobody is put in with somebody who paid to be alone, and somebody who paid
    to be alone is not put in with anybody.
  - 'Four to a room' is an arrangement the house can set, and it counts four
    to a room when rooms are counted.
  - The room form saves the workshop figure, capped at four.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZFOUR"


def _cleanup(conn):
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _room(conn, label, sleeps, workshop_sleeps=None):
    conn.execute(
        """INSERT INTO rooms (name, export_token, active, max_occupancy, max_adults, price_per_night,
               sort_order, workshop_sleeps) VALUES (?, ?, 1, ?, 2, 200.0, 996, ?)""",
        (f"{TAG} {label}", _harness.secrets_token(), sleeps, workshop_sleeps))
    conn.commit()
    return conn.execute("SELECT * FROM rooms WHERE name = ?", (f"{TAG} {label}",)).fetchone()


def _attendee(conn, sid, ref, party=1, occupancy="double"):
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
               occupancy_type, reference_code, manage_token, created_at)
           VALUES (?, ?, ?, ?, 'confirmed', ?, ?, ?, ?)""",
        (sid, f"{ref} {TAG}", f"{TAG.lower()}{ref.lower()}@example.invalid", party, occupancy,
         f"{TAG}{ref}", f"tok{TAG}{ref}{_harness.secrets_token()}".lower(), _harness.datetime_now()))
    conn.commit()
    return conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                        (f"{TAG}{ref}",)).fetchone()["id"]


def _room_of(conn, bid):
    return conn.execute("SELECT assigned_room_id FROM workshop_bookings WHERE id = ?",
                        (bid,)).fetchone()["assigned_room_id"]


def run():
    s = Suite("Up to four to a room on a workshop")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    try:
        _checks(s, oc, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _checks(s, oc, conn):
    start = house_today() + timedelta(days=345)
    now = _harness.datetime_now()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                        active, sort_order, created_at) VALUES (?, '', 3800, 12, 1, 96, ?)""",
                 (f"{TAG} Cooking", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Cooking",)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
                        created_at) VALUES (?, ?, ?, 12, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=5)).isoformat(), f"{TAG} s", now))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} s",)).fetchone()["id"]
    suite = _room(conn, "Suite", sleeps=5)
    twin = _room(conn, "Twin", sleeps=2)
    roomy_twin = _room(conn, "Twin with a sofa bed", sleeps=2, workshop_sleeps=4)
    huge = _room(conn, "Dormitory", sleeps=2, workshop_sleeps=9)

    s.section("What a room sleeps on a workshop")
    s.check("a room for five by the night takes four on a workshop, no more",
            m.workshop_room_sleeps(suite) == 4)
    s.check("a twin takes two", m.workshop_room_sleeps(twin) == 2)
    s.check("a room the owner says takes four on a workshop takes four",
            m.workshop_room_sleeps(roomy_twin) == 4)
    s.check("and four is the most, whatever is typed", m.workshop_room_sleeps(huge) == 4)
    s.check("its nightly figure is left as it was",
            conn.execute("SELECT max_occupancy FROM rooms WHERE id = ?",
                         (roomy_twin["id"],)).fetchone()[0] == 2)

    s.section("Three friends who booked separately, in one room")
    def put(bid, room):
        return oc.post(f"/admin/workshops/registrations/{bid}/assign-room",
                       data={"room_id": room["id"] if room else ""})
    friends = [_attendee(conn, sid, r) for r in ("BELINDA", "JENNY", "HELEN")]
    for bid in friends:
        put(bid, suite)
    s.check("all three are in the room that sleeps four on a workshop",
            all(_room_of(conn, b) == suite["id"] for b in friends),
            detail=str([_room_of(conn, b) for b in friends]))
    couple = _attendee(conn, sid, "SAWYERS", party=2)
    r = put(couple, suite)
    s.check("a couple who would make five is refused", _room_of(conn, couple) is None)
    with oc.session_transaction() as sess:
        said = " ".join(str(x) for x in sess.get("_flashes", []))
    clash = m.workshop_room_conflict(conn, suite["id"], start, start + timedelta(days=5), sid,
                                     exclude_registration_id=couple, party_size=2)
    s.check("and told how many it sleeps and who is in it",
            clash and "sleeps 4 on a workshop and would have 5" in clash and f"BELINDA {TAG}" in clash,
            detail=str(clash) + " / " + said[:120])
    fourth = _attendee(conn, sid, "AMY")
    put(fourth, suite)
    s.check("a fourth on her own fits", _room_of(conn, fourth) == suite["id"])
    fifth = _attendee(conn, sid, "ZOE")
    put(fifth, suite)
    s.check("and a fifth does not", _room_of(conn, fifth) is None)
    pair = [_attendee(conn, sid, r) for r in ("ONE", "TWO")]
    for bid in pair:
        put(bid, twin)
    s.check("two share a twin", all(_room_of(conn, b) == twin["id"] for b in pair))
    third = _attendee(conn, sid, "THREE")
    put(third, twin)
    s.check("a third is not squeezed into it", _room_of(conn, third) is None)

    s.section("A room somebody paid to have alone")
    alone = _attendee(conn, sid, "ALONE", occupancy="solo")
    put(alone, roomy_twin)
    s.check("they have it", _room_of(conn, alone) == roomy_twin["id"])
    guest = _attendee(conn, sid, "JOINER")
    put(guest, roomy_twin)
    s.check("nobody is put in with them, however many beds it has", _room_of(conn, guest) is None)
    sharer = _attendee(conn, sid, "SHARER")
    put(sharer, huge)
    other_alone = _attendee(conn, sid, "ALONE2", occupancy="solo")
    put(other_alone, huge)
    s.check("and somebody who paid to be alone is not put in a room with others, "
            "though it has beds to spare",
            _room_of(conn, sharer) == huge["id"] and _room_of(conn, other_alone) is None)

    s.section("The rooming page counts the same way")
    plan = m.rooming_plan(conn, sid)
    s.check("its rooms say what they sleep on a workshop",
            plan["sleeps"][suite["id"]] == 4 and plan["sleeps"][twin["id"]] == 2)
    page = oc.get(f"/admin/workshops/sessions/{sid}/rooming").get_data(as_text=True)
    s.check("and the page says so", "sleeps 4 on a workshop" in page)
    s.check("a full room is not reported as overfull",
            not [p for p in plan["problems"] if p["kind"] == "overfull"], detail=str(plan["problems"]))

    s.section("Four to a room")
    s.check("is an arrangement the house can set", "quad" in m.ALL_OCCUPANCY_TYPES)
    s.check("and is not offered to the public, where nobody chooses it",
            "quad" not in m.PUBLIC_OCCUPANCY_TYPES)
    s.check("four at four to a room take one room, five take two",
            m.rooms_needed("quad", 4) == 1 and m.rooms_needed("quad", 5) == 2)
    s.check("with no supplement", m.workshop_single_supplement({"single_supplement": 900}, "quad") == 0)
    reg = oc.get(f"/admin/workshops/registrations?session_id={sid}").get_data(as_text=True)
    s.check("the registrations page offers it", 'value="quad"' in reg and "Four to a room" in reg)

    s.section("The room form")
    base = {"name": twin["name"], "max_adults": "2", "max_children": "0", "max_occupancy": "2",
            "price_per_night": "200", "active": "on"}
    oc.post(f"/admin/rooms/{twin['id']}/edit", data=dict(base, workshop_sleeps="4"))
    row = conn.execute("SELECT workshop_sleeps, max_occupancy FROM rooms WHERE id = ?",
                       (twin["id"],)).fetchone()
    s.check("saves what it sleeps on a workshop, leaving the nightly figure",
            row["workshop_sleeps"] == 4 and row["max_occupancy"] == 2, detail=str(dict(row)))
    oc.post(f"/admin/rooms/{twin['id']}/edit", data=dict(base, workshop_sleeps="12"))
    s.check("capped at four", conn.execute("SELECT workshop_sleeps FROM rooms WHERE id = ?",
                                           (twin["id"],)).fetchone()[0] == 4)
    oc.post(f"/admin/rooms/{twin['id']}/edit", data=dict(base, workshop_sleeps=""))
    s.check("and emptied, the nightly figure again", conn.execute(
        "SELECT workshop_sleeps FROM rooms WHERE id = ?", (twin["id"],)).fetchone()[0] is None)
    s.check("the form asks it", 'name="workshop_sleeps"' in
            oc.get(f"/admin/rooms/{twin['id']}/edit").get_data(as_text=True))


if __name__ == "__main__":
    print(run().report())
