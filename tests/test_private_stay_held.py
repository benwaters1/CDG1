"""The RetreatGuru private stay, brought in and holding the château (25 Nov 2026).

BRIEF-private-retreat-and-images, 1b. RetreatGuru's "Four Starry French Summer Nights – A Private Stay" (18-22 June
2027, four guests, €12,000) now sends anyone with its old link to /events/private, and every other RetreatGuru
programme is closed to new registrations: bookings come through this site only.

  - /events/private answers at that address after the rework: a page, not a redirect, and on no list of old
    addresses that would move it.
  - The four already registered come in through the RetreatGuru import (plan_rg_import / run_rg_import, the owner's
    Bring Registrations Across), onto the private stay's own sitting: confirmed, emails off, nothing sent.
  - The stay is on the house calendar on every day it covers, not only its first.
  - The château is held for those nights: no room is offered (and a guest is told only that the dates are held
    for a private stay, never whose), the public /book says so, and no occasion can be put on sale over them.
    The night it ends is free again.

On live this was done on 9 Nov (workshop 12, its sitting 18-22 June 2027, the four brought across with emails off),
and /book on www says "a retreat holds the whole château" for those nights. This suite holds the path, on a private
stay of its own, so it stays true.
"""
import re
from datetime import timedelta

from _harness import Suite, db, visible_text
import _harness

m = _harness.m
house_today = m.house_today

TAG = "ZZPSH"
LOW = TAG.lower()
TITLE = f"{TAG} Four Starry Nights – A Private Stay"
REG_HEADER = ("first_name,last_name,submitted,cancelled,booking_window,reg_form_complete,phone,"
              "payment,optional_items,roommate,discount_code,travel_phone_,items_discounts_total,"
              "payments_refunds_total,balance_due,scheduled_payments_total,"
              "balance_after_scheduled,registration_amount")
STRIPE_HEADER = ("id,PaymentIntent ID,Created date (UTC),Amount,Amount Refunded,Currency,Status,"
                 "Customer Email,Description")
# Four guests at €3,000 each, as on RetreatGuru: one has paid her deposit, three in full.
GUESTS = (("Barb", 600), ("Kim", 3000), ("Terri", 3000), ("Rose", 3000))


def _email(first):
    return f"{LOW}.{first.lower()}@example.invalid"


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    like = f"{LOW}%"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM import_drafts")
    conn.commit()
    conn.close()


def _private_stay():
    """A private stay like workshop 12: €3,000, 20% deposit, ten places, booked only by its own link, four nights
    on dates nothing else in the house holds (the import finds a sitting by the day it starts)."""
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity, active, sort_order,
             created_at, deposit_percent, private_token)
           VALUES (?, '', 3000, 10, 1, 94, ?, 20, ?)""", (TITLE, now, f"{LOW}-token"))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (TITLE,)).fetchone()["id"]
    taken = [(r["start_date"], r["end_date"]) for r in conn.execute(
        "SELECT start_date, end_date FROM workshop_sessions WHERE cancelled_at IS NULL")]
    booked = [(r["arrival_date"], r["departure_date"]) for r in conn.execute(
        "SELECT arrival_date, departure_date FROM bookings WHERE status NOT IN ('cancelled', 'declined')")]
    start = house_today() + timedelta(days=200)
    while any(a and b and a <= (start + timedelta(days=5)).isoformat() and b >= (start - timedelta(days=1)).isoformat()
              for a, b in taken + booked):
        start += timedelta(days=1)
    end = start + timedelta(days=4)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 10, ?, ?)""", (wid, start.isoformat(), end.isoformat(), f"{TAG} sitting", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} sitting",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return wid, sid, start, end


def _files(start):
    """RetreatGuru's three exports and Stripe's, for the four."""
    regs = [REG_HEADER]
    for minute, (first, paid) in enumerate(GUESTS):
        submitted = f"{(start - timedelta(days=60)).isoformat()} 10:{minute:02d}:00"
        owing = 3000 - paid
        regs.append(",".join([first, f"{TAG}{first}", submitted, "", "60", "x", "", "", "", "", "", "",
                              "3000.00", f"{paid:.2f}", f"{owing:.2f}", "0", f"{owing:.2f}", "3000.00"]))
    people = "first_name,last_name,person_id,email,num_programs,overall_balance\n" + "\n".join(
        f"{f},{TAG}{f},{i},{_email(f)},1,0" for i, (f, _p) in enumerate(GUESTS)) + "\n"
    when = (start - timedelta(days=60)).isoformat()
    stripe = STRIPE_HEADER + "\n" + "\n".join(
        f'ch_{LOW}_{f.lower()},pi_{LOW}_{f.lower()},{when} 10:30:00,"{p},00","0,00",eur,Paid,{_email(f)},'
        "RetreatGuru registration" for f, p in GUESTS) + "\n"
    return "\n".join(regs) + "\n", people, stripe


def _on(cell, text):
    return [e for e in (cell.get("events") or []) if text in (e.get("title") or "")]


def run():
    s = Suite("The RetreatGuru private stay, brought in and holding the château")
    _cleanup()
    try:
        _run(s)
    finally:
        _cleanup()
    return s


def _run(s):
    client = m.app.test_client()

    s.section("/events/private answers at the address RetreatGuru now sends people to")
    r = client.get("/events/private")
    s.check("it is a page, not a redirect", r.status_code == 200, detail=str(r.status_code))
    s.check("and it is the private retreat", "A Private Retreat" in visible_text(r.get_data(as_text=True)))
    moved = [p for p in m.LEGACY_PATHS if "/events/private" in str(p[0] if isinstance(p, (tuple, list)) else p)]
    s.check("no old-address rule would move it", not moved, detail=str(moved))

    wid, sid, start, end = _private_stay()
    regs_text, people_text, stripe_text = _files(start)

    s.section("The four come in through the RetreatGuru import, onto the private stay")
    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, *a, **k: sent.append((to, subject)) or True
    try:
        with m.app.test_request_context():
            conn = db()
            regs, _c = m.parse_rg_registrations(regs_text, house_today())
            people, names = m.parse_rg_people(people_text)
            plan = m.plan_rg_import(conn, regs, {}, names, m.parse_stripe_payments(stripe_text),
                                    due_days=30, today=house_today(), stripe_given=True)
            lines = plan["payers"]
            s.check("the check finds all four", len(lines) == 4, detail=str(len(lines)))
            s.check("each on the private stay's own sitting",
                    lines and all(l["session"] and l["session"]["id"] == sid for l in lines),
                    detail=str([(l["key"], l["session"] and l["session"]["id"]) for l in lines]))
            s.check("with nothing in the way", not [b for l in lines for b in l["blocking"]],
                    detail=str([b for l in lines for b in l["blocking"]]))
            made, kept, problems = m.run_rg_import(conn, plan, {l["key"] for l in lines}, people, user_id=None)
            if problems:
                conn.rollback()
            else:
                conn.commit()
            s.check("bringing them across goes through", not problems, detail=str(problems))
            rows = conn.execute(
                """SELECT * FROM workshop_bookings WHERE session_id = ? AND guest_email LIKE ?""",
                (sid, f"{LOW}%")).fetchall()
            conn.close()
    finally:
        m.send_email = real_send
    s.check("four places on the sitting, confirmed, emails off",
            len(rows) == 4 and all(b["status"] == "confirmed" and b["do_not_email"] == 1 for b in rows),
            detail=str([(b["guest_email"], b["status"], b["do_not_email"]) for b in rows]))
    s.check("and nobody was written to", not sent, detail=str(sent))

    s.section("The stay is on the house calendar on every day it covers")
    conn = db()
    # The calendar reads the viewer's row, as a page hands it over.
    owner = conn.execute("SELECT * FROM users WHERE id = ?", (_harness.ensure_owner()["id"],)).fetchone()
    with m.app.test_request_context("/"):
        month = m.build_calendar(conn, "month", start, viewer=owner)
        after = m.build_calendar(conn, "month", end, viewer=owner)
    cells = {c["date"]: c for c in month["cells"] + after["cells"]}
    nights = [start + timedelta(days=k) for k in range((end - start).days)]
    missing = [d.isoformat() for d in nights if not _on(cells.get(d, {}), TAG)]
    s.check(f"all {len(nights)} days, {nights[0].isoformat()} to {nights[-1].isoformat()}", not missing,
            detail=f"missing {missing}")

    s.section("The château is held for those nights")
    rooms = conn.execute("SELECT * FROM rooms WHERE active = 1").fetchall()
    offers = [(r["name"],) + tuple(m.room_offer(conn, r, start, end, 2)) for r in rooms]
    s.check("no room is offered for them", rooms and not any(ok for _n, ok, _w in offers),
            detail=str([(n, ok) for n, ok, _w in offers]))
    gate = {m.is_range_available(conn, r["id"], start, end)[1] for r in rooms}
    s.check("the gate's reason is the private stay", gate == {m.PRIVATE_STAY_WHY}, detail=str(gate))
    s.check("and what a guest reads never names it",
            not any(TAG in (w or "") for _n, _ok, w in offers), detail=str({w for _n, _ok, w in offers}))
    one_night = [(r["name"],) + tuple(m.room_offer(conn, r, nights[-1], end, 2)) for r in rooms]
    s.check("down to the last night of the stay", not any(ok for _n, ok, _w in one_night))
    free_after = [r["name"] for r in rooms
                  if m.room_offer(conn, r, end, end + timedelta(days=1), 2)[0]]
    s.check("and the night it ends is free again", free_after, detail="no room offered the night it ends")
    clashes = m.stay_occasion_clashes(conn, nights[0].isoformat(), nights[-1].isoformat())
    s.check("no occasion can go on sale over it",
            any(c["blocks"] and c["why"].startswith("A private stay holds the château") for c in clashes),
            detail=str(clashes))
    s.check("an occasion inside it is stopped too",
            m.stay_occasion_blocked(m.stay_occasion_clashes(conn, nights[1].isoformat(), nights[2].isoformat())))
    conn.close()
    page = visible_text(client.get(f"/book?arrival={start.isoformat()}&departure={end.isoformat()}&party_size=2")
                        .get_data(as_text=True))
    s.check("the public /book says no room is free on those dates", "No rooms free on these dates" in page,
            detail=page[page.find("Rooms, Each"):][:200])
    s.check("and never names the stay", TITLE not in page and TAG not in page)


if __name__ == "__main__":
    print(run().report())
