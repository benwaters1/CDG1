"""Bulk approvals, recurring costs, and the supplier's upload link.

Three untested clusters that all move money or open a door:

bulk_approve_queue approves leave and expenses from one checkbox list. Its
whole safety rests on `AND status = 'pending'` inside each UPDATE, so an item
already decided cannot be re-approved by a stale page — somebody looking at
yesterday's queue, or a double-submitted form. That is invisible until it
double-pays a supplier.

regenerate_supplier_link promises the old link "no longer works". Nobody had
ever checked that it does stop working, which is the only part that matters:
that link is unauthenticated and lets a stranger upload an invoice.

Recurring costs feed the financial picture, and a toggle that silently does
nothing shows the owner a monthly figure that is wrong.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZAPPR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM expenses WHERE vendor_name LIKE ? OR description LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM leave_requests WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _pending_expense(amount=250.0):
    conn = db()
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status, submitted_at)
           VALUES ('supplier_invoice', ?, ?, ?, 'pending', ?)""",
        (f"{TAG} Supplier", f"{TAG} invoice", amount,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM expenses WHERE vendor_name LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    return row


def _pending_leave(emp_id):
    start = datetime.now(timezone.utc).date() + timedelta(days=400)
    conn = db()
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(leave_requests)").fetchall()}
    fields = {"user_id": emp_id, "start_date": start.isoformat(),
              "end_date": (start + timedelta(days=2)).isoformat(),
              "reason": f"{TAG} holiday", "status": "pending",
              "requested_at": datetime.now(timezone.utc).isoformat()}
    if "leave_type" in cols:
        fields["leave_type"] = "vacation"
    keys = [k for k in fields if k in cols]
    conn.execute(f"INSERT INTO leave_requests ({', '.join(keys)}) "
                 f"VALUES ({', '.join('?' * len(keys))})", [fields[k] for k in keys])
    conn.commit()
    row = conn.execute(
        "SELECT * FROM leave_requests WHERE reason LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Approvals and supplier money")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("Approving a queue in bulk")
    exp = _pending_expense(250.0)
    lv = _pending_leave(emp["id"])
    r = oc.post("/admin/approvals/bulk",
                data={"items": [f"expense:{exp['id']}", f"leave:{lv['id']}"]},
                follow_redirects=True)
    conn = db()
    exp_after = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp["id"],)).fetchone()
    lv_after = conn.execute("SELECT * FROM leave_requests WHERE id = ?", (lv["id"],)).fetchone()
    conn.close()
    s.check("the expense is approved", exp_after["status"] == "approved", r)
    s.check("and stamped with when", exp_after["decided_at"] is not None)
    s.check("the leave request is approved", lv_after["status"] == "approved")

    s.section("A stale page cannot approve the same thing twice")
    # The guard is `AND status = 'pending'` inside the UPDATE. Re-posting the
    # same ids is what a double-submit or a yesterday's-queue reload does.
    before = exp_after["decided_at"]
    oc.post("/admin/approvals/bulk",
            data={"items": [f"expense:{exp['id']}", f"leave:{lv['id']}"]},
            follow_redirects=True)
    conn = db()
    again = conn.execute("SELECT * FROM expenses WHERE id = ?", (exp["id"],)).fetchone()
    conn.close()
    s.check("the decided-at timestamp is not overwritten", again["decided_at"] == before,
            detail=f"{before} -> {again['decided_at']}")

    s.section("Nonsense in the checkbox list is ignored, not fatal")
    fresh = _pending_expense(90.0)
    r2 = oc.post("/admin/approvals/bulk", data={"items": [
        "expense:notanumber", "leave:", "banana", f"expense:{fresh['id']}",
        "expense:999999",
    ]}, follow_redirects=True)
    conn = db()
    ok = conn.execute("SELECT status FROM expenses WHERE id = ?", (fresh["id"],)).fetchone()
    conn.close()
    s.check("the page still works", r2.status_code == 200, r2)
    s.check("and the one real item was approved anyway", ok["status"] == "approved")

    s.section("Only the owner may approve")
    third = _pending_expense(75.0)
    ec.post("/admin/approvals/bulk", data={"items": [f"expense:{third['id']}"]})
    conn = db()
    untouched = conn.execute("SELECT status FROM expenses WHERE id = ?", (third["id"],)).fetchone()
    conn.close()
    s.check("an employee's bulk approval does nothing", untouched["status"] == "pending",
            detail=f"status is {untouched['status']}")

    s.section("Regenerating the supplier link really does kill the old one")
    conn = db()
    old = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'supplier_upload_token'").fetchone()
    conn.close()
    if old and old["value"]:
        pub = m.app.test_client()
        s.check("the current link opens", pub.get(f"/supplier-invoices/submit/{old['value']}").status_code == 200)
        oc.post("/expenses/regenerate-supplier-link", follow_redirects=True)
        conn = db()
        new = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'supplier_upload_token'").fetchone()
        conn.close()
        s.check("the token actually changed", new["value"] != old["value"])
        s.check("the OLD link is now a 404 — this is the whole point",
                pub.get(f"/supplier-invoices/submit/{old['value']}").status_code == 404,
                detail="the old supplier link still works")
        s.check("and the new one opens",
                pub.get(f"/supplier-invoices/submit/{new['value']}").status_code == 200)
        s.check("a made-up token is refused",
                pub.get("/supplier-invoices/submit/not-a-real-token").status_code == 404)
    else:
        s.check("a supplier upload token exists to test", False, detail="none set")

    s.section("Recurring costs")
    r3 = oc.post("/management/recurring-costs/new", data={
        "label": f"{TAG} Broadband", "amount": "45.50", "frequency": "monthly",
        "category": "utilities", "next_due_date": (datetime.now(timezone.utc).date()
                                                   + timedelta(days=20)).isoformat(),
    }, follow_redirects=True)
    conn = db()
    cost = conn.execute(
        "SELECT * FROM recurring_costs WHERE label LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    s.check("a recurring cost is created", cost is not None, r3)

    if cost:
        s.check("with the amount it was given",
                abs((cost["amount"] or 0) - 45.50) < 0.01, detail=f"got {cost['amount']}")
        was_active = cost["active"]
        oc.post(f"/management/recurring-costs/{cost['id']}/toggle-active", follow_redirects=True)
        conn = db()
        toggled = conn.execute(
            "SELECT active FROM recurring_costs WHERE id = ?", (cost["id"],)).fetchone()
        conn.close()
        # A toggle that silently does nothing leaves the owner reading a
        # monthly total that includes a cost they switched off.
        s.check("toggling actually flips it", toggled["active"] != was_active,
                detail=f"{was_active} -> {toggled['active']}")

        oc.post(f"/management/recurring-costs/{cost['id']}/edit", data={
            "label": f"{TAG} Broadband", "amount": "60.00", "frequency": "monthly",
            "category": "utilities", "next_due_date": cost["next_due_date"] or "",
        }, follow_redirects=True)
        conn = db()
        edited = conn.execute(
            "SELECT amount FROM recurring_costs WHERE id = ?", (cost["id"],)).fetchone()
        conn.close()
        s.check("editing the amount sticks", abs((edited["amount"] or 0) - 60.00) < 0.01,
                detail=f"got {edited['amount']}")

        s.check("an employee cannot edit a recurring cost",
                ec.post(f"/management/recurring-costs/{cost['id']}/edit",
                        data={"label": "x", "amount": "1", "frequency": "monthly"}
                        ).status_code in (302, 403))

    _cleanup()
    return s
