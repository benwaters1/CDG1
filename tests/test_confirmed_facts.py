"""What the owner has confirmed about the house, held to across every template.

The design side keeps a FACT LEDGER: every claim the site makes about the
property, one line each, so that the wrong ones are caught on purpose rather
than by chance. Two were found by chance first — one dog, not two; ten acres,
not six — and then this round found the same wrong facts again in the NINE
PARTIALS the design side cannot see, because a ledger kept in a text file is
checked by whoever happens to read it.

So the parts the owner has confirmed are checks, and a handover that brings a
retired claim back is named by file.

  THE ANIMALS. Bruce, a French bulldog, and four cats. No second dog and no
  chickens — which the site had printed in three places, one of them the
  allergy line on the booking form.

  THE ESTATE. Ten acres, about four hectares. Not six, not 2.4, and not "six
  hectares" in French either.

  THE STAIRS. Every bedroom is upstairs and every bathroom is downstairs.
  This is the one that costs somebody something. The house told an arriving
  guest "tell us if the staircase is a problem and we will put you on the
  ground floor" — to precisely the guest who needs that promise kept, about a
  floor with no bedroom on it. And it described private bathrooms as "along
  the corridor", when they are a flight of stairs away at three in the
  morning.

  AND THE ROOM THAT IS CHEAPEST. Tilleul was called "the least expensive way
  into" the house, at €240, beside Cerise at €220.

Checked on the SOURCE, with comments stripped, because partials are where
these hide: a claim in a macro nothing currently calls is a claim the next
page to call it will print.
"""
import glob
import io
import os
import re
from datetime import timedelta

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZFACT"

# Retired claims. Each is something the site said that the owner has said is
# not so. Lower-case, matched on words.
RETIRED = {
    "six acres": "ten acres, about four hectares",
    "2.4 hectares": "about four hectares",
    "six hectares": "about four hectares",
    "two dogs": "one dog, Bruce",
    "six cats": "four cats",
    "chickens": "there are none",
    "put you on the ground floor": "no bedroom is on the ground floor",
    "along the corridor": "the bathrooms are downstairs",
}
COMMENTS = re.compile(r"\{#.*?#\}|<!--.*?-->", re.S)


def _templates():
    root = os.path.join(_harness.ROOT, "templates")
    for path in sorted(glob.glob(os.path.join(root, "*.html"))):
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            yield os.path.basename(path), COMMENTS.sub("", fh.read()).lower()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM workshop_sessions WHERE notes = ?", (TAG,))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM waitlist_entries WHERE email LIKE ?", ("zzfact%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", ("%zzfact%",))
    conn.commit()
    conn.close()


def _atelier(start, days):
    conn = db()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn.execute("INSERT INTO workshops (title, description, price_per_person, "
                 "active, created_at) VALUES (?, 'x', 100, 1, ?)",
                 (TAG + " atelier", now))
    wid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute("INSERT INTO workshop_sessions (workshop_id, start_date, end_date, "
                 "capacity, notes, created_at) VALUES (?, ?, ?, 8, ?, ?)",
                 (wid, start.isoformat(), (start + timedelta(days=days - 1)).isoformat(),
                  TAG, now))
    conn.commit()
    conn.close()


def run():
    s = Suite("What the owner has confirmed about the house")
    _cleanup()
    clients()
    anon = m.app.test_client()

    s.section("No template says what the owner has said is not so")
    pages = list(_templates())
    s.check("there are templates to read", len(pages) > 100, detail=str(len(pages)))
    for claim, truth in RETIRED.items():
        found = [name for name, text in pages if claim in text]
        s.check(f"nothing says \"{claim}\"", not found,
                detail=f"{', '.join(found)} — {truth}")

    s.section("The room data agrees")
    conn = db()
    rooms = conn.execute("SELECT name, floor, price_per_night FROM rooms "
                         "WHERE active = 1").fetchall()
    conn.close()
    grounded = [r["name"] for r in rooms if (r["floor"] or "").lower() == "ground"]
    s.check("no bedroom is on the ground floor, even on a fresh install",
            not grounded,
            detail=f"{grounded} — the room picker reads this column and tells a "
                   "guest who said stairs were difficult \"it is on the ground "
                   "floor, so no staircase\"")
    seeded = [row for row in m.ROOM_IDENTITY if (row[6] or "").lower() == "ground"]
    s.check("and nothing seeds one there",
            not seeded,
            detail=f"{[r[1] for r in seeded]} — the migration that clears it runs "
                   "on an empty table, and the rename runs after the seed")

    s.section("A price comparison in prose names the right room")
    cheapest = min(rooms, key=lambda r: r["price_per_night"] or 1e9)["name"] if rooms else None
    wrong = []
    for name, text in pages:
        for hit in re.finditer(r"least expensive|cheapest|costs least", text):
            window = text[max(0, hit.start() - 400):hit.end() + 80]
            named = [r["name"] for r in rooms if r["name"].lower() in window]
            if named and cheapest and cheapest.lower() not in " ".join(named).lower():
                wrong.append(f"{name}: {named}")
    s.check("no page calls a room the cheapest when another is",
            not wrong, detail=f"{wrong} — the cheapest is {cheapest}")

    s.section("When an atelier is why no room is free, the page says so")
    start = m.house_today() + timedelta(days=500)
    _atelier(start, days=3)
    q = "/book?arrival=%s&departure=%s" % (start.isoformat(),
                                           (start + timedelta(days=1)).isoformat())
    page = anon.get(q).get_data(as_text=True)
    s.check("the refusal names the atelier and its dates",
            "an atelier holds the whole house" in page,
            detail="a blind \"no rooms free\" sends somebody away from a house "
                   "that is free the day after")
    s.check("and says when the house is free again",
            "free again from the day after" in page)
    # Back to back: the day after is also held, so "free again" would be false.
    _atelier(start + timedelta(days=3), days=2)
    page = anon.get(q).get_data(as_text=True)
    s.check("but not when the day after is held too",
            "free again from the day after" not in page,
            detail="two ateliers back to back make that sentence a promise the "
                   "house cannot keep")
    _cleanup()
    free = m.house_today() + timedelta(days=520)
    page = anon.get("/book?arrival=%s&departure=%s" % (
        free.isoformat(), (free + timedelta(days=1)).isoformat())).get_data(as_text=True)
    s.check("and says nothing about ateliers when none is the reason",
            "an atelier holds the whole house" not in page)

    s.section("A guest's own account lists their event enquiries")
    conn = db()
    email = "zzfact.event@example.invalid"
    now = m.datetime.now(m.timezone.utc).isoformat()
    for label, status in (("live", "quoted"), ("gone", "declined")):
        conn.execute(
            """INSERT INTO event_inquiries (event_type, contact_name, contact_email,
                 preferred_date, status, reference_code, manage_token, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("Wedding " + label, TAG + " " + label, email,
             (m.house_today() + timedelta(days=200)).isoformat(), status,
             m.make_event_reference_code(), "zzfact" + m.secrets.token_hex(6), now))
    conn.commit()
    stays, dinners, ateliers, events = m.guest_portal_contents(conn, email)
    conn.close()
    kinds = [e["event_type"] for e in events]
    s.check("the enquiry they have is there", "Wedding live" in kinds,
            detail=f"{kinds} — a couple whose only booking was their wedding "
                   "read \"Nothing booked at the moment\"")
    s.check("and one the house declined is not", "Wedding gone" not in kinds,
            detail=str(kinds))

    s.section("The rough-dates enquiry takes what people actually know")
    r = anon.post("/contact", data={
        "action": "flexible", "email": "zzfact.flex@example.invalid",
        "name": TAG + " Margot", "rough_month": "", "rough_nights": "0",
        "rough_guests": "2", "flexibility": "any", "message": ""},
        follow_redirects=True)
    conn = db()
    row = conn.execute("SELECT * FROM waitlist_entries WHERE email = ?",
                       ("zzfact.flex@example.invalid",)).fetchone()
    task = conn.execute("SELECT title FROM tasks WHERE title LIKE ?",
                        ("%zzfact.flex%",)).fetchone()
    conn.close()
    s.check("an enquiry with no month is accepted", bool(row),
            detail=f"HTTP {r.status_code} — a press or general question should "
                   "not have to pick a month")
    s.check("\"not decided\" is recorded as not decided",
            row and "not decided" in (row["notes"] or "")
            and "1 night" not in (row["notes"] or ""),
            detail=f"{row['notes'] if row else None!r} — it used to arrive as a "
                   "request for one night")
    s.check("and the name they gave is kept", row and row["name"] == TAG + " Margot",
            detail=f"{row['name'] if row else None!r}")
    s.check("so the task says who to answer",
            task and "Margot" in task["title"], detail=f"{task['title'] if task else None}")

    _cleanup()
    return s
