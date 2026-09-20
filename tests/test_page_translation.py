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

    s.section("What a page could not translate is written down")
    # THE POINT OF THE WHOLE THING. The list of work is computed from what was
    # actually served, so a handover that redraws the public pages on Friday
    # puts its new sentences on this list the first time somebody reads one in
    # French. Nobody maintains it, and it cannot go stale — which a hand-typed
    # list did, inside an hour, when a handover changed "two hundred and
    # fifty years" to "two hundred and eighty" in a paragraph being translated.
    conn = db()
    conn.execute("DELETE FROM page_translations")
    conn.commit()
    guest = m.app.test_client()
    guest.get("/language/fr")
    guest.get("/book")
    wanted = conn.execute(
        "SELECT COUNT(*) c FROM page_translations WHERE lang='fr'").fetchone()["c"]
    s.check("one French page view records what it could not say",
            wanted > 50, detail="%d rows" % wanted)
    s.check("and an English reader records nothing new",
            (m.app.test_client().get("/book"),
             conn.execute("SELECT COUNT(*) c FROM page_translations"
                          ).fetchone()["c"])[1] == wanted,
            detail="English is the source; there is nothing to want")
    before = wanted
    guest.get("/book")
    s.check("and reading it twice does not write it twice",
            conn.execute("SELECT COUNT(*) c FROM page_translations WHERE lang='fr'"
                         ).fetchone()["c"] == before,
            detail="the unique key makes the second view a no-op")

    # WHAT MUST NOT BE ON THE LIST. Text that is already French is the trap:
    # t() has run by the time this sees the page, so the nav and the footer
    # are French already and look exactly like English nobody has translated.
    # The first version of this recorded "Séjourner au château" as wanting a
    # French translation.
    french_already = conn.execute(
        """SELECT COUNT(*) c FROM page_translations
           WHERE lang='fr' AND source_text IN (?, ?)""",
        ("Séjourner", "Séances photo")).fetchone()["c"]
    s.check("text that is already French is not asked for again",
            french_already == 0,
            detail="otherwise the job translates French into French")
    s.check("and neither is an address or an email",
            not m.worth_translating("ariege@chateaugudanes.com")
            and not m.worth_translating("+33 6 28 06 97 76"),
            detail="a translated address is a guest writing to nobody")
    s.check("nor a proper noun",
            not m.worth_translating("Château de Gudanes")
            and not m.worth_translating("La Table"),
            detail="asked to translate it, a model will oblige")

    s.section("The job fills them, and the page then says them")
    calls = []

    def fake_batch(lines, lang):
        calls.append((tuple(lines), lang))
        # Deliberately REVERSED, and with one line dropped. A job that pairs
        # answers to questions by position would put the wrong French under
        # every sentence and nothing would look wrong from outside.
        out = {}
        for src_line in list(lines)[::-1][:-1] or list(lines)[::-1]:
            out[src_line] = "[%s] %s" % (lang.upper(), src_line)
        return out, {}, None

    was_batch = m.translate_batch_with_claude
    was_conf = m.claude_configured
    m.translate_batch_with_claude = fake_batch
    m.claude_configured = lambda: True
    try:
        said = m.run_page_translation_job(conn, limit=30)
        s.check("the job reports what it did", "translated" in said, detail=said)
        done = conn.execute(
            """SELECT source_text, translated FROM page_translations
               WHERE status='machine' LIMIT 200""").fetchall()
        s.check("rows come back translated", len(done) > 0,
                detail="%d written" % len(done))
        s.check("and every answer is under its own question",
                all(r["translated"] == "[FR] " + r["source_text"] for r in done),
                detail="matched back by source text, never by position")
        # And it reaches a guest.
        m.translation_memory(force=True)
        page = guest.get("/book").get_data(as_text=True)
        s.check("and a guest is served them",
                any("[FR] " + r["source_text"] in page for r in done),
                detail="the memory is read on the next response")
    finally:
        m.translate_batch_with_claude = was_batch
        m.claude_configured = was_conf

    s.section("It never asks the same question twice")
    m.translate_batch_with_claude = lambda lines, lang: ({}, {l: True for l in lines}, None)
    m.claude_configured = lambda: True
    try:
        m.run_page_translation_job(conn, limit=20)
        left = conn.execute(
            "SELECT COUNT(*) c FROM page_translations WHERE status='skip'"
        ).fetchone()["c"]
        s.check("a line the model says to leave alone is filed as skip",
                left > 0, detail="%d skipped" % left)
        s.check("and skip is not wanted, so it is never sent again",
                conn.execute(
                    """SELECT COUNT(*) c FROM page_translations
                       WHERE status='skip' AND status='wanted'""").fetchone()["c"] == 0,
                detail="otherwise every proper noun goes to the model every "
                       "ten minutes for ever")
    finally:
        m.translate_batch_with_claude = was_batch
        m.claude_configured = was_conf

    s.section("A person's translation outranks the machine's")
    conn.execute(
        """INSERT OR REPLACE INTO page_translations
           (source_text, lang, translated, status, created_at)
           VALUES ('Lime, not cement', 'fr', 'De la chaux, pas du ciment',
                   'approved', ?)""", (m.datetime.now(m.timezone.utc).isoformat(),))
    conn.commit()
    m.translate_batch_with_claude = lambda lines, lang: (
        {l: "MACHINE OVERWROTE IT" for l in lines}, {}, None)
    m.claude_configured = lambda: True
    try:
        m.run_page_translation_job(conn, limit=50)
        kept = conn.execute(
            """SELECT translated, status FROM page_translations
               WHERE source_text='Lime, not cement' AND lang='fr'""").fetchone()
        s.check("an approved line is left exactly as the person wrote it",
                kept["translated"] == "De la chaux, pas du ciment"
                and kept["status"] == "approved",
                detail="%r / %s" % (kept["translated"], kept["status"]))
    finally:
        m.translate_batch_with_claude = was_batch
        m.claude_configured = was_conf

    s.section("It cannot spend without a ceiling, and cannot be reached from a page")
    s.check("a run is capped", m.TRANSLATION_MAX_PER_RUN <= 500,
            detail="a handover can add two thousand sentences in an afternoon")
    s.check("and batched rather than sent one at a time",
            1 < m.TRANSLATION_BATCH <= 100, detail=str(m.TRANSLATION_BATCH))
    # NEVER INSIDE A REQUEST. A page a guest is waiting for must not depend on
    # a third party being up, whatever the latency.
    reached = []
    m.translate_batch_with_claude = lambda lines, lang: (reached.append(1), ({}, {}, None))[1]
    m.claude_configured = lambda: True
    try:
        conn.execute("UPDATE page_translations SET status='wanted' WHERE status='machine'")
        conn.commit()
        m.translation_memory(force=True)
        guest.get("/book")
        s.check("serving a page calls no model at all", reached == [],
                detail="%d call(s) from one page view" % len(reached))
    finally:
        m.translate_batch_with_claude = was_batch
        m.claude_configured = was_conf

    s.section("The model's answers are matched back by source, never by order")
    # THE ONE PLACE A SILENT, SERIOUS CORRUPTION COULD HAPPEN. If answers were
    # paired to questions by position, a model that dropped or reordered a
    # line would put the third sentence's French under the second sentence's
    # English — fluent, plausible, on the wrong paragraph, and invisible to
    # anybody who does not read both languages. Every other test here mocks
    # translate_batch_with_claude out; this one drives it.
    asked = ["Lime, not cement", "Approved, then done", "The building breathes"]

    class _FakeMessages:
        def __init__(self, payload):
            self.payload = payload
            self.seen = []

        def parse(self, **kw):
            self.seen.append(kw)
            return type("R", (), {"parsed_output": self.payload})()

    class _FakeClient:
        def __init__(self, payload):
            self.messages = _FakeMessages(payload)

    def with_payload(payload):
        holder = {}

        class _Anthropic:
            def __init__(self, **kw):
                holder["client"] = _FakeClient(payload)
                self.messages = holder["client"].messages
        return _Anthropic, holder

    was_anthropic = m.anthropic.Anthropic
    was_conf = m.claude_configured
    m.claude_configured = lambda: True
    try:
        # Reordered, one dropped, and one line invented that was never asked
        # about — all three things a model can do.
        payload = {"lines": [
            {"source": "The building breathes", "translated": "Le bâtiment respire",
             "leave_alone": False},
            {"source": "A line nobody asked about", "translated": "Inventé",
             "leave_alone": False},
            {"source": "Lime, not cement", "translated": "De la chaux, pas du ciment",
             "leave_alone": False},
        ]}
        m.anthropic.Anthropic, _h = with_payload(payload)
        done, leave, why = m.translate_batch_with_claude(asked, "fr")
        s.check("a reordered answer lands under its own question",
                done.get("Lime, not cement") == "De la chaux, pas du ciment"
                and done.get("The building breathes") == "Le bâtiment respire",
                detail=repr(done))
        s.check("a line the model never answered is simply absent",
                "Approved, then done" not in done,
                detail="it stays wanted and is asked again next run")
        s.check("and a line nobody asked about is discarded",
                "A line nobody asked about" not in done,
                detail="otherwise the model can write rows for text that is "
                       "on no page at all")

        # An answer identical to the question is not a translation.
        m.anthropic.Anthropic, _h = with_payload({"lines": [
            {"source": "Lime, not cement", "translated": "Lime, not cement",
             "leave_alone": False}]})
        done, leave, why = m.translate_batch_with_claude(["Lime, not cement"], "fr")
        s.check("an answer identical to the question is left alone, not stored",
                not done and leave.get("Lime, not cement"),
                detail="storing it would serve English and call it French")

        # And the provider falling over is a quiet no-op.
        class _Boom:
            def __init__(self, **kw):
                raise RuntimeError("provider down")
        m.anthropic.Anthropic = _Boom
        done, leave, why = m.translate_batch_with_claude(asked, "fr")
        s.check("a provider that is down costs nothing", done == {} and leave == {},
                detail="the rows stay wanted and the pages stay English")
    finally:
        m.anthropic.Anthropic = was_anthropic
        m.claude_configured = was_conf

    s.section("A line approved while the job is running is not overwritten")
    # The rows are chosen, then translated, then written. Somebody approving a
    # line in between is a real sequence, and the write must lose that race
    # rather than win it — which is what `AND status = 'wanted'` on the UPDATE
    # is for.
    conn = db()
    conn.execute("DELETE FROM page_translations")
    stamp = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO page_translations (source_text, lang, status, created_at)
           VALUES ('Lime, not cement', 'fr', 'wanted', ?)""", (stamp,))
    conn.commit()

    def approve_then_answer(lines, lang):
        # The person gets there first, while the model is still thinking.
        other = db()
        other.execute(
            """UPDATE page_translations SET translated = ?, status = 'approved'
               WHERE source_text = 'Lime, not cement' AND lang = 'fr'""",
            ("De la chaux, pas du ciment",))
        other.commit()
        other.close()
        return {l: "CE QUE LA MACHINE A DIT" for l in lines}, {}, None

    was_batch = m.translate_batch_with_claude
    m.translate_batch_with_claude = approve_then_answer
    m.claude_configured = lambda: True
    try:
        m.run_page_translation_job(conn, limit=10)
        row = conn.execute(
            """SELECT translated, status FROM page_translations
               WHERE source_text = 'Lime, not cement' AND lang = 'fr'""").fetchone()
        s.check("the person's words are the ones kept",
                row["translated"] == "De la chaux, pas du ciment",
                detail="%r / %s" % (row["translated"], row["status"]))
        s.check("and it stays approved rather than being demoted to machine",
                row["status"] == "approved", detail=row["status"])
    finally:
        m.translate_batch_with_claude = was_batch
        m.claude_configured = was_conf
    conn.execute("DELETE FROM page_translations")
    conn.commit()
    # Left OPEN: the section below still uses it. Closing it here
    # crashed the suite on "Cannot operate on a closed database".

    s.section("The answer is read under either name the SDK uses")
    # meeting_minutes already reads `parsed_output or parsed`, because the
    # SDK has answered under both. This read parsed_output directly, and a
    # bare attribute access raises AttributeError -- which the handler around
    # it then swallowed, so every call came back empty and the rows sat
    # "wanted" for ever with nothing said about it anywhere.
    was_conf, was_anth = m.claude_configured, m.anthropic.Anthropic
    m.claude_configured = lambda: True

    class _OldName:
        """A response carrying `parsed` and no `parsed_output` at all."""
        def __init__(self, **kw):
            self.messages = self
        def parse(self, **kw):
            class R:
                parsed = {"lines": [{"source": "Lime, not cement",
                                     "translated": "De la chaux, pas du ciment",
                                     "leave_alone": False}]}
            return R()
    m.anthropic.Anthropic = _OldName
    try:
        done, leave, why = m.translate_batch_with_claude(["Lime, not cement"], "fr")
        s.check("a response with only `parsed` is still read",
                done.get("Lime, not cement") == "De la chaux, pas du ciment",
                detail="done=%r why=%r" % (done, why))
        s.check("and it is not reported as a failure", why is None, detail=str(why))
    finally:
        m.anthropic.Anthropic = was_anth
        m.claude_configured = was_conf

    s.section("A run that gets nowhere says so, rather than saying nothing")
    # THE FAULT THIS WAS WRITTEN FOR. The job reported "translated 0, left 0"
    # whatever went wrong, so a provider refusing every call looked exactly
    # like a queue that was already empty. Eighteen minutes of watching a
    # percentage that never moved is what it cost, and on a quiet site nobody
    # would have noticed for weeks.
    conn = db()
    conn.execute("DELETE FROM page_translations")
    conn.execute("""INSERT INTO page_translations
                    (source_text, lang, status, created_at)
                    VALUES (?, 'fr', 'wanted', ?)""",
                 ("A sentence waiting to be translated.",
                  m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    was_conf = m.claude_configured
    was_anth = m.anthropic.Anthropic
    m.claude_configured = lambda: True

    class _Down:
        def __init__(self, **kw):
            raise RuntimeError("provider refused the key")
    m.anthropic.Anthropic = _Down
    try:
        said = m.run_page_translation_job(conn, limit=5)
        s.check("the job names the failure", "refused the key" in said,
                detail=said)
        s.check("and says how many are still waiting", "waiting" in said,
                detail=said)
        s.check("and does not claim to have translated anything",
                "translated 0" not in said, detail=said)
        s.check("the row is left wanted for the next run",
                conn.execute("""SELECT status FROM page_translations
                                WHERE lang = 'fr'""").fetchone()["status"]
                == "wanted")
    finally:
        m.anthropic.Anthropic = was_anth
        m.claude_configured = was_conf

    # And an answer in a shape we do not read is reported too, rather than
    # counting as an empty reply.
    class _Odd:
        def __init__(self, **kw):
            self.messages = self
        def parse(self, **kw):
            return type("R", (), {"parsed_output": "not a dict"})()
    m.claude_configured = lambda: True
    m.anthropic.Anthropic = _Odd
    try:
        said = m.run_page_translation_job(conn, limit=5)
        s.check("an unreadable answer is reported, not swallowed",
                "shape" in said or "waiting" in said, detail=said)
    finally:
        m.anthropic.Anthropic = was_anth
        m.claude_configured = was_conf
    conn.execute("DELETE FROM page_translations")
    conn.commit()

    s.section("Whether the site is translating itself can be read from outside")
    # An hour was spent unable to tell two faults apart: sentences never
    # written down, and sentences written down but never translated. From
    # outside both were the same unmoving percentage on a French page, and
    # the automation page that would have said which needs a login -- on a
    # site whose whole recovery story is about not being able to log in.
    diag = db()
    diag.execute("DELETE FROM page_translations")
    diag.commit()
    guest2 = m.app.test_client()
    guest2.get("/language/fr")
    guest2.get("/book")
    t = m.app.test_client().get("/status").get_json().get("translation") or {}
    s.check("status counts what is waiting", t.get("wanted", 0) > 50,
            detail=str(t))
    s.check("and what has been translated", "machine" in t and "approved" in t)
    s.check("and whether the job is switched on at all",
            t.get("enabled") is True,
            detail="read from the database, not from the default constant")

    # THE KIND OF FAILURE, NEVER THE PROVIDER'S PROSE. A class name says
    # which fix is needed; a message body can carry a fragment of what was
    # sent, and this page is public.
    was_conf2, was_anth2 = m.claude_configured, m.anthropic.Anthropic
    m.claude_configured = lambda: True

    class _Refused:
        def __init__(self, **kw):
            raise RuntimeError("key sk-secret-do-not-print-abcdefgh was refused")
    m.anthropic.Anthropic = _Refused
    try:
        m.run_page_translation_job(diag, limit=5)
        t = m.app.test_client().get("/status").get_json()["translation"]
        s.check("the failure reaches the status page with its reason",
                "RuntimeError" in (t.get("last_error") or "")
                and "was refused" in (t.get("last_error") or ""),
                detail=str(t.get("last_error")))
        # THE MESSAGE IS KEPT, THE KEY IS NOT. What gets sent to the
        # provider is sentences already printed on public pages, so a
        # provider quoting the request back costs nothing. The
        # credential is the one thing in that string worth hiding.
        body = m.app.test_client().get("/status").get_data(as_text=True)
        s.check("but the key never does",
                "sk-secret-do-not-print" not in body,
                detail="a public page must never echo the credential")
    finally:
        m.anthropic.Anthropic = was_anth2
        m.claude_configured = was_conf2
    diag.execute("DELETE FROM page_translations")
    diag.commit()
    diag.close()

    s.section("With no provider it is a no-op, not an error")
    # The shipping state today: there is no key, and the site must be exactly
    # as it was rather than broken.
    s.check("the job says so plainly",
            m.run_page_translation_job(conn) == "no model provider configured")
    s.check("and a page is still served",
            guest.get("/book").status_code == 200)
    conn.execute("DELETE FROM page_translations")
    conn.commit()
    conn.close()

    s.section("A hand-written translation that no longer matches any page")
    # THE REASON THE REST OF THIS EXISTS, made into a check.
    #
    # page_text.py holds the translations written by hand rather than by the
    # job, and it is keyed on the English sentence. The design side redraws
    # the public pages most weeks, so the day a sentence is reworded its
    # translation stops matching — and nothing breaks. The page simply serves
    # that paragraph in English again, silently, for ever.
    #
    # It is not a hypothetical. Twenty-two entries were written in one sitting
    # and two of them were dead within the hour, because a handover installed
    # that morning had already changed "two hundred and fifty years" to "two
    # hundred and eighty" in one of the paragraphs being translated.
    #
    # So a key here that matches nothing a guest is served is a FAILURE, and
    # the fix is to delete it rather than retype it against today's wording:
    # the job translates whatever the page actually says, which is the whole
    # point of it existing.
    import page_text
    live = set()
    walked = 0
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
        walked += 1
        live |= {m.translation_key(node)
                 for _st, _e, node in m.page_text_spans(text)}
    s.check("there are public pages to check against", walked > 10,
            detail="%d walked; if this collapses the check below proves "
                   "nothing" % walked)
    for code, table in (("fr", page_text.FR), ("es", page_text.ES)):
        stale = sorted(k for k in table if k not in live)
        s.check("every hand-written %s key is still on a page" % code,
                not stale,
                detail="%d stale: %s" % (len(stale), "; ".join(
                    k[:60] for k in stale[:3])))

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
