"""/events/weddings, the top of the page, as redone on 23 Nov 2026.

Owner: "the spacing here isnt great and theres no photo on top", and of the Marquis Suite, "the text is pretty basic".

THE HERO. The page about marrying in the château opened on a drawing of two rings on navy, the same art hero the
photoshoot page had until earlier that day. It opens on the long table now, every candle lit, moved up from the
collage below so it is drawn once; the words are the ones the art hero carried.

THE OPENING. Two sections stood side by side with a drawn medallion each and wide empty gaps, and the two wedding
photographs sat in a pair of their own further down. Each section now sits beside one of them, not a word changed.

THE MARQUIS SUITE. One sentence beside a tall photograph, and a field of empty sand. It says what the suite is for
on the day now, and its facts are a short list, each one already said on /book or by the owner.
"""
import re

from _harness import Suite
import _harness
from test_page_pictures import _visible

m = _harness.m
PATH = "/events/weddings"


def run():
    s = Suite("The top of the weddings page")
    client = m.app.test_client()
    r = client.get(PATH)
    if not s.check(f"{PATH} loads", r.status_code == 200, detail=str(r.status_code)):
        return s
    page = r.get_data(as_text=True)

    s.section("It opens on a photograph")
    hero = re.search(r'<section class="g-hero[^"]*">(.*?)</section>', page, re.S)
    hero = hero.group(0) if hero else ""
    # Negative control: put back the g-hero--art section with illus('rings'), and these go red.
    s.check("the hero is a photograph", 'class="g-hero__img"' in hero, detail=hero[:160])
    s.check("of the long table, candles lit", 'alt="The long table, every candle lit"' in hero)
    s.check("not the drawn art on navy", "g-hero--art" not in hero)
    s.check("its words are the ones the art hero carried",
            all(w in hero for w in ("Marry in a Monument", "Not a wing, not an afternoon", "Ask about a date")))
    body = re.search(r'<body class="([^"]*)"', page)
    s.check("the header lies over it", body is not None and "has-hero" in body.group(1),
            detail=body.group(1) if body else "no body class")

    s.section("The two opening sections, each beside a photograph")
    # 24 Nov 2026 (owner: the order, most important first): All Inclusive comes between the two now, so each is a
    # section of its own, with its one photograph (test_wedding_inclusive holds the order).
    intros = re.findall(r'<section class="g-sec g-wed-intro">(.*?)</section>', page, re.S)
    intro = "".join(intros)
    s.check("two sections, a photograph beside each", [i.count('class="g-sp__main"') for i in intros] == [1, 1],
            detail=str([i.count('class="g-sp__main"') for i in intros]))
    for words in ("For the days of your wedding, the château and its grounds are yours alone.",
                  "The setting asks for little decoration: old stone, candlelight, and the valley at every window."):
        s.check(f"and keeps its words: {words[:40]}...", words in intro)
    s.check("the medallion pair is gone", "g-duo-sec" not in page)
    s.check("and so is the pair of photographs they came from", "g-wedpair" not in page)

    s.section("The Marquis Suite says enough to fill its side")
    gold = re.search(r'<section class="g-sec g-sec--sand g-wed-gold">(.*?)</section>', page, re.S)
    gold = gold.group(1) if gold else ""
    facts = re.findall(r'<li><b>([^<]*)</b>', gold)
    s.check("four facts, in a list", facts == ["70 m²", "An emperor bed", "Mountain and front views", "A bath"],
            detail=str(facts))
    book = client.get("/book").get_data(as_text=True)
    # Every fact is one the room's card on /book already states, so the page claims nothing the house has not.
    # (The views are the room's own feature list, which lives in the database; the owner gave them on 14 Oct.)
    s.check("the size, the bed and the bath are what /book says", "70 m²" in book and "emperor bed" in book
            and "The King Suite, the Marquis Suite and the Family Suite have the use of a larger bathroom, with a bath" in book)

    s.section("Nothing twice")
    shown = _visible(page)
    for alt in ("The long table, every candle lit", "A bride at the window, the mountains beyond",
                "The cake among the flowers and the candles"):
        n = len(re.findall(r'<img\b[^>]*\salt="' + re.escape(alt), shown))
        s.check(f"{alt[:40]} drawn once", n == 1, detail=str(n))
    return s


if __name__ == "__main__":
    print(run().report())
