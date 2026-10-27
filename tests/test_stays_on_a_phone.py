"""A stay's pages on a phone: nothing on them wider than the screen.

The owner, 27 October 2026, on the arrivals sheet, the booking and the atelier
pages: "make sure it's all perfect and easy". Measured, because measuring found
what reading the code had not. The atelier page was 448px wide on a 375px
phone: a label hidden for screen readers, in the last heading of a table that
scrolls sideways, still counted towards the page's width -- the clip hides the
ink, not the box -- and dragged the page, the header and the menu sideways
with it. Nothing errored and every word was there.

Only a browser can tell, so this needs one and fails rather than skips without
it (set GUDANES_CHROME), as test_staff_header_on_a_phone does, whose measuring
this borrows. The words are made long on purpose -- an occasion, a diet and an
extra each a sentence -- because a page that fits with short ones is not the
page a real guest produces.
"""
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness
from test_staff_header_on_a_phone import find_browser, measure, standalone

m = _harness.m
TAG = "ZZSOP"
WIDTHS = (390, 320)


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = 'guest_request' AND title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.commit()
    conn.close()


def run():
    s = Suite("A stay's pages on a phone")
    browser = find_browser()
    if not s.check("a browser to measure with was found", browser,
                   detail="install Chrome or Chromium, or set GUDANES_CHROME to one -- "
                          "this suite measures and cannot without one"):
        return s

    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    day = _harness.free_window(room["id"], 4, after_days=600, clear_of_ateliers=True)
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    bid = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           guest_phone, arrival_date, departure_date, party_size, status, payment_status,
           total_price, amount_paid, created_at, estimated_arrival_time, special_occasion,
           dietary_notes, special_requests)
           VALUES (?, ?, ?, ?, ?, '+33 6 12 34 56 78', ?, ?, 2, 'confirmed', 'unpaid', 900, 0, ?,
                   '17:45', ?, ?, ?)""",
        (room["id"], f"{TAG}-ONE", f"tok{TAG}one".lower(), f"{TAG} Marie-Christine de La Rochefoucauld",
         "zzsop.one@example.invalid", day.isoformat(), (day + timedelta(days=3)).isoformat(), now,
         "Our tenth wedding anniversary, and Jane's fiftieth birthday the same week",
         "no shellfish, no pork, coeliac -- please keep everything apart in the kitchen",
         "A quiet room on the garden side if at all possible, we are very light sleepers")).lastrowid
    conn.execute(
        """INSERT INTO booking_extras (category, booking_id, name, unit_price, quantity, notes,
           status, scheduled_for, created_at) VALUES ('room', ?, ?, 60, 2, ?, 'requested', ?, ?)""",
        (bid, "Champagne and a cheese board from the fromagerie in Foix",
         "in the room before they arrive, with the card", day.isoformat(), now))
    conn.execute(
        """INSERT INTO tasks (title, notes, priority, due_date, status, created_at, origin, booking_id)
           VALUES (?, ?, 'normal', ?, 'open', ?, 'guest_request', ?)""",
        (f"{TAG} a special note for the anniversary of Jane, on arrival, on the pillow",
         f"{TAG} a special note for the anniversary of Jane, on arrival, on the pillow",
         day.isoformat(), now, bid))
    workshop = conn.execute("SELECT id FROM workshops ORDER BY id LIMIT 1").fetchone()
    sid = None
    if workshop:
        sid = conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
               created_at) VALUES (?, ?, ?, 12, ?, ?)""",
            (workshop["id"], day.isoformat(), (day + timedelta(days=3)).isoformat(), TAG, now)).lastrowid
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
               guest_email, party_size, status, created_at, assigned_room_id, estimated_arrival_time,
               occupancy_type, special_occasion, dietary_notes, medical_notes)
               VALUES (?, ?, ?, ?, ?, 1, 'confirmed', ?, ?, '15:00', 'solo', ?, ?, ?)""",
            (sid, f"{TAG}-WA", f"tok{TAG}wa".lower(), f"{TAG} Anne-Sophie Vandenberghe-Lefèvre",
             "zzsop.wa@example.invalid", now, room["id"],
             "Her retirement, after thirty-one years at the hospital in Toulouse",
             "vegetarian, and no mushrooms of any kind", "asthma, carries an inhaler at all times"))
    conn.commit()
    conn.close()
    s.check("an atelier to measure", sid is not None, detail="reported rather than skipped")

    pages = {"the arrivals sheet": f"/admin/arrivals?date={day.isoformat()}",
             "the booking": f"/admin/bookings/{bid}"}
    if sid:
        pages.update({"the atelier's sitting": f"/admin/workshops/sessions/{sid}",
                      "the registrations page": f"/admin/workshops/registrations?session_id={sid}",
                      "the instructor's sheet": f"/workshops/{sid}/sheet"})

    workdir = tempfile.mkdtemp(prefix="gudanes_stay_phone_")
    html, frames = [], []
    try:
        s.section("The pages")
        for name, path in pages.items():
            r = oc.get(path)
            if not s.check(f"{name} renders", r.status_code == 200, r):
                continue
            html.append(standalone(r.get_data(as_text=True)))
            for w in WIDTHS:
                frames.append({"page": len(html) - 1, "width": w, "name": name})
        # The control: the first page with something 640px wide at the top.
        # If that is not reported too wide, no pass below is worth reading.
        control = re.sub(r"<body[^>]*>",
                         lambda tag: tag.group(0) + '<div style="width:640px;height:2px"></div>',
                         html[0], count=1)
        html.append(control)
        frames.append({"page": len(html) - 1, "width": 390, "control": True})
        for i, page in enumerate(html):
            with open(os.path.join(workdir, f"page{i}.html"), "w", encoding="utf-8") as fh:
                fh.write(page)
        seen, proc = measure(browser, frames, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        _cleanup()

    s.section("Nothing wider than the phone")
    missing = [i for i in range(len(frames)) if i not in seen]
    s.check(f"every frame reported back ({len(seen)} of {len(frames)})", not missing,
            detail="the browser said: " + " | ".join(proc.stderr.strip().splitlines()[-3:]))
    ctl = [seen[i] for i, f in enumerate(frames) if f.get("control") and i in seen]
    s.check("a page made too wide on purpose is reported too wide",
            ctl and ctl[0]["docW"] > ctl[0]["vw"],
            detail=f"measured {ctl[0]['docW']}px in {ctl[0]['vw']}px" if ctl else "no report")
    for i, f in enumerate(frames):
        if f.get("control") or i not in seen:
            continue
        d = seen[i]
        far = d.get("widest") or {}
        s.check(f"{f['name']} fits {f['width']}px", d["docW"] <= d["vw"],
                detail=f"{d['docW']}px wide in {d['vw']}px; furthest right: "
                       f"{far.get('what')} at {far.get('right')}px")
    return s


if __name__ == "__main__":
    print(run().report())
