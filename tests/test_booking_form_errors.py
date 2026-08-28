"""What a guest sees when a booking does not go through.

The validation in book_room() is genuinely thorough — past dates, occupancy,
minimum nights, email, terms, house capacity, promo codes and a Stripe failure
are all handled. Two things about the FAILURE path were not.

  1. Reversed dates were reported as a minimum-stay problem. `(departure -
     arrival).days` is negative when the dates are the wrong way round, and
     negative is less than min_nights, so the guest was told "this room
     requires a minimum stay of 2 nights". They try three nights, still
     backwards, and get the same answer — with nothing on the page pointing at
     what is actually wrong.

  2. The re-render threw away everything except the dates. The template has
     always read prefill_name, prefill_email, prefill_phone,
     prefill_party_size, prefill_requests and prefill_promo; the route never
     passed any of them. So a guest who mistyped an email or missed the terms
     box got a blank form back and re-typed the lot — on the one page in the
     app where giving up costs a booking.

agree_terms is deliberately NOT carried back, and that is checked here too. A
remembered tick is not agreement: they should agree on the submission that
actually goes through.
"""
from datetime import date, timedelta

from _harness import Suite, db, flashes
import _harness

m = _harness.m


def _extra_id():
    conn = db()
    try:
        row = conn.execute("SELECT id, name FROM extras WHERE active = 1 LIMIT 1").fetchone()
        return (row["id"], row["name"]) if row else (None, None)
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE 'ZZFORM%'")
    conn.execute("DELETE FROM submission_log")
    conn.commit()
    conn.close()


def run():
    s = Suite("Booking form errors")
    _cleanup()
    room = _harness.ensure_room()
    pub = m.app.test_client()
    good_in = (date.today() + timedelta(days=60)).isoformat()
    good_out = (date.today() + timedelta(days=63)).isoformat()
    extra_id, extra_name = _extra_id()

    typed = {
        "guest_name": "ZZFORM Jane Traveller",
        "guest_email": "jane@example.invalid",
        "guest_phone": "+33 6 12 34 56 78",
        "party_size": "2",
        "special_requests": "a quiet room away from the stairs if possible",
        "promo_code": "SPRINGLIGHT",
        "agree_terms": "on",
    }

    s.section("Dates the wrong way round say so")
    r = pub.post(f"/book/{room['id']}",
                 data=dict(typed, arrival_date=good_out, departure_date=good_in))
    said = " ".join(flashes(r)).lower()
    s.check("the message names the real problem", "wrong way round" in said
            or "after your arrival" in said, detail=f"{flashes(r)[:1]}")
    s.check("and does not blame the minimum stay", "minimum stay" not in said,
            detail=f"{flashes(r)[:1]} — three nights backwards gets the same "
                   "answer, and nothing points at the dates")

    s.section("Same-day is not a minimum-stay problem either")
    r = pub.post(f"/book/{room['id']}",
                 data=dict(typed, arrival_date=good_in, departure_date=good_in))
    said = " ".join(flashes(r)).lower()
    s.check("it is refused", bool(flashes(r)))
    s.check("and explained as the dates, not the room's rules",
            "after your arrival" in said or "wrong way round" in said,
            detail=f"{flashes(r)[:1]}")

    s.section("A real minimum-stay problem still says minimum stay")
    # The new branch must not swallow the one it sits in front of.
    conn = db()
    conn.execute("UPDATE rooms SET min_nights = 3 WHERE id = ?", (room["id"],))
    conn.commit()
    conn.close()
    r = pub.post(f"/book/{room['id']}", data=dict(
        typed, arrival_date=good_in,
        departure_date=(date.today() + timedelta(days=61)).isoformat()))
    said = " ".join(flashes(r)).lower()
    s.check("one night against a three-night minimum is reported as such",
            "minimum stay" in said, detail=f"{flashes(r)[:1]}")
    conn = db()
    conn.execute("UPDATE rooms SET min_nights = 1 WHERE id = ?", (room["id"],))
    conn.commit()
    conn.close()

    s.section("A rejected booking hands back what the guest typed")
    data = dict(typed, arrival_date=good_in, departure_date=good_out,
                guest_email="not-an-email")
    if extra_id:
        data["extras"] = str(extra_id)
    r = pub.post(f"/book/{room['id']}", data=data)
    body = r.get_data(as_text=True)
    s.check("the form comes back, not an error page", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    for label, needle in (("their name", "ZZFORM Jane Traveller"),
                          ("their phone", "+33 6 12 34 56 78"),
                          ("their requests", "a quiet room away from the stairs"),
                          ("the promo code", "SPRINGLIGHT")):
        s.check(f"{label} is still there", needle in body,
                detail=f"{needle!r} was thrown away — they have to type it again")
    s.check("and the dates they chose", good_in in body and good_out in body)
    if extra_id:
        # Looking for "checked" anywhere on the page is not enough — the word
        # appears on other controls, so that version passed with the prefill
        # removed entirely. It has to be THIS extra's own input.
        import re as _re
        mark = _re.search(
            r'id="extra_%d"[^>]*' % extra_id, body)
        s.check("the extra they picked is still ticked",
                bool(mark) and "checked" in mark.group(0),
                detail=f"{extra_name!r} was unticked by the failed submission — "
                       f"{mark.group(0)[:90] if mark else 'input not found'}")

    s.section("But the terms box is not re-ticked for them")
    # A remembered tick is not agreement.
    s.check("agree_terms comes back empty",
            'name="agree_terms"' in body and 'name="agree_terms" checked' not in body
            and "agree_terms\" checked" not in body,
            detail="an earlier tick was remembered on the guest's behalf")

    s.section("The same holds for the other ways it can fail")
    for label, override in (
        ("no terms agreed", {"agree_terms": ""}),
        ("a party too large", {"party_size": "99"}),
        ("an arrival in the past",
         {"arrival_date": (date.today() - timedelta(days=5)).isoformat()}),
    ):
        d = dict(typed, arrival_date=good_in, departure_date=good_out)
        d.update(override)
        rr = pub.post(f"/book/{room['id']}", data=d)
        b = rr.get_data(as_text=True)
        s.check(f"{label}: their name survives", "ZZFORM Jane Traveller" in b,
                detail="a different failure path still wipes the form")

    s.section("And nothing was booked by any of it")
    conn = db()
    made = conn.execute(
        "SELECT COUNT(*) AS c FROM bookings WHERE guest_name LIKE 'ZZFORM%'").fetchone()["c"]
    conn.close()
    s.check("no booking was created", made == 0, detail=f"{made} created")

    _cleanup()
    return s
