"""The home page in pictures: the image layout brief, section 4.2 (28 Oct 2026),
as the owner's second review of the site cut it (9 Nov 2026, evening).

The second review took the page from nineteen sections to eight. The eight
moments, the estate band, the film strip, the postcard beside the journal and
A Night at Gudanes went; test_site_review_2 holds the eight and their order.
What is left of the first brief, and would go quietly if it went:

  - the rooms as a slider of their cards, one photograph each, the estate from
    the air as its first slide (in place of the band it had)
  - the dining room's before and now side by side, each whole, with the page's own two words
    (owner's site-copy brief, 11 Nov 2026: the slider hid the horse and jumbled two photographs
    taken facing different ways)
  - each guest's quote a gilded cartouche: its photograph in an oval hung from a bow, and the wax
    seal by "A guest of the château" (the same brief, part 4)
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

    s.section("the brief's components, where they still are")
    s.check("the eight moments, the estate band, the film strip and the postcard went (owner, 9 Nov)",
            "g-moments" not in page and 'class="g-plein g-plein--words"' not in page
            and 'class="g-bande' not in page and "postcard-valley" not in page)
    conn = db()
    rooms = conn.execute("SELECT id FROM rooms WHERE active = 1").fetchall()
    conn.close()
    cars = page[page.find('class="g-car g-car--cards"'):]
    cars = cars[:cars.find("</section>")]
    cards = cars.count('<article class="g-homeroom g-car__s"')
    s.check("the rooms are a slider of their cards, every room", cards == len(rooms) and cards > 0,
            detail=f"{cards} cards, {len(rooms)} rooms")
    s.check("the estate from the air is its first slide",
            cars.find('g-homeroom--aerial') > 0 and cars.find("from-the-air-pool-garden") > 0
            and cars.find('g-homeroom--aerial') < cars.find('<article class="g-homeroom g-car__s"'))
    s.check("one photograph a card, not a slideshow in each",
            len(re.findall(r"<img\b", cars)) == cards + 1 and "g-slides" not in cars)
    s.check("the slider has its arrows and its count, the estate included",
            'data-car-prev' in cars and f"1 / {cards + 1}" in cars)
    resto = page[page.find("g-homeresto"):]
    pair = resto[:resto.find("</section>")]
    figs = re.findall(r'<figure class="g-homeresto__f">(.*?)</figure>', pair, re.S)
    s.check("the dining room's before and now side by side, not a slider",
            len(figs) == 2 and "data-ba" not in pair and 'class="g-aa ' not in pair, detail=f"{len(figs)} photographs")
    s.check("before on the left, the horse's photograph, and nothing pushing it sideways",
            bool(figs) and "horse-dining-room-a" in figs[0] and "object-position" not in figs[0])
    s.check("with the page's own two words on them, Before and Now",
            len(figs) == 2 and ">Before<" in figs[0] and ">Now<" in figs[1])
    says = page[page.find('class="g-sec g-says rc-says"'):]
    says = says[:says.find("</section>")]
    quotes = says.count('<blockquote class="rc-q">')
    s.check("every guest's quote a cartouche: an oval photograph on a bow, and a seal",
            quotes >= 2 and says.count('class="rc-oval"') == quotes and says.count('class="rc-bow"') == quotes
            and len(re.findall(r'class="g-seal[ "]', says)) == quotes,
            detail=f"{quotes} quotes")
    s.check("signed as a guest of the château, with no name or stars made up",
            says.count("A guest of the château") >= 2 and "★" not in says and "&#9733;" not in says)
    s.check("and the two navy rules that sat over the cards are gone", 'class="g-fleur"' not in says)
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
