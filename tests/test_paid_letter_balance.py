"""A guest who has paid is told so, in the letter that confirms the stay.

17 October 2026, GUD-CZ1XOG: a stay paid in full by card online. The house
letter said "Paid in full. Nothing to do", and the guest's confirmation, the
same minute, said the whole 833.20 was still to pay. The confirmation reads
the bill and the bill reads what has been received. The Stripe path recorded
the payment just AFTER create_booking had confirmed the stay and written to
the guest, so the letter saw a stay with nothing received against it.

Held here, by paying for a stay the way the webhook does and reading the
letter the guest is sent:
  - paid in full: "Paid in full: EUR X. There is nothing more to pay.", and no
    "still to pay" anywhere in it;
  - part paid: "Paid: EUR X. Still to pay: EUR Y";
  - nothing paid: the balance line as before.
"""
from datetime import timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "ZZPLB"
_n = [0]


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_payments WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _free(conn, room_id, start):
    while not m.is_range_available(conn, room_id, start, start + timedelta(days=2))[0]:
        start += timedelta(days=3)
    return start


def _session(room_id, arrival, total, paid):
    _n[0] += 1
    return {
        "id": f"cs_test_{TAG}_{_n[0]}",
        "payment_intent": f"pi_test_{TAG}_{_n[0]}",
        "amount_total": int(round(paid * 100)),
        "metadata": {
            "room_id": str(room_id), "guest_name": f"{TAG} Guest {_n[0]}",
            "guest_email": f"zzplb{_n[0]}@example.invalid", "guest_phone": "",
            "arrival_date": arrival.isoformat(),
            "departure_date": (arrival + timedelta(days=2)).isoformat(),
            "party_size": "2", "special_requests": "", "extra_ids": "",
            "promo_code": "", "total_price": f"{total:.2f}", "discount_amount": "0.00",
        },
    }


def _book(room, start, total, paid):
    """Pay for a stay as the webhook does; return (booking, letters sent)."""
    sent = []
    real_send, real_enabled = m.send_email, m.email_enabled

    def capture(to, subject, body, *a, **kw):
        sent.append((to, subject, body))
        return True

    m.send_email, m.email_enabled = capture, (lambda: True)
    conn = db()
    try:
        arrival = _free(conn, room["id"], start)
        with m.app.test_request_context("/"):
            token = m.create_booking_from_stripe_session(
                conn, _session(room["id"], arrival, total, paid))
        conn.commit()
        row = conn.execute("SELECT * FROM bookings WHERE manage_token = ?", (token,)).fetchone()
        bill = m.booking_bill(conn, row["id"]) if row else None
    finally:
        m.send_email, m.email_enabled = real_send, real_enabled
        conn.close()
    mine = [(subj, body) for to, subj, body in sent
            if row and (row["guest_email"] in (to if isinstance(to, str) else " ".join(to)))]
    return row, bill, mine


def run():
    s = Suite("A guest who has paid is told so")
    _cleanup()
    room = _harness.ensure_room()
    start = m.house_today() + timedelta(days=610)
    try:
        s.section("Paid in full by card")
        # The card is charged the stay AND the taxe de sejour, as GUD-CZ1XOG's
        # was (830.00 + 3.20); the bill carries the tax on top of total_price.
        conn = db()
        tax = m.compute_city_tax(conn, 2, 0, 2)
        conn.close()
        tax = tax[0] if isinstance(tax, (tuple, list)) else tax
        row, bill, letters = _book(room, start, 500.0, 500.0 + float(tax or 0))
        s.check("the stay is booked and paid", row is not None and bill and bill["owed"] < 0.01,
                detail=str(bill and (bill["total"], bill["paid"], bill["owed"])))
        total = bill["total"] if bill else 0
        s.check("the guest is written to", bool(letters), detail=str(len(letters)))
        text = "\n".join(b for _s, b in letters)
        s.check("and told it is paid in full",
                f"Paid in full: €{bill['paid']:,.2f}. There is nothing more to pay." in text,
                detail=text[:400])
        s.check("and never that any of it is still to pay",
                "still to pay" not in text.lower(), detail=text[:400])

        s.section("A deposit paid by card")
        # Priced above what was paid: the metadata's total is what the stay
        # costs, amount_total what the card was charged.
        row, bill, letters = _book(room, start + timedelta(days=40), 800.0, 240.0)
        text = "\n".join(b for _s, b in letters)
        owed = bill["owed"] if bill else 0
        s.check("the bill has the deposit received and the rest owed",
                bill and abs(bill["paid"] - 240) < 0.01 and owed > 0.01,
                detail=str(bill and (bill["total"], bill["paid"], bill["owed"])))
        s.check("the letter says both halves",
                f"Paid: €240.00. Still to pay: €{owed:,.2f}" in text, detail=text[:400])
        s.check("and does not say the whole price is owed",
                f"€{bill['total']:,.2f} still to pay" not in text, detail=text[:400])

        s.section("Paid in full, the guest landing on the success page first")
        # The other way a paid checkout becomes a stay: the guest's browser
        # reaches /book/stripe-success before the webhook does. Stripe's lookup
        # of the session is stood in; nothing reaches Stripe.
        conn = db()
        arrival = _free(conn, room["id"], start + timedelta(days=80))
        conn.close()
        session = _session(room["id"], arrival, 500.0, 500.0 + float(tax or 0))
        session["payment_status"] = "paid"
        sent = []

        def capture(to, subject, body, *a, **kw):
            sent.append((to if isinstance(to, str) else " ".join(to), body))
            return True

        saved = (m.send_email, m.email_enabled, m.stripe_enabled,
                 m.stripe.checkout.Session.retrieve)
        m.send_email, m.email_enabled = capture, (lambda: True)
        m.stripe_enabled = lambda: True
        m.stripe.checkout.Session.retrieve = (
            lambda sid, *a, **k: session if sid == session["id"] else None)
        try:
            r = m.app.test_client().get(f"/book/stripe-success?session_id={session['id']}")
        finally:
            (m.send_email, m.email_enabled, m.stripe_enabled,
             m.stripe.checkout.Session.retrieve) = saved
        conn = db()
        row = conn.execute("SELECT * FROM bookings WHERE stripe_session_id = ?",
                           (session["id"],)).fetchone()
        bill = m.booking_bill(conn, row["id"]) if row else None
        conn.close()
        s.check("the page books the stay and sends the guest on",
                r.status_code in (302, 303) and row is not None and bill and bill["owed"] < 0.01,
                detail=f"HTTP {r.status_code}, {bill and (bill['total'], bill['paid'])}")
        text = "\n".join(b for to, b in sent if session["metadata"]["guest_email"] in to)
        s.check("the guest's letter says it is paid in full",
                bool(bill) and f"Paid in full: €{bill['paid']:,.2f}." in text, detail=text[:400])
        s.check("and never that any of it is still to pay",
                bool(text) and "still to pay" not in text.lower(), detail=text[:400])
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
