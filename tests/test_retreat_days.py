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
  know which table is open); a day's words short enough to read as a shape.

  THE ROOMS. Le Bar and la Bibliothèque, never the Château Bar, the library or a
  tasting "beside the cellar"; the wine tasting is in Le Bar; the last night ends
  at the disco.
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
# Nothing for a December day: too cold, too dark, and the river and the pool are shut.
NOT_IN_WINTER = [r"\byoga\b", r"\bpool\b", r"terrace", r"under the stars", r"\bcanoe\b", r"\bkayak\b",
                 r"barbecue", r"(?<!table )\btennis\b", r"ice cream", r"\briver\b"]
WINTER_MONTHS = {11, 12, 1, 2}
PARA_WORDS, MOMENT_WORDS = 70, 12


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
            "moments": [_say(v) for v in re.findall(r'<span class="g-journey__mt">(.*?)</span></li>', d, re.S)],
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
            words = len(d["para"].split())
            s.check(f"{title}, day {n}: short enough to read as the shape of a day", words <= PARA_WORDS,
                    detail=f"{words} words")
            long_m = [x for x in d["moments"] if len(x.split()) > MOMENT_WORDS]
            s.check(f"{title}, day {n}: morning, afternoon and evening in a line each", not long_m,
                    detail=str(long_m[:1]))
            if "Mirepoix" in day_words(d):
                mirepoix_seen += 1
                wd = EN[(start.weekday() + n - 1) % 7]
                s.check(f"{title}, day {n}: Mirepoix on market day, a Monday", wd == "Monday", detail=wd)
        # 10 Nov (owner): "Bastille Day dinner & party", "Fête de la Musique - dinner in Salon de Musique". The dates come
        # from this file's own arithmetic on the sitting, not the app's.
        if len(starts) == 1:
            for n, d in enumerate(days[:-1], 1):
                md = (start + timedelta(days=n - 1)).strftime("%m-%d")
                words = day_words(d)
                if md == "07-14":
                    occasions_seen += 1
                    s.check(f"{title}, day {n} is 14 July: the Bastille Day dinner and party",
                            "le 14 juillet" in words and "Bastille Day" in words
                            and ("party" in words or "disco" in words), detail=words[-160:])
                elif md == "06-21":
                    occasions_seen += 1
                    s.check(f"{title}, day {n} is 21 June: the Fête de la Musique, dinner in the Salon de Musique",
                            "Fête de la Musique" in words and "Salon de Musique" in words, detail=words[-160:])
                else:
                    s.check(f"{title}, day {n}: no occasion on an ordinary day",
                            "Bastille" not in words and "Fête de la Musique" not in words, detail=md)
        s.check(f"{title}: no evening marker left on the page", not any("|" in day_words(d) for d in days))
        if len(days) - 1 in (5, 7):
            # the page promises dinners at the two Les Cabannes restaurants, so two evenings go out, whatever 14 July took
            outs = sum("dinner out in Les Cabannes" in d["para"] for d in days)
            s.check(f"{title}: two dinners out in Les Cabannes, as its page promises", outs == 2, detail=f"{outs}")
        every = " ".join(day_words(d) for d in days)
        if "tasting" in every:
            s.check(f"{title}: the wine tasting is in Le Bar", "tasting in Le Bar" in every,
                    detail=every[every.find("tasting") - 40:every.find("tasting") + 40])
        s.check(f"{title}: the last night ends at the disco", "disco" in day_words(days[-2]),
                detail=days[-2]["para"][-120:])
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
            for thing in ("Christmas market", "Christmas tree", "wreaths", "Carols", "vin chaud", "gingerbread château"):
                s.check(f"{title}: {thing}", thing in every, detail=every[:100])

    s.check("a Mirepoix day was there to check", mirepoix_seen >= 2, detail=f"{mirepoix_seen}")
    s.check("a 14 July and a 21 June were there to check", occasions_seen >= 2, detail=f"{occasions_seen}")
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

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
