"""A guest who starts an atelier booking again is one guest, holding one place.

Leaving the card page and filling the registration form in again -- or
pressing the button twice -- made a second registration while the first still
held its places for the hour. On 24 November 2026 Belinda Down did exactly
that: WRK-ZSO3FV held and never paid, WRK-TFM3I5 paid at 02:07, the first
given back by the hourly run at 03:13. Three things followed from it, none of
which errored:

  * one person held two places with nothing paid on one of them, and on a
    session with one place left the guest would have been told it was fully
    booked -- by their own first attempt;
  * the register listed her twice, once released and once paid, which reads
    as a double booking;
  * the first card page stayed open until its hold ran out, and a deposit paid
    on it would have taken a second place back for her.

Now the earlier hold is given back the moment the same guest registers again
for the same date, the register names it on the registration that followed
rather than listing it, and a deposit on the attempt she left is put in front
of the owner as a second payment, never a second place.

Stripe is stood in for the length of each branch; nothing leaves the building.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today, WORKSHOP_DETAILS
import _harness
from test_workshop_deposit_hold import _CardMachine, _paid

m = _harness.m
TAG = "ZZAGN"
_ip = [0]


def _cleanup():
    conn = db()
    ids = ("(SELECT id FROM workshop_bookings WHERE guest_email LIKE ? "
           "OR reference_code LIKE ?)")
    args = (TAG.lower() + "%", TAG + "%")
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages",
                  "workshop_custom_field_responses"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", args)
    conn.execute("DELETE FROM refunds WHERE category = 'workshop' AND booking_id IN " + ids, args)
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ? OR reference_code LIKE ?",
                 args)
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _session(label, *, capacity=4, deposit=30, starts_in=210):
    conn = db()
    now = _harness.datetime_now()
    title = f"{TAG} {label}"
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 1000, ?, 1, 97, ?, ?)""", (title, capacity, now, deposit))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (title,)).fetchone()["id"]
    start = house_today() + timedelta(days=starts_in)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
           created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=2)).isoformat(), capacity,
         f"{TAG} {label}", now))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {label}",)).fetchone()["id"]
    conn.close()
    return sid


def _email(who):
    return f"{TAG.lower()}.{who.lower()}@example.invalid"


def _register(sid, who, party=1):
    """Post the public form as a guest would, and return the registration it
    made: the newest under that address on that session."""
    _ip[0] += 1
    data = {"guest_name": f"{TAG} {who}", "guest_email": _email(who),
            "party_size": str(party), "occupancy_type": "double", **WORKSHOP_DETAILS}
    r = m.app.test_client().post(f"/workshops/register/{sid}", data=data,
                                 environ_base={"REMOTE_ADDR": f"203.0.113.{_ip[0]}"})
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE session_id = ? AND guest_email = ? "
                       "ORDER BY id DESC LIMIT 1", (sid, _email(who))).fetchone()
    conn.close()
    return r, row


def _row(bid):
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _left(sid):
    conn = db()
    n = m.workshop_session_remaining_capacity(conn, sid)
    conn.close()
    return n


def _release(minutes_on):
    conn = db()
    with m.app.test_request_context():
        n = m.release_lapsed_workshop_holds(
            conn, now=datetime.now(timezone.utc) + timedelta(minutes=minutes_on))
    conn.close()
    return n


def _cards(body, who):
    """How many registration cards on a register page are this guest's."""
    return body.count(f"<strong>{TAG} {who}</strong>")


def run():
    s = Suite("A guest who starts again holds one place and is listed once")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    # ------------------------------------------------------------------
    s.section("Registering again gives the first unpaid hold back")
    sid = _session("Cuisine", capacity=4)
    other_sid = _session("Painting", capacity=4)
    with _CardMachine():
        _r, first = _register(sid, "Belinda", party=2)
        _r, elsewhere = _register(other_sid, "Belinda", party=1)
        _r, stranger = _register(sid, "Carl", party=1)
    if not (first and elsewhere and stranger):
        s.check("the three first registrations were made", False,
                detail=f"{first} {elsewhere} {stranger}")
        _cleanup()
        return s
    s.check("the first attempt is held for the deposit",
            first["status"] == "pending" and first["deposit_hold_until"],
            detail=first["status"])
    s.check("and with Carl's place, one place is left", _left(sid) == 1, detail=str(_left(sid)))

    # The address as another path might have stored it: one person whatever
    # the case, or a stray space.
    conn = db()
    conn.execute("UPDATE workshop_bookings SET guest_email = ? WHERE id = ?",
                 (" " + _email("Belinda").upper(), first["id"]))
    conn.commit()
    conn.close()

    with _CardMachine() as card:
        r, second = _register(sid, "Belinda", party=2)
    s.check("starting again for two is not refused as fully booked by her own hold",
            r.status_code == 303 and card.sessions and second["id"] != first["id"],
            detail=f"{r.status_code} {r.headers.get('Location')} {second and second['id']}")
    s.check("it goes to a card page of its own", r.headers.get("Location", "").startswith(
        "https://checkout.stripe.invalid/"), detail=r.headers.get("Location"))
    was = _row(first["id"])
    s.check("the first attempt is given back at once, not an hour later",
            was["status"] == "cancelled" and was["hold_released_at"],
            detail=f"{was['status']} {was['hold_released_at']}")
    s.check("the second holds her two places, and only those",
            second["status"] == "pending" and _left(sid) == 1, detail=str(_left(sid)))
    s.check("somebody else's hold on the same date is left alone",
            _row(stranger["id"])["status"] == "pending")
    s.check("and her own hold on another date is left alone",
            _row(elsewhere["id"])["status"] == "pending")
    conn = db()
    line = conn.execute("SELECT action, actor_user_id, details FROM audit_log WHERE target = ? "
                        "ORDER BY id DESC LIMIT 1", (first["reference_code"],)).fetchone()
    conn.close()
    s.check("its history says the guest gave it up by registering again",
            line and line["action"] == "workshop_hold_released_by_guest"
            and "registered again" in (line["details"] or ""),
            detail=str(line and dict(line)))
    page = oc.get(f"/admin/history/workshop_booking/{first['id']}").get_data(as_text=True)
    s.check("and names the guest, not a scheduled job, as who did it",
            "The guest" in page and "A scheduled job" not in page)

    # ------------------------------------------------------------------
    s.section("The register lists her once, with the attempt she left named")
    with _CardMachine():
        outcome = _paid(second["id"], second["deposit_amount"], "agn-second")
    s.check("the second attempt's deposit confirms it", outcome == "confirmed"
            and _row(second["id"])["status"] == "confirmed", detail=str(outcome))
    reg = oc.get(f"/admin/workshops/registrations?q={TAG}+Belinda&when=all&sort=recent")
    body = reg.get_data(as_text=True)
    s.check("one card for her on this date, plus the other date's",
            _cards(body, "Belinda") == 2, detail=str(_cards(body, "Belinda")), response=reg)
    s.check("the card names the attempt she left",
            "Also started" in body and first["reference_code"] in body)
    s.check("and says it is not a second booking", "Not a second booking" in body)
    found = oc.get(f"/admin/workshops/registrations?q={first['reference_code']}&when=all")
    fbody = found.get_data(as_text=True)
    s.check("searching the reference she left finds the registration that has her place",
            second["reference_code"] in fbody and _cards(fbody, "Belinda") == 1,
            response=found)
    sess = oc.get(f"/admin/workshops/sessions/{sid}").get_data(as_text=True)
    s.check("the session page does not list her among the cancelled",
            "Cancelled or declined" not in sess)
    conn = db()
    with m.app.test_request_context():
        _items, lv = m.workshop_register_view(conn, {"q": f"{TAG} Belinda", "when": "all"})
        csv_ids = {r["id"] for r in lv["rows"]}
    conn.close()
    s.check("the export of the view carries one registration for her on this date",
            second["id"] in csv_ids and first["id"] not in csv_ids, detail=str(csv_ids))

    s.section("A hold nobody came back for is still a row of its own")
    _release(61)
    lapsed = _row(stranger["id"])
    s.check("Carl's hold lapses", lapsed["status"] == "cancelled" and lapsed["hold_released_at"])
    reg = oc.get(f"/admin/workshops/registrations?q={TAG}+Carl&when=all").get_data(as_text=True)
    s.check("and is listed, released for want of the deposit",
            _cards(reg, "Carl") == 1 and "released, deposit never came" in reg)

    # ------------------------------------------------------------------
    s.section("Paying on the page she left is a second payment, not a second place")
    places_before = _left(sid)
    with _CardMachine():
        outcome = _paid(first["id"], first["deposit_amount"], "agn-first")
    after = _row(first["id"])
    s.check("the attempt she left is not taken back",
            outcome == "unplaced" and after["status"] == "cancelled",
            detail=f"{outcome} {after['status']}")
    s.check("no further places are taken", _left(sid) == places_before,
            detail=f"{places_before} -> {_left(sid)}")
    conn = db()
    note = conn.execute("SELECT title, body FROM notifications WHERE title LIKE ? "
                        "ORDER BY id DESC LIMIT 1", (f"%{TAG} Belinda%",)).fetchone()
    refunded = conn.execute("SELECT COUNT(*) FROM refunds WHERE category = 'workshop' "
                            "AND booking_id = ?", (first["id"],)).fetchone()[0]
    conn.close()
    s.check("the owner is told she has paid twice for one place, and on which",
            note and note["title"].startswith("Deposit paid for a released place")
            and "paid twice" in note["body"] and second["reference_code"] in note["body"],
            detail=str(note and dict(note)))
    s.check("nothing is refunded by the app", refunded == 0, detail=str(refunded))
    reg = oc.get(f"/admin/workshops/registrations?q={TAG}+Belinda&when=all").get_data(as_text=True)
    s.check("with money on it, the attempt she left is a row again, to give it back from",
            _cards(reg, "Belinda") == 3, detail=str(_cards(reg, "Belinda")))

    s.section("Paying on the page she left, before the second was paid, is her place")
    sid3 = _session("Weaving", capacity=4)
    with _CardMachine():
        _r, a = _register(sid3, "Dora", party=1)
        _r, b = _register(sid3, "Dora", party=1)
        outcome = _paid(a["id"], a["deposit_amount"], "agn-dora")
    s.check("the first attempt's deposit takes its place back when the second is unpaid",
            outcome == "reconfirmed" and _row(a["id"])["status"] == "confirmed",
            detail=f"{outcome} {_row(a['id'])['status']}")
    s.check("and the second is still only a hold, to lapse on its own",
            _row(b["id"])["status"] == "pending" and _row(b["id"])["deposit_hold_until"])
    _release(61)
    reg = oc.get(f"/admin/workshops/registrations?q={TAG}+Dora&when=all").get_data(as_text=True)
    s.check("once it has, she is listed once, with the later attempt named on her place",
            _cards(reg, "Dora") == 1 and b["reference_code"] in reg and "Also started" in reg,
            detail=str(_cards(reg, "Dora")))

    s.section("Nothing was left standing in")
    s.check("stripe_enabled() is off again", not m.stripe_enabled())
    s.check("and the key is still neutralised", not getattr(m.stripe, "api_key", None))

    _cleanup()
    return s
