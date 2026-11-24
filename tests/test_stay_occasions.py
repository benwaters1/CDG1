"""Occasions at Gudanes: stays sold around a date (owner's brief, 24 November 2026).

"put the prices up 50 euros a night and have something where people see it ...
on the homepage a special events like bastille day, tour de france, new years,
christmas, easter at gudanes - all for stay the night rather than actual
events ... i want to be able to set the price, i want suggested occasions based
on calendar and general news, i want it to be automated the description, where
i can click will chef be onsite yes or no ... tabled like workshops in a grid".

What this holds:
  - THE PRICE is the owner's, per room per occasion, and room_night_rate is the
    one place it is read: compute_room_total charges it inside the nights only,
    a stay half inside pays it on the inside nights, a draft or dismissed
    occasion changes nothing, and the bill names it in rows that add up.
  - A CLASH with a retreat keeps an occasion off sale and says why.
  - SUGGESTIONS: a dismissed one never returns; the news reader drops anything
    without a source link that answers and names the year (Claude stood down).
  - THE CHEF SWITCH: off, nothing on the card or the page offers a meal.
  - THE GRID: the home page and /whats-on/at-gudanes draw the retreats' cards,
    upcoming live occasions only; What's On is a dropdown with both entries.
  - THE WORDS: what the owner types is saved as typed, and Write it again never
    replaces an edited description without being told to.
  - A GUEST CHEF opens /restaurant/book on its dinner nights only, at its price
    and covers, refuses outside diners when the switch says so, shows on the
    house calendar on every night, and closes again when deleted.

No suite reaches a real site or the assistant: the pages and the answers are
handed in here.
"""
import re
from datetime import timedelta

from _harness import (Suite, clients, db, ensure_room, flashes, free_window,
                      keep_rows, visible_text)
import _harness

m = _harness.m
TAG = "ZOCC"


def _occasion(conn, name):
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _form(occ_or_values, rooms=None, **over):
    """An occasion form as the page posts it."""
    f = {"name": occ_or_values["name"], "first_night": occ_or_values["first_night"],
         "last_night": occ_or_values["last_night"], "kind": occ_or_values.get("kind", "stay"),
         "chef_on_site": str(occ_or_values.get("chef_on_site", 0)),
         "status": occ_or_values.get("status", "draft"),
         "description": occ_or_values.get("description", ""), "photo": ""}
    for room_id, price in (rooms or {}).items():
        f[f"price_{room_id}"] = str(price)
    f.update(over)
    return f


def _cleanup(conn):
    conn.execute(f"DELETE FROM stay_occasion_dinners WHERE occasion_id IN "
                 f"(SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM stay_occasion_prices WHERE occasion_id IN "
                 f"(SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM stay_occasions WHERE name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM workshop_sessions WHERE workshop_id IN "
                 f"(SELECT id FROM workshops WHERE title LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM workshops WHERE title LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM restaurant_bookings WHERE guest_name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM bookings WHERE guest_name LIKE '{TAG}%'")
    conn.commit()


def run():
    s = Suite("Occasions at Gudanes")
    oc, _ec, owner, _emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasion_suggestions", "1 = 1"),
                keep_rows(conn, "app_settings", "key LIKE 'stay_occasions_%'"),
                keep_rows(conn, "app_settings", "key IN ('dining_days', 'dining_mode')"),
                keep_rows(conn, "restaurant_settings", "id = 1"),
                keep_rows(conn, "stay_occasions", "status = 'live'")]
    # Only this suite's occasions are on sale for the run.
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE status = 'live'")
    conn.commit()
    _cleanup(conn)
    try:
        _run(s, oc, owner, conn)
    finally:
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _make(oc, conn, name, first, last, prices, **over):
    """Create through the page, then save with whatever `over` says."""
    values = {"name": name, "first_night": first.isoformat(), "last_night": last.isoformat()}
    r = oc.post("/admin/stay-occasions/new", data=_form(values, prices))
    occ = _occasion(conn, name)
    if over and occ:
        r = oc.post(f"/admin/stay-occasions/{occ['id']}",
                    data=_form(dict(values, **{k: v for k, v in over.items()
                                               if k in ("kind", "chef_on_site", "status", "description")}),
                               prices, **{k: v for k, v in over.items()
                                          if k not in ("kind", "chef_on_site", "status", "description")}),
                    follow_redirects=True)
        occ = _occasion(conn, name)
    return occ, r


def _run(s, oc, owner, conn):
    today = m.house_today()
    room = ensure_room()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    usual = room["price_per_night"]
    first = free_window(room["id"], 10, after_days=200, clear_of_ateliers=True,
                        clear_of_rate_overrides=True)
    a = first + timedelta(days=2)              # the occasion's first night
    b = a + timedelta(days=2)                  # and its last: three nights

    s.section("The owner's pages answer")
    page = oc.get("/admin/stay-occasions")
    s.check("the occasions page opens", page.status_code == 200)
    s.check("the new-occasion form opens", oc.get("/admin/stay-occasions/new").status_code == 200)
    first = {r["name"]: r for r in conn.execute(
        "SELECT * FROM stay_occasions WHERE name IN (%s)" % ",".join("?" * len(m.FIRST_OCCASIONS)),
        [row[0] for row in m.FIRST_OCCASIONS])}
    s.check("the owner's first list is there, every one a draft",
            len(first) == len(m.FIRST_OCCASIONS) == 15
            and all(r["status"] in ("draft", "live") for r in first.values()),
            detail=str(sorted(first)))
    s.check("with the chef off on every one",
            all(not r["chef_on_site"] for r in first.values()))
    s.check("and the list's own words, without the chef line (the page adds it)",
            first.get("Tour de France Week") is not None
            and first["Tour de France Week"]["description"].startswith("The Tour comes to the south-west")
            and not any(m.CHEF_OFF_SITE_LINE in (r["description"] or "") for r in first.values()))
    s.check("Bastille Day is 2028, not 2027", first.get("Bastille Day") is not None
            and first["Bastille Day"]["first_night"].startswith("2028-07"))
    s.check("Christmas is the nights of 23 to 25 December, leaving on the 26th",
            first.get("Christmas at Gudanes") is not None
            and (first["Christmas at Gudanes"]["first_night"], first["Christmas at Gudanes"]["last_night"])
            == ("2026-12-23", "2026-12-25"))

    seeded = {r["key"] for r in conn.execute("SELECT key FROM stay_occasion_suggestions")}
    s.check("the two that clash with a retreat are suggestions, not drafts",
            {"first-list:ariegeoise:2027", "first-list:bastille:2027"} <= seeded
            and _occasion(conn, "L'Ariégeoise") is None, detail=str(sorted(seeded)[:6]))

    s.section("A room closed on the nights is not sold for them")
    now0 = m.datetime.now(m.timezone.utc).isoformat()
    shut = conn.execute("SELECT id FROM rooms WHERE active = 1 AND id != ? ORDER BY id LIMIT 1",
                        (room["id"],)).fetchone()["id"]
    span_a = first_free = free_window(room["id"], 4, after_days=600, clear_of_ateliers=True)
    block = conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date, reason, created_at) "
                         "VALUES (?, ?, ?, 'ZOCC', ?)",
                         (shut, span_a.isoformat(), (span_a + timedelta(days=3)).isoformat(), now0)).lastrowid
    conn.commit()
    with m.app.test_request_context("/"):
        occ_shut = m.create_stay_occasion(conn, name=f"{TAG} Closed rooms", first_night=span_a.isoformat(),
                                          last_night=(span_a + timedelta(days=1)).isoformat(),
                                          write=lambda o: "")
    priced = {r["room_id"] for r in conn.execute(
        "SELECT room_id FROM stay_occasion_prices WHERE occasion_id = ?", (occ_shut,))}
    s.check("the closed room has no price on the occasion", shut not in priced, detail=str(priced))
    s.check("an open one has", room["id"] in priced, detail=str(priced))
    conn.execute("DELETE FROM room_blocks WHERE id = ?", (block,))
    conn.commit()

    s.section("The price: the owner's, inside the nights only")
    name = f"{TAG} Week"
    occ, r = _make(oc, conn, name, a, b, {room["id"]: usual + 77})
    s.check("created as a draft", occ and occ["status"] == "draft", response=r)
    s.check("with the owner's price stored for the room",
            occ and conn.execute("SELECT price FROM stay_occasion_prices WHERE occasion_id = ? "
                                 "AND room_id = ?", (occ["id"], room["id"])).fetchone()["price"] == usual + 77)
    with m.app.test_request_context("/"):
        draft_total = m.compute_room_total(conn, room, a, b + timedelta(days=1))
    s.check("a draft changes no price", draft_total == round(usual * 3, 2),
            detail=f"{draft_total} vs {usual * 3}")
    r = oc.post(f"/admin/stay-occasions/{occ['id']}",
                data=_form(dict(occ), {room["id"]: usual + 77}, status="live"), follow_redirects=True)
    occ = _occasion(conn, name)
    s.check("it goes on sale", occ["status"] == "live", response=r)
    with m.app.test_request_context("/"):
        inside = m.compute_room_total(conn, room, a, b + timedelta(days=1))
        half = m.compute_room_total(conn, room, a - timedelta(days=2), a + timedelta(days=1))
        outside = m.compute_room_total(conn, room, first - timedelta(days=5), first)
        quote = m.room_quote(conn, room, a - timedelta(days=1), b + timedelta(days=1)) \
            if hasattr(m, "room_quote") else None
    s.check("compute_room_total charges the occasion price inside the nights",
            inside == round((usual + 77) * 3, 2), detail=f"{inside}")
    s.check("a stay half inside pays it on the inside night only",
            half == round(usual * 2 + usual + 77, 2), detail=f"{half}")
    s.check("and outside the nights nothing changes", outside == round(usual * 5, 2), detail=f"{outside}")
    with m.app.test_request_context("/"):
        segs = m.room_night_segments(conn, room, a - timedelta(days=1), b + timedelta(days=1))
        total = m.compute_room_total(conn, room, a - timedelta(days=1), b + timedelta(days=1))
    s.check("the bill names it: three nights at the owner's price",
            any(seg["label"] == f"{name}, 3 nights at €{usual + 77:,.0f}" for seg in segs),
            detail=str(segs))
    s.check("and the rows add up to the total", round(sum(x["amount"] for x in segs), 2) == total,
            detail=f"{segs} vs {total}")
    api = oc.get(f"/api/quote?room_id={room['id']}&arrival={(a - timedelta(days=1)).isoformat()}"
                 f"&departure={(b + timedelta(days=1)).isoformat()}").get_json() or {}
    labels = [l.get("label") for l in api.get("lines", [])]
    s.check("the booking page's quote names the occasion's nights",
            any(name in (l or "") and "3 nights at" in l for l in labels), detail=str(labels))
    s.check("and carries its chef line", any(o.get("chef_line") == m.CHEF_OFF_SITE_LINE
                                             for o in api.get("occasions", [])), detail=str(api.get("occasions")))
    conn.execute("UPDATE stay_occasions SET status = 'dismissed' WHERE id = ?", (occ["id"],))
    conn.commit()
    with m.app.test_request_context("/"):
        dismissed_total = m.compute_room_total(conn, room, a, b + timedelta(days=1))
    s.check("a dismissed occasion changes nothing", dismissed_total == round(usual * 3, 2),
            detail=f"{dismissed_total}")
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE id = ?", (occ["id"],))
    conn.commit()

    s.section("The quick fill: the usual rate plus €50 on every room")
    r = oc.post(f"/admin/stay-occasions/{occ['id']}",
                data=_form(dict(occ), {}, fill="1", fill_uplift="50", status="live"))
    s.check("the form comes back with every room at its usual rate plus 50",
            f'name="price_{room["id"]}" value="{int(usual + 50)}"' in r.get_data(as_text=True),
            response=r)

    s.section("The chef switch")
    html = oc.get("/whats-on/at-gudanes").get_data(as_text=True)
    card = re.search(r'<article class="g-sit g-occasion">.*?</article>', html, re.S)
    card_text = visible_text(card.group(0)) if card else ""
    s.check("the card says Room and breakfast", "Room and breakfast" in card_text, detail=card_text[:200])
    s.check("and never lunch or dinner", not re.search(r"lunch|dinner", card_text, re.I), detail=card_text)
    page = oc.get(f"/whats-on/at-gudanes/{occ['slug']}").get_data(as_text=True)
    body = re.search(r'class="g-wrap g-occasion-page">(.*?)</div></section>', page, re.S)
    body_text = visible_text(body.group(1)) if body else ""
    s.check("the page carries the line word for word", m.CHEF_OFF_SITE_LINE in body_text,
            detail=body_text[:300])
    s.check("and says nothing of lunch or dinner at the château",
            not re.search(r"lunch|dinner", body_text, re.I), detail=body_text)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}",
                data=_form(dict(occ), {room["id"]: usual + 77}, status="live",
                           description="Come for the week. Dinner is served on the terrace."),
                follow_redirects=True)
    s.check("a description offering dinner is refused while the chef is away",
            "Dinner is served" not in (_occasion(conn, name)["description"] or "")
            and any("chef is not in the house" in f for f in flashes(r)), response=r)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}",
                data=_form(dict(occ), {room["id"]: usual + 77}, status="live", chef_on_site="1"),
                follow_redirects=True)
    page = oc.get(f"/whats-on/at-gudanes/{_occasion(conn, name)['slug']}").get_data(as_text=True)
    s.check("switched on, the page says the chef is in the house", m.CHEF_ON_SITE_LINE in visible_text(page))
    oc.post(f"/admin/stay-occasions/{occ['id']}",
            data=_form(dict(occ), {room["id"]: usual + 77}, status="live", chef_on_site="0"))

    s.section("The words are the owner's to keep")
    occ = _occasion(conn, name)
    mine = "A week in the mountains, kept for the occasion. Breakfast on the terrace each morning."
    oc.post(f"/admin/stay-occasions/{occ['id']}",
            data=_form(dict(occ), {room["id"]: usual + 77}, status="live", description=mine))
    occ = _occasion(conn, name)
    s.check("what the owner typed is saved as typed", occ["description"] == mine,
            detail=occ["description"])
    s.check("and marked as theirs", occ["description_edited"] == 1)
    r = oc.post(f"/admin/stay-occasions/{occ['id']}/write", data={"field": "description"},
                follow_redirects=True)
    s.check("Write it again asks first, and leaves the words alone",
            _occasion(conn, name)["description"] == mine
            and "Replace my words" in r.get_data(as_text=True), response=r)
    oc.post(f"/admin/stay-occasions/{occ['id']}/write", data={"field": "description", "confirm": "1"})
    occ = _occasion(conn, name)
    s.check("told to, it writes them again", occ["description"] != mine and occ["description_edited"] == 0,
            detail=occ["description"])
    s.check("in the house voice: no exclamation marks, no contractions",
            "!" not in occ["description"] and not m._CONTRACTION.search(occ["description"]))

    s.section("A clash with a retreat keeps it off sale, and says why")
    now = m.datetime.now(m.timezone.utc).isoformat()
    wid = conn.execute("INSERT INTO workshops (title, description, price_per_person, created_at) "
                       "VALUES (?, '', 1000, ?)", (f"{TAG} Starry Nights", now)).lastrowid
    c_a = b + timedelta(days=20)
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
                 "VALUES (?, ?, ?, 10, ?)", (wid, c_a.isoformat(), (c_a + timedelta(days=7)).isoformat(), now))
    conn.commit()
    with m.app.test_request_context("/"):
        clashes = m.stay_occasion_clashes(conn, (c_a + timedelta(days=4)).isoformat(),
                                          (c_a + timedelta(days=5)).isoformat())
        ends_free = m.stay_occasion_clashes(conn, (c_a + timedelta(days=7)).isoformat(),
                                            (c_a + timedelta(days=9)).isoformat())
    s.check("a night inside the retreat clashes, naming it and its dates",
            any(c["blocks"] and c["why"].startswith(f"{TAG} Starry Nights runs") for c in clashes),
            detail=str(clashes))
    s.check("the night the retreat ends on is free (it holds to the night before)",
            not m.stay_occasion_blocked(ends_free), detail=str(ends_free))
    cl_name = f"{TAG} Clash"
    cl, r = _make(oc, conn, cl_name, c_a + timedelta(days=4), c_a + timedelta(days=5),
                  {room["id"]: usual + 50}, status="live")
    s.check("it cannot go on sale", cl and cl["status"] == "draft", response=r)
    s.check("and the owner is told why",
            any(f"{TAG} Starry Nights runs" in f for f in flashes(r)), response=r)
    sug = conn.execute(
        "INSERT INTO stay_occasion_suggestions (key, name, first_night, last_night, why, source, "
        "status, found_at) VALUES (?, ?, ?, ?, 'test', 'calendar', 'new', ?)",
        (f"test:{TAG}:clash", f"{TAG} Suggested clash", (c_a + timedelta(days=1)).isoformat(),
         (c_a + timedelta(days=2)).isoformat(), now)).lastrowid
    conn.commit()
    page = oc.get("/admin/stay-occasions").get_data(as_text=True)
    row = re.search(rf'<tr data-clash="1"[^>]*>(?:(?!</tr>).)*{TAG} Suggested clash.*?</tr>', page, re.S)
    s.check("a clashing suggestion is greyed out with the reason",
            row and f"{TAG} Starry Nights runs" in row.group(0) and "Cannot go on sale" in row.group(0),
            detail=(row.group(0)[:300] if row else "no greyed row"))
    r = oc.post(f"/admin/stay-occasions/suggestion/{sug}/create", follow_redirects=True)
    s.check("and Create is refused for it", _occasion(conn, f"{TAG} Suggested clash") is None, response=r)

    s.section("Suggestions: from the calendar, and a dismissed one never returns")
    conn.execute("DELETE FROM stay_occasion_suggestions")
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'stay_occasions_%read_at'")
    conn.commit()
    school = {"results": [{"description": "Vacances de Printemps", "population": "-",
                           "start_date": f"{today.year + 1}-04-02T22:00:00+00:00",
                           "end_date": f"{today.year + 1}-04-18T22:00:00+00:00",
                           "location": "Toulouse", "zones": "Zone C"}]}
    import json as _json
    asked = []

    def fetch(url):
        asked.append(url)
        if url.startswith(m.SCHOOL_HOLIDAYS_URL):
            return _json.dumps(school)
        if url == "https://www.example.org/tour":
            return f"<html>Le Tour {today.year + 1}: étape à Foix</html>"
        if url == "https://www.example.org/old":
            return "<html>Nothing about the year here</html>"
        raise OSError("not here")

    tour_day = today + timedelta(days=240)
    news = [
        {"name": f"{TAG} Tour stage", "first_day": tour_day.isoformat(), "last_day": tour_day.isoformat(),
         "place": "Foix", "why": "A stage finish", "source_url": "https://www.example.org/tour"},
        {"name": f"{TAG} Unsourced festival", "first_day": tour_day.isoformat(),
         "last_day": tour_day.isoformat(), "place": "Toulouse", "why": "", "source_url": ""},
        {"name": f"{TAG} Stale page", "first_day": tour_day.isoformat(),
         "last_day": tour_day.isoformat(), "place": "Andorra", "why": "", "source_url": "https://www.example.org/old"},
    ]
    with m.app.test_request_context("/"):
        said = m.run_stay_occasions_job(conn, today=today, fetch=fetch, ask_news=lambda t: news)
    keys = {r["key"]: r for r in conn.execute("SELECT * FROM stay_occasion_suggestions")}
    s.check("it runs and says what it did", "new suggested" in said, detail=said)
    s.check("Christmas is suggested from the calendar",
            any(k.startswith("calendar:christmas:") for k in keys), detail=str(list(keys)[:5]))
    s.check("so is a long weekend from the public holidays",
            any(k.startswith("calendar:pont-") for k in keys))
    s.check("and the school holidays in Toulouse, read from the ministry's open data",
            any(k.startswith("school:Vacances de Printemps") for k in keys)
            and any(u.startswith(m.SCHOOL_HOLIDAYS_URL) for u in asked))
    s.check("the news suggestion with a source that names the year is kept, with its link",
            any(r["name"] == f"{TAG} Tour stage" and r["source_url"] == "https://www.example.org/tour"
                for r in keys.values()))
    s.check("one with no source link is dropped",
            not any(r["name"] == f"{TAG} Unsourced festival" for r in keys.values()))
    s.check("and one whose source does not mention the year",
            not any(r["name"] == f"{TAG} Stale page" for r in keys.values()))
    s.check("the run says what it dropped, and why", "no source" in said, detail=said)
    xmas = next(r for k, r in keys.items() if k.startswith("calendar:christmas:"))
    r = oc.post(f"/admin/stay-occasions/suggestion/{xmas['id']}/dismiss", follow_redirects=True)
    s.check("Dismiss answers", r.status_code == 200 and conn.execute(
        "SELECT status FROM stay_occasion_suggestions WHERE id = ?", (xmas["id"],)).fetchone()["status"]
        == "dismissed", response=r)
    with m.app.test_request_context("/"):
        m.run_stay_occasions_job(conn, today=today, fetch=fetch, ask_news=lambda t: news)
        still = m.stay_occasion_suggestions_open(conn, today)
    s.check("a dismissed suggestion does not return on the next run",
            not any(x["key"] == xmas["key"] for x in still)
            and conn.execute("SELECT COUNT(*) AS n FROM stay_occasion_suggestions WHERE key = ?",
                             (xmas["key"],)).fetchone()["n"] == 1)
    s.check("the news is not asked again within the week",
            conn.execute("SELECT value FROM app_settings WHERE key = 'stay_occasions_news_read_at'")
            .fetchone()["value"] == today.isoformat())
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, today)
    waiting = [w for w in warnings if "suggested occasion" in w["title"]]
    s.check("the owner home says how many are waiting", waiting, detail=str([w["title"] for w in warnings]))
    conn.execute("UPDATE stay_occasion_suggestions SET status = 'dismissed'")
    conn.commit()
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, today)
    s.check("and the line clears itself once they are dealt with",
            not [w for w in warnings if "suggested occasion" in w["title"]])
    tour = next((r for r in keys.values() if r["name"] == f"{TAG} Tour stage"), None)
    conn.execute("UPDATE stay_occasion_suggestions SET status = 'new' WHERE id = ?", (tour["id"],))
    conn.commit()
    r = oc.post(f"/admin/stay-occasions/suggestion/{tour['id']}/create", follow_redirects=True)
    made = _occasion(conn, f"{TAG} Tour stage")
    s.check("Create drafts it at the usual rates plus €50",
            made and made["status"] == "draft" and conn.execute(
                "SELECT price FROM stay_occasion_prices WHERE occasion_id = ? AND room_id = ?",
                (made["id"], room["id"])).fetchone()["price"] >= usual + 50, response=r)
    s.check("with a description written for it", made and len(made["description"] or "") > 40)

    s.section("The grid, on the home page and on its own page")
    past = conn.execute(
        """INSERT INTO stay_occasions (name, slug, first_night, last_night, status, kind, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'live', 'stay', ?, ?)""",
        (f"{TAG} Past", f"{TAG.lower()}-past", (today - timedelta(days=9)).isoformat(),
         (today - timedelta(days=1)).isoformat(), now, now)).lastrowid
    conn.execute("INSERT INTO stay_occasion_prices (occasion_id, room_id, price) VALUES (?, ?, 300)",
                 (past, room["id"]))
    conn.commit()
    home = oc.get("/", headers={"Host": "www.chateaugudanes.com"}).get_data(as_text=True)
    if "g-occasions-home" not in home:
        _harness_public = m.app.test_client()
        home = _harness_public.get("/").get_data(as_text=True)
    ours = oc.get("/whats-on/at-gudanes").get_data(as_text=True)
    for where, html in (("the home page", home), ("/whats-on/at-gudanes", ours)):
        s.check(f"{where} draws the retreats' grid and card",
                'class="g-sittings-grid g-occasions"' in html and 'class="g-sit g-occasion"' in html
                and 'class="g-sit__when"' in html and 'class="g-sit__img"' in html)
        s.check(f"{where} shows the live occasion with its from-price and Check these dates",
                name in html and "Check these dates" in html and f"€{usual + 77:,.0f}" in html)
        s.check(f"{where} leaves out a draft and a past one",
                f"{TAG} Tour stage" not in html and f"{TAG} Past" not in html)
    s.check("Check these dates opens /book with the dates filled in",
            f"arrival={a.isoformat()}" in ours and f"departure={(b + timedelta(days=1)).isoformat()}" in ours)
    conn.execute("UPDATE stay_occasions SET status = 'draft' WHERE name LIKE ?", (f"{TAG}%",))
    conn.commit()
    empty_home = m.app.test_client().get("/").get_data(as_text=True)
    s.check("with nothing on sale, the home page has no occasions section",
            "g-occasions-home" not in empty_home)
    s.check("the past occasion's page is gone", oc.get(f"/whats-on/at-gudanes/{TAG.lower()}-past")
            .status_code in (200, 404) and m.app.test_client().get(
                f"/whats-on/at-gudanes/{TAG.lower()}-past").status_code == 404)
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE name = ?", (name,))
    conn.commit()

    s.section("What's On is a dropdown: In the Ariège and At Gudanes")
    html = m.app.test_client().get("/whats-on").get_data(as_text=True)
    sub = re.search(r'<div class="g-nav__has-sub">\s*<a href="/whats-on".*?</ul>', html, re.S)
    s.check("the top bar's What's On opens a list",
            sub is not None, detail="no dropdown around /whats-on")
    s.check("with In the Ariège and At Gudanes",
            sub and "In the Ariège" in sub.group(0) and 'href="/whats-on/at-gudanes"' in sub.group(0)
            and "At Gudanes" in sub.group(0))
    s.check("the menu panel and the footer carry At Gudanes too",
            html.count('href="/whats-on/at-gudanes"') >= 3, detail=str(html.count('href="/whats-on/at-gudanes"')))

    s.section("A guest chef in residence: the restaurant, opened and closed again")
    conn.execute("UPDATE restaurant_settings SET enabled = 1, capacity = 20, price_per_person = 60 WHERE id = 1")
    g_first = free_window(room["id"], 4, after_days=(b - today).days + 40, clear_of_ateliers=True)
    nights = [g_first + timedelta(days=i) for i in range(3)]
    closed_days = ",".join(str(d) for d in range(7) if d not in {n.weekday() for n in nights})
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_days', ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (closed_days or "0",))
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('dining_mode', 'open') "
                 "ON CONFLICT(key) DO UPDATE SET value = 'open'")
    conn.commit()
    with m.app.test_request_context("/"):
        before = [m.restaurant_closed_evening(conn, n) for n in nights]
    s.check("before the chef, La Table is shut on those evenings", all(before), detail=str(before))
    gname = f"{TAG} Chef week"
    chef, r = _make(oc, conn, gname, nights[0], nights[-1], {room["id"]: usual + 40},
                    kind="guest_chef", status="live", chef_name=f"{TAG} Marie", dinner_time="19:30",
                    dinner_covers="8", dinner_price="95",
                    **{f"dinner_{nights[0].isoformat()}": "1", f"dinner_{nights[1].isoformat()}": "1"})
    s.check("a guest-chef occasion goes on sale", chef and chef["status"] == "live", response=r)
    s.check("with the chef switch set to yes by itself", chef and chef["chef_on_site"] == 1)
    with m.app.test_request_context("/"):
        opened = [m.restaurant_closed_evening(conn, n) for n in nights]
        rate = m.restaurant_night_rate(conn, nights[0].isoformat())
        seats = m.restaurant_remaining_capacity(conn, nights[0].isoformat())
        off_rate = m.restaurant_night_rate(conn, nights[2].isoformat())
    s.check("its dinner nights are open", opened[0] is None and opened[1] is None, detail=str(opened))
    s.check("the night without a dinner stays shut", opened[2] is not None)
    s.check("at its price", rate == 95, detail=str(rate))
    s.check("and its covers", seats == 8, detail=str(seats))
    s.check("the other nights keep the restaurant's own price", off_rate == 60, detail=str(off_rate))
    book = oc.get(f"/restaurant/book?date={nights[0].isoformat()}").get_data(as_text=True)
    s.check("/restaurant/book names the chef and the nights",
            f"{TAG} Marie" in book and "Guest chef in residence" in book)
    form = {"guest_name": f"{TAG} Outsider", "guest_email": "outsider.zocc@example.com",
            "guest_phone": "+33 6 12 34 56 78", "dinner_date": nights[0].isoformat(), "party_size": "2"}
    r = oc.post("/restaurant/book", data=form, follow_redirects=True)
    s.check("an outside diner is refused while the switch says staying guests only",
            any("for guests staying at the château" in f for f in flashes(r))
            and not conn.execute("SELECT 1 FROM restaurant_bookings WHERE guest_name = ?",
                                 (f"{TAG} Outsider",)).fetchone(), response=r)
    conn.execute(
        """INSERT INTO bookings (room_id, guest_name, guest_email, arrival_date, departure_date, status,
               party_size, manage_token, reference_code, created_at)
           VALUES (?, ?, 'stayer.zocc@example.com', ?, ?, 'confirmed', 2, ?, ?, ?)""",
        (room["id"], f"{TAG} Stayer", nights[0].isoformat(), (nights[-1] + timedelta(days=1)).isoformat(),
         "zocc-token-1", "GUD-ZOCC1", now))
    conn.commit()
    with m.app.test_request_context("/"):
        stayer_ok = m.guest_chef_refusal(conn, nights[0], "stayer.zocc@example.com")
    s.check("a guest staying that night is let through", stayer_ok is None, detail=str(stayer_ok))
    r = oc.post("/restaurant/book", data=dict(form, guest_name=f"{TAG} Stayer",
                                              guest_email="stayer.zocc@example.com"),
                follow_redirects=True)
    s.check("and their table is booked, at the chef's price",
            conn.execute("SELECT total_price FROM restaurant_bookings WHERE guest_name = ?",
                         (f"{TAG} Stayer",)).fetchone() is not None, response=r)
    with m.app.test_request_context("/"):
        dinners = m.guest_chef_dinners_for_stay(conn, nights[0].isoformat(),
                                                (nights[-1] + timedelta(days=1)).isoformat())
    s.check("the staying guest's confirmation offers the dinner nights",
            [d["dinner_date"] for d in dinners] == [nights[0].isoformat(), nights[1].isoformat()],
            detail=str(dinners))

    s.section("On the house calendar, every night it covers")
    with m.app.test_request_context("/"):
        user = conn.execute("SELECT * FROM users WHERE id = ?", (owner["id"],)).fetchone()
        month = m.build_calendar(conn, "month", nights[0], viewer=user)
        nxt = m.build_calendar(conn, "month", nights[-1], viewer=user)
    cells = {c["date"]: c for c in month["cells"] + nxt["cells"]}
    on = [any(e.get("kind") == "occasion" and f"{TAG} Marie" in (e.get("title") or "")
              for e in cells.get(n, {"events": []})["events"]) for n in nights]
    s.check("the guest chef is on all three nights", all(on), detail=str(on))
    first_title = next((e["title"] for e in cells[nights[0]]["events"] if e.get("kind") == "occasion"), "")
    s.check("with the covers booked so far", "2 covers booked of 16" in first_title, detail=first_title)

    s.section("Deleted, it closes what it opened")
    r = oc.post(f"/admin/stay-occasions/{chef['id']}/delete", follow_redirects=True)
    with m.app.test_request_context("/"):
        after = [m.restaurant_closed_evening(conn, n) for n in nights[:2]]
        rate_after = m.restaurant_night_rate(conn, nights[0].isoformat())
    s.check("the occasion is gone", _occasion(conn, gname) is None, response=r)
    s.check("its dinner nights are shut again", all(after), detail=str(after))
    s.check("at the restaurant's own price", rate_after == 60, detail=str(rate_after))
    s.check("and the table already booked is named, not cancelled",
            any("not cancelled" in f for f in flashes(r))
            and conn.execute("SELECT status FROM restaurant_bookings WHERE guest_name = ?",
                             (f"{TAG} Stayer",)).fetchone()["status"] in ("pending", "confirmed"),
            response=r)

    s.section("The job is the house's to switch")
    s.check("it is on the automation list", any(j[0] == "stay_occasions" for j in m.AUTOMATION_JOBS))
    s.check("and Occasions at Gudanes can be found from the palette",
            any(p[1] == "admin_stay_occasions" for p in m.PALETTE_PAGES))


if __name__ == "__main__":
    print(run().report())
