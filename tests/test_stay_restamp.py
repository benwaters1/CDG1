"""Everything stamped on a stay follows the stay when it moves.

Three figures are stamped on a room booking rather than derived — the taxe de
séjour, the deposit, and the dated balance — and three paths move a stay: the
owner editing it, the guest changing their own dates, and the guest adding
nights on the end. One cell of those nine was covered. The tax was re-stamped
on the owner edit, with a comment saying exactly why it had to be, and nothing
else was re-stamped anywhere.

What it cost, measured before the fix: a stay moved from October to the
following April kept a balance falling due on 30 September. The reminder job
selects its window on balance_due_date, so that guest was chased seven months
early — and because the reminder is sent once per booking and stamps itself,
they were then never chased again. Nobody asks in April, and the money turns up
as a conversation at reception, which is the exact thing the reminder job was
written to prevent.

Two things carry this file.

  ALL THREE FIGURES, ON ALL THREE PATHS. The grid is the fault, so the checks
  are a grid. One function does the re-stamping and all three paths call it,
  which is the only arrangement in which they cannot drift apart again.

  A DEPOSIT ALREADY TAKEN IS NOT RE-QUOTED. What was charged is history, and
  restating it moves a figure a card has already been debited for. Only what is
  left, and when it falls due, follow the stay. The workshop side has worked
  this way since it was written.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZRS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _setting(conn, key, value):
    conn.execute("""INSERT INTO app_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                 (key, value))
    conn.commit()


def _read(conn, key):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _free(booking_id, from_date, nights=2):
    """The first window from here that the house would actually accept.

    The seeded ateliers hold the WHOLE chateau for their runs, so a date
    reached by arithmetic alone is refused every so often -- and a refused
    edit is indistinguishable from a feature that does not work. This suite
    lost half an hour to exactly that.
    """
    conn = db()
    try:
        row = conn.execute("SELECT room_id FROM bookings WHERE id = ?",
                           (booking_id,)).fetchone()
        day = from_date
        for _ in range(200):
            ok, _why = m.is_range_available(
                conn, row["room_id"], day, day + timedelta(days=nights),
                exclude_booking_id=booking_id)
            if ok:
                return day
            day += timedelta(days=3)
        raise AssertionError("no free window in the next 600 days")
    finally:
        conn.close()


def _row(booking_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    finally:
        conn.close()


def _make(conn, room, ref, arrival, nights=2, paid=False):
    with m.app.test_request_context("/"):
        code, token = m.create_booking(
            conn, room, f"{TAG} {ref}", f"zzrs.{ref}@example.invalid".lower(), "",
            arrival, arrival + timedelta(days=nights), 2, "", [],
            payment_status="paid" if paid else "unpaid")
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?", (code,)).fetchone()
    conn.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ?", (row["id"],))
    conn.commit()
    return conn.execute("SELECT * FROM bookings WHERE id = ?", (row["id"],)).fetchone()


def run():
    s = Suite("A stay that moves takes its figures with it")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    was_rate = room["price_per_night"]
    was_pct = _read(conn, "room_deposit_percent")
    was_days = _read(conn, "room_balance_due_days_before")
    was_tax = _read(conn, "city_tax_per_adult_per_night")
    conn.execute("UPDATE rooms SET price_per_night = 200 WHERE id = ?", (room["id"],))
    conn.commit()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    # A deposit, so there is a dated balance to move at all. At the shipped
    # default of 0% there is no schedule and this whole fault is invisible,
    # which is most of why it survived.
    _setting(conn, "room_deposit_percent", "30")
    _setting(conn, "room_balance_due_days_before", "14")
    _setting(conn, "city_tax_per_adult_per_night", "0.80")

    sent = []
    was_email = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append(to), True)[1]

    try:
        arrival = m.house_today() + timedelta(days=40)
        b = _make(conn, room, "MOVE", arrival)
        conn.close()

        s.section("As booked")
        s.check("a deposit is asked for now",
                b["deposit_amount"] and abs(b["deposit_amount"] - 120) < 0.01,
                detail=f"{b['deposit_amount']} — 30% of 400")
        s.check("and a balance falls due before they travel",
                b["balance_due_date"] ==
                (arrival - timedelta(days=14)).isoformat(),
                detail=f"{b['balance_due_date']}")
        s.check("with the taxe de sejour stamped too",
                b["city_tax"] and abs(b["city_tax"] - 3.20) < 0.01,
                detail=f"{b['city_tax']} — two adults, two nights, 0.80")

        s.section("The owner moves the stay six months later")
        later = _free(b["id"], arrival + timedelta(days=180))
        oc.post(f"/admin/bookings/{b['id']}/edit", data={
            "arrival_date": later.isoformat(),
            "departure_date": (later + timedelta(days=2)).isoformat(),
            "party_size": "2", "guest_phone": "", "special_requests": "",
            "source": "direct", "reference_code": b["reference_code"],
        }, follow_redirects=True)
        moved = _row(b["id"])
        # THE MEASUREMENT. Before the fix this still read 30 September.
        s.check("the balance falls due before the NEW arrival",
                moved["balance_due_date"] == (later - timedelta(days=14)).isoformat(),
                detail=f"{moved['balance_due_date']} for a stay arriving "
                       f"{moved['arrival_date']} — a date in the past means the "
                       "guest is chased seven months early and then never again")
        conn = db()
        with m.app.test_request_context("/"):
            bill = m.booking_bill(conn, b["id"])
        conn.close()
        s.check("and their own page does not call it overdue",
                not bill["balance_overdue"],
                detail="a stay months away is not a debt")

        s.section("So the reminder reaches them at the right time, not the wrong one")
        sent.clear()
        conn = db()
        with m.app.test_request_context("/"):
            said = m.run_room_balance_reminder_job(conn, 30)
        conn.close()
        s.check("nobody is chased for a stay seven months off",
                b["guest_email"] not in sent, detail=f"{said} / {sent}")

        s.section("And a reminder already sent can go out again for the new date")
        # The second half of the same fault: the reminder is once per booking
        # and stamps itself, so being chased early was also being exempted for
        # ever. Both directions, because a deadline that has moved NEARER than
        # the one the guest wrote down is the more urgent of the two.
        def _stamp_reminder():
            c = db()
            c.execute("UPDATE bookings SET balance_reminder_sent_at = ? WHERE id = ?",
                      (datetime.now(timezone.utc).isoformat(), b["id"]))
            c.commit()
            c.close()

        def _move(to_date):
            oc.post(f"/admin/bookings/{b['id']}/edit", data={
                "arrival_date": to_date.isoformat(),
                "departure_date": (to_date + timedelta(days=2)).isoformat(),
                "party_size": "2", "guest_phone": "", "special_requests": "",
                "source": "direct", "reference_code": b["reference_code"],
            }, follow_redirects=True)

        _stamp_reminder()
        _move(_free(b["id"], later + timedelta(days=90)))
        s.check("set aside when the date moves further away",
                _row(b["id"])["balance_reminder_sent_at"] is None,
                detail="a reminder that went out for a date that no longer "
                       "exists has to be able to go out again")
        _stamp_reminder()
        nearer = _free(b["id"], m.house_today() + timedelta(days=40))
        _move(nearer)
        s.check("and when it moves nearer",
                _row(b["id"])["balance_reminder_sent_at"] is None,
                detail="they were told a deadline that is now sooner than the "
                       "one they wrote down")
        # And an edit that moves nothing must not re-chase anybody.
        _stamp_reminder()
        _move(nearer)
        s.check("but an edit that leaves the dates alone does not",
                _row(b["id"])["balance_reminder_sent_at"] is not None,
                detail="a second copy of the same email, for the same date, "
                       "because somebody fixed a telephone number")
        conn = db()
        conn.execute("UPDATE bookings SET balance_reminder_sent_at = NULL WHERE id = ?",
                     (b["id"],))
        conn.commit()
        conn.close()
        # And it genuinely arrives when the new date comes round.
        conn = db()
        near = m.house_today() + timedelta(days=20)
        conn.execute(
            """UPDATE bookings SET arrival_date = ?, departure_date = ?,
               balance_due_date = ? WHERE id = ?""",
            (near.isoformat(), (near + timedelta(days=2)).isoformat(),
             (m.house_today() + timedelta(days=6)).isoformat(), b["id"]))
        conn.commit()
        sent.clear()
        with m.app.test_request_context("/"):
            said = m.run_room_balance_reminder_job(conn, 30)
        conn.close()
        s.check("and it does arrive once the balance is actually near",
                b["guest_email"] in sent, detail=f"{said} / {sent}")

        s.section("The guest changing their own dates")
        conn = db()
        own = _make(conn, room, "OWN", arrival)
        conn.execute("UPDATE bookings SET status = 'pending', payment_status = 'unpaid' "
                     "WHERE id = ?", (own["id"],))
        conn.commit()
        conn.close()
        their = _free(own["id"], arrival + timedelta(days=90), nights=4)
        oc.post(f"/book/manage/{own['manage_token']}", data={
            "action": "change_dates",
            "new_arrival_date": their.isoformat(),
            "new_departure_date": (their + timedelta(days=4)).isoformat(),
        }, follow_redirects=True)
        after = _row(own["id"])
        s.check("the due date follows",
                after["balance_due_date"] == (their - timedelta(days=14)).isoformat(),
                detail=f"{after['balance_due_date']}")
        s.check("and so does the taxe de sejour",
                after["city_tax"] and abs(after["city_tax"] - 6.40) < 0.01,
                detail=f"{after['city_tax']} — two nights became four, and the "
                       "commune is owed the difference")
        s.check("with the balance recut from the longer stay",
                (after["balance_amount"] or 0) > (own["balance_amount"] or 0),
                detail=f"{own['balance_amount']} then {after['balance_amount']}")

        s.section("The guest adding nights on the end")
        conn = db()
        more = _make(conn, room, "MORE", arrival)
        conn.close()
        oc.post(f"/book/manage/{more['manage_token']}",
                data={"action": "add_nights", "nights": "2"}, follow_redirects=True)
        longer = _row(more["id"])
        s.check("the extra nights are taxed",
                longer["city_tax"] and abs(longer["city_tax"] - 6.40) < 0.01,
                detail=f"{longer['city_tax']} against {more['city_tax']}")
        s.check("and the balance grows with them",
                (longer["balance_amount"] or 0) > (more["balance_amount"] or 0),
                detail=f"{more['balance_amount']} then {longer['balance_amount']}")
        s.check("the due date still sits before arrival",
                longer["balance_due_date"] ==
                (arrival - timedelta(days=14)).isoformat(),
                detail=f"{longer['balance_due_date']} — the arrival did not "
                       "move, so neither should this")

        s.section("A deposit already taken is not re-quoted")
        conn = db()
        held = _make(conn, room, "PAID", arrival)
        conn.execute(
            """UPDATE bookings SET deposit_amount = 120, deposit_paid_at = ?,
               amount_paid = 120 WHERE id = ?""",
            (datetime.now(timezone.utc).isoformat(), held["id"]))
        conn.commit()
        conn.close()
        # Somewhere free and five nights long, so the total genuinely grows --
        # a refused edit would leave the deposit at 120 and this check would
        # pass without the code doing anything.
        bigger = _free(held["id"], arrival + timedelta(days=300), nights=5)
        oc.post(f"/admin/bookings/{held['id']}/edit", data={
            "arrival_date": bigger.isoformat(),
            "departure_date": (bigger + timedelta(days=5)).isoformat(),
            "party_size": "2", "guest_phone": "", "special_requests": "",
            "source": "direct", "reference_code": held["reference_code"],
        }, follow_redirects=True)
        grown = _row(held["id"])
        s.check("the stay did grow", (grown["total_price"] or 0) > 900,
                detail=f"{grown['total_price']} — without this the check below "
                       "passes on a refused edit")
        s.check("what was charged stays what was charged",
                abs((grown["deposit_amount"] or 0) - 120) < 0.01,
                detail=f"{grown['deposit_amount']} — a card has already been "
                       "debited for this, and restating it moves history")
        s.check("and the whole of the rest is the balance",
                abs((grown["balance_amount"] or 0)
                    - ((grown["total_price"] or 0) - 120)) < 0.01,
                detail=f"{grown['balance_amount']} of {grown['total_price']}")
        s.check("the deposit is still marked paid",
                bool(grown["deposit_paid_at"]))

        s.section("A stay that was never charged the tax does not start owing it")
        # THE ONE THAT WOULD MOVE WHAT SOMEBODY ALREADY AGREED. booking_bill
        # reads the stamped tax and never computes one, because a stay taken
        # before the taxe de sejour existed was never charged it. An edit is
        # not the moment to start. A zero and a never are the same thing on
        # the row -- the column is NOT NULL DEFAULT 0 -- so nil stays nil.
        conn = db()
        old_stay = _make(conn, room, "OLD", arrival)
        conn.execute("UPDATE bookings SET city_tax = 0, amount_paid = total_price, "
                     "payment_status = 'paid' WHERE id = ?", (old_stay["id"],))
        conn.commit()
        conn.close()
        elsewhere = _free(old_stay["id"], arrival + timedelta(days=400), nights=3)
        oc.post(f"/admin/bookings/{old_stay['id']}/edit", data={
            "arrival_date": elsewhere.isoformat(),
            "departure_date": (elsewhere + timedelta(days=3)).isoformat(),
            "party_size": "2", "guest_phone": "", "special_requests": "",
            "source": "direct", "reference_code": old_stay["reference_code"],
        }, follow_redirects=True)
        untaxed = _row(old_stay["id"])
        s.check("the stay did move", untaxed["arrival_date"] == elsewhere.isoformat(),
                detail=f"{untaxed['arrival_date']}")
        s.check("and no tax appears on it",
                not untaxed["city_tax"],
                detail=f"{untaxed['city_tax']} — inventing one moves what an "
                       "existing guest owes for a stay already agreed, in some "
                       "cases already paid in full")
        conn = db()
        with m.app.test_request_context("/"):
            untaxed_bill = m.booking_bill(conn, old_stay["id"])
        conn.close()
        s.check("so their bill has no tax line",
                not [l for l in untaxed_bill["lines"] if l["kind"] == "city_tax"],
                detail=f"{[l['label'] for l in untaxed_bill['lines']]}")

        s.section("With no deposit configured, nothing is scheduled at all")
        # The shipped default, and the state in which this whole fault is
        # invisible — which is most of why it survived.
        conn = db()
        _setting(conn, "room_deposit_percent", "0")
        plain = _make(conn, room, "PLAIN", arrival)
        conn.close()
        oc.post(f"/admin/bookings/{plain['id']}/edit", data={
            "arrival_date": (arrival + timedelta(days=7)).isoformat(),
            "departure_date": (arrival + timedelta(days=9)).isoformat(),
            "party_size": "2", "guest_phone": "", "special_requests": "",
            "source": "direct", "reference_code": plain["reference_code"],
        }, follow_redirects=True)
        s.check("no due date is invented", _row(plain["id"])["balance_due_date"] is None,
                detail=f"{_row(plain['id'])['balance_due_date']} — a stay paid "
                       "in one go has no dated balance")
        s.check("but the tax still follows the stay",
                _row(plain["id"])["city_tax"] is not None,
                detail="the tax is owed whether or not a deposit is taken")
    finally:
        m.send_email = was_email
        conn = db()
        conn.execute("UPDATE rooms SET price_per_night = ? WHERE id = ?",
                     (was_rate, room["id"]))
        for key, value in (("room_deposit_percent", was_pct),
                           ("room_balance_due_days_before", was_days),
                           ("city_tax_per_adult_per_night", was_tax)):
            if value is None:
                conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            else:
                _setting(conn, key, value)
        conn.commit()
        conn.close()
        _cleanup()
    return s
