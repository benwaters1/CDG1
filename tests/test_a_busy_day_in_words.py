"""A busy day, read the way the house writes: dates in words, labels that are
true, one line a page, and a search hit that opens the thing it found.

WHAT WENT WRONG. Each of these rendered every word it was meant to and
errored nowhere, and none showed on the database the page sweep reads, because
each needs a day with people in it. Found on a seeded Thursday, 22 October 2026:

    "CANNOT EAT: Anniversary - could we have flowers in the room?" Arrivals
    joined the guest record's dietary notes and the booking's own request box
    and printed both under Cannot eat. Guests do type "no shellfish" in that
    box, which is why it is shown -- but as their words, not as an allergy.

    2026-10-29. The top-bar Search printed every stay, dinner and task date as
    a stamp; the Waitlist gave a Contact enquiry's month as "2026-12-01 →
    2027-01-01" and its note as "Around 2026-12"; the owner home's "Days with
    nobody on" named its day as a stamp (the other half of the same line did
    not); the kitchen's pass screen and the fire roll call too.

    A SEARCH HIT THAT OPENED A LIST. An event opened all events, an enquiry
    the whole waitlist.

    THE SAME PAGE TWICE. The reports index listed Events and "Event run
    sheet" -- one page under two names -- each with the same sentence.

    "AUTO-PREPPED 2 ARRIVING BOOKINGS". The owner home's own jargon for the
    checklist it had just made.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import (Suite, clients, contact_form_fields, db, ensure_room, flashes,
                      house_today, visible_text)
import _harness

m = _harness.m
TAG = "ZZWORDS"
ISO = re.compile(r"(?<![\w/+.-])20\d\d-\d\d(?:-\d\d)?(?![\w/+-])")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    # The checklist the owner home makes for a stay is titled by its room.
    conn.execute("DELETE FROM tasks WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE guest_name LIKE ?)", (f"{TAG}%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM waitlist_entries WHERE name LIKE ? OR email LIKE ?",
                 (f"{TAG}%", f"{TAG.lower()}%"))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (f"{TAG}%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (f"{TAG}%",))
    conn.commit()
    conn.close()


def _main(html):
    hit = re.search(r"<main\b.*?</main>", html, re.S)
    return hit.group(0) if hit else html


def _stay(conn, room_id, arrive, nights, name, email, requests=None, prepped=True):
    cur = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, created_at, source,
           special_requests, arrival_prepped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 600, ?, 'direct', ?, ?)""",
        (room_id, f"{TAG}{_harness.secrets_token()[:6].upper()}", _harness.secrets_token(), name, email,
         arrive.isoformat(), (arrive + timedelta(days=nights)).isoformat(), _now(),
         requests, _now() if prepped else None))
    return cur.lastrowid


def run():
    s = Suite("A busy day in the house's words")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = house_today()
    room = ensure_room()
    try:
        conn = db()
        # In the house now, so the pass screen and the roll call carry it.
        here = _stay(conn, room["id"], today - timedelta(days=1), 3, f"{TAG} Ellison",
                     "zzwords.ellison@example.invalid")
        # Arriving tomorrow: a request in the box, and a guest record that
        # says what they cannot eat.
        tomorrow = today + timedelta(days=1)
        asks = f"{TAG} Anniversary - could we have flowers in the room?"
        diet = f"{TAG} no nuts"
        arriving = _stay(conn, room["id"], tomorrow, 2, f"{TAG} Dufresne",
                         "zzwords.dufresne@example.invalid", requests=asks)
        conn.execute(
            "INSERT INTO guests (name, email, dietary_notes, created_at) VALUES (?, ?, ?, ?)",
            (f"{TAG} Dufresne", "zzwords.dufresne@example.invalid", diet, _now()))
        conn.execute(
            """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name,
               guest_email, party_size, dinner_date, status, created_at)
               VALUES (?, ?, ?, 'zzwords.dinner@example.invalid', 2, ?, 'confirmed', ?)""",
            (f"{TAG}DIN", _harness.secrets_token(), f"{TAG} Laurent", today.isoformat(), _now()))
        conn.execute(
            """INSERT INTO tasks (title, priority, due_date, status, created_at, origin)
               VALUES (?, 'normal', ?, 'open', ?, 'manual')""",
            (f"{TAG} call the piano tuner", tomorrow.isoformat(), _now()))
        cur = conn.execute(
            """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, guest_count, status, created_at, message)
               VALUES (?, ?, 'wedding', ?, 'zzwords.event@example.invalid', ?, 80, 'new', ?,
                       'A summer wedding.')""",
            (f"{TAG}EV", _harness.secrets_token(), f"{TAG} Charlotte",
             (today + timedelta(days=200)).isoformat(), _now()))
        event_id = cur.lastrowid
        # An enquiry as the Contact page stored it before the month was in
        # words: the whole month as its range, and the old note.
        first = (today.replace(day=1) + timedelta(days=62)).replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        cur = conn.execute(
            """INSERT INTO waitlist_entries (name, email, desired_arrival, desired_departure,
               notes, status, created_at, source)
               VALUES (?, 'zzwords.enquiry@example.invalid', ?, ?, ?, 'enquiry', ?, 'contact')""",
            (f"{TAG} Fontaine", first.isoformat(), nxt.isoformat(),
             f"Something else. Around {first.strftime('%Y-%m')}. Is the Family Suite free?",
             _now()))
        entry_id = cur.lastrowid
        conn.commit()
        conn.close()
        month = f"{first.strftime('%B')} {first.year}"

        s.section("Arrivals: what they cannot eat, and what they asked, apart")
        conn = db()
        with m.app.test_request_context("/"):
            sheet = m.arrivals_sheet(conn, tomorrow)
        conn.close()
        row = next((r for r in sheet["arriving"] if r["booking"]["id"] == arriving), None)
        s.check("the guest record's dietary notes are what they cannot eat",
                row is not None and row["dietary"] == diet, detail=str(row and row["dietary"]))
        s.check("and the booking's own box is not folded into it",
                row is not None and asks not in row["dietary"] and row["requests"] == asks)
        page = visible_text(_main(oc.get(f"/admin/arrivals?date={tomorrow.isoformat()}")
                                  .get_data(as_text=True)))
        s.check("on the sheet, Cannot eat says only that",
                f"Cannot eat {diet}" in page and f"Cannot eat {asks}" not in page
                and f"{diet} · {asks}" not in page, detail=page[page.find(diet) - 60:][:200])
        s.check("and the request is shown, as theirs", f"They asked {asks}" in page)

        s.section("Search: the house's dates, and the thing itself")
        for q, what in ((f"{TAG} Ellison", "a stay"), (f"{TAG} Laurent", "a dinner"),
                        (f"{TAG} call the piano", "a task"), (f"{TAG} Fontaine", "an enquiry"),
                        (f"{TAG} Charlotte", "an event")):
            hits = _main(oc.get("/search", query_string={"q": q}).get_data(as_text=True))
            text = visible_text(hits)
            s.check(f"{what}: its date in words, not as a stamp",
                    q.split()[1] in text and not ISO.search(text),
                    detail=str(ISO.findall(text)[:3]))
        hits = _main(oc.get("/search", query_string={"q": f"{TAG} Fontaine"}).get_data(as_text=True))
        s.check("an enquiry found opens the enquiry",
                f'href="/admin/waitlist?when=all#entry-{entry_id}"' in hits)
        s.check("its month as a month", month in visible_text(hits), detail=month)
        hits = _main(oc.get("/search", query_string={"q": f"{TAG} Charlotte"}).get_data(as_text=True))
        s.check("an event found opens the event, not the list of them",
                f'href="/admin/events/{event_id}"' in hits)

        s.section("The Waitlist: a Contact enquiry's month, as a month")
        wl = visible_text(_main(oc.get("/admin/waitlist?when=all").get_data(as_text=True)))
        at = wl.find(f"{TAG} Fontaine")
        card = wl[at:at + 400]
        s.check("the dates it wants read as the month", month in card and not ISO.search(card),
                detail=card[:200])
        s.check("an older note's month is read in words too",
                f"Around {month}" in card, detail=card[:200])
        fields = contact_form_fields()
        fields.update({"name": f"{TAG} Lou", "email": "zzwords.lou@example.invalid",
                       "topic": "other", "rough_month": first.strftime("%Y-%m"),
                       "message": "Four nights, two children."})
        m.app.test_client().post("/contact", data=fields)
        conn = db()
        saved = conn.execute("SELECT notes FROM waitlist_entries WHERE email = ?",
                             ("zzwords.lou@example.invalid",)).fetchone()
        conn.close()
        s.check("and a new one is written in words from the start",
                saved is not None and f"Around {month}" in (saved["notes"] or ""),
                detail=str(saved and saved["notes"]))

        s.section("The pass, the roll call and the owner home")
        for path in ("/pass", "/roll-call"):
            text = visible_text(_main(oc.get(path).get_data(as_text=True)))
            at = text.find(f"{TAG} Ellison")
            near = text[max(0, at - 120):at + 200] if at >= 0 else ""
            s.check(f"{path}: the guest in the house, with their dates in words",
                    at >= 0 and not ISO.search(near), detail=near[:200])
        gap_day = (today + timedelta(days=5)).isoformat()
        real = m.cover_gaps
        try:
            m.cover_gaps = lambda *_a, **_k: [{"date": gap_day, "uncovered": True,
                                               "in_house": 2, "arrivals": 1}]
            conn = db()
            with m.app.test_request_context("/"):
                warnings = m.owner_home_warnings(conn, today)
            conn.close()
        finally:
            m.cover_gaps = real
        gap = next((w for w in warnings if w["title"] == "Days with nobody on"), None)
        s.check("Days with nobody on names its day in words",
                gap is not None and m.format_date_short(gap_day) in gap["detail"]
                and gap_day not in gap["detail"], detail=str(gap and gap["detail"]))

        s.section("The reports index: one line a page")
        idx = _main(oc.get("/reports").get_data(as_text=True))
        links = re.findall(r'<td[^>]*>\s*<a href="([^"]+)"><strong>', idx)
        twice = sorted({u for u in links if links.count(u) > 1})
        s.check("no page listed twice", links and not twice, detail=str(twice))
        s.check("a second name for a page is said beside it",
                "also: Event run sheet" in visible_text(idx))
        found = visible_text(_main(oc.get("/reports", query_string={"q": "run sheet"})
                                   .get_data(as_text=True)))
        s.check("and still found by searching for it", "Event run sheet" in found)
        s.check("the calendar described in the house's words, not a developer's",
                "it's the" not in visible_text(idx)
                and "The house's calendar by day, week or month" in visible_text(idx))

        s.section("The checklist the owner home makes, said in words")
        conn = db()
        _stay(conn, room["id"], tomorrow, 1, f"{TAG} Gallagher",
              "zzwords.gallagher@example.invalid", prepped=False)
        conn.commit()
        conn.close()
        said = " ".join(flashes(oc.get("/")))
        s.check("no 'Auto-prepped'", "Auto-prepped" not in said, detail=said[:160])
        s.check("it says what was made, and for whom",
                "arriving soon: the checklist to get" in said, detail=said[:160])
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
