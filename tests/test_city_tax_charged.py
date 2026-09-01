"""The taxe de sejour is stored by the app, charged to the guest, and declared.

test_city_tax already covers the ARITHMETIC of the declaration -- nights clipped
to the period, the amount apportioned. It covers it against rows whose city_tax
the fixture computes and writes itself, which is the whole reason this file
exists: nothing in the app wrote that column. Every booking carried the NOT NULL
DEFAULT 0, so:

  - the declaration to the commune reported nothing collected, however many
    guests had stayed;
  - guest_statement computed a figure of its own and added it to the guest's
    total, while booking_bill -- THE definition of what a stay owes, behind the
    Pay button, the balance chase and the outstanding list -- left it out. The
    two documents disagreed about one stay by the amount of the tax, on EVERY
    stay: a guest who paid what the bill asked was then sent a statement showing
    a balance;
  - guests_under_18 decides who is exempt, and no form asked.

So this suite books through the FORM and asks what the app stored. Nothing here
writes city_tax or guests_under_18 by hand.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZCTAX"


def _cleanup():
    conn = db()
    for t in ("bookings",):
        conn.execute(f"DELETE FROM {t} WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action IN ('book_room', 'walk_in')")
    conn.commit()
    conn.close()


def _room(conn, sleeps=4):
    """A room that actually sleeps the party being tested.

    The first active room sleeps two, so a party of four was refused by the form
    and every check after it failed for a reason that had nothing to do with the
    tax.
    """
    return conn.execute(
        "SELECT * FROM rooms WHERE active = 1 AND max_occupancy >= ? "
        "ORDER BY max_occupancy, id LIMIT 1", (sleeps,)).fetchone()


def _rate(conn):
    return m.tax_rate(conn, "city_tax_per_adult_per_night")


def _booked(reference_like):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM bookings WHERE guest_name LIKE ? ORDER BY id DESC LIMIT 1",
            (reference_like + "%",)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Taxe de sejour: stored, charged, declared")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    conn = db()
    room = _room(conn, 4)
    rate = _rate(conn)
    exempt_under = m.house_setting(conn, "city_tax_exempt_under_age") \
        if hasattr(m, "house_setting") else "18"
    conn.close()

    arrival = house_today() + timedelta(days=40)
    departure = arrival + timedelta(days=3)          # 3 nights

    s.section("A booking made through the form carries the tax")
    r = anon.post(f"/book/{room['id']}", data={
        "arrival_date": arrival.isoformat(),
        "departure_date": departure.isoformat(),
        "guest_name": f"{TAG} Family", "guest_email": "zzctax@example.invalid",
        "guest_phone": "", "party_size": "4", "guests_under_18": "2",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    s.check("the form takes it", r.status_code in (302, 303),
            detail=f"HTTP {r.status_code}")
    b = _booked(TAG)
    s.check("a booking was made", b is not None)
    if b:
        s.check("the exempt count was stored", (b["guests_under_18"] or 0) == 2,
                detail=f"{b['guests_under_18']} — the column was read in seven "
                       "places and no form asked for it")
        expected = round(2 * 3 * rate, 2)     # 4 guests less 2 children, 3 nights
        s.check("and the tax was stamped on the booking",
                abs(float(b["city_tax"] or 0) - expected) < 0.01,
                detail=f"stored {b['city_tax']}, expected {expected} — nothing "
                       "wrote this column, so every booking carried the "
                       "NOT NULL DEFAULT 0 and the commune was told nothing")
        s.check("children are not charged as adults",
                abs(float(b["city_tax"] or 0) - round(4 * 3 * rate, 2)) > 0.01,
                detail="a family of four with two children paid for four")

    s.section("The bill and the statement agree, to the cent")
    # They did not. The statement computed the tax and added it; booking_bill had
    # no such line. The gap was the tax, on every stay.
    if b:
        conn = db()
        bill = m.booking_bill(conn, b["id"])
        stmt = m.guest_statement(conn, conn.execute(
            "SELECT * FROM bookings WHERE id = ?", (b["id"],)).fetchone())
        conn.close()
        s.check("the totals match",
                abs(bill["total"] - stmt["total"]) < 0.005,
                detail=f"bill {bill['total']:.2f} vs statement {stmt['total']:.2f} "
                       "— a guest who pays what the bill asks is then sent a "
                       "document showing a balance")
        s.check("the bill names the tax as its own line",
                any(l.get("kind") == "city_tax" for l in bill["lines"]),
                detail="folded into the room total, so the guest cannot see a "
                       "tax that carries no VAT and belongs to the commune")
        s.check("and the lines add up to the total",
                abs(sum(l["amount"] for l in bill["lines"]) - bill["total"]) < 0.005,
                detail=f"{sum(l['amount'] for l in bill['lines']):.2f} vs "
                       f"{bill['total']:.2f}")
        s.check("the statement shows the same tax figure",
                abs(stmt["city_tax"] - float(b["city_tax"] or 0)) < 0.005,
                detail=f"statement {stmt['city_tax']} vs stored {b['city_tax']}")
        s.check("and the tax carries no VAT",
                all(abs(row.get("net", 0) + row.get("vat", 0)
                        - row.get("gross", 0)) < 0.02 for row in stmt["vat"])
                if stmt.get("vat") else True,
                detail="the VAT breakdown does not reconcile")

    s.section("The declaration now sees what was collected")
    # Confirmed through the app's own route, not by editing the status column.
    # The declaration counts confirmed stays only -- which is right: an enquiry
    # nobody has accepted is not a stay the commune is owed for -- so a booking
    # request would have shown nothing here and said nothing about the fix.
    if b:
        oc.post(f"/admin/bookings/{b['id']}/confirm", follow_redirects=True)
        conn = db()
        start = date(arrival.year, arrival.month, 1)
        end = (date(arrival.year + 1, 1, 1) if arrival.month == 12
               else date(arrival.year, arrival.month + 1, 1))
        working = m.city_tax_working(conn, start, end)
        conn.close()
        charged = working["total"]
        s.check("the period reports the tax rather than zero", charged > 0,
                detail=f"{charged} — the declaration read a column nothing wrote, "
                       "so it reported nothing collected however many had stayed")
        mine = [row for row in working["rows"] if TAG in (row["guest"] or "")]
        s.check("and this stay is one of its rows", len(mine) == 1,
                detail=f"{len(mine)} rows matched")

    s.section("A stay never charged the tax is not invented one")
    # The other half of the fix, and the half that is easy to undo. booking_bill,
    # guest_statement and the settings panel all used to work out a figure of
    # their own whenever the column was 0 -- which was every booking. That put a
    # charge the house never asked for onto the guest's VAT document, told the
    # owner they owed the commune money they had never taken, and would move what
    # an existing guest owes for a stay already agreed and sometimes already paid
    # in full. Stamped or nothing.
    conn = db()
    old_room = _room(conn, 2)
    old_arrival = house_today() + timedelta(days=300)
    old_departure = old_arrival + timedelta(days=2)
    # Priced from the rate card, with no discount_amount, because that is the
    # only consistent state. total_price, discount_amount and the rate card are a
    # triple: booking_bill recomputes the room from the card and applies the
    # stored discount, while guest_statement reads total_price. Setting one of
    # the three alone builds a booking the app cannot produce, and the two
    # documents then disagree about the accommodation -- which reads as a defect
    # and is a fixture.
    old_priced = m.compute_room_total(conn, old_room, old_arrival, old_departure)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, '', '', ?, ?, 2, 'confirmed', ?, 0, ?)""",
        (old_room["id"], f"{TAG}-OLD", f"tok{TAG}old", f"{TAG} Before",
         old_arrival.isoformat(), old_departure.isoformat(), old_priced,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    legacy = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                          (f"{TAG}-OLD",)).fetchone()
    s.check("the stay carries no stamped tax",
            not float(legacy["city_tax"] or 0),
            detail="the fixture is presupposing the thing being tested")
    legacy_bill = m.booking_bill(conn, legacy["id"])
    legacy_stmt = m.guest_statement(conn, legacy)
    conn.close()
    s.check("the bill does not invent one",
            not any(l.get("kind") == "city_tax" for l in legacy_bill["lines"]),
            detail="a stay already agreed, and possibly already paid in full, "
                   "silently owes more than it did")
    s.check("nor does the statement",
            abs(legacy_stmt["city_tax"]) < 0.005,
            detail=f"{legacy_stmt['city_tax']} on the guest's VAT document, for a "
                   "charge the house never asked for and cannot now collect")
    s.check("and the two still agree",
            abs(legacy_bill["total"] - legacy_stmt["total"]) < 0.005,
            detail=f"bill {legacy_bill['total']:.2f} vs statement "
                   f"{legacy_stmt['total']:.2f}")

    s.section("A walk-in taken at the desk carries it too")
    wi_arrival = house_today() + timedelta(days=60)
    r = oc.post("/admin/bookings/walk-in", data={
        "room_id": str(room["id"]),
        "arrival_date": wi_arrival.isoformat(),
        "departure_date": (wi_arrival + timedelta(days=2)).isoformat(),
        "guest_name": f"{TAG} Desk", "guest_email": "", "guest_phone": "",
        "party_size": "3", "guests_under_18": "1", "special_requests": "",
        "charge": "500", "payment_method": "cash",
    }, follow_redirects=True)
    s.check("the desk form takes it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    w = _booked(TAG + " Desk")
    if w:
        s.check("the tax is on the walk-in",
                abs(float(w["city_tax"] or 0) - round(2 * 2 * rate, 2)) < 0.01,
                detail=f"{w['city_tax']} for 3 guests less 1 child over 2 nights")

    s.section("Editing a stay moves the tax with it")
    if b:
        longer = departure + timedelta(days=2)        # 3 nights -> 5
        r = oc.post(f"/admin/bookings/{b['id']}/edit", data={
            "arrival_date": arrival.isoformat(),
            "departure_date": longer.isoformat(),
            "party_size": "4", "guests_under_18": "2",
            "guest_phone": "", "special_requests": "",
        }, follow_redirects=True)
        after = _booked(TAG + " Family")
        s.check("a longer stay owes more tax",
                abs(float(after["city_tax"] or 0) - round(2 * 5 * rate, 2)) < 0.01,
                detail=f"{after['city_tax']} after 3 nights became 5 — leaving the "
                       "stamped figure behind puts the declaration out by the "
                       "difference with nothing looking wrong on the page")
        r = oc.post(f"/admin/bookings/{b['id']}/edit", data={
            "arrival_date": arrival.isoformat(),
            "departure_date": longer.isoformat(),
            "party_size": "4", "guests_under_18": "0",
            "guest_phone": "", "special_requests": "",
        }, follow_redirects=True)
        after = _booked(TAG + " Family")
        s.check("and dropping the exemption raises it",
                abs(float(after["city_tax"] or 0) - round(4 * 5 * rate, 2)) < 0.01,
                detail=f"{after['city_tax']} with no children on a party of 4")

    s.section("The forms actually ask")
    page = anon.get(f"/book/{room['id']}?arrival={arrival.isoformat()}"
                    f"&departure={departure.isoformat()}").get_data(as_text=True)
    s.check("the public form has the field", 'name="guests_under_18"' in page,
            detail="the route reads a field no form sends, so it is always 0")
    s.check("and says why it is asking",
            "taxe de s" in page.lower() and "exempt" in page.lower(),
            detail="a number box with no reason given reads as nosy")
    desk = oc.get("/admin/bookings/walk-in").get_data(as_text=True)
    s.check("the desk form has it too", 'name="guests_under_18"' in desk)
    ed = oc.get(f"/admin/bookings/{b['id']}/edit").get_data(as_text=True) if b else ""
    s.check("and so does the edit form", 'name="guests_under_18"' in ed)

    s.section("Nonsense in the field cannot make the tax bigger")
    r = anon.post(f"/book/{room['id']}", data={
        "arrival_date": (arrival + timedelta(days=100)).isoformat(),
        "departure_date": (arrival + timedelta(days=102)).isoformat(),
        "guest_name": f"{TAG} Odd", "guest_email": "zzctax.odd@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "-5",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    odd = _booked(TAG + " Odd")
    if odd:
        s.check("a negative exempt count is floored at zero",
                (odd["guests_under_18"] or 0) >= 0,
                detail=f"{odd['guests_under_18']} — a negative would charge the "
                       "guest for more adults than were in the room")
        s.check("and the tax is never more than the party justifies",
                float(odd["city_tax"] or 0) <= round(2 * 2 * rate, 2) + 0.01,
                detail=f"{odd['city_tax']}")

    s.section("More children than guests is not a discount scheme")
    r = anon.post(f"/book/{room['id']}", data={
        "arrival_date": (arrival + timedelta(days=200)).isoformat(),
        "departure_date": (arrival + timedelta(days=202)).isoformat(),
        "guest_name": f"{TAG} Many", "guest_email": "zzctax.many@example.invalid",
        "guest_phone": "", "party_size": "2", "guests_under_18": "9",
        "special_requests": "", "agree_terms": "on",
    }, follow_redirects=False)
    many = _booked(TAG + " Many")
    if many:
        s.check("the exempt count is clamped to the party",
                (many["guests_under_18"] or 0) <= (many["party_size"] or 0),
                detail=f"{many['guests_under_18']} of {many['party_size']}")
        s.check("and the tax never goes negative",
                float(many["city_tax"] or 0) >= 0,
                detail=f"{many['city_tax']} — a negative tax is a credit the "
                       "house pays the guest for bringing children")

    _cleanup()
    return s
