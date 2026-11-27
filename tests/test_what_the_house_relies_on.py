"""The figures and buttons the house acts on, said truly.

Found by reading every staff page on a seeded busy day (back-end review,
27 November 2026). Each of these did the wrong thing quietly -- nothing
errored, the page rendered, and the owner or a member of staff acted on it:

  Timesheets      a shift being worked right now was a shift "to fix before
                  payroll", with a button to void it; the owner's own old
                  clockings stood there too.
  Workshops       a registration still waiting on the owner was money "past
                  due", and Send statements would have told them they owed it.
  Approvals       approving in bulk skipped the email, the bell and the audit
                  that one approval does; every decision left the queue; a
                  staff claim did not say whose money it was.
  The till        publishing tonight's card took every wine and coffee off the
                  till, and nothing could stop it; a party that had paid came
                  back "not seated"; a tab opened by mistake could never close;
                  "94635 min"; cash-up refused a closed day without naming why.
  The kitchen     anybody could widen a fridge's band or switch it off, and a
                  breach vanished with it.
  Room checks     anybody could rewrite the list a room is walked against.
  Handover        a note's author was told they had not read it.
  Names           "The Okafor family" was greeted "Hi The," and asked for the
                  surname "family".
  Front desk      ROOM READY without the room's own checklist; "2 free" on a
                  night the house was full.
  A booking       no Check in on arrival day; "Guest checked out" before they came.
  Money           a promo code taken off twice on three pages; Money ahead
                  without guests in the house or overdue atelier balances;
                  Held, not earned reading stamps instead of the ledger.
  Settings        Run now threw away unsaved switches; the changes log printed
                  a yes as "€1.00"; Is mail going out? said On with half of it off.

Every check below was broken on purpose once and seen to fail.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, ensure_room, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZB2"


def _now(delta_h=0):
    return (datetime.now(timezone.utc) + timedelta(hours=delta_h)).isoformat()


def _main(html):
    hit = re.search(r"<main\b.*?</main>", html, re.S)
    return hit.group(0) if hit else html


def _cleanup(made):
    conn = db()
    conn.execute("DELETE FROM tasks WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (f"{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    for table, col in (("tasks", "title"), ("bookings", "guest_name"),
                       ("leave_requests", "reason"), ("expenses", "description"),
                       ("shift_handovers", "body"), ("room_standards", "what"),
                       ("fridge_units", "name"), ("menu_items", "name"),
                       ("restaurant_bookings", "guest_name"), ("workshops", "title")):
        conn.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", (f"%{TAG}%",))
    for table, ids in made.items():
        for i in ids:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (i,))
    conn.commit()
    conn.close()


def run():
    s = Suite("What the house relies on")
    made = {}
    _cleanup(made)
    oc, ec, owner, emp = clients()
    today = house_today()
    iso = today.isoformat()
    room = ensure_room()

    def keep(table, rowid):
        made.setdefault(table, []).append(rowid)
        return rowid

    try:
        # ------------------------------------------------------------------
        s.section("Timesheets: somebody at work now is not a shift to fix")
        conn = db()
        open_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM time_entries WHERE user_id = ? AND clock_out_at IS NULL",
            (emp["id"],)).fetchall()]
        for i in open_ids:          # one open clocking a person; put back below
            conn.execute("UPDATE time_entries SET clock_out_at = clock_in_at, auto_closed = 0 "
                         "WHERE id = ?", (i,))
        made["_reopen"] = open_ids
        at_work = keep("time_entries", conn.execute(
            "INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
            (emp["id"], _now(-2))).lastrowid)
        conn.commit()
        conn.close()
        page = oc.get("/admin/timesheets").get_data(as_text=True)
        with m.app.test_request_context("/"):
            repair = m.url_for("repair_time_entry", entry_id=at_work)
        s.check("a clocking opened two hours ago reads at work now",
                "at work now" in visible_text(_main(page)))
        s.check("and has no button to void it", f'action="{repair}"' not in page)
        conn = db()
        state = m.timesheet_state(conn.execute("SELECT * FROM time_entries WHERE id = ?",
                                               (at_work,)).fetchone(), m.at_work_window(conn))
        old = dict(conn.execute("SELECT * FROM time_entries WHERE id = ?", (at_work,)).fetchone())
        old["clock_in_at"] = _now(-30)
        stale = m.timesheet_state(old, m.at_work_window(conn))
        conn.close()
        s.check("at work now is not one of the states payroll cannot pay",
                state == "At work now" and state not in m.TIMESHEET_TO_FIX, detail=state)
        s.check("one opened thirty hours ago was never clocked out",
                stale == "Never clocked out" and stale in m.TIMESHEET_TO_FIX, detail=stale)
        conn = db()
        owner_old = keep("time_entries", conn.execute(
            "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at, auto_closed) "
            "VALUES (?, ?, ?, 1)", (owner["id"], _now(-24 * 80), _now(-24 * 80 + 9))).lastrowid)
        conn.commit()
        conn.close()
        with m.app.test_request_context("/"):
            owner_repair = m.url_for("repair_time_entry", entry_id=owner_old)
        page = oc.get("/admin/timesheets").get_data(as_text=True)
        s.check("the owner's own old clocking is not a payroll blocker on the page",
                f'action="{owner_repair}"' not in page)

        # ------------------------------------------------------------------
        s.section("Workshops: a request nobody has accepted owes nothing yet")
        conn = db()
        wid = conn.execute(
            "INSERT INTO workshops (title, description, price_per_person, active, created_at) "
            "VALUES (?, '', 1000, 1, ?)", (f"{TAG} Painting week", _now())).lastrowid
        start = today + timedelta(days=40)
        sid = keep("workshop_sessions", conn.execute(
            "INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
            "VALUES (?, ?, ?, 10, ?)", (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(),
                                         _now())).lastrowid)
        regs = {}
        for status, name in (("pending", f"{TAG} Waiting Person"),
                             ("confirmed", f"{TAG} Accepted Person")):
            regs[status] = keep("workshop_bookings", conn.execute(
                """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
                   guest_name, guest_email, party_size, status, total_price, created_at,
                   balance_due_date, occupancy_type)
                   VALUES (?, ?, ?, ?, ?, 1, ?, 1000, ?, ?, 'shared')""",
                (sid, f"{TAG}{status[:3].upper()}", _harness.secrets_token(), name,
                 f"{status}@example.invalid", status, _now(-24 * 20),
                 (today - timedelta(days=2)).isoformat())).lastrowid)
        conn.commit()
        workshops = conn.execute("SELECT * FROM workshops WHERE id = ?", (wid,)).fetchall()
        rows = m.workshop_sitting_rows(conn, workshops, today)
        entry = next(e for e in rows["upcoming"][wid] if e["session"]["id"] == sid)
        s.check("past due counts the accepted place only",
                abs(entry["register"].get("past_due", 0) - 1000) < 0.01,
                detail=str(entry["register"]))
        with m.app.test_request_context("/"):
            found = m.group_members(conn, "session", sid)
            going, skipped = m.group_statement_plan(conn, found[2])
            waiting = next(mm for mm in found[2] if mm["id"] == regs["pending"])
            sent, why = m.send_statement(conn, waiting)
        conn.close()
        s.check("Send statements leaves the waiting registration out, and says why",
                (f"{TAG} Waiting Person, 1", m.GROUP_NOT_ACCEPTED) in skipped
                or any(m.GROUP_NOT_ACCEPTED == w and TAG in lbl for lbl, w in skipped),
                detail=str(skipped))
        s.check("and the one statement to them is refused the same way",
                not sent and why == m.GROUP_NOT_ACCEPTED, detail=str(why))

        # ------------------------------------------------------------------
        s.section("Approvals: the bulk button does what one button does")
        conn = db()
        exp = conn.execute(
            "INSERT INTO expenses (kind, submitted_by_user_id, vendor_name, description, amount, "
            "status, submitted_at) VALUES ('staff_expense', ?, 'Leroy Merlin', ?, 23.9, "
            "'pending', ?)", (emp["id"], f"{TAG} light bulbs", _now(-5))).lastrowid
        leave = conn.execute(
            "INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type, "
            "status, requested_at) VALUES (?, ?, ?, ?, 'vacation', 'pending', ?)",
            (emp["id"], (today + timedelta(days=60)).isoformat(),
             (today + timedelta(days=62)).isoformat(), f"{TAG} a wedding", _now(-5))).lastrowid
        before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM notifications").fetchone()[0]
        audit_before = conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit_log").fetchone()[0]
        conn.commit()
        conn.close()
        page = oc.get("/admin/approvals").get_data(as_text=True)
        text = visible_text(_main(page))
        s.check("a staff claim on the queue names whose money it is",
                f"{emp['name']} · Leroy Merlin" in text, detail=text[text.find("Leroy"):][:80])
        oc.post("/admin/approvals/bulk", data={"items": [f"expense:{exp}", f"leave:{leave}"]})
        conn = db()
        notes = [r["kind"] for r in conn.execute(
            "SELECT kind FROM notifications WHERE id > ? AND user_id = ?", (before, emp["id"]))]
        audits = [r["action"] for r in conn.execute(
            "SELECT action FROM audit_log WHERE id > ?", (audit_before,))]
        states = (conn.execute("SELECT status FROM expenses WHERE id = ?", (exp,)).fetchone()[0],
                  conn.execute("SELECT status FROM leave_requests WHERE id = ?",
                               (leave,)).fetchone()[0])
        conn.close()
        s.check("both approved", states == ("approved", "approved"), detail=str(states))
        s.check("the claimant's bell rings for the expense", "expense_decided" in notes,
                detail=str(notes))
        s.check("and for the time off", "leave_decided" in notes, detail=str(notes))
        s.check("and both are in the audit log",
                "expense_approved" in audits and "leave_approved" in audits, detail=str(audits))
        conn = db()
        exp2 = conn.execute(
            "INSERT INTO expenses (kind, submitted_by_user_id, vendor_name, description, amount, "
            "status, submitted_at) VALUES ('staff_expense', ?, 'Shop', ?, 9, 'pending', ?)",
            (emp["id"], f"{TAG} string", _now(-5))).lastrowid
        conn.commit()
        conn.close()
        r = oc.post(f"/expenses/{exp2}/decide", data={"status": "approved", "next": "approvals"})
        s.check("a decision made on the queue comes back to the queue",
                r.status_code in (302, 303) and r.headers.get("Location", "").endswith("/admin/approvals"),
                detail=r.headers.get("Location"))

        # ------------------------------------------------------------------
        s.section("The till: the bar stays on under tonight's card")
        conn = db()
        oc.post("/admin/restaurant/menu/new", data={
            "name": f"{TAG} Crémant by the glass", "course": "wine", "price": "9",
            "sold_in_pos": "1", "always_available": "1"})
        wine = conn.execute("SELECT * FROM menu_items WHERE name = ?",
                            (f"{TAG} Crémant by the glass",)).fetchone()
        s.check("a wine can be marked to stay on the till every night",
                wine is not None and wine["always_available"] == 1,
                detail=str(dict(wine) if wine else None))
        if wine:
            oc.post(f"/admin/restaurant/menu/{wine['id']}/edit", data={
                "name": wine["name"], "course": "wine", "price": "9", "sold_in_pos": "1"})
            unticked = conn.execute("SELECT always_available FROM menu_items WHERE id = ?",
                                    (wine["id"],)).fetchone()[0]
            s.check("and unticked again on the dish's form", unticked == 0, detail=str(unticked))
            conn.execute("UPDATE menu_items SET always_available = 1 WHERE id = ?", (wine["id"],))
        menu_id = keep("menus", conn.execute(
            "INSERT INTO menus (service_date, service, status, created_at) "
            "VALUES (?, 'dinner', 'published', ?)", (iso, _now())).lastrowid)
        conn.commit()
        till = m.pos_menu(conn, service_date=iso)
        names = [i["name"] for dishes in till.values() for i in dishes] if isinstance(till, dict) \
            else [i["name"] for _c, dishes in till for i in dishes]
        conn.execute("DELETE FROM app_settings WHERE key = 'standing_drinks_2026_11_27'")
        conn.execute("UPDATE menu_items SET always_available = 0 WHERE name = ?",
                     (f"{TAG} Crémant by the glass",))
        conn.commit()
        changed = m.apply_standing_drinks_2026_11_27(conn)
        after = conn.execute("SELECT always_available FROM menu_items WHERE name = ?",
                             (f"{TAG} Crémant by the glass",)).fetchone()
        conn.close()
        s.check("with a card published, the standing wine is still on the till",
                f"{TAG} Crémant by the glass" in names, detail=str(names)[:200])
        s.check("the once-off fix ticks the drink courses", changed >= 1 and after[0] == 1,
                detail=f"{changed} changed")
        conn = db()
        conn.execute("UPDATE menu_items SET always_available = 0")
        conn.commit()
        conn.close()
        day = visible_text(_main(oc.get(f"/admin/restaurant/menu/day?date={iso}").get_data(as_text=True)))
        s.check("tonight's card warns, before Publish, when nothing would stay",
                "Nothing on the standing list stays on the till under a card" in day)
        conn = db()
        conn.execute("UPDATE menu_items SET always_available = 1 WHERE course IN "
                     "('aperitif','wine','drink','coffee')")
        conn.commit()
        conn.close()

        s.section("The till: seated, closed, and how long")
        conn = db()
        rb = conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
               guest_email, party_size, dinner_date, status, created_at)
               VALUES (?, ?, ?, 'zzb2@example.invalid', 2, ?, 'confirmed', ?)""",
            (f"{TAG}R", _harness.secrets_token(), f"{TAG} Paid And Gone", iso, _now())).lastrowid
        paid_tab = keep("pos_orders", conn.execute(
            "INSERT INTO pos_orders (table_label, covers, restaurant_booking_id, status, "
            "opened_at, closed_at, service_date) VALUES ('ZZ1', 2, ?, 'paid', ?, ?, ?)",
            (rb, _now(-3), _now(-1), iso)).lastrowid)
        empty_tab = keep("pos_orders", conn.execute(
            "INSERT INTO pos_orders (table_label, covers, status, opened_at, service_date) "
            "VALUES ('ZZ2', 2, 'open', ?, ?)", (_now(-1), iso)).lastrowid)
        conn.commit()
        conn.close()
        floor = visible_text(_main(oc.get("/pos").get_data(as_text=True)))
        s.check("a party that has paid is not booked in tonight, not seated",
                f"{TAG} Paid And Gone" not in floor, detail=floor[:200])
        day_page = oc.get(f"/pos/day?date={iso}").get_data(as_text=True)
        s.check("cash up names the tab still open, before anybody counts the drawer",
                "Still open:" in day_page and "ZZ2" in visible_text(_main(day_page)))
        order_page = oc.get(f"/pos/{empty_tab}").get_data(as_text=True)
        with m.app.test_request_context("/"):
            close_url = m.url_for("pos_close_empty", order_id=empty_tab)
        s.check("an empty tab offers a way to close it", f'action="{close_url}"' in order_page)
        oc.post(close_url)
        conn = db()
        st = conn.execute("SELECT status FROM pos_orders WHERE id = ?", (empty_tab,)).fetchone()[0]
        journal = conn.execute("SELECT event_type FROM pos_journal WHERE order_id = ? "
                               "ORDER BY sequence DESC LIMIT 1", (empty_tab,)).fetchone()
        conn.close()
        s.check("closing it voids it, and the journal says so",
                st == "void" and journal and journal[0] == "tab_closed_empty",
                detail=f"{st}, {journal and journal[0]}")
        s.check("a tab left open for weeks reads in days, not 94635 min",
                m.minutes_words(94635) == "65 days" and m.minutes_words(130) == "2h 10m"
                and m.minutes_words(45) == "45 min",
                detail=str((m.minutes_words(94635), m.minutes_words(130))))

        # ------------------------------------------------------------------
        s.section("The kitchen: a breach cannot be made to vanish by whoever reads it")
        conn = db()
        unit = keep("fridge_units", conn.execute(
            "INSERT INTO fridge_units (name, where_it_is, min_c, max_c, active, created_at) "
            "VALUES (?, 'Pastry', 0, 5, 1, ?)", (f"{TAG} Dessert fridge", _now())).lastrowid)
        keep("fridge_readings", conn.execute(
            "INSERT INTO fridge_readings (unit_id, read_at, celsius, read_by_user_id, created_at) "
            "VALUES (?, ?, 7.8, ?, ?)", (unit, _now(-2), emp["id"], _now(-2))).lastrowid)
        conn.commit()
        conn.close()
        ec.post(f"/kitchen/fridges/unit/{unit}", data={"what": "range", "min_c": "0", "max_c": "8"})
        ec.post(f"/kitchen/fridges/unit/{unit}", data={"what": "off"})
        conn = db()
        u = conn.execute("SELECT * FROM fridge_units WHERE id = ?", (unit,)).fetchone()
        conn.close()
        s.check("an employee cannot widen the band", u["max_c"] == 5, detail=str(u["max_c"]))
        s.check("nor switch the fridge off", u["active"] == 1)
        oc.post(f"/kitchen/fridges/unit/{unit}", data={"what": "off"})
        conn = db()
        u = conn.execute("SELECT active FROM fridge_units WHERE id = ?", (unit,)).fetchone()
        conn.close()
        s.check("not even the owner, while a breach is unanswered", u["active"] == 1)
        s.check("and the controls are not drawn for an employee",
                "Change its range, or switch it off" not in ec.get("/kitchen/fridges").get_data(as_text=True))

        s.section("Room checks: the list is the owner's")
        ec.post("/admin/room-checks", data={"what": "standard", "line": f"{TAG} a line"})
        conn = db()
        added = conn.execute("SELECT COUNT(*) FROM room_standards WHERE what LIKE ?",
                             (f"%{TAG}%",)).fetchone()[0]
        conn.close()
        s.check("an employee cannot add to it", added == 0, detail=str(added))
        s.check("nor is offered Take off",
                "Take off" not in ec.get("/admin/room-checks").get_data(as_text=True))
        oc.post("/admin/room-checks", data={"what": "standard", "line": f"{TAG} owner's line"})
        conn = db()
        added = conn.execute("SELECT COUNT(*) FROM room_standards WHERE what LIKE ?",
                             (f"%{TAG}%",)).fetchone()[0]
        conn.close()
        s.check("the owner can", added == 1, detail=str(added))

        s.section("Handover: whoever wrote it has read it")
        conn = db()
        conn.execute("INSERT INTO shift_handovers (written_by_user_id, for_date, body, created_at) "
                     "VALUES (?, ?, ?, ?)", (emp["id"], iso, f"{TAG} boiler noise", _now(-1)))
        conn.commit()
        note_id = conn.execute("SELECT id FROM shift_handovers WHERE body LIKE ?",
                               (f"%{TAG}%",)).fetchone()[0]
        conn.close()
        # The author taps it anyway, as the old page asked them to.
        with m.app.test_request_context("/"):
            mark = m.url_for("mark_handover_read", note_id=note_id)
        ec.post(mark)
        conn = db()
        notes = m.handover_notes(conn, today=today, user_id=emp["id"])
        mine = next(n for n in notes if TAG in (n["note"]["body"] or ""))
        conn.close()
        s.check("the author's own note counts as read by them", mine["read_by_me"])
        s.check("and they are not counted among its readers", mine["note"]["read_count"] == 0)

        # ------------------------------------------------------------------
        s.section("Names: a household is greeted as one")
        s.check("The Okafor family is greeted Okafor family",
                m.greeting_name("The Okafor family") == "Okafor family",
                detail=m.greeting_name("The Okafor family"))
        s.check("and found by the surname Okafor", m.surname_of("The Okafor family") == "Okafor")
        s.check("a person is still greeted by their first name",
                m.greeting_name("Isabelle Dufresne") == "Isabelle")
        conn = db()
        bid = conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status, total_price,
               created_at, source, amount_paid)
               VALUES (?, ?, ?, ?, 'zzb2.okafor@example.invalid', ?, ?, 4, 'confirmed', 900, ?,
                       'direct', 0)""",
            # Exactly the name a guest types: a tag in front would make
            # "Dear ZZB2," and the check below could never fail.
            (room["id"], f"{TAG}OK", _harness.secrets_token(), "The Okafor family",
             iso, (today + timedelta(days=3)).isoformat(), _now(-24 * 9))).lastrowid
        conn.commit()
        conn.close()
        s.check("the fiche's surname is not 'family'",
                m.fiche_prefill({"guest_name": "The Okafor family"})["surname"] == "Okafor")
        page = oc.get(f"/admin/bookings/{bid}").get_data(as_text=True)
        s.check("the booking page writes Dear Okafor family, not Dear The",
                "Dear Okafor family," in page and "Dear The," not in page
                and "Write to The<" not in page)

        s.section("A booking on its arrival day")
        with m.app.test_request_context("/"):
            check_in = m.url_for("check_in_booking", booking_id=bid)
            check_out = m.url_for("checkout_booking", booking_id=bid)
        s.check("its own page offers Check in", f'action="{check_in}"' in page)
        s.check("and not Guest checked out before they are in", f'action="{check_out}"' not in page)
        conn = db()
        conn.execute("UPDATE bookings SET checked_in_at = ? WHERE id = ?", (_now(-1), bid))
        conn.commit()
        conn.close()
        page = oc.get(f"/admin/bookings/{bid}").get_data(as_text=True)
        s.check("once in, checking out is offered, asking about what is owed",
                f'action="{check_out}"' in page and "is still owed on this stay" in page)

        s.section("The front desk")
        conn = db()
        for item in ("Fresh towels", "Wine bottle"):
            conn.execute("INSERT INTO tasks (title, room_note, priority, due_date, status, "
                         "created_at, origin, booking_id) VALUES (?, ?, 'high', ?, 'open', ?, "
                         "'checklist', ?)", (f"{room['name']}: {item}", f"{TAG} prep", iso, _now(),
                                             bid))
        conn.commit()
        with m.app.test_request_context("/"):
            desk = m.front_desk_day(conn, today)
        conn.close()
        row = next(x for x in desk["arriving"] if x.get("id") == bid)
        s.check("the room's checklist counts towards ready",
                row["prep"] is not None and row["prep"]["open"] >= 2, detail=str(row["prep"]))
        s.check("free rooms are never more than the house can still sell",
                desk["free_tonight"] <= max(0, m.MAX_ROOMS_LET_PER_NIGHT - desk["let_tonight"]),
                detail=f"{desk['free_tonight']} free, {desk['let_tonight']} let")
        if desk["let_tonight"] >= m.MAX_ROOMS_LET_PER_NIGHT:
            s.check("with the house full, an empty room is not for sale",
                    all(r["state"] != "Free tonight" for r in desk["rooms"]))

        # ------------------------------------------------------------------
        s.section("Money: each figure once, and the ones that were missing")
        conn = db()
        coded = conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status, total_price,
               discount_amount, created_at, source)
               VALUES (?, ?, ?, ?, 'zzb2.code@example.invalid', ?, ?, 2, 'confirmed', 900, 100,
                       ?, 'direct')""",
            (room["id"], f"{TAG}PC", _harness.secrets_token(), f"{TAG} Coded Guest",
             (today + timedelta(days=200)).isoformat(), (today + timedelta(days=203)).isoformat(),
             _now())).lastrowid
        conn.commit()
        with_code = m.revenue_on_the_books(conn, today)["rooms"]
        conn.execute("UPDATE bookings SET discount_amount = 0 WHERE id = ?", (coded,))
        conn.commit()
        no_code = m.revenue_on_the_books(conn, today)["rooms"]
        conn.close()
        s.check("a promo code changes nothing on the books: total_price is already net of it",
                abs(with_code - no_code) < 0.01, detail=f"{with_code} with the code, {no_code} without")
        conn = db()
        inhouse = conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status, total_price,
               created_at, source, room_total_quoted, room_total_quoted_for)
               VALUES (?, ?, ?, ?, 'zzb2.in@example.invalid', ?, ?, 2, 'confirmed', 680, ?,
                       'direct', 680, ?)""",
            (room["id"], f"{TAG}IN", _harness.secrets_token(), f"{TAG} In The House",
             (today - timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat(),
             _now(-24 * 30), f"{(today - timedelta(days=1)).isoformat()}|{(today + timedelta(days=2)).isoformat()}|{room['id']}")).lastrowid
        conn.commit()
        with m.app.test_request_context("/"):
            ahead = m.money_ahead(conn, days=90, today=today)
        conn.close()
        labels = " ".join(x.get("label", "") for x in ahead.get("incoming", []))
        s.check("money ahead counts a guest in the house who still owes",
                f"{TAG} In The House" in labels, detail=labels[:300])
        s.check("and the accepted atelier place whose balance is overdue",
                f"{TAG} Accepted Person" in labels or "Painting week" in labels,
                detail=labels[:300])
        conn = db()
        conn.execute("UPDATE workshop_bookings SET deposit_paid_at = ?, balance_paid_at = ? "
                     "WHERE id = ?", (_now(), _now(), regs["confirmed"]))
        conn.execute("UPDATE workshop_bookings SET deposit_amount = 300, balance_amount = 700 "
                     "WHERE id = ?", (regs["confirmed"],))
        conn.commit()
        held = m.money_held_not_earned(conn, today=today)
        conn.close()
        s.check("held, not earned reads the ledger, not the paid stamps",
                not any(TAG in (r.get("label") or "") for r in held["workshops"]),
                detail=str([r.get("label") for r in held["workshops"]])[:200])

        # ------------------------------------------------------------------
        s.section("Settings pages that said the wrong thing")
        auto = oc.get("/admin/automation").get_data(as_text=True)
        s.check("Run now warns before it throws away unsaved switches",
                "auto-run-now" in auto and "Run now does not ' +" in auto
                or "Run now does not" in auto)
        s.check("each switch that is not a timer gives its own reason",
                "when an atelier is over" in visible_text(auto)
                and "when a guest's email arrives" in visible_text(auto))
        s.check("a yes in the changes log is Yes, not €1.00",
                m.change_log_value("in_booking_total", 1, {}) == "Yes",
                detail=str(m.change_log_value("in_booking_total", 1, {})))
        mail = visible_text(_main(oc.get("/admin/emails").get_data(as_text=True)))
        if "Writing to guests is on" in mail:
            s.check("is mail going out says what Guest emails lets through",
                    "Guest emails are" in mail, detail=mail[:300])
        css = open(m.os.path.join(_harness.ROOT, "static", "style.css"), encoding="utf-8").read()
        rule = re.search(r"\.cal-today\{([^}]*)\}", css)
        s.check("today on the team calendar is outlined, not filled over the shift colour",
                rule is not None and "background" not in rule.group(1), detail=rule and rule.group(1))
    finally:
        reopen = made.pop("_reopen", [])
        _cleanup(made)
        conn = db()
        for i in reopen:
            conn.execute("UPDATE time_entries SET clock_out_at = NULL WHERE id = ?", (i,))
        conn.commit()
        conn.close()
    return s
