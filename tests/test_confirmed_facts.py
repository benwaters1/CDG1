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
    # 2 October: the design side wrote "tennis or padel" into the free-time
    # copy. There is no padel court -- padel needs a walled court of its own.
    # Guests play pickleball on the tennis court with the house's racquets;
    # the court has NO pickleball lines (owner, 3 October), so "a pickleball
    # court" is as wrong as a padel one. tools/repair_handover.py rewrites
    # both on install; these name any that get past it.
    "padel": "no padel court; pickleball is played on the tennis court",
    "pickleball court": "no pickleball court or lines; guests play it on the "
                        "tennis court with the house's racquets",
    "lined for pickleball": "the tennis court has no pickleball lines",
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
    # "an atelier" became "a workshop" site-wide on 1 October; the check is
    # on the substance -- that the house is held, and by what -- not the noun.
    s.check("the refusal names the atelier and its dates",
            "holds the whole house" in page,
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

    s.section("A stay is paid online, and the letter does not argue with itself")
    # The confirmation ends by saying the house only ever takes payment on its
    # own site. {stay_details} is built separately and dropped into the middle
    # of it, and used to say the balance was due on arrival, in cash -- so the
    # one sentence that helps a guest spot a scam was contradicted above it.
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE active = 1 "
                        "ORDER BY id LIMIT 1").fetchone()
    day = m.house_today()
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
             guest_email, arrival_date, departure_date, party_size, status,
             total_price, amount_paid, balance_due_date, created_at)
           VALUES (?, ?, ?, ?, 'zzfact@example.invalid', ?, ?, 2, 'confirmed',
                   900, 200, ?, ?)""",
        (room["id"], TAG + "PAY", TAG + "paytok", TAG + " Payer",
         (day + timedelta(days=30)).isoformat(),
         (day + timedelta(days=33)).isoformat(),
         (day + timedelta(days=16)).isoformat(),
         m.utc_now_iso() if hasattr(m, "utc_now_iso") else day.isoformat()))
    conn.commit()
    booking = conn.execute("SELECT * FROM bookings WHERE reference_code = ?",
                           (TAG + "PAY",)).fetchone()
    # An app context: the builder makes the guest's own links with url_for,
    # which is the whole reason the letter can be read without the app.
    with m.app.test_request_context("/"):
        # It answers (context, the figures the caller also needs), so the
        # letter's own merge fields are the first half.
        details = m.room_confirmation_context(
            conn, booking, room["name"])[0]["stay_details"]
    conn.close()
    s.check("the balance is not asked for at the door",
            "on arrival" not in details.lower() and "cash" not in details.lower(),
            detail=details)
    # The MONEY line, not the whole block: "Check in online" sits a few lines
    # below, so searching the block passed with the sentence deleted. The
    # control caught that, which is the only reason it is written this way.
    money_line = next((l for l in details.splitlines()
                       if "to pay" in l.lower()), "")
    s.check("and that line says where to settle it",
            "online" in money_line.lower(), detail=repr(money_line))
    s.check("with the date the house set, so nobody has to work out the day",
            m.format_date_human((day + timedelta(days=16)).isoformat()) in details,
            detail=details)

    # 8 October (owner): "events are more likely to be bank transfer". The
    # anti-scam sentence promises the house never asks for one -- true of a
    # stay and an atelier, not of an event, where this file already says the
    # balance is usually a transfer somebody arranges. On an event letter it
    # would teach a real client to refuse a real request.
    #
    # Both ways in one check: the sentence quietly going missing from the
    # letters it protects is the same kind of silent failure as it appearing
    # where it is false.
    MONEY_LETTERS_THAT_WARN = {
        "room_confirmed", "room_balance_before", "room_balance_after",
        "workshop_confirmed", "workshop_deposit_receipt",
        "workshop_balance_reminder"}
    bodies = {k: b for k, _l, _s, b in m.DEFAULT_EMAIL_TEMPLATES}
    warns = {k for k, b in bodies.items() if m.PAYMENT_SAFETY_LINE in b}
    s.check("no event letter promises the house never asks for a transfer",
            not any(k.startswith("event") for k in warns),
            detail=str(sorted(k for k in warns if k.startswith("event"))))
    s.check("and every stay and atelier money letter still carries it",
            warns == MONEY_LETTERS_THAT_WARN,
            detail="missing %s; unexpected %s"
                   % (sorted(MONEY_LETTERS_THAT_WARN - warns),
                      sorted(warns - MONEY_LETTERS_THAT_WARN)))

    _cleanup()
    return s
