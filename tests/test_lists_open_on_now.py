"""The lists open on what is current, and what is over goes into history.

The owner asked for it in so many words: workshops and bookings should
"disappear into history when they are over". The bookings list opened on every
stay there had ever been, oldest first, so the first screen was last year; the
restaurant list and the three waitlists were the same, with a status dropdown
or nothing. Each now opens on what is current, with History one chip away
(newest first), and the standard toolbar in place of the dropdown.

And two things found on the way: a telephone number was only found if it was
typed the way it had been stored, and a guest profile merged into another
still appeared on the guest list and in its export -- the duplicate the merge
existed to remove, back one page on.
"""
import csv
import io
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZLO"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_waitlist WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_waitlist WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, arrive_in, nights=2, phone=None, status="confirmed"):
    conn = db()
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    arrival = house_today() + timedelta(days=arrive_in)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           guest_phone, arrival_date, departure_date, party_size, status, total_price, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, 300, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid", phone, arrival.isoformat(),
         (arrival + timedelta(days=nights)).isoformat(), status, _harness.datetime_now()))
    conn.commit()
    conn.close()


def _dinner(ref, on_in, phone=None, status="confirmed", no_show=False):
    conn = db()
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
           guest_phone, party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, ?, 2, ?, ?, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}d{ref}".lower(), f"{TAG} {ref}",
         f"{TAG.lower()}.d{ref.lower()}@example.invalid", phone,
         (house_today() + timedelta(days=on_in)).isoformat(), status, _harness.datetime_now()))
    if no_show:
        conn.execute("UPDATE restaurant_bookings SET no_show_at = ? WHERE reference_code = ?",
                     (_harness.datetime_now(), f"{TAG}-{ref}"))
    conn.commit()
    conn.close()


def _in(page, ref):
    """On a waitlist, where nothing but the list names anybody."""
    return f"{TAG} {ref}" in page


def _listed(page, ref):
    """In THE LIST, by reference code. The bookings page names whoever is here
    or arriving in a band above the list, so a name found anywhere on it says
    nothing about what the list shows."""
    return f"{TAG}-{ref}" in page


def run():
    s = Suite("Lists open on now")
    oc, _ec, _owner, _emp = clients()
    _cleanup()

    s.section("Bookings open on what is current")
    _stay("Last", -30)
    _stay("Older", -60)
    _stay("Now", -1, nights=3)
    _stay("Soon", 10, phone="+33 6 12 34 56 78")
    page = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("a stay still to come is there", _listed(page, "Soon"))
    s.check("so is one happening now", _listed(page, "Now"))
    s.check("one that is over is not", not _listed(page, "Last") and not _listed(page, "Older"),
            detail="the first screen was last year")
    hist = oc.get("/admin/bookings?when=History").get_data(as_text=True)
    s.check("History has them, and not what is current",
            _listed(hist, "Last") and _listed(hist, "Older") and not _listed(hist, "Soon"))
    s.check("most recent first", hist.find(f"{TAG}-Last") < hist.find(f"{TAG}-Older"),
            detail="history read from the oldest down is history nobody reads")
    here = oc.get("/admin/bookings?stage=Here+now").get_data(as_text=True)
    s.check("who is here now is its own chip", _listed(here, "Now") and not _listed(here, "Soon"))
    old = oc.get("/admin/bookings?when=Here+now").get_data(as_text=True)
    s.check("an old 'here now' link still arrives there", _listed(old, "Now") and not _listed(old, "Soon"))
    gone = oc.get("/admin/bookings?when=Been+and+gone").get_data(as_text=True)
    s.check("and an old 'been and gone' one at History", _listed(gone, "Last") and not _listed(gone, "Now"))

    s.section("A telephone number is found however it is typed")
    for typed in ("06 12 34 56 78", "0612345678", "+33612345678", "12 34 56 78"):
        page = oc.get("/admin/bookings", query_string={"q": typed}).get_data(as_text=True)
        s.check(f"searching {typed!r} finds +33 6 12 34 56 78", _listed(page, "Soon"),
                detail="a search box that only matched the way it was stored found nobody")
    page = oc.get("/admin/bookings", query_string={"q": "0699999999"}).get_data(as_text=True)
    s.check("and another number does not", not _listed(page, "Soon"))

    s.section("The restaurant opens on tonight and what is coming")
    _dinner("Tonight", 0)
    _dinner("Week", 5, phone="06 55 44 33 22")
    _dinner("Before", -3, no_show=True)
    _dinner("Earlier", -4)
    page = oc.get("/admin/restaurant").get_data(as_text=True)
    s.check("tonight and later are there", _listed(page, "Tonight") and _listed(page, "Week"))
    s.check("last week is not", not _listed(page, "Before"))
    hist = oc.get("/admin/restaurant?when=History").get_data(as_text=True)
    s.check("it is under History", _listed(hist, "Before") and not _listed(hist, "Week"))
    shows = oc.get("/admin/restaurant?when=History&came=No-show").get_data(as_text=True)
    s.check("a no-show is a chip of its own there",
            'No-show <span class="chip-n">' in hist
            and _listed(shows, "Before") and not _listed(shows, "Earlier"),
            detail="the dinner's own no-show badge is not a filter")
    page = oc.get("/admin/restaurant?status=pending").get_data(as_text=True)
    s.check("an old ?status= link is still the Status chip", not _listed(page, "Week"))
    page = oc.get("/admin/restaurant", query_string={"q": "+33655443322"}).get_data(as_text=True)
    s.check("and a telephone number is found", _listed(page, "Week"))

    s.section("The waitlists: somebody who wanted a date that has passed is History")
    conn = db()
    now = _harness.datetime_now()
    past, future = ((house_today() + timedelta(days=d)).isoformat() for d in (-5, 20))
    conn.execute("INSERT INTO waitlist_entries (name, email, desired_arrival, desired_departure, "
                 "status, created_at) VALUES (?, 'a@example.invalid', ?, ?, 'open', ?)",
                 (f"{TAG} RoomGone", past, past, now))
    conn.execute("INSERT INTO waitlist_entries (name, email, desired_arrival, desired_departure, "
                 "status, created_at) VALUES (?, 'b@example.invalid', ?, ?, 'open', ?)",
                 (f"{TAG} RoomSoon", future, future, now))
    conn.execute("INSERT INTO waitlist_entries (name, email, status, created_at) "
                 "VALUES (?, 'c@example.invalid', 'open', ?)", (f"{TAG} RoomUndated", now))
    conn.execute("INSERT INTO restaurant_waitlist (name, email, desired_date, status, created_at) "
                 "VALUES (?, 'd@example.invalid', ?, 'open', ?)", (f"{TAG} DinGone", past, now))
    conn.execute("INSERT INTO restaurant_waitlist (name, email, desired_date, status, created_at) "
                 "VALUES (?, 'e@example.invalid', ?, 'open', ?)", (f"{TAG} DinSoon", future, now))
    conn.execute("INSERT INTO workshops (title, description, price_per_person, default_capacity, "
                 "active, sort_order, created_at) VALUES (?, '', 100, 10, 1, 92, ?)",
                 (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    for label, start in (("Gone", past), ("Soon", future)):
        conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, "
                     "notes, created_at) VALUES (?, ?, ?, 10, ?, ?)",
                     (wid, start, start, f"{TAG} {label}", now))
        sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                           (f"{TAG} {label}",)).fetchone()["id"]
        conn.execute("INSERT INTO workshop_waitlist (session_id, name, email, status, created_at) "
                     "VALUES (?, ?, 'f@example.invalid', 'open', ?)", (sid, f"{TAG} Ws{label}", now))
    conn.commit()
    conn.close()
    for url, gone, soon in (("/admin/waitlist", "RoomGone", "RoomSoon"),
                            ("/admin/restaurant/waitlist", "DinGone", "DinSoon"),
                            ("/admin/workshops/waitlist", "WsGone", "WsSoon")):
        page = oc.get(url).get_data(as_text=True)
        hist = oc.get(url + "?when=History").get_data(as_text=True)
        s.check(f"{url}: the one still wanted is there and the passed one is not",
                _in(page, soon) and not _in(page, gone))
        s.check(f"{url}: and the passed one is under History", _in(hist, gone) and not _in(hist, soon))
    page = oc.get("/admin/waitlist").get_data(as_text=True)
    s.check("an enquiry with no date stays current until somebody closes it",
            _in(page, "RoomUndated"))
    r = oc.get("/admin/waitlist/export.csv?when=History")
    names = [row["name"] for row in csv.DictReader(io.StringIO(r.get_data(as_text=True)))
             if row["name"].startswith(TAG)]
    s.check("the export is the view it was pressed on", names == [f"{TAG} RoomGone"],
            detail=f"{names}")

    s.section("A merged profile is not a guest any more")
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, 'keep@example.invalid', ?)",
                 (f"{TAG} Keeper", now))
    keeper = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} Keeper",)).fetchone()["id"]
    conn.execute("INSERT INTO guests (name, email, created_at, merged_into_id) "
                 "VALUES (?, 'dup@example.invalid', ?, ?)", (f"{TAG} Merged", now, keeper))
    conn.commit()
    conn.close()
    page = oc.get("/guests").get_data(as_text=True)
    s.check("the list shows the one kept, not the one merged into it",
            _in(page, "Keeper") and not _in(page, "Merged"))
    r = oc.get("/admin/guests/export.csv")
    exported = r.get_data(as_text=True)
    s.check("and nor does the export", f"{TAG} Keeper" in exported and f"{TAG} Merged" not in exported)

    s.section("The new chips survive being saved")
    kept = m.saved_view_query({"stage": "Here now", "came": "No-show", "when": "History"})
    s.check("stage, came and when are kept", "stage=Here+now" in kept and "came=No-show" in kept
            and "when=History" in kept, detail=kept)

    _cleanup()
    return s
