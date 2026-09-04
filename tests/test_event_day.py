"""Who works the wedding, what the suppliers cost, and whether they may work.

The run sheet could say who was rostered that DAY, and that was all anybody
could know: a wedding for eighty and four rooms let the same night were one
undifferentiated list of names, so "are we staffed for the wedding" could not
be asked. The rota was worse — cover_gaps counted arrivals, dinners and
ateliers, and did not know events existed, so the one day of the year the
house most needs people on it read as a day with nothing happening.

event_suppliers recorded who was coming and whether they had confirmed. Not
what they charged, so no event had a margin; not whether they had been paid,
so a florist chasing an invoice was a search through somebody's email; and
nothing at all about insurance, on people working a listed monument for a day.

Four things carry this file.

  A SHIFT BELONGS TO THE EVENT OR IT DOES NOT. Everybody else on that day is
  still shown — they are in the building and they are who could be moved onto
  it — but they are not counted as cover for it.

  SHORT IS THE WORST DAY, NOT THE TOTAL. Three people across three days is not
  three people on the day of the wedding.

  A TOTAL WITH SUPPLIERS MISSING FROM IT NAMES THEM. A supplier with no cost
  recorded is named rather than treated as zero, because a margin that quietly
  omits the caterer is worse than no margin.

  PAPERS ARE JUDGED AGAINST THE DAY THEY WORK, not against today. A certificate
  that expires the week before the wedding is missing for the wedding, and
  finding that out in March is much cheaper than on the morning. The
  attestation de vigilance is required by the contract value because the law
  puts that obligation on the client — the château — rather than the supplier.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZED"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM shifts WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM event_suppliers WHERE event_id IN
                    (SELECT id FROM event_inquiries WHERE contact_name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("""DELETE FROM vendor_documents WHERE vendor_id IN
                    (SELECT id FROM vendors WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _person(conn, ref, status="active"):
    from werkzeug.security import generate_password_hash
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, ?, 'employee', ?, ?)""",
        (f"{TAG} {ref}", f"zzed.{ref}@example.invalid".lower(),
         generate_password_hash("x"), status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {ref}",)).fetchone()


def _shift(conn, user_id, day, start="14:00", end="23:00"):
    conn.execute(
        """INSERT INTO shifts (user_id, shift_date, start_time, end_time, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, day.isoformat(), start, end,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return conn.execute(
        "SELECT * FROM shifts WHERE user_id = ? AND shift_date = ? ORDER BY id DESC LIMIT 1",
        (user_id, day.isoformat())).fetchone()


def _row(event_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM event_inquiries WHERE id = ?",
                            (event_id,)).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("The day of the event")
    _cleanup()
    oc, ec, _owner, _emp = clients()
    conn = db()
    day = m.house_today() + timedelta(days=45)
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type,
           contact_name, contact_email, preferred_date, end_date, guest_count,
           status, quoted_price, amount_paid, created_at)
           VALUES (?, ?, 'wedding', ?, ?, ?, ?, 80, 'confirmed', 25000, 0, ?)""",
        (f"{TAG}-1", f"tok{TAG}1", f"{TAG} Couple", "zzed@example.invalid",
         day.isoformat(), (day + timedelta(days=1)).isoformat(),
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    e = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                     (f"{TAG}-1",)).fetchone()

    s.section("A wedding is work on the rota")
    # cover_gaps did not know events existed.
    with m.app.test_request_context("/"):
        gaps = m.cover_gaps(conn, day, day)
    mine = [g for g in gaps if g["date"] == day.isoformat()]
    s.check("the day shows up at all", mine,
            detail="a wedding for eighty on a day with no rooms let read as a "
                   "day with nothing happening")
    s.check("and it says what the work is",
            mine and any(x["what"] == "wedding" for x in mine[0].get("events", [])),
            detail=f"{mine[0].get('events') if mine else None}")

    s.section("How many the day needs")
    oc.post(f"/admin/events/{e['id']}/staffing", data={"staff_needed": "6"},
            follow_redirects=True)
    s.check("it is recorded", (_row(e["id"])["staff_needed"] or 0) == 6,
            detail=f"{_row(e['id'])['staff_needed']}")
    with m.app.test_request_context("/"):
        gaps = m.cover_gaps(conn, day, day)
    mine = [g for g in gaps if g["date"] == day.isoformat()]
    s.check("and the rota can see the headcount",
            mine and mine[0]["events"][0]["needs"] == 6,
            detail="a day that needs six and has two on it is the gap this "
                   "page exists to show")

    s.section("A shift is for the wedding, or it is not")
    a = _person(conn, "A")
    b = _person(conn, "B")
    sh_a = _shift(conn, a["id"], day)
    _shift(conn, b["id"], day)
    conn.close()
    conn = db()
    with m.app.test_request_context("/"):
        st = m.event_staffing(conn, e["id"])
    conn.close()
    s.check("nobody is on the event yet", not st["assigned"],
            detail="two people are rostered that day; neither is for the "
                   "wedding until somebody says so")
    s.check("but they are shown as the people who could be",
            len(st["elsewhere"]) >= 2, detail=f"{len(st['elsewhere'])}")
    oc.post(f"/admin/events/{e['id']}/staffing/assign",
            data={"shift_id": str(sh_a["id"])}, follow_redirects=True)
    conn = db()
    with m.app.test_request_context("/"):
        st = m.event_staffing(conn, e["id"])
    conn.close()
    s.check("now one is", len(st["assigned"]) == 1, detail=f"{len(st['assigned'])}")
    s.check("and the other is not counted as cover for it",
            b["name"] not in [x["who"] for x in st["assigned"]]
            and b["name"] in [x["who"] for x in st["elsewhere"]],
            detail=f"on the event: {[x['who'] for x in st['assigned']]} — "
                   "being in the building is not being on the wedding")
    s.check("short by the worst day, not the total",
            st["short"] == 6, detail=f"{st['short']} — one person on a two-day "
                                     "event has a day with nobody on it")

    s.section("A shift in another week cannot be for it")
    conn = db()
    far = _shift(conn, b["id"], day + timedelta(days=40))
    conn.close()
    page = oc.post(f"/admin/events/{e['id']}/staffing/assign",
                   data={"shift_id": str(far["id"])},
                   follow_redirects=True).get_data(as_text=True)
    conn = db()
    still = conn.execute("SELECT event_id FROM shifts WHERE id = ?",
                         (far["id"],)).fetchone()["event_id"]
    conn.close()
    s.check("it is refused", still is None, detail=f"{still}")
    s.check("and says why", "not on one of the event" in page,
            detail="a shift in another week attached to a wedding is a rota "
                   "nobody can read")

    s.section("A shift can be taken off again")
    oc.post(f"/admin/events/{e['id']}/staffing/assign",
            data={"shift_id": str(sh_a["id"]), "off": "1"}, follow_redirects=True)
    conn = db()
    with m.app.test_request_context("/"):
        st = m.event_staffing(conn, e["id"])
    conn.close()
    s.check("and it is off", not st["assigned"], detail=f"{len(st['assigned'])}")

    s.section("Somebody deactivated does not count as cover")
    conn = db()
    gone = _person(conn, "GONE")
    sh_gone = _shift(conn, gone["id"], day)
    conn.execute("UPDATE shifts SET event_id = ? WHERE id = ?", (e["id"], sh_gone["id"]))
    conn.execute("UPDATE users SET status = 'inactive' WHERE id = ?", (gone["id"],))
    conn.commit()
    with m.app.test_request_context("/"):
        st = m.event_staffing(conn, e["id"])
    conn.close()
    s.check("their leftover shift is not counted", not st["assigned"],
            detail="deactivating somebody does not delete their future "
                   "shifts, and one left behind counted as cover once before")

    s.section("What the suppliers cost")
    conn = db()
    conn.execute(
        """INSERT INTO vendors (name, contact_person, created_at)
           VALUES (?, 'A Florist', ?)""",
        (f"{TAG} Flowers", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    vendor = conn.execute("SELECT * FROM vendors WHERE name = ?",
                          (f"{TAG} Flowers",)).fetchone()
    for name, kind in ((f"{TAG} Flowers", "flowers"), (f"{TAG} Band", "music")):
        conn.execute(
            """INSERT INTO event_suppliers (event_id, name, kind, created_at)
               VALUES (?, ?, ?, ?)""",
            (e["id"], name, kind, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    sups = conn.execute(
        "SELECT * FROM event_suppliers WHERE event_id = ? ORDER BY name",
        (e["id"],)).fetchall()
    conn.close()
    band = [x for x in sups if "Band" in x["name"]][0]
    flowers = [x for x in sups if "Flowers" in x["name"]][0]

    oc.post(f"/admin/events/supplier/{flowers['id']}/cost",
            data={"cost": "1800", "vendor_id": str(vendor["id"]),
                  "invoice_ref": "F-2026-11"}, follow_redirects=True)
    conn = db()
    with m.app.test_request_context("/"):
        rd = m.event_supplier_readiness(conn, e["id"])
    conn.close()
    s.check("the cost is on the event", abs(rd["cost"] - 1800) < 0.01,
            detail=f"{rd['cost']}")
    s.check("and it counts as unpaid until it is paid",
            abs(rd["unpaid"] - 1800) < 0.01, detail=f"{rd['unpaid']}")
    # NAMED, NOT COUNTED. A total that quietly omits the band is worse.
    s.check("the supplier with no cost is named, not treated as nothing",
            rd["unpriced"] == [band["name"]], detail=f"{rd['unpriced']}")
    oc.post(f"/admin/events/supplier/{flowers['id']}/cost",
            data={"cost": "1800", "vendor_id": str(vendor["id"]), "paid": "1"},
            follow_redirects=True)
    conn = db()
    with m.app.test_request_context("/"):
        rd = m.event_supplier_readiness(conn, e["id"])
    conn.close()
    s.check("marking it paid clears the unpaid figure", rd["unpaid"] == 0,
            detail=f"{rd['unpaid']}")

    s.section("The papers they must hold")
    s.check("a supplier not on the list is named as such",
            band["name"] in rd["not_on_the_list"],
            detail=f"{rd['not_on_the_list']} — their papers cannot be recorded "
                   "against nobody")
    s.check("and the linked one is short of its papers",
            f"{TAG} Flowers" in rd["papers_missing"],
            detail=f"{rd['papers_missing']} — public liability is asked of "
                   "anybody working on the house")
    oc.post(f"/admin/vendors/{vendor['id']}/document",
            data={"kind": "public_liability",
                  "expires_on": (day + timedelta(days=200)).isoformat(),
                  "reference": "PL-99"}, follow_redirects=True)
    conn = db()
    with m.app.test_request_context("/"):
        rd = m.event_supplier_readiness(conn, e["id"])
    conn.close()
    s.check("recording it clears them", f"{TAG} Flowers" not in rd["papers_missing"],
            detail=f"{rd['papers_missing']}")

    s.section("Judged against the day they work, not against today")
    conn = db()
    conn.execute(
        """UPDATE vendor_documents SET expires_on = ? WHERE vendor_id = ?""",
        ((day - timedelta(days=7)).isoformat(), vendor["id"]))
    conn.commit()
    with m.app.test_request_context("/"):
        rd = m.event_supplier_readiness(conn, e["id"])
        today_view = m.vendor_paper_state(conn, vendor["id"])
    conn.close()
    s.check("a certificate expiring before the wedding is missing for it",
            f"{TAG} Flowers" in rd["papers_missing"],
            detail="finding that out in March is much cheaper than on the "
                   "morning")
    s.check("even though it is valid today", today_view["ok"],
            detail="which is exactly why the day matters and today does not")

    s.section("The attestation is required by the amount, not by the supplier")
    conn = db()
    conn.execute("UPDATE vendor_documents SET expires_on = ? WHERE vendor_id = ?",
                 ((day + timedelta(days=200)).isoformat(), vendor["id"]))
    conn.commit()
    with m.app.test_request_context("/"):
        small = m.vendor_paper_state(conn, vendor["id"], on_day=day, contract_value=1800)
        large = m.vendor_paper_state(conn, vendor["id"], on_day=day, contract_value=6000)
    conn.close()
    s.check("under the threshold it is not asked for",
            "attestation_vigilance" not in small["required"],
            detail=f"{small['required']}")
    s.check("at or over it, it is",
            "attestation_vigilance" in large["required"],
            detail=f"{large['required']} — the obligation is the château's, "
                   "for a contract at or over the statutory figure")
    s.check("and the supplier reads as short of it until it is recorded",
            not large["ok"] and "attestation_vigilance" in large["missing"],
            detail=f"{large['missing']}")

    s.section("The pages")
    sheet = oc.get(f"/admin/events/{e['id']}/run-sheet").get_data(as_text=True)
    s.check("the run sheet says who is on the event", "On the event itself" in sheet)
    s.check("and what a supplier costs", "1800.00" in sheet.replace(",", ""))
    s.check("and whether their papers are in order",
            "papers in order" in sheet or "not on the list" in sheet)
    # NAMED AT THE TOP, not only badged per row. A table of eleven suppliers
    # on the morning of a wedding is not read carefully.
    s.check("and the ones with a problem are named at the top",
            band["name"] in sheet.split("On the event itself")[0],
            detail="a badge in the eleventh row is a badge nobody sees")
    conn = db()
    docs = conn.execute("SELECT * FROM vendor_documents WHERE vendor_id = ?",
                        (vendor["id"],)).fetchall()
    conn.close()
    oc.post(f"/admin/vendors/document/{docs[0]['id']}/delete", follow_redirects=True)
    conn = db()
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM vendor_documents WHERE id = ?",
        (docs[0]["id"],)).fetchone()["c"]
    with m.app.test_request_context("/"):
        after = m.event_supplier_readiness(conn, e["id"])
    conn.close()
    s.check("a paper can be removed", left == 0, detail=f"{left}")
    s.check("and whatever required it reads as short again",
            f"{TAG} Flowers" in after["papers_missing"],
            detail=f"{after['papers_missing']}")

    scorecard = oc.get("/management/suppliers-scorecard").get_data(as_text=True)
    s.check("the supplier list carries their papers",
            "public liability" in scorecard.lower())
    s.check("and offers the attestation as something to record",
            'value="attestation_vigilance"' in scorecard,
            detail="the one paper nobody would think to ask for, and the one "
                   "the château rather than the supplier is liable for")

    s.section("Guards")
    s.check("an employee cannot set how many the day needs",
            ec.post(f"/admin/events/{e['id']}/staffing",
                    data={"staff_needed": "1"}).status_code in (302, 403))
    before = (_row(e["id"])["staff_needed"] or 0)
    ec.post(f"/admin/events/{e['id']}/staffing", data={"staff_needed": "99"})
    s.check("and it really does not change",
            (_row(e["id"])["staff_needed"] or 0) == before,
            detail="read back, because a refusal and a save are both a 302")
    s.check("nor record a supplier's papers",
            ec.post(f"/admin/vendors/{vendor['id']}/document",
                    data={"kind": "public_liability"}).status_code in (302, 403))
    s.check("an unknown supplier is a 404",
            oc.post("/admin/events/supplier/999999/cost",
                    data={"cost": "1"}).status_code == 404)

    _cleanup()
    return s
