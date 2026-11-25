"""A letter to a retreat sitting's group, set to go later.

The owner, 25 November 2026: "add schedule to write to the group for
workshops". Write to the group already sent at once; now the same letter can be
set for a day and a time.

Held here:
  - setting it sends nothing; a time already gone is refused;
  - the job sends nothing before its time, and at its time sends it through the
    same function as the Send button, to whoever is on the list THEN -- a
    registration made after it was set hears it too;
  - who it left out, and why, is written on the letter and named on the owner
    home, since nobody is watching the page when it goes;
  - a second run sends nothing more, and a cancelled letter never goes;
  - the page shows what is waiting and what went.

send_email is stood in for throughout. Nothing here sends mail.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZGLS"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *args, **kw):
        self.calls.append({"to": to_address, "subject": subject})
        return True


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute(f"DELETE FROM workshop_messages WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM group_letters_scheduled WHERE subject LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _session():
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active,
           sort_order, created_at, deposit_percent) VALUES (?, '', 2000, 30, 1, 96, ?, 30)""",
        (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()[0]
    start = house_today() + timedelta(days=60)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 30, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} s", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} s",)).fetchone()[0]
    conn.commit()
    conn.close()
    return sid


def _reg(sid, ref, *, do_not_email=0):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           do_not_email) VALUES (?, ?, ?, 1, 'confirmed', ?, ?, ?, 2000, 600, 1400, ?)""",
        (sid, f"{TAG} {ref}", f"{TAG.lower()}{ref.lower()}@example.invalid", f"{TAG}{ref}",
         f"tok{TAG}{ref}", now, do_not_email))
    conn.commit()
    conn.close()


def _letters(subject):
    conn = db()
    rows = conn.execute("SELECT * FROM group_letters_scheduled WHERE subject = ? ORDER BY id",
                        (subject,)).fetchall()
    conn.close()
    return rows


def _run_job(at):
    conn = db()
    with m.app.test_request_context("/"):
        out = m.run_group_letters_job(conn, now=at)
    conn.close()
    return out


def run():
    s = Suite("A letter to a group, set to go later")
    _cleanup()
    real = m.send_email
    rec = Recorder()
    m.send_email = rec
    try:
        _suite(s, rec)
    finally:
        m.send_email = real
        _cleanup()
    return s


def _suite(s, rec):
    oc, _ec, _owner, _emp = clients()
    sid = _session()
    _reg(sid, "Ann")
    _reg(sid, "Bea", do_not_email=1)
    url = f"/admin/workshops/sessions/{sid}/email"
    subject = f"{TAG} Before you come"
    tomorrow = house_today() + timedelta(days=1)

    s.section("Setting it")
    page = oc.get(url).get_data(as_text=True)
    s.check("the page offers to send it later", "Or send it later" in page and 'value="schedule"' in page)
    r = oc.post(url, data={"subject": subject, "body": "Dear {{first_name}}, a note.",
                           "action": "schedule", "send_day": tomorrow.isoformat(), "send_time": "09:30"},
                follow_redirects=True)
    rows = _letters(subject)
    s.check("it is set, waiting", len(rows) == 1 and rows[0]["status"] == "scheduled",
            detail=str([dict(x) for x in rows]))
    want = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 9, 30, tzinfo=m.LOCAL_TZ)
    s.check("for 9:30 at the château, kept in UTC",
            rows and datetime.fromisoformat(rows[0]["send_at"]) == want
            and rows[0]["send_at"].endswith("+00:00"), detail=rows[0]["send_at"] if rows else "")
    s.check("and setting it sends nothing", not rec.calls, detail=str(rec.calls))
    s.check("the page says it waits, and when", "Set for later" in r.get_data(as_text=True)
            and "waiting" in r.get_data(as_text=True))
    yesterday = house_today() - timedelta(days=1)
    r = oc.post(url, data={"subject": f"{TAG} too late", "body": "x", "action": "schedule",
                           "send_day": yesterday.isoformat(), "send_time": "09:00"}, follow_redirects=True)
    s.check("a time already gone is refused", not _letters(f"{TAG} too late")
            and any("passed" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("Before its time, and at it")
    _run_job(want - timedelta(minutes=1))
    s.check("a minute before, nothing goes", not rec.calls and _letters(subject)[0]["status"] == "scheduled",
            detail=str(rec.calls))
    _reg(sid, "Cal")   # booked after the letter was set
    _run_job(want + timedelta(minutes=2))
    sent_to = sorted(c["to"] for c in rec.calls)
    s.check("at its time it goes to everybody who can be written to",
            sent_to == [f"{TAG.lower()}ann@example.invalid", f"{TAG.lower()}cal@example.invalid"],
            detail=str(sent_to))
    s.check("including the one who booked after it was set",
            f"{TAG.lower()}cal@example.invalid" in sent_to)
    s.check("never the one who asked not to be emailed",
            f"{TAG.lower()}bea@example.invalid" not in sent_to)
    row = _letters(subject)[0]
    s.check("the letter says it went, and names who it left out",
            row["status"] == "sent" and row["sent_count"] == 2 and f"{TAG} Bea" in (row["result"] or "")
            and row["result_ok"] == 0, detail=str(dict(row)))

    s.section("Said where it will be seen")
    conn = db()
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, house_today())
    conn.close()
    s.check("the owner home names the letter that did not reach everybody",
            any(subject in w["title"] for w in warnings), detail=str([w["title"] for w in warnings])[:400])
    page = oc.get(url).get_data(as_text=True)
    s.check("and the group's page shows what it did", f"{TAG} Bea" in page and "sent" in page)

    s.section("Never twice, and never once cancelled")
    rec.calls.clear()
    _run_job(want + timedelta(hours=1))
    s.check("a second run sends nothing more", not rec.calls, detail=str(rec.calls))
    other = f"{TAG} Cancelled one"
    oc.post(url, data={"subject": other, "body": "x", "action": "schedule",
                       "send_day": tomorrow.isoformat(), "send_time": "10:00"})
    lid = _letters(other)[0]["id"]
    oc.post(url, data={"action": "unschedule", "scheduled_id": str(lid)})
    _run_job(want + timedelta(days=2))
    s.check("a cancelled letter never goes", not rec.calls and _letters(other)[0]["status"] == "cancelled",
            detail=str(rec.calls))


if __name__ == "__main__":
    print(run().report())
