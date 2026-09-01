"""The company's own paperwork: documents, suppliers, insurance, exports.

Fifteen routes with no check between them. They are grouped because the thing
that matters about all of them is the same: they hold the house's own records
rather than a guest's, so nothing here is noticed when it goes wrong. A vendor
row with a blank name, a policy whose renewal date quietly cleared, a document
somebody could fetch without an account — none of those show up on a page
anybody looks at daily.

The document routes get the most attention because they are the ones that
serve a file off disk. Three things are worth pinning:

  - what may be uploaded, since `view` serves inline. The upload list has no
    svg or html in it and the viewable list is images and pdf, so an inline
    file cannot carry script into the app's own origin. That is true today by
    the contents of two sets, and a set is an easy thing to widen without
    thinking about who serves what.
  - that a filename cannot escape the upload directory
  - that two people uploading `contract.pdf` do not overwrite each other

The CSV exports are checked for the formula-injection guard, because a report
is opened in Excel by definition and half its columns come from names guests
typed themselves.
"""
import os
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZTREC"


def _cleanup():
    conn = db()
    rows = conn.execute("SELECT filename FROM company_documents WHERE title LIKE ?",
                        (TAG + "%",)).fetchall()
    for r in rows:
        path = os.path.join(m.UPLOAD_DIR, r["filename"] or "")
        if r["filename"] and os.path.exists(path):
            os.remove(path)
    conn.execute("DELETE FROM company_documents WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM insurance_policies WHERE provider LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _upload(client, title, filename, content=b"a real enough file", extra=None):
    data = {"title": title, "document": (BytesIO(content), filename)}
    data.update(extra or {})
    return client.post("/management/documents/upload", data=data,
                       content_type="multipart/form-data", follow_redirects=True)


def _doc(title):
    conn = db()
    try:
        return conn.execute("SELECT * FROM company_documents WHERE title = ?",
                            (TAG + title,)).fetchone()
    finally:
        conn.close()


def _one(table, column, value):
    conn = db()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (value,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Company records")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    s.section("Uploading a document")
    r = _upload(oc, TAG + "Lease", "lease.pdf", b"%PDF-1.4 lease")
    doc = _doc("Lease")
    s.check("it is recorded", doc is not None, detail=str(flashes(r)))
    s.check("and the file is on disk",
            doc and os.path.exists(os.path.join(m.UPLOAD_DIR, doc["filename"])))
    s.check("stored under a name of the app's choosing, not the guest's",
            doc and doc["filename"] != "lease.pdf" and doc["filename"].endswith("lease.pdf"),
            detail=str(doc["filename"]) if doc else "")
    s.check("with who uploaded it", doc and doc["uploaded_by_user_id"] == owner["id"])

    s.section("Two people uploading the same filename")
    # A stored name taken straight from the upload would silently overwrite the
    # first contract with the second, and both rows would then point at one file.
    _upload(oc, TAG + "Lease two", "lease.pdf", b"%PDF-1.4 a different lease")
    second = _doc("Lease two")
    s.check("the second does not take the first's place",
            second and second["filename"] != doc["filename"],
            detail=f"{doc['filename']} vs {second['filename'] if second else None}")
    s.check("and both files still exist",
            all(os.path.exists(os.path.join(m.UPLOAD_DIR, d["filename"]))
                for d in (doc, second) if d))

    s.section("What may be uploaded at all")
    for bad in ("payload.svg", "note.html", "run.exe", "sheet.xlsx"):
        _upload(oc, TAG + "Bad " + bad, bad, b"<svg onload=alert(1)>")
        s.check(f"{bad} is refused", _doc("Bad " + bad) is None)
    # The pairing that keeps inline serving safe. Stated as a check because it
    # is two sets in different parts of the file, and widening either without
    # the other is how a viewable script type gets in.
    s.check("nothing that can carry script is uploadable",
            not (m.ALLOWED_EXTENSIONS & {"svg", "html", "htm", "xml", "js"}),
            detail=str(sorted(m.ALLOWED_EXTENSIONS)))
    s.check("and nothing servable inline can either",
            not (m.VIEWABLE_EXTENSIONS - m.ALLOWED_EXTENSIONS)
            and not (m.VIEWABLE_EXTENSIONS & {"svg", "html", "htm"}),
            detail=str(sorted(m.VIEWABLE_EXTENSIONS)))

    s.section("A filename cannot climb out of the upload directory")
    _upload(oc, TAG + "Traverse", "../../../../etc/passwd.pdf", b"%PDF-1.4")
    trav = _doc("Traverse")
    s.check("the path separators are gone",
            trav and "/" not in trav["filename"] and "\\" not in trav["filename"]
            and ".." not in trav["filename"],
            detail=str(trav["filename"]) if trav else "not stored at all")
    s.check("and it landed inside the upload directory",
            trav and os.path.exists(os.path.join(m.UPLOAD_DIR, trav["filename"])))

    s.section("Fetching one back")
    r = oc.get(f"/management/documents/{doc['id']}/download")
    s.check("the owner can download it", r.status_code == 200, detail=str(r.status_code))
    s.check("as an attachment", "attachment" in r.headers.get("Content-Disposition", ""),
            detail=r.headers.get("Content-Disposition"))
    r = oc.get(f"/management/documents/{doc['id']}/view")
    s.check("a pdf can be viewed inline", r.status_code == 200, detail=str(r.status_code))

    # A type that is allowed on the way in but not renderable must not be
    # served inline -- the browser would sniff it, and a .txt rendered in the
    # app's origin is the cheapest version of the same problem.
    _upload(oc, TAG + "Notes", "notes.txt", b"just text")
    txt = _doc("Notes")
    s.check("a .txt is accepted", txt is not None)
    s.check("but is not served inline",
            oc.get(f"/management/documents/{txt['id']}/view").status_code == 404,
            detail=str(oc.get(f"/management/documents/{txt['id']}/view").status_code))
    s.check("while downloading it still works",
            oc.get(f"/management/documents/{txt['id']}/download").status_code == 200)

    s.section("Who can fetch the company's paperwork")
    for label, client in (("an employee", ec), ("somebody with no account", anon)):
        for verb in ("download", "view"):
            r = client.get(f"/management/documents/{doc['id']}/{verb}")
            s.check(f"{label} cannot {verb} it", r.status_code in (302, 401, 403),
                    detail=str(r.status_code))
    s.check("a document that does not exist is a 404",
            oc.get("/management/documents/99999999/download").status_code == 404)

    s.section("Editing and removing one")
    oc.post(f"/management/documents/{doc['id']}/edit",
            data={"title": TAG + "Lease renewed", "expiry_date": "2027-01-31"},
            follow_redirects=True)
    edited = _one("company_documents", "id", doc["id"])
    s.check("the title changes", edited["title"] == TAG + "Lease renewed",
            detail=str(edited["title"]))
    s.check("and the renewal date is kept", edited["expiry_date"] == "2027-01-31",
            detail=str(edited["expiry_date"]))
    s.check("an employee cannot edit it",
            ec.post(f"/management/documents/{doc['id']}/edit",
                    data={"title": "hijacked"}).status_code in (302, 403))
    s.check("and the title is untouched by the attempt",
            _one("company_documents", "id", doc["id"])["title"] == TAG + "Lease renewed")

    gone_path = os.path.join(m.UPLOAD_DIR, second["filename"])
    oc.post(f"/management/documents/{second['id']}/delete", follow_redirects=True)
    s.check("deleting removes the row", _doc("Lease two") is None)
    s.check("and the file with it, not just the row",
            not os.path.exists(gone_path),
            detail="an orphaned file nobody can reach is still the document")

    s.section("Suppliers")
    oc.post("/management/vendors/new", data={
        "name": TAG + " Ariège Électricité", "contact_person": "Mme Roux",
        "phone": "+33 5 61 00 00 00", "email": "compta@example.invalid",
        "payment_terms": "30 days end of month", "notes": "meter readings quarterly",
    }, follow_redirects=True)
    v = _one("vendors", "name", TAG + " Ariège Électricité")
    s.check("a supplier is added", v is not None)
    s.check("with the terms, which is the point of keeping them",
            v and v["payment_terms"] == "30 days end of month",
            detail=str(v["payment_terms"]) if v else "")

    r = oc.post("/management/vendors/new", data={"name": "  "}, follow_redirects=True)
    s.check("one with no name is refused",
            any("name" in f.lower() for f in flashes(r)), detail=str(flashes(r)))

    oc.post(f"/management/vendors/{v['id']}/edit", data={
        "name": TAG + " Ariège Électricité", "contact_person": "M. Fabre",
        "phone": "", "email": "", "payment_terms": "", "notes": "",
    }, follow_redirects=True)
    after = _one("vendors", "id", v["id"])
    s.check("editing changes what was sent", after["contact_person"] == "M. Fabre",
            detail=str(after["contact_person"]))
    s.check("and clearing a field really clears it", not (after["phone"] or ""),
            detail=f"{after['phone']!r} — a blank that means 'leave it' hides a "
                   "number somebody deliberately removed")
    s.check("an employee cannot add a supplier",
            ec.post("/management/vendors/new",
                    data={"name": TAG + " Rogue"}).status_code in (302, 403))
    s.check("and none was added", _one("vendors", "name", TAG + " Rogue") is None)

    s.section("Insurance")
    conn = db()
    conn.execute(
        """INSERT INTO insurance_policies (provider, policy_number, coverage_type,
           premium, premium_frequency, expiry_date, created_at)
           VALUES (?, 'POL-1', 'buildings', 4200, 'annual', ?, ?)""",
        (TAG + " Assureur", (house_today() + timedelta(days=40)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    pol = _one("insurance_policies", "provider", TAG + " Assureur")
    new_expiry = (house_today() + timedelta(days=400)).isoformat()
    oc.post(f"/management/insurance/{pol['id']}/edit", data={
        "provider": TAG + " Assureur", "policy_number": "POL-2",
        "coverage_type": "buildings and contents", "premium": "4800",
        "premium_frequency": "annual", "expiry_date": new_expiry, "notes": "renewed",
    }, follow_redirects=True)
    after = _one("insurance_policies", "id", pol["id"])
    s.check("the renewal date moves", after["expiry_date"] == new_expiry,
            detail=str(after["expiry_date"]))
    s.check("and the premium with it", float(after["premium"]) == 4800.0,
            detail=str(after["premium"]))
    r = oc.post(f"/management/insurance/{pol['id']}/edit",
                data={"provider": "", "policy_number": "POL-3"}, follow_redirects=True)
    s.check("a policy with no provider is refused",
            any("provider" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and the real one is untouched",
            _one("insurance_policies", "id", pol["id"])["policy_number"] == "POL-2")
    s.check("an employee cannot edit a policy",
            ec.post(f"/management/insurance/{pol['id']}/edit",
                    data={"provider": "Rogue"}).status_code in (302, 403))

    s.section("The report exports")
    for slug in sorted(m.REPORT_BUILDERS):
        r = oc.get(f"/admin/reports/{slug}/export.csv?period=month")
        s.check(f"the {slug} report exports",
                r.status_code == 200
                and "text/csv" in r.headers.get("Content-Type", ""),
                detail=f"{r.status_code} {r.headers.get('Content-Type')}")
    s.check("a report that does not exist is a 404",
            oc.get("/admin/reports/not-a-report/export.csv").status_code == 404)
    s.check("an employee cannot export one",
            ec.get("/admin/reports/financial/export.csv").status_code in (302, 403))

    # A report is opened in a spreadsheet by definition, and half of what is in
    # it was typed into a public form by somebody the house has never met.
    conn = db()
    room = conn.execute("SELECT id FROM rooms LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           payment_status, total_price, created_at)
           VALUES (?, ?, ?, '=cmd|calc', 'formula@example.invalid', ?, ?, 2,
           'confirmed', 'paid', 100, ?)""",
        (room, TAG + "FORMULA", TAG + "ftok",
         house_today().isoformat(), (house_today() + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    body = oc.get("/admin/bookings/export.csv").get_data(as_text=True)
    s.check("a name that looks like a formula is neutralised in the CSV",
            "=cmd|calc" not in body or "'=cmd|calc" in body,
            detail="a guest name is enough to run something when the owner "
                   "opens the export")
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code = ?", (TAG + "FORMULA",))
    conn.commit()
    conn.close()

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
