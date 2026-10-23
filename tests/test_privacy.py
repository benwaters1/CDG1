"""The privacy notice, and whether the software actually does what it says.

The page was written and never routed — the footer linked "Privacy Policy" to
"#" on every public page while the site took card payments and dietary and
medical notes from guests in the EU.

The more interesting half is the second one. A privacy notice is a set of
factual claims about the code, and a notice that says something the software
does not do is worse than no notice: it is a written statement to guests
about health data that is not true. So the claims are checked, not trusted.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-priv-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_police_register WHERE workshop_booking_id IN "
                 "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ? "
                 "OR reference_code LIKE ?)", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ? "
                 "OR reference_code LIKE ?", (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM police_register WHERE booking_id IN "
        "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("privacy notice")
    anon = m.app.test_client()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    s.section("It is reachable at all")
    r = anon.get("/privacy")
    s.check("the page is served", r.status_code == 200, detail=str(r.status_code))
    page = r.get_data(as_text=True)
    s.check("it is the notice, not a redirect to something else",
            "data controller" in page)
    s.check("it carries a review date, so a reader can judge whether it is current",
            bool(re.search(r"Last reviewed \d{1,2} \w+ \d{4}", page)))

    s.section("Every public page can reach it")
    # The specific failure this replaces: the link existed and went nowhere.
    for path in ("/", "/book", "/contact", "/restaurant"):
        body = anon.get(path, follow_redirects=True).get_data(as_text=True)
        if "Privacy" not in body:
            s.check(f"{path} links to it", False, detail="no privacy link at all")
            continue
        s.check(f"{path} links to it, not to '#'", 'href="/privacy"' in body,
                detail="the footer link is still a dead anchor")

    s.section("It does not promise a tracker-free site while carrying one")
    for bad, what in (("google-analytics.com", "Google Analytics"),
                      ("googletagmanager.com", "Tag Manager"),
                      ("connect.facebook.net", "the Facebook pixel"),
                      ("hotjar", "Hotjar")):
        s.check(f"no {what} on the public site", bad not in page.lower())

    s.section("Dietary and medical notes really are deleted afterwards")
    # The claim: "Deleted once the stay is over." Health data under GDPR, and
    # nothing in the app cleared it until this was checked.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, dietary_notes, created_at)
           VALUES (?, ?, 'Diner', 'd@example.invalid', 2, ?, 'confirmed', ?, ?)""",
        (TAG + "PAST", TAG + "tok1", _iso(-3), "severe nut allergy", now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, dietary_notes, created_at)
           VALUES (?, ?, 'Diner', 'd@example.invalid', 2, ?, 'confirmed', ?, ?)""",
        (TAG + "SOON", TAG + "tok2", _iso(3), "coeliac", now))
    conn.commit()

    ws = conn.execute("SELECT id FROM workshops LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
           notes, created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (ws, _iso(-10), _iso(-6), TAG + "over", now))
    conn.commit()
    done = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                        (TAG + "over",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, status, dietary_notes, medical_notes, created_at)
           VALUES (?, ?, ?, 'Maker', ?, 'confirmed', ?, ?, ?)""",
        (done, TAG + "WB", TAG + "wbtok", TAG + "m@example.invalid", "vegetarian",
         "carries an epipen", now))
    conn.commit()

    # Inside a request context because that is how it really runs: the
    # automation tick wraps every job in one, so log_audit can resolve who
    # (nobody, here) triggered it.
    with m.app.test_request_context():
        cleared = m.purge_health_notes(conn)
    s.check("something was cleared", sum(cleared.values()) >= 2, detail=str(cleared))

    past = conn.execute("SELECT dietary_notes FROM restaurant_bookings WHERE reference_code = ?",
                        (TAG + "PAST",)).fetchone()["dietary_notes"]
    soon = conn.execute("SELECT dietary_notes FROM restaurant_bookings WHERE reference_code = ?",
                        (TAG + "SOON",)).fetchone()["dietary_notes"]
    s.check("a dinner that has happened has its allergy removed", not past,
            detail=str(past))
    # The other half, and the one that would actually hurt somebody: a purge
    # that ran early would delete the allergy of a guest still to be cooked for.
    s.check("a dinner still to come keeps it", soon == "coeliac", detail=str(soon))

    wb = conn.execute(
        "SELECT dietary_notes, medical_notes FROM workshop_bookings WHERE guest_email = ?",
        (TAG + "m@example.invalid",)).fetchone()
    s.check("a finished atelier's dietary note is removed", not wb["dietary_notes"])
    s.check("and the medical note with it", not wb["medical_notes"])

    # What was deleted must not survive in the audit trail.
    leaked = conn.execute(
        """SELECT COUNT(*) AS c FROM audit_log
            WHERE action = 'health_notes_purged'
              AND (COALESCE(details, '') LIKE '%epipen%'
                OR COALESCE(details, '') LIKE '%allergy%')""").fetchone()["c"]
    s.check("the audit line does not quote what it deleted", leaked == 0)

    s.section("The allergy is not left behind in a second place")
    # The booking used to copy the dietary note verbatim into a staff
    # notification, a table nothing deletes — so the purge cleared one copy and
    # the notice's promise was still untrue.
    old_stamp = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    owner_id = conn.execute("SELECT id FROM users WHERE role='owner' LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO notifications (user_id, kind, title, body, created_at)
           VALUES (?, 'restaurant_booking', ?, 'severe shellfish allergy', ?)""",
        (owner_id, TAG + "old dinner", old_stamp))
    conn.execute(
        """INSERT INTO notifications (user_id, kind, title, body, created_at)
           VALUES (?, 'restaurant_booking', ?, 'coeliac', ?)""",
        (owner_id, TAG + "recent dinner", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    with m.app.test_request_context():
        m.purge_health_notes(conn)
    gone = conn.execute("SELECT body FROM notifications WHERE title = ?",
                        (TAG + "old dinner",)).fetchone()["body"]
    kept = conn.execute("SELECT body FROM notifications WHERE title = ?",
                        (TAG + "recent dinner",)).fetchone()["body"]
    s.check("an allergy in an old notification is cleared too", not gone,
            detail=str(gone))
    # A dinner still to come must keep it, exactly as the booking does.
    s.check("a recent one is left alone", kept == "coeliac", detail=str(kept))

    s.section("A new booking does not copy the allergy anywhere else")
    # The purge above cleans up history. This is the source: a dinner booked
    # today must not put the guest's allergy into a second table at all.
    pub = m.app.test_client()
    soon = (datetime.now(m.LOCAL_TZ).date() + timedelta(days=20)).isoformat()
    # /restaurant/book 404s while the restaurant is switched off, so without
    # this the POST does nothing and "no notification carries the allergy" is
    # true because there was no booking. The first version of this check
    # passed on exactly that empty result.
    # Being open is not enough: the restaurant also has an opening_date, seeded
    # to today + 42 days, and the booking form refuses anything before it. So
    # this POST was refused with "we're not taking reservations before ..." and
    # the booking was never made — the same empty-result failure this section
    # was written to close, one condition further along.
    was = conn.execute(
        "SELECT enabled, opening_date FROM restaurant_settings LIMIT 1").fetchone()
    reopened = bool(was) and not was["enabled"]
    was_opening = was["opening_date"] if was else None
    if reopened:
        conn.execute("UPDATE restaurant_settings SET enabled = 1")
    if was_opening and was_opening > soon:
        conn.execute("UPDATE restaurant_settings SET opening_date = ?",
                     ((datetime.now(m.LOCAL_TZ).date() - timedelta(days=1)).isoformat(),))
    conn.commit()

    before = conn.execute("SELECT COUNT(*) AS c FROM notifications").fetchone()["c"]
    pub.post("/restaurant/book", data={
        "guest_name": TAG + "Allergic", "guest_email": TAG + "a@example.invalid",
        "dinner_date": soon, "party_size": "2",
        "dietary_notes": "anaphylactic to peanuts",
    }, follow_redirects=True)

    # Prove the booking happened before asserting anything about it.
    made = conn.execute(
        "SELECT id, dietary_notes FROM restaurant_bookings WHERE guest_name = ?",
        (TAG + "Allergic",)).fetchone()
    s.check("the booking was actually created", made is not None,
            detail="without this, the leak check below passes on nothing")
    s.check("and the allergy IS kept on the booking itself",
            made and "peanut" in (made["dietary_notes"] or ""),
            detail=str(made["dietary_notes"]) if made else "")

    leaked = conn.execute(
        """SELECT COUNT(*) AS c FROM notifications
            WHERE kind = 'restaurant_booking'
              AND COALESCE(body, '') LIKE '%peanut%'""").fetchone()["c"]
    s.check("but it is not written into a staff notification", leaked == 0,
            detail=f"{leaked} notification(s) carry it")
    after = conn.execute("SELECT COUNT(*) AS c FROM notifications").fetchone()["c"]
    s.check("and somebody is still told the booking exists", after > before,
            detail=f"{before} -> {after}")

    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    if reopened:
        conn.execute("UPDATE restaurant_settings SET enabled = 0")
    if was_opening is not None:
        conn.execute("UPDATE restaurant_settings SET opening_date = ?", (was_opening,))
    conn.commit()


    s.section("Enquiries that came to nothing really are cleared")
    # The notice's second promise. "Once it is clear nothing came of them" was
    # not a period anybody could check, so the notice now says twelve months
    # and this is what makes that true.
    old = _iso(-400)
    recent = _iso(-30)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, status, created_at)
           VALUES (?, ?, 'wedding', 'Nobody', 'n@example.invalid', ?, 'new', ?)""",
        (TAG + "DEAD", TAG + "t1", old, now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, status, created_at)
           VALUES (?, ?, 'wedding', 'Booked', 'b@example.invalid', ?, 'confirmed', ?)""",
        (TAG + "HELD", TAG + "t2", old, now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, status, created_at)
           VALUES (?, ?, 'party', 'Live', 'l@example.invalid', ?, 'new', ?)""",
        (TAG + "LIVE", TAG + "t3", recent, now))
    conn.execute(
        """INSERT INTO waitlist_entries (name, email, desired_arrival, desired_departure,
           party_size, status, created_at)
           VALUES (?, 'w@example.invalid', ?, ?, 2, 'open', ?)""",
        (TAG + "waiter", old, old, now))
    conn.commit()

    with m.app.test_request_context():
        killed = m.purge_dead_enquiries(conn)

    def _gone(ref):
        return conn.execute(
            "SELECT COUNT(*) AS c FROM event_inquiries WHERE reference_code = ?",
            (ref,)).fetchone()["c"] == 0

    s.check("an enquiry nobody took up is deleted", _gone(TAG + "DEAD"))
    # The one that must survive: it happened, it has a price, and it falls
    # under accounting retention rather than this rule.
    s.check("one that became a confirmed event is NOT", not _gone(TAG + "HELD"),
            detail="deleting this would destroy the record of an event that took place")
    s.check("and a recent enquiry is left alone", not _gone(TAG + "LIVE"))
    s.check("a waitlist request for dates long past is cleared",
            conn.execute("SELECT COUNT(*) AS c FROM waitlist_entries WHERE name = ?",
                         (TAG + "waiter",)).fetchone()["c"] == 0)
    s.check("something was reported as cleared", sum(killed.values()) >= 2,
            detail=str(killed))

    s.section("The notice does not overstate what unsubscribing does")
    # It sets a flag and keeps the address, which is right — it is what stops
    # an old import re-adding somebody. The notice has to say so.
    page = anon.get("/privacy").get_data(as_text=True)
    s.check("it says the address is kept on a do-not-write list",
            "do-not-write list" in page)
    s.check("and that deletion can be asked for", "delete it" in page)
    s.check("the enquiry period is a number a reader can check",
            "twelve months" in page)

    s.section("The police register really does go six months after the stay")
    # The row on this page most worth knowing is true: a name and a home
    # address, held for every guest who is not a French national, under a
    # legal obligation with a stated end.
    booking = conn.execute(
        "SELECT id FROM bookings ORDER BY id DESC LIMIT 1").fetchone()
    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    if booking and room:
        def _stay(ref, departed_days_ago, nights=3):
            left = m.house_today() - timedelta(days=departed_days_ago)
            conn.execute(
                """INSERT INTO bookings (room_id, reference_code, manage_token,
                           guest_name, guest_email, arrival_date,
                           departure_date, party_size, status, total_price,
                           created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?)""",
                (room["id"], TAG + ref, (TAG + ref).lower(), TAG + ref,
                 TAG + ref + "@example.invalid",
                 (left - timedelta(days=nights)).isoformat(),
                 left.isoformat(), now))
            bid = conn.execute(
                "SELECT last_insert_rowid() AS id").fetchone()["id"]
            # Recorded on ARRIVAL, which is when a fiche is filled in. That
            # is what makes the long-stay case below discriminating: stamp
            # them all with today and the two datings cannot be told apart,
            # which is how the first version of this passed either way.
            conn.execute(
                """INSERT INTO police_register (booking_id, surname,
                           first_names, nationality, recorded_at)
                   VALUES (?, ?, 'A', 'GB', ?)""",
                (bid, TAG + ref,
                 (left - timedelta(days=nights)).isoformat() + "T15:00:00"))
            return bid

        old_stay = _stay("old", 400)
        recent = _stay("recent", 30)
        # A long stay whose fiche was written on ARRIVAL, months before the
        # guest left. Dating the six months from the fiche rather than the
        # departure would take this one early -- which is the mistake the
        # function's own docstring says it is avoiding.
        long_stay = _stay("long", 20, nights=200)   # arrived 220 days ago
        conn.commit()

        cleared = m.purge_police_register(conn)
        conn.commit()

        def _still(bid):
            return conn.execute(
                "SELECT COUNT(*) AS c FROM police_register WHERE booking_id = ?",
                (bid,)).fetchone()["c"] > 0

        s.check("a stay that ended over a year ago has no fiche left",
                not _still(old_stay),
                detail="the notice says six months, and names the law it is "
                       "keeping it under")
        s.check("one that ended last month still has its fiche",
                _still(recent),
                detail="the house has to be able to produce it if it is asked")
        s.check("and a long stay is dated from when the guest LEFT",
                _still(long_stay),
                detail="the fiche was written on arrival two hundred days "
                       "before departure; dating the six months from the "
                       "fiche would delete it while the law still wants it")
        s.check("and it says what it cleared",
                sum(cleared.values()) >= 1, detail=str(cleared))

        s.section("And the purge is wired to something that actually runs")
        # A retention rule in a function nobody calls is a paragraph on a page.
        import inspect
        job = inspect.getsource(m.run_health_notes_purge_job)
        code = "\n".join(l.split("#")[0] for l in job.splitlines())
        s.check("the housekeeping job calls it",
                "purge_police_register" in code,
                detail="written and never called, the notice is a statement "
                       "about code that does not run")
    else:
        s.check("a room and a booking exist to attach a fiche to", False,
                detail="reported rather than skipped: every check above would "
                       "pass on nothing")

    _online_checkin(s, conn, now)
    _microsoft_and_anthropic(s, conn)
    _cleanup(conn)
    return s


def _online_checkin(s, conn, now):
    """The online check-in for the house's own guests (22 October 2026), and
    each thing the notice says about it, checked against the code."""
    s.section("Online check-in: what it asks, and how long it is kept")
    page = m.app.test_client().get("/privacy").get_data(as_text=True)
    text = re.sub(r"\s+", " ", page)
    s.check("the notice says there is an online check-in, and what it asks",
            "If you check in online" in text
            and "for each adult staying a name and a nationality" in text)

    # "only for someone who is not French does it ask for the rest": the form
    # is read so that a French adult's block keeps nothing past the name.
    stay = {"party_size": 2, "children_asked": True}
    form = {"p1_first_names": "Élodie", "p1_surname": "Rouvière", "p1_nationality": "French",
            "p1_born_on": "1980-01-01", "p1_born_at": "Lyon", "p1_home": "Lyon",
            "p1_phone": "+33 6 00 00 00 00", "p1_email": "e@example.invalid",
            "p2_first_names": "Ada", "p2_surname": "Lovelace", "p2_nationality": "British",
            "p2_born_on": "1984-06-11", "p2_born_at": "Leeds", "p2_home": "York",
            "p2_signed_name": "Ada Lovelace", "agree": "on"}
    with m.app.test_request_context():
        got, problem = m.read_direct_checkin(form, stay)
    french, british = (got or {"people": [{}, {}]})["people"]
    s.check("a French adult's birth, address, mobile and email are not kept",
            not problem and not ({"born_on", "born_at", "home_address", "phone", "email"}
                                 & set(french)), detail=str(problem or french))
    s.check("and everything else the fiche lists is, for anybody who is not French",
            british.get("born_on") == "1984-06-11" and british.get("born_at") == "Leeds"
            and british.get("home_address") == "York")
    s.check("the notice says a French national's register line is a name and French",
            "notes only your name and that you are French" in text)

    s.check("the fiche paragraph names the mobile and email the register keeps",
            "your mobile number and email address" in text
            and {"phone", "email"} <= {c[1] for c in conn.execute(
                "PRAGMA table_info(police_register)")})
    s.check("and says a child under 15 goes on it as a number, nothing else",
            "A child under 15 travelling with you goes on your fiche as a number" in text
            and "children_under_15" in {c[1] for c in conn.execute(
                "PRAGMA table_info(police_register)")})

    # The signature and the passport (23 October 2026): signed, kept with the
    # fiche -- so the six-month purge takes it -- and the passport seen, never
    # copied, which the CNIL requires and the schema can be read to show.
    s.check("the notice says the fiche is signed and the signature kept with it",
            "you sign on your own screen when you check in online" in text
            and "the signature is kept with the fiche" in text)
    cols = {c[1] for c in conn.execute("PRAGMA table_info(police_register)")}
    s.check("and the signature is a column of the fiche itself, purged with it",
            {"signature", "signed_at"} <= cols)
    s.check("the notice says the passport is seen and never copied",
            "we keep no copy or scan of it" in text
            and "note only that it was seen, when, and by whom" in text)
    s.check("and nothing in any register could hold a copy",
            not any({c[1] for c in conn.execute(f"PRAGMA table_info({t})")}
                    & {"passport", "passport_photo", "scan", "id_scan", "photo",
                       "photo_filename", "document"}
                    for t in ("police_register", "channel_police_register",
                              "workshop_police_register"))
            and {"id_checked_at", "id_checked_by_user_id"} <= cols)

    # "a stay, a table, a workshop -- is deleted once that booking is over"
    s.check("the notice counts a stay among the notes deleted afterwards",
            "a stay, a table, a workshop" in text)
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    left = m.house_today() - timedelta(days=1)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
               guest_email, arrival_date, departure_date, party_size, status,
               total_price, created_at, dietary_notes)
           VALUES (?, ?, ?, 'Diner', ?, ?, ?, 1, 'confirmed', 100, ?, 'no gluten')""",
        (room["id"], TAG + "CI", (TAG + "ci").lower(), TAG + "ci@example.invalid",
         (left - timedelta(days=2)).isoformat(), left.isoformat(), now))
    conn.commit()
    with m.app.test_request_context():
        m.purge_health_notes(conn)
    conn.commit()
    s.check("and a stay's note really is cleared once it is over",
            conn.execute("SELECT dietary_notes FROM bookings WHERE reference_code = ?",
                         (TAG + "CI",)).fetchone()[0] is None)

    # "for a room, a stay booked through Booking.com, or an atelier, which ends
    # on its last day"
    s.check("the notice says an atelier's fiche goes six months after its last day",
            "or an atelier, which ends on its last day" in text)
    ws = conn.execute("SELECT id FROM workshops LIMIT 1").fetchone()["id"]
    ended = m.house_today() - timedelta(days=6 * 31 + 3)
    sid = conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
               notes, created_at) VALUES (?, ?, ?, 8, ?, ?)""",
        (ws, (ended - timedelta(days=4)).isoformat(), ended.isoformat(),
         TAG + "fiche", now)).lastrowid
    wid = conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
               guest_name, guest_email, status, created_at)
           VALUES (?, ?, ?, 'Maker', ?, 'confirmed', ?)""",
        (sid, TAG + "WF", TAG + "wftok", TAG + "wf@example.invalid", now)).lastrowid
    conn.execute("""INSERT INTO workshop_police_register (workshop_booking_id, surname,
                    first_names, nationality, recorded_at) VALUES (?, 'X', 'Y', 'Swiss', ?)""",
                 (wid, now))
    conn.commit()
    with m.app.test_request_context():
        m.purge_police_register(conn)
    conn.commit()
    s.check("and an atelier's fiche really does go then",
            not conn.execute("SELECT 1 FROM workshop_police_register WHERE workshop_booking_id = ?",
                             (wid,)).fetchone())


def _microsoft_and_anthropic(s, conn):
    """The two companies the notice names for the Outlook add-in and the house
    calendar, and each sentence it says about them, checked against the code
    rather than trusted."""
    import inspect
    import json as _json

    s.section("Microsoft and Anthropic, and what each is given")
    page = m.app.test_client().get("/privacy").get_data(as_text=True)
    s.check("the notice names Microsoft", "<dt>Microsoft</dt>" in page)
    s.check("and Anthropic", "<dt>Anthropic</dt>" in page)
    now = datetime.now(timezone.utc).isoformat()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    gid = conn.execute(
        """INSERT INTO guests (name, email, phone, dietary_notes, access_needs, created_at)
           VALUES (?, ?, '+33 6 10 20 30 40', 'ZZPRIV shellfish', 'ZZPRIV wheelchair', ?)""",
        (TAG + "Claimed", TAG + "claims@example.invalid", now)).lastrowid
    bid = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             guest_phone, arrival_date, departure_date, party_size, status, special_requests,
             total_price, created_at)
           VALUES (?, ?, ?, ?, ?, '+33 6 10 20 30 40', ?, ?, 2, 'confirmed', 'ZZPRIV pillow',
                   777, ?)""",
        (room["id"] if room else 0, TAG + "CLAIM", TAG + "claimtok", TAG + "Claimed",
         TAG + "claims@example.invalid", _iso(10), _iso(12), now)).lastrowid
    conn.commit()
    with m.app.test_request_context("/"):
        dossier = m.guest_dossier(conn, gid, money=True, letters=True)
        sent = _json.dumps(m.dossier_for_prompt(dossier), default=str)
        s.check("the drafter is never sent a dietary note", "ZZPRIV shellfish" not in sent)
        s.check("nor an access need", "ZZPRIV wheelchair" not in sent)
        s.check("nor a telephone", "10 20 30 40" not in sent)
        conn.execute("UPDATE guests SET restricted_at = ? WHERE id = ?", (now, gid))
        conn.commit()
        ctx = m.gather_reply_context(conn, TAG + "claims@example.invalid",
                                     caller={"owner": True, "user_id": None, "name": "x",
                                             "how": "test"}, compose_text="Hello")
        s.check("and for somebody who asked us to stop, nothing but the email",
                ctx["guest"] is None and not ctx["correspondence"]["current_thread"]
                and TAG + "CLAIM" not in _json.dumps(ctx, default=str))
        items = [i for i in m.house_calendar_items(conn, m.house_today(),
                                                   m.house_today() + timedelta(days=30))
                 if i["key"] == f"stay:{bid}"]
        text = _json.dumps(items, ensure_ascii=False)
        s.check("the staff calendar has the guest's name", items and TAG + "Claimed" in text)
        s.check("and not their email, telephone, money or requests",
                "claims@example.invalid" not in text and "10 20 30 40" not in text
                and "777" not in text and "ZZPRIV pillow" not in text, detail=text[:200])
    reads = "".join(inspect.getsource(f) for f in (
        m.guest_email_history, m.email_bodies, m.mailbox_voice_samples, m.reply_correspondence))
    s.check("emails are read and held in memory, never written to the database",
            "conn.execute" not in reads and "INSERT" not in reads.upper().replace("INSERT_", ""))
    s.check("for a few minutes", m.EMAIL_HISTORY_TTL <= 600 and m.EMAIL_BODY_TTL <= 600)
    conn.execute("DELETE FROM bookings WHERE id = ?", (bid,))
    conn.execute("DELETE FROM guests WHERE id = ?", (gid,))
    conn.commit()


if __name__ == "__main__":
    print(run().report())
