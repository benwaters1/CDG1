"""A guest says the stay was poor, and somebody finds out.

Before this the review was inserted and that was the end of it. No email,
nothing on the owner's home page, no task, no record that anybody had read
it. The owner had to go and look, and the only reason to look would be
already suspecting. Yesterday made that worse rather than better: room
guests are now ASKED for a review, so the volume goes up while the silence
stays the same.

There was no way to answer one either. A review was invisible or featured on
the front page, with nothing in between and no right of reply.

AND THE FEATURE BUTTON DID NOTHING. book_rooms ran a query for featured
reviews on every single page load and the template rendered none of them —
the section it belonged in had been replaced by three hardcoded quotes. So
the one moderation control the house had was a button with no effect, and
the test written for it the day before checked that the FLAG changed rather
than that anything happened. That is the whole reason the last section of
this file loads the page and looks for the words.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTREV"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM guest_feedback WHERE booking_id IN
                    (SELECT id FROM bookings WHERE reference_code LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE '%out of 5%'")
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM audit_log WHERE action LIKE 'feedback_%'")
    conn.commit()
    conn.close()


def _stay(ref, name="Amelie Fontaine"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    left = datetime.now(m.LOCAL_TZ).date() - timedelta(days=2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + ref, TAG.lower() + "tok" + ref, name,
         f"{TAG.lower()}{ref.lower()}@example.invalid",
         (left - timedelta(days=3)).isoformat(), left.isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("When a guest says it was poor")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    today = datetime.now(m.LOCAL_TZ).date()

    s.section("A poor review reaches somebody the same day")
    # The timing is the point. A guest who left this morning and has just said
    # the room was cold can still be answered today; the same words found on a
    # dashboard three weeks later are a fact about the past.
    bad = _stay("BAD", name=TAG + " Bernard Roux")
    r = anon.post(f"/feedback/{bad['manage_token']}",
                  data={"rating": "2", "comment": "The room was cold and nobody came."},
                  follow_redirects=True)
    row = _one("SELECT * FROM guest_feedback WHERE booking_id = ?", (bad["id"],))
    s.check("the review is recorded", row is not None, detail=str(flashes(r)))
    told = _one("SELECT * FROM email_outbox WHERE subject LIKE '%out of 5%' "
                "ORDER BY id DESC LIMIT 1")
    s.check("and the house is written to about it", told is not None,
            detail="it used to be inserted and that was the end of it")
    s.check("with the rating in the subject, so it reads at a glance",
            told and "2 out of 5" in (told["subject"] or ""),
            detail=str(told["subject"]) if told else "")
    s.check("and what they actually said in the body",
            told and "room was cold" in (told["body"] or ""),
            detail=(told["body"] or "")[:90] if told else "")
    s.check("it says nothing has been published",
            told and "nothing has been published" in (told["body"] or "").lower(),
            detail="a guest complaining is not a guest published")

    s.section("A good one does not")
    before = _one("SELECT COUNT(*) AS c FROM email_outbox WHERE subject LIKE '%out of 5%'")["c"]
    good = _stay("GOOD")
    anon.post(f"/feedback/{good['manage_token']}",
              data={"rating": "5", "comment": "Wonderful, thank you."},
              follow_redirects=True)
    s.check("five out of five interrupts nobody",
            _one("SELECT COUNT(*) AS c FROM email_outbox WHERE subject LIKE '%out of 5%'")["c"] == before,
            detail="something that arrives for every review is something "
                   "nobody opens for the one that matters")
    s.check("but it is still recorded",
            _one("SELECT COUNT(*) AS c FROM guest_feedback WHERE booking_id = ?",
                 (good["id"],))["c"] == 1)

    s.section("It stays on the owner's home page until somebody deals with it")
    conn = db()
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    conn.close()
    review_warning = [w for w in warnings if "answered" in w["title"]]
    s.check("it is on the panel", bool(review_warning),
            detail=str([w["title"] for w in warnings])[:120])
    # The panel names the WORST outstanding review, which in a full run may
    # belong to another suite. What this file can claim is that its own is
    # counted, and that the naming works at all — checked against the list the
    # panel is built from rather than against whichever one came top.
    conn = db()
    with m.app.test_request_context():
        outstanding = m.unanswered_reviews(conn, today=today)
    conn.close()
    mine = [p for p in outstanding if (p["guest_name"] or "").startswith(TAG)]
    s.check("this review is one of the ones outstanding", bool(mine),
            detail=f"{len(outstanding)} outstanding in total")
    s.check("carrying the guest and the score",
            mine and mine[0]["guest_name"] == TAG + " Bernard Roux"
            and int(mine[0]["rating"]) == 2,
            detail=str(dict(mine[0]))[:110] if mine else "")
    s.check("and what they said, so it can be judged without opening anything",
            mine and "cold" in (mine[0]["comment"] or "").lower(),
            detail=str(mine[0]["comment"])[:90] if mine else "")
    s.check("the panel counts them all rather than showing only one",
            review_warning and review_warning[0]["count"] == len(outstanding),
            detail=f"{review_warning[0]['count'] if review_warning else '?'} "
                   f"vs {len(outstanding)}")

    # The morning note reuses owner_home_warnings, so this arrives there too
    # without a second list to keep in agreement.
    conn = db()
    with m.app.test_request_context():
        _subject, body, _any = m.morning_digest(conn, today)
    conn.close()
    s.check("and the morning note carries it, because it reads the same list",
            "answered" in body,
            detail="two lists of what is wrong eventually disagree")

    s.section("Answering it")
    r = oc.post(f"/admin/feedback/{row['id']}/reply",
                data={"reply": "I am sorry — the boiler was being replaced that "
                               "week and we should have told you."},
                follow_redirects=True)
    after = _one("SELECT * FROM guest_feedback WHERE id = ?", (row["id"],))
    s.check("the reply is saved", after["reply"] and "boiler" in after["reply"],
            detail=str(flashes(r)))
    s.check("stamped with when", bool(after["replied_at"]))
    s.check("and marked as read, by whom", after["acknowledged_at"]
            and after["acknowledged_by_user_id"] == owner["id"],
            detail=str(after["acknowledged_by_user_id"]))
    sent = _one("SELECT * FROM email_outbox WHERE to_address = ? ORDER BY id DESC LIMIT 1",
                (bad["guest_email"],))
    s.check("the guest is written to", sent is not None)
    s.check("quoting what they said, so the reply makes sense on its own",
            sent and "room was cold" in (sent["body"] or ""),
            detail=(sent["body"] or "")[:80] if sent else "")
    s.check("and it is on the record",
            _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'feedback_answered'")["c"] > 0)

    conn = db()
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    conn.close()
    conn = db()
    with m.app.test_request_context():
        outstanding_now = m.unanswered_reviews(conn, today=today)
    conn.close()
    s.check("and it comes off the home page by itself",
            not [p for p in outstanding_now if (p["guest_name"] or "").startswith(TAG)
                 and p["id"] == row["id"]],
            detail="a warning that has to be dismissed by hand becomes a list "
                   "of everything that has ever been wrong")

    s.section("Marking one read without writing anything")
    # Some reviews want a telephone call. Forcing a written reply into the app
    # to clear a warning produces written replies nobody meant to send.
    quiet = _stay("QUIET", name=TAG + " Claire Weber")
    anon.post(f"/feedback/{quiet['manage_token']}",
              data={"rating": "3", "comment": "It was fine."}, follow_redirects=True)
    q = _one("SELECT * FROM guest_feedback WHERE booking_id = ?", (quiet["id"],))
    mail_before = _one("SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ?",
                       (quiet["guest_email"],))["c"]
    oc.post(f"/admin/feedback/{q['id']}/reply", data={"reply": ""}, follow_redirects=True)
    q2 = _one("SELECT * FROM guest_feedback WHERE id = ?", (q["id"],))
    s.check("it is marked read", bool(q2["acknowledged_at"]))
    s.check("with no reply stored", not q2["reply"])
    s.check("and nothing was sent to the guest",
            _one("SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ?",
                 (quiet["guest_email"],))["c"] == mail_before,
            detail="an empty reply must not email somebody a blank page")

    s.section("Featuring one actually puts it on the booking page")
    # THE CHECK THAT WAS MISSING. The button said "Feature on booking page";
    # the booking page queried the reviews and rendered none of them, because
    # the section had been replaced by hardcoded quotes. A test that watched
    # the FLAG flip passed the whole time.
    page = anon.get("/book")
    s.check("the booking page opens", page.status_code == 200,
            detail=str(page.status_code))
    body = page.get_data(as_text=True)
    s.check("an unfeatured review is not on it", "Wonderful, thank you." not in body,
            detail="nothing is published unless somebody chooses to publish it")

    gid = _one("SELECT id FROM guest_feedback WHERE booking_id = ?", (good["id"],))["id"]
    oc.post(f"/admin/feedback/{gid}/toggle-featured", follow_redirects=True)
    body = anon.get("/book").get_data(as_text=True)
    # Featuring is now half the answer. The form the guest filled in says
    # nothing is published without asking them first, so the public query
    # wants consent as well -- and the interesting half of that rule is this
    # one, where somebody has decided to use a review and nobody has written
    # to the guest yet.
    s.check("featuring alone does NOT put the words on the page",
            "Wonderful, thank you." not in body,
            detail="nobody has asked this guest, and the form they filled in "
                   "promised they would be asked first")

    conn = db()
    conn.execute("UPDATE guest_feedback SET publish_consent = 1, "
                 "publish_consent_at = ?, publish_consent_how = 'spoken:test' "
                 "WHERE id = ?",
                 (m.datetime.now(m.timezone.utc).isoformat(), gid))
    conn.commit()
    conn.close()
    body = anon.get("/book").get_data(as_text=True)
    s.check("featured AND agreed to puts them on it",
            "Wonderful, thank you." in body,
            detail="the flag changing is not the same as anything happening")

    oc.post(f"/admin/feedback/{gid}/toggle-featured", follow_redirects=True)
    body = anon.get("/book").get_data(as_text=True)
    s.check("and unfeaturing takes them off again",
            "Wonderful, thank you." not in body,
            detail="a review that can be put up and not taken down is one the "
                   "house cannot withdraw")

    s.section("A featured review shows what the house said back")
    oc.post(f"/admin/feedback/{row['id']}/toggle-featured", follow_redirects=True)
    conn = db()
    conn.execute("UPDATE guest_feedback SET publish_consent = 1, "
                 "publish_consent_at = ?, publish_consent_how = 'spoken:test' "
                 "WHERE id = ?",
                 (m.datetime.now(m.timezone.utc).isoformat(), row["id"]))
    conn.commit()
    conn.close()
    body = anon.get("/book").get_data(as_text=True)
    s.check("the review is there", "room was cold" in body)
    s.check("and the reply with it", "boiler" in body,
            detail="a complaint answered well reads better to the next guest "
                   "than no complaint at all")
    oc.post(f"/admin/feedback/{row['id']}/toggle-featured", follow_redirects=True)

    s.section("Who may answer")
    r = ec.post(f"/admin/feedback/{q['id']}/reply", data={"reply": TAG + " employee wrote this"},
                follow_redirects=False)
    s.check("an employee cannot reply", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and nothing was written",
            TAG + " employee" not in (_one("SELECT reply FROM guest_feedback WHERE id = ?",
                                           (q["id"],))["reply"] or ""),
            detail="the redirect alone would pass even if the reply had saved")
    r = anon.post(f"/admin/feedback/{q['id']}/reply", data={"reply": "hello"},
                  follow_redirects=False)
    s.check("nor can a stranger", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")
    r = oc.post("/admin/feedback/99999999/reply", data={"reply": "hello"},
                follow_redirects=False)
    s.check("a review that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
