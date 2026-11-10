"""A booking's own confirmation flagged as clashing with itself, and €1,400
read as €1.40 (brief of 10 November 2026).

On 9 November, 15:43, Inbox Flags raised "AVAILABILITY CONFLICT … is not free.
Those dates overlap an existing booking" on "Booked — The Twin Room (Ground
Floor) (GUD-…)" and on the Stripe receipt for it. The existing booking was
that booking. And "the revised invoice for €1,400" was read as €1.40.

  AN EMAIL THAT NAMES ITS STAY is checked against that stay: by its reference,
  by the Stripe payment the app holds for it, by its Booking.com reservation
  number, or by an event's reference -- and that stay is never counted
  against itself. Anybody else's booking on those nights still is.

  A FIGURE IS READ AS IT IS WRITTEN: €1,400, 1 400 € and 1.400 € are all
  fourteen hundred; €1,40 is one euro forty.

The same check stops a reply on Send in Outlook, so this is that check too.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOB"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM ota_reservations WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()


def _free_run(conn, room_id, nights, start):
    """The first run of free nights in this room from `start`, a week at a time."""
    day = start
    for _ in range(40):
        ok, _why = m.is_range_available(conn, room_id, day, day + timedelta(days=nights))
        if ok:
            return day
        day += timedelta(days=7)
    return None


def run():
    s = Suite("an email about its own booking")
    clients()
    conn = db()
    _cleanup(conn)
    now_iso = m.datetime.now(m.timezone.utc).isoformat()
    today = m.house_today()

    s.section("A figure is read as it is written")
    for written, meant in (("the revised invoice for €1,400", 1400.0),
                           ("the revised invoice for 1 400 €", 1400.0),
                           ("the revised invoice for 1 400 €", 1400.0),
                           ("the revised invoice for 1 400 €", 1400.0),
                           ("the revised invoice for 1.400 €", 1400.0),
                           ("€1,40 a sachet", 1.40),
                           ("€1998.00", 1998.0), ("€1,998.00", 1998.0),
                           ("1.998,00 €", 1998.0), ("1 400,50 €", 1400.5),
                           ("EUR 12,5", 12.5), ("2.400.000 €", 2400000.0)):
        got = m.extract_money_amounts(written)
        s.check(f"{written!r} is {meant}", got == [meant], detail=str(got))
    s.check("and a bare number is still not a price", m.extract_money_amounts("room 12, the 5th") == [])

    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()
    room, other_room = rooms[0], rooms[1]
    nights = 3
    arrive = _free_run(conn, room["id"], nights, today + timedelta(days=580))
    s.check("a run of free nights to book, far enough ahead to be nobody's",
            arrive is not None)
    if arrive is None:
        conn.close()
        return s
    leave = arrive + timedelta(days=nights)
    ref = m.make_reference_code()
    intent = "pi_" + TAG + "own0000000001"
    booking = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, party_size, status, total_price, deposit_amount,
             stripe_payment_intent_id, created_at)
           VALUES (?, ?, ?, ?, 'zzob.guest@example.invalid', ?, ?, 2, 'confirmed', 999, 300, ?, ?)""",
        (room["id"], ref, f"{TAG}tok", f"{TAG} Guest", arrive.isoformat(), leave.isoformat(),
         intent, now_iso)).lastrowid
    conn.commit()
    stay_words = f"{arrive.isoformat()} to {leave.isoformat()}"

    def check(subject, body, sender="zzob.guest@example.invalid"):
        with m.app.test_request_context("/"):
            return m.guess_email_conflict(conn, sender, subject, body, now_iso)

    def clash(found):
        return bool(found and found["availability_conflict"])

    def wrong_price(found):
        return bool(found and found["price_conflict"])

    s.section("Named by its reference, a booking is not counted against itself")
    own = check(f"Booked — {room['name']} ({ref})", f"{room['name']}, {stay_words}, 2 guests.")
    s.check("the booking's own confirmation does not clash with the booking", not clash(own),
            detail=own and own["note"])
    unnamed = check(f"Booked — {room['name']}", f"{room['name']}, {stay_words}, 2 guests.")
    s.check("while the same words without the reference do -- it is the reference that settles it",
            clash(unnamed), detail=unnamed and unnamed["note"])
    lower = check(f"Re: booking {ref.lower()}", f"{room['name']}, {stay_words}.")
    s.check("a reference typed in lower case is the same reference", not clash(lower),
            detail=lower and lower["note"])
    room_from_booking = check(f"Re: {ref}", f"Can we still come {stay_words}?")
    s.check("an email naming only the reference, with its own nights, has nothing wrong",
            not clash(room_from_booking), detail=str(room_from_booking))
    paid = check(f"Your receipt from the château ({ref})",
                 f"{room['name']}, {stay_words}. Amount paid €999.00")
    s.check("the price the booking was made at is right, whatever today's rate",
            paid and not wrong_price(paid), detail=paid and paid["note"])
    deposit = check(f"Your receipt from the château ({ref})",
                    f"{room['name']}, {stay_words}. Deposit paid €300.00")
    s.check("and so is its deposit", deposit and not wrong_price(deposit), detail=deposit and deposit["note"])
    off = check(f"Your receipt ({ref})", f"{room['name']}, {stay_words}. Amount paid €123.00")
    s.check("but a figure that is none of the booking's is still a wrong price",
            wrong_price(off), detail=str(off))
    moved = check(f"Re: {ref}", f"Could we move to {(arrive + timedelta(days=1)).isoformat()} "
                                f"to {(leave + timedelta(days=1)).isoformat()} in the {room['name']}?")
    s.check("moving a stay a night on is not refused for the nights it already holds",
            not clash(moved), detail=moved and moved["note"])

    s.section("By the Stripe payment the app holds for it")
    receipt = check("Receipt for your payment", f"Payment {intent}: {room['name']}, {stay_words}. €999.00")
    s.check("a receipt naming the payment does not clash with the booking it paid for",
            receipt and not clash(receipt) and not wrong_price(receipt), detail=str(receipt))
    stranger = check("Receipt for your payment",
                     f"Payment pi_{TAG}nobody000000: {room['name']}, {stay_words}. €999.00")
    s.check("a payment the app does not hold names nothing", clash(stranger), detail=str(stranger))

    s.section("Somebody else's booking on those nights still counts")
    rival = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, 'zzob.rival@example.invalid', ?, ?, 2, 'confirmed', 999, ?)""",
        (room["id"], m.make_reference_code(), f"{TAG}tok2", f"{TAG} Rival",
         (leave + timedelta(days=7)).isoformat(), (leave + timedelta(days=10)).isoformat(),
         now_iso)).lastrowid
    conn.commit()
    onto = check(f"Re: {ref}", f"Could we come {(leave + timedelta(days=7)).isoformat()} to "
                               f"{(leave + timedelta(days=10)).isoformat()} in the {room['name']} instead?")
    s.check("asking for another guest's nights, under your own reference, is still a clash",
            clash(onto), detail=onto and onto["note"])
    only_ref = check(f"Re: {ref}", f"Could we come {(leave + timedelta(days=7)).isoformat()} to "
                                   f"{(leave + timedelta(days=10)).isoformat()} instead?")
    s.check("and an email naming only the reference is checked against that booking's room",
            only_ref and only_ref["category"] == f"Room: {room['name']}" and clash(only_ref),
            detail=str(only_ref))

    s.section("By a Booking.com reservation number")
    c_arrive = _free_run(conn, other_room["id"], 2, today + timedelta(days=600))
    number = "9876512340"
    if c_arrive:
        c_leave = c_arrive + timedelta(days=2)
        conn.execute(
            """INSERT INTO ota_reservations (channel, reservation_number, status, guest_name,
                   arrival_date, departure_date, room_id, first_seen_at, last_event_at, updated_at)
               VALUES ('booking.com', ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?)""",
            (number, f"{TAG} Channel Guest", c_arrive.isoformat(), c_leave.isoformat(),
             other_room["id"], now_iso, now_iso, now_iso))
        conn.execute("INSERT INTO blocked_dates (room_id, start_date, end_date) VALUES (?, ?, ?)",
                     (other_room["id"], c_arrive.isoformat(), c_leave.isoformat()))
        conn.commit()
        words = f"{other_room['name']}, {c_arrive.isoformat()} to {c_leave.isoformat()}"
        mine = check(f"New booking {number}", words, sender="zzob.channel@example.invalid")
        theirs = check("New booking", words, sender="zzob.channel@example.invalid")
        s.check("an email naming the reservation is not refused for that reservation's own nights",
                not clash(mine), detail=mine and mine["note"])
        s.check("the same nights without the number are taken", clash(theirs),
                detail=theirs and theirs["note"])
        s.check("and a telephone number is never taken for a reservation",
                clash(check("Call me on 0612345678", words, sender="zzob.channel@example.invalid")))
        conn.execute("DELETE FROM blocked_dates WHERE room_id = ? AND start_date = ? AND end_date = ?",
                     (other_room["id"], c_arrive.isoformat(), c_leave.isoformat()))
        conn.commit()
    else:
        s.check("free nights for the Booking.com case", False)

    s.section("By an event's reference")
    e_day = _free_run(conn, other_room["id"], 1, today + timedelta(days=640))
    if e_day:
        e_ref = m.make_event_reference_code()
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
                 contact_email, preferred_date, guest_count, status, quoted_price, created_at)
               VALUES (?, ?, 'wedding', ?, 'zzob.couple@example.invalid', ?, 60, 'confirmed', 18000, ?)""",
            (e_ref, f"{TAG}etok", f"{TAG} Couple", e_day.isoformat(), now_iso))
        conn.commit()
        words = f"{other_room['name']} for the night, {e_day.isoformat()} to {(e_day + timedelta(days=1)).isoformat()}"
        mine = check(f"Re: our wedding {e_ref}", words, sender="zzob.couple@example.invalid")
        theirs = check("Re: a room", words, sender="zzob.someone@example.invalid")
        s.check("a couple's own wedding does not make their room for that night taken",
                not clash(mine), detail=mine and mine["note"])
        s.check("while anybody else is told the house is held", clash(theirs),
                detail=theirs and theirs["note"])
        quote = check(f"Re: our wedding {e_ref}", "The balance of €18,000 as agreed.",
                      sender="zzob.other.address@example.invalid")
        s.check("and its figure is checked against that event's quote, whoever writes",
                quote and quote["category"] == "Event" and not wrong_price(quote), detail=str(quote))
    else:
        s.check("a free day for the event case", False)

    _cleanup(conn)
    conn.execute("DELETE FROM bookings WHERE id IN (?, ?)", (booking, rival))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
