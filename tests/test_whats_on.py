"""What's on locally, for the week a guest is actually here.

A market is a rule — "Sunday mornings, forever" — but a guest staying Thursday
to Monday does not need the rule, they need to know whether it falls while they
are here. So recurring entries are resolved against real dates, and the awkward
parts are the ones worth testing: a season that wraps the new year, a one-off
that has been and gone, and the two shapes a row can take.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "wo-"


def _cleanup(conn):
    conn.execute("DELETE FROM whats_on WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def _add(conn, title, **kw):
    cols = {"title": TAG + title, "description": None, "location": None,
            "distance": None, "weekday": None, "event_date": None,
            "start_time": None, "end_time": None, "season_from": None,
            "season_to": None, "is_active": 1, "sort_order": 0}
    cols.update(kw)
    conn.execute(
        f"""INSERT INTO whats_on ({','.join(cols)}, created_at)
            VALUES ({','.join('?' * len(cols))}, ?)""",
        tuple(cols.values()) + (datetime.now(timezone.utc).isoformat(),))
    conn.commit()


def run():
    s = Suite("What's on")
    oc, ec, _owner, _emp = clients()
    anon = m.app.test_client()          # nobody logged in
    conn = db()
    _cleanup(conn)
    today = datetime.now(timezone.utc).astimezone(m.LOCAL_TZ).date()

    s.section("The pages are public")
    for path in ("/facilities", "/whats-on"):
        r = anon.get(path)
        s.check(f"{path} opens without logging in", r.status_code == 200,
                detail=str(r.status_code))

    s.section("The markets are seeded, not invented")
    seeded = conn.execute(
        "SELECT title, weekday FROM whats_on WHERE title LIKE '%Market%'").fetchall()
    s.check("four markets are there", len(seeded) >= 4, detail=str(len(seeded)))
    s.check("each one repeats on a weekday rather than a single date",
            all(r["weekday"] is not None for r in seeded))
    # The apostrophe in "Producers'" arrived corrupted in the handover SQL.
    titles = [r["title"] for r in seeded]
    s.check("the producers' market reads as English",
            any("Producers' Market" in t for t in titles), detail=str(titles))
    for town in ("Foix", "Mirepoix", "Saint-Girons"):
        s.check(f"the {town} market is there",
                any(town in t for t in titles), detail=str(titles))

    s.section("And the one that is not a market keeps no market day")
    # The whole reason the seed data stopped being positional tuples. A farm
    # shop has no market day and does not open at eight and shut at one;
    # squeezing it into the old six-field shape meant inventing both, and an
    # invented opening time on a public page sends a guest to a closed gate.
    farm = conn.execute(
        "SELECT * FROM whats_on WHERE title LIKE '%Ferme du Qui%'").fetchone()
    s.check("the farm is listed", bool(farm), detail="the fourth thing guests ask about")
    if farm:
        s.check("with no invented market day", farm["weekday"] is None,
                detail=f"weekday={farm['weekday']} — a guest would drive out on it")
        s.check("and no invented opening hours",
                not farm["start_time"] and not farm["end_time"],
                detail=f"{farm['start_time']}–{farm['end_time']}")
        s.check("and it says to ring first rather than turn up",
                "ring" in (farm["description"] or "").lower(),
                detail=f"{(farm['description'] or '')[:80]!r}")

    s.section("A weekday rule lands on the right day of the week")
    wanted = (today + timedelta(days=3))
    _add(conn, "Thursday thing", weekday=wanted.weekday(),
         start_time="09:00", end_time="12:00", location="The square")
    r = anon.get("/whats-on")
    s.check("it appears in the coming week", (TAG + "Thursday thing").encode() in r.data)
    s.check("with its hours", b"09:00" in r.data and b"12:00" in r.data)

    # Something on today must say Today, not the weekday name — that is the
    # whole point of resolving against real dates.
    _add(conn, "Today thing", weekday=today.weekday(), start_time="08:00")
    r = anon.get("/whats-on")
    s.check("and something on today is labelled Today", b"Today" in r.data)

    s.section("A season that wraps the new year")
    # December to March is an ordinary season here, and read as "between" it
    # would be empty all winter.
    winter = {"season_from": "12-01", "season_to": "03-31"}
    row = dict(weekday=0, **winter)
    for on, expected in [("2026-01-15", True), ("2026-12-15", True),
                         ("2026-07-15", False), ("2026-03-31", True),
                         ("2026-04-01", False)]:
        d = m.parse_date(on)
        got = m.whats_on_in_season(row, d)
        s.check(f"{on} is {'in' if expected else 'out of'} a Dec–Mar season",
                got == expected, detail=f"got {got}")

    summer = {"season_from": "06-01", "season_to": "09-30"}
    s.check("and an ordinary summer season still reads as between",
            m.whats_on_in_season(dict(weekday=0, **summer), m.parse_date("2026-07-15"))
            and not m.whats_on_in_season(dict(weekday=0, **summer),
                                         m.parse_date("2026-01-15")))
    s.check("no season at all means always on",
            m.whats_on_in_season({"season_from": None, "season_to": None}, today))

    # A seasonal entry out of season must not be listed at all.
    _add(conn, "Out of season", weekday=today.weekday(),
         season_from="01-01", season_to="01-02")
    r = anon.get("/whats-on")
    out_of_season = (TAG + "Out of season").encode() in r.data
    in_window = today.strftime("%m-%d") in ("01-01", "01-02")
    s.check("something out of season is left off", out_of_season == in_window,
            detail=f"listed={out_of_season}, today={today}")

    s.section("One-off dates")
    _add(conn, "Yesterday concert", event_date=(today - timedelta(days=1)).isoformat())
    _add(conn, "Soon concert", event_date=(today + timedelta(days=2)).isoformat())
    _add(conn, "Later concert", event_date=(today + timedelta(days=40)).isoformat())
    r = anon.get("/whats-on")
    s.check("one that has been and gone is not listed",
            (TAG + "Yesterday concert").encode() not in r.data)
    s.check("one this week is listed", (TAG + "Soon concert").encode() in r.data)
    s.check("and one next month is too, further down",
            (TAG + "Later concert").encode() in r.data)
    # Order matters: this week before later, or the page reads backwards.
    s.check("this week comes before later on the page",
            r.data.index((TAG + "Soon concert").encode())
            < r.data.index((TAG + "Later concert").encode()))

    _add(conn, "Junk date", event_date="not a date")
    r = anon.get("/whats-on")
    s.check("the page still opens with a bad date in the table",
            r.status_code == 200, detail=str(r.status_code))

    s.section("A French guest reads French")
    # The day name is built in Python, the Today flag in the template. Miss one
    # and the page reads "Aujourd'hui" on one row and "Sunday" on the next.
    fr = m.app.test_client()
    with fr.session_transaction() as sess:
        sess["lang"] = "fr"
    r = fr.get("/whats-on")
    s.check("the page opens in French", r.status_code == 200, detail=str(r.status_code))
    body = r.data.decode("utf-8", "replace")
    # Jinja escapes the apostrophe, so compare against what actually ships.
    s.check("the heading is translated",
            "l&#39;affiche" in body or "l'affiche" in body)
    s.check("and the day names with it, not just the Today flag",
            any(d in body for d in ("Lundi", "Mardi", "Mercredi", "Jeudi",
                                    "Vendredi", "Samedi", "Dimanche",
                                    "Aujourd'hui")),
            detail="no French weekday found")
    s.check("no English weekday is left behind beside them",
            not any(f">{d}<" in body for d in
                    ("Monday", "Tuesday", "Wednesday", "Thursday",
                     "Friday", "Saturday", "Sunday")))

    s.section("The standing week and the agenda agree")
    # The page used to carry three markets in hardcoded HTML as well as in the
    # table, so Les Cabannes appeared twice — once translated, once not — and
    # editing it on the admin page changed only one of them.
    r = anon.get("/whats-on")
    body = r.data.decode("utf-8", "replace")
    s.check("the standing week lists the seeded markets",
            "Les Cabannes" in body and "Ax-les-Thermes" in body)
    s.check("and Les Cabannes is not printed twice from two sources",
            body.count("Les Cabannes Market") <= 2, detail=str(body.count("Les Cabannes Market")))
    # Renaming it on the admin page must move both.
    cab = conn.execute("SELECT id FROM whats_on WHERE title LIKE 'Les Cabannes%'").fetchone()
    conn.execute("UPDATE whats_on SET title = ? WHERE id = ?",
                 (TAG + "Renamed market", cab["id"]))
    conn.commit()
    body = anon.get("/whats-on").data.decode("utf-8", "replace")
    s.check("renaming it leaves no copy of the old name behind",
            "Les Cabannes Market" not in body)
    conn.execute("UPDATE whats_on SET title = 'Les Cabannes Market' WHERE id = ?",
                 (cab["id"],))
    conn.commit()

    s.section("Switched off means off")
    _add(conn, "Hidden thing", weekday=today.weekday(), is_active=0)
    r = anon.get("/whats-on")
    s.check("an inactive entry is not shown",
            (TAG + "Hidden thing").encode() not in r.data)

    s.section("Only the owner can edit what guests read")
    r = ec.get("/admin/whats-on")
    s.check("an employee is refused the admin page", r.status_code in (302, 403),
            detail=str(r.status_code))
    r = ec.post("/admin/whats-on/save", data={"title": TAG + "Sneaky", "weekday": "1"},
                follow_redirects=False)
    s.check("and cannot save one", r.status_code in (302, 403),
            detail=str(r.status_code))
    s.check("nothing was written",
            not conn.execute("SELECT 1 FROM whats_on WHERE title = ?",
                             (TAG + "Sneaky",)).fetchone())

    s.section("Adding and editing")
    r = oc.get("/admin/whats-on")
    s.check("the owner gets the page", r.status_code == 200, detail=str(r.status_code))
    oc.post("/admin/whats-on/save",
            data={"title": TAG + "New market", "weekday": "3", "location": "Somewhere",
                  "distance": "10 minutes", "start_time": "08:00", "end_time": "12:00",
                  "is_active": "on", "sort_order": "9"}, follow_redirects=True)
    row = conn.execute("SELECT * FROM whats_on WHERE title = ?",
                       (TAG + "New market",)).fetchone()
    s.check("it is saved", bool(row))
    s.check("with its weekday", row and row["weekday"] == 3, detail=str(row["weekday"] if row else None))
    oc.post("/admin/whats-on/save",
            data={"id": str(row["id"]), "title": TAG + "New market",
                  "weekday": "4", "is_active": "on"}, follow_redirects=True)
    again = conn.execute("SELECT weekday FROM whats_on WHERE id = ?", (row["id"],)).fetchone()
    s.check("and editing changes it rather than adding a second",
            again["weekday"] == 4
            and conn.execute("SELECT COUNT(*) c FROM whats_on WHERE title = ?",
                             (TAG + "New market",)).fetchone()["c"] == 1)

    s.section("An entry has to be one shape or the other")
    # Neither a weekday nor a date can never appear, so it would look saved and
    # silently do nothing.
    oc.post("/admin/whats-on/save",
            data={"title": TAG + "Shapeless", "is_active": "on"}, follow_redirects=True)
    s.check("neither a weekday nor a date is refused",
            not conn.execute("SELECT 1 FROM whats_on WHERE title = ?",
                             (TAG + "Shapeless",)).fetchone())
    # Both would be listed twice in the same week.
    oc.post("/admin/whats-on/save",
            data={"title": TAG + "Both", "weekday": "2",
                  "event_date": (today + timedelta(days=2)).isoformat(),
                  "is_active": "on"}, follow_redirects=True)
    both = conn.execute("SELECT weekday, event_date FROM whats_on WHERE title = ?",
                        (TAG + "Both",)).fetchone()
    s.check("giving both keeps the date and drops the weekday",
            both and both["weekday"] is None and both["event_date"],
            detail=str(tuple(both) if both else None))
    oc.post("/admin/whats-on/save", data={"title": "  ", "weekday": "2"},
            follow_redirects=True)
    s.check("and a blank title does not create a nameless row",
            not conn.execute("SELECT 1 FROM whats_on WHERE TRIM(title) = ''").fetchone())

    s.section("Removing")
    victim = conn.execute("SELECT id FROM whats_on WHERE title = ?",
                          (TAG + "New market",)).fetchone()["id"]
    r = ec.post(f"/admin/whats-on/{victim}/delete", follow_redirects=False)
    s.check("an employee cannot delete one", r.status_code in (302, 403))
    s.check("so it is still there",
            bool(conn.execute("SELECT 1 FROM whats_on WHERE id = ?", (victim,)).fetchone()))
    oc.post(f"/admin/whats-on/{victim}/delete", follow_redirects=True)
    s.check("the owner can", not conn.execute("SELECT 1 FROM whats_on WHERE id = ?",
                                              (victim,)).fetchone())
    r = oc.post("/admin/whats-on/999999/delete", follow_redirects=False)
    s.check("deleting one that never existed is a 404, not a crash",
            r.status_code == 404, detail=str(r.status_code))

    _cleanup(conn)
    conn.close()
    return s
