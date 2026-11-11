"""The public pages measured in a browser: how long, how sharp, and on a phone.

9 November 2026, evening: the owner's second review of the site
(BRIEF-site-review-2-9-nov.md) measured the live pages at 1498px and found
them three times too long -- the home page seventeen screens, /book and
/workshops twenty-five, the gallery forty-one -- with photographs drawn wider
than the files they came from, and nothing measuring a public page on a phone
at all (test_staff_header_on_a_phone holds the staff side). A length, a
drawn width and a fit to a screen are sums no reading of the templates can do,
so, like that suite, the real pages are rendered through the test client,
their stylesheets read from the folder, and measured in headless Chrome. No
browser is a failure, not a skip: a check that quietly does not run reads as
cover.

WHAT IS HELD

  - LENGTH, at 1400px wide. No public page over 9,000px, or 12,000 for
    /gallery and /whats-on, whose length is their contents. Fails when a
    section is added. The pages still over it after the owner's cuts are named
    in KNOWN_LONG, each with the height it may not grow past, and the list is
    checked both ways like COVERAGE_KNOWN_GAPS: a page that grows reds the run,
    and so does one that has come under the budget and is still on the list.
  - SHARPNESS, at 1400px. No house photograph (one pic() sizes from the file)
    drawn wider than 1.25 times the widest file it has. And every lazy one asks
    for the width it is drawn at (sizes="auto, ..."): the components guessed,
    and a retreat photograph said 389px and was drawn at 686, so the browser
    took a file half the width it needed and the page looked soft.
  - THE PHONE, at 320, 375 and 390px with a touch screen emulated, on the home
    page, /book, /workshops, a room's page and a retreat's: nothing past the
    right edge, no tap target under 44px, and no words under 12px.

Each reader is proved first on a copy of the home page doctored to fail it.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from _harness import Suite
import _harness
from test_staff_header_on_a_phone import find_browser
from test_copy_unchanged import public_paths
from test_public_margins import _render

m = _harness.m
ROOT = _harness.ROOT

DESK = 1400
PHONES = (320, 375, 390)
BUDGET = 9000
LONG_BUDGET = {"/gallery": 12000, "/whats-on": 12000}
SHARP = 1.25
TAP = 44
SMALLEST = 12

# Still over the budget after the owner's cuts of 9 Nov 2026, with the height
# measured that evening (fresh test database, 1400px) and a little room. Each
# is a page the brief kept long on purpose or did not touch: /restoration's
# chapters, the retreats' day-by-day, the rooms' cards on /book, the room
# pages' booking form, the history the brief says to leave alone. Keyed by
# address, with a room or a retreat by its kind, since the ids differ between
# databases. Take a page off when it comes under the budget; the run says so.
KNOWN_LONG = {
    "/restoration": 15100,
    "/events/weddings": 12700,
    # 11 Nov: +600 for the bathrooms' own section, which the owner asked for ("they are a real feature").
    "/book": 13100,
    "/workshops": 11600,
    "/workshops/<id>": 11900,
    "/history": 9900,
    "/book/<id>": 9600,
}


def kind_of(path):
    return re.sub(r"/\d+$", "/<id>", path)


def budget(path):
    return LONG_BUDGET.get(path, BUDGET)


PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function shown(el) {
    var cs = getComputedStyle(el), r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility === 'visible' && parseFloat(cs.opacity) > 0.05
        && r.width >= 1 && r.height >= 1;
  }
  // A component that scrolls or clips its own row on purpose (a slider's track)
  // excuses what is past the edge inside it. The page-wide clip on html, body,
  // main, the header and the footer does not: it turns a too-wide element into
  // one cut off at the edge, which is the same fault without the scroll bar.
  function clipped(el) {
    for (var n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      if (/^(MAIN|HEADER|FOOTER)$/.test(n.tagName)) continue;
      var o = getComputedStyle(n).overflowX;
      if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true;
    }
    return false;
  }
  function skip(el) {
    return el.closest('[aria-hidden="true"], dialog, [hidden], .g-navwrap, .g-panel-overlay, template, .g-skip');
  }
  function name(el) {
    var c = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
    return el.tagName.toLowerCase() + (c ? '.' + c : '');
  }
  function tag_is_tick(el) { return el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio'); }
  function words(el) { return (el.textContent || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 30); }
  function go() {
    var de = document.documentElement, vw = de.clientWidth, out = {id: q.id, page: q.p, w: +q.w, vw: vw,
      h: de.scrollHeight, docW: de.scrollWidth};
    if (q.mode === 'desk') {
      out.soft = []; out.guess = [];
      [].forEach.call(document.querySelectorAll('img[srcset]'), function (i) {
        if (skip(i) || !shown(i) || i.getAttribute('srcset').indexOf('/pic/') < 0) return;
        var widest = Math.max.apply(null, i.getAttribute('srcset').split(',').map(function (c) {
          var mo = c.trim().match(/\s(\d+)w$/); return mo ? +mo[1] : 0; }));
        var drawn = i.getBoundingClientRect().width;
        if (widest && drawn > widest * %(sharp)s)
          out.soft.push(i.getAttribute('alt') + ' drawn ' + Math.round(drawn) + 'px from a ' + widest + 'px file');
        if (i.loading === 'lazy' && !/^auto\b/.test(i.getAttribute('sizes') || ''))
          out.guess.push(i.getAttribute('alt') + ' sizes="' + i.getAttribute('sizes') + '"');
      });
    } else {
      out.wide = []; out.small = []; out.tiny = [];
      [].forEach.call(document.querySelectorAll('body *'), function (el) {
        if (skip(el) || !shown(el)) return;
        var r = el.getBoundingClientRect(), cs = getComputedStyle(el);
        // A box or a dot to tick is aimed at through its label, which is its target.
        if (tag_is_tick(el)) { var lab = el.closest('label') || (el.id && document.querySelector('label[for="' + el.id + '"]'));
          if (lab) r = lab.getBoundingClientRect(); }
        if (r.right > vw + 1 && !clipped(el) && cs.position !== 'fixed')
          out.wide.push(name(el) + ' "' + words(el) + '" ends at ' + Math.round(r.right));
        var tag = el.tagName;
        var tappable = tag === 'BUTTON' || tag === 'SELECT' || tag === 'SUMMARY'
          || (tag === 'INPUT' && el.type !== 'hidden') || (tag === 'A' && el.hasAttribute('href'));
        // A link inside a line of words is the line's, and WCAG exempts it: it is the
        // words around it that a finger aims at.
        var inline = tag === 'A' && cs.display === 'inline';
        if (tappable && !inline && (r.width < %(tap)s || r.height < %(tap)s) && !el.closest('.g-stickybar.is-waiting'))
          out.small.push(name(el) + ' "' + words(el) + '" ' + Math.round(r.width) + 'x' + Math.round(r.height));
        var own = [].some.call(el.childNodes, function (n) { return n.nodeType === 3 && n.textContent.trim(); });
        if (own && parseFloat(cs.fontSize) < %(small)s)
          out.tiny.push(name(el) + ' "' + words(el) + '" ' + cs.fontSize);
      });
    }
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 50); });
})();
</script>
""" % {"sharp": SHARP, "tap": TAP, "small": SMALLEST}

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<script>
window.addEventListener('message', function (e) {
  var pre = document.createElement('pre'); pre.className = 'r'; pre.textContent = e.data;
  document.body.appendChild(pre);
});
</script>
%s
</body></html>"""

# Copies of the home page, each with one fault the readers must find.
TOO_LONG = '<div style="height:12000px"></div></main>'
TOO_SOFT = ('<p><img src="/pic/480/x.jpg" srcset="/pic/480/x.jpg 480w" sizes="auto, 480px" loading="lazy" '
            'alt="ZZ soft" width="480" height="320" style="width:1100px;height:auto"></p>'
            '<p><img src="/pic/480/y.jpg" srcset="/pic/480/y.jpg 480w" sizes="30vw" loading="lazy" '
            'alt="ZZ guess" width="480" height="320" style="width:200px"></p></main>')
PHONE_FAULTS = ('<p><button type="button" style="width:20px;height:20px;min-height:0;min-width:0;padding:0">x</button></p>'
                '<p style="font-size:10px">ZZ tiny words</p></main>'
                '<div style="width:600px;height:20px" class="zz-wide"></div>')


def _measure(browser, pages, widths, mode, coarse, workdir, tag):
    cells, n = [], 0
    for i, (path, body) in enumerate(pages):
        name = "%s%d.html" % (tag, i)
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
            fh.write(body.replace("</body>", PROBE + "</body>", 1))
        for w in widths:
            cells.append('<iframe src="%s#id=%d&w=%d&mode=%s&p=%s" style="display:block;border:0;'
                         'width:%dpx;height:%dpx"></iframe>'
                         % (name, n, w, mode, path.replace("&", "%26"), w, 900 if mode == "desk" else 844))
            n += 1
    wrapper = os.path.join(workdir, "%s.html" % tag)
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER % "\n".join(cells))
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--user-data-dir=" + os.path.join(workdir, "profile-" + tag),
           "--hide-scrollbars", "--allow-file-access-from-files", "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=90000", "--dump-dom"]
    if coarse:
        cmd.append("--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
                   "primaryHoverType=1,availableHoverTypes=1")
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    cmd.append("file:///" + wrapper.replace("\\", "/").lstrip("/"))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    seen = [json.loads(htmllib.unescape(b)) for b in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S)]
    return seen, n


def run():
    s = Suite("The public pages measured: length, sharpness, and on a phone")
    browser = find_browser()
    s.section("A browser to measure in")
    s.check("headless Chrome, Chromium or Edge is on this machine", bool(browser),
            detail="set GUDANES_CHROME to its path; a check that quietly does not run reads as cover")
    if not browser:
        return s
    anon = m.app.test_client()
    workdir = tempfile.mkdtemp(prefix="gudanes_measured_")
    was_live = m.SITE_IS_LIVE
    try:
        for css in ("gudanes.css", "gudanes-pictures.css"):
            shutil.copy(os.path.join(ROOT, "static", css), os.path.join(workdir, css))
        m.SITE_IS_LIVE = True
        pages = _render(anon, public_paths(anon))
        _run(s, browser, pages, workdir)
    finally:
        m.SITE_IS_LIVE = was_live
        shutil.rmtree(workdir, ignore_errors=True)
    return s


def _run(s, browser, pages, workdir):
    by_path = dict(pages)
    s.check("the sitemap gives a full set of public pages", len(pages) >= 20, detail=str(len(pages)))
    home = by_path.get("/", "")

    # ---- length and sharpness, at a desk -------------------------------
    doctored = [("control:long", home.replace("</main>", TOO_LONG, 1)),
                ("control:soft", home.replace("</main>", TOO_SOFT, 1))]
    desk, n = _measure(browser, pages + doctored, (DESK,), "desk", False, workdir, "desk")
    got = {d["page"]: d for d in desk}
    s.section("Every page measured at %dpx" % DESK)
    s.check("every page came back from the browser", len(desk) == n, detail="%d of %d" % (len(desk), n))

    s.section("Length")
    long_c = got.get("control:long", {})
    s.check("the reader finds a page made 12,000px longer", long_c.get("h", 0) > BUDGET,
            detail=str(long_c.get("h")))
    over, grown, mended = [], [], []
    tallest = {}
    for path, _body in pages:
        h = got.get(path, {}).get("h")
        if h is None:
            continue
        k = kind_of(path)
        tallest[k] = max(tallest.get(k, 0), h)
        ceiling = KNOWN_LONG.get(k)
        if h > budget(path) and ceiling is None:
            over.append("%s %dpx" % (path, h))
        if ceiling is not None and h > ceiling:
            grown.append("%s %dpx, may not pass %d" % (path, h, ceiling))
    for k, ceiling in KNOWN_LONG.items():
        if k in tallest and tallest[k] <= budget(k):
            mended.append("%s is %dpx now" % (k, tallest[k]))
    s.check("no public page is over its budget (%d, or 12,000 for the gallery and What's On)" % BUDGET,
            not over, detail="; ".join(over))
    s.check("no page on the known-long list has grown", not grown, detail="; ".join(grown))
    s.check("no page on that list has come under the budget and is still on it", not mended,
            detail="take off KNOWN_LONG: " + "; ".join(mended))
    s.check("the home page is under it, from seventeen screens", 0 < got.get("/", {}).get("h", 0) <= BUDGET,
            detail=str(got.get("/", {}).get("h")))
    s.check("and the gallery, from forty-one", 0 < got.get("/gallery", {}).get("h", 0) <= LONG_BUDGET["/gallery"],
            detail=str(got.get("/gallery", {}).get("h")))

    s.section("Sharpness")
    soft_c = got.get("control:soft", {})
    s.check("the reader finds a photograph drawn wider than its file",
            any("ZZ soft" in x for x in soft_c.get("soft", [])), detail=str(soft_c.get("soft")))
    s.check("and one that guesses its width", any("ZZ guess" in x for x in soft_c.get("guess", [])),
            detail=str(soft_c.get("guess")))
    soft = {p: d["soft"] for p, d in got.items() if not p.startswith("control:") and d.get("soft")}
    guess = {p: d["guess"] for p, d in got.items() if not p.startswith("control:") and d.get("guess")}
    s.check("no house photograph is drawn more than %.2f times its widest file" % SHARP, not soft,
            detail=str(soft)[:600])
    s.check("every lazy house photograph asks for the width it is drawn at", not guess, detail=str(guess)[:600])

    # ---- on a phone ------------------------------------------------------
    wanted = ["/", "/book", "/workshops"]
    wanted += sorted(p for p in by_path if re.fullmatch(r"/book/\d+", p))[:1]
    wanted += sorted(p for p in by_path if re.fullmatch(r"/workshops/\d+", p))[:1]
    phone_pages = [(p, by_path[p]) for p in wanted if p in by_path]
    s.section("On a phone, at %s px" % ", ".join(str(w) for w in PHONES))
    s.check("the five pages the brief names are there to measure", len(phone_pages) == 5,
            detail=str([p for p, _ in phone_pages]))
    doctored = [("control:phone", home.replace("</main>", PHONE_FAULTS, 1))]
    phone, n = _measure(browser, phone_pages + doctored, PHONES, "phone", True, workdir, "phone")
    s.check("every frame came back from the browser", len(phone) == n, detail="%d of %d" % (len(phone), n))
    ctrl = [d for d in phone if d["page"] == "control:phone"]
    s.check("the reader finds a block past the edge", all(any("zz-wide" in x for x in d["wide"]) for d in ctrl) and ctrl)
    s.check("a 20px button", all(any("20x20" in x for x in d["small"]) for d in ctrl) and ctrl)
    s.check("and words at 10px", all(any("ZZ tiny" in x for x in d["tiny"]) for d in ctrl) and ctrl)
    for d in sorted((d for d in phone if d["page"] != "control:phone"), key=lambda d: (d["page"], d["w"])):
        where = "%s at %dpx" % (d["page"], d["w"])
        s.check("%s: nothing scrolls sideways" % where, d["docW"] <= d["vw"] + 1 and not d["wide"],
                detail=("page %dpx wide; " % d["docW"]) + "; ".join(d["wide"][:6]))
        s.check("%s: every tap target is %dpx or more" % (where, TAP), not d["small"],
                detail="; ".join(d["small"][:8]))
        s.check("%s: no words under %dpx" % (where, SMALLEST), not d["tiny"], detail="; ".join(d["tiny"][:8]))


if __name__ == "__main__":
    print(run().report())
