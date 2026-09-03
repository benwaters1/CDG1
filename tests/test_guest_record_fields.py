"""Three things the guest record could not hold: why we said no, what language
they read in, and whether they mind being photographed.

  WHY A REQUEST WAS TURNED AWAY. Cancellations have carried a reason and a note
  for a while, and there is a report on them. Declines carried nothing — so the
  pattern in what the house says no to (too large, too short, the wrong week)
  was never written down and could not be priced for. It is optional on
  purpose: refusing a decline over a missing dropdown means somebody picks
  whatever is first to get past it, which is worse than blank.

  THE LANGUAGE THEY READ IN. The site speaks three. Every email still goes out
  in English, because the templates are one per key and writing twenty of them
  in French is the house's job, not the app's. What this does is record the
  answer and use it on the pages a guest reaches through a link we sent them.

  PHOTOGRAPH CONSENT. privacy.html tells guests that if they would rather not
  appear in anything we publish they can say so. There was nowhere to record
  that they had, so a published promise was being kept by somebody remembering.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZGR"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _guest(name="A"):
    conn = db()
    conn.execute(
        "INSERT INTO guests (name, email, created_at) VALUES (?, ?, ?)",
        (f"{TAG} {name}", f"zzgr.{name}@example.invalid".lower(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM guests WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _reload(gid):
    conn = db()
    try:
        return conn.execute("SELECT * FROM guests WHERE id = ?", (gid,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("What the guest record holds")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()

    s.section("Why a request was turned away")
    conn = db()
    arrival = m.house_today() + timedelta(days=40)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 9, 'pending', 'unpaid', 900, 0, ?)""",
        (room["id"], f"{TAG}-DEC", f"tok{TAG}dec", f"{TAG} Declined",
         "zzgr.dec@example.invalid", arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    bid = conn.execute("SELECT id FROM bookings WHERE reference_code = ?",
                       (f"{TAG}-DEC",)).fetchone()["id"]
    conn.close()

    was_email = m.send_email
    m.send_email = lambda to, subj, body, **k: True
    try:
        oc.post(f"/admin/bookings/{bid}/decline",
                data={"decline_reason": "too_large",
                      "decline_note": "nine in a room for four"},
                follow_redirects=True)
    finally:
        m.send_email = was_email
    conn = db()
    row = conn.execute("SELECT status, decline_reason, decline_note FROM bookings WHERE id = ?",
                       (bid,)).fetchone()
    conn.close()
    s.check("the request is declined", row["status"] == "declined")
    s.check("and why is kept with it", row["decline_reason"] == "too_large",
            detail=f"{row['decline_reason']!r} — without this the only record is "
                   "that somebody said no")
    s.check("along with what was actually said",
            "nine in a room" in (row["decline_note"] or ""),
            detail=f"{row['decline_note']!r}")

    s.section("And it is readable, not just stored")
    body = oc.get("/management/why-they-cancel").get_data(as_text=True)
    s.check("the page counts what was turned away",
            "Requests turned away" in body,
            detail="a reason recorded and never surfaced is a tidy database")
    s.check("naming the commonest one", "Too large for the room" in body,
            detail="nine requests turned away for the same reason in a month is "
                   "a pricing decision waiting to be made")
    # The honest half: a report whose biggest category is "nobody wrote it down"
    # has to say so, because the fix is a habit rather than a feature.
    s.check("and saying how many have no reason on them",
            "no reason written down" in body or "declines.unrecorded" not in body,
            detail="the dropdown is optional and this is what that costs")

    s.section("The decline form actually offers the choice")
    # A PENDING request has to be on the page: the decline button only appears
    # on one, and the booking above is declined by now.
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'pending', 'unpaid', 300, 0, ?)""",
        (room["id"], f"{TAG}-PEND", f"tok{TAG}pend", f"{TAG} Pending",
         "zzgr.pend@example.invalid", arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    listing = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the reasons are on the page", 'name="decline_reason"' in listing,
            detail="the route reads a field no form sends, so it is always blank")
    s.check("and they are the decline reasons, not the cancellation ones",
            "Not a week the house takes bookings" in listing,
            detail="a cancellation is the guest changing their mind and a "
                   "decline is the house saying no; the reasons do not overlap")

    s.section("The language they read in")
    g = _guest("LANG")
    oc.post(f"/guests/{g['id']}/edit",
            data={"name": g["name"], "email": g["email"], "language": "fr",
                  "photo_consent": "unknown"}, follow_redirects=True)
    s.check("it is saved", _reload(g["id"])["language"] == "fr",
            detail=f"{_reload(g['id'])['language']!r}")
    s.check("the form offers it", 'name="language"' in
            oc.get(f"/guests/{g['id']}/edit").get_data(as_text=True))
    # Rubbish is not stored. A language the site cannot render would leave the
    # guest on a page of missing strings.
    oc.post(f"/guests/{g['id']}/edit",
            data={"name": g["name"], "email": g["email"], "language": "klingon",
                  "photo_consent": "unknown"}, follow_redirects=True)
    s.check("and a language the site does not speak is refused",
            _reload(g["id"])["language"] is None,
            detail=f"{_reload(g['id'])['language']!r}")

    s.section("Photograph consent, which the privacy notice already promises")
    g2 = _guest("PHOTO")
    oc.post(f"/guests/{g2['id']}/edit",
            data={"name": g2["name"], "email": g2["email"], "photo_consent": "no"},
            follow_redirects=True)
    after = _reload(g2["id"])
    s.check("their answer is recorded", after["photo_consent"] == "no",
            detail=f"{after['photo_consent']!r}")
    s.check("and when they were asked", bool(after["photo_consent_at"]),
            detail="the date is the evidence that somebody was asked at all")
    stamped = after["photo_consent_at"]
    oc.post(f"/guests/{g2['id']}/edit",
            data={"name": g2["name"], "email": g2["email"], "photo_consent": "no"},
            follow_redirects=True)
    s.check("saving the same answer does not restamp it",
            _reload(g2["id"])["photo_consent_at"] == stamped,
            detail="the date would otherwise say they were asked today every "
                   "time somebody opened the form")
    s.check("not asked is a word, not a blank",
            m.PHOTO_CONSENT.get("unknown") == "Not asked",
            detail="unknown and no are different answers and must not look the same")

    s.section("Guards")
    s.check("an employee cannot edit a profile",
            ec.post(f"/guests/{g2['id']}/edit",
                    data={"name": "x", "photo_consent": "yes"}).status_code in (302, 403))

    _cleanup()
    return s
