# -*- coding: utf-8 -*-
"""Every form in the app, submitted empty, and none of them may crash.

A form arrives empty more often than anyone expects: a field renamed in a
template, a double submit after the first one cleared the page, a phone whose
connection drops mid-post, a bot. The right answer is a message and a
redirect. The wrong answer is a 500, which the person in front of it reads as
the château's software breaking.

One route did exactly that. The walk-in desk form re-parses its dates
strictly on POST — deliberately, with a comment saying that a missing date is
"a mistake to report, not something to fill in behind somebody's back" — and
then reported it by re-rendering a template that calls .isoformat() on the
date it had just decided was missing. So every careful message that route
composes ("Enter both dates", "Choose a room", "Put a name on it") was
unreachable the moment a date would not parse: the front desk got a stack
trace instead of the sentence telling them what to fix.

The sweep below is the guard, not the single fix — this is the class of bug
where finding one means finding it once, and a sweep means finding it every
time. Every parameterless POST is submitted empty and must answer, not raise.
"""
from _harness import Suite, clients, db
import _harness

m = _harness.m

# A POST that legitimately reaches something the suite must never touch. The
# harness stands Pennylane down at import and that guard raising IS the
# correct behaviour, so it would otherwise read as a crash here.
NOT_OURS = {"sync_pennylane"}


def run():
    s = Suite("Empty forms answer rather than crash")
    oc, _ec, _owner, _emp = clients()

    rules = []
    for r in m.app.url_map.iter_rules():
        if "POST" not in (r.methods or set()):
            continue
        if r.arguments or r.endpoint in ("login", "logout") or r.endpoint in NOT_OURS:
            continue
        rules.append((r.endpoint, str(r.rule)))
    rules.sort()

    s.check("there are forms to submit", len(rules) > 100,
            detail="%d found" % len(rules))

    crashed = []
    for endpoint, path in rules:
        try:
            code = oc.post(path, data={}).status_code
        except Exception as exc:
            crashed.append("%s (%s)" % (endpoint, type(exc).__name__))
            continue
        if code >= 500:
            crashed.append("%s (HTTP %d)" % (endpoint, code))
    s.check("no form crashes when it arrives empty",
            not crashed,
            detail="%s — a 500 on an empty post is the person in front of it "
                   "reading a stack trace instead of the sentence telling "
                   "them what to fix" % crashed)

    # The specific one, by name, so the sweep above cannot go quiet by
    # accident and take this with it. A real room id, because "Choose a
    # room" is checked first and would answer instead of the date.
    conn = db()
    room = conn.execute(
        "SELECT id FROM rooms WHERE active = 1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    s.check("there is a room to book", room is not None)
    rid = str(room["id"]) if room else "1"

    resp = oc.post("/admin/bookings/walk-in", data={
        "room_id": rid, "guest_name": "Nobody",
        "arrival_date": "", "departure_date": ""})
    s.check("the walk-in desk answers a missing date instead of crashing",
            resp.status_code < 500, detail="HTTP %d" % resp.status_code)
    page = resp.get_data(as_text=True)
    s.check("and says which mistake it was",
            "Enter both dates" in page, detail=page[:200])

    # And it keeps what was typed. Re-rendering an empty form is its own
    # small cruelty at a desk with somebody waiting.
    resp = oc.post("/admin/bookings/walk-in", data={
        "room_id": rid, "guest_name": "Marguerite",
        "arrival_date": "2026-12-01", "departure_date": ""})
    page = resp.get_data(as_text=True)
    s.check("keeping the fields that were filled in",
            resp.status_code < 500 and "Marguerite" in page
            and "2026-12-01" in page,
            detail="HTTP %d" % resp.status_code)
    return s
