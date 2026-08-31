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
