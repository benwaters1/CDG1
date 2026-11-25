# -*- coding: utf-8 -*-
"""Booking.com's stays in the VAT working and the taxe de séjour return.

The owner, 23 November 2026, on the two gaps found that day: the VAT working
and the taxe de séjour return both read the house's own bookings and nothing
else, so every stay sold on Booking.com -- €9,944 of rooms that year -- was in
neither. Both now read them, from Booking.com's reservations statement.

And on the VAT: "this is for 1 Jan, we aren't registered yet". So the house
has a date it is registered for VAT from (1 January 2027, on the rates page),
and a period before it says there is nothing to declare rather than showing
figures laid out like a return.

WHAT IS HELD HERE

  THE REGISTRATION DATE. A period wholly before it has no lines and says so;
  one that straddles it is counted from that day, and says that too.

  BOOKING.COM ROOMS IN THE VAT. Counted at the accommodation rate from the
  statement's price, by arrival. A stay with no statement yet is named, not
  counted as nothing. A cancelled stay is not money. A stay the house also
  entered as its own booking is not counted twice. The commission is shown
  beside the total and never in it.

  BOOKING.COM NIGHTS IN THE TAXE DE SÉJOUR. Clipped to the period like any
  stay; adults and children from the statement; with no statement, the
  email's guest count, all taken as adults and marked as not confirmed.

Figures are compared before and after the fixtures go in, so whatever else
the copy of the house's database holds in those months does not matter.
"""
import csv
import io
from datetime import date, datetime, timezone

from _harness import Suite, clients, db, ensure_room, visible_text
import _harness

m = _harness.m
TAG = "ZZBRET"
NUM = "97455"          # reservation numbers 97455xxxxx belong to this suite


def _cleanup(conn):
    conn.execute(f"DELETE FROM ota_reservations WHERE reservation_number LIKE '{NUM}%'")
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.commit()


def _ota(conn, number, room_id, arrive, leave, *, status="confirmed", amount=None,
         commission=None, adults=None, children=None, guests=2, statement=True,
         cancelled_after=False):
    now = datetime.now(timezone.utc).isoformat()
    statement_at = "2031-01-01T10:00:00+00:00" if statement else None
    # A cancellation the statement has not seen since: the cancel came later.
    last_event = "2031-02-01T10:00:00+00:00" if cancelled_after else "2030-12-01T10:00:00+00:00"
    conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
               arrival_date, departure_date, room_id, guests, amount, currency,
               commission_amount, adults, children, statement_at,
               first_seen_at, last_event_at, updated_at)
           VALUES ('booking.com', ?, ?, ?, ?, ?, ?, ?, ?, 'EUR', ?, ?, ?, ?, ?, ?, ?)""",
        (number, status, f"{TAG} {number[-3:]}", arrive, leave, room_id, guests, amount,
         commission, adults, children, statement_at, now, last_event, now))
    conn.commit()


def _own_booking(conn, room_id, ref, arrive, leave, price=900.0):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, guest_phone, arrival_date, departure_date, party_size,
               guests_under_18, status, total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'zzbret@example.invalid', '', ?, ?, 2, 0, 'confirmed',
                   ?, ?, 0, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} own {ref}",
         arrive, leave, price, price, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _set_registered(conn, value):
    conn.execute("""INSERT INTO app_settings (key, value) VALUES ('tax_vat_registered_from', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (value,))
    conn.commit()


def _line(working, source):
    return next((l for l in working["lines"] if l["source"] == source), None)


def run():
    s = Suite("Booking.com's stays in the VAT working and the taxe de séjour return")
    oc, ec, _owner, _emp = clients()
    conn = db()
    held = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'tax_vat_registered_from'").fetchone()
    _cleanup(conn)
    try:
        _run(s, oc, ec, conn)
    finally:
        _cleanup(conn)
        if held is None:
            conn.execute("DELETE FROM app_settings WHERE key = 'tax_vat_registered_from'")
        else:
            _set_registered(conn, held["value"])
        conn.commit()
        conn.close()
    return s


def _run(s, oc, ec, conn):
    room = ensure_room()
    rid = room["id"]
    acc = m.tax_rate(conn, "vat_accommodation")

    # ---------------------------------------------------------------------
    s.section("Registered for VAT from 1 January 2027")
    conn.execute("DELETE FROM app_settings WHERE key = 'tax_vat_registered_from'")
    conn.commit()
    s.check("the date is the owner's word, 1 January 2027, until they change it",
            m.vat_registered_from(conn) == date(2027, 1, 1),
            detail=str(m.vat_registered_from(conn)))
    _own_booking(conn, rid, "OCT", "2026-10-10", "2026-10-12")
    before = m.vat_working(conn, date(2026, 10, 1), date(2026, 11, 1))
    s.check("a month before it has nothing to declare, and says so",
            before["not_registered"] and before["lines"] == [] and before["total_vat"] == 0,
            detail=str([l["source"] for l in before["lines"]]))
    s.check("even with a stay in it", conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE reference_code = ?", (f"{TAG}-OCT",)
    ).fetchone()[0] == 1)
    page = visible_text(oc.get("/admin/vat?period=month&date=2026-10-15").get_data(as_text=True))
    s.check("the page says there is nothing to declare, and from when",
            "Nothing in this period is VAT to declare" in page and "registered for VAT from" in page,
            detail=page[:300])

    _own_booking(conn, rid, "DEC", "2026-12-20", "2026-12-22", price=500.0)
    _own_booking(conn, rid, "JAN", "2027-01-20", "2027-01-22", price=700.0)
    straddle = m.vat_working(conn, date(2026, 12, 1), date(2027, 3, 1))
    rooms = _line(straddle, "Rooms")
    s.check("a quarter that straddles it is counted from that day",
            straddle["counted_from_registration"] and not straddle["not_registered"]
            and straddle["start"] == date(2027, 1, 1), detail=str(straddle["start"]))
    jan = _line(m.vat_working(conn, date(2027, 1, 1), date(2027, 3, 1)), "Rooms")
    s.check("so December's stay is not in it and January's is",
            rooms is not None and jan is not None and rooms["gross"] >= 700.0 - 0.01
            and abs(rooms["gross"] - jan["gross"]) < 0.01,
            detail=f"{rooms} against {jan}")
    page = visible_text(oc.get("/admin/vat?period=week&date=2027-01-01").get_data(as_text=True))
    s.check("the page says where the counting starts",
            "when the house is registered for VAT" in page, detail=page[:300])

    s.section("The date is set on the rates page")
    form = oc.get("/admin/tax").get_data(as_text=True)
    s.check("the rates page has the date", 'name="vat_registered_from"' in form
            and 'value="2027-01-01"' in form)
    current = {k: m.tax_setting(conn, k) for k in m.TAX_DEFAULTS}
    r = oc.post("/admin/tax", data=dict(current, vat_registered_from="2026-06-01"))
    s.check("and saving it moves the day", r.status_code in (302, 303)
            and m.vat_registered_from(conn) == date(2026, 6, 1),
            detail=str(m.vat_registered_from(conn)))
    s.check("so October is a month to declare after all",
            not m.vat_working(conn, date(2026, 10, 1), date(2026, 11, 1))["not_registered"])

    # From here the house is taken as registered, to look at the figures.
    _set_registered(conn, "2020-01-01")

    # ---------------------------------------------------------------------
    s.section("Booking.com's rooms in the VAT working")
    start, end = date(2031, 3, 1), date(2031, 4, 1)
    base = m.vat_working(conn, start, end)
    base_bcom = _line(base, "Rooms sold on Booking.com")
    base_gross = base_bcom["gross"] if base_bcom else 0.0
    base_comm = base["booking_com_commission"]
    _ota(conn, f"{NUM}00001", rid, "2031-03-05", "2031-03-08", amount=1100.0, commission=165.0,
         adults=2, children=0)
    _ota(conn, f"{NUM}00002", rid, "2031-03-10", "2031-03-12", statement=False)
    _ota(conn, f"{NUM}00003", rid, "2031-03-14", "2031-03-16", status="cancelled",
         amount=800.0, commission=120.0, cancelled_after=True)
    _ota(conn, f"{NUM}00004", rid, "2031-03-20", "2031-03-22", amount=600.0, commission=90.0)
    _own_booking(conn, rid, "DUP", "2031-03-20", "2031-03-22", price=600.0)
    _ota(conn, f"{NUM}00005", rid, "2031-04-02", "2031-04-04", amount=999.0, commission=99.0)
    w = m.vat_working(conn, start, end)
    bcom = _line(w, "Rooms sold on Booking.com")
    s.check("a stay sold on Booking.com is in the rooms, at the statement's price",
            bcom is not None and abs(bcom["gross"] - base_gross - 1100.0) < 0.01,
            detail=str(bcom))
    s.check("at the accommodation rate, VAT taken out of the price",
            bcom is not None and bcom["rate"] == acc
            and abs(bcom["vat"] - round(bcom["gross"] - bcom["gross"] / (1 + acc / 100), 2)) < 0.02,
            detail=str(bcom))
    s.check("and the basis says where the price came from",
            bcom is not None and "statement" in bcom["basis"] and not bcom["exact"])
    waiting = [r["reservation_number"] for r in w["booking_com_waiting"]]
    s.check("a stay no statement has covered is named, not counted as nothing",
            f"{NUM}00002" in waiting, detail=str(waiting))
    s.check("a stay cancelled since the statement is not money", f"{NUM}00003" not in
            [r["reservation_number"] for r in m.booking_com_stays_sold(conn, start, end)])
    s.check("a stay the house also entered itself is counted once, as its own",
            f"{NUM}00004" not in
            [r["reservation_number"] for r in m.booking_com_stays_sold(conn, start, end)])
    s.check("a stay arriving next month is next month's",
            f"{NUM}00005" in [r["reservation_number"] for r in
                              m.booking_com_stays_sold(conn, end, date(2031, 5, 1))])
    s.check("the commission is beside the working",
            abs(w["booking_com_commission"] - base_comm - 165.0) < 0.01,
            detail=str(w["booking_com_commission"]))
    s.check("and never in its total: the total is the lines",
            abs(w["total_vat"] - round(sum(l["vat"] for l in w["lines"]), 2)) < 0.01)
    s.check("the same stays as the page about what a booking costs to get",
            {r["reservation_number"] for r in m.booking_com_stays_sold(conn, start, end)}
            >= {f"{NUM}00001", f"{NUM}00002"})
    page = visible_text(oc.get("/admin/vat?period=month&date=2031-03-15").get_data(as_text=True))
    s.check("the page shows the line, the stay waiting and the commission",
            "Rooms sold on Booking.com" in page and "not in the figures yet" in page
            and f"{TAG} 002" in page and "reverse-charged" in page, detail=page[:400])
    body = oc.get("/admin/vat/export.csv?period=month&date=2031-03-15").get_data(as_text=True)
    s.check("the file carries the line, the commission apart, and the stay waiting",
            "Rooms sold on Booking.com" in body
            and "Booking.com commission (not in the total)" in body
            and f"{NUM}00002" in body, detail=body[-400:])

    # ---------------------------------------------------------------------
    s.section("Booking.com's nights in the taxe de séjour")
    rate = m.tax_rate(conn, "city_tax_per_adult_per_night")
    t_start, t_end = date(2031, 6, 1), date(2031, 7, 1)
    t_base = m.city_tax_working(conn, t_start, t_end)
    _ota(conn, f"{NUM}00011", rid, "2031-06-10", "2031-06-13", amount=900.0, commission=135.0,
         adults=2, children=1, guests=3)
    _ota(conn, f"{NUM}00012", rid, "2031-06-29", "2031-07-03", amount=900.0, commission=135.0,
         adults=1, children=0, guests=1)
    _ota(conn, f"{NUM}00013", rid, "2031-06-20", "2031-06-22", statement=False, guests=3)
    _ota(conn, f"{NUM}00014", rid, "2031-06-24", "2031-06-26", status="cancelled",
         amount=500.0, commission=75.0, adults=2)
    _ota(conn, f"{NUM}00015", rid, "2031-06-15", "2031-06-17", amount=500.0, adults=2)
    _own_booking(conn, rid, "TDUP", "2031-06-15", "2031-06-17")
    t = m.city_tax_working(conn, t_start, t_end)
    rows = {r["reference"]: r for r in t["rows"] if r["channel"] == "Booking.com"}
    one = rows.get(f"{NUM}00011")
    s.check("a Booking.com stay is on the return", one is not None, detail=str(list(rows)))
    s.check("its adults and its children from the statement, children exempt",
            one is not None and one["adults"] == 2 and one["exempt_guests"] == 1
            and one["adult_nights"] == 6 and one["exempt_nights"] == 3, detail=str(one))
    s.check("owed at the rate: adult-nights times the rate",
            one is not None and abs(one["amount"] - round(6 * rate, 2)) < 0.01,
            detail=f"{one and one['amount']} at {rate}")
    two = rows.get(f"{NUM}00012")
    s.check("a stay running into the next month owes this month only its nights here",
            two is not None and two["nights"] == 2 and two["total_nights"] == 4,
            detail=str(two and (two["nights"], two["total_nights"])))
    three = rows.get(f"{NUM}00013")
    s.check("with no statement, the email's guests are all counted, and it says so",
            three is not None and three["adults"] == 3 and not three["party_known"]
            and three in t["booking_com"]["party_unknown"], detail=str(three))
    s.check("a cancelled stay spent no nights", f"{NUM}00014" not in rows)
    s.check("a stay the house also entered is on the return once",
            f"{NUM}00015" not in rows
            and any((r["reference"] or "").endswith("TDUP") for r in t["rows"]))
    # 6 + 2 + 6 adult-nights on Booking.com, and the house's own duplicate's
    # 4 (two adults, two nights), which carries no tax charged.
    added = t["adult_nights"] - t_base["adult_nights"]
    s.check("the totals carry them",
            added == 18 and abs(t["total"] - t_base["total"]
                                - (round(6 * rate, 2) + round(2 * rate, 2)
                                   + round(6 * rate, 2))) < 0.02,
            detail=f"{added} adult-nights added, {t['total'] - t_base['total']:.2f} owed")
    s.check("and the Booking.com part is counted on its own",
            t["booking_com"]["stays"] - t_base["booking_com"]["stays"] == 3)
    page = visible_text(oc.get("/admin/city-tax?period=month&date=2031-06-15").get_data(as_text=True))
    s.check("the page says which stays were booked on Booking.com, and which are not confirmed",
            "booked on Booking.com" in page and "not covered by a statement yet" in page
            and f"{TAG} 013" in page, detail=page[:400])
    body = oc.get("/admin/city-tax/export.csv?period=month&date=2031-06-15").get_data(as_text=True)
    lines = {r["reference"]: r for r in csv.DictReader(io.StringIO(body))}
    s.check("the file to the commune says where each stay was booked",
            lines.get(f"{NUM}00011", {}).get("booked_through") == "Booking.com"
            and lines.get(f"{NUM}00013", {}).get("booked_through")
            == "Booking.com (party not confirmed)", detail=str(lines.get(f"{NUM}00013")))
    s.check("and does not call a Booking.com stay settled or unpaid",
            lines.get(f"{NUM}00011", {}).get("settled") == "not charged through the app")

    s.section("Staff are not shown either")
    s.check("the VAT working is the owner's",
            ec.get("/admin/vat").status_code in (302, 303, 403))
    s.check("so is the taxe de séjour", ec.get("/admin/city-tax").status_code in (302, 303, 403))
