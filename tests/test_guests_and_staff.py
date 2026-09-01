"""Six more: three about guests, three about staff.

WHY A CANCELLATION HAPPENED. The app wrote status = 'cancelled' and nothing
else, so a guest cancelling because the price went up and a guest cancelling
because their mother is ill were the same row.

  - ASKED, NEVER REQUIRED. A guest cancelling in a hurry does not owe the
    house an explanation, and a mandatory field collects a lie or an empty
    string. Most reasons arrive on the telephone, so they can be added
    afterwards.
  - AND CANCELLATIONS WITH NOTHING AGAINST THEM ARE COUNTED, not dropped.
    Most will have none, and a chart of only the explained ones makes a
    handful of answers look like the whole picture.

ENQUIRIES THAT BECAME SOMETHING. Counted against the ones that GOT AN
ANSWER. Treating still-open enquiries as failures would make the current
month always look terrible, because this month's enquiries have not had
time to become anything.

RETURNING A DEPOSIT. Taking one was built and returning one was not.
Recording the return moves no money — that is a bank transfer or a Stripe
refund, made deliberately, exactly as a refund is.

SHIFT HANDOVER. Not tasks: a task is something to DO and this is something
to KNOW. Who has read one is recorded, because a note nobody read is the
same as no note.

MILEAGE. The rate is COPIED ONTO THE CLAIM as it is made. Reading the
setting back later would mean changing the rate silently restates what
somebody was already paid.

EXIT INTERVIEWS. Deliberately not in the `team` access area — it is written
after the working relationship ended, and a manager should not be able to
read what somebody said about them on the way out.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ZZGS"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM shift_handover_reads WHERE handover_id IN "
                 "(SELECT id FROM shift_handovers WHERE body LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM shift_handovers WHERE body LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mileage_claims WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM exit_interviews WHERE person_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Guests and staff")
    conn = db()
    oc, ec, _owner, emp = clients()
    _cleanup(conn)
    today = date.today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()

    def cancelled(name, reason=None):
        conn.execute(
            """INSERT INTO bookings (room_id, guest_name, guest_email,
                       arrival_date, departure_date, party_size, status,
                       manage_token, reference_code, cancel_reason,
                       decided_at, created_at)
               VALUES (?, ?, 'c@example.invalid', ?, ?, 2, 'cancelled', ?, ?, ?, ?, ?)""",
            (room["id"], TAG + " " + name, today.isoformat(),
             (today + timedelta(days=2)).isoformat(),
             f"tok-{TAG.lower()}-{name}", f"{TAG}{name.upper()}", reason,
             now, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    a = cancelled("Aline", "price")
    b = cancelled("Bruno", "illness")
    c = cancelled("Chantal")          # nobody asked
    conn.commit()

    s.section("Why they cancelled")
    reasons = m.cancellation_reasons(conn, today=today)
    labels = {r["key"]: r for r in reasons["rows"]}
    s.check("a reason is counted", labels.get("price", {}).get("count", 0) >= 1,
            detail=str([(r["key"], r["count"]) for r in reasons["rows"]]))
    s.check("and the ones nobody asked about are counted too",
            any(r["unexplained"] for r in reasons["rows"]),
            detail="a chart of only the explained ones makes a handful of "
                   "answers look like the whole picture")
    s.check("they are not silently dropped from the total",
            reasons["total"] >= 3 and reasons["explained"] >= 2,
            detail=f"{reasons['explained']} explained of {reasons['total']}")

    s.section("A reason can be added afterwards")
    # Most arrive on the telephone; a reason nobody could add later would be
    # a field that is almost always empty.
    r = oc.post(f"/management/cancellations/{c}/reason",
                data={"reason": "plans_changed", "cancel_note": "family thing"},
                follow_redirects=True)
    got = conn.execute("SELECT cancel_reason, cancel_note FROM bookings WHERE id = ?",
                       (c,)).fetchone()
    s.check("it is written down", got["cancel_reason"] == "plans_changed",
            detail=str(flashes(r)))
    s.check("with the note", got["cancel_note"] == "family thing")

    r = oc.post(f"/management/cancellations/{c}/reason",
                data={"reason": "invented_reason"}, follow_redirects=True)
    s.check("a reason that is not on the list is refused",
            any("not one of the reasons" in f for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and the old one survives",
            conn.execute("SELECT cancel_reason FROM bookings WHERE id = ?",
                         (c,)).fetchone()["cancel_reason"] == "plans_changed")

    s.section("Enquiries that got an answer")
    for name, status in (("Won", "confirmed"), ("Lost", "declined"),
                         ("Waiting", "new")):
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token,
                       event_type, contact_name, contact_email, guest_count,
                       status, created_at)
               VALUES (?, ?, 'wedding', ?, 'e@example.invalid', 40, ?, ?)""",
            (f"{TAG}{name.upper()}", f"tok-{TAG.lower()}-{name}",
             TAG + " " + name, status, now))
    conn.commit()
    conv = m.enquiry_conversion(conn, today=today)
    s.check("one that became a booking is counted as won", conv["won"] >= 1,
            detail=str(conv))
    s.check("one still open is not counted as lost", conv["open"] >= 1,
            detail=f"{conv['open']} open — counting them as failures would "
                   "make the current month always look terrible")
    s.check("and the rate is of the ones that got an answer",
            conv["rate"] == round(conv["won"] / conv["settled"] * 100),
            detail=f"{conv['rate']}% of {conv['settled']} settled")

    s.section("Returning a deposit")
    conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
                   departure_date, party_size, status, manage_token,
                   reference_code, deposit_amount, created_at)
           VALUES (?, ?, 'd@example.invalid', ?, ?, 2, 'confirmed', ?, ?, 200, ?)""",
        (room["id"], TAG + " Deposit", today.isoformat(),
         (today + timedelta(days=2)).isoformat(), f"tok-{TAG.lower()}-dep",
         TAG + "DEP", now))
    conn.commit()
    dep = conn.execute("SELECT id FROM bookings WHERE guest_name = ?",
                       (TAG + " Deposit",)).fetchone()["id"]

    r = oc.post(f"/management/deposits/{dep}/return", data={"amount": ""},
                follow_redirects=True)
    s.check("returning nothing is refused",
            any("how much" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    r = oc.post(f"/management/deposits/{dep}/return",
                data={"amount": "150", "note": "kept 50 for the lamp"},
                follow_redirects=True)
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (dep,)).fetchone()
    s.check("part of a deposit is a real answer",
            row["deposit_returned_amount"] == 150.0, detail=str(flashes(r)))
    s.check("with the reason on it",
            row["deposit_returned_note"] == "kept 50 for the lamp")
    s.check("and the wording does not claim money was sent",
            any("Nothing has been sent" in f for f in flashes(r)),
            detail=f"{flashes(r)} — a bank transfer or a Stripe refund, made "
                   "deliberately")

    r = oc.post(f"/management/deposits/{dep}/return", data={"amount": "150"},
                follow_redirects=True)
    s.check("returning it twice is refused",
            any("already recorded" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("What one shift tells the next")
    ec.post("/handover", data={"body": TAG + " boiler making a noise"},
            follow_redirects=True)
    notes = m.handover_notes(conn, days=7, user_id=emp["id"] if emp else None)
    mine = [n for n in notes if n["note"]["body"].startswith(TAG)]
    s.check("a note can be left", len(mine) == 1, detail=str(len(mine)))
    s.check("and the person who wrote it has not 'read' it",
            not mine[0]["read_by_me"],
            detail="a note nobody read is the same as no note, so this is "
                   "recorded rather than assumed")

    note_id = mine[0]["note"]["id"]
    ec.post(f"/handover/{note_id}/read", follow_redirects=True)
    after = [n for n in m.handover_notes(conn, days=7,
                                         user_id=emp["id"] if emp else None)
             if n["note"]["id"] == note_id][0]
    s.check("reading it is recorded", after["read_by_me"])
    s.check("and the count goes up", after["note"]["read_count"] == 1,
            detail=str(after["note"]["read_count"]))
    # A double-click, which is what this actually protects against. The
    # UNIQUE index keeps the count right either way; what INSERT OR IGNORE
    # adds is that the second press is not a 500.
    second = ec.post(f"/handover/{note_id}/read", follow_redirects=False)
    twice = [n for n in m.handover_notes(conn, days=7) if n["note"]["id"] == note_id][0]
    s.check("reading it twice does not count twice",
            twice["note"]["read_count"] == 1,
            detail=str(twice["note"]["read_count"]))
    s.check("and does not throw",
            second.status_code in (200, 302, 303),
            detail=f"HTTP {second.status_code} — the index keeps the count "
                   "right on its own; what OR IGNORE adds is that a "
                   "double-click is not an error page")

    s.section("Mileage, priced at the rate as it was")
    rate = m.mileage_rate(conn)
    ec.post("/mileage", data={"kilometres": "40", "from_place": "Gudanes",
                              "to_place": "Foix", "reason": TAG + " the bank"},
            follow_redirects=True)
    claim = conn.execute("SELECT * FROM mileage_claims WHERE reason LIKE ?",
                         (TAG + "%",)).fetchone()
    s.check("a claim is priced by the rate", claim["amount"] == round(40 * rate, 2),
            detail=f"{claim['amount']} at {rate}")
    s.check("and starts pending", claim["status"] == "pending")

    # The whole reason the rate is copied rather than looked up.
    conn.execute(
        """INSERT INTO app_settings (key, value) VALUES (?, '99')
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (m.MILEAGE_RATE_SETTING,))
    conn.commit()
    unchanged = conn.execute("SELECT amount, rate FROM mileage_claims WHERE id = ?",
                             (claim["id"],)).fetchone()
    s.check("changing the rate does not restate what was already claimed",
            unchanged["amount"] == claim["amount"] and unchanged["rate"] == rate,
            detail=f"{unchanged['amount']} at {unchanged['rate']} — reading "
                   "the setting back later would silently rewrite what "
                   "somebody was already paid")
    # And the PAGE must show what was claimed, not what a kilometre is
    # worth today. Recomputing on display is the same lie in a different
    # place.
    page = ec.get("/mileage").get_data(as_text=True)
    s.check("the page shows the rate the claim was made at",
            f"{claim['rate']:.2f}" in page,
            detail=f"expected {claim['rate']:.2f} with the setting now at 99")

    conn.execute("DELETE FROM app_settings WHERE key = ?",
                 (m.MILEAGE_RATE_SETTING,))
    conn.commit()

    s.section("An employee cannot approve their own mileage")
    r = ec.post(f"/mileage/{claim['id']}/decide", data={"state": "approved"},
                follow_redirects=False)
    s.check("it is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and it is still pending",
            conn.execute("SELECT status FROM mileage_claims WHERE id = ?",
                         (claim["id"],)).fetchone()["status"] == "pending")

    s.section("Exit interviews are held apart from the team area")
    # A manager with team access should not be able to read what somebody
    # said about them on the way out.
    # Named against the real constant. The first version fell back to True
    # when it could not find the map, which is a check that cannot fail --
    # and it could not find it, because the map is NAV_AREAS.
    s.check("the team area exists to be checked against",
            "team" in m.NAV_AREAS, detail=str(sorted(m.NAV_AREAS))[:120])
    s.check("and the exit interview page is not in it",
            "exit_interviews" not in m.NAV_AREAS["team"],
            detail="written after the working relationship ended; a manager "
                   "should not be able to read what somebody said about "
                   "them on the way out")
    r = ec.get("/admin/exit-interviews", follow_redirects=False)
    s.check("an employee cannot open it", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    r = oc.post("/admin/exit-interviews",
                data={"person_name": TAG + " Leaver",
                      "why_leaving": "moving away",
                      "what_did_not": "the winter"}, follow_redirects=True)
    row = conn.execute("SELECT * FROM exit_interviews WHERE person_name LIKE ?",
                       (TAG + "%",)).fetchone()
    s.check("the owner can record one", row is not None, detail=str(flashes(r)))
    s.check("with what did not work on it",
            row and row["what_did_not"] == "the winter")

    r = oc.post("/admin/exit-interviews", data={"person_name": ""},
                follow_redirects=True)
    s.check("one with nobody's name is refused",
            any("whose interview" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
