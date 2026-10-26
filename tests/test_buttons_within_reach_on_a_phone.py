"""On a phone, the button somebody came for is on the screen.

WHAT WENT WRONG. Measured on a seeded Thursday, 22 October 2026, every staff
page at 390px with a touch screen: on these the action a row is for sat past
the right edge, inside a table that scrolled sideways --

    Front Desk      Check in, Take payment, Arrival card, Check out
    Reservations    Mark no-show, Cancel
    Room Checks     Walk it
    Extras Due      Handed over
    Guests          Stays, Edit
    Tasks           the week was seven columns of 120px: Monday to Wednesday
                    on the screen and today off it, every tick with it

Four of those are tiles on the home screen, which is a phone's front door.
Nothing looked broken: a table-wrap scrolls, so the page did not overflow
and test_text_stands_clear had nothing to say. The button was simply not
there until somebody thought to swipe a table sideways.

WHAT IS CHECKED. Every page behind a home-screen tile, for the owner and for
an employee, and the staff lists above, on a day with people in it: loaded in
headless Chrome as a 390px touch screen, and every visible button, link-button
and select must end inside the screen. THE MEASURING IS CHECKED TOO: the same
pages go round again with the old layout put back, and must then be caught.
No browser is a failure, not a skip; set GUDANES_CHROME.
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

from _harness import Suite, clients, db, ensure_room, house_today
import _harness
import test_staff_header_on_a_phone as phone

m = _harness.m
TAG = "ZZREACH"
WIDTH = 390
LISTS = ("/extras/due", "/guests", "/admin/room-checks", "/admin/desk",
         "/admin/restaurant", "/admin/tasks")

# The layout as it was, for the control: tables as tables, the week as seven
# columns. !important, because the fixes are rules in the same sheet.
OLD_LAYOUT = """<style>
@media (max-width: 760px){
  .phone-cards, .phone-cards tbody, .phone-cards tr, .phone-cards td,
  .phone-cards tbody th{ display:revert !important; width:auto !important; }
  .phone-cards thead{ position:static !important; width:auto !important;
    height:auto !important; clip:auto !important; }
}
@media (max-width: 600px){
  .week-grid{ grid-template-columns:repeat(7, minmax(120px,1fr)) !important;
    overflow-x:auto !important; }
}
</style>"""

PROBE = r"""<script>
(function(){
 function seen(el){
  if (el.checkVisibility && !el.checkVisibility({opacityProperty:true, visibilityProperty:true})) return false;
  if (el.closest('details:not([open])') && !el.closest('summary')) return false;
  for (var e = el; e && e !== document.body; e = e.parentElement)
   if (getComputedStyle(e).position === 'fixed') return false;
  return true;
 }
 function go(){
  var vw = document.documentElement.clientWidth, out = [];
  var main = document.querySelector('main') || document.body;
  main.querySelectorAll('button, input[type=submit], a.btn-mini, a.btn-primary, a.btn-ghost, select').forEach(function(c){
   if (!seen(c)) return;
   var r = c.getBoundingClientRect();
   if (r.width < 2 || r.height < 2) return;
   if (r.right > vw + 1) out.push((c.textContent || c.value || c.name || c.tagName).replace(/\s+/g,' ').trim().slice(0,40));
  });
  parent.postMessage(JSON.stringify({id: location.hash.slice(1), vw: vw, off: out}), '*');
 }
 if (document.readyState === 'complete') setTimeout(go, 50); else addEventListener('load', function(){ setTimeout(go, 50); });
})();
</script>"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ? OR booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (f"%{TAG}%", f"{TAG}%"))
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (f"{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (f"{TAG}%",))
    conn.commit()
    conn.close()


def _day(emp_id):
    """A day with a row on every list: one arriving owing money, one leaving,
    one staying on, a dinner tonight, an extra to hand over, a checklist."""
    today = house_today()
    room = ensure_room()
    conn = db()
    stays = {}
    for key, arrive, nights in (("in", 0, 2), ("out", -2, 2), ("on", -1, 3)):
        a = today + timedelta(days=arrive)
        cur = conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, guest_phone, arrival_date, departure_date, party_size, status,
               total_price, created_at, source, arrival_prepped_at, amount_paid)
               VALUES (?, ?, ?, ?, ?, '+33 6 12 34 56 78', ?, ?, 2, 'confirmed', 900, ?, 'direct',
                       ?, 0)""",
            (room["id"], f"{TAG}{key.upper()}", _harness.secrets_token(),
             f"{TAG} Bartholomew-Okonkwo {key}", f"zzreach.{key}@example.invalid",
             a.isoformat(), (a + timedelta(days=nights)).isoformat(), _now(), _now()))
        stays[key] = cur.lastrowid
    conn.execute(
        """INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity, status,
           scheduled_for, created_at) VALUES ('room', ?, ?, 80, 1, 'confirmed', ?, ?)""",
        (stays["on"], f"{TAG} Crémant & charcuterie board", today.isoformat(), _now()))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
           party_size, dinner_date, status, created_at, total_price, payment_status)
           VALUES (?, ?, ?, 'zzreach.dinner@example.invalid', 4, ?, 'confirmed', ?, 180, 'unpaid')""",
        (f"{TAG}DIN", _harness.secrets_token(), f"{TAG} Laurent-Marchand", today.isoformat(),
         _now()))
    conn.execute(
        """INSERT INTO guests (name, email, phone, dietary_notes, created_at)
           VALUES (?, 'zzreach.in@example.invalid', '+33 6 12 34 56 78', 'no shellfish', ?)""",
        (f"{TAG} Bartholomew-Okonkwo in", _now()))
    for item in ("Wine bottle", "Fresh towels", "Confirm arrival time with guest"):
        conn.execute(
            """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date,
               status, created_at, origin, booking_id)
               VALUES (?, ?, ?, 'high', ?, 'open', ?, 'checklist', ?)""",
            (emp_id, f"{room['name']}: {item}", f"{TAG} arriving today, party of 2.",
             today.isoformat(), _now(), stays["in"]))
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, priority, due_date, status, created_at,
           origin) VALUES (?, ?, 'normal', ?, 'open', ?, 'manual')""",
        (emp_id, f"{TAG} restock the honesty bar", today.isoformat(), _now()))
    conn.commit()
    conn.close()


def _tiles(client):
    page = client.get("/app").get_data(as_text=True)
    return re.findall(r'<a href="([^"]+)" class="ph-tile"', page)


def _measure(browser, pages, workdir, extra=""):
    """{label: [controls past the right edge]} for every page, at WIDTH."""
    cells, labels = [], []
    for i, (label, html) in enumerate(pages):
        name = f"p{i}.html"
        body = phone.standalone(html).replace(phone.PROBE, "")
        body = body.replace("</head>", extra + "</head>", 1).replace("</body>", PROBE + "</body>")
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
            fh.write(body)
        cells.append(f'<iframe src="{name}#{i}" style="display:block;border:0;'
                     f'width:{WIDTH}px;height:900px"></iframe>')
        labels.append(label)
    seen = {}
    for start in range(0, len(cells), 20):
        wrapper = os.path.join(workdir, f"w{start}{'x' if extra else ''}.html")
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(phone.WRAPPER_HEAD + "\n".join(cells[start:start + 20]) + "</body></html>")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
               "--user-data-dir=" + os.path.join(workdir, "profile"),
               "--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
               "primaryHoverType=1,availableHoverTypes=1",
               "--host-resolver-rules=MAP * ~NOTFOUND", "--virtual-time-budget=60000",
               "--dump-dom", "file:///" + wrapper.replace("\\", "/").lstrip("/")]
        if sys.platform.startswith("linux"):
            cmd.insert(1, "--no-sandbox")
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=300).stdout
        for blob in re.findall(r'<pre class="r">(.*?)</pre>', out, re.S):
            d = json.loads(htmllib.unescape(blob))
            seen[int(d["id"])] = d
    return {labels[i]: (seen[i]["off"] if i in seen else None) for i in range(len(labels))}


def run():
    s = Suite("On a phone, the button somebody came for is on the screen")
    browser = phone.find_browser()
    if not s.check("a browser to measure with was found", browser,
                   detail="install Chrome or Chromium, or set GUDANES_CHROME -- this "
                          "suite measures and cannot without one"):
        return s
    _cleanup()
    oc, ec, owner, emp = clients()
    _day(emp["id"])
    workdir = tempfile.mkdtemp(prefix="gudanes_reach_")
    try:
        pages = []
        for who, client in (("owner", oc), ("employee", ec)):
            paths = list(dict.fromkeys(_tiles(client) + list(LISTS)))
            for path in paths:
                r = client.get(path)
                if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
                    continue
                pages.append((f"{who} {path}", r.get_data(as_text=True)))
        s.check("the home screen's pages and the lists were all drawn",
                len(pages) >= 12, detail=str([p for p, _h in pages]))

        s.section(f"Every control within a {WIDTH}px phone's screen")
        got = _measure(browser, pages, workdir)
        unmeasured = [k for k, v in got.items() if v is None]
        s.check("every page was measured", not unmeasured, detail=str(unmeasured))
        for label, off in got.items():
            if off is None:
                continue
            s.check(f"{label}: nothing to swipe sideways for", not off,
                    detail=f"{len(off)} past the edge: {off[:4]}")

        s.section("The measuring is checked: the old layout put back must be caught")
        control = [p for p in pages if p[0] in ("owner /admin/desk", "owner /admin/tasks",
                                                  "owner /admin/room-checks")]
        old = _measure(browser, control, workdir, extra=OLD_LAYOUT)
        for label in ("owner /admin/desk", "owner /admin/tasks", "owner /admin/room-checks"):
            s.check(f"{label}, laid out as it was, is caught",
                    bool(old.get(label)), detail=str(old.get(label))[:120])
    finally:
        _cleanup()
        shutil.rmtree(workdir, ignore_errors=True)
    return s


if __name__ == "__main__":
    print(run().report())
