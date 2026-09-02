"""What a guest is actually charged: codes, seasons, extras, deposits.

Nothing in this set had a check, and all of it decides a number somebody pays.
A promo code with the wrong cap is money given away one booking at a time; a
rate override off by one night sells a peak night at the flat rate, or refuses
to sell one at all.

Three properties here are stated in the code's own comments and are worth
holding as checks rather than as prose:

  - The refusal a guest sees is deliberately the SAME sentence whether the
    code has expired, is switched off, is for a different category, is fully
    redeemed, or does not exist at all. Distinct messages made the promo field
    an oracle: submit a guess, and "isn't recognised" versus "has expired"
    tells you whether you found a real code. The owner gets the real reason
    somewhere else.
  - validate_promo_code and promo_refusal_reason run the same checks in the
    same order so the two cannot disagree about which rule bit. That is only
    true while somebody keeps them in step, and nothing but a test notices.
  - A rate override's end_date is INCLUSIVE. The same off-by-one in the other
    direction is the one that has already bitten this app twice in calendar
    feeds, and here it either sells a peak night cheap or refuses a night the
    house could let.

Refusal checks name the mechanism rather than the outcome: a guard and a crash
produce the same absence and only one of them is a feature.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTPRICE"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM promo_code_redemptions WHERE promo_code_id IN
                    (SELECT id FROM promo_codes WHERE code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_rate_overrides WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM deposit_rules WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _promo(code, **over):
    """A promo row, usable unless the caller says otherwise."""
    row = {"discount_type": "percent", "discount_value": 10.0,
           "max_discount_amount": None, "applies_to": "all", "min_spend": None,
           "max_redemptions": None, "redemption_count": 0,
           "valid_from": None, "valid_until": None, "active": 1}
    row.update(over)
    conn = db()
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
           max_discount_amount, applies_to, min_spend, max_redemptions,
           redemption_count, valid_from, valid_until, active, created_at)
           VALUES (?, 'test', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (TAG + code, row["discount_type"], row["discount_value"],
         row["max_discount_amount"], row["applies_to"], row["min_spend"],
         row["max_redemptions"], row["redemption_count"], row["valid_from"],
         row["valid_until"], row["active"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    out = conn.execute("SELECT * FROM promo_codes WHERE code = ?", (TAG + code,)).fetchone()
    conn.close()
    return out


def _validate(code, category="room", subtotal=500.0):
    conn = db()
    try:
        return m.validate_promo_code(conn, code, category, subtotal)
    finally:
        conn.close()


def _reason(code, category="room", subtotal=500.0):
    conn = db()
    try:
        return m.promo_refusal_reason(conn, code, category, subtotal)
    finally:
        conn.close()


def run():
    s = Suite("What the guest is charged")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = m.house_today()

    s.section("The discount itself")
    pct = _promo("PCT", discount_type="percent", discount_value=20)
    s.check("a percentage comes off", m.compute_promo_discount(500.0, pct) == 100.0,
            detail=str(m.compute_promo_discount(500.0, pct)))
    fixed = _promo("FIXED", discount_type="fixed", discount_value=50)
    s.check("a fixed amount comes off", m.compute_promo_discount(500.0, fixed) == 50.0,
            detail=str(m.compute_promo_discount(500.0, fixed)))
    capped = _promo("CAPPED", discount_type="percent", discount_value=20,
                    max_discount_amount=60)
    s.check("a cap on a percentage holds",
            m.compute_promo_discount(500.0, capped) == 60.0,
            detail=str(m.compute_promo_discount(500.0, capped)))

    # The one that costs real money: a discount larger than the bill would make
    # the total negative, which is the house paying the guest to stay.
    big = _promo("BIG", discount_type="fixed", discount_value=500)
    s.check("a discount can never exceed the bill",
            m.compute_promo_discount(100.0, big) == 100.0,
            detail=f"{m.compute_promo_discount(100.0, big)} off a 100.00 bill")
    s.check("and is never negative",
            m.compute_promo_discount(0.0, big) == 0.0,
            detail=str(m.compute_promo_discount(0.0, big)))

    s.section("A refused code says nothing about whether it exists")
    # Distinct messages made this field an oracle: submit a guess, and
    # "isn't recognised" versus "has expired" tells you which codes are real.
    _promo("OFF", active=0)
    _promo("EXPIRED", valid_until=(today - timedelta(days=1)).isoformat())
    _promo("EARLY", valid_from=(today + timedelta(days=7)).isoformat())
    _promo("DINNERONLY", applies_to="restaurant")
    _promo("USEDUP", max_redemptions=2, redemption_count=2)

    messages = {}
    for label, code in (("no such code", TAG + "NOTHINGHERE"),
                        ("switched off", TAG + "OFF"),
                        ("expired", TAG + "EXPIRED"),
                        ("not started", TAG + "EARLY"),
                        ("wrong category", TAG + "DINNERONLY"),
                        ("fully redeemed", TAG + "USEDUP")):
        promo, discount, err = _validate(code)
        messages[label] = err
        s.check(f"a {label} code gives no discount", promo is None and discount == 0.0,
                detail=f"{promo and promo['code']} / {discount}")
    s.check("and every one of them says the same sentence",
            len(set(messages.values())) == 1,
            detail=str(messages))
    s.check("which is the one that gives nothing away",
            set(messages.values()) == {m.PROMO_REFUSED}, detail=str(set(messages.values())))

    # The exception, and it is deliberate: a minimum spend is something the
    # guest can act on, so it says the number.
    _promo("BIGSPEND", min_spend=1000)
    _promo2, _d, err = _validate(TAG + "BIGSPEND", subtotal=500.0)
    s.check("a minimum spend is told to the guest, because they can act on it",
            err and "1000" in err, detail=str(err))

    s.section("The owner is told the real reason")
    for label, code, needle in (("no such code", TAG + "NOTHINGHERE", "no such"),
                                ("switched off", TAG + "OFF", "switched off"),
                                ("expired", TAG + "EXPIRED", "expired"),
                                ("not started", TAG + "EARLY", "not valid until"),
                                ("wrong category", TAG + "DINNERONLY", "restaurant"),
                                ("fully redeemed", TAG + "USEDUP", "redeemed")):
        got = _reason(code)
        s.check(f"{label} is named for the owner",
                got and needle in got.lower(), detail=str(got))

    s.section("The guest's answer and the owner's never disagree")
    # Two functions running the same checks in the same order, which is true
    # only while somebody keeps them in step.
    cases = [TAG + "NOTHINGHERE", TAG + "OFF", TAG + "EXPIRED", TAG + "EARLY",
             TAG + "DINNERONLY", TAG + "USEDUP", TAG + "BIGSPEND", TAG + "PCT"]
    disagreements = []
    for code in cases:
        promo, _d, err = _validate(code)
        reason = _reason(code)
        if (promo is None) != (reason is not None):
            disagreements.append((code, promo is not None, reason))
    s.check("every code is refused by both or by neither", not disagreements,
            detail=str(disagreements))
    s.check("and a usable code is refused by neither",
            _validate(TAG + "PCT")[0] is not None and _reason(TAG + "PCT") is None)

    s.section("A code runs out")
    limited = _promo("LIMITED", max_redemptions=1)
    s.check("it works the first time", _validate(TAG + "LIMITED")[0] is not None)
    conn = db()
    m.record_promo_redemption(conn, limited, "room", "REF-1", "guest@example.invalid",
                              500.0, 50.0)
    conn.commit()
    conn.close()
    s.check("and not the second", _validate(TAG + "LIMITED")[0] is None)
    conn = db()
    red = conn.execute(
        """SELECT * FROM promo_code_redemptions WHERE promo_code_id = ?""",
        (limited["id"],)).fetchone()
    count = conn.execute("SELECT redemption_count c FROM promo_codes WHERE id = ?",
                         (limited["id"],)).fetchone()["c"]
    conn.close()
    s.check("the redemption is recorded", red is not None)
    s.check("with what it actually cost the house",
            red and red["final_amount"] == 450.0,
            detail=f"{red['final_amount'] if red else None} — original minus discount")
    s.check("and the counter moved", count == 1, detail=str(count))

    s.section("A season priced differently")
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    base = room["price_per_night"]
    start = today + timedelta(days=200)
    end = start + timedelta(days=9)
    conn.execute(
        """INSERT INTO room_rate_overrides (room_id, start_date, end_date,
           price_per_night, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (room["id"], start.isoformat(), end.isoformat(), base + 111,
         TAG + " Peak", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    s.check("a night inside the season takes the override",
            m.room_night_rate(conn, room, start + timedelta(days=3)) == base + 111,
            detail=str(m.room_night_rate(conn, room, start + timedelta(days=3))))
    s.check("the first night of it too",
            m.room_night_rate(conn, room, start) == base + 111)
    # end_date is inclusive. Off by one here either sells the last peak night
    # at the flat rate, or prices a night the season does not cover.
    s.check("and the last night, because end_date is inclusive",
            m.room_night_rate(conn, room, end) == base + 111,
            detail=f"{m.room_night_rate(conn, room, end)} vs {base + 111}")
    s.check("the night after it falls back to the flat rate",
            m.room_night_rate(conn, room, end + timedelta(days=1)) == base,
            detail=str(m.room_night_rate(conn, room, end + timedelta(days=1))))
    s.check("and the night before it does too",
            m.room_night_rate(conn, room, start - timedelta(days=1)) == base)

    # Two overlapping seasons: the most recent wins, so a correction entered
    # later actually takes effect.
    conn.execute(
        """INSERT INTO room_rate_overrides (room_id, start_date, end_date,
           price_per_night, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (room["id"], start.isoformat(), end.isoformat(), base + 222,
         TAG + " Correction", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    s.check("a correction entered later wins over the earlier season",
            m.room_night_rate(conn, room, start) == base + 222,
            detail=str(m.room_night_rate(conn, room, start)))
    conn.close()

    s.section("Adding a season through the page")
    r = oc.post(f"/admin/rooms/{room['id']}/rates/new", data={
        "start_date": (today + timedelta(days=300)).isoformat(),
        "end_date": (today + timedelta(days=310)).isoformat(),
        "price_per_night": "395", "label": TAG + " Christmas",
    }, follow_redirects=True)
    conn = db()
    added = conn.execute("SELECT * FROM room_rate_overrides WHERE label = ?",
                         (TAG + " Christmas",)).fetchone()
    conn.close()
    s.check("it is saved", added is not None, detail=str(flashes(r)))
    s.check("at the price given", added and float(added["price_per_night"]) == 395.0,
            detail=str(added["price_per_night"]) if added else "")

    # A range that ends before it starts would price nothing, or everything,
    # depending on how it is read. It must not be storable.
    r = oc.post(f"/admin/rooms/{room['id']}/rates/new", data={
        "start_date": (today + timedelta(days=320)).isoformat(),
        "end_date": (today + timedelta(days=310)).isoformat(),
        "price_per_night": "395", "label": TAG + " Backwards",
    }, follow_redirects=True)
    conn = db()
    backwards = conn.execute("SELECT * FROM room_rate_overrides WHERE label = ?",
                             (TAG + " Backwards",)).fetchone()
    conn.close()
    s.check("a season that ends before it starts is not stored",
            backwards is None, detail="it would price nothing, or everything")
    s.check("and the page says so rather than erroring", r.status_code == 200,
            detail=f"HTTP {r.status_code} — a 500 also stores nothing")

    s.section("Extras a guest can add")
    oc.post("/admin/extras/new", data={
        "name": TAG + " Champagne on arrival", "price": "45,50",
        "description": "A bottle in the room", "category": "welcome",
    }, follow_redirects=True)
    conn = db()
    ex = conn.execute("SELECT * FROM extras WHERE name = ?",
                      (TAG + " Champagne on arrival",)).fetchone()
    conn.close()
    s.check("an extra is added", ex is not None)
    s.check("a comma decimal is read as money", ex and float(ex["price"]) == 45.50,
            detail=str(ex["price"]) if ex else "")
    s.check("and it starts available", ex and ex["active"] == 1)

    oc.post(f"/admin/extras/{ex['id']}/toggle", follow_redirects=True)
    conn = db()
    off = conn.execute("SELECT active FROM extras WHERE id = ?", (ex["id"],)).fetchone()
    conn.close()
    s.check("switching it off takes it off sale", off["active"] == 0,
            detail=str(off["active"]))
    body = m.app.test_client().get(f"/book/{room['id']}").get_data(as_text=True)
    s.check("and it disappears from the booking form",
            TAG + " Champagne on arrival" not in body,
            detail="an extra switched off is still being sold")

    # A price the app cannot read must be refused, not crashed on. float() on
    # a form field returned a 500 for "45,50" — which is how the price is
    # written here — and for any typo. Nothing was saved either way, so the
    # absence proved nothing; what separates the guard from the crash is that
    # the page still works and says what to type instead.
    r = oc.post("/admin/extras/new", data={
        "name": TAG + " Nonsense price", "price": "about forty",
    }, follow_redirects=True)
    conn = db()
    junk = conn.execute("SELECT COUNT(*) c FROM extras WHERE name = ?",
                        (TAG + " Nonsense price",)).fetchone()["c"]
    conn.close()
    s.check("a price that is not a number is refused", junk == 0, detail=str(junk))
    s.check("and the page survives it", r.status_code == 200,
            detail=f"HTTP {r.status_code} — a 500 saves nothing either")
    s.check("and says what to type instead",
            any("price" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    oc.post(f"/admin/extras/{ex['id']}/edit", data={
        "name": TAG + " Champagne on arrival", "price": "55",
        "description": "A better bottle", "category": "welcome",
    }, follow_redirects=True)
    conn = db()
    edited = conn.execute("SELECT * FROM extras WHERE id = ?", (ex["id"],)).fetchone()
    conn.close()
    s.check("its price can be changed", float(edited["price"]) == 55.0,
            detail=str(edited["price"]))

    s.section("None of it is the employees' to price")
    ec.post("/admin/extras/new", data={"name": TAG + " Rogue extra", "price": "1"})
    ec.post(f"/admin/rooms/{room['id']}/rates/new", data={
        "start_date": (today + timedelta(days=400)).isoformat(),
        "end_date": (today + timedelta(days=401)).isoformat(),
        "price_per_night": "1", "label": TAG + " Rogue rate"})
    ec.post("/admin/promo-codes/new", data={
        "code": TAG + "ROGUE", "discount_type": "percent", "discount_value": "90"})
    conn = db()
    rogue = [
        conn.execute("SELECT COUNT(*) c FROM extras WHERE name = ?",
                     (TAG + " Rogue extra",)).fetchone()["c"],
        conn.execute("SELECT COUNT(*) c FROM room_rate_overrides WHERE label = ?",
                     (TAG + " Rogue rate",)).fetchone()["c"],
        conn.execute("SELECT COUNT(*) c FROM promo_codes WHERE code = ?",
                     (TAG + "ROGUE",)).fetchone()["c"],
    ]
    conn.close()
    s.check("an employee cannot invent an extra, a rate or a discount",
            rogue == [0, 0, 0], detail=str(rogue))
    s.check("and is sent away rather than shown the page",
            ec.get("/admin/promo-codes").status_code in (302, 403))
    s.check("while the owner can open it", oc.get("/admin/promo-codes").status_code == 200)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
