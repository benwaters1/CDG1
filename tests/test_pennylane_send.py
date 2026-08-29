"""Sending a cost to the accountant, and everything that must stop first.

These are the only routes in the app that write into a real accounting system,
and the token for it is live. So the first thing checked here is not behaviour
at all: it is that the suite cannot reach Pennylane even while exercising the
code that would. `_harness` replaces `_pennylane_request` with a function that
raises, and every check below stands in one layer above it — at
pennylane_upload_file and friends — so a stand-in that is forgotten or wrongly
named fails loudly instead of quietly making a network call.

Every check whose subject is a REFUSAL is treated as guilty until its control
proves otherwise. Four checks between the two sessions working on this repo
have now passed for the wrong reason and every one of them was that shape: a
404 the URL converter produced before the handler ran, a 302 that redirected to
the page rather than away from it, a guard on a route that was disabled anyway,
and a word matched somewhere else on the page. So the refusals here assert what
is in the database afterwards, not the status code.

The arithmetic is the other half. Pennylane rejects an invoice whose lines do
not add up to its total, and the house's own rule is that rows must reconcile
with the figure above them. `_expense_pennylane_lines` turns whatever was read
into stock into accounting lines and puts the rest on one balancing line — so
what the accountant receives is the classification already done here, and it
has to come to the same number either way.
"""
import os
from datetime import datetime, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTPEN"


def _cleanup():
    conn = db()
    for r in conn.execute("SELECT filename FROM expenses WHERE vendor_name LIKE ?",
                          (TAG + "%",)).fetchall():
        p = os.path.join(m.UPLOAD_DIR, r["filename"] or "")
        if r["filename"] and os.path.exists(p):
            os.remove(p)
    conn.execute("""DELETE FROM stock_movements WHERE expense_id IN
                    (SELECT id FROM expenses WHERE vendor_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM stock_items WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _expense(vendor, amount=120.0, with_file=True, invoice_id=None, ledger=None,
             description="Case of wine"):
    """An expense, optionally with a real file on disk."""
    conn = db()
    stored = None
    if with_file:
        stored = f"{TAG.lower()}_{vendor.replace(' ', '')}.pdf"
        with open(os.path.join(m.UPLOAD_DIR, stored), "wb") as fh:
            fh.write(b"%PDF-1.4 invoice")
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, filename,
           status, ledger_code, pennylane_invoice_id, submitted_at)
           VALUES ('supplier_invoice', ?, ?, ?, ?, 'approved', ?, ?, ?)""",
        (TAG + " " + vendor, description, amount, stored, ledger, invoice_id,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM expenses WHERE vendor_name = ?",
                       (TAG + " " + vendor,)).fetchone()
    conn.close()
    return row


def _get(expense_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    finally:
        conn.close()


class _Pennylane:
    """Stands in for the three calls that leave the building.

    One layer above `_pennylane_request`, which the harness has already
    replaced with a raiser — so if any of these names stops being the seam,
    the call falls through to that and the suite says so instead of dialling
    the accountant.
    """

    def __init__(self, supplier=(True, 4242), upload=(True, "file_1"),
                 invoice=(True, {"id": "inv_9"})):
        self.supplier, self.upload, self.invoice = supplier, upload, invoice
        self.sent = []

    def install(self):
        self._real = (m.pennylane_find_supplier, m.pennylane_upload_file,
                      m.pennylane_import_supplier_invoice, m.PENNYLANE_API_TOKEN)
        m.pennylane_find_supplier = lambda name: self.supplier
        m.pennylane_upload_file = lambda b, fn: self.upload
        def _import(**kw):
            self.sent.append(kw)
            return self.invoice
        m.pennylane_import_supplier_invoice = _import
        m.PENNYLANE_API_TOKEN = "pnl_stand_in_not_real"
        return self

    def remove(self):
        (m.pennylane_find_supplier, m.pennylane_upload_file,
         m.pennylane_import_supplier_invoice, m.PENNYLANE_API_TOKEN) = self._real

    def __enter__(self):
        return self.install()

    def __exit__(self, *a):
        self.remove()
        return False


def run():
    s = Suite("Sending a cost to Pennylane")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("The suite cannot reach the accountant")
    # First, because the token is live and everything below exercises the code
    # that would use it.
    s.check("Pennylane reads as not connected under test",
            m.pennylane_configured() is False)
    raised = False
    try:
        m._pennylane_request("GET", "/accounts")
    except Exception:
        raised = True
    s.check("and the request function refuses to run at all", raised,
            detail="the harness's block is what stands between this suite and "
                   "the real ledger")

    s.section("Unconfigured, it refuses and writes nothing")
    e = _expense("Caves")
    r = oc.post(f"/expenses/{e['id']}/send-to-pennylane", follow_redirects=True)
    after = _get(e["id"])
    s.check("no invoice id is recorded", after["pennylane_invoice_id"] is None,
            detail=str(after["pennylane_invoice_id"]))
    s.check("and it says why rather than failing silently",
            any("pennylane" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("The same cost cannot be filed twice")
    # Duplicating a supplier invoice in the accountant's books is the expensive
    # mistake here, and a double-submit is the ordinary way to make it.
    done = _expense("Deja", invoice_id="inv_already")
    with _Pennylane() as pnl:
        r = oc.post(f"/expenses/{done['id']}/send-to-pennylane", follow_redirects=True)
    s.check("nothing is sent for one already in Pennylane", pnl.sent == [],
            detail=f"{len(pnl.sent)} invoice(s) sent for a cost already filed")
    s.check("and its existing id is untouched",
            _get(done["id"])["pennylane_invoice_id"] == "inv_already",
            detail=str(_get(done["id"])["pennylane_invoice_id"]))
    s.check("the page says it is already there",
            any("already" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("Nothing goes without its document")
    bare = _expense("Nodoc", with_file=False)
    with _Pennylane() as pnl:
        r = oc.post(f"/expenses/{bare['id']}/send-to-pennylane", follow_redirects=True)
    s.check("an expense with no file is not sent", pnl.sent == [],
            detail=f"{len(pnl.sent)} sent without an invoice attached")
    # "Nothing was sent" is also true when the route throws, and removing the
    # guard makes it throw — os.path.join(UPLOAD_DIR, None) raises before
    # anything reaches Pennylane. So the absence proves nothing on its own;
    # what proves the guard is that the refusal is deliberate and survivable.
    s.check("and it refuses rather than falling over", r.status_code == 200,
            detail=f"HTTP {r.status_code} — a 500 also sends nothing")
    s.check("and is asked for one",
            any("attach" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    # A filename in the row whose file is not on disk is a different fault from
    # no filename at all, and it must not be reported as the same thing.
    lost = _expense("Lostfile")
    os.remove(os.path.join(m.UPLOAD_DIR, lost["filename"]))
    with _Pennylane() as pnl:
        r = oc.post(f"/expenses/{lost['id']}/send-to-pennylane", follow_redirects=True)
    s.check("a file missing from the server is not sent either", pnl.sent == [])
    s.check("and that too is a refusal, not a crash", r.status_code == 200,
            detail=f"HTTP {r.status_code} — open() on a missing path sends "
                   "nothing either, and is not the same thing")
    s.check("and is reported as missing, not as unattached",
            any("missing" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    s.section("A refusal from Pennylane is recorded, not swallowed")
    fail = _expense("Refused")
    with _Pennylane(invoice=(False, "422 lines do not balance")):
        oc.post(f"/expenses/{fail['id']}/send-to-pennylane", follow_redirects=True)
    after = _get(fail["id"])
    s.check("the error is kept against the cost",
            after["pennylane_error"] and "422" in after["pennylane_error"],
            detail=str(after["pennylane_error"]))
    s.check("and it is NOT marked as sent", after["pennylane_invoice_id"] is None,
            detail=f"{after['pennylane_invoice_id']} — a cost marked sent that "
                   "was refused never reaches the accountant and nothing chases it")

    upfail = _expense("Uploadfail")
    with _Pennylane(upload=(False, "413 too large")):
        oc.post(f"/expenses/{upfail['id']}/send-to-pennylane", follow_redirects=True)
    s.check("a rejected document is recorded the same way",
            "413" in (_get(upfail["id"])["pennylane_error"] or ""),
            detail=str(_get(upfail["id"])["pennylane_error"]))
    s.check("and leaves nothing marked sent",
            _get(upfail["id"])["pennylane_invoice_id"] is None)

    s.section("A successful send")
    good = _expense("Goodsend", amount=240.0)
    with _Pennylane() as pnl:
        r = oc.post(f"/expenses/{good['id']}/send-to-pennylane", follow_redirects=True)
    after = _get(good["id"])
    s.check("one invoice is sent", len(pnl.sent) == 1, detail=str(len(pnl.sent)))
    s.check("the returned id is stored", after["pennylane_invoice_id"] == "inv_9",
            detail=str(after["pennylane_invoice_id"]))
    s.check("with when it went", bool(after["pennylane_synced_at"]))
    s.check("and any previous error is cleared", after["pennylane_error"] is None)
    s.check("it carries a reference back to this expense",
            pnl.sent and pnl.sent[0]["external_reference"] == f"gudanes-expense-{good['id']}",
            detail=str(pnl.sent[0].get("external_reference")) if pnl.sent else "")
    conn = db()
    logged = conn.execute(
        "SELECT COUNT(*) c FROM audit_log WHERE action = 'pennylane_sent' AND target = ?",
        (f"expense #{good['id']}",)).fetchone()["c"]
    conn.close()
    s.check("and it is written to the audit log", logged == 1, detail=str(logged))

    # Having sent it once, the duplicate guard is now live on a real send.
    with _Pennylane() as pnl2:
        oc.post(f"/expenses/{good['id']}/send-to-pennylane", follow_redirects=True)
    s.check("sending the same one again does nothing", pnl2.sent == [],
            detail=f"{len(pnl2.sent)} — the guard has to hold after a real send, "
                   "not just against a hand-written id")

    s.section("The lines add up to the invoice")
    # Pennylane rejects an invoice whose lines do not sum to its total, and the
    # house's own rule is that rows reconcile with the figure above them.
    conn = db()
    vat = m.tax_rate(conn, "vat_food") or 20.0
    plain = _expense("Plain", amount=120.0)
    lines = m._expense_pennylane_lines(conn, _get(plain["id"]))
    conn.close()
    gross = sum(float(l["currency_amount"]) + float(l["currency_tax"]) for l in lines)
    s.check("a cost with nothing stocked becomes one line", len(lines) == 1,
            detail=str(len(lines)))
    s.check("and its net plus tax is the invoice total", abs(gross - 120.0) < 0.02,
            detail=f"{gross:.2f} vs 120.00")

    # Now with stock behind it: the classification already done here is what
    # the accountant receives.
    conn = db()
    conn.execute(
        """INSERT INTO stock_items (name, unit, ledger_code, active, created_at)
           VALUES (?, 'bottle', '606100', 1, ?)""",
        (TAG + " Wine", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    item = conn.execute("SELECT id FROM stock_items WHERE name = ?",
                        (TAG + " Wine",)).fetchone()["id"]
    conn.close()
    stocked = _expense("Stocked", amount=240.0)
    conn = db()
    m.record_stock_movement(conn, item, 10, "purchase", unit_cost=10.0,
                            expense_id=stocked["id"], user_id=owner["id"])
    conn.commit()
    lines = m._expense_pennylane_lines(conn, _get(stocked["id"]))
    conn.close()
    gross = sum(float(l["currency_amount"]) + float(l["currency_tax"]) for l in lines)
    s.check("the stock line carries the item's ledger code",
            any(l.get("ledger_account_number") == "606100" for l in lines),
            detail=str([l.get("ledger_account_number") for l in lines]))
    s.check("what stock did not cover goes on a balancing line", len(lines) == 2,
            detail=str([l["label"] for l in lines]))
    s.check("and the lines still come to the invoice total",
            abs(gross - 240.0) < 0.02, detail=f"{gross:.2f} vs 240.00")

    # And when stock accounts for all of it, no balancing line is invented.
    exact = _expense("Exact", amount=round(100 * (1 + vat / 100), 2))
    conn = db()
    m.record_stock_movement(conn, item, 10, "purchase", unit_cost=10.0,
                            expense_id=exact["id"], user_id=owner["id"])
    conn.commit()
    lines = m._expense_pennylane_lines(conn, _get(exact["id"]))
    conn.close()
    s.check("a fully stocked invoice gets no spurious extra line", len(lines) == 1,
            detail=str([l["label"] for l in lines]))

    s.section("Scanning without a scanner")
    conn = db()
    before = conn.execute("SELECT COUNT(*) c FROM expenses").fetchone()["c"]
    conn.close()
    r = oc.post("/expenses/scan", follow_redirects=True)
    conn = db()
    after_count = conn.execute("SELECT COUNT(*) c FROM expenses").fetchone()["c"]
    conn.close()
    s.check("no expense is invented when there is no scanner", after_count == before,
            detail=f"{after_count - before} row(s) created")
    s.check("and it says to photograph it instead",
            any("scanner" in f.lower() or "photograph" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))

    s.section("None of it is the employees'")
    send_target = _expense("Guarded")
    with _Pennylane() as pnl:
        ec.post(f"/expenses/{send_target['id']}/send-to-pennylane")
        ec.post("/admin/pennylane/sync")
        ec.post("/expenses/scan")
    s.check("an employee cannot send a cost to the accountant", pnl.sent == [],
            detail=f"{len(pnl.sent)} sent by an employee")
    s.check("and nothing was marked as sent",
            _get(send_target["id"])["pennylane_invoice_id"] is None)
    s.check("nor open the account mapping",
            ec.get("/admin/pennylane").status_code in (302, 403))
    s.check("while the owner can", oc.get("/admin/pennylane").status_code == 200)

    s.section("And the block is still in place afterwards")
    # A stand-in put back wrongly would leave the next suite talking to the
    # real ledger, so this is checked rather than assumed.
    s.check("Pennylane is unconfigured again", m.pennylane_configured() is False)
    still = False
    try:
        m._pennylane_request("GET", "/accounts")
    except Exception:
        still = True
    s.check("and the request function still refuses", still)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
