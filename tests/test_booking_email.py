"""What the guest is told the moment they book, paid and unpaid.

Both paths went through one hardcoded message, and it was written for the
unpaid one. A guest who paid by card got a subject reading "Booking request
received" and an opening line saying their request "is awaiting confirmation"
— with, several lines below, "Total: €900.00 (paid)". Somebody who has just
handed over nine hundred euros reads that as though the payment did not land.

The fix is not to call it confirmed. `bookings.status` defaults to 'pending'
and paying does not change it, so the dates really are still to be confirmed;
claiming otherwise would be the worse error of the two. The paid message leads
with the payment, then says what remains and that nothing is needed from them.

So this file checks both halves and, deliberately, that the paid message does
NOT overclaim — a test that only checked the payment was acknowledged would be
satisfied by a mail promising a confirmation the owner has not given.
"""
from datetime import date, timedelta

from _harness import Suite, db, house_today
import _harness

m = _harness.m
TAG = "ZZBMAIL"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _book(payment_status, price=900.0, offset=400):
    """Make a booking through the real helper and capture the guest's email."""
    sent = []

    def capture(to, subject, body, ics_content=None, ics_filename=None, keep=True):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=offset)
    was_send = m.send_email
    m.send_email = capture
    conn = db()
    try:
        with m.app.test_request_context("/"):
            m.create_booking(
                conn, room, f"{TAG} Guest", f"{TAG.lower()}@example.invalid", "",
                arrival, arrival + timedelta(days=3), 2, None, [],
                payment_status=payment_status, total_price_override=price)
    finally:
        conn.close()
        m.send_email = was_send
    guest = next((e for e in sent
                  if e["to"] == f"{TAG.lower()}@example.invalid"), None)
    return guest


def run():
    s = Suite("Booking email")
    _cleanup()

    s.section("Somebody who has paid is told the money arrived")
    paid = _book("paid", price=900.0)
    s.check("an email goes to the guest", paid is not None)
    if paid:
        subject, body = paid["subject"], paid["body"]
        s.check("the subject does not call it a request",
                "request received" not in subject.lower(),
                detail=f"{subject!r} — they paid, they did not enquire")
        s.check("the subject says the payment landed", "payment received" in subject.lower(),
                detail=f"{subject!r}")
        s.check("and the amount is in the message", "900.00" in body,
                detail="the guest cannot check the figure they were charged")
        s.check("it does not open by calling the booking unconfirmed",
                "awaiting confirmation" not in body.lower(),
                detail="a paying guest is told their booking is unconfirmed")

        s.section("But it does not promise a confirmation nobody has given")
        # status is still 'pending'. Saying "confirmed" would be the worse bug.
        s.check("it does not claim the booking is confirmed",
                "is confirmed" not in body.lower()
                and "booking confirmed" not in body.lower(),
                detail=f"the owner has not confirmed it yet — {body[:120]!r}")
        s.check("it says the dates are still being confirmed",
                "confirming the dates" in body.lower(),
                detail="the guest is not told what is still outstanding")
        s.check("and that they need do nothing",
                "nothing further is needed" in body.lower())
        s.check("the reference code is there either way", "Reference code:" in body)

    s.section("Somebody who has not paid gets the request wording")
    unpaid = _book("unpaid", price=750.0, offset=430)
    s.check("an email goes to them too", unpaid is not None)
    if unpaid:
        s.check("the subject calls it a request",
                "request received" in unpaid["subject"].lower(),
                detail=f"{unpaid['subject']!r}")
        s.check("and it says it awaits confirmation",
                "awaiting confirmation" in unpaid["body"].lower(),
                detail=f"{unpaid['body'][:120]!r}")
        s.check("with no claim that anything was paid",
                "payment of" not in unpaid["body"].lower(),
                detail="an unpaid booking was acknowledged as paid")

    s.section("The two are actually different messages")
    # If the branch is ever collapsed back to one message this is what catches
    # it, whichever wording survives.
    if paid and unpaid:
        s.check("the subjects differ", paid["subject"] != unpaid["subject"],
                detail=f"both read {paid['subject']!r}")
        s.check("and so do the bodies",
                paid["body"].split("\n\n")[1] != unpaid["body"].split("\n\n")[1],
                detail="the opening line is the same for paid and unpaid")

    s.section("The stay details are on both")
    for label, mail in (("paid", paid), ("unpaid", unpaid)):
        if not mail:
            continue
        s.check(f"{label}: the arrival date is there", "Arrival:" in mail["body"])
        s.check(f"{label}: and a way back into the booking",
                "manage" in mail["body"].lower() or "check in" in mail["body"].lower())

    _cleanup()
    return s
