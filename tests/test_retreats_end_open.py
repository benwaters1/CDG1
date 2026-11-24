"""A retreat is not a list: every description of one ends open (24 November 2026).

The owner: "where we describe whats included for the workshops put and much
more.. do it not just there but everywhere, doesnt have to be that wording but
its absolutely not limited to that. its the house as it was, full of life."

Every place the site said what a retreat holds read as a closed list -- the
kitchen, the markets and brocantes, long candlelit dinners, full stop -- and a
guest reads a closed list as everything they get. Each now ends open, in wording
varied so a page does not say the same five words five times; the lists are not
longer, because the point is that they are open (nothing invented to fill the
"more").

Held here, page by page, in the element that carries each description:
  - it ends open: "more" (French "plus", Spanish "más");
  - house voice: no "etc.", no ellipsis;
  - the menu card's new line has its French and Spanish, and they are served;
  - each retreat's own lines, for every kind and every length the site writes
    words for, and the seed a new database starts from;
  - on /workshops, the four named retreats' card lines do not all end alike.

Left alone on purpose, as the brief says: the day-by-day itineraries (a day is
what it is), the "Not included" lines, and the comparison table's rows (a cell
is a yes or a no, not a description).
"""
import os
import re

from _harness import Suite, visible_text
import _harness

m = _harness.m
ROOT = _harness.ROOT

OPEN = re.compile(r"\b(more|plus)\b|\bmás\b", re.I)
CLOSED_HABITS = ("etc.", "...", "…")
MENU_EN = "Cooking, the brocantes, the restoration and more"


def opened(text):
    return bool(OPEN.search(text)) and not any(h in text for h in CLOSED_HABITS)


def grab(page, pattern):
    """Every element matching the pattern, as the words a reader sees."""
    return [visible_text(x) for x in re.findall(pattern, page, re.S)]


def macro(src, **kw):
    with m.app.test_request_context("/"):
        return m.app.jinja_env.from_string(src).render(**kw)


def run():
    s = Suite("A retreat is not a list: every description ends open")
    client = m.app.test_client()
    pages = {p: client.get(p) for p in ("/", "/workshops", "/facilities", "/events/private", "/social")}
    for p, r in pages.items():
        s.check(f"{p} loads", r.status_code == 200, detail=str(r.status_code))
    html = {p: r.get_data(as_text=True) for p, r in pages.items()}

    found = {
        "/ La Vie de Château, the paragraph of the days":
            [x for x in grab(html["/"], r'<p class="g-full__p">(.*?)</p>') if "The days belong to the kitchen" in x],
        "/ the Retreats tab's foot": grab(html["/"], r'<p class="g-hbook__foot">(.*?)</p>'),
        "/ the A Retreat column, its last line":
            [grab(col, r"<li\b[^>]*>(.*?)</li>")[-1] for col in
             re.findall(r'<article class="g-sow__col g-sow__col--full[^"]*">.*?<ul class="g-sow__list">(.*?)</ul>',
                        html["/"], re.S)],
        # The retreats brief (23 Nov) rewrote the opening to say what a retreat is, not what it is not.
        "/workshops the opening": [x for x in grab(html["/workshops"], r'<p class="g-p g-lede-first">(.*?)</p>')
                                   if "A few days of French château life" in x],
        "/workshops a retreat gives you the château":
            [x for x in grab(html["/workshops"], r'<p class="g-p">(.*?)</p>') if x.startswith("A night’s stay is")
             or x.startswith("A night's stay is")],
        "/workshops each retreat's card": grab(html["/workshops"], r'<p class="g-p g-wscard__blurb">(.*?)</p>'),
        "/facilities the La vie de château card":
            [x for x in grab(html["/facilities"], r'<p class="g-three__d">(.*?)</p>') if "for a retreat" in x],
        "/events/private the retreats": [x for x in grab(html["/events/private"], r'<p class="g-p">(.*?)</p>')
                                         if "the retreats" in x],
        "/social the retreats": [x for x in grab(html["/social"], r'<p class="g-p">(.*?)</p>') if "Retreats of" in x],
    }
    # What search engines show for /workshops: the page describes the retreats there too.
    for name in ("description", "og:description"):
        attr = "property" if ":" in name else "name"
        hit = re.search(r'<meta\s+%s="%s"\s+content="([^"]*)"' % (attr, re.escape(name)), html["/workshops"])
        found[f"/workshops {name}"] = [hit.group(1)] if hit else []

    s.section("Each page's description of a retreat ends open")
    for where, texts in found.items():
        if not s.check(f"{where}: found", bool(texts)):
            continue
        for t in texts:
            s.check(f"{where}: ends open — “…{t[-60:]}”", opened(t), detail=t)

    s.section("A retreat's own page")
    conn = _harness.db()
    rows = conn.execute(f"SELECT id, title FROM workshops WHERE active = 1 AND {m.WORKSHOP_IS_PUBLIC}").fetchall()
    conn.close()
    shown = 0
    for row in rows:
        r = client.get(f"/workshops/{row['id']}")
        if r.status_code != 200:
            # A retreat with no date still to come sends its visitor back to /workshops: no page to read.
            continue
        shown += 1
        page = r.get_data(as_text=True)
        copy = grab(page, r'<div class="g-sp__text"><p class="g-p">(.*?)</p>')
        s.check(f"{row['title']}: its paragraph ends open", bool(copy) and opened(copy[0]),
                detail=(copy[0][-120:] if copy else "no paragraph"))
        incl = [x for x in grab(page, r"<li>(The classes[^<]*)</li>")]
        s.check(f"{row['title']}: what is included, the classes and outings, ends open",
                bool(incl) and all(opened(x) for x in incl), detail=str(incl))
        og = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', page)
        s.check(f"{row['title']}: what is shared ends open", og is not None and opened(og.group(1)))
    s.check("there are retreats with a page of their own to read", shown > 0, detail=f"{shown} of {len(rows)}")

    s.section("Every retreat's own lines, whatever its kind and length")
    kinds = {"Noël at Gudanes": "4 nights / 5 days", "Cooking in the Cuisine": "5 nights / 6 days",
             "Antique & French Finds": "3 nights / 4 days", "Seven Starry Nights": "7 nights / 8 days",
             "Seven Starry Nights (five)": "5 nights / 6 days"}
    for n in ("3", "4", "5", "7", ""):
        kinds[f"An unnamed retreat of {n or 'unknown'} nights"] = f"{n} nights" if n else ""
    for title, label in kinds.items():
        w = {"title": title.replace(" (five)", ""), "nights_label": label}
        for name in ("ws_lead", "ws_copy"):
            words = visible_text(macro("{% from '_ws_copy.html' import " + name + " %}{{ " + name + "(w) }}", w=w))
            s.check(f"{name}, {title}", opened(words), detail=words[-120:])
    named = [visible_text(macro("{% from '_ws_copy.html' import ws_lead %}{{ ws_lead(w) }}",
                                w={"title": t, "nights_label": kinds[t]}))
             for t in ("Noël at Gudanes", "Cooking in the Cuisine", "Antique & French Finds", "Seven Starry Nights")]
    endings = [re.search(r"[^,:]*$", x).group(0).strip() for x in named]
    s.check("the four named retreats' cards do not all end alike", len(set(endings)) == 4, detail=str(endings))
    nxt = visible_text(macro("{% from '_stay_or_workshop.html' import next_time %}{{ next_time() }}"))
    s.check("the 'next time' card on a stay's own pages", opened(nxt), detail=nxt)
    conf = open(os.path.join(ROOT, "templates", "booking_confirmation.html"), encoding="utf-8").read()
    line = re.search(r'<p class="g-find__d">(Stay longer, for a retreat:[^<]*)', conf)
    s.check("the booking confirmation's retreat card", line is not None and opened(line.group(1)),
            detail=line.group(1) if line else "not found")

    s.section("The menu card, in three languages")
    base = open(os.path.join(ROOT, "templates", "public_base.html"), encoding="utf-8").read()
    s.check("the menu card asks for the open line", "t('%s')" % MENU_EN in base)
    s.check("and no longer the closed one", "t('Cooking, the brocantes and the restoration')" not in base)
    tr = m.translations
    fr, es = tr.FR.get(MENU_EN, ""), tr.ES.get(MENU_EN, "")
    s.check("its French", opened(fr) and "bien plus encore" in fr, detail=fr)
    s.check("its Spanish", opened(es) and "mucho más" in es, detail=es)
    for lang, said in (("fr", fr), ("es", es)):
        page = client.get(f"/?lang={lang}").get_data(as_text=True)
        s.check(f"served in the menu, ?lang={lang}", said and said in visible_text(page))
    client.get("/?lang=en")

    s.section("The seed a new database starts from")
    for w in m.DEFAULT_WORKSHOPS:
        if w["title"] == m.RETIRED_WORKSHOP_TITLE:
            continue
        s.check(f"{w['title']}: its description ends open", opened(w["description"]), detail=w["description"][-100:])

    s.section("The check itself")
    # Negative controls: the closed lines this replaced are seen as closed, and the habits are caught.
    s.check("a closed list is closed", not opened("Three nights of la vie de château: the brocantes of the valley, "
                                                  "the kitchen, and long dinners at the château."))
    s.check("an etc. is not an opening", not opened("the kitchen, the brocantes, etc., and more"))
    s.check("nor an ellipsis", not opened("the kitchen, the brocantes and more…"))
    s.check("the French and the Spanish are read", opened("et bien plus encore") and opened("y mucho más"))
    return s


if __name__ == "__main__":
    print(run().report())
