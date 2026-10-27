"""What's On keeps itself up to date, from the official sites, and never guesses.

The owner, 26 October 2026: "i dont wanna add stuff ... it needs to be
accurate ... i dont want to be the person advertising wrong shit". So every
night the house reads the tourist office and Tourisme Occitanie, and:

  - CONFIRMS: an entry with an official page has its dates read off it, from
    the page's own data -- and only the event's dates. The same page carries
    other start/end pairs (what a price is valid for, "1 January to 31
    December"), and the first version read a March race as on all year.
  - ROLLS ON: an annual entry that is over comes back as next year's, in
    words ("July 2027, date to come"), never with a day nobody has published.
  - FINDS: what the sites list within reach is kept behind the scenes, sorted
    into events, regular activities and local listings. Nothing found reaches
    the public page until the owner presses Add.
  - SAYS WHAT IT COULD NOT DO: an unreadable page, an unconfirmed date, in a
    weekly task that closes itself once the list is clear.

No suite reaches a real site: the pages are handed to the reader here.
"""
import json
from datetime import timedelta
from urllib.error import HTTPError

from _harness import Suite, clients, db, flashes, keep_rows, visible_text
import _harness

m = _harness.m
TAG = "ZKEPT"
OT = "https://www.pyrenees-ariegeoises.com/a-voir-a-faire/sortir/agenda/"
OCC = "https://www.tourisme-occitanie.com/fr/fiche/fete-et-manifestation/"


def _ld(name, town, start=None, end=None):
    event = {"@type": "Event", "name": name,
             "location": {"@type": "Place", "address": {"@type": "PostalAddress",
                                                        "addressLocality": town}}}
    if start:
        event["startDate"], event["endDate"] = start, end or start
    return ('<script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org", "@graph": [event]}) + "</script>")


def ot_page(name, town, spans, tariff=True):
    """A tourist office event page as the site draws one: its schema.org
    Event without dates, a price valid all year, and its occurrence list."""
    tariffs = ('"tariffs":[{"periods":{"startDate":"2026-01-01","endDate":"2026-12-31"}}],'
               if tariff else "")
    fma = ",".join('{"startDate":"%sT00:00:00+01:00","endDate":"%sT23:59:59+01:00"}' % (a, b)
                   for a, b in spans)
    return ("<html>" + _ld(name, town) + "<script>var data={" + tariffs
            + '"fmaInsert":[' + fma + ']};</script></html>')


class Sites:
    """The tourism sites, as a dictionary of pages. Anything not in it is a 404."""

    def __init__(self, pages):
        self.pages, self.asked = dict(pages), []

    def __call__(self, url):
        self.asked.append(url)
        if url not in self.pages:
            raise HTTPError(url, 404, "Not Found", None, None)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def _cleanup(conn):
    conn.execute("DELETE FROM whats_on_found")
    conn.execute(f"DELETE FROM whats_on WHERE title LIKE '{TAG}%' OR title LIKE 'Festival {TAG}%'"
                 f" OR title LIKE 'The {TAG}%'")
    conn.execute(f"DELETE FROM tasks WHERE title = 'Look over What''s On'")
    conn.commit()


def _add(conn, title, **kw):
    cols = {"title": TAG + " " + title, "event_date": None, "event_until": None,
            "when_words": None, "source_url": None, "recurs_yearly": 0,
            "dates_status": "unconfirmed", "is_active": 1, "sort_order": 0}
    cols.update(kw)
    cur = conn.execute(
        f"INSERT INTO whats_on ({','.join(cols)}, created_at) "
        f"VALUES ({','.join('?' * len(cols))}, ?)",
        tuple(cols.values()) + (m.datetime.now(m.timezone.utc).isoformat(),))
    conn.commit()
    return cur.lastrowid


def _row(conn, row_id):
    return conn.execute("SELECT * FROM whats_on WHERE id = ?", (row_id,)).fetchone()


def run():
    s = Suite("What's On kept up by itself")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "whats_on", "1 = 1"),
                keep_rows(conn, "whats_on_found", "1 = 1"),
                keep_rows(conn, "tasks", "origin = 'watch'"),
                keep_rows(conn, "app_settings", "key = 'automation_whats_on_reader_enabled'")]
    # Only this suite's entries are read: everything already on the page is
    # put aside for the run and back afterwards.
    conn.execute("UPDATE whats_on SET is_active = 0")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, conn)
    finally:
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, conn):
    today = m.house_today()
    d = lambda n: (today + timedelta(days=n)).isoformat()

    s.section("Only the event's own dates are read")
    page = ot_page("Snow " + TAG, "Ax-les-Thermes", [(d(60), d(66))])
    parsed = m.parse_event_page(page)
    s.check("the race's dates, from its occurrence list",
            parsed and parsed["occurrences"] == [(today + timedelta(days=60),
                                                  today + timedelta(days=66))],
            detail=str(parsed))
    s.check("not the year a price is valid for",
            parsed and all(a.isoformat() != "2026-01-01" for a, _b in parsed["occurrences"]))
    s.check("with its name and town", parsed and parsed["title"] == "Snow " + TAG
            and parsed["town"] == "Ax-les-Thermes")
    s.check("schema.org dates are read where a page gives them",
            m.parse_event_page("<html>" + _ld("Fête", "Foix", d(10), d(11)) + "</html>")
            ["occurrences"] == [(today + timedelta(days=10), today + timedelta(days=11))])
    cal = ('<a href="https://www.google.com/calendar/render?action=TEMPLATE&amp;text='
           'Nuit+aux+chandelles&amp;dates=%sT140000/%sT174500">' %
           (d(20).replace("-", ""), d(20).replace("-", "")))
    got = m.parse_event_page(cal)
    s.check("and Ariège Pyrénées Tourisme's calendar link",
            got and got["title"] == "Nuit aux chandelles"
            and got["occurrences"] == [(today + timedelta(days=20),) * 2], detail=str(got))
    s.check("a page with no event on it is nothing", m.parse_event_page("<html></html>") is None)

    s.section("The sites, as the reader will meet them")
    race_url = OT + f"snow-{TAG.lower()}-ax-les-thermes-fr-9000001/"
    yoga_url = OT + "hatha-yoga-beille-les-cabannes-fr-9000002/"
    cine_url = OT + "cinema-seances-tarascon-sur-ariege-fr-9000003/"
    fest_url = OCC + f"festival-{TAG.lower()}-foix_TFOSITRA2_EVE_9000004/"
    far_url = OCC + "fete-de-la-mer-sete_TFOSITRA2_EVE_9000005/"
    prades_url = OCC + "visite-guidee-de-la-ville-de-prades-prades_TFOSITRA2_EVE_9000006/"
    past_url = OT + f"over-{TAG.lower()}-auzat-fr-9000007/"
    dup_url = OCC + f"snow-{TAG.lower()}-ax-les-thermes_TFOSITRA2_EVE_9000008/"
    listing = '<a href="%s">x</a><a href="%s">y</a><a href="%s">z</a><a href="%s">w</a>' % (
        race_url, yoga_url, cine_url, past_url)
    sitemap = "".join(f"<url><loc>{u}</loc></url>" for u in (fest_url, far_url, prades_url,
                                                              dup_url))
    yoga_dates = [(d(n), d(n)) for n in range(3, 70, 7)]
    pages = {
        m.WHATS_ON_OT_AGENDA + "?listpage=1": listing,
        m.WHATS_ON_OCC_SITEMAPS[0]: sitemap, m.WHATS_ON_OCC_SITEMAPS[1]: "",
        race_url: ot_page("Snow " + TAG, "Ax-les-Thermes", [(d(60), d(66))]),
        yoga_url: ot_page("Hatha Yoga à Beille", "Les Cabannes", yoga_dates),
        cine_url: ot_page("Cinéma séances", "Tarascon-sur-Ariège", [(d(4), d(4))]),
        past_url: ot_page("Over " + TAG, "Auzat", [(d(-40), d(-38))]),
        fest_url: "<html>" + _ld(f"Festival {TAG}", "FOIX", d(30), d(32)) + "</html>",
        far_url: "<html>" + _ld("Fête de la mer", "Sète", d(30)) + "</html>",
        prades_url: "<html>" + _ld("Visite guidée", "Prades", d(30)) + "</html>",
        # The region listing the tourist office's race again, under its own ref.
        dup_url: "<html>" + _ld(f"Snow {TAG} 2027", "AX-LES-THERMES", d(60), d(66)) + "</html>",
    }

    # The page's own entries: one with an official page that has moved its
    # dates, one annual whose page shows only last year, one annual with no
    # page at all, one whose page cannot be read, and one never confirmed.
    moved = _add(conn, "race", event_date=d(50), when_words="Spring", source_url=race_url)
    over_page = _add(conn, "over", event_date=d(-40), event_until=d(-38),
                     source_url=past_url, recurs_yearly=1)
    over_none = _add(conn, "fair", event_date=d(-10), recurs_yearly=1)
    broken = _add(conn, "broken", event_date=d(70), source_url=OT + "gone-fr-9000099/")
    unsure = _add(conn, "unsure", event_date=d(40))
    pages[OT + "gone-fr-9000099/"] = TimeoutError("timed out")

    sites = Sites(pages)
    asked_classify = []

    def classify(rows):
        asked_classify.extend(r["title"] for r in rows)
        return {r["id"]: (True, "The " + TAG + " festival in Foix", "A summer festival")
                for r in rows if r["title"].startswith("Festival")}

    said = m.run_whats_on_reader_job(conn, fetch=sites, today=today, classify=classify, pause=0)
    s.check("it runs and says what it did", "confirmed" in said and "listed" in said,
            detail=said)

    s.section("It confirms dates from the official page")
    r = _row(conn, moved)
    s.check("an entry takes its official page's dates",
            r["event_date"] == d(60) and r["event_until"] == d(66), detail=str(dict(r))[:200])
    s.check("is marked confirmed, today", r["dates_status"] == "confirmed"
            and m.house_date_iso(r["dates_checked_at"]) == today.isoformat())
    s.check("and loses the words it was filed under", r["when_words"] is None)

    s.section("An annual entry that is over comes back as next year's, in words")
    r = _row(conn, over_page)
    last_year = today + timedelta(days=-40)
    s.check("its official page has nothing to come, so it is rolled on a year",
            r["event_date"] and m.parse_date(r["event_date"]).year == last_year.year + 1,
            detail=str(r["event_date"]))
    filed = m.parse_date(r["event_date"])
    s.check("printed as the month and 'date to come', never a day",
            filed is not None and r["when_words"] ==
            f"{filed.strftime('%B')} {filed.year}, date to come",
            detail=str(r["when_words"]))
    s.check("unconfirmed until a page gives the day", r["dates_status"] == "unconfirmed")
    s.check("and the reason is kept", "no date still to come" in (r["confirm_problem"] or ""))
    r = _row(conn, over_none)
    s.check("one with no official page rolls on the same way",
            r["when_words"] and r["when_words"].endswith("date to come")
            and r["dates_status"] == "unconfirmed")

    s.section("What it cannot read, it says, and leaves alone")
    r = _row(conn, broken)
    s.check("an unreadable page keeps its dates", r["event_date"] == d(70))
    s.check("and says why", "could not be read" in (r["confirm_problem"] or ""),
            detail=str(r["confirm_problem"]))

    s.section("It finds what the sites list, and sorts it")
    found = {f["title"]: f for f in conn.execute("SELECT * FROM whats_on_found")}
    s.check("the tourist office's events are kept", "Snow " + TAG in found)
    s.check("the region's, in towns within reach", f"Festival {TAG}" in found)
    s.check("not a town an hour's drive is not", "Fête de la mer" not in found)
    s.check("nor the Prades over in the Pyrénées-Orientales", "Visite guidée" not in found)
    s.check("nor an event that is over", "Over " + TAG not in found)
    s.check("a weekly class is a regular activity",
            found.get("Hatha Yoga à Beille", {"kind": None})["kind"] == "regular")
    s.check("a film showing is a local listing",
            found.get("Cinéma séances", {"kind": None})["kind"] == "local")
    s.check("a festival is an event", found.get(f"Festival {TAG}", {"kind": None})["kind"] == "event")
    s.check("one event the region lists again is kept once",
            f"Snow {TAG} 2027" not in found and "Snow " + TAG in found)
    s.check("and one already on the page, from the same official page, is not suggested",
            found["Snow " + TAG]["status"] == "added"
            and found["Snow " + TAG]["whats_on_id"] == moved)
    s.check("only events are put to the assistant",
            asked_classify and all(not t.startswith(("Hatha", "Cinéma")) for t in asked_classify),
            detail=str(asked_classify))
    fest = found.get(f"Festival {TAG}")
    s.check("which gives it an English title, kept as a suggestion",
            fest and fest["english_title"] == f"The {TAG} festival in Foix" and fest["worth"] == 1)

    s.section("Nothing found is on the public page until the owner adds it")
    anon = m.app.test_client()
    page_now = visible_text(anon.get("/whats-on").get_data(as_text=True))
    s.check("the festival is not on it yet", f"{TAG} festival" not in page_now
            and f"Festival {TAG}" not in page_now)
    admin = oc.get("/admin/whats-on")
    html = admin.get_data(as_text=True)
    s.check("the admin page lists it to look at", admin.status_code == 200
            and f"The {TAG} festival in Foix" in html, admin)
    s.check("and says how many dates nothing has confirmed",
            "not confirmed" in visible_text(html))

    s.section("The weekly look")
    m.generate_watch_tasks(conn, today)
    task = conn.execute("SELECT * FROM tasks WHERE origin = 'watch' AND status != 'done' "
                        "AND title = 'Look over What''s On'").fetchone()
    s.check("a task asks for it", task is not None)
    s.check("due on a Monday", task and m.parse_date(task["due_date"]).weekday() == 0
            and m.parse_date(task["due_date"]) >= today)
    s.check("saying what is waiting", task and "found on the official sites" in task["notes"]
            and "could not be checked" in task["notes"], detail=(task["notes"] if task else ""))

    s.section("Add, wave away, mark checked")
    r_ = oc.post(f"/admin/whats-on/found/{fest['id']}/add", data={"yearly": "1"},
                 follow_redirects=True)
    added = conn.execute("SELECT * FROM whats_on WHERE title = ?",
                         (f"The {TAG} festival in Foix",)).fetchone()
    s.check("one press puts it on the page, under its English title", added is not None, r_,
            detail=str(flashes(r_)))
    s.check("with its official page, its dates, confirmed and yearly",
            added and added["source_url"] == fest_url and added["event_date"] == d(30)
            and added["event_until"] == d(32) and added["dates_status"] == "confirmed"
            and added["recurs_yearly"] == 1)
    page_now = visible_text(anon.get("/whats-on").get_data(as_text=True))
    s.check("and the public page shows it", f"The {TAG} festival in Foix" in page_now)
    s.check("a second press adds nothing twice",
            oc.post(f"/admin/whats-on/found/{fest['id']}/add").status_code in (302, 303)
            and conn.execute("SELECT COUNT(*) AS n FROM whats_on WHERE title = ?",
                             (f"The {TAG} festival in Foix",)).fetchone()["n"] == 1)
    race = found["Snow " + TAG]
    oc.post(f"/admin/whats-on/found/{race['id']}/dismiss", follow_redirects=True)
    s.check("waved away, it is no longer to look at",
            all(f["id"] != race["id"] for f in m.whats_on_to_look_at(conn, today)["new"]))
    oc.post(f"/admin/whats-on/found/{race['id']}/dismiss", data={"bring_back": "1"},
            follow_redirects=True)
    s.check("and it can be brought back",
            any(f["id"] == race["id"] for f in m.whats_on_to_look_at(conn, today)["new"]))
    oc.post(f"/admin/whats-on/found/{race['id']}/dismiss", follow_redirects=True)
    oc.post(f"/admin/whats-on/{unsure}/checked", follow_redirects=True)
    s.check("an unconfirmed date the owner has checked is no longer unconfirmed",
            _row(conn, unsure)["dates_status"] == "checked")

    s.section("A second night")
    del pages[race_url]
    pages[m.WHATS_ON_OT_AGENDA + "?listpage=1"] = listing.replace(race_url, "#")
    m.run_whats_on_reader_job(conn, fetch=Sites(pages), today=today, classify=classify, pause=0)
    gone = conn.execute("SELECT gone_at FROM whats_on_found WHERE title = ?",
                        ("Snow " + TAG,)).fetchone()
    s.check("something the listing no longer names is marked gone",
            gone and gone["gone_at"] is not None)
    s.check("and the confirmed entry whose page has gone says so, keeping its dates",
            "no longer" in (_row(conn, moved)["confirm_problem"] or "")
            or "could not be read" in (_row(conn, moved)["confirm_problem"] or ""),
            detail=str(_row(conn, moved)["confirm_problem"]))
    s.check("its dates are untouched", _row(conn, moved)["event_date"] == d(60))
    half = dict(pages)
    half[yoga_url] = TimeoutError("timed out")
    before = conn.execute("SELECT COUNT(*) AS n FROM whats_on_found WHERE gone_at IS NOT NULL"
                          ).fetchone()["n"]
    m.run_whats_on_reader_job(conn, fetch=Sites(half), today=today, classify=classify, pause=0)
    after = conn.execute("SELECT COUNT(*) AS n FROM whats_on_found WHERE gone_at IS NOT NULL"
                         ).fetchone()["n"]
    s.check("a listing that half failed marks nothing gone", after == before,
            detail=f"{before} -> {after}")

    s.section("The task closes itself once there is nothing to look at")
    for row in conn.execute("SELECT id FROM whats_on WHERE is_active = 1 AND "
                            "(dates_status = 'unconfirmed' OR confirm_problem IS NOT NULL)"):
        oc.post(f"/admin/whats-on/{row['id']}/checked")
    for f in conn.execute("SELECT id FROM whats_on_found WHERE status = 'new'"):
        oc.post(f"/admin/whats-on/found/{f['id']}/dismiss")
    m.generate_watch_tasks(conn, today)
    s.check("closed", conn.execute(
        "SELECT 1 FROM tasks WHERE origin = 'watch' AND status != 'done' "
        "AND title = 'Look over What''s On'").fetchone() is None,
            detail=str(m.whats_on_to_look_at(conn, today)))

    s.section("Registered like every other job")
    s.check("its switch is on by default",
            m.AUTOMATION_SETTING_DEFAULTS["automation_whats_on_reader_enabled"] == "1")
    s.check("it runs in the small hours", m.JOB_HOURS.get("whats_on_reader") == (3, 6))
    s.check("and the tests can never reach a real site",
            m.whats_on_fetch.__name__ == "_blocked")


if __name__ == "__main__":
    print(run().report())
