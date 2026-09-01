"""A stay reaches the accountant split by what it actually is.

booking_pennylane_lines walked the statement's VAT BANDS -- grouped by rate --
and posted every one of them to pennylane_account_accommodation under the label
"Accommodation and extras". The VAT was right and the revenue analysis was not: a
hamper, a transfer and a bunch of flowers were all booked as room revenue, and
pennylane_account_extras was a setting the owner could fill in that nothing read.

Grouping by rate could not have fixed it either, and that is the check that
matters most here: if the house sets the extras rate equal to the accommodation
rate, both fall into ONE band and nothing about the rate can separate them. So
the split is by component.

Nothing here touches Pennylane. _harness pins _pennylane_request to a function
that raises, and these checks only build the lines.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZPLS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("UPDATE revenue_categories SET ledger_account = NULL "
                 "WHERE ledger_account LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, extras=(), city_tax=0.0):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    arrival = house_today() + timedelta(days=15)
    departure = arrival + timedelta(days=2)
    priced = m.compute_room_total(conn, room, arrival, departure)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', ?, 0, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}@example.invalid", arrival.isoformat(),
         departure.isoformat(), priced, city_tax,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-{ref}",)).fetchone()
    for name, price, qty in extras:
        conn.execute(
            """INSERT INTO booking_extras (category, booking_id, name, unit_price,
               quantity, status, created_at) VALUES ('room', ?, ?, ?, ?, 'confirmed', ?)""",
            (booking["id"], f"{TAG} {name}", price, qty,
             datetime.now(timezone.utc).isoformat()))
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return booking


def _lines(booking):
    conn = db()
    try:
        statement = m.guest_statement(conn, booking)
        return statement, m.booking_pennylane_lines(conn, statement)
    finally:
        conn.close()


def _set_account(key, value):
    conn = db()
    conn.execute("UPDATE revenue_categories SET ledger_account = ? WHERE key = ?",
                 (value, key))
    conn.commit()
    conn.close()


def _set_rate(key, value):
    conn = db()
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, str(value)))
    conn.commit()
    conn.close()


def run():
    s = Suite("A stay, split for the accountant")
    _cleanup()

    conn = db()
    rate_room = m.tax_rate(conn, "vat_accommodation")
    rate_extras = m.tax_rate(conn, "vat_extras")
    original_extras_rate = rate_extras
    conn.close()

    s.section("Nothing here can reach Pennylane")
    s.check("the request function is pinned to one that raises",
            getattr(m._pennylane_request, "__name__", "") != "_pennylane_request",
            detail=f"{getattr(m._pennylane_request, '__name__', '?')} — the token "
                   "is live and these checks build invoice lines")

    # Set on the revenue_categories table, which is what the page writes and
    # every sender reads. The labels are the owner's to rename, so this asks the
    # table what they are called rather than hardcoding "Accommodation" -- a
    # suite that hardcodes them goes red the first time somebody renames one on
    # a settings page, which is not a defect.
    _set_account("nightly", f"{TAG}706100")
    _set_account("extras", f"{TAG}706300")
    _set_account("city_tax", f"{TAG}447100")
    conn = db()
    NAME = {k: m.revenue_category_label(conn, k)
            for k in ("nightly", "extras", "city_tax")}
    conn.close()

    booking = _stay("A", extras=[("hamper", 60.0, 1)], city_tax=4.80)
    statement, lines = _lines(booking)

    s.section("Every part is its own line")
    labels = [l["label"] for l in lines]
    s.check("the stay has one",
            any(l.startswith(NAME["nightly"]) for l in labels), detail=f"{labels}")
    s.check("extras have one of their own",
            any(l.startswith(NAME["extras"]) for l in labels),
            detail=f"{labels} — a hamper was booked as room revenue")
    s.check("and the taxe de sejour",
            any(l.startswith(NAME["city_tax"]) for l in labels), detail=f"{labels}")
    s.check("no line still says 'Accommodation and extras'",
            not any("and extras" in l for l in labels),
            detail=f"{labels} — the old label was at least honest about the merge")

    s.section("Each reaches the account the owner set for it")
    by_label = {l["label"].split(" at ")[0]: l for l in lines}
    s.check("the stay to the nightly account",
            by_label[NAME["nightly"]].get("ledger_account_number") == f"{TAG}706100",
            detail=f"{by_label[NAME['nightly']].get('ledger_account_number')}")
    s.check("extras to the extras account",
            by_label[NAME["extras"]].get("ledger_account_number") == f"{TAG}706300",
            detail=f"{by_label[NAME['extras']].get('ledger_account_number')} — the "
                   "setting existed and nothing read it")
    s.check("and the tax to its own",
            by_label[NAME["city_tax"]].get("ledger_account_number") == f"{TAG}447100",
            detail=f"{by_label[NAME['city_tax']].get('ledger_account_number')}")

    s.section("The lines still add up to the statement")
    total = round(sum(float(l["currency_amount"]) + float(l["currency_tax"])
                      for l in lines), 2)
    s.check("to the cent", abs(total - statement["total"]) < 0.005,
            detail=f"{total} vs {statement['total']} — an invoice that does not "
                   "match the document the guest was given is worse than none")
    s.check("accommodation net plus its VAT is the room total",
            abs(float(by_label[NAME["nightly"]]["currency_amount"])
                + float(by_label[NAME["nightly"]]["currency_tax"])
                - statement["accommodation"]) < 0.02,
            detail=f"{statement['accommodation']}")
    s.check("and extras likewise",
            abs(float(by_label[NAME["extras"]]["currency_amount"])
                + float(by_label[NAME["extras"]]["currency_tax"])
                - statement["extras_total"]) < 0.02,
            detail=f"{statement['extras_total']}")
    s.check("the tax carries no VAT",
            abs(float(by_label[NAME["city_tax"]]["currency_tax"])) < 0.005,
            detail="collected for the commune, and not the house's income")

    s.section("Each carries its own VAT rate")
    s.check("accommodation at the accommodation rate",
            f"{rate_room}" in by_label[NAME["nightly"]]["label"],
            detail=f"{by_label[NAME["nightly"]]['label']} vs {rate_room}")
    s.check("extras at the extras rate",
            f"{rate_extras}" in by_label[NAME["extras"]]["label"],
            detail=f"{by_label[NAME["extras"]]['label']} vs {rate_extras}")

    s.section("They stay separate even when the two rates are the same")
    # The check the old code could never have passed. Grouped by rate, these
    # collapse into one band and nothing about the rate can tell a bed from a
    # hamper -- so the split has to be by component, not by rate.
    _set_rate("vat_extras", rate_room)
    statement, lines = _lines(booking)
    labels = [l["label"] for l in lines]
    s.check("there are still two revenue lines",
            sum(1 for l in labels
                if l.startswith((NAME["nightly"], NAME["extras"]))) == 2,
            detail=f"{labels} — one band, two kinds of revenue, and the ledger "
                   "cannot tell them apart")
    by_label = {l["label"].split(" at ")[0]: l for l in lines}
    s.check("and they still reach different accounts",
            by_label[NAME["nightly"]].get("ledger_account_number")
            != by_label[NAME["extras"]].get("ledger_account_number"),
            detail="both booked to the same account the moment the rates match")
    total = round(sum(float(l["currency_amount"]) + float(l["currency_tax"])
                      for l in lines), 2)
    s.check("and the total is unchanged", abs(total - statement["total"]) < 0.005,
            detail=f"{total} vs {statement['total']}")
    _set_rate("vat_extras", original_extras_rate)

    s.section("A stay with no extras has no extras line")
    plain = _stay("B", city_tax=3.20)
    _statement, lines = _lines(plain)
    s.check("nothing empty is sent",
            not any(l["label"].startswith(NAME["extras"]) for l in lines),
            detail=f"{[l['label'] for l in lines]} — a zero line on an invoice is "
                   "a question the accountant has to ask")
    s.check("and the accommodation line is still there",
            any(l["label"].startswith(NAME["nightly"]) for l in lines))

    s.section("An account nobody has filled in is left off, not guessed")
    conn = db()
    conn.execute("UPDATE revenue_categories SET ledger_account = NULL WHERE key = 'extras'")
    conn.commit()
    conn.close()
    _statement, lines = _lines(booking)
    extras_line = [l for l in lines if l["label"].startswith(NAME["extras"])][0]
    s.check("the line is still sent", extras_line is not None)
    s.check("without a made-up ledger account",
            "ledger_account_number" not in extras_line,
            detail=f"{extras_line.get('ledger_account_number')!r} — a guessed "
                   "account number files revenue against the wrong thing, which "
                   "is harder to unpick than a missing one")

    _cleanup()
    return s
