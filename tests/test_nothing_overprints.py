"""Words printed over words, and figures printed out of their tiles.

WHAT WENT WRONG. Two faults on staff pages, both seen in a screenshot on
29 September 2026, neither of which any check could have noticed, because
both pages rendered every word they were meant to.

    The fridge log. Under each unit, the hint ("Should sit between 0°C and
    5°C") is followed by a disclaimer ("2 readings outside the range with
    nothing written against them"). The disclaimer carries margin-top:-6px,
    which is right under a form or a table and wrong under a hint that has no
    margin of its own: its first line printed 6px into the hint's last, at a
    desk and on a phone alike.

    The event agreement. Five tiles -- agreed, received, still owed, not yet
    scheduled, next -- in a 150px-minimum grid. These tiles hold a label and a
    figure straight inside, with no icon and no body, and the tile is a flex
    ROW, so "€25000.00" sat beside "AGREED" instead of under it and ended
    41px past the tile's padding at 1100px (65px for "€10000.00" beside "NOT
    YET SCHEDULED"), and 33-57px on a 390px phone. The margin, guest-list,
    party, split-bill and pay-statement pages write their tiles the same way.

WHY A BROWSER. Both are sums -- a margin against a line box, a label's width
plus a figure's against a grid column that depends on the screen -- and only
layout can do them. So the real pages are rendered through the test client,
loaded into headless Chrome as a touch screen (the iPad by the kitchen door,
and a phone) and measured, using the plumbing of test_staff_header_on_a_phone.

WHAT IS MEASURED. The ink, not the boxes: each line of text as the browser
set it. A hint and the disclaimer under it must not share a pixel row. Every
tile's figure must end inside the tile's padding, sit on one line, and share a
top with the other figures in its row, so a two-line label does not knock one
figure out of line with its neighbours.

THE MEASURING IS CHECKED TOO. The same pages are measured again with the old
two rules put back in a <style> of their own, and must then be reported as
overprinted and clipped. If that control passes, nothing below is worth
reading. No browser is a failure, not a skip; set GUDANES_CHROME.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta

from _harness import Suite, clients, db
import _harness
import test_staff_header_on_a_phone as phone

m = _harness.m
TAG = "ZZOVERPRINT"
# 1100: a desk, and an iPad on its side. 390 and 320: a phone, and the
# narrowest still about.
WIDTHS = (1100, 390, 320)

# The two rules as they were, to put back for the control.
OLD_RULES = """<style>
.upload-hint + .pay-disclaimer{ margin-top:-6px; }
.stat-tile:has(> .stat-tile-label){ flex-direction:row; align-items:center; justify-content:normal; gap:13px; }
</style>"""

PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function ink(el) {
    var r = document.createRange(); r.selectNodeContents(el);
    return [].filter.call(r.getClientRects(), function (x) {
      return x.width >= 1 && x.height >= 1;
    });
  }
  function tops(rects) {
    var t = [];
    rects.forEach(function (x) {
      if (t.every(function (y) { return Math.abs(y - x.top) > 2; })) t.push(x.top);
    });
    return t;
  }
  function go() {
    var de = document.documentElement, out = {
      id: q.id, asked: +q.w, vw: de.clientWidth, docW: de.scrollWidth,
      coarse: matchMedia('(pointer: coarse)').matches, pairs: [], tiles: []};
    [].forEach.call(document.querySelectorAll('.upload-hint + .pay-disclaimer'), function (b) {
      var a = b.previousElementSibling, worst = 0;
      ink(a).forEach(function (x) { ink(b).forEach(function (y) {
        var h = Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top);
        var w = Math.min(x.right, y.right) - Math.max(x.left, y.left);
        if (h > 0 && w > 0) worst = Math.max(worst, h);
      }); });
      out.pairs.push({hint: a.textContent.trim().replace(/\s+/g, ' ').slice(0, 40),
                      overlap: worst});
    });
    [].forEach.call(document.querySelectorAll('.stat-tile'), function (t) {
      var v = t.querySelector('.stat-tile-value');
      if (!v) return;
      var tr = t.getBoundingClientRect(), cs = getComputedStyle(t), lines = ink(v);
      var right = Math.max.apply(null, lines.map(function (x) { return x.right; }));
      out.tiles.push({
        figure: v.textContent.trim(), bare: !!t.querySelector(':scope > .stat-tile-label'),
        tileTop: tr.top,
        past: right - (tr.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth)),
        lines: tops(lines).length,
        top: Math.min.apply(null, lines.map(function (x) { return x.top; }))});
    });
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 0); });
})();
</script>
"""


def _cleanup(conn):
    conn.execute("""DELETE FROM fridge_readings WHERE unit_id IN
                    (SELECT id FROM fridge_units WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM fridge_units WHERE name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM event_instalments WHERE event_id IN
                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()


def _event(conn, ref, price, paid, stages):
    when = m.house_today() + timedelta(days=300)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, end_date, guest_count,
           status, quoted_price, amount_paid, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, ?, 80, 'confirmed', ?, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG}.{ref}@example.invalid".lower(), when.isoformat(),
         (when + timedelta(days=1)).isoformat(), price, paid, _harness.datetime_now()))
    eid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    with m.app.test_request_context("/"):
        for label, amount, days in stages:
            _row, err = m.add_event_instalment(
                conn, eid, label, str(amount),
                (m.house_today() + timedelta(days=days)).isoformat())
            if err:
                raise AssertionError(f"instalment {label}: {err}")
    return eid


def run():
    s = Suite("nothing overprints")

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
        now = _harness.datetime_now()
        conn.execute("""INSERT INTO fridge_units (name, where_it_is, min_c, max_c, active,
                        created_at) VALUES (?, 'Back kitchen', 0, 5, 1, ?)""",
                     (TAG + " Walk-in", now))
        unit = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        # Two out of range and nobody said anything: that is what puts the
        # disclaimer under the hint.
        for celsius, days in ((3.8, 3), (9.1, 2), (8.4, 1)):
            at = (m.datetime.now(m.timezone.utc) - timedelta(days=days)).isoformat()
            conn.execute("""INSERT INTO fridge_readings (unit_id, read_at, celsius,
                            read_by_user_id, action_taken, note, created_at)
                            VALUES (?, ?, ?, NULL, NULL, NULL, ?)""", (unit, at, celsius, at))
        conn.commit()
        # The wedding from the screenshot, with a "next" stage so all five
        # tiles show; and a big one, nothing paid yet, for the widest figures.
        usual = _event(conn, "W", 25000, 10000, (("Deposit", 5000, 2), ("Second", 10000, 100)))
        big = _event(conn, "B", 125000, 0, (("Deposit", 25000, 20),))

        pages = []        # (name, html)
        for name, url in (("the fridge log", "/kitchen/fridges"),
                          ("the agreement for a €25,000 wedding", f"/admin/events/{usual}/agreement"),
                          ("the agreement for a €125,000 one", f"/admin/events/{big}/agreement"),
                          ("the margin for a €125,000 one", f"/admin/events/{big}/margin"),
                          ("the guest list", f"/admin/events/{usual}/guests")):
            r = oc.get(url)
            if not s.check(f"{name} renders", r.status_code == 200, r,
                           detail=f"status {r.status_code}"):
                continue
            page = phone.standalone(r.get_data(as_text=True))
            if not s.check(f"{name} can be fitted with the probe", phone.PROBE in page):
                continue
            pages.append((name, page.replace(phone.PROBE, PROBE)))
    finally:
        _cleanup(conn)
        conn.close()
    if not pages:
        return s

    frames, files = [], []
    for control in (False, True):
        for name, page in pages:
            if control:
                page = page.replace("</head>", OLD_RULES + "</head>", 1)
            files.append(page)
            for w in WIDTHS:
                frames.append({"page": len(files) - 1, "width": w, "name": name,
                               "control": control})

    workdir = tempfile.mkdtemp(prefix="gudanes_overprint_")
    try:
        for i, page in enumerate(files):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(page)
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
    wrong = [f"{f['name']} at {f['width']}: frame {d['vw']}px, coarse {d['coarse']}"
             for f, d in got if d["vw"] != f["width"] or not d["coarse"]]
    s.check("each frame is a touch screen at the width asked for", not wrong,
            detail="; ".join(wrong))

    def overprinted(d):
        return [f"\"{p['hint']}\" by {p['overlap']:.1f}px" for p in d["pairs"]
                if p["overlap"] > 0.5]

    def clipped(d):
        return [f"{t['figure']} by {t['past']:.1f}px" for t in d["tiles"] if t["past"] > 0.5]

    def folded(d):
        return [f"{t['figure']} on {t['lines']} lines" for t in d["tiles"] if t["lines"] > 1]

    def out_of_line(d):
        rows = {}
        for t in d["tiles"]:
            rows.setdefault(round(t["tileTop"]), []).append(t)
        bad = []
        for row in rows.values():
            spread = max(t["top"] for t in row) - min(t["top"] for t in row)
            if spread > 1:
                bad.append(" / ".join(t["figure"] for t in row) + f" ({spread:.0f}px apart)")
        return bad

    def too_wide(d):
        return [f"{d['docW']}px in {d['vw']}px"] if d["docW"] > d["vw"] else []

    s.section("The control: the old rules put back must be caught")
    ctl = [(f, d) for f, d in got if f["control"]]
    caught_print = [f"{f['name']} at {f['width']}" for f, d in ctl if overprinted(d)]
    caught_clip = [f"{f['name']} at {f['width']}" for f, d in ctl if clipped(d)]
    s.check("the fridge log is reported overprinted at every width",
            len([c for c in caught_print if c.startswith("the fridge log")]) == len(WIDTHS),
            detail="caught at: " + (", ".join(caught_print) or "nowhere"))
    s.check("the €25,000 agreement is reported clipped at 1100px and 390px",
            all(f"the agreement for a €25,000 wedding at {w}" in caught_clip for w in (1100, 390)),
            detail="caught at: " + (", ".join(caught_clip) or "nowhere"))

    real = [(f, d) for f, d in got if not f["control"]]
    s.section("There is something to measure")
    fridge = [d for f, d in real if f["name"] == "the fridge log"]
    s.check("the fridge log has a hint with a disclaimer under it",
            fridge and all(d["pairs"] for d in fridge),
            detail=f"{[len(d['pairs']) for d in fridge]}")
    agreement = [d for f, d in real if f["name"] == "the agreement for a €25,000 wedding"]
    s.check("the agreement shows its five tiles, written bare",
            agreement and all(sum(t["bare"] for t in d["tiles"]) == 5 for d in agreement),
            detail=f"{[[t['figure'] for t in d['tiles']] for d in agreement]}")

    for w in WIDTHS:
        group = [(f, d) for f, d in real if f["width"] == w]
        s.section(f"At {w}px")

        def each(test):
            return "; ".join(f"{f['name']}: " + ", ".join(why)
                             for f, d in group for why in [test(d)] if why)

        for label, test in (
                ("no disclaimer prints over the hint above it", overprinted),
                ("every figure ends inside its tile", clipped),
                ("every figure is on one line", folded),
                ("the figures in a row of tiles share a line", out_of_line),
                ("the page is no wider than the screen", too_wide)):
            bad = each(test)
            s.check(label, not bad, detail=bad)
    return s


if __name__ == "__main__":
    print(run().report())
