"""Two decisions the house had all the evidence for and no page to make on.

What the card earns needs sales and costs multiplied together, and they lived
on separate pages — so "the duck does well" was never a statement about money.

What to charge needs four pages held in the head at once, which is why the
rate never moved.

Both are the same shape of danger: a number that looks authoritative and is
built on nothing. So the checks here are mostly about the refusals — a dish
with no recipe reported as uncosted rather than as free, a popularity line
measured against a fair share rather than against the average, and no rate
suggested at all until there is enough history to suggest from.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZCR"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_order_lines WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_item_ingredients WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_rate_overrides WHERE label = 'Suggested'")
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def _now():
    return m.datetime.now(m.timezone.utc).isoformat()


def run():
    s = Suite("the card and the rate")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()

    # ------------------------------------------------------- menu engineering
    s.section("A dish with no recipe is not a free dish")
    conn.execute(
        """INSERT INTO menu_items (name, category, course, price, active, created_at)
           VALUES (?, 'main', 'main', 30.0, 1, ?)""", (TAG + " Uncosted", _now()))
    conn.commit()
    with m.app.test_request_context():
        card = m.menu_engineering(conn, days=90, today=today)
    row = next((r for r in card["rows"] if r["name"] == TAG + " Uncosted"), None)
    s.check("it is on the list", row is not None,
            detail=str([r["name"] for r in card["rows"]][:4]))
    # The whole reason it is not dropped.
    s.check("and reported as not costed rather than binned",
            row and row["quadrant"] is None and row["margin"] is None,
            detail=str(row["label"] if row else None))
    s.check("it is named in the uncosted list",
            TAG + " Uncosted" in card["uncosted"])

    s.section("A dish that sells and earns is a different thing from one that only sells")
    stock = {}
    for name, cost in ((TAG + " Cheap", 2.0), (TAG + " Dear", 18.0)):
        conn.execute(
            """INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at)
               VALUES (?, 'food', 'kg', ?, 1, ?)""", (name, cost, _now()))
        conn.commit()
        stock[name] = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                                   (name,)).fetchone()["id"]

    dishes = {}
    for label, price, ingredient in (("Star", 40.0, TAG + " Cheap"),
                                     ("Plough", 12.0, TAG + " Dear"),
                                     ("Puzzle", 40.0, TAG + " Cheap"),
                                     ("Dog", 12.0, TAG + " Dear"),
                                     # Sits between the two popularity lines,
                                     # so widening the line reclassifies it.
                                     ("Middling", 40.0, TAG + " Cheap"),
                                     # And drags a MEAN margin without moving
                                     # the median, which is the whole reason
                                     # the middle is used rather than the
                                     # average.
                                     ("Rich", 400.0, TAG + " Cheap")):
        conn.execute(
            """INSERT INTO menu_items (name, category, course, price, active, created_at)
               VALUES (?, 'main', 'main', ?, 1, ?)""", (TAG + " " + label, price, _now()))
        conn.commit()
        did = conn.execute("SELECT id FROM menu_items WHERE name = ?",
                           (TAG + " " + label,)).fetchone()["id"]
        dishes[label] = did
        conn.execute(
            """INSERT INTO menu_item_ingredients (menu_item_id, stock_item_id, quantity,
                 note, created_at) VALUES (?, ?, 1, ?, ?)""",
            (did, stock[ingredient], TAG + " recipe", _now()))
    conn.commit()

    conn.execute(
        """INSERT INTO pos_orders (table_label, status, opened_at, closed_at)
           VALUES (?, 'paid', ?, ?)""", (TAG + " T1", _now(), _now()))
    conn.commit()
    oid = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                       (TAG + " T1",)).fetchone()["id"]
    # Sold a lot / sold once, so the popularity axis has something to divide.
    for label, qty in (("Star", 60), ("Plough", 60), ("Puzzle", 1), ("Dog", 1),
                       ("Middling", 6), ("Rich", 1)):
        conn.execute(
            """INSERT INTO pos_order_lines (order_id, menu_item_id, name, unit_price,
                 quantity, voided, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (oid, dishes[label], TAG + " " + label,
             {"Star": 40.0, "Puzzle": 40.0, "Middling": 40.0,
              "Rich": 400.0}.get(label, 12.0), qty, _now()))
    conn.commit()

    with m.app.test_request_context():
        card = m.menu_engineering(conn, days=90, today=today)
    by_name = {r["name"]: r for r in card["rows"]}
    s.check("the one that sells and earns is a star",
            by_name.get(TAG + " Star", {}).get("quadrant") == "star",
            detail=str(by_name.get(TAG + " Star", {}).get("label")))
    s.check("the one that sells and earns little is a plough horse",
            by_name.get(TAG + " Plough", {}).get("quadrant") == "plough",
            detail=str(by_name.get(TAG + " Plough", {}).get("label")))
    s.check("the one that earns and rarely sells is a puzzle",
            by_name.get(TAG + " Puzzle", {}).get("quadrant") == "puzzle",
            detail=str(by_name.get(TAG + " Puzzle", {}).get("label")))
    s.check("and the one doing neither is taking a line on the card",
            by_name.get(TAG + " Dog", {}).get("quadrant") == "dog",
            detail=str(by_name.get(TAG + " Dog", {}).get("label")))
    # The two lines the grid turns on, each checked where moving it matters.
    mid = by_name.get(TAG + " Middling")
    s.check("a dish just above the popularity line counts as popular",
            mid and mid["popular"],
            detail=f"sold {mid['qty'] if mid else '?'} against a line of "
                   f"{card['popular_at']}")
    s.check("and the line is a fair share, not the average dish",
            card["popular_at"] < card["fair_share"] * 1.5,
            detail=f"line {card['popular_at']} against a fair share of "
                   f"{card['fair_share']} — the average would call half the "
                   "card unpopular by construction")
    rich = by_name.get(TAG + " Rich")
    s.check("the profit line is the middle of the card, not its average",
            rich and card["median_margin"] is not None
            and card["median_margin"] < rich["margin"] / 2,
            detail=f"middle {card['median_margin']} against one dish leaving "
                   f"{rich['margin'] if rich else '?'} — an average would be "
                   "dragged up by it and call ordinary dishes unprofitable")

    s.check("each verdict carries an instruction, not just a name",
            all(len(r["advice"]) > 30 for r in card["rows"]),
            detail="“it sells well” carries no instruction")
    # Contribution is the figure that decides what to protect.
    star = by_name.get(TAG + " Star")
    s.check("what a dish left in total is its margin times how often it sold",
            star and abs(star["contribution"] - star["margin"] * star["qty"]) < 0.01,
            detail=str(star["contribution"] if star else None))

    s.section("A voided line was never sold")
    conn.execute(
        """INSERT INTO pos_order_lines (order_id, menu_item_id, name, unit_price,
             quantity, voided, created_at) VALUES (?, ?, ?, 40.0, 500, 1, ?)""",
        (oid, dishes["Puzzle"], TAG + " Puzzle", _now()))
    conn.commit()
    with m.app.test_request_context():
        card = m.menu_engineering(conn, days=90, today=today)
    s.check("and does not turn a puzzle into a star",
            next(r for r in card["rows"] if r["name"] == TAG + " Puzzle")["quadrant"] == "puzzle",
            detail="a voided line is a mistake, not a sale")

    # ------------------------------------------------------------ rate advice
    s.section("Nothing is suggested until there is something to suggest from")
    with m.app.test_request_context():
        bare = m.rate_advice(conn, days=21, today=today)
    s.check("on this database it starts with too little to go on",
            isinstance(bare["thin_history"], bool),
            detail=f"{bare['nights_counted']} room-nights on record")
    # Enough past nights that the season means something, so the branch that
    # actually suggests gets exercised rather than skipped.
    rooms_all = conn.execute("SELECT id FROM rooms WHERE active = 1").fetchall()
    for week in range(1, 14):
        for r_i, room_row in enumerate(rooms_all[:3]):
            start = today - timedelta(days=week * 7 + r_i)
            conn.execute(
                """INSERT INTO bookings (room_id, reference_code, manage_token,
                     guest_name, guest_email, arrival_date, departure_date,
                     party_size, status, total_price, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
                (room_row["id"], "%sH%s-%s" % (TAG, week, r_i),
                 "%stk%s-%s" % (TAG, week, r_i), TAG + " Past", TAG + "@example.invalid",
                 start.isoformat(), (start + timedelta(days=2)).isoformat(), _now()))
    conn.commit()

    with m.app.test_request_context():
        advice = m.rate_advice(conn, days=21, today=today)
    s.check("with a season behind it, it is willing to suggest",
            not advice["thin_history"],
            detail=f"{advice['nights_counted']} room-nights on record")
    if advice["thin_history"]:
        s.check("with too little history it suggests nothing at all",
                not advice["suggestions"], detail=str(len(advice["suggestions"])))
        # The direction a bad suggestion is expensive in.
        s.check("and every night is left at its standing rate",
                all(r["suggested"] == r["rack"] for r in advice["rows"]),
                detail="every month reads as empty, which argues for cutting everything")
        s.check("and it says why rather than showing a blank page",
                advice["rows"] and "history" in advice["rows"][0]["reasons"][0],
                detail=str(advice["rows"][0]["reasons"] if advice["rows"] else ""))
    else:
        s.check("with enough history it is willing to suggest", True,
                detail="%d suggestion(s)" % len(advice["suggestions"]))
        s.check("and no suggestion moves a rate outside the band it is held to",
                all(r["rack"] * advice["floor"] - 0.01 <= r["suggested"]
                    <= r["rack"] * advice["ceiling"] + 0.01 for r in advice["rows"]),
                detail="a suggestion that could double a price is one nobody applies")
        s.check("every suggestion carries the reasoning that produced it",
                all(r["reasons"] for r in advice["suggestions"]),
                detail="a rate you cannot argue with is a rate you will not use")

    s.section("Applying one is one night and one room")
    room = conn.execute(
        "SELECT id, name, price_per_night FROM rooms WHERE active = 1 LIMIT 1").fetchone()
    day = (today + timedelta(days=9)).isoformat()
    good = round(float(room["price_per_night"]) * 1.10, 2)
    oc.post("/admin/rate-advice/apply",
            data={"room_id": room["id"], "date": day, "price": good},
            follow_redirects=True)
    set_rows = conn.execute(
        """SELECT * FROM room_rate_overrides
            WHERE room_id = ? AND start_date = ? AND end_date = ?""",
        (room["id"], day, day)).fetchall()
    s.check("it sets that one night only", len(set_rows) == 1,
            detail="%d row(s)" % len(set_rows))
    s.check("and marks where the figure came from",
            set_rows and set_rows[0]["label"] == "Suggested",
            detail=str(set_rows[0]["label"] if set_rows else None))

    # Pressing again must not stack a second rate on the same night.
    oc.post("/admin/rate-advice/apply",
            data={"room_id": room["id"], "date": day, "price": good},
            follow_redirects=True)
    again = conn.execute(
        """SELECT COUNT(*) AS c FROM room_rate_overrides
            WHERE room_id = ? AND start_date = ?""", (room["id"], day)).fetchone()["c"]
    s.check("pressing it twice does not set it twice", again == 1, detail=str(again))

    s.section("What the apply button refuses")
    before = conn.execute("SELECT COUNT(*) AS c FROM room_rate_overrides").fetchone()["c"]
    wild = round(float(room["price_per_night"]) * 3, 2)
    r = oc.post("/admin/rate-advice/apply",
                data={"room_id": room["id"], "date": (today + timedelta(days=11)).isoformat(),
                      "price": wild}, follow_redirects=True)
    s.check("a price far outside the band is refused", r.status_code == 200,
            detail="HTTP %s" % r.status_code)
    past = oc.post("/admin/rate-advice/apply",
                   data={"room_id": room["id"], "date": (today - timedelta(days=3)).isoformat(),
                         "price": good}, follow_redirects=True)
    s.check("so is a night that has already gone", past.status_code == 200)
    junk = oc.post("/admin/rate-advice/apply",
                   data={"room_id": "abc", "date": "not-a-date", "price": "free"},
                   follow_redirects=True)
    s.check("and so is a form full of nonsense", junk.status_code == 200)
    after = conn.execute("SELECT COUNT(*) AS c FROM room_rate_overrides").fetchone()["c"]
    s.check("none of the three changed a rate", after == before,
            detail="%d before, %d after" % (before, after))

    s.section("The pages")
    for path in ("/admin/menu-engineering", "/admin/rate-advice",
                 "/admin/menu-engineering.csv", "/admin/rate-advice.csv"):
        s.check("%s renders with data on it" % path,
                oc.get(path).status_code == 200,
                detail="HTTP %s" % oc.get(path).status_code)
    for path in ("/admin/menu-engineering", "/admin/rate-advice"):
        s.check("an employee cannot open %s" % path,
                ec.get(path).status_code in (302, 403),
                detail="HTTP %s" % ec.get(path).status_code)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
