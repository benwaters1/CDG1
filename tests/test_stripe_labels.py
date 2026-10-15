# -*- coding: utf-8 -*-
"""Every card payment says in Stripe what it was for.

The owner, 15 October 2026, after the first live payments (handover z17, B):
Stripe's list of payments showed nothing but "pi_3UQrju...". The old system
wrote "Chateau de Gudanes - Stay Overnight #248 (Nov 12 - 13, 2026)", so a
payment could be matched to a stay by reading the list. Ours set the line
item's name -- which Stripe shows on the receipt and the card page -- and
nothing on the payment itself, which is what the dashboard, the payout
report and a dispute all show.

So every Checkout Session now carries payment_intent_data with a
description (what, when, the reference) and the same facts as metadata,
made in one place, stripe_payment_label. A new room booking and a new dinner
have no reference until the webhook writes them, so the reference is made
before the card page instead and the webhook keeps it -- otherwise the one
payment a guest would quote a reference about is the one that has none.

Nothing here reaches Stripe: test_checkout_starts's stand-in records what
each Session.create was handed and refuses anything it did not expect.
"""
import re
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room, free_window, ROOT
import _harness
from test_checkout_starts import _CardMachine

m = _harness.m
TAG = "ZZLABEL"
GUEST_IP = "203.0.113.61"


def _label(card):
    """The payment_intent_data of the last checkout, or {}."""
    return (card.sessions[-1].get("payment_intent_data") or {}) if card.sessions else {}


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE ip_address = ?", (GUEST_IP,))
    conn.commit()


def run():
    s = Suite("Every card payment says what it was for")
    _oc, _ec, _o, _e = clients()
    conn = db()
    _cleanup(conn)

    s.section("No checkout is started without a label")
    src = open(f"{ROOT}/app.py", encoding="utf-8").read()
    calls, bare = 0, []
    for hit in re.finditer(r"stripe\.checkout\.Session\.create\(", src):
        depth, i = 1, hit.end()
        while depth and i < len(src):
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        calls += 1
        if "payment_intent_data" not in src[hit.end():i]:
            bare.append(src.count("\n", 0, hit.start()) + 1)
    s.check("every Checkout Session in app.py is handed payment_intent_data",
            calls >= 7 and not bare,
            detail=f"{calls} call(s); without a label at line(s) {bare}")

    s.section("The label itself")
    with m.app.test_request_context("/"):
        lab = m.stripe_payment_label("The Twin Room", "2027-03-09", "2027-03-10",
                                     reference="GUD-ABC123", kind="room", part="deposit")
    s.check("it says what, when, and the reference",
            lab["description"] == "The Twin Room · 9 – 10 March 2027 · Ref GUD-ABC123 · deposit",
            detail=lab["description"])
    s.check("and the same facts are metadata, as Stripe takes them",
            lab["metadata"].get("reference") == "GUD-ABC123" and lab["metadata"].get("kind") == "room"
            and all(isinstance(v, str) and len(v) <= 500 for v in lab["metadata"].values())
            and all(len(k) <= 40 for k in lab["metadata"]),
            detail=str(lab["metadata"]))

    room = ensure_room()
    full = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    day = free_window(room["id"], 2, after_days=640)
    leave = day + timedelta(days=2)
    guest = m.app.test_client()
    guest.environ_base["REMOTE_ADDR"] = GUEST_IP
    try:
        s.section("A new stay, paid at the card page")
        with _CardMachine() as card:
            r = guest.post(f"/book/{room['id']}", data={
                "arrival_date": day.isoformat(), "departure_date": leave.isoformat(),
                "guest_name": TAG + " Guest", "guest_email": "zzlabel@example.invalid",
                "guest_phone": "+33 6 00 00 00 00", "adults": "2", "party_size": "2",
                "agree_terms": "on", "guest_address": "1 Rue de l'Église",
                "guest_city": "Château-Verdun", "guest_postcode": "09310", "guest_country": "FR",
                "estimated_arrival_time": "16:00",
            }, follow_redirects=False)
        lab = _label(card)
        meta = card.sessions[-1]["metadata"] if card.sessions else {}
        ref = meta.get("reference_code") or ""
        s.check("the guest is sent to the card page", r.status_code in (302, 303) and card.sessions,
                detail=f"{r.status_code}, {len(card.sessions)} session(s)")
        s.check("a reference is made before the card page, and carried in the metadata",
                ref.startswith(m.REFERENCE_PREFIXES["room"]), detail=ref or "none")
        with m.app.test_request_context("/"):
            dates = m.format_date_range(day.isoformat(), leave.isoformat())
        s.check("the payment says the room, the nights and that reference",
                lab.get("description", "").startswith(f"{full['name']} · {dates} · Ref {ref}"),
                detail=lab.get("description"))

        # One euro received, so the stay still owes most of it and a part
        # payment below is a part.
        session = {"id": f"cs_test_{TAG}_1", "payment_intent": f"pi_test_{TAG}",
                   "amount_total": 100, "metadata": meta}
        with m.app.test_request_context("/"):
            m.create_booking_from_stripe_session(conn, session)
        conn.commit()
        made = conn.execute("SELECT reference_code FROM bookings WHERE stripe_session_id = ?",
                            (session["id"],)).fetchone()
        s.check("and the booking the webhook writes keeps it",
                bool(made) and made["reference_code"] == ref,
                detail=str(made and made["reference_code"]))

        s.section("A reference already taken is not reused")
        clash = dict(meta, guest_name=TAG + " Second")
        with m.app.test_request_context("/"):
            m.create_booking_from_stripe_session(
                conn, {"id": f"cs_test_{TAG}_2", "payment_intent": f"pi_test_{TAG}2",
                       "amount_total": 100, "metadata": clash})
        conn.commit()
        second = conn.execute("SELECT reference_code FROM bookings WHERE stripe_session_id = ?",
                              (f"cs_test_{TAG}_2",)).fetchone()
        s.check("a second booking is given a reference of its own",
                bool(second) and second["reference_code"] != ref
                and second["reference_code"].startswith(m.REFERENCE_PREFIXES["room"]),
                detail=str(second and second["reference_code"]))

        s.section("Paying what is left on a stay")
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (ref,)).fetchone()
        if bid:
            with _CardMachine() as card, m.app.test_request_context("/"):
                m.start_booking_stripe_payment(conn, bid["id"], amount=20)
            desc = _label(card).get("description", "")
            s.check("a part payment says the stay, its reference and that it is a part",
                    desc.startswith(f"{full['name']} · {dates} · Ref {ref}") and desc.endswith("part payment"),
                    detail=desc)

            s.section("One traveller's share")
            share = {"id": 0, "booking_id": bid["id"], "amount": 30, "name": "Ana",
                     "email": "zzlabel.ana@example.invalid", "token": TAG.lower() + "share"}
            booking = conn.execute(
                "SELECT bookings.*, rooms.name AS room_name FROM bookings JOIN rooms "
                "ON rooms.id = bookings.room_id WHERE bookings.id = ?", (bid["id"],)).fetchone()
            with _CardMachine() as card, m.app.test_request_context("/"):
                m.start_share_payment(conn, share, booking)
            desc = _label(card).get("description", "")
            s.check("a share says whose it is", desc.endswith("Ana's share") and f"Ref {ref}" in desc,
                    detail=desc)

        s.section("An event")
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
                   contact_email, preferred_date, end_date, guest_count, status, quoted_price, created_at)
               VALUES (?, ?, 'Wedding', ?, 'zzlabel.e@example.invalid', ?, ?, 80, 'confirmed', 9000, ?)""",
            ("EVT-" + TAG, TAG.lower() + "evt", TAG + " Couple", day.isoformat(),
             (day + timedelta(days=1)).isoformat(), _harness.datetime_now()))
        conn.commit()
        eid = conn.execute("SELECT id FROM event_inquiries WHERE manage_token = ?",
                           (TAG.lower() + "evt",)).fetchone()["id"]
        with _CardMachine() as card, m.app.test_request_context("/"):
            m.start_event_stripe_payment(conn, eid, amount=500)
        desc = _label(card).get("description", "")
        s.check("an event says what it is, its days and its reference",
                desc.startswith("Wedding · ") and "Ref EVT-" + TAG in desc and desc.endswith("part payment"),
                detail=desc)

        s.section("A workshop deposit, which also keeps the card")
        sess = conn.execute(
            """SELECT workshop_sessions.*, workshops.title FROM workshop_sessions
               JOIN workshops ON workshops.id = workshop_sessions.workshop_id
               ORDER BY workshop_sessions.id DESC LIMIT 1""").fetchone()
        if sess:
            conn.execute(
                # A Customer already on file, so the card-keeping branch is
                # taken without this file's stand-in having to be one.
                """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
                       guest_email, party_size, status, total_price, deposit_amount, created_at,
                       stripe_customer_id)
                   VALUES (?, ?, ?, ?, 'zzlabel.w@example.invalid', 1, 'confirmed', 600, 150, ?,
                           'cus_test_ZZLABEL')""",
                (sess["id"], "WRK-" + TAG, TAG.lower() + "ws", TAG + " Maker", _harness.datetime_now()))
            conn.commit()
            wid = conn.execute("SELECT id FROM workshop_bookings WHERE manage_token = ?",
                               (TAG.lower() + "ws",)).fetchone()["id"]
            with _CardMachine() as card, m.app.test_request_context("/"):
                m.start_workshop_stripe_payment(conn, wid, "deposit")
            pid = _label(card)
            s.check("it says the workshop, its dates and the reference",
                    pid.get("description", "").startswith(sess["title"] + " · ")
                    and "Ref WRK-" + TAG in pid.get("description", ""),
                    detail=pid.get("description"))
            s.check("and still asks Stripe to keep the card for the balance",
                    pid.get("setup_future_usage") == "off_session", detail=str(pid))
            conn.execute("DELETE FROM workshop_transactions WHERE workshop_booking_id = ?", (wid,))
            conn.execute("DELETE FROM workshop_bookings WHERE id = ?", (wid,))
            conn.commit()
        else:
            s.check("a workshop sitting exists to register onto", False,
                    detail="reported rather than skipped")

        s.section("A bill at the till, paid on the guest's phone")
        with m.app.test_request_context("/"):
            one_day = m.format_date_range(day.isoformat(), day.isoformat())
        now = _harness.datetime_now()
        conn.execute("INSERT INTO pos_orders (table_label, covers, status, opened_at, service_date) "
                     "VALUES (?, 2, 'open', ?, ?)", (TAG + "7", now, day.isoformat()))
        conn.commit()
        tab = conn.execute("SELECT id FROM pos_orders WHERE table_label = ?", (TAG + "7",)).fetchone()["id"]
        conn.execute("INSERT INTO pos_order_lines (order_id, name, unit_price, quantity, created_at) "
                     "VALUES (?, ?, 40.0, 2, ?)", (tab, TAG + " dish", now))
        conn.commit()
        with _CardMachine() as card:
            _oc.post(f"/pos/{tab}/pay-link", follow_redirects=False)
        lab = _label(card)
        s.check("it says the table and the service day",
                lab.get("description", "").startswith(f"Bill for table {TAG}7 · {one_day} · ")
                and lab.get("metadata", {}).get("pos_order_id") == str(tab),
                detail=str(lab))

        s.section("A dinner, paid when it is reserved")
        put_back = _harness.keep_rows(conn, "restaurant_settings", "id = 1")
        put_mode = _harness.keep_rows(conn, "app_settings", "key = 'dining_mode'")
        try:
            conn.execute("UPDATE restaurant_settings SET enabled = 1, price_per_person = 55, "
                         "deposit_percent = NULL, opening_date = NULL WHERE id = 1")
            conn.execute("DELETE FROM app_settings WHERE key = 'dining_mode'")
            conn.commit()
            with _CardMachine() as card:
                r = guest.post("/restaurant/book", data={
                    "guest_name": TAG + " Diner", "guest_email": "zzlabel.d@example.invalid",
                    "guest_phone": "+33 6 00 00 00 01", "dinner_date": day.isoformat(),
                    "party_size": "2", "agree_terms": "on"}, follow_redirects=False)
            lab, meta = _label(card), (card.sessions[-1]["metadata"] if card.sessions else {})
            ref = meta.get("reference_code") or ""
            s.check("it is sent to the card page with a reference already made",
                    bool(card.sessions) and ref.startswith(m.REFERENCE_PREFIXES["restaurant"]),
                    detail=f"{r.status_code}; " + "; ".join(_harness.flashes(r)[:2]))
            s.check("and the payment says the dinner, the day and that reference",
                    lab.get("description", "") == f"Dinner for 2 · {one_day} · Ref {ref}",
                    detail=lab.get("description"))
            if card.sessions:
                with m.app.test_request_context("/"):
                    m.create_restaurant_booking_from_stripe_session(
                        conn, {"id": f"cs_test_{TAG}_d", "payment_intent": f"pi_test_{TAG}d",
                               "amount_total": card.cents(), "metadata": meta})
                conn.commit()
                made = conn.execute("SELECT reference_code FROM restaurant_bookings "
                                    "WHERE stripe_session_id = ?", (f"cs_test_{TAG}_d",)).fetchone()
                s.check("and the reservation the webhook writes keeps it",
                        bool(made) and made["reference_code"] == ref,
                        detail=str(made and made["reference_code"]))
        finally:
            put_back()
            put_mode()
    finally:
        tabs = [r["id"] for r in conn.execute("SELECT id FROM pos_orders WHERE table_label LIKE ?",
                                              (TAG + "%",))]
        for t in tabs:
            conn.execute("DELETE FROM pos_order_lines WHERE order_id = ?", (t,))
            conn.execute("DELETE FROM pos_payments WHERE order_id = ?", (t,))
        conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
        _cleanup(conn)
        conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
