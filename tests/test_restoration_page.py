"""The restoration page, 24 November 2026 (the owner's brief).

1. The painted beams and the frescoes, a section of their own, after Why the
   Walls Stay. The owner: "yes, they are 16th century hand painted". So the
   beams are sixteenth-century, and the frescoes stay Renaissance, as the page
   already had them. One large photograph and two smaller, captioned for what
   they show and no more.
3. The timeline brought up to 2026, with only what is on record: 2018, the
   first retreats; 2019, the bathrooms; 2026, the rooms open for a night. "Now"
   stays the last line, and a year can be added in one line.
5. Then and Now's three pairs keep their sliders.

What breaks quietly here, and is therefore checked:

  - The same sentence twice. The beams and the frescoes were the first sentence
    of What the Walls Gave Back. A new section beside an unchanged paragraph
    says it all twice, in two different words for the same century.
  - The same photograph twice. The medallion by the ladder was already on this
    page, under a Squarespace address, beside the second half of Restoring the
    Chateau. The brief named the house's copy of it for the new section, and
    the two addresses look like two photographs to anybody reading the source.
  - A year the record does not hold. The brief gave three, and said to add no
    other; a timeline is the first place a plausible date gets invented.
  - Two room counts on one page. "Six guest rooms" typed into the 2026 line
    would sit above a Now line that reads the rooms table, and the day they
    differ the page contradicts itself (test_house_facts_on_pages).
"""
import re

from _harness import Suite, visible_text
import _harness

m = _harness.m

# What the brief put on the record, and nothing else (owner, 24 Nov 2026).
YEARS = ["1741", "1994", "2013", "2014", "2016", "2018", "2019", "2020", "2026"]
MEDALLION = "img/retreats/fresco-uncovered-by-hand"
MEDALLION_ASSET = "1582799762807-88XQQSIAXWOEHIA2UVSO"   # the same photograph, on Squarespace


def _section(page, sec_id):
    found = re.search(r'<section[^>]*\bid="%s"[^>]*>(.*?)</section>' % sec_id, page, re.S)
    return found.group(1) if found else ""


def _img_srcs(html):
    return [s.group(1) for t in re.findall(r"<img\b[^>]*>", html)
            for s in [re.search(r'\ssrc="([^"]*)"', t)] if s]


def run():
    s = Suite("The restoration page: the paintings, the timeline")
    r = m.app.test_client().get("/restoration")
    if not s.check("/restoration loads", r.status_code == 200, detail=str(r.status_code)):
        return s
    page = r.get_data(as_text=True)
    words = visible_text(page)

    s.section("The Paintings, a section of their own")
    body = _section(page, "paintings")
    said = visible_text(body)
    s.check("there is a section for the paintings", bool(body))
    s.check("headed Beams Painted by Hand in the Sixteenth Century",
            '<h2 class="g-place">Beams Painted by Hand in the Sixteenth Century</h2>' in body)
    s.check("labelled in French and English: Les peintures, The paintings",
            '<p class="g-fr">Les peintures <span class="g-fr__en">The paintings</span></p>' in body)
    s.check("the beams are sixteenth century, as the owner said",
            "the beams above it were found painted by hand in the sixteenth century" in said, detail=said[:200])
    s.check("the frescoes stay Renaissance, as the page had them",
            "Under the wall plaster, Renaissance frescoes have come back to light" in said)
    s.check("and the château beside the bridge is still the one believed to be it",
            "what is believed to be the château as it once was, beside the bridge that can still be seen "
            "from the window" in said)
    at = {k: page.find(k) for k in ("Why the Walls Stay", 'id="paintings"', 'id="how"')}
    s.check("it comes after Why the Walls Stay and before Restoring the Château",
            -1 not in at.values() and at["Why the Walls Stay"] < at['id="paintings"'] < at['id="how"'],
            detail=str(at))

    srcs = _img_srcs(body)
    s.check("with at least two photographs", len(srcs) >= 2, detail=str(srcs))
    s.check("one large and two smaller", 'class="g-mos g-mos--lead g-mos--3' in body)
    for name in (MEDALLION, "img/retreats/guests-uncovering-fresco", "img/found/beams-and-panels"):
        s.check(f"{name.split('/')[-1]} is among them", any(name in x for x in srcs), detail=str(srcs))
    caps = [visible_text(c) for c in re.findall(r"<figcaption[^>]*>(.*?)</figcaption>", body, re.S)]
    s.check("captioned for what they show, and no caption claims a century",
            len(caps) == 3 and not any(w in c.lower() for c in caps
                                       for w in ("century", "renaissance", "16th", "guest")),
            detail=str(caps))

    s.section("Said once, shown once")
    s.check("the beams and the frescoes are no longer in What the Walls Gave Back",
            "hand-painted Renaissance beams" not in words
            and words.count("beside the bridge that can still be seen from the window") == 1,
            detail=str(words.count("beside the bridge")))
    s.check("which keeps the rest of what the walls gave back",
            "What the Walls Gave Back Wax seals from the French Revolution are still on centuries-old walnut doors"
            in words)
    every = _img_srcs(page)
    medallion = [x for x in every if MEDALLION in x or MEDALLION_ASSET in x]
    s.check("the medallion by the ladder is on the page once, in the new section",
            len(medallion) == 1 and any(MEDALLION in x for x in srcs), detail=str(medallion))
    s.check("and the second half of Restoring the Château has a photograph of its own",
            len(re.findall(r'class="g-sp[^"]*">\s*<div class="g-sp__text">\s*<ul class="g-how">', page)) == 2
            and "img/retreats/restorer-painted-wall" in _section(page, "how"))

    s.section("The timeline, up to 2026")
    tl = re.search(r'<ul class="g-time">(.*?)</ul>', page, re.S)
    lines = re.findall(r"<li><b>([^<]+)</b><span>(.*?)</span></li>", tl.group(1) if tl else "", re.S)
    years = [y for y, _ in lines]
    said = {y: visible_text(t) for y, t in lines}
    s.check("2018, 2019 and 2026 are on it", all(y in years for y in ("2018", "2019", "2026")), detail=str(years))
    s.check("every year in order, and no year the record does not hold",
            years[:-1] == YEARS, detail=str(years))
    s.check("it still ends on Now", bool(years) and years[-1] == "Now", detail=str(years))
    s.check("2018: the first guests, the first retreats, the Seven Starry Nights among them",
            said.get("2018") == "The first guests: the château’s first retreats, the Seven Starry Nights among them.",
            detail=said.get("2018"))
    s.check("2019: the bathrooms, made to come away",
            said.get("2019") == "The guest bathrooms are built, made to come away.", detail=said.get("2019"))
    word = m.house_room_facts_cached()["count_word"]
    s.check(f"2026: the rooms open for a night, as many as the rooms table holds ({word})",
            said.get("2026") == f"The rooms open for a night: {word} guest rooms, room and breakfast, on your own dates.",
            detail=said.get("2026"))
    s.check("and the Now line gives the same number",
            f"with {word} guest rooms" in said.get("Now", ""), detail=said.get("Now"))
    s.check("1741 still ends on its link to the history",
            re.search(r'<li><b>1741</b><span>[^<]*Gabriel\. <a href="/history">Its history</a></span></li>',
                      tl.group(1) if tl else "") is not None)
    s.check("the retreats are named on it, not linked (owner: not now)",
            "<a" not in dict(lines).get("2018", "<a"))

    s.section("Then and Now, and the tables")
    s.check("the kitchen, the bedrooms and the vestibule still carry data-slider",
            page.count('<div class="g-tn__pair" data-slider>') == 3)
    tables = len(re.findall(r"<table\b", page))
    wrapped = len(re.findall(r'<div class="table-wrap">\s*<table\b', page))
    s.check("no table outside a table-wrap", tables == wrapped, detail=f"{tables} tables, {wrapped} wrapped")
    closer = page[page.find('class="g-sec g-sec--dark g-closer"'):]
    closer = closer[:closer.find("</section>")]
    with m.app.test_request_context("/"):
        was = [m.url_for("book_rooms"), m.url_for("workshops_public")]
    s.check("and the closing section is as it was (brief, 2: no link to the retreats yet)",
            "Stay While It Happens" in closer and re.findall(r'href="([^"]+)"', closer) == was,
            detail=str(re.findall(r'href="([^"]+)"', closer)))
    return s


if __name__ == "__main__":
    print(run().report())
