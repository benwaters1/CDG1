"""Extras managed from the back: arranged, then handed over, and seen coming.

The owner, 17 October 2026, after the first real booking: a stay and its
extras were "impossible to manage from the back, it's not clear". The extras
side of that was four things.

- There was no step between a guest asking and it being handed over. A
  charcuterie board ordered from the épicerie and one nobody had thought about
  looked the same, and so did a car booked and a car not.
- Nothing said WHEN something had to be arranged. The catalogue holds the
  notice each extra needs and the run sheet never read it, so a board needing
  three days was a job on the morning it was due.
- The catalogue was a price list: a name and a price, nothing about who had
  ordered what or when the next one was. And deleting an extra somebody had
  been sold left their bill pointing at nothing.
- What had already been sold on stays still to come was on no page that
  counts money, and the extras on a stay waiting for the owner's decision were
  on no list at all.

Each is checked here against the page or the figure the owner reads.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZBOARD"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute("SELECT id FROM bookings WHERE guest_name LIKE ?",
                                         (TAG + "%",))]
    for bid in ids:
        conn.execute("DELETE FROM stock_movements WHERE booking_extra_id IN "
                     "(SELECT id FROM booking_extras WHERE booking_id = ?)", (bid,))
        conn.execute("DELETE FROM booking_extras WHERE booking_id = ?", (bid,))
        conn.execute("DELETE FROM vehicle_transfers WHERE booking_id = ?", (bid,))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicle_transfers WHERE vehicle_id IN "
                 "(SELECT id FROM vehicles WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE origin = ? AND title LIKE ?",
                 (m.WATCH_TASK_ORIGIN, f"%{TAG}%"))
    conn.commit()
    conn.close()


def _extra_kind(name, category, price, notice):
    conn = db()
    conn.execute("""INSERT INTO extras (name, price, active, sort_order, category,
                        lead_time_days, guest_bookable) VALUES (?, ?, 1, 900, ?, ?, 1)""",
                 (f"{TAG} {name}", price, category, notice))
    conn.commit()
    row = conn.execute("SELECT * FROM extras WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _stay(ref, arrive, *, nights=3, status="confirmed", phone="+33 6 11 22 33 44"):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               guest_phone, arrival_date, departure_date, party_size, status, total_price,
               amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, 300, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid", phone, arrive.isoformat(),
         (arrive + timedelta(days=nights)).isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _sell(booking, extra, on, notes=None):
    conn = db()
    line_id = m.add_booking_extra(conn, "room", booking["id"], extra,
                                  scheduled_for=on.isoformat() if on else None, notes=notes)
    conn.commit()
    conn.close()
    return line_id


def _board():
    conn = db()
    try:
        return m.extras_due(conn)
    finally:
        conn.close()


def _row(board, line_id, key="rows"):
    return next((r for r in board[key] if r["line"]["id"] == line_id), None)


def run():
    s = Suite("Extras managed from the back")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    today = m.service_day()
    try:
        transfer = _extra_kind("airport transfer", "transfer", 250, 0)
        board = _extra_kind("charcuterie board", "food", 80, 3)
        wine = _extra_kind("bottle of wine", "drinks", 40, 0)

        stay = _stay("A", today + timedelta(days=10))
        t_line = _sell(stay, transfer, today + timedelta(days=10), notes="AF7520 lands 14:35")
        b_line = _sell(stay, board, today + timedelta(days=11))
        w_line = _sell(stay, wine, today + timedelta(days=12))
        soon = _stay("B", today + timedelta(days=2))
        soon_board = _sell(soon, board, today + timedelta(days=2))

        s.section("When each thing has to be arranged")
        b = _board()
        r = _row(b, b_line)
        s.check("a board needing three days' notice is to be arranged three days ahead",
                r and r["needs_arranging"]
                and r["arrange_by"] == (today + timedelta(days=8)).isoformat(),
                detail=str(r and r["arrange_by"]))
        r = _row(b, t_line)
        s.check("a transfer with no notice set is still to be arranged the day before",
                r and r["transfer"] and r["arrange_by"] == (today + timedelta(days=9)).isoformat(),
                detail=str(r and r["arrange_by"]))
        r = _row(b, w_line)
        s.check("a bottle from the cellar has nothing to arrange",
                r and not r["needs_arranging"] and r["arrange_by"] is None)
        r = _row(b, soon_board)
        s.check("one whose day is closer than its notice is to be arranged now",
                r and r["arrange_now"], detail=str(r and r["arrange_by"]))
        s.check("and only that one", [x["line"]["id"] for x in b["arrange_now"]
                                      if TAG in x["line"]["name"]] == [soon_board])

        s.section("Arranged, by whoever sorted it")
        resp = ec.post(f"/extras/{soon_board}/arranged", data={"note": "Ordered from the épicerie"})
        s.check("anybody on shift can say so", resp.status_code in (302, 303))
        conn = db()
        line = conn.execute("SELECT * FROM booking_extras WHERE id = ?", (soon_board,)).fetchone()
        conn.close()
        s.check("it records who and when, and the note",
                bool(line["arranged_at"]) and line["arranged_by_user_id"] is not None
                and line["arranged_note"] == "Ordered from the épicerie")
        s.check("it changes no money: the line is still confirmed and still on the bill",
                line["status"] == "confirmed")
        r = _row(_board(), soon_board)
        s.check("and it is no longer to arrange now", r and r["arranged"] and not r["arrange_now"])
        stamp = line["arranged_at"]
        ec.post(f"/extras/{soon_board}/arranged", data={"note": "Ready on Friday"})
        conn = db()
        again = conn.execute("SELECT * FROM booking_extras WHERE id = ?", (soon_board,)).fetchone()
        conn.close()
        s.check("saying more updates the note, and not who or when",
                again["arranged_note"] == "Ready on Friday" and again["arranged_at"] == stamp)
        ec.post(f"/extras/{soon_board}/arranged", data={"undo": "1"})
        r = _row(_board(), soon_board)
        s.check("it can be undone", r and not r["arranged"] and r["arrange_now"])

        s.section("A transfer is arranged by booking the car")
        # Its own car, so the check does not lean on whatever the database it
        # runs on happens to hold: a fresh seed has no vehicle at all, and this
        # passed only on a copy of the house's (another session found it).
        conn = db()
        conn.execute("INSERT INTO vehicles (name, created_at) VALUES (?, ?)",
                     (f"{TAG} car", datetime.now(timezone.utc).isoformat()))
        conn.commit()
        vehicle = conn.execute("SELECT id FROM vehicles WHERE name = ?", (f"{TAG} car",)).fetchone()
        if vehicle:
            conn.execute(
                """INSERT INTO vehicle_transfers (vehicle_id, guest_name, direction, scheduled_at,
                       created_at, transfer_type, booking_id)
                   VALUES (?, ?, 'pickup', ?, ?, 'airport', ?)""",
                (vehicle["id"], stay["guest_name"], (today + timedelta(days=10)).isoformat() + "T12:35:00",
                 datetime.now(timezone.utc).isoformat(), stay["id"]))
            conn.commit()
        conn.close()
        r = _row(_board(), t_line)
        s.check("a run on the Transfers page for the stay counts as arranged",
                bool(vehicle) and r and r["arranged"] and r["arranged_by_run"])

        s.section("A stay not yet confirmed")
        waiting = _stay("C", today + timedelta(days=5), status="pending")
        w_wait = _sell(waiting, transfer, today + timedelta(days=5))
        b = _board()
        s.check("its extras are not jobs yet", _row(b, w_wait) is None)
        s.check("but they are on the board for the owner", _row(b, w_wait, "unconfirmed") is not None)
        page = oc.get("/extras/due").get_data(as_text=True)
        s.check("the owner sees them under their own heading",
                "On stays not yet confirmed" in page and f"{TAG} C" in page)
        staff = ec.get("/extras/due").get_data(as_text=True)
        s.check("the shift does not", f"{TAG} C" not in staff)

        s.section("The page")
        s.check("the owner sees what each line is worth", "€250" in page or "€250.00" in page)
        s.check("the shift sees no prices at all", "€" not in staff,
                detail=f"{staff.count(chr(0x20ac))} price marks")
        s.check("the guest's telephone is there to ring", "tel:+33611223344" in page)
        s.check("the transfer shows what the guest wrote", "AF7520 lands 14:35" in page)
        with m.app.test_request_context("/"):
            href = m.booking_href(stay["id"], stay["reference_code"])
        s.check("each stay links to its booking", href in page.replace("&amp;", "&"), detail=href)
        filtered = oc.get("/extras/due", query_string={"state": "Arrange now"}).get_data(as_text=True)
        s.check("'Arrange now' shows the job that needs it",
                f"{TAG} B" in filtered and f"{TAG} bottle of wine" not in filtered)
        searched = oc.get("/extras/due", query_string={"q": f"{TAG} A"}).get_data(as_text=True)
        s.check("and the search finds a guest", f"{TAG} bottle of wine" in searched
                and f"{TAG} B" not in searched)
        s.check("every table is wrapped for a phone", page.count("<table") == page.count('class="table-wrap"'))

        s.section("And the shift sees today's on the page it opens")
        hamper = _sell(stay, wine, today, notes="With two glasses")
        today_page = ec.get("/today").get_data(as_text=True)
        s.check("Today lists what is to hand over today",
                "Extras to hand over today" in today_page and "With two glasses" in today_page)
        s.check("with the button to say it was handed over",
                f"/extras/{hamper}/delivered" in today_page)
        s.check("and no prices", "€" not in today_page.split("Extras to hand over today")[1].split("My list")[0])

        s.section("It reaches the owner, and the calendar, and closes itself")
        conn = db()
        with m.app.test_request_context("/"):
            warnings = m.owner_home_warnings(conn, m.house_today())
        found = [w for w in warnings if "to arrange now" in w["title"]]
        # Counted against the board rather than read for a name: the copy can
        # hold other suites' jobs, and the line names only the first two.
        now_count = len(m.extras_due(conn, m.house_today())["arrange_now"])
        s.check("the owner home says what is to arrange now",
                found and found[0]["count"] == now_count and now_count >= 1,
                detail=str(found[:1]))
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
        conn.commit()
        task = conn.execute("SELECT * FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                            (m.WATCH_TASK_ORIGIN,
                             f"Arrange {TAG} charcuterie board for {TAG} B ({TAG}-B)")).fetchone()
        s.check("and it is a task on the day it is to be arranged by",
                task is not None and task["due_date"] == today.isoformat(),
                detail=str(task and task["due_date"]))
        far = conn.execute("SELECT 1 FROM tasks WHERE origin = ? AND status != 'done' AND title = ?",
                           (m.WATCH_TASK_ORIGIN,
                            f"Arrange {TAG} charcuterie board for {TAG} A ({TAG}-A)")).fetchone()
        s.check("the one for the stay in ten days is a task too, dated when it falls due",
                far is not None)
        conn.close()
        ec.post(f"/extras/{soon_board}/arranged", data={"note": "Ordered"})
        conn = db()
        with m.app.test_request_context("/"):
            m.generate_watch_tasks(conn)
            warnings = m.owner_home_warnings(conn, m.house_today())
        conn.commit()
        closed = conn.execute("SELECT status FROM tasks WHERE origin = ? AND title = ?",
                              (m.WATCH_TASK_ORIGIN,
                               f"Arrange {TAG} charcuterie board for {TAG} B ({TAG}-B)")).fetchone()
        conn.close()
        s.check("arranged, the task closes", closed is not None and closed["status"] == "done")
        left = [w for w in warnings if "to arrange now" in w["title"]]
        s.check("and the owner home counts one fewer",
                (left[0]["count"] if left else 0) == now_count - 1,
                detail=str(left[:1]))

        s.section("The catalogue says what each extra is doing")
        conn = db()
        figures = m.extras_catalogue_figures(conn)
        conn.close()
        f = figures.get(transfer["id"], {})
        s.check("how many are on stays still to come",
                f.get("upcoming") == 2, detail=str(f))
        s.check("and when the next is", f.get("next_on") == (today + timedelta(days=5)).isoformat(),
                detail=str(f.get("next_on")))
        cat = oc.get("/admin/extras").get_data(as_text=True)
        s.check("the catalogue page shows it, linked to the run sheet",
                "2 on stays to come" in cat, detail="the line under Coming up")
        s.check("every table is wrapped for a phone", cat.count("<table") == cat.count('class="table-wrap"'))

        s.section("An extra somebody has had is withdrawn, not deleted")
        resp = oc.post(f"/admin/extras/{transfer['id']}/delete", follow_redirects=True)
        conn = db()
        still = conn.execute("SELECT 1 FROM extras WHERE id = ?", (transfer["id"],)).fetchone()
        conn.close()
        s.check("deleting one that has been sold is refused", still is not None)
        s.check("and the page says to withdraw it instead",
                "Withdraw it instead" in resp.get_data(as_text=True))
        spare = _extra_kind("never sold", "other", 10, 0)
        oc.post(f"/admin/extras/{spare['id']}/delete")
        conn = db()
        gone = conn.execute("SELECT 1 FROM extras WHERE id = ?", (spare["id"],)).fetchone()
        conn.close()
        s.check("one nobody has ever had can still be deleted", gone is None)

        s.section("What is already sold on stays still to come")
        conn = db()
        perf = m.extras_performance(conn)
        conn.close()
        ahead = {r["name"]: r for r in perf["ahead"]}
        s.check("the earnings page counts the stays still to come",
                # Two bottles by now: the one sold with the stay and the one for
                # today, both on a stay still to arrive.
                f"{TAG} bottle of wine" in ahead and ahead[f"{TAG} bottle of wine"]["revenue"] == 80
                and ahead[f"{TAG} bottle of wine"]["units"] == 2,
                detail=str(ahead.get(f"{TAG} bottle of wine")))
        s.check("and does not call an extra booked ahead 'never taken'",
                f"{TAG} airport transfer" not in perf["never_sold"])

        s.section("Transfers reads what the guest wrote")
        conn = db()
        asked = m.transfers_asked_for(conn)
        conn.execute("DELETE FROM vehicle_transfers WHERE booking_id = ?", (stay["id"],))
        conn.commit()
        asked = {r["id"]: r for r in m.transfers_asked_for(conn)}
        conn.close()
        mine = asked.get(stay["id"])
        s.check("the note on the transfer is read with the stay",
                mine is not None and "AF7520 lands 14:35" in (mine["transfer_extra_notes"] or ""))
        tpage = oc.get("/transfers").get_data(as_text=True)
        s.check("and the page shows it instead of saying no flight was given",
                "AF7520 lands 14:35" in tpage)
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
