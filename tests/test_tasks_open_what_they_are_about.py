"""A task opens what it is about, and a room's checklist reads as one thing.

WHAT WENT WRONG. Read on a seeded Thursday, 22 October 2026:

    A TASK THAT COULD NOT BE OPENED. "Cot in the Family Suite for the Okafors"
    carries the stay it came from, and so does a room's checklist and an
    enquiry's task. On Today, Tasks, the calendar and Search they could be
    ticked and opened to nothing -- Search sent every task to the Tasks page --
    so whoever had one went looking for the guest by name. Only the owner
    home's day list opened an enquiry. And the other way round: the booking
    page listed "Waiting on the house" and the stay's jobs with nothing to
    press, so settling one meant finding it again on Tasks.

    EIGHT ROWS FOR ONE ROOM. Arrival prep is made as eight tasks, each "urgent"
    and each carrying "The Okafor family arriving 22 October 2026, party of 4."
    An employee's Today held thirteen rows and eight were that room; Thursday
    on Tasks was sixteen cards; the owner home's day the same. The room board
    already read them as one room. The lists did not.

    A TICK THAT SAID IT HAD FAILED. In the Tasks week view -- the view Tasks
    opens on -- a tick saved and then said "Could not update that task": the
    script looked for a .task-row and the week's cards are .task-card, so it
    threw after the save had landed.

WHAT IS CHECKED. task_subject and task_entries directly; then every list
that draws a task, rendered, for the owner and for an employee who may not
open a booking; then the booking page's own tick; then the week view's tick,
by reading the selector the script uses against the cards it is pressed on.
"""
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from _harness import Suite, clients, db, ensure_room, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZTOPEN"
ROOM = f"{TAG} Room"
NOTE = f"{TAG} the Okafors arriving today, party of 4."
ITEMS = ("Wine bottle", "Hand sanitiser", "Fresh towels")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM waitlist_entries WHERE name LIKE ?", (f"{TAG}%",))
    conn.commit()
    conn.close()


def _setup(emp_id):
    """A stay arriving today with a guest's request and a three-item checklist
    on the employee, an enquiry with its task, and a task about nothing."""
    today = house_today().isoformat()
    room = ensure_room()
    conn = db()
    # arrival_prepped_at set, so opening the owner home does not add the
    # house's own eight-item prep to this stay and change the counts.
    cur = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at,
           source, arrival_prepped_at)
           VALUES (?, ?, ?, ?, ?, ?, date(?, '+2 days'), 4, 'confirmed', 500, ?, 'direct', ?)""",
        (room["id"], f"{TAG}REF", _harness.secrets_token(), f"{TAG} Okafor",
         "zztopen@example.invalid", today, today, _now(), _now()))
    booking_id = cur.lastrowid
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, notes, priority, due_date, status,
           created_at, origin, booking_id)
           VALUES (?, ?, 'They asked for a cot.', 'normal', ?, 'open', ?, 'guest_request', ?)""",
        (emp_id, f"{TAG} cot for the Okafors", today, _now(), booking_id))
    for item in ITEMS:
        conn.execute(
            """INSERT INTO tasks (assigned_to_user_id, title, room_note, priority, due_date,
               status, created_at, origin, booking_id)
               VALUES (?, ?, ?, 'high', ?, 'open', ?, 'checklist', ?)""",
            (emp_id, f"{ROOM}: {item}", NOTE, today, _now(), booking_id))
    cur = conn.execute(
        """INSERT INTO waitlist_entries (name, email, notes, status, created_at, source)
           VALUES (?, 'zztopen.enquiry@example.invalid', 'Around December 2026.', 'enquiry', ?,
                   'contact')""", (f"{TAG} Fontaine", _now()))
    entry_id = cur.lastrowid
    conn.execute(
        """INSERT INTO tasks (title, priority, due_date, status, created_at, origin,
           waitlist_entry_id)
           VALUES (?, 'normal', ?, 'open', ?, 'enquiry', ?)""",
        (f"Answer an enquiry from {TAG} Fontaine", today, _now(), entry_id))
    conn.execute(
        """INSERT INTO tasks (assigned_to_user_id, title, priority, due_date, status,
           created_at, origin)
           VALUES (?, ?, 'normal', ?, 'open', ?, 'manual')""",
        (emp_id, f"{TAG} sweep the terrace", today, _now()))
    conn.commit()
    tasks = {r["title"]: r for r in conn.execute(
        "SELECT * FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))}
    conn.close()
    return booking_id, entry_id, tasks


def _main(html):
    hit = re.search(r"<main\b.*?</main>", html, re.S)
    return hit.group(0) if hit else html


class _Ticks(HTMLParser):
    """For every toggleTask button, the classes of the elements around it."""

    def __init__(self):
        super().__init__()
        self.stack, self.found = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("input", "br", "img", "meta", "link"):
            return
        if tag == "button" and "toggleTask(" in (a.get("onclick") or ""):
            self.found.append([set((c or "").split()) for c in self.stack])
        self.stack.append(a.get("class") or "")

    def handle_endtag(self, tag):
        if tag in ("input", "br", "img", "meta", "link"):
            return
        if self.stack:
            self.stack.pop()


def run():
    s = Suite("A task opens what it is about, and a room's checklist reads as one")
    _cleanup()
    oc, ec, owner, emp = clients()
    booking_id, entry_id, tasks = _setup(emp["id"])
    booking_url = f"/admin/bookings/{booking_id}"
    entry_url = f"/admin/waitlist?when=all#entry-{entry_id}"
    try:
        s.section("Where a task opens, for whoever is looking")
        conn = db()
        owner_row = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
        emp_row = conn.execute("SELECT * FROM users WHERE id = ?", (emp["id"],)).fetchone()
        conn.close()
        cot = tasks[f"{TAG} cot for the Okafors"]
        enquiry = tasks[f"Answer an enquiry from {TAG} Fontaine"]
        sweep = tasks[f"{TAG} sweep the terrace"]
        with m.app.test_request_context("/"):
            got = m.task_subject(cot, owner_row)
            s.check("a guest's request opens the booking it came from",
                    got and got["url"] == booking_url and got["words"] == "Open the booking",
                    detail=str(got))
            got = m.task_subject(enquiry, owner_row)
            s.check("an enquiry's task opens the enquiry",
                    got and got["url"] == entry_url, detail=str(got))
            s.check("a task about nothing opens nothing of its own",
                    m.task_subject(sweep, owner_row) is None)
            reach = m.can_reach(emp_row, "admin_booking")
            s.check("never a page the person cannot open (a link that answers 403)",
                    reach or m.task_subject(cot, emp_row) is None,
                    detail="the employee here may open bookings" if reach else "")
        s.check("and nothing outside a request, where the assistant reads the day",
                m.task_subject(cot, owner_row) is None)

        s.section("A room's checklist is one entry, its items still their own ticks")
        conn = db()
        mine = conn.execute(
            """SELECT tasks.*, users.name AS employee_name FROM tasks
               LEFT JOIN users ON users.id = tasks.assigned_to_user_id
               WHERE tasks.title LIKE ? ORDER BY tasks.id""", (f"%{TAG}%",)).fetchall()
        conn.close()
        with m.app.test_request_context("/"):
            entries = m.task_entries(mine, owner_row)
        groups = [e for e in entries if e["group"]]
        s.check("the three items of one stay's checklist are one group",
                len(groups) == 1 and groups[0]["total"] == len(ITEMS),
                detail=str([(e["group"], e.get("heading") or e["task"]["title"]) for e in entries]))
        g = groups[0] if groups else {}
        s.check("named by its room and what is to be done",
                g.get("heading") == f"{ROOM}: get it ready", detail=str(g.get("heading")))
        s.check("its note said once, its items by their own names",
                g.get("note") == NOTE and [i["item"] for i in g.get("items", [])] == list(ITEMS),
                detail=str([i["item"] for i in g.get("items", [])]))
        s.check("urgent once, because an item in it is", g.get("urgent") is True)
        s.check("and it opens the stay", (g.get("subject") or {}).get("url") == booking_url)
        s.check("the other tasks stay as they are",
                sum(1 for e in entries if not e["group"]) == 3)

        s.section("Today, as the employee who has the work")
        page = _main(ec.get("/today").get_data(as_text=True))
        text = visible_text(page)
        s.check("the checklist is one block, headed by the room",
                text.count(f"{ROOM}: get it ready") == 1, detail=text[:200])
        s.check("and its note is printed once, not once an item",
                text.count(NOTE) == 1, detail=f"{text.count(NOTE)} times")
        s.check("saying how far it has got", f"0 of {len(ITEMS)} done" in text)
        # Their own list sits under the rooms and the guests, on purpose; the
        # top of the page says how many are on it and goes straight there.
        s.check("the top of the page goes straight down to the list",
                re.search(r'href="#my-list"[^>]*>[^<]*· \d+<', page) is not None
                and 'id="my-list"' in page)
        s.check("every item still a tick of its own",
                all(f'onclick="completeTask({t["id"]}, this)"' in page
                    for title, t in tasks.items() if title.startswith(ROOM)))
        s.check("each item by its own name, not the room's again",
                all(f">{item}<" in page for item in ITEMS)
                and f"{ROOM}: {ITEMS[0]}" not in text)
        start = page.find('class="check-group')
        block = page[start:page.find(f">{ITEMS[-1]}<", start) + 200] if start >= 0 else ""
        s.check("urgent said once for the room, not on every item",
                block.count(">urgent<") == 1, detail=f"{block.count('>urgent<')} times")
        if not m.can_reach(emp_row, "admin_booking"):
            s.check("no link the employee cannot follow", booking_url not in page)

        s.section("Tasks, the day and the week, as the owner")
        day = _main(oc.get(f"/admin/tasks?view=day&date={house_today().isoformat()}")
                    .get_data(as_text=True))
        dtext = visible_text(day)
        s.check("the day: the checklist once, with its note once",
                dtext.count(f"{ROOM}: get it ready") == 1 and dtext.count(NOTE) == 1,
                detail=f"{dtext.count(NOTE)} notes")
        s.check("the guest's request opens the booking",
                re.search(rf'href="{re.escape(booking_url)}"[^>]*>Open the booking', day) is not None)
        s.check("the enquiry's task opens the enquiry",
                f'href="{entry_url}"' in day)
        s.check("the lightning bolt and the arrow are words now",
                "⚡" not in day and "↻</button>" not in day and "Send now" in day
                and "Copy to next week" in day)
        week = _main(oc.get(f"/admin/tasks?view=week&date={house_today().isoformat()}")
                     .get_data(as_text=True))
        wtext = visible_text(week)
        s.check("the week: the checklist is one card",
                wtext.count(f"{ROOM}: get it ready") == 1 and wtext.count(NOTE) == 1,
                detail=f"{wtext.count(NOTE)} notes")
        s.check("which opens the stay", f'href="{booking_url}"' in week)

        s.section("The week's tick finds the card it is pressed on")
        base = open(m.app.jinja_loader.get_source(m.app.jinja_env, "base.html")[1],
                    encoding="utf-8").read()
        fn = base.split("function toggleTask", 1)[1][:900]
        sel = re.search(r"closest\('([^']+)'\)", fn)
        wanted = {c.strip().lstrip(".") for c in sel.group(1).split(",")} if sel else set()
        for name, html in (("week", week), ("day", day)):
            p = _Ticks()
            p.feed(html)
            lost = [i for i, around in enumerate(p.found)
                    if not any(cls & wanted for cls in around)]
            s.check(f"every tick on the {name} view sits in something the script looks for",
                    p.found and not lost,
                    detail=f"{len(lost)} of {len(p.found)} not found by closest({sel and sel.group(1)!r})")

        s.section("The owner home's day")
        conn = db()
        with m.app.test_request_context("/"):
            day_rows = m.owner_home_day(conn, house_today(), owner_row)
        conn.close()
        mine_rows = [r for r in day_rows if TAG in r["title"]]
        s.check("the checklist is one line on the day",
                [r["title"] for r in mine_rows].count(f"{ROOM}: get it ready") == 1
                and not any(r["title"].startswith(f"{ROOM}: {i}") for r in mine_rows for i in ITEMS),
                detail=str([r["title"] for r in mine_rows]))
        line = next((r for r in mine_rows if r["title"] == f"{ROOM}: get it ready"), {})
        s.check("saying how far it has got, and opening the stay",
                line.get("detail", "").startswith(f"0 of {len(ITEMS)} done")
                and line.get("href") == booking_url, detail=str(line))
        req = next((r for r in mine_rows if r["title"] == f"{TAG} cot for the Okafors"), {})
        s.check("a guest's request opens the booking, not the task list",
                req.get("href") == booking_url, detail=str(req.get("href")))
        # Some callers hold only an id and a name. Which pages somebody may
        # open is read off their whole record, and read off half of one it
        # raised -- the full run found it, from test_booking_com_stays.
        conn = db()
        try:
            with m.app.test_request_context("/"):
                partial = m.owner_home_day(conn, house_today(),
                                           {"id": owner["id"], "name": owner["name"]})
            err = None
        except Exception as e:  # noqa: BLE001 -- the failure is the finding
            partial, err = [], f"{type(e).__name__}: {e}"
        finally:
            conn.close()
        got = next((r for r in partial if r["title"] == f"{TAG} cot for the Okafors"), {})
        s.check("handed only part of the person, the day still draws, and opens the stay",
                err is None and got.get("href") == booking_url, detail=err or str(got.get("href")))

        s.section("The calendar's card and Search")
        cal = _main(oc.get(f"/calendar?view=day&date={house_today().isoformat()}")
                    .get_data(as_text=True))
        i = cal.find(f'cal-card-title">{TAG} cot for the Okafors')
        card = cal[i:i + 1500] if i >= 0 else ""
        s.check("the request's card opens the booking",
                re.search(rf'href="{re.escape(booking_url)}"[^>]*>Open the booking', card) is not None,
                detail=card[:200])
        found = _main(oc.get(f"/search?q={TAG}+cot").get_data(as_text=True))
        s.check("a task found by Search opens what it is about",
                re.search(rf'href="{re.escape(booking_url)}"[^>]*>\s*(?:<[^>]+>\s*)*{TAG} cot', found)
                is not None, detail=re.findall(r'href="([^"]+)"', found)[-6:])

        s.section("The booking page settles its own jobs")
        bpage = _main(oc.get(booking_url).get_data(as_text=True))
        s.check("each open request and job has its tick there",
                all(f'stayTaskDone({t["id"]}, this)' in bpage for title, t in tasks.items()
                    if t["booking_id"] == booking_id), detail=str(bpage.count("stayTaskDone(")))
        r = oc.post(f"/tasks/{cot['id']}/complete")
        conn = db()
        state = conn.execute("SELECT status FROM tasks WHERE id = ?", (cot["id"],)).fetchone()
        conn.close()
        s.check("pressing it settles the task", r.status_code == 200 and state["status"] == "done",
                detail=f"{r.status_code} {state['status']}")
        bpage = _main(oc.get(booking_url).get_data(as_text=True))
        s.check("and the request leaves Waiting on the house",
                f'stayTaskDone({cot["id"]}, this)' not in bpage
                and f"{TAG} cot for the Okafors" not in bpage)
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
