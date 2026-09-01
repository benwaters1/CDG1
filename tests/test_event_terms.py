"""An event's deposit and balance due date, set by the app rather than by a test.

WHY THIS SUITE EXISTS, which is the whole point of it.

event_inquiries.deposit_amount and event_inquiries.balance_due_date were both
READ and never WRITTEN:

  - event_bill returned a `deposit` that was always None and a `deposit_paid`
    flag that was always False, so the guest page could not show a deposit and
    the pay box could not offer one;
  - run_event_balance_reminder_job selects on `balance_due_date IS NOT NULL`,
    so the job that chases the LARGEST single sum the house is owed could never
    match a row. It ran daily, selected nothing, recorded "no event balances
    due", and looked healthy forever.

And its own test passed, because the fixture wrote balance_due_date in its own
INSERT — a state no route in the app could produce. That is the failure this
file is written to prevent, so almost nothing here writes either column
directly: an event is confirmed THROUGH /admin/events/<id>/update the way the
owner confirms one, and then the job is asked whether it can see it.

A fixture that writes what only the app should write does not test the app. It
tests the fixture, and it reports success either way.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZTERMS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM event_payments WHERE event_id IN "
                 "(SELECT id FROM event_inquiries WHERE contact_name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM deposit_rules WHERE label LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _enquiry(ref, *, days_out=200, price=None, guests=80):
    """An enquiry as it arrives: no price, no deposit, no due date.

    Deliberately NOT given a balance_due_date. Every check below that depends
    on one being present depends on the app having put it there.
    """
    conn = db()
    kinds = m.known_event_types(conn)
    when = (house_today() + timedelta(days=days_out)).isoformat()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, contact_phone, preferred_date, guest_count,
           message, status, quoted_price, amount_paid, created_at)
           VALUES (?, ?, ?, ?, ?, '', ?, ?, 'ZZ test', 'new', ?, 0, ?)""",
        (f"{TAG}-{ref}", f"tok{TAG}{ref}".lower(), (kinds or ["wedding"])[0],
         f"{TAG} {ref}", f"{TAG.lower()}.{ref}@example.invalid".lower(), when,
         guests, price, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{ref}",)).fetchone()
    conn.close()
    return row


def _row(event_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM event_inquiries WHERE id = ?",
                            (event_id,)).fetchone()
    finally:
        conn.close()


def _confirm(client, event, **extra):
    """Confirm an event the way the owner does: through the form."""
    data = {"status": "confirmed", "quoted_price": "4500", "owner_note": ""}
    data.update(extra)
    return client.post(f"/admin/events/{event['id']}/update", data=data,
                       follow_redirects=True)


def run():
    s = Suite("Event deposit and balance terms")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("Confirming an event sets terms the app can act on")
    ev = _enquiry("A", days_out=200)
    s.check("the enquiry arrives with neither",
            ev["deposit_amount"] is None and ev["balance_due_date"] is None,
            detail="the fixture is presupposing what the app is meant to write")
    r = _confirm(oc, ev)
    s.check("the form saves", r.status_code == 200, detail=f"HTTP {r.status_code}")
    after = _row(ev["id"])
    s.check("a deposit is now on the event",
            after["deposit_amount"] is not None,
            detail="event_bill's deposit is None for every event that exists")
    s.check("and it is 30% of the quote",
            abs((after["deposit_amount"] or 0) - 1350.0) < 0.01,
            detail=f"{after['deposit_amount']}")
    s.check("a balance due date is now on the event",
            after["balance_due_date"] is not None,
            detail="the balance reminder job selects on this column being set, "
                   "so without it the job can never match a row")
    s.check("45 days before the event",
            after["balance_due_date"] ==
            (date.fromisoformat(after["preferred_date"]) - timedelta(days=45)).isoformat(),
            detail=f"{after['balance_due_date']} for {after['preferred_date']}")
    s.check("and it is in the future",
            after["balance_due_date"] > house_today().isoformat(),
            detail="a due date already past makes the reminder fire on the spot")

    s.section("The reminder job can now reach an event the app confirmed")
    # The check this suite is really for. Nothing here writes balance_due_date;
    # if the route does not write it, the job finds nothing and this fails.
    conn = db()
    try:
        near = _enquiry("B", days_out=50)      # due date lands ~5 days out
        _confirm(oc, near)
        conn2 = db()
        confirmed = conn2.execute("SELECT * FROM event_inquiries WHERE id = ?",
                                  (near["id"],)).fetchone()
        conn2.close()
        s.check("its due date is inside the reminder window",
                confirmed["balance_due_date"] is not None
                and confirmed["balance_due_date"] <=
                (house_today() + timedelta(days=21)).isoformat(),
                detail=f"{confirmed['balance_due_date']}")
        result = m.run_event_balance_reminder_job(conn, 21)
        s.check("the job sees it rather than reporting nothing to do",
                "no event balances due" not in result,
                detail=f"job said {result!r} — it selects on balance_due_date "
                       "IS NOT NULL, and nothing in the app wrote that column")
        s.check("and counts it as due",
                "due event" in result, detail=f"{result!r}")
    finally:
        conn.close()

    s.section("What the owner types wins")
    ev = _enquiry("C", days_out=200)
    _confirm(oc, ev, deposit_amount="2000", balance_due_date="2027-01-15")
    after = _row(ev["id"])
    s.check("the typed deposit is kept", abs((after["deposit_amount"] or 0) - 2000) < 0.01,
            detail=f"{after['deposit_amount']} — the house percentage overrode a "
                   "figure somebody quoted in writing")
    s.check("and the typed date", after["balance_due_date"] == "2027-01-15",
            detail=f"{after['balance_due_date']}")
    ev_c2 = _enquiry("C2", days_out=200)
    _confirm(oc, ev_c2, deposit_amount="1750,50")
    s.check("1750,50 reads as 1750.50",
            abs((_row(ev_c2["id"])["deposit_amount"] or 0) - 1750.50) < 0.01,
            detail=f"{_row(ev_c2['id'])['deposit_amount']} — a French keyboard "
                   "types a comma and the field is a number box either way")

    s.section("Re-saving does not move a date already given to a guest")
    ev = _enquiry("D", days_out=200)
    _confirm(oc, ev, deposit_amount="1000", balance_due_date="2027-03-01")
    oc.post(f"/admin/events/{ev['id']}/update",
            data={"status": "confirmed", "quoted_price": "9000", "owner_note": "bumped"},
            follow_redirects=True)
    after = _row(ev["id"])
    s.check("the deposit stands", abs((after["deposit_amount"] or 0) - 1000) < 0.01,
            detail=f"{after['deposit_amount']} — a blank field silently recomputed "
                   "terms the guest has in writing")
    s.check("and so does the due date", after["balance_due_date"] == "2027-03-01",
            detail=f"{after['balance_due_date']}")
    s.check("while the quote itself does change",
            abs((after["quoted_price"] or 0) - 9000) < 0.01,
            detail=f"{after['quoted_price']}")

    s.section("An event held inside the window is due in full")
    ev = _enquiry("E", days_out=10)
    _confirm(oc, ev)
    after = _row(ev["id"])
    s.check("no due date is invented in the past",
            after["balance_due_date"] is None
            or after["balance_due_date"] >= house_today().isoformat(),
            detail=f"{after['balance_due_date']} — a due date already gone makes "
                   "the reminder fire the moment the event is confirmed")
    s.check("and there is no deposit stage", after["deposit_amount"] is None,
            detail=f"{after['deposit_amount']} — a deposit with no balance to "
                   "follow is just the bill under another name")

    s.section("A deposit rule can be written for an event at all")
    # deposit_rules' CHECK listed restaurant, workshop and room. An event rule
    # was refused outright, which is the same bug rooms had — and it matters
    # more here: a wedding in August and a meeting in February are not held on
    # the same terms.
    conn = db()
    accepted, detail = True, ""
    try:
        conn.execute(
            """INSERT INTO deposit_rules (category, start_date, end_date,
               min_party_size, deposit_percent, label, created_at)
               VALUES ('event', NULL, NULL, 60, 50, ?, ?)""",
            (f"{TAG} big parties", datetime.now(timezone.utc).isoformat()))
        conn.commit()
    except Exception as e:
        accepted = False
        detail = f"{type(e).__name__}: {e}"
    finally:
        conn.close()
    s.check("the table accepts an event rule", accepted,
            detail=detail if not accepted else "")

    if accepted:
        ev = _enquiry("F", days_out=200, guests=80)
        _confirm(oc, ev)
        after = _row(ev["id"])
        s.check("and it applies over the house percentage",
                abs((after["deposit_amount"] or 0) - 2250.0) < 0.01,
                detail=f"{after['deposit_amount']} — expected 50% of 4500 for a "
                       "party of 80, not the house 30%")
        ev = _enquiry("G", days_out=200, guests=12)
        _confirm(oc, ev)
        s.check("a small party still gets the house percentage",
                abs((_row(ev["id"])["deposit_amount"] or 0) - 1350.0) < 0.01,
                detail=f"{_row(ev['id'])['deposit_amount']} — the rule is scoped "
                       "to 60 guests and applied to 12")

    s.section("The guest sees the terms and is offered the deposit")
    # A small party on purpose: the 60-guest rule inserted above is still in
    # the table, and this section is about what the guest is shown, not which
    # percentage applied.
    ev = _enquiry("H", days_out=200, guests=12)
    _confirm(oc, ev)
    after = _row(ev["id"])
    anon = m.app.test_client()
    page = anon.get(f"/events/manage/{after['manage_token']}").get_data(as_text=True)
    s.check("it names the deposit", "Deposit to hold the date" in page,
            detail="the deposit is on the row and the guest cannot see it")
    s.check("the deposit figure is on it", "1,350.00" in page or "1350.00" in page,
            detail="a guest told there is a deposit but not what it is writes back "
                   "to ask")
    s.check("and the date the balance falls due is named",
            "Balance due by" in page,
            detail="a guest holding a date does not know when the rest is wanted")

    s.section("The rebuild reaches a database that already exists")
    # Two code paths make this table: CREATE TABLE IF NOT EXISTS on a fresh
    # database, and the guarded rebuild for one that already exists. PRODUCTION
    # TAKES THE REBUILD — the live database was made before events were a
    # category — so the path the test DB does not take is the only one that
    # matters on deploy. Put the table back into its old shape and run the
    # migration, which is what a deploy does.
    conn = db()
    # Three other suites read this table. Putting it back into its old shape
    # means dropping it, so every row is saved here and restored below -- a
    # suite that leaves the database poorer than it found it makes the next
    # one's result depend on the order they ran in.
    saved = [tuple(r) for r in conn.execute(
        "SELECT category, start_date, end_date, min_party_size, deposit_percent, "
        "label, created_at FROM deposit_rules").fetchall()]
    conn.execute("DROP TABLE IF EXISTS deposit_rules_old")
    conn.execute("DROP TABLE IF EXISTS deposit_rules")
    conn.execute("""CREATE TABLE deposit_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        category TEXT NOT NULL
                            CHECK(category IN ('restaurant','workshop','room')),
                        start_date TEXT, end_date TEXT, min_party_size INTEGER,
                        deposit_percent REAL NOT NULL, label TEXT,
                        created_at TEXT NOT NULL)""")
    conn.execute("""INSERT INTO deposit_rules (category, deposit_percent, label, created_at)
                    VALUES ('room', 20, ?, ?)""",
                 (f"{TAG} pre-existing", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    m.init_db()

    conn = db()
    sql = (conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'deposit_rules'").fetchone() or {"sql": ""})["sql"]
    s.check("the rebuild ran and the category is now allowed",
            "'event'" in (sql or ""),
            detail="an existing database keeps the old CHECK, so every event "
                   "deposit rule the owner writes is refused in production while "
                   "a fresh test database accepts it")
    kept = conn.execute("SELECT COUNT(*) c FROM deposit_rules WHERE label = ?",
                        (f"{TAG} pre-existing",)).fetchone()["c"]
    s.check("and it carried the existing rules across", kept == 1,
            detail=f"{kept} — a rebuild that loses rules loses deposit terms the "
                   "owner set by hand")
    s.check("with no leftover shadow table",
            conn.execute("SELECT COUNT(*) c FROM sqlite_master WHERE "
                         "name = 'deposit_rules_old'").fetchone()["c"] == 0,
            detail="deposit_rules_old survived the rebuild")
    conn.execute("DELETE FROM deposit_rules")
    conn.executemany(
        "INSERT INTO deposit_rules (category, start_date, end_date, min_party_size, "
        "deposit_percent, label, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", saved)
    conn.commit()
    s.check("and the table is left as this suite found it",
            conn.execute("SELECT COUNT(*) c FROM deposit_rules").fetchone()["c"]
            == len(saved),
            detail=f"{len(saved)} rows were here before")
    conn.close()

    s.section("Guards")
    s.check("an employee cannot set an event's terms",
            ec.post(f"/admin/events/{ev['id']}/update",
                    data={"status": "confirmed", "quoted_price": "1"},
                    follow_redirects=False).status_code in (302, 403))
    ev = _enquiry("I", days_out=200)
    _confirm(oc, ev, balance_due_date="not-a-date", deposit_amount="abc")
    after = _row(ev["id"])
    s.check("rubbish in the date field does not land in the column",
            after["balance_due_date"] is None
            or after["balance_due_date"][:2] == "20",
            detail=f"{after['balance_due_date']!r}")
    s.check("and rubbish in the deposit field does not either",
            after["deposit_amount"] is None
            or isinstance(after["deposit_amount"], float),
            detail=f"{after['deposit_amount']!r}")

    _cleanup()
    return s
