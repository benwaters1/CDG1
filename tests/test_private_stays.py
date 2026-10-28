"""A private stay: an atelier somebody else organises, booked only by its link.

Handover z30, so that Retreat Guru can be switched off. Retreat Guru let a
programme be hidden from its list and still be booked by a direct link, and
that was the one thing it did that this house did not. A friend's birthday, a
family, a group: set up as an atelier with `private_token`, it is on no public
page, in no search engine and in nothing the house sends out, and the
organiser's guests book through /p/<token> exactly as on a public atelier.

What can go wrong is quiet. A private stay that leaks onto the list, the site
map, the newsletter or a drafted reply renders perfectly and tells strangers
about somebody's party. A link that the numbered page still answers, or a
form that takes a place without the key, makes the link pointless. And the
other way round: a private stay that stops holding the house, or stops closing
Booking.com, double-sells the château on the organiser's dates. So this reads
both halves -- everything that OFFERS an atelier leaves it out, and everything
that RUNS the house keeps it.

Stripe is stood in for while a deposit is taken; nothing leaves the building.
"""
import re
from datetime import timedelta

from _harness import (Suite, clients, db, ensure_room, flashes, free_window, house_today,
                      visible_text, WORKSHOP_DETAILS)
import _harness

m = _harness.m
TAG = "ZZPRIV"
TITLE = f"{TAG} Four Starry Nights"
ORGANISER = f"{TAG} Organiser"
ORGANISER_EMAIL = f"{TAG.lower()}.organiser@example.invalid"
FAKE_URL = "https://checkout.stripe.invalid/c/pay/" + TAG
NOINDEX = re.compile(r'<meta[^>]+name=["\']robots["\'][^>]+noindex', re.I)
_ip = [0]


class _Made:
    def __init__(self, **kw):
        self.kw = kw
        self.id = f"cs_{TAG}_{len(kw)}"
        self.url = FAKE_URL


class _CardMachine:
    """Stripe answers for the length of one branch, and never actually."""

    def __enter__(self):
        self.sessions = []
        self.saved = (m.stripe_enabled, m.stripe.checkout.Session.create,
                      m.stripe.Customer.create, m.card_from_stripe)
        m.stripe_enabled = lambda: True

        def _session(**kw):
            self.sessions.append(kw)
            return _Made(**kw)

        m.stripe.checkout.Session.create = _session
        m.stripe.Customer.create = lambda **kw: _Made(**kw)
        m.card_from_stripe = lambda ref: None
        return self

    def __exit__(self, *_exc):
        (m.stripe_enabled, m.stripe.checkout.Session.create,
         m.stripe.Customer.create, m.card_from_stripe) = self.saved
        return False


def _cleanup():
    conn = db()
    ids = ("(SELECT id FROM workshop_bookings WHERE guest_email LIKE ? "
           "OR reference_code LIKE ?)")
    args = (TAG.lower() + "%", TAG + "%")
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages",
                  "workshop_custom_field_responses"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", args)
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ? OR reference_code LIKE ?",
                 args)
    conn.execute("DELETE FROM workshop_waitlist WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.execute("""DELETE FROM workshop_sessions WHERE workshop_id IN
                    (SELECT id FROM workshops WHERE title LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (f"%{TAG}%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.execute("DELETE FROM mail_log WHERE to_address LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _workshop(title, *, private=True, organiser_email=ORGANISER_EMAIL, deposit=20):
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent, private_token, organiser_name,
           organiser_email) VALUES (?, '', 3000, 10, 1, 96, ?, ?, ?, ?, ?)""",
        (title, now, deposit, m.new_private_token() if private else None,
         ORGANISER if private else None, organiser_email if private else None))
    row = conn.execute("SELECT * FROM workshops WHERE title = ?", (title,)).fetchone()
    conn.commit()
    conn.close()
    return row


def _sitting(workshop_id, start, nights=4, capacity=10, balance_due_on=None):
    conn = db()
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity,
           balance_due_on, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (workshop_id, start.isoformat(), (start + timedelta(days=nights)).isoformat(), capacity,
         balance_due_on, _harness.datetime_now()))
    sid = conn.execute("SELECT MAX(id) AS i FROM workshop_sessions WHERE workshop_id = ?",
                       (workshop_id,)).fetchone()["i"]
    conn.commit()
    conn.close()
    return sid


def _token(workshop_id):
    conn = db()
    t = conn.execute("SELECT private_token FROM workshops WHERE id = ?",
                     (workshop_id,)).fetchone()["private_token"]
    conn.close()
    return t


def _register(path, who, extra=None, party=1):
    """Post a registration form as a guest would, from an address of its own."""
    _ip[0] += 1
    data = {"guest_name": f"{TAG} {who}", "guest_email": f"{TAG.lower()}.{who.lower()}@example.invalid",
            "party_size": str(party), "occupancy_type": "double",
            "dietary_notes": "No shellfish", "medical_notes": "Asthma",
            "guest_phone": "+33 6 12 34 56 78", **WORKSHOP_DETAILS, **(extra or {})}
    r = m.app.test_client().post(path, data=data,
                                 environ_base={"REMOTE_ADDR": f"198.51.100.{_ip[0]}"})
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE guest_email = ?",
                       (data["guest_email"],)).fetchone()
    conn.close()
    return r, row


def _paid(bid, amount, suffix):
    session = {"id": f"cs_{TAG}_{suffix}", "amount_total": int(round(amount * 100)),
               "payment_status": "paid",
               "metadata": {"workshop_booking_id": str(bid), "kind": "workshop_deposit"}}
    conn = db()
    with m.app.test_request_context():
        outcome = m.mark_workshop_payment_paid(conn, session)
    conn.close()
    return outcome


def _to(address):
    conn = db()
    rows = conn.execute("SELECT subject, body FROM email_outbox WHERE to_address = ? ORDER BY id",
                        (address,)).fetchall()
    conn.close()
    return rows


def _row(bid):
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _in_request(fn, *args, **kwargs):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return fn(conn, *args, **kwargs)
    finally:
        conn.close()


def _events(body):
    out = []
    for block in body.split("BEGIN:VEVENT")[1:]:
        f = dict(re.findall(r"^([A-Z]+)(?:;[^:]*)?:(.*?)\r?$", block, re.M))
        iso = lambda v: f"{v[:4]}-{v[4:6]}-{v[6:8]}" if v else None
        out.append((f.get("UID"), iso(f.get("DTSTART")), iso(f.get("DTEND")), f.get("SUMMARY")))
    return out


def run():
    s = Suite("Private stays: hidden ateliers booked only by their link")
    oc, _ec, _owner, _emp = clients()
    anon = m.app.test_client()
    _cleanup()
    room = ensure_room()
    conn = db()
    room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room["id"],)).fetchone()
    conn.close()

    # A public atelier beside it, so "absent" is never the page being empty.
    public = _workshop(f"{TAG} Open Atelier", private=False)
    w0 = free_window(room["id"], 12, after_days=120, clear_of_ateliers=True)
    _sitting(public["id"], w0 + timedelta(days=200))
    private = _workshop(TITLE)
    start = w0 + timedelta(days=2)
    sid = _sitting(private["id"], start, nights=4)
    token = private["private_token"]

    s.section("The one definition, and the schema under it")
    s.check("WORKSHOP_IS_PUBLIC reads the token", m.WORKSHOP_IS_PUBLIC == "(workshops.private_token IS NULL)")
    conn = db()
    idx = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'idx_workshops_private_token'").fetchone()
    try:
        conn.execute("UPDATE workshops SET private_token = ? WHERE id = ?", (token, public["id"]))
        unique = False
    except m.sqlite3.IntegrityError:
        unique = True
    conn.rollback()
    conn.close()
    s.check("one atelier per link: the token is a unique index", idx is not None and unique,
            detail=str(idx and idx["sql"]))
    s.check("a token is unguessable", len(token) >= 16, detail=token)

    # ------------------------------------------------------------------
    s.section("Everything that OFFERS an atelier leaves it out")
    listing = anon.get("/workshops").get_data(as_text=True)
    s.check("the public list carries the public atelier (so the check below means something)",
            f"{TAG} Open Atelier" in listing)
    s.check("and not the private stay", TITLE not in listing and token not in listing)
    home = anon.get("/").get_data(as_text=True)
    s.check("the home page's next dates leave it out", TITLE not in home)
    with m.app.test_request_context():
        panel_pages = [m.url_for(e) for e in ("facilities_page", "gallery_page", "press",
                                              "story") if e in m.app.view_functions]
    for path in panel_pages:
        body = anon.get(path).get_data(as_text=True)
        s.check(f"the stay panel on {path} leaves it out", TITLE not in body)
    s.check("the stay panel pages were found", len(panel_pages) >= 2, detail=str(panel_pages))
    sitemap = anon.get("/sitemap.xml").get_data(as_text=True)
    s.check("the site map leaves it out", f"/workshops/{private['id']}<" not in sitemap
            and "/p/" not in sitemap and f"/workshops/{public['id']}<" in sitemap,
            detail=sitemap[-400:])
    whats_on = anon.get("/whats-on").get_data(as_text=True)
    s.check("What's On leaves it out", TITLE not in whats_on)
    conn = db()
    with m.app.test_request_context("/"):
        letter = [w["title"] for w in m.newsletter_workshops(conn, limit=50)]
        offered = [w["title"] for w in m.offered_workshops()]
        snapshot = [w["title"] for w in m.current_offerings_snapshot(conn)["upcoming_workshop_sessions"]]
        facts = m.live_facts_for_text(conn, f"Do you have anything from {start.isoformat()} "
                                            f"to {(start + timedelta(days=3)).isoformat()}?")
        reference = [w["title"] for w in m.house_reference(conn)["ateliers"]]
        deposit_shown = m.workshop_deposit_to_show(conn)
    conn.close()
    s.check("the newsletter's list of what is on leaves it out", TITLE not in letter)
    s.check("the stay panel's workshops leave it out", TITLE not in offered)
    s.check("the reply-drafting facts leave it out", TITLE not in snapshot, detail=str(snapshot))
    s.check("the concierge's facts leave it out",
            TITLE not in [a["title"] for a in facts["ateliers"]], detail=str(facts["ateliers"]))
    s.check("the email drafter's house facts leave it out", TITLE not in reference)
    s.check("its 20% deposit is no part of the deposit the public list states",
            deposit_shown != 20 or all(
                (r["deposit_percent"] or 20) == 20 for r in db().execute(
                    f"SELECT deposit_percent FROM workshops WHERE active = 1 AND {m.WORKSHOP_IS_PUBLIC}")),
            detail=str(deposit_shown))
    r = anon.get(f"/workshops/{private['id']}")
    s.check("its numbered page is a plain 404, not a redirect to its link",
            r.status_code == 404 and token not in r.get_data(as_text=True), detail=str(r.status_code))

    # ------------------------------------------------------------------
    s.section("The link, and only the link")
    r = anon.get(f"/p/{token}")
    page = r.get_data(as_text=True)
    s.check("/p/<token> answers", r.status_code == 200, detail=str(r.status_code))
    s.check("it is never indexed: header", r.headers.get("X-Robots-Tag") == "noindex, nofollow")
    s.check("and meta", bool(NOINDEX.search(page)))
    was_live = m.SITE_IS_LIVE
    try:
        m.SITE_IS_LIVE = True
        live_page = anon.get(f"/p/{token}").get_data(as_text=True)
        s.check("and the meta is there when the site is live, not only while staging",
                bool(NOINDEX.search(live_page)))
        live_public = anon.get(f"/workshops/{public['id']}").get_data(as_text=True)
        s.check("while a public atelier's page stays indexable", not NOINDEX.search(live_public))
    finally:
        m.SITE_IS_LIVE = was_live
    s.check("the key never leaves in a Referer", r.headers.get("Referrer-Policy") == "no-referrer")
    s.check("a wrong token is a 404", anon.get(f"/p/{token}x").status_code == 404)
    text = visible_text(page)
    s.check("it says who arranged it, under the title", f"Arranged by {ORGANISER}" in text)
    # The page's own words, not the footer's list of the house's inboxes.
    main = visible_text(page[page.find("<main"):page.find("</main>")])
    s.check("it never calls itself a workshop", main and "workshop" not in main.lower()
            and "A workshop holds" not in page,
            detail=str([main[max(0, i - 60):i + 40] for i in
                        [mt.start() for mt in re.finditer("(?i)workshop", main)]][:3]))
    catalogue = [h for h in re.findall(r'href="([^"]+)"', page)
                 if re.match(r"^(https?://[^/]+)?/workshops(/\d+)?([#?\"]|$)", h)]
    s.check("nothing on it leads to the public catalogue", not catalogue, detail=str(catalogue))
    s.check("no newsletter sign-up", "/newsletter/subscribe" not in page
            and "Subscribe to our newsletter" not in page)
    s.check("no compare, and no other atelier", "Compare the retreats" not in page
            and f"{TAG} Open Atelier" not in page)
    register = [h for h in re.findall(r'href="([^"]+)"', page) if "/workshops/register/" in h]
    s.check("its dates are booked through the form, carrying the key",
            register and all(f"k={token}" in h for h in register), detail=str(register))

    path = f"/workshops/register/{sid}"
    s.check("the form without the key is a 404", anon.get(path).status_code == 404)
    # One character off. Never "always end it in x": a token can end in x
    # already, and then the wrong key is the right one (it did, once in 64).
    wrong = token[:-1] + ("y" if token[-1] == "x" else "x")
    s.check("with a wrong key, a 404", wrong != token and anon.get(path + "?k=" + wrong).status_code == 404)
    r = anon.get(path + "?k=" + token)
    form = r.get_data(as_text=True)
    s.check("with the right key it answers", r.status_code == 200, detail=str(r.status_code))
    s.check("never indexed, never referred",
            r.headers.get("X-Robots-Tag") == "noindex, nofollow"
            and r.headers.get("Referrer-Policy") == "no-referrer")
    s.check("the key rides in the form, so the POST carries it",
            f'name="k" value="{token}"' in form)
    s.check("the form leads back to the stay, not to the catalogue",
            f"/p/{token}" in form and "Compare the retreats" not in form
            and not re.search(r'href="(https?://[^/]+)?/workshops(#|"|\?)', form))
    _r, nothing = _register(path, "Stranger")
    s.check("a POST without the key takes no place", nothing is None and _r.status_code == 404,
            detail=str(_r.status_code))

    # ------------------------------------------------------------------
    s.section("Registering through the link: the place, the deposit, the confirmation")
    with _CardMachine() as card:
        r, row = _register(path, "Barbara", {"k": token})
    s.check("the guest is sent to the card page", r.status_code == 303
            and r.headers.get("Location") == FAKE_URL, detail=f"{r.status_code} {r.headers.get('Location')}")
    s.check("and the hand-off to Stripe sends no Referer either",
            r.headers.get("Referrer-Policy") == "no-referrer")
    s.check("the registration exists", row is not None)
    if row is None:
        _cleanup()
        return s
    bid = row["id"]
    s.check("a 20% deposit is asked for", round(row["deposit_amount"] or 0, 2) == 600.0,
            detail=str(row["deposit_amount"]))
    s.check("the organiser hears nothing while the place is only held",
            not _to(ORGANISER_EMAIL), detail=str([t["subject"] for t in _to(ORGANISER_EMAIL)]))
    with _CardMachine():
        outcome = _paid(bid, row["deposit_amount"], "barbara")
    after = _row(bid)
    s.check("the deposit confirms the place", outcome == "confirmed" and after["status"] == "confirmed"
            and after["deposit_paid_at"], detail=f"{outcome} {after['status']}")
    conn = db()
    letters = conn.execute(
        """SELECT subject, body FROM email_outbox WHERE to_address = ? ORDER BY id""",
        (row["guest_email"],)).fetchall()
    conn.close()
    s.check("the confirmation is written", any("confirm" in (l["subject"] or "").lower()
                                               or "confirm" in (l["body"] or "").lower()
                                               for l in letters),
            detail=str([l["subject"] for l in letters]))
    links = sorted({u for l in letters for u in re.findall(r"https?://[^\s\"'<>)]+", l["body"] or "")
                    if "/workshops/" in u or "/p/" in u or "/book" in u})
    broken = []
    for u in links:
        here = re.sub(r"^https?://[^/]+", "", u).rstrip(".,")
        got = anon.get(here, follow_redirects=True)
        if got.status_code != 200:
            broken.append(f"{here} -> {got.status_code}")
    s.check("every link in the confirmation works", links and not broken,
            detail=f"links={links} broken={broken}")
    notes = _to(ORGANISER_EMAIL)
    s.check("the organiser is told once: one registration, its deposit paid",
            len(notes) == 1 and "Deposit paid." in (notes[0]["body"] if notes else ""),
            detail=str([(n["subject"], n["body"][:120]) for n in notes]))
    body = notes[0]["body"] if notes else ""
    s.check("the note names the guest, the stay and the dates",
            f"{TAG} Barbara" in body and TITLE in body
            and m.format_date_range(start.isoformat(),
                                    (start + timedelta(days=4)).isoformat()) in body, detail=body)
    s.check("and carries nothing else: no dietary, medical, telephone, address or amounts",
            not any(x in body for x in ("shellfish", "Asthma", "+33", "12 34", "€", "600",
                                        WORKSHOP_DETAILS.get("guest_address", "@@"))), detail=body)
    page = anon.get(f"/p/{token}").get_data(as_text=True)
    s.check("places left are shown, who else is coming is not",
            f"{TAG} Barbara" not in page)

    s.section("Booked without a card: registered, and later paid")
    r, carla = _register(path, "Carla", {"k": token})
    s.check("with Stripe off the place is confirmed at once",
            carla is not None and carla["status"] == "confirmed", detail=str(carla and carla["status"]))
    notes = _to(ORGANISER_EMAIL)
    s.check("the organiser is told: registered, not yet paid",
            len(notes) == 2 and "Registered, not yet paid." in notes[-1]["body"]
            and f"{TAG} Carla" in notes[-1]["body"], detail=str([n["body"][-80:] for n in notes]))
    if carla:
        oc.post(f"/admin/workshops/registrations/{carla['id']}/mark-deposit-paid",
                data={"method": "bank_transfer"})
        notes = _to(ORGANISER_EMAIL)
        s.check("and told again when the deposit arrives",
                len(notes) == 3 and "Deposit paid." in notes[-1]["body"]
                and f"{TAG} Carla" in notes[-1]["body"], detail=str([n["body"][-60:] for n in notes]))

    s.section("The organiser's note answers to Guest emails: off")
    real = m.guest_letter_held_why
    try:
        m.guest_letter_held_why = _harness.REAL_GUEST_LETTER_HELD_WHY
        conn = db()
        held = conn.execute("SELECT 1 FROM guest_letter_approvals WHERE letter_key = 'code:tell_the_organiser'").fetchone()
        conn.close()
        with m.app.test_request_context("/"):
            why = m.guest_letter_held_why("code:tell_the_organiser")
        s.check("unapproved, it is held like any other letter the house writes by itself",
                held is not None or why, detail=repr(why))
    finally:
        m.guest_letter_held_why = real
    guest_emails = oc.get("/admin/guest-emails").get_data(as_text=True)
    s.check("and it is on the Guest Emails page to be read and approved, both endings shown",
            "a note to its organiser" in guest_emails and "Deposit paid." in guest_emails
            and "Registered, not yet paid." in guest_emails and "{status}" not in guest_emails)

    s.section("No organiser address, no note")
    conn = db()
    conn.execute("UPDATE workshops SET organiser_email = NULL WHERE id = ?", (private["id"],))
    conn.commit()
    conn.close()
    _r, dora = _register(path, "Dora", {"k": token})
    s.check("a registration with the address blank writes to nobody",
            dora is not None and len(_to(ORGANISER_EMAIL)) == 3)
    page = anon.get(path + "?k=" + token).get_data(as_text=True)
    s.check("and the form no longer says the organiser is told", "is told that you have registered" not in page)

    # ------------------------------------------------------------------
    s.section("The waiting list answers only to the link")
    w = {"session_id": str(sid), "name": f"{TAG} Wait", "email": f"{TAG.lower()}.wait@example.invalid",
         "party_size": "1"}
    s.check("joining without the key is a 404",
            anon.post("/workshops/waitlist/join", data=w).status_code == 404)
    r = anon.post("/workshops/waitlist/join", data={**w, "k": token})
    conn = db()
    joined = conn.execute("SELECT COUNT(*) AS n FROM workshop_waitlist WHERE email = ?",
                          (w["email"],)).fetchone()["n"]
    conn.close()
    s.check("with it, they are on the list, and sent back to the form with the key",
            joined == 1 and f"k={token}" in (r.headers.get("Location") or ""),
            detail=f"{joined} {r.headers.get('Location')}")
    wl = oc.get("/admin/workshops/waitlist").get_data(as_text=True)
    s.check("the owner's 'register them' link carries the key", f"k={token}" in wl)

    # ------------------------------------------------------------------
    s.section("It still RUNS the house like any atelier")
    ok, why = _in_request(m.is_range_available, room["id"], start + timedelta(days=1),
                          start + timedelta(days=2))
    s.check("a nightly booking on its dates is refused", not ok)
    s.check("with 'held for a private stay', and no title",
            why == "Those dates are held for a private stay." and TAG not in (why or ""), detail=repr(why))
    picture = _in_request(m._availability_picture, fresh=True)
    s.check("the room calendar's own words say the same, with no title",
            picture["night_label"].get(m.PRIVATE_STAY_WHY) == "Held for a private stay"
            and not any(TITLE in v for v in picture["night_label"].values()))
    feed = anon.get(f"/ics/{room['export_token']}.ics").get_data(as_text=True)
    ws = [e for e in _events(feed) if e[0] == f"ws-{sid}@gudanes-hr.local"]
    s.check("the room .ics closes its nights for Booking.com, with the day either side",
            [(e[1], e[2]) for e in ws]
            == [((start - timedelta(days=1)).isoformat(), (start + timedelta(days=5)).isoformat())],
            detail=str(ws))
    s.check("and says nothing of whose stay it is", TITLE not in feed and ORGANISER not in feed)
    house = [e for e in _events(feed) if (e[0] or "").startswith("house-")
             and e[1] == start.isoformat()]
    s.check("not closed twice (it is an atelier, not a house-wide event)", not house, detail=str(house))
    with m.app.test_request_context():
        room_path = m.url_for("book_room", room_id=room["id"]) if "book_room" in m.app.view_functions else None
    if room_path:
        rp = anon.get(room_path).get_data(as_text=True)
        s.check("the room page's calendar never names it", TITLE not in rp)
    owner_list = oc.get("/admin/workshops").get_data(as_text=True)
    s.check("the owner's workshops page has it, badged Private",
            TITLE in owner_list and "Private" in owner_list)
    regs = oc.get(f"/admin/workshops/registrations?session_id={sid}&when=all").get_data(as_text=True)
    s.check("so does the registrations list", f"{TAG} Barbara" in regs and ">Private<" in regs)

    # ------------------------------------------------------------------
    s.section("The owner's controls")
    admin = oc.get(f"/admin/workshops/{private['id']}").get_data(as_text=True)
    link = re.search(r'value="([^"]+/p/[^"]+)"[^>]*data-private-link', admin)
    s.check("the workshop's page shows the full link to send, with Copy",
            link and link.group(1).endswith(f"/p/{token}") and "data-private-copy" in admin,
            detail=str(link and link.group(1)))
    s.check("and its own page is the private one", f'href="/p/{token}"' in admin)
    r = oc.post(f"/admin/workshops/{private['id']}/new-link", follow_redirects=True)
    new = _token(private["id"])
    s.check("Make a new link changes it, and says the old one stops working",
            new and new != token and any("no longer works" in f for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("the old link is a 404", anon.get(f"/p/{token}").status_code == 404)
    s.check("and so is its form", anon.get(path + "?k=" + token).status_code == 404)
    s.check("the new link works", anon.get(f"/p/{new}").status_code == 200)
    s.check("guests already registered keep their manage link",
            anon.get(f"/workshops/manage/{row['manage_token']}").status_code == 200)
    manage = anon.get(f"/workshops/manage/{row['manage_token']}").get_data(as_text=True)
    s.check("which does not call it a workshop", "Your workshop" not in manage
            and "another workshop" not in manage)

    s.section("The form makes one private, and public again")
    form = {"title": f"{TAG} Family", "price_per_person": "1500", "default_capacity": "8",
            "deposit_percent": "20", "private": "1", "organiser_name": f"{TAG} Family Organiser",
            "organiser_email": "not an address"}
    oc.post("/admin/workshops/new", data=form)
    conn = db()
    bad = conn.execute("SELECT 1 FROM workshops WHERE title = ?", (form["title"],)).fetchone()
    conn.close()
    s.check("an organiser address that is not one is refused", bad is None)
    form["organiser_email"] = f"{TAG.lower()}.family@example.invalid"
    oc.post("/admin/workshops/new", data=form)
    conn = db()
    fam = conn.execute("SELECT * FROM workshops WHERE title = ?", (form["title"],)).fetchone()
    conn.close()
    s.check("ticking Private makes a link", fam is not None and fam["private_token"]
            and fam["organiser_email"] == form["organiser_email"])
    if fam:
        edit = {**form, "active": "on"}
        edit.pop("private")
        r = oc.post(f"/admin/workshops/{fam['id']}/edit", data=edit, follow_redirects=True)
        conn = db()
        fam2 = conn.execute("SELECT * FROM workshops WHERE id = ?", (fam["id"],)).fetchone()
        conn.close()
        s.check("unticking it makes it public, and its link stops working",
                fam2["private_token"] is None and anon.get(f"/p/{fam['private_token']}").status_code == 404
                and any("public again" in f for f in flashes(r)), detail=str(flashes(r)))
        s.check("and its numbered page answers again",
                anon.get(f"/workshops/{fam['id']}").status_code in (200, 302))
    shown = oc.get("/admin/workshops/new").get_data(as_text=True)
    s.check("the form asks it in the owner's words",
            "Private: only people with the link can see and book it" in shown)

    # ------------------------------------------------------------------
    s.section("A fixed balance date on the sitting")
    due = start - timedelta(days=60)
    later_start = start + timedelta(days=400)
    sid2 = _sitting(private["id"], later_start, nights=4, balance_due_on=(later_start - timedelta(days=200)).isoformat())
    new = _token(private["id"])
    r, eve = _register(f"/workshops/register/{sid2}", "Eve", {"k": new})
    s.check("a registration falls due on that day, not thirty days before",
            eve is not None and eve["balance_due_date"] == (later_start - timedelta(days=200)).isoformat(),
            detail=str(eve and eve["balance_due_date"]))
    s.check("and pays the deposit, the rest later",
            eve is not None and round(eve["deposit_amount"], 2) == 600.0 and round(eve["balance_amount"], 2) == 2400.0,
            detail=str(eve and (eve["deposit_amount"], eve["balance_amount"])))
    form_page = anon.get(f"/workshops/register/{sid2}?k={new}").get_data(as_text=True)
    s.check("the form states that date", m.format_date_human((later_start - timedelta(days=200)).isoformat())
            in visible_text(form_page))
    s.check("and its payment sums are worked out from it",
            f"var dueOn = '{(later_start - timedelta(days=200)).isoformat()}'" in form_page)
    def _due_on(day):
        return oc.post(f"/admin/workshops/sessions/{sid2}/edit",
                       data={"capacity": "10", "start_date": later_start.isoformat(),
                             "end_date": (later_start + timedelta(days=4)).isoformat(),
                             "balance_due_on": day, "back_session": str(sid2)},
                       follow_redirects=True)

    moved_to = (later_start - timedelta(days=100)).isoformat()
    r = _due_on(moved_to)
    conn = db()
    saved = conn.execute("SELECT balance_due_on FROM workshop_sessions WHERE id = ?", (sid2,)).fetchone()
    conn.close()
    s.check("the sitting's form sets it", saved["balance_due_on"] == moved_to, detail=str(dict(saved)))
    moved = _row(eve["id"]) if eve else None
    s.check("and changing it moves the balances already owed, and says to when",
            moved is not None and moved["balance_due_date"] == moved_to
            and any(m.format_date_human(moved_to) in f for f in flashes(r)),
            detail=f"{moved and moved['balance_due_date']} {flashes(r)}")
    r = _due_on((later_start + timedelta(days=1)).isoformat())
    s.check("a due date after the sitting starts is refused",
            any("after the sitting has started" in f for f in flashes(r)), detail=str(flashes(r)))
    past_due = (house_today() - timedelta(days=1)).isoformat()
    _due_on(past_due)
    _r, fay = _register(f"/workshops/register/{sid2}", "Fay", {"k": new})
    s.check("booked after it, the whole price is due now",
            fay is not None and round(fay["deposit_amount"], 2) == 3000.0
            and not fay["balance_amount"] and fay["balance_due_date"] is None,
            detail=str(fay and (fay["deposit_amount"], fay["balance_amount"], fay["balance_due_date"])))
    with m.app.test_request_context("/"):
        d, b, when = m.compute_workshop_payment_terms(1000, 10, house_today() + timedelta(days=90))
        d2, b2, when2 = m.compute_workshop_payment_terms(1000, 10, house_today() + timedelta(days=20))
    s.check("without one, the thirty days stand as before",
            (d, b, when) == (100.0, 900.0, (house_today() + timedelta(days=60)).isoformat())
            and (d2, b2, when2) == (1000.0, 0.0, None), detail=f"{(d, b, when)} {(d2, b2, when2)}")

    s.section("The privacy notice says what the code now does")
    privacy = visible_text(anon.get("/privacy").get_data(as_text=True))
    s.check("it says an organiser is told of a registration, and of nothing else",
            "private stay" in privacy and "whether your deposit is paid" in privacy)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
