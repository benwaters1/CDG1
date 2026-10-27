# -*- coding: utf-8 -*-
"""A cancelled stay owes nothing (owner, 26 October 2026).

After the refund-then-cancel flow went live, two cancelled stays still read as
debts on the live site:

  - Tess Hoffman, GUD-JWPAWW, refunded her EUR 64.50 in full: "CANCELLED ·
    EUR 150.50 OWED", a bill reading "Paid, after refunds EUR 0.00, Still owed
    EUR 150.50", and "Deposit EUR 215.00 — not paid yet" under a EUR 64.50
    deposit listed as paid.
  - Inna Korniienko, GUD-QKMUGV, refunded EUR 271.60 in full: "CANCELLED ·
    EUR 270.00 OWED".

booking_bill worked out `owed` as total less paid whatever the stay's status,
and every page that read it without asking first whether the stay still
stands -- the booking page, the refund desk, the guest's own portal, the guest
record, the party, the Outlook add-in, the assistant -- said the guest owed
the price of a stay that is not happening.

The rule:
  - A cancelled or declined stay owes nothing.
  - Money paid and not refunded is KEPT under the non-refundable terms. Never
    owed, never chased. Where the house said no (declined, or "the house could
    not") it is the guest's, to give back.
  - The bill of a cancelled stay reads Total, Paid, Refunded (only when there
    is one), Kept. No "Still owed", no "Deposit ... not paid yet".

And a confirmed stay goes on owing exactly as before: it is the positive
control for every list below, so a list that has simply stopped working
cannot pass as one that leaves cancelled stays out.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZCO"
EMAIL = "zzco-guest@example.invalid"
PORTAL = "zzcoportaltoken0123456789"


def _cleanup():
    conn = db()
    stays = "(SELECT id FROM bookings WHERE reference_code LIKE ?)"
    conn.execute("DELETE FROM refunds WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(f"DELETE FROM booking_payments WHERE booking_id IN {stays}", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email = ?", (EMAIL,))
    conn.commit()
    conn.close()


def _stay(ref, *, status, total, paid, refunded=0.0, deposit=None, deposit_stamped=False,
          due_in=None, cancel_reason=None, party_id=None):
    """One stay of one night, priced at `total`, with what came in and went back."""
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    today = house_today()
    arrival = today + timedelta(days=60)
    departure = arrival + timedelta(days=1)
    now = datetime.now(timezone.utc).isoformat()
    code = f"{TAG}{ref}"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, payment_status, total_price,
           amount_paid, created_at, deposit_amount, deposit_paid_at, balance_due_date,
           cancel_reason, party_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'unpaid', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (room, code, f"tok{code}".lower(), f"{TAG} Guest {ref}", EMAIL,
         arrival.isoformat(), departure.isoformat(), status, total, paid, now,
         deposit, now if deposit_stamped else None,
         (today + timedelta(days=due_in)).isoformat() if due_in is not None else None,
         cancel_reason, party_id))
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (code,)).fetchone()["id"]
    m.stamp_room_total(conn, bid, total, arrival.isoformat(), departure.isoformat())
    if paid:
        conn.execute("""INSERT INTO booking_payments (booking_id, amount, method, created_at)
                        VALUES (?, ?, 'other', ?)""", (bid, paid, now))
    if refunded:
        conn.execute(
            """INSERT INTO refunds (category, booking_id, reference_code, guest_name,
               guest_email, amount, reason, method, created_at)
               VALUES ('room', ?, ?, ?, ?, ?, 'Cancelled', 'other', ?)""",
            (bid, code, f"{TAG} Guest {ref}", EMAIL, refunded, now))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _bill(b):
    conn = db()
    bill = m.booking_bill(conn, b["id"])
    conn.close()
    return bill


def run():
    s = Suite("A cancelled stay owes nothing")
    oc, _ec, owner, _emp = clients()
    _cleanup()
    letters = []
    was = m.write_about_stay

    def _stand_in(booking, subject, body, *a, **kw):
        # Records who would have been written to, and says it did not go, so
        # nothing is stamped as sent on the copy's real bookings either.
        letters.append(booking["reference_code"])
        return False
    m.write_about_stay = _stand_in
    try:
        _checks(s, oc, owner, letters)
    finally:
        m.write_about_stay = was
        _cleanup()
    return s


def _checks(s, oc, owner, letters):
    conn = db()
    conn.execute("INSERT INTO booking_parties (name, created_at) VALUES (?, ?)",
                 (f"{TAG} party", datetime.now(timezone.utc).isoformat()))
    party_id = conn.execute("SELECT id FROM booking_parties WHERE name = ?",
                            (f"{TAG} party",)).fetchone()["id"]
    conn.execute("INSERT INTO guests (name, email, portal_token, created_at) VALUES (?, ?, ?, ?)",
                 (f"{TAG} Guest", EMAIL, PORTAL, datetime.now(timezone.utc).isoformat()))
    gid = conn.execute("SELECT id FROM guests WHERE email = ?", (EMAIL,)).fetchone()["id"]
    conn.commit()
    conn.close()

    # As Tess's and Inna's stays stood on the live site, balance date passed.
    tess = _stay("TESS", status="cancelled", total=150.50, paid=64.50, refunded=64.50,
                 deposit=215.0, due_in=-1, party_id=party_id)
    inna = _stay("INNA", status="cancelled", total=270.00, paid=271.60, refunded=271.60,
                 due_in=-1)
    part = _stay("PART", status="cancelled", total=400.0, paid=400.0, refunded=150.0,
                 due_in=-1)
    none = _stay("NONE", status="cancelled", total=300.0, paid=120.0, due_in=-1)
    unpaid = _stay("UNPAID", status="cancelled", total=250.0, paid=0.0, due_in=-1)
    declined = _stay("DECL", status="declined", total=200.0, paid=50.0, due_in=-1)
    house = _stay("HOUSE", status="cancelled", total=180.0, paid=80.0, due_in=-1,
                  cancel_reason="house_could_not")
    # The positive control: a confirmed stay, owing 200 and a day late.
    conf = _stay("CONF", status="confirmed", total=300.0, paid=100.0, due_in=-1,
                 party_id=party_id)
    called_off = [tess, inna, part, none, unpaid, declined, house]
    off_refs = {b["reference_code"] for b in called_off}

    # ------------------------------------------------------------------
    s.section("The bill: a cancelled stay owes nothing, and what it holds is kept")
    for b, received, refunded, kept, back in (
            (tess, 64.50, 64.50, 0.0, 0.0),
            (inna, 271.60, 271.60, 0.0, 0.0),
            (part, 400.0, 150.0, 250.0, 0.0),
            (none, 120.0, 0.0, 120.0, 0.0),
            (unpaid, 0.0, 0.0, 0.0, 0.0),
            (declined, 50.0, 0.0, 0.0, 50.0),
            (house, 80.0, 0.0, 0.0, 80.0)):
        bill = _bill(b)
        ref = b["reference_code"]
        s.check(f"{ref}: owes nothing", bill["owed"] == 0.0, detail=f"owed {bill['owed']}")
        s.check(f"{ref}: never overdue", not bill["balance_overdue"])
        s.check(f"{ref}: not 'in credit' either", bill["overpaid"] == 0.0,
                detail=f"overpaid {bill['overpaid']}")
        s.check(f"{ref}: paid {received}, refunded {refunded}",
                abs(bill["received"] - received) < 0.005
                and abs(bill["refunded"] - refunded) < 0.005,
                detail=f"received {bill['received']} refunded {bill['refunded']}")
        s.check(f"{ref}: kept {kept}, to give back {back}",
                abs(bill["kept"] - kept) < 0.005 and abs(bill["to_give_back"] - back) < 0.005,
                detail=f"kept {bill['kept']} back {bill['to_give_back']}")
        s.check(f"{ref}: paid less refunded is what it holds",
                abs(bill["received"] - bill["refunded"]
                    - bill["kept"] - bill["to_give_back"]) < 0.005)

    bill = _bill(conf)
    s.check("the confirmed stay still owes its 200", abs(bill["owed"] - 200.0) < 0.005,
            detail=f"owed {bill['owed']}")
    s.check("and is still overdue", bill["balance_overdue"])
    s.check("and keeps nothing: it stands", bill["kept"] == 0.0 and not bill["called_off"])
    s.check("and its rows add up: total - paid + refunded = balance due",
            abs(bill["total"] - bill["received"] + bill["refunded"] - bill["owed"]) < 0.005)

    # ------------------------------------------------------------------
    s.section("Tess's booking page: no owed, no deposit not paid, Kept instead")
    page = oc.get(f"/admin/bookings/{tess['id']}").get_data(as_text=True)
    text = _harness.visible_text(page)
    s.check("the page opens", "TESS" in page)
    s.check("the header does not say owed", not re.search(r"€[\d,.]+ (owed|balance due)", text),
            detail=re.findall(r"€[\d,.]+ (?:owed|balance due)", text)[:3])
    s.check("no 'Still owed' anywhere", "Still owed" not in text)
    s.check("no 'Balance due' on a cancelled stay", "Balance due" not in text)
    s.check("no 'Paid, after refunds'", "Paid, after refunds" not in text)
    s.check("no 'Deposit ... not paid yet'", "not paid yet" not in text and "Deposit €215" not in text)
    foot = page[page.find("<tfoot>"):page.find("</tfoot>")]
    heads = re.findall(r'<th scope="row">([^<]+)</th>', foot)
    s.check("the bill reads Total, Paid, Refunded, Kept", heads == ["Total", "Paid", "Refunded", "Kept"],
            detail=f"{heads}")
    s.check("with the figures 150.50, 64.50, 64.50, 0.00",
            re.findall(r"€([\d,.]+)", foot) == ["150.50", "64.50", "64.50", "0.00"],
            detail=f"{re.findall(r'€([0-9,.]+)', foot)}")
    s.check("and says plainly that nothing is owed", "Cancelled: nothing is owed" in text)
    s.check("no form to record a payment against it", 'id="take-payment"' not in page)

    page = oc.get(f"/admin/bookings/{none['id']}").get_data(as_text=True)
    foot = page[page.find("<tfoot>"):page.find("</tfoot>")]
    heads = re.findall(r'<th scope="row">([^<]+)</th>', foot)
    s.check("nothing refunded: no Refunded line at all", heads == ["Total", "Paid", "Kept"],
            detail=f"{heads}")
    s.check("the header says what was kept", "€120.00 kept" in _harness.visible_text(page))

    page = oc.get(f"/admin/bookings/{declined['id']}").get_data(as_text=True)
    foot = page[page.find("<tfoot>"):page.find("</tfoot>")]
    s.check("declined with money held: To give back, not Kept",
            "To give back" in foot and "Kept" not in foot, detail=foot[-300:])

    s.section("A confirmed stay's page owes as before, in the new words")
    page = oc.get(f"/admin/bookings/{conf['id']}").get_data(as_text=True)
    foot = page[page.find("<tfoot>"):page.find("</tfoot>")]
    heads = re.findall(r'<th scope="row">([^<]+)</th>', foot)
    s.check("Total, Paid, Balance due (no refund, so no Refunded line)",
            heads == ["Total", "Paid", "Balance due"], detail=f"{heads}")
    s.check("balance due 200.00", "€200.00" in foot, detail=foot[-200:])
    s.check("the header says so", "€200.00 balance due" in _harness.visible_text(page))
    s.check("and the payment form is there", 'id="take-payment"' in page)

    # ------------------------------------------------------------------
    s.section("The deposit line agrees with the money")
    dep = _stay("DEP", status="confirmed", total=400.0, paid=64.50, deposit=215.0, due_in=30)
    text = _harness.visible_text(oc.get(f"/admin/bookings/{dep['id']}").get_data(as_text=True))
    s.check("EUR 64.50 in against a EUR 215 deposit: says what is paid, not 'not paid yet'",
            "Deposit €215.00 — €64.50 paid" in text and "not paid yet" not in text,
            detail=re.findall(r"Deposit[^.]*\.", text)[:2])
    dep2 = _stay("DEP2", status="confirmed", total=400.0, paid=215.0, deposit=215.0, due_in=30)
    text = _harness.visible_text(oc.get(f"/admin/bookings/{dep2['id']}").get_data(as_text=True))
    s.check("the deposit fully in, never stamped: paid", "Deposit €215.00 — paid" in text,
            detail=re.findall(r"Deposit[^.]*\.", text)[:2])
    dep3 = _stay("DEP3", status="confirmed", total=400.0, paid=0.0, deposit=215.0, due_in=30)
    text = _harness.visible_text(oc.get(f"/admin/bookings/{dep3['id']}").get_data(as_text=True))
    s.check("nothing in: still 'not paid yet'", "Deposit €215.00 — not paid yet" in text,
            detail=re.findall(r"Deposit[^.]*\.", text)[:2])

    # ------------------------------------------------------------------
    s.section("Absent from every list of what is owed")
    conn = db()
    rows = m.outstanding_balances(conn)
    refs = {r["reference"] for r in rows}
    s.check("What we're owed lists the confirmed stay", conf["reference_code"] in refs)
    s.check("and no cancelled or declined one", not (refs & off_refs), detail=f"{refs & off_refs}")
    ahead = m.expected_money_in(conn, house_today())
    refs = {x["ref"] for x in ahead}
    s.check("money expected in: the confirmed stay", conf["reference_code"] in refs)
    s.check("and no cancelled one", not (refs & off_refs), detail=f"{refs & off_refs}")
    with m.app.test_request_context("/"):
        forecast = m.money_ahead(conn, days=90)
    refs = {x.get("ref") for x in forecast["incoming"]}
    s.check("money ahead: the confirmed stay", conf["reference_code"] in refs)
    s.check("and no cancelled one", not (refs & off_refs), detail=f"{refs & off_refs}")
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, house_today())
    late = [r for r in m.outstanding_balances(conn)
            if r["kind"] in ("stay", "event") and r["state"] in ("gone", "overdue")]
    line = next((w for w in warnings if "and late" in w["title"]), None)
    s.check("the owner home's late line counts the confirmed stay, and only standing ones",
            line is not None and f"by {len(late)} guest" in line["title"]
            and conf["reference_code"] in {r["reference"] for r in late}
            and not ({r["reference"] for r in late} & off_refs),
            detail=line["title"] if line else "no late line")
    party = m.party_detail(conn, party_id)
    s.check("the party owes the confirmed stay's 200 and not Tess's 150.50",
            abs(party["owed"] - 200.0) < 0.005, detail=f"owed {party['owed']}")
    s.check("and its three figures add up: whole party - received = owed",
            abs(party["total"] - party["paid"] - party["owed"]) < 0.005,
            detail=f"{party['total']} - {party['paid']} != {party['owed']}")
    record = m.guest_record(conn, gid)
    s.check("the guest record: each cancelled stay owes nothing",
            all(record["bills"][b["id"]]["owed"] == 0.0 for b in called_off))
    with m.app.test_request_context("/"):
        dossier = m.guest_dossier(conn, gid, money=True)
    owed_by_ref = {i["ref"]: i["owed"] for i in dossier["items"] if i["kind"] == "Stay"}
    s.check("the Outlook add-in: the confirmed stay owes 200",
            abs((owed_by_ref.get(conf["reference_code"]) or 0) - 200.0) < 0.005,
            detail=f"{owed_by_ref}")
    s.check("and no cancelled stay owes anything",
            all(not owed_by_ref.get(r) for r in off_refs),
            detail=f"{ {r: owed_by_ref.get(r) for r in off_refs} }")
    # One reference at a time: the tool returns eight bookings at most.
    said = {r: m.assistant_read_tool(conn, owner, "find_booking", {"query": r})
            for r in off_refs | {conf["reference_code"]}}
    s.check("the assistant: the confirmed stay owes",
            "owes €200.00" in said[conf["reference_code"]], detail=said[conf["reference_code"]])
    s.check("and no cancelled one does",
            not any("owes" in said[r] for r in off_refs),
            detail=f"{[said[r] for r in off_refs if 'owes' in said[r]]}")
    for b in (tess, part, none):
        view = m.booking_statement_view(conn, "room", b["id"])
        s.check(f"{b['reference_code']}: its statement asks for nothing",
                view and view["owed"] == 0.0, detail=f"{view and view['owed']}")
    conn.close()

    page = oc.get("/admin/bookings", query_string={"q": TAG}).get_data(as_text=True)
    s.check("the bookings list: the confirmed stay owes 200",
            re.search(r'data-label="Owed"[^>]*>\s*<strong class="owed-flag">€200\.00<', page))
    page = oc.get("/admin/bookings", query_string={"when": "Called off", "q": TAG}
                  ).get_data(as_text=True)
    s.check("the cancelled ones are on the list", tess["reference_code"] in page)
    s.check("and none of them shows as owing",
            not re.search(r'data-label="Owed"[^>]*>\s*<strong class="owed-flag">', page))
    s.check("a stay cancelled with money kept says so", "€250.00 kept" in page)
    page = oc.get("/admin/bookings", query_string={"money": "Owes money", "when": "Called off",
                                                   "q": TAG}).get_data(as_text=True)
    s.check("the 'Owes money' chip finds no cancelled stay",
            not any(r in page for r in off_refs))

    page = oc.get(f"/admin/parties/{party_id}").get_data(as_text=True)
    owed_tile = re.search(r"Still owed</span>\s*<span class=\"stat-tile-value\">€([\d,.]+)<", page)
    s.check("the party page's Still owed is the confirmed stay's 200 alone",
            owed_tile and owed_tile.group(1) == "200.00",
            detail=owed_tile.group(1) if owed_tile else "no tile")
    tess_card = page[page.find(tess["reference_code"]):]
    tess_card = tess_card[:tess_card.find("</table>")]
    s.check("Tess's card: Paid, Refunded, Kept and no Still owed",
            "Still owed" not in tess_card and "Kept" in tess_card and "Refunded" in tess_card,
            detail=_harness.visible_text(tess_card)[-200:])

    # The portal lists only stays that stand, so it was never wrong; this
    # keeps it asking for exactly what each of those owes.
    page = oc.get(f"/my/{PORTAL}").get_data(as_text=True)
    still = sorted(re.findall(r"Still to pay: <strong>€([\d,.]+)</strong>", page))
    standing = sorted(f"{_bill(b)['owed']:,.2f}" for b in (conf, dep, dep2, dep3))
    s.check("the guest's own portal asks for each standing stay's balance and nothing more",
            still == standing, detail=f"{still} against {standing}")

    page = oc.get(f"/admin/refunds/room/{tess['id']}").get_data(as_text=True)
    text = _harness.visible_text(page)
    s.check("the refund desk: Kept, cancelled, nothing owed",
            "Kept: cancelled, nothing is owed" in text and "Still owed" not in text,
            detail=re.findall(r"(?:Still owed|Kept)[^\n]{0,40}", text)[:3])

    # ------------------------------------------------------------------
    s.section("Never chased")
    del letters[:]
    r = oc.post(f"/management/outstanding/{tess['id']}/chase", follow_redirects=True)
    s.check("asking Tess for the balance by hand is refused",
            any("nothing outstanding" in f for f in flashes(r)), r)
    s.check("and nothing is written to her", tess["reference_code"] not in letters,
            detail=f"{letters}")
    r = oc.post(f"/management/outstanding/{conf['id']}/chase", follow_redirects=True)
    s.check("the confirmed stay can still be chased", conf["reference_code"] in letters,
            r, detail=f"{letters}")
    del letters[:]
    conn = db()
    with m.app.test_request_context("/"):
        said = m.run_room_balance_reminder_job(conn, 0)
    conn.close()
    s.check("the automatic reminder reaches the confirmed stay",
            conf["reference_code"] in letters, detail=f"{said}; {letters[:6]}")
    s.check("and no cancelled or declined one", not (set(letters) & off_refs),
            detail=f"{set(letters) & off_refs}")
