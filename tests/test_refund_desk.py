"""The refund desk: money going back against the payment it came in by.

What this holds, each of which was wrong before it:

  - A refund goes to the card the money came from. A stay settled in two card
    payments -- one of them a friend's share -- was refunded to whichever card
    the booking had heard of first.
  - A stay paid only in part can be refunded. The engine asked payment_status,
    which says 'paid' only once a stay is paid in full.
  - A goodwill refund on a booking that still stands does not put the money
    back on the bill. It came off what was received and nothing else, so the
    Pay button, the balance chase and -- on an atelier -- the saved card all
    asked for it again.
  - An event can be refunded. There was no way to at all.
  - A refund typed into an atelier's ledger goes through the engine, with its
    ceiling, instead of around it.
  - A deposit taken on a request that is then declined goes back.
  - The guest is told, in a letter the owner can edit.

Stripe is stood in for throughout and put back in a `finally`; mail is caught.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRD"


def _cleanup():
    conn = db()
    stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
    regs = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    events = "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)"
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
    conn.execute(f"DELETE FROM booking_shares WHERE booking_id IN {stays}", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {regs}",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM event_payments WHERE event_id IN {events}", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


class _FakeStripe:
    """Stands in for the module. Records every refund asked for, answers a
    Checkout Session with the intent behind it, and can be told to refuse."""

    def __init__(self, sessions=None, refuse=()):
        self.refunds, self.retrieved = [], []
        self.sessions, self.refuse = dict(sessions or {}), set(refuse)
        outer = self

        class Refund:
            @staticmethod
            def create(**kw):
                if kw.get("payment_intent") in outer.refuse:
                    raise RuntimeError("Stripe refused this refund")
                outer.refunds.append(kw)
                obj = type("StripeRefund", (), {})()
                obj.id = f"re_desk_{len(outer.refunds)}"
                return obj

        class Session:
            @staticmethod
            def retrieve(sid):
                outer.retrieved.append(sid)
                return {"id": sid, "payment_intent": outer.sessions.get(sid)}

        class Checkout:
            pass

        Checkout.Session = Session
        self.Refund, self.checkout = Refund, Checkout


def _with_stripe(fake, fn):
    real_stripe, real_key = m.stripe, m.STRIPE_SECRET_KEY
    m.stripe, m.STRIPE_SECRET_KEY = fake, "sk_test_stand_in"
    try:
        return fn()
    finally:
        m.stripe, m.STRIPE_SECRET_KEY = real_stripe, real_key


def _stay(ref, *, room_total=400.0, status="confirmed", payments=()):
    """A stay whose room is agreed at room_total, with payments of
    (amount, method, intent, session, share_name)."""
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=60)
    departure = arrival + timedelta(days=2)
    paid = round(sum(p[0] for p in payments), 2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, payment_status, total_price,
           amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?)""",
        (room, f"{TAG}{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} Guest {ref}",
         f"{TAG.lower()}{ref.lower()}@example.invalid", arrival.isoformat(),
         departure.isoformat(), status, "paid" if paid >= room_total else "unpaid",
         room_total, paid, datetime.now(timezone.utc).isoformat()))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    m.stamp_room_total(conn, bid, room_total, arrival.isoformat(), departure.isoformat())
    for n, (amount, method, intent, session_id, share) in enumerate(payments):
        conn.execute(
            """INSERT INTO booking_payments (booking_id, amount, method, stripe_session_id,
               stripe_payment_intent_id, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (bid, amount, method, session_id, intent,
             (datetime.now(timezone.utc) - timedelta(minutes=10 - n)).isoformat()))
        if share:
            conn.execute(
                """INSERT INTO booking_shares (booking_id, name, email, amount, status,
                   created_at, stripe_session_id) VALUES (?, ?, ?, ?, 'paid', ?, ?)""",
                (bid, share, "share@example.invalid", amount,
                 datetime.now(timezone.utc).isoformat(), session_id))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _workshop_reg(ref, *, total=1000.0, paid=1000.0, stripe_ref=None):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', ?, 10, 1, 95, ?, 10)""", (f"{TAG} Atelier {ref}", total, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (f"{TAG} Atelier {ref}",)).fetchone()["id"]
    start = house_today() + timedelta(days=90)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
           created_at) VALUES (?, ?, ?, 10, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} {ref}", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {ref}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
           status, reference_code, manage_token, created_at, total_price)
           VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, ?)""",
        (sid, f"{TAG} Maker {ref}", f"{TAG.lower()}.w{ref.lower()}@example.invalid",
         f"{TAG}{ref}", f"tokw{TAG}{ref}".lower(), now, total))
    rid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, rid, "payment", "Paid in full", paid,
                                   method="stripe", stripe_ref=stripe_ref)
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (rid,)).fetchone()
    conn.close()
    return row


def _event(ref, *, price=2000.0, paid=2000.0, card_session=None):
    conn = db()
    kinds = m.known_event_types(conn)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
           contact_email, contact_phone, preferred_date, guest_count, message, status,
           quoted_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, 40, 'ZZ', 'confirmed', ?, 0, ?)""",
        (f"{TAG}{ref}", f"toke{TAG}{ref}".lower(), (kinds or ["wedding"])[0],
         f"{TAG} Couple {ref}", f"{TAG.lower()}.e{ref.lower()}@example.invalid",
         (house_today() + timedelta(days=120)).isoformat(), price,
         datetime.now(timezone.utc).isoformat()))
    eid = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        if card_session:
            m.record_event_payment(conn, eid, paid, method="card_link", reference=card_session)
        else:
            m.record_event_payment(conn, eid, paid, method="bank_transfer", reference="VIR 123")
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE id = ?", (eid,)).fetchone()
    conn.close()
    return row


def _row(table, rid):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()


def _refunds(category, bid):
    conn = db()
    try:
        return conn.execute("SELECT * FROM refunds WHERE category = ? AND booking_id = ? ORDER BY id",
                            (category, bid)).fetchall()
    finally:
        conn.close()


def _call(fn, *args, **kw):
    conn = db()
    try:
        return fn(conn, *args, **kw)
    finally:
        conn.commit()
        conn.close()


def _bill(bid):
    return _call(m.booking_bill, bid)


def _payments(category, booking):
    return _call(m.payments_received, category, booking)


def run():
    s = Suite("The refund desk")
    oc, ec, _owner, _emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, **k: sent.append((to, subject, body)) or True
    try:
        _run(s, oc, ec, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, ec, sent):
    s.section("A refund goes to the card the money came from")
    split = _stay("SPLIT", payments=[(250.0, "stripe", "pi_first", "cs_first", None),
                                     (150.0, "stripe", "pi_friend", "cs_friend", "Friend")])
    listed = _payments("room", split)
    s.check("both card payments are listed, each with its own card",
            [(p["amount"], p["card"]) for p in listed] == [(250.0, "pi_first"), (150.0, "pi_friend")],
            detail=str([(p["amount"], p["card"]) for p in listed]))
    s.check("and the share says whose it was", listed[1]["payer"] == "Friend",
            detail=str(listed[1]["payer"]))
    fake = _FakeStripe()
    ok, err, _ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "room", split, 100, "Goodwill", method="stripe",
        payment_key=listed[1]["key"], reason_code="goodwill"))
    s.check("a refund against the friend's payment goes to the friend's card",
            ok and [r["payment_intent"] for r in fake.refunds] == ["pi_friend"],
            detail=f"{err} {[r['payment_intent'] for r in fake.refunds]}")
    s.check("and in cents", fake.refunds and fake.refunds[0]["amount"] == 10000,
            detail=str(fake.refunds[:1]))
    after = {p["key"]: p for p in _payments("room", split)}
    s.check("what can still go back on that payment drops by it",
            after[listed[1]["key"]]["refundable"] == 50.0
            and after[listed[0]["key"]]["refundable"] == 250.0,
            detail=str({k: v["refundable"] for k, v in after.items()}))

    both = _stay("BOTH", payments=[(250.0, "stripe", "pi_b1", "cs_b1", None),
                                   (150.0, "stripe", "pi_b2", "cs_b2", None)])
    fake = _FakeStripe()
    ok, err, ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "room", both, 400, "The house called it off", method="stripe",
        reason_code="house_cancelled"))
    s.check("all of it goes back as one refund per card, newest first",
            ok and [(r["payment_intent"], r["amount"]) for r in fake.refunds]
            == [("pi_b2", 15000), ("pi_b1", 25000)],
            detail=f"{err} {[(r['payment_intent'], r['amount']) for r in fake.refunds]}")
    s.check("each written against its own payment",
            len(ids) == 2 and all(r["payment_key"] for r in _refunds("room", both["id"])),
            detail=str([r["payment_key"] for r in _refunds("room", both["id"])]))

    fake = _FakeStripe(refuse={"pi_c1"})
    half = _stay("HALF", payments=[(250.0, "stripe", "pi_c1", "cs_c1", None),
                                   (150.0, "stripe", "pi_c2", "cs_c2", None)])
    ok, err, ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "room", half, 400, "The house called it off", method="stripe",
        reason_code="house_cancelled"))
    s.check("when the second card refuses, the first refund still stands and it says so",
            not ok and len(ids) == 1 and "went back" in (err or "")
            and [r["amount"] for r in _refunds("room", half["id"])] == [150.0],
            detail=f"{ok} {err} {[r['amount'] for r in _refunds('room', half['id'])]}")

    by_session = _stay("SESS", payments=[(400.0, "stripe", None, "cs_only", None)])
    fake = _FakeStripe(sessions={"cs_only": "pi_behind_it"})
    ok, err, _ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "room", by_session, 40, "Goodwill", method="stripe",
        reason_code="goodwill"))
    s.check("a payment kept only by its Checkout session is refunded to the intent behind it",
            ok and fake.retrieved == ["cs_only"]
            and [r["payment_intent"] for r in fake.refunds] == ["pi_behind_it"],
            detail=f"{err} {fake.retrieved} {fake.refunds}")
    conn = db()
    kept = conn.execute("SELECT stripe_payment_intent_id FROM booking_payments WHERE booking_id = ?",
                        (by_session["id"],)).fetchone()[0]
    conn.close()
    s.check("and the intent is kept, so the next refund need not ask", kept == "pi_behind_it",
            detail=str(kept))

    s.section("A stay paid only in part can be refunded")
    part = _stay("PART", payments=[(100.0, "cash", None, None, None)])
    s.check("it is not paid in full", _row("bookings", part["id"])["payment_status"] == "unpaid")
    ok, err, _ids = _call(m.make_refund, "room", part, 100, "They cancelled", method="cash",
                          reason_code="guest_cancelled")
    s.check("the deposit taken at the desk can go back", ok, detail=str(err))
    s.check("and more than was taken cannot", not _call(
        m.make_refund, "room", part, 1, "Again", method="cash")[0])

    s.section("A goodwill refund does not put the money back on the bill")
    good = _stay("GOOD", payments=[(400.0, "cash", None, None, None)])
    s.check("a stay paid in full owes nothing", _bill(good["id"])["owed"] == 0.0)
    ok, err, _ids = _call(m.make_refund, "room", good, 50, "Goodwill: the pool was shut",
                          method="cash", reason_code="goodwill", bill_effect=m.BILL_REDUCED)
    bill = _bill(good["id"])
    s.check("a goodwill refund on it still leaves nothing owed", ok and bill["owed"] == 0.0,
            detail=f"{err} owed {bill['owed']}")
    s.check("because the bill costs less by it, on a line of its own",
            bill["total"] == 350.0 and any(l["kind"] == "refund_reduction" and l["amount"] == -50.0
                                           for l in bill["lines"]),
            detail=f"total {bill['total']}, lines {[l['kind'] for l in bill['lines']]}")
    conn = db()
    listed_owing = [r for r in m.outstanding_balances(conn)
                    if r["kind"] == "stay" and r["booking"]["id"] == good["id"]]
    statement = m.guest_statement(conn, _row("bookings", good["id"]))
    conn.close()
    s.check("so nobody chases it", not listed_owing, detail=str(listed_owing))
    s.check("and the guest's own statement agrees with the bill",
            abs(statement["total"] - bill["total"]) < 0.01 and statement["balance"] <= 0.005
            and statement["taken_off"] == 50.0,
            detail=f"statement {statement['total']} / {statement['balance']}, bill {bill['total']}")
    page = oc.get(f"/booking/{good['manage_token']}/statement").get_data(as_text=True)
    s.check("with the reduction shown on it, not a smaller total with no reason",
            "Reduction, refunded to you" in page)

    stands = _stay("STAND", payments=[(400.0, "cash", None, None, None)])
    _call(m.make_refund, "room", stands, 50, "Paid the wrong way", method="cash",
          reason_code="wrong_card", bill_effect=m.BILL_STANDS)
    s.check("while a refund where the price stands is owed again, as it should be",
            _bill(stands["id"])["owed"] == 50.0, detail=str(_bill(stands["id"])["owed"]))

    credit = _stay("CRED", payments=[(450.0, "cash", None, None, None)])
    s.check("a stay paid 50 over is 50 in credit", _bill(credit["id"])["overpaid"] == 50.0)
    _call(m.make_refund, "room", credit, 80, "Goodwill", method="cash",
          reason_code="goodwill", bill_effect=m.BILL_REDUCED)
    after = _bill(credit["id"])
    s.check("refunding 80 hands back the 50 credit and takes only 30 off the bill",
            after["owed"] == 0.0 and after["overpaid"] == 0.0 and after["total"] == 370.0,
            detail=f"owed {after['owed']}, over {after['overpaid']}, total {after['total']}")

    s.section("On an atelier, the saved card is not left ready to take it again")
    reg = _workshop_reg("GOODW", paid=1000.0, stripe_ref="pi_atelier")
    _call(m.make_refund, "workshop", reg, 100, "Goodwill", method="other",
          reason_code="goodwill", bill_effect=m.BILL_REDUCED)
    due, charged, paid = _call(m.workshop_balance_due, reg["id"])
    s.check("a goodwill refund leaves nothing to collect", due == 0.0,
            detail=f"due {due}, charged {charged}, paid {paid}")
    conn = db()
    lines = [r["kind"] for r in conn.execute(
        "SELECT kind FROM workshop_transactions WHERE workshop_booking_id = ? ORDER BY id",
        (reg["id"],)).fetchall()]
    conn.close()
    s.check("because the ledger carries what came off the bill beside the refund",
            lines == ["payment", "refund", "discount"], detail=str(lines))
    fake = _FakeStripe()
    ok, err, _ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "workshop", reg, 100, "Goodwill", method="stripe",
        reason_code="goodwill", bill_effect=m.BILL_REDUCED))
    s.check("and an atelier paid by card can be refunded to that card",
            ok and [r["payment_intent"] for r in fake.refunds] == ["pi_atelier"],
            detail=f"{err} {fake.refunds}")

    s.section("An event can be refunded")
    ev = _event("EV", price=2000.0, paid=2000.0)
    _call(m.make_refund, "event", ev, 200, "Goodwill", method="bank_transfer",
          reason_code="goodwill", bill_effect=m.BILL_REDUCED)
    eb = _call(m.event_bill, ev["id"])
    s.check("a goodwill refund takes it off the quote and off what was received",
            eb["quoted"] == 1800.0 and eb["paid"] == 1800.0 and eb["owed"] == 0.0
            and eb["refunded"] == 200.0,
            detail=f"quoted {eb['quoted']}, paid {eb['paid']}, owed {eb['owed']}")
    ev2 = _event("EV2", price=2000.0, paid=2000.0)
    _call(m.make_refund, "event", ev2, 200, "Overpaid", method="bank_transfer",
          reason_code="wrong_card", bill_effect=m.BILL_STANDS)
    s.check("and one where the price stands is owed again",
            _call(m.event_bill, ev2["id"])["owed"] == 200.0)
    card_ev = _event("EVC", price=1000.0, paid=1000.0, card_session="cs_event")
    fake = _FakeStripe(sessions={"cs_event": "pi_event"})
    ok, err, _ids = _with_stripe(fake, lambda: _call(
        m.make_refund, "event", card_ev, 100, "Goodwill", method="stripe",
        reason_code="goodwill"))
    s.check("an event paid by card link goes back to that card",
            ok and [r["payment_intent"] for r in fake.refunds] == ["pi_event"],
            detail=f"{err} {fake.refunds}")

    s.section("The desk")
    desk = _stay("DESK", payments=[(300.0, "stripe", "pi_desk", "cs_desk", None),
                                   (100.0, "cash", None, None, None)])
    page = oc.get(f"/admin/refunds/room/{desk['id']}")
    text = page.get_data(as_text=True)
    s.check("it opens on the stay's payments", page.status_code == 200
            and "Card, online" in text and "Cash" in text and "Can go back" in text, page)
    s.check("and offers all of it, each payment back the way it came", 'value="all"' in text)
    r = oc.post(f"/admin/refunds/room/{desk['id']}", data={"payment": "", "amount": "10"},
                follow_redirects=True)
    s.check("a refund with no reason chosen is refused",
            not _refunds("room", desk["id"]) and any("why" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    r = oc.post(f"/admin/refunds/room/{desk['id']}",
                data={"payment": "", "amount": "10", "reason_code": "other"},
                follow_redirects=True)
    s.check("and 'something else' with no note says what is missing",
            not _refunds("room", desk["id"]) and any("note" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    sent.clear()
    fake = _FakeStripe()
    r = _with_stripe(fake, lambda: oc.post(
        f"/admin/refunds/room/{desk['id']}",
        data={"payment": "all", "reason_code": "house_cancelled", "bill": m.BILL_REDUCED,
              "tell_guest": "1"}, follow_redirects=True))
    rows = _refunds("room", desk["id"])
    s.check("all of it goes back: the card to the card, the cash recorded as cash",
            sorted((x["method"], x["amount"]) for x in rows) == [("cash", 100.0), ("stripe", 300.0)]
            and [k["payment_intent"] for k in fake.refunds] == ["pi_desk"],
            detail=f"{[(x['method'], x['amount']) for x in rows]} {flashes(r)}")
    letters = [x for x in sent if x[0] == desk["guest_email"]]
    s.check("and the guest is told, once, what went back",
            len(letters) == 1 and "€400.00" in letters[0][2] and TAG + "DESK" in letters[0][1],
            detail=str([x[1] for x in sent]))
    s.check("with its being told written down", all(x["guest_told_at"] for x in rows))
    s.check("and the refund is in the audit trail by its reference", _audit_count(TAG + "DESK") == 1)

    quiet = _stay("QUIET", payments=[(400.0, "cash", None, None, None)])
    sent.clear()
    oc.post(f"/admin/refunds/room/{quiet['id']}",
            data={"payment": "", "amount": "20", "reason_code": "goodwill", "method": "cash"})
    s.check("left unticked, nobody is written to",
            len(_refunds("room", quiet["id"])) == 1 and not sent, detail=str(sent))

    s.check("an employee cannot open it", ec.get(f"/admin/refunds/room/{quiet['id']}").status_code
            in (302, 403))
    ec.post(f"/admin/refunds/room/{quiet['id']}",
            data={"payment": "", "amount": "20", "reason_code": "goodwill", "method": "cash"})
    s.check("nor refund through it", len(_refunds("room", quiet["id"])) == 1)
    s.check("an unknown kind of booking is a 404",
            oc.get("/admin/refunds/spaceship/1").status_code == 404)
    s.check("and a booking that does not exist is a 404",
            oc.get("/admin/refunds/room/99999999").status_code == 404)
    evpage = oc.get(f"/admin/refunds/event/{ev['id']}")
    s.check("an event's desk opens too", evpage.status_code == 200
            and "Bank transfer" in evpage.get_data(as_text=True), evpage)
    wpage = oc.get(f"/admin/refunds/workshop/{reg['id']}")
    s.check("and an atelier's", wpage.status_code == 200, wpage)

    s.section("The lists lead to it")
    lst = oc.get("/admin/events?when=all").get_data(as_text=True)
    s.check("the events list has a way to refund an event that took money",
            f"/admin/refunds/event/{ev['id']}" in lst)
    reg_page = oc.get(f"/admin/workshops/registrations?q={TAG}GOODW&when=all").get_data(as_text=True)
    s.check("the register's ledger no longer offers a refund around the engine",
            'value="refund"' not in reg_page)

    s.section("The refunds list, by what, how and why")
    full = oc.get("/admin/refunds?q=" + TAG)
    text = full.get_data(as_text=True)
    s.check("it opens, searchable", full.status_code == 200 and TAG + "EV" in text, full)
    only_events = oc.get("/admin/refunds?q=" + TAG + "&product=Event").get_data(as_text=True)
    s.check("the What-for chip narrows it to events",
            TAG + "EV" in only_events and TAG + "DESK" not in only_events)
    csv_text = oc.get("/admin/refunds/export.csv?q=" + TAG + "&product=Event").get_data(as_text=True)
    s.check("and the export is the view, not everything",
            TAG + "EV" in csv_text and TAG + "DESK" not in csv_text, detail=csv_text[:200])

    s.section("A refund typed into an atelier's ledger goes through the engine")
    ledger = _workshop_reg("LEDG", paid=300.0)
    r = oc.post(f"/admin/workshops/registrations/{ledger['id']}/add-transaction",
                data={"kind": "refund", "description": "Cash back", "amount": "500",
                      "method": "cash"}, follow_redirects=True)
    s.check("more than was paid is refused",
            not _refunds("workshop", ledger["id"])
            and any("failed" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    oc.post(f"/admin/workshops/registrations/{ledger['id']}/add-transaction",
            data={"kind": "refund", "description": "Cash back", "amount": "100", "method": "cash"})
    rows = _refunds("workshop", ledger["id"])
    s.check("and what is within it is in the refunds record, not only the ledger",
            [(x["amount"], x["method"]) for x in rows] == [(100.0, "cash")],
            detail=str([(x["amount"], x["method"]) for x in rows]))

    s.section("A deposit taken on a request that is then declined goes back")
    pend = _stay("PEND", status="pending", payments=[(120.0, "stripe", "pi_pend", "cs_pend", None)])
    fake = _FakeStripe()
    sent.clear()
    _with_stripe(fake, lambda: oc.post(f"/admin/bookings/{pend['id']}/decline",
                                       follow_redirects=True))
    s.check("the part-paid request's deposit is refunded to its card",
            [(k["payment_intent"], k["amount"]) for k in fake.refunds] == [("pi_pend", 12000)]
            and [x["amount"] for x in _refunds("room", pend["id"])] == [120.0],
            detail=str(fake.refunds))
    declined = [x for x in sent if x[0] == pend["guest_email"]]
    s.check("and the guest's letter says so", declined and "refunded" in declined[-1][2],
            detail=str([x[1] for x in sent]))

    s.section("The letter is the owner's to word")
    conn = db()
    row = conn.execute("SELECT * FROM email_templates WHERE template_key = 'refund_issued'").fetchone()
    conn.close()
    s.check("it is a template, on the templates page", row is not None)
    s.check("and it cannot be saved without the amount",
            "amount" in m.REQUIRED_MERGE_TAGS.get("refund_issued", {}))


def _audit_count(ref):
    conn = db()
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'refund_issued' "
                            "AND target LIKE ?", ("%" + ref,)).fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    print(run().report())
