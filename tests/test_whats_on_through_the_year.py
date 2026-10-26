"""What's On: the owner's six new sections, and the year's dates.

The owner's brief of 26 October 2026 (gudanes-whats-on-new-sections.md, from
the research of the same day) added sections for the hot springs, outdoors,
winter, the caves, Andorra and what is close by, and asked for the dates list
to show more than markets: ski openings, races, festivals, the caves
reopening.

What this holds:

  - THE SECTIONS ARE THERE, in the owner's words, and every link out opens in
    a new tab without handing the page to the site it opens.
  - WHAT THE RESEARCH SAID TO LEAVE OUT IS OUT: the wild springs at Mérens
    (possibly private land) and Carcanières (no longer a spa).
  - A DATE THAT RUNS SEVERAL DAYS is printed as its days ("8–14 March") and
    stays on the page while it runs, not only on its first day.
  - A DATE NOBODY HAS FIXED is printed in the house's words ("Late December"),
    never as the placeholder day it is filed under.
  - ALL YEAR is listed apart, and a date that is over goes.
  - The year's dates are seeded once: one the owner deletes stays deleted.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, visible_text
import _harness

m = _harness.m
TAG = "wo-ty-"


def _cleanup(conn):
    conn.execute("DELETE FROM whats_on WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def _add(conn, title, **kw):
    cols = {"title": TAG + title, "description": None, "location": None,
            "distance": None, "weekday": None, "event_date": None,
            "event_until": None, "when_words": None, "start_time": None,
            "end_time": None, "is_active": 1, "sort_order": 0}
    cols.update(kw)
    conn.execute(
        f"""INSERT INTO whats_on ({','.join(cols)}, created_at)
            VALUES ({','.join('?' * len(cols))}, ?)""",
        tuple(cols.values()) + (datetime.now(timezone.utc).isoformat(),))
    conn.commit()


def _block(html, start, end):
    """The page between two markers, so a check reads one list and not another."""
    a = html.find(start)
    b = html.find(end, a + 1) if a >= 0 else -1
    return html[a:b] if a >= 0 and b > a else ""


def _article(html, title):
    """The one agenda row carrying this title, or ""."""
    for row in re.findall(r"<article[\s\S]*?</article>", html):
        if title in row:
            return row
    return ""


def run():
    s = Suite("What's On through the year")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()
    _cleanup(conn)
    today = m.house_today()
    try:
        _run(s, oc, anon, conn, today)
    finally:
        _cleanup(conn)
        conn.close()
    return s


def _run(s, oc, anon, conn, today):
    r = anon.get("/whats-on")
    html = r.get_data(as_text=True)
    text = visible_text(html)
    s.check("the page opens to anybody", r.status_code == 200, r)

    s.section("The six new sections, in the owner's words")
    for heading in ("The Hot Springs", "Outdoors and Adventure", "Winter", "The Caves",
                    "Andorra", "Close By"):
        s.check(f"{heading} is there", f">{heading}</h2>" in html)
    for words in ("Ax-les-Thermes has some sixty hot springs",
                  "It is the only via ferrata in the Ariège",
                  "Our own snow: a high plateau at 1,800 metres",
                  "the original prehistoric paintings, not copies",
                  "Bring a passport or ID card: Andorra is outside the EU",
                  "An eleventh-century fortress and Cathar refuge"):
        s.check(f"“{words[:40]}…”", words in text)
    s.check("the existing places list is kept above them",
            0 < html.find("Towns and Places Nearby") < html.find("The Hot Springs"))
    s.check("and they come before the Journal and the close",
            html.find("Close By</h2>") < html.find("Follow the Restoration"))

    s.section("What the research said to leave out stays out")
    s.check("no Mérens hot springs", "Mérens hot" not in text
            and "wild hot springs" not in text.lower())
    s.check("no Carcanières", "Carcanières" not in text)
    s.check("no prices in the new sections",
            not re.search(r"€\s?\d", _block(html, 'id="hot-springs"', 'id="close-by"')))

    s.section("Every link out opens in a new tab, safely")
    sections = _block(html, 'id="hot-springs"', "THE JOURNAL")
    outward = re.findall(r'<a href="(https?://[^"]+)"([^>]*)>', sections)
    s.check("there are links out", len(outward) >= 25, detail=str(len(outward)))
    bare = [h for h, attrs in outward
            if 'target="_blank"' not in attrs or 'rel="noopener"' not in attrs]
    s.check("each one opens a new tab with rel=noopener", not bare, detail=str(bare[:3]))
    s.check("the tourist office's old address is not used",
            "vallees-ax.com" not in sections)
    s.check("nor the spa's or the resort's old ones",
            "bains-couloubret.com" not in sections and "ax3domaines" not in sections)

    s.section("The year's dates are in, once")
    titles = {r_["title"] for r_ in conn.execute("SELECT title FROM whats_on")}
    missing = [e["title"] for e in m.DEFAULT_WHATS_ON_DATES if e["title"] not in titles]
    s.check(f"all {len(m.DEFAULT_WHATS_ON_DATES)} are in the table", not missing,
            detail=str(missing[:3]))
    dupes = conn.execute(
        "SELECT title, COUNT(*) AS n FROM whats_on GROUP BY title HAVING n > 1").fetchall()
    s.check("none twice", not dupes, detail=str([d["title"] for d in dupes]))
    s.check("a placeholder day never stands in for words",
            all(e.get("date") for e in m.DEFAULT_WHATS_ON_DATES if not e.get("when"))
            and all(e.get("until") for e in m.DEFAULT_WHATS_ON_DATES
                    if e.get("when") and e.get("date")),
            detail="a loose date needs the words to print and a span to sit in")
    seeded_future = [e for e in m.DEFAULT_WHATS_ON_DATES
                     if e.get("date") and m.parse_date(e.get("until") or e["date"]) >= today]
    coming = _block(html, "Through the year", "</section>")
    shown = [e for e in seeded_future if e["title"] in visible_text(coming)
             or e["title"] in text]
    s.check("each one still to come is on the page", len(shown) == len(seeded_future),
            detail=f"{len(shown)} of {len(seeded_future)}")

    s.section("A date that runs several days")
    start = today + timedelta(days=20)
    _add(conn, "Festival", event_date=start.isoformat(),
         event_until=(start + timedelta(days=4)).isoformat())
    _add(conn, "Running now", event_date=(today - timedelta(days=2)).isoformat(),
         event_until=(today + timedelta(days=1)).isoformat())
    _add(conn, "Over", event_date=(today - timedelta(days=5)).isoformat(),
         event_until=(today - timedelta(days=1)).isoformat())
    page = anon.get("/whats-on").get_data(as_text=True)
    week = _block(page, "This Week", "Coming Up")
    label = m.whats_on_when({"when_words": None}, start, start + timedelta(days=4))
    s.check("it is printed as its days", label in page, detail=label)
    s.check("one that began before today and runs on is in this week, as Today",
            TAG + "Running now" in week
            and re.search(r"Today</span>[\s\S]{0,400}" + re.escape(TAG + "Running now"), week)
            is not None)
    s.check("one that has finished is gone", TAG + "Over" not in page)
    s.check("the days are written the house's way",
            m.whats_on_when({"when_words": None}, m.parse_date("2027-03-08"),
                            m.parse_date("2027-03-14")) == "8–14 March"
            and m.whats_on_when({"when_words": None}, m.parse_date("2026-12-30"),
                                m.parse_date("2027-01-02")) == "30 December – 2 January 2027")

    s.section("A date nobody has fixed is printed in words")
    filed = today + timedelta(days=30)
    _add(conn, "Loose", event_date=filed.isoformat(),
         event_until=(filed + timedelta(days=8)).isoformat(), when_words="Late Whenever")
    page = anon.get("/whats-on").get_data(as_text=True)
    row = _article(page, TAG + "Loose")
    s.check("the words are shown where the date goes", "Late Whenever" in row,
            detail=row[:200])
    s.check("and the day it is filed under is not",
            bool(row) and m.whats_on_date_label(filed) not in row, detail=row[:200])

    s.section("All year is listed apart")
    _add(conn, "Always open", when_words="All year")
    page = anon.get("/whats-on").get_data(as_text=True)
    s.check("under All Year", TAG + "Always open" in _block(page, "All Year</p>", "</section>"))
    s.check("the seeded all-year baths are there too",
            "Les Bains du Couloubret thermal baths, Ax-les-Thermes, open all year" in
            _block(page, "All Year</p>", "</section>"))

    s.section("Coming up is in the order things happen")
    _add(conn, "Later", event_date=(today + timedelta(days=60)).isoformat(), sort_order=-5)
    _add(conn, "Sooner", event_date=(today + timedelta(days=40)).isoformat(), sort_order=50)
    page = anon.get("/whats-on").get_data(as_text=True)
    s.check("sooner first, whatever order they were entered in",
            0 < page.find(TAG + "Sooner") < page.find(TAG + "Later"))

    s.section("The owner adds them on the admin page")
    r = oc.post("/admin/whats-on/save", data={
        "title": TAG + "Admin span", "weekday": "none",
        "event_date": (today + timedelta(days=50)).isoformat(),
        "event_until": (today + timedelta(days=52)).isoformat(), "is_active": "on"},
        follow_redirects=True)
    got = conn.execute("SELECT * FROM whats_on WHERE title = ?",
                       (TAG + "Admin span",)).fetchone()
    s.check("a last day is saved", got is not None and got["event_until"]
            == (today + timedelta(days=52)).isoformat(), r, detail=str(flashes(r)))
    r = oc.post("/admin/whats-on/save", data={
        "title": TAG + "Backwards", "weekday": "none",
        "event_date": (today + timedelta(days=50)).isoformat(),
        "event_until": (today + timedelta(days=49)).isoformat(), "is_active": "on"},
        follow_redirects=True)
    s.check("a last day before the first is refused",
            conn.execute("SELECT 1 FROM whats_on WHERE title = ?",
                         (TAG + "Backwards",)).fetchone() is None
            and any("last day" in f for f in flashes(r)), r)
    r = oc.post("/admin/whats-on/save", data={
        "title": TAG + "Words only", "weekday": "none", "when_words": "All year",
        "is_active": "on"}, follow_redirects=True)
    s.check("words alone are accepted", conn.execute(
        "SELECT 1 FROM whats_on WHERE title = ?", (TAG + "Words only",)).fetchone()
            is not None, r, detail=str(flashes(r)))
    listing = oc.get("/admin/whats-on").get_data(as_text=True)
    s.check("and the list shows both", TAG + "Admin span" in listing
            and TAG + "Words only" in listing)

    s.section("Seeded once: a date the owner deletes stays deleted")
    first = m.DEFAULT_WHATS_ON_DATES[0]["title"]
    kept = conn.execute("SELECT * FROM whats_on WHERE title = ?", (first,)).fetchone()
    conn.execute("DELETE FROM whats_on WHERE title = ?", (first,))
    conn.commit()
    try:
        made = m.seed_whats_on_dates(conn)
        s.check("seeding again puts nothing back", made == 0 and conn.execute(
            "SELECT 1 FROM whats_on WHERE title = ?", (first,)).fetchone() is None)
    finally:
        if kept:
            row = dict(kept)
            conn.execute(f"INSERT INTO whats_on ({','.join(row)}) "
                         f"VALUES ({','.join('?' * len(row))})", tuple(row.values()))
            conn.commit()


if __name__ == "__main__":
    print(run().report())
