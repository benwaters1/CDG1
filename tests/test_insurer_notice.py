"""Incidents the insurer was never told about.

The register already said "not yet reported" against an incident. It said it
in the same quiet grey whether the thing happened this morning or in April,
and only when a policy had been attached — so an incident with no policy
chosen said nothing about insurance at all, which is the case most likely to
be forgotten.

Late notification is how cover is lost. A complete register and an insurer
who knows nothing are entirely compatible states.
"""
import re
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db
import _harness

m = _harness.m
TAG = "ztest-ins-"


def _cleanup(conn):
    conn.execute("DELETE FROM incidents WHERE summary LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM insurance_policies WHERE policy_number LIKE ?", (TAG + "%",))
    conn.commit()


def _incident(conn, summary, days_ago, kind="guest", severity="minor", policy_id=None,
              reported=None):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        """INSERT INTO incidents (kind, occurred_at, summary, severity,
           insurance_policy_id, reported_to_insurer_at, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
        (kind, when, TAG + summary, severity, policy_id, reported,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _find(rows, summary):
    return next((r for r in rows if r["summary"] == TAG + summary), None)


def run():
    s = Suite("insurer notice")
    oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = datetime.now(timezone.utc)

    conn.execute(
        """INSERT INTO insurance_policies (provider, policy_number, coverage_type,
           created_at) VALUES ('Testeur SA', ?, 'liability', ?)""",
        (TAG + "POL", now.isoformat()))
    conn.commit()
    pol = conn.execute("SELECT id FROM insurance_policies WHERE policy_number = ?",
                       (TAG + "POL",)).fetchone()["id"]

    s.section("The clock")
    _incident(conn, "fresh", 1, policy_id=pol)
    _incident(conn, "late", 30, policy_id=pol)
    data = m.incidents_awaiting_insurer(conn)

    fresh, late = _find(data["all"], "fresh"), _find(data["all"], "late")
    s.check("a recent one is listed but not overdue",
            fresh and fresh["overdue"] is False)
    s.check("and says how long is left", fresh and fresh["days_left"] > 0,
            detail=str(fresh["days_left"]) if fresh else "")
    s.check("one past the window is overdue", late and late["overdue"] is True)
    s.check("it is in the overdue list", _find(data["overdue"], "late") is not None)
    s.check("the overdue one sorts above the fresh one",
            data["all"] and data["all"][0]["summary"] == TAG + "late",
            detail=str([r["summary"] for r in data["all"]]))

    s.section("A workplace accident has a shorter, statutory window")
    # 48 hours to the CPAM. Three days is fine for a guest incident and
    # already late for this one — the difference is the whole point.
    _incident(conn, "worker", 3, kind="workplace", severity="significant", policy_id=pol)
    _incident(conn, "visitor", 3, kind="guest", policy_id=pol)
    data = m.incidents_awaiting_insurer(conn)
    worker, visitor = _find(data["all"], "worker"), _find(data["all"], "visitor")
    s.check("three days is already overdue for a workplace accident",
            worker and worker["overdue"] is True)
    s.check("and it is marked statutory", worker and worker["statutory"] is True)
    s.check("but three days is fine for a guest incident",
            visitor and visitor["overdue"] is False)

    s.section("The case that used to be silent")
    _incident(conn, "nopolicy", 20, severity="significant")
    data = m.incidents_awaiting_insurer(conn)
    orphan = _find(data["all"], "nopolicy")
    s.check("an incident with no policy is still listed", orphan is not None,
            detail="it showed nothing about insurance at all before")
    s.check("and is separated out, because it needs a different action",
            _find(data["no_policy"], "nopolicy") is not None)
    s.check("it is not counted as merely unsent",
            _find(data["unreported"], "nopolicy") is None)

    s.section("What should not be chased")
    _incident(conn, "nearmiss", 40, kind="near_miss", severity="near_miss")
    _incident(conn, "done", 40, policy_id=pol, reported=now.isoformat())
    data = m.incidents_awaiting_insurer(conn)
    s.check("a near miss is not chased — nobody to tell",
            _find(data["all"], "nearmiss") is None)
    s.check("one already reported drops off", _find(data["all"], "done") is None)

    s.section("The page")
    page = oc.get("/admin/incidents?status=open").get_data(as_text=True)
    s.check("the banner appears", "The insurer has not been told" in page)
    s.check("the overdue one is named", TAG + "late" in page)
    s.check("it says 48h is statutory", "48h is statutory" in page)
    s.check("and does not present the five days as law",
            "check yours" in page)

    # An incident somebody marked closed can still be one the insurer was
    # never told about; the status tab must not hide it.
    conn.execute("UPDATE incidents SET status = 'closed' WHERE summary = ?",
                 (TAG + "late",))
    conn.commit()
    page = oc.get("/admin/incidents?status=open").get_data(as_text=True)
    s.check("closing an incident does not hide it from the banner",
            TAG + "late" in page)

    s.section("The clock starts when it happened, on the house's clock")
    # Recorded through the form at 23:30 on 4 September, here. The workplace
    # window is 48 hours, so it closes at 23:30 on the 6th. Read as UTC, as it
    # used to be, the accident is two hours younger and still inside it.
    r = oc.post("/admin/incidents/new", data={
        "occurred_at": "2026-09-04T23:30", "kind": "workplace", "severity": "minor",
        "summary": TAG + "terrace"}, follow_redirects=True)
    after = datetime(2026, 9, 6, 22, 30, tzinfo=timezone.utc)    # 00:30 on the 7th, here
    t = _find(m.incidents_awaiting_insurer(conn, now=after)["all"], "terrace")
    s.check("at 00:30 on the 7th it is 49 hours old", t and abs(t["age_days"] * 24 - 49) < 0.01,
            r, detail=f"{t['age_days'] * 24:.2f}h" if t else "not listed")
    s.check("and past the 48-hour window", t and t["overdue"] is True)
    before = datetime(2026, 9, 6, 20, 30, tzinfo=timezone.utc)   # 22:30 on the 6th, here
    t = _find(m.incidents_awaiting_insurer(conn, now=before)["all"], "terrace")
    s.check("at 22:30 on the 6th it has an hour left, and is not overdue",
            t and t["overdue"] is False and abs(t["days_left"] * 24 - 1) < 0.01,
            detail=f"{t['days_left'] * 24:.2f}h left" if t else "not listed")

    s.section("The day it was reported")
    # 22:30 UTC on the 5th is half past midnight on the 6th here. The card
    # used to slice the first ten characters off an already-worded date, and
    # printed "reported September " with no day at all.
    _incident(conn, "told", 40, policy_id=pol, reported="2026-09-05T22:30:00+00:00")
    page = oc.get("/admin/incidents?status=open").get_data(as_text=True)
    card = next((c for c in page.split('class="expense-card"')
                 if f"<strong>{TAG}told</strong>" in c), "")
    said = re.search(r"Policy:[^·]*·\s*reported\s+(.*?)\s*</p>", card, re.S)
    said = " ".join(said.group(1).split()) if said else None
    s.check("the card says it was reported on 6 September 2026",
            said == "6 September 2026", detail=f"says {said!r}")

    _cleanup(conn)
    return s


if __name__ == "__main__":
    print(run().report())
