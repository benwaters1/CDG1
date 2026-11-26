"""The lightbox's photograph in the middle of the screen, and the guests' signatures inside their cards.

WHAT WENT WRONG. Two faults on the public pages, both in screenshots the owner
sent on 25 November 2026, neither of which any check could have noticed, since
both pages drew every photograph and every word they were meant to.

    The lightbox. A portrait photograph opened hard against the left edge of
    the screen, the arrows nowhere and the cross in the corner. The dialog was
    fixed with left and right at 0 and width auto, so it took the full 96vw
    allowed it, and the photograph sat at its left. A landscape photograph
    fills most of that width and looks centred, which is why it went unseen
    until a tall one was opened. The arrows were 60px outside the box, so off
    the screen.

    The signatures. "WORKSHOP GUEST, AUTUMN 2023" beside its wax seal, set in
    spaced capitals that could not wrap, is wider than a card three to a row.
    It ran out through the gilt frame on the right and, being centred, put the
    seal over the frame on the left.

WHY A BROWSER. Both are sums -- a photograph's drawn width against a box that
sizes itself, a line of spaced capitals against a grid column that depends on
the screen -- and only layout can do them. The real pages are rendered through
the test client, loaded into headless Chrome with the plumbing of
test_staff_header_on_a_phone, and measured at a desk and on a phone.

WHAT IS MEASURED. The lightbox on /gallery and /workshops is opened on a
photograph swapped for a tall picture (2400 x 3600) and then a wide one (3600
x 2400): each must sit centred across the screen and wholly on it, the cross
and both arrows on the screen, and the cross off the photograph. On the home
page, the ink of every signature -- the seal and the words -- must end inside
its card's padding.

THE MEASURING IS CHECKED TOO. The same pages are measured again with the old
rules put back in a <style> of their own, and must then be reported off centre
and spilling. If that control passes, nothing below is worth reading. No
browser is a failure, not a skip; set GUDANES_CHROME.
"""
import os
import re
import shutil
import subprocess
import tempfile

from _harness import Suite
import _harness
import test_staff_header_on_a_phone as phone

m = _harness.m
# 1400 and 1100: a desk (cards three to a row; the owner's screenshot). 820: an iPad upright.
# 390 and 320: a phone, and the narrowest still about.
WIDTHS = (1400, 1100, 820, 390, 320)
DESKS = (1400, 1100)
PAGES = (("the home page", "/"), ("the gallery", "/gallery"))
LIGHTBOXES = ("the gallery", "a retreat")

# The rules as they were before 25 Nov 2026, to put back for the control.
OLD_RULES = """<style>
.g section.rc-says figcaption.rc-sig{white-space:nowrap}
.g section.rc-says .rc-sig .rc-sig__l{white-space:inherit;text-align:inherit}
.g-lb[open]{position:fixed;inset:0;margin:auto;width:auto;height:fit-content;max-width:96vw;max-height:96vh;
  padding:0;display:block}
.g-lb__img{max-width:92vw;max-height:82vh}
.g-lb__x{top:-56px;right:0}
.g-lb__nav--p{left:-60px}
.g-lb__nav--n{right:-60px}
@media (max-width: 52rem){ .g-lb__nav--p{left:4px} .g-lb__nav--n{right:4px} .g-lb__x{top:4px;right:4px} }
</style>"""

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function picture(w, h) {
    return 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="' + w +
      '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '"><rect width="100%" height="100%" fill="#8a6"/></svg>');
  }
  function box(el) {
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height};
  }
  function ink(el) {
    var r = document.createRange(); r.selectNodeContents(el);
    return [].filter.call(r.getClientRects(), function (x) { return x.width >= 1 && x.height >= 1; });
  }
  /* A timer, not requestAnimationFrame: Chrome runs no animation frames in an iframe that is off the screen, and
     most of these are. Reading a box lays the page out there and then anyway. */
  function settle() { return new Promise(function (ok) { setTimeout(ok, 50); }); }
  function loaded(img) {
    return new Promise(function (ok) {
      if (img.complete && img.naturalWidth) return ok();
      img.addEventListener('load', function () { ok(); }, {once: true});
      img.addEventListener('error', function () { ok(); }, {once: true});
      setTimeout(ok, 3000);      /* never hang the frame: a picture that never came is reported as such */
    });
  }
  function signatures() {
    return [].map.call(document.querySelectorAll('.rc-card'), function (card) {
      var sig = card.querySelector('.rc-sig');
      if (!sig) return null;
      var c = card.getBoundingClientRect(), cs = getComputedStyle(card);
      var inL = c.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
      var inR = c.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
      var label = sig.querySelector('.rc-sig__l') || sig, rects = ink(label);
      var seal = sig.querySelector('svg');
      if (seal) rects.push(seal.getBoundingClientRect());
      if (!rects.length) return null;
      return {by: label.textContent.trim().replace(/\s+/g, ' '),
              left: inL - Math.min.apply(null, rects.map(function (x) { return x.left; })),
              right: Math.max.apply(null, rects.map(function (x) { return x.right; })) - inR};
    }).filter(Boolean);
  }
  /* What a guest clicks: a set's "See all photos" button, which opens the set at its first photograph (the
     retreat page, as in the owner's screenshot), or else a photograph on the page. Returns [what to click, the
     photograph the lightbox will show]; the set's photographs may still be packed in its <template>. */
  function trigger() {
    var b = document.querySelector('[data-lb-group] [data-lb-open]');
    if (b) {
      var g = b.closest('[data-lb-group]'), first = g.querySelector('img');
      var t = g.querySelector('template[data-lb-more]');
      if (!first && t) first = t.content.querySelector('img');
      return first ? [b, first] : null;
    }
    var shot = document.querySelector('.g-lb-scope img');
    return shot ? [shot, shot] : null;
  }
  async function lightbox() {
    var dlg = document.querySelector('[data-lb]');
    if (!dlg || !dlg.showModal || !trigger()) return null;
    var big = dlg.querySelector('[data-lb-img]'), out = {};
    var sizes = [['tall', 2400, 3600], ['wide', 3600, 2400]];
    for (var i = 0; i < sizes.length; i++) {
      if (dlg.open) dlg.close();
      var tr = trigger(), shot = tr[1];
      shot.removeAttribute('srcset');
      shot.setAttribute('src', picture(sizes[i][1], sizes[i][2]));
      tr[0].click();
      await loaded(big);
      await settle();
      out[sizes[i][0]] = {open: dlg.open, natural: big.naturalWidth, img: box(big),
                          x: box(dlg.querySelector('[data-lb-close]')),
                          prev: box(dlg.querySelector('[data-lb-prev]')),
                          next: box(dlg.querySelector('[data-lb-next]'))};
    }
    dlg.close();
    return out;
  }
  window.addEventListener('load', function () {
    setTimeout(async function () {
      var out = {id: q.id, vw: document.documentElement.clientWidth, vh: window.innerHeight,
                 sigs: signatures(), lb: null};
      try { out.lb = await lightbox(); } catch (e) { out.err = String(e); }
      parent.postMessage(JSON.stringify(out), '*');
    }, 0);
  });
})();
</script>
"""


def run():
    s = Suite("the lightbox and the signatures")

    s.section("The measuring")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found",
                   browser, detail="install Chrome or Chromium, or set GUDANES_CHROME "
                                   "to one -- this suite measures and cannot without one"):
        return s

    anon = m.app.test_client()
    # A retreat's own page, the one in the owner's screenshot: the first the retreats page links to.
    found = re.findall(r'href="(/retreats/[a-z0-9-]+)"', anon.get("/workshops").get_data(as_text=True))
    if not s.check("the retreats page links to a retreat to open", found, detail="no /workshops/<id> link"):
        return s
    pages = []
    for name, url in PAGES + (("a retreat", found[0]),):
        r = anon.get(url)
        if not s.check(f"{name} renders", r.status_code == 200, r, detail=f"status {r.status_code}"):
            continue
        page = phone.standalone(r.get_data(as_text=True))
        if not s.check(f"{name} can be fitted with the probe", phone.PROBE in page):
            continue
        pages.append((name, page.replace(phone.PROBE, PROBE)))
    if not pages:
        return s

    frames_, files = [], []
    for control in (False, True):
        for name, page in pages:
            if control:
                page = page.replace("</head>", OLD_RULES + "</head>", 1)
            files.append(page)
            for w in WIDTHS:
                frames_.append({"page": len(files) - 1, "width": w, "name": name, "control": control})

    workdir = tempfile.mkdtemp(prefix="gudanes_lightbox_")
    try:
        for i, page in enumerate(files):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(page)
        try:
            seen, proc = phone.measure(browser, frames_, workdir)
        except subprocess.TimeoutExpired:
            s.check("the browser finished measuring", False, detail="timed out after 240s")
            return s
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    missing = [i for i in range(len(frames_)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames_)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    got = [(f, seen[i]) for i, f in enumerate(frames_) if i in seen]
    broke = [f"{f['name']} at {f['width']}: {d['err']}" for f, d in got if d.get("err")]
    s.check("the probe ran to the end in every frame", not broke, detail="; ".join(broke))

    def spilling(d):
        return [f"\"{g['by']}\" {g['left']:.0f}px out on the left, {g['right']:.0f}px on the right"
                for g in d["sigs"] if g["left"] > 0.5 or g["right"] > 0.5]

    def off_centre(d):
        bad = []
        for kind, o in (d["lb"] or {}).items():
            i = o["img"]
            if not o["open"] or not i or i["w"] < 1:
                bad.append(f"the {kind} picture did not open")
                continue
            dx = (i["l"] + i["r"]) / 2 - d["vw"] / 2
            if abs(dx) > 2:
                bad.append(f"the {kind} picture {dx:+.0f}px from the middle")
        return bad

    def off_screen(d):
        bad = []
        for kind, o in (d["lb"] or {}).items():
            for part in ("img", "x", "prev", "next"):
                b = o.get(part)
                if b and (b["l"] < -0.5 or b["t"] < -0.5 or b["r"] > d["vw"] + 0.5 or b["b"] > d["vh"] + 0.5):
                    bad.append(f"{kind}: the {part} at {b['l']:.0f},{b['t']:.0f} to {b['r']:.0f},{b['b']:.0f}")
        return bad

    def cross_on_picture(d):
        bad = []
        for kind, o in (d["lb"] or {}).items():
            i, x = o.get("img"), o.get("x")
            if i and x and x["l"] < i["r"] and x["r"] > i["l"] and x["t"] < i["b"] and x["b"] > i["t"]:
                bad.append(f"{kind}: the cross over the photograph")
        return bad

    s.section("The control: the old rules put back must be caught")
    ctl = [(f, d) for f, d in got if f["control"]]
    caught_spill = [f"{f['name']} at {f['width']}" for f, d in ctl if spilling(d)]
    caught_centre = [f"{f['name']} at {f['width']}" for f, d in ctl if off_centre(d)]
    caught_screen = [f"{f['name']} at {f['width']}" for f, d in ctl if off_screen(d)]
    s.check("the home page's signatures are reported spilling at 1100px, the owner's screenshot",
            "the home page at 1100" in caught_spill, detail="caught at: " + (", ".join(caught_spill) or "nowhere"))
    s.check("the tall picture is reported off centre at a desk, on both pages",
            all(f"{n} at {w}" in caught_centre for n in LIGHTBOXES for w in DESKS),
            detail="caught at: " + (", ".join(caught_centre) or "nowhere"))
    s.check("and the arrows reported off the screen at a desk",
            all(f"a retreat at {w}" in caught_screen for w in DESKS),
            detail="caught at: " + (", ".join(caught_screen) or "nowhere"))

    real = [(f, d) for f, d in got if not f["control"]]
    s.section("There is something to measure")
    home = [d for f, d in real if f["name"] == "the home page"]
    s.check("the home page has its six signed cards at every width",
            len(home) == len(WIDTHS) and all(len(d["sigs"]) == 6 for d in home),
            detail=str([len(d["sigs"]) for d in home]))
    s.check("and one of them is a retreat guest's, the long one",
            home and all(any("Retreat guest" in g["by"] for g in d["sigs"]) for d in home))
    for name in LIGHTBOXES:
        lb = [d for f, d in real if f["name"] == name]
        s.check(f"{name} opens its lightbox at every width, on both pictures",
                len(lb) == len(WIDTHS) and all(d["lb"] and set(d["lb"]) == {"tall", "wide"}
                                               and all(o["natural"] >= 2400 for o in d["lb"].values())
                                               for d in lb),
                detail=str([sorted(d["lb"] or {}) for d in lb]))

    for w in WIDTHS:
        group = [(f, d) for f, d in real if f["width"] == w]
        s.section(f"At {w}px")

        def each(test):
            return "; ".join(f"{f['name']}: " + ", ".join(why)
                             for f, d in group for why in [test(d)] if why)

        for label, test in (
                ("every signature ends inside its card", spilling),
                ("the photograph opens in the middle of the screen", off_centre),
                ("the photograph, the cross and the arrows are all on the screen", off_screen),
                ("the cross is off the photograph", cross_on_picture)):
            bad = each(test)
            s.check(label, not bad, detail=bad)
    return s


if __name__ == "__main__":
    print(run().report())
