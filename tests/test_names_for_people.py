"""A page names things for the person reading it, not by the key they are stored under.

WHAT WENT WRONG. Read on 12 October 2026, and none of it errored:
  - the notifications page's chips read "hr escalation", "inbox flag
    escalated", "shift swap needs approval" -- the kind each was filed under,
    underscores taken out -- one chip per kind, so eleven sorts of shift and HR
    message were eleven chips;
  - the workshop register said a deposit was paid "· stripe";
  - Channels listed "direct" and "booking.com" as stored, and the commission
    box for "booking.com" was labelled "Booking.com" by accident of capitalize;
  - Extras' chips said "food" and "other" above a form that says "Food &
    hampers"; Recurring costs' said "monthly" and "annual"; the audit log's
    actions were lower case; the menu's allergens were the stored keys.

WHAT THIS PINS. Every kind of notification the code sends has a name, and no
name is kept for a kind nothing sends; the pages show the names. The owner
sweep holds every chip on every owner page to the same rule.
"""
import ast
import os
from datetime import timedelta

from _harness import Suite, clients, db, house_today
import _harness

m = _harness.m
TAG = "ZZNAMES"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kinds_sent():
    """The kind of every send_notification in app.py that names one outright."""
    tree = ast.parse(open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())
    kinds = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "send_notification":
            if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
                kinds.add(node.args[2].value)
            for kw in node.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    kinds.add(kw.value.value)
    return kinds


def _cleanup(conn):
    conn.execute("DELETE FROM notifications WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM bookings WHERE reference_code LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Names for people")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)

    s.section("Every kind of notification has a name")
    sent = _kinds_sent()
    s.check("the reader found the kinds the code sends", len(sent) >= 25,
            detail=f"{len(sent)} -- a reader that finds nothing agrees with everything")
    unnamed = sorted(sent - set(m.NOTIFICATION_KIND_LABELS))
    s.check("every kind sent has a name for the chips", not unnamed,
            detail=f"{unnamed} -- add it to NOTIFICATION_KIND_LABELS")
    stale = sorted(set(m.NOTIFICATION_KIND_LABELS) - sent)
    s.check("and no name is kept for a kind nothing sends", not stale, detail=str(stale))
    for kind in ("hr_escalation", "inbox_flag_escalated", "shift_swap_needs_approval"):
        conn.execute("INSERT INTO notifications (user_id, kind, title, created_at) "
                     "VALUES (?, ?, ?, ?)", (owner["id"], kind, f"{TAG} {kind}",
                                             _harness.datetime_now()))
    conn.commit()
    page = oc.get("/notifications").get_data(as_text=True)
    chips = page[page.find('facet-label">About'):]
    chips = chips[:chips.find("</div>")]
    s.check("the page's chips read HR, Inbox and Shifts",
            all(f">{w} <" in chips or f">{w}<" in chips or f"{w} <span" in chips
                for w in ("HR", "Inbox", "Shifts")) and "escalation" not in chips,
            detail=" ".join(chips.split())[:300])

    s.section("How something was paid, in words")
    s.check("a card payment online", m.payment_method_label("stripe") == "Card, online")
    s.check("one on the terminal", m.payment_method_label("card_terminal")
            == m.POS_PAYMENT_METHODS["card_terminal"])
    s.check("and a key it has never seen is at least not a key",
            m.payment_method_label("cheque_postal") == "Cheque postal")

    s.section("A channel, in words")
    room = conn.execute("SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()["id"]
    day = house_today() + timedelta(days=900)
    conn.execute(
        """INSERT INTO bookings (room_id, reference_code, manage_token, guest_name, guest_email,
           arrival_date, departure_date, party_size, status, total_price, source, created_at)
           VALUES (?, ?, ?, ?, 'zznames@example.invalid', ?, ?, 2, 'confirmed', 300, 'booking.com', ?)""",
        (room, TAG + "1", "tok" + TAG, TAG + " Guest", day.isoformat(),
         (day + timedelta(days=2)).isoformat(), _harness.datetime_now()))
    conn.commit()
    page = oc.get(f"/management/channels?from={day.isoformat()}&to={(day + timedelta(days=3)).isoformat()}"
                  ).get_data(as_text=True)
    table = page[page.find("<tbody"):page.find("</tbody>")]
    s.check("a booking through Booking.com is listed as Booking.com",
            ">Booking.com<" in table and ">booking.com<" not in table,
            detail=" ".join(table.split())[:200])
    s.check("and the CSV keeps the key it is stored under",
            any(r["source"] == "booking.com"
                for r in m.channel_cost(conn, day, day + timedelta(days=3))["csv"]))

    s.section("A recurring cost's frequency, in words")
    for label, freq in ((TAG + " Water", "monthly"), (TAG + " Insurance", "annual")):
        conn.execute("INSERT INTO recurring_costs (label, amount, frequency, active, created_at) "
                     "VALUES (?, 100, ?, 1, ?)", (label, freq, _harness.datetime_now()))
    conn.commit()
    page = oc.get("/management/recurring-costs").get_data(as_text=True)
    chips = page[page.find('facet-label">How often'):]
    chips = chips[:chips.find("</div>")]
    s.check("the chips say every month and once a year, not monthly and annual",
            "Every month" in chips and "Once a year" in chips and "annual" not in chips,
            detail=" ".join(chips.split())[:200])

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
