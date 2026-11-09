"""The public site's margins, measured in a browser at a phone, a tablet and a desk.

9 November 2026, the owner: "double check spacing site wide for margins".
Every public page was measured at 390, 768 and 1440 for its side gutters, the
gap between one band and the next, and words at the edge of the screen. Most
of it was already even -- a 16px gutter on a phone and 24px above, the same on
every page. What was not, and what each check below now holds:

  - LA TABLE'S "NOT TAKING RESERVATIONS" PAGE HAD NO SIDE GUTTER AT ALL. An
    inline `padding: clamp(...) 0` on its .g-wrap took the wrap's gutter away,
    so every word on the page touched the edge of a phone, and on a desk the
    text stood 24px left of every other page's column.
  - WRITE TO US, ON CONTACT, SAT 8PX IN FROM EVERY OTHER LINE on a phone:
    .g-two used as the page container kept the desktop gutter where .g-wrap
    drops to 16px.
  - AN EMPTY STRIP, 84-88PX, between the Monument Historique band and the
    reassurance band on every page on a phone or a tablet. main still made
    room at its foot for the booking bar, from before those bands came after
    it.
  - A 48PX STRIP OF CREAM between the reassurance band and the footer on a
    phone, from a margin written before the reassurance band existed.
  - THE BOOKING BAR OVER THE FOOTER'S LAST LINE on a phone -- the copyright
    and the card marks -- because the footer's allowance for the bar was
    overridden at phone width; while a tablet with no bar on the page got
    120px of empty navy for one.
  - THE DATES ON /workshops COULD NOT ALL BE REACHED ON A PHONE. They are
    centred on a wide screen, and the centring followed them into the row you
    swipe, where it pushes half the overflow off the left edge -- and nothing
    scrolls to the left of the start. The first dates of the year were simply
    not there. And once they could be reached, each date card turned out to
    keep an empty column down its left side on a phone, where the photograph
    it no longer shows used to go.

None of these errored, and every page rendered. A gutter is a sum of rules
and a gap is a sum of boxes, which no reading of the stylesheet can add up --
so, like test_staff_header_on_a_phone, the real pages are rendered through the
test client and measured in headless Chrome: each page in an iframe of the
width asked for, a touch screen emulated for the phone and the tablet so every
touch rule applies as it does in a hand. The pages come from the sitemap, so a
new public page is measured the day it is added, plus La Table's closed page,
which the sitemap does not list.

What every frame is asked:

  - it lays out at the width asked, and nothing scrolls sideways;
  - no words within 12px of either edge of the screen (outside the hero, which
    sets its own type over a photograph, and outside rows that scroll);
  - every page container holding words -- .g-wrap, or .g-two standing in for
    one -- has the site's gutter on both sides: 16px on a phone, 24px above;
  - the last thing in main ends where main ends, and the reassurance band
    meets the footer: no strips;
  - where a booking bar is drawn, the footer's last line ends above it when
    the page is scrolled to the bottom;
  - every row that scrolls sideways starts at its first card;
  - a card showing no photograph has no empty column where one would go;
  - on the home page, the address is one screen down at most on a phone
    (test_house_address holds what it says and where it goes).

THE MEASURING IS CHECKED TOO. Each frame must come back the width it was asked
for, the phone and tablet ones with the touch rules in force; and a copy of a
page with the old faults put back on purpose must be reported for every one
of them, or a pass here would mean nothing.

NO BROWSER IS A FAILURE, not a skip. Set GUDANES_CHROME to run it.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from _harness import Suite, db
import _harness
from test_staff_header_on_a_phone import find_browser
from test_copy_unchanged import public_paths

m = _harness.m
ROOT = _harness.ROOT

PHONE, TABLET, DESK = 390, 768, 1440
HEIGHT = {PHONE: 844, TABLET: 1024, DESK: 900}
EDGE = 12                 # px: words closer than this to the screen's edge are at it
PHONE_GUTTER_UNTIL = 544  # px: .g-wrap's 16px gutter applies at 34rem and below


def gutter(width):
    return 16 if width <= PHONE_GUTTER_UNTIL else 24


PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  var EDGE = %d;
  var SKIP = '[aria-hidden="true"], .g-hero, .g-navwrap, dialog, [hidden], header, '
           + '.g-stickybar, .g-bookbar, script, style, template, noscript';
  var WORDY = /^(P|H1|H2|H3|H4|H5|H6|LI|DT|DD|FIGCAPTION|BLOCKQUOTE|LABEL|TD|TH|LEGEND|A|SPAN|EM|B|STRONG|SMALL|BUTTON)$/;
  function shown(el) {
    var cs = getComputedStyle(el), r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility === 'visible'
        && parseFloat(cs.opacity) > 0.05 && r.width >= 1 && r.height >= 1;
  }
  function ink(el) {
    var range = document.createRange();
    range.selectNodeContents(el);
    return [].filter.call(range.getClientRects(), function (x) { return x.width > 1 && x.height > 4; });
  }
  function scrolls(el) {
    for (var n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      var o = getComputedStyle(n).overflowX;
      if (o === 'auto' || o === 'scroll') return true;
    }
    return false;
  }
  function name(el) {
    var c = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    return el.tagName.toLowerCase() + (c ? '.' + c : '');
  }
  function words(el) { return el.textContent.replace(/\s+/g, ' ').trim().slice(0, 40); }
  function go() {
    var de = document.documentElement, vw = de.clientWidth;
    var out = {id: q.id, page: q.p, asked: +q.w, vw: vw, docW: de.scrollWidth,
               coarse: matchMedia('(pointer: coarse)').matches};

    var edge = [];
    [].forEach.call(document.querySelectorAll('main *, .g-assure *, footer *, .g-where'), function (el) {
      if (!WORDY.test(el.tagName) || el.closest(SKIP) || !shown(el) || scrolls(el)) return;
      var rs = ink(el);
      if (!rs.length) return;
      var l = Math.min.apply(null, rs.map(function (x) { return x.left; }));
      var r = vw - Math.max.apply(null, rs.map(function (x) { return x.right; }));
      if (l < EDGE || r < EDGE) edge.push(name(el) + ' "' + words(el) + '" ' + Math.round(l) + 'px/' + Math.round(r) + 'px');
    });
    out.edge = edge;

    var gutters = [];
    [].forEach.call(document.querySelectorAll('.g-wrap, .g-two'), function (el) {
      if (el.parentElement.closest('.g-wrap, .g-two') || el.closest(SKIP) || !shown(el)) return;
      if (!el.textContent.trim()) return;
      var cs = getComputedStyle(el);
      gutters.push([name(el) + ' in ' + name(el.parentElement), parseFloat(cs.paddingLeft), parseFloat(cs.paddingRight)]);
    });
    out.gutters = gutters;

    var main = document.querySelector('main'), assure = document.querySelector('.g-assure'),
        foot = document.querySelector('footer.g-footer');
    var last = null;
    [].forEach.call(main ? main.children : [], function (c) { if (shown(c)) last = c; });
    function y(v) { return v + window.scrollY; }
    out.run = {
      last: last ? name(last) : null,
      lastB: last ? y(last.getBoundingClientRect().bottom) : null,
      mainB: main ? y(main.getBoundingClientRect().bottom) : null,
      assureT: assure ? y(assure.getBoundingClientRect().top) : null,
      assureB: assure ? y(assure.getBoundingClientRect().bottom) : null,
      footT: foot ? y(foot.getBoundingClientRect().top) : null
    };

    var where = document.querySelector('.g-where');
    out.where = where ? {top: y(where.getBoundingClientRect().top), vh: window.innerHeight,
                         footT: out.run.footT} : null;

    var rows = [];
    [].forEach.call(document.querySelectorAll('main *'), function (el) {
      var o = getComputedStyle(el).overflowX;
      if ((o !== 'auto' && o !== 'scroll') || !shown(el) || el.scrollWidth <= el.clientWidth + 1) return;
      el.scrollLeft = 0;
      var start = el.getBoundingClientRect().left, first = null;
      [].forEach.call(el.children, function (c) {
        var r = c.getBoundingClientRect();
        if (r.width > 0 && (first === null || r.left < first)) first = r.left;
      });
      rows.push([name(el), first === null ? 0 : Math.round(first - start)]);
    });
    out.rows = rows;

    // A card showing no photograph must not keep the photograph's column.
    var cards = [];
    [].forEach.call(document.querySelectorAll('main .g-sit, main .g-card, main .g-wscard'), function (c) {
      if (!shown(c) || c.closest(SKIP)) return;
      if ([].some.call(c.querySelectorAll('img'), shown)) return;
      var kids = [].filter.call(c.children, function (k) {
        return shown(k) && getComputedStyle(k).position !== 'absolute';
      });
      if (!kids.length) return;
      var cs = getComputedStyle(c);
      var inner = c.getBoundingClientRect().left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
      var first = Math.min.apply(null, kids.map(function (k) { return k.getBoundingClientRect().left; }));
      if (first - inner > 2) cards.push(name(c) + ' "' + words(c) + '" ' + Math.round(first - inner) + 'px');
    });
    out.cards = cards;

    var bar =[].filter.call(document.querySelectorAll('.g-stickybar, .g-bookbar'), shown)[0];
    window.scrollTo(0, de.scrollHeight);
    setTimeout(function () {
      var base = document.querySelector('.g-footer__base');
      out.bar = bar ? {what: name(bar), top: bar.getBoundingClientRect().top,
                       lastLine: base ? base.getBoundingClientRect().bottom : null} : null;
      parent.postMessage(JSON.stringify(out), '*');
    }, 30);
  }
  // A timer, not requestAnimationFrame: Chrome runs no animation frames in an
  // iframe scrolled out of view (test_staff_header_on_a_phone found it).
  window.addEventListener('load', function () { setTimeout(go, 0); });
})();
</script>
""" % EDGE

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<script>
window.addEventListener('message', function (e) {
  var pre = document.createElement('pre');
  pre.className = 'r';
  pre.textContent = e.data;
  document.body.appendChild(pre);
});
</script>
%s
</body></html>"""

# Put back, on a copy of one page, every fault this suite was written for. The
# measuring has to see each of them, or its passes say nothing.
OLD_FAULTS = """<style>
.g main > .g-sec > .g-wrap{ padding-left: 0 !important; padding-right: 0 !important; }
@media (max-width: 61.99rem){ .g main{ padding-bottom: 88px !important; } .g .g-footer{ padding-bottom: 24px !important; } }
.g .g-footer{ margin-top: 48px !important; }
</style>"""


def _localise(page):
    """The page with its stylesheets read from the folder, and nothing from the network."""
    def link(match):
        tag = match.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        if not href or "stylesheet" not in tag:
            return tag if "preconnect" not in tag else ""
        url = href.group(1)
        if url.startswith("/static/"):
            return '<link rel="stylesheet" href="%s">' % url[len("/static/"):].split("?")[0]
        return ""
    return re.sub(r"<link\b[^>]*>", link, page)


def _render(anon, paths):
    pages = []
    for path in paths:
        r = anon.get(path)
        if r.status_code != 200 or "text/html" not in (r.content_type or ""):
            continue
        body = r.get_data(as_text=True)
        if '<footer class="g-footer">' not in body:
            continue              # not a page drawn by public_base
        pages.append((path, _localise(body)))
    return pages


def _measure(browser, pages, widths, coarse, workdir, tag):
    cells, n = [], 0
    for i, (path, body) in enumerate(pages):
        name = "%s%d.html" % (tag, i)
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
            fh.write(body.replace("</body>", PROBE + "</body>", 1))
        for w in widths:
            cells.append('<iframe src="%s#id=%d&w=%d&p=%s" style="display:block;border:0;'
                         'width:%dpx;height:%dpx"></iframe>'
                         % (name, n, w, path.replace("&", "%26"), w, HEIGHT[w]))
            n += 1
    wrapper = os.path.join(workdir, "%s.html" % tag)
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER % "\n".join(cells))
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--user-data-dir=" + os.path.join(workdir, "profile-" + tag),
           "--hide-scrollbars", "--allow-file-access-from-files",
           "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=90000", "--dump-dom"]
    if coarse:
        cmd.append("--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
                   "primaryHoverType=1,availableHoverTypes=1")
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    cmd.append("file:///" + wrapper.replace("\\", "/").lstrip("/"))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    seen = []
    for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        seen.append(json.loads(htmllib.unescape(blob)))
    return seen, n


def _faults(d):
    """Everything wrong with one frame, as sentences: empty when it is right."""
    w = d["asked"]
    out = []
    if d["vw"] != w:
        out.append("laid out at %dpx, not %d" % (d["vw"], w))
    if d["docW"] > d["vw"] + 1:
        out.append("scrolls sideways (%dpx wide)" % d["docW"])
    if d["edge"]:
        out.append("words at the edge: " + "; ".join(d["edge"][:3]))
    g = gutter(w)
    off = ["%s %g/%g" % (n, l, r) for n, l, r in d["gutters"] if l != g or r != g]
    if off:
        out.append("gutter not %dpx: %s" % (g, "; ".join(off[:3])))
    run = d["run"]
    if run["lastB"] is not None and run["mainB"] is not None and run["mainB"] - run["lastB"] > 1:
        out.append("%dpx empty under %s at the foot of main" % (run["mainB"] - run["lastB"], run["last"]))
    if run["assureT"] is not None and run["mainB"] is not None and abs(run["assureT"] - run["mainB"]) > 1:
        out.append("%dpx between main and the reassurance band" % (run["assureT"] - run["mainB"]))
    if run["assureB"] is not None and run["footT"] is not None and abs(run["footT"] - run["assureB"]) > 1:
        out.append("%dpx between the reassurance band and the footer" % (run["footT"] - run["assureB"]))
    bar = d.get("bar")
    if bar and bar["lastLine"] is not None and bar["lastLine"] > bar["top"] + 0.5:
        out.append("%s covers the footer's last line by %dpx" % (bar["what"], bar["lastLine"] - bar["top"]))
    lost = ["%s starts %dpx left of its row" % (n, -x) for n, x in d["rows"] if x < -1]
    if lost:
        out.append("; ".join(lost))
    if d.get("cards"):
        out.append("an empty column beside the words: " + "; ".join(d["cards"][:3]))
    return out


def run():
    s = Suite("Margins on the public site, at a phone, a tablet and a desk")
    browser = find_browser()
    s.section("A browser to measure in")
    s.check("headless Chrome, Chromium or Edge is on this machine", bool(browser),
            detail="set GUDANES_CHROME to its path; a check that quietly does "
                   "not run reads as cover")
    if not browser:
        return s

    anon = m.app.test_client()
    workdir = tempfile.mkdtemp(prefix="gudanes_margins_")
    conn = db()
    was_enabled = conn.execute("SELECT enabled FROM restaurant_settings WHERE id = 1").fetchone()
    conn.close()
    was_live = m.SITE_IS_LIVE
    try:
        for css in ("gudanes.css", "gudanes-pictures.css"):
            src = os.path.join(ROOT, "static", css)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(workdir, css))

        m.SITE_IS_LIVE = True     # the sitemap as the public is given it
        paths = public_paths(anon)
        pages = _render(anon, paths)
        # La Table's page for while it takes no bookings, which nothing links
        # to from the sitemap. It is what /restaurant/book answers while the
        # dining room is switched off.
        conn = db()
        conn.execute("UPDATE restaurant_settings SET enabled = 0 WHERE id = 1")
        conn.commit()
        conn.close()
        closed = _render(anon, ["/restaurant/book"])
        s.check("La Table's closed page is among those measured",
                bool(closed) and "Not Taking Reservations" in closed[0][1],
                detail="it is the page the owner's margins brief found with no gutter")
        pages += closed
        s.check("the sitemap gives a full set of public pages to measure",
                len(pages) >= 15, detail="%d pages: %s" % (len(pages), [p for p, _ in pages]))

        # The home page for the control: it draws the booking bar, so the bar
        # fault can be put back and seen as well as the others.
        control = [p for p in pages if p[0] == "/"] or pages[:1]
        doctored = [("control:" + control[0][0],
                     control[0][1].replace("</head>", OLD_FAULTS + "</head>", 1))]

        touch, n_touch = _measure(browser, pages + doctored, (PHONE, TABLET), True, workdir, "touch")
        desk, n_desk = _measure(browser, pages, (DESK,), False, workdir, "desk")
        frames = touch + desk

        s.section("The measuring")
        s.check("every frame reported", len(frames) == n_touch + n_desk,
                detail="%d of %d" % (len(frames), n_touch + n_desk))
        s.check("each laid out at the width it was asked for",
                all(d["vw"] == d["asked"] for d in frames),
                detail=str([(d["page"], d["asked"], d["vw"]) for d in frames if d["vw"] != d["asked"]][:5]))
        s.check("the phone and the tablet with the touch rules in force",
                all(d["coarse"] for d in touch) and not any(d["coarse"] for d in desk))
        ctl = [d for d in touch if d["page"].startswith("control:") and d["asked"] == PHONE]
        said = " | ".join(_faults(ctl[0])) if ctl else ""
        s.check("a page with the old faults put back is reported for the missing gutter",
                "gutter not 16px" in said and "words at the edge" in said, detail=said or "no frame")
        s.check("for the empty strip under main",
                "empty under" in said and "at the foot of main" in said, detail=said)
        s.check("for the strip over the footer",
                "between the reassurance band and the footer" in said, detail=said)
        s.check("and for the booking bar over the footer's last line",
                "covers the footer's last line" in said, detail=said)

        real = [d for d in frames if not d["page"].startswith("control:")]
        for width, label in ((PHONE, "on a phone (390px)"), (TABLET, "on a tablet (768px)"),
                             (DESK, "on a desk (1440px)")):
            s.section("Every public page " + label)
            these = [d for d in real if d["asked"] == width]
            s.check("all %d pages measured" % len(pages), len(these) == len(pages),
                    detail="%d frames" % len(these))

            def each(test):
                return ["%s: %s" % (d["page"], f) for d in these for f in _faults(d) if test(f)]

            bad = each(lambda f: "scrolls sideways" in f or "laid out at" in f)
            s.check("nothing scrolls sideways", not bad, detail=" | ".join(bad[:4]))
            bad = each(lambda f: "words at the edge" in f)
            s.check("no words within %dpx of either edge" % EDGE, not bad, detail=" | ".join(bad[:4]))
            bad = each(lambda f: f.startswith("gutter not"))
            s.check("every page container keeps the %dpx gutter on both sides" % gutter(width),
                    not bad, detail=" | ".join(bad[:4]))
            bad = each(lambda f: "empty under" in f or "between main" in f
                       or "between the reassurance band" in f)
            s.check("the Monument band, the reassurance band and the footer meet with no strip",
                    not bad, detail=" | ".join(bad[:4]))
            bad = each(lambda f: "covers the footer's last line" in f)
            s.check("no booking bar over the footer's last line", not bad, detail=" | ".join(bad[:4]))
            bad = each(lambda f: "left of its row" in f)
            s.check("every row you swipe starts at its first card", not bad, detail=" | ".join(bad[:4]))
            bad = each(lambda f: "an empty column" in f)
            s.check("a card showing no photograph gives its words the whole card",
                    not bad, detail=" | ".join(bad[:4]))

        s.section("The address, high on the home page")
        home = [d for d in real if d["page"] == "/" and d["asked"] == PHONE]
        where = home[0]["where"] if home else None
        s.check("the home page draws the address line", bool(where), detail=str(home[:1]))
        if where:
            s.check("within one screen of the top on a phone",
                    where["top"] <= where["vh"] * 1.25,
                    detail="at %dpx on a %dpx screen; the footer starts at %dpx"
                           % (where["top"], where["vh"], where["footT"] or 0))
        bars = [d for d in real if d.get("bar")]
        s.check("and the bar check met a bar at all", bool(bars),
                detail="if no page drew one, 'no bar over the footer' is not a finding")
    finally:
        m.SITE_IS_LIVE = was_live
        if was_enabled is not None:
            conn = db()
            conn.execute("UPDATE restaurant_settings SET enabled = ? WHERE id = 1",
                         (was_enabled["enabled"],))
            conn.commit()
            conn.close()
        shutil.rmtree(workdir, ignore_errors=True)
    return s
