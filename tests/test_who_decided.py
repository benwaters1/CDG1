"""Four decisions the house records, each with nobody's name on it.

The same pattern the caution flag had: a `*_by_user_id` written by the route
that makes the decision, and no query anywhere joining `users` to turn it into
a name. Nineteen tables carry one; these four are the rest of the ones that
decide money or speak for the house.

    pos_closures.closed_by_user_id            who cashed up
    breakages.decided_by_user_id              who decided a guest pays
    mileage_claims.decided_by_user_id         who approved the journey
    guest_feedback.acknowledged_by_user_id    who answered the complaint

On a till closure "who counted this" is the question an accountant asks first.
On a breakage it is a judgement about somebody else's money. On a complaint it
is somebody speaking for the house. Every one has been anonymous.

AND THE REPLY-TIMES PAGE WAS HALF A PAGE. It reported an average wait and
listed only the people still waiting — so the average had nothing behind it,
and no reply that actually happened appeared anywhere. The answered half is
now on it, which is also the only place the fourth name could go.
"""
from datetime import timedelta

from _harness import Suite, clients, db, ensure_room

import _harness

m = _harness.m
TAG = "whotest-"


def _cleanup(conn):
    conn.execute("DELETE FROM pos_closures WHERE period LIKE ?", ("2098-%",))
    conn.execute("DELETE FROM breakages WHERE what LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mileage_claims WHERE reason LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM guest_feedback WHERE guest_name LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM users WHERE email LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("Four decisions that had nobody's name on them")
    oc, _ec, owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)
    today = m.house_today()

    conn.execute(
        """INSERT INTO users (name, email, password_hash, role, status, created_at)
           VALUES (?, ?, 'x', 'employee', 'active', ?)""",
        (TAG + "Decider", TAG + "d@example.invalid", now.isoformat()))
    conn.commit()
    who = conn.execute("SELECT id FROM users WHERE email = ?",
                       (TAG + "d@example.invalid",)).fetchone()["id"]

    # ------------------------------------------------------- the cash-up
    s.section("Who cashed up")
    conn.execute(
        """INSERT INTO pos_closures (kind, period, gross_total, discount_total,
                   service_total, taken_total, vat_json, by_method_json,
                   ticket_count, covers, perpetual_total, prev_hash, hash,
                   closed_by_user_id, closed_at)
           VALUES ('day', '2098-01-01', 1200.0, 0, 0, 1200.0, '{}', '{}',
                   14, 26, 99000.0, 'x', 'y', ?, ?)""",
        (who, now.isoformat()))
    conn.commit()
    body = oc.get("/admin/pos/journal").get_data(as_text=True)
    s.check("the closure is on the page", "2098-01-01" in body)
    s.check("with the name of whoever counted it", TAG + "Decider" in body,
            detail="the question an accountant asks first, and it had no "
                   "answer on the page that shows the closure")

    conn.execute("UPDATE pos_closures SET closed_by_user_id = NULL "
                 "WHERE period = '2098-01-01'")
    conn.commit()
    s.check("and one nobody signed says so rather than blaming somebody",
            "nobody recorded" in oc.get("/admin/pos/journal").get_data(as_text=True),
            detail="rows written before the column existed have no name")

    # ------------------------------------------------------ the breakage
    s.section("Who decided a guest should pay")
    room = ensure_room()["id"]
    conn.execute(
        """INSERT INTO breakages (what, room_id, found_on, replacement_cost,
                   charge_decision, charged_amount, decided_by_user_id,
                   created_at)
           VALUES (?, ?, ?, 180.0, 'charged', 180.0, ?, ?)""",
        (TAG + " Broken lamp", room, today.isoformat(), who, now.isoformat()))
    conn.commit()
    rows = m.breakage_recovery(conn)["rows"]
    mine = next((r for r in rows if str(r["what"]).startswith(TAG)), None)
    s.check("the breakage is found", mine is not None,
            detail=str([r["what"] for r in rows])[:120])
    s.check("carrying who decided it",
            mine and mine["decided_by"] == TAG + "Decider",
            detail=str(mine and mine["decided_by"]))
    page = oc.get("/management/breakage-recovery").get_data(as_text=True)
    s.check("and the page prints the name", TAG + "Decider" in page,
            detail="a judgement about somebody else's money with nobody's "
                   "name on it is one nobody can be asked about")

    # ------------------------------------------------------- the mileage
    s.section("Who approved the journey")
    conn.execute(
        """INSERT INTO mileage_claims (user_id, travelled_on, from_place,
                   to_place, reason, kilometres, rate, amount, status,
                   decided_by_user_id, created_at)
           VALUES (?, ?, 'Château', 'Foix', ?, 40, 0.35, 14.0, 'approved',
                   ?, ?)""",
        (who, today.isoformat(), TAG + " supplies run", who, now.isoformat()))
    conn.commit()
    mile = oc.get("/mileage").get_data(as_text=True)
    s.check("the claim is on the page", TAG + " supplies run" in mile)
    s.check("with the name of whoever approved it",
            mile.count(TAG + "Decider") >= 2,
            detail="two names on that row now: who made the journey and who "
                   "decided to pay for it, which are different people and "
                   "were the same blank")

    # ------------------------------------------------------ the complaint
    s.section("Who answered the complaint")
    conn.execute(
        """INSERT INTO guest_feedback (guest_name, rating, comment,
                   submitted_at, acknowledged_at, acknowledged_by_user_id)
           VALUES (?, 2, ?, ?, ?, ?)""",
        (TAG + " Unhappy", TAG + " the shutters banged all night",
         (now - timedelta(days=4)).isoformat(), (now - timedelta(days=2)).isoformat(),
         who))
    conn.commit()
    data = m.review_reply_times(conn, days=30)
    answered = [a for a in data["answered"] if str(a["who"]).startswith(TAG)]
    s.check("it counts as answered", answered, detail=str(data["answered"])[:120])
    s.check("with the name against it",
            answered and answered[0]["answered_by"] == TAG + "Decider",
            detail=str(answered[0] if answered else None))
    s.check("and how long it took",
            answered and answered[0]["days"] == 2,
            detail=str(answered[0]["days"] if answered else None))

    s.section("The reply-times page shows the replies as well as the waiting")
    rt = oc.get("/admin/reply-times").get_data(as_text=True)
    s.check("there is an answered section at all",
            'section-heading">Answered' in rt,
            detail="the page reported an average wait and listed only the "
                   "people still waiting, so the average had nothing behind it")
    s.check("the answered complaint is on it", TAG + " Unhappy" in rt)
    s.check("with who answered it", TAG + "Decider" in rt)

    conn.execute("UPDATE guest_feedback SET acknowledged_by_user_id = NULL "
                 "WHERE guest_name = ?", (TAG + " Unhappy",))
    conn.commit()
    s.check("and one nobody signed says nobody recorded",
            "nobody recorded" in oc.get("/admin/reply-times").get_data(as_text=True),
            detail="attaching the nearest name would be worse than a blank")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
