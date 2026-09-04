"""One bill for a party, and a stay somebody else booked.

booking_parties has tied a family's rooms together for a while — they are
written to once rather than once each — and the money stayed resolutely per
booking. So the family treated as one for letters got three bills, three
statements and three payment links, and somebody added them up by hand to
answer "what do we owe?".

And a booking had one name on it. An agent, a company, or a son booking for his
parents: the payer and the guest were the same record, so the arrival details
went to whoever was paying and the bill went to whoever was sleeping here.

Two things carry this file.

  THE PARTY TOTAL IS SUMMED FROM THE BILLS, never recomputed. booking_bill is
  the one definition of what a stay costs, and a party total that could
  disagree with the bills inside it is worse than no party total at all.

  THE SPLIT GOES ONE WAY ONLY. The bill follows the payer; the arrival details
  — directions, the door code, what time to come — always follow the guest. The
  one thing worse than sending those to the wrong person is sending the door
  code to the wrong person.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZPB"


def _cleanup():
    conn = db()
    conn.execute("UPDATE bookings SET party_id = NULL WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, room_id, arrival, total=600, nights=2):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 'unpaid', ?, 0, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzpb.{ref}@example.invalid".lower(), arrival.isoformat(),
         (arrival + timedelta(days=nights)).isoformat(), total,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _get(ref):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                            (f"{TAG}-{ref}",)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("One bill for a party")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    arrival = m.house_today() + timedelta(days=45)

    s.section("Three rooms, one bill")
    a = _stay("A", room_id=room["id"], arrival=arrival, total=600)
    b = _stay("B", room_id=room["id"], arrival=arrival, total=400)
    conn = db()
    cur = conn.execute(
        "INSERT INTO booking_parties (name, lead_booking_id, created_at) VALUES (?, ?, ?)",
        (f"{TAG} Family", a["id"], datetime.now(timezone.utc).isoformat()))
    pid = cur.lastrowid
    conn.execute("UPDATE bookings SET party_id = ? WHERE id IN (?, ?)",
                 (pid, a["id"], b["id"]))
    conn.commit()
    with m.app.test_request_context("/"):
        detail = m.party_detail(conn, pid)
    conn.close()
    s.check("both stays are in it", detail["rooms"] == 2, detail=f"{detail['rooms']}")
    # SUMMED FROM THE BILLS, not recomputed. The check is that it agrees with
    # what each booking's own bill says, because a party total that can
    # disagree with them is worse than none.
    conn = db()
    with m.app.test_request_context("/"):
        own = sum(m.booking_bill(conn, x["id"])["total"] for x in (a, b))
    conn.close()
    s.check("and the total is the sum of their own bills",
            abs(detail["total"] - own) < 0.01,
            detail=f"party {detail['total']} against {own} — booking_bill is "
                   "the one definition of what a stay costs and this is not a "
                   "second one")

    s.section("And it is a page, not a number in a function")
    page = oc.get(f"/admin/parties/{pid}").get_data(as_text=True)
    s.check("the statement opens", f"{TAG} Family" in page)
    s.check("both rooms are on it",
            f"{TAG}-A" in page and f"{TAG}-B" in page,
            detail="a total with no lines under it is a total somebody re-adds "
                   "by hand, which is what this replaces")
    s.check("with what the whole party owes",
            "%.2f" % detail["total"] in page or "1000" in page.replace(",", ""),
            detail=f"{detail['total']}")
    s.check("and it is reachable from the bookings list",
            f"/admin/parties/{pid}" in oc.get("/admin/bookings").get_data(as_text=True),
            detail="a page nobody can get to is a page nobody uses")

    s.section("A party that no longer exists is a 404, not a blank page")
    s.check("an unknown party", oc.get("/admin/parties/999999").status_code == 404)

    s.section("A stay somebody else booked")
    c = _stay("AGENT", room_id=room["id"], arrival=arrival + timedelta(days=10))
    oc.post(f"/admin/bookings/{c['id']}/edit", data={
        "arrival_date": c["arrival_date"], "departure_date": c["departure_date"],
        "party_size": "2", "guest_phone": "", "special_requests": "",
        "source": "direct", "reference_code": c["reference_code"],
        "booked_by_name": "Travel Agency", "booked_by_email": "agent@example.invalid",
    }, follow_redirects=True)
    after = _get("AGENT")
    s.check("who booked it is recorded",
            (after["booked_by_email"] or "") == "agent@example.invalid",
            detail=f"{after['booked_by_email']!r}")
    s.check("the bill goes to them", m.bill_goes_to(after) == "agent@example.invalid",
            detail="an agent's client should not be sent an invoice")
    s.check("and the stay details still go to the guest",
            m.stay_details_go_to(after) == "zzpb.agent@example.invalid",
            detail="the one thing worse than sending directions to the wrong "
                   "person is sending the door code to the wrong person")

    s.section("A name with nowhere to send the bill is not kept")
    oc.post(f"/admin/bookings/{c['id']}/edit", data={
        "arrival_date": c["arrival_date"], "departure_date": c["departure_date"],
        "party_size": "2", "guest_phone": "", "special_requests": "",
        "source": "direct", "reference_code": c["reference_code"],
        "booked_by_name": "Somebody", "booked_by_email": "",
    }, follow_redirects=True)
    s.check("both are dropped together", not (_get("AGENT")["booked_by_name"] or ""),
            detail="the page would say somebody else booked it while the "
                   "statement quietly kept going to the guest")

    s.section("An ordinary booking is unaffected")
    plain = _get("A")
    s.check("the bill goes to the guest",
            m.bill_goes_to(plain) == "zzpb.a@example.invalid",
            detail="almost every booking, and it must not need the new fields")
    s.check("and so do the stay details",
            m.stay_details_go_to(plain) == "zzpb.a@example.invalid")

    s.section("Guards")
    s.check("an employee cannot read a party's money",
            ec.get(f"/admin/parties/{pid}").status_code in (302, 403))

    _cleanup()
    return s
