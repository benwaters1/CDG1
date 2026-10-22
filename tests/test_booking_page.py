"""One stay, on one page -- and the list of them that can be read across.

The bookings list was a pile of cards headed "Booking Requests", with every
form a stay could ever need open on each one. What the owner could not find:

  - WHEN an extra was wanted. The crémant board asks the guest for a day, the
    day is kept on the line (scheduled_for), and the page printed
    extras_summary -- a line of text written at booking time with no day in
    it. So the board on Saturday read exactly like the board on arrival.
  - The letters and texts to the guest. They were on a page of their own that
    nothing on the list linked to.
  - Moving the dates, which was behind "Edit", on a form that posts every
    field on the booking.
  - The refund, which was a tick box under the cancel button and a strip.

This checks the page shows those things and that each action on it does what
it says: moves the dates through the same availability gate and pricing,
moves the day of an extra, puts a new extra on with its day, sends the
confirmation again, writes and texts the guest and files both against THIS
stay, refunds only when asked (Stripe stood in for), and cancels without
moving any money.
"""
import html
import itertools
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZBP"
_IDS = itertools.count(1)


def _q(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
    like = TAG + "%"
    conn.execute(f"DELETE FROM guest_messages WHERE booking_id IN {stays}", (like,))
    conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN {stays}",
                 (like,))
    card = "(SELECT id FROM bookings WHERE reference_code = ?)"
    for table in ("guest_messages", "booking_payments", "tasks"):
        conn.execute(f"DELETE FROM {table} WHERE booking_id IN {card}", (f"GUD-{TAG}CARD",))
    conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' AND booking_id IN {card}",
                 (f"GUD-{TAG}CARD",))
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (like,))
    conn.execute(f"DELETE FROM tasks WHERE booking_id IN {stays}", (like,))
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (like,))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (like,))
    conn.execute("DELETE FROM bookings WHERE reference_code = ?", (f"GUD-{TAG}CARD",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (like,))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM sms_outbox WHERE phone = ?", ("+33612345678",))
    conn.commit()
    conn.close()


class _FakeStripe:
    """Refunds stood in for. Nothing here can reach Stripe."""

    def __init__(self):
        self.refunds = []
        outer = self

        class Refund:
            @staticmethod
            def create(**kw):
                outer.refunds.append(kw)
                obj = type("StripeRefund", (), {})()
                obj.id = f"re_{TAG}{next(_IDS)}"
                return obj

            @staticmethod
            def list(payment_intent=None, limit=None):
                return {"data": []}

        self.Refund = Refund


def _with_stripe(fake, fn):
    saved = m.stripe, m.STRIPE_SECRET_KEY
    m.stripe, m.STRIPE_SECRET_KEY = fake, "sk_test_stand_in"
    try:
        return fn()
    finally:
        m.stripe, m.STRIPE_SECRET_KEY = saved


def _room():
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    finally:
        conn.close()


def _extra(name, price, ask_when=1):
    conn = db()
    conn.execute(
        """INSERT INTO extras (name, price, active, sort_order, guest_bookable, ask_when)
           VALUES (?, ?, 1, 99, 1, ?)""", (f"{TAG} {name}", price, ask_when))
    row = conn.execute("SELECT * FROM extras WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.commit()
    conn.close()
    return row


def _stay(ref, room, *, offset=40, nights=2, status="confirmed", paid=0.0,
          phone="+33612345678", clear=0):
    """A stay written straight in, `offset` days out.

    `clear` is for a stay the suite goes on to MOVE: it asks for that many
    nights from the arrival that the house would really take, rather than
    trusting whatever sits at the offset on the copied calendar. On 22
    October 2026 the main stay's move landed in a workshop that holds the
    whole house, was refused, and everything after it either went red or
    crashed for want of the extra the move should have carried.
    """
    if clear:
        arrival = _harness.free_window(room["id"], clear, after_days=offset)
    else:
        arrival = house_today() + timedelta(days=offset)
    conn = db()
    departure = arrival + timedelta(days=nights)
    room_total = round(m.compute_room_total(conn, room, arrival, departure), 2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?)""",
        (room["id"], f"{TAG}{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} Guest {ref}",
         f"{TAG.lower()}{ref.lower()}@example.invalid", phone, arrival.isoformat(),
         departure.isoformat(), status, "paid" if paid else "unpaid", room_total, paid,
         datetime.now(timezone.utc).isoformat()))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    m.stamp_room_total(conn, bid, room_total, arrival.isoformat(), departure.isoformat())
    if paid:
        conn.execute(
            """INSERT INTO booking_payments (booking_id, amount, method, stripe_session_id,
               stripe_payment_intent_id, created_at) VALUES (?, ?, 'stripe', ?, ?, ?)""",
            (bid, paid, f"cs_{TAG}{ref}", f"pi_{TAG}{ref}",
             datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _booking(bid):
    return _q("SELECT * FROM bookings WHERE id = ?", bid)[0]


def _day_words(iso):
    d = m.parse_date(iso)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def run():
    s = Suite("One stay, on one page")
    _cleanup()
    oc, ec, owner, emp = clients()
    room = _room()
    board = _extra("Crémant board", 80.0, ask_when=1)
    tour = _extra("Tour", 50.0, ask_when=1)

    # Free for six nights: its own two, and the three it is moved to later
    # on, three days after it arrives.
    stay = _stay("MAIN", room, paid=200.0, clear=6)
    arrival = m.parse_date(stay["arrival_date"])
    second_day = (arrival + timedelta(days=1)).isoformat()
    conn = db()
    m.add_booking_extra(conn, "room", stay["id"], board, 1, scheduled_for=second_day,
                        notes="6pm, on the terrace", in_booking_total=False)
    conn.commit()
    conn.close()

    s.section("A card-paid booking keeps the day an extra is wanted")
    # Guests pay by card, so their booking is written by the Stripe webhook
    # from the checkout's metadata -- a second road from the form to the line,
    # and the one almost every real booking takes. Nothing else walked it.
    a0 = house_today() + timedelta(days=150)
    d0 = a0 + timedelta(days=3)
    details = {board["id"]: {"on": a0.isoformat(), "notes": "On arrival — 6pm"},
               tour["id"]: {"on": (a0 + timedelta(days=1)).isoformat(), "notes": "10am"}}
    meta = {"reference_code": f"GUD-{TAG}CARD", "room_id": str(room["id"]),
            "guest_name": f"{TAG} Card", "guest_email": f"{TAG.lower()}card@example.invalid",
            "guest_phone": "", "arrival_date": a0.isoformat(), "departure_date": d0.isoformat(),
            "party_size": "2", "guests_under_18": "0", "special_requests": "",
            "extra_ids": f"{board['id']},{tour['id']}",
            "extra_when": m.pack_extra_details(details), "promo_code": ""}
    conn = db()
    try:
        with m.app.test_request_context("/"):
            m.create_booking_from_stripe_session(conn, {
                "id": f"cs_{TAG}card", "payment_intent": f"pi_{TAG}card", "metadata": meta,
                "amount_total": 0, "payment_status": "paid"})
        lines = {r["extra_id"]: (r["scheduled_for"], r["notes"]) for r in conn.execute(
            """SELECT extra_id, scheduled_for, notes FROM booking_extras
                WHERE category = 'room' AND booking_id =
                      (SELECT id FROM bookings WHERE reference_code = ?)""",
            (f"GUD-{TAG}CARD",)).fetchall()}
    finally:
        conn.close()
    s.check("the board keeps its day and its note through the webhook",
            lines.get(board["id"]) == (a0.isoformat(), "On arrival — 6pm"), detail=str(lines))
    s.check("and so does the tour", lines.get(tour["id"])
            == ((a0 + timedelta(days=1)).isoformat(), "10am"), detail=str(lines))

    s.section("The list reads across, and says when an extra is wanted")
    body = oc.get(f"/admin/bookings?q={TAG}MAIN").get_data(as_text=True)
    s.check("it is called Bookings", "<h1>Bookings</h1>" in body
            and "Booking Requests" not in body,
            detail="the owner: it still says booking requests")
    s.check("the stay is a row in a table, inside a table-wrap",
            'class="data-table bk-table"' in body and "table-wrap" in body)
    s.check("the extra is on the row by name", f"{TAG} Crémant board" in html.unescape(body))
    s.check("with the day the guest wants it", _day_words(second_day) in body,
            detail=f"looked for {_day_words(second_day)!r}: the day was kept and never shown")
    s.check("and the row opens the stay's own page",
            f'href="/admin/bookings/{stay["id"]}"' in body)
    bill = _bill(stay["id"])
    s.check("the row's owed figure is the bill's, extras included",
            m.money(bill["owed"]) in html.unescape(body),
            detail=f"bill says {bill['owed']}")

    s.section("The stay's page has everything on it")
    r = oc.get(f"/admin/bookings/{stay['id']}")
    page = html.unescape(r.get_data(as_text=True))
    s.check("it opens", r.status_code == 200, detail=str(r.status_code))
    s.check("the extra, its day and the note",
            f"{TAG} Crémant board" in page and "6pm, on the terrace" in page
            and m.format_date_short(second_day) in page)
    s.check("what came in, and how", "card, through Stripe" in page and "€200.00" in page)
    s.check("a refund form, apart from cancelling",
            'name="reason_code"' in page and "Make the refund" in page
            and 'name="refund_paid"' not in page,
            detail="cancelling and refunding are two deliberate acts, not one tick box")
    s.check("and cancelling says it moves no money", "It moves no money" in page)
    s.check("send the confirmation again", "Send the confirmation again" in page)
    s.check("write to the guest, and text them", "Send the letter" in page
            and "Send the text" in page)
    s.check("move the dates", 'name="move_arrival"' in page)
    conn = db()
    conn.execute(
        """INSERT INTO tasks (title, notes, priority, due_date, created_at, origin, booking_id)
           VALUES (?, '', 'high', ?, ?, 'checklist', ?)""",
        (f"Prepare the room — {TAG}MAIN", stay["arrival_date"],
         datetime.now(timezone.utc).isoformat(), stay["id"]))
    conn.commit()
    conn.close()
    page = html.unescape(oc.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True))
    s.check("the house's own jobs for the stay are under arrival and departure",
            f"Prepare the room — {TAG}MAIN" in page[page.index("Arrival & departure</h2>"):])
    s.check("and not among what the guest is waiting on", "Waiting on the house" not in page,
            detail="a checklist is the house's work, not a request somebody made")
    s.check("arrival prep is still there, lower down",
            page.index("Assign arrival prep") > page.index("Move dates</h2>"))
    s.check("an employee cannot open it",
            ec.get(f"/admin/bookings/{stay['id']}").status_code in (302, 403))

    s.section("Moving the dates: the same gate, the same prices")
    # Well past the nights the stay is moved into below, wherever it landed.
    blocker = _stay("BLOCK", room, offset=(arrival - house_today()).days + 20, nights=3)
    new_arrival = arrival + timedelta(days=3)
    new_departure = new_arrival + timedelta(days=3)
    page = html.unescape(oc.get(
        f"/admin/bookings/{stay['id']}?move_arrival={new_arrival}&move_departure={new_departure}"
    ).get_data(as_text=True))
    conn = db()
    expect_room = round(m.compute_room_total(conn, room, new_arrival, new_departure), 2)
    conn.close()
    s.check("a free stretch is quoted at the rate card's price for those nights",
            "is free" in page and m.money(expect_room) in page,
            detail=f"expected the room at {m.money(expect_room)}")
    taken_from = m.parse_date(blocker["arrival_date"])
    page = html.unescape(oc.get(
        f"/admin/bookings/{stay['id']}?move_arrival={taken_from}"
        f"&move_departure={taken_from + timedelta(days=2)}").get_data(as_text=True))
    s.check("a taken stretch says so, and offers no button", "Not free" in page
            and "Move the stay" not in page)
    r = oc.post(f"/admin/bookings/{stay['id']}/move-dates",
                data={"arrival_date": taken_from.isoformat(),
                      "departure_date": (taken_from + timedelta(days=2)).isoformat()},
                follow_redirects=True)
    s.check("and posting it anyway is refused", _booking(stay["id"])["arrival_date"]
            == arrival.isoformat(), detail=str(flashes(r)))

    conn = db()
    conn.execute(
        """INSERT INTO tasks (title, notes, priority, due_date, created_at, origin, booking_id)
           VALUES (?, 'move us please', 'normal', ?, ?, 'guest_request', ?)""",
        (f"Date change request — {TAG}MAIN", new_arrival.isoformat(),
         datetime.now(timezone.utc).isoformat(), stay["id"]))
    conn.commit()
    conn.close()
    before_total = _booking(stay["id"])["total_price"]
    r = oc.post(f"/admin/bookings/{stay['id']}/move-dates",
                data={"arrival_date": new_arrival.isoformat(),
                      "departure_date": new_departure.isoformat(), "tell_guest": "1"},
                follow_redirects=True)
    moved = _booking(stay["id"])
    s.check("the stay moves", (moved["arrival_date"], moved["departure_date"])
            == (new_arrival.isoformat(), new_departure.isoformat()), detail=str(flashes(r)))
    s.check("repriced: the new nights, the extras carried",
            round(moved["total_price"], 2) == round(expect_room + (before_total - stay["total_price"]), 2)
            and round(moved["room_total_quoted"] or 0, 2) == expect_room,
            detail=f"total {moved['total_price']} room {moved['room_total_quoted']}")
    line = _q("SELECT scheduled_for FROM booking_extras WHERE booking_id = ? AND category = 'room'",
              stay["id"])[0]
    s.check("the board moves with it, to the same day of the stay",
            line["scheduled_for"] == (new_arrival + timedelta(days=1)).isoformat(),
            detail=f"{line['scheduled_for']}: left behind, it is made for an empty room")
    s.check("written on its history",
            bool(_q("SELECT 1 FROM audit_log WHERE action = 'booking_dates_moved' AND target = ?",
                    f"{TAG}MAIN")))
    s.check("the guest's request for new dates is answered",
            not _q("""SELECT 1 FROM tasks WHERE booking_id = ? AND status != 'done'
                      AND origin = 'guest_request'""", stay["id"]))
    s.check("while the house's own checklist for the stay is left alone",
            bool(_q("""SELECT 1 FROM tasks WHERE booking_id = ? AND status != 'done'
                       AND origin = 'checklist'""", stay["id"])))
    s.check("and the guest is sent the new dates, against this stay",
            bool(_q("""SELECT 1 FROM guest_messages WHERE booking_id = ?
                       OR (about_category = 'room' AND about_id = ?)""", stay["id"], stay["id"])))
    s.check("the page lands back on the stay", r.request.path == f"/admin/bookings/{stay['id']}")

    short = _stay("SHORT", room, offset=120, nights=3, clear=3)
    conn = db()
    conn.execute("UPDATE rooms SET min_nights = 3 WHERE id = ?", (room["id"],))
    conn.commit()
    conn.close()
    try:
        a = m.parse_date(short["arrival_date"])
        oc.post(f"/admin/bookings/{short['id']}/move-dates",
                data={"arrival_date": a.isoformat(),
                      "departure_date": (a + timedelta(days=1)).isoformat()})
        s.check("shorter than the minimum is refused unless ticked",
                _booking(short["id"])["departure_date"] == (a + timedelta(days=3)).isoformat())
        oc.post(f"/admin/bookings/{short['id']}/move-dates",
                data={"arrival_date": a.isoformat(),
                      "departure_date": (a + timedelta(days=1)).isoformat(),
                      "allow_short": "1"})
        s.check("and let when the house says so",
                _booking(short["id"])["departure_date"] == (a + timedelta(days=1)).isoformat())
    finally:
        conn = db()
        conn.execute("UPDATE rooms SET min_nights = ? WHERE id = ?",
                     (room["min_nights"], room["id"]))
        conn.commit()
        conn.close()

    s.section("Extras: put on with a day, and the day changed")
    moved = _booking(stay["id"])
    a = m.parse_date(moved["arrival_date"])
    day3 = (a + timedelta(days=2)).isoformat()
    r = oc.post(f"/admin/bookings/{stay['id']}/extras/add",
                data={"extra_id": tour["id"], "quantity": "1", "extra_when": day3,
                      "extra_note": "10am"}, follow_redirects=True)
    added = _q("""SELECT * FROM booking_extras WHERE booking_id = ? AND category = 'room'
                  AND extra_id = ?""", stay["id"], tour["id"])
    s.check("the tour is on the stay, for the day asked",
            len(added) == 1 and added[0]["scheduled_for"] == day3
            and "10am" in (added[0]["notes"] or ""), detail=str(flashes(r)))
    s.check("and on the bill", any(l["label"].startswith(f"{TAG} Tour")
                                   for l in _bill(stay["id"])["lines"]))
    oc.post(f"/admin/bookings/{stay['id']}/extras/add",
            data={"extra_id": tour["id"], "quantity": "1",
                  "extra_when": (a + timedelta(days=30)).isoformat()})
    s.check("a day outside the stay is refused",
            len(_q("SELECT 1 FROM booking_extras WHERE booking_id = ? AND extra_id = ?",
                   stay["id"], tour["id"])) == 1)
    r = oc.post(f"/admin/bookings/extras/{added[0]['id']}/when",
                data={"extra_when": a.isoformat(), "extra_note": "on arrival"},
                follow_redirects=True)
    again = _q("SELECT scheduled_for, notes FROM booking_extras WHERE id = ?", added[0]["id"])[0]
    s.check("its day can be changed", again["scheduled_for"] == a.isoformat()
            and again["notes"] == "on arrival", detail=f"{tuple(again)} {flashes(r)}")
    r = oc.post(f"/admin/bookings/extras/{added[0]['id']}/when",
                data={"extra_when": (a - timedelta(days=5)).isoformat()}, follow_redirects=True)
    s.check("but not to a day they are not here",
            _q("SELECT scheduled_for FROM booking_extras WHERE id = ?",
               added[0]["id"])[0][0] == a.isoformat())
    # The run sheet's two steps, from the stay's own page: arranged (with how),
    # then handed over -- each saying who and when, and coming back here.
    here = {"Referer": f"http://localhost/admin/bookings/{stay['id']}"}
    r = oc.post(f"/extras/{added[0]['id']}/arranged", data={"note": f"{TAG} booked with Marc"},
                headers=here)
    page = html.unescape(oc.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True))
    s.check("arranged, how and by whom, is on the line",
            f"{TAG} booked with Marc" in page and "Arranged " in page
            and r.headers.get("Location", "").endswith(f"/admin/bookings/{stay['id']}"),
            detail=r.headers.get("Location"))
    oc.post(f"/extras/{added[0]['id']}/delivered", headers=here)
    page = html.unescape(oc.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True))
    s.check("and handed over, with when",
            "Handed over " + m.format_date_short(m.house_today_iso()) in page,
            detail="the line should say it reached the guest, and on which day")

    s.section("Letters and texts, kept with the stay")
    r = oc.post(f"/admin/bookings/{stay['id']}/write",
                data={"subject": f"{TAG} About your dinner", "body": "Dear guest,\n\nSee you soon."},
                follow_redirects=True)
    filed = _q("SELECT * FROM guest_messages WHERE booking_id = ? AND subject = ?",
               stay["id"], f"{TAG} About your dinner")
    s.check("a letter written here is filed against this stay", len(filed) == 1,
            detail=str(flashes(r)))
    s.check("and shown on its page", f"{TAG} About your dinner" in html.unescape(
        oc.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True)))
    s.check("an empty letter is refused", "needs a subject" in " ".join(flashes(
        oc.post(f"/admin/bookings/{stay['id']}/write", data={"subject": "", "body": ""},
                follow_redirects=True))))

    before = len(_q("SELECT 1 FROM guest_messages WHERE booking_id = ?", stay["id"]))
    r = oc.post(f"/admin/bookings/{stay['id']}/resend-confirmation", follow_redirects=True)
    after = len(_q("SELECT 1 FROM guest_messages WHERE booking_id = ?", stay["id"]))
    s.check("the confirmation goes again, and is filed", after == before + 1,
            detail=str(flashes(r)))
    s.check("on the stay's history",
            bool(_q("SELECT 1 FROM audit_log WHERE action = 'booking_confirmation_resent' "
                    "AND target = ?", f"{TAG}MAIN")))
    pending = _stay("PEND", room, offset=200, status="pending")
    oc.post(f"/admin/bookings/{pending['id']}/resend-confirmation")
    s.check("a pending request has no confirmation to resend",
            not _q("SELECT 1 FROM guest_messages WHERE booking_id = ?", pending["id"]))

    # A second stay on the same telephone, and one they are ON now -- which is
    # the stay a lookup by number picks. The text written from the first
    # stay's page must still be filed against the first stay.
    _stay("HERENOW", room, offset=-1, nights=3)
    r = oc.post(f"/admin/bookings/{stay['id']}/text",
                data={"body": f"{TAG} The gate code is on your page."}, follow_redirects=True)
    texts = _q("""SELECT booking_id FROM guest_messages WHERE channel = 'sms'
                  AND body LIKE ?""", f"{TAG} The gate code%")
    s.check("a text written here is filed against this stay, not the one the number is on now",
            [t["booking_id"] for t in texts] == [stay["id"]],
            detail=f"{[t['booking_id'] for t in texts]} {flashes(r)}")
    landline = _stay("LAND", room, offset=320, phone="not a number")
    r = oc.post(f"/admin/bookings/{landline['id']}/text", data={"body": "hello"},
                follow_redirects=True)
    s.check("a number that cannot take a text is refused, and says why",
            any("Not sent" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("When it can go, the page says it went")
    # Under test nothing leaves the building, so every send above was held and
    # said so. Stood in here so the branch that tells the owner a letter or a
    # text WENT is run too -- and says so only when it did.
    sent, texted = [], []
    real_send, real_sms = m.send_email, m.send_sms
    m.send_email = lambda to, subject, body, **k: sent.append((to, subject)) or True
    m.send_sms = lambda conn, number, body, **k: texted.append(body) or (True, None)
    try:
        r1 = oc.post(f"/admin/bookings/{stay['id']}/write",
                     data={"subject": f"{TAG} Your table", "body": "Dear guest, it is booked."},
                     follow_redirects=True)
        r2 = oc.post(f"/admin/bookings/{stay['id']}/resend-confirmation", follow_redirects=True)
        r3 = oc.post(f"/admin/bookings/{stay['id']}/text",
                     data={"body": f"{TAG} See you at six."}, follow_redirects=True)
    finally:
        m.send_email, m.send_sms = real_send, real_sms
    s.check("a letter that went says it went",
            any("Sent to" in f for f in flashes(r1))
            and (stay["guest_email"], f"{TAG} Your table") in sent, detail=str(flashes(r1)))
    s.check("so does the confirmation sent again",
            any("Confirmation sent again" in f for f in flashes(r2)), detail=str(flashes(r2)))
    s.check("and a text", any(f == "Text sent." for f in flashes(r3))
            and texted == [f"{TAG} See you at six."], detail=str(flashes(r3)))

    s.section("Refunds: asked for, partial, and back to this page")
    fake = _FakeStripe()
    r = _with_stripe(fake, lambda: oc.post(
        f"/admin/refunds/room/{stay['id']}",
        data={"payment": f"bp{_q('SELECT id FROM booking_payments WHERE booking_id = ?', stay['id'])[0][0]}",
              "amount": "50", "reason_code": "goodwill", "note": "", "bill": "reduce",
              "back": "booking"}))
    s.check("fifty euros go back to the card", [k.get("amount") for k in fake.refunds] == [5000],
            detail=str(fake.refunds))
    s.check("recorded once", [round(x["amount"], 2) for x in _q(
        "SELECT amount FROM refunds WHERE category = 'room' AND booking_id = ?", stay["id"])]
            == [50.0])
    s.check("and the owner is back on the stay's money",
            r.headers.get("Location", "").endswith(f"/admin/bookings/{stay['id']}#money"),
            detail=r.headers.get("Location"))
    page = html.unescape(oc.get(f"/admin/bookings/{stay['id']}").get_data(as_text=True))
    s.check("the page shows what went back", "€50.00" in page and "Refunds made" in page)

    s.section("Cancelling moves no money")
    fake = _FakeStripe()
    r = _with_stripe(fake, lambda: oc.post(f"/admin/bookings/{stay['id']}/cancel",
                                           data={"cancel_reason": "plans_changed",
                                                 "back": "booking"}))
    s.check("cancelled", _booking(stay["id"])["status"] == "cancelled")
    s.check("nothing sent to Stripe", not fake.refunds, detail=str(fake.refunds))
    s.check("and back on the stay's page",
            r.headers.get("Location", "").endswith(f"/admin/bookings/{stay['id']}"),
            detail=r.headers.get("Location"))
    r = oc.post(f"/admin/bookings/{stay['id']}/move-dates",
                data={"arrival_date": new_arrival.isoformat(),
                      "departure_date": (new_departure + timedelta(days=1)).isoformat()},
                follow_redirects=True)
    s.check("a cancelled stay cannot be moved",
            _booking(stay["id"])["departure_date"] == new_departure.isoformat())

    _cleanup()
    return s


def _bill(bid):
    conn = db()
    try:
        return m.booking_bill(conn, bid)
    finally:
        conn.close()
