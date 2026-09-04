"""Splitting one stay's bill between the people travelling.

Four friends take a room and one of them is left carrying it, chasing three
bank transfers and reconciling them by hand. The house could send one bill and
take one payment, so the splitting happened somewhere the château could not see
and it heard about it only when the money was late.

Four things carry this file.

  THE HOUSE NAMES THE AMOUNTS. A share is a fixed sum for a named person,
  payable once. There is no amount field at the other end. That is the whole
  difference between this and letting a guest type a figure at the card page,
  which is off on purpose — see room_part_payment_allowed, whose written reason
  is that letting a guest send forty euros against a stay turns every arrival
  into a conversation about what is left. This does not turn that on, and there
  is a check below that it is still off.

  THE SHARES CAN NEVER ADD UP TO MORE THAN IS OWED. Refused, not clamped:
  clamping is right at the card page, where a guest typing 500 against a 480
  balance means all of it, but here somebody is dividing a known total between
  known people and silently shrinking their third to fit is how a split stops
  adding up.

  AN EVEN SPLIT ADDS UP EXACTLY. €1000 three ways is 333.34 and two of 333.33.
  Three of 333.33 is 999.99 and leaves a stay a cent short of paid for ever.

  A PAID-UP STAY HAS NO OPEN SHARES LEFT. The one that costs real money: four
  friends each hold a link for €250, one of them settles the whole €1000 at the
  desk, and without this the other three links still work — €1750 taken and
  three refunds owed, on a house where refunds are a manual call.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZSP"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM booking_shares WHERE booking_id IN
                    (SELECT id FROM bookings WHERE guest_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, room_id, arrival, total=1000, paid=0, status="confirmed"):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 4, ?, 'unpaid', ?, ?, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"zzsp.{ref}@example.invalid".lower(), arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(), status, total, paid,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _shares(booking_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM booking_shares WHERE booking_id = ? ORDER BY id",
            (booking_id,)).fetchall()
    finally:
        conn.close()


def run():
    s = Suite("Splitting a bill")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    arrival = m.house_today() + timedelta(days=70)
    sent = []
    was_email = m.send_email
    m.send_email = lambda to, subj, body, **k: (sent.append((to, body)), True)[1]

    try:
        s.section("An even split adds up to the whole, to the cent")
        parts = m.even_split(1000, 3)
        s.check("three parts", len(parts) == 3, detail=f"{parts}")
        s.check("and they come to exactly the bill",
                abs(sum(parts) - 1000) < 0.0001,
                detail=f"{parts} sums to {sum(parts)} — three of 333.33 is "
                       "999.99 and leaves a stay a cent short of paid for ever")
        s.check("the odd cents go on the first",
                parts[0] >= parts[-1],
                detail="whoever is organising it carries the rounding, because "
                       "they are the one who can be told")
        s.check("splitting more ways than there are cents is refused",
                m.even_split(0.02, 5) == [],
                detail="five shares of nothing is five links that cannot be paid")

        s.section("Dividing a real bill")
        b = _stay("A", room_id=room["id"], arrival=arrival, total=1000)
        conn = db()
        with m.app.test_request_context("/"):
            state = m.split_state(conn, b["id"])
        owed = state["bill"]["owed"]
        conn.close()
        s.check("nothing is anybody's to start with",
                abs(state["unassigned"] - owed) < 0.01, detail=f"{state['unassigned']}")
        oc.post(f"/admin/bookings/{b['id']}/split/evenly", data={"ways": "4"},
                follow_redirects=True)
        rows = _shares(b["id"])
        s.check("four shares are made", len(rows) == 4, detail=f"{len(rows)}")
        s.check("and they add up to the balance",
                abs(sum(r["amount"] for r in rows) - owed) < 0.01,
                detail=f"{sum(r['amount'] for r in rows)} against {owed}")
        conn = db()
        with m.app.test_request_context("/"):
            state = m.split_state(conn, b["id"])
        conn.close()
        s.check("so none of it is unclaimed", state["unassigned"] < 0.01,
                detail=f"{state['unassigned']}")
        s.check("the lead guest is named on the first",
                (rows[0]["email"] or "") == b["guest_email"],
                detail="the rest are numbered until somebody says who they are")

        s.section("A share that would take the total over what is owed")
        page = oc.post(f"/admin/bookings/{b['id']}/split/add",
                       data={"name": "One more", "email": "more@example.invalid",
                             "amount": "50"}, follow_redirects=True)
        s.check("is refused", len(_shares(b["id"])) == 4,
                detail="a split that does not add up is worse than no split")
        s.check("and says why, with the figure",
                "unclaimed" in page.get_data(as_text=True),
                detail="refusing without saying how much is left means guessing")

        s.section("Nor is it quietly shrunk to fit")
        conn = db()
        with m.app.test_request_context("/"):
            made, error = m.create_booking_share(conn, b["id"], "X", "x@example.invalid", 50)
        conn.close()
        s.check("no share appears at all", made is None and error,
                detail="clamping is right at the card page and wrong here")

        s.section("Taking one back frees the money again")
        third = rows[2]
        oc.post(f"/admin/split/{third['id']}/cancel", follow_redirects=True)
        conn = db()
        with m.app.test_request_context("/"):
            state = m.split_state(conn, b["id"])
        conn.close()
        s.check("it is cancelled, not deleted",
                len(_shares(b["id"])) == 4
                and _shares(b["id"])[2]["status"] == "cancelled",
                detail="somebody was asked for this and may still put it in "
                       "the bank; a row that vanished cannot be reconciled")
        s.check("and their part is unclaimed again",
                abs(state["unassigned"] - third["amount"]) < 0.01,
                detail=f"{state['unassigned']} against {third['amount']}")

        s.section("Saying afterwards whose share is whose")
        # The order it actually happens in at the desk: split it four ways
        # first, put the names on when somebody has asked round the table.
        second = _shares(b["id"])[1]
        s.check("the even split leaves them unnamed",
                not (second["email"] or ""), detail=f"{second['email']!r}")
        oc.post(f"/admin/split/{second['id']}/edit",
                data={"name": "Marie", "email": "marie@example.invalid",
                      "note": "the small room"}, follow_redirects=True)
        named = _shares(b["id"])[1]
        s.check("a name and an address go on",
                named["name"] == "Marie"
                and named["email"] == "marie@example.invalid",
                detail=f"{named['name']!r} {named['email']!r}")
        s.check("and the amount is untouched",
                abs(named["amount"] - second["amount"]) < 0.001,
                detail="changing an amount would have to re-check the whole "
                       "split against the balance; cancel and remake instead")

        s.section("Asking somebody for their part")
        first = _shares(b["id"])[0]
        sent.clear()
        oc.post(f"/admin/split/{first['id']}/send", follow_redirects=True)
        s.check("the email goes to them", sent and sent[0][0] == first["email"],
                detail=f"{sent[:1]}")
        s.check("with the amount in it",
                sent and ("%.2f" % first["amount"]) in sent[0][1],
                detail="a request for money that does not say how much is not "
                       "a request")
        s.check("and a link that is theirs alone",
                sent and first["token"] in sent[0][1])
        s.check("the ask is recorded", bool(_shares(b["id"])[0]["sent_at"]),
                detail="so the page can say who has been asked and who has not")

        s.section("What the person holding a share sees")
        page = oc.get(f"/book/pay-share/{first['token']}").get_data(as_text=True)
        s.check("their own amount", ("%.2f" % first["amount"]) in page)
        s.check("and the stay it belongs to", b["reference_code"] in page)
        # THE POINT OF THE WHOLE DESIGN. They are asked for a named sum, not
        # asked how much they would like to send.
        s.check("and NO field to type an amount into",
                'name="amount"' not in page,
                detail="a guest choosing their own figure is a different "
                       "feature, and it is off on purpose")
        s.check("the page is never indexed", "noindex" in page,
                detail="one named person's money, reached by a link they were "
                       "sent")
        s.check("a link that is not a share is a 404",
                oc.get("/book/pay-share/notatoken").status_code == 404)

        s.section("And the setting it does not turn on")
        conn = db()
        s.check("a guest still cannot name their own amount",
                m.room_payment_setting(conn, "room_part_payment_allowed", cast=int) != 1,
                detail="letting a guest send forty euros against a stay turns "
                       "every arrival into a conversation about what is left. "
                       "This feature works the other way: the house names it")
        conn.close()

        s.section("A share bigger than what is left is not payable")
        # The stay shrinks under a live share: a night comes off, so the bill
        # drops below what somebody was asked for. Nothing cancels the share --
        # it is still open and still theirs -- but the card page must not take
        # more than the booking owes and leave the house holding a refund.
        c = _stay("SHRINK", room_id=room["id"], arrival=arrival + timedelta(days=20),
                  total=900)
        conn = db()
        with m.app.test_request_context("/"):
            whole = m.split_state(conn, c["id"])["bill"]["owed"]
            big, err = m.create_booking_share(conn, c["id"], "All of it",
                                              "all@example.invalid", whole)
        conn.close()
        s.check("the share is made for the whole balance", big is not None and not err,
                detail=f"{err}")
        page = oc.get(f"/book/pay-share/{big['token']}").get_data(as_text=True)
        s.check("and while it matches, it can be paid",
                "by card" in page, detail="the ordinary case")
        conn = db()
        conn.execute("UPDATE bookings SET departure_date = ? WHERE id = ?",
                     ((arrival + timedelta(days=21)).isoformat(), c["id"]))
        conn.commit()
        conn.close()
        page = oc.get(f"/book/pay-share/{big['token']}").get_data(as_text=True)
        s.check("once the stay shrinks, the card button goes",
                "by card" not in page,
                detail="charging them the old figure is a refund the house "
                       "has to make by hand")
        s.check("and it says what is actually outstanding",
                "more than what is now outstanding" in page,
                detail="a dead button with no explanation is a phone call")
        s.check("the share itself is untouched",
                _shares(c["id"])[0]["status"] == "open",
                detail="nobody cancelled it; it is simply not payable as it "
                       "stands, and the house can replace it")

        s.section("Somebody settles the whole stay another way")
        # THE ONE THAT COSTS REAL MONEY. Three people are holding links for
        # this bill when the lead guest pays the lot at the desk.
        live = [r for r in _shares(b["id"]) if r["status"] == "open"]
        s.check("three links are live before it", len(live) == 3,
                detail=f"{len(live)}")
        conn = db()
        with m.app.test_request_context("/"):
            m.record_booking_payment(conn, b["id"], owed, reference="ZZSP-desk")
        conn.commit()
        conn.close()
        after = _shares(b["id"])
        s.check("every open share is taken back",
                not [r for r in after if r["status"] == "open"],
                detail="four people holding links for a bill somebody has "
                       "already settled is 1750 euros taken and three refunds "
                       "owed, on a house where refunds are a manual call")
        s.check("and the reason is on the record",
                all("paid in full" in (r["closed_reason"] or "")
                    for r in after if r["status"] == "cancelled"
                    and r["id"] != third["id"]),
                detail="a share that just stopped working, with no reason, is "
                       "a phone call")
        page = oc.get(f"/book/pay-share/{first['token']}").get_data(as_text=True)
        s.check("their link says so rather than taking the money",
                "no longer open" in page or "nothing outstanding" in page,
                detail="a card page that charges them anyway is the refund "
                       "this exists to prevent")

        s.section("A cancelled stay cannot be divided")
        dead = _stay("DEAD", room_id=room["id"], arrival=arrival + timedelta(days=10),
                     total=800, status="cancelled")
        conn = db()
        with m.app.test_request_context("/"):
            made, error = m.create_booking_share(conn, dead["id"], "Y", "y@example.invalid", 100)
        conn.close()
        s.check("refused, and it says why",
                made is None and "cancelled" in (error or ""), detail=f"{error}")

        s.section("Guards")
        s.check("an unknown booking is a 404",
                oc.get("/admin/bookings/999999/split").status_code == 404)
        s.check("an employee cannot divide a bill",
                ec.get(f"/admin/bookings/{b['id']}/split").status_code in (302, 403))
        s.check("nor take a share back",
                ec.post(f"/admin/split/{first['id']}/cancel").status_code in (302, 403))
    finally:
        m.send_email = was_email
        _cleanup()
    return s
