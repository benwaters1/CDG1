"""Four things a kitchen does, none of which anything recorded.

THE FRIDGE LOG. A restaurant serving the public keeps a record of its cold
storage, and this app never had one. The compliance tick is the least
interesting part: what is worth having is noticing that one unit has drifted
up all week, before anything in it has to be thrown out.

Two decisions in it are worth holding in place, because both are the kind
that get "simplified" away later:

  - A unit with NO RANGE SET reads as unjudged, not as fine. Silence and
    approval look identical on a page and only one of them is honest about a
    fridge nobody has told the app about.
  - A reading outside the range with NOTHING written against it is called
    out as unanswered. It is still recorded — refusing it would mean the
    reading never gets written down at all — but an out-of-range reading
    that nobody answered is a record of a problem nobody dealt with, and
    the page has to say so rather than list it like any other row.

WHAT GETS THROWN AWAY. The stock ledger has had a 'wastage' reason since the
day stock was built and nothing has ever added it up.

HOW LONG FOOD TOOK. pos_order_lines has stamped sent_at, ready_at and
served_at since the till was built, and nothing has ever read them back.
Reported a NIGHT AT A TIME rather than as one average, because a month's
mean hides the two evenings that were actually bad.

THE PREP LIST. The board in the kitchen. It resets daily and a line nobody
did yesterday does not follow you around, which is the whole difference
between it and the house's task list.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZKITCHEN"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM fridge_readings WHERE unit_id IN "
        "(SELECT id FROM fridge_units WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM fridge_units WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM prep_items WHERE what LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM stock_movements WHERE stock_item_id IN "
        "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the kitchen")
    today = house_today()
    now = m.datetime.now(m.timezone.utc)
    conn = db()
    oc, ec, _owner, emp = clients()
    _cleanup(conn)

    # ------------------------------------------------------------ fridges
    def add_unit(name, min_c, max_c):
        conn.execute(
            """INSERT INTO fridge_units (name, where_it_is, min_c, max_c,
                                         active, created_at)
               VALUES (?, 'Back kitchen', ?, ?, 1, ?)""",
            (TAG + " " + name, min_c, max_c, now.isoformat()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    walk_in = add_unit("Walk-in", 0, 5)
    freezer = add_unit("Freezer", None, -15)
    unranged = add_unit("Cellar chiller", None, None)

    def read(unit_id, celsius, days_ago, action=None):
        when = (now - timedelta(days=days_ago)).isoformat()
        conn.execute(
            """INSERT INTO fridge_readings (unit_id, read_at, celsius,
                       read_by_user_id, action_taken, note, created_at)
               VALUES (?, ?, ?, NULL, ?, NULL, ?)""",
            (unit_id, when, celsius, action, when))

    read(walk_in, 3.8, 3)
    read(walk_in, 9.1, 2, action="Door had been left open. Closed it, 4.2 an hour later.")
    read(walk_in, 8.4, 1)                    # out of range, nobody said anything
    read(freezer, -18.0, 1)
    # Well below anything anyone would set as a floor, and the freezer has
    # no floor. It is here to fail the naive version of this check --
    # comparing against `u["min_c"] or 0` and calling -22 too cold.
    read(freezer, -22.0, 2)
    read(unranged, 40.0, 1)                  # absurd, and deliberately unjudged
    read(walk_in, 4.0, 30)                   # outside the fortnight
    conn.commit()

    log = {u["unit"]["name"]: u for u in m.fridge_log(conn, today=today)}
    w = log[TAG + " Walk-in"]

    s.section("The fridge log reads back what was written")
    s.check("it only looks at the last fortnight", len(w["readings"]) == 3,
            detail=f"{len(w['readings'])} readings, and a fourth is 30 days old")
    s.check("both readings above the range are found", len(w["out_of_band"]) == 2,
            detail=str([round(x["row"]["celsius"], 1) for x in w["out_of_band"]]))

    s.section("A reading nobody answered is separated from one somebody did")
    # The whole point. Both are out of range; only one is a problem still
    # sitting there. A page that lists them identically is a page that lets
    # the open one sit for a fortnight.
    s.check("only the one with nothing written against it is unanswered",
            len(w["unanswered"]) == 1,
            detail=f"{len(w['unanswered'])} of 2 out-of-range readings")
    s.check("and it is the right one",
            w["unanswered"] and abs(w["unanswered"][0]["row"]["celsius"] - 8.4) < 0.01,
            detail=str([round(x["row"]["celsius"], 1) for x in w["unanswered"]]))

    s.section("A ceiling with no floor still judges the ceiling")
    f = log[TAG + " Freezer"]
    s.check("a freezer at -18 with a max of -15 is in range",
            f["judged"] and not f["out_of_band"],
            detail="both readings are cold; neither is out of range")
    s.check("and -22 is not called too cold either",
            not [x for x in f["out_of_band"] if x["row"]["celsius"] < -20],
            detail="min_c is NULL. The naive version of this compares "
                   "against `u['min_c'] or 0` and reports every freezer in "
                   "the house as a fault, every day, until nobody reads it")
    s.check("a reading above the ceiling would still be caught", f["judged"],
            detail="a unit with only a max is judged, not skipped")

    s.section("A unit with no range set is unjudged, not fine")
    u = log[TAG + " Cellar chiller"]
    s.check("it is marked as having nothing to judge against", not u["judged"])
    s.check("and nothing is reported as wrong with it", not u["out_of_band"],
            detail="it read 40C, which would be alarming if anyone had said "
                   "what it should be")
    s.check("but the reading is still there to look at", len(u["readings"]) == 1,
            detail="an unjudged unit that also stops recording is worse than "
                   "no unit at all")

    s.section("The page shows all three, and says which is unanswered")
    r = ec.get("/kitchen/fridges")
    body = r.get_data(as_text=True)
    s.check("an employee can open it", r.status_code == 200,
            detail=f"HTTP {r.status_code} — whoever is in the kitchen "
                   "reads the fridge, not the owner")
    for name in ("Walk-in", "Freezer", "Cellar chiller"):
        s.check(f"{name} is on it", TAG + " " + name in body)
    s.check("the unanswered reading is called out", "nothing said" in body)
    s.check("and the one somebody dealt with is not called out",
            "Door had been left open" in body)
    s.check("the unranged unit says nothing is being judged",
            "No range has been set" in body)

    s.section("Writing a reading down through the page")
    before = conn.execute(
        "SELECT COUNT(*) FROM fridge_readings WHERE unit_id = ?",
        (walk_in,)).fetchone()[0]
    r = ec.post("/kitchen/fridges",
                data={"unit_id": str(walk_in), "celsius": "4,4"},
                follow_redirects=True)
    after = conn.execute(
        "SELECT COUNT(*) FROM fridge_readings WHERE unit_id = ?",
        (walk_in,)).fetchone()[0]
    s.check("it is recorded", after == before + 1,
            detail=f"{before} then {after}")
    # A French keyboard writes 4,4. Refusing it would mean the reading is not
    # written down, which is the only failure that matters here.
    got = conn.execute(
        "SELECT celsius FROM fridge_readings WHERE unit_id = ? "
        "ORDER BY id DESC LIMIT 1", (walk_in,)).fetchone()[0]
    s.check("a comma decimal is read as a number", abs(got - 4.4) < 0.001,
            detail=f"stored {got}")

    s.section("An out-of-range reading with nothing said still goes in")
    before = conn.execute(
        "SELECT COUNT(*) FROM fridge_readings WHERE unit_id = ?",
        (walk_in,)).fetchone()[0]
    r = ec.post("/kitchen/fridges",
                data={"unit_id": str(walk_in), "celsius": "11"},
                follow_redirects=True)
    after = conn.execute(
        "SELECT COUNT(*) FROM fridge_readings WHERE unit_id = ?",
        (walk_in,)).fetchone()[0]
    s.check("it is recorded rather than refused", after == before + 1,
            detail="refusing it would mean the reading never gets written "
                   "down at all, which is worse than one with no action "
                   "against it")
    s.check("and the person is told it needs an answer",
            "outside its range" in r.get_data(as_text=True))

    # -------------------------------------------------------------- waste
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost,
                                    reorder_level, active, created_at)
           VALUES (?, 'food', 'kg', 12.0, 1, 1, ?)""",
        (TAG + " Turbot", now.isoformat()))
    turbot = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost,
                                    reorder_level, active, created_at)
           VALUES (?, 'food', 'kg', 2.0, 1, 1, ?)""",
        (TAG + " Potatoes", now.isoformat()))
    spuds = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def move(item, delta, reason, days_ago, unit_cost=None):
        when = (now - timedelta(days=days_ago)).isoformat()
        conn.execute(
            """INSERT INTO stock_movements (stock_item_id, delta, reason,
                       unit_cost, note, created_at)
               VALUES (?, ?, ?, ?, NULL, ?)""",
            (item, delta, reason, unit_cost, when))

    move(turbot, -2, "wastage", 5)            # 2kg at 12.00 = 24.00
    move(spuds, -3, "wastage", 5)             # 3kg at  2.00 =  6.00
    move(spuds, -4, "wastage", 5)             # 4kg at  2.00 =  8.00
    move(turbot, -5, "sale", 5)               # sold, not wasted
    move(turbot, -9, "wastage", 200)          # outside the window
    move(turbot, 6, "purchase", 5)            # in, not out
    # Keyed 4kg when it was 3. The kilo comes back as a wastage movement the
    # other way, and the report has to net it -- otherwise the only way to
    # correct a fat-fingered write-off is to edit the ledger.
    move(spuds, 1, "wastage", 4)
    conn.commit()

    waste = m.waste_log(conn, days=90, today=today)
    rows = {r["name"]: r for r in waste["rows"] if r["name"].startswith(TAG)}

    s.section("What gets thrown away is added up from the ledger")
    s.check("both wasted items are found", len(rows) == 2, detail=str(list(rows)))
    s.check("the turbot is 2kg", abs(rows[TAG + " Turbot"]["quantity"] - 2) < 0.001,
            detail="a sale and a delivery on the same item must not be counted")
    s.check("worth 24.00", abs(rows[TAG + " Turbot"]["worth"] - 24.0) < 0.01,
            detail=str(rows[TAG + " Turbot"]["worth"]))
    s.check("the potatoes are 7kg written off less the 1kg put back",
            abs(rows[TAG + " Potatoes"]["quantity"] - 6) < 0.001,
            detail=f"{rows[TAG + ' Potatoes']['quantity']}kg -- 3 and 4 "
                   "thrown away, 1 keyed by mistake and reversed")
    s.check("and 12.00 rather than 14.00",
            abs(rows[TAG + " Potatoes"]["worth"] - 12.0) < 0.01,
            detail=str(rows[TAG + " Potatoes"]["worth"]))
    s.check("and dearest is first",
            [r["name"] for r in waste["rows"]].index(TAG + " Turbot")
            < [r["name"] for r in waste["rows"]].index(TAG + " Potatoes"),
            detail="the point of the page is which one to do something about")

    s.section("A write-off entirely reversed disappears rather than showing nil")
    conn.execute(
        """INSERT INTO stock_items (name, category, unit, unit_cost,
                                    reorder_level, active, created_at)
           VALUES (?, 'food', 'kg', 5.0, 1, 1, ?)""",
        (TAG + " Chard", now.isoformat()))
    chard = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    move(chard, -2, "wastage", 5)
    move(chard, 2, "wastage", 4)
    conn.commit()
    netted = m.waste_log(conn, days=90, today=today)
    s.check("it is not on the page at all",
            TAG + " Chard" not in {r["name"] for r in netted["rows"]},
            detail="'0.0kg thrown away, twice' is noise on a page whose "
                   "whole job is which one thing to do something about")

    s.section("The window is a window")
    short = m.waste_log(conn, days=7, today=today)
    names = {r["name"] for r in short["rows"]}
    s.check("a 200-day-old write-off is out of a 90-day view",
            abs(rows[TAG + " Turbot"]["quantity"] - 2) < 0.001,
            detail="9kg more was thrown away 200 days ago")
    s.check("and a 5-day-old one is out of a 7-day view only if it is older",
            TAG + " Turbot" in names,
            detail="5 days ago is inside 7 days, so it must still be there")

    s.section("The waste page is the owner's")
    r = oc.get("/kitchen/waste?days=90")
    body = r.get_data(as_text=True)
    s.check("the owner can open it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("the turbot is on it", TAG + " Turbot" in body)
    r = ec.get("/kitchen/waste", follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code} — it is a page about money")

    # ------------------------------------------------------ service times
    s.section("How long food took, from stamps nothing ever read")
    have_lines = conn.execute(
        "SELECT COUNT(*) FROM pos_order_lines "
        "WHERE sent_at IS NOT NULL AND ready_at IS NOT NULL").fetchone()[0]
    nights = m.service_times(conn, days=3650, today=today)
    if have_lines:
        s.check("nights come back where the till has stamped both times",
                bool(nights), detail=f"{have_lines} stamped lines, "
                                     f"{len(nights)} nights")
        s.check("each night has a figure in minutes",
                all(n["to_ready"] is None or n["to_ready"] >= 0 for n in nights))
    else:
        # Said out loud rather than skipped. An empty result here is honest
        # (nothing has been stamped yet) but it is also what a broken query
        # returns, and the two must not look the same in the report.
        s.check("no lines carry both stamps yet, so there is nothing to measure",
                nights == [],
                detail="the till writes these stamps; this reads them back. "
                       "Once one service has run there will be nights here.")

    r = oc.get("/kitchen/service-times")
    s.check("the page opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("it says a night at a time, not a monthly average",
            "night at a time" in r.get_data(as_text=True) or
            "No lines with both stamps" in r.get_data(as_text=True))
    r = ec.get("/kitchen/service-times", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    # -------------------------------------------------------------- prep
    s.section("The prep board resets every day")
    r = ec.post("/kitchen/prep",
                data={"what": TAG + " Bone out the lamb",
                      "for_date": today.isoformat()},
                follow_redirects=True)
    row = conn.execute("SELECT * FROM prep_items WHERE what LIKE ? ORDER BY id DESC",
                       (TAG + "%",)).fetchone()
    s.check("a line goes on the board", row is not None)
    s.check("for the day asked for", row and row["for_date"] == today.isoformat(),
            detail=row["for_date"] if row else "no row")

    body = ec.get("/kitchen/prep").get_data(as_text=True)
    s.check("it shows on today's board", TAG + " Bone out the lamb" in body)
    tomorrow = ec.get(
        "/kitchen/prep?date=" + (today + timedelta(days=1)).isoformat()
    ).get_data(as_text=True)
    s.check("and not on tomorrow's", TAG + " Bone out the lamb" not in tomorrow,
            detail="a line that follows you around is the house task list, "
                   "which this deliberately is not")

    s.section("Ticking it, and unticking it")
    ec.post(f"/kitchen/prep/{row['id']}/done", follow_redirects=True)
    done = conn.execute("SELECT done_at, done_by_user_id FROM prep_items WHERE id = ?",
                        (row["id"],)).fetchone()
    s.check("it is ticked", done["done_at"] is not None)
    s.check("and says who", done["done_by_user_id"] is not None,
            detail="a board that does not say who did it settles no argument")
    ec.post(f"/kitchen/prep/{row['id']}/done", follow_redirects=True)
    undone = conn.execute("SELECT done_at, done_by_user_id FROM prep_items WHERE id = ?",
                          (row["id"],)).fetchone()
    s.check("ticking again unticks it", undone["done_at"] is None,
            detail="people mistake rows, and a board you cannot correct is a "
                   "board people stop using")
    s.check("and forgets who", undone["done_by_user_id"] is None,
            detail="leaving the name on an unticked line accuses somebody of "
                   "something they did not do")

    # ------------------------------------------------- reachable, by anyone
    s.section("Every one of these is reachable without knowing the URL")
    # The check this batch nearly shipped without. Thirteen staff pages --
    # the handover, the cleaning rounds, the visitor book, the mileage claim
    # and more -- were in PALETTE_PAGES and in no menu, and the search box
    # that reads the palette is wrapped in {% if user['role'] == 'owner' %}.
    # For every employee in the house they were pages that did not exist.
    emp_nav = ec.get("/").get_data(as_text=True)
    emp_nav = emp_nav[:emp_nav.find("</nav>")] if "</nav>" in emp_nav else emp_nav
    # The employee here has no access preset, so user_access() returns an
    # empty set and they get the plain lane -- which is the lane most of the
    # house is actually in, and the one that had none of these pages on it.
    s.check("this is the plain employee lane, not the manager one",
            "The House" in emp_nav,
            detail="if this employee has areas granted they see the manager "
                   "menu, and every check below proves nothing about the "
                   "person on a Tuesday morning")
    for path, name in (("/kitchen/fridges", "the fridge log"),
                       ("/management/meters", "the meter readings"),
                       ("/restaurant/tables", "the table plan"),
                       ("/guests/dates", "dates worth knowing"),
                       ("/kitchen/prep", "the prep list"),
                       ("/handover", "the handover"),
                       ("/management/cleaning", "the cleaning rounds"),
                       ("/management/visitors", "the visitor book"),
                       ("/management/breakages", "breakages"),
                       ("/management/lost-property", "lost property"),
                       ("/mileage", "the mileage claim"),
                       ("/restaurant/covers", "covers ahead"),
                       ("/extras/due", "extras due")):
        # Counted, not just found: the group's own label points at the
        # handover, so a bare `in` was satisfied for /handover by the label
        # alone and would not have noticed the item vanishing from the menu.
        s.check(f"an employee can navigate to {name}",
                emp_nav.count('href="%s"' % path) >= 1,
                detail="in the nav for somebody who cannot use the search box")

    s.section("And the owner has them too")
    own_nav = oc.get("/").get_data(as_text=True)
    own_nav = own_nav[:own_nav.find("</nav>")] if "</nav>" in own_nav else own_nav
    for path in ("/kitchen/fridges", "/kitchen/prep", "/handover",
                 "/management/cleaning", "/management/meters"):
        s.check(f"{path} is in the owner's nav", path in own_nav)

    s.section("The two money pages are in the palette, where the owner looks")
    palette = {e for _l, e, _k in m.PALETTE_PAGES}
    for endpoint in ("kitchen_waste", "kitchen_service_times",
                     "kitchen_fridges", "kitchen_prep"):
        s.check(f"{endpoint} can be searched for", endpoint in palette)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
