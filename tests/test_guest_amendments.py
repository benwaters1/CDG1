"""A guest changing their own dinner or event, instead of emailing about it.

Both manage pages have shipped a "Change your reservation" form for several
handovers — party size, dietary notes, guest count, a message — posting
`action="update"`. Nothing handled that action. The guest filled the form in,
pressed Save, and the page came back unchanged with no message at all. A form
that silently discards what somebody typed is worse than no form: they believe
they have told you.

What the wiring has to get right, and what this file is mostly about:

  - CAPACITY. The amendment uses the booking form's rule, not a second one:
    at most the room's capacity, at most what is left that night. `exclude_id`
    takes this booking's own covers out of "what is left", or going from two to
    three would be refused by the two already counted.

  - THE PRICE FOLLOWS THE COVERS, through compute_restaurant_total — the same
    helper the booking used. But when no per-person rate is on file for that
    date, that helper returns 0, and recalculating from it would rewrite a real
    €130 total to zero and tell the guest their dinner is free. That is checked
    explicitly because it is the state the live restaurant is actually in.

  - MONEY NEVER MOVES. A smaller party does not trigger a refund. Refunds are
    a decision somebody makes, not a consequence of a form.

  - An event quote is NOT recalculated from a head count. A marquee is not a
    linear function of guests, so the count changes and the owner is told the
    quote needs revisiting.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZAMEND"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("UPDATE restaurant_settings SET price_per_person = NULL WHERE id = 1")
    conn.commit()
    conn.close()


def _rate(value):
    conn = db()
    conn.execute("UPDATE restaurant_settings SET price_per_person = ? WHERE id = 1", (value,))
    conn.commit()
    conn.close()


def _capacity():
    conn = db()
    try:
        row = conn.execute("SELECT capacity FROM restaurant_settings WHERE id = 1").fetchone()
        return row["capacity"] if row else 20
    finally:
        conn.close()


def _dinner(ref, party=2, total=130.0, status="confirmed", days=30, paid="unpaid"):
    conn = db()
    when = (house_today() + timedelta(days=days)).isoformat()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, total_price, payment_status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}@example.invalid", party, when, status, total, paid,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM restaurant_bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _event(ref, count=40, quote=None, status="quoted"):
    conn = db()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, guest_count, status, quoted_price,
           preferred_date, message, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, ?, ?, ?, 'first note', ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}@example.invalid", count, status, quote,
         (house_today() + timedelta(days=200)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _read(table, ref):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE reference_code = ?",
                            (f"{TAG}-{ref}",)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Guest amendments")
    _cleanup()
    # clients() is what creates the owner. Without it owner_email() is None and
    # the notification branch is correctly skipped — which reads as "the email
    # is broken" when it is the fixture that is missing.
    clients()
    sent = []
    was_send = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append((to, subj, body)), True)[1]
    anon = m.app.test_client()
    try:
        # ------------------------------------------------------ the dinner
        s.section("The form that used to do nothing now saves")
        b = _dinner("A", party=2)
        r = anon.post(f"/restaurant/manage/{b['manage_token']}",
                      data={"action": "update", "party_size": "4",
                            "dietary_notes": "no shellfish"}, follow_redirects=True)
        after = _read("restaurant_bookings", "A")
        s.check("the page comes back", r.status_code == 200, r)
        s.check("the covers changed", after["party_size"] == 4,
                detail=f"got {after['party_size']}")
        s.check("and the notes with them", after["dietary_notes"] == "no shellfish",
                detail=f"got {after['dietary_notes']!r}")
        s.check("and the guest is told it worked",
                any("updated" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:2]} — a silent save is why this was "
                       "reported as broken in the first place")

        s.section("The château hears about it")
        s.check("an email goes to the owner",
                any("changed" in subj.lower() for _to, subj, _b in sent),
                detail=f"{[x[1] for x in sent]}")
        s.check("and it says what actually changed",
                any("2" in body and "4" in body for _t, _s2, body in sent))

        s.section("With no per-person rate, a real total is not zeroed")
        # The live state. compute_restaurant_total returns 0 with no rate, and
        # writing that back would tell the guest their dinner is free.
        b = _dinner("B", party=2, total=130.0)
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "update", "party_size": "5"}, follow_redirects=True)
        after = _read("restaurant_bookings", "B")
        s.check("the covers still change", after["party_size"] == 5)
        s.check("but the total is left alone", abs(after["total_price"] - 130.0) < 0.01,
                detail=f"got {after['total_price']} — a real total was rewritten "
                       "to a figure nobody can derive")

        s.section("With a rate, the total follows the covers")
        _rate(65.0)
        b = _dinner("C", party=2, total=130.0)
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "update", "party_size": "4"}, follow_redirects=True)
        after = _read("restaurant_bookings", "C")
        s.check("four at 65 is 260", abs(after["total_price"] - 260.0) < 0.01,
                detail=f"got {after['total_price']}")

        s.section("Shrinking the party does not refund anybody")
        b = _dinner("D", party=6, total=390.0, paid="paid")
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "update", "party_size": "2"}, follow_redirects=True)
        after = _read("restaurant_bookings", "D")
        s.check("the total comes down", abs(after["total_price"] - 130.0) < 0.01,
                detail=f"got {after['total_price']}")
        s.check("but it is still marked paid", after["payment_status"] == "paid",
                detail="the payment status was changed by a form")
        s.check("and the owner is told money has not moved",
                any("no money has moved" in body for _t, _s2, body in sent),
                detail="a refund became a consequence of a guest editing a form")

        s.section("Capacity is the booking form's rule, not a second one")
        cap = _capacity()
        b = _dinner("E", party=2)
        r = anon.post(f"/restaurant/manage/{b['manage_token']}",
                      data={"action": "update", "party_size": str(cap + 1)},
                      follow_redirects=True)
        s.check("more than the room seats is refused",
                _read("restaurant_bookings", "E")["party_size"] == 2,
                detail="the dining room was oversold from the guest's own page")
        s.check("and it says how many can be seated",
                any(str(cap) in f for f in flashes(r)), detail=f"{flashes(r)[:2]}")

        s.section("But their own covers do not count against them")
        # exclude_id. Without it, going 2 -> 3 is refused by the 2 already there.
        b = _dinner("F", party=2)
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "update", "party_size": "3"}, follow_redirects=True)
        s.check("two can become three", _read("restaurant_bookings", "F")["party_size"] == 3,
                detail="the booking's own covers were counted as somebody else's")

        s.section("Nonsense is refused, and nothing is written")
        b = _dinner("G", party=4)
        for label, value in (("zero", "0"), ("blank", ""), ("not a number", "four"),
                             ("negative", "-2")):
            anon.post(f"/restaurant/manage/{b['manage_token']}",
                      data={"action": "update", "party_size": value}, follow_redirects=True)
            s.check(f"{label} leaves it at four",
                    _read("restaurant_bookings", "G")["party_size"] == 4,
                    detail=f"party_size={_read('restaurant_bookings','G')['party_size']}")

        s.section("A cancelled or past reservation is closed to changes")
        b = _dinner("H", party=2, status="cancelled")
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "update", "party_size": "6"}, follow_redirects=True)
        s.check("a cancelled booking cannot be amended",
                _read("restaurant_bookings", "H")["party_size"] == 2)
        b = _dinner("I", party=2, days=-5)
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "update", "party_size": "6"}, follow_redirects=True)
        s.check("nor can a dinner that has already happened",
                _read("restaurant_bookings", "I")["party_size"] == 2)

        s.section("Cancelling still works")
        b = _dinner("J", party=2)
        anon.post(f"/restaurant/manage/{b['manage_token']}",
                  data={"action": "cancel"}, follow_redirects=True)
        s.check("the reservation is cancelled",
                _read("restaurant_bookings", "J")["status"] == "cancelled",
                detail="the new branch swallowed the cancel action")

        # ------------------------------------------------------- the event
        s.section("An event's guest count is the guest's to change")
        e = _event("EV1", count=40, quote=None)
        r = anon.post(f"/events/manage/{e['manage_token']}",
                      data={"action": "update", "guest_count": "62",
                            "message": "we have added the evening reception"},
                      follow_redirects=True)
        after = _read("event_inquiries", "EV1")
        s.check("the count changes", after["guest_count"] == 62,
                detail=f"got {after['guest_count']}")
        s.check("and their note with it",
                "evening reception" in (after["message"] or ""),
                detail=f"got {after['message']!r}")
        s.check("and they are told", any("updated" in f.lower() for f in flashes(r)))

        s.section("But the quote is not recalculated from a head count")
        # A marquee is not a linear function of guests. Requoting is the
        # owner's call, and silently scaling their figure would be a lie.
        sent.clear()
        e = _event("EV2", count=40, quote=8000.0)
        anon.post(f"/events/manage/{e['manage_token']}",
                  data={"action": "update", "guest_count": "80"}, follow_redirects=True)
        after = _read("event_inquiries", "EV2")
        s.check("the count doubles", after["guest_count"] == 80)
        s.check("the quote does not", abs((after["quoted_price"] or 0) - 8000.0) < 0.01,
                detail=f"got {after['quoted_price']} — the app invented a price")
        s.check("and the owner is told to requote",
                any("requote" in body.lower() for _t, _s2, body in sent),
                detail=f"{[x[1] for x in sent]}")

        s.section("A closed enquiry is closed")
        e = _event("EV3", count=30, status="declined")
        anon.post(f"/events/manage/{e['manage_token']}",
                  data={"action": "update", "guest_count": "99"}, follow_redirects=True)
        s.check("a declined enquiry cannot be amended",
                _read("event_inquiries", "EV3")["guest_count"] == 30)

        s.section("And an absurd party is a phone call, not a form")
        e = _event("EV4", count=30)
        anon.post(f"/events/manage/{e['manage_token']}",
                  data={"action": "update", "guest_count": "5000"}, follow_redirects=True)
        s.check("five thousand is refused",
                _read("event_inquiries", "EV4")["guest_count"] == 30)

        s.section("A token nobody issued is a 404")
        s.check("dinner", anon.post("/restaurant/manage/nope",
                                    data={"action": "update", "party_size": "2"}).status_code == 404)
        s.check("event", anon.post("/events/manage/nope",
                                  data={"action": "update", "guest_count": "2"}).status_code == 404)

        s.section("The forms are still in the templates")
        # These pages arrive by handover. If a future drop strips the form, the
        # routes keep working and nobody can reach them — so this goes red
        # rather than the feature disappearing quietly.
        import os
        for name, field in (("restaurant_manage.html", "party_size"),
                            ("event_manage.html", "guest_count")):
            path = os.path.join(_harness.ROOT, "templates", name)
            body = open(path, encoding="utf-8", errors="replace").read()
            s.check(f"{name} still posts action=update",
                    'name="action" value="update"' in body,
                    detail="the amendment form has gone from the template")
            s.check(f"{name} still asks for {field}", f'name="{field}"' in body)
    finally:
        m.send_email = was_send
        _cleanup()
    return s
