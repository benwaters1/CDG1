"""Three more columns the house writes and nothing reads back.

A CAUTION WITH NO NAME ON IT. `set_guest_caution` writes
`caution_set_by_user_id`; the page showed the date and not the person. A
standing instruction not to accept somebody is the strongest thing this app
can say about a guest, and "who decided this" is the first question anybody
asks about one — because the answer decides whether whoever is on tonight can
overturn it or has to ring somebody. Joined in `guest_record` rather than in
the route, so anything else reading a whole guest gets it too.

WHAT THE ACCOUNTANT HAS NOT BEEN GIVEN, GOING OUT. `revenue_to_send` exists
because "only expenses ever reached Pennylane — money going out", and that
half was true and unmeasured: the expenses page shows a tick per row and
nothing counted them. `pennylane_synced_at` records when one went and was read
by nothing, so the lag between paying something and the accountant seeing it
was invisible. Reported as a MEDIAN — one invoice sent eight months late makes
an average lie about a good quarter.

HOW LONG AN AGREEMENT HAS RUN. `supplier_agreements.started_on` is written by
the form and read by nothing. The page is ordered by notice deadline, which is
right, and said nothing about age — yet a contract in its first year and one
in its ninth are different conversations with the same supplier, and the
second is where the money is.
"""
from datetime import timedelta

import re as _re

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "rnrtest-"


def _cleanup(conn):
    conn.execute("DELETE FROM guests WHERE email LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM expenses WHERE description LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM supplier_agreements WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("A caution, a cost and a contract, each read back at last")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    # ---------------------------------------------------------- the caution
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Manager", TAG + "mgr@example.invalid", now.isoformat()))
    conn.commit()
    setter = conn.execute("SELECT id FROM users WHERE email = ?",
                          (TAG + "mgr@example.invalid",)).fetchone()["id"]
    conn.execute(
        """INSERT INTO guests (name, email, caution, caution_level,
                   caution_set_at, caution_set_by_user_id, created_at)
           VALUES (?, ?, 'Left without paying', 'refuse', ?, ?, ?)""",
        (TAG + " Flagged", TAG + "flagged@example.invalid",
         now.isoformat(), setter, now.isoformat()))
    conn.commit()
    gid = conn.execute("SELECT id FROM guests WHERE email = ?",
                       (TAG + "flagged@example.invalid",)).fetchone()["id"]

    s.section("A caution says who put it there")
    record = m.guest_record(conn, gid)
    s.check("the record carries the name",
            record["guest"]["caution_set_by"] == TAG + "Manager",
            detail=str(record["guest"]["caution_set_by"]))
    body = oc.get(f"/guests/{gid}").get_data(as_text=True)
    s.check("and the page prints it", TAG + "Manager" in body,
            detail="on the strongest thing this app can say about a guest, "
                   "who decided it is the first question anybody asks")
    s.check("beside the day it was set", "set " in body and "by " in body)
    s.check("with what was actually written", "Left without paying" in body)

    s.section("And a caution nobody signed does not invent an author")
    conn.execute("UPDATE guests SET caution_set_by_user_id = NULL WHERE id = ?",
                 (gid,))
    conn.commit()
    unsigned = m.guest_record(conn, gid)
    s.check("the name is simply absent",
            unsigned["guest"]["caution_set_by"] is None,
            detail="rows written before the column existed have no author, "
                   "and guessing one would be worse than saying nothing")
    s.check("and the page still renders",
            oc.get(f"/guests/{gid}").status_code == 200)
    conn.execute("UPDATE guests SET caution_set_by_user_id = ? WHERE id = ?",
                 (setter, gid))
    conn.commit()

    # ------------------------------------------------------------ the costs
    def cost(desc, amount, spent_days_ago, *, sent_days_after=None):
        spent = today - timedelta(days=spent_days_ago)
        sent = ((now - timedelta(days=spent_days_ago - sent_days_after))
                if sent_days_after is not None else None)
        conn.execute(
            """INSERT INTO expenses (kind, description, amount, status,
                       spent_on, submitted_at, pennylane_invoice_id,
                       pennylane_synced_at, vendor_name)
               VALUES ('supplier_invoice', ?, ?, 'approved', ?, ?, ?, ?, ?)""",
            (TAG + desc, amount, spent.isoformat(), now.isoformat(),
             ("PL-" + desc) if sent_days_after is not None else None,
             sent.isoformat() if sent else None, TAG + " Supplier"))
        conn.commit()

    def banner_total():
        """The figure in the "have not been sent" line, as a number."""
        flat = " ".join(oc.get("/management/revenue-to-send")
                        .get_data(as_text=True).split())
        at = flat.find("not been sent")
        if at < 0:
            return 0.0
        hit = _re.search(r"[\u20ac&#;a-z0-9]*?([\d,]+(?:\.\d+)?) the accountant",
                         flat[at:at + 240])
        return float(hit.group(1).replace(",", "")) if hit else 0.0

    before = banner_total()
    cost("waiting-one", 480.0, 30)
    cost("waiting-two", 120.0, 10)
    # A cost the house refused. It is not owed to anybody and it is not
    # something the accountant is missing, so it must not be on the list --
    # and it must not be in the total, which is the number somebody acts on.
    conn.execute(
        """INSERT INTO expenses (kind, description, amount, status, spent_on,
                   submitted_at, vendor_name)
           VALUES ('staff_expense', ?, 9999.0, 'rejected', ?, ?, ?)""",
        (TAG + "declined-one", (today - timedelta(days=5)).isoformat(),
         now.isoformat(), TAG + " Supplier"))
    conn.commit()
    cost("sent-quick", 200.0, 20, sent_days_after=2)
    cost("sent-slow", 300.0, 60, sent_days_after=40)

    s.section("The costs the accountant has not been given")
    page = oc.get("/management/revenue-to-send").get_data(as_text=True)
    s.check("the section is there", "Costs going the other way" in page)
    s.check("an unsent cost is on it", TAG + "waiting-one" in page)
    s.check("marked as not sent", "Not sent" in page)
    # Scoped to the banner sentence rather than the whole page. Looking for
    # "600" anywhere matched some other number and passed with the sum taken
    # out entirely, which is a check that measured nothing.
    flat_page = " ".join(page.split())
    banner = (flat_page[flat_page.find("not been sent"):][:220]
              if "not been sent" in flat_page else "")
    # The CHANGE, not the total. The database holds the house's own unsent
    # costs, so asserting 600 was asserting somebody else's rows — it passed
    # alone and failed in the run, which is the same order-dependence that
    # caught the voids suite.
    s.check("with a total, not just a list",
            abs((banner_total() - before) - 600.0) < 0.01,
            detail=f"{banner_total()} against {before} before — 480 and 120; "
                   "a page that lists them and does not add them up makes you "
                   "do the arithmetic that decides whether to open it")
    s.check("and a cost the house refused is not in it",
            TAG + "declined-one" not in page
            and "9999" not in banner.replace(",", ""),
            detail="a refused cost is owed to nobody, and counting it makes "
                   "the total wrong in the direction somebody acts on")
    s.check("and a sent one says when it went",
            TAG + "sent-quick" in page and "after it was spent" in page,
            detail="pennylane_synced_at, written since the column existed "
                   "and read by nothing")

    s.section("The lag is a median, not an average")
    # Spent 20 days ago and sent 2 days later; spent 60 and sent 40 later.
    # The mean is 21 days, the median is 2 or 40 depending on ordering — the
    # point is that one very late invoice cannot move it far.
    cost("sent-terrible", 50.0, 300, sent_days_after=280)
    page = oc.get("/management/revenue-to-send").get_data(as_text=True)
    # Scoped to the HEADLINE, not the whole page: 280 days is the right
    # answer in that invoice's own row, and a check that searched the page for
    # it was failing on the number being correctly reported.
    flat = " ".join(page.split())
    headline = _re.search(
        r"took <strong>(\d+) days?</strong> from being spent", flat)
    typical = int(headline.group(1)) if headline else None
    s.check("the headline figure exists at all", typical is not None,
            detail=str(page[page.find("from being spent") - 120:
                            page.find("from being spent") + 30])[:200])
    s.check("one invoice sent nine months late does not become it",
            typical is not None and typical < 100,
            detail=f"{typical} days — the mean of 2, 40 and 280 is 107, which "
                   "would report the outlier as the house's normal speed")

    # ------------------------------------------------------- the agreements
    conn.execute(
        """INSERT INTO supplier_agreements (what, started_on, renews_on,
                   notice_days, annual_value, active, created_at)
           VALUES (?, ?, ?, 90, 4000, 1, ?)""",
        (TAG + " Old contract", (today - timedelta(days=365 * 9)).isoformat(),
         (today + timedelta(days=200)).isoformat(), now.isoformat()))
    conn.execute(
        """INSERT INTO supplier_agreements (what, started_on, renews_on,
                   notice_days, annual_value, active, created_at)
           VALUES (?, NULL, ?, 90, 900, 1, ?)""",
        (TAG + " Undated contract", (today + timedelta(days=210)).isoformat(),
         now.isoformat()))
    conn.commit()

    s.section("How long an agreement has run")
    rows = {a["row"]["what"]: a for a in m.supplier_agreements(conn, today)
            if str(a["row"]["what"]).startswith(TAG)}
    s.check("a nine-year contract says so",
            rows[TAG + " Old contract"]["running_years"] >= 8.9,
            detail=str(rows[TAG + " Old contract"]["running_years"]))
    s.check("one with no start date says that instead of nought",
            rows[TAG + " Undated contract"]["running_years"] is None,
            detail="nought years reads as a contract signed this morning, "
                   "which is the opposite of not knowing")
    body = oc.get("/management/agreements").get_data(as_text=True)
    s.check("the page prints the years", "running 9" in body or "running 8" in body,
            detail="a contract in its first year and one in its ninth are "
                   "different conversations with the same supplier")
    s.check("and names the ones it cannot date", "no start date on it" in body)

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
