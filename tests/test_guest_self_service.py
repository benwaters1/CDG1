"""What a guest can do for themselves, without writing to the house.

Three things that were missing, and they were missing in different ways.

THEY COULD NOT CORRECT THEIR OWN DETAILS. Not name, not phone, anywhere. That
was awkward before and load-bearing now: the phone on a booking is what decides
whether they get told where to go the day before, and a guest who mistyped it
had no way to fix it except to write and wait for somebody to edit a row.

The email is deliberately still not editable, and that is the interesting half.
It is the address the booking is filed under and the one the account page is
keyed on, so changing it from a page reached by a link would move the booking
to whoever holds the link — and anybody who has ever seen a forwarded
confirmation holds one.

NO EXTRA COULD BE ADDED AT ALL. The account page listed the airport transfer
under "extras we can arrange" and the manage page offered it, and the action
behind both filtered on `category = 'room'` — which is not one of the seven
categories the app defines. So nothing could ever match, and every guest who
tried was told "that isn't something we can add". The feature read as built
from every direction except using it.

AND THE CATALOGUE COULD NOT BE FILLED IN. Every column the guest pages read —
category, description, guest_bookable, the notice a thing needs, the most one
guest may take — existed, was already used for filtering on the owner's page,
and could not be set from anywhere. The form saved a name and a price. That is
why the catalogue held one item, uncategorised.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTSELF"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_extras WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(ref, phone="", days=60):
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 LIMIT 1").fetchone()["id"]
    arrival = date.today() + timedelta(days=days)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, created_at)
           VALUES (?, ?, ?, 'Amelie Fontaine', ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + ref, TAG.lower() + "tok" + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid", phone,
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _get(ref):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                            (TAG + ref,)).fetchone()
    finally:
        conn.close()


def _lines(ref):
    conn = db()
    try:
        return conn.execute(
            """SELECT be.* FROM booking_extras be JOIN bookings b ON b.id = be.booking_id
               WHERE b.reference_code = ?""", (TAG + ref,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("What a guest can do for themselves")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()

    s.section("Correcting their own name and number")
    b = _booking("FIX", phone="")
    token = b["manage_token"]
    anon.post(f"/book/manage/{token}", data={
        "action": "contact", "guest_name": "Amelie Fontaine-Roux",
        "guest_phone": "06 12 34 56 78",
    }, follow_redirects=True)
    after = _get("FIX")
    s.check("the name changes", after["guest_name"] == "Amelie Fontaine-Roux",
            detail=str(after["guest_name"]))
    # The point of letting them do it at all: the number becomes one the
    # arrival text can use.
    s.check("and the number is stored in a form we could text",
            after["guest_phone"] == "+33612345678", detail=str(after["guest_phone"]))

    r = anon.post(f"/book/manage/{token}", data={
        "action": "contact", "guest_name": "Amelie Fontaine-Roux",
        "guest_phone": "ask my wife",
    }, follow_redirects=True)
    s.check("a number we cannot read is still saved rather than refused",
            _get("FIX")["guest_phone"] == "ask my wife",
            detail="throwing it away would lose the only contact they gave")
    # ...but they are told, because an unusable number looks identical to a
    # good one once it is in the box.
    s.check("and they are told we will not be able to text them",
            any("text you" in f for f in flashes(r)), detail=str(flashes(r)))

    r = anon.post(f"/book/manage/{token}", data={
        "action": "contact", "guest_name": "  ", "guest_phone": "06 12 34 56 78",
    }, follow_redirects=True)
    s.check("a booking cannot be left with no name at all",
            _get("FIX")["guest_name"] == "Amelie Fontaine-Roux",
            detail=str(_get("FIX")["guest_name"]))
    s.check("and it says so rather than falling over", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.section("But not the email, which is the way in")
    # Anybody holding this link could otherwise move the booking to their own
    # address — and a forwarded confirmation is a link.
    before_email = _get("FIX")["guest_email"]
    anon.post(f"/book/manage/{token}", data={
        "action": "contact", "guest_name": "Amelie Fontaine-Roux",
        "guest_phone": "06 12 34 56 78",
        "guest_email": "someone-else@example.invalid",
    }, follow_redirects=True)
    s.check("an email sent with the form is ignored",
            _get("FIX")["guest_email"] == before_email,
            detail=str(_get("FIX")["guest_email"]))
    page = anon.get(f"/book/manage/{token}").get_data(as_text=True)
    s.check("the page says which address it is filed under", before_email in page)
    s.check("and offers a way to have it changed",
            "send us a message" in page.lower(),
            detail="refusing silently would read as a bug rather than a rule")

    s.section("Adding something to the stay")
    conn = db()
    conn.execute(
        """INSERT INTO extras (name, price, description, category, guest_bookable,
           lead_time_days, max_qty, active, sort_order)
           VALUES (?, 90, 'Two day passes', 'activity', 1, 0, 2, 1, 99)""",
        (TAG + " Guest passes",))
    conn.commit()
    extra = conn.execute("SELECT * FROM extras WHERE name = ?",
                         (TAG + " Guest passes",)).fetchone()
    conn.close()

    # The bug: the action filtered on category = 'room', which is not one of
    # EXTRA_CATEGORIES, so nothing could ever match.
    s.check("the category is not one the old filter would have matched",
            extra["category"] != "room" and extra["category"] in m.EXTRA_CATEGORIES,
            detail=str(extra["category"]))
    r = anon.post(f"/book/manage/{token}", data={
        "action": "add_extra", "extra_id": str(extra["id"]), "quantity": "2",
    }, follow_redirects=True)
    lines = _lines("FIX")
    s.check("a guest can add it", len(lines) == 1, detail=str(flashes(r)))
    s.check("at the quantity they asked for", lines and lines[0]["quantity"] == 2,
            detail=str(lines[0]["quantity"]) if lines else "")
    s.check("and it is priced from the catalogue, not from the form",
            lines and lines[0]["unit_price"] == 90.0,
            detail=str(lines[0]["unit_price"]) if lines else "")

    r = anon.post(f"/book/manage/{token}", data={
        "action": "add_extra", "extra_id": str(extra["id"]), "quantity": "9",
    }, follow_redirects=True)
    s.check("more than the limit is refused",
            len(_lines("FIX")) == 1, detail=str(len(_lines("FIX"))))
    s.check("and says what the limit is",
            any("only do 2" in f for f in flashes(r)), detail=str(flashes(r)))

    # Something the owner has NOT opened to guests must stay closed.
    conn = db()
    conn.execute(
        """INSERT INTO extras (name, price, category, guest_bookable, lead_time_days,
           active, sort_order) VALUES (?, 40, 'other', 0, 0, 1, 99)""",
        (TAG + " Staff only",))
    conn.commit()
    closed = conn.execute("SELECT * FROM extras WHERE name = ?",
                          (TAG + " Staff only",)).fetchone()
    conn.close()
    anon.post(f"/book/manage/{token}", data={
        "action": "add_extra", "extra_id": str(closed["id"]), "quantity": "1",
    }, follow_redirects=True)
    s.check("one the owner has not opened to guests cannot be added",
            len(_lines("FIX")) == 1,
            detail="guest_bookable is the flag that decides this, and it has "
                   "to still decide it")

    s.section("Notice that a thing needs is still honoured")
    soon = _booking("SOON", phone="", days=1)
    conn = db()
    conn.execute("UPDATE extras SET lead_time_days = 7 WHERE id = ?", (extra["id"],))
    conn.commit()
    conn.close()
    r = anon.post(f"/book/manage/{soon['manage_token']}", data={
        "action": "add_extra", "extra_id": str(extra["id"]), "quantity": "1",
    }, follow_redirects=True)
    s.check("a thing needing a week cannot be added for tomorrow",
            len(_lines("SOON")) == 0, detail=str(len(_lines("SOON"))))
    s.check("and the guest is told why rather than just refused",
            any("notice" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("The owner can now describe an extra properly")
    # Every one of these columns was already read by the guest pages and could
    # not be set from anywhere.
    oc.post("/admin/extras/new", data={
        "name": TAG + " Picnic hamper", "price": "65,00",
        "description": "For two, packed the night before",
        "category": "food", "guest_bookable": "on",
        "lead_time_days": "2", "max_qty": "3",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT * FROM extras WHERE name = ?",
                        (TAG + " Picnic hamper",)).fetchone()
    conn.close()
    s.check("it is created", made is not None)
    s.check("with its category", made and made["category"] == "food",
            detail=str(made["category"]) if made else "")
    s.check("its description", made and "packed the night before" in (made["description"] or ""))
    s.check("the notice it needs", made and made["lead_time_days"] == 2,
            detail=str(made["lead_time_days"]) if made else "")
    s.check("how many one guest may take", made and made["max_qty"] == 3,
            detail=str(made["max_qty"]) if made else "")
    s.check("and that guests may add it", made and made["guest_bookable"] == 1)
    s.check("the comma price still reads as money", made and made["price"] == 65.0,
            detail=str(made["price"]) if made else "")

    # Blank notice must be 0 and not NULL — the column is NOT NULL, so a
    # "safe" None fails on exactly the action it was meant to protect.
    oc.post("/admin/extras/new", data={
        "name": TAG + " Simple thing", "price": "10", "category": "other",
    }, follow_redirects=True)
    conn = db()
    plain = conn.execute("SELECT * FROM extras WHERE name = ?",
                         (TAG + " Simple thing",)).fetchone()
    conn.close()
    s.check("one with nothing else filled in still saves", plain is not None)
    s.check("with no notice needed rather than no value",
            plain and plain["lead_time_days"] == 0,
            detail=str(plain["lead_time_days"]) if plain else "")
    s.check("and no limit on how many, which is not the same as none allowed",
            plain and plain["max_qty"] is None,
            detail=str(plain["max_qty"]) if plain else "")
    # Off unless asked for: something a guest can put on their own bill without
    # anybody looking is a decision.
    s.check("and closed to guests unless it was ticked",
            plain and plain["guest_bookable"] == 0,
            detail=str(plain["guest_bookable"]) if plain else "")

    oc.post(f"/admin/extras/{made['id']}/edit", data={
        "name": TAG + " Picnic hamper", "price": "70", "category": "celebration",
        "description": "Now with the good wine", "lead_time_days": "1",
    }, follow_redirects=True)
    conn = db()
    edited = conn.execute("SELECT * FROM extras WHERE id = ?", (made["id"],)).fetchone()
    conn.close()
    s.check("editing changes the category too", edited["category"] == "celebration",
            detail=str(edited["category"]))
    s.check("and unticking closes it to guests again", edited["guest_bookable"] == 0,
            detail="a checkbox left off has to mean off, or it can never be "
                   "withdrawn once opened")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
