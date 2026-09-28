"""What the road is doing, for the week it is worse than usual.

The last few miles to this house are unlit, there is no street number, and an
address search puts people in the village square. All of that is in the
approach copy already, because it is always true. None of it covers the week
there is ice on the D8.

THE COMPULSORY END DATE IS THE WHOLE DESIGN, and it is the only interesting
decision here. Every notice like this fails the same way: switched on in
February for a real reason, still there in July, because taking it down is
nobody's job and nothing reminds them. A snow warning on the page in summer is
worse than no warning at all -- it is the reason the next real one is not
believed, and the next real one is the night somebody drives up an unlit
mountain road on ice. So it expires by itself, it cannot be created without an
end, and it cannot run longer than three weeks: past that it is a fact about
the road rather than a warning about this week, and facts belong in the
approach copy where they are written once and read calmly.

ONE AT A TIME. Two notices on an arrival page is a wall of warnings, which
reads as a house with a problem rather than a road with one.

AND IT IS DRAWN ABOVE THE PAGE, on every public page, from a context
processor. The guest this exists for is reading the directions on the morning
they set off; a notice below the fold on one page is the same as no notice.
"""
from _harness import Suite, clients, db

from datetime import timedelta

import _harness

m = _harness.m
TAG = "ZZROAD"


def _cleanup(conn):
    conn.execute("DELETE FROM road_notices WHERE headline LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("what the road is doing")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    anon = m.app.test_client()
    today = m.house_today()

    s.section("It has to stop on its own")

    ok, why = m.add_road_notice(conn, TAG + " Ice", "", "serious",
                                today.isoformat(), "")
    s.check("a notice with no end date is refused", not ok, detail=str(why))
    s.check("and says why", why and "stop on its own" in why, detail=str(why))

    ok, why = m.add_road_notice(conn, TAG + " Ice", "", "serious",
                                today.isoformat(),
                                (today + timedelta(days=90)).isoformat())
    s.check("and neither can it run for three months", not ok, detail=str(why))
    s.check("with the reason being what it would cost",
            why and "approach copy" in why,
            detail="a warning that outlives the weather is what teaches "
                   "everybody to ignore the panel: %s" % why)

    ok, why = m.add_road_notice(conn, TAG + " Backwards", "", "note",
                                (today + timedelta(days=5)).isoformat(),
                                today.isoformat())
    s.check("nor can it end before it starts", not ok, detail=str(why))

    ok, why = m.add_road_notice(conn, "", "", "note", today.isoformat(),
                                today.isoformat())
    s.check("and it needs something to read", not ok, detail=str(why))

    s.section("A real one")

    ok, why = m.add_road_notice(
        conn, TAG + " Chains needed above Les Cabannes",
        "The D8 is gritted to the village only.", "serious",
        today.isoformat(), (today + timedelta(days=3)).isoformat())
    s.check("goes up", ok, detail=str(why))
    conn.commit()

    now = m.road_notice(conn)
    s.check("and is what a guest is shown",
            now and now["headline"] == TAG + " Chains needed above Les Cabannes",
            detail=str(dict(now) if now else None))

    s.section("Yesterday's weather is not today's")

    conn.execute("DELETE FROM road_notices WHERE headline LIKE ?", (TAG + "%",))
    m.add_road_notice(conn, TAG + " Old ice", "", "serious",
                      (today - timedelta(days=30)).isoformat(),
                      (today - timedelta(days=20)).isoformat())
    conn.commit()
    s.check("a notice whose last day has passed is not shown",
            m.road_notice(conn) is None,
            detail="it came down without anybody remembering to take it down, "
                   "which is the only reason the next one will be believed")
    s.check("but it is still on the owner's list",
            any(r["headline"] == TAG + " Old ice" for r in m.road_notices_all(conn)))
    s.check("marked as finished rather than live",
            [r for r in m.road_notices_all(conn)
             if r["headline"] == TAG + " Old ice"][0]["finished"])

    # And one that has not started yet.
    m.add_road_notice(conn, TAG + " Next week", "", "note",
                      (today + timedelta(days=7)).isoformat(),
                      (today + timedelta(days=9)).isoformat())
    conn.commit()
    s.check("and one that starts next week is not shown yet",
            m.road_notice(conn) is None, detail=str(m.road_notice(conn)))

    s.section("Only one, and the loudest")

    conn.execute("DELETE FROM road_notices WHERE headline LIKE ?", (TAG + "%",))
    m.add_road_notice(conn, TAG + " A note", "", "note", today.isoformat(),
                      (today + timedelta(days=2)).isoformat())
    m.add_road_notice(conn, TAG + " Serious", "", "serious", today.isoformat(),
                      (today + timedelta(days=2)).isoformat())
    conn.commit()
    now = m.road_notice(conn)
    s.check("the serious one wins", now and now["headline"] == TAG + " Serious",
            detail=str(now["headline"]) if now else None)
    s.check("and only one comes back",
            not isinstance(m.road_notice(conn), list),
            detail="two notices on an arrival page is a wall of warnings, "
                   "which reads as a house with a problem rather than a road "
                   "with one")

    s.section("Where a guest actually sees it")

    body = anon.get("/").get_data(as_text=True)
    s.check("it is on the public page", TAG + " Serious" in body)
    # The one that matters: the person this is for is reading the directions.
    for path in ("/contact", "/book"):
        r = anon.get(path)
        s.check("and on %s" % path, TAG + " Serious" in r.get_data(as_text=True),
                detail="HTTP %s — a notice on one page is the same as no "
                       "notice for a guest who opened a different one"
                       % r.status_code)
    s.check("drawn above the page rather than buried in it",
            body.index(TAG + " Serious") < body.index("g-cred"),
            detail="somebody reading the directions on the morning they set "
                   "off does not scroll")

    conn.execute("DELETE FROM road_notices WHERE headline LIKE ?", (TAG + "%",))
    conn.commit()
    s.check("and it is gone the moment it comes down",
            TAG + " Serious" not in anon.get("/").get_data(as_text=True))

    s.section("The page")

    r = oc.get("/admin/road")
    s.check("the owner can open it", r.status_code == 200, r)
    s.check("an employee cannot", ec.get("/admin/road").status_code != 200)
    r = oc.post("/admin/road", data={
        "headline": TAG + " Through the form", "detail": "",
        "severity": "note", "starts_on": today.isoformat(),
        "ends_on": (today + timedelta(days=1)).isoformat()}, follow_redirects=True)
    s.check("and can put one up through it",
            conn.execute("SELECT COUNT(*) c FROM road_notices WHERE headline = ?",
                         (TAG + " Through the form",)).fetchone()["c"] == 1,
            detail="; ".join(_harness.flashes(r)[:1]))
    row = conn.execute("SELECT id FROM road_notices WHERE headline = ?",
                       (TAG + " Through the form",)).fetchone()
    if row:
        oc.post("/admin/road/%d/remove" % row["id"], follow_redirects=True)
        s.check("and take it down early when the weather turns out better",
                conn.execute("SELECT COUNT(*) c FROM road_notices WHERE id = ?",
                             (row["id"],)).fetchone()["c"] == 0)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
