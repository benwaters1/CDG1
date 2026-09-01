"""Five things the house needs to do with a guest and could not.

  - MERGE two profiles. The unique index stops two sharing an email address and
    does nothing about the same family booking online one year and walking in
    the next. Their history, their allergy and every note anybody wrote sit on
    the profile nobody opens.
  - WARN before the second profile exists, because merging afterwards depends on
    somebody noticing.
  - A CAUTION that is read where a booking is taken, rather than found later in
    a free-text box.
  - A RUNNING NOTE that is append-only. guests.notes is one field: whoever types
    last replaces what the last person wrote, and what gets lost is always the
    older observation nobody thought to repeat.
  - DATES THAT MATTER, day and month only.

Two things here are load-bearing beyond "does it work":

  - A MERGE MUST NOT LOSE ANYTHING. It keeps every field already filled on the
    survivor, fills only its blanks, and deletes nothing -- the absorbed profile
    stays, pointing at its survivor, because a booking or an invoice may still
    reference it.
  - THE YEAR OF A BIRTHDAY IS THROWN AWAY. The house wants to know the 3rd of
    June matters to somebody; a date of birth is something it would then have to
    justify holding.
"""
import io
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZGM"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM guest_notes WHERE guest_id IN "
                 "(SELECT id FROM guests WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("UPDATE bookings SET linked_guest_id = NULL WHERE linked_guest_id IN "
                 "(SELECT id FROM guests WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _guest(name, **cols):
    conn = db()
    cols.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    cols["name"] = f"{TAG} {name}"
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO guests ({keys}) VALUES ({marks})", list(cols.values()))
    conn.commit()
    row = conn.execute("SELECT * FROM guests WHERE name = ? ORDER BY id DESC LIMIT 1",
                       (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _row(guest_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM guests WHERE id = ?", (guest_id,)).fetchone()
    finally:
        conn.close()


def _notes(guest_id):
    conn = db()
    try:
        return m.guest_notes(conn, guest_id)
    finally:
        conn.close()


def _stay_for(guest, ref):
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    arrival = date.today() + timedelta(days=20)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, guest_phone, arrival_date, departure_date, party_size,
           status, total_price, amount_paid, linked_guest_id, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 2, 'confirmed', 100, 0, ?, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), guest["name"],
         guest["email"] or "", arrival.isoformat(),
         (arrival + timedelta(days=2)).isoformat(), guest["id"],
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def run():
    s = Suite("Guest management")
    _cleanup()
    oc, ec, owner, emp = clients()

    # ---------------------------------------------------------------- merge
    s.section("Two profiles for one person, folded together")
    keep = _guest("Rouvière", email="zzgm.a@example.invalid",
                  phone="0612345678", preferences="Quiet side")
    # A CONFLICTING preference on purpose. With the absorbed profile silent on
    # it, "what was already there is untouched" passes even when the merge
    # overwrites — there is nothing to overwrite with.
    other = _guest("Rouvière", phone="06 12 34 56 78",
                   dietary_notes="No shellfish", preferences="Near the stairs",
                   notes="Brings a small dog")
    _stay_for(other, "OLD")

    r = oc.post(f"/guests/{keep['id']}/merge", data={"merge_id": str(other["id"])},
                follow_redirects=True)
    after = _row(keep["id"])
    s.check("it goes through", r.status_code == 200, detail=f"{flashes(r)[:1]}")
    s.check("a blank on the survivor is filled from the other",
            (after["dietary_notes"] or "") == "No shellfish",
            detail=f"{after['dietary_notes']!r} — a merge that dropped an allergy "
                   "is worse than two profiles")
    s.check("but what was already there is untouched",
            (after["preferences"] or "") == "Quiet side",
            detail=f"{after['preferences']!r} — the older profile overwrote the "
                   "newer one, which is a data loss dressed as a tidy-up")
    s.check("and the survivor keeps its own email",
            (after["email"] or "") == "zzgm.a@example.invalid",
            detail=f"{after['email']!r}")

    s.section("Nothing is deleted")
    absorbed = _row(other["id"])
    s.check("the other profile is still there", absorbed is not None,
            detail="a booking or an invoice may still reference it, and a row "
                   "that vanishes takes the explanation with it")
    s.check("pointing at the one it went into",
            absorbed["merged_into_id"] == keep["id"],
            detail=f"{absorbed['merged_into_id']}")

    s.section("The stays come with it")
    conn = db()
    moved = conn.execute(
        "SELECT COUNT(*) c FROM bookings WHERE linked_guest_id = ?",
        (keep["id"],)).fetchone()["c"]
    conn.close()
    s.check("the linked stay is now on the survivor", moved == 1,
            detail=f"{moved} — the point of a merge is that the history follows")

    s.section("And its free-text note is kept as history, not pasted over")
    bodies = " | ".join(n["body"] for n in _notes(keep["id"]))
    s.check("the old note is on the timeline", "small dog" in bodies,
            detail=f"{bodies[:90]!r}")
    s.check("and the merge itself is recorded", "Merged the duplicate" in bodies,
            detail="somebody looking later cannot tell this profile was two")

    s.section("A merge that would lose something is refused")
    r = oc.post(f"/guests/{keep['id']}/merge", data={"merge_id": str(keep['id'])},
                follow_redirects=True)
    s.check("a profile cannot be merged into itself",
            any("same profile" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]}")
    r = oc.post(f"/guests/{keep['id']}/merge", data={"merge_id": str(other["id"])},
                follow_redirects=True)
    s.check("and one already merged is not merged twice",
            any("already been merged" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)[:1]} — the notes would be moved a second time")

    # ------------------------------------------------------- duplicate warning
    s.section("A second profile is questioned before it exists")
    conn = db()
    before = conn.execute("SELECT COUNT(*) c FROM guests WHERE name LIKE ?",
                          (TAG + "%",)).fetchone()["c"]
    conn.close()
    r = oc.post("/guests/new", data={"name": f"{TAG} Rouvière",
                                     "email": "zzgm.new@example.invalid",
                                     "phone": "0612345678"},
                follow_redirects=True)
    body = r.get_data(as_text=True)
    conn = db()
    after_count = conn.execute("SELECT COUNT(*) c FROM guests WHERE name LIKE ?",
                               (TAG + "%",)).fetchone()["c"]
    conn.close()
    s.check("nothing is created yet", after_count == before,
            detail=f"{before} -> {after_count}")
    s.check("and the match is shown", "may already be somebody we know" in body,
            detail="reception is told nothing and files the second profile")
    s.check("with why it matched",
            "same phone number" in body or "same name" in body,
            detail="a warning with no reason on it gets clicked past")

    s.section("The phone number alone is enough to ask the question")
    # Different name, same number. With the name matching too, the phone rule
    # could be deleted and nothing here would notice.
    _guest("Estève", email="zzgm.est@example.invalid", phone="0799887766")
    r = oc.post("/guests/new", data={"name": f"{TAG} Someone Else",
                                     "email": "zzgm.else@example.invalid",
                                     "phone": "07 99 88 77 66"},
                follow_redirects=True)
    body = r.get_data(as_text=True)
    s.check("it still asks", "may already be somebody we know" in body,
            detail="a family books under one number and two names, and the "
                   "second profile goes in unremarked")
    s.check("naming the number as the reason", "same phone number" in body,
            detail=f"{body[body.find('may already'):][:120]!r}")

    s.section("But saving again goes through, because two people are called Martin")
    r = oc.post("/guests/new", data={"name": f"{TAG} Rouvière",
                                     "email": "zzgm.new@example.invalid",
                                     "phone": "0612345678",
                                     "confirm_duplicate": "1"},
                follow_redirects=True)
    # Counted on THIS name, not on every profile the suite has made. Sections
    # in between create their own, so a whole-set count was measuring the order
    # the suite runs in.
    conn = db()
    final = conn.execute(
        "SELECT COUNT(*) c FROM guests WHERE name = ?",
        (f"{TAG} Rouvière",)).fetchone()["c"]
    conn.close()
    s.check("the profile is created", final == 3,
            detail=f"{final} of 3 (two originals, one merged, plus this) — a form "
                   "that will not let reception save a real guest is worse than a "
                   "duplicate")

    # -------------------------------------------------------------- caution
    s.section("A caution is read where the booking is taken")
    trouble = _guest("Beaumont", email="zzgm.trouble@example.invalid")
    r = oc.post(f"/guests/{trouble['id']}/caution",
                data={"caution_level": "refuse",
                      "caution": "Left without paying, twice."},
                follow_redirects=True)
    after = _row(trouble["id"])
    s.check("it is recorded", (after["caution_level"] or "") == "refuse",
            detail=f"{after['caution_level']!r} — {flashes(r)[:1]}")
    s.check("with who set it and when",
            after["caution_set_by_user_id"] is not None and after["caution_set_at"],
            detail="a caution nobody owns is one nobody will lift")
    s.check("and it appears on the running note",
            any("Caution set" in n["body"] for n in _notes(trouble["id"])),
            detail="set and lifted with no trace of either")

    _stay_for(trouble, "TROUBLE")
    listing = oc.get("/admin/bookings").get_data(as_text=True)
    s.check("the booking row carries it",
            "Do not accept a booking" in listing,
            detail="found afterwards in a notes field nobody opened, which is "
                   "after the room was given")

    s.section("A caution needs a reason, and can be lifted")
    r = oc.post(f"/guests/{trouble['id']}/caution",
                data={"caution_level": "care", "caution": ""}, follow_redirects=True)
    s.check("one with nothing written on it is refused",
            (_row(trouble["id"])["caution_level"] or "") == "refuse",
            detail=f"{flashes(r)[:1]} — nobody can act on or lift a caution with "
                   "no reason attached")
    r = oc.post(f"/guests/{trouble['id']}/caution",
                data={"caution_level": "", "caution": ""}, follow_redirects=True)
    s.check("and clearing it works",
            not (_row(trouble["id"])["caution_level"] or ""),
            detail=f"{flashes(r)[:1]}")
    s.check("with the lifting on the record too",
            any("lifted" in n["body"].lower() for n in _notes(trouble["id"])))

    s.section("An employee cannot decide who the house refuses")
    r = ec.post(f"/guests/{trouble['id']}/caution",
                data={"caution_level": "refuse", "caution": "ZZ nope"},
                follow_redirects=False)
    s.check("refused", r.status_code in (302, 403), detail=f"HTTP {r.status_code}")
    s.check("and nothing was set", not (_row(trouble["id"])["caution_level"] or ""))

    # ------------------------------------------------------- the running note
    s.section("The note is append-only")
    subject = _guest("Lacombe", email="zzgm.l@example.invalid")
    ec.post(f"/guests/{subject['id']}/note",
            data={"body": "Prefers the quiet side"}, follow_redirects=True)
    ec.post(f"/guests/{subject['id']}/note",
            data={"body": "Asked about the bread"}, follow_redirects=True)
    notes = _notes(subject["id"])
    s.check("both are kept", len(notes) == 2,
            detail=f"{[n['body'] for n in notes]} — one free-text box means "
                   "whoever types last replaces what the last person wrote")
    s.check("newest first", notes[0]["body"] == "Asked about the bread",
            detail=f"{notes[0]['body']!r}")
    s.check("with who wrote it", all(n["written_by_user_id"] for n in notes),
            detail="'prefers the quiet side' from the housekeeper and from the "
                   "guest's own email are worth different amounts")
    s.check("an employee can write one, which is the whole value of it",
            notes[0]["written_by_user_id"] == emp["id"],
            detail=f"{notes[0]['written_by_user_id']} vs {emp['id']}")
    r = ec.post(f"/guests/{subject['id']}/note", data={"body": "   "},
                follow_redirects=True)
    s.check("an empty one is refused", len(_notes(subject["id"])) == 2,
            detail=f"{flashes(r)[:1]}")

    # -------------------------------------------------------------- the dates
    s.section("Dates that matter, without the year")
    r = ec.post(f"/guests/{subject['id']}/dates",
                data={"birthday": "3/6", "anniversary": "1990-09-14"},
                follow_redirects=True)
    after = _row(subject["id"])
    s.check("a day and month goes in", (after["birthday"] or "") == "06-03",
            detail=f"{after['birthday']!r}")
    s.check("a whole date has its year thrown away",
            (after["anniversary"] or "") == "09-14",
            detail=f"{after['anniversary']!r} — a date of birth is something the "
                   "house would then have to justify holding")
    s.check("and no year is stored anywhere",
            "1990" not in str(dict(after)),
            detail="the year came in with the rest of the row")
    r = ec.post(f"/guests/{subject['id']}/dates",
                data={"birthday": "the third of June", "anniversary": ""},
                follow_redirects=True)
    s.check("something unreadable is refused rather than stored",
            (_row(subject["id"])["birthday"] or "") == "06-03",
            detail=f"{flashes(r)[:1]}")

    s.section("And they come up before they arrive")
    conn = db()
    today = m.service_day()
    soon = today + timedelta(days=3)
    conn.execute("UPDATE guests SET birthday = ?, anniversary = NULL WHERE id = ?",
                 (f"{soon.month:02d}-{soon.day:02d}", subject["id"]))
    conn.commit()
    rows = m.upcoming_guest_dates(conn, within_days=30)
    conn.close()
    mine = [r for r in rows if r["guest"]["id"] == subject["id"]]
    s.check("it is on the list", len(mine) == 1,
            detail=f"{[(x['guest']['name'], x['days']) for x in rows][:4]}")
    s.check("with the right notice", mine and mine[0]["days"] == 3,
            detail=f"{mine[0]['days'] if mine else None}")

    s.section("The year wraps, which is when the house is fullest")
    # On the 20th of December the 3rd of January is a fortnight away, not three
    # hundred and fifty days. Get this wrong and the list quietly empties over
    # Christmas.
    conn = db()
    december = date(today.year, 12, 20)
    january = _guest("Newyear", email="zzgm.ny@example.invalid", birthday="01-03")
    rows = m.upcoming_guest_dates(conn, within_days=30, today=december)
    conn.close()
    hit = [r for r in rows if r["guest"]["id"] == january["id"]]
    s.check("a January date shows in December", len(hit) == 1,
            detail=f"{[(x['guest']['name'], x['days']) for x in rows][:4]}")
    s.check("as a fortnight away, not a year", hit and hit[0]["days"] == 14,
            detail=f"{hit[0]['days'] if hit else None}")

    s.section("The page")
    body = ec.get("/guests/dates").get_data(as_text=True)
    s.check("an employee can read it", "Dates that matter" in body,
            detail="acting on one is a job, and jobs are the employee side")
    s.check("every table is wrapped for a phone",
            body.count("<table") == body.count('class="table-wrap"'))
    s.check("and it can be empty",
            "{% if not rows %}" in
            io.open("templates/guest_dates.html", encoding="utf-8").read(),
            detail="a panel that can never be empty becomes furniture")

    s.section("A refusal stops a booking at the desk")
    # Where a caution most needs reading: somebody is standing there and a room
    # is about to be given. "Do not accept" stops it, because that is what the
    # owner recorded — the way to overrule it is to lift it, deliberately, on
    # the guest's own page.
    barred = _guest("Pontis", email="zzgm.barred@example.invalid")
    oc.post(f"/guests/{barred['id']}/caution",
            data={"caution_level": "refuse", "caution": "Damage to the room."},
            follow_redirects=True)
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    arrival = date.today() + timedelta(days=120)
    r = oc.post("/admin/bookings/walk-in", data={
        "room_id": str(room["id"]),
        "arrival_date": arrival.isoformat(),
        "departure_date": (arrival + timedelta(days=1)).isoformat(),
        "guest_name": barred["name"], "guest_email": barred["email"],
        "guest_phone": "", "party_size": "2", "special_requests": "",
        "charge": "200", "payment_method": "cash",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT COUNT(*) c FROM bookings WHERE guest_name = ?",
                        (barred["name"],)).fetchone()["c"]
    conn.close()
    s.check("the booking is refused", made == 0,
            detail=f"{made} — the owner recorded a standing instruction and the "
                   "desk took the booking anyway")
    s.check("and the reason is quoted back",
            any("Damage to the room" in f for f in flashes(r)),
            detail=f"{flashes(r)[:1]} — 'refused' with no reason sends reception "
                   "to look for one while somebody waits")
    s.check("with how to overrule it",
            any("lift it" in f.lower() for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("A different email address does not lift a refusal")
    # The check that matters: matching on the address alone would mean anybody
    # turned away gets in by using another one, which is the first thing they
    # would do.
    r = oc.post("/admin/bookings/walk-in", data={
        "room_id": str(room["id"]),
        "arrival_date": (arrival + timedelta(days=2)).isoformat(),
        "departure_date": (arrival + timedelta(days=3)).isoformat(),
        "guest_name": barred["name"], "guest_email": "zzgm.another@example.invalid",
        "guest_phone": "", "party_size": "2", "special_requests": "",
        "charge": "200", "payment_method": "cash",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT COUNT(*) c FROM bookings WHERE guest_name = ?",
                        (barred["name"],)).fetchone()["c"]
    conn.close()
    s.check("still refused, on the name", made == 0,
            detail=f"{made} — a new address walked straight past a standing "
                   "instruction")

    s.section("A softer caution is said, and the booking still goes on")
    oc.post(f"/guests/{barred['id']}/caution",
            data={"caution_level": "care", "caution": "Late arrivals."},
            follow_redirects=True)
    r = oc.post("/admin/bookings/walk-in", data={
        "room_id": str(room["id"]),
        "arrival_date": arrival.isoformat(),
        "departure_date": (arrival + timedelta(days=1)).isoformat(),
        "guest_name": barred["name"], "guest_email": barred["email"],
        "guest_phone": "", "party_size": "2", "special_requests": "",
        "charge": "200", "payment_method": "cash",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute("SELECT COUNT(*) c FROM bookings WHERE guest_name = ?",
                        (barred["name"],)).fetchone()["c"]
    conn.close()
    s.check("it is taken", made == 1,
            detail=f"{made} — 'handle with care' is not 'turn them away'")
    s.check("and the desk is told anyway",
            any("Late arrivals" in f for f in flashes(r)), detail=f"{flashes(r)[:1]}")

    s.section("A stored date is shown the way a person reads it")
    # Read from what is stored rather than hardcoding "June": an earlier section
    # moves this guest's birthday, so a fixed month here would be asserting the
    # order the sections happen to run in.
    stored = _row(subject["id"])["birthday"]
    body = ec.get(f"/guests/{subject['id']}").get_data(as_text=True)
    s.check("not as a storage format",
            m.day_month_human(stored) in body,
            detail=f"expected {m.day_month_human(stored)!r} for stored {stored!r}")
    s.check("and the raw form is still in the field to edit",
            f'value="{stored}"' in body,
            detail="showing only the pretty form leaves nothing to correct")

    s.section("Guards")
    s.check("a merged profile is not offered as a duplicate again",
            not any(d["guest"]["id"] == other["id"]
                    for d in m.possible_duplicate_guests(db(), f"{TAG} Rouvière")),
            detail="the same fold is offered forever")
    s.check("a guest that does not exist is a 404",
            ec.post("/guests/999999/note", data={"body": "x"},
                    follow_redirects=False).status_code == 404)

    _cleanup()
    return s
