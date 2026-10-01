"""The staff header on a phone, measured in a browser rather than read.

WHAT WENT WRONG. The strip at the top right of every staff page -- the bell,
the name and role, Backup for the owner, EN/FR/ES, Password, Log out -- was a
flex row that could not wrap, and the touch rules in style.css rightly give
the bell and every link a 44px minimum, so on a phone the row simply ran off
the edge and dragged the whole document sideways with it. Measured before the
fix, with the touch rules applied:

    owner, 390px screen      the strip ended at 401px (Log out cut in half)
    owner, in French         434px; in Spanish 428px
    employee, 375px, French  389px; Spanish 383px
    a long name              476px, and an employee's pages went too

And where it did fit it fitted by crushing: "Log out" folded onto two lines
inside a target 30px wide, "Mot de passe" onto three.

WHY A BROWSER. Every other layout suite here reads the source, because the
suite had no browser and the faults they guard are a rule that is missing.
This one is a sum: seven things side by side, each with a floor under it,
adding up to more than a phone. The total depends on the words, the language
and the person's name, and no reading of the stylesheet can do that
arithmetic. So the real pages are rendered through the test client, loaded
into headless Chrome at phone widths, and measured.

HOW IT IS MADE A PHONE. `--blink-settings=primaryPointerType=2,...` makes
`(pointer: coarse)` true, so every touch rule in style.css applies exactly as
it does on a phone -- the stylesheet is not rewritten. `--hide-scrollbars`,
because a phone's scrollbars sit over the page, and without it a 390px frame
lays out at 375. Each page sits in an iframe of the phone's width, because a
desktop window will not go that narrow.

WHICH WIDTHS. 390 and 375 are the phones the house measured; 360 is the
commonest Android and 320 the narrowest still about. 601px is the narrowest
screen that gets the arrangement everything wider uses, so a change on either
side of the line at 600px is seen. Checks about one arrangement are asked only
where it applies: on a phone, Log out at the far end of its row and no more
rows than before; at 601px, the account links sharing a row.

THE NAME IS MEASURED BY ITS WORDS, not only by its box. A box can be the right
size while the words spill out over the link beside it, so the text is found
line by line, cut to what its box lets show, and must stay on the screen, off
every control, and on two lines at most.

NO NETWORK. Google Fonts are stripped and every hostname is pointed nowhere,
so the text is set in the machine's fallback fonts. That is why the checks are
about structure -- nothing past the edge, nothing hidden, Log out clear of
everything -- rather than pixel positions.

THE MEASURING IS CHECKED TOO. Every frame must be the width it was asked for
with the touch rules in force, or its other answers mean nothing; and a page
made too wide on purpose must be reported too wide, or a pass means nothing.

NO BROWSER IS A FAILURE, not a skip. A check that quietly does not run reads
as cover. Point GUDANES_CHROME at Chrome, Chromium or Edge to run it.
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from _harness import Suite, clients, db
import _harness

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TAG = "ZZPHONEHEAD"

PHONES = (390, 375, 360, 320)
NARROWEST_DESK = 601
WIDTHS = PHONES + (NARROWEST_DESK,)
LANGS = ("en", "fr", "es")
LANG_NAMES = {"en": "English", "fr": "French", "es": "Spanish"}
# Long, hyphenated and with a particle: the name that finds every assumption.
LONG_NAME = "Marie-Christine de La Rochefoucauld-Montmorency"
PAGE = "/today"          # the page staff open first, on a phone
LOGOUT_CLEARANCE = 12    # px: what the touch rules gave it before, and no less
NAME_AT_LEAST = 40       # px of the name left showing, however little room

# Rows the header had before the fix, at 375 and 390, in English. The fix must
# not buy the fit by stacking the header taller: it is sticky, so every row is
# screen gone from every page.
ROWS_BEFORE = {"owner": 3, "employee": 2}

CONTROLS = [
    ("menu", ".topbar .sidebar-toggle"),
    ("search", "#topbar-search-input"),
    ("bell", ".topbar .bell-link"),
    ("backup", '.topbar a[href$="/admin/backup"]'),
    ("EN", '.topbar a[hreflang="en"]'),
    ("FR", '.topbar a[hreflang="fr"]'),
    ("ES", '.topbar a[hreflang="es"]'),
    ("password", '.topbar a[href$="/change-password"]'),
    ("logout", "#logout-link"),
]
EVERYONE = ["menu", "bell", "EN", "FR", "ES", "password", "logout"]
OWNER_ONLY = ["search", "backup"]
LABELLED = ["backup", "EN", "FR", "ES", "password", "logout"]

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  var CONTROLS = %s;
  function box(el) {
    var r = el.getBoundingClientRect();
    return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height};
  }
  function shown(el) {
    var r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width >= 1 && r.height >= 1 && cs.visibility === 'visible'
        && parseFloat(cs.opacity) > 0;
  }
  function lines(el) {
    var range = document.createRange(), tops = [];
    range.selectNodeContents(el);
    [].forEach.call(range.getClientRects(), function (r) {
      if (r.width < 1) return;
      var t = Math.round(r.top);
      if (tops.every(function (x) { return Math.abs(x - t) > 2; })) tops.push(t);
    });
    return tops.length;
  }
  // Where the words of an element actually are, line by line, cut down to
  // what its box lets show: a box can be the right size while its text
  // spills out over whatever is next to it.
  function inked(el) {
    var range = document.createRange(), cs = getComputedStyle(el), out = [];
    range.selectNodeContents(el);
    var clip = (cs.overflowX !== 'visible' || cs.overflowY !== 'visible')
        ? el.getBoundingClientRect() : null;
    [].forEach.call(range.getClientRects(), function (r) {
      var l = r.left, t = r.top, rr = r.right, b = r.bottom;
      if (clip) {
        l = Math.max(l, clip.left); t = Math.max(t, clip.top);
        rr = Math.min(rr, clip.right); b = Math.min(b, clip.bottom);
      }
      if (rr - l >= 1 && b - t >= 1) out.push({l: l, r: rr, t: t, b: b});
    });
    return out;
  }
  function go() {
    var de = document.documentElement, out = {
      id: q.id, asked: +q.w, vw: de.clientWidth, docW: de.scrollWidth,
      coarse: matchMedia('(pointer: coarse)').matches, controls: {}};
    CONTROLS.forEach(function (c) {
      var el = document.querySelector(c[1]);
      if (!el) return;
      var b = box(el);
      b.shown = shown(el);
      b.lines = el.tagName === 'A' && el.textContent.trim() ? lines(el) : 1;
      out.controls[c[0]] = b;
    });
    var nm = document.querySelector('.topbar .who-name') || document.querySelector('.topbar .who');
    if (nm) {
      out.name = box(nm); out.name.shown = shown(nm); out.name.ink = inked(nm);
      // How wide the name is when nothing constrains it, set in the same
      // font beside the real one: a short name is not a squeezed one.
      var free = document.createElement('span');
      free.textContent = nm.firstChild ? nm.firstChild.textContent : nm.textContent;
      free.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
      nm.parentNode.appendChild(free);
      out.name.natural = free.getBoundingClientRect().width;
      free.remove();
    }
    var badge = document.querySelector('.topbar .role-badge');
    if (badge) { out.badge = box(badge); out.badge.shown = shown(badge); }
    var inner = document.querySelector('.topbar-inner');
    if (inner) out.innerRight = inner.getBoundingClientRect().right
        - parseFloat(getComputedStyle(inner).paddingRight);
    var far = null;
    [].forEach.call(document.body.querySelectorAll('*'), function (el) {
      var r = el.getBoundingClientRect();
      if (r.width && (!far || r.right > far.right)) {
        var cls = typeof el.className === 'string' ? el.className.trim() : '';
        far = {right: Math.round(r.right),
               what: el.tagName.toLowerCase() + (cls ? '.' + cls.split(/\s+/).join('.') : '')};
      }
    });
    out.widest = far;
    parent.postMessage(JSON.stringify(out), '*');
  }
  // A timer, not requestAnimationFrame: Chrome does not run animation frames
  // in an iframe scrolled out of view, so every frame but the first would
  // wait for ever. Asking for a box lays the page out anyway.
  window.addEventListener('load', function () {
    var ready = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    ready.then(function () { setTimeout(go, 0); });
  });
})();
</script>
""" % json.dumps(CONTROLS)

WRAPPER_HEAD = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0">
<script>
window.addEventListener('message', function (e) {
  var pre = document.createElement('pre');
  pre.className = 'r';
  pre.textContent = e.data;
  document.body.appendChild(pre);
});
</script>
"""


def find_browser():
    """Chrome, Chromium or Edge: whatever this machine has, named or found."""
    named = os.environ.get("GUDANES_CHROME")
    if named:
        return shutil.which(named) or (named if os.path.exists(named) else None)
    for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "chrome", "msedge", "microsoft-edge"):
        found = shutil.which(exe)
        if found:
            return found
    places = []
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            places += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                       os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    places += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
               "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    return next((p for p in places if os.path.exists(p)), None)


def standalone(page):
    """The page as a phone would get it, minus the network.

    The stylesheets it links from /static are put inline, so the frame lays out
    with exactly the CSS the site serves. The web fonts are taken out, so the
    run is the same on a train as on a desk.
    """
    def inline(match):
        tag = match.group(0)
        href = re.search(r'href="([^"]+)"', tag)
        if not href or "stylesheet" not in tag:
            return tag
        url = href.group(1)
        if "fonts.googleapis" in url:
            return ""
        if url.startswith("/static/"):
            path = os.path.join(ROOT, "static", url[len("/static/"):].split("?")[0])
            with open(path, encoding="utf-8") as fh:
                return "<style>\n" + fh.read() + "\n</style>"
        return tag
    page = re.sub(r"<link\b[^>]*>", inline, page)
    page = re.sub(r'<link rel="preconnect"[^>]*>', "", page)
    return page.replace("</body>", PROBE + "</body>")


def measure(browser, frames, workdir):
    """Load every frame at its width in one headless browser; return what each saw."""
    cells = []
    for i, f in enumerate(frames):
        name = f"page{f['page']}.html"
        cells.append(f'<iframe src="{name}#id={i}&w={f["width"]}" '
                     f'style="display:block;border:0;width:{f["width"]}px;height:700px"></iframe>')
    wrapper = os.path.join(workdir, "phones.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER_HEAD + "\n".join(cells) + "\n</body></html>")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--user-data-dir=" + os.path.join(workdir, "profile"),
           "--hide-scrollbars",
           # A phone: a coarse pointer and no hover, as a touch screen reports.
           "--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
           "primaryHoverType=1,availableHoverTypes=1",
           "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=60000", "--dump-dom",
           "file:///" + wrapper.replace("\\", "/").lstrip("/")]
    if sys.platform.startswith("linux"):
        # CI runners refuse Chrome's sandbox; these are our own local files.
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    seen = {}
    for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        d = json.loads(htmllib.unescape(blob))
        seen[int(d["id"])] = d
    return seen, proc


def gap(a, b):
    """Edge-to-edge distance between two boxes; 0 if they touch or overlap."""
    dx = max(b["l"] - a["r"], a["l"] - b["r"], 0)
    dy = max(b["t"] - a["b"], a["t"] - b["b"], 0)
    return (dx * dx + dy * dy) ** 0.5


def overlap(a, b):
    return (min(a["r"], b["r"]) - max(a["l"], b["l"]) > 0.5
            and min(a["b"], b["b"]) - max(a["t"], b["t"]) > 0.5)


def rows(controls):
    """How many rows the header's controls sit on, by where their middles are."""
    middles = sorted((c["t"] + c["b"]) / 2 for c in controls.values() if c["shown"])
    count, last = 0, None
    for y in middles:
        if last is None or y - last > 20:
            count += 1
            last = y
    return count


def run():
    s = Suite("the staff header on a phone")

    s.section("The measuring")
    browser = find_browser()
    if not s.check("a browser to measure with was found",
                   browser, detail="install Chrome or Chromium, or set GUDANES_CHROME "
                                   "to one -- this suite measures and cannot without one"):
        return s

    oc, ec, owner, emp = clients()
    people = (("owner", oc, owner), ("employee", ec, emp))
    conn = db()
    saved = {r["id"]: (r["name"], r["language"]) for r in conn.execute(
        "SELECT id, name, language FROM users WHERE id IN (?, ?)",
        (owner["id"], emp["id"]))}
    # A two-figure count on the bell, as a real morning has: the badge hangs
    # off the bell's corner and must not be what pushes the row past the edge.
    for uid in saved:
        conn.executemany(
            "INSERT INTO notifications (user_id, kind, title, created_at) VALUES (?,?,?,?)",
            [(uid, TAG, f"{TAG} {n}", _harness.datetime_now()) for n in range(12)])
    conn.commit()

    workdir = tempfile.mkdtemp(prefix="gudanes_phone_")
    pages, frames = [], []
    try:
        for role, client, who in people:
            for long_name in (False, True):
                for lang in LANGS:
                    conn.execute("UPDATE users SET name = ?, language = ? WHERE id = ?",
                                 (LONG_NAME if long_name else saved[who["id"]][0],
                                  lang, who["id"]))
                    conn.commit()
                    r = client.get(PAGE)
                    if r.status_code != 200:
                        s.check(f"{PAGE} renders for the {role} in {LANG_NAMES[lang]}",
                                False, r, detail=f"status {r.status_code}")
                        continue
                    pages.append(standalone(r.get_data(as_text=True)))
                    for w in WIDTHS:
                        frames.append({"page": len(pages) - 1, "width": w, "role": role,
                                       "lang": lang, "long": long_name})
    finally:
        for uid, (name, lang) in saved.items():
            conn.execute("UPDATE users SET name = ?, language = ? WHERE id = ?",
                         (name, lang, uid))
        conn.execute("DELETE FROM notifications WHERE kind = ?", (TAG,))
        conn.commit()
        conn.close()

    try:
        # The control: the same page with something 640px wide at the top of
        # it. If this is not reported too wide, no pass below is worth reading.
        control = re.sub(r"<body[^>]*>",
                         lambda tag: tag.group(0) + '<div style="width:640px;height:2px"></div>',
                         pages[0], count=1)
        pages.append(control)
        frames.append({"page": len(pages) - 1, "width": 390, "control": True})
        for i, page in enumerate(pages):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(page)
        try:
            seen, proc = measure(browser, frames, workdir)
        except subprocess.TimeoutExpired:
            s.check("the browser finished measuring", False, detail="timed out after 240s")
            return s
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    missing = [i for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    ctl = [seen[i] for i, f in enumerate(frames) if f.get("control") and i in seen]
    s.check("a page made too wide on purpose is reported too wide",
            ctl and ctl[0]["docW"] > ctl[0]["vw"],
            detail=f"measured {ctl[0]['docW']}px in {ctl[0]['vw']}px" if ctl else "no report")

    real = [(f, seen[i]) for i, f in enumerate(frames) if not f.get("control") and i in seen]
    wrong = [f"{f['role']} {f['lang']} at {f['width']}: frame {d['vw']}px, "
             f"coarse pointer {d['coarse']}, bell {d['controls'].get('bell', {}).get('h')}px tall"
             for f, d in real
             if d["vw"] != f["width"] or not d["coarse"]
             or d["controls"].get("bell", {}).get("h", 0) < 44]
    s.check("each frame is a touch screen at the width asked for, "
            "with the 44px touch rules in force", not wrong, detail="; ".join(wrong))

    def label(f):
        return f"{LANG_NAMES[f['lang']]}" + (", long name" if f["long"] else "")

    def each(group, test):
        bad = []
        for f, d in group:
            why = test(f, d)
            if why:
                bad.append(f"{label(f)}: {why}")
        return bad

    def page_width(f, d):
        if d["docW"] > d["vw"]:
            w = d.get("widest") or {}
            return f"{d['docW']}px wide, furthest is {w.get('what')} at {w.get('right')}px"

    def ink(d):
        return (d.get("name") or {}).get("ink") or []

    def on_screen(f, d):
        off = [k for k, c in d["controls"].items()
               if c["shown"] and (c["l"] < -0.5 or c["r"] > d["vw"] + 0.5)]
        if any(r["l"] < -0.5 or r["r"] > d["vw"] + 0.5 for r in ink(d)):
            off.append("the name")
        return ("past the edge: " + ", ".join(off)) if off else None

    def name_lines(f, d):
        tops = []
        for r in ink(d):
            if all(abs(r["t"] - t) > 2 for t in tops):
                tops.append(r["t"])
        if len(tops) > 2:
            return f"{len(tops)} lines"

    def nothing_hidden(f, d):
        want = EVERYONE + (OWNER_ONLY if f["role"] == "owner" else [])
        gone = [k for k in want if not d["controls"].get(k, {}).get("shown")]
        name = d.get("name") or {}
        room = min(name.get("natural", 0), NAME_AT_LEAST)
        if not name.get("shown") or name.get("w", 0) < room - 0.5:
            gone.append(f"the name ({round(name.get('w', 0))}px of "
                        f"{round(name.get('natural', 0))}px showing)")
        if not (d.get("badge") or {}).get("shown"):
            gone.append("the role")
        return ("not shown: " + ", ".join(gone)) if gone else None

    def no_overlap(f, d):
        shown = [(k, c) for k, c in d["controls"].items() if c["shown"]]
        hits = [f"{a} and {b}" for n, (a, ca) in enumerate(shown)
                for b, cb in shown[n + 1:] if overlap(ca, cb)]
        # The name is not a control, but words printed across one are.
        hits += sorted({f"the name and {k}" for r in ink(d) for k, c in shown
                        if overlap(r, c)})
        return ("overlapping: " + ", ".join(hits)) if hits else None

    def logout_size(f, d):
        lo = d["controls"].get("logout")
        if not lo:
            return "no Log out"
        if lo["w"] < 44 - 0.5 or lo["h"] < 44 - 0.5:
            return f"{lo['w']:.0f}x{lo['h']:.0f}"

    def logout_clear(f, d):
        lo = d["controls"].get("logout")
        if not lo:
            return "no Log out"
        near = [(gap(lo, c), k) for k, c in d["controls"].items()
                if k != "logout" and c["shown"]]
        dist, what = min(near)
        if dist < LOGOUT_CLEARANCE - 0.5:
            return f"{dist:.0f}px from {what}"

    def logout_far_end(f, d):
        lo = d["controls"].get("logout")
        if not lo or "innerRight" not in d:
            return "no Log out"
        if lo["r"] < d["innerRight"] - 1:
            return f"ends at {lo['r']:.0f}px, the row at {d['innerRight']:.0f}px"

    def account_one_row(f, d):
        middles = sorted((c["t"] + c["b"]) / 2 for k, c in d["controls"].items()
                         if k in LABELLED and c["shown"])
        if middles and middles[-1] - middles[0] > 20:
            return "split over more than one row"

    def one_line(f, d):
        folded = [f"{k} ({d['controls'][k]['lines']} lines)" for k in LABELLED
                  if k in d["controls"] and d["controls"][k]["lines"] > 1]
        return ("folded: " + ", ".join(folded)) if folded else None

    everywhere = [
        ("the page is no wider than the screen", page_width),
        ("every control is on the screen", on_screen),
        ("no control sits on another", no_overlap),
        ("Log out is at least 44x44", logout_size),
        (f"Log out is at least {LOGOUT_CLEARANCE}px from every other control", logout_clear),
        ("every label stays on one line", one_line),
        # A long name squeezed into a column is not room found for it.
        ("the name takes no more than two lines", name_lines),
    ]
    for role, _client, _who in people:
        shown_list = ", ".join(EVERYONE + (OWNER_ONLY if role == "owner" else []))
        for w in WIDTHS:
            group = [(f, d) for f, d in real if f["role"] == role and f["width"] == w]
            where = ("the narrowest screen laid out as on a desk -- " if w == NARROWEST_DESK
                     else "") + "English, French, Spanish; their own name and a long one"
            s.section(f"The {role}'s header at {w}px ({where})")
            if not group:
                s.check("measured at all", False, detail="no frame reported")
                continue
            checks = everywhere + [
                (f"nothing is hidden: {shown_list}, the name and the role", nothing_hidden)]
            if w in PHONES:
                checks.append(("Log out sits at the far end of its row", logout_far_end))
            else:
                # With this much room the strip breaks between its two groups,
                # never inside the second: Log out left alone under Backup is
                # the ragged edge the wrap is there to prevent.
                checks.append(("the account links share one row", account_one_row))
            for name, test in checks:
                bad = each(group, test)
                s.check(name, not bad, detail="; ".join(bad))
            if w in (375, 390):
                limit = ROWS_BEFORE[role]
                bad = each([(f, d) for f, d in group if f["lang"] == "en"],
                           lambda f, d: (f"{rows(d['controls'])} rows"
                                         if rows(d["controls"]) > limit else None))
                s.check(f"in English it takes no more rows than before: {limit}",
                        not bad, detail="; ".join(bad))
    return s


if __name__ == "__main__":
    print(run().report())
