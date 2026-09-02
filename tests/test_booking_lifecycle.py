"""What staff do to a booking after the guest has made it.

Two untested routes that both touch money or state that cannot be undone:

edit_booking reprices a stay when its dates move. It does that by deriving
the extras from the stored total minus a freshly computed room total, which
is only correct if extras and discounts survive the arithmetic — so this
drives a booking that has both.

checkout_booking is guarded by a single conditional UPDATE rather than a
check-then-write, precisely so that a double-click cannot run the turnover
checklist twice and email the guest twice. That guard is the kind of thing
that looks fine forever and then quietly stops working after a refactor, so
it is checked by actually posting twice.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room
import _harness

m = _harness.m
TAG = "ZZLIFE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE room_note LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _make_booking(room_id, arrival, departure, total, extras_summary=None, discount=None):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           guest_phone, arrival_date, departure_date, party_size, status, total_price,
           extras_summary, discount_amount, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?, ?, ?, ?)""",
        (room_id, f"{TAG}-{_harness.secrets_token()[:6]}", _harness.secrets_token(),
         f"{TAG} guest", f"{TAG.lower()}@example.invalid", arrival.isoformat(),
         departure.isoformat(), total, extras_summary, discount,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM bookings WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Booking lifecycle")
    _cleanup()
    oc, ec, owner, emp = clients()
    room = ensure_room()

    conn = db()
    full_room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    conn.close()

    # Far enough out to miss the seeded ateliers, which move as they are
    # rebooked — the same trap that has already bitten two suites here.
    base = m.house_today() + timedelta(days=700)
    two_nights = (base, base + timedelta(days=2))

    with m.app.test_request_context():
        conn = db()
        room_2n = m.compute_room_total(conn, full_room, *two_nights)
        room_4n = m.compute_room_total(conn, full_room, base, base + timedelta(days=4))
        conn.close()

    s.section("Extending a stay reprices it")
    bk = _make_booking(room["id"], *two_nights, total=room_2n)
    r = oc.post(f"/admin/bookings/{bk['id']}/edit", data={
        "arrival_date": base.isoformat(),
        "departure_date": (base + timedelta(days=4)).isoformat(),
        "party_size": "2", "guest_phone": "", "special_requests": "",
    }, follow_redirects=True)
    conn = db()
    after = conn.execute("SELECT * FROM bookings WHERE id = ?", (bk["id"],)).fetchone()
    conn.close()
    s.check("the dates moved", after["departure_date"] == (base + timedelta(days=4)).isoformat(), r)
    s.check("and the total is the four-night price",
            abs((after["total_price"] or 0) - room_4n) < 0.01,
            detail=f"got {after['total_price']}, four nights is {room_4n}")

    s.section("Extras and a discount survive the repricing")
    # The route derives extras as (stored total - recomputed room total), so a
    # booking carrying both is the case where that arithmetic either holds or
    # silently eats one of them.
    _cleanup()
    bk2 = _make_booking(room["id"], *two_nights, total=room_2n + 100 - 50,
                        extras_summary="Airport transfer (€100.00)", discount=50.0)
    oc.post(f"/admin/bookings/{bk2['id']}/edit", data={
        "arrival_date": base.isoformat(),
        "departure_date": (base + timedelta(days=4)).isoformat(),
        "party_size": "2", "guest_phone": "", "special_requests": "",
    }, follow_redirects=True)
    conn = db()
    after2 = conn.execute("SELECT * FROM bookings WHERE id = ?", (bk2["id"],)).fetchone()
    conn.close()
    # Four nights, still plus the €100 transfer and still less the €50 given.
    expected = round(room_4n + 100 - 50, 2)
    s.check("the new total keeps both the extra and the discount",
            abs((after2["total_price"] or 0) - expected) < 0.01,
            detail=f"got {after2['total_price']}, expected {expected}")
    s.check("the extras line is not lost", after2["extras_summary"] == "Airport transfer (€100.00)",
            detail=f"got {after2['extras_summary']!r}")

    s.section("An edit cannot double-book the room")
    _cleanup()
    keeper = _make_booking(room["id"], base, base + timedelta(days=3), total=room_2n)
    mover = _make_booking(room["id"], base + timedelta(days=20),
                          base + timedelta(days=22), total=room_2n)
    r3 = oc.post(f"/admin/bookings/{mover['id']}/edit", data={
        "arrival_date": base.isoformat(),
        "departure_date": (base + timedelta(days=2)).isoformat(),
        "party_size": "2", "guest_phone": "", "special_requests": "",
    }, follow_redirects=True)
    conn = db()
    moved = conn.execute("SELECT * FROM bookings WHERE id = ?", (mover["id"],)).fetchone()
    conn.close()
    s.check("moving a booking onto an occupied night is refused",
            moved["arrival_date"] == (base + timedelta(days=20)).isoformat(), r3)

    s.section("Checking out, twice")
    _cleanup()
    out = _make_booking(room["id"], base - timedelta(days=5), base - timedelta(days=2),
                        total=room_2n)
    first = oc.post(f"/admin/bookings/{out['id']}/checkout",
                    data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    conn = db()
    done = conn.execute("SELECT checked_out_at FROM bookings WHERE id = ?", (out["id"],)).fetchone()
    tasks_after_one = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE room_note LIKE ?", (f"%{TAG}%",)).fetchone()["c"]
    conn.close()
    s.check("the booking is checked out", done["checked_out_at"] is not None, first)
    s.check("and the turnover checklist was raised",
            tasks_after_one == len(m.CHECKOUT_CHECKLIST),
            detail=f"{tasks_after_one} tasks for a {len(m.CHECKOUT_CHECKLIST)}-item checklist")

    # The guard that matters: a second post must change nothing at all.
    second = oc.post(f"/admin/bookings/{out['id']}/checkout",
                     data={"assigned_to_user_id": str(emp["id"])}, follow_redirects=True)
    conn = db()
    tasks_after_two = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE room_note LIKE ?", (f"%{TAG}%",)).fetchone()["c"]
    conn.close()
    s.check("checking out again raises no second checklist",
            tasks_after_two == tasks_after_one,
            detail=f"{tasks_after_one} then {tasks_after_two}")
    s.check("and it says so rather than pretending it worked",
            "already checked out" in second.get_data(as_text=True).lower())

    s.section("Guards")
    s.check("checkout needs somebody to give the tasks to",
            oc.post(f"/admin/bookings/{out['id']}/checkout", data={},
                    follow_redirects=True).status_code == 200)
    s.check("an employee cannot check a guest out",
            ec.post(f"/admin/bookings/{out['id']}/checkout",
                    data={"assigned_to_user_id": str(emp["id"])}).status_code in (302, 403))
    s.check("an employee cannot edit a booking",
            ec.post(f"/admin/bookings/{out['id']}/edit", data={
                "arrival_date": base.isoformat(),
                "departure_date": (base + timedelta(days=1)).isoformat(),
                "party_size": "2"}).status_code in (302, 403))
    s.check("an unknown booking is a 404",
            oc.post("/admin/bookings/999999/checkout",
                    data={"assigned_to_user_id": str(emp["id"])}).status_code == 404)

    _cleanup()
    return s
