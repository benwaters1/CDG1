"""Three things about buying that nobody could check.

WHAT ARRIVED AGAINST WHAT WAS CHARGED. Reading an invoice into stock
records what somebody typed, and nothing recorded that it differed from
what the invoice said. Eight cases turn up against an invoice for ten and
the only trace is a shelf that is short later.

WHAT IT COST LAST TIME. Reading stock in OVERWRITES stock_items.unit_cost
with the newest figure — deliberately, so the valuation is what was
actually paid — and every older figure is still in the ledger, where
nothing had ever compared them. A supplier putting fifteen per cent on a
line was invisible until somebody remembered what it used to be.

Two rules there, and the second is the one worth having:

  - READ FROM THE LEDGER, NOT THE ITEM. The item cannot say what it used
    to cost; that is the point of overwriting it.
  - COMPARED PER ITEM *AND* PER SUPPLIER. The same crate from two merchants
    at two prices is not a price rise, it is two merchants — and reporting
    it as a rise would have the owner ringing somebody to complain about
    somebody else's price.

THEIR LIST AGAINST OURS. The app has never seen a supplier's statement, and
inventing what they think is owed would be inventing both halves. It puts
our half in the shape theirs arrives in.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZBUY"


def _cleanup(conn):
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Buying")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level,
                                    unit_cost, active, created_at)
           VALUES (?, 'food', 'kg', 0, 4.0, 1, ?)""", (TAG + " Flour", now))
    flour = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def invoice(vendor, amount, status="approved", days_ago=0):
        conn.execute(
            """INSERT INTO expenses (kind, vendor_name, description, amount,
                                     status, submitted_at)
               VALUES ('supplier_invoice', ?, 'delivery', ?, ?, ?)""",
            (vendor, amount,  status,
             (m.datetime.now(m.timezone.utc)
              - m.timedelta(days=days_ago)).isoformat()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    roux = invoice(TAG + " Maison Roux", 400.0, days_ago=60)
    conn.commit()

    s.section("Eight arrived against an invoice for ten")
    m.record_stock_movement(conn, flour, 8, "purchase", unit_cost=4.0,
                            expense_id=roux, invoiced_quantity=10)
    conn.commit()
    short = [x for x in m.delivery_shortfalls(conn, today=today)
             if x["row"]["name"].startswith(TAG)]
    s.check("it is reported", len(short) == 1, detail=str(len(short)))
    s.check("as two short", short[0]["short"] and short[0]["gap"] == 2,
            detail=str(short[0]["gap"]))
    s.check("worth eight euros", short[0]["worth"] == 8.0,
            detail=f"{short[0]['worth']} — the figure to take to the supplier")

    s.section("A delivery that matched is not a discrepancy")
    m.record_stock_movement(conn, flour, 5, "purchase", unit_cost=4.0,
                            expense_id=roux)
    conn.commit()
    s.check("nothing is reported for it",
            len([x for x in m.delivery_shortfalls(conn, today=today)
                 if x["row"]["name"].startswith(TAG)]) == 1,
            detail="a movement with no invoiced quantity against it is an "
                   "ordinary delivery, not a silent match")

    s.section("And over is reported too, not only short")
    m.record_stock_movement(conn, flour, 12, "purchase", unit_cost=4.0,
                            expense_id=roux, invoiced_quantity=10)
    conn.commit()
    both = [x for x in m.delivery_shortfalls(conn, today=today)
            if x["row"]["name"].startswith(TAG)]
    over = [x for x in both if not x["short"]]
    s.check("an over-delivery is on the list", len(over) == 1,
            detail="a supplier who sends more than they billed will "
                   "eventually bill for it")

    s.section("A price rise, read from the ledger rather than the item")
    _cleanup(conn)
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level,
                                    unit_cost, active, created_at)
           VALUES (?, 'food', 'kg', 0, 4.0, 1, ?)""", (TAG + " Flour", now))
    flour = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    roux = invoice(TAG + " Maison Roux", 400.0, days_ago=60)
    conn.commit()

    for days_ago, cost in ((60, 4.0), (5, 5.0)):
        conn.execute(
            """INSERT INTO stock_movements (stock_item_id, delta, reason,
                       unit_cost, expense_id, created_at)
               VALUES (?, 10, 'purchase', ?, ?, ?)""",
            (flour, cost, roux,
             (m.datetime.now(m.timezone.utc)
              - m.timedelta(days=days_ago)).isoformat()))
    # The item itself now says 4.0, which is stale on purpose -- the
    # valuation uses whatever was last actually paid, and that is why the
    # comparison cannot come from here.
    conn.commit()

    rises = [r for r in m.price_changes(conn, today=today)
             if r["name"].startswith(TAG)]
    s.check("a quarter on the price is reported", len(rises) == 1,
            detail=str(len(rises)))
    s.check("with what it was and what it is",
            rises[0]["was"] == 4.0 and rises[0]["now"] == 5.0,
            detail=str(rises[0]))
    s.check("and by how much", rises[0]["pct"] == 25.0 and rises[0]["up"],
            detail=str(rises[0]["pct"]))

    s.section("Two merchants at two prices is not a price rise")
    # The rule that stops the owner ringing somebody to complain about
    # somebody else's price.
    other = invoice(TAG + " Autre Fournisseur", 300.0, days_ago=3)
    conn.execute(
        """INSERT INTO stock_movements (stock_item_id, delta, reason,
                   unit_cost, expense_id, created_at)
           VALUES (?, 10, 'purchase', 9.0, ?, ?)""",
        (flour, other, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    rises = [r for r in m.price_changes(conn, today=today)
             if r["name"].startswith(TAG)]
    s.check("the second merchant does not create a rise",
            len(rises) == 1 and rises[0]["vendor"] == TAG + " Maison Roux",
            detail=str([(r['vendor'], r['pct']) for r in rises]))

    s.section("A small move is not worth a page")
    conn.execute(
        """INSERT INTO stock_movements (stock_item_id, delta, reason,
                   unit_cost, expense_id, created_at)
           VALUES (?, 10, 'purchase', 5.05, ?, ?)""",
        (flour, roux, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    rises = [r for r in m.price_changes(conn, today=today)
             if r["name"].startswith(TAG) and r["vendor"] == TAG + " Maison Roux"]
    s.check("a one per cent move is left off", not rises,
            detail=f"{rises} — a page that lists every rounding is a page "
                   "nobody reads")

    s.section("Our half of a statement")
    st = m.supplier_statement(conn, TAG + " Maison Roux", today=today)
    s.check("it lists what we have from them", st["rows"], detail=str(len(st["rows"])))
    s.check("with a total", st["total"] == 400.0, detail=str(st["total"]))
    s.check("and what is still outstanding",
            st["outstanding"] == 400.0 and st["unpaid"] == 1,
            detail=f"{st['outstanding']} across {st['unpaid']} — approved is "
                   "not paid")

    conn.execute("UPDATE expenses SET status = 'paid' WHERE id = ?", (roux,))
    conn.commit()
    st = m.supplier_statement(conn, TAG + " Maison Roux", today=today)
    s.check("paying one takes it off the outstanding figure",
            st["outstanding"] == 0.0 and st["total"] == 400.0,
            detail=f"{st['outstanding']} outstanding of {st['total']} — the "
                   "total is what they sent, not what is owed")

    s.section("The page")
    body = oc.get("/management/buying").get_data(as_text=True)
    s.check("it opens and names a supplier", TAG + " Maison Roux" in body)
    r = oc.get(f"/management/buying?vendor={TAG}+Maison+Roux")
    s.check("and a statement can be pulled up", r.status_code == 200)
    r = ec.get("/management/buying", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
