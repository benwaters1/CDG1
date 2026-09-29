"""Statements, line by line: every booking's own statement reads the way a
statement reads, and says what every other document says about the same money.

What there was: a stay's statement listed what was charged and then two totals,
"received" and "refunded", so a guest who paid in three goes saw one figure and
no dates, and a discount was a smaller number with no code beside it. An
atelier had no statement at all. Nothing anywhere said which card paid.

What this holds:

  - A stay's statement: every charge dated, the discount named by its code and
    what it promised; every payment on its own line with the card it came from;
    the refund on its own line, to the card it went back to; the total is the
    stay's bill, what is left is what the bill says, and the VAT invoice agrees.
  - An atelier's statement: the price in the pieces it was sold in, coming to
    the ledger's total; every payment with its card; what is left is what the
    ledger says. Not for search engines; an unknown link is a 404; the manage
    page and the owner's record of the guest lead to it.
  - The same money on every document: a booking's lines on its own statement
    are its lines on the person's statement of account, which names the card.
  - The card: asked of Stripe once, and only its brand and last four kept; one
    Stripe cannot name is not asked about again for a week; nothing is asked or
    written while Stripe is not connected; the nightly job names the rest, no
    more at a time than it is allowed; a guest's copy of what we hold includes
    the card, and erasing them takes it while the payment stays.
  - The notice says what is kept of a card.
"""
from datetime import timedelta

from _harness import Suite, db, house_today, visible_text, clients
import _harness

m = _harness.m
TAG = "ZZSL"
WHO = f"{TAG.lower()}@example.invalid"
ATELIER = f"{TAG.lower()}.atelier@example.invalid"
GONE = f"{TAG.lower()}.gone@example.invalid"
CARD = f"pi_{TAG}card"
DEPOSIT = f"cs_{TAG}deposit"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM refunds WHERE category = 'room' AND booking_id IN {stays}", like)
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", like)
        conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN {stays}",
                     like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        places = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {places}", like)
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                     "(SELECT id FROM workshops WHERE title LIKE ?)", like)
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", like)
        conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", like)
        conn.execute("DELETE FROM payment_cards WHERE ref LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM guests WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.commit()
    finally:
        conn.close()


class _FakeStripe:
    """PaymentIntents and Checkout Sessions that carry a card, and a record of
    every question asked of them."""

    def __init__(self, cards):
        self.cards = dict(cards)      # ref -> card dict, or an exception to raise
        self.asked = []
        outer = self

        def _charge(ref):
            outer.asked.append(ref)
            found = outer.cards.get(ref)
            if isinstance(found, Exception):
                raise found
            if found is None:
                raise RuntimeError(f"no such payment: {ref}")
            return {"payment_method_details": {"type": "card", "card": found}}

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


def _one(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _guest(email, name):
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (name, email, _harness.datetime_now()))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?", (email,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def _stay(conn, ref, email, arrive, room, **more):
    cols = {"room_id": room["id"], "reference_code": ref, "manage_token": (ref + "tok").lower(),
            "guest_name": f"{TAG} Guest", "guest_email": email,
            "arrival_date": arrive.isoformat(),
            "departure_date": (arrive + timedelta(days=2)).isoformat(), "party_size": 2,
            "status": "confirmed", "total_price": 0, "created_at": _harness.datetime_now()}
    cols.update(more)
    conn.execute(f"INSERT INTO bookings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 tuple(cols.values()))
    return conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (ref,)).fetchone()


def run():
    s = Suite("Statements, line by line")
    _cleanup()
    try:
        _run(s)
    finally:
        _cleanup()
    return s


def _run(s):
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    today = house_today()
    now = _harness.datetime_now()
    room = _harness.ensure_room()
    stay_gid = _guest(WHO, f"{TAG} Guest")
    atelier_gid = _guest(ATELIER, f"{TAG} Atelier Guest")

    # ---- a stay: a code off it, an extra, a card and a transfer, a refund ----
    conn = db()
    conn.execute("""INSERT INTO promo_codes (code, discount_type, discount_value, created_at)
                    VALUES (?, 'percent', 15, ?)""", (f"{TAG}MERCI", now))
    promo = conn.execute("SELECT id FROM promo_codes WHERE code = ?", (f"{TAG}MERCI",)).fetchone()["id"]
    arrive = today + timedelta(days=930)       # far out: no real stay on these dates
    stay = _stay(conn, f"{TAG}S", WHO, arrive, room, promo_code_id=promo, discount_amount=45,
                 linked_guest_id=stay_gid)
    bill = m.booking_bill(conn, stay["id"])
    room_line = next((l["amount"] for l in bill["lines"] if l["kind"] == "room"), 0)
    # What the invoice reads, set to what was agreed: the nights less the code.
    conn.execute("UPDATE bookings SET total_price = ? WHERE id = ?",
                 (round(room_line - 45, 2), stay["id"]))
    extra_at = (m.datetime.now(m.timezone.utc) - timedelta(days=3)).isoformat()
    conn.execute("""INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity,
                    status, created_at) VALUES ('room', ?, ?, 60, 1, 'confirmed', ?)""",
                 (stay["id"], f"{TAG} Champagne", extra_at))
    first = (m.datetime.now(m.timezone.utc) - timedelta(days=5)).isoformat()
    later = (m.datetime.now(m.timezone.utc) - timedelta(days=2)).isoformat()
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method,
                    stripe_payment_intent_id, created_at) VALUES (?, 300, 'stripe', ?, ?)""",
                 (stay["id"], CARD, first))
    card_payment = conn.execute("SELECT id FROM booking_payments WHERE stripe_payment_intent_id = ?",
                                (CARD,)).fetchone()["id"]
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                    VALUES (?, 150, 'bank_transfer', ?)""", (stay["id"], later))
    conn.execute("UPDATE bookings SET amount_paid = 450 WHERE id = ?", (stay["id"],))
    conn.execute("""INSERT INTO refunds (category, booking_id, reference_code, guest_name,
                    guest_email, amount, reason, method, payment_key, reduces_bill, created_at)
                    VALUES ('room', ?, ?, ?, ?, 50, 'goodwill', 'stripe', ?, 50, ?)""",
                 (stay["id"], f"{TAG}S", f"{TAG} Guest", WHO, f"bp{card_payment}", later))
    conn.execute("""INSERT INTO payment_cards (ref, brand, last4, wallet, fetched_at)
                    VALUES (?, 'visa', '4242', NULL, ?)""", (CARD, now))
    conn.commit()
    stay = conn.execute("SELECT bookings.*, rooms.name AS room_name FROM bookings "
                        "JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ?",
                        (stay["id"],)).fetchone()
    bill = m.booking_bill(conn, stay["id"])
    view = m.booking_statement_view(conn, "room", stay["id"])
    invoice = m.guest_statement(conn, stay)
    conn.close()

    s.section("A stay's statement, line by line")
    s.check("there is a stay to write a statement for", room_line > 0 and view is not None,
            detail=str(bill["lines"]))
    page = anon.get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True)
    text = visible_text(page)
    s.check("the discount is named by its code and what it promised",
            f"Discount: {TAG}MERCI (15%)" in text, detail=[x["what"] for x in view["charges"]])
    s.check("every charge is dated, an extra on the day it was added",
            all(x["day"] for x in view["charges"])
            and any(x["what"].startswith(f"{TAG} Champagne")
                    and x["day"] == m.house_date_iso(extra_at) for x in view["charges"]),
            detail=str([(x["day"], x["what"]) for x in view["charges"]]))
    s.check("each payment is on its own line, dated, by how, with the card it came from",
            [(x["paid"], x["method"], x["card_words"]) for x in view["payments"]]
            == [(300.0, "Card, online", "Visa ····4242"), (150.0, "Bank transfer", None)]
            and "Visa ····4242" in text and "€300.00" in text and "€150.00" in text,
            detail=str([(x["paid"], x["method"], x["card_words"]) for x in view["payments"]]))
    s.check("the refund is a line of its own, to the card it went back to",
            [(x["back"], x["card_words"]) for x in view["refunds"]] == [(50.0, "Visa ····4242")]
            and "Refunded, to the card" in text and "−€50.00" in text,
            detail=str([(x["back"], x["what"], x["card_words"]) for x in view["refunds"]]))
    s.check("the total is the stay's bill, and what is left is what the bill says",
            abs(view["total"] - bill["total"]) < 0.005 and abs(view["owed"] - bill["owed"]) < 0.005
            and "Balance due" in text and f"€{bill['owed']:,.2f}" in text,
            detail=f"statement {view['total']}/{view['owed']}, bill {bill['total']}/{bill['owed']}")
    s.check("and the VAT invoice comes to the same total",
            abs(invoice["total"] - view["total"]) < 0.005,
            detail=f"invoice {invoice['total']}, statement {view['total']}")
    # A stay that did not go ahead, its deposit kept under the terms.
    conn = db()
    off = _stay(conn, f"{TAG}C", WHO, arrive + timedelta(days=30), room, status="cancelled",
                total_price=400, amount_paid=100)
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                    VALUES (?, 100, 'bank_transfer', ?)""", (off["id"], first))
    conn.commit()
    conn.close()
    called_off = visible_text(anon.get(f"/booking/{off['manage_token']}/statement").get_data(as_text=True))
    s.check("a stay that did not go ahead charges what it kept, and no VAT on nights nobody stayed",
            "Kept on cancellation" in called_off and "VAT included in the above" not in called_off,
            detail=called_off[:400])

    # ---- an atelier: two places, a supplement, a code, a deposit on a card ----
    conn = db()
    conn.execute("INSERT INTO workshops (title, price_per_person, created_at) VALUES (?, 1300, ?)",
                 (f"{TAG} Autumn Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (f"{TAG} Autumn Atelier",)).fetchone()["id"]
    start = today + timedelta(days=940)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, created_at)
                    VALUES (?, ?, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    # Two places at 1,300, a single supplement of 200, 15% off the 2,800: 2,380.
    conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                    guest_name, guest_email, party_size, status, total_price, created_at,
                    promo_code_id, discount_amount, single_supplement)
                    VALUES (?, ?, ?, ?, ?, 2, 'confirmed', 2380, ?, ?, 420, 200)""",
                 (sid, f"{TAG}W", f"{TAG}wtok".lower(), f"{TAG} Atelier Guest", ATELIER, now, promo))
    wb = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?", (f"{TAG}W",)).fetchone()
    conn.execute("""INSERT INTO workshop_transactions (workshop_booking_id, kind, description,
                    amount, method, created_at, stripe_ref)
                    VALUES (?, 'payment', 'Deposit — Stripe', 700, 'stripe', ?, ?)""",
                 (wb["id"], first, DEPOSIT))
    conn.execute("""INSERT INTO workshop_transactions (workshop_booking_id, kind, description,
                    amount, created_at) VALUES (?, 'charge', ?, 80, ?)""",
                 (wb["id"], f"{TAG} Airport transfer", later))
    conn.execute("""INSERT INTO payment_cards (ref, brand, last4, wallet, fetched_at)
                    VALUES (?, 'mastercard', '4444', NULL, ?)""", (DEPOSIT, now))
    conn.commit()
    due, charged, paid = m.workshop_balance_due(conn, wb["id"])
    wview = m.booking_statement_view(conn, "workshop", wb["id"])
    conn.close()

    s.section("An atelier's own statement")
    r = anon.get(f"/workshops/manage/{wb['manage_token']}/statement")
    wpage = r.get_data(as_text=True)
    wtext = visible_text(wpage)
    whats = [x["what"] for x in wview["charges"]]
    s.check("it opens from the link the guest keeps", r.status_code == 200, detail=str(r.status_code))
    s.check("the price is in the pieces it was sold in: the places, the supplement, the code",
            any(w.startswith(f"{TAG} Autumn Atelier") and w.endswith("2 places") for w in whats)
            and "Single supplement" in whats and f"Discount: {TAG}MERCI (15%)" in whats
            and [x["charge"] for x in wview["charges"]
                 if x["line"] in ("programme", "supplement", "discount")] == [2600.0, 200.0, -420.0],
            detail=str([(x["what"], x["charge"]) for x in wview["charges"]]))
    s.check("and they come to what the ledger says it charged",
            abs(wview["total"] - charged) < 0.005, detail=f"statement {wview['total']}, ledger {charged}")
    s.check("every payment with the card it came from",
            [(x["paid"], x["card_words"]) for x in wview["payments"]] == [(700.0, "Mastercard ····4444")]
            and "Mastercard ····4444" in wtext,
            detail=str([(x["paid"], x["card_words"]) for x in wview["payments"]]))
    s.check("what is left is what the ledger says",
            abs(wview["owed"] - due) < 0.005 and "Balance due" in wtext and f"€{due:,.2f}" in wtext,
            detail=f"statement {wview['owed']}, ledger {due}")
    s.check("it is not for search engines", 'name="robots" content="noindex' in wpage)
    s.check("an unknown link is a 404",
            anon.get("/workshops/manage/nope/statement").status_code == 404)
    manage = anon.get(f"/workshops/manage/{wb['manage_token']}").get_data(as_text=True)
    s.check("the guest's manage page leads to it",
            f"/workshops/manage/{wb['manage_token']}/statement" in manage)
    record = oc.get(f"/guests/{atelier_gid}").get_data(as_text=True)
    s.check("and so does the owner's record of the guest",
            f"/workshops/manage/{wb['manage_token']}/statement" in record)

    s.section("The same money on every document")
    conn = db()
    account = m.guest_account_statement(conn, stay_gid)
    conn.close()
    shape = lambda xs: sorted((x["what"], x["charge"], x["paid"], x["back"]) for x in xs)
    mine = [x for x in account["lines"] if x["ref"] == f"{TAG}S"]
    s.check("a stay's lines on its own statement are its lines on the person's statement",
            mine and shape(mine) == shape(view["lines"]),
            detail=f"{shape(mine)} vs {shape(view['lines'])}")
    s.check("which names the card too",
            any(x["card_words"] == "Visa ····4242" and x["paid"] == 300.0 for x in mine),
            detail=str([(x["what"], x.get("card_words")) for x in mine if x["paid"]]))

    s.section("The card: asked once, and only what a statement needs")
    fresh, broken = f"pi_{TAG}fresh", f"cs_{TAG}broken"
    fake = _FakeStripe({
        fresh: {"brand": "amex", "last4": "0005", "exp_month": 1, "exp_year": 2031,
                "fingerprint": f"fp{TAG}secret", "wallet": {"type": "apple_pay"}},
        broken: RuntimeError("Stripe could not say")})
    conn = db()
    row = _with_stripe(fake, lambda: m.remember_card(conn, fresh))
    conn.commit()
    kept = conn.execute("SELECT * FROM payment_cards WHERE ref = ?", (fresh,)).fetchone()
    s.check("it is asked of Stripe, and only its brand and last four are kept",
            row is not None and kept is not None
            and (kept["brand"], kept["last4"], kept["wallet"]) == ("amex", "0005", "apple_pay")
            and set(kept.keys()) == {"ref", "brand", "last4", "wallet", "fetched_at"}
            and "2031" not in str(tuple(kept)) and "secret" not in str(tuple(kept)),
            detail=str(dict(kept)) if kept else "nothing kept")
    s.check("and read as a guest would say it",
            m.card_words(kept) == "American Express ····0005 (Apple Pay)", detail=m.card_words(kept))
    # A month on, well past the week a card Stripe could not name waits: a card
    # that WAS named is still not asked about, and is still there to read.
    conn.execute("UPDATE payment_cards SET fetched_at = ? WHERE ref = ?",
                 ((m.datetime.now(m.timezone.utc) - timedelta(days=30)).isoformat(), fresh))
    again = _with_stripe(fake, lambda: m.remember_card(conn, fresh))
    s.check("once named, it is not asked about again, however long ago",
            fake.asked.count(fresh) == 1 and again is not None and again["last4"] == "0005",
            detail=f"{fake.asked} / {dict(again) if again else None}")
    first_try = _with_stripe(fake, lambda: m.remember_card(conn, broken))
    second_try = _with_stripe(fake, lambda: m.remember_card(conn, broken))
    conn.commit()
    s.check("a card Stripe cannot name is not asked about again for a week",
            first_try is None and second_try is None and fake.asked.count(broken) == 1
            and conn.execute("SELECT last4 FROM payment_cards WHERE ref = ?",
                             (broken,)).fetchone() is not None,
            detail=str(fake.asked))
    offline = m.remember_card(conn, f"pi_{TAG}offline")
    s.check("with Stripe not connected nothing is asked, and nothing written",
            offline is None and conn.execute("SELECT 1 FROM payment_cards WHERE ref = ?",
                                             (f"pi_{TAG}offline",)).fetchone() is None)

    # Two card payments nobody has named yet, on a stay of their own.
    job_stay = _stay(conn, f"{TAG}J", WHO, arrive + timedelta(days=10), room, amount_paid=20)
    for n in (1, 2):
        conn.execute("""INSERT INTO booking_payments (booking_id, amount, method,
                        stripe_payment_intent_id, created_at) VALUES (?, 10, 'stripe', ?, ?)""",
                     (job_stay["id"], f"pi_{TAG}job{n}", now))
    conn.commit()
    jobs = _FakeStripe({f"pi_{TAG}job{n}": {"brand": "visa", "last4": f"000{n}"} for n in (1, 2)})
    _with_stripe(jobs, lambda: m.run_card_details_job(conn, limit=1))
    s.check("the nightly job asks no more at a time than it is allowed", len(jobs.asked) == 1,
            detail=str(jobs.asked))
    said = _with_stripe(jobs, lambda: m.run_card_details_job(conn, limit=10000))
    named = [r["ref"] for r in conn.execute(
        "SELECT ref FROM payment_cards WHERE ref LIKE ? AND last4 IS NOT NULL",
        (f"pi_{TAG}job%",)).fetchall()]
    s.check("and names the rest when it runs again, saying how many",
            sorted(named) == [f"pi_{TAG}job1", f"pi_{TAG}job2"] and "card(s) named" in said,
            detail=f"{named} / {said}")
    s.check("it is on the list of jobs, with a switch and a label",
            "card_details" in [j[0] for j in m.AUTOMATION_JOBS]
            and "card_details" in m.AUTOMATION_JOB_LABELS
            and "automation_card_details_enabled" in m.get_automation_settings(conn))
    conn.close()

    s.section("It goes with the person's data")
    conn = db()
    copy = m.guest_data_export(conn, WHO)
    conn.close()
    s.check("a guest's copy of what we hold includes the card their payment came from",
            any(r["ref"] == CARD and r["last4"] == "4242"
                for r in copy["tables"].get("payment_cards", [])),
            detail=str(sorted(copy["tables"])))
    conn = db()
    gone = _stay(conn, f"{TAG}G", GONE, arrive + timedelta(days=20), room, amount_paid=90)
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method,
                    stripe_payment_intent_id, created_at) VALUES (?, 90, 'stripe', ?, ?)""",
                 (gone["id"], f"pi_{TAG}gone", now))
    conn.execute("""INSERT INTO payment_cards (ref, brand, last4, wallet, fetched_at)
                    VALUES (?, 'visa', '1881', NULL, ?)""", (f"pi_{TAG}gone", now))
    conn.commit()
    result = m.guest_data_erase(conn, GONE)
    conn.commit()
    left = conn.execute("SELECT 1 FROM payment_cards WHERE ref = ?", (f"pi_{TAG}gone",)).fetchone()
    payment = conn.execute("SELECT amount FROM booking_payments WHERE stripe_payment_intent_id = ?",
                           (f"pi_{TAG}gone",)).fetchone()
    conn.close()
    s.check("and erasing them takes the card, while the payment stays",
            left is None and payment is not None and payment["amount"] == 90
            and (result or {}).get("deleted", {}).get("payment_cards") == 1,
            detail=str((result or {}).get("deleted")))

    s.section("The notice says so")
    notice = visible_text(anon.get("/privacy").get_data(as_text=True))
    s.check("what is kept of a card, and that nothing else is",
            "the card's brand and last four digits" in notice
            and "Nothing else about the card is kept" in notice)
