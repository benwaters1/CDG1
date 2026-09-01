"""What the house owes the people who work here.

The supplier invoice's sibling, on the other side of the same table, and thin
in the same two ways.

  - A CLAIM RECORDED WHEN IT WAS FILED, never when the money was spent. Staff
    submit in batches weeks later, so a March purchase handed in during April
    went into the books as April. Exactly the fault the supplier invoice had.
  - "PAID" WAS A WORD IN A STATUS COLUMN. Nothing recorded when somebody was
    actually reimbursed, how, or by whom — so "when did we pay Marie back for
    the eighty euros" had no answer anywhere in the app, and "still owed" and
    "approved a while ago" were the same query. That is money owed to a real
    person who works here.
  - And approving it wrote NO AUDIT LINE. Putting somebody on a dinner service
    has been on the record since the beginning; agreeing to pay them money was
    not.

The date of spend is asked for and not required, deliberately. A receipt with
no legible date is a real thing and refusing the claim over it would send
somebody away with their own money still spent.
"""
import io
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTCLM"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE action LIKE 'expense_%' AND details LIKE '%.%' "
                 "AND target LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _claim(client, **fields):
    data = {"description": TAG + " a claim", "amount": "80.00", "csrf_token": "x"}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    data["receipt"] = (io.BytesIO(b"a receipt\n"), "receipt.txt")
    return client.post("/expenses/submit", data=data,
                       content_type="multipart/form-data", follow_redirects=True)


def run():
    s = Suite("What we owe our own people")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    s.section("A claim knows when the money was spent")
    spent = (today - timedelta(days=21)).isoformat()
    r = _claim(ec, description=TAG + " paint for the shutters", spent_on=spent)
    claim = _one("SELECT * FROM expenses WHERE description = ?",
                 (TAG + " paint for the shutters",))
    s.check("the claim is recorded", claim is not None, detail=str(flashes(r)))
    s.check("carrying the date it was spent", claim and claim["spent_on"] == spent,
            detail=str(claim["spent_on"]) if claim else "")
    s.check("which is not the date it was filed",
            claim and claim["spent_on"] != (claim["submitted_at"] or "")[:10],
            detail="three weeks apart in this fixture, and a whole month apart "
                   "when somebody hands in a batch")
    s.check("and the date on the paper is what the books use",
            claim and m.expense_document_date(claim) == spent,
            detail=str(m.expense_document_date(claim)) if claim else "")

    r = _claim(ec, description=TAG + " from next month",
               spent_on=(today + timedelta(days=30)).isoformat())
    s.check("a date in the future is refused",
            any("future" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and nothing was stored for it",
            _one("SELECT COUNT(*) AS c FROM expenses WHERE description = ?",
                 (TAG + " from next month",))["c"] == 0)

    # Not required. A receipt whose date has faded is a real thing, and
    # refusing the claim over it sends somebody away with their own money
    # still spent.
    r = _claim(ec, description=TAG + " no date on the receipt", spent_on="")
    undated = _one("SELECT * FROM expenses WHERE description = ?",
                   (TAG + " no date on the receipt",))
    s.check("a claim with no date is still accepted", undated is not None,
            detail=str(flashes(r)))
    # The day it was handed in HERE. Handed over at 00:30 in the Ariege, the
    # stamp reads yesterday in UTC, and the claim would be dated the day before
    # the person walked in with it.
    s.check("and falls back to when it was handed in, rather than to nothing",
            undated and m.expense_document_date(undated) == m.house_date_iso(undated["submitted_at"]),
            detail=str(m.expense_document_date(undated)) if undated else "")

    s.section("Approving it is on the record")
    before = _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'expense_approved'")["c"]
    oc.post(f"/expenses/{claim['id']}/decide", data={"status": "approved"},
            follow_redirects=True)
    s.check("the claim is approved",
            _one("SELECT status FROM expenses WHERE id = ?", (claim["id"],))["status"] == "approved")
    s.check("and agreeing to pay somebody is written down",
            _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'expense_approved'")["c"] > before,
            detail="putting somebody on a dinner service has been audited since "
                   "the beginning; agreeing to pay them was not")

    s.section("The accountant is told when it was spent")
    # Fixing the date for supplier invoices left staff claims still dated by
    # the day they were handed in -- and expense_document_date, written for
    # both, was called by nothing. A March receipt filed in April went to the
    # accountant as April.
    import inspect
    src = inspect.getsource(m.send_to_pennylane)
    s.check("the send uses the date on the paper, whichever paper it is",
            "expense_document_date" in src,
            detail="reading invoice_date alone dates every staff claim by "
                   "its upload")
    conn = db()
    claim_row = conn.execute("SELECT * FROM expenses WHERE id = ?", (claim["id"],)).fetchone()
    conn.close()
    s.check("and for this claim that is the date of spend",
            m.expense_document_date(claim_row) == spent,
            detail=f"{m.expense_document_date(claim_row)}, filed "
                   f"{(claim_row['submitted_at'] or '')[:10]}")

    s.section("What is still owed, and for how long")
    conn = db()
    with m.app.test_request_context():
        owed = m.staff_reimbursements_owed(conn, today)
    conn.close()
    mine = next((p for p in owed["people"] if p["name"] == emp["name"]), None)
    s.check("the approved claim is owed to them", mine is not None,
            detail=str([p["name"] for p in owed["people"]])[:110])
    s.check("for the amount claimed", mine and mine["total"] >= 80.0,
            detail=str(mine["total"]) if mine else "")
    s.check("and the total is the sum of the people",
            abs(sum(p["total"] for p in owed["people"]) - owed["total"]) < 0.01,
            detail=f"{owed['total']}")

    s.section("Paying them back is an event, not a word")
    r = oc.post(f"/expenses/{claim['id']}/decide",
                data={"status": "paid", "paid_reference": "Bank transfer 4471"},
                follow_redirects=True)
    paid = _one("SELECT * FROM expenses WHERE id = ?", (claim["id"],))
    s.check("the status changes", paid["status"] == "paid")
    s.check("and it records WHEN", bool(paid["paid_at"]),
            detail="a status saying paid cannot answer when")
    s.check("and BY WHOM", paid["paid_by_user_id"] == owner["id"],
            detail=str(paid["paid_by_user_id"]))
    s.check("and HOW", paid["paid_reference"] == "Bank transfer 4471",
            detail=str(paid["paid_reference"]))
    s.check("the payment is on the record too",
            _one("SELECT COUNT(*) AS c FROM audit_log WHERE action = 'expense_paid'")["c"] > 0)

    conn = db()
    with m.app.test_request_context():
        after = m.staff_reimbursements_owed(conn, today)
    conn.close()
    still = next((p for p in after["people"] if p["name"] == emp["name"]), None)
    s.check("and it stops being owed",
            still is None or still["total"] < (mine["total"] if mine else 0),
            detail=str(still["total"]) if still else "nothing owed")

    s.section("A claim that was paid does not become owed again")
    # staff_reimbursements_owed filters on BOTH status = 'approved' and
    # paid_at IS NULL, which looks like belt and braces because the status
    # already excludes a paid claim. They mean different things in one real
    # case: somebody clicks Approve on a claim that has already been paid,
    # which puts the status back and would otherwise make the money owed for
    # a second time. paid_at is the record that it went out.
    oc.post(f"/expenses/{claim['id']}/decide", data={"status": "approved"},
            follow_redirects=True)
    conn = db()
    with m.app.test_request_context():
        reopened = m.staff_reimbursements_owed(conn, today)
    conn.close()
    again = next((p for p in reopened["people"] if p["name"] == emp["name"]), None)
    s.check("re-approving something already paid does not owe it twice",
            again is None or claim["id"] not in [i["id"] for i in again["items"]],
            detail="status alone would say this is owed; the record of the "
                   "payment is what says it is not")
    s.check("and the payment record survived the re-approval",
            _one("SELECT paid_at FROM expenses WHERE id = ?", (claim["id"],))["paid_at"],
            detail="losing it would make the money owed again on the next look")

    s.section("Somebody kept waiting is visible")
    conn = db()
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    conn.execute(
        """INSERT INTO expenses (kind, submitted_by_user_id, description, amount,
           status, spent_on, submitted_at, decided_at)
           VALUES ('staff_expense', ?, ?, 45.0, 'approved', ?, ?, ?)""",
        (emp["id"], TAG + " waiting since last month",
         (today - timedelta(days=45)).isoformat(), long_ago, long_ago))
    conn.commit()
    with m.app.test_request_context():
        waiting = m.staff_reimbursements_owed(conn, today)
    conn.close()
    who = next((p for p in waiting["people"] if p["name"] == emp["name"]), None)
    s.check("they are owed it", who is not None)
    s.check("and it says how long they have been waiting",
            who and who["waiting_days"] and who["waiting_days"] >= 35,
            detail=str(who["waiting_days"]) if who else "")

    r = oc.get("/expenses")
    body = r.get_data(as_text=True)
    s.check("the page says what is owed to our own people",
            "our own people" in body.lower(), detail="a figure nobody can see "
                                                     "is not a figure")
    s.check("and how long somebody has been waiting", "waiting" in body.lower())

    s.section("Who can decide")
    conn = db()
    conn.execute(
        """INSERT INTO expenses (kind, submitted_by_user_id, description, amount,
           status, submitted_at) VALUES ('staff_expense', ?, ?, 12.0, 'pending', ?)""",
        (emp["id"], TAG + " employee decides their own",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    own = conn.execute("SELECT id FROM expenses WHERE description = ?",
                       (TAG + " employee decides their own",)).fetchone()["id"]
    conn.close()
    r = ec.post(f"/expenses/{own}/decide", data={"status": "approved"},
                follow_redirects=False)
    s.check("an employee cannot approve a claim", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("not even their own",
            _one("SELECT status FROM expenses WHERE id = ?", (own,))["status"] == "pending",
            detail="the redirect alone would pass even if the row had changed")
    r = ec.post(f"/expenses/{own}/decide", data={"status": "paid",
                                                 "paid_reference": "cash from the till"},
                follow_redirects=False)
    s.check("nor mark themselves reimbursed",
            _one("SELECT paid_at FROM expenses WHERE id = ?", (own,))["paid_at"] is None,
            detail="this is the one that would take money")

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
