"""The customer's record: one click from any booking, and saying what matters.

The owner asked for "all the functionality of managing the customers on the
back end". Most of it existed; what was missing was the way in and two things
the record never said:

  - The registrations, dinners and events pages linked "full history" to an
    older page keyed on the email address, so the profile -- notes, what they
    owe across everything, what they have been sent -- was a page further off.
    Now each links to the person: their profile if they have one.
  - That older page matched stays on the address whatever its capitals, and
    everything else exactly, so "Ben@Chateau.com" had a stay and no dinners.
  - Whether somebody may be sent marketing email lived in two tables nothing
    showed, and what the house holds about them (the file they may ask for)
    was a search on another page rather than a link from their record.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZCR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM restaurant_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM newsletter_subscribers WHERE email LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (f"{TAG.lower()}%",))
    conn.commit()
    conn.close()


def run():
    s = Suite("The customer record")
    oc, ec, _owner, _emp = clients()
    _cleanup()
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    conn.execute("INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
                 (f"{TAG} Fontaine", f"{TAG.lower()}.fontaine@example.invalid", now))
    gid = conn.execute("SELECT id FROM guests WHERE name = ?", (f"{TAG} Fontaine",)).fetchone()["id"]
    conn.execute("INSERT INTO guests (name, email, created_at, merged_into_id) VALUES (?, ?, ?, ?)",
                 (f"{TAG} Old copy", f"{TAG.lower()}.merged@example.invalid", now, gid))
    # A dinner and a wedding under the same person, the address typed with capitals.
    conn.execute(
        """INSERT INTO restaurant_bookings (reference_code, manage_token, guest_name, guest_email,
           party_size, dinner_date, status, created_at)
           VALUES (?, ?, ?, ?, 2, ?, 'confirmed', ?)""",
        (f"{TAG}-D", f"tok{TAG}d".lower(), f"{TAG} Fontaine", f"{TAG}.Fontaine@Example.invalid",
         (house_today() + timedelta(days=4)).isoformat(), now))
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
           contact_email, contact_phone, preferred_date, guest_count, message, status, created_at)
           VALUES (?, ?, 'wedding', ?, ?, '', ?, 40, 'ZZ', 'new', ?)""",
        (f"{TAG}-E", f"tok{TAG}e".lower(), f"{TAG} Fontaine", f"{TAG}.FONTAINE@example.invalid",
         (house_today() + timedelta(days=200)).isoformat(), now))
    conn.commit()
    conn.close()

    s.section("From any booking to the person")
    r = oc.get("/guests/find", query_string={"email": f"{TAG}.FONTAINE@Example.invalid"})
    s.check("an address with a profile goes to the profile, whatever its capitals",
            r.status_code in (302, 303) and r.headers.get("Location", "").endswith(f"/guests/{gid}"),
            detail=f"HTTP {r.status_code} -> {r.headers.get('Location')}")
    r = oc.get("/guests/find", query_string={"email": f"{TAG.lower()}.merged@example.invalid"})
    s.check("an address only a merged-away profile had goes to the person it was merged into",
            (r.headers.get("Location") or "").endswith(f"/guests/{gid}"),
            detail=f"-> {r.headers.get('Location')}")
    r = oc.get("/guests/find", query_string={"email": f"{TAG.lower()}.nobody@example.invalid"})
    s.check("an address with no profile goes to its history",
            "/admin/bookings/guest/" in (r.headers.get("Location") or ""),
            detail=f"-> {r.headers.get('Location')}")
    r = ec.get("/guests/find", query_string={"email": f"{TAG.lower()}.fontaine@example.invalid"})
    s.check("and it is the owner's", r.status_code != 302 or f"/guests/{gid}" not in
            (r.headers.get("Location") or ""), detail=f"HTTP {r.status_code}")
    page = oc.get("/admin/restaurant").get_data(as_text=True)
    s.check("the restaurant links a dinner to the person", "/guests/find?email=" in page)

    s.section("The history finds everything under an address, however it was typed")
    page = oc.get(f"/admin/bookings/guest/{TAG.lower()}.fontaine@example.invalid").get_data(as_text=True)
    # By their dates, which nothing else on the page carries.
    dinner_on = (house_today() + timedelta(days=4))
    wedding_on = (house_today() + timedelta(days=200))
    s.check("the dinner typed with capitals is there",
            dinner_on.isoformat() in page or m.format_date_short(dinner_on.isoformat()) in page,
            detail="stays were matched whatever the capitals, and nothing else was")
    s.check("and so is the wedding",
            wedding_on.isoformat() in page or m.format_date_short(wedding_on.isoformat()) in page)

    s.section("The record says what they may be sent, and links to what we hold")
    page = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("somebody who never signed up says so", "Has not signed up to the newsletter" in page)
    conn = db()
    conn.execute("INSERT INTO newsletter_subscribers (email, token, source, confirmed_at, created_at) "
                 "VALUES (?, ?, 'site', ?, ?)",
                 (f"{TAG.lower()}.fontaine@example.invalid", f"{TAG}tok", now, now))
    conn.commit()
    conn.close()
    page = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("a subscriber says so", "Subscribed to the newsletter" in page)
    conn = db()
    conn.execute("INSERT INTO email_optouts (email, reason, created_at) VALUES (?, 'asked', ?)",
                 (f"{TAG.lower()}.fontaine@example.invalid", now))
    conn.commit()
    conn.close()
    page = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("and asking not to be sent marketing outranks a subscription",
            "Asked not to be sent marketing email" in page)
    s.check("with a link to everything the house holds about them",
            "/admin/data-requests?email=" in page)
    staff = ec.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("which a colleague is not shown",
            "What we hold about them" not in staff and "Asked not to be sent" not in staff)

    _cleanup()
    return s
