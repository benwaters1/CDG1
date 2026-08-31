"""Whether the house is actually insured, and who finds out when it is not.

The château is open to the public. Its liability cover lapsing is the single
largest thing in this app that can go wrong quietly, and the whole signal for
it was one count on a management band — a page somebody has to decide to open.

That count could not tell the two states apart either. It asked for
`expiry_date < today + 60 days`, which is true of a policy expiring next week
AND of one that lapsed six months ago. Both read as the same number, so it
could not distinguish "renew this soon" from "you have been uninsured since
March".

A policy with no expiry date is a THIRD state and is deliberately neither. It
is a record somebody did not finish, and calling it covered or lapsed would be
inventing a fact about the house's insurance from a blank field.

Vehicle policies are left out of the owner's home page on purpose. The vehicle
check names the CAR — "the Berline is not legal to drive" is a more useful
sentence than "policy P-4471 has expired" — and counting both would put two
lines on the panel about one lapsed van policy.
"""
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTCOV"


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM insurance_policies WHERE provider LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM vehicles WHERE name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM tasks WHERE title LIKE ?", (TAG + "%",))
    conn.commit()
    conn.close()


def _policy(provider, expires, coverage="public liability", vehicle_id=None):
    conn = db()
    conn.execute(
        """INSERT INTO insurance_policies (provider, policy_number, coverage_type,
           premium, premium_frequency, expiry_date, vehicle_id, created_at)
           VALUES (?, ?, ?, 900, 'annual', ?, ?, ?)""",
        (TAG + " " + provider, "P-" + provider[:4].upper(), coverage, expires,
         vehicle_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM insurance_policies WHERE provider = ?",
                       (TAG + " " + provider,)).fetchone()
    conn.close()
    return row


def _cover(today=None):
    conn = db()
    try:
        with m.app.test_request_context():
            return {c["policy"]["provider"]: c
                    for c in m.insurance_cover(conn, today or datetime.now(m.LOCAL_TZ).date())}
    finally:
        conn.close()


def run():
    s = Suite("Is the house covered")
    _cleanup()
    oc, ec, owner, emp = clients()
    today = datetime.now(m.LOCAL_TZ).date()

    s.section("Three states, not one number")
    _policy("Axa", (today + timedelta(days=300)).isoformat())
    _policy("Groupama", (today + timedelta(days=20)).isoformat())
    _policy("Allianz", (today - timedelta(days=90)).isoformat())
    _policy("Maif", None)
    cover = _cover()

    s.check("a policy with a year to run reads as covered",
            cover[TAG + " Axa"]["state"] == "ok", detail=cover[TAG + " Axa"]["state"])
    s.check("one running out inside six weeks reads as expiring",
            cover[TAG + " Groupama"]["state"] == "expiring",
            detail=cover[TAG + " Groupama"]["state"])
    s.check("and one that has already gone reads as lapsed",
            cover[TAG + " Allianz"]["state"] == "lapsed",
            detail=cover[TAG + " Allianz"]["state"])
    s.check("with how long ago, so it can be judged at a glance",
            cover[TAG + " Allianz"]["days"] == 90,
            detail=str(cover[TAG + " Allianz"]["days"]))
    s.check("a policy with no date is neither covered nor lapsed",
            cover[TAG + " Maif"]["state"] == "no_date",
            detail="calling a blank field covered, or lapsed, invents a fact "
                   "about the house's insurance")

    s.section("The band stops adding the two together")
    conn = db()
    with m.app.test_request_context():
        band = m.management_overview(conn, "month", today)
    conn.close()
    labels = {c["label"]: c for c in band}
    s.check("there is a figure for what has run out", "Not covered" in labels,
            detail=str(list(labels))[:130])
    s.check("and a separate one for what is about to", "Insurance expiring" in labels)
    s.check("the lapsed one counts the lapsed policy",
            labels["Not covered"]["value"] >= 1,
            detail=str(labels["Not covered"]["value"]))
    # The invariant, rather than the two happening to differ today. Adding
    # ANOTHER lapsed policy must move only the lapsed figure -- before this
    # both came from `expiry_date < soon`, so one policy sat in both at once
    # and a lapse made the "expiring" number go up as well.
    was_lapsed = labels["Not covered"]["value"]
    was_expiring = labels["Insurance expiring"]["value"]
    _policy("Generali", (today - timedelta(days=400)).isoformat())
    conn = db()
    with m.app.test_request_context():
        band2 = {c["label"]: c for c in m.management_overview(conn, "month", today)}
    conn.close()
    s.check("a second lapsed policy moves the lapsed figure",
            band2["Not covered"]["value"] == was_lapsed + 1,
            detail=f"{was_lapsed} -> {band2['Not covered']['value']}")
    s.check("and leaves the expiring one alone",
            band2["Insurance expiring"]["value"] == was_expiring,
            detail=f"{was_expiring} -> {band2['Insurance expiring']['value']} "
                   "-- one policy used to land in both figures")
    # Taken away again. It exists only to prove that figure moves on its own,
    # and leaving it would change which policy is the WORST lapsed one -- so
    # every check below would be about this fixture instead of the one they
    # were written for.
    conn = db()
    conn.execute("DELETE FROM insurance_policies WHERE provider = ?",
                 (TAG + " Generali",))
    conn.commit()
    conn.close()

    s.section("A lapsed policy is on the owner's home page")
    conn = db()
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    conn.close()
    cov = [w for w in warnings if "run out" in w["title"]]
    s.check("it is there", bool(cov), detail=str([w["title"] for w in warnings])[:130])
    s.check("as a blocker", cov and cov[0]["severity"] == "blocker",
            detail=str(cov[0]["severity"]) if cov else "")
    s.check("naming the insurer and what it covered",
            cov and TAG + " Allianz" in cov[0]["detail"]
            and "liability" in cov[0]["detail"],
            detail=str(cov[0]["detail"])[:110] if cov else "")
    s.check("and saying why it matters for this house",
            cov and "open to the public" in cov[0]["detail"],
            detail=str(cov[0]["detail"])[:110] if cov else "")

    s.section("A lapsed VEHICLE policy is left to the vehicle check")
    conn = db()
    conn.execute("INSERT INTO vehicles (name, created_at) VALUES (?, ?)",
                 (TAG + " Van", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    van = conn.execute("SELECT id FROM vehicles WHERE name = ?", (TAG + " Van",)).fetchone()["id"]
    conn.close()
    _policy("Vanassur", (today - timedelta(days=5)).isoformat(),
            coverage="vehicle", vehicle_id=van)

    conn = db()
    with m.app.test_request_context():
        all_lapsed = m.lapsed_cover(conn, today)
        without = m.lapsed_cover(conn, today, include_vehicles=False)
        warnings = m.owner_home_warnings(conn, today)
    conn.close()
    s.check("it is a lapsed policy", any(TAG + " Vanassur" == c["policy"]["provider"]
                                        for c in all_lapsed),
            detail=str(len(all_lapsed)))
    s.check("but not one the home page counts as insurance",
            not any(TAG + " Vanassur" == c["policy"]["provider"] for c in without),
            detail="one lapsed van policy would otherwise put two lines on the "
                   "panel about the same problem")
    s.check("because the vehicle check has it instead",
            any("not legal to drive" in w["title"] for w in warnings),
            detail=str([w["title"] for w in warnings])[:130])

    s.section("It becomes a task that closes itself")
    conn = db()
    with m.app.test_request_context():
        found, _dropped = m.watch_task_findings(conn, today)
    conn.close()
    ours = [f for f in found if f[0] == "policy" and TAG in f[1]]
    s.check("the lapsed and expiring ones are findings", len(ours) >= 2,
            detail=f"{len(ours)}: {[f[1] for f in ours][:2]}")
    s.check("the lapsed one is high priority",
            any(f[4] == "high" for f in ours if "run out" in f[1]),
            detail=str([(f[1][:40], f[4]) for f in ours])[:130])
    s.check("the one merely expiring is not",
            all(f[4] == "normal" for f in ours if "runs out soon" in f[1]),
            detail="if everything is high priority nothing is")
    s.check("no date is in the title, which is the dedupe key",
            all(str(today.year) not in f[1] for f in ours),
            detail="a countdown there raises a fresh task every morning")
    s.check("and the vehicle policy is not among them",
            not any("Vanassur" in f[1] for f in ours),
            detail=str([f[1] for f in ours])[:130])

    conn = db()
    with m.app.test_request_context():
        m.generate_watch_tasks(conn, today)
    conn.commit()
    made = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE origin = ? AND title LIKE ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, TAG + "%")).fetchone()["c"]
    conn.close()
    s.check("tasks are raised", made >= 2, detail=str(made))

    conn = db()
    conn.execute("UPDATE insurance_policies SET expiry_date = ? WHERE provider = ?",
                 ((today + timedelta(days=400)).isoformat(), TAG + " Allianz"))
    conn.commit()
    with m.app.test_request_context():
        m.generate_watch_tasks(conn, today)
    conn.commit()
    left = conn.execute(
        "SELECT COUNT(*) AS c FROM tasks WHERE origin = ? AND title LIKE ? AND status != 'done'",
        (m.WATCH_TASK_ORIGIN, TAG + " Allianz%")).fetchone()["c"]
    conn.close()
    s.check("and renewing it closes its own task", left == 0,
            detail=f"{left} still open — nothing in this set has a done action "
                   "of its own")

    conn = db()
    with m.app.test_request_context():
        warnings = m.owner_home_warnings(conn, today)
    conn.close()
    s.check("and it comes off the home page",
            not [w for w in warnings if "run out" in w["title"]],
            detail=str([w["title"] for w in warnings])[:130])

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
