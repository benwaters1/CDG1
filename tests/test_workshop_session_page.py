"""One sitting of a workshop, managed from one page.

The register showed every registration as a card -- right for one person,
twenty-two people long for a session -- and nothing said how a sitting stood:
places, money in and owed, who was still without a room, what the kitchen had
to know (owner, 17-18 October 2026: the first-month pages were "too basic on a
management level"). And every action on a registration sent the owner back to
the whole register, so confirming somebody lost the session they were on.

The page's money and places come from the register's own helpers, and that is
checked here too: two pages that disagree about who owes what are worse than
one page.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZSESS"


def _cleanup():
    conn = db()
    for r in conn.execute("SELECT wb.id FROM workshop_bookings wb JOIN workshop_sessions ws "
                          "ON ws.id = wb.session_id JOIN workshops w ON w.id = ws.workshop_id "
                          "WHERE w.title LIKE ?", (TAG + "%",)).fetchall():
        conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id = ?", (r["id"],))
        conn.execute("DELETE FROM workshop_bookings WHERE id = ?", (r["id"],))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN "
                 "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _setup(today):
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                        active, created_at) VALUES (?, 'x', 1000, 6, 1, ?)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = today + timedelta(days=200)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at)
                    VALUES (?, ?, ?, 6, ?)""", (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE workshop_id = ?", (wid,)).fetchone()["id"]
    regs = {}
    for key, status, diet, dep_paid, paid in (
            ("paid", "confirmed", "Vegetarian", True, 1000),
            ("owing", "confirmed", None, True, 300),
            ("nodeposit", "confirmed", None, False, 0),
            ("waiting", "pending", "No nuts", False, 0),
            ("gone", "cancelled", None, False, 0)):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
                   guest_email, party_size, status, total_price, created_at, occupancy_type,
                   dietary_notes, deposit_amount, deposit_paid_at, balance_due_date)
               VALUES (?, ?, ?, ?, ?, 1, ?, 1000, ?, 'double', ?, 300, ?, ?)""",
            (sid, f"WRK-{TAG}{key.upper()}", f"tok{TAG}{key}", f"{TAG} {key}",
             f"zz.sess.{key}@example.invalid", status, now, diet, now if dep_paid else None,
             (today + timedelta(days=170)).isoformat()))
        rid = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?",
                           (f"tok{TAG}{key}",)).fetchone()["id"]
        if paid:
            conn.execute("""INSERT INTO workshop_transactions (workshop_booking_id, kind, description,
                                amount, created_at) VALUES (?, 'payment', 'Paid', ?, ?)""", (rid, paid, now))
        regs[key] = rid
    conn.commit()
    conn.close()
    return sid, regs


def run():
    s = Suite("One sitting of a workshop, on one page")
    _cleanup()
    oc, ec, _o, _e = clients()
    today = m.house_today()
    try:
        sid, regs = _setup(today)
        url = f"/admin/workshops/sessions/{sid}"

        s.section("It opens, for the owner")
        r = oc.get(url)
        page = r.get_data(as_text=True)
        s.check("the page opens", r.status_code == 200, detail=str(r.status_code))
        s.check("and not for somebody without the owner's access",
                ec.get(url).status_code in (302, 303, 403))

        s.section("The places and the money agree with the register")
        conn = db()
        register = [x for x in m.workshop_register_rows(conn, today) if x["session_id"] == sid]
        free = m.workshop_session_remaining_capacity(conn, sid)
        conn.close()
        owed = round(sum(max(x["owed"], 0) for x in register if x["live"]), 2)
        s.check("three confirmed, one waiting, two free of six",
                "3 of 6" in page and "1 waiting on you" in page and f"{free} free" in page and free == 2,
                detail=f"free={free}")
        with m.app.test_request_context("/"):
            owed_words = m.money(owed, 0)
        s.check("still owed is the register's figure", owed_words in page and owed == 2700,
                detail=f"{owed} / {owed_words}")

        s.section("Everybody coming, one row each")
        s.check("every live registration has a row",
                all(f"{TAG} {k}" in page for k in ("paid", "owing", "nodeposit", "waiting")))
        roster = page.split("Who is coming")[1].split("Cancelled or declined")[0]
        s.check("the cancelled one is not among them", f"{TAG} gone" not in roster)
        s.check("the two with no deposit are each marked so", roster.count("deposit not paid") == 2,
                detail=str(roster.count("deposit not paid")))
        s.check("but is listed below, with nothing held", "Cancelled or declined — 1" in page)
        s.check("the one waiting comes first, to be decided",
                roster.index(f"{TAG} waiting") < roster.index(f"{TAG} paid"))
        s.check("the kitchen sees what it needs", "For the kitchen" in page and "No nuts" in page
                and "Vegetarian" in page)
        s.check("every table is wrapped for a phone", page.count("<table") == page.count('class="table-wrap"'))

        s.section("Actions come back to the session")
        r = oc.post(f"/admin/workshops/registrations/{regs['waiting']}/confirm",
                    data={"back_session": str(sid)})
        s.check("confirming from the session returns to the session",
                r.status_code in (302, 303) and r.headers.get("Location", "").endswith(url),
                detail=r.headers.get("Location"))
        conn = db()
        status = conn.execute("SELECT status FROM workshop_bookings WHERE id = ?",
                              (regs["waiting"],)).fetchone()["status"]
        room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY max_occupancy DESC LIMIT 1").fetchone()
        conn.close()
        s.check("and it is confirmed", status == "confirmed")
        r = oc.post(f"/admin/workshops/registrations/{regs['owing']}/assign-room",
                    data={"room_id": str(room["id"]), "back_session": str(sid)})
        s.check("giving somebody a room returns to the session",
                r.headers.get("Location", "").endswith(url), detail=r.headers.get("Location"))
        r = oc.post(f"/admin/workshops/registrations/{regs['nodeposit']}/mark-deposit-paid",
                    data={"method": "bank_transfer", "back_session": str(sid)})
        s.check("marking a deposit paid returns to the session",
                r.headers.get("Location", "").endswith(url), detail=r.headers.get("Location"))
        conn = db()
        got = conn.execute("SELECT COALESCE(SUM(amount), 0) AS a FROM workshop_transactions "
                           "WHERE workshop_booking_id = ? AND kind = 'payment'",
                           (regs["nodeposit"],)).fetchone()["a"]
        conn.close()
        s.check("and the deposit is on their ledger", round(got, 2) == 300, detail=str(got))
        r = oc.post(f"/admin/workshops/registrations/{regs['owing']}/assign-room", data={"room_id": ""})
        s.check("from the register, the register is where it still goes",
                "/admin/workshops/registrations" in r.headers.get("Location", ""),
                detail=r.headers.get("Location"))

        s.section("It can be found")
        s.check("the workshops page links each sitting to it",
                url in oc.get("/admin/workshops").get_data(as_text=True))
        s.check("and so does the register, filtered to the session",
                url in oc.get("/admin/workshops/registrations",
                              query_string={"session_id": sid}).get_data(as_text=True))
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
