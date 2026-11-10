"""A night is a room and breakfast, with the restaurants nearby; a retreat has the gourmet chefs (owner, 10 Nov 2026).

First: "in the comparisons make it clear that one is a b&b - there is no lunch
or dinner or chefs on site; workshops there is gourmet chefs with lunch and
dinner included". Then, the same day: "change the comparisons back to room and
breakfast so its succinct / but have something where it says lunch and dinner
unavailable and point it to the restaurants nearby". The three comparisons in
_stay_or_workshop.html:
  - the two columns on the home page and Stay
  - the side-by-side table on /workshops
  - the notice before a night is booked, on Stay and each room's page
Each calls the night a room and breakfast, says its lunch and dinner are
unavailable and links the restaurants nearby -- a map search for restaurants
centred on the pin at the gates, never a search by name -- and gives the
retreat its gourmet chefs, every meal, and drinks included. The night never
offers a dinner, whatever the dining switch says, since there is no chef.
"""
import re

from _harness import Suite, db, visible_text
import _harness

m = _harness.m

STAY_OFFERS_DINNER = re.compile(
    r"Dinner, booked separately|dinner on the nights the table is open|Dinner can be booked", re.I)
PIN = ("42.783835", "1.680329")
EAT = "https://www.google.com/maps/search/restaurants/@42.783835,1.680329,13z"
LINK = re.compile(r'<a href="([^"]*)"([^>]*)>restaurants nearby</a>')


def _block(page, start, end):
    i = page.find(start)
    j = page.find(end, i + 1) if i != -1 else -1
    return page[i:j] if i != -1 and j != -1 else ""


def _setting(conn, key, value):
    conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    if value is not None:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def _linked(html):
    """Every 'restaurants nearby' in this piece links the search at the pin, in a new tab, safely."""
    links = LINK.findall(html)
    return bool(links) and all(href == EAT and 'target="_blank"' in attrs and "noopener" in attrs
                               for href, attrs in links)


def run():
    s = Suite("Room and breakfast, or the chefs")
    client = m.app.test_client()
    conn = db()
    keys = ("dining_mode", "house_lat", "house_lng")
    was = {k: (conn.execute("SELECT value FROM app_settings WHERE key = ?", (k,)).fetchone() or [None])[0] for k in keys}
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY sort_order LIMIT 1").fetchone()
    try:
        _setting(conn, "house_lat", PIN[0])
        _setting(conn, "house_lng", PIN[1])
        s.check("the restaurants nearby are a search at the pin, never by name",
                m.house_map_links().get("eat") == EAT, detail=str(m.house_map_links().get("eat")))
        for mode in ("open", "hidden"):
            _setting(conn, "dining_mode", mode)
            s.section(f"With the dining switch {mode}")
            pages = {p: client.get(p).get_data(as_text=True) for p in ("/", "/book", "/workshops", f"/book/{room['id']}")}

            for path in ("/", "/book"):
                panel = _block(pages[path], 'class="g-sec g-sow"', "</section>")
                stay_html = _block(panel, 'class="g-sow__col', 'g-sow__col--full')
                stay, retreat = visible_text(stay_html), visible_text(_block(panel, 'g-sow__col--full', "</article>"))
                s.check(f"{path}: the night is a room and breakfast", "Room and breakfast" in stay, detail=stay[:160])
                s.check(f"{path}: its lunch and dinner unavailable, the restaurants nearby linked",
                        "Lunch and dinner unavailable: restaurants nearby" in stay and _linked(stay_html), detail=stay[:200])
                s.check(f"{path}: and no dinner offered to it", not STAY_OFFERS_DINNER.search(stay), detail=stay[:240])
                s.check(f"{path}: the retreat has the gourmet chefs, every meal, and drinks included",
                        "gourmet chefs" in retreat and "every meal, and drinks included" in retreat, detail=retreat[:200])

            table = _block(pages["/workshops"], 'id="compare"', "</section>")
            s.check("/workshops: above the table, the retreat's every meal, and drinks included",
                    "every meal, and drinks included" in visible_text(table))
            s.check("/workshops: the table calls the night a room and breakfast",
                    '<span class="g-vs__t">Room and breakfast</span>' in table and "a room and breakfast." in visible_text(table))
            meals = re.search(r'<th scope="row">Lunch and dinner</th><td>(.*?)</td><td class="g-vs__full">(.*?)</td>', table, re.S)
            s.check("/workshops: lunch and dinner unavailable with a night, the restaurants nearby linked; every meal and drinks with a retreat",
                    bool(meals) and visible_text(meals.group(1)).strip() == "Unavailable: restaurants nearby"
                    and _linked(meals.group(1)) and "Every meal, and drinks included" in meals.group(2),
                    detail=meals.group(0)[:240] if meals else "no row")
            chefs = re.search(r'<th scope="row">Gourmet chefs in the house</th><td>(.*?)</td><td class="g-vs__full">(.*?)</td>', table, re.S)
            s.check("/workshops: gourmet chefs in the house, not with a night; with a retreat",
                    bool(chefs) and "Not included" in chefs.group(1) and "Included" in chefs.group(2),
                    detail=chefs.group(0)[:200] if chefs else "no row")
            s.check("/workshops: the night offers no dinner", not STAY_OFFERS_DINNER.search(visible_text(table)))

            for path in ("/book", f"/book/{room['id']}"):
                notice_html = _block(pages[path], 'class="g-night"', "</aside>")
                notice = visible_text(notice_html)
                s.check(f"{path}: before a night is booked, it is called a room and breakfast",
                        "A night here is a room and breakfast." in notice, detail=notice[:160])
                s.check(f"{path}: lunch and dinner unavailable, the restaurants nearby linked, the chefs with the retreats",
                        "Lunch and dinner are unavailable: there are restaurants nearby" in notice and _linked(notice_html)
                        and "gourmet chefs" in notice and not STAY_OFFERS_DINNER.search(notice), detail=notice[:240])

        s.section("Where no pin is set, the words stand without a link")
        _setting(conn, "house_lat", None)
        _setting(conn, "house_lng", None)
        page = client.get("/book").get_data(as_text=True)
        notice_html = _block(page, 'class="g-night"', "</aside>")
        s.check("the restaurants are still mentioned", "restaurants nearby" in visible_text(notice_html))
        s.check("and nothing links to a map search that has nowhere to start",
                not LINK.search(notice_html) and "maps/search/restaurants" not in page)
    finally:
        for k, v in was.items():
            _setting(conn, k, v)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
