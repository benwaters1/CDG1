"""What the house is tied into, and the last day it can get out.

Insurance knew its renewal date and a fixed-term contract knew when it ended.
The laundry, the waste collection, the alarm monitoring, the internet, the
card terminal and eleven software subscriptions knew nothing — they were
recurring_costs rows, which say when the money goes out and nothing about
whether the house is still free to stop it.

THE DATE THAT MATTERS IS NOT THE RENEWAL DATE. An agreement that rolls over
on 1 January with three months' notice has to be cancelled by 1 October. By
the time the renewal is close enough to be noticed, the decision was taken
three months ago by nobody. Everything here is about that gap:

  - the list sorts by the DEADLINE, not the renewal;
  - the warning fires on the deadline, not the renewal;
  - and once the deadline has passed the agreement stops being a job and
    becomes a fact. It says "too late", and it comes OFF the warnings —
    because a list that keeps asking for a cancellation nobody can make any
    more is a list people stop opening, including on the morning it has
    something real on it.

"Keeping it" is a decision and stops the asking, exactly like giving notice.
The thing being asked for is a decision, not an outcome.
"""
from datetime import timedelta

from _harness import Suite, clients, db, house_today

import _harness

m = _harness.m
TAG = "ZZAGREE"


def _cleanup(conn):
    conn.execute("DELETE FROM supplier_agreements WHERE what LIKE ?", (TAG + "%",))
    conn.commit()


def run():
    s = Suite("what we are tied into")
    today = house_today()
    now = m.datetime.now(m.timezone.utc).isoformat()
    conn = db()
    oc, ec, _owner, _emp = clients()
    _cleanup(conn)

    def add(what, renews_in, notice, *, auto=1, value=None, decided=None):
        conn.execute(
            """INSERT INTO supplier_agreements (what, renews_on, auto_renews,
                       notice_days, annual_value, decided_at, decision,
                       active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (TAG + " " + what, (today + timedelta(days=renews_in)).isoformat(),
             auto, notice, value, decided, "keep" if decided else None, now))
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    # Renews in 300 days with 90 days' notice: the deadline is 210 days off.
    far = add("Alarm monitoring", 300, 90, value=1200)
    # Renews in 100 days with 90 days' notice: 10 days to decide. Urgent, and
    # a diary sorted by renewal date would have it near the BOTTOM.
    urgent = add("Linen hire", 100, 90, value=8400)
    # Renews in 30 days with 90 days' notice: the window shut 60 days ago.
    late = add("Waste collection", 30, 90, value=2600)
    # Renews in 40 days, no notice period at all.
    simple = add("Card terminal", 40, 0, value=600)
    # Ends in 20 days and does not roll on.
    ending = add("Scaffolding", 20, 30, auto=0, value=5000)
    # Already decided.
    settled = add("Internet", 80, 30, value=900, decided=now)
    # Renewal already gone by.
    passed = add("Window cleaning", -10, 30, value=400)
    # Committed before anything goes through a test client. An open write
    # transaction on this connection locks the database against the request's
    # own connection, and every page in the suite comes back a 500 that has
    # nothing to do with the code being tested.
    conn.commit()

    rows = {a["row"]["what"]: a for a in m.supplier_agreements(conn, today)}

    s.section("The deadline is worked out from the notice period")
    s.check("300 days out with 90 days' notice leaves 210",
            rows[TAG + " Alarm monitoring"]["days_left"] == 210,
            detail=str(rows[TAG + " Alarm monitoring"]["days_left"]))
    s.check("and no notice period means the deadline is the renewal",
            rows[TAG + " Card terminal"]["days_left"] == 40,
            detail=str(rows[TAG + " Card terminal"]["days_left"]))

    s.section("The list is ordered by the deadline, not the renewal")
    # This is the entire point. Sorted by renewal date the linen would sit
    # eighth of nine and read as next year's problem, with ten days left on it.
    order = [a["row"]["what"] for a in m.supplier_agreements(conn, today)
             if a["row"]["what"].startswith(TAG)]
    s.check("the waste collection, whose window has already shut, comes first",
            order[0] == TAG + " Waste collection",
            detail=" | ".join(order))
    s.check("the linen with ten days left beats the alarm with two hundred",
            order.index(TAG + " Linen hire") < order.index(TAG + " Alarm monitoring"),
            detail=" | ".join(order))
    # The demonstration, rather than an assertion about one position: the
    # card terminal renews SOONER than the linen (40 days against 100) and
    # has to come AFTER it, because the linen's notice window shuts in ten
    # days and the card terminal's has not started to close. A diary ordered
    # by renewal date puts these two the other way round, which is exactly
    # how the expensive one gets missed.
    by_renewal = sorted(
        (a for a in m.supplier_agreements(conn, today)
         if a["row"]["what"].startswith(TAG) and a["renews"]),
        key=lambda a: a["renews"])
    s.check("the card terminal renews before the linen",
            [a["row"]["what"] for a in by_renewal].index(TAG + " Card terminal")
            < [a["row"]["what"] for a in by_renewal].index(TAG + " Linen hire"))
    s.check("and yet comes after it on this page",
            order.index(TAG + " Linen hire") < order.index(TAG + " Card terminal"),
            detail=" | ".join(order) + " — ordered by renewal date these two "
                   "swap, and the one with ten days left drops below the one "
                   "with forty")

    s.section("Past the notice date is not the same as due soon")
    s.check("the waste collection is too late",
            rows[TAG + " Waste collection"]["state"] == "too_late",
            detail=rows[TAG + " Waste collection"]["state"])
    s.check("and the linen is still open",
            rows[TAG + " Linen hire"]["state"] == "open")
    s.check("a fixed-term agreement past its notice date is running out, "
            "not locked in",
            rows[TAG + " Scaffolding"]["state"] == "running_out",
            detail="it does not roll on, so 'too late' would be a lie — "
                   "there is nothing to get out of")
    s.check("and one whose renewal has been and gone has rolled on",
            rows[TAG + " Window cleaning"]["state"] == "rolled_on",
            detail="the dates need rewriting; saying 'due soon' about it "
                   "would be asking for a decision on the wrong year")

    s.section("Only the ones somebody can still act on are asked about")
    to_decide = {a["row"]["what"]
                 for a in m.agreements_to_decide(conn, within_days=60, today=today)}
    s.check("the linen, with ten days left, is asked about",
            TAG + " Linen hire" in to_decide)
    s.check("the card terminal, forty days out, is asked about",
            TAG + " Card terminal" in to_decide)
    s.check("the waste collection is NOT",
            TAG + " Waste collection" not in to_decide,
            detail="asking every morning for a cancellation nobody can make "
                   "any more is how a list stops being read, including on "
                   "the morning it has something real on it")
    s.check("nor is the alarm, two hundred days off",
            TAG + " Alarm monitoring" not in to_decide,
            detail="a warning that fires seven months early is furniture")
    s.check("nor one already decided", TAG + " Internet" not in to_decide,
            detail="'keeping it' is an answer")

    s.section("A wider window reaches further")
    wide = {a["row"]["what"]
            for a in m.agreements_to_decide(conn, within_days=250, today=today)}
    s.check("the alarm appears at 250 days", TAG + " Alarm monitoring" in wide,
            detail="the window is a window, not a filter that drops things")

    s.section("The warning fires on the deadline, not the renewal")
    raised, _dropped = m.watch_task_findings(conn, today=today)
    ours = [f for f in raised if f[0] == "agreement" and TAG in f[1]]
    titles = {f[1] for f in ours}
    s.check("the linen is raised",
            any(TAG + " Linen hire" in t for t in titles), detail=str(titles))
    s.check("and the note gives the day notice has to be given by",
            any("Notice has to be given by" in f[2] for f in ours),
            detail=str([f[2] for f in ours][:1]))
    s.check("the due date on the task is the deadline, not the renewal",
            any(f[3] == rows[TAG + " Linen hire"]["deadline"].isoformat()
                for f in ours if TAG + " Linen hire" in f[1]),
            detail="a task due on the renewal date is a task that comes up "
                   "three months after it could have been acted on")
    s.check("nothing too late is raised",
            not any(TAG + " Waste collection" in t for t in titles),
            detail=str(titles))
    s.check("it is a registered kind, so it can be routed and turned off",
            "agreement" in m.WATCH_TASK_KINDS)

    s.section("Deciding stops the asking")
    r = oc.post("/management/agreements",
                data={"what": "decide", "agreement_id": str(urgent),
                      "decision": "keep"},
                follow_redirects=True)
    still = {a["row"]["what"]
             for a in m.agreements_to_decide(conn, within_days=60, today=today)}
    s.check("keeping it counts as an answer", TAG + " Linen hire" not in still,
            detail="what is being asked for is a decision, not an outcome")
    s.check("and it says which decision", "Keeping it" in r.get_data(as_text=True))

    s.section("And undeciding starts it again")
    oc.post("/management/agreements",
            data={"what": "reopen", "agreement_id": str(urgent)},
            follow_redirects=True)
    back = {a["row"]["what"]
            for a in m.agreements_to_decide(conn, within_days=60, today=today)}
    s.check("it is asked about again", TAG + " Linen hire" in back,
            detail="somebody who ticked the wrong row has to be able to "
                   "put it back")

    s.section("The page")
    body = oc.get("/management/agreements").get_data(as_text=True)
    s.check("the owner can open it", TAG + " Linen hire" in body)
    # The whole sentence, not the phrase. "Too late this time" is one of
    # the counted chips at the top of the page, so a bare "Too late" was
    # satisfied by the chip and would not have noticed the ROW going quiet.
    s.check("it says plainly when it is too late",
            "It has rolled on for another term" in body,
            detail="a row that only says 'rolls over soon' about something "
                   "already renewed reads as a job somebody can still do")
    s.check("and what a rolled-on agreement needs",
            "these dates need rewriting" in body)
    r = ec.get("/management/agreements", follow_redirects=False)
    s.check("an employee cannot", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    s.section("Adding one through the page")
    oc.post("/management/agreements",
            data={"what_it_is": TAG + " Coffee machine",
                  "renews_on": (today + timedelta(days=200)).isoformat(),
                  "notice_days": "60", "annual_value": "1 200,50",
                  "auto_renews": "1"},
            follow_redirects=True)
    made = conn.execute(
        "SELECT * FROM supplier_agreements WHERE what = ?",
        (TAG + " Coffee machine",)).fetchone()
    s.check("it is saved", made is not None)
    s.check("with the notice period", made and made["notice_days"] == 60,
            detail=str(made["notice_days"]) if made else "")
    s.check("and a price typed the French way is read",
            made and abs((made["annual_value"] or 0) - 1200.50) < 0.01,
            detail=str(made["annual_value"]) if made else "")
    st = m.agreement_state(made, today)
    s.check("its deadline is 140 days off, not 200", st["days_left"] == 140,
            detail=str(st["days_left"]))

    s.section("A price written the way the owner writes it")
    # Found here, but it is parse_money, which nineteen forms use. The comma
    # was already handled -- somebody hit that and fixed it. The thousands
    # separator is a SPACE in French, and it was not, so every amount over a
    # thousand came back None. None means "left empty" here, so the form
    # saved with the field silently blank and said nothing.
    for typed, want in (("1 200,50", 1200.50), ("1200,50", 1200.50),
                        ("12 345 678,90", 12345678.90),
                        ("1 000,25", 1000.25), ("2400", 2400.0)):
        got = m.parse_money(typed)
        s.check(f"{typed!r} reads as {want}",
                got is not None and abs(got - want) < 0.005, detail=str(got))
    for junk in ("45 euros", "", "   ", "abc", "-5"):
        s.check(f"{junk!r} is still not a number", m.parse_money(junk) is None,
                detail="widening what counts as a number must not start "
                       "accepting prose")

    s.section("An agreement with no useful date says so rather than nothing")
    conn.execute(
        """INSERT INTO supplier_agreements (what, renews_on, auto_renews,
                   notice_days, active, created_at)
           VALUES (?, 'not a date', 1, 30, 1, ?)""",
        (TAG + " Mystery", now))
    conn.commit()
    mystery = [a for a in m.supplier_agreements(conn, today)
               if a["row"]["what"] == TAG + " Mystery"]
    s.check("it does not crash the page", len(mystery) == 1)
    s.check("it is marked undated", mystery and mystery[0]["state"] == "undated")
    s.check("and it sorts last, not first",
            m.supplier_agreements(conn, today)[-1]["row"]["what"] == TAG + " Mystery",
            detail="undated at the top would push a real deadline down the "
                   "page; undated at the bottom still says nobody has dated it")
    s.check("and it is not asked about, because there is nothing to ask",
            TAG + " Mystery" not in {
                a["row"]["what"]
                for a in m.agreements_to_decide(conn, within_days=9999,
                                                today=today)})

    s.section("It is reachable")
    nav = oc.get("/").get_data(as_text=True)
    nav = nav[:nav.find("</nav>")] if "</nav>" in nav else nav
    s.check("in the nav", "/management/agreements" in nav)
    s.check("and in the palette",
            "supplier_agreements_page" in {e for _l, e, _k in m.PALETTE_PAGES})

    _cleanup(conn)
    conn.close()
    return s


if __name__ == "__main__":
    print(run().report())
