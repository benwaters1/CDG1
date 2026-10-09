"""All settings lists every place a setting lives -- and keeps doing so.

WHAT WENT WRONG. The page's own docstring said "everything the owner can set,
in one place", and it held eleven cards. The restaurant's settings, wages,
leave, card fees, the terms, the review link, the channels' commission, the
card reader, texting, the inboxes and more were each on a page of their own and
nowhere here -- found, if at all, by remembering they existed.

WHAT THIS PINS. Read from the source rather than a list somebody keeps:
every route that saves something to the settings table is found, the page
its form sits on is worked out from the templates, and that page must be on
All settings. A new settings page that nobody adds turns this red.
"""
import glob
import os
import re

from _harness import Suite, clients
import _harness

m = _harness.m
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Routes that write the settings table without being somewhere the owner
# sets anything -- a timestamp, a token, a cursor. Each with its reason, and
# each must still be found by the scan: a reason for something that no
# longer happens is how a real one hides.
NOT_SETTINGS = {
    "edit_own_contact_info": "a member of staff's own contact details",
    "regenerate_supplier_link": "a new token for the supplier upload link",
    "supplier_invoice_submit": "a supplier's upload, from outside",
    "workshop_feedback": "a guest's feedback, from outside",
}

SAVES_A_SETTING = re.compile(r"app_settings|set_setting\(|save_setting\(|_save_money_setting|"
                             r"upsert_setting|set_house_setting|set_app_setting")


def settings_writers(src):
    """{endpoint: rule} for every POST route whose body saves a setting."""
    found = {}
    for block in re.split(r"\n(?=@app\.route\()", src):
        head = re.match(r'@app\.route\("([^"]+)"(?:, methods=\[([^\]]*)\])?\)', block)
        fn = re.search(r"\ndef (\w+)\(", block)
        if not head or not fn or "POST" not in (head.group(2) or ""):
            continue
        body = block[fn.start():]
        nxt = re.search(r"\n(?:def |@app\.)", body[1:])
        body = body[:nxt.start() + 1] if nxt else body
        if SAVES_A_SETTING.search(body):
            found[fn.group(1)] = head.group(1)
    return found


def pages_rendering(src):
    """{template: {endpoints that render it}}."""
    out = {}
    for block in re.split(r"\n(?=def )", src):
        fn = re.match(r"def (\w+)\(", block)
        if not fn:
            continue
        for t in re.findall(r'render_template\(\s*"([\w/]+\.html)"', block):
            out.setdefault(t, set()).add(fn.group(1))
    return out


def page_of(writer, src, templates, rendered):
    """Where somebody sets what a writer saves: the writer itself when it is a
    page that saves itself, otherwise the pages whose FORM posts to it. A
    page that merely links to a settings page is not where it is set -- the
    restaurant's deposit rules link to its settings, and counting that let
    the settings themselves go missing unnoticed."""
    if writer in m.app.view_functions:
        rule = next((r for r in m.app.url_map.iter_rules() if r.endpoint == writer), None)
        if rule and "GET" in rule.methods:
            return {writer}
    pages = set()
    posts_here = re.compile(r"""action="\{\{\s*url_for\('%s'""" % re.escape(writer))
    for name, text in templates.items():
        if name == "base.html" or not posts_here.search(text):
            continue
        holders = {name}
        # A form in a partial is on whichever page includes it.
        if name.startswith("_"):
            holders = {n for n, t in templates.items() if f'"{name}"' in t or f"'{name}'" in t}
        for h in holders:
            pages |= rendered.get(h, set())
    return pages


def run():
    s = Suite("All settings lists every place a setting lives")
    oc, ec, _o, _e = clients()
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    templates = {os.path.basename(f): open(f, encoding="utf-8").read()
                 for f in glob.glob(os.path.join(ROOT, "templates", "*.html"))}
    rendered = pages_rendering(src)
    places = [ep for _g, _i, entries in m.SETTINGS_PLACES for ep, _t, _w in entries]

    s.section("The page")
    r = oc.get("/management")
    page = r.get_data(as_text=True)
    s.check("draws", r.status_code == 200, detail=str(r.status_code))
    with m.app.test_request_context():
        hrefs = {ep: m.url_for(ep) for ep in places if ep in m.app.view_functions}
    s.check("every place on the list is a page that exists",
            len(hrefs) == len(places), detail=str(sorted(set(places) - set(hrefs))))
    # Below the title only: the sidebar links to half of these anyway, and a
    # card that stopped linking would hide behind it.
    content = page[page.find("<h1>All settings</h1>"):]
    unlinked = sorted(ep for ep, h in hrefs.items() if f'href="{h}"' not in content)
    s.check("and every one of them is on the page", not unlinked, detail=", ".join(unlinked))
    s.check("each once", len(places) == len(set(places)),
            detail=str(sorted({p for p in places if places.count(p) > 1})))
    s.check("under its heading",
            all(f'class="section-heading">{g}</h2>' in page.replace("&amp;", "&")
                for g, _i, _e in m.SETTINGS_PLACES))
    s.check("and each opens", all(oc.get(h).status_code == 200 for h in hrefs.values()),
            detail=str([ep for ep, h in hrefs.items() if oc.get(h).status_code != 200]))

    s.section("Nowhere a setting is saved is missing from it")
    writers = settings_writers(src)
    s.check("the scan finds the routes that save settings", len(writers) >= 20,
            detail=f"{len(writers)} found")
    missing = []
    for writer in sorted(writers):
        if writer in NOT_SETTINGS:
            continue
        pages = page_of(writer, src, templates, rendered)
        if not pages & set(places):
            missing.append(f"{writer} (form on {sorted(pages) or 'no page found'})")
    s.check("every place a setting is saved is on All settings", not missing,
            detail="; ".join(missing[:5]))
    stale = sorted(set(NOT_SETTINGS) - set(writers))
    s.check("and every 'not a setting' still saves something", not stale,
            detail=", ".join(stale))
    planted = src + '''

@app.route("/admin/new-thing/settings", methods=["POST"])
@owner_required
def save_new_thing_settings():
    conn = get_db()
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('x', '1')")
'''
    s.check("the scan can see a new one",
            "save_new_thing_settings" in settings_writers(planted)
            and not page_of("save_new_thing_settings", planted, templates, rendered) & set(places))

    s.section("An employee sees none of it")
    s.check("All settings is not theirs", ec.get("/management").status_code in (302, 403))
    return s


if __name__ == "__main__":
    print(run().report())
