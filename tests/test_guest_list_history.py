"""The guest list says who has been before, how often, and when they are next due.

The list counted only stays LINKED to a profile, so a guest whose booking had
matched them by address -- every stay that came in before the link was made --
read "First time" on the list while their own page showed the stay. And a card
said nothing of the guest's history at all: the owner opened each one to find
out whether somebody was a regular or arriving on Friday (17 October 2026).

guest_record is the definition of which stays are a guest's: linked ones, and
unlinked ones under their address. The list follows it.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZHIST"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _profile(name, email):
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} {name}", email, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()["id"]
    conn.close()
    return gid


def _stay(ref, email, arrive, nights, *, linked=None, no_show=False):
    conn = db()
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at,
               linked_guest_id, no_show_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 300, ?, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}", email,
         arrive.isoformat(), (arrive + timedelta(days=nights)).isoformat(),
         datetime.now(timezone.utc).isoformat(), linked,
         datetime.now(timezone.utc).isoformat() if no_show else None))
    conn.commit()
    conn.close()


def run():
    s = Suite("The guest list says who has been before")
    _cleanup()
    oc, _ec, _o, _e = clients()
    today = m.house_today()
    try:
        addr = "zzhist.regular@example.invalid"
        regular = _profile("Regular", addr)
        # Two past stays matched by ADDRESS only, never linked -- the case that
        # read "First time" -- one stay that never happened, and one to come.
        _stay("P1", addr, today - timedelta(days=200), 3)
        _stay("P2", addr, today - timedelta(days=40), 2)
        _stay("NS", addr, today - timedelta(days=20), 4, no_show=True)
        _stay("F1", addr, today + timedelta(days=12), 2)
        linked = _profile("Linked", "zzhist.linked@example.invalid")
        _stay("L1", "someone.else@example.invalid", today - timedelta(days=10), 1, linked=linked)
        new = _profile("New", "zzhist.new@example.invalid")

        conn = db()
        h = m.guest_stay_summaries(conn, today)
        conn.close()
        mine = h.get(regular, {})
        s.check("stays matched by address count, as on their own page",
                mine.get("stays") == 2, detail=str(mine))
        s.check("a stay nobody came for does not", mine.get("nights") == 5, detail=str(mine))
        s.check("last here is the most recent stay that began",
                mine.get("last") == (today - timedelta(days=40)).isoformat())
        s.check("and the next is the one still to come",
                mine.get("next") == (today + timedelta(days=12)).isoformat())
        s.check("a linked stay is theirs whatever address it was under",
                h.get(linked, {}).get("stays") == 1)
        s.check("somebody who has not stayed has no history", not h.get(new))

        page = oc.get("/guests", query_string={"q": TAG}).get_data(as_text=True)
        with m.app.test_request_context("/"):
            last = m.format_date_short((today - timedelta(days=40)).isoformat())
            nxt = m.format_date_short((today + timedelta(days=12)).isoformat())
        s.check("the card says how often and how long",
                "2 stays, 5 nights" in page, detail="the line under their contact details")
        s.check("when they were last here", f"last here {last}" in page)
        s.check("and when they are next arriving", f"next arriving {nxt}" in page)
        s.check("a new profile says so", "Not stayed yet" in page)
        been = oc.get("/guests", query_string={"q": TAG, "known": "Been before"}).get_data(as_text=True)
        s.check("'Been before' finds the guest matched by address",
                f"{TAG} Regular" in been and f"{TAG} New" not in been)
        s.check("no money on the list", "€" not in page.split("Guest profiles")[-1],
                detail="what a guest is worth is on their own page, from each stay's bill")
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
