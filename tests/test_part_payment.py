"""Who may pay part of a stay, and who may not.

A nightly stay is quoted as a price and paid as one. A guest is offered the
whole balance and nothing else, because letting somebody send forty euros
against a weekend turns every arrival into a conversation about what is left.

Workshops are the other way round on purpose, and already were: a place is held
with a deposit and settled in instalments up to the session, because booking an
atelier eight months out is a different commitment from taking a room for the
weekend. That path is untouched here and checked at the end.

So part payment on a stay belongs in two places and not a third:

  THE HOUSE, ALWAYS. Cash at the desk, a bank transfer, a card taken in person.
  This did not exist at all — both callers of record_booking_payment were
  Stripe paths, so a guest who handed over two hundred euros could not be
  credited for it, the stay went on showing the whole balance, and the reminder
  job went on asking them for money they had already paid.

  THE GUEST, ONLY IF THE HOUSE SAYS SO. Off by default, and the switch is
  checked in the ROUTE rather than only in the template — a hidden form is not
  a rule, and a hand-made POST must not get round it.

The two differ deliberately on an amount over the balance. On the guest's own
button it is clamped: they typed 500 against 480 and meant "all of it", and
taking twenty euros the house must give back is worse. Typed at the desk it is
refused, because there it is far more likely to be a slip, and quietly taking
less than the number in front of you is how a till and a bill start disagreeing.

SAFETY. Stripe is pinned off by the harness; the stand-in here sits above that,
so anything this file forgets to stand in for falls through to the refusal
rather than reaching the real account.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZPART"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key = 'room_part_payment_allowed'")
    conn.commit()
    conn.close()


def _allow(on):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('room_part_payment_allowed', ?)",
        ("1" if on else "0",))
    conn.commit()
    conn.close()


def _stay(ref, paid=0.0, status="confirmed"):
    conn = db()
    room = _harness.ensure_room()
    arrival = date.today() + timedelta(days=30)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'zzpart@example.invalid', '', ?, ?, 2, ?, 900, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         arrival.isoformat(), (arrival + timedelta(days=3)).isoformat(), status,
         paid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _owed(booking_id):
    conn = db()
    try:
        return m.booking_bill(conn, booking_id)["owed"]
    finally:
        conn.close()


def run():
    s = Suite("Part payment")
    _cleanup()
    _allow(False)
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    stay = _stay("A")
    owed = _owed(stay["id"])

    asked = []
    was_enabled = m.stripe_enabled
    was_session = m.stripe.checkout.Session

    class _Stub:
        @staticmethod
        def create(**kwargs):
            asked.append(kwargs)
            return type("S", (), {"url": "https://stripe.invalid/checkout"})()

    def cents():
        if not asked:
            return None
        return asked[-1]["line_items"][0]["price_data"]["unit_amount"]

    def full():
        return int(round(owed * 100))

    try:
        m.stripe_enabled = lambda: True
        m.stripe.checkout.Session = _Stub

        s.check("the stay owes something to begin with", owed > 1, detail=f"{owed}")

        s.section("On a house that has never touched the setting")
        # _allow(False) writes an explicit row, so it exercises the code and not
        # the SHIPPED DEFAULT. With no row at all — which is every new install —
        # the answer has to be the same one, and changing the default in
        # ROOM_PAYMENT_DEFAULTS has to be what changes it.
        conn = db()
        conn.execute("DELETE FROM app_settings WHERE key = 'room_part_payment_allowed'")
        conn.commit()
        conn.close()
        s.check("the shipped default is off",
                m.ROOM_PAYMENT_DEFAULTS["room_part_payment_allowed"] == "0",
                detail=f"{m.ROOM_PAYMENT_DEFAULTS['room_part_payment_allowed']!r}")
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "40"})
        s.check("and with no row at all, an amount is still ignored",
                cents() == full(), detail=f"{cents()}")
        _allow(False)

        s.section("A guest is offered the whole balance and nothing else")
        r = anon.get(f"/book/pay/{stay['manage_token']}")
        s.check("the link goes to Stripe", r.status_code in (302, 303),
                detail=f"HTTP {r.status_code}")
        s.check("for the whole balance", cents() == full(), detail=f"{cents()}")
        s.check("and it is not called a part payment",
                "part payment" not in asked[-1]["line_items"][0]["price_data"]
                                           ["product_data"]["name"])

        s.section("An amount posted by hand does not get round that")
        # The switch is checked in the route, not only in the template. A form
        # that is merely hidden is not a rule.
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "40"})
        s.check("forty euros is ignored", cents() == full(),
                detail=f"{cents()} — a hidden field is not a guard")

        s.section("The box is not on the guest's page either")
        page = anon.get(f"/book/manage/{stay['manage_token']}")
        html = page.get_data(as_text=True) if page.status_code == 200 else ""
        if not html:
            html = anon.get(f"/booking/{stay['manage_token']}").get_data(as_text=True)
        s.check("no amount box", 'name="amount"' not in html,
                detail="offered something the route will refuse")
        s.check("but the whole-balance button is still there", "Pay €" in html,
                detail="the common case must stay one click")

        s.section("Unless the house turns it on")
        _allow(True)
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "100"})
        s.check("then one hundred euros is one hundred", cents() == 10000,
                detail=f"{cents()}")
        s.check("labelled as a part payment",
                "part payment" in asked[-1]["line_items"][0]["price_data"]
                                       ["product_data"]["name"])
        s.check("still recorded as a room balance, not a new booking",
                asked[-1]["metadata"]["kind"] == "room_balance")
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "12,50"})
        s.check("and a comma is a decimal point", cents() == 1250, detail=f"{cents()}")
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": f"{owed + 200:.2f}"})
        s.check("over the balance is clamped, because they meant all of it",
                cents() == full(), detail=f"{cents()}")
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "0.20"})
        s.check("under what Stripe will take is refused before the card page",
                not asked, detail=f"{len(asked)}")
        html = anon.get(f"/book/manage/{stay['manage_token']}").get_data(as_text=True)
        s.check("and now the box is on the page", 'name="amount"' in html)
        _allow(False)

        s.section("A settled or cancelled stay takes nothing either way")
        settled = _stay("B")
        conn = db()
        conn.execute("UPDATE bookings SET amount_paid = ? WHERE id = ?",
                     (m.booking_bill(conn, settled["id"])["total"], settled["id"]))
        conn.commit()
        conn.close()
        asked.clear()
        anon.get(f"/book/pay/{settled['manage_token']}")
        s.check("nothing for a settled stay", not asked, detail=f"{len(asked)}")
        cancelled = _stay("C", status="cancelled")
        anon.get(f"/book/pay/{cancelled['manage_token']}")
        s.check("nor a cancelled one", not asked, detail=f"{len(asked)}")
        s.check("a token nobody holds is a 404",
                anon.get("/book/pay/not-a-token").status_code == 404)
    finally:
        m.stripe_enabled = was_enabled
        m.stripe.checkout.Session = was_session

    s.section("The house can take part of it at the desk, and could not before")
    # Cash, a transfer, a card in person. Both callers of record_booking_payment
    # were Stripe paths, so none of that could be recorded at all.
    desk = _stay("D")
    before = _owed(desk["id"])
    r = oc.post(f"/management/outstanding/{desk['id']}/payment",
                data={"amount": "200", "method": "bank_transfer",
                      "reference": "ZZ cash at the desk"},
                follow_redirects=True)
    s.check("it is accepted", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and comes off what they owe",
            abs(_owed(desk["id"]) - (before - 200)) < 0.01,
            detail=f"{before} -> {_owed(desk['id'])}")
    s.check("the owner is told what is left",
            any("still to pay" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")
    conn = db()
    row = conn.execute(
        """SELECT * FROM booking_payments WHERE booking_id = ? ORDER BY id DESC LIMIT 1""",
        (desk["id"],)).fetchone()
    conn.close()
    s.check("as a line of its own, not just a bigger number on the stay",
            bool(row), detail="nothing in booking_payments — three months on, "
                              "a bank statement cannot be matched to a booking")
    if row:
        s.check("for the amount taken", abs(row["amount"] - 200) < 0.01,
                detail=f"{row['amount']}")
        s.check("with how it was taken", row["method"] == "bank_transfer",
                detail=f"{row['method']!r}")
        s.check("the reference a bank line can be matched to",
                "ZZ cash" in (row["reference"] or ""), detail=f"{row['reference']!r}")
        s.check("and who took it", row["taken_by_user_id"] == owner["id"],
                detail=f"{row['taken_by_user_id']}")
    conn = db()
    stashed = conn.execute("SELECT stripe_payment_intent_id FROM bookings WHERE id = ?",
                           (desk["id"],)).fetchone()
    conn.close()
    s.check("and a cash reference is NOT filed as a Stripe payment intent",
            not (stashed["stripe_payment_intent_id"] or ""),
            detail=f"{stashed['stripe_payment_intent_id']!r} — that column means "
                   "something specific and this is not it")

    s.section("Finishing it off says so rather than saying a number")
    left = _owed(desk["id"])
    r = oc.post(f"/management/outstanding/{desk['id']}/payment",
                data={"amount": f"{left:.2f}"}, follow_redirects=True)
    s.check("the stay is settled", _owed(desk["id"]) <= 0.005,
            detail=f"{_owed(desk['id'])}")
    s.check("and it says settled", any("settled" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("More than is owed is REFUSED at the desk, not clamped")
    # The opposite of the guest's button, deliberately. A guest typing over the
    # balance meant all of it; a number typed here is far more likely a slip,
    # and quietly taking less is how a till and a bill start disagreeing.
    over = _stay("E")
    owed_e = _owed(over["id"])
    r = oc.post(f"/management/outstanding/{over['id']}/payment",
                data={"amount": f"{owed_e + 50:.2f}"}, follow_redirects=True)
    s.check("nothing is recorded", abs(_owed(over["id"]) - owed_e) < 0.01,
            detail=f"{_owed(over['id'])} vs {owed_e}")
    s.check("and it says what the figure actually is",
            any("outstanding" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("Nothing, or nonsense, records nothing")
    for label, data in (("blank", {"amount": ""}), ("letters", {"amount": "some"}),
                        ("zero", {"amount": "0"}), ("a negative", {"amount": "-100"})):
        before = _owed(over["id"])
        oc.post(f"/management/outstanding/{over['id']}/payment", data=data,
                follow_redirects=True)
        s.check(f"{label}: the balance does not move",
                abs(_owed(over["id"]) - before) < 0.01, detail=f"{_owed(over['id'])}")

    s.section("Guards")
    before = _owed(over["id"])
    code = ec.post(f"/management/outstanding/{over['id']}/payment",
                   data={"amount": "50"}).status_code
    s.check("an employee cannot record one", code in (302, 403), detail=f"HTTP {code}")
    s.check("and nothing moved", abs(_owed(over["id"]) - before) < 0.01)
    s.check("a booking that does not exist is a 404",
            oc.post("/management/outstanding/999999/payment",
                    data={"amount": "50"}).status_code == 404)

    s.section("Workshops are untouched, and still settle in instalments")
    # The other side of the rule: an atelier held with a deposit and paid off
    # up to the session is a different commitment, and that page still offers it.
    conn = db()
    sid = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY start_date DESC LIMIT 1").fetchone()
    if sid:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
               party_size, status, reference_code, manage_token, created_at,
               total_price, deposit_amount, balance_amount, deposit_paid_at)
               VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, 1000, 300, 700, ?)""",
            (sid["id"], f"{TAG} atelier", "zzpart.ws@example.invalid",
             f"{TAG}-WS", f"tok{TAG}ws", now, now))
        conn.commit()
    reg = conn.execute(
        "SELECT manage_token FROM workshop_bookings WHERE reference_code = ?",
        (f"{TAG}-WS",)).fetchone()
    conn.close()
    # Asserted rather than skipped. Written as `if reg:` first, which meant the
    # section quietly ran nothing whenever the database had no registration —
    # the one shape of check that can never fail.
    s.check("there is a registration to look at", bool(reg),
            detail="nothing to check against, so the claim below is untested")
    if reg:
        # Stripe on for this, exactly as for the room page above: with it off
        # the workshop page shows no payment controls at all, and the check
        # would fail for a reason that has nothing to do with instalments.
        was = m.stripe_enabled
        try:
            m.stripe_enabled = lambda: True
            html = anon.get(f"/workshops/manage/{reg['manage_token']}").get_data(as_text=True)
        finally:
            m.stripe_enabled = was
        s.check("a workshop guest is still offered an amount",
                'name="amount"' in html,
                detail="the workshop instalment path was collateral damage")
        s.check("and told what the smallest instalment is",
                "upwards" in html or "part" in html.lower(),
                detail="the box is there with no rule stated")

    s.section("And Stripe is still switched off for whoever runs next")
    s.check("stripe_enabled is the harness's answer again", not m.stripe_enabled())
    s.check("the api key is still blank", not getattr(m.stripe, "api_key", None))
    s.check("Session.create is the library's own again",
            m.stripe.checkout.Session is not _Stub)

    _cleanup()
    return s
