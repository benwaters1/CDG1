# -*- coding: utf-8 -*-
"""Five sparklines, thirty-five queries, and three of them on the wrong day.

The five figures across the top of the owner home each carry a seven-day
series, and each was built by asking the database once per day. Seven
questions to draw twenty-two pixels, five times over — thirty-five of the 435
queries that page ran. The rule this file already keeps for hours ("never
loop the helper, batch it") applied where it had not been.

Three of the five were also counting the wrong day, which is the part that
was wrong rather than merely slow:

  DATE(clock_in_at) READS THE UTC DATE of a timestamp stored in UTC, and the
  house is in the Ariège. Somebody clocking in at half past midnight was
  counted on the day before — every night of the year. The same fault twice
  more in the decisions series, on submitted_at and requested_at. It is the
  stamp[:10] mistake in SQL's spelling: the same wrong answer in a form the
  rule against that one does not name.

And putting the readiness checks on this page cost 74ms of scrypt, because
one of them runs the password hash to see whether the seeded password is
still in place. The front page asks for the cheap set; the readiness page
asks for everything. Omitted rather than faked — a clean pass on the seeded
password would be a lie about the only item on that list that is a way in.
"""
from datetime import timedelta

from _harness import Suite, db
import _harness

m = _harness.m
# time_entries has no free-text column to tag a fixture with, so the row is
# identified by its own timestamp — which is exactly the value under test,
# and therefore unambiguous.


def _cleanup(conn, stamp=None):
    if stamp:
        conn.execute("DELETE FROM time_entries WHERE clock_in_at = ?", (stamp,))
        conn.commit()


def run():
    s = Suite("The owner's five figures: batched, and on the house's day")
    conn = db()
    today = m.house_today()

    # The fixture's own row is removed BEFORE the baseline is read. Taking
    # the snapshot first and cleaning after meant a leftover from an earlier
    # run was already counted in it, so the bar could not be seen to move —
    # green alone, red in the full run, which is the order-dependence this
    # suite would otherwise have introduced.
    _stamp_today = m.datetime.combine(
        today, m.dtime(0, 30)).replace(tzinfo=m.LOCAL_TZ).astimezone(
            m.timezone.utc).isoformat()
    _cleanup(conn, _stamp_today)

    # ---- one query per series, not one per day ---------------------------
    seen = []
    conn.set_trace_callback(seen.append)
    figures = m.owner_home_figures(conn, today)
    conn.set_trace_callback(None)

    s.check("five figures, each with a week of history",
            len(figures) == 5
            and all(len(f["trend"]) == 7 for f in figures),
            detail=str([f["label"] for f in figures]))
    # Twelve is the real number, not a round one. A ceiling with slack in it
    # would let a single series quietly go back to a query per day — putting
    # the arrivals loop back costs exactly seven, which a ceiling of twenty
    # would have waved through.
    s.check("built in a handful of queries rather than one per day",
            len(seen) <= 12,
            detail="%d — seven questions to draw a 22px sparkline, five "
                   "times over, was thirty-five of them" % len(seen))
    s.check("and the ceiling has no slack for a loop to hide in",
            len(seen) >= 12,
            detail="%d — fewer than the ceiling, so lower it; slack is where "
                   "a series creeps back to one query per day" % len(seen))

    # ---- the day it happened, not the day UTC thought ---------------------
    #
    # A clock-in just after midnight in the Ariège is the previous day in UTC.
    # Placed at 00:30 local on purpose: that is the row DATE(clock_in_at) put
    # on yesterday's bar, every night of the year.
    staff = conn.execute(
        "SELECT id FROM users WHERE role != 'owner' LIMIT 1").fetchone()
    if not staff:
        s.check("there is somebody to clock in", False)
        conn.close()
        return s

    local_after_midnight = m.datetime.combine(
        today, m.dtime(0, 30)).replace(tzinfo=m.LOCAL_TZ)
    in_utc = local_after_midnight.astimezone(m.timezone.utc)
    s.check("half past midnight here is the day before in UTC",
            in_utc.date() != today,
            detail="if this is false the fixture proves nothing — the offset "
                   "has to actually cross midnight for the bug to show")

    stamp = in_utc.isoformat()
    _cleanup(conn, stamp)

    # ENOUGH OF THEM TO DECIDE THE BAR. The series is drawn as percentages of
    # its own tallest day, so a single extra entry cannot be seen when today
    # is already the tallest — it goes from 100 to 100. Inserting more than
    # any day in the window holds makes the answer unambiguous in both
    # directions: with the day read correctly today is the tallest bar, and
    # with DATE(clock_in_at) reading UTC, yesterday is.
    biggest = conn.execute(
        """SELECT COUNT(*) AS c FROM time_entries
            WHERE clock_in_at >= ? GROUP BY SUBSTR(clock_in_at, 1, 10)
            ORDER BY c DESC LIMIT 1""",
        ((today - timedelta(days=9)).isoformat(),)).fetchone()
    many = (biggest["c"] if biggest else 0) + 5
    for _ in range(many):
        conn.execute(
            """INSERT INTO time_entries (user_id, clock_in_at, clock_out_at)
               VALUES (?, ?, ?)""",
            (staff["id"], stamp, (in_utc + timedelta(hours=6)).isoformat()))
    conn.commit()

    after = m.owner_home_figures(conn, today)
    shifts = next(f for f in after if f["label"] == "Staff on shift")
    s.check("a shift started after midnight counts as today, not yesterday",
            shifts["trend"][-1] > shifts["trend"][-2],
            detail="today %s vs yesterday %s — DATE(clock_in_at) reads the "
                   "UTC date, so half past midnight went on yesterday's bar"
                   % (shifts["trend"][-1], shifts["trend"][-2]))
    s.check("and today is the tallest bar in the week",
            shifts["trend"][-1] == max(shifts["trend"]),
            detail=str(shifts["trend"]))

    _cleanup(conn, stamp)

    # ---- the front page does not pay for scrypt ---------------------------
    #
    # Asked of the CALLER, not only of the function. Comparing the two sets
    # proves readiness_checks can skip the slow check; it says nothing about
    # whether the owner home actually asks it to, and that is the half that
    # costs 74ms on every load.
    asked = []
    real_checks = m.readiness_checks
    m.readiness_checks = lambda c, **kw: (asked.append(kw), real_checks(c, **kw))[1]
    try:
        with m.app.test_request_context("/"):
            m.owner_home_warnings(conn, today)
    finally:
        m.readiness_checks = real_checks
    s.check("the owner home asks for the cheap set",
            asked and all(kw.get("include_slow") is False for kw in asked),
            detail="%s — the front page paying for a password hash is 74ms "
                   "on every load of the page drawn most" % asked)

    cheap = {c["label"] for c in m.readiness_checks(conn, include_slow=False)}
    full = {c["label"] for c in m.readiness_checks(conn)}
    s.check("the slow check is omitted, not faked",
            "Owner password changed" in full
            and "Owner password changed" not in cheap,
            detail="reporting a clean pass on the seeded password would be a "
                   "lie about the one item on that list that is a way in")
    s.check("and everything else is still asked",
            full - cheap == {"Owner password changed"},
            detail=str(sorted(full - cheap)))
    s.check("so the three the front page carries cost nothing extra",
            set(m.FRONT_PAGE_READINESS) <= cheap,
            detail="%s — a fourth from the slow set would put a 74ms password "
                   "hash back on every load of the owner home"
                   % sorted(set(m.FRONT_PAGE_READINESS) - cheap))

    conn.close()
    return s
