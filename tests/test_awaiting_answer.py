"""A guest waiting for an answer, before the app answers for them.

Every link in this chain was already true and nothing joined them up.

The booking page tells a guest "The château confirms within a day", and
"Every request is read by hand, usually the same day".

expire_stale_pending_bookings auto-declines a pending, unpaid request after
48 hours -- deliberately, and for a good reason: is_range_available treats
pending as a blocker, so a request nobody acts on holds those dates against
every other guest indefinitely.

It runs from run_housekeeping_job, on a ten-minute timer, whether or not
anybody is looking.

And the owner's morning panel -- thirteen warnings, including "No backup is
arriving" and "Somebody who has left still holds a key" -- said nothing about
a guest waiting. The only mention of the rule was a flash AFTER it fired:
"with no response after 48h".

WHAT IS HELD HERE

  THE PANEL NAMES THEM, and turns blocker when the deadline is close, because
  at that point it stops being "somebody is waiting" and becomes "a decline
  the house did not choose".

  THE NOTICE GOES OUT BEFORE THE EXPIRY, from inside the same job. That
  ordering is the whole feature. A panel warning helps an owner who opens the
  page; the request is declined on a timer, and the harm happens exactly when
  nobody opens anything. Both run from run_housekeeping_job, so which of the
  two sentences the owner gets -- "somebody is waiting" or "somebody was
  declined" -- is decided by nothing but the order of two lines.

  AND ONLY ONCE PER REQUEST. The job runs every ten minutes. Forty
  notifications an hour about one guest is not being told, it is being
  buried.

  A PAID request is included and has NO deadline, because those are excluded
  from the auto-decline by design -- a silent decline-and-refund because
  nobody checked the site is not a decision anybody made. It still wants
  answering.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZWAIT"


def _cleanup(conn):
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("a guest waiting for an answer")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = house_today()

    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if not room:
        s.section("Setup")
        s.check("a room exists", False, detail="reported rather than skipped")
        conn.close()
        return s

    def ask(ref, hours_ago, paid=False, arrival_offset=600):
        arrival = today + timedelta(days=arrival_offset)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, payment_status,
                       created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'pending', 400, ?, ?)""",
            (room["id"], TAG + ref, (TAG + ref).lower(), TAG + " " + ref,
             f"{TAG}.{ref}@example.invalid".lower(), arrival.isoformat(),
             (arrival + timedelta(days=2)).isoformat(),
             "paid" if paid else "unpaid",
             (now - timedelta(hours=hours_ago)).isoformat()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    just_asked = ask("NEW", 2, arrival_offset=600)
    overdue = ask("OLD", 30, arrival_offset=610)
    nearly = ask("NEARLY", 45, arrival_offset=620)
    paid_one = ask("PAID", 30, paid=True, arrival_offset=630)
    conn.commit()

    def waiting_ids():
        return {w["booking"]["id"]: w
                for w in m.bookings_awaiting_answer(conn, now)}

    s.section("Who counts as waiting")
    w = waiting_ids()
    s.check("a request made two hours ago does not",
            just_asked not in w,
            detail=f"the promise is {m.BOOKING_ANSWER_HOURS} hours")
    s.check("one made thirty hours ago does", overdue in w)
    s.check("and it says how long they have waited",
            overdue in w and 29 <= w[overdue]["waited"] <= 31,
            detail=str(w.get(overdue, {}).get("waited")))
    s.check("and how long is left before the app answers",
            overdue in w and 17 <= w[overdue]["left"] <= 19,
            detail=f"{w.get(overdue, {}).get('left')} — the app declines at "
                   f"{m.STALE_PENDING_BOOKING_HOURS} hours")

    s.section("A paid request waits too, and has no deadline")
    s.check("it is on the list", paid_one in w)
    s.check("with no countdown against it",
            paid_one in w and w[paid_one]["left"] is None,
            detail="a paid request is deliberately never auto-declined, "
                   "because a silent decline-and-refund is not a decision "
                   "anybody made")
    s.check("and it is marked as paid so the wording can differ",
            paid_one in w and w[paid_one]["paid"] is True)

    s.section("The oldest is first, because that is who to answer")
    order = [x["booking"]["id"] for x in m.bookings_awaiting_answer(conn, now)]
    s.check("the longest wait comes first", order and order[0] == nearly,
            detail=str(order))

    s.section("The morning panel says so")
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, today)
    hit = next((x for x in warns if "nobody has answered" in x["title"]), None)
    s.check("the panel carries it", hit is not None,
            detail=str([x["title"] for x in warns])[:160])
    s.check("it names the guest who has waited longest",
            hit and TAG + " NEARLY" in hit["detail"], detail=str(hit)[:200])
    s.check("and says how long is left rather than only that they waited",
            hit and "app declines" in hit["detail"], detail=str(hit)[:200])
    s.check("it is a blocker, because the deadline is close",
            hit and hit["severity"] == "blocker",
            detail=f"{hit['severity'] if hit else None} — three hours left is "
                   "not a thing to read past")
    s.check("and it links to where they can be answered",
            hit and "bookings" in hit["href"], detail=str(hit))

    s.section("With time in hand it asks for attention, not alarm")
    conn.execute("DELETE FROM bookings WHERE reference_code = ?", (TAG + "NEARLY",))
    conn.commit()
    with m.app.test_request_context("/"):
        warns = m.owner_home_warnings(conn, today)
    hit = next((x for x in warns if "nobody has answered" in x["title"]), None)
    s.check("still on the panel", hit is not None)
    s.check("but not a blocker with eighteen hours left",
            hit and hit["severity"] != "blocker",
            detail=f"{hit['severity'] if hit else None} — a panel that shouts "
                   "about everything is one nobody reads")

    s.section("And somebody is told, before the app answers for them")
    def notes():
        return conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? "
            "AND kind = 'booking_waiting'", (owner["id"],)).fetchone()["c"]

    before = notes()
    told = m.notify_bookings_awaiting_answer(conn, now)
    s.check("the ones waiting are announced", told >= 2, detail=str(told))
    s.check("as notifications the owner will see", notes() > before,
            detail=f"{before} -> {notes()}")

    s.section("And only once, however often the job runs")
    again = m.notify_bookings_awaiting_answer(conn, now)
    s.check("a second pass announces nobody", again == 0, detail=str(again))
    s.check("and writes no second notification", notes() == before + told,
            detail="the job runs every ten minutes; forty notices an hour "
                   "about one guest is being buried, not being told")
    s.check("the booking records that it was announced",
            conn.execute(
                "SELECT waiting_notified_at FROM bookings WHERE id = ?",
                (overdue,)).fetchone()["waiting_notified_at"] is not None)

    s.section("The job tells before it expires, not after")
    # The whole feature is this ordering. Both run on the same timer, so
    # which sentence the owner gets -- "somebody is waiting" or "somebody was
    # declined" -- is decided by nothing else.
    import inspect
    src = inspect.getsource(m.run_housekeeping_job)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    s.check("both are in the housekeeping job",
            "notify_bookings_awaiting_answer" in code
            and "expire_stale_pending_bookings" in code, detail=code[:200])
    s.check("and the telling comes first",
            code.index("notify_bookings_awaiting_answer")
            < code.index("expire_stale_pending_bookings"),
            detail="after the expiry, the owner is told a guest was declined "
                   "rather than that a guest was waiting")

    s.section("And the job proves it, rather than the source saying so")
    # The check above reads the two lines. This one runs them: a request at
    # 47 hours is inside the same pass that will expire it, so with the
    # telling first the owner hears about it and THEN it goes, and with the
    # telling second it is already declined and is never mentioned at all.
    # Past 48 hours, so the expiry really does fire in this pass -- at 47 it
    # survives either way and the check proves nothing.
    late = ask("LATE", 49, arrival_offset=640)
    conn.commit()
    before_late = notes()
    m.run_housekeeping_job(conn)
    conn.commit()
    s.check("the guest at forty-nine hours was announced",
            notes() > before_late,
            detail="with the expiry first there is nothing left to announce")
    s.check("and then declined by the same pass",
            conn.execute("SELECT status FROM bookings WHERE id = ?",
                         (late,)).fetchone()["status"] == "declined",
            detail="the expiry still has to happen; the point is the order")

    s.section("Answering it takes it off the list")
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?",
                 (overdue,))
    conn.commit()
    s.check("a confirmed request is no longer waiting",
            overdue not in waiting_ids(),
            detail="nothing here has a done action of its own; answering the "
                   "guest is what closes it")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
