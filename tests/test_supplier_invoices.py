"""Supplier invoices, and the four things about them that reached the books.

WHAT WAS THERE. One upload link, shared by every supplier, for ever. It could
not be withdrawn from one of them without withdrawing it from all of them.
Nothing recorded which supplier had sent what, because the supplier typed
their own company name into a box — so a typo made a second supplier and a
forwarded link made anybody's invoice look like theirs. The route had no rate
limit at all, which made the address a way for whoever held it to put any
number of rows into the house's payables.

WHAT WAS WORSE, because it reached the accountant. The invoice's own facts
were never asked for, so the ones sent onward were made up from what was to
hand:

  - the DATE was the day somebody uploaded it, not the date on the paper;
  - the DEADLINE was that same day, so every supplier invoice arrived at the
    accountant already due, and an ageing report would have said everything
    was overdue;
  - the INVOICE NUMBER was the description, so nothing could be reconciled
    against a supplier's own statement;
  - the VAT was the food rate applied to everything and derived from the
    total, which is a wrong number on the return rather than a missing one.

vendors.payment_terms had existed the whole time and nothing read it.

WHAT THIS FILE WILL NOT DO. It will not check that a duplicate is refused,
because it must not be. A supplier re-sending a chaser looks exactly like a
second invoice and so does buying the same case of wine twice in a month.
Only a person can tell, so the job is to put it in front of one.
"""
import io
import os
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTSUP"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM expenses WHERE description LIKE ? OR vendor_name LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action LIKE 'supplier_invoice:%'")
    conn.commit()
    conn.close()


def _vendor(name, terms=None):
    conn = db()
    conn.execute(
        "INSERT INTO vendors (name, payment_terms, active, created_at) VALUES (?, ?, 1, ?)",
        (TAG + " " + name, terms, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM vendors WHERE name = ?", (TAG + " " + name,)).fetchone()
    conn.close()
    return row


def _post(client, token, **fields):
    data = {"description": TAG + " a delivery", "amount": "120.00",
            "invoice_date": datetime.now(m.LOCAL_TZ).date().isoformat(),
            "csrf_token": "x"}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    data["invoice"] = (io.BytesIO(b"%PDF-1.4 an invoice\n"), "invoice.pdf")
    return client.post(f"/supplier-invoices/submit/{token}", data=data,
                       content_type="multipart/form-data", follow_redirects=True)


def run():
    s = Suite("Supplier invoices")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()
    today = datetime.now(m.LOCAL_TZ).date()

    # ------------------------------------------------------- terms parsing
    s.section("Reading what a supplier means by their terms")
    # Free text, because a supplier writes what they like on an invoice.
    for text, want in (("30 days", 30), ("Net 30", 30), ("45 jours", 45),
                       ("payable a reception", 0), ("60 DAYS", 60)):
        s.check(f"{text!r} reads as {want} day(s)",
                m.parse_payment_terms(text) == want,
                detail=str(m.parse_payment_terms(text)))
    for text in ("fin de mois", "when we get round to it", "", None):
        s.check(f"{text!r} is not guessed at", m.parse_payment_terms(text) is None,
                detail="a due date invented from an unreadable phrase is worse "
                       "than an honest default nobody mistakes for their terms")
    s.check("a due date is the invoice date plus the terms",
            m.invoice_due_date("2026-03-03", "30 days") == "2026-04-02",
            detail=str(m.invoice_due_date("2026-03-03", "30 days")))
    s.check("and unreadable terms fall back to thirty days rather than to today",
            m.invoice_due_date("2026-03-03", "fin de mois") == "2026-04-02",
            detail="everything used to be due the day it was uploaded")
    s.check("no invoice date means no due date, not today's",
            m.invoice_due_date("", "30 days") is None)

    # ------------------------------------------------------ per-vendor link
    s.section("A supplier with a link of their own")
    v = _vendor("Boucherie", terms="30 days")
    s.check("a new supplier has no link until one is given", not v["upload_token"])

    r = oc.post(f"/management/vendors/{v['id']}/upload-link", follow_redirects=True)
    v = _one("SELECT * FROM vendors WHERE id = ?", (v["id"],))
    s.check("the owner can issue one", bool(v["upload_token"]), detail=str(flashes(r)))
    s.check("stamped with when", bool(v["token_issued_at"]))
    s.check("and it is on the record",
            _one("SELECT COUNT(*) AS c FROM audit_log "
                 "WHERE action = 'vendor_upload_link_issued' AND target = ?",
                 (v["name"],))["c"] == 1)

    page = anon.get(f"/supplier-invoices/submit/{v['upload_token']}")
    body = page.get_data(as_text=True)
    s.check("the link opens", page.status_code == 200, detail=str(page.status_code))
    s.check("and it names them rather than asking who they are",
            v["name"] in body and 'name="vendor_name"' not in body,
            detail="the typed name was the only thing tying an invoice to a "
                   "supplier, so a typo made a second one")
    s.check("it tells them the terms the due date will be worked out from",
            "30 days" in body, detail=body[:200] if "30 days" not in body else "")
    s.check("and it asks search engines to stay away",
            "noindex" in body,
            detail="this page is reached by a link, not by a password")

    # ---------------------------------------------------------- submission
    s.section("An invoice arriving through it")
    r = _post(anon, v["upload_token"], invoice_number="F-2026-0412",
              invoice_date=(today - timedelta(days=2)).isoformat(),
              amount="240.50", tax_amount="40.08")
    inv = _one("SELECT * FROM expenses WHERE description = ? ORDER BY id DESC LIMIT 1",
               (TAG + " a delivery",))
    s.check("it is recorded", inv is not None, detail=str(flashes(r)))
    s.check("attributed to the supplier, not to a typed name",
            inv and inv["vendor_id"] == v["id"], detail=str(inv["vendor_id"]) if inv else "")
    s.check("carrying their own invoice number", inv and inv["invoice_number"] == "F-2026-0412")
    s.check("and the date printed on it, not the date it was uploaded",
            inv and inv["invoice_date"] == (today - timedelta(days=2)).isoformat(),
            detail=str(inv["invoice_date"]) if inv else "")
    s.check("with the VAT they stated, not a rate we assumed",
            inv and abs((inv["tax_amount"] or 0) - 40.08) < 0.01,
            detail=str(inv["tax_amount"]) if inv else "")
    want_due = (today - timedelta(days=2) + timedelta(days=30)).isoformat()
    s.check("and a due date worked out from their terms", inv and inv["due_date"] == want_due,
            detail=f"{inv['due_date']}, expected {want_due}" if inv else "")
    s.check("the supplier's last-seen date is updated",
            _one("SELECT last_submitted_at FROM vendors WHERE id = ?",
                 (v["id"],))["last_submitted_at"] is not None)
    s.check("and it is on the record",
            _one("SELECT COUNT(*) AS c FROM audit_log "
                 "WHERE action = 'supplier_invoice_received' AND target = ?",
                 (v["name"],))["c"] >= 1)

    s.section("What it will not accept")
    before = _one("SELECT COUNT(*) AS c FROM expenses WHERE vendor_id = ?", (v["id"],))["c"]
    r = _post(anon, v["upload_token"], invoice_date="", description=TAG + " undated")
    s.check("an invoice with no date is refused",
            any("date on the invoice" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    r = _post(anon, v["upload_token"],
              invoice_date=(today + timedelta(days=400)).isoformat(),
              description=TAG + " from the future")
    s.check("and one dated next year is refused as the typo it usually is",
            any("future" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    r = _post(anon, v["upload_token"], amount="100", tax_amount="150",
              description=TAG + " impossible vat")
    s.check("VAT larger than the total is refused",
            any("vat" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and none of the three was stored",
            _one("SELECT COUNT(*) AS c FROM expenses WHERE vendor_id = ?", (v["id"],))["c"] == before,
            detail="a refusal that still writes the row is not a refusal")

    # ----------------------------------------------------------- duplicates
    s.section("The same invoice arriving twice")
    dup_v = _vendor("Cave", terms="Net 30")
    conn = db()
    m.issue_vendor_token(conn, dup_v["id"])
    conn.commit()
    conn.close()
    tok = _one("SELECT upload_token FROM vendors WHERE id = ?", (dup_v["id"],))["upload_token"]

    _post(anon, tok, invoice_number="C-900", amount="310.00",
          description=TAG + " first time")
    _post(anon, tok, invoice_number="C-900", amount="310.00",
          description=TAG + " second time")
    second = _one("SELECT * FROM expenses WHERE description = ?", (TAG + " second time",))
    first = _one("SELECT * FROM expenses WHERE description = ?", (TAG + " first time",))
    s.check("the second one is still accepted", second is not None,
            detail="refusing it would send a supplier away over something only "
                   "a person can judge")
    s.check("but flagged against the first", second and second["duplicate_of_id"] == first["id"],
            detail=str(second["duplicate_of_id"]) if second else "")

    _post(anon, tok, invoice_number="C-901", amount="310.00",
          description=TAG + " same amount no number")
    same_amount = _one("SELECT * FROM expenses WHERE description = ?",
                       (TAG + " same amount no number",))
    s.check("a different number for the same amount within the window is flagged too",
            same_amount and same_amount["duplicate_of_id"] is not None,
            detail="a supplier re-sending a chaser rarely reuses the number")

    _post(anon, tok, invoice_number="C-902", amount="77.77",
          description=TAG + " unrelated")
    unrelated = _one("SELECT * FROM expenses WHERE description = ?", (TAG + " unrelated",))
    s.check("an ordinary second invoice is not flagged",
            unrelated and unrelated["duplicate_of_id"] is None,
            detail="if everything were flagged the flag would mean nothing")

    r = oc.get("/expenses")
    s.check("and the warning is where the owner will see it",
            "may already have this one" in r.get_data(as_text=True).lower(),
            detail="a flag on a row nobody opens is not a warning")

    # -------------------------------------------------------- withdrawing
    s.section("Withdrawing one supplier's link, and nobody else's")
    r = oc.post(f"/management/vendors/{v['id']}/revoke-link", follow_redirects=True)
    s.check("it is withdrawn", _one("SELECT token_revoked_at FROM vendors WHERE id = ?",
                                    (v["id"],))["token_revoked_at"] is not None,
            detail=str(flashes(r)))
    gone = anon.get(f"/supplier-invoices/submit/{v['upload_token']}")
    s.check("the link stops working", gone.status_code == 404,
            detail=f"HTTP {gone.status_code}")
    still = anon.get(f"/supplier-invoices/submit/{tok}")
    s.check("and the other supplier's still works", still.status_code == 200,
            detail="this is the whole reason for a link each — one shared token "
                   "could only ever be withdrawn from everybody at once")

    # The token is kept rather than blanked, so the trail still resolves.
    s.check("the withdrawn token is kept, not erased",
            _one("SELECT upload_token FROM vendors WHERE id = ?", (v["id"],))["upload_token"],
            detail="blanking it would leave an audit line pointing at nothing")

    r = oc.post(f"/management/vendors/{v['id']}/upload-link", follow_redirects=True)
    reissued = _one("SELECT * FROM vendors WHERE id = ?", (v["id"],))
    s.check("a new one can be issued after that", reissued["token_revoked_at"] is None)
    s.check("and it is a different token", reissued["upload_token"] != v["upload_token"],
            detail="reissuing the same string would put whoever had the old one "
                   "straight back in")
    back = anon.get(f"/supplier-invoices/submit/{reissued['upload_token']}")
    s.check("the new one works", back.status_code == 200)
    dead = anon.get(f"/supplier-invoices/submit/{v['upload_token']}")
    s.check("and the old one still does not", dead.status_code == 404,
            detail=f"HTTP {dead.status_code}")

    # ---------------------------------------------------------- the ageing
    s.section("What the house owes, and how late")
    conn = db()
    for label, due_offset in (("overdue", -10), ("due soon", 3), ("later", 60)):
        conn.execute(
            """INSERT INTO expenses (kind, vendor_id, vendor_name, description, amount,
               status, invoice_date, due_date, submitted_at)
               VALUES ('supplier_invoice', ?, ?, ?, 50.0, 'approved', ?, ?, ?)""",
            (dup_v["id"], dup_v["name"], TAG + " ageing " + label,
             today.isoformat(), (today + timedelta(days=due_offset)).isoformat(),
             datetime.now(timezone.utc).isoformat()))
    conn.commit()
    with m.app.test_request_context():
        ageing = m.payables_ageing(conn, today)
    conn.close()
    refs = {b: [r["description"] for r in rows] for b, rows in ageing["buckets"].items()}
    s.check("something ten days past its date is overdue",
            TAG + " ageing overdue" in refs["overdue"], detail=str(refs["overdue"])[:110])
    s.check("something due this week is due soon",
            TAG + " ageing due soon" in refs["due_soon"], detail=str(refs["due_soon"])[:110])
    s.check("and something two months out is neither",
            TAG + " ageing later" in refs["not_yet_due"], detail=str(refs["not_yet_due"])[:110])
    s.check("the totals add up to the total",
            abs(sum(ageing["totals"].values()) - ageing["total"]) < 0.01,
            detail=f"{ageing['totals']} vs {ageing['total']}")

    r = oc.get("/management/vendors")
    s.check("the vendors page says what is outstanding",
            "outstanding" in r.get_data(as_text=True).lower(),
            detail="lifetime spend says what was bought; this says what has not "
                   "been paid for, which is the figure that decides whether "
                   "they keep delivering")

    # ------------------------------------------------------------ who may
    s.section("Who can hand out a link")
    v2 = _vendor("Poissonnerie")
    r = ec.post(f"/management/vendors/{v2['id']}/upload-link", follow_redirects=False)
    s.check("an employee cannot issue one", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and none was issued",
            _one("SELECT upload_token FROM vendors WHERE id = ?", (v2["id"],))["upload_token"] is None,
            detail="the redirect alone would pass even if the token had been made")
    r = anon.post(f"/management/vendors/{v2['id']}/revoke-link", follow_redirects=False)
    s.check("nor can a stranger withdraw one", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")
    r = oc.post("/management/vendors/99999999/upload-link", follow_redirects=False)
    s.check("a supplier that does not exist is a 404", r.status_code == 404,
            detail=f"HTTP {r.status_code}")

    s.section("The link is not a way to fill the ledger")
    conn = db()
    m.issue_vendor_token(conn, v2["id"])
    conn.commit()
    conn.close()
    tok2 = _one("SELECT upload_token FROM vendors WHERE id = ?", (v2["id"],))["upload_token"]
    refused = 0
    for i in range(m.BOOKING_RATE_LIMIT_PER_HOUR + 3):
        rr = _post(anon, tok2, invoice_number=f"R-{i}", description=TAG + f" flood {i}")
        if any("lot of invoices" in f.lower() for f in flashes(rr)):
            refused += 1
    s.check("a flood is eventually refused", refused > 0,
            detail=f"{refused} refused of {m.BOOKING_RATE_LIMIT_PER_HOUR + 3} — "
                   "without this the address is a way for whoever holds it to "
                   "put any number of rows in the payables")
    s.check("and the refusals stopped it writing",
            _one("SELECT COUNT(*) AS c FROM expenses WHERE description LIKE ?",
                 (TAG + " flood%",))["c"] <= m.BOOKING_RATE_LIMIT_PER_HOUR,
            detail="a limit that says no and stores it anyway is not a limit")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
