"""Pages that no test had ever let answer.

Coverage counts the ANSWER, not the request: a page is covered only if a view
ran its working branch. These four had never once done that. Two of them are
the usual shape — the helper underneath is tested thoroughly and the route
somebody actually presses never is — and a helper that works behind a route
nobody has exercised is half a feature. The route is where the redirect, the
flash, the permission and the form parsing live, and any of those can be the
thing that is broken.

  THE EMAIL PREVIEW must not save. The dev database holds the live email
  templates, so a preview that quietly wrote the draft would be an edit that
  ships to guests. And it must tell the truth about a refusal: a draft with a
  tag nothing fills is not what a guest would receive — the shipped wording is.

  AN INSTALMENT is refused, not shrunk, when it would take the plan over the
  quote. Silently cutting a stage to fit is how a payment plan stops adding up
  to the agreement.

  A RELEASED HOLD puts the dates back on the market — checked by asking
  whether a room can be booked on them, which is the claim the flash makes.

  A MEETING'S STATUS carries a cancellation reason only when it is cancelled,
  so a meeting cancelled and then held does not keep a reason for a thing that
  did not happen.
"""
import json
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZNEVER"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM event_instalments WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE contact_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_holds WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE contact_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM meetings WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _event(price=10000):
    conn = db()
    when = m.house_today() + timedelta(days=640)
    ref = m.make_event_reference_code()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
             contact_name, contact_email, preferred_date, end_date, guest_count,
             status, quoted_price, amount_paid, created_at)
           VALUES (?, ?, 'wedding', ?, 'zznever@example.invalid', ?, ?, 60,
                   'quoted', ?, 0, ?)""",
        (ref, "zznever" + m.secrets.token_hex(6), TAG + " couple",
         when.isoformat(), (when + timedelta(days=1)).isoformat(), price,
         datetime.now(timezone.utc).isoformat()))
    eid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    return eid


def run():
    s = Suite("Pages that had never answered")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    # ------------------------------------------------------------------
    s.section("The email preview")
    key = "workshop_confirmed"
    conn = db()
    stored_before = conn.execute(
        "SELECT subject, body FROM email_templates WHERE template_key = ?",
        (key,)).fetchone()
    conn.close()
    draft_subject = "Confirmed: {workshop_title}"
    draft_body = "Dear {guest_name}, see you at {workshop_title}."
    r = oc.post(f"/management/email-templates/{key}/preview",
                data=json.dumps({"subject": draft_subject, "body": draft_body}),
                content_type="application/json")
    got = r.get_json(silent=True) or {}
    s.check("it answers with the letter", r.status_code == 200 and "body" in got,
            detail=f"HTTP {r.status_code} {str(got)[:80]}")
    s.check("with the merge tags filled, not printed",
            "{guest_name}" not in got.get("body", "")
            and "{workshop_title}" not in got.get("body", ""),
            detail=repr(got.get("body", "")[:90]))
    s.check("and it is the DRAFT it shows, not the saved wording",
            got.get("body", "").startswith("Dear ") and not got.get("refused"),
            detail="the question is about text that has not been saved yet")
    conn = db()
    stored_after = conn.execute(
        "SELECT subject, body FROM email_templates WHERE template_key = ?",
        (key,)).fetchone()
    conn.close()
    s.check("and previewing it saved nothing",
            (tuple(stored_before) if stored_before else None)
            == (tuple(stored_after) if stored_after else None),
            detail="the email templates are live config in this database; a "
                   "preview that wrote the draft would be an edit that ships")

    bad = oc.post(f"/management/email-templates/{key}/preview",
                  data=json.dumps({"subject": "Hi", "body": "Hello {nobody_fills_this}"}),
                  content_type="application/json").get_json(silent=True) or {}
    s.check("a draft with a tag nothing fills is called a refusal",
            bad.get("refused") and "nobody_fills_this" in (bad.get("why") or ""),
            detail=f"{bad.get('why')!r}")
    s.check("and shows the shipped wording a guest would actually get",
            bad.get("shipped_instead") and "nobody_fills_this" not in bad.get("body", ""),
            detail="the send uses the shipped text in that case, and a preview "
                   "that showed the draft would be showing a letter nobody gets")
    s.check("a template that does not exist is a 404",
            oc.post("/management/email-templates/zz_no_such/preview",
                    data="{}", content_type="application/json").status_code == 404)
    s.check("an employee cannot preview guest mail",
            ec.post(f"/management/email-templates/{key}/preview",
                    data="{}", content_type="application/json").status_code != 200)

    # ------------------------------------------------------------------
    s.section("An instalment on an event")
    eid = _event(price=10000)
    r = oc.post(f"/admin/events/{eid}/instalment",
                data={"label": "Deposit", "amount": "3000",
                      "due_date": (m.house_today() + timedelta(days=30)).isoformat()},
                follow_redirects=True)
    conn = db()
    rows = conn.execute("SELECT * FROM event_instalments WHERE event_id = ?",
                        (eid,)).fetchall()
    conn.close()
    s.check("the route answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("and the stage is written", len(rows) == 1 and rows[0]["amount"] == 3000,
            detail=f"{[dict(x) for x in rows]}")
    s.check("and it says when it falls due",
            any("falls due" in f for f in flashes(r)), detail="; ".join(flashes(r)[:1]))
    oc.post(f"/admin/events/{eid}/instalment",
            data={"label": "Second", "amount": "2000,50",
                  "due_date": (m.house_today() + timedelta(days=60)).isoformat()})
    s.check("a comma for a decimal point is read as one",
            _amounts(eid) == [3000.0, 2000.5],
            detail=f"{_amounts(eid)} — a French keyboard types 2000,50")
    over = oc.post(f"/admin/events/{eid}/instalment",
                   data={"label": "Too much", "amount": "9000",
                         "due_date": (m.house_today() + timedelta(days=90)).isoformat()},
                   follow_redirects=True)
    s.check("a stage that would go over the quote is refused, not shrunk",
            _amounts(eid) == [3000.0, 2000.5],
            detail=f"{_amounts(eid)} — cutting it to fit is how a plan stops "
                   "adding up to the agreement")
    s.check("and says how much is left to schedule",
            any("unscheduled" in f for f in flashes(over)),
            detail="; ".join(flashes(over)[:1]))
    s.check("an employee cannot add one",
            ec.post(f"/admin/events/{eid}/instalment", data={"amount": "1"}).status_code != 200)

    # ------------------------------------------------------------------
    s.section("Letting a hold go")
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    start = _harness.free_window(room["id"], 2, after_days=700, clear_of_ateliers=True)
    end = start + timedelta(days=1)
    hold_event = _event(price=5000)
    conn = db()
    conn.execute(
        """INSERT INTO event_holds (event_id, start_date, end_date, expires_at,
             note, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (hold_event, start.isoformat(), end.isoformat(),
         (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), TAG,
         datetime.now(timezone.utc).isoformat()))
    hold_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    held_ok, _why = m.is_range_available(conn, room["id"], start, end + timedelta(days=1))
    conn.close()
    s.check("while it is held, the room cannot be booked on those dates", not held_ok,
            detail="if the hold did not block anything, releasing it proves nothing")
    r = oc.post(f"/admin/events/hold/{hold_id}/release", follow_redirects=True)
    conn = db()
    hold = conn.execute("SELECT * FROM event_holds WHERE id = ?", (hold_id,)).fetchone()
    free_ok, why = m.is_range_available(conn, room["id"], start, end + timedelta(days=1))
    conn.close()
    s.check("the route answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("the hold is released, and says the house let it go",
            hold["released_at"] and hold["released_reason"] == "let go by the house",
            detail=f"{hold['released_at']} / {hold['released_reason']}")
    s.check("and the dates really are back on the market", free_ok,
            detail=f"{why} — the flash says 'back on the market'; this is the "
                   "check that it is")
    again = oc.post(f"/admin/events/hold/{hold_id}/release", follow_redirects=True)
    s.check("releasing it twice says so rather than pretending",
            any("already" in f for f in flashes(again)), detail="; ".join(flashes(again)[:1]))
    s.check("a hold that does not exist is a 404",
            oc.post("/admin/events/hold/99887766/release").status_code == 404)

    # ------------------------------------------------------------------
    s.section("A meeting's status")
    conn = db()
    conn.execute("INSERT INTO meetings (title, meeting_date, created_at) VALUES (?, ?, ?)",
                 (TAG + " staff meeting", (m.house_today() + timedelta(days=3)).isoformat(),
                  datetime.now(timezone.utc).isoformat()))
    mid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    r = oc.post(f"/meetings/{mid}/status",
                data={"status": "cancelled", "cancelled_reason": "Half the team off sick"},
                follow_redirects=True)
    s.check("the route answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("a cancellation is recorded with its reason",
            _meeting(mid) == ("cancelled", "Half the team off sick"), detail=str(_meeting(mid)))
    oc.post(f"/meetings/{mid}/status", data={"status": "held",
                                            "cancelled_reason": "leftover"})
    s.check("and held afterwards, it keeps no reason for a thing that did not happen",
            _meeting(mid) == ("held", None), detail=str(_meeting(mid)))
    conn = db()
    logged = conn.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = "
                          "'meeting_status_changed' AND target = ?",
                          (TAG + " staff meeting",)).fetchone()["n"]
    conn.close()
    s.check("each change is in the audit trail", logged >= 2, detail=f"{logged}")
    s.check("a status that is not one is refused",
            oc.post(f"/meetings/{mid}/status", data={"status": "postponed"}).status_code == 400)
    s.check("a meeting that does not exist is a 404",
            oc.post("/meetings/99887766/status", data={"status": "held"}).status_code == 404)
    s.check("an employee cannot change it",
            ec.post(f"/meetings/{mid}/status", data={"status": "held"}).status_code != 200)

    _cleanup()
    return s


def _amounts(event_id):
    conn = db()
    try:
        return [r["amount"] for r in conn.execute(
            "SELECT amount FROM event_instalments WHERE event_id = ? ORDER BY id",
            (event_id,)).fetchall()]
    finally:
        conn.close()


def _meeting(meeting_id):
    conn = db()
    try:
        row = conn.execute("SELECT status, cancelled_reason FROM meetings WHERE id = ?",
                           (meeting_id,)).fetchone()
        return (row["status"], row["cancelled_reason"]) if row else None
    finally:
        conn.close()
