"""The Languages page: where French and Spanish stand, and the switch.

French and Spanish were built long before anybody could see them: a
translator over every public page, a queue, a job that sends the queue to
Claude. On 15 October 2026 the owner asked where they were. Most pages had
never been queued -- a sentence queues only when its page is read in that
language -- and the language picker hung on a setting nothing wrote.

Claude is never called here: queueing reads pages, it does not translate.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "Zzlang"


def _setting(key):
    conn = db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def run():
    s = Suite("Languages")
    oc, ec, _o, _e = clients()
    was_switch = _setting("show_language_switcher")

    s.section("The owner can see where each language stands")
    conn = db()
    conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (TAG + "%",))
    for i, (status, lang) in enumerate((("machine", "fr"), ("machine", "fr"),
                                        ("wanted", "fr"), ("wanted", "es"))):
        conn.execute(
            """INSERT INTO page_translations (source_text, lang, translated, status, created_at)
               VALUES (?, ?, ?, ?, '2026-10-15')""",
            (f"{TAG} sentence {i}", lang, "traduit" if status == "machine" else None, status))
    conn.commit()
    progress = {p["code"]: p for p in m.language_progress(conn)}
    conn.close()
    s.check("each language counts its own sentences",
            progress["fr"]["done"] >= 2 and progress["fr"]["waiting"] >= 1
            and progress["es"]["waiting"] >= 1, detail=str(progress))
    page = oc.get("/admin/languages")
    body = page.get_data(as_text=True)
    s.check("the page opens", page.status_code == 200, detail=str(page.status_code))
    s.check("naming both languages and how far they are",
            "French:" in body and "Spanish:" in body and "%" in body, detail=body[:300])
    s.check("and it says plainly when there is no Claude key",
            m.claude_configured() or "ANTHROPIC_API_KEY" in body)
    s.check("a member of staff cannot open it",
            ec.get("/admin/languages").status_code in (302, 403))
    s.check("it is in the menu, under Website & Email",
            "admin_languages" in [i["endpoint"] for g in m.OWNER_MENU if g[0] == "comms"
                                  for _h, items in g[5] for i in items])

    s.section("The picker is switched from here")
    r = oc.post("/admin/languages/switcher", data={"show": "1"})
    s.check("switched on", r.status_code in (302, 303) and _setting("show_language_switcher") == "1",
            detail=f"{r.status_code} {_setting('show_language_switcher')}")
    public = m.app.test_client().get("/story").get_data(as_text=True)
    s.check("and every public page offers French and Spanish",
            'class="g-lang"' in public and "/language/fr" in public)
    oc.post("/admin/languages/switcher", data={"show": "0"})
    public = m.app.test_client().get("/story").get_data(as_text=True)
    s.check("switched off, the picker goes", 'class="g-lang"' not in public)

    s.section("Every page can be queued at once")
    conn = db()
    conn.execute("DELETE FROM page_translations WHERE lang IN ('fr', 'es') AND status = 'wanted'")
    views_before = conn.execute("SELECT COUNT(*) AS c FROM page_views").fetchone()["c"]
    conn.commit()
    conn.close()
    m.LANGUAGE_QUEUE_INLINE = True
    try:
        r = oc.post("/admin/languages/queue")
    finally:
        m.LANGUAGE_QUEUE_INLINE = False
    conn = db()
    wanted = {row["lang"]: row["c"] for row in conn.execute(
        """SELECT lang, COUNT(*) AS c FROM page_translations
            WHERE status = 'wanted' GROUP BY lang""")}
    views_after = conn.execute("SELECT COUNT(*) AS c FROM page_views").fetchone()["c"]
    pages = m.pages_a_visitor_can_reach(m.app.test_client())
    conn.close()
    s.check("it answers", r.status_code in (302, 303), detail=str(r.status_code))
    s.check("the pages it reads are the site's own: the sitemap and the front page's links",
            "/book" in pages and "/story" in pages
            and not any(p.startswith(("/admin", "/api", "/static", "/webhooks")) for p in pages),
            detail=str(pages[:12]))
    s.check("sentences in French and in Spanish are now waiting",
            wanted.get("fr", 0) > 20 and wanted.get("es", 0) > 20, detail=str(wanted))
    s.check("and the house reading its own site is not counted as visitors",
            views_after == views_before, detail=f"{views_before} -> {views_after}")
    s.check("nothing was sent to Claude: queueing is reading, not translating",
            m.claude_configured() is False)

    conn = db()
    conn.execute("DELETE FROM page_translations WHERE source_text LIKE ?", (TAG + "%",))
    if was_switch is None:
        conn.execute("DELETE FROM app_settings WHERE key = 'show_language_switcher'")
    else:
        conn.execute("UPDATE app_settings SET value = ? WHERE key = 'show_language_switcher'",
                     (was_switch,))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
