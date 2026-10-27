# -*- coding: utf-8 -*-
"""The bill and the payments, in a hotel system's words (owner, 27 October 2026).

The owner's wording review, items 4 and 5, approved for this change:

  4. The money on a booking reads as separate lines -- Total, Paid, Refunded
     (only when something has been refunded), Balance due -- on the booking
     page AND on the refund page, where "Received, after refunds" made every
     guest look as though they had had money back. "Email the statement"
     becomes "Email the bill to the guest", and money "going back" is a refund.
  5. A payment says what it was. Tess Hoffman's EUR 64.50 (GUD-JWPAWW) was a
     card payment taken through RetreatGuru, recorded by hand with its Stripe
     charge id as the reference, and every page called it "Other". It is
     "Card (Stripe, via RetreatGuru)". A card payment online is "Card (Stripe)".

Wording only: nothing here changes what any figure is, only what it is called.
The one figure that is new on a page is Paid on the refund page, which is what
came in before refunds -- the line the owner asked for -- and is checked to be
exactly received-after-refunds plus refunded, so it adds up.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZBW"


def _cleanup():
    conn = db()
    stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, total, payments=(), refunded=0.0, email=True):
    """A confirmed one-night stay, with payments as (amount, method, reference)."""
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=50)
    departure = arrival + timedelta(days=1)
    now = datetime.now(timezone.utc).isoformat()
    code = f"{TAG}{ref}"
    paid = round(sum(p[0] for p in payments), 2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, payment_status, total_price,
           amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'confirmed', 'unpaid', ?, ?, ?)""",
        (room, code, f"tok{code}".lower(), f"{TAG} Guest {ref}",
         f"zzbw{ref.lower()}@example.invalid" if email else "",
         arrival.isoformat(), departure.isoformat(), total, paid, now))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (code,)).fetchone()["id"]
    m.stamp_room_total(conn, bid, total, arrival.isoformat(), departure.isoformat())
    for amount, method, reference in payments:
        conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, reference,
                        created_at) VALUES (?, ?, ?, ?, ?)""",
                     (bid, amount, method, reference, now))
    if refunded:
        conn.execute(
            """INSERT INTO refunds (category, booking_id, reference_code, guest_name,
               guest_email, amount, reason, method, created_at)
               VALUES ('room', ?, ?, ?, '', ?, 'Goodwill', 'other', ?)""",
            (bid, code, f"{TAG} Guest {ref}", refunded, now))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _tile_labels(page):
    tiles = page[page.find('<div class="stat-tiles">'):]
    tiles = tiles[:tiles.find('<h2')]
    return re.findall(r'stat-tile-label">([^<]+)<', tiles), tiles


def run():
    s = Suite("The bill and the payments, in a hotel system's words")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    try:
        _checks(s, oc)
    finally:
        _cleanup()
    return s


def _checks(s, oc):
    s.section("A payment says what it was")
    words = m.payment_method_words
    for args, want, why in (
            (("other", "ch_3QzzbwTESS", None), "Card (Stripe, via RetreatGuru)",
             "a Stripe charge recorded by hand, as Tess's was"),
            (("other", "pi_3Qzzbw", None), "Card (Stripe)",
             "a PaymentIntent recorded by hand"),
            (("other", "Cheque 1234", None), "Other", "something genuinely other"),
            (("other", None, None), "Other", "other with nothing to go on"),
            (("stripe", "cs_test_zzbw", None), "Card (Stripe)", "a card payment online"),
            (("stripe", "ch_3Qzzbw", "Paid through RetreatGuru"),
             "Card (Stripe, via RetreatGuru)", "an atelier payment brought across"),
            (("other", None, "Paid in RetreatGuru (no card payment found in Stripe)"),
             "Via RetreatGuru", "paid in RetreatGuru, no card found"),
            (("card_in_person", None, None), "Card (in person)", "a card taken in person"),
            (("bank_transfer", "VIR 22", None), "Bank transfer", "a transfer"),
            (("cash", None, None), "Cash", "cash")):
        got = words(*args)
        s.check(f"{why}: {want}", got == want, detail=f"{args} -> {got}")
    s.check("the record-a-payment choices are standard and none is vague",
            list(m.MANUAL_PAYMENT_METHODS.values())
            == ["Cash", "Bank transfer", "Card (in person)", "Other"],
            detail=f"{list(m.MANUAL_PAYMENT_METHODS.values())}")
    # The transactions page files each payment under a chip by looking its
    # WORDS up, so a label renamed in one place and not the other sends every
    # card payment to Other without a thing going red.
    labels = set(m.PAYMENT_METHOD_WORDS.values()) | {"Card (Stripe, via RetreatGuru)"}
    unfiled = sorted(w for w in labels if w.startswith("Card")
                     and m.TRANSACTION_METHODS.get(w) != "Card")
    s.check("every card label is filed under Card on the transactions page", not unfiled,
            detail=f"{unfiled}")
    s.check("and cash, transfer and voucher under their own",
            all(m.TRANSACTION_METHODS.get(w) == w for w in ("Cash", "Bank transfer", "Gift voucher")))
    page = oc.get("/admin/bookings/walk-in").get_data(as_text=True)
    sel = page[page.find('id="wi_method"'):]
    sel = sel[:sel.find("</select>")]
    s.check("the walk-in page offers the same four choices",
            sorted(re.findall(r">([^<]+)</option>", sel))
            == sorted(m.MANUAL_PAYMENT_METHODS.values()) and 'aria-label="Payment method"' in sel,
            detail=f"{re.findall(r'>([^<]+)</option>', sel)}")

    # Tess, as she was recorded on 15 October, a stay owing money, and a stay
    # with a refund on it.
    tess = _stay("TESS", total=215.0, payments=[(64.50, "other", "ch_3QzzbwTESS")])
    owing = _stay("OWE", total=300.0, payments=[(100.0, "stripe", "cs_test_zzbwOWE")])
    settled = _stay("SET", total=200.0, payments=[(150.0, "other", "pi_3QzzbwSET"),
                                                   (50.0, "card_in_person", None)])
    refunded = _stay("REF", total=200.0, payments=[(200.0, "bank_transfer", "VIR 9")],
                     refunded=40.0)

    s.section("The booking page")
    page = oc.get(f"/admin/bookings/{tess['id']}").get_data(as_text=True)
    text = _harness.visible_text(page)
    s.check("Tess's EUR 64.50 says it was a card, through Stripe, via RetreatGuru",
            "Card (Stripe, via RetreatGuru)" in text and "ch_3QzzbwTESS" in text,
            detail=re.findall(r"Card \([^)]*\)|Other", text)[:4])
    came_in = page[page.find("<h3>What came in</h3>"):]
    came_in = came_in[:came_in.find("</table>")]
    s.check("the payments table: Payment method, Paid, Refunded",
            re.findall(r'<th scope="col"[^>]*>([^<]+)</th>', came_in)
            == ["When", "Payment method", "Paid", "Refunded"],
            detail=f"{re.findall(r'<th scope=.col.[^>]*>([^<]+)</th>', came_in)}")
    s.check("no 'Other' for a card payment", ">Other<" not in came_in)
    # "Something else" stays among the refund REASONS, where a note goes with
    # it; it is the payment method that must not be vague (checked below).
    for gone in ("Paid, after refunds", "Still owed", "Gone back", "go back", "Giving money back",
                 "card, through Stripe", "Email the statement"):
        s.check(f"no '{gone}'", gone not in text)
    page = oc.get(f"/admin/bookings/{settled['id']}").get_data(as_text=True)
    text = _harness.visible_text(page)
    s.check("a settled stay: 'Email the bill to the guest'", "Email the bill to the guest" in text)
    s.check("both its payments named", "Card (Stripe)" in text and "Card (in person)" in text)
    page = oc.get(f"/admin/bookings/{owing['id']}").get_data(as_text=True)
    form = page[page.find('id="take-payment"'):]
    form = form[:form.find("</form>")]
    s.check("record a payment offers Other, not Something else",
            ">Other<" in form and "Something else" not in form and "Card (in person)" in form)
    s.check("its method box is called Payment method", 'aria-label="Payment method"' in form)
    s.check("a stay owing money still offers the bill with a Pay now link",
            "Email the bill with a Pay now link" in _harness.visible_text(page))

    s.section("The refund page: Total, Paid, Refunded only when there is one, Balance due")
    page = oc.get(f"/admin/refunds/room/{owing['id']}").get_data(as_text=True)
    labels, tiles = _tile_labels(page)
    s.check("nothing refunded: Total, Paid, Balance due", labels == ["Total", "Paid", "Balance due"],
            detail=f"{labels}")
    page = oc.get(f"/admin/refunds/room/{refunded['id']}").get_data(as_text=True)
    labels, tiles = _tile_labels(page)
    s.check("something refunded: Total, Paid, Refunded, then what is left",
            labels[:3] == ["Total", "Paid", "Refunded"], detail=f"{labels}")
    values = re.findall(r'stat-tile-value">€([\d,.]+)<', tiles)
    s.check("Paid is what came in, EUR 200, not what is left after the refund",
            values[1:3] == ["200.00", "40.00"], detail=f"{values}")
    text = _harness.visible_text(page)
    for gone in ("Received, after refunds", "Still owed", "Gone back", "Can go back",
                 "can go back", "give back", "What it costs"):
        s.check(f"no '{gone}'", gone not in text)
    s.check("the payments table names its columns plainly",
            all(w in text for w in ("Payment method", "Refunded", "Refundable")))
    with m.app.test_request_context("/"):
        conn = db()
        for b in (tess, owing, settled, refunded):
            desk = m.refund_desk_context(conn, "room", b)
            money = desk["money"]
            s.check(f"{b['reference_code']}: Paid is received after refunds plus refunded",
                    abs(money["paid"] - money["received"] - money["refunded"]) < 0.005,
                    detail=f"{money}")
        conn.close()

    s.section("The statement says it the same way, capitals kept")
    conn = db()
    view = m.booking_statement_view(conn, "room", tess["id"])
    conn.close()
    line = view["payments"][0] if view and view["payments"] else {}
    s.check("Tess's statement: 'Paid, card (Stripe, via RetreatGuru)'",
            line.get("what") == "Paid, card (Stripe, via RetreatGuru)", detail=f"{line.get('what')}")
    s.check("and the method column says the same",
            line.get("method") == "Card (Stripe, via RetreatGuru)", detail=f"{line.get('method')}")

    s.section("The transactions page files each payment where it belongs")
    conn = db()
    today = house_today()
    rows = m.house_transactions(conn, (today - timedelta(days=1)).isoformat(),
                                (today + timedelta(days=2)).isoformat())
    conn.close()
    by_person = {}
    for r in rows:
        if r.get("line") == "payment" and TAG in (r.get("person") or ""):
            by_person.setdefault(r["person"].rsplit(" ", 1)[-1], set()).add(r["how"])
    for ref, want in (("OWE", {"Card"}), ("TESS", {"Card"}), ("SET", {"Card"}),
                      ("REF", {"Bank transfer"})):
        s.check(f"{ref}: {', '.join(sorted(want))}", by_person.get(ref) == want,
                detail=f"{by_person.get(ref)}")
