"""What the house is owed, including from the guests who have already left.

Money Ahead answers a good question well: what is expected over the next N
days, dated on arrival. It has a window, and that window is the problem. A
guest who checked out last month still owing four hundred euros has an arrival
date in the past, so they drop out of it and off every other screen in the
house. The automatic reminder cannot reach them either — its query requires
`COALESCE(balance_due_date, arrival_date) >= today`, so somebody who has gone
is past every date it checks.

THE POINT OF THIS PAGE is therefore the guest nobody can see: the one who left
owing. Everything else here is arithmetic that already existed.

Two things are checked that are easy to get wrong in a debtors list:

  A REFUND REOPENS THE BILL, and that is correct, not a bug. booking_bill
  takes a refund off what was RECEIVED rather than off the total, because the
  stay still cost what it cost — so money given back is money owed again. What
  stops that becoming a nonsense is the next line: you refund a guest because
  you cancelled their stay, and a cancelled stay is excluded. A refund on a
  stay that still stands really is owed, and belongs here.

  A CANCELLED STAY IS NOT A DEBT. The bill still exists for the record, and
  nobody is being asked for it.

And the wording is shared with the automatic reminder rather than written
twice, because the two differ in exactly one respect and it is the one that
matters: "we are looking forward to seeing you", sent to somebody who checked
out a fortnight ago, reads as a machine that has not noticed.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZOWED"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM refunds WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, arrive_in, nights=2, status="confirmed", paid=0.0,
          email="zzowed@example.invalid", due=None):
    conn = db()
    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=arrive_in)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, balance_due_date, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 500, ?, 0, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", email,
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(), status,
         paid, due, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _rows():
    conn = db()
    try:
        return {r["booking"]["reference_code"]: r
                for r in m.outstanding_balances(conn)
                if (r["booking"]["reference_code"] or "").startswith(TAG)}
    finally:
        conn.close()


def _settle(booking_id):
    conn = db()
    total = m.booking_bill(conn, booking_id)["total"]
    conn.execute("UPDATE bookings SET amount_paid = ? WHERE id = ?", (total, booking_id))
    conn.commit()
    conn.close()


def run():
    s = Suite("What we are owed")
    _cleanup()
    oc, ec, owner, emp = clients()

    gone = _stay("GONE", arrive_in=-20)
    soon = _stay("SOON", arrive_in=30)
    late = _stay("LATE", arrive_in=10,
                 due=(house_today() - timedelta(days=5)).isoformat())
    settled = _stay("PAID", arrive_in=-10)
    _settle(settled["id"])
    cancelled = _stay("CANX", arrive_in=-15, status="cancelled")

    s.section("The guest nobody else can see")
    rows = _rows()
    s.check("a guest who left owing is on the list", f"{TAG}-GONE" in rows,
            detail=f"{sorted(rows)} — this is the one Money Ahead cannot show "
                   "and the reminder job cannot reach")
    s.check("marked as gone rather than merely due",
            rows.get(f"{TAG}-GONE", {}).get("state") == "gone",
            detail=f"{rows.get(f'{TAG}-GONE', {}).get('state')}")
    s.check("with how long ago they left",
            rows.get(f"{TAG}-GONE", {}).get("days_late", 0) >= 17,
            detail=f"{rows.get(f'{TAG}-GONE', {}).get('days_late')} days")

    s.section("And the ones who have not gone yet")
    s.check("a balance due date in the past is overdue",
            rows.get(f"{TAG}-LATE", {}).get("state") == "overdue",
            detail=f"{rows.get(f'{TAG}-LATE', {}).get('state')}")
    s.check("a stay still to come is just due",
            rows.get(f"{TAG}-SOON", {}).get("state") == "due",
            detail=f"{rows.get(f'{TAG}-SOON', {}).get('state')}")
    s.check("the ones who have gone sort first",
            [r["booking"]["reference_code"]
             for r in m.outstanding_balances(db()) if
             (r["booking"]["reference_code"] or "").startswith(TAG)][0] == f"{TAG}-GONE",
            detail="the most urgent is not at the top")

    s.section("What is not a debt")
    s.check("a settled stay is off the list", f"{TAG}-PAID" not in rows,
            detail="somebody who has paid would be chased for it")
    s.check("and a cancelled one too", f"{TAG}-CANX" not in rows,
            detail="the bill exists for the record; nobody is being asked for it")

    s.section("A refund puts the money back on the bill")
    # Refunding a stay that still stands means the guest has the money and
    # the house has the nights: owed again, and this must agree with
    # booking_bill rather than inventing a second answer. The case where that
    # would be wrong — refunded because cancelled — is excluded by status.
    refunded = _stay("REFD", arrive_in=-30)
    _settle(refunded["id"])
    conn = db()
    total = m.booking_bill(conn, refunded["id"])["total"]
    conn.execute(
        """INSERT INTO refunds (booking_id, category, amount, reason, created_at)
           VALUES (?, 'room', ?, 'ZZ test', ?)""",
        (refunded["id"], total, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    after = _rows()
    s.check("a stay refunded in full shows as owed, because it now is",
            f"{TAG}-REFD" in after,
            detail="a refund removes money received, so the bill is open again "
                   "— that is what booking_bill says and this must not disagree")
    if f"{TAG}-REFD" in after:
        s.check("for the amount, not for double it",
                abs(after[f"{TAG}-REFD"]["owed"] - total) < 0.01,
                detail=f"{after[f'{TAG}-REFD']['owed']} vs {total}")

    s.section("The page itself")
    r = oc.get("/management/outstanding")
    s.check("it opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    html = r.get_data(as_text=True)
    s.check("the guest who left owing is on it", f"{TAG} GONE" in html)
    s.check("and the settled one is not", f"{TAG} PAID" not in html)
    s.check("it separates what is owed by people who have gone",
            "have left" in html.lower(),
            detail="one total does not tell you which of it needs a decision")
    s.check("and it has the search and chips every list here has",
            'name="q"' in html and "chip" in html.lower(),
            detail="another one-off list nobody can filter")

    s.section("Asking one guest for it")
    sent = []
    real_send = m.send_email

    def capture(to, subject, body, *a, **kw):
        sent.append((to, subject, body))
        return True

    try:
        m.send_email = capture
        r = oc.post(f"/management/outstanding/{gone['id']}/chase", follow_redirects=True)
        s.check("it sends", len(sent) == 1, detail=f"{len(sent)}")
        if sent:
            to, subject, body = sent[0]
            s.check("to the guest", to == "zzowed@example.invalid", detail=to)
            # The whole reason the wording is shared rather than duplicated.
            s.check("and does NOT say it is looking forward to seeing them",
                    "looking forward" not in body.lower(),
                    detail=f"{body[:140]!r} — they left three weeks ago")
            s.check("it says what is owed", "owed" in body.lower() or "outstanding" in body.lower(),
                    detail=f"{body[:140]!r}")
            s.check("and gives them somewhere to pay it",
                    gone["manage_token"] in body, detail=f"{body[:200]!r}")

        s.section("But a guest still to come gets the other wording")
        sent.clear()
        oc.post(f"/management/outstanding/{soon['id']}/chase", follow_redirects=True)
        s.check("that one does look forward to seeing them",
                bool(sent) and "looking forward" in sent[0][2].lower(),
                detail=f"{sent[0][2][:140]!r}" if sent else "nothing sent")

        s.section("Nothing to chase, and nobody to chase it from")
        sent.clear()
        r = oc.post(f"/management/outstanding/{settled['id']}/chase", follow_redirects=True)
        s.check("a settled stay sends nothing", not sent, detail=f"{sent[:1]}")
        s.check("and says so", any("nothing outstanding" in f.lower() for f in flashes(r)),
                detail=f"{flashes(r)[:1]}")
        no_email = _stay("NOML", arrive_in=-5, email="")
        r = oc.post(f"/management/outstanding/{no_email['id']}/chase", follow_redirects=True)
        s.check("a booking with no address sends nothing", not sent, detail=f"{sent[:1]}")
        s.check("and says that too",
                any("email" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

        s.section("Guards")
        sent.clear()
        s.check("an employee cannot open the page",
                ec.get("/management/outstanding").status_code in (302, 403))
        code = ec.post(f"/management/outstanding/{gone['id']}/chase").status_code
        s.check("nor chase anybody", code in (302, 403), detail=f"HTTP {code}")
        s.check("and none went", not sent, detail=f"{sent[:1]}")
        s.check("a booking that does not exist is a 404",
                oc.post("/management/outstanding/999999/chase").status_code == 404)
    finally:
        m.send_email = real_send
    s.check("the real sender is restored", m.send_email is real_send)

    _cleanup()
    return s
