"""The home page's opening screen on a phone: the photograph clear, the words on navy.

WHAT WENT WRONG. On 26 November 2026 the owner sent a screenshot of the home
page on an iPhone and wrote "this doesnt look very good". Every word was
there. They were simply printed on the building: the hero's still was
stretched to cover a hero far taller than the screen, which zoomed it in on
the château's pale stone, and the white title ran across its windows and the
gilt dates along its wall, where neither could be read. The answer the site
had used before -- a darker veil over the whole photograph -- had been taken
off that same morning, at the owner's word ("its so dark").

WHAT THE PAGE DOES NOW. Below 600px the photograph is a band across the top,
the whole estate in it, fading into the hero's own navy. The name sits in the
fade; the dates, the motto and the two sentences under it sit on the navy.
Wider screens keep the photograph behind the words, unchanged.

THE VIDEO. The phone had been given a still on 9 November, because the film
cropped to a tall hero was a slice of wall. In the band it is 43% of the
film's width -- the château at the end of the drive, the carriage, the gates
-- so the same evening the owner asked for it back ("yes i want the video").
A phone that will not play it (data saving, less motion, Low Power Mode) gets
the still from the air instead, held to the same band.

WHAT IS MEASURED, in headless Chrome as a touch screen at 390, 375 and 320px
(the plumbing of test_staff_header_on_a_phone), twice: once with the film
there to play, and once with play() refused as an iPhone in Low Power Mode
refuses it, which is the still's path:

- with the film there, it plays, and fills exactly the band;
- with play refused, the still from the air is on the hero, held to the top:
  not stretched to cover it, not tiled;
- either way the band is most of the first screen -- the photograph is seen,
  not reduced to a strip;
- the wash is that same band, so the fade ends where the photograph does;
- the eyebrow and the name start in the band's lower part, where the fade has
  darkened it, never over the bright top of the photograph;
- the dates, the motto, the lede and the sentence after it are all below the
  band, on the navy;
- and the page is no wider than the screen.

THE MEASURING IS CHECKED TOO. The same page is measured again with the old
layout put back in a <style> of its own -- the still covering the whole hero
and the words starting under the header -- and must then be reported as
words over the photograph. At 1100px the photograph must still be behind the
words, so the change cannot leak onto a desk. No browser is a failure, not a
skip; set GUDANES_CHROME.
"""
import os
import re
import shutil
import subprocess
import tempfile

from _harness import Suite
import _harness
import test_staff_header_on_a_phone as phone

PHONES = (390, 375, 320)
DESK = 1100

# The phone hero as it was before 26 November, to put back for the control.
OLD_LAYOUT = """<style>
.g .g-hero:has(.g-hbook){ background-size: cover !important; background-position: center !important; }
.g .g-hero:has(.g-hbook) .g-hero__wash{ inset: 0 !important; height: auto !important; }
.g .g-hero:has(.g-hbook) .g-hero__in{ padding-top: calc(86px + 1rem) !important; }
</style>"""

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function top(sel) {
    var el = document.querySelector(sel);
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return r.height >= 1 ? r.top : null;
  }
  function go() {
    var de = document.documentElement, hero = document.querySelector('.g-hero');
    var wash = document.querySelector('.g-hero__wash');
    var out = {id: q.id, asked: +q.w, vw: de.clientWidth, docW: de.scrollWidth,
               vh: de.clientHeight, coarse: matchMedia('(pointer: coarse)').matches};
    if (hero) {
      var cs = getComputedStyle(hero), hr = hero.getBoundingClientRect();
      out.hero = {top: hr.top, h: hr.height, image: cs.backgroundImage,
                  size: cs.backgroundSize, repeat: cs.backgroundRepeat,
                  position: cs.backgroundPosition};
      var v = document.querySelector('.g-hero__video');
      out.video = !!v;
      if (v) {
        var vr = v.getBoundingClientRect();
        out.film = {top: vr.top, bottom: vr.bottom, w: vr.width,
                    playing: !v.paused && v.readyState >= 2 && v.currentTime > 0};
      }
    }
    if (wash) { var wr = wash.getBoundingClientRect(); out.wash = {top: wr.top, bottom: wr.bottom}; }
    out.words = {
      eyebrow: top('.g-hero__eyebrow'), title: top('.g-hero__title'),
      dates: top('.g-hist-tag--home'), motto: top('.g-hero__motto'),
      lede: top('.g-hero__lede'), fact: top('.g-hero__fact')};
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 1500); });
})();
</script>
"""


def run():
    s = Suite("home hero on a phone")

    s.section("The measuring")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found",
                   browser, detail="install Chrome or Chromium, or set GUDANES_CHROME "
                                   "to one -- this suite measures and cannot without one"):
        return s

    r = _harness.m.app.test_client().get("/")
    if not s.check("the home page renders", r.status_code == 200, r,
                   detail=f"status {r.status_code}"):
        return s
    page = phone.standalone(r.get_data(as_text=True))
    if not s.check("the home page can be fitted with the probe", phone.PROBE in page):
        return s
    page = page.replace(phone.PROBE, PROBE)
    s.check("the home hero is the one with the booking card", 'class="g-hbook"' in page)

    # The film as the page names it, and two versions of the page, both with its real file: one where it can be
    # played, and one where play() is refused before any of the page's scripts run, as Low Power Mode refuses it.
    film = re.search(r'data-src="(/static/video/[^"]+)"', page)
    if not s.check("the hero names its film", bool(film)):
        return s
    there = "file:///" + os.path.join(_harness.ROOT, "static", film.group(1)[len("/static/"):]).replace("\\", "/")
    playable = page.replace(film.group(0), f'data-src="{there}"')
    missing = playable.replace("<head>", "<head><script>HTMLMediaElement.prototype.play = function () {"
                               " return Promise.reject(new DOMException('refused', 'NotAllowedError')); };"
                               "</script>", 1)
    s.check("the refusal goes in ahead of the page's scripts", missing.count("NotAllowedError") == 1
            and missing.index("NotAllowedError") < missing.index("data-src="))
    files = [missing, missing.replace("</head>", OLD_LAYOUT + "</head>", 1), playable]
    frames = [{"page": 0, "width": w, "control": False, "film": False} for w in PHONES + (DESK,)]
    frames += [{"page": 1, "width": w, "control": True, "film": False} for w in PHONES]
    frames += [{"page": 2, "width": w, "control": False, "film": True} for w in PHONES]

    workdir = tempfile.mkdtemp(prefix="gudanes_homehero_")
    try:
        for i, f in enumerate(files):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(f)
        try:
            seen, proc = phone.measure(browser, frames, workdir)
        except subprocess.TimeoutExpired:
            s.check("the browser finished measuring", False, detail="timed out after 240s")
            return s
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    missing = [i for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    got = [(f, seen[i]) for i, f in enumerate(frames) if i in seen]
    wrong = [f"{f['width']}: frame {d['vw']}px, coarse {d['coarse']}"
             for f, d in got if d["vw"] != f["width"] or not d["coarse"]]
    s.check("each frame is a touch screen at the width asked for", not wrong,
            detail="; ".join(wrong))

    def band(d):
        """The photograph's band: where the wash is, top and bottom."""
        w = d.get("wash") or {}
        return w.get("top"), w.get("bottom")

    def over_photo(d):
        """The words that start above the navy, and how far into the band."""
        t, b = band(d)
        if t is None:
            return ["no wash to measure"]
        words, bad = d["words"], []
        fade = t + 0.6 * (b - t)          # the band's lower part, where the fade has darkened it
        for name in ("eyebrow", "title"):
            if words.get(name) is None:
                bad.append(f"no {name}")
            elif words[name] < fade:
                bad.append(f"the {name} at {words[name]:.0f}px, over the bright photograph above {fade:.0f}px")
        for name in ("dates", "motto", "lede", "fact"):
            if words.get(name) is None:
                bad.append(f"no {name}")
            elif words[name] < b - 2:
                bad.append(f"the {name} at {words[name]:.0f}px, above the navy at {b:.0f}px")
        return bad

    s.section("The control: the old layout put back must be caught")
    ctl = [(f, d) for f, d in got if f["control"]]
    s.check("measured with the old layout at every phone width", len(ctl) == len(PHONES))
    for f, d in ctl:
        s.check(f"at {f['width']}px the old layout is reported as words over the photograph",
                over_photo(d), detail="nothing reported: the measuring cannot see the fault")
        s.check(f"at {f['width']}px the old layout is reported as a still stretched to cover",
                d.get("hero", {}).get("size") == "cover", detail=str(d.get("hero")))

    for f, d in got:
        if f["control"]:
            continue
        w = f["width"]
        hero = d.get("hero") or {}
        if w == DESK:
            s.section(f"At {w}px, a desk: unchanged")
            t, b = band(d)
            s.check("the wash still covers the hero, the photograph behind the words",
                    t is not None and b - t >= hero.get("h", 0) - 2,
                    detail=f"wash {t}-{b}, hero {hero.get('h')}")
            s.check("and the name is over the photograph, as it was",
                    d["words"].get("title") is not None and d["words"]["title"] < b,
                    detail=str(d["words"]))
            continue
        t, b = band(d)
        if f["film"]:
            s.section(f"At {w}px, with the film to play")
            film_box = d.get("film") or {}
            s.check("the film is there and playing", d.get("video") and film_box.get("playing"), detail=str(film_box))
            s.check("it fills exactly the band, top to bottom and edge to edge",
                    t is not None and abs(film_box.get("top", -99) - t) < 1
                    and abs(film_box.get("bottom", -99) - b) < 1 and abs(film_box.get("w", 0) - d["vw"]) < 1,
                    detail=f"film {film_box}, band {t}-{b}")
            s.check("the photograph is most of the first screen, not a strip",
                    t is not None and (b - t) >= 0.5 * d["vh"], detail=f"band {t}-{b} of {d['vh']}px")
            bad = over_photo(d)
            s.check("no word is printed over the bright part of the film", not bad, detail="; ".join(bad))
            continue
        s.section(f"At {w}px, a phone that will not play the film")
        s.check("the still from the air is on the hero instead",
                not d.get("video") and "from-the-air" in hero.get("image", ""), detail=str(hero))
        s.check("the still is held to the top of the hero, not stretched to cover it",
                hero.get("size") not in ("cover", "", None) and hero.get("repeat") == "no-repeat"
                and hero.get("position", "").split()[-1:] in (["0%"], ["0px"]), detail=str(hero))
        s.check("and it is exactly as tall as the band, so the fade ends where the photograph does",
                t is not None and hero.get("size", "").endswith(f"{round(b - t)}px"),
                detail=f"still {hero.get('size')}, band {t}-{b}")
        s.check("the band is the photograph's, starting at the top of the hero",
                t is not None and abs(t - hero.get("top", 0)) < 1, detail=f"wash {t}, hero {hero.get('top')}")
        s.check("the photograph is most of the first screen, not a strip",
                t is not None and (b - t) >= 0.5 * d["vh"], detail=f"band {t}-{b} of {d['vh']}px")
        bad = over_photo(d)
        s.check("no word is printed over the bright part of the photograph", not bad, detail="; ".join(bad))
        s.check("the page is no wider than the screen", d["docW"] <= d["vw"],
                detail=f"{d['docW']}px on a {d['vw']}px screen")
    return s


if __name__ == "__main__":
    print(run().report())
