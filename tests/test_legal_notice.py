# -*- coding: utf-8 -*-
"""The legal notice: who publishes the site and who hosts it.

French law (LCEN, art. 6-III) asks it of every business website, and the
1 October handover drew the page. What makes it worth a suite is where its
facts come from. The publisher is the company that issues guest invoices --
the same row the bill prints -- so the name, form, capital and numbers are
kept once, under Management, Company info, and a change there reaches the
invoice and this page together.

WHAT IS CHECKED, and why:

  IT IS PUBLIC, AND REACHABLE. A legal notice somebody has to log in to see,
  or cannot find from the footer, is not one.

  THE PUBLISHER IS THE INVOICING COMPANY, not a sentence typed into the page:
  change the company and the page follows.

  NOTHING IS PRINTED BLANK. A row with nothing on file is left off rather
  than shown empty or with a placeholder -- in particular the consumer
  mediator, which must never be named before the house has joined one.

  THE HOST IS NAMED IN FULL: name, address and telephone, which is what the
  article asks for, not a city.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m

NAME = "Legal Notice Test SASU"


def run():
    s = Suite("The legal notice, read from the invoicing company")
    oc, ec, _owner, _emp = clients()
    anon = m.app.test_client()

    conn = db()
    before = conn.execute(
        "SELECT id FROM companies WHERE issues_guest_documents = 1").fetchone()
    conn.execute("UPDATE companies SET issues_guest_documents = 0")
    conn.execute(
        """INSERT INTO companies (legal_name, legal_form, country, share_capital,
               registration_number, registration_office, vat_number,
               registered_address, issues_guest_documents, active)
           VALUES (?, 'SASU', 'FR', '500 €', '106 121 783 00018', 'RCS Foix',
                   'FR12106121783', 'Château de Gudanes, Gudanes, 09310 Château-Verdun',
                   1, 1)""", (NAME,))
    conn.commit()
    conn.close()

    try:
        s.section("Anybody can read it, and find it")
        r = anon.get("/legal")
        s.check("it answers without a login", r.status_code == 200, r)
        page = r.get_data(as_text=True)
        home = anon.get("/").get_data(as_text=True)
        s.check("the footer links to it", 'href="/legal"' in home,
                detail="a notice nobody can find is not one")
        # Until launch EVERY page is noindex (SITE_IS_LIVE), so the question is
        # asked of the site as it will be, the way test_noindex_meta asks it.
        was_live = m.SITE_IS_LIVE
        try:
            m.SITE_IS_LIVE = True
            live_head = anon.get("/legal").get_data(as_text=True).split("</head>")[0]
        finally:
            m.SITE_IS_LIVE = was_live
        s.check("and once the site is live it is not hidden from search engines",
                "noindex" not in live_head,
                detail="unlike a guest's own pages, this one is meant to be found")

        s.section("The publisher is the company that issues guest invoices")
        s.check("named", NAME in page)
        s.check("with its form and capital",
                "SASU" in page and "share capital of 500 €" in page)
        s.check("its SIRET and VAT number",
                "106 121 783 00018" in page and "FR12106121783" in page)
        s.check("and its trade-register entry",
                "RCS Foix 106 121 783" in page,
                detail="LCEN asks for the RCS number, not only the SIRET")
        s.check("and its registered office",
                "Gudanes, 09310 Château-Verdun" in page)

        s.section("Nothing is printed blank")
        s.check("no director of publication row while none is on file",
                "Director of publication" not in page)
        s.check("and no mediator while the house has not joined one",
                "Consumer mediation" not in page,
                detail="naming a mediator the house has not joined is a "
                       "false statement on a legal page")

        s.section("Filled in from the company form, it appears")
        conn = db()
        cid = conn.execute("SELECT id FROM companies WHERE legal_name = ?",
                           (NAME,)).fetchone()["id"]
        conn.close()
        form = {"legal_name": NAME, "legal_form": "SASU", "country": "FR",
                "share_capital": "500 €", "registration_number": "106 121 783 00018",
                "registration_office": "RCS Foix", "vat_number": "FR12106121783",
                "registered_address": "Château de Gudanes, Gudanes, 09310 Château-Verdun",
                "publication_director": "Karina Waters",
                "consumer_mediator": "Test Mediator, 1 rue Test, Paris",
                "issues_guest_documents": "1", "active": "1"}
        r = oc.post(f"/management/companies/{cid}", data=form, follow_redirects=True)
        page = anon.get("/legal").get_data(as_text=True)
        s.check("the company form saves the director of publication",
                "Director of publication" in page and "Karina Waters" in page, r)
        s.check("and the mediator, once there is one",
                "Test Mediator, 1 rue Test, Paris" in page)

        s.section("The host is named in full")
        s.check("Railway, by its legal name", "Railway Corporation" in page)
        s.check("with its postal address",
                "548 Market St PMB 68956, San Francisco, California 94104" in page)
        s.check("and a telephone number", "415 707 7675" in page,
                detail="LCEN asks for the host's telephone, not only a website")
    finally:
        conn = db()
        conn.execute("DELETE FROM companies WHERE legal_name = ?", (NAME,))
        if before:
            conn.execute("UPDATE companies SET issues_guest_documents = 1 WHERE id = ?",
                         (before["id"],))
        conn.commit()
        conn.close()

    return s
