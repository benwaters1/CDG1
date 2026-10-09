"""Every owner page, read for the faults that render perfectly.

WHAT WENT WRONG. On 9 October 2026 every page the owner can open without an
argument -- 232 of them -- was rendered on a copy of the real database,
screenshotted, and read. These were found by reading, not by any check, because
each one rendered every word it was meant to and errored nowhere:

    THE WORD "None" IN A BOX. Jinja writes a Python None out as "None", so an
    empty column came up as the word, inside the input, ready to be saved. The
    house card on Company & Insurance could not be saved at all -- the browser
    refused "None" as an email address -- so the limit on guests in the house
    could not be changed until somebody cleared two boxes. On The Menu, 14 of
    25 dishes have no description, and changing one's price wrote "None" into
    the description guests read.

    THE SAME CHIP TWICE. Email Wording drew "Rooms 14" twice: two prefixes map
    to Rooms, and the chip order was built from that table without removing the
    repeat.

    THE TEMPLATE'S INDENTATION, PRINTED. `.manual-body` keeps line breaks, as
    it should for a note somebody typed. Sixteen places wrapped a sentence the
    page composes in it, split across the template's lines -- so the template's
    indentation printed mid-sentence, and Booking Requests drew a blank band in
    every card.

    THINGS ABOVE THE PAGE'S NAME. The Wages warning, the Social schedule's
    settings form (its title sat halfway down the page) and the Manual's date
    all came before the title, where a line reads as the end of the page before.

    TWO PAGES, ONE NAME. "What a night costs" was two different pages, and
    "Ask HR" was both the page the team writes from and the one the owner reads.

    A STYLE NOTHING READ. The Social schedule's rules for suggested captions
    sat after the template's last block, which an extending template never
    renders.

    A WARNING THAT NAMED NOBODY. Wages said "4 without a wage on file" and then
    printed three dots: it read r['name'] from rows that hold r['person'].

WHY A SWEEP. Each was one page, and the next one will be a different page. So
the checks run over every page the owner can open, and a page added tomorrow is
covered without anybody remembering to list it.
"""
import glob
import os
import re
from html.parser import HTMLParser

from _harness import Suite, clients, db, forms_on
import _harness

m = _harness.m
TAG = "ZZREADCLEAN"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Opening these is not reading them: one ends the session the rest of the
# sweep is using, one changes the language every later page is read in, and
# one builds a zip of the whole database.
NOT_OPENED = {"logout", "set_language", "download_backup", "static"}

# Pages whose first words are not their title, on purpose, and why. Checked
# both ways: a page that starts putting its title first comes off the list.
TITLE_NOT_FIRST = {
    "dashboard": "the owner home opens on the day and the week above the greeting",
    "pass_screen": "the kitchen's pass screen, a wall display with no title at all",
    "photo_intake": "the photo drop is drawn in the public site's own design",
    "forgot_password": "a sign-in card: the house's name sits over the form, as on the login page",
    "reset_password": "the same sign-in card",
}

_PAGES = None


def owner_pages(oc):
    """(endpoint, url, html) for every staff page the owner opens with no arguments.

    Found from the URL map rather than listed, so a page nobody remembered to
    add is read anyway. Only the staff side: a page drawn on the public base
    belongs to the design side and arrives by handover. Read once per run and
    shared, because the browser suite reads the same set.
    """
    global _PAGES
    if _PAGES is not None:
        return _PAGES
    pages = []
    for rule in sorted(m.app.url_map.iter_rules(), key=lambda r: r.rule):
        if "GET" not in rule.methods or rule.arguments or rule.endpoint in NOT_OPENED:
            continue
        r = oc.get(rule.rule)
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            continue
        html = r.get_data(as_text=True)
        if 'class="topbar-search"' not in html:
            continue
        pages.append((rule.endpoint, rule.rule, html))
    _PAGES = pages
    return pages


class _Page(HTMLParser):
    """What the sweep needs from one page: inside <main> only, so the menu and
    the header -- the same on every page -- are not read 232 times."""

    VOID = {"br", "input", "img", "hr", "meta", "link", "source", "wbr", "col", "area"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_main = False
        self.stack = []
        self.none_boxes = []
        self.none_text = []
        self.h1 = []
        self._h1 = None
        self._textarea = None
        self.first = None
        self.facets = []
        self._facet = None
        self._chip = None
        self._last = ""

    def _classes(self, a):
        return (a.get("class") or "").split()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "main":
            self.in_main = True
            self.stack = []
            return
        if not self.in_main:
            return
        if tag == "input" and a.get("value") == "None" and a.get("type") != "hidden":
            self.none_boxes.append(a.get("name") or "(unnamed)")
        if tag in self.VOID:
            return
        cls = self._classes(a)
        self.stack.append((tag, cls, a.get("id") or ""))
        if tag == "h1":
            self._h1 = ""
        if tag == "textarea":
            self._textarea = [a.get("name") or "(unnamed)", ""]
        if "facet-row" in cls:
            self._facet = {"label": "", "chips": []}
            self.facets.append(self._facet)
        if tag == "a" and "chip" in cls and self._facet is not None:
            self._chip = ""

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False
            return
        if not self.in_main or tag in self.VOID:
            return
        while self.stack:
            t, cls, _id = self.stack.pop()
            if "facet-row" in cls:
                self._facet = None
            if t == tag:
                break
        if tag == "h1" and self._h1 is not None:
            self.h1.append(" ".join(self._h1.split()))
            self._h1 = None
        if tag == "textarea" and self._textarea is not None:
            if self._textarea[1].strip() == "None":
                self.none_boxes.append(self._textarea[0])
            self._textarea = None
        if tag == "a" and self._chip is not None:
            # "Rooms 14": the label is everything before the count.
            words = self._chip.split()
            label = " ".join(words[:-1]) if words and words[-1].isdigit() else " ".join(words)
            if self._facet is not None:
                self._facet["chips"].append(label)
            self._chip = None

    def handle_data(self, data):
        if not self.in_main:
            return
        if self._h1 is not None:
            self._h1 += data
        if self._textarea is not None:
            self._textarea[1] += data
        if self._chip is not None:
            self._chip += data
        if self._facet is not None and self.stack and "facet-label" in self.stack[-1][1]:
            self._facet["label"] += data.strip()
        text = data.strip()
        if not text:
            return
        if any(t in ("script", "style", "template", "textarea", "option") for t, _c, _i in self.stack):
            return
        if text == "None":
            self.none_text.append(f"a {self.stack[-1][0] if self.stack else '?'}"
                                  f" after “{self._last[-50:]}”")
        self._last = text
        if self.first is None:
            # The flashes and the (hidden) push prompt come from base.html,
            # above every page's content, and are not the page's first words.
            if any("flash-wrap" in c or i == "push-prompt" for _t, c, i in self.stack):
                return
            in_title = any(t == "h1" or "page-head" in c for t, c, _i in self.stack)
            self.first = (in_title, text[:60])


def read(html):
    p = _Page()
    p.feed(html)
    return p


def _template_sources():
    for path in sorted(glob.glob(os.path.join(ROOT, "templates", "*.html"))):
        with open(path, encoding="utf-8") as fh:
            yield os.path.basename(path), fh.read()


def _manual_bodies_spanning_lines(name, src):
    """Every manual-body element whose source runs over more than one line,
    without `wraps`: pre-wrap will print the template's own line breaks."""
    found = []
    for mt in re.finditer(r'<(\w+)\b[^>]*class="([^"]*\bmanual-body\b[^"]*)"[^>]*>', src):
        tag, classes = mt.group(1), mt.group(2).split()
        depth, i = 1, mt.end()
        close = re.compile(r"<(/?)%s\b" % tag)
        end = None
        while depth:
            nxt = close.search(src, i)
            if not nxt:
                break
            depth += -1 if nxt.group(1) else 1
            i, end = nxt.end(), nxt.start()
        inner = src[mt.end():end] if end is not None else ""
        if "\n" in inner and "wraps" not in classes:
            found.append(f"{name}:{src.count(chr(10), 0, mt.start()) + 1}")
    return found


def run():
    s = Suite("owner pages read cleanly")
    oc, _ec, _owner, _emp = clients()

    s.section("Nothing prints as the word None")
    s.check("an empty value renders as nothing",
            m.app.jinja_env.from_string("[{{ x }}]").render(x=None) == "[]",
            detail=repr(m.app.jinja_env.from_string("[{{ x }}]").render(x=None)))

    conn = db()
    had = conn.execute("SELECT * FROM company_info WHERE id = 1").fetchone()
    try:
        if had:
            conn.execute("""UPDATE company_info SET accountant_name = NULL,
                            accountant_email = NULL, insurance_broker_email = NULL
                            WHERE id = 1""")
        else:
            conn.execute("""INSERT INTO company_info (id, legal_name, updated_at)
                            VALUES (1, ?, ?)""", (TAG, _harness.datetime_now()))
        conn.execute("""INSERT INTO menu_items (name, description, category, dietary_tags,
                        price, active, sort_order, created_at)
                        VALUES (?, NULL, 'main', NULL, 18, 1, 999, ?)""",
                     (TAG + " Truite", _harness.datetime_now()))
        dish = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.commit()

        page = oc.get("/management/company-info")
        house = [f for f in forms_on(page.get_data(as_text=True))
                 if any(x["name"] == "accountant_email" for x in f["fields"])]
        boxes = {x["name"]: x["value"] for f in house for x in f["fields"]}
        s.check("Company & Insurance draws the house card", page.status_code == 200 and house,
                page, detail=f"status {page.status_code}")
        s.check("with an empty accountant and broker left empty, so the card can be saved",
                house and boxes.get("accountant_email") == "" and
                boxes.get("insurance_broker_email") == "" and boxes.get("accountant_name") == "",
                detail=f"{ {k: v for k, v in boxes.items() if 'accountant' in k or 'broker' in k} }")

        page = oc.get("/admin/restaurant/menu")
        edit = [f for f in forms_on(page.get_data(as_text=True))
                if (f["action"] or "").endswith(f"/{dish}/edit")]
        fields = {x["name"]: x["value"] for f in edit for x in f["fields"]}
        s.check("a dish with no description has an edit form",
                page.status_code == 200 and edit, page,
                detail=f"no form posting to .../{dish}/edit")
        s.check("whose description and dietary boxes are empty, so saving a price "
                "does not write 'None' for guests to read",
                edit and fields.get("description") == "" and fields.get("dietary_tags") == "",
                detail=f"description={fields.get('description')!r}, "
                       f"dietary_tags={fields.get('dietary_tags')!r}")
    finally:
        conn.execute("DELETE FROM menu_items WHERE name LIKE ?", (TAG + "%",))
        if had:
            conn.execute("""UPDATE company_info SET accountant_name = ?, accountant_email = ?,
                            insurance_broker_email = ? WHERE id = 1""",
                         (had["accountant_name"], had["accountant_email"],
                          had["insurance_broker_email"]))
        else:
            conn.execute("DELETE FROM company_info WHERE legal_name = ?", (TAG,))
        conn.commit()
        conn.close()

    pages = owner_pages(oc)
    s.section(f"Read across every owner page ({len(pages)})")
    s.check("the sweep found the owner's pages", len(pages) > 150,
            detail=f"only {len(pages)} -- the URL map or the staff base marker has moved")
    parsed = [(ep, url, read(html)) for ep, url, html in pages]

    boxes = [f"{url} ({', '.join(p.none_boxes)})" for _ep, url, p in parsed if p.none_boxes]
    s.check("no box on any page is filled with the word None", not boxes,
            detail="; ".join(boxes))
    text = [f"{url} ({'; '.join(sorted(set(p.none_text))[:3])})"
            for _ep, url, p in parsed if p.none_text]
    s.check("and no page says None where a value should be", not text,
            detail="; ".join(text))

    conn = db()
    try:
        with m.app.test_request_context("/"):
            m.log_audit(conn, TAG.lower() + "_none", target=str(None), details="None")
        row = conn.execute("SELECT target, details FROM audit_log WHERE action = ?",
                           (TAG.lower() + "_none",)).fetchone()
        s.check("and the audit log does not keep the word None as what was acted on",
                row is not None and row["target"] is None and row["details"] is None,
                detail=str(dict(row)) if row else "not written")
        # A line written before that, holding the word, as the live log may.
        conn.execute("INSERT INTO audit_log (action, target, created_at) VALUES (?, 'None', ?)",
                     (TAG.lower() + "_none_old", _harness.datetime_now()))
        conn.commit()
        shown = read(oc.get("/admin/audit-log?q=" + TAG.lower() + "_none_old")
                     .get_data(as_text=True))
        s.check("nor shows it, for a line that was written before it refused to",
                not shown.none_text, detail="; ".join(shown.none_text))
    finally:
        conn.execute("DELETE FROM audit_log WHERE action IN (?, ?)",
                     (TAG.lower() + "_none", TAG.lower() + "_none_old"))
        conn.commit()
        conn.close()

    s.section("No chip twice in one row")
    s.check("a classification keeps the first place a name appears and drops the repeat",
            m.facet("k", "K", lambda r: r, order=["Rooms", "Events", "Rooms"])["order"]
            == ["Rooms", "Events"])
    rows = sum(len(p.facets) for _ep, _u, p in parsed)
    s.check("there are chip rows to read", rows > 40, detail=f"{rows} rows")
    twice = []
    for _ep, url, p in parsed:
        for f in p.facets:
            seen = set()
            for label in f["chips"]:
                if label in seen:
                    twice.append(f"{url}: {f['label']} → {label}")
                seen.add(label)
    s.check("no row of chips offers the same choice twice", not twice,
            detail="; ".join(twice))
    wording = next((p for _ep, url, p in parsed if url == "/management/email-templates"), None)
    part = [f for f in (wording.facets if wording else []) if f["label"].lower().startswith("part")]
    s.check("Email Wording offers Rooms once",
            part and part[0]["chips"].count("Rooms") == 1,
            detail=f"{part[0]['chips'] if part else 'no Part of the house row'}")

    s.section("The page's name comes first")
    above, exempt_ok = [], []
    for ep, url, p in parsed:
        if p.first is None or p.first[0]:
            if ep in TITLE_NOT_FIRST:
                exempt_ok.append(ep)
            continue
        if ep not in TITLE_NOT_FIRST:
            above.append(f"{url}: “{p.first[1]}”")
    s.check("nothing is printed above a page's title", not above, detail="; ".join(above))
    s.check("every page excused from that still needs the excuse", not exempt_ok,
            detail="now title-first, take off TITLE_NOT_FIRST: " + ", ".join(exempt_ok))
    seen_eps = {ep for ep, _u, _p in parsed}
    gone = sorted(set(TITLE_NOT_FIRST) - seen_eps)
    s.check("and every excused page still exists", not gone, detail=", ".join(gone))

    s.section("No two pages share a name")
    by_title = {}
    for ep, url, p in parsed:
        if p.h1:
            by_title.setdefault(p.h1[0], []).append(url)
    shared = [f"“{t}”: {', '.join(urls)}" for t, urls in by_title.items()
              if len(urls) > 1]
    s.check("every owner page has its own title", not shared, detail="; ".join(shared))

    s.section("Wages names who has no wage")
    conn = db()
    try:
        conn.execute("""INSERT INTO users (name, email, password_hash, role, status, created_at)
                        VALUES (?, ?, 'x', 'employee', 'active', ?)""",
                     (TAG + " Unpriced", TAG.lower() + ".unpriced@example.invalid",
                      _harness.datetime_now()))
        conn.commit()
        page = oc.get("/admin/payroll/wages").get_data(as_text=True)
        block = re.search(r'<div class="pass-short".*?</div>', page, re.S)
        s.check("the warning is on the page", block is not None)
        s.check("and names the person it counts",
                block is not None and TAG + " Unpriced" in block.group(0),
                detail=(" ".join(re.sub(r"<[^>]+>", " ", block.group(0)).split())[:160]
                        if block else ""))
        title = page.find("<h1>")
        s.check("under the page's title, not above it",
                block is not None and -1 < title < block.start())
    finally:
        conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
        conn.close()

    s.section("In the templates themselves")
    spans = [hit for name, src in _template_sources()
             for hit in _manual_bodies_spanning_lines(name, src)]
    s.check("a manual-body written over several lines is marked to wrap", not spans,
            detail="add `wraps` (text the page composes) or write it on one line "
                   "(text somebody typed): " + ", ".join(spans))
    with open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8") as fh:
        css = fh.read()
    s.check("and `wraps` sets the text flowing normally",
            re.search(r"\.manual-body\.wraps\s*\{[^}]*white-space\s*:\s*normal", css) is not None)
    first = []
    for name, src in _template_sources():
        if '{% extends "base.html" %}' not in src or '<div class="page-head"' not in src:
            continue
        opened = src.find("{% block content %}")
        before = src[opened:src.index('<div class="page-head"')] if opened >= 0 else ""
        # What prints nothing: comments, macros (defined here, drawn later),
        # and the tags that only set things up.
        before = re.sub(r"\{#.*?#\}", "", before, flags=re.S)
        before = re.sub(r"\{%-?\s*macro\b.*?\{%-?\s*endmacro\s*-?%\}", "", before, flags=re.S)
        if re.search(r"<[a-zA-Z]", before):
            first.append(name)
    s.check("and in the templates, nothing is drawn before a page's title -- a "
            "warning that only appears with data is still in the wrong place",
            not first, detail=", ".join(first))
    stranded = []
    for name, src in _template_sources():
        if "{% extends" not in src or "{% endblock %}" not in src:
            continue
        tail = src[src.rindex("{% endblock %}") + len("{% endblock %}"):]
        if re.sub(r"\{#.*?#\}", "", tail, flags=re.S).strip():
            stranded.append(name)
    s.check("nothing is written after an extending template's last block, where it "
            "never renders", not stranded, detail=", ".join(stranded))
    return s


if __name__ == "__main__":
    print(run().report())
