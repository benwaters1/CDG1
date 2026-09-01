"""Chasing a room balance before the guest travels.

Workshop guests have been reminded since the start. Room guests never were, so
somebody owing €600 on a stay arriving Friday found out at the door — or nobody
did, and it became a conversation at reception instead of a payment.

Three things this has to get right, all of which cost money or goodwill:

  - WHAT IS OWED comes from booking_bill, not `total_price - amount_paid`.
    The bill recomputes the nights from the rates and counts extras as real
    line items, so a guest who added a night or a dinner is chased for what
    they actually owe. The subtraction misses everything added after booking,
    which is exactly how a bill and a reminder come to disagree in front of
    somebody who has just arrived.

  - ONCE. `balance_reminder_sent_at` is stamped only when the send returns
    true, so a run with no email provider configured leaves the booking
    un-stamped and tries again when one exists — rather than marking everybody
    reminded and telling nobody. That distinction is the whole reason the
    workshop version was written the way it was.

  - NOT EVERYBODY. Somebody who has paid, somebody who cancelled, and somebody
    arriving in four months must not receive it.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZRBR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _booking(ref, days_ahead, total, paid, status="confirmed", email=None):
    conn = db()
    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=days_ahead)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid" if email is None else email,
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(), status,
         total, paid, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _stamped(ref):
    conn = db()
    try:
        row = conn.execute(
            "SELECT balance_reminder_sent_at FROM bookings WHERE reference_code = ?",
            (f"{TAG}-{ref}",)).fetchone()
        return bool(row and row["balance_reminder_sent_at"])
    finally:
        conn.close()


def _run(days_before=7, provider=True):
    """Run the job with a capturing provider, or one that cannot deliver."""
    sent = []

    def ok(to, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    def broken(to, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body})
        return False        # what send_email really returns with nothing configured

    was = m.send_email
    m.send_email = ok if provider else broken
    conn = db()
    try:
        with m.app.test_request_context("/"):
            result = m.run_room_balance_reminder_job(conn, days_before)
    finally:
        conn.close()
        m.send_email = was
    return result, sent


def run():
    s = Suite("Room balance reminders")
    _cleanup()
    clients()

    s.section("Only the guest who owes something")
    owes = _booking("OWES", 4, total=900.0, paid=300.0)
    _booking("PAID", 4, total=900.0, paid=900.0)
    _booking("GONE", 4, total=900.0, paid=0.0, status="cancelled")
    _booking("LATER", 120, total=900.0, paid=0.0)
    result, sent = _run()
    to = [e["to"] for e in sent]
    s.check("the one with a balance is written to",
            any("owes" in t for t in to), detail=f"{to}")
    s.check("somebody who has paid is not", not any("paid" in t for t in to),
            detail=f"{to} — a guest who has paid was chased for money")
    s.check("nor is a cancelled booking", not any("gone" in t for t in to),
            detail=f"{to}")
    s.check("nor somebody arriving in four months",
            not any("later" in t for t in to), detail=f"{to}")
    s.check("and the job says what it did", "reminded" in result, detail=result)

    s.section("The email says what is owed and how to pay it")
    mail = next((e for e in sent if "owes" in e["to"]), None)
    s.check("there is one", mail is not None)
    if mail:
        s.check("it names the room", "Mountain View" in mail["subject"]
                or "Room" in mail["subject"], detail=f"{mail['subject']!r}")
        s.check("it gives a figure", "€" in mail["body"])
        s.check("and a link to settle it", "/book/manage/" in mail["body"],
                detail="no way to pay from the reminder")
        s.check("and the reference code", f"{TAG}-OWES" in mail["body"])
        s.check("it does not demand payment before arrival",
                "on arrival" in mail["body"].lower(),
                detail="a chase with no option to pay on the day reads badly "
                       "for a stay somebody has already committed to")

    s.section("Once, not every night")
    s.check("the first run stamped them", _stamped("OWES"))
    result2, sent2 = _run()
    s.check("a second run writes to nobody", not sent2, detail=f"{sent2}")
    s.check("and says so", "nobody" in result2 or "0 of" in result2, detail=result2)

    s.section("What is owed is the bill, not total minus paid")
    # booking_bill recomputes the nights from the rates and counts extras as
    # real lines. The subtraction misses anything added after booking.
    _cleanup()
    b = _booking("BILL", 3, total=100.0, paid=0.0)
    conn = db()
    bill = m.booking_bill(conn, b["id"])
    conn.close()
    _result, sent3 = _run()
    mail = next((e for e in sent3 if "bill" in e["to"]), None)
    s.check("the reminder quotes the bill's figure",
            mail is not None and f"{bill['owed']:.2f}" in mail["body"],
            detail=f"bill says {bill['owed']:.2f}, "
                   f"stored total_price was 100.00")

    s.section("With no email provider, nobody is marked as told")
    # send_email returns False when nothing is configured. Stamping on that
    # would mark the whole house reminded and deliver none of it.
    _cleanup()
    _booking("NOPROV", 3, total=900.0, paid=100.0)
    _run(provider=False)
    s.check("the booking is not stamped", not _stamped("NOPROV"),
            detail="everybody was marked reminded and nobody was emailed")
    s.check("so a later run still finds them",
            any("noprov" in e["to"] for _r, e in [(None, x) for x in _run()[1]]),
            detail="the guest fell through the gap between the two runs")

    s.section("Somebody with no address is skipped, not crashed on")
    _cleanup()
    _booking("NOMAIL", 3, total=900.0, paid=0.0, email="")
    result4, sent4 = _run()
    s.check("no email is attempted", not sent4, detail=f"{sent4}")
    s.check("and the job still returns cleanly", isinstance(result4, str))

    s.section("The window is the setting, not a guess")
    _cleanup()
    _booking("EDGE", 10, total=900.0, paid=0.0)
    _r5, sent5 = _run(days_before=7)
    s.check("ten days out is beyond a seven-day window", not sent5, detail=f"{sent5}")
    _r6, sent6 = _run(days_before=14)
    s.check("and inside a fourteen-day one", len(sent6) == 1, detail=f"{sent6}")

    s.section("It is switchable, and labelled where the owner looks")
    s.check("the setting exists",
            "automation_room_balance_reminder_enabled" in m.AUTOMATION_SETTING_DEFAULTS)
    s.check("with a days-before setting",
            "automation_room_balance_reminder_days_before" in m.AUTOMATION_SETTING_DEFAULTS)
    s.check("and a label on the Automation page",
            "room_balance_reminder" in m.AUTOMATION_JOB_LABELS,
            detail="a job nobody can see is a job nobody can turn off")

    _cleanup()
    return s
