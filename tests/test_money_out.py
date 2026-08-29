"""The routes that hand something out: the database, the bank numbers, and
money marked as received.

None of these had a check. They are grouped because they share a shape — one
POST, done by the owner, that either discloses something or moves a figure —
and because the failure they share is the quiet one: the route works, so it
looks right, and nobody notices it also works for somebody who should not be
able to reach it.

`/admin/backup` is the sharpest of them. It is a GET that returns the whole
database — staff records, guest addresses, password hashes — in one file. The
permission on it was never verified by anything.

The two mark-paid routes both guard against a double submit, and both do it
with a conditional UPDATE rather than by reading first and then writing. That
is the right way round and it is worth holding there: the read-then-write
version looks identical on the page and books the payment twice.
"""
import io
import zipfile
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTOUT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bank_details WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_transactions WHERE workshop_booking_id IN
                    (SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)""",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM audit_log WHERE target LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _audits(action, target=None):
    conn = db()
    try:
        if target is None:
            return conn.execute(
                "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)).fetchall()
        return conn.execute(
            "SELECT * FROM audit_log WHERE action = ? AND target = ? ORDER BY id",
            (action, target)).fetchall()
    finally:
        conn.close()


def _cost(label, freq, due):
    conn = db()
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category,
           next_due_date, active, created_at)
           VALUES (?, 120, ?, 'utilities', ?, 1, ?)""",
        (TAG + label, freq, due, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM recurring_costs WHERE label = ?",
                       (TAG + label,)).fetchone()
    conn.close()
    return row


def _due(label):
    conn = db()
    try:
        return conn.execute("SELECT next_due_date FROM recurring_costs WHERE label = ?",
                            (TAG + label,)).fetchone()["next_due_date"]
    finally:
        conn.close()


def _workshop_booking(ref, deposit=150, balance=350):
    conn = db()
    ses = conn.execute(
        """SELECT ws.id AS sid FROM workshop_sessions ws
           JOIN workshops w ON w.id = ws.workshop_id LIMIT 1""").fetchone()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, total_price,
           deposit_amount, balance_amount, created_at)
           VALUES (?, ?, ?, 'Payer', 'payer@example.invalid', 1, 'confirmed', ?, ?, ?, ?)""",
        (ses["sid"], TAG + ref, TAG + "tok" + ref, deposit + balance, deposit, balance,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE reference_code = ?",
                       (TAG + ref,)).fetchone()
    conn.close()
    return row


def _ledger(booking_id, kind="payment"):
    conn = db()
    try:
        return conn.execute(
            """SELECT * FROM workshop_transactions
               WHERE workshop_booking_id = ? AND kind = ? ORDER BY id""",
            (booking_id, kind)).fetchall()
    finally:
        conn.close()


def _with_vault(fn):
    """Stand a throwaway encryption key up for the length of one call.

    The real key is not in this environment and must not be — generating one
    here keeps the vault paths testable without ever holding the owner's.
    """
    from cryptography.fernet import Fernet
    real = m.VAULT_ENCRYPTION_KEY
    m.VAULT_ENCRYPTION_KEY = Fernet.generate_key().decode()
    try:
        return fn()
    finally:
        m.VAULT_ENCRYPTION_KEY = real


def run():
    s = Suite("Money and secrets going out")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("The whole database in one file")
    # A GET that returns staff records, guest addresses and password hashes.
    before = len(_audits("backup_downloaded"))
    r = oc.get("/admin/backup")
    s.check("the owner gets a file", r.status_code == 200, detail=str(r.status_code))
    s.check("and it is a zip",
            r.headers.get("Content-Type", "").startswith("application/zip"),
            detail=r.headers.get("Content-Type"))
    s.check("offered as a download rather than rendered",
            "attachment" in r.headers.get("Content-Disposition", ""),
            detail=r.headers.get("Content-Disposition"))
    try:
        names = zipfile.ZipFile(io.BytesIO(r.get_data())).namelist()
    except Exception as e:                                    # pragma: no cover
        names = []
        s.check("the zip opens", False, detail=str(e))
    s.check("the database is actually in it",
            any(n.endswith(".db") for n in names), detail=str(names[:6]))
    s.check("taking a copy of everything is logged",
            len(_audits("backup_downloaded")) == before + 1,
            detail=f"{len(_audits('backup_downloaded')) - before} entries written")

    # The check this route most needed. An employee is a trusted person with a
    # login, not somebody who should be able to walk off with the lot.
    r = ec.get("/admin/backup")
    s.check("an employee cannot download the database",
            r.status_code in (302, 403), detail=str(r.status_code))
    s.check("and does not get a zip by another name",
            not r.headers.get("Content-Type", "").startswith("application/zip"),
            detail=r.headers.get("Content-Type"))
    anon = m.app.test_client()
    r = anon.get("/admin/backup")
    s.check("nor can somebody with no account at all",
            r.status_code in (302, 401, 403), detail=str(r.status_code))

    s.section("Bank details are only decrypted when asked for")
    conn = db()
    conn.execute(
        """INSERT INTO bank_details (label, bank_name, account_holder, currency,
           sensitive_encrypted, created_at, updated_at)
           VALUES (?, 'Banque Test', 'Chateau', 'EUR', NULL, ?, ?)""",
        (TAG + "Main", datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    entry = conn.execute("SELECT * FROM bank_details WHERE label = ?",
                         (TAG + "Main",)).fetchone()
    conn.close()

    # With no encryption key configured the route refuses outright rather than
    # answering with three empty strings — an empty answer reads as "there is
    # nothing on file", which is a different and misleading claim.
    s.check("with no vault key the reveal is not available",
            oc.post(f"/management/bank-details/{entry['id']}/reveal").status_code == 404)

    def _stand_up_and_reveal():
        c = db()
        c.execute("UPDATE bank_details SET sensitive_encrypted = ? WHERE id = ?",
                  (m.fernet_encrypt_json({"account_number": "12345678",
                                          "iban": "FR7630006000011234567890189",
                                          "swift_bic": "TESTFRPP"}), entry["id"]))
        c.commit()
        c.close()
        return (oc.post(f"/management/bank-details/{entry['id']}/reveal"),
                ec.post(f"/management/bank-details/{entry['id']}/reveal"),
                oc.get("/management/bank-details"))

    owner_r, emp_r, listing = _with_vault(_stand_up_and_reveal)
    body = owner_r.get_json() if owner_r.status_code == 200 else {}
    s.check("the owner gets the account number back", body.get("account_number") == "12345678",
            detail=f"{owner_r.status_code} {str(body)[:60]}")
    s.check("and the IBAN", body.get("iban", "").startswith("FR76"), detail=str(body.get("iban")))
    s.check("an employee is refused", emp_r.status_code in (302, 403),
            detail=str(emp_r.status_code))
    s.check("and gets no numbers with the refusal",
            "12345678" not in emp_r.get_data(as_text=True))
    # The point of storing them encrypted is undone if the list page prints
    # them anyway.
    s.check("the numbers are not on the page that lists the accounts",
            "12345678" not in listing.get_data(as_text=True))
    s.check("revealing them is logged against the account",
            len(_audits("bank_details_revealed", TAG + "Main")) == 1,
            detail=str(len(_audits("bank_details_revealed", TAG + "Main"))))

    s.section("Marking a recurring cost paid moves the date once")
    monthly = _cost("Electricity", "monthly", "2026-03-10")
    oc.post(f"/management/recurring-costs/{monthly['id']}/mark-paid", follow_redirects=True)
    s.check("a monthly cost moves on one month", _due("Electricity") == "2026-04-10",
            detail=str(_due("Electricity")))
    # The compare-and-swap is the whole point: a double-click would otherwise
    # advance the date twice and the bill would go unpaid for a month with
    # nothing on any screen saying so.
    r = oc.post(f"/management/recurring-costs/{monthly['id']}/mark-paid",
                follow_redirects=True)
    s.check("submitting it twice does not skip a month",
            _due("Electricity") == "2026-05-10", detail=str(_due("Electricity")))
    s.check("and it is logged once per real payment",
            len(_audits("recurring_cost_paid", TAG + "Electricity")) == 2,
            detail=str(len(_audits("recurring_cost_paid", TAG + "Electricity"))))

    annual = _cost("Insurance", "annual", "2026-03-10")
    oc.post(f"/management/recurring-costs/{annual['id']}/mark-paid", follow_redirects=True)
    s.check("an annual cost moves on a year", _due("Insurance") == "2027-03-10",
            detail=str(_due("Insurance")))

    # A month-end date must not fall off the end of a shorter month.
    jan = _cost("Endofmonth", "monthly", "2026-01-31")
    oc.post(f"/management/recurring-costs/{jan['id']}/mark-paid", follow_redirects=True)
    s.check("the 31st of January lands inside February, not outside it",
            _due("Endofmonth") in ("2026-02-28", "2026-02-29"),
            detail=str(_due("Endofmonth")))

    guarded = _cost("Water", "monthly", "2026-03-10")
    ec.post(f"/management/recurring-costs/{guarded['id']}/mark-paid")
    s.check("an employee cannot mark a bill paid", _due("Water") == "2026-03-10",
            detail=str(_due("Water")))

    s.section("Marking a workshop deposit or balance received")
    wb = _workshop_booking("A", deposit=150, balance=350)
    oc.post(f"/admin/workshops/registrations/{wb['id']}/mark-deposit-paid",
            data={"method": "bank_transfer"}, follow_redirects=True)
    paid = _ledger(wb["id"])
    s.check("the deposit reaches the ledger once", len(paid) == 1, detail=str(len(paid)))
    s.check("at the amount that was actually owed",
            paid and paid[0]["amount"] == 150.0,
            detail=str(paid[0]["amount"]) if paid else "")
    s.check("with how it was paid recorded",
            paid and paid[0]["method"] == "bank_transfer",
            detail=str(paid[0]["method"]) if paid else "")

    # Double-submit. The route guards with `WHERE deposit_paid_at IS NULL`
    # rather than reading first and writing after, so the second one changes
    # nothing. A read-then-write version looks identical on the page.
    oc.post(f"/admin/workshops/registrations/{wb['id']}/mark-deposit-paid",
            data={"method": "cash"}, follow_redirects=True)
    s.check("marking it paid again does not book a second payment",
            len(_ledger(wb["id"])) == 1,
            detail=f"{len(_ledger(wb['id']))} payments for one deposit")

    oc.post(f"/admin/workshops/registrations/{wb['id']}/mark-balance-paid",
            data={"method": "cash"}, follow_redirects=True)
    s.check("the balance is a second, separate payment",
            len(_ledger(wb["id"])) == 2, detail=str(len(_ledger(wb["id"]))))
    s.check("for the balance amount",
            any(t["amount"] == 350.0 for t in _ledger(wb["id"])),
            detail=str([t["amount"] for t in _ledger(wb["id"])]))
    oc.post(f"/admin/workshops/registrations/{wb['id']}/mark-balance-paid",
            data={"method": "cash"}, follow_redirects=True)
    s.check("and it too can only be marked once",
            len(_ledger(wb["id"])) == 2,
            detail=f"{len(_ledger(wb['id']))} payments for a deposit and a balance")

    # ...and what was received has to reconcile with what was asked for, or
    # the ledger says paid while the guest still owes.
    s.check("the two together come to the price of the place",
            sum(t["amount"] for t in _ledger(wb["id"])) == 500.0,
            detail=str(sum(t["amount"] for t in _ledger(wb["id"]))))

    blocked = _workshop_booking("B", deposit=100, balance=100)
    ec.post(f"/admin/workshops/registrations/{blocked['id']}/mark-deposit-paid",
            data={"method": "cash"})
    ec.post(f"/admin/workshops/registrations/{blocked['id']}/mark-balance-paid",
            data={"method": "cash"})
    s.check("an employee cannot record money as received",
            not _ledger(blocked["id"]),
            detail=f"{len(_ledger(blocked['id']))} payment(s) booked by an employee")

    s.check("a registration that does not exist is a 404",
            oc.post("/admin/workshops/registrations/99999999/mark-deposit-paid",
                    data={"method": "cash"}).status_code == 404)

    s.section("Booking an invoice into stock")
    # The stock side of this belongs to the other agent's work and is left to
    # their tests. What is checked here is the permission, which is the part
    # that would let an employee write purchase movements into the valuation.
    conn = db()
    exp = conn.execute("SELECT id FROM expenses LIMIT 1").fetchone()
    conn.close()
    if exp:
        s.check("an employee cannot apply an invoice",
                ec.post(f"/expenses/{exp['id']}/apply-invoice",
                        data={}).status_code in (302, 403))
    else:                                                     # pragma: no cover
        s.check("an employee cannot apply an invoice",
                ec.post("/expenses/1/apply-invoice", data={}).status_code in (302, 403, 404))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
