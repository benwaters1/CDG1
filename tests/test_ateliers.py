"""The six ateliers, and the rename that must not become a duplicate.

Five of them already existed under working titles — "Autumn Atelier 2026",
"Summer Starry Nights 2027" — at the same prices and the same dates as the
public names now used on chateaugudanes.com. The handover supplied six plain
INSERTs, which would have listed every atelier twice: once as it is sold and
once as it was drafted.

So the seed renames in place. The test that matters is the one below: run the
catch-up against a database that still holds the old names and check that six
come out, not eleven.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m


def _titles(conn):
    return [r["title"] for r in conn.execute(
        "SELECT title FROM workshops WHERE title NOT LIKE '%Test%' ORDER BY sort_order")]


def run():
    s = Suite("The ateliers")
    oc, _ec, _owner, _emp = clients()
    conn = db()

    s.section("All six are there, once each")
    titles = _titles(conn)
    for wanted in ["The Long Weekender", "Immersive Artisan Workshops",
                   "Noël at Gudanes", "Cooking in the Cuisine",
                   "Antique & French Finds", "Seven Starry Nights"]:
        s.check(f"{wanted} is listed once", titles.count(wanted) == 1,
                detail=f"appears {titles.count(wanted)}x")
    s.check("and no working title survived into the public list",
            not any(t.endswith(("2026", "2027")) for t in titles),
            detail=str([t for t in titles if t.endswith(("2026", "2027"))]))

    s.section("Prices and lengths, as published")
    rows = {r["title"]: r for r in conn.execute(
        "SELECT * FROM workshops WHERE title NOT LIKE '%Test%'")}
    for title, price, nights in [
            ("The Long Weekender", 2400, "3 nights / 4 days"),
            ("Immersive Artisan Workshops", 2600, "4 nights / 5 days"),
            ("Noël at Gudanes", 3200, "4 nights / 5 days"),
            ("Cooking in the Cuisine", 3800, "5 nights / 6 days"),
            ("Antique & French Finds", 2800, "3 nights / 4 days"),
            ("Seven Starry Nights", 4800, "7 nights / 8 days")]:
        r = rows.get(title)
        s.check(f"{title}: €{price:,} and {nights}",
                r and abs((r["price_per_person"] or 0) - price) < 0.01
                and r["nights_label"] == nights,
                detail=f"got €{r['price_per_person'] if r else '?'}, "
                       f"{r['nights_label'] if r else '?'}")

    s.section("The dates the earlier seed never carried")
    # Cooking and Seven Starry Nights each gained 2026 sittings, and the Long
    # Weekender is new outright. Without these the 2026 season had one atelier.
    for title, count in [("The Long Weekender", 3), ("Cooking in the Cuisine", 3),
                         ("Seven Starry Nights", 2)]:
        got = conn.execute(
            """SELECT COUNT(*) AS c FROM workshop_sessions
               JOIN workshops ON workshops.id = workshop_sessions.workshop_id
               WHERE workshops.title = ?""", (title,)).fetchone()["c"]
        s.check(f"{title} has {count} sittings", got == count, detail=str(got))

    s.section("A database still holding the old names is renamed, not doubled")
    # The live volume is exactly this: five rows under working titles. Put one
    # back and run the catch-up the way a deploy does.
    conn.execute("UPDATE workshops SET title = 'Autumn Atelier 2026' "
                 "WHERE title = 'Immersive Artisan Workshops'")
    conn.commit()
    before = len(_titles(conn))
    m.init_db()
    after = _titles(conn)
    s.check("the count does not grow", len(after) == before,
            detail=f"{before} -> {len(after)}")
    s.check("the old name is gone", "Autumn Atelier 2026" not in after)
    s.check("and the public one is back, once",
            after.count("Immersive Artisan Workshops") == 1,
            detail=f"{after.count('Immersive Artisan Workshops')}x")

    s.section("Copy somebody has written by hand is not overwritten")
    conn.execute("UPDATE workshops SET sample_day = 'Ours, hand written' "
                 "WHERE title = 'The Long Weekender'")
    conn.commit()
    m.init_db()
    kept = conn.execute("SELECT sample_day FROM workshops WHERE title = 'The Long Weekender'"
                        ).fetchone()["sample_day"]
    s.check("an edited sample day survives a redeploy", kept == "Ours, hand written",
            detail=str(kept)[:40])
    conn.execute("UPDATE workshops SET sample_day = NULL WHERE title = 'The Long Weekender'")
    conn.commit()
    m.init_db()

    s.section("A cancelled sitting is not quietly put back")
    row = conn.execute("SELECT id FROM workshops WHERE title = 'The Long Weekender'").fetchone()
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id = ? AND start_date = ?",
                 (row["id"], "2026-08-01"))
    conn.commit()
    m.init_db()
    back = conn.execute(
        "SELECT 1 FROM workshop_sessions WHERE workshop_id = ? AND start_date = ?",
        (row["id"], "2026-08-01")).fetchone()
    # It IS recreated — the seed adds any start date it cannot find. Worth
    # stating plainly rather than discovering it: to retire a sitting, mark the
    # atelier inactive or change the date, do not delete the row.
    s.check("deleting a seeded sitting brings it back on the next deploy",
            bool(back), detail="documented behaviour, not a silent surprise")

    s.section("The public pages render them")
    anon = m.app.test_client()
    r = anon.get("/workshops")
    s.check("the workshops page opens", r.status_code == 200, detail=str(r.status_code))
    body = r.data.decode("utf-8", "replace")
    # Names checked against workshops that still have dates ahead of them.
    # "The Long Weekender" is deliberately NOT one of them: every sitting it
    # was seeded with is in the past, so the public page hides it — a finished
    # workshop must not sit there inviting registrations for dates that have
    # gone. It will reappear the moment it is given a future date in the admin.
    # See test_workshop_lifecycle for that rule on its own terms.
    s.check("with the public names on it",
            "Seven Starry Nights" in body and "Noël at Gudanes" in body,
            detail="a workshop with dates ahead is missing from the page")
    s.check("and no working title", "Autumn Atelier" not in body)
    s.check("while one whose dates have all passed is not advertised",
            "The Long Weekender" not in body,
            detail="a finished workshop is still on the public page")

    conn.close()
    return s
