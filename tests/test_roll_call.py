"""Who is in the building, counted once instead of on two screens.

The app knew who was clocked in and it knew who was staying. It had never put
the two together, so the list a fire officer asks for could only be made by
opening two pages and adding them up on paper -- at the moment when nobody is
going to open two pages.

FOUR THINGS THIS HAS TO GET RIGHT, and each is a way of being wrong that reads
as working.

  PARTY SIZE, NOT BOOKINGS. A family of five is five people on a landing. A
  headcount that counted bookings would send somebody back in for four people
  who were already outside, or leave four inside.

  THE CLOCK IS LOCAL. A muster list showing 13:20 for a shift that started at
  15:20 is read by somebody standing in a courtyard comparing it against their
  own watch. Timestamps are stored in UTC and the Ariege is not UTC.

  A FORGOTTEN CLOCK-OUT IS NAMED, NOT HIDDEN AND NOT DROPPED. Somebody showing
  as on shift for thirty hours is almost certainly at home. Dropping them is
  how a real person stops being looked for; saying nothing makes the total
  wrong. It says which, and still counts them.

  AND ANY MEMBER OF STAFF CAN OPEN IT. Every other list here can wait for the
  right person to log in. This one is read at two in the morning by whoever is
  standing outside, and an access preset is not a thing anybody checks first.
"""
from _harness import Suite, clients, db

from datetime import timedelta

import _harness

m = _harness.m
TAG = "ZZROLL"


def _cleanup(conn):
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("who is in the house")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    room = _harness.ensure_room()

    base = m.on_site_now(conn)

    s.section("Staff who are clocked in")

    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, phone,
             status, created_at)
           VALUES (?, 'x', 'employee', ?, 'Gardener', '+33600000001', 'active', ?)""",
        (TAG + "@example.invalid", TAG + " Gardener", now.isoformat()))
    gardener = conn.execute("SELECT id FROM users WHERE name = ?",
                            (TAG + " Gardener",)).fetchone()["id"]
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at) VALUES (?, ?)",
                 (gardener, (now - timedelta(hours=3)).isoformat()))
    conn.commit()

    here = m.on_site_now(conn)
    s.check("somebody on shift is on the list",
            here["staff_count"] == base["staff_count"] + 1,
            detail="%d then %d" % (base["staff_count"], here["staff_count"]))
    mine = [r for r in here["staff"] if r["name"] == TAG + " Gardener"]
    s.check("with what they do and how to ring them",
            mine and mine[0]["job_role"] == "Gardener"
            and mine[0]["phone"] == "+33600000001",
            detail=str(mine[:1]))

    # THE CLOCK. Stored UTC, read by somebody looking at their own watch.
    expected = (now - timedelta(hours=3)).astimezone(m.LOCAL_TZ).strftime("%H:%M")
    s.check("the time shown is the local clock, not UTC",
            mine and mine[0]["since_time"] == expected,
            detail="%s, and the Ariege said %s"
                   % (mine[0]["since_time"] if mine else None, expected))

    s.section("A clock-out somebody forgot")

    conn.execute("UPDATE time_entries SET clock_in_at = ? WHERE user_id = ?",
                 ((now - timedelta(hours=30)).isoformat(), gardener))
    conn.commit()
    here = m.on_site_now(conn)
    mine = [r for r in here["staff"] if r["name"] == TAG + " Gardener"]
    s.check("thirty hours on shift is flagged as doubtful",
            mine and mine[0]["probably_forgot"], detail=str(mine[:1]))
    s.check("and named where somebody will read it",
            TAG + " Gardener" in here["doubtful"], detail=str(here["doubtful"]))
    s.check("but still counted", here["staff_count"] == base["staff_count"] + 1,
            detail="dropping them is how a real person stops being looked for")

    s.section("Guests staying tonight")

    ref = TAG + "-1"
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, guest_phone, arrival_date, departure_date, party_size,
             status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, '', '+33600000002', ?, ?, 5, 'confirmed', 0, 0, ?)""",
        (room["id"], ref, "tok" + TAG.lower(), TAG + " Family",
         (today - timedelta(days=1)).isoformat(),
         (today + timedelta(days=2)).isoformat(), now.isoformat()))
    conn.commit()

    here = m.on_site_now(conn)
    fam = [g for g in here["guests"] if g["name"] == TAG + " Family"]
    s.check("a stay covering tonight is on the list", bool(fam), detail=str(len(here["guests"])))
    # The one that sends somebody back into a building for people already out.
    s.check("five people count as five, not as one booking",
            here["guest_count"] == base["guest_count"] + 5,
            detail="%d then %d — a booking is not a person"
                   % (base["guest_count"], here["guest_count"]))
    s.check("and the total is staff plus people",
            here["total"] == here["staff_count"] + here["guest_count"],
            detail="%d vs %d + %d" % (here["total"], here["staff_count"],
                                      here["guest_count"]))

    s.section("Who is not on it")

    # Departure morning: the room is free and they have gone.
    conn.execute("UPDATE bookings SET departure_date = ? WHERE reference_code = ?",
                 (today.isoformat(), ref))
    conn.commit()
    here = m.on_site_now(conn)
    s.check("somebody who checked out this morning is not counted",
            not [g for g in here["guests"] if g["name"] == TAG + " Family"],
            detail="departure day is a night they are not here for")

    conn.execute("UPDATE bookings SET departure_date = ?, status = 'pending' "
                 "WHERE reference_code = ?",
                 ((today + timedelta(days=2)).isoformat(), ref))
    conn.commit()
    here = m.on_site_now(conn)
    s.check("and neither is a request nobody has accepted",
            not [g for g in here["guests"] if g["name"] == TAG + " Family"],
            detail="a pending booking is a hope, not somebody in a bed")

    s.section("The page")

    r = oc.get("/roll-call")
    s.check("the owner can open it", r.status_code == 200, r)
    # The whole argument for this page: it is read by whoever is outside.
    r = ec.get("/roll-call")
    s.check("and so can any member of staff", r.status_code == 200,
            detail="HTTP %s — an access preset is not a thing anybody checks "
                   "at two in the morning" % r.status_code)
    body = r.get_data(as_text=True)
    s.check("it says the count out loud", str(m.on_site_now(conn)["total"]) in body)
    s.check("and says what it cannot know",
            "not who is" in body.lower() or "expected to be here" in body.lower(),
            detail="a list that claimed to be certain would be trusted at "
                   "exactly the wrong moment")
    anon = m.app.test_client()
    s.check("a stranger cannot read who is in the house",
            anon.get("/roll-call", follow_redirects=False).status_code in (301, 302, 401, 403),
            detail="it carries every guest's name, room and telephone number")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
