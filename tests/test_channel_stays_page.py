# -*- coding: utf-8 -*-
"""One page for the stays the house did not take itself.

Booking.com stays have reached the calendar, who-is-here, the office
display, the turnaround report and the owner's day for some time —
channel_stays() feeds all of them. What there has never been is a page to
OPEN: they arrive by email, land in a table, surface as a line on six other
pages, and there was nowhere to go and look at one.

The three things this page exists to show, and what each is really guarding:

  THE POLICE FICHE. A stay at a French chambre d'hôtes needs a register
  entry by law, and a Booking.com stay has no `bookings` row to hang one on
  — which is exactly why channel_police_register exists as its own table. A
  missing fiche is the only thing here that is a legal problem rather than
  an inconvenience, so the band counts it and the page can be filtered by
  it. If this check ever reads "0 missing" on a stay with no fiche, the
  owner is being told the register is in order when it is not.

  THE UNMATCHED ROOM. The channel sends a room LABEL, not the house's room.
  Unmatched, the stay sits on the calendar with no room against it and the
  turnover list cannot tell anybody which bed to make. It is a quiet
  failure: the stay is there, it just has a hole in it.

  THE PAIR. channel_stays() drops a stay the house has also entered by hand
  so nothing is counted twice — correctly, and SILENTLY. Two rows for the
  same nights is how a room gets cleaned twice and a guest gets two
  welcomes, so this page is where that pair becomes visible.

And the page shows CANCELLED and undated stays, which channel_stays() does
not return at all. A cancelled stay still has to be seen, and one that
arrived with no dates on it is precisely the row somebody has to go and
look at — if this page only listed what the other pages already list, it
would have no reason to exist.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZOTA"


def _cleanup():
    conn = db()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM ota_reservations WHERE reservation_number LIKE ?",
        (TAG + "%",))]
    for oid in ids:
        conn.execute("DELETE FROM channel_police_register "
                     "WHERE ota_reservation_id = ?", (oid,))
    conn.execute("DELETE FROM ota_reservations WHERE reservation_number LIKE ?",
                 (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(conn, ref, *, start_in=10, nights=3, status="confirmed",
          room_id=None, name="A Channel Guest", dated=True):
    start = m.house_today() + timedelta(days=start_in)
    now = _harness.datetime_now()
    cur = conn.execute(
        """INSERT INTO ota_reservations (channel, reservation_number, status,
               guest_name, arrival_date, departure_date, room_label, room_id,
               guests, amount, currency, first_seen_at, updated_at)
           VALUES ('booking.com', ?, ?, ?, ?, ?, 'Chambre Bleue', ?, 2, 420.0,
                   'EUR', ?, ?)""",
        (TAG + ref, status, name,
         start.isoformat() if dated else None,
         (start + timedelta(days=nights)).isoformat() if dated else None,
         room_id, now, now))
    conn.commit()
    return cur.lastrowid


def run():
    s = Suite("Booking.com stays, on a page of their own")
    oc, ec, _owner, _emp = clients()
    _cleanup()
    conn = db()
    room = conn.execute(
        "SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 1"
    ).fetchone()

    plain = _stay(conn, "-PLAIN", room_id=room["id"], name=TAG + " Fiched")
    nofiche = _stay(conn, "-NOFICHE", start_in=14, room_id=room["id"],
                    name=TAG + " Unregistered")
    unmatched = _stay(conn, "-NOROOM", start_in=18, room_id=None,
                      name=TAG + " Unroomed")
    gone = _stay(conn, "-GONE", start_in=22, status="cancelled",
                 room_id=room["id"], name=TAG + " Cancelled")
    undated = _stay(conn, "-NODATE", room_id=room["id"], dated=False,
                    name=TAG + " Undated")
    conn.execute(
        """INSERT INTO channel_police_register (ota_reservation_id, surname,
               first_names, nationality, recorded_at)
           VALUES (?, 'Fiched', 'A', 'FR', ?)""",
        (plain, _harness.datetime_now()))
    conn.commit()
    conn.close()

    page = oc.get("/admin/channel-stays")
    s.check("the page opens", page.status_code == 200, detail=str(page.status_code))
    body = page.get_data(as_text=True)
    flat = " ".join(body.split())

    s.section("It shows what the other pages do not")
    s.check("a cancelled stay is still on it", TAG + " Cancelled" in flat,
            detail="channel_stays() returns only confirmed, dated stays — a "
                   "page that showed only those would have nothing the "
                   "calendar does not already have")
    s.check("and one that arrived with no dates", TAG + " Undated" in flat,
            detail="exactly the row somebody has to go and look at")

    s.section("The fiche, which is the legal one")
    s.check("a stay with no police fiche is called out",
            "Not done" in flat,
            detail="a stay here needs a register entry by law, and these "
                   "cannot use the register the house's own bookings use")
    s.check("and the one that has a fiche is not",
            flat.count("Not done") >= 1
            and "1 on file" in flat,
            detail="if a fiched stay also read Not done the count would be "
                   "noise and nobody would act on it")

    s.section("The room nobody has matched")
    s.check("an unmatched stay says so rather than showing a blank",
            "Not matched" in flat,
            detail="the channel sends a room name, not the house's room; "
                   "unmatched, the turnover list cannot say which bed")

    s.section("What the band counts")
    # Read from the page, not from a helper, because the band is the thing
    # the owner looks at first and a correct helper behind a wrong band is
    # still a wrong page.
    s.check("the band names the missing fiches",
            "No police fiche" in flat)
    s.check("and the unmatched rooms", "No room matched" in flat)

    s.section("Who may open it")
    s.check("an employee cannot", ec.get("/admin/channel-stays").status_code == 403)
    anon = m.app.test_client()
    s.check("and a stranger is sent to log in",
            anon.get("/admin/channel-stays").status_code in (302, 401, 403))

    _cleanup()
    return s
