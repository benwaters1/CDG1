"""Insurance policies, and the renewal that must not happen twice.

An insurance policy quietly lapsing is a business risk rather than a cosmetic
one, and the whole register was untested.

The renewal is the interesting part. It moves the expiry forward by a year (or
a month, for a monthly premium) using an optimistic-concurrency guard:

    UPDATE ... SET expiry_date = ? WHERE id = ? AND expiry_date = <the old one>

That guard buys one specific thing: two requests racing each other both read
the same expiry, and the second one's write loses. It does NOT make renewal
idempotent — a second, later click re-reads the new expiry and renews again,
which is legitimate if you meant it and a year of imaginary cover if you did
not. I assumed the former when writing this and was wrong; the guard is right
and the assertion was the mistake.

So the sequential case is handled where it belongs, in the markup: the button
asks before it moves the date. Being wrong here means believing you are
covered when you are not, which is the worst direction for this field to fail.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZINS"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM insurance_policies WHERE provider LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _policy(expiry, frequency="annual"):
    conn = db()
    conn.execute(
        """INSERT INTO insurance_policies (provider, policy_number, coverage_type,
           premium, premium_frequency, expiry_date, created_at)
           VALUES (?, ?, 'buildings', 1200.0, ?, ?, ?)""",
        (f"{TAG} Assureur", f"{TAG}-001", frequency, expiry,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM insurance_policies WHERE provider LIKE ? ORDER BY id DESC LIMIT 1",
        (TAG + "%",)).fetchone()
    conn.close()
    return row


def run():
    s = Suite("Insurance")
    _cleanup()
    oc, ec, owner, emp = clients()
    expiry = (m.house_today() + timedelta(days=40)).isoformat()

    s.section("Adding a policy")
    r = oc.post("/management/insurance/new", data={
        "provider": f"{TAG} Assureur", "policy_number": f"{TAG}-NEW",
        "coverage_type": "buildings", "premium": "1450.75",
        "premium_frequency": "annual", "expiry_date": expiry, "notes": "",
    }, follow_redirects=True)
    conn = db()
    made = conn.execute(
        "SELECT * FROM insurance_policies WHERE policy_number = ?", (f"{TAG}-NEW",)).fetchone()
    conn.close()
    s.check("the policy is recorded", made is not None, r)
    if made:
        s.check("with its premium", abs((made["premium"] or 0) - 1450.75) < 0.01,
                detail=f"got {made['premium']}")
        s.check("and its expiry", made["expiry_date"] == expiry)

    s.section("Renewing moves the expiry forward a year")
    pol = _policy(expiry)
    oc.post(f"/management/insurance/{pol['id']}/renew", follow_redirects=True)
    conn = db()
    after = conn.execute(
        "SELECT expiry_date FROM insurance_policies WHERE id = ?", (pol["id"],)).fetchone()
    conn.close()
    expected = m.add_months(m.parse_date(expiry), 12).isoformat()
    s.check("the expiry is a year later", after["expiry_date"] == expected,
            detail=f"got {after['expiry_date']}, expected {expected}")

    s.section("The guard rejects a stale write, which is what it is for")
    # Two requests racing both read the same expiry; the second's write must
    # lose. That is what `AND expiry_date = <the value we read>` buys, and it
    # is testable directly rather than by trying to race the test client.
    #
    # It does NOT make renewal idempotent across separate clicks: a second,
    # later request re-reads the new expiry and legitimately renews again. That
    # is why the button now asks first — see _insurance_section.html.
    conn = db()
    stale = conn.execute(
        "UPDATE insurance_policies SET expiry_date = ? WHERE id = ? AND expiry_date = ?",
        ("2099-01-01", pol["id"], "1999-01-01")).rowcount
    fresh_val = conn.execute(
        "SELECT expiry_date FROM insurance_policies WHERE id = ?", (pol["id"],)).fetchone()
    conn.rollback()
    conn.close()
    s.check("a write based on a stale expiry changes nothing", stale == 0,
            detail=f"rowcount {stale}")
    s.check("and the policy still holds the renewed date",
            fresh_val["expiry_date"] == expected, detail=f"got {fresh_val['expiry_date']}")

    s.section("The Renew button asks before it moves the date")
    page = oc.get("/management/company-info").get_data(as_text=True)
    s.check("a confirmation is attached to the form",
            "return confirm(" in page and "expiry forward" in page,
            detail="Renew submits with no confirmation, so a second click adds a year silently")

    s.section("A monthly premium renews by a month, not a year")
    monthly = _policy(expiry, frequency="monthly")
    oc.post(f"/management/insurance/{monthly['id']}/renew", follow_redirects=True)
    conn = db()
    m_after = conn.execute(
        "SELECT expiry_date FROM insurance_policies WHERE id = ?", (monthly["id"],)).fetchone()
    conn.close()
    s.check("one month on", m_after["expiry_date"] == m.add_months(m.parse_date(expiry), 1).isoformat(),
            detail=f"got {m_after['expiry_date']}")

    s.section("An expiring policy reaches the owner")
    # Already past its date, so it must show up wherever expiry is surfaced.
    lapsed = _policy((m.house_today() - timedelta(days=3)).isoformat())
    page = oc.get("/management/company-info")
    s.check("the register lists it", page.status_code == 200 and TAG in page.get_data(as_text=True),
            detail=f"HTTP {page.status_code}")
    home = oc.get("/").get_data(as_text=True)
    s.check("and an expired policy is not silently invisible on Home",
            "nsurance" in home or "xpir" in home,
            detail="nothing on the dashboard mentions insurance at all")

    s.section("Guards")
    s.check("an employee cannot add a policy",
            ec.post("/management/insurance/new", data={
                "provider": "x", "policy_number": "y", "coverage_type": "z",
                "premium": "1", "premium_frequency": "annual",
                "expiry_date": expiry}).status_code in (302, 403))
    s.check("an employee cannot renew one",
            ec.post(f"/management/insurance/{pol['id']}/renew").status_code in (302, 403))
    s.check("renewing something that does not exist is a 404",
            oc.post("/management/insurance/999999/renew").status_code == 404)

    conn = db()
    audited = conn.execute(
        """SELECT COUNT(*) AS c FROM audit_log
           WHERE action = 'insurance_policy_renewed' AND target LIKE ?""",
        (TAG + "%",)).fetchone()["c"]
    conn.close()
    s.check("a renewal is written to the audit log", audited >= 1, detail=f"{audited} entries")

    _cleanup()
    return s
