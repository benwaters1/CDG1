"""The home page, laid out by the image layout brief, section 4.2 (28 Oct 2026).

What the brief asked of it and what would go quietly if it went:

  - the eight moments as a staggered row in slim mounts (a slider on a phone)
  - the estate from above as one drone photograph across the page, the words on it
  - the rooms as a slider of their cards, one photograph each
  - the band of five as a film strip
  - the dining room's before and now as one slider, with the page's own two words
  - a medallion of the house and a wax seal by each guest's quote
  - an old postcard beside Follow the Restoration
  - and no photograph twice. Checked by the file the house holds, not by the
    address: the same breakfast photograph is a Squarespace address in Ways to
    Spend the Days and a house copy elsewhere, and two addresses for one picture
    is still the picture twice. That is how it was caught here, once already.

The words are test_copy_unchanged's.
"""
import re

from _harness import Suite, db
import _harness

m = _harness.m


def _visible_imgs(page):
    page = re.sub(r"<div hidden>.*?</div>", "", page, flags=re.S)
    return [t for t in re.findall(r"<img\b[^>]*>", page) if "data-lb-img" not in t]


def _src(tag):
    hit = re.search(r'\ssrc="([^"]*)"', tag)
    return hit.group(1) if hit else ""


def picture_key(src):
    """What a photograph IS: the house's file for it where there is one, else its address."""
    held = m._pic_held(src) if src.startswith("https://") else None
    if held:
        return held
    base = src.split("?", 1)[0]
    if base.startswith("/static/img/"):
        base = base[len("/static/img/"):]
    return re.sub(r"-800(\.jpg)$", r"\1", base)


def run():
    s = Suite("Home page, in pictures")
    client = m.app.test_client()
    r = client.get("/")
    page = r.get_data(as_text=True)
    if not s.check("the home page loads", r.status_code == 200, detail=str(r.status_code)):
        return s

    s.section("the brief's components, where it put them")
    moments = page[page.find('class="g-moments g-moments--stagger"'):]
    moments = moments[:moments.find("</ul>")]
    s.check("the eight moments, staggered, each in its mount",
            moments.count('class="g-moment__mount"') == 8, detail=str(moments.count("g-moment__mount")))
    band = page[page.find('class="g-plein g-plein--words"'):]
    s.check("the estate from above is one photograph across the page",
            'class="g-plein g-plein--words"' in page and band.find("from-the-air-pool-garden") >= 0)
    s.check("with the heading and its words on the photograph",
            0 < band.find("The Estate from Above") < band.find("</section>"))
    s.check("and the other two aerial tiles are gone from here", "from-the-air-parterre" not in page)
    conn = db()
    rooms = conn.execute("SELECT id FROM rooms WHERE active = 1").fetchall()
    conn.close()
    cars = page[page.find('class="g-car g-car--cards"'):]
    cars = cars[:cars.find("</section>")]
    cards = cars.count('<article class="g-homeroom g-car__s"')
    s.check("the rooms are a slider of their cards, every room", cards == len(rooms) and cards > 0,
            detail=f"{cards} cards, {len(rooms)} rooms")
    s.check("one photograph a card, not a slideshow in each",
            len(re.findall(r"<img\b", cars)) == cards and "g-slides" not in cars)
    s.check("the slider has its arrows and its count", 'data-car-prev' in cars and f"1 / {cards}" in cars)
    s.check("the band of five is a film strip", re.search(r'class="g-bande g-bande--drift', page) is not None
            and 'class="g-band"' not in page)
    resto = page[page.find("g-homeresto"):]
    s.check("the dining room's before and now is one slider",
            'class="g-aa ' in resto[:4000] and "horse-dining-room-a" in resto[:4000])
    s.check("with the page's own two words on it, Before and Now",
            re.search(r'g-ba__tag--b">Before<', resto) is not None and re.search(r'g-ba__tag--a">Now<', resto) is not None)
    says = page[page.find('class="g-sec g-says"'):]
    says = says[:says.find("</section>")]
    quotes = says.count('<blockquote class="g-say__q">')
    s.check("every guest's quote has a medallion of the house and a seal",
            quotes and says.count('class="g-med ') == quotes and len(re.findall(r'class="g-seal[ "]', says)) == quotes,
            detail=f"{quotes} quotes")
    journal = page[page.find("g-journal"):]
    s.check("an old postcard beside Follow the Restoration",
            'class="g-cp ' in journal[:6000] and "postcard-valley" in journal[:6000])

    s.section("no photograph twice")
    keys = [picture_key(_src(t)) for t in _visible_imgs(page) if "Logo" not in _src(t)]
    twice = sorted({k for k in keys if keys.count(k) > 1})
    s.check("each photograph once on the page, by what it is", not twice, detail=", ".join(twice[:4]))
    breakfast = ("https://images.squarespace-cdn.com/content/v1/6a55fc287fb69872c3cc49bd/"
                 "5589cd5d-9fd8-4363-a8e8-1c7db5e8e8fb/VeronicaOlson_August2022_ChateaudeGudanes__2012.jpg?format=1000w")
    s.check("the check sees through an address to the house's file (the breakfast photograph)",
            picture_key(breakfast) == picture_key("/static/img/retreats/breakfast-croissants.jpg"),
            detail=f"{picture_key(breakfast)} vs {picture_key('/static/img/retreats/breakfast-croissants.jpg')}")
    return s


if __name__ == "__main__":
    print(run().report())
