"""Seven lists that had a dropdown of their own now have the chips every list has.

WHAT WENT WRONG. The house standard is one toolbar per list -- search, counted
chips, sort -- and these seven each narrowed themselves some other way:
Incidents with four tabs beside the heading, Inbox Flags with two dropdowns
behind a Filter button AND a row of inbox buttons under them, Room Issues with
a status dropdown in a bar of its own ABOVE the chips it already had,
Candidates and the Social schedule with dropdowns that counted nothing,
Timesheets with a person dropdown beside the dates, and Time Off with nothing
at all -- fifty-one requests drawn one card each, with the clash checks run
for every one of them to draw the page. The audit log and the mail log
stopped at the most recent 400 and said so in a line.

WHAT THIS PINS.
  - Each page draws the toolbar, and no dropdown that narrows the list.
  - Each still opens where it did (open incidents, open flags, open issues),
    and a link written before the chips -- ?status=, ?kind=, ?mailbox=,
    ?employee_id= -- lands on the same rows it always did.
  - Timesheets: the hours table follows the chips, and a blocker from before
    the dates is listed to be fixed without being added to them.
  - Time Off: the tiles count every request, not the page of them.
  - Inbox Flags: resolving one goes back to the list as it was being looked
    at, and only ever to that page.
  - The logs page through everything.
  - What the card earns says each verdict's reasoning once, and calls no dish
    a Dog when nothing was sold to judge by.
"""
import re
from datetime import timedelta
from urllib.parse import unquote_plus

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "zzchips"
# The time entries this suite makes, by id: a time entry has nothing to carry
# a tag in, and a leftover shift would be somebody's hours in the next suite.
MADE_ENTRIES = []


def _now():
    return m.datetime.now(m.timezone.utc).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM incidents WHERE summary LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM candidates WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM social_posts WHERE caption LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_flags WHERE graph_message_id LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM room_issues WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.executemany("DELETE FROM time_entries WHERE id = ?", [(i,) for i in MADE_ENTRIES])
    MADE_ENTRIES.clear()
    conn.execute("DELETE FROM audit_log WHERE action LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_order_lines WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_item_ingredients WHERE note LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _costed_card(conn, now):
    """Two dishes with a recipe, one of them sold. The copy's card has no
    recipe costed at all, so without these every dish is 'not costed' either
    way and nothing below could fail."""
    conn.execute("INSERT INTO stock_items (name, category, unit, unit_cost, active, created_at) "
                 "VALUES (?, 'food', 'kg', 3.0, 1, ?)", (TAG + " flour", now))
    flour = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                         (TAG + " flour",)).fetchone()["id"]
    dishes = {}
    for label in ("Tart", "Soup"):
        conn.execute("INSERT INTO menu_items (name, category, course, price, active, created_at) "
                     "VALUES (?, 'main', 'main', 20.0, 1, ?)", (f"{TAG} {label}", now))
        dishes[label] = conn.execute("SELECT id FROM menu_items WHERE name = ?",
                                     (f"{TAG} {label}",)).fetchone()["id"]
        conn.execute("INSERT INTO menu_item_ingredients (menu_item_id, stock_item_id, quantity, "
                     "note, created_at) VALUES (?, ?, 1, ?, ?)",
                     (dishes[label], flour, TAG + " recipe", now))
    conn.execute("INSERT INTO pos_orders (table_label, status, opened_at, closed_at) "
                 "VALUES (?, 'paid', ?, ?)", (TAG + " T1", now, now))
    order = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                         (TAG + " T1",)).fetchone()["id"]
    conn.execute("INSERT INTO pos_order_lines (order_id, menu_item_id, name, unit_price, "
                 "quantity, voided, created_at) VALUES (?, ?, ?, 20.0, 30, 0, ?)",
                 (order, dishes["Tart"], TAG + " Tart", now))
    conn.commit()


def _chip(page, facet_label, chip):
    """The count printed on one chip of one facet row, or None."""
    row = re.search(r'<span class="facet-label">%s</span>(.*?)</div>' % re.escape(facet_label),
                    page, re.S)
    if not row:
        return None
    hit = re.search(r'>\s*%s <span class="chip-n">(\d+)</span>' % re.escape(chip), row.group(1))
    return int(hit.group(1)) if hit else None


def _listed(page):
    """The list itself, from its toolbar down. Incidents has a panel above it
    of open ones the insurer has not been told about, whatever the chips say,
    and that panel is right to ignore them."""
    at = page.find(LIST_SEARCH)
    return page[at:] if at >= 0 else ""


# The list's own toolbar. Not class="list-toolbar": the saved-views strip in
# base.html wears that class too, on every staff page, so a check for it
# passes on a page with no list toolbar at all.
LIST_SEARCH = 'class="list-search"'


def _narrowing_selects(page):
    """Dropdowns in a GET form -- a list narrowing itself outside the toolbar.
    The toolbar's own sort menu is the one allowed."""
    out = []
    for form in re.findall(r'<form[^>]*method="get"[^>]*>(.*?)</form>', page, re.S | re.I):
        for name in re.findall(r'<select[^>]*name="([^"]+)"', form):
            if name != "sort":
                out.append(name)
    return out


def run():
    s = Suite("seven lists with chips rather than dropdowns")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)
    today = house_today()
    now = _now()

    # ------------------------------------------------------------- fixtures
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    conn.executemany(
        "INSERT INTO incidents (kind, occurred_at, summary, severity, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [("guest", now, TAG + " open spill", "minor", "open", now),
         ("property", now, TAG + " closed window", "near_miss", "closed", now)])
    conn.executemany(
        "INSERT INTO candidates (name, role_applied, status, created_at) VALUES (?, ?, ?, ?)",
        [(TAG + " New Person", "Commis", "new", now),
         (TAG + " Hired Person", "Commis", "hired", now)])
    conn.executemany(
        "INSERT INTO social_posts (platform, caption, status, scheduled_date, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [("Instagram", TAG + " an idea", "idea", None, now),
         ("Facebook", TAG + " gone out", "posted", today.isoformat(), now)])
    conn.execute(
        "INSERT INTO mailbox_routing (mailbox, label, active, created_at) VALUES (?, ?, 1, ?)",
        (TAG + "@example.invalid", "Zz Chips Inbox", now))
    conn.executemany(
        "INSERT INTO email_flags (graph_message_id, from_address, subject, received_at, "
        "unanswered, price_conflict, status, mailbox, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(TAG + "-1", "a@example.invalid", TAG + " unanswered one", now, 1, 0, "open",
          TAG + "@example.invalid", now, now),
         (TAG + "-2", "b@example.invalid", TAG + " wrong price", now, 0, 1, "open",
          TAG + "@example.invalid", now, now),
         (TAG + "-3", "c@example.invalid", TAG + " dealt with", now, 1, 0, "resolved",
          TAG + "@example.invalid", now, now)])
    if room:
        conn.executemany(
            "INSERT INTO room_issues (room_id, title, status, created_at) VALUES (?, ?, ?, ?)",
            [(room["id"], TAG + " dripping tap", "open", now),
             (room["id"], TAG + " fixed lamp", "resolved", now)])
    conn.commit()

    # ---------------------------------------------------------------- shape
    s.section("Each list draws the toolbar, and nothing else narrows it")
    pages = ["/admin/incidents", "/admin/inbox-flags", "/room-issues", "/candidates",
             "/management/social", "/admin/timesheets", "/admin/leave",
             "/admin/audit-log", "/admin/mail-log"]
    for url in pages:
        r = oc.get(url)
        page = r.get_data(as_text=True)
        s.check(f"{url} draws", r.status_code == 200, detail=str(r.status_code))
        s.check(f"{url} has the toolbar", LIST_SEARCH in page and 'class="facet-row"' in page)
        extra = _narrowing_selects(page)
        s.check(f"{url} has no dropdown of its own narrowing it", not extra,
                detail=f"select name(s): {extra}")

    # ------------------------------------------------- opens where it did
    s.section("Each opens where it did, and an old link lands where it did")
    page = oc.get("/admin/incidents").get_data(as_text=True)
    listed = _listed(page)
    s.check("incidents open on the open ones",
            TAG + " open spill" in listed and TAG + " closed window" not in listed)
    page = oc.get("/admin/incidents?status=closed").get_data(as_text=True)
    listed = _listed(page)
    s.check("?status=closed shows the closed one",
            TAG + " closed window" in listed and TAG + " open spill" not in listed)
    page = oc.get("/admin/incidents?status=").get_data(as_text=True)
    listed = _listed(page)
    s.check("and the old All tab, ?status= with nothing, shows the whole register",
            TAG + " closed window" in listed and TAG + " open spill" in listed)
    page = oc.get("/admin/incidents?status=all").get_data(as_text=True)
    listed = _listed(page)
    s.check("as does ?status=all",
            TAG + " closed window" in listed and TAG + " open spill" in listed)
    s.check("severity is a chip, in the words the form uses",
            _chip(page, "How bad", m.INCIDENT_SEVERITIES["near_miss"]) is not None)

    page = oc.get("/admin/inbox-flags").get_data(as_text=True)
    s.check("flags open on the open ones",
            TAG + " unanswered one" in page and TAG + " dealt with" not in page)
    page = oc.get("/admin/inbox-flags?kind=conflict").get_data(as_text=True)
    s.check("?kind=conflict is the wrong price and not the unanswered one",
            TAG + " wrong price" in page and TAG + " unanswered one" not in page)
    page = oc.get("/admin/inbox-flags?status=all&kind=unanswered").get_data(as_text=True)
    s.check("?status=all&kind=unanswered has both unanswered ones",
            TAG + " unanswered one" in page and TAG + " dealt with" in page
            and TAG + " wrong price" not in page)
    page = oc.get(f"/admin/inbox-flags?mailbox={TAG}@example.invalid&status=all").get_data(as_text=True)
    s.check("?mailbox= still narrows to one inbox, and the chip carries its label",
            _chip(page, "Inbox", "Zz Chips Inbox") == 3,
            detail=str(_chip(page, "Inbox", "Zz Chips Inbox")))
    s.check("the routing fields name their inbox to a screen reader",
            f'aria-label="What to call the {TAG}@example.invalid inbox"' in page,
            detail="mailbox_routing has no address column; they read 'the  inbox'")

    if room:
        page = oc.get("/room-issues").get_data(as_text=True)
        s.check("room issues open on the open ones",
                TAG + " dripping tap" in page and TAG + " fixed lamp" not in page)
        page = oc.get("/room-issues?status=resolved").get_data(as_text=True)
        s.check("?status=resolved shows the resolved one",
                TAG + " fixed lamp" in page and TAG + " dripping tap" not in page)

    page = oc.get("/candidates?status=hired").get_data(as_text=True)
    s.check("?status=hired is the hired one",
            TAG + " Hired Person" in page and TAG + " New Person" not in page)
    page = oc.get("/candidates").get_data(as_text=True)
    s.check("and with no status, everyone", TAG + " Hired Person" in page
            and TAG + " New Person" in page)

    page = oc.get("/management/social?status=posted&platform=Facebook").get_data(as_text=True)
    s.check("?status=&platform= on the social schedule narrow as they did",
            TAG + " gone out" in page and TAG + " an idea" not in page)

    # ---------------------------------------------------------- timesheets
    s.section("Timesheets: the hours follow the chips")
    yesterday = m.datetime.now(m.timezone.utc) - timedelta(days=1)
    for uid, hours in ((emp["id"], 4), (emp["id"], 3), (owner["id"], 2)):
        start = yesterday.replace(hour=8, minute=0, second=0, microsecond=0)
        cur = conn.execute(
            "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) "
            "VALUES (?, ?, ?, 0)",
            (uid, start.isoformat(), (start + timedelta(hours=hours)).isoformat()))
        MADE_ENTRIES.append(cur.lastrowid)
    # From well before the dates, and broken: it blocks payroll, so it is
    # shown -- and it is no part of this fortnight's hours.
    old = (m.datetime.now(m.timezone.utc) - timedelta(days=70)).replace(microsecond=0)
    cur = conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) "
        "VALUES (?, ?, ?, 0)", (emp["id"], old.isoformat(),
                                (old - timedelta(hours=1)).isoformat()))
    blocker = cur.lastrowid
    MADE_ENTRIES.append(blocker)
    conn.commit()

    r = oc.get(f"/admin/timesheets?employee_id={emp['id']}&start={today - timedelta(days=3)}")
    loc = unquote_plus(r.headers.get("Location", ""))
    s.check("a link with employee_id lands on that person's chip",
            r.status_code == 302 and f"who={emp['name']}" in loc
            and "employee_id" not in loc and "start=" in loc,
            detail=f"{r.status_code} {loc}")

    def totals(page):
        table = re.search(r"Hours in these dates.*?</table>", page, re.S)
        return re.findall(r'<th scope="row">([^<]+)</th>\s*<td class="num">(\d+)</td>',
                          table.group(0)) if table else []

    whole = oc.get("/admin/timesheets").get_data(as_text=True)
    names = [n for n, _ in totals(whole)]
    s.check("with nobody chosen, both people are in the hours",
            emp["name"] in names and owner["name"] in names, detail=str(names))
    one = oc.get(f"/admin/timesheets?who={emp['name']}").get_data(as_text=True)
    rows = totals(one)
    s.check("choose one and the hours are theirs alone",
            [n for n, _ in rows] == [emp["name"]], detail=str(rows))
    in_window = [e for e in m.timesheet_query(conn, "", today - timedelta(days=13), today)
                 if e["user_id"] == emp["id"]]
    s.check("and the blocker from before the dates is not counted in them",
            rows and int(rows[0][1]) == len(in_window),
            detail=f"{rows} against {len(in_window)} shifts in the dates")
    s.check("but it is on the page, with its repair form",
            f"/admin/timesheets/{blocker}/repair" in one)
    s.check("under a chip of its own",
            (_chip(one, "When", "Earlier, still to fix") or 0) >= 1)

    # ------------------------------------------------------------ time off
    s.section("Time Off: the tiles count every request, not the page")
    future = today + timedelta(days=40)
    conn.executemany(
        "INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type, "
        "status, requested_at) VALUES (?, ?, ?, ?, 'vacation', ?, ?)",
        [(emp["id"], (future + timedelta(days=i * 3)).isoformat(),
          (future + timedelta(days=i * 3 + 1)).isoformat(), f"{TAG} {i}", "pending", now)
         for i in range(60)])
    conn.commit()
    pending = conn.execute("SELECT COUNT(*) FROM leave_requests WHERE status = 'pending'"
                           ).fetchone()[0]
    page = oc.get("/admin/leave").get_data(as_text=True)
    tile = re.search(r'<span class="stat-tile-value">(\d+)</span><span class="stat-tile-label">'
                     r'Awaiting your decision', page)
    s.check("the waiting tile counts every pending request",
            tile and int(tile.group(1)) == pending,
            detail=f"tile {tile.group(1) if tile else None}, {pending} pending")
    s.check("which is more than one page holds, so it pages",
            'class="list-pager"' in page)
    s.check("the waiting chip agrees",
            _chip(page, "Where it stands", "Waiting on you") == pending,
            detail=str(_chip(page, "Where it stands", "Waiting on you")))
    s.check("and the leave settings come after the list, folded",
            page.find('class="list-pager"') < page.find("How leave is earned")
            and re.search(r"<details[^>]*>\s*<summary><strong>How leave is earned", page))

    # ---------------------------------------------------------- inbox flags
    s.section("Resolving a flag goes back to the list as it was")
    flag = conn.execute("SELECT id FROM email_flags WHERE graph_message_id = ?",
                        (TAG + "-1",)).fetchone()["id"]
    back = f"/admin/inbox-flags?kind=unanswered&q={TAG}"
    r = oc.post(f"/admin/inbox-flags/{flag}/assign", data={"user_id": "", "back": back})
    s.check("to the same chips and search", r.headers.get("Location", "").endswith(back),
            detail=r.headers.get("Location"))
    for bad in ("https://example.invalid/admin/inbox-flags", "//example.invalid/x",
                "/admin/inbox-flags/1/resolve", "/admin/leave"):
        r = oc.post(f"/admin/inbox-flags/{flag}/assign", data={"user_id": "", "back": bad})
        loc = r.headers.get("Location", "")
        s.check(f"and never anywhere else: {bad}",
                "example.invalid" not in loc and loc.split("?")[0].endswith("/admin/inbox-flags"),
                detail=loc)
    r = oc.post(f"/admin/inbox-flags/{flag}/resolve", data={"return_status": "all"})
    s.check("a form from before the chips still lands on its status",
            "status=all" in r.headers.get("Location", ""), detail=r.headers.get("Location"))

    # ----------------------------------------------------------------- logs
    s.section("The logs page through everything")
    conn.executemany(
        "INSERT INTO audit_log (actor_user_id, action, target, details, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(owner["id"], TAG + "_paging", f"row {i}", None, now) for i in range(460)])
    conn.commit()
    page = oc.get(f"/admin/audit-log?q={TAG}_paging").get_data(as_text=True)
    s.check("no 'most recent 400' line", "most recent 400" not in page)
    s.check("a pager instead", 'class="list-pager"' in page)
    last = oc.get(f"/admin/audit-log?q={TAG}_paging&page=10").get_data(as_text=True)
    s.check("and the last page reaches the oldest of them", "row 0" in last
            and "Showing 451&ndash;460 of 460" in last.replace("–", "&ndash;"),
            detail=re.search(r"Showing [^<]*", last).group(0) if "Showing" in last else "")

    # ------------------------------------------------------ the card earns
    s.section("What the card earns says each reason once")
    _costed_card(conn, now)
    with m.app.test_request_context():
        # A week in 2000: the fixture's sale is not in it, nor is anything else.
        bare = m.menu_engineering(conn, days=7, today=m.date(2000, 1, 7))
        card = m.menu_engineering(conn, days=90, today=today)
    s.check("there are dishes with a recipe to judge",
            sum(1 for r in bare["rows"] if r["margin"] is not None
                and r["name"].startswith(TAG)) == 2,
            detail="with none, the two checks below pass whatever the code does")
    s.check("with nothing sold, no dish is called a Dog",
            not any(r["quadrant"] for r in bare["rows"]),
            detail=str([(r["name"], r["label"]) for r in bare["rows"] if r["quadrant"]][:3]))
    s.check("and a costed dish says why it is not placed",
            all(r["label"] == "Nothing sold to judge by"
                for r in bare["rows"] if r["margin"] is not None))
    placed = [r for r in card["rows"] if r["name"].startswith(TAG) and r["quadrant"]]
    s.check("and once the tart has sold, both are placed", len(placed) == 2,
            detail=str([(r["name"], r["label"]) for r in card["rows"]
                        if r["name"].startswith(TAG)]))
    # The window is the till's days, both ends. A week ending Saturday 10
    # March 2001: half past one on Sunday morning is still Saturday's
    # service, half past five is not; and at the far end, four in the
    # morning on the 3rd is the 2nd's service, half past five is the 3rd's.
    day = m.date(2001, 3, 10)
    order = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                         (TAG + " T1",)).fetchone()["id"]
    soup = conn.execute("SELECT id FROM menu_items WHERE name = ?",
                        (TAG + " Soup",)).fetchone()["id"]
    for (y, mo, d, h, mi), qty in (((2001, 3, 11, 1, 30), 1), ((2001, 3, 11, 5, 30), 10),
                                   ((2001, 3, 3, 4, 0), 100), ((2001, 3, 3, 5, 30), 1000)):
        stamp = m.datetime(y, mo, d, h, mi, tzinfo=m.LOCAL_TZ).astimezone(m.timezone.utc)
        conn.execute("INSERT INTO pos_order_lines (order_id, menu_item_id, name, unit_price, "
                     "quantity, voided, created_at) VALUES (?, ?, ?, 20.0, ?, 0, ?)",
                     (order, soup, TAG + " Soup", qty, stamp.isoformat()))
    conn.commit()
    with m.app.test_request_context():
        week = m.menu_engineering(conn, days=7, today=day)
    sold = next((r["qty"] for r in week["rows"] if r["name"] == TAG + " Soup"), None)
    s.check("the card's week is the till's days, at both ends", sold == 1001,
            detail=f"{sold} sold; 1001 is the 01:30 and the 05:30 inside it -- "
                   "1010 or 1100 is a UTC day, and anything over 1111 has no end")

    page = oc.get("/admin/menu-engineering").get_data(as_text=True)
    s.check("each placed dish's verdict points at what it means",
            all(f'href="#what-{r["quadrant"]}"' in page for r in placed) and placed)
    for key, (name, advice) in m.MENU_QUADRANTS.items():
        said = page.count(advice)
        s.check(f"what a {name} means is said once", said <= 1, detail=f"{said} times")
    s.check("and every name in the table points at its meaning",
            all(f'id="what-{k}"' in page for k in m.MENU_QUADRANTS))

    s.section("An employee sees none of it")
    for url in ("/admin/incidents", "/admin/inbox-flags", "/admin/timesheets", "/admin/leave"):
        s.check(f"{url} is not theirs", ec.get(url).status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
