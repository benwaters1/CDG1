"""A promo code honoured on an event, with the redemption recorded.

Two CHECKs listed room, restaurant and workshop. So a code could not be scoped
to events, and an event redemption could not be written at all. The house could
still HONOUR a code it had agreed to -- by typing a smaller number into the quote
-- and that is the failure worth naming: a discount with no redemption row makes
max_redemptions silently bypassable, so a code offered to "the first ten couples"
runs forever, and "What discounts cost" reports less than was given away.

The order of operations is the other thing checked here. An event is quoted, not
priced off a rate card, so there is nothing to discount when the enquiry arrives.
The code is captured then and applied when the owner sets a price.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZEPROMO"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM promo_code_redemptions WHERE promo_code_id IN "
                 "(SELECT id FROM promo_codes WHERE code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'event_inquiry'")
    conn.commit()
    conn.close()


def _code(code, *, applies_to="event", percent=10, max_redemptions=None):
    conn = db()
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
           applies_to, max_redemptions, redemption_count, active, created_at)
           VALUES (?, 'ZZ test', 'percent', ?, ?, ?, 0, 1, ?)""",
        (code, percent, applies_to, max_redemptions,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM promo_codes WHERE code = ?", (code,)).fetchone()
    conn.close()
    return row


def _event(ref, *, days_out=200, status="new", promo=None):
    conn = db()
    kinds = m.known_event_types(conn)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, contact_phone, preferred_date, guest_count,
           message, status, amount_paid, promo_code, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, 60, 'ZZ', ?, 0, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), (kinds or ["wedding"])[0],
         f"{TAG} {ref}", f"{TAG.lower()}.{ref}@example.invalid".lower(),
         (date.today() + timedelta(days=days_out)).isoformat(), status, promo,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _row(event_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM event_inquiries WHERE id = ?",
                            (event_id,)).fetchone()
    finally:
        conn.close()


def _bill(event_id):
    conn = db()
    try:
        return m.event_bill(conn, event_id)
    finally:
        conn.close()


def _redemptions(code):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM promo_code_redemptions WHERE promo_code_id = "
            "(SELECT id FROM promo_codes WHERE code = ?)", (code,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Promo codes on events")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("A code can be scoped to events at all")
    s.check("events are an option on the promo form",
            "event" in m.PROMO_APPLIES_TO, detail=f"{m.PROMO_APPLIES_TO}")
    body = oc.get("/admin/promo-codes").get_data(as_text=True)
    s.check("and the page offers it", 'value="event"' in body,
            detail="the constant lists it and the form does not")
    conn = db()
    for table in ("promo_codes", "promo_code_redemptions"):
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                           "AND name = ?", (table,)).fetchone()["sql"] or ""
        s.check(f"{table} accepts an event", "'event'" in sql,
                detail="the CHECK refuses the row, so the discount happens and "
                       "the redemption does not")
    conn.close()

    s.section("The enquiry captures what the guest claims")
    r = anon.post("/events/inquire", data={
        "event_type": "wedding", "contact_name": f"{TAG} Claimant",
        "contact_email": "zzepromo.claim@example.invalid", "contact_phone": "",
        "preferred_date": (date.today() + timedelta(days=250)).isoformat(),
        "guest_count": "80", "message": "ZZ", "promo_code": "ZZEPROMO-WED",
    }, follow_redirects=True)
    conn = db()
    claimed = conn.execute("SELECT * FROM event_inquiries WHERE contact_name = ?",
                           (f"{TAG} Claimant",)).fetchone()
    conn.close()
    s.check("the enquiry is taken", claimed is not None, detail=f"{flashes(r)[:1]}")
    if claimed:
        s.check("with the code on it", (claimed["promo_code"] or "") == "ZZEPROMO-WED",
                detail=f"{claimed['promo_code']!r} — the owner cannot honour a "
                       "code they were never shown")
        s.check("and nothing discounted yet, because nothing is priced yet",
                not claimed["discount_amount"],
                detail=f"{claimed['discount_amount']} against a quote of "
                       f"{claimed['quoted_price']}")

    s.section("And the form actually asks for it")
    # The gap that let a design handover drop this field without a single check
    # going red: everything above posts to the ROUTE, which reads promo_code
    # whether or not any form sends it. A parameter the route reads and no page
    # supplies is the read-and-never-written shape one level up, and it fails
    # silently — an enquiry with no code is an ordinary enquiry.
    page = anon.get("/events").get_data(as_text=True)
    s.check("the enquiry form carries the field",
            'name="promo_code"' in page,
            detail="the route reads a parameter nothing sends")
    s.check("and says what it is for",
            "promo" in page.lower() and "quote" in page.lower(),
            detail="an unlabelled box on a wedding enquiry gets left empty")

    s.section("An unknown code does not lose the enquiry")
    # Refusing a wedding enquiry over a typo is a worse outcome than an
    # unapplied discount.
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'event_inquiry'")
    conn.commit()
    conn.close()
    r = anon.post("/events/inquire", data={
        "event_type": "wedding", "contact_name": f"{TAG} Typo",
        "contact_email": "zzepromo.typo@example.invalid", "contact_phone": "",
        "preferred_date": (date.today() + timedelta(days=260)).isoformat(),
        "guest_count": "40", "message": "ZZ", "promo_code": "NOT-A-REAL-CODE",
    }, follow_redirects=True)
    conn = db()
    typo = conn.execute("SELECT COUNT(*) c FROM event_inquiries WHERE contact_name = ?",
                        (f"{TAG} Typo",)).fetchone()["c"]
    conn.close()
    s.check("the enquiry still lands", typo == 1,
            detail="a mistyped code cost the house a wedding enquiry")

    s.section("Quoting applies it, and the discount shows its working")
    promo = _code(f"{TAG}-WED", percent=10)
    ev = _event("A", promo=f"{TAG}-WED")
    r = oc.post(f"/admin/events/{ev['id']}/update", data={
        "status": "confirmed", "quoted_price": "8000", "owner_note": "",
        "promo_code": f"{TAG}-WED",
    }, follow_redirects=True)
    after = _row(ev["id"])
    s.check("the discount is stored",
            abs(float(after["discount_amount"] or 0) - 800) < 0.01,
            detail=f"{after['discount_amount']} — 10% of 8000")
    s.check("and which code did it",
            after["promo_code_id"] == promo["id"],
            detail=f"{after['promo_code_id']} — a discount with no code attached "
                   "cannot be reported on or reconciled")
    bill = _bill(ev["id"])
    s.check("the quote stays the gross figure",
            abs(bill["gross_quote"] - 8000) < 0.01, detail=f"{bill['gross_quote']}")
    s.check("the discount is its own figure",
            abs(bill["discount"] - 800) < 0.01, detail=f"{bill['discount']}")
    s.check("and what they owe is the net",
            abs(bill["quoted"] - 7200) < 0.01,
            detail=f"{bill['quoted']} — six callers read this as the total, so "
                   "the discount has to come off here rather than in each of them")
    s.check("owed agrees with it", abs(bill["owed"] - 7200) < 0.01,
            detail=f"{bill['owed']}")
    s.check("the owner is told it applied",
            any("applied" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("The redemption is recorded, so the cap actually caps")
    reds = _redemptions(f"{TAG}-WED")
    s.check("one redemption row exists", len(reds) == 1,
            detail=f"{len(reds)} — without it max_redemptions is bypassable and "
                   "'What discounts cost' under-reports")
    if reds:
        s.check("filed against events", reds[0]["category"] == "event",
                detail=f"{reds[0]['category']}")
        s.check("with the amount given away",
                abs(float(reds[0]["discount_amount"] or 0) - 800) < 0.01,
                detail=f"{reds[0]['discount_amount']}")
        s.check("and the event it was given on",
                reds[0]["booking_reference"] == ev["reference_code"],
                detail=f"{reds[0]['booking_reference']}")

    s.section("Re-saving the quote does not redeem it twice")
    oc.post(f"/admin/events/{ev['id']}/update", data={
        "status": "confirmed", "quoted_price": "8000", "owner_note": "again",
        "promo_code": f"{TAG}-WED",
    }, follow_redirects=True)
    s.check("still one redemption", len(_redemptions(f"{TAG}-WED")) == 1,
            detail=f"{len(_redemptions(f'{TAG}-WED'))} — a cap of ten would be "
                   "eaten by one owner pressing Save twice")
    s.check("and the discount did not double",
            abs(float(_row(ev["id"])["discount_amount"] or 0) - 800) < 0.01,
            detail=f"{_row(ev['id'])['discount_amount']}")

    s.section("A code for stays only is refused on an event")
    rooms_only = _code(f"{TAG}-ROOMS", applies_to="room", percent=50)
    ev2 = _event("B")
    r = oc.post(f"/admin/events/{ev2['id']}/update", data={
        "status": "confirmed", "quoted_price": "5000", "owner_note": "",
        "promo_code": f"{TAG}-ROOMS",
    }, follow_redirects=True)
    s.check("nothing is discounted",
            not _row(ev2["id"])["discount_amount"],
            detail=f"{_row(ev2['id'])['discount_amount']} — a stays code took "
                   "half off a wedding")
    s.check("and the owner is told why",
            any("not applied" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]} — silently ignoring it means the guest is "
                   "told one figure and invoiced another")
    s.check("no redemption was written", not _redemptions(f"{TAG}-ROOMS"))

    s.section("A cap that has been reached stops applying")
    capped = _code(f"{TAG}-ONE", percent=20, max_redemptions=1)
    first = _event("C")
    oc.post(f"/admin/events/{first['id']}/update",
            data={"status": "confirmed", "quoted_price": "1000",
                  "owner_note": "", "promo_code": f"{TAG}-ONE"},
            follow_redirects=True)
    s.check("the first event gets it",
            abs(float(_row(first["id"])["discount_amount"] or 0) - 200) < 0.01,
            detail=f"{_row(first['id'])['discount_amount']}")
    second = _event("D")
    r = oc.post(f"/admin/events/{second['id']}/update",
                data={"status": "confirmed", "quoted_price": "1000",
                      "owner_note": "", "promo_code": f"{TAG}-ONE"},
                follow_redirects=True)
    s.check("the second does not",
            not _row(second["id"])["discount_amount"],
            detail=f"{_row(second['id'])['discount_amount']} — this is the whole "
                   "point of recording the redemption")
    s.check("and only one redemption stands",
            len(_redemptions(f"{TAG}-ONE")) == 1,
            detail=f"{len(_redemptions(f'{TAG}-ONE'))}")

    s.section("A cancelled event owes nothing, discount or not")
    oc.post(f"/admin/events/{ev['id']}/update",
            data={"status": "cancelled", "quoted_price": "8000", "owner_note": ""},
            follow_redirects=True)
    bill = _bill(ev["id"])
    s.check("the total is zero", abs(bill["quoted"]) < 0.01, detail=f"{bill['quoted']}")
    s.check("and so is the discount, rather than a credit",
            abs(bill["discount"]) < 0.01,
            detail=f"{bill['discount']} — a discount on nothing reads as money owed "
                   "back")

    s.section("Guards")
    ev3 = _event("E")
    s.check("an employee cannot quote or discount an event",
            ec.post(f"/admin/events/{ev3['id']}/update",
                    data={"status": "confirmed", "quoted_price": "1",
                          "promo_code": f"{TAG}-WED"},
                    follow_redirects=False).status_code in (302, 403))
    s.check("and nothing was redeemed by trying",
            len(_redemptions(f"{TAG}-WED")) == 1)

    _cleanup()
    return s
