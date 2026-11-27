"""The Tour de France 2027 map as an occasion's photograph (owner, 27 Nov 2026).

The owner had a map drawn of the château against the last week of the 2027
Tour de France, to be the photograph of Tour de France Week. A map is not a
photograph: it has words on it, and every frame an occasion's photograph goes
into crops to fill. Set on the occasion as a photograph is, it showed

    on the occasion's page at 1280px    58% of its height: the title,
                                        Toulouse and the château cut off
    on its card on a 375px phone        a 104px thumbnail, a quarter of its
                                        width
    in the home page's band on a phone  84% of its height, the title clipped

and every one of those pages rendered perfectly.

WHAT IS HELD.
  - THE FILES: the 2400 x 1600 map and its 800 x 533 copy, the name the
    page's -800 lookup finds. The copy is the same picture, not a stand-in.
  - THE LIST: the photograph select offers it as "Occasions: Tour de France
    2027 map", never its 800px copy, and an occasion saved with it through
    the page keeps it. Nothing sets it on occasion 10: the owner picks it.
  - DRAWN WHOLE: the card, the home page's band and the occasion's page mark
    it g-occasion__whole, and a photograph never; the page draws the
    full-size file, so the place names are sharp at the column's width.
  - IN A BROWSER, because a class in the markup says nothing about which of
    the rules that crop wins (.g-sit__img is cover in five places, one four
    classes deep): at 1280 and 375 the map is shown entire on the sand, in a
    grid of cards, as a lone card, in the band and on its page, and a
    photograph's card still fills its frame.
  - THE CONTROL: the same pages as they were before -- the class taken off,
    the page's old 28rem cover put back -- must be measured as cropped, or
    the measuring cannot see a crop and nothing here is worth reading. No
    browser is a failure, not a skip; set GUDANES_CHROME.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, free_window, keep_rows
import _harness
import test_staff_header_on_a_phone as phone

m = _harness.m
ROOT = _harness.ROOT
TAG = "ZMAPPHOTO"
MAP = "img/occasions/tour-de-france-2027-map.jpg"
MAP_800 = "img/occasions/tour-de-france-2027-map-800.jpg"
LABEL = "Occasions: Tour de France 2027 map"
PHOTO = "img/views/clouds-over-the-peaks.jpg"
WIDTHS = (1280, 375)

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function go() {
    var de = document.documentElement, out = {id: q.id, vw: de.clientWidth, pics: []};
    [].forEach.call(document.querySelectorAll('img'), function (i) {
      var where = i.closest('.g-sit') ? 'card' : i.closest('.g-hband__ph') ? 'band'
                : i.closest('.g-occasion-page figure') ? 'page' : null;
      if (!where) return;
      var r = i.getBoundingClientRect(), cs = getComputedStyle(i);
      var nw = i.naturalWidth, nh = i.naturalHeight, sw = 0, sh = 0;
      if (nw && nh && r.width && r.height) {
        // What share of the picture is on screen: cover scales it up to fill
        // and cuts what overflows; contain and fill show all of it.
        var s = cs.objectFit === 'cover' ? Math.max(r.width / nw, r.height / nh) : 0;
        sw = s ? Math.min(1, r.width / (nw * s)) : 1;
        sh = s ? Math.min(1, r.height / (nh * s)) : 1;
      }
      var name = i.closest('.g-sit') ? (i.closest('.g-sit').querySelector('.g-sit__name') || {}).textContent : '';
      out.pics.push({where: where, src: i.getAttribute('src'), name: (name || '').trim(),
                     whole: i.classList.contains('g-occasion__whole'),
                     w: r.width, h: r.height, fit: cs.objectFit, bg: cs.backgroundColor,
                     nw: nw, nh: nh, shown_w: sw, shown_h: sh,
                     ratio: r.height ? r.width / r.height : 0});
    });
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 0); });
})();
</script>
"""

# The occasion's page as it was before 27 Nov: every photograph cut to 28rem.
OLD_PAGE_IMG = 'style="width:100%;max-height:28rem;object-fit:cover;border-radius:2px"'


def standalone(page):
    """phone.standalone, with the pictures read from disk and loaded at once.

    The page names /static/img and the response gives it /pic srcsets; in a
    file:// frame neither answers, and a picture that never loads measures as
    nothing at all. So the srcset goes, the src points at the file itself, and
    lazy becomes eager: a card far down the home page must load in a 700px frame.
    """
    page = phone.standalone(page)
    page = re.sub(r'\s(?:srcset|sizes)="[^"]*"', "", page)
    static = "file:///" + os.path.join(ROOT, "static").replace("\\", "/").lstrip("/") + "/"
    page = page.replace('src="/static/', f'src="{static}')
    page = page.replace('loading="lazy"', 'loading="eager"')
    return page.replace(phone.PROBE, PROBE)


def as_it_was(page):
    """The control: the same page with the map treated as a photograph."""
    page = re.sub(r'<img class="g-occasion__whole"([^>]*?) style="[^"]*"',
                  lambda x: "<img" + x.group(1) + " " + OLD_PAGE_IMG, page)
    return page.replace(" g-occasion__whole", "").replace(' class="g-occasion__whole"', "")


def _cleanup(conn):
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN "
                 f"(SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM stay_occasions WHERE name LIKE '{TAG}%'")
    conn.commit()


def _occasion(conn, name):
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _form(name, first, last, **over):
    f = {"name": name, "first_night": first.isoformat(), "last_night": last.isoformat(),
         "kind": "stay", "chef_on_site": "1", "status": "draft", "description": "", "photo": ""}
    f.update(over)
    return f


def run():
    s = Suite("the Tour de France map as an occasion's photograph")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasions", "status = 'live' OR home_banner = 1")]
    conn.execute("UPDATE stay_occasions SET status = 'draft', home_banner = 0 "
                 "WHERE status = 'live' OR home_banner = 1")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, conn)
    finally:
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, conn):
    s.section("The files")
    from PIL import Image, ImageChops, ImageStat
    full_path = os.path.join(ROOT, "static", *MAP.split("/"))
    small_path = os.path.join(ROOT, "static", *MAP_800.split("/"))
    sizes = {}
    for p in (full_path, small_path):
        try:
            with Image.open(p) as im:
                sizes[p] = im.size
        except OSError:
            sizes[p] = None
    s.check("the map is in static/img/occasions at 2400 x 1600",
            sizes[full_path] == (2400, 1600), detail=str(sizes[full_path]))
    s.check("its copy is 800 x 533, under the name the page's -800 lookup finds",
            sizes[small_path] == (800, 533), detail=str(sizes[small_path]))
    if sizes[full_path] and sizes[small_path]:
        with Image.open(full_path) as big, Image.open(small_path) as small:
            shrunk = big.convert("RGB").resize(small.size, Image.LANCZOS)
            off = max(ImageStat.Stat(ImageChops.difference(shrunk, small.convert("RGB"))).mean)
        s.check("and it is the same picture, not a stand-in", off < 4,
                detail=f"differs by {off:.1f} a channel on average")
    with m.app.test_request_context("/"):
        url = m.stay_occasion_photo_url(MAP)
    s.check("an occasion with the map is drawn from the 800px copy on its card",
            url.endswith("/static/" + MAP_800), detail=url)

    s.section("The photograph list")
    listed = dict(m.stay_occasion_photos())
    s.check(f'the list offers it as "{LABEL}"', listed.get(MAP) == LABEL, detail=str(listed.get(MAP)))
    s.check("and never its 800px copy", MAP_800 not in listed)
    s.check("the other photographs keep the labels they had",
            listed.get(PHOTO) == "Views: clouds over the peaks", detail=str(listed.get(PHOTO)))
    page = oc.get("/admin/stay-occasions/new").get_data(as_text=True)
    s.check("the new-occasion form draws it in the photograph select",
            f'<option value="{MAP}">{LABEL}</option>' in page)

    # Both in one year, so the occasions page draws them as a grid of cards
    # (each year's are a grid of their own): a March two years on, clear of
    # the ateliers, which keep an occasion off sale.
    today = m.house_today()
    room = ensure_room()
    march = today.replace(year=today.year + 2, month=3, day=1)
    first = free_window(room["id"], 14, after_days=(march - today).days, clear_of_ateliers=True)
    if first.year != march.year or (first + timedelta(days=12)).year != march.year:
        first = march
    name_map, name_photo = f"{TAG} Tour", f"{TAG} Peaks"
    for name, photo, a in ((name_map, MAP, first), (name_photo, PHOTO, first + timedelta(days=10))):
        oc.post("/admin/stay-occasions/new", data=_form(name, a, a + timedelta(days=2)))
        occ = _occasion(conn, name)
        if not s.check(f"{name} is made through the page", occ is not None):
            return
        # On sale needs a price for a room.
        r = oc.post(f"/admin/stay-occasions/{occ['id']}", follow_redirects=True,
                    data=_form(name, a, a + timedelta(days=2), status="live", photo=photo,
                               home_banner="1" if photo == MAP else "",
                               **{f"price_{room['id']}": "300"}))
        s.check(f"{name} is saved live with its picture through the page",
                (_occasion(conn, name)["photo"], _occasion(conn, name)["status"]) == (photo, "live"),
                r, detail=str(dict(_occasion(conn, name))["photo"]))
    tour = _occasion(conn, name_map)
    s.check("the map's occasion is the home page's band",
            tour["home_banner"] == 1, detail=str(tour["home_banner"]))

    s.section("The markup")
    pages = {}
    for key, path in (("the occasions", "/whats-on/at-gudanes"), ("the home page", "/"),
                      ("the occasion's page", f"/whats-on/at-gudanes/{tour['slug']}")):
        r = m.app.test_client().get(path)
        if s.check(f"{key} answers", r.status_code == 200, r, detail=f"status {r.status_code}"):
            pages[key] = r.get_data(as_text=True)
    imgs = {k: re.findall(r"<img\b[^>]*>", v) for k, v in pages.items()}

    def tags(key, needle):
        return [t for t in imgs.get(key, []) if needle in t]

    map_cards = tags("the occasions", "tour-de-france-2027-map")
    s.check("the map's card is marked to be drawn whole",
            map_cards and all("g-sit__img g-occasion__whole" in t for t in map_cards), detail=str(map_cards))
    peaks = tags("the occasions", "clouds-over-the-peaks")
    s.check("a photograph's card is not", peaks and not any("g-occasion__whole" in t for t in peaks),
            detail=str(peaks))
    band = [t for t in tags("the home page", "tour-de-france-2027-map") if "g-sit__img" not in t]
    s.check("the home page's band marks the map too",
            band and all('class="g-occasion__whole"' in t for t in band), detail=str(band))
    on_page = tags("the occasion's page", "tour-de-france-2027-map")
    s.check("the occasion's page draws the full-size map, whole",
            on_page and all(f'src="/static/{MAP}"' in t and "g-occasion__whole" in t
                            and "max-height" not in t for t in on_page), detail=str(on_page))
    with m.app.test_request_context("/"):
        card = m.stay_occasion_card(conn, _occasion(conn, name_photo))
    s.check("a photograph's occasion is not marked whole", card["photo_whole"] is False)

    s.section("In a browser")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found", browser,
                   detail="install Chrome or Chromium, or set GUDANES_CHROME to one -- "
                          "this suite measures and cannot without one"):
        return
    # The lone card: the rules that crop hardest are the ones for a card on its
    # own. The photograph's occasion off sale leaves the map alone on the list.
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE name = ?", (name_photo,))
    conn.commit()
    r = m.app.test_client().get("/whats-on/at-gudanes")
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE name = ?", (name_photo,))
    conn.commit()
    if s.check("the occasions with the map alone answer", r.status_code == 200, r):
        pages["the map's lone card"] = r.get_data(as_text=True)

    files, frames = [], []
    for control in (False, True):
        for key, html in pages.items():
            page = standalone(html)
            files.append(as_it_was(page) if control else page)
            for w in WIDTHS:
                frames.append({"page": len(files) - 1, "width": w, "name": key, "control": control})
    workdir = tempfile.mkdtemp(prefix="gudanes_mapphoto_")
    try:
        for i, page in enumerate(files):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(page)
        try:
            seen, proc = phone.measure(browser, frames, workdir)
        except subprocess.TimeoutExpired:
            s.check("the browser finished measuring", False, detail="timed out after 240s")
            return
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    missing = [i for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    got = [(f, seen[i]) for i, f in enumerate(frames) if i in seen]
    s.check("each frame is the width asked for",
            all(d["vw"] == f["width"] for f, d in got),
            detail="; ".join(f"{f['name']} {f['width']}: {d['vw']}" for f, d in got if d["vw"] != f["width"]))

    def the_map(d):
        return [p for p in d["pics"] if "tour-de-france-2027-map" in (p["src"] or "")]

    def cropped(p):
        return p["shown_w"] < 0.995 or p["shown_h"] < 0.995

    def say(p):
        return (f"{p['where']} {p['w']:.0f}x{p['h']:.0f} {p['fit']}, "
                f"{p['shown_w']:.0%} x {p['shown_h']:.0%} shown")

    s.section("The control: the map drawn as a photograph must be caught cropped")
    ctl = [(f, d) for f, d in got if f["control"]]
    caught = {(f["name"], f["width"]) for f, d in ctl if any(cropped(p) for p in the_map(d))}
    for key, w in (("the occasion's page", 1280), ("the occasions", 375), ("the home page", 375)):
        s.check(f"{key} at {w}px is reported cropped", (key, w) in caught,
                detail="caught at: " + (", ".join(f"{k} {x}" for k, x in sorted(caught)) or "nowhere"))

    real = [(f, d) for f, d in got if not f["control"]]
    for w in WIDTHS:
        group = [(f, d) for f, d in real if f["width"] == w]
        s.section(f"At {w}px")
        found = {(f["name"], p["where"]) for f, d in group for p in the_map(d)}
        s.check("the map is drawn in every place it goes",
                {("the occasions", "card"), ("the map's lone card", "card"), ("the home page", "card"),
                 ("the home page", "band"), ("the occasion's page", "page")} <= found,
                detail=str(sorted(found)))
        unloaded = [f"{f['name']}: {p['where']}" for f, d in group for p in the_map(d) if not p["nw"]]
        s.check("and loaded, every time", not unloaded, detail="; ".join(unloaded))
        cut = [f"{f['name']}: {say(p)}" for f, d in group for p in the_map(d) if cropped(p)]
        s.check("nowhere is any of it cut off", not cut, detail="; ".join(cut))
        sand = [f"{f['name']}: {p['where']} on {p['bg']}" for f, d in group for p in the_map(d)
                if p["bg"] not in ("rgb(246, 241, 231)",)]
        s.check("it sits on the sand (#F6F1E7), a shade off its own paper", not sand,
                detail="; ".join(sand))
        page_map = [p for f, d in group if f["name"] == "the occasion's page" for p in the_map(d)]
        s.check("on its page it is drawn at its own shape, as wide as the column, from the full-size file",
                page_map and all(abs(p["ratio"] - 1.5) < 0.02 and p["nw"] == 2400 for p in page_map),
                detail="; ".join(f"{p['w']:.0f}x{p['h']:.0f} of {p['nw']}" for p in page_map))
        photos = [p for f, d in group if f["name"] == "the occasions" for p in d["pics"]
                  if "clouds-over-the-peaks" in (p["src"] or "")]
        s.check("a photograph's card still fills its frame (cover)",
                photos and all(p["fit"] == "cover" and not p["whole"] for p in photos),
                detail="; ".join(say(p) for p in photos))
