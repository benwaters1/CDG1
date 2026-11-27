"""The footer on a phone: everything still in it, and only what is asked for drawn.

WHAT WENT WRONG. After the home page on a phone (26 November 2026) the home
page was 7,104px at 375 x 812, and 1,955px of that was the footer: the same
1,955px under every public page. Most of it was Visit and Help, twelve and
seven links at 45px apiece, side by side, 579px of links a phone had not asked
for and the menu already holds. The owner said yes to a shorter footer on a
phone the next morning.

WHAT IT DOES NOW, under 600px (gudanes.css, THE FOOTER ON A PHONE; the
buttons are made by the script under the footer in public_base.html):

- Visit and Help are each a button over its own list, shut until pressed.
  The heading is still the heading: the button is inside the h2. Each says
  whether its list is open and which list it opens.
- With no script, every list shows, as it always did.
- If the screen widens past 600px (a phone turned on its side) the buttons
  are taken away and the lists show; narrowed again, they come back shut.
- The newsletter's field and its Subscribe button share one row.
- The house's drawing is drawn at 13.5rem, not the phone's whole width, and
  its three dates are stacked without the rule that was left at the start of
  the second and third lines.

Nothing is taken out: every link the footer has at a desk is in the page on
a phone. From 600px up none of this applies.

WHAT IS MEASURED, in headless Chrome as a touch screen at 2x (the plumbing of
test_staff_header_on_a_phone, and test_home_on_a_phone's stand-in copies of
the photographs): the footer's height on five pages at 375, nothing past the
screen's edge at 375 and 320, the buttons pressed and the lists read back,
the frame widened and narrowed, and the same page at 1100.

THE MEASURING IS CHECKED TOO, by four pages it must call wrong: the script
taken out (which is also the no-script page, so it checks the fallback), the
buttons drawn too small to tap, something 640px wide in the footer at 320,
and a list that never opens. No browser is a failure, not a skip; set
GUDANES_CHROME.
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
import test_staff_header_on_a_phone as phone
import test_home_on_a_phone as home

m = _harness.m

SCREEN_H = 812
PHONE = 375
NARROW = 320
WIDE = 700
DESK = 1100
PAGES = ("/", "/book", "/workshops", "/contact", "/history")
# The footer at 375, on any public page: one and a half screens and a little. It was 1,955px on 27 Nov 2026,
# 1,224px after; 72px of the home page's is the room left under it for the bar that follows a guest down the page.
FOOT_LIMIT = 1300
# How wide the drawing may be drawn on a phone (13.5rem).
CREST_MAX = 216

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
    return {top: Math.round(r.top + scrollY), left: Math.round(r.left), right: Math.round(r.right),
            w: Math.round(r.width), h: Math.round(r.height)};
  }
  function lists() {
    var grid = document.querySelector('.g-footer__grid');
    return grid ? [].filter.call(grid.children, function (c) {
      var h = c.firstElementChild;
      return h && h.tagName === 'H2' && c.querySelector('ul');
    }) : [];
  }
  function state() {
    var f = document.querySelector('footer.g-footer'), de = document.documentElement;
    var s = {docH: de.scrollHeight, docW: de.scrollWidth, vw: de.clientWidth, foot: f ? box(f) : null};
    s.cols = lists().map(function (c) {
      var h = c.firstElementChild, b = h.querySelector('button.g-footer__tog'), ul = c.querySelector('ul');
      var links = c.querySelectorAll('ul a');
      return {name: h.textContent.trim(), h2: h.tagName,
              button: b ? {h: box(b).h, w: box(b).w, expanded: b.getAttribute('aria-expanded'),
                           controlsOk: !!b.getAttribute('aria-controls')
                                       && document.getElementById(b.getAttribute('aria-controls')) === ul} : null,
              links: links.length, drawn: [].filter.call(links, drawn).length,
              short: [].filter.call(links, function (a) { return drawn(a) && box(a).h < 44; }).length};
    });
    s.links = f ? f.querySelectorAll('a').length : 0;
    /* Anything placed wholly off the left of the screen is hidden that way on purpose (the label a screen reader
       reads, the field only a robot fills), so it is not "past the edge"; anything reaching over either edge is. */
    s.past = [];
    if (f) f.querySelectorAll('*').forEach(function (el) {
      if (!drawn(el)) return;
      var r = el.getBoundingClientRect();
      if (r.right <= 0) return;
      if (r.right > de.clientWidth + 1 || r.left < -1)
        s.past.push(String(el.className || el.tagName).split(' ')[0] + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
    });
    s.past = s.past.slice(0, 5);
    var inp = f && f.querySelector('.g-news--footer input[type=email]');
    var btn = f && f.querySelector('.g-news--footer button[type=submit]');
    s.news = inp && btn ? {inp: box(inp), btn: box(btn)} : null;
    var img = f && f.querySelector('.g-crest--foot img');
    s.crestW = img ? box(img).w : null;
    s.dateRules = f ? [].filter.call(f.querySelectorAll('.g-crest--foot .g-crest__date'), function (d) {
      return parseFloat(getComputedStyle(d).borderLeftWidth) > 0;
    }).length : null;
    return s;
  }
  function tog(i) { var c = lists()[i]; return c ? c.querySelector('h2 > button.g-footer__tog') : null; }
  var resized = [];
  window.addEventListener('resize', function () {
    resized.push(innerWidth + (matchMedia('(max-width: 599px)').matches ? ' phone' : ' wide'));
  });
  async function go() {
    var out = {id: q.id, asked: +q.w, dpr: devicePixelRatio, coarse: matchMedia('(pointer: coarse)').matches};
    out.shut = state();
    if (tog(0)) {
      tog(0).click(); await sleep(250); out.open0 = state();
      if (tog(1)) { tog(1).click(); await sleep(250); out.open1 = state(); tog(1).click(); await sleep(100); }
      tog(0).click(); await sleep(250); out.shutAgain = state();
    }
    /* One turn of the screen per frame, read once the frame has been told of it. Headless Chrome tells a frame of
       its first resize and not reliably of a second, so widening and narrowing are two frames, not one frame turned
       twice: one opens as a phone and is widened, the other opens wide and is narrowed to a phone. */
    if (q.turn) {
      parent.postMessage(JSON.stringify({resize: +q.id, w: +q.turn}), '*');
      for (var t = 0; t < 50 && !resized.length; t++) await sleep(100);
      await sleep(200);
      out.turned = state();
      out.resized = resized;
      var t0 = tog(0);
      if (t0) { t0.click(); await sleep(250); out.turnedOpen = state(); }
    }
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 600); });
})();
</script>
"""

# The frames ask to be widened and narrowed again; anything else they post is their result.
WRAPPER_HEAD = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<script>
window.addEventListener('message', function (e) {
  var d = JSON.parse(e.data);
  if (d.resize !== undefined) {
    document.querySelectorAll('iframe')[d.resize].style.width = d.w + 'px';
    return;
  }
  var pre = document.createElement('pre');
  pre.className = 'r';
  pre.textContent = e.data;
  document.body.appendChild(pre);
});
</script>
"""


def measure(browser, frames, workdir):
    """Each frame at 2x and a screen tall; the frames say when they are done."""
    cells = [f'<iframe src="page{f["page"]}.html#id={i}&w={f["width"]}{"&turn=%d" % f["turn"] if f.get("turn") else ""}" '
             f'style="display:block;border:0;width:{f["width"]}px;height:{SCREEN_H}px"></iframe>'
             for i, f in enumerate(frames)]
    wrapper = os.path.join(workdir, "footers.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER_HEAD + "\n".join(cells) + "\n</body></html>")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--user-data-dir=" + os.path.join(workdir, "profile"), "--hide-scrollbars",
           "--force-device-scale-factor=2",
           "--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
           "primaryHoverType=1,availableHoverTypes=1",
           "--allow-file-access-from-files",
           "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=60000", "--dump-dom", home._file_url(wrapper)]
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
def _no_script(page):
    """The page with the script that makes the buttons taken out: the no-script page."""
    mark = page.find("document.querySelector('.g-footer__grid')")
    start = page.rfind("<script", 0, mark)      # it arrives as <script nonce="...">
    end = page.find("</script>", mark)
    if mark < 0 or start < 0 or end < 0:
        return page
    return page[:start] + page[end + len("</script>"):]


SMALL = "<style>.g .g-footer .g-footer__tog{ min-height: 0 !important; height: 30px !important; }</style>"
SHUT_FAST = "<style>.g .g-footer__grid > div > ul{ display: none !important; }</style>"
SPILL = '<div style="width:640px;height:2px"></div>'


def run():
    s = Suite("the footer on a phone")

    s.section("The measuring")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found",
                   browser, detail="install Chrome or Chromium, or set GUDANES_CHROME "
                                   "to one -- this suite measures and cannot without one"):
        return s

    client = m.app.test_client()
    # The stand-in copies stay until the browser has measured, and go in the reverse order they came: the pages
    # share photographs (the logo on every one), so a later page's undo puts back an earlier page's stand-in.
    undos, pages = [], []
    workdir = tempfile.mkdtemp(prefix="gudanes_footer_")
    try:
        for path in PAGES:
            first = client.get(path)
            if not s.check(f"{path} renders", first.status_code == 200, first, detail=f"status {first.status_code}"):
                return s
            undos.append(home.stand_in_mirror(first.get_data(as_text=True))[1])
            page = phone.standalone(client.get(path).get_data(as_text=True))
            if not s.check(f"{path} can be fitted with the probe", phone.PROBE in page):
                return s
            pages.append(home.point_at_files(page.replace(phone.PROBE, PROBE)))
        made = "b.className = 'g-footer__tog'"       # the probe looks for the grid too; only the script makes these
        s.check("the home page carries the script that makes the buttons", pages[0].count(made) == 1)
        base = pages[0]
        controls = [_no_script(base),
                    base.replace("</head>", SMALL + "</head>", 1),
                    re.sub(r'<footer class="g-footer">', lambda t: t.group(0) + SPILL, base, count=1),
                    base.replace("</head>", SHUT_FAST + "</head>", 1)]
        s.check("the controls each changed the page", all(c != base for c in controls)
                and made not in controls[0] and len(controls[0]) < len(base),
                detail=str([c != base for c in controls]))
        pages += controls
        n = len(PAGES)
        frames = [{"page": 0, "width": PHONE, "turn": WIDE}, {"page": 0, "width": NARROW}]
        frames += [{"page": i, "width": PHONE} for i in range(1, n)]
        frames += [{"page": 0, "width": DESK}, {"page": 0, "width": WIDE, "turn": PHONE}]
        frames += [{"page": n, "width": PHONE, "control": "no script"},
                   {"page": n + 1, "width": PHONE, "control": "small"},
                   {"page": n + 2, "width": NARROW, "control": "spill"},
                   {"page": n + 3, "width": PHONE, "control": "never opens"}]
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
        for undo in reversed(undos):
            undo()

    missing = [i for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    got = [(f, seen[i]) for i, f in enumerate(frames) if i in seen]
    wrong = [f"{f['width']}: frame {d['shut']['vw']}px, coarse {d['coarse']}, {d['dpr']}x"
             for f, d in got if d["shut"]["vw"] != f["width"] or not d["coarse"] or d["dpr"] != 2]
    s.check("each frame is a 2x touch screen at the width asked for", not wrong, detail="; ".join(wrong))
    ctl = {f["control"]: d for f, d in got if f.get("control")}
    real = [(f, d) for f, d in got if not f.get("control")]

    def by(path, width):
        for f, d in real:
            if PAGES[f["page"]] == path and f["width"] == width:
                return d
        return None

    def foot_h(d):
        return d["shut"]["foot"]["h"] if d and d["shut"]["foot"] else 0

    def short_buttons(st):
        return [c["name"] for c in st["cols"] if not c["button"] or c["button"]["h"] < 44]

    s.section("The controls: four pages it must call wrong")
    if "no script" in ctl:
        st = ctl["no script"]["shut"]
        s.check("the footer with every list drawn is reported too long", foot_h(ctl["no script"]) > FOOT_LIMIT,
                detail=f"{foot_h(ctl['no script'])}px")
        s.check("and with no script every Visit and Help link shows, as it always did",
                st["cols"] and all(c["drawn"] == c["links"] > 0 and not c["button"] for c in st["cols"]),
                detail=str(st["cols"]))
    if "small" in ctl:
        s.check("buttons drawn 30px tall are reported too small to tap",
                bool(short_buttons(ctl["small"]["shut"])), detail=str(ctl["small"]["shut"]["cols"]))
    if "spill" in ctl:
        s.check("something 640px wide in the footer at 320 is reported past the edge",
                bool(ctl["spill"]["shut"]["past"]), detail=str(ctl["spill"]["shut"]["past"]))
    if "never opens" in ctl:
        o = ctl["never opens"].get("open0")
        s.check("a list that does not open when pressed is reported",
                bool(o) and o["cols"] and o["cols"][0]["drawn"] < o["cols"][0]["links"], detail=str(o and o["cols"]))

    s.section("Shorter, on every page")
    for path in PAGES:
        d = by(path, PHONE)
        s.check(f"{path} at 375: the footer is {foot_h(d)}px, no more than {FOOT_LIMIT} (it was 1,955 on /)",
                bool(d) and 0 < foot_h(d) <= FOOT_LIMIT)
    for path, width in [(p, PHONE) for p in PAGES] + [("/", NARROW)]:
        d = by(path, width)
        if not d:
            continue
        st = d["shut"]
        s.check(f"{path} at {width}: nothing in the footer runs past the screen's edge, and the page is no wider",
                not st["past"] and st["docW"] <= st["vw"], detail=f"{st['past']} page {st['docW']}px")

    s.section("Visit and Help, each a button over its own list")
    desk = by("/", DESK)
    for path, width in [(p, PHONE) for p in PAGES] + [("/", NARROW)]:
        d = by(path, width)
        if not d:
            continue
        st = d["shut"]
        names = [c["name"] for c in st["cols"]]
        s.check(f"{path} at {width}: Visit and Help are buttons inside their headings, each 44px or more",
                names[:2] == ["Visit", "Help"] and all(c["h2"] == "H2" for c in st["cols"]) and not short_buttons(st),
                detail=str(st["cols"]))
        s.check(f"{path} at {width}: each shut, saying so, and naming the list it opens",
                st["cols"] and all(c["button"] and c["button"]["expanded"] == "false" and c["button"]["controlsOk"]
                                   and c["drawn"] == 0 for c in st["cols"]), detail=str(st["cols"]))
        if desk and path == "/":
            s.check(f"/ at {width}: every link the footer has at a desk is still in the page ({desk['shut']['links']})",
                    st["links"] == desk["shut"]["links"] > 20, detail=f"{st['links']} here")

    d = by("/", PHONE)
    if d and d.get("open0"):
        o0, o1, again = d["open0"]["cols"], d.get("open1", {}).get("cols", []), d["shutAgain"]["cols"]
        s.check("pressing Visit opens it: every one of its links drawn, each 44px or more, and it says it is open",
                o0[0]["button"]["expanded"] == "true" and o0[0]["drawn"] == o0[0]["links"] > 0 and not o0[0]["short"],
                detail=str(o0[0]))
        s.check("and Help stays shut", o0[1]["drawn"] == 0 and o0[1]["button"]["expanded"] == "false",
                detail=str(o0[1]))
        s.check("pressing Help opens it too: every one of its links drawn",
                bool(o1) and o1[1]["drawn"] == o1[1]["links"] > 0 and not o1[1]["short"], detail=str(o1[1:2]))
        s.check("and pressing Visit again shuts it",
                again[0]["drawn"] == 0 and again[0]["button"]["expanded"] == "false", detail=str(again[0]))
        grew = d["open0"]["foot"]["h"] - d["shut"]["foot"]["h"]
        s.check(f"the footer grows by the list when it opens ({grew}px), and by nothing when it is shut",
                grew > 300 and d["shutAgain"]["foot"]["h"] == d["shut"]["foot"]["h"],
                detail=f"{d['shut']['foot']['h']} -> {d['open0']['foot']['h']} -> {d['shutAgain']['foot']['h']}")
    else:
        s.check("the Visit button could be pressed on the home page at 375", False, detail=str(d and d["shut"]["cols"]))
    if d and d.get("turned"):
        w = d["turned"]["cols"]
        s.check(f"a phone turned to {WIDE}px: the buttons are taken away, the headings read as before, every list shows",
                d["turned"]["vw"] == WIDE and [c["name"] for c in w][:2] == ["Visit", "Help"]
                and all(not c["button"] and c["drawn"] == c["links"] > 0 for c in w),
                detail=f"at {d['turned']['vw']}px, resized {d.get('resized')}: {w}")
    else:
        s.check("the home page at 375 could be widened", False)
    wide = next((dd for f, dd in real if f["width"] == WIDE and f.get("turn") == PHONE), None)
    if wide and wide.get("turned"):
        st0, b = wide["shut"]["cols"], wide["turned"]["cols"]
        s.check(f"at {WIDE}px to begin with, no buttons and every list shown",
                all(not c["button"] and c["drawn"] == c["links"] > 0 for c in st0), detail=str(st0))
        s.check(f"narrowed to {PHONE}px, they become buttons, shut",
                wide["turned"]["vw"] == PHONE
                and all(c["button"] and c["button"]["expanded"] == "false" and c["drawn"] == 0 for c in b),
                detail=f"at {wide['turned']['vw']}px, resized {wide.get('resized')}: {b}")
        o = (wide.get("turnedOpen") or {}).get("cols")
        s.check("and the buttons made then open their lists like the others",
                bool(o) and o[0]["drawn"] == o[0]["links"] > 0 and o[0]["button"]["expanded"] == "true", detail=str(o))
    else:
        s.check(f"the home page at {WIDE}px could be narrowed", False)

    s.section("The newsletter, the drawing")
    for width in (PHONE, NARROW):
        dd = by("/", width)
        nw = dd and dd["shut"]["news"]
        s.check(f"at {width}: the newsletter's field and its button on one row, the button 44px or more, "
                f"the field room to type in",
                bool(nw) and abs(nw["inp"]["top"] - nw["btn"]["top"]) <= 2 and nw["btn"]["h"] >= 44
                and nw["inp"]["w"] >= 140, detail=str(nw))
    for path in PAGES:
        dd = by(path, PHONE)
        if dd:
            st = dd["shut"]
            s.check(f"{path} at 375: the drawing at {st['crestW']}px, no more than {CREST_MAX}, "
                    f"its dates stacked with no rule left beside them",
                    st["crestW"] and st["crestW"] <= CREST_MAX and st["dateRules"] == 0,
                    detail=f"{st['dateRules']} date(s) with a rule")

    s.section("From 600px up, as it was")
    if desk:
        st = desk["shut"]
        s.check("at 1100: no buttons, and every Visit and Help link drawn",
                st["cols"] and all(not c["button"] and c["drawn"] == c["links"] > 0 for c in st["cols"]),
                detail=str(st["cols"]))
        s.check("and the drawing wider than on a phone", (st["crestW"] or 0) > CREST_MAX, detail=f"{st['crestW']}px")
    return s
