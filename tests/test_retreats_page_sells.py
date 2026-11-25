"""/workshops made to sell (the owner's retreats brief, 23 November 2026).

"i dont know why theyre not selling." The page opened on what a retreat is
NOT, led each card with the price and "about €800 a night" under it (on a
site where a room is €270), scattered what a guest actually does across three
sections, gave the restoration one phrase and the bathrooms one line that read
as a warning, and buried "10% to reserve" in Key Information. This holds what
replaced it:

  WHAT YOU'LL DO. One section, high on the page (after the opening, before
  The Retreats), with every activity the brief lists, each with a photograph.
  The three the owner named lead, with two photographs each, and are named in
  the hero and the opening line: the cooking classes, the brocantes, the
  restoration. The restoration says it is Cooking in the Cuisine and Seven
  Starry Nights, and never names Noël or Antique & French Finds.

  THE FRESCOES. Cooking and Seven Starry Nights name the Renaissance frescoes
  and the painted beams on their card, their opening paragraph and their own
  page; Noël and Antique & French Finds do not.

  THE BATHROOMS. A section of their own, beautiful first and then the fact:
  three, on the ground floor, shared between the group. Never "private
  bathroom" anywhere on the page -- that is a night's stay, not a retreat.

  THE CARDS AND THE DATES. What is included first, then the price, then what
  reserves it, then the per-night figure. Nothing says "a night" above what
  is included. "10% reserves your place" (the retreat's own percent) on every
  card -- and on a date inside the balance window, where the whole amount is
  taken at booking, the truth instead (reserve_line).

  THE LOUNGE. Wherever the pool and the tennis court are offered, a glass of
  wine in the lounge (owner, the same day).

  GUEST WORDS. Hidden until there are some, never an empty heading.

  AND TWO MORE FROM THE OWNER THAT EVENING. A wine tasting is included on
  every retreat, said on each card, on each retreat's page and in the one
  sentence the letters share; and the retreats with transfers start at midday
  on the first day at the Grand Hôtel de l'Opéra, Place du Capitole, Toulouse
  -- on the page, the retreat's own page, its first day, and the confirmation
  letter, and on none that has no transfers. It said 11am in central Toulouse.
"""
import os
import re
from datetime import timedelta

from _harness import Suite, db, visible_text
import _harness

m = _harness.m

ACTIVITIES = ["Cooking with the chefs", "The brocantes", "The restoration", "The markets",
              "Castles and caves", "The mountains and the river", "At leisure", "The evenings"]
LEAD = ACTIVITIES[:3]
LOUNGE = "glass of wine in the lounge"
HOTEL = "Grand Hôtel de l’Opéra"


def words(html):
    return re.sub(r"\s+([,.:;])", r"\1", visible_text(html))


def kind_of(title):
    with m.app.test_request_context("/"):
        return m.app.jinja_env.from_string(
            "{% from '_ws_copy.html' import ws_kind %}{{ ws_kind(w)|trim }}").render(w={"title": title})


def macro(name, w):
    with m.app.test_request_context("/"):
        return words(m.app.jinja_env.from_string(
            "{% from '_ws_copy.html' import " + name + " %}{{ " + name + "(w) }}").render(w=w))


def flags(w):
    """(transfers, excursions) as the page's own macros read them."""
    with m.app.test_request_context("/"):
        out = m.app.jinja_env.from_string(
            "{% from '_stay_or_workshop.html' import tx_flag, ex_flag %}{{ tx_flag(w)|trim }}|{{ ex_flag(w)|trim }}"
        ).render(w=w)
    t, e = out.split("|")
    return t == "yes", e == "yes"


def section(html, marker):
    """From the <section> holding `marker` to its </section>."""
    at = html.find(marker)
    if at < 0:
        return ""
    start = html.rfind("<section", 0, at)
    end = html.find("</section>", at)
    return html[start:end] if start > -1 and end > -1 else ""


def blocks(html, open_tag):
    """Every <article ...open_tag...> up to its </article>."""
    out = []
    for hit in re.finditer(re.escape(open_tag), html):
        end = html.find("</article>", hit.start())
        out.append(html[hit.start():end])
    return out


def run():
    s = Suite("The retreats page, made to sell (23 Nov)")
    c = m.app.test_client()
    r = c.get("/workshops")
    s.check("/workshops answers", r.status_code == 200, detail=str(r.status_code))
    html = r.get_data(as_text=True)
    seen = words(html)

    conn = db()
    today = m.house_today()
    offer = [w for w, _ in m.workshops_on_offer(conn, today)]
    conn.close()
    kinds = {w["title"]: kind_of(w["title"]) for w in offer}
    s.check("there are retreats on offer to look at", bool(offer),
            detail="nothing on offer, so the card checks below would prove nothing")

    s.section("The opening says what a retreat is")
    s.check("never 'This is not a hotel stay'", "This is not" not in seen)
    hero = words(html[html.find('class="g-hero__lede"'):html.find("</p>", html.find('class="g-hero__lede"'))])
    opening = words(html[html.find('class="g-p g-lede-first"'):html.find("</p>", html.find('class="g-p g-lede-first"'))])
    for name, said in (("the hero", hero), ("the opening line", opening)):
        for want in ("ook", "brocantes", "frescoes"):
            s.check(f"{name} names the {'cooking' if want == 'ook' else want}", want in said, detail=said)
    s.check("the opening line: take part in everything, or nothing", "Take part in everything, or nothing at all." in opening,
            detail=opening)

    s.section("What You'll Do")
    todo = section(html, 'id="what-you-do"')
    s.check("the section is there", bool(todo))
    at_todo, at_cards = html.find('id="what-you-do"'), html.find('class="g-wscards"')
    s.check("high on the page: after the opening, before The Retreats",
            html.find('class="g-p g-lede-first"') < at_todo and (at_cards < 0 or at_todo < at_cards)
            and at_todo < html.find(">The Retreats<"),
            detail=f"opening {html.find('g-lede-first')}, section {at_todo}, cards {at_cards}")
    items = re.findall(r'<(?:article|li) class="g-todo__i[^"]*" id="todo-([a-z]+)">(.*?)</(?:article|li)>', todo, re.S)
    by_name = {}
    for key, body in items:
        h = re.search(r'<h3 class="g-todo__h">(.*?)</h3>', body, re.S)
        by_name[words(h.group(1)) if h else key] = body
    for name in ACTIVITIES:
        body = by_name.get(name, "")
        s.check(f"{name}: there, with its line", bool(body) and bool(words(
            (re.search(r'<p class="g-todo__p">(.*?)</p>', body, re.S) or re.match("", "")).group(0))),
            detail=", ".join(by_name))
        # the house's own copy, and a file that is really there: a broken <img> renders as nothing and reports nothing
        pics = [p for p in re.findall(r'<img\b[^>]*\bsrc="/static/img/(retreats/[^"?]+)"', body)
                if os.path.isfile(os.path.join(_harness.ROOT, "static", "img", *p.split("/")))]
        need = 2 if name in LEAD else 1
        s.check(f"{name}: {need} photograph{'s' if need > 1 else ''} of the house's own", len(pics) >= need,
                detail=str(pics))
        s.check(f"{name}: says which retreats, or the season", '<p class="g-todo__on">' in body
                and words(body[body.find('<p class="g-todo__on">'):]).strip() != "", detail=body[-200:])
    lead = re.findall(r'class="g-todo__i g-todo__i--lead" id="todo-([a-z]+)"', todo)
    s.check("the three the owner named lead, in their own larger row",
            lead == ["cooking", "brocantes", "restoration"], detail=str(lead))
    s.check("never the word the retreats deck ruled out", "activities" not in words(todo).lower())

    resto = by_name.get("The restoration", "")
    on = resto[resto.find('<p class="g-todo__on">'):]
    named = [words(t) for t in re.findall(r"<a [^>]*>(.*?)</a>", on, re.S)]
    fresco_ones = [t for t, k in kinds.items() if k in ("cooking", "starry")]
    s.check("the restoration names Cooking in the Cuisine and Seven Starry Nights where they are on offer",
            sorted(named) == sorted(fresco_ones) if fresco_ones else "On selected retreats" in words(on),
            detail=f"named {named}, on offer {fresco_ones}")
    for t, k in kinds.items():
        if k in ("noel", "finds"):
            s.check(f"and never {t}", t not in words(on), detail=words(on))
    s.check("nor 'On every retreat'", "every retreat" not in words(on).lower(), detail=words(on))
    leisure = words(by_name.get("At leisure", ""))
    s.check("At leisure: the pool and tennis, or a glass of wine in the lounge",
            "pool" in leisure and "tennis" in leisure and "or enjoy a glass of wine in the lounge" in leisure,
            detail=leisure)

    s.section("The frescoes and painted beams: Cooking and Seven Starry Nights, and no other")
    for kind, title, nights in (("cooking", "Cooking in the Cuisine", "5 nights / 6 days"),
                                ("starry", "Seven Starry Nights", "7 nights / 8 days"),
                                ("noel", "Noël at Gudanes", "4 nights / 5 days"),
                                ("finds", "Antique & French Finds", "3 nights / 4 days")):
        w = {"title": title, "nights_label": nights}
        for mac in ("ws_lead", "ws_copy"):
            said = macro(mac, w).lower()
            has = "fresco" in said and "painted beams" in said
            if kind in ("cooking", "starry"):
                s.check(f"{title}: {mac} names the frescoes and the painted beams", has, detail=said[:200])
            else:
                s.check(f"{title}: {mac} does not", "fresco" not in said and "painted beam" not in said,
                        detail=said[:200])
    for w in offer:
        page = words(c.get("/workshops/%d" % w["id"]).get_data(as_text=True)).lower()
        if kinds[w["title"]] in ("cooking", "starry"):
            s.check(f"{w['title']}: its own page names the painted beams", "painted beams" in page)
        elif kinds[w["title"]] in ("noel", "finds"):
            s.check(f"{w['title']}: its own page does not", "painted beam" not in page and "fresco" not in page)

    s.section("The bathrooms")
    baths = section(html, 'id="bathrooms"')
    said = words(baths)
    s.check("a section of their own", bool(baths) and "The Bathrooms" in said)
    s.check("says they are shared between the group", "shared between the group" in said, detail=said[:300])
    s.check("and on the ground floor", "ground floor" in said)
    s.check("beautiful first, then the fact",
            0 <= said.find("Antoinette Poisson") < said.find("shared"), detail=said[:300])
    s.check("with its photographs", len(re.findall(r"<img\b", baths)) >= 4)
    s.check("never 'private bathroom', anywhere on the page", "private bathroom" not in seen.lower())
    s.check("nor 'your own' bathroom", "bathroom of your own" not in seen.lower() and "your own bathroom" not in seen.lower())
    s.check("Key Information points to it", 'href="#bathrooms"' in section(html, ">Key Information<"))

    s.section("Each card and each date: what is included, the price, what reserves it")
    cards = blocks(html, '<article class="g-wscard">')
    dates = blocks(html, '<article class="g-sit')
    s.check("there are cards to look at", bool(cards))
    for kind_name, found, inc_cls, title_re in (("card", cards, "g-wscard__inc", r'<h3 class="g-wscard__name">(.*?)</h3>'),
                                                 ("date", dates, "g-sit__inc", r'<p class="g-sit__name">(.*?)</p>')):
        for block in found:
            t = re.search(title_re, block, re.S)
            t = words(t.group(1)) if t else "?"
            w = next((x for x in offer if x["title"] == t), None)
            inc = block.find(f'class="{inc_cls}"')
            price = block.find('class="g-fig"')
            s.check(f"{kind_name} {t}: what is included, above the price", -1 < inc < price or (inc > -1 and price < 0),
                    detail=f"included at {inc}, price at {price}")
            s.check(f"{kind_name} {t}: nothing says 'a night' above it", inc > -1 and "a night" not in words(block[:inc]),
                    detail=words(block[:inc])[-120:])
            if w is not None:
                tx, ex = flags(w)
                line = words(block[block.find(">", inc) + 1:block.find("</p>", inc)])
                s.check(f"{kind_name} {t}: every meal and the wine", line.startswith("Every meal"), detail=line)
                s.check(f"{kind_name} {t}: transfers only where it has them", ("transfers" in line) == tx, detail=line)
                s.check(f"{kind_name} {t}: excursions only where it has them", ("excursions" in line) == ex, detail=line)
            if price > -1:
                res = block.find('class="g-toreserve"')
                s.check(f"{kind_name} {t}: what reserves it, beside the price", price < res, detail=f"{price} {res}")
                rline = words(block[block.find(">", res) + 1:block.find("</p>", res)]) if res > -1 else ""
                s.check(f"{kind_name} {t}: 'reserves your place' or the whole amount",
                        "reserves your place" in rline or "paid in full" in rline, detail=rline)
                night = block.find('class="g-pernight"')
                s.check(f"{kind_name} {t}: the per-night figure, if any, comes last", night < 0 or res < night)
    for block in cards:
        t = words((re.search(r'<h3 class="g-wscard__name">(.*?)</h3>', block, re.S) or re.match("", "")).group(0))
        w = next((x for x in offer if x["title"] == t), None)
        if w is None or not w["price_per_person"]:
            continue
        pct = int(w["deposit_percent"] if w["deposit_percent"] is not None else m.WORKSHOP_DEPOSIT_PERCENT)
        s.check(f"card {t}: '{pct}% reserves your place'", f"{pct}% reserves your place" in words(block),
                detail=words(block)[-200:])

    s.section("reserve_line: the deposit while it is a deposit")
    today = m.house_today()
    far = {"start_date": (today + timedelta(days=90)).isoformat(), "balance_due_on": None}
    near = {"start_date": (today + timedelta(days=10)).isoformat(), "balance_due_on": None}
    fixed_past = {"start_date": (today + timedelta(days=90)).isoformat(),
                  "balance_due_on": (today - timedelta(days=1)).isoformat()}
    rl = m.reserve_line
    s.check("the retreat's own percent, with no dates", rl({"deposit_percent": 25}) == "25% reserves your place.",
            detail=rl({"deposit_percent": 25}))
    s.check("no percent set: the house's", rl({"deposit_percent": None}) ==
            f"{m.WORKSHOP_DEPOSIT_PERCENT}% reserves your place.")
    s.check("dates far off: the deposit", rl({"deposit_percent": 10}, [far]) == "10% reserves your place.")
    s.check("dates inside the window: the whole amount, said",
            "paid in full" in rl({"deposit_percent": 10}, [near]), detail=rl({"deposit_percent": 10}, [near]))
    s.check("one far, one near: still a deposit for the far one",
            rl({"deposit_percent": 10}, [near, far]) == "10% reserves your place.")
    s.check("a balance day the owner fixed, passed: the whole amount",
            "paid in full" in rl({"deposit_percent": 10}, [fixed_past]))
    s.check("a hundred percent is not a deposit", "% reserves" not in rl({"deposit_percent": 100}))
    terms = m.compute_workshop_payment_terms(1000, 10, today + timedelta(days=10))
    s.check("and it agrees with what is charged: inside the window the deposit is the whole price",
            terms[0] == 1000 and "paid in full" in rl({"deposit_percent": 10}, [near]), detail=str(terms))

    s.section("The pool and the tennis court come with a glass of wine in the lounge")
    for path in ("/workshops", "/facilities", "/book"):
        page = words(c.get(path).get_data(as_text=True))
        s.check(f"{path}", LOUNGE in page)
    starry = macro("ws_copy", {"title": "Seven Starry Nights", "nights_label": "7 nights / 8 days"})
    s.check("Seven Starry Nights' opening paragraph", LOUNGE in starry and
            "the rare luxury of having nowhere else to be" in starry, detail=starry[-200:])

    s.section("A wine tasting, on every retreat (owner, 23 Nov: \"also include wine tasting for all 3 workshops\")")
    for kind_name, found, inc_cls in (("card", cards, "g-wscard__inc"), ("date", dates, "g-sit__inc")):
        for block in found:
            inc = block.find(f'class="{inc_cls}"')
            line = words(block[block.find(">", inc) + 1:block.find("</p>", inc)]) if inc > -1 else ""
            s.check(f"{kind_name}: '{line[:60]}' names the wine tasting", "a wine tasting" in line, detail=line)

    class _Row(dict):
        def keys(self):
            return list(dict.keys(self))

    said = m.atelier_included_words(_Row(title="Cooking in the Cuisine", excursions_included=1))
    s.check("the one sentence the letters and the /book card share names it", "a wine tasting" in said, detail=said)

    s.section("The retreats with transfers start at midday at the Grand Hôtel de l’Opéra")
    s.check("never 11am in central Toulouse, on /workshops", "11am" not in seen and "central Toulouse" not in seen)
    s.check("/workshops says where and when, in its questions and Key Information",
            seen.count(HOTEL) >= 2 and "midday on the first day" in seen, detail=str(seen.count(HOTEL)))
    for w in offer:
        page = words(c.get("/workshops/%d" % w["id"]).get_data(as_text=True))
        tx, _ex = flags(w)
        if tx:
            s.check(f"{w['title']}: its own page says the hotel, at midday", HOTEL in page and "midday" in page)
            if kinds[w["title"]] in ("cooking", "finds", "starry"):
                s.check(f"{w['title']}: and its first day starts there", f"Midday at the {HOTEL}, Toulouse" in page)
        else:
            s.check(f"{w['title']}: no meeting in Toulouse on a retreat without transfers", HOTEL not in page)
    row = dict(guest_name="X", title="Cooking in the Cuisine", start_date="2027-05-01", end_date="2027-05-06",
               party_size=2, reference_code="R", total_price=100.0, deposit_amount=10.0, balance_amount=90.0,
               balance_due_date=None, manage_token="tok", transfers_included=1, excursions_included=1)
    with m.app.test_request_context("/"):
        on = m.workshop_email_context(_Row(row))["included_note"]
        row["transfers_included"] = 0
        off = m.workshop_email_context(_Row(row))["included_note"]
    s.check("the confirmation letter says the same: midday, the hotel, Place du Capitole",
            "midday" in on and "Grand Hôtel de l'Opéra" in on and "Place du Capitole" in on and "11am" not in on,
            detail=on[:200])
    s.check("and a retreat without transfers is told no such thing", "Opéra" not in off, detail=off[:160])
    s.check("and the letter promises the wine tasting too", "a wine tasting" in on, detail=on[:200])

    # 24 Nov: the owner's guests' lines from their thank-you letters are on the
    # page now (6d92fc0, test_guest_letters), as g-say--letter blockquotes.
    s.section("Guest words: hidden until there are some")
    s.check("never a heading with nothing under it",
            "What Retreat Guests Say" not in seen
            or bool(re.search(r'<blockquote class="g-panel g-say[ "]', html)))
    return s


if __name__ == "__main__":
    print(run().report())
