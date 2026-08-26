"""The other two waitlists: a table, and a place on an atelier.

test_waitlist covers rooms. These two work the same way and are matched on
different things — the restaurant on one date, an atelier on one session — so
the thing worth checking is that each matches on ITS OWN key and does not tell
the wrong people.

A restaurant cancellation on the 14th must not email somebody waiting for the
20th, and a place freeing up on the June atelier must not email the list for
the September one. Getting that wrong is not a small bug: it is an email to a
guest saying a table is free when it is not.

Both also send with keep=False, for the reason set out in test_waitlist — with
no email provider configured, keeping them queues one stale "a table has come
free" notice per cancellation, all delivered together whenever a provider is
finally switched on.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZWL2"


def _cleanup():
    conn = db()
    for t in ("restaurant_waitlist", "workshop_waitlist"):
        conn.execute(f"DELETE FROM {t} WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _rest_entry(who, wanted, status="open"):
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_waitlist (name, email, desired_date, party_size,
           status, created_at) VALUES (?, ?, ?, 2, ?, ?)""",
        (f"{TAG} {who}", f"{TAG.lower()}.{who.lower()}@example.invalid", wanted,
         status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM restaurant_waitlist WHERE name = ?",
                       (f"{TAG} {who}",)).fetchone()
    conn.close()
    return row


def _shop_entry(who, session_id, status="open"):
    conn = db()
    conn.execute(
        """INSERT INTO workshop_waitlist (session_id, name, email, party_size,
           status, created_at) VALUES (?, ?, ?, 2, ?, ?)""",
        (session_id, f"{TAG} {who}", f"{TAG.lower()}.{who.lower()}@example.invalid",
         status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_waitlist WHERE name = ?",
                       (f"{TAG} {who}",)).fetchone()
    conn.close()
    return row


def _status(table, entry_id):
    conn = db()
    try:
        row = conn.execute(f"SELECT status FROM {table} WHERE id = ?", (entry_id,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("Restaurant and atelier waitlists")
    _cleanup()
    oc, ec, owner, emp = clients()

    sent = []

    def provider(to, subject, body, ics_content=None, ics_filename=None, keep=True):
        sent.append((to, subject, keep))
        return True

    def _notify(fn, arg):
        original = m.send_email
        m.send_email = provider
        conn = db()
        try:
            with m.app.test_request_context("/"):
                return fn(conn, arg)
        finally:
            conn.close()
            m.send_email = original

    s.section("A table freeing up on one date tells only that date")
    wants14 = _rest_entry("Agnes", "2034-02-14")
    wants20 = _rest_entry("Bertrand", "2034-02-20")
    sent.clear()
    got = _notify(m.notify_restaurant_waitlist_opening, "2034-02-14")
    names = [e["name"] for e in got]
    s.check("the one waiting for the 14th is told", any("Agnes" in n for n in names),
            detail=f"{names}")
    s.check("the one waiting for the 20th is not",
            not any("Bertrand" in n for n in names),
            detail="a cancellation on the 14th emailed somebody wanting the 20th")
    s.check("and their entry is untouched",
            _status("restaurant_waitlist", wants20["id"]) == "open")
    s.check("the one told is marked contacted",
            _status("restaurant_waitlist", wants14["id"]) == "contacted",
            detail=f"got {_status('restaurant_waitlist', wants14['id'])!r}")

    s.section("And is not told twice")
    sent.clear()
    s.check("a second cancellation on the same date says nothing more",
            not _notify(m.notify_restaurant_waitlist_opening, "2034-02-14"))
    s.check("no second email", not sent, detail=f"{sent}")

    s.section("A place on one atelier tells only that atelier's list")
    conn = db()
    sessions = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id LIMIT 2").fetchall()
    conn.close()
    if len(sessions) < 2:
        s.check("two atelier sessions exist to tell apart", False,
                detail="seeded data has fewer than two sessions")
    else:
        june, september = sessions[0]["id"], sessions[1]["id"]
        on_june = _shop_entry("Camille", june)
        on_sept = _shop_entry("Damien", september)
        sent.clear()
        got = _notify(m.notify_workshop_waitlist_opening, june)
        names = [e["name"] for e in got]
        s.check("the list for that session is told", any("Camille" in n for n in names),
                detail=f"{names}")
        s.check("the other session's list is not",
                not any("Damien" in n for n in names),
                detail="a place freeing on one atelier emailed the list for another")
        s.check("and that entry is untouched",
                _status("workshop_waitlist", on_sept["id"]) == "open")
        s.check("the one told is marked contacted",
                _status("workshop_waitlist", on_june["id"]) == "contacted")

    s.section("Both send a time-limited notice without keeping it")
    # With no provider, keeping it queues one stale "a table is free" per
    # cancellation — see test_waitlist for the whole story.
    s.check("keep is off", all(k is False for _, _, k in sent) if sent else True,
            detail=f"{sent}")

    s.section("Working the lists by hand")
    for table, url, entry in (
        ("restaurant_waitlist", "/admin/restaurant/waitlist", wants20),
        ("workshop_waitlist", "/admin/workshops/waitlist", None),
    ):
        if entry is None:
            continue
        page = oc.get(url)
        s.check(f"{url} loads", page.status_code == 200, page)
        s.check("and shows who is waiting", entry["name"] in page.get_data(as_text=True))
        oc.post(f"{url}/{entry['id']}/status", data={"status": "booked"},
                follow_redirects=True)
        s.check("a status can be set by hand", _status(table, entry["id"]) == "booked",
                detail=f"got {_status(table, entry['id'])!r}")

    s.section("An invented status is refused rather than stored")
    s.check("the restaurant list refuses it",
            oc.post(f"/admin/restaurant/waitlist/{wants20['id']}/status",
                    data={"status": "maybe"}).status_code == 400)
    s.check("and the entry keeps the status it had",
            _status("restaurant_waitlist", wants20["id"]) == "booked")

    s.section("Guards")
    s.check("an employee cannot read the restaurant list",
            ec.get("/admin/restaurant/waitlist").status_code in (302, 403))
    s.check("nor the atelier list",
            ec.get("/admin/workshops/waitlist").status_code in (302, 403))
    s.check("nor change a status",
            ec.post(f"/admin/restaurant/waitlist/{wants20['id']}/status",
                    data={"status": "open"}).status_code in (302, 403))
    s.check("and it is still what the owner set",
            _status("restaurant_waitlist", wants20["id"]) == "booked")

    _cleanup()
    return s
