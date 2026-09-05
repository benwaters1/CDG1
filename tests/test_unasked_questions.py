"""Two lists of choices, handed to every template, offered on no form.

`EXPENSE_DOC_TYPES` and `TRANSFER_TYPES` are both defined as dictionaries,
both injected into every page through the template globals, and both used by
nothing. Their columns exist — `expenses.doc_type` defaulting to
`'bill_to_pay'`, `vehicle_transfers.transfer_type` defaulting to `'airport'` —
and no form ever set either, so every row in the database carries the default
and the distinction each list was written to record had never once been
recorded.

WHY THE EXPENSE ONE IS A MONEY BUG, NOT A TIDINESS ONE.

`payables_ageing` is "what the house owes suppliers, and how late it is". It
takes every supplier invoice that is pending or approved with no `paid_at`.
So a photograph of a receipt for something settled at the counter sat in it as
an outstanding payable, ageing, until somebody thought to mark a `paid_at` on
a bill that had never been owed. The figure was too high — in the direction
that makes the house look worse than it is, and the fix somebody reaches for
is to pay it again.

THE DEFAULT MUST STAY `bill_to_pay`, and this suite insists on it. Every row
written before the question was asked carries that value, and treating an
unanswered question as "already paid" would silently remove real liabilities
from what the house believes it owes — the same error in the direction nobody
notices.
"""
from datetime import timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "unasked-"


def _cleanup(conn):
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicle_transfers WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Two questions the forms never asked")
    oc, ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    s.section("The choices existed and no form offered them")
    s.check("the document types are defined", len(m.EXPENSE_DOC_TYPES) == 4,
            detail=str(sorted(m.EXPENSE_DOC_TYPES)))
    s.check("and the journey types too", len(m.TRANSFER_TYPES) >= 8,
            detail=str(sorted(m.TRANSFER_TYPES)))
    form = oc.get("/expenses/submit").get_data(as_text=True)
    s.check("the expense form now asks", 'name="doc_type"' in form)
    s.check("offering every one of them",
            all(label in form for label in m.EXPENSE_DOC_TYPES.values()),
            detail=str([l for l in m.EXPENSE_DOC_TYPES.values()
                        if l not in form]))

    s.section("An unanswered question is a bill, not a receipt")
    s.check("no answer means a bill we owe",
            m.expense_doc_type(None) == "bill_to_pay")
    s.check("and so does an answer nobody recognises",
            m.expense_doc_type("something-else") == "bill_to_pay",
            detail="treating it as already paid would quietly remove real "
                   "liabilities, which is the same error in the direction "
                   "nobody notices")
    s.check("a real answer is kept",
            m.expense_doc_type("bill_paid") == "bill_paid")

    s.section("What the house owes counts bills and not receipts")

    def invoice(desc, amount, doc_type, days_overdue=10):
        conn.execute(
            """INSERT INTO expenses (kind, description, amount, status,
                       due_date, doc_type, submitted_at, vendor_name)
               VALUES ('supplier_invoice', ?, ?, 'approved', ?, ?, ?, ?)""",
            (TAG + desc, amount, (today - timedelta(days=days_overdue)).isoformat(),
             doc_type, now.isoformat(), TAG + " Supplier"))
        conn.commit()

    def payable_rows():
        """Every invoice the house believes it owes, across all four buckets.

        payables_ageing returns {buckets, totals, total}, so the lists are one
        level down -- reading .values() at the top found dicts and counted
        nothing, and the checks passed on an empty list.
        """
        return [r for v in m.payables_ageing(conn)["buckets"].values()
                for r in v]

    before_total = len(payable_rows())

    invoice("a-real-bill", 500.0, "bill_to_pay")
    invoice("already-paid", 700.0, "bill_paid")
    invoice("small-receipt", 40.0, "receipt")
    invoice("staff-claim", 60.0, "employee_reimbursement")

    listed = [r["description"] for r in payable_rows()]
    s.check("a bill we owe is on it", TAG + "a-real-bill" in listed,
            detail=str([d for d in listed if str(d).startswith(TAG)]))
    s.check("a receipt for something already paid is not",
            TAG + "already-paid" not in listed,
            detail="it was ageing in here as an outstanding payable until "
                   "somebody marked a paid_at on a bill never owed")
    s.check("nor a small purchase already made",
            TAG + "small-receipt" not in listed)
    s.check("nor a staff claim, which has its own list",
            TAG + "staff-claim" not in listed,
            detail="staff_reimbursements_owed answers that one, and counting "
                   "it twice is how two figures come to disagree")
    s.check("so exactly one of the four is a payable",
            len(payable_rows()) == before_total + 1,
            detail=f"{before_total} before, {len(payable_rows())} after — "
                   "measured as the change, because the database holds the "
                   "house's own unpaid invoices")

    s.section("An expense submitted through the form keeps its answer")
    # Through the real route. Every fixture above writes doc_type straight
    # into the table, which tests the query and not the mechanism -- and a
    # version that ignored the field entirely passed all of it, because the
    # form asked the question and the query read the answer and nothing
    # carried it from one to the other.
    oc.post("/expenses/submit",
            data={"vendor_name": TAG + " Shop",
                  "description": TAG + "posted-receipt",
                  "amount": "31.50",
                  "spent_on": today.isoformat(),
                  "doc_type": "bill_paid"},
            follow_redirects=True)
    row = conn.execute(
        "SELECT kind, doc_type FROM expenses WHERE description = ?",
        (TAG + "posted-receipt",)).fetchone()
    s.check("the submitted expense exists", row is not None,
            detail="if the post failed the check below proves nothing")
    s.check("and it kept the answer the form gave",
            row and row["doc_type"] == "bill_paid",
            detail=f"{dict(row) if row else None} — asked, filtered on, and "
                   "never carried between the two")

    s.section("And a row written before the question was asked is still a bill")
    conn.execute(
        """INSERT INTO expenses (kind, description, amount, status, due_date,
                   doc_type, submitted_at, vendor_name)
           VALUES ('supplier_invoice', ?, 900.0, 'approved', ?, NULL, ?, ?)""",
        (TAG + "legacy-row", (today - timedelta(days=30)).isoformat(),
         now.isoformat(), TAG + " Supplier"))
    conn.commit()
    listed = [r["description"] for r in payable_rows()]
    s.check("a NULL doc_type still counts as money owed",
            TAG + "legacy-row" in listed,
            detail="every row in the database predates the question; a "
                   "COALESCE that dropped them would take real liabilities "
                   "off the figure on the day this shipped")

    s.section("The journey type is asked for and kept")
    vehicle = conn.execute("SELECT id FROM vehicles LIMIT 1").fetchone()
    if not vehicle:
        conn.execute("INSERT INTO vehicles (name, license_plate) VALUES (?, ?)",
                     (TAG + " Van", TAG + "REG"))
        conn.commit()
        vehicle = conn.execute("SELECT id FROM vehicles WHERE license_plate = ?",
                               (TAG + "REG",)).fetchone()
    page = oc.get(f"/management/vehicles/{vehicle['id']}/transfers")
    s.check("the transfers page offers the journey types",
            'name="transfer_type"' in page.get_data(as_text=True),
            detail=str(page.status_code))
    s.check("with every one of them",
            all(label in page.get_data(as_text=True)
                for label in m.TRANSFER_TYPES.values()),
            detail=str([l for l in m.TRANSFER_TYPES.values()
                        if l not in page.get_data(as_text=True)]))

    r = oc.post(f"/management/vehicles/{vehicle['id']}/transfers/new",
                data={"guest_name": TAG + " Rider", "direction": "pickup",
                      "scheduled_at": (now + timedelta(days=1)).strftime(
                          "%Y-%m-%dT%H:%M"),
                      "transfer_type": "ski", "notes": ""},
                follow_redirects=True)
    saved = conn.execute(
        "SELECT transfer_type FROM vehicle_transfers WHERE guest_name = ?",
        (TAG + " Rider",)).fetchone()
    s.check("a ski transfer is recorded as one",
            saved and saved["transfer_type"] == "ski",
            detail=f"{dict(saved) if saved else None} — eight kinds of "
                   "journey were defined and every run the house had ever "
                   "made was filed as an airport transfer")

    r = oc.post(f"/management/vehicles/{vehicle['id']}/transfers/new",
                data={"guest_name": TAG + " Nonsense", "direction": "pickup",
                      "scheduled_at": (now + timedelta(days=1)).strftime(
                          "%Y-%m-%dT%H:%M"),
                      "transfer_type": "not-a-real-type", "notes": ""},
                follow_redirects=True)
    saved = conn.execute(
        "SELECT transfer_type FROM vehicle_transfers WHERE guest_name = ?",
        (TAG + " Nonsense",)).fetchone()
    s.check("and a value nobody offered falls back to the column's default",
            saved and saved["transfer_type"] == "airport",
            detail=str(dict(saved) if saved else None))

    s.section("And the third question on the same page")
    # ct_note sits beside ct_expires_on. The date is asked for, saved and
    # warned about; the note was asked for by nothing -- and it is the half
    # that matters between tests, because the advisories are what fail the
    # next one.
    page = oc.get("/management/vehicles").get_data(as_text=True)
    s.check("the vehicle form asks what the test said",
            'name="ct_note"' in page)
    was = conn.execute("SELECT * FROM vehicles WHERE id = ?",
                       (vehicle["id"],)).fetchone()
    oc.post(f"/management/vehicles/{vehicle['id']}/edit",
            data={"name": was["name"] or "Van",
                  "ct_expires_on": was["ct_expires_on"] or "",
                  "ct_note": TAG + " nearside sill starting to go",
                  "odometer_km": was["odometer_km"] or ""},
            follow_redirects=True)
    after_save = conn.execute("SELECT ct_note FROM vehicles WHERE id = ?",
                              (vehicle["id"],)).fetchone()
    s.check("and keeps the answer",
            after_save and (after_save["ct_note"] or "").startswith(TAG),
            detail=str(dict(after_save) if after_save else None))
    s.check("which then shows on the page",
            "nearside sill starting to go"
            in oc.get("/management/vehicles").get_data(as_text=True),
            detail="a note nobody can read back is the same as no note")
    conn.execute("UPDATE vehicles SET ct_note = ? WHERE id = ?",
                 (was["ct_note"], vehicle["id"]))
    conn.commit()

    conn.execute("DELETE FROM vehicles WHERE license_plate = ?", (TAG + "REG",))
    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
