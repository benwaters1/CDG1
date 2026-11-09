"""Bringing the RetreatGuru registrations across a second time.

Ateliers went on being sold and paid through RetreatGuru after the first run
(payment links sent from it as late as 6-7 November 2026), so the owner runs
Bring Registrations Across again to pick everything up. Two things made that
unsafe, and this holds the import to the brief that closed them
(BRIEF-rg-import-second-run.md):

  1. A registration already brought across was kept exactly as it was. Money
     paid in RetreatGuru since -- Heidi Welch's EUR 500 on 31 October and EUR
     404 on 7 November -- was never added, and nothing said the export and
     the ledger here now disagreed. Now each one is held up against the
     export: more paid, and nothing else changed, is matched to Stripe
     payments nobody here holds and offered as lines of its own, added only
     when ticked; anything else is shown and left to settle by hand.

  2. A guest already booked on the site for the same retreat was booked a
     second time, the place and the money counted twice. Now the row is
     stopped, and its RetreatGuru payments can be attached to that booking
     instead.

And through both: a Stripe payment the app already holds -- on a room, on a
registration, by its id or by its own label, or, where the export cannot show
it by id at all, by its amount, address and day -- is never matched again.
The export the house took in October had no PaymentIntent column, so on live
that last one is not a corner case.

Everything runs through the owner's own page, on made-up exports shaped like
the real ones. Nothing here reaches Stripe or sends a letter.
"""
import sqlite3
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRG2"
LOW = TAG.lower()
REG_HEADER = ("first_name,last_name,submitted,cancelled,booking_window,reg_form_complete,phone,"
              "payment,optional_items,roommate,discount_code,travel_phone_,items_discounts_total,"
              "payments_refunds_total,balance_due,scheduled_payments_total,"
              "balance_after_scheduled,registration_amount")
STRIPE_HEADER = ("id,PaymentIntent ID,Created date (UTC),Amount,Amount Refunded,Currency,Status,"
                 "Customer Email,Description,kind (metadata),reference (metadata)")
PEOPLE = ("Heidi", "Ivan", "Jude", "Kai", "Lea", "Mia", "Ned", "Ola", "Pia")


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject))
        return True


def _email(first):
    return f"{LOW}.{first.lower()}@example.invalid"


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    like = f"{LOW}%"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM booking_payments WHERE booking_id IN (SELECT id FROM bookings "
                 "WHERE guest_email LIKE ?)", (like,))
    conn.execute("DELETE FROM bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM import_drafts")
    conn.commit()
    conn.close()


def _session():
    """One EUR 2,000 atelier, starting on a day nothing else in the house does:
    the import finds a registration's session by the day it starts."""
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 2000, 12, 1, 93, ?, 20)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?",
                       (f"{TAG} Atelier",)).fetchone()["id"]
    taken = {r["start_date"] for r in conn.execute("SELECT start_date FROM workshop_sessions")}
    start = house_today() + timedelta(days=55)
    while start.isoformat() in taken:
        start += timedelta(days=1)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
             created_at) VALUES (?, ?, ?, 12, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} one", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} one",)).fetchone()["id"]
    conn.commit()
    conn.close()
    return sid, start


def _reg(first, start, *, agreed, paid, minute):
    """One row of the registrations export, booked 60 days before `start`."""
    submitted = f"{(start - timedelta(days=60)).isoformat()} 10:{minute:02d}:00"
    owing = round(agreed - paid, 2)
    return ",".join([first, f"{TAG}{first}", submitted, "", "60", "x", "", "", "", "", "", "",
                     f"{agreed:.2f}", f"{paid:.2f}", f"{owing:.2f}", "0", f"{owing:.2f}",
                     f"{agreed:.2f}"])


def _stripe(rows):
    """A Stripe dashboard export: (id, intent, created, amount, who, description,
    kind, reference)."""
    return STRIPE_HEADER + "\n" + "\n".join(
        f'{c},{pi},{when},"{amount}","0,00",eur,Paid,{_email(who)},{words},{kind},{ref}'
        for c, pi, when, amount, who, words, kind, ref in rows) + "\n"


def _people():
    return "first_name,last_name,person_id,email,num_programs,overall_balance\n" + "\n".join(
        f"{f},{TAG}{f},{i},{_email(f)},1,0" for i, f in enumerate(PEOPLE)) + "\n"


def _upload(text, name):
    import io
    return (io.BytesIO(text.encode("utf-8")), name)


def _check(oc, regs, stripe):
    return oc.post("/management/import-registrations", data={
        "action": "check", "due_days": "30",
        "registrations": _upload(regs, "registrations.csv"),
        "people": _upload(_people(), "personslist.csv"),
        "stripe": _upload(stripe, "payments.csv")}, content_type="multipart/form-data")


def _run(oc, page, **ticks):
    data = {"action": "run", "token": _token(page), "checked_flags": "on",
            "bring": _ticked(page, "bring")}
    data.update(ticks)
    return oc.post("/management/import-registrations", data=data, follow_redirects=True)


def _token(page):
    import re
    found = re.search(r'name="token" value="([^"]+)"', page)
    return found.group(1) if found else ""


def _ticked(page, name):
    import re
    return re.findall(rf'name="{name}" value="([^"]+)"[^>]*checked', page)


def _offered(page, name):
    import re
    return re.findall(rf'name="{name}" value="([^"]+)"', page)


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in ("workshop_bookings", "workshop_transactions", "email_outbox",
                      "notifications")}


def _booking(conn, first):
    return conn.execute("SELECT * FROM workshop_bookings WHERE guest_email = ? ORDER BY id",
                        (_email(first),)).fetchall()


def _lines(conn, bid):
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, amount, method, stripe_ref, description, created_at "
        "FROM workshop_transactions WHERE workshop_booking_id = ? ORDER BY id", (bid,))]


def _site_booking(conn, sid, first, *, paid, paid_at, session_ref):
    """A registration made on the site: confirmed, deposit paid by card, the
    payment kept by its Checkout Session as mark_workshop_payment_paid keeps it."""
    now = _harness.datetime_now()
    code = m.make_workshop_reference_code()
    cur = conn.execute(
        """INSERT INTO workshop_bookings (session_id, reference_code, manage_token, guest_name,
             guest_email, party_size, status, total_price, created_at, decided_at,
             deposit_amount, deposit_paid_at, balance_amount)
           VALUES (?, ?, ?, ?, ?, 1, 'confirmed', 2000, ?, ?, ?, ?, ?)""",
        (sid, code, f"{TAG}-{first}-token", f"{first} {TAG}{first}", _email(first), now, now,
         paid, paid_at, 2000 - paid))
    conn.execute(
        """INSERT INTO workshop_transactions (workshop_booking_id, kind, description, amount,
             method, created_at, stripe_ref) VALUES (?, 'payment', 'Deposit — Stripe', ?,
             'stripe', ?, ?)""", (cur.lastrowid, paid, paid_at, session_ref))
    return cur.lastrowid, code


def run():
    s = Suite("bringing RetreatGuru across a second time")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    mail = Recorder()
    real_send = m.send_email
    m.send_email = mail
    try:
        _suite(s, oc, mail)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _suite(s, oc, mail):
    sid, start = _session()
    long_ago = (start - timedelta(days=60)).isoformat()

    # THE FIRST RUN, as on 15 October: five registrations come across.
    regs1 = "\n".join([REG_HEADER,
                       _reg("Heidi", start, agreed=2000, paid=0, minute=1),
                       _reg("Ivan", start, agreed=2000, paid=600, minute=2),
                       _reg("Jude", start, agreed=2000, paid=500, minute=3),
                       _reg("Kai", start, agreed=2000, paid=0, minute=4),
                       _reg("Lea", start, agreed=2000, paid=500, minute=5)]) + "\n"
    first = [
        ("ch_ivan_1", "", f"{long_ago} 09:00:00", "600,00", "Ivan", "RetreatGuru", "", ""),
        ("ch_jude_1", "pi_jude_1", f"{long_ago} 09:00:00", "500,00", "Jude", "RetreatGuru",
         "", ""),
        ("ch_lea_1", "pi_lea_1", f"{long_ago} 09:00:00", "500,00", "Lea", "RetreatGuru", "", ""),
    ]
    page = _check(oc, regs1, _stripe(first)).get_data(as_text=True)
    r = _run(oc, page)
    conn = db()
    here = {f: _booking(conn, f) for f in PEOPLE}
    conn.close()
    s.section("The first run, as it was")
    s.check("five registrations came across, one each",
            [len(here[f]) for f in ("Heidi", "Ivan", "Jude", "Kai", "Lea")] == [1] * 5,
            detail=" ".join(flashes(r)))

    # THE SITE, since: Mia and Ned booked the same retreat here and paid a
    # deposit by card; Ola paid for a room. Each payment kept as the app keeps it.
    conn = db()
    site_moment = _harness.datetime_now()
    ned_moment = f"{(house_today() - timedelta(days=20)).isoformat()}T09:00:00+00:00"
    mia_id, mia_code = _site_booking(conn, sid, "Mia", paid=600, paid_at=site_moment,
                                     session_ref="cs_test_zzrg2_mia")
    ned_id, ned_code = _site_booking(conn, sid, "Ned", paid=400, paid_at=ned_moment,
                                     session_ref="cs_test_zzrg2_ned")
    room = conn.execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, status, total_price, created_at, payment_status,
             stripe_payment_intent_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'cancelled', 300, ?, 'paid', 'pi_ola_room')""",
        (room, f"GUD-{TAG}", f"{TAG}-room-token", f"Ola {TAG}Ola", _email("Ola"),
         house_today().isoformat(), (house_today() + timedelta(days=1)).isoformat(),
         site_moment))
    # Pia's room, paid on the site before payments were labelled, and kept by
    # its PaymentIntent -- which the export the house took in October did not
    # have a column for, so the export shows her charge alone.
    pia_moment = f"{(house_today() - timedelta(days=12)).isoformat()}T10:00:00+00:00"
    pia = conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
             arrival_date, departure_date, status, total_price, created_at, payment_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'cancelled', 250, ?, 'paid')""",
        (room, f"GUD-{TAG}P", f"{TAG}-pia-token", f"Pia {TAG}Pia", _email("Pia"),
         house_today().isoformat(), (house_today() + timedelta(days=1)).isoformat(),
         pia_moment)).lastrowid
    conn.execute("INSERT INTO booking_payments (booking_id, amount, method, "
                 "stripe_payment_intent_id, created_at) VALUES (?, 250, 'stripe', "
                 "'pi_pia_room', ?)", (pia, pia_moment))
    conn.commit()
    conn.close()

    # THE SECOND EXPORT, November: more paid by Heidi and Ivan; less by Jude; a
    # new price for Kai; more by Lea that Stripe cannot account for; and three
    # new rows for people the site already knows.
    nov = (house_today() - timedelta(days=9)).isoformat()
    day_after = (start - timedelta(days=59)).isoformat()
    regs2 = "\n".join([REG_HEADER,
                       _reg("Heidi", start, agreed=2000, paid=904, minute=1),
                       _reg("Ivan", start, agreed=2000, paid=1200, minute=2),
                       _reg("Jude", start, agreed=2000, paid=400, minute=3),
                       _reg("Kai", start, agreed=1800, paid=0, minute=4),
                       _reg("Lea", start, agreed=2000, paid=800, minute=5),
                       _reg("Mia", start, agreed=2000, paid=600, minute=6),
                       _reg("Ned", start, agreed=2000, paid=400, minute=7),
                       _reg("Ola", start, agreed=2000, paid=300, minute=8),
                       _reg("Pia", start, agreed=2000, paid=250, minute=9)]) + "\n"
    stripe2 = _stripe(first + [
        ("ch_heidi_1", "pi_heidi_1", f"{nov} 09:00:00", "500,00", "Heidi", "RetreatGuru", "", ""),
        ("ch_heidi_2", "pi_heidi_2", f"{nov} 15:00:00", "404,00", "Heidi", "RetreatGuru", "", ""),
        # Ivan's second instalment: the same amount as his first, a day later.
        ("ch_ivan_2", "pi_ivan_2", f"{day_after} 09:00:00", "600,00", "Ivan", "RetreatGuru",
         "", ""),
        ("ch_lea_2", "pi_lea_2", f"{nov} 09:00:00", "250,00", "Lea", "RetreatGuru", "", ""),
        # Mia's deposit on the site, labelled as the app labels every payment
        # -- earlier than her RetreatGuru one and the same amount, so a matcher
        # that did not read the label would take it first. Dated well away from
        # the line it is kept on here, so that only the label can set it aside.
        ("ch_mia_site", "pi_mia_site", f"{(house_today() - timedelta(days=30)).isoformat()} "
         "08:00:00", "600,00", "Mia",
         f"{TAG} Atelier · Ref {mia_code} · deposit", "workshop_deposit", mia_code),
        ("ch_mia_rg", "pi_mia_rg", f"{nov} 09:00:00", "600,00", "Mia", "RetreatGuru", "", ""),
        # Ned's deposit on the site, from before payments were labelled: known
        # here only by its Checkout Session, and the only EUR 400 Ned paid.
        ("ch_ned_site", "pi_ned_site", f"{ned_moment[:10]} 09:20:00", "400,00", "Ned", "", "",
         ""),
        # Ola's room, already on a stay here by its PaymentIntent.
        ("ch_ola_room", "pi_ola_room", f"{nov} 09:00:00", "300,00", "Ola", "Reservation", "", ""),
        # Pia's room: the charge alone, no PaymentIntent, no label.
        ("ch_pia_room", "", f"{pia_moment[:10]} 10:05:00", "250,00", "Pia", "", "", ""),
    ])

    s.section("The check: every registration already here, held up against the export")
    conn = db()
    before = _counts(conn)
    conn.close()
    r = _check(oc, regs2, stripe2)
    page = r.get_data(as_text=True)
    text = " ".join(page.split())
    conn = db()
    s.check("it answers, and writes nothing", r.status_code == 200 and _counts(conn) == before,
            detail=f"{r.status_code} {before} -> {_counts(conn)}")
    conn.close()
    topups = _offered(page, "topup")
    s.check("Heidi's two November payments are offered, each on its own",
            sorted(t.rsplit("|", 1)[1] for t in topups if ":heidi|" in t)
            == ["pi_heidi_1", "pi_heidi_2"], detail=str(topups))
    s.check("and Ivan's second EUR 600, though it is the twin of one already here",
            [t.rsplit("|", 1)[1] for t in topups if ":ivan|" in t] == ["pi_ivan_2"],
            detail=str(topups))
    s.check("shown line by line: paid in RetreatGuru now, and here",
            "Paid: €904.00 in RetreatGuru now, €0.00 here." in text
            and "Owing: €1,096.00 in RetreatGuru now, €2,000.00 here." in text, detail=text[:0])
    s.check("none of it ticked for the owner: offered, never assumed",
            not _ticked(page, "topup"), detail=str(_ticked(page, "topup")))
    for who, says in (("Jude", "Paid: €400.00 in RetreatGuru now, €500.00 here."),
                      ("Kai", "Agreed: €1,800.00 in RetreatGuru now, €2,000.00 here."),
                      ("Lea", "Stripe has €250.00 from")):
        part = text[text.find(f"{who} {TAG}{who}"):]
        s.check(f"{who}: the difference is shown, and stops the row until settled by hand",
                says in part[:2500] and "settle it by hand" in part[:2500].lower()
                and not [k for k in _ticked(page, "bring") if f":{who.lower()}|" in k],
                detail=part[:900])
    s.check("Mia, booked here already, is stopped rather than booked twice",
            f"Already booked here on this retreat: {mia_code}" in text
            and not [k for k in _ticked(page, "bring") if ":mia|" in k], detail=text[:0])
    attach = _offered(page, "attach")
    s.check("and offered: attach her RetreatGuru payment to that booking, unticked",
            attach == [k for k in attach if ":mia|" in k] and len(attach) == 1
            and attach[0].endswith(f"|{mia_id}") and not _ticked(page, "attach"),
            detail=str(attach))
    mia_part = text[text.find(f"Mia {TAG}Mia"):]
    s.check("with her RetreatGuru payment, never the site's own labelled one",
            "pi_mia_rg" in mia_part[:2500] and "pi_mia_site" not in mia_part[:2500],
            detail=mia_part[:900])
    s.check("Ned is stopped too, with nothing to attach: his only EUR 400 is the site's",
            f"Already booked here on this retreat: {ned_code}" in text
            and not [a for a in attach if ":ned|" in a]
            and "pi_ned_site" in text and "taken through this site" in text, detail=text[:0])
    ola = text[text.find(f"Ola {TAG}Ola"):]
    s.check("Ola's EUR 300 is already on a stay here, so it is never matched again",
            "Stripe has €0.00 from" in ola[:2500]
            and not [k for k in _ticked(page, "bring") if ":ola|" in k], detail=ola[:900])
    pia_part = text[text.find(f"Pia {TAG}Pia"):]
    s.check("Pia's EUR 250 is her room on the site, though the export shows it by charge alone",
            "Stripe has €0.00 from" in pia_part[:2500]
            and "ch_pia_room, the same amount from the same address as a payment taken "
                "through this site" in text
            and not [k for k in _ticked(page, "bring") if ":pia|" in k], detail=pia_part[:900])
    s.check("and the payments set aside say why",
            "pi_ola_room, already a payment line here" in text
            and "pi_mia_site, taken through this site" in text, detail=text[:0])

    s.section("Ticking one of Heidi's two adds one, and says the other was not")
    conn = db()
    heidi, ivan = _booking(conn, "Heidi")[0], _booking(conn, "Ivan")[0]
    kept_heidi, kept_ivan = _lines(conn, heidi["id"]), _lines(conn, ivan["id"])
    before = _counts(conn)
    conn.close()
    heidi_1 = [t for t in topups if t.endswith("|pi_heidi_1")]
    ivan_2 = [t for t in topups if t.endswith("|pi_ivan_2")]
    # A tick for a payment that was never offered stops the whole run.
    forged = [t.rsplit("|", 1)[0] + "|pi_ola_room" for t in ivan_2]
    r = _run(oc, page, topup=heidi_1 + forged)
    conn = db()
    s.check("(a payment not offered, ticked anyway, stops the run with nothing kept)",
            _counts(conn) == before and any("was not offered" in f for f in flashes(r)),
            detail=f"{before} -> {_counts(conn)} / {flashes(r)}")
    conn.close()
    page = _check(oc, regs2, stripe2).get_data(as_text=True)
    topups = _offered(page, "topup")
    r = _run(oc, page, topup=[t for t in topups if t.endswith(("|pi_heidi_1", "|pi_ivan_2"))])
    said = " ".join(flashes(r))
    conn = db()
    after = _counts(conn)
    heidi = _booking(conn, "Heidi")[0]
    ivan = _booking(conn, "Ivan")[0]
    s.check("two lines added, nothing else written",
            after["workshop_transactions"] == before["workshop_transactions"] + 2
            and after["workshop_bookings"] == before["workshop_bookings"],
            detail=f"{before} -> {after} / {said}")
    s.check("the summary names what was added, and the payment not added",
            "Heidi ZZRG2Heidi €500.00" in said and "Ivan ZZRG2Ivan €600.00" in said
            and "(pi_heidi_2) was not added: not ticked" in said, detail=said)
    s.check("Ivan: what RetreatGuru says, to the cent",
            m.workshop_balance_due(conn, ivan["id"]) == (800.0, 2000.0, 1200.0),
            detail=str(m.workshop_balance_due(conn, ivan["id"])))
    s.check("his first-run line untouched, the new one beside it with its own Stripe id",
            _lines(conn, ivan["id"])[:len(kept_ivan)] == kept_ivan
            and [(l["amount"], l["method"], l["stripe_ref"])
                 for l in _lines(conn, ivan["id"])[len(kept_ivan):]]
            == [(600.0, "stripe", "pi_ivan_2")], detail=str(_lines(conn, ivan["id"])))
    s.check("Heidi: EUR 500 of the 904 now", m.workshop_balance_due(conn, heidi["id"])
            == (1500.0, 2000.0, 500.0), detail=str(m.workshop_balance_due(conn, heidi["id"])))
    s.check("and, paid nothing when she came across, that money is now her deposit -- so her "
            "page does not ask for the whole price again",
            heidi["deposit_amount"] == 500 and heidi["deposit_paid_at"]
            and heidi["deposit_paid_at"].startswith(nov) and heidi["balance_amount"] == 1500,
            detail=str(dict(heidi)))
    s.check("her first-run lines untouched",
            _lines(conn, heidi["id"])[:len(kept_heidi)] == kept_heidi)
    conn.close()

    s.section("The next check offers only what is still missing")
    page = _check(oc, regs2, stripe2).get_data(as_text=True)
    topups = _offered(page, "topup")
    s.check("Heidi's EUR 404 alone; Ivan is the same as RetreatGuru now",
            [t.rsplit("|", 1)[1] for t in topups] == ["pi_heidi_2"], detail=str(topups))
    conn = db()
    before = _counts(conn)
    conn.close()
    r = _run(oc, page, topup=topups, attach=_offered(page, "attach"))
    said = " ".join(flashes(r))
    conn = db()
    after = _counts(conn)
    heidi = _booking(conn, "Heidi")[0]
    s.check("Heidi now says what RetreatGuru says: EUR 904 paid, EUR 1,096 owing",
            m.workshop_balance_due(conn, heidi["id"]) == (1096.0, 2000.0, 904.0),
            detail=str(m.workshop_balance_due(conn, heidi["id"])))
    s.check("her deposit stays as first stamped", heidi["deposit_amount"] == 500)
    mias = _booking(conn, "Mia")
    s.check("Mia: still one booking, never a second",
            len(mias) == 1 and mias[0]["id"] == mia_id, detail=str([dict(b) for b in mias]))
    s.check("and her RetreatGuru EUR 600 is on it, by its own Stripe id, beside her deposit",
            [(l["amount"], l["stripe_ref"]) for l in _lines(conn, mia_id)]
            == [(600.0, "cs_test_zzrg2_mia"), (600.0, "pi_mia_rg")]
            and m.workshop_balance_due(conn, mia_id) == (800.0, 2000.0, 1200.0),
            detail=str(_lines(conn, mia_id)))
    s.check("Ned, Ola, Pia, Jude, Kai and Lea: nothing written for any of them",
            len(_booking(conn, "Ned")) == 1 and len(_lines(conn, ned_id)) == 1
            and not _booking(conn, "Ola") and not _booking(conn, "Pia") and all(
                len(_lines(conn, _booking(conn, f)[0]["id"])) == n
                for f, n in (("Jude", 1), ("Kai", 0), ("Lea", 1))))
    s.check("two lines in all (Heidi's 404, Mia's 600), and no booking",
            after["workshop_transactions"] == before["workshop_transactions"] + 2
            and after["workshop_bookings"] == before["workshop_bookings"],
            detail=f"{before} -> {after}")
    s.check("and the summary says so, naming those left out",
            "Mia ZZRG2Mia €600.00 to " + mia_code in said and "Ned ZZRG2Ned" in said
            and "Nobody has been written to" in said, detail=said)
    held = m.stripe_ids_held(conn)
    every = [r["stripe_ref"] for r in conn.execute(
        "SELECT stripe_ref FROM workshop_transactions WHERE stripe_ref IS NOT NULL")]
    s.check("every Stripe payment is on one line in the whole app, once",
            len(every) == len(set(every))
            and {"pi_heidi_1", "pi_heidi_2", "pi_ivan_2", "pi_mia_rg", "pi_ola_room"} <= held
            and not {"pi_mia_site", "pi_ned_site", "pi_lea_2", "ch_pia_room"} & held,
            detail=str(sorted(held)))
    conn.close()

    s.section("A third run adds nothing")
    conn = db()
    before = _counts(conn)
    conn.close()
    page = _check(oc, regs2, stripe2).get_data(as_text=True)
    text = " ".join(page.split())
    s.check("nothing is offered to add or attach",
            not _offered(page, "topup") and not _offered(page, "attach"),
            detail=f"{_offered(page, 'topup')} {_offered(page, 'attach')}")
    s.check("the three brought up to date read as the same as RetreatGuru",
            "Paid: €904.00 in RetreatGuru now" not in text, detail=text[:0])
    s.check("and Mia's RetreatGuru payment, now held here, is never matched again",
            "pi_mia_rg, already a payment line here" in text, detail=text[:0])
    r = _run(oc, page, topup=["retreatguru:heidi|zzrg2heidi|x|pi_heidi_2"])
    conn = db()
    s.check("a stale tick for money already added is refused, and nothing is written",
            _counts(conn) == before and any("was not offered" in f for f in flashes(r)),
            detail=f"{before} -> {_counts(conn)} / {flashes(r)}")
    conn.close()
    r = _run(oc, page)
    conn = db()
    s.check("running it writes nothing more", _counts(conn) == before,
            detail=f"{before} -> {_counts(conn)} / {flashes(r)}")
    conn.close()

    s.section("The write itself refuses a Stripe payment already held")
    with m.app.test_request_context():
        conn = db()
        regs, _c = m.parse_rg_registrations(regs2, house_today())
        charges = m.parse_stripe_payments(stripe2)
        plan = m.plan_rg_import(conn, regs, {}, m.parse_rg_people(_people())[1], charges,
                                due_days=30, today=house_today(), stripe_given=True)
        line = next(l for l in plan["payers"] if l["key"].startswith("retreatguru:heidi|"))
        # As if the check had missed it: pi_heidi_1 is on Heidi's ledger already.
        line["topup"] = [c for c in charges if c["ref"] == "pi_heidi_1"]
        before = _counts(conn)
        try:
            _made, _kept, problems = m.run_rg_import(
                conn, plan, set(), [], user_id=None, topups={line["key"] + "|pi_heidi_1"})
        except sqlite3.IntegrityError as e:
            problems = [("the database", f"raised {e}")]
        conn.rollback()
        s.check("(slipped past the check, it is refused at the write, with nothing kept)",
                any("already recorded here: pi_heidi_1" in p[1] for p in problems)
                and _counts(conn) == before, detail=str(problems))
        conn.close()

    s.section("Nothing was sent")
    s.check("no email of any kind", not mail.calls, detail=str(mail.calls))


if __name__ == "__main__":
    print(run().report())
