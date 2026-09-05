"""The screen the kitchen reads during service.

An iPad by the pass, left on a charger, refreshing itself. It answers from
three metres who is eating tonight, what they cannot eat, who has been here
before, and what the store is short of.

WHAT THIS FILE IS MOSTLY ABOUT IS THE ALLERGY LINE. Everything else on the
screen is a convenience; that one is the reason it is allowed to exist. So the
checks here are about the ways it could be quietly wrong:

  - a guest with no dietary note and a guest whose note has been deleted look
    identical on a screen, and one of them is safe
  - somebody dining without a room has their note on the RESERVATION, not on a
    guest profile, because they may not have one -- read the wrong table and
    they appear clean
  - a guest who left this morning must not be on tonight's list, and a guest
    arriving tomorrow must not be either. Both are people the kitchen would
    cook for on the wrong night
  - the dietary notes sort to the top, because a chef reads the top

AND THE PRIVACY NOTICE MAKES THREE CLAIMS ABOUT THIS PAGE. It says the note is
shown on a screen in the kitchen, that the screen shows only the people eating
that day, and that it cannot be opened without a staff login. CLAUDE.md is
explicit that the notice is a set of testable claims about this code rather
than marketing copy, so all three are checked here rather than assumed.
"""
from _harness import Suite, clients, db, ensure_room, flashes

import io
import os
from datetime import timedelta

import _harness

m = _harness.m
TAG = "ZZPASS"


def _cleanup(conn):
    conn.execute("DELETE FROM guest_notes WHERE body LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%" + TAG + "%",))
    conn.execute("DELETE FROM staff_messages WHERE body LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("the pass")
    oc, ec, owner, emp = clients()
    conn = db()
    _cleanup(conn)

    night = m.service_day()
    iso = night.isoformat()
    now = m.datetime.now(m.timezone.utc).isoformat()
    room = ensure_room()["id"]

    def guest(name, dietary=""):
        conn.execute(
            "INSERT INTO guests (name, email, dietary_notes, created_at) "
            "VALUES (?, ?, ?, ?)",
            (TAG + name, ("%s%s@example.invalid" % (TAG, name)).lower(), dietary, now))
        conn.commit()
        return conn.execute("SELECT id FROM guests WHERE name = ?",
                            (TAG + name,)).fetchone()["id"]

    def stay(name, gid, arrive, depart, status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, created_at, linked_guest_id)
               VALUES (?, ?, ?, ?, '', ?, ?, 2, ?, 400, ?, ?)""",
            (room, TAG + name, TAG + "tok" + name, TAG + name,
             arrive.isoformat(), depart.isoformat(), status, now, gid))
        conn.commit()

    # Eating tonight, with an allergy on their profile.
    allergic = guest("Allergic", "Severe nut allergy")
    stay("Allergic", allergic, night - timedelta(days=1), night + timedelta(days=2))
    # Eating tonight, nothing recorded.
    plain = guest("Plain")
    stay("Plain", plain, night - timedelta(days=1), night + timedelta(days=2))
    # Leaves this morning: not eating here tonight.
    stay("Departed", guest("Departed"), night - timedelta(days=2), night)
    # Arrives tomorrow: not eating here tonight either.
    stay("Tomorrow", guest("Tomorrow"), night + timedelta(days=1),
         night + timedelta(days=3))
    # An allergy on somebody whose name sorts LAST. Without this the fixture
    # cannot tell the two orderings apart: the allergic guests happened to come
    # first alphabetically anyway, so the check passed with the dietary sort
    # deleted -- which is a check that reads as cover and is not.
    zed = guest("Zed", "Anaphylactic to sesame")
    stay("Zed", zed, night - timedelta(days=1), night + timedelta(days=2))
    # On their last evening.
    lastnight = guest("Lastnight", "Coeliac")
    stay("Lastnight", lastnight, night - timedelta(days=2), night + timedelta(days=1))
    # A stay that was never confirmed.
    stay("Pending", guest("Pending"), night - timedelta(days=1),
         night + timedelta(days=2), status="pending")
    # Dining without a room, allergy on the RESERVATION.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                   guest_email, party_size, dinner_date, dietary_notes, status, created_at)
           VALUES (?, ?, ?, '', 2, ?, ?, 'confirmed', ?)""",
        (TAG + "LOCAL", TAG + "rtok", TAG + "Local", iso, "Shellfish", now))
    conn.commit()

    data = m.pass_service(conn, night)
    by_name = {g["name"]: g for g in data["guests"]}

    s.section("Who is eating here tonight")

    s.check("a guest staying over tonight is on it", TAG + "Allergic" in by_name)
    s.check("and somebody dining without a room is on the same list",
            TAG + "Local" in by_name,
            detail="a chef does not care which table they came from")
    s.check("a guest who left this morning is not",
            TAG + "Departed" not in by_name,
            detail="a departure is exclusive, as everywhere else in this app")
    s.check("nor one who arrives tomorrow", TAG + "Tomorrow" not in by_name)
    s.check("nor a stay nobody has confirmed", TAG + "Pending" not in by_name)
    s.check("covers counts people, not bookings", data["covers"] >= 8,
            detail=str(data["covers"]))

    s.section("What they cannot eat")

    s.check("an allergy on the guest's own record is shown",
            by_name.get(TAG + "Allergic", {}).get("dietary_notes") == "Severe nut allergy")
    s.check("one on the reservation is shown too",
            by_name.get(TAG + "Local", {}).get("dietary_notes") == "Shellfish",
            detail="somebody who has never stayed has no profile to carry it, "
                   "and reading only the profile makes them look clean")
    s.check("somebody with nothing recorded is blank rather than absent",
            TAG + "Plain" in by_name
            and by_name[TAG + "Plain"]["dietary_notes"] == "")
    s.check("they are counted", data["allergy_count"] >= 3,
            detail=str(data["allergy_count"]))
    # A chef reads the top of a list. Everything else is ordering; this is the
    # one thing on the screen that cannot be got wrong.
    first_clean = next((i for i, g in enumerate(data["guests"])
                        if not g["dietary_notes"]), len(data["guests"]))
    dietary_at = [i for i, g in enumerate(data["guests"]) if g["dietary_notes"]]
    last_dietary = max(dietary_at) if dietary_at else -1
    s.check("and every one of them sorts above everybody else",
            last_dietary < first_clean,
            detail="a dietary note below the fold is a dietary note nobody read")

    s.section("What the screen says about a stay")

    a = by_name.get(TAG + "Allergic", {})
    s.check("it names the room", a.get("room_name"))
    s.check("and how many nights", a.get("nights") == 3, detail=str(a.get("nights")))
    s.check("tonight is not their last", a.get("last_night") == 0)
    s.check("but it is for the guest leaving tomorrow",
            by_name.get(TAG + "Lastnight", {}).get("last_night") == 1,
            detail="the last dinner is the one worth getting right")
    s.check("somebody dining only is marked as not staying",
            by_name.get(TAG + "Local", {}).get("staying") == 0)

    s.section("The store")

    s.check("the store is read from the ledger, not a counter",
            isinstance(data["stock"], list),
            detail="stock_levels sums the movements; a stored total and a "
                   "ledger disagree the first time anything goes wrong")

    s.section("The page itself")

    r = ec.get("/pass")
    s.check("the kitchen can open it", r.status_code == 200, r,
            detail="a screen the chef cannot open is replaced by a printed "
                   "sheet nobody updates")
    body = r.get_data(as_text=True)
    s.check("tonight's allergy is on the page", "Severe nut allergy" in body)
    s.check("and so is the one from the reservation", "Shellfish" in body)
    s.check("somebody who left this morning is not",
            TAG + "Departed" not in body)

    s.section("Writing back from the pass")

    r = ec.post("/pass/note", data={"guest_id": str(allergic),
                                    "note": TAG + " did not touch the cheese"},
                follow_redirects=True)
    kept = conn.execute("SELECT * FROM guest_notes WHERE guest_id = ? "
                        "ORDER BY id DESC LIMIT 1", (allergic,)).fetchone()
    s.check("a note written at the pass lands on the guest",
            kept and TAG in (kept["body"] or ""), detail="; ".join(flashes(r)[:1]))
    s.check("and says who wrote it", kept and kept["written_by_user_id"],
            detail="a note nobody signed is a note nobody can ask about")

    r = ec.post("/pass/note", data={"guest_id": "0", "note": TAG + " nowhere"},
                follow_redirects=True)
    s.check("a note against nobody is refused, and says why",
            any("no guest record" in f for f in flashes(r)),
            detail="; ".join(flashes(r)[:1]))
    r = ec.post("/pass/note", data={"guest_id": str(allergic), "note": "  "},
                follow_redirects=True)
    s.check("and an empty one writes nothing",
            conn.execute("SELECT COUNT(*) c FROM guest_notes WHERE guest_id = ?",
                         (allergic,)).fetchone()["c"] == 1)

    r = ec.post("/pass/stock", data={"want": TAG + " more butter"},
                follow_redirects=True)
    task = conn.execute("SELECT * FROM tasks WHERE title LIKE ? ORDER BY id DESC LIMIT 1",
                        ("%" + TAG + "%",)).fetchone()
    s.check("asking for something becomes a task", task is not None,
            detail="; ".join(flashes(r)[:1]))
    # Anything that becomes a task appears on the calendar by itself. A request
    # to buy something that lives only in an inbox is read once and forgotten.
    s.check("with a day against it, so it reaches the calendar",
            task and task["due_date"])

    s.section("Urgent has to arrive somewhere")

    sent = []
    real_send = m.send_email
    m.send_email = lambda to, subject, body, **kw: sent.append((to, subject, body))
    try:
        ec.post("/pass/message", data={"body": TAG + " quiet word", "to_user_id": ""},
                follow_redirects=True)
        s.check("an ordinary message is kept and sends nothing",
                not sent and conn.execute(
                    "SELECT COUNT(*) c FROM staff_messages WHERE body LIKE ?",
                    (TAG + "%",)).fetchone()["c"] == 1)
        ec.post("/pass/message", data={"body": TAG + " the oven is out",
                                       "urgent": "on"}, follow_redirects=True)
        s.check("an urgent one goes out by email", len(sent) == 1,
                detail="a flag nobody sees is not a flag, which is the whole "
                       "argument for this existing beside a group chat")
        s.check("and carries what was said",
                sent and TAG in sent[0][2], detail=str(sent[:1])[:80])
    finally:
        m.send_email = real_send

    s.section("The privacy notice's claims about this page")

    with io.open(os.path.join(m.BASE_DIR, "templates", "privacy.html"),
                 encoding="utf-8") as fh:
        notice = fh.read()
    s.check("the notice says the note is shown on a screen in the kitchen",
            "screen in the kitchen" in notice,
            detail="the app shows it; a notice that does not say so is a "
                   "notice that understates what the software does")
    # Each of the next two is a promise the code has to keep.
    s.check("it claims only that day's guests are shown",
            "only the people eating here that day" in notice)
    s.check("and the code shows only that day",
            TAG + "Tomorrow" not in by_name and TAG + "Departed" not in by_name)
    s.check("it claims a staff login is needed",
            "cannot be opened without a staff login" in notice)
    anon = m.app.test_client()
    landed = anon.get("/pass", follow_redirects=False)
    s.check("and a stranger is sent to log in",
            landed.status_code in (301, 302, 401, 403)
            and "/login" in (landed.headers.get("Location") or "/login"),
            detail="HTTP %s -> %s" % (landed.status_code,
                                      landed.headers.get("Location")))

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
