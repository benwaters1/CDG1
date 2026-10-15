"""Bringing the RetreatGuru registrations across.

About thirty people booked ateliers on the old system and paid deposits or part
payments there, through the house's own Stripe account. They must arrive with
exactly what they agreed and exactly what they paid, without being asked to pay
again and without any email until the owner says so -- right to the cent, and
proved on a copy before it touches the live database.

So this runs the import end to end through the owner's own page, on made-up
exports shaped exactly like the real ones, and holds it to every line of the
brief:

  - a check writes nothing, and its totals equal the export's to the cent;
  - each registration arrives confirmed, emails off, at its agreed price, each
    payment its own line with the Stripe id it came through, its balance and
    due date as stated;
  - a guest paid in full; a part payment that is not a clean percentage, in
    two card payments; a couple on one registration; one person on two
    retreats; a guest whose session has been called off; a guest paid nothing;
  - the pay-the-balance link asks for exactly what is owed;
  - no reminder goes, nothing tries a card, and no email of any kind is sent
    or queued;
  - a refund of a migrated payment goes back to the charge it came from;
  - running it again changes nothing.

Stripe is stood in for throughout. Nothing here reaches the network.
"""
import types
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZRGI"
LOW = TAG.lower()
REG_HEADER = ("first_name,last_name,submitted,cancelled,booking_window,reg_form_complete,phone,"
              "payment,optional_items,roommate,discount_code,travel_phone_,items_discounts_total,"
              "payments_refunds_total,balance_due,scheduled_payments_total,"
              "balance_after_scheduled,registration_amount")


class _Obj(dict):
    """A Stripe object: read by attribute and by bracket alike."""
    __getattr__ = dict.get


class FakeStripe:
    """Checkout and refunds, stood in for: every call kept, none sent."""

    def __init__(self):
        self.sessions, self.refunds = [], []
        outer = self

        class Session:
            @staticmethod
            def create(**kw):
                outer.sessions.append(kw)
                return _Obj(id=f"cs_test_{TAG}_{len(outer.sessions)}",
                            url="https://checkout.stripe.test/pay")

            @staticmethod
            def retrieve(*a, **kw):
                raise RuntimeError("not asked for here")

        class Refund:
            @staticmethod
            def create(**kw):
                outer.refunds.append(kw)
                return types.SimpleNamespace(id=f"re_test_{TAG}_{len(outer.refunds)}")

        self.checkout = types.SimpleNamespace(Session=Session)
        self.Refund = Refund

    def __getattr__(self, name):                  # PaymentIntent, Charge and the rest
        raise RuntimeError(f"stripe.{name} is not stood in for here")


class _Stood:
    def __enter__(self):
        self.real = (m.stripe, m.STRIPE_SECRET_KEY)
        self.fake = FakeStripe()
        m.stripe, m.STRIPE_SECRET_KEY = self.fake, "sk_test_stand_in"
        return self.fake

    def __exit__(self, *exc):
        m.stripe, m.STRIPE_SECRET_KEY = self.real
        return False


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject))
        return True


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE guest_email LIKE ?)"
    like = f"{LOW}%"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}", (like,))
    conn.execute("DELETE FROM refunds WHERE booking_ref LIKE ?", (f"%{TAG}%",)) \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'refunds' AND sql LIKE "
                        "'%booking_ref%'").fetchone() else None
    conn.execute("DELETE FROM workshop_bookings WHERE guest_email LIKE ?", (like,))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (like,))
    conn.execute("DELETE FROM import_drafts")
    conn.commit()
    conn.close()


def _sessions():
    """Three sessions of one €2,600 atelier: two to come, one called off."""
    conn = db()
    now = _harness.datetime_now()
    conn.execute(
        """INSERT INTO workshops (title, description, price_per_person, default_capacity,
           active, sort_order, created_at, deposit_percent)
           VALUES (?, '', 2600, 10, 1, 92, ?, 10)""", (f"{TAG} Atelier", now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    out = {}
    for label, days, off in (("one", 40, False), ("two", 120, False), ("gone", 80, True)):
        start = house_today() + timedelta(days=days)
        conn.execute(
            """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes,
                 created_at, cancelled_at) VALUES (?, ?, ?, 10, ?, ?, ?)""",
            (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} {label}",
             now, now if off else None))
        out[label] = (conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                                   (f"{TAG} {label}",)).fetchone()["id"], start)
    conn.commit()
    conn.close()
    return out


def _reg(first, last, start, *, agreed, paid, list_price=None, code="", roommate="",
         cancelled="", minute=0):
    """One row of the registrations export, booked 60 days before `start`."""
    submitted_on = start - timedelta(days=60)
    submitted = f"{submitted_on.isoformat()} 10:{minute:02d}:00"
    owing = round(agreed - paid, 2)
    return ",".join([first, last, submitted, cancelled, "60", "x", "", "", "", roommate, code,
                     "", f"{list_price if list_price is not None else agreed:.2f}",
                     f"{paid:.2f}", f"{owing:.2f}", "0", f"{owing:.2f}", f"{agreed:.2f}"])


def _files(s):
    one, two, gone = s["one"][1], s["two"][1], s["gone"][1]
    past = house_today() - timedelta(days=30)
    regs = "\n".join([
        REG_HEADER,
        _reg("Ann", f"{TAG}Full", one, agreed=2600, paid=2600),
        _reg("Bob", f"{TAG}Part", one, agreed=2210, paid=777.77, list_price=2600, code="MERCI15"),
        _reg("Cara", f"{TAG}Pair", one, agreed=5200, paid=1560, roommate="Dan Pair", minute=1),
        _reg("Dan", f"{TAG}Pair", one, agreed=0, paid=0, roommate="Cara Pair", minute=1),
        _reg("Eve", f"{TAG}Twice", one, agreed=2600, paid=780, minute=2),
        _reg("Eve", f"{TAG}Twice", two, agreed=2600, paid=260, minute=3),
        _reg("Fay", f"{TAG}Gone", gone, agreed=2600, paid=260),
        _reg("Gil", f"{TAG}Cancel", one, agreed=2600, paid=0, cancelled="2026-09-01"),
        _reg("Pat", f"{TAG}Past", past, agreed=2600, paid=2600),
        _reg("Hal", f"{TAG}Nothing", two, agreed=2600, paid=0, minute=4),
        _reg("Ivy", f"{TAG}Unknown", two, agreed=2600, paid=260, minute=5),
        _reg("Jon", f"{TAG}Transfer", two, agreed=2600, paid=300, minute=6),
        # A price for two places, and one person named: brought across, with a
        # note the owner has to say they read.
        _reg("Kim", f"{TAG}Solo", two, agreed=5200, paid=520, minute=7),
        # Booked for a week that has since moved: nothing here starts that day.
        _reg("Lou", f"{TAG}Moved", one + timedelta(days=7), agreed=2600, paid=0, minute=8),
        # Paid in full on RetreatGuru, then refunded in Stripe, which
        # RetreatGuru never hears about: its export still says paid.
        _reg("Mo", f"{TAG}Refunded", two, agreed=2600, paid=2600, minute=9),
        # Paid €969.59 by booking a room by mistake, which RetreatGuru took off
        # her PRICE (2600 - 969.59 = 1630.41, less 15%) rather than calling paid.
        _reg("Nia", f"{TAG}Credit", two, agreed=1240.41, paid=0, list_price=1630.41, minute=10),
    ]) + "\n"
    people = "first_name,last_name,person_id,email,num_programs,overall_balance\n" + "\n".join(
        f"{f},{TAG}{l},{i},{LOW}.{f.lower()}@example.invalid,1,0"
        for i, (f, l) in enumerate([("Ann", "Full"), ("Bob", "Part"), ("Cara", "Pair"),
                                    ("Dan", "Pair"), ("Eve", "Twice"), ("Fay", "Gone"),
                                    ("Hal", "Nothing"), ("Jon", "Transfer"),
                                    ("Kim", "Solo"), ("Lou", "Moved"),
                                    ("Mo", "Refunded"), ("Nia", "Credit")])) + "\n"
    programmes = ("program_id,title,date_start,date_end\n"
                  f"1,The Autumn One,{one.isoformat()},{(one + timedelta(days=4)).isoformat()}\n"
                  f"2,The Summer One,{two.isoformat()},{(two + timedelta(days=4)).isoformat()}\n")
    when = (one - timedelta(days=60)).isoformat()
    stripe_rows = [
        ("ch_ann_1", "pi_ann_1", "2600,00", "0,00", "ann"),
        ("ch_bob_1", "", "500,00", "0,00", "bob"),              # a charge with no PaymentIntent
        ("ch_bob_2", "pi_bob_2", "277,77", "0,00", "bob"),
        ("ch_cara_1", "pi_cara_1", "1560,00", "0,00", "cara"),
        ("ch_eve_1", "pi_eve_1", "780,00", "0,00", "eve"),
        ("ch_eve_2", "pi_eve_2", "260,00", "0,00", "eve"),
        ("ch_eve_old", "pi_eve_old", "999,00", "0,00", "eve"),  # a retreat already over
        ("ch_ann_x", "pi_ann_x", "50,00", "50,00", "ann"),      # refunded in full: not a payment
        ("ch_kim_1", "pi_kim_1", "520,00", "0,00", "kim"),
    ]
    stripe = ("id,PaymentIntent ID,Created date (UTC),Amount,Amount Refunded,Currency,Status,"
              "Customer Email,Description\n" + "\n".join(
                  f'{c},{pi},{when} 09:00:00,"{a}","{r}",eur,Paid,{LOW}.{who}@example.invalid,'
                  "RetreatGuru registration"
                  for c, pi, a, r, who in stripe_rows)) + "\n"
    # Refunded in full: the dashboard marks it Refunded, not Paid.
    stripe += (f'ch_mo_1,pi_mo_1,{when} 09:00:00,"2600,00","2600,00",eur,Refunded,'
               f'{LOW}.mo@example.invalid,RetreatGuru registration\n')
    # Nia's room, booked by mistake: a py_ payment, no PaymentIntent.
    stripe += (f'py_nia_1,,{when} 09:00:00,"969,59","0,00",eur,Paid,'
               f'{LOW}.nia@example.invalid,Reservation #148\n')
    return regs, people, programmes, stripe


def _upload(text, name):
    import io
    return (io.BytesIO(text.encode("utf-8")), name)


def _check(oc, files, *, stripe=True, unmatched_ok=False):
    regs, people, programmes, stripe_csv = files
    data = {"action": "check", "due_days": "30",
            "registrations": _upload(regs, "registrations.csv"),
            "people": _upload(people, "personslist.csv"),
            "programmes": _upload(programmes, "programs.csv")}
    if stripe:
        data["stripe"] = _upload(stripe_csv, "payments.csv")
    if unmatched_ok:
        data["unmatched_ok"] = "on"
    return oc.post("/management/import-registrations", data=data,
                   content_type="multipart/form-data")


def _token(page):
    import re
    found = re.search(r'name="token" value="([^"]+)"', page)
    return found.group(1) if found else ""


def _keys(page):
    import re
    return re.findall(r'name="bring" value="([^"]+)"[^>]*checked', page)


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in ("workshop_bookings", "workshop_transactions", "email_outbox",
                      "notifications", "guests")}


def _mine(conn):
    return {f"{r['guest_name']}|{r['start_date']}": r for r in conn.execute(
        """SELECT wb.*, ws.start_date FROM workshop_bookings wb
             JOIN workshop_sessions ws ON ws.id = wb.session_id
            WHERE wb.guest_email LIKE ?""", (f"{LOW}%",))}


def run():
    s = Suite("bringing the RetreatGuru registrations across")
    oc, _ec, _owner, _emp = clients()
    _cleanup()
    mail = Recorder()
    real_send = m.send_email
    m.send_email = mail
    try:
        _run(s, oc, mail)
    finally:
        m.send_email = real_send
        _cleanup()
    return s


def _run(s, oc, mail):
    sess = _sessions()
    files = _files(sess)
    one, two = sess["one"][1].isoformat(), sess["two"][1].isoformat()

    s.section("Stripe's amounts, however the account's language writes them")
    read = {v: m._stripe_money(v) for v in ("1547,00", "1,90", "64,50", "2,600.00", "1.547,00",
                                            "1 547,00", "960.00", "2720", "abc", "")}
    s.check("a French account's 1547,00 is €1,547.00, not €154,700",
            read == {"1547,00": 1547.0, "1,90": 1.9, "64,50": 64.5, "2,600.00": 2600.0,
                     "1.547,00": 1547.0, "1 547,00": 1547.0, "960.00": 960.0,
                     "2720": 2720.0, "abc": None, "": None}, detail=str(read))

    s.section("The check writes nothing, and adds up to the export")
    conn = db()
    before = _counts(conn)
    conn.close()
    r = _check(oc, files)
    page = r.get_data(as_text=True)
    conn = db()
    after = _counts(conn)
    conn.close()
    s.check("it answers", r.status_code == 200, r)
    s.check("and nothing was written", before == after, detail=f"{before} -> {after}")
    s.check("the cancelled and the past stay where they are",
            "1 for retreats already over and 1 cancelled" in page)
    s.check("Bob's discount is shown against the list price", "less MERCI15" in page)
    s.check("the couple is one registration for two", "with Dan " in page)
    s.check("the called-off session stops Fay", "has been called off here" in page)
    s.check("nobody on the customer list stops Ivy", "no email address for them" in page)
    lou = page[page.find(f"Lou {TAG}Moved"):]
    s.check("a session that has moved stops Lou, rather than landing on another week",
            "No session in this app starts on" in lou[:3000], detail=lou[:600])
    s.check("a payment Stripe does not hold stops Jon", "Stripe has €0.00 from" in page)
    s.check("a payment refunded in Stripe stops Mo, and says so",
            "Stripe shows €2,600.00 refunded to this address (€2,600.00 of €2,600.00, pi_mo_1)"
            in page)
    s.check("Eve's €999 for a retreat already over is listed as not matched", "pi_eve_old" in page)
    s.check("the refunded €50 is not a payment at all", "pi_ann_x" not in page)
    s.check("Nia's €969.59 is offered as paid towards her retreat, not assumed",
            '|py_nia_1"' in page and "Reservation #148" in page
            and "the price becomes €2,210.00, paid €969.59, and €1,240.41 is still owed"
            in " ".join(page.split()))
    s.check("it says no email goes", "Emails sent or queued by bringing them across" in page)
    with m.app.test_request_context():
        conn = db()
        regs, _c = m.parse_rg_registrations(files[0], house_today())
        plan = m.plan_rg_import(conn, regs, m.parse_rg_programmes(files[2]),
                                m.parse_rg_people(files[1])[1],
                                m.parse_stripe_payments(files[3]), due_days=30,
                                today=house_today(), stripe_given=True)
        conn.close()
    want = {"agreed": round(2600 + 2210 + 5200 + 2600 + 2600 + 2600 + 2600 + 2600 + 2600 + 5200
                           + 2600 + 2600 + 1240.41, 2),
            "paid": round(2600 + 777.77 + 1560 + 780 + 260 + 260 + 0 + 260 + 300 + 520 + 2600, 2)}
    s.check("the plan's totals are the export's, to the cent",
            plan["export"]["agreed"] == want["agreed"] and plan["export"]["paid"] == want["paid"]
            and plan["export"]["owing"] == round(want["agreed"] - want["paid"], 2),
            detail=str(plan["export"]))

    s.section("A ledger that does not come out right keeps nothing")
    real = m.workshop_balance_due
    m.workshop_balance_due = lambda c, bid: (0.01, 0.0, 0.0)
    try:
        conn = db()
        before_bad = _counts(conn)
        conn.close()
        with _Stood():
            r = oc.post("/management/import-registrations", data={
                "action": "run", "token": _token(page), "bring": _keys(page),
                "checked_flags": "on"}, follow_redirects=True)
    finally:
        m.workshop_balance_due = real
    conn = db()
    after_bad = _counts(conn)
    conn.close()
    s.check("(negative control: a ledger off by a cent stops the run with nothing kept)",
            before_bad == after_bad and any("Nothing was kept" in f for f in flashes(r)),
            detail=f"{before_bad} -> {after_bad} / {flashes(r)}")

    s.section("Brought across: confirmed, silent, as agreed, every payment its own")
    token, keys = _token(page), _keys(page)
    s.check("the blocked ones cannot be ticked",
            not any(w in k for k in keys for w in ("fay", "ivy", "jon", "lou", "mo|")), detail=str(keys))
    r = oc.post("/management/import-registrations",
                data={"action": "run", "token": token, "bring": keys}, follow_redirects=True)
    s.check("Kim's price for two places, with one named, is a note to read",
            "which is 2 places at €2,600.00; 1 is named" in page)
    s.check("with a note unread, it asks to be told they were read",
            any("I have read every note" in f for f in flashes(r)), detail=str(flashes(r)))
    with _Stood():
        r = oc.post("/management/import-registrations", data={
            "action": "run", "token": token, "bring": keys, "checked_flags": "on",
            "credit": [k + "|py_nia_1" for k in keys if k.startswith("retreatguru:nia|")]},
            follow_redirects=True)
    said = " ".join(flashes(r))
    s.check("it says what it did, and that nobody has been written to",
            "Brought across 8" in said and "Nobody has been written to" in said, detail=said)
    conn = db()
    rows = _mine(conn)
    s.check("eight registrations", len(rows) == 8, detail=str(sorted(rows)))
    s.check("all confirmed, emails off, each with its RetreatGuru key",
            all(r["status"] == "confirmed" and r["do_not_email"] == 1 and r["import_key"]
                for r in rows.values()))
    expect = {
        f"Ann {TAG}Full|{one}": (0.0, 2600.0, 2600.0),
        f"Bob {TAG}Part|{one}": (1432.23, 2210.0, 777.77),
        f"Cara {TAG}Pair|{one}": (3640.0, 5200.0, 1560.0),
        f"Eve {TAG}Twice|{one}": (1820.0, 2600.0, 780.0),
        f"Eve {TAG}Twice|{two}": (2340.0, 2600.0, 260.0),
        f"Hal {TAG}Nothing|{two}": (2600.0, 2600.0, 0.0),
        f"Kim {TAG}Solo|{two}": (4680.0, 5200.0, 520.0),
        # RetreatGuru's €1,240.41 owing, unmoved; the €969.59 now shown as paid.
        f"Nia {TAG}Credit|{two}": (1240.41, 2210.0, 969.59),
    }
    for key, figures in expect.items():
        row = rows.get(key)
        got = m.workshop_balance_due(conn, row["id"]) if row else None
        s.check(f"{key.split('|')[0]}: owed, charged, paid as the export says",
                got == figures, detail=f"{got} vs {figures}")
    bob = rows.get(f"Bob {TAG}Part|{one}")
    lines = conn.execute("SELECT kind, amount, method, stripe_ref, description FROM "
                         "workshop_transactions WHERE workshop_booking_id = ? ORDER BY id",
                         (bob["id"],)).fetchall() if bob else []
    s.check("Bob: the list price less MERCI15, as a discount line",
            bob and bob["total_price"] == 2600 and any(
                l["kind"] == "discount" and l["amount"] == 390 and "MERCI15" in l["description"]
                for l in lines), detail=str([dict(l) for l in lines]))
    s.check("and his two card payments, each its own line with its Stripe id",
            sorted((l["amount"], l["stripe_ref"], l["method"]) for l in lines if l["kind"] == "payment")
            == [(277.77, "pi_bob_2", "stripe"), (500.0, "ch_bob_1", "stripe")],
            detail=str([dict(l) for l in lines]))
    nia = rows.get(f"Nia {TAG}Credit|{two}")
    nia_lines = conn.execute("SELECT kind, amount, method, stripe_ref FROM workshop_transactions "
                             "WHERE workshop_booking_id = ? ORDER BY id",
                             (nia["id"],)).fetchall() if nia else []
    s.check("Nia: the full price, the discount, and the room money paid against its Stripe id",
            nia and nia["total_price"] == 2600 and sorted(
                (l["kind"], l["amount"], l["method"], l["stripe_ref"]) for l in nia_lines)
            == [("discount", 390.0, None, None), ("payment", 969.59, "stripe", "py_nia_1")],
            detail=str([dict(l) for l in nia_lines]))
    s.check("and the summary says so",
            "Plus €969.59 paid through another booking" in said, detail=said)
    cara = rows.get(f"Cara {TAG}Pair|{one}")
    party = [g["guest_name"] for g in conn.execute(
        "SELECT guest_name FROM workshop_booking_guests WHERE workshop_booking_id = ? "
        "ORDER BY is_lead DESC", (cara["id"],))] if cara else []
    s.check("the couple: one registration of two, both named",
            cara and cara["party_size"] == 2 and party == [f"Cara {TAG}Pair", f"Dan {TAG}Pair"],
            detail=str(party))
    eves = [r for k, r in rows.items() if "Twice" in k]
    refs = {r["start_date"]: [l["stripe_ref"] for l in conn.execute(
        "SELECT stripe_ref FROM workshop_transactions WHERE workshop_booking_id = ? AND "
        "kind = 'payment'", (r["id"],))] for r in eves}
    s.check("one person on two retreats: two registrations, each with its own payment",
            refs == {one: ["pi_eve_1"], two: ["pi_eve_2"]}, detail=str(refs))
    hal = rows.get(f"Hal {TAG}Nothing|{two}")
    s.check("paid nothing: the whole price is what is due first",
            hal and hal["deposit_amount"] == 2600 and not hal["deposit_paid_at"])
    s.check("the balance is due as stated, thirty days before",
            all(r["balance_due_date"] == (m.parse_date(r["start_date"]) - timedelta(days=30)).isoformat()
                for r in rows.values()), detail=str({k: r["balance_due_date"] for k, r in rows.items()}))
    s.check("and says where it came from",
            all("Brought across from the old booking system (RetreatGuru)" in (r["notes"] or "")
                for r in rows.values()))
    guests = {g["email"] for g in conn.execute("SELECT email FROM guests WHERE email LIKE ?",
                                               (f"{LOW}%",))}
    s.check("the people are on file as guests", {f"{LOW}.ann@example.invalid",
                                                 f"{LOW}.cara@example.invalid"} <= guests)
    after_run = _counts(conn)
    conn.close()
    s.check("no email of any kind was sent", not mail.calls, detail=str(mail.calls))
    s.check("or queued, and no notification made",
            after_run["email_outbox"] == before["email_outbox"]
            and after_run["notifications"] == before["notifications"],
            detail=f"{before} -> {after_run}")

    s.section("The guest's own page, and what it asks them to pay")
    with _Stood() as fake:
        r = oc.post(f"/workshops/pay-balance/{bob['manage_token']}")
        asked = [li["price_data"]["unit_amount"] for call in fake.sessions
                 for li in call.get("line_items", [])]
    s.check("the pay link asks for exactly what is owed, €1,432.23", asked == [143223],
            detail=str(asked))
    page = oc.get(f"/workshops/manage/{bob['manage_token']}/statement").get_data(as_text=True)
    s.check("and the statement page says the same", "1,432.23" in page)

    s.section("Nothing chases them, and nothing tries a card")
    conn = db()
    with m.app.test_request_context():
        m.run_workshop_balance_reminder_job(conn, 400)
    listed = [r for r in m.workshop_balances_due(conn, today=house_today(), ahead_days=400)
              if (r["email"] or "").startswith(LOW)]
    conn.close()
    s.check("the reminder writes to none of them", not mail.calls, detail=str(mail.calls))
    s.check("Balances to collect lists what they owe, with no card to take it from",
            listed and all(r["state"] in ("no_card", "coming") for r in listed),
            detail=str([(r["who"], r["state"]) for r in listed]))

    s.section("A refund of a brought-across payment goes back to its own charge")
    conn = db()
    booking = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bob["id"],)).fetchone()
    ch_line = conn.execute("SELECT id FROM workshop_transactions WHERE workshop_booking_id = ? "
                           "AND stripe_ref = 'ch_bob_1'", (bob["id"],)).fetchone()
    pi_line = conn.execute("SELECT id FROM workshop_transactions WHERE workshop_booking_id = ? "
                           "AND stripe_ref = 'pi_bob_2'", (bob["id"],)).fetchone()
    with _Stood() as fake:
        ok1, err1, _ = m.make_refund(conn, "workshop", booking, 100.0, "test", method="stripe",
                                     payment_key=f"wt{ch_line['id']}", reason_code="other",
                                     bill_effect=m.BILL_STANDS)
        conn.commit()
        ok2, err2, _ = m.make_refund(conn, "workshop", booking, 50.0, "test", method="stripe",
                                     payment_key=f"wt{pi_line['id']}", reason_code="other",
                                     bill_effect=m.BILL_STANDS)
        conn.commit()
        sent = fake.refunds
    conn.close()
    s.check("the €500 charge with no PaymentIntent is refunded by the charge",
            ok1 and sent and sent[0].get("charge") == "ch_bob_1" and sent[0]["amount"] == 10000,
            detail=f"{err1} {sent[:1]}")
    s.check("and the other by its PaymentIntent",
            ok2 and len(sent) > 1 and sent[1].get("payment_intent") == "pi_bob_2",
            detail=f"{err2} {sent[1:2]}")

    s.section("Running it again changes nothing")
    conn = db()
    before_again = _counts(conn)
    conn.close()
    r = _check(oc, files)
    page = r.get_data(as_text=True)
    s.check("the check says they are already here", page.count("already here") >= 7)
    with _Stood():
        r = oc.post("/management/import-registrations", data={
            "action": "run", "token": _token(page), "bring": _keys(page), "checked_flags": "on"},
            follow_redirects=True)
    conn = db()
    after_again = _counts(conn)
    conn.close()
    s.check("and running it writes nothing more", before_again == after_again,
            detail=f"{before_again} -> {after_again} / {flashes(r)}")

    s.section("A payment Stripe does not hold, when the owner says so")
    r = _check(oc, files, unmatched_ok=True)
    page = r.get_data(as_text=True)
    jon_keys = [k for k in _keys(page) if "jon" in k]
    s.check("Jon can be ticked now", len(jon_keys) == 1, detail=str(_keys(page)))
    s.check("but Mo cannot: money Stripe gave back is never brought across as paid",
            not [k for k in _keys(page) if k.startswith("retreatguru:mo|")]
            and "Stripe shows €2,600.00 refunded to this address" in page,
            detail=str(_keys(page)))
    with _Stood():
        oc.post("/management/import-registrations", data={
            "action": "run", "token": _token(page), "bring": jon_keys, "checked_flags": "on"})
    conn = db()
    jon = _mine(conn).get(f"Jon {TAG}Transfer|{two}")
    line = conn.execute("SELECT * FROM workshop_transactions WHERE workshop_booking_id = ? AND "
                        "kind = 'payment'", (jon["id"],)).fetchone() if jon else None
    conn.close()
    s.check("brought across paid, not by card, with nothing to refund through Stripe",
            line and line["amount"] == 300 and line["method"] == "other" and not line["stripe_ref"],
            detail=str(dict(line)) if line else "none")


if __name__ == "__main__":
    print(run().report())
