"""Two ways a guest gets a document without asking twice.

THE TAB THAT SENDS ITS OWN RECEIPT. Settling a tab and then remembering to
email the receipt is two jobs, and the second one is the one that gets dropped
on a busy service. When the house turns it on, a settled tab posts its own
receipt to the address already on file.

Three things guard it, and each is checked here:

  IT IS OFF UNTIL ASKED FOR. An upgrade that shipped with the box ticked would
  start mailing a house's guests without anybody deciding to.

  IT FIRES ONLY WHEN THE BILL IS ACTUALLY SETTLED. A part payment leaves money
  on the tab, and a receipt for a bill that is still open is not a receipt.

  IT NEVER SENDS TWICE. The part payment that finishes the bill would otherwise
  send a second copy, and a guest who gets two receipts for one dinner wonders
  whether they were charged twice.

THE STATEMENT A GUEST CAN POST TO THEMSELVES. The bill is already a page they
can open with their manage token; a business guest needs it as something they
can forward to whoever pays, and asking them to print a web page to PDF is
asking them to do our job.

The check that matters there is the LAST one: it sends only to the address on
the booking, never to one supplied with the request. The manage token is the
credential, so a form field naming the recipient would turn every guest's bill
into something anybody holding a link could post to themselves.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZAUTO"
GUEST = "zzauto.guest@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM pos_payments WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE order_id IN "
                 "(SELECT id FROM pos_orders WHERE table_label LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("UPDATE restaurant_settings SET auto_email_receipt = 0 WHERE id = 1")
    conn.commit()
    conn.close()


def _auto(on):
    conn = db()
    conn.execute("UPDATE restaurant_settings SET auto_email_receipt = ? WHERE id = 1",
                 (1 if on else 0,))
    conn.commit()
    conn.close()


def _stay(ref, email=GUEST):
    conn = db()
    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=3)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 400, 0, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", email,
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _dish(price=25.0):
    conn = db()
    cur = conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, available,
           sold_in_pos, sort_order, created_at)
           VALUES (?, 'main', 'main', ?, 1, 1, 1, 0, ?)""",
        (TAG + " Plat", price, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def _order(label):
    conn = db()
    try:
        return conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                            (label,)).fetchone()
    finally:
        conn.close()


def _tab(oc, label, dish_id, covers="2"):
    oc.post("/pos/open", data={"table_label": label, "covers": covers},
            follow_redirects=True)
    order = _order(label)
    oc.post(f"/pos/{order['id']}/add", data={"menu_item_id": dish_id},
            follow_redirects=True)
    return _order(label)


def run():
    s = Suite("Auto receipt and emailed statement")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    dish = _dish()
    stay = _stay("A")

    sent = []
    real_send = m.send_email

    def capture(to, subject, body, *a, **kw):
        sent.append((to, subject, body))
        return True

    try:
        m.send_email = capture

        s.section("Off until the house asks for it")
        _auto(False)
        tab = _tab(oc, TAG + "1", dish)
        oc.post(f"/pos/{tab['id']}/pay",
                data={"method": "room", "room_booking_id": str(stay["id"])},
                follow_redirects=True)
        s.check("nothing is sent", not sent,
                detail=f"{sent[:1]} — an upgrade started mailing somebody's guests")
        s.check("and nothing is recorded as sent",
                not _order(TAG + "1")["receipt_emailed_at"])

        s.section("On, and a settled tab posts its own receipt")
        _auto(True)
        sent.clear()
        tab = _tab(oc, TAG + "2", dish)
        r = oc.post(f"/pos/{tab['id']}/pay",
                    data={"method": "room", "room_booking_id": str(stay["id"])},
                    follow_redirects=True)
        s.check("it goes", len(sent) == 1, detail=f"{len(sent)}")
        if sent:
            s.check("to the guest whose room it went on", sent[0][0] == GUEST,
                    detail=sent[0][0])
            s.check("with the dish on it", TAG + " Plat" in sent[0][2],
                    detail=f"{sent[0][2][:120]!r}")
        s.check("the floor is told rather than left guessing",
                any("receipt sent" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:2]}")
        s.check("and it is recorded",
                (_order(TAG + "2")["receipt_emailed_to"] or "") == GUEST)

        s.section("A part payment is not a settled bill")
        # Money still on the tab means the bill is open, and a receipt for an
        # open bill is not a receipt.
        #
        # The tab is attached to the stay FIRST. Without that it has no address
        # on it at all, so nothing would be sent whatever the settled check did
        # — the section would pass on a guard it was not testing.
        sent.clear()
        tab = _tab(oc, TAG + "3", dish)
        conn = db()
        conn.execute("UPDATE pos_orders SET room_booking_id = ? WHERE id = ?",
                     (stay["id"], tab["id"]))
        conn.commit()
        conn.close()
        s.check("the tab does have somewhere to send to",
                bool(m.pos_receipt_default_email(db(), _order(TAG + "3"))),
                detail="otherwise the next check proves nothing")
        oc.post(f"/pos/{tab['id']}/pay",
                data={"method": "cash", "amount": "5.00", "received": "5.00"},
                follow_redirects=True)
        s.check("nothing is sent while money is still owed", not sent,
                detail=f"{sent[:1]}")

        s.section("And finishing it sends exactly one")
        oc.post(f"/pos/{tab['id']}/pay", data={"method": "cash"}, follow_redirects=True)
        s.check("the tab is settled", _order(TAG + "3")["status"] != "open",
                detail=f"{_order(TAG + '3')['status']}")
        s.check("one receipt goes", len(sent) == 1, detail=f"{len(sent)}")

        s.section("A second settle on the same tab sends nothing more")
        # Called directly rather than through a reopen-and-re-settle. That was
        # the first attempt and it is not reliable: a tab whose service day has
        # been closed off cannot be reopened at all — correctly — so in a full
        # run, where another suite closes a day, the section quietly tested
        # nothing. This asks the guard the question straight.
        #
        # The rule is automatic once, by hand as often as you like. A second
        # receipt arriving on its own reads as a second charge; a corrected one
        # the owner chose to send does not.
        conn = db()
        again = m.pos_auto_send_receipt(conn, tab["id"])
        conn.commit()
        conn.close()
        s.check("the second call declines", again is None,
                detail=f"returned {again!r}")
        s.check("and nothing more was sent", len(sent) == 1,
                detail=f"{len(sent)} — a second receipt arriving on its own "
                       "reads as a second charge")
        s.check("while sending a corrected one by hand still works",
                oc.post(f"/pos/{tab['id']}/email-receipt", data={"email": GUEST},
                        follow_redirects=True) is not None and len(sent) == 2,
                detail=f"{len(sent)} — the owner has no way to send a corrected "
                       "bill after a tab is amended")

        s.section("A tab with nobody to send to is left alone")
        sent.clear()
        walk_in = _tab(oc, TAG + "4", dish)
        oc.post(f"/pos/{walk_in['id']}/pay",
                data={"method": "cash", "amount": "25.00", "received": "30.00"},
                follow_redirects=True)
        s.check("a cash walk-in gets no email", not sent, detail=f"{sent[:1]}")
        s.check("and the tab still settles",
                _order(TAG + "4")["status"] != "open",
                detail=f"{_order(TAG + '4')['status']}")

        s.section("The statement a guest can post to themselves")
        sent.clear()
        r = anon.post(f"/booking/{stay['manage_token']}/statement/email",
                      follow_redirects=True)
        s.check("it sends", len(sent) == 1, detail=f"{len(sent)}")
        if sent:
            to, subject, body = sent[0]
            s.check("to the address on the booking", to == GUEST, detail=to)
            s.check("with the reference in the subject",
                    stay["reference_code"] in subject, detail=subject)
            s.check("the nights are on it", "night" in body.lower(),
                    detail=f"{body[:150]!r}")
            s.check("and a total", "Total" in body, detail=f"{body[:200]!r}")
            s.check("and a link back to the full statement",
                    stay["manage_token"] in body, detail=f"{body[-200:]!r}")

        s.section("It cannot be aimed at anybody else")
        # The token is the credential. If the form could name the recipient,
        # anybody holding a link could post a guest's bill to themselves.
        sent.clear()
        anon.post(f"/booking/{stay['manage_token']}/statement/email",
                  data={"email": "zzattacker@example.invalid",
                        "to": "zzattacker@example.invalid",
                        "guest_email": "zzattacker@example.invalid"},
                  follow_redirects=True)
        s.check("a supplied address is ignored",
                bool(sent) and sent[0][0] == GUEST,
                detail=f"{sent[0][0] if sent else None} — the bill was posted to "
                       "whoever asked for it")

        s.section("Nothing to send it to, and nothing to send")
        no_email = _stay("B", email="")
        sent.clear()
        r = anon.post(f"/booking/{no_email['manage_token']}/statement/email",
                      follow_redirects=True)
        s.check("a booking with no address sends nothing", not sent, detail=f"{sent[:1]}")
        s.check("and says why", any("email" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:1]}")
        s.check("an unknown token is a 404",
                anon.post("/booking/not-a-real-token/statement/email").status_code == 404)
    finally:
        m.send_email = real_send
        _auto(False)
    s.check("the real sender is restored", m.send_email is real_send)

    s.section("The buttons do not print onto the guest's own bill")
    # no-print is load-bearing: without a rule behind it the two buttons appear
    # on the statement a guest forwards to their accounts department.
    page = anon.get(f"/booking/{stay['manage_token']}/statement").get_data(as_text=True)
    s.check("the actions are marked no-print", "no-print" in page)
    s.check("and no-print actually hides them",
            "no-print{ display:none" in page.replace("\\r", ""),
            detail="the class is on the markup with no rule behind it")

    _cleanup()
    return s
