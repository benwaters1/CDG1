"""What a bulk action says happened, and what actually happened.

Every bulk action in the app counted its successes and stayed quiet about the
rest — `if not row: continue`, then a cheerful total. The owner ticks ten
boxes, six go through, and the page says "Approved 6" with nothing at all to
say the other four did not. It reads as finished, and the four are found weeks
later or not at all.

One of them did something worse than stay quiet. Bulk confirm reported every
refusal as a date conflict, so a booking refused because somebody had left a
standing instruction not to accept that guest was announced as a clash with
another booking. The owner is sent to a calendar that is perfectly clear, and
the one refusal they needed to read is the one that got buried.

And bulk decline had a gap that was not about wording at all. It was written
as a loop over the core helper, so the two things the single-booking ROUTE did
afterwards never happened in bulk: the waitlist that had been sitting there
wanting exactly those dates was never worked, and a refund that failed at
Stripe was reported to nobody. Declining ten bookings one at a time and
declining the same ten together did different things to the house's money.

So this file is not about message wording. It is about a bulk action being the
same action as doing it one at a time, and saying so truthfully.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZBH"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(ref, *, room_id, paid=False, status="pending"):
    """Dates far enough out that nothing else in the database overlaps.

    Decline does not test availability, so the dates only have to be unique to
    this suite — which is worth saying, because picking "free" dates is where
    fixtures in this repo have gone wrong before.
    """
    conn = db()
    arrival = datetime.now(timezone.utc).date() + timedelta(days=900)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, ?, 400, ?, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzbh.{ref}@example.invalid".lower(), arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(), status,
         "paid" if paid else "unpaid", 400 if paid else 0,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _status(ref):
    conn = db()
    try:
        row = conn.execute("SELECT status FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-{ref}",)).fetchone()
        return row["status"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("Bulk honesty")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()

    # ---------------------------------------------------------- the reporter
    s.section("The message itself")
    msg, cat = m.bulk_message("Confirmed", "booking", 1, [])
    s.check("one is singular", msg == "Confirmed 1 booking." and cat == "success",
            detail=f"{msg!r}")
    msg, cat = m.bulk_message("Confirmed", "booking", 3, [])
    s.check("three are plural", msg == "Confirmed 3 bookings.", detail=f"{msg!r}")

    msg, cat = m.bulk_message("Confirmed", "booking", 0, [])
    s.check("nothing selected says nothing happened",
            "Nothing was selected" in msg and cat == "error", detail=f"{msg!r}")

    # The whole point. Six of ten with four skipped must not read as six of six.
    msg, cat = m.bulk_message("Confirmed", "booking", 6, [
        ("GUD-1", "the room is taken"), ("GUD-2", "the room is taken"),
        ("GUD-3", "a standing instruction not to accept them")])
    s.check("a partial run says it is partial",
            msg.startswith("Confirmed 6 of 9 bookings."), detail=f"{msg!r}")
    s.check("and is an error, not a success", cat == "error",
            detail="a bulk action that half worked is exactly the thing that "
                   "must not look clean")
    s.check("both reasons survive",
            "the room is taken" in msg
            and "a standing instruction not to accept them" in msg,
            detail=f"{msg!r} — the second reason is the one somebody has to act "
                   "on, and it is the one a single count loses")
    s.check("a shared reason is said once, not per item",
            msg.count("the room is taken") == 1, detail=f"{msg!r}")
    s.check("and the items are named", "GUD-1" in msg and "GUD-3" in msg,
            detail=f"{msg!r} — a count tells you something went wrong and not "
                   "which of forty rows it was")

    many = [(f"GUD-{i}", "the room is taken") for i in range(9)]
    msg, cat = m.bulk_message("Confirmed", "booking", 1, many)
    s.check("a long list is capped rather than dumped",
            "and 6 more" in msg and "GUD-8" not in msg,
            detail=f"{msg!r} — a flash naming twenty references is not read")

    msg, cat = m.bulk_message("Removed", "task", 0, [("Beds", "already done")])
    s.check("none at all says none at all",
            msg.startswith("Nothing was removed."), detail=f"{msg!r}")

    msg, cat = m.bulk_message("Approved", "item", 4, [], detail="3 time off, 1 expense")
    s.check("a breakdown rides along when there is one",
            msg == "Approved 4 items (3 time off, 1 expense).", detail=f"{msg!r}")

    # ------------------------------------------------- declining, in bulk
    s.section("Bulk decline does what declining one at a time does")
    a = _booking("A", room_id=room["id"])
    b = _booking("B", room_id=room["id"])
    sent, notified_for = [], []
    was_email, was_notify = m.send_email, m.notify_room_waitlist_opening
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]
    # Two entries, so the count in the message has to come from the return
    # value and cannot be a coincidence of the number of bookings.
    m.notify_room_waitlist_opening = lambda conn, arr, dep: (
        notified_for.append((arr, dep)), [{"id": 1}, {"id": 2}])[1]
    try:
        r = oc.post("/admin/bookings/bulk-decline",
                    data={"booking_ids": [str(a["id"]), str(b["id"])]},
                    follow_redirects=True)
        msg = " ".join(flashes(r))
        s.check("both are declined",
                _status("A") == "declined" and _status("B") == "declined",
                detail=f"{msg}")
        # THE BUG. Ten bookings declined one at a time worked the waitlist ten
        # times; the same ten together worked it not at all.
        s.check("the waitlist is worked for every booking",
                len(notified_for) == 2, detail=f"{notified_for} — ten room-nights "
                "came free and nobody waiting for them was told")
        s.check("and the message says how many were told",
                "4 waitlist guests told" in msg,
                detail=f"{msg} — two entries for each of two bookings")
    finally:
        m.send_email, m.notify_room_waitlist_opening = was_email, was_notify

    s.section("A refund that fails is money, and has to be said")
    paid = _booking("PAID", room_id=room["id"], paid=True)
    was_refund = m.refund_booking
    m.send_email = lambda to, subj, body, **k: True
    m.notify_room_waitlist_opening = lambda conn, arr, dep: []
    m.refund_booking = lambda conn, booking: (False, "card expired")
    try:
        r = oc.post("/admin/bookings/bulk-decline",
                    data={"booking_ids": [str(paid["id"])]}, follow_redirects=True)
        msg = " ".join(flashes(r))
        s.check("the decline still stands", _status("PAID") == "declined",
                detail=f"{msg} — a refund that failed is not a booking that "
                       "failed to decline")
        s.check("but the failure is named, with the booking and the reason",
                "did NOT go through" in msg and f"{TAG}-PAID" in msg
                and "card expired" in msg,
                detail=f"{msg} — the single-booking route has always said this "
                       "and bulk threw it away")
    finally:
        m.refund_booking, m.send_email = was_refund, was_email
        m.notify_room_waitlist_opening = was_notify

    s.section("Bulk decline reports what it could not do")
    already = _booking("DONE", room_id=room["id"], status="confirmed")
    m.send_email = lambda to, subj, body, **k: True
    m.notify_room_waitlist_opening = lambda conn, arr, dep: []
    try:
        r = oc.post("/admin/bookings/bulk-decline",
                    data={"booking_ids": [str(already["id"]), "99999999"]},
                    follow_redirects=True)
        msg = " ".join(flashes(r))
        s.check("an already-confirmed booking is named, not swallowed",
                f"{TAG}-DONE" in msg and "already confirmed" in msg,
                detail=f"{msg}")
        s.check("and one that has gone is too", "no longer there" in msg,
                detail=f"{msg}")
        s.check("nothing is claimed to have been declined",
                "Nothing was declined" in msg, detail=f"{msg}")
        s.check("and the confirmed booking is untouched",
                _status("DONE") == "confirmed", detail=f"{_status('DONE')}")
    finally:
        m.send_email, m.notify_room_waitlist_opening = was_email, was_notify

    # ------------------------------------------------------ approvals queue
    s.section("The approvals queue says what it skipped")
    emp = _harness.ensure_employee()
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    for ref, status in (("P", "pending"), ("D", "approved")):
        conn.execute(
            """INSERT INTO leave_requests (user_id, start_date, end_date, reason,
               leave_type, status, requested_at) VALUES (?, ?, ?, ?, 'vacation', ?, ?)""",
            (emp["id"], "2037-04-01", "2037-04-03", f"{TAG} {ref}", status, now))
    conn.commit()
    rows = {r["reason"]: r["id"] for r in conn.execute(
        "SELECT id, reason FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))}
    conn.close()

    m.send_email = lambda to, subj, body, **k: True
    try:
        r = oc.post("/admin/approvals/bulk",
                    data={"items": [f"leave:{rows[TAG + ' P']}",
                                    f"leave:{rows[TAG + ' D']}",
                                    "expense:99999999", "widget:3", "leave:abc"]},
                    follow_redirects=True)
        msg = " ".join(flashes(r))
        s.check("the pending one is approved", "Approved 1 of 5 items" in msg,
                detail=f"{msg}")
        s.check("the breakdown by kind survives", "1 time off, 0 expenses" in msg,
                detail=f"{msg} — two kinds go through this one queue and the "
                       "owner wants to know which")
        # The ordinary case, not the strange one: two people working the same
        # queue at once. It used to be an identical silent `continue`.
        s.check("one somebody else already decided says so",
                "already approved" in msg, detail=f"{msg}")
        s.check("naming the person, not the row number",
                emp["name"] in msg, detail=f"{msg}")
        s.check("an expense that has gone is reported",
                "no longer there" in msg, detail=f"{msg}")
        s.check("and a kind this queue does not handle is not silently dropped",
                "not something this queue approves" in msg
                or "not something that can be approved" in msg, detail=f"{msg}")
    finally:
        m.send_email = was_email

    conn = db()
    left = conn.execute("SELECT status FROM leave_requests WHERE id = ?",
                        (rows[TAG + " P"],)).fetchone()["status"]
    conn.close()
    s.check("the approval really happened", left == "approved", detail=left)

    _cleanup()
    return s
