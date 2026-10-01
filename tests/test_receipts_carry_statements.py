"""Every card payment's receipt carries the statement, and the house hears of it.

What there was: a stay paid by card sent the guest the amount and what was
left, and nothing to show what it had paid for; an atelier's first deposit sent
a receipt of the same sort; an atelier's balance and an event paid by card sent
nothing at all. The house was told of none of them.

What this holds:

  - A stay paid by card: the guest's receipt carries the statement -- the
    lines, the card, what is left -- drawn as tables and written out, saying
    what the statement page says. The inbox that looks after stays is sent
    "Payment received from ...": the amount, the card, the reference, the
    statement and the way to the booking's payments; never filed as the guest's
    correspondence.
  - An atelier: the first deposit's receipt carries the statement; a balance,
    which was taken in silence, has a receipt of its own; the inbox that looks
    after ateliers hears of both.
  - An event paid by card has a receipt with its statement, and its inbox is told.
  - The statement a guest emails themselves from the page is the page's, line
    for line, the card included.
  - The three new letters are stored wording the owner can change, listed with
    when they go and to whom.
  - A letter with no statement is drawn exactly as before.
"""
from datetime import timedelta

from _harness import Suite, db, house_today, visible_text, clients
import _harness

m = _harness.m
TAG = "ZZRC"
WHO = f"{TAG.lower()}@example.invalid"
ATELIER = f"{TAG.lower()}.atelier@example.invalid"
EVENTS = f"{TAG.lower()}.event@example.invalid"


class _FakeStripe:
    """PaymentIntents and Checkout Sessions that carry a card."""

    def __init__(self, cards):
        self.cards = dict(cards)
        outer = self

        def _charge(ref):
            card = outer.cards.get(ref)
            if not card:
                raise RuntimeError(f"no such payment: {ref}")
            return {"payment_method_details": {"type": "card", "card": card}}

        class PaymentIntent:
            @staticmethod
            def retrieve(ref, expand=None):
                return {"id": ref, "latest_charge": _charge(ref)}

        class Session:
            @staticmethod
            def retrieve(ref, expand=None):
                return {"id": ref, "payment_intent": {"id": "pi_" + ref,
                                                      "latest_charge": _charge(ref)}}

        class Checkout:
            pass

        class Charge:
            @staticmethod
            def retrieve(ref):
                return None

        Checkout.Session = Session
        self.PaymentIntent, self.checkout, self.Charge = PaymentIntent, Checkout, Charge


def _with_stripe(fake, fn):
    saved = m.stripe, m.STRIPE_SECRET_KEY
    m.stripe, m.STRIPE_SECRET_KEY = fake, "sk_test_stand_in"
    try:
        return fn()
    finally:
        m.stripe, m.STRIPE_SECRET_KEY = saved


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        places = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {places}", like)
        conn.execute(f"DELETE FROM workshop_messages WHERE workshop_booking_id IN {places}", like)
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                     "(SELECT id FROM workshops WHERE title LIKE ?)", like)
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", like)
        events = "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM event_payments WHERE event_id IN {events}", like)
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM payment_cards WHERE ref LIKE ?", (f"%{TAG}%",))
        conn.commit()
    finally:
        conn.close()


def run():
    s = Suite("Receipts carry the statement")
    _cleanup()
    sent = []
    real_send = m.send_email

    def _capture(to, subject, body, *a, **k):
        sent.append({"to": to, "subject": subject, "body": body, "html": k.get("html") or "",
                     "key": k.get("template_key"), "about": k.get("about")})
        return True

    m.send_email = _capture
    try:
        _run(s, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _letters(sent, key):
    return [x for x in sent if x["key"] == key]


def _run(s, sent):
    oc, _ec, _owner, _emp = clients()
    today = house_today()
    now = _harness.datetime_now()
    room = _harness.ensure_room()
    fake = _FakeStripe({
        f"pi_{TAG}room": {"brand": "visa", "last4": "4242"},
        f"cs_{TAG}dep": {"brand": "mastercard", "last4": "4444"},
        f"cs_{TAG}bal": {"brand": "mastercard", "last4": "4444"},
        f"pi_{TAG}evt": {"brand": "amex", "last4": "0005"},
    })

    # ---- a stay, paid by card ----
    conn = db()
    arrive = today + timedelta(days=960)
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 0, ?)""",
                 (room["id"], f"{TAG}S", f"{TAG}stok".lower(), f"{TAG} Guest", WHO,
                  arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(), now))
    stay = conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()
    bill = m.booking_bill(conn, stay["id"])
    conn.execute("UPDATE bookings SET total_price = ? WHERE id = ?", (bill["total"], stay["id"]))
    conn.commit()
    session = {"id": f"cs_{TAG}room", "amount_total": 30000, "payment_intent": f"pi_{TAG}room",
               "payment_status": "paid",
               "metadata": {"kind": "room_balance", "booking_id": str(stay["id"])}}
    with m.app.test_request_context("/"):
        _with_stripe(fake, lambda: m.mark_booking_payment_paid(conn, session))
    conn.commit()
    view = m.booking_statement_view(conn, "room", stay["id"])
    house_rooms = m.house_address_for(conn, "rooms")
    conn.close()

    s.section("A stay paid by card")
    # By who it went to and what it says it is: a stay's letters go through
    # write_about_stay, which leaves the letter's key for send_email to find
    # by its subject -- and send_email is stood in for here.
    receipt = next((x for x in sent if x["to"] == WHO
                    and x["key"] in (None, "room_payment_received")
                    and "payment" in x["subject"].lower()), None)
    s.check("the guest has a receipt", receipt is not None, detail=str([x["key"] for x in sent]))
    receipt = receipt or {"body": "", "html": ""}
    s.check("which carries the statement: the lines, the card, what is left",
            "Statement" in receipt["body"] and "Visa ····4242" in receipt["body"]
            and "Total paid" in receipt["body"]
            and ("Balance due" in receipt["body"] or "Settled" in receipt["body"]),
            detail=receipt["body"][-600:])
    s.check("drawn as tables in the letter, not only written out",
            "<table" in receipt["html"] and "Visa ····4242" in receipt["html"]
            and "Total paid" in receipt["html"], detail=receipt["html"][-300:])
    s.check("saying what the statement page says",
            f"Total  €{view['total']:,.2f}" in receipt["body"]
            and f"Total paid  €{view['total_paid']:,.2f}" in receipt["body"],
            detail=f"view {view['total']} / {view['total_paid']}")
    notice = next((x for x in _letters(sent, "house_payment_received") if x["to"] == house_rooms), None)
    s.check("the inbox that looks after stays is told", notice is not None,
            detail=f"{house_rooms}: {[(x['to'], x['key']) for x in sent]}")
    notice = notice or {"subject": "", "body": "", "about": "x"}
    s.check("who paid, how much, by which card, for which booking",
            notice["subject"] == f"Payment received from {TAG} Guest"
            and "Amount: €300.00" in notice["body"] and "How: Card, online, Visa ····4242" in notice["body"]
            and f"{TAG}S" in notice["body"], detail=notice["body"][:500])
    s.check("with the statement, and the way to the booking's payments",
            "Total paid" in notice["body"] and f"/admin/refunds/room/{stay['id']}" in notice["body"],
            detail=notice["body"][-400:])
    s.check("and it is not filed as the guest's correspondence",
            notice["about"] is None and notice.get("to") != WHO)

    # ---- an atelier: the first deposit, then the balance ----
    conn = db()
    conn.execute("INSERT INTO workshops (title, price_per_person, created_at) VALUES (?, 1000, ?)",
                 (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = today + timedelta(days=970)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, created_at)
                    VALUES (?, ?, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                    guest_name, guest_email, party_size, status, total_price, deposit_amount,
                    balance_amount, created_at) VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 1000, 300,
                    700, ?)""",
                 (sid, f"{TAG}W", f"{TAG}wtok".lower(), f"{TAG} Atelier Guest", ATELIER, now))
    wb = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?", (f"{TAG}W",)).fetchone()
    conn.commit()
    deposit = {"id": f"cs_{TAG}dep", "amount_total": 30000, "payment_intent": None,
               "payment_status": "paid",
               "metadata": {"kind": "workshop_deposit", "workshop_booking_id": str(wb["id"])}}
    balance = {"id": f"cs_{TAG}bal", "amount_total": 70000, "payment_intent": None,
               "payment_status": "paid",
               "metadata": {"kind": "workshop_balance", "workshop_booking_id": str(wb["id"])}}
    house_ateliers = m.house_address_for(conn, "workshops")
    with m.app.test_request_context("/"):
        _with_stripe(fake, lambda: m.mark_workshop_payment_paid(conn, deposit))
        _with_stripe(fake, lambda: m.mark_workshop_payment_paid(conn, balance))
    conn.commit()
    conn.close()

    s.section("An atelier, paid by card")
    first = next((x for x in _letters(sent, "workshop_deposit_receipt") if x["to"] == ATELIER), None)
    s.check("the first deposit's receipt carries the statement, card and all",
            first is not None and "Statement" in first["body"]
            and "Mastercard ····4444" in first["body"] and "<table" in first["html"],
            detail=(first or {}).get("body", "")[-500:])
    later = next((x for x in _letters(sent, "workshop_payment_received") if x["to"] == ATELIER), None)
    s.check("the balance, taken in silence until now, has a receipt of its own",
            # The sentence that says it, not the figure anywhere: the statement
            # beneath lists the payment too, and would pass for a receipt that
            # got the amount wrong.
            later is not None and f"we have received €700.00 towards {TAG} Atelier" in later["body"]
            and "paid in full" in later["body"], detail=(later or {}).get("body", "")[:500])
    s.check("with the statement beneath it, settled",
            later is not None and "Settled" in later["body"] and "<table" in later["html"],
            detail=(later or {}).get("body", "")[-400:])
    told = [x for x in _letters(sent, "house_payment_received") if x["to"] == house_ateliers]
    s.check("and the inbox that looks after ateliers hears of both",
            sorted(a for a in ("Amount: €300.00", "Amount: €700.00")
                   for x in told if a in x["body"]) == ["Amount: €300.00", "Amount: €700.00"],
            detail=f"{house_ateliers}: {[x['subject'] for x in told]}")

    # ---- an event, paid by card ----
    conn = db()
    conn.execute("""INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                    contact_name, contact_email, contact_phone, preferred_date, guest_count,
                    message, status, quoted_price, amount_paid, created_at)
                    VALUES (?, ?, 'wedding', ?, ?, '', ?, 80, 'ZZ test', 'confirmed', 9000, 0, ?)""",
                 (f"{TAG}E", f"{TAG}etok".lower(), f"{TAG} Couple", EVENTS,
                  (today + timedelta(days=980)).isoformat(), now))
    ev = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?", (f"{TAG}E",)).fetchone()
    conn.commit()
    paid = {"id": f"cs_{TAG}evt", "amount_total": 250000, "payment_intent": f"pi_{TAG}evt",
            "payment_status": "paid", "metadata": {"event_id": str(ev["id"])}}
    house_events = m.house_address_for(conn, "events")
    with m.app.test_request_context("/"):
        credited = _with_stripe(fake, lambda: m.record_event_checkout(conn, paid))
    conn.commit()
    conn.close()

    s.section("An event, paid by card")
    evr = next((x for x in _letters(sent, "event_payment_received") if x["to"] == EVENTS), None)
    s.check("the contact has a receipt, with the statement and the card",
            credited and evr is not None and "we have received €2,500.00 towards your wedding" in evr["body"]
            and "American Express ····0005" in evr["body"] and "Balance due" in evr["body"],
            detail=(evr or {}).get("body", "")[-500:])
    s.check("and the inbox that looks after events is told",
            any(x["to"] == house_events and "Amount: €2,500.00" in x["body"]
                for x in _letters(sent, "house_payment_received")),
            detail=house_events)

    s.section("The statement a guest sends themselves is the page's")
    before = len(sent)
    oc.post(f"/booking/{stay['manage_token']}/statement/email")
    mine = [x for x in sent[before:] if x["to"] == WHO]
    s.check("line for line, the card included",
            mine and "Visa ····4242" in mine[0]["body"] and "Total paid" in mine[0]["body"],
            detail=mine[0]["body"][-400:] if mine else str([x["to"] for x in sent[before:]]))

    s.section("The letters are the owner's to word")
    conn = db()
    rows = {r["template_key"] for r in conn.execute(
        "SELECT template_key FROM email_templates WHERE template_key IN "
        "('workshop_payment_received', 'event_payment_received', 'house_payment_received')")}
    conn.close()
    s.check("the three new letters are stored wording",
            rows == {"workshop_payment_received", "event_payment_received", "house_payment_received"},
            detail=str(rows))
    listed = oc.get("/management/email-templates").get_data(as_text=True)
    s.check("listed where the owner changes wording, with when they go and to whom",
            all(k in m.EMAIL_TEMPLATE_INFO for k in rows)
            and "House: Payment received" in listed and "Workshop: Payment received" in listed)

    s.section("A letter with no statement is drawn as before")
    with m.app.test_request_context("/"):
        plain = m.letter_html("A plain letter", "Hello,\n\nNothing owed here.")
    s.check("no statement where none was asked for",
            plain and "Nothing owed here." in plain and "Total paid" not in plain)
