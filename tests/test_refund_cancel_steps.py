# -*- coding: utf-8 -*-
"""Refund, then cancel, then choose whether to write (owner, 26 October 2026).

  1. Refund: by card through Stripe (sends the money), or record a refund made
     somewhere else -- the Stripe dashboard, RetreatGuru, the bank -- with
     where and its reference, and nothing moves. Tess Hoffman's EUR 64.50
     (GUD-JWPAWW) went back in the Stripe dashboard and must be recorded
     without refunding her twice.
  2. Cancel: a confirmation step that says the room goes back on sale and no
     money moves, with "Email the guest a cancellation confirmation" unticked
     and a preview of exactly what would go.
  3. The cancellation letter knows the money: refunded in full (how much, how,
     roughly when), in part (refunded and kept), or not at all (the terms).

Nothing is emailed unless a box is ticked; and a ticked box goes even while
Guest emails is off, because a person chose that letter after reading it.
The real gate is used here, with the mail transport stood in for.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRC"


def _cleanup():
    conn = db()
    stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", ("zzrc%",))
    conn.commit()
    conn.close()


def _stay(ref, paid, method="other"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    arrival = house_today() + timedelta(days=70)
    departure = arrival + timedelta(days=1)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, payment_status, total_price,
           amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'confirmed', 'unpaid', 215, ?, ?)""",
        (room, f"{TAG}{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} Guest {ref}",
         f"zzrc{ref.lower()}@example.invalid", arrival.isoformat(), departure.isoformat(),
         paid, datetime.now(timezone.utc).isoformat()))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    m.stamp_room_total(conn, bid, 215.0, arrival.isoformat(), departure.isoformat())
    if paid:
        conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                        VALUES (?, ?, ?, ?)""",
                     (bid, paid, method, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _refunds(bid):
    conn = db()
    rows = conn.execute("SELECT * FROM refunds WHERE category = 'room' AND booking_id = ?",
                        (bid,)).fetchall()
    conn.close()
    return rows


def _status(bid):
    conn = db()
    st = conn.execute("SELECT status FROM bookings WHERE id = ?", (bid,)).fetchone()["status"]
    conn.close()
    return st


def run():
    s = Suite("Refund, cancel, and the choice to write")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    sent = []
    conn = db()
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'guest_emails_on'").fetchone()
    was_on = row["value"] if row else None
    conn.execute("DELETE FROM app_settings WHERE key = 'guest_emails_on'")
    conn.commit()
    conn.close()
    was = (m.send_email_via_resend, m.resend_enabled, m.guest_letter_held_why, m.stripe)
    m.resend_enabled = lambda: True
    m.send_email_via_resend = (
        lambda to, subj, body, ics=None, name=None, html=None, reply_to=None:
        (sent.append({"to": to, "subject": subj, "body": body}), (True, None))[1])
    m.guest_letter_held_why = _harness.REAL_GUEST_LETTER_HELD_WHY

    class _NoStripe:
        def __getattr__(self, name):
            raise AssertionError("Stripe was called: " + name)
    m.stripe = _NoStripe()
    try:
        _checks(s, oc, sent)
    finally:
        m.send_email_via_resend, m.resend_enabled, m.guest_letter_held_why, m.stripe = was
        conn = db()
        if was_on is not None:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('guest_emails_on', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (was_on,))
        conn.commit()
        conn.close()
        _cleanup()
    return s


def _letters_to(sent, b):
    return [x for x in sent if x["to"] == b["guest_email"]]


def _checks(s, oc, sent):
    s.section("The refund form says which option sends money")
    tess = _stay("TESS", 64.50)
    page = oc.get(f"/admin/bookings/{tess['id']}").get_data(as_text=True)
    for label in ("Card refund via Stripe (sends the money)",
                  "Refunded via a third party (record only, no money moves)",
                  "Bank transfer (record only)", "Cash (record only)"):
        s.check(f"offers: {label}", label in page)
    s.check("no vague 'Some other way'", "Some other way" not in page)
    s.check("asks where, from a short list", all(w in page for w in
            ("Stripe dashboard", "RetreatGuru", ">Bank<", ">Other<")))
    box = page[page.find('name="tell_guest"') - 120:page.find('name="tell_guest"') + 160]
    s.check("'Email the guest a refund confirmation', unticked",
            "Email the guest a refund confirmation" in page and "checked" not in box, detail=box)

    s.section("Recording a refund made in the Stripe dashboard moves nothing")
    payments = m.payments_received(m.get_db() if False else db(), "room", tess)
    key = payments[0]["key"]
    r = oc.post(f"/admin/refunds/room/{tess['id']}", data={
        "payment": key, "amount": "64.50", "method": "third_party",
        "reason_code": "goodwill", "back": "booking"}, follow_redirects=True)
    s.check("without saying where, it is refused", not _refunds(tess["id"])
            and any("where" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    del sent[:]
    r = oc.post(f"/admin/refunds/room/{tess['id']}", data={
        "payment": key, "amount": "64.50", "method": "third_party",
        "refunded_via": "stripe_dashboard", "external_reference": "ch_3UNhDw4gymLnXm471WlrewUB",
        "reason_code": "goodwill", "back": "booking"}, follow_redirects=True)
    rows = _refunds(tess["id"])
    s.check("recorded once, as a third-party refund (Stripe was never called)",
            len(rows) == 1 and rows[0]["method"] == "other"
            and rows[0]["refunded_via"] == "stripe_dashboard" and rows[0]["amount"] == 64.50,
            detail=f"{[dict(x) for x in rows]} {flashes(r)}")
    s.check("with its reference", rows and rows[0]["external_reference"]
            == "ch_3UNhDw4gymLnXm471WlrewUB")
    s.check("the flash says no money moved",
            any("no money moved" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and nobody was emailed: the box was not ticked", not _letters_to(sent, tess),
            detail=str(sent))
    page = oc.get(f"/admin/bookings/{tess['id']}").get_data(as_text=True)
    s.check("the booking says how it went back",
            "Refunded via a third party (Stripe dashboard), ref ch_3UNhDw4gymLnXm471WlrewUB" in page)

    s.section("Cancel: a step that says what happens, and sends nothing by itself")
    page = oc.get(f"/admin/bookings/{tess['id']}").get_data(as_text=True)
    s.check("the booking's Cancel goes to the confirmation step",
            f"/admin/bookings/{tess['id']}/cancel/confirm" in page)
    conf = oc.get(f"/admin/bookings/{tess['id']}/cancel/confirm")
    text = conf.get_data(as_text=True)
    s.check("it opens", conf.status_code == 200, response=conf)
    s.check("it says the room goes back on sale and no money moves",
            "room goes back on sale" in text and "No money moves" in text)
    box = text[text.find('name="email_guest"') - 120:text.find('name="email_guest"') + 160]
    s.check("'Email the guest a cancellation confirmation', unticked",
            "Email the guest a cancellation confirmation" in text and "checked" not in box)
    s.check("the preview knows it was all refunded, how much and how",
            "We have refunded the €64.50 you paid to the card you paid with" in text,
            detail=text[text.find("Preview"):text.find("Preview") + 600])
    del sent[:]
    r = oc.post(f"/admin/bookings/{tess['id']}/cancel", data={"cancel_reason": ""},
                follow_redirects=True)
    s.check("cancelled", _status(tess["id"]) == "cancelled", detail=str(flashes(r)))
    s.check("and no email, with the box left alone", not _letters_to(sent, tess),
            detail=str(sent))
    conn = db()
    held = conn.execute("SELECT 1 FROM email_outbox WHERE to_address = ?",
                        (tess["guest_email"],)).fetchone()
    conn.close()
    s.check("not even held to go later", held is None)
    s.check("the flash says no email was sent",
            any("No email was sent" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("and no refund was made by cancelling", len(_refunds(tess["id"])) == 1)

    s.section("Ticked: the letter in the preview goes, word for word, while Guest emails is off")
    part = _stay("PART", 100.0, method="bank_transfer")
    pk = m.payments_received(db(), "room", part)[0]["key"]
    oc.post(f"/admin/refunds/room/{part['id']}", data={
        "payment": pk, "amount": "40", "method": "bank_transfer",
        "reason_code": "goodwill", "back": "booking"})
    text = oc.get(f"/admin/bookings/{part['id']}/cancel/confirm").get_data(as_text=True)
    s.check("part refunded: says refunded and kept",
            "We have refunded €40.00 by bank transfer" in text
            and "the remaining €60.00 is non-refundable and is kept" in text)
    with m.app.test_request_context("/"):
        c = db()
        b = c.execute("""SELECT bookings.*, rooms.name AS room_name FROM bookings
                         JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ?""",
                      (part["id"],)).fetchone()
        preview = m.cancellation_letter(c, b)
        c.close()
    del sent[:]
    with m.app.test_request_context("/"):
        s.check("Guest emails is off", m.guest_emails_on() is False)
    oc.post(f"/admin/bookings/{part['id']}/cancel", data={"email_guest": "1"})
    got = _letters_to(sent, part)
    s.check("the guest is emailed, once", len(got) == 1, detail=str(sent))
    s.check("with exactly the previewed words", got and got[0]["subject"] == preview[0]
            and preview[1] in got[0]["body"], detail=str(got[:1]))

    s.section("Nothing refunded: the letter states the terms")
    kept = _stay("KEPT", 215.0, method="cash")
    text = oc.get(f"/admin/bookings/{kept['id']}/cancel/confirm").get_data(as_text=True)
    s.check("says it is non-refundable under the booking terms, and how much",
            "bookings are non-refundable once confirmed and paid for, so the €215.00 you "
            "paid is not refunded" in text)
    nopay = _stay("NOPAY", 0)
    text = oc.get(f"/admin/bookings/{nopay['id']}/cancel/confirm").get_data(as_text=True)
    s.check("nothing paid: no money sentence at all",
            "refunded" not in text.split("Preview")[-1].lower())

    s.section("A refund letter goes only when ticked, and then even with Guest emails off")
    del sent[:]
    oc.post(f"/admin/refunds/room/{kept['id']}", data={
        "payment": m.payments_received(db(), "room", kept)[0]["key"], "amount": "15",
        "method": "third_party", "refunded_via": "retreatguru", "external_reference": "RG-1",
        "reason_code": "goodwill", "tell_guest": "1", "back": "booking"})
    got = _letters_to(sent, kept)
    s.check("ticked: the refund confirmation goes", len(got) == 1, detail=str(sent))
    s.check("and says how it went back", got and "through RetreatGuru" in got[0]["body"],
            detail=got[0]["body"] if got else "")


if __name__ == "__main__":
    print(run().report())
