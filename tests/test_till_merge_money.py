"""Merging two tabs on the till never leaves money behind.

A merge moves every line off one tab onto another and voids the tab it
emptied. Payments never moved with the lines, and the only check was that the
tab was open -- which a part-paid tab still is. So a table that had put EUR 5 in
cash towards EUR 20 could be merged into the next table, and three things went
wrong at once, none of them loudly:

  - the merged bill asked for the full amount again, because a bill counts the
    payments on its own tab and the EUR 5 was on the void one;
  - the cash-up lost the EUR 5, because it reads settled tabs only, so the
    drawer came out EUR 5 over with nothing to say why;
  - a receipt number (issued at the first payment) sat on a void tab with no
    lines on it.

Moving the payments with the lines was the other way to fix it, and the wrong
one for a French till: each payment is journalled against the tab it was taken
on, under that tab's receipt number, and the journal cannot be rewritten to say
otherwise. Nothing else in the app ever changes a stored payment. So the tab
holding money is never the one emptied -- the other table is brought onto it
instead, and both the route and the page work that way round.

Two payments that are still on their way get the same thought. A charge sitting
on the card reader stops a merge either way: completed against a void tab, the
reader's status check says "settled" and records nothing, so the money would be
taken at Stripe and appear nowhere. A QR link can be paid an hour later, long
after nobody remembers showing it, so it follows the merge to the bill that is
still live.
"""
from datetime import timedelta

from _harness import Suite, clients, db, flashes, forms_on
import _harness

m = _harness.m
TAG = "ZMRGM"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM pos_order_lines WHERE order_id IN
                    (SELECT id FROM pos_orders WHERE table_label LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM pos_payments WHERE order_id IN
                    (SELECT id FROM pos_orders WHERE table_label LIKE ?)""", (TAG + "%",))
    # Merged tabs point at each other; unhook them before either goes.
    conn.execute("UPDATE pos_orders SET merged_into_order_id = NULL WHERE table_label LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE table_label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


_oc = []


def _open(label, covers=2):
    _oc[0].post("/pos/open", data={"table_label": TAG + label, "covers": str(covers)},
                follow_redirects=True)
    return _tab_id(label)


def _tab_id(label):
    conn = db()
    try:
        return conn.execute("SELECT id FROM pos_orders WHERE table_label = ?",
                            (TAG + label,)).fetchone()["id"]
    finally:
        conn.close()


def _line(order_id, name, price):
    _oc[0].post(f"/pos/{order_id}/add",
                data={"name": TAG + " " + name, "unit_price": str(price), "quantity": "1"},
                follow_redirects=True)


def _pay_cash(order_id, amount):
    return _oc[0].post(f"/pos/{order_id}/pay", data={"method": "cash", "amount": str(amount)},
                       follow_redirects=True)


def _tab(order_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM pos_orders WHERE id = ?", (order_id,)).fetchone()
    finally:
        conn.close()


def _bill(order_id):
    conn = db()
    try:
        return m.pos_bill(conn, order_id)
    finally:
        conn.close()


def _payments_on(order_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM pos_payments WHERE order_id = ? ORDER BY id",
                            (order_id,)).fetchall()
    finally:
        conn.close()


def _merged_entries(order_id):
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM pos_journal WHERE event_type = 'tab_merged' AND order_id = ?",
            (order_id,)).fetchall()
    finally:
        conn.close()


def _merge_form(html, field):
    """The merge form on a till page that posts `field`, and its options."""
    for f in forms_on(html):
        for x in f["fields"]:
            if x["name"] == field:
                return f, [o for o in x["options"] if o]
    return None, []


def run():
    s = Suite("Merging tabs never leaves money behind")
    _cleanup()
    oc, ec, owner, emp = clients()
    _oc[:] = [oc]
    conn = db()
    chain_was_sound = m.pos_journal_verify(conn) is None
    gaps_before = m.receipt_sequence_gaps(conn)
    conn.close()

    # ------------------------------------------------------------------
    s.section("A tab that has taken money is not emptied into another")
    d = _open("D")
    _line(d, "Menu du jour", 20)
    _pay_cash(d, 5)
    e = _open("E")
    _line(e, "Menu du jour", 20)
    receipt_d = _tab(d)["receipt_number"]
    s.check("D has part-paid and been numbered",
            _bill(d)["paid"] == 5.0 and bool(receipt_d),
            detail=f"paid {_bill(d)['paid']}, receipt {receipt_d!r}")

    r = oc.post(f"/pos/{d}/move", data={"merge_into": str(e)}, follow_redirects=True)
    s.check("the merge is refused", _tab(d)["status"] == "open",
            detail=f"D is {_tab(d)['status']}, merged into {_tab(d)['merged_into_order_id']}")
    s.check("D's order is still on D", len(_bill(d)["live"]) == 1,
            detail=f"{len(_bill(d)['live'])} line(s) on D")
    s.check("its EUR 5 is still on the bill it was paid against",
            _bill(d)["paid"] == 5.0 and _bill(d)["outstanding"] == 15.0,
            detail=f"paid {_bill(d)['paid']}, outstanding {_bill(d)['outstanding']}")
    s.check("and E has not been asked for anything extra",
            _bill(e)["total"] == 20.0 and _bill(e)["paid"] == 0.0,
            detail=f"E total {_bill(e)['total']}, paid {_bill(e)['paid']}")
    s.check("nothing was journalled as merged", not _merged_entries(d),
            detail=f"{len(_merged_entries(d))} tab_merged entr(ies) for D")
    said = " ".join(flashes(r))
    s.check("it says how much and on which receipt",
            "5.00" in said and receipt_d in said, detail=said)
    s.check("and names what to do instead",
            f"Bring Table {TAG}E onto Table {TAG}D" in said, detail=said)

    # ------------------------------------------------------------------
    s.section("The till page offers the way round, not the merge it would refuse")
    f = _open("F")
    _line(f, "Vin rouge", 30)
    _pay_cash(f, 10)
    k = _open("K")
    _line(k, "Cafe", 3)
    conn = db()
    # A charge sitting on the card reader for K. Set by hand: the reader is
    # Stripe's, and no test reaches Stripe.
    conn.execute("UPDATE pos_orders SET payment_intent_id = 'pi_" + TAG + "' WHERE id = ?", (k,))
    conn.commit()
    conn.close()

    page_d = oc.get(f"/pos/{d}").get_data(as_text=True)
    into_form, into_opts = _merge_form(page_d, "merge_into")
    bring_form, bring_opts = _merge_form(page_d, "merge_from")
    s.check("a tab holding money is not offered 'Merge into'", into_form is None,
            detail=f"offered {into_opts}")
    s.check("it is offered the other table to bring onto it", str(e) in bring_opts,
            detail=f"offered {bring_opts}, E is {e}")
    s.check("but not a table that has taken money too", str(f) not in bring_opts,
            detail=f"offered {bring_opts}, F is {f}")
    s.check("nor one with a card payment on the reader", str(k) not in bring_opts,
            detail=f"offered {bring_opts}, K is {k}")
    s.check("and the page says why", "already taken" in page_d and "keeps its bill" in page_d)

    page_e = oc.get(f"/pos/{e}").get_data(as_text=True)
    _, e_into = _merge_form(page_e, "merge_into")
    s.check("a tab with no money can still be merged into one that has some",
            str(d) in e_into, detail=f"offered {e_into}, D is {d}")
    s.check("but not into one with a card payment on the reader",
            str(k) not in e_into, detail=f"offered {e_into}, K is {k}")
    s.check("and it is not offered the bring-onto form it has no need of",
            _merge_form(page_e, "merge_from")[0] is None)

    page_k = oc.get(f"/pos/{k}").get_data(as_text=True)
    s.check("a tab with a card payment waiting is offered neither",
            _merge_form(page_k, "merge_into")[0] is None
            and _merge_form(page_k, "merge_from")[0] is None)
    s.check("and is told to let it finish or cancel it", "waiting on the reader" in page_k)

    # ------------------------------------------------------------------
    s.section("Bringing the other table onto the one with the money")
    # Submitted from the page's own form, so a template that drops or renames
    # the field fails here the way it would at the table.
    if s.check("the page has a form to bring E over", bring_form is not None):
        r = oc.post(bring_form["action"],
                    data=_harness.fill(bring_form, {"merge_from": str(e)}),
                    follow_redirects=True)
    else:
        # Straight to the route, so the rest still says what it finds.
        r = oc.post(f"/pos/{d}/move", data={"merge_from": str(e)}, follow_redirects=True)
    after = _bill(d)
    s.check("D keeps its bill and gains E's order", len(after["live"]) == 2,
            detail=f"{len(after['live'])} line(s) on D")
    s.check("the bill credits what was already paid",
            after["total"] == 40.0 and after["paid"] == 5.0 and after["outstanding"] == 35.0,
            detail=f"total {after['total']}, paid {after['paid']}, "
                   f"outstanding {after['outstanding']}")
    s.check("E is the tab emptied", _tab(e)["status"] == "void"
            and _tab(e)["merged_into_order_id"] == d,
            detail=f"E is {_tab(e)['status']}, into {_tab(e)['merged_into_order_id']}")
    s.check("the covers are added", _tab(d)["covers"] == 4, detail=str(_tab(d)["covers"]))
    s.check("D keeps its receipt number", _tab(d)["receipt_number"] == receipt_d,
            detail=str(_tab(d)["receipt_number"]))
    s.check("and the emptied tab never had one", not _tab(e)["receipt_number"],
            detail=str(_tab(e)["receipt_number"]))
    entries = _merged_entries(e)
    s.check("the journal says E went into D",
            len(entries) == 1 and f'"into_order_id":{d}' in entries[0]["payload"],
            detail=entries[0]["payload"] if entries else "no entry")
    s.check("the page lands on the bill that survived",
            r.request.path == f"/pos/{d}" and any("Merged" in x for x in flashes(r)),
            detail=f"{r.request.path} {flashes(r)}")

    s.section("The money reaches the cash-up")
    day = _tab(d)["service_date"]
    conn = db()
    cash_before = m.pos_day_report(conn, day)["by_method"].get("cash", 0)
    conn.close()
    _pay_cash(d, 35)
    conn = db()
    cash_after = m.pos_day_report(conn, day)["by_method"].get("cash", 0)
    conn.close()
    s.check("the merged bill settles", _tab(d)["status"] == "paid", detail=_tab(d)["status"])
    s.check("and the cash-up counts all 40, the 5 taken before the merge included",
            round(cash_after - cash_before, 2) == 40.0,
            detail=f"cash went {cash_before} -> {cash_after}")

    # ------------------------------------------------------------------
    s.section("Two tables that have both taken money stay two bills")
    g = _open("G")
    _line(g, "Dessert", 9)
    _pay_cash(g, 4)
    r1 = oc.post(f"/pos/{f}/move", data={"merge_into": str(g)}, follow_redirects=True)
    r2 = oc.post(f"/pos/{f}/move", data={"merge_from": str(g)}, follow_redirects=True)
    s.check("neither direction is allowed",
            _tab(f)["status"] == "open" and _tab(g)["status"] == "open"
            and len(_bill(f)["live"]) == 1 and len(_bill(g)["live"]) == 1,
            detail=f"F {_tab(f)['status']}, G {_tab(g)['status']}")
    s.check("and each keeps its own money",
            _bill(f)["paid"] == 10.0 and _bill(g)["paid"] == 4.0,
            detail=f"F paid {_bill(f)['paid']}, G paid {_bill(g)['paid']}")
    s.check("it says each settles on its own",
            any("each settles as its own bill" in x for x in flashes(r1) + flashes(r2)),
            detail=str(flashes(r1) + flashes(r2)))

    s.section("A tab with no money still merges into one that has some")
    h = _open("H")
    _line(h, "Digestif", 8)
    oc.post(f"/pos/{h}/move", data={"merge_into": str(g)}, follow_redirects=True)
    s.check("the lines move onto the paying tab",
            len(_bill(g)["live"]) == 2 and _tab(h)["status"] == "void",
            detail=f"{len(_bill(g)['live'])} line(s) on G, H {_tab(h)['status']}")
    s.check("and its payment is credited against the larger bill",
            _bill(g)["paid"] == 4.0 and _bill(g)["outstanding"] == 13.0,
            detail=f"paid {_bill(g)['paid']}, outstanding {_bill(g)['outstanding']}")

    s.section("A reopened tab has taken the lot")
    # The expensive case: settled in full, reopened for a forgotten coffee,
    # then merged. Before, its whole payment would have left the bill.
    j = _open("J")
    _line(j, "Plat", 25)
    _pay_cash(j, 25)
    # Reopening is refused inside a closed day, and another suite may well
    # have closed today by the time this runs. So J is put on a day nobody
    # has closed: what matters here is the merge, not the closure.
    conn = db()
    free = next(d for d in (_harness.house_today() + timedelta(days=i) for i in range(1, 400))
                if not conn.execute("SELECT 1 FROM pos_closures WHERE kind = 'day' AND period = ?",
                                    (d.isoformat(),)).fetchone())
    conn.execute("UPDATE pos_orders SET service_date = ? WHERE id = ?", (free.isoformat(), j))
    conn.commit()
    conn.close()
    oc.post(f"/pos/{j}/reopen", follow_redirects=True)
    j2 = _open("J2")
    _line(j2, "Cafe", 3)
    s.check("it is open again", _tab(j)["status"] == "open", detail=_tab(j)["status"])
    oc.post(f"/pos/{j}/move", data={"merge_into": str(j2)}, follow_redirects=True)
    s.check("and cannot be merged away",
            _tab(j)["status"] == "open" and _bill(j)["paid"] == 25.0
            and _bill(j2)["paid"] == 0.0,
            detail=f"J {_tab(j)['status']} paid {_bill(j)['paid']}; J2 paid {_bill(j2)['paid']}")

    # ------------------------------------------------------------------
    s.section("A card payment waiting on the reader")
    l = _open("L")
    _line(l, "Biere", 6)
    r1 = oc.post(f"/pos/{k}/move", data={"merge_into": str(l)}, follow_redirects=True)
    s.check("the tab with the charge cannot be emptied",
            _tab(k)["status"] == "open" and len(_bill(k)["live"]) == 1,
            detail=f"K {_tab(k)['status']}")
    r2 = oc.post(f"/pos/{l}/move", data={"merge_into": str(k)}, follow_redirects=True)
    s.check("nor can another be merged into it while the reader is asking",
            _tab(l)["status"] == "open" and len(_bill(k)["live"]) == 1,
            detail=f"L {_tab(l)['status']}, {len(_bill(k)['live'])} line(s) on K")
    s.check("and both say so",
            all(any("waiting on the reader" in x for x in flashes(r)) for r in (r1, r2)),
            detail=str(flashes(r1) + flashes(r2)))
    # Cancelled the way the page says to, with the call to the reader stood
    # down: the reader is Stripe's, and no test reaches Stripe.
    real_cancel = m.terminal_cancel
    m.terminal_cancel = lambda reader_id, intent_id: ""
    try:
        oc.post(f"/pos/{k}/cancel-card", follow_redirects=True)
    finally:
        m.terminal_cancel = real_cancel
    oc.post(f"/pos/{l}/move", data={"merge_into": str(k)}, follow_redirects=True)
    s.check("once it is cancelled the merge goes through",
            _tab(l)["status"] == "void" and len(_bill(k)["live"]) == 2,
            detail=f"L {_tab(l)['status']}, {len(_bill(k)['live'])} line(s) on K")

    # ------------------------------------------------------------------
    s.section("A QR link paid after its table was merged away")
    q = _open("Q")
    _line(q, "Menu enfant", 12)
    n = _open("N")
    _line(n, "Menu du jour", 20)
    conn = db()
    conn.execute("UPDATE pos_orders SET stripe_session_id = ? WHERE id = ?",
                 ("cs_" + TAG + "q", q))
    conn.commit()
    conn.close()
    oc.post(f"/pos/{q}/move", data={"merge_into": str(n)}, follow_redirects=True)
    s.check("a link shown is not money taken, so the merge goes ahead",
            _tab(q)["status"] == "void" and _tab(q)["merged_into_order_id"] == n,
            detail=f"Q {_tab(q)['status']}")
    paid_link = {"id": "cs_" + TAG + "q", "amount_total": 1200, "payment_status": "paid"}
    conn = db()
    with m.app.test_request_context():
        m.settle_pos_from_stripe_session(conn, paid_link, {"pos_order_id": str(q)})
    conn.close()
    s.check("the guest's payment lands on the bill the lines went to",
            _bill(n)["paid"] == 12.0 and _bill(n)["outstanding"] == 20.0,
            detail=f"N paid {_bill(n)['paid']}, outstanding {_bill(n)['outstanding']}")
    s.check("not on the void tab, where nothing would ever read it",
            not _payments_on(q), detail=f"{len(_payments_on(q))} payment(s) on Q")
    conn = db()
    entry = conn.execute(
        "SELECT payload FROM pos_journal WHERE event_type = 'payment_taken' AND order_id = ? "
        "ORDER BY sequence DESC LIMIT 1", (n,)).fetchone()
    conn.close()
    s.check("and the journal says which table the link was shown on",
            entry is not None and f'"raised_on_order_id":{q}' in entry["payload"],
            detail=entry["payload"] if entry else "no payment entry on N")
    conn = db()
    with m.app.test_request_context():
        m.settle_pos_from_stripe_session(conn, paid_link, {"pos_order_id": str(q)})
    conn.commit()
    conn.close()
    s.check("a retried webhook still pays once", _bill(n)["paid"] == 12.0,
            detail=str(_bill(n)["paid"]))

    # ------------------------------------------------------------------
    s.section("The records hold together")
    conn = db()
    stray = conn.execute(
        """SELECT table_label FROM pos_orders WHERE table_label LIKE ? AND status = 'void'
           AND (receipt_number IS NOT NULL
                OR id IN (SELECT order_id FROM pos_payments))""", (TAG + "%",)).fetchall()
    chain = m.pos_journal_verify(conn)
    gaps_after = m.receipt_sequence_gaps(conn)
    conn.close()
    s.check("no void tab holds a payment or a receipt number", not stray,
            detail=", ".join(x["table_label"] for x in stray))
    s.check("the receipt numbering has no new holes", gaps_after == gaps_before,
            detail=f"{gaps_before} -> {gaps_after}")
    if chain_was_sound:
        s.check("and the journal chain still verifies", chain is None, detail=str(chain))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
