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
}


def _visible(page):
    page = re.sub(r"<div hidden>.*?</div>", "", page, flags=re.S)
    page = re.sub(r'<dialog class="g-lb".*?</dialog>', "", page, flags=re.S)
    return page


def run():
    s = Suite("Pages in pictures")
    client = m.app.test_client()
    for path, wants in PAGES.items():
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
