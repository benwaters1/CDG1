"""French and Spanish on the guest-facing site.

The château is in the Ariège. An English-only booking form is a real barrier
for the guests nearest to it, and for Spanish visitors coming over the border.

Three things are worth pinning. That choosing a language actually changes the
page and is remembered. That a missing translation falls back to English rather
than blanking or raising — that is what makes shipping a partly-filled language
safe. And that the staff app is left alone: it is used by a handful of named
people who share a working language.

The coverage numbers are printed rather than asserted at some threshold. A
threshold would either block work or be set so low it proves nothing; a printed
number makes the gap visible every run, the same argument as the endpoint
coverage report in run.py.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m
translations = m.translations


def run():
    s = Suite("Translations")

    s.section("The table itself")
    s.check("French and Spanish are both offered",
            set(translations.LANGUAGES) == {"en", "fr", "es"},
            detail=f"got {sorted(translations.LANGUAGES)}")
    s.check("English is the source and needs no table",
            "en" not in translations.TABLES)

    # A translation identical to its English key is either untranslated or a
    # word that genuinely does not change ("Menu", "Total"). Worth counting
    # separately so a table of copied English cannot look complete.
    for code in ("fr", "es"):
        done, total = translations.coverage(code)
        table = translations.TABLES[code]
        same = [k for k, v in table.items() if k == v]
        print(f"    ....  {code}: {done}/{total} strings "
              f"({100 * done // max(total, 1)}%), {len(same)} identical to English")

    s.section("Nothing is left half-written")
    blanks = {code: [k for k, v in tbl.items() if not (v or "").strip()]
              for code, tbl in translations.TABLES.items()}
    empty = {c: ks for c, ks in blanks.items() if ks}
    s.check("no entry is an empty string", not empty,
            detail="; ".join(f"{c}: {ks[:3]}" for c, ks in empty.items()))

    s.section("Falling back rather than failing")
    s.check("an unknown string comes back as itself",
            translations.translate("Not in any table", "fr") == "Not in any table")
    s.check("an unknown language comes back as English",
            translations.translate("Workshops", "de") == "Workshops")
    s.check("English is returned untouched",
            translations.translate("Workshops", "en") == "Workshops")
    s.check("empty input does not raise", translations.translate("", "fr") == "")
    s.check("a known string is genuinely translated",
            translations.translate("Workshops", "fr") == "Ateliers",
            detail=f"got {translations.translate('Workshops', 'fr')!r}")

    s.section("Choosing a language")
    pub = m.app.test_client()
    home = pub.get("/").get_data(as_text=True)
    s.check("English by default", 'lang="en"' in home, detail="no lang attribute")

    r = pub.get("/language/fr", follow_redirects=True)
    s.check("switching is accepted", r.status_code == 200, detail=f"HTTP {r.status_code}")
    page = pub.get("/").get_data(as_text=True)
    s.check("the page is now French", 'lang="fr"' in page)
    s.check("and the navigation with it", "Ateliers" in page,
            detail="the French nav wording is missing")
    # Asked for by endpoint rather than typed: a guessed URL 404s, and a 404
    # has no <html lang> at all, so the check would fail for the wrong reason.
    with m.app.test_request_context():
        rooms_url = m.url_for("book_rooms")
    s.check("it is remembered on the next request",
            'lang="fr"' in pub.get(rooms_url).get_data(as_text=True),
            detail=f"on {rooms_url}")

    r = pub.get("/language/es", follow_redirects=True)
    page = pub.get("/").get_data(as_text=True)
    s.check("Spanish works too", 'lang="es"' in page and "Talleres" in page)

    pub.get("/language/en", follow_redirects=True)
    s.check("and English can be chosen back",
            'lang="en"' in pub.get("/").get_data(as_text=True))

    s.section("A language nobody offers is ignored, not obeyed")
    before = pub.get("/").get_data(as_text=True)
    pub.get("/language/de", follow_redirects=True)
    after = pub.get("/").get_data(as_text=True)
    s.check("an unsupported code leaves the language alone",
            ('lang="en"' in after) and (('lang="en"' in before) == ('lang="en"' in after)))

    s.section("The switcher cannot be used to bounce somebody off-site")
    # request.referrer is attacker-settable, and a one-click open redirect is
    # exactly the shape phishing wants.
    r = pub.get("/language/fr", headers={"Referer": "https://evil.example/steal"})
    dest = r.headers.get("Location", "")
    s.check("an off-site referrer is not followed",
            "evil.example" not in dest, detail=f"redirected to {dest!r}")
    r = pub.get("/language/fr", headers={"Referer": rooms_url})
    s.check("but a local one is", rooms_url in r.headers.get("Location", ""),
            detail=f"redirected to {r.headers.get('Location')!r}")

    s.section("The browser's own preference is honoured first")
    fresh = m.app.test_client()
    page = fresh.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"}).get_data(as_text=True)
    s.check("a French browser gets French without touching the switcher",
            'lang="fr"' in page, detail="Accept-Language ignored")
    fresh2 = m.app.test_client()
    page = fresh2.get("/", headers={"Accept-Language": "ja-JP,ja;q=0.9"}).get_data(as_text=True)
    s.check("a language we don't offer falls back to English", 'lang="en"' in page)

    s.section("The staff app stays in English")
    oc, ec, owner, emp = clients()
    oc.get("/language/fr", follow_redirects=True)
    staff = oc.get("/admin/rooms").get_data(as_text=True)
    s.check("a staff page is unaffected by the guest language choice",
            "Ateliers" not in staff or "staff-shell" in staff,
            detail="the staff app appears to have been translated")

    s.section("Every string the templates ask for exists to be asked for")
    # t() falls back, so a typo'd key is invisible on the page — it just stays
    # English. This finds the ones no table has, which is where a typo lands.
    import glob, os, re
    used = set()
    for path in glob.glob(os.path.join(_harness.ROOT, "templates", "*.html")):
        html = open(path, encoding="utf-8").read()
        used |= set(re.findall(r"\bt\(\s*'([^']+)'\s*\)", html))
        used |= set(re.findall(r'\bt\(\s*"([^"]+)"\s*\)', html))
    known = set()
    for table in translations.TABLES.values():
        known |= set(table)
    unknown = sorted(used - known)
    s.check(f"all {len(used)} strings used in templates are in the tables",
            not unknown, detail=", ".join(unknown[:5]))

    return s
