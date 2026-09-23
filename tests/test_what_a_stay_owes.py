"""What a stay owes, said the same way everywhere -- and no tax on nights nobody spent.

booking_bill is THE definition of what a stay owes: the Pay button, the
balance chase, the debtors list and the guest's own page all read it. Three
other places worked it out for themselves, and each was wrong differently.

  THE FORECAST OF MONEY COMING IN took the discount off total_price a second
  time. total_price is already net of it, so every stay booked with a code was
  forecast short by its discount, and anything added to a stay afterwards was
  not forecast at all.

  THE MONEY-DUE LIST read total_price less what was paid, which leaves out the
  taxe de sejour and anything added since.

  THE NO-SHOW MESSAGE did the same, under a comment that said it read the bill.

And underneath it, the tax. The taxe de sejour is due per person per night
actually spent. The return has always left cancelled stays out for that
reason, but a stay nobody arrived for was declared, charged on the bill and
printed on the guest's statement as though the nights had happened. That is
the house handing the commune tax on nights nobody slept.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, free_window
import _harness

m = _harness.m
TAG = "ZZOWED"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM bookings WHERE guest_name LIKE ?", (TAG + "%",)).fetchall()]
    for bid in ids:
        conn.execute("DELETE FROM booking_extras WHERE category = 'room' AND booking_id = ?",
                     (bid,))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _past_stay(ref, room_id, city_tax, paid):
    """A stamped stay that began three days ago, the way create_booking leaves one."""
    conn = db()
    arrival = m.house_today() - timedelta(days=3)
    departure = arrival + timedelta(days=2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, city_tax, created_at,
           room_total_quoted, room_total_quoted_for)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 'unpaid', 500, ?, ?, ?,
                   500, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzowed.{ref}@example.invalid".lower(), arrival.isoformat(),
         departure.isoformat(), paid, city_tax, datetime.now(timezone.utc).isoformat(),
         f"{arrival.isoformat()}|{departure.isoformat()}"))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _row(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("What a stay owes")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    today = m.house_today()

    s.section("One figure, wherever the question is asked")
    # A stay booked with a discount, with something added to it afterwards --
    # the two things the forecasts got wrong -- and the taxe de sejour on top.
    arrival = free_window(room["id"], nights=2, after_days=30, clear_of_ateliers=True,
                          clear_of_rate_overrides=True)
    departure = arrival + timedelta(days=2)
    conn = db()
    with m.app.test_request_context("/"):
        ref, _tok = m.create_booking(
            conn, room, TAG + " Forecast", "zzowed.forecast@example.invalid", "",
            arrival, departure, 2, "", [], total_price_override=900.0,
            discount_amount_override=100.0, confirm_now=True)
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (ref,)).fetchone()
    m.add_booking_extra(conn, "room", booking["id"], "ztest late bottle", 1,
                        unit_price=40.0)
    conn.execute("UPDATE bookings SET balance_due_date = ? WHERE id = ?",
                 ((arrival - timedelta(days=7)).isoformat(), booking["id"]))
    conn.commit()
    bill = m.booking_bill(conn, booking["id"])
    ahead = m.money_ahead(conn, days=90)
    due = m.money_due(conn, weeks=12)
    conn.close()
    s.check("the stay carries a discount, a later extra and the tax",
            (booking["discount_amount"] or 0) == 100 and bill["owed"] > 900,
            detail=f"owed {bill['owed']}, lines {[(l['label'], l['amount']) for l in bill['lines']]}")
    forecast = next((i["amount"] for i in ahead["incoming"] if i["ref"] == ref), None)
    s.check("the forecast of money coming in is what the bill says is owed",
            forecast is not None and abs(forecast - bill["owed"]) < 0.01,
            detail=f"forecast {forecast}, bill {bill['owed']} — it took the discount "
                   "off total_price a second time, and missed the extra")
    listed = next((i["amount"] for i in due["csv"]
                   if i["who"] == TAG + " Forecast" and i["what"] == "Room balance"), None)
    s.check("and so is the money-due list",
            listed is not None and abs(listed - bill["owed"]) < 0.01,
            detail=f"listed {listed}, bill {bill['owed']} — total_price less paid "
                   "left out the tax and the extra")

    s.section("No tax on nights nobody spent")
    gone = _past_stay("NS", room["id"], city_tax=13.20, paid=0)
    # Something charged to the stay after it was booked, so the bill and
    # "total_price less paid" give different answers and the check below can
    # tell which one the message quoted.
    conn = db()
    m.add_booking_extra(conn, "room", gone["id"], "ztest minibar", 1, unit_price=25.0)
    conn.commit()
    conn.close()
    r = oc.post(f"/admin/bookings/{gone['id']}/no-show",
                data={"note": "never came"}, follow_redirects=True)
    msg = " ".join(flashes(r))
    conn = db()
    bill = m.booking_bill(conn, gone["id"])
    statement = m.guest_statement(conn, _row(gone["id"]))
    working = m.city_tax_working(conn, today - timedelta(days=10), today + timedelta(days=1))
    conn.close()
    s.check("the bill carries no taxe de sejour for a stay nobody arrived for",
            not any(l["kind"] == "city_tax" for l in bill["lines"]),
            detail=str([(l["label"], l["amount"]) for l in bill["lines"]]))
    s.check("nor does the guest's statement", statement["city_tax"] == 0,
            detail=str(statement["city_tax"]))
    s.check("and the stay is not on the return to the commune",
            not any(r_["reference"] == gone["reference_code"] for r_ in working["rows"]),
            detail="cancelled stays were always left out for having no nights; a "
                   "no-show has none either")
    s.check("the message quotes the bill, the tax already off it",
            f"{bill['owed']:,.2f}" in msg and "taxe de sejour is off the bill" in msg,
            detail=msg)
    oc.post(f"/admin/bookings/{gone['id']}/no-show/undo", follow_redirects=True)
    conn = db()
    back = m.booking_bill(conn, gone["id"])
    conn.close()
    s.check("and if the mark is undone, the tax comes back",
            any(l["kind"] == "city_tax" and l["amount"] == 13.20 for l in back["lines"]),
            detail=str([(l["label"], l["amount"]) for l in back["lines"]]))

    untaxed = _past_stay("NT", room["id"], city_tax=0, paid=0)
    oc.post(f"/admin/bookings/{untaxed['id']}/no-show", follow_redirects=True)
    conn = db()
    arrears = m.city_tax_arrears(conn)
    with m.app.test_request_context("/"):
        ok, said = m.charge_city_tax_now(conn, untaxed["id"])
    conn.close()
    s.check("a no-show is not listed as tax still to charge",
            not any(x["booking"]["id"] == untaxed["id"]
                    for x in arrears["upcoming"] + arrears["departed"]))
    s.check("and charging it anyway is refused, saying why",
            not ok and "Nobody arrived" in said, detail=said)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
