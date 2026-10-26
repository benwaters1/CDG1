"""A guest's own words are never written down for the translator.

WHAT WENT WRONG, AND WHY NOTHING REPORTED IT.

The public pages are translated at the response, and every sentence with no
translation yet is written down as 'wanted'; a job sends those to Claude and
keeps the answer for good. That was argued safe because the list only ever
held sentences printed on public pages for anybody to read.

A guest's own pages are public pages in that sense -- the guest site's body
class, no login -- and they print what the guest typed. On 23 Oct 2026 a
booking with "my husband has a severe nut allergy please" in its special
requests, read at /book/manage/<token>?lang=fr, left two French 'wanted' rows
holding that sentence. The next run of the job would have sent a guest's
health note to Anthropic and kept the French for ever, under a privacy notice
promising Anthropic is sent nothing about a guest's health and that a dietary
note is deleted once the booking is over. The page rendered perfectly.

WHAT IS ASSERTED HERE.

That a French and a Spanish read of the manage page file nothing a guest
wrote, while still filing the house's own sentences off the same page (or
the fix would be "stop filing", which turns translation off for every page a
guest is sent to). That the address is noted without the guest's key in it.
That rows filed before the fix are deleted, before the job sends anything,
and that rows off pages nobody owns are left alone. And a negative control:
with the guest-page test stood down, the same read DOES file the note -- so
the checks above are measuring the fix and not a page that never printed it.
"""
import inspect
from datetime import datetime, timedelta, timezone

from _harness import Suite, db, ensure_room, house_today, keep_rows
import _harness

m = _harness.m

TAG = "ZZGW"
TOKEN = f"tok{TAG}manage"
NOTE = f"my husband has a severe nut allergy please {TAG}"
ASKED = f"we are vegan and cannot eat honey either thank you {TAG}"


def _cleanup(conn):
    conn.execute("DELETE FROM tasks WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (f"{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (f"%{TAG}%",))
    conn.commit()


def _filed(conn, words):
    return conn.execute(
        "SELECT lang, status, seen_on FROM page_translations WHERE source_text LIKE ?",
        (f"%{words}%",)).fetchall()


def _read(lang):
    """A fresh reader each time, so one read's language cannot carry over."""
    return m.app.test_client().get(f"/book/manage/{TOKEN}?lang={lang}")


def run():
    s = Suite("A guest's own words are not sent to the translator")
    conn = db()
    _cleanup(conn)
    room = ensure_room()
    arrival = house_today() + timedelta(days=640)
    now = datetime.now(timezone.utc).isoformat()
    bid = conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date,
           departure_date, party_size, status, reference_code, manage_token,
           special_requests, created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'confirmed', ?, ?, ?, ?)""",
        (room["id"], f"{TAG} Guest", f"{TAG.lower()}@example.invalid",
         arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
         f"{TAG}1", TOKEN, NOTE, now)).lastrowid
    # A request the guest made from the page itself, shown back to them.
    conn.execute(
        """INSERT INTO tasks (title, notes, priority, due_date, created_at, origin, booking_id)
           VALUES ('Dietary request', ?, 'normal', ?, ?, 'guest_request', ?)""",
        (ASKED, arrival.isoformat(), now, bid))
    conn.commit()
    try:
        _reads(s, conn)
        _house_words_still_filed(s, conn)
        _which_pages(s)
        _old_rows(s, conn)
        _the_job(s, conn)
        _negative_control(s, conn)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _reads(s, conn):
    s.section("A French and a Spanish read of the manage page")
    for lang, word in (("fr", "French"), ("es", "Spanish")):
        r = _read(lang)
        html = r.get_data(as_text=True)
        s.check(f"the {word} page answers", r.status_code == 200, r)
        s.check(f"and prints the guest's dietary note", NOTE in html,
                detail="without this the checks below prove nothing")
        s.check(f"and the request they made", ASKED in html)
        s.check(f"in {word}, at the page's own address",
                f'lang="{lang}"' in html)
    s.check("the dietary note is filed in no language", not _filed(conn, NOTE),
            detail=str([dict(r) for r in _filed(conn, NOTE)]))
    s.check("nor is the request", not _filed(conn, ASKED),
            detail=str([dict(r) for r in _filed(conn, ASKED)]))
    s.check("and no row anywhere holds the guest's key",
            not conn.execute("SELECT 1 FROM page_translations WHERE seen_on LIKE ?",
                             (f"%{TOKEN}%",)).fetchone(),
            detail="the key to somebody's booking does not belong in a table "
                   "kept for as long as the sentence is")


def _house_words_still_filed(s, conn):
    """The fix must narrow the filing, not stop it."""
    s.section("The house's own sentences on that page are still written down")
    html = m.app.test_client().get(f"/book/manage/{TOKEN}").get_data(as_text=True)
    ours = m.template_sentences()
    table = m.translations.TABLES["fr"]
    remembered = m.translation_memory(force=True).get("fr", {})
    candidates = []
    for _st, _e, text in m.page_text_spans(html):
        key = m.translation_key(text)
        if (key in ours and m.worth_translating(key) and key not in table
                and key not in remembered and TAG not in key):
            candidates.append(key)
    s.check("the page has words of ours with no French yet", bool(candidates))
    if not candidates:
        return
    pick = candidates[0]
    put_back = keep_rows(conn, "page_translations", "source_text = ? AND lang = 'fr'",
                         (pick,))
    try:
        conn.execute("DELETE FROM page_translations WHERE source_text = ? AND lang = 'fr'",
                     (pick,))
        conn.commit()
        _read("fr")
        row = conn.execute(
            "SELECT status, seen_on FROM page_translations WHERE source_text = ? AND lang = 'fr'",
            (pick,)).fetchone()
        s.check("a sentence from the template is filed as wanted",
                row is not None and row["status"] == "wanted", detail=pick)
        s.check("against the route's pattern, not the guest's address",
                row is not None and row["seen_on"] == "/book/manage/<manage_token>",
                detail=row["seen_on"] if row else "not filed")
    finally:
        put_back()


def _which_pages(s):
    s.section("Which pages count as a guest's own")
    cases = (
        ("/book/manage/abc", "GET", True, "a token in the route"),
        ("/stay/abc", "GET", True, "a shared stay"),
        ("/my/abc", "GET", True, "the guest portal"),
        ("/workshops/stripe-cancel/abc", "GET", True, "a Stripe return"),
        ("/restaurant/confirmation/abc?session_id=cs_test_1", "GET", True,
         "a session id in the query"),
        ("/contact", "POST", True, "the answer to a form"),
        ("/restoration?lang=fr", "GET", False, "the restoration story"),
        ("/", "GET", False, "the home page"),
    )
    for path, method, want, what in cases:
        with m.app.test_request_context(path, method=method):
            s.check(f"{what}: {'yes' if want else 'no'}",
                    m.is_a_guests_own_page() is want, detail=path)
    with m.app.test_request_context("/rooms"):
        m.session["guest_email"] = "zzgw@example.invalid"
        s.check("a public page read by a recognised guest: yes",
                m.is_a_guests_own_page() is True,
                detail="the rooms page greets them by name")
    s.check("a stored address is read the same way",
            m.address_is_a_guests_page("/book/manage/<manage_token>")
            and m.address_is_a_guests_page("/book/manage/tokOLD")
            and not m.address_is_a_guests_page("/restoration")
            and not m.address_is_a_guests_page("/no/such/page"))


def _old_rows(s, conn):
    s.section("What was filed before this is deleted")
    stamp = datetime.now(timezone.utc).isoformat()
    ours = sorted(k for k in m.template_sentences()
                  if m.worth_translating(k) and len(k) > 20)
    house = ours[0]
    put_back = keep_rows(conn, "page_translations", "source_text = ? AND lang = 'es'",
                         (house,))
    try:
        conn.execute("DELETE FROM page_translations WHERE source_text = ? AND lang = 'es'",
                     (house,))
        conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (f"%{TAG}%",))
        rows = (
            (NOTE, "fr", "machine", "mon mari est allergique", "/book/manage/tokOLD"),
            (ASKED, "es", "approved", "somos veganos", "/my/oldtoken"),
            (f"a {TAG} line the owner typed into a page", "fr", "wanted", None,
             "/restoration"),
            (house, "es", "wanted", None, "/book/manage/tokOLD"),
        )
        conn.executemany(
            """INSERT INTO page_translations (source_text, lang, status, translated,
               seen_on, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            [r + (stamp,) for r in rows])
        conn.commit()
        gone = m.purge_guest_page_translations(conn)
        s.check("a guest's note translated by the job goes", not _filed(conn, NOTE))
        s.check("and one a person approved goes too", not _filed(conn, ASKED),
                detail="approving its Spanish does not make a guest's note the house's")
        s.check("the house's own sentence seen on a guest's page stays",
                conn.execute("SELECT 1 FROM page_translations WHERE source_text = ? "
                             "AND lang = 'es'", (house,)).fetchone() is not None,
                detail=house)
        s.check("and nothing off a page nobody owns is touched",
                bool(_filed(conn, f"a {TAG} line the owner typed")))
        s.check("it says how many it deleted", gone >= 2, detail=str(gone))
        s.check("and the daily retention pass runs it",
                "purge_guest_page_translations" in inspect.getsource(
                    m.run_health_notes_purge_job),
                detail="the translation job can be switched off; the deletion "
                       "the notice promises cannot depend on it")
    finally:
        conn.execute("DELETE FROM page_translations WHERE source_text = ? AND lang = 'es'",
                     (house,))
        conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (f"%{TAG}%",))
        conn.commit()
        put_back()


def _the_job(s, conn):
    s.section("The job deletes before it sends")
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (f"%{TAG}%",))
    conn.execute(
        """INSERT INTO page_translations (source_text, lang, status, seen_on, created_at)
           VALUES (?, 'fr', 'wanted', '/book/manage/tokOLD', ?)""", (NOTE, stamp))
    conn.commit()
    sent = []
    was_batch, was_configured = m.translate_batch_with_claude, m.claude_configured

    def batch(lines, lang):
        sent.extend(lines)
        return {}, [], None
    m.translate_batch_with_claude = batch
    m.claude_configured = lambda: True
    try:
        m.run_page_translation_job(conn, limit=5000)
    finally:
        m.translate_batch_with_claude = was_batch
        m.claude_configured = was_configured
    s.check("the guest's note is never sent", NOTE not in sent,
            detail="the job sent the note it was about to delete")
    s.check("and it is gone from the table", not _filed(conn, NOTE))


def _negative_control(s, conn):
    """Stand the guest-page test down and the same read must file the note.
    If it does not, the checks above were measuring a page that never offered
    the note to the filer, and they prove nothing."""
    s.section("Negative control: without the guest-page test it IS filed")
    was = m.is_a_guests_own_page
    m.is_a_guests_own_page = lambda: False
    try:
        _read("fr")
    finally:
        m.is_a_guests_own_page = was
    filed = _filed(conn, NOTE)
    s.check("the dietary note is filed as wanting French",
            any(r["lang"] == "fr" and r["status"] == "wanted" for r in filed),
            detail=str([dict(r) for r in filed]))
    conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (f"%{TAG}%",))
    conn.commit()


if __name__ == "__main__":
    print(run().report())
