"""Money going back out of the house.

`issue_refund` is the most carefully built function in this app — a refund
ceiling, partial refunds, a Stripe idempotency key, a guard against
double-counting the workshop ledger, and a comment stating that
"double-counting is the bug this whole area keeps producing". None of it was
exercised by a single check. The one test that mentioned refunds inserted a
row into `refunds` directly and never called the engine at all.

An untested refund path is the worst kind of untested code here: everything
else in the app can be corrected by editing a row, and this one moves real
money to a stranger's card.

Stripe is stood in for throughout. The harness strips the keys out of the
environment and blanks `STRIPE_SECRET_KEY`, so the card path cannot reach the
network even if a stub were forgotten; where a card refund has to be
exercised the fake is swapped in and put back in a `finally`.

The checks worth the most are the ones about what is NOT written. A refund
that fails at the payment provider and still lands a row in `refunds` is
money the books say went back and the guest never received — and nothing
downstream can tell, because a recorded refund looks exactly like a real one.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTREF"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_transactions WHERE workshop_booking_id IN
                    (SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)""",
                 (TAG + "%",))
    for t in ("bookings", "restaurant_bookings", "workshop_bookings"):
        conn.execute(f"DELETE FROM {t} WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _room_booking(ref, total=400, status="paid", intent=None):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           payment_status, total_price, stripe_payment_intent_id, created_at)
           VALUES (?, ?, ?, 'Refund Guest', 'refund@example.invalid',
           '2026-09-01', '2026-09-03', 2, 'confirmed', ?, ?, ?, ?)""",
        (room, TAG + ref, TAG + "tok" + ref, status, total, intent,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return _get("bookings", ref)


def _restaurant_booking(ref, total=200, deposit=None):
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, dinner_date, party_size, status, payment_status,
           total_price, deposit_amount, created_at)
           VALUES (?, ?, 'Diner', 'diner@example.invalid', '2026-09-04', 4,
           'confirmed', 'paid', ?, ?, ?)""",
        (TAG + ref, TAG + "rtok" + ref, total, deposit,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return _get("restaurant_bookings", ref)


def _workshop_booking(ref, total=300, paid=None):
    conn = db()
    ses = conn.execute("SELECT id FROM workshop_sessions LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, 'Maker', 'maker@example.invalid', 1, 'confirmed', ?, ?)""",
        (ses["id"] if ses else None, TAG + ref, TAG + "wtok" + ref, total,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", paid, method="stripe")
        conn.commit()
    conn.close()
    return _get("workshop_bookings", ref)


def _get(table, ref):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE reference_code = ?",
                            (TAG + ref,)).fetchone()
    finally:
        conn.close()


def _refund_rows(ref):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM refunds WHERE reference_code = ? ORDER BY id",
            (TAG + ref,)).fetchall()
    finally:
        conn.close()


def _issue(category, booking, amount, reason, method="other"):
    """Call the engine on its own connection, the way a request would."""
    conn = db()
    try:
        return m.issue_refund(conn, category, booking, amount, reason, method=method)
    finally:
        conn.commit()
        conn.close()


def _ceiling(category, booking):
    conn = db()
    try:
        return m.refundable_amount(conn, category, booking)
    finally:
        conn.close()


class _FakeStripe:
    """Stands in for the module. Idempotent the way the real one is: the same
    idempotency key returns the same refund object rather than a second one."""

    def __init__(self):
        self.keys = []
        self._issued = {}

    class _Refund:
        pass

    @property
    def Refund(self):
        outer = self

        class R:
            @staticmethod
            def create(**kw):
                key = kw.get("idempotency_key")
                outer.keys.append(key)
                if key not in outer._issued:
                    obj = type("StripeRefund", (), {})()
                    obj.id = f"re_fake_{len(outer._issued) + 1}"
                    outer._issued[key] = obj
                return outer._issued[key]
        return R


def _with_stripe(fake, fn):
    """Swap Stripe out, run, and always put the real module back."""
    real_stripe, real_key = m.stripe, m.STRIPE_SECRET_KEY
    m.stripe, m.STRIPE_SECRET_KEY = fake, "sk_test_stand_in"
    try:
        return fn()
    finally:
        m.stripe, m.STRIPE_SECRET_KEY = real_stripe, real_key


def run():
    s = Suite("Refunds")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("A refund nobody can explain is not recorded")
    b = _room_booking("A")
    ok, err = _issue("room", b, 100, "")
    s.check("a refund with no reason is refused", ok is False and "reason" in err.lower(),
            detail=str(err))
    ok, err = _issue("room", b, 100, "   ")
    s.check("and whitespace is not a reason", ok is False, detail=str(err))

    s.section("The amount has to be a real amount")
    s.check("zero is refused", _issue("room", b, 0, "why")[0] is False)
    s.check("a negative amount is refused", _issue("room", b, -50, "why")[0] is False)
    s.check("text in the amount box is refused",
            _issue("room", b, "four hundred", "why")[0] is False)
    s.check("none of those wrote a row", len(_refund_rows("A")) == 0,
            detail=f"{len(_refund_rows('A'))} row(s) written by refused refunds")

    s.section("Nothing is refunded that was never paid")
    unpaid = _room_booking("B", status="unpaid")
    ok, err = _issue("room", unpaid, 50, "changed their mind")
    s.check("an unpaid booking cannot be refunded", ok is False, detail=str(err))
    s.check("and the reason says so plainly", "paid" in (err or "").lower(),
            detail=str(err))
    s.check("its status is untouched",
            _get("bookings", "B")["payment_status"] == "unpaid")

    s.section("Part of it back, then the rest")
    # The whole reason the function was rewritten: "give them half back because
    # they cancelled late" used to be impossible.
    s.check("the full price is refundable to start with", _ceiling("room", b) == 400.0,
            detail=str(_ceiling("room", b)))
    ok, err = _issue("room", b, 100, "late cancellation")
    s.check("a partial refund is accepted", ok is True, detail=str(err))
    s.check("the ceiling drops by what went back", _ceiling("room", b) == 300.0,
            detail=str(_ceiling("room", b)))
    s.check("a partially refunded booking is still 'paid', not 'refunded'",
            _get("bookings", "A")["payment_status"] == "paid",
            detail=_get("bookings", "A")["payment_status"]
                   + " — flipping early hides an outstanding balance")

    s.section("More than was taken never goes back")
    ok, err = _issue("room", b, 400, "all of it")
    s.check("over-refunding is refused", ok is False, detail=str(err))
    s.check("and it says what is actually left", "300" in (err or ""), detail=str(err))
    s.check("still only the one row", len(_refund_rows("A")) == 1,
            detail=f"{len(_refund_rows('A'))} rows")

    ok, err = _issue("room", b, 300, "the rest")
    s.check("refunding exactly what is left is allowed", ok is True, detail=str(err))
    s.check("now it is marked refunded",
            _get("bookings", "A")["payment_status"] == "refunded",
            detail=_get("bookings", "A")["payment_status"])
    s.check("and nothing further can be taken back",
            _issue("room", b, 1, "one more")[0] is False)
    s.check("the two refunds add up to what was paid",
            sum(r["amount"] for r in _refund_rows("A")) == 400.0,
            detail=str(sum(r["amount"] for r in _refund_rows("A"))))

    s.section("A card refund that cannot be issued is not recorded as issued")
    # The most expensive failure available here. A row in `refunds` is
    # indistinguishable from money that actually moved, so writing one when
    # the provider refused means the books say the guest was paid back and
    # nobody downstream can tell they were not.
    card = _room_booking("C", intent="pi_test_never_used")
    ok, err = _issue("room", card, 100, "card refund", method="stripe")
    s.check("with Stripe unconfigured, a card refund is refused", ok is False,
            detail=str(err))
    s.check("it says why rather than falling back to a manual record",
            "stripe" in (err or "").lower(), detail=str(err))
    s.check("and the books show no refund at all", len(_refund_rows("C")) == 0,
            detail=f"{len(_refund_rows('C'))} phantom refund(s) recorded")

    s.section("A card refund that does go through")
    fake = _FakeStripe()
    ok, err = _with_stripe(fake, lambda: _issue("room", card, 100, "card refund",
                                                method="stripe"))
    s.check("it is accepted", ok is True, detail=str(err))
    s.check("Stripe was actually called once", len(fake.keys) == 1,
            detail=str(fake.keys))
    rows = _refund_rows("C")
    s.check("the provider's refund id is kept against the row",
            len(rows) == 1 and rows[0]["stripe_refund_id"] == "re_fake_1",
            detail=str([r["stripe_refund_id"] for r in rows]))
    s.check("the amount reached Stripe in cents, not euros",
            fake.keys and "10000" in fake.keys[0], detail=str(fake.keys))

    # Two legitimate refunds of the SAME amount must not collide at Stripe. If
    # the idempotency key were built from the booking and amount alone, the
    # second €100 would return the first refund and the guest would be €100
    # short with both showing as issued.
    ok2, err2 = _with_stripe(fake, lambda: _issue("room", card, 100, "second instalment",
                                                 method="stripe"))
    s.check("a second refund of the same amount is accepted", ok2 is True,
            detail=str(err2))
    s.check("and gets a different idempotency key from the first",
            len(set(fake.keys)) == 2, detail=str(fake.keys))
    s.check("so Stripe issues a second refund rather than replaying the first",
            {r["stripe_refund_id"] for r in _refund_rows("C")} == {"re_fake_1", "re_fake_2"},
            detail=str([r["stripe_refund_id"] for r in _refund_rows("C")]))

    s.section("Two people refunding at once")
    # Both read the booking, then both submit. The ceiling is re-read inside
    # issue_refund rather than trusted from the caller's copy, which is what
    # stops the second one going out.
    race = _room_booking("D", total=200)
    stale = _get("bookings", "D")           # a copy taken before anything happened
    s.check("the first refund succeeds", _issue("room", race, 200, "cancelled")[0] is True)
    ok, err = _issue("room", stale, 200, "cancelled")
    s.check("the second, working from a stale copy, is refused", ok is False,
            detail=str(err))
    s.check("and only one refund exists", len(_refund_rows("D")) == 1,
            detail=f"{len(_refund_rows('D'))} rows for one cancellation")

    s.section("The restaurant took a deposit, not the whole bill")
    # amount_paid_for is per-category on purpose: refunding the full sitting
    # price when only a deposit was taken hands back money never received.
    dep = _restaurant_booking("E", total=200, deposit=50)
    s.check("only the deposit is refundable", _ceiling("restaurant", dep) == 50.0,
            detail=str(_ceiling("restaurant", dep)))
    s.check("the full bill is refused",
            _issue("restaurant", dep, 200, "no show")[0] is False)
    s.check("the deposit itself goes back",
            _issue("restaurant", dep, 50, "no show")[0] is True)
    s.check("and the reservation reads as refunded",
            _get("restaurant_bookings", "E")["payment_status"] == "refunded",
            detail=_get("restaurant_bookings", "E")["payment_status"])

    s.section("Workshops keep their money in a ledger")
    ws = _workshop_booking("F", total=300, paid=300)
    s.check("what was actually paid is refundable", _ceiling("workshop", ws) == 300.0,
            detail=str(_ceiling("workshop", ws)))
    # A workshop has no single payment intent -- deposit and balance are taken
    # as two separate Stripe sessions -- so a card refund genuinely cannot be
    # issued from here, and saying so is better than recording one that moved
    # no money.
    ok, err = _with_stripe(_FakeStripe(),
                           lambda: _issue("workshop", ws, 100, "cancelled", method="stripe"))
    s.check("a card refund is refused for want of a single payment", ok is False,
            detail=str(err))
    s.check("and it says where to do it instead", "dashboard" in (err or "").lower(),
            detail=str(err))

    ok, err = _issue("workshop", ws, 100, "cancelled")
    s.check("a recorded refund is accepted", ok is True, detail=str(err))
    s.check("it is written into the workshop ledger too",
            any(t["kind"] == "refund" for t in _ws_ledger(ws["id"])),
            detail=str([t["kind"] for t in _ws_ledger(ws["id"])]))

    # The bug the code comments call out twice: the refund lands in BOTH the
    # refunds table and the workshop ledger, and counting both halves it again.
    s.check("the ledger copy is not counted a second time",
            _ceiling("workshop", ws) == 200.0,
            detail=f"{_ceiling('workshop', ws)} — 100.00 means the refund was "
                   "subtracted twice")
    # ...and the ceiling must not shrink on its own as refunds accumulate,
    # which is the failure amount_paid_for's docstring describes: a guest who
    # could never be made whole.
    s.check("a second refund can still use the whole remainder",
            _issue("workshop", ws, 200, "the rest")[0] is True)
    s.check("which returns everything that was paid",
            _ceiling("workshop", ws) == 0.0, detail=str(_ceiling("workshop", ws)))

    s.section("A refund entered straight into the ledger still counts")
    # Somebody records a refund on the workshop's own money page rather than
    # through this engine. It has to reduce what is left, or the same money
    # goes back twice.
    ws2 = _workshop_booking("G", total=200, paid=200)
    conn = db()
    m.add_workshop_transaction(conn, ws2["id"], "refund", "Bank transfer back", 80,
                               method="bank_transfer")
    conn.commit()
    conn.close()
    s.check("a hand-entered ledger refund lowers the ceiling",
            _ceiling("workshop", ws2) == 120.0, detail=str(_ceiling("workshop", ws2)))

    s.section("The pages behind it")
    live = _room_booking("H", total=250)
    r = oc.post(f"/admin/bookings/{live['id']}/refund",
                data={"reason": "cancelled by the house", "method": "other"},
                follow_redirects=True)
    said = " ".join(flashes(r)).lower()
    s.check("leaving the amount blank refunds all of it",
            len(_refund_rows("H")) == 1 and _refund_rows("H")[0]["amount"] == 250.0,
            detail=str([r2["amount"] for r2 in _refund_rows("H")]))
    s.check("and the page says what went back", "250" in said, detail=said)
    s.check("the booking is marked refunded",
            _get("bookings", "H")["payment_status"] == "refunded")

    conn = db()
    audit = conn.execute(
        """SELECT COUNT(*) c FROM audit_log WHERE action = 'refund_issued'
           AND target LIKE ?""", ("%" + TAG + "H%",)).fetchone()["c"]
    conn.close()
    s.check("it is written to the audit log", audit == 1, detail=str(audit))

    # A refused refund must not be reported as a success on the way out.
    r = oc.post(f"/admin/bookings/{live['id']}/refund",
                data={"amount": "50", "reason": "again", "method": "other"},
                follow_redirects=True)
    s.check("a refused refund says so on the page",
            "failed" in " ".join(flashes(r)).lower(), detail=str(flashes(r)))
    s.check("and still only one refund exists", len(_refund_rows("H")) == 1)

    s.check("a booking that does not exist is a 404",
            oc.post("/admin/bookings/99999999/refund",
                    data={"reason": "x"}).status_code == 404)

    s.section("Only the owner gives money back")
    guard = _room_booking("I", total=100)
    ws3 = _workshop_booking("J", total=100, paid=100)
    rest = _restaurant_booking("K", total=100, deposit=100)
    # Each of these asserts the MONEY, not the status code. All three routes
    # redirect on success as well as on refusal, so `status_code in (302, 403)`
    # passes just as happily when the employee's refund went through — a check
    # that cannot fail is worse than no check.
    body = {"amount": "100", "reason": "x", "method": "other"}
    ec.post(f"/admin/bookings/{guard['id']}/refund", data=body)
    s.check("an employee cannot refund a stay", not _refund_rows("I"),
            detail=f"{len(_refund_rows('I'))} refund(s) issued by an employee")
    ec.post(f"/admin/workshops/registrations/{ws3['id']}/refund", data=body)
    s.check("nor a workshop place", not _refund_rows("J"),
            detail=f"{len(_refund_rows('J'))} refund(s) issued by an employee")
    ec.post(f"/admin/restaurant/{rest['id']}/refund", data=body)
    s.check("nor a table", not _refund_rows("K"),
            detail=f"{len(_refund_rows('K'))} refund(s) issued by an employee")
    s.check("and they are sent away rather than shown the page",
            ec.post(f"/admin/bookings/{guard['id']}/refund", data=body
                    ).status_code in (302, 403))

    # ...and the owner CAN, so the guard above is proving a permission rather
    # than a broken route.
    s.check("while the owner can",
            oc.post(f"/admin/workshops/registrations/{ws3['id']}/refund",
                    data={"amount": "100", "reason": "cancelled", "method": "other"},
                    follow_redirects=True) is not None
            and len(_refund_rows("J")) == 1,
            detail=f"{len(_refund_rows('J'))} rows")

    _cleanup()
    return s


def _ws_ledger(booking_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM workshop_transactions WHERE workshop_booking_id = ?",
            (booking_id,)).fetchall()
    finally:
        conn.close()


if __name__ == "__main__":
    print(run().report())
