"""The check on Send, and a right reply in French that it stopped.\n\nTHE FAULT IT IS HERE FOR (19 October 2026). A reply in French offered a guest\nthe Twin Room on the ground floor for the night of the 30th of October, at\n€270. Every fact in it was right. The add-in stopped it:\n\n    The Twin Room, 18 - 31 October 2026 (13 nights) is not free ...\n    This email quotes €270.00 ... The room's price for those nights is €3,510.00.\n\nThree faults, each enough on its own to stop a right reply, and nothing\nerrored:\n\n  1. THE QUOTED THREAD WAS READ AS THE SENDER'S WORDS. "18 October" came from\n     the quoted "Sent: Sunday, October 18, 2026 3:55 PM". The header is found\n     only in its plain form, and Outlook does not always write it plainly: in\n     bold ("*From:*"), with French typography's space before the colon\n     ("De\xa0:"), with its lines broken by Word's soft break, or run together\n     on one line. And a date before the day a reply is written is never the\n     stay it offers, so it is not taken for the arrival even when a header in\n     some form not yet seen gets through.\n  2. FRENCH MONTHS WERE INVISIBLE. "du 30 au 31 octobre" named no date; the\n     only ones found were the English subject's "31 October" and the quote's.\n  3. THE WRONG ROOM WAS CHOSEN. "The Twin Room" is inside "The Twin Room
     (Ground Floor)", and the first room whose name appeared anywhere won. And\n     the French reply never names the room in full: it says "Twin Room au
     rez-de-chaussée".\n\nTHE REPLY. The brief carries its facts and its quoted header, not its text, so\nREPLY below is that reply rebuilt from them: French, offering the ground-floor\nTwin Room for the 30th to the 31st at €270, over the guest's message quoted\nunder an English Outlook header. It also names the upstairs Twin Room as taken,\nwhich is the harder case for fault 3. It is checked as the brief has it (the\ndates read from it) and, for the full check, with its dates moved to nights\nthis database has free, because a fresh seed places its ateliers relative to\ntoday and one can sit over the 30th.\n\nNEGATIVE CONTROLS. A check that cannot stop anything passes every right\nreply. So each fix is paired with a reply that states something wrong -- a\ntaken room, a taken night, a wrong price -- in French and in English, and that\nreply must still be stopped, through the same route Outlook calls.\n\nThe rooms here are the suite's own, under the house's names; the rooms the\ndatabase has are stood down for the duration and put back after.\n"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, free_window
import _harness

m = _harness.m
TAG = "zzfr"
GUEST = "zzfr.guest@example.invalid"
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")

# The reply, as the person at the house wrote it. {d1} {d2} {mois} are the
# stay; the brief's own are 30, 31, octobre.
REPLY = (
    "Bonjour Madame,\n"
    "\n"
    "Merci beaucoup pour votre message.\n"
    "\n"
    "La Twin Room est malheureusement déjà réservée pour la nuit du {d1} {mois}, "
    "mais nous pouvons vous proposer notre Twin Room au rez-de-chaussée, libre "
    "du {d1} au {d2} {mois}, au tarif de {price}\xa0€ pour la nuit.\n"
    "\n"
    "Si cela vous convient, vous pouvez réserver directement sur notre site, "
    "ou me répondre et je m'en occupe.\n"
    "\n"
    "Bien cordialement,\n"
    "\n"
    "Château de Gudanes\n"
)
SUBJECT = "Re: Reservation {d1}-{d2} {Month}"
# The guest's own message, quoted under the header. Read as the house's words
# it asks for three nights, not one, so it would stop the reply on its own.
GUESTS_MESSAGE = (
    "Hello,\n"
    "We would like to book The Twin Room for the night of {d1} {Month}, or until "
    "{d4} {Month4} if you have space. Is it available?\n"
    "Thank you\n"
)


def _headers(sent, month_fr):
    """The quoted header in each form Outlook may hand the add-in, by name.\n    `sent` is the moment the guest's message was sent."""
    en = (f"{DAYS[sent.weekday()]}, {MONTHS[sent.month - 1]} {sent.day}, "
          f"{sent.year} 3:55 PM")
    fr = f"{JOURS[sent.weekday()]} {sent.day} {MOIS[sent.month - 1]} {sent.year} 15:55"
    who = "Guest <zzfr.guest@example.invalid>"
    house = "Château de Gudanes <reservations@chateaugudanes.com>"
    subj = "Reservation {d1}-{d2} {Month}"
    return {
        "plain, as Outlook on the web writes it":
            f"From: {who}\nSent: {en}\nTo: {house}\nSubject: {subj}\n\n",
        "in bold, as the HTML becomes text (*From:*)":
            f"*From:* {who}\n*Sent:* {en}\n*To:* {house}\n*Subject:* {subj}\n\n",
        "with a no-break space before each colon":
            f"From\xa0: {who}\nSent\xa0: {en}\nTo\xa0: {house}\nSubject\xa0: {subj}\n\n",
        "in French, as French Outlook writes it":
            f"De\xa0: {who}\nEnvoyé\xa0: {fr}\nÀ\xa0: {house}\nObjet\xa0: {subj}\n\n",
        "its lines broken by Word's soft break, as classic Outlook writes it":
            f"From: {who}\vSent: {en}\vTo: {house}\vSubject: {subj}\r\n\r\n",
        "run together on one line":
            f"From: {who} Sent: {en} To: {house} Subject: {subj}\n\n",
        "with the Sent line first":
            f"Sent: {en}\nTo: {house}\nSubject: {subj}\n\n",
    }


def _fill(text, **kw):
    return text.format(**kw)


def _post(client, data, key):
    r = client.post("/api/check-send-conflict", data=data,
                    headers={"Authorization": f"Bearer {key}"})
    status, body = r.status_code, r.get_json(silent=True)
    r.close()
    return status, body


def _stopped(got):
    return bool(got and (got["price_conflict"] or got["availability_conflict"]))


def run():
    s = Suite("the check on Send, and a right reply in French")
    conn = db()
    stood_down = [r["id"] for r in conn.execute("SELECT id FROM rooms WHERE active = 1")]
    last_log = conn.execute("SELECT COALESCE(MAX(id), 0) FROM submission_log").fetchone()[0]
    try:
        _the_three_readings(s)
        conn.execute("UPDATE rooms SET active = 0 WHERE id IN (%s)"
                     % ",".join("?" * len(stood_down)), stood_down)
        rooms = {}
        for name, price, order in (("The Twin Room", 270.0, 1),
                                   ("The King Room", 380.0, 2),
                                   ("The King Suite", 480.0, 3),
                                   ("The Twin Room (Ground Floor)", 270.0, 6)):
            cur = conn.execute(
                """INSERT INTO rooms (name, export_token, active, max_occupancy,\n                     price_per_night, sort_order, min_nights) VALUES (?, ?, 1, 2, ?, ?, 1)""",
                (name, TAG + _harness.secrets_token(), price, order))
            rooms[name] = conn.execute("SELECT * FROM rooms WHERE id = ?",
                                       (cur.lastrowid,)).fetchone()
        conn.commit()
        _which_room(s, conn, rooms)
        _the_whole_check(s, conn, rooms)
    finally:
        conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG.upper() + "%",))
        conn.execute("DELETE FROM rooms WHERE export_token LIKE ?", (TAG + "%",))
        if stood_down:
            conn.execute("UPDATE rooms SET active = 1 WHERE id IN (%s)"
                         % ",".join("?" * len(stood_down)), stood_down)
        conn.execute("DELETE FROM addin_send_checks WHERE platform LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM addin_sessions WHERE label LIKE ?", (TAG + "%",))
        conn.execute("DELETE FROM submission_log WHERE id > ? AND action LIKE 'check_send%'",
                     (last_log,))
        conn.commit()
        conn.close()
    return s


# ---- 1 and 2: what is read, and the dates in it ----------------------------

def _the_three_readings(s):
    s.section("The reply as the brief has it: the 30th to the 31st of October 2026")
    written = "2026-10-19T09:30:00+00:00"
    sent = datetime(2026, 10, 18, 15, 55)
    kw = dict(d1=30, d2=31, mois="octobre", Month="October", price=270, d4=2, Month4="November")
    for form, header in _headers(sent, "octobre").items():
        body = _fill(REPLY, **kw) + "\n" + _fill(header, **kw) + _fill(GUESTS_MESSAGE, **kw)
        mine = m.own_words(body)
        s.check(f"quoted header {form}: the reply is cut where the quote starts",
                "Bien cordialement" in mine and "Sent" not in mine and "Envoy" not in mine
                and "We would like" not in mine, detail=repr(mine[-160:]))
        dates = sorted(set(m.extract_dates(_fill(SUBJECT, **kw) + "\n" + mine, written)))
        s.check(f"  and the dates read from what was written are the 30th and the 31st",
                dates == [date(2026, 10, 30), date(2026, 10, 31)], detail=str(dates))
    whole = sorted(set(m.extract_dates(body, written)))
    s.check("(negative control: read whole, the quote's own dates are there too)",
            date(2026, 10, 18) in whole and date(2026, 11, 2) in whole, detail=str(whole))
    s.check("an ordinary 'From: 12 August' in a quote of the stay is still not a header",
            m.own_words("Your stay\nFrom: 12 August 2027\nTo: 15 August 2027\nPrice: €1,950")
            .endswith("€1,950"))
    s.check("nor is 'Sent: the invoice' with no header under it",
            m.own_words("Sent: the invoice, last week.\nThank you") ==
            "Sent: the invoice, last week.\nThank you")

    s.section("French dates, and ranges in either language")
    at = "2026-10-19T09:30:00+00:00"

    def days(text):
        return sorted(set(m.extract_dates(text, at)))
    o30, o31, n1, n2 = date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)
    for text, want in (
            ("du 30 au 31 octobre", [o30, o31]),
            ("le 30 octobre", [o30]),
            ("30\xa0octobre", [o30]),
            ("du 30 octobre au 1er novembre", [o30, n1]),
            ("le 30 et le 31 oct.", [o30, o31]),
            ("30-31 October", [o30, o31]),
            ("30 – 31 October", [o30, o31]),
            ("the 30th to the 31st of October", [o30, o31]),
            ("October 30-31", [o30, o31]),
            ("October 30 to November 2", [o30, n2]),
            ("30-2 November", [o30, n2]),
            ("Sunday, October 18, 2026 3:55 PM", [date(2026, 10, 18)]),
            ("OCTOBRE 30", []),
            ("2-3 people, in October", []),
    ):
        s.check(f"{text!r} -> {', '.join(d.isoformat() for d in want) or 'no date'}",
                days(text) == want, detail=str(days(text)))

    s.section("A header in a form not yet known is named in the log, and only its label")
    shape = m.unrecognised_quote_header("Merci.\n\nDate\xa0: dimanche 18 octobre, Inna Mona")
    s.check("the label and its marks", shape == "Date\xa0: ", detail=repr(shape))
    s.check("and nothing after them", "Inna" not in (shape or "") and "octobre" not in (shape or ""))
    s.check("a header it knows is not reported",
            m.unrecognised_quote_header("Merci.\n\nFrom: G <g@x>\nSent: Sunday") is None)
    s.check("nor a reply with no header at all", m.unrecognised_quote_header("Merci, à bientôt.") is None)


# ---- 3: which room ---------------------------------------------------------

def _which_room(s, conn, rooms):
    s.section("The room named is the most specific one, whatever the table's order")
    active = conn.execute("SELECT * FROM rooms WHERE active = 1 ORDER BY sort_order, id").fetchall()

    def named(text, order=active):
        return [r["name"] for r in m.rooms_named_in(order, text)]
    for order_label, order in (("upstairs first", active), ("ground floor first", list(reversed(active)))):
        for text, want in (
                ("The Twin Room (Ground Floor) is free", "The Twin Room (Ground Floor)"),
                ("the Twin Room on the ground floor is free", "The Twin Room (Ground Floor)"),
                ("our ground floor Twin Room is free", "The Twin Room (Ground Floor)"),
                ("la Twin Room au rez-de-chaussée est libre", "The Twin Room (Ground Floor)"),
                ("la Twin Room du rez de chaussée", "The Twin Room (Ground Floor)"),
                ("The Twin Room is free", "The Twin Room"),
                ("la Twin Room est libre", "The Twin Room"),
                ("The King Suite is free", "The King Suite"),
                ("The King Room is free", "The King Room")):
            got = named(text, order)
            s.check(f"{order_label}: {text!r} is {want}, and only that", got == [want],
                    detail=str(got))
    both = named("La Twin Room est prise, mais la Twin Room au rez-de-chaussée est libre.")
    s.check("a reply naming both: both are named, the ground floor first",
            both == ["The Twin Room (Ground Floor)", "The Twin Room"], detail=str(both))
    s.check("the bare name stands for the bracketed room when no other room has it",
            [r["name"] for r in m.rooms_named_in(
                [rooms["The Twin Room (Ground Floor)"]], "the Twin Room is free")]
            == ["The Twin Room (Ground Floor)"])
    s.check("(negative control: but not while The Twin Room is one of the rooms)",
            named("the Twin Room is free") == ["The Twin Room"])
    s.check("a word that only contains a name is not the name",
            named("the twin roomful of guests") == [])


# ---- the whole check, and the route Outlook calls ---------------------------

def _window(conn, rooms):
    """A night both Twin Rooms have free, inside its month, with another free\n    night for the ground-floor room a few days later."""
    ground, twin = rooms["The Twin Room (Ground Floor)"], rooms["The Twin Room"]
    after = 30
    while True:
        d = free_window(ground["id"], 1, after_days=after, clear_of_rate_overrides=True)
        if d.day <= 26 and m.is_range_available(conn, twin["id"], d, d + timedelta(days=1))[0]:
            later = free_window(ground["id"], 1, after_days=(d - m.house_today()).days + 3,
                                clear_of_rate_overrides=True)
            # Nobody else in the house that night, so the King Suite is free
            # beside the two Twin Rooms this suite books.
            if later.day <= 26 and not m.rooms_let_on(m._availability_picture(conn), later):
                return d, later
        after = (d - m.house_today()).days + 1


def _book(conn, room, night, ref):
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,\n             arrival_date, departure_date, party_size, status, created_at)\n           VALUES (?, ?, ?, 'zzfr Other Guest', 'zzfr.other@example.invalid', ?, ?, 2,\n                   'confirmed', ?)""",
        (room["id"], ref, TAG + ref, night.isoformat(), (night + timedelta(days=1)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _the_whole_check(s, conn, rooms):
    ground, twin = rooms["The Twin Room (Ground Floor)"], rooms["The Twin Room"]
    night, taken_night = _window(conn, rooms)
    _book(conn, twin, night, TAG.upper() + "T1")          # the upstairs Twin, as on the 30th
    _book(conn, ground, taken_night, TAG.upper() + "G1")  # for the wrong-date control
    _book(conn, twin, taken_night, TAG.upper() + "T2")    # and both taken, for "neither fits"
    now = datetime.now(timezone.utc)
    sent = now - timedelta(days=1)
    nxt = night + timedelta(days=1)
    far = night + timedelta(days=3)
    kw = dict(d1=night.day, d2=nxt.day, mois=MOIS[night.month - 1], Month=MONTHS[night.month - 1],
              price=270, d4=far.day, Month4=MONTHS[far.month - 1])
    stay = f"{night.isoformat()} to {nxt.isoformat()}"

    s.section(f"The situation the brief describes, on {stay}")
    s.check("the upstairs Twin Room is taken that night",
            not m.is_range_available(conn, twin["id"], night, nxt)[0])
    s.check("the ground-floor one is free",
            m.is_range_available(conn, ground["id"], night, nxt)[0])
    s.check("and costs €270 for it", m.compute_room_total(conn, ground, night, nxt) == 270,
            detail=str(m.compute_room_total(conn, ground, night, nxt)))

    s.section("The reply passes, under every form of the quoted header")
    for form, header in _headers(sent, kw["mois"]).items():
        body = _fill(REPLY, **kw) + "\n" + _fill(header, **kw) + _fill(GUESTS_MESSAGE, **kw)
        got = m.guess_email_conflict(conn, [GUEST], _fill(SUBJECT, **kw), m.own_words(body),
                                     now.isoformat())
        s.check(f"{form}: not stopped", not _stopped(got), detail=str(got))
        s.check("  and it was checked, against the ground-floor room",
                got and got["category"] == "Room: The Twin Room (Ground Floor)", detail=str(got))
        whole = m.guess_email_conflict(conn, [GUEST], _fill(SUBJECT, **kw), body, now.isoformat())
        s.check("  (negative control: read whole, the guest's three nights stop it)",
                _stopped(whole), detail=str(whole))

    s.section("A date before the day a reply is written is not its arrival")
    text = (f"The Twin Room (Ground Floor), {night.day} to {nxt.day} {MONTHS[nxt.month - 1]}, "
            f"is €270. You wrote on {sent.day} {MONTHS[sent.month - 1]}.")
    if nxt.month != night.month:
        text = text.replace(f"{night.day} to", f"{night.day} {MONTHS[night.month - 1]} to")
    got = m.guess_email_conflict(conn, [GUEST], "Re: your stay", text, now.isoformat())
    s.check("a quoted Sent date that got through is not taken for the arrival",
            not _stopped(got), detail=str(got))
    earlier = (sent - timedelta(days=2)).isoformat()
    got = m.guess_email_conflict(conn, [GUEST], "Re: your stay", text, earlier)
    s.check("(negative control: written before it, the same date is the arrival, and stops it)",
            _stopped(got), detail=str(got))

    s.section("A reply that is wrong is still stopped: in French")
    wrong = {
        "the upstairs Twin Room, which is taken": (
            "Bonjour,\nLa Twin Room est libre du {d1} au {d2} {mois}, au tarif de 270 €.\n"
            "Bien cordialement", "availability_conflict", "The Twin Room,"),
        "the right room at the wrong price": (
            REPLY.replace("{price}", "200"), "price_conflict", "€270"),
        "the ground-floor room on a night it is taken": (
            "Bonjour,\nLa Twin Room au rez-de-chaussée est libre du {t1} au {t2} {tmois}, "
            "au tarif de 270 €.\nBien cordialement", "availability_conflict",
            "The Twin Room (Ground Floor),"),
    }
    t2 = taken_night + timedelta(days=1)
    tkw = dict(kw, t1=taken_night.day, t2=t2.day, tmois=MOIS[taken_night.month - 1],
               tMonth=MONTHS[taken_night.month - 1])
    for label, (body, flag, says) in wrong.items():
        got = m.guess_email_conflict(conn, [GUEST], "Re: votre séjour",
                                     m.own_words(_fill(body, **tkw)), now.isoformat())
        s.check(f"{label}: stopped", got and got[flag], detail=str(got))
        s.check("  and the note says which", says in ((got or {}).get("note") or ""),
                detail=(got or {}).get("note"))

    s.section("And in English")
    english = {
        "The Twin Room, which is taken": (
            "Hello,\nThe Twin Room is free from {d1} to {d2} {Month}, at €270.\nBest wishes",
            "availability_conflict", "The Twin Room,"),
        "the right room at the wrong price": (
            "Hello,\nThe Twin Room (Ground Floor) is free from {d1} to {d2} {Month}, "
            "at €200.\nBest wishes", "price_conflict", "€270"),
        "the ground-floor room on a night it is taken": (
            "Hello,\nThe Twin Room (Ground Floor) is free {t1}-{t2} {tMonth}, at €270.\n"
            "Best wishes", "availability_conflict", "The Twin Room (Ground Floor),"),
        "the King Suite named, but the ground floor offered on a night it is taken": (
            "Hello,\nThe King Suite is taken, but The Twin Room (Ground Floor) is free "
            "{t1}-{t2} {tMonth}, at €270.\nBest wishes", "availability_conflict",
            "The Twin Room (Ground Floor),"),
        "both rooms named, and neither fits": (
            "Hello,\nThe Twin Room is taken, but The Twin Room (Ground Floor) is free "
            "{t1}-{t2} {tMonth}, at €270.\nBest wishes", "availability_conflict",
            "The Twin Room (Ground Floor),"),
    }
    for label, (body, flag, says) in english.items():
        got = m.guess_email_conflict(conn, [GUEST], "Re: your stay",
                                     m.own_words(_fill(body, **tkw)), now.isoformat())
        s.check(f"{label}: stopped", got and got[flag], detail=str(got))
        s.check("  and the note says which", says in ((got or {}).get("note") or ""),
                detail=(got or {}).get("note"))
    right = ("Hello,\nThe Twin Room is taken, but The Twin Room (Ground Floor) is free "
             "{d1}-{d2} {Month}, at €270.\nBest wishes")
    got = m.guess_email_conflict(conn, [GUEST], "Re: your stay", _fill(right, **tkw),
                                 now.isoformat())
    s.check("(and the same, on a night the ground floor is free, goes)", not _stopped(got),
            detail=str(got))

    s.section("A reply naming two rooms offers the one that fits, even when it is not the longer name")
    s.check("the King Suite is free on the night both Twin Rooms are taken",
            m.is_range_available(conn, rooms["The King Suite"]["id"], taken_night, t2)[0])
    for label, body in (
            ("in French", "Bonjour,\nLa Twin Room au rez-de-chaussée est prise du {t1} au {t2} "
                          "{tmois}, mais la King Suite est libre, à 480 €.\nBien cordialement"),
            ("in English", "Hello,\nThe Twin Room (Ground Floor) is taken {t1}-{t2} {tMonth}, "
                           "but The King Suite is free, at €480.\nBest wishes")):
        got = m.guess_email_conflict(conn, [GUEST], "Re: stay", _fill(body, **tkw), now.isoformat())
        s.check(f"{label}: the King Suite at €480 goes", not _stopped(got)
                and got and got["category"] == "Room: The King Suite", detail=str(got))
        wrong_price = _fill(body, **tkw).replace("480", "380")
        got = m.guess_email_conflict(conn, [GUEST], "Re: stay", wrong_price, now.isoformat())
        s.check(f"  (negative control: at €380, which is neither, it is stopped)", _stopped(got),
                detail=str(got))

    s.section("Through the route Outlook calls")
    _oc, _ec, owner, _emp = clients()
    anon = m.app.test_client()
    key, _ = m.addin_issue_session(conn, owner["id"], "test", label=TAG + " owner")
    conn.commit()
    header = _headers(sent, kw["mois"])["its lines broken by Word's soft break, as classic Outlook writes it"]
    reply = (_fill(REPLY, **kw) + "\n" + _fill(header, **kw) + _fill(GUESTS_MESSAGE, **kw)).replace("\n", "\r\n")
    status, got = _post(anon, {"recipients": GUEST, "subject": _fill(SUBJECT, **kw), "body": reply,
                               "source": "send", "platform": TAG + "-right"}, key)
    s.check("the reply goes", status == 200 and got and got.get("conflict") is False,
            detail=f"{status} {got}")
    s.check("  having been checked against the ground-floor room",
            got and "The Twin Room (Ground Floor)" in (got.get("checked") or ""), detail=str(got))
    for label, body in (("in French, the taken Twin Room", wrong["the upstairs Twin Room, which is taken"][0]),
                        ("in English, the wrong price", english["the right room at the wrong price"][0])):
        text = _fill(body, **tkw) + "\r\n\r\n" + _fill(header, **kw) + _fill(GUESTS_MESSAGE, **kw)
        status, got = _post(anon, {"recipients": GUEST, "subject": "Re: stay", "body": text,
                                   "source": "send", "platform": TAG + "-wrong"}, key)
        s.check(f"{label}: stopped", status == 200 and got and got.get("conflict") is True,
                detail=f"{status} {got}")
