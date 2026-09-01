"""The checkout list says to look for damage, and there was nowhere to write it.

CHECKOUT_CHECKLIST has said "Inspect for damage or missing items" since it
was written. Somebody walks the room, finds a broken lamp, and the app had
no place to put that — so it was remembered, or it was not.

NOT room_issues, which already exists and is a different thing. A fault is
something broken that needs fixing and is nobody's fault: the shower drips,
a plumber comes. A breakage is something a GUEST broke or took, and it
carries a question a fault never does.

Three things this holds:

  - RECORDED ALWAYS, CHARGED SOMETIMES. Three broken glasses across a
    season is wear; three in one stay is a conversation. Neither is visible
    unless both are written down, so a breakage the house lets go stays on
    the record.

  - THE DECISION IS THE OWNER'S. Charging a guest for damage is the same
    shape as refusing a refund, and the house's stated position is that
    those are decisions somebody makes, never rules. Staff record; the
    owner decides.

  - AND DECIDING IS NOT TAKING. Marking one charged writes down what the
    house decided. It moves no money — that is a line on a bill, made
    deliberately, exactly as a refund is.
"""
from datetime import date, timedelta

from _harness import Suite, clients, db, flashes

import _harness

m = _harness.m
TAG = "ZZBRK"


def _cleanup(conn):
    conn.execute("DELETE FROM breakages WHERE what LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Breakages")
    conn = db()
    oc, ec, _owner, emp = clients()
    _cleanup(conn)
    today = date.today()

    s.section("The checkout list still says to look")
    # If this ever stops saying it, the page below is answering a question
    # nobody is being asked to consider.
    s.check("inspecting for damage is on the turnover checklist",
            any("damage" in item.lower() for item in m.CHECKOUT_CHECKLIST),
            detail=str(m.CHECKOUT_CHECKLIST))

    s.section("Anybody turning a room round can write one down")
    # The person who finds the broken lamp is whoever is doing the room,
    # not the owner.
    r = ec.post("/management/breakages",
                data={"what": TAG + " bedside lamp", "replacement_cost": "45",
                      "note": "shade cracked"}, follow_redirects=True)
    row = conn.execute("SELECT * FROM breakages WHERE what LIKE ?",
                       (TAG + "%",)).fetchone()
    s.check("an employee can record one", row is not None,
            detail=str(flashes(r)))
    s.check("with what it costs to replace",
            row and row["replacement_cost"] == 45.0,
            detail=str(row["replacement_cost"]) if row else "")
    s.check("and it starts undecided",
            row and row["charge_decision"] == "undecided",
            detail=str(row["charge_decision"]) if row else "")

    s.section("Without a stay attached, if there is none")
    # A breakage found in a room nobody has been in belongs to no stay, and
    # guessing would put it on the wrong guest.
    s.check("the stay is optional", row and row["booking_id"] is None,
            detail=str(row["booking_id"]) if row else "")
    before = conn.execute("SELECT COUNT(*) AS c FROM breakages "
                          "WHERE what LIKE ?", (TAG + "%",)).fetchone()["c"]
    r = ec.post("/management/breakages", data={"what": ""},
                follow_redirects=True)
    s.check("but saying nothing at all is refused",
            any("what was broken" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and nothing was written",
            conn.execute("SELECT COUNT(*) AS c FROM breakages WHERE what LIKE ?",
                         (TAG + "%",)).fetchone()["c"] == before)

    s.section("An employee cannot decide what the house says about it")
    r = ec.post(f"/management/breakages/{row['id']}/decide",
                data={"decision": "let_it_go"}, follow_redirects=False)
    s.check("the decision is refused", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and it is still undecided",
            conn.execute("SELECT charge_decision FROM breakages WHERE id = ?",
                         (row["id"],)).fetchone()["charge_decision"] == "undecided")

    s.section("The owner decides, and deciding is not taking")
    r = oc.post(f"/management/breakages/{row['id']}/decide",
                data={"decision": "charged", "charged_amount": "45"},
                follow_redirects=True)
    after = conn.execute("SELECT * FROM breakages WHERE id = ?",
                         (row["id"],)).fetchone()
    s.check("it is recorded as charged",
            after["charge_decision"] == "charged" and after["charged_amount"] == 45.0,
            detail=str(flashes(r)))
    s.check("with who decided and when",
            after["decided_by_user_id"] and after["decided_at"],
            detail=str(dict(after)))
    s.check("and the wording does not claim money has moved",
            any("has been taken from anybody" in f
                or "not take money" in f.lower() for f in flashes(r)),
            detail=f"{flashes(r)} — charging is a line on their bill, made "
                   "deliberately, exactly as a refund is")

    s.section("Charging without an amount is refused")
    conn.execute(
        """INSERT INTO breakages (what, found_on, created_at)
           VALUES (?, ?, ?)""",
        (TAG + " missing towels", today.isoformat(),
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()
    second = conn.execute("SELECT id FROM breakages WHERE what = ?",
                          (TAG + " missing towels",)).fetchone()["id"]
    r = oc.post(f"/management/breakages/{second}/decide",
                data={"decision": "charged"}, follow_redirects=True)
    s.check("it says how much is missing",
            any("how much" in f.lower() for f in flashes(r)),
            detail=str(flashes(r)))
    s.check("and nothing was decided",
            conn.execute("SELECT charge_decision FROM breakages WHERE id = ?",
                         (second,)).fetchone()["charge_decision"] == "undecided")

    s.section("One that is let go stays on the record")
    # The whole reason a decision is not a deletion. Three glasses across a
    # season is wear; three in one stay is a conversation.
    oc.post(f"/management/breakages/{second}/decide",
            data={"decision": "let_it_go"}, follow_redirects=True)
    still = conn.execute("SELECT * FROM breakages WHERE id = ?",
                         (second,)).fetchone()
    s.check("it is still there", still is not None)
    s.check("marked as let go rather than removed",
            still["charge_decision"] == "let_it_go",
            detail="a pattern is only visible if the ones nobody charged "
                   "for are on the list too")

    s.section("What it cost and what came back are kept apart")
    summary = m.breakage_summary(conn)
    mine = [r for r in summary["rows"] if r["what"].startswith(TAG)]
    s.check("both breakages are counted", len(mine) == 2, detail=str(len(mine)))
    # Contribution rather than the house's totals, so other suites' rows do
    # not decide this.
    cost = sum(r["replacement_cost"] or 0 for r in mine)
    recovered = sum(r["charged_amount"] or 0 for r in mine
                    if r["charge_decision"] == "charged")
    s.check("the replacement cost is counted whatever was decided",
            cost == 45.0, detail=str(cost))
    s.check("and what was recovered is a separate figure",
            recovered == 45.0, detail=str(recovered))
    s.check("never netted into one number",
            "absorbed" in summary and "recovered" in summary
            and summary["absorbed"] == round(summary["cost"]
                                             - summary["recovered"], 2),
            detail="what the house absorbs is a cost of trading; what it "
                   "recovered is a decision it made, and one figure says "
                   "neither")

    s.section("The page")
    # One still undecided, so the buttons have something to render against.
    # By this point in the suite both of the others have been decided, and
    # a page with nothing left to decide quite rightly shows no buttons.
    conn.execute(
        """INSERT INTO breakages (what, found_on, created_at)
           VALUES (?, ?, ?)""",
        (TAG + " chipped basin", today.isoformat(),
         m.datetime.now(m.timezone.utc).isoformat()))
    conn.commit()

    body = oc.get("/management/breakages").get_data(as_text=True)
    s.check("it lists them", TAG + " bedside lamp" in body)
    s.check("and offers the owner the decision", "Let it go" in body)
    staff_body = ec.get("/management/breakages").get_data(as_text=True)
    s.check("an employee sees the list without the decision buttons",
            TAG + " bedside lamp" in staff_body
            and "Let it go" not in staff_body,
            detail="recording is theirs; deciding is not")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
