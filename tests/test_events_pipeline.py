"""The events page says where the business stands, in money, and what fits each stage.

It counted enquiries -- one new, two confirmed -- and said nothing of what was
riding on them; every card offered a run sheet and a guest list, a brand-new
enquiry and a declined one alike; and nothing said how long somebody had been
waiting for a reply or that a balance had fallen due (owner, 18 October 2026,
"do the rest of the back office"). Every figure here is event_bill's.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPIPE"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _event(key, status, quote, paid, *, days_ahead=90, created_days_ago=0, due_days=None):
    t = m.house_today()
    conn = db()
    created = (datetime.now(timezone.utc) - timedelta(days=created_days_ago)).isoformat()
    conn.execute(
        """INSERT INTO event_inquiries (reference_code, manage_token, event_type, contact_name,
               contact_email, preferred_date, guest_count, status, quoted_price, amount_paid,
               created_at, balance_due_date)
           VALUES (?, ?, 'wedding', ?, ?, ?, 80, ?, ?, ?, ?, ?)""",
        (f"EVT-{TAG}{key}", f"tok{TAG}{key}", f"{TAG} {key}", f"zz.pipe.{key}@example.invalid",
         (t + timedelta(days=days_ahead)).isoformat(), status, quote, paid, created,
         (t + timedelta(days=due_days)).isoformat() if due_days is not None else None))
    conn.commit()
    eid = conn.execute("SELECT id FROM event_inquiries WHERE manage_token = ?",
                       (f"tok{TAG}{key}",)).fetchone()["id"]
    conn.close()
    return eid


def run():
    s = Suite("The events page in money, and by stage")
    _cleanup()
    oc, _ec, _o, _e = clients()
    try:
        _event("new", "new", None, 0, created_days_ago=3)
        _event("quoted", "quoted", 20000, 0)
        _event("late", "confirmed", 30000, 9000, due_days=-2)
        _event("ontime", "confirmed", 5000, 1000, due_days=20)
        _event("declined", "declined", None, 0)

        conn = db()
        rows = conn.execute("SELECT * FROM event_inquiries WHERE contact_name LIKE ?", (TAG + "%",)).fetchall()
        bills = {e["id"]: m.event_bill(conn, e["id"]) for e in rows}
        conn.close()
        p = m.events_pipeline(rows, bills)
        s.check("the quote still out is counted at its value",
                p["quotes_out"] == 1 and p["quotes_value"] == 20000, detail=str(p))
        s.check("the confirmed events at theirs", p["confirmed"] == 2 and p["confirmed_value"] == 35000)
        s.check("still to collect is what the bills say is owed", p["to_collect"] == 25000)
        s.check("and the one past its due date is counted", p["overdue"] == 1)
        s.check("the oldest new enquiry has waited three days", p["oldest_wait"] == 3)

        page = oc.get("/admin/events", query_string={"q": TAG}).get_data(as_text=True)
        s.check("the page shows the money tiles",
                "still out" in page and "Still to collect" in page and "past due" in page)
        s.check("a new enquiry says how long it has waited", "waiting 3 days for a reply" in page)

        def card(key):
            at = page.find(f"{TAG} {key}</strong>")
            nxt = page.find('class="expense-card"', at + 1)
            return page[at:nxt if nxt > 0 else len(page)]

        s.check("a new enquiry offers no run sheet or guest list",
                "Run sheet" not in card("new") and "Guest list" not in card("new"))
        s.check("a declined one neither", "Run sheet" not in card("declined"))
        s.check("a quote offers its agreement and nothing to run yet",
                "Agreement" in card("quoted") and "Run sheet" not in card("quoted"))
        s.check("a confirmed event has its run sheet, guest list and margin",
                all(w in card("late") for w in ("Run sheet", "Guest list", "What it made")))
        s.check("and says its balance is past due", "past due" in card("late")
                and "past due" not in card("ontime"))
    finally:
        _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
