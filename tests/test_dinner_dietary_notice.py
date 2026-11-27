"""A dinner guest's dietary note is named to the house, never quoted (owner, 27 November 2026).

When a guest changed their dinner, the restaurant inbox was told "dietary notes now: no
shellfish" -- the note in full. The privacy notice promises a note attached to a table is
deleted once the dinner is over, and purge_health_notes does delete it from the booking.
But the house keeps its own letters two years (the mail log, the outbox) and its mailbox
for ever, so the copy in the notice outlived the promise by two years. Now:

  - THE NOTICE NAMES IT: "Dietary notes: added / changed / taken off", and links the
    kitchen's dietary list for that night, which is where the notice says the note is read.
    The words the guest typed appear nowhere in what is sent.
  - THE NOTE IS STILL SAVED and the kitchen's list for the night shows it, so nothing the
    house needs is lost: the link leads to it.
  - THE COVERS ARE STILL QUOTED. A party size is not health data.
  - ONE WORDING for every house notice about a guest's health note: the retreat page and
    the dinner page both say it through health_note_line.
  - THE COPIES ALREADY KEPT are scrubbed at startup (scrub_quoted_dinner_notes): the note
    comes out of every old dinner-changed notice in the mail log and the outbox, however
    many lines the guest typed, and the rest of the letter stays as it was.
"""
import inspect
from datetime import datetime, timedelta, timezone

import _harness
from _harness import Suite, clients, db, flashes, house_today

m = _harness.m
TAG = "ZZDN"

# Words that appear nowhere else in the app, so finding them anywhere is unambiguous.
DIETARY = "Zzdn cannot eat sesame, not a seed"
DIETARY_2 = "Zzdn no sesame and now no walnuts either"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject, body))
        return True


def _cleanup(conn):
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (f"{TAG}-%",))
    conn.execute("DELETE FROM mail_log WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM email_outbox WHERE subject LIKE ?", (f"%{TAG}%",))
    conn.commit()


def _dinner(conn, ref, party=2, dietary=None, days=40):
    when = (house_today() + timedelta(days=days)).isoformat()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
               guest_email, party_size, dinner_date, status, total_price, dietary_notes,
               created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'confirmed', 130, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"Anne {TAG}",
         f"{TAG.lower()}-{ref.lower()}@example.invalid", party, when, dietary,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT * FROM restaurant_bookings WHERE reference_code = ?",
                        (f"{TAG}-{ref}",)).fetchone()


def _change(client, booking, party, dietary):
    return client.post(f"/restaurant/manage/{booking['manage_token']}",
                       data={"action": "update", "party_size": str(party),
                             "dietary_notes": dietary}, follow_redirects=True)


def run():
    s = Suite("A dinner guest's dietary note is named to the house, never quoted")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    mail = Recorder()
    real_send = m.send_email
    m.send_email = mail
    try:
        _run(s, oc, conn, mail)
    finally:
        m.send_email = real_send
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, conn, mail):
    anon = m.app.test_client()
    house = m.house_address_for(conn, "restaurant")

    s.section("A guest adds a note to their dinner")
    b = _dinner(conn, "A")
    r = _change(anon, b, 2, DIETARY)
    s.check("the change is taken", any("updated" in f.lower() for f in flashes(r)),
            detail=f"flashes={flashes(r)}")
    row = conn.execute("SELECT * FROM restaurant_bookings WHERE id = ?", (b["id"],)).fetchone()
    s.check("and the note is on the booking, as they typed it", row["dietary_notes"] == DIETARY,
            detail=repr(row["dietary_notes"]))
    to_house = [c for c in mail.calls if c[0] == house]
    s.check("one notice goes to the restaurant's inbox", len(to_house) == 1,
            detail=f"house={house!r} calls={[(c[0], c[1]) for c in mail.calls]}")
    s.check("and nothing to anybody else", len(mail.calls) == len(to_house),
            detail=str([(c[0], c[1]) for c in mail.calls]))
    body = to_house[0][2] if to_house else ""
    subject = to_house[0][1] if to_house else ""
    s.check("the notice says a note was added", "Dietary notes: added." in body, detail=body)
    s.check("and NEVER what it says", "sesame" not in body.lower() and DIETARY not in body,
            detail=body)
    s.check("nor in the subject", "sesame" not in subject.lower(), detail=subject)
    s.check("it says where to read it, and that it is kept out of emails on purpose",
            "kitchen's dietary list" in body and "not written into emails" in body, detail=body)
    link = f"/kitchen/dietary?date={b['dinner_date']}"
    s.check("and links the kitchen's list for that night", link in body, detail=body)
    page = oc.get(link).get_data(as_text=True)
    s.check("which is where the note is", DIETARY in page,
            detail="the kitchen list for the night does not show the note")

    s.section("Changed, then taken off")
    mail.calls.clear()
    _change(anon, b, 2, DIETARY_2)
    body = mail.calls[0][2] if mail.calls else ""
    s.check("a changed note says changed", "Dietary notes: changed." in body, detail=body)
    s.check("and quotes neither the old nor the new",
            "sesame" not in body.lower() and "walnut" not in body.lower(), detail=body)
    mail.calls.clear()
    _change(anon, b, 2, "")
    body = mail.calls[0][2] if mail.calls else ""
    s.check("an emptied note says taken off", "Dietary notes: taken off." in body, detail=body)
    s.check("and does not quote what it was", "walnut" not in body.lower(), detail=body)
    row = conn.execute("SELECT dietary_notes FROM restaurant_bookings WHERE id = ?",
                       (b["id"],)).fetchone()
    s.check("and the booking holds no note", row["dietary_notes"] is None,
            detail=repr(row["dietary_notes"]))

    s.section("The covers are still quoted")
    c = _dinner(conn, "C", party=2, dietary=DIETARY)
    mail.calls.clear()
    _change(anon, c, 3, DIETARY)
    body = mail.calls[0][2] if mail.calls else ""
    s.check("a party of two that becomes three says so", "party of 2 → 3" in body, detail=body)
    s.check("and an unchanged note is not mentioned at all",
            "Dietary notes" not in body and "sesame" not in body.lower(), detail=body)

    s.section("One wording for every notice about a health note")
    manage_src = inspect.getsource(m.restaurant_manage)
    retreat_src = inspect.getsource(m.guest_details_changes)
    s.check("the dinner page says it through health_note_line",
            "health_note_line(" in manage_src and "dietary notes now" not in manage_src)
    s.check("and so does the retreat page", "health_note_line(" in retreat_src
            and "not written into emails" not in retreat_src)
    s.check("the line itself never carries the note",
            "sesame" not in m.health_note_line("Dietary notes", "", DIETARY, "here").lower()
            and "sesame" not in m.health_note_line("Dietary notes", DIETARY, DIETARY_2,
                                                   "here").lower())

    s.section("The copies already kept lose the note")
    now = datetime.now(timezone.utc).isoformat()
    old = (f"Anne {TAG} changed their reservation for Fri 4 Dec:\n\n"
           "- party of 2 → 3\n"
           "- dietary notes now: Zzdn line one, shellfish\nZzdn line two, and nuts\n\n"
           "Total now €195.00.\n\nThey have already paid. The total has been recalculated.")
    log_id = conn.execute(
        "INSERT INTO mail_log (to_address, subject, status, body, kept, created_at) "
        "VALUES ('house@example.invalid', ?, 'sent', ?, 'here', ?)",
        (f"Dinner reservation changed — {TAG}-OLD", old, now)).lastrowid
    box_id = conn.execute(
        "INSERT INTO email_outbox (to_address, subject, body, reason, created_at, sent_at) "
        "VALUES ('house@example.invalid', ?, ?, 'test', ?, ?)",
        (f"Dinner reservation changed — {TAG}-OLD", old, now, now)).lastrowid
    other_id = conn.execute(
        "INSERT INTO mail_log (to_address, subject, status, body, kept, created_at) "
        "VALUES ('house@example.invalid', ?, 'sent', ?, 'here', ?)",
        (f"Something else entirely — {TAG}", "- dietary notes now: Zzdn keep\n\nTotal now €1.", now)
    ).lastrowid
    conn.commit()
    n = m.scrub_quoted_dinner_notes(conn)
    kept = conn.execute("SELECT body FROM mail_log WHERE id = ?", (log_id,)).fetchone()["body"]
    boxed = conn.execute("SELECT body FROM email_outbox WHERE id = ?", (box_id,)).fetchone()["body"]
    other = conn.execute("SELECT body FROM mail_log WHERE id = ?", (other_id,)).fetchone()["body"]
    s.check("both kept copies are scrubbed", n == 2, detail=f"scrubbed {n}")
    s.check("the note is gone from the mail log's copy, every line of it",
            "Zzdn line" not in kept and "shellfish" not in kept and "nuts" not in kept, detail=kept)
    s.check("and from the outbox's", "Zzdn line" not in boxed, detail=boxed)
    s.check("the copy says a note changed and was not kept",
            m.QUOTED_DINNER_NOTE_KEPT_AS in kept, detail=kept)
    s.check("and the rest of the letter is as it was",
            "- party of 2 → 3" in kept and "Total now €195.00." in kept
            and "They have already paid." in kept, detail=kept)
    s.check("a letter that is not a dinner change is left alone", "Zzdn keep" in other,
            detail=other)
    s.check("run again, it finds nothing", m.scrub_quoted_dinner_notes(conn) == 0)
    s.check("it runs at every start", "scrub_quoted_dinner_notes(conn)" in inspect.getsource(m.init_db))
