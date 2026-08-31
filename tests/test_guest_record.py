"""One page that is the whole of a guest, rather than four holding a bit each.

What was there: a profiles LIST with a name and some notes, an edit form, and
— separately — a history page keyed on an email STRING. The two never met. A
profile with nothing linked showed no history at all, and a guest whose
address had changed had theirs split down the middle with the lifetime spend
halved underneath it.

THREE THINGS THIS FILE IS ABOUT.

MATCHED TWO WAYS, AND SAID SO. A booking belongs to a profile because it was
linked, or because it carries the same address. Both count — but the record
says which is which, because "we think this is the same person" and "this is
the same person" are different claims and a page that blurs them will one day
show somebody else's stay to a colleague.

THE MONEY COMES FROM THE BILLS. Summed from each booking's own bill rather
than worked out again, so the lifetime figure and the guest's own statement
cannot disagree. That is the rule everywhere else here and this is not an
exception to it.

AND WHAT A COLLEAGUE SEES IS NOT WHAT THE OWNER SEES. Somebody carrying bags
needs to know a guest is vegetarian and how to say their name. What they have
spent is not a colleague's business, and neither is every email the house has
sent them.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTREC"
WHO = f"{TAG.lower()}person@example.invalid"


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
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_email = ?", (WHO,))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address = ?", (WHO,))
    conn.execute("DELETE FROM sms_outbox WHERE phone = '+33677777777'")
    conn.execute("DELETE FROM audit_log WHERE action = 'guest_bookings_linked'")
    conn.commit()
    conn.close()


def _profile():
    conn = db()
    conn.execute(
        """INSERT INTO guests (name, email, phone, dietary_notes, preferences,
           name_pronunciation, vip, created_at)
           VALUES (?, ?, '+33677777777', 'No shellfish', 'The quiet side',
                   'ah-MAY-lee', 1, ?)""",
        (TAG + " Amelie Fontaine", WHO, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM guests WHERE name = ?",
                       (TAG + " Amelie Fontaine",)).fetchone()
    conn.close()
    return row


def _stay(ref, room_id, offset, nights=2, linked=None, email=WHO, status="confirmed"):
    conn = db()
    today = datetime.now(m.LOCAL_TZ).date()
    start = today + timedelta(days=offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, linked_guest_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 400, ?, ?)""",
        (room_id, TAG + ref, TAG.lower() + "tok" + ref, TAG + " Amelie Fontaine",
         email, start.isoformat(), (start + timedelta(days=nights)).isoformat(),
         status, linked, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _record(guest_id):
    conn = db()
    try:
        with m.app.test_request_context():
            return m.guest_record(conn, guest_id)
    finally:
        conn.close()


def run():
    s = Suite("The whole of a guest")
    _cleanup()
    oc, ec, owner, emp = clients()

    conn = db()
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY sort_order LIMIT 2").fetchall()
    conn.close()
    g = _profile()

    s.section("Both ways of belonging, counted and distinguished")
    attached = _stay("LINK", rooms[0]["id"], -60, linked=g["id"])
    guessed = _stay("MAIL", rooms[1]["id"], -30)          # same address, not linked
    other = _stay("ELSE", rooms[0]["id"], -20, email="someone.else@example.invalid")

    rec = _record(g["id"])
    refs = {b["reference_code"] for b in rec["stays"]}
    s.check("a linked stay is on the record", TAG + "LINK" in refs)
    s.check("and one matched on the address is too", TAG + "MAIL" in refs,
            detail="a profile with nothing linked used to show no history at all")
    s.check("somebody else's stay is not", TAG + "ELSE" not in refs,
            detail=str(sorted(refs)))
    s.check("neither is counted twice", len(rec["stays"]) == 2,
            detail=f"{len(rec['stays'])}")

    matched = {b["reference_code"]: b["matched_by"] for b in rec["stays"]}
    s.check("the record says which was linked",
            matched.get(TAG + "LINK") == "linked")
    s.check("and which was only matched on an address",
            matched.get(TAG + "MAIL") == "address",
            detail="'we think this is the same person' and 'this is the same "
                   "person' are different claims")
    s.check("it counts them separately",
            rec["linked_count"] == 1 and rec["by_email_count"] == 1,
            detail=f"{rec['linked_count']} linked, {rec['by_email_count']} matched")

    s.section("The money comes from the bills, not from a second sum")
    conn = db()
    with m.app.test_request_context():
        bills = [m.booking_bill(conn, b["id"]) for b in rec["stays"]]
    conn.close()
    s.check("what they have spent is the bills added up",
            abs(rec["spent"] - sum(b["total"] for b in bills)) < 0.01,
            detail=f"{rec['spent']} vs {[b['total'] for b in bills]}")
    s.check("and so is what they still owe",
            abs(rec["owed"] - sum(b["owed"] for b in bills)) < 0.01,
            detail=f"{rec['owed']}")
    s.check("which is a real figure", rec["spent"] > 0, detail=str(rec["spent"]))
    s.check("and the nights are counted", rec["nights"] == 4,
            detail=f"{rec['nights']} — two stays of two")

    s.section("A cancelled stay is not money they spent")
    _stay("VOID", rooms[0]["id"], -10, linked=g["id"], status="cancelled")
    after = _record(g["id"])
    s.check("it is still on the record, because it happened",
            TAG + "VOID" in {b["reference_code"] for b in after["stays"]})
    s.check("but it adds nothing to what they spent",
            abs(after["spent"] - rec["spent"]) < 0.01,
            detail=f"{rec['spent']} -> {after['spent']}")

    s.section("Everything the house has sent them, in one list")
    conn = db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO email_outbox (to_address, subject, body, reason, created_at) "
        "VALUES (?, ?, 'the body', 'no provider', ?)",
        (WHO, TAG + " your stay is confirmed", now))
    conn.execute(
        "INSERT INTO email_outbox (to_address, subject, body, reason, created_at, sent_at) "
        "VALUES (?, ?, 'gone', 'ok', ?, ?)",
        (WHO, TAG + " and this one went", now, now))
    conn.execute(
        """INSERT INTO sms_outbox (phone, body, purpose, reason, created_at)
           VALUES ('+33677777777', ?, 'transactional', 'no provider', ?)""",
        (TAG + " tomorrow, here is where we are", now))
    conn.commit()
    conn.close()

    conn = db()
    with m.app.test_request_context():
        msgs = m.guest_messages(conn, g)
    conn.close()
    kinds = {x["kind"] for x in msgs}
    s.check("mail is in it", "email" in kinds, detail=str(kinds))
    s.check("and texts are too", "text" in kinds,
            detail="three tables, and no page had ever put them together")
    s.check("held is not shown as sent",
            any(x["subject"].startswith(TAG) and not x["sent"] for x in msgs),
            detail="a page that showed both the same way would say the house "
                   "had written to somebody it had not")
    s.check("and what actually went is marked as sent",
            any("and this one went" in (x["subject"] or "") and x["sent"] for x in msgs))
    s.check("newest first", [x["when"] for x in msgs] ==
            sorted([x["when"] for x in msgs], reverse=True),
            detail="a timeline out of order is a list")

    s.section("Attaching what was only matched")
    r = oc.post(f"/guests/{g['id']}/link-bookings", follow_redirects=True)
    linked_now = _record(g["id"])
    s.check("the guessed stay becomes a linked one",
            linked_now["by_email_count"] == 0 and linked_now["linked_count"] >= 2,
            detail=f"{linked_now['linked_count']} linked, "
                   f"{linked_now['by_email_count']} matched — {flashes(r)}")
    s.check("the same stays are still there",
            {b["reference_code"] for b in linked_now["stays"]}
            == {b["reference_code"] for b in after["stays"]},
            detail="attaching must not add or lose anybody's history")
    s.check("and it is on the record",
            _one("SELECT COUNT(*) AS c FROM audit_log "
                 "WHERE action = 'guest_bookings_linked'")["c"] == 1)
    s.check("somebody else's stay was not swept in",
            _one("SELECT linked_guest_id FROM bookings WHERE reference_code = ?",
                 (TAG + "ELSE",))["linked_guest_id"] is None,
            detail="matching on an address is only ever about THIS address")

    s.section("What a colleague sees, and what they do not")
    r = ec.get(f"/guests/{g['id']}")
    body = r.get_data(as_text=True)
    s.check("an employee can open the record", r.status_code == 200,
            detail=str(r.status_code))
    # The half a colleague needs before walking round the corner.
    s.check("they see how to say the name", "ah-MAY-lee" in body)
    s.check("and what not to serve them", "No shellfish" in body)
    s.check("and what the guest likes", "quiet side" in body)
    # And the half that is not theirs.
    s.check("but not what the guest has spent", "Spent with us" not in body,
            detail="a colleague needs the dietary note; the money is the "
                   "owner's business")
    s.check("nor every email the house has sent them",
            "Everything we have sent them" not in body)

    r = oc.get(f"/guests/{g['id']}")
    body = r.get_data(as_text=True)
    s.check("the owner sees the money", "Spent with us" in body)
    s.check("and the messages", "Everything we have sent them" in body)

    s.section("One statement across everything")
    r = oc.get(f"/guests/{g['id']}/statement")
    body = r.get_data(as_text=True)
    s.check("it opens", r.status_code == 200, detail=str(r.status_code))
    s.check("naming the guest", TAG + " Amelie Fontaine" in body)
    s.check("with a line for each stay", body.count("Stay total") >= 2,
            detail=f"{body.count('Stay total')} — guest_statement has always "
                   "been per booking, which cannot answer 'ever'")
    s.check("and one figure for everything taken", "Everything taken" in body)
    s.check("the cancelled one is left off entirely", TAG + "VOID" not in body,
            detail="a cancelled stay is not a line on a statement, not even a "
                   "zero")
    r = ec.get(f"/guests/{g['id']}/statement", follow_redirects=False)
    s.check("an employee cannot see the statement",
            r.status_code in (302, 303, 403), detail=f"HTTP {r.status_code}")

    s.section("A guest who does not exist")
    r = oc.get("/guests/99999999", follow_redirects=False)
    s.check("is a 404", r.status_code == 404, detail=f"HTTP {r.status_code}")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
