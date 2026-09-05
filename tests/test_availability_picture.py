# -*- coding: utf-8 -*-
"""Availability read once per request, and never once too often.

is_range_available asked six questions per call. Three of them took no
parameters at all — which workshops are running, which events are confirmed,
which dates are provisionally held — so the answer was identical every time
it was asked. next_free_nights calls the function once per room per day
across the horizon, so the public booking page asked those three questions
seventy times each and got the same answer seventy times: 476 queries, six
of them different.

It now builds the picture once and holds it on `g` for the life of the
request. That is a correctness decision as much as a speed one, and it cuts
both ways:

  IT MUST STILL REFUSE EVERY BLOCKER. Six things close a date and each has
  its own sentence, because "not available" sends whoever is at the desk
  hunting for a booking that does not exist. All six are checked below
  through the real function, including the two flags — exclude_booking_id,
  which lets a booking be moved without colliding with itself, and
  include_pending, which must be off at confirm time or two rival requests
  for the same dates can never be resolved.

  AND THE WRITE PATH MUST NOT SEE IT. claim_range takes the
  booking lock and re-asks. If it were answered from a picture built earlier
  in the same request — before the lock — two guests could pass the same
  check and get the same room. That is the failure this cache could have
  introduced, so it is the one checked hardest: a booking written directly
  into the database mid-request is invisible to the cached picture and MUST
  be visible to the locked one.
"""
from datetime import timedelta

from _harness import Suite, db
import _harness

m = _harness.m
TAG = "availpic-"


def _cleanup(conn):
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM rooms WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id NOT IN "
                 "(SELECT id FROM workshops)")
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?",
                 (TAG + "%",))
    conn.execute("DELETE FROM event_holds WHERE event_id NOT IN "
                 "(SELECT id FROM event_inquiries)")
    conn.commit()


def run():
    s = Suite("Availability: read once, and never once too often")
    conn = db()
    _cleanup(conn)
    today = m.house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()

    # A room of our own, far enough out that nothing real is near it.
    far = today + timedelta(days=400)
    conn.execute(
        """INSERT INTO rooms (name, description, price_per_night,
                              max_occupancy, active, min_nights, sort_order,
                              export_token)
           VALUES (?, 'test', 100, 2, 1, 1, 999, ?)""",
        (TAG + "room", TAG + "token"))
    room_id = conn.execute("SELECT id FROM rooms WHERE name = ?",
                           (TAG + "room",)).fetchone()["id"]
    conn.commit()

    def ask(start_offset, nights=1, **kw):
        a = far + timedelta(days=start_offset)
        with m.app.test_request_context("/"):
            return m.is_range_available(conn, room_id, a,
                                        a + timedelta(days=nights), **kw)

    # -- nothing in the way ------------------------------------------------
    ok, why = ask(0)
    s.check("an empty room on empty dates is free", ok and why is None,
            detail=str(why))

    # -- each blocker, with the sentence it is supposed to give -------------
    def booking(offset, nights, status):
        conn.execute(
            """INSERT INTO bookings (room_id, guest_name, guest_email,
                                     arrival_date, departure_date, party_size,
                                     status, created_at, reference_code,
                                     manage_token)
               VALUES (?, ?, 'a@b.c', ?, ?, 1, ?, ?, ?, ?)""",
            (room_id, TAG + status, (far + timedelta(days=offset)).isoformat(),
             (far + timedelta(days=offset + nights)).isoformat(), status, now,
             TAG + status + str(offset), TAG + status + str(offset) + "tok"))
        conn.commit()
        return conn.execute("SELECT id FROM bookings WHERE guest_name = ?"
                            " ORDER BY id DESC LIMIT 1",
                            (TAG + status,)).fetchone()["id"]

    bid = booking(10, 2, "confirmed")
    ok, why = ask(10)
    s.check("a confirmed booking closes its nights",
            not ok and "overlap an existing booking" in (why or ""),
            detail=str(why))

    # The checkout day is not an overlap — standard hotel convention, and the
    # thing a naive rewrite of this loop breaks first.
    ok, _ = ask(12)
    s.check("but the checkout day itself is free again", ok)

    s.check("and a booking can be moved without colliding with itself",
            ask(10, exclude_booking_id=bid)[0])

    pid = booking(20, 2, "pending")
    s.check("a pending request closes them too, for a new guest",
            not ask(20)[0])
    s.check("and not at confirm time, when two rivals is the normal case",
            ask(20, include_pending=False)[0])
    conn.execute("DELETE FROM bookings WHERE id IN (?, ?)", (bid, pid))

    conn.execute("INSERT INTO blocked_dates (room_id, start_date, end_date)"
                 " VALUES (?, ?, ?)",
                 (room_id, (far + timedelta(days=30)).isoformat(),
                  (far + timedelta(days=32)).isoformat()))
    conn.commit()
    ok, why = ask(30)
    s.check("a date held on another channel is not sold twice",
            not ok and "another booking channel" in (why or ""), detail=str(why))
    conn.execute("DELETE FROM blocked_dates WHERE room_id = ?", (room_id,))

    conn.execute("INSERT INTO room_blocks (room_id, start_date, end_date,"
                 " reason, created_at) VALUES (?, ?, ?, 'test', ?)",
                 (room_id, (far + timedelta(days=40)).isoformat(),
                  (far + timedelta(days=42)).isoformat(), now))
    conn.commit()
    s.check("a manual block closes the room", not ask(40)[0])
    conn.execute("DELETE FROM room_blocks WHERE room_id = ?", (room_id,))

    # -- the three that close the WHOLE house ------------------------------
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person,
                                  default_capacity, active, created_at)
           VALUES (?, 'x', 100, 4, 1, ?)""", (TAG + "atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (TAG + "atelier",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
                                          capacity, created_at)
           VALUES (?, ?, ?, 4, ?)""",
        (wid, (far + timedelta(days=50)).isoformat(),
         (far + timedelta(days=52)).isoformat(), now))
    conn.commit()
    ok, why = ask(50)
    s.check("a workshop takes the whole château, this room included",
            not ok and "workshop" in (why or "").lower(), detail=str(why))
    # End-INCLUSIVE, unlike a booking's departure day: a workshop finishing
    # on the 52nd still holds the 52nd.
    s.check("and holds its last day, where a departure would not",
            not ask(52)[0])
    conn.execute("DELETE FROM workshop_sessions WHERE workshop_id = ?", (wid,))
    conn.execute("DELETE FROM workshops WHERE id = ?", (wid,))

    conn.execute(
        """INSERT INTO event_inquiries (contact_name, contact_email,
                                        event_type, status, preferred_date,
                                        end_date, created_at, manage_token,
                                        reference_code)
           VALUES (?, 'a@b.c', 'wedding', 'confirmed', ?, ?, ?, ?, ?)""",
        (TAG + "wedding", (far + timedelta(days=60)).isoformat(),
         (far + timedelta(days=62)).isoformat(), now, TAG + "tok",
         TAG + "evref"))
    eid = conn.execute("SELECT id FROM event_inquiries WHERE contact_name = ?",
                       (TAG + "wedding",)).fetchone()["id"]
    conn.commit()
    ok, why = ask(60)
    s.check("a confirmed event does the same", not ok and "event" in (why or ""),
            detail=str(why))
    # The whole run, not the first day — a three-day wedding used to block one.
    s.check("for its whole run, not just the first day", not ask(62)[0])

    conn.execute("UPDATE event_inquiries SET status = 'new' WHERE id = ?", (eid,))
    conn.execute(
        """INSERT INTO event_holds (event_id, start_date, end_date, expires_at,
                                    created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (eid, (far + timedelta(days=70)).isoformat(),
         (far + timedelta(days=72)).isoformat(),
         (m.datetime.now(m.timezone.utc) + timedelta(days=7)).isoformat(), now))
    conn.commit()
    ok, why = ask(70)
    s.check("and a date promised while somebody decides is named as held",
            not ok and "provisionally held" in (why or ""), detail=str(why))
    # Named, not merely refused: "not available" sends the desk hunting for a
    # booking that does not exist, when what they want is a person to ring.
    s.check("with the kind of event, so there is somebody to ring",
            "wedding" in (why or ""), detail=str(why))

    conn.execute("DELETE FROM event_holds WHERE event_id = ?", (eid,))
    conn.execute("DELETE FROM event_inquiries WHERE id = ?", (eid,))
    conn.commit()

    # -- the cache's lifetime ----------------------------------------------
    #
    # Within one request the picture is held; across two it is rebuilt. Both
    # halves matter: the first is the whole point, and the second is what
    # stops yesterday's answer being shown tomorrow.
    with m.app.test_request_context("/"):
        first = m._availability_picture(conn)
        again = m._availability_picture(conn)
        s.check("the picture is built once per request",
                first is again)
    with m.app.test_request_context("/"):
        s.check("and again for the next one",
                m._availability_picture(conn) is not first)

    # THE ONE THAT MATTERS. A booking written after the picture was built is
    # invisible to it — that is what caching means. The locked check must not
    # be answered from it, or two guests pass the same check and get the same
    # room.
    with m.app.test_request_context("/"):
        a = far + timedelta(days=80)
        d = a + timedelta(days=2)
        stale_ok, _ = m.is_range_available(conn, room_id, a, d)
        s.check("a free date reads free before anything is written", stale_ok)

        conn.execute(
            """INSERT INTO bookings (room_id, guest_name, guest_email,
                                     arrival_date, departure_date, party_size,
                                     status, created_at, reference_code,
                                     manage_token)
               VALUES (?, ?, 'a@b.c', ?, ?, 1, 'confirmed', ?, ?, ?)""",
            (room_id, TAG + "rival", a.isoformat(), d.isoformat(), now,
             TAG + "rivalref", TAG + "rivaltok"))
        conn.commit()

        cached_ok, _ = m.is_range_available(conn, room_id, a, d)
        s.check("the cached picture cannot see a booking taken since",
                cached_ok,
                detail="if this fails the cache is not caching and the rest "
                       "of this check proves nothing")

        locked_ok, why = m.claim_range(conn, room_id, a, d)
        s.check("but the check under the booking lock does",
                not locked_ok,
                detail="%s — a locked check answered from a read taken "
                       "before the lock is how two guests get one room" % why)

    _cleanup(conn)
    conn.close()
    return s
