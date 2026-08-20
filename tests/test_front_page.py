"""The front page, which is the one page every visitor sees.

It used to be given no data at all, so it could only be a menu of links. Now it
shows real rooms and the next real sittings — which means it can now fail in
ways a static page could not, and it did: the template reads optional keys with
.get(), a sqlite3.Row has no .get(), and "/" returned a 500 to every visitor.

Nothing tested it. The failure surfaced two suites away, in the translation
tests, because those happened to fetch "/" on their way to something else. This
suite exists so the next one is caught where it happens.
"""
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m


def run():
    s = Suite("The front page")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    conn = db()

    s.section("A visitor gets the château, not a login screen")
    r = anon.get("/")
    s.check("it opens", r.status_code == 200, detail=str(r.status_code))
    body = r.get_data(as_text=True)
    s.check("and it is the public page, not the staff dashboard",
            "g-hero" in body or "g-wrap" in body, detail="public shell missing")
    s.check("with no sign of the staff shell", "Clock in" not in body)

    s.section("Real rooms, not category tiles")
    # The names come out of the rooms table. A tile saying "Rooms" that links
    # away is what this replaced.
    first = conn.execute(
        """SELECT name FROM rooms WHERE active = 1
           ORDER BY sort_order, price_per_night LIMIT 1""").fetchone()
    if first:
        s.check("the cheapest active room is named on it", first["name"] in body,
                detail=f"expected {first['name']!r}")
    else:
        s.check("no active rooms, so nothing to show", "g-homerooms" not in body)
    s.check("at most four of them", body.count("g-homeroom__name") <= 4,
            detail=f"{body.count('g-homeroom__name')} shown")

    s.section("Only dates still ahead")
    # A front page advertising a workshop that finished in June is worse than
    # one advertising none.
    today = m.service_day().isoformat()
    past = conn.execute(
        """SELECT workshops.title FROM workshop_sessions
             JOIN workshops ON workshops.id = workshop_sessions.workshop_id
            WHERE workshop_sessions.start_date < ?
              AND workshops.id NOT IN (
                  SELECT workshop_id FROM workshop_sessions WHERE start_date >= ?)
            LIMIT 1""", (today, today)).fetchone()
    if past:
        s.check("a workshop whose dates have all gone is not advertised",
                past["title"] not in body, detail=f"{past['title']!r} is on the page")
    ahead = conn.execute(
        """SELECT workshops.title, workshop_sessions.start_date FROM workshop_sessions
             JOIN workshops ON workshops.id = workshop_sessions.workshop_id
            WHERE workshops.active = 1 AND workshop_sessions.start_date >= ?
            ORDER BY workshop_sessions.start_date LIMIT 1""", (today,)).fetchone()
    if ahead:
        s.check("the next sitting is on it", ahead["title"] in body,
                detail=f"expected {ahead['title']!r}")
        # Raw ISO on the front page is the symptom of the date filters not
        # being registered, which has happened before.
        s.check("its date is written out, not left as ISO",
                ahead["start_date"] not in body,
                detail=f"{ahead['start_date']} printed raw")
    s.check("at most three sittings", body.count("g-agenda__title") <= 3,
            detail=f"{body.count('g-agenda__title')} shown")

    s.section("An empty house still renders")
    # Both lists are optional in the template. If that ever stops being true,
    # a château with no rooms entered yet gets a broken front page on day one.
    conn.execute("UPDATE rooms SET active = 0")
    conn.execute("UPDATE workshops SET active = 0")
    conn.commit()
    r = anon.get("/")
    s.check("with no rooms and no workshops it still opens",
            r.status_code == 200, detail=str(r.status_code))
    empty = r.get_data(as_text=True)
    s.check("and says nothing about rooms rather than showing an empty frame",
            "g-homeroom__name" not in empty)
    s.check("nor about dates", "g-agenda__title" not in empty)
    conn.execute("UPDATE rooms SET active = 1")
    conn.execute("UPDATE workshops SET active = 1")
    conn.commit()

    s.section("Signed in, the same address is the dashboard")
    r = oc.get("/")
    s.check("an owner gets their own screen", r.status_code == 200,
            detail=str(r.status_code))
    s.check("which is not the visitor's front page",
            "g-homerooms" not in r.get_data(as_text=True))

    conn.close()
    return s
