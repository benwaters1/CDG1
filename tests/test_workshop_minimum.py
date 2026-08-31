"""A workshop that will not reach the number it needs to run.

Capacity was recorded from the beginning -- the most a session can take --
and the number below which it should not go ahead at all was not recorded
anywhere in 44k lines. So a session with two people three weeks out looked
identical, on every page, to a session with nine.

Both ways of getting this wrong cost real money: running a residential
workshop for two loses the difference between what it takes and what it
costs, and cancelling one late means guests who have already booked flights.

The two decisions this suite exists to hold in place:

  - CONFIRMED HEADS ONLY. A pending registration has not paid a deposit.
    Counting it makes the figure look better and changes nothing about who
    turns up, which is the exact self-deception the feature exists to
    prevent. It is reported beside the figure, never inside it.

  - THE WAITLIST CHANGES THE ANSWER. A session three short with four people
    waiting needs a telephone call, not a cancellation. A warning that says
    "this will not run" is worth much less than one that says "and here are
    the people who could make it" -- and telling the owner to cancel a
    session they could have filled is the more expensive mistake of the two.
"""
from datetime import date, timedelta

from _harness import Suite, db, clients, flashes
import _harness

m = _harness.m


def _make_workshop(conn, title, price=450.0):
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, '', ?, 12, 1, ?)""",
        (title, price, m.datetime.now(m.timezone.utc).isoformat()))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _make_session(conn, workshop_id, start, minimum, decide_by_days=21,
                  capacity=12):
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                          capacity, min_participants,
                                          decide_by_days, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (workshop_id, start.isoformat(), (start + timedelta(days=4)).isoformat(),
         capacity, minimum, decide_by_days,
         m.datetime.now(m.timezone.utc).isoformat()))
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _register(conn, session_id, name, status, party_size=1):
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                                          party_size, status, reference_code,
                                          manage_token, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, name, f"{name.lower()}@example.invalid", party_size, status,
         f"ZZWMIN{session_id}{name[:3].upper()}",
         f"tok-zzwmin-{session_id}-{name.lower()}",
         m.datetime.now(m.timezone.utc).isoformat()))


def _wait(conn, session_id, name, party_size=1, status="open"):
    conn.execute(
        """INSERT INTO workshop_waitlist (session_id, name, email, party_size,
                                          status, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, name, f"{name.lower()}@example.invalid", party_size, status,
         m.datetime.now(m.timezone.utc).isoformat()))


def _find(rows, session_id):
    return next((w for w in rows if w["session"]["id"] == session_id), None)


def run():
    s = Suite("A workshop that will not fill")
    today = date.today()
    conn = db()
    oc, ec, _owner, _emp = clients()

    # Everything is created fresh and torn down at the end, and every check
    # measures THIS session rather than the whole house -- a seeded database
    # has workshops of its own and a check that counts them all passes or
    # fails on somebody else's data.
    wid = _make_workshop(conn, "Test: Winter Botanical Printing")

    # Short, and the decision is due in three days.
    soon = _make_session(conn, wid, today + timedelta(days=24), minimum=6)
    _register(conn, soon, "Aline", "confirmed", 2)

    # Short, but the decision is nine weeks out.
    far = _make_session(conn, wid, today + timedelta(days=70), minimum=6)
    _register(conn, far, "Bruno", "confirmed", 2)

    # Comfortably full.
    full = _make_session(conn, wid, today + timedelta(days=24), minimum=4)
    _register(conn, full, "Chantal", "confirmed", 5)

    # No minimum set: the default. Nothing about it should change.
    unset = _make_session(conn, wid, today + timedelta(days=24), minimum=0)
    _register(conn, unset, "Damien", "confirmed", 1)

    conn.commit()
    rows = m.sessions_at_risk(conn, today)

    s.section("It knows which sessions are actually in trouble")
    s.check("a session short of its minimum is found", _find(rows, soon) is not None)
    s.check("one that has reached its minimum is not",
            _find(rows, full) is None,
            detail="five confirmed against a minimum of four")
    s.check("and one with no minimum set is not",
            _find(rows, unset) is None,
            detail="the default is 0, so no existing session became at risk "
                   "the moment this shipped")

    s.section("A decision two months away is not an alarm")
    near, distant = _find(rows, soon), _find(rows, far)
    s.check("a session short with days left is urgent", near and near["urgent"])
    s.check("one short with nine weeks left is not",
            distant and not distant["urgent"],
            detail="it is still listed -- it is simply not something to be "
                   "told about every morning, and a panel that shouts about "
                   "both teaches the owner to skim the one that matters")

    s.section("Only people who have paid are counted")
    # This is the whole argument. Three pending registrations would take the
    # session from 2 to 5 of 6 and it would still not run, because none of
    # them has paid a deposit.
    _register(conn, soon, "Elodie", "pending", 3)
    conn.commit()
    after = _find(m.sessions_at_risk(conn, today), soon)
    s.check("a pending registration does not raise the count",
            after and after["confirmed"] == 2,
            detail=f"{after['confirmed'] if after else '?'} confirmed")
    s.check("the session is still short", after and after["short"] == 4)
    s.check("but the owner is told they exist",
            after and after["pending"] == 3,
            detail="shown beside the figure and never added into it, so it "
                   "can be chased without flattering the number")

    s.section("A waiting list changes what to do about it")
    before_wait = _find(m.sessions_at_risk(conn, today), soon)
    s.check("with nobody waiting, nothing suggests it can be filled",
            before_wait and not before_wait["waitlist_could_fill"])

    _wait(conn, soon, "Fabienne", 4)
    conn.commit()
    with_wait = _find(m.sessions_at_risk(conn, today), soon)
    s.check("four people waiting against a shortfall of four can cover it",
            with_wait and with_wait["waitlist_could_fill"],
            detail="the action is a telephone call, not a cancellation")
    s.check("and they are counted as heads, not as rows",
            with_wait and with_wait["waiting_heads"] == 4 and with_wait["waiting"] == 1,
            detail="one entry for a party of four fills four places")

    # Somebody already contacted and booked is not still waiting.
    conn.execute("UPDATE workshop_waitlist SET status = 'booked' WHERE session_id = ?",
                 (soon,))
    conn.commit()
    settled = _find(m.sessions_at_risk(conn, today), soon)
    s.check("somebody who already booked is no longer on the waiting list",
            settled and not settled["waitlist_could_fill"],
            detail="otherwise the same person would be counted twice -- once "
                   "as a registration and once as somebody to ring")
    conn.execute("UPDATE workshop_waitlist SET status = 'open' WHERE session_id = ?",
                 (soon,))
    conn.commit()

    s.section("The deadline is the decision, not the start")
    late = _make_session(conn, wid, today + timedelta(days=10), minimum=6,
                         decide_by_days=21)
    _register(conn, late, "Gilles", "confirmed", 1)
    conn.commit()
    overdue = _find(m.sessions_at_risk(conn, today), late)
    s.check("a session past its own decision date is overdue",
            overdue and overdue["overdue"],
            detail="it starts in ten days and the call was due at twenty-one")
    s.check("and the days left reads as negative rather than as time in hand",
            overdue and overdue["days_left"] < 0,
            detail=str(overdue["days_left"]) if overdue else "")

    s.section("What running it as it stands is worth")
    money = _find(m.sessions_at_risk(conn, today), soon)
    s.check("what two people at 450 brings in", money and money["taken"] == 900.0,
            detail=str(money["taken"]) if money else "")
    s.check("against what six would", money and money["at_minimum"] == 2700.0,
            detail=str(money["at_minimum"]) if money else "")
    s.check("and the two agree with the shortfall between them",
            money and round(money["taken"] + money["gap"], 2) == money["at_minimum"],
            detail="rows must add up to their total")

    s.section("A minimum above the capacity could never be met")
    r = oc.post(f"/admin/workshops/{wid}/sessions/new",
                data={"start_date": (today + timedelta(days=40)).isoformat(),
                      "end_date": (today + timedelta(days=44)).isoformat(),
                      "capacity": "4", "min_participants": "9"},
                follow_redirects=True)
    made = conn.execute(
        """SELECT * FROM workshop_sessions WHERE workshop_id = ?
            ORDER BY id DESC LIMIT 1""", (wid,)).fetchone()
    s.check("is clamped to the capacity",
            made and made["min_participants"] == 4,
            detail=(f"minimum {made['min_participants']} of capacity "
                    f"{made['capacity']}" if made else str(flashes(r))) +
                   " -- otherwise it sits on the at-risk list until its own "
                   "start date with nothing that could ever clear it")
    s.check("and the decision window defaults rather than being left empty",
            made and made["decide_by_days"] == m.WORKSHOP_DECIDE_BY_DAYS,
            detail=str(made["decide_by_days"]) if made else "")

    s.section("It reaches the owner rather than sitting in a function")
    warnings = None
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    mine = [w for w in warnings if "short of the number to run" in w["title"]]
    s.check("a short session appears on the owner's home page", bool(mine),
            detail=f"{len(warnings)} warnings, none about workshops"
                   if not mine else mine[0]["detail"])
    # A panel that can never be empty becomes furniture, so this asks a date
    # past every session the suite made. Scoped to sessions with a minimum,
    # which -- because the migration defaults every existing one to 0 -- are
    # only ever sessions somebody deliberately set a number on.
    with m.app.test_request_context():
        far_off = m.owner_home_warnings(conn, today + timedelta(days=400))
    s.check("with no session short, the warning is absent",
            not [w for w in far_off if "short of the number to run" in w["title"]],
            detail=str([w["title"] for w in far_off][:3]))

    s.section("The page the warning links to says so too")
    # The owner's home page sends them here. If this list then shows nothing
    # about the shortfall, the link is a dead end and the finding is back to
    # being something counted by hand.
    #
    # Worth rendering explicitly: the route sweep opens this page every run
    # and never exercised this block, because no seeded session has a
    # minimum -- so it was green over markup that had never once rendered.
    page = oc.get("/admin/workshops")
    body = page.get_data(as_text=True)
    s.check("the sessions list opens", page.status_code == 200,
            detail=f"HTTP {page.status_code}")
    s.check("and names the shortfall", "of 6 needed" in body,
            detail="the page renders, which a route sweep would call a pass, "
                   "while saying nothing about the session that will not run")
    s.check("saying who has not paid rather than counting them",
            "without paying, not counted" in body)
    s.check("and pointing at the waiting list before a cancellation",
            "could cover it" in body,
            detail="telling the owner to cancel a session they could have "
                   "filled is the more expensive of the two mistakes")

    s.section("And becomes a task, which closes itself")
    found, _dropped = m.watch_task_findings(conn, today)
    ours = [f for f in found if f[0] == "workshop"]
    s.check("a workshop finding is raised", bool(ours),
            detail=f"kinds raised: {sorted({f[0] for f in found})}")
    s.check("naming the session and the numbers",
            any("of 6 needed" in f[1] for f in ours),
            detail=str([f[1] for f in ours][:2]))
    s.check("the note says the waiting list could cover it",
            any("enough to cover it" in f[2] for f in ours),
            detail="the sentence that stops the owner cancelling a session "
                   "they could have filled")

    # Fill it and the finding goes, without anybody ticking anything.
    _register(conn, soon, "Helene", "confirmed", 4)
    conn.commit()
    found_after, _d = m.watch_task_findings(conn, today)
    # Scoped to the session that was filled. The suite deliberately keeps a
    # second, overdue session of the same workshop alive for the check above,
    # and matching on the title alone would find that one instead -- passing
    # or failing on the wrong session.
    soon_start = (today + timedelta(days=24)).isoformat()
    still = [f for f in found_after
             if f[0] == "workshop" and soon_start in f[1]]
    s.check("one more booking and the task closes itself", not still,
            detail=str([f[1] for f in still]) +
                   " -- nothing in this set has a done action of its own, so "
                   "every run rebuilds the picture")

    s.section("An employee cannot set what a session needs")
    r = ec.post(f"/admin/workshops/{wid}/sessions/new",
                data={"start_date": (today + timedelta(days=50)).isoformat(),
                      "capacity": "8", "min_participants": "2"},
                follow_redirects=False)
    s.check("the form is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    # ---------------------------------------------------------------- cleanup
    conn.execute("DELETE FROM workshop_waitlist WHERE session_id IN "
                 "(SELECT id FROM workshop_sessions WHERE workshop_id = ?)", (wid,))
    conn.execute("DELETE FROM workshop_bookings WHERE session_id IN "
                 "(SELECT id FROM workshop_sessions WHERE workshop_id = ?)", (wid,))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id = ?", (wid,))
    conn.execute("DELETE FROM workshops WHERE id = ?", (wid,))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
