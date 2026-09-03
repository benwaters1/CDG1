"""Saying no to a guest, and everything that follows from saying it.

Three routes the suite reached and only ever got a 403 or a 404 out of. Both
are worth proving -- an employee must not be able to decline a booking, and a
booking that does not exist must not pretend to. Neither is the decline.

So the branch that never ran was the one where the château actually turns
somebody away, which is the branch with a refund, an email and a waiting list
behind it. app.py says as much in decline_one_and_follow_up: bulk decline was
once a loop over the core helper, so ten room-nights came free and nobody
waiting for them was told, and a refund that failed at Stripe was reported to
nobody. That helper exists to stop it happening again, and nothing exercised
the path through it.

WHAT IS HELD HERE:

  THE WAITING LIST IS WORKED. Somebody wanting exactly those dates is told,
  and the message back says how many. A decline that frees a room and tells
  nobody is the whole reason the helper was written.

  AND WHEN NOBODY COULD BE TOLD, IT SAYS SO. The other half of that message
  names how many are still waiting, so an owner who declines at midnight is
  not left thinking the list was empty.

  A FAILED REFUND IS REPORTED. Declining a paid booking with no card
  processor configured cannot move money, and the flash has to say that out
  loud. Silence here is money nobody chases -- which is the failure named in
  that same docstring.

  AND IT ONLY HAPPENS ONCE. The second decline of the same booking is a 404
  and refunds nothing, because the state change is guarded on still-pending.

No Stripe call is made by any of this: issue_refund returns "Stripe isn't
configured" before it reaches the network, which is precisely the branch
being tested.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZDEC"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?",
                 (TAG + "%",))
    for t in ("waitlist_entries", "restaurant_waitlist", "workshop_waitlist"):
        conn.execute(f"DELETE FROM {t} WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


class _MailReaches:
    """A provider that accepts the message, and still sends nothing.

    notify_room_waitlist_opening marks an entry contacted only if the email
    or the text actually reached them. Here neither transport exists -- the
    harness pins both off -- so `notified` comes back empty every time and
    the "Notified N waitlist guests automatically" branch is unreachable in
    a test. That branch is the point of the whole helper.

    So send_email is replaced with one that reports success and sends
    nothing. Strictly less capable than the pinned stub it replaces: it
    cannot reach anybody, it only says the provider would have. Restored
    after, and the harness's own assertion is re-checked at the end.
    """

    def __enter__(self):
        self.real = m.send_email
        self.sent = []

        def _accept(to, subject, body, **kw):
            self.sent.append((to, subject))
            return True

        m.send_email = _accept
        return self

    def __exit__(self, *_exc):
        m.send_email = self.real
        return False


AUTONOTIFY = "automation_waitlist_autonotify_enabled"


def _switch(conn, value):
    """Set the waitlist auto-notify switch, returning what it was.

    Stated rather than assumed. This suite is about what happens when a room
    comes free, and notify_room_waitlist_opening returns an empty list the
    moment that switch is off -- so a suite earlier in the run leaving it off
    turns every check below into a test of the quiet path, passing on the
    wrong branch. That is not hypothetical: test_automation_switches restored
    only the switches with a scheduled job behind them and left this one off
    for the 134 suites after it.
    """
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (AUTONOTIFY,)).fetchone()
    was = row["value"] if row else None
    conn.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (AUTONOTIFY, value))
    conn.commit()
    return was


def run():
    s = Suite("declining, and what follows from it")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    was_autonotify = _switch(conn, "1")
    now = m.datetime.now(m.timezone.utc).isoformat()
    today = house_today()

    def rowid():
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.section("Setup")
        s.check("a room exists", False, detail="reported rather than skipped")
        conn.close()
        return s

    def make_booking(ref, arrival, nights=2, paid=False):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, payment_status,
                       created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'pending', 400, ?, ?)""",
            (room["id"], TAG + ref, (TAG + ref).lower(), TAG + " " + ref,
             f"{TAG}.{ref}@example.invalid".lower(), arrival.isoformat(),
             (arrival + timedelta(days=nights)).isoformat(),
             "paid" if paid else "unpaid", now))
        return rowid()

    # ------------------------------------------------------- the waiting list
    s.section("A room freed tells whoever was waiting for it")
    arrival = today + timedelta(days=210)
    bid = make_booking("ONE", arrival)
    conn.execute(
        """INSERT INTO waitlist_entries (name, email, desired_arrival,
                   desired_departure, party_size, status, created_at)
           VALUES (?, ?, ?, ?, 2, 'open', ?)""",
        (TAG + " Waiting", f"{TAG}.w@example.invalid".lower(),
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(), now))
    conn.commit()

    with _MailReaches() as mail:
        r = oc.post(f"/admin/bookings/{bid}/decline", follow_redirects=True)
    said = " ".join(flashes(r))
    row = conn.execute("SELECT status, decided_at FROM bookings WHERE id = ?",
                       (bid,)).fetchone()
    s.check("the booking is declined", row and row["status"] == "declined",
            detail=str(dict(row)) if row else "gone")
    s.check("and the moment is recorded", bool(row and row["decided_at"]),
            detail="a decision with no time on it cannot be disputed or "
                   "explained later")
    s.check("the guest waiting was written to",
            any(f"{TAG}.w@example.invalid".lower() == to for to, _sub in mail.sent),
            detail=str(mail.sent))
    s.check("the message says how many were told",
            "Notified 1 waitlist guest" in said,
            detail=said or "nothing was said")
    s.check("and that entry is marked contacted, not left open",
            conn.execute(
                "SELECT status FROM waitlist_entries WHERE name = ?",
                (TAG + " Waiting",)).fetchone()["status"] == "contacted",
            detail="left open, the next cancellation on any date tells them "
                   "all over again")

    s.section("And when nobody could be told, it says who is still waiting")
    # The other half of that same sentence, and it needs an entry that COUNTS
    # but is not notified. Party size does not do it: the match is
    # deliberately not size-aware, because a waitlist request is for
    # "anything available" and the note is a nudge to go and look. An entry
    # already CONTACTED is the real case -- matching_waitlist_entries counts
    # open and contacted, notify_room_waitlist_opening emails only open --
    # and it is the honest one too: somebody told about an earlier opening is
    # still waiting for these dates.
    far = today + timedelta(days=640)
    bid2 = make_booking("TWO", far)
    conn.execute(
        """INSERT INTO waitlist_entries (name, email, desired_arrival,
                   desired_departure, party_size, status, created_at)
           VALUES (?, ?, ?, ?, 2, 'contacted', ?)""",
        (TAG + " Already", f"{TAG}.b@example.invalid".lower(),
         far.isoformat(), (far + timedelta(days=2)).isoformat(), now))
    conn.commit()
    r = oc.post(f"/admin/bookings/{bid2}/decline", follow_redirects=True)
    said2 = " ".join(flashes(r))
    s.check("the booking is declined",
            conn.execute("SELECT status FROM bookings WHERE id = ?",
                         (bid2,)).fetchone()["status"] == "declined")
    s.check("nobody was emailed a second time",
            "Notified" not in said2,
            detail=said2 or "nothing was said")
    s.check("and it says somebody is still waiting",
            "check the waitlist" in said2,
            detail=said2 or "nothing was said — an owner declining at "
                            "midnight would read that as an empty list")

    # ------------------------------------------------------------- the money
    s.section("A refund that cannot be issued is said out loud")
    paid_id = make_booking("PAID", today + timedelta(days=250), paid=True)
    conn.commit()
    r = oc.post(f"/admin/bookings/{paid_id}/decline", follow_redirects=True)
    said3 = " ".join(flashes(r))
    s.check("the booking is still declined",
            conn.execute("SELECT status FROM bookings WHERE id = ?",
                         (paid_id,)).fetchone()["status"] == "declined",
            detail="a refund that cannot be issued does not un-decline it")
    s.check("and the failure is reported rather than swallowed",
            "refund" in said3.lower(),
            detail=said3 or "nothing was said — this is the exact silence "
                            "decline_one_and_follow_up was written to end")
    s.check("nothing was recorded as refunded",
            conn.execute(
                "SELECT COUNT(*) FROM refunds WHERE booking_id = ? "
                "AND category = 'room'", (paid_id,)).fetchone()[0] == 0,
            detail="a refund row with no money behind it reads as settled")

    s.section("Declining twice does it once")
    r = oc.post(f"/admin/bookings/{bid}/decline", follow_redirects=False)
    s.check("the second attempt is refused", r.status_code == 404,
            detail=f"status {r.status_code}")
    s.check("and the booking is unchanged",
            conn.execute("SELECT status FROM bookings WHERE id = ?",
                         (bid,)).fetchone()["status"] == "declined")

    # --------------------------------------------------------- the restaurant
    s.section("A table given back tells the restaurant's waiting list")
    dinner = today + timedelta(days=30)
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token,
                   guest_name, guest_email, dinner_date, party_size, status,
                   total_price, payment_status, created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'pending', 0, 'unpaid', ?)""",
        (TAG + "RES", (TAG + "res").lower(), TAG + " Diner",
         f"{TAG}.res@example.invalid".lower(), dinner.isoformat(), now))
    res_id = rowid()
    conn.execute(
        """INSERT INTO restaurant_waitlist (name, email, desired_date,
                   party_size, status, created_at)
           VALUES (?, ?, ?, 2, 'open', ?)""",
        (TAG + " Hungry", f"{TAG}.h@example.invalid".lower(),
         dinner.isoformat(), now))
    conn.commit()
    with _MailReaches() as mail4:
        r = oc.post(f"/admin/restaurant/{res_id}/decline", follow_redirects=True)
    said4 = " ".join(flashes(r))
    s.check("the reservation is declined",
            conn.execute("SELECT status FROM restaurant_bookings WHERE id = ?",
                         (res_id,)).fetchone()["status"] == "declined")
    s.check("the guest waiting for a table was written to",
            any(f"{TAG}.h@example.invalid".lower() == to
                for to, _sub in mail4.sent),
            detail=str(mail4.sent))
    s.check("and the message says how many were told",
            "Notified 1 waitlist guest" in said4,
            detail=said4 or "nothing was said")

    # ---------------------------------------------------------- the workshop
    s.section("A place given back tells the workshop's waiting list")
    ws = conn.execute(
        "SELECT id FROM workshop_sessions ORDER BY id DESC LIMIT 1").fetchone()
    if ws:
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, reference_code,
                       manage_token, guest_name, guest_email, party_size,
                       status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, 1, 'pending', 0, ?)""",
            (ws["id"], TAG + "WSB", (TAG + "wsb").lower(), TAG + " Maker",
             f"{TAG}.ws@example.invalid".lower(), now))
        wsb = rowid()
        conn.execute(
            """INSERT INTO workshop_waitlist (session_id, name, email,
                       party_size, status, created_at)
               VALUES (?, ?, ?, 1, 'open', ?)""",
            (ws["id"], TAG + " Keen", f"{TAG}.k@example.invalid".lower(), now))
        conn.commit()
        with _MailReaches() as mail5:
            r = oc.post(f"/admin/workshops/registrations/{wsb}/decline",
                        follow_redirects=True)
        said5 = " ".join(flashes(r))
        s.check("the registration is declined",
                conn.execute(
                    "SELECT status FROM workshop_bookings WHERE id = ?",
                    (wsb,)).fetchone()["status"] == "declined")
        s.check("the guest waiting for a place was written to",
                any(f"{TAG}.k@example.invalid".lower() == to
                    for to, _sub in mail5.sent),
                detail=str(mail5.sent))
        s.check("and the message says how many were told",
                "Notified 1 waitlist guest" in said5,
                detail=said5 or "nothing was said")
    else:
        s.check("a workshop session exists to decline a place on", False,
                detail="reported rather than skipped: the checks above would "
                       "pass on nothing")

    s.section("And the transports are back as the harness pinned them")
    s.check("send_email is the harness's own again",
            m.send_email.__name__ != "_accept",
            detail="left replaced, every later suite would believe its mail "
                   "went out")

    if was_autonotify is not None:
        _switch(conn, was_autonotify)
    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
