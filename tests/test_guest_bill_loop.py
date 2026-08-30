"""Dinner on the till, onto the room, onto the account, paid by the guest.

One loop, four places, and it only works if every hand-off keeps the same
figure. A guest eats in the restaurant, says "put it on the room", and expects
to find it itemised on their bill, added to what they owe, visible on their
account without opening each booking, and payable without ringing anybody.

WHAT WAS ALREADY RIGHT: charging a tab to a room writes every line onto the
booking BY NAME, not one lump called "restaurant", and booking_bill reads
those extras. So the money arrives in the right place with the detail intact.

WHAT WAS NOT:

  - The receipt could not be emailed at all. pos_receipt prints from the
    browser, which covers somebody standing at the table and nobody else: not
    the guest who wants it for expenses, not the guest who has already left.
    And the print link only rendered while the tab was OPEN, so the moment a
    guest actually asks — having just paid — it was the one thing the screen
    no longer offered.

  - The account page printed each booking's PRICE and stopped. That is a
    different number from what is owed the moment anything is paid, and a
    different one again once a dinner lands on the bill as an extra. Somebody
    with two stays had to open both and do the subtraction to find out where
    they stood, and there was nothing to pay with.

THE CHECK THAT MATTERS MOST is the last one in the second section: a receipt
is transactional. A guest who has unsubscribed from offers must still get the
record of money they just handed over, and it must not carry a marketing
unsubscribe footer. Getting that backwards in either direction is the failure
— withholding a receipt, or dressing one up as a campaign.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZLOOP"
GUEST = "zzloop.guest@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM pos_payments WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE 'zzloop.%'")
    conn.execute("DELETE FROM guest_sessions WHERE email LIKE 'zzloop.%'")
    conn.commit()
    conn.close()


def _stay(ref, total=600.0, paid=0.0, status="confirmed", offset=40):
    conn = db()
    room = _harness.ensure_room()
    arrival = date.today() + timedelta(days=offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, ?, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", GUEST,
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(), status,
         total, paid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _menu():
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    ids = {}
    for name, course, price in ((TAG + " Soupe", "starter", 12.0),
                                (TAG + " Canard", "main", 32.0)):
        cur = conn.execute(
            """INSERT INTO menu_items (name, category, course, price, active,
               available, sold_in_pos, sort_order, created_at)
               VALUES (?, 'main', ?, ?, 1, 1, 1, 0, ?)""", (name, course, price, now))
        ids[course] = cur.lastrowid
    conn.commit()
    conn.close()
    return ids


def _bill(booking_id):
    conn = db()
    try:
        return m.booking_bill(conn, booking_id)
    finally:
        conn.close()


def _settle(booking_id):
    """Pay a stay off in full, whatever it actually comes to.

    Not by writing the number the fixture chose: booking_bill recomputes the
    stay from the room's nightly price rather than reading total_price, so a
    fixture that sets total_price=300 and amount_paid=300 still leaves a
    balance, and the "square account" checks fail for a reason that has nothing
    to do with the code under test.
    """
    owed = _bill(booking_id)["total"]
    conn = db()
    conn.execute("UPDATE bookings SET amount_paid = ? WHERE id = ?", (owed, booking_id))
    conn.commit()
    conn.close()


def _order(label):
    conn = db()
    try:
        return conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                            (label,)).fetchone()
    finally:
        conn.close()


def _account_page(client, email):
    """A real signed-in account page, reached the way a guest reaches it."""
    conn = db()
    token = _harness.secrets_token()
    conn.execute(
        """INSERT INTO guest_sessions (email, token, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (email, token, datetime.now(timezone.utc).isoformat(),
         (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()))
    conn.commit()
    conn.close()
    return client.get(f"/my-account/{token}")


def run():
    s = Suite("Bill loop: till to room to account")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    dish = _menu()
    stay = _stay("A")
    table = TAG + "9"

    sent = []
    real_send = m.send_email

    def capture(to_address, subject, body, *a, **kw):
        sent.append((to_address, subject, body))
        return True

    s.section("Dinner goes onto the room, itemised")
    oc.post("/pos/open", data={"table_label": table, "covers": "2"},
            follow_redirects=True)
    order = _order(table)
    s.check("a tab opens", bool(order), detail=f"{order}")
    for course in ("starter", "main"):
        oc.post(f"/pos/{order['id']}/add", data={"menu_item_id": dish[course]},
                follow_redirects=True)
    owed_before = _bill(stay["id"])["owed"]
    r = oc.post(f"/pos/{order['id']}/pay",
                data={"method": "room", "room_booking_id": str(stay["id"])},
                follow_redirects=True)
    s.check("it settles against the room", r.status_code == 200,
            detail=f"HTTP {r.status_code} {flashes(r)[:1]}")
    bill = _bill(stay["id"])
    labels = " | ".join(l["label"] for l in bill["lines"])
    s.check("both dishes are on the bill by name",
            TAG + " Soupe" in labels and TAG + " Canard" in labels,
            detail=f"{labels} — one lump called 'restaurant' is not a bill")
    s.check("and what they owe went up by the tab",
            abs(bill["owed"] - (owed_before + 44.0)) < 0.01,
            detail=f"{owed_before} -> {bill['owed']}, expected +44.00")

    s.section("The receipt can be emailed, and is offered after paying too")
    # The print link used to render only while the tab was open, so the moment
    # the guest asks for a receipt the screen no longer had one.
    page = oc.get(f"/pos/{order['id']}").get_data(as_text=True)
    s.check("the settled tab still offers the receipt",
            "/receipt" in page, detail="a paid guest cannot get their receipt")
    s.check("and a box to email it", "email-receipt" in page)
    s.check("prefilled with the address it went on the room for",
            GUEST in page, detail="the waiter retypes it off a phone screen")

    try:
        m.send_email = capture
        r = oc.post(f"/pos/{order['id']}/email-receipt",
                    data={"email": GUEST}, follow_redirects=True)
        s.check("it sends", len(sent) == 1, detail=f"{len(sent)} sent")
        if sent:
            to, subject, body = sent[0]
            s.check("to the guest", to == GUEST, detail=to)
            s.check("with the dishes named in it", TAG + " Canard" in body,
                    detail=f"{body[:160]!r}")
            s.check("and the total", "44.00" in body, detail=f"{body[:200]!r}")
            s.check("and a receipt number to quote",
                    "Receipt number" in body, detail=f"{body[-200:]!r}")
            # The check this file exists for, first half.
            s.check("and NO marketing unsubscribe footer",
                    "unsubscribe" not in body.lower(),
                    detail="a receipt is transactional; dressing it as a "
                           "campaign invites somebody to opt out of their own bill")
        s.check("the floor can see it went",
                (_order(table)["receipt_emailed_to"] or "") == GUEST,
                detail=f"{_order(table)['receipt_emailed_to']!r} — with no record "
                       "it gets sent three times because nobody can tell")

        s.section("A guest who unsubscribed still gets their receipt")
        # The other half. Withholding the record of money somebody just handed
        # over, because they once opted out of offers, is the worse mistake.
        conn = db()
        conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'ZZ', ?)",
                     (GUEST, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        sent.clear()
        oc.post(f"/pos/{order['id']}/email-receipt", data={"email": GUEST},
                follow_redirects=True)
        s.check("it still goes", len(sent) == 1,
                detail="an opt-out from marketing withheld a transactional receipt")
        conn = db()
        conn.execute("DELETE FROM email_optouts WHERE email LIKE 'zzloop.%'")
        conn.commit()
        conn.close()

        s.section("An address that is not one sends nothing")
        sent.clear()
        r = oc.post(f"/pos/{order['id']}/email-receipt",
                    data={"email": "not-an-address"}, follow_redirects=True)
        s.check("nothing is sent", not sent, detail=f"{sent[:1]}")
        s.check("and the waiter is told", any("email" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:1]}")

        s.section("An empty tab has no receipt to send")
        oc.post("/pos/open", data={"table_label": TAG + "E", "covers": "2"},
                follow_redirects=True)
        empty = _order(TAG + "E")
        sent.clear()
        r = oc.post(f"/pos/{empty['id']}/email-receipt", data={"email": GUEST},
                    follow_redirects=True)
        s.check("nothing is sent", not sent, detail=f"{sent[:1]}")
        s.check("and it says why", any("nothing" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:1]}")

        s.section("Guards")
        sent.clear()
        code = anon.post(f"/pos/{order['id']}/email-receipt",
                         data={"email": GUEST}).status_code
        s.check("a stranger cannot send one", code in (302, 401, 403),
                detail=f"HTTP {code}")
        s.check("and none went", not sent, detail=f"{sent[:1]}")
        s.check("a tab that does not exist is a 404",
                oc.post("/pos/999999/email-receipt", data={"email": GUEST}
                        ).status_code == 404)
    finally:
        m.send_email = real_send
    s.check("the real sender is restored", m.send_email is real_send)

    s.section("The account shows what is owed, not what it cost")
    second = _stay("B", total=300.0, offset=90)
    _settle(second["id"])
    page = _account_page(anon, GUEST)
    s.check("the account page opens", page.status_code == 200,
            detail=f"HTTP {page.status_code}")
    html = page.get_data(as_text=True)
    bill = _bill(stay["id"])
    s.check("the balance across both stays is shown",
            "Your balance" in html,
            detail="a guest with two stays had to open each one and subtract")
    s.check("and it is what is actually owed, not the price",
            f"{bill['owed']:.2f}" in html,
            detail=f"expected {bill['owed']:.2f} somewhere on the page")
    s.check("the settled stay is not asked for again",
            html.count("still to pay") == 1,
            detail=f"{html.count('still to pay')} stays asked for money")

    s.section("Paying it is offered only when a card can actually be taken")
    # Stripe is off under test, and a Pay button that leads to "card payment
    # isn't connected" is worse than no button.
    s.check("no Pay button while Stripe is off", "Pay €" not in html,
            detail="a dead end dressed as a payment")
    s.check("and it says what to do instead",
            "settle with us" in html.lower(), detail="the guest is left guessing")

    was = m.stripe_enabled
    try:
        m.stripe_enabled = lambda: True
        html = _account_page(anon, GUEST).get_data(as_text=True)
        s.check("with Stripe on, the stay can be paid from the account",
                "Pay €" in html, detail="the guest still has to ring somebody")
        s.check("for the amount outstanding",
                f"Pay €{bill['owed']:.2f}" in html,
                detail=f"expected 'Pay €{bill['owed']:.2f}'")
        s.check("and it goes to the booking's own payment page",
                f"/book/pay/{stay['manage_token']}" in html,
                detail="a second payment path would be a second thing to get wrong")
    finally:
        m.stripe_enabled = was
    s.check("stripe_enabled is back to the harness's answer", not m.stripe_enabled())

    s.section("An account with nothing outstanding says nothing")
    paid_up = _stay("C", total=200.0, offset=120)
    _settle(paid_up["id"])
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code IN (?, ?)",
                 (f"{TAG}-A", f"{TAG}-B"))
    conn.commit()
    conn.close()
    html = _account_page(anon, GUEST).get_data(as_text=True)
    s.check("no balance band on a square account", "Your balance" not in html,
            detail="a zero displayed is furniture, and furniture gets ignored")
    s.check("but the stay is still listed", paid_up["reference_code"] in html)

    _cleanup()
    return s
