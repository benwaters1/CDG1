"""One person, one month, and the hours the agreement pays extra for.

Hospitality hours are unsocial by nature and the convention collective pays for
it. The rota and the clock already knew the exact hours to the minute; nothing
applied a rate to them, so a Sunday shift cost the house exactly what a Tuesday
did in every report the owner reads — including the one they would use to
decide whether to open on a Sunday at all. The meals the château feeds its
staff are a benefit in kind with a value URSSAF sets every year, and they
appeared in no total anywhere.

And "what did Marie cost in July" was answered with a CSV of the whole team.

Four things carry this file.

  IT IS NOT A BULLETIN DE PAIE, and the page says so above the numbers. A
  bulletin is a legal document with mandatory headings, the employee's net and
  the employee-side contributions line by line. Something that LOOKED like one
  would be worse than nothing, because it would be filed.

  ZERO MEANS NOT SET, EVERYWHERE. Every rate here ships at zero for the same
  reason the employer contribution does: the real figures come from the
  agreement the house works to, and a percentage the app invented would read as
  one the app knows. Hours worked at a rate nobody has set are NAMED, not
  dropped — a statement that quietly omits the Sunday hours is one the
  accountant produces a bulletin from.

  ONE DEFINITION OF LABOUR COST. Every figure comes from
  labour_cost_breakdown, so this is a view of the costing rather than a second
  one, and the premiums are inside gross because the employer contribution is
  due on them.

  THE HOLIDAYS ARE COMPUTED, NOT LISTED. Five of the eleven French jours fériés
  move with Easter, and a table of dates is a table somebody has to remember to
  extend.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPS"


def _cleanup():
    conn = db()
    for sql in (
        "DELETE FROM time_entries WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
        "DELETE FROM wage_records WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
        "DELETE FROM leave_requests WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
        "DELETE FROM users WHERE name LIKE ?",
    ):
        conn.execute(sql, (TAG + "%",))
    conn.commit()
    conn.close()


def _setting(conn, key, value):
    conn.execute("""INSERT INTO app_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                 (key, value))
    conn.commit()


def _clear(conn, *keys):
    for k in keys:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (k,))
    conn.commit()


def _person(conn, ref, *, basis="hourly", amount=15.0, leave=25):
    from werkzeug.security import generate_password_hash
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status,
           annual_leave_days, start_date, created_at)
           VALUES (?, ?, ?, 'employee', 'active', ?, '2020-01-01', ?)""",
        (f"{TAG} {ref}", f"zzps.{ref}@example.invalid".lower(),
         generate_password_hash("x"), leave,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {ref}",)).fetchone()
    conn.execute(
        """INSERT INTO wage_records (user_id, basis, gross_amount, effective_from,
           created_at) VALUES (?, ?, ?, '2020-01-01', ?)""",
        (row["id"], basis, amount, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return row


def _worked(conn, user_id, day, start_hour, hours):
    """A clocked shift in LOCAL time, stored as the app stores them."""
    began = datetime(day.year, day.month, day.day, start_hour, 0,
                     tzinfo=m.LOCAL_TZ)
    conn.execute(
        """INSERT INTO time_entries (user_id, clock_in_at, clock_out_at)
           VALUES (?, ?, ?)""",
        (user_id, began.astimezone(timezone.utc).isoformat(),
         (began + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()))
    conn.commit()


def run():
    s = Suite("A month of somebody's pay")
    _cleanup()
    oc, ec, _owner, _emp = clients()

    s.section("The French holidays are computed, not listed")
    s.check("eleven of them", len(m.french_public_holidays(2026)) == 11,
            detail=f"{len(m.french_public_holidays(2026))}")
    s.check("Easter Monday 2026 is the 6th of April",
            date(2026, 4, 6) in m.french_public_holidays(2026))
    s.check("and 2027's is the 29th of March",
            date(2027, 3, 29) in m.french_public_holidays(2027),
            detail="five of the eleven move with Easter, which is why they "
                   "are computed")
    s.check("Ascension 2026 is the 14th of May",
            date(2026, 5, 14) in m.french_public_holidays(2026))
    s.check("Pentecost Monday is one of them",
            date(2026, 5, 25) in m.french_public_holidays(2026),
            detail="a jour ferie even in years it is worked as the journee de "
                   "solidarite, which is a question about pay not the calendar")
    s.check("an ordinary Tuesday is not",
            not m.is_french_holiday(date(2026, 9, 15)))

    # A month with a Sunday, a night shift and a holiday in it. May 2026 has
    # the 1st (Fete du Travail) and the 8th, so it is the natural month to use.
    year, month = 2026, 5
    conn = db()
    p = _person(conn, "MARIE", basis="hourly", amount=20.0)
    # Sunday 3 May, a plain Tuesday, a night shift, and the 1st of May.
    _worked(conn, p["id"], date(2026, 5, 3), 10, 6)     # Sunday, daytime
    _worked(conn, p["id"], date(2026, 5, 5), 10, 8)     # Tuesday, daytime
    _worked(conn, p["id"], date(2026, 5, 6), 21, 4)     # 21:00-01:00, night
    _worked(conn, p["id"], date(2026, 5, 1), 10, 5)     # Fete du Travail
    conn.close()

    s.section("The hours are split before anybody sets a rate")
    conn = db()
    with m.app.test_request_context("/"):
        hours = m.premium_hours(conn, p["id"], "2026-05-01", "2026-06-01")
    conn.close()
    s.check("everything worked is counted", abs(hours["worked"] - 23) < 0.01,
            detail=f"{hours}")
    s.check("the Sunday hours are found", abs(hours["sunday"] - 6) < 0.01,
            detail=f"{hours['sunday']}")
    s.check("the holiday hours are found", abs(hours["holiday"] - 5) < 0.01,
            detail=f"{hours['holiday']} — the 1st of May")
    # THE WINDOW WRAPS MIDNIGHT. 21:00-01:00 is four night hours, not one.
    s.check("and the night window wraps midnight",
            abs(hours["night"] - 4) < 0.01,
            detail=f"{hours['night']} — 21:00 to 01:00 is four night hours, "
                   "and a window that did not wrap would find one")

    s.section("With no rates set, nothing is added and it says so")
    conn = db()
    _clear(conn, "payroll_sunday_premium_percent", "payroll_night_premium_percent",
           "payroll_holiday_premium_percent", "payroll_meal_value_eur",
           "payroll_meals_per_shift", "payroll_employer_contribution_percent")
    with m.app.test_request_context("/"):
        st = m.monthly_pay_statement(conn, p["id"], year, month)
    conn.close()
    s.check("gross is just the hours", abs(st["gross"] - 23 * 20) < 0.01,
            detail=f"{st['gross']}")
    s.check("but the unpriced hours are named",
            any("Sunday" in u for u in st["unrated"])
            and any("night" in u for u in st["unrated"]),
            detail=f"{st['unrated']} — a statement that silently drops them is "
                   "one the accountant produces a bulletin from")
    s.check("and so are the meals", any("staff meals" in u for u in st["unrated"]),
            detail=f"{st['unrated']}")

    s.section("Once the rates are set, each one is its own line")
    conn = db()
    _setting(conn, "payroll_sunday_premium_percent", "50")
    _setting(conn, "payroll_night_premium_percent", "20")
    _setting(conn, "payroll_holiday_premium_percent", "100")
    with m.app.test_request_context("/"):
        st = m.monthly_pay_statement(conn, p["id"], year, month)
    conn.close()
    kinds = [l["kind"] for l in st["lines"]]
    labels = " | ".join(l["label"] for l in st["lines"])
    s.check("the base hours are a line", "base" in kinds, detail=labels)
    s.check("and there are three premium lines",
            kinds.count("premium") == 3, detail=labels)
    # 23h at 20 = 460, plus 6h Sunday at +50% = 60, 4h night at +20% = 16,
    # 5h holiday at +100% = 100.
    s.check("the arithmetic is what the agreement says",
            abs(st["gross"] - (460 + 60 + 16 + 100)) < 0.01,
            detail=f"{st['gross']} — 460 base, 60 Sunday, 16 night, 100 holiday")
    s.check("the rows add up to the gross",
            abs(sum(l["amount"] for l in st["lines"]) - st["gross"]) < 0.01,
            detail="a statement whose lines do not reconcile is worse than none")
    s.check("and nothing is left unpriced now", not st["unrated"] or
            all("meals" in u for u in st["unrated"]), detail=f"{st['unrated']}")

    s.section("An hour can be Sunday and night at once, in both rows")
    conn = db()
    q = _person(conn, "BOTH", basis="hourly", amount=10.0)
    _worked(conn, q["id"], date(2026, 5, 10), 22, 2)   # Sunday 22:00-00:00
    with m.app.test_request_context("/"):
        both = m.premium_hours(conn, q["id"], "2026-05-01", "2026-06-01")
    conn.close()
    s.check("counted as Sunday", abs(both["sunday"] - 2) < 0.01, detail=f"{both}")
    s.check("and as night", abs(both["night"] - 2) < 0.01,
            detail=f"{both} — how they combine is a question for the "
                   "agreement, not for this function")
    s.check("but only two hours were worked", abs(both["worked"] - 2) < 0.01,
            detail=f"{both['worked']} — counting the hour twice in `worked` "
                   "would double the month")

    s.section("The meals the house feeds them")
    conn = db()
    _setting(conn, "payroll_meals_per_shift", "1")
    _setting(conn, "payroll_meal_value_eur", "5.35")
    with m.app.test_request_context("/"):
        st = m.monthly_pay_statement(conn, p["id"], year, month)
    conn.close()
    benefit = [l for l in st["lines"] if l["kind"] == "benefit"]
    s.check("it is a line of its own", len(benefit) == 1, detail=f"{st['lines']}")
    s.check("one per shift worked, not one per day",
            abs(benefit[0]["amount"] - 4 * 5.35) < 0.01,
            detail=f"{benefit[0]['amount']} — four clocked shifts")
    s.check("and it is inside gross, because contributions are due on it",
            abs(st["gross"] - (460 + 60 + 16 + 100 + 4 * 5.35)) < 0.01,
            detail=f"{st['gross']}")

    s.section("A meal value with no meals per shift is still no meals")
    # The two settings are independent, and the section above cleared both --
    # so a break that counted a meal per shift regardless was invisible. How
    # many meals a shift carries is the owner's to say: a lunch service and a
    # split shift are not the same thing.
    conn = db()
    _setting(conn, "payroll_meal_value_eur", "5.35")
    _clear(conn, "payroll_meals_per_shift")
    with m.app.test_request_context("/"):
        none_taken = m.meals_taken(conn, p["id"], "2026-05-01", "2026-06-01")
        none_worth = m.meal_benefit(conn, p["id"], "2026-05-01", "2026-06-01")
    _setting(conn, "payroll_meals_per_shift", "1")
    conn.close()
    s.check("no meals are counted", none_taken["meals"] == 0,
            detail=f"{none_taken} — a value set does not say how many")
    s.check("and nothing is added for them", none_worth is None,
            detail=f"{none_worth}")

    s.section("Employer contributions are on all of it, and never folded in")
    conn = db()
    _setting(conn, "payroll_employer_contribution_percent", "40")
    with m.app.test_request_context("/"):
        st = m.monthly_pay_statement(conn, p["id"], year, month)
    conn.close()
    s.check("the rate is read", st["employer_rate"] == 40,
            detail=f"{st['employer_rate']}")
    s.check("and applied to gross including the premiums",
            abs(st["employer"] - round(st["gross"] * 0.4, 2)) < 0.01,
            detail=f"{st['employer']} on {st['gross']}")
    s.check("the cost to the house is the two added, stated as such",
            abs(st["total"] - round(st["gross"] + st["employer"], 2)) < 0.01)

    s.section("A salaried month is not repriced by the hour")
    conn = db()
    sal = _person(conn, "SALARIED", basis="monthly", amount=2400.0)
    _worked(conn, sal["id"], date(2026, 5, 3), 10, 6)
    with m.app.test_request_context("/"):
        st_sal = m.monthly_pay_statement(conn, sal["id"], year, month)
    conn.close()
    base = [l for l in st_sal["lines"] if l["kind"] == "base"]
    s.check("the salary is the base line",
            base and abs(base[0]["amount"] - 2400) < 0.01,
            detail=f"{base}")
    s.check("and no premium is invented from an hourly rate they do not have",
            not [l for l in st_sal["lines"] if l["kind"] == "premium"],
            detail=f"{[l['kind'] for l in st_sal['lines']]} — a percentage of "
                   "an hourly rate the house has not typed is inventing both "
                   "halves")

    s.section("The page")
    page = oc.get(f"/admin/payroll/statement/{p['id']}/{year}/{month}").get_data(as_text=True)
    s.check("it opens", p["name"] in page)
    # THE HONEST BIT, above the numbers.
    s.check("it says what it is not, in as many words",
            "not a bulletin de paie" in page.lower(),
            detail="something that looked like one would be filed, and would "
                   "be wrong")
    s.check("the premium hours are on it", "Sunday" in page)
    # AGAINST A FIGURE WORKED OUT HERE, not against leave_balance itself. The
    # first version of this compared the page to the same function that drew
    # it, so a break that made the balance always read zero agreed with itself
    # and passed.
    conn = db()
    conn.execute(
        """INSERT INTO leave_requests (user_id, leave_type, start_date, end_date,
           status, requested_at) VALUES (?, 'annual', '2026-04-06', '2026-04-10',
           'approved', ?)""",
        (p["id"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    page = oc.get(f"/admin/payroll/statement/{p['id']}/{year}/{month}").get_data(as_text=True)
    s.check("and the leave balance as at the month end",
            "5 of 25" in page and "2026-05-31" in page,
            detail="five days approved in April, inside the leave year to "
                   "31 May, against an entitlement of twenty-five")
    s.check("so twenty left", ">20<" in page or " 20 " in page or "20 left" in page,
            detail="the figure somebody plans a holiday around")
    s.check("it is reachable from the payroll pack",
            "/admin/payroll/statement/" in oc.get("/admin/payroll").get_data(as_text=True),
            detail="a page nobody can get to is a page nobody uses")

    s.section("A month with nothing in it says so rather than showing zero")
    empty = oc.get(f"/admin/payroll/statement/{p['id']}/2024/1").get_data(as_text=True)
    s.check("it is honest about it", "nothing to itemise" in empty,
            detail="a confident zero on a pay document is worse than a blank")

    s.section("Guards")
    s.check("an unknown person is a 404",
            oc.get(f"/admin/payroll/statement/999999/{year}/{month}").status_code == 404)
    s.check("an employee cannot read somebody's pay",
            ec.get(f"/admin/payroll/statement/{p['id']}/{year}/{month}"
                   ).status_code in (302, 403))

    conn = db()
    _clear(conn, "payroll_sunday_premium_percent", "payroll_night_premium_percent",
           "payroll_holiday_premium_percent", "payroll_meal_value_eur",
           "payroll_meals_per_shift", "payroll_employer_contribution_percent")
    conn.close()
    _cleanup()
    return s
