"""Deciding a sitting will not run, and a page for whoever is teaching it.

The job that spots a session which will not reach its number has warned for a
while, and there was nothing to DO about it: deleting a session refuses while
anybody is registered and says "cancel them first" — one at a time, in a
hurry, on the day somebody has decided not to run it.

And a workshop has an instructor with no page of their own. Who is coming,
what they cannot eat, the materials, the rooming: all of it asked for by email
every time and answered with a screenshot.

Four things carry this file.

  THE BULK ACTION IS THE SAME ACTION, DONE MANY TIMES. Every bulk action in
  this app started as a loop over the core helper, so the behaviour that lived
  in the single-item ROUTE never happened in bulk. The letter and the audit
  line now live in a function both paths call, and this file checks the single
  cancellation still goes through it.

  THE WAITING LIST IS TOLD IT IS OFF, not offered a place. Offering somebody a
  place on an atelier that is not running is worse than saying nothing.

  MONEY IS NAMED AND NOT MOVED. Refunds in this house are a deliberate
  case-by-case decision, so calling a sitting off says who is owed what and
  leaves it there.

  THE INSTRUCTOR'S PAGE IS NARROW ON PURPOSE. No money, no other sittings,
  nobody else's guests. It is not a revenue share, which was declined.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZCO2"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM workshop_bookings WHERE session_id IN
                    (SELECT ws.id FROM workshop_sessions ws
                      JOIN workshops w ON w.id = ws.workshop_id
                     WHERE w.title LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_waitlist WHERE session_id IN
                    (SELECT ws.id FROM workshop_sessions ws
                      JOIN workshops w ON w.id = ws.workshop_id
                     WHERE w.title LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_sessions WHERE workshop_id IN
                    (SELECT id FROM workshops WHERE title LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _session(conn, ref, start):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, active,
           instructor_name, created_at) VALUES (?, '', 400, 1, 'A Plasterer', ?)""",
        (f"{TAG} {ref}", now))
    conn.commit()
    w = conn.execute("SELECT * FROM workshops WHERE title = ?",
                     (f"{TAG} {ref}",)).fetchone()
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
           capacity, created_at) VALUES (?, ?, ?, 10, ?)""",
        (w["id"], start.isoformat(), (start + timedelta(days=3)).isoformat(), now))
    conn.commit()
    return conn.execute(
        "SELECT * FROM workshop_sessions WHERE workshop_id = ? ORDER BY id DESC LIMIT 1",
        (w["id"],)).fetchone()


def _place(conn, session_id, ref, *, status="confirmed", paid=0.0):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, total_price,
           dietary_notes, created_at)
           VALUES (?, ?, ?, ?, ?, 2, ?, 800, ?, ?)""",
        (session_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzco2.{ref}@example.invalid".lower(), status,
         "no shellfish" if ref == "A" else None, now))
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    if paid:
        conn.execute(
            """INSERT INTO workshop_transactions (workshop_booking_id, kind,
               description, amount, created_at) VALUES (?, 'payment', 'deposit', ?, ?)""",
            (row["id"], paid, now))
        conn.commit()
    return row


def _row(ref):
    conn = db()
    try:
        return conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                            (f"{TAG}-{ref}",)).fetchone()
    finally:
        conn.close()


def _sess(session_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM workshop_sessions WHERE id = ?",
                            (session_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Calling a sitting off")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    sent = []
    was_email, was_ws = m.send_email, m.send_workshop_email
    m.send_email = lambda to, subj, body, **k: (sent.append((to, subj, body)), True)[1]
    m.send_workshop_email = lambda conn, b, key, ctx, **k: sent.append(
        (b["guest_email"], key, "")) or True

    try:
        conn = db()
        start = m.house_today() + timedelta(days=50)
        sess = _session(conn, "PLASTER", start)
        a = _place(conn, sess["id"], "A", paid=300)
        b = _place(conn, sess["id"], "B")
        _place(conn, sess["id"], "GONE", status="cancelled")
        conn.execute(
            """INSERT INTO workshop_waitlist (session_id, name, email, party_size,
               status, created_at) VALUES (?, ?, ?, 2, 'open', ?)""",
            (sess["id"], f"{TAG} Waiting", "zzco2.wait@example.invalid",
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

        s.section("Deleting still refuses, which is why this exists")
        page = oc.post(f"/admin/workshops/sessions/{sess['id']}/delete",
                       follow_redirects=True).get_data(as_text=True)
        s.check("the sitting is still there", _sess(sess["id"]) is not None,
                detail="delete refuses while anybody is registered, and tells "
                       "you to cancel them one at a time")

        s.section("Calling it off")
        sent.clear()
        resp = oc.post(f"/admin/workshops/session/{sess['id']}/call-off",
                       data={"reason": "Only two places taken."},
                       follow_redirects=True)
        said = " ".join(flashes(resp))
        s.check("both live places are cancelled",
                _row("A")["status"] == "cancelled" and _row("B")["status"] == "cancelled",
                detail=f"{_row('A')['status']} / {_row('B')['status']}")
        s.check("and it says how many", "2 place" in said, detail=said)
        s.check("the sitting is marked off rather than deleted",
                _sess(sess["id"])["cancelled_at"] is not None,
                detail="why it did not run is the one thing worth knowing when "
                       "deciding whether to put it on again")
        s.check("with the reason kept",
                (_sess(sess["id"])["cancelled_reason"] or "").startswith("Only two"),
                detail=f"{_sess(sess['id'])['cancelled_reason']!r}")

        s.section("Everybody is written to, once")
        told = [t for t, _s, _b in sent]
        s.check("the person who had paid", a["guest_email"] in told, detail=f"{told}")
        s.check("and the one who had not", b["guest_email"] in told)
        s.check("but not the one who had already cancelled",
                "zzco2.gone@example.invalid" not in told,
                detail=f"{told} — they were already off it")

        s.section("The waiting list is told it is not running")
        # NOT offered a place. Offering somebody a place on an atelier that is
        # not happening is worse than saying nothing.
        waiting_mail = [(t, subj, body) for t, subj, body in sent
                        if t == "zzco2.wait@example.invalid"]
        s.check("they are written to", waiting_mail, detail=f"{told}")
        s.check("and told it is off, not offered a place",
                waiting_mail and "not running" in (waiting_mail[0][1] or "").lower(),
                detail=f"{waiting_mail[0][1] if waiting_mail else None!r}")
        s.check("and it says why they are hearing from us",
                waiting_mail and "Only two places" in (waiting_mail[0][2] or ""),
                detail="the reason travels with the message")
        conn = db()
        still_open = conn.execute(
            "SELECT COUNT(*) AS c FROM workshop_waitlist WHERE session_id = ? AND status = 'open'",
            (sess["id"],)).fetchone()["c"]
        conn.close()
        s.check("and nobody is left waiting for it", still_open == 0,
                detail=f"{still_open}")

        s.section("Money is named, and not moved")
        s.check("what is owed back is in the message",
                "300" in said.replace(",", ""),
                detail=f"{said} — refunds here are a deliberate case-by-case "
                       "decision, so this says who is owed what")
        s.check("and whose it is", f"{TAG} A" in said, detail=said)
        conn = db()
        refunds = conn.execute(
            "SELECT COUNT(*) AS c FROM refunds WHERE reference_code = ?",
            (f"{TAG}-A",)).fetchone()["c"]
        conn.close()
        s.check("no money actually moved", refunds == 0,
                detail=f"{refunds} refund row(s) — building the capability is "
                       "one thing; moving real money on a button is another")

        s.section("Somebody who cannot be written to is named")
        # send_workshop_email quietly skips anybody who has asked not to be
        # written to -- correctly, it is their choice. Without saying so, the
        # message would read "Cancelled 3 places" while one of the three never
        # heard that their atelier is not running.
        conn = db()
        quiet_sess = _session(conn, "QUIET", start + timedelta(days=60))
        _place(conn, quiet_sess["id"], "LOUD")
        hush = _place(conn, quiet_sess["id"], "HUSH")
        conn.execute("UPDATE workshop_bookings SET do_not_email = 1 WHERE id = ?",
                     (hush["id"],))
        conn.commit()
        conn.close()
        told_msg = " ".join(flashes(oc.post(
            f"/admin/workshops/session/{quiet_sess['id']}/call-off",
            follow_redirects=True)))
        s.check("both are still cancelled",
                _row("HUSH")["status"] == "cancelled"
                and _row("LOUD")["status"] == "cancelled",
                detail="not writing to somebody is not a reason to leave them "
                       "on a sitting that is not running")
        s.check("and the one who was not told is named",
                f"{TAG} HUSH" in told_msg,
                detail=f"{told_msg} — a cheerful total with one person never "
                       "told is exactly what this app has a rule against")
        s.check("with what to do about it",
                "tell them yourself" in told_msg, detail=told_msg)

        s.section("Calling it off twice does nothing twice")
        sent.clear()
        again = " ".join(flashes(oc.post(
            f"/admin/workshops/session/{sess['id']}/call-off",
            follow_redirects=True)))
        s.check("it says so", "already" in again.lower(), detail=again)
        s.check("and writes to nobody", not sent, detail=f"{sent}")

        s.section("A single cancellation still does the whole follow-up")
        # The bulk path and the single path share one function now. If the
        # single route stopped calling it, this is what would notice.
        conn = db()
        other = _session(conn, "GILDING", start + timedelta(days=30))
        solo = _place(conn, other["id"], "SOLO")
        conn.commit()
        conn.close()
        sent.clear()
        oc.post(f"/admin/workshops/registrations/{solo['id']}/cancel",
                follow_redirects=True)
        s.check("they are cancelled", _row("SOLO")["status"] == "cancelled")
        s.check("and written to", any(t == solo["guest_email"] for t, _s, _b in sent),
                detail=f"{[t for t, _s, _b in sent]} — the letter lives in the "
                       "shared follow-up, where a loop cannot miss it")

        s.section("The page for whoever is teaching")
        resp = oc.post(f"/admin/workshops/session/{other['id']}/instructor-link",
                       follow_redirects=True)
        token = _sess(other["id"])["instructor_token"]
        s.check("a link is minted", bool(token))
        s.check("and handed over", token in " ".join(flashes(resp)),
                detail="a link nobody is given is a link nobody uses")
        conn = db()
        _place(conn, other["id"], "PUPIL")
        conn.close()
        sheet = oc.get(f"/workshops/teaching/{token}").get_data(as_text=True)
        s.check("it opens", f"{TAG} GILDING" in sheet)
        s.check("with who is coming", f"{TAG} PUPIL" in sheet)
        s.check("never indexed", "noindex" in sheet,
                detail="somebody's guests and what they cannot eat")
        # DELIBERATELY NARROW.
        template = open("templates/instructor_sheet.html", encoding="utf-8").read()
        s.check("and no money on it",
                not any(word in template for word in
                        ("total_price", "price_per_person", "deposit_amount",
                         "balance_amount", "&euro;")),
                detail="what somebody teaching a plaster course needs is who "
                       "is in the room, not what anybody paid. Checked on the "
                       "template, because a bare number search across an HTML "
                       "page finds font-weight: 400 and proves nothing")
        s.check("nor anybody from another sitting",
                f"{TAG} A" not in sheet,
                detail="one sitting, not every sitting they teach")
        s.check("a made-up token is a 404",
                oc.get("/workshops/teaching/nonsense").status_code == 404)

        s.section("And the link can be taken away")
        oc.post(f"/admin/workshops/session/{other['id']}/instructor-link",
                data={"off": "1"}, follow_redirects=True)
        s.check("the token is gone", _sess(other["id"])["instructor_token"] is None)
        s.check("and the link stops working",
                oc.get(f"/workshops/teaching/{token}").status_code == 404)

        s.section("Guards")
        s.check("an employee cannot call a sitting off",
                ec.post(f"/admin/workshops/session/{other['id']}/call-off"
                        ).status_code in (302, 403))
        s.check("and really cannot", _sess(other["id"])["cancelled_at"] is None,
                detail="read back, because a refusal and a success are both a 302")
        s.check("nor mint a teaching link",
                ec.post(f"/admin/workshops/session/{other['id']}/instructor-link"
                        ).status_code in (302, 403))
        s.check("an unknown sitting is a 404",
                oc.post("/admin/workshops/session/999999/call-off").status_code == 404)
    finally:
        m.send_email, m.send_workshop_email = was_email, was_ws
        _cleanup()
    return s
