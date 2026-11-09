"""The rest of the public pages, laid out by the image layout brief (sections 4.3 to 4.7).

The retreat pages and the home page have suites of their own. This one holds
every other page the brief converts, with the same three rules for each:

  - never more than four photographs together: no grid of photographs is left
    on the page, and a collage holds four at most
  - no photograph twice, judged by the file the house holds for it, so one
    picture under a Squarespace address and a house copy counts once
  - the components the brief gave that page are there

and, page by page, what the brief asked of it. The words are
test_copy_unchanged's.
"""
import re

from _harness import Suite
import _harness
from test_home_pictures import picture_key

m = _harness.m

# For each page: (what to look for, how many at least), in the brief's words.
PAGES = {
    "/workshops": [
        ("each retreat's photograph under an arched top", r'class="g-wscard__arch"', 1),
        ("a day at Gudanes, down a timeline, swapping sides", r'class="g-day g-day--alt"', 1),
        ("The Table: a collage of four", r'class="g-mos g-mos--lead g-mos--4 ', 1),
        ("and a slider of the rest", r'aria-label="The Table"', 1),
        ("The Art of the Find: the vide-grenier framed", r'class="g-cadre g-cadre--rect[^"]*">\s*<div class="g-cadre__frame"><div class="g-cadre__mount"><img src="[^"]*vide-grenier', 1),
        ("and three finds in an album", r'class="g-album g-album--3 ', 1),
        ("The Evenings: a film strip", r'aria-label="The evenings"', 1),
        ("Be Part of It: the words beside the restorer", r'class="g-sp g-sp--flip g-sp--tuck', 1),
        ("The Valley: a slider", r'aria-label="The Valley"', 1),
        ("its Commons photographs credited", r'Traumrune.*Jeunamateur', 1),
        ("Why So Many Return: an album", r'class="g-album g-album--2 ', 1),
        ("the band of four: a film strip", r'aria-label="At the table"', 1),
        ("A Few Days at Gudanes: a slider, and the whole set behind See all", r'See all 23 photos', 1),
    ],
    "/book": [
        ("the rooms: each card its words beside its photographs, sides alternating", r'class="g-cards g-cards--split"', 1),
        ("each room's photographs in a slider of five at most", r'class="g-car g-car--room"', 1),
        ("the band at the top: a film strip", r'aria-label="The house"', 1),
        ("Your Room, Breakfast and the Lounge: a collage of four", r'class="g-mos g-mos--lead g-mos--4 ', 1),
        ("and all nine behind See all", r'See all 9 photos', 1),
        ("the park: one drone photograph across the page", r'class="g-plein "[^>]*>\s*<div class="g-plein__f"><img src="[^"]*from-the-air-pool-garden', 1),
        ("and the rest of it in a slider", r'aria-label="A château park to explore"', 1),
    ],
    "/events": [
        ("three ways to take the château, each with an arched framed photograph", r'class="g-cadre g-cadre--arch[^"]*g-way__cadre"', 3),
        # (the château at night has a house copy, so its address on the page is the copy's, not IMG_4368)
        ("the band of four: one photograph across the page, the château at night",
         r'class="g-plein "[^>]*>\s*<div class="g-plein__f"><img src="[^"]*" alt="The château lit at night', 1),
    ],
    "/events/weddings": [
        ("the Marquis Suite: its words beside its photographs", r'<div class="g-sp g-sp--tuck', 1),
        ("the church: its words beside the ceremony", r'<div class="g-sp g-sp--flip g-sp--tuck', 1),
        ("Weddings at Gudanes: a collage of four", r'class="g-mos g-mos--lead g-mos--4 ', 1),
        ("a slider of ten", r'aria-label="Weddings at Gudanes"', 1),
        ("and all forty-five behind See all", r'See all 45 photos', 1),
    ],
    "/events/private": [
        ("A Gathering of Your Own: a collage, a slider, and the whole set behind See all",
         r'class="g-mos g-mos--lead g-mos--4 .*aria-label="A gathering of your own".*See all 18 photos', 1),
    ],
    "/events/photoshoots": [
        ("an uneven collage", r'class="g-mos g-mos--tall ', 1),
        ("two rooms across the page, one restored and one as it was found", r'class="g-plein "', 2),
        ("a room as found among them", r'mountains-through-the-roof', 1),
        ("and all thirteen behind See all", r'See all 13 photos', 1),
    ],
    "/restoration": [
        ("As We Found It: the fifteen as a contact sheet", r'class="g-bande g-bande--contact', 1),
        ("Restoring the Chateau: the long list in two halves, each beside a photograph", r'class="g-sp[^"]*">\s*<div class="g-sp__text">\s*<ul class="g-how">', 2),
        ("The Work, Room by Room: a slider", r'aria-label="The work, room by room"', 1),
        ("The Wallpaper: framed, and in a museum mount", r'<div class="g-pair"><figure class="g-cadre g-cadre--rect.*?<figure class="g-pp ', 1),
        ("Then and Now keeps its three sliders", r'data-slider', 3),
    ],
    "/history": [
        ("the early postcard, as a postcard", r'class="g-cp g-rv g-hist__fig"', 1),
        ("the early photograph, framed", r'class="g-cadre g-cadre--rect g-rv g-hist__fig"', 1),
        ("Revolution and the Ironmasters: a photograph beside the long section", r'class="g-sp g-sp--flip', 1),
        ("the chateau today across the page", r'class="g-plein "', 1),
    ],
    "/story": [
        ("The People Who Keep It: three in an album", r'class="g-album g-album--3 ', 1),
        ("and the rest in a slider", r'aria-label="The people who keep it"', 1),
    ],
    "/facilities": [
        ("The Grounds: a collage of four", r'class="g-mos g-mos--lead g-mos--4 ', 1),
        ("and the rest in a slider", r'aria-label="The grounds"', 1),
        ("Winter at Gudanes: the chateau in snow across the page", r'class="g-plein "', 1),
        ("and an album", r'class="g-album g-album--3 ', 1),
    ],
    "/whats-on": [
        ("each place section with a photograph beside it", r'class="g-sp[^"]*">\s*<div class="g-sp__text">', 3),
        ("Skiing Nearby: a slider", r'aria-label="Skiing nearby"', 1),
        ("its Commons credits where they can be seen", r'<p class="g-car__credits"><span class="g-credit">', 1),
        ("the hot springs and the cave credited", r'Jeunamateur.*Traumrune', 1),
    ],
    "/press": [
        ("The Pages Themselves: a fanned album", r'class="g-album g-album--3 g-rv g-album--fan"', 1),
        ("and all six behind See all", r'See all 6 photos', 1),
    ],
    "/contact": [
        ("the band of four: one photograph across the page, down the drive", r'class="g-plein "[^>]*>\s*<div class="g-plein__f"><img src="[^"]*down-the-drive', 1),
    ],
    "/gallery": [
        ("the gallery as a masonry of mixed heights", r'g-gal--masonry', 1),
    ],
}


# Every room's own page, besides the pages above (added in run(), one per room on sale).
ROOM_PAGE = [
    ("one large photograph at a time, arrows and a count", r'class="g-roomgal-car"><section class="g-car', 1),
    ("and a strip of small ones under it, one for each", r'class="g-car__thumb"', 2),
    ("a bathroom photograph by the bathroom line", r'<dt>Your bathroom</dt><dd><figure class="g-med', 1),
    ("Bruce by his line", r'<dt>Animals</dt><dd><figure class="g-med', 1),
    ("This Room Was a Ruin: the bedroom before, in a museum mount", r'class="g-pp g-rv g-ruin__fig"', 1),
]


def _visible(page):
    page = re.sub(r"<div hidden>.*?</div>", "", page, flags=re.S)
    # A gallery's strip of small photographs is the way round it, not more photographs.
    page = re.sub(r'<div class="g-car__thumbs".*?</div>', "", page, flags=re.S)
    page = re.sub(r'<dialog class="g-lb".*?</dialog>', "", page, flags=re.S)
    return page


def run():
    s = Suite("Pages in pictures")
    client = m.app.test_client()
    pages = dict(PAGES)
    conn = _harness.db()
    for row in conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY sort_order, name").fetchall():
        pages["/book/%d" % row["id"]] = ROOM_PAGE
    conn.close()
    for path, wants in pages.items():
        s.section(path)
        r = client.get(path)
        if not s.check(f"{path} loads", r.status_code == 200, detail=str(r.status_code)):
            continue
        page = r.get_data(as_text=True)
        shown = _visible(page)
        for words, pattern, least in wants:
            s.check(words, len(re.findall(pattern, page, re.S)) >= least, detail=pattern)
        grids = re.findall(r'<ul class="g-befgrid g-gallery[^"]*">(.*?)</ul>', shown, re.S)
        s.check("no grid of photographs left", not grids, detail=f"{len(grids)} left")
        s.check("no band of four left (a film strip or one photograph across the page instead)",
                'class="g-band"' not in shown)
        mos = re.findall(r'<div class="g-mos .*?</div>\s*(?=<(?!figure))', shown, re.S)
        s.check("no collage of more than four", all(b.count("<img") <= 4 for b in mos))
        found = (re.search(r'\ssrc="([^"]*)"', t) for t in re.findall(r"<img\b[^>]*>", shown))
        srcs = [hit.group(1) for hit in found if hit]
        keys = [picture_key(u) for u in srcs if u and "Logo" not in u]
        twice = sorted({k for k in keys if keys.count(k) > 1})
        s.check("no photograph twice, by what it is", not twice, detail=", ".join(twice[:4]))
    return s


if __name__ == "__main__":
    print(run().report())
