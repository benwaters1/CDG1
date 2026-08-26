"""Paid leave is earned, not granted.

The app held one number per person — `annual_leave_days` — and counted approved
days against it. That answers "how much of the allowance is left", which is the
right question only for somebody who worked the whole year. Here, most people
did not.

Somebody who works June to September has earned about ten days, not thirty. The
flat figure over-credits them all summer, and then the part that actually costs
money: leave earned and not taken must be PAID when they go, and there was
nothing anywhere that worked out how much.

So: earned month by month at a configurable rate, over a leave year that starts
on a configurable month — the two things a convention collective changes, and
neither should need a deploy. The French statutory default is 2.5 days a month
over a year running 1 June to 31 May, which is a default and not a legal
opinion.

The arithmetic is deliberately conservative. Whole months only: three weeks in
earns nothing. The statutory test is finer than that — four weeks, or 24 days
actually worked, makes a month — so this reads low rather than high, and says
so rather than presenting itself as payroll.
"""
import os as _os
import sqlite3
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZACC2"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM leave_requests WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("""DELETE FROM offboarding_items WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    try:
        conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.execute("UPDATE users SET status='inactive' WHERE name LIKE ?", (TAG + "%",))
        conn.commit()
    conn.execute("UPDATE app_settings SET value = '2.5' WHERE key = 'leave_accrual_days_per_month'")
    conn.execute("UPDATE app_settings SET value = '6' WHERE key = 'leave_year_start_month'")
    conn.commit()
    conn.close()


def _employee(name, start=None, end=None, entitlement=None):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, role, status, job_role, password_hash,
           start_date, contract_end_date, annual_leave_days, created_at)
           VALUES (?, ?, 'employee', 'active', 'Front of house', 'x', ?, ?, ?, ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid",
         start, end, entitlement, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _accrual(person, on):
    conn = db()
    try:
        return m.leave_accrual(conn, person, on)
    finally:
        conn.close()


def _leave(user_id, start, end, kind="vacation", status="approved"):
    conn = db()
    conn.execute(
        """INSERT INTO leave_requests (user_id, start_date, end_date, reason, leave_type,
           status, requested_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, start, end, f"{TAG} time off", kind, status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def run():
    s = Suite("Leave accrual")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("A season earns part of a year, not all of it")
    seasonal = _employee("Margot", start="2026-06-01")
    a = _accrual(seasonal, date(2026, 10, 1))
    s.check("four months worked", a["months_worked"] == 4, detail=f"got {a['months_worked']}")
    s.check("earns ten days, not thirty", a["accrued"] == 10.0, detail=f"got {a['accrued']}")

    s.section("A whole year earns the whole entitlement")
    full = _employee("Hugo", start="2025-06-01")
    b = _accrual(full, date(2027, 5, 31))
    s.check("twelve months", b["months_worked"] == 12)
    s.check("thirty days", b["accrued"] == 30.0, detail=f"got {b['accrued']}")

    s.section("Part of a month earns nothing yet")
    # Conservative on purpose: the statutory test is finer, so this reads low
    # rather than paying for a month somebody has not finished.
    s.check("three weeks in is still zero",
            _accrual(seasonal, date(2026, 6, 21))["accrued"] == 0.0)
    s.check("but the first full month counts",
            _accrual(seasonal, date(2026, 7, 1))["accrued"] == 2.5,
            detail=f"got {_accrual(seasonal, date(2026, 7, 1))['accrued']}")

    s.section("An agreement caps it")
    capped = _employee("Inès", start="2025-06-01", entitlement=25)
    s.check("a 25-day agreement is a ceiling",
            _accrual(capped, date(2027, 5, 31))["accrued"] == 25.0)

    s.section("Somebody who has already left stops earning")
    gone = _employee("Léon", start="2026-06-01", end="2026-08-31")
    g = _accrual(gone, date(2026, 12, 1))
    s.check("three months, then nothing more", g["months_worked"] == 3,
            detail=f"got {g['months_worked']}")
    s.check("7.5 days earned", g["accrued"] == 7.5)

    s.section("The leave year is not the calendar year")
    conn = db()
    ws, we = m.leave_year_window(conn, date(2026, 3, 1))
    conn.close()
    s.check("March falls in the year that began the previous June",
            (ws, we) == (date(2025, 6, 1), date(2026, 5, 31)), detail=f"{ws}..{we}")

    s.section("Days taken come off what was earned")
    _leave(seasonal["id"], "2026-07-06", "2026-07-10")
    a2 = _accrual(seasonal, date(2026, 10, 1))
    s.check("five days taken", a2["taken"] == 5, detail=f"got {a2['taken']}")
    s.check("five left of the ten earned", a2["remaining"] == 5.0,
            detail=f"got {a2['remaining']}")

    s.section("Sick leave does not eat the paid entitlement")
    _leave(seasonal["id"], "2026-08-03", "2026-08-05", kind="sick")
    s.check("still five taken", _accrual(seasonal, date(2026, 10, 1))["taken"] == 5)

    s.section("A request straddling the year end is split, not double counted")
    # A week over the changeover belongs partly to each year. Counting it whole
    # in both would pay for it twice.
    strad = _employee("Noé", start="2024-06-01")
    _leave(strad["id"], "2026-05-29", "2026-06-04")
    before = _accrual(strad, date(2026, 5, 31))["taken"]
    after = _accrual(strad, date(2026, 7, 1))["taken"]
    s.check("three days land in the year that ends", before == 3, detail=f"got {before}")
    s.check("four in the one that starts", after == 4, detail=f"got {after}")

    s.section("What is owed if they walked out today")
    owed = _accrual(gone, date(2026, 12, 1))
    s.check("everything earned and not taken", owed["owed_on_departure"] == 7.5)
    ahead = _employee("Paul", start="2026-06-01")
    _leave(ahead["id"], "2026-06-08", "2026-06-28")     # 21 days, more than earned
    a3 = _accrual(ahead, date(2026, 7, 1))
    s.check("taking more than earned is not a debt owed back to them",
            a3["owed_on_departure"] == 0.0,
            detail=f"remaining {a3['remaining']}, owed {a3['owed_on_departure']}")
    s.check("though the negative balance is still visible", a3["remaining"] < 0)

    s.section("Leaving puts the payout on the offboarding checklist")
    # The line with a number on it. "Settle final pay" does not carry the
    # figure, so whoever settles the pay has to know it was worked out.
    leaver = _employee(
        "Sylvain",
        start=(date.today().replace(day=1) - timedelta(days=120)).isoformat())
    oc.post(f"/directory/{leaver['id']}/toggle-status", follow_redirects=True)
    conn = db()
    labels = [r["label"] for r in conn.execute(
        "SELECT label FROM offboarding_items WHERE user_id = ?", (leaver["id"],)).fetchall()]
    conn.close()
    s.check("the days owed are named on it",
            any("leave earned and not taken" in l for l in labels),
            detail=f"{labels}")
    s.check("with the number, not just a reminder",
            any(ch.isdigit() for l in labels if "leave earned" in l for ch in l),
            detail="a checklist line with no figure is the same reminder as before")

    s.section("Leaving also produces the paperwork they are owed")
    # The app drafted what a new employee needs and nothing for somebody going,
    # which is the harder half: it happens at short notice and two of these are
    # statutory rather than courteous.
    conn = db()
    conn.execute(
        """INSERT INTO company_info (id, legal_name, registered_address, registration_number)
           VALUES (1, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET legal_name = excluded.legal_name,
             registered_address = excluded.registered_address,
             registration_number = excluded.registration_number""",
        ("SCI Torrents", "Chateau de Gudanes, 09140", "812 345 678 00019"))
    conn.commit()
    docs = conn.execute("SELECT title, filename FROM documents WHERE user_id = ?",
                        (leaver["id"],)).fetchall()
    conn.close()
    titles = [d["title"] for d in docs]
    s.check("a certificat de travail is drafted",
            any("Certificat de travail" in t for t in titles), detail=f"{titles}")
    s.check("and a solde de tout compte",
            any("Solde de tout compte" in t for t in titles), detail=f"{titles}")
    s.check("both are marked as drafts, because neither is ready to sign",
            all("DRAFT" in t for t in titles if "de tra" in t or "tout compte" in t),
            detail=f"{titles}")

    body = {}
    for d in docs:
        with open(_os.path.join(m.UPLOAD_DIR, d["filename"]), encoding="utf-8") as f:
            body[d["title"]] = f.read()
    cert = next((v for k, v in body.items() if "Certificat" in k), "")
    solde = next((v for k, v in body.items() if "Solde" in k), "")

    s.check("the certificat names the employee", leaver["name"] in cert)
    s.check("and the dates employed", (leaver["start_date"] or "") in cert,
            detail="a certificat without the dates is not a certificat")
    s.check("and the job held", "Front of house" in cert)
    # The law is specific: it carries the dates and the work, and nothing that
    # could count against the person holding it.
    s.check("and carries no judgement of the person",
            not any(w in cert.lower() for w in
                    ("dismiss", "misconduct", "performance", "reason for leaving",
                     "resigned", "sacked")),
            detail="a certificat de travail must not say why they left")

    s.check("the solde carries the leave figure this app worked out",
            "Leave earned and not taken" in solde, detail=solde[:120])
    s.check("and says plainly that it is not the receipt",
            "NOT the receipt" in solde,
            detail="a draft that reads as final is worse than no draft")
    s.check("and leaves the payroll figures blank rather than inventing them",
            "__________" in solde)
    s.check("the attestation France Travail is deliberately NOT drafted",
            "deliberately does not draft one" in solde,
            detail="a plausible-looking draft of a filed declaration is worse than none")

    conn = db()
    labels = [r["label"] for r in conn.execute(
        "SELECT label FROM offboarding_items WHERE user_id = ?", (leaver["id"],)).fetchall()]
    conn.close()
    s.check("and it is on the checklist instead",
            any("attestation France Travail" in l for l in labels), detail=f"{labels}")
    s.check("with handing over the certificat",
            any("certificat de travail" in l for l in labels))

    s.section("The documents are not written twice")
    oc.post(f"/directory/{leaver['id']}/toggle-status", follow_redirects=True)
    oc.post(f"/directory/{leaver['id']}/toggle-status", follow_redirects=True)
    conn = db()
    again = conn.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE user_id = ? AND title LIKE 'Certificat%'",
        (leaver["id"],)).fetchone()["c"]
    conn.close()
    s.check("coming back and leaving again does not stack up copies", again == 1,
            detail=f"{again} certificats on file")

    s.section("The rate and the leave year are settings, not code")
    page = oc.get("/admin/leave")
    html = page.get_data(as_text=True)
    s.check("the leave page loads", page.status_code == 200, page)
    s.check("and shows what each person has earned", "earned" in html)
    s.check("the rate can be changed", "leave_accrual_days_per_month" in html)
    r = oc.post("/admin/leave/settings",
                data={"leave_accrual_days_per_month": "2", "leave_year_start_month": "1"},
                follow_redirects=True)
    conn = db()
    now_rate = m.leave_setting(conn, "leave_accrual_days_per_month")
    ws2, _ = m.leave_year_window(conn, date(2026, 3, 1))
    conn.close()
    s.check("a different agreement takes effect", now_rate == 2.0, detail=f"{flashes(r)[:1]}")
    s.check("including a leave year that starts in January", ws2 == date(2026, 1, 1))
    # Four months, not nine: the leave year now starts in January but she did
    # not start until June, and accrual runs from whichever is later. My first
    # expectation here said nine and the app said four — the app was right.
    s.check("and it is applied to the figures",
            _accrual(seasonal, date(2026, 10, 1))["accrued"] == 8.0,
            detail=f"got {_accrual(seasonal, date(2026, 10, 1))['accrued']} "
                   "(June to October is 4 months, at 2 a month)")

    s.section("Nonsense is refused rather than stored")
    bad = oc.post("/admin/leave/settings",
                  data={"leave_accrual_days_per_month": "abc"}, follow_redirects=True)
    conn = db()
    unchanged = m.leave_setting(conn, "leave_accrual_days_per_month")
    conn.close()
    s.check("a rate that is not a number is refused", unchanged == 2.0,
            detail=f"{flashes(bad)[:1]}")
    oc.post("/admin/leave/settings", data={"leave_accrual_days_per_month": "0"},
            follow_redirects=True)
    conn = db()
    still = m.leave_setting(conn, "leave_accrual_days_per_month")
    conn.close()
    s.check("and so is a rate of nothing", still == 2.0)

    s.section("Changing how leave is earned is the owner's call")
    s.check("an employee cannot change it",
            ec.post("/admin/leave/settings",
                    data={"leave_accrual_days_per_month": "9"}).status_code in (302, 403))
    conn = db()
    audited = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'leave_settings_changed'"
    ).fetchone()["c"]
    conn.close()
    s.check("and a change is written down", audited >= 1, detail=f"{audited} entries")

    _cleanup()
    return s
