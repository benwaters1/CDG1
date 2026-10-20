"""A statement says each thing once, and says where the house is.

TWO THINGS SEEN ON ONE STATEMENT (GUD-1AG30U, 19 Oct 2026). The sums were
right; it read wrong.

  THE DISCOUNT SHOWED TWICE. A "Discount -€30.00" line among the charges, then
  "Subtotal" and "Discounts and reductions -€30.00" under them, so a guest
  could think €60 had come off. The brief allowed two honest shapes: the lines
  straight to the Total, or a Subtotal that is the sum of the lines above it.
  The letter redesign of 19 Oct (ca5ebb6) took the second: what was added, a
  Subtotal, each reduction once by name, the Total. This holds whichever shape
  is drawn: the discount appears ONCE, and every run of rows adds up to the
  figure beneath it. Checked on the letter (both halves), the statement pages,
  the stay's own statement letter, and every kind: stay, atelier, event, dinner.

  THE ADDRESS READ "Château de Gudanes, Gudanes, 09310 Château-Verdun,
  France": the name twice and no street. The street is the owner's to put in
  the company's record (it is data, not code). What the code owes: the name
  once however the address was typed, the house's own address in the foot of
  every letter, and one helper that every document's block goes through.

Nothing is sent: the stay's letter is caught at write_about_stay.
"""
import re
from datetime import timedelta

from _harness import Suite, db, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZRO"
WHO = f"{TAG.lower()}@example.invalid"
OFF = 37.5                       # an odd figure, so counting it means something
OFF_WORDS = "37.50"
LIVE_ADDRESS = "Château de Gudanes, Gudanes, 09310 Château-Verdun, France"
GOOD_ADDRESS = "2 Route de Beille, 09310 Château-Verdun, France"
GOOD_LINE = "Château de Gudanes · 2 Route de Beille · 09310 Château-Verdun · France"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                     "(SELECT id FROM bookings WHERE reference_code LIKE ?)", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                     "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", like)
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                     "(SELECT id FROM workshops WHERE title LIKE ?)", like)
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", like)
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", like)
        conn.commit()
    finally:
        conn.close()


def _text_adds_up(text):
    """Whether the statement half of a plain letter adds up, down to its Total:
    the rows above a Subtotal come to it, and the Subtotal and the rows under
    it come to the Total. Money is the last figure on each line. Returns
    (adds up?, what was read)."""
    running, seen, started = 0.0, [], False
    for line in text.splitlines():
        if line.startswith("Statement"):
            started = True
            continue
        if not started:
            continue
        found = re.search(r"(-?)€([\d,]+\.\d\d)\s*$", line)
        if not found:
            continue
        amount = float(found.group(2).replace(",", "")) * (-1 if found.group(1) else 1)
        word = line.strip().split("  ")[0]
        seen.append((word, amount))
        if word == "Subtotal":
            if abs(running - amount) > 0.005:
                return False, seen
            running = amount
        elif word == "Total":
            return bool(len(seen) > 1) and abs(running - amount) < 0.005, seen
        else:
            running += amount
    return False, seen


def _page_adds_up(html):
    """Whether the charges table on a statement page adds up: its body comes to
    the first figure in its foot (a Subtotal, or the Total itself), and the
    Subtotal and the reductions under it come to the Total. Returns (adds up?,
    body amounts, foot amounts)."""
    table = re.search(r'<table class="data-table statement-lines".*?</table>', html, re.S)
    if not table:
        return False, [], []
    body = re.search(r"<tbody>(.*?)</tbody>", table.group(0), re.S)
    foot = re.search(r"<tfoot>(.*?)</tfoot>", table.group(0), re.S)

    def amounts(chunk):
        return [float(x.replace(",", "")) * (-1 if sign else 1)
                for sign, x in re.findall(r"(−?)€([\d,]+\.\d\d)", chunk or "")]
    rows, under = amounts(body.group(1) if body else ""), amounts(foot.group(1) if foot else "")
    ok = (bool(rows) and bool(under) and abs(sum(rows) - under[0]) < 0.005
          and abs(sum(under[:-1]) - under[-1]) < 0.005 if len(under) > 1 else
          bool(rows) and bool(under) and abs(sum(rows) - under[0]) < 0.005)
    return ok, rows, under


def run():
    s = Suite("A statement says each thing once")
    _cleanup()
    conn = db()
    put_back = _harness.keep_rows(conn, "companies", "1 = 1")
    try:
        _run(s)
    finally:
        put_back()
        conn.close()
        _cleanup()
    return s


def _letter_says_once(s, what, conn, category, bid):
    with m.app.test_request_context("/"):
        body, html = m.letter_with_statement(conn, "Your statement", "Hello.", category, bid)
    shown = visible_text(html)
    s.check(f"{what}: the plain letter shows the discount once",
            body.count(OFF_WORDS) == 1, detail=body)
    s.check(f"{what}: and so does the drawn letter",
            shown.count(OFF_WORDS) == 1, detail=shown[-900:])
    s.check(f"{what}: no second discount figure under the lines, in either half",
            all("Discounts and reductions" not in x for x in (body, shown)))
    ok, seen = _text_adds_up(body)
    s.check(f"{what}: every run of rows adds up to the figure beneath it", ok,
            detail=str(seen))
    return body


def _run(s):
    oc, _ec, _owner, _emp = _harness.clients()
    anon = m.app.test_client()
    today = house_today()
    now = _harness.datetime_now()
    room = _harness.ensure_room()

    conn = db()
    conn.execute("""INSERT INTO promo_codes (code, discount_type, discount_value, created_at)
                    VALUES (?, 'fixed', ?, ?)""", (f"{TAG}MERCI", OFF, now))
    promo = conn.execute("SELECT id FROM promo_codes WHERE code = ?",
                         (f"{TAG}MERCI",)).fetchone()["id"]

    # ---- a stay with a code off it, paid in part ------------------------------
    arrive = today + timedelta(days=961)       # far out: no real stay on these dates
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status, total_price,
               promo_code_id, discount_amount, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 0, ?, ?, ?)""",
        (room["id"], f"{TAG}S", f"{TAG}stok".lower(), f"{TAG} Guest", WHO,
         arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(), promo, OFF, now))
    stay = conn.execute("SELECT bookings.*, rooms.name AS room_name FROM bookings "
                        "JOIN rooms ON rooms.id = bookings.room_id "
                        "WHERE reference_code = ?", (f"{TAG}S",)).fetchone()
    bill = m.booking_bill(conn, stay["id"])
    room_line = next((l["amount"] for l in bill["lines"] if l["kind"] == "room"), 0)
    conn.execute("UPDATE bookings SET total_price = ? WHERE id = ?",
                 (round(room_line - OFF, 2), stay["id"]))
    conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                    VALUES (?, 100, 'bank_transfer', ?)""", (stay["id"], now))
    conn.execute("UPDATE bookings SET amount_paid = 100 WHERE id = ?", (stay["id"],))
    conn.commit()
    stay = conn.execute("SELECT bookings.*, rooms.name AS room_name FROM bookings "
                        "JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ?",
                        (stay["id"],)).fetchone()
    view = m.booking_statement_view(conn, "room", stay["id"])

    s.section("A stay")
    s.check("there is a stay with a discount to write a statement for",
            room_line > 0 and view is not None
            and [x["charge"] for x in view["charges"] if x["line"] == "discount"] == [-OFF],
            detail=str(view and [(x["what"], x["charge"]) for x in view["charges"]]))
    s.check("the statement's total is the sum of its lines, and is the stay's bill",
            abs(sum(x["charge"] for x in view["charges"]) - view["total"]) < 0.005
            and abs(view["total"] - m.booking_bill(conn, stay["id"])["total"]) < 0.005,
            detail=f"{[x['charge'] for x in view['charges']]} -> {view['total']}")
    _letter_says_once(s, "the stay's receipt", conn, "room", stay["id"])

    page = anon.get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True)
    text = visible_text(page)
    ok, rows, under = _page_adds_up(page)
    s.check("the statement page shows the discount once", text.count(OFF_WORDS) == 1,
            detail=text[:1500])
    s.check("with no second discount figure", "Discounts and reductions" not in text)
    s.check("and every run of rows adds up to the figure beneath it", ok,
            detail=f"{rows} / {under}")

    caught = []
    was = m.write_about_stay
    m.write_about_stay = lambda booking, subject, body, **k: (caught.append(
        (subject, body, k.get("html") or "")), True)[1]
    try:
        with m.app.test_request_context("/"):
            m.send_room_statement(conn, stay)
    finally:
        m.write_about_stay = was
    body = caught[0][1] if caught else ""
    s.check("the stay's own statement letter shows the discount once",
            body.count(OFF_WORDS) == 1 and "Discounts and reductions" not in body,
            detail=body)
    ok, seen = _text_adds_up(body)
    s.check("and every run of rows adds up to the figure beneath it", ok, detail=str(seen))

    # ---- an atelier with the same code ---------------------------------------
    conn.execute("INSERT INTO workshops (title, price_per_person, created_at) VALUES (?, 900, ?)",
                 (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = today + timedelta(days=975)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, created_at)
                    VALUES (?, ?, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                    guest_name, guest_email, party_size, status, total_price, created_at,
                    promo_code_id, discount_amount, single_supplement)
                    VALUES (?, ?, ?, ?, ?, 1, 'confirmed', ?, ?, ?, ?, 0)""",
                 (sid, f"{TAG}W", f"{TAG}wtok".lower(), f"{TAG} Atelier Guest", WHO,
                  900 - OFF, now, promo, OFF))
    wb = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                      (f"{TAG}W",)).fetchone()
    conn.commit()

    s.section("An atelier")
    _letter_says_once(s, "the atelier's letter", conn, "workshop", wb["id"])
    page = anon.get(f"/workshops/manage/{wb['manage_token']}/statement").get_data(as_text=True)
    text = visible_text(page)
    ok, rows, under = _page_adds_up(page)
    s.check("the atelier's statement page shows the discount once",
            text.count(OFF_WORDS) == 1 and "Discounts and reductions" not in text,
            detail=text[:1500])
    s.check("and every run of rows adds up to the figure beneath it", ok,
            detail=f"{rows} / {under}")

    # ---- an event with a discount on its quote -------------------------------
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, guest_count, status, quoted_price,
               discount_amount, promo_code_id, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, 40, 'confirmed', 4000, ?, ?, ?)""",
        (f"{TAG}E", f"{TAG}etok".lower(), f"{TAG} Couple", WHO,
         (today + timedelta(days=990)).isoformat(), OFF, promo, now))
    eid = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}E",)).fetchone()["id"]
    conn.commit()

    s.section("An event")
    _letter_says_once(s, "the event's letter", conn, "event", eid)

    # ---- a dinner whose deposit was taken ------------------------------------
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
               dinner_date, party_size, status, total_price, payment_status, deposit_amount,
               created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'confirmed', 0, 'paid', 40, ?)""",
        (f"{TAG}R", f"{TAG}rtok".lower(), f"{TAG} Diner", WHO,
         (today + timedelta(days=995)).isoformat(), now))
    rid = conn.execute("SELECT id FROM restaurant_bookings WHERE reference_code = ?",
                       (f"{TAG}R",)).fetchone()["id"]
    conn.commit()
    s.section("A dinner")
    with m.app.test_request_context("/"):
        body, html = m.letter_with_statement(conn, "Your deposit", "Hello.", "restaurant", rid)
    ok, seen = _text_adds_up(body)
    s.check("a dinner's letter adds up to its total", ok, detail=body)

    # ---- the address ---------------------------------------------------------
    s.section("The name once, and the street, wherever the company is printed")
    company = conn.execute("SELECT id FROM companies WHERE issues_guest_documents = 1 "
                           "AND active = 1").fetchone()
    if company is None:
        conn.execute("""INSERT INTO companies (legal_name, country, issues_guest_documents,
                        is_employer, active, created_at, updated_at)
                        VALUES ('Château de Gudanes', 'FR', 1, 0, 1, ?, ?)""", (now, now))
        company = conn.execute("SELECT id FROM companies WHERE issues_guest_documents = 1").fetchone()
    # The record as it stood on 19 Oct 2026.
    conn.execute("UPDATE companies SET legal_name = 'Château de Gudanes', registered_address = ? "
                 "WHERE id = ?", (LIVE_ADDRESS, company["id"]))
    conn.commit()
    block = m.company_block_text(conn)
    s.check("typed with the name in it, the address does not say the name again",
            block.count("Château de Gudanes") == 1
            and block.splitlines()[0] == "Château de Gudanes · Gudanes · 09310 Château-Verdun · France",
            detail=block)
    page = anon.get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True)
    # The block in the corner that says who issues the bill: the name, then
    # the address under it.
    issuer = re.search(r'<div class="g-hint">(.*?)</div>', page, re.S)
    issuer = visible_text(issuer.group(1)) if issuer else ""
    s.check("nor does the statement page", issuer.count("Château de Gudanes") == 1
            and "Gudanes · 09310 Château-Verdun" in issuer, detail=issuer)
    footer = m.campaign_unsubscribe_footer(conn, "zz-token")
    s.check("nor the foot of an offer", footer.count("Château de Gudanes") == 1, detail=footer)

    conn.execute("UPDATE companies SET registered_address = ? WHERE id = ?",
                 (GOOD_ADDRESS, company["id"]))
    conn.commit()
    block = m.company_block_text(conn)
    s.check("with the street put in the record, every block reads as the house wants",
            block.splitlines()[0] == GOOD_LINE and "2 Route de Beille" in block
            and block.count("Château de Gudanes") == 1, detail=block)
    caught.clear()
    m.write_about_stay = lambda booking, subject, body, **k: (caught.append(
        (subject, body, k.get("html") or "")), True)[1]
    try:
        with m.app.test_request_context("/"):
            m.send_room_statement(conn, stay)
    finally:
        m.write_about_stay = was
    body = caught[0][1] if caught else ""
    s.check("the stay's statement letter carries that line, the name once",
            GOOD_LINE in body and body.count("Château de Gudanes ·") == 1, detail=body[-600:])
    s.check("the one helper gives that line from the record",
            m.company_address_line(m.operating_company(conn)) == GOOD_LINE)

    s.section("The foot of every letter is the house's own address")
    with m.app.test_request_context("/"):
        letter = m.letter_html("Hello", "Just to say hello.")
    s.check("it names the street", "2 Route de Beille" in letter
            and m.HOUSE_POSTAL.startswith("2 Route de Beille"), detail=letter[-1500:])
    s.check("and the old line without one is gone",
            "Château de Gudanes, 09310 Château-Verdun" not in letter)
    conn.close()
