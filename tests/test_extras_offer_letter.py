"""The letter before arrival that offers what a stay can still add.

The owner asked for it on 22 October 2026: about a week out, a letter that
offers the stay's extras -- the tour, the crémant board, a transfer, dinner --
each with a link that puts it on the stay through the guest's own booking page.
The same day the two guest texts were switched off because their default
wording had been made up ("Keys in the bowl by the door"), so this letter is
held to the rules that came out of that, and they are what is checked here:

  - OFF BY DEFAULT, and IN THE OWNER'S WORDS. It ships with no wording, and
    switched on with none it sends nothing to anybody.
  - ONCE PER STAY, and never to a stay that is cancelled, already here, or too
    close to arrival -- nor while Write to guests is off, because an offer kept
    in the outbox and sent a fortnight late offers things the stay no longer
    has the notice for.
  - NOTHING ALREADY ON THE STAY, nothing it has not the notice for.
  - NOTHING ABOUT PAYING. Extras are settled at the house and guests are not
    told so; the letter's wording is refused if it raises money at all.
  - THE LINK WORKS: it opens the booking page at that extra, and the Add button
    there puts it on the stay. Following a link adds nothing by itself -- a
    mail scanner follows every link in a letter.

send_email is replaced for every send; nothing here can reach a guest.
"""
import html as htmllib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from _harness import Suite, clients, db, forms_on, fill, house_today, keep_rows
import _harness

m = _harness.m
TAG = "ZZXO"
KEY = "room_extras_offer"
SWITCH = "automation_extras_offer_enabled"
DAYS = "automation_extras_offer_days_before"
SETTINGS = (SWITCH, DAYS, "guest_mail_live", "dining_mode")
WORDING = ("Before you come to {room_name}",
           "Hi {guest_name},\n\nWe look forward to {arrival_date}. If you would like "
           "any of these while you are here:\n\n{extras_block}\n\n"
           "Your booking page:\n{manage_url}\n\n— Château de Gudanes")


def flashes(response):
    """The page's messages as a person reads them: the apostrophe in
    "isn't" arrives as &#39; in the markup."""
    return [htmllib.unescape(f) for f in _harness.flashes(response)]


def _cleanup(conn):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))]
    marks = ",".join("?" * len(ids))
    if ids:
        conn.execute(f"DELETE FROM booking_extras WHERE category = 'room' "
                     f"AND booking_id IN ({marks})", ids)
        conn.execute(f"UPDATE bookings SET party_id = NULL WHERE id IN ({marks})", ids)
    conn.execute("DELETE FROM booking_parties WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM extras WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_optouts WHERE email LIKE ?", (TAG.lower() + ".%",))
    conn.commit()


def _set(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def _wording(conn, subject, body):
    conn.execute("UPDATE email_templates SET subject = ?, body = ? WHERE template_key = ?",
                 (subject, body, KEY))
    conn.commit()


def _stay(conn, ref, arrive_in, *, status="confirmed", nights=3, email=None):
    room = _harness.ensure_room()
    arrival = house_today() + timedelta(days=arrive_in)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name,
           guest_email, arrival_date, departure_date, party_size, status,
           total_price, amount_paid, city_tax, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, 600, 600, 0, ?)""",
        (room["id"], f"{TAG}-{ref}", f"tok{TAG}{ref}{_harness.secrets_token()}".lower(),
         f"Marie {TAG}{ref}",
         f"{TAG.lower()}.{ref.lower()}@example.invalid" if email is None else email,
         arrival.isoformat(), (arrival + timedelta(days=nights)).isoformat(), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return _row(conn, ref)


def _row(conn, ref):
    return conn.execute(
        """SELECT bookings.*, rooms.name AS room_name FROM bookings
             JOIN rooms ON rooms.id = bookings.room_id
            WHERE reference_code = ?""", (f"{TAG}-{ref}",)).fetchone()


def _extra(conn, name, *, lead=0, max_qty=None, bookable=1, active=1, ask_when=0,
           price=40.0, description=None):
    conn.execute(
        """INSERT INTO extras (name, price, active, sort_order, category, description,
                               lead_time_days, max_qty, guest_bookable, ask_when)
           VALUES (?, ?, ?, 0, 'other', ?, ?, ?, ?, ?)""",
        (f"{TAG} {name}", price, active, description, lead, max_qty, bookable, ask_when))
    conn.commit()
    return conn.execute("SELECT * FROM extras WHERE name = ?", (f"{TAG} {name}",)).fetchone()


def _run(provider=True, **kw):
    """Run the job with a stand-in for the post office. (result, letters, raised)."""
    letters = []

    def post(to, subject, body, **opts):
        letters.append({"to": to, "subject": subject, "body": body,
                        "html": opts.get("html") or ""})
        return provider

    was = m.send_email
    m.send_email = post
    conn = db()
    try:
        with m.app.test_request_context("/", base_url="https://www.chateaugudanes.com"):
            try:
                return m.run_extras_offer_job(conn, **kw), letters, None
            except m.JobNotSetUp as e:
                return None, letters, str(e)
    finally:
        conn.close()
        m.send_email = was


def _to(letters, ref):
    return [x for x in letters if f".{ref.lower()}@" in x["to"]]


def _stamp(conn, ref):
    r = _row(conn, ref)
    return r["extras_offer_sent_at"], r["extras_offer_outcome"]


def _offers(conn, ref):
    with m.app.test_request_context("/", base_url="https://www.chateaugudanes.com"):
        return m.extras_offers_for_stay(conn, _row(conn, ref))


def run():
    s = Suite("The letter before arrival offering a stay's extras")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    put_settings = keep_rows(conn, "app_settings",
                             "key IN (%s)" % ",".join("?" * len(SETTINGS)), SETTINGS)
    put_letter = keep_rows(conn, "email_templates", "template_key = ?", (KEY,))
    put_restaurant = keep_rows(conn, "restaurant_settings", "id = 1")
    _cleanup(conn)
    # Every letter in the suite goes to a stand-in, including the note the
    # house gets when the guest presses Add; _run swaps in its own for the job.
    was = m.send_email
    m.send_email = lambda *a, **kw: True
    try:
        _checks(s, oc, conn)
    finally:
        m.send_email = was
        _cleanup(conn)
        put_restaurant()
        put_letter()
        put_settings()
        conn.close()
    return s


def _checks(s, oc, conn):
    s.section("Off, and with no words of the house's own")
    s.check("the switch ships off",
            m.AUTOMATION_SETTING_DEFAULTS.get(SWITCH) == "0",
            detail=str(m.AUTOMATION_SETTING_DEFAULTS.get(SWITCH)))
    s.check("about a week before arrival unless the owner says otherwise",
            m.AUTOMATION_SETTING_DEFAULTS.get(DAYS) == "7")
    job = {name: key for name, key, _i, _c, _f in m.AUTOMATION_JOBS}
    s.check("and the scheduler asks that switch before it runs the job",
            job.get("extras_offer") == SWITCH, detail=str(job.get("extras_offer")))
    shipped = {k: (sj, b) for k, _l, sj, b in m.DEFAULT_EMAIL_TEMPLATES}
    s.check("the letter ships with no subject and no body",
            shipped.get(KEY) == ("", ""), detail=repr(shipped.get(KEY)))
    s.check("and is one of the letters that ship unwritten", KEY in m.UNWRITTEN_LETTERS)
    s.check("it is not one of the letters that carry the payment sentence",
            KEY not in m.LETTERS_WITH_PAYMENT_LINE)

    # A clean slate: the switch as shipped, the letter as shipped, the house
    # writing to guests, and no restaurant in the way until it is asked about.
    _set(conn, SWITCH, None)
    _set(conn, DAYS, None)
    _set(conn, "guest_mail_live", "1")
    _set(conn, "dining_mode", "hidden")
    _wording(conn, "", "")
    soon = _stay(conn, "SOON", 5)

    result, letters, why = _run()
    s.check("left as shipped, it sends nothing", not letters, detail=str(letters[:1]))
    s.check("and says it is switched off", why and "switched off" in why, detail=str(why))
    s.check("and stamps nobody", _stamp(conn, "SOON") == (None, None))

    r = oc.post("/admin/automation/run/extras_offer", follow_redirects=True)
    s.check("Run now on a switched-off letter sends nothing either",
            any("Couldn't run" in f for f in flashes(r)), detail=str(flashes(r)))

    s.section("Switched on with nothing written, still nothing")
    _set(conn, SWITCH, "1")
    result, letters, why = _run()
    s.check("no letter goes", not letters, detail=str(letters[:1]))
    s.check("and the reason is the wording", why and "no wording" in why, detail=str(why))
    s.check("nobody is stamped, so writing it later still reaches them",
            _stamp(conn, "SOON") == (None, None))
    with m.app.test_request_context("/"):
        rendered = m.render_email_template(conn, KEY, {"guest_name": "Marie"})
    s.check("and the shipped wording cannot be drawn into a letter",
            rendered == (None, None, None), detail=str(rendered)[:120])
    page = oc.get("/management/email-templates").get_data(as_text=True)
    card = page[page.find(f'id="tpl-{KEY}"'):][:900]
    s.check("the wording page calls it not written yet", "not written yet" in card,
            detail=card[:300])
    r = oc.post(f"/management/email-templates/{KEY}/restore", follow_redirects=True)
    s.check("and there is no original to restore it to",
            any("no wording" in f for f in flashes(r)), detail=str(flashes(r)))
    auto = oc.get("/admin/automation").get_data(as_text=True)
    at = auto.find(f'id="{SWITCH}"')
    row = auto[at:auto.find('<div class="auto-row', at + 10)] if at >= 0 else ""
    s.check("the automation page names it under the letters to guests",
            "Offer what a guest can add, before they arrive" in row
            and "Writes to guests" in row, detail=row[:200])
    s.check("and says it has no wording yet, with the way to write it",
            "No wording yet" in row and f"#tpl-{KEY}" in row)

    s.section("The owner's words, and never a word about paying")
    for sentence in ("Pay for them when you arrive.", "It all goes on your bill.",
                     "Settle up at the house.", "Everything is payable onsite."):
        body = WORDING[1].replace("We look forward", sentence + " We look forward")
        r = oc.post(f"/management/email-templates/{KEY}/edit",
                    data={"subject": WORDING[0], "body": body}, follow_redirects=True)
        stored = conn.execute("SELECT body FROM email_templates WHERE template_key = ?",
                              (KEY,)).fetchone()["body"]
        s.check(f"refused: “{sentence}”",
                stored == "" and any("never mentions paying" in f for f in flashes(r)),
                detail=str(flashes(r))[:200])
    r = oc.post(f"/management/email-templates/{KEY}/edit",
                data={"subject": WORDING[0], "body": WORDING[1].replace("{extras_block}", "")},
                follow_redirects=True)
    s.check("and refused without the list of what they can add",
            any("{extras_block}" in f for f in flashes(r)), detail=str(flashes(r))[:200])
    r = oc.post(f"/management/email-templates/{KEY}/edit",
                data={"subject": WORDING[0], "body": WORDING[1]}, follow_redirects=True)
    stored = conn.execute("SELECT subject, body FROM email_templates WHERE template_key = ?",
                          (KEY,)).fetchone()
    s.check("the owner's own wording saves", (stored["subject"], stored["body"]) == WORDING,
            detail=str(flashes(r)))
    # The editor's preview draws the catalogue as it stands.
    preview = oc.post(f"/management/email-templates/{KEY}/preview",
                      json={"subject": WORDING[0], "body": WORDING[1]}).get_json() or {}
    s.check("the editor's preview draws the cards", "Add to your stay" in (preview.get("html") or "")
            and not preview.get("refused"), detail=str(preview.get("why")))

    s.section("Once, to the right stays, offering the right things")
    tasting = _extra(conn, "tasting", max_qty=1, description="In the cellar, before dinner.")
    longer = _extra(conn, "far ahead", lead=30)
    _extra(conn, "staff only", bookable=0)
    _extra(conn, "withdrawn", active=0)
    _extra(conn, "short notice", lead=3)
    taken = _stay(conn, "TAKEN", 6)
    m.add_booking_extra(conn, "room", taken["id"], tasting, 1, notes="Added by the guest")
    conn.commit()
    cancelled = _stay(conn, "GONE", 5, status="cancelled")
    pending = _stay(conn, "WAIT", 5, status="pending")
    near = _stay(conn, "NEAR", 1)
    later = _stay(conn, "LATER", 20)
    here = _stay(conn, "HERE", 3)
    conn.execute("UPDATE bookings SET checked_in_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), here["id"]))
    shy = _stay(conn, "SHY", 4)
    conn.execute("INSERT OR IGNORE INTO email_optouts (email, created_at) VALUES (?, ?)",
                 (shy["guest_email"], datetime.now(timezone.utc).isoformat()))
    conn.commit()

    names = [o["name"] for o in _offers(conn, "SOON")]
    s.check("it offers what is on sale and the guest may add",
            f"{TAG} tasting" in names and f"{TAG} short notice" in names, detail=str(names))
    s.check("not what a guest may not add, nor what is withdrawn",
            f"{TAG} staff only" not in names and f"{TAG} withdrawn" not in names,
            detail=str(names))
    s.check("nor what needs more notice than is left",
            f"{TAG} far ahead" not in names, detail=str(names))
    taken_names = [o["name"] for o in _offers(conn, "TAKEN")]
    s.check("nor what is already on the stay",
            f"{TAG} tasting" not in taken_names and f"{TAG} short notice" in taken_names,
            detail=str(taken_names))

    sent_before = conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE "
                               "extras_offer_sent_at IS NOT NULL").fetchone()["n"]
    result, letters, why = _run()
    s.check("it runs", why is None, detail=str(why))
    first = _to(letters, "SOON")
    s.check("the stay in the window gets one letter", len(first) == 1,
            detail=f"{len(first)}; {result}")
    s.check("so does the one that has had something added", len(_to(letters, "TAKEN")) == 1)
    for ref, what in (("GONE", "a cancelled stay"), ("WAIT", "a stay not confirmed"),
                      ("NEAR", "a stay arriving tomorrow"), ("LATER", "a stay three weeks off"),
                      ("HERE", "a guest already checked in"),
                      ("SHY", "a guest who asked not to be written to")):
        s.check(f"nothing to {what}", not _to(letters, ref) and _stamp(conn, ref) == (None, None),
                detail=str(_stamp(conn, ref)))
    s.check("the result names who was not written to and why",
            "asked not to be written to" in (result or ""), detail=str(result))
    s.check("the letters went stamped as sent",
            _stamp(conn, "SOON")[1] == "sent" and _stamp(conn, "SOON")[0]
            and _stamp(conn, "TAKEN")[1] == "sent")

    letter = first[0] if first else {"subject": "", "body": "", "html": ""}
    s.check("it is the owner's letter, filled in",
            letter["subject"] == f"Before you come to {soon['room_name']}"
            and letter["body"].startswith("Hi Marie,"), detail=letter["subject"])
    taken_letter = (_to(letters, "TAKEN") or [{"body": "", "html": ""}])[0]
    s.check("the letter to the stay with the tasting does not offer it",
            f"{TAG} tasting" not in taken_letter["body"]
            and f"{TAG} tasting" not in taken_letter["html"])
    s.check("the plain letter lists each offer with its price and its link",
            f"{TAG} tasting — €40.00" in letter["body"]
            and f"?add={tasting['id']}#add-{tasting['id']}" in letter["body"],
            detail=letter["body"][:400])
    s.check("the notice an extra needs is said, as the booking form says it",
            "3 days' notice" in letter["body"])
    s.check("the drawn letter has a card for each, with its button",
            letter["html"].count("Add to your stay") == len(_offers(conn, "SOON")) >= 2
            and f"add={tasting['id']}#add-{tasting['id']}" in letter["html"],
            detail=f"{letter['html'].count('Add to your stay')} buttons, "
                   f"{len(_offers(conn, 'SOON'))} offers")
    s.check("and it is drawn in the house's own shell, not as bare text",
            letter["html"].lstrip().lower().startswith("<!doctype"))
    words = m._PAYING_WORDS.findall(letter["subject"] + " " + letter["body"] + " "
                                    + re.sub(r"<[^>]+>", " ", letter["html"]))
    s.check("nothing in it, drawn or plain, says anything about paying",
            not words, detail=str(words))
    s.check("nor carries the line about how the house takes payment",
            m.PAYMENT_SAFETY_LINE not in letter["body"])

    result2, letters2, why2 = _run()
    s.check("run again the next morning, nobody hears twice", not letters2,
            detail=f"{[x['to'] for x in letters2]}; {result2}")

    s.section("Not while Write to guests is off, and a held one is not sent again")
    late = _stay(conn, "LATE", 6)
    _set(conn, "guest_mail_live", "0")
    result, letters, why = _run()
    s.check("with Write to guests off, nothing goes", not letters, detail=str(letters[:1]))
    s.check("and nothing waits in the outbox to go later", why and "Write to guests" in why,
            detail=str(why))
    s.check("and the stay is left for when it can", _stamp(conn, "LATE") == (None, None))
    _set(conn, "guest_mail_live", "1")
    result, letters, why = _run(provider=False)
    s.check("a letter the provider will not take is stamped as held",
            _stamp(conn, "LATE")[1] == "held", detail=str(_stamp(conn, "LATE")))
    result, letters, why = _run()
    s.check("and is not written a second time", not _to(letters, "LATE"))

    s.section("A party hears once")
    lead = _stay(conn, "LEAD", 5)
    other = _stay(conn, "ROOM2", 5, email=f"{TAG.lower()}.lead2@example.invalid")
    ok, why_not = m.join_booking_party(conn, lead["id"], other["id"])
    conn.execute("UPDATE booking_parties SET name = ? WHERE id = "
                 "(SELECT party_id FROM bookings WHERE id = ?)", (f"{TAG} party", lead["id"]))
    conn.commit()
    result, letters, why = _run()
    party = [x for x in letters if "lead" in x["to"]]
    s.check("two rooms in one party get one letter", ok and len(party) == 1,
            detail=f"{why_not}; {[x['to'] for x in party]}")
    stamps = sorted(filter(None, (_stamp(conn, "LEAD")[1], _stamp(conn, "ROOM2")[1])))
    s.check("and the other room is marked as covered by it", stamps == ["party", "sent"],
            detail=str(stamps))

    s.section("The link in the letter adds the extra, through the booking page")
    url = next((u for u in re.findall(r"https?://\S+", letter["body"])
                if f"add={tasting['id']}" in u), "")
    parts = urlsplit(url)
    s.check("the link is to the guest's own booking page",
            parts.path == f"/book/manage/{soon['manage_token']}", detail=url)
    lines_before = conn.execute("SELECT COUNT(*) AS n FROM booking_extras WHERE "
                                "category = 'room' AND booking_id = ?",
                                (soon["id"],)).fetchone()["n"]
    guest = m.app.test_client()
    page = guest.get(f"{parts.path}?{parts.query}")
    html = page.get_data(as_text=True)
    s.check("it opens", page.status_code == 200, page)
    s.check("following it adds nothing by itself",
            conn.execute("SELECT COUNT(*) AS n FROM booking_extras WHERE category = 'room' "
                         "AND booking_id = ?", (soon["id"],)).fetchone()["n"] == lines_before)
    form = next((f for f in forms_on(html) if f["id"] == f"add-{tasting['id']}"), None)
    s.check("the page has the extra the link names, where the link points", form is not None)
    marked = re.search(r'<form[^>]*id="add-%d"[^>]*>' % tasting["id"], html)
    s.check("and marks it", bool(marked) and "g-mb-extra--offered" in marked.group(0),
            detail=marked.group(0) if marked else "")
    if form:
        r = guest.post(parts.path, data=fill(form, {}), follow_redirects=True)
        s.check("its Add button puts it on the stay",
            any("added" in f for f in flashes(r)), detail=str(flashes(r)))
    line = conn.execute("SELECT * FROM booking_extras WHERE category = 'room' "
                        "AND booking_id = ? AND extra_id = ?",
                        (soon["id"], tasting["id"])).fetchone()
    s.check("as a line on the booking, from the guest", bool(line)
            and "Added by the guest" in (line["notes"] or ""), detail=str(dict(line) if line else None))
    again = guest.get(f"{parts.path}?{parts.query}")
    s.check("followed again, the page says it is on the booking already",
            any("already on this booking" in f for f in flashes(again)),
            detail=str(flashes(again)))
    s.check("and a link to something not for sale says so",
            any("isn't something we can add" in f for f in flashes(
                guest.get(f"{parts.path}?add={longer['id'] + 100000}"))))
    far = guest.get(f"{parts.path}?add={longer['id']}")
    s.check("as does one whose notice has run out",
            any("too late for this stay" in f for f in flashes(far)), detail=str(flashes(far)))

    s.section("Dinner, when the stay could book it")
    _set(conn, "dining_mode", "open")
    conn.execute("UPDATE restaurant_settings SET enabled = 1, opening_date = NULL, "
                 "price_per_person = 65 WHERE id = 1")
    conn.commit()
    dinner = [o for o in _offers(conn, "TAKEN") if o["kind"] == "dinner"]
    s.check("La Table is offered while it takes bookings",
            len(dinner) == 1 and dinner[0]["url"].endswith("#dinner")
            and dinner[0]["price"] == "€65 per person", detail=str(dinner))
    _set(conn, "dining_mode", "hidden")
    s.check("and not while it does not",
            not [o for o in _offers(conn, "TAKEN") if o["kind"] == "dinner"])

    s.section("The stay's own page says whether and when")
    _set(conn, SWITCH, "0")
    html = oc.get(f"/admin/bookings/{soon['id']}").get_data(as_text=True)
    s.check("switched off, the stay's page does not mention it",
            "The letter offering what they can add" not in html)
    _set(conn, SWITCH, "1")
    html = oc.get(f"/admin/bookings/{soon['id']}").get_data(as_text=True)
    s.check("switched on, it is under the texts and letters the house sends",
            "Texts and letters the house sends by itself" in html
            and "The letter offering what they can add" in html)
    item = html[html.find('id="extras-offer"'):][:600]
    s.check("a letter that went says when", "sent " in item, detail=item[:300])
    upcoming = _stay(conn, "UPCOMING", 12)
    item = oc.get(f"/admin/bookings/{upcoming['id']}").get_data(as_text=True)
    item = item[item.find('id="extras-offer"'):][:900]
    goes = (house_today() + timedelta(days=5)).isoformat()
    with m.app.test_request_context("/"):
        on_day = m.format_date_short(goes)
    s.check("one still to come says the morning it goes, and what it offers",
            "goes the morning of" in item and on_day in item and f"{TAG} tasting" in item,
            detail=item[:400])
    item = oc.get(f"/admin/bookings/{cancelled['id']}").get_data(as_text=True)
    item = item[item.find('id="extras-offer"'):][:400]
    s.check("a cancelled one says it will not go", "not sent: the stay is cancelled" in item,
            detail=item[:200])
