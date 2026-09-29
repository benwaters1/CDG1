"""Every transaction: one page of every charge, payment and refund, with who it
was, what it was for, and how it was paid.

What there was: nothing. Money in lived in five places -- a stay's payments, an
atelier's ledger, an event's payments, a dinner's deposit, the refunds -- and
"what came in this week, and what for?" meant opening each.

What this holds:

  - Every payment and refund of the period is a row, dated on the house's day,
    with who, what it was for (the booking, linked), when that booking ends,
    how it was paid and by which card.
  - The rows are the statement's lines: a person's rows here are their
    statement's lines for the same days.
  - The totals are the shown rows added up; filtered to refunds they are the
    refunds, and the export is the view, totals and all.
  - A chip counts what clicking it gives; search finds a card by its last four.
  - The period is the period: last month's payment is under last month.
  - The house's day, not UTC's: half an hour past midnight here on the 1st is
    the new month, though in UTC it is still the old; the month ends where the
    next begins; a dinner's deposit is charged in the month it was confirmed.
  - Somebody with no profile is still named; an employee cannot see the page.
"""
import csv
import io
from datetime import timedelta

from _harness import Suite, db, house_today, visible_text, clients
import _harness

m = _harness.m
TAG = "ZZTX"
WHO = f"{TAG.lower()}@example.invalid"
EVENTS = f"{TAG.lower()}.couple@example.invalid"
CARD = f"pi_{TAG}card"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM refunds WHERE category = 'room' AND booking_id IN {stays}", like)
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        places = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {places}", like)
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                     "(SELECT id FROM workshops WHERE title LIKE ?)", like)
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", like)
        events = "(SELECT id FROM event_inquiries WHERE reference_code LIKE ?)"
        conn.execute(f"DELETE FROM event_payments WHERE event_id IN {events}", like)
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM payment_cards WHERE ref LIKE ?", (f"%{TAG}%",))
        conn.execute("DELETE FROM guests WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.commit()
    finally:
        conn.close()


def run():
    s = Suite("Every transaction")
    _cleanup()
    try:
        _run(s)
    finally:
        _cleanup()
    return s


def _mine(rows):
    return [x for x in rows if (x["ref"] or "").startswith(TAG)]


def _run(s):
    oc, ec, _owner, _emp = clients()
    today = house_today()
    now = _harness.datetime_now()
    at_now = m.datetime.now(m.timezone.utc)
    room = _harness.ensure_room()
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Guest", WHO, now))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?", (WHO,)).fetchone()["id"]

    # A stay: charged today, paid by card and by transfer, part refunded.
    arrive = today + timedelta(days=990)
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    amount_paid, created_at, linked_guest_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 0, 450, ?, ?)""",
                 (room["id"], f"{TAG}S", f"{TAG}stok".lower(), f"{TAG} Guest", WHO,
                  arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(), now, gid))
    stay = conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method,
                    stripe_payment_intent_id, created_at) VALUES (?, 300, 'stripe', ?, ?)""",
                 (stay["id"], CARD, now))
    card_payment = conn.execute("SELECT id FROM booking_payments WHERE stripe_payment_intent_id = ?",
                                (CARD,)).fetchone()["id"]
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                    VALUES (?, 150, 'bank_transfer', ?)""", (stay["id"], now))
    conn.execute("""INSERT INTO refunds (category, booking_id, reference_code, guest_name,
                    guest_email, amount, reason, method, payment_key, created_at)
                    VALUES ('room', ?, ?, ?, ?, 40, ?, 'stripe', ?, ?)""",
                 (stay["id"], f"{TAG}S", f"{TAG} Guest", WHO, f"{TAG} a night's noise",
                  f"bp{card_payment}", now))
    conn.execute("""INSERT INTO payment_cards (ref, brand, last4, wallet, fetched_at)
                    VALUES (?, 'visa', '4242', NULL, ?)""", (CARD, now))
    # An atelier: its deposit taken today, and a payment last month.
    conn.execute("INSERT INTO workshops (title, price_per_person, created_at) VALUES (?, 500, ?)",
                 (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = today + timedelta(days=1000)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, created_at)
                    VALUES (?, ?, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    # Inside last month whatever today is: the 6th of it, at noon.
    earlier_month = m.resolve_period("month", m.resolve_period("month", today.isoformat())["prev_anchor"])
    last_month = f"{(earlier_month['start'] + timedelta(days=5)).isoformat()}T12:00:00+00:00"
    conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                    guest_name, guest_email, party_size, status, total_price, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 500, ?)""",
                 (sid, f"{TAG}W", f"{TAG}wtok".lower(), f"{TAG} Guest", WHO, last_month))
    wb = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?", (f"{TAG}W",)).fetchone()
    conn.execute("""INSERT INTO workshop_transactions (workshop_booking_id, kind, description,
                    amount, method, created_at) VALUES (?, 'payment', 'Deposit', 120, 'bank_transfer', ?)""",
                 (wb["id"], last_month))
    conn.execute("""INSERT INTO workshop_transactions (workshop_booking_id, kind, description,
                    amount, method, created_at) VALUES (?, 'payment', 'Balance', 380, 'cash', ?)""",
                 (wb["id"], now))
    # An event paid by somebody with no profile at all.
    conn.execute("""INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                    contact_name, contact_email, contact_phone, preferred_date, guest_count,
                    message, status, quoted_price, amount_paid, created_at)
                    VALUES (?, ?, 'wedding', ?, ?, '', ?, 60, 'ZZ test', 'confirmed', 6000, 1000, ?)""",
                 (f"{TAG}E", f"{TAG}etok".lower(), f"{TAG} Couple", EVENTS,
                  (today + timedelta(days=1010)).isoformat(), now))
    ev = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?", (f"{TAG}E",)).fetchone()
    conn.execute("""INSERT INTO event_payments (event_id, amount, method, reference, created_at)
                    VALUES (?, 1000, 'bank_transfer', ?, ?)""", (ev["id"], f"{TAG}-transfer", now))
    conn.commit()
    this_month = m.resolve_period("month", today.isoformat())
    rows = m.house_transactions(conn, this_month["start_iso"], this_month["end_iso"])
    # The statement's "to" is the last day; the period's end is the day after.
    account = m.guest_account_statement(conn, gid, this_month["start"],
                                        this_month["end"] - timedelta(days=1))
    conn.close()
    mine = _mine(rows)

    s.section("Every payment and refund of the period is a row")
    paid = sorted((x["ref"], x["paid"]) for x in mine if x["paid"])
    s.check("the card, the transfer, the atelier's balance and the event's payment are all here",
            paid == [(f"{TAG}E", 1000.0), (f"{TAG}S", 150.0), (f"{TAG}S", 300.0), (f"{TAG}W", 380.0)],
            detail=str(paid))
    s.check("and the refund, a row of its own",
            [(x["ref"], x["back"]) for x in mine if x["back"]] == [(f"{TAG}S", 40.0)],
            detail=str([(x["ref"], x["back"]) for x in mine if x["back"]]))
    card_row = next((x for x in mine if x["paid"] == 300.0), None)
    s.check("with who it was, what it was for, when that ends, and how it was paid",
            card_row is not None and card_row["person"] == f"{TAG} Guest"
            and card_row["guest_id"] == gid and card_row["kind"] == "Stay"
            and room["name"] in card_row["booking_what"]
            and card_row["ends"] == (arrive + timedelta(days=2)).isoformat()
            and card_row["method"] == "Card, online" and card_row["card_words"] == "Visa ····4242"
            and card_row["day"] == today.isoformat(),
            detail=str({k: card_row[k] for k in ("person", "kind", "booking_what", "ends", "method",
                                                 "card_words", "day")}) if card_row else "no row")
    page = oc.get("/admin/transactions").get_data(as_text=True)
    text = visible_text(page)
    s.check("the page shows it, the booking linked to its payments",
            "Visa ····4242" in text and f"{TAG} a night's noise" in text
            and f"/admin/refunds/room/{stay['id']}" in page
            and f"/guests/{gid}" in page, detail=text[:300])
    couple = next((x for x in mine if x["ref"] == f"{TAG}E"), None)
    s.check("somebody with no profile is named all the same, without a link",
            couple is not None and couple["person"] == f"{TAG} Couple" and couple["guest_id"] is None,
            detail=str(couple and {k: couple[k] for k in ("person", "guest_id")}))

    s.section("The rows are the statement's lines")
    shape = lambda xs: sorted((x["ref"], x["what"], x["charge"], x["paid"], x["back"]) for x in xs)
    theirs = [x for x in mine if x["guest_id"] == gid]
    s.check("a person's rows here are their statement's lines for the same days",
            shape(theirs) == shape(account["lines"]),
            detail=f"{shape(theirs)} vs {shape(account['lines'])}")

    s.section("The totals are the rows added up")
    only = oc.get(f"/admin/transactions?q={TAG}").get_data(as_text=True)
    conn = db()
    lv = m.transactions_list_view(conn, {"q": TAG})
    refunds_only = m.transactions_list_view(conn, {"q": TAG, "type": "Refund"})
    conn.close()
    add = lambda xs, k: round(sum(x[k] for x in xs), 2)
    s.check("paid in, refunded and what was charged are the shown rows' sums",
            lv["totals"]["paid"] == add(lv["rows"], "paid") == 1830.0
            and lv["totals"]["refunded"] == add(lv["rows"], "back") == 40.0
            and lv["totals"]["charged"] == add(lv["rows"], "charge")
            and lv["totals"]["net"] == 1790.0,
            detail=str(lv["totals"]))
    s.check("and the page says so", "€1,830.00" in visible_text(only) and "€1,790.00" in visible_text(only))
    s.check("filtered to refunds, only refunds, and the totals follow",
            [x["group"] for x in refunds_only["rows"]] == ["Refund"]
            and refunds_only["totals"]["paid"] == 0 and refunds_only["totals"]["refunded"] == 40.0,
            detail=str(refunds_only["totals"]))
    chip = next((c for f in lv["facets"] if f["key"] == "type" for c in f["options"]
                 if c["value"] == "Refund"), None)
    s.check("the refunds chip counts what clicking it gives",
            chip is not None and chip["count"] == len(refunds_only["rows"]),
            detail=str(chip))
    conn = db()
    by_card = m.transactions_list_view(conn, {"q": "4242"})
    conn.close()
    found = _mine(by_card["rows"])
    s.check("search finds a payment by its card's last four -- and the refund that went back to it",
            sorted((x["paid"], x["back"]) for x in found) == [(0.0, 40.0), (300.0, 0.0)]
            and all(x["card_words"] == "Visa ····4242" for x in found),
            detail=str([(x["ref"], x["paid"], x["back"], x["card_words"]) for x in found]))
    exported = oc.get(f"/admin/transactions/export.csv?q={TAG}&type=Payment").get_data(as_text=True)
    lines = list(csv.DictReader(io.StringIO(exported)))
    s.check("the export is the view: the same rows, the same sums",
            len(lines) == 4 and round(sum(float(r["paid"] or 0) for r in lines), 2) == 1830.0
            and {r["for"] for r in lines} == {"Stay", "Workshop", "Event"},
            detail=f"{len(lines)} rows: {[r['reference'] for r in lines]}")

    s.section("The period is the period")
    s.check("last month's payment is not in this month",
            not any(x["ref"] == f"{TAG}W" and x["paid"] == 120.0 for x in mine))
    before = m.resolve_period("month", this_month["prev_anchor"])
    conn = db()
    earlier = _mine(m.house_transactions(conn, before["start_iso"], before["end_iso"]))
    conn.close()
    s.check("it is under last month, with the atelier's charge of that day",
            any(x["ref"] == f"{TAG}W" and x["paid"] == 120.0 for x in earlier)
            and any(x["ref"] == f"{TAG}W" and x["charge"] == 500.0 for x in earlier),
            detail=str([(x["ref"], x["charge"], x["paid"]) for x in earlier]))

    s.section("The house's day, not UTC's")
    # Midnight here as this month begins -- in UTC, still an evening of last month.
    turn = m.datetime.fromisoformat(m.house_moment(this_month["start"]))
    late = (turn - timedelta(minutes=30)).isoformat()
    early = (turn + timedelta(minutes=30)).isoformat()
    next_month = (m.datetime.fromisoformat(m.house_moment(this_month["end"]))
                  + timedelta(hours=12)).isoformat()
    last_day = (this_month["start"] - timedelta(days=1)).isoformat()
    conn = db()
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    amount_paid, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 0, 90, ?)""",
                 (room["id"], f"{TAG}N", f"{TAG}ntok".lower(), f"{TAG} Night",
                  f"{TAG.lower()}.night@example.invalid", (arrive + timedelta(days=5)).isoformat(),
                  (arrive + timedelta(days=6)).isoformat(), late))
    night = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}N",)).fetchone()["id"]
    for amount, at in ((20, late), (30, early), (40, next_month)):
        conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                        VALUES (?, ?, 'cash', ?)""", (night, amount, at))
    # A table: its deposit paid on the 6th of last month, the booking confirmed today.
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                    guest_email, party_size, dinner_date, status, payment_status, deposit_amount,
                    created_at, decided_at) VALUES (?, ?, ?, ?, 2, ?, 'confirmed', 'paid', 60, ?, ?)""",
                 (f"{TAG}R", f"{TAG}rtok".lower(), f"{TAG} Diner", f"{TAG.lower()}.diner@example.invalid",
                  (today + timedelta(days=995)).isoformat(), last_month, now))
    conn.commit()
    this_time = _mine(m.house_transactions(conn, this_month["start_iso"], this_month["end_iso"]))
    last_time = _mine(m.house_transactions(conn, before["start_iso"], before["end_iso"]))
    conn.close()
    paid_in = lambda xs: sorted((x["paid"], x["day"]) for x in xs if x["ref"] == f"{TAG}N" and x["paid"])
    s.check("half an hour past midnight here on the 1st is this month, dated the 1st, "
            "though in UTC it is still last month",
            (30.0, this_month["start_iso"]) in paid_in(this_time)
            and not any(p == 30.0 for p, _d in paid_in(last_time)),
            detail=f"this month {paid_in(this_time)}, last month {paid_in(last_time)}")
    s.check("and half an hour before it is last month, dated its last day",
            (20.0, last_day) in paid_in(last_time)
            and not any(p == 20.0 for p, _d in paid_in(this_time)),
            detail=f"this month {paid_in(this_time)}, last month {paid_in(last_time)}")
    s.check("the month ends where the next begins: the 1st of next month is not in it",
            not any(p == 40.0 for p, _d in paid_in(this_time)),
            detail=str(paid_in(this_time)))
    table = lambda xs: [(x["line"], x["charge"], x["paid"]) for x in xs if x["ref"] == f"{TAG}R"]
    s.check("a table's deposit is charged in the month it was confirmed, and paid in the month it was paid",
            table(this_time) == [("deposit", 60.0, 0.0)] and table(last_time) == [("payment", 0.0, 60.0)],
            detail=f"this month {table(this_time)}, last month {table(last_time)}")

    s.section("Who may see it")
    s.check("an employee cannot", ec.get("/admin/transactions").status_code in (302, 403))
    s.check("nor take the export", ec.get("/admin/transactions/export.csv").status_code in (302, 403))
