"""Three things the house does and never wrote down.

THE CLEANING ROUND. Turnover tasks exist per stay — strip the bed, reset
the bathroom, the room the guest just left. Nothing covered the parts
nobody books: the hall, the stairs, the salon, the windows twice a year.
Deliberately not the maintenance schedule, which already exists and is a
different rhythm — a chimney is swept by somebody who comes, a staircase by
whoever is here.

METER READINGS. Energy is the biggest variable cost after labour and the
app had no idea what it was. Not bill-checking: the supplier sends the
bill. It is the thing a bill cannot say, which is whether this month is
like the last one.

LOST PROPERTY. A guest rings about a scarf.

What each one is really guarding:

  - NEVER DONE READS AS DUE NOW, not as nothing. A NULL sorted on a date
    quietly means "never due", which is the opposite of true.

  - A METER ONLY GOES UP. A reading below the last one is a misread or a
    replaced meter, and writing it down silently turns one month's use
    negative and the next month's enormous.

  - AND USE IS PER DAY. Readings are never taken evenly, so a bare
    difference between a fortnight and a quarter is not a comparison.

  - NOTHING IS EVER THROWN AWAY BY THE APP. An old item is flagged as fair
    to let go; letting it go stays something a person does. The one thing
    a guest rings about six months later is the thing a job would have
    binned.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes, house_today

import _harness

m = _harness.m
TAG = "ZZUPK"


def _cleanup(conn):
    conn.execute("DELETE FROM cleaning_rounds WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM meter_readings WHERE meter LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM lost_property WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM site_visitors WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Cleaning, meters and the drawer")
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    # ------------------------------------------------------------- cleaning
    s.section("A round nobody has done yet is due now")
    r = ec.post("/management/cleaning",
                data={"what": TAG + " sweep the main stair", "area": "hall",
                      "every_days": "7"}, follow_redirects=True)
    rounds = [x for x in m.cleaning_rounds_due(conn, today)
              if x["round"]["what"].startswith(TAG)]
    s.check("an employee can add one", len(rounds) == 1, detail=str(flashes(r)))
    s.check("never done reads as due now",
            rounds[0]["never"] and rounds[0]["days"] == 0,
            detail=f"{rounds[0]['days']} — a NULL sorted on a date quietly "
                   "means never due, which is the opposite of true")

    s.section("And is measured from when it was last done")
    rid = rounds[0]["round"]["id"]
    ec.post(f"/management/cleaning/{rid}/done",
            data={"done_on": (today - timedelta(days=10)).isoformat()},
            follow_redirects=True)
    again = [x for x in m.cleaning_rounds_due(conn, today)
             if x["round"]["id"] == rid][0]
    s.check("done ten days ago on a seven-day round is three days late",
            again["overdue"] and again["days"] == -3,
            detail=str(again["days"]))

    ec.post(f"/management/cleaning/{rid}/done",
            data={"done_on": today.isoformat()}, follow_redirects=True)
    fresh = [x for x in m.cleaning_rounds_due(conn, today)
             if x["round"]["id"] == rid][0]
    s.check("and doing it late does not make the next one early",
            fresh["days"] == 7,
            detail=f"{fresh['days']} — counted from the day it was done, not "
                   "from the day it was due")

    s.section("Only the owner takes something off the round")
    r = ec.post(f"/management/cleaning/{rid}/stop", follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    # --------------------------------------------------------------- meters
    s.section("A meter only goes up")
    ec.post("/management/meters",
            data={"meter": TAG + " Electricity", "reading": "1000",
                  "read_on": (today - timedelta(days=30)).isoformat()},
            follow_redirects=True)
    r = ec.post("/management/meters",
                data={"meter": TAG + " Electricity", "reading": "900",
                      "read_on": today.isoformat()}, follow_redirects=True)
    s.check("a lower reading is refused",
            any("only goes up" in f for f in flashes(r)), detail=str(flashes(r)))
    s.check("and nothing was written",
            conn.execute("SELECT COUNT(*) AS c FROM meter_readings "
                         "WHERE meter LIKE ?", (TAG + "%",)).fetchone()["c"] == 1,
            detail="a lower number written silently makes one month's use "
                   "negative and the next month's enormous")

    r = ec.post("/management/meters",
                data={"meter": TAG + " Electricity", "reading": "900",
                      "read_on": today.isoformat(), "confirm_lower": "on"},
                follow_redirects=True)
    s.check("unless somebody says the meter was replaced",
            conn.execute("SELECT COUNT(*) AS c FROM meter_readings "
                         "WHERE meter LIKE ?", (TAG + "%",)).fetchone()["c"] == 2,
            detail=str(flashes(r)))

    s.section("Use is worked out, and per day")
    conn.execute("DELETE FROM meter_readings WHERE meter LIKE ?", (TAG + "%",))
    for days_ago, value in ((40, 1000.0), (10, 1600.0)):
        conn.execute(
            """INSERT INTO meter_readings (meter, read_on, reading, created_at)
               VALUES (?, ?, ?, ?)""",
            (TAG + " Electricity",
             (today - timedelta(days=days_ago)).isoformat(), value, now))
    conn.commit()
    hist = m.meter_history(conn, meter=TAG + " Electricity")[TAG + " Electricity"]
    latest = hist[0]
    s.check("six hundred used over thirty days", latest["used"] == 600.0,
            detail=str(latest["used"]))
    s.check("which is twenty a day", latest["per_day"] == 20.0,
            detail=f"{latest['per_day']} — readings are never taken evenly, "
                   "so a bare difference between a fortnight and a quarter "
                   "is not a comparison")
    s.check("and the first reading has nothing to compare against",
            hist[-1]["used"] is None,
            detail="a reading on its own says nothing; it only ever goes up")

    s.section("Two readings for one meter on one day")
    r = ec.post("/management/meters",
                data={"meter": TAG + " Electricity", "reading": "1700",
                      "read_on": (today - timedelta(days=10)).isoformat(),
                      "confirm_lower": "on"}, follow_redirects=True)
    s.check("the second is refused rather than silently replacing the first",
            any("already has a reading" in f for f in flashes(r)),
            detail=str(flashes(r)))

    # ------------------------------------------------------- lost property
    s.section("The drawer")
    ec.post("/management/lost-property",
            data={"what": TAG + " green scarf", "found_where": "salon",
                  "guest_name": "Mme Aubert",
                  "guest_contact": "aubert@example.invalid"},
            follow_redirects=True)
    item = conn.execute("SELECT * FROM lost_property WHERE what LIKE ?",
                        (TAG + "%",)).fetchone()
    s.check("something left behind is written down", item is not None)
    s.check("and starts held", item and item["status"] == "held",
            detail=str(item["status"]) if item else "")

    s.section("Sending it back keeps it on the list")
    ec.post(f"/management/lost-property/{item['id']}/resolve",
            data={"state": "returned", "resolved_note": "posted"},
            follow_redirects=True)
    after = conn.execute("SELECT * FROM lost_property WHERE id = ?",
                         (item["id"],)).fetchone()
    s.check("it is still there", after is not None)
    s.check("marked as sent back rather than deleted",
            after["status"] == "returned" and after["resolved_on"],
            detail="'did we ever send that scarf back' has to stay "
                   "answerable after the drawer is tidied")

    s.section("Nothing is thrown away by the app")
    old = conn.execute(
        """INSERT INTO lost_property (what, found_on, status, created_at)
           VALUES (?, ?, 'held', ?)""",
        (TAG + " umbrella",
         (today - timedelta(days=m.LOST_PROPERTY_KEEP_DAYS + 30)).isoformat(),
         now))
    conn.commit()
    body = ec.get("/management/lost-property").get_data(as_text=True)
    s.check("an old item is flagged as fair to let go",
            "fair to let go" in body or "held over" in body,
            detail="said, never done")
    s.check("but it is still held",
            conn.execute("SELECT status FROM lost_property WHERE what = ?",
                         (TAG + " umbrella",)).fetchone()["status"] == "held",
            detail="the one thing a guest rings about six months later is "
                   "the thing a job would have binned")

    s.section("Who is on site who is neither staff nor guest")
    ec.post("/management/visitors",
            data={"name": TAG + " Roofer", "company": "Maison Roux",
                  "reason": "scaffolding"}, follow_redirects=True)
    here = [v for v in m.visitors_on_site(conn)
            if v["visitor"]["name"].startswith(TAG)]
    s.check("signing somebody in puts them on the list", len(here) == 1,
            detail=str(len(here)))
    s.check("and they are not flagged as probably gone yet",
            not here[0]["probably_gone"], detail=str(here[0]["hours"]))

    s.section("Somebody here since this morning is a judgement, not a fact")
    # A register that tidies itself is always neat and never true, which is
    # worse than none in the one situation it exists for.
    vid = here[0]["visitor"]["id"]
    long_ago = (m.datetime.now(m.timezone.utc)
                - m.timedelta(hours=m.VISITOR_LONG_HOURS + 2)).isoformat()
    conn.execute("UPDATE site_visitors SET signed_in_at = ? WHERE id = ?",
                 (long_ago, vid))
    conn.commit()
    stale = [v for v in m.visitors_on_site(conn) if v["visitor"]["id"] == vid][0]
    s.check("they are flagged after a long day", stale["probably_gone"],
            detail=str(stale["hours"]))
    s.check("but still counted as on site",
            conn.execute("SELECT signed_out_at FROM site_visitors WHERE id = ?",
                         (vid,)).fetchone()["signed_out_at"] is None,
            detail="nothing signs anybody out automatically")

    s.section("Signing out, once")
    ec.post(f"/management/visitors/{vid}/out", follow_redirects=True)
    s.check("they come off the list",
            not [v for v in m.visitors_on_site(conn)
                 if v["visitor"]["id"] == vid])
    r = ec.post(f"/management/visitors/{vid}/out", follow_redirects=True)
    s.check("and signing out twice says so rather than moving the time",
            any("already signed out" in f for f in flashes(r)),
            detail=str(flashes(r)))

    s.section("A stranger cannot see any of it")
    anon = m.app.test_client()
    for url in ("/management/cleaning", "/management/meters",
                "/management/lost-property", "/management/visitors"):
        r = anon.get(url, follow_redirects=False)
        s.check(f"{url} needs a login",
                r.status_code in (302, 303) and "/login" in r.headers.get("Location", ""),
                detail=f"HTTP {r.status_code}")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
