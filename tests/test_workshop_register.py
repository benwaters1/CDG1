"""The workshop register: who is coming, what each owes, and what to do.

The owner asked whether it was easy to see who is on each workshop, and it was
not. The registrations page listed every registration there had ever been,
oldest session first, behind one status dropdown that quietly forgot which
session you were on. Nobody could be added by hand, moved to another date or
have a misspelt name put right, and a cancelled registration lost its refund
form at exactly the moment a refund is decided.

So this is the register: it opens on what is current with what is over under
History, it searches and counts, it prints and exports the view on screen,
and it takes, moves and corrects people -- each through the same arithmetic
the booking itself uses.
"""
import csv
import io
import re
import html as _html
from datetime import timedelta

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZWR"


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM workshop_bookings WHERE reference_code LIKE ? OR guest_name LIKE ?)"
    for table in ("workshop_transactions", "workshop_booking_guests", "workshop_messages"):
        conn.execute(f"DELETE FROM {table} WHERE workshop_booking_id IN {ids}",
                     (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM workshop_bookings WHERE reference_code LIKE ? OR guest_name LIKE ?",
                 (TAG + "%", TAG + "%"))
    conn.execute("DELETE FROM workshop_sessions WHERE notes LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM workshops WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM email_outbox WHERE to_address LIKE ?", (f"{TAG.lower()}%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (f"%{TAG}%",))
    conn.commit()
    conn.close()


def _workshop(name="Atelier", price=2000.0):
    conn = db()
    now = _harness.datetime_now()
    title = f"{TAG} {name}"
    if not conn.execute("SELECT 1 FROM workshops WHERE title = ?", (title,)).fetchone():
        conn.execute(
            """INSERT INTO workshops (title, description, price_per_person, default_capacity,
               active, sort_order, created_at, deposit_percent)
               VALUES (?, '', ?, 10, 1, 93, ?, 30)""", (title, price, now))
        conn.commit()
    wid = conn.execute("SELECT id FROM workshops WHERE title = ?", (title,)).fetchone()["id"]
    conn.close()
    return wid


def _session(label, starts_in, *, wid=None, days=4, capacity=10):
    conn = db()
    start = house_today() + timedelta(days=starts_in)
    conn.execute(
        """INSERT INTO workshop_sessions (workshop_id, start_date, end_date, capacity, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (wid or _workshop(), start.isoformat(), (start + timedelta(days=days)).isoformat(),
         capacity, f"{TAG} {label}", _harness.datetime_now()))
    conn.commit()
    sid = conn.execute("SELECT id FROM workshop_sessions WHERE notes = ?",
                       (f"{TAG} {label}",)).fetchone()["id"]
    conn.close()
    return sid


def _reg(ref, sid, *, status="confirmed", party=1, total=2000.0, paid=600.0,
         phone=None, deposit_paid=True, due_in=None):
    conn = db()
    now = _harness.datetime_now()
    start = conn.execute("SELECT start_date FROM workshop_sessions WHERE id = ?",
                         (sid,)).fetchone()["start_date"]
    due = (m.parse_date(start) - timedelta(days=30)).isoformat() if due_in is None else \
        (house_today() + timedelta(days=due_in)).isoformat()
    conn.execute(
        """INSERT INTO workshop_bookings (session_id, guest_name, guest_email, guest_phone,
           party_size, status, reference_code, manage_token, created_at, total_price,
           deposit_amount, balance_amount, deposit_paid_at, balance_due_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, f"{TAG} {ref}", f"{TAG.lower()}{ref.lower()}@example.invalid", phone, party,
         status, f"{TAG}{ref}", f"tok{TAG}{ref}", now, total, round(total * 0.3, 2),
         round(total * 0.7, 2), now if deposit_paid else None, due))
    conn.commit()
    bid = conn.execute("SELECT id FROM workshop_bookings WHERE reference_code = ?",
                       (f"{TAG}{ref}",)).fetchone()["id"]
    conn.execute("INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name, is_lead, "
                 "created_at) VALUES (?, ?, 1, ?)", (bid, f"{TAG} {ref}", now))
    if paid:
        m.add_workshop_transaction(conn, bid, "payment", "Deposit", paid, method="stripe")
    conn.commit()
    conn.close()
    return bid


def _row(bid):
    conn = db()
    row = conn.execute("SELECT * FROM workshop_bookings WHERE id = ?", (bid,)).fetchone()
    conn.close()
    return row


def _owed(bid):
    conn = db()
    due = m.workshop_balance_due(conn, bid)[0]
    conn.close()
    return due


def _shown(page):
    found = re.search(r"Showing (\d+) of (\d+)", page)
    return (int(found.group(1)), int(found.group(2))) if found else None


def run():
    s = Suite("The workshop register")
    oc, ec, _owner, _emp = clients()
    _cleanup()

    wid = _workshop()
    past = _session("Past", -40)
    now_on = _session("Now", -1, days=4)
    soon = _session("Soon", 60)
    later = _session("Later", 120)
    other_wid = _workshop("Other")
    elsewhere = _session("Elsewhere", 90, wid=other_wid)
    ids = {
        "Gone": _reg("Gone", past),
        "Here": _reg("Here", now_on, paid=2000.0),
        "Coming": _reg("Coming", soon, phone="+33612345678"),
        "Owing": _reg("Owing", soon, paid=1100.0),
        "Unpaid": _reg("Unpaid", soon, paid=0, deposit_paid=False),
        "Cancelled": _reg("Cancelled", soon, status="cancelled"),
        "Wait": _reg("Wait", soon, status="pending", paid=0, deposit_paid=False),
    }
    conn = db()
    conn.execute("INSERT INTO workshop_booking_guests (workshop_booking_id, guest_name, is_lead, "
                 "created_at) VALUES (?, ?, 0, ?)",
                 (ids["Coming"], f"{TAG} Plusone Marguerite", _harness.datetime_now()))
    conn.commit()
    conn.close()

    s.section("It opens on what is current, with what is over under History")
    page = oc.get("/admin/workshops/registrations").get_data(as_text=True)
    s.check("a session still to come is there", f"{TAG} Coming" in page)
    s.check("so is one running now", f"{TAG} Here" in page)
    s.check("one that is over is not", f"{TAG} Gone" not in page,
            detail="every registration there had ever been, oldest first")
    s.check("and it says what is out of view", _shown(page) is not None
            and _shown(page)[0] < _shown(page)[1], detail=f"{_shown(page)}")
    hist = oc.get("/admin/workshops/registrations?when=History").get_data(as_text=True)
    s.check("History has the one that is over", f"{TAG} Gone" in hist and f"{TAG} Coming" not in hist)
    every = oc.get("/admin/workshops/registrations?when=all").get_data(as_text=True)
    s.check("and All has both", f"{TAG} Gone" in every and f"{TAG} Coming" in every)

    s.section("An old link to one session opens it, over or not")
    page = oc.get(f"/admin/workshops/registrations?session_id={past}").get_data(as_text=True)
    s.check("the past session's registration is shown", f"{TAG} Gone" in page)
    s.check("and nobody else's", f"{TAG} Coming" not in page)
    page = oc.get(f"/admin/workshops/registrations?session_id={soon}&status=pending").get_data(as_text=True)
    s.check("a status on top of it keeps the session",
            f"{TAG} Wait" in page and f"{TAG} Coming" not in page and f"{TAG} Here" not in page,
            detail="choosing a status used to widen back to every session")

    s.section("Search finds people however they were written down")
    page = oc.get("/admin/workshops/registrations?q=612345678").get_data(as_text=True)
    s.check("by telephone number", f"{TAG} Coming" in page and f"{TAG} Owing" not in page)
    page = oc.get("/admin/workshops/registrations?q=marguerite").get_data(as_text=True)
    s.check("by the name of somebody else in their party", f"{TAG} Coming" in page)

    s.section("Money is the ledger's, and the chips count what they give")
    page = oc.get(f"/admin/workshops/registrations?session_id={soon}").get_data(as_text=True)
    chip = re.search(r'<a href="([^"]+)"\s*class="chip[^"]*">Balance owed '
                     r'<span class="chip-n">(\d+)</span>', page)
    got = oc.get(_html.unescape(chip.group(1))).get_data(as_text=True) if chip else ""
    s.check("the Balance owed chip gives what it counts",
            chip is not None and _shown(got) is not None and _shown(got)[0] == int(chip.group(2)),
            detail=f"chip {chip.group(2) if chip else None}, shown {_shown(got)}")
    s.check("a part payment counts as part paid", f"{TAG} Owing" in got and f"{TAG} Here" not in got)
    page = oc.get(f"/admin/workshops/registrations?session_id={soon}&money=Deposit+unpaid").get_data(as_text=True)
    s.check("and a deposit not yet paid is its own chip", f"{TAG} Unpaid" in page
            and f"{TAG} Owing" not in page)

    s.section("One session has a head: places, money, and what is done from it")
    page = oc.get(f"/admin/workshops/registrations?session_id={soon}").get_data(as_text=True)
    s.check("how many places are left", "Places left" in page and "6 of 10" in page,
            detail="10 places, four taken by the confirmed and the pending")
    head_owed = re.search(r'Still owed</span><strong>€([\d,.]+)</strong>', page)
    s.check("what it is still owed", head_owed is not None and head_owed.group(1) == "6,300.00",
            detail=f"{head_owed.group(1) if head_owed else None}: Coming 1400 + Owing 900 + "
                   "Unpaid 2000 + Wait 2000, and the cancelled one is not")
    mailto = re.search(r'href="mailto:\?bcc=([^"&]+)', page)
    addresses = set(mailto.group(1).split(",")) if mailto else set()
    s.check("everyone coming can be written to at once, in blind copy",
            f"{TAG.lower()}coming@example.invalid" in addresses
            and f"{TAG.lower()}cancelled@example.invalid" not in addresses,
            detail=f"{sorted(addresses)}")
    s.check("it links to its printable register",
            f"/admin/workshops/sessions/{soon}/register" in page)

    s.section("The register prints")
    r = oc.get(f"/admin/workshops/sessions/{soon}/register")
    page = r.get_data(as_text=True)
    s.check("it answers", r.status_code == 200, detail=f"HTTP {r.status_code}")
    s.check("with who is coming, confirmed and pending",
            f"{TAG} Coming" in page and f"{TAG} Wait" in page and f"{TAG} Cancelled" not in page)
    s.check("and what each still owes", "€900.00" in page)
    s.check("in a table that cannot drag a phone sideways", 'class="table-wrap"' in page)

    s.section("Export this view is this view")
    r = oc.get("/admin/workshops/registrations/export.csv?when=History")
    rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    mine = [x for x in rows if x["reference_code"].startswith(TAG)]
    s.check("History exports the session that is over, and only it",
            [x["reference_code"] for x in mine] == [f"{TAG}Gone"], detail=f"{[x['reference_code'] for x in mine]}")
    r = oc.get(f"/admin/workshops/registrations/export.csv?session_id={soon}")
    rows = {x["reference_code"]: x for x in csv.DictReader(io.StringIO(r.get_data(as_text=True)))}
    s.check("with what was paid and what is owed, from the ledger",
            rows.get(f"{TAG}Owing", {}).get("paid") == "1100.00"
            and rows.get(f"{TAG}Owing", {}).get("owed") == "900.00",
            detail=f"{rows.get(f'{TAG}Owing')}")

    s.section("Somebody booked on the telephone is registered by hand")
    r = oc.post(f"/admin/workshops/sessions/{later}/add",
                data={"guest_name": f"{TAG} Phoned", "guest_email": f"{TAG.lower()}phoned@example.invalid",
                      "guest_phone": "+33 6 11 22 33 44", "party_size": "2",
                      "occupancy_type": "double", "dietary_notes": "No shellfish"},
                follow_redirects=True)
    conn = db()
    added = conn.execute("SELECT * FROM workshop_bookings WHERE guest_name = ?",
                         (f"{TAG} Phoned",)).fetchone()
    letter = conn.execute("SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ?",
                          (f"{TAG.lower()}phoned@example.invalid",)).fetchone()["c"]
    audited = conn.execute("SELECT COUNT(*) AS c FROM audit_log WHERE action = "
                           "'workshop_registration_added'").fetchone()["c"]
    conn.close()
    s.check("they are registered and confirmed", added is not None and added["status"] == "confirmed",
            detail=f"{dict(added) if added else flashes(r)}")
    s.check("priced the way the public form prices them",
            added is not None and added["total_price"] == 4000.0 and added["deposit_amount"] == 1200.0,
            detail=f"{added['total_price'] if added else None}, {added['deposit_amount'] if added else None}")
    s.check("and sent the letter with the link to pay", letter >= 1, detail=f"{letter} emails")
    s.check("on the record", audited >= 1)
    tiny = _session("Tiny", 150, capacity=1)
    r = oc.post(f"/admin/workshops/sessions/{tiny}/add",
                data={"guest_name": f"{TAG} Crowd", "guest_email": f"{TAG.lower()}crowd@example.invalid",
                      "party_size": "3", "occupancy_type": "double"}, follow_redirects=True)
    conn = db()
    crowd = conn.execute("SELECT COUNT(*) AS c FROM workshop_bookings WHERE guest_name = ?",
                         (f"{TAG} Crowd",)).fetchone()["c"]
    conn.close()
    s.check("a session without the places refuses, and says how many there are",
            crowd == 0 and any("place" in f for f in flashes(r)), detail=f"{flashes(r)}")
    oc.post(f"/admin/workshops/sessions/{tiny}/add",
            data={"guest_name": f"{TAG} Crowd", "guest_email": f"{TAG.lower()}crowd@example.invalid",
                  "party_size": "3", "occupancy_type": "double", "over_capacity": "on"})
    conn = db()
    crowd = conn.execute("SELECT COUNT(*) AS c FROM workshop_bookings WHERE guest_name = ?",
                         (f"{TAG} Crowd",)).fetchone()["c"]
    conn.close()
    s.check("unless the owner says over capacity", crowd == 1)

    s.section("Moving somebody to another date")
    conn = db()
    conn.execute("UPDATE workshop_bookings SET balance_due_noticed_at = ?, balance_reminder_sent_at = ?, "
                 "assigned_room_id = (SELECT id FROM rooms LIMIT 1) WHERE id = ?",
                 (_harness.datetime_now(), _harness.datetime_now(), ids["Owing"]))
    conn.execute(
        "INSERT INTO tasks (title, notes, priority, due_date, status, origin, created_at) "
        "VALUES (?, 'asked', 'normal', ?, 'open', 'guest_request', ?)",
        (f"Workshop date change request — {TAG}Owing", house_today().isoformat(),
         _harness.datetime_now()))
    conn.commit()
    conn.close()
    r = oc.post(f"/admin/workshops/registrations/{ids['Owing']}/move",
                data={"new_session_id": str(later), "tell_guest": "on"}, follow_redirects=True)
    moved = _row(ids["Owing"])
    conn = db()
    later_start = conn.execute("SELECT start_date FROM workshop_sessions WHERE id = ?",
                               (later,)).fetchone()["start_date"]
    request_task = conn.execute("SELECT status FROM tasks WHERE title = ?",
                                (f"Workshop date change request — {TAG}Owing",)).fetchone()
    told = conn.execute("SELECT COUNT(*) AS c FROM email_outbox WHERE to_address = ? "
                        "AND subject LIKE 'Your new dates%'",
                        (f"{TAG.lower()}owing@example.invalid",)).fetchone()["c"]
    conn.close()
    s.check("they are on the new session", moved["session_id"] == later, detail=f"{flashes(r)}")
    s.check("the balance falls due thirty days before the new date",
            moved["balance_due_date"] == (m.parse_date(later_start) - timedelta(days=30)).isoformat(),
            detail=f"{moved['balance_due_date']}")
    s.check("so the reminder and the owner's notice are due again",
            not moved["balance_due_noticed_at"] and not moved["balance_reminder_sent_at"])
    s.check("the deposit they paid stays paid, and so does what else they paid",
            moved["deposit_paid_at"] and _owed(ids["Owing"]) == 900.0, detail=f"{_owed(ids['Owing'])}")
    s.check("the room they had on the old dates is let go", moved["assigned_room_id"] is None)
    s.check("their request to move is answered", request_task is not None
            and request_task["status"] == "done", detail=f"{dict(request_task) if request_task else None}")
    s.check("and they are told their new dates", told == 1, detail=f"{told}")
    r = oc.post(f"/admin/workshops/registrations/{ids['Owing']}/move",
                data={"new_session_id": str(elsewhere)}, follow_redirects=True)
    s.check("not to a different atelier, which is a different price",
            _row(ids["Owing"])["session_id"] == later, detail=f"{flashes(r)}")
    oc.post(f"/admin/workshops/registrations/{ids['Cancelled']}/move",
            data={"new_session_id": str(later)})
    s.check("and not a cancelled registration", _row(ids["Cancelled"])["session_id"] == soon)

    s.section("A paid deposit moved inside the thirty days is due now, not never")
    # compute_workshop_payment_terms answers "all of it now" by giving no due
    # date, which left a balance nothing would ever ask for.
    close = _session("Close", 10)
    oc.post(f"/admin/workshops/registrations/{ids['Coming']}/move",
            data={"new_session_id": str(close)})
    close_row = _row(ids["Coming"])
    s.check("the balance is due today", close_row["session_id"] == close
            and close_row["balance_due_date"] == house_today().isoformat(),
            detail=f"session {close_row['session_id']}, due {close_row['balance_due_date']}")

    s.section("Putting their details right")
    r = oc.post(f"/admin/workshops/registrations/{ids['Wait']}/edit",
                data={"guest_name": f"{TAG} Waite", "guest_email": f"{TAG.lower()}waite@example.invalid",
                      "guest_phone": "+33 6 99 88 77 66", "party_size": "2",
                      "dietary_notes": "Vegetarian"}, follow_redirects=True)
    edited = _row(ids["Wait"])
    conn = db()
    lead = conn.execute("SELECT guest_name FROM workshop_booking_guests WHERE workshop_booking_id = ? "
                        "AND is_lead = 1", (ids["Wait"],)).fetchone()
    trail = conn.execute("SELECT details FROM audit_log WHERE action = 'workshop_registration_edited' "
                         "AND target = ?", (f"{TAG}Wait",)).fetchone()
    conn.close()
    s.check("the name and email are saved", edited["guest_name"] == f"{TAG} Waite"
            and edited["guest_email"] == f"{TAG.lower()}waite@example.invalid", detail=f"{flashes(r)}")
    s.check("the lead of the party is renamed with them", lead and lead["guest_name"] == f"{TAG} Waite")
    s.check("a bigger party is repriced", edited["party_size"] == 2 and edited["total_price"] == 4000.0,
            detail=f"{edited['party_size']}, {edited['total_price']}")
    s.check("and the record says what changed",
            trail is not None and "guest_name" in trail["details"] and "party_size" in trail["details"],
            detail=f"{trail['details'] if trail else None}")

    s.section("A new supplement on a place paid in full is owed again")
    oc.post(f"/admin/workshops/registrations/{ids['Here']}/occupancy",
            data={"occupancy_type": "solo", "single_supplement": "300"})
    s.check("it owes the supplement, and no longer reads as paid",
            _owed(ids["Here"]) == 300.0 and not _row(ids["Here"])["balance_paid_at"],
            detail=f"owed {_owed(ids['Here'])}, stamp {_row(ids['Here'])['balance_paid_at']}")

    s.section("A cancelled registration keeps its refund form")
    page = oc.get(f"/admin/workshops/registrations?session_id={soon}&state=Cancelled").get_data(as_text=True)
    s.check("the refund form is there when money was paid",
            f"/admin/workshops/registrations/{ids['Cancelled']}/refund" in page,
            detail="calling a session off tells the owner to refund each one from here")

    s.section("None of it answers to staff")
    before = _row(ids["Unpaid"])
    ec.post(f"/admin/workshops/registrations/{ids['Unpaid']}/edit",
            data={"guest_name": "Changed by staff", "guest_email": "x@example.invalid"})
    ec.post(f"/admin/workshops/registrations/{ids['Unpaid']}/move", data={"new_session_id": str(later)})
    ec.post(f"/admin/workshops/sessions/{later}/add",
            data={"guest_name": f"{TAG} Staff", "guest_email": f"{TAG.lower()}staff@example.invalid",
                  "party_size": "1"})
    after = _row(ids["Unpaid"])
    conn = db()
    staffed = conn.execute("SELECT COUNT(*) AS c FROM workshop_bookings WHERE guest_name = ?",
                           (f"{TAG} Staff",)).fetchone()["c"]
    conn.close()
    s.check("staff cannot edit, move or add", after["guest_name"] == before["guest_name"]
            and after["session_id"] == before["session_id"] and staffed == 0)
    s.check("or open the printable register",
            ec.get(f"/admin/workshops/sessions/{soon}/register").status_code != 200)

    s.section("The workshops page says who is coming, and what each session is owed")
    page = oc.get("/admin/workshops").get_data(as_text=True)
    s.check("each session links to its register with how many are booked",
            re.search(r"Who is coming \(\d+\)", page) is not None)

    s.section("A chip's default is where a list opens, not a filter somebody chose")
    rows = [{"n": 1, "when": "Current"}, {"n": 2, "when": "History"}, {"n": 3, "when": "Current"}]
    facets = [m.facet("when", "When", lambda r: r["when"], default="Current")]
    opened = m.list_view(rows, {}, facets=facets)
    s.check("it opens on the default", [r["n"] for r in opened["rows"]] == [1, 3],
            detail=f"{[r['n'] for r in opened['rows']]}")
    s.check("which is not offered as a filter to clear, but says what it hides",
            not opened["filtered"] and opened["defaulted"])
    everything = m.list_view(rows, {"when": m.LIST_ALL}, facets=facets)
    s.check("All is everything", len(everything["rows"]) == 3 and everything["filtered"])
    chosen = m.list_view(rows, {"when": "Current"}, facets=facets)
    s.check("and choosing the default is the same as arriving on it",
            not chosen["filtered"] and len(chosen["rows"]) == 2)
    page = oc.get("/admin/workshops/registrations").get_data(as_text=True)
    every_chip = re.search(r'<span class="facet-label">When</span>\s*<a href="([^"]+)"', page)
    got = oc.get(_html.unescape(every_chip.group(1))).get_data(as_text=True) if every_chip else ""
    s.check("the All chip on the page shows what is over too",
            f"{TAG} Gone" in got and f"{TAG} Coming" in got,
            detail=f"{every_chip.group(1) if every_chip else 'no When row found'}")

    s.section("A saved view keeps the new chips")
    kept = m.saved_view_query({"when": "History", "session": "X", "money": "Balance owed"})
    s.check("when, session and money survive being saved",
            "when=History" in kept and "session=X" in kept and "money=Balance+owed" in kept,
            detail=kept)

    _cleanup()
    return s
