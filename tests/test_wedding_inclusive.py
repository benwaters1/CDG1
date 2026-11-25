"""Weddings and private events, 24 November 2026 (the owner's brief for /restoration, sections 6 to 9).

6. EIGHTY INSIDE, NOT FORTY. The owner: "yes we can have 80 inside". The brochures
   already said it ("two large adjoining salons hold around forty guests each");
   the site said forty, on the weddings page four times and once on /events.
7. ALL INCLUSIVE, AND NO WEDDING PLANNER. The owner: "i dont think any weddings we
   have held they have bothered with wedding planners, we have just included our
   help with most things as part of the all inclusive arrangement"; "just focus on
   it being all inclusive"; "include spirits"; "put in bbq on the terrace"; "dont
   forget the FAQ". The Finer Details' Included column became All Inclusive, four
   groups; what is left is what the couple arrange and what can be added.
8. THE WEDDING QUOTE. "We are still dreaming of being at the Chateau with
   everyone." did not read as a testimonial; another line from the same letter,
   signed, with no names.
9. MOST IMPORTANT FIRST. What a couple decides on, in order, then the detail, the
   questions and the enquiry. The private page's What We Look After is All
   Inclusive, third.

What breaks quietly here, and is therefore checked:

  - A figure left behind. "Forty" was in a stat tile, a heading, a paragraph, a
    meta description and an answer in a closed <details>; any one of them left
    would contradict the rest. The only forty allowed is the brochure's own
    "around forty guests each", which is how eighty is made up.
  - A signature nobody can see. Every figure caption under a photograph is hidden
    site-wide (15 Oct, the owner), and the quote is a figure: it rendered its
    signature into the page and showed none. The stylesheet must let a pull
    quote's caption through.
  - A section moved and its anchor lost. The hero's "Ask about a date" goes to
    #enquire; it must still land.
  - A planner by another name. "Arranged by you or your planner", "your wedding
    planner's", "Can we bring our own photographer or wedding planner?": the page
    says planner once, in the question that answers no.
"""
import os
import re

from _harness import Suite, visible_text
import _harness

m = _harness.m
ROOT = _harness.ROOT

WEDDING_ORDER = [
    "Marry in a Monument",
    "The Whole Château, Not a Room In It",
    "All Inclusive",
    "A Wedding in a Château",
    "Weddings at Gudanes",
    "How Many, and Where",
    "A Church Ceremony, If You Wish",
    "At the Château’s Tables",
    "A Sample Celebration",
    "Where Your Wedding Guests Stay",
    "The Marquis Suite",
    "The Finer Details",
    "Questions Couples Ask",
    "Tell Us the Shape of It",
]
# 24 Nov 2026, later (BRIEF-private-retreat-and-images, 1): the page became a private retreat. All Inclusive is still
# third; A Few Days of Your Own went into Your Own Retreat; the occasions are "If There Is a Reason", lower down.
PRIVATE_ORDER = [
    "A Private Retreat",
    "Your Own Retreat",
    "All Inclusive",
    "Choose Your Days",
    "A Sample Private Retreat",
    "If There Is a Reason",
    "At the Château’s Tables",
    "The Finer Details",
    "If More of Your Party Would Like to Come",
    "A Gathering of Your Own",
    "What Are You Marking?",
]
GROUPS = {
    "Your Celebration": ["The whole château, its gardens and grounds, for the days of your wedding, from three nights",
                         "No other event under the roof",
                         "Our help planning every part of your wedding, before you arrive and on the day",
                         "Flowers throughout, from the gardens and the local market, created with you",
                         "A historic tour of the château",
                         # 24 Nov 2026 (BRIEF-private-retreat-and-images, 2): the speakers, as JBL.
                         "Two large JBL speakers, connected, indoors or out"],
    "Your Guests": ["Fifteen of you staying in the château’s bedrooms and suites",
                    "Rooms nearby for the rest of your guests, arranged by us",
                    "Refreshments and flowers waiting in their rooms",
                    "A shuttle between their rooms and the château, every day of your stay"],
    "The Table": ["Every meal at the château for your whole party, every day of your wedding",
                  "Menus created with our gourmet chefs",
                  "Local, seasonal produce",
                  "A welcome reception, long lunches and candlelit dinners",
                  "A barbecue on the terrace",
                  "Your wedding dinner and wedding cake"],
    "The Bar": ["Local Ariégeois wine",
                "Crémant, from the welcome reception to the last toast",
                "Spirits at the bar, open to you and your guests throughout your stay",
                "Choices without alcohol at every meal",
                "The tea and coffee salon"],
}
QUOTE = ("I’ve found a lot of delight watching virtually as subsequent guests enjoyed the chateau, knowing how magical "
         "the experience is to be there with you all.")
OLD_QUOTE = "We are still dreaming of being at the Chateau with everyone."
SIGNED = "Married at the château, 2024"
ALLOWED_FORTY = "around forty guests each"


def headings(page):
    """The page's own headings, top to bottom: the hero's <h1>, then each section's <h2 class="g-place">."""
    main = re.search(r"<main\b.*?</main>", page, re.S)
    main = main.group(0) if main else page
    out = []
    for tag in re.findall(r'<h1\b[^>]*>.*?</h1>|<h2\b[^>]*class="g-place"[^>]*>.*?</h2>', main, re.S):
        out.append(" ".join(visible_text(tag).split()))
    return out


def section(page, cls):
    hit = re.search(r'<section class="[^"]*\b%s\b[^"]*"[^>]*>(.*?)</section>' % re.escape(cls), page, re.S)
    return hit.group(1) if hit else ""


def groups_of(incl):
    """{heading: [items]} for the All Inclusive grid, and the photographs over them."""
    out, photos = {}, []
    for block in re.findall(r'<div class="g-wed-incl__g">(.*?)</ul>', incl, re.S):
        head = re.search(r'<h3 class="g-wed-incl__h">(.*?)</h3>', block, re.S)
        items = [" ".join(visible_text(li).split()) for li in re.findall(r"<li>(.*?)</li>", block, re.S)]
        out[visible_text(head.group(1)) if head else "?"] = items
        photos.append(bool(re.search(r"<img\b", block)))
    return out, photos


def fortys(text):
    """Every 'forty' in the words, with what stands around it, less the one the brochure says."""
    return [text[max(0, x.start() - 30):x.end() + 30] for x in re.finditer(r"(?i)\bforty\b", text)
            if ALLOWED_FORTY not in text[max(0, x.start() - 10):x.end() + 20]]


def meta(page, name):
    hit = re.search(r'<meta\s+name="%s"\s+content="([^"]*)"' % name, page)
    return hit.group(1) if hit else ""


def run():
    s = Suite("Weddings and private events: eighty inside, all inclusive, most important first")
    client = m.app.test_client()
    wed_r = client.get("/events/weddings")
    priv_r = client.get("/events/private")
    ev_r = client.get("/events")
    for path, r in (("/events/weddings", wed_r), ("/events/private", priv_r), ("/events", ev_r)):
        s.check(f"{path} loads", r.status_code == 200, detail=str(r.status_code))
    wed = wed_r.get_data(as_text=True)
    priv = priv_r.get_data(as_text=True)
    ev = ev_r.get_data(as_text=True)
    wed_words = visible_text(wed)

    s.section("9. The weddings page, most important first")
    got = headings(wed)
    s.check("the sections in the brief's order", got == WEDDING_ORDER,
            detail=" | ".join(got) if got != WEDDING_ORDER else "")
    # Negative control: the same reading of a page with the gallery back near the bottom is not the order.
    late = WEDDING_ORDER[:4] + WEDDING_ORDER[5:12] + [WEDDING_ORDER[4]] + WEDDING_ORDER[12:]
    s.check("and a page with the gallery near the bottom would not pass", late != WEDDING_ORDER)
    s.check("the church directly under How Many, and Where",
            got.index("A Church Ceremony, If You Wish") == got.index("How Many, and Where") + 1 if
            "A Church Ceremony, If You Wish" in got and "How Many, and Where" in got else False)
    s.check("What We Look After is gone from the weddings page", "What We Look After" not in wed_words)
    s.check("the hero's Ask about a date still lands", 'href="#enquire"' in wed and 'id="enquire"' in wed)

    s.section("9. The private page: All Inclusive third")
    got_p = headings(priv)
    s.check("the sections in the brief's order", got_p == PRIVATE_ORDER,
            detail=" | ".join(got_p) if got_p != PRIVATE_ORDER else "")
    s.check("All Inclusive is the third", len(got_p) > 2 and got_p[2] == "All Inclusive",
            detail=got_p[2] if len(got_p) > 2 else "")
    s.check("What We Look After is not there by its old name", "What We Look After" not in visible_text(priv))
    # 24 Nov 2026, later: what it includes is the retreats' own list now (test_private_retreat holds it).
    s.check("it says what a retreat includes", "And much more: the château as it was, full of life" in visible_text(priv))
    s.check("the hero's Ask about your dates still lands", 'href="#enquire"' in priv and 'id="enquire"' in priv)

    s.section("7A. All Inclusive, in four groups")
    incl = section(wed, "g-wed-incl")
    s.check("the section is there", bool(incl))
    s.check("for every day of the wedding", "All inclusive, for every day of your wedding." in visible_text(incl))
    groups, photos = groups_of(incl)
    s.check("four groups, in the brochure's order", list(groups) == list(GROUPS), detail=str(list(groups)))
    for name, items in GROUPS.items():
        s.check(f"{name}: one thing to a line, as the brochure has it", groups.get(name) == items,
                detail=str(groups.get(name)))
    s.check("spirits in The Bar", any("Spirits" in x for x in groups.get("The Bar", [])))
    s.check("a barbecue on the terrace in The Table", any("barbecue on the terrace" in x for x in groups.get("The Table", [])))
    s.check("a photograph over each group, so the four line up", photos == [True] * 4, detail=str(photos))

    s.section("7B. The Finer Details: arranged by you, and on request")
    fine = section(wed, "g-wed-fine")
    cols = dict((visible_text(h), visible_text(body)) for h, body in
                re.findall(r'<h3 class="g-wed-fine__h">(.*?)</h3><ul>(.*?)</ul>', fine, re.S))
    s.check("two columns, named as the brief names them", list(cols) == ["Arranged by you", "Also possible, on request"],
            detail=str(list(cols)))
    s.check("the marquee is on request, with our help", "A marquee in the grounds for a larger wedding, which we help "
            "you arrange" in cols.get("Also possible, on request", ""))
    s.check("and not among what the couple arrange", "marquee" not in cols.get("Arranged by you", "").lower())
    s.check("a longer celebration, not further nights",
            "A longer celebration: more days at the château, before or after your wedding" in fine
            and "Further nights" not in wed_words)
    s.check("the old Included column is gone", ">Included<" not in fine)

    s.section("7. No wedding planner, all inclusive")
    planners = [wed_words[max(0, x.start() - 40):x.end() + 10] for x in re.finditer(r"(?i)planner", wed_words)]
    s.check("planner said once, in the question that answers it",
            len(planners) == 1 and "Do we need a wedding planner?" in wed_words, detail=str(planners))
    s.check("its answer: no", "No. We help you plan every part of your wedding." in wed_words)
    s.check("What is included? is asked", "What is included?" in wed_words
            and "and spirits at the bar." in wed_words)
    s.check("the catering question went into it", "Do you do the catering?" not in wed_words)
    table = section(wed, "g-wed-table")
    for words in ("a barbecue on the terrace", "with our gourmet chefs", "Local Ariégeois wine and crémant",
                  "the bar, with its spirits, is open"):
        s.check(f"At the Château’s Tables: {words}", words in visible_text(table))

    s.section("6. Eighty inside")
    s.check("the figures under the hero: Eighty", re.search(r">\s*Eighty\s*<", wed) is not None)
    s.check("How Many, and Where: up to eighty", "Up to eighty" in wed_words
            and "in the two adjoining salons and the dining room, for up to eighty guests" in wed_words)
    s.check("the questions: eighty, two salons of around forty each",
            "Indoors, up to eighty: two adjoining salons hold around forty guests each" in wed_words)
    s.check("what search shows says eighty", "up to eighty guests inside" in meta(wed, "description"),
            detail=meta(wed, "description"))
    fr_page = m.app.test_client().get("/events/weddings?lang=fr").get_data(as_text=True)
    s.check("the French for it too", "quatre-vingts convives" in meta(fr_page, "description")
            and "quarante" not in meta(fr_page, "description"), detail=meta(fr_page, "description"))
    s.check("/events: up to eighty", "Up to eighty guests inside the château" in visible_text(ev))
    left = {p: fortys(visible_text(page) + " " + meta(page, "description"))
            for p, page in (("/events/weddings", wed), ("/events/private", priv), ("/events", ev))}
    s.check("no forty left on any of the three, but the brochure's forty each",
            not any(left.values()), detail=str({k: v for k, v in left.items() if v}))
    s.check("and the check sees one", fortys("Up to forty guests inside the château.") != [])
    s.check("while it lets the brochure's line pass", fortys("two salons hold around forty guests each, for") == [])

    s.section("8. The quote, signed")
    s.check("the old line is gone", OLD_QUOTE not in wed_words)
    pull = re.search(r'<figure class="g-pull g-pull--alone">(.*?)</figure>', wed, re.S)
    pull = pull.group(1) if pull else ""
    s.check("the couple's other line, word for word", QUOTE in visible_text(pull))
    s.check("signed under it", f"<figcaption><cite>{SIGNED}</cite></figcaption>" in pull)
    css = open(os.path.join(ROOT, "static", "gudanes-pictures.css"), encoding="utf-8").read()
    hide = open(os.path.join(ROOT, "static", "gudanes.css"), encoding="utf-8").read()
    # Every caption under a photograph is hidden (gudanes.css); the pull quote's caption is let through, by a rule
    # that names the figure's class and so outranks it, in the stylesheet read after it.
    s.check("captions are hidden site-wide, so the signature needs letting through",
            ".g main figure figcaption{ display: none !important; }" in hide)
    s.check("and the pull quote's is let through",
            re.search(r"\.g main figure\.g-pull figcaption\{[^}]*display:\s*block !important", css) is not None)
    s.check("no names", not re.search(r"\b(Rebecca|Ken)\b", wed_words))
    return s


if __name__ == "__main__":
    print(run().report())
