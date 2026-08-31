"""Money coming IN, sent to the accountant.

Pennylane only ever received expenses — money going out, one supplier invoice
at a time, when somebody pressed a button on it. Everything coming in reached
the accountant as a CSV somebody remembered to export, or not at all. Adding
credentials would not have changed that: there was no code that could send
revenue, so this is a thing that was missing rather than a thing switched off.

WHAT MATTERS HERE IS THAT AN INVOICE CANNOT GO TWICE. A second one is invisible
from this end — it has to be found and voided in Pennylane by somebody reading
a ledger — so the guard is a UNIQUE row rather than a flag, and a double press,
a retried request and a second person on another screen all have to collapse to
one invoice. That is the first thing checked after the happy path.

THE OTHER HALF is that the row is written only AFTER Pennylane accepts it.
Backwards, a stay would be marked as filed when it is not, and nothing would
ever look at it again — the failure that hides itself.

SAFETY. _harness replaces _pennylane_request with a function that raises,
because the token is live. The stand-in here sits ABOVE that, on the two client
calls, so anything this file forgets to stand in for hits the refusal rather
than the real account. The last section checks the refusal is back afterwards.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZPLREV"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM pennylane_exports WHERE source_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pennylane_exports WHERE kind = 'pos_day' AND source_id IN "
                 "(SELECT id FROM pos_closures WHERE period LIKE '2099%')")
    conn.execute("DELETE FROM pos_closures WHERE period LIKE '2099%'")
    conn.execute("DELETE FROM pennylane_exports WHERE kind = 'workshop' AND source_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, days_ago=10, status="confirmed", nights=2):
    conn = db()
    room = _harness.ensure_room()
    departure = date.today() - timedelta(days=days_ago)
    arrival = departure - timedelta(days=nights)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 600, 600, 4.40, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}@example.invalid", arrival.isoformat(), departure.isoformat(),
         status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
             JOIN rooms ON rooms.id = bookings.room_id
            WHERE reference_code = ?""", (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _exports(booking_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM pennylane_exports WHERE kind='booking' AND source_id = ?",
            (booking_id,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Revenue to Pennylane")
    _cleanup()
    oc, ec, owner, emp = clients()

    was_configured = m.pennylane_configured
    was_customer = m.pennylane_find_customer
    was_import = m.pennylane_import_customer_invoice
    sent, customers = [], []

    def fake_customer(name, email=None):
        customers.append((name, email))
        return True, 4242

    def fake_import(**kwargs):
        sent.append(kwargs)
        return True, {"id": f"pl-{len(sent)}"}

    try:
        m.pennylane_configured = lambda: True
        m.pennylane_find_customer = fake_customer
        m.pennylane_import_customer_invoice = fake_import

        s.section("A departed stay goes as a customer invoice")
        stay = _stay("A")
        conn = db()
        statement = m.guest_statement(conn, stay)
        ok, msg = m.send_booking_to_pennylane(conn, stay, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("it is accepted", ok, detail=msg)
        s.check("one invoice was created", len(sent) == 1, detail=f"{len(sent)}")
        if sent:
            body = sent[0]
            s.check("for the statement's total, to the penny",
                    abs(float(body["amount"]) - statement["total"]) < 0.01,
                    detail=f"{body['amount']} vs {statement['total']}")
            s.check("filed against a customer", body["customer_id"] == 4242)
            s.check("carrying the booking reference, so it can be traced back",
                    body["invoice_number"] == stay["reference_code"],
                    detail=f"{body['invoice_number']}")
            s.check("dated the day they left", body["date"] == stay["departure_date"],
                    detail=f"{body['date']}")
            s.check("and the lines add up to the total",
                    abs(sum(float(l["currency_amount"]) + float(l["currency_tax"])
                            for l in body["lines"]) - float(body["amount"])) < 0.02,
                    detail=f"{body['lines']} vs {body['amount']} — Pennylane "
                           "rejects an invoice whose lines do not add up")
            s.check("with the tourist tax on its own line at zero",
                    any(l["vat_rate"] == "FR_000" for l in body["lines"]),
                    detail=f"{[(l['label'], l['vat_rate']) for l in body['lines']]} — "
                           "taxe de sejour is collected for the commune, not income")
        s.check("and it is written down as sent", len(_exports(stay["id"])) == 1,
                detail=f"{len(_exports(stay['id']))}")

        s.section("It cannot go twice")
        # A second invoice is invisible from here and has to be voided in
        # Pennylane by somebody reading a ledger.
        for _ in range(3):
            conn = db()
            ok2, msg2 = m.send_booking_to_pennylane(conn, stay, user_id=owner["id"])
            conn.commit()
            conn.close()
        s.check("the second is refused", not ok2, detail=msg2)
        s.check("and says when the first went",
                "already sent" in msg2.lower(), detail=msg2)
        s.check("still one invoice", len(sent) == 1, detail=f"{len(sent)}")
        s.check("and still one row", len(_exports(stay["id"])) == 1,
                detail=f"{len(_exports(stay['id']))}")

        s.section("A refusal from Pennylane records nothing")
        # Written after acceptance, not before: the other way round marks a stay
        # as filed when it is not, and nothing looks at it again.
        refused = _stay("B")
        m.pennylane_import_customer_invoice = lambda **k: (False, "422 unprocessable")
        conn = db()
        ok3, msg3 = m.send_booking_to_pennylane(conn, refused, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("it fails", not ok3, detail=msg3)
        s.check("and says what Pennylane said", "422" in msg3, detail=msg3)
        s.check("nothing is marked as sent", not _exports(refused["id"]),
                detail="a stay would be filed as done having never gone")
        m.pennylane_import_customer_invoice = fake_import

        s.section("A customer that cannot be filed stops before the invoice")
        m.pennylane_find_customer = lambda name, email=None: (False, "no customer")
        before = len(sent)
        conn = db()
        ok4, msg4 = m.send_booking_to_pennylane(conn, refused, user_id=owner["id"])
        conn.close()
        s.check("it fails", not ok4, detail=msg4)
        s.check("and no invoice was attempted", len(sent) == before, detail=f"{len(sent)}")
        m.pennylane_find_customer = fake_customer

        s.section("A cancelled stay is not revenue")
        cancelled = _stay("C", status="cancelled")
        conn = db()
        ok5, msg5 = m.send_booking_to_pennylane(conn, cancelled)
        conn.close()
        s.check("refused", not ok5, detail=msg5)
        s.check("and nothing recorded", not _exports(cancelled["id"]))

        s.section("With no token it says which setting, not a network error")
        m.pennylane_configured = lambda: False
        fresh = _stay("D")
        before = len(sent)
        conn = db()
        ok6, msg6 = m.send_booking_to_pennylane(conn, fresh)
        conn.close()
        s.check("refused", not ok6, detail=msg6)
        s.check("naming the token", "PENNYLANE_API_TOKEN" in msg6, detail=msg6)
        s.check("and nothing was attempted", len(sent) == before)
        m.pennylane_configured = lambda: True

        s.section("An atelier is invoiced on its ledger, not its sticker price")
        # A registration picks up charges and discounts of its own. Invoicing
        # total_price would bill the advertised figure and leave every
        # adjustment out of the accounts.
        conn = db()
        sid = conn.execute(
            "SELECT id FROM workshop_sessions ORDER BY start_date LIMIT 1").fetchone()
        now2 = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
               party_size, status, reference_code, manage_token, created_at,
               total_price, deposit_amount, balance_amount)
               VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, 1000, 300, 700)""",
            (sid["id"], f"{TAG} Atelier", f"{TAG.lower()}.ws@example.invalid",
             f"{TAG}-WS", f"tok{TAG}ws", now2))
        conn.commit()
        wb = conn.execute(
            """SELECT workshop_bookings.*, workshops.title AS workshop_title,
                      workshop_sessions.start_date, workshop_sessions.end_date
                 FROM workshop_bookings
                 JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
                 JOIN workshops ON workshops.id = workshop_sessions.workshop_id
                WHERE workshop_bookings.reference_code = ?""", (f"{TAG}-WS",)).fetchone()
        # A discount of 100 on the ledger: the invoice must be 900, not 1000.
        m.add_workshop_transaction(conn, wb["id"], "discount", "ZZ returning guest", 100.0)
        conn.commit()
        before = len(sent)
        okw, msgw = m.send_workshop_to_pennylane(conn, wb, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("it is accepted", okw, detail=msgw)
        if len(sent) > before:
            body = sent[-1]
            s.check("charged on the ledger, not the sticker price",
                    abs(float(body["amount"]) - 900.0) < 0.01,
                    detail=f"{body['amount']} — 1000 is the advertised price and "
                           "100 was taken off on the ledger")
            s.check("the reference goes with it",
                    body["invoice_number"] == f"{TAG}-WS", detail=f"{body['invoice_number']}")
            s.check("and the line adds up to the total",
                    abs(float(body["lines"][0]["currency_amount"])
                        + float(body["lines"][0]["currency_tax"]) - 900.0) < 0.02,
                    detail=f"{body['lines']}")

        s.section("And an atelier cannot go twice")
        conn = db()
        okw2, msgw2 = m.send_workshop_to_pennylane(conn, wb, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("the second is refused", not okw2, detail=msgw2)
        s.check("and says when the first went", "already sent" in msgw2.lower(),
                detail=msgw2)

        s.section("A closed service day goes as one invoice, not one per diner")
        # Forty tables of two are not forty customers. The figures come off the
        # closure's own Z-report, which was hashed when the day was closed, so
        # the invoice and the report the house signed off cannot disagree.
        conn = db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO pos_closures (kind, period, gross_total, discount_total,
               service_total, taken_total, vat_json, by_method_json, ticket_count,
               covers, perpetual_total, prev_hash, hash, closed_at)
               VALUES ('day', ?, 330.0, 0, 0, 330.0, ?, '{}', 12, 24, 0, '', ?, ?)""",
            (f"2099-01-0{1}",
             '{"10.0": {"gross": 220.0, "vat": 20.0, "net": 200.0},'
             ' "20.0": {"gross": 110.0, "vat": 18.33, "net": 91.67}}',
             "zzhash", now))
        conn.commit()
        closure = conn.execute(
            "SELECT * FROM pos_closures WHERE period = '2099-01-01'").fetchone()
        before = len(sent)
        ok7, msg7 = m.send_pos_day_to_pennylane(conn, closure, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("it is accepted", ok7, detail=msg7)
        s.check("one invoice for the whole day", len(sent) == before + 1,
                detail=f"{len(sent) - before}")
        if len(sent) > before:
            body = sent[-1]
            s.check("for what the Z-report says was taken",
                    abs(float(body["amount"]) - 330.0) < 0.02,
                    detail=f"{body['amount']} vs 330.00 on the closure")
            s.check("split by rate, food and drink apart",
                    len(body["lines"]) == 2,
                    detail=f"{[(l['label'], l['vat_rate']) for l in body['lines']]}")
            s.check("at the French codes for those rates",
                    {l["vat_rate"] for l in body["lines"]} == {"FR_100", "FR_200"},
                    detail=f"{[l['vat_rate'] for l in body['lines']]}")
            s.check("and the lines add up to the day",
                    abs(sum(float(l["currency_amount"]) + float(l["currency_tax"])
                            for l in body["lines"]) - 330.0) < 0.02,
                    detail=f"{body['lines']}")
            s.check("dated the service day, not today",
                    body["date"] == "2099-01-01", detail=f"{body['date']}")

        s.section("And a day cannot go twice either")
        conn = db()
        ok8, msg8 = m.send_pos_day_to_pennylane(conn, closure, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("the second is refused", not ok8, detail=msg8)
        s.check("and says when the first went", "already sent" in msg8.lower(),
                detail=msg8)

        s.section("A month or year closure is not a day's takings")
        # Same table, three kinds of row. Sending a month after its days would
        # book the same money twice.
        conn = db()
        conn.execute(
            """INSERT INTO pos_closures (kind, period, gross_total, discount_total,
               service_total, taken_total, vat_json, by_method_json, ticket_count,
               covers, perpetual_total, prev_hash, hash, closed_at)
               VALUES ('month', '2099-01', 9000.0, 0, 0, 9000.0,
               '{"10.0": {"gross": 9000.0, "vat": 818.18, "net": 8181.82}}',
               '{}', 300, 600, 0, '', 'zzhash2', ?)""", (now,))
        conn.commit()
        month = conn.execute(
            "SELECT * FROM pos_closures WHERE kind='month' AND period='2099-01'").fetchone()
        before = len(sent)
        ok9, msg9 = m.send_pos_day_to_pennylane(conn, month)
        conn.commit()
        conn.close()
        s.check("refused", not ok9, detail=msg9)
        s.check("and nothing was sent", len(sent) == before,
                detail="the same takings would be booked twice")

        s.section("The page it is done from")
        r = oc.get("/management/revenue-to-send")
        s.check("it opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
        html = r.get_data(as_text=True)
        s.check("a stay still waiting is on it", f"{TAG} D" in html)
        s.check("the one already sent is marked so", "Sent" in html)
        s.check("and there is no send-everything button",
                "send all" not in html.lower(),
                detail="a wrong batch has to be voided by hand, invoice by invoice")

        s.section("Guards")
        before = len(sent)
        s.check("an employee cannot open it",
                ec.get("/management/revenue-to-send").status_code in (302, 403))
        code = ec.post(f"/management/revenue-to-send/{fresh['id']}").status_code
        s.check("nor send anything", code in (302, 403), detail=f"HTTP {code}")
        s.check("and none went", len(sent) == before)
        s.check("a stay that does not exist is a 404",
                oc.post("/management/revenue-to-send/999999").status_code == 404)
    finally:
        m.pennylane_configured = was_configured
        m.pennylane_find_customer = was_customer
        m.pennylane_import_customer_invoice = was_import

    s.section("And the live account is out of reach again")
    s.check("the client call is the real one", m.pennylane_import_customer_invoice is was_import)
    s.check("and the token is still cleared under test", not m.PENNYLANE_API_TOKEN,
            detail="the live Pennylane token is set during a run")
    raised = False
    try:
        m._pennylane_request("GET", "/customers")
    except AssertionError:
        raised = True
    s.check("and _pennylane_request still refuses to reach the network", raised,
            detail="the harness block was left off")

    _cleanup()
    return s
