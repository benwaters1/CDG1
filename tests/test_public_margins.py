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

Later the same day the guests' own pages were measured too -- a stay's
confirmation and manage page, the statement, an atelier's, an event's and a
dinner's pages, the account and the guest's own page, the check-ins and the
pages a booking is found from. They are in no sitemap, so rows are made here
for them and removed after. They added three more:

  - THE STATEMENT ENDED ON THE MONUMENT BAND, its last line 3-10px above it
    at every width: the page's wrapper padded its sides and nothing under.
  - THE MANAGE PAGES SET THEIR OWN EDGE, 20px on a phone and 31px on a
    tablet, out of line with the site's gutter and the header above them;
    and their framed cards kept 32px of padding on a phone, so a reference
    framed inside a card stood 81px in on a 390px screen.
  - EVERY PANEL KEPT 32PX SIDES AT EVERY WIDTH. Two rules said 24px from a
    tablet down; a later one overrode both, on public pages as well.

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
  - a page that pads main itself keeps the same gutter while main runs the
    width of the screen;
  - the last thing in main ends where main ends, and the reassurance band
    meets the footer: no strips;
  - the last words above the Monument band stand at least 24px clear of it;
  - on a phone, framed cards and panels are padded 24px at the sides;
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
from datetime import datetime, timedelta, timezone

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
    var main = document.querySelector('main'), assure = document.querySelector('.g-assure'),
        foot = document.querySelector('footer.g-footer');
    // A page that pads main itself (the manage pages) is its own container
    // while main runs the width of the screen.
    if (main) {
      var mcs = getComputedStyle(main);
      if ((parseFloat(mcs.paddingLeft) || parseFloat(mcs.paddingRight))
          && main.getBoundingClientRect().width >= vw - 1)
        gutters.push(['main', parseFloat(mcs.paddingLeft), parseFloat(mcs.paddingRight)]);
    }
    out.gutters = gutters;

    // The cards and panels a phone drops to 24px of padding.
    out.panels = [];
    [].forEach.call(document.querySelectorAll('main .g-mb-card, main .g-panel'), function (el) {
      if (!shown(el) || el.closest(SKIP)) return;
      var cs = getComputedStyle(el);
      if (parseFloat(cs.borderLeftWidth) || cs.backgroundColor !== 'rgba(0, 0, 0, 0)')
        out.panels.push([name(el) + ' "' + words(el) + '"', parseFloat(cs.paddingLeft)]);
    });

    var last = null;
    [].forEach.call(main ? main.children : [], function (c) { if (shown(c)) last = c; });
    function y(v) { return v + window.scrollY; }
    // How far the last line of words above the Monument band stands from it.
    var cred = main ? main.querySelector(':scope > .g-cred') : null, credGap = null;
    if (cred && shown(cred)) {
      var prev = cred.previousElementSibling;
      while (prev && !shown(prev)) prev = prev.previousElementSibling;
      var low = null;
      [prev].concat(prev ? [].slice.call(prev.querySelectorAll('*')) : []).forEach(function (el) {
        if (!el || !shown(el) || el.closest('[aria-hidden="true"]')) return;
        [].forEach.call(el.childNodes, function (n) {
          if (n.nodeType !== 3 || !n.textContent.trim()) return;
          var range = document.createRange();
          range.selectNodeContents(n);
          [].forEach.call(range.getClientRects(), function (x) {
            if (x.height > 4 && (low === null || x.bottom > low)) low = x.bottom;
          });
        });
      });
      if (low !== null) credGap = cred.getBoundingClientRect().top - low;
    }
    out.run = {
      last: last ? name(last) : null,
      lastB: last ? y(last.getBoundingClientRect().bottom) : null,
      mainB: main ? y(main.getBoundingClientRect().bottom) : null,
      assureT: assure ? y(assure.getBoundingClientRect().top) : null,
      assureB: assure ? y(assure.getBoundingClientRect().bottom) : null,
      footT: foot ? y(foot.getBoundingClientRect().top) : null,
      credGap: credGap
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
.g main > .g-close{ padding-bottom: 0 !important; }
</style>"""

BAND_CLEAR = 24   # px: the last words above the Monument band stand at least this far off it
PANEL_PAD = 24    # px: what a card or panel is padded on a phone

# The guests' own pages: reached by the link in a booking's letter, in no
# sitemap, and so measured from rows made here (and removed after).
GUEST = "zmargin-"
GUEST_NAME = "Marie-Christine de La Rochefoucauld"   # long, so it wraps where it can


def _seed_guests(conn):
    """One of each private page's rows; the paths to measure."""
    _unseed_guests(conn)
    now = datetime.now(timezone.utc).isoformat()
    today = m.house_today()
    later = lambda days: (today + timedelta(days=days)).isoformat()
    room = _harness.ensure_room()
    email = GUEST + "guest@example.invalid"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, share_token,
               guest_name, guest_email, guest_phone, arrival_date, departure_date,
               party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, '+33 6 00 00 00 00', ?, ?, 2, 'confirmed', 960, ?)""",
        (room["id"], GUEST + "R1", GUEST + "room", GUEST + "share", GUEST_NAME, email,
         later(40), later(43), now))
    # A room the house is holding for a guest to finish booking.
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status,
               total_price, reserved_until, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'pending', 960, ?, ?)""",
        (room["id"], GUEST + "R2", GUEST + "held", GUEST_NAME, email, later(50), later(52),
         (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(), now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
               contact_name, contact_email, created_at) VALUES (?, ?, 'wedding', ?, ?, ?)""",
        (GUEST + "E1", GUEST + "event", GUEST_NAME, email, now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
               guest_email, party_size, dinner_date, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', 130, ?)""",
        (GUEST + "D1", GUEST + "rest", GUEST_NAME, email, later(40), now))
    conn.execute(
        "INSERT INTO guest_sessions (email, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (email, GUEST + "acct", now, (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()))
    conn.execute(
        "INSERT INTO guests (name, email, portal_token, created_at) VALUES (?, ?, ?, ?)",
        (GUEST_NAME, email, GUEST + "portal", now))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ota_reservations)")}
    ota = {"reservation_number": GUEST + "OTA1", "status": "confirmed", "first_seen_at": now,
           "updated_at": now, "checkin_token": GUEST + "ota", "room_id": room["id"],
           "arrival_date": later(40), "departure_date": later(43), "guest_name": GUEST_NAME,
           "guest_email": GUEST + "ota@example.invalid"}
    ota = {k: v for k, v in ota.items() if k in cols}
    conn.execute("INSERT INTO ota_reservations (%s) VALUES (%s)"
                 % (", ".join(ota), ", ".join("?" * len(ota))), list(ota.values()))
    paths = ["/book/confirmation/%sroom", "/book/manage/%sroom", "/booking/%sroom/statement",
             "/booking/%sheld/finish", "/feedback/%sroom", "/stay/%sshare", "/checkin/stay/%sota",
             "/events/confirmation/%sevent", "/events/manage/%sevent",
             "/restaurant/confirmation/%srest", "/restaurant/manage/%srest",
             "/my-account/%sacct", "/my/%sportal"]
    paths = [p % GUEST for p in paths]
    session = conn.execute(
        """SELECT workshop_sessions.id FROM workshop_sessions
             JOIN workshops ON workshops.id = workshop_sessions.workshop_id
            WHERE workshops.active = 1 AND workshop_sessions.start_date >= ?
            ORDER BY workshop_sessions.start_date LIMIT 1""", (today.isoformat(),)).fetchone()
    if session:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                   guest_name, guest_email, party_size, status, created_at)
               VALUES (?, ?, ?, ?, ?, 2, 'confirmed', ?)""",
            (session["id"], GUEST + "W1", GUEST + "ws", GUEST_NAME, email, now))
        paths += ["/workshops/confirmation/%sws" % GUEST, "/workshops/manage/%sws" % GUEST,
                  "/workshops/register/%d" % session["id"]]
    conn.commit()
    # And the pages a guest finds a booking from, which need no row at all.
    return paths + ["/book/manage", "/events/find", "/restaurant/find", "/workshops/find",
                    "/my-account"]


def _unseed_guests(conn):
    like = GUEST + "%"
    for table, col in (("bookings", "reference_code"), ("workshop_bookings", "reference_code"),
                       ("event_inquiries", "reference_code"), ("restaurant_bookings", "reference_code"),
                       ("guest_sessions", "token"), ("guests", "portal_token"),
                       ("ota_reservations", "reservation_number")):
        conn.execute("DELETE FROM %s WHERE %s LIKE ?" % (table, col), (like,))
    conn.commit()


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
    if run.get("credGap") is not None and run["credGap"] < BAND_CLEAR:
        out.append("words %dpx above the Monument band" % run["credGap"])
    if w <= PHONE_GUTTER_UNTIL:
        fat = ["%s %gpx" % (n, pad) for n, pad in d.get("panels", []) if pad > PANEL_PAD]
        if fat:
            out.append("a panel padded past %dpx on a phone: %s" % (PANEL_PAD, "; ".join(fat[:3])))
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

        conn = db()
        guest_paths = _seed_guests(conn)
        conn.close()
        guests = _render(anon, guest_paths)
        s.check("and every guest's own page renders to be measured too",
                len(guests) == len(guest_paths) and len(guest_paths) >= 20,
                detail="rendered %s of %s" % ([p for p, _ in guests], guest_paths))
        pages += guests

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
        s.check("for the booking bar over the footer's last line",
                "covers the footer's last line" in said, detail=said)
        s.check("and for words pressed against the Monument band",
                "above the Monument band" in said, detail=said)

        real = [d for d in frames if not d["page"].startswith("control:")]
        for width, label in ((PHONE, "on a phone (390px)"), (TABLET, "on a tablet (768px)"),
                             (DESK, "on a desk (1440px)")):
            s.section("Every public page, and every guest's own, " + label)
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
            bad = each(lambda f: "above the Monument band" in f)
            s.check("the last words stand at least %dpx clear of the Monument band" % BAND_CLEAR,
                    not bad, detail=" | ".join(bad[:4]))
            if width <= PHONE_GUTTER_UNTIL:
                bad = each(lambda f: "a panel padded past" in f)
                s.check("cards and panels drop to %dpx of padding" % PANEL_PAD,
                        not bad, detail=" | ".join(bad[:4]))
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
        conn = db()
        _unseed_guests(conn)
        conn.close()
        if was_enabled is not None:
            conn = db()
            conn.execute("UPDATE restaurant_settings SET enabled = ? WHERE id = 1",
                         (was_enabled["enabled"],))
            conn.commit()
            conn.close()
        shutil.rmtree(workdir, ignore_errors=True)
    return s
