"""An occasion on a retreat's nights becomes that retreat's celebration (owner, 25 November 2026).

"add those dates to the itineraries of the retreats, put on celebration for them" -- said of the two
first occasions that could not go on sale because a retreat had the house: Bastille Day 2027 on Seven
Starry Nights, and L'Ariégeoise on Cooking in the Cuisine. Those two are written into _ws_copy.html
(test_retreat_days holds them). This holds the rule going forward, so the next one needs no brief:

  - THE PAGE SAYS SO. An occasion whose nights fall on a retreat says which retreat and which day, and
    takes one line for that evening. A suggestion that only a retreat blocks offers to be celebrated
    there; one that anything else blocks does not, and neither can go on sale.
  - THE LINE IS A LINE: twelve words at most, as every bullet on a retreat's days is.
  - IT SHOWS WHERE IT SAYS, AND ONLY THERE. Written, the retreat's own page draws it on that day in
    place of the evening and names it in the day's heading; on a last night the banquet stays, named
    for it. No other day changes. Emptied, the day is its own again.
  - THE OWNER'S WORD WINS over the template's own occasions (21 June).
  - IT IS CHECKED, NOT ASSUMED. A retreat whose page draws no dated days (one in winter) does not show
    it, and the owner is told so instead of "saved".
  - WHAT THEY TYPE IS TEXT. An ampersand or a tag is drawn as typed, never as markup.
"""
import html as htmllib
import re

from _harness import Suite, clients, db, ensure_room, flashes as _flashes, keep_rows, visible_text as _visible
import _harness
from test_retreat_days import journey

m = _harness.m
TAG = "ZZRC"


def visible_text(page):
    """The page's words, with no space left before a comma where a tag was."""
    return re.sub(r"\s+([,.:;])", r"\1", _visible(page))


def flashes(response):
    """The flash messages as read, entities decoded (an apostrophe arrives as &#39;)."""
    return [htmllib.unescape(f) for f in _flashes(response)]


def _cleanup(conn):
    for table in ("stay_occasion_prices", "stay_occasion_dinners"):
        conn.execute(f"DELETE FROM {table} WHERE occasion_id IN "
                     f"(SELECT id FROM stay_occasions WHERE name LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM stay_occasions WHERE name LIKE '{TAG}%'")
    conn.execute(f"DELETE FROM stay_occasion_suggestions WHERE key LIKE 'test:{TAG}%'")
    conn.execute(f"DELETE FROM workshop_sessions WHERE workshop_id IN "
                 f"(SELECT id FROM workshops WHERE title LIKE '{TAG}%')")
    conn.execute(f"DELETE FROM workshops WHERE title LIKE '{TAG}%'")
    conn.commit()


def _occ(conn, name):
    return conn.execute("SELECT * FROM stay_occasions WHERE name = ?", (name,)).fetchone()


def _retreat(conn, title, start, end):
    now = m.datetime.now(m.timezone.utc).isoformat()
    wid = conn.execute(
        "INSERT INTO workshops (title, description, price_per_person, active, created_at, nights_label) "
        "VALUES (?, '', 2000, 1, ?, '5 nights / 6 days')", (title, now)).lastrowid
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, created_at) "
                 "VALUES (?, ?, ?, 10, ?)", (wid, start, end, now))
    conn.commit()
    return wid


def _occasion(conn, name, first, last):
    with m.app.test_request_context("/"):
        occ_id = m.create_stay_occasion(conn, name=name, first_night=first, last_night=last,
                                        write=lambda o: "")
    conn.commit()
    return occ_id


def _evening(client, occ_id, night, text):
    return client.post(f"/admin/stay-occasions/{occ_id}/retreat",
                       data={"retreat_night": night, "retreat_evening": text}, follow_redirects=True)


def _days(client, wid):
    r = client.get(f"/workshops/{wid}")
    days, _section = journey(r.get_data(as_text=True))
    return r, days


def run():
    s = Suite("An occasion on a retreat's nights is its celebration (owner, 25 November)")
    oc, ec, _owner, emp = clients()
    conn = db()
    put_back = [keep_rows(conn, "stay_occasion_suggestions", "1 = 1")]
    _cleanup(conn)
    made = []                       # occasions this suite made under a real name
    try:
        _run(s, oc, ec, emp, conn, made)
    finally:
        for occ_id in made:
            conn.execute("DELETE FROM stay_occasion_prices WHERE occasion_id = ?", (occ_id,))
            conn.execute("DELETE FROM stay_occasions WHERE id = ?", (occ_id,))
        conn.commit()
        _cleanup(conn)
        for p in reversed(put_back):
            p()
        conn.close()
    return s


def _run(s, oc, ec, emp, conn, made):
    # A summer retreat of five nights, from Friday 3 August 2029: evenings on the 3rd to the 7th, leaving on the
    # 8th. Far enough ahead that no real retreat or occasion is on these nights.
    wid = _retreat(conn, f"{TAG} Five Summer Nights", "2029-08-03", "2029-08-08")
    name = f"{TAG} Fête du Village"
    occ_id = _occasion(conn, name, "2029-08-04", "2029-08-07")

    s.section("The occasion's page says which retreat, and which day")
    r = oc.get(f"/admin/stay-occasions/{occ_id}")
    page = r.get_data(as_text=True)
    panel = re.search(r'<section class="detail-card" id="retreat".*?</section>', page, re.S)
    said = visible_text(panel.group(0)) if panel else ""
    s.check("the page opens, with its On a retreat panel", r.status_code == 200 and bool(panel),
            detail=said[:200], response=r)
    s.check("it names the retreat and the days its nights fall on",
            f"Falls on {TAG} Five Summer Nights, days 2 to 5" in said, detail=said[:200])
    s.check("and offers each of those evenings to choose from",
            len(re.findall(r'<option value="2029-08-0[4-7]"', panel.group(0) if panel else "")) == 4,
            detail=said[:300])
    s.check("the last of them marked as the last night", "day 5 (the last night)" in said, detail=said[:300])
    s.check("and it still cannot go on sale, saying why",
            f"Clash: {TAG} Five Summer Nights runs" in visible_text(page))

    s.section("The line is a line")
    long_line = "A long table on the lawn for the whole village, then fireworks over the valley"
    r = _evening(oc, occ_id, "2029-08-05", long_line)
    s.check(f"{len(long_line.split())} words are refused, with the reason",
            any("Keep it to a line" in f for f in flashes(r))
            and not _occ(conn, name)["retreat_evening"], response=r)
    r = _evening(oc, occ_id, "2029-08-03", "Dinner on the lawn")
    s.check("a night that is not one of the occasion's is refused",
            any("Choose the night" in f for f in flashes(r)) and not _occ(conn, name)["retreat_evening"],
            response=r)
    r = ec.post(f"/admin/stay-occasions/{occ_id}/retreat",
                data={"retreat_night": "2029-08-05", "retreat_evening": "Dinner on the lawn"})
    s.check("and a member of staff cannot write one", not _occ(conn, name)["retreat_evening"],
            detail=str(r.status_code))

    s.section("Written, the retreat's own page shows it on that day, and only there")
    line = "The village fête: a long table outside, then music"
    r = _evening(oc, occ_id, "2029-08-05", line)
    occ = _occ(conn, name)
    s.check("saved, with the night it falls on",
            occ["retreat_evening"] == line and occ["retreat_night"] == "2029-08-05", detail=dict(occ).get("retreat_evening"))
    s.check("and the owner is told what the retreat now shows, not just 'saved'",
            any(f"{TAG} Five Summer Nights, day 3, now celebrates {name}" in f for f in flashes(r)), response=r)
    r, days = _days(oc, wid)
    s.check("the retreat's page draws six days", r.status_code == 200 and len(days) == 6, detail=str(len(days)))
    if len(days) == 6:
        s.check("day 3's evening is the owner's line", days[2]["moments"][-1] == line, detail=str(days[2]["moments"]))
        s.check("and its other bullets are the day's own", len(days[2]["moments"]) >= 2
                and "Foix" in " ".join(days[2]["moments"][:-1]), detail=str(days[2]["moments"]))
        s.check("its heading names the occasion", days[2]["title"].endswith(f"and {name}"), detail=days[2]["title"])
        others = [n for n, d in enumerate(days, 1) if n != 3 and ("Fête du Village" in d["title"] + " ".join(d["moments"]))]
        s.check("no other day changes", not others, detail=str(others))
    page = oc.get(f"/admin/stay-occasions/{occ_id}").get_data(as_text=True)
    s.check("the occasion's page now says it shows",
            "shows on that retreat's days as a celebration" in visible_text(page))
    s.check("and the occasions list carries it under the name",
            f"On the retreat, " in visible_text(oc.get("/admin/stay-occasions").get_data(as_text=True))
            and line in visible_text(oc.get("/admin/stay-occasions").get_data(as_text=True)))

    s.section("On the retreat's last night the banquet stays, named for it")
    r = _evening(oc, occ_id, "2029-08-07", line)
    r, days = _days(oc, wid)
    if len(days) == 6:
        s.check("day 5's evening is the banquet, named for the occasion",
                days[4]["moments"][-1] == f"{name}: the banquet in the Salon de Musique, then dancing",
                detail=str(days[4]["moments"]))
        s.check("and its heading is the last night's own", not days[4]["title"].endswith(name),
                detail=days[4]["title"])
        s.check("day 3 is its own again", line not in " ".join(days[2]["moments"]), detail=str(days[2]["moments"]))
    else:
        s.check("the retreat's page draws six days", False, detail=str(len(days)))

    s.section("What the owner types is drawn as text")
    typed = "Fish & chips <b>by the pool</b>, then a film"
    r = _evening(oc, occ_id, "2029-08-05", typed)
    html = oc.get(f"/workshops/{wid}").get_data(as_text=True)
    s.check("an ampersand and a tag come out as typed, not as markup",
            "Fish &amp; chips &lt;b&gt;by the pool&lt;/b&gt;" in html and "<b>by the pool</b>" not in html,
            detail=html[html.find("Fish"):][:120])
    s.check("and the check that it shows still finds it",
            any("now celebrates" in f for f in flashes(r)), response=r)

    s.section("The owner's word wins over the template's own occasions")
    # Five nights from Monday 18 June 2029: 21 June is day 4, which _ws_copy.html makes the Fête de la Musique.
    wid2 = _retreat(conn, f"{TAG} June Nights", "2029-06-18", "2029-06-23")
    r, days = _days(oc, wid2)
    s.check("without one, 21 June is the Fête de la Musique",
            len(days) == 6 and "Fête de la Musique" in days[3]["moments"][-1],
            detail=str(days[3]["moments"] if len(days) == 6 else len(days)))
    mus = _occasion(conn, f"{TAG} Midsummer", "2029-06-21", "2029-06-21")
    own = "Midsummer: a bonfire in the park, then music"
    _evening(oc, mus, "2029-06-21", own)
    r, days = _days(oc, wid2)
    s.check("with one, it is the owner's line instead",
            len(days) == 6 and days[3]["moments"][-1] == own,
            detail=str(days[3]["moments"] if len(days) == 6 else len(days)))

    s.section("A retreat whose page has no dated days does not show it, and the owner is told")
    wid3 = _retreat(conn, f"{TAG} Winter Nights", "2029-01-05", "2029-01-10")
    win = _occasion(conn, f"{TAG} Epiphany", "2029-01-06", "2029-01-06")
    r = _evening(oc, win, "2029-01-06", "Epiphany: the galette des rois, then a film")
    s.check("saved, but the flash says the page does not show it",
            any("does not show it" in f for f in flashes(r)) and not any("now celebrates" in f for f in flashes(r)),
            response=r)
    page = visible_text(oc.get(f"/admin/stay-occasions/{win}").get_data(as_text=True))
    s.check("and so does the occasion's page", "its page does not show it" in page, detail=page[:300])
    s.check("which is true: the winter retreat draws no days at all",
            _days(oc, wid3)[1] == [] and "galette" not in oc.get(f"/workshops/{wid3}").get_data(as_text=True))

    s.section("Emptied, the evening is the retreat's own again")
    r = _evening(oc, occ_id, "", "")
    occ = _occ(conn, name)
    s.check("the line and the night are cleared", not occ["retreat_evening"] and not occ["retreat_night"],
            response=r)
    s.check("and the owner is told", any("off the retreat" in f for f in flashes(r)), response=r)
    r, days = _days(oc, wid)
    s.check("no day of the retreat names it any more",
            len(days) == 6 and not any("Fête du Village" in d["title"] + " ".join(d["moments"]) for d in days)
            and "Fish" not in r.get_data(as_text=True))
    with m.app.test_request_context("/"):
        held = m.retreat_celebrations(conn)
    s.check("retreat_celebrations holds the two still written, keyed by their nights",
            set(held) >= {"2029-06-21", "2029-01-06"} and "2029-08-05" not in held and "2029-08-07" not in held,
            detail=str(sorted(held)))

    s.section("A suggestion only a retreat blocks can be celebrated there")
    now = m.datetime.now(m.timezone.utc).isoformat()
    sug = conn.execute(
        "INSERT INTO stay_occasion_suggestions (key, name, first_night, last_night, why, source, status, found_at) "
        "VALUES (?, ?, '2029-08-04', '2029-08-05', 'test', 'calendar', 'new', ?)",
        (f"test:{TAG}:retreat", f"{TAG} Suggested on a retreat", now)).lastrowid
    # One that something else blocks too: an occasion already on sale on its night.
    live = _occasion(conn, f"{TAG} Already on sale", "2029-08-06", "2029-08-06")
    conn.execute("UPDATE stay_occasions SET status = 'live' WHERE id = ?", (live,))
    sug2 = conn.execute(
        "INSERT INTO stay_occasion_suggestions (key, name, first_night, last_night, why, source, status, found_at) "
        "VALUES (?, ?, '2029-08-06', '2029-08-06', 'test', 'calendar', 'new', ?)",
        (f"test:{TAG}:other", f"{TAG} Suggested on a sale", now)).lastrowid
    conn.commit()
    page = oc.get("/admin/stay-occasions").get_data(as_text=True)
    row = re.search(rf'<tr data-clash="1"[^>]*>(?:(?!</tr>).)*{TAG} Suggested on a retreat.*?</tr>', page, re.S)
    row2 = re.search(rf'<tr data-clash="1"[^>]*>(?:(?!</tr>).)*{TAG} Suggested on a sale.*?</tr>', page, re.S)
    s.check("greyed, it offers Celebrate on the retreat",
            bool(row) and "Celebrate on the retreat" in row.group(0) and "Cannot go on sale" in row.group(0),
            detail=row.group(0)[:300] if row else "no greyed row")
    s.check("one something else blocks does not",
            bool(row2) and "Celebrate on the retreat" not in row2.group(0),
            detail=row2.group(0)[:300] if row2 else "no greyed row")
    r = oc.post(f"/admin/stay-occasions/suggestion/{sug}/create", follow_redirects=True)
    s.check("Create on its own is still refused", _occ(conn, f"{TAG} Suggested on a retreat") is None, response=r)
    r = oc.post(f"/admin/stay-occasions/suggestion/{sug2}/create", data={"celebrate": "1"}, follow_redirects=True)
    s.check("and celebrating the other is refused too", _occ(conn, f"{TAG} Suggested on a sale") is None,
            response=r)
    r = oc.post(f"/admin/stay-occasions/suggestion/{sug}/create", data={"celebrate": "1"})
    got = _occ(conn, f"{TAG} Suggested on a retreat")
    s.check("celebrating it drafts the occasion and opens its retreat panel",
            got is not None and got["status"] == "draft"
            and r.headers.get("Location", "").endswith(f"/admin/stay-occasions/{got['id'] if got else 0}#retreat"),
            detail=r.headers.get("Location"))
    s.check("and the suggestion is taken up, so it stops waiting on the owner",
            conn.execute("SELECT status FROM stay_occasion_suggestions WHERE id = ?", (sug,)).fetchone()["status"]
            == "created")
    if got:
        room = ensure_room()
        r = oc.post(f"/admin/stay-occasions/{got['id']}", follow_redirects=True, data={
            "name": got["name"], "first_night": got["first_night"], "last_night": got["last_night"],
            "kind": "stay", "chef_on_site": "0", "status": "live", "description": "", "photo": "",
            f"price_{room['id']}": "300"})
        s.check("and it still cannot go on sale",
                _occ(conn, got["name"])["status"] == "draft"
                and any("Five Summer Nights runs" in f for f in flashes(r)), response=r)

    s.section("The owner's own case: L'Ariégeoise on Cooking in the Cuisine")
    ar = conn.execute("SELECT * FROM stay_occasion_suggestions WHERE key = 'first-list:ariegeoise:2027'").fetchone()
    cooking = conn.execute(
        """SELECT workshops.id FROM workshops JOIN workshop_sessions ON workshop_sessions.workshop_id = workshops.id
            WHERE workshops.title = 'Cooking in the Cuisine' AND workshop_sessions.start_date = '2027-06-25'
              AND workshops.active = 1""").fetchone()
    s.check("the seeded suggestion and the retreat are there to check", bool(ar and cooking),
            detail=f"suggestion {bool(ar)}, retreat {bool(cooking)}")
    if not (ar and cooking):
        return
    conn.execute("UPDATE stay_occasion_suggestions SET status = 'new' WHERE id = ?", (ar["id"],))
    conn.commit()
    page = oc.get("/admin/stay-occasions").get_data(as_text=True)
    row = re.search(r'<tr data-clash="1"[^>]*>(?:(?!</tr>).)*L&#39;Ariégeoise.*?</tr>', page, re.S)
    s.check("its greyed row offers Celebrate on the retreat",
            bool(row) and "Celebrate on the retreat" in row.group(0), detail=row.group(0)[:300] if row else "")
    oc.post(f"/admin/stay-occasions/suggestion/{ar['id']}/create", data={"celebrate": "1"})
    occ = conn.execute("SELECT * FROM stay_occasions WHERE name = 'L''Ariégeoise' AND first_night = '2027-06-25'"
                       ).fetchone()
    s.check("it is drafted", occ is not None)
    if not occ:
        return
    made.append(occ["id"])
    said = visible_text(oc.get(f"/admin/stay-occasions/{occ['id']}").get_data(as_text=True))
    s.check("its page says: Falls on Cooking in the Cuisine, days 1 to 2",
            "Falls on Cooking in the Cuisine, days 1 to 2" in said, detail=said[said.find("Falls"):][:120])
    own = "The evening of L'Ariégeoise: a barbecue, then a film under the stars"
    r = _evening(oc, occ["id"], "2027-06-26", own)
    s.check("its evening on the 26th is day 2, and the page says so",
            any("Cooking in the Cuisine, day 2, now celebrates L'Ariégeoise" in f for f in flashes(r)), response=r)
    r, days = _days(oc, cooking["id"])
    s.check("Cooking in the Cuisine's day 2 is the owner's line, ahead of the template's",
            len(days) == 6 and days[1]["moments"][-1] == own and days[1]["title"].endswith("and L’Ariégeoise"),
            detail=str(days[1] if len(days) > 1 else days))


if __name__ == "__main__":
    print(run().report())
