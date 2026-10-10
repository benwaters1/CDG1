# -*- coding: utf-8 -*-
"""The booking terms, which are the other document a guest legally agrees to.

The privacy notice already has a suite, and for a reason written down in
CLAUDE.md: it is a set of testable claims about this code rather than
marketing copy. The terms are the same kind of document and had nothing
guarding them at all — which showed, when a version carrying
"Last updated: [add a date once this is finalized]" sat on the live page for
two days before anybody opened it.

They are also DATA, not code. They live in app_settings and are edited from
/admin/terms, so nothing about changing them appears in a diff, a commit or
a review. That is the same shape as the email templates that twice went out
reading "TEST SUBJECT {guest_name}".

WHAT IS CHECKED, and why each one:

  NO UNFINISHED PLACEHOLDER. A square bracket on a page somebody is agreeing
  to is the cheapest possible tell that nobody has read it, and it is the
  one thing a lawyer's eye would catch in a second and an app never would.

  A DATE. A reader cannot tell whether what they agreed to in March is what
  is on the page in October without one.

  AND THE PAYMENT WINDOW MUST MATCH THE CODE. This is the real one. The
  terms promise a workshop balance is due a stated number of days before the
  stay; compute_workshop_payment_terms charges on WORKSHOP_BALANCE_DAYS.
  Change the constant and the terms become a promise the software breaks —
  silently, because the page still renders and the charge still goes
  through, just not when the guest was told. Nothing else in the app
  connects those two numbers.
"""
import html
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m


def run():
    s = Suite("The booking terms, which are a set of claims")
    oc, ec, _owner, _emp = clients()

    # Read as a stranger. The terms are a public page and the version that
    # matters is the one somebody with no session sees.
    anon = m.app.test_client()
    r = anon.get("/terms")
    s.check("the page is served", r.status_code == 200,
            detail=str(r.status_code))
    body = r.get_data(as_text=True)
    flat = " ".join(body.split())

    s.section("Nobody left it half-written")
    # The DOCUMENT, read from where it is stored, not the HTML around it.
    # Searching the rendered page matched the stylesheet's own attribute
    # selectors -- [name="csrf-token"], [data-year] -- which live in a
    # <script> block and are nobody's unfinished sentence.
    conn = db()
    try:
        doc = conn.execute(
            "SELECT value FROM app_settings WHERE key='terms_and_conditions'"
        ).fetchone()["value"]
    finally:
        conn.close()
    # Anchored on the SUBSTANCE, not the first line. The document opens with
    # its own title — "BOOKING TERMS & CONDITIONS — …" — and the page
    # deliberately drops that block because it only repeats the h1 above it.
    # Keyed on the title, this check called a correct page broken.
    #
    # So: a clause from the middle, and one from the end, compared as text
    # rather than markup because the page escapes ampersands and accents.
    body_text = html.unescape(flat)
    sample = [l.strip() for l in doc.splitlines()
              if len(l.strip()) > 45 and not l.strip().isupper()]
    missing = [l[:45] for l in (sample[:1] + sample[-1:])
               if l[:45] not in body_text]
    s.check("the stored document is what the page shows",
            not missing,
            detail="%s — on the setting and not on the page, so the page is "
                   "rendering something other than what is stored" % missing)
    # Square brackets around a word, which is how every unfinished note in
    # this document has been written. Not a bare "[", which appears in
    # ordinary prose.
    holes = re.findall(r"\[[a-z][^\]]{3,60}\]", doc, re.I)
    s.check("no unfinished placeholder on a page people agree to",
            not holes,
            detail="%s — a square bracket here is the cheapest possible tell "
                   "that nobody has read it" % holes[:3])

    s.check("it carries a date, so a reader can tell whether it is current",
            re.search(r"Last updated:\s*\S", flat) is not None,
            detail="without one, somebody who agreed in March cannot tell "
                   "whether these are the same terms")

    s.section("The payment window is the one the app actually charges on")
    # The claim and the code, compared. Nothing else in the app connects
    # these two numbers, so a change to the constant turns the terms into a
    # promise the software breaks -- silently, because the page still renders
    # and the charge still goes through, just not when the guest was told.
    stated = re.search(r"balance is due\s+(\d+)\s+days before", flat, re.I)
    s.check("the terms state a workshop payment window",
            stated is not None,
            detail="the page no longer says when a balance falls due")
    s.check("and it is the window the code charges on",
            stated and int(stated.group(1)) == m.WORKSHOP_BALANCE_DAYS,
            detail="terms say %s, WORKSHOP_BALANCE_DAYS is %d"
                   % (stated.group(1) if stated else "nothing",
                      m.WORKSHOP_BALANCE_DAYS))

    s.section("A fresh install would start with the same terms")
    # DEFAULT_TERMS is what init_db seeds when app_settings has no row — a
    # new deployment, or the Railway volume starting empty. It is in git;
    # the live text is in the database and is not. So the two drift silently
    # and in the one direction that matters: the live page can be corrected
    # and the SEED left behind, and then the day the house actually goes
    # live it starts from the old wording.
    #
    # That is exactly what happened, twice. Seven clauses went into the page
    # on 29 September and into the seed not at all; then on 1 October the
    # page was replaced outright -- Chateau de Gudanes SASU as the company,
    # not SCI Torrents -- and the seed was "corrected" to the 29 September
    # wording, because the copy of the database on the owner's machine was
    # read as the live one. It is not: the live database is on Railway, and
    # nothing in this repository can see it. These clauses are read off the
    # live page itself.
    missing = [c for c in ("Your booking is with Château de Gudanes, a société",
                           "RCS of Foix under number 106 121 783",
                           "balance is due 30",
                           "Assistance and guide dogs are welcome",
                           "Transfers and excursions are included only where",
                           "2 Route de Beille",
                           # L616-1 wants the mediator named. Generic until the
                           # owner picks one (reminder set for 8 October 2026).
                           "may be referred to a consumer mediator",
                           "Last updated: 1 October 2026")
               if c not in m.DEFAULT_TERMS]
    s.check("the seeded default carries the same clauses as the live page",
            not missing,
            detail="%s — in the live terms and not in DEFAULT_TERMS, so a "
                   "fresh deployment would serve the old wording" % missing)
    s.check("and names the SASU, not SCI Torrents, as who a guest contracts with",
            "SCI Torrents" not in m.DEFAULT_TERMS,
            detail="the SCI owns the building; bookings are with the SASU")
    s.check("and mentions insurance no more than the live page does",
            ("insurance" in m.DEFAULT_TERMS.lower())
            == ("insurance" in doc.lower()),
            detail="seed:%s live:%s"
                   % ("insurance" in m.DEFAULT_TERMS.lower(),
                      "insurance" in doc.lower()))

    s.section("It is laid out as a document, not dumped as one block")
    # The stored terms are plain text, hard-wrapped at about seventy-two
    # characters because that is what a textarea gives you. Put straight into
    # a <p> the newlines collapsed and the whole thing rendered as one
    # unbroken wall — every word present, so every check above still passed,
    # and nobody would ever have read it. Only looking at the page found it.
    s.check("the numbered sections are headings on the page",
            body.count("<h2") >= 8,
            detail="%d headings — the document has %d numbered sections"
                   % (body.count("<h2"),
                      len([b for b in m.terms_blocks(doc) if b[0] == "heading"])))
    s.check("and the bulleted clauses are a list",
            body.count("<li") >= 10,
            detail="%d list items" % body.count("<li"))
    s.check("no clause is left carrying the textarea's own line wrapping",
            "white-space:pre-line" not in body.replace(" ", ""),
            detail="pre-line keeps the 72-character wraps and breaks every "
                   "line mid-sentence in a narrow column")

    s.section("It is reachable from where somebody agrees to it")
    # A document nobody can open is not a document anybody agreed to.
    found = []
    for path in ("/book", "/workshops", "/"):
        page = anon.get(path)
        if page.status_code == 200 and "/terms" in page.get_data(as_text=True):
            found.append(path)
    s.check("at least one public page links to it", found,
            detail="checked /book, /workshops and the home page")

    s.check("and anybody may read it without logging in",
            anon.get("/terms").status_code == 200)

    s.section("The readiness page reads both copies at once")
    # The checks above compare DEFAULT_TERMS against clauses written into
    # this file. That is the best the suite can do — it never touches the
    # network, so from a laptop it has no live site to ask — and it goes
    # stale the next time somebody edits the terms on the deployment.
    #
    # The deployed app has both halves in one process: the row it is serving
    # and the constant it was built with. So the reading that matters is
    # taken there, on /admin/readiness, and this only checks that it exists
    # and that it is not permanently crying wolf.
    # Its own connection: the one above is closed as soon as the document
    # has been read, and readiness_checks does its own queries.
    rconn = db()
    # The two made the same here, as the owner's textarea would save them --
    # CRLF -- rather than relying on the copied database happening to match
    # the code. It stopped matching the day the shipped terms moved arrival
    # to 2pm, which is the readiness check doing its job on the live site
    # and no reason for this one to go red.
    rconn.execute("UPDATE app_settings SET value = ? WHERE key = 'terms_and_conditions'",
                  (m.DEFAULT_TERMS.replace("\n", "\r\n"),))
    rconn.commit()
    rows = [r for r in m.readiness_checks(rconn, include_slow=False)
            if r["label"] == "Terms, in the code and on the site"]
    s.check("the readiness page carries the comparison", len(rows) == 1,
            detail="%d check(s) named that" % len(rows))
    s.check("and it passes when the two say the same thing",
            rows and rows[0]["ok"],
            detail=(rows[0]["detail"][:110] if rows else "")
                   + " — the database row is CRLF because it is typed into a "
                     "textarea and the constant is LF because it is Python, "
                     "so a raw comparison reports drift every day forever and "
                     "the check becomes furniture")

    # And it has to be able to go off, or it is decoration. Changed here in
    # the throwaway copy, never on anything the house serves.
    rconn.execute("UPDATE app_settings SET value = ? "
                  "WHERE key = 'terms_and_conditions'",
                  (m.DEFAULT_TERMS.replace("\n", "\r\n")
                   + "\r\n\r\nA clause nobody put in the code.",))
    rconn.commit()
    moved = [r for r in m.readiness_checks(rconn, include_slow=False)
             if r["label"] == "Terms, in the code and on the site"]
    s.check("and fails the moment the live wording moves",
            moved and not moved[0]["ok"],
            detail="a page edited on the deployment leaves the repository "
                   "describing a contract the site no longer offers")
    rconn.execute("UPDATE app_settings SET value = ? "
                  "WHERE key = 'terms_and_conditions'", (doc,))
    rconn.commit()
    rconn.close()

    return s
