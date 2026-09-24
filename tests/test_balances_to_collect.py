"""The day a workshop balance falls due, and the button that takes it.

The owner asked for it this way: a notification when a balance falls due --
thirty days before the atelier -- and then they press the button. Nothing
charges a card on its own. So this is the whole path: which balances the page
lists and what each one needs; the button taking exactly the ticked amounts,
through the same charge the daily job uses; everything it did NOT take named
with its reason; and the notice reaching the owner once, staying on the owner
home and as a task until the money is in, then closing itself.

Stripe is stood in for. Nothing here reaches the network or moves money.
"""
import itertools
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZBC"


class CardError(Exception):
    """Named like stripe.CardError, which is how the app knows a refusal."""
    def __init__(self, message):
        super().__init__(message)
        self.user_message = message


_ISSUED = itertools.count(1)


class FakeIntents:
    """Charges succeed, except on the cards set up to refuse. Every one gets an
    id nobody else has, as Stripe's do -- the ledger writes each PaymentIntent
    once, so two stand-ins handing out the same id would look like one payment
    reported twice."""
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("payment_method") == "pm_decline":
            raise CardError("Your card was declined.")
        return {"id": f"pi_{TAG}_{next(_ISSUED)}", "status": "succeeded"}


class FakeStripe:
    def __init__(self):
        self.PaymentIntent = FakeIntents()


class _Stood:
    """Stripe stood in for, and the real one put back however the block ends."""
    def __enter__(self):
        self.real = (m.stripe, m.STRIPE_SECRET_KEY)
        self.fake = FakeStripe()
        m.stripe, m.STRIPE_SECRET_KEY = self.fake, "sk_test_stand_in"
        return self.fake

    def __exit__(self, *exc):
        m.stripe, m.STRIPE_SECRET_KEY = self.real
        return False


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ?)"
    conn.execute(f"DELETE FROM workshop_transactions WHERE workshop_booking_id IN {ids}",
                 (TAG + "%",))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM notifications WHERE kind = 'balance_due'")
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.commit()
    conn.close()


def _registration(ref, *, due_in=0, card=True, method="pm_test", paid=600.0, opt_out=0,
                  failed=False, hold=None, status="confirmed", do_not_email=0, total=2000.0):
    """A registration whose balance falls due `due_in` days from today."""
    conn = db()
    now = _harness.datetime_now()
    if not conn.execute("SELECT 1 FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone():
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, default_capacity,
               active, sort_order, created_at, deposit_percent)
               VALUES (?, '', ?, 30, 1, 94, ?, 30)""", (f"{TAG} Atelier", total, now))
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (f"{TAG} Atelier",)).fetchone()["id"]
    start = house_today() + timedelta(days=30 + max(due_in, 0))
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, 30, ?, ?)""",
        (wid, start.isoformat(), (start + timedelta(days=4)).isoformat(), f"{TAG} {ref}", now))
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {ref}",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, party_size, status,
           reference_code, manage_token, created_at, total_price, deposit_amount, balance_amount,
           deposit_paid_at, balance_due_date, stripe_customer_id, stripe_payment_method_id,
           autocharge_opt_out, autocharge_failed_at, collect_hold, do_not_email)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 600, 1400, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} {ref}", f"{TAG.lower()}{ref.lower()}@example.invalid", status,
         f"{TAG}{ref}", f"tok{TAG}{ref}", now, total, now,
         (house_today() + timedelta(days=due_in)).isoformat(),
         "cus_test" if card else None, method if card else None, opt_out,
         now if failed else None, hold, do_not_email))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", paid, method="stripe")
        conn.commit()
    conn.close()
    return bid


def _mine(items):
    return {r["reference"][len(TAG):]: r for r in items if r["reference"].startswith(TAG)}


def _owed(bid):
    conn = db()
    due = m.workshop_balance_due(conn, bid)[0]
    conn.close()
    return due


def _row(bid):
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Balances to collect")
    oc, ec, _owner, _emp = clients()
    _cleanup()

    ids = {
        "Ready": _registration("Ready"),
        "Late": _registration("Late", due_in=-3),
        "Part": _registration("Part", paid=1100.0),
        "Decline": _registration("Decline", method="pm_decline"),
        "NoCard": _registration("NoCard", card=False),
        "Opted": _registration("Opted", opt_out=1),
        "Quiet": _registration("Quiet", card=False, do_not_email=1),
        "Refused": _registration("Refused", failed=True),
        "Held": _registration("Held", hold="Stripe gave no clear answer. Look for ZZBCHeld."),
        "Soon": _registration("Soon", due_in=5),
        "Settled": _registration("Settled", paid=2000.0),
        "Far": _registration("Far", due_in=30),
        "Gone": _registration("Gone", status="cancelled"),
    }

    s.section("What the page lists, and what each one needs")
    conn = db()
    listed = _mine(m.workshop_balances_due(conn))
    conn.close()
    want = {"Ready": "ready", "Late": "ready", "Part": "ready", "Decline": "ready",
            "NoCard": "no_card", "Opted": "opted_out", "Quiet": "no_card",
            "Refused": "refused", "Held": "held", "Soon": "coming"}
    s.check("each due balance is there with what it needs",
            {k: v["state"] for k, v in listed.items()} == want,
            detail=f"{ {k: v['state'] for k, v in listed.items()} }")
    s.check("a settled one, a cancelled one and one a month off are not",
            not ({"Settled", "Gone", "Far"} & set(listed)), detail=f"{sorted(listed)}")
    s.check("what is owed is the ledger's, not the figure from booking day",
            listed.get("Part", {}).get("owed") == 900.0,
            detail=f"{listed.get('Part', {}).get('owed')} -- a guest who part-paid would be "
                   "charged all of it")
    s.check("and how late it is, in days", listed.get("Late", {}).get("days_late") == 3,
            detail=f"{listed.get('Late', {}).get('days_late')}")

    s.section("The page shows them, and only to the owner")
    with _Stood():
        r = oc.get("/management/balances")
    page = r.get_data(as_text=True)
    s.check("it answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("a balance ready to take is ticked, with its amount beside it",
            f'name="reg_id" value="{ids["Ready"]}" checked' in page
            and f'name="expect_{ids["Ready"]}" value="1400.00"' in page)
    s.check("one with no card is offered a link to pay instead",
            f'name="link_id" value="{ids["NoCard"]}"' in page
            and f'name="reg_id" value="{ids["NoCard"]}"' not in page)
    s.check("one coming up can be seen and not taken",
            f"{TAG} Soon" in page and f'value="{ids["Soon"]}"' not in page)
    s.check("a held one says why", "Look for ZZBCHeld" in page)
    # The counted chips are the point of the toolbar: one that says 4 and
    # gives 3 is worse than none.
    import html as _html
    import re as _re
    chip = _re.search(r'<a href="([^"]+)"\s*class="chip[^"]*">Take from the card '
                      r'<span class="chip-n">(\d+)</span>', page)
    if chip:
        with _Stood():
            got = oc.get(_html.unescape(chip.group(1))).get_data(as_text=True)
        shown = _re.search(r"Showing (\d+) of (\d+)", got)
        s.check("its 'take from the card' chip gives what it counts",
                shown is not None and int(shown.group(1)) == int(chip.group(2)),
                detail=f"chip {chip.group(2)}, shown {shown.group(1) if shown else None}")
    else:
        s.check("its 'take from the card' chip is there", False, detail="no chip on the page")
    r = ec.get("/management/balances")
    s.check("staff cannot open it", r.status_code != 200 or f"{TAG} Ready" not in
            r.get_data(as_text=True), detail=f"HTTP {r.status_code}")
    # With Stripe stood in for, or a route open to anybody would still take
    # nothing -- for want of Stripe, not for want of permission -- and this
    # would pass for the wrong reason.
    with _Stood() as fake:
        r = ec.post("/management/balances/collect",
                    data={"reg_id": str(ids["Ready"]), f"expect_{ids['Ready']}": "1400.00"})
    s.check("or take money from it", _owed(ids["Ready"]) == 1400.0
            and not fake.PaymentIntent.calls,
            detail=f"owed {_owed(ids['Ready'])}, {len(fake.PaymentIntent.calls)} charge(s) "
                   f"asked for after a staff POST (HTTP {r.status_code})")
    with _Stood() as fake:
        ec.post(f"/management/balances/{ids['Held']}/release", data={"note": "staff"})
        ec.post(f"/management/balances/{ids['Refused']}/retry", data={"expect": "1400.00"})
        ec.post("/management/balances/send-links", data={"link_id": str(ids["NoCard"])})
    conn = db()
    staff_mail = conn.execute("SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ?",
                              (f"{TAG.lower()}nocard@example.invalid",)).fetchone()["c"]
    conn.close()
    s.check("nor release a hold, try a card, or send a link",
            _row(ids["Held"])["collect_hold"] and not fake.PaymentIntent.calls
            and _row(ids["Refused"])["autocharge_failed_at"] and staff_mail == 0,
            detail=f"hold {bool(_row(ids['Held'])['collect_hold'])}, "
                   f"{len(fake.PaymentIntent.calls)} charges, {staff_mail} emails")

    s.section("The button takes exactly what was ticked, and names what it did not")
    ticked = ["Ready", "Late", "Part", "Decline", "NoCard", "Held", "Refused"]
    form = {"reg_id": [str(ids[k]) for k in ticked]}
    for k in ticked:
        form[f"expect_{ids[k]}"] = f"{listed[k]['owed']:.2f}"
    # One amount out of date: the guest paid something after the page was drawn.
    form[f"expect_{ids['Late']}"] = "1300.00"
    with _Stood() as fake:
        r = oc.post("/management/balances/collect", data=form, follow_redirects=True)
    said = " ".join(flashes(r))
    asked = [c["payment_method"] for c in fake.PaymentIntent.calls]
    s.check("Stripe is asked for the ready ones only",
            len(asked) == 3 and fake.PaymentIntent.calls[0]["off_session"] is True,
            detail=f"{len(asked)} charges asked for: {asked}")
    s.check("and for exactly what is owed",
            sorted(c["amount"] for c in fake.PaymentIntent.calls) == [90000, 140000, 140000],
            detail=f"{[c['amount'] for c in fake.PaymentIntent.calls]}")
    s.check("the ones taken owe nothing now",
            _owed(ids["Ready"]) == 0.0 and _owed(ids["Part"]) == 0.0,
            detail=f"Ready {_owed(ids['Ready'])}, Part {_owed(ids['Part'])}")
    s.check("the message counts them and says how much",
            "Collected 2 of 7 balances" in said and "€2,300.00" in said, detail=said)
    s.check("the refused card is named, with what the bank said",
            f"{TAG}Decline" in said and "Your card was declined." in said, detail=said)
    s.check("and so is the one whose amount changed, with nothing taken from it",
            f"{TAG}Late" in said and "changed" in said and _owed(ids["Late"]) == 1400.0,
            detail=said)
    s.check("and the one with no card", f"{TAG}NoCard" in said and "no card" in said, detail=said)
    s.check("and the held one", f"{TAG}Held" in said and "held" in said, detail=said)
    s.check("a card refused before is not tried again from the list",
            f"{TAG}Refused" in said and ids["Refused"] and
            not any(c.get("metadata", {}).get("workshop_booking_id") == str(ids["Refused"])
                    for c in fake.PaymentIntent.calls), detail=said)
    s.check("and it says what it skipped, rather than a cheerful total", "Skipped" in said,
            detail=said)
    conn = db()
    audited = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'workshop_balance_collect' "
        "AND target LIKE ?", (TAG + "%",)).fetchone()["c"]
    receipts = conn.execute(
        "SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ? AND subject LIKE 'Balance paid%'",
        (f"{TAG.lower()}ready@example.invalid",)).fetchone()["c"]
    conn.close()
    s.check("every attempt is in the audit log, with who pressed it", audited == 3,
            detail=f"{audited} audit lines")
    s.check("and the guest whose balance was taken gets a receipt", receipts == 1,
            detail=f"{receipts} receipts")

    s.section("Pressing it again takes nothing more")
    with _Stood() as fake:
        oc.post("/management/balances/collect",
                data={"reg_id": str(ids["Ready"]), f"expect_{ids['Ready']}": "1400.00"},
                follow_redirects=True)
    s.check("a balance already taken is not charged twice", not fake.PaymentIntent.calls
            and _owed(ids["Ready"]) == 0.0, detail=f"{len(fake.PaymentIntent.calls)} charges")

    s.section("One not yet due is not taken, whatever is posted")
    with _Stood() as fake:
        r = oc.post("/management/balances/collect",
                    data={"reg_id": str(ids["Soon"]), f"expect_{ids['Soon']}": "1400.00"},
                    follow_redirects=True)
    s.check("nothing is charged", not fake.PaymentIntent.calls)
    s.check("and it says so", any("not due yet" in f for f in flashes(r)), detail=f"{flashes(r)}")

    s.section("A link to pay, for the ones no card can settle")
    with _Stood():
        r = oc.post("/management/balances/send-links",
                    data={"link_id": [str(ids["NoCard"]), str(ids["Opted"]), str(ids["Quiet"])]},
                    follow_redirects=True)
    conn = db()
    mail = {row["to_address"]: row["body"] for row in conn.execute(
        "SELECT to_address, body FROM email_outbox WHERE to_address LIKE ?",
        (f"{TAG.lower()}%",)).fetchall()}
    conn.close()
    said = " ".join(flashes(r))
    s.check("both are emailed, asking for what is owed",
            "1400.00" in mail.get(f"{TAG.lower()}nocard@example.invalid", "")
            and f"{TAG.lower()}opted@example.invalid" in mail,
            detail=f"{sorted(mail)}")
    s.check("the guest who asked not to be emailed is not",
            f"{TAG.lower()}quiet@example.invalid" not in mail)
    s.check("and is named as not sent", "Sent 2 of 3 pay links" in said
            and f"{TAG}Quiet" in said and "asked not to be emailed" in said, detail=said)

    s.section("A held balance is released by a person, then collected")
    r = oc.post(f"/management/balances/{ids['Held']}/release",
                data={"note": "Looked in Stripe, nothing came in"}, follow_redirects=True)
    conn = db()
    released = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'workshop_collect_hold_released' "
        "AND target = ?", (f"{TAG}Held",)).fetchone()
    conn.close()
    s.check("the hold comes off", not _row(ids["Held"])["collect_hold"])
    s.check("and the record says who looked and what the hold was",
            released is not None and "nothing came in" in released["details"]
            and "ZZBCHeld" in released["details"],
            detail=f"{released['details'] if released else None}")
    with _Stood() as fake:
        oc.post("/management/balances/collect",
                data={"reg_id": str(ids["Held"]), f"expect_{ids['Held']}": "1400.00"},
                follow_redirects=True)
    s.check("after which it can be collected", _owed(ids["Held"]) == 0.0
            and len(fake.PaymentIntent.calls) == 1, detail=f"owed {_owed(ids['Held'])}")

    s.section("A refused card is tried again only when the owner chooses")
    with _Stood() as fake:
        r = oc.post(f"/management/balances/{ids['Refused']}/retry",
                    data={"expect": "1400.00"}, follow_redirects=True)
    s.check("tried once, and taken", len(fake.PaymentIntent.calls) == 1
            and _owed(ids["Refused"]) == 0.0, detail=f"{flashes(r)}")
    s.check("the refusal is cleared", not _row(ids["Refused"])["autocharge_failed_at"])

    s.section("The notice: once, the day they fall due")
    _cleanup()
    _registration("NoticeA")
    _registration("NoticeB", card=False)
    _registration("NoticeLater", due_in=4)
    conn = db()
    # Worked out from the same list the job reads, so a registration some
    # other suite left behind moves the expectation rather than the result.
    newly = [r for r in m.workshop_balances_due(conn)
             if r["state"] != "coming" and not r["noticed_at"]]
    expect_title = (f"{len(newly)} workshop balance{'' if len(newly) == 1 else 's'} due — "
                    f"€{sum(r['owed'] for r in newly):,.2f} to collect")
    with m.app.test_request_context("/"):
        said = m.run_balance_due_notice_job(conn)
        again = m.run_balance_due_notice_job(conn)
    notes = conn.execute("SELECT * FROM notifications WHERE kind = 'balance_due'").fetchall()
    conn.close()
    note = notes[0] if notes else None
    s.check("the owner is told", len(notes) == 1, detail=f"{len(notes)} notifications ({said})")
    s.check("how many, and how much", note is not None and note["title"] == expect_title
            and len(newly) >= 2, detail=f"{note['title'] if note else None} / {expect_title}")
    s.check("naming each, and which can be taken from a card",
            note is not None and f"{TAG} NoticeA" in note["body"]
            and "no card kept" in note["body"] and "1 of them can be taken" in note["body"],
            detail=f"{note['body'] if note else None}")
    s.check("it goes to the page with the button",
            note is not None and note["link"] == "/management/balances",
            detail=f"{note['link'] if note else None}")
    s.check("not the one still to come", note is not None and "NoticeLater" not in note["body"])
    s.check("and not again the next morning", again == "nothing newly due", detail=again)
    _registration("NoticeC")
    conn = db()
    with m.app.test_request_context("/"):
        m.run_balance_due_notice_job(conn)
    newest = conn.execute("SELECT * FROM notifications WHERE kind = 'balance_due' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    s.check("one falling due later is its own notice, naming only it",
            newest is not None and f"{TAG} NoticeC" in newest["body"]
            and "NoticeA" not in newest["body"], detail=f"{newest['body'] if newest else None}")
    s.check("the job is registered, and on without anybody turning it on",
            any(j[0] == "balance_due_notice" for j in m.AUTOMATION_JOBS)
            and m.AUTOMATION_SETTING_DEFAULTS["automation_balance_due_notice_enabled"] == "1")

    s.section("The owner home says so until they are collected")
    conn = db()
    due_now = [r for r in m.workshop_balances_due(conn)
               if r["state"] not in ("coming", "refused")]
    others = [r for r in due_now if not r["reference"].startswith(TAG)]
    ready_n = sum(1 for r in due_now if r["state"] == "ready")
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, house_today())
    conn.close()
    line = next((w for w in warnings if "workshop balance" in w["title"]), None)
    s.check("there is a line for them", line is not None
            and line["href"] == "/management/balances", detail=f"{[w['title'] for w in warnings]}")
    s.check("counting them all, and how many can be taken from a card",
            line is not None and line["count"] == len(due_now)
            and f"{ready_n} can be taken from the card" in line["detail"] and ready_n >= 2,
            detail=f"{line['detail'] if line else None} ({len(due_now)} due, {ready_n} ready)")
    conn = db()
    found, _dropped = m.watch_task_findings(conn, house_today())
    conn.close()
    task = next((f for f in found if f[0] == "balances"), None)
    s.check("and a task for the calendar", task is not None
            and task[1] == "Workshop balances to collect", detail=f"{task}")

    s.section("And both close themselves once the money is in")
    conn = db()
    for row in conn.execute("SELECT id FROM workshop_bookings WHERE reference_code IN (?, ?, ?)",
                            (f"{TAG}NoticeA", f"{TAG}NoticeB", f"{TAG}NoticeC")).fetchall():
        m.add_workshop_transaction(conn, row["id"], "payment", "Bank transfer", 1400.0,
                                   method="bank_transfer")
    conn.commit()
    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, house_today())
    found, _dropped = m.watch_task_findings(conn, house_today())
    conn.close()
    line = next((w for w in warnings if "workshop balance" in w["title"]), None)
    task = next((f for f in found if f[0] == "balances"), None)
    if others:
        # Somebody else's due balance keeps the line; ours must have left it.
        s.check("the line on the owner home no longer counts them",
                line is not None and line["count"] == len(others),
                detail=f"{line['count'] if line else None} vs {len(others)} others")
        s.check("and nor does the task", task is not None and TAG not in task[2])
    else:
        s.check("the line on the owner home goes", line is None,
                detail=f"{[w['title'] for w in warnings]}")
        s.check("and so does the task", task is None, detail=f"{task}")

    s.section("Run now for the charging job shows the page instead of charging blind")
    _cleanup()
    _registration("Blind")
    with _Stood() as fake:
        r = oc.post("/admin/automation/run/workshop_autocharge")
    s.check("it goes to the balances page", r.status_code in (302, 303)
            and r.headers.get("Location", "").endswith("/management/balances"),
            detail=f"HTTP {r.status_code} -> {r.headers.get('Location')}")
    s.check("and takes nothing", not fake.PaymentIntent.calls,
            detail=f"{len(fake.PaymentIntent.calls)} charges")

    s.section("It can be reached from the menu")
    page = oc.get("/admin/workshops").get_data(as_text=True)
    start = page.find("Workshops &amp; Sessions</a>")
    menu = page[start:page.find("</div>", start)] if start >= 0 else ""
    s.check("under Workshops", 'href="/management/balances"' in menu,
            detail="looked in the Workshops menu" if menu else "no Workshops menu found")

    _cleanup()
    return s
