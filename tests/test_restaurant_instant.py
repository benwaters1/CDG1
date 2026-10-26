"""A table that confirms itself once it is paid, and only when it should.

The booking page has said, since the 1 October handover, that a table is
"confirmed as soon as the payment goes through" when the house switches
restaurant_instant on. Nothing in app.py read the switch -- and the page could
not have read it either, because the key was never allowed out to the public
templates. So switching it on would have changed nothing, and had it reached
the page it would have promised something that never happened.

What is checked here, all of it with Stripe stood in and the mail caught:

  * both ways a paid table arrives -- the return page and the webhook --
    confirm it, and the guest gets the confirmation letter INSTEAD of
    "request received", never both
  * every reason the house already keeps for wanting to look first leaves it
    a request, the guest is told it is a request, and the owner is told why
  * the owner's Confirm button and the automatic path are one action: the
    standing instruction, the guest profile and the letter happen in both
  * the switch is on the settings page, saved, and reaches the public page
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, keep_rows, visible_text
import _harness

m = _harness.m
TAG = "ZZINSTANT"
DOMAIN = "instant-test.invalid"
GUEST_IP = "203.0.113.83"
SETTING_KEYS = ("restaurant_instant", "dining_mode", "dining_reopens", "dining_days")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _email(who):
    return f"{who.lower()}@{DOMAIN}"


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", ("%@" + DOMAIN,))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_dishes WHERE menu_id IN "
                 "(SELECT id FROM menus WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menus WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_tables WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()


def _free_evening(conn, after_days=120):
    """An evening with nothing on it: no table, no atelier, no card."""
    day = m.house_today() + timedelta(days=after_days)
    for _ in range(400):
        iso = day.isoformat()
        busy = conn.execute(
            "SELECT 1 FROM restaurant_bookings WHERE dinner_date = ? "
            "AND status IN ('pending', 'confirmed')", (iso,)).fetchone()
        card = conn.execute("SELECT 1 FROM menus WHERE service_date = ?", (iso,)).fetchone()
        with m.app.test_request_context("/"):
            row = m.dinner_covers_forecast(conn, start=day, days=1)["rows"][0]
        if not busy and not card and not row["atelier"]:
            return day
        day += timedelta(days=1)
    raise RuntimeError("no free evening in the test database")


def _setting(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def _room(conn, capacity=12, opening=None):
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = ?, price_per_person = 50, "
                 "deposit_percent = NULL, opening_date = ? WHERE id = 1", (capacity, opening))
    conn.commit()


def _table_booked(conn, who, party, day, status="confirmed"):
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
             guest_email, party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (TAG + who + day, TAG + "tok" + who + day, TAG + " " + who, _email(who),
         party, day, status, _now()))
    conn.commit()


class _Mail:
    """send_email caught, for the length of a `with`. Nothing leaves."""

    def __enter__(self):
        self.sent = []
        self._real = m.send_email

        def _catch(to, subject, body, *a, **kw):
            self.sent.append({"to": to, "subject": subject,
                              "template_key": kw.get("template_key")})
            return True
        m.send_email = _catch
        return self

    def __exit__(self, *_exc):
        m.send_email = self._real
        return False

    def keys_to(self, address):
        return [x["template_key"] for x in self.sent if x["to"] == address]


class _Stripe:
    """Stripe answering one known session, and nothing else.

    The return page retrieves the session and the webhook is handed it, so
    both are stood in here: retrieve gives back exactly the session made for
    the test, and the signature check accepts it. The key stays blank
    throughout (_harness), so anything that slipped past would meet a library
    with no credential."""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self._enabled = m.stripe_enabled
        self._retrieve = m.stripe.checkout.Session.retrieve
        self._construct = m.stripe.Webhook.construct_event
        self._secret = m.STRIPE_WEBHOOK_SECRET
        m.stripe_enabled = lambda: True

        def _retrieve(session_id, *a, **kw):
            if session_id != self.session["id"]:
                raise AssertionError("retrieved a session this test did not make")
            return self.session
        m.stripe.checkout.Session.retrieve = _retrieve
        m.STRIPE_WEBHOOK_SECRET = "whsec_test_only_never_real"
        m.stripe.Webhook.construct_event = (
            lambda payload, sig, secret: {"type": "checkout.session.completed",
                                          "data": {"object": self.session}})
        return self

    def __exit__(self, *_exc):
        m.stripe_enabled = self._enabled
        m.stripe.checkout.Session.retrieve = self._retrieve
        m.stripe.Webhook.construct_event = self._construct
        m.STRIPE_WEBHOOK_SECRET = self._secret
        return False


def _session(who, party, day, price=50):
    total = price * party
    return {
        "id": f"cs_test_{TAG}_{who}_{day}",
        "payment_intent": f"pi_test_{TAG}_{who}",
        "payment_status": "paid",
        "amount_total": int(total * 100),
        "metadata": {
            "kind": "restaurant", "reference_code": "",
            "guest_name": f"{TAG} {who}", "guest_email": _email(who),
            "guest_phone": "", "dinner_date": day, "party_size": str(party),
            "dietary_notes": "", "promo_code": "",
            "total_price": str(float(total)), "discount_amount": "0.0",
        },
    }


def _pay(guest, who, party, day, via="return"):
    """A table paid for, arriving the way `via` says. (booking row, mail, response)."""
    sess = _session(who, party, day)
    with _Mail() as mail, _Stripe(sess):
        if via == "return":
            r = guest.get(f"/restaurant/stripe-success?session_id={sess['id']}",
                          follow_redirects=False)
        else:
            r = guest.post("/webhooks/stripe", data=b"{}",
                           headers={"Stripe-Signature": "t=1,v1=stub"})
    conn = db()
    row = conn.execute("SELECT * FROM restaurant_bookings WHERE stripe_session_id = ?",
                       (sess["id"],)).fetchone()
    conn.close()
    return row, mail, r


def _held(s, guest, who, party, day, why, label, keep=False):
    """A paid table that must stay a request, for the reason given.

    Taken away afterwards unless `keep`: a request still waiting is a cover
    (the booking form's rule), so one left behind would be the reason the
    NEXT case was held, and that case would pass without its own rule ever
    being asked."""
    row, mail, _r = _pay(guest, who, party, day)
    if not keep:
        conn = db()
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email = ?", (_email(who),))
        conn.commit()
        conn.close()
    s.check(f"{label}: it stays a request",
            row is not None and row["status"] == "pending",
            detail=str(row and row["status"]))
    s.check(f"{label}: the owner is told why",
            row is not None and why in (row["instant_held"] or ""),
            detail=str(row and row["instant_held"]))
    s.check(f"{label}: the guest hears it is a request, and is not told it is confirmed",
            mail.keys_to(_email(who)) == ["restaurant_reservation_received"],
            detail=str(mail.keys_to(_email(who))))
    return row


def run():
    s = Suite("A paid table confirms itself, when the evening has room")
    oc, _ec, _owner, _emp = clients()
    guest = m.app.test_client()
    guest.environ_base["REMOTE_ADDR"] = GUEST_IP
    conn = db()
    _cleanup(conn)

    put_settings = keep_rows(conn, "restaurant_settings", "id = 1")
    marks = ",".join("?" * len(SETTING_KEYS))
    put_app = keep_rows(conn, "app_settings", f"key IN ({marks})", SETTING_KEYS)
    # The floor plan, laid for the test and put back as it was. Not a
    # delete-and-reinsert: bookings and tabs point at these rows, and the
    # foreign keys would quietly unseat every one of them.
    laid = [r["id"] for r in conn.execute("SELECT id FROM restaurant_tables WHERE active = 1")]
    try:
        conn.execute("UPDATE restaurant_tables SET active = 0 WHERE active = 1")
        for label, seats in ((TAG + "4", 4), (TAG + "6", 6)):
            conn.execute("INSERT INTO restaurant_tables (label, area, seats, sort_order, active, "
                         "created_at) VALUES (?, 'salle', ?, 99, 1, ?)", (label, seats, _now()))
        conn.commit()
        _setting(conn, "dining_mode", None)
        _setting(conn, "dining_days", None)
        _room(conn)
        day = _free_evening(conn)
        D = day.isoformat()

        s.section("Switched off, a paid table is a request, as it always was")
        _setting(conn, "restaurant_instant", None)
        row, mail, _r = _pay(guest, "Off", 2, D)
        s.check("it is written down, paid", row is not None and row["payment_status"] == "paid",
                detail=str(row and dict(row)))
        s.check("and waits for the owner", row is not None and row["status"] == "pending",
                detail=str(row and row["status"]))
        s.check("the guest gets 'request received' and nothing else",
                mail.keys_to(_email("Off")) == ["restaurant_reservation_received"],
                detail=str(mail.keys_to(_email("Off"))))
        s.check("and nothing is recorded as held: there was no promise to keep",
                row is not None and not row["instant_held"], detail=str(row and row["instant_held"]))
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email = ?", (_email("Off"),))
        conn.commit()

        _setting(conn, "restaurant_instant", "1")

        s.section("Switched on, paid, and the evening has room: from the return page")
        row, mail, r = _pay(guest, "Back", 2, D, via="return")
        s.check("it is confirmed", row is not None and row["status"] == "confirmed",
                detail=f"{row and row['status']}; held: {row and row['instant_held']}")
        s.check("with the time it was decided",
                row is not None and bool(row["decided_at"]))
        s.check("the guest gets the confirmation letter, and not 'request received' as well",
                mail.keys_to(_email("Back")) == ["restaurant_confirmed"],
                detail=str(mail.keys_to(_email("Back"))))
        page = visible_text(guest.get(r.headers.get("Location", "/x")).get_data(as_text=True)) \
            if r.status_code in (302, 303) else ""
        s.check("and the page they land on says the table is booked",
                "Your table is booked" in page, detail=f"{r.status_code}: {page[:160]}")
        log = conn.execute("SELECT * FROM audit_log WHERE target = ? ORDER BY id",
                           (row["reference_code"] if row else "",)).fetchall()
        took = [a for a in log if a["action"] == "restaurant_booking_confirmed_online"]
        s.check("its history says it confirmed itself, against nobody",
                len(took) == 1 and took[0]["actor_user_id"] is None,
                detail=str([(a["action"], a["actor_user_id"]) for a in log]))
        # The same confirmation as the button's: the profile is made there.
        s.check("the guest has a profile, as the Confirm button makes one",
                conn.execute("SELECT 1 FROM guests WHERE email = ?",
                             (_email("Back"),)).fetchone() is not None)
        owner = conn.execute("SELECT id FROM users WHERE role = 'owner' LIMIT 1").fetchone()
        note = conn.execute(
            "SELECT title FROM notifications WHERE user_id = ? AND title LIKE ? ORDER BY id DESC",
            (owner["id"], f"%{TAG} Back%")).fetchone()
        s.check("the owner is told it is confirmed, not asked to decide",
                note is not None and "confirmed" in note["title"].lower()
                and "waiting on you" not in note["title"],
                detail=str(note and note["title"]))

        s.section("From the webhook, for a guest who closed the tab")
        row, mail, r = _pay(guest, "Hook", 2, D, via="webhook")
        s.check("the webhook answered", r.status_code == 200, detail=str(r.status_code))
        s.check("it is confirmed", row is not None and row["status"] == "confirmed",
                detail=f"{row and row['status']}; held: {row and row['instant_held']}")
        s.check("the guest gets the confirmation letter alone",
                mail.keys_to(_email("Hook")) == ["restaurant_confirmed"],
                detail=str(mail.keys_to(_email("Hook"))))
        # And the return page arriving after it changes nothing.
        sess = _session("Hook", 2, D)
        with _Mail() as again, _Stripe(sess):
            guest.get(f"/restaurant/stripe-success?session_id={sess['id']}")
        s.check("the return page after it writes nothing and sends nothing",
                conn.execute("SELECT COUNT(*) AS c FROM restaurant_bookings WHERE guest_email = ?",
                             (_email("Hook"),)).fetchone()["c"] == 1 and not again.sent,
                detail=str(again.sent))
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email IN (?, ?)",
                     (_email("Back"), _email("Hook")))
        conn.commit()

        s.section("Anything the house would want to look at first stays a request")
        # Covers: six confirmed and a party of four, in a room that takes eight.
        _room(conn, capacity=8)
        _table_booked(conn, "Six", 6, D)
        held = _held(s, guest, "Four", 4, D, "against the 8 covers", "over the covers", keep=True)
        conf = guest.get(f"/restaurant/confirmation/{held['manage_token']}") if held else None
        s.check("over the covers: the page they land on says requested, not booked",
                conf is not None and "requested" in visible_text(conf.get_data(as_text=True))
                and "Your table is booked" not in conf.get_data(as_text=True))
        queue = oc.get("/admin/restaurant?q=" + TAG + "+Four").get_data(as_text=True)
        s.check("over the covers: the reason is beside Confirm in the owner's queue",
                "Not confirmed automatically" in queue and "against the 8 covers" in queue)
        note = conn.execute("SELECT title, body FROM notifications WHERE title LIKE ? "
                            "ORDER BY id DESC", (f"%{TAG} Four%",)).fetchone()
        s.check("over the covers: the owner's notification says it is waiting, and why",
                note is not None and "waiting on you" in note["title"]
                and "against the 8 covers" in (note["body"] or ""),
                detail=str(note and dict(note)))
        # A reservation still waiting counts too: it is the booking form's rule.
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
        conn.commit()
        _table_booked(conn, "Waiting", 6, D, status="pending")
        _held(s, guest, "Four2", 4, D, "against the 8 covers", "a request already waiting counts")
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
        conn.commit()

        # Seats: the room takes twelve, but the floor plan lays ten.
        _room(conn, capacity=12)
        _table_booked(conn, "Six", 6, D)
        _held(s, guest, "Five", 5, D, "floor plan seats 10", "more than the floor plan seats")
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
        conn.commit()

        _held(s, guest, "Seven", 7, D, "biggest table seats (6)", "a party bigger than any table")
        _held(s, guest, "Big", 13, D, "more than the 12 the room takes", "a party bigger than the room")

        # The atelier eats here, so its guests are covers.
        conn.execute("INSERT INTO workshops (title, active, created_at) VALUES (?, 1, ?)",
                     (TAG + " atelier", _now()))
        wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                           (TAG + " atelier",)).fetchone()["id"]
        conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
                          notes, created_at) VALUES (?, ?, ?, 15, ?, ?)""",
                     (wid, D, (day + timedelta(days=2)).isoformat(), TAG + " sess", _now()))
        sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                           (TAG + " sess",)).fetchone()["id"]
        conn.execute("""INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                          guest_name, guest_email, party_size, status, total_price, created_at)
                        VALUES (?, ?, ?, 'A Potter', ?, 9, 'confirmed', 100, ?)""",
                     (sid, TAG + "W1", TAG + "wtok", _email("potter"), _now()))
        conn.commit()
        _held(s, guest, "Atelier", 4, D, "9 atelier guests dining in", "atelier guests eating in")
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.commit()

        # Closed: an evening the opening hours leave out, and before opening.
        others = ",".join(str(i) for i in range(7) if i != day.weekday())
        _setting(conn, "dining_days", others)
        _held(s, guest, "Shut", 2, D, f"closed on {day.strftime('%A')}s", "an evening it does not serve")
        _setting(conn, "dining_days", None)
        _room(conn, capacity=12, opening=(day + timedelta(days=1)).isoformat())
        _held(s, guest, "Early", 2, D, "does not open until", "before the opening date")
        _room(conn, capacity=12)

        # The card: published for that evening, one main, one portion counted.
        conn.execute("""INSERT INTO menus (service_date, service, title, status, formule_price,
                          source, created_at) VALUES (?, 'dinner', ?, 'published', 65, 'manual', ?)""",
                     (D, TAG + " card", _now()))
        menu_id = conn.execute("SELECT id FROM menus WHERE title = ?", (TAG + " card",)).fetchone()["id"]
        conn.execute("""INSERT INTO stock_items (name, category, unit, reorder_level, active, created_at)
                        VALUES (?, 'food', 'portions', 0, 1, ?)""", (TAG + " pigeon", _now()))
        stock = conn.execute("SELECT id FROM stock_items WHERE name = ?", (TAG + " pigeon",)).fetchone()["id"]
        conn.execute("INSERT INTO stock_movements (stock_item_id, delta, reason, note, created_at) "
                     "VALUES (?, 1, 'opening', 'test opening', ?)", (stock, _now()))
        conn.execute("""INSERT INTO menu_items (name, category, course, price, active, sold_in_pos,
                          stock_item_id, stock_qty_per_unit, created_at)
                        VALUES (?, 'main', 'main', 28, 1, 1, ?, 1, ?)""", (TAG + " pigeon", stock, _now()))
        item = conn.execute("SELECT id FROM menu_items WHERE name = ?", (TAG + " pigeon",)).fetchone()["id"]
        conn.execute("""INSERT INTO menu_dishes (menu_id, course, menu_item_id, name, carte_price,
                          in_formule, available, sort_order, created_at)
                        VALUES (?, 'main', ?, ?, 28, 1, 1, 0, ?)""", (menu_id, item, TAG + " Pigeon", _now()))
        conn.commit()
        _held(s, guest, "Card", 2, D, "card would run short", "a card that would run short")
        conn.execute("DELETE FROM menu_dishes WHERE menu_id = ?", (menu_id,))
        conn.execute("DELETE FROM menus WHERE id = ?", (menu_id,))
        conn.commit()

        # The guest: a caution on their profile, and no-shows behind them.
        conn.execute("INSERT INTO guests (name, email, caution, caution_level, created_at) "
                     "VALUES (?, ?, 'test caution', 'care', ?)", (TAG + " Careful", _email("Careful"), _now()))
        conn.commit()
        _held(s, guest, "Careful", 2, D, "Handle with care", "a guest marked handle with care")
        for n in (1, 2):
            past = (m.house_today() - timedelta(days=30 * n)).isoformat()
            conn.execute(
                """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                     guest_email, party_size, dinner_date, status, no_show_at, created_at)
                   VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?, ?)""",
                (f"{TAG}NS{n}", f"{TAG}nstok{n}", TAG + " Absent", _email("Absent"), past, _now(), _now()))
        conn.commit()
        _held(s, guest, "Absent", 2, D, "not turned up 2 times", "two no-shows before")

        s.section("The rules read here are the real ones, not a copy of them")
        # An evening already gone, through the function, because no form
        # takes one: a payment that settles days late is the way in.
        gone = {"id": None, "dinner_date": (m.house_today() - timedelta(days=1)).isoformat(),
                "party_size": 2, "guest_email": _email("late"), "guest_name": TAG + " Late"}
        with m.app.test_request_context("/"):
            why = m.restaurant_instant_holds(conn, gone)
            clear = m.restaurant_instant_holds(conn, dict(gone, dinner_date=D))
        s.check("an evening already gone is held", any("already gone" in w for w in why), detail=str(why))
        s.check("and the same table on a free evening is not", clear == [], detail=str(clear))

        s.section("Nothing to pay online: confirmed as the form is sent")
        conn.execute("DELETE FROM restaurant_bookings WHERE guest_email LIKE ?", ("%@" + DOMAIN,))
        conn.commit()
        with _Mail() as mail:
            r = guest.post("/restaurant/book", data={
                "guest_name": TAG + " Free", "guest_email": _email("Free"),
                "guest_phone": "", "dinner_date": D, "party_size": "2",
                "dietary_notes": "", "promo_code": ""}, follow_redirects=True)
        row = conn.execute("SELECT * FROM restaurant_bookings WHERE guest_email = ?",
                           (_email("Free"),)).fetchone()
        s.check("it is confirmed", row is not None and row["status"] == "confirmed",
                detail=f"{row and row['status']}; {flashes(r)[:1]}")
        s.check("with the confirmation letter alone",
                mail.keys_to(_email("Free")) == ["restaurant_confirmed"],
                detail=str(mail.keys_to(_email("Free"))))
        s.check("and the page says the table is booked",
                "Your table is booked" in r.get_data(as_text=True))
        with _Mail() as mail:
            r = guest.post("/restaurant/book", data={
                "guest_name": TAG + " Seven", "guest_email": _email("Seven2"),
                "guest_phone": "", "dinner_date": D, "party_size": "7",
                "dietary_notes": "", "promo_code": ""}, follow_redirects=True)
        row = conn.execute("SELECT * FROM restaurant_bookings WHERE guest_email = ?",
                           (_email("Seven2"),)).fetchone()
        s.check("one the house should look at stays a request here too",
                row is not None and row["status"] == "pending"
                and "requested" in visible_text(r.get_data(as_text=True)),
                detail=f"{row and row['status']}")

        s.section("The Confirm button is the same confirmation")
        _setting(conn, "restaurant_instant", None)
        _table_booked(conn, "Manual", 2, D, status="pending")
        mid = conn.execute("SELECT id FROM restaurant_bookings WHERE guest_email = ?",
                           (_email("Manual"),)).fetchone()["id"]
        with _Mail() as mail:
            r = oc.post(f"/admin/restaurant/{mid}/confirm", follow_redirects=True)
        state = conn.execute("SELECT status FROM restaurant_bookings WHERE id = ?", (mid,)).fetchone()
        s.check("it confirms", state["status"] == "confirmed", r)
        s.check("and sends the confirmation letter once",
                mail.keys_to(_email("Manual")) == ["restaurant_confirmed"],
                detail=str(mail.keys_to(_email("Manual"))))
        s.check("written down as the owner's decision",
                conn.execute("SELECT actor_user_id FROM audit_log WHERE action = "
                             "'restaurant_booking_confirmed' AND target = ? ORDER BY id DESC",
                             (TAG + "Manual" + D,)).fetchone()["actor_user_id"] == _owner["id"])
        s.check("confirming it twice is a 404, as it was",
                oc.post(f"/admin/restaurant/{mid}/confirm").status_code == 404)
        conn.execute("INSERT INTO guests (name, email, caution, caution_level, created_at) "
                     "VALUES (?, ?, 'test refusal', 'refuse', ?)", (TAG + " Barred", _email("Barred"), _now()))
        _table_booked(conn, "Barred", 2, D, status="pending")
        bid = conn.execute("SELECT id FROM restaurant_bookings WHERE guest_email = ?",
                           (_email("Barred"),)).fetchone()["id"]
        with _Mail() as mail:
            r = oc.post(f"/admin/restaurant/{bid}/confirm", follow_redirects=True)
        s.check("a standing instruction still refuses it, and says so",
                conn.execute("SELECT status FROM restaurant_bookings WHERE id = ?",
                             (bid,)).fetchone()["status"] == "pending"
                and any("standing instruction" in f for f in flashes(r))
                and not mail.sent, detail=str(flashes(r)[:1]))

        s.section("The switch: on the settings page, saved, and on the public page")
        page = oc.get("/admin/restaurant/settings").get_data(as_text=True)
        s.check("the settings page has it, and says what it does",
                'name="restaurant_instant"' in page
                and "Confirm tables automatically" in page
                and "still comes to you as a" in page)
        form = {"opening_date": "", "dinner_time": "19:30", "capacity": "12",
                "price_per_person": "50", "lead_user_id": "", "profit_share_percent": "50",
                "deposit_percent": "", "enabled": "on", "dining_mode": "open",
                "dining_reopens": ""}
        oc.post("/admin/restaurant/settings", data=dict(form, restaurant_instant="on"))
        s.check("ticked and saved, it is on", m.restaurant_instant_on(conn))
        page = oc.get("/admin/restaurant/settings").get_data(as_text=True)
        s.check("and the box stays ticked",
                'name="restaurant_instant" checked' in page)
        with _Stripe(_session("Page", 2, D)):
            on = visible_text(guest.get("/restaurant/book").get_data(as_text=True))
        s.check("the booking page now promises confirmation on payment",
                "confirmed as soon as the payment goes through" in on,
                detail="the key has to be in PUBLIC_SETTINGS for the page to read it")
        oc.post("/admin/restaurant/settings", data=form)
        s.check("unticked and saved, it is off", not m.restaurant_instant_on(conn))
        with _Stripe(_session("Page", 2, D)):
            off = visible_text(guest.get("/restaurant/book").get_data(as_text=True))
        s.check("and the page is back to describing a request",
                "not an instant confirmation" in off
                and "confirmed as soon as the payment goes through" not in off)
    finally:
        conn.execute("DELETE FROM restaurant_tables WHERE label LIKE ?", (TAG + "%",))
        if laid:
            conn.execute(f"UPDATE restaurant_tables SET active = 1 WHERE id IN "
                         f"({','.join('?' * len(laid))})", laid)
        conn.commit()
        put_settings()
        put_app()
        _cleanup(conn)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
