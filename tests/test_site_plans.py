"""Plans of the house, and problems pinned to them.

WHAT THIS IS FOR. Somebody on the far side of the park finds a branch down
across the drive. On their phone they open the plan of the grounds, tap where
it is, add a photograph and a line, and send. It must become a maintenance job
like any other -- a task, on somebody's list and on the calendar -- carrying
the place they tapped, and the map must show it there.

THE APP NEVER DRAWS A PLAN. A floor plan drawn on the design side was rejected
by the owner on 9 October 2026 as not even accurate. Every plan here is a
picture the owner uploaded, and these checks upload one.

FOUR THINGS ARE WORTH TESTING HARDEST, because each would look fine on the page
while being wrong:

  - A pin is stored as FRACTIONS of the picture. A tap is pixels on a picture
    drawn at whatever size the phone drew it, and the arithmetic between the two
    is done once (pin_from_tap). Get it wrong and every report lands somewhere
    plausible and false.
  - The pin must stay on its spot at any width. That is a property of the CSS
    and only a browser can check it, so the last section loads the real map in
    headless Chrome at a phone's width, a desk's, and zoomed, and measures
    where the point of each pin actually is.
  - A report is a JOB, not a second list: origin 'maintenance', due today, on
    the calendar, and a schedule's own job finds its place through its schedule.
  - A job page only ever shows maintenance work. Tasks are also the owner's
    private list, and a page that shows any task by number would hand it out.

NO BROWSER IS A FAILURE, not a skip, as in the phone header suite.
"""
import html as htmllib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZPLAN"


def _png(width, height, fmt="PNG", exif=None):
    """A picture with four coloured quarters, so a pin's place can be seen."""
    from PIL import Image
    im = Image.new("RGB", (width, height), (120, 160, 90))
    for (x0, y0), colour in (((0, 0), (200, 60, 60)), ((width // 2, 0), (60, 60, 200)),
                             ((0, height // 2), (230, 200, 70)),
                             ((width // 2, height // 2), (90, 90, 90))):
        im.paste(colour, (x0, y0, x0 + width // 2, y0 + height // 2))
    buf = io.BytesIO()
    if exif is not None:
        im.save(buf, fmt, exif=exif)
    else:
        im.save(buf, fmt)
    return buf.getvalue()


def _cleanup():
    conn = db()
    plans = [r["id"] for r in conn.execute(
        "SELECT id FROM site_plans WHERE name LIKE ?", (TAG + "%",))]
    files = [r["filename"] for r in conn.execute(
        "SELECT filename FROM site_plans WHERE name LIKE ?", (TAG + "%",))]
    tasks = [r["id"] for r in conn.execute(
        "SELECT id FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))]
    for t in tasks:
        row = conn.execute("SELECT photo_filename FROM plan_pins WHERE task_id = ?",
                           (t,)).fetchone()
        if row and row["photo_filename"]:
            files.append(row["photo_filename"])
    conn.execute("DELETE FROM notifications WHERE related_task_id IN (%s)"
                 % ",".join("?" * len(tasks)), tasks)
    conn.execute("DELETE FROM plan_pins WHERE plan_id IN (%s)"
                 % ",".join("?" * len(plans)), plans)
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM maintenance_visits WHERE schedule_id IN "
                 "(SELECT id FROM maintenance_schedules WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM maintenance_schedules WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM site_plans WHERE name LIKE ?", (TAG + "%",))
    # The gardener took a job, which the audit log remembers; that line goes
    # with them, or the database will not let them go.
    conn.execute("DELETE FROM audit_log WHERE actor_user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG.lower() + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()
    for f in files:
        try:
            os.remove(os.path.join(m.ROOM_PHOTO_DIR, f))
        except OSError:
            pass


def _second_employee():
    conn = db()
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status, created_at)
           VALUES (?, 'x', 'employee', ?, 'Gardener', 'active', ?)""",
        (TAG.lower() + "-gardener@example.invalid", TAG + " Gardener",
         _harness.datetime_now()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE email = ?",
                       (TAG.lower() + "-gardener@example.invalid",)).fetchone()
    conn.close()
    c = m.app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = row["id"]
    return c, row


def _upload(client, name, data, filename="grounds.png", **extra):
    form = {"name": name, "kind": "grounds", "source": "Test aerial", "sort_order": "-900",
            "image": (io.BytesIO(data), filename)}
    form.update(extra)
    return client.post("/management/plans/new", data=form,
                       content_type="multipart/form-data")


def _report(client, plan_id, line, tap=None, photo=None, dangerous=False):
    form = {"plan_id": str(plan_id) if plan_id else "", "line": line}
    if tap:
        form.update(tap_x=str(tap[0]), tap_y=str(tap[1]),
                    shown_w=str(tap[2]), shown_h=str(tap[3]))
    if photo is not None:
        form["photo"] = (io.BytesIO(photo), "branch.jpg")
    if dangerous:
        form["dangerous"] = "1"
    return client.post("/report-a-problem", data=form, content_type="multipart/form-data")


def run():
    s = Suite("Plans of the house, and problems pinned to them")
    _cleanup()
    oc, ec, owner, emp = clients()
    gc, gardener = _second_employee()
    conn = db()

    # -----------------------------------------------------------------------
    s.section("A tap becomes fractions of the picture, whatever size it was drawn")
    s.check("the middle of a phone-sized drawing is the middle",
            m.pin_from_tap(195, 130, 390, 260) == (0.5, 0.5),
            detail=str(m.pin_from_tap(195, 130, 390, 260)))
    s.check("a quarter across and three quarters down",
            m.pin_from_tap(97.5, 195, 390, 260) == (0.25, 0.75),
            detail=str(m.pin_from_tap(97.5, 195, 390, 260)))
    s.check("the same spot tapped on a desk-sized drawing is the same pin",
            m.pin_from_tap(320, 600, 1280, 800) == m.pin_from_tap(97.5, 195, 390, 260))
    s.check("x is across and y is down, not the other way round",
            m.pin_from_tap(10, 200, 400, 400) == (0.025, 0.5),
            detail=str(m.pin_from_tap(10, 200, 400, 400)))
    for args, why in (((400, 10, 390, 260), "a tap past the right edge"),
                      ((-1, 10, 390, 260), "a tap before the left edge"),
                      ((10, 10, 0, 260), "a picture drawn no wide"),
                      (("nan", 10, 390, 260), "NaN, which float() accepts"),
                      (("inf", 10, 390, 260), "infinity"),
                      (("", "", "", ""), "nothing at all")):
        s.check(f"refused: {why}", m.pin_from_tap(*args) is None,
                detail=str(m.pin_from_tap(*args)))
    # The other way round -- a stored pin drawn at another size -- is the
    # page's own percentages, and only a browser can check those: see the
    # last section.

    # -----------------------------------------------------------------------
    s.section("The owner uploads a plan; the app draws none of its own")
    r = oc.get("/management/plans")
    s.check("the Plans page opens", r.status_code == 200, r)
    body = r.get_data(as_text=True)
    s.check("it says what to upload", "aerial photograph" in body and "survey plan" in body
            and "cadastre" in body)
    s.check("and that the app draws no plan of its own", "never draws a plan" in body)
    s.check("an employee is turned away from it", ec.get("/management/plans").status_code
            in (302, 403))
    s.check("and from adding one",
            _upload(ec, TAG + " Sneaky", _png(1200, 800)).status_code in (302, 403))

    before = conn.execute("SELECT COUNT(*) FROM site_plans").fetchone()[0]
    r = _upload(oc, TAG + " PDF", b"%PDF-1.4 a cadastre extract", filename="cadastre.pdf")
    said = flashes(oc.get("/management/plans"))
    s.check("a PDF is refused with how to fix it",
            any("screenshot" in f for f in said), detail=str(said))
    r = _upload(oc, TAG + " Tiny", _png(300, 200))
    said = flashes(oc.get("/management/plans"))
    s.check("so is a picture too small to pin anything on precisely",
            any("too small" in f for f in said), detail=str(said))
    r = _upload(oc, "", _png(1200, 800))
    s.check("and one with no name",
            any("name" in f for f in flashes(oc.get("/management/plans"))))
    s.check("none of the three was stored",
            conn.execute("SELECT COUNT(*) FROM site_plans").fetchone()[0] == before)

    r = _upload(oc, TAG + " Grounds", _png(1200, 800))
    plan = conn.execute("SELECT * FROM site_plans WHERE name = ?",
                        (TAG + " Grounds",)).fetchone()
    s.check("a real picture is taken", plan is not None and r.status_code == 302, r)
    if not plan:
        conn.close()
        return s
    s.check("and the owner lands on it as the map",
            r.headers.get("Location", "").endswith(f"/plans/{plan['id']}"),
            detail=r.headers.get("Location"))
    s.check("its size is the picture's", (plan["width"], plan["height"]) == (1200, 800),
            detail=f"{plan['width']} x {plan['height']}")
    path = os.path.join(m.ROOM_PHOTO_DIR, plan["filename"])
    s.check("it is on the volume beside the room photographs, not in the repository",
            os.path.exists(path) and os.path.dirname(os.path.abspath(path))
            == os.path.abspath(m.ROOM_PHOTO_DIR)
            and not os.path.abspath(path).startswith(os.path.abspath(_harness.ROOT)),
            detail=path)
    img = ec.get(f"/plans/{plan['id']}/image")
    s.check("staff are served the picture", img.status_code == 200
            and img.headers.get("Content-Type", "").startswith("image/jpeg"), img)
    img.close()       # a served file stays open until then, and Windows will not delete it
    s.check("nobody signed out is",
            m.app.test_client().get(f"/plans/{plan['id']}/image").status_code == 302)
    s.check("and a plan that does not exist has no picture",
            ec.get("/plans/999999/image").status_code == 404)

    # -----------------------------------------------------------------------
    s.section("Somebody reports a problem from the plan, and it becomes a job")
    r = ec.get("/report-a-problem")
    page = r.get_data(as_text=True)
    s.check("the report page opens for staff", r.status_code == 200, r)
    s.check("on the first plan in the owner's order", f'name="plan_id" value="{plan["id"]}"'
            in page, detail="sort_order -900 puts the test plan first")
    s.check("with the picture to tap", f"/plans/{plan['id']}/image" in page)
    s.check("and a photograph that the phone can take", 'name="photo"' in page
            and 'accept="image/*"' in page)

    before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    r = _report(ec, plan["id"], TAG + " branch without a place")
    s.check("a report with no tap is sent back", r.status_code == 200 and any(
        "Tap the plan" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("with what they wrote still in the box",
            TAG + " branch without a place" in r.get_data(as_text=True))
    s.check("and no job made", conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            == before)
    r = _report(ec, plan["id"], "", tap=(10, 10, 390, 260))
    s.check("a report with no words is sent back too", any(
        "few words" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("keeping where they tapped", 'id="plan-marker"' in r.get_data(as_text=True)
            and "left:2.564%" in r.get_data(as_text=True),
            detail="10/390 = 2.564% across")

    # A photograph off a phone carries the phone's own details. None of them
    # should be kept with a picture of a branch.
    from PIL import Image
    exif = Image.Exif()
    exif[0x010F] = TAG + " PHONE MAKER"
    exif[0x0110] = TAG + " MODEL"
    photo = _png(1600, 1200, fmt="JPEG", exif=exif)
    r = _report(ec, plan["id"], TAG + " fallen branch by the gate",
                tap=(97.5, 195, 390, 260), photo=photo)
    job = conn.execute("SELECT * FROM tasks WHERE title LIKE ?",
                       ("%" + TAG + " fallen branch by the gate",)).fetchone()
    s.check("a report with a tap, a photograph and a line is taken",
            job is not None and r.status_code == 302, detail=str(flashes(oc.get("/"))))
    if not job:
        conn.close()
        return s
    s.check("and it goes to its job page", r.headers.get("Location", "").endswith(
        f"/maintenance/jobs/{job['id']}"), detail=r.headers.get("Location"))
    s.check("it is a maintenance job, in the same list the schedules raise into",
            job["origin"] == "maintenance" and job["status"] == "open",
            detail=f"{job['origin']} / {job['status']}")
    s.check("titled with the plan's name, so it can never pass for a schedule's job",
            job["title"] == f"{TAG} Grounds: {TAG} fallen branch by the gate",
            detail=job["title"])
    s.check("due today, at the house", job["due_date"] == house_today().isoformat(),
            detail=job["due_date"])
    s.check("normal priority when nobody said it was dangerous",
            job["priority"] == "normal")
    s.check("and its notes say who sent it",
            emp["name"] in (job["notes"] or ""), detail=job["notes"])
    pin = conn.execute("SELECT * FROM plan_pins WHERE task_id = ?", (job["id"],)).fetchone()
    s.check("it carries its pin", pin is not None and pin["plan_id"] == plan["id"])
    s.check("at the fractions of the picture where they tapped",
            pin and (pin["x"], pin["y"]) == (0.25, 0.75),
            detail=f"{(pin['x'], pin['y']) if pin else None}")
    s.check("placed by the person who reported it",
            pin and pin["placed_by_user_id"] == emp["id"])
    stored = os.path.join(m.ROOM_PHOTO_DIR, pin["photo_filename"]) if pin and \
        pin["photo_filename"] else ""
    s.check("the photograph is kept beside the room photographs",
            bool(stored) and os.path.exists(stored), detail=stored)
    if stored and os.path.exists(stored):
        with Image.open(stored) as im:
            kept = dict(im.getexif())
        s.check("without the phone's details on it", not kept,
                detail=f"{kept} -- photo_master drops the EXIF, GPS included")

    told = conn.execute(
        "SELECT * FROM notifications WHERE related_task_id = ? AND kind = 'maintenance'",
        (job["id"],)).fetchall()
    s.check("the owner is told, with a link to the job",
            any(n["user_id"] == owner["id"]
                and n["link"] == f"/maintenance/jobs/{job['id']}" for n in told),
            detail=str([dict(n) for n in told]))
    s.check("and the person who sent it is not told about their own report",
            not any(n["user_id"] == emp["id"] for n in told))

    with m.app.test_request_context():
        rows = m.build_overview(conn, "day", house_today())["rows"]
    s.check("it is on today's calendar, as every task is",
            any(r["kind"] == "task" and r["id"] == job["id"] for r in rows))

    r = _report(ec, plan["id"], TAG + " tree across the drive", tap=(390, 0, 390, 260),
                dangerous=True)
    danger = conn.execute("SELECT * FROM tasks WHERE title LIKE ?",
                          ("%" + TAG + " tree across the drive",)).fetchone()
    s.check("something dangerous is urgent",
            danger is not None and danger["priority"] == "high",
            detail=danger["priority"] if danger else "not made")
    dpin = conn.execute("SELECT * FROM plan_pins WHERE task_id = ?",
                        (danger["id"],)).fetchone() if danger else None
    s.check("and a tap on the very corner of the picture is still on it",
            dpin is not None and (dpin["x"], dpin["y"]) == (1.0, 0.0),
            detail=str((dpin["x"], dpin["y"]) if dpin else None))

    # -----------------------------------------------------------------------
    s.section("The job page: what, where, the photograph, and who has it")
    r = ec.get(f"/maintenance/jobs/{job['id']}")
    page = r.get_data(as_text=True)
    s.check("the job page opens for staff", r.status_code == 200, r)
    s.check("it shows the plan with the job's pin on it",
            f"/plans/{plan['id']}/image" in page and "left:25.000%; top:75.000%" in page)
    s.check("and the photograph", f"/maintenance/jobs/{job['id']}/photo" in page)
    ph = ec.get(f"/maintenance/jobs/{job['id']}/photo")
    s.check("which is served by its job", ph.status_code == 200
            and ph.headers.get("Content-Type", "").startswith("image/"), ph)
    ph.close()
    s.check("a job with no photograph has none to serve",
            ec.get(f"/maintenance/jobs/{danger['id']}/photo").status_code == 404)

    # The owner's own private task, which is also a row in tasks. The job page
    # must not be a window onto it.
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, priority, status, origin, created_at)
           VALUES (?, ?, 'normal', 'open', 'manual', ?)""",
        (owner["id"], TAG + " the owner's private note", _harness.datetime_now()))
    conn.commit()
    private = conn.execute("SELECT id FROM tasks WHERE title = ?",
                           (TAG + " the owner's private note",)).fetchone()
    s.check("a task that is not maintenance is not a job page",
            ec.get(f"/maintenance/jobs/{private['id']}").status_code == 404)
    s.check("nor can its status be changed from one",
            ec.post(f"/maintenance/jobs/{private['id']}/status",
                    data={"action": "done"}).status_code == 404)

    def status_of(tid):
        return conn.execute("SELECT status, assigned_to_user_id FROM tasks WHERE id = ?",
                            (tid,)).fetchone()

    r = gc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "done"})
    s.check("somebody who does not have it cannot finish it",
            status_of(job["id"])["status"] == "open"
            and any("Only whoever" in f for f in flashes(gc.get(
                f"/maintenance/jobs/{job['id']}"))))
    r = gc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "take"})
    st = status_of(job["id"])
    s.check("anybody can take a job nobody has",
            st["assigned_to_user_id"] == gardener["id"] and st["status"] == "in_progress",
            detail=str(dict(st)))
    r = ec.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "take"})
    s.check("but not one somebody else already has",
            status_of(job["id"])["assigned_to_user_id"] == gardener["id"]
            and any("already has" in f for f in flashes(ec.get(
                f"/maintenance/jobs/{job['id']}"))))
    r = gc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "done"})
    s.check("whoever has it can finish it", status_of(job["id"])["status"] == "done")
    r = gc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "reopen"})
    s.check("and open it again", status_of(job["id"])["status"] == "open")
    r = oc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "start"})
    s.check("the owner can move anybody's on",
            status_of(job["id"])["status"] == "in_progress")

    # -----------------------------------------------------------------------
    s.section("The map shows the open jobs where they are")
    r = ec.get(f"/plans/{plan['id']}")
    page = r.get_data(as_text=True)
    s.check("the map opens for staff", r.status_code == 200, r)
    s.check("it is the uploaded picture", f"/plans/{plan['id']}/image" in page)
    pin_html = re.findall(r'<a class="plan-pin plan-pin--(\w+)" href="([^"]+)"\s+'
                          r'style="left:([\d.]+)%; top:([\d.]+)%"', page)
    s.check("the reported job is a pin at the place it was tapped",
            ("doing", f"/maintenance/jobs/{job['id']}", "25.000", "75.000") in pin_html,
            detail=str(pin_html))
    s.check("every pin opens its job",
            pin_html and all(h.startswith(("/maintenance/jobs/", "/maintenance/checks/"))
                             for _t, h, _x, _y in pin_html), detail=str(pin_html))
    s.check("and the list under it says the same", f"/maintenance/jobs/{job['id']}" in page
            and "Being dealt with" in page)
    s.check("the list goes through list_view, with counted chips",
            "facet-row" in page and "chip-n" in page)
    s.check("and sits in a table-wrap", '<div class="table-wrap">' in page)
    r = ec.get("/plans")
    s.check("/plans opens on the first plan",
            r.status_code == 200 and f"/plans/{plan['id']}/image" in r.get_data(as_text=True))

    oc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "done"})
    page = ec.get(f"/plans/{plan['id']}").get_data(as_text=True)
    s.check("a finished job is off the map by default",
            f'href="/maintenance/jobs/{job["id"]}"' not in page)
    page = ec.get(f"/plans/{plan['id']}?status=done").get_data(as_text=True)
    s.check("and under Done lately, green",
            f'plan-pin--done" href="/maintenance/jobs/{job["id"]}"' in page)
    long_ago = (datetime.now(timezone.utc)
                - timedelta(days=m.PLAN_DONE_SHOWN_DAYS + 2)).isoformat()
    conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (long_ago, job["id"]))
    conn.commit()
    page = ec.get(f"/plans/{plan['id']}?status=all").get_data(as_text=True)
    s.check("until it is history, when it drops off altogether",
            f'href="/maintenance/jobs/{job["id"]}"' not in page)
    oc.post(f"/maintenance/jobs/{job['id']}/status", data={"action": "reopen"})

    # -----------------------------------------------------------------------
    s.section("A check on a schedule can be given a place")
    fields = {"name": TAG + " Trees inspected", "category": "grounds", "location": None,
              "every_months": 12, "last_done_on": None,
              "next_due_on": (house_today() + timedelta(days=200)).isoformat(),
              "lead_days": 21, "required_by": "none", "active": 1,
              "created_at": _harness.datetime_now()}
    conn.execute(f"INSERT INTO maintenance_schedules ({', '.join(fields)}) "
                 f"VALUES ({', '.join('?' * len(fields))})", list(fields.values()))
    conn.commit()
    sched = conn.execute("SELECT * FROM maintenance_schedules WHERE name = ?",
                         (TAG + " Trees inspected",)).fetchone()
    r = oc.get("/management/maintenance")
    page = r.get_data(as_text=True)
    s.check("the maintenance page offers to put it on the plan",
            f"/management/plans/{plan['id']}/place?schedule_id={sched['id']}" in page)
    r = oc.get(f"/management/plans/{plan['id']}/place?schedule_id={sched['id']}")
    s.check("the place page opens", r.status_code == 200
            and "Trees inspected" in r.get_data(as_text=True), r)
    s.check("an employee cannot place things",
            ec.get(f"/management/plans/{plan['id']}/place?schedule_id={sched['id']}")
            .status_code in (302, 403))
    r = oc.post(f"/management/plans/{plan['id']}/place",
                data={"schedule_id": sched["id"], "tap_x": "300", "tap_y": "100",
                      "shown_w": "1200", "shown_h": "800"})
    spin = conn.execute("SELECT * FROM plan_pins WHERE schedule_id = ?",
                        (sched["id"],)).fetchone()
    s.check("a tap puts it there", spin is not None
            and (spin["x"], spin["y"]) == (0.25, 0.125),
            detail=str((spin["x"], spin["y"]) if spin else None))
    s.check("and the owner goes back to it on the maintenance page",
            r.headers.get("Location", "").endswith(f"/management/maintenance#sched-{sched['id']}"),
            detail=r.headers.get("Location"))
    oc.post(f"/management/plans/{plan['id']}/place",
            data={"schedule_id": sched["id"], "tap_x": "600", "tap_y": "400",
                  "shown_w": "1200", "shown_h": "800"})
    moved = conn.execute("SELECT * FROM plan_pins WHERE schedule_id = ?",
                         (sched["id"],)).fetchall()
    s.check("placing it again moves it rather than adding a second",
            len(moved) == 1 and (moved[0]["x"], moved[0]["y"]) == (0.5, 0.5),
            detail=str([(p["x"], p["y"]) for p in moved]))
    page = oc.get("/management/maintenance").get_data(as_text=True)
    s.check("the maintenance page says which plan it is on",
            f"/maintenance/checks/{sched['id']}" in page and TAG + " Grounds" in page)
    page = ec.get(f"/plans/{plan['id']}").get_data(as_text=True)
    s.check("a check not yet due is not an open job",
            f"/maintenance/checks/{sched['id']}" not in page)
    page = ec.get(f"/plans/{plan['id']}?status=not_due").get_data(as_text=True)
    s.check("it is under Checks not due yet", f'plan-pin--check" href="/maintenance/checks/'
            f'{sched["id"]}"' in page, detail="a steel pin opening the check")
    r = ec.get(f"/maintenance/checks/{sched['id']}")
    s.check("staff can open a check to see where it is", r.status_code == 200
            and "left:50.000%; top:50.000%" in r.get_data(as_text=True), r)

    conn.execute("UPDATE maintenance_schedules SET next_due_on = ? WHERE id = ?",
                 ((house_today() - timedelta(days=3)).isoformat(), sched["id"]))
    conn.commit()
    m.generate_maintenance_tasks(conn)
    sjob = conn.execute(
        "SELECT * FROM tasks WHERE origin = 'maintenance' AND title = ? AND status != 'done'",
        (TAG + " Trees inspected",)).fetchone()
    s.check("once it falls due the schedule raises its job as always", sjob is not None)
    page = ec.get(f"/plans/{plan['id']}").get_data(as_text=True)
    drawn = re.findall(r'<a class="plan-pin plan-pin--(\w+)" href="([^"]+)"\s+'
                       r'style="left:([\d.]+)%; top:([\d.]+)%"', page)
    s.check("and that job is an open pin at the schedule's place",
            sjob and ("open", f"/maintenance/jobs/{sjob['id']}", "50.000", "50.000") in drawn,
            detail=f"{drawn} -- the job finds its place through its schedule")
    r = ec.get(f"/maintenance/jobs/{sjob['id']}") if sjob else None
    s.check("its job page says where the schedule is kept",
            r is not None and r.status_code == 200 and "is kept" in r.get_data(as_text=True))
    oc.post(f"/maintenance/jobs/{sjob['id']}/status", data={"action": "done"})
    s.check("and it cannot be ticked off without recording the visit",
            status_of(sjob["id"])["status"] != "done",
            detail="ticking it alone leaves the schedule owed, and it comes back tomorrow")

    r = oc.post(f"/management/plans/{plan['id']}/place",
                data={"schedule_id": sched["id"], "remove": "1"})
    s.check("it can be taken off the plan",
            conn.execute("SELECT COUNT(*) FROM plan_pins WHERE schedule_id = ?",
                         (sched["id"],)).fetchone()[0] == 0)
    s.check("and the schedule itself stays",
            conn.execute("SELECT active FROM maintenance_schedules WHERE id = ?",
                         (sched["id"],)).fetchone()["active"] == 1)

    # -----------------------------------------------------------------------
    s.section("A job with no pin still works, and can be given one")
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, priority, status, due_date,
                              origin, created_at)
           VALUES (NULL, ?, 'normal', 'open', ?, 'maintenance', ?)""",
        (TAG + " gutter on the barn", house_today().isoformat(), _harness.datetime_now()))
    conn.commit()
    loose = conn.execute("SELECT * FROM tasks WHERE title = ?",
                         (TAG + " gutter on the barn",)).fetchone()
    r = ec.get(f"/maintenance/jobs/{loose['id']}")
    s.check("its job page opens and says it is on no plan", r.status_code == 200
            and "Not on a plan" in r.get_data(as_text=True), r)
    page = oc.get(f"/plans/{plan['id']}?where=unplaced").get_data(as_text=True)
    s.check("the map lists it as not placed yet, with a way to place it",
            f"/management/plans/{plan['id']}/place?task_id={loose['id']}" in page)
    oc.post(f"/management/plans/{plan['id']}/place",
            data={"task_id": loose["id"], "tap_x": "900", "tap_y": "200",
                  "shown_w": "1200", "shown_h": "800"})
    lp = conn.execute("SELECT * FROM plan_pins WHERE task_id = ?", (loose["id"],)).fetchone()
    s.check("the owner can put it on the plan", lp is not None
            and (lp["x"], lp["y"]) == (0.75, 0.25))
    r = oc.post(f"/management/plans/{plan['id']}/place",
                data={"task_id": loose["id"], "tap_x": "", "tap_y": "",
                      "shown_w": "", "shown_h": ""})
    s.check("a place with no tap is refused, and the pin stays",
            conn.execute("SELECT x FROM plan_pins WHERE task_id = ?",
                         (loose["id"],)).fetchone()["x"] == 0.75)
    s.check("a private task cannot be placed",
            oc.get(f"/management/plans/{plan['id']}/place?task_id={private['id']}")
            .status_code == 404)

    # -----------------------------------------------------------------------
    s.section("On the phone's home screen, for staff and the owner")
    s.check("staff have a Report a Problem tile",
            'href="/report-a-problem"' in ec.get("/app").get_data(as_text=True))
    s.check("so does the owner",
            'href="/report-a-problem"' in oc.get("/app").get_data(as_text=True))

    # -----------------------------------------------------------------------
    s.section("Changing a plan keeps its pins where they were")
    before = conn.execute("SELECT x, y FROM plan_pins WHERE task_id = ?",
                          (danger["id"],)).fetchone()
    old_file = plan["filename"]
    r = oc.post(f"/management/plans/{plan['id']}/edit",
                data={"name": TAG + " Grounds", "kind": "grounds", "sort_order": "-900",
                      "reports_to_user_id": str(gardener["id"]), "active": "1",
                      "image": (io.BytesIO(_png(2400, 1600)), "sharper.png")},
                content_type="multipart/form-data")
    after = conn.execute("SELECT * FROM site_plans WHERE id = ?", (plan["id"],)).fetchone()
    s.check("a sharper picture goes in", after["filename"] != old_file
            and (after["width"], after["height"]) == (2400, 1600)
            and after["image_replaced_at"], detail=str(dict(after)))
    s.check("the old picture is gone from the volume",
            not os.path.exists(os.path.join(m.ROOM_PHOTO_DIR, old_file)))
    pin_now = conn.execute("SELECT x, y FROM plan_pins WHERE task_id = ?",
                           (danger["id"],)).fetchone()
    s.check("and every pin kept its share of the picture", tuple(pin_now) == tuple(before),
            detail=f"{tuple(before)} -> {tuple(pin_now)}")
    r = _report(ec, plan["id"], TAG + " wasps in the boathouse", tap=(5, 5, 100, 100))
    wasp = conn.execute("SELECT * FROM tasks WHERE title LIKE ?",
                        ("%" + TAG + " wasps in the boathouse",)).fetchone()
    s.check("a plan's reports go to whoever the owner chose",
            wasp is not None and wasp["assigned_to_user_id"] == gardener["id"])
    s.check("and they are the one told",
            wasp and conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE related_task_id = ? AND user_id = ?",
                (wasp["id"], gardener["id"])).fetchone()[0] == 1)

    r = oc.post(f"/management/plans/{plan['id']}/edit",
                data={"name": TAG + " Grounds", "kind": "grounds", "sort_order": "-900"},
                content_type="multipart/form-data")
    s.check("a plan can be retired",
            conn.execute("SELECT active FROM site_plans WHERE id = ?",
                         (plan["id"],)).fetchone()["active"] == 0)
    page = ec.get("/report-a-problem").get_data(as_text=True)
    s.check("and a report no longer opens on it",
            f'name="plan_id" value="{plan["id"]}"' not in page)
    r = _report(ec, plan["id"], TAG + " onto a retired plan", tap=(5, 5, 100, 100))
    s.check("nor can one be sent to it from a page left open",
            conn.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE ?",
                         ("%" + TAG + " onto a retired plan",)).fetchone()[0] == 0
            and any("no longer in use" in f for f in flashes(r)), detail=str(flashes(r)))
    r = ec.get(f"/maintenance/jobs/{danger['id']}")
    s.check("its jobs still show where they are",
            r.status_code == 200 and f"/plans/{plan['id']}/image" in r.get_data(as_text=True))
    oc.post(f"/management/plans/{plan['id']}/edit",
            data={"name": TAG + " Grounds", "kind": "grounds", "sort_order": "-900",
                  "active": "1"}, content_type="multipart/form-data")

    conn.close()
    _browser_section(s, oc, ec, plan["id"])
    _cleanup()
    return s


# ---------------------------------------------------------------------------
# In a browser. The page is rendered by the app, its stylesheet put inline the
# way the phone header suite does it, the plan's own picture put in as a data
# URL, and loaded into headless Chrome in frames of a phone's width and a
# desk's. A probe on each frame either taps the picture or measures where the
# point of every pin really is.
# ---------------------------------------------------------------------------
PROBE = r"""
<script>
(function () {
  var q = {};
  location.hash.slice(1).split('&').forEach(function (kv) {
    var p = kv.split('='); q[p[0]] = decodeURIComponent(p[1] || '');
  });
  function go() {
    var de = document.documentElement;
    var out = {id: q.id, asked: +q.w, vw: de.clientWidth, docW: de.scrollWidth,
               coarse: matchMedia('(pointer: coarse)').matches};
    var img = document.querySelector('#plan-frame > img');
    if (!img) { out.error = 'no plan on the page'; parent.postMessage(JSON.stringify(out), '*'); return; }
    if (q.zoom) {
      var b = document.querySelector('.plan-zoom [data-zoom="' + q.zoom + '"]');
      if (b) b.click();
    }
    var r = img.getBoundingClientRect();
    out.img = {w: r.width, h: r.height, loaded: img.complete && img.naturalWidth > 0};
    if (q.mode === 'tap') {
      img.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window,
        clientX: r.left + (+q.fx) * r.width, clientY: r.top + (+q.fy) * r.height}));
      var form = img.closest('form');
      ['tap_x', 'tap_y', 'shown_w', 'shown_h'].forEach(function (n) {
        out[n] = form.querySelector('[name="' + n + '"]').value;
      });
      // Measured again AFTER the tap: anything the tap changes above the
      // picture moves it, and the next tap then lands somewhere else.
      var r2 = img.getBoundingClientRect();
      out.moved = Math.max(Math.abs(r2.top - r.top), Math.abs(r2.left - r.left));
      var mk = document.getElementById('plan-marker'), mb = mk.getBoundingClientRect();
      out.marker = {hidden: mk.hidden, fx: (mb.left + mb.width / 2 - r2.left) / r2.width,
                    fy: (mb.bottom - r2.top) / r2.height};
    } else {
      out.pins = [].map.call(document.querySelectorAll('#plan-frame .plan-pin'), function (p) {
        var b = p.getBoundingClientRect();
        return {href: p.getAttribute('href'), w: b.width, h: b.height,
                fx: (b.left + b.width / 2 - r.left) / r.width,
                fy: (b.bottom - r.top) / r.height};
      });
    }
    parent.postMessage(JSON.stringify(out), '*');
  }
  window.addEventListener('load', function () { setTimeout(go, 0); });
})();
</script>
"""


def _in_chrome(browser, frames, workdir):
    """Each frame is (html, width, hash). Returns {index: what its probe saw}."""
    from test_staff_header_on_a_phone import WRAPPER_HEAD
    cells = []
    for i, (html, width, extra) in enumerate(frames):
        name = f"plan{i}.html"
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as fh:
            fh.write(html)
        cells.append(f'<iframe src="{name}#id={i}&w={width}&{extra}" '
                     f'style="display:block;border:0;width:{width}px;height:900px"></iframe>')
    wrapper = os.path.join(workdir, "plans.html")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(WRAPPER_HEAD + "\n".join(cells) + "\n</body></html>")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--disable-extensions",
           "--user-data-dir=" + os.path.join(workdir, "profile"), "--hide-scrollbars",
           "--blink-settings=primaryPointerType=2,availablePointerTypes=2,"
           "primaryHoverType=1,availableHoverTypes=1",
           "--host-resolver-rules=MAP * ~NOTFOUND",
           "--virtual-time-budget=30000", "--dump-dom",
           "file:///" + wrapper.replace("\\", "/").lstrip("/")]
    if sys.platform.startswith("linux"):
        cmd.insert(1, "--no-sandbox")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    seen = {}
    for blob in re.findall(r'<pre class="r">(.*?)</pre>', proc.stdout, re.S):
        d = json.loads(htmllib.unescape(blob))
        seen[int(d["id"])] = d
    return seen


def _as_phone(page, picture_url, data_url):
    from test_staff_header_on_a_phone import standalone, PROBE as HEADER_PROBE
    out = standalone(page)
    return out.replace(HEADER_PROBE, PROBE).replace(picture_url, data_url)


def _browser_section(s, oc, ec, plan_id):
    import base64
    s.section("In a browser: a real tap fills the form, and pins stay on their spot")
    from test_staff_header_on_a_phone import find_browser
    browser = find_browser()
    if not s.check("a browser to measure with was found", browser,
                   detail="install Chrome or Chromium, or set GUDANES_CHROME -- this "
                          "section measures and cannot without one"):
        return
    conn = db()
    plan = conn.execute("SELECT * FROM site_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    picture_url = f"/plans/{plan_id}/image?v={plan['filename']}"
    served = ec.get(picture_url)
    raw = served.get_data()
    served.close()
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    report = ec.get(f"/report-a-problem?plan={plan_id}").get_data(as_text=True)
    the_map = ec.get(f"/plans/{plan_id}?status=all").get_data(as_text=True)
    s.check("the pages name the picture the way the probe expects",
            picture_url.replace("&", "&amp;") in report or picture_url in report,
            detail=picture_url)
    widths = (390, 1280)
    frames = [(_as_phone(report, picture_url, data_url), w, "mode=tap&fx=0.3&fy=0.6")
              for w in widths]
    frames.append((_as_phone(report, picture_url, data_url), 390,
                   "mode=tap&fx=0.3&fy=0.6&zoom=3"))
    frames += [(_as_phone(the_map, picture_url, data_url), w, "mode=pins") for w in widths]
    frames.append((_as_phone(the_map, picture_url, data_url), 390, "mode=pins&zoom=3"))
    workdir = tempfile.mkdtemp(prefix="gudanes-plans-")
    try:
        seen = _in_chrome(browser, frames, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    s.check("every frame reported back", len(seen) == len(frames),
            detail=f"{len(seen)} of {len(frames)}")
    s.check("as a touch screen, at the width asked for",
            all(d.get("coarse") and d.get("vw") == d.get("asked") for d in seen.values()),
            detail=str([(d.get("asked"), d.get("vw"), d.get("coarse")) for d in seen.values()]))
    s.check("with the plan's picture loaded",
            all((d.get("img") or {}).get("loaded") for d in seen.values()),
            detail=str([d.get("img") or d.get("error") for d in seen.values()]))

    conn = db()
    for i in range(3):
        d = seen.get(i) or {}
        where = f"{d.get('asked')}px" + (", zoomed" if i == 2 else "")
        point = m.pin_from_tap(d.get("tap_x"), d.get("tap_y"), d.get("shown_w"),
                               d.get("shown_h"))
        iw, ih = (d.get("img") or {}).get("w") or 1, (d.get("img") or {}).get("h") or 1
        # In pixels on the picture as drawn, not as a share: Chrome hands a
        # synthetic click whole-pixel coordinates, so a phone-sized drawing is
        # out by up to a pixel, which is a bigger share of a smaller picture.
        s.check(f"a tap at {where} fills the form with where it landed",
                point is not None and abs(point[0] - 0.3) * iw <= 1.5
                and abs(point[1] - 0.6) * ih <= 1.5,
                detail=f"{point} from {d.get('tap_x')},{d.get('tap_y')} on "
                       f"{d.get('shown_w')}x{d.get('shown_h')}")
        s.check(f"and the plan does not move under the finger ({where})",
                d.get("moved", 99) < 0.5,
                detail=f"moved {d.get('moved')}px -- the second tap would land "
                       "that far from where the person aimed")
        mk = d.get("marker") or {}
        s.check(f"and the marker's point is on the spot tapped ({where})",
                not mk.get("hidden") and abs(mk.get("fx", 9) - 0.3) * d["img"]["w"] < 1.5
                and abs(mk.get("fy", 9) - 0.6) * d["img"]["h"] < 1.5, detail=str(mk))
        s.check(f"and the page is not dragged sideways ({where})",
                d.get("docW", 9999) <= d.get("vw", 0),
                detail=f"{d.get('docW')}px wide on a {d.get('vw')}px screen")
        if i == 0 and point:
            # What the phone would actually send, sent: the pin the server
            # stores from the browser's own numbers.
            r = _report(ec, plan_id, TAG + " from a real tap",
                        tap=(d["tap_x"], d["tap_y"], d["shown_w"], d["shown_h"]))
            p = conn.execute(
                """SELECT plan_pins.* FROM plan_pins JOIN tasks ON tasks.id = plan_pins.task_id
                    WHERE tasks.title LIKE ?""", ("%" + TAG + " from a real tap",)).fetchone()
            s.check("the server stores the browser's tap as the same place",
                    p is not None and (p["x"], p["y"]) == point,
                    detail=f"{(p['x'], p['y']) if p else None} vs {point}")
    expected = {f"/maintenance/jobs/{r['task_id']}": (r["x"], r["y"]) for r in conn.execute(
        "SELECT task_id, x, y FROM plan_pins WHERE plan_id = ? AND task_id IS NOT NULL",
        (plan_id,))}
    conn.close()
    for i in (3, 4, 5):
        d = seen.get(i) or {}
        where = f"{d.get('asked')}px" + (", zoomed" if i == 5 else "")
        pins = d.get("pins") or []
        s.check(f"the map at {where} draws the pins", len(pins) >= 2,
                detail=f"{len(pins)} drawn")
        off = []
        for p in pins:
            want = expected.get(p["href"])
            if not want:
                continue
            dx = abs(p["fx"] - want[0]) * d["img"]["w"]
            dy = abs(p["fy"] - want[1]) * d["img"]["h"]
            if dx > 1.5 or dy > 1.5:
                off.append((p["href"], round(dx, 1), round(dy, 1)))
        s.check(f"every pin's point is on its spot at {where}",
                pins and not off, detail=f"{off} px out -- a pin is placed by share of the "
                                         "picture, so it must not drift with the width")
        small = [(p["href"], p["w"], p["h"]) for p in pins if p["w"] < 44 or p["h"] < 44]
        s.check(f"and every pin is a 44px target at {where}", pins and not small,
                detail=str(small))
        s.check(f"and the page is not dragged sideways ({where})",
                d.get("docW", 9999) <= d.get("vw", 0),
                detail=f"{d.get('docW')}px wide on a {d.get('vw')}px screen")


if __name__ == "__main__":
    print(run().report())
