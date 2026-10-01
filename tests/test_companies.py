# -*- coding: utf-8 -*-
"""The companies: one house, several legal entities.

Company info was one row, held there by CHECK (id = 1). The house is now an
SCI that owns the building, an SASU that runs the hospitality, and companies
in Australia and Andorra, with more coming. One row could not say which of
them a guest's invoice is issued by -- and that is not a matter of taste: an
invoice naming the company that owns the walls, rather than the one that sold
the stay, is an invoice from the wrong taxpayer.

WHAT IS CHECKED, and why:

  THE MOVE CHANGES NOTHING PRINTED. Whatever the single record named becomes
  the company that issues guest documents and employs the staff, once, and
  a second start does not make a second copy.

  EXACTLY ONE ISSUER, EXACTLY ONE EMPLOYER. Ticking either on one company
  takes it off the company that had it. Two issuers would make every invoice
  a coin toss over which row SQLite returned first.

  THE DOCUMENTS ASK BY ROLE. The receipt and the emailed statement print the
  invoicing company, form, capital and RCS included, whichever order the
  companies were added in.

  A MISTYPED FRENCH NUMBER IS REFUSED, and the form comes back as typed. A
  SIREN and SIRET carry a check digit and the TVA key is computed from the
  SIREN, so one wrong digit is detectable -- and otherwise it is printed on
  every invoice until somebody's accountant notices.

  THE HOUSE FORM CANNOT BLANK A COMPANY. Saving the accountant used to write
  every legal column from the same form.
"""
import sqlite3

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m

SASU = {
    "legal_name": "Château de Gudanes (test)", "legal_form": "SASU", "country": "FR",
    "role": "Runs the hospitality", "registration_number": "106 121 783 00018",
    "registration_office": "RCS Foix", "vat_number": "FR12106121783",
    "share_capital": "500 €",
    "registered_address": "Château de Gudanes, Gudanes, 09310 Château-Verdun",
    "directors": "Karina Waters", "issues_guest_documents": "1", "active": "1",
}


def _company(conn, name):
    return conn.execute("SELECT * FROM companies WHERE legal_name = ?", (name,)).fetchone()


def run():
    s = Suite("The companies: several, and each document names the right one")
    oc, ec, _owner, _emp = clients()

    s.section("The single record moves across once, and nothing printed changes")
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("""CREATE TABLE company_info (id INTEGER PRIMARY KEY, legal_name TEXT,
                   registration_number TEXT, vat_number TEXT, registered_address TEXT,
                   incorporation_date TEXT)""")
    # The real table's own DDL, so this cannot drift from what init_db builds.
    real = db()
    try:
        create = real.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='companies'"
        ).fetchone()["sql"]
    finally:
        real.close()
    mem.execute(create)
    m.seed_companies_from_company_info(mem)
    s.check("an empty record seeds nothing",
            mem.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0)
    mem.execute("""INSERT INTO company_info (id, legal_name, registration_number,
                   vat_number, registered_address) VALUES
                   (1, 'SCI Torrents', '81234567800019', NULL, '2 Route de Beille')""")
    m.seed_companies_from_company_info(mem)
    m.seed_companies_from_company_info(mem)
    rows = mem.execute("SELECT * FROM companies").fetchall()
    s.check("the old record becomes one company, however often the app starts",
            len(rows) == 1, detail=f"{len(rows)} rows")
    s.check("and it is still the one invoices and staff paperwork name",
            rows and rows[0]["issues_guest_documents"] == 1 and rows[0]["is_employer"] == 1)
    s.check("with its numbers carried over",
            rows and rows[0]["registration_number"] == "81234567800019"
            and rows[0]["registered_address"] == "2 Route de Beille")
    try:
        mem.execute("INSERT INTO companies (legal_name, issues_guest_documents) VALUES ('Two', 1)")
        two_issuers = True
    except sqlite3.IntegrityError:
        two_issuers = False
    s.check("the database itself refuses a second invoicing company", not two_issuers)
    mem.close()

    s.section("French numbers are checked as they are typed")
    s.check("the SASU's own numbers pass",
            m.company_number_problems("FR", "106 121 783 00018", "FR12106121783") == [],
            detail=str(m.company_number_problems("FR", "106 121 783 00018", "FR12106121783")))
    s.check("a SIREN alone is accepted",
            m.company_number_problems("FR", "106121783", "") == [])
    s.check("one digit wrong in the SIRET is caught",
            m.company_number_problems("FR", "106 121 783 00019", "") != [])
    s.check("a TVA key that does not fit its SIREN is caught",
            m.company_number_problems("FR", "", "FR13106121783") != [])
    s.check("a TVA number for a different SIREN than the SIRET is caught",
            any("different SIREN" in p for p in
                m.company_number_problems("FR", "10612178300018", "FR40800123456")))
    s.check("an Australian ABN is stored as given",
            m.company_number_problems("AU", "51 824 753 556", "") == [])

    s.section("Adding the SASU makes it the invoicing company, and only it")
    conn = db()
    before = conn.execute(
        "SELECT id FROM companies WHERE issues_guest_documents = 1").fetchone()
    conn.close()
    r = oc.post("/management/companies/new", data=SASU, follow_redirects=True)
    s.check("it saves", r.status_code == 200 and "added" in " ".join(flashes(r)),
            r, detail=str(flashes(r)))
    conn = db()
    sasu = _company(conn, SASU["legal_name"])
    issuers = conn.execute(
        "SELECT id FROM companies WHERE issues_guest_documents = 1").fetchall()
    s.check("it is the invoicing company", sasu and sasu["issues_guest_documents"] == 1)
    s.check("and the company that was is not any more",
            len(issuers) == 1,
            detail=f"{len(issuers)} invoicing companies; was {before['id'] if before else None}")
    s.check("operating_company() answers with it",
            (m.operating_company(conn) or {}).get("legal_name") == SASU["legal_name"])
    block = m.company_block_text(conn)
    s.check("the emailed statement names it",
            SASU["legal_name"] in block, detail=block)
    s.check("with the form, capital and RCS a French invoice owes",
            "SASU au capital de 500 € · RCS Foix 106 121 783" in block, detail=block)
    conn.close()

    s.section("The SCI owns the building and employs the staff")
    sci = {"legal_name": "SCI Torrents (test)", "legal_form": "SCI", "country": "FR",
           "role": "Owns the building", "is_employer": "1", "active": "1"}
    r = oc.post("/management/companies/new", data=sci, follow_redirects=True)
    conn = db()
    s.check("it saves with only a name and a role", _company(conn, sci["legal_name"]) is not None,
            r, detail=str(flashes(r)))
    s.check("staff paperwork names it",
            (m.employer_company(conn) or {}).get("legal_name") == sci["legal_name"])
    s.check("while guest invoices still name the SASU",
            (m.operating_company(conn) or {}).get("legal_name") == SASU["legal_name"])
    s.check("and the certificat's employer reads the SCI",
            m._employer_identity(conn)["name"] == sci["legal_name"])
    conn.close()

    s.section("Andorra and Australia are asked for their own numbers")
    r = oc.post("/management/companies/new", data={
        "legal_name": "Gudanes Andorra (test)", "country": "AD", "active": "1",
        "registration_number": "L-123456-X"}, follow_redirects=True)
    conn = db()
    ad = _company(conn, "Gudanes Andorra (test)")
    conn.close()
    page = oc.get(f"/management/companies/{ad['id']}").get_data(as_text=True) if ad else ""
    s.check("the Andorran company's page asks for an NRT, not a SIRET",
            ">NRT<" in page.replace("\n", "") and "IGI number" in page)
    r = oc.post("/management/companies/new", data={
        "legal_name": "Gudanes Pty Ltd (test)", "country": "AU", "active": "1"},
        follow_redirects=True)
    listing = oc.get("/management/company-info").get_data(as_text=True)
    s.check("the list shows every company",
            all(n.replace("'", "&#39;") in listing or n in listing for n in (
                SASU["legal_name"], sci["legal_name"], "Gudanes Andorra (test)",
                "Gudanes Pty Ltd (test)")))
    s.check("and says which one invoices are issued by", "Guest invoices" in listing)
    s.check("and names what is still to fill in, rather than a percentage",
            "Still to fill in:" in listing)
    found = oc.get("/management/company-info?q=Andorra").get_data(as_text=True)
    s.check("search finds a company by name",
            "Gudanes Andorra (test)" in found and SASU["legal_name"] not in found)

    s.section("A mistyped number is refused, and the form keeps what was typed")
    bad = dict(SASU, legal_name="Typo SASU (test)", registration_number="106 121 783 00019",
               issues_guest_documents="")
    r = oc.post("/management/companies/new", data=bad)
    body = r.get_data(as_text=True)
    conn = db()
    s.check("it is refused", r.status_code == 400, r)
    s.check("nothing is saved", _company(conn, "Typo SASU (test)") is None)
    s.check("and the form comes back filled in", "Typo SASU (test)" in body
            and "106 121 783 00019" in body)
    s.check("saying what is wrong", "check digit" in body)
    conn.close()

    s.section("A closed company cannot be named on anything")
    r = oc.post("/management/companies/new", data={
        "legal_name": "Closed and invoicing (test)", "issues_guest_documents": "1"})
    s.check("a closed company cannot issue guest invoices", r.status_code == 400, r)
    conn = db()
    s.check("and the SASU is still the invoicing company",
            (m.operating_company(conn) or {}).get("legal_name") == SASU["legal_name"])
    conn.close()

    s.section("Deleting")
    conn = db()
    sasu_id = _company(conn, SASU["legal_name"])["id"]
    au_id = _company(conn, "Gudanes Pty Ltd (test)")["id"]
    conn.close()
    r = oc.post(f"/management/companies/{sasu_id}/delete", follow_redirects=True)
    conn = db()
    s.check("the invoicing company cannot be deleted",
            _company(conn, SASU["legal_name"]) is not None, r, detail=str(flashes(r)))
    s.check("and says what to do instead",
            any("Mark another company" in f for f in flashes(r)), detail=str(flashes(r)))
    conn.close()
    r = oc.post(f"/management/companies/{au_id}/delete", follow_redirects=True)
    conn = db()
    s.check("a company entered by mistake can be",
            _company(conn, "Gudanes Pty Ltd (test)") is None, r)
    conn.close()
    s.check("an unknown company is a 404",
            oc.get("/management/companies/999999").status_code == 404)

    s.section("The house's own form cannot blank a company")
    r = oc.post("/management/company-info", data={
        "accountant_name": "Cabinet test", "house_guest_capacity": "20"},
        follow_redirects=True)
    conn = db()
    still = _company(conn, SASU["legal_name"])
    s.check("saving the accountant leaves the SASU's numbers alone",
            still and still["vat_number"] == "FR12106121783", r)
    s.check("and the accountant is saved",
            conn.execute("SELECT accountant_name FROM company_info WHERE id = 1"
                         ).fetchone()["accountant_name"] == "Cabinet test")
    conn.close()

    s.section("Readiness says when nobody issues guest documents")
    conn = db()
    def issuer_check():
        return next(c for c in m.readiness_checks(conn, include_slow=False)
                    if c["label"] == "Which company issues guest documents")
    s.check("with the SASU marked, it passes", issuer_check()["ok"])
    conn.execute("UPDATE companies SET issues_guest_documents = 0")
    s.check("with none marked, it fails and says where to fix it",
            not issuer_check()["ok"] and "Company info" in issuer_check()["detail"])
    conn.execute("UPDATE companies SET issues_guest_documents = 1 WHERE id = ?", (sasu_id,))
    conn.commit()
    conn.close()

    s.section("Only the owner")
    s.check("staff cannot open a company",
            ec.get(f"/management/companies/{sasu_id}").status_code in (302, 403))
    s.check("or add one",
            ec.post("/management/companies/new", data=SASU).status_code in (302, 403))

    return s
