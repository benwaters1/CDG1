"""What to buy, in one list, grouped by who you buy it from.

The house had two separate ways of knowing something was needed and no way
to act on either. Stock had a reorder level and a "Running low" tag on a
page listing everything. Workshop materials knew a session could not be
run. Neither produced a list you could take to a supplier, and the two
never spoke — so an item wanted for both reasons appeared twice, in
different places, in different shapes.

THE ARITHMETIC IS THE POINT and it is neither figure alone:

    to buy = (what the workshops need) + (the level to keep) - (what is held)

If a workshop needs eight kilos more than the shelf has, buying exactly
eight leaves the shelf at zero afterwards — and the reorder level, the
whole purpose of which is what REMAINS, is gone. Taking the larger of the
two figures under-buys every time both apply; adding them without
subtracting stock buys what is already on the shelf.

And a session's NEED is used rather than its shortfall, because a shortfall
is measured against a shelf this list is about to refill: two sessions each
wanting the same eight kilos are sixteen kilos of need, but two shortfalls
of eight against the same empty shelf are not sixteen kilos to buy.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZBASK"


def _cleanup(conn):
    conn.execute("DELETE FROM stock_movements WHERE stock_item_id IN "
                 "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshop_materials WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("What to buy")
    today = house_today()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    conn.execute("INSERT INTO vendors (name, phone, created_at) VALUES (?, ?, ?)",
                 (TAG + " Poterie Ariege", "05 61 00 00 00", now))
    vendor = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def item(name, unit, opening, keep=0, cost=None, vid=None):
        conn.execute(
            """INSERT INTO stock_items (name, category, unit, reorder_level,
                                        unit_cost, vendor_id, active, created_at)
               VALUES (?, 'other', ?, ?, ?, ?, 1, ?)""",
            (TAG + " " + name, unit, keep, cost, vid, now))
        iid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        if opening:
            m.record_stock_movement(conn, iid, opening, "opening", note="test")
        return iid

    # Wanted by a workshop AND below the level to keep.
    clay = item("Clay", "kg", 2, keep=5, cost=4.0, vid=vendor)
    # Below the level to keep, no workshop wants it.
    soap = item("Soap", "each", 1, keep=6, cost=2.0, vid=vendor)
    # Plenty on the shelf, nothing wants it: must not appear at all.
    rope = item("Rope", "m", 500, keep=10, cost=1.0, vid=vendor)
    # Wanted by a workshop, and nobody on file to buy it from.
    gold = item("Gold leaf", "book", 0, keep=0, cost=30.0, vid=None)

    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', 300, 12, 1, ?)""", (TAG + " Gilding", now))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                          capacity, created_at)
           VALUES (?, ?, ?, 12, ?)""",
        (wid, (today + timedelta(days=5)).isoformat(),
         (today + timedelta(days=8)).isoformat(), now))
    sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                          party_size, status, reference_code,
                                          manage_token, created_at)
           VALUES (?, 'Aline', 'a@example.invalid', 4, 'confirmed', ?, ?, ?)""",
        (sid, TAG + "ALINE", "tok-" + TAG.lower() + "-aline", now))
    for iid, per_person, per_session in ((clay, 2, 0), (gold, 0, 3)):
        conn.execute(
            """INSERT INTO workshop_materials (workshop_id, stock_item_id,
                       qty_per_person, qty_per_session, created_at)
               VALUES (?, ?, ?, ?, ?)""", (wid, iid, per_person, per_session, now))
    conn.commit()

    basket = m.shopping_basket(conn, today)
    rows = {r["item"]["name"]: r
            for g in basket["vendors"] for r in g["rows"]}

    s.section("What lands on the list, and what does not")
    s.check("something a workshop needs and the shelf cannot cover is on it",
            TAG + " Clay" in rows, detail=str(sorted(rows)))
    s.check("so is something merely below the level you keep",
            TAG + " Soap" in rows)
    s.check("but something well stocked and unwanted is not",
            TAG + " Rope" not in rows,
            detail="500m on the shelf against a level of 10 and no session "
                   "asking for any")

    s.section("The quantity covers the workshop AND leaves the shelf stocked")
    # Four people at 2kg each is 8kg needed. 2kg is held. The level to keep
    # is 5kg. Buying only the 6kg shortfall would leave nothing on the shelf
    # the moment the workshop runs.
    clay_row = rows[TAG + " Clay"]
    s.check("eight needed, two held, five to keep: buy eleven",
            clay_row["buy"] == 11,
            detail=f"{clay_row['buy']} — not 6, which is the shortfall "
                   "and would empty the shelf; not 8, which ignores the "
                   "level; not 13, which would buy what is already there")
    s.check("and it says both reasons", clay_row["both"],
            detail=str(clay_row["why"]))
    s.check("naming the session that wants it",
            any("Gilding" in reason for reason in clay_row["why"]),
            detail=str(clay_row["why"]))
    s.check("it appears once, not once per reason",
            len([r for g in basket["vendors"] for r in g["rows"]
                 if r["item"]["name"] == TAG + " Clay"]) == 1,
            detail="buying a thing twice because two parts of the app asked "
                   "separately is the failure this replaces")

    s.section("Something wanted for only one reason")
    soap_row = rows[TAG + " Soap"]
    s.check("five held short of six: buy five", soap_row["buy"] == 5,
            detail=f"{soap_row['buy']} — one held, six to keep")
    s.check("and it is not marked as both", not soap_row["both"],
            detail=str(soap_row["why"]))

    s.section("Grouped by who to ring")
    named = [g for g in basket["vendors"] if g["vendor"]]
    s.check("the supplier's own list is together",
            any(g["vendor"]["name"] == TAG + " Poterie Ariege"
                and len(g["rows"]) == 2 for g in named),
            detail=str([(g["vendor"]["name"] if g["vendor"] else None,
                         len(g["rows"])) for g in basket["vendors"]]))
    s.check("with its telephone number, so it is one call",
            any(g["vendor"] and g["vendor"]["phone"] for g in named))
    s.check("and its own total",
            any(g["total"] == round(11 * 4.0 + 5 * 2.0, 2) for g in named),
            detail=str([g["total"] for g in named]))

    s.section("Things with nobody to buy them from are a different job")
    orphan = [g for g in basket["vendors"] if not g["vendor"]]
    s.check("they are kept apart", bool(orphan)
            and any(r["item"]["name"] == TAG + " Gold leaf"
                    for r in orphan[0]["rows"]),
            detail=str([r["item"]["name"] for g in orphan for r in g["rows"]]))
    s.check("and sorted last, because 'find out who sells this' is not a "
            "telephone call", basket["vendors"][-1]["vendor"] is None,
            detail=str([g["vendor"]["name"] if g["vendor"] else None
                        for g in basket["vendors"]]))

    s.section("A workshop already taken out of stock does not still ask")
    m.consume_session_materials(conn, sid, user_id=None)
    conn.commit()
    after = {r["item"]["name"]: r for g in m.shopping_basket(conn, today)["vendors"]
             for r in g["rows"]}
    s.check("the workshop stops being a reason",
            TAG + " Clay" not in after
            or not any("Gilding" in w for w in after[TAG + " Clay"]["why"]),
            detail=str(after.get(TAG + " Clay", {}).get("why")))

    s.section("The page")
    r = oc.get("/admin/stock/basket")
    body = r.get_data(as_text=True)
    s.check("it opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and is reachable from stock",
            "/admin/stock/basket" in oc.get("/admin/stock").get_data(as_text=True),
            detail="a page nobody can navigate to is a URL, not a feature")
    r = ec.get("/admin/stock/basket", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    s.section("And it can be empty")
    # A list that can never be empty becomes furniture. Everything this
    # suite made is removed, so what is left is the house's own basket --
    # which may or may not be empty, so the check is that the PAGE copes,
    # not that the house has nothing to buy.
    _cleanup(conn)
    r = oc.get("/admin/stock/basket")
    s.check("the page renders either way", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    empty = m.shopping_basket(conn, today)
    s.check("and an empty basket is a real state, not a crash",
            isinstance(empty["vendors"], list) and empty["total"] >= 0,
            detail=f"{empty['lines']} line(s) in the house's own basket")

    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
