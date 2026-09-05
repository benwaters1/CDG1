# -*- coding: utf-8 -*-
"""The owner was shown the puddle every morning and never the burst pipe.

readiness_checks has always known fourteen things about this deployment that
are not right, one of them a blocker: no outbound email is configured, so no
confirmation, no reminder and no campaign can leave the building. That is why
484 messages are sitting in the outbox.

The owner home said "483 held messages too old to send". It never once said
that nothing can send. Weeks of being shown the consequence, with the cause
on a page nobody has a reason to open twice — which is the exact failure the
warnings panel exists to prevent.

WHAT THIS HAS TO GET RIGHT:

  THE RIGHT THREE, NOT ALL FOURTEEN. Nothing can send email; the terms are
  still the placeholder guests agree to at booking; the public address is
  unset, so every link the app writes into an email is broken. Stripe being
  unconfigured is a decision the house has not taken rather than a fault, and
  the optional tokens are optional. A morning panel that lists everything is
  a panel nobody reads, and that failure is the whole point of this section.

  THEY MUST CLOSE THEMSELVES. Set the key and the line goes. The panel has to
  be able to be empty or it becomes furniture — there is a separate test for
  exactly that, and this must not be the thing that breaks it.

  AND THE NAMES MUST STILL MATCH. The three are keyed by the readiness
  check's own label. Rename a check and the front page would quietly stop
  carrying it, which is the same silence this is here to end — so the keys
  are checked against the live list rather than assumed.
"""
from _harness import Suite, db
import _harness

m = _harness.m


def run():
    s = Suite("The deployment's own faults reach the owner's morning")
    conn = db()

    checks = m.readiness_checks(conn)
    labels = {c["label"] for c in checks}

    # The keys are labels of real checks. A renamed check would otherwise
    # drop off the front page in silence.
    unknown = sorted(set(m.FRONT_PAGE_READINESS) - labels)
    s.check("every front-page key names a readiness check that exists",
            not unknown,
            detail="%s — renamed or removed, so the front page has quietly "
                   "stopped carrying it" % unknown)

    s.check("three of them, not all fourteen",
            len(m.FRONT_PAGE_READINESS) == 3,
            detail="%d — a morning panel that lists everything is one nobody "
                   "reads" % len(m.FRONT_PAGE_READINESS))

    with m.app.test_request_context("/"):
        warnings = m.owner_home_warnings(conn, m.house_today())
    titles = {w["title"] for w in warnings}

    # Whatever is actually unmet in this database must be on the panel, and
    # whatever is met must not be. Read from the checks rather than assumed,
    # so this says something wherever it runs.
    for check in checks:
        if check["label"] not in m.FRONT_PAGE_READINESS:
            continue
        wanted = m.FRONT_PAGE_READINESS[check["label"]]
        if check["ok"]:
            s.check("'%s' is settled, so it is not on the front page" % wanted,
                    wanted not in titles,
                    detail="a warning that cannot clear itself is furniture")
        else:
            s.check("'%s' reaches the owner's morning" % wanted,
                    wanted in titles,
                    detail="it was on the readiness page all along and on no "
                           "page anybody opens")

    # THE CLEARING HALF, PROVED RATHER THAN ASSUMED. All three are unmet in
    # this database, so the branch that takes a line OFF the panel never runs
    # on its own — and a warning that cannot clear itself is furniture, which
    # is what the empty-panel test exists to stop. So one is satisfied here
    # and the panel re-read.
    was = m.PUBLIC_BASE_URL
    try:
        m.PUBLIC_BASE_URL = "https://chateaugudanes.example"
        with m.app.test_request_context("/"):
            after = {w["title"] for w
                     in m.owner_home_warnings(conn, m.house_today())}
        s.check("setting the public address takes its line off the panel",
                "Links in guest email go nowhere" not in after,
                detail="a warning that cannot clear itself becomes furniture, "
                       "and then so does the panel")
        s.check("and leaves the other two, which are still true",
                "Nothing can send email" in after,
                detail="clearing one must not clear the rest")
    finally:
        m.PUBLIC_BASE_URL = was

    # And it links somewhere that explains, rather than just asserting.
    for w in warnings:
        if w["title"] in m.FRONT_PAGE_READINESS.values():
            s.check("'%s' links to the page that says more" % w["title"][:34],
                    "readiness" in (w["href"] or ""), detail=w["href"])

    # The cause is a blocker where the symptom is only a warning. Held mail
    # is a tidying job; nothing being able to send is a guest who booked and
    # heard nothing.
    email = [w for w in warnings if w["title"] == "Nothing can send email"]
    if email:
        s.check("and nothing being able to send is a blocker",
                email[0]["severity"] == "blocker",
                detail="a guest books and hears nothing back")

    conn.close()
    return s
