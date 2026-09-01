"""The biggest list in the app, brought onto the shared toolbar.

The house rule is that every list gets list_view — search, counted chips, sort —
and never another one-off search box. This page had a one-off search box: three
dropdowns and a Filter button. So the list the house looks at most was the only
one with no counts on its filters, no way to sort, and no saved views.

The risk in converting it is not the toolbar, it is the URLs. ?status=pending
and ?room_id=3 are in bookmarks, in links people have sent each other, and open
on the second screen at the desk. A conversion that quietly stopped honouring
them would look exactly like the filters had broken, so half of this file is
about those still working.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZBL"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM saved_views WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _rooms():
    conn = db()
    try:
        return conn.execute(
            "SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 2").fetchall()
    finally:
        conn.close()


def _stay(ref, *, room, status="confirmed", arrival_offset=10, nights=2, name=None):
    """Dates built on the basis THE PAGE USES, which is the UTC date.

    house_today() is the local one, and on this machine the two differ for part
    of every day — so a fixture built on local time put a stay one day either
    side of where the page thought it was, and the "here now" boundary failed
    for a reason that had nothing to do with the feature.

    Matching the page keeps its own bands consistent with each other: "arriving
    today" on the same screen is worked out the same way. Whether this page
    ought to use service_day() instead is a real question and a separate one —
    changing it as a side effect of converting a list would be the wrong place
    for that argument.
    """
    conn = db()
    arrival = datetime.now(timezone.utc).date() + timedelta(days=arrival_offset)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 300, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(),
         name or f"{TAG} {ref}", f"zzbl.{ref}@example.invalid".lower(),
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _names(body):
    """Which of this suite's bookings are IN THE LIST on the rendered page.

    Matched on the REFERENCE CODE, not the guest name. The summary band at the
    top of this page names whoever is arriving or departing today, so a helper
    that searched the whole body for a guest's name reported them as present on
    every filtered view — and the "here now" check passed a guest who was in the
    header and not in the list.
    """
    return {ref for ref in ("PEND", "CONF", "OTHER", "PAST", "NOW", "LEAVING",
                            "NOEMAIL")
            if f"{TAG}-{ref}" in body}


def run():
    s = Suite("The bookings list")
    _cleanup()
    oc, ec, owner, emp = clients()
    rooms = _rooms()
    if len(rooms) < 2:
        s.check("two rooms to filter between", False,
                detail="the fixture needs two rooms and the house has one")
        return s
    first, second = rooms[0], rooms[1]

    _stay("PEND", room=first, status="pending", arrival_offset=12)
    _stay("CONF", room=first, status="confirmed", arrival_offset=14)
    _stay("OTHER", room=second, status="confirmed", arrival_offset=16)
    _stay("PAST", room=first, status="confirmed", arrival_offset=-30)
    _stay("NOW", room=second, status="confirmed", arrival_offset=-1, nights=4)

    s.section("The shared toolbar is there")
    body = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the page opens", "list-toolbar" in body)
    s.check("with counted chips", "chip-n" in body,
            detail="a status filter that lists the statuses makes you click each "
                   "one to find where the work is")
    s.check("and a sort", "list-sort" in body)
    s.check("and no leftover one-off filter bar", 'class="search-bar"' not in body,
            detail="two filter bars on one page is worse than the one it had")

    s.section("The old links still work, because people have them")
    body = oc.get("/admin/bookings?status=pending").get_data(as_text=True)
    shown = _names(body)
    s.check("?status=pending still filters", shown == {"PEND"},
            detail=f"{shown} — bookmarked, sent to people, open on the second "
                   "screen at the desk")
    body = oc.get(f"/admin/bookings?room_id={second['id']}").get_data(as_text=True)
    shown = _names(body)
    s.check("?room_id= still filters", shown == {"OTHER", "NOW"},
            detail=f"{shown}")
    body = oc.get(f"/admin/bookings?q={TAG}+CONF").get_data(as_text=True)
    s.check("?q= still searches by name", _names(body) == {"CONF"},
            detail=f"{_names(body)}")
    # By REFERENCE, which is what somebody types off a card or an email. The
    # version of this that searched the guest's name passed with the search
    # narrowed to names only — it was testing that search worked at all.
    body = oc.get(f"/admin/bookings?q={TAG}-OTHER").get_data(as_text=True)
    # NOT by looking for the reference in the page: the search box renders the
    # term back into its own value attribute, so searching for a reference
    # "finds" it whether or not a single row matched. What proves the search
    # reached the reference column is that the OTHER bookings went away and the
    # empty state did not appear.
    s.check("and by reference code",
            "PEND" not in _names(body) and "CONF" not in _names(body),
            detail=f"{_names(body)} — the reference is the one thing a guest "
                   "reads out over the telephone")
    s.check("with something actually found",
            "No bookings match those filters" not in body,
            detail="an empty list also excludes everything, which is not the "
                   "same as having searched")

    s.section("And the new ones do too")
    body = oc.get("/admin/bookings?state=Pending").get_data(as_text=True)
    s.check("the status facet filters", _names(body) == {"PEND"},
            detail=f"{_names(body)}")
    # Somebody leaving TODAY is not here now: the room is being turned round.
    # With nothing in the fixture on that boundary the check could not tell a
    # correct comparison from an off-by-one.
    _stay("LEAVING", room=first, status="confirmed", arrival_offset=-2, nights=2)
    body = oc.get("/admin/bookings?when=Here+now").get_data(as_text=True)
    s.check("and so does who is here now", _names(body) == {"NOW"},
            detail=f"{_names(body)} — the one question this page is opened for "
                   "most mornings")
    s.check("somebody leaving today is not here now",
            f"{TAG}-LEAVING" not in body,
            detail="the room is being turned round, and counting them in gives "
                   "the housekeeper one more bed than there is")
    body = oc.get("/admin/bookings?when=Been+and+gone").get_data(as_text=True)
    s.check("and who has gone", "PAST" in _names(body) and "CONF" not in _names(body),
            detail=f"{_names(body)}")

    s.section("An old link and a new one do not fight")
    # The legacy parameter is mapped onto the facet. If both are present the
    # explicit one wins, because that is the one somebody just clicked.
    body = oc.get("/admin/bookings?status=pending&state=Confirmed").get_data(as_text=True)
    s.check("the facet wins",
            _names(body) == {"CONF", "OTHER", "PAST", "NOW", "LEAVING"},
            detail=f"{_names(body)} — otherwise clicking a chip on a page reached "
                   "from an old link appears to do nothing")

    s.section("Sorting")
    # With no sort asked for. Passing ?sort=arrival tests that the option works
    # and says nothing about what somebody sees when they simply open the page,
    # which is almost everybody.
    body = oc.get("/admin/bookings").get_data(as_text=True)
    default_order = [r for r in ("PAST", "LEAVING", "NOW", "PEND", "CONF", "OTHER")
                     if f"{TAG}-{r}" in body]
    default_pos = [body.index(f"{TAG}-{r}") for r in default_order]
    s.check("the page opens sorted by arrival", default_pos == sorted(default_pos),
            detail=f"{default_order} — a list with no order is a list somebody "
                   "has to read all of")

    body = oc.get("/admin/bookings?sort=arrival").get_data(as_text=True)
    order = [r for r in ("PAST", "LEAVING", "NOW", "PEND", "CONF", "OTHER")
             if f"{TAG}-{r}" in body]
    positions = [body.index(f"{TAG}-{r}") for r in order]
    s.check("soonest arrival first", positions == sorted(positions),
            detail=f"{order}")
    body = oc.get("/admin/bookings?sort=name").get_data(as_text=True)
    s.check("and by guest works too", "list-sort" in body and _names(body),
            detail="a sort that empties the list is worse than no sort")

    s.section("It can be saved like any other list now")
    oc.post("/views/save", data={"endpoint": "admin_bookings",
                                 "name": f"{TAG} Here now", "when": "Here now"},
            follow_redirects=True)
    body = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the saved view appears", f"{TAG} Here now" in body,
            detail="the whole reason for converting this page rather than "
                   "leaving it alone")
    body = oc.get("/admin/bookings?when=Here+now").get_data(as_text=True)
    s.check("and following it filters", _names(body) == {"NOW"},
            detail=f"{_names(body)}")

    s.section("What the page was already good at still works")
    body = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the counts band is intact", "Arriving today" in body or "counts" in body,
            detail="the conversion took the summary with it")
    # A booking with no email, made on purpose, so the flag has something to be
    # about. The version of this check that ended in "or True" could not fail —
    # which is worse than not checking, because it reads as cover.
    _stay("NOEMAIL", room=first, status="confirmed", arrival_offset=20)
    conn = db()
    conn.execute("UPDATE bookings SET guest_email = '' WHERE reference_code = ?",
                 (f"{TAG}-NOEMAIL",))
    conn.commit()
    conn.close()
    body = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("and a booking with no email is still flagged",
            "no email" in body.lower(),
            detail="the flag that tells reception who needs a printed card")

    s.section("Guards")
    s.check("an employee still cannot open it",
            ec.get("/admin/bookings").status_code in (302, 403))

    _cleanup()
    return s
