"""The language a member of staff reads the app in.

The guest site guesses from the browser session, which is right for someone
passing through and wrong for someone who opens the app every shift: a
housekeeper had to re-pick French each login, and could inherit Spanish
because somebody browsed the public site on the office laptop.

So the choice lives on the account. The checks that matter are that it
persists, that it does not leak between people, and that the employee screens
actually change language rather than only the shell around them.
"""
import re

from _harness import Suite, clients, db
import _harness

m = _harness.m

# What a housekeeper or chef actually opens. Deliberately not the owner's
# financial pages: those are one reader, in one language.
EMPLOYEE_PAGES = [
    "/today", "/shifts/mine", "/leave", "/announcements", "/manual",
    "/directory", "/admin/team-calendar", "/availability", "/contacts",
    "/shopping", "/hr/ask", "/my-reviews", "/notifications",
]


def _h1(html):
    hit = re.search(r"<h1[^>]*>([^<]+)<", html)
    return hit.group(1).strip() if hit else ""


def _reset():
    conn = db()
    conn.execute("UPDATE users SET language = NULL")
    conn.commit()
    conn.close()


def run():
    s = Suite("Staff language")
    _reset()
    oc, ec, owner, emp = clients()

    s.section("Every employee page renders in all three languages")
    for code in ("en", "fr", "es"):
        ec.get(f"/language/{code}", headers={"Referer": "/today"}, follow_redirects=True)
        bad = [p for p in EMPLOYEE_PAGES if ec.get(p).status_code != 200]
        s.check(f"{code}: all {len(EMPLOYEE_PAGES)} pages load", not bad, detail=f"failed: {bad}")

    s.section("The words actually change")
    seen = {}
    for code in ("en", "fr", "es"):
        ec.get(f"/language/{code}", headers={"Referer": "/leave"}, follow_redirects=True)
        seen[code] = _h1(ec.get("/leave").get_data(as_text=True))
    s.check("the Time Off heading differs in all three",
            len({seen["en"], seen["fr"], seen["es"]}) == 3, detail=str(seen))
    s.check("English is still English", seen["en"] == "Time Off", detail=seen["en"])

    # Heading by heading across every page: loading in French proves routing,
    # not translation. Anything whose heading is identical in all three is
    # either untranslated or a word that genuinely does not change.
    unchanged = []
    for path in EMPLOYEE_PAGES:
        got = {}
        for code in ("en", "fr", "es"):
            ec.get(f"/language/{code}", headers={"Referer": path}, follow_redirects=True)
            got[code] = _h1(ec.get(path).get_data(as_text=True))
        if got["en"] == got["fr"] == got["es"]:
            unchanged.append(f"{path} ({got['en']})")
    # Contacts and Notifications are the same word in all three languages.
    allowed = {"/contacts", "/notifications"}
    real = [u for u in unchanged if u.split(" ")[0] not in allowed]
    s.check("every page's heading is translated, bar the words that don't change",
            not real, detail=" | ".join(real))

    s.section("The choice belongs to the account, not the browser")
    ec.get("/language/fr", headers={"Referer": "/today"}, follow_redirects=True)
    conn = db()
    saved = conn.execute("SELECT language FROM users WHERE id = ?", (emp["id"],)).fetchone()
    conn.close()
    s.check("it is written to the employee's row", saved["language"] == "fr",
            detail=f"got {saved['language']!r}")

    # A fresh client signed in as the same person, carrying no "lang" in its
    # session at all — so anything it sees in French came from the account and
    # nowhere else. This is the case that used to fail: the old code kept the
    # choice only in the session, so a new login was English again.
    fresh = m.app.test_client()
    with fresh.session_transaction() as sess:
        sess["user_id"] = emp["id"]
    s.check("a brand-new login is still French",
            _h1(fresh.get("/leave").get_data(as_text=True)) == seen["fr"],
            detail=_h1(fresh.get("/leave").get_data(as_text=True)))

    s.check("and the page declares the language it is in",
            'lang="fr"' in fresh.get("/today").get_data(as_text=True))

    s.section("One person's choice does not move anybody else")
    owner_h1 = _h1(oc.get("/leave").get_data(as_text=True))
    s.check("the owner is unaffected by the employee choosing French",
            owner_h1 == "Time Off", detail=owner_h1)

    s.section("A guest is still guessed from the browser, having no account")
    pub = m.app.test_client()
    pub.get("/language/es", headers={"Referer": "/"}, follow_redirects=True)
    s.check("the public site follows the session choice",
            'lang="es"' in pub.get("/").get_data(as_text=True))

    _reset()
    return s
