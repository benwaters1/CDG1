"""A drawing beside every place on /whats-on (owner, 23 Nov 2026).

"i want illustrations and stuff on here and around, all the plain text is not
good." draw_the_nearby_vignettes() in app.py puts a drawing from
templates/_nearby_art.html at the head of each entry on /whats-on, at the
response, chosen from the entry's heading (NEARBY_ART). Done there because
whats_on.html arrives whole in the design handovers.

What is held:
  - every entry on the page has a drawing, and today's entries each have their
    OWN drawing, not the fallback mountains: a heading reworded so that its word
    no longer matches would quietly lose its picture otherwise;
  - the ones the owner's screenshot showed get the right one (the river a kayak,
    canyoning a gorge, fly fishing a trout, the caves a cave);
  - no two entries side by side in one section share a drawing;
  - every kind NEARBY_ART names draws something, so a typo is not a blank circle;
  - the drawings are decorative (aria-hidden, no <title>), add no words, and
    keep the #skiing id /book links to;
  - the other pages that use the same list (/contact, /book) are left alone;
  - the page carries the draw-in script once.
A negative control breaks the choice in place and checks the run would see it.
"""
import re

from _harness import Suite
import _harness

m = _harness.m

ITEM = re.compile(r'<div class="g-key__i[^"]*"[^>]*>')
DRAWN = re.compile(r'<div class="g-key__i g-key__i--drawn" data-art="([a-z]+)"[^>]*>\s*'
                   r'<span class="g-key__art" aria-hidden="true">(<svg.*?</svg>)</span>\s*<p class="g-key__k">(.*?)</p>', re.S)

# The four in the owner's screenshot, and a few that are easy to get wrong
# because a more general word is also in the heading.
EXPECT = {
    # Site review 25 Nov: "On the river" and "Canyoning · Tarascon" were the same two things said again under
    # Outdoors; they are said once now, there.
    "Rafting, canoe": "kayak",
    "Canyoning · Auzat": "canyon",
    "Fly fishing": "fish",
    "Caves and prehistory": "cave",
    "Les Bains du Couloubret": "thermal",
    "A thermal spa half-day": "spa",
    "Caldea · Andorra": "caldea",
    "Andorra · about an hour": "road",
    "Tarascon-sur-Ari": "cafe",
    "Ax-les-Thermes · about": "spring",
    "Mountain biking": "bike",
    "Grotte de Niaux": "bison",
    "Mas d": "arch",
}


def _sections(body):
    return re.split(r'<section class="g-sec', body)


def run():
    s = Suite("A drawing beside every place on /whats-on")
    anon = m.app.test_client()
    r = anon.get("/whats-on")
    body = r.get_data(as_text=True)
    s.check("/whats-on answers", r.status_code == 200, detail=str(r.status_code))

    s.section("Every entry has its own drawing")
    items = ITEM.findall(body)
    drawn = DRAWN.findall(body)
    s.check("there are entries to draw beside", len(items) >= 30, detail=str(len(items)))
    s.check("every entry on the page has a drawing", len(drawn) == len(items),
            detail="%d entries, %d drawn" % (len(items), len(drawn)))
    plain = [h for k, _svg, h in drawn if k == m.NEARBY_ART_FALLBACK]
    s.check("none of today's entries has fallen back to the mountains", not plain, detail="; ".join(plain))
    got = {h: k for k, _svg, h in drawn}
    for start, kind in EXPECT.items():
        found = [k for h, k in got.items() if h.startswith(start) or start in h[:len(start) + 2]]
        s.check("%s… is drawn as %s" % (start, kind), found and found[0] == kind, detail=str(found))

    s.section("Neighbours differ, and every kind draws")
    for sec in _sections(body):
        kinds = re.findall(r'data-art="([a-z]+)"', sec)
        twins = [a for a, b in zip(kinds, kinds[1:]) if a == b]
        if twins:
            s.check("no two entries side by side share a drawing", False, detail=", ".join(twins))
            break
    else:
        s.check("no two entries side by side share a drawing", True)
    with m.app.app_context():
        empty = [k for _rx, k in m.NEARBY_ART
                 if len(re.findall(r"<(path|circle|ellipse)\b", m.nearby_art_svg(k))) < 3]
    s.check("every kind NEARBY_ART names draws at least three strokes", not empty, detail=", ".join(empty))

    s.section("Decoration only")
    svgs = [svg for _k, svg, _h in drawn]
    s.check("no drawing carries words (no <title>, no <text>)",
            not any("<title" in v or "<text" in v for v in svgs))
    s.check("each medallion is aria-hidden", body.count('<span class="g-key__art" aria-hidden="true">') == len(drawn))
    s.check("the #skiing entry keeps its id (/book links to it)",
            re.search(r'<div class="g-key__i g-key__i--drawn" data-art="ski" id="skiing">', body) is not None)
    s.check("the draw-in script is on the page once", body.count("g-key__art\");if(!a.length") == 1)
    for path in ("/contact", "/book"):
        other = anon.get(path).get_data(as_text=True)
        s.check("%s's key list is left as it was" % path, "g-key__i--drawn" not in other and "g-key__art" not in other)

    s.section("Negative control")
    saved = m._NEARBY_ART_RX
    try:
        m._NEARBY_ART_RX = []        # NEGCTL: nothing matches
        broken = anon.get("/whats-on").get_data(as_text=True)
    finally:
        m._NEARBY_ART_RX = saved
    lost = [h for k, _svg, h in DRAWN.findall(broken) if k == m.NEARBY_ART_FALLBACK]
    s.check("with the choice broken, the run sees entries fall back", len(lost) == len(items), detail=str(len(lost)))
    return s
