"""What the contract says against what happened, and whose hours earned the tips.

Three things the house could not ask, each with a way of being wrong that is
worse than not asking.

THE CONTRACT. contract_type has said CDI or CDD for months; it has never said
thirty-five hours. So "are they working what we agreed" could not be asked at
all -- and in France that is not a management nicety, it is the difference
between paid overtime and unpaid overtime. The gap matters in BOTH directions:
over is a liability the house is accruing, under is somebody on a thirty-five
hour contract being given twenty, which is a pay cut nobody agreed to.

Somebody with no contracted figure is LISTED with the gap blank, never
computed against zero. Reading a missing number as nought would put every one
of them at the top as wildly over, which is exactly the sort of confident
wrong answer that gets a report ignored.

THE TIPS. pos_orders.service_charge has been collected since the till was
built and never distributed. Shared by hours on the floor, because that is the
only basis this app can defend -- the house has never recorded a role
weighting, and inventing one here would be software deciding a chef is worth
0.8 of a server.

Two details that are the whole difference between a fair split and an argument:
the service DAY, not the calendar day, because a table that pays at 01:30 was
worked by the night before's staff; and rounding DOWN, with the remainder named
as its own line. A few cents a service is nothing. A few cents a service that
always lands on the same person is noticed, and remembered.

WHEN SOMEBODY CANNOT WORK, said before the rota is built rather than after. Not
a swap -- a swap is what you need once a shift is already on you. A range that
ends before it starts is refused, because the row it would write matches no day
at all: the person believes they have told the house and the rota never hears.
"""
from _harness import Suite, clients, db

from datetime import timedelta

import _harness

m = _harness.m
TAG = "ZZTIP"


def _cleanup(conn):
    conn.execute("DELETE FROM staff_unavailability WHERE user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM time_entries WHERE user_id IN "
                 "(SELECT id FROM users WHERE name LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM pos_orders WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.commit()


def _staff(conn, name, hours=None):
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, status,
             contracted_hours_per_week, created_at)
           VALUES (?, 'x', 'employee', ?, 'active', ?, ?)""",
        ("%s.%s@example.invalid" % (TAG.lower(), name.split()[-1].lower()),
         name, hours, m.datetime.now(m.timezone.utc).isoformat()))
    return conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()["id"]


def run():
    s = Suite("hours against the contract, and tips against the hours")
    oc, ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)

    s.section("What the contract says against what happened")

    over = _staff(conn, TAG + " Overworked", 35.0)
    under = _staff(conn, TAG + " Underused", 35.0)
    silent = _staff(conn, TAG + " Nofigure", None)
    start = m.house_today() - timedelta(days=7)
    end = m.house_today()

    def shift(uid, day_offset, hours):
        begin = now - timedelta(days=day_offset)
        conn.execute(
            "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
            (uid, begin.isoformat(), (begin + timedelta(hours=hours)).isoformat()))

    for d in range(1, 6):
        shift(over, d, 10)      # 50 hours against 35
        shift(under, d, 2)      # 10 hours against 35
        shift(silent, d, 8)
    conn.commit()

    data = m.contracted_vs_worked(conn, start, end)
    rows = {r["name"]: r for r in data["rows"]}
    s.check("somebody over their contract shows a positive gap",
            rows[TAG + " Overworked"]["gap"] > 0,
            detail=str(rows[TAG + " Overworked"]))
    s.check("and somebody under shows a negative one",
            rows[TAG + " Underused"]["gap"] < 0,
            detail="under is a pay cut nobody agreed to, not a quiet week: %s"
                   % rows[TAG + " Underused"])
    # The confident wrong answer this avoids.
    s.check("somebody with no contracted figure has no gap, not a huge one",
            rows[TAG + " Nofigure"]["gap"] is None,
            detail="reading a missing number as nought puts every one of them "
                   "at the top as wildly over: %s" % rows[TAG + " Nofigure"])
    s.check("but they are still listed, and named as needing a figure",
            TAG + " Nofigure" in data["no_contracted_hours"],
            detail=str(data["no_contracted_hours"])[:120])
    s.check("the worst gap is at the top, whichever way it goes",
            data["rows"][0]["name"] == TAG + " Underused",
            detail="ten hours against thirty-five agreed is a gap of -25, "
                   "which is worse than fifty against thirty-five: %s"
                   % [(r["name"], r["gap"]) for r in data["rows"][:3]])

    # A clock-out before its clock-in poisons any bare SUM of hours.
    conn.execute(
        "INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
        (under, now.isoformat(), (now - timedelta(hours=5)).isoformat()))
    conn.commit()
    poisoned = {r["name"]: r for r in m.contracted_vs_worked(conn, start, end)["rows"]}
    s.check("a clock-out before its clock-in cannot take hours away",
            poisoned[TAG + " Underused"]["worked"] >= rows[TAG + " Underused"]["worked"],
            detail="%s then %s — a negative shift subtracted from a total is "
                   "how a timesheet ends up owing the house time"
                   % (rows[TAG + " Underused"]["worked"],
                      poisoned[TAG + " Underused"]["worked"]))

    s.section("Tips, shared by hours on the floor")

    # A service day well in the past: today's window has the demo staff and
    # this suite's own contract fixtures clocked into it, and a check that
    # counts everybody on the floor cannot tell its own two apart from theirs.
    day = m.service_day() - timedelta(days=200)
    first, last = m.service_day_window(day)
    opened = m.parse_datetime_iso(first) + timedelta(hours=2)
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, service_date, opened_at,
             closed_at, status, service_charge, notes)
           VALUES ('T1', 2, ?, ?, ?, 'paid', 100.0, ?)""",
        (day.isoformat(), opened.isoformat(), opened.isoformat(), TAG + " service"))
    conn.commit()

    took = m.service_charge_taken(conn, day)
    s.check("the till's service charge is found", took["total"] == 100.0, detail=str(took))

    a = _staff(conn, TAG + " Longshift", 35.0)
    b = _staff(conn, TAG + " Lateon", 35.0)
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
                 (a, opened.isoformat(), (opened + timedelta(hours=6)).isoformat()))
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
                 (b, (opened + timedelta(hours=4)).isoformat(),
                  (opened + timedelta(hours=6)).isoformat()))
    # A THIRD person, one hour, so the split does not divide exactly. With
    # 6 + 2 + 1 hours against 150 the shares repeat, and rounding a share up
    # instead of down pays out more than came in -- which is invisible on any
    # fixture where the arithmetic happens to come out even.
    c = _staff(conn, TAG + " Onehour", 35.0)
    conn.execute("INSERT INTO time_entries (user_id, clock_in_at, clock_out_at) VALUES (?, ?, ?)",
                 (c, (opened + timedelta(hours=5)).isoformat(),
                  (opened + timedelta(hours=6)).isoformat()))
    conn.commit()

    tronc = m.tronc_shares(conn, day)
    by_name = {r["name"]: r for r in tronc["shares"]}
    s.check("both people who worked it are on it",
            TAG + " Longshift" in by_name and TAG + " Lateon" in by_name,
            detail=str(tronc["shares"]))
    s.check("six hours earns three times what two hours earns",
            abs(by_name[TAG + " Longshift"]["share"]
                - 3 * by_name[TAG + " Lateon"]["share"]) < 0.05,
            detail=str(tronc["shares"]))
    # Never more than came in.
    s.check("the shares never add up to more than the pot",
            tronc["allocated"] <= tronc["pot"],
            detail="%s of %s — rounding a share UP pays out money the house "
                   "did not take" % (tronc["allocated"], tronc["pot"]))
    s.check("the split does not come out even, which is the point of it",
            tronc["remainder"] > 0,
            detail="%s left over from %s across %s hours — a fixture that "
                   "divides exactly cannot tell rounding up from rounding down"
                   % (tronc["remainder"], tronc["pot"], tronc["hours"]))
    s.check("and the remainder is named rather than given to somebody",
            tronc["remainder"] == round(tronc["pot"] - tronc["allocated"], 2),
            detail="a few cents that always land on the same person is the "
                   "kind of thing that is remembered")

    s.section("A table that pays at half past one")

    # The service day ends at 05:00. A bill closed after midnight belongs to
    # the night before and to the people who worked it.
    after_midnight = m.parse_datetime_iso(last) - timedelta(hours=1)
    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, service_date, opened_at,
             closed_at, status, service_charge, notes)
           VALUES ('T2', 2, ?, ?, ?, 'paid', 50.0, ?)""",
        (day.isoformat(), after_midnight.isoformat(), after_midnight.isoformat(),
         TAG + " late"))
    conn.commit()
    s.check("it counts to the night it was worked",
            m.service_charge_taken(conn, day)["total"] == 150.0,
            detail="%s — reading a calendar date would hand that table's "
                   "service to the wrong shift"
                   % m.service_charge_taken(conn, day)["total"])
    s.check("and not to the next day",
            m.service_charge_taken(conn, day + timedelta(days=1))["total"] == 0,
            detail=str(m.service_charge_taken(conn, day + timedelta(days=1))))

    s.section("A bill that was voided")

    conn.execute(
        """INSERT INTO pos_orders (table_label, covers, service_date, opened_at,
             closed_at, status, service_charge, notes)
           VALUES ('T3', 2, ?, ?, ?, 'void', 500.0, ?)""",
        (day.isoformat(), opened.isoformat(), opened.isoformat(), TAG + " voided"))
    conn.commit()
    s.check("its service is not shared out",
            m.service_charge_taken(conn, day)["total"] == 150.0,
            detail="%s — a voided bill was never taken, so there is nothing to "
                   "share; paying it out would be the house handing over money "
                   "it did not receive" % m.service_charge_taken(conn, day)["total"])

    s.section("Service charged with nobody clocked in")

    conn.execute("DELETE FROM time_entries WHERE user_id IN (?, ?, ?)", (a, b, c))
    conn.commit()
    orphan = m.tronc_shares(conn, day)
    s.check("the money is not quietly lost", orphan["pot"] == 150.0, detail=str(orphan))
    s.check("it is reported as a timesheet to fix", orphan["nobody_clocked_in"],
            detail="the service was charged and somebody worked it; the answer "
                   "is that the timesheets are wrong, not that the tips are")

    s.section("Saying you cannot work")

    ok, why = m.record_unavailability(conn, over, "2027-03-04", "2027-03-06", "A wedding")
    s.check("a range is accepted", ok, detail=str(why))
    conn.commit()
    # Inclusive at both ends: a person saying "the fourth to the sixth" means
    # three days, which is not what a half-open range does.
    for d, expected in (("2027-03-04", True), ("2027-03-05", True),
                        ("2027-03-06", True), ("2027-03-07", False)):
        found = [r for r in m.unavailable_on(conn, m.parse_date(d))
                 if r["user_id"] == over]
        s.check("%s is %s" % (d, "covered" if expected else "not"),
                bool(found) == expected)

    ok, why = m.record_unavailability(conn, over, "2027-04-10", "2027-04-01")
    s.check("a range that ends before it starts is refused", not ok, detail=str(why))
    s.check("and says so rather than writing a row that matches no day",
            why and "before" in why.lower(), detail=str(why))

    s.section("Taking one back")

    # Through the page, as a real person: the employee client is signed in as
    # somebody, and this proves the WORKING branch rather than the refusal.
    emp_id = conn.execute("SELECT id FROM users WHERE role = 'employee' "
                          "ORDER BY id LIMIT 1").fetchone()["id"]
    ec.post("/rota/cannot-work",
            data={"starts_on": "2027-05-01", "ends_on": "2027-05-03",
                  "reason": TAG + " a wedding"}, follow_redirects=True)
    theirs = conn.execute(
        "SELECT * FROM staff_unavailability WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (emp_id,)).fetchone()
    s.check("somebody can file their own through the page", theirs is not None,
            detail="user %s" % emp_id)
    if theirs:
        r = ec.post("/rota/cannot-work/%d/remove" % theirs["id"], follow_redirects=True)
        s.check("and take it back again", r.status_code == 200, detail=str(r.status_code))
        s.check("the row is really gone",
                conn.execute("SELECT COUNT(*) c FROM staff_unavailability WHERE id = ?",
                             (theirs["id"],)).fetchone()["c"] == 0)

    # Somebody else's. An id in a URL is not proof of whose it is.
    mine_id = conn.execute(
        "SELECT id FROM staff_unavailability WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (over,)).fetchone()
    if mine_id:
        r = ec.post("/rota/cannot-work/%d/remove" % mine_id["id"], follow_redirects=False)
        s.check("but not somebody else's", r.status_code == 404,
                detail="HTTP %s — deleting another person's is how a shift "
                       "lands on somebody who is away" % r.status_code)
        s.check("and it is still there",
                conn.execute("SELECT COUNT(*) c FROM staff_unavailability WHERE id = ?",
                             (mine_id["id"],)).fetchone()["c"] == 1)

    s.section("The pages")

    r = oc.get("/admin/tronc")
    s.check("the owner can see the tips page", r.status_code == 200, r)
    s.check("an employee cannot", ec.get("/admin/tronc").status_code != 200)
    r = ec.get("/rota/cannot-work")
    s.check("but anybody can say when they cannot work", r.status_code == 200, r)
    body = r.get_data(as_text=True)
    s.check("and the page says it is not a swap and not leave",
            "not a swap" in body.lower(),
            detail="the three get confused, and the difference is whether a "
                   "shift lands on somebody at all")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
