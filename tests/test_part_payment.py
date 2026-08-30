"""Paying some of a stay rather than all of it.

A stay gets settled in two or three goes more often than in one — a card now
and the rest nearer the time, or split between the people travelling. All-or-
nothing makes the house chase money it could already have had, and gives a
guest who wanted to pay something no way to.

WHAT IS CHECKED HERE IS THE AMOUNT THAT REACHES STRIPE, not the page. Every
other guard in this file exists because the number in the Checkout session is
the number the guest's card is charged, and there is no second chance to get it
right: too much and the house is refunding money it should not have taken, too
little and the bill is wrong afterwards.

  OVER THE BALANCE IS CLAMPED, NOT REFUSED. A guest typing 500 against a 480
  balance means "all of it". Taking 20 euros the house then has to give back is
  worse than rounding their intention down, and an error message at the moment
  somebody had decided to pay is worse than both.

  UNDER FIFTY CENTS IS REFUSED. Stripe will not take it, and a button that
  errors on the card page has told the guest nothing.

  NOTHING, OR NONSENSE, MEANS ALL OF IT. That is also what a plain GET means,
  so every link that existed before this keeps working.

SAFETY. Stripe is pinned off by the harness; the stand-in here sits above that
on stripe_enabled and Session.create, so anything this file forgets to stand in
for falls through to the refusal rather than reaching the real account. The
last section checks the block is still in place afterwards.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPART"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
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
    clients()
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
        """What the last Checkout session would actually charge."""
        if not asked:
            return None
        return asked[-1]["line_items"][0]["price_data"]["unit_amount"]

    try:
        m.stripe_enabled = lambda: True
        m.stripe.checkout.Session = _Stub

        s.section("The balance is a real number to begin with")
        s.check("the stay owes something", owed > 1, detail=f"{owed}")

        s.section("A plain link still means all of it")
        # Every link that existed before this change is a GET.
        r = anon.get(f"/book/pay/{stay['manage_token']}")
        s.check("it goes to Stripe", r.status_code in (302, 303),
                detail=f"HTTP {r.status_code}")
        s.check("for the whole balance", cents() == int(round(owed * 100)),
                detail=f"{cents()} vs {int(round(owed * 100))}")

        s.section("An amount charges that amount")
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "100"})
        s.check("one hundred euros, not nine hundred", cents() == 10000,
                detail=f"{cents()}")
        s.check("and it is labelled as a part payment",
                "part payment" in asked[-1]["line_items"][0]["price_data"]
                                       ["product_data"]["name"],
                detail=f"{asked[-1]['line_items'][0]['price_data']['product_data']['name']!r}")
        s.check("with the booking still named on it",
                stay["reference_code"] in
                asked[-1]["line_items"][0]["price_data"]["product_data"]["name"])
        s.check("and it is still recorded as a room balance, not a new booking",
                asked[-1]["metadata"]["kind"] == "room_balance",
                detail=f"{asked[-1]['metadata']}")

        s.section("A comma is a decimal point to most of Europe")
        asked.clear()
        anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "12,50"})
        s.check("12,50 is twelve euros fifty", cents() == 1250, detail=f"{cents()}")

        s.section("More than the balance is clamped, not refused")
        # Taking money the house has to give back is worse than rounding
        # somebody's intention down to what they actually owe.
        asked.clear()
        r = anon.post(f"/book/pay/{stay['manage_token']}",
                      data={"amount": f"{owed + 200:.2f}"})
        s.check("they still reach the card page", r.status_code in (302, 303),
                detail=f"HTTP {r.status_code}")
        s.check("charged the balance, not what they typed",
                cents() == int(round(owed * 100)), detail=f"{cents()}")
        s.check("and not labelled a part payment, because it is not one",
                "part payment" not in asked[-1]["line_items"][0]["price_data"]
                                          ["product_data"]["name"])

        s.section("Nothing, or nonsense, means all of it")
        for label, data in (("an empty box", {"amount": ""}),
                            ("letters", {"amount": "a lot"}),
                            ("zero", {"amount": "0"}),
                            ("a negative", {"amount": "-50"})):
            asked.clear()
            anon.post(f"/book/pay/{stay['manage_token']}", data=data)
            s.check(f"{label}: charges the whole balance",
                    cents() == int(round(owed * 100)), detail=f"{cents()}")

        s.section("Less than Stripe will take is refused before the card page")
        asked.clear()
        r = anon.post(f"/book/pay/{stay['manage_token']}", data={"amount": "0.20"},
                      follow_redirects=True)
        s.check("no session is created", not asked, detail=f"{len(asked)}")
        s.check("and the guest is not dropped on an error",
                r.status_code == 200, detail=f"HTTP {r.status_code}")

        s.section("A stay with nothing owing takes no money")
        settled = _stay("B")
        conn = db()
        conn.execute("UPDATE bookings SET amount_paid = ? WHERE id = ?",
                     (m.booking_bill(conn, settled["id"])["total"], settled["id"]))
        conn.commit()
        conn.close()
        asked.clear()
        anon.post(f"/book/pay/{settled['manage_token']}", data={"amount": "50"})
        s.check("nothing is created", not asked, detail=f"{len(asked)}")

        s.section("Nor does a cancelled one")
        cancelled = _stay("C", status="cancelled")
        asked.clear()
        anon.post(f"/book/pay/{cancelled['manage_token']}", data={"amount": "50"})
        s.check("nothing is created", not asked, detail=f"{len(asked)}")

        s.section("A token nobody holds is a 404")
        s.check("404", anon.post("/book/pay/not-a-token",
                                 data={"amount": "10"}).status_code == 404)
    finally:
        m.stripe_enabled = was_enabled
        m.stripe.checkout.Session = was_session

    s.section("And the guest is offered it on their own page")
    with_stripe = m.stripe_enabled
    try:
        m.stripe_enabled = lambda: True
        page = anon.get(f"/book/manage/{stay['manage_token']}")
        html = page.get_data(as_text=True) if page.status_code == 200 else ""
        if not html:
            page = anon.get(f"/booking/{stay['manage_token']}")
            html = page.get_data(as_text=True)
        s.check("the manage page opens", page.status_code == 200,
                detail=f"HTTP {page.status_code}")
        s.check("the whole-balance button is still there",
                f"Pay €{owed:.2f}" in html or "Pay €" in html,
                detail="the common case must stay one click")
        s.check("and paying part of it is offered", 'name="amount"' in html,
                detail="the feature exists and nobody can reach it")
    finally:
        m.stripe_enabled = with_stripe

    s.section("And Stripe is still switched off for whoever runs next")
    s.check("stripe_enabled is the harness's answer again", not m.stripe_enabled())
    s.check("the api key is still blank", not getattr(m.stripe, "api_key", None))
    s.check("Session.create is the library's own again",
            m.stripe.checkout.Session is not _Stub)

    _cleanup()
    return s
