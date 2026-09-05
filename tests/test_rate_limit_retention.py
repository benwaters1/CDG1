"""An IP address kept for ever, to enforce a limit measured in minutes.

`submission_log` is the rate limiter. Every entry is (ip_address, action,
when), written on every attempt at a public form — a booking request, a
newsletter sign-up, a password reset, a promo code, a look at what is free —
and on every failed PIN at the kitchen door.

Every one of those limits looks at the LAST HOUR. `rate_limited` defaults to
`window_hours=1` and not one of its twenty-odd callers passes anything else;
the PIN lockout looks at ten minutes. So a row is load-bearing for sixty
minutes and is then a visitor's IP address, kept indefinitely, for nothing.

WHICH THE HOUSE ALREADY KNOWS BETTER THAN. `run_health_notes_purge_job` clears
six kinds of data on a timer — health notes, dead enquiries, the police
register, stale access needs, spent door codes, old guest messages — each with
a retention the privacy notice states. This table was not on the list, and it
is the only one holding an identifier for people who never became guests at
all: somebody who opened the availability calendar and left.

SEVEN DAYS, NOT TWO HOURS, and the suite insists on the reason. A rate limit
whose history is deleted underneath it stops being a rate limit; a week costs
almost nothing beside an attacker learning the counter resets at lunchtime.
The point is retention with a reason rather than retention by omission, which
is the difference the notice has to be able to claim.
"""
from datetime import timedelta

from _harness import Suite, clients, db

import _harness

m = _harness.m
TAG = "ratetest-"


def _cleanup(conn):
    conn.execute("DELETE FROM submission_log WHERE action LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("An address kept for ever, for a limit measured in minutes")
    _oc, _ec, _owner, _emp = clients()
    conn = db()
    _cleanup(conn)
    now = m.datetime.now(m.timezone.utc)

    def entry(action, days_ago, ip="203.0.113.9"):
        conn.execute(
            "INSERT INTO submission_log (ip_address, action, created_at) "
            "VALUES (?, ?, ?)",
            (ip, TAG + action, (now - timedelta(days=days_ago)).isoformat()))
        conn.commit()

    entry("fresh", 0)
    entry("yesterday", 1)
    entry("last-week", 6)
    entry("old", 30)
    entry("ancient", 400)

    def mine():
        return {r["action"].replace(TAG, "") for r in conn.execute(
            "SELECT action FROM submission_log WHERE action LIKE ?",
            (TAG + "%",))}

    s.section("Every limit looks at the last hour")
    import inspect
    src = inspect.getsource(m.rate_limited)
    s.check("the default window is an hour", "window_hours=1" in src,
            detail=src.split("\n")[0])
    s.check("and no caller asks for longer",
            "window_hours=" not in open("app.py", encoding="utf-8").read()
            .replace("def rate_limited(conn, action, limit, window_hours=1):", "")
            .replace("timedelta(hours=window_hours)", ""),
            detail="if one did, the retention below would have to cover it")
    s.check("the PIN lockout is shorter still",
            m.PIN_LOCKOUT_MINUTES <= 60, detail=str(m.PIN_LOCKOUT_MINUTES))

    s.section("So a week is kept and everything older goes")
    s.check("a week is the retention", m.SUBMISSION_LOG_KEEP_DAYS == 7,
            detail=str(m.SUBMISSION_LOG_KEEP_DAYS))
    before = len(mine())
    dropped = m.purge_submission_log(conn, now=now)
    left = mine()
    s.check("today's entry survives", "fresh" in left, detail=str(sorted(left)))
    s.check("and yesterday's", "yesterday" in left)
    s.check("and one from six days ago", "last-week" in left,
            detail="a rate limit whose history is deleted underneath it stops "
                   "being a rate limit")
    s.check("a month-old address is gone", "old" not in left)
    s.check("and one from last year", "ancient" not in left,
            detail="an address kept indefinitely for a counter that stopped "
                   "reading it after sixty minutes")
    # The count it reports covers the whole table, and other suites write to
    # it too — asserting a bare 2 was asserting their rows. What is this
    # suite's to claim is that its own two went and that the total is at
    # least that.
    s.check("and it says how many it dropped",
            before - len(left) == 2
            and dropped.get("rate-limit records dropped", 0) >= 2,
            detail=f"{before - len(left)} of this suite's own, "
                   f"{dropped.get('rate-limit records dropped')} altogether")

    s.section("The nightly job does it, not a person")
    src = inspect.getsource(m.run_health_notes_purge_job)
    s.check("the purge is on the daily pass",
            "purge_submission_log" in src,
            detail="six other kinds of data are cleared here and this was not "
                   "one of them")
    s.check("beside the ones that were already there",
            all(name in src for name in
                ("purge_health_notes", "purge_dead_enquiries",
                 "purge_guest_messages")),
            detail="if the others had gone this check would pass on a job "
                   "that does almost nothing")

    s.section("It still stops the forms being flooded")
    # The limit has to survive its own purge. Written the moment before, so
    # nothing a purge could reach.
    with m.app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.7"}):
        first = m.rate_limited(conn, TAG + "guard", limit=2)
        second = m.rate_limited(conn, TAG + "guard", limit=2)
        third = m.rate_limited(conn, TAG + "guard", limit=2)
    conn.commit()
    s.check("the first two attempts are allowed",
            first is False and second is False,
            detail=f"{first}, {second}")
    s.check("and the third is refused", third is True,
            detail="the purge must not make the limiter forget inside its "
                   "own window")

    s.section("The privacy notice says what the code does")
    notice = " ".join(m.app.test_client().get("/privacy")
                      .get_data(as_text=True).split())
    s.check("the record is named at all",
            "stops the forms being flooded" in notice)
    s.check("with the retention the code actually uses",
            f"deleted after {m.SUBMISSION_LOG_KEEP_DAYS} days" in notice
            or "deleted after seven days" in notice,
            detail="the notice is a set of testable claims about this code")
    s.check("saying it stops counting after an hour",
            "stops counting after an hour" in notice,
            detail="which is the whole reason keeping it longer was pointless")
    s.check("and that nothing is joined to it",
            "it is not looked up against anybody" in notice,
            detail="claiming less than it does is safe; claiming more is what "
                   "makes a guest stop believing the rest of the notice")

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
