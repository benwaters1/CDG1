"""The private page is a private retreat; the speakers are JBL (24 Nov 2026).

BRIEF-private-retreat-and-images-24-nov, sections 1 and 2. The owner: "check the private stay page, it should be like a
private workshop". /events/private read as a party venue ("Smaller Than a Wedding, Longer Than a Dinner", "A Gathering
of Your Own"). It sells a retreat now, for one party: the retreats' days, chefs, meals, wine and outings, on their
own dates, with nobody else under the roof.

WHAT IS HELD

  - The hero says "A Private Retreat", and the party venue's words are gone.
  - All Inclusive is the retreats' own list, drawn from one partial (_retreat_included.html) by this page and by the
    retreat column on the home page: change a line in the partial and both pages say the new line. A second list written
    for this page would have drifted from the first.
  - Transfers from Toulouse "can be arranged" here; they are not in the list, which is the retreats' (whose fixed
    11am meeting a private party does not share).
  - Choose Your Days: the retreats' themes, each with a photograph of its own.
  - A Sample Private Retreat, from the retreats' days, with Au Revoir and its picnic bag; and the occasions, as "If
    There Is a Reason", below it.
  - No price figure on the page.
  - The enquiry opens the Contact form with "A private retreat" chosen, still stored as private_event, and an
    enquiry made before (its notes begin "Private event.") still reads as one.
  - "Bring your own group" on the retreat card, and the retreats page's Groups line, lead here; the menu says
    "Private Retreats", in French "Séjours privés" (not "retraites", which reads as pensions).
  - The weddings page says JBL, and no page says "connected speakers" without it.
"""
import re

import jinja2

from _harness import Suite, visible_text
import _harness

m = _harness.m
PATH = "/events/private"


def _headings(page):
    return [visible_text(h) for h in re.findall(r"<h[12]\b[^>]*>(.*?)</h[12]>", page, re.S)]


def _render_with_partial(client, paths, partial_source):
    """The pages drawn with _retreat_included.html replaced by `partial_source`, the real loader put back after."""
    env = m.app.jinja_env
    real = env.loader
    env.loader = jinja2.ChoiceLoader([jinja2.DictLoader({"_retreat_included.html": partial_source}), real])
    env.cache.clear()
    try:
        return {p: client.get(p).get_data(as_text=True) for p in paths}
    finally:
        env.loader = real
        env.cache.clear()


def run():
    s = Suite("The private page is a private retreat; the speakers are JBL")
    client = m.app.test_client()
    r = client.get(PATH)
    if not s.check(f"{PATH} loads", r.status_code == 200, detail=str(r.status_code)):
        return s
    page = r.get_data(as_text=True)
    words = visible_text(page)

    s.section("A retreat, not a party venue")
    hero = re.search(r'<section class="g-hero[^"]*">(.*?)</section>', page, re.S)
    hero = hero.group(1) if hero else ""
    s.check("the hero says A Private Retreat", re.search(r'<h1 class="g-hero__title">A Private Retreat</h1>', hero) is not None,
            detail=visible_text(hero)[:120])
    s.check("for your party alone, and nobody else under the roof",
            "for your party alone: your dates, your people, and nobody else under the roof" in visible_text(hero))
    s.check("its button asks about your dates, and lands on the enquiry",
            re.search(r'href="#enquire"[^>]*>Ask about your dates<', hero) is not None and 'id="enquire"' in page)
    for gone in ("Smaller Than a Wedding", "A Few Days of Your Own", "Whatever You Are Celebrating", "A Sample Stay",
                 "would rather spend its days learning something", "Private Hire"):
        s.check(f"no “{gone}”", gone not in words)
    heads = _headings(page)
    s.check("the first section says it is the retreats' programme, private",
            "Your Own Retreat" in heads and "The retreats’ programme, for your party alone" in words
            and "three to seven nights" in words and "up to fifteen of you" in words)

    s.section("What it includes is what a retreat includes, written once")
    incl = re.search(r'<section class="g-sec g-sec--sand g-wed-us g-priv-incl">(.*?)</section>', page, re.S)
    incl = incl.group(1) if incl else ""
    items = [visible_text(x) for x in re.findall(r"<li\b[^>]*>(.*?)</li>",
                                                 (re.search(r'<ul class="g-sow__list g-priv-incl__l">(.*?)</ul>', incl, re.S)
                                                  or re.match("()", "")).group(1), re.S)]
    home = client.get("/").get_data(as_text=True)
    col = re.search(r'<article class="g-sow__col g-sow__col--full[^"]*">.*?<ul class="g-sow__list">(.*?)</ul>', home, re.S)
    theirs = [visible_text(x) for x in re.findall(r"<li\b[^>]*>(.*?)</li>", col.group(1), re.S)] if col else []
    s.check("All Inclusive lists what the retreat column on the home page lists, line for line",
            items and items == theirs, detail=f"{items} / {theirs}")
    s.check("and ends open", items and items[-1].startswith("And much more"), detail=str(items[-1:]))
    src = open(m.os.path.join(_harness.ROOT, "templates", "_retreat_included.html"), encoding="utf-8").read()
    changed = src.replace("Foix and the caves", "Foix and the caves, and a line written for this check")
    s.check("the partial holds the line the check changes", changed != src)
    both = _render_with_partial(client, (PATH, "/"), changed)
    s.check("change a line in the partial, and the private page says the new line",
            "and a line written for this check" in visible_text(both[PATH]))
    s.check("and so does the retreat column on the home page",
            "and a line written for this check" in visible_text(both["/"]))
    s.check("and both say it once, from the one list",
            visible_text(both[PATH]).count("a line written for this check") == 1)
    after = client.get(PATH).get_data(as_text=True)
    s.check("and the real partial is back afterwards", "a line written for this check" not in visible_text(after))
    s.check("transfers from Toulouse can be arranged",
            "Transfers from Toulouse can be arranged" in visible_text(incl))
    s.check("and are not among what is included", not any("Toulouse" in i for i in items), detail=str(items))

    s.section("Choose Your Days, a sample, and the reason lower down")
    choose = re.search(r'<section class="g-sec g-pdays-sec"[^>]*>(.*?)</section>', page, re.S)
    choose = choose.group(1) if choose else ""
    names = [visible_text(x) for x in re.findall(r'<h3 class="g-pdays__fr"[^>]*>(.*?)</h3>', choose, re.S)]
    s.check("the retreats' five themes", names == ["En cuisine", "Chiner", "La restauration", "Noël", "L’été"],
            detail=str(names))
    s.check("each with a photograph of its own",
            len(re.findall(r'<li class="g-pdays__i">\s*<figure class="g-pdays__ph"><img\b', choose)) == 5
            and len(set(re.findall(r'<img\b[^>]*\salt="([^"]*)"', choose))) == 5)
    days = re.findall(r'<h3 class="g-shootsteps__t">([^<]*)</h3>', page)
    s.check("A Sample Private Retreat: the arrival, the kitchen and the market, the restoration and Foix, Au Revoir",
            "A Sample Private Retreat" in heads
            and days == ["The Day of Arrival", "The Kitchen and the Market", "The Restoration and Foix", "Au Revoir"],
            detail=str(days))
    s.check("the picnic bag stays on the last day", "A little picnic bag each for your onward travels" in words)
    s.check("If There Is a Reason comes after the sample",
            "If There Is a Reason" in heads and heads.index("If There Is a Reason") > heads.index("A Sample Private Retreat"),
            detail=" | ".join(heads))
    s.check("the tables, the finer details and the enquiry stay",
            all(h in heads for h in ("At the Château’s Tables", "The Finer Details", "What Are You Marking?")))
    s.check("no price on the page", not re.search(r"€\s?\d", re.sub(r"<script.*?</script>", "", page, flags=re.S)))

    s.section("The enquiry: a private retreat, stored as before")
    send = re.search(r'<section class="g-sec g-sec--sand" id="enquire">.*?href="([^"]*)"[^>]*>Send an enquiry<', page, re.S)
    target = m.html_unescape(send.group(1)) if send else ""
    s.check("Send an enquiry opens the Contact form with the topic chosen",
            target.startswith("/contact?topic=private_event") and target.endswith("#write-to-us"), detail=target)
    form = client.get(target.split("#")[0]).get_data(as_text=True)
    s.check("where the form has #write-to-us to land on, its own", form.count('id="write-to-us"') == 1)
    s.check("and opens with A private retreat chosen",
            re.search(r'<option value="private_event" selected>A private retreat</option>', form) is not None)
    plain = client.get("/contact").get_data(as_text=True)
    s.check("with no topic in the address, it still asks to choose one",
            re.search(r'<option value="" selected disabled>Choose one</option>', plain) is not None
            and not re.search(r'<option value="private_event" selected', plain))
    s.check("the house reads it as Private retreat", m.CONTACT_TOPICS.get("private_event") == "Private retreat")
    s.check("an enquiry made before, 'Private event. ...', still reads as its topic and then their words",
            m.enquiry_message_part("Private event. A birthday dinner in June.") == "A birthday dinner in June."
            and m.enquiry_message_part("Private retreat. A birthday dinner in June.") == "A birthday dinner in June.")

    s.section("The links in, and the menu")
    for path in ("/story", "/facilities"):
        other = client.get(path).get_data(as_text=True)
        found = re.findall(r'<a href="([^"]*)">Bring your own group</a>', other)
        s.check(f"{path}: Bring your own group leads to the private retreat", found and all(f == PATH for f in found),
                detail=str(found))
    ws = client.get("/workshops").get_data(as_text=True)
    s.check("the retreats page's Groups line leads here",
            re.search(r'Groups can take the whole château for <a href="/events/private">a private retreat</a>', ws) is not None)
    s.check("the menu says Private Retreats",
            re.search(r'<a href="/events/private"[^>]*>Private Retreats</a>', page) is not None)
    s.check("in French, Séjours privés", m.translations.TABLES["fr"].get("Private Retreats") == "Séjours privés")

    s.section("2. The speakers are JBL")
    wed = client.get("/events/weddings").get_data(as_text=True)
    s.check("the weddings page says JBL", "Two large JBL speakers, connected, indoors or out" in visible_text(wed))
    loose = [p for p in ("/", "/events", "/events/weddings", PATH, "/facilities")
             if re.search(r"connected speakers", visible_text(client.get(p).get_data(as_text=True)), re.I)]
    s.check("and no page says connected speakers without it", not loose, detail=str(loose))

    s.section("3. The photographs: the gallery's three sections each show some")
    gal = client.get("/gallery").get_data(as_text=True)

    def part(start, end_pattern):
        at = gal.find(start)
        if at < 0:
            return ""
        nxt = re.search(end_pattern, gal[at + len(start):])
        return gal[at:at + len(start) + (nxt.start() if nxt else len(gal))]

    story = part('id="restoration"', r"<h2\b")
    story_alts = re.findall(r'<img\b[^>]*\salt="([^"]*)"', story)
    s.check("The Restoration Story shows photographs", len(story_alts) >= 3, detail=str(len(story_alts)))
    s.check("none of them the drive or the gates, here or behind See all",
            not [a for a in story_alts if re.search(r"\bdrive\b|\bgates?\b", a, re.I)],
            detail=str([a for a in story_alts if re.search(r"\bdrive\b|\bgates?\b", a, re.I)]))
    s.check("it opens on the château as it was found: its first photograph is the ruin",
            story_alts and story_alts[0] == "The mountains, through a broken window under the roof", detail=str(story_alts[:2]))
    grounds = part('id="grounds"', r"<h2\b")
    s.check("the drive and the gates are with the grounds",
            "The drive under a storm sky" in grounds and "Through the gates" in grounds)
    archive = part('id="archive"', r"</section>")
    s.check("Rooms That No Longer Look Like This shows the Then and Now pairs",
            archive.count("data-slider") >= 3 and len(re.findall(r"<img\b", archive)) >= 6,
            detail=f"{archive.count('data-slider')} pairs, {len(re.findall(r'<img', archive))} photographs")
    resto = client.get("/restoration").get_data(as_text=True)
    s.check("the same pairs as /restoration, from the one partial",
            re.findall(r'<div class="g-tn__pair" data-slider>.*?<p class="g-tn__cap">([^<]*)</p>', archive, re.S)
            == re.findall(r'<div class="g-tn__pair" data-slider>.*?<p class="g-tn__cap">([^<]*)</p>', resto, re.S)
            != [])
    s.check("and each page carries the slider's script once",
            gal.count("if (window.__gThenNow)") == 1 and resto.count("if (window.__gThenNow)") == 1)
    work = part("The Work, As It Happens", r"</section>")
    before_buttons = work.split("instagram.com")[0]
    s.check("The Work, As It Happens shows six photographs before its buttons",
            len(re.findall(r"<img\b", before_buttons)) == 6, detail=str(len(re.findall(r"<img\b", before_buttons))))
    s.check("each described", all(len(a) > 12 for a in re.findall(r'<img\b[^>]*\salt="([^"]*)"', before_buttons)))

    s.section("3. The logo and the engraving have a size before they arrive")
    for path in ("/", "/events", "/book"):
        other = client.get(path).get_data(as_text=True)
        engr = re.search(r'<img\b[^>]*chateau_engraving\.png[^>]*>', other)
        s.check(f"{path}: the engraving at the foot", engr and 'width="691"' in engr.group(0) and 'height="396"' in engr.group(0),
                detail=engr.group(0)[:160] if engr else "none")
        marks = re.findall(r'<img class="g-close__mark"[^>]*>', other)
        s.check(f"{path}: the logo in the closing band", marks and all('width="62" height="62"' in t for t in marks),
                detail=str([t[:80] for t in marks]))
    return s


if __name__ == "__main__":
    print(run().report())
