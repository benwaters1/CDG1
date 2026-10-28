"""The retreat pages, laid out by the image layout brief (owner's go-ahead, 28 Oct 2026).

Each retreat page had one "…, in Pictures" block of twenty to thirty photographs
sitting before an itinerary that had none. Now every day of the itinerary has
its own photograph, the best of the rest follow it, and the whole set is behind
"See all photos". What is held here is the brief's rules, page by page:

  - the block and its heading are gone, and nothing else is (test_copy_unchanged
    checks the words; this checks the pictures)
  - one photograph on every day, and none when the days and the photographs
    stop matching, so a change of nights cannot pin Foix to the wrong day
  - the whole set is still reachable, and the count on the link is true
  - never more than four photographs together, and no photograph twice
  - the rooms, one each, each opening its own page
  - a Commons photograph carries its licence credit
  - and pic() finds the house's own copy of a Squarespace photograph, finds the
    mirror's, and steps aside for one the owner has swapped
"""
import os
import re
import shutil

from _harness import Suite, db, ROOT
import _harness

m = _harness.m

KINDS = ("Noël", "Cooking", "Antique", "Seven")
DAYS = {"Noël": 5, "Cooking": 6, "Antique": 4, "Seven": 8}


def _section(page, cls):
    """From the first element with this class to the end of the page."""
    at = page.find(f'class="{cls}')
    return page[at:] if at >= 0 else ""


def _visible_imgs(page):
    """Images a guest sees: not the hidden See-all set, not the lightbox's stage."""
    page = re.sub(r"<div hidden>.*?</div>", "", page, flags=re.S)
    return [t for t in re.findall(r"<img\b[^>]*>", page) if "data-lb-img" not in t]


def _src(tag):
    hit = re.search(r'\ssrc="([^"]*)"', tag)
    return hit.group(1) if hit else ""


def run():
    s = Suite("Retreat pages, in pictures")
    conn = db()
    rows = conn.execute("SELECT id, title FROM workshops WHERE active = 1").fetchall()
    rooms = conn.execute("SELECT id FROM rooms WHERE active = 1").fetchall()
    conn.close()
    client = m.app.test_client()

    for kind in KINDS:
        row = next((r for r in rows if kind in r["title"]), None)
        if not s.check(f"{kind}: there is such a retreat to read", row is not None):
            continue
        r = client.get(f"/workshops/{row['id']}")
        if not s.check(f"{kind}: the page loads", r.status_code == 200, detail=str(r.status_code)):
            continue
        page = r.get_data(as_text=True)
        s.section(kind)
        s.check("the …, in Pictures block is gone", ", in Pictures" not in page
                and "g-gallery-sec" not in page)
        days = page.count('class="g-journey__d"')
        pinned = page.count('class="g-journey__card g-journey__card--pic"')
        s.check(f"every day of the itinerary has its photograph ({DAYS[kind]} days)",
                days == DAYS[kind] and pinned == days, detail=f"{pinned} of {days}")
        s.check("each pinned as a journal page", page.count('class="g-carnet ') == days)
        # The whole set, behind See all, with the number it says.
        seeall = re.search(r'<div class="g-seeall" data-lb-group>.*?<div hidden>(.*?)</div>', page, re.S)
        said = re.search(r"See all (\d+) photos", page)
        held = len(re.findall(r"<img\b", seeall.group(1))) if seeall else 0
        s.check("See all opens the whole set, and says how many truly",
                said and held and int(said.group(1)) == held, detail=f"{said and said.group(1)} / {held}")
        s.check("and the lightbox it opens is on the page", 'data-lb aria-label="Photograph"' in page)
        # Never more than four together, never one photograph twice.
        mos = re.findall(r'<div class="g-mos .*?(?=<div class="g-cadre|<figure class="g-cadre|<div class="g-seeall)', page, re.S)
        s.check("a collage is never more than four", all(len(re.findall(r"<img\b", b)) <= 4 for b in mos))
        # The house's logo is on every page twice, at the head and the close; it is not a photograph.
        # By what the photograph is: the page reaches here after the house's copies are swapped in, so a
        # Squarespace address and a house file can turn out to be one picture, at -800 or full size.
        srcs = [re.sub(r"-(800|2400)\.jpg$", ".jpg", _src(t).split("?", 1)[0])
                for t in _visible_imgs(page) if "Logo" not in _src(t)]
        twice = sorted({x for x in srcs if srcs.count(x) > 1})
        s.check("no photograph shown twice on the page", not twice, detail=", ".join(twice[:4]))
        s.check("the hero zooms slowly, and is asked for first",
                re.search(r'<img[^>]*fetchpriority="high"[^>]*class="g-hero__img g-zoom"', page) is not None)
        # The rooms.
        cards = re.findall(r'<a class="g-car__a" href="/book/(\d+)" aria-label="([^"]+)"', page)
        s.check("Les Chambres shows the rooms, each opening its own page",
                cards and {int(i) for i, _ in cards} <= {r["id"] for r in rooms}
                and len({i for i, _ in cards}) == len(cards), detail=str(cards))
        s.check("and each is named on its link", all(name.strip() for _, name in cards))
        if kind == "Cooking":
            s.check("the cave at Lombrives carries its licence credit",
                    "lombrives-cave-mouth" in page and "Traumrune" in page and "CC BY-SA 3.0" in page)
        if kind == "Seven":
            s.check("Seven Starry Nights has the mountains from the air across the page",
                    'class="g-plein ' in page and "from-the-air-mountains" in page)

    s.section("a change of nights cannot pin a photograph to the wrong day")
    row = next((r for r in rows if "Antique" in r["title"]), None)
    if row:
        with m.app.test_request_context("/"):
            conn = db()
            w = conn.execute("SELECT * FROM workshops WHERE id = ?", (row["id"],)).fetchone()
            conn.close()
            tpl = m.app.jinja_env.from_string(
                "{% from '_ws_copy.html' import ws_journey with context %}{{ ws_journey(w, day_photos=ph) }}")
            one = ["/static/img/retreats/foix-castle.jpg", "Foix"]
            right = tpl.render(w=w, ph=[one] * DAYS["Antique"])
            short = tpl.render(w=w, ph=[one] * (DAYS["Antique"] - 1))
        s.check("one for every day: pinned", right.count('class="g-carnet ') == DAYS["Antique"])
        s.check("one short: none at all, the drawings as they were",
                'class="g-carnet ' not in short and short.count('class="g-journey__ill"') == DAYS["Antique"])

    s.section("pic() and the house's copies of Squarespace photographs")
    held_url = ("https://images.squarespace-cdn.com/content/53c3b576e4b02bad423517b8/"
                "1481616074741-RYJEHZMD9TK30AACGC1R/foix-castle.jpg?format=1000w")
    with m.app.test_request_context("/"):
        tag = str(m.pic(held_url, "Foix"))
    s.check("one the house holds a copy of keeps its own address",
            f'src="{held_url}"' in tag, detail=tag[:160])
    s.check("and takes its size and its WebP sizes from the copy",
            "/pic/480/retreats/foix-castle.jpg 480w" in tag and 'width="' in tag, detail=tag[:300])
    # The mirror: a photograph fetched onto the volume.
    mirrored_url = ("https://images.squarespace-cdn.com/content/v1/6a55fc287fb69872c3cc49bd/"
                    "0000aaaa-test-photo/a-mirrored-one.jpg?format=1000w")
    name = m.mirror_name(mirrored_url) + ".jpg"     # as the mirror stores it
    os.makedirs(m.MIRROR_DIR, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "static", "img", "retreats", "vin-chaud-800.jpg"),
                os.path.join(m.MIRROR_DIR, name))
    real_index, real_over = m.mirror_cache_index, m.site_override_cache_index
    try:
        m.mirror_cache_index = lambda: {mirrored_url: name}
        with m.app.test_request_context("/"):
            tag = str(m.pic(mirrored_url, "Mirrored"))
        s.check("one the mirror holds gets its size and WebP sizes from the mirror",
                f"/pic/480/~m/{name} 480w" in tag and 'width="800" height="800"' in tag, detail=tag[:300])
        r = client.get(f"/pic/480/~m/{name}")
        s.check("and /pic serves the mirrored one as a WebP", r.status_code == 200
                and r.mimetype == "image/webp", detail=str(r.status_code))
        s.check("but nothing on the volume that is not a mirrored photograph",
                client.get("/pic/480/~m/../gudanes_hr.db").status_code == 404
                and client.get("/pic/480/~m/not-a-hash.jpg").status_code == 404)
        m.site_override_cache_index = lambda: {mirrored_url: "/site-photo/x/abc.jpg"}
        with m.app.test_request_context("/"):
            tag = str(m.pic(mirrored_url, "Swapped"))
        s.check("one the owner has swapped is left alone, so the swap shows and not the old picture",
                "srcset" not in tag and f'src="{mirrored_url}"' in tag, detail=tag[:200])
    finally:
        m.mirror_cache_index, m.site_override_cache_index = real_index, real_over
        try:
            os.remove(os.path.join(m.MIRROR_DIR, name))
        except OSError:
            pass
    return s


if __name__ == "__main__":
    print(run().report())
