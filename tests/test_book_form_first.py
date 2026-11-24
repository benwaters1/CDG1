"""The room page a guest books from: the form first, and a Pay & book that can be seen (24 Nov 2026).

The owner, on /book/<room> -- the page a guest lands on from Book this room:
"too much scrolling" before the details can be entered (the room's facts, a
paragraph on the walls, then a photograph the width of the screen, and only
then the form), and Pay & book "very hard to see". Both rendered perfectly
and broke nothing else, so both are held here in a browser, on the real page
with its own stylesheets and scripts, at a phone's width, a tablet's and a
computer's.

WHAT IS HELD

  1. The form's first step ("01 Your dates") is reached without a long scroll:
     inside the first screen on a computer, within a screen and a half on a
     phone. It was three screens and more at both.
  2. Nothing between the top of the page and that step is a photograph taller
     than 40% of the screen. The room's photograph is beside its name, or a
     short band over it on a phone.
  3. Nothing is lost: the room's own words, its facts and its gallery are all
     still on the page, after the form.
  4. On a computer the booking panel starts beside the room's name, so Pay &
     book is inside the first screen; on a phone and a tablet the bar at the
     foot carries it, and that is inside the first screen too.
  5. Pay & book, in the panel and in the bar, in every state it has -- as
     drawn, under the pointer, with the keyboard on it, and disabled while the
     page is sent ("Holding...") -- has words at 4.5:1 at least against its own
     colour (WCAG AA), a colour at 3:1 against the navy it sits on, and, with
     the keyboard on it, a focus ring at 3:1 against that navy. The states are
     resolved by the browser's own cascade: every rule written for :hover or
     :focus-visible is copied, in place, for a class the probe can set.
  6. The room's photograph is fetched at the size it is drawn: the page says
     its own sizes, and the srcset added after it does not add a second one.
"""
import html as htmllib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import timedelta

from _harness import Suite
import _harness
from test_staff_header_on_a_phone import find_browser

m = _harness.m
ROOT = _harness.ROOT

FRAMES = (("phone", 390, 844), ("tablet", 800, 1024), ("computer", 1400, 900))

# fetch() from a file:// page reaches nothing. The room page asks for the nights
# already taken before it shows its calendar: none, so it comes up as on the site.
FETCH_STUB = r"""<script>
window.fetch = function (url) {
  url = String(url);
  if (url.indexOf('/api/availability') > -1) {
    return Promise.resolve({ok: true, json: function () { return Promise.resolve({unavailable: {}}); }});
  }
  return Promise.resolve({ok: false, json: function () { return Promise.resolve(null); }});
};
</script>"""

PROBE = r"""<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function top(el) { return el ? Math.round(el.getBoundingClientRect().top + scrollY) : null; }
  function shown(el) { if (!el) return false; var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; }
  function ground(el) {
    for (var n = el; n; n = n.parentElement) {
      var c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(c)) return c;
    }
    return 'rgb(255, 255, 255)';
  }
  /* A state, by the browser's own cascade: every rule written for the pseudo-class is copied, in its place, for a
     class with the same weight, and the class is set. The copy sits straight after the rule it copies. */
  var STATES = {hover: /:hover\b/g, focus: /:focus-visible\b|:focus(?![-\w])/g};
  function copyRules(name) {
    var rx = STATES[name];
    function walk(list, owner) {
      for (var i = list.length - 1; i >= 0; i--) {
        var r = list[i];
        if (r.selectorText !== undefined) {
          if (rx.test(r.selectorText)) {
            rx.lastIndex = 0;
            try { owner.insertRule(r.selectorText.replace(rx, '.t-' + name) + '{' + r.style.cssText + '}', i + 1); } catch (e) {}
          }
          rx.lastIndex = 0;
        } else if (r.cssRules && r.insertRule) { walk(r.cssRules, r); }
      }
    }
    [].forEach.call(document.styleSheets, function (s) { try { walk(s.cssRules, s); } catch (e) {} });
  }
  function look(el) {
    var cs = getComputedStyle(el), panel = el.closest('.g-dossier, .g-bookbar') || el.parentElement;
    return {bg: cs.backgroundColor, color: cs.color, opacity: cs.opacity, ground: ground(panel),
            outline: {w: parseFloat(cs.outlineWidth) || 0, style: cs.outlineStyle, color: cs.outlineColor},
            h: Math.round(el.getBoundingClientRect().height), text: el.textContent.replace(/\s+/g, ' ').trim()};
  }
  function states(el) {
    var out = {plain: look(el)};
    ['hover', 'focus'].forEach(function (n) { el.classList.add('t-' + n); out[n] = look(el); el.classList.remove('t-' + n); });
    var was = el.disabled; el.disabled = true; out.disabled = look(el); el.disabled = was;
    return out;
  }
  function report() {
    var out = {id: q.id, w: innerWidth, h: innerHeight, label: q.label};
    try {
      /* no transition between states: the probe reads where a state ends, not the first frame of getting there */
      var still = document.createElement('style'); still.textContent = '*{transition:none !important}';
      document.head.appendChild(still);
      var form = document.getElementById('book-form'), step = document.getElementById('book');
      out.step = top(step);
      out.big = [].filter.call(document.images, function (im) {
        return shown(im) && top(im) < out.step && im.getBoundingClientRect().height > 0.4 * innerHeight;
      }).map(function (im) { return {cls: im.className, h: Math.round(im.getBoundingClientRect().height), top: top(im)}; });
      function after(sel) {
        var el = document.querySelector(sel);
        if (!el) return 'missing';
        if (form.contains(el)) return 'inside the form';
        return (form.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) ? 'after' : 'before';
      }
      out.room = {words: after('.g-room__words'), facts: after('.g-glance'), note: after('.g-room__note'),
                  gallery: after('.g-roomgal-car'), photos: document.querySelectorAll('.g-roomgal-car img').length};
      out.name_top = top(document.querySelector('h1'));
      var pay = document.querySelector('.g-dossier .g-submit'), bar = document.querySelector('.g-bookbar .g-btn');
      var barOn = bar && getComputedStyle(bar.closest('.g-bookbar')).display !== 'none';
      out.pay_bottom = pay ? Math.round(pay.getBoundingClientRect().bottom) : null;
      out.panel_top = pay ? top(pay.closest('.g-dossier')) : null;
      out.bar_bottom = barOn ? Math.round(bar.getBoundingClientRect().bottom) : null;
      copyRules('hover'); copyRules('focus');
      out.states = {};
      if (pay && shown(pay)) out.states.panel = states(pay);
      if (barOn && shown(bar)) out.states.bar = states(bar);
    } catch (e) { out.error = String(e) + ' ' + (e.stack || ''); }
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(report, 800); });
})();
</script>"""

WRAPPER = """<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<script>
window.addEventListener('message', function (e) {
  var pre = document.createElement('pre'); pre.className = 'r'; pre.textContent = e.data; document.body.appendChild(pre);
});
</script>
%s
</body></html>"""


def _rgba(c):
    nums = [float(x) for x in re.findall(r"[\d.]+", c)]
    return nums[:3], (nums[3] if len(nums) > 3 else 1.0)


def _over(c, under, alpha=None):
    """Colour c laid over the colour under, at c's own alpha (or the one given)."""
    rgb, a = _rgba(c)
    a = a if alpha is None else a * alpha
    base, _ = _rgba(under)
    return "rgb(%s)" % ", ".join(str(round(a * x + (1 - a) * y)) for x, y in zip(rgb, base))


def _contrast(a, b):
    """WCAG contrast ratio of two opaque colours, 'rgb(r, g, b)'."""
    def lum(c):
        r, g, b = _rgba(c)[0]
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def seen_colours(state):
    """(words, button, ground) as the eye gets them: the button's colour over the navy it sits on, its words over
    that, and the whole at the element's opacity -- a disabled submit is drawn at 0.65."""
    ground = state["ground"]
    op = float(state["opacity"])
    button = _over(state["bg"], ground)
    words = _over(state["color"], button)
    return _over(words, ground, op), _over(button, ground, op), ground


def _inline_styles(page):
    """The page with its stylesheets put inline (so the probe may read and copy their rules), and nothing fetched."""
    def inline(match):
        tag = match.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        if not href or "stylesheet" not in tag:
            return "" if "preconnect" in tag else tag
        url = href.group(1)
        if url.startswith("/static/"):
            with open(os.path.join(ROOT, "static", url[len("/static/"):].split("?")[0]), encoding="utf-8") as fh:
                return "<style>\n" + fh.read() + "\n</style>"
        return ""
    return re.sub(r"<link\b[^>]*>", inline, page)


def measure(browser, page, workdir):
    with open(os.path.join(workdir, "room.html"), "w", encoding="utf-8") as fh:
        fh.write(page.replace("<head>", "<head>" + FETCH_STUB, 1).replace("</body>", PROBE + "</body>", 1))
    cells = ['<iframe src="room.html#id=%d&label=%s" style="display:block;border:0;width:%dpx;height:%dpx"></iframe>'
             % (i, label, w, h) for i, (label, w, h) in enumerate(FRAMES)]
    wrapper = os.path.join(workdir, "wrapper.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER % "\n".join(cells))
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--user-data-dir=" + os.path.join(workdir, "profile"),
           "--hide-scrollbars", "--allow-file-access-from-files", "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=20000", "--dump-dom",
           "file:///" + wrapper.replace("\\", "/").lstrip("/")]
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    seen = {}
    for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        got = json.loads(htmllib.unescape(blob))
        seen[got.get("label")] = got
    return seen


def run():
    s = Suite("The room page a guest books from: the form first, and Pay & book in sight (24 Nov 2026)")
    anon = m.app.test_client()
    room = _harness.ensure_room()
    arrival = _harness.free_window(room["id"], 2, clear_of_ateliers=True)
    departure = arrival + timedelta(days=2)
    url = f"/book/{room['id']}?arrival={arrival.isoformat()}&departure={departure.isoformat()}&party_size=2"

    # Drawn as the site draws it, with card payments on, so the button is "Pay & book" and the panel is the one a
    # paying guest sees. Only drawn: nothing here posts, and stripe.api_key is still None (_harness), so nothing could.
    real_enabled, was_live = m.stripe_enabled, m.SITE_IS_LIVE
    m.stripe_enabled, m.SITE_IS_LIVE = (lambda: True), True
    try:
        r = anon.get(url)
        page = r.get_data(as_text=True)
    finally:
        m.stripe_enabled, m.SITE_IS_LIVE = real_enabled, was_live

    s.section("The page, as drawn")
    s.check("the room's page answers, with Pay & book on it", r.status_code == 200 and "Pay &amp; book" in page,
            detail=str(r.status_code))
    hero = re.search(r'<img[^>]*class="g-roomhero[^"]*"[^>]*>', page, re.S)
    s.check("the room's photograph says the width it is drawn at, once (a srcset added after it adds no second sizes)",
            hero and len(re.findall(r"\ssizes=", hero.group(0))) == 1 and "100vw\"" not in hero.group(0),
            detail=hero.group(0)[:240] if hero else "no g-roomhero")
    s.check("a hero that says nothing still gets the full width",
            'sizes="100vw"' in m.size_hero('<img class="g-roomhero" src="/static/img/x.jpg">')
            or m.pic_size("/static/img/x.jpg") is None)
    form_at = page.find('<form id="book-form"')
    form_end, words_at = page.find("</form>", form_at), page.find('class="g-p g-intro g-room__words"')
    s.check("the room's own words come after the form's end, not before its first field",
            -1 < form_at < form_end < words_at, detail=f"form {form_at} to {form_end}, words {words_at}")

    s.section("And in a browser: a phone, a tablet, a computer")
    browser = find_browser()
    s.check("headless Chrome is on this machine to lay the page out", bool(browser),
            detail="set GUDANES_CHROME; a check that could not run is not a pass")
    if not browser:
        return s
    workdir = tempfile.mkdtemp(prefix="gudanes_formfirst_")
    seen = measure(browser, _inline_styles(page), workdir)
    s.check("every width reported back", len(seen) == len(FRAMES) and not any(v.get("error") for v in seen.values()),
            detail=str({k: v.get("error") for k, v in seen.items()}) + f" {len(seen)} of {len(FRAMES)}")

    for label, w, h in FRAMES:
        got = seen.get(label) or {}
        if not got:
            continue
        step = got.get("step") or 10 ** 6
        # 1. the first step, within reach
        reach = h if label == "computer" else int(1.5 * h)
        s.check(f"{label} ({w}px): the form's first step starts within {'the first screen' if label == 'computer' else 'a screen and a half'}",
                step < reach, detail=f"01 Your dates at {step}px, the screen {h}px tall")
        # 2. no large photograph on the way
        s.check(f"{label}: no photograph taller than 40% of the screen stands between the top and the form",
                not got.get("big"), detail=str(got.get("big")))
        # 3. nothing lost
        room_ = got.get("room") or {}
        s.check(f"{label}: the room's words, facts, note and gallery are all still there, after the form",
                all(room_.get(k) == "after" for k in ("words", "facts", "note", "gallery")) and room_.get("photos", 0) > 0,
                detail=str(room_))
        # 4. Pay & book in the first screen
        if got.get("bar_bottom") is not None:
            s.check(f"{label}: the bar at the foot carries Pay & book in the first screen",
                    got["bar_bottom"] <= h, detail=f"bar button foot at {got['bar_bottom']}px")
        else:
            s.check(f"{label}: the booking panel starts beside the room's name, and Pay & book is in the first screen",
                    got.get("panel_top") is not None and abs(got["panel_top"] - (got.get("name_top") or 0)) < 80
                    and (got.get("pay_bottom") or 10 ** 6) <= h,
                    detail=f"panel at {got.get('panel_top')}px, name at {got.get('name_top')}px, "
                           f"Pay & book's foot at {got.get('pay_bottom')}px of {h}")
        # 5. every state
        for where, states in (got.get("states") or {}).items():
            for state in ("plain", "hover", "focus", "disabled"):
                st = states.get(state) or {}
                if not st:
                    s.check(f"{label}, {where}: the {state} state was read", False)
                    continue
                words, button, ground = seen_colours(st)
                s.check(f"{label}, {where} Pay & book, {state}: its words stand off it at 4.5:1 at least (AA)",
                        _contrast(words, button) >= 4.5,
                        detail=f"{_contrast(words, button):.2f}: {st['color']} on {st['bg']} at opacity {st['opacity']}")
                s.check(f"{label}, {where} Pay & book, {state}: it stands off the navy it sits on at 3:1 at least",
                        _contrast(button, ground) >= 3, detail=f"{_contrast(button, ground):.2f}: {button} on {ground}")
                if state == "focus":
                    ring = st["outline"]
                    s.check(f"{label}, {where} Pay & book, with the keyboard on it: a ring of 2px or more, at 3:1 on the navy",
                            ring["style"] not in ("none", "hidden") and ring["w"] >= 2
                            and _contrast(_over(ring["color"], ground), ground) >= 3,
                            detail=str(ring))
            plain = states.get("plain") or {}
            s.check(f"{label}, {where}: it says Pay & book, and is 48px tall at least",
                    plain.get("text") == "Pay & book" and (plain.get("h") or 0) >= 48, detail=str(plain.get("text")) + f" {plain.get('h')}px")
        if label == "computer":
            s.check("computer: the panel's Pay & book was found and measured", "panel" in (got.get("states") or {}))
        else:
            s.check(f"{label}: the bar's Pay & book was found and measured", "bar" in (got.get("states") or {}))
    return s


if __name__ == "__main__":
    print(run().report())
