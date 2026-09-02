"""Who can put a guest in a private room, and at what price.

A private room on an atelier is arranged with the château directly — what it
costs depends on the atelier, the dates and what is actually free, so it is
quoted per enquiry rather than carrying a standing supplement.

That makes the server-side refusal the point of this suite, not the form.
Removing the <option> stops nobody: the interesting case is a POST that asks
for 'solo' anyway, which before this change was accepted and — with no
supplement set on any atelier — handed over a private room for nothing.

Staff keep the ability, because the arrangement has to be recordable once it
has been agreed on the phone.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZSOLO"


def _session_with_space():
    """A future atelier session, created if there isn't one."""
    conn = db()
    row = conn.execute(
        """SELECT ws.id, ws.capacity, w.price_per_person, w.id AS workshop_id
           FROM workshop_sessions ws JOIN workshops w ON w.id = ws.workshop_id
           WHERE ws.start_date > ? AND COALESCE(w.price_per_person, 0) > 0
           ORDER BY ws.start_date LIMIT 1""",
        (m.house_today().isoformat(),)).fetchone()
    conn.close()
    return row


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE '%workshop%'")
    conn.commit()
    conn.close()


def run():
    s = Suite("Solo occupancy")
    _cleanup()
    pub = m.app.test_client()
    oc, ec, owner, emp = clients()
    ses = _session_with_space()
    if not ses:
        s.check("a priced future atelier session exists to test against", False,
                detail="none found — suite skipped")
        return s

    s.section("The public form does not offer a private room")
    page = pub.get(f"/workshops/register/{ses['id']}").get_data(as_text=True)
    s.check("no solo option in the markup", 'value="solo"' not in page)
    # Assert the SUBSTANCE, not the sentence. This matched a literal phrase and
    # broke on a handover that merely reworded it -- "Rooms are arranged for two,
    # with a third bed available on request" became "Arranged for two, and a
    # third bed goes in if you need it", which tells the guest exactly the same
    # thing. A copy edit failing a test teaches people to override tests. What
    # must not vanish is the two facts: shared by default, third bed possible.
    low = page.lower()
    s.check("but it says how rooms are arranged",
            "third bed" in low and "two" in low,
            detail="nothing explains the sleeping arrangements — a guest booking "
                   "alone is not told they will be sharing")

    s.section("And the server refuses one even if it is posted")
    r = pub.post(f"/workshops/register/{ses['id']}", data={
        "guest_name": f"{TAG} Chancer", "guest_email": f"{TAG.lower()}@example.invalid",
        "guest_phone": "", "party_size": "1", "occupancy_type": "solo",
        "notes": "", "requested_roommate": "", "dietary_notes": "",
        "medical_notes": "", "special_occasion": "", "other_guest_names": "",
        "promo_code": "", "agree_terms": "on",
    }, follow_redirects=True)
    conn = db()
    booked = conn.execute(
        """SELECT occupancy_type, single_supplement, total_price FROM workshop_bookings
           WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1""", (TAG + "%",)).fetchone()
    conn.close()
    s.check("a registration was still created", booked is not None, r)
    s.check("but NOT as solo — it falls back to a shared room",
            booked is not None and booked["occupancy_type"] != "solo",
            detail=f"stored {booked['occupancy_type'] if booked else 'no row'}")
    s.check("so no private room was given away free",
            booked is not None and not booked["single_supplement"])

    s.section("Staff can record one that was arranged by phone, with its price")
    conn = db()
    reg = conn.execute(
        "SELECT id, reference_code FROM workshop_bookings WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    r2 = oc.post(f"/admin/workshops/registrations/{reg['id']}/occupancy",
                 data={"occupancy_type": "solo", "single_supplement": "600"},
                 follow_redirects=True)
    conn = db()
    after = conn.execute(
        "SELECT * FROM workshop_bookings WHERE id = ?", (reg["id"],)).fetchone()
    conn.close()
    s.check("it is recorded as solo", after["occupancy_type"] == "solo", r2)
    s.check("with the agreed supplement", abs((after["single_supplement"] or 0) - 600) < 0.01,
            detail=f"got {after['single_supplement']}")
    expected = round((ses["price_per_person"] + 600) * 1, 2)
    s.check("and the total is repriced to include it",
            abs((after["total_price"] or 0) - expected) < 0.01,
            detail=f"got {after['total_price']}, expected {expected}")
    s.check("the deposit moved with it", (after["deposit_amount"] or 0) > 0)

    s.section("Switching back to a shared room clears the supplement")
    oc.post(f"/admin/workshops/registrations/{reg['id']}/occupancy",
            data={"occupancy_type": "double", "single_supplement": "600"},
            follow_redirects=True)
    conn = db()
    back = conn.execute(
        "SELECT * FROM workshop_bookings WHERE id = ?", (reg["id"],)).fetchone()
    conn.close()
    s.check("the supplement is gone", not back["single_supplement"],
            detail=f"got {back['single_supplement']}")
    s.check("and the total is back to the shared price",
            abs((back["total_price"] or 0) - ses["price_per_person"]) < 0.01,
            detail=f"got {back['total_price']}")

    s.section("Guards")
    s.check("an employee cannot set a room arrangement",
            ec.post(f"/admin/workshops/registrations/{reg['id']}/occupancy",
                    data={"occupancy_type": "solo", "single_supplement": "600"}
                    ).status_code in (302, 403))
    s.check("an unknown registration is a 404",
            oc.post("/admin/workshops/registrations/999999/occupancy",
                    data={"occupancy_type": "solo"}).status_code == 404)
    r3 = oc.post(f"/admin/workshops/registrations/{reg['id']}/occupancy",
                 data={"occupancy_type": "penthouse"}, follow_redirects=True)
    conn = db()
    unchanged = conn.execute(
        "SELECT occupancy_type FROM workshop_bookings WHERE id = ?", (reg["id"],)).fetchone()
    conn.close()
    s.check("a nonsense arrangement is refused, not stored",
            unchanged["occupancy_type"] == "double", r3)

    _cleanup()
    return s
