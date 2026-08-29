"""Moving a tab around the floor, and the switches nobody tests.

The till half is the one with money in it. Merging two tabs moves every line
from one order onto another, voids the first, and adds its covers and deposit
credit to the second — four writes that have to agree, on the busiest screen in
the building, done by somebody holding a tablet in one hand.

The case worth the most is merging a tab into ITSELF. The page never offers it,
because the dropdown is built from `other_tables` — but the route takes whatever
id the POST carries, and a list on a page is a convenience, not a boundary.
Without a guard the lines move nowhere, the covers double, and the tab is voided
with its order still sitting on it. A voided tab is never paid, so a table's
whole bill quietly leaves the day's takings and has to be re-rung, which is the
exact thing this route exists to avoid.

The toggles are small and none of them had a check. The one that matters is
do-not-email on a workshop registration: a guest who asked not to be written to
and is written to anyway is the same broken promise as the do-not-write list,
one table over.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTILL"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM pos_order_lines WHERE order_id IN
                    (SELECT id FROM pos_orders WHERE table_label LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_tables WHERE label LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_waitlist WHERE name LIKE ?""", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_feedback WHERE guest_name LIKE ?""", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _order(label, covers=2):
    conn = db()
    conn.close()
    oc = _clients[0]
    oc.post("/pos/open", data={"table_label": TAG + label, "covers": str(covers)},
            follow_redirects=True)
    conn = db()
    row = conn.execute("SELECT * FROM pos_orders WHERE table_label = ?",
                       (TAG + label,)).fetchone()
    conn.close()
    return row


def _line(order_id, name, price, qty=1):
    _clients[0].post(f"/pos/{order_id}/add",
                     data={"name": TAG + " " + name, "unit_price": str(price),
                           "quantity": str(qty)}, follow_redirects=True)


def _order_row(order_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM pos_orders WHERE id = ?", (order_id,)).fetchone()
    finally:
        conn.close()


def _lines_on(order_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM pos_order_lines WHERE order_id = ? AND voided = 0",
            (order_id,)).fetchall()
    finally:
        conn.close()


_clients = []


def run():
    s = Suite("The till floor, and the switches")
    _cleanup()
    oc, ec, owner, emp = clients()
    _clients[:] = [oc, ec]

    s.section("Renaming a tab")
    a = _order("A", covers=4)
    s.check("a tab opens", a is not None and a["status"] == "open")
    oc.post(f"/pos/{a['id']}/move", data={"table_label": TAG + "A-terrace"},
            follow_redirects=True)
    s.check("it can be renamed",
            _order_row(a["id"])["table_label"] == TAG + "A-terrace",
            detail=_order_row(a["id"])["table_label"])
    s.check("and stays open", _order_row(a["id"])["status"] == "open")

    s.section("Merging one tab into another")
    _line(a["id"], "Bottle of Madiran", 42)
    _line(a["id"], "Cheese", 14)
    b = _order("B", covers=2)
    _line(b["id"], "Coffee", 4)
    s.check("each has its own lines",
            len(_lines_on(a["id"])) == 2 and len(_lines_on(b["id"])) == 1,
            detail=f"{len(_lines_on(a['id']))} and {len(_lines_on(b['id']))}")

    oc.post(f"/pos/{a['id']}/move", data={"merge_into": str(b["id"])},
            follow_redirects=True)
    merged, source = _order_row(b["id"]), _order_row(a["id"])
    s.check("every line ends up on the target", len(_lines_on(b["id"])) == 3,
            detail=f"{len(_lines_on(b['id']))} — two moved onto one already there")
    s.check("and none is left behind", len(_lines_on(a["id"])) == 0,
            detail=str(len(_lines_on(a["id"]))))
    s.check("the money moves with them",
            sum(l["unit_price"] * l["quantity"] for l in _lines_on(b["id"])) == 60.0,
            detail=str(sum(l["unit_price"] * l["quantity"] for l in _lines_on(b["id"]))))
    s.check("the covers are added, not replaced", merged["covers"] == 6,
            detail=f"{merged['covers']} — 4 moving onto 2")
    s.check("the emptied tab is voided", source["status"] == "void",
            detail=source["status"])
    s.check("and says where its order went",
            source["merged_into_order_id"] == b["id"],
            detail=str(source["merged_into_order_id"]))
    conn = db()
    journalled = conn.execute(
        "SELECT COUNT(*) c FROM pos_journal WHERE event_type = 'tab_merged' AND order_id = ?",
        (a["id"],)).fetchone()["c"]
    conn.close()
    s.check("and it is in the journal", journalled == 1, detail=str(journalled))

    s.section("A tab cannot be merged into itself")
    # The page never offers this — the dropdown is built from other_tables —
    # but the route takes whatever id the POST carries, and without a guard the
    # lines move nowhere, the covers double, and the tab is voided with its
    # order still on it. A voided tab is never paid.
    solo = _order("SOLO", covers=4)
    _line(solo["id"], "Armagnac", 18)
    r = oc.post(f"/pos/{solo['id']}/move", data={"merge_into": str(solo["id"])},
                follow_redirects=True)
    after = _order_row(solo["id"])
    s.check("it is still open", after["status"] == "open", detail=after["status"])
    s.check("its order is still on it", len(_lines_on(solo["id"])) == 1,
            detail=str(len(_lines_on(solo["id"]))))
    s.check("the covers did not double", after["covers"] == 4, detail=str(after["covers"]))
    s.check("and it says no rather than falling over", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    s.section("Nor into a tab that is not open")
    closed = _order("CLOSED", covers=2)
    conn = db()
    conn.execute("UPDATE pos_orders SET status = 'void' WHERE id = ?", (closed["id"],))
    conn.commit()
    conn.close()
    live = _order("LIVE", covers=2)
    _line(live["id"], "Digestif", 9)
    r = oc.post(f"/pos/{live['id']}/move", data={"merge_into": str(closed["id"])},
                follow_redirects=True)
    s.check("the live tab is untouched",
            _order_row(live["id"])["status"] == "open"
            and len(_lines_on(live["id"])) == 1,
            detail=f"{_order_row(live['id'])['status']} with "
                   f"{len(_lines_on(live['id']))} line(s)")
    s.check("and it says which way round the problem is",
            any("open" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("Where a table is up to")
    state = _order("STATE", covers=2)
    oc.post(f"/pos/{state['id']}/service-state", data={"service_state": "mains"},
            follow_redirects=True)
    s.check("a real state sticks", _order_row(state["id"])["service_state"] == "mains",
            detail=str(_order_row(state["id"])["service_state"]))
    r = oc.post(f"/pos/{state['id']}/service-state", data={"service_state": "on fire"})
    s.check("one the app does not know is refused", r.status_code == 400,
            detail=str(r.status_code))
    s.check("and the real one is unchanged",
            _order_row(state["id"])["service_state"] == "mains",
            detail=str(_order_row(state["id"])["service_state"]))

    s.section("The floor plan")
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_tables (label, area, seats, sort_order, active, created_at)
           VALUES (?, 'salle', 4, 0, 1, ?)""",
        (TAG + "T1", datetime.now(timezone.utc).isoformat()))
    conn.execute(
        """INSERT INTO restaurant_tables (label, area, seats, sort_order, active, created_at)
           VALUES (?, 'terrace', 2, 1, 1, ?)""",
        (TAG + "T2", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    t1 = conn.execute("SELECT * FROM restaurant_tables WHERE label = ?",
                      (TAG + "T1",)).fetchone()
    conn.close()

    oc.post(f"/admin/restaurant/tables/{t1['id']}/save",
            data={"label": TAG + "T1a", "area": "terrace", "seats": "6"},
            follow_redirects=True)
    conn = db()
    saved = conn.execute("SELECT * FROM restaurant_tables WHERE id = ?",
                         (t1["id"],)).fetchone()
    conn.close()
    s.check("a table can be renamed and moved", saved["label"] == TAG + "T1a"
            and saved["area"] == "terrace", detail=f"{saved['label']} / {saved['area']}")
    s.check("and reseated", saved["seats"] == 6, detail=str(saved["seats"]))

    # Two tables called the same thing is how an order reaches the wrong one.
    r = oc.post(f"/admin/restaurant/tables/{t1['id']}/save",
                data={"label": TAG + "T2", "area": "salle", "seats": "4"},
                follow_redirects=True)
    conn = db()
    unchanged = conn.execute("SELECT label FROM restaurant_tables WHERE id = ?",
                             (t1["id"],)).fetchone()["label"]
    conn.close()
    s.check("a name another table already has is refused",
            unchanged == TAG + "T1a", detail=unchanged)
    # Two guards: the route checks for a clash, and `label TEXT NOT NULL UNIQUE`
    # enforces it underneath. Both mean the same thing — no two tables share a
    # name — so removing the route's check leaves the invariant intact and only
    # the message degrades, from a sentence naming the table to an
    # IntegrityError. That is the difference worth holding.
    s.check("and it says which name rather than throwing",
            any(TAG + "T2" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("with the page still working", r.status_code == 200,
            detail=f"HTTP {r.status_code} — the unique index refuses it either "
                   "way; only one of the two can be shown to somebody")

    oc.post(f"/admin/restaurant/tables/{t1['id']}/save",
            data={"label": TAG + "T1a", "area": "the moon", "seats": "0"},
            follow_redirects=True)
    conn = db()
    fallback = conn.execute("SELECT * FROM restaurant_tables WHERE id = ?",
                            (t1["id"],)).fetchone()
    conn.close()
    s.check("an area that does not exist falls back to the real one",
            fallback["area"] == "terrace", detail=fallback["area"])
    s.check("and a table never seats nobody", fallback["seats"] >= 1,
            detail=str(fallback["seats"]))

    s.section("A guest who asked not to be written to")
    conn = db()
    ses = conn.execute(
        """SELECT ws.id AS sid FROM workshop_sessions ws
           JOIN workshops w ON w.id = ws.workshop_id LIMIT 1""").fetchone()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, total_price,
           balance_amount, created_at)
           VALUES (?, ?, ?, 'Quiet Guest', 'quiet@example.invalid', 1, 'confirmed',
           300, 100, ?)""",
        (ses["sid"], TAG + "QUIET", TAG + "qtok", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    wb = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                      (TAG + "QUIET",)).fetchone()
    conn.close()
    s.check("they start reachable", wb["do_not_email"] == 0)

    oc.post(f"/admin/workshops/registrations/{wb['id']}/toggle-do-not-email",
            follow_redirects=True)
    conn = db()
    quiet = conn.execute("SELECT do_not_email FROM workshop_bookings WHERE id = ?",
                         (wb["id"],)).fetchone()["do_not_email"]
    conn.close()
    s.check("the switch marks them do-not-email", quiet == 1, detail=str(quiet))

    # The switch has to mean something. send_workshop_email records a skipped
    # message rather than sending one, and that is the whole point of it.
    conn = db()
    booking = conn.execute(
        """SELECT workshop_bookings.*, workshop_sessions.start_date,
                  workshop_sessions.end_date, workshops.title
           FROM workshop_bookings
           JOIN workshop_sessions ON workshop_sessions.id = workshop_bookings.session_id
           JOIN workshops ON workshops.id = workshop_sessions.workshop_id
           WHERE workshop_bookings.id = ?""", (wb["id"],)).fetchone()
    # workshop_email_context builds an external manage URL, which needs a
    # request context to know the host.
    with m.app.test_request_context():
        m.send_workshop_email(conn, booking, "workshop_balance_reminder",
                              m.workshop_email_context(booking))
    conn.commit()
    sent = conn.execute(
        "SELECT COUNT(*) c FROM email_outbox WHERE to_address = 'quiet@example.invalid'"
    ).fetchone()["c"]
    logged = conn.execute(
        """SELECT status FROM workshop_messages WHERE workshop_booking_id = ?
           ORDER BY id DESC LIMIT 1""", (wb["id"],)).fetchone()
    conn.close()
    s.check("and nothing is sent to them", sent == 0,
            detail=f"{sent} message(s) to somebody who asked not to be written to")
    s.check("while the skip is recorded rather than silent",
            logged and "opted out" in (logged["status"] or ""),
            detail=str(logged["status"]) if logged else "no message row at all")

    oc.post(f"/admin/workshops/registrations/{wb['id']}/toggle-do-not-email",
            follow_redirects=True)
    conn = db()
    back = conn.execute("SELECT do_not_email FROM workshop_bookings WHERE id = ?",
                        (wb["id"],)).fetchone()["do_not_email"]
    conn.close()
    s.check("and it switches back", back == 0, detail=str(back))

    s.section("The workshop waiting list")
    conn = db()
    conn.execute(
        """INSERT INTO workshop_waitlist (session_id, name, email, party_size,
           status, created_at)
           VALUES (?, ?, 'waiting@example.invalid', 2, 'open', ?)""",
        (ses["sid"], TAG + " Hopeful", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    entry = conn.execute("SELECT * FROM workshop_waitlist WHERE name = ?",
                         (TAG + " Hopeful",)).fetchone()
    conn.close()
    oc.post(f"/admin/workshops/waitlist/{entry['id']}/status",
            data={"status": "contacted"}, follow_redirects=True)
    conn = db()
    moved = conn.execute("SELECT status FROM workshop_waitlist WHERE id = ?",
                         (entry["id"],)).fetchone()["status"]
    conn.close()
    s.check("an entry can be moved along", moved == "contacted", detail=moved)
    r = oc.post(f"/admin/workshops/waitlist/{entry['id']}/status",
                data={"status": "invented"})
    conn = db()
    unmoved = conn.execute("SELECT status FROM workshop_waitlist WHERE id = ?",
                           (entry["id"],)).fetchone()["status"]
    conn.close()
    s.check("a status the app does not know is refused", r.status_code == 400,
            detail=str(r.status_code))
    s.check("and the real one is unchanged", unmoved == "contacted", detail=unmoved)

    s.section("Choosing a review to show")
    # Featuring one puts a named guest's words on a public page, so it has to
    # be deliberate in both directions and reversible.
    conn = db()
    conn.execute(
        """INSERT INTO workshop_feedback (workshop_booking_id, guest_name, rating,
           comment, featured, submitted_at)
           VALUES (?, ?, 5, 'The best week of the year.', 0, ?)""",
        (wb["id"], TAG + " Quiet Guest", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    fb = conn.execute("SELECT * FROM workshop_feedback WHERE guest_name = ?",
                      (TAG + " Quiet Guest",)).fetchone()
    conn.close()
    s.check("a review starts unfeatured", fb["featured"] == 0)
    oc.post(f"/admin/workshops/feedback/{fb['id']}/toggle-featured", follow_redirects=True)
    conn = db()
    on = conn.execute("SELECT featured FROM workshop_feedback WHERE id = ?",
                      (fb["id"],)).fetchone()["featured"]
    conn.close()
    s.check("it can be chosen", on == 1, detail=str(on))
    oc.post(f"/admin/workshops/feedback/{fb['id']}/toggle-featured", follow_redirects=True)
    conn = db()
    off = conn.execute("SELECT featured FROM workshop_feedback WHERE id = ?",
                       (fb["id"],)).fetchone()["featured"]
    conn.close()
    s.check("and taken down again", off == 0, detail=str(off))
    s.check("a review that does not exist is a 404",
            oc.post("/admin/workshops/feedback/99999999/toggle-featured").status_code == 404)
    ec.post(f"/admin/workshops/feedback/{fb['id']}/toggle-featured")
    conn = db()
    untouched_fb = conn.execute("SELECT featured FROM workshop_feedback WHERE id = ?",
                                (fb["id"],)).fetchone()["featured"]
    conn.close()
    s.check("an employee cannot publish somebody's words", untouched_fb == 0,
            detail=str(untouched_fb))

    s.section("None of the switches are the employees'")
    guard_tab = _order("GUARD", covers=2)
    ec.post(f"/admin/restaurant/tables/{t1['id']}/save",
            data={"label": TAG + "Rogue", "area": "bar", "seats": "9"})
    ec.post(f"/admin/workshops/registrations/{wb['id']}/toggle-do-not-email")
    conn = db()
    table_now = conn.execute("SELECT label FROM restaurant_tables WHERE id = ?",
                             (t1["id"],)).fetchone()["label"]
    quiet_now = conn.execute("SELECT do_not_email FROM workshop_bookings WHERE id = ?",
                             (wb["id"],)).fetchone()["do_not_email"]
    conn.close()
    s.check("an employee cannot rename a table", table_now == TAG + "T1a",
            detail=table_now)
    s.check("nor change who may be written to", quiet_now == 0, detail=str(quiet_now))
    # ...but the floor itself IS theirs, or the till would need the owner
    # standing at it all service.
    s.check("while a member of staff can move a tab they are working",
            ec.post(f"/pos/{guard_tab['id']}/move",
                    data={"table_label": TAG + "GUARD2"},
                    follow_redirects=True).status_code == 200)
    s.check("and it really moved",
            _order_row(guard_tab["id"])["table_label"] == TAG + "GUARD2",
            detail=_order_row(guard_tab["id"])["table_label"])

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
