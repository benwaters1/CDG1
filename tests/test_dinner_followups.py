"""What the live check of the dinner build found (brief of 27 Nov 2026, BRIEF-dinner-followups-27-nov).

  1. TWO TABS. /admin/stay-occasions opens on Your occasions, with Suggested (N) behind the second tab, N the
     suggestions still to decide. An occasion's form has The stay and Chef & dinner, one form across both; a save
     refused for something on the other tab opens that tab and says so, and one that went through as a draft
     because the dinner still needs something comes back on Chef & dinner. The script that switches them
     (static/tabs.js) is run in Chrome: the tab in the address, a click, a save keeping it, a box the browser
     refuses on a hidden tab, a tab opened by the server.
  2. ANY LANGUAGE ("do I have to type in French, we don't all speak French"). A menu typed in English alone saves
     with French on every line, marked "French suggested, not checked" on the builder until ticked or edited; one in
     Spanish alone gets French and English. A cook who wrote the French and left the English blank is not
     overruled. The printed menus never carry the mark.
  3. THE EMPTY LIST reads as one sentence: the panel is set as text, not a column of pieces, measured in Chrome.
  4. THE DISH LIBRARY: each dish and wine with Edit and Remove; a menu that serves one keeps its words; the two
     dishes the live check left are removed once, and either can be written again after.
  5. LES GÎTES DE BEILLE: next door, under a hundred metres to our gates at the foot of the lane, then a short walk up.

Nothing reaches the assistant (stood in), the mail or Stripe.
"""
import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import timedelta

from werkzeug.datastructures import MultiDict

from _harness import Suite, clients, db, ensure_room, flashes, free_window, keep_rows, visible_text
import _harness
from test_staff_header_on_a_phone import find_browser

m = _harness.m
TAG = "ZDFU"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What the stood-in assistant knows: each line by the words it was typed in.
WORDS = {
    "Roast duck breast with ZDFU cherries": {"fr": "Magret de canard aux cerises ZDFU", "es": "Magret de pato ZDFU"},
    "ZDFU walnut tart": {"fr": "Tarte aux noix ZDFU", "es": "Tarta de nueces ZDFU"},
    "Red · ZDFU AOP": {"fr": "Rouge · ZDFU AOP", "es": "Tinto · ZDFU AOP"},
    "ZDFU duck": {"fr": "Canard ZDFU", "es": "Pato ZDFU"},
    "Cordero asado con tomillo": {"fr": "Agneau rôti au thym, façon ZDFU", "en": "Roast lamb with thyme, ZDFU style"},
    "Cordero ZDFU": {"fr": "Agneau ZDFU", "en": "ZDFU lamb"},
    "Tarta de queso": {"fr": "Gâteau au fromage ZDFU", "en": "ZDFU cheesecake"},
}


def lang_form(name, lines, **extra):
    """The builder's form as the page posts it: lines are (course, {field: words})."""
    md = MultiDict({"name": name, "price": "80", "service_time": "19:30"})
    for i, (course, fields) in enumerate(lines):
        md.add(f"c-{i}-pos", str(i))
        md.add(f"c-{i}-course_fr", course)
        md.add(f"c-{i}-fm", "1")
        for k, v in fields.items():
            md.add(f"c-{i}-{k}", v)
    for k, v in extra.items():
        md[k] = v
    return md


def _menu(conn, name):
    return conn.execute("SELECT * FROM dinner_menus WHERE name = ?", (name,)).fetchone()


def _courses(conn, menu_id):
    return [dict(r, unchecked=m._marks(r["unchecked"])) for r in conn.execute(
        "SELECT * FROM dinner_menu_courses WHERE menu_id = ? ORDER BY position", (menu_id,))]


def _between(page, start, end):
    a = page.find(start)
    b = page.find(end, a + len(start)) if a >= 0 else -1
    return page[a:b] if a >= 0 and b > a else ""


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%'")]
    marks = ",".join("?" * len(ids)) or "NULL"
    conn.execute(f"DELETE FROM campaign_templates WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM stay_occasions WHERE id IN ({marks})", ids)
    conn.execute(f"DELETE FROM dinner_menus WHERE name LIKE '{TAG}%'")
    conn.execute("DELETE FROM dinner_dishes WHERE dish_fr LIKE '%ZDFU%' OR dish_en LIKE '%ZDFU%'")
    conn.execute("DELETE FROM dinner_wines WHERE wine_fr LIKE '%ZDFU%'")
    conn.commit()


def run():
    s = Suite("Dinner follow-ups: two tabs, a menu in any language, the empty list, the dish library, the gîtes")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasions", "status = 'live'"),
                keep_rows(conn, "app_settings", "key = 'live_check_dishes_removed_2026_11_27'")]
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.commit()
    _cleanup(conn)
    was = (m.menu_lines_ask, m.dinner_menu_rows)
    try:
        _tabs(s, oc, conn)
        _languages(s, oc, conn)
        _empty_list(s, oc)
        _library(s, oc, conn)
        _gites(s)
        _in_chrome(s, oc, conn)
    finally:
        m.menu_lines_ask, m.dinner_menu_rows = was
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


# ---------------------------------------------------------------------------------------------------------------
def _new_occasion(oc, conn, name, nights=3):
    room = ensure_room()
    first = free_window(room, nights, after_days=260, clear_of_ateliers=True)
    last = first + timedelta(days=nights - 1)
    oc.post("/admin/stay-occasions/new", data={"name": name, "first_night": first.isoformat(),
                                               "last_night": last.isoformat(), "kind": "stay", "photo": ""})
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _tabs(s, oc, conn):
    s.section("1. The stay occasions open on your own, the suggestions behind a tab with their count")
    r = oc.get("/admin/stay-occasions")
    page = r.get_data(as_text=True)
    s.check("the list answers", r.status_code == 200, r)
    to_decide = page.count("/dismiss\"")
    s.check("two tabs: Your occasions, then Suggested with the count still to decide",
            re.search(r'data-tab="yours"[^>]*>Your occasions</a>', page) is not None
            and f'Suggested <span class="page-tab__n">({to_decide})</span>' in page,
            detail=f"{to_decide} suggestions on the page")
    s.check("the owner's own come first in the page, so without script they are first too",
            0 <= page.find('data-tab-panel="yours"') < page.find('data-tab-panel="suggested"'))
    yours = _between(page, 'data-tab-panel="yours"', 'data-tab-panel="suggested"')
    s.check("search, chips and sort stay on Your occasions", 'class="list-toolbar"' in yours
            and 'class="list-toolbar"' not in page.replace(yours, ""), detail=yours[:200])
    s.check("and the script that switches them is on the page", "/static/tabs.js" in page)

    s.section("1. An occasion's form: The stay, and Chef & dinner")
    occ = _new_occasion(oc, conn, f"{TAG} Tabs week")
    s.check("an occasion to try it on", occ is not None)
    if occ is None:
        return
    r = oc.get(f"/admin/stay-occasions/{occ['id']}")
    page = r.get_data(as_text=True)
    s.check("the form answers", r.status_code == 200, r)
    s.check("with its two tabs", 'href="#the-stay"' in page and ">The stay</a>" in page
            and 'href="#chef-and-dinner"' in page and ">Chef &amp; dinner</a>" in page)
    stay = _between(page, 'id="tp-stay"', 'id="tp-dinner"')
    dinner = _between(page, 'id="tp-dinner"', "</form>")
    for name, what in [("name", "the name"), ("first_night", "the dates"), ("kind", "what it is"),
                       ("photo", "the photograph"), ("status", "the state"), ("fill_uplift", "the rooms' prices"),
                       ("description", "the words"), ("home_banner", "the home page")]:
        s.check(f"The stay holds {what}", f'name="{name}"' in stay and f'name="{name}"' not in dinner)
    for name, what in [("chef_on_site", "the chef on site"), ("serve_dinner", "the dinner"),
                       ("dinner_menu_id", "the menu"), ("dinner_price", "the dinner's price"),
                       ("dinner_covers", "the covers"), ("chef_name", "the guest chef")]:
        s.check(f"Chef & dinner holds {what}", f'name="{name}"' in dinner and f'name="{name}"' not in stay)
    s.check("one form across both, so one Save keeps both", page.count('id="occasion-form"') == 1
            and page.find('id="occasion-form"') < page.find('id="tp-stay"') < page.find('id="tp-dinner"')
            < page.find("Saves both tabs"))
    s.check("nothing is opened for you when nothing is wrong", "data-open=" not in page)

    s.section("1. A save refused for something on the other tab opens that tab and says so")
    base = {"name": occ["name"], "first_night": occ["first_night"], "last_night": occ["last_night"],
            "kind": "stay", "status": "draft", "photo": "", "description": ""}
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(base, serve_dinner="1", tab_shown="stay"))
    page = r.get_data(as_text=True)
    s.check("a dinner with no price is refused", r.status_code == 200
            and "Dinner at Gudanes needs a price a person." in " ".join(flashes(r)), detail=flashes(r))
    s.check("Chef & dinner opens", 'data-open="dinner"' in page)
    s.check("and says so", "The problem is on Chef &amp; dinner, so it is open." in page
            and re.search(r'id="occ-tabs-note"[^>]*hidden', page) is None)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(base, serve_dinner="1", tab_shown="dinner"))
    page = r.get_data(as_text=True)
    s.check("already looking at Chef & dinner: it stays open, nothing said about moving",
            'data-open="dinner"' in page and "so it is open" not in page)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(base, name="", tab_shown="dinner"))
    page = r.get_data(as_text=True)
    s.check("a name emptied while on Chef & dinner opens The stay, and says so",
            'data-open="stay"' in page and "The problem is on The stay, so it is open." in page)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(base, chef_link="not a link", tab_shown="stay"))
    s.check("the guest chef's link is on Chef & dinner too", 'data-open="dinner"' in r.get_data(as_text=True))

    s.section("1. On sale refused for the dinner: saved as a draft, back on Chef & dinner")
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(
        base, status="live", serve_dinner="1", dinner_price="60", dinner_covers="10", tab_shown="stay"))
    where = r.headers.get("Location", "")
    s.check("saved, as a draft: the dinner has no menu", r.status_code == 302 and conn.execute(
        "SELECT status FROM stay_occasions WHERE id = ?", (occ["id"],)).fetchone()["status"] == "draft",
        detail=f"HTTP {r.status_code}")
    s.check("and sent back to Chef & dinner", "problem=dinner" in where and where.endswith("#chef-and-dinner"),
            detail=where)
    page = oc.get(where).get_data(as_text=True)
    s.check("which opens there and says why", 'data-open="dinner"' in page
            and "The problem is on Chef &amp; dinner" in page and "needs a menu" in page)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}", data=dict(
        base, status="live", serve_dinner="1", dinner_price="60", dinner_covers="10", tab_shown="dinner"))
    s.check("already on Chef & dinner: back to the page as it was", r.headers.get("Location", "").endswith(
        f"/admin/stay-occasions/{occ['id']}"), detail=r.headers.get("Location"))


# ---------------------------------------------------------------------------------------------------------------
def _languages(s, oc, conn):
    asked = []

    def _ask(items):
        asked.append(items)
        out = []
        for it in items:
            src = next(iter(it["known"].values()))
            w = WORDS.get(src, {})
            out.append({lang: (w.get(lang, "") if lang in it["want"] else "") for lang in ("fr", "en", "es")})
        return out
    m.menu_lines_ask = _ask

    s.section("2. The builder takes any of the three languages")
    page = oc.get("/admin/menus/new").get_data(as_text=True)
    s.check("each line has a French, an English and a Spanish box",
            all(f'name="c-0-{line}_{lang}"' in page for line in ("dish", "wine", "origin") for lang in ("fr", "en", "es")))
    s.check("and says so", "Write each dish in the language you know; the others are suggested in grey." in page)
    s.check("no example sits grey in a box, where a suggestion would", 'placeholder="e.g. Agneau' not in page)
    s.check("the menus page says it too", "Write each dish in the language you know; the others are suggested."
            in oc.get("/admin/menus").get_data(as_text=True))

    s.section("2. A menu typed in English alone saves with French on every line, marked")
    name = f"{TAG} English only"
    r = oc.post("/admin/menus/new", data=lang_form(name, [
        ("Plat", {"dish_en": "Roast duck breast with ZDFU cherries", "wine_en": "Red · ZDFU AOP",
                  "origin_en": "ZDFU duck", "country_fr": "France", "meat": "canard"}),
        ("Starter", {"dish_en": "ZDFU walnut tart"})]))
    menu = _menu(conn, name)
    s.check("saved", r.status_code == 302 and menu is not None, detail=f"HTTP {r.status_code}")
    if menu is None:
        return
    cs = _courses(conn, menu["id"])
    s.check("the French on every line, the assistant's",
            cs[0]["dish_fr"] == "Magret de canard aux cerises ZDFU" and cs[0]["wine_fr"] == "Rouge · ZDFU AOP"
            and cs[0]["origin_fr"] == "Canard ZDFU" and cs[1]["dish_fr"] == "Tarte aux noix ZDFU",
            detail=[(c["dish_fr"], c["wine_fr"], c["origin_fr"]) for c in cs])
    s.check("asked once, for every line at once", len(asked) == 1 and len(asked[0]) == 4, detail=asked)
    s.check("each marked: French suggested, not checked",
            set(cs[0]["unchecked"]) == {"dish_fr", "wine_fr", "origin_fr"} and set(cs[1]["unchecked"]) == {"dish_fr"},
            detail=[c["unchecked"] for c in cs])
    s.check("the English as typed, and not marked", cs[0]["dish_en"] == "Roast duck breast with ZDFU cherries"
            and "dish_en" not in cs[0]["unchecked"])
    s.check("the Spanish suggested is not saved: only the French the guest's menu needs", not cs[0]["dish_es"])
    s.check("'Starter' is the house's Entrée", cs[1]["course_fr"] == "Entrée" and cs[1]["course_en"] == "Starter",
            detail=(cs[1]["course_fr"], cs[1]["course_en"]))

    page = oc.get(f"/admin/menus/{menu['id']}").get_data(as_text=True)
    s.check("the builder marks it beside the box", "French suggested, not checked." in page
            and 'name="c-0-ok-dish_fr"' in page and 'name="c-0-sugg-dish_fr" value="Magret de canard aux cerises ZDFU"' in page)
    s.check("and not the English nobody needed to check", 'name="c-0-ok-dish_en"' not in page)
    for kind in ("a4", "a3", "allergens"):
        out = oc.get(f"/admin/menus/{menu['id']}/print/{kind}").get_data(as_text=True)
        s.check(f"the {kind} print has the French and never the mark",
                "Magret de canard aux cerises ZDFU" in out and "not checked" not in out and "suggested" not in out.lower())
    s.check("nor the guest's page drawn from the form",
            "not checked" not in oc.post("/admin/menus/preview", data=lang_form(name, [
                ("Plat", {"dish_en": "ZDFU walnut tart", "sugg-dish_fr": "Tarte aux noix ZDFU"})])).get_data(as_text=True))

    s.section("2. The mark stays until ticked or edited")
    back = lambda **over: lang_form(name, [  # noqa: E731 -- the builder posting the saved page back
        ("Plat", dict({"dish_fr": cs[0]["dish_fr"], "sugg-dish_fr": cs[0]["dish_fr"],
                       "dish_en": cs[0]["dish_en"], "wine_fr": cs[0]["wine_fr"], "sugg-wine_fr": cs[0]["wine_fr"],
                       "wine_en": cs[0]["wine_en"], "origin_fr": cs[0]["origin_fr"],
                       "sugg-origin_fr": cs[0]["origin_fr"], "origin_en": "ZDFU duck", "country_fr": "France",
                       "meat": "canard"}, **over)),
        ("Entrée", {"dish_fr": "Tarte aux noix ZDFU", "sugg-dish_fr": "Tarte aux noix ZDFU", "dish_en": "ZDFU walnut tart"})])
    asked.clear()
    oc.post(f"/admin/menus/{menu['id']}", data=back())
    cs2 = _courses(conn, menu["id"])
    s.check("saved again untouched: still marked, nothing asked", set(cs2[0]["unchecked"]) == {
        "dish_fr", "wine_fr", "origin_fr"} and not asked, detail=(cs2[0]["unchecked"], asked))
    oc.post(f"/admin/menus/{menu['id']}", data=back(**{"ok-dish_fr": "1", "wine_fr": "Rouge · ZDFU AOP 2019"}))
    cs3 = _courses(conn, menu["id"])
    s.check("ticked as right: checked", "dish_fr" not in cs3[0]["unchecked"], detail=cs3[0]["unchecked"])
    s.check("edited: checked, and the words are the cook's", "wine_fr" not in cs3[0]["unchecked"]
            and cs3[0]["wine_fr"] == "Rouge · ZDFU AOP 2019", detail=cs3[0])
    s.check("the one left alone still marked", "origin_fr" in cs3[0]["unchecked"])

    s.section("2. A menu typed in Spanish alone gets French and English")
    name_es = f"{TAG} Solo en español"
    asked.clear()
    r = oc.post("/admin/menus/new", data=lang_form(name_es, [
        ("Plat", {"dish_es": "Cordero asado con tomillo", "origin_es": "Cordero ZDFU", "country_fr": "España",
                  "meat": "agneau"}),
        ("Postre", {"dish_es": "Tarta de queso"})]))
    menu_es = _menu(conn, name_es)
    s.check("saved", r.status_code == 302 and menu_es is not None, detail=f"HTTP {r.status_code}")
    if menu_es is not None:
        cs = _courses(conn, menu_es["id"])
        s.check("French and English on every line", cs[0]["dish_fr"] == "Agneau rôti au thym, façon ZDFU"
                and cs[0]["dish_en"] == "Roast lamb with thyme, ZDFU style" and cs[0]["origin_fr"] == "Agneau ZDFU"
                and cs[0]["origin_en"] == "ZDFU lamb" and cs[1]["dish_fr"] == "Gâteau au fromage ZDFU"
                and cs[1]["dish_en"] == "ZDFU cheesecake", detail=[(c["dish_fr"], c["dish_en"]) for c in cs])
        s.check("both marked", {"dish_fr", "dish_en", "origin_fr", "origin_en"} <= set(cs[0]["unchecked"]),
                detail=cs[0]["unchecked"])
        s.check("the Spanish kept as typed", cs[0]["dish_es"] == "Cordero asado con tomillo")
        s.check("'España' is Espagne, in English Spain", cs[0]["country_fr"] == "Espagne"
                and cs[0]["country_en"] == "Spain", detail=(cs[0]["country_fr"], cs[0]["country_en"]))
        s.check("'Postre' is the house's Dessert", cs[1]["course_fr"] == "Dessert" and cs[1]["course_en"] == "Dessert")
        s.check("and the law's line reads in French", m._course_view(cs[0])["origin_line_fr"] == "Agneau ZDFU · origine : Espagne",
                detail=m._course_view(cs[0])["origin_line_fr"])

    s.section("2. What the page already suggested is used; a French written is not overruled")
    asked.clear()
    name_js = f"{TAG} From the page"
    oc.post("/admin/menus/new", data=lang_form(name_js, [
        ("Plat", {"dish_en": "Roast duck breast with ZDFU cherries", "sugg-dish_fr": "Magret ZDFU (from the page)"}),
        ("Plat", {"dish_fr": "Pigeon rôti façon ZDFU", "sugg-dish_en": "Roast pigeon"})]))
    menu_js = _menu(conn, name_js)
    cs = _courses(conn, menu_js["id"]) if menu_js else []
    s.check("the French the page showed in grey is the one saved, marked, and nothing asked",
            bool(cs) and cs[0]["dish_fr"] == "Magret ZDFU (from the page)" and "dish_fr" in cs[0]["unchecked"]
            and not asked, detail=(cs[:1], asked))
    s.check("a French written and an English left blank: the English stays blank",
            len(cs) > 1 and cs[1]["dish_en"] == "" and not cs[1]["unchecked"], detail=cs[1:])
    s.check("read straight from the form, the same", m.read_dinner_menu_form(MultiDict({
        "name": "x", "c-0-course_fr": "Plat", "c-0-dish_fr": "Pigeon rôti", "c-0-sugg-dish_en": "Roast pigeon"}))
        ["courses"][0]["dish_en"] == "")

    s.section("2. The library answers in any language")
    asked.clear()
    got = oc.post("/admin/menus/suggest", data={"kind": "dish", "en": "Roast duck breast with ZDFU cherries"}).get_json()
    s.check("English the library knows gives its French, the assistant not asked",
            got["fr"] in ("Magret ZDFU (from the page)", "Magret de canard aux cerises ZDFU"), detail=got)
    s.check("from the library", got["from"].get("fr") == "library" and not asked, detail=(got, asked))
    got = oc.post("/admin/menus/suggest", data={"kind": "course", "es": "Postre"}).get_json()
    s.check("a course in Spanish: the house's", got["fr"] == "Dessert" and got["en"] == "Dessert", detail=got)
    got = oc.post("/admin/menus/suggest", data={"kind": "country", "en": "Spain"}).get_json()
    s.check("a country in English: its French", got["fr"] == "Espagne", detail=got)

    s.section("2. No French to be had: not saved, and said why")

    def _boom(items):
        raise RuntimeError("no network here")
    m.menu_lines_ask = _boom
    name_x = f"{TAG} No French"
    r = oc.post("/admin/menus/new", data=lang_form(name_x, [("Plat", {"dish_en": "Something ZDFU unheard of"})]))
    page = r.get_data(as_text=True)
    s.check("refused", r.status_code == 400 and _menu(conn, name_x) is None, detail=f"HTTP {r.status_code}")
    s.check("saying the French is needed, and why", "write the dish in French" in page
            and "the guest’s menu is always in French" in page)
    m.menu_lines_ask = _ask


# ---------------------------------------------------------------------------------------------------------------
EMPTY_SENTENCE = ("No menus yet. Write the first one: the house’s seven courses are laid out for you.")


def _empty_list(s, oc):
    s.section("3. No menus: one sentence")
    real = m.dinner_menu_rows
    m.dinner_menu_rows = lambda conn, status="active": []
    try:
        r = oc.get("/admin/menus")
    finally:
        m.dinner_menu_rows = real
    page = r.get_data(as_text=True)
    para = re.search(r'<p class="empty-state" id="menus-empty">(.*?)</p>', page, re.S)
    s.check("the empty list answers", r.status_code == 200 and para is not None, r)
    text = re.sub(r"<[^>]+>", "", para.group(1)) if para else ""
    s.check("in one sentence, with the link inside it", text == EMPTY_SENTENCE
            and '<a href="/admin/menus/new">Write the first one</a>: the house' in para.group(1), detail=text)
    css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
    rule = re.search(r"\n\.empty-state\{([^}]*)\}", css)
    s.check("and the panel is set as text, not a column of pieces",
            rule is not None and "flex" not in rule.group(1) and "text-align:center" in rule.group(1).replace(" ", ""),
            detail=rule.group(1) if rule else "no .empty-state rule")
    _EMPTY_PAGE[0] = page


_EMPTY_PAGE = [None]


# ---------------------------------------------------------------------------------------------------------------
def _library(s, oc, conn):
    s.section("4. The dish library: every dish and wine, with Edit and Remove")
    name = f"{TAG} Library"
    oc.post("/admin/menus/new", data=lang_form(name, [
        ("Entrée", {"dish_fr": "Velouté ZDFU", "dish_en": "ZDFU soup", "dish_es": "Crema ZDFU",
                    "wine_fr": "Blanc ZDFU", "wine_en": "White ZDFU", "wine_glass": "12 cl"})]))
    menu = _menu(conn, name)
    dish = conn.execute("SELECT * FROM dinner_dishes WHERE dish_fr = 'Velouté ZDFU'").fetchone()
    wine = conn.execute("SELECT * FROM dinner_wines WHERE wine_fr = 'Blanc ZDFU'").fetchone()
    s.check("a dish and a wine in the library", menu is not None and dish is not None and wine is not None)
    if not (menu and dish and wine):
        return
    s.check("the Spanish kept in the library", dish["dish_es"] == "Crema ZDFU")
    r = oc.get("/admin/menus/library")
    page = r.get_data(as_text=True)
    s.check("the library answers", r.status_code == 200, r)
    s.check("listing both, each with Edit and Remove",
            f"/admin/menus/library/dish/{dish['id']}/remove" in page and f"/admin/menus/library/wine/{wine['id']}/remove" in page
            and f'href="/admin/menus/library/dish/{dish["id"]}">Velouté ZDFU</a>' in page)
    s.check("searchable, with counted chips", "Search a dish or a wine" in page and "Dishes" in page and "Wines" in page)
    s.check("reached from the menus and from the builder",
            "/admin/menus/library" in oc.get("/admin/menus").get_data(as_text=True)
            and "/admin/menus/library" in oc.get("/admin/menus/new").get_data(as_text=True))
    s.check("the staff side's restaurant area reaches it", all(
        m.ENDPOINT_AREA.get(e) == "restaurant" for e in
        ("admin_dinner_library", "admin_dinner_library_item", "admin_dinner_library_remove", "admin_dinner_menu_suggest")))
    s.check("the French marked when nobody checked it",
            "French suggested, not checked" in oc.get("/admin/menus/library?q=Magret").get_data(as_text=True))

    s.section("4. Edit: the builder offers it that way from now on; the menu keeps its words")
    r = oc.get(f"/admin/menus/library/dish/{dish['id']}")
    s.check("the dish's page answers", r.status_code == 200 and 'name="dish_fr"' in r.get_data(as_text=True), r)
    r = oc.post(f"/admin/menus/library/dish/{dish['id']}", data={
        "dish_fr": "Velouté ZDFU", "dish_en": "ZDFU velouté", "dish_es": "Crema ZDFU", "meat": "agneau"})
    s.check("meat with no country is refused, as in the builder", r.status_code == 400
            and "French law asks it" in r.get_data(as_text=True))
    r = oc.post(f"/admin/menus/library/dish/{dish['id']}", data={"dish_fr": "", "dish_en": "x"})
    s.check("an empty French is refused", r.status_code == 400 and "cannot be empty" in r.get_data(as_text=True))
    other = conn.execute("SELECT dish_fr FROM dinner_dishes WHERE id != ? LIMIT 1", (dish["id"],)).fetchone()
    if other:
        r = oc.post(f"/admin/menus/library/dish/{dish['id']}", data={"dish_fr": other["dish_fr"]})
        s.check("a French another dish has is refused", r.status_code == 400
                and "already has" in r.get_data(as_text=True))
    r = oc.post(f"/admin/menus/library/dish/{dish['id']}", data={
        "dish_fr": "Velouté ZDFU", "dish_en": "ZDFU velouté", "dish_es": "Crema ZDFU", "al": ["milk", "celery"],
        "fm": "1"})
    now = conn.execute("SELECT * FROM dinner_dishes WHERE id = ?", (dish["id"],)).fetchone()
    s.check("saved", r.status_code == 302 and now["dish_en"] == "ZDFU velouté" and now["allergens"] == "milk,celery",
            detail=dict(now))
    s.check("the menu keeps what it was written with", _courses(conn, menu["id"])[0]["dish_en"] == "ZDFU soup")
    r = oc.post(f"/admin/menus/library/wine/{wine['id']}", data={
        "wine_fr": "Blanc ZDFU", "wine_en": "ZDFU white", "wine_es": "Blanco ZDFU", "glass": "10 cl"})
    s.check("a wine too", r.status_code == 302 and conn.execute(
        "SELECT glass FROM dinner_wines WHERE id = ?", (wine["id"],)).fetchone()["glass"] == "10 cl")

    s.section("4. Remove: no longer offered, and the menu keeps it")
    r = oc.post(f"/admin/menus/library/dish/{dish['id']}/remove")
    said = " ".join(flashes(oc.get("/admin/menus/library")))
    s.check("removed", r.status_code == 302 and conn.execute(
        "SELECT 1 FROM dinner_dishes WHERE id = ?", (dish["id"],)).fetchone() is None, detail=f"HTTP {r.status_code}")
    s.check("and the page says the menu keeps it", "Removed Velouté ZDFU from the library: it is no longer offered. "
            "1 menu that serves it keeps it as written." in said, detail=said)
    c = _courses(conn, menu["id"])[0]
    s.check("the menu's course keeps its words, losing only the link",
            c["dish_fr"] == "Velouté ZDFU" and c["dish_en"] == "ZDFU soup" and c["dish_id"] is None, detail=c)
    s.check("no longer offered as the cook types",
            '<option value="Velouté ZDFU">' not in oc.get("/admin/menus/new").get_data(as_text=True))
    r = oc.post(f"/admin/menus/library/wine/{wine['id']}/remove")
    s.check("a wine removed the same way", r.status_code == 302 and conn.execute(
        "SELECT 1 FROM dinner_wines WHERE id = ?", (wine["id"],)).fetchone() is None)
    r = oc.post(f"/admin/menus/library/dish/{dish['id']}/remove", follow_redirects=True)
    s.check("removing it twice says it is gone, and nothing breaks",
            r.status_code == 200 and "no longer in the library" in r.get_data(as_text=True))
    s.check("an unknown kind is not a page", oc.get("/admin/menus/library/cheese/1").status_code == 404)

    s.section("4. The two dishes the live check left: removed once, and free to be written again")
    for dish_fr in m.LIVE_CHECK_DISHES_2026_11_27:
        conn.execute("INSERT OR IGNORE INTO dinner_dishes (dish_fr, dish_en, uses) VALUES (?, 'live check', 1)",
                     (dish_fr,))
    conn.execute("DELETE FROM app_settings WHERE key = 'live_check_dishes_removed_2026_11_27'")
    conn.commit()
    gone = m.remove_live_check_dishes_2026_11_27(conn)
    left = conn.execute("SELECT COUNT(*) AS n FROM dinner_dishes WHERE dish_fr IN (?, ?)",
                        m.LIVE_CHECK_DISHES_2026_11_27).fetchone()["n"]
    s.check("both removed", gone == 2 and left == 0, detail=(gone, left))
    conn.execute("INSERT OR IGNORE INTO dinner_dishes (dish_fr, uses) VALUES (?, 1)", (m.LIVE_CHECK_DISHES_2026_11_27[1],))
    conn.commit()
    s.check("once only: written again, it stays", m.remove_live_check_dishes_2026_11_27(conn) == 0
            and conn.execute("SELECT 1 FROM dinner_dishes WHERE dish_fr = ?",
                             (m.LIVE_CHECK_DISHES_2026_11_27[1],)).fetchone() is not None)
    conn.execute("DELETE FROM dinner_dishes WHERE dish_fr IN (?, ?)", m.LIVE_CHECK_DISHES_2026_11_27)
    conn.commit()


# ---------------------------------------------------------------------------------------------------------------
def _gites(s):
    s.section("5. Les Gîtes de Beille: next door, a short walk up our lane")
    anon = m.app.test_client()
    for path in ("/events/weddings", "/events/photoshoots", "/events/private"):
        r = anon.get(path)
        text = visible_text(r.get_data(as_text=True))
        s.check(f"{path} answers", r.status_code == 200, r)
        s.check(f"{path}: under a hundred metres to our gates at the foot of the lane, then a short walk up",
                "under a hundred metres to our gates at the foot of the lane, then a short walk up" in text
                and "a short walk up our lane" in text)
        s.check(f"{path}: never the drive, never Pays de Beille", "foot of the drive" not in text
                and "private drive" not in text and "Pays de Beille" not in text)
    for path in ("/events/weddings", "/events/photoshoots"):
        text = visible_text(anon.get(path).get_data(as_text=True))
        s.check(f"{path}'s question about where people stay says it too",
                "Les Gîtes de Beille next door, a short walk up our lane" in text)


# ---------------------------------------------------------------------------------------------------------------
PROBE = r"""<script>(function(){
  var out = {};
  function sel(){ var t = document.querySelector('[data-tab][aria-selected="true"]'); return t ? t.getAttribute('data-tab') : null; }
  function hid(k){ var p = document.querySelector('[data-tab-panel="' + k + '"]'); return p ? p.hidden : null; }
  function note(){ var n = document.getElementById('occ-tabs-note'); return n && !n.hidden ? n.textContent.trim() : ''; }
  var step = %s;
  /* After the page has finished loading: that is when a browser scrolls to the address's hash. */
  window.addEventListener('load', function(){ setTimeout(function(){
    out.y = Math.round(window.scrollY);
    try { step(out, sel, hid, note); } catch (e) { out.error = String(e); }
    document.documentElement.setAttribute('data-probe', JSON.stringify(out));
  }, 50); });
})();</script>"""

STEP_OCCASION = r"""function(out, sel, hid, note){
  out.start = {tab: sel(), stay_hidden: hid('stay'), dinner_hidden: hid('dinner'), hash: location.hash};
  document.querySelector('[data-tab="dinner"]').click();
  var form = document.getElementById('occasion-form');
  out.clicked = {tab: sel(), stay_hidden: hid('stay'), dinner_hidden: hid('dinner'), hash: location.hash,
                 shown: form.querySelector('[name="tab_shown"]').value};
  form.dispatchEvent(new Event('submit', {cancelable: true}));
  out.action = form.getAttribute('action');
  form.setAttribute('action', form.getAttribute('action').split('#')[0]);
  document.getElementById('so_name').value = '';
  out.valid = form.checkValidity();
  out.invalid = {tab: sel(), note: note()};
}"""

STEP_HASH = r"""function(out, sel, hid, note){ out.tab = sel(); out.dinner_hidden = hid('dinner'); out.hash = location.hash; }"""
STEP_FORCED = r"""function(out, sel, hid, note){ out.tab = sel(); out.note = note(); out.stay_hidden = hid('stay'); }"""
STEP_LIST = r"""function(out, sel, hid, note){
  out.start = {tab: sel(), suggested_hidden: hid('suggested'), yours_hidden: hid('yours')};
  document.querySelector('[data-tab="suggested"]').click();
  out.clicked = {tab: sel(), suggested_hidden: hid('suggested'), yours_hidden: hid('yours'), hash: location.hash};
}"""
STEP_EMPTY = r"""function(out){
  var p = document.getElementById('menus-empty'), a = p.querySelector('a');
  var texts = Array.prototype.filter.call(p.childNodes, function(n){ return n.nodeType === 3 && n.textContent.trim(); });
  function top(node){ var r = document.createRange(); r.selectNodeContents(node); var b = r.getClientRects(); return b.length ? b[0].top : null; }
  out.tops = [top(texts[0]), a.getBoundingClientRect().top, top(texts[texts.length - 1])];
  out.display = getComputedStyle(p).display;
}"""


def _standalone(html, step):
    css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
    tabs = open(os.path.join(ROOT, "static", "tabs.js"), encoding="utf-8").read()
    html = re.sub(r'<link[^>]*href="/static/style\.css[^"]*"[^>]*>', lambda _m: "<style>%s</style>" % css, html)
    html, n = re.subn(r'<script[^>]*src="/static/tabs\.js[^"]*"[^>]*></script>',
                      lambda _m: "<script>%s</script>" % tabs.replace("</", "<\\/"), html)
    assert n <= 1, n
    html = re.sub(r'<script[^>]*src="[^"]*"[^>]*></script>', "", html)
    return html.replace("</body>", (PROBE % step) + "</body>", 1)


def _in_chrome(s, oc, conn):
    s.section("1 and 3 in a browser: the tabs' script, and the empty sentence on one line")
    chrome = find_browser()
    if not s.check("there is a browser to run them in", chrome,
                   detail="none found -- set GUDANES_CHROME; nothing below was checked"):
        return
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (f"{TAG} Tabs week",)).fetchone()
    if occ is None:
        s.check("the occasion from part 1 is there", False)
        return
    form_page = oc.get(f"/admin/stay-occasions/{occ['id']}").get_data(as_text=True)
    refused = oc.post(f"/admin/stay-occasions/{occ['id']}", data={
        "name": occ["name"], "first_night": occ["first_night"], "last_night": occ["last_night"], "kind": "stay",
        "status": "draft", "photo": "", "serve_dinner": "1", "tab_shown": "stay"}).get_data(as_text=True)
    pages = {"occasion.html": _standalone(form_page, STEP_OCCASION),
             "hash.html": _standalone(form_page, STEP_HASH),
             "inside.html": _standalone(form_page, STEP_HASH),
             "forced.html": _standalone(refused, STEP_FORCED),
             "list.html": _standalone(oc.get("/admin/stay-occasions").get_data(as_text=True), STEP_LIST)}
    if _EMPTY_PAGE[0]:
        pages["empty.html"] = _standalone(_EMPTY_PAGE[0], STEP_EMPTY)
    work = tempfile.mkdtemp(prefix="gudanes_tabs_")
    server = None
    try:
        for name, html in pages.items():
            open(os.path.join(work, name), "w", encoding="utf-8").write(html)
        handler = functools.partial(_Quiet, directory=work)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d/" % server.server_address[1]

        def probe(path):
            r = subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                 "--user-data-dir=" + os.path.join(work, "profile-" + re.sub(r"\W", "", path)),
                 "--window-size=1280,900", "--virtual-time-budget=4000", "--dump-dom", base + path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            got = re.search(r'data-probe="([^"]*)"', r.stdout)
            return json.loads(got.group(1).replace("&quot;", '"').replace("&amp;", "&")) if got else {"none": r.stderr[-300:]}

        o = probe("occasion.html")
        st, ck, inv = o.get("start") or {}, o.get("clicked") or {}, o.get("invalid") or {}
        s.check("the page ran and reported", "start" in o and "error" not in o, detail=str(o)[:300])
        s.check("it opens on The stay, Chef & dinner hidden", st.get("tab") == "stay" and st.get("stay_hidden") is False
                and st.get("dinner_hidden") is True, detail=st)
        s.check("at the top of the page: the tabs are where the eye starts, not scrolled away under the header",
                o.get("y") == 0, detail=f"scrolled {o.get('y')}px")
        s.check("a click opens Chef & dinner and hides The stay", ck.get("tab") == "dinner"
                and ck.get("stay_hidden") is True and ck.get("dinner_hidden") is False, detail=ck)
        s.check("the tab is in the address, so a reload keeps it", ck.get("hash") == "#chef-and-dinner", detail=ck)
        s.check("and the server hears which tab was in front", ck.get("shown") == "dinner", detail=ck)
        s.check("a save goes with the tab on its address, which the redirect after it keeps",
                (o.get("action") or "").endswith("#chef-and-dinner"), detail=o.get("action"))
        s.check("a name left empty on the hidden tab: The stay opens so the browser can show it",
                o.get("valid") is False and inv.get("tab") == "stay", detail=inv)
        s.check("and says so", "needs filling in" in (inv.get("note") or "") and "The stay" in (inv.get("note") or ""),
                detail=inv)
        h = probe("hash.html#chef-and-dinner")
        s.check("opened at #chef-and-dinner: Chef & dinner", h.get("tab") == "dinner" and h.get("dinner_hidden") is False,
                detail=h)
        s.check("and still at the top, the tabs in view", h.get("y") == 0, detail=f"scrolled {h.get('y')}px")
        h = probe("inside.html#dinner")
        s.check("opened at #dinner, as the menus' links say: the tab holding it", h.get("tab") == "dinner", detail=h)
        f = probe("forced.html")
        s.check("a save refused for the dinner, from The stay: Chef & dinner open", f.get("tab") == "dinner"
                and f.get("stay_hidden") is True, detail=f)
        s.check("saying so", "The problem is on Chef & dinner" in (f.get("note") or ""), detail=f)
        lst = probe("list.html")
        lst_s, lst_c = lst.get("start") or {}, lst.get("clicked") or {}
        s.check("the list opens on Your occasions, the suggestions hidden", lst_s.get("tab") == "yours"
                and lst_s.get("suggested_hidden") is True and lst_s.get("yours_hidden") is False, detail=lst)
        s.check("at the top of the page", lst.get("y") == 0, detail=f"scrolled {lst.get('y')}px")
        s.check("and Suggested is a click away", lst_c.get("tab") == "suggested" and lst_c.get("yours_hidden") is True
                and lst_c.get("hash") == "#suggested", detail=lst_c)
        if "empty.html" in pages:
            e = probe("empty.html")
            tops = [t for t in (e.get("tops") or []) if t is not None]
            s.check("the empty list's sentence is one line, its link inside it",
                    len(tops) == 3 and max(tops) - min(tops) < 4 and e.get("display") == "block", detail=e)
    finally:
        if server:
            server.shutdown()
        shutil.rmtree(work, ignore_errors=True)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a):
        pass

