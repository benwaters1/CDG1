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
import html
import json
import os
import re
import shutil
from datetime import timedelta

from _harness import Suite
import _harness
from test_home_pictures import picture_key

m = _harness.m

# The retreats /workshops knows by a word in their titles (WS_CARD_IMG and WS_PIC in workshops_public.html).
NAMED = ("Antique", "Noël", "Cooking", "Seven")
TAG = "ZZPP"
# Named for none of the four. The first has a photo uploaded for it and two dates; the rest have neither, and are
# as many as the page has spare pairs, so each should have a pair of its own.
UNNAMED = [TAG + " Painting Week", TAG + " Garden Week", TAG + " Music Week", TAG + " Walking Week",
           TAG + " Linen Week"]
OWN_PHOTO = "zpp_own_photo.jpg"

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
        ("each place section with a photograph beside it", r'class="g-sp[^"]*">\s*<div class="g-sp__text">', 4),
        # 9 Nov (owner): Andorra had none the house held; Sant Joan de Caselles, from Commons, credited under it.
        ("Andorra: Sant Joan de Caselles beside it, its photographer credited",
         r'id="andorra".*?retreats/sant-joan-de-caselles\.jpg.*?<p class="g-car__credits"><small class="g-credit">Photo <a [^>]*>Pierre Bona</a>, <a [^>]*>CC BY-SA 3\.0</a>.*?id="close-by"', 1),
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


def _src(block, cls):
    tag = re.search(r'<img\b[^>]*\bclass="%s"[^>]*>' % cls, block)
    hit = tag and re.search(r'\ssrc="([^"]*)"', tag.group(0))
    return html.unescape(hit.group(1)) if hit else ""


def _unnamed_retreats(s, client):
    """A fifth retreat, named for none of the four: its own photograph, or one shown nowhere else on the page.

    Each card and each row of dates picked its photograph by a word in the retreat's title, and anything else fell
    back to Cooking's two, so a retreat the owner adds showed Cooking's photographs a second time, and a photo
    uploaded for it (workshops.photo_filename) was never shown. The check above cannot see that: within a list a
    photograph counts once, so a second retreat on Cooking's photograph folded into Cooking's.
    """
    s.section("/workshops: a retreat named for none of the four")
    conn = _harness.db()
    real_dir = m.ROOM_PHOTO_DIR
    scratch = os.path.join(m.UPLOAD_DIR, "zpp_room_photos")
    hidden = []
    try:
        # The upload, where room_photo will look for it, never in the real room photographs.
        os.makedirs(scratch, exist_ok=True)
        m.ROOM_PHOTO_DIR = scratch
        from PIL import Image
        Image.new("RGB", (40, 30), (120, 90, 60)).save(os.path.join(scratch, OWN_PHOTO), "JPEG")
        with open(os.path.join(scratch, OWN_PHOTO), "rb") as f:
            own_bytes = f.read()

        # Any other retreat named for none of the four is the run's, not this check's (a whole run leaves some),
        # and would take spare pairs before these do. Off the page while this looks, back after.
        for row in conn.execute(f"SELECT id, title FROM workshops WHERE active = 1 AND {m.WORKSHOP_IS_PUBLIC}"):
            if not any(k in row["title"] for k in NAMED):
                hidden.append(row["id"])
        if hidden:
            conn.execute(f"UPDATE workshops SET active = 0 WHERE id IN ({','.join('?' * len(hidden))})", hidden)

        today = m.house_today()
        ids = []
        for i, title in enumerate(UNNAMED):
            wid = conn.execute(
                "INSERT INTO workshops (title, description, price_per_person, active, sort_order, photo_filename, "
                "created_at) VALUES (?, 'x', 1000, 1, ?, ?, ?)",
                (title, 9000 + i, OWN_PHOTO if i == 0 else None, _harness.datetime_now())).lastrowid
            ids.append(wid)
            for start in ([today + timedelta(days=90), today + timedelta(days=120)] if i == 0
                          else [today + timedelta(days=100 + i)]):
                conn.execute(
                    "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at) "
                    "VALUES (?, ?, ?, 15, ?, ?)",
                    (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), TAG, _harness.datetime_now()))
        conn.commit()

        r = client.get("/workshops")
        if not s.check("/workshops loads with them", r.status_code == 200, detail=str(r.status_code)):
            return
        page = r.get_data(as_text=True)
        shown = _visible(page)
        with m.app.test_request_context():
            own_src = m.url_for("room_photo", filename=OWN_PHOTO)

        cards = {}
        for block in re.findall(r'<article class="g-wscard">(.*?)</article>', shown, re.S):
            name = re.search(r'<h3 class="g-wscard__name">(.*?)</h3>', block, re.S)
            cards[html.unescape(name.group(1).strip()) if name else ""] = _src(block, "g-wscard__img")
        dates = {}
        for wid, block in re.findall(r'<article class="g-sit[^"]*" data-ws="(\d+)">(.*?)</article>', shown, re.S):
            dates.setdefault(int(wid), []).append(_src(block, "g-sit__img"))
        mine = dict(zip(UNNAMED, ids))

        own, rest = UNNAMED[0], UNNAMED[1:]
        s.check("its card shows the photo uploaded for it", cards.get(own) == own_src,
                detail=f"{cards.get(own)!r}, wanted {own_src!r}")
        s.check("and so does each of its dates", len(dates.get(mine[own], [])) == 2
                and all(d.split("?", 1)[0] == own_src for d in dates[mine[own]]), detail=repr(dates.get(mine[own])))
        served = client.get(own_src)
        s.check("which is the photo uploaded, at the address room photographs are served from",
                served.status_code == 200 and served.data == own_bytes, detail=str(served.status_code))

        spare = [(cards.get(t, ""), (dates.get(mine[t]) or [""])[0]) for t in rest]
        s.check("each one with no photo of its own shows a photograph on its card and on its dates",
                all(c and d for c, d in spare), detail=repr(spare))
        s.check("each a file the house holds, since a broken photograph shows nothing and says nothing",
                all(m._pic_file(x.split("?", 1)[0]) for pair in spare for x in pair if x),
                detail=", ".join(x for pair in spare for x in pair if x and not m._pic_file(x.split("?", 1)[0])))
        spare_keys = [picture_key(x) for pair in spare for x in pair]
        s.check(f"all {len(spare_keys)} of theirs different: a pair of its own for each, while there are pairs",
                len(set(spare_keys)) == len(spare_keys), detail=", ".join(sorted(spare_keys)))
        # Cooking's were the fallback; any of the four's would be the same fault.
        named = {picture_key(src) for t, src in cards.items() if any(k in t for k in NAMED)}
        for row in conn.execute("SELECT id, title FROM workshops WHERE active = 1"):
            if any(k in row["title"] for k in NAMED):
                named |= {picture_key(d) for d in dates.get(row["id"], [])}
        s.check("and none of them a named retreat's, Cooking's least of all", named and not named & set(spare_keys),
                detail=", ".join(sorted(named & set(spare_keys))) or "no named retreat on the page to compare")

        # The whole page. A retreat's dates rows are one photograph however many dates it has, and its uploaded photo
        # is one photograph on its card and its dates: that is the retreat, shown where it is listed. Anything else
        # counts every time: one retreat's photograph on another's card, a card's on the dates, either in the page.
        seen = set()
        for title, src in cards.items():
            key = picture_key(src)
            seen.add((("own", mine[own]) if src == own_src and title == own else ("card", title), key))
        for wid, srcs in dates.items():
            for src in srcs:
                key = picture_key(src)
                seen.add((("own", wid) if src.split("?", 1)[0] == own_src and wid == mine[own] else ("dates", wid), key))
        elsewhere = re.sub(r'<article class="g-(?:wscard|sit)\b.*?</article>', "", shown, flags=re.S)
        n = 0
        for tag in re.findall(r"<img\b[^>]*>", elsewhere):
            hit = re.search(r'\ssrc="([^"]*)"', tag)
            if not hit or not hit.group(1) or "Logo" in hit.group(1):
                continue
            n += 1
            seen.add((("page", n), picture_key(html.unescape(hit.group(1)))))
        keys = [k for _, k in seen]
        twice = sorted({k for k in keys if keys.count(k) > 1})
        s.check("no photograph twice between the cards, the dates and the rest of the page", not twice,
                detail=", ".join(twice[:4]))

        events = []
        for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
            try:
                data = json.loads(blob)
            except ValueError:
                continue
            events += [e for e in (data if isinstance(data, list) else [data]) if isinstance(e, dict)]
        wrong = []
        for title, wid in mine.items():
            want = list(dict.fromkeys([cards.get(title, ""), (dates.get(wid) or [""])[0].split("?", 1)[0]]))
            for e in events:
                if e.get("url", "").endswith("/workshops/%d" % wid) and e.get("image") != want:
                    wrong.append(f"{title}: {e.get('image')}")
        s.check("the page's event data names each retreat's own photographs", not wrong
                and any(e.get("url", "").endswith("/workshops/%d" % ids[0]) for e in events),
                detail="; ".join(wrong[:2]) or "no event for the retreat with its own photo")
    finally:
        m.ROOM_PHOTO_DIR = real_dir
        conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + " %",))
        if hidden:
            conn.execute(f"UPDATE workshops SET active = 1 WHERE id IN ({','.join('?' * len(hidden))})", hidden)
        conn.commit()
        conn.close()
        shutil.rmtree(scratch, ignore_errors=True)


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
        keys, listed = [], set()
        for tag in re.findall(r"<img\b[^>]*>", shown):
            hit = re.search(r'\ssrc="([^"]*)"', tag)
            if not hit or not hit.group(1) or "Logo" in hit.group(1):
                continue
            key = picture_key(hit.group(1))
            # /workshops' retreat cards and its list of dates are one entry a retreat and one a date, so a retreat
            # with three dates shows its photograph three times by design, and how many retreats there are is the
            # database's (a run of the whole suite leaves more). Within each list a photograph counts once;
            # between a list and the rest of the page it still counts.
            in_list = re.search(r'class="[^"]*\b(g-wscard__img|g-sit__img)\b', tag)
            if in_list:
                if (in_list.group(1), key) in listed:
                    continue
                listed.add((in_list.group(1), key))
            keys.append(key)
        twice = sorted({k for k in keys if keys.count(k) > 1})
        s.check("no photograph twice, by what it is", not twice, detail=", ".join(twice[:4]))
    _unnamed_retreats(s, client)
    return s


if __name__ == "__main__":
    print(run().report())
