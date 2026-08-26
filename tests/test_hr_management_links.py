"""Three things the data held separately and could not be asked together.

A standing cost is money going to somebody, and the somebody was never
recorded — so "what do we pay this supplier" was answered from invoices and
silently left out the subscription that renews itself.

An announcement was posted and nobody knew who had read it. That is the
question that matters after a closure or a safety notice.

An annual review only appeared once it was already twelve months late, which
is the first moment it is too late to have done on time.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-link-"


def _iso(days):
    return (datetime.now(m.LOCAL_TZ).date() + timedelta(days=days)).isoformat()


def _cleanup(conn):
    conn.execute("DELETE FROM announcement_reads WHERE announcement_id IN "
                 "(SELECT id FROM announcements WHERE title LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM announcements WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM recurring_costs WHERE label LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vendors WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM performance_reviews WHERE user_id IN "
                 "(SELECT id FROM users WHERE email LIKE ?)", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("HR and management links")
    oc, ec, _owner, employee = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------- a standing cost and its supplier
    s.section("A standing cost knows who it goes to")
    conn.execute("INSERT INTO vendors (name, created_at) VALUES (?, ?)",
                 (TAG + "Assureur", now))
    conn.commit()
    vid = conn.execute("SELECT id FROM vendors WHERE name = ?",
                       (TAG + "Assureur",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category, active,
           vendor_id, created_at) VALUES (?, 200, 'monthly', 'Insurance', 1, ?, ?)""",
        (TAG + "Buildings cover", vid, now))
    conn.execute(
        """INSERT INTO recurring_costs (label, amount, frequency, category, active,
           created_at) VALUES (?, 50, 'monthly', 'Software', 1, ?)""",
        (TAG + "Orphan tool", now))
    conn.commit()

    page = oc.get("/management/recurring-costs").get_data(as_text=True)
    s.check("the cost page names the supplier", TAG + "Assureur" in page)
    s.check("and offers a way to link the ones that are not",
            'name="vendor_id"' in page)

    # One real invoice of a known amount, so the paid figure can be pinned
    # exactly. Without it there is no row to check and the assertion below
    # passes for the wrong reason — which is precisely what happened first.
    conn.execute(
        """INSERT INTO expenses (kind, vendor_name, description, amount, status,
           submitted_at) VALUES ('supplier_invoice', ?, ?, 175.00, 'paid', ?)""",
        (TAG + "Assureur", TAG + "excess", now))
    conn.commit()

    data = m.spend_by_vendor(conn, start=_iso(-400))
    row = next((r for r in data["rows"] if r["vendor_name"] == TAG + "Assureur"), None)
    s.check("the supplier is on the spend page", row is not None)
    s.check("its standing cost is annualised, not counted once",
            row and abs(row["standing_yearly"] - 2400) < 0.01,
            detail=str(row["standing_yearly"]) if row else "")
    s.check("and the subscription is named, not just totalled",
            row and any("Buildings cover" in i for i in row["standing_items"]),
            detail=str(row["standing_items"]) if row else "")
    # The invariant that keeps the page honest: what has been PAID and what is
    # committed answer different questions and must never be summed.
    s.check("the paid figure is invoices only, with no standing cost mixed in",
            row and abs(row["total"] - 175.00) < 0.005,
            detail=str(row["total"]) if row else "")
    s.check("a supplier with a standing cost and no invoice is still counted",
            data["standing_total"] >= 2400, detail=str(data["standing_total"]))

    # -------------------------------------------------- who has seen it
    s.section("Who has actually seen an announcement")
    conn.execute(
        """INSERT INTO announcements (posted_by_user_id, title, body, created_at)
           VALUES (NULL, ?, 'The cellar stair is closed.', ?)""",
        (TAG + "Cellar closed", now))
    conn.commit()
    aid = conn.execute("SELECT id FROM announcements WHERE title = ?",
                       (TAG + "Cellar closed",)).fetchone()["id"]

    before = m.announcement_readership(conn, [aid])[aid]
    s.check("before anybody looks, nobody has seen it", before["seen"] == [])
    s.check("and everyone active is listed as not having",
            len(before["not_seen"]) == before["total"] and before["total"] > 0,
            detail=str(before["total"]))

    ec.get("/announcements")
    after = m.announcement_readership(conn, [aid])[aid]
    s.check("an employee opening the page is recorded", len(after["seen"]) == 1,
            detail=str(after["seen"]))
    s.check("and they drop off the not-seen list",
            len(after["not_seen"]) == before["total"] - 1)

    # The owner's own board is not evidence the staff read anything.
    seen_names = {x["name"] for x in after["seen"]}
    oc.get("/announcements")
    owner_after = m.announcement_readership(conn, [aid])[aid]
    s.check("the owner opening it does not mark it seen for them",
            {x["name"] for x in owner_after["seen"]} == seen_names,
            detail=str([x["name"] for x in owner_after["seen"]]))

    first_at = after["seen"][0]["at"]
    ec.get("/announcements")
    again = m.announcement_readership(conn, [aid])[aid]
    s.check("reading it twice does not move the date it was first seen",
            again["seen"][0]["at"] == first_at)

    owner_page = oc.get("/announcements").get_data(as_text=True)
    s.check("the owner is told who has NOT seen it", "Not seen by" in owner_page
            or "Seen by everyone" in owner_page)

    # ------------------------------------------------ reviews, before they are late
    s.section("An annual review surfaces before it is overdue")
    conn.execute(
        """INSERT INTO users (email, password_hash, role, name, job_role, status,
           start_date, created_at)
           VALUES (?, 'x', 'employee', 'Duesoon', 'General', 'active', ?, ?)""",
        (TAG + "d@example.invalid", _iso(-400), now))
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE email = ?",
                       (TAG + "d@example.invalid",)).fetchone()["id"]
    # Reviewed almost a year ago: due in a fortnight, not yet late.
    conn.execute(
        """INSERT INTO performance_reviews (user_id, review_date, status, created_at)
           VALUES (?, ?, 'draft', ?)""", (uid, _iso(-351), now))
    conn.commit()

    today = datetime.now(m.LOCAL_TZ).date()
    due = m.annual_reviews_due(conn, today)
    hit = next((r for r in due if r["user_id"] == uid), None)
    s.check("it is listed before it falls due", hit is not None)
    s.check("with the date it is due", hit and hit["due"] == _iso(14),
            detail=str(hit["due"]) if hit else "")

    # Already late is the escalation's job, and saying it twice in different
    # words teaches people to read neither.
    conn.execute("UPDATE performance_reviews SET review_date = ? WHERE user_id = ?",
                 (_iso(-400), uid))
    conn.commit()
    s.check("once overdue it drops off this list",
            not any(r["user_id"] == uid for r in m.annual_reviews_due(conn, today)))

    # And one reviewed last week is nowhere near due.
    conn.execute("UPDATE performance_reviews SET review_date = ? WHERE user_id = ?",
                 (_iso(-7), uid))
    conn.commit()
    s.check("a recent review is not chased",
            not any(r["user_id"] == uid for r in m.annual_reviews_due(conn, today)))

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
