"""VAT by the extra, and a board that is food at one rate and wine at another.

Every extra went through at one rate, the house's extras rate. That is the
safe reading for a crémant and charcuterie board -- the higher rate on the
whole -- but it is not the only reading, and the owner asked for the one that
is right: the charcuterie at the food rate and the wine at the wine rate.

  AN EXTRA CARRIES ITS OWN RATE, and may carry a part of its price at a
  second rate -- the wine's share. Blank is the house's extras rate, which is
  what every extra was before, so nothing already sold moves.

  AND THE RATE IS COPIED ONTO THE LINE when it is sold, like the revenue
  account. A rate changed next year must not restate what a guest was
  charged this year.

  THREE PLACES READ IT: the guest's statement (their VAT record), the VAT
  working, and the lines sent to Pennylane. All three split the board.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, free_window
import _harness

m = _harness.m
TAG = "ztest-vat"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM bookings WHERE guest_email LIKE 'ztest-vat-%'").fetchall()]
    for bid in ids:
        conn.execute("DELETE FROM booking_extras WHERE category = 'room' AND booking_id = ?",
                     (bid,))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE 'ztest-vat-%'")
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _extra(name):
    conn = db()
    try:
        return conn.execute("SELECT * FROM extras WHERE name = ?", (name,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("VAT by the extra")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    # The whole row: pricing a stay reads price_per_night, which ensure_room's
    # own answer does not carry.
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?",
                        (_harness.ensure_room()["id"],)).fetchone()
    conn.close()
    today = m.house_today()

    s.section("Set on the catalogue")
    oc.post("/admin/extras/new", data={
        "name": TAG + " board", "price": "80", "category": "food",
        "guest_bookable": "on", "vat_rate": "10", "vat_part_amount": "30",
        "vat_part_rate": "20"})
    board = _extra(TAG + " board")
    s.check("an extra can carry its own rate, and a part at another",
            board and board["vat_rate"] == 10 and board["vat_part_amount"] == 30
            and board["vat_part_rate"] == 20,
            detail=str({k: board[k] for k in ("vat_rate", "vat_part_amount",
                                              "vat_part_rate")}) if board else "not saved")
    for label, form, said in (
            ("a part bigger than the price", {"vat_part_amount": "95", "vat_part_rate": "20"},
             "more than the price"),
            ("a rate that is not a rate", {"vat_rate": "twenty"}, "0 to 100"),
            ("a part with no rate for it", {"vat_part_amount": "30"}, "which rate")):
        r = oc.post("/admin/extras/new", data=dict({
            "name": TAG + " refused", "price": "80", "category": "food"}, **form),
            follow_redirects=True)
        s.check(f"{label} is refused, saying why",
                _extra(TAG + " refused") is None
                and any(said in f for f in flashes(r)), detail=str(flashes(r)))
    oc.post("/admin/extras/new", data={"name": TAG + " plain", "price": "15",
                                       "category": "food", "guest_bookable": "on"})
    plain = _extra(TAG + " plain")
    s.check("left blank, it is the house's extras rate, as every extra was",
            plain and plain["vat_rate"] is None and plain["vat_part_amount"] is None)

    s.section("Copied onto the line when it is sold")
    arrival = free_window(room["id"], nights=2, after_days=35, clear_of_ateliers=True,
                          clear_of_rate_overrides=True)
    conn = db()
    with m.app.test_request_context("/"):
        ref, _tok = m.create_booking(
            conn, room, "Ztest Vat", "ztest-vat-a@example.invalid", "", arrival,
            arrival + timedelta(days=2), 2, "", [board, plain], confirm_now=True)
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (ref,)).fetchone()
    line = conn.execute("SELECT * FROM booking_extras WHERE booking_id = ? AND name = ?",
                        (booking["id"], TAG + " board")).fetchone()
    s.check("the line carries the board's rates",
            line and line["vat_rate"] == 10 and line["vat_part_amount"] == 30
            and line["vat_part_rate"] == 20)
    conn.execute("UPDATE extras SET vat_rate = 5.5, vat_part_amount = NULL WHERE id = ?",
                 (board["id"],))
    conn.commit()
    again = conn.execute("SELECT * FROM booking_extras WHERE id = ?", (line["id"],)).fetchone()
    s.check("and keeps them when the catalogue changes later",
            again["vat_rate"] == 10 and again["vat_part_amount"] == 30,
            detail="a rate changed next year must not restate this year's bill")

    s.section("On the guest's statement")
    statement = m.guest_statement(conn, booking)
    bands = {b["rate"]: b for b in statement["vat"]}
    room_vat = m.tax_rate(conn, "vat_accommodation")
    s.check("the wine's share is at the wine's rate",
            20.0 in bands and abs(bands[20.0]["gross"] - (30.0 + 15.0)) < 0.01,
            detail=f"20%: {bands.get(20.0)} — the board's 30 and the plain extra's 15")
    s.check("and the charcuterie at the food rate",
            10.0 in bands and abs(bands[10.0]["gross"] - (statement["accommodation"] + 50.0)
                                  if room_vat == 10 else bands[10.0]["gross"] - 50.0) < 0.01,
            detail=f"10%: {bands.get(10.0)}")
    s.check("the bands still add up to what the guest was charged",
            abs(sum(b["gross"] for b in statement["vat"])
                - (statement["accommodation"] + statement["extras_total"])) < 0.01)

    s.section("On the VAT working, and to Pennylane")
    lines = m.vat_working(conn, today, today + timedelta(days=1))["lines"]
    extras = {l["rate"]: l["gross"] for l in lines if l["source"] == "Extras"}
    s.check("the working shows the extras at each rate",
            extras.get(10.0, 0) >= 50.0 - 0.01 and extras.get(20.0, 0) >= 45.0 - 0.01,
            detail=str(extras))
    penny = m.booking_pennylane_lines(conn, statement)
    penny_lines = penny["lines"] if isinstance(penny, dict) else penny
    extras_lines = [l for l in penny_lines if " at " in l["label"]
                    and "Extras" in l["label"]]
    s.check("and Pennylane gets the board as two lines, one a rate",
            len({l["vat_rate"] for l in extras_lines}) >= 2,
            detail=str([(l["label"], l["currency_amount"], l["currency_tax"])
                        for l in extras_lines]))
    conn.close()

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
