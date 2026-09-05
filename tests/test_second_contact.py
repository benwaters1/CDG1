"""A second person to write to, copied on every letter about the stay.

A stay had exactly one address on it, and the house has never worked that way.
A daughter books the room and her father is paying; a company books three rooms
and the assistant who arranged it wants to see what we sent; an agent books on
behalf of a client. The payer already had somewhere to go -- booked_by, which
REPLACES the guest on the money side. Nobody had anywhere to be COPIED.

What the house did instead was forward the confirmation by hand, which means
the second person saw the confirmation and not the change of dates. That is
worse than never copying them: they believe they are up to date.

  ONE RESOLVER, NOT A LINE AT EVERY SEND SITE. Adding an address to each send
  is adding it to each send you REMEMBER, and the bill, the confirmation, the
  decline, the receipt and the two balance letters are written in five
  different places. write_about_stay resolves the recipients once and every
  letter about a stay goes through it.

  THE COPY IS A COPY, NOT A REDIRECTION. The guest still gets everything.
  Whether the guest got it is what write_about_stay returns, because the
  "sent" or "held" the page flashes is a sentence about the guest.

  AND THE TWO SIDES STILL POINT DIFFERENT WAYS. The bill goes to the payer and
  the arrival details go to whoever is sleeping here -- the one thing worse
  than sending the door code to the wrong person is sending it to two wrong
  people. Copying the second contact does not flatten that.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZSC"
GUEST = "zzsc.guest@example.invalid"
COPY = "zzsc.assistant@example.invalid"
PAYER = "zzsc.payer@example.invalid"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_messages WHERE to_address LIKE 'zzsc.%'")
    conn.commit()
    conn.close()


def _row(booking_id):
    conn = db()
    try:
        return conn.execute(
            """SELECT bookings.*, rooms.name AS room_name FROM bookings
                 JOIN rooms ON rooms.id = bookings.room_id
                WHERE bookings.id = ?""", (booking_id,)).fetchone()
    finally:
        conn.close()


def _set(booking_id, **cols):
    conn = db()
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"UPDATE bookings SET {sets} WHERE id = ?",
                 tuple(cols.values()) + (booking_id,))
    conn.commit()
    conn.close()


def run():
    s = Suite("A second person to write to")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()

    arrival = m.house_today() + timedelta(days=140)
    departure = arrival + timedelta(days=2)
    sent = []
    was_email = m.send_email
    # Every send recorded rather than made. The point of this suite is WHO was
    # written to, which is exactly what the outbox cannot tell you once a real
    # send has failed and been queued.
    m.send_email = lambda to, subj, body, **k: (sent.append((to, subj, body)), True)[1]
    try:
        with m.app.test_request_context("/"):
            ref, token = m.create_booking(
                conn, room, f"{TAG} Guest", GUEST, "", arrival, departure, 2,
                "", [], payment_status="unpaid")
        conn.commit()
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                           (ref,)).fetchone()["id"]
        conn.close()

        s.section("With nobody else on it, nothing changes")
        b = _row(bid)
        s.check("one recipient", m.stay_recipients(b) == [GUEST],
                detail=str(m.stay_recipients(b)))
        s.check("and the same one for the bill",
                m.stay_recipients(b, side="bill") == [GUEST])
        s.check("and a second contact is nobody yet", m.second_contact(b) == "")

        s.section("A second address is added")
        _set(bid, second_contact_name=TAG + " Assistant", second_contact_email=COPY)
        b = _row(bid)
        s.check("the guest is still first",
                m.stay_recipients(b)[0] == GUEST,
                detail="a copy that displaces the guest is not a copy")
        s.check("and the second person is on it",
                m.stay_recipients(b) == [GUEST, COPY],
                detail=str(m.stay_recipients(b)))
        s.check("on the money side as well",
                m.stay_recipients(b, side="bill") == [GUEST, COPY],
                detail="the assistant who arranged it wants the bill too")

        s.section("The same address twice is one letter, not two")
        _set(bid, second_contact_email=GUEST.upper())
        b = _row(bid)
        s.check("counted once, whatever the case",
                m.stay_recipients(b) == [GUEST],
                detail=str(m.stay_recipients(b)))
        _set(bid, second_contact_email=COPY)

        s.section("Every letter about the stay carries the copy")
        # THE WHOLE POINT. Each of these is written in a different place in
        # app.py, and adding an address to each is adding it to each you
        # remember. A letter missing from this list is the failure this
        # feature exists to prevent.
        b = _row(bid)
        conn = db()
        with m.app.test_request_context("/"):
            del sent[:]
            m.confirm_booking_by_id(conn, bid)
            confirmed = [to for to, _s, _b in sent]
        conn.close()
        s.check("the confirmation goes to both",
                sorted(confirmed) == sorted([GUEST, COPY]),
                detail=str(confirmed))

        conn = db()
        with m.app.test_request_context("/"):
            del sent[:]
            bill = m.booking_bill(conn, bid)
            subject, body = m.balance_request_email(_row(bid), bill, departed=False)
            m.write_about_stay(_row(bid), subject, body, side="bill")
            asked = [to for to, _s, _b in sent]
        conn.close()
        s.check("and so does the letter asking for the balance",
                sorted(asked) == sorted([GUEST, COPY]), detail=str(asked))

        _set(bid, status="pending")
        conn = db()
        with m.app.test_request_context("/"):
            del sent[:]
            m.decline_booking_by_id(conn, bid)
            declined = [to for to, _s, _b in sent]
        conn.close()
        s.check("and the refusal, which is the one nobody thinks of",
                sorted(declined) == sorted([GUEST, COPY]), detail=str(declined))
        _set(bid, status="confirmed")

        s.section("The copy says why it arrived")
        del sent[:]
        with m.app.test_request_context("/"):
            m.write_about_stay(_row(bid), "Your stay", "Come any time after two.")
        bodies = {to: body for to, _s, body in sent}
        s.check("the guest's letter is unchanged",
                bodies.get(GUEST) == "Come any time after two.",
                detail=repr(bodies.get(GUEST)))
        s.check("and the copy explains itself",
                "copied on this because" in (bodies.get(COPY) or "")
                and TAG in (bodies.get(COPY) or ""),
                detail="a bill in a stranger's inbox with no explanation reads "
                       "as a scam: " + repr(bodies.get(COPY)))
        s.check("and says how to stop it",
                "rather we did not" in (bodies.get(COPY) or ""))

        s.section("And the copy is filed against the stay, not thrown away")
        # write_guest_messages asks booking_for_contact which stay a message
        # belongs to, and drops anything that answers nothing as staff mail.
        # The second contact is on no booking of their own, so every copy the
        # house sent them was being discarded -- and the correspondence page
        # for the stay would say we had never written to them.
        conn = db()
        try:
            s.check("a message to the copy knows which stay it is about",
                    m.booking_for_contact(conn, address=COPY.upper()) == bid,
                    detail=str(m.booking_for_contact(conn, address=COPY)))
        finally:
            conn.close()
        m.send_email = was_email
        with m.app.test_client() as probe:
            with probe.session_transaction():
                pass
        with m.app.test_request_context("/"):
            m.write_about_stay(_row(bid), "Filed copy", "For the record.")
            m.close_open_connections(None)
        conn = db()
        try:
            filed = conn.execute(
                """SELECT to_address FROM guest_messages
                    WHERE booking_id = ? AND subject = 'Filed copy'""",
                (bid,)).fetchall()
        finally:
            conn.close()
        s.check("so the stay's own correspondence shows both letters",
                sorted(r["to_address"] for r in filed) == sorted([GUEST, COPY]),
                detail=str([r["to_address"] for r in filed]))
        m.send_email = lambda to, subj, body, **k: (sent.append((to, subj, body)), True)[1]

        s.section("The bill and the arrival details still point different ways")
        _set(bid, booked_by_name=TAG + " Payer", booked_by_email=PAYER)
        b = _row(bid)
        s.check("money goes to the payer first",
                m.stay_recipients(b, side="bill")[0] == PAYER,
                detail=str(m.stay_recipients(b, side="bill")))
        s.check("the door code goes to the guest first",
                m.stay_recipients(b)[0] == GUEST,
                detail=str(m.stay_recipients(b)))
        s.check("and the second person is copied on both",
                COPY in m.stay_recipients(b, side="bill")
                and COPY in m.stay_recipients(b))
        s.check("three people, not four",
                len(m.stay_recipients(b, side="bill")) == 2,
                detail="the payer replaces the guest on the money side; it "
                       "does not add to them")
        _set(bid, booked_by_name=None, booked_by_email=None)

        s.section("What it says happened is about the guest")
        # The page flashes "sent" or "held" off this, and that sentence is
        # about the guest -- a copy that got through does not make it true.
        m.send_email = lambda to, subj, body, **k: to != GUEST
        with m.app.test_request_context("/"):
            told = m.write_about_stay(_row(bid), "Your stay", "Two o'clock.")
        s.check("the guest's letter failing is a failure",
                told is False,
                detail="the copy went; the guest is the one the flash is about")
        m.send_email = lambda to, subj, body, **k: (sent.append((to, subj, body)), True)[1]

        s.section("The owner can set one, and unset it")
        del sent[:]
        page = oc.get(f"/admin/bookings/{bid}/edit").get_data(as_text=True)
        s.check("the field is on the page", 'name="second_contact_email"' in page)
        form = {
            "arrival_date": arrival.isoformat(),
            "departure_date": departure.isoformat(),
            "party_size": "2", "guests_under_18": "0",
            "second_contact_name": TAG + " New Assistant",
            "second_contact_email": "ZZSC.New@Example.Invalid",
        }
        oc.post(f"/admin/bookings/{bid}/edit", data=form, follow_redirects=True)
        s.check("saving one keeps it, lowercased",
                _row(bid)["second_contact_email"] == "zzsc.new@example.invalid",
                detail=repr(_row(bid)["second_contact_email"]))
        form["second_contact_email"] = ""
        form["second_contact_name"] = TAG + " Nowhere"
        oc.post(f"/admin/bookings/{bid}/edit", data=form, follow_redirects=True)
        after = _row(bid)
        s.check("a name with nowhere to write to is nobody",
                not after["second_contact_email"] and not after["second_contact_name"],
                detail="leaving the name implies somebody is copied when "
                       "nobody is: " + repr(after["second_contact_name"]))
    finally:
        m.send_email = was_email
        _cleanup()
    return s
