"""The home page on a phone: one question asked once, two rows to swipe, every photograph sharp.

WHAT WENT WRONG. The owner, 26 November 2026: "I don't like the homepage on
the mobile". Measured at 375 x 812 it was 12,264px, fifteen screens: the same
choice was put three times (the hero's Retreats and Stay the night tabs, La
Vie de Château's two buttons, then A Night, or la Vie de Château), the rooms
were six cards stacked one under another, and What Guests Say took 3,003px
for 116 words, one quote to a screen. Nothing was broken. It was simply long.

WHAT THE PAGE DOES NOW, under 600px (BRIEF-home-on-a-phone-26-nov):

- the opening screen as it was, with one sentence under the motto, so it
  ends inside the first screen;
- the choice, asked once: two picture cards, Retreats and Stay the night,
  each opening its booking (the retreat dates, the date search);
- the next three dates, with "See every date" under them;
- the rooms, one card and the edge of the next, to swipe;
- the restoration in one photograph, one paragraph and its link;
- Ways to Spend the Days in its tabs, inside one screen;
- the quotes, one card and the edge of the next, to swipe, with dots;
- Come and see it, and the footer, as they were.

From 600px up none of it applies and the page is as it was.

WHAT IS MEASURED, in headless Chrome as a touch screen at 2x (the plumbing
of test_staff_header_on_a_phone):

- the page above its footer is no more than LIMIT_ABOVE_FOOTER at 375px,
  and no page is wider than its screen at 320, 375 or 390;
- each section does the one thing above, and the three that were the same
  question are not drawn;
- every photograph drawn has a srcset and a sizes, and the copy the browser
  chose from it is at least twice the width it is drawn at;
- nothing in the first screen loads lazily, and everything after it does
  (and anything not drawn does too, so a phone never fetches it);
- at 1100px the phone's cards and dots are not drawn and the hero's card,
  La Vie de Château and the comparison are.

WHY NOT 6,000px FOR THE WHOLE PAGE, as the brief asked. The brief measured
12,264px with the footer in it and asked for half, with the footer kept as it
is. The footer alone is 1,908px at 375 on every page of the site, so the
home page's own part would have had to fit in about 4,100px, five screens for
the nine things the brief lists to keep, the opening screen and Come and see
it among them. What is held instead is the brief's "about six screens" for
the page's own part: six and a half screens of 812, against fourteen before.
The footer is a question for every page, not this one. It was answered the
next day (27 Nov 2026, the owner's yes to a shorter footer on a phone): Visit
and Help each a button over its list, and 1,955px became about 1,220 under
every page, so the home page at 375 is about 6,370px in all, against 12,591.
test_footer_on_a_phone holds that.

THE PICTURES ARE THE LIVE SITE'S. The page's photographs from Squarespace and
Booking.com are served live from the house's own copies (the photo mirror);
here there are none, and without them those photographs would go out with no
srcset at all, which is not what a guest gets. So each is given a stand-in
copy as wide as its address asks Squarespace for, exactly as the mirror would
hold it, and taken away after. The browser is then pointed at the files
themselves: each srcset candidate names its file with "#w=<width>", so the
copy the browser chose can be read back from currentSrc.

THE MEASURING IS CHECKED TOO, by four pages it must call wrong: one with a
section lengthened, one with a srcset taken off a room's photograph, one
whose first card offers only a copy too small for it, and one with something
640px wide at 320px. No browser is a failure, not a skip; set GUDANES_CHROME.
"""
import html as htmllib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from _harness import Suite, db, ROOT
import _harness
import test_staff_header_on_a_phone as phone

m = _harness.m

SCREEN_H = 812
PHONES = (390, 375, 320)
PHONE = 375
DESK = 1100
# Six and a half screens of 812 for the page above its footer (see WHY NOT 6,000px above).
LIMIT_ABOVE_FOOTER = 5278

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function drawn(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }
  function box(el) {
    var r = el.getBoundingClientRect();
    return {top: Math.round(r.top + scrollY), left: Math.round(r.left), w: Math.round(r.width), h: Math.round(r.height)};
  }
  function section(el) {
    var s = el.closest('section, header, footer, p.g-where');
    return s ? (s.tagName.toLowerCase() + '.' + String(s.className).split(' ')[0]) : '?';
  }
  async function go() {
    var de = document.documentElement;
    /* Down the page as a guest would, so every lazy photograph is asked for; then each row to its end. */
    for (var y = 0; y < de.scrollHeight; y += 300) { scrollTo(0, y); await sleep(60); }
    document.querySelectorAll('.g-car__track, .rc-grid').forEach(function (t) { t.scrollLeft = t.scrollWidth; });
    await sleep(500);
    document.querySelectorAll('.g-car__track, .rc-grid').forEach(function (t) { t.scrollLeft = 0; });
    scrollTo(0, 0);
    await sleep(1500);
    var foot = document.querySelector('footer');
    var out = {id: q.id, asked: +q.w, vw: de.clientWidth, docW: de.scrollWidth, docH: de.scrollHeight,
               vh: de.clientHeight, dpr: devicePixelRatio, coarse: matchMedia('(pointer: coarse)').matches,
               footTop: foot ? box(foot).top : null, imgs: [], show: {}, rows: {}, out: []};
    /* Past the screen's edge: the page clips its sides, so a document wider than the screen never shows as one;
       anything drawn beyond the edge is simply cut off. So each element is asked, except inside a box that
       scrolls or clips sideways on purpose (the two rows to swipe, the tabs). */
    var vw = de.clientWidth;
    function clipped(el) {
      for (var a = el.parentElement; a && a !== document.body && a !== de; a = a.parentElement) {
        var ox = getComputedStyle(a).overflowX;
        if (ox !== 'visible') return true;
      }
      return false;
    }
    document.querySelectorAll('body *').forEach(function (el) {
      if (out.out.length > 8 || !drawn(el)) return;
      var r = el.getBoundingClientRect();
      if ((r.right > vw + 1 || r.left < -1) && !clipped(el)) {
        out.out.push(el.tagName.toLowerCase() + '.' + String(el.className).split(' ')[0] + ' ' + Math.round(r.left) + '-' + Math.round(r.right));
      }
    });
    document.querySelectorAll('img').forEach(function (im) {
      var shown = drawn(im), b = box(im), cur = im.currentSrc || '';
      var w = /#w=(\d+)$/.exec(cur);
      out.imgs.push({where: section(im), cls: String(im.className), alt: (im.alt || '').slice(0, 50),
        shown: shown, top: b.top, w: b.w, h: b.h, lazy: im.getAttribute('loading') === 'lazy',
        srcset: im.hasAttribute('srcset'), sizes: im.getAttribute('sizes'), loaded: im.complete && im.naturalWidth > 0,
        chose: w ? +w[1] : (im.complete ? im.naturalWidth : 0), file: cur.split('/').slice(-2).join('/')});
    });
    var hero = document.querySelector('.g-hero');
    out.heroBottom = hero ? box(hero).top + box(hero).h : null;
    /* The film's pause button and the words it must not cover. */
    var pause = document.querySelector('.g-hero__pause');
    out.pause = drawn(pause) ? box(pause) : null;
    out.heroWords = [].filter.call(document.querySelectorAll(
      '.g-hero__eyebrow, .g-hero__title, .g-hist-tag--home, .g-hero__motto, .g-hero__lede, .g-hero__fact'), drawn)
      .map(function (el) {
        /* the words themselves, not the paragraph's full width: a range round the text */
        var rg = document.createRange(); rg.selectNodeContents(el);
        return [].map.call(rg.getClientRects(), function (r) {
          return {cls: String(el.className), top: Math.round(r.top + scrollY), left: Math.round(r.left),
                  w: Math.round(r.width), h: Math.round(r.height)};
        });
      }).reduce(function (a, b) { return a.concat(b); }, []);
    [['hbook', '.g-hero .g-hbook'], ['fact', '.g-hero__fact'], ['lede', '.g-hero__lede'], ['twoways', '.g-twoways'],
     ['fullCopy', '.g-full__copy'], ['fullSide', '.g-full__side'], ['every', '.g-full__every'], ['sow', '.g-sow'],
     ['aerial', '.g-homeroom--aerial'], ['carBar', '.g-car--cards .g-car__bar'], ['dots', '.rc-dots'],
     ['restoNow', '.g-homeresto__f:nth-child(2)']].forEach(function (p) {
      out.show[p[0]] = drawn(document.querySelector(p[1]));
    });
    out.cards = [].map.call(document.querySelectorAll('.g-twoways__c'), function (a) {
      var im = a.querySelector('img');
      /* textContent, not innerText: what a screen reader reads as the link's name, so the name and its line
         need a space between them in the page itself. */
      return {shown: drawn(a), href: a.getAttribute('href'), words: a.textContent.replace(/\s+/g, ' ').trim(),
              photo: im ? (im.currentSrc || '').split('#')[0].split('/').pop() : '', h: box(a).h};
    });
    var every = document.querySelector('.g-full__every a');
    out.everyHref = every ? every.getAttribute('href') : null;
    out.dateRows = [].filter.call(document.querySelectorAll('.g-full__row'), drawn).length;
    function row(sel, card) {
      var t = document.querySelector(sel);
      if (!t) return null;
      var cs = [].filter.call(t.querySelectorAll(card), drawn);
      return {scrolls: t.scrollWidth > t.clientWidth + 1, trackW: t.clientWidth, n: cs.length,
              firstW: cs.length ? box(cs[0]).w : 0, top: box(t).top, h: box(t).h};
    }
    out.rows.rooms = row('.g-car--cards .g-car__track', '.g-homeroom');
    out.rows.says = row('.rc-says .rc-grid', '.rc-card');
    out.roomCards = [].filter.call(document.querySelectorAll('.g-car--cards .g-homeroom:not(.g-homeroom--aerial)'), drawn).map(function (c) {
      return {photo: drawn(c.querySelector('img')), name: drawn(c.querySelector('.g-homeroom__name')),
              price: drawn(c.querySelector('.g-price')), check: drawn(c.querySelector('a.g-btn')),
              words: drawn(c.querySelector('.g-p')), tags: drawn(c.querySelector('.g-tags'))};
    });
    out.dotN = document.querySelectorAll('.rc-dots .rc-dot').length;
    out.quoteN = document.querySelectorAll('.rc-says .rc-card').length;
    out.restoPhotos = [].filter.call(document.querySelectorAll('.g-homeresto img'), drawn).length;
    var secs = {};
    [['plaisirs', '.g-plaisirs'], ['says', 'section.rc-says'], ['twowaysSec', '.g-twoways']].forEach(function (p) {
      var el = document.querySelector(p[1]); secs[p[0]] = drawn(el) ? box(el).h : 0;
    });
    out.secs = secs;
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 600); });
})();
</script>
"""

# What the mirror would hold for a Squarespace or Booking.com address: as wide as the address asks for.
_ASKED = (re.compile(r"[?&]format=(\d+)w"), re.compile(r"/max(\d+)x\d+/"))


def _asked_width(url):
    for pattern in _ASKED:
        hit = pattern.search(url)
        if hit:
            return int(hit.group(1))
    return 1600


def stand_in_mirror(page):
    """Give every hotlinked photograph the page shows a copy on the (scratch) mirror, as the live site has.

    Returns an undo: the rows as they were and the files written."""
    from PIL import Image
    urls = sorted({htmllib.unescape(u) for u in re.findall(r'<img\b[^>]*\ssrc="(https://[^"]+)"', page)
                   if any(h in u for h in m.MIRROR_HOSTS)})
    conn = db()
    saved = {r["source_url"]: dict(r) for r in conn.execute(
        "SELECT * FROM mirrored_images WHERE source_url IN (%s)" % ",".join("?" * len(urls)), urls)} if urls else {}
    written = []
    os.makedirs(m.MIRROR_DIR, exist_ok=True)
    for url in urls:
        png = ".png" in url.split("?", 1)[0].lower()
        name = m.mirror_name(url) + (".png" if png else ".jpg")
        path = os.path.join(m.MIRROR_DIR, name)
        if not os.path.exists(path):
            w = _asked_width(url)
            im = Image.new("RGBA" if png else "RGB", (w, w * 2 // 3), (196, 184, 160, 255) if png else (196, 184, 160))
            buf = io.BytesIO()
            im.save(buf, "PNG" if png else "JPEG", quality=50)
            with open(path, "wb") as fh:
                fh.write(buf.getvalue())
            written.append(path)
        conn.execute("DELETE FROM mirrored_images WHERE source_url = ?", (url,))
        conn.execute("INSERT INTO mirrored_images (source_url, filename, bytes, content_type, fetched_at) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (url, name, os.path.getsize(path), "image/png" if png else "image/jpeg",
                      _harness.datetime_now()))
    conn.commit()
    conn.close()
    m._MIRROR_CACHE["at"] = 0.0

    def undo():
        c = db()
        for url in urls:
            c.execute("DELETE FROM mirrored_images WHERE source_url = ?", (url,))
            if url in saved:
                row = saved[url]
                cols = list(row)
                c.execute("INSERT INTO mirrored_images (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                          [row[k] for k in cols])
        c.commit()
        c.close()
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass
        m._MIRROR_CACHE["at"] = 0.0
    return urls, undo


def _file_url(path):
    return "file:///" + path.replace("\\", "/").lstrip("/")


def _local(rel):
    """The file a /pic name or a page address stands for."""
    rel = htmllib.unescape(rel).split("?", 1)[0].split("#", 1)[0]
    if rel.startswith("~m/"):
        return os.path.join(m.MIRROR_DIR, rel[3:])
    if rel.startswith("~r/"):
        return os.path.join(m.ROOM_PHOTO_DIR, rel[3:])
    return os.path.join(ROOT, "static", "img", *rel.split("/"))


def point_at_files(page):
    """Every photograph's src and srcset at the file itself, each candidate marked with its width."""
    def src(match):
        url = htmllib.unescape(match.group(1))
        if url.startswith("/static/"):
            return ' src="%s"' % _file_url(os.path.join(ROOT, "static", *url[len("/static/"):].split("?")[0].split("/")))
        if url.startswith("/mirrored-photo/"):
            return ' src="%s"' % _file_url(_local("~m/" + url[len("/mirrored-photo/"):]))
        if url.startswith("/room-photos/"):
            return ' src="%s"' % _file_url(_local("~r/" + url[len("/room-photos/"):]))
        return match.group(0)

    def srcset(match):
        out = []
        for cand in match.group(1).split(","):
            cand = cand.strip()
            hit = re.fullmatch(r"/pic/(\d+)/(\S+)\s+(\d+)w", cand)
            if hit:
                out.append("%s#w=%s %sw" % (_file_url(_local(hit.group(2))), hit.group(1), hit.group(3)))
            else:
                out.append(cand)
        return ' srcset="%s"' % ", ".join(out)

    def tag(match):
        t = re.sub(r'\ssrc="([^"]*)"', src, match.group(0))
        return re.sub(r'\ssrcset="([^"]*)"', srcset, t)
    return re.sub(r"<img\b[^>]*>", tag, page)


def measure(browser, frames, workdir):
    """phone.measure, at 2x and a screen tall, the frames left to say when they are done."""
    cells = [f'<iframe src="page{f["page"]}.html#id={i}&w={f["width"]}" '
             f'style="display:block;border:0;width:{f["width"]}px;height:{SCREEN_H}px"></iframe>'
             for i, f in enumerate(frames)]
    wrapper = os.path.join(workdir, "phones.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(phone.WRAPPER_HEAD + "\n".join(cells) + "\n</body></html>")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--user-data-dir=" + os.path.join(workdir, "profile"), "--hide-scrollbars",
           "--force-device-scale-factor=2",
           "--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
           "primaryHoverType=1,availableHoverTypes=1",
           "--allow-file-access-from-files",
           "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=90000", "--dump-dom", _file_url(wrapper)]
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    seen = {}
    for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        d = json.loads(htmllib.unescape(blob))
        seen[int(d["id"])] = d
    return seen, proc


# The controls: each page must be called wrong.
LONG = "<style>.g .g-twoways{ padding-bottom: 1600px !important; }</style>"
WIDE = '<div style="width:640px;height:2px"></div>'


def _first_img(page, cls, change):
    """The page with change() made to the first <img> of this class that has a srcset."""
    done = []

    def one(match):
        t = match.group(0)
        if done or f'class="{cls}"' not in t or " srcset=" not in t:
            return t
        done.append(1)
        return change(t)
    return re.sub(r"<img\b[^>]*>", one, page)


def _drop_room_srcset(page):
    """A room card's photograph without its srcset (the second card: the first is the estate from the air)."""
    seen = []

    def one(match):
        t = match.group(0)
        if 'class="g-homeroom__img"' not in t or " srcset=" not in t or "from-the-air" in t:
            return t
        seen.append(1)
        return re.sub(r'\ssrcset="[^"]*"', "", t) if len(seen) == 1 else t
    return re.sub(r"<img\b[^>]*>", one, page)


def _small_twoways(page):
    """The first card's photograph offered only at 480px."""
    def change(t):
        cand = re.search(r'srcset="([^",]+?) 480w', t)
        return re.sub(r'\ssrcset="[^"]*"', ' srcset="%s 480w"' % cand.group(1), t) if cand else t
    return _first_img(page, "g-twoways__img", change)


def soft(d):
    """Photographs drawn without a srcset and sizes, or from a copy under twice their width, or never loaded."""
    bad = []
    for im in d["imgs"]:
        if not im["shown"] or im["w"] < 2:
            continue
        name = f"{im['where']} {im['cls'] or im['alt']!r}"
        if not im["srcset"] or not im["sizes"]:
            bad.append(f"{name}: no {'srcset' if not im['srcset'] else 'sizes'}")
        elif not im["loaded"]:
            bad.append(f"{name}: never loaded")
        elif im["chose"] < 2 * im["w"]:
            bad.append(f"{name}: the {im['chose']}px copy for {im['w']}px drawn")
    return bad


def run():
    s = Suite("the home page on a phone")

    s.section("The measuring")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found",
                   browser, detail="install Chrome or Chromium, or set GUDANES_CHROME "
                                   "to one -- this suite measures and cannot without one"):
        return s

    client = m.app.test_client()
    first = client.get("/")
    if not s.check("the home page renders", first.status_code == 200, first, detail=f"status {first.status_code}"):
        return s
    urls, undo = stand_in_mirror(first.get_data(as_text=True))
    try:
        r = client.get("/")
        page = r.get_data(as_text=True)
        s.check(f"its {len(urls)} photographs from Squarespace and Booking.com are the house's copies, as live",
                not any(u in page for u in urls) or not urls,
                detail=", ".join(u for u in urls if u in page)[:300])
        s.check("every one of them is one the mirror fetches", set(urls) <= set(m.hotlinked_urls()),
                detail=", ".join(sorted(set(urls) - set(m.hotlinked_urls())))[:300])
        page = phone.standalone(page)
        if not s.check("the home page can be fitted with the probe", phone.PROBE in page):
            return s
        page = point_at_files(page.replace(phone.PROBE, PROBE))
        pages = [page,
                 page.replace("</head>", LONG + "</head>", 1),
                 _drop_room_srcset(page),
                 _small_twoways(page),
                 re.sub(r"<body[^>]*>", lambda t: t.group(0) + WIDE, page, count=1)]
        s.check("the controls each changed the page", all(p != page for p in pages[1:]),
                detail=str([p != page for p in pages[1:]]))
        frames = [{"page": 0, "width": w} for w in PHONES + (DESK,)]
        frames += [{"page": 1, "width": PHONE, "control": "long"},
                   {"page": 2, "width": PHONE, "control": "no srcset"},
                   {"page": 3, "width": PHONE, "control": "small copy"},
                   {"page": 4, "width": 320, "control": "wide"}]
        workdir = tempfile.mkdtemp(prefix="gudanes_homephone_")
        try:
            for i, p in enumerate(pages):
                with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                    fh.write(p)
            try:
                seen, proc = measure(browser, frames, workdir)
            except subprocess.TimeoutExpired:
                s.check("the browser finished measuring", False, detail="timed out after 300s")
                return s
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    finally:
        undo()

    missing = [i for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    got = [(f, seen[i]) for i, f in enumerate(frames) if i in seen]
    wrong = [f"{f['width']}: frame {d['vw']}px, coarse {d['coarse']}, {d['dpr']}x"
             for f, d in got if d["vw"] != f["width"] or not d["coarse"] or d["dpr"] != 2]
    s.check("each frame is a 2x touch screen at the width asked for", not wrong, detail="; ".join(wrong))
    real = {f["width"]: d for f, d in got if not f.get("control")}
    ctl = {f["control"]: d for f, d in got if f.get("control")}

    def above_footer(d):
        return (d["footTop"] if d["footTop"] is not None else d["docH"])

    s.section("The controls: four pages it must call wrong")
    if "long" in ctl:
        s.check("a section lengthened is reported too long", above_footer(ctl["long"]) > LIMIT_ABOVE_FOOTER,
                detail=f"{above_footer(ctl['long'])}px")
    if "no srcset" in ctl:
        hit = [b for b in soft(ctl["no srcset"]) if "g-homeroom__img" in b and "no srcset" in b]
        s.check("a room's photograph without its srcset is reported", hit, detail=str(soft(ctl["no srcset"])[:3]))
    if "small copy" in ctl:
        hit = [b for b in soft(ctl["small copy"]) if "g-twoways__img" in b and "480px copy" in b]
        s.check("a card offered only a copy too small for it is reported", hit, detail=str(soft(ctl["small copy"])[:3]))
    if "wide" in ctl:
        s.check("something 640px wide at 320 is reported past the screen's edge", ctl["wide"]["out"],
                detail=f"{ctl['wide']['docW']}px in {ctl['wide']['vw']}px, nothing named")
    s.check("all four controls were measured", len(ctl) == 4, detail=str(sorted(ctl)))

    s.section("How long, how wide")
    d = real.get(PHONE)
    if not s.check("measured at 375px", d is not None):
        return s
    top = above_footer(d)
    s.check(f"at 375px the page above its footer is {top}px: no more than {LIMIT_ABOVE_FOOTER} "
            f"(six and a half screens; {d['docH']}px with the footer)", top <= LIMIT_ABOVE_FOOTER)
    for w in PHONES:
        if w in real:
            s.check(f"at {w}px nothing is wider than the screen, or drawn past its edge",
                    real[w]["docW"] <= real[w]["vw"] and not real[w]["out"],
                    detail=f"{real[w]['docW']}px on a {real[w]['vw']}px screen; past the edge: {real[w]['out']}")

    s.section("At 375px, each screen its one job")
    show = d["show"]
    s.check("the opening screen ends inside the first screen", d["heroBottom"] is not None and d["heroBottom"] <= SCREEN_H,
            detail=f"the hero ends at {d['heroBottom']}px")
    s.check("one sentence under the motto, not two", show["lede"] and not show["fact"], detail=str(show))
    for w in PHONES:
        dw = real.get(w)
        if not dw or not dw["pause"]:
            continue
        p = dw["pause"]
        hit = [r["cls"] for r in dw["heroWords"]
               if min(p["left"] + p["w"], r["left"] + r["w"]) - max(p["left"], r["left"]) > 0.5
               and min(p["top"] + p["h"], r["top"] + r["h"]) - max(p["top"], r["top"]) > 0.5]
        s.check(f"at {w}px the film's pause button covers no word of the hero", not hit,
                detail=f"the button at {p}, over {sorted(set(hit))}")
    s.check("the hero's Retreats / Stay the night card is not drawn", not show["hbook"])
    s.check("nor La Vie de Château's words, nor A Night, or la Vie de Château: the choice is asked once",
            not show["fullCopy"] and not show["sow"], detail=str(show))
    cards = [c for c in d["cards"] if c["shown"]]
    s.check("two picture cards ask it", len(cards) == 2, detail=str(d["cards"]))
    if len(cards) == 2:
        retreat, stay = cards
        s.check("Retreats, three to seven nights, all inclusive, opening the retreat dates",
                retreat["words"] == "Retreats Three to seven nights, all inclusive"
                and retreat["href"].endswith("/workshops#dates"), detail=str(retreat))
        s.check("Stay the night, a room and breakfast, your own dates, opening the date search",
                stay["words"] == "Stay the night A room and breakfast, your own dates"
                and stay["href"].rstrip("/").endswith("/book"),
                detail=str(stay))
        s.check("each its own photograph", retreat["photo"] and stay["photo"] and retreat["photo"] != stay["photo"],
                detail=f"{retreat['photo']} / {stay['photo']}")
    if show["fullSide"] and d["dateRows"]:
        s.check(f"the next dates: {d['dateRows']} shown, no more than three", 1 <= d["dateRows"] <= 3)
        s.check("and See every date under them, opening the retreat dates",
                show["every"] and (d["everyHref"] or "").endswith("/workshops#dates"), detail=str(d["everyHref"]))
    rooms = d["rows"]["rooms"]
    if rooms:
        s.check("the rooms are a row to swipe", rooms["scrolls"], detail=str(rooms))
        s.check("one card and the edge of the next on the screen",
                0.7 * rooms["trackW"] <= rooms["firstW"] <= 0.92 * rooms["trackW"], detail=str(rooms))
        s.check("the estate from the air does not open them: on a phone the row is rooms, and that photograph is "
                "the opening screen's wherever the film will not play", not show["aerial"])
        bad = [c for c in d["roomCards"] if not (c["photo"] and c["name"] and c["price"] and c["check"])
               or c["words"] or c["tags"]]
        s.check("each room card its photograph, its name, its price and Check dates, and no more",
                d["roomCards"] and not bad, detail=str(bad[:2] or d["roomCards"][:1]))
    s.check("the restoration in one photograph", d["restoPhotos"] == 1 and not show["restoNow"],
            detail=f"{d['restoPhotos']} drawn")
    s.check("Ways to Spend the Days in its tabs, inside one screen", 0 < d["secs"]["plaisirs"] <= SCREEN_H,
            detail=f"{d['secs']['plaisirs']}px")
    says = d["rows"]["says"]
    if says:
        s.check("the quotes are a row to swipe", says["scrolls"], detail=str(says))
        s.check("one quote and the edge of the next on the screen",
                0.7 * says["trackW"] <= says["firstW"] <= 0.92 * says["trackW"], detail=str(says))
        s.check(f"a dot for each of the {d['quoteN']} quotes, under them", show["dots"] and d["dotN"] == d["quoteN"],
                detail=f"{d['dotN']} dots, drawn {show['dots']}")
        s.check("and all of What Guests Say inside one screen, never one quote to a screen",
                0 < d["secs"]["says"] <= SCREEN_H, detail=f"{d['secs']['says']}px")

    s.section("At 375px on a 2x screen, every photograph sharp")
    bad = soft(d)
    s.check(f"each of the {sum(1 for i in d['imgs'] if i['shown'])} photographs drawn has a srcset and a sizes, "
            "and the copy chosen is at least twice its drawn width", not bad, detail="; ".join(bad[:6]))
    early = [f"{i['where']} {i['cls'] or i['alt']!r} at {i['top']}px" for i in d["imgs"]
             if i["shown"] and i["top"] < SCREEN_H and i["lazy"]]
    s.check("nothing in the first screen loads lazily", not early, detail="; ".join(early))
    late = [f"{i['where']} {i['cls'] or i['alt']!r} at {i['top']}px" for i in d["imgs"]
            if i["shown"] and i["top"] >= SCREEN_H and not i["lazy"]]
    s.check("everything after it does", not late, detail="; ".join(late[:6]))
    unseen = [f"{i['where']} {i['cls'] or i['alt']!r}" for i in d["imgs"] if not i["shown"] and not i["lazy"]]
    s.check("and nothing a phone does not draw is fetched (lazy, so it never asks)", not unseen,
            detail="; ".join(unseen[:6]))

    s.section("At 1100px, as it was")
    desk = real.get(DESK)
    if s.check("measured at 1100px", desk is not None):
        ds = desk["show"]
        s.check("the phone's two cards and the quotes' dots are not drawn", not ds["twoways"] and not ds["dots"],
                detail=str(ds))
        s.check("the hero's card, La Vie de Château and the comparison are",
                ds["hbook"] and ds["fullCopy"] and ds["sow"], detail=str(ds))
        s.check("the estate from the air still opens the rooms, and both photographs of the restoration show",
                ds["aerial"] and ds["restoNow"], detail=str(ds))
    return s


if __name__ == "__main__":
    print(run().report())
