"""Three money questions the data could always answer and never did.

Spend by supplier: expenses carry a typed vendor name while vendors is a
table, so nobody could total what one supplier had been paid.

What discounts cost: the codes page said a code existed and how often it was
used, never what it gave away.

Held, not earned: deposits are cash in the account and are not income until
the thing happens. Money Ahead counts them as incoming, which for cash flow
is right — these are the other question about the same euros, and the pair
must never be added together.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-money-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM promo_code_redemptions WHERE booking_reference LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.commit()


def _expense(conn, vendor, amount, status="paid", days_ago=10):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
           submitted_at) VALUES ('supplier_invoice', ?, ?, ?, ?, ?)""",
        (vendor, TAG + "line", amount, status, when))
    conn.commit()


def run():
    s = Suite("money reports")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------- spend by supplier
    s.section("What each supplier has been paid")
    conn.execute("INSERT INTO vendors (name, created_at) VALUES (?, ?)",
                 (TAG + "Roofer", now))
    conn.commit()
    _expense(conn, TAG + "Roofer", 1000.0)
    _expense(conn, TAG + "Roofer", 500.0, status="approved")
    _expense(conn, TAG + "Roofer", 9999.0, status="pending")     # not yet money out
    _expense(conn, TAG + "Roofer", 8888.0, status="rejected")    # never money out
    _expense(conn, TAG + "Plumber", 300.0)                       # not on the vendor list

    data = m.spend_by_vendor(conn, start=_iso(-60))
    rows = {r["vendor_name"]: r for r in data["rows"]}
    s.check("approved and paid are counted together",
            rows.get(TAG + "Roofer", {}).get("total") == 1500.0,
            detail=str(rows.get(TAG + "Roofer", {}).get("total")))
    s.check("pending is not counted as money out",
            9999.0 not in [r["total"] for r in data["rows"]])
    s.check("nor is rejected",
            8888.0 not in [r["total"] for r in data["rows"]])
    s.check("the invoice count is right",
            rows.get(TAG + "Roofer", {}).get("invoices") == 2)
    s.check("a supplier on the vendor list is matched",
            rows.get(TAG + "Roofer", {}).get("on_file") is True)
    s.check("one that is not is flagged rather than hidden",
            rows.get(TAG + "Plumber", {}).get("on_file") is False)
    s.check("and it appears in the unmatched list",
            any(r["vendor_name"] == TAG + "Plumber" for r in data["unmatched"]))

    # The same supplier typed two ways must not be silently merged, and must
    # not silently vanish either — both rows have to be visible.
    # Deliberately the most RECENT invoice, and deliberately the wrong
    # spelling. Grouping hands back a bare column from an arbitrary row, so a
    # tie decides nothing — this makes the sloppy spelling the one a naive
    # implementation would show, and the check below then means something.
    _expense(conn, TAG + "roofer  ", 100.0, days_ago=1)
    data = m.spend_by_vendor(conn, start=_iso(-60))
    roofers = [r for r in data["rows"] if "roofer" in r["vendor_name"].lower()]
    s.check("a name typed differently is normalised onto one row",
            len(roofers) == 1, detail=str([r["vendor_name"] for r in roofers]))
    s.check("and its total includes both spellings",
            roofers and roofers[0]["total"] == 1600.0,
            detail=str(roofers[0]["total"]) if roofers else "")
    # Which spelling gets shown must not be luck. Grouping hands back an
    # arbitrary row's name, so the same page could name a supplier differently
    # between two loads — and it did, until the name on the vendor list won.
    s.check("the name shown is the one on the vendor list, not a typo of it",
            roofers and roofers[0]["vendor_name"] == TAG + "Roofer",
            detail=str(roofers[0]["vendor_name"]) if roofers else "")

    # The invariant that makes the page trustworthy.
    s.check("the rows add up to the stated total",
            abs(sum(r["total"] for r in data["rows"]) - data["total"]) < 0.005)

    page = oc.get("/management/spend-by-vendor?months=6").get_data(as_text=True)
    s.check("the page renders", "Spend by supplier" in page)
    s.check("the supplier is on it", TAG + "Roofer" in page,
            detail=("page has %d table rows; engine has %s"
                    % (page.count("<tr>"),
                       [r["vendor_name"] for r in data["rows"]][:6])))
    s.check("and the unmatched one is called out", "not on file" in page)

    # ----------------------------------------------- what discounts cost
    s.section("What the discounting cost")
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
           active, created_at) VALUES (?, ?, 'percent', 10, 1, ?)""",
        (TAG + "TENOFF", TAG + "ten percent", now))
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, discount_value,
           active, created_at) VALUES (?, ?, 'percent', 50, 1, ?)""",
        (TAG + "NEVER", TAG + "unused", now))
    conn.commit()
    code_id = conn.execute("SELECT id FROM promo_codes WHERE code = ?",
                           (TAG + "TENOFF",)).fetchone()["id"]
    for original, discount in ((1000.0, 100.0), (500.0, 50.0)):
        conn.execute(
            """INSERT INTO promo_code_redemptions (promo_code_id, category,
               booking_reference, original_amount, discount_amount, final_amount,
               redeemed_at) VALUES (?, 'room', ?, ?, ?, ?, ?)""",
            (code_id, TAG + "REF", original, discount, original - discount, now))
    conn.commit()

    d = m.discount_cost(conn, start=_iso(-60))
    mine = [r for r in d["rows"] if r["code"] == TAG + "TENOFF"]
    s.check("the code's giveaway is totalled", mine and mine[0]["given"] == 150.0,
            detail=str(mine[0]["given"]) if mine else "")
    s.check("against what those sales were worth before it",
            mine and mine[0]["gross"] == 1500.0)
    s.check("so the share is of the same sales, not of all revenue",
            mine and abs(mine[0]["share"] - 10.0) < 0.01,
            detail=str(mine[0]["share"]) if mine else "")
    s.check("gross minus given equals net",
            mine and abs((mine[0]["gross"] - mine[0]["given"]) - mine[0]["net"]) < 0.005)
    s.check("a code nobody used is listed rather than omitted",
            any(r["code"] == TAG + "NEVER" for r in d["unused"]))
    s.check("and it is not counted as a cost",
            not any(r["code"] == TAG + "NEVER" for r in d["rows"]))

    page = oc.get("/management/discounts?months=6").get_data(as_text=True)
    s.check("the page renders", "What discounts cost" in page)
    s.check("the code is on it", TAG + "TENOFF" in page)
    s.check("and the unused one too", TAG + "NEVER" in page)

    # ------------------------------------------------ held, not earned
    s.section("Money held but not earned")
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]

    def _booking(ref, arrival, paid, status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status,
               total_price, amount_paid, created_at)
               VALUES (?, ?, ?, 'Payer', 'p@example.invalid', ?, ?, 2, ?, 900, ?, ?)""",
            (room, ref, ref + "tok", arrival,
             (m.parse_date(arrival) + timedelta(days=2)).isoformat(), status, paid, now))
        conn.commit()

    _booking(TAG + "FUTURE", _iso(40), 300.0)
    _booking(TAG + "PAST", _iso(-40), 400.0)          # already stayed: income, not held
    _booking(TAG + "UNPAID", _iso(50), 0.0)           # nothing taken yet
    _booking(TAG + "CANCELLED", _iso(60), 250.0, status="cancelled")

    held = m.money_held_not_earned(conn)
    refs = {r["reference_code"] for r in held["rooms"]}
    s.check("a paid future stay is held", TAG + "FUTURE" in refs)
    s.check("a stay that already happened is not", TAG + "PAST" not in refs)
    s.check("nor is one nothing has been paid on", TAG + "UNPAID" not in refs)
    s.check("nor a cancelled one", TAG + "CANCELLED" not in refs)

    mine_total = sum(r["paid"] for r in held["rooms"]
                     if r["reference_code"].startswith(TAG))
    s.check("the amount held is what was actually paid", mine_total == 300.0,
            detail=str(mine_total))
    s.check("it is scheduled into the month the stay happens",
            any(k.startswith(_iso(40)[:7]) for k, _v in held["by_month"]),
            detail=str(held["by_month"]))
    s.check("the section totals add up to the whole",
            abs(sum(held["totals"].values()) - held["total"]) < 0.005)

    page = oc.get("/management/held-not-earned").get_data(as_text=True)
    s.check("the page renders", "Held, not earned" in page)
    # The single most important sentence on it: these euros are already counted
    # somewhere else, for a different question.
    s.check("it says plainly that this is not Money ahead's money",
            "not the same money" in page)

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
