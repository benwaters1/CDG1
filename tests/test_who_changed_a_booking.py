"""Who did what to a booking, on the booking's own history.

What there was: a stay's history page read the audit trail, and a stay's own
decisions never wrote to it. Confirming, declining, cancelling and editing --
the four things done to a stay most -- left nothing, so "who moved these
dates" had no answer on the page that exists to answer it, while its empty
state said confirmations and declines "all land here". The stale-request
expiry runs on the dashboard, so a line from it would have been put down to
whoever opened it. What a guest did from their own page -- cancel, move the
dates, correct their number -- was nobody's. The one way to the history was
the edit page, which refuses a stay no longer pending or confirmed: the
histories worth reading could not be reached. Ateliers, tables and events had
no history page. Its times were UTC, sliced. And an edit that moved no night
re-priced the stay at the day's rates, so correcting a phone number could
change what the guest owed.

What this holds:

  - Confirming says who; a stay booked or paid for online says it took itself,
    and is the guest's.
  - Declining says who and why, never the note; one the expiry declined says
    so, and is not put down to whoever opened the dashboard.
  - Cancelling says why, at the time, and happens once; the reason decides
    whose held money is; a reason put on afterwards is on the history too.
  - An edit says what moved: by value for the stay, by name only for a
    person's details. The guest is written to only when the dates or the party
    moved. A save that changed nothing writes nothing, and one that moves no
    night keeps the price agreed.
  - Checking out is on it.
  - What the guest does on their own page is theirs.
  - Every stay, whatever became of it, leads to its history, and so does every
    atelier place, table and event.
  - The times are the house's.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZWC"
EMAIL = f"{TAG.lower()}@example.invalid"


def _cleanup():
    conn = db()
    try:
        stays = "(SELECT id FROM bookings WHERE reference_code LIKE ? OR guest_name LIKE ?)"
        like = (TAG + "%", TAG + "%")
        conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", like)
        conn.execute(f"DELETE FROM tasks WHERE booking_id IN {stays}", like)
        conn.execute(f"DELETE FROM guest_messages WHERE booking_id IN {stays}", like)
        conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
        refs = [r[0] for r in conn.execute(
            "SELECT reference_code FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))]
        refs += [r[0] for r in conn.execute(
            "SELECT reference_code FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))]
        refs += [r[0] for r in conn.execute(
            "SELECT reference_code FROM restaurant_bookings WHERE reference_code LIKE ?",
            (TAG + "%",))]
        refs += [r[0] for r in conn.execute(
            "SELECT reference_code FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))]
        for ref in refs:
            conn.execute("DELETE FROM audit_log WHERE target = ? OR target LIKE ?",
                         (ref, f"% {ref}"))
        conn.execute("DELETE FROM audit_log WHERE action = ?", (TAG + "_at_night",))
        conn.execute("DELETE FROM audit_log WHERE details = ?", (TAG + " sitting",))
        conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM guest_messages WHERE LOWER(to_address) LIKE ?",
                     (TAG.lower() + "%",))
        conn.execute("DELETE FROM email_outbox WHERE LOWER(to_address) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
        conn.commit()
    finally:
        conn.close()


def _one(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


class _Stays:
    """Stays for the suite, each on nights of its own so none blocks another."""

    def __init__(self):
        self.room = _harness.ensure_room()
        self.n = 0

    def make(self, name, *, confirm_now=False, payment_status="unpaid"):
        self.n += 1
        # Well past anything real: this runs on a copy of the house's own
        # bookings, and a confirm that meets one of them is refused.
        arrival = house_today() + timedelta(days=900 + 7 * self.n)
        conn = db()
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (self.room["id"],)).fetchone()
        with m.app.test_request_context("/"):
            ref, token = m.create_booking(
                conn, room, f"{TAG} {name}", EMAIL, "", arrival, arrival + timedelta(days=3),
                2, "", [], payment_status=payment_status, confirm_now=confirm_now)
        conn.commit()
        bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (ref,)).fetchone()["id"]
        conn.close()
        return ref, bid, token


def _lines(kind, record_id):
    conn = db()
    try:
        return m.history_for(conn, kind, record_id)["entries"]
    finally:
        conn.close()


def _only(kind, record_id, action):
    return [e for e in _lines(kind, record_id) if e["action"] == action]


def _edit_form(bid, **changes):
    """The edit page's form as it stands for this stay, with `changes` made."""
    b = _one("SELECT * FROM bookings WHERE id = ?", bid)
    form = {"arrival_date": b["arrival_date"], "departure_date": b["departure_date"],
            "party_size": str(b["party_size"]), "guests_under_18": str(b["guests_under_18"] or 0),
            "guest_phone": b["guest_phone"] or "", "special_requests": b["special_requests"] or "",
            "second_contact_name": b["second_contact_name"] or "",
            "second_contact_email": b["second_contact_email"] or "",
            "booked_by_name": b["booked_by_name"] or "", "booked_by_email": b["booked_by_email"] or "",
            "source": b["source"] or "", "heard_via": b["heard_via"] or ""}
    form.update(changes)
    return form


def run():
    s = Suite("Who changed a booking")
    oc, ec, owner, emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, *a, **k: sent.append((to, subject)) or True
    try:
        _run(s, oc, ec, owner, emp, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, ec, owner, emp, sent):
    stays = _Stays()

    s.section("Confirming says who")
    ref, bid, _t = stays.make("Confirmed")
    oc.post(f"/admin/bookings/{bid}/confirm")
    took = _only("booking", bid, "booking_confirmed")
    s.check("confirming is on the stay's history, against who did it",
            len(took) == 1 and took[0]["actor_user_id"] == owner["id"],
            detail=str([dict(e) for e in _lines("booking", bid)]))
    ref2, bid2, _t = stays.make("Bulk")
    oc.post("/admin/bookings/bulk-confirm", data={"booking_ids": [str(bid2)]})
    s.check("and so is confirming several at once",
            len(_only("booking", bid2, "booking_confirmed")) == 1)
    _r, online, _t = stays.make("Online", confirm_now=True, payment_status="paid")
    by_itself = _only("booking", online, "booking_confirmed_online")
    s.check("one paid for online says it took itself, and is the guest's",
            len(by_itself) == 1 and by_itself[0]["actor_user_id"] is None
            and m.audit_who(by_itself[0]) == "The guest"
            and "paid for online" in (by_itself[0]["details"] or ""),
            detail=str([dict(e) for e in by_itself]))

    s.section("Declining says who, and why")
    _r, bid, _t = stays.make("Declined")
    oc.post(f"/admin/bookings/{bid}/decline",
            data={"decline_reason": "dates_gone", "decline_note": f"{TAG} a private note on them"})
    said = _only("booking", bid, "booking_declined")
    s.check("declining is on it, against who, with the reason given",
            len(said) == 1 and said[0]["actor_user_id"] == owner["id"]
            and m.DECLINE_REASONS["dates_gone"] in (said[0]["details"] or ""),
            detail=str([dict(e) for e in said]))
    s.check("and never the note, which is about a person",
            said and "private note" not in (said[0]["details"] or ""))
    _r, stale, _t = stays.make("Stale")
    conn = db()
    conn.execute("UPDATE bookings SET created_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(days=12)).isoformat(), stale))
    conn.commit()
    conn.close()
    oc.get("/")          # the owner's dashboard, which runs the expiry
    gone = _only("booking", stale, "booking_declined")
    s.check("one the expiry declined says so",
            len(gone) == 1 and "no answer within" in (gone[0]["details"] or ""),
            detail=str([dict(e) for e in gone]))
    s.check("and is not put down to whoever opened the dashboard",
            gone and gone[0]["actor_user_id"] is None
            and m.audit_who(gone[0]) == "A scheduled job",
            detail=str([dict(e) for e in gone]))

    s.section("Cancelling says why, at the time, and once")
    _r, bid, _t = stays.make("Cancelled")
    oc.post(f"/admin/bookings/{bid}/confirm")
    conn = db()
    conn.execute("INSERT INTO booking_payments (booking_id, amount, method, created_at) "
                 "VALUES (?, 300, 'cash', ?)", (bid, _harness.datetime_now()))
    conn.execute("UPDATE bookings SET amount_paid = 300 WHERE id = ?", (bid,))
    conn.commit()
    conn.close()
    sent.clear()
    oc.post(f"/admin/bookings/{bid}/cancel", data={"cancel_reason": "house_could_not"})
    row = _one("SELECT * FROM bookings WHERE id = ?", bid)
    s.check("the reason is kept with the stay", row["cancel_reason"] == "house_could_not",
            detail=str(row["cancel_reason"]))
    went = _only("booking", bid, "booking_cancelled")
    s.check("and the cancellation is on its history, with it",
            len(went) == 1 and m.CANCEL_REASONS["house_could_not"] in (went[0]["details"] or "")
            and went[0]["actor_user_id"] == owner["id"], detail=str([dict(e) for e in went]))
    s.check("so what they paid is theirs to have back", m.held_money_is_theirs("room", row))
    letters = len([to for to, _s in sent if to == EMAIL])
    r = oc.post(f"/admin/bookings/{bid}/cancel", data={}, follow_redirects=True)
    s.check("a second press cancels nothing, and says so",
            len(_only("booking", bid, "booking_cancelled")) == 1
            and "already" in " ".join(flashes(r)).lower(), detail=str(flashes(r)))
    s.check("and does not write to the guest again",
            len([to for to, _s in sent if to == EMAIL]) == letters, detail=str(sent))
    oc.post(f"/management/cancellations/{bid}/reason", data={"reason": "illness"})
    later = _only("booking", bid, "booking_cancel_reason_set")
    s.check("a reason put on afterwards is on the history too",
            len(later) == 1 and m.CANCEL_REASONS["illness"] in (later[0]["details"] or ""),
            detail=str([dict(e) for e in later]))

    s.section("An edit says what moved")
    _r, bid, _t = stays.make("Edited")
    oc.post(f"/admin/bookings/{bid}/confirm")
    before = _one("SELECT * FROM bookings WHERE id = ?", bid)
    later_in = (m.parse_date(before["arrival_date"]) + timedelta(days=1)).isoformat()
    later_out = (m.parse_date(before["departure_date"]) + timedelta(days=1)).isoformat()
    sent.clear()
    oc.post(f"/admin/bookings/{bid}/edit",
            data=_edit_form(bid, arrival_date=later_in, departure_date=later_out))
    moved = _only("booking", bid, "booking_edited")
    s.check("moving the dates is on the history, from and to",
            len(moved) == 1 and "arrival" in moved[0]["details"] and "→" in moved[0]["details"]
            and m.format_date_human(later_in) in moved[0]["details"],
            detail=str([dict(e) for e in moved]))
    s.check("and the guest is sent the new details", EMAIL in [to for to, _s in sent],
            detail=str(sent))
    # A rate card that has moved since the booking: an edit that moves no night
    # must not reach for it.
    conn = db()
    conn.execute("UPDATE bookings SET total_price = 123.45, room_total_quoted = 123.45 "
                 "WHERE id = ?", (bid,))
    conn.commit()
    conn.close()
    sent.clear()
    oc.post(f"/admin/bookings/{bid}/edit", data=_edit_form(bid, guest_phone="+33 6 12 34 56 78"))
    moved = _only("booking", bid, "booking_edited")
    newest = moved[0]["details"] if moved else ""
    s.check("a new phone number says the phone changed, and not what to",
            len(moved) == 2 and "phone changed" in newest and "56 78" not in newest
            and "612345678" not in newest, detail=newest)
    s.check("and is not a letter to anybody", not sent, detail=str(sent))
    s.check("and moving no night keeps the price the nights were agreed at",
            abs((_one("SELECT total_price FROM bookings WHERE id = ?", bid)[0] or 0) - 123.45) < 0.005
            and "total" not in newest,
            detail=f"{_one('SELECT total_price FROM bookings WHERE id = ?', bid)[0]} — {newest}")
    r = oc.post(f"/admin/bookings/{bid}/edit", data=_edit_form(bid), follow_redirects=True)
    s.check("a save that changed nothing writes nothing and sends nothing",
            len(_only("booking", bid, "booking_edited")) == 2 and not sent,
            detail=f"{flashes(r)} {sent}")

    s.section("Checking out")
    oc.post(f"/admin/bookings/{bid}/checkout", data={"assigned_to_user_id": str(emp["id"])})
    s.check("is on the history, against who did it",
            [e["actor_user_id"] for e in _only("booking", bid, "booking_checked_out")]
            == [owner["id"]])

    s.section("What the guest does on their own page is theirs")
    _r, bid, token = stays.make("Own page")      # _r: this stay's reference
    guest = m.app.test_client()
    guest.post(f"/book/manage/{token}", data={"action": "contact", "guest_name": f"{TAG} Own page",
                                              "guest_phone": "+33 6 98 76 54 32"})
    theirs = _only("booking", bid, "booking_contact_changed_by_guest")
    s.check("a new number of their own says it changed, and is theirs",
            len(theirs) == 1 and m.audit_who(theirs[0]) == "The guest"
            and "phone" in (theirs[0]["details"] or "")
            and "54 32" not in (theirs[0]["details"] or "")
            and "765432" not in (theirs[0]["details"] or ""),
            detail=str([dict(e) for e in theirs]))
    whole = visible_text(oc.get(f"/admin/audit-log?q={_r}").get_data(as_text=True))
    s.check("and the whole log says it was the guest, not a job", "The guest" in whole
            and "A scheduled job" not in whole, detail=whole[-400:])
    b = _one("SELECT * FROM bookings WHERE id = ?", bid)
    new_in = (m.parse_date(b["arrival_date"]) + timedelta(days=1)).isoformat()
    new_out = (m.parse_date(b["departure_date"]) + timedelta(days=1)).isoformat()
    guest.post(f"/book/manage/{token}", data={"action": "change_dates", "new_arrival_date": new_in,
                                              "new_departure_date": new_out})
    shifted = _only("booking", bid, "booking_dates_changed_by_guest")
    s.check("moving their own dates is on it, from and to",
            len(shifted) == 1 and m.format_date_human(new_in) in (shifted[0]["details"] or "")
            and m.audit_who(shifted[0]) == "The guest", detail=str([dict(e) for e in shifted]))
    guest.post(f"/book/manage/{token}", data={"action": "cancel"})
    s.check("and so is cancelling it themselves",
            [m.audit_who(e) for e in _only("booking", bid, "booking_cancelled_by_guest")]
            == ["The guest"])
    _r, bid, token = stays.make("Asked")
    oc.post(f"/admin/bookings/{bid}/confirm")
    b = _one("SELECT * FROM bookings WHERE id = ?", bid)
    guest.post(f"/book/manage/{token}", data={
        "action": "change_dates",
        "new_arrival_date": (m.parse_date(b["arrival_date"]) + timedelta(days=2)).isoformat(),
        "new_departure_date": (m.parse_date(b["departure_date"]) + timedelta(days=2)).isoformat()})
    s.check("asking to move a confirmed stay is on it, as asked, not done",
            len(_only("booking", bid, "booking_change_asked_by_guest")) == 1
            and _one("SELECT arrival_date FROM bookings WHERE id = ?", bid)[0] == b["arrival_date"])

    s.section("Every booking leads to its history")
    cancelled = _one("SELECT id, reference_code FROM bookings WHERE guest_name = ?",
                     f"{TAG} Cancelled")
    listing = oc.get(f"/admin/bookings?q={cancelled['reference_code']}").get_data(as_text=True)
    s.check("a cancelled stay, which cannot be edited, still leads to its history",
            f"/admin/history/booking/{cancelled['id']}" in listing)
    page = oc.get(f"/admin/history/booking/{cancelled['id']}").get_data(as_text=True)
    s.check("and its history leads back to it, not to an edit page that refuses it",
            f"/admin/bookings/{cancelled['id']}/edit" not in page
            and "/admin/bookings?q=" in page)
    now = _harness.datetime_now()
    conn = db()
    conn.execute("""INSERT INTO workshops (title, description, price_per_person, default_capacity,
                    active, sort_order, created_at, deposit_percent)
                    VALUES (?, '', 400, 10, 1, 97, ?, 10)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=80)
    conn.execute("""INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
                    notes, created_at) VALUES (?, ?, ?, 10, ?, ?)""",
                 (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), f"{TAG} S", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} S",)).fetchone()["id"]
    conn.execute("""INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size,
                    status, reference_code, manage_token, created_at, total_price)
                    VALUES (?, ?, ?, 1, 'pending', ?, ?, ?, 400)""",
                 (sid, f"{TAG} Maker", EMAIL, f"{TAG}W", f"tokw{TAG}".lower(), now))
    place = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                         (f"{TAG}W",)).fetchone()["id"]
    conn.execute("""INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                    guest_email, dinner_date, party_size, status, total_price, payment_status,
                    created_at) VALUES (?, ?, ?, ?, ?, 2, 'pending', 0, 'unpaid', ?)""",
                 (f"{TAG}T", f"tokt{TAG}".lower(), f"{TAG} Diner", EMAIL,
                  (house_today() + timedelta(days=20)).isoformat(), now))
    table = conn.execute("SELECT id FROM restaurant_bookings WHERE reference_code = ?",
                         (f"{TAG}T",)).fetchone()["id"]
    conn.execute("""INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                    contact_name, contact_email, contact_phone, preferred_date, guest_count,
                    message, status, quoted_price, amount_paid, created_at)
                    VALUES (?, ?, 'wedding', ?, ?, '', ?, 40, 'ZZ', 'confirmed', 9000, 0, ?)""",
                 (f"{TAG}E", f"toke{TAG}".lower(), f"{TAG} Couple", EMAIL,
                  (house_today() + timedelta(days=200)).isoformat(), now))
    event = conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                         (f"{TAG}E",)).fetchone()["id"]
    conn.commit()
    conn.close()
    oc.post(f"/admin/workshops/registrations/{place}/confirm")
    # A sitting's lines are filed under its bare number, and a place and a
    # sitting can share one. The place's history must not take the sitting's.
    conn = db()
    conn.execute("""INSERT INTO audit_log (actor_user_id, action, target, details, created_at)
                    VALUES (NULL, 'workshop_session_called_off', ?, ?, ?)""",
                 (str(place), TAG + " sitting", now))
    conn.commit()
    conn.close()
    s.check("a sitting's line under the same number is not the place's",
            not any(e["details"] == TAG + " sitting" for e in _lines("workshop_booking", place)))
    oc.post(f"/admin/restaurant/{table}/confirm")
    oc.post(f"/admin/events/{event}/update", data={"status": "confirmed", "quoted_price": "9000"})
    for kind, record_id, action, listing_url, word in (
            ("workshop_booking", place, "workshop_registration_confirmed",
             "/admin/workshops/registrations", "The registration"),
            ("restaurant_booking", table, "restaurant_booking_confirmed", "/admin/restaurant",
             "The table"),
            ("event", event, "event_inquiry_updated", "/admin/events", "The event")):
        s.check(f"a {kind.replace('_', ' ')}'s decision is on its own history",
                len(_only(kind, record_id, action)) == 1,
                detail=str([e["action"] for e in _lines(kind, record_id)]))
        page = oc.get(f"/admin/history/{kind}/{record_id}").get_data(as_text=True)
        s.check(f"its page opens, and leads back to the {kind.replace('_', ' ')}",
                word in page and action.replace("_", " ").capitalize() in page)
        s.check(f"and the {kind.replace('_', ' ')} list leads to it",
                f"/admin/history/{kind}/{record_id}" in oc.get(listing_url).get_data(as_text=True))

    s.section("The times are the house's")
    at_night = "2026-07-01T23:30:00+00:00"          # half past one on the 2nd, here
    conn = db()
    conn.execute("""INSERT INTO audit_log (actor_user_id, action, target, details, created_at)
                    VALUES (NULL, ?, ?, NULL, ?)""",
                 (TAG + "_at_night", cancelled["reference_code"], at_night))
    conn.commit()
    conn.close()
    page = visible_text(oc.get(f"/admin/history/booking/{cancelled['id']}").get_data(as_text=True))
    s.check("a change after midnight shows on the house's clock and the house's day",
            m.local_datetime_str(at_night) in page and "2026-07-01 23:30" not in page,
            detail=m.local_datetime_str(at_night))
