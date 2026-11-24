"""The room booking path, from the search to "Pay & book" (24 Nov 2026).

BRIEF-booking-flow-24-nov-for-Claude-Code.md walked the live site at 16:45, at
1366px and on a 375px phone: /book, a search for two nights and two guests, a
room, the form, and "Pay & book" pressed with the form empty. Every fault it
found rendered perfectly and broke nothing else, so most of what is held here
is held in a browser, on the real pages, with their own scripts and stylesheets.

WHAT IS HELD

  1. "Pay & book" is a button: its background stands off the navy panel it
     sits in by a contrast of 3 at least, on a computer (the booking panel) and
     on a phone (the bar at the foot), and it is 48px tall. It was navy on navy.
  2. Every search for rooms opens on its results (#results), from /book, the
     home page's two and the menu's quick dates. A search that went wrong has no
     #results to land on, so the page stays where the flash says why.
  3. The bar over the results says the dates it searched ("Fri 12 Mar"), not
     "Add dates": the picker painted only the first pair on the page, which is
     the menu's.
  4. The guest count the bar shows reaches the room: every "Book this room"
     carries it, 2 when the search gave none.
  5. The country of residence starts on "Choose your country", in a browser
     whose language says en-US; the telephone code is not guessed separately,
     and follows the country chosen. A proxy's country header (visitor_country)
     is the one thing that may choose first, for both.
  6. One way to choose dates: once the calendar is up the date boxes are out of
     sight and the dates read above it as words -- and an empty one still says
     "This one is needed." where it can be seen.
  7. The bar at the foot and the summary show the same total, to the cent.
  8. The room's top photograph goes out with its smaller sizes (srcset), and
     "With the Room" has no row with one tile left on it, at any width.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta

from _harness import Suite
import _harness
from test_staff_header_on_a_phone import find_browser
from test_public_margins import _localise

m = _harness.m
ROOT = _harness.ROOT

DESK, PHONE, TABLET = 1366, 375, 800

# fetch() from a file:// page reaches nothing. The room page asks the server for
# the nights already taken before it shows its calendar; answered here with none,
# so the calendar comes up as it does on the site. Anything else is refused, as
# a failed request is, and the page keeps what the server drew.
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
  function box(el) { if (!el) return null; var r = el.getBoundingClientRect(); return {w: r.width, h: r.height}; }
  function ground(el) {
    for (var n = el; n; n = n.parentElement) {
      var c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(c)) return c;
    }
    return 'rgb(255, 255, 255)';
  }
  function text(el) { return el ? el.textContent.replace(/\s+/g, ' ').trim() : null; }
  function report() {
    var out = {id: q.id, w: +q.w, page: q.p};
    try {
      var bar = document.querySelector('form.g-search');
      if (bar) {
        out.bar = {arrival: text(bar.querySelector('[data-rp-label=arrival]')),
                   departure: text(bar.querySelector('[data-rp-label=departure]')),
                   nights: text(bar.querySelector('[data-rp-nights]'))};
      }
      var pay = document.querySelector('.g-dossier .g-submit');
      if (pay && getComputedStyle(pay).display !== 'none' && box(pay).w > 0) {
        var panel = pay.closest('.g-dossier');
        var pcs = getComputedStyle(panel);
        out.pay = {bg: getComputedStyle(pay).backgroundColor, ground: ground(panel),
                   color: getComputedStyle(pay).color, box: box(pay),
                   inner: box(panel).w - parseFloat(pcs.paddingLeft) - parseFloat(pcs.paddingRight)};
      }
      var bb = document.querySelector('.g-bookbar .g-btn');
      if (bb && getComputedStyle(bb.closest('.g-bookbar')).display !== 'none') {
        out.barbtn = {bg: getComputedStyle(bb).backgroundColor, ground: ground(bb.parentElement),
                      color: getComputedStyle(bb).color, box: box(bb)};
      }
      var bt = document.querySelector('[data-bar-total]'), qt = document.getElementById('quote_total');
      if (bt && qt) out.totals = {bar: text(bt), summary: text(qt)};
      var row = document.querySelector('[data-datefields]');
      if (row) {
        out.dates = {read: row.classList.contains('is-read'),
                     boxes: [box(document.getElementById('arrival_date')).w, box(document.getElementById('departure_date')).w],
                     words: [].map.call(row.querySelectorAll('[data-dateread]'), function (e) {
                       return {text: text(e), shown: !e.hidden && box(e).h > 0}; }),
                     calendar: !document.getElementById('availability_calendar').hidden};
      }
      var items = document.querySelectorAll('.g-fstrip > .g-fstrip__i');
      if (items.length) {
        var rows = {};
        [].forEach.call(items, function (e) { var t = Math.round(e.getBoundingClientRect().top); rows[t] = (rows[t] || 0) + 1; });
        out.strip = Object.keys(rows).map(Number).sort(function (a, b) { return a - b; }).map(function (k) { return rows[k]; });
      }
      var cs = document.querySelector('[data-country]'), cc = document.querySelector('[data-phone-cc]');
      if (cs && cc) {
        out.lang = navigator.language;
        out.country = {start: cs.value, code: cc.value};
        var gb = cs.querySelector('option[data-iso="GB"]');
        gb.selected = true; cs.dispatchEvent(new Event('change', {bubbles: true}));
        out.country.code_after_gb = cc.value;
      }
      if (q.submit) {
        var f = document.getElementById('book-form');
        f.requestSubmit();
        var err = document.getElementById('arrival_date-err');
        out.empty = {said: text(err), box: box(err)};
      }
    } catch (e) { out.error = String(e); }
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


def _contrast(a, b):
    """WCAG contrast ratio of two computed colours, 'rgb(r, g, b)'."""
    def lum(c):
        r, g, b = [int(x) for x in re.findall(r"[\d.]+", c)[:3]]
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _pretty(d):
    """The picker's own words for a date: "Fri 12 Mar"."""
    return d.strftime("%a ") + str(d.day) + d.strftime(" %b")


def _measure(browser, frames, workdir):
    """frames: [(label, html, width, extra_hash)]. Returns {label@width: what the page reported}."""
    cells = []
    for i, (label, body, width, extra) in enumerate(frames):
        name = "f%d.html" % i
        page = body.replace("<head>", "<head>" + FETCH_STUB, 1).replace("</body>", PROBE + "</body>", 1)
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
            fh.write(page)
        cells.append('<iframe src="%s#id=%d&w=%d&p=%s%s" style="display:block;border:0;width:%dpx;height:900px"></iframe>'
                     % (name, i, width, label, extra, width))
    wrapper = os.path.join(workdir, "wrapper.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER % "\n".join(cells))
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--disable-extensions", "--user-data-dir=" + os.path.join(workdir, "profile"),
           "--hide-scrollbars", "--allow-file-access-from-files", "--host-resolver-rules=MAP * ~NOTFOUND",
           # The language that gave one visitor the United States for a telephone code.
           "--lang=en-US", "--accept-lang=en-US",
           "--virtual-time-budget=20000", "--dump-dom",
           "file:///" + wrapper.replace("\\", "/").lstrip("/")]
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    seen = {}
    for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        got = json.loads(htmllib.unescape(blob))
        seen["%s@%s" % (got.get("page"), got.get("w"))] = got
    return seen


def _book_links(html):
    return [htmllib.unescape(u) for u in re.findall(r'href="(/book/\d+\?[^"]*)"[^>]*>Book this room<', html)]


def _selected(html, select_id):
    """The options a <select> is drawn with selected: (value, data-iso)."""
    found = re.search(r'<select[^>]*\bid="%s"[^>]*>(.*?)</select>' % re.escape(select_id), html, re.S)
    if not found:
        return None
    return re.findall(r'<option value="([^"]*)" data-iso="([^"]*)"[^>]*\bselected\b', found.group(1))


def run():
    s = Suite("The room booking path, search to Pay & book (24 Nov 2026)")
    anon = m.app.test_client()
    conn = _harness.db()
    room = _harness.ensure_room()
    arrival = _harness.free_window(room["id"], 2, clear_of_ateliers=True)
    departure = arrival + timedelta(days=2)
    a, d = arrival.isoformat(), departure.isoformat()
    conn.close()

    # ---- 2. the searches land on the results --------------------------------
    s.section("Every search for rooms opens on its results")
    searched = anon.get(f"/book?arrival={a}&departure={d}&party_size=2").get_data(as_text=True)
    s.check("/book's own search posts to #results",
            re.search(r'<form method="get" action="/book#results" class="g-search"', searched) is not None,
            detail="it posted with no anchor, so the page came back at the hero")
    s.check("and the menu's quick dates do too",
            re.search(r'<form class="g-qbook" method="get" action="/book#results"', searched) is not None)
    home = anon.get("/").get_data(as_text=True)
    home_forms = re.findall(r'<form class="g-herosearch" method="get" action="([^"]*)"', home)
    s.check("and the home page's search", home_forms and all(f == "/book#results" for f in home_forms),
            detail=str(home_forms))
    s.check("a search with rooms to show has #results to land on", 'id="results"' in searched)
    wrong = anon.get(f"/book?arrival={d}&departure={a}").get_data(as_text=True)
    s.check("a search that went wrong has none, so the page stays where the flash says why",
            'id="results"' not in wrong and "after the arrival" in wrong)

    # ---- 4. the guest count reaches the room --------------------------------
    s.section("The guest count the bar shows reaches the room")
    bare = anon.get(f"/book?arrival={a}&departure={d}").get_data(as_text=True)
    links = _book_links(bare)
    s.check("with no count in the address, the bar shows 2",
            re.search(r'id="s_party" name="party_size"[^>]*value="2"', bare) is not None)
    s.check("and every Book this room carries party_size=2, not party_size= empty",
            links and all("party_size=2" in u for u in links), detail=str(links[:2]))
    three = _book_links(anon.get(f"/book?arrival={a}&departure={d}&party_size=3").get_data(as_text=True))
    s.check("a count the guest chose is carried as chosen",
            three and all("party_size=3" in u for u in three), detail=str(three[:2]))

    # ---- 5. the country, and the telephone code ------------------------------
    s.section("The country of residence starts unchosen, and the telephone code follows it")
    url = f"/book/{room['id']}?arrival={a}&departure={d}&party_size=2"
    page = anon.get(url).get_data(as_text=True)
    s.check("with no proxy's word for it, no country is chosen ('Choose your country')",
            _selected(page, "guest_country") == [], detail=str(_selected(page, "guest_country")))
    s.check("and the telephone code starts on the house's, France",
            [iso for _, iso in (_selected(page, "guest_phone_cc") or [])] == ["FR"],
            detail=str(_selected(page, "guest_phone_cc")))
    country_js = open(os.path.join(ROOT, "templates", "_country.html"), encoding="utf-8").read()
    s.check("the country is never guessed from the browser's language",
            "navigator.language" not in country_js)
    geo = anon.get(url, headers={"CF-IPCountry": "GB"}).get_data(as_text=True)
    s.check("a proxy's country header chooses the country first",
            [iso for _, iso in (_selected(geo, "guest_country") or [])] == ["GB"],
            detail=str(_selected(geo, "guest_country")))
    s.check("and the telephone code with it, the same answer",
            [iso for _, iso in (_selected(geo, "guest_phone_cc") or [])] == ["GB"]
            and "data-geo" in geo, detail=str(_selected(geo, "guest_phone_cc")))
    for code, why in (("XX", "Cloudflare could not tell"), ("AQ", "a country not on the list")):
        odd = anon.get(url, headers={"CF-IPCountry": code}).get_data(as_text=True)
        s.check(f"{code} ({why}) chooses nothing, and the code stays on France",
                _selected(odd, "guest_country") == []
                and [iso for _, iso in (_selected(odd, "guest_phone_cc") or [])] == ["FR"])

    # ---- 7. one total, as the server draws it --------------------------------
    s.section("The bar and the summary say the same total")
    bar = re.search(r'data-bar-total>([^<]*)<', page)
    summary = re.search(r'id="quote_total">([^<]*)<', page)
    s.check("drawn by the server: the bar's figure is the summary's, to the cent",
            bar and summary and bar.group(1).strip() == summary.group(1).strip()
            and re.fullmatch(r"€\d+\.\d\d", bar.group(1).strip()),
            detail=f"bar {bar.group(1) if bar else None!r}, summary {summary.group(1) if summary else None!r}")

    # ---- 8. the photograph and the strip -------------------------------------
    s.section("The room's photograph, sized; With the Room, in full rows")
    tag = '<img loading="eager" class="g-roomhero" src="/static/img/%s" alt="A room">'
    # Any photograph of the house's own wide enough to have smaller sizes.
    held = next((rel for rel in sorted(
                    os.path.relpath(os.path.join(dp, f), os.path.join(ROOT, "static", "img")).replace(os.sep, "/")
                    for dp, _, fs in os.walk(os.path.join(ROOT, "static", "img")) for f in fs)
                 if rel.lower().endswith((".jpg", ".jpeg")) and (m.pic_size("/static/img/" + rel) or (0,))[0] > 600),
                None)
    sized = m._HERO_IMG.sub(lambda x: m.size_hero(x.group(0)), tag % held) if held else ""
    s.check("a room's top photograph (g-roomhero) is given its smaller sizes, as a page hero is",
            held and ' srcset="/pic/' in sized and 'sizes="100vw"' in sized, detail=sized[:160])
    hero = re.search(r'<img[^>]*class="g-roomhero"[^>]*>', page, re.S)
    name = m._hero_pic_name(re.search(r'\ssrc="([^"]*)"', hero.group(0)).group(1)) if hero else None
    s.check("and on the room page itself, when the house holds the file",
            hero and (name is None or " srcset=" in hero.group(0)),
            detail=f"held as {name!r}: {hero.group(0)[:160] if hero else 'no g-roomhero'}")
    strip = re.findall(r'class="g-fstrip__i"', page)
    s.check("With the Room has twelve, which rows of three, four and six all divide",
            len(strip) == 12, detail=f"{len(strip)} tiles")

    # ---- in a browser ---------------------------------------------------------
    s.section("And in a browser, on the pages as they are drawn")
    browser = find_browser()
    s.check("headless Chrome is on this machine to run the pages' own scripts", bool(browser),
            detail="set GUDANES_CHROME; a check that could not run is not a pass")
    if not browser:
        return s
    workdir = tempfile.mkdtemp(prefix="gudanes_bookflow_")
    was_live = m.SITE_IS_LIVE
    try:
        for css in ("gudanes.css", "gudanes-pictures.css"):
            shutil.copy(os.path.join(ROOT, "static", css), os.path.join(workdir, css))
        m.SITE_IS_LIVE = True

        def drawn(path):
            return _localise(anon.get(path).get_data(as_text=True))
        search_page, room_page = drawn(f"/book?arrival={a}&departure={d}&party_size=2"), drawn(url)
        empty_page = drawn(f"/book/{room['id']}")
        frames = [("search", search_page, DESK, ""),
                  ("room", room_page, DESK, ""), ("room", room_page, TABLET, ""), ("room", room_page, PHONE, ""),
                  ("empty", empty_page, DESK, "&submit=1")]
        seen = _measure(browser, frames, workdir)
        s.check("every page reported back", len(seen) == len(frames) and not any(v.get("error") for v in seen.values()),
                detail=str({k: v.get("error") for k, v in seen.items()}) + f" {len(seen)} of {len(frames)}")

        # 3. the bar says what it searched
        got = (seen.get(f"search@{DESK}") or {}).get("bar") or {}
        s.check("/book's bar says the dates it searched, not Add dates",
                got.get("arrival") == _pretty(arrival) and got.get("departure") == _pretty(departure),
                detail=f"{got} for {_pretty(arrival)} to {_pretty(departure)}")
        s.check("and the nights", got.get("nights") == "2 nights", detail=str(got.get("nights")))

        # 1. Pay & book is a button
        pay = (seen.get(f"room@{DESK}") or {}).get("pay")
        s.check("on a computer, Pay & book stands off its navy panel (contrast 3 or more)",
                pay and _contrast(pay["bg"], pay["ground"]) >= 3,
                detail=f"{pay and pay['bg']} on {pay and pay['ground']}: "
                       f"{pay and round(_contrast(pay['bg'], pay['ground']), 2)}")
        s.check("its words stand off the button", pay and _contrast(pay["color"], pay["bg"]) >= 4.5,
                detail=f"{pay and pay['color']} on {pay and pay['bg']}")
        s.check("48px tall at least, and the width of the panel",
                pay and pay["box"]["h"] >= 48 and pay["box"]["w"] >= pay["inner"] - 1,
                detail=str(pay and (pay["box"], pay["inner"])))
        for width in (TABLET, PHONE):
            bb = (seen.get(f"room@{width}") or {}).get("barbtn")
            s.check(f"at {width}px, the bar's Pay & book stands off the navy bar (contrast 3 or more), 48px tall",
                    bb and _contrast(bb["bg"], bb["ground"]) >= 3 and bb["box"]["h"] >= 48,
                    detail=f"{bb and bb['bg']} on {bb and bb['ground']}, {bb and bb['box']}")

        # 7. one total
        for width in (TABLET, PHONE):
            tot = (seen.get(f"room@{width}") or {}).get("totals") or {}
            s.check(f"at {width}px, the bar's total is the summary's, to the cent",
                    tot.get("bar") and tot.get("bar") == tot.get("summary"), detail=str(tot))

        # 5. the country and the code, with the browser saying en-US
        cty = (seen.get(f"room@{DESK}") or {}).get("country") or {}
        lang = (seen.get(f"room@{DESK}") or {}).get("lang")
        s.check(f"in a browser saying {lang}, the country starts on Choose your country",
                cty.get("start") == "", detail=str(cty))
        s.check("and the telephone code is not guessed from the language (France, not +1)",
                cty.get("code") == "+33", detail=str(cty))
        s.check("choosing the United Kingdom moves the code to +44", cty.get("code_after_gb") == "+44",
                detail=str(cty))

        # 6. one way to choose the dates
        for width in (DESK, PHONE):
            dt = (seen.get(f"room@{width}") or {}).get("dates") or {}
            words = [w.get("text") for w in dt.get("words") or []]
            s.check(f"at {width}px, with the calendar up, the date boxes are out of sight",
                    dt.get("calendar") and dt.get("read") and all(w <= 1 for w in dt.get("boxes") or [99]),
                    detail=str(dt))
            want = [arrival.strftime("%a ") + str(arrival.day) + arrival.strftime(" %B %Y"),
                    departure.strftime("%a ") + str(departure.day) + departure.strftime(" %B %Y")]
            s.check(f"at {width}px, the dates read above it as words",
                    words == want and all(w.get("shown") for w in dt.get("words")), detail=f"{words} for {want}")
        empty = seen.get(f"empty@{DESK}") or {}
        s.check("with no dates, they say to choose on the calendar",
                [w.get("text") for w in (empty.get("dates") or {}).get("words") or []] == ["Choose on the calendar"] * 2,
                detail=str(empty.get("dates")))
        said = empty.get("empty") or {}
        s.check("and Pay & book with none says 'This one is needed.' where it can be seen",
                said.get("said") == "This one is needed." and (said.get("box") or {}).get("h", 0) > 0,
                detail=str(said))

        # 8. With the Room, in full rows
        for width in (DESK, TABLET, PHONE):
            rows = (seen.get(f"room@{width}") or {}).get("strip") or []
            s.check(f"at {width}px, With the Room fills every row ({'+'.join(map(str, rows))})",
                    rows and len(set(rows)) == 1, detail=str(rows))
    finally:
        m.SITE_IS_LIVE = was_live
        shutil.rmtree(workdir, ignore_errors=True)
    return s


if __name__ == "__main__":
    print(run().report())
