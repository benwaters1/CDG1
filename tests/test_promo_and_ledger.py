"""Discount codes, the blast that sends one out, and the workshop ledger.

The last of the routes that move a figure. Three things here are worth more
than the rest:

A percent discount over 100 would pay the guest to come. `_parse_promo_form`
refuses it, and that refusal is the only thing between a mistyped 150 and a
negative bill — compute_promo_discount clamps at the subtotal, so the loss
stops at "the stay is free" rather than going further, but free is already the
whole booking.

A blast substitutes {guest_name} and {promo_code} into the body. An
unsubstituted placeholder is a guest receiving a message that says
"{promo_code}", which is the kind of thing a château does not get to do twice.

And the blast is the third path that has to respect the do-not-write list. The
first two — the mailing list and the campaign sender — are checked elsewhere.
This one is built from confirmed bookings rather than from subscribers, so it
reaches people who never subscribed to anything, and its own comment records
that the opt-out join was missing from it entirely at one point: a guest could
unsubscribe and still be sent the next code.

The ledger half is smaller and simpler: a hand-entered charge, discount,
payment or refund has to move the balance by exactly what it says, and nothing
else may be written by a kind the app does not recognise.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZTBLAST"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM promo_code_redemptions WHERE promo_code_id IN
                    (SELECT id FROM promo_codes WHERE code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.execute("""DELETE FROM workshop_transactions WHERE workshop_booking_id IN
                    (SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _promo_row(code):
    conn = db()
    try:
        return conn.execute("SELECT * FROM promo_codes WHERE code = ?",
                            (TAG + code,)).fetchone()
    finally:
        conn.close()


def _stayed(who, offset_days=-8):
    """A confirmed past room guest, so the blast has somebody to reach."""
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=offset_days)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           payment_status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 'paid', 400, ?)""",
        (room, TAG + who, TAG + "tok" + who, TAG + " " + who,
         f"{TAG.lower()}{who.lower()}@example.invalid", arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return f"{TAG.lower()}{who.lower()}@example.invalid"


def _workshop(ref, total=500):
    conn = db()
    ses = conn.execute(
        """SELECT ws.id AS sid FROM workshop_sessions ws
           JOIN workshops w ON w.id = ws.workshop_id LIMIT 1""").fetchone()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, 'Ledger Guest', 'ledger@example.invalid', 1,
           'confirmed', ?, ?)""",
        (ses["sid"], TAG + ref, TAG + "wtok" + ref, total,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _ledger(booking_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM workshop_transactions WHERE workshop_booking_id = ? ORDER BY id",
            (booking_id,)).fetchall()
    finally:
        conn.close()


def _held(address):
    conn = db()
    try:
        return conn.execute("SELECT * FROM email_outbox WHERE to_address = ?",
                            (address,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Codes, blasts and the ledger")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("A discount that would pay the guest")
    r = oc.post("/admin/promo-codes/new", data={
        "code": TAG + "TOOMUCH", "discount_type": "percent", "discount_value": "150",
    }, follow_redirects=True)
    s.check("a percent discount over 100 is refused", _promo_row("TOOMUCH") is None)
    s.check("and says so rather than erroring", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("naming the actual limit",
            any("100" in f for f in flashes(r)), detail=str(flashes(r)))

    r = oc.post("/admin/promo-codes/new", data={
        "code": TAG + "ZERO", "discount_type": "percent", "discount_value": "0",
    }, follow_redirects=True)
    s.check("a discount of nothing is refused", _promo_row("ZERO") is None)
    r = oc.post("/admin/promo-codes/new", data={
        "code": TAG + "WORDS", "discount_type": "percent", "discount_value": "loads",
    }, follow_redirects=True)
    s.check("a discount that is not a number is refused too",
            _promo_row("WORDS") is None)
    s.check("and none of those left a code behind", r.status_code == 200)

    r = oc.post("/admin/promo-codes/new", data={
        "code": "  ", "discount_type": "percent", "discount_value": "10",
    }, follow_redirects=True)
    s.check("one with no code at all is refused",
            any("code" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("A code the house can actually use")
    oc.post("/admin/promo-codes/new", data={
        "code": TAG.lower() + "welcome", "description": "Returning guests",
        "discount_type": "percent", "discount_value": "15",
        "applies_to": "room", "max_redemptions": "50",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT * FROM promo_codes WHERE code = ?",
                        (TAG + "WELCOME",)).fetchone()
    conn.close()
    s.check("it is stored upper-cased, however it was typed", made is not None,
            detail="a guest typing it in capitals must match")
    s.check("with the cap on how many can use it",
            made and made["max_redemptions"] == 50, detail=str(made["max_redemptions"]))
    s.check("and it starts switched on", made and made["active"] == 1)

    oc.post(f"/admin/promo-codes/{made['id']}/edit", data={
        "code": TAG + "WELCOME", "description": "Returning guests",
        "discount_type": "fixed", "discount_value": "40", "applies_to": "all",
    }, follow_redirects=True)
    conn = db()
    edited = conn.execute("SELECT * FROM promo_codes WHERE id = ?",
                          (made["id"],)).fetchone()
    conn.close()
    s.check("it can be changed to a fixed amount",
            edited["discount_type"] == "fixed" and edited["discount_value"] == 40,
            detail=f"{edited['discount_type']} {edited['discount_value']}")

    conn = db()
    usable = m.validate_promo_code(conn, TAG + "WELCOME", "room", 500.0)[0]
    conn.close()
    s.check("and a guest can use it", usable is not None)
    oc.post(f"/admin/promo-codes/{made['id']}/toggle", follow_redirects=True)
    conn = db()
    off = conn.execute("SELECT active FROM promo_codes WHERE id = ?",
                       (made["id"],)).fetchone()["active"]
    still = m.validate_promo_code(conn, TAG + "WELCOME", "room", 500.0)[0]
    conn.close()
    s.check("switching it off records that", off == 0, detail=str(off))
    s.check("and a guest can no longer use it", still is None,
            detail="switched off has to mean switched off at the till, not "
                   "only on the page that lists them")
    oc.post(f"/admin/promo-codes/{made['id']}/toggle", follow_redirects=True)
    conn = db()
    back = m.validate_promo_code(conn, TAG + "WELCOME", "room", 500.0)[0]
    conn.close()
    s.check("and it can be switched back on", back is not None)

    s.section("Sending one out")
    kept = _stayed("Keeper")
    left = _stayed("Leaver")
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, ?, ?)",
                 (left, "unsubscribed", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    recent = (house_today() - timedelta(days=30)).isoformat()
    who = m.promo_blast_recipients(conn, ["room"], recent)
    conn.close()
    s.check("a past guest is in the list", kept in who, detail=str(list(who)[:4]))
    # The third path that has to honour the do-not-write list. This one is
    # built from bookings rather than subscribers, so it reaches people who
    # never signed up to anything — and its own comment records that this join
    # was missing once.
    s.check("somebody who unsubscribed is not", left not in who,
            detail="a guest could unsubscribe and still be sent the next code")

    # since_months narrows the blast to guests who stayed recently. Without it
    # this exercises the feature across every confirmed booking in the copied
    # database — which cannot send, because the harness clears the credentials
    # and blocks both transports, but it does write an outbox row per real
    # guest and makes the check below share a page with hundreds of others.
    # A test of a fan-out should still aim at a known target.
    r = oc.post(f"/admin/promo-codes/{made['id']}/blast/send", data={
        "segment": "room", "since_months": "1",
        "subject": TAG + " A code for you",
        "body": "Dear {guest_name}, use {promo_code} on your next stay.",
    }, follow_redirects=True)
    held = _held(kept)
    s.check("the guest is written to", len(held) == 1, detail=str(len(held)))
    body = (held[0]["body"] or "") if held else ""
    s.check("their name is filled in", TAG + " Keeper" in body, detail=body[:70])
    s.check("and the code is", TAG + "WELCOME" in body, detail=body[:70])
    s.check("with no placeholder left showing",
            "{guest_name}" not in body and "{promo_code}" not in body,
            detail="a guest receiving a literal {promo_code} is not a thing "
                   "the house gets to do twice")
    s.check("and nothing went to the guest who unsubscribed", not _held(left),
            detail=f"{len(_held(left))} message(s)")

    before = len(_held(kept))
    r = oc.post(f"/admin/promo-codes/{made['id']}/blast/send",
                data={"segment": "room", "since_months": "1",
                      "subject": "", "body": "x"},
                follow_redirects=True)
    s.check("a blast with no subject sends nothing", len(_held(kept)) == before,
            detail=f"{len(_held(kept)) - before} sent")
    s.check("and says what is missing",
            any("subject" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and does not fall over doing it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.section("The workshop ledger takes only what it understands")
    wb = _workshop("LEDGER", total=500)
    oc.post(f"/admin/workshops/registrations/{wb['id']}/add-transaction", data={
        "kind": "payment", "description": "Bank transfer", "amount": "200",
        "method": "bank_transfer",
    }, follow_redirects=True)
    rows = _ledger(wb["id"])
    s.check("a payment is recorded", len(rows) == 1, detail=str(len(rows)))
    s.check("for the amount given", rows and rows[0]["amount"] == 200.0,
            detail=str(rows[0]["amount"]) if rows else "")

    for bad_kind in ("theft", "", "PAYMENT "):
        oc.post(f"/admin/workshops/registrations/{wb['id']}/add-transaction", data={
            "kind": bad_kind, "description": "Nope", "amount": "50",
        }, follow_redirects=True)
    # Two guards, and they mean the same thing: the route validates the kind,
    # and the column carries CHECK(kind IN ('charge','discount','payment',
    # 'refund')). Removing the route's check leaves this green because the
    # constraint refuses the write — so the invariant holds and only the error
    # degrades, from a flash to a 500. That is the benign version of a control
    # passing. The one to worry about is two guards that mean DIFFERENT things
    # and happen to coincide in the fixture, where the branch being named is
    # never the one doing the work.
    s.check("a kind the app does not know writes nothing",
            len(_ledger(wb["id"])) == 1,
            detail=f"{len(_ledger(wb['id']))} rows — the ledger accepted a "
                   "transaction type nothing else can read")
    s.check("and the column refuses it too, independently of the route",
            "CHECK(kind IN" in (m.get_db().execute(
                "SELECT sql FROM sqlite_master WHERE name = 'workshop_transactions'"
            ).fetchone()["sql"] or "").replace(" ", "").replace("CHECK(kindIN", "CHECK(kind IN"),
            detail="the route's validation is the good error message; the "
                   "constraint is what makes it true")

    r = oc.post(f"/admin/workshops/registrations/{wb['id']}/add-transaction", data={
        "kind": "payment", "description": "Cash", "amount": "not a number",
    }, follow_redirects=True)
    s.check("an amount that is not a number writes nothing",
            len(_ledger(wb["id"])) == 1)
    s.check("and refuses rather than falling over", r.status_code == 200,
            detail=f"HTTP {r.status_code} — a 500 writes nothing either")

    oc.post(f"/admin/workshops/registrations/{wb['id']}/add-transaction", data={
        "kind": "payment", "description": "No description here", "amount": "10",
    }, follow_redirects=True)
    oc.post(f"/admin/workshops/registrations/{wb['id']}/add-transaction", data={
        "kind": "payment", "description": "", "amount": "10",
    }, follow_redirects=True)
    s.check("one with no description is refused, one with a description is not",
            len(_ledger(wb["id"])) == 2, detail=str(len(_ledger(wb["id"]))))

    s.section("Guards")
    ec.post("/admin/promo-codes/new", data={
        "code": TAG + "ROGUE", "discount_type": "percent", "discount_value": "90"})
    ec.post(f"/admin/promo-codes/{made['id']}/toggle")
    ec.post(f"/admin/promo-codes/{made['id']}/blast/send",
            data={"segment": "room", "subject": "x", "body": "y"})
    ec.post(f"/admin/workshops/registrations/{wb['id']}/add-transaction",
            data={"kind": "payment", "description": "Rogue", "amount": "999"})
    conn = db()
    rogue = conn.execute("SELECT COUNT(*) c FROM promo_codes WHERE code = ?",
                         (TAG + "ROGUE",)).fetchone()["c"]
    still_on = conn.execute("SELECT active FROM promo_codes WHERE id = ?",
                            (made["id"],)).fetchone()["active"]
    conn.close()
    s.check("an employee cannot invent a discount code", rogue == 0, detail=str(rogue))
    s.check("nor switch one off", still_on == 1, detail=str(still_on))
    s.check("nor write to the workshop ledger", len(_ledger(wb["id"])) == 2,
            detail=str(len(_ledger(wb["id"]))))
    s.check("nor send a blast to the guest list",
            len(_held(kept)) == before, detail=f"{len(_held(kept)) - before} sent")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
