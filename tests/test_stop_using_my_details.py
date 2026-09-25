"""Stop using my details: recorded, honoured, and on the guest's own page.

What there was: the privacy notice lists the right to ask the house to stop
using what it holds, and nothing recorded that anybody had asked -- so the
next campaign, the next invitation to review, the next birthday greeting went
out all the same. And every one of the rights the notice lists meant writing
in and waiting: a copy of what we hold, an end to the offers, being forgotten.

What this holds:

  - The owner records it, with how they asked; the record says so, with who
    wrote it down; it is on their history; it is lifted only at their word.
  - While it stands nothing goes to them for the house's own reasons: no
    campaign, no newsletter, no promo code, no marketing text, no greeting on
    a date that matters, no invitation to review us -- daily or by hand -- and
    no request for feedback. What their booking needs still goes.
  - The count the owner confirms for a campaign leaves them out, and a send
    that holds anybody back says so: to the owner, on the trail, and in the
    line the automation page shows.
  - A merge keeps it.
  - From their own page they can take a copy of everything, stop the offers,
    ask us to stop using their details, and ask to be forgotten -- which
    becomes a task for the owner, a month out, as the law gives.
  - The notice says so.
"""
import json
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today, visible_text
import _harness

m = _harness.m
TAG = "ZZSU"
WHO = f"{TAG.lower()}@example.invalid"
# Somebody who asked nothing of us, so a list that leaves them out too is seen.
NEIGHBOUR = f"{TAG.lower()}.neighbour@example.invalid"
HELD = "1 held back because they asked us to stop using their details"
PHONE = "+33 6 77 88 99 00"
REVIEW_SAVED = []


def _cleanup():
    conn = db()
    try:
        like = (TAG + "%",)
        ids = "(SELECT id FROM guests WHERE name LIKE ?)"
        for table in ("consent_events", "guest_profile_changes", "guest_messages"):
            conn.execute(f"DELETE FROM {table} WHERE guest_id IN {ids}", like)
        conn.execute("DELETE FROM consent_events WHERE LOWER(email) LIKE ? OR phone = ?",
                     (TAG.lower() + "%", m.normalise_phone(PHONE)))
        conn.execute("DELETE FROM sms_consents WHERE phone = ?", (m.normalise_phone(PHONE),))
        conn.execute("DELETE FROM email_optouts WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM newsletter_subscribers WHERE LOWER(email) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM campaign_sends WHERE LOWER(recipient_email) LIKE ?", (TAG.lower() + "%",))
        conn.execute("DELETE FROM campaign_templates WHERE name LIKE ?", like)
        conn.execute("DELETE FROM promo_codes WHERE code LIKE ?", like)
        conn.execute("DELETE FROM guest_feedback WHERE booking_id IN "
                     "(SELECT id FROM bookings WHERE reference_code LIKE ?)", like)
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", like)
        conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("A guest asked%",))
        conn.execute("DELETE FROM email_outbox WHERE LOWER(to_address) LIKE ?", (TAG.lower() + "%",))
        conn.execute("UPDATE guests SET merged_into_id = NULL WHERE name LIKE ?", like)
        conn.execute("DELETE FROM guests WHERE name LIKE ?", like)
        # The house's own review page, put back as it was.
        while REVIEW_SAVED:
            value = REVIEW_SAVED.pop()
            if value is None:
                conn.execute("DELETE FROM app_settings WHERE key = ?", (m.REVIEW_LINK_SETTING,))
            else:
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                             (m.REVIEW_LINK_SETTING, value))
        conn.commit()
    finally:
        conn.close()


def _one(sql, *args):
    conn = db()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _guest(name, email, **more):
    conn = db()
    cols = ["name", "email", "created_at"] + list(more)
    conn.execute(f"INSERT INTO guests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 (f"{TAG} {name}", email, _harness.datetime_now(), *more.values()))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return gid


def run():
    s = Suite("Stop using my details")
    oc, ec, owner, _emp = clients()
    _cleanup()
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, *a, **k: sent.append((to, subject)) or True
    try:
        _run(s, oc, owner, sent)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, owner, sent):
    today = house_today()
    gid = _guest("Guest", WHO, phone=PHONE, birthday=f"{today.month:02d}-{today.day:02d}")
    _guest("Neighbour", NEIGHBOUR)
    number = m.normalise_phone(PHONE)
    now = _harness.datetime_now()
    room = _harness.ensure_room()
    conn = db()
    conn.execute("""INSERT INTO newsletter_subscribers (email, token, source, created_at, confirmed_at)
                    VALUES (?, ?, 'site', ?, ?)""", (WHO, f"tok{TAG}".lower(), now, now))
    conn.execute("INSERT INTO sms_consents (phone, source, granted_at) VALUES (?, 'at the desk', ?)",
                 (number, now))
    arrive = today - timedelta(days=12)
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at, linked_guest_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?, ?)""",
                 (room["id"], f"{TAG}S", f"toks{TAG}".lower(), f"{TAG} Guest", WHO,
                  arrive.isoformat(), (arrive + timedelta(days=2)).isoformat(), now, gid))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?", (f"{TAG}S",)).fetchone()["id"]
    conn.execute("""INSERT INTO guest_feedback (booking_id, guest_name, rating, comment, submitted_at)
                    VALUES (?, ?, 5, 'Wonderful', ?)""",
                 (bid, f"{TAG} Guest", (m.datetime.now(m.timezone.utc) - timedelta(days=30)).isoformat()))
    # A review page to send them to, or the daily job asks nobody and proves nothing.
    saved = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                         (m.REVIEW_LINK_SETTING,)).fetchone()
    REVIEW_SAVED.append(saved["value"] if saved else None)
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
                 (m.REVIEW_LINK_SETTING, "https://example.invalid/review"))
    fid = conn.execute("SELECT id FROM guest_feedback WHERE booking_id = ?", (bid,)).fetchone()["id"]
    conn.execute("""INSERT INTO campaign_templates (name, subject, body, trigger_active, created_at)
                    VALUES (?, 'Spring', 'Come back in spring.', 0, ?)""", (f"{TAG} Spring", now))
    template = conn.execute("SELECT * FROM campaign_templates WHERE name = ?",
                            (f"{TAG} Spring",)).fetchone()
    conn.commit()
    conn.close()

    s.section("The owner records it")
    oc.post(f"/guests/{gid}/restrict", data={"on": "1", "how": "by email on Tuesday"})
    row = _one("SELECT * FROM guests WHERE id = ?", gid)
    s.check("it is recorded, with how they asked and who wrote it down",
            row["restricted_at"] and row["restricted_how"] == "by email on Tuesday"
            and row["restricted_by_user_id"] == owner["id"], detail=str(dict(row)))
    page = visible_text(oc.get(f"/guests/{gid}").get_data(as_text=True))
    s.check("the record says so, and who wrote it down",
            "They asked us to stop using their details" in page and owner["name"] in page)
    conn = db()
    lines = [x["title"] for x in m.guest_timeline(conn, gid) if x["kind"] == "Consent"]
    conn.close()
    s.check("and it is on their history", any(t.startswith("Using their details: no") for t in lines),
            detail=str(lines))

    s.section("While it stands, nothing goes for the house's own reasons")
    conn = db()
    result = m.send_campaign(conn, template, {WHO: f"{TAG} Guest"}, owner["id"])
    news = [r["email"] for r in m.newsletter_recipients(conn)]
    blast = m.promo_blast_recipients(conn, ["room", "newsletter"])
    texted = m.can_text(conn, number, "marketing")
    greeted = [x for x in m.upcoming_guest_dates(conn, within_days=2) if x["guest"]["id"] == gid]
    conn.close()
    s.check("a campaign is withheld from them, and says so",
            result.get("withheld") == 1 and result["sent"] == 0 and not sent,
            detail=str(result))
    s.check("they are off the newsletter's list", WHO not in news)
    s.check("and a promo code's", WHO not in blast)
    s.check("no marketing text", texted[0] is None and "stop using" in (texted[1] or ""),
            detail=str(texted))
    s.check("no greeting on a date that matters", not greeted, detail=str(greeted)[:200])
    conn = db()
    m.run_review_invitation_job(conn, days_after=1)
    invited = _one("SELECT review_invited_at FROM guest_feedback WHERE id = ?", fid)
    booking = conn.execute("SELECT bookings.*, rooms.name AS room_name FROM bookings "
                           "JOIN rooms ON rooms.id = bookings.room_id WHERE bookings.id = ?",
                           (bid,)).fetchone()
    conn.execute("DELETE FROM guest_feedback WHERE id = ?", (fid,))
    conn.commit()
    with m.app.test_request_context("/"):
        asked = m.ask_room_feedback(conn, booking)
    conn.close()
    s.check("no invitation to review us", not [x for x in sent if x[0] == WHO],
            detail=str(sent))
    s.check("and no request for feedback", asked is False)
    conn = db()
    conn.execute("""INSERT INTO guest_feedback (booking_id, guest_name, rating, comment, submitted_at)
                    VALUES (?, ?, 5, 'Wonderful', ?)""", (bid, f"{TAG} Guest", now))
    fid = conn.execute("SELECT id FROM guest_feedback WHERE booking_id = ?", (bid,)).fetchone()["id"]
    conn.commit()
    conn.close()
    r = oc.post(f"/admin/feedback/{fid}/ask-for-a-review", follow_redirects=True)
    s.check("not even by hand",
            not [x for x in sent if x[0] == WHO] and "stop using their details" in " ".join(flashes(r)),
            detail=str(flashes(r)))
    conn = db()
    with m.app.test_request_context("/"):
        m.write_about_stay(conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone(),
                           f"{TAG} About your stay", "Your room is ready.")
    conn.close()
    s.check("while what their booking needs still goes to them",
            any(to == WHO and subject == f"{TAG} About your stay" for to, subject in sent),
            detail=str(sent))

    s.section("The count the owner confirms is the count that goes")
    mark = len(sent)
    conn = db()
    counted = m.campaign_audience(conn, ["profiles"])
    conn.close()
    s.check("the guest list a campaign is counted from leaves them out, and nobody else",
            WHO not in counted and NEIGHBOUR in counted, detail=f"{len(counted)} counted")
    # However an audience comes to have them in it, the send holds them back --
    # and says so where the owner reads what it did.
    real_audience, real_blast = m.campaign_audience, m.promo_blast_recipients
    both = {WHO: f"{TAG} Guest", NEIGHBOUR: f"{TAG} Neighbour"}
    m.campaign_audience = lambda *a, **k: dict(both)
    m.promo_blast_recipients = lambda *a, **k: dict(both)
    try:
        told = " ".join(flashes(oc.post(
            f"/admin/emails/{template['id']}/send",
            data={"segments": "profiles", "confirm_count": "2"}, follow_redirects=True)))
        conn = db()
        conn.execute("""INSERT INTO promo_codes (code, discount_type, discount_value, created_at)
                        VALUES (?, 'percent', 10, ?)""", (f"{TAG}TEN", now))
        code = conn.execute("SELECT id FROM promo_codes WHERE code = ?", (f"{TAG}TEN",)).fetchone()["id"]
        conn.commit()
        conn.close()
        blasted = " ".join(flashes(oc.post(
            f"/admin/promo-codes/{code}/blast/send",
            data={"segment": "room", "subject": "Ten off", "body": "Use {promo_code}."},
            follow_redirects=True)))
    finally:
        m.campaign_audience, m.promo_blast_recipients = real_audience, real_blast
    trail = _one("SELECT details FROM audit_log WHERE action = 'campaign_sent' AND target = ? "
                 "ORDER BY id DESC LIMIT 1", template["name"])
    promo_trail = _one("SELECT details FROM audit_log WHERE action = 'promo_blast_sent' "
                       "AND target = ? ORDER BY id DESC LIMIT 1", f"{TAG}TEN")
    s.check("an audience that has them anyway: they are held back, the rest are sent to",
            not [x for x in sent[mark:] if x[0] == WHO]
            and len([x for x in sent[mark:] if x[0] == NEIGHBOUR]) == 2, detail=str(sent[mark:]))
    s.check("and the owner is told how many, and why", HELD in told, detail=told)
    s.check("and so is the trail", bool(trail) and HELD in (trail["details"] or ""),
            detail=str(trail and dict(trail)))
    s.check("a promo code's send tells the owner too", HELD in blasted, detail=blasted)
    s.check("and its trail", bool(promo_trail) and HELD in (promo_trail["details"] or ""),
            detail=str(promo_trail and dict(promo_trail)))
    # The campaigns that fire on their own build their own audience, and it is
    # there somebody is held back for real: arriving in a while, having asked
    # us to stop. The line the job returns is what the automation page shows.
    later = today + timedelta(days=900)
    conn = db()
    conn.execute("""INSERT INTO campaign_templates (name, subject, body, trigger_event,
                    trigger_offset_days, trigger_active, created_at)
                    VALUES (?, 'Before you come', 'See you soon.', 'arrival', -900, 1, ?)""",
                 (f"{TAG} Before", now))
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at, linked_guest_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?, ?)""",
                 (room["id"], f"{TAG}T", f"tokt{TAG}".lower(), f"{TAG} Guest", WHO,
                  later.isoformat(), (later + timedelta(days=2)).isoformat(), now, gid))
    conn.commit()
    job = m.run_campaign_triggers_job(conn)
    conn.execute("UPDATE campaign_templates SET trigger_active = 0 WHERE name = ?", (f"{TAG} Before",))
    conn.execute("DELETE FROM bookings WHERE reference_code = ?", (f"{TAG}T",))
    conn.commit()
    conn.close()
    s.check("a campaign that fires on its own holds them back, and the job says so",
            "held back because they asked us to stop using their details" in job
            and not [x for x in sent[mark:] if x[0] == WHO], detail=job)

    s.section("Lifted only at their word, and carried by a merge")
    oc.post(f"/guests/{gid}/restrict", data={"on": "0", "how": "they rang to say so"})
    conn = db()
    back_on = WHO in [r["email"] for r in m.newsletter_recipients(conn)]
    conn.close()
    s.check("lifted, they are written to again as anybody is", back_on)
    other = _guest("Other profile", f"{TAG.lower()}.other@example.invalid")
    oc.post(f"/guests/{other}/restrict", data={"on": "1", "how": "in person"})
    oc.post(f"/guests/{gid}/merge", data={"merge_id": str(other)})
    s.check("and a merge keeps it", bool(_one("SELECT restricted_at FROM guests WHERE id = ?", gid)[0]))
    oc.post(f"/guests/{gid}/restrict", data={"on": "0", "how": "tidying up"})

    s.section("On their own page")
    conn = db()
    with m.app.test_request_context("/"):
        token = m.guest_portal_token(conn, WHO)
    conn.commit()
    conn.close()
    guest = m.app.test_client()
    page = guest.get(f"/my/{token}").get_data(as_text=True)
    s.check("the page offers all four",
            f"/my/{token}/data.json" in page and f"/my/{token}/stop-offers" in page
            and f"/my/{token}/stop-using" in page and f"/my/{token}/forget" in page)
    r = guest.get(f"/my/{token}/data.json")
    copy = json.loads(r.get_data(as_text=True)) if r.status_code == 200 else {}
    s.check("a copy of everything, as a file, never cached",
            "attachment" in r.headers.get("Content-Disposition", "")
            and r.headers.get("Cache-Control") == "no-store"
            and any(b.get("reference_code") == f"{TAG}S"
                    for b in copy.get("tables", {}).get("bookings", [])),
            detail=str(sorted(copy.get("tables", {}))))
    conn = db()
    took = [x for x in m.history_for(conn, "guest", gid)["entries"]
            if x["action"] == "guest_downloaded_their_data"]
    conn.close()
    s.check("and taking it is on their record, as theirs, without their address",
            len(took) == 1 and m.audit_who(took[0]) == "The guest"
            and "@" not in (took[0]["target"] or "") + (took[0]["details"] or ""),
            detail=str([dict(x) for x in took]))
    conn = db()
    conn.execute("INSERT OR IGNORE INTO sms_consents (phone, source, granted_at) VALUES (?, 'x', ?)",
                 (number, now))
    conn.execute("UPDATE newsletter_subscribers SET confirmed_at = ?, unsubscribed_at = NULL "
                 "WHERE email = ?", (now, WHO))
    conn.execute("DELETE FROM email_optouts WHERE email = ?", (WHO,))
    conn.commit()
    conn.close()
    guest.post(f"/my/{token}/stop-offers")
    s.check("no more offers: newsletter, email and marketing texts, each written down as theirs",
            _one("SELECT 1 FROM email_optouts WHERE email = ?", WHO)
            and _one("SELECT unsubscribed_at FROM newsletter_subscribers WHERE email = ?", WHO)[0]
            and not _one("SELECT 1 FROM sms_consents WHERE phone = ?", number)
            and _one("SELECT COUNT(*) FROM consent_events WHERE how = ? AND guest_id = ?",
                     m.PORTAL_HOW, gid)[0] >= 3)
    guest.post(f"/my/{token}/stop-using")
    s.check("asking to stop using their details is recorded at once, as theirs",
            _one("SELECT restricted_how FROM guests WHERE id = ?", gid)[0] == m.PORTAL_HOW)
    s.check("and the owner is told",
            _one("SELECT id FROM tasks WHERE title = ?",
                 f"A guest asked us to stop using their details (profile #{gid})") is not None)
    guest.post(f"/my/{token}/forget")
    guest.post(f"/my/{token}/forget")
    tasks = conn_rows = _one("SELECT COUNT(*), MIN(due_date) FROM tasks WHERE title = ? AND status = 'open'",
                             f"A guest asked to be forgotten (profile #{gid})")
    s.check("asking to be forgotten is a task for the owner, once, due within the month",
            # A month, as the law gives -- written here, not read from the app.
            tasks[0] == 1 and tasks[1] == (house_today() + timedelta(days=30)).isoformat(),
            detail=str(tuple(conn_rows)))
    s.check("and nothing is erased by the page itself",
            _one("SELECT id FROM guests WHERE id = ?", gid) is not None)
    line = _one("SELECT actor_user_id, target FROM audit_log WHERE action = 'guest_asked_to_be_forgotten' "
                "ORDER BY id DESC LIMIT 1")
    s.check("which is on the audit trail as the guest's, without their address",
            line and line["actor_user_id"] is None and "@" not in line["target"])

    s.section("The notice says so")
    notice = visible_text(m.app.test_client().get("/privacy").get_data(as_text=True))
    s.check("what stopping the use of their details stops, and where to ask for each thing",
            "If you ask us to stop using your details" in notice
            and "From your account page" in notice and "within a month" in notice)
