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
from datetime import date, datetime, timezone

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
    pages = [(w["title"], "/workshops/%d" % w["id"], sittings) for w, sittings in offer]
    private = conn.execute("SELECT * FROM workshops WHERE title LIKE ?", (f"{TAG} Four%",)).fetchone()
    pages.append((private["title"], "/p/zzrdprivatekey", m.public_sittings(conn, private["id"], today)))
    conn.close()
    s.check("there are retreats on offer to read", len(offer) >= 3, detail=f"{len(offer)} on offer")

    mirepoix_seen = winter_seen = 0
    for title, path, sittings in pages:
        r = c.get(path)
        html = r.get_data(as_text=True)
        days, section = journey(html)
        s.section(f"{title}")
        s.check(f"{title}: the page answers", r.status_code == 200, detail=str(r.status_code))
        s.check(f"{title}: a day-by-day plan", len(days) >= 4, detail=f"{len(days)} days")
        if not days:
            continue
        starts = {date.fromisoformat(str(x["start_date"])) for x in sittings}
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
        every = " ".join(day_words(d) for d in days)
        if "tasting" in every:
            s.check(f"{title}: the wine tasting is in Le Bar", "tasting in Le Bar" in every,
                    detail=every[every.find("tasting") - 40:every.find("tasting") + 40])
        s.check(f"{title}: the last night ends at the disco", "disco" in day_words(days[-2]),
                detail=days[-2]["para"][-120:])
        if any(d.month in WINTER_MONTHS for d in starts):
            winter_seen += 1
            ticks = re.search(r'<ul class="g-ticks">(.*?)</ul>', html, re.S)
            read = section + " " + (_say(ticks.group(1)) if ticks else "")
            cold = [p for p in NOT_IN_WINTER if re.search(p, read, re.I)]
            s.check(f"{title}: in winter, nothing that needs summer", not cold, detail=str(cold))
            s.check(f"{title}: and no yoga anywhere on the page", not re.search(r"\byoga\b", _say(html), re.I))
            for thing in ("Christmas tree", "wreaths", "Carols", "vin chaud", "gingerbread château"):
                s.check(f"{title}: {thing}", thing in every, detail=every[:100])

    s.check("a Mirepoix day was there to check", mirepoix_seen >= 2, detail=f"{mirepoix_seen}")
    s.check("a winter retreat was there to check", winter_seen >= 1,
            detail="Noël was not on offer, so the winter rules proved nothing")

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
