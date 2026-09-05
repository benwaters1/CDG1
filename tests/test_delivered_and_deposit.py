"""Who ticked off a delivery, and what a workshop really costs to reserve.

WHO TICKED IT OFF. `booking_extras` carries `delivered_at` and
`delivered_by_user_id`, both written by `mark_extra_delivered` and read by
nothing. A delivered line drops off `extras_due` — which is the point of that
list — and no other page showed it, so from the moment somebody ticked it the
record was invisible. When a guest says the champagne never came, the honest
answer was "somebody ticked it off at some point".

WHAT A WORKSHOP COSTS TO RESERVE. The public page stated, flatly:

    Deposit   30% to reserve; the balance thirty days before

The second half is true. The first was true only while every workshop happened
to be set to 30 — `deposit_percent` is a column on each workshop with a
default, not a house rule — and the page stated it as one.

And it left out the part that costs a guest money. `compute_workshop_payment_terms`:

    if (start_date - today).days < 30:
        return round(total_price, 2), 0.0, None

Book inside thirty days and the WHOLE amount falls due at once. Somebody
reading "30% to reserve" three weeks before a session was told one figure and
charged another, on the page where they decided — the room-deposit lie again,
in the one place nobody had looked.
"""
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room

import _harness

m = _harness.m
TAG = "delivtest-"


def _cleanup(conn):
    conn.execute("DELETE FROM booking_extras WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("A delivery with a name on it, and an honest deposit")
    oc, ec, _owner, emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    night = m.service_day()
    opened = m.parse_datetime_iso(m.service_day_window(night)[0])

    def tonight(hours_ago):
        """A moment `hours_ago` before now, but never before this service began.

        The window is 23, 24 or 25 hours long and starts at five in the
        morning. Anchoring a fixture to "now minus two hours" puts it in
        yesterday's service for the first two hours of every day -- which is a
        suite that goes red at breakfast and green by nine, twice as confusing
        as one that is simply wrong.
        """
        return max(now - timedelta(hours=hours_ago), opened + timedelta(minutes=1))

    room = ensure_room()["id"]

    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Porter", TAG + "porter@example.invalid", now.isoformat()))
    conn.commit()
    porter = conn.execute("SELECT id FROM users WHERE email = ?",
                          (TAG + "porter@example.invalid",)).fetchone()["id"]

    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                   guest_email, arrival_date, departure_date, party_size, status,
                   total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
        (room, TAG + "STAY", TAG + "tok", TAG + " Guest",
         TAG + "g@example.invalid", (night - timedelta(days=1)).isoformat(),
         (night + timedelta(days=2)).isoformat(), now.isoformat()))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (TAG + "STAY",)).fetchone()["id"]

    def extra(name, status, *, delivered_at=None, by=None):
        conn.execute(
            """INSERT INTO booking_extras (booking_id, name, quantity, unit_price,
                       status, delivered_at, delivered_by_user_id, created_at)
               VALUES (?, ?, 1, 60.0, ?, ?, ?, ?)""",
            (bid, TAG + name, status,
             delivered_at.isoformat() if delivered_at else None, by,
             now.isoformat()))
        conn.commit()

    extra("still-due", "confirmed")
    extra("champagne", "delivered",
          delivered_at=tonight(2), by=porter)
    extra("anonymous", "delivered",
          delivered_at=tonight(1) + timedelta(minutes=1), by=None)
    # Delivered a week ago. Today's list only — a running history of every
    # hamper the house has ever carried upstairs is a page nobody opens twice.
    extra("last-week", "delivered",
          delivered_at=now - timedelta(days=7), by=porter)

    s.section("What was ticked off tonight, and by whom")
    done = m.extras_delivered_today(conn, night)
    mine = {r["name"].replace(TAG, ""): r for r in done
            if str(r["name"]).startswith(TAG)}
    s.check("tonight's deliveries are found", "champagne" in mine,
            detail=str(sorted(mine)))
    s.check("with the name against them",
            mine.get("champagne", {}).get("delivered_by") == TAG + "Porter",
            detail=str(mine.get("champagne", {}).get("delivered_by")))
    s.check("and the time it happened",
            bool(mine.get("champagne", {}).get("when")),
            detail=str(mine.get("champagne", {}).get("when")))
    s.check("something still due is not on it", "still-due" not in mine,
            detail="that list is above it and this one is what has been done")
    # Directly, because the check above passed for the wrong reason: the
    # still-due fixture has no delivered_at either, so the date window
    # excluded it and the status filter was never exercised at all.
    s.check("and everything on the list has actually been delivered",
            all(r["status"] == "delivered" for r in done),
            detail=str({r["name"]: r["status"] for r in done
                        if r["status"] != "delivered"}))
    s.check("and last week's is not either", "last-week" not in mine,
            detail="a running history of every hamper the house has ever "
                   "carried upstairs is a page nobody opens twice")

    s.section("A tick with no name says so rather than blaming somebody")
    s.check("it is still listed", "anonymous" in mine)
    s.check("with nobody named",
            mine.get("anonymous", {}).get("delivered_by") is None,
            detail="rows written before the column existed have no name, and "
                   "attaching the nearest one would be worse than a blank")

    s.section("The page whoever is on shift already has open")
    body = ec.get("/extras/due").get_data(as_text=True)
    s.check("the page renders for an employee",
            ec.get("/extras/due").status_code == 200)
    s.check("with what is still due", TAG + "still-due" in body)
    s.check("and what has been done tonight", TAG + "champagne" in body)
    s.check("naming who did it", TAG + "Porter" in body,
            detail="asked while carrying the next one upstairs, not by going "
                   "to another page")
    s.check("a tick with no name reads as nobody recorded",
            "nobody recorded" in body)

    s.section("Ticking one off through the route puts it on the list")
    line = conn.execute(
        "SELECT id FROM booking_extras WHERE name = ?",
        (TAG + "still-due",)).fetchone()["id"]
    ec.post(f"/extras/{line}/delivered", data={}, follow_redirects=True)
    after = {r["name"].replace(TAG, "") for r in
             m.extras_delivered_today(conn, night)
             if str(r["name"]).startswith(TAG)}
    s.check("it moves across", "still-due" in after, detail=str(sorted(after)))
    named = next((r for r in m.extras_delivered_today(conn, night)
                  if r["name"] == TAG + "still-due"), None)
    s.check("carrying the name of whoever ticked it",
            named and named["delivered_by"] == emp["name"],
            detail=f"{named['delivered_by'] if named else None} vs "
                   f"{emp['name']}")

    # ---------------------------------------------------------- the deposit
    s.section("What a workshop costs to reserve is read, not stated")
    s.check("the house has one answer today",
            m.workshop_deposit_to_show(conn) == 30,
            detail=str(m.workshop_deposit_to_show(conn)))
    page = " ".join(m.app.test_client().get("/workshops")
                    .get_data(as_text=True).split())
    s.check("and the page prints that answer", "30% to reserve" in page)
    # Against what the MONEY does, not against the constant. Asking whether
    # the page says WORKSHOP_BALANCE_DAYS days reads the same variable on
    # both sides, so changing it changed both and the check followed along.
    far_off = m.house_today() + timedelta(days=200)
    _dep, _bal, due_iso = m.compute_workshop_payment_terms(1000.0, 30, far_off)
    real_days = (far_off - m.parse_date(due_iso)).days if due_iso else None
    s.check("the balance date the money uses is a real number of days",
            real_days is not None, detail=str(due_iso))
    s.check("and the page states that same number",
            real_days is not None
            and f"the balance {real_days} days before" in page,
            detail=f"the money charges the balance {real_days} days before; "
                   f"{page[page.find('to reserve') - 40:page.find('to reserve') + 70]!r}")

    s.section("And the part that costs a guest money is on the page")
    s.check("booking inside the window means the whole amount",
            "The whole amount is due then" in page,
            detail="somebody reading '30% to reserve' three weeks out was "
                   "told one figure and charged another, on the page where "
                   "they decided")

    s.section("When the workshops disagree, the page says it depends")
    one = conn.execute(
        "SELECT id, deposit_percent FROM workshops WHERE active = 1 "
        "ORDER BY id LIMIT 1").fetchone()
    if one:
        conn.execute("UPDATE workshops SET deposit_percent = 55 WHERE id = ?",
                     (one["id"],))
        conn.commit()
        s.check("the house has no single answer any more",
                m.workshop_deposit_to_show(conn) is None,
                detail=str(m.workshop_deposit_to_show(conn)))
        varied = " ".join(m.app.test_client().get("/workshops")
                          .get_data(as_text=True).split())
        s.check("and the page says so rather than picking one",
                "It depends on the workshop" in varied,
                detail="a guest shown 30% who is charged 55% has been told a "
                       "wrong figure about their own money")
        s.check("without printing either number as though it were the rule",
                "30% to reserve" not in varied and "55% to reserve" not in varied,
                detail=varied[varied.find("Deposit"):][:160])
        conn.execute("UPDATE workshops SET deposit_percent = ? WHERE id = ?",
                     (one["deposit_percent"], one["id"]))
        conn.commit()
    else:
        s.check("there is a workshop to vary", False,
                detail="reported rather than skipped: the checks above would "
                       "pass on an empty catalogue")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
