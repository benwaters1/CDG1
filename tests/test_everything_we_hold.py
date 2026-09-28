"""What we hold about somebody, all of it -- and what an erasure leaves.

What there was: a request for somebody's data searched by their address, so
a text, a stop, a yes to marketing texts and their history -- all kept by the
number -- were never in it and never erased. An erasure deleted three sales
outright: an event, a share of a split bill, and a card dispute; and it left
the words on a stay that said who had stayed, and the somebody-else they had
asked us to copy or to bill. The audit trail, which outlives everything, wrote
people's addresses and numbers into its lines, and an erasure left them there.
Every copy of a confirmation the house kept carried the link that opens the
booking without a password, though the notice says a message that is itself a
key is never kept. And nothing ever deleted a held text, or a sign-in link to
somebody's account once it had stopped working.

What this holds:

  - The export and the erasure find what is kept by their numbers -- every
    number they are known by, however it was written.
  - A sale is kept without them: an event, a share, a dispute, a stay, each
    with the person taken off it and the money left on.
  - The audit trail no longer writes an address or a number, and an erasure
    takes the person out of the lines already written, leaving who acted.
  - A letter or a text is kept without its private link, which still goes out
    in the letter itself; the copies already kept are cleaned at startup; and
    the record still lists a held text once.
  - Held texts go after the two years, as held letters do, and sign-in links a
    month after they stop working. The nightly job does both.
  - The notice says so.
"""
import inspect
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZEH"
WHO = f"{TAG.lower()}@example.invalid"
PHONE = "+33 6 55 44 33 22"
STAY_PHONE = "06 11 22 33 44"


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        numbers = [m.normalise_phone(PHONE), m.normalise_phone(STAY_PHONE)]
        for table in ("sms_outbox", "sms_optouts", "sms_consents", "consent_events"):
            conn.execute(f"DELETE FROM {table} WHERE phone IN (?, ?)", numbers)
        conn.execute("DELETE FROM guest_messages WHERE LOWER(to_address) LIKE ? "
                     "OR subject LIKE ? OR body LIKE ?",
                     (TAG.lower() + "%", TAG + "%", TAG + "%"))
        conn.execute("DELETE FROM guest_messages WHERE to_address IN (?, ?, ?, ?)",
                     numbers + [PHONE, STAY_PHONE])
        conn.execute("DELETE FROM email_outbox WHERE LOWER(to_address) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM payment_disputes WHERE stripe_dispute_id LIKE ?", ("dp_" + TAG + "%",))
        conn.execute("DELETE FROM booking_shares WHERE booking_id IN "
                     "(SELECT id FROM bookings WHERE reference_code LIKE ?)", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM guest_sessions WHERE token LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM audit_log WHERE action LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM guests WHERE name LIKE ? OR LOWER(email) = ?", (TAG + "%", WHO))
        conn.commit()
    finally:
        conn.close()


def _rows(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Everything we hold")
    oc, ec, owner, _emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, *a, **k: sent.append((to, subject, body)) or True
    try:
        _run(s, oc, owner, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _seed(owner):
    """Somebody the house knows by an address and two numbers, with a stay, an
    event, a share of somebody's bill and a card dispute."""
    now = _harness.datetime_now()
    number, stay_number = m.normalise_phone(PHONE), m.normalise_phone(STAY_PHONE)
    conn = db()
    conn.execute("INSERT INTO guests (name, email, phone, created_at) VALUES (?, ?, ?, ?)",
                 (f"{TAG} Solène Martin", WHO, PHONE, now))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?", (WHO,)).fetchone()["id"]
    room = _harness.ensure_room()
    arrive = house_today() - timedelta(days=40)
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, guest_phone, arrival_date, departure_date, party_size, status,
                    total_price, created_at, special_requests, booked_by_name, booked_by_email)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?, ?, ?, ?)""",
                 (room["id"], f"{TAG}S", f"toks{TAG}".lower(), f"{TAG} Solène Martin", WHO,
                  STAY_PHONE, arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(), now,
                  f"{TAG} a ground-level shower, for her knee", f"{TAG} Paul Martin",
                  f"{TAG.lower()}.paul@example.invalid"))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()["id"]
    conn.execute("""INSERT INTO booking_shares (booking_id, name, email, amount, status, note,
                    created_at) VALUES (?, ?, ?, 200, 'paid', ?, ?)""",
                 (bid, f"{TAG} Solène Martin", WHO, f"{TAG} half, as agreed", now))
    conn.execute("""INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                    contact_name, contact_email, contact_phone, preferred_date, guest_count,
                    message, status, quoted_price, amount_paid, created_at)
                    VALUES (?, ?, 'birthday', ?, ?, ?, ?, 20, ?, 'confirmed', 2000, 500, ?)""",
                 (f"{TAG}E", f"toke{TAG}".lower(), f"{TAG} Solène Martin", WHO, PHONE,
                  (house_today() + timedelta(days=60)).isoformat(),
                  f"{TAG} for my mother's eightieth", now))
    conn.execute("""INSERT INTO payment_disputes (stripe_dispute_id, amount, status, opened_at,
                    guest_name, guest_email, reference_code) VALUES (?, 400, 'lost', ?, ?, ?, ?)""",
                 (f"dp_{TAG}", now, f"{TAG} Solène Martin", WHO, f"{TAG}S"))
    conn.execute("""INSERT INTO sms_outbox (phone, body, purpose, reason, created_at)
                    VALUES (?, ?, 'transactional', 'no provider', ?)""",
                 (stay_number, f"{TAG} your room is ready", now))
    conn.execute("INSERT INTO sms_optouts (phone, reason, created_at) VALUES (?, ?, ?)",
                 (number, f"{TAG} asked us", now))
    conn.execute("INSERT INTO sms_consents (phone, source, granted_at) VALUES (?, ?, ?)",
                 (stay_number, f"{TAG} at the desk", now))
    conn.execute("""INSERT INTO consent_events (channel, granted, how, phone, created_at)
                    VALUES ('texts', 0, ?, ?, ?)""", (f"{TAG} asked us", number, now))
    # A text filed by the number as somebody typed it, before there was a profile.
    conn.execute("""INSERT INTO guest_messages (booking_id, channel, to_address, subject, body,
                    delivered, created_at) VALUES (NULL, 'sms', ?, NULL, ?, 1, ?)""",
                 (PHONE, f"{TAG} see you at four", now))
    for target, details in ((WHO, "an export"), (PHONE, None),
                            (f"room booking {TAG}S", f"{TAG} Solène Martin, party of 2")):
        conn.execute("""INSERT INTO audit_log (actor_user_id, action, target, details, created_at)
                        VALUES (?, ?, ?, ?, ?)""", (owner["id"], TAG.lower() + "_did_something",
                                                    target, details, now))
    conn.commit()
    conn.close()
    return gid, bid


def _run(s, oc, owner, sent):
    gid, bid = _seed(owner)

    s.section("By their numbers as well as their address")
    conn = db()
    export = m.guest_data_export(conn, WHO)
    conn.close()
    held = export["tables"]
    s.check("a text waiting to go to the number on their stay is in it",
            any(TAG in (r.get("body") or "") for r in held.get("sms_outbox", [])),
            detail=str(sorted(held)))
    s.check("and their stop, their yes to marketing texts, and its history",
            held.get("sms_optouts") and held.get("sms_consents") and any(
                TAG in (r.get("how") or "") for r in held.get("consent_events", [])))
    s.check("and a text filed under the number as somebody typed it",
            any(r.get("body") == f"{TAG} see you at four" for r in held.get("guest_messages", [])))

    s.section("An erasure keeps the sale, and nothing else")
    conn = db()
    result = m.guest_data_erase(conn, WHO)
    conn.commit()
    conn.close()
    numbers = [m.normalise_phone(PHONE), m.normalise_phone(STAY_PHONE)]
    s.check("what was kept by their numbers is gone",
            not _rows("SELECT id FROM sms_outbox WHERE phone IN (?, ?)", *numbers)
            and not _rows("SELECT id FROM sms_optouts WHERE phone IN (?, ?)", *numbers)
            and not _rows("SELECT id FROM sms_consents WHERE phone IN (?, ?)", *numbers)
            and not _rows("SELECT id FROM consent_events WHERE phone IN (?, ?)", *numbers)
            and not _rows("SELECT id FROM guest_messages WHERE body = ?", f"{TAG} see you at four"),
            detail=str(result["deleted"]))
    event = _rows("SELECT * FROM event_inquiries WHERE reference_code = ?", f"{TAG}E")
    s.check("the event is still there, with its money, and without them",
            event and event[0]["quoted_price"] == 2000 and event[0]["contact_name"] == m.ERASED_MARKER
            and not event[0]["contact_email"] and not event[0]["contact_phone"]
            and not event[0]["message"], detail=str([dict(r) for r in event]))
    share = _rows("SELECT * FROM booking_shares WHERE booking_id = ?", bid)
    s.check("so is their share of the bill",
            share and share[0]["amount"] == 200 and share[0]["name"] == m.ERASED_MARKER
            and not share[0]["email"] and not share[0]["note"], detail=str([dict(r) for r in share]))
    dispute = _rows("SELECT * FROM payment_disputes WHERE stripe_dispute_id = ?", f"dp_{TAG}")
    s.check("and the dispute",
            dispute and dispute[0]["amount"] == 400 and not dispute[0]["guest_email"]
            and dispute[0]["guest_name"] == m.ERASED_MARKER, detail=str([dict(r) for r in dispute]))
    stay = _rows("SELECT * FROM bookings WHERE id = ?", bid)
    s.check("and the stay no longer says who stayed, or who they asked us to bill",
            stay and not stay[0]["special_requests"] and stay[0]["booked_by_name"] == m.ERASED_MARKER
            and not stay[0]["booked_by_email"] and stay[0]["total_price"] == 400,
            detail=str(dict(stay[0])) if stay else "")

    s.section("The audit trail keeps who acted, and not them")
    lines = _rows("SELECT target, details, actor_user_id FROM audit_log WHERE action = ?",
                  TAG.lower() + "_did_something")
    text = " ".join(f"{r['target']} {r['details'] or ''}" for r in lines)
    s.check("their address, number and name are out of the lines already written",
            WHO not in text and PHONE not in text and "Solène Martin" not in text
            and text.count(m.AUDIT_ERASED) >= 3, detail=text)
    s.check("and each line still says who acted, and on which booking",
            len(lines) == 3 and all(r["actor_user_id"] == owner["id"] for r in lines)
            and any(f"room booking {TAG}S" == r["target"] for r in lines),
            detail=str([tuple(r) for r in lines]))
    oc.get("/admin/data-requests/export.json", query_string={"email": WHO})
    latest = _rows("SELECT target FROM audit_log WHERE action = 'guest_data_exported' "
                   "ORDER BY id DESC LIMIT 1")
    s.check("and a new export is written down without the address",
            latest and "@" not in (latest[0]["target"] or ""), detail=str([tuple(r) for r in latest]))
    source = inspect.getsource(m.allow_texting_number) + inspect.getsource(m.record_texting_consent)
    s.check("nor does taking a number off the stop list, or a yes to texts, write the number",
            'target=row["phone"]' not in source and "target=number" not in source)

    s.section("A letter is kept without its private link")
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Again", WHO, _harness.datetime_now()))
    conn.commit()
    conn.close()
    with m.app.test_request_context("/"):
        private = m.url_for("manage_booking", manage_token=f"tok{TAG}secret".lower(), _external=True)
        public = m.url_for("preview_home", _external=True)
    with m.app.test_request_context("/"):
        m.keep_guest_message(WHO, f"{TAG} Your booking", f"{TAG} Open it here: {private} — or see {public}",
                             delivered=True)
        m.write_guest_messages(m.g.pop("_guest_messages", []))
    kept = _rows("SELECT body FROM guest_messages WHERE subject = ?", f"{TAG} Your booking")
    s.check("the copy kept has the link taken out",
            kept and private not in kept[0]["body"] and m.PRIVATE_LINK_KEPT_AS in kept[0]["body"],
            detail=str([r["body"] for r in kept]))
    s.check("and keeps an ordinary one", kept and public in kept[0]["body"])
    conn = db()
    conn.execute("""INSERT INTO guest_messages (booking_id, channel, to_address, subject, body,
                    delivered, created_at) VALUES (NULL, 'email', ?, ?, ?, 1, ?)""",
                 (WHO, f"{TAG} From before", f"{TAG} Your link: {private}", _harness.datetime_now()))
    conn.commit()
    conn.close()
    m.init_db()
    old = _rows("SELECT body FROM guest_messages WHERE subject = ?", f"{TAG} From before")
    s.check("a copy kept before is cleaned at startup",
            old and private not in old[0]["body"], detail=str([r["body"] for r in old]))
    number = m.normalise_phone(PHONE)
    conn = db()
    conn.execute("UPDATE guests SET phone = ? WHERE email = ?", (PHONE, WHO))
    conn.execute("""INSERT INTO sms_outbox (phone, body, purpose, reason, created_at)
                    VALUES (?, ?, 'transactional', 'no provider', ?)""",
                 (number, f"{TAG} check in here: {private}", _harness.datetime_now()))
    m.file_guest_text(conn, number, f"{TAG} check in here: {private}", delivered=False)
    conn.commit()
    guest = conn.execute("SELECT * FROM guests WHERE email = ?", (WHO,)).fetchone()
    with m.app.test_request_context("/"):
        listed = [x for x in m.guest_messages(conn, guest) if (x["body"] or "").startswith(f"{TAG} check in")]
    conn.close()
    s.check("a text is kept without it too, and the record lists it once",
            len(listed) == 1 and private not in listed[0]["body"],
            detail=str([x["body"] for x in listed]))

    s.section("What nothing ever deleted")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()
    now = datetime.now(timezone.utc)
    conn = db()
    conn.execute("""INSERT INTO sms_outbox (phone, body, purpose, reason, created_at)
                    VALUES (?, ?, 'transactional', 'no provider', ?)""",
                 (number, f"{TAG} long ago", long_ago))
    for token, gone in ((f"{TAG.lower()}old", 40), (f"{TAG.lower()}recent", 10)):
        conn.execute("""INSERT INTO guest_sessions (email, token, created_at, expires_at)
                        VALUES (?, ?, ?, ?)""",
                     (WHO, token, (now - timedelta(days=gone + 2)).isoformat(),
                      (now - timedelta(days=gone)).isoformat()))
    conn.commit()
    cleared = m.run_health_notes_purge_job(conn)
    conn.close()
    s.check("a held text goes after the two years, as a held letter does",
            not _rows("SELECT id FROM sms_outbox WHERE body = ?", f"{TAG} long ago"),
            detail=str(cleared))
    s.check("a sign-in link a month after it stopped working, and not before",
            not _rows("SELECT id FROM guest_sessions WHERE token = ?", f"{TAG.lower()}old")
            and _rows("SELECT id FROM guest_sessions WHERE token = ?", f"{TAG.lower()}recent"))

    s.section("The notice says so")
    notice = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("that the private link is taken out of the copy kept, and texts kept as letters are",
            "taken out of the copy we keep" in notice
            and "The texts we send you are kept the same way" in notice)
