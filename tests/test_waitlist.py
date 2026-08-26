"""The waitlist, which is how a cancellation turns back into money.

A cancelled room is only lost revenue if nobody is told. Three waitlists exist —
rooms, the restaurant, the ateliers — and all three notify on cancellation, and
none of it was tested.

The mechanism is careful in the ways that matter: it emails only entries whose
dates actually overlap what freed up, marks them contacted so an unrelated
cancellation later does not chase the same person again, and only marks them
once the email has really gone.

That last detail is where the interesting bug was. With no email provider
configured — the state this site is in — send_email queues the message and
returns False. So the entry correctly stayed open, and the message correctly
went nowhere, and one copy of it was left in the outbox every single time.
Three cancellations, three identical "a room may have opened up" notices,
waiting to be delivered together the day a provider is switched on, for dates
that were probably resold weeks earlier.

These now use keep=False, the same rule a password-reset link uses: a message
that is only true while the dates are free should not be kept. Nothing is
queued, the entry stays open, and the owner is told to work the list by hand.
"""
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZWL"


def _cleanup():
    conn = db()
    for t in ("waitlist_entries", "restaurant_waitlist", "workshop_waitlist"):
        conn.execute(f"DELETE FROM {t} WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _room_entry(who, arrival, departure, status="open"):
    conn = db()
    conn.execute(
        """INSERT INTO waitlist_entries (name, email, desired_arrival, desired_departure,
           party_size, status, created_at) VALUES (?, ?, ?, ?, 2, ?, ?)""",
        (f"{TAG} {who}", f"{TAG.lower()}.{who.lower()}@example.invalid",
         arrival, departure, status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM waitlist_entries WHERE name = ?",
                       (f"{TAG} {who}",)).fetchone()
    conn.close()
    return row


def _status(entry_id, table="waitlist_entries"):
    conn = db()
    try:
        row = conn.execute(f"SELECT status FROM {table} WHERE id = ?", (entry_id,)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def _outbox_count():
    conn = db()
    try:
        return conn.execute("SELECT COUNT(*) AS c FROM email_outbox WHERE to_address LIKE ?",
                            (TAG.lower() + "%",)).fetchone()["c"]
    finally:
        conn.close()


def _notify(arrival, departure, sender=None):
    """Run the real notifier, optionally with a working email provider."""
    original = m.send_email
    if sender is not None:
        m.send_email = sender
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.notify_room_waitlist_opening(conn, arrival, departure)
    finally:
        conn.close()
        m.send_email = original


def run():
    s = Suite("Waitlist")
    _cleanup()
    oc, ec, owner, emp = clients()

    sent = []

    def working_provider(to, subject, body, ics_content=None, ics_filename=None, keep=True):
        sent.append((to, subject, keep))
        return True

    s.section("Only the people whose dates actually freed up")
    wants = _room_entry("Amelie", "2033-05-10", "2033-05-14")
    other = _room_entry("Bruno", "2033-09-01", "2033-09-05")
    got = _notify("2033-05-11", "2033-05-13", working_provider)
    names = [e["name"] for e in got]
    s.check("the overlapping entry is told", any("Amelie" in n for n in names),
            detail=f"{names}")
    s.check("somebody wanting a different month is not",
            not any("Bruno" in n for n in names),
            detail="a cancellation in May emailed a September waitlist")
    s.check("and their entry is untouched", _status(other["id"]) == "open")

    s.section("Being told once is being told once")
    s.check("they are marked contacted", _status(wants["id"]) == "contacted",
            detail=f"got {_status(wants['id'])!r}")
    sent.clear()
    again = _notify("2033-05-11", "2033-05-13", working_provider)
    s.check("a second cancellation does not chase them again", not again,
            detail=f"{[e['name'] for e in again]}")
    s.check("and no second email goes out", not sent, detail=f"{sent}")

    s.section("With no email provider, nothing is queued for later")
    # The bug. send_email queues and returns False when nothing is configured,
    # so this used to leave one copy per cancellation in the outbox — all
    # delivered together the day a provider is switched on, for dates long gone.
    fresh = _room_entry("Camille", "2033-06-10", "2033-06-14")
    before = _outbox_count()
    for _ in range(3):
        _notify("2033-06-11", "2033-06-13")          # the real send_email
    s.check("three cancellations queue nothing", _outbox_count() == before,
            detail=f"{before} -> {_outbox_count()}; stale 'a room came free' "
                   "notices are waiting to be delivered")
    s.check("and the entry stays open, because nobody was actually told",
            _status(fresh["id"]) == "open", detail=f"got {_status(fresh['id'])!r}")

    s.section("So the owner is told to work the list by hand")
    conn = db()
    room = _harness.ensure_room()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, city_tax, created_at)
           VALUES (?, ?, ?, ?, 'c@example.invalid', '2033-06-10', '2033-06-14', 2,
                   'confirmed', 900, 0, ?)""",
        (room["id"], f"{TAG}-BK", f"tok{TAG}bk", f"{TAG} Leaver",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    booking = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-BK",)).fetchone()["id"]
    conn.close()
    r = oc.post(f"/admin/bookings/{booking}/cancel", follow_redirects=True)
    said = " ".join(flashes(r)).lower()
    s.check("cancelling says something about the waitlist",
            "waitlist" in said or "waiting" in said,
            detail=f"{flashes(r)[:2]} — a cancellation with somebody waiting "
                   "said nothing about them")

    s.section("Turning the automation off means nobody is emailed")
    conn = db()
    conn.execute("""INSERT INTO app_settings (key, value)
                    VALUES ('automation_waitlist_autonotify_enabled', '0')
                    ON CONFLICT(key) DO UPDATE SET value = '0'""")
    conn.commit()
    conn.close()
    sent.clear()
    quiet = _room_entry("Delphine", "2033-07-10", "2033-07-14")
    off = _notify("2033-07-11", "2033-07-13", working_provider)
    s.check("nobody is notified", not off)
    s.check("no email is sent", not sent, detail=f"{sent}")
    s.check("and the entry is left alone for somebody to ring",
            _status(quiet["id"]) == "open")
    conn = db()
    conn.execute("UPDATE app_settings SET value = '1' "
                 "WHERE key = 'automation_waitlist_autonotify_enabled'")
    conn.commit()
    conn.close()

    s.section("A time-limited message is not kept when it cannot be sent")
    sent.clear()
    _room_entry("Elise", "2033-08-10", "2033-08-14")
    _notify("2033-08-11", "2033-08-13", working_provider)
    s.check("the notice is sent with keep=False",
            any(k is False for _, _, k in sent),
            detail=f"{sent} — a 'room came free' email held for weeks is worse "
                   "than none")

    s.section("The owner can work the list on the page")
    page = oc.get("/admin/waitlist")
    s.check("the waitlist page loads", page.status_code == 200, page)
    body = page.get_data(as_text=True)
    s.check("and shows who is waiting", f"{TAG} Bruno" in body, detail="entries missing")
    oc.post(f"/admin/waitlist/{other['id']}/status", data={"status": "contacted"},
            follow_redirects=True)
    s.check("a status can be set by hand", _status(other["id"]) == "contacted",
            detail=f"got {_status(other['id'])!r}")

    s.section("Guards")
    s.check("an employee cannot read the waitlist",
            ec.get("/admin/waitlist").status_code in (302, 403))
    s.check("nor change somebody's status",
            ec.post(f"/admin/waitlist/{other['id']}/status",
                    data={"status": "open"}).status_code in (302, 403))

    _cleanup()
    return s
