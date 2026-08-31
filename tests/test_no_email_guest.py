"""The guest who never gave an email address.

Reception books somebody in at the door and they do not leave an address —
there is a whole form for that now, and it is right that a booking with a name
is worth more than no booking. But everything the house sends a guest travels by
email, so that guest had NO ROUTE to their own booking at all:

  - /book/manage required the reference AND the email on the booking, so the one
    way in was closed to exactly the guests who had no email on it;
  - the account link goes by email;
  - the balance chase goes by email;
  - the statement goes by email.

The booking existed and its owner could not reach it. Two things fix it, and
both are checked here.

FINDING A BOOKING BY SURNAME. The reference is the credential either way — GUD-
plus six characters from secrets.choice, about two billion of them — and the
route is rate-limited per hour. A surname is no weaker a second factor than an
email address, which is not secret either. What must NOT work is the reference
on its own, and that is the check that matters in the first section: a route
that accepts a reference alone turns two billion into one field somebody can
paste from a photograph of a card.

A CARD TO HAND OVER. Carrying the reference and the surname, because those are
the two things the find page now takes. A manage token is forty characters and
nobody types that off a card.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZNOMAIL"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM submission_log WHERE action = 'find_booking'")
    conn.commit()
    conn.close()


def _clear_throttle():
    """Give the next section a fresh allowance.

    /book/manage allows five attempts an hour from one address, which this suite
    exhausts around its sixth POST — so everything after that returned the
    throttle page and four checks failed for a reason that had nothing to do
    with what they were testing. Cleared between sections, and tested on
    purpose in its own section rather than tripped over.
    """
    conn = db()
    conn.execute("DELETE FROM submission_log WHERE action = 'find_booking'")
    conn.commit()
    conn.close()


def _stay(ref, *, email="", surname="Vaugirard", paid=0.0):
    conn = db()
    room = _harness.ensure_room()
    arrival = date.today() + timedelta(days=2)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 500, ?, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(),
         f"{TAG} {surname}", email, arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(), paid,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("The guest with no email")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    walkin = _stay("A")                       # no email at all
    online = _stay("B", email="zznomail.b@example.invalid", surname="Peyrat")

    s.section("A reference on its own gets nobody in")
    # The check that matters. Two billion references is only worth two billion
    # while a second field is required alongside.
    r = anon.post("/book/manage", data={"reference_code": walkin["reference_code"]},
                  follow_redirects=False)
    s.check("it does not redirect to anybody's booking",
            r.status_code == 200,
            detail=f"HTTP {r.status_code} → {r.headers.get('Location')} — a "
                   "reference alone is one field off a photograph of a card")
    body = anon.post("/book/manage", data={"reference_code": walkin["reference_code"]},
                     follow_redirects=True).get_data(as_text=True)
    s.check("and the token is nowhere on the page",
            walkin["manage_token"] not in body)
    s.check("it says what else is needed",
            "surname" in body.lower() or "email" in body.lower(),
            detail="refused with nothing to act on")

    _clear_throttle()
    s.section("The walk-in gets in with the reference and their surname")
    r = anon.post("/book/manage",
                  data={"reference_code": walkin["reference_code"],
                        "surname": "Vaugirard"}, follow_redirects=False)
    s.check("it lets them through", r.status_code in (302, 303),
            detail=f"HTTP {r.status_code} — the only way in was closed to exactly "
                   "the guests with no email on the booking")
    s.check("to their own booking",
            walkin["manage_token"] in (r.headers.get("Location") or ""),
            detail=f"{r.headers.get('Location')}")

    _clear_throttle()
    s.section("Case and spacing do not lock them out")
    for label, data in (
        ("lower-case reference", {"reference_code": walkin["reference_code"].lower(),
                                  "surname": "Vaugirard"}),
        ("shouted surname", {"reference_code": walkin["reference_code"],
                             "surname": "VAUGIRARD"}),
        ("surname with spaces", {"reference_code": walkin["reference_code"],
                                 "surname": "  Vaugirard  "}),
    ):
        r = anon.post("/book/manage", data=data, follow_redirects=False)
        s.check(f"{label} works", r.status_code in (302, 303),
                detail=f"HTTP {r.status_code} — somebody reading it off a card "
                       "types what they see")

    _clear_throttle()
    s.section("A French surname in capitals still gets in")
    # SQLite's LOWER and LIKE are both ASCII-only, so BÉATRICE did not match a
    # stored Béatrice and ROUVIÈRE did not match Rouvière. French forms are
    # routinely filled in capitals — it is the convention on their own identity
    # documents — so this is not an edge case, it is how a good share of guests
    # will type it. Compared with casefold in Python now.
    # Stored in CAPITALS, which is what reception types off a French identity
    # card and what the walk-in form therefore records. That is the direction
    # that breaks: SQLite's LOWER leaves È alone, so LOWER('ROUVIÈRE') is
    # 'rouviÈre' and never matches a casefolded 'rouvière'. Stored title-case it
    # works either way, which is why the first version of this check could not
    # tell the fix from its absence.
    accented = _stay("C", surname="ROUVIÈRE")
    for label, typed in (("as printed", "Rouvière"),
                         ("in capitals, as on a French form", "ROUVIÈRE"),
                         ("all lower case", "rouvière")):
        _clear_throttle()
        r = anon.post("/book/manage",
                      data={"reference_code": accented["reference_code"],
                            "surname": typed}, follow_redirects=False)
        s.check(f"{label} works", r.status_code in (302, 303),
                detail=f"HTTP {r.status_code} — {typed!r} against stored "
                       "stored 'ROUVIÈRE'; SQLite's LOWER leaves the "
                       "accent alone, so SQL matching refuses this")

    _clear_throttle()
    s.section("Somebody else's surname does not")
    r = anon.post("/book/manage",
                  data={"reference_code": walkin["reference_code"],
                        "surname": "Peyrat"}, follow_redirects=False)
    s.check("refused", r.status_code == 200, detail=f"HTTP {r.status_code}")
    r = anon.post("/book/manage",
                  data={"reference_code": "GUD-000000", "surname": "Vaugirard"},
                  follow_redirects=False)
    s.check("and a reference nobody holds is refused too", r.status_code == 200,
            detail=f"HTTP {r.status_code}")

    _clear_throttle()
    s.section("The email route still works, for the guests who used it")
    r = anon.post("/book/manage",
                  data={"reference_code": online["reference_code"],
                        "email": "zznomail.b@example.invalid"}, follow_redirects=False)
    s.check("unchanged", r.status_code in (302, 303), detail=f"HTTP {r.status_code}")
    s.check("to the right booking",
            online["manage_token"] in (r.headers.get("Location") or ""))
    r = anon.post("/book/manage",
                  data={"reference_code": online["reference_code"],
                        "email": "ZZNOMAIL.B@EXAMPLE.INVALID"}, follow_redirects=False)
    s.check("and a shouted address still matches", r.status_code in (302, 303),
            detail=f"HTTP {r.status_code}")

    s.section("And five wrong guesses an hour is all anybody gets")
    # The reference is two billion, but only while somebody cannot sit and try.
    _clear_throttle()
    for _ in range(6):
        anon.post("/book/manage", data={"reference_code": "GUD-ZZZZZZ",
                                        "surname": "Nobody"}, follow_redirects=True)
    r = anon.post("/book/manage",
                  data={"reference_code": walkin["reference_code"],
                        "surname": "Vaugirard"}, follow_redirects=True)
    s.check("a correct one is refused once the limit is hit",
            walkin["manage_token"] not in r.get_data(as_text=True),
            detail="somebody can sit and guess references all afternoon")
    s.check("and is told to wait rather than told nothing",
            any("too many" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")
    _clear_throttle()

    s.section("The card reception hands over")
    r = oc.get(f"/admin/bookings/{walkin['id']}/card")
    s.check("it opens", r.status_code == 200, detail=f"HTTP {r.status_code}")
    card = r.get_data(as_text=True)
    s.check("with the reference on it in full",
            walkin["reference_code"] in card)
    # Anchored to the instruction, not to the page. "Vaugirard" also appears
    # in the guest's full name at the top, so `"Vaugirard" in card` passed even
    # with the instruction reduced to "the surname of the booking".
    s.check("the surname they will be asked for is named in the instruction",
            "<strong>Vaugirard</strong>" in card,
            detail="the card tells them to enter a surname and not which one")
    s.check("and where to go", "/book/manage" in card,
            detail="a reference with nowhere to type it")
    s.check("it does NOT print the manage token",
            walkin["manage_token"] not in card,
            detail="forty characters nobody types, on a card somebody may lose")
    s.check("what they still owe is on it", "500" in card or "Still to pay" in card,
            detail="a guest handed a card with no figure on it asks at breakfast")
    s.check("and the furniture is marked not to print", "no-print" in card)
    s.check("with a rule behind it, or it prints anyway",
            "no-print{ display:none" in card.replace("\\r", ""),
            detail="the class is on the markup and does nothing")

    s.section("A booking with no email is flagged where reception will see it")
    listing = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the list offers the card", f"/admin/bookings/{walkin['id']}/card" in listing,
            detail="the page exists and nothing links to it")
    s.check("and says which bookings have no email", "no email" in listing.lower(),
            detail="the ones that need a card look like the ones that do not")

    s.section("Guards")
    s.check("an employee cannot print somebody's card",
            ec.get(f"/admin/bookings/{walkin['id']}/card").status_code in (302, 403))
    s.check("a booking that does not exist is a 404",
            oc.get("/admin/bookings/999999/card").status_code == 404)

    _cleanup()
    return s
