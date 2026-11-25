"""An occasion's prices, carried to Booking.com by hand (owner's brief, 25 Nov 2026).

Booking.com takes prices only from a certified channel manager, and the
cheapest costs more than a dozen price changes a year are worth. So the app
prepares the list and a person types it into the extranet under the owner's own
sign-in: the app never signs in to Booking.com and holds no password for it.

What this holds, each through the routes the owner uses:

  - A LIVE occasion makes one line per room it prices above (or below) that
    room's usual rate on those nights; a room at its usual price makes none, and
    a draft makes nothing at all.
  - A line not yet done FOLLOWS the occasion: a new price rewrites it in place.
    A line done is what Booking.com now shows, so a change after it is a NEW
    line, from the price entered to the new one.
  - Taking an occasion off sale after its prices were entered puts the nights
    BACK to the usual price; before they were entered, its lines are cancelled.
    Dates moved after entering: the nights left behind go back, the nights
    gained get the price, and the nights kept need nothing.
  - ONE TASK, "Booking.com prices to update", while any line is to do -- on the
    owner home, on the calendar, opening the list -- and it closes itself when
    the last line is marked done. The morning's watch run keeps it, too.
  - Mark done refuses a line that changed since the page was drawn, by name.
  - The list as JSON is the owner's: staff are refused and a visitor is sent to
    sign in.

No suite reaches Booking.com: nothing in this feature can.
"""
import json
from datetime import timedelta

from _harness import Suite, clients, db, flashes, free_window, keep_rows, secrets_token, visible_text
import _harness

m = _harness.m
TAG = "ZZBCP"
KING = TAG + " King Room"          # priced above usual, Booking.com name given
QUEEN = TAG + " Queen Room"        # priced above usual, no Booking.com name yet
TWIN = TAG + " Twin Room"          # priced AT its usual rate: no line
KING_ON_BCOM = TAG + " King Room with Mountain View"
USUAL = 200.0
TASK = "Booking.com prices to update"


def _cleanup(conn):
    conn.execute("DELETE FROM room_rate_overrides WHERE label = ?", (TAG,))
    conn.execute("DELETE FROM channel_price_pushes WHERE room_id IN "
                 "(SELECT id FROM rooms WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM channel_price_pushes WHERE occasion_name LIKE ?", (TAG + "%",))
    for table in ("stay_occasion_dinners", "stay_occasion_prices"):
        conn.execute(f"DELETE FROM {table} WHERE occasion_id IN "
                     "(SELECT id FROM stay_occasions WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM stay_occasions WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _room(conn, name, channel_name=None):
    conn.execute(
        """INSERT INTO rooms (name, channel_name, export_token, active, max_occupancy, max_adults,
               price_per_night, sort_order) VALUES (?, ?, ?, 1, 2, 2, ?, 98)""",
        (name, channel_name, secrets_token(), USUAL))
    conn.commit()
    return conn.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchone()


def _form(name, first, last, prices, status):
    f = {"name": name, "first_night": first.isoformat(), "last_night": last.isoformat(),
         "kind": "stay", "chef_on_site": "0", "status": status, "description": "", "photo": ""}
    for room_id, price in prices.items():
        f[f"price_{room_id}"] = str(price)
    return f


def _occasion(conn, name):
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _make(oc, conn, name, first, last, prices, status="live"):
    """Drafted through the new-occasion form, then saved with the status."""
    oc.post("/admin/stay-occasions/new", data=_form(name, first, last, prices, "draft"))
    occ = _occasion(conn, name)
    r = _save(oc, occ, first, last, prices, status) if occ else None
    return _occasion(conn, name), r


def _save(oc, occ, first, last, prices, status):
    return oc.post(f"/admin/stay-occasions/{occ['id']}",
                   data=_form(occ["name"], first, last, prices, status), follow_redirects=True)


def _lines(conn, room_id, state=None):
    sql = "SELECT * FROM channel_price_pushes WHERE room_id = ?"
    args = [room_id]
    if state:
        sql += " AND state = ?"
        args.append(state)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def _task(conn):
    return conn.execute("SELECT * FROM tasks WHERE origin = ? AND title = ? AND status != 'done' "
                        "ORDER BY id DESC LIMIT 1", (m.WATCH_TASK_ORIGIN, TASK)).fetchone()


def _mark_done(oc, rows, stamps=None):
    data = {"line": [str(r["id"]) for r in rows]}
    for r in rows:
        data[f"stamp_{r['id']}"] = (stamps or {}).get(r["id"], r["updated_at"])
    return oc.post("/admin/channels/booking-prices/done", data=data, follow_redirects=True)


def run():
    s = Suite("Booking.com prices, entered by hand")
    oc, ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasions", "status = 'live'"),
                keep_rows(conn, "tasks", "origin = ? AND title = ?", (m.WATCH_TASK_ORIGIN, TASK)),
                keep_rows(conn, "channel_price_pushes", "1 = 1")]
    # Only this suite's occasions are on sale, and only its lines are on the
    # list, for the run: the list is worked out across the whole house.
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.execute("DELETE FROM channel_price_pushes")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, ec, owner, conn)
    finally:
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, ec, owner, conn):
    today = m.house_today()
    king = _room(conn, KING, KING_ON_BCOM)
    queen = _room(conn, QUEEN)
    twin = _room(conn, TWIN)
    owner_row = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
    n0 = free_window(king["id"], 24, after_days=150, clear_of_ateliers=True,
                     clear_of_rate_overrides=True)
    a1, b1 = n0 + timedelta(days=2), n0 + timedelta(days=4)     # the first occasion: three nights
    prices = {king["id"]: 260, queen["id"]: 280, twin["id"]: USUAL}
    name1 = TAG + " Midsummer"

    s.section("A draft changes no price, so it makes no line")
    occ, r = _make(oc, conn, name1, a1, b1, prices, status="draft")
    s.check("the occasion is drafted", occ is not None and occ["status"] == "draft",
            response=r)
    s.check("and nothing is on the list for any of its rooms",
            not any(_lines(conn, x["id"]) for x in (king, queen, twin)))
    s.check("so there is no task", _task(conn) is None)

    s.section("On sale: one line a room priced off its usual rate, none for one at it")
    r = _save(oc, occ, a1, b1, prices, "live")
    occ = _occasion(conn, name1)
    s.check("it goes on sale", occ["status"] == "live", response=r)
    k, q, t = _lines(conn, king["id"]), _lines(conn, queen["id"]), _lines(conn, twin["id"])
    s.check("the King Room has one line to do", len(k) == 1 and k[0]["state"] == "to_do",
            detail=str([dict(x) for x in k]))
    s.check("the Queen Room has one line to do", len(q) == 1 and q[0]["state"] == "to_do",
            detail=str([dict(x) for x in q]))
    s.check("the Twin Room, at its usual price, has none", not t,
            detail="a line saying 'change €200 to €200' is a job for nobody: "
                   + str([dict(x) for x in t]))
    if k:
        s.check("the line is the occasion's nights, first and last included",
                (k[0]["first_night"], k[0]["last_night"]) == (a1.isoformat(), b1.isoformat()),
                detail=f"{k[0]['first_night']}..{k[0]['last_night']}")
        s.check("at the occasion's price, replacing the usual one",
                k[0]["price"] == 260 and k[0]["previous_price"] == USUAL and k[0]["kind"] == "occasion",
                detail=f"{k[0]['price']} for {k[0]['previous_price']}")
        s.check("and it knows its occasion", k[0]["occasion_id"] == occ["id"]
                and k[0]["occasion_name"] == name1 and k[0]["channel"] == "booking.com")
    said = " ".join(flashes(r))
    s.check("the save says so", "Booking.com: 2 prices to enter" in said, detail=said)

    s.section("One task while anything is to do")
    task = _task(conn)
    s.check("'Booking.com prices to update' is open", task is not None)
    if task:
        s.check("due today, so it is on today's list", task["due_date"] == today.isoformat())
        s.check("its note names the occasion and how many",
                "2 prices to enter" in task["notes"] and name1 in task["notes"], detail=task["notes"])
        s.check("and the room with no Booking.com name yet, and not the one with",
                QUEEN in task["notes"] and KING not in task["notes"], detail=task["notes"])
        with m.app.test_request_context("/"):
            subject = m.task_subject(task, owner_row)
        s.check("it opens the price list", subject and subject["url"] == "/admin/channels/booking-prices",
                detail=str(subject))
        with m.app.test_request_context("/"):
            day = m.owner_home_day(conn, today, owner_row)
        mine = [x for x in day if x["title"] == TASK]
        s.check("on the owner home's day, opening the list",
                len(mine) == 1 and mine[0].get("href") == "/admin/channels/booking-prices",
                detail=str(mine))
        home = oc.get("/").get_data(as_text=True)
        s.check("which the owner home draws", TASK in home)
        cal = visible_text(oc.get(f"/calendar?view=day&date={today.isoformat()}").get_data(as_text=True))
        s.check("and on the calendar", TASK in cal)
        found, _dropped = m.watch_task_findings(conn, today)
        s.check("the morning's watch run wants it too, so does not close it",
                any(f[1] == TASK for f in found))
        m.generate_watch_tasks(conn, today)
        s.check("and after that run it is still open", _task(conn) is not None)

    s.section("The page: grouped by occasion, ready to copy")
    page = oc.get("/admin/channels/booking-prices")
    html = page.get_data(as_text=True)
    text = visible_text(html)
    s.check("the page opens for the owner", page.status_code == 200)
    s.check("headed by the occasion", name1 in text)
    s.check("naming the room as Booking.com does", KING_ON_BCOM in text)
    s.check("and saying where that name is missing", "Booking.com name not given" in text)
    copy = html.split('data-copy-text>', 1)[1].split("</textarea>", 1)[0] if "data-copy-text>" in html else ""
    want = " | ".join((KING_ON_BCOM, m.format_date_human(a1.isoformat()),
                       m.format_date_human(b1.isoformat()), "260"))
    s.check("the copy block has the line as room | from | to | price",
            want in copy.replace("&amp;", "&"), detail=copy[:300])
    s.check("with the room that has no name flagged, not dropped",
            f"{QUEEN} (Booking.com name not given)" in copy, detail=copy[:300])
    s.check("and no line for the room at its usual price", TWIN not in copy)
    s.check("Mark done beside each line, and for the whole occasion",
            html.count(">Mark done</button>") >= 2 and "Mark all 2 done" in html)

    s.section("The list as data, for the owner's assistant")
    js = oc.get("/admin/channels/booking-prices.json")
    s.check("the owner reads it", js.status_code == 200 and js.mimetype == "application/json")
    data = js.get_json() or {}
    ours = [x for x in data.get("to_do", []) if x["house_room"].startswith(TAG)]
    s.check("both lines to do, and only those", sorted(x["house_room"] for x in ours) == [KING, QUEEN],
            detail=json.dumps(ours)[:300])
    kj = next((x for x in ours if x["house_room"] == KING), {})
    s.check("each with its Booking.com room, nights and prices",
            kj.get("booking_com_room") == KING_ON_BCOM and kj.get("first_night") == a1.isoformat()
            and kj.get("last_night") == b1.isoformat() and kj.get("price") == 260
            and kj.get("previous_price") == USUAL, detail=str(kj))
    s.check("and the missing name said as missing, not as the house's",
            QUEEN in data.get("missing_booking_com_names", [])
            and next((x for x in ours if x["house_room"] == QUEEN), {}).get("booking_com_room") is None)
    staff = ec.get("/admin/channels/booking-prices.json")
    s.check("staff are refused it", staff.status_code == 403, detail=str(staff.status_code))
    s.check("and staff cannot open the page either",
            ec.get("/admin/channels/booking-prices").status_code == 403)
    nobody = m.app.test_client().get("/admin/channels/booking-prices.json")
    s.check("a visitor is sent to sign in", nobody.status_code in (301, 302)
            and "login" in (nobody.headers.get("Location") or ""),
            detail=f"{nobody.status_code} {nobody.headers.get('Location')}")

    s.section("A new price before it is entered rewrites the line")
    before = _lines(conn, king["id"])[0]
    prices[king["id"]] = 270
    r = _save(oc, occ, a1, b1, prices, "live")
    k = _lines(conn, king["id"])
    s.check("still one line for the King Room, the same one",
            len(k) == 1 and k[0]["id"] == before["id"], detail=str([dict(x) for x in k]))
    s.check("now saying the new price", k and k[0]["price"] == 270 and k[0]["state"] == "to_do"
            and k[0]["previous_price"] == USUAL)
    s.check("the save says it changed to match", "changed to match" in " ".join(flashes(r)),
            detail=" ".join(flashes(r)))

    s.section("Mark done refuses a line that changed since the page was drawn")
    q = _lines(conn, queen["id"])[0]
    r = _mark_done(oc, [q], stamps={q["id"]: "drawn-before-it-changed"})
    s.check("the Queen line is not ticked", _lines(conn, queen["id"])[0]["state"] == "to_do")
    s.check("and the refusal names it", any(QUEEN in f and "changed since" in f for f in flashes(r)),
            detail=str(flashes(r)))

    s.section("Marked done: who and when")
    k = _lines(conn, king["id"])[0]
    r = _mark_done(oc, [k])
    k = _lines(conn, king["id"])[0]
    s.check("the King line is done", k["state"] == "done", response=r)
    s.check("by the owner, with the moment", k["done_by_user_id"] == owner["id"] and k["done_at"])
    s.check("the page says who", (owner["name"] or "") in visible_text(
        oc.get("/admin/channels/booking-prices?state=done").get_data(as_text=True)))
    task = _task(conn)
    s.check("the task stays while the Queen line is to do",
            task is not None and "1 price to enter" in task["notes"], detail=task and task["notes"])

    s.section("A new price after it is entered is a new line")
    prices[king["id"]] = 290
    _save(oc, occ, a1, b1, prices, "live")
    done = _lines(conn, king["id"], "done")
    todo = _lines(conn, king["id"], "to_do")
    s.check("the line done is left as it was entered", len(done) == 1 and done[0]["price"] == 270)
    s.check("and a new one is to do, from the price entered to the new one",
            len(todo) == 1 and todo[0]["price"] == 290 and todo[0]["previous_price"] == 270,
            detail=str([dict(x) for x in todo]))

    s.section("Every line of an occasion at once, and the task closes itself")
    allof = _lines(conn, king["id"], "to_do") + _lines(conn, queen["id"], "to_do")
    r = _mark_done(oc, allof)
    s.check("both ticked", all(x["state"] == "done" for x in
                               _lines(conn, king["id"]) + _lines(conn, queen["id"])), response=r)
    s.check("said as two", "Ticked off 2 prices" in " ".join(flashes(r)), detail=str(flashes(r)))
    s.check("nothing is left to do, so the task is closed", _task(conn) is None)
    s.check("and the copy block is gone with it",
            "data-copy-text" not in oc.get("/admin/channels/booking-prices").get_data(as_text=True))

    s.section("Off sale after it was entered: the nights go back to the usual price")
    r = _save(oc, occ, a1, b1, prices, "dismissed")
    s.check("it is dismissed", _occasion(conn, name1)["status"] == "dismissed", response=r)
    for room, was in ((king, 290), (queen, 280)):
        back = _lines(conn, room["id"], "to_do")
        s.check(f"{room['name'][len(TAG) + 1:]}: a line putting its nights back",
                len(back) == 1 and back[0]["kind"] == "usual" and back[0]["price"] == USUAL
                and back[0]["previous_price"] == was
                and (back[0]["first_night"], back[0]["last_night"]) == (a1.isoformat(), b1.isoformat()),
                detail=str([dict(x) for x in back]))
    s.check("so the task is back", _task(conn) is not None)
    _mark_done(oc, _lines(conn, king["id"], "to_do") + _lines(conn, queen["id"], "to_do"))
    s.check("and closes again once they are entered", _task(conn) is None)

    s.section("Off sale before it was entered: its line is cancelled")
    a2, b2 = n0 + timedelta(days=8), n0 + timedelta(days=9)
    name2 = TAG + " Harvest"
    occ2, r = _make(oc, conn, name2, a2, b2, {king["id"]: 300})
    line = [x for x in _lines(conn, king["id"]) if x["occasion_id"] == (occ2 or {"id": -1})["id"]]
    s.check("on sale, it has its line", len(line) == 1 and line[0]["state"] == "to_do", response=r)
    _save(oc, occ2, a2, b2, {king["id"]: 300}, "draft")
    line = [x for x in _lines(conn, king["id"]) if x["occasion_id"] == occ2["id"]]
    s.check("back to a draft, the line is cancelled and not reversed",
            len(line) == 1 and line[0]["state"] == "cancelled", detail=str([dict(x) for x in line]))
    s.check("saying why", line and "taken off sale" in (line[0]["note"] or ""))
    s.check("and no task is left behind", _task(conn) is None)

    s.section("Dates moved after it was entered")
    a3, b3 = n0 + timedelta(days=12), n0 + timedelta(days=14)
    name3 = TAG + " Festival"
    occ3, _r = _make(oc, conn, name3, a3, b3, {king["id"]: 250})
    _mark_done(oc, [x for x in _lines(conn, king["id"], "to_do") if x["occasion_id"] == occ3["id"]])
    moved_a, moved_b = a3 + timedelta(days=1), b3 + timedelta(days=1)
    _save(oc, occ3, moved_a, moved_b, {king["id"]: 250}, "live")
    todo = [x for x in _lines(conn, king["id"], "to_do") if x["occasion_id"] == occ3["id"]]
    back = [x for x in todo if x["kind"] == "usual"]
    gained = [x for x in todo if x["kind"] == "occasion"]
    s.check("the night left behind goes back to the usual price",
            len(back) == 1 and back[0]["first_night"] == back[0]["last_night"] == a3.isoformat()
            and back[0]["price"] == USUAL, detail=str([dict(x) for x in todo]))
    s.check("the night gained gets the price",
            len(gained) == 1 and gained[0]["first_night"] == gained[0]["last_night"] == moved_b.isoformat()
            and gained[0]["price"] == 250)
    s.check("and the nights kept need nothing", len(todo) == 2)

    s.section("Not done after all")
    first = gained[0] if gained else None
    if first:
        _mark_done(oc, [first])
        r = oc.post(f"/admin/channels/booking-prices/{first['id']}/undo", follow_redirects=True)
        again = conn.execute("SELECT * FROM channel_price_pushes WHERE id = ?", (first["id"],)).fetchone()
        s.check("a line marked done by mistake goes back on the list",
                again["state"] == "to_do" and again["done_at"] is None, response=r)
        s.check("and says so", any("back on the list" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("A night whose usual rate is the occasion's price needs nothing")
    conn.execute("INSERT INTO room_rate_overrides (room_id, start_date, end_date, price_per_night, "
                 "label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (queen["id"], (n0 + timedelta(days=17)).isoformat(),
                  (n0 + timedelta(days=17)).isoformat(), 310, TAG, m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    a4, b4 = n0 + timedelta(days=16), n0 + timedelta(days=18)
    occ4, r = _make(oc, conn, TAG + " Fair", a4, b4, {queen["id"]: 310})
    mine = [x for x in _lines(conn, queen["id"], "to_do") if x["occasion_id"] == (occ4 or {"id": -1})["id"]]
    s.check("the Queen Room gets the nights either side, not the middle one",
            [(x["first_night"], x["last_night"]) for x in mine]
            == [(a4.isoformat(), a4.isoformat()), (b4.isoformat(), b4.isoformat())],
            detail=str([(x["first_night"], x["last_night"], x["price"]) for x in mine]))

    s.section("Deleted after it was entered: back to the usual price")
    _mark_done(oc, mine)
    s.check("its two lines are entered", all(
        conn.execute("SELECT state FROM channel_price_pushes WHERE id = ?", (x["id"],)).fetchone()["state"]
        == "done" for x in mine) and len(mine) == 2)
    r = oc.post(f"/admin/stay-occasions/{occ4['id']}/delete", follow_redirects=True)
    back = [x for x in _lines(conn, queen["id"], "to_do") if x["occasion_name"] == TAG + " Fair"]
    s.check("both of its nights go back, still named for it",
            len(back) == 2 and all(x["kind"] == "usual" and x["price"] == USUAL for x in back),
            detail=str([dict(x) for x in back]))
    s.check("and the deletion says so", any("Booking.com: 2 prices to enter" in f for f in flashes(r)),
            detail=str(flashes(r)))
    conn.execute("DELETE FROM room_rate_overrides WHERE room_id = ?", (queen["id"],))
    conn.commit()

    s.section("Booking.com's names for the rooms")
    r = oc.post("/admin/channels/booking-names",
                data={f"booking_name_{queen['id']}": f"  {TAG}   Queen Room  ",
                      f"booking_name_{king['id']}": KING_ON_BCOM}, follow_redirects=True)
    got = conn.execute("SELECT channel_name FROM rooms WHERE id = ?", (queen["id"],)).fetchone()
    s.check("saved, its spaces tidied", got["channel_name"] == f"{TAG} Queen Room",
            detail=repr(got["channel_name"]), response=r)
    task = _task(conn)
    s.check("and the task stops asking for it", task is not None and QUEEN not in task["notes"],
            detail=task and task["notes"])
    oc.post("/admin/channels/booking-names", data={f"booking_name_{queen['id']}": "   "})
    s.check("blank stores nothing, as the room's own form does",
            conn.execute("SELECT channel_name FROM rooms WHERE id = ?",
                         (queen["id"],)).fetchone()["channel_name"] is None)

    s.section("A line whose nights pass before it is entered falls off")
    gone_a, gone_b = today - timedelta(days=5), today - timedelta(days=3)
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO channel_price_pushes (occasion_id, occasion_name, channel, room_id, kind,
               first_night, last_night, price, previous_price, state, created_at, updated_at)
           VALUES (NULL, ?, 'booking.com', ?, 'occasion', ?, ?, 333, 200, 'to_do', ?, ?)""",
        (TAG + " Long Ago", twin["id"], gone_a.isoformat(), gone_b.isoformat(), now, now))
    m.sync_booking_prices(conn, today)
    conn.commit()
    old = conn.execute("SELECT * FROM channel_price_pushes WHERE occasion_name = ?",
                       (TAG + " Long Ago",)).fetchone()
    s.check("it is cancelled, and says its nights passed",
            old["state"] == "cancelled" and "passed" in (old["note"] or ""), detail=dict(old))
