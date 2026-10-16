"""Who can put a guest in a private room, and at what price.

A room to yourself on an atelier is sold online since 16 October 2026 (owner,
handover z22 item 12) — but only when it is really on: the atelier carries a
supplement above nothing, it has a price, the guest is coming alone, and a
room is still free on the session. Everywhere else the château arranges it
directly and staff record it by hand.

That makes the server the point of this suite, not the form. Hiding the tick
stops nobody: the interesting cases are a POST that asks for 'solo' where it
is not on offer, which must still become a shared room (before the first
version of this suite it was accepted and handed over a private room for
nothing), and a POST that asks for it where it IS on offer, which must charge
the supplement on the total, the deposit and the promo preview alike.

Staff keep the ability, because an arrangement agreed on the phone has to be
recordable.
"""
from _harness import Suite, clients, db, WORKSHOP_DETAILS
import _harness

m = _harness.m
TAG = "ZZSOLO"
SUPPLEMENT = 500.0
_REAL_ROOM_COUNT = m.bookable_room_count


def _session_with_space():
    """A future, running, priced atelier session."""
    conn = db()
    row = conn.execute(
        """SELECT ws.id, ws.capacity, ws.start_date, w.price_per_person, w.id AS workshop_id,
                  w.single_supplement AS original_supplement, w.deposit_percent
           FROM workshop_sessions ws JOIN workshops w ON w.id = ws.workshop_id
           WHERE ws.start_date > ? AND COALESCE(w.price_per_person, 0) > 0
             AND w.active = 1 AND ws.cancelled_at IS NULL
           ORDER BY ws.start_date LIMIT 1""",
        (m.house_today().isoformat(),)).fetchone()
    conn.close()
    return row


def _set_supplement(workshop_id, value):
    conn = db()
    conn.execute("UPDATE workshops SET single_supplement = ? WHERE id = ?", (value, workshop_id))
    conn.commit()
    conn.close()


def _ensure_free_rooms(session_id, n):
    """At least n rooms free on the session, so space is not what is tested."""
    conn = db()
    free = m.bookable_room_count(conn) - m.session_rooms_used(conn, session_id)
    for i in range(max(n - free, 0)):
        conn.execute(
            """INSERT INTO rooms (name, export_token, active, max_occupancy, price_per_night,
               sort_order) VALUES (?, ?, 1, 2, 200.0, 900)""",
            (f"{TAG} Spare {i}", _harness.secrets_token()))
    conn.commit()
    conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE '%workshop%' OR action LIKE '%promo%'")
    conn.commit()
    conn.close()


def _clear_rate_limit():
    # Six registrations in a minute is more than one connection is allowed;
    # a refusal for that reason would pass the "not registered" checks below
    # for the wrong reason, so every post starts with a clean log.
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action LIKE '%workshop%'")
    conn.commit()
    conn.close()


def _post(pub, session_id, name, party="1", occupancy="solo", **extra):
    _clear_rate_limit()
    data = {
        "guest_name": f"{TAG} {name}", "guest_email": f"{TAG.lower()}.{name.lower()}@example.invalid",
        "guest_phone": "", "party_size": party, "occupancy_type": occupancy,
        "notes": "", "requested_roommate": "", "dietary_notes": "",
        "medical_notes": "", "special_occasion": "", "other_guest_names": "",
        "promo_code": "", "agree_terms": "on", **WORKSHOP_DETAILS,
    }
    data.update(extra)
    return pub.post(f"/workshops/register/{session_id}", data=data, follow_redirects=True)


def _booked(name):
    conn = db()
    row = conn.execute(
        """SELECT * FROM workshop_bookings WHERE guest_name = ? ORDER BY id DESC LIMIT 1""",
        (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


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
    price = float(ses["price_per_person"])
    _ensure_free_rooms(ses["id"], 3)
    try:
        _run(s, pub, oc, ec, ses, price)
    finally:
        m.bookable_room_count = _REAL_ROOM_COUNT
        _set_supplement(ses["workshop_id"], ses["original_supplement"])
        _cleanup()
    return s


def _run(s, pub, oc, ec, ses, price):
    url = f"/workshops/register/{ses['id']}"

    # ------------------------------------------------------------------
    s.section("No supplement set: the form does not offer a private room")
    _set_supplement(ses["workshop_id"], None)
    page = pub.get(url).get_data(as_text=True)
    s.check("no solo tick in the markup", 'value="solo"' not in page)
    # Assert the SUBSTANCE, not the sentence: a handover that rewords the
    # copy must not fail this. What must not vanish is that a guest coming
    # alone is told they will be sharing.
    low = page.lower()
    s.check("but it says how rooms are arranged",
            "arranged for two" in low and "share" in low,
            detail="nothing explains the sleeping arrangements — a guest booking "
                   "alone is not told they will be sharing")

    s.section("And the server refuses one even if it is posted")
    r = _post(pub, ses["id"], "Chancer")
    booked = _booked("Chancer")
    s.check("a registration was still created", booked is not None, r)
    s.check("but NOT as solo — it falls back to a shared room",
            booked is not None and booked["occupancy_type"] != "solo",
            detail=f"stored {booked['occupancy_type'] if booked else 'no row'}")
    s.check("so no private room was given away free",
            booked is not None and not booked["single_supplement"])

    # ------------------------------------------------------------------
    s.section("A supplement set and a room free: the form sells one")
    _set_supplement(ses["workshop_id"], SUPPLEMENT)
    page = pub.get(url).get_data(as_text=True)
    s.check("the tick is offered", 'value="solo"' in page)
    s.check("with its price on it", "A room to myself, +€500" in page,
            detail="the guest must see what the room costs before ticking it")
    s.check("and the page's own sum is told the supplement",
            "var supp = 500" in page.replace(".0;", ";"),
            detail="without it the total, deposit and button show the shared price")

    r = _post(pub, ses["id"], "Alone")
    booked = _booked("Alone")
    s.check("one guest ticking it is registered", booked is not None, r)
    if booked:
        s.check("as solo", booked["occupancy_type"] == "solo",
                detail=f"stored {booked['occupancy_type']}")
        s.check("with the supplement recorded on the booking",
                abs((booked["single_supplement"] or 0) - SUPPLEMENT) < 0.01,
                detail=f"got {booked['single_supplement']}")
        s.check("and in the total charged",
                abs((booked["total_price"] or 0) - (price + SUPPLEMENT)) < 0.01,
                detail=f"got {booked['total_price']}, expected {price + SUPPLEMENT}")
        conn = db()
        pct = m.resolve_deposit_percent(conn, "workshop", ses["start_date"], 1, ses["deposit_percent"])
        conn.close()
        dep, bal, _ = m.compute_workshop_payment_terms(
            price + SUPPLEMENT, pct, m.parse_date(ses["start_date"]))
        s.check("the deposit is taken from that total, not the shared price",
                abs((booked["deposit_amount"] or 0) - (dep or 0)) < 0.01
                and abs((booked["balance_amount"] or 0) - (bal or 0)) < 0.01,
                detail=f"got {booked['deposit_amount']}/{booked['balance_amount']}, "
                       f"expected {dep}/{bal}")

    s.section("It is for someone coming alone")
    r = _post(pub, ses["id"], "Pair", party="2")
    booked = _booked("Pair")
    s.check("a party of two posting solo is registered", booked is not None, r)
    s.check("sharing, at the shared price — the page never offered them the tick",
            booked is not None and booked["occupancy_type"] != "solo"
            and not booked["single_supplement"]
            and abs((booked["total_price"] or 0) - price * 2) < 0.01,
            detail=(f"stored {booked['occupancy_type']} at {booked['total_price']}"
                    if booked else "no row"))

    s.section("Not ticking it is sharing, as before")
    r = _post(pub, ses["id"], "Sharer", occupancy="")
    booked = _booked("Sharer")
    s.check("registered sharing at the published price",
            booked is not None and booked["occupancy_type"] == "double"
            and abs((booked["total_price"] or 0) - price) < 0.01,
            detail=(f"stored {booked['occupancy_type']} at {booked['total_price']}"
                    if booked else "no row"))

    s.section("A validation error keeps the tick")
    r = _post(pub, ses["id"], "Typo", guest_email="not-an-email")
    body = r.get_data(as_text=True)
    s.check("the form comes back with the room still ticked",
            'value="solo" id="occupancy_solo" checked' in body,
            detail="the guest would pay the shared price without noticing the tick had gone")

    # ------------------------------------------------------------------
    s.section("The promo preview prices the room in")
    conn = db()
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
               applies_to, active, redemption_count, created_at)
           VALUES (?, 'test', 'percent', 10, 'workshop', 1, 0, '2026-01-01T00:00:00')""",
        (f"{TAG}TEN",))
    conn.commit()
    conn.close()

    def preview(party, occupancy):
        return pub.post("/api/validate-promo-code", data={
            "category": "workshop", "code": f"{TAG}TEN", "session_id": str(ses["id"]),
            "party_size": party, "occupancy_type": occupancy}).get_json() or {}

    solo = preview("1", "solo")
    s.check("ten percent off one guest alone is ten percent of price + supplement",
            solo.get("valid") and abs(solo.get("subtotal", 0) - (price + SUPPLEMENT)) < 0.01
            and abs(solo.get("discount_amount", 0) - round((price + SUPPLEMENT) * 0.10, 2)) < 0.01,
            detail=f"got {solo}")
    shared = preview("1", "")
    s.check("and without the tick, ten percent of the shared price",
            shared.get("valid") and abs(shared.get("subtotal", 0) - price) < 0.01,
            detail=f"got {shared}")
    pair = preview("2", "solo")
    s.check("a party of two is previewed sharing, as it would be charged",
            pair.get("valid") and abs(pair.get("subtotal", 0) - price * 2) < 0.01,
            detail=f"got {pair}")
    page = pub.get(url).get_data(as_text=True)
    s.check("the page sends the tick with the preview request",
            "occupancy_type: (soloBox && soloBox.checked" in page,
            detail="the server can only price in a room the page tells it about")

    # ------------------------------------------------------------------
    s.section("No room free: not offered, and not sold")
    # Stand the room count down to what the session already uses, so the
    # last room is gone. The route asks again under the write lock, which is
    # what stops two guests ticking it for the same last room.
    m.bookable_room_count = lambda conn: m.session_rooms_used(conn, ses["id"])
    page = pub.get(url).get_data(as_text=True)
    s.check("the tick is not offered", 'value="solo"' not in page)
    r = _post(pub, ses["id"], "Late")
    body = r.get_data(as_text=True)
    s.check("a guest who ticked it on an older page is not registered",
            _booked("Late") is None,
            detail="they were promised a room to themselves and would be put in a shared one")
    s.check("and is told why, with the sharing alternative",
            "just been taken" in body and "share" in body.lower(), r)
    m.bookable_room_count = _REAL_ROOM_COUNT

    s.section("The rule itself")
    s.check("no supplement → not bookable",
            not m.workshop_solo_bookable({"single_supplement": None, "price_per_person": 900}, 3))
    s.check("no price → not bookable",
            not m.workshop_solo_bookable({"single_supplement": 500, "price_per_person": 0}, 3))
    s.check("no room → not bookable",
            not m.workshop_solo_bookable({"single_supplement": 500, "price_per_person": 900}, 0))
    s.check("all three → bookable",
            m.workshop_solo_bookable({"single_supplement": 500, "price_per_person": 900}, 1))

    # ------------------------------------------------------------------
    s.section("Staff can record one that was arranged by phone, with its price")
    reg = _booked("Chancer")
    r2 = oc.post(f"/admin/workshops/registrations/{reg['id']}/occupancy",
                 data={"occupancy_type": "solo", "single_supplement": "600"},
                 follow_redirects=True)
    after = _booked("Chancer")
    s.check("it is recorded as solo", after["occupancy_type"] == "solo", r2)
    s.check("with the agreed supplement", abs((after["single_supplement"] or 0) - 600) < 0.01,
            detail=f"got {after['single_supplement']}")
    s.check("and the total is repriced to include it",
            abs((after["total_price"] or 0) - (price + 600)) < 0.01,
            detail=f"got {after['total_price']}, expected {price + 600}")
    s.check("the deposit moved with it", (after["deposit_amount"] or 0) > 0)

    s.section("Switching back to a shared room clears the supplement")
    oc.post(f"/admin/workshops/registrations/{reg['id']}/occupancy",
            data={"occupancy_type": "double", "single_supplement": "600"},
            follow_redirects=True)
    back = _booked("Chancer")
    s.check("the supplement is gone", not back["single_supplement"],
            detail=f"got {back['single_supplement']}")
    s.check("and the total is back to the shared price",
            abs((back["total_price"] or 0) - price) < 0.01,
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
    unchanged = _booked("Chancer")
    s.check("a nonsense arrangement is refused, not stored",
            unchanged["occupancy_type"] == "double", r3)
