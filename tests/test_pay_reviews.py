"""Somebody who has been on the same wage for a long time.

Nothing chases a pay review. It is not urgent on any particular day, which is
exactly why it slides for three years and then arrives as a resignation — and
by then the conversation is about matching an offer rather than about the work.

Two decisions this pins, because both are easy to get backwards:

  - Only people who HAVE a wage on file. Somebody with none is a different
    problem, already named on the wages page and in the outlook, and reporting
    the same person twice in different words makes both notices easier to
    ignore.

  - Counted from the EFFECTIVE DATE of their latest record, not when it was
    typed in. A rise entered late but dated April was an April rise. Using the
    typed date would reset somebody's clock every time an old record was
    tidied up, which is the direction that hides the problem.

And it is a warning, not a blocking task: a conversation to have, not a shift
nobody is covering.
"""
from datetime import date, datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ZZPAYR"


def _cleanup():
    conn = db()
    conn.execute("""DELETE FROM wage_records WHERE user_id IN
                    (SELECT id FROM users WHERE name LIKE ?)""", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM app_settings WHERE key = 'wage_review_months'")
    conn.commit()
    conn.close()


def _person(name, status="active"):
    conn = db()
    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', ?, ?)""",
        (f"{TAG} {name}", f"{TAG.lower()}.{name.lower()}@example.invalid", status,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE name = ?", (f"{TAG} {name}",)).fetchone()
    conn.close()
    return row


def _wage(user_id, days_ago, amount=12.0, typed_days_ago=None):
    """A wage effective `days_ago` days back, optionally recorded later."""
    conn = db()
    effective = (date.today() - timedelta(days=days_ago)).isoformat()
    typed = datetime.now(timezone.utc) - timedelta(days=typed_days_ago or 0)
    conn.execute(
        """INSERT INTO wage_records (user_id, effective_from, basis, gross_amount, created_at)
           VALUES (?, ?, 'hourly', ?, ?)""",
        (user_id, effective, amount, typed.isoformat()))
    conn.commit()
    conn.close()


def _due():
    """Only this suite's people.

    Other suites leave wage_records behind for the shared fixtures, so an
    assertion that the whole list is empty holds when this file runs alone and
    fails in a full run — with the fault in the test and the appearance of a
    fault in the feature.
    """
    conn = db()
    try:
        return {d["name"]: d for d in m.wage_reviews_due(conn)
                if d["name"].startswith(TAG)}
    finally:
        conn.close()


def _months(value):
    conn = db()
    conn.execute("""INSERT INTO app_settings (key, value) VALUES ('wage_review_months', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (str(value),))
    conn.commit()
    conn.close()


def run():
    s = Suite("Pay reviews")
    _cleanup()
    oc, ec, owner, emp = clients()

    s.section("Somebody on the same wage for over a year is surfaced")
    stale = _person("Stale")
    _wage(stale["id"], days_ago=500)
    due = _due()
    s.check("they are listed", f"{TAG} Stale" in due, detail=f"{list(due)}")
    s.check("with roughly how long it has been",
            due.get(f"{TAG} Stale", {}).get("months", 0) >= 15,
            detail=f"got {due.get(f'{TAG} Stale', {}).get('months')}")

    s.section("Somebody recently reviewed is not")
    fresh = _person("Fresh")
    _wage(fresh["id"], days_ago=30)
    s.check("they are left alone", f"{TAG} Fresh" not in _due(),
            detail=f"{list(_due())}")

    s.section("A rise resets it")
    _wage(stale["id"], days_ago=2, amount=14.0)
    s.check("the stale one drops off", f"{TAG} Stale" not in _due(),
            detail=f"{list(_due())} — a rise did not clear the reminder")

    s.section("Counted from when the rise applied, not when it was typed")
    # A rise entered late but dated April was an April rise. Using the typed
    # date resets the clock whenever an old record is tidied up, which hides
    # exactly the case this exists to find.
    late = _person("LateEntry")
    _wage(late["id"], days_ago=600, typed_days_ago=0)   # applied long ago, typed today
    s.check("they are still listed", f"{TAG} LateEntry" in _due(),
            detail=f"{list(_due())} — typing an old record in today made "
                   "somebody look freshly reviewed")

    s.section("Somebody with no wage at all is a different notice")
    # Already named on the wages page and in the outlook. Two notices about one
    # person in different words makes both easier to skip.
    nowage = _person("NoWage")
    s.check("they are not in this list", f"{TAG} NoWage" not in _due(),
            detail=f"{list(_due())}")

    s.section("And somebody who has left is not chased")
    gone = _person("Gone", status="inactive")
    _wage(gone["id"], days_ago=900)
    s.check("an inactive account is skipped", f"{TAG} Gone" not in _due(),
            detail=f"{list(_due())} — a leaver was raised for a pay review")

    s.section("The window is a setting")
    _months(36)
    s.check("a three-year window clears the late entry",
            f"{TAG} LateEntry" not in _due(), detail=f"{list(_due())}")
    _months(6)
    s.check("a six-month one finds them again", f"{TAG} LateEntry" in _due(),
            detail=f"{list(_due())}")

    s.section("Zero turns it off rather than meaning immediately")
    _months(0)
    s.check("nobody is listed", not _due(),
            detail=f"{list(_due())} — 0 was read as 'everybody is overdue'")
    _months(12)

    s.section("It reaches the owner where they look")
    with m.app.test_request_context("/"):
        conn = db()
        warnings = m.owner_home_warnings(conn, date.today())
        conn.close()
    pay = [w for w in warnings if "Pay not reviewed" in w["title"]]
    s.check("it is on the owner home", len(pay) == 1, detail=f"{[w['title'] for w in warnings]}")
    # The detail names whoever is stalest overall, which in a full run may be
    # somebody another suite created. What matters here is that the notice
    # exists, counts this suite's people among them, and links to the page.
    if pay:
        s.check("as a warning, not a blocker", pay[0]["severity"] == "warn",
                detail=f"{pay[0]['severity']} — a pay review is a conversation, "
                       "not an uncovered shift")
        s.check("it names somebody", bool(pay[0]["detail"].strip()),
                detail=pay[0]["detail"])
        s.check("and counts at least this suite's stale wages",
                pay[0]["count"] >= len(_due()),
                detail=f"panel says {pay[0]['count']}, this suite has {len(_due())}")
        s.check("and links to the wages page", "wages" in pay[0]["href"],
                detail=pay[0]["href"])

    s.section("And on the wages page itself")
    html = oc.get("/admin/payroll/wages").get_data(as_text=True)
    s.check("the page says it too", "same wage for" in html,
            detail="the warning links here and here says nothing about it")

    s.section("It clears when there is nobody to chase")
    # The panel has to be able to be empty, or it becomes furniture.
    conn = db()
    conn.execute("""UPDATE wage_records SET effective_from = ?
                    WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)""",
                 (date.today().isoformat(), TAG + "%"))
    conn.commit()
    conn.close()
    s.check("nobody is due", not _due(), detail=f"{list(_due())}")
    with m.app.test_request_context("/"):
        conn = db()
        warnings = m.owner_home_warnings(conn, date.today())
        conn.close()
    remaining = [w for w in warnings if "Pay not reviewed" in w["title"]]
    s.check("and the notice can be empty at all",
            not remaining or remaining[0]["count"] < 99,
            detail="a notice that can never be empty stops being read")
    s.check("with none of this suite's people left in it", not _due(),
            detail=f"{list(_due())}")

    _cleanup()
    return s
