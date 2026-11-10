"""A night is a bed and breakfast; a retreat has the gourmet chefs (owner, 10 Nov 2026).

"in the comparisons make it clear that one is a b&b - there is no lunch or
dinner or chefs on site; workshops there is gourmet chefs with lunch and
dinner included". The three comparisons in _stay_or_workshop.html:
  - the two columns on the home page and Stay
  - the side-by-side table on /workshops
  - the notice before a night is booked, on Stay and each room's page
Each says the night is a bed and breakfast with no lunch, no dinner and no
chefs in the house, and the retreat has gourmet chefs with lunch and dinner
included -- and the night never offers a dinner, whatever the dining switch
says, because there is no chef to cook it.
"""
import re

from _harness import Suite, db, visible_text
import _harness

m = _harness.m

STAY_OFFERS_DINNER = re.compile(
    r"Dinner, booked separately|dinner on the nights the table is open|Dinner can be booked", re.I)


def _block(page, start, end):
    i = page.find(start)
    j = page.find(end, i + 1) if i != -1 else -1
    return page[i:j] if i != -1 and j != -1 else ""


def _set_dining(conn, mode):
    conn.execute("DELETE FROM app_settings WHERE key = 'dining_mode'")
    if mode is not None:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_mode', ?)", (mode,))
    conn.commit()


def run():
    s = Suite("A bed and breakfast, or the chefs")
    client = m.app.test_client()
    conn = db()
    was = conn.execute("SELECT value FROM app_settings WHERE key = 'dining_mode'").fetchone()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY sort_order LIMIT 1").fetchone()
    try:
        for mode in ("open", "hidden"):
            _set_dining(conn, mode)
            s.section(f"With the dining switch {mode}")
            pages = {p: client.get(p).get_data(as_text=True) for p in ("/", "/book", "/workshops", f"/book/{room['id']}")}

            for path in ("/", "/book"):
                panel = _block(pages[path], 'class="g-sec g-sow"', "</section>")
                stay = visible_text(_block(panel, 'class="g-sow__col', 'g-sow__col--full'))
                retreat = visible_text(_block(panel, 'g-sow__col--full', "</article>"))
                s.check(f"{path}: the night is a bed and breakfast", "Bed and breakfast" in stay, detail=stay[:160])
                s.check(f"{path}: with no lunch or dinner, and no chefs in the house",
                        "No lunch or dinner, and no chefs in the house" in stay, detail=stay[:200])
                s.check(f"{path}: and no dinner offered to it", not STAY_OFFERS_DINNER.search(stay) and "dinner on" not in stay.lower(),
                        detail=stay[:240])
                s.check(f"{path}: the retreat has the gourmet chefs, lunch and dinner included",
                        "gourmet chefs" in retreat and "lunch and dinner included" in retreat, detail=retreat[:200])

            table = _block(pages["/workshops"], 'id="compare"', "</section>")
            s.check("/workshops: the table names the night a bed and breakfast",
                    '<span class="g-vs__t">Bed and breakfast</span>' in table and "Room and breakfast" not in table)
            s.check("/workshops: and says above it there is no lunch, no dinner and no chefs",
                    "is a bed and breakfast" in visible_text(table) and "no lunch or dinner and no chefs in the house" in visible_text(table))
            meals = re.search(r'<th scope="row">Lunch and dinner</th><td>(.*?)</td><td class="g-vs__full">(.*?)</td>', table, re.S)
            s.check("/workshops: lunch and dinner, not with a night; every meal with a retreat",
                    bool(meals) and "Not included" in meals.group(1) and "Every meal, included" in meals.group(2),
                    detail=meals.group(0)[:200] if meals else "no row")
            chefs = re.search(r'<th scope="row">Gourmet chefs in the house</th><td>(.*?)</td><td class="g-vs__full">(.*?)</td>', table, re.S)
            s.check("/workshops: gourmet chefs in the house, not with a night; with a retreat",
                    bool(chefs) and "Not included" in chefs.group(1) and "Included" in chefs.group(2),
                    detail=chefs.group(0)[:200] if chefs else "no row")
            s.check("/workshops: the night offers no dinner", not STAY_OFFERS_DINNER.search(visible_text(table)))

            for path in ("/book", f"/book/{room['id']}"):
                notice = visible_text(_block(pages[path], 'class="g-night"', "</aside>"))
                s.check(f"{path}: before a night is booked, it is called a bed and breakfast",
                        "A night here is a bed and breakfast." in notice, detail=notice[:160])
                s.check(f"{path}: with no lunch or dinner and no chefs; the chefs come with the retreats",
                        "no lunch or dinner, and no chefs in the house" in notice and "gourmet chefs" in notice
                        and not STAY_OFFERS_DINNER.search(notice), detail=notice[:240])
    finally:
        _set_dining(conn, was["value"] if was else None)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
