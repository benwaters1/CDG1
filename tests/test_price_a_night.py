"""No nightly figure on a retreat; a room's rate is per room, a night (owner, 27 Nov 2026).

From 23 Oct to 27 Nov every retreat's price carried a second line, "about €690
a night": the price over the nights, worked out on the page by a_night in
app.py. It was on the /workshops cards, under each date in "When You Can
Come", in the Price row of a retreat's At a Glance ("€4,800 per person,
sharing a room (about €690 a night): every meal..."), under each date in
Choose Your Stay, and under the next dates on the home page. The owner took it
off all of them: a retreat is sold on one price per person with nothing to pay
at the end, and a nightly figure beside it was read against a room's rate. The
helper went too, so nothing is left to bring it back by accident.

This holds, for a retreat with a known price and length (where the old line
read "about €930 a night") and for every retreat on offer, in English, French
and Spanish:

  - no retreat card, dates card, At a Glance or home-page date carries a
    figure a night, in any of the three languages;
  - the total is still there word for word, "€2,790 per person · nothing to pay
    at the end", and At a Glance reads "€2,790 per person, sharing a room: every
    meal..." with nothing in brackets;
  - the currency switcher has only the total to convert: it converts what
    carries data-eur, and every data-eur on a retreat card is the price itself.

And a room's rate still says "per room, a night": that one stays.

The nights a retreat runs (workshop_nights) are still worked out, for the names
of its days, so those checks stay here too.
"""
import re
from datetime import timedelta

from _harness import Suite, db, visible_text, house_today, ensure_room, datetime_now
import _harness

m = _harness.m

TITLE = "Price Line Atelier"
PRICE, NIGHTS = 2790, 3
OLD_LINE = "about €930 a night"  # what a_night printed for this one

# A figure followed straight away by "a night" in any of the three languages:
# "€930 a night", "930 € a night", "930 € la nuit", "930 € por noche". A
# room's "From €270 per room, a night" has words between, and is not a
# retreat's anyway; the checks below only read retreat blocks.
_NIGHT = r"(?:a night|per night|each night|par nuit|la nuit|por noche|la noche|cada noche)"
_FIG = r"(?:[€$£]\s?\d[\d.,\s]*|\d[\d.,\s]*€)"
NIGHTLY = re.compile(_FIG + r"\s*" + _NIGHT, re.I)
ABOUT = re.compile(r"\b(?:about|environ|aproximadamente|unos)\s+(?:€\s?)?\d", re.I)

_ARTICLE = re.compile(r'<article class="(?:g-wscard|g-sit)\b.*?</article>', re.S)
_FACTS = re.compile(r'<dl class="g-facts-list">.*?</dl>', re.S)
_HOME_DATES = re.compile(r'<ul class="g-full__list">.*?</ul>', re.S)


def _card(page, opener):
    """The card that opens with `opener`, to the end of its article."""
    at = page.find(opener)
    if at < 0:
        return ""
    return page[at:page.find("</article>", at)]


def _get(client, url):
    r = client.get(url)
    return r.status_code, r.get_data(as_text=True)


def _nightly(block):
    """Any figure a night in this block of HTML, as read on screen."""
    words = visible_text(block)
    return [x.group(0) for x in NIGHTLY.finditer(words)] + [x.group(0) for x in ABOUT.finditer(words)]


def _switch(on):
    conn = db()
    try:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('show_language_switcher', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if on else "",))
        conn.commit()
    finally:
        conn.close()


def _switch_was_on():
    conn = db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'show_language_switcher'").fetchone()
        return bool(row and row["value"])
    finally:
        conn.close()


def run():
    s = Suite("No figure a night on a retreat, and per room on a room")

    s.section("The helper is gone")
    s.check("app.py no longer works out a figure a night", not hasattr(m, "about_a_night"))
    s.check("and no template can ask for one", "a_night" not in m.app.jinja_env.globals)

    s.section("The nights a retreat runs, still counted for its days")
    nights = m.workshop_nights
    s.check("from the label", nights({"nights_label": "4 nights / 5 days"}) == 4)
    s.check("a label in words", nights({"nights_label": "Seven nights"}) == 7)
    s.check("one night", nights({"nights_label": "1 night"}) == 1)
    s.check("no label, no count", nights({"nights_label": ""}) is None)
    s.check("a label without a number, no count", nights({"nights_label": "A long weekend"}) is None)
    s.check("a session's own dates win over the label",
            nights({"nights_label": "3 nights / 4 days"},
                   {"start_date": "2027-07-10", "end_date": "2027-07-17"}) == 7)
    s.check("dates that make no sense fall back to the label",
            nights({"nights_label": "3 nights / 4 days"},
                   {"start_date": "2027-07-10", "end_date": None}) == 3)

    today = house_today()
    start, end = today + timedelta(days=60), today + timedelta(days=60 + NIGHTS)
    conn = db()
    wid = conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, active, nights_label, inclusions,
           default_capacity, created_at) VALUES (?, '', ?, 1, '3 nights / 4 days', 'Per person, sharing a room.', 15, ?)""",
        (TITLE, PRICE, datetime_now())).lastrowid
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
           VALUES (?, ?, ?, 15, ?)""",
        (wid, start.isoformat(), end.isoformat(), datetime_now()))
    conn.commit()
    conn.close()
    was_on = _switch_was_on()
    client = m.app.test_client()
    total = f"€{PRICE:,} per person · nothing to pay at the end"
    try:
        s.section("/workshops: The Retreats and When You Can Come")
        code, page = _get(client, "/workshops")
        s.check("/workshops opens", code == 200, detail=f"HTTP {code}")
        card = _card(page, f'<h3 class="g-wscard__name">{TITLE}</h3>')
        dated = _card(page, f'<p class="g-sit__name">{TITLE}</p>')
        s.check("the retreat has a card and a dates card", bool(card) and bool(dated))
        for name, block in (("card", card), ("dates card", dated)):
            words = visible_text(block)
            s.check(f"its {name} keeps the total: {total}", total in words, detail=words[-220:])
            s.check(f"its {name} says nothing a night", not _nightly(block) and OLD_LINE not in words,
                    detail=str(_nightly(block)))
        eurs = re.findall(r'data-eur="([^"]*)"', card)
        s.check("the switcher converts the total and nothing else on the card",
                eurs and all(float(e) == PRICE for e in eurs), detail=str(eurs))

        s.section("The retreat's own page: At a Glance and Choose Your Stay")
        own_url = m.retreat_path(wid)
        code, own = _get(client, own_url)
        s.check("its own page opens", code == 200, detail=f"HTTP {code}")
        facts = visible_text("".join(_FACTS.findall(own)))
        want = f"€{PRICE:,} per person, sharing a room: every meal and every glass"
        s.check(f"the price row reads '{want}...'", want in facts, detail=facts[facts.find("Price"):][:200])
        s.check("with nothing in brackets after the price", f"€{PRICE:,} per person, sharing a room (" not in facts)
        s.check("and nothing a night in At a Glance", not _nightly("".join(_FACTS.findall(own))),
                detail=str(_nightly("".join(_FACTS.findall(own)))))
        dates = _card(own, f'<p class="g-sit__name">{TITLE}</p>')
        s.check("its dates card keeps the total", total in visible_text(dates), detail=visible_text(dates)[-220:])
        s.check("and says nothing a night", not _nightly(dates), detail=str(_nightly(dates)))
        s.check("the small line under the price, where there is one, is only the balance date",
                "g-pernight" not in dates or "Balance due by" in visible_text(dates))

        s.section("Every retreat on offer, in English, French and Spanish")
        _switch(True)
        conn = db()
        offered = [r["id"] for r in conn.execute(
            "SELECT id FROM workshops WHERE active = 1 AND COALESCE(price_per_person, 0) > 0").fetchall()]
        conn.close()
        for prefix, lang in (("", "English"), ("/fr", "French"), ("/es", "Spanish")):
            code, page = _get(client, prefix + "/workshops")
            s.check(f"{lang} /workshops opens", code == 200, detail=f"HTTP {code}")
            blocks = _ARTICLE.findall(page)
            found = [x for b in blocks for x in _nightly(b)]
            s.check(f"{lang} /workshops: {len(blocks)} retreat cards, none with a figure a night",
                    bool(blocks) and not found, detail=str(found[:6]))
            pages, bad = 0, []
            for w in offered:
                code, own = _get(client, prefix + m.retreat_path(w))
                if code != 200:
                    continue  # a retreat with no public page has nothing to say a night
                pages += 1
                for b in _ARTICLE.findall(own) + _FACTS.findall(own):
                    bad += [f"{m.retreat_path(w)}: {x}" for x in _nightly(b)]
            s.check(f"{lang}: {pages} retreat pages, none with a figure a night in its dates or At a Glance",
                    pages > 0 and not bad, detail=str(bad[:6]))
            code, home = _get(client, prefix + "/")
            s.check(f"{lang} home opens", code == 200, detail=f"HTTP {code}")
            listed = "".join(_HOME_DATES.findall(home))
            s.check(f"{lang} home: the next dates say nothing a night",
                    not _nightly(listed) and "g-full__night" not in home, detail=str(_nightly(listed)))
    finally:
        _switch(was_on)
        conn = db()
        conn.execute("DELETE FROM workshop_sessions WHERE workshop_id = ?", (wid,))
        conn.execute("DELETE FROM workshops WHERE id = ?", (wid,))
        conn.commit()
        conn.close()

    s.section("A room's rate is per room, a night, and stays so")
    client = m.app.test_client()  # a new reader: the last one chose Spanish
    room = ensure_room()
    conn = db()
    priced = conn.execute(
        "SELECT COUNT(*) AS n FROM rooms WHERE active = 1 AND COALESCE(price_per_night, 0) > 0").fetchone()["n"]
    conn.close()
    code, stay = _get(client, "/book")
    s.check("/book opens", code == 200, detail=f"HTTP {code}")
    words = visible_text(stay)
    said = words.count("per room, a night · breakfast included")
    s.check("every priced room card says per room, a night", priced > 0 and said >= priced,
            detail=f"{said} card(s) say it, {priced} room(s) priced")
    s.check("none is left saying only 'a night · room and breakfast'",
            "a night · room and breakfast" not in words)
    code, own = _get(client, f"/book/{room['id']}")
    s.check("a room's own page opens", code == 200, detail=f"HTTP {code}")
    s.check("and its rate says per room, a night", "per room, a night" in visible_text(own))
    code, home = _get(client, "/")
    if "g-price__u" in home:
        s.check("the home page says per room, a night too",
                "per room, a night" in visible_text(home))
    return s


if __name__ == "__main__":
    print(run().report())
