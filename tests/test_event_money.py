"""An event had a price and no money.

event_inquiries carried a quoted_price and nothing else — no deposit, no amount
received, no payments. So the largest single transactions the house takes, a
wedding at several thousand euros, lived entirely outside it: no bill, no
balance, nothing on the debtors list, nothing to chase, and nothing for the
accountant. Rooms, the restaurant and the ateliers all had a full money model.

event_bill is now the ONE definition of what an event owes, the way booking_bill
is for a stay, so the events page, the debtors list and the invoice cannot
disagree about a figure. That is the first thing checked here.

TWO DELIBERATE DIFFERENCES from a stay, both checked:

  A payment at the desk REFUSES more than is owed rather than clamping it. On a
  guest's own button clamping is a kindness — they meant "all of it". Typed here
  with the file open it is far more likely a slip, and quietly taking less than
  the number in front of you is how a ledger and a bank statement start
  disagreeing.

  The invoice is dated on the EVENT, not on the quote. A wedding agreed in
  January and held in August belongs in August's books.

And amount_paid is additive, so a deposit followed by a balance accumulates
instead of overwriting — the mistake that made a stay look unpaid after its
second instalment.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZEVT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM event_payments WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE contact_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pennylane_exports WHERE kind = 'event' AND source_id IN "
                 "(SELECT id FROM event_inquiries WHERE contact_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _event(ref, *, price=4500.0, status="confirmed", held=None, paid=0.0, due=None):
    conn = db()
    kinds = m.known_event_types(conn)
    when = (held or (date.today() - timedelta(days=20))).isoformat()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, contact_phone, preferred_date, guest_count,
           message, status, quoted_price, amount_paid, balance_due_date, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, 80, 'ZZ test', ?, ?, ?, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), (kinds or ["wedding"])[0],
         f"{TAG} {ref}", f"{TAG.lower()}@example.invalid", when, status, price,
         paid, due, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _bill(event_id):
    conn = db()
    try:
        return m.event_bill(conn, event_id)
    finally:
        conn.close()


def _payments(event_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM event_payments WHERE event_id = ? ORDER BY id",
                            (event_id,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Event money")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("An event has a bill at all")
    ev = _event("A")
    bill = _bill(ev["id"])
    s.check("there is one", bill is not None)
    if bill:
        s.check("quoted at the price agreed", abs(bill["quoted"] - 4500) < 0.01,
                detail=f"{bill['quoted']}")
        s.check("nothing received yet", bill["paid"] == 0)
        s.check("and the whole of it owed", abs(bill["owed"] - 4500) < 0.01,
                detail=f"{bill['owed']}")

    s.section("A deposit, then a balance, accumulate")
    # Additive, not overwriting. Overwriting is what made a stay look unpaid
    # after its second instalment.
    oc.post(f"/admin/events/{ev['id']}/payment",
            data={"amount": "1500", "method": "bank_transfer",
                  "reference": "ZZ deposit"}, follow_redirects=True)
    s.check("the deposit comes off", abs(_bill(ev["id"])["owed"] - 3000) < 0.01,
            detail=f"{_bill(ev['id'])['owed']}")
    r = oc.post(f"/admin/events/{ev['id']}/payment",
                data={"amount": "3000", "method": "bank_transfer"},
                follow_redirects=True)
    after = _bill(ev["id"])
    s.check("and the balance settles it", after["owed"] <= 0.005,
            detail=f"{after['owed']}")
    s.check("with both received, not just the last",
            abs(after["paid"] - 4500) < 0.01,
            detail=f"{after['paid']} — an overwriting total would read 3000")
    s.check("it says settled rather than a number",
            any("settled" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("Each payment is a line, not just a bigger number")
    lines = _payments(ev["id"])
    s.check("two lines", len(lines) == 2, detail=f"{len(lines)}")
    if lines:
        s.check("with how it arrived", lines[0]["method"] == "bank_transfer")
        s.check("the reference a bank statement is matched by",
                "ZZ deposit" in (lines[0]["reference"] or ""),
                detail=f"{lines[0]['reference']!r}")
        s.check("and who recorded it", lines[0]["taken_by_user_id"] == owner["id"])

    s.section("More than is owed is refused, not clamped")
    over = _event("B", price=2000.0)
    r = oc.post(f"/admin/events/{over['id']}/payment",
                data={"amount": "2500"}, follow_redirects=True)
    s.check("nothing is recorded", _bill(over["id"])["paid"] == 0,
            detail=f"{_bill(over['id'])['paid']}")
    s.check("and it says what the figure is",
            any("outstanding" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("Money on an event with no price is refused")
    unpriced = _event("C", price=0.0)
    r = oc.post(f"/admin/events/{unpriced['id']}/payment", data={"amount": "500"},
                follow_redirects=True)
    s.check("nothing is recorded", not _payments(unpriced["id"]),
            detail="money against a figure nobody agreed")
    s.check("and it says to price it first",
            any("price" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("A cancelled event owes nothing")
    canx = _event("D", price=3000.0, status="cancelled")
    s.check("the quote survives for the record but nothing is owed",
            _bill(canx["id"])["owed"] == 0, detail=f"{_bill(canx['id'])['owed']}")

    s.section("An unpaid event is on the debtors list")
    # It could not be, before: there was no bill to put it there with.
    owing = _event("E", price=6000.0, held=date.today() - timedelta(days=5))
    conn = db()
    rows = {r["reference"]: r for r in m.outstanding_balances(conn)}
    conn.close()
    s.check("it appears", f"{TAG}-E" in rows, detail=f"{[k for k in rows if TAG in str(k)]}")
    if f"{TAG}-E" in rows:
        row = rows[f"{TAG}-E"]
        s.check("for what is owed on it", abs(row["owed"] - 6000) < 0.01,
                detail=f"{row['owed']}")
        s.check("marked as an event, not as a stay", row["kind"] == "event",
                detail=f"{row['kind']}")
        s.check("with the contact named where a guest would be",
                row["who"] == f"{TAG} E", detail=f"{row['who']}")
        s.check("and it does not claim to have a room",
                row["what"] != "", detail=f"{row['what']!r}")
        s.check("nor point at a room's manage page",
                row["link_endpoint"] != "manage_booking",
                detail=f"{row['link_endpoint']} — an event token on a room page")

    s.section("And the page renders with both kinds on it")
    html = oc.get("/management/outstanding").get_data(as_text=True)
    s.check("the page opens with an event on it", f"{TAG} E" in html)
    s.check("labelled as an event", "event" in html.lower())

    s.section("The invoice to the accountant")
    was_conf, was_cust, was_imp = (m.pennylane_configured, m.pennylane_find_customer,
                                   m.pennylane_import_customer_invoice)
    sent = []
    try:
        m.pennylane_configured = lambda: True
        m.pennylane_find_customer = lambda name, email=None: (True, 77)
        m.pennylane_import_customer_invoice = lambda **kw: (sent.append(kw), (True, {"id": "pl-e"}))[1]
        conn = db()
        ok, msg = m.send_event_to_pennylane(conn, ev, user_id=owner["id"])
        conn.commit()
        conn.close()
        s.check("it goes", ok, detail=msg)
        if sent:
            body = sent[0]
            s.check("for the price agreed", abs(float(body["amount"]) - 4500) < 0.01,
                    detail=f"{body['amount']}")
            s.check("dated the event, not the quote",
                    body["date"] == ev["preferred_date"],
                    detail=f"{body['date']} vs {ev['preferred_date']} — a wedding "
                           "agreed in January and held in August belongs in August")
            s.check("carrying the reference", body["invoice_number"] == f"{TAG}-A",
                    detail=f"{body['invoice_number']}")
            s.check("and the line adds up to the total",
                    abs(float(body["lines"][0]["currency_amount"])
                        + float(body["lines"][0]["currency_tax"]) - 4500) < 0.02,
                    detail=f"{body['lines']}")
        conn = db()
        ok2, msg2 = m.send_event_to_pennylane(conn, ev, user_id=owner["id"])
        conn.close()
        s.check("and it cannot go twice", not ok2, detail=msg2)
        s.check("saying when the first went", "already sent" in msg2.lower(), detail=msg2)

        s.section("An event that is not confirmed is not revenue")
        quoted = _event("F", price=9000.0, status="quoted")
        before = len(sent)
        conn = db()
        ok3, msg3 = m.send_event_to_pennylane(conn, quoted)
        conn.close()
        s.check("refused", not ok3, detail=msg3)
        s.check("and nothing was sent", len(sent) == before,
                detail="a quote is not money")
    finally:
        m.pennylane_configured = was_conf
        m.pennylane_find_customer = was_cust
        m.pennylane_import_customer_invoice = was_imp
    s.check("the real client calls are restored",
            m.pennylane_import_customer_invoice is was_imp)

    s.section("And it can be reached from the events page")
    # The route existed and nothing on that page linked to it, so a wedding
    # deposit could only be entered by somebody who knew the URL. The nav check
    # cannot see this: it is a POST, and POSTs are not browsed to.
    fresh = _event("G", price=1200.0)
    html = oc.get("/admin/events").get_data(as_text=True)
    s.check("the page opens with an event on it", f"{TAG} G" in html,
            detail="nothing below is being looked at")
    s.check("it shows what is still owed", "still to pay" in html.lower(),
            detail="a quote with no balance beside it tells you nothing")
    s.check("and offers somewhere to record a payment",
            f"/admin/events/{fresh['id']}/payment" in html,
            detail="the form exists and the page does not link to it")

    s.section("Guards")
    before = _bill(over["id"])["paid"]
    code = ec.post(f"/admin/events/{over['id']}/payment",
                   data={"amount": "100"}).status_code
    s.check("an employee cannot record a payment", code in (302, 403),
            detail=f"HTTP {code}")
    s.check("and nothing moved", _bill(over["id"])["paid"] == before)
    s.check("an event that does not exist is a 404",
            oc.post("/admin/events/999999/payment",
                    data={"amount": "100"}).status_code == 404)
    s.check("nor can an employee send one to the accountant",
            ec.post(f"/management/revenue-to-send/event/{ev['id']}"
                    ).status_code in (302, 403))

    _cleanup()
    return s
