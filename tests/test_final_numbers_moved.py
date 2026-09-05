"""Final numbers that were not final, and a date nobody could see.

The run sheet knows two things about the headcount: whether a number has been
given, and whether the deadline passed without one. Both matter and both work.

What it could not say is WHEN the number was fixed, or whether it is still
that number. `final_numbers_at` records the first — written with a COALESCE so
it keeps the first time a figure was given — and was read by nothing at all.
The second was not recorded anywhere: `save_event_run_details` overwrites
`final_numbers`, so a wedding that went from eighty to a hundred and twenty a
week before the day read exactly like one that was eighty all along.

WHICH IS MONEY. An event is priced per head. Forty more dinners, forty more
chairs, forty more of everything — and the run sheet said "confirmed" in the
same green as before. The kitchen orders to the number on the sheet and the
invoice is built from the same one; nothing anywhere said it had moved, when,
or by how much.

The first figure is now kept beside the first date, and the run sheet says
both — and if the number moved AFTER the deadline it says that too, because
that is the version somebody has to re-price rather than simply note.
"""
from datetime import timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "numtest-"


def _cleanup(conn):
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.commit()


def run():
    s = Suite("A headcount that moved after it was final")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    def event(ref, days_away):
        conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token,
                       contact_name, contact_email, event_type, preferred_date,
                       guest_count, status, created_at)
               VALUES (?, ?, ?, ?, 'wedding', ?, 80, 'confirmed', ?)""",
            (TAG + ref, (TAG + ref).lower(), TAG + " Couple",
             TAG + "c@example.invalid",
             (today + timedelta(days=days_away)).isoformat(), now.isoformat()))
        conn.commit()
        return conn.execute("SELECT id FROM event_inquiries WHERE reference_code = ?",
                            (TAG + ref,)).fetchone()["id"]

    def give_numbers(eid, n):
        return oc.post(f"/admin/events/{eid}/run-sheet/details",
                       data={"final_numbers": str(n), "arrival_time": "",
                             "carriages_time": "", "run_sheet_note": ""},
                       follow_redirects=True)

    # Well outside the window, so numbers given now are given in good time.
    early = event("EARLY", 90)

    s.section("The first figure and the first date are both kept")
    give_numbers(early, 80)
    row = conn.execute(
        "SELECT final_numbers, final_numbers_first, final_numbers_at "
        "FROM event_inquiries WHERE id = ?", (early,)).fetchone()
    s.check("the number is recorded", row["final_numbers"] == 80,
            detail=str(dict(row)))
    s.check("and the first figure with it", row["final_numbers_first"] == 80,
            detail=str(dict(row)))
    s.check("and when it was given", bool(row["final_numbers_at"]),
            detail="recorded since the column existed and read by nothing")

    first_at = row["final_numbers_at"]
    give_numbers(early, 120)
    row = conn.execute(
        "SELECT final_numbers, final_numbers_first, final_numbers_at "
        "FROM event_inquiries WHERE id = ?", (early,)).fetchone()
    s.check("changing it moves the current number", row["final_numbers"] == 120)
    s.check("but not the first one", row["final_numbers_first"] == 80,
            detail=f"{dict(row)} — without this, eighty to a hundred and "
                   "twenty reads exactly like eighty all along")
    s.check("nor the date it was first fixed",
            row["final_numbers_at"] == first_at,
            detail=str(row["final_numbers_at"]))

    s.section("The run sheet says it moved")
    body = " ".join(oc.get(f"/admin/events/{early}/run-sheet")
                    .get_data(as_text=True).split())
    s.check("the page renders",
            oc.get(f"/admin/events/{early}/run-sheet").status_code == 200)
    s.check("naming both figures",
            "The headcount has changed since it was fixed" in body
            and "80 to 120" in body,
            detail=body[body.find("headcount has changed"):][:140])
    s.check("and saying when it was fixed", "fixed " in body,
            detail="a date recorded and never shown")
    # Both halves, because a version that called every change late passed
    # everything else — this one is ninety days out and inside the window,
    # so it is a correction to note rather than a quote to redo.
    s.check("but not calling a change made in good time a late one",
            "after the deadline" not in body,
            detail=body[body.find("headcount has changed"):][:170])
    s.check("and not asking anybody to re-price it",
            "to re-price" not in body,
            detail="the kitchen has not ordered and the quote has not gone "
                   "out; saying so anyway is how a real warning stops being "
                   "read")

    s.section("Moving it after the deadline is a different sentence")
    # Inside the window, so numbers given now are given late.
    late = event("LATE", 3)
    give_numbers(late, 80)
    conn.execute(
        # Backdate the first fixing so it sits before the deadline, which is
        # what makes the change a LATE one rather than a correction inside
        # the window.
        "UPDATE event_inquiries SET final_numbers_at = ? WHERE id = ?",
        ((now - timedelta(days=40)).isoformat(), late))
    conn.commit()
    give_numbers(late, 130)
    late_body = " ".join(oc.get(f"/admin/events/{late}/run-sheet")
                         .get_data(as_text=True).split())
    s.check("it says the change came after the deadline",
            "after the deadline" in late_body,
            detail=late_body[late_body.find("headcount has changed"):][:170])
    s.check("with the number of covers to re-price",
            "50 more covers to re-price" in late_body,
            detail="the kitchen has ordered to the old figure and the quote "
                   "went out on it")

    s.section("A headcount that never moved says nothing at all")
    steady = event("STEADY", 60)
    give_numbers(steady, 95)
    quiet = " ".join(oc.get(f"/admin/events/{steady}/run-sheet")
                     .get_data(as_text=True).split())
    s.check("no banner about it changing",
            "headcount has changed" not in quiet,
            detail="a line that is always there is furniture, and this one "
                   "sits beside a warning that is not")
    s.check("but the date it was fixed is still shown", "fixed " in quiet)

    s.section("Clearing the numbers clears the record with them")
    oc.post(f"/admin/events/{steady}/run-sheet/details",
            data={"final_numbers": "", "arrival_time": "",
                  "carriages_time": "", "run_sheet_note": ""},
            follow_redirects=True)
    row = conn.execute(
        "SELECT final_numbers, final_numbers_first, final_numbers_at "
        "FROM event_inquiries WHERE id = ?", (steady,)).fetchone()
    s.check("all three go",
            row["final_numbers"] is None and row["final_numbers_first"] is None
            and row["final_numbers_at"] is None,
            detail=f"{dict(row)} — re-entering a number after a cancellation "
                   "starts a fresh record rather than comparing against a "
                   "figure from a different conversation")

    s.section("It is the owner's page")
    s.check("an employee cannot open the run sheet",
            ec.get(f"/admin/events/{early}/run-sheet").status_code in (302, 403))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
