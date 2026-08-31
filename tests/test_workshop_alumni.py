"""The people who did this workshop before.

The only workshop segment was "everyone who has ever attended anything",
which is both too broad and useless for the one pitch that actually works:
a new session of a thing somebody has already done and liked. Emailing the
pottery alumni about a painting week is how a mailing list gets
unsubscribed from.

Four things worth holding:

  - PAST SESSIONS ONLY. Somebody booked on next month's running has not
    done it, they are coming to it, and "you loved this, come again" is
    nonsense to them.

  - NOT THE PEOPLE ALREADY BOOKED on the session being promoted. Sending
    "come and do this" to somebody who has paid for it is the most obvious
    possible way to look like nobody reads their own records. Pending too:
    somebody halfway through paying does not need advertising at.

  - do_not_email, honoured like every other segment.

  - AND A MALFORMED TOKEN MUST NOT WIDEN THE SEND. Both call sites fall
    back to every segment when nothing valid is chosen, which is right for
    an empty form and catastrophic for a dropped alumni token: aiming at
    twenty-three people and hitting the whole database. That is the check
    that matters most here.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ZZWALUM"


def _cleanup(conn):
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_sessions WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("People who did this workshop before")
    today = date.today()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc).isoformat()

    def workshop(title):
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person,
                                      default_capacity, active, created_at)
               VALUES (?, '', 300, 12, 1, ?)""", (TAG + " " + title, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def session(wid, start):
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                              capacity, created_at)
               VALUES (?, ?, ?, 12, ?)""",
            (wid, start.isoformat(), (start + timedelta(days=3)).isoformat(), now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def register(sid, name, email=None, status="confirmed", no_email=0):
        email = email or f"{name.lower()}@example.invalid"
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                              party_size, status, reference_code,
                                              manage_token, do_not_email, created_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
            (sid, name, email, status, f"{TAG}{sid}{name[:4].upper()}",
             f"tok-{TAG.lower()}-{sid}-{name.lower()}", no_email, now))

    pottery = workshop("Pottery")
    painting = workshop("Painting")

    last_year = session(pottery, today - timedelta(days=300))
    last_spring = session(pottery, today - timedelta(days=120))
    coming = session(pottery, today + timedelta(days=40))
    other = session(painting, today - timedelta(days=200))

    register(last_year, "Aline")
    register(last_spring, "Aline")          # twice
    register(last_year, "Bruno")
    register(last_spring, "Chantal")
    register(last_year, "Dorothee", no_email=1)
    register(last_year, "Emile", status="cancelled")
    register(other, "Fabien")               # a different workshop
    register(coming, "Gilles")              # coming, has not done it
    # Bruno has already booked the coming one.
    register(coming, "Bruno")
    conn.commit()

    def emails(rows):
        return sorted(r["guest_email"] for r in rows)

    s.section("Who has done this one")
    rows = m.workshop_alumni(conn, pottery)
    s.check("everyone who attended a past session is there",
            "aline@example.invalid" in emails(rows)
            and "bruno@example.invalid" in emails(rows)
            and "chantal@example.invalid" in emails(rows),
            detail=str(emails(rows)))
    s.check("somebody who did a different workshop is not",
            "fabien@example.invalid" not in emails(rows),
            detail="the pottery alumni do not want to hear about the "
                   "painting week, which is the entire reason this exists")
    s.check("nor somebody booked on the session still to come",
            "gilles@example.invalid" not in emails(rows),
            detail="they have not done it, they are coming to it")
    s.check("a cancelled registration does not count as having attended",
            "emile@example.invalid" not in emails(rows))
    s.check("and do-not-email is honoured",
            "dorothee@example.invalid" not in emails(rows),
            detail="the same flag every other segment respects")

    s.section("How many times, and when")
    by_email = {r["guest_email"]: r for r in rows}
    s.check("somebody who came twice is counted twice",
            by_email["aline@example.invalid"]["times"] == 2,
            detail=str(by_email["aline@example.invalid"]["times"]))
    s.check("with the most recent visit against them",
            by_email["aline@example.invalid"]["last_attended"]
            == (today - timedelta(days=120)).isoformat(),
            detail=str(by_email["aline@example.invalid"]["last_attended"]))
    s.check("and the most frequent come first",
            rows[0]["guest_email"] == "aline@example.invalid",
            detail=str([r["guest_email"] for r in rows]))

    s.section("Not the people already booked on the one being sold")
    rows = m.workshop_alumni(conn, pottery, exclude_session_id=coming)
    s.check("somebody already booked on it is left out",
            "bruno@example.invalid" not in emails(rows),
            detail="'come and do this' to somebody who has paid for it is "
                   "the most obvious way to look like nobody reads their "
                   "own records")
    s.check("the rest are still there",
            "aline@example.invalid" in emails(rows)
            and "chantal@example.invalid" in emails(rows),
            detail=str(emails(rows)))

    # Halfway through paying is still booked.
    conn.execute("UPDATE workshop_bookings SET status = 'pending' "
                 "WHERE session_id = ? AND guest_name = 'Bruno'", (coming,))
    conn.commit()
    rows = m.workshop_alumni(conn, pottery, exclude_session_id=coming)
    s.check("and so is somebody halfway through paying for it",
            "bruno@example.invalid" not in emails(rows),
            detail="a deposit not yet taken is not a reason to advertise at "
                   "somebody the thing they are in the middle of buying")

    s.section("It reaches the campaign as a segment")
    picked = m.promo_blast_recipients(conn, [f"workshop:{pottery}"])
    s.check("the token selects the alumni",
            "aline@example.invalid" in picked and "chantal@example.invalid" in picked,
            detail=str(sorted(picked)[:6]))
    s.check("and nobody from another workshop",
            "fabien@example.invalid" not in picked)

    both = m.promo_blast_recipients(conn, [f"workshop:{pottery}",
                                           f"notsession:{coming}"])
    s.check("with the session to exclude given too",
            "bruno@example.invalid" not in both and "aline@example.invalid" in both,
            detail=str(sorted(both)[:6]))

    s.section("A dropped token must not widen the send")
    # The call sites fall back to every segment when nothing valid is
    # chosen. Right for an empty form; catastrophic if a malformed alumni
    # token were silently dropped, because the campaign would go from
    # twenty-three people to the entire database.
    s.check("a well-formed token survives the filter",
            m.valid_segments([f"workshop:{pottery}"]) == [f"workshop:{pottery}"],
            detail=str(m.valid_segments([f"workshop:{pottery}"])))
    for junk in ("workshop:", "workshop:abc", "workshop:1:2", "everyone",
                 "workshop;1"):
        s.check(f"{junk!r} is refused", m.valid_segments([junk]) == [],
                detail=str(m.valid_segments([junk])))
    s.check("the plain segments still pass",
            m.valid_segments(["room", "workshop", "newsletter"])
            == ["room", "workshop", "newsletter"])

    s.section("A workshop that has never run has no alumni")
    fresh = workshop("Never Yet Run")
    session(fresh, today + timedelta(days=60))
    conn.commit()
    s.check("and reports none rather than erroring",
            m.workshop_alumni(conn, fresh) == [])

    s.section("The owner can pick one")
    # Its own code rather than whatever is seeded: a suite that depends on
    # existing rows passes or fails on somebody else's data.
    conn.execute(
        """INSERT INTO promo_codes (code, discount_type, discount_value,
                                    active, created_at)
           VALUES (?, 'percent', 10, 1, ?)""", (TAG + "CODE", now))
    code_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()

    r = oc.get(f"/admin/promo-codes/{code_id}/blast")
    body = r.get_data(as_text=True)
    s.check("the blast page offers the workshops that have alumni",
            r.status_code == 200 and f'value="workshop:{pottery}"' in body,
            detail=f"HTTP {r.status_code}")
    s.check("but not one that has never run",
            f'value="workshop:{fresh}"' not in body,
            detail="offering a list that cannot exist")

    r = oc.get(f"/admin/promo-codes/{code_id}/blast?segment=workshop:{pottery}")
    body = r.get_data(as_text=True)
    s.check("picking one keeps it picked",
            f'value="workshop:{pottery}" selected' in body,
            detail="the option must carry `selected`, not merely be on a "
                   "page that contains the word somewhere")
    s.check("and the count is the alumni rather than everybody",
            str(len(m.workshop_alumni(conn, pottery))) in body,
            detail="if the token were dropped by the segment filter the "
                   "fallback would send to every list there is")

    conn.execute("DELETE FROM promo_codes WHERE code = ?", (TAG + "CODE",))
    conn.commit()

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
