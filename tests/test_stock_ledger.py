"""Stock is a ledger, not a counter.

The level is never stored on the item — it is summed from the movements every
time it is read, so the number on screen is always exactly what the history
says. A stored counter and a ledger disagree the first time anything goes
wrong, and then nobody knows which to believe.

That design is worth pinning because it is the whole reason the figure can be
trusted, and because the run-out happens mid-service: you find out from a guest
asking for a bottle you have not got.

One real bug found here. A movement against an item that does not exist reached
the INSERT and raised a foreign-key error — a 500 with a stack trace instead of
a 404. Worse, the route had no try/finally, so the connection was never closed
and held its write lock: the very next query anywhere in the app got "database
is locked". It checks first now.

Negative stock is deliberately allowed and deliberately visible. Selling six
bottles when the ledger says three does not mean the sale was wrong — it means
the count was, and hiding it at zero would erase the only evidence.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZSTK"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM stock_movements WHERE stock_item_id IN
                    (SELECT id FROM stock_items WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _item(name, reorder=0.0, unit="bottle", category="drinks"):
    conn = db()
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, reorder_level, active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        (f"{TAG} {name}", category, unit, reorder,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM stock_items WHERE name = ?",
                       (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _level(item_id):
    conn = db()
    try:
        return m.stock_levels(conn, [item_id]).get(item_id, 0)
    finally:
        conn.close()


def _movements(item_id):
    conn = db()
    try:
        return conn.execute(
            """SELECT delta, reason, note FROM stock_movements
               WHERE stock_item_id = ? ORDER BY id""", (item_id,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Stock ledger")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("A new item starts at nothing")
    wine = _item("Jurançon", reorder=6)
    s.check("no movements, no level", _level(wine["id"]) == 0,
            detail=f"got {_level(wine['id'])}")

    s.section("Buying adds, selling takes away")
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "purchase", "quantity": "12"}, follow_redirects=True)
    s.check("a delivery of twelve", _level(wine["id"]) == 12,
            detail=f"got {_level(wine['id'])}")
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "sale", "quantity": "4"}, follow_redirects=True)
    s.check("four sold leaves eight", _level(wine["id"]) == 8,
            detail=f"got {_level(wine['id'])}")

    s.section("The sign comes from the reason, not from the typing")
    # Somebody typing "-4" for a sale must not accidentally add four back.
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "sale", "quantity": "-2"}, follow_redirects=True)
    s.check("a sale of minus two still takes two away", _level(wine["id"]) == 6,
            detail=f"got {_level(wine['id'])} — the minus sign was obeyed twice")
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "purchase", "quantity": "-1"}, follow_redirects=True)
    s.check("and a purchase of minus one still adds one", _level(wine["id"]) == 7,
            detail=f"got {_level(wine['id'])}")

    s.section("Wastage is a loss, not a sale")
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "wastage", "quantity": "1", "note": f"{TAG} corked"},
            follow_redirects=True)
    s.check("it comes off the shelf", _level(wine["id"]) == 6)
    kinds = [r["reason"] for r in _movements(wine["id"])]
    s.check("and is recorded as wastage, so the sales figure is not polluted",
            "wastage" in kinds and kinds.count("sale") == 2, detail=f"{kinds}")

    s.section("A stocktake is a count, not a change")
    # The interesting number is the difference between the shelf and the ledger.
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "stocktake", "quantity": "4"}, follow_redirects=True)
    s.check("the level becomes what was counted", _level(wine["id"]) == 4,
            detail=f"got {_level(wine['id'])}")
    last = _movements(wine["id"])[-1]
    s.check("the movement is the difference, not the count",
            abs(last["delta"] + 2) < 0.001,
            detail=f"delta {last['delta']} — a stocktake that writes the count "
                   "as a movement doubles the shelf")
    s.check("and it says what was counted and what the ledger thought",
            "4" in (last["note"] or "") and "6" in (last["note"] or ""),
            detail=f"{last['note']!r}")

    s.section("Counting and finding it right is still worth recording")
    before = len(_movements(wine["id"]))
    oc.post(f"/admin/stock/{wine['id']}/move",
            data={"reason": "stocktake", "quantity": "4"}, follow_redirects=True)
    s.check("a zero-difference stocktake is written down",
            len(_movements(wine["id"])) == before + 1,
            detail="'I counted it and it was right' is information too")
    s.check("and the level does not move", _level(wine["id"]) == 4)

    s.section("Selling more than you have shows as less than nothing")
    # Not an error to hide: the sale happened, so the count was wrong, and
    # clamping at zero erases the only evidence of that.
    short = _item("Champagne", reorder=6)
    oc.post(f"/admin/stock/{short['id']}/move",
            data={"reason": "purchase", "quantity": "3"}, follow_redirects=True)
    oc.post(f"/admin/stock/{short['id']}/move",
            data={"reason": "sale", "quantity": "5"}, follow_redirects=True)
    s.check("the level is negative", _level(short["id"]) == -2,
            detail=f"got {_level(short['id'])}")

    s.section("A mistake is corrected by another movement, never by an edit")
    corrected = _item("Armagnac")
    oc.post(f"/admin/stock/{corrected['id']}/move",
            data={"reason": "purchase", "quantity": "10"}, follow_redirects=True)
    oc.post(f"/admin/stock/{corrected['id']}/move",
            data={"reason": "correction", "quantity": "3",
                  "note": f"{TAG} counted the case twice"}, follow_redirects=True)
    s.check("the correction comes off", _level(corrected["id"]) == 7,
            detail=f"got {_level(corrected['id'])}")
    s.check("and both rows are still there, so the history stays truthful",
            len(_movements(corrected["id"])) == 2,
            detail="a correction that rewrites the original loses what happened")

    s.section("Below the reorder level, it says so")
    page = oc.get("/admin/stock")
    html = page.get_data(as_text=True)
    s.check("the stock page loads", page.status_code == 200, page)
    s.check("and the short item is on it", f"{TAG} Champagne" in html)
    s.check("flagged as needing more",
            "reorder" in html.lower() or "short" in html.lower() or "low" in html.lower(),
            detail="nothing on the page says which items to buy")

    s.section("A quantity that is not a number changes nothing")
    steady = _level(wine["id"])
    r = oc.post(f"/admin/stock/{wine['id']}/move",
                data={"reason": "purchase", "quantity": "a case"}, follow_redirects=True)
    s.check("the level is untouched", _level(wine["id"]) == steady,
            detail=f"{steady} -> {_level(wine['id'])}")
    s.check("and it says so", any("quantity" in x.lower() for x in flashes(r)),
            detail=f"{flashes(r)[:1]}")

    s.section("An invented reason is refused outright")
    s.check("not a 500, and nothing written",
            oc.post(f"/admin/stock/{wine['id']}/move",
                    data={"reason": "shrinkage", "quantity": "1"}).status_code == 400)
    s.check("the level is still what it was", _level(wine["id"]) == steady)

    s.section("A movement against an item that is not there is a 404")
    # It used to be a 500 from a foreign-key error, and it left the connection
    # open holding its write lock — so the next query anywhere got
    # "database is locked".
    s.check("404, not 500",
            oc.post("/admin/stock/999999/move",
                    data={"reason": "purchase", "quantity": "5"}).status_code == 404)
    s.check("and the database is still usable afterwards",
            _level(wine["id"]) == steady,
            detail="a stuck write lock takes the whole app down, not just this page")

    s.section("Adding an item")
    oc.post("/admin/stock/new", data={
        "name": f"{TAG} Izarra", "category": "drinks", "unit": "bottle",
        "reorder_level": "4", "unit_cost": "18.50",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT * FROM stock_items WHERE name = ?",
                        (f"{TAG} Izarra",)).fetchone()
    conn.close()
    s.check("it is created", made is not None)
    if made:
        s.check("with its reorder level", made["reorder_level"] == 4)
        s.check("and its cost", abs((made["unit_cost"] or 0) - 18.50) < 0.01)
        s.check("and starts at nothing until something arrives",
                _level(made["id"]) == 0)

    s.section("Guards")
    s.check("an employee cannot move stock",
            ec.post(f"/admin/stock/{wine['id']}/move",
                    data={"reason": "sale", "quantity": "1"}).status_code in (302, 403))
    s.check("nor add an item",
            ec.post("/admin/stock/new", data={"name": "x"}).status_code in (302, 403))
    s.check("and the level is unchanged by either attempt",
            _level(wine["id"]) == steady)

    _cleanup()
    return s
