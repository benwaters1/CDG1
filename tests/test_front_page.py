"""The front page, which is the one page every visitor sees.

It used to be given no data at all, so it could only be a menu of links. Now it
shows real rooms and the next real sittings — which means it can now fail in
ways a static page could not, and it did: the template reads optional keys with
.get(), a sqlite3.Row has no .get(), and "/" returned a 500 to every visitor.

Nothing tested it. The failure surfaced two suites away, in the translation
tests, because those happened to fetch "/" on their way to something else. This
suite exists so the next one is caught where it happens.
"""
import re
from datetime import datetime, timezone, timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m

# The markers this suite counts. Both are asserted present on a populated
# page before they are counted, so renaming them in a design pass fails
# here rather than quietly turning every count into zero.
ROOM_MARK = "g-homeroom__name"
SIT_MARK = "g-sit__name"


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
    # ROOM_MARK and SIT_MARK are checked for PRESENCE first, deliberately.
    # A design pass renamed the sitting cards from g-agenda__* to g-sit__* and
    # the counters below silently went to zero — which passes "at most three"
    # while guarding nothing. Asserting the marker is there first makes a
    # rename fail loudly instead, and the fix is one line here.
    #
    # Links are not enough on their own: the placeholder rooms that keep
    # arriving in handovers carry no id, so they link to /book without one and
    # a link-counting check never sees them.
    s.check("the room cards are still marked the way this test expects",
            ROOM_MARK in body,
            detail=f"{ROOM_MARK!r} not found — markup renamed, update this test")
    # Every active room, not a sample. This used to assert "at most four",
    # which was the LIMIT 4 in dashboard() written down as if it were a
    # decision — it dropped whichever room sat last in an order nobody could
    # set, and that was the Suite with Mountain View, the dearest room in the
    # house. The count is now the count.
    counter = db()
    active = counter.execute("SELECT COUNT(*) AS c FROM rooms WHERE active = 1").fetchone()["c"]
    counter.close()
    s.check("every active room is on it", body.count(ROOM_MARK) == active,
            detail=f"{body.count(ROOM_MARK)} shown, {active} active — the front "
                   "page is advertising the house with a room missing")

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
        #
        # THE PROSE ONLY. public_base carries a machine-readable block of
        # booked dates for the date picker, which is ISO on purpose and is not
        # read by anybody -- so searching the whole body started failing on
        # data doing its job. Stripped rather than the check weakened: a raw
        # date in the copy is still a fault.
        import re as _re
        prose = _re.sub(r'<script[^>]*application/json[^>]*>.*?</script>', "",
                        body, flags=_re.S)
        s.check("its date is written out, not left as ISO",
                ahead["start_date"] not in prose,
                detail=f"{ahead['start_date']} printed raw")
    if ahead:
        s.check("the sitting cards are still marked the way this test expects",
                SIT_MARK in body,
                detail=f"{SIT_MARK!r} not found — markup renamed, update this test")
    s.check("at most three sittings", body.count(SIT_MARK) <= 3,
            detail=f"{body.count(SIT_MARK)} shown")

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
    # Not "no links" — the placeholder rooms link to /book with no id, so a
    # link check passes while four invented rooms sit on the page.
    s.check("and says nothing about rooms rather than showing an empty frame",
            ROOM_MARK not in empty, detail=f"{empty.count(ROOM_MARK)} rooms shown")
    s.check("nor about dates", SIT_MARK not in empty,
            detail=f"{empty.count(SIT_MARK)} sittings shown")
    conn.execute("UPDATE rooms SET active = 1")
    conn.execute("UPDATE workshops SET active = 1")
    conn.commit()

    s.section("Signed in, the same address is the dashboard")
    r = oc.get("/")
    s.check("an owner gets their own screen", r.status_code == 200,
            detail=str(r.status_code))
    s.check("which is not the visitor's front page",
            ROOM_MARK not in r.get_data(as_text=True))

    conn.close()
    return s
