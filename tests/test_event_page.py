"""Every event has a page of its own, and the page says what to do next.

The owner, 19 October 2026: the event manager was "impossible to use". Every
event was a card on one long list; its quote, guest list, run sheet and money
were each a page reached from a button on the card; nothing said which of them
mattered now; and once an event was typed in, its date, its numbers and who to
ring could not be changed at all.

Held here:

  - the page opens for the owner, is refused to staff, and 404s for nothing;
  - it says where the event stands and what to do next, from the facts, so
    doing the thing takes it off;
  - its details can be changed, are checked, are recorded in the audit log,
    and a moved date names what else is on the new one;
  - the stage buttons move it on without wiping the private note;
  - a quote sent moves it to Quoted, and a quote accepted asks for it to be
    confirmed;
  - the list is one line an event, each opening its page, saying what is next;
  - money recorded from the page comes back to the page.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes, house_today
import _harness

m = _harness.m
TAG = "ZZEVP"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, to_address, subject, body, *a, **kw):
        self.calls.append((to_address, subject))
        return True


def _cleanup():
    conn = db()
    ids = "(SELECT id FROM event_inquiries WHERE contact_name LIKE ?)"
    for table in ("event_quotes", "event_holds", "event_payments", "event_instalments"):
        conn.execute(f"DELETE FROM {table} WHERE event_id IN {ids}", (TAG + "%",))
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _event(key, *, status="new", day=None, price=None, deposit=None, paid=0.0,
           due=None, waited=0, note=None, guests=60):
    conn = db()
    created = (datetime.now(timezone.utc) - timedelta(days=waited)).isoformat()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
           contact_email, contact_phone, preferred_date, guest_count, status, quoted_price,
           deposit_amount, amount_paid, balance_due_date, owner_note, created_at)
           VALUES (?, ?, 'wedding', ?, ?, '0600000000', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"{TAG}-{key}", f"tok{TAG}{key}".lower(), f"{TAG} {key}",
         f"{TAG.lower()}.{key.lower()}@example.invalid",
         day.isoformat() if day else None, guests, status, price, deposit, paid,
         due.isoformat() if due else None, note, created))
    conn.commit()
    row = conn.execute("SELECT * FROM event_inquiries WHERE reference_code = ?",
                       (f"{TAG}-{key}",)).fetchone()
    conn.close()
    return row


def _row(event_id):
    conn = db()
    try:
        return conn.execute("SELECT * FROM event_inquiries WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()


def _quiet_day(conn, after):
    """A day at least `after` days ahead with no stay, sitting or event on it."""
    day = house_today() + timedelta(days=after)
    for _ in range(400):
        iso = day.isoformat()
        busy = conn.execute(
            """SELECT 1 FROM bookings WHERE status IN ('pending','confirmed')
                AND arrival_date <= ? AND departure_date > ?""", (iso, iso)).fetchone() \
            or m.sessions_holding_the_house(conn, iso, iso) \
            or conn.execute("""SELECT 1 FROM event_inquiries WHERE status = 'confirmed'
                               AND preferred_date = ?""", (iso,)).fetchone()
        if not busy:
            return day
        day += timedelta(days=1)
    return day


def run():
    s = Suite("every event has a page of its own, saying what to do next")
    oc, ec, _owner, _emp = clients()
    _cleanup()
    sent = Recorder()
    real_send = m.send_email
    m.send_email = sent
    try:
        conn = db()
        far = _quiet_day(conn, 120)
        conn.close()

        s.section("The page")
        fresh = _event("New", waited=3, day=far)
        r = oc.get(f"/admin/events/{fresh['id']}")
        page = r.get_data(as_text=True)
        s.check("it opens for the owner", r.status_code == 200, r)
        s.check("staff are turned away", ec.get(f"/admin/events/{fresh['id']}").status_code in (302, 403))
        s.check("an event that is not there is a 404", oc.get("/admin/events/999999").status_code == 404)
        s.check("it names the event, its date and its reference",
                f"{TAG} New" in page and fresh["reference_code"] in page
                and m.format_date_short(far.isoformat()) in page)
        s.check("it says where it stands: an enquiry, at step one",
                'aria-current="step"' in page and "Enquiry" in page)
        s.check("and what to do: reply, after three days of waiting",
                "Reply to them" in page and "waiting 3 days" in page)
        s.check("with the way to put a price to them",
                "Put a price to them" in page and f"/admin/events/{fresh['id']}/agreement" in page)
        s.check("it says nothing else is on the date", "Nothing else in the house." in page)
        s.check("and no run sheet or guest list before it is confirmed",
                "Run sheet" not in page and "Guest list" not in page)
        # 19 October 2026 review: every step's button says what it does.
        s.check("replying is a button that writes to them, with the reference",
                f"mailto:{fresh['contact_email']}?subject=" in page
                and fresh["reference_code"] in page[page.find("mailto:"):page.find("mailto:") + 200])
        s.check("no step's button is just 'Go'", ">Go</a>" not in page)
        s.check("the last stage is 'Took place', not 'Held' beside 'Hold the date'",
                "Took place" in page and ">Held<" not in page)

        s.section("Its details can be changed")
        moved_to = far + timedelta(days=7)
        r = oc.post(f"/admin/events/{fresh['id']}/details", data={
            "event_type": "wedding", "contact_name": f"{TAG} New", "contact_email":
            f"{TAG.lower()}.new@example.invalid", "contact_phone": "0611111111",
            "preferred_date": moved_to.isoformat(), "end_date": (moved_to + timedelta(days=1)).isoformat(),
            "alternate_date": "", "guest_count": "95", "spaces": "The orangery",
            "message": "A long lunch.", "owner_note": "Ring after six."}, follow_redirects=True)
        after = _row(fresh["id"])
        s.check("the date, the numbers, the phone, the spaces and the note all change",
                after["preferred_date"] == moved_to.isoformat()
                and after["end_date"] == (moved_to + timedelta(days=1)).isoformat()
                and after["guest_count"] == 95 and after["contact_phone"] == "0611111111"
                and after["spaces"] == "The orangery" and after["owner_note"] == "Ring after six.",
                detail=str(dict(after)) + " " + str(flashes(r)))
        conn = db()
        logged = conn.execute(
            """SELECT details FROM audit_log WHERE action = 'event_details_changed' AND target = ?
                ORDER BY id DESC LIMIT 1""", (fresh["reference_code"],)).fetchone()
        conn.close()
        s.check("and what changed is in the audit log, by name",
                logged and "preferred_date" in logged[0] and "guest_count" in logged[0],
                detail=str(logged and logged[0]))
        for why, data, says in [
                ("a last day before the first", {"preferred_date": moved_to.isoformat(),
                                                 "end_date": (moved_to - timedelta(days=2)).isoformat()},
                 "before the first"),
                ("an email that is not one", {"contact_email": "not-an-address"}, "look right"),
                ("a kind of event the house does not run", {"event_type": "rodeo"}, "Choose an event type"),
                ("no name", {"contact_name": ""}, "name has to be on it"),
                ("guests that are not a number", {"guest_count": "lots"}, "whole number")]:
            base = {"event_type": "wedding", "contact_name": f"{TAG} New",
                    "contact_email": after["contact_email"], "preferred_date": after["preferred_date"],
                    "end_date": after["end_date"] or "", "guest_count": "95"}
            base.update(data)
            said = " ".join(flashes(oc.post(f"/admin/events/{fresh['id']}/details", data=base,
                                            follow_redirects=True)))
            s.check(f"refused: {why}", says in said and _row(fresh["id"])["guest_count"] == 95,
                    detail=said)
        same = {"event_type": "wedding", "contact_name": f"{TAG} New",
                "contact_email": after["contact_email"], "contact_phone": after["contact_phone"],
                "preferred_date": after["preferred_date"], "end_date": after["end_date"],
                "guest_count": "95", "spaces": "The orangery", "message": "A long lunch.",
                "owner_note": "Ring after six."}
        said = " ".join(flashes(oc.post(f"/admin/events/{fresh['id']}/details", data=same,
                                        follow_redirects=True)))
        s.check("saving nothing new says so", "Nothing had changed" in said, detail=said)

        s.section("Moving it on, without losing the note")
        r = oc.post(f"/admin/events/{fresh['id']}/update", data={"status": "contacted", "back": "event"})
        s.check("a stage button comes back to the event's page",
                r.status_code == 302 and r.headers["Location"].endswith(f"/admin/events/{fresh['id']}"),
                detail=r.headers.get("Location"))
        now = _row(fresh["id"])
        s.check("it is now replied to", now["status"] == "contacted")
        s.check("and the private note is still there", now["owner_note"] == "Ring after six.",
                detail=repr(now["owner_note"]))
        r = oc.post(f"/admin/events/{fresh['id']}/update", data={"status": "contacted"})
        s.check("from the list it goes back to the list",
                r.headers.get("Location", "").endswith("/admin/events"), detail=r.headers.get("Location"))

        s.section("A quote sent is the quoted stage; one accepted asks to be confirmed")
        r = oc.post(f"/admin/events/{fresh['id']}/quote", data={"quoted_price": "12000"},
                    follow_redirects=True)
        conn = db()
        quote = conn.execute("SELECT * FROM event_quotes WHERE event_id = ? ORDER BY id DESC",
                             (fresh["id"],)).fetchone()
        conn.close()
        page = oc.get(f"/admin/events/{fresh['id']}").get_data(as_text=True)
        s.check("a quote written and not sent says to send it", quote and "Send the quote" in page)
        oc.post(f"/admin/events/quote/{quote['id']}/send", follow_redirects=True)
        s.check("sending it moves the event to Quoted", _row(fresh["id"])["status"] == "quoted",
                detail=_row(fresh["id"])["status"])
        page = oc.get(f"/admin/events/{fresh['id']}").get_data(as_text=True)
        s.check("and the page says it is waiting for their answer", "Waiting for their answer" in page)
        anon = m.app.test_client()
        anon.post(f"/events/quote/{quote['token']}", data={"accepted_name": "Jo Couple"})
        page = oc.get(f"/admin/events/{fresh['id']}").get_data(as_text=True)
        s.check("once they accept, the first thing is to confirm it",
                "They accepted — confirm it" in page and "Jo Couple" in page)

        s.section("Confirming names what else is on the dates")
        conn = db()
        room = conn.execute("SELECT id, name FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
        conn.execute(
            """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
               arrival_date, departure_date, party_size, status, total_price, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 2, 'confirmed', 500, ?)""",
            (room["id"], f"{TAG}-STAY", f"tok{TAG}stay".lower(), f"{TAG} Overlap Guest",
             f"{TAG.lower()}@example.invalid", (moved_to - timedelta(days=1)).isoformat(),
             (moved_to + timedelta(days=1)).isoformat(), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        page = oc.get(f"/admin/events/{fresh['id']}").get_data(as_text=True)
        s.check("the page names the stay on its dates before anybody presses Confirm",
                f"{TAG} Overlap Guest" in page and room["name"] in page)
        said = " ".join(flashes(oc.post(f"/admin/events/{fresh['id']}/update",
                                        data={"status": "confirmed", "back": "event"},
                                        follow_redirects=True)))
        s.check("confirming says so by name", f"{TAG} Overlap Guest" in said, detail=said)
        s.check("and it is confirmed", _row(fresh["id"])["status"] == "confirmed")

        s.section("What is next, once it is confirmed")
        soon = _quiet_day(db(), 5)
        due = _event("Due", status="confirmed", day=soon, price=9000, deposit=3000,
                     paid=0, due=house_today() - timedelta(days=2))
        page = oc.get(f"/admin/events/{due['id']}").get_data(as_text=True)
        s.check("a deposit not in says to collect it", "Collect the deposit" in page)
        s.check("a balance past its day says it is late", "Collect the balance — it is late" in page)
        s.check("final numbers inside a fortnight are asked for", "Get their final numbers" in page)
        s.check("with a way to write to them and the run sheet",
                "Write to them" in page[page.find("Get their final numbers"):page.find("Get their final numbers") + 900])
        s.check("and the balance step's button says it records a payment",
                "Record a payment" in page and ">Go</a>" not in page)
        s.check("and the run sheet, guest list and margin are there",
                all(w in page for w in ("Run sheet", "Guest list", "What it made")))
        r = oc.post(f"/admin/events/{due['id']}/payment",
                    data={"amount": "3000", "method": "bank_transfer", "back": "event"})
        s.check("money recorded from the page comes back to the page",
                r.headers.get("Location", "").endswith(f"/admin/events/{due['id']}"),
                detail=r.headers.get("Location"))
        page = oc.get(f"/admin/events/{due['id']}").get_data(as_text=True)
        s.check("and the deposit step has gone", "Collect the deposit" not in page)
        s.check("with the payment listed", "What has come in" in page and "€3,000.00" in page)

        s.section("The list: one line an event, saying what is next")
        listed = oc.get("/admin/events", query_string={"q": TAG, "when": "all"}).get_data(as_text=True)
        s.check("every line opens its event",
                f'href="/admin/events/{fresh["id"]}"' in listed and f'href="/admin/events/{due["id"]}"' in listed)
        s.check("and says the next thing to do", "Collect the balance — it is late" in listed)
        chips = oc.get("/admin/events", query_string={"state": "Contacted", "when": "all"}).get_data(as_text=True)
        import re as _re
        chip = _re.search(r'<a[^>]*\?[^"]*state=Contacted"[^>]*class="chip[^"]*"[^>]*>([^<]*)<', chips) or             _re.search(r'<a href="[^"]*state=Contacted"\s*class="chip[^"]*">([^<]*)<', chips)
        s.check("its status chip says 'Replied to', as the rows do, and old links still filter",
                bool(chip) and chip.group(1).strip() == "Replied to", detail=chip.group(1) if chip else "no chip")
        s.check("there are no forms on the list but the add and the settings",
                listed.count("/payment\"") == 0 and "/update\"" not in listed)

        s.section("Adding one")
        r = oc.post("/admin/events/new", data={
            "event_type": "wedding", "contact_name": f"{TAG} Phoned", "status": "new",
            "preferred_date": far.isoformat()})
        made = db().execute("SELECT id FROM event_inquiries WHERE contact_name = ?",
                            (f"{TAG} Phoned",)).fetchone()
        s.check("goes straight to its page",
                made and r.headers.get("Location", "").endswith(f"/admin/events/{made['id']}"),
                detail=r.headers.get("Location"))
        page = oc.get(f"/admin/events?add=1&date={far.isoformat()}").get_data(as_text=True)
        s.check("and the calendar's Add opens the form with the day filled in",
                f'name="preferred_date" value="{far.isoformat()}"' in page
                and 'id="add-event-form" style="margin-bottom' in page)

        s.section("The calendar leads to the page")
        with m.app.test_request_context("/"):
            sheet = m.build_overview(db(), "day", soon)
        ours = [r for r in sheet["rows"] if r["kind"] == "event" and r["id"] == due["id"]]
        s.check("an event on the calendar opens its own page",
                ours and ours[0]["link"] == f"/admin/events/{due['id']}",
                detail=str(ours and ours[0]["link"]))
    finally:
        m.send_email = real_send
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
