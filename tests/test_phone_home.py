"""The phone's home screen (/app): big tiles, the right ones, and it fits.

WHAT WAS MISSING. On 19 October 2026 the owner asked what had happened to the
phone app's easy icons. There never were any: the home-screen app
(static/manifest.json) opened "/", which is the full dashboard -- a page for a
desk, with a hundred links behind a menu button. /app is the screen that was
expected: a grid of large tiles, and the manifest now starts there.

WHAT IS ASSERTED.

- The manifest opens the home-screen app on /app, and /app is this page.
- The tiles are the sidebar's own items (phone=True in app.py), so an
  employee's tiles are drawn from STAFF_MENU and the owner's from OWNER_MENU.
- AN EMPLOYEE NEVER SEES AN OWNER-ONLY TILE. Every tile an employee is shown
  is opened by the employee's own client and must answer, not refuse; none is
  an owner-only page. And the guard is proved, not assumed: an owner-only page
  flagged into the employee's menu on purpose still does not appear, and with
  the guard stood down for one request it DOES -- so the check can fail.
- The Today tile carries the number of the person's tasks still to do.
- It fits a phone, measured in headless Chrome with a touch screen emulated
  (the machinery is test_staff_header_on_a_phone's): nothing past the edge at
  320-390px, three tiles a row at 320, every tile far past the 44px touch
  floor, no tile over another, no label out of its tile. A page made too wide
  on purpose must be reported too wide. No browser is a failure, not a skip.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

from _harness import Suite, clients, db
import _harness
import test_staff_header_on_a_phone as phone

m = _harness.m
TAG = "ZZPHONEAPP"
PHONES = (390, 375, 360, 320)
BIG = 80          # px: a tile, not a link -- near twice the 44px touch floor


def tiles_of(page):
    """[(endpoint, href, label)] for each tile on the page, in order."""
    return [(ep, href, re.sub(r"\s+", " ", label).strip()) for href, ep, label in re.findall(
        r'<a href="([^"]+)" class="ph-tile[^"]*" data-endpoint="([^"]+)">.*?'
        r'<span class="ph-label">(.*?)</span>', page, re.S)]


PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function box(el) {
    var r = el.getBoundingClientRect();
    return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height};
  }
  function go() {
    var de = document.documentElement, out = {
      id: q.id, asked: +q.w, vw: de.clientWidth, docW: de.scrollWidth,
      coarse: matchMedia('(pointer: coarse)').matches, tiles: []};
    [].forEach.call(document.querySelectorAll('.ph-tile'), function (a) {
      var b = box(a), lab = a.querySelector('.ph-label'), lb = box(lab);
      b.ep = a.getAttribute('data-endpoint');
      b.label = {l: lb.l, r: lb.r, t: lb.t, b: lb.b,
                 spill: lab.scrollWidth > lab.clientWidth + 1};
      out.tiles.push(b);
    });
    var c = document.querySelector('.ph-clock');
    if (c) out.clock = box(c);
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () {
    var ready = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
    ready.then(function () { setTimeout(go, 0); });
  });
})();
</script>
"""


def as_phone(page):
    """The page with its CSS inline and this suite's probe in place of the
    header suite's."""
    return phone.standalone(page).replace(phone.PROBE, PROBE)


def run():
    s = Suite("the phone's home screen")
    oc, ec, owner, emp = clients()
    conn = db()
    saved_preset = conn.execute("SELECT access_preset FROM users WHERE id = ?",
                                (emp["id"],)).fetchone()[0]
    # An employee with no areas: the person this page must be most careful of.
    conn.execute("UPDATE users SET access_preset = NULL WHERE id = ?", (emp["id"],))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    try:
        _checks(s, oc, ec, owner, emp)
    finally:
        conn = db()
        conn.execute("UPDATE users SET access_preset = ? WHERE id = ?",
                     (saved_preset, emp["id"]))
        conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()
    return s


def _checks(s, oc, ec, owner, emp):
    s.section("The home-screen app opens here")
    manifest = json.load(open(os.path.join(_harness.ROOT, "static", "manifest.json"),
                              encoding="utf-8"))
    s.check("the manifest starts the app on /app", manifest.get("start_url") == "/app",
            detail=f"start_url is {manifest.get('start_url')!r}")
    with m.app.test_request_context():
        s.check("and /app is the phone's home screen",
                m.app.url_map.bind("localhost").match("/app")[0] == "phone_home")
    s.check("the manifest still installs standalone, inside the whole site",
            manifest.get("display") == "standalone" and manifest.get("scope") == "/")
    anon = m.app.test_client().get("/app")
    s.check("somebody signed out is sent to sign in, not shown the tiles",
            anon.status_code in (301, 302) and "login" in anon.headers.get("Location", ""),
            anon)

    r_owner = oc.get("/app")
    r_emp = ec.get("/app")
    s.check("the owner's home screen answers", r_owner.status_code == 200, r_owner)
    s.check("an employee's home screen answers", r_emp.status_code == 200, r_emp)
    owner_page = r_owner.get_data(as_text=True)
    emp_page = r_emp.get_data(as_text=True)
    own_tiles, emp_tiles = tiles_of(owner_page), tiles_of(emp_page)
    own_eps = [t[0] for t in own_tiles]
    emp_eps = [t[0] for t in emp_tiles]

    s.section("Each person sees what they can open")
    flagged = {i["endpoint"] for i in m.NAV_TOP if i["phone"]}
    s.check("the tiles come from the sidebar: every one is a nav item marked for the phone",
            all(ep in flagged or ep == "admin_home"
                or any(it["endpoint"] == ep and it["phone"]
                       for menu in (m.OWNER_MENU, m.STAFF_MENU)
                       for g in menu for _h, items in g[5] for it in items)
                for ep in own_eps + emp_eps),
            detail=f"owner {own_eps}; employee {emp_eps}")
    for ep in ("staff_today", "ops_calendar", "chat_home"):
        s.check(f"both get the {ep} tile", ep in own_eps and ep in emp_eps,
                detail=f"owner {own_eps}; employee {emp_eps}")
    for ep in ("admin_approvals", "admin_tasks", "front_desk", "admin_restaurant", "pos_home"):
        s.check(f"the owner gets {ep}", ep in own_eps, detail=str(own_eps))
    for ep in ("pos_home", "breakfast", "my_shifts", "room_checks_page"):
        s.check(f"the employee gets {ep}", ep in emp_eps, detail=str(emp_eps))
    s.check("an employee gets a handful, the owner more",
            4 <= len(emp_eps) <= 12 and len(own_eps) > len(emp_eps),
            detail=f"employee {len(emp_eps)}, owner {len(own_eps)}")
    s.check("a phone screen, not a sidebar: no more than sixteen tiles for anybody",
            len(own_eps) <= 16, detail=f"the owner has {len(own_eps)}")
    s.check("no tile twice", len(set(own_eps)) == len(own_eps)
            and len(set(emp_eps)) == len(emp_eps))
    s.check("the computer's dashboard is one tile away",
            own_eps[-1:] == ["admin_home"] and emp_eps[-1:] == ["admin_home"])

    with m.app.test_request_context():
        emp_row = db().execute("SELECT * FROM users WHERE id = ?", (emp["id"],)).fetchone()
        owner_only = [ep for ep in emp_eps if ep in m.OWNER_ONLY_ENDPOINTS
                      or (m.view_is_guarded(ep) and not m.can_reach(emp_row, ep))]
    s.check("AN EMPLOYEE IS SHOWN NO OWNER-ONLY TILE", not owner_only,
            detail=f"shown {owner_only}")
    refused = []
    for ep, href, _label in emp_tiles:
        r = ec.get(href)
        if r.status_code != 200:
            refused.append(f"{ep} {r.status_code}")
    s.check("every tile an employee is shown, the employee can open",
            not refused, detail="; ".join(refused))

    s.section("The guard holds even when the menu is wrong")
    planted = m._nav("admin_approvals", "Approvals", "approvals", phone=True)
    m.STAFF_MENU[0][5][0][1].append(planted)
    try:
        leaked = ec.get("/app").get_data(as_text=True)
        s.check("an owner-only page flagged into the employee's menu by mistake "
                "still gets no tile", 'data-endpoint="admin_approvals"' not in leaked)
        real_reach = m.can_reach
        m.can_reach = lambda user, endpoint: True
        try:
            unguarded = ec.get("/app").get_data(as_text=True)
        finally:
            m.can_reach = real_reach
        s.check("negative control: with the guard stood down it does appear, "
                "so the check above can fail", 'data-endpoint="admin_approvals"' in unguarded)
    finally:
        m.STAFF_MENU[0][5][0][1].remove(planted)
    s.check("and the menu is put back", 'data-endpoint="admin_approvals"' not in
            ec.get("/app").get_data(as_text=True))

    s.section("The numbers on the tiles")
    conn = db()
    today = m.house_today_iso()
    before = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE assigned_to_user_id = ? AND status != 'done' "
        "AND (due_date IS NULL OR due_date <= ?)", (emp["id"], today)).fetchone()[0]
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(tasks)")}
    for n in range(3):
        row = {"title": f"{TAG} {n}", "assigned_to_user_id": emp["id"], "status": "open",
               "due_date": today, "created_at": _harness.datetime_now()}
        row = {k: v for k, v in row.items() if k in cols}
        conn.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                     tuple(row.values()))
    conn.commit()
    conn.close()
    page = ec.get("/app").get_data(as_text=True)
    badge = re.search(r'data-endpoint="staff_today">.*?(?:class="ph-badge"[^>]*>(\d+)<|</a>)',
                      page, re.S)
    s.check("the Today tile counts the tasks still to do",
            badge and badge.group(1) == str(before + 3),
            detail=f"badge {badge.group(1) if badge else None}, expected {before + 3}")
    s.check("the clock is on the screen, the first and last tap of a shift",
            'class="ph-clock' in page)

    s.section("The computer's dashboard is unchanged")
    dash = oc.get("/admin")
    s.check("/admin is still the dashboard, not the tiles",
            dash.status_code == 200 and 'class="ph-grid"' not in dash.get_data(as_text=True),
            dash)

    s.section("It fits a phone")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found", browser,
                   detail="install Chrome or Chromium, or set GUDANES_CHROME -- "
                          "this check measures and cannot without one"):
        return
    pages = [as_phone(owner_page), as_phone(emp_page)]
    frames = [{"page": p, "width": w, "who": who}
              for p, who in ((0, "owner"), (1, "employee")) for w in PHONES]
    control = re.sub(r"<body[^>]*>",
                     lambda tag: tag.group(0) + '<div style="width:640px;height:2px"></div>',
                     pages[0], count=1)
    pages.append(control)
    frames.append({"page": 2, "width": 320, "control": True})
    workdir = tempfile.mkdtemp(prefix="gudanes_phonehome_")
    try:
        for i, p in enumerate(pages):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(p)
        try:
            seen, proc = phone.measure(browser, frames, workdir)
        except subprocess.TimeoutExpired:
            s.check("the browser finished measuring", False, detail="timed out")
            return
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    s.check(f"every frame reported back ({len(seen)} of {len(frames)})",
            len(seen) == len(frames),
            detail=" | ".join(proc.stderr.strip().splitlines()[-3:]))
    ctl = seen.get(len(frames) - 1)
    s.check("a page made too wide on purpose is reported too wide",
            ctl and ctl["docW"] > ctl["vw"],
            detail=f"{ctl['docW']}px in {ctl['vw']}px" if ctl else "no report")
    real = [(f, seen[i]) for i, f in enumerate(frames) if not f.get("control") and i in seen]
    s.check("each frame is a touch screen at the width asked for",
            real and all(d["vw"] == f["width"] and d["coarse"] for f, d in real),
            detail="; ".join(f"{f['width']}: {d['vw']}px coarse={d['coarse']}"
                             for f, d in real))
    s.check("the tiles were found on every frame",
            all(len(d["tiles"]) >= 4 for _f, d in real))

    def each(test):
        return [f"{f['who']} at {f['width']}: {msg}" for f, d in real for msg in test(d)]

    def none_of(name, bad):
        s.check(name, not bad, detail="; ".join(bad[:8]))

    none_of("nothing runs past the edge of the screen",
            each(lambda d: ([f"page {d['docW']}px wide"] if d["docW"] > d["vw"] else [])
                     + [f"{t['ep']} {t['l']:.0f}-{t['r']:.0f}" for t in d["tiles"]
                        if t["l"] < 0 or t["r"] > d["vw"] + 0.5]))
    none_of(f"every tile is big: at least {BIG}px each way",
            each(lambda d: [f"{t['ep']} {t['w']:.0f}x{t['h']:.0f}" for t in d["tiles"]
                                if t["w"] < BIG or t["h"] < BIG]))
    none_of("three tiles a row on the narrowest phone",
            each(lambda d: [] if d["asked"] != 320 or sum(
                1 for t in d["tiles"] if abs(t["t"] - d["tiles"][0]["t"]) < 2) >= 3
                else ["fewer than three in the first row"]))

    def overlaps(d):
        out = []
        for i, a in enumerate(d["tiles"]):
            for b in d["tiles"][i + 1:]:
                if (min(a["r"], b["r"]) - max(a["l"], b["l"]) > 0.5
                        and min(a["b"], b["b"]) - max(a["t"], b["t"]) > 0.5):
                    out.append(f"{a['ep']} over {b['ep']}")
        return out
    none_of("no tile sits over another", each(overlaps))
    none_of("every label stays inside its tile",
            each(lambda d: [t["ep"] for t in d["tiles"] if t["label"]["spill"]
                                or t["label"]["l"] < t["l"] - 0.5
                                or t["label"]["r"] > t["r"] + 0.5
                                or t["label"]["b"] > t["b"] + 0.5]))
    none_of("the clock button is a touch target and on the screen",
            each(lambda d: [] if d.get("clock") and d["clock"]["h"] >= 44
                     and d["clock"]["r"] <= d["vw"] + 0.5 else [f"clock {d.get('clock')}"]))
