"""Two things the house records about a guest and never looks at again.

A KEY HANDED TO A GUEST. `booking_access_codes` is written by two routes and
read by exactly one query — on the arrivals sheet, filtered to bookings
*arriving*. So the key for somebody who left three weeks ago is on no page in
the building, and `good_until`, which the issue route sets to their departure
date, was read by nothing at all.

The register that should hold this already existed. `things_still_out` covers
staff key holdings and lent kit, and its own docstring says why one outranks
the other: a key held by somebody who has left "is a person who can still open
a door". A guest who checked out with a key is that sentence about a different
person, so it goes on the same register rather than a second page. Two lists
of what the house has lent would eventually disagree, and the one nobody
opened would be the one holding the key.

KEYS, NOT CODES. A key is an object and comes back or does not. A code is
revoked, not returned, and blanking it is a nightly job's business. Listing
every code ever issued as "still out" would bury the keys that are.

BEING PHOTOGRAPHED. `photo_consent` has three values and a `photo_consent_at`
stamp. Both are written by the guest form and the only place either is read
back is that same form, showing the guest their own answer. Nobody who might
take or publish a photograph ever saw it — which is worse than not asking,
because asking creates an expectation that the answer will be honoured.

It is not a block, and the test insists on that. The software cannot tell who
is in a photograph, so a rule that refused would refuse the wrong ones and be
switched off inside a week. It says who is in the house and has declined, at
the moment somebody is about to publish, and leaves the judgement with a
person. The privacy notice now says so, which is a claim this suite checks.
"""
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room

import _harness

m = _harness.m
TAG = "keytest-"


def _cleanup(conn):
    conn.execute("DELETE FROM booking_access_codes WHERE booking_id IN "
                 "(SELECT id FROM bookings WHERE reference_code LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("A guest's key, and a guest who would rather not be photographed")
    oc, ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()
    room = ensure_room()["id"]   # ensure_room hands back the row

    def booking(ref, name, email, arrive, depart, status="confirmed"):
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token,
                       guest_name, guest_email, arrival_date, departure_date,
                       party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 400, ?)""",
            (room, TAG + ref, (TAG + ref).lower(), name, email,
             arrive.isoformat(), depart.isoformat(), status, now.isoformat()))
        conn.commit()
        return conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                            (TAG + ref,)).fetchone()["id"]

    # Somebody who left a fortnight ago with a key.
    gone = booking("GONE", TAG + " Departed", TAG + "gone@example.invalid",
                   today - timedelta(days=17), today - timedelta(days=14))
    # Somebody here now with a key, which is fine.
    here = booking("HERE", TAG + " Staying", TAG + "here@example.invalid",
                   today - timedelta(days=1), today + timedelta(days=3))

    def key(booking_id, value, kind, good_until, returned=None):
        conn.execute(
            """INSERT INTO booking_access_codes (booking_id, kind, value,
                       issued_to, issued_at, good_until, returned_at,
                       issued_by_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (booking_id, kind, value, TAG + " holder",
             (now - timedelta(days=17)).isoformat(), good_until.isoformat(),
             returned.isoformat() if returned else None, owner["id"]))
        conn.commit()

    key(gone, TAG + "big-iron-key", "key", today - timedelta(days=14))
    key(here, TAG + "current-key", "key", today + timedelta(days=3))
    key(gone, TAG + "returned-key", "key", today - timedelta(days=14),
        returned=now - timedelta(days=13))
    key(gone, TAG + "door-code", "code", today - timedelta(days=14))

    data = m.things_still_out(conn, today=today)
    mine = {r["what"].replace(TAG, ""): r for r in data["lent"]
            if str(r["what"]).startswith(TAG)}

    s.section("A key that left with a guest is on the register")
    s.check("it is there at all", "big-iron-key" in mine,
            detail=str(sorted(mine)))
    s.check("as a guest key rather than staff kit",
            mine.get("big-iron-key", {}).get("kind") == "guest key",
            detail=str(mine.get("big-iron-key", {}).get("kind")))
    s.check("with the name it was issued to",
            mine.get("big-iron-key", {}).get("who") == TAG + " holder")
    s.check("and who issued it",
            bool(mine.get("big-iron-key", {}).get("issued_by")),
            detail="on a key register, who authorised this is half the point")

    s.section("The one it should worry about is the one past its date")
    s.check("a guest who has gone is flagged the way a leaver is",
            mine.get("big-iron-key", {}).get("gone") is True,
            detail="good_until is the departure date, written by the issue "
                   "route since it was built and read by nothing until now")
    s.check("a guest still in the house is not",
            mine.get("current-key", {}).get("gone") is False,
            detail="a key in the room it belongs to is not a finding")
    s.check("and the flagged one is in with_leavers",
            any(r["what"] == TAG + "big-iron-key" for r in data["with_leavers"]))

    s.section("A key that came back is not still out")
    s.check("it is off the register", "returned-key" not in mine,
            detail="a list that shows keys already hanging up is a list "
                   "nobody trusts")

    s.section("A code is not a key")
    s.check("a door code is not listed as something to get back",
            "door-code" not in mine,
            detail="a code is revoked, not returned; listing every one ever "
                   "issued would bury the keys that genuinely are out")

    s.section("A guest holding a key counts as a person")
    s.check("the count includes them", data["people"] >= 1,
            detail=f"{data['people']} — a guest has no user row, so counting "
                   "distinct user_id read 'With people: 0' beside a table of "
                   "keys held by guests")

    s.section("The page says it, and says when it was due back")
    body = oc.get("/admin/still-out").get_data(as_text=True)
    s.check("the key is on the page", TAG + "big-iron-key" in body)
    s.check("with the guest marked as checked out", "has checked out" in body)
    s.check("and the day it was due back", "due back" in body)

    # ------------------------------------------------------------ photographs
    s.section("A guest who would rather not be photographed")
    conn.execute(
        """INSERT INTO guests (name, email, photo_consent, photo_consent_at,
                   created_at)
           VALUES (?, ?, 'no', ?, ?)""",
        (TAG + " Camera Shy", TAG + "here@example.invalid",
         now.isoformat(), now.isoformat()))
    conn.execute(
        """INSERT INTO guests (name, email, photo_consent, created_at)
           VALUES (?, ?, 'yes', ?)""",
        (TAG + " Happy", TAG + "gone@example.invalid", now.isoformat()))
    conn.commit()

    declines = m.photo_declines(conn, today)
    names = {d["who"] for d in declines}
    s.check("they are found while they are in the house",
            TAG + " Camera Shy" in names, detail=str(names))
    s.check("with the room they are in",
            any(d["room"] for d in declines if d["who"] == TAG + " Camera Shy"),
            detail=str(declines))
    s.check("somebody who said yes is not on the list",
            TAG + " Happy" not in names,
            detail="a list of everybody asked is not a list of who declined")

    s.section("Somebody the house has never asked is not guessed at")
    # A stay in the house tonight whose email matches no profile at all. The
    # branch that skips them is the one carrying the risk: a version that
    # filled in a blank profile instead would put a stranger on a list of
    # people who declined, which is a refusal nobody gave.
    booking("STRANGER", TAG + " Unknown", TAG + "nobody@example.invalid",
            today - timedelta(days=1), today + timedelta(days=2))
    listed = {d["who"] for d in m.photo_declines(conn, today)}
    s.check("they are not on the list", TAG + " Unknown" not in listed,
            detail=f"{listed} — no profile and no answer; a name is not an "
                   "identity and an invented refusal is still an invention")
    s.check("and the people who did answer still are",
            TAG + " Camera Shy" in listed,
            detail="if the list were empty the check above would prove "
                   "nothing at all")

    s.section("It is the profile that answers, not the booking")
    s.check("when it was answered comes with it",
            any(d["asked_at"] for d in declines
                if d["who"] == TAG + " Camera Shy"),
            detail="photo_consent_at, written since the field existed and "
                   "read by nothing")
    conn.execute("UPDATE guests SET photo_consent = 'yes' WHERE email = ?",
                 (TAG + "here@example.invalid",))
    conn.commit()
    s.check("changing their mind takes them off it",
            TAG + " Camera Shy" not in {d["who"] for d in
                                        m.photo_declines(conn, today)},
            detail="the answer belongs to the person and travels with them "
                   "across stays; it is not a property of one booking")
    conn.execute("UPDATE guests SET photo_consent = 'no' WHERE email = ?",
                 (TAG + "here@example.invalid",))
    conn.commit()

    s.section("It reaches the camera and the publishing, which are different people")
    for path, what in (("/admin/today-sheet", "the sheet the staff read"),
                       ("/admin/gallery", "the gallery"),
                       ("/admin/images", "the site photographs")):
        page = oc.get(path).get_data(as_text=True)
        s.check(f"named on {what}", TAG + " Camera Shy" in page,
                detail=f"{path} — telling only whoever publishes is how a "
                       "photograph gets taken correctly and used anyway")

    s.section("And it does not pretend to decide")
    r = oc.get("/admin/images")
    s.check("the publishing page still works normally",
            r.status_code == 200,
            detail="no program can tell who is in a picture, so one that "
                   "refused would refuse the wrong ones and be switched off")
    s.check("it says the software does not check",
            "nothing else in the software checks"
            in r.get_data(as_text=True),
            detail="claiming more than it does is the thing that makes a "
                   "guest stop believing the rest of it")

    s.section("The privacy notice says what the code now does")
    # Whitespace flattened: the sentence is wrapped across two lines in the
    # template, so a substring with one space in it never matched and the
    # check failed while the claim was on the page.
    notice = " ".join(
        m.app.test_client().get("/privacy").get_data(as_text=True).split())
    s.check("it mentions photographs at all", "Photographs" in notice)
    s.check("saying the answer is kept against the person, not the booking",
            "every stay" in notice,
            detail="which is what makes it true; it reads the guest profile")
    s.check("and that a person decides rather than the software",
            "no program can tell who is in a picture" in notice,
            detail="the notice is a set of testable claims about this code, "
                   "and overstating what the software does is worse than "
                   "having no notice")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
