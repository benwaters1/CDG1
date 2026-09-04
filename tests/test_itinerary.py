"""What is on at the château while a guest is here.

A guest knew their arrival date and nothing else. Whether the restaurant served
on the Tuesday, whether there was an atelier on while they were here, what they
had already booked themselves — all of it was in the app and none of it was on
one page in front of the person it concerned, so it was asked by email, one
question at a time.

Three things carry this file.

  A PRIVATE EVENT IS NEVER ON IT. A wedding, a shoot or a hire is somebody
  else's day at the house, and putting it on a stranger's itinerary tells them
  who is getting married here and when. "What's on" means what this guest may
  join, not everything in the diary.

  NOTHING IS INVENTED. Every line comes from something the house has actually
  recorded — their arrival time if they gave one, the dinner hour the
  restaurant is set to, an atelier that genuinely runs while they are here. A
  made-up hour on a guest's own itinerary is worse than no itinerary, because
  they will plan a drive round it.

  IT IS THEIRS. Reached by their manage link, names their room and what they
  have booked, and never indexed — the same rule as their bill.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZIT"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_bookings WHERE guest_name LIKE ?""", (TAG + "%",))
    conn.execute("""DELETE FROM workshop_sessions WHERE workshop_id IN
                    (SELECT id FROM workshops WHERE title LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("What is on while you are here")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    arrival = m.house_today() + timedelta(days=40)
    departure = arrival + timedelta(days=3)
    now = datetime.now(timezone.utc).isoformat()
    email = "zzit.guest@example.invalid"

    conn = db()
    # The restaurant SERVING, deliberately. Without this the dinner lines
    # never appear at all, and two of the checks below could not fail --
    # "they are not also sold a table" passes just as well when nobody is
    # ever offered one. Found by breaking the code and watching nothing
    # go red.
    conn.execute(
        """INSERT INTO restaurant_settings (id, dinner_time, capacity, enabled)
           VALUES (1, '19:30', 20, 1)
           ON CONFLICT(id) DO UPDATE SET enabled = 1, dinner_time = '19:30'""")
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at,
           estimated_arrival_time)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 'unpaid', 800, 0, ?, '18:30')""",
        (room["id"], f"{TAG}-A", f"tok{TAG}a".lower(), f"{TAG} Guest", email,
         arrival.isoformat(), departure.isoformat(), now))
    # A dinner they have already booked, on their second night.
    second = (arrival + timedelta(days=1)).isoformat()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
           guest_email, party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
        (f"{TAG}-DIN", f"tok{TAG}din".lower(), f"{TAG} Guest", email, second, now))
    # An atelier running across their stay.
    cur = conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, active,
           created_at) VALUES (?, '', 200, 1, ?)""", (f"{TAG} Plasterwork", now))
    wid = cur.lastrowid
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
           capacity, created_at) VALUES (?, ?, ?, 10, ?)""",
        (wid, second, (arrival + timedelta(days=2)).isoformat(), now))
    # One that runs after they leave, which is not on during their stay.
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, active,
           created_at) VALUES (?, '', 200, 1, ?)""", (f"{TAG} Later Course", now))
    later_id = conn.execute("SELECT id FROM workshops WHERE title = ?",
                            (f"{TAG} Later Course",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date,
           capacity, created_at) VALUES (?, ?, ?, 10, ?)""",
        (later_id, (departure + timedelta(days=5)).isoformat(),
         (departure + timedelta(days=7)).isoformat(), now))
    # A wedding, in the diary, while they are here. NEVER on their itinerary.
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, status, created_at)
           VALUES (?, ?, 'wedding', ?, 'someone@example.invalid', ?, 'confirmed', ?)""",
        (f"{TAG}-WED", f"tok{TAG}wed".lower(), f"{TAG} Somebody Else",
         second, now))
    conn.commit()
    booking = conn.execute("SELECT bookings.*, rooms.name AS room_name FROM bookings "
                           "JOIN rooms ON rooms.id = bookings.room_id "
                           "WHERE reference_code = ?", (f"{TAG}-A",)).fetchone()
    with m.app.test_request_context("/"):
        days = m.stay_itinerary(conn, booking)
    conn.close()

    s.section("Every day of the stay, in order")
    s.check("one entry per day, arrival to departure", len(days) == 4,
            detail=f"{len(days)} for a three-night stay")
    s.check("starting the day they arrive",
            days and days[0]["date"] == arrival.isoformat())
    s.check("and ending the day they leave",
            days and days[-1]["date"] == departure.isoformat())

    kinds = {d["date"]: [e["kind"] for e in d["entries"]] for d in days}
    whats = {d["date"]: " | ".join(e["what"] for e in d["entries"]) for d in days}

    s.section("What the house already knows about them")
    s.check("their arrival is the first thing on it",
            "arrival" in kinds[arrival.isoformat()])
    s.check("with the time they gave, not one we made up",
            "18:30" in whats[arrival.isoformat()],
            detail="a made-up hour is worse than none — they will plan a "
                   "drive round it")
    s.check("the dinner service is on a night they have not booked",
            "dinner_open" in kinds[arrival.isoformat()],
            detail=f"{kinds[arrival.isoformat()]} — without this the two "
                   "checks below cannot fail")
    s.check("their own dinner is on the right night",
            "dinner_yours" in kinds[second], detail=f"{kinds[second]}")
    s.check("and they are not also told the restaurant exists that night",
            "dinner_open" not in kinds[second],
            detail="a guest who has a table does not need to be sold one")
    s.check("the atelier they are not on is offered",
            "atelier" in kinds[second], detail=f"{kinds[second]}")
    s.check("and it is named", f"{TAG} Plasterwork" in whats[second])
    s.check("on the days it actually runs, and no others",
            f"{TAG} Plasterwork" not in whats[arrival.isoformat()]
            and f"{TAG} Plasterwork" not in whats[departure.isoformat()],
            detail="a two-day course printed against all four days is a guest "
                   "turning up on the wrong morning")

    s.section("What is not theirs is not on it")
    everything = " ".join(whats.values())
    # THE ONE THAT WOULD BE A BREACH RATHER THAN A BUG.
    s.check("somebody else's wedding is nowhere on the page",
            "Somebody Else" not in everything and "wedding" not in everything.lower(),
            detail="a private event on a stranger's itinerary tells them who "
                   "is getting married here and when")
    s.check("nor a course that runs after they have gone",
            "Later Course" not in everything,
            detail="a course starting the day after they leave is not "
                   "something on during their stay")

    s.section("The last night")
    s.check("they are told they are leaving",
            "departure" in kinds[departure.isoformat()])
    s.check("and not offered dinner on the evening they have gone",
            "dinner_open" not in kinds[departure.isoformat()],
            detail=f"{kinds[departure.isoformat()]}")

    s.section("An atelier they ARE on reads differently")
    conn = db()
    session_id = conn.execute(
        """SELECT workshop_sessions.id FROM workshop_sessions
             JOIN workshops ON workshops.id = workshop_sessions.workshop_id
            WHERE workshops.title = ?""", (f"{TAG} Plasterwork",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token,
           guest_name, guest_email, party_size, status, created_at)
           VALUES (?, ?, ?, ?, ?, 2, 'confirmed', ?)""",
        (session_id, f"{TAG}-WS", f"tok{TAG}ws".lower(), f"{TAG} Guest", email, now))
    conn.commit()
    with m.app.test_request_context("/"):
        days2 = m.stay_itinerary(conn, booking)
    conn.close()
    theirs = [e for d in days2 for e in d["entries"] if e["kind"] == "atelier_yours"]
    s.check("it says they are booked on it", theirs,
            detail="'on at the château' and 'you are booked on this' are "
                   "different sentences to read the morning of")
    s.check("and it is not also offered to them",
            not [e for d in days2 for e in d["entries"] if e["kind"] == "atelier"],
            detail="being sold a place they already hold")

    s.section("The page")
    page = oc.get(f"/book/manage/tok{TAG.lower()}a/itinerary").get_data(as_text=True)
    s.check("it opens on their link", booking["reference_code"] in page)
    s.check("with the days on it", f"{TAG} Plasterwork" in page)
    s.check("never indexed", "noindex" in page,
            detail="their room and what they have booked — the same rule as "
                   "their bill")
    s.check("and it is reachable from their booking page",
            "/itinerary" in oc.get(f"/book/manage/tok{TAG.lower()}a").get_data(as_text=True),
            detail="a page nobody can get to is a page nobody uses")
    s.check("somebody else's token is a 404",
            oc.get("/book/manage/nonsense/itinerary").status_code == 404)

    _cleanup()
    return s
