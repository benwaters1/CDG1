"""Three edits the suite had only ever been refused by.

Reached with an id that does not exist, or as somebody not allowed, and the
404 or the 403 was counted as coverage. What none of them ran was the edit.

Each carries a claim worth holding:

  A DOCUMENT IS EDITED BY ITS OWNER OR THE OWNER OF THE HOUSE, and that is
  the identical clause the delete route uses -- `user["role"] != "owner" and
  user["id"] != doc["user_id"]`. Nothing above the route says it. Clearing
  the expiry date is a real edit too, not a no-op: a certificate with a date
  that has quietly stayed put is one the compliance page still believes in.

  A SOCIAL PLAN CHANGES WHAT IT PRODUCES NEXT, NOT WHAT IS ALREADY WRITTEN.
  In app.py's own words: "Posts already generated keep the day and time they
  were given. Changing a plan changes what it produces next, not what
  somebody may already have written a caption for." That is a promise about
  somebody's unpublished work, and nothing checked it.

  AND AN INVOICE BOOKS ONLY WHAT WAS TICKED, at whatever quantity the owner
  left in the box -- "their edit beats the suggestion". A line nobody ticked
  must not reach the stock ledger, and a quantity the owner corrected must
  not be silently replaced by the one the scan guessed.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZOED"


def _cleanup(conn):
    conn.execute("DELETE FROM documents WHERE title LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM stock_movements WHERE stock_item_id IN "
        "(SELECT id FROM stock_items WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM social_posts WHERE plan_id IN "
                 "(SELECT id FROM social_plans WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM social_plans WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("edits that had only ever been refused")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today()

    def rowid():
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # ----------------------------------------------------------- documents
    s.section("A document is renamed by the person it belongs to")
    expiry = (today + timedelta(days=90)).isoformat()
    conn.execute(
        "INSERT INTO documents (user_id, title, filename, expiry_date, "
        "uploaded_at) VALUES (?, ?, ?, ?, ?)",
        (emp["id"], TAG + " old name", TAG + "-doc.pdf", expiry, now))
    mine = rowid()
    conn.execute(
        "INSERT INTO documents (user_id, title, filename, uploaded_at) "
        "VALUES (?, ?, ?, ?)",
        (owner["id"], TAG + " theirs", TAG + "-other.pdf", now))
    theirs = rowid()
    conn.commit()

    ec.post(f"/documents/{mine}/edit",
            data={"title": TAG + " new name", "expiry_date": expiry},
            follow_redirects=True)
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (mine,)).fetchone()
    s.check("the title is changed", row["title"] == TAG + " new name")

    s.section("And the expiry can be taken off, not just moved")
    # A blank box means "there is no expiry", and it has to reach the column
    # as NULL. Left as it was, the compliance page keeps counting down to a
    # date the owner has already said is wrong.
    ec.post(f"/documents/{mine}/edit",
            data={"title": TAG + " new name", "expiry_date": ""},
            follow_redirects=True)
    s.check("a blank expiry clears it",
            conn.execute("SELECT expiry_date FROM documents WHERE id = ?",
                         (mine,)).fetchone()["expiry_date"] is None,
            detail="a date the owner has removed but the app still holds is "
                   "one the compliance page is still counting down to")

    s.section("A title is required, and somebody else's is not theirs to touch")
    r = ec.post(f"/documents/{mine}/edit",
                data={"title": "   ", "expiry_date": ""},
                follow_redirects=True)
    s.check("a blank title is refused",
            "A title is required" in " ".join(flashes(r)),
            detail=str(flashes(r))[:100])
    s.check("and the name is unchanged",
            conn.execute("SELECT title FROM documents WHERE id = ?",
                         (mine,)).fetchone()["title"] == TAG + " new name")

    r = ec.post(f"/documents/{theirs}/edit",
                data={"title": TAG + " renamed by nobody"},
                follow_redirects=False)
    s.check("somebody else's document is refused", r.status_code == 403,
            detail=f"status {r.status_code}")
    s.check("and is untouched",
            conn.execute("SELECT title FROM documents WHERE id = ?",
                         (theirs,)).fetchone()["title"] == TAG + " theirs")

    r = oc.post(f"/documents/{theirs}/edit",
                data={"title": TAG + " owner renamed"}, follow_redirects=True)
    s.check("but the owner of the house may",
            conn.execute("SELECT title FROM documents WHERE id = ?",
                         (theirs,)).fetchone()["title"] == TAG + " owner renamed",
            detail="the same clause as the delete route, and nothing above "
                   "either of them says it")

    # --------------------------------------------------------- social plans
    s.section("Changing a plan does not rewrite what is already scheduled")
    conn.execute(
        """INSERT INTO social_plans (name, platform, cadence, weekday,
                   post_time, active, created_at)
           VALUES (?, 'Instagram', 'weekly', 1, '09:00', 1, ?)""",
        (TAG + " weekly plan", now))
    plan = rowid()
    conn.commit()
    # A post already generated from it, with a caption somebody wrote. The
    # day and the time are two columns, so the claim is about both: moving a
    # plan from Monday 09:00 to Thursday 17:30 must leave this one where it
    # was told to be.
    made_on = (today + timedelta(days=3)).isoformat()
    conn.execute(
        "INSERT INTO social_posts (plan_id, platform, caption, scheduled_date, "
        "scheduled_time, status, created_at) "
        "VALUES (?, 'Instagram', ?, ?, '09:00', 'drafted', ?)",
        (plan, TAG + " a caption somebody wrote", made_on, now))
    existing = rowid()
    conn.commit()
    before = (made_on, "09:00")

    r = oc.post(f"/management/social/plans/{plan}/edit",
                data={"name": TAG + " weekly plan", "platform": "Instagram",
                      "cadence": "weekly", "weekday": "4",
                      "post_time": "17:30"},
                follow_redirects=True)
    row = conn.execute("SELECT * FROM social_plans WHERE id = ?",
                       (plan,)).fetchone()
    s.check("the plan is saved", row["weekday"] == 4 and row["post_time"] == "17:30",
            detail=f"weekday {row['weekday']}, time {row['post_time']}")
    s.check("and the page says what it scheduled",
            "Plan saved" in " ".join(flashes(r)), detail=str(flashes(r))[:100])
    kept = conn.execute(
        "SELECT scheduled_date, scheduled_time, caption FROM social_posts "
        "WHERE id = ?", (existing,)).fetchone()
    s.check("a post already made keeps the day and time it was given",
            (kept["scheduled_date"], kept["scheduled_time"]) == before,
            detail=f"{kept['scheduled_date']} {kept['scheduled_time']} — the "
                   "plan moved from Monday 09:00 to Thursday 17:30")
    s.check("and the caption somebody wrote is still on it",
            kept["caption"] == TAG + " a caption somebody wrote",
            detail="rewriting a scheduled post to match a changed plan throws "
                   "away work that was already done")

    s.section("A plan needs a name and a platform the app knows")
    r = oc.post(f"/management/social/plans/{plan}/edit",
                data={"name": "  ", "platform": "Instagram"},
                follow_redirects=True)
    s.check("no name is refused",
            "Give the plan a name" in " ".join(flashes(r)),
            detail=str(flashes(r))[:100])
    r = oc.post(f"/management/social/plans/{plan}/edit",
                data={"name": TAG + " weekly plan", "platform": "Friendster"},
                follow_redirects=True)
    s.check("and a platform it does not have is too",
            "not one of the platforms" in " ".join(flashes(r)),
            detail=str(flashes(r))[:100])
    s.check("the plan is as it was after both",
            conn.execute("SELECT name FROM social_plans WHERE id = ?",
                         (plan,)).fetchone()["name"] == TAG + " weekly plan")

    # --------------------------------------------------------- the invoice
    s.section("An invoice books what was ticked, and only that")
    conn.execute(
        "INSERT INTO expenses (kind, description, amount, submitted_at) "
        "VALUES ('supplier_invoice', ?, 240.0, ?)", (TAG + " delivery", now))
    expense = rowid()
    items = []
    for name, cost in ((" flour", 1.2), (" butter", 6.4), (" salt", 0.8),
                       (" yeast", 2.5)):
        conn.execute(
            "INSERT INTO stock_items (name, unit, unit_cost, active, created_at) "
            "VALUES (?, 'kg', ?, 1, ?)", (TAG + name, cost, now))
        items.append(rowid())
    conn.commit()

    def stock_of(item_id):
        return conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS n FROM stock_movements "
            "WHERE stock_item_id = ?", (item_id,)).fetchone()["n"]

    # Two ticked, one not. The middle one carries a quantity the owner has
    # corrected downwards -- eight turned up, ten were invoiced.
    # Line 0 arrived exactly as invoiced -- the ordinary delivery, and the
    # case where filling the box in is typing the same number twice.
    # Line 1 is short: eight turned up, ten were charged for.
    # Line 2 is ticked but has no quantity, which is a row somebody started
    # and abandoned; it must not reach the ledger as a zero movement.
    # Line 3 is not ticked at all.
    r = oc.post(f"/expenses/{expense}/apply-invoice", data={
        "apply": ["0", "1", "2"],
        "item_0": str(items[0]), "qty_0": "25", "cost_0": "1.10",
        "invoiced_0": "25",
        "item_1": str(items[1]), "qty_1": "8", "cost_1": "6.90",
        "invoiced_1": "10",
        "item_2": str(items[2]), "qty_2": "0", "cost_2": "0.80",
        "item_3": str(items[3]), "qty_3": "5", "cost_3": "0.80",
    }, follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("it says how many lines went in", "2 lines added to stock" in said,
            detail=said or "nothing was said")
    s.check("the first is in stock", stock_of(items[0]) == 25,
            detail=f"{stock_of(items[0])}")
    s.check("the second at the quantity the owner left, not the invoice's",
            stock_of(items[1]) == 8,
            detail=f"{stock_of(items[1])} — their edit beats the suggestion")
    s.check("a ticked line with no quantity does not go in",
            stock_of(items[2]) == 0,
            detail=f"{stock_of(items[2])} — a row somebody started and left "
                   "blank is not a delivery of nothing, it is not a delivery")
    s.check("and the line nobody ticked did not go in",
            stock_of(items[3]) == 0,
            detail=f"{stock_of(items[3])} — a line the owner did not tick "
                   "reaching the ledger is stock the château does not have")

    s.check("the difference between delivered and invoiced is kept",
            conn.execute(
                "SELECT invoiced_quantity FROM stock_movements "
                "WHERE stock_item_id = ?", (items[1],)).fetchone()[
                "invoiced_quantity"] == 10,
            detail="eight arrived and ten were charged for; the gap is the "
                   "whole reason that column exists")
    s.check("and is NOT recorded when they match",
            conn.execute(
                "SELECT invoiced_quantity FROM stock_movements "
                "WHERE stock_item_id = ?", (items[0],)).fetchone()[
                "invoiced_quantity"] is None,
            detail="the form DID send invoiced_0 = 25 against a delivery of "
                   "25; storing it would fill the column on every ordinary "
                   "line and leave nothing to notice on the short one")

    s.check("the item's cost is brought up to what was paid",
            abs((conn.execute("SELECT unit_cost FROM stock_items WHERE id = ?",
                              (items[0],)).fetchone()["unit_cost"] or 0)
                - 1.10) < 0.001,
            detail="the valuation should reflect the last invoice, not what "
                   "was typed when the item was created")

    s.section("And ticking nothing says so rather than claiming success")
    r = oc.post(f"/expenses/{expense}/apply-invoice", data={},
                follow_redirects=True)
    s.check("it says nothing changed",
            "Nothing was ticked" in " ".join(flashes(r)),
            detail=str(flashes(r))[:100])

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
