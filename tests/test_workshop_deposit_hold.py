"""A place on an atelier is held while the deposit is paid, not confirmed.

The registration form used to confirm the place, write the guest the
confirmed letter and tell the owner "Atelier booked" -- and only THEN send
the guest to Stripe for the deposit. Anybody who closed the card page kept a
confirmed place with no deposit and no card, and nothing ever gave it back.
On 18 October 2026 a script did exactly that: random letters in every field,
CONFIRMED, two of ten places on a June atelier.

Now the place is held for WORKSHOP_DEPOSIT_HOLD_MINUTES while the guest is at
Stripe, counted as taken, with nobody told anything. The deposit confirms it,
with the letter and the owner's notice. A hold nobody paid for is given back
by the housekeeping run, writing to nobody. A deposit that arrives after the
place was given back is honoured if there is room and put in front of the
owner if there is not -- never refunded on a timer.

Where no card is taken -- Stripe off, a 0% deposit, a card page Stripe would
not give us -- the place is confirmed on the spot, as it always was.

Stripe is stood in for the length of each branch; nothing leaves the building.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today, WORKSHOP_DETAILS
import _harness

m = _harness.m
TAG = "ZZWDH"
FAKE_URL = "https://checkout.stripe.invalid/c/pay/" + TAG
_ip = [0]


class _Made:
    def __init__(self, **kw):
        self.kw = kw
        self.id = f"cs_{TAG}_{len(kw)}"
        self.url = FAKE_URL


class _CardMachine:
    """Stripe answers for the length of one branch, and never actually."""

    def __init__(self, fail=False):
        self.fail = fail

    def __enter__(self):
        self.sessions = []
        self.saved = (m.stripe_enabled, m.stripe.checkout.Session.create,
                      m.stripe.Customer.create, m.card_from_stripe)
        m.stripe_enabled = lambda: True

        def _session(**kw):
            if self.fail:
                raise RuntimeError("Stripe is down, as far as this branch knows")
            self.sessions.append(kw)
            return _Made(**kw)

        m.stripe.checkout.Session.create = _session
        m.stripe.Customer.create = lambda **kw: _Made(**kw)
        m.card_from_stripe = lambda ref: None
        return self

    def __exit__(self, *_exc):
        (m.stripe_enabled, m.stripe.checkout.Session.create,
         m.stripe.Customer.create, m.card_from_stripe) = self.saved
        return False


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


def _session(label, *, capacity=10, deposit=30, starts_in=200):
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


def _register(sid, who, party=1):
    """Post the public form as a guest would, from an address of its own so
    the hourly limit never decides anything here."""
    _ip[0] += 1
    anon = m.app.test_client()
    data = {"guest_name": f"{TAG} {who}", "guest_email": f"{TAG.lower()}.{who.lower()}@example.invalid",
            "party_size": str(party), "occupancy_type": "double", **WORKSHOP_DETAILS}
    r = anon.post(f"/workshops/register/{sid}", data=data,
                  environ_base={"REMOTE_ADDR": f"198.51.100.{_ip[0]}"})
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE guest_email = ?",
                       (data["guest_email"],)).fetchone()
    conn.close()
    return r, row


def _row(bid):
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _letters(bid):
    conn = db()
    subjects = [r["subject"] for r in conn.execute(
        "SELECT subject FROM workshop_messages WHERE workshop_booking_id = ? ORDER BY id",
        (bid,)).fetchall()]
    conn.close()
    return subjects


def _notices(who):
    conn = db()
    titles = [r["title"] for r in conn.execute(
        "SELECT title FROM notifications WHERE title LIKE ? ORDER BY id",
        (f"%{TAG} {who}%",)).fetchall()]
    conn.close()
    return titles


def _paid(bid, amount, sid_suffix):
    """A deposit arriving from Stripe, as the webhook and the return page
    both hand it to mark_workshop_payment_paid."""
    session = {"id": f"cs_{TAG}_{sid_suffix}", "amount_total": int(round(amount * 100)),
               "payment_status": "paid",
               "metadata": {"workshop_booking_id": str(bid), "kind": "workshop_deposit"}}
    conn = db()
    with m.app.test_request_context():
        outcome = m.mark_workshop_payment_paid(conn, session)
    conn.close()
    return outcome


def _release(minutes_on):
    conn = db()
    with m.app.test_request_context():
        n = m.release_lapsed_workshop_holds(
            conn, now=datetime.now(timezone.utc) + timedelta(minutes=minutes_on))
    conn.close()
    return n


def _left(sid):
    conn = db()
    n = m.workshop_session_remaining_capacity(conn, sid)
    conn.close()
    return n


def run():
    s = Suite("Atelier places held for the deposit, not confirmed before it")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    s.check("the hold is one named length, an hour",
            m.WORKSHOP_DEPOSIT_HOLD_MINUTES == 60, detail=str(m.WORKSHOP_DEPOSIT_HOLD_MINUTES))

    # ------------------------------------------------------------------
    s.section("Registering holds the place and tells nobody yet")
    sid = _session("Cooking", capacity=4)
    with _CardMachine() as card:
        r, row = _register(sid, "Ada", party=2)
    s.check("the guest is sent to the card page", r.status_code == 303
            and r.headers.get("Location") == FAKE_URL,
            detail=f"{r.status_code} {r.headers.get('Location')}")
    s.check("the registration exists", row is not None)
    if row is None:
        _cleanup()
        return s
    bid = row["id"]
    s.check("it is NOT confirmed", row["status"] == "pending", detail=row["status"])
    until = m.parse_datetime_iso(row["deposit_hold_until"]) if row["deposit_hold_until"] else None
    mins = (until - datetime.now(timezone.utc)).total_seconds() / 60 if until else None
    s.check("it is held for about an hour", mins is not None and 58 <= mins <= 61,
            detail=f"{mins}")
    s.check("the held places count as taken", _left(sid) == 2, detail=str(_left(sid)))
    s.check("no letter has gone to the guest", _letters(bid) == [], detail=str(_letters(bid)))
    s.check("and the owner has not been told 'Atelier booked'", _notices("Ada") == [],
            detail=str(_notices("Ada")))
    sent = card.sessions[-1] if card.sessions else {}
    closes = sent.get("expires_at")
    s.check("the card page closes when the hold does",
            closes is not None and until is not None
            and abs(closes - until.timestamp()) < 120,
            detail=f"expires_at={closes} hold={until and until.timestamp()}")

    s.section("The owner sees it as waiting for the deposit, not as confirmed")
    page = oc.get(f"/admin/workshops/registrations?q={row['reference_code']}&when=all")
    body = page.get_data(as_text=True)
    s.check("the register says it is waiting for the deposit, with its deadline",
            "waiting for the deposit until" in body, response=page)
    s.check("and does not badge it confirmed or pending",
            f'status-confirmed" style="margin-left:8px;">confirmed' not in body
            and 'style="margin-left:8px;">pending' not in body)
    sess = oc.get(f"/admin/workshops/sessions/{sid}").get_data(as_text=True)
    s.check("the session page says the same", "waiting for the deposit until" in sess)
    s.check("and does not count it as waiting on the owner", "waiting on you" not in sess)
    conn = db()
    awaiting = conn.execute("SELECT COUNT(*) FROM workshop_bookings WHERE status = 'pending' "
                            "AND deposit_hold_until IS NULL AND id = ?", (bid,)).fetchone()[0]
    conn.close()
    s.check("a hold is not counted among registrations awaiting a decision", awaiting == 0)
    conn = db()
    wrow = conn.execute("SELECT w.* FROM workshops w JOIN workshop_sessions ws ON ws.workshop_id = w.id "
                        "WHERE ws.id = ?", (sid,)).fetchone()
    upcoming = m.workshop_sitting_rows(conn, [wrow], house_today())["upcoming"]
    conn.close()
    entry = next(e for e in upcoming[wrow["id"]] if e["session"]["id"] == sid)
    s.check("the workshops page does not say the sitting needs the owner for it",
            entry["needs"] == [] and entry["holding"] == 2,
            detail=f"needs={entry['needs']} holding={entry['holding']}")
    manage = m.app.test_client().get(f"/workshops/manage/{row['manage_token']}")
    s.check("the guest's own page says the place is held, and until when",
            "We are holding your place until" in manage.get_data(as_text=True), response=manage)

    s.section("Walking away from the card page keeps the hold, and says until when")
    r = m.app.test_client().get(f"/workshops/stripe-cancel/{row['manage_token']}",
                                follow_redirects=True)
    s.check("they are told the place is held until a time",
            any("holding your place until" in f for f in flashes(r)), detail=str(flashes(r)))

    # ------------------------------------------------------------------
    s.section("The deposit confirms it, with the letter and the owner's notice")
    with _CardMachine():
        outcome = _paid(bid, row["deposit_amount"], "ada")
    after = _row(bid)
    s.check("it is confirmed", after["status"] == "confirmed" and outcome == "confirmed",
            detail=f"{after['status']} / {outcome}")
    s.check("and no longer held", after["deposit_hold_until"] is None)
    letters = _letters(bid)
    s.check("the guest has the confirmed letter",
            any(x.startswith("Registration confirmed") for x in letters), detail=str(letters))
    s.check("and the deposit receipt", any(x.startswith("Deposit received") for x in letters),
            detail=str(letters))
    s.check("the owner is told 'Atelier booked' now",
            any(t.startswith("Atelier booked") for t in _notices("Ada")),
            detail=str(_notices("Ada")))
    with _CardMachine():
        again = _paid(bid, row["deposit_amount"], "ada")
    s.check("the same payment reported twice confirms nothing twice",
            again is None and sum(1 for x in _letters(bid) if x.startswith("Registration confirmed")) == 1,
            detail=str(_letters(bid)))

    # ------------------------------------------------------------------
    s.section("A hold nobody pays for is given back, writing to nobody")
    with _CardMachine():
        _r, gone = _register(sid, "Bot", party=2)
    gid = gone["id"]
    s.check("the session is full while it is held", _left(sid) == 0, detail=str(_left(sid)))
    s.check("nothing is released before the deadline", _release(30) == 0
            and _row(gid)["status"] == "pending")
    released = _release(61)
    g = _row(gid)
    s.check("after it, the hold is released", released >= 1 and g["status"] == "cancelled"
            and g["hold_released_at"], detail=f"{released} {g['status']}")
    s.check("and the places are free again", _left(sid) == 2, detail=str(_left(sid)))
    s.check("no letter went to the guest", _letters(gid) == [], detail=str(_letters(gid)))
    s.check("and the owner was told nothing", _notices("Bot") == [], detail=str(_notices("Bot")))
    reg = oc.get(f"/admin/workshops/registrations?q={g['reference_code']}&when=all")
    s.check("the register says it was released for want of the deposit",
            "released, deposit never came" in reg.get_data(as_text=True))
    conn = db()
    job = m.run_housekeeping_job(conn)
    conn.close()
    s.check("the housekeeping run does this", "held for a deposit that never came" in job,
            detail=job)
    r = m.app.test_client().get(f"/workshops/pay-deposit/{g['manage_token']}")
    s.check("a pay link on a released place sends them to register again",
            r.status_code == 302 and f"/workshops/register/{sid}" in r.headers.get("Location", ""),
            detail=f"{r.status_code} {r.headers.get('Location')}")
    page = m.app.test_client().get(f"/workshops/manage/{g['manage_token']}").get_data(as_text=True)
    s.check("and their page says the time ran out", "the time ran out" in page)

    s.section("A deposit after the release is honoured when there is room")
    with _CardMachine():
        outcome = _paid(gid, g["deposit_amount"], "bot-late")
    g = _row(gid)
    s.check("the place is taken again and confirmed",
            outcome == "reconfirmed" and g["status"] == "confirmed", detail=f"{outcome} {g['status']}")
    s.check("the session is full again", _left(sid) == 0)
    s.check("the owner is told it came late",
            any(t.startswith("Atelier booked") for t in _notices("Bot")))

    # ------------------------------------------------------------------
    s.section("A deposit after the release, with the atelier full, goes to the owner")
    sid2 = _session("Painting", capacity=2)
    with _CardMachine():
        _r, late = _register(sid2, "Late", party=2)
    lid = late["id"]
    _release(61)
    with _CardMachine():
        _r, filler = _register(sid2, "Filler", party=2)
        _paid(filler["id"], filler["deposit_amount"], "filler")
    s.check("the places went to somebody else", _row(filler["id"])["status"] == "confirmed")
    with _CardMachine():
        outcome = _paid(lid, late["deposit_amount"], "late")
    L = _row(lid)
    s.check("it is not placed", outcome == "unplaced" and L["status"] == "cancelled",
            detail=f"{outcome} {L['status']}")
    conn = db()
    refunded = conn.execute("SELECT COUNT(*) FROM refunds WHERE category = 'workshop' "
                            "AND booking_id = ?", (lid,)).fetchone()[0]
    paid_in = m.amount_paid_for(conn, "workshop", L)
    conn.close()
    s.check("nothing is refunded", refunded == 0 and paid_in > 0, detail=f"{refunded} {paid_in}")
    s.check("the guest is sent no receipt for a place they do not have",
            _letters(lid) == [], detail=str(_letters(lid)))
    loud = [t for t in _notices("Late") if t.startswith("Deposit paid for a released place")]
    s.check("the owner is told at once", bool(loud), detail=str(_notices("Late")))
    conn = db()
    with m.app.test_request_context():
        warns = m.owner_home_warnings(conn, house_today())
    conn.close()
    hit = [w for w in warns if "paid a deposit for a place that had been released" in w["title"]]
    s.check("and the owner home says so, as a blocker",
            bool(hit) and hit[0]["severity"] == "blocker", detail=str([w["title"] for w in warns]))

    # Returning from Stripe to a place that is gone says so, not "thank you".
    saved_retrieve = m.stripe.checkout.Session.retrieve
    with _CardMachine():
        m.stripe.checkout.Session.retrieve = lambda sid_: {
            "id": f"cs_{TAG}_late", "amount_total": 60000, "payment_status": "paid",
            "metadata": {"workshop_booking_id": str(lid), "kind": "workshop_deposit"}}
        try:
            r = m.app.test_client().get(
                f"/workshops/stripe-success?manage_token={L['manage_token']}&kind=deposit"
                f"&session_id=cs_{TAG}_late", follow_redirects=True)
        finally:
            m.stripe.checkout.Session.retrieve = saved_retrieve
    s.check("the return page tells them the place had gone",
            any("already been released" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("The owner takes them back, and the warning closes itself")
    conn = db()
    conn.execute("UPDATE workshop_bookings SET status = 'cancelled' WHERE id = ?", (filler["id"],))
    conn.commit()
    conn.close()
    r = oc.post(f"/admin/workshops/registrations/{lid}/take-back", follow_redirects=True)
    s.check("taking them back confirms them", _row(lid)["status"] == "confirmed",
            detail=str(flashes(r)))
    conn = db()
    with m.app.test_request_context():
        warns = m.owner_home_warnings(conn, house_today())
    conn.close()
    s.check("and the line is gone from the owner home",
            not [w for w in warns if "place that had been released" in w["title"]])
    r = oc.post(f"/admin/workshops/registrations/{gid}/take-back")
    s.check("take-back refuses anything that was not a released, paid place",
            r.status_code == 404, detail=str(r.status_code))

    # ------------------------------------------------------------------
    s.section("Where no card is taken, the place is confirmed as before")
    sid3 = _session("Weaving", capacity=6)
    _r, off = _register(sid3, "NoStripe")
    s.check("with Stripe off, confirmed on the spot", off["status"] == "confirmed"
            and off["deposit_hold_until"] is None, detail=off["status"])
    sid4 = _session("Pottery", capacity=6, deposit=0)
    with _CardMachine() as card:
        _r, zero = _register(sid4, "Nothing")
    s.check("with no deposit to take, confirmed on the spot", zero["status"] == "confirmed"
            and zero["deposit_hold_until"] is None and not card.sessions,
            detail=f"{zero['status']} {len(card.sessions)}")
    with _CardMachine(fail=True):
        r, down = _register(sid3, "Down")
    down = _row(down["id"])
    s.check("when Stripe gives no card page, confirmed on the spot",
            down["status"] == "confirmed" and down["deposit_hold_until"] is None,
            detail=f"{down['status']} {down['deposit_hold_until']}")
    s.check("and they land on the confirmation, not a card page",
            "/workshops/confirmation/" in r.headers.get("Location", ""),
            detail=r.headers.get("Location"))
    s.check("with the confirmed letter", any(x.startswith("Registration confirmed")
                                             for x in _letters(down["id"])))

    s.section("A deposit marked paid by hand confirms a held place too")
    with _CardMachine():
        _r, hand = _register(sid3, "ByHand")
    r = oc.post(f"/admin/workshops/registrations/{hand['id']}/mark-deposit-paid",
                data={"method": "bank_transfer"})
    s.check("confirmed and no longer held", _row(hand["id"])["status"] == "confirmed"
            and _row(hand["id"])["deposit_hold_until"] is None, detail=_row(hand["id"])["status"])

    s.section("Nothing was left standing in")
    s.check("stripe_enabled() is off again", not m.stripe_enabled())
    s.check("and the key is still neutralised", not getattr(m.stripe, "api_key", None))

    _cleanup()
    return s
