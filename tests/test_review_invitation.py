"""Asking a guest to say it publicly — but only the ones who said it privately.

The house asks departed guests how it was, and reads every answer. What it
could not do was the obvious next thing: ask the ones who were delighted to say
so where other people can read it. Every review the château has came from a
guest who thought of it unprompted.

Three things carry this file.

  THE PUBLIC ASK IS GATED ON THE PRIVATE ANSWER. Writing "would you review us?"
  to everybody who stayed is how a house ends up soliciting a public review from
  the guest whose boiler failed — a guest who has already told us, on the form,
  that it failed. Nobody under four out of five is ever asked, by the job or by
  the button, and both read the same function so they cannot come to differ.

  NOBODY IS ASKED WHILE THERE IS NOWHERE TO SEND THEM. The link is blank until
  the house pastes its own in. A guessed URL sends guests to another house's
  listing, and an invitation that lands on a broken link is worse than none.

  NOBODY IS ASKED TWICE. Stamped before the send, and stamped even when the
  answer was too poor to follow up — otherwise the job reconsiders the same
  disappointed guest every morning for ever.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZRV"
LINK = "https://example.invalid/review-the-chateau"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM guest_feedback WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", ("zzrv.%",))
    conn.commit()
    conn.close()


def _set_link(value):
    conn = db()
    was = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                       (m.REVIEW_LINK_SETTING,)).fetchone()
    conn.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (m.REVIEW_LINK_SETTING, value))
    conn.commit()
    conn.close()
    return was["value"] if was else None


def _stay_and_answer(ref, *, room_id, rating, days_ago=5, optout=False):
    conn = db()
    departed = m.house_today() - timedelta(days=days_ago)
    email = f"zzrv.{ref}@example.invalid".lower()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 'paid', 400, 400, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", email,
         (departed - timedelta(days=2)).isoformat(), departed.isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (f"{TAG}-{ref}",)).fetchone()
    conn.execute(
        """INSERT INTO guest_feedback (booking_id, guest_name, rating, comment,
           submitted_at) VALUES (?, ?, ?, ?, ?)""",
        (booking["id"], f"{TAG} {ref}", rating, "A comment.",
         (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()))
    if optout:
        conn.execute(
            "INSERT INTO email_optouts (email, created_at) VALUES (?, ?)",
            (email, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    answer = conn.execute(
        "SELECT * FROM guest_feedback WHERE booking_id = ?", (booking["id"],)).fetchone()
    conn.close()
    return booking, answer


def _answer(feedback_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM guest_feedback WHERE id = ?",
                            (feedback_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Asking for a public review")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    sent = []
    was_email = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append((to, body)), True)[1]
    was_link = _set_link("")

    try:
        happy, happy_answer = _stay_and_answer("HAPPY", room_id=room["id"], rating=5)
        cross, cross_answer = _stay_and_answer("CROSS", room_id=room["id"], rating=2)
        meh, meh_answer = _stay_and_answer("MEH", room_id=room["id"], rating=3)
        quiet, quiet_answer = _stay_and_answer("QUIET", room_id=room["id"], rating=5,
                                               optout=True)

        s.section("With nowhere to send them, nobody is asked")
        conn = db()
        with m.app.test_request_context("/"):
            said = m.run_review_invitation_job(conn)
        conn.close()
        s.check("nothing goes out", not sent, detail=f"{sent}")
        s.check("and it says why, without calling it a failure",
                "no review page is set" in said, detail=said)
        s.check("nobody is stamped either",
                not _answer(happy_answer["id"])["review_invited_at"],
                detail="stamping now would mean this guest is never asked once "
                       "the link is finally pasted in")

        s.section("With a link, the delighted guest is asked")
        _set_link(LINK)
        sent.clear()
        conn = db()
        with m.app.test_request_context("/"):
            said = m.run_review_invitation_job(conn)
        conn.close()
        asked = [to for to, _ in sent]
        s.check("they are written to", happy["guest_email"] in asked, detail=f"{asked}")
        s.check("with the house's own link in it",
                any(LINK in body for _, body in sent),
                detail="a guessed URL sends guests to another house's listing")
        s.check("and it is recorded", bool(_answer(happy_answer["id"])["review_invited_at"]))

        s.section("And the unhappy guest is not")
        # THE POINT OF THE WHOLE FEATURE. This guest has already told us, on
        # the form, that something went wrong.
        s.check("nothing goes to them", cross["guest_email"] not in asked,
                detail="soliciting a public review from the guest whose boiler "
                       "failed is the mistake this exists to avoid")
        s.check("nor to the merely polite one", meh["guest_email"] not in asked,
                detail="three out of five in private is not four out of five "
                       "in public")
        s.check("but they are marked as decided",
                bool(_answer(cross_answer["id"])["review_invited_at"]),
                detail="otherwise the job reconsiders the same disappointed "
                       "guest every morning for ever")

        s.section("Nor anybody who asked not to be written to")
        s.check("they are left alone", quiet["guest_email"] not in asked,
                detail=f"{asked}")

        s.section("Nobody is asked twice")
        sent.clear()
        conn = db()
        with m.app.test_request_context("/"):
            again = m.run_review_invitation_job(conn)
        conn.close()
        s.check("a second run writes nothing", not sent, detail=f"{sent}")
        s.check("and says so plainly", "nobody new" in again, detail=again)

        s.section("Asking one by hand")
        fresh, fresh_answer = _stay_and_answer("FRESH", room_id=room["id"], rating=5,
                                               days_ago=0)
        sent.clear()
        oc.post(f"/admin/feedback/{fresh_answer['id']}/ask-for-a-review",
                follow_redirects=True)
        s.check("the button sends it", [to for to, _ in sent] == [fresh["guest_email"]],
                detail=f"{sent}")
        s.check("and stamps it", bool(_answer(fresh_answer["id"])["review_invited_at"]))

        s.section("The button obeys the same rule as the job")
        sent.clear()
        page = oc.post(f"/admin/feedback/{cross_answer['id']}/ask-for-a-review",
                       follow_redirects=True).get_data(as_text=True)
        s.check("it refuses", not sent, detail=f"{sent}")
        s.check("and says what the guest actually thought",
                "2 out of 5" in page,
                detail="one function decides this, so the button and the job "
                       "cannot come to differ")

        s.section("The day before is too soon")
        # Not the same day: the house has just read the private answer, and if
        # something was wrong there has to be time for a person to see it.
        today_stay, today_answer = _stay_and_answer("TODAY", room_id=room["id"],
                                                    rating=5, days_ago=0)
        sent.clear()
        conn = db()
        with m.app.test_request_context("/"):
            m.run_review_invitation_job(conn)
        conn.close()
        s.check("today's answer waits",
                today_stay["guest_email"] not in [to for to, _ in sent],
                detail="a second email an hour after the first reads as a "
                       "machine")

        s.section("The page")
        page = oc.get("/admin/feedback").get_data(as_text=True)
        s.check("the link can be set from it", "review_link_url" in page)
        s.check("and it shows who has been asked", "asked for a review" in page)
        s.check("with a way to ask somebody by hand",
                "Ask for a public review" in page)

        s.section("And the guest is offered it the moment they say it was good")
        # The best moment to ask is while they are still on the page.
        latest, _latest_answer = _stay_and_answer("FORM", room_id=room["id"], rating=5)
        conn = db()
        conn.execute("DELETE FROM guest_feedback WHERE booking_id = ?", (latest["id"],))
        conn.commit()
        conn.close()
        thanks = oc.post(f"/feedback/{latest['manage_token']}",
                         data={"rating": "5", "comment": "Wonderful."}).get_data(as_text=True)
        s.check("the link is on the thank-you page", LINK in thanks,
                detail="they are here, they are warm, and no email is involved")
        low, _ = _stay_and_answer("LOWFORM", room_id=room["id"], rating=5)
        conn = db()
        conn.execute("DELETE FROM guest_feedback WHERE booking_id = ?", (low["id"],))
        conn.commit()
        conn.close()
        thanks = oc.post(f"/feedback/{low['manage_token']}",
                         data={"rating": "2", "comment": "The boiler failed."}).get_data(as_text=True)
        s.check("and never after a poor one", LINK not in thanks,
                detail="the same rule as everywhere else, at the one place a "
                       "guest could be nudged without any email at all")

        s.section("Guards")
        # Read back out of the database rather than off the page: what
        # matters is that the bad value was not stored, and a page that
        # happens not to echo it back proves nothing.
        page = oc.post("/admin/feedback/review-link",
                       data={"review_link_url": "chateaugudanes.com/reviews"},
                       follow_redirects=True).get_data(as_text=True)
        conn = db()
        still = m.review_link(conn)
        conn.close()
        s.check("a link that is not a link is refused", still == LINK,
                detail=f"{still!r} — a broken link is worse than a blank one")
        s.check("and it says what is wrong with it",
                "http://" in page and "https://" in page,
                detail="'that is not valid' tells somebody nothing they can act on")
        # Read the setting back, not the status code: a refusal and a
        # successful save are both a 302 here, so the code alone cannot tell
        # them apart -- and a check that cannot fail reads as cover.
        ec.post("/admin/feedback/review-link",
                data={"review_link_url": "https://example.invalid/employee-set-this"})
        conn = db()
        after = m.review_link(conn)
        conn.close()
        s.check("an employee cannot set it", after == LINK, detail=f"{after!r}")
        s.check("nor ask a guest for a review",
                ec.post(f"/admin/feedback/{happy_answer['id']}/ask-for-a-review"
                        ).status_code in (302, 403))
    finally:
        m.send_email = was_email
        _set_link(was_link or "")
        _cleanup()
    return s
