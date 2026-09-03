"""«When a session reads as full, it is full — we do not overbook a house this size.»

That sentence is on the public workshops page. It is a claim about what the
software does, and until this it was not one the software kept.

Rooms have claim_range() and a written account of the bug it exists for: a
bare SELECT takes no write lock, so two requests arriving together both read
"free" and both write, nothing errors, and the first anybody knows is two cars
in the drive. Workshops had exactly the same check-then-write and no lock at
all — so two people taking the last two places at the same instant both saw
room and both got one, on the page that promises otherwise.

WHAT THIS FILE ACTUALLY PROVES is the hard part. A race is not reproducible by
asking politely, so there are two halves and neither is sufficient alone:

  - the MECHANISM: the guest paths go through claim_workshop_places, which
    takes the write lock before counting, and the read-only paths do NOT —
    putting the whole public site behind whoever is mid-registration would be
    its own outage;
  - and the BEHAVIOUR under a real second connection: a registration is held
    open mid-transaction and a second connection is shown to block on its own
    write rather than count a world that does not include it.

Confirming a registration is deliberately NOT guarded. It writes and then says
"over the cap", which is a staff override with a warning — the same reasoning
as entering a booking by hand. The promise is about a GUEST being told there
is room and finding there was not, and that is the path that locks.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZFULL"


def _cleanup(conn):
    conn.execute(
        "DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute(
        "DELETE FROM workshop_sessions WHERE workshop_id IN "
        "(SELECT id FROM workshops WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("we do not overbook")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    oc, _ec, _owner, _emp = clients()
    _cleanup(conn)

    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                   default_capacity, active, created_at)
           VALUES (?, '', 100, 4, 1, ?)""", (TAG + " Indigo", now))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                   capacity, created_at)
           VALUES (?, ?, ?, 4, ?)""",
        (wid, (today + timedelta(days=40)).isoformat(),
         (today + timedelta(days=43)).isoformat(), now))
    sid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def register(ref, party, status="confirmed"):
        conn.execute(
            """INSERT INTO workshop_bookings (session_id, guest_name, guest_email,
                       party_size, status, reference_code, manage_token,
                       total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 100, ?)""",
            (sid, TAG + " " + ref, f"{TAG}.{ref}@example.invalid".lower(),
             party, status, TAG + ref, f"tok-{TAG}-{ref}".lower(), now))

    conn.commit()

    s.section("The page says it, so the code has to mean it")
    public = m.app.test_client().get("/workshops").get_data(as_text=True)
    s.check("the promise is really on the public page",
            "we do not overbook" in public.lower(),
            detail="if this sentence goes, this whole file is about copy that "
                   "no longer exists and should be reconsidered, not deleted "
                   "quietly")

    s.section("The guest paths take the lock; the reading ones do not")
    # The mechanism, checked in the source. A race cannot be reproduced by
    # asking politely, so this half says WHICH paths are joined to their write
    # and which are deliberately left free.
    import inspect
    for fn, why in (("workshop_register", "a guest taking a place"),
                    ("workshop_manage", "a guest moving to another date")):
        src = inspect.getsource(m.app.view_functions[fn])
        s.check(f"{fn} claims before it writes ({why})",
                "claim_workshop_places" in src,
                detail="check-then-write with nothing joining them is the bug "
                       "claim_range was written for")
    for fn, why in (("workshops_public", "the public list"),
                    ("admin_workshops", "the sessions list")):
        if fn not in m.app.view_functions:
            continue
        src = inspect.getsource(m.app.view_functions[fn])
        s.check(f"{fn} does NOT take the write lock ({why})",
                "claim_workshop_places" not in src,
                detail="putting every visitor behind whoever is mid-"
                       "registration would be its own outage")

    s.section("And confirming is a staff override, said out loud")
    src = inspect.getsource(m.app.view_functions["confirm_workshop_registration"])
    # Comments stripped: the route explains IN A COMMENT why it does not lock,
    # and reading that as code made this fail on the very thing it says.
    code = " ".join(line.split("#")[0] for line in src.splitlines())
    s.check("it does not lock", "claim_workshop_places" not in code)
    s.section("The count itself is right")
    # Every claim_ call is followed by a commit. That is not tidiness: the
    # helper takes a write lock and holds it until the CALLER commits, which
    # is its documented contract -- and forgetting it here left the lock held
    # so that the very next HTTP request in this file came back a 500. The
    # app's own callers all commit for the same reason.
    def places():
        n = m.claim_workshop_places(conn, sid)
        conn.commit()
        return n

    register("A", 3)
    conn.commit()
    s.check("three of four taken leaves one", places() == 1)
    register("B", 1, status="pending")
    conn.commit()
    s.check("a PENDING request counts against the room", places() == 0,
            detail="two rival requests for the last place is exactly the case "
                   "this is about")
    register("C", 2, status="cancelled")
    conn.commit()
    s.check("and a cancelled one does not", places() == 0,
            detail="a cancelled place that still counts is a session that "
                   "reads full when it is not")

    s.section("Confirming past the cap goes through, and says so")
    # Exercised, not read. The sentence is in the source whether or not the
    # branch that prints it can be reached, so looking for the words passed
    # with the warning switched off -- which is the failure it is about.
    #
    # After the counting section on purpose: putting this fixture first added
    # two places to the session and every count above came out wrong, which
    # the suite caught immediately.
    register("D", 2, status="pending")
    conn.commit()
    over = conn.execute(
        "SELECT id FROM workshop_bookings WHERE reference_code = ?",
        (TAG + "D",)).fetchone()["id"]
    r = oc.post(f"/admin/workshops/registrations/{over}/confirm",
                follow_redirects=True)
    page = r.get_data(as_text=True)
    s.check("it goes through",
            conn.execute("SELECT status FROM workshop_bookings WHERE id = ?",
                         (over,)).fetchone()["status"] == "confirmed",
            detail="the override is the point; it is not supposed to refuse")
    # "over the" and "cap" are both already on that page in its own copy, so
    # looking for them passed with the warning switched off. The phrase that
    # only the warning produces is the one to look for.
    s.check("and says so out loud",
            "Heads up" in page and "-spot cap" in page,
            detail="writing past the cap in silence would be the same failure "
                   "wearing a uniform")
    s.check("naming the figure it has reached",
            "now total 5" in page,
            detail="'over the cap' without the number is a warning somebody "
                   "has to go and check")

    s.section("A guest is refused the place that is not there")
    anon = m.app.test_client()
    r = anon.post(f"/workshops/register/{sid}",
                  data={"guest_name": TAG + " Late", "party_size": "1",
                        "guest_email": f"{TAG}.late@example.invalid".lower()},
                  follow_redirects=True)
    body = r.get_data(as_text=True)
    s.check("they are told it is full",
            "fully booked" in body.lower(),
            detail=f"HTTP {r.status_code}")
    s.check("and nothing was written",
            conn.execute(
                "SELECT COUNT(*) FROM workshop_bookings WHERE guest_name = ?",
                (TAG + " Late",)).fetchone()[0] == 0)

    s.section("A second connection really does wait its turn")
    # The behaviour half. Not a simulated race -- a real second connection,
    # shown to block on its own write while the first holds the lock, which
    # is the whole mechanism. Without the UPDATE in claim_workshop_places the
    # second connection reads straight through and the two disagree.
    import sqlite3
    conn.commit()                                # nothing of ours held
    held = db()
    held.execute("BEGIN")
    m.claim_workshop_places(held, sid)          # takes RESERVED

    other = db()
    other.execute("PRAGMA busy_timeout = 250")  # do not sit here for 5 seconds
    blocked = False
    try:
        other.execute("UPDATE booking_write_lock SET held_at = ? WHERE id = 1",
                      (now,))
    except sqlite3.OperationalError as e:
        blocked = "locked" in str(e).lower() or "busy" in str(e).lower()
    s.check("a second writer is made to wait", blocked,
            detail="if it is not, claim_workshop_places is not taking a write "
                   "lock and the guard is decoration")
    other.close()
    held.rollback()
    held.close()

    s.section("And the lock is the same one rooms use")
    # One lock, not two. Two locks would let a room booking and a workshop
    # registration proceed in parallel, which is fine -- but it would also
    # mean the next person to add a third path invents a third.
    room_src = inspect.getsource(m.claim_range)
    shop_src = inspect.getsource(m.claim_workshop_places)
    s.check("both write to booking_write_lock",
            "booking_write_lock" in room_src and "booking_write_lock" in shop_src)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
