"""The stays carrying no taxe de sejour, and charging them.

Nothing wrote the city_tax column until recently, so every stay taken before
that carries the NOT NULL DEFAULT 0. Those nights happened and the commune's
share of them was never collected. Nothing invents the figure on a bill or in
the declaration -- a charge the house never asked for cannot be added to a
document after the fact -- so the decision is the owner's, and this is the list
they make it from.

The split is the point. A stay still to come can be charged and the guest pays it
with the rest. Charging one that has departed and settled creates a balance on a
finished stay, and the balance chase will then email a guest months after they
went home. So there is a one-press button for the first group and deliberately
not for the second, and that asymmetry is what most of these checks are about.
"""
import io
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZARR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, days_from_today, nights=2, party=2, under18=0, paid=0.0,
          tax=None, status="confirmed"):
    """A stay with NO city tax stamped, unless one is asked for."""
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 AND max_occupancy >= ? "
                        "ORDER BY id LIMIT 1", (party,)).fetchone()
    arrival = date.today() + timedelta(days=days_from_today)
    departure = arrival + timedelta(days=nights)
    priced = m.compute_room_total(conn, room, arrival, departure)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           guests_under_18, status, total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'zzarr@example.invalid', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         arrival.isoformat(), departure.isoformat(), party, under18, status,
         priced, paid if paid else (priced if paid is None else paid),
         tax if tax is not None else 0,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _row(bid):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    finally:
        conn.close()


def _arrears():
    conn = db()
    try:
        return m.city_tax_arrears(conn)
    finally:
        conn.close()


def _mine(arrears, key):
    return [r for r in arrears[key] if TAG in (r["booking"]["guest_name"] or "")]


def run():
    s = Suite("Taxe de sejour arrears")
    _cleanup()
    oc, ec, owner, emp = clients()

    conn = db()
    rate = m.tax_rate(conn, "city_tax_per_adult_per_night")
    conn.close()

    future = _stay("FUTURE", days_from_today=40, nights=3, party=4, under18=1)
    gone = _stay("GONE", days_from_today=-30, nights=2, party=2)
    exempt = _stay("EXEMPT", days_from_today=50, nights=2, party=2, under18=2)
    carried = _stay("CARRIED", days_from_today=60, nights=2, party=2, tax=3.20)
    pending = _stay("PENDING", days_from_today=70, nights=2, party=2, status="pending")

    s.section("The list finds the stays with none")
    a = _arrears()
    up = _mine(a, "upcoming")
    dep = _mine(a, "departed")
    s.check("a stay still to come is listed", len(up) == 1,
            detail=f"{[r['booking']['guest_name'] for r in up]}")
    s.check("with what it would come to",
            up and abs(up[0]["would_be"] - round(3 * 3 * rate, 2)) < 0.01,
            detail=f"{up[0]['would_be'] if up else None} for 4 guests less 1 child "
                   "over 3 nights")
    s.check("a departed stay is listed separately", len(dep) == 1,
            detail=f"{[r['booking']['guest_name'] for r in dep]} — charging one "
                   "that has gone is a different decision from charging one that "
                   "has not arrived")

    s.section("And leaves alone what it should")
    names = [r["booking"]["guest_name"] for r in a["upcoming"] + a["departed"]]
    s.check("a stay that already carries the tax is not offered again",
            f"{TAG} CARRIED" not in names,
            detail="charging it twice would double what the commune is told")
    s.check("a stay where every guest is exempt is not listed",
            f"{TAG} EXEMPT" not in names,
            detail="a row offering to charge zero is a row nobody can act on")
    s.check("and an unconfirmed enquiry is not listed",
            f"{TAG} PENDING" not in names,
            detail="the commune is owed for stays, not for enquiries")

    s.section("Charging one stay puts it on that stay only")
    before_others = {r["booking"]["id"]: r["would_be"] for r in a["upcoming"]}
    r = oc.post(f"/admin/city-tax/charge/{future['id']}", follow_redirects=True)
    after = _row(future["id"])
    s.check("the tax is now on it",
            abs(float(after["city_tax"] or 0) - round(3 * 3 * rate, 2)) < 0.01,
            detail=f"{after['city_tax']}")
    s.check("the message shows the working",
            any("adult" in f.lower() and "night" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]} — a figure with no working invites an "
                   "argument with a guest")
    s.check("it drops off the list", not _mine(_arrears(), "upcoming"),
            detail="the list would offer it again and charge twice")
    s.check("and the departed one is untouched",
            not float(_row(gone["id"])["city_tax"] or 0),
            detail="one press charged more than one stay")

    s.section("Charging it a second time is refused")
    r = oc.post(f"/admin/city-tax/charge/{future['id']}", follow_redirects=True)
    s.check("the figure does not double",
            abs(float(_row(future["id"])["city_tax"] or 0)
                - round(3 * 3 * rate, 2)) < 0.01,
            detail=f"{_row(future['id'])['city_tax']}")
    s.check("and it says why",
            any("already" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("It appears on the bill, and the bill still adds up")
    conn = db()
    bill = m.booking_bill(conn, future["id"])
    conn.close()
    s.check("as its own line",
            any(l.get("kind") == "city_tax" for l in bill["lines"]),
            detail="charged and invisible to the guest")
    s.check("and the rows add up to the total",
            abs(sum(l["amount"] for l in bill["lines"]) - bill["total"]) < 0.005,
            detail=f"{sum(l['amount'] for l in bill['lines'])} vs {bill['total']}")

    s.section("And in the declaration for its month")
    conn = db()
    arrival = date.fromisoformat(_row(future["id"])["arrival_date"])
    start = date(arrival.year, arrival.month, 1)
    end = (date(arrival.year + 1, 1, 1) if arrival.month == 12
           else date(arrival.year, arrival.month + 1, 1))
    working = m.city_tax_working(conn, start, end)
    conn.close()
    s.check("the stay is a row on the return",
            any(TAG in (row["guest"] or "") for row in working["rows"]),
            detail="charged, and the commune is still told nothing")

    s.section("Charge-all takes the future stays and not the past ones")
    _stay("F2", days_from_today=80, nights=1, party=2)
    _stay("F3", days_from_today=90, nights=2, party=3)
    before_gone = float(_row(gone["id"])["city_tax"] or 0)
    r = oc.post("/admin/city-tax/charge-upcoming", follow_redirects=True)
    s.check("both future stays are charged", not _mine(_arrears(), "upcoming"),
            detail=f"{[x['booking']['guest_name'] for x in _mine(_arrears(), 'upcoming')]}")
    s.check("the departed stay is still untouched",
            abs(float(_row(gone["id"])["city_tax"] or 0) - before_gone) < 0.005,
            detail="one press put a balance on a stay that went home months ago, "
                   "and the chase will email them about it")
    s.check("and the owner is told how many and how much",
            any("total" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("There is no one-press button for the departed ones")
    body = oc.get("/admin/city-tax").get_data(as_text=True)
    s.check("the page shows them", "Already departed" in body,
            detail="the nights happened and nothing says so")
    s.check("with no charge-all among them",
            body.count("charge-upcoming") <= 1,
            detail="a second charge-all button would reach the departed stays")
    s.check("and says why each is its own decision",
            "own" in body.lower() and "chase" in body.lower(),
            detail="a button with no warning next to it will be pressed")

    s.section("A departed stay can still be charged, one at a time")
    r = oc.post(f"/admin/city-tax/charge/{gone['id']}", follow_redirects=True)
    s.check("it takes the charge",
            float(_row(gone["id"])["city_tax"] or 0) > 0,
            detail=f"{_row(gone['id'])['city_tax']} — the owner's call, and the "
                   "app should not refuse it")

    s.section("When there is nothing left, the page says so")
    # Enforced on the SOURCE, the way test_table_overflow is, rather than by
    # rendering and hoping the database is quiet. In a full run other suites
    # leave confirmed stays with no tax behind, so a rendered check here passed
    # or failed on which suites ran first -- which is not a fact about this
    # feature. The claim being made is that the template HAS an empty state.
    src = io.open("templates/admin_city_tax.html", encoding="utf-8").read()
    s.check("the panel has an empty branch", "{% elif arrears %}" in src,
            detail="a panel that can never be empty becomes furniture")
    s.check("and it says something rather than rendering blank",
            "Nothing outstanding" in src,
            detail="an empty panel with no words in it reads as a broken page")
    conn = db()
    quiet = m.city_tax_arrears(conn)
    conn.close()
    s.check("and the count is a real count, not a constant",
            quiet["count"] == len(quiet["upcoming"]) + len(quiet["departed"]),
            detail=f"{quiet['count']}")

    s.section("Guards")
    left = _stay("GUARD", days_from_today=100, nights=2, party=2)
    s.check("an employee cannot charge a stay",
            ec.post(f"/admin/city-tax/charge/{left['id']}",
                    follow_redirects=False).status_code in (302, 403))
    s.check("nor charge them all",
            ec.post("/admin/city-tax/charge-upcoming",
                    follow_redirects=False).status_code in (302, 403))
    s.check("and nothing was charged by trying",
            not float(_row(left["id"])["city_tax"] or 0))
    s.check("a booking that does not exist is refused, not a 500",
            oc.post("/admin/city-tax/charge/999999",
                    follow_redirects=True).status_code == 200)

    _cleanup()
    return s
