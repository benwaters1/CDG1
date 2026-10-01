"""A kitchen page on the kitchen wall: ?wall=1, run in a real browser.

Wall mode is two things, and both had quietly stopped working.

The LOOK was written into gudanes.css against the public site's chrome, and
the kitchen pages load style.css only, so wall mode reloaded on its timer and
did nothing else: the admin bar and the menu stayed, the type stayed at desk
size and the stamp that says how fresh the page is printed as two loose words
above the heading. test_design proves the classes have rules; this proves the
rules do what a wall needs, by asking the browser what it drew.

The STAMP went blank at the first reload. The script held on to the element it
found when the page loaded, and a reload replaces everything inside <main>,
the stamp included, so every later write went to an element no longer on the
screen -- and a reload that failed turned the bar red under the word "live",
which is the one thing a wall screen must never say when it is stale. That is
only visible by running the script, so this runs it: the page is loaded in
headless Chrome with fetch stood in, so the first reload succeeds and every
later one fails, and virtual time is run past twelve minutes.

Needs Chrome. Without it the suite says so out loud and checks nothing -- a
skip that printed PASS would read as cover.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import _harness
from _harness import Suite

ROOT = _harness.ROOT
CANDIDATES = [
    os.environ.get("CHROME_PATH") or "",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
    shutil.which("chrome") or "",
]

# The wall's own reload is a fetch of the page's own address. The first one
# succeeds with the page exactly as the server sent it; every one after fails,
# as Starlink does. Anything else the shell fetches -- the notification count
# is polled every thirty seconds -- is refused, and is not counted: it took the
# one good answer the first time this was written, the wall never reloaded,
# and a stamp held from the first load passed as if it had survived one.
# Then, once the stamp has had its chance to go stale, the browser reports
# what it drew into the page for the dump to carry out.
PROBE = """<script>
(function(){
  var served = %s, calls = 0;
  window.fetch = function(url){
    if (String(url) !== location.href) return Promise.reject(new Error('not this test'));
    calls++;
    if (calls === 1) return Promise.resolve({ok:true, text:function(){ return Promise.resolve(served); }});
    return Promise.reject(new Error('the connection dropped'));
  };
  function look(){
    var cs = function(sel, prop){ var el = document.querySelector(sel);
      return el ? getComputedStyle(el)[prop] : null; };
    var page = document.querySelector('.page');
    return {
      time: (document.querySelector('[data-kw-time]') || {}).textContent || '',
      state: (document.querySelector('[data-kw-state]') || {}).textContent || '',
      stale: document.body.classList.contains('kw-stale'),
      stamps: document.querySelectorAll('.kw-stamp').length,
      calls: calls,
      topbar: cs('.topbar', 'display'), sidebar: cs('.sidebar', 'display'),
      stamp_position: cs('.kw-stamp', 'position'),
      stamp_bottom: document.querySelector('.kw-stamp') ?
        Math.round(window.innerHeight - document.querySelector('.kw-stamp').getBoundingClientRect().bottom) : null,
      zoom: page ? parseFloat(getComputedStyle(page).zoom || '1') : null,
      body_bg: getComputedStyle(document.body).backgroundColor,
      buttons: Array.prototype.filter.call(document.querySelectorAll('.page .btn-mini, .page .btn-ghost'),
        function(b){ return b.getClientRects().length; }).length
    };
  }
  window.addEventListener('load', function(){
    document.body.setAttribute('data-first', JSON.stringify(look()));
    setTimeout(function(){ document.body.setAttribute('data-later', JSON.stringify(look())); },
               16 * 60 * 1000);
  });
})();
</script>"""


def _chrome():
    for c in CANDIDATES:
        if c and os.path.exists(c):
            return c
    return None


def _standalone(html):
    css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
    html = re.sub(r'<link[^>]*href="/static/style\.css[^"]*"[^>]*>', lambda m: "<style>%s</style>" % css, html)
    html = re.sub(r'<script[^>]*src="[^"]*"[^>]*></script>', "", html)
    # The reply is the page as served, before any script has touched it: a
    # stamp copied out of the live page already has a time written in, and
    # would hide exactly the blank this is here to see. </ is escaped so the
    # string cannot close the script it sits in.
    served = json.dumps(html).replace("</", "<\\/")
    return html.replace("<head>", "<head>" + (PROBE % served), 1)


def _run_in_chrome(chrome, html, width):
    work = tempfile.mkdtemp(prefix="gudanes_wall_")
    try:
        page = os.path.join(work, "page.html")
        open(page, "w", encoding="utf-8").write(html)
        r = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--user-data-dir=" + os.path.join(work, "profile"),
             "--window-size=%d,900" % width, "--virtual-time-budget=1200000",
             "--dump-dom", "file:///" + page.replace("\\", "/")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        out = {}
        for key in ("first", "later"):
            m = re.search(r'data-%s="([^"]*)"' % key, r.stdout)
            out[key] = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&")) if m else None
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run():
    s = Suite("Kitchen wall")
    oc, _ec, _owner, _emp = _harness.clients()

    s.section("The page asks for wall mode and gets it")
    r = oc.get("/kitchen/prep?wall=1")
    html = r.get_data(as_text=True)
    s.check("the prep list answers in wall mode", r.status_code == 200 and "kw-stamp" in html, r)
    s.check("and without ?wall=1 it is the ordinary page", "kw-stamp" not in
            oc.get("/kitchen/prep").get_data(as_text=True))

    chrome = _chrome()
    if not chrome:
        print("    SKIP  no Chrome on this machine, so nothing below was checked -- "
              "set CHROME_PATH to run it")
        return s

    s.section("What the browser draws, at a kitchen tablet's width")
    got = _run_in_chrome(chrome, _standalone(html), 1180)
    first, later = got["first"] or {}, got["later"] or {}
    s.check("the page ran and reported", bool(first) and bool(later), detail=str(got)[:200])
    s.check("the admin bar and the menu are gone",
            first.get("topbar") == "none" and first.get("sidebar") == "none",
            detail="topbar %r, sidebar %r" % (first.get("topbar"), first.get("sidebar")))
    s.check("the controls are gone: nobody taps a wall", first.get("buttons") == 0,
            detail="%r buttons still showing" % first.get("buttons"))
    s.check("the stamp is pinned to the foot of the screen",
            first.get("stamp_position") == "fixed" and first.get("stamp_bottom") == 0,
            detail="position %r, %r px off the bottom"
                   % (first.get("stamp_position"), first.get("stamp_bottom")))
    s.check("everything on it is drawn larger than at a desk", (first.get("zoom") or 1) >= 1.3,
            detail="zoom %r" % first.get("zoom"))
    s.check("on the dark ground the pass uses", first.get("body_bg") == "rgb(14, 28, 48)",
            detail=first.get("body_bg"))
    s.check("and the stamp says when it loaded", bool(first.get("time")) and first.get("state") == "live",
            detail="time %r, state %r" % (first.get("time"), first.get("state")))

    s.section("After one reload that worked and then a connection that dropped")
    s.check("it did reload, then fail", (later.get("calls") or 0) >= 2,
            detail="%r reloads attempted" % later.get("calls"))
    s.check("there is still exactly one stamp", later.get("stamps") == 1,
            detail="%r" % later.get("stamps"))
    s.check("the time is still on it -- it went blank at the first reload",
            bool(later.get("time")), detail="time %r" % later.get("time"))
    s.check("and it says the screen is stale, in words, not only in red",
            later.get("stale") and "min old" in (later.get("state") or ""),
            detail="stale %r, state %r -- red under 'live' is the lie this "
                   "screen exists to avoid" % (later.get("stale"), later.get("state")))

    s.section("On a phone, somebody checking the wall from elsewhere")
    phone = _run_in_chrome(chrome, _standalone(html), 600)["first"] or {}
    s.check("the chrome still goes, but nothing is enlarged",
            phone.get("topbar") == "none" and (phone.get("zoom") or 1) == 1,
            detail="topbar %r, zoom %r" % (phone.get("topbar"), phone.get("zoom")))
    return s


if __name__ == "__main__":
    print(run().report())
