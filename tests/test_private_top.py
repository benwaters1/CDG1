"""/events/private, as redone on 23 Nov 2026.

Owner: "same with private theirs massive ugly gaps", after the weddings page ("lets use the info from everything and
the brochure and really make it good").

THE HERO. The page opened on a drawing of a glass on navy. It opens on the long table by candlelight now; the words
are the ones the art hero carried.

THE GAPS. Two short sections stood side by side with a drawn medallion each and wide empty space around them. Each
sits beside a photograph now, taken from the set behind See all so no photograph shows twice.

THE BROCHURE. The page now says what the private events brochure says: the occasions, the table, a sample of the
days, what is included and what is possible on request, and where more guests can stay.
"""
import re

from _harness import Suite
import _harness
from test_page_pictures import _visible

m = _harness.m
PATH = "/events/private"


def run():
    s = Suite("The private events page")
    client = m.app.test_client()
    r = client.get(PATH)
    if not s.check(f"{PATH} loads", r.status_code == 200, detail=str(r.status_code)):
        return s
    page = r.get_data(as_text=True)

    s.section("It opens on a photograph")
    hero = re.search(r'<section class="g-hero[^"]*">(.*?)</section>', page, re.S)
    hero = hero.group(0) if hero else ""
    # Negative control: put back the g-hero--art section with illus('wine'), and these go red.
    s.check("the hero is a photograph", 'class="g-hero__img"' in hero, detail=hero[:160])
    s.check("not the drawn art on navy", "g-hero--art" not in hero)
    s.check("its words are the ones the art hero carried",
            all(w in hero for w in ("A Private Château for a Few Days", "reasons that need no name", "Ask about private hire")))
    body = re.search(r'<body class="([^"]*)"', page)
    s.check("the header lies over it", body is not None and "has-hero" in body.group(1))

    s.section("No medallions and gaps")
    intro = re.search(r'<section class="g-sec g-wed-intro">(.*?)</section>', page, re.S)
    intro = intro.group(1) if intro else ""
    s.check("the two sections each sit beside a photograph", intro.count('class="g-sp__main"') == 2,
            detail=str(intro.count('class="g-sp__main"')))
    s.check("the medallion pair is gone", "g-duo-sec" not in page)

    s.section("What the brochure says")
    occ = re.findall(r'<h3 class="g-occ__t">([^<]*)</h3>', page)
    s.check("five occasions", occ == ["Milestone birthdays", "Anniversaries", "Family gatherings", "Company retreats",
                                      "House parties"], detail=str(occ))
    days = re.findall(r'<h3 class="g-shootsteps__t">([^<]*)</h3>', page)
    s.check("a sample stay of four days", days == ["The Day of Arrival", "The Celebration", "The Day After", "Au Revoir"],
            detail=str(days))
    fine = re.search(r'<section class="g-sec g-wed-fine[^"]*">(.*?)</section>', page, re.S)
    fine = fine.group(1) if fine else ""
    s.check("what is included, and what is possible on request",
            "Included" in fine and "Also possible, on request" in fine)
    # Owner, 23 Nov: transport from Toulouse is possible, never included.
    inc = fine.split("Also possible, on request")[0]
    s.check("transport from Toulouse is not among what is included", "Toulouse" not in inc)
    s.check("the places close by are on the page", "If More of Your Party Would Like to Come" in page and "Maison Beaucoup" in page)
    # The brochure has prices nowhere (owner, 23 Nov: "dont like money in the brochure"); nor does this.
    s.check("no price on the page", not re.search(r"€\s?\d", re.sub(r"<script.*?</script>", "", page, flags=re.S)))

    s.section("Nothing twice")
    shown = _visible(page)
    for alt in ("The long table by candlelight", "A salon, the mountains through its windows",
                "A terrace at night, strung with lights", "Fish with herbs on a floral plate"):
        n = len(re.findall(r'<img\b[^>]*\salt="' + re.escape(alt), shown))
        s.check(f"{alt[:40]} drawn once", n == 1, detail=str(n))
    return s


if __name__ == "__main__":
    print(run().report())
