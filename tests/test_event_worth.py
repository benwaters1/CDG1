"""What an event made, and who is actually coming to it.

Rooms have night margin, break-even and room economics. An event had a quoted
price and nothing else, so the most expensive thing the house sells was the one
thing nobody could say made money. And `final_numbers` was a single integer:
the kitchen sheet gathers every dietary note in the house for a day — for
rooms and for ateliers — and a wedding's eighty covers had none of it, so the
caterer got a headcount and a telephone call.

Four things carry this file.

  THE ROWS ADD UP TO THE TOTAL, and everything the figure leaves out is NAMED.
  A margin that quietly omits the caterer and two salaried staff is worse than
  no margin, because somebody will price the next wedding off it.

  A MONTHLY SALARY IS NOT CHARGED TO AN EVENT. Somebody on a monthly wage is
  paid whether the wedding happens or not; inventing a day rate out of a salary
  would put a figure on the page that is neither the château's marginal cost
  nor that person's pay. Their hours are reported and their cost is not
  attributed, and the page says so.

  ONE DEFINITION OF LABOUR COST. Same precedence as labour_cost_breakdown —
  typed wage, then the free-text estimate, then unpriced and never zero — and
  gross is never added to employer contributions in one unlabelled number.

  A LIST AND A NUMBER THAT DISAGREE IS THE POINT. Eighty confirmed and
  sixty-two names is either eighteen people nobody has written down or a number
  that is out of date, and both are worth knowing before the caterer is told.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZZEW"


def _cleanup():
    conn = db()
    for sql in (
        "DELETE FROM shifts WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
        "DELETE FROM wage_records WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
        "DELETE FROM users WHERE name LIKE ?",
        """DELETE FROM event_guests WHERE event_id IN
           (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""",
        """DELETE FROM event_suppliers WHERE event_id IN
           (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""",
        "DELETE FROM event_inquiries WHERE contact_name LIKE ?",
    ):
        conn.execute(sql, (TAG + "%",))
    conn.commit()
    conn.close()


def _person(conn, ref, *, basis=None, amount=None, pay_rate=None, pay_type=None):
    from werkzeug.security import generate_password_hash
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status,
           pay_rate, pay_type, created_at)
           VALUES (?, ?, ?, 'employee', 'active', ?, ?, ?)""",
        (f"{TAG} {ref}", f"zzew.{ref}@example.invalid".lower(),
         generate_password_hash("x"), pay_rate, pay_type,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {ref}",)).fetchone()
    if basis:
        conn.execute(
            """INSERT INTO wage_records (user_id, basis, gross_amount,
               effective_from, created_at) VALUES (?, ?, ?, ?, ?)""",
            (row["id"], basis, amount,
             (m.house_today() - timedelta(days=365)).isoformat(),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return row


def _margin(event_id):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.event_margin(conn, event_id)
    finally:
        conn.close()


def _list(event_id):
    conn = db()
    try:
        with m.app.test_request_context("/"):
            return m.event_guest_list(conn, event_id)
    finally:
        conn.close()


def run():
    s = Suite("What an event made")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    conn = db()
    day = m.house_today() + timedelta(days=60)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, guest_count, status,
           quoted_price, amount_paid, staff_needed, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, 80, 'confirmed', 20000, 0, 3, ?)""",
        (f"{TAG}-1", f"tok{TAG}1", f"{TAG} Couple", "zzew@example.invalid",
         day.isoformat(), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    e = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                     (f"{TAG}-1",)).fetchone()

    s.section("With nothing costed, it says so rather than showing a profit")
    conn.close()
    d = _margin(e["id"])
    s.check("the revenue is what was agreed", abs(d["revenue"] - 20000) < 0.01,
            detail=f"{d['revenue']}")
    s.check("and the margin is the whole of it for now",
            abs(d["margin"] - 20000) < 0.01, detail=f"{d['margin']}")
    s.check("but employer contributions are flagged as unset",
            any("employer contributions" in c for c in d["caveats"]),
            detail=f"{d['caveats']} — gross pay only is a different figure "
                   "and must not read as the total cost")

    s.section("Suppliers come off it, and an uncosted one is named")
    conn = db()
    for name in (f"{TAG} Caterer", f"{TAG} Band"):
        conn.execute(
            """INSERT INTO event_suppliers (event_id, name, created_at)
               VALUES (?, ?, ?)""",
            (e["id"], name, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    caterer = conn.execute(
        "SELECT * FROM event_suppliers WHERE event_id = ? AND name LIKE ?",
        (e["id"], f"%Caterer")).fetchone()
    conn.execute("UPDATE event_suppliers SET cost = 6000 WHERE id = ?", (caterer["id"],))
    conn.commit()
    conn.close()
    d = _margin(e["id"])
    s.check("the costed one comes off", abs(d["margin"] - 14000) < 0.01,
            detail=f"{d['margin']}")
    # NAMED, NOT COUNTED.
    s.check("and the uncosted one is named, not treated as free",
            any(f"{TAG} Band" in c for c in d["caveats"]),
            detail=f"{d['caveats']}")
    s.check("the rows add up to the total",
            abs(sum(l["amount"] for l in d["lines"]) - d["margin"]) < 0.01,
            detail="a report whose lines do not reconcile is worse than none")

    s.section("An hourly wage is charged to the event")
    conn = db()
    hourly = _person(conn, "HOURLY", basis="hourly", amount=20)
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, event_id, created_at)
           VALUES (?, ?, '14:00', '23:00', ?, ?)""",
        (hourly["id"], day.isoformat(), e["id"],
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    d = _margin(e["id"])
    s.check("their hours are counted", abs(d["labour"]["hours"] - 9) < 0.01,
            detail=f"{d['labour']['hours']}")
    s.check("at the wage on file", abs(d["labour"]["gross"] - 180) < 0.01,
            detail=f"{d['labour']['gross']} — nine hours at twenty")
    s.check("and it comes off the margin", abs(d["margin"] - 13820) < 0.01,
            detail=f"{d['margin']}")

    s.section("A shift over midnight is nine hours, not minus fifteen")
    conn = db()
    late = _person(conn, "LATE", basis="hourly", amount=10)
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, event_id, created_at)
           VALUES (?, ?, '20:00', '02:00', ?, ?)""",
        (late["id"], day.isoformat(), e["id"],
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    d = _margin(e["id"])
    them = [p for p in d["labour"]["people"] if p["name"].endswith("LATE")][0]
    s.check("six hours, the ordinary case at a wedding",
            abs(them["hours"] - 6) < 0.01,
            detail=f"{them['hours']} — a negative span would quietly subtract "
                   "hours from the event")

    s.section("A monthly salary is reported, not charged")
    conn = db()
    salaried = _person(conn, "SALARIED", basis="monthly", amount=2400)
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, event_id, created_at)
           VALUES (?, ?, '10:00', '18:00', ?, ?)""",
        (salaried["id"], day.isoformat(), e["id"],
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    d = _margin(e["id"])
    them = [p for p in d["labour"]["people"] if p["name"].endswith("SALARIED")][0]
    s.check("their hours are on the page", abs(them["hours"] - 8) < 0.01,
            detail=f"{them['hours']}")
    s.check("their cost is not attributed", them["gross"] is None,
            detail=f"{them['gross']} — they are paid whether the wedding "
                   "happens or not, and a day rate out of a salary is neither "
                   "the marginal cost nor their pay")
    s.check("and the page says so rather than hiding it",
            any("salaried" in c for c in d["caveats"]), detail=f"{d['caveats']}")

    s.section("Somebody with no wage on file is unpriced, never zero")
    conn = db()
    nowage = _person(conn, "NOWAGE")
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, event_id, created_at)
           VALUES (?, ?, '12:00', '16:00', ?, ?)""",
        (nowage["id"], day.isoformat(), e["id"],
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    d = _margin(e["id"])
    s.check("they are named as unpriced",
            any("NOWAGE" in c for c in d["caveats"]), detail=f"{d['caveats']}")
    s.check("and the margin has not silently improved",
            abs(d["margin"] - 13760) < 0.01,
            detail=f"{d['margin']} — 20000 less 6000 of catering, 180 of "
                   "hourly and 60 of the late shift. Counting the unpriced "
                   "person as free would flatter it")

    s.section("Employer contributions are a separate line, never folded in")
    conn = db()
    conn.execute(
        """INSERT INTO app_settings (key, value)
           VALUES ('payroll_employer_contribution_percent', '40')
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""")
    conn.commit()
    conn.close()
    d = _margin(e["id"])
    labels = [l["label"] for l in d["lines"]]
    s.check("gross is its own line", any("gross" in l for l in labels),
            detail=f"{labels}")
    s.check("and the employer's share is its own",
            any("Employer contributions" in l for l in labels), detail=f"{labels}")
    s.check("the rows still add up",
            abs(sum(l["amount"] for l in d["lines"]) - d["margin"]) < 0.01)
    s.check("and the caveat about them being unset has gone",
            not any("employer contributions are not set" in c for c in d["caveats"]),
            detail=f"{d['caveats']}")
    conn = db()
    conn.execute("DELETE FROM app_settings WHERE key = 'payroll_employer_contribution_percent'")
    conn.commit()
    conn.close()

    s.section("The guest list, and the number they told us")
    oc.post(f"/admin/events/{e['id']}/guests/paste", data={
        "names": "Marie Dubois, Table 3, no shellfish\n"
                 "Jean Petit, Table 3\n"
                 "Anne Moreau, Table 1, vegetarian\n"
                 "\n"
                 ", , nothing but a comma",
    }, follow_redirects=True)
    gl = _list(e["id"])
    s.check("the names go on", gl["named"] == 3, detail=f"{gl['named']}")
    s.check("with the tables", "Table 3" in gl["seatings"], detail=f"{gl['seatings']}")
    s.check("and what they cannot eat", len(gl["dietary"]) == 2,
            detail=f"{len(gl['dietary'])}")
    # A LIST AND A NUMBER THAT DISAGREE is the thing worth showing.
    s.check("the gap against what they told us is stated",
            gl["gap"] == 77, detail=f"{gl['gap']} — eighty told us, three named")

    s.section("A line it could not read is named, not swallowed")
    resp = oc.post(f"/admin/events/{e['id']}/guests/paste",
                   data={"names": "Paul Girard" + chr(10) + ", , only commas"},
                   follow_redirects=True)
    said = " ".join(flashes(resp))
    s.check("the line it could not read is named in the message",
            "only commas" in said,
            detail=f"{said!r} — a cheerful total with one line quietly dropped "
                   "is the bulk-reporting failure this app has a rule about")
    s.check("and it is reported as an error, not a success",
            "1 of 2" in said or "Nothing was" in said,
            detail=f"{said!r} — anything skipped makes the whole action an "
                   "error, because a bulk action that half worked must not "
                   "look clean")

    s.section("The kitchen sheet finally has the wedding in it")
    conn = db()
    with m.app.test_request_context("/"):
        sheet = m.kitchen_sheet(conn, day)
    conn.close()
    events = [sec for sec in sheet["sections"] if sec["kind"] == "event"]
    names = [r["who"] for sec in events for r in sec["rows"]]
    s.check("the named guests are on it", "Marie Dubois" in names,
            detail=f"{names[:4]} — the one service with the most people in it "
                   "had the least information")
    s.check("and somebody with nothing to declare is not",
            "Jean Petit" not in names,
            detail="a sheet listing everybody is a sheet nobody reads")

    s.section("And it is cleared once the event is over")
    conn = db()
    conn.execute("UPDATE event_inquiries SET preferred_date = ?, end_date = ? WHERE id = ?",
                 ((m.house_today() - timedelta(days=2)).isoformat(),
                  (m.house_today() - timedelta(days=2)).isoformat(), e["id"]))
    conn.commit()
    with m.app.test_request_context("/"):
        cleared = m.purge_health_notes(conn)
    conn.close()
    gl = _list(e["id"])
    s.check("the dietary notes go", not gl["dietary"],
            detail=f"{cleared.get('event_guests')} cleared — health data, kept "
                   "only to cook for somebody safely")
    s.check("the names stay, because the event record does",
            gl["named"] == 4,
            detail=f"{gl['named']} — three pasted, plus the one added while "
                   "testing the reporter")
    s.check("and the notice says so",
            "guest list for an event" in oc.get("/privacy").get_data(as_text=True).lower(),
            detail="the notice is a set of testable claims about this code")

    s.section("The pages")
    margin_page = oc.get(f"/admin/events/{e['id']}/margin").get_data(as_text=True)
    s.check("the margin page opens", e["reference_code"] in margin_page)
    s.check("with the caveats on it",
            "does not include" in margin_page,
            detail="somebody will price the next wedding off this page")
    guests_page = oc.get(f"/admin/events/{e['id']}/guests").get_data(as_text=True)
    s.check("the guest list opens", "Marie Dubois" in guests_page)
    s.check("both are reachable from the enquiries list",
            f"/admin/events/{e['id']}/margin" in oc.get("/admin/events").get_data(as_text=True)
            and f"/admin/events/{e['id']}/guests" in oc.get("/admin/events").get_data(as_text=True))

    s.section("Guards")
    s.check("an unknown event has no margin page",
            oc.get("/admin/events/999999/margin").status_code == 404)
    s.check("an employee cannot read what an event made",
            ec.get(f"/admin/events/{e['id']}/margin").status_code in (302, 403))
    s.check("nor the guest list",
            ec.get(f"/admin/events/{e['id']}/guests").status_code in (302, 403))
    before = _list(e["id"])["named"]
    ec.post(f"/admin/events/{e['id']}/guests/add", data={"name": "Sneaked In"})
    s.check("nor add to it", _list(e["id"])["named"] == before,
            detail="read back, because a refusal and a save are both a 302")

    s.section("One at a time, and off again")
    # Reached as the owner, not only refused as an employee: a route the suite
    # only ever bounces off has not been tested.
    oc.post(f"/admin/events/{e['id']}/guests/add",
            data={"name": "Luc Bernard", "seating": "Table 2",
                  "dietary_notes": "no dairy"}, follow_redirects=True)
    gl = _list(e["id"])
    s.check("one guest can be added on their own", gl["named"] == before + 1,
            detail=f"{gl['named']}")
    added = [g for g in gl["guests"] if g["name"] == "Luc Bernard"][0]
    s.check("with what they cannot eat", (added["dietary_notes"] or "") == "no dairy")
    oc.post(f"/admin/events/guest/{added['id']}/delete", follow_redirects=True)
    s.check("and taken off again", _list(e["id"])["named"] == before,
            detail=f"{_list(e['id'])['named']}")
    s.check("an unknown guest is a 404",
            oc.post("/admin/events/guest/999999/delete").status_code == 404)

    _cleanup()
    return s
