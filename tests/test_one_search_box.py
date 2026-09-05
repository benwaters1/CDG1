"""One box that finds everything the house holds — including the stay.

The search page existed and covered fourteen kinds of record. It was fourteen
hand-written LIKE clauses in the route and fourteen hand-written blocks in the
template: the same list written twice, in two files, by hand.

Which is how the hole got there. Search a guest's name and you found their
profile, their dinner and their atelier place — and not their STAY. The room
booking is the record this house is actually about, the one every other record
hangs off, and it was the single kind the box could not find. Nothing errored;
the page looked complete, because a section with no results is invisible and a
section that does not exist looks exactly the same.

  ONE DECLARATION PER SOURCE. The query, the fields it searches, and how a hit
  is drawn, in one entry. Adding a record type is one entry rather than two
  blocks in two files that have to agree.

  AND A CHECK THAT EVERY DECLARED SOURCE ACTUALLY ANSWERS. This suite seeds one
  record in each of the kinds that matter and asks the box for it by name. A
  source that stops answering — a renamed column, a dropped join — is found
  here rather than by somebody who assumed the house had no record of it.

  IT SAYS WHEN IT STOPPED. A group that hits the limit is marked, because a
  list quietly showing twenty of eighty has answered a different question from
  the one that was asked.
"""
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZSB"
NEEDLE = "Quintaine"


def _cleanup():
    conn = db()
    for sql in (
        "DELETE FROM bookings WHERE guest_name LIKE ?",
        "DELETE FROM guests WHERE name LIKE ?",
        "DELETE FROM restaurant_bookings WHERE guest_name LIKE ?",
        "DELETE FROM event_inquiries WHERE contact_name LIKE ?",
        "DELETE FROM expenses WHERE description LIKE ?",
        "DELETE FROM vendors WHERE name LIKE ?",
        "DELETE FROM tasks WHERE title LIKE ?",
        "DELETE FROM waitlist_entries WHERE name LIKE ?",
    ):
        conn.execute(sql, (TAG + "%",))
    conn.commit()
    conn.close()


def _find(q):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.search_house(conn, q)
    finally:
        conn.close()


def _group(groups, key):
    for g in groups:
        if g["key"] == key:
            return g
    return None


def run():
    s = Suite("One search box for the house")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    now = m.datetime.now(m.timezone.utc).isoformat()
    arrival = m.house_today() + timedelta(days=170)
    departure = arrival + timedelta(days=2)

    with m.app.test_request_context("/"):
        ref, _token = m.create_booking(
            conn, room, f"{TAG} {NEEDLE}", "zzsb@example.invalid", "",
            arrival, departure, 2, "", [], payment_status="unpaid")
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (ref,)).fetchone()["id"]
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} {NEEDLE}", "zzsb@example.invalid", now))
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
                   guest_email, party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
        (TAG + "R1", TAG + "RT1", f"{TAG} {NEEDLE}", "zzsb@example.invalid",
         arrival.isoformat(), now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
                   contact_name, contact_email, status, created_at)
           VALUES (?, ?, 'wedding', ?, ?, 'new', ?)""",
        (TAG + "E1", TAG + "ET1", f"{TAG} {NEEDLE}", "zzsb@example.invalid", now))
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
                   doc_type, submitted_at)
           VALUES ('supplier_invoice', ?, ?, 240.0, 'pending', 'bill_to_pay', ?)""",
        (f"{TAG} {NEEDLE} Stone", f"{TAG} {NEEDLE} lime render", now))
    conn.execute(
        """INSERT INTO vendors (name, contact_person, notes, active, created_at)
           VALUES (?, ?, ?, 1, ?)""",
        (f"{TAG} {NEEDLE} Stone", TAG + " Contact",
         TAG + " they deliver to the lower gate only", now))
    conn.execute(
        "INSERT INTO tasks (title, status, created_at) VALUES (?, 'open', ?)",
        (f"{TAG} {NEEDLE} — repoint the terrace", now))
    conn.execute(
        """INSERT INTO waitlist_entries (name, email, status, created_at)
           VALUES (?, ?, 'open', ?)""",
        (f"{TAG} {NEEDLE}", "zzsb@example.invalid", now))
    conn.commit()
    conn.close()

    try:
        s.section("The stay, which is the one it could not find")
        groups = _find(NEEDLE)
        stays = _group(groups, "bookings")
        s.check("a room booking is found by the guest's name",
                stays is not None,
                detail="every other record type was searchable and this one "
                       "was not; found: " + str([g["key"] for g in groups]))
        s.check("and it is the right one",
                stays and any(ref in h["line"] for h in stays["hits"]),
                detail=str(stays["hits"] if stays else None))
        s.check("with somewhere to go from it",
                stays and stays["hits"][0]["href"].endswith(f"/{bid}/edit"),
                detail=stays["hits"][0]["href"] if stays else "")
        s.check("and by its reference code as well",
                _group(_find(ref), "bookings") is not None,
                detail="a guest rings and reads out the code; that is the "
                       "commonest way this box is used at all")

        s.section("And every other kind of record it claims to hold")
        # THE DRIFT GUARD. A renamed column or a dropped join makes a source
        # answer nothing, and a section with no results is invisible -- exactly
        # the way the stays hole hid for as long as it did.
        for key, what in (("guests", "the guest's own profile"),
                          ("restaurant_bookings", "their table"),
                          ("event_inquiries", "an event enquiry"),
                          ("expenses", "an invoice"),
                          ("vendors", "the supplier who sent it"),
                          ("tasks", "a task"),
                          ("waitlist", "somebody on the waiting list")):
            s.check(what, _group(groups, key) is not None,
                    detail="declared and answering nothing: "
                           + str([g["key"] for g in groups]))
        declared = _sources()
        s.check("and every declared source is asked, not just the ones seeded",
                len(declared) >= 16
                and {d["key"] for d in declared} >= {g["key"] for g in groups},
                detail="the declaration is the list, and a source dropped from "
                       "it is one nobody will notice is gone: "
                       + str([d["key"] for d in declared]))

        s.section("It searches more than the name")
        s.check("a supplier by who you speak to there",
                _group(_find(TAG + " Contact"), "vendors") is not None)
        s.check("and by what is written in their notes",
                _group(_find("lower gate only"), "vendors") is not None,
                detail="where a supplier delivers is written in the notes and "
                       "nowhere else, and it is the thing people search for")
        s.check("and a stay by the second person we write to",
                _second_contact_is_found(bid),
                detail="a PA rings about a booking they are copied on, and "
                       "their address is the only thing they can quote")

        s.section("Nothing matches nothing")
        s.check("a needle nobody has is no groups at all",
                _find("zzzz-nobody-has-this") == [],
                detail="an empty group would draw a heading over nothing")
        s.check("and an empty box is not a search",
                _find("") == [] and _find("   ") == [])

        s.section("The page draws what it was given")
        page = oc.get(f"/search?q={NEEDLE}").get_data(as_text=True)
        s.check("it renders", "Search" in page)
        s.check("the stay is on it", ref in page,
                detail="the route can find it and the page can still not draw it")
        s.check("under a heading that says what it is", "Stays" in page)
        s.check("and it says how many, across how many kinds",
                "matches for" in page and "kinds of record" in page,
                detail="a wall of headings with no count is a page you have "
                       "to read all of to know whether it found anything")
        s.check("an employee cannot search the house",
                ec.get(f"/search?q={NEEDLE}").status_code in (302, 403),
                detail="this reads guest notes, invoices and pay-adjacent "
                       "records in one place")

        s.section("And it says when it stopped rather than trailing off")
        conn = db()
        for i in range(25):
            conn.execute(
                "INSERT INTO tasks (title, status, created_at) VALUES (?, 'open', ?)",
                (f"{TAG} {NEEDLE} filler {i}", now))
        conn.commit()
        conn.close()
        tasks = _group(_find(NEEDLE), "tasks")
        s.check("a group over the limit is capped at twenty",
                tasks and len(tasks["hits"]) == 20,
                detail=str(len(tasks["hits"]) if tasks else None))
        s.check("and says so",
                tasks and tasks["capped"] is True,
                detail="twenty of eighty with no note is a different answer "
                       "from the one that was asked for")
        capped_page = oc.get(f"/search?q={NEEDLE}").get_data(as_text=True)
        s.check("on the page, in words",
                "Showing the first 20" in capped_page,
                detail="the flag is no use if the page does not draw it")
        s.check("and a group under the limit is not marked",
                _group(_find(NEEDLE), "vendors")["capped"] is False)
    finally:
        _cleanup()
    return s


def _sources():
    with m.app.test_request_context("/"):
        return m._search_sources()


def _second_contact_is_found(booking_id):
    conn = db()
    try:
        conn.execute(
            "UPDATE bookings SET second_contact_email = ? WHERE id = ?",
            ("zzsb.assistant@example.invalid", booking_id))
        conn.commit()
        with m.app.test_request_context("/"):
            groups = m.search_house(conn, "zzsb.assistant@example.invalid")
        return _group(groups, "bookings") is not None
    finally:
        conn.close()
