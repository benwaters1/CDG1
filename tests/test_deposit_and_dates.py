"""The 10% deposit, balances that follow their session, and parties of stays.

Three things, found or asked for after the workshop register went in:

  - The owner: "workshop deposit is 10%". It was 30, a bare number in the
    seed, the column's default, the fallback and the form. It is one named
    figure now, every atelier still on the old 30 moved to it once, and a
    registration whose deposit is not yet paid asks for the new one.
  - A session given new dates kept every registration's balance falling due
    thirty days before the OLD date -- so the notice that it had fallen due,
    the reminder and the list of balances to collect all ran a month off.
  - "Tie together as a party" is for stays that are usually confirmed by the
    time a party is obvious, and only pending stays had a tick box.
"""
import contextlib
import io
import re
import sys
from datetime import datetime, timedelta, timezone

from flask import has_request_context

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZDD"


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("UPDATE bookings SET party_id = NULL WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _workshop(name, percent, price=2000.0):
    conn = db()
    conn.execute("INSERT INTO workshops (title, description, price_per_person, default_capacity, "
                 "active, sort_order, created_at, deposit_percent) VALUES (?, '', ?, 10, 1, 90, ?, ?)",
                 (f"{TAG} {name}", price, _harness.datetime_now(), percent))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return wid


def _session(wid, label, starts_in):
    conn = db()
    start = house_today() + timedelta(days=starts_in)
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, "
                 "created_at) VALUES (?, ?, ?, 10, ?, ?)",
                 (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} {label}",
                  _harness.datetime_now()))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?", (f"{TAG} {label}",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return sid


def _reg(ref, sid, *, total=2000.0, deposit=600.0, paid=False, status="confirmed"):
    conn = db()
    now = _harness.datetime_now()
    start = conn.execute("SELECT start_date FROM workshop_sessions WHERE id = ?", (sid,)).fetchone()["start_date"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           deposit_paid_at, balance_due_date, balance_due_noticed_at)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} {ref}", f"{TAG.lower()}{ref.lower()}@example.invalid", status, f"{TAG}{ref}",
         f"tok{TAG}{ref}", now, total, deposit, total - deposit, now if paid else None,
         (m.parse_date(start) - timedelta(days=30)).isoformat(), now))
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?", (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", deposit, method="stripe")
    conn.commit()
    conn.close()
    return bid


def _deploy():
    """Run init_db as wsgi.py does, and hand back what it printed -- which is
    the deploy log -- while still printing it."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        m.init_db()
    sys.stdout.write(out.getvalue())
    return out.getvalue()


def _row(table, bid):
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Deposit, dates and parties")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    s.section("The workshop deposit is 10%")
    s.check("the house's figure is 10", m.WORKSHOP_DEPOSIT_PERCENT == 10)
    conn = db()
    left = conn.execute("SELECT title FROM workshops WHERE deposit_percent = 30 "
                        "AND title NOT LIKE ?", (TAG + "%",)).fetchall()
    conn.close()
    s.check("no atelier is left on the old 30", not left, detail=f"{[r['title'] for r in left]}")
    form = oc.get("/admin/workshops/new").get_data(as_text=True)
    s.check("a new atelier's form offers 10", 'name="deposit_percent" min="0" max="100" value="10"' in form)
    oc.post("/admin/workshops/new", data={"title": f"{TAG} Blank", "description": "x",
                                           "price_per_person": "1000", "default_capacity": "8",
                                           "deposit_percent": ""}, follow_redirects=True)
    conn = db()
    blank = conn.execute("SELECT deposit_percent FROM workshops WHERE title = ?", (f"{TAG} Blank",)).fetchone()
    conn.close()
    s.check("and one saved with the box left empty takes 10",
            blank is not None and blank["deposit_percent"] == 10, detail=f"{dict(blank) if blank else None}")

    s.section("Once: every atelier on the old 30 moves, and an unpaid deposit asks for 10%")
    old = _workshop("Old", 30)
    kept = _workshop("Kept", 25)
    sid = _session(old, "OldSession", 120)
    unpaid = _reg("Unpaid", sid, deposit=600.0, paid=False)
    paid = _reg("Paid", sid, deposit=600.0, paid=True)
    conn = db()
    conn.execute("DELETE FROM app_settings WHERE key = 'workshop_deposit_percent_done'")
    conn.commit()
    conn.close()
    # The way a deploy runs it: init_db, from wsgi.py, before there is any
    # request. Called directly, the one-off could be tested perfectly and never
    # be called at all -- and a fresh database is seeded at 10, so nothing else
    # here would notice. And nothing it calls may lean on a request: one that
    # did would stop the app booting on the first deploy with a deposit to move.
    try:
        log, failed = _deploy(), None
    except Exception as exc:        # named below, not a crash of the suite
        log, failed = "", exc
    s.check("a deploy runs it, with no request to lean on",
            failed is None and not has_request_context(), detail=f"{failed!r}")
    s.check("the atelier on 30 is on 10 now", _row("workshops", old)["deposit_percent"] == 10)
    s.check("one the owner set to 25 is left alone", _row("workshops", kept)["deposit_percent"] == 25)
    s.check("the unpaid deposit is 10% of the place",
            abs((_row("workshop_bookings", unpaid)["deposit_amount"] or 0) - 200.0) < 0.01,
            detail=f"{_row('workshop_bookings', unpaid)['deposit_amount']}")
    s.check("and the balance is the rest", abs((_row("workshop_bookings", unpaid)["balance_amount"] or 0) - 1800.0) < 0.01)
    s.check("a deposit already paid keeps the terms it was made on",
            abs((_row("workshop_bookings", paid)["deposit_amount"] or 0) - 600.0) < 0.01)
    said = re.search(r"Workshop deposit set to 10%: ([1-9]\d*) atelier\(s\), ([1-9]\d*) unpaid", log)
    s.check("and the deploy log says what it did", said is not None, detail=log.strip()[-160:])
    conn = db()
    conn.execute("UPDATE workshops SET deposit_percent = 30 WHERE id = ?", (old,))
    conn.commit()
    conn.close()
    again = _deploy()
    s.check("and the next deploy leaves it be: an atelier put back to 30 on purpose stays at 30",
            _row("workshops", old)["deposit_percent"] == 30 and "Workshop deposit set to" not in again,
            detail=again.strip()[-160:])
    page = oc.get(f"/workshops/register/{_session(kept, 'KeptSession', 100)}").get_data(as_text=True)
    s.check("the page a guest books on states the atelier's own figure", "A 25% deposit is due" in page)

    s.section("A session's new dates move its balances")
    moves = _workshop("Moves", 10)
    msid = _session(moves, "Moving", 60)
    stay = _reg("Stay", msid, deposit=200.0, paid=True)
    gone = _reg("Gone", msid, status="cancelled")
    before_gone = _row("workshop_bookings", gone)["balance_due_date"]
    new_start = house_today() + timedelta(days=90)
    r = oc.post(f"/admin/workshops/sessions/{msid}/edit",
                data={"capacity": "10", "start_date": new_start.isoformat(),
                      "end_date": (new_start + timedelta(days=4)).isoformat(), "notes": f"{TAG} Moving"},
                follow_redirects=True)
    after = _row("workshop_bookings", stay)
    s.check("the balance falls due thirty days before the NEW date",
            after["balance_due_date"] == (new_start - timedelta(days=30)).isoformat(),
            detail=f"{after['balance_due_date']}")
    s.check("so the owner is told again when it falls due", not after["balance_due_noticed_at"])
    s.check("the deposit paid stays paid", after["deposit_paid_at"] and abs(after["deposit_amount"] - 200.0) < 0.01)
    s.check("a cancelled registration is left as it was",
            _row("workshop_bookings", gone)["balance_due_date"] == before_gone)
    s.check("and the page says the guests have not been told", any("not been told" in f for f in flashes(r)),
            detail=f"{flashes(r)}")
    due_now = after["balance_due_date"]
    oc.post(f"/admin/workshops/sessions/{msid}/edit",
            data={"capacity": "12", "start_date": new_start.isoformat(),
                  "end_date": (new_start + timedelta(days=4)).isoformat(), "notes": f"{TAG} Moving"})
    s.check("a change of capacity alone moves nothing",
            _row("workshop_bookings", stay)["balance_due_date"] == due_now)

    s.section("A party of confirmed stays can be tied together")
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    for ref, offset in (("A", 50), ("B", 50)):
        arrival = house_today() + timedelta(days=offset)
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 400, ?)""",
            (room, f"{TAG}-{ref}", f"tok{TAG}p{ref}".lower(), f"{TAG} Family {ref}",
             f"{TAG.lower()}.{ref.lower()}@example.invalid", arrival.isoformat(),
             (arrival + timedelta(days=2)).isoformat(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    ids = [r["id"] for r in conn.execute("SELECT id FROM bookings WHERE reference_code IN (?, ?) ORDER BY id",
                                         (f"{TAG}-A", f"{TAG}-B")).fetchall()]
    conn.close()
    page = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("a confirmed stay has a tick box",
            all(f'value="{i}" class="bulk-check"' in page for i in ids),
            detail="only pending stays could be ticked, and a party is usually confirmed")
    oc.post("/admin/parties/new", data={"booking_ids": [str(i) for i in ids], "name": f"{TAG} Party"},
            follow_redirects=True)
    tied = {_row("bookings", i)["party_id"] for i in ids}
    s.check("and ticking two ties them into one party", len(tied) == 1 and None not in tied, detail=f"{tied}")
    r = oc.post("/admin/bookings/bulk-confirm", data={"booking_ids": [str(ids[0])]}, follow_redirects=True)
    s.check("confirming one already confirmed says so, not that somebody else did",
            any("already confirmed" in f for f in flashes(r)), detail=f"{flashes(r)}")

    _cleanup()
    return s
