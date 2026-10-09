"""No word printed over a button or over another word, on any owner page.

WHAT WENT WRONG. Read page by page on 9 October 2026, then measured:

    Change Password. "Signing in at the wrong address?" sat behind the Update
    password button. The disclaimer style pulls itself up 6px to tuck under a
    table; under a button that put the line underneath it.

    Company & Insurance. An "Add a company" button set inside the small italic
    line under its heading, pulled up 10px, printed over the end of the
    sentence above.

    The owner home, on a phone. In "Waiting on you" the supplier's name fell
    into the 16px column meant for the coloured bar -- one word per line -- and
    the amount printed over it from the column beside.

    The till journal, and every tile band like it. A tile's figure and its
    small note were written flush, so "Verified" and "recomputed" were one word
    to the browser, and on a narrow tile the note ran into the next tile.

    Overview and Tasks. The Day / Week / Month switch stood one above the
    other, a tall box across the page: a rule for a label over its input
    caught it.

    Breakfast and Workshops, on a phone. A row of a tick, a name and up to
    five buttons could not wrap, and ran to 480px and 651px inside 390,
    dragging the page sideways with it.

WHY A BROWSER, AND WHY EVERY PAGE. Each is a sum of widths and margins that
only layout can do, and each was on a page nobody was looking for it on. So
every staff page the owner opens without arguments is loaded into headless
Chrome as a touch screen, at a desk's width and a phone's, and measured: the
ink of every line of text against every visible control and every other line.
Anything folded away in a closed <details>, hidden, or in a fixed overlay
(the PIN pad on the arrival screen) is not on the page, and is not measured.

THE MEASURING IS CHECKED TOO. The same pages go round again with the old rules
put back in a <style> of their own, and must then be caught. No browser is a
failure, not a skip; set GUDANES_CHROME.
"""
import json
import os
import shutil
import subprocess
import tempfile

from _harness import Suite, clients, db
import _harness
import test_owner_pages_read_cleanly as sweep
import test_staff_header_on_a_phone as phone

m = _harness.m
TAG = "ZZSTANDCLEAR"
WIDTHS = (1100, 600, 390)   # a desk, a small tablet or a large phone, a phone
CHUNK = 40          # frames per browser: a few hundred iframes in one page times out

# The faults as they were, to put back for the control. !important because
# two of the fixes are inline on the element.
OLD_RULES = """<style>
.search-bar > .segmented{ flex-direction:column !important; }
form + .pay-disclaimer{ margin-top:-6px !important; }
.task-row, .doc-row{ flex-wrap:nowrap !important; }
@media (max-width:720px){
  .oh-qrow > :not(.oh-bar){ grid-column:auto !important; }
  .oh-qrow > .oh-bar{ grid-row:auto !important; height:26px !important; }
}
</style>"""

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function fixedUp(el) {
    for (var e = el; e && e !== document.body; e = e.parentElement)
      if (getComputedStyle(e).position === 'fixed') return true;
    return false;
  }
  function seen(el) {
    if (el.checkVisibility && !el.checkVisibility(
        {contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true}))
      return false;
    if (el.closest('details:not([open])') && !el.closest('summary')) return false;
    return !fixedUp(el);
  }
  // What of a line is actually on screen: a box that hides its overflow --
  // the calendar clamps a long task title to three lines -- still reports
  // the hidden lines where they would have been, over the next chip.
  function clipped(el, r) {
    var box = {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
    for (var e = el; e && e !== document.documentElement; e = e.parentElement) {
      var cs = getComputedStyle(e);
      if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
      var c = e.getBoundingClientRect();
      if (cs.overflowX !== 'visible') { box.left = Math.max(box.left, c.left); box.right = Math.min(box.right, c.right); }
      if (cs.overflowY !== 'visible') { box.top = Math.max(box.top, c.top); box.bottom = Math.min(box.bottom, c.bottom); }
    }
    return (box.right - box.left >= 2 && box.bottom - box.top >= 2) ? box : null;
  }
  function ov(a, b) {
    return Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
           Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2;
  }
  function say(s) { return s.replace(/\s+/g, ' ').trim().slice(0, 40); }
  function go() {
    var de = document.documentElement, main = document.querySelector('main') || document.body;
    var out = {id: q.id, asked: +q.w, vw: de.clientWidth,
               coarse: matchMedia('(pointer: coarse)').matches,
               onControl: [], onText: [], switches: []};
    var sel = 'button, a.btn-mini, a.btn-primary, a.btn-ghost, input[type=submit], select, ' +
              'textarea, input[type=text], input[type=email], input[type=date], input[type=number]';
    var ctls = [].filter.call(main.querySelectorAll(sel), function (c) {
      var r = c.getBoundingClientRect(); return r.width > 2 && r.height > 2 && seen(c);
    }).map(function (c) { return {el: c, r: c.getBoundingClientRect()}; });
    var texts = [], walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT, null), n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue.trim()) continue;
      var p = n.parentElement;
      if (!p || p.closest('script,style,option,select,textarea,[hidden]') || !seen(p)) continue;
      var rg = document.createRange(); rg.selectNodeContents(n);
      [].forEach.call(rg.getClientRects(), function (r) {
        var shown = (r.width >= 2 && r.height >= 2) ? clipped(p, r) : null;
        if (shown) texts.push({node: n, el: p, r: shown});
      });
    }
    texts.forEach(function (t) {
      ctls.forEach(function (c) {
        if (c.el === t.el || c.el.contains(t.el)) return;
        var lab = t.el.closest('label');
        if (lab && lab.contains(c.el)) return;
        if (ov(t.r, c.r)) out.onControl.push('"' + say(t.node.nodeValue) + '" over ' +
          c.el.tagName.toLowerCase() + ' "' + say(c.el.textContent || c.el.name || '') + '"');
      });
    });
    for (var i = 0; i < texts.length; i++) for (var j = i + 1; j < texts.length; j++) {
      if (texts[i].node !== texts[j].node && ov(texts[i].r, texts[j].r))
        out.onText.push('"' + say(texts[i].node.nodeValue) + '" and "' +
                        say(texts[j].node.nodeValue) + '"');
    }
    [].forEach.call(main.querySelectorAll('.segmented'), function (sg) {
      if (!seen(sg)) return;
      var tops = [].map.call(sg.children, function (a) { return a.getBoundingClientRect().top; });
      out.switches.push({choices: tops.length,
                         rows: tops.filter(function (t, k) {
                           return tops.slice(0, k).every(function (u) { return Math.abs(u - t) > 2; });
                         }).length});
    });
    // A tile's figure, and the small note beside it, end inside the tile.
    out.tiles = [];
    [].forEach.call(main.querySelectorAll('.overview-cell'), function (t) {
      var v = t.querySelector('.overview-value');
      if (!v || !seen(t)) return;
      var tr = t.getBoundingClientRect(), cs = getComputedStyle(t);
      var inner = tr.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
      var rg = document.createRange(); rg.selectNodeContents(v);
      var right = Math.max.apply(null, [].map.call(rg.getClientRects(),
                                                   function (x) { return x.right; }));
      out.tiles.push({figure: say(v.textContent), past: right - inner});
    });
    out.docW = de.scrollWidth;
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 0); });
})();
</script>
"""


def _queue_row(conn):
    """Something waiting on the owner, so the home page's queue has a row to lay
    out -- and something in stock, so each atelier draws its materials form,
    whose note printed behind its button. Without these the two pages are
    measured only when an earlier suite happened to leave them, which is a
    check that passes or fails on the order the suites ran in."""
    conn.execute("""INSERT INTO stock_items (name, category, unit, active, created_at)
                    VALUES (?, 'other', 'each', 1, ?)""",
                 (TAG + " Aprons", _harness.datetime_now()))
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, 'Roofing materials, north tower', 3400, 'pending', ?)""",
        (TAG + " Domaine Fournitures SARL", _harness.datetime_now()))
    conn.commit()


def _cleanup(conn):
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _measure(browser, files, frames):
    """phone.measure in chunks, ids kept global."""
    seen, stderr = {}, []
    for start in range(0, len(frames), CHUNK):
        part = frames[start:start + CHUNK]
        workdir = tempfile.mkdtemp(prefix="gudanes_standclear_")
        try:
            used = sorted({f["page"] for f in part})
            for i in used:
                with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                    fh.write(files[i])
            # phone.measure numbers frames from 0 within the call; map back.
            got, proc = phone.measure(browser, part, workdir)
            stderr += proc.stderr.strip().splitlines()[-2:]
            for k, d in got.items():
                seen[start + k] = d
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return seen, stderr


def run():
    s = Suite("text stands clear")

    s.section("The measuring")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found",
                   browser, detail="install Chrome or Chromium, or set GUDANES_CHROME "
                                   "to one -- this suite measures and cannot without one"):
        return s

    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    try:
        _queue_row(conn)
        fresh = {"dashboard": "/", "admin_workshops": "/admin/workshops"}
        pages = [(ep, url, html) for ep, url, html in sweep.owner_pages(oc)
                 if ep not in fresh]
        home = oc.get("/")
        s.check("the owner home renders with something waiting on it",
                home.status_code == 200 and TAG in home.get_data(as_text=True), home)
        pages.append(("dashboard", "/", home.get_data(as_text=True)))
        ateliers = oc.get("/admin/workshops")
        s.check("the ateliers page renders with something in stock to use",
                ateliers.status_code == 200 and TAG + " Aprons" in ateliers.get_data(as_text=True),
                ateliers)
        pages.append(("admin_workshops", "/admin/workshops", ateliers.get_data(as_text=True)))
    finally:
        _cleanup(conn)
        conn.close()

    files, names = [], []
    for ep, url, html in pages:
        page = phone.standalone(html)
        if PROBE and phone.PROBE in page:
            files.append(page.replace(phone.PROBE, PROBE))
            names.append(url)
    s.check(f"every page could be fitted with the probe ({len(files)} of {len(pages)})",
            len(files) == len(pages))

    # The control: the pages whose faults were found, with the old rules back.
    control_urls = ("/admin/overview", "/change-password", "/", "/admin/pos/journal",
                    "/breakfast")
    frames = []
    for i, url in enumerate(names):
        for w in WIDTHS:
            frames.append({"page": i, "width": w, "name": url, "control": False})
    for url in control_urls:
        if url in names:
            # And the figure and its note written flush again, as they were.
            files.append(files[names.index(url)].replace("</head>", OLD_RULES + "</head>", 1)
                         .replace('<wbr><span class="overview-sub">',
                                  '<span class="overview-sub">'))
            for w in WIDTHS:
                frames.append({"page": len(files) - 1, "width": w, "name": url,
                               "control": True})

    try:
        seen, stderr = _measure(browser, files, frames)
    except subprocess.TimeoutExpired:
        s.check("the browser finished measuring", False, detail="timed out")
        return s
    missing = [frames[i]["name"] for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail=", ".join(missing[:10]) + " | " + " | ".join(stderr[-3:]))
    got = [(f, seen[i]) for i, f in enumerate(frames) if i in seen]
    wrong = [f"{f['name']} at {f['width']}: frame {d['vw']}px, coarse {d['coarse']}"
             for f, d in got if d["vw"] != f["width"] or not d["coarse"]]
    s.check("each frame is a touch screen at the width asked for", not wrong,
            detail="; ".join(wrong[:5]))

    def stacked(d):
        return [f"{x['choices']} choices on {x['rows']} rows" for x in d["switches"]
                if x["rows"] > 1]

    s.section("The control: the old rules put back must be caught")
    ctl = {(f["name"], f["width"]): d for f, d in got if f["control"]}
    s.check("the Overview switch is reported standing on end",
            stacked(ctl.get(("/admin/overview", 1100), {"switches": []})),
            detail=json.dumps(ctl.get(("/admin/overview", 1100), {}).get("switches")))
    s.check("the Change Password line is reported behind its button",
            any(ctl.get(("/change-password", w), {}).get("onControl") for w in WIDTHS))
    s.check("the till journal's tile note is reported running out of its tile",
            any(t["past"] > 0.5 for w in WIDTHS
                for t in ctl.get(("/admin/pos/journal", w), {}).get("tiles", [])),
            detail="; ".join(f"{w}px: {t['figure']} {t['past']:.0f}px"
                             for w in WIDTHS
                             for t in ctl.get(("/admin/pos/journal", w), {}).get("tiles", [])
                             if "Verified" in t["figure"]))
    s.check("the owner home's queue is reported overprinted on a phone",
            ctl.get(("/", 390), {}).get("onText"),
            detail=json.dumps(ctl.get(("/", 390), {}).get("onText", [])[:2]))
    s.check("the breakfast checklist is reported wider than a phone",
            ctl.get(("/breakfast", 390), {}).get("docW", 0) > 390,
            detail=f"{ctl.get(('/breakfast', 390), {}).get('docW')}px")

    real = [(f, d) for f, d in got if not f["control"]]
    s.section("There is something to measure")
    switches = sum(len(d["switches"]) for f, d in real if f["width"] == 1100)
    s.check("there are Day / Week / Month switches on the pages", switches >= 2,
            detail=f"{switches}")
    tiles = sum(len(d["tiles"]) for f, d in real if f["width"] == 1100)
    s.check("there are tile bands on the pages", tiles > 100, detail=f"{tiles}")

    for w in WIDTHS:
        group = [(f, d) for f, d in real if f["width"] == w]
        s.section(f"At {w}px, {len(group)} pages")

        def each(test):
            return "; ".join(f"{f['name']}: " + ", ".join(why[:3])
                             for f, d in group for why in [test(d)] if why)

        for label, test in (
                ("no text is printed over a button or a box", lambda d: d["onControl"]),
                ("no text is printed over other text", lambda d: d["onText"]),
                ("every Day / Week / Month switch lies in one row", stacked),
                ("the page is no wider than the screen",
                 lambda d: [f"{d['docW']}px"] if d["docW"] > d["vw"] else []),
                ("every tile's figure ends inside its tile",
                 lambda d: [f"{t['figure']} by {t['past']:.0f}px" for t in d["tiles"]
                            if t["past"] > 0.5])):
            bad = each(test)
            s.check(label, not bad, detail=bad)
    return s


if __name__ == "__main__":
    print(run().report())
