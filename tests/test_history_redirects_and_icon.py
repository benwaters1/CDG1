"""The owner, 24 November 2026: "put it to history", "the G crest", "fix the photo".

1. The old Squarespace addresses still asked for land somewhere. The Captain's
   Log (the old blog, 3,000 requests between 1 October and 23 November) and
   every page about the house's past or its restoration go to The History; the
   Log temporarily, since the house may publish it again. The rest go where
   OLD-ADDRESSES-23-nov.md said: offers to the retreats, gardens to the estate,
   the old FAQ to the family, the terms to the terms. A bot's probe still gets
   "not found".
2. The house's crest is the site's icon, and /favicon.ico and
   /apple-touch-icon.png answer at the root, where browsers ask for them whatever
   a page says (6,299 "not found" in November for the first alone).
3. /press opens on the front elevation at night, 1,600px, rather than a soft
   896px file; the band on /events that showed the same photograph shows the
   gates in the snow, so each has one home.
"""
import io
import os

from _harness import Suite
import _harness

m = _harness.m
ROOT = _harness.ROOT

TO = {
    "/captains-log": ("/history", 302),
    "/captains-log/": ("/captains-log", 301),     # the slash taken off first, then to The History
    "/captains-log/london-calling": ("/history", 302),
    "/captains-log/2017/12/18/a-true-love-story-never-ends": ("/history", 302),
    "/captains-log2/": ("/captains-log2", 301),
    "/the-project": ("/history", 301),
    "/front-facade": ("/history", 301),
    "/restoration-timeline": ("/history", 301),
    "/chateauexperiences/": ("/chateauexperiences", 301),
    "/chateauexperiences": ("/workshops", 301),
    "/gardens": ("/facilities", 301),
    "/about-us": ("/story", 301),
    "/terms-and-conditions": ("/terms", 301),
    "/mentions-legales": ("/legal", 301),
    "/chateau-videos/christmas-2023": ("/gallery", 302),
    "/boutique/antique-champagne-bucket": ("/", 301),
    "/39-treading-lightly": ("/", 301),
    "/chateaubook": ("/press", 301),
}


def run():
    s = Suite("Old addresses to The History, the crest as the icon, the press photograph")
    anon = m.app.test_client()

    s.section("1. Old addresses land")
    for path, (where, code) in TO.items():
        r = anon.get(path)
        loc = r.headers.get("Location", "")
        s.check(f"{path} -> {where} ({code})", r.status_code == code and loc.split("?")[0].endswith(where),
                detail=f"{r.status_code} {loc}")
    r = anon.get("/cookinginthecuisine")
    s.check("an old retreat offer goes to that retreat, or the list of them",
            r.status_code == 302 and ("/workshops" in r.headers.get("Location", "")
                                     or "/retreats/" in r.headers.get("Location", "")),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    r = anon.get("/captains-log?utm_source=pinterest")
    s.check("and keeps the campaign an old link carried",
            r.headers.get("Location", "").endswith("/history?utm_source=pinterest"),
            detail=str(r.headers.get("Location")))
    for probe in ("/wp-admin/", "/.env", "/dashboard/api-keys", "/captainslog-nonsense"):
        s.check(f"a probe is still not found: {probe}", anon.get(probe).status_code == 404)
    s.check("the history page itself still answers", anon.get("/history").status_code == 200)

    s.section("2. The crest is the site's icon")
    r = anon.get("/favicon.ico")
    body = r.get_data()
    s.check("/favicon.ico answers at the root", r.status_code == 200 and body[:4] == b"\x00\x00\x01\x00",
            detail=f"{r.status_code} {body[:8]!r}")
    from PIL import Image
    r = anon.get("/apple-touch-icon.png")
    s.check("/apple-touch-icon.png answers, 180px square",
            r.status_code == 200 and Image.open(io.BytesIO(r.get_data())).size == (180, 180),
            detail=str(r.status_code))
    s.check("so does the older iPhone name", anon.get("/apple-touch-icon-precomposed.png").status_code == 200)
    home = anon.get("/").get_data(as_text=True)
    s.check("a public page names the crest icons, not the lettered G drawn in SVG",
            'href="/static/favicon.ico"' in home and 'href="/static/apple-touch-icon.png"' in home
            and "data:image/svg+xml" not in home.split("</head>")[0], detail="")
    base = io.open(os.path.join(ROOT, "templates", "base.html"), encoding="utf-8").read()
    s.check("the staff pages too", "apple-touch-icon.png" in base and "favicon.ico" in base)
    icon = Image.open(os.path.join(ROOT, "static", "icon-512.png")).convert("RGB")
    corner, centre = icon.getpixel((4, 4)), icon.getpixel((256, 256))
    s.check("the app icon is the crest, white on the house navy (not the dark circle with a gold G)",
            corner == (20, 48, 95), detail=f"corner {corner} centre {centre}")

    s.section("3. /press opens on a sharp photograph of the front")
    press = anon.get("/press").get_data(as_text=True)
    s.check("the front elevation at night, 1,600px", "img/retreats/chateau-at-night.jpg" in press.split(
        'class="g-hero__img"')[1][:400] if 'class="g-hero__img"' in press else False)
    s.check("the soft 896px file is gone from it", "chateau_facade_formal.jpg" not in press)
    events = anon.get("/events").get_data(as_text=True)
    s.check("/events' band is the gates in the snow, so the night photograph has one home",
            "gate-in-snow.jpg" in events and "IMG_4368" not in events)
    return s


if __name__ == "__main__":
    print(run().report())
