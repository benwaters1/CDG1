"""Each retreat's days, as the owner asked for them on 9 November 2026.

"Make it more broad, what's on each day, not so specific, and think about the
logic on them too, like yoga is too cold for Noël so remove it ... don't say X
food in copper pot ... it should say Day 1 and have the actual day like Friday,
then repeat in French ... disco night, use room names in French, wine tasting
in le bar."

The plans live in _ws_copy.html, which the design side has rewritten before, so
these are the rules a handover or a hurried edit breaks without anything
erroring:

  THE HEADINGS. Every day reads "Day 1 · Friday · Vendredi", the weekday worked
  out from the dates on the page, never typed in. The weekdays here come from
  this file's own table, not app.py's, so a wrong table in the app cannot agree
  with itself. A retreat whose dates start on different weekdays says "Day 1"
  and no weekday, because one plan cannot be right for both.

  THE LOGIC. Mirepoix's market is on Monday mornings: a day that goes there is a
  Monday. A retreat in the winter months has no yoga, no pool, no terrace, no
  river and nothing under the stars, and Noël has its Christmas things instead.

  BROAD. No dish, pan, mould or drink on a day; no restaurant by name (Maison
  Beaucoup's restaurant was listed as shut Monday to Thursday, and a plan cannot
  know which table is open).

  SHORT (the owner's site-copy brief, 11 Nov): each day is its heading and two
  to four plain bullets, a line each -- no paragraph under the heading and no
  Matin / Après-midi / Soir labels. The last night is the banquet in the Salon
  de Musique, then dancing.

  THE ROOMS. Le Bar and la Bibliothèque, never the Château Bar, the library or a
  tasting "beside the cellar".

  AN INVITATION, NOT A SCHEDULE (owner, 25 Nov: "The itinerary is good, but the
  wording is not elegant and marketing enough"). The days, their order and every
  fact stayed; only the words changed. So: no bullet says "Leisure time" (the
  everyday block's heading above the days is the one place it stays), "dinner
  out" is nowhere, the last night is the farewell banquet, every plan keeps the
  number of bullets each day had, Cooking in the Cuisine still has no pool, and
  Noël still has nothing outdoors once the light goes and no outing. The same on
  /events/private. A bullet is still a line, but a longer one: the brief's own
  run to eighteen words and the arrival keeps the midday meeting inside its new
  words, so twenty (it was twelve). The owner's own evening for an occasion,
  typed in a form, keeps its twelve (RETREAT_EVENING_WORDS in app.py).
"""
import re
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, db, visible_text
import _harness

m = _harness.m
TAG = "ZZRD"

# This file's own week, Monday first, so the app's table is checked rather than trusted.
EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# Too fine for a day: the dish, the pan, the drink, the restaurant (owner, 9 Nov).
TOO_FINE = [r"copper", r"\bmoulds?\b", r"champagne", r"jell(?:y|ies)", r"\bfillet\b", r"ballotine",
            r"guinea fowl", r"\btrout\b", r"\bbeef\b", r"Bassin des Ladres", r"Kir Royale", r"Lillet",
            r"\bros[ée]\b", r"Maison Beaucoup", r"Lacube", r"\bbûche\b"]
# The rooms by their French names (owner, 9 Nov): the old English ones, and the old place for the tasting.
OLD_ROOMS = [r"Château Bar", r"Chateau Bar", r"the library", r"beside the cellar", r"medieval kitchen",
             r"\bthe bar\b"]
# Nothing for a December day: too cold, too dark, and the river and the pool are shut. "beneath the stars" since
# 25 Nov, when the film moved from under them: the guard follows the words or it guards nothing.
NOT_IN_WINTER = [r"\byoga\b", r"\bpool\b", r"terrace", r"(?:under|beneath) the stars", r"\bcanoe\b", r"\bkayak\b",
                 r"barbecue", r"(?<!table )\btennis\b", r"ice cream", r"\briver\b"]
# Outdoors, for an evening: what a December evening must not offer once the light has gone (owner, 27 Oct).
OUTDOORS = NOT_IN_WINTER + [r"\bgrounds\b", r"\bgarden\b", r"outdoors?", r"\bmountains?\b"]
# Out of the house: no vans in winter (owner, 27 Oct), so no Noël day goes anywhere.
OUTINGS = [r"\bFoix\b", r"Lombrives", r"Ax-les-Thermes", r"Mirepoix", r"Toulouse", r"Les Cabannes", r"\btransfer",
           r"\bcollected\b", r"\bdriven\b", r"vide-grenier", r"brocantes"]
WINTER_MONTHS = {11, 12, 1, 2}
PARA_WORDS, MOMENT_WORDS = 70, 20


def _say(html):
    return re.sub(r"\s+([,.:;])", r"\1", re.sub(r"\s+", " ", visible_text(html))).strip()


def journey(html):
    """The day-by-day section: [{kicker, label, title, para, moments}], and the whole section's words."""
    sec = html[html.find('class="g-sec g-sec--sand g-journey-sec"'):]
    sec = sec[:sec.find("</section>")]
    days = []
    for d in re.findall(r'<li class="g-journey__d">(.*?)</li>\s*(?=<li class="g-journey__d">|</ol>)', sec, re.S):
        grab = lambda pat: (lambda f: _say(f.group(1)) if f else "")(re.search(pat, d, re.S))
        days.append({
            "kicker": grab(r'<p class="g-journey__k"[^>]*>(.*?)</p>'),
            "label": grab(r'<span class="g-journey__l"[^>]*>(.*?)</span></div>'),
            "title": grab(r'<h3 class="g-journey__t">(.*?)</h3>'),
            "para": grab(r'<p class="g-journey__p">(.*?)</p>'),
            "moments": [_say(v) for b in re.findall(r'<ul class="g-journey__list">(.*?)</ul>', d, re.S)
                        for v in re.findall(r"<li>(.*?)</li>", b, re.S)],
            "labels": re.findall(r'<b lang="fr">', d),
        })
    return days, _say(sec)


def day_words(day):
    return " ".join([day["title"], day["para"]] + day["moments"])


def too_fine(text):
    return [p for p in TOO_FINE + OLD_ROOMS if re.search(p, text, re.I)]


def expected_heading(n, start):
    """'Day 3 · Monday · Lundi' for day n of a retreat starting on `start`."""
    wd = (start.weekday() + n - 1) % 7
    return f"Day {n} · {EN[wd]} · {FR[wd]}"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id IN (SELECT id FROM workshops WHERE title LIKE ?)",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("Each retreat's days (owner, 9 November)")
    _cleanup()
    c = m.app.test_client()

    s.section("The week, worked out from the dates")
    friday = [{"session": {"start_date": "2027-06-25", "end_date": "2027-06-30"}}]
    wk = m.retreat_week({"nights_label": "5 nights / 6 days"}, friday)
    s.check("a Friday start, five nights: six days, Friday to Wednesday",
            wk["days"] == [(EN[i % 7], FR[i % 7]) for i in range(4, 10)], detail=str(wk))
    wk = m.retreat_week({"nights_label": "4 nights / 5 days"}, friday + [
        {"session": {"start_date": "2027-07-03", "end_date": "2027-07-07"}}])
    s.check("two sittings on different weekdays: no weekday is named", wk["days"] == [], detail=str(wk))
    s.check("but the nights still come from the label", wk["nights"] == 4, detail=str(wk))
    wk = m.retreat_week({"nights_label": ""}, [{"session": {"start_date": "2027-06-18", "end_date": "2027-06-22"}}])
    s.check("no label: the nights come from the sitting", wk["nights"] == 4 and len(wk["days"]) == 5, detail=str(wk))
    s.check("no sittings: nothing named", m.retreat_week({"nights_label": "3 nights"}, [])["days"] == [])
    s.check("June dates are summer", m.retreat_week({"nights_label": "5 nights"}, friday)["summer"] is True)
    s.check("one start date: each day's date, for the evenings that fall on an occasion",
            m.retreat_week({"nights_label": "5 nights"}, friday)["dates"]
            == ["06-25", "06-26", "06-27", "06-28", "06-29", "06-30"])
    s.check("two start dates: no dates, since one plan cannot hold one sitting's 14 July",
            m.retreat_week({"nights_label": "5 nights"}, friday + [
                {"session": {"start_date": "2027-07-02", "end_date": "2027-07-07"}}])["dates"] == [])
    s.check("January dates are not", m.retreat_week({"nights_label": "3 nights"}, [
        {"session": {"start_date": "2027-01-09", "end_date": "2027-01-12"}}])["summer"] is False)

    s.section("The guard can fail")
    s.check("a dish in a copper mould is caught",
            too_fine("champagne jellies in antique copper moulds") == [r"copper", r"\bmoulds?\b", r"champagne",
                                                                       r"jell(?:y|ies)"])
    s.check("and so is the tasting beside the cellar", too_fine("a tasting beside the cellar") == [r"beside the cellar"])
    s.check("and the broad words are not", too_fine("A wine tasting in Le Bar, then the disco") == [])
    s.check("a heading one day out is a different heading",
            expected_heading(1, date(2027, 6, 25)) == "Day 1 · Friday · Vendredi"
            and expected_heading(2, date(2027, 6, 25)) != "Day 2 · Friday · Vendredi")

    # The private stay, as it was set up on live (9 Nov): four nights from a Friday, no nights label, the transfers
    # and the outings ticked. It had no day-by-day at all, only the generic outline.
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    cur = conn.execute(
        "INSERT INTO workshops (title, description, price_per_person, active, created_at, private_token, "
        "transfers_included, excursions_included) VALUES (?, '', 3000, 1, ?, ?, 1, 1)",
        (f"{TAG} Four Starry French Summer Nights", now, "zzrdprivatekey"))
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
                 "VALUES (?, '2027-06-18', '2027-06-22', 10, ?)", (cur.lastrowid, now))
    # A three-night retreat set in January: every plan but Noël's is a summer plan, so this one must not get one.
    # Found 9 Nov: another suite's January sitting of The Long Weekender drew the pool, the terrace and yoga.
    cur = conn.execute("INSERT INTO workshops (title, description, price_per_person, active, created_at, nights_label) "
                       "VALUES (?, '', 2000, 1, ?, '3 nights / 4 days')", (f"{TAG} Antique Finds in January", now))
    winter_id = cur.lastrowid
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
                 "VALUES (?, '2027-01-09', '2027-01-12', 10, ?)", (winter_id, now))
    # One retreat with two sittings that start on different weekdays.
    cur = conn.execute("INSERT INTO workshops (title, description, price_per_person, active, created_at, nights_label) "
                       "VALUES (?, '', 2000, 1, ?, '3 nights / 4 days')", (f"{TAG} Two Weekdays", now))
    two_id = cur.lastrowid
    for start, end in (("2027-07-03", "2027-07-06"), ("2027-07-08", "2027-07-11")):
        conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
                     "VALUES (?, ?, ?, 10, ?)", (two_id, start, end, now))
    conn.commit()
    today = m.house_today()
    offer = [(w, sittings) for w, sittings in m.workshops_on_offer(conn, today)
             if not w["title"].startswith(TAG)]
    pages = [(w["title"], "/workshops/%d" % w["id"], sittings, m.retreat_week(w, sittings)["nights"])
             for w, sittings in offer]
    private = conn.execute("SELECT * FROM workshops WHERE title LIKE ?", (f"{TAG} Four%",)).fetchone()
    private_sittings = m.public_sittings(conn, private["id"], today)
    pages.append((private["title"], "/p/zzrdprivatekey", private_sittings,
                  m.retreat_week(private, private_sittings)["nights"]))
    conn.close()
    s.check("there are retreats on offer to read", len(offer) >= 3, detail=f"{len(offer)} on offer")

    mirepoix_seen = winter_seen = occasions_seen = 0
    ariegeoise_on = []          # (retreat, day) wherever L'Ariégeoise was drawn
    for title, path, sittings, nights in pages:
        r = c.get(path)
        html = r.get_data(as_text=True)
        days, section = journey(html)
        s.section(f"{title}")
        s.check(f"{title}: the page answers", r.status_code == 200, detail=str(r.status_code))
        starts = {date.fromisoformat(str(x["start_date"])) for x in sittings}
        # Every plan but Noël's is a summer plan, for dates May to September (this file's own reading of it). A retreat
        # on offer with dates outside them -- another suite's January Long Weekender, in a full run -- has the outline.
        if not re.search(r"No[eë]l|Christmas", title) and not all(5 <= d.month <= 9 for d in starts):
            cold = [p for p in NOT_IN_WINTER if re.search(p, section, re.I)]
            s.check(f"{title}: dates outside the summer, so the outline and no summer days", days == [] and not cold,
                    detail=f"{len(days)} days, {cold}")
            continue
        # A plan is written for three, four, five and seven nights. An atelier with no length and no dates -- the
        # "Session Change Test" rows another suite once left in the local database -- has the outline, rightly.
        if nights not in (3, 4, 5, 7):
            s.check(f"{title}: no length to plan by, so the outline", days == [], detail=f"{nights} nights, {len(days)} days")
            continue
        s.check(f"{title}: a day-by-day plan", len(days) >= 4, detail=f"{len(days)} days")
        if not days:
            continue
        start = min(starts)
        same_weekday = len({d.weekday() for d in starts}) == 1
        s.check(f"{title}: its dates start on one weekday, so the days can be named", same_weekday,
                detail=", ".join(sorted(str(d) for d in starts)))
        want = [expected_heading(n, start) for n in range(1, len(days) + 1)]
        got = [d["kicker"] for d in days]
        s.check(f"{title}: every day headed Day N · the weekday · le jour", got == want,
                detail=f"{got[:3]} want {want[:3]}")
        s.check(f"{title}: and the same beside the day on a wide screen",
                [d["label"] for d in days] == [w.replace(" · ", " ") for w in want],
                detail=str([d["label"] for d in days][:3]))
        s.check(f"{title}: and a screen reader hears the day and its weekday",
                all(d["title"].startswith(w.split(" · ")[0] + ", " + w.split(" · ")[1] + ":")
                    for d, w in zip(days, want)), detail=days[0]["title"][:60])

        for n, d in enumerate(days, 1):
            fine = too_fine(day_words(d))
            s.check(f"{title}, day {n}: broad, with no dish, drink, restaurant or old room name", not fine,
                    detail=f"{fine}: {day_words(d)[:120]}")
            s.check(f"{title}, day {n}: its heading and bullets, no paragraph under it", d["para"] == "",
                    detail=d["para"][:80])
            s.check(f"{title}, day {n}: plain bullets, no Matin / Après-midi / Soir", not d["labels"])
            want_n = (1, 2) if n == len(days) else (2, 3, 4)
            s.check(f"{title}, day {n}: {'one or two' if n == len(days) else 'two to four'} bullets",
                    len(d["moments"]) in want_n, detail=str(d["moments"]))
            long_m = [x for x in d["moments"] if len(x.split()) > MOMENT_WORDS]
            s.check(f"{title}, day {n}: each bullet a line", not long_m, detail=str(long_m[:1]))
            if "Mirepoix" in day_words(d):
                mirepoix_seen += 1
                wd = EN[(start.weekday() + n - 1) % 7]
                s.check(f"{title}, day {n}: Mirepoix on market day, a Monday", wd == "Monday", detail=wd)
        # 10 Nov (owner): "Bastille Day dinner & party", "Fête de la Musique - dinner in Salon de Musique". The dates come
        # from this file's own arithmetic on the sitting, not the app's.
        if len(starts) == 1:
            for n, d in enumerate(days[:-1], 1):
                md = (start + timedelta(days=n - 1)).strftime("%m-%d")
                full = (start + timedelta(days=n - 1)).isoformat()
                words = day_words(d)
                # 25 Nov (owner): L'Ariégeoise, keyed by its full date because the race moves. On 26 June 2027 the
                # evening becomes it and keeps the day's barbecue and film; on any other day it is not there.
                if "Ariégeoise" in words:
                    ariegeoise_on.append((title, n))
                if full == "2027-06-26":
                    last_night = n == len(days) - 1
                    s.check(f"{title}, day {n} is 26 June 2027: L’Ariégeoise, its evening and its heading",
                            "L’Ariégeoise" in d["moments"][-1]
                            and (last_night or ("barbecue" in d["moments"][-1]
                                                and "film under the stars" in d["moments"][-1]
                                                and d["title"].endswith("and L’Ariégeoise"))),
                            detail=f"{d['title']} | {d['moments']}")
                else:
                    s.check(f"{title}, day {n}: no L’Ariégeoise on any other day", "Ariégeoise" not in words,
                            detail=full)
                if md == "07-14":
                    occasions_seen += 1
                    s.check(f"{title}, day {n} is 14 July: the Bastille Day dinner and party",
                            "Bastille Day" in d["moments"][-1] and ("party" in words or "dancing" in words)
                            and (n == len(days) - 1 or "Bastille Day" in d["title"]), detail=words[-160:])
                elif md == "06-21":
                    occasions_seen += 1
                    s.check(f"{title}, day {n} is 21 June: the Fête de la Musique, dinner in the Salon de Musique",
                            "Fête de la Musique" in words and "Salon de Musique" in words, detail=words[-160:])
                else:
                    s.check(f"{title}, day {n}: no occasion on an ordinary day",
                            "Bastille" not in words and "Fête de la Musique" not in words, detail=md)
        s.check(f"{title}: no evening marker left on the page", not any("|" in day_words(d) for d in days))
        if len(days) - 1 in (5, 7):
            # the page promises dinners at the two Les Cabannes restaurants, so two evenings go out, whatever 14 July took.
            # 25 Nov: "dinner in the village of Les Cabannes", never "dinner out".
            outs = sum("dinner in the village of les cabannes" in day_words(d).lower() for d in days)
            s.check(f"{title}: two dinners in the village of Les Cabannes, as its page promises", outs == 2,
                    detail=f"{outs}")
        s.check(f"{title}: no 'dinner out' and no bullet of 'Leisure time'",
                "dinner out" not in section.lower()
                and not any("leisure time" in x.lower() for d in days for x in d["moments"]),
                detail=str([x for d in days for x in d["moments"] if "leisure" in x.lower() or "out in" in x])[:200])
        every = " ".join(day_words(d) for d in days)
        # 11 Nov (the owner's site-copy brief): "A wine tasting, then dinner out", with no room named; a room that is
        # named is still the right one (OLD_ROOMS above catches "beside the cellar").
        last = day_words(days[-2])
        s.check(f"{title}: the last night is the banquet in the Salon de Musique, then dancing",
                "Salon de Musique" in last and "dancing" in last, detail=last[-120:])
        if any(d.month in WINTER_MONTHS for d in starts):
            cold = [p for p in NOT_IN_WINTER if re.search(p, section, re.I)]
            s.check(f"{title}: winter dates, and nothing in its days that needs summer", not cold, detail=str(cold))
        if any(d.month in WINTER_MONTHS for d in starts) and re.search(r"No[eë]l|Christmas", title):
            winter_seen += 1
            ticks = re.search(r'<ul class="g-ticks">(.*?)</ul>', html, re.S)
            read = section + " " + (_say(ticks.group(1)) if ticks else "")
            cold = [p for p in NOT_IN_WINTER if re.search(p, read, re.I)]
            s.check(f"{title}: in winter, nothing that needs summer", not cold, detail=str(cold))
            s.check(f"{title}: and no yoga anywhere on the page", not re.search(r"\byoga\b", _say(html), re.I))
            # 11 Nov (the owner's site-copy brief): the days as the brief gives them, and no drink named on one.
            for thing in ("a welcome glass by the fire", "Christmas baking", "gingerbread château", "Christmas film",
                          "dressing the tree", "Christmas banquet"):
                s.check(f"{title}: {thing}", thing in every, detail=every[:100])
            s.check(f"{title}: and no vin chaud on a day", "vin chaud" not in every)

    s.check("a Mirepoix day was there to check", mirepoix_seen >= 2, detail=f"{mirepoix_seen}")
    s.check("a 14 July and a 21 June were there to check", occasions_seen >= 2, detail=f"{occasions_seen}")
    # The owner's case, by name rather than by number: Cooking in the Cuisine is /workshops/8 on live and a
    # different id in a copy. 25 to 30 June 2027, so 26 June is its second day, En cuisine.
    s.check("Cooking in the Cuisine shows L’Ariégeoise on day 2, and nothing else does",
            ariegeoise_on == [("Cooking in the Cuisine", 2)], detail=str(ariegeoise_on))
    s.check("a winter retreat was there to check", winter_seen >= 1,
            detail="Noël was not on offer, so the winter rules proved nothing")

    s.section("The opening paragraphs, as broad as the days (owner, 10 Nov)")
    # "fix it, it can be generic": no dish in any opening paragraph; and "could be 2 nights to a week": only a week
    # says "a week". The private stay is four nights, and its page opened "A week of long, golden days".
    # Not "copper": copper pans are among the finds on Antique & French Finds, which is what a brocante sells, not a
    # dish. Noël's old "champagne jellies in antique copper moulds" is still caught three ways.
    dishes = [r"fillet", r"ballotine", r"guinea fowl", r"\btrout\b", r"\bbeef\b", r"\bmoulds?\b",
              r"jell(?:y|ies)", r"champagne"]
    for t, nights in (("Noël at Gudanes", "4 nights / 5 days"), ("Cooking in the Cuisine", "5 nights / 6 days"),
                      ("Antique & French Finds", "3 nights / 4 days"), ("Seven Starry Nights", "7 nights / 8 days"),
                      ("Four Starry French Summer Nights", "")):
        with m.app.test_request_context("/"):
            para = _say(m.app.jinja_env.from_string(
                "{% from '_ws_copy.html' import ws_copy %}{{ ws_copy(w) }}").render(w={"title": t, "nights_label": nights}))
        named = [d for d in dishes if re.search(d, para, re.I)]
        s.check(f"{t}: its opening paragraph names no dish", not named, detail=f"{named}: {para[:100]}")
        week = para.lower().startswith("a week")
        s.check(f"{t}: it says 'a week' only if it is one", week == (nights.startswith("7")), detail=para[:60])
    private_page = _say(c.get("/p/zzrdprivatekey").get_data(as_text=True))
    s.check("the private stay's own page does not call four nights a week", "A week of" not in private_page,
            detail=private_page[private_page.find("A week"):][:80])

    s.section("Cooking in the Cuisine: time in the kitchen every full day (owner, 25 Nov)")
    # "add in more cooking classes, remove market with the chef ... it's on Sundays? ... there's hardly any cooking
    # classes". The plan had the chef taking guests to market on the Saturday; the village market at Les Cabannes is
    # a Sunday, Mirepoix the Monday. Drawn from the template with the 2027 dates, so it holds without the retreat
    # being in the copy of the database.
    with m.app.test_request_context("/"):
        cooking = m.app.jinja_env.from_string(
            "{% from '_ws_copy.html' import ws_journey %}{{ ws_journey(w, sittings=rows) }}").render(
            w={"title": "Cooking in the Cuisine", "nights_label": "5 nights / 6 days"},
            rows=[{"session": {"start_date": "2027-06-25", "end_date": "2027-06-30"}}])
    days, section = journey(cooking)
    s.check("six days, Friday to Wednesday", [d["kicker"] for d in days]
            == [expected_heading(n, date(2027, 6, 25)) for n in range(1, 7)], detail=str([d["kicker"] for d in days]))
    s.check("no market with the chef", not re.search(r"market with the chef\b", section, re.I),
            detail=section[section.lower().find("market with"):][:80])
    kitchen = re.compile(r"cooking class|pastry kitchen|chocolate-making", re.I)
    s.check("every full day has its time in the kitchen",
            len(days) == 6 and all(kitchen.search(" ".join(d["moments"])) for d in days[1:5]),
            detail=" | ".join("; ".join(d["moments"]) for d in days[1:5]))
    s.check("three of them cooking classes or the pastry kitchen, besides the chocolate",
            sum(bool(re.search(r"cooking class|pastry kitchen", " ".join(d["moments"]), re.I)) for d in days) >= 3,
            detail=" | ".join("; ".join(d["moments"]) for d in days))
    marketing = [d["kicker"] for d in days if re.search(r"\bmarket\b", day_words(d), re.I)]
    s.check("a market only on the Monday, Mirepoix's day", marketing and all("Monday" in k for k in marketing),
            detail=str(marketing))

    s.section("A retreat set in winter that is not Noël")
    r = c.get("/workshops/%d" % winter_id)
    html = r.get_data(as_text=True)
    days, section = journey(html)
    s.check("the page answers", r.status_code == 200, detail=str(r.status_code))
    s.check("no summer day-by-day for January", days == [], detail=f"{len(days)} days")
    cold = [p for p in NOT_IN_WINTER if re.search(p, section, re.I)]
    s.check("and its outline promises nothing that needs summer", not cold and "Afternoons" in section,
            detail=f"{cold}: {section[:160]}")

    s.section("Dates on two weekdays")
    r = c.get("/workshops/%d" % two_id)
    days, _section = journey(r.get_data(as_text=True))
    s.check("the page answers, with its plan", r.status_code == 200 and len(days) == 4,
            detail=f"{r.status_code}, {len(days)} days")
    s.check("each day is 'Day N' and no weekday", [d["kicker"] for d in days] == [f"Day {n}" for n in range(1, 5)],
            detail=str([d["kicker"] for d in days]))

    s.section("L’Ariégeoise moves, so it is keyed by its full date (owner, 25 Nov)")
    # Five nights from Sunday 25 June 2028: 26 June is day 2, En cuisine, the day the 2027 retreat celebrates.
    # Keyed by month and day it would fire here too; keyed by the full date it does not.
    conn = db()
    cur = conn.execute("INSERT INTO workshops (title, description, price_per_person, active, created_at, nights_label) "
                       "VALUES (?, '', 2000, 1, ?, '5 nights / 6 days')", (f"{TAG} Five Nights in 2028", now))
    later_id = cur.lastrowid
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
                 "VALUES (?, '2028-06-25', '2028-06-30', 10, ?)", (later_id, now))
    conn.commit()
    conn.close()
    r = c.get("/workshops/%d" % later_id)
    days, section = journey(r.get_data(as_text=True))
    s.check("the page answers, with its six days", r.status_code == 200 and len(days) == 6,
            detail=f"{r.status_code}, {len(days)} days")
    s.check("its 26 June keeps its own evening, the barbecue and the film",
            len(days) == 6 and days[1]["moments"][-1] == "A barbecue in the grounds, then a film beneath the stars",
            detail=str(days[1]["moments"] if len(days) > 1 else days))
    s.check("and L’Ariégeoise is nowhere on it", "Ariégeoise" not in section, detail=section[:200])
    with m.app.test_request_context("/"):
        wk = m.retreat_week({"nights_label": "5 nights / 6 days"},
                            [{"session": {"start_date": "2027-06-25", "end_date": "2027-06-30"}}])
    s.check("retreat_week gives the full date of each day beside the month-day",
            wk["full"][1] == "2027-06-26" and wk["dates"][1] == "06-26" and len(wk["full"]) == 6, detail=str(wk))

    invitation(s, c)
    _cleanup()
    return s


# Every plan in _ws_copy.html, drawn from the macro rather than found on offer, so the four-night plans nobody is
# selling this month are read too. Dates in 2028 that no occasion falls on. The bullets each day had before the
# 25 Nov words, from the plans as they stood: the words changed, the shape did not.
PLANS = [
    ("Cooking in the Cuisine", {"title": "Cooking in the Cuisine", "nights_label": "5 nights / 6 days"},
     ("2028-06-02", "2028-06-07"), [3, 3, 3, 3, 3, 1]),
    ("Seven Starry Nights", {"title": "Seven Starry Nights", "nights_label": "7 nights / 8 days"},
     ("2028-06-03", "2028-06-10"), [3, 3, 3, 3, 3, 3, 3, 1]),
    ("Antique & French Finds", {"title": "Antique & French Finds", "nights_label": "3 nights / 4 days"},
     ("2028-06-03", "2028-06-06"), [4, 4, 3, 1]),
    ("the private stay's four nights", {"title": "Four Starry French Summer Nights", "transfers_included": 1,
                                        "excursions_included": 1}, ("2028-06-02", "2028-06-06"), [3, 3, 3, 3, 1]),
    ("four summer nights at the house", {"title": "A Summer Atelier", "nights_label": "4 nights / 5 days",
                                         "excursions_included": 0}, ("2028-06-02", "2028-06-06"), [3, 3, 3, 3, 1]),
    ("Noël at Gudanes", {"title": "Noël at Gudanes", "nights_label": "4 nights / 5 days"},
     ("2028-12-15", "2028-12-19"), [3, 4, 3, 3, 1]),
]
# Lines the 25 Nov brief kept word for word. Their French and Spanish come from the translation job; every other line
# on the days is new and has its own in translations.py, so a French or Spanish reader never meets it in English.
KEPT = {"Christmas baking with the pastry chef", "Dinner by candlelight", "Chocolate-making with the chefs",
        "The Christmas banquet in the Salon de Musique, then dancing", "Arrive in the afternoon, to a welcome glass by the fire",
        "Dinner by candlelight, then a film", "The art of the find, and how to bargain in French",
        "L’apéritif in the Salon de Musique, then a candlelit welcome dinner", "Arrive in the afternoon, to a welcome glass"}
FAREWELL = "The farewell banquet in the Salon de Musique, then dancing"


def dishes_in_days(html):
    """test_site_review_2's reader, imported when it is used: that suite imports nothing of this one, but a cycle
    tomorrow should cost nothing at import."""
    from test_site_review_2 import dishes_in_days as reader
    return reader(html)


def _bullets(html, ul_class):
    return [[_say(v) for v in re.findall(r"<li>(.*?)</li>", b, re.S)]
            for b in re.findall(r'<ul class="%s">(.*?)</ul>' % ul_class, html, re.S)]


def invitation(s, c):
    """The 25 Nov words: an invitation, with every fact and the shape of every day as it was."""
    s.section("The days as an invitation (owner, 25 Nov)")
    for name, w, (start, end), shape in PLANS:
        with m.app.test_request_context("/"):
            html = m.app.jinja_env.from_string(
                "{% from '_ws_copy.html' import ws_journey with context %}{{ ws_journey(w, sittings=rows) }}").render(
                w=w, rows=[{"session": {"start_date": start, "end_date": end}}])
        days, section = journey(html)
        moments = [x for d in days for x in d["moments"]]
        s.check(f"{name}: every day keeps the bullets it had", [len(d["moments"]) for d in days] == shape,
                detail=f"{[len(d['moments']) for d in days]} want {shape}")
        s.check(f"{name}: no bullet says 'Leisure time'", not [x for x in moments if "leisure time" in x.lower()],
                detail=str([x for x in moments if "leisure" in x.lower()]))
        s.check(f"{name}: but the everyday block above the days keeps its heading",
                '<p class="g-everyday__en">Leisure time</p>' in html)
        s.check(f"{name}: 'dinner out' nowhere", "dinner out" not in section.lower())
        long_m = [x for x in moments if len(x.split()) > MOMENT_WORDS]
        s.check(f"{name}: each bullet a line", not long_m, detail=str(long_m[:1]))
        # test_site_review_2's reader, here on the plans no page is selling yet: the brief's "welcome glass of
        # crémant" on the four nights at the house would have gone in unseen, since no such retreat is on offer.
        dishes = dishes_in_days(html)
        s.check(f"{name}: no dish or drink named on a day, a tasting apart", dishes == [], detail=str(dishes))
        last = days[-2]["moments"][-1] if len(days) > 1 else ""
        if name.startswith("Noël"):
            s.check(f"{name}: the last night is the Christmas banquet",
                    last == "The Christmas banquet in the Salon de Musique, then dancing", detail=last)
            evenings = [d["moments"][-1] for d in days[:-1]]
            dark = [(e, p) for e in evenings for p in OUTDOORS if re.search(p, e, re.I)]
            s.check(f"{name}: nothing outdoors once the light goes", not dark, detail=str(dark))
            cold = [p for p in NOT_IN_WINTER if re.search(p, section, re.I)]
            s.check(f"{name}: and nothing on any day that needs summer", not cold, detail=str(cold))
            out = [p for p in OUTINGS if re.search(p, section)]
            s.check(f"{name}: and no outing: no vans in winter", not out, detail=str(out))
        else:
            s.check(f"{name}: the last night is the farewell banquet", last == FAREWELL, detail=last)
        if name in ("Cooking in the Cuisine", "Seven Starry Nights", "Antique & French Finds",
                    "the private stay's four nights"):
            # The transfers: midday at the hotel (owner, 24 Nov), and back to Toulouse at eleven.
            first, gone = days[0]["moments"][0], days[-1]["moments"][0]
            s.check(f"{name}: the first day starts at midday at the Grand Hôtel de l’Opéra, Toulouse",
                    first.startswith("Collected at midday from the Grand Hôtel de l’Opéra, Toulouse"), detail=first)
            s.check(f"{name}: and the last goes back to Toulouse at eleven", "Toulouse at eleven" in gone, detail=gone)
        if name == "Cooking in the Cuisine":
            s.check(f"{name}: no pool on its days (owner, 10 Nov: too cold)", not re.search(r"\bpool\b", section, re.I))
            s.check(f"{name}: the frescoes on its days (owner, 23 Nov)", "frescoes" in section)
        if name == "Seven Starry Nights":
            s.check(f"{name}: the frescoes and painted beams on its days (owner, 23 Nov)",
                    "frescoes and painted beams" in section)
        for lang in ("fr", "es"):
            tdays, _t = journey(m.translate_page(html, lang))
            still = [en for d, td in zip(days, tdays) for en, said in zip(d["moments"], td["moments"])
                     if en == said and en not in KEPT]
            s.check(f"{name}: every new line has its {lang.upper()} in translations.py", not still and tdays,
                    detail=str(still[:3]))

    s.section("/events/private as an invitation (owner, 25 Nov)")
    html = c.get("/events/private").get_data(as_text=True)
    sample = _bullets(html, "g-wed-days__l")
    choose = [_say(x) for x in re.findall(r'<p class="g-pdays__p">(.*?)</p>', html, re.S)]
    every = [x for day in sample for x in day] + choose
    s.check("the sample keeps its days and bullets", [len(d) for d in sample] == [3, 3, 3, 2],
            detail=str([len(d) for d in sample]))
    s.check("and Choose Your Days its five", len(choose) == 5, detail=str(choose))
    s.check("no line says 'Leisure time'", not [x for x in every if "leisure time" in x.lower()],
            detail=str([x for x in every if "leisure" in x.lower()]))
    s.check("'dinner out' nowhere on the page", "dinner out" not in _say(html).lower())
    s.check("the sample's last night is the farewell banquet", len(sample) == 4 and sample[2][-1] == FAREWELL,
            detail=str(sample[2] if len(sample) > 2 else sample))
    for lang in ("fr", "es"):
        said = m.translate_page(html, lang)
        tsample = _bullets(said, "g-wed-days__l")
        tchoose = [_say(x) for x in re.findall(r'<p class="g-pdays__p">(.*?)</p>', said, re.S)]
        still = [en for en, t in zip(every, [x for day in tsample for x in day] + tchoose) if en == t and en not in KEPT]
        s.check(f"every new line has its {lang.upper()} in translations.py", not still and tchoose, detail=str(still[:3]))


if __name__ == "__main__":
    print(run().report())
