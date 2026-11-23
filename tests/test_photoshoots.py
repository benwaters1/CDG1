"""/events/photoshoots, as redone on 23 Nov 2026 (owner: "then work on the photoshoot page improving it").

THE PAGE. A page selling the house to photographers opened on a drawing of a
camera on navy, and its two sections stood side by side with a medallion each
and no photograph: the one page where the photographs are the point showed
none until a third of the way down. It now opens on a shoot (the editorial in
the salon, which has no other home on the site), under a strip of the figures
a location scout asks first, with each section beside a room it describes,
and How a Shoot Works sets out the four steps from first email to shoot day.
Every word of the steps is something the page already said.

THE ENQUIRY. "Tell us about the shoot" sent the reader to /events, where the
form asked again what kind of event it was. It now carries ?type=photoshoot
and the form opens with Photography or film chosen. Any other type, or none,
leaves the choice to the reader.

THE CURRENCY CONTROL. Found on the way: on a page with breadcrumbs the header
stays cream at the top and the logo and menu stay navy, but the currency
turned white with the photograph rule, white on cream, on /events and every
page like it. It stays navy there now.

The negative controls are in the comments beside the checks, run by hand.
"""
import os
import re

from _harness import Suite
import _harness
from test_page_pictures import _visible

m = _harness.m
PATH = "/events/photoshoots"


def _hero(page):
    hit = re.search(r'<section class="g-hero[^"]*">(.*?)</section>', page, re.S)
    return hit.group(0) if hit else ""


def _select(page):
    hit = re.search(r'<select id="event_type".*?</select>', page, re.S)
    return hit.group(0) if hit else ""


def run():
    s = Suite("The photoshoot page")
    client = m.app.test_client()
    r = client.get(PATH)
    if not s.check(f"{PATH} loads", r.status_code == 200, detail=str(r.status_code)):
        return s
    page = r.get_data(as_text=True)

    s.section("It opens on a shoot")
    hero = _hero(page)
    # Negative control: put back the g-hero--art section with illus('camera'), and these go red.
    s.check("the hero is a photograph", 'class="g-hero__img"' in hero, detail=hero[:160])
    # By its words, not its address: the house's own copy of it replaces the address at the response.
    s.check("of the editorial in the salon", 'alt="An editorial shoot in the salon' in hero)
    s.check("not the drawn art on navy", "g-hero--art" not in hero)
    s.check("its words are the ones the art hero carried",
            all(w in hero for w in ("Every Room, Every State", "Bare plaster, gilded panelling, old stone",
                                    "Ask about a shoot")))
    s.check("with a second way in, to the recce", 'href="#recce"' in hero and 'id="recce"' in page)
    body = re.search(r'<body class="([^"]*)"', page)
    s.check("the header lies over it, as on /events", body is not None and "has-hero" in body.group(1),
            detail=body.group(1) if body else "no body class")

    s.section("The figures, and the rooms beside their words")
    figures = re.findall(r'class="g-strip__n">([^<]*)<', page)
    s.check("a strip of four figures", figures == ["About 55", "Class I", "One", "On site"], detail=str(figures))
    rooms = re.search(r'<section class="g-sec g-shoot-rooms">(.*?)</section>', page, re.S)
    rooms = rooms.group(1) if rooms else ""
    s.check("each section sits beside a photograph", rooms.count('class="g-sp__main"') == 2,
            detail=str(rooms.count('class="g-sp__main"')))
    for words in ("About fifty-five rooms, in every state between ruin and restored. The walls stay as they are.",
                  "Very few locations look like this, because very few are allowed to stay this way."):
        s.check(f"and keeps its words: {words[:40]}...", words in rooms)
    s.check("the medallion pair is gone", "g-duo-sec" not in page)

    s.section("How a Shoot Works")
    how = re.search(r'<ol class="g-shootsteps">(.*?)</ol>', page, re.S)
    how = how.group(1) if how else ""
    steps = re.findall(r'<h3 class="g-shootsteps__t">([^<]*)</h3>', how)
    s.check("four steps, in order", steps == ["Tell us", "Come and see", "Agreed in advance", "The house is yours"],
            detail=str(steps))
    numerals = re.findall(r'<b class="g-shootsteps__n" aria-hidden="true">([IV]+)</b>', how)
    # A <span> numeral is set to 15px by the reading floor (main span:not(...)); a <b> is not.
    s.check("numbered I to IV, in a <b> the reading floor leaves alone", numerals == ["I", "II", "III", "IV"],
            detail=str(numerals))
    s.check("every step something the page already says",
            all(f in page for f in ("every shoot is agreed with us", "Only one event runs at the château at once",
                                    "by appointment")))

    s.section("Nothing twice, nothing dead")
    # test_page_pictures holds the whole page to no photograph twice; these four are the ones that moved. By their
    # words, as above; See all's hidden set and the lightbox are not on the page until asked for.
    shown = _visible(page)
    for alt, what in (("An editorial shoot in the salon", "the editorial in the salon"),
                      ("A salon, the chandelier lit", "the chandelier lit"),
                      ("A tall window, the mountains beyond", "the tall window"),
                      ("A red dress on the staircase", "the red dress on the staircase")):
        n = len(re.findall(r'<img\b[^>]*\salt="' + re.escape(alt), shown))
        s.check(f"{what} drawn once", n == 1, detail=str(n))
    # The light-through-the-day script outlived its section (cut 1 Oct) and looked for it on every load.
    s.check("the script for a section cut on 1 Oct is gone", "data-light" not in page)

    s.section("The enquiry knows what it is about")
    s.check("the button carries the kind of event",
            re.search(r'href="/events\?type=photoshoot#enquire"[^>]*>Tell us about the shoot<', page) is not None)
    chosen = _select(client.get("/events?type=photoshoot").get_data(as_text=True))
    s.check("and /events opens with Photography or film chosen", '<option value="photoshoot" selected>' in chosen,
            detail=chosen[:200])
    # Negative control: mark every option selected, and the two below go red.
    s.check("with no type, nothing is chosen for the reader",
            " selected>" not in _select(client.get("/events").get_data(as_text=True)))
    s.check("nor with a type it does not know",
            " selected>" not in _select(client.get("/events?type=nonsense").get_data(as_text=True)))

    s.section("The currency control under breadcrumbs")
    root = m.app.root_path
    with open(os.path.join(root, "static", "gudanes-pictures.css"), encoding="utf-8") as fh:
        pics = fh.read()
    white = pics.find("body.has-hero .g-top:not(.is-solid) .g-curtop__s{ color: #fff; }")
    navy = pics.find(".g.has-hero.at-top:has(.g-crumbs) .g-top .g-curtop__s{ color: var(--blue-deep")
    # (0,6,0) against the white rule's (0,4,1), and after it as well. Negative control: delete the navy rule.
    s.check("stays navy where the logo and menu do, after the rule that turns it white",
            white != -1 and navy > white, detail=f"white at {white}, navy at {navy}")
    return s


if __name__ == "__main__":
    print(run().report())
