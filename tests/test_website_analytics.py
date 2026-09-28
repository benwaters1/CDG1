"""Website analytics: who comes to the site, from where, and what they book --
counted by the house, without a cookie, without keeping an internet address,
and without a way to follow anybody from one day to the next.

What there was: nothing. The house could not say how many people looked, what
they read, where they came from, what they looked for and did not find, or
what any of it booked.

What this holds:

  - A visit is counted page by page: its first page carries where it came
    from -- the site that sent them by its name alone, a search engine by one
    name in every country, or a campaign's tags -- and every page after
    carries the same, so a booking is counted against where the visit began,
    and a confirmation seen again is not another booking. A page that was not
    there is written down as such.
  - Never kept: the internet address, the browser's description of itself, a
    guest's key -- a private link is written as its kind, and a key in an
    address that matched nothing is taken out of it.
  - The code for a browser changes every day, and making the new day's salt
    throws the old one away.
  - Not counted: the house's own people, robots, prefetches, a browser that
    asks not to be tracked, anything but a page read -- a POST, a file, a
    redirect -- and a robot trying doors for somebody else's software.
  - The page: visits, pages, one-page visits, bookings and visits that booked,
    where they came from and what that booked, campaigns, pages read, pages
    that were not there, devices, languages. An employee sees none of it.
  - Thirteen months, and the housekeeping job clears it. The notice says so.
"""
from datetime import date, timedelta

from _harness import Suite, db, visible_text, clients
import _harness

import os
import re

m = _harness.m
TAG = "ZZWA"
DAY = "2099-06-15"
NEXT = "2099-06-16"      # the day after: a period's end, which it does not include
PHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         f"(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1 {TAG}")
COMPUTER = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 {TAG}"
ADDRESS = "203.0.113.77"
# A page that is not there, by a name long enough to be taken for a key.
GONE = f"/{TAG.lower()}-the-old-summer-page"
# A key as the app makes them (secrets.token_urlsafe), in an address that matches nothing.
LOST_KEY = "Zq3xT9wLmN4pR7vK2sY8bH5cJ1dF6gA0"


def _cleanup():
    conn = db()
    try:
        conn.execute("DELETE FROM page_views WHERE day >= '2099-01-01'")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'analytics_salt:2099%'")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'analytics_salt:2100%'")
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
        conn.commit()
    finally:
        conn.close()


def _views():
    conn = db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM page_views WHERE day = ? ORDER BY id", (DAY,)).fetchall()]
    finally:
        conn.close()


def run():
    s = Suite("Website visitors")
    _cleanup()
    real_day = m.house_today_iso
    # Every view in here lands on one day nobody else's traffic reaches.
    m.house_today_iso = lambda: DAY
    try:
        _run(s)
    finally:
        m.house_today_iso = real_day
        _cleanup()
    return s


def _run(s):
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    conn.execute("""INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
                    guest_email, arrival_date, departure_date, party_size, status, total_price,
                    created_at) VALUES (?, ?, ?, ?, ?, '2099-07-01', '2099-07-03', 2, 'confirmed',
                    300, ?)""", (room["id"], f"{TAG}S", f"{TAG}secretkey".lower(), f"{TAG} Guest",
                                 f"{TAG.lower()}@example.invalid", _harness.datetime_now()))
    conn.commit()
    conn.close()
    key = f"{TAG}secretkey".lower()
    phone = m.app.test_client()
    here = {"REMOTE_ADDR": ADDRESS}

    def visit(client, path, agent, referrer=None, address=ADDRESS, **headers):
        head = {"User-Agent": agent, "Accept-Language": headers.pop("lang", "fr-FR,fr;q=0.9,en;q=0.8")}
        if referrer:
            head["Referer"] = referrer
        head.update(headers)
        return client.get(path, headers=head, environ_base={"REMOTE_ADDR": address})

    visit(phone, "/", PHONE, referrer="https://www.google.co.uk/search?q=chateau+de+gudanes")
    visit(phone, "/privacy", PHONE, referrer="http://localhost/")
    visit(phone, f"/booking/{key}/statement", PHONE, referrer="http://localhost/privacy")
    visit(phone, GONE, PHONE)
    visit(phone, f"/book/confirmation/{key}", PHONE, referrer="http://localhost/")
    visit(phone, f"/book/confirmation/{key}", PHONE)          # and again: a reload
    other = m.app.test_client()
    visit(other, "/?utm_source=newsletter&utm_medium=email&utm_campaign=autumn", COMPUTER,
          lang="en-GB,en;q=0.9")
    views = _views()

    s.section("A visit, page by page")
    first = views[0] if views else {}
    s.check("its first page is counted, with the site that sent them by its name alone",
            first.get("path") == "/" and first.get("entry") == 1
            and first.get("referrer") == "Google" and first.get("source") == "Google"
            and first.get("device") == "Phone" and first.get("language") == "fr",
            detail=str(first))
    names = {host: m.analytics_source_name(host) for host in (
        "www.google.fr", "google.com.au", "mail.google.com", "com.google.android.gm",
        "l.facebook.com", "lm.facebook.com", "fr.search.yahoo.com", "www.qwant.com",
        "www.tripadvisor.co.uk", "t.co", "chatgpt.com", "chateau-friends.example.org")}
    s.check("a search engine is one name in every country it answers from, and a site the house "
            "has no name for is its own address's host",
            names == {"www.google.fr": "Google", "google.com.au": "Google",
                      "mail.google.com": "Gmail", "com.google.android.gm": "Gmail",
                      "l.facebook.com": "Facebook", "lm.facebook.com": "Facebook",
                      "fr.search.yahoo.com": "Yahoo", "www.qwant.com": "Qwant",
                      "www.tripadvisor.co.uk": "Tripadvisor", "t.co": "X",
                      "chatgpt.com": "ChatGPT",
                      "chateau-friends.example.org": "chateau-friends.example.org"},
            detail=str(names))
    mine = [v for v in views if v["visitor"] == first.get("visitor")]
    s.check("and every page after is the same visit, from the same place",
            len(mine) == 6 and [v["entry"] for v in mine] == [1, 0, 0, 0, 0, 0]
            and {v["source"] for v in mine} == {"Google"} and mine[1]["referrer"] is None,
            detail=str([(v["path"], v["entry"], v["source"], v["referrer"]) for v in mine]))
    s.check("a private link is written as its kind, never its key",
            any(v["path"] == "/booking/…/statement" for v in views)
            and not any(key in str(v) for v in views),
            detail=str([v["path"] for v in views]))
    s.check("a page that was not there is written down as not found, by its own name",
            any(v["path"] == GONE and v["status"] == 404 for v in views),
            detail=str([(v["path"], v["status"]) for v in views]))
    booked = [v for v in views if v["goal"]]
    s.check("a booking's confirmation is a booking, against where the visit began",
            bool(booked) and (booked[0]["goal"], booked[0]["source"]) == ("Stay", "Google"),
            detail=str([(v["goal"], v["source"]) for v in booked]))
    s.check("and seeing it again -- a reload, the back button -- is not another booking",
            len(booked) == 1 and len([v for v in mine if v["endpoint"] == "booking_confirmation"]) == 2,
            detail=str([(v["path"], v["goal"]) for v in mine]))
    campaign = [v for v in views if v["visitor"] != first.get("visitor")]
    s.check("a campaign's tags are its source, medium and campaign",
            len(campaign) == 1 and (campaign[0]["source"], campaign[0]["medium"],
                                    campaign[0]["campaign"]) == ("newsletter", "email", "autumn")
            and campaign[0]["device"] == "Computer" and campaign[0]["language"] == "en",
            detail=str(campaign))
    s.check("the internet address and the browser's description are never kept",
            views and not any(ADDRESS in str(v) or TAG in str(v.get("visitor")) or "Safari" in str(v)
                              for v in views))

    s.section("What is not counted")
    before = len(_views())
    oc.get("/privacy", headers={"User-Agent": COMPUTER}, environ_base=here)
    visit(m.app.test_client(), "/privacy", "Mozilla/5.0 (compatible; Googlebot/2.1)")
    visit(m.app.test_client(), "/privacy", COMPUTER, DNT="1")
    visit(m.app.test_client(), "/privacy", COMPUTER, **{"Sec-GPC": "1"})
    visit(m.app.test_client(), "/privacy", COMPUTER, **{"Sec-Purpose": "prefetch"})
    m.app.test_client().post("/privacy", headers={"User-Agent": COMPUTER}, environ_base=here)
    visit(m.app.test_client(), "/robots.txt", COMPUTER)
    visit(m.app.test_client(), "/static/style.css", COMPUTER)
    visit(m.app.test_client(), "/admin/analytics", COMPUTER)
    after = _views()
    s.check("the house's own people, robots, prefetches, a browser asking not to be tracked, "
            "a POST, a file and a redirect are none of them counted",
            len(after) == before, detail=str([(v["path"], v["status"]) for v in after[before:]]))
    visit(m.app.test_client(), "/wp-login.php", COMPUTER)
    visit(m.app.test_client(), "/.env", COMPUTER)
    visit(m.app.test_client(), "/xmlrpc.php", COMPUTER)
    probed = _views()[len(after):]
    s.check("nor is a robot trying doors for somebody else's software, dressed as a browser",
            not probed, detail=str([(v["path"], v["status"]) for v in probed]))

    s.section("The code changes every day")
    conn = db()
    one = m.analytics_salt(conn, "2099-06-16")
    two = m.analytics_salt(conn, "2099-06-17")
    left = [r["key"] for r in conn.execute(
        "SELECT key FROM app_settings WHERE key LIKE 'analytics_salt:2099%'").fetchall()]
    conn.commit()
    conn.close()
    s.check("a browser's code is different the next day",
            m.analytics_visitor(one, ADDRESS, PHONE) != m.analytics_visitor(two, ADDRESS, PHONE)
            and m.analytics_visitor(one, ADDRESS, PHONE) == m.analytics_visitor(one, ADDRESS, PHONE))
    s.check("and making the new day's salt throws the old one away",
            left == ["analytics_salt:2099-06-17"], detail=str(left))

    s.section("The page")
    conn = db()
    # Somebody the next day, asked about the way a page asks: from a day up
    # to the day after, which is where the next period begins.
    conn.execute("""INSERT INTO page_views (day, at, visitor, path, endpoint, status, source,
                    device, language, entry) VALUES (?, ?, 'zzwa-next-day', '/', 'index', 200,
                    'Direct', 'Computer', 'en', 1)""", (NEXT, f"{NEXT}T09:00:00+00:00"))
    conn.commit()
    summary = m.analytics_summary(conn, DAY, NEXT)
    after_it = m.analytics_summary(conn, NEXT, "2099-06-17")
    conn.close()
    s.check("a day's figures stop where the next day's begin",
            summary["views"] == 7 and after_it["views"] == 1,
            detail=f"the day {summary['views']}, the day after {after_it['views']}")
    s.check("visits, pages, one-page visits, bookings and visits that booked",
            (summary["visits"], summary["views"], summary["bounce"], summary["bookings"],
             summary["converted"]) == (2, 7, 50.0, 1, 50.0),
            detail=str({k: summary[k] for k in ("visits", "views", "bounce", "bookings", "converted")}))
    s.check("where they came from, and what that booked",
            dict(summary["sources"]).get("Google") == {"visits": 1, "bookings": 1}
            and dict(summary["sources"]).get("newsletter") == {"visits": 1, "bookings": 0}
            and summary["goals"] == {"Stay": [("Google", 1)]},
            detail=f"{summary['sources']} / {summary['goals']}")
    s.check("and in what language, by its name",
            dict(summary["languages"]) == {"French": {"visits": 1, "bookings": 1},
                                           "English": {"visits": 1, "bookings": 0}},
            detail=str(summary["languages"]))
    s.check("campaigns, pages read and pages that were not there",
            [c for c, _t in summary["campaigns"]] == ["newsletter / email / autumn"]
            and any(p == "/privacy" for p, _x in summary["pages"])
            and [p for p, _b in summary["broken"]] == [GONE],
            detail=f"{summary['campaigns']} / {summary['broken']}")
    raw = oc.get(f"/admin/analytics?period=day&date={DAY}").get_data(as_text=True)
    page = visible_text(raw)
    s.check("and the page says so, a guest's own link marked as one",
            "Google" in page and "newsletter / email / autumn" in page and GONE in page
            and "/booking/…/statement a guest's own link" in page and "Visits" in page,
            detail=page[:400])
    week = oc.get(f"/admin/analytics?period=week&date={DAY}").get_data(as_text=True)
    # The chart alone: the menu's icons are drawn with rectangles too.
    chart = (week.split('aria-label="Visits a day', 1)[1].split("</svg>", 1)[0]
             if 'aria-label="Visits a day' in week else "")
    bars = chart.count("<rect ")
    empty = chart.count('height="0"') + chart.count('height="0.0"')
    s.check("the week drawn a day at a time, the days nobody came drawn as nobody",
            bars == 7 and empty == 5 and "Busiest: " in visible_text(week),
            detail=f"{bars} bars, {empty} of them empty")
    cells = {seg.split('overview-label">')[1].split("<")[0].strip(): seg
             for seg in week.split('class="overview-cell')[1:] if 'overview-label">' in seg}
    s.check("more people leaving after one page is coloured as the bad news it is",
            "overview-delta-down" in cells.get("Left after one page", "")
            and "overview-delta-up" in cells.get("Visits", ""),
            detail=str({k: ("down" if "overview-delta-down" in v else "up") for k, v in cells.items()}))
    css = open(os.path.join(os.path.dirname(os.path.abspath(m.__file__)), "static", "style.css"),
               encoding="utf-8").read()
    # The rule that does the work, not merely the selector: the phone-width
    # rule beneath it names the same selector for its padding alone.
    s.check("its tables' rows are headed where a cell would be, not centred by the browser",
            'class="data-table named-rows"' in raw
            and re.search(r"\.data-table\.named-rows tbody th\{\s*text-align:left;", css) is not None)
    s.check("an employee sees none of it", ec.get("/admin/analytics").status_code in (302, 403))

    s.section("Thirteen months")
    conn = db()
    later = date.fromisoformat(DAY) + timedelta(days=400)
    result = m.purge_page_views(conn, today=later)
    gone = not conn.execute("SELECT 1 FROM page_views WHERE day = ?", (DAY,)).fetchone()
    salts = conn.execute("SELECT COUNT(*) AS c FROM app_settings WHERE key LIKE 'analytics_salt:2099%'"
                         ).fetchone()["c"]
    conn.close()
    import inspect
    s.check("past thirteen months a view goes, and so does any salt but the day's",
            gone and salts == 0 and result.get("old page views", 0) >= 6, detail=str(result))
    s.check("and the housekeeping job does it",
            "purge_page_views(conn)" in inspect.getsource(m.run_health_notes_purge_job))

    s.section("A key is never kept")
    # A guest's link to their itinerary, mangled on the way -- by an email
    # program, a copy and paste -- into an address that matches nothing.
    visit(m.app.test_client(), f"/book/manage/{LOST_KEY}/itinerary.pdf", COMPUTER,
          address="203.0.113.78")
    lost = _views()
    s.check("a key in an address that was not there is taken out of it, and the page still counted",
            [(v["path"], v["status"]) for v in lost] == [("/book/manage/…/itinerary.pdf", 404)]
            and not any(LOST_KEY[:12] in str(v) for v in lost),
            detail=str([(v["path"], v["status"]) for v in lost]))

    s.section("The notice says so")
    notice = m.app.test_client().get("/privacy").get_data(as_text=True)
    words = visible_text(notice)
    # The entry's own heading, not the phrase: the cookies section names it too.
    s.check("no cookie, no address, never a link's key, a code that changes every day, "
            "not counted if asked, thirteen months",
            "<dt>When you visit the website</dt>" in notice and "no cookie" in words
            and "never the link itself" in words
            and "Your internet address is not kept with any of it" in words
            and "changes every day" in words and "you are not counted at all" in words
            and "Kept for thirteen months" in words)
    # The cookies section said there was no analytics at all. True until this
    # page, and a notice that says it now would be the notice lying.
    s.check("and nowhere does it still say there is no analytics on the site",
            "There is no analytics on this site" not in words
            and "The house counts its own visits" in words)
