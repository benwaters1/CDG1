"""What a guest typed, after the form comes back at them.

book_room was fixed for this; the other three public forms were not. They lose
what was typed on every validation error, and the enquiry form loses it twice
over because its error path redirects, which cannot carry a form at all.

The fields that matter are not the name and the email — those are quick to
retype. They are the ones somebody writes carefully:

  - workshop registration: dietary notes, medical notes, who they want to
    share a room with, and what the occasion is
  - restaurant: dietary notes, which is the one field a guest with an allergy
    thinks about before typing
  - events: the message, which on a wedding enquiry is where somebody has
    described their day

Being asked to write your medical notes out a second time because the party
size was blank is the worst version of this on the site.

Two things are checked as hard as the returning: that `agree_terms` is never
handed back — re-ticking it is the entire point of it — and that a failed
submission leaves nothing behind in the database. The privacy notice makes
specific promises about dietary and medical notes, so keeping them for the
length of one form and no longer is part of the claim, not a detail.
"""
from datetime import date, timedelta

from _harness import Suite, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZTPRE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM restaurant_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _count(table, column, like=TAG + "%"):
    conn = db()
    try:
        return conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE {column} LIKE ?",
                            (like,)).fetchone()["c"]
    finally:
        conn.close()


def _session():
    conn = db()
    try:
        return conn.execute(
            """SELECT ws.id AS sid FROM workshop_sessions ws
               JOIN workshops w ON w.id = ws.workshop_id
               ORDER BY ws.start_date DESC LIMIT 1""").fetchone()
    finally:
        conn.close()


def run():
    s = Suite("What the guest typed comes back")
    _cleanup()
    c = m.app.test_client()
    soon = (house_today() + timedelta(days=60)).isoformat()

    s.section("The restaurant keeps the allergy")
    # The restaurant is switched off in this database, and /restaurant/book
    # 404s while it is — so every check below would pass on a page that was
    # never rendered. Turn it on for the length of this section and put the
    # setting back, then assert the route is actually reachable before
    # believing anything it says.
    conn = db()
    was_enabled = conn.execute(
        "SELECT enabled FROM restaurant_settings LIMIT 1").fetchone()["enabled"]
    conn.execute("UPDATE restaurant_settings SET enabled = 1")
    conn.commit()
    conn.close()
    s.check("the booking page is reachable at all",
            c.get("/restaurant/book").status_code == 200,
            detail="a 404 here would make every check in this section vacuous")
    body = {
        "guest_name": TAG + " Camille",
        "guest_email": "camille@example.invalid",
        "guest_phone": "+33 6 11 22 33 44",
        "dinner_date": "not-a-date",                 # the validation error
        "party_size": "4",
        "dietary_notes": "coeliac, and a severe nut allergy",
        "promo_code": "AUTUMNLIGHT",
    }
    r = c.post("/restaurant/book", data=body, follow_redirects=True)
    page = r.get_data(as_text=True)
    s.check("the form comes back rather than a redirect to an empty one",
            r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("the allergy is still in the box", "severe nut allergy" in page,
            detail="the one field a guest with an allergy writes carefully")
    s.check("and the promo code", "AUTUMNLIGHT" in page)
    s.check("their name came back", TAG + " Camille" in page)
    s.check("their phone came back", "+33 6 11 22 33 44" in page)
    s.check("and they were told what was wrong",
            any("date" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("nothing was written for a booking that failed",
            _count("restaurant_bookings", "guest_name") == 0)
    conn = db()
    conn.execute("UPDATE restaurant_settings SET enabled = ?", (was_enabled,))
    conn.commit()
    conn.close()

    s.section("The workshop keeps the medical notes")
    ses = _session()
    body = {
        "guest_name": TAG + " Dominique",
        "guest_email": "dominique@example.invalid",
        "guest_phone": "+33 6 55 44 33 22",
        "party_size": "",                            # the validation error
        "notes": "first time throwing a pot",
        "dietary_notes": "vegetarian, no dairy",
        "medical_notes": "epilepsy — carries medication",
        "special_occasion": "our tenth anniversary",
        "requested_roommate": "Sam Delacroix",
        "promo_code": "AUTUMNLIGHT",
    }
    r = c.post(f"/workshops/register/{ses['sid']}", data=body, follow_redirects=True)
    page = r.get_data(as_text=True)
    s.check("the form comes back", r.status_code == 200, detail=f"HTTP {r.status_code}")
    for label, needle in (
            ("the dietary notes", "vegetarian, no dairy"),
            ("the medical notes", "epilepsy — carries medication"),
            ("the occasion", "our tenth anniversary"),
            ("who they want to share with", "Sam Delacroix"),
            ("their own notes", "first time throwing a pot"),
            ("the promo code", "AUTUMNLIGHT")):
        s.check(f"{label} came back", needle in page,
                detail=f"{needle!r} was thrown away over a blank party size")
    s.check("nothing was written for a registration that failed",
            _count("workshop_bookings", "guest_name") == 0)

    s.section("The enquiry keeps the description of the day")
    # This one redirected, so everything was lost — and it is the form where
    # somebody has written the most.
    body = {
        "event_type": "not-a-real-type",             # the validation error
        "contact_name": TAG + " Elodie",
        "contact_email": "elodie@example.invalid",
        "contact_phone": "+33 6 77 88 99 00",
        "guest_count": "80",
        "preferred_date": soon,
        "alternate_date": (house_today() + timedelta(days=67)).isoformat(),
        "message": "A wedding in the courtyard, with dinner under the plane trees.",
    }
    r = c.post("/events/inquire", data=body, follow_redirects=True)
    page = r.get_data(as_text=True)
    s.check("the enquiry form comes back", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("the message is still there",
            "dinner under the plane trees" in page,
            detail="a redirect cannot carry a form, so this was lost outright")
    s.check("their name came back", TAG + " Elodie" in page)
    s.check("their phone came back", "+33 6 77 88 99 00" in page)
    s.check("the guest count came back", 'value="80"' in page)
    s.check("the preferred date came back", soon in page,
            detail="a wedding date, retyped because the type was wrong")
    s.check("the alternate date came back",
            (house_today() + timedelta(days=67)).isoformat() in page)
    s.check("nothing was written for an enquiry that failed",
            _count("event_inquiries", "contact_name") == 0)

    s.section("A valid enquiry still goes through")
    # Otherwise the section above passes just as well on a route that has
    # stopped accepting anything at all.
    good = dict(body, event_type="wedding", contact_name=TAG + " Fabienne")
    r = c.post("/events/inquire", data=good, follow_redirects=True)
    s.check("it is accepted", _count("event_inquiries", "contact_name") == 1,
            detail=str(flashes(r)))

    s.section("Agreeing to the terms is never remembered")
    # The one field that must NOT come back. A remembered tick is not
    # agreement, and it is the field that would be most convenient to keep.
    kept = m.public_form_prefill(
        {"guest_name": "x", "agree_terms": "on"}, m.WORKSHOP_PREFILL)
    s.check("agree_terms is not in what the workshop form hands back",
            not any("agree" in k for k in kept), detail=str(list(kept)))
    kept = m.public_form_prefill(
        {"agree_terms": "on"}, m.RESTAURANT_PREFILL)
    s.check("nor the restaurant's", not any("agree" in k for k in kept))
    s.check("nor the room form's, which is where the tick actually is",
            not any("agree" in k for k in m.book_room_prefill({"agree_terms": "on"})),
            detail=str(list(m.book_room_prefill({"agree_terms": "on"}))))

    s.section("Every name the template reads is supplied")
    # A prefill_* the route forgets is an undefined in Jinja: empty today, and
    # a silent hole the first time somebody puts a filter on it.
    import os
    import re
    tpl_dir = os.path.join(_harness.ROOT, "templates")
    for tpl, mapping in (("restaurant_book.html", m.RESTAURANT_PREFILL),
                         ("workshop_register.html", m.WORKSHOP_PREFILL),
                         ("events_info.html", m.EVENT_PREFILL)):
        wanted = set(re.findall(r"prefill_[a-z_]+",
                                open(os.path.join(tpl_dir, tpl), encoding="utf-8").read()))
        supplied = set(m.public_form_prefill({}, mapping))
        missing = sorted(wanted - supplied - {"prefill_email"})  # newsletter box
        s.check(f"{tpl} asks for nothing the route does not pass", not missing,
                detail=str(missing))

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
