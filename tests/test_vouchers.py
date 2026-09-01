"""Gift vouchers: sold, spent, and reconciling to the cent.

A voucher is money the house has ALREADY BEEN PAID for something that has not
happened yet, which makes it two things at once: a liability on the books and a
bearer instrument in somebody's coat pocket. Both of those shape the checks
here.

As a liability: the balance is a LEDGER, not a column. A voucher part-spent at
the till and part-spent against a stay has to reconcile months later, and a
counter decremented in two places goes wrong in two places -- the same reason
stock is a ledger in this app. So the checks below spend a voucher twice, in two
different ways, and then ask the ledger and the bill to agree.

As a bearer instrument: a redemption over the balance is REFUSED rather than
trimmed, the public balance page says nothing about who bought it, and
cancelling keeps the history rather than deleting it. Money already spent with
nothing to account for it is worse than a voucher that cannot be spent again.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZVOU"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM voucher_redemptions WHERE voucher_id IN "
                 "(SELECT id FROM gift_vouchers WHERE note LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM gift_vouchers WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'check_voucher'")
    conn.commit()
    conn.close()


def _issue(oc, amount, **extra):
    data = {"amount": str(amount), "note": f"{TAG} test"}
    data.update(extra)
    r = oc.post("/management/vouchers/new", data=data, follow_redirects=True)
    conn = db()
    row = conn.execute("SELECT * FROM gift_vouchers WHERE note LIKE ? "
                       "ORDER BY id DESC LIMIT 1", (TAG + "%",)).fetchone()
    conn.close()
    return row, r


def _ledger(voucher_id):
    conn = db()
    try:
        return m.voucher_ledger(conn, voucher_id)
    finally:
        conn.close()


def _stay(ref, price=600.0):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    arrival = house_today() + timedelta(days=25)
    departure = arrival + timedelta(days=2)
    priced = m.compute_room_total(conn, room, arrival, departure)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, 'zzvou@example.invalid', '', ?, ?, 2, 'confirmed', ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         arrival.isoformat(), departure.isoformat(), priced,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _owed(booking_id):
    conn = db()
    try:
        bill = m.booking_bill(conn, booking_id)
        return bill["owed"] if bill else 0
    finally:
        conn.close()


def run():
    s = Suite("Gift vouchers")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("Selling one")
    v, r = _issue(oc, 250, purchaser_name="ZZ Buyer", recipient_name="ZZ Friend",
                  message="Happy birthday")
    s.check("it is written down", v is not None, detail=f"{flashes(r)[:1]}")
    s.check("with what was paid for it",
            abs(float(v["original_amount"] or 0) - 250) < 0.01,
            detail=f"{v['original_amount']}")
    s.check("and a code",
            bool(v["code"]) and len(v["code"].replace("-", "")) >= 12,
            detail=f"{v['code']!r}")
    s.check("in groups, so it can be read down a telephone",
            v["code"].count("-") >= 3, detail=f"{v['code']!r}")
    s.check("with no easily-confused characters in it",
            not set(v["code"]) & set("O0I1L"),
            detail=f"{v['code']!r} — 0/O and 1/I/L cost more in misread codes "
                   "than they buy in length")
    s.check("the balance starts at the full amount",
            abs(_ledger(v["id"])["balance"] - 250) < 0.01)
    s.check("and there is no balance column to go stale",
            "balance" not in [c[1] for c in db().execute(
                "PRAGMA table_info(gift_vouchers)").fetchall()],
            detail="a counter decremented in two places goes wrong in two places")

    s.section("Two codes are never the same")
    codes = {v["code"]}
    for i in range(6):
        other, _ = _issue(oc, 10 + i)
        codes.add(other["code"])
    s.check("seven vouchers, seven codes", len(codes) == 7,
            detail=f"{len(codes)} distinct — a collision lets one person spend "
                   "another's gift")

    s.section("Spending part of it, at the till")
    ok_before = _ledger(v["id"])["balance"]
    r = oc.post(f"/management/vouchers/{v['id']}/spend",
                data={"amount": "60", "kind": "pos", "reference": "ZZ table 4"},
                follow_redirects=True)
    led = _ledger(v["id"])
    s.check("it comes off the balance", abs(led["balance"] - (ok_before - 60)) < 0.01,
            detail=f"{ok_before} -> {led['balance']}")
    s.check("and is on the voucher's own history", len(led["redemptions"]) == 1,
            detail=f"{len(led['redemptions'])} rows — a balance with no history "
                   "cannot be reconciled by somebody who was not there")
    s.check("with what it was spent on",
            led["redemptions"][0]["kind"] == "pos",
            detail=f"{led['redemptions'][0]['kind']}")
    s.check("and the reference typed with it",
            led["redemptions"][0]["reference"] == "ZZ table 4")

    s.section("Spending the rest against a stay, where the bill can see it")
    stay = _stay("A")
    owed_before = _owed(stay["id"])
    r = oc.post(f"/management/vouchers/{v['id']}/spend",
                data={"amount": "190", "booking_id": str(stay["id"])},
                follow_redirects=True)
    s.check("the stay owes 190 less",
            abs(_owed(stay["id"]) - (owed_before - 190)) < 0.01,
            detail=f"{owed_before} -> {_owed(stay['id'])} — a voucher the bill "
                   "cannot see is a voucher the balance chase still asks for")
    conn = db()
    pay = conn.execute(
        "SELECT * FROM booking_payments WHERE booking_id = ? ORDER BY id DESC LIMIT 1",
        (stay["id"],)).fetchone()
    conn.close()
    s.check("recorded as a payment, not a discount", pay is not None,
            detail="a discount would take the same sum off revenue twice and make "
                   "the stay look cheaper than it was sold for")
    if pay:
        s.check("and named as a voucher", pay["method"] == "voucher",
                detail=f"{pay['method']} — three months on, a bank statement has "
                       "to be matched to a booking by somebody who was not there")
    led = _ledger(v["id"])
    s.check("the voucher is now spent in full", led["state"] == "spent",
            detail=f"{led['state']} with {led['balance']} left")
    s.check("and its rows add up to what it was worth",
            abs(led["spent"] - led["original"]) < 0.01,
            detail=f"{led['spent']} of {led['original']}")

    s.section("More than is left is refused, not trimmed")
    v2, _ = _issue(oc, 100)
    r = oc.post(f"/management/vouchers/{v2['id']}/spend",
                data={"amount": "150", "kind": "pos"}, follow_redirects=True)
    led2 = _ledger(v2["id"])
    s.check("nothing was taken", abs(led2["balance"] - 100) < 0.01,
            detail=f"{led2['balance']} — with a guest waiting, quietly taking "
                   "less is how a voucher and a bill start disagreeing")
    s.check("and the balance is named in the refusal",
            any("100" in f for f in flashes(r)), detail=f"{flashes(r)[:1]}")
    s.check("no redemption row was written", not led2["redemptions"])

    s.section("A spent voucher cannot be spent again")
    r = oc.post(f"/management/vouchers/{v['id']}/spend",
                data={"amount": "5", "kind": "pos"}, follow_redirects=True)
    s.check("refused", len(_ledger(v["id"])["redemptions"]) == 2,
            detail="a third redemption landed on a voucher with nothing left")
    s.check("and it says so",
            any("spent" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("An expired voucher takes nothing, and says when it went")
    conn = db()
    conn.execute("UPDATE gift_vouchers SET expires_on = ? WHERE id = ?",
                 ((house_today() - timedelta(days=3)).isoformat(), v2["id"]))
    conn.commit()
    conn.close()
    s.check("the state reads expired", _ledger(v2["id"])["state"] == "expired")
    r = oc.post(f"/management/vouchers/{v2['id']}/spend",
                data={"amount": "5", "kind": "pos"}, follow_redirects=True)
    s.check("and nothing is taken", not _ledger(v2["id"])["redemptions"],
            detail=f"{flashes(r)[:1]}")
    s.check("the date it expired is in the message",
            any("expired on" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]} — 'expired' with no date invites an argument")

    s.section("An expiry already past is refused when selling")
    before = db().execute("SELECT COUNT(*) c FROM gift_vouchers").fetchone()["c"]
    r = oc.post("/management/vouchers/new",
                data={"amount": "50", "note": f"{TAG} past",
                      "expires_on": (house_today() - timedelta(days=1)).isoformat()},
                follow_redirects=True)
    after = db().execute("SELECT COUNT(*) c FROM gift_vouchers").fetchone()["c"]
    s.check("no voucher is created", after == before,
            detail="sold worthless the moment it was paid for")
    s.check("and the reason is given",
            any("past" in f.lower() or "worthless" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("Cancelling keeps the history")
    v3, _ = _issue(oc, 80)
    oc.post(f"/management/vouchers/{v3['id']}/spend",
            data={"amount": "20", "kind": "restaurant"}, follow_redirects=True)
    oc.post(f"/management/vouchers/{v3['id']}/void", follow_redirects=True)
    led3 = _ledger(v3["id"])
    s.check("it stops being spendable", not led3["spendable"],
            detail=f"{led3['state']}")
    s.check("but the spent 20 is still on the record",
            len(led3["redemptions"]) == 1 and abs(led3["spent"] - 20) < 0.01,
            detail="deleting would leave money already spent with nothing to "
                   "account for it")
    r = oc.post(f"/management/vouchers/{v3['id']}/spend",
                data={"amount": "10", "kind": "pos"}, follow_redirects=True)
    s.check("and nothing more can be taken", len(_ledger(v3["id"])["redemptions"]) == 1,
            detail=f"{flashes(r)[:1]}")
    oc.post(f"/management/vouchers/{v3['id']}/void", follow_redirects=True)
    s.check("reinstating works, because cancelling by mistake happens",
            _ledger(v3["id"])["spendable"])

    s.section("The holder can check a balance, however they type the code")
    v4, _ = _issue(oc, 120, recipient_name="ZZ Secret Recipient",
                   purchaser_name="ZZ Secret Buyer")
    for label, typed in (("as printed", v4["code"]),
                         ("in lower case", v4["code"].lower()),
                         ("with spaces instead of hyphens", v4["code"].replace("-", " ")),
                         ("with nothing between the groups", v4["code"].replace("-", ""))):
        body = anon.post("/vouchers", data={"code": typed},
                         follow_redirects=True).get_data(as_text=True)
        s.check(f"{label} finds it", "120.00" in body,
                detail="somebody reading a card aloud is not typing hyphens")
        conn = db()
        conn.execute("DELETE FROM submission_log WHERE action = 'check_voucher'")
        conn.commit()
        conn.close()

    s.section("And learns nothing else about the household")
    body = anon.post("/vouchers", data={"code": v4["code"]},
                     follow_redirects=True).get_data(as_text=True)
    s.check("not who bought it", "ZZ Secret Buyer" not in body,
            detail="a voucher code is a bearer instrument — somebody who found "
                   "one is entitled to its value and nothing else")
    s.check("not who it was for", "ZZ Secret Recipient" not in body)
    s.check("and the page is noindex", "noindex" in body,
            detail="a page that answers 'what is this code worth' should not be "
                   "in a search index")
    # The route's dict is the real guard, so it is named here rather than left
    # implicit: the public page is handed the value, the state and the code, and
    # nothing about a person. The checks above prove the page does not show the
    # names; this one is what stops the next line added to that template from
    # leaking them.
    conn = db()
    ledger = m.voucher_ledger(conn, v4["id"])
    conn.close()
    s.check("the ledger does hold the names, privately",
            ledger["voucher"]["purchaser_name"] == "ZZ Secret Buyer",
            detail="the checks above would pass for the wrong reason if the "
                   "names were simply not recorded anywhere")

    s.section("A code nobody holds is refused, and slowly")
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'check_voucher'")
    conn.commit()
    conn.close()
    r = anon.post("/vouchers", data={"code": "ZZZZ-ZZZZ-ZZZZ-ZZZZ"},
                  follow_redirects=True)
    s.check("nothing is found",
            any("could not find" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")
    for _ in range(12):
        anon.post("/vouchers", data={"code": "ZZZZ-ZZZZ-ZZZZ-ZZZY"},
                  follow_redirects=True)
    r = anon.post("/vouchers", data={"code": v4["code"]}, follow_redirects=True)
    s.check("and guessing is rate limited",
            "120.00" not in r.get_data(as_text=True),
            detail="16 characters is plenty, but only while somebody cannot sit "
                   "and try")
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'check_voucher'")
    conn.commit()
    conn.close()

    s.section("The owner's list adds up")
    body = oc.get("/management/vouchers").get_data(as_text=True)
    s.check("it opens", "Gift vouchers" in body)
    s.check("and leads on what is still to spend", "Still to spend" in body,
            detail="a voucher is a liability; leading on what was sold flatters")
    s.check("with the code on the row", v4["code"] in body)
    s.check("and the list has a toolbar like every other list",
            "q=" in body or 'name="q"' in body,
            detail="another one-off search box")

    s.section("Guards")
    s.check("an employee cannot see the vouchers",
            ec.get("/management/vouchers").status_code in (302, 403))
    s.check("nor sell one",
            ec.post("/management/vouchers/new", data={"amount": "10"},
                    follow_redirects=False).status_code in (302, 403))
    s.check("nor spend one",
            ec.post(f"/management/vouchers/{v4['id']}/spend",
                    data={"amount": "10"},
                    follow_redirects=False).status_code in (302, 403))
    s.check("a voucher that does not exist is a 404",
            oc.get("/management/vouchers/999999").status_code == 404)
    s.check("and the balance still reads 120 after all that",
            abs(_ledger(v4["id"])["balance"] - 120) < 0.01,
            detail=f"{_ledger(v4['id'])['balance']}")

    _cleanup()
    return s
