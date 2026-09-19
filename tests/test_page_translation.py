"""The guest site in French and Spanish, done to the response.

WHAT WENT WRONG, AND WHY NOTHING REPORTED IT.

t() translates a string a template asked to have translated, and the guest
site almost never asked. 531 words across 55 public pages sat inside t() and
22,812 did not. So the switcher changed the nav, the footer and the booking
widget, and every sentence of the restoration story, the rooms and the
workshops stayed in English — a guest reading "L'histoire de la restauration"
in the menu got "There is no electricity, no heating and no water" in the
page.

test_translations reported fr at 100%. It was measuring whether every string
the templates ASK for has an entry, and the templates ask for almost nothing,
so the tables really were full. The number was right and answered a question
nobody had. Its funnel check asserted `lang="fr"` was in the HTML and that one
heading was French, and both of those are true of a page that is otherwise
entirely English.

WHAT IS ASSERTED HERE.

Mostly that the substitution is incapable of damage. It runs on every public
response, so a bug in it is a broken page for every guest in every language —
which is a worse failure than the one it fixes. The parser only LOCATES text;
every byte outside a text node is spliced back untouched. The first check is
that a page with nothing to translate comes back identical, byte for byte,
because that is the property the whole design rests on.
"""
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m

# A page with prose, markup, entities and a script on it.
PAGE = "/restoration"


def run():
    s = Suite("The site in three languages")
    anon = m.app.test_client()
    en = anon.get(PAGE)
    body = en.get_data(as_text=True)

    s.section("It cannot damage a page it has nothing to say about")
    was = m.translations.TABLES["fr"]
    m.translations.TABLES["fr"] = {}
    try:
        s.check("a page with an empty table comes back byte for byte",
                m.translate_page(body, "fr") == body,
                detail="this runs on every public response; if the round trip "
                       "is not exact the site is broken in every language")
        s.check("and so does one in a language we do not have",
                m.translate_page(body, "de") == body)
        s.check("and English is never touched at all",
                m.translate_page(body, "en") == body,
                detail="English is the source, not a translation of itself")
    finally:
        m.translations.TABLES["fr"] = was

    s.section("It replaces the words and nothing else")
    # A real paragraph off the real page, so this cannot pass against markup
    # invented to suit it.
    para = None
    for _st, _e, text in m.page_text_spans(body):
        key = m.translation_key(text)
        if len(re.findall(r"[A-Za-z]{3,}", key)) > 12:
            para = key
            break
    s.check("there is prose on the page to work with", para is not None)
    said = "CECI EST LA PHRASE TRADUITE, et rien d'autre ne doit bouger."
    # ONE ENTRY AND NOTHING ELSE, so the size check below measures this
    # substitution rather than this one plus whichever of the other 544 table
    # entries happen to appear on the page.
    was_fr = m.translations.TABLES["fr"]
    m.translations.TABLES["fr"] = {para: said}
    try:
        out = m.translate_page(body, "fr")
        s.check("the translation is on the page", said in out)
        s.check("and the English it replaced is gone", para not in out)
        # The markup is what must survive: same tags, same count, same order.
        s.check("every tag survives unchanged",
                re.findall(r"<[^>]+>", out) == re.findall(r"<[^>]+>", body),
                detail="the parser locates text; it must never rebuild markup")
        hits = body.count(para)
        s.check("and the page moves by exactly the words that changed",
                len(out) - len(body) == hits * (len(said) - len(para)),
                detail="a different delta means something outside the span moved")
    finally:
        m.translations.TABLES["fr"] = was_fr

    s.section("What it must never translate")
    # Script and style are the dangerous ones: a "translation" inside either
    # is a syntax error served to every guest.
    probe = ('<body class="g"><script>var greeting = "Open today";</script>'
             '<style>/* Open today */</style>'
             '<textarea>Open today</textarea>'
             '<p>Open today</p></body>')
    m.translations.TABLES["fr"]["Open today"] = "Ouvert aujourd'hui"
    m.translations.TABLES["fr"]['var greeting = "Open today";'] = "CASSE"
    try:
        out = m.translate_page(probe, "fr")
        s.check("the paragraph is translated", "<p>Ouvert aujourd'hui</p>" in out)
        s.check("the script is left alone", 'var greeting = "Open today";' in out,
                detail="a translation inside a script is a syntax error")
        s.check("the stylesheet too", "/* Open today */" in out)
        s.check("and a textarea, which holds somebody's unsaved words",
                "<textarea>Open today</textarea>" in out)
    finally:
        for k in ("Open today", 'var greeting = "Open today";'):
            m.translations.TABLES["fr"].pop(k, None)

    s.section("Entities and layout survive the round trip")
    ent = '<body class="g"><p>Rooms &amp; Rates</p><p>A &lt;tag&gt; here</p></body>'
    was_fr = m.translations.TABLES["fr"]
    m.translations.TABLES["fr"] = {"Rooms & Rates": "Chambres & tarifs"}
    try:
        out = m.translate_page(ent, "fr")
        s.check("an entity in the source is matched decoded",
                "Chambres" in out,
                detail="the key is 'Rooms & Rates', not 'Rooms &amp; Rates'")
        # THE WHOLE DOCUMENT, not a substring of it. `"Chambres &amp; tarifs"
        # in out` passes while four stray characters sit beside it, which is
        # exactly what happens if a span's end is computed from the DECODED
        # length: "Rooms &amp; Rates" is seventeen characters of source and
        # thirteen of text, so the splice leaves the difference behind.
        s.check("and the document is exactly what it should be",
                out == '<body class="g"><p>Chambres &amp; tarifs</p>'
                       '<p>A &lt;tag&gt; here</p></body>',
                detail=repr(out))
    finally:
        m.translations.TABLES["fr"] = was_fr

    s.section("The spacing around the words is the page's, and is kept")
    # A sentence with a link in the middle arrives as three text nodes, and the
    # spaces at their edges are the gaps between the words and the link. Drop
    # them and the sentence closes up into "readmoreabout".
    spaced = ('<body class="g"><p>Read <a href="/x">more</a> about the house.</p>'
              '</body>')
    was_fr = m.translations.TABLES["fr"]
    m.translations.TABLES["fr"] = {"Read": "Lire", "more": "la suite",
                                   "about the house.": "au sujet de la maison."}
    try:
        out = m.translate_page(spaced, "fr")
        s.check("the gaps between the words and the link survive",
                out == '<body class="g"><p>Lire <a href="/x">la suite</a> '
                       'au sujet de la maison.</p></body>',
                detail=repr(out))
    finally:
        m.translations.TABLES["fr"] = was_fr

    s.section("A key matches however the page happens to be laid out")
    # The same sentence is indented one way in the template, another after
    # Jinja has run, and wraps differently again when the design side reflows
    # the file. The key is whitespace-normalised so none of that matters —
    # without it, every translation stops matching the next time a page is
    # redrawn, which is the failure this whole approach exists to avoid.
    ragged = ('<body class="g"><p>\n        One sentence,\n'
              '        split across three lines\n        by the template.\n'
              '      </p></body>')
    key = "One sentence, split across three lines by the template."
    was_fr = m.translations.TABLES["fr"]
    m.translations.TABLES["fr"] = {key: "Une phrase, sur trois lignes."}
    try:
        out = m.translate_page(ragged, "fr")
        s.check("a sentence broken over three indented lines is matched",
                "Une phrase, sur trois lignes." in out,
                detail="the key is normalised; the page is not")
        s.check("and the indentation around it is left where it was",
                "<p>\n        Une phrase" in out and "\n      </p>" in out,
                detail=repr(out))
    finally:
        m.translations.TABLES["fr"] = was_fr

    s.section("Through the response, as a guest actually gets it")
    fr = m.app.test_client()
    fr.get("/language/fr")
    m.translations.TABLES["fr"][para] = said
    try:
        served = fr.get(PAGE).get_data(as_text=True)
        s.check("the page a guest is served is translated", said in served)
        s.check("and it says which language it is in", 'lang="fr"' in served)
        english = anon.get(PAGE).get_data(as_text=True)
        s.check("while an English reader still gets English",
                said not in english and para in english,
                detail="the switch must not be global")
    finally:
        m.translations.TABLES["fr"].pop(para, None)

    s.section("The staff app is left in English on purpose")
    # translations.py has the reasoning: payroll, the financials and the till
    # are read by one person in one language, and a half-translated ledger is
    # worse than an English one.
    # PUT THE OWNER'S LANGUAGE BACK AFTERWARDS. set_language writes the choice
    # to the signed-in person's ROW, not to their session — deliberately, so a
    # housekeeper does not re-pick French every login. That makes it shared
    # state in a suite run: leaving the owner set to French sent seventeen
    # later checks red and crashed one, in suites that have nothing to do with
    # language, because every staff page they read came back translated.
    oc, _ec, owner, _emp = clients()
    conn = db()
    before_lang = conn.execute("SELECT language FROM users WHERE id = ?",
                               (owner["id"],)).fetchone()["language"]
    oc.get("/language/fr")
    staff = oc.get("/").get_data(as_text=True)
    s.check("a staff page is not a public page",
            'class="staff-shell"' in staff and '<body class="g' not in staff,
            detail="the body class is what the hook tests")

    # AND IT IS ACTUALLY LEFT ALONE, which the class check above does not say.
    # Take a sentence off the owner's own home page, put a translation in the
    # table for it, and require that it does NOT appear. Checking the class is
    # checking the hook's input; this checks its behaviour.
    victim = None
    for _st, _e, node in m.page_text_spans(staff):
        cand = m.translation_key(node)
        if len(re.findall(r"[A-Za-z]{3,}", cand)) >= 4:
            victim = cand
            break
    s.check("there is a sentence on the staff home to try it with",
            victim is not None)
    if victim:
        marker = "CETTE PHRASE NE DOIT PAS APPARAITRE"
        was_fr = m.translations.TABLES["fr"]
        m.translations.TABLES["fr"] = {victim: marker}
        try:
            again = oc.get("/").get_data(as_text=True)
            s.check("and the page translator does not touch it",
                    marker not in again and victim in again,
                    detail="the owner's side is payroll and the till, read by "
                           "one person in one language")
        finally:
            m.translations.TABLES["fr"] = was_fr
    conn.execute("UPDATE users SET language = ? WHERE id = ?",
                 (before_lang, owner["id"]))
    conn.commit()
    conn.close()
    s.check("and the owner is left in the language they were in",
            before_lang == db().execute(
                "SELECT language FROM users WHERE id = ?",
                (owner["id"],)).fetchone()["language"],
            detail="this is shared state; leaving it French breaks the suites "
                   "that run after this one")

    s.section("How much of the site can actually be read in each language")
    # PRINTED, NOT ASSERTED, and deliberately the number that was missing: not
    # "is the table full" but "how much of what a guest reads is translated".
    # A threshold would either block work or be set low enough to prove
    # nothing; a number every run makes the gap impossible to mistake for
    # done, which is exactly how 2% came to read as 100%.
    pages = 0
    keys = {}
    for rule in m.app.url_map.iter_rules():
        if rule.arguments or "GET" not in (rule.methods or ()):
            continue
        if rule.endpoint.startswith("static") or "webhook" in rule.endpoint:
            continue
        r = anon.get(str(rule))
        if r.status_code != 200:
            continue
        text = r.get_data(as_text=True)
        if '<body class="g' not in text:
            continue
        pages += 1
        for _st, _e, node in m.page_text_spans(text):
            key = m.translation_key(node)
            if len(re.findall(r"[A-Za-z][A-Za-z'’-]*", key)) >= 2:
                keys[key] = True
    total = max(len(keys), 1)
    for code in ("fr", "es"):
        table = m.translations.TABLES[code]
        done = sum(1 for k in keys if table.get(k) and table[k] != k)
        print("    ....  %s: %d/%d of the sentences a guest reads (%d%%) "
              "across %d public pages" % (code, done, total,
                                          100 * done // total, pages))
    s.check("the guest site has prose to translate at all", len(keys) > 200,
            detail="if this collapses, the walk above stopped finding pages "
                   "and the percentages are measuring nothing")

    return s
