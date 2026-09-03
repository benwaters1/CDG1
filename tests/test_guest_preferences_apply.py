"""The things the profile knows, used at the moment they would save somebody typing.

The guest record has held a usual arrival time, dietary notes, a telephone
number and what somebody needs from the house for a while. Nothing read any of
it at the point it would have mattered, so a returning guest typed it all again
and the house asked questions it already had the answers to.

Two of those moments, and one dead feature.

  THE WELCOME BACK PANEL. book_rooms.html has carried one since it was
  designed -- guarded, styled, and reading a variable nothing ever passed. It
  has never once appeared for anybody. Only from a stay that DEPARTED:
  somebody with a booking still to come is waiting, not returning, and
  "welcome back" before they have arrived reads as a mistake.

  THE BOOKING FORM. Blanks only, and anything the form already carries wins --
  what somebody just typed is more current than what is on file.

  THE SAME-DAY CHANGEOVER. The one turnaround with no slack in it, and exactly
  where "they cannot manage the stairs" has to be known before the room is made
  up rather than after.

Identity comes from a token page and nowhere else: the guest arrived on a link
addressed to them, which is the only point on the public site where the app
knows who is reading. It lasts the session and no longer.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, room_id, arrival, nights=2, email=None, status="confirmed"):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 'unpaid', 400, 0, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         email or f"zzpr.{ref}@example.invalid".lower(), arrival.isoformat(),
         (arrival + timedelta(days=nights)).isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _profile(email, **cols):
    conn = db()
    keys = dict(name=f"{TAG} Profile", email=email,
                created_at=datetime.now(timezone.utc).isoformat())
    keys.update(cols)
    conn.execute("INSERT INTO guests (%s) VALUES (%s)"
                 % (", ".join(keys), ", ".join("?" * len(keys))), list(keys.values()))
    conn.commit()
    conn.close()


def run():
    s = Suite("Preferences that apply themselves")
    _cleanup()
    oc, _ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    today = m.house_today()

    s.section("Nobody is recognised until they arrive on their own link")
    anon = m.app.test_client()
    body = anon.get("/book").get_data(as_text=True)
    s.check("a stranger gets no welcome", "Welcome back" not in body,
            detail="the panel is guarded on a name nothing passed, so it has "
                   "never appeared for anybody at all")
    s.check("and the form is empty",
            'value="' + TAG not in body,
            detail="an anonymous visitor costs no session and no query")

    s.section("A guest who has been here before, on their own link")
    past = _stay("PAST", room_id=room["id"], arrival=today - timedelta(days=90))
    _profile("zzpr.past@example.invalid", phone="+33 6 11 22 33 44",
             dietary_notes="coeliac", usual_arrival_time="16:00")
    known = m.app.test_client()
    known.get(f"/book/manage/{past['manage_token']}")
    body = known.get("/book").get_data(as_text=True)
    s.check("the welcome finally appears", "Welcome back" in body,
            detail="designed, styled, guarded and never once rendered")
    s.check("naming the room they had", room["name"] in body)
    s.check("and the year they were here", str((today - timedelta(days=90)).year) in body,
            detail="'you stayed here' with no when is a sentence nobody believes")

    s.section("And the booking form knows what we already have")
    form = known.get(f"/book/{room['id']}").get_data(as_text=True)
    s.check("their telephone is filled in", "+33 6 11 22 33 44" in form,
            detail="it was on the profile and nothing read it at the one moment "
                   "it would have saved somebody typing")
    s.check("and what they cannot eat", "coeliac" in form)

    s.section("But what they just typed always wins")
    # The profile says coeliac; this submission says otherwise, and the form
    # that comes back must say what they typed.
    posted = known.post(f"/book/{room['id']}", data={
        "guest_name": f"{TAG} Typed", "guest_email": "zzpr.past@example.invalid",
        "guest_phone": "+33 7 99 88 77 66", "special_requests": "actually fine now",
        "arrival_date": "", "departure_date": "", "party_size": "2",
    }, follow_redirects=True).get_data(as_text=True)
    s.check("the typed telephone comes back", "+33 7 99 88 77 66" in posted,
            detail="what somebody just typed is more current than what is on file")
    s.check("and not the one on file", "+33 6 11 22 33 44" not in posted,
            detail="filling a blank is help; overwriting an answer is a bug")

    s.section("Waiting is not returning")
    future = _stay("SOON", room_id=room["id"], arrival=today + timedelta(days=30),
                   email="zzpr.soon@example.invalid")
    waiting = m.app.test_client()
    waiting.get(f"/book/manage/{future['manage_token']}")
    body = waiting.get("/book").get_data(as_text=True)
    s.check("somebody with a stay still to come gets no welcome back",
            "Welcome back" not in body,
            detail="they are waiting, not returning, and being welcomed back "
                   "before arriving reads as a mistake")

    s.section("A same-day changeover says what the incoming guest needs")
    # One leaves, another arrives the same day, and the second cannot manage
    # stairs. This is the turnaround with no slack in it.
    out = _stay("OUT", room_id=room["id"], arrival=today + timedelta(days=5), nights=3)
    _stay("IN", room_id=room["id"], arrival=today + timedelta(days=8), nights=2,
          email="zzpr.in@example.invalid")
    _profile("zzpr.in@example.invalid", name=f"{TAG} Incoming",
             access_needs="cannot manage the stairs")
    conn = db()
    data = m.turnaround_report(conn, 60)
    conn.close()
    mine = [c for c in data.get("csv", []) if False]  # csv shape differs; use the page
    page = oc.get("/admin/turnarounds").get_data(as_text=True)
    s.check("the changeover is listed", f"{TAG} IN" in page,
            detail="the fixture has to produce one for the next check to mean "
                   "anything")
    s.check("and what they need is on it", "cannot manage the stairs" in page,
            detail="it was on the profile, and the room is made up before "
                   "anybody would have gone looking")

    s.section("The helper is honest about knowing nothing")
    conn = db()
    s.check("no address means no answer", m.access_needs_for(conn, "") == "")
    s.check("and an address we have never seen means no answer",
            m.access_needs_for(conn, "nobody@example.invalid") == "",
            detail="an empty string rather than a raise: a guest with no "
                   "profile is the ordinary case")
    conn.close()

    _cleanup()
    return s
