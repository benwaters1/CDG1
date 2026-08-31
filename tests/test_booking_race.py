"""Two people cannot be given the same room at the same moment.

Every path that books a room asked whether the dates were free and then wrote,
and nothing joined the two steps. A bare SELECT takes no write lock, so two
requests arriving together both read "free" and both write. Nothing errors,
nothing is logged, and the page renders perfectly for both of them. The first
anybody knows is two cars in the drive.

CONFIRMING IS THE DANGEROUS ONE, not the public form. Two rival pending
requests for the same nights are normal — nothing stops two people asking —
so confirm deliberately passes include_pending=False, because a sibling
pending is not a conflict until something is actually confirmed. Which means
each of two simultaneous confirms looks at the other, sees a pending it is
entitled to ignore, and proceeds. The one check written specifically to
prevent double-booking was the one that could not see the other half of it.

WHY THIS TEST USES THREADS. A test that calls the two confirms one after the
other passes whether or not the lock exists, because the first has committed
before the second reads. That is not the failure. The failure needs both
inside the window, so both run against a real second connection with a
barrier holding them until they are ready. Anything less is a test of
sequential code that reads like a test of concurrent code — which is worse
than no test, because it looks like cover.

The negative control for this file is not subtle: remove claim_range's UPDATE
and the second confirm succeeds.
"""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTRACE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _free_week(conn, room_id):
    """Dates nothing else in the database is using.

    Chosen rather than guessed. A fixed offset passes alone and fails in a
    full run the moment another suite leaves a stay on those nights, which
    has now happened four times in this suite's short history.
    """
    today = datetime.now(m.LOCAL_TZ).date()
    for n in range(400, 900):
        start = today + timedelta(days=n)
        end = start + timedelta(days=2)
        clash = conn.execute(
            """SELECT 1 FROM bookings WHERE room_id = ? AND status IN ('pending','confirmed')
               AND arrival_date < ? AND departure_date > ?""",
            (room_id, end.isoformat(), start.isoformat())).fetchone()
        if not clash:
            return start, end
    return None, None


def _pending(conn, room_id, ref, start, end):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'pending', 400, ?)""",
        (room_id, TAG + ref, TAG.lower() + "tok" + ref, f"Guest {ref}",
         f"{TAG.lower()}{ref.lower()}@example.invalid",
         start.isoformat(), end.isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                        (TAG + ref,)).fetchone()["id"]


def _confirmed(room_id, start, end):
    conn = db()
    try:
        return conn.execute(
            """SELECT reference_code FROM bookings
               WHERE room_id = ? AND status = 'confirmed'
                 AND arrival_date < ? AND departure_date > ?
                 AND reference_code LIKE ?""",
            (room_id, end.isoformat(), start.isoformat(), TAG + "%")).fetchall()
    finally:
        conn.close()


def _race(ids):
    """Confirm both bookings from two threads, released together.

    Each thread opens its OWN connection, because two threads sharing one
    connection would serialise in Python and prove nothing about SQLite.
    """
    ready = threading.Barrier(len(ids))
    results = {}

    def go(booking_id):
        conn = m.get_db()
        try:
            ready.wait(timeout=10)
            with m.app.test_request_context():
                ok, reason = m.confirm_booking_by_id(conn, booking_id)
            results[booking_id] = (ok, reason)
        except Exception as exc:                      # noqa: BLE001 - reported
            results[booking_id] = (None, f"{type(exc).__name__}: {exc}")
        finally:
            try:
                conn.commit()
            finally:
                conn.close()

    threads = [threading.Thread(target=go, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def run():
    s = Suite("The same room, twice, at once")
    _cleanup()
    oc, _ec, _owner, _emp = clients()

    conn = db()
    room = conn.execute("SELECT id, name FROM rooms LIMIT 1").fetchone()
    start, end = _free_week(conn, room["id"])
    conn.close()

    s.section("Two people ask for the same nights")
    s.check("there is a free stretch to race for", start is not None)
    if start is None:
        return s

    conn = db()
    first = _pending(conn, room["id"], "ONE", start, end)
    second = _pending(conn, room["id"], "TWO", start, end)
    conn.close()
    s.check("both requests exist, which is allowed", first and second,
            detail="nothing stops two people asking for the same nights")
    s.check("neither is confirmed yet", len(_confirmed(room["id"], start, end)) == 0)

    s.section("Confirmed at the same instant, one of them loses")
    results = _race([first, second])
    s.check("both attempts finished", len(results) == 2, detail=str(results))
    won = [b for b, (ok, _r) in results.items() if ok is True]
    crashed = [r for _b, (ok, r) in results.items() if ok is None]
    s.check("neither attempt crashed", not crashed, detail=str(crashed))
    # The point. Not "an error was raised" — the room is let once.
    s.check("exactly one of them is confirmed", len(won) == 1,
            detail=f"{len(won)} confirmed: {results}")
    held = _confirmed(room["id"], start, end)
    s.check("and the database agrees there is one booking on those nights",
            len(held) == 1,
            detail=f"{[h['reference_code'] for h in held]} — this is the check that "
                   "fails when the lock is removed")

    s.section("The one that lost is told why, and is still a live request")
    lost = [(b, r) for b, (ok, r) in results.items() if ok is False]
    s.check("it is refused with a reason", lost and lost[0][1],
            detail=str(lost))
    s.check("the reason is about the dates, not a database error",
            lost and "overlap" in (lost[0][1] or "").lower(),
            detail=str(lost[0][1]) if lost else "")
    conn = db()
    still = conn.execute(
        "SELECT status FROM bookings WHERE id = ?", (lost[0][0],)).fetchone() if lost else None
    conn.close()
    s.check("and it is left pending rather than quietly cancelled",
            still and still["status"] == "pending",
            detail=str(dict(still)) if still else "",
            )

    s.section("Which side of the line every caller is on")
    # Read out of the source rather than named here, because the failure this
    # guards is somebody adding an EIGHTH place that books a room and asking
    # the unlocked question in it. A list in this file would be a list of the
    # seven that were already right.
    #
    # It cuts the other way too. A display page that took the write lock would
    # put the whole site behind whoever is mid-booking, which is a worse bug
    # than the one being fixed and a much easier one to introduce by accident.
    import ast as _ast
    import os as _os

    def _functions(tree):
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                called = {c.func.id for c in _ast.walk(node)
                          if isinstance(c, _ast.Call) and isinstance(c.func, _ast.Name)}
                yield node.name, called

    app_src = open(_os.path.join(_harness.ROOT, "app.py"), encoding="utf-8").read()
    tree = _ast.parse(app_src)
    locks, asks = set(), set()
    for name, called in _functions(tree):
        if name == "claim_range":
            continue                       # it is the one that wraps the other
        if "claim_range" in called:
            locks.add(name)
        if "is_range_available" in called:
            asks.add(name)

    # A function that WRITES a booking after asking. Recognised by what it does
    # rather than by being on a list: it decides on a room and then inserts or
    # updates a booking row.
    def _writes_bookings(name):
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name == name:
                body = _ast.get_source_segment(app_src, node) or ""
                low = body.lower()
                return ("insert into bookings" in low
                        or "update bookings" in low
                        or "create_booking(" in body
                        or "confirm_booking_by_id" in body)
        return False

    s.check("the writing paths take the lock", bool(locks),
            detail=", ".join(sorted(locks)))
    unlocked_writers = sorted(n for n in asks if _writes_bookings(n))
    s.check("and no path that writes a booking asks without it",
            not unlocked_writers,
            detail=("these decide on a room and then write, without the lock: "
                    + ", ".join(unlocked_writers)) if unlocked_writers else "")

    readers = sorted(asks - locks)
    s.check("the pages that only show availability are still lock-free",
            bool(readers), detail=", ".join(readers))
    s.check("and none of them takes it", not (set(readers) & locks),
            detail="a page that only displays whether a room is free must not "
                   "serialise the site behind whoever is mid-booking")

    s.section("The lock is a write, because a read takes no lock at all")
    # If this ever becomes a SELECT the whole file above still passes in a
    # single-threaded run and protects nothing in production.
    claim = next((n for n in _ast.walk(tree)
                  if isinstance(n, _ast.FunctionDef) and n.name == "claim_range"), None)
    claim_src = (_ast.get_source_segment(app_src, claim) or "") if claim else ""
    s.check("claim_range exists", claim is not None)
    s.check("and it writes before it reads",
            "UPDATE booking_write_lock" in claim_src,
            detail="a SELECT takes no write lock in SQLite, so a read-only "
                   "version of this function would be decoration")
    conn = db()
    lock_row = conn.execute("SELECT COUNT(*) AS c FROM booking_write_lock").fetchone()["c"]
    conn.close()
    s.check("and the row it writes to actually exists", lock_row == 1,
            detail=f"{lock_row} rows — with none, the UPDATE touches nothing "
                   "and takes no lock")

    s.section("A room nobody else wants is unaffected")
    conn = db()
    other = conn.execute("SELECT id FROM rooms WHERE id != ? LIMIT 1",
                         (room["id"],)).fetchone()
    conn.close()
    if other:
        conn = db()
        o_start, o_end = _free_week(conn, other["id"])
        solo = _pending(conn, other["id"], "SOLO", o_start, o_end)
        conn.close()
        conn = db()
        with m.app.test_request_context():
            ok, reason = m.confirm_booking_by_id(conn, solo)
        conn.commit()
        conn.close()
        s.check("an uncontested booking still confirms normally", ok is True,
                detail=str(reason))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
