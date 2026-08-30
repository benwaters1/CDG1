"""Opening the receipt attached to an expense.

Two routes serve the file: one downloads it, one shows it in the browser. Both
used to decide who may look with

    if user["role"] != "owner" and row["submitted_by_user_id"] != user["id"]

which is a different question from the one the pages linking to it ask, and it
got both ends wrong.

THE END THAT MATTERS: the financial preset grants `expenses`, `admin_approvals`,
`decide_expense`, `delete_expense` and `export_expenses_csv`. So the person the
château trusts to approve spending, delete a claim and export the lot was the
one person who could not open the receipt - every View and Download button on
the page they were working from returned 403, while the page itself rendered
perfectly. Approving spend without being able to see what was spent is the
failure; a broken-looking link is only how you notice.

THE OTHER END: an owner given a NARROW preset kept sight of every receipt in
the house, because a role test never consults the preset. That is the direction
that actually leaks, and it is checked here too.

The fix is a helper rather than @owner_required on the routes, because neither
endpoint is in ENDPOINT_AREA and an unmapped endpoint is owner-only by design -
which would have taken my_expenses away from every member of staff. The last
section is there to keep that true.
"""
import os
from datetime import datetime, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZEXPF"
PNG = b"\x89PNG\r\n\x1a\n" + b"zz-not-a-real-png"
TXT = b"zz receipt text"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE 'zzexpf.%'")
    conn.commit()
    conn.close()
    for name in (TAG + "-receipt.png", TAG + "-receipt.txt"):
        try:
            os.remove(os.path.join(m.UPLOAD_DIR, name))
        except OSError:
            pass


def _person(slug, role, preset):
    """A member of staff holding a named access preset."""
    from werkzeug.security import generate_password_hash
    email = f"zzexpf.{slug}@example.invalid"
    conn = db()
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           access_preset, created_at) VALUES (?, ?, ?, ?, 'Test', 'active', ?, ?)""",
        (email, generate_password_hash("zz"), role, f"{TAG} {slug}", preset,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    client = m.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = row["id"]
    return client, row


def _expense(ref, submitter_id, filename):
    conn = db()
    conn.execute(
        """INSERT INTO expenses (kind, submitted_by_user_id, vendor_name, description,
           amount, filename, status, submitted_at)
           VALUES ('staff_expense', ?, 'ZZ Supplier', ?, 12.5, ?, 'pending', ?)""",
        (submitter_id, f"{TAG} {ref}", filename, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM expenses WHERE description = ?",
                       (f"{TAG} {ref}",)).fetchone()
    conn.close()
    return row


def _areas(slug):
    conn = db()
    try:
        row = conn.execute("SELECT areas, is_full_access FROM access_presets WHERE slug = ?",
                           (slug,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    if row["is_full_access"] or (row["areas"] or "").strip() == "*":
        return {"*"}
    return {a.strip() for a in (row["areas"] or "").split(",") if a.strip()}


def run():
    s = Suite("Expense receipts")
    _cleanup()
    oc, ec, owner, emp = clients()

    os.makedirs(m.UPLOAD_DIR, exist_ok=True)
    png_name, txt_name = TAG + "-receipt.png", TAG + "-receipt.txt"
    with open(os.path.join(m.UPLOAD_DIR, png_name), "wb") as fh:
        fh.write(PNG)
    with open(os.path.join(m.UPLOAD_DIR, txt_name), "wb") as fh:
        fh.write(TXT)

    # Somebody else's claim, so "it is mine" can never be what lets a client in.
    theirs = _expense("someone elses receipt", owner["id"], png_name)
    theirs_txt = _expense("someone elses note", owner["id"], txt_name)
    mine = _expense("my own receipt", emp["id"], png_name)
    no_file = _expense("nothing attached", owner["id"], None)

    dl = lambda e: f"/expenses/{e['id']}/file"
    vw = lambda e: f"/expenses/{e['id']}/view"

    s.section("The preset this rests on really does grant the money pages")
    # Stated rather than assumed: if the manager preset stopped covering
    # financial, every check below would pass for the wrong reason.
    manager = _areas("manager")
    s.check("the manager preset exists", manager is not None,
            detail="no manager preset — the rest of this suite proves nothing")
    s.check("and it covers financial",
            bool(manager) and ("*" in manager or "financial" in manager),
            detail=f"{manager}")
    narrow = _areas("karina")
    s.check("and there is a preset that does not", bool(narrow) and "financial" not in narrow,
            detail=f"{narrow}")

    book_c, book = _person("bookkeeper", "employee", "manager")
    narrow_c, _narrow_user = _person("narrowowner", "owner", "karina")

    s.section("Whoever may approve the spending may see what was spent")
    # The financial preset grants decide_expense and delete_expense. Being
    # trusted to sign a claim off and refused sight of its receipt is the bug.
    s.check("the bookkeeper can open the expenses page",
            book_c.get("/expenses").status_code == 200,
            detail=f"HTTP {book_c.get('/expenses').status_code}")
    s.check("and the approvals queue they would decide it from",
            book_c.get("/admin/approvals").status_code == 200,
            detail=f"HTTP {book_c.get('/admin/approvals').status_code}")
    r = book_c.get(dl(theirs))
    s.check("they can download a receipt somebody else submitted",
            r.status_code == 200,
            detail=f"HTTP {r.status_code} — approving spend without being able "
                   "to look at it")
    s.check("and it is the file, not a page about it", r.data == PNG,
            detail=f"{r.data[:24]!r}")
    s.check("they can view it in the browser too",
            book_c.get(vw(theirs)).status_code == 200,
            detail=f"HTTP {book_c.get(vw(theirs)).status_code}")

    s.section("A preset that does not cover money does not open receipts")
    # The leaking direction. This account's ROLE is owner; only the preset
    # says no, and the old test never consulted it.
    s.check("an owner on a narrow preset is refused",
            narrow_c.get(dl(theirs)).status_code == 403,
            detail=f"HTTP {narrow_c.get(dl(theirs)).status_code} — the preset was "
                   "ignored because the role said owner")
    s.check("on the viewer as well",
            narrow_c.get(vw(theirs)).status_code == 403,
            detail=f"HTTP {narrow_c.get(vw(theirs)).status_code}")

    s.section("Your own claim is still your own")
    s.check("an employee with no preset can open their own receipt",
            ec.get(dl(mine)).status_code == 200,
            detail=f"HTTP {ec.get(dl(mine)).status_code} — my_expenses would be "
                   "a page of links that all fail")
    s.check("and view it", ec.get(vw(mine)).status_code == 200,
            detail=f"HTTP {ec.get(vw(mine)).status_code}")
    s.check("but not somebody else's", ec.get(dl(theirs)).status_code == 403,
            detail=f"HTTP {ec.get(dl(theirs)).status_code}")
    s.check("nor view somebody else's", ec.get(vw(theirs)).status_code == 403,
            detail=f"HTTP {ec.get(vw(theirs)).status_code}")

    s.section("A full owner is unaffected")
    s.check("the owner can still download any receipt",
            oc.get(dl(theirs)).status_code == 200,
            detail=f"HTTP {oc.get(dl(theirs)).status_code}")
    s.check("and view it", oc.get(vw(theirs)).status_code == 200)

    s.section("The two routes differ on purpose")
    # Only types a browser renders are served inline. A .txt or .docx shown
    # inline is served from the app's own origin, so the split is not cosmetic.
    r = oc.get(vw(theirs_txt))
    s.check("a text file is not shown inline", r.status_code == 404,
            detail=f"HTTP {r.status_code} — served from the app's own origin")
    r = oc.get(dl(theirs_txt))
    s.check("but it can still be downloaded", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("as an attachment",
            "attachment" in (r.headers.get("Content-Disposition") or ""),
            detail=f"{r.headers.get('Content-Disposition')!r}")
    r = oc.get(vw(theirs))
    s.check("while the viewer does not force a download",
            "attachment" not in (r.headers.get("Content-Disposition") or ""),
            detail=f"{r.headers.get('Content-Disposition')!r}")

    s.section("Nothing to open")
    s.check("an expense with no file is a 404",
            oc.get(dl(no_file)).status_code == 404,
            detail=f"HTTP {oc.get(dl(no_file)).status_code}")
    s.check("and so is one that does not exist",
            oc.get("/expenses/999999/file").status_code == 404)
    s.check("on the viewer too", oc.get("/expenses/999999/view").status_code == 404)

    s.section("Logged out gets nothing")
    anon = m.app.test_client()
    for label, url in (("download", dl(theirs)), ("view", vw(theirs))):
        r = anon.get(url)
        s.check(f"a stranger cannot {label} a receipt",
                r.status_code in (302, 401, 403) and r.data != PNG,
                detail=f"HTTP {r.status_code}")

    s.section("The routes stay off the area map")
    # The helper exists because these endpoints are unmapped, and an unmapped
    # endpoint is owner-only. Mapping them or decorating them with
    # @owner_required would take my_expenses from every member of staff, and
    # the check above would go on passing for the owner while it did.
    for name in ("download_expense_file", "view_expense_file"):
        s.check(f"{name} is not in an access area",
                m.ENDPOINT_AREA.get(name) is None,
                detail=f"mapped to {m.ENDPOINT_AREA.get(name)!r} — staff lose "
                       "their own receipts unless the helper is revisited")

    _cleanup()
    return s
