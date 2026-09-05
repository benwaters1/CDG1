"""Ten more decisions the house records with nobody's name on them.

Four were done already — who cashed up, who decided a guest pays for a
breakage, who approved a journey, who answered a complaint. These are the
rest:

    site_visitors.signed_in_by_user_id      who let a stranger into the building
    site_visitors.signed_out_by_user_id     and who saw them off the premises
    vault_entries.updated_by_user_id        who last changed a password
    email_flags.resolved_by_user_id         who decided a message needed no reply
    cleaning_rounds.last_done_by_user_id    who says the deep clean was done
    tasks.directed_by_user_id               who put this on somebody's list
    company_documents.uploaded_by_user_id   who filed the certificate
    site_images.uploaded_by_user_id         who put that photograph on the site
    campaign_sends.sent_by_user_id          who wrote to every guest at once
    pennylane_exports.sent_by_user_id       who sent the books to the accountant

THE SITE REGISTER IS THE SHARPEST. It exists so a Class I monument open to the
public can say who was in the building. Every row has carried who signed a
visitor in and who signed them out since the table was written, and neither
was ever shown — so the register answered "somebody was here" and not "we let
them in", which is the half an insurer and a fire officer both ask for.

AND ITS TIMES WERE UTC. `v['signed_in_at'][:16]` slices the stored stamp and
prints it, so a visitor signed in at half past midnight in the Ariège appeared
on the register as the previous day, and every time on the page was an hour or
two out for the rest of the year. On a page whose whole purpose is answering
"who was in the building, and when", the when being wrong is not cosmetic.
"""
from datetime import timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "whodid-"


def _cleanup(conn):
    conn.execute("DELETE FROM site_visitors WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vault_entries WHERE title LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Ten more decisions that had nobody's name on them")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)

    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Porter", TAG + "p@example.invalid", now.isoformat()))
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Nightman", TAG + "n@example.invalid", now.isoformat()))
    conn.commit()
    porter = conn.execute("SELECT id FROM users WHERE email = ?",
                          (TAG + "p@example.invalid",)).fetchone()["id"]
    nightman = conn.execute("SELECT id FROM users WHERE email = ?",
                            (TAG + "n@example.invalid",)).fetchone()["id"]

    # ==================================================== the site register
    s.section("Who let a stranger into the building")
    # Signed in at 00:30 in the Ariège, which is 23:30 the day before in UTC.
    # The register used to slice the stored stamp and print it, so this
    # visitor appeared on the wrong day.
    local_half_past = m.datetime(2027, 6, 12, 0, 30, tzinfo=m.LOCAL_TZ)
    stored = local_half_past.astimezone(m.timezone.utc)
    conn.execute(
        """INSERT INTO site_visitors (name, company, reason, host_user_id,
                   signed_in_at, signed_in_by_user_id, created_at)
           VALUES (?, ?, 'Delivery', ?, ?, ?, ?)""",
        (TAG + " Stranger", TAG + " Haulage", porter, stored.isoformat(),
         porter, now.isoformat()))
    conn.commit()

    here = m.visitors_on_site(conn, now)
    mine = next((v for v in here if str(v["visitor"]["name"]).startswith(TAG)),
                None)
    s.check("the visitor is on the register", mine is not None,
            detail=str([v["visitor"]["name"] for v in here])[:120])
    s.check("with the name of whoever let them in",
            mine and mine["signed_in_by"] == TAG + "Porter",
            detail=f"{mine and mine.get('signed_in_by')} — the register said "
                   "somebody was here rather than who admitted them")
    body = oc.get("/management/visitors").get_data(as_text=True)
    s.check("and the page prints it", TAG + "Porter" in body)
    s.check("under a heading that says what it is",
            "Signed in by" in body)

    s.section("And who saw them off the premises")
    conn.execute(
        "UPDATE site_visitors SET signed_out_at = ?, signed_out_by_user_id = ? "
        "WHERE name = ?",
        ((stored + timedelta(hours=2)).isoformat(), nightman, TAG + " Stranger"))
    conn.commit()
    page = " ".join(
        oc.get("/management/visitors").get_data(as_text=True).split())

    def visit_row():
        """The one row of the register this suite made.

        Scoped, because the page carries a <select> of every active member of
        staff for the sign-in form — so both fixture names are on the document
        whatever the register says, and a check reading the whole page passed
        with the join broken AND the columns deleted.
        """
        at = page.find(TAG + " Stranger")
        if at < 0:
            return ""
        end = page.find("</tr>", at)
        return page[at:end if end > at else at + 700]

    row = visit_row()
    s.check("the closed visit is on the register", bool(row),
            detail=page[page.find("Been and gone"):][:150])
    s.check("naming who let them in", TAG + "Porter" in row,
            detail=row[:220])
    s.check("and who saw them out", TAG + "Nightman" in row,
            detail=f"{row[:220]} — a register recording an arrival and not a "
                   "departure cannot answer the only question it is ever "
                   "asked in an emergency: who is still inside")

    s.section("And the register is in the château's own clock")
    s.check("a visitor signed in at half past midnight is on that day",
            "June 12, 2027" in row,
            detail=f"{row[:220]} — stored as {stored.isoformat()[:16]} in "
                   "UTC, which is the 11th")
    s.check("and not on the day before",
            "June 11, 2027" not in row and "2027-06-11" not in row,
            detail=f"{row[:220]} — never slice a stamp to get a day; that "
                   "reads UTC, and this page exists to answer when somebody "
                   "was in the building")

    # ========================================================= the vault
    s.section("Who last changed a password")
    conn.execute(
        """INSERT INTO vault_entries (title, username, url, secret_encrypted,
                   created_at, updated_at, updated_by_user_id)
           VALUES (?, 'someone', '', 'x', ?, ?, ?)""",
        (TAG + " Router", now.isoformat(), now.isoformat(), porter))
    conn.commit()
    # The vault needs a key the harness clears — correctly, since it holds
    # real secrets. Enabled here so the page's own path runs rather than the
    # "not set up yet" branch, which would let the check pass on a page that
    # never renders an entry.
    was_enabled = m.vault_enabled
    try:
        m.vault_enabled = lambda: True
        vault = oc.get("/management/vault").get_data(as_text=True)
    finally:
        m.vault_enabled = was_enabled
    s.check("the entry is there", TAG + " Router" in vault)
    s.check("with who last changed it", TAG + "Porter" in vault,
            detail="a password altered with nobody's name against it is one "
                   "nobody can be asked about")

    # ================================================== all ten are joined
    s.section("And none of the ten is still anonymous")
    import re
    code = re.sub(r"#[^\n]*", "", open("app.py", encoding="utf-8").read())
    unnamed = []
    for table in [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]:
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]
        except Exception:
            continue
        for col in cols:
            if not col.endswith("_by_user_id"):
                continue
            # The JOIN itself, qualified by TABLE. Two things had to be
            # fixed here: it used to pass on the alias appearing in any
            # template, which is not this decision being answerable — and
            # then on an unqualified column name, so breaking
            # campaign_sends.sent_by_user_id was masked by the intact join on
            # pennylane_exports.sent_by_user_id, which is spelt the same.
            joined = re.search(
                r"ON\s+\w+\.id\s*=\s*%s\.%s"
                % (re.escape(table), re.escape(col)), code, re.I)
            if not joined:
                unnamed.append("%s.%s" % (table, col))
    # Sixteen are joined now. These are the rest, each with what it would
    # answer, so nobody has to rediscover them — and the number is a ceiling
    # that may fall and must not rise, the same as the undescribed pages.
    #
    # Four of them are money and want a page before they want a name:
    # booking_payments, event_payments and voucher_redemptions are only ever
    # read as totals, never listed per booking with who took the money, and
    # workshop_transactions the same. Joining a name onto a SUM shows nobody
    # anything.
    STILL_ANONYMOUS = {
        "booking_payments.taken_by_user_id": "who took a guest's money",
        "event_payments.taken_by_user_id": "who took an event payment",
        "voucher_redemptions.taken_by_user_id": "who honoured a voucher",
        "workshop_transactions.created_by_user_id": "who wrote the ledger line",
        "booking_extras.added_by_user_id": "who put an extra on a bill",
        "pos_order_formules.added_by_user_id": "who added a set menu",
        "absences.recorded_by_user_id": "who recorded somebody absent",
        "workshop_feedback.acknowledged_by_user_id": "who answered it",
        "shopping_items.added_by_user_id": "who asked for it to be bought",
        "breakfast_checklist_log.checked_by_user_id": "who ticked breakfast off",
        "meter_readings.read_by_user_id": "who read the meter",
        "maintenance_schedules.created_by_user_id": "who set the schedule",
        "maintenance_visits.recorded_by_user_id": "who logged the visit",
        "menus.created_by_user_id": "who wrote the menu",
        "promo_codes.created_by_user_id": "who created the code",
        "event_quotes.created_by_user_id": "who quoted it",
        "event_holds.created_by_user_id": "who promised the date",
        "filings_made.created_by_user_id": "who filed it",
        "social_posts.created_by_user_id": "who wrote the post",
        "social_plans.created_by_user_id": "who planned it",
        "vendor_documents.recorded_by_user_id": "who filed the supplier's paper",
    }
    unexpected = sorted(set(unnamed) - set(STILL_ANONYMOUS))
    s.check("no decision is anonymous that was not already",
            not unexpected,
            detail=f"{unexpected} — a *_by_user_id nothing joins is a "
                   "decision nobody can be asked about; there were "
                   "twenty-three and sixteen are done")
    fixed = sorted(set(STILL_ANONYMOUS) - set(unnamed))
    s.check("and the list does not keep names it no longer needs",
            not fixed,
            detail=f"{fixed} — joined since this list was written, so it "
                   "should come off it; a list that only grows is one nobody "
                   "reads twice")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
