"""What the house can manage, and what a guest needs from it.

There were four Access tickboxes on a room — ground floor, stairs only,
step-free, family friendly — and nothing in the app read any of them. Two of
the four appeared nowhere outside the constant that defined them. So a guest
rang, said their mother was eighty-eight and could not manage stairs, it went
into special_requests as prose, and which room she got was decided by whoever
remembered.

Four things this holds in place, and they are the four that would be
"simplified" away:

  - BLANK IS UNKNOWN, NOT ZERO. A room nobody has measured must not sort
    among the ground-floor ones and read as easy. That is the single wrong
    answer here that puts somebody in front of a staircase they cannot manage.
  - IT LIVES ON THE PERSON. Somebody who could not manage stairs in May
    cannot manage them in September. Putting it on the booking means asking
    them again every time, which is the thing this exists to stop.
  - WHICH MAKES IT HEALTH DATA, so it is cleared twelve months after their
    last stay, and templates/privacy.html says so. The notice is a set of
    claims about this code; the last section here checks them.
  - AND THE TASK DOES NOT QUOTE IT. A task title goes on the calendar, into
    a notification and onto whoever it is assigned to. The finding names the
    booking and the room and stops. Anyone who needs the rest opens a page
    that is the owner's.

It does not decide anything. Eleven steps may be perfectly fine for a
particular person and only they know; the job is to put two facts side by
side early enough for somebody to pick up the telephone.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZACCESS"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("what the house can manage")
    today = house_today()
    now = m.datetime.now(m.timezone.utc)
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)

    def add_room(name, steps, **kw):
        conn.execute(
            """INSERT INTO rooms (name, description, max_occupancy, max_adults,
                       price_per_night, active, sort_order, export_token,
                       access_steps, access_car_metres, access_bathroom,
                       access_notes)
               VALUES (?, '', 2, 2, 200, 1, 90, ?, ?, ?, ?, ?)""",
            (TAG + " " + name, f"tok-{TAG}-{name}".lower(), steps,
             kw.get("car"), kw.get("bathroom"), kw.get("notes")))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    ground = add_room("Orangerie", 0, car=15, bathroom="step_free",
                      notes="Through the garden door, level all the way.")
    tower = add_room("Tower", 62, car=90, bathroom="over_bath",
                     notes="Sixty-two steps, worn stone, a rope not a rail.")
    unmeasured = add_room("Blue Room", None)
    conn.commit()

    rooms = {r["room"]["name"]: r for r in m.room_access(conn)}

    s.section("A room nobody has measured is unknown, not easy")
    blue = rooms[TAG + " Blue Room"]
    s.check("it is not treated as having a number", not blue["known"])
    s.check("and it says what nobody has looked at",
            set(blue["unmeasured"]) == {"steps to it", "how far a car can get",
                                        "the bathroom"},
            detail=str(blue["unmeasured"]))
    order = [r["room"]["name"] for r in m.room_access(conn)
             if r["room"]["name"].startswith(TAG)]
    s.check("and it sorts LAST, not first",
            order.index(TAG + " Blue Room") == len(order) - 1,
            detail=" | ".join(order) + " — a COALESCE(steps, 0) would put it "
                                       "at the top reading as step-free")

    s.section("The rest are easiest first")
    s.check("the orangerie comes before the tower",
            order.index(TAG + " Orangerie") < order.index(TAG + " Tower"),
            detail=" | ".join(order))
    s.check("a measured step-free room has nothing missing",
            not rooms[TAG + " Orangerie"]["unmeasured"])
    s.check("the bathroom reads in English, not as a key",
            rooms[TAG + " Tower"]["bathroom"] == "Shower over the bath",
            detail=str(rooms[TAG + " Tower"]["bathroom"]))

    # ------------------------------------------------------------- guests
    def add_guest(name, needs, email=None):
        conn.execute(
            """INSERT INTO guests (name, email, access_needs,
                       access_needs_updated_at, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (TAG + " " + name, email, needs,
             now.isoformat() if needs else None, now.isoformat()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def add_booking(ref, room_id, arrival, *, guest_id=None, email=None,
                    guest_name="Someone", status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (reference_code, manage_token, guest_name,
                       guest_email, room_id, linked_guest_id, arrival_date,
                       departure_date, party_size, status, total_price,
                       created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, 400, ?)""",
            (TAG + ref, f"tok-{TAG}-{ref}".lower(), TAG + " " + guest_name,
             email or f"{TAG}.{ref}@example.invalid".lower(), room_id, guest_id,
             arrival.isoformat(), (arrival + timedelta(days=2)).isoformat(),
             status, now.isoformat()))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    linked = add_guest("Odile", "Cannot manage more than a few steps.")
    add_guest("Margot", "Uses a stick on stairs.",
              email="zzaccess.margot@example.invalid")
    no_needs = add_guest("Pierre", None)

    soon = today + timedelta(days=10)
    add_booking("TOWER", tower, soon, guest_id=linked, guest_name="Odile")
    add_booking("GROUND", ground, soon, guest_id=linked, guest_name="Odile")
    add_booking("BLUE", unmeasured, soon, guest_id=linked, guest_name="Odile")
    # No profile link at all — the booking was taken before the profile was
    # made. Same person, and dropping her is the failure the page exists for.
    add_booking("EMAIL", tower, soon, email="ZZAccess.Margot@Example.Invalid",
                guest_name="Margot")
    add_booking("QUIET", tower, soon, guest_id=no_needs, guest_name="Pierre")
    add_booking("PAST", tower, today - timedelta(days=30), guest_id=linked,
                guest_name="Odile")
    conn.commit()

    checks = {c["booking"]["reference_code"]: c
              for c in m.access_to_check(conn, days=90, today=today)}

    s.section("Which bookings are worth a telephone call")
    s.check("a room with steps is raised", TAG + "TOWER" in checks)
    s.check("with the number of steps in the reason",
            "62 steps" in checks.get(TAG + "TOWER", {}).get("why", ""),
            detail=checks.get(TAG + "TOWER", {}).get("why"))
    s.check("a measured step-free room is NOT raised", TAG + "GROUND" not in checks,
            detail="raising it would make the list noise, and a list that is "
                   "noise is one nobody reads on the morning it matters")
    s.check("a room nobody has measured IS raised", TAG + "BLUE" in checks,
            detail="'we do not know what this room asks' is a reason to "
                   "check, not a reason to stay quiet")
    s.check("and says that is why",
            "nobody has measured" in checks.get(TAG + "BLUE", {}).get("why", ""),
            detail=checks.get(TAG + "BLUE", {}).get("why"))
    # There is deliberately no "not allocated yet" case. bookings.room_id is
    # NOT NULL, so every booking has a room from the moment it exists -- I
    # wrote a branch for that state anyway, and the fixture meant to exercise
    # it would not insert. Both are gone.
    s.check("every booking has a room, so there is no unallocated case",
            "NOT NULL" in conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'bookings'"
            ).fetchone()[0].split("room_id")[1].split(",")[0],
            detail="if room_id ever becomes nullable, access_to_check needs a "
                   "branch for it and this check is the reminder")

    s.section("A guest with no profile link is still found")
    s.check("matched on the email address", TAG + "EMAIL" in checks,
            detail="the booking was taken before the profile existed; it is "
                   "the same person and the same staircase")

    s.section("And nobody else is dragged in")
    s.check("a guest who has told us nothing is left alone",
            TAG + "QUIET" not in checks)
    s.check("and a stay that already happened is not raised",
            TAG + "PAST" not in checks,
            detail="ringing somebody about a staircase they climbed last "
                   "month helps nobody")

    # ------------------------------------------------------ the retention
    s.section("It is forgotten once they stop coming")
    stale = add_guest("Hortense", "Cannot do stairs at all.")
    conn.commit()
    m.purge_stale_access_needs(conn, today=today)
    row = conn.execute("SELECT access_needs, access_needs_updated_at "
                       "FROM guests WHERE id = ?", (stale,)).fetchone()
    s.check("a guest with no stay at all is cleared", row["access_needs"] is None)
    s.check("and the date it was told to us goes with it",
            row["access_needs_updated_at"] is None,
            detail="a date left on an emptied field is still a record that "
                   "somebody told us something, which is the fact we said we "
                   "would stop holding")

    s.section("But not while they are still a guest")
    m.purge_stale_access_needs(conn, today=today)
    s.check("somebody with a booking ahead keeps theirs",
            conn.execute("SELECT access_needs FROM guests WHERE id = ?",
                         (linked,)).fetchone()["access_needs"] is not None,
            detail="forgetting it the week before they arrive is worse than "
                   "never having asked")

    recent = add_guest("Cecile", "Slowly, and not the tower.")
    add_booking("RECENT", ground, today - timedelta(days=60), guest_id=recent,
                guest_name="Cecile")
    conn.commit()
    m.purge_stale_access_needs(conn, today=today)
    s.check("and so does somebody who stayed two months ago",
            conn.execute("SELECT access_needs FROM guests WHERE id = ?",
                         (recent,)).fetchone()["access_needs"] is not None)

    # Thirteen months on, with nothing since.
    m.purge_stale_access_needs(conn, today=today + timedelta(days=400))
    s.check("thirteen months later, with nothing since, it goes",
            conn.execute("SELECT access_needs FROM guests WHERE id = ?",
                         (recent,)).fetchone()["access_needs"] is None,
            detail="twelve months is what the notice promises")

    s.section("A cancelled booking is not a reason to keep it")
    gone = add_guest("Amelie", "A ground-floor room, please.")
    add_booking("CANCEL", ground, today + timedelta(days=5), guest_id=gone,
                guest_name="Amelie", status="cancelled")
    conn.commit()
    m.purge_stale_access_needs(conn, today=today)
    s.check("it is cleared",
            conn.execute("SELECT access_needs FROM guests WHERE id = ?",
                         (gone,)).fetchone()["access_needs"] is None,
            detail="a cancelled stay is not a relationship, and holding "
                   "health information on the strength of one is exactly "
                   "what the notice says we do not do")

    s.section("The daily pass actually runs it")
    # The retention promise is kept by one function, so the notice can be
    # checked against one function. A purge nothing calls is a promise
    # nothing keeps.
    import inspect
    src = inspect.getsource(m.run_health_notes_purge_job)
    s.check("run_health_notes_purge_job calls it",
            "purge_stale_access_needs" in src)

    # ------------------------------------------------------------- pages
    conn.execute("UPDATE guests SET access_needs = ?, access_needs_updated_at = ? "
                 "WHERE id = ?",
                 ("Cannot manage more than a few steps.", now.isoformat(), linked))
    conn.commit()

    s.section("The page")
    r = oc.get("/admin/room-access")
    body = r.get_data(as_text=True)
    s.check("the owner can open it", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("the rooms are on it", TAG + " Tower" in body)
    s.check("what the guest said is on it",
            "Cannot manage more than a few steps." in body)
    s.check("and the bookings worth a call", TAG + "TOWER" in body)
    r = ec.get("/admin/room-access", follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code} — it holds health information "
                   "about named guests")

    s.section("It is reachable")
    nav = oc.get("/").get_data(as_text=True)
    nav = nav[:nav.find("</nav>")] if "</nav>" in nav else nav
    s.check("linked from the nav", '/admin/room-access' in nav)
    s.check("and searchable",
            "room_access_page" in {e for _l, e, _k in m.PALETTE_PAGES})

    s.section("Recording it where the conversation happens")
    form = oc.get(f"/guests/{linked}/edit").get_data(as_text=True)
    s.check("the guest's own profile has the field",
            'name="access_needs"' in form,
            detail="a page under Rooms is not where somebody is when a guest "
                   "says it on the telephone")
    oc.post(f"/guests/{linked}/edit",
            data={"name": TAG + " Odile", "access_needs": "A few steps at most."},
            follow_redirects=True)
    after = conn.execute("SELECT access_needs, access_needs_updated_at "
                         "FROM guests WHERE id = ?", (linked,)).fetchone()
    s.check("editing it saves", after["access_needs"] == "A few steps at most.",
            detail=str(after["access_needs"]))
    s.check("and stamps when", after["access_needs_updated_at"] is not None)

    s.section("And the create form does not throw it away")
    # guest_form.html serves both. A field added to the template and to the
    # edit handler only is a box somebody types into on a new profile and
    # which vanishes without a word -- and the one thing nobody would ever
    # find out is that it happened, because the guest already told you.
    oc.post("/guests/new",
            data={"name": TAG + " Sylvie", "confirm_duplicate": "1",
                  "access_needs": "Ground floor only."},
            follow_redirects=True)
    made = conn.execute("SELECT access_needs FROM guests WHERE name = ?",
                        (TAG + " Sylvie",)).fetchone()
    s.check("a new profile keeps what was typed",
            made is not None and made["access_needs"] == "Ground floor only.",
            detail=str(made["access_needs"]) if made else "no profile created")

    s.section("Clearing it clears the date with it")
    oc.post(f"/guests/{linked}/edit",
            data={"name": TAG + " Odile", "access_needs": "  "},
            follow_redirects=True)
    cleared = conn.execute("SELECT access_needs, access_needs_updated_at "
                           "FROM guests WHERE id = ?", (linked,)).fetchone()
    s.check("the words go", cleared["access_needs"] is None)
    s.check("and the date", cleared["access_needs_updated_at"] is None)

    # ------------------------------------------------- the task says less
    s.section("The task that comes off this does not quote it")
    conn.execute("UPDATE guests SET access_needs = ? WHERE id = ?",
                 ("Cannot manage more than a few steps.", linked))
    conn.commit()
    # watch_task_findings returns (found, dropped) -- the second half is the
    # count it had to cap, which is a different thing from the findings.
    raised, _dropped = m.watch_task_findings(conn, today=today)
    findings = [f for f in raised if f[0] == "access"]
    s.check("a finding is raised", bool(findings),
            detail=f"{len(findings)} access findings")
    text = " ".join(str(part) for f in findings for part in f)
    s.check("the booking is named", TAG + "TOWER" in text or TAG in text)
    s.check("and what the guest said is NOT in it",
            "Cannot manage more than a few steps." not in text,
            detail="a task title goes on the calendar, into a notification "
                   "and onto whoever it is assigned to. This is the least "
                   "private place in the app.")
    s.check("nor any of their own words",
            "stick" not in text.lower(),
            detail="the other guest in this suite uses a stick on stairs")
    s.check("it is a registered kind, so it can be routed and turned off",
            "access" in m.WATCH_TASK_KINDS)

    s.section("The notice says what the code does")
    # templates/privacy.html is a set of claims about this software. Holding
    # health information the notice does not mention is worse than not
    # holding it.
    notice = oc.get("/privacy").get_data(as_text=True)
    # Both halves, not an `or`. The first version of this passed with the
    # heading torn out, because the sentence after it still happened to
    # contain one of the two phrases -- so it was checking that SOME words
    # survived rather than that the claim did.
    s.check("the notice has a heading for it",
            "What you need from the house" in notice,
            detail="we now keep health-adjacent information against a name, "
                   "and a retention list that does not mention it is a "
                   "notice that understates what the software does")
    s.check("and says what it is",
            "getting around" in notice and "cannot manage many steps" in notice,
            detail="in the guest's language, not ours")
    s.check("and says how long", "twelve months after your last stay" in notice,
            detail="which is what purge_stale_access_needs actually does")
    s.check("and that they can ask sooner",
            "forget it sooner" in notice)
    s.check("the retention window in the code matches the number in the notice",
            m.ACCESS_NEEDS_RETENTION_MONTHS == 12,
            detail=str(m.ACCESS_NEEDS_RETENTION_MONTHS))

    _cleanup(conn)
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
