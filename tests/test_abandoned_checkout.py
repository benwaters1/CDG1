"""Coming back from a card payment the guest changed their mind about.

`cancel_url` carried only `room_id`, so Stripe returned the guest to book_room
as a plain GET and the form was empty. They had filled in everything AND
decided to pay — a worse loss than a validation error, and on the page where
giving up costs a booking outright.

The obvious fix is the wrong one. Putting a name, email and phone in the
cancel_url writes them into Stripe's referrer header, into the access log of
every proxy between here and there, and into the guest's browser history — for
a booking that never happened. This app already refuses that trade: the Outlook
add-in endpoints read their token from the POST body for the same reason. So
what the guest typed goes into their OWN SESSION on the way out and is handed
back on the way in, and the URL still carries nothing but room_id.

Three things this pins:

  - it comes back, all of it, including the extras they had ticked
  - it is used ONCE. Left in the session it would refill the form days later,
    possibly on a shared computer, with details of a booking somebody decided
    against
  - a completed payment drops it too, so a booking that went through cannot
    reappear as a half-filled form
"""
from datetime import date, timedelta

from _harness import Suite, db
import _harness

m = _harness.m


def _extra_id():
    """A real extra's id, rather than assuming there is one with id 1.

    The suite runs against a copy of the live database, where extra 1 has been
    deleted — so the ticked-extra check looked for id="extra_1", found no such
    input, and failed. It passes on a fresh seeded database, which is why it
    was not caught. Returns None if the house sells no extras at all.
    """
    conn = db()
    try:
        row = conn.execute(
            "SELECT id FROM extras WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _stash(client, room_id, **over):
    data = {
        "room_id": room_id,
        "arrival": (date.today() + timedelta(days=70)).isoformat(),
        "departure": (date.today() + timedelta(days=73)).isoformat(),
        "guest_name": "ZZABAN Amelie Returning",
        "guest_email": "amelie@example.invalid",
        "guest_phone": "+33 6 99 88 77 66",
        "party_size": "2",
        "special_requests": "a cot for the baby if one can be found",
        "promo_code": "AUTUMNLIGHT",
        "extras": [_extra_id()] if _extra_id() else [],
    }
    data.update(over)
    with client.session_transaction() as sess:
        sess["abandoned_booking"] = data
    return data


def run():
    s = Suite("Abandoned checkout")
    room = _harness.ensure_room()
    other = None
    conn = db()
    row = conn.execute("SELECT id FROM rooms WHERE id != ? AND active = 1 LIMIT 1",
                       (room["id"],)).fetchone()
    other = row["id"] if row else None
    conn.close()

    s.section("Stripe sends them back with a word about what happened")
    c = m.app.test_client()
    r = c.get(f"/book/stripe-cancel?room_id={room['id']}", follow_redirects=True)
    body = r.get_data(as_text=True)
    s.check("the cancel route lands on the booking form", r.status_code == 200,
            detail=f"HTTP {r.status_code}")
    s.check("and says nothing was booked",
            "no booking was made" in body.lower() or "cancelled" in body.lower(),
            detail="a guest who backs out of payment must not be left assuming "
                   "it went through")

    s.section("And with everything they had typed")
    c = m.app.test_client()
    stashed = _stash(c, room["id"])
    body = c.get(f"/book/{room['id']}").get_data(as_text=True)
    for label, needle in (("their name", stashed["guest_name"]),
                          ("their email", stashed["guest_email"]),
                          ("their phone", stashed["guest_phone"]),
                          ("their requests", "a cot for the baby"),
                          ("the promo code", "AUTUMNLIGHT"),
                          ("the arrival date", stashed["arrival"]),
                          ("the departure date", stashed["departure"])):
        s.check(f"{label} came back", needle in body,
                detail=f"{needle!r} was lost on the way back from Stripe")
    import re
    extra_id = _extra_id()
    mark = re.search(rf'id="extra_{extra_id}"[^>]*', body) if extra_id else None
    s.check("and the extra they had ticked is still ticked",
            bool(mark) and "checked" in mark.group(0),
            detail=mark.group(0)[:80] if mark else "input not found")

    s.section("None of it travels in the URL")
    # The whole reason it is in the session. A name and email in a query string
    # reach Stripe's referrer, the proxy logs and the browser history.
    import io as _io, os as _os
    src = _io.open(_os.path.join(_harness.ROOT, "app.py"),
                   encoding="utf-8", errors="replace").read()
    cancel_line = next((l for l in src.split("\n")
                        if 'cancel_url=url_for("stripe_cancel"' in l), "")
    s.check("the cancel_url is built", bool(cancel_line))
    for leaked in ("guest_name", "guest_email", "guest_phone", "name=", "email="):
        s.check(f"it does not carry {leaked!r}", leaked not in cancel_line,
                detail=f"{cancel_line.strip()[:110]}")

    s.section("It is for one return trip only")
    with c.session_transaction() as sess:
        s.check("the stash is dropped once used", "abandoned_booking" not in sess)
    again = c.get(f"/book/{room['id']}").get_data(as_text=True)
    s.check("a second visit is a clean form",
            "ZZABAN Amelie Returning" not in again,
            detail="the form refilled itself later with a booking they "
                   "decided against")

    s.section("It only refills the room it belongs to")
    if other:
        c2 = m.app.test_client()
        _stash(c2, room["id"])
        cross = c2.get(f"/book/{other}").get_data(as_text=True)
        s.check("another room's form is not filled in",
                "ZZABAN Amelie Returning" not in cross,
                detail="one room's abandoned form leaked onto another")

    s.section("Paying drops it too")
    # A completed booking must not come back as a half-filled form.
    c3 = m.app.test_client()
    _stash(c3, room["id"])
    c3.get("/book/stripe-success?session_id=nothing-real")
    with c3.session_transaction() as sess:
        s.check("a successful return clears the stash",
                "abandoned_booking" not in sess,
                detail="a booking that went through can reappear as a form")

    s.section("A link from the room list still behaves as it did")
    # The query string wins where it is present, so the ordinary path through
    # the site is unchanged.
    c4 = m.app.test_client()
    _stash(c4, room["id"])
    body = c4.get(f"/book/{room['id']}?name=Someone+Else").get_data(as_text=True)
    s.check("an explicit name in the URL wins", "Someone Else" in body,
            detail="the stash overrode what the link asked for")

    s.section("And nothing breaks with no stash at all")
    c5 = m.app.test_client()
    r5 = c5.get(f"/book/{room['id']}")
    s.check("the form renders", r5.status_code == 200, detail=f"HTTP {r5.status_code}")
    s.check("and is empty", "ZZABAN" not in r5.get_data(as_text=True))

    return s
