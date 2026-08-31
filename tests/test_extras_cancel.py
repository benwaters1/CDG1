"""Taking something back off a guest's stay.

cancel_booking_extra has existed since the extras were built. It is carefully
written — it corrects the stock ledger by APPENDING a matching movement rather
than rewriting the sale, because the ledger is append-only — and nothing had
ever called it. So a guest could add a case of wine to their stay and nobody
could take it off again: not the guest, not the owner, not anybody.

AND THE SAME BUG WAS WAITING ON THE OTHER SIDE. Four places total what a guest
added. The bill skips a cancelled line, room economics skips it, stock
depletion skips it — and the VAT working summed every row in the table with no
status filter at all, while every other figure in that same function filtered
on 'confirmed'. It was harmless only because nothing could produce a cancelled
line. Wiring up the cancel would have put money on a tax return that nobody
was ever charged.

So the interesting checks here are not "does the button work". They are:

  - the stock goes BACK, by a new movement rather than by deleting the old one;
  - and every figure that counts extras moves together.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTXTR"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM stock_movements WHERE booking_extra_id IN
                    (SELECT id FROM booking_extras WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE action = 'booking_extra_cancelled'")
    conn.commit()
    conn.close()


def _stay(ref):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    today = datetime.now(m.LOCAL_TZ).date()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, 'Amelie Fontaine', ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + ref, TAG.lower() + "tok" + ref,
         f"{TAG.lower()}{ref.lower()}@example.invalid", today.isoformat(),
         (today + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _sell(booking_id, price=60.0, qty=2, stock_item_id=None):
    """A line on the stay, with a stock movement behind it if asked."""
    conn = db()
    conn.execute(
        """INSERT INTO booking_extras (booking_id, category, name, unit_price,
           quantity, status, created_at)
           VALUES (?, 'room', ?, ?, ?, 'confirmed', ?)""",
        (booking_id, TAG + " case of wine", price, qty,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    line = conn.execute(
        "SELECT * FROM booking_extras WHERE booking_id = ? ORDER BY id DESC LIMIT 1",
        (booking_id,)).fetchone()
    if stock_item_id:
        m.record_stock_movement(conn, stock_item_id, -qty, "sale",
                                booking_extra_id=line["id"])
        conn.commit()
    conn.close()
    return line


def _stock_level(item_id):
    return _one("SELECT COALESCE(SUM(delta), 0) AS d FROM stock_movements "
                "WHERE stock_item_id = ?", (item_id,))["d"]


def run():
    s = Suite("Taking a line off a stay")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    today = datetime.now(m.LOCAL_TZ).date()

    conn = db()
    conn.execute(
        """INSERT INTO stock_items (name, unit, active, created_at)
           VALUES (?, 'bottle', 1, ?)""",
        (TAG + " Jurançon", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    item = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                        (TAG + " Jurançon",)).fetchone()["id"]
    m.record_stock_movement(conn, item, 12, "purchase")
    conn.commit()
    conn.close()

    stay = _stay("ONE")
    line = _sell(stay["id"], price=60.0, qty=2, stock_item_id=item)

    s.section("Before anybody cancels anything")
    s.check("the line is on the bill", any(
        l.get("kind") == "extra" for l in _bill(stay["id"])["lines"]),
        detail="nothing below means anything without it")
    s.check("and the stock came out", _stock_level(item) == 10,
            detail=f"{_stock_level(item)} of 12 after selling 2")

    s.section("Cancelling it")
    r = oc.post(f"/admin/bookings/extras/{line['id']}/cancel", follow_redirects=True)
    after = _one("SELECT * FROM booking_extras WHERE id = ?", (line["id"],))
    s.check("the line is marked cancelled", after["status"] == "cancelled",
            detail=str(flashes(r)))
    s.check("it comes off the bill",
            not any(l.get("kind") == "extra" for l in _bill(stay["id"])["lines"]),
            detail="a cancelled line the guest is still charged for is worse "
                   "than one that could not be cancelled")
    s.check("and the stock goes back", _stock_level(item) == 12,
            detail=f"{_stock_level(item)} of 12")

    # The ledger is append-only: a mistake is corrected by a further entry
    # rather than by rewriting history, so the sale is still there.
    moves = _one("SELECT COUNT(*) AS c FROM stock_movements WHERE booking_extra_id = ?",
                 (line["id"],))["c"]
    s.check("by a NEW movement, not by deleting the sale", moves == 2,
            detail=f"{moves} movements — the sale and its correction")
    sale_still = _one("SELECT COUNT(*) AS c FROM stock_movements "
                      "WHERE booking_extra_id = ? AND delta < 0", (line["id"],))["c"]
    s.check("the original sale is still on the ledger", sale_still == 1,
            detail="rewriting it would lose the fact that it happened")
    s.check("and the correction says what it was for",
            _one("SELECT COUNT(*) AS c FROM stock_movements WHERE booking_extra_id = ? "
                 "AND reason = 'correction'", (line["id"],))["c"] == 1)
    s.check("it is on the audit record",
            _one("SELECT COUNT(*) AS c FROM audit_log "
                 "WHERE action = 'booking_extra_cancelled' AND target = ?",
                 (TAG + "ONE",))["c"] == 1,
            detail="taking money off a bill is worth writing down")

    s.section("Cancelling it twice does nothing twice")
    r = oc.post(f"/admin/bookings/extras/{line['id']}/cancel", follow_redirects=True)
    s.check("it says it was already done",
            any("already" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the stock does not go back a second time", _stock_level(item) == 12,
            detail=f"{_stock_level(item)} — a double click used to be the only "
                   "way to find out this was possible")
    s.check("with no second correction on the ledger",
            _one("SELECT COUNT(*) AS c FROM stock_movements WHERE booking_extra_id = ?",
                 (line["id"],))["c"] == 2)

    s.section("The function guards itself, not only the route")
    # Going through the route cannot prove this: the route ALSO checks the
    # status and answers "already cancelled" before calling anything. So the
    # function's own guard is untested through the web, and the function is the
    # reusable half -- the next caller may not have a route in front of it.
    conn = db()
    solo_stay = _stay("SOLO")
    conn.close()
    solo = _sell(solo_stay["id"], price=25.0, qty=3, stock_item_id=item)
    level_before = _stock_level(item)
    conn = db()
    with m.app.test_request_context():
        first = m.cancel_booking_extra(conn, solo["id"])
        second = m.cancel_booking_extra(conn, solo["id"])
    conn.commit()
    conn.close()
    s.check("the first call cancels it", first is True)
    s.check("the second refuses", second is False,
            detail="without this the stock goes back twice and the ledger says "
                   "the house has three bottles it does not have")
    s.check("and the stock moved once, not twice",
            _stock_level(item) == level_before + 3,
            detail=f"{_stock_level(item)}, expected {level_before + 3}")

    s.section("Every figure that counts extras agrees")
    # The VAT working summed the whole table with no status filter, while the
    # bill, room economics and stock depletion all skipped cancelled lines. It
    # was harmless only because nothing could cancel one.
    fresh = _stay("TWO")
    sold = _sell(fresh["id"], price=100.0, qty=1)
    start = today.replace(day=1)
    end = (today.replace(day=1) + timedelta(days=45)).replace(day=1)

    conn = db()
    with m.app.test_request_context():
        before_vat = m.vat_working(conn, start, end)
    conn.close()
    vat_before = _extras_line(before_vat)

    oc.post(f"/admin/bookings/extras/{sold['id']}/cancel", follow_redirects=True)
    conn = db()
    with m.app.test_request_context():
        after_vat = m.vat_working(conn, start, end)
    conn.close()
    vat_after = _extras_line(after_vat)

    s.check("the VAT working had the sale in it", vat_before >= 100.0,
            detail=f"{vat_before}")
    s.check("and drops it when the line is cancelled",
            abs((vat_before - vat_after) - 100.0) < 0.01,
            detail=f"{vat_before} -> {vat_after} — this figure used to keep it, "
                   "putting money on a tax return that nobody was charged")

    s.section("Who may take a line off")
    third = _stay("THREE")
    theirs = _sell(third["id"], price=40.0, qty=1)
    r = ec.post(f"/admin/bookings/extras/{theirs['id']}/cancel", follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and the line is untouched",
            _one("SELECT status FROM booking_extras WHERE id = ?",
                 (theirs["id"],))["status"] == "confirmed")
    r = anon.post(f"/admin/bookings/extras/{theirs['id']}/cancel", follow_redirects=False)
    s.check("nor a stranger", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")
    # Deliberately not the guest either: some of these have been ordered in or
    # driven somewhere by the time anybody changes their mind, and the app does
    # not know which. Refunds are a manual call here and so is this.
    page = anon.get(f"/book/manage/{third['manage_token']}")
    s.check("and the guest is not offered the button",
            "cancel_booking_extra_line" not in page.get_data(as_text=True)
            and "take off" not in page.get_data(as_text=True),
            detail="the guest asks and a person decides, the same way a refund "
                   "works in this house")

    r = oc.post("/admin/bookings/extras/99999999/cancel", follow_redirects=False)
    s.check("a line that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    _cleanup()
    return s


def _bill(booking_id):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.booking_bill(conn, booking_id)
    finally:
        conn.close()


def _extras_line(working):
    """The gross figure vat_working attributes to extras."""
    for line in working["lines"]:
        if "extra" in str(line["source"]).lower():
            return float(line["gross"])
    return 0.0


if __name__ == "__main__":
    print(run().report())
