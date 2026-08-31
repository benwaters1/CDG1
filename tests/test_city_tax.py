"""Taxe de séjour, as the commune asks for it.

Collected from the guest on the château's behalf of the commune and handed over
with a periodic return. Everything needed for that return was already in the
database and there was nowhere to read it: vat_working leaves the tourist tax
out on purpose — it is not the château's revenue — and nothing else picked it
up, so the figure had to be rebuilt from the bookings by hand each time it fell
due.

THE ONE ARITHMETIC THAT MATTERS is a stay that straddles the period boundary.
A guest arriving on the 28th of September and leaving on the 3rd of October owes
three nights to September and two to October. Counting the whole stay in either
month is wrong, and wrong in the particular way that nobody notices until two
returns disagree with the bank — so it is the first thing checked here, from
both sides of the boundary, with the two halves required to add up to the whole.

THE SECOND THING is that the amount is apportioned from what the guest was
ACTUALLY CHARGED rather than recomputed at today's rate. If the commune raises
the rate mid-year, recomputing would put a figure on the return that never
appeared on anybody's bill — the same fault as the room discount that reached
the statement and not the bill.

Under-18s are exempt and are counted separately, because the return asks for
exempt nights as well as chargeable ones. A cancelled stay owes nothing: no
nights were spent. An unpaid stay still owes — the tax is due on the nights, not
on our success in collecting them — but it is reported so the house can see what
it is handing over ahead of receiving.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZCTAX"
# Far enough out that no other suite's fixture shares these nights.
BASE = date(2098, 9, 1)


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


# This fixture WRITES city_tax itself, and that is deliberate here: these checks
# are about the declaration's arithmetic - nights clipped to the period, the
# amount apportioned across a stay that straddles two months - and that needs
# controlled figures rather than whatever the rate happens to be.
#
# It is also how the missing write went unnoticed for so long. Nothing in the app
# wrote that column, so the declaration reported nothing collected, and this
# suite passed throughout because it supplied the value the app should have. The
# app's own write path is covered in test_city_tax_charged, which books through
# the form and never touches either column by hand. Keep it that way: if this
# file is ever the only place city_tax is set, the feature is dead again and
# green.
def _stay(ref, arrive, nights, *, party=2, under18=0, city_tax=None,
          status="confirmed", paid=None):
    conn = db()
    room = _harness.ensure_room()
    depart = arrive + timedelta(days=nights)
    adults = max(0, party - under18)
    rate = m.tax_rate(conn, "city_tax_per_adult_per_night")
    tax = city_tax if city_tax is not None else round(adults * nights * rate, 2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           guests_under_18, status, total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'zzctax@example.invalid', '', ?, ?, ?, ?, ?, 900, ?, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         arrive.isoformat(), depart.isoformat(), party, under18, status,
         900 if paid is None else paid, tax,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _working(start, end):
    conn = db()
    try:
        w = m.city_tax_working(conn, start, end)
    finally:
        conn.close()
    w["rows"] = [r for r in w["rows"] if (r["reference"] or "").startswith(TAG)]
    w["nights"] = sum(r["nights"] for r in w["rows"])
    w["adult_nights"] = sum(r["adult_nights"] for r in w["rows"])
    w["exempt_nights"] = sum(r["exempt_nights"] for r in w["rows"])
    w["total"] = round(sum(r["amount"] for r in w["rows"]), 2)
    return w


def _row(w, ref):
    return next((r for r in w["rows"] if r["reference"] == f"{TAG}-{ref}"), None)


SEP = (date(2098, 9, 1), date(2098, 10, 1))     # end exclusive
OCT = (date(2098, 10, 1), date(2098, 11, 1))


def run():
    s = Suite("Taxe de séjour")
    _cleanup()
    oc, ec, owner, emp = clients()

    conn = db()
    rate = m.tax_rate(conn, "city_tax_per_adult_per_night")
    conn.close()
    s.check("the commune's rate is set", rate > 0, detail=f"{rate}")

    s.section("A stay that straddles the boundary is split, not double-counted")
    # 28 Sept to 3 Oct: three nights in September, two in October.
    _stay("SPAN", date(2098, 9, 28), 5, party=2)
    sep, oct_ = _working(*SEP), _working(*OCT)
    span_sep, span_oct = _row(sep, "SPAN"), _row(oct_, "SPAN")
    s.check("it appears in September", bool(span_sep))
    s.check("and in October", bool(span_oct))
    if span_sep and span_oct:
        s.check("with three nights in September", span_sep["nights"] == 3,
                detail=f"{span_sep['nights']}")
        s.check("and two in October", span_oct["nights"] == 2,
                detail=f"{span_oct['nights']}")
        s.check("the two halves add up to the whole stay",
                span_sep["nights"] + span_oct["nights"] == 5,
                detail=f"{span_sep['nights']} + {span_oct['nights']}")
        s.check("and so does the money, to the penny",
                abs((span_sep["amount"] + span_oct["amount"])
                    - round(2 * 5 * rate, 2)) < 0.02,
                detail=f"{span_sep['amount']} + {span_oct['amount']} vs "
                       f"{round(2 * 5 * rate, 2)} — a night charged twice across "
                       "a boundary is the one mistake a return must not make")
        s.check("each row says how much of the stay it is covering",
                span_sep["total_nights"] == 5 and span_oct["total_nights"] == 5,
                detail="the working cannot be checked without it")

    s.section("Children are exempt and counted separately")
    _stay("FAMILY", date(2098, 9, 10), 2, party=4, under18=2)
    w = _working(*SEP)
    fam = _row(w, "FAMILY")
    s.check("the family is on the return", bool(fam))
    if fam:
        s.check("two adults are chargeable", fam["adults"] == 2, detail=f"{fam['adults']}")
        s.check("and two nights each", fam["adult_nights"] == 4,
                detail=f"{fam['adult_nights']}")
        s.check("the children are counted as exempt, not ignored",
                fam["exempt_nights"] == 4, detail=f"{fam['exempt_nights']} — the "
                                                  "return asks for exempt nights too")
        s.check("and charged for the adults only",
                abs(fam["amount"] - round(2 * 2 * rate, 2)) < 0.01,
                detail=f"{fam['amount']} vs {round(2 * 2 * rate, 2)}")

    s.section("The figure comes from what was charged, not today's rate")
    # A stay charged at an old rate must appear at that rate. Recomputing would
    # put a number on the return that was never on anybody's bill.
    _stay("OLDRATE", date(2098, 9, 20), 2, party=2, city_tax=1.00)
    w = _working(*SEP)
    old = _row(w, "OLDRATE")
    s.check("it is on the return", bool(old))
    if old:
        s.check("at the euro it was actually charged",
                abs(old["amount"] - 1.00) < 0.01,
                detail=f"{old['amount']} — recomputed at {rate} it would be "
                       f"{round(2 * 2 * rate, 2)}, a figure no guest ever saw")

    s.section("What is not owed")
    _stay("CANX", date(2098, 9, 5), 2, status="cancelled")
    w = _working(*SEP)
    s.check("a cancelled stay owes nothing", _row(w, "CANX") is None,
            detail="no nights were spent")
    _stay("AFTER", date(2098, 11, 5), 2)
    w = _working(*SEP)
    s.check("nor does one outside the period", _row(w, "AFTER") is None)

    s.section("An unpaid stay is still declared")
    # The tax is due on the nights, not on our collecting it.
    _stay("UNPAID", date(2098, 9, 14), 2, paid=0)
    w = _working(*SEP)
    unpaid = _row(w, "UNPAID")
    s.check("it is on the return", bool(unpaid),
            detail="the commune is owed whether or not the guest has paid us")
    if unpaid:
        s.check("and flagged as not settled", not unpaid["settled"],
                detail="the house cannot see what it is handing over ahead of "
                       "receiving")

    s.section("The totals are the sum of the rows")
    w = _working(*SEP)
    s.check("nights add up",
            w["nights"] == sum(r["nights"] for r in w["rows"]))
    s.check("and money adds up",
            abs(w["total"] - round(sum(r["amount"] for r in w["rows"]), 2)) < 0.01,
            detail=f"{w['total']}")

    s.section("The page and the file")
    r = oc.get("/admin/city-tax?period=month&date=2098-09-15")
    s.check("the page opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    html = r.get_data(as_text=True)
    s.check("the straddling stay is on it", f"{TAG} SPAN" in html)
    s.check("it shows the exempt nights as their own figure",
            "Exempt" in html or "exempt" in html)
    s.check("and it has a period control, unlike the VAT page before today",
            "period-bar" in html, detail="no way to pick the quarter being filed")
    r = oc.get("/admin/city-tax/export.csv?period=month&date=2098-09-15")
    s.check("the CSV downloads", r.status_code == 200, detail=f"HTTP {r.status_code}")
    body = r.get_data(as_text=True)
    s.check("one row per stay, not one lump", body.count(f"{TAG}-") >= 4,
            detail=f"{body.count(TAG + '-')} rows — a commune asks about single "
                   "lines and the filed document has to answer")
    s.check("with a TOTAL row to file against", "TOTAL" in body)
    s.check("and the exempt nights in it", "exempt_nights" in body)

    s.section("The VAT page now points here instead of just excusing itself")
    v = oc.get("/admin/vat").get_data(as_text=True)
    s.check("there is a link to the declaration", "/admin/city-tax" in v,
            detail="the page says the tourist tax is elsewhere and never said where")
    s.check("and the VAT page has its own period control back",
            "period-bar" in v,
            detail="it included a partial filename that does not exist, with "
                   "`ignore missing`, so it silently had none")

    s.section("Guards")
    s.check("an employee cannot open the declaration",
            ec.get("/admin/city-tax").status_code in (302, 403))
    s.check("nor download it",
            ec.get("/admin/city-tax/export.csv").status_code in (302, 403))

    _cleanup()
    return s
