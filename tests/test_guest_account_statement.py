"""One statement of account per person, dated, that adds up to every bill.

What there was: a bill per stay, a ledger per atelier, an event's bill, and a
lifetime page that added the confirmed ones up, netted refunds away without a
word and dated nothing. "Send me everything for my accountant", or "what did
I pay, and what came back", had no answer but copying figures by hand.

What this holds:

  - Every charge, payment and refund is a dated line with a running balance.
  - Booking by booking, it adds up to that booking's own bill -- the one
    definition for each kind -- and the whole to the sum of them.
  - A booking that came to nothing and moved no money is not on it; one that
    was cancelled with money still held shows that money as credit.
  - A stay booked under an address from before a merge is theirs.
  - A period carries what came before it in as the opening balance.
  - The owner can print it, export it and send it; the guest can open their
    own from the link they keep.

Mail is caught; nothing reaches a provider.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZAS"
NOW = datetime.now(timezone.utc)


def _cleanup():
    conn = db()
    try:
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
        regs = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
        events = "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)"
        conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
        conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN {stays}",
                     (TAG + "%",))
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {regs}",
                     (TAG + "%",))
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
        conn.execute(f"DELETE FROM event_payments WHERE event_id IN {events}", (TAG + "%",))
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
    finally:
        conn.close()


def _at(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def _guest(name, email, merged_into=None):
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at, merged_into_id) VALUES (?, ?, ?, ?)",
                 (f"{TAG} {name}", email, _harness.datetime_now(), merged_into))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def _stay(ref, email, *, status="confirmed", room_total=400.0, payments=(), decided=30,
          arrive_in=40, cancel_reason=None):
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=arrive_in)
    departure = arrival + timedelta(days=2)
    paid = round(sum(p[0] for p in payments), 2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, payment_status, total_price,
           amount_paid, created_at, decided_at, cancel_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?)""",
        (room, f"{TAG}{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} Guest", email,
         arrival.isoformat(), departure.isoformat(), status,
         "paid" if paid >= room_total else "unpaid", room_total, paid, _at(decided + 1),
         _at(decided), cancel_reason))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    m.stamp_room_total(conn, bid, room_total, arrival.isoformat(), departure.isoformat())
    for amount, method, days_ago in payments:
        conn.execute(
            """INSERT INTO booking_payments (booking_id, amount, method, created_at)
               VALUES (?, ?, ?, ?)""", (bid, amount, method, _at(days_ago)))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _call(fn, *args, **kw):
    conn = db()
    try:
        return fn(conn, *args, **kw)
    finally:
        conn.commit()
        conn.close()


def run():
    s = Suite("A guest's statement of account")
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
    email = f"{TAG.lower()}@example.invalid"
    old_email = f"{TAG.lower()}.before@example.invalid"
    gid = _guest("Amelie", email)
    _guest("Amelie (before)", old_email, merged_into=gid)

    # A stay paid in two goes, then a goodwill refund taken off its price.
    paid_stay = _stay("A", email, payments=[(100.0, "cash", 29), (300.0, "bank_transfer", 20)])
    _call(m.make_refund, "room", paid_stay, 50, "Goodwill: the pool was shut", method="cash",
          reason_code="goodwill", bill_effect=m.BILL_REDUCED)
    # A stay still owing.
    owing = _stay("B", email, payments=[(100.0, "cash", 10)], decided=12, arrive_in=60)
    # Called off by the house, with the deposit still held: theirs.
    _stay("C", email, status="cancelled", payments=[(80.0, "cash", 15)], decided=16,
          cancel_reason="house_could_not")
    # Cancelled by them, deposit kept under the non-refundable terms: the house's.
    _stay("F", email, status="cancelled", payments=[(120.0, "cash", 14)], decided=13,
          cancel_reason="plans_changed")
    # A request declined that moved no money: not on a statement.
    _stay("D", email, status="declined", decided=5)
    # Booked under the address used before the merge.
    before = _stay("E", old_email, payments=[(400.0, "cash", 40)], decided=41, arrive_in=5)

    conn = db()
    now = _harness.datetime_now()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                    active, sort_order, created_at, deposit_percent)
                    VALUES (?, '', 900, 10, 1, 97, ?, 10)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=90)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
                    notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), f"{TAG} S", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} S",)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
                    status, reference_code, manage_token, created_at, decided_at, total_price)
                    VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, ?, 900)""",
                 (sid, f"{TAG} Guest", email, f"{TAG}W", f"tokw{TAG}".lower(), _at(9), _at(8)))
    rid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}W",)).fetchone()["id"]
    m.add_workshop_transaction(conn, rid, "payment", "Deposit", 90.0, method="stripe")
    m.add_workshop_transaction(conn, rid, "charge", "A room of their own", 120.0)
    kinds = m.known_event_types(conn)
    conn.execute("""INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                    contact_name, contact_email, contact_phone, preferred_date, guest_count,
                    message, status, quoted_price, amount_paid, created_at)
                    VALUES (?, ?, ?, ?, ?, '', ?, 60, 'ZZ', 'confirmed', 3000, 0, ?)""",
                 (f"{TAG}V", f"tokv{TAG}".lower(), (kinds or ["wedding"])[0], f"{TAG} Guest",
                  email, (house_today() + timedelta(days=150)).isoformat(), _at(7)))
    eid = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}V",)).fetchone()["id"]
    m.record_event_payment(conn, eid, 1000.0, method="bank_transfer", reference="VIR 1")
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                    guest_email, dinner_date, party_size, status, payment_status, total_price,
                    deposit_amount, created_at, decided_at)
                    VALUES (?, ?, ?, ?, ?, 4, 'confirmed', 'paid', 240, 60, ?, ?)""",
                 (f"{TAG}R", f"tokr{TAG}".lower(), f"{TAG} Guest", email,
                  (house_today() + timedelta(days=3)).isoformat(), _at(4), _at(4)))
    conn.commit()
    conn.close()
    ev = _call(lambda c: c.execute("SELECT * FROM event_inquiries WHERE id = ?", (eid,)).fetchone())
    _call(m.make_refund, "event", ev, 200, "Overpaid", method="bank_transfer",
          reason_code="overpaid", bill_effect=m.BILL_STANDS)

    st = _call(m.guest_account_statement, gid)
    refs = {x["ref"] for x in st["lines"]}

    s.section("Every charge, payment and refund, dated, with a running balance")
    s.check("stays, the atelier, the event and the dinner deposit are all on it",
            {f"{TAG}A", f"{TAG}B", f"{TAG}W", f"{TAG}V", f"{TAG}R"} <= refs, detail=str(sorted(refs)))
    s.check("every line is dated", all(x["day"] for x in st["lines"]))
    s.check("in order", [x["at"] for x in st["lines"]] == sorted(x["at"] for x in st["lines"]))
    running, ok = 0.0, True
    for x in st["lines"]:
        running = round(running + x["charge"] - x["paid"] + x["back"], 2)
        ok = ok and abs(running - x["balance"]) < 0.01
    s.check("and the balance runs line by line", ok)
    a_lines = [x for x in st["lines"] if x["ref"] == f"{TAG}A"]
    s.check("a stay paid in two goes shows both payments, each dated and by how",
            [(x["paid"], x["what"]) for x in a_lines if x["paid"]]
            == [(100.0, "Paid, cash"), (300.0, "Paid, bank transfer")],
            detail=str([(x["paid"], x["what"]) for x in a_lines if x["paid"]]))
    s.check("and its goodwill refund is two lines, not a smaller total with no reason",
            any(x["back"] == 50.0 for x in a_lines)
            and any(x["charge"] == -50.0 and "Reduction" in x["what"] for x in a_lines))

    s.section("It adds up to every bill, booking by booking")
    by = {(b["category"], b["ref"]): b for b in st["bookings"]}
    bill_a = _call(m.booking_bill, paid_stay["id"])
    bill_b = _call(m.booking_bill, owing["id"])
    s.check("a stay paid in full comes to what its bill says it owes: nothing",
            by[("room", f"{TAG}A")]["net"] == round(bill_a["owed"] - bill_a["overpaid"], 2) == 0.0,
            detail=f"{by[('room', f'{TAG}A')]['net']} vs {bill_a['owed']}")
    s.check("a stay still owing comes to exactly what its bill still asks for",
            by[("room", f"{TAG}B")]["net"] == bill_b["owed"] == 300.0,
            detail=f"{by[('room', f'{TAG}B')]['net']} vs {bill_b['owed']}")
    due, _charged, _paid = _call(m.workshop_balance_due, rid)
    s.check("the atelier comes to what its ledger says, extra charge and all",
            by[("workshop", f"{TAG}W")]["net"] == due == 930.0,
            detail=f"{by[('workshop', f'{TAG}W')]['net']} vs {due}")
    eb = _call(m.event_bill, eid)
    s.check("the event comes to what its bill says, the refund put back on it",
            by[("event", f"{TAG}V")]["net"] == round(eb["owed"] - eb["overpaid"], 2) == 2200.0,
            detail=f"{by[('event', f'{TAG}V')]['net']} vs {eb['owed']}")
    s.check("a dinner deposit taken for a table that stands nets to nothing",
            by[("restaurant", f"{TAG}R")]["net"] == 0.0)
    s.check("and the whole is the sum of its bookings",
            abs(sum(b["net"] for b in st["bookings"]) - st["balance"]) < 0.01
            and abs(st["closing"] - st["balance"]) < 0.01,
            detail=f"{sum(b['net'] for b in st['bookings'])} vs {st['balance']}")

    s.section("What came to nothing, and what is still held")
    # Off the whole of it: not a dated line, and not a row of its own in the
    # booking-by-booking part either, where it would read as "settled, EUR 0".
    s.check("a request declined that moved no money is not on it",
            f"{TAG}D" not in refs and not any(b["ref"] == f"{TAG}D" for b in st["bookings"]),
            detail=str([b["ref"] for b in st["bookings"]]))
    s.check("a stay the house called off, its deposit still held, shows it as theirs",
            by[("room", f"{TAG}C")]["net"] == -80.0
            and not any(x["charge"] for x in st["lines"] if x["ref"] == f"{TAG}C"),
            detail=str(by.get(("room", f"{TAG}C"))))
    kept_lines = [x for x in st["lines"] if x["ref"] == f"{TAG}F"]
    s.check("while one they cancelled keeps its deposit, said as such, and owes nothing",
            by[("room", f"{TAG}F")]["net"] == 0.0
            and any(x["charge"] == 120.0 and "non-refundable" in x["what"] for x in kept_lines),
            detail=str([(x["what"], x["charge"], x["paid"]) for x in kept_lines]))
    s.check("a stay booked under the address from before a merge is theirs",
            f"{TAG}E" in refs)

    s.section("A period brings forward what came before it")
    cut = house_today() - timedelta(days=25)
    period = _call(m.guest_account_statement, gid, cut, house_today())
    before_cut = [x for x in st["lines"] if x["day"] < cut.isoformat()]
    s.check("the opening balance is everything before it",
            abs(period["opening"] - sum(x["charge"] - x["paid"] + x["back"] for x in before_cut)) < 0.01
            and period["opening"] != 0, detail=f"{period['opening']}")
    s.check("only the period's lines are shown", all(x["day"] >= cut.isoformat() for x in period["lines"])
            and len(period["lines"]) < len(st["lines"]))
    s.check("and it closes on the same figure as the whole", abs(period["closing"] - st["balance"]) < 0.01,
            detail=f"{period['closing']} vs {st['balance']}")

    s.section("The owner's statement")
    page = oc.get(f"/guests/{gid}/statement")
    text = visible_text(page.get_data(as_text=True))
    s.check("it opens, with the account and every booking", page.status_code == 200
            and "The account" in text and f"{TAG}B" in text and f"{TAG}V" in text, page)
    s.check("with what is outstanding in one figure", f"€{st['balance']:.2f}" in text,
            detail=f"€{st['balance']:.2f}")
    ranged = visible_text(oc.get(f"/guests/{gid}/statement?from={cut.isoformat()}").get_data(as_text=True))
    s.check("a period shows what it brought forward", "Brought forward" in ranged)
    csv_text = oc.get(f"/guests/{gid}/statement.csv").get_data(as_text=True)
    s.check("it exports, line for line", csv_text.count("\n") - 1 == len(st["lines"])
            and f"{TAG}W" in csv_text, detail=csv_text[:160])
    s.check("an employee cannot see it", ec.get(f"/guests/{gid}/statement").status_code in (302, 403))
    sent.clear()
    r = oc.post(f"/guests/{gid}/statement/email", data={"from": "", "to": ""}, follow_redirects=True)
    mine = [x for x in sent if x[0] == email]
    s.check("sending it writes to them once, with the lines and what is owed",
            len(mine) == 1 and f"{TAG}V" in mine[0][2] and f"€{st['balance']:.2f}" in mine[0][2],
            detail=str([x[1] for x in sent]) + str(flashes(r)))
    token = _call(lambda c: c.execute("SELECT portal_token FROM guests WHERE id = ?", (gid,)).fetchone()[0])
    s.check("with the link to their own copy", mine and f"/my/{token}/statement" in mine[0][2])

    s.section("The guest's own statement")
    mine_page = m.app.test_client().get(f"/my/{token}/statement")
    body = mine_page.get_data(as_text=True)
    s.check("it opens from the link they keep", mine_page.status_code == 200
            and f"{TAG}V" in body and "outstanding" in body, mine_page)
    s.check("and is not for search engines", 'name="robots" content="noindex' in body)
    s.check("it says where to pay what is still owed",
            f"/book/manage/{owing['manage_token']}" in body or owing["manage_token"] in body)
    # The site says workshop in English now, and so does what a guest keeps.
    # (The atelier in this suite is called "... Atelier" by its own title.)
    words = visible_text(body)
    s.check("and calls an atelier a workshop, as the site now does",
            "Pay for this workshop" in words and f"Workshop · {TAG}W" in words
            and "Pay for this atelier" not in words and "Atelier · " not in words,
            detail=words[:400])
    with m.app.test_request_context("/"):
        conn = db()
        letter, _card = m.room_confirmation_context(conn, paid_stay, "The Twin Room",
                                                    portal_url=f"/my/{token}")
        conn.close()
    s.check("and a stay's confirmation letter calls them workshops when it points there",
            "any workshops or dinners" in letter["stay_details"]
            and "atelier" not in letter["stay_details"].lower(),
            detail=letter["stay_details"][-240:])
    old_token = _call(lambda c: m.guest_portal_token(c, old_email))
    merged_page = m.app.test_client().get(f"/my/{old_token}/statement").get_data(as_text=True)
    s.check("a merged-away profile's link opens the one it joined", f"{TAG}B" in merged_page)
    s.check("an unknown link is a 404", m.app.test_client().get("/my/nope/statement").status_code == 404)
    portal = m.app.test_client().get(f"/my/{token}").get_data(as_text=True)
    s.check("and their account page leads to it", f"/my/{token}/statement" in portal)

    s.section("The letter is the owner's to word")
    row = _call(lambda c: c.execute(
        "SELECT 1 FROM email_templates WHERE template_key = 'guest_account_statement'").fetchone())
    s.check("it is a template", row is not None)
    s.check("that cannot be saved without the link",
            "statement_url" in m.REQUIRED_MERGE_TAGS.get("guest_account_statement", {}))
