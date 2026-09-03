"""Empty nights, the people who might take them, and booking one of them in.

The app has known which nights are unsold and who has stayed before for a long
time, and had no way to put the two facts together — so an offer went to the
whole list or to nobody at all, and a list sent things nobody wanted is a list
that stops being read.

Four things carry this file.

  THE REASON IS ON THE ROW. A name with no reason beside it is one somebody
  either trusts blindly or ignores. Three reasons, each a fact about that
  guest: overdue against THEIR OWN typical gap, has stayed in this month
  before, usually takes a stay about this long.

  NOBODY STANDING OUT IS AN ANSWER. An empty candidate list means nobody is
  overdue, nobody has come at this time of year and nobody takes stays this
  long — and a note sent anyway is one read less carefully next time.

  REBOOKING GOES THROUGH THE SAME LOCK. Typing a booking on a guest's behalf
  must not be a way past the availability check. Two rebooks arriving together
  would otherwise both read "free" and both write, and the first anybody knows
  is two cars in the drive. It is created as a REQUEST, not confirmed.

  THE THIRD STAY IS SAID OUT LOUD. Not every visit — a guest told "your
  seventh stay" hears a loyalty scheme — but three is when somebody stops
  being a visitor, and it was getting the same greeting as a stranger.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZGAP"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _stay(ref, *, room_id, arrival, nights=3, email, name=None, status="confirmed"):
    conn = db()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, payment_status, total_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, ?, 'paid', 900, 900, ?)""",
        (room_id, f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), name or f"{TAG} {ref}",
         email, arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(),
         status, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def run():
    s = Suite("Filling the empty weeks")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    room = _harness.ensure_room()
    today = m.house_today()

    s.section("Who to offer a run of empty nights to")
    # Somebody who has come in this month before, twice, and is well past the
    # gap they usually leave between visits.
    reg = "zzgap.regular@example.invalid"
    target = today + timedelta(days=45)
    for n, back in enumerate((730, 400)):
        _stay(f"R{n}", room_id=room["id"],
              arrival=target.replace(year=target.year - 1) - timedelta(days=back - 365),
              nights=3, email=reg, name=f"{TAG} Regular")
    conn = db()
    cand = m.gap_candidates(conn, target.isoformat(),
                            (target + timedelta(days=3)).isoformat())
    conn.close()
    s.check("the run is measured in nights", cand["nights"] == 3,
            detail=f"{cand['nights']}")
    s.check("and the month is named", cand["month"] == target.strftime("%B"),
            detail=f"{cand['month']}")
    ours = [c for c in cand["candidates"] if c["email"] == reg]
    s.check("a guest with history in that month is a candidate", bool(ours),
            detail=f"{[c['email'] for c in cand['candidates']][:4]}")
    if ours:
        why = " | ".join(ours[0]["reasons"])
        s.check("and the row says why", bool(ours[0]["reasons"]),
                detail=f"{why} — a name with no reason is trusted blindly or "
                       "ignored")
        s.check("naming the month as one of the reasons",
                target.strftime("%B") in why,
                detail=f"{why} — seasonality is the strongest signal a small "
                       "house has and a generic list throws it away")

    s.section("Somebody with no history at all is not a candidate")
    # A guest who HAS stayed but qualifies for nothing: it was last week so
    # they are not overdue, it was a different month, and it was one night
    # against a three-night gap. Without them there is nothing for a padded
    # list to pad WITH, and the check below could not fail.
    _stay("NOPE", room_id=room["id"], arrival=today - timedelta(days=10),
          nights=1, email="zzgap.nope@example.invalid", name=f"{TAG} Nope")
    conn = db()
    cand2 = m.gap_candidates(conn, target.isoformat(),
                             (target + timedelta(days=3)).isoformat())
    conn.close()
    s.check("a stranger is not on the list",
            not any(c["email"] == "nobody@example.invalid" for c in cand2["candidates"]))
    # THE PROPERTY THAT MATTERS, and the one a "reasons are non-empty" check
    # cannot see: somebody with no real reason must not appear at all. A list
    # padded with generic entries is the whole-list send this page exists to
    # replace.
    every = [w for c in cand2["candidates"] for w in c["reasons"]]
    s.check("and every reason given is a specific one",
            every and all(("overdue" in w) or ("stayed in" in w) or ("nights" in w)
                          for w in every),
            detail=f"{sorted(set(every))[:4]} — a generic 'candidate' beside a "
                   "name is the padding that makes a list stop being read")
    # A run that makes no sense gets an honest empty answer rather than a guess.
    conn = db()
    s.check("a backwards run is refused",
            m.gap_candidates(conn, target.isoformat(),
                             (target - timedelta(days=3)).isoformat())["candidates"] == [],
            detail="a departure before the arrival is a typo, not a gap")
    conn.close()

    s.section("The page shows the runs and the reasons")
    body = oc.get("/management/fill-a-gap").get_data(as_text=True)
    s.check("it opens", "Fill a gap" in body)
    s.check("and offers the runs", "Who for?" in body or "Nothing unsold" in body,
            detail="the page is useless without a way into a particular run")
    picked = oc.get("/management/fill-a-gap?from=%s&to=%s"
                    % (target.isoformat(), (target + timedelta(days=3)).isoformat())
                    ).get_data(as_text=True)
    s.check("a chosen run names its candidates",
            f"{TAG} Regular" in picked or "Nobody stands out" in picked,
            detail="either a list or the honest answer that there is none")
    s.check("and sends nothing itself",
            "campaign sender" in picked or "Write to them" in picked,
            detail="opt-outs, the unsubscribe line and the record of what went "
                   "to whom all live in the sender; duplicating any of it here "
                   "is how somebody gets two copies")

    s.section("Booking a returning guest in again")
    conn = db()
    conn.execute(
        """INSERT INTO guests (name, email, phone, dietary_notes,
           usual_arrival_time, created_at) VALUES (?, ?, '+33 6 00 00 00 00',
           'no shellfish', '17:30', ?)""",
        (f"{TAG} Rebook", "zzgap.rebook@example.invalid",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    gid = conn.execute("SELECT id FROM guests WHERE email = ?",
                       ("zzgap.rebook@example.invalid",)).fetchone()["id"]
    conn.close()
    far = today + timedelta(days=200)
    r = oc.post(f"/guests/{gid}/rebook", data={
        "room_id": str(room["id"]), "arrival_date": far.isoformat(),
        "departure_date": (far + timedelta(days=2)).isoformat(),
        "party_size": "2"}, follow_redirects=True)
    msg = " ".join(flashes(r))
    conn = db()
    made = conn.execute(
        """SELECT * FROM bookings WHERE guest_email = ? ORDER BY id DESC LIMIT 1""",
        ("zzgap.rebook@example.invalid",)).fetchone()
    conn.close()
    s.check("a booking is created", made is not None, detail=f"{msg}")
    if made:
        s.check("as a REQUEST, not confirmed", made["status"] == "pending",
                detail=f"{made['status']} — a stay typed on somebody's behalf "
                       "is still a request until the house looks at the dates")
        s.check("what they cannot eat came across",
                "no shellfish" in (made["special_requests"] or ""),
                detail="it is on their record precisely so nobody retypes it")
        s.check("and when they usually arrive",
                (made["estimated_arrival_time"] or "") == "17:30",
                detail=f"{made['estimated_arrival_time']!r}")
        s.check("it is attached to the profile", made["linked_guest_id"] == gid)
        s.check("and marked as a returning booking", made["source"] == "returning",
                detail=f"{made['source']!r} — otherwise the channel mix credits "
                       "it to nobody")
    s.check("and the message says nothing was sent",
            "nothing has been sent" in msg.lower(), detail=f"{msg}")

    s.section("Rebooking cannot walk past the availability check")
    r2 = oc.post(f"/guests/{gid}/rebook", data={
        "room_id": str(room["id"]), "arrival_date": far.isoformat(),
        "departure_date": (far + timedelta(days=2)).isoformat(),
        "party_size": "2"}, follow_redirects=True)
    s.check("the same nights twice is refused",
            "not free" in " ".join(flashes(r2)),
            detail=f"{flashes(r2)[:1]} — two cars in the drive is what this "
                   "stops")
    r3 = oc.post(f"/guests/{gid}/rebook", data={
        "room_id": str(room["id"]),
        "arrival_date": (today - timedelta(days=5)).isoformat(),
        "departure_date": today.isoformat()}, follow_redirects=True)
    s.check("and an arrival in the past is refused",
            "in the past" in " ".join(flashes(r3)), detail=f"{flashes(r3)[:1]}")

    s.section("The third stay is said out loud")
    third = "zzgap.third@example.invalid"
    base = today - timedelta(days=400)
    for i in range(2):
        _stay(f"T{i}", room_id=room["id"], arrival=base + timedelta(days=i * 100),
              nights=2, email=third, name=f"{TAG} Third")
    soon = today + timedelta(days=1)
    _stay("T3", room_id=room["id"], arrival=soon, nights=2, email=third,
          name=f"{TAG} Third")
    conn = db()
    n = m.stay_number(conn, third, soon.isoformat())
    milestone = m.stay_milestone(conn, third, soon.isoformat())
    conn.close()
    s.check("the visit is counted", n == 3, detail=f"{n}")
    s.check("and three is a milestone", milestone == 3, detail=f"{milestone}")
    sheet = oc.get("/admin/arrivals").get_data(as_text=True)
    s.check("it is on the arrivals sheet", "3rd stay" in sheet,
            detail="the people most likely to come back were getting the same "
                   "greeting as a stranger")
    conn = db()
    s.check("a second visit is not a milestone",
            m.stay_milestone(conn, third, (base + timedelta(days=100)).isoformat()) is None,
            detail="every visit numbered out loud reads as a loyalty scheme")
    conn.close()

    s.section("Guards")
    s.check("an employee cannot see who to offer nights to",
            ec.get("/management/fill-a-gap").status_code in (302, 403))
    before = None
    conn = db()
    before = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE guest_email = ?",
                          ("zzgap.rebook@example.invalid",)).fetchone()["c"]
    conn.close()
    ec.post(f"/guests/{gid}/rebook", data={
        "room_id": str(room["id"]),
        "arrival_date": (today + timedelta(days=300)).isoformat(),
        "departure_date": (today + timedelta(days=302)).isoformat()})
    conn = db()
    after = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE guest_email = ?",
                         ("zzgap.rebook@example.invalid",)).fetchone()["c"]
    conn.close()
    s.check("nor book somebody in", after == before,
            detail="checked by effect: a refusal and a success both redirect")

    _cleanup()
    return s
