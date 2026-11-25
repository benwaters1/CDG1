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
    # 24 Nov 2026 (BRIEF-private-retreat-and-images, 1): a private retreat (test_private_retreat).
    s.check("its words are a private retreat's",
            all(w in hero for w in ("A Private Retreat", "for your party alone", "Ask about your dates")))
    body = re.search(r'<body class="([^"]*)"', page)
    s.check("the header lies over it", body is not None and "has-hero" in body.group(1))

    s.section("No medallions and gaps")
    # 24 Nov 2026 (owner: the order, most important first): All Inclusive comes between the two now, so each is a
    # section of its own, with its one photograph (test_wedding_inclusive holds the order).
    intros = re.findall(r'<section class="g-sec g-wed-intro">(.*?)</section>', page, re.S)
    # 24 Nov 2026, later: one now. A Few Days of Your Own said what Your Own Retreat says, and went.
    s.check("the opening section sits beside a photograph", [i.count('class="g-sp__main"') for i in intros] == [1],
            detail=str([i.count('class="g-sp__main"') for i in intros]))
    s.check("the medallion pair is gone", "g-duo-sec" not in page)

    s.section("What the brochure says")
    occ = re.findall(r'<h3 class="g-occ__t">([^<]*)</h3>', page)
    s.check("five occasions", occ == ["Milestone birthdays", "Anniversaries", "Family gatherings", "Company retreats",
                                      "A party of friends"], detail=str(occ))
    days = re.findall(r'<h3 class="g-shootsteps__t">([^<]*)</h3>', page)
    s.check("a sample private retreat of four days, from the retreats' own",
            days == ["The Day of Arrival", "The Kitchen and the Market", "The Restoration and Foix", "Au Revoir"],
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
                "Fish with herbs on a floral plate", "Mirepoix under its old arcades",
                "Guests on the scaffold among the frescoes"):
        n = len(re.findall(r'<img\b[^>]*\salt="' + re.escape(alt), shown))
        s.check(f"{alt[:40]} drawn once", n == 1, detail=str(n))
    return s


if __name__ == "__main__":
    print(run().report())
