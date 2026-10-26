"""What a workshop comes to a night, and what a room's rate is for (owner, 23 Oct 2026).

Under each workshop's price on /workshops and on the workshop's own page, one
small line: "about €650 a night", the price over the nights to the nearest
€10. It is worked out on the page (a_night in app.py), never typed, so it has
to follow the price and the length; this suite changes both and reads it
again. The owner's own figures for the five ateliers are checked as sums.

And a room's rate says "per room, a night", so nobody reads it as per person,
which is how the workshops are priced.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from _harness import Suite, db, visible_text, house_today, ensure_room, datetime_now
import _harness

m = _harness.m

TITLE = "A Night Priced Atelier"


def _expect(price, nights):
    tens = int((Decimal(str(price)) / nights / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"about €{tens * 10:,} a night"


def _card(page, opener):
    """The card that opens with `opener`, to the end of its article."""
    at = page.find(opener)
    if at < 0:
        return ""
    return page[at:page.find("</article>", at)]


def _page(url):
    r = m.app.test_client().get(url)
    return r.status_code, r.get_data(as_text=True)


def run():
    s = Suite("About € a night, and per room")

    s.section("The sum, on the owner's five")
    owners = [  # price, nights, what the owner worked out
        ("Immersive Artisan Workshops", 2600, 4, "about €650 a night"),
        ("Noël at Gudanes", 3200, 4, "about €800 a night"),
        ("Cooking in the Cuisine", 3800, 5, "about €760 a night"),
        ("Antique & French Finds", 2800, 3, "about €930 a night"),
        ("Seven Starry Nights", 4800, 7, "about €690 a night"),
    ]
    for name, price, nights, said in owners:
        got = m.about_a_night(price, nights)
        s.check(f"{name}: {said}", got == said, detail=got)
    # 1,325 over one night is 132.5 tens: half up is €1,330, where Python's
    # round() would give €1,320.
    got = m.about_a_night(1325, 1)
    s.check("a half rounds up, as money does", got == "about €1,330 a night", detail=got)
    for price, nights in ((None, 4), (2600, None), (2600, 0), (0, 4), ("", 4)):
        got = m.about_a_night(price, nights)
        s.check(f"nothing at all for price {price!r} over {nights!r} nights", got == "", detail=got)

    s.section("The nights it divides by")
    nights = m.workshop_nights
    s.check("from the label", nights({"nights_label": "4 nights / 5 days"}) == 4)
    s.check("a label in words", nights({"nights_label": "Seven nights"}) == 7)
    s.check("one night", nights({"nights_label": "1 night"}) == 1)
    s.check("no label, no figure", nights({"nights_label": ""}) is None)
    s.check("a label without a number, no figure", nights({"nights_label": "A long weekend"}) is None)
    s.check("a session's own dates win over the label",
            nights({"nights_label": "3 nights / 4 days"},
                   {"start_date": "2027-07-10", "end_date": "2027-07-17"}) == 7)
    s.check("dates that make no sense fall back to the label",
            nights({"nights_label": "3 nights / 4 days"},
                   {"start_date": "2027-07-10", "end_date": None}) == 3)

    s.section("On the page, and following the price and the length")
    today = house_today()
    start, end = today + timedelta(days=60), today + timedelta(days=63)
    conn = db()
    wid = conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, active, nights_label,
           default_capacity, created_at) VALUES (?, '', 2790, 1, '3 nights / 4 days', 15, ?)""",
        (TITLE, datetime_now())).lastrowid
    sid = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
           VALUES (?, ?, ?, 15, ?)""",
        (wid, start.isoformat(), end.isoformat(), datetime_now())).lastrowid
    conn.commit()
    conn.close()
    try:
        code, page = _page("/workshops")
        s.check("/workshops opens", code == 200, detail=f"HTTP {code}")
        card = _card(page, f'<h3 class="g-wscard__name">{TITLE}</h3>')
        s.check("the atelier has a card", bool(card))
        words = visible_text(card)
        s.check("the card says about €930 a night", "about €930 a night" in words, detail=words[-200:])
        s.check("and still says per person", "per person" in words)
        price_ends = card.find("</p>", card.find('class="g-wscard__price"'))
        line = card.find('class="g-pernight"')
        s.check("the line comes after the price, on its own",
                0 < price_ends < line < card.find('class="g-btn"'),
                detail=f"price ends {price_ends}, line at {line}")
        dated = visible_text(_card(page, f'<p class="g-sit__name">{TITLE}</p>'))
        s.check("so does the card for its dates", "about €930 a night" in dated, detail=dated[-200:])

        code, own = _page(f"/workshops/{wid}")
        s.check("its own page opens", code == 200, detail=f"HTTP {code}")
        dates = visible_text(_card(own, f'<p class="g-sit__name">{TITLE}</p>'))
        s.check("its dates card says about €930 a night", "about €930 a night" in dates, detail=dates[-200:])
        said = visible_text(own)
        s.check("and the price in its facts says it",
                "€2,790 per person (about €930 a night)" in said,
                detail=said[said.find("Price"):][:160])

        # A new price and a longer week: nothing typed anywhere has to move.
        conn = db()
        conn.execute("UPDATE workshops SET price_per_person = 3000, nights_label = '4 nights / 5 days' "
                     "WHERE id = ?", (wid,))
        conn.execute("UPDATE workshop_sessions SET end_date = ? WHERE id = ?",
                     ((start + timedelta(days=4)).isoformat(), sid))
        conn.commit()
        conn.close()
        want = _expect(3000, 4)
        _c, page = _page("/workshops")
        words = visible_text(_card(page, f'<h3 class="g-wscard__name">{TITLE}</h3>'))
        s.check(f"a new price and length: {want}", want in words, detail=words[-200:])
        s.check("and not the old figure", "about €930 a night" not in words)

        # Dates that run longer than the label: the dated card counts the dates.
        conn = db()
        conn.execute("UPDATE workshop_sessions SET end_date = ? WHERE id = ?",
                     ((start + timedelta(days=5)).isoformat(), sid))
        conn.commit()
        conn.close()
        _c, page = _page("/workshops")
        dated = visible_text(_card(page, f'<p class="g-sit__name">{TITLE}</p>'))
        s.check(f"five nights of dates: {_expect(3000, 5)}", _expect(3000, 5) in dated, detail=dated[-200:])
        words = visible_text(_card(page, f'<h3 class="g-wscard__name">{TITLE}</h3>'))
        s.check(f"while the atelier's card keeps its four: {want}", want in words)

        # No length known at all: no line, rather than a guess.
        conn = db()
        conn.execute("UPDATE workshops SET nights_label = NULL WHERE id = ?", (wid,))
        conn.commit()
        conn.close()
        _c, page = _page("/workshops")
        card = _card(page, f'<h3 class="g-wscard__name">{TITLE}</h3>')
        s.check("no length, no line", "a night" not in visible_text(card) and "g-pernight" not in card,
                detail=visible_text(card)[-160:])
    finally:
        conn = db()
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id = ?", (wid,))
        conn.execute("DELETE FROM workshops WHERE id = ?", (wid,))
        conn.commit()
        conn.close()

    s.section("A room's rate is per room")
    room = ensure_room()
    conn = db()
    priced = conn.execute(
        "SELECT COUNT(*) AS n FROM rooms WHERE active = 1 AND COALESCE(price_per_night, 0) > 0").fetchone()["n"]
    conn.close()
    code, stay = _page("/book")
    s.check("/book opens", code == 200, detail=f"HTTP {code}")
    words = visible_text(stay)
    said = words.count("per room, a night · breakfast included")
    s.check("every priced room card says per room, a night", priced > 0 and said >= priced,
            detail=f"{said} card(s) say it, {priced} room(s) priced")
    s.check("none is left saying only 'a night · room and breakfast'",
            "a night · room and breakfast" not in words)
    code, own = _page(f"/book/{room['id']}")
    s.check("a room's own page opens", code == 200, detail=f"HTTP {code}")
    s.check("and its rate says per room, a night", "per room, a night" in visible_text(own))
    code, home = _page("/")
    if "g-price__u" in home:
        s.check("the home page says per room, a night too",
                "per room, a night" in visible_text(home))
    return s


if __name__ == "__main__":
    print(run().report())
