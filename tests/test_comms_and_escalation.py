"""The eight admin POSTs behind comms, chase-ups and push notifications.

None of these touch money or a guest's record, which is why they were the
last ones left untested — and also why the interesting question about them
is not "does it work" but "who is allowed to". Every one is a POST, so an
unguarded one is not a page somebody stumbles onto; it is a request anybody
can send.

A refusal check has to name the mechanism, not the outcome. Five checks in
this repo have passed for the wrong reason — a redirect to the login page
looks exactly like a refusal, and so does a 404 on a route that never
existed. So each refusal here is paired with a positive: the same request,
from somebody who IS allowed, actually changing something. If the pair does
not move together the refusal proved nothing.
"""
import json
from datetime import datetime, timedelta, timezone

from _harness import Suite, clients, db, flashes
import _harness

m = _harness.m
TAG = "ZTCOMM"


def _cleanup():
    conn = db()
    conn.execute("DELETE FROM social_posts WHERE caption LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint LIKE ?", (TAG + "%",))
    conn.execute("DELETE FROM mailbox_routing WHERE mailbox LIKE ?", (TAG.lower() + "%",))
    conn.commit()
    conn.close()


def _seed_mailbox():
    """An inbox to route, made rather than found.

    The real table is filled from MS_GRAPH_MAILBOXES, which is not set under
    test — so on this database it is empty, and the whole section below it was
    passing over nothing. A check that only runs when the environment happens
    to be configured is a check that reports green on the days it does least.
    """
    conn = db()
    conn.execute(
        "INSERT INTO mailbox_routing (mailbox, label, active, created_at) VALUES (?, ?, 1, ?)",
        (TAG.lower() + "@example.invalid", "Seeded inbox",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    row = conn.execute("SELECT * FROM mailbox_routing WHERE mailbox = ?",
                       (TAG.lower() + "@example.invalid",)).fetchone()
    conn.close()
    return row


def _one(sql, params=()):
    conn = db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def run():
    s = Suite("Comms, chase-ups and push")
    _cleanup()
    oc, ec, owner, emp = clients()
    anon = m.app.test_client()

    # ------------------------------------------------------------ social
    s.section("Putting a post on the schedule")
    r = oc.post("/management/social/new",
                data={"platform": "Instagram", "caption": TAG + " the kitchen at six",
                      "post_type": "photo", "scheduled_date": "2026-09-14",
                      "notes": "before the light goes"},
                follow_redirects=True)
    row = _one("SELECT * FROM social_posts WHERE caption = ?", (TAG + " the kitchen at six",))
    s.check("the post is saved", row is not None, detail=str(flashes(r)))
    s.check("with what was typed, not a default",
            row and row["platform"] == "Instagram" and row["post_type"] == "photo",
            detail=str(dict(row)) if row else "")
    s.check("and it knows who added it", row and row["created_by_user_id"],
            detail="an unattributed post is one nobody can ask about")

    # Measured against the count before the attempt, not against the whole
    # table. "No post anywhere has a blank caption" is a claim about every
    # other suite's fixtures as well as this one's, and it failed in a full
    # run for a reason that had nothing to do with the refusal being tested.
    before_blank = _one("SELECT COUNT(*) AS c FROM social_posts")["c"]
    r = oc.post("/management/social/new", data={"platform": "Instagram", "caption": "   "},
                follow_redirects=True)
    s.check("a post with nothing to say is refused",
            any("caption" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and nothing was written",
            _one("SELECT COUNT(*) AS c FROM social_posts")["c"] == before_blank,
            detail=f"{before_blank} before the attempt")

    s.section("Editing one")
    oc.post(f"/management/social/{row['id']}/edit",
            data={"platform": "Facebook", "caption": TAG + " the kitchen at seven",
                  "status": "scheduled"}, follow_redirects=True)
    after = _one("SELECT * FROM social_posts WHERE id = ?", (row["id"],))
    s.check("the caption changes", after["caption"] == TAG + " the kitchen at seven",
            detail=after["caption"])
    s.check("and so does the platform", after["platform"] == "Facebook")
    s.check("a real status is kept", after["status"] == "scheduled", detail=after["status"])

    oc.post(f"/management/social/{row['id']}/edit",
            data={"caption": TAG + " still here", "status": "posted-ish"},
            follow_redirects=True)
    after = _one("SELECT status FROM social_posts WHERE id = ?", (row["id"],))
    s.check("an invented status is not", after["status"] == "idea",
            detail=f"{after['status']} — anything outside the four falls back to "
                   "'idea' rather than being stored")

    s.section("Generating a batch")
    before = _one("SELECT COUNT(*) AS c FROM social_posts")["c"]
    r = oc.post("/management/social/generate", follow_redirects=True)
    s.check("it runs and says what it did", bool(flashes(r)), detail=str(flashes(r)))
    now = _one("SELECT COUNT(*) AS c FROM social_posts")["c"]
    s.check("it either scheduled something or said there was nothing to schedule",
            now > before or any("nothing new" in f.lower() for f in flashes(r)),
            detail=f"{before} -> {now}: {flashes(r)}")
    # Run twice. The point of a generator that fills a window is that it does
    # not refill it.
    again = _one("SELECT COUNT(*) AS c FROM social_posts")["c"]
    oc.post("/management/social/generate", follow_redirects=True)
    s.check("running it twice does not duplicate the window",
            _one("SELECT COUNT(*) AS c FROM social_posts")["c"] == again,
            detail="a second run added more, so the window is refilled every time "
                   "somebody presses it")

    # ------------------------------------------------------- inbox routing
    s.section("Who owns each inbox")
    seeded = _seed_mailbox()
    boxes = db()
    mailboxes = boxes.execute("SELECT id, mailbox FROM mailbox_routing ORDER BY id").fetchall()
    boxes.close()
    s.check("there is an inbox to route", seeded is not None and bool(mailboxes),
            detail="seeded, because the real ones come from MS_GRAPH_MAILBOXES "
                   "and nothing below would run without one")
    if seeded:
        mb = seeded
        payload = {f"default_user_{mb['id']}": str(emp["id"]),
                   f"label_{mb['id']}": TAG + " front desk",
                   f"on_shift_{mb['id']}": "1",
                   f"escalate_{mb['id']}": "12"}
        r = oc.post("/admin/inbox-flags/routing", data=payload, follow_redirects=True)
        got = _one("SELECT * FROM mailbox_routing WHERE id = ?", (mb["id"],))
        s.check("the owner of the inbox is set", got["default_user_id"] == emp["id"],
                detail=str(dict(got)))
        s.check("the label with it", got["label"] == TAG + " front desk")
        s.check("the on-shift routing flag", got["route_to_on_shift"] == 1)
        s.check("and the escalation window", abs((got["escalate_hours"] or 0) - 12) < 0.01,
                detail=str(got["escalate_hours"]))
        # Unticking a checkbox sends nothing at all, which is the classic way a
        # boolean gets stuck on.
        payload.pop(f"on_shift_{mb['id']}")
        oc.post("/admin/inbox-flags/routing", data=payload, follow_redirects=True)
        got = _one("SELECT route_to_on_shift FROM mailbox_routing WHERE id = ?", (mb["id"],))
        s.check("and unticking it actually turns it off", got["route_to_on_shift"] == 0,
                detail="an absent checkbox has to read as off, or it can never be "
                       "switched back")

    # ------------------------------------------------------ HR escalation
    s.section("How long an HR item may sit")
    rules = db()
    rule = rules.execute("SELECT * FROM hr_escalation_rules LIMIT 1").fetchone()
    rules.close()
    s.check("there are chase-up rules to tune", rule is not None)
    if rule:
        oc.post("/admin/hr/escalation-rules",
                data={f"sla_{rule['id']}": "7", f"esc_{rule['id']}": "14",
                      f"active_{rule['id']}": "1"}, follow_redirects=True)
        got = _one("SELECT * FROM hr_escalation_rules WHERE id = ?", (rule["id"],))
        s.check("the reminder threshold is saved", abs(got["sla_days"] - 7) < 0.01,
                detail=str(got["sla_days"]))
        s.check("and the escalation one", abs(got["escalate_after_days"] - 14) < 0.01)

        # Escalating before the first reminder would produce an escalation
        # nobody had been warned about.
        oc.post("/admin/hr/escalation-rules",
                data={f"sla_{rule['id']}": "10", f"esc_{rule['id']}": "3",
                      f"active_{rule['id']}": "1"}, follow_redirects=True)
        got = _one("SELECT * FROM hr_escalation_rules WHERE id = ?", (rule["id"],))
        s.check("escalating before the reminder is corrected, not stored",
                abs(got["escalate_after_days"] - got["sla_days"]) < 0.01,
                detail=f"sla {got['sla_days']}, escalate {got['escalate_after_days']}")

        oc.post("/admin/hr/escalation-rules",
                data={f"sla_{rule['id']}": "not a number",
                      f"esc_{rule['id']}": "14"}, follow_redirects=True)
        unchanged = _one("SELECT sla_days FROM hr_escalation_rules WHERE id = ?", (rule["id"],))
        s.check("junk in a threshold leaves the old one alone",
                abs(unchanged["sla_days"] - got["sla_days"]) < 0.01,
                detail=f"{unchanged['sla_days']} — a rule that silently becomes "
                       "zero chases everybody every day")

    r = oc.post("/admin/hr/run-escalation", follow_redirects=True)
    s.check("the chase-up run can be triggered by hand",
            any("chase-up run" in f.lower() for f in flashes(r)), detail=str(flashes(r)))
    s.check("and it reports what it did rather than only that it ran",
            any(len(f) > len("Chase-up run: ") for f in flashes(r)), detail=str(flashes(r)))

    # -------------------------------------------------------------- push
    s.section("A browser subscribing to notifications")
    sub = {"endpoint": TAG + "-endpoint-1",
           "keys": {"p256dh": "a-public-key", "auth": "an-auth-secret"}}
    r = ec.post("/notifications/subscribe", json=sub)
    s.check("an employee can subscribe", r.status_code == 200, detail=str(r.status_code))
    saved = _one("SELECT * FROM push_subscriptions WHERE endpoint = ?", (TAG + "-endpoint-1",))
    s.check("and it is stored against them", saved and saved["user_id"] == emp["id"],
            detail=str(dict(saved)) if saved else "")

    # The same browser subscribing again. Counting rows cannot prove this on
    # its own: endpoint is UNIQUE, so a route with no upsert also leaves
    # exactly one row — by raising IntegrityError and 500ing. The count check
    # passed while the real behaviour was an error on every returning browser.
    # So the second call has to SUCCEED, and the row has to carry the keys it
    # just sent rather than the ones from last time.
    again = ec.post("/notifications/subscribe",
                    json={"endpoint": TAG + "-endpoint-1",
                          "keys": {"p256dh": "a-rotated-key", "auth": "a-rotated-secret"}})
    s.check("the same browser can subscribe again without erroring",
            again.status_code == 200,
            detail=f"HTTP {again.status_code} \u2014 a unique index turns a missing "
                   "upsert into a 500, not into a duplicate row")
    count = _one("SELECT COUNT(*) AS c FROM push_subscriptions WHERE endpoint = ?",
                 (TAG + "-endpoint-1",))["c"]
    s.check("and there is still only one row", count == 1,
            detail=f"{count} rows \u2014 each one is another copy of every notification")
    keys = _one("SELECT p256dh FROM push_subscriptions WHERE endpoint = ?",
                (TAG + "-endpoint-1",))
    s.check("carrying the key it just sent, not the one from last time",
            keys and keys["p256dh"] == "a-rotated-key",
            detail=str(keys["p256dh"]) if keys else "")

    r = ec.post("/notifications/subscribe", json={"endpoint": TAG + "-broken"})
    s.check("a subscription with no keys is refused", r.status_code == 400,
            detail=str(r.status_code))
    s.check("and nothing was stored for it",
            _one("SELECT COUNT(*) AS c FROM push_subscriptions WHERE endpoint = ?",
                 (TAG + "-broken",))["c"] == 0)

    r = ec.post("/notifications/unsubscribe", json={"endpoint": TAG + "-endpoint-1"})
    s.check("unsubscribing works", r.status_code == 200)
    s.check("and the row is gone",
            _one("SELECT COUNT(*) AS c FROM push_subscriptions WHERE endpoint = ?",
                 (TAG + "-endpoint-1",))["c"] == 0)

    # --------------------------------------------------------- who may not
    s.section("Who is allowed to do any of this")
    # Named by mechanism. A logged-out POST that redirects to the login page
    # and a POST an employee is refused look identical from the status code,
    # so each is paired with the write NOT having happened.
    before = _one("SELECT COUNT(*) AS c FROM social_posts")["c"]
    r = ec.post("/management/social/new",
                data={"platform": "Instagram", "caption": TAG + " employee wrote this"},
                follow_redirects=False)
    s.check("an employee cannot add a post", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and none was added",
            _one("SELECT COUNT(*) AS c FROM social_posts WHERE caption LIKE ?",
                 (TAG + " employee%",))["c"] == 0,
            detail="the status code alone would pass on a redirect to a page "
                   "that then did the write anyway")
    s.check("the count is unchanged",
            _one("SELECT COUNT(*) AS c FROM social_posts")["c"] == before)

    r = anon.post("/management/social/generate", follow_redirects=False)
    s.check("a stranger cannot run the generator", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")

    r = ec.post("/admin/hr/run-escalation", follow_redirects=False)
    s.check("an employee cannot run the HR chase-ups", r.status_code in (302, 303, 403),
            detail=f"HTTP {r.status_code}")

    if seeded:
        mb = seeded
        was = _one("SELECT label FROM mailbox_routing WHERE id = ?", (mb["id"],))["label"]
        ec.post("/admin/inbox-flags/routing",
                data={f"label_{mb['id']}": TAG + " employee renamed it"},
                follow_redirects=True)
        now_label = _one("SELECT label FROM mailbox_routing WHERE id = ?", (mb["id"],))["label"]
        s.check("nor change who owns an inbox", now_label == was,
                detail=f"{was!r} -> {now_label!r}")

    # A signed-in employee IS allowed the two that are theirs, which is what
    # makes the refusals above mean something rather than being a blanket lock.
    r = ec.post("/notifications/subscribe",
                json={"endpoint": TAG + "-endpoint-2",
                      "keys": {"p256dh": "k", "auth": "a"}})
    s.check("but the same employee can still subscribe to notifications",
            r.status_code == 200,
            detail="if this failed too, every refusal above would just be "
                   "'employees cannot POST anything'")

    r = anon.post("/notifications/subscribe",
                  json={"endpoint": TAG + "-endpoint-3",
                        "keys": {"p256dh": "k", "auth": "a"}})
    s.check("a stranger cannot", r.status_code in (302, 303, 401, 403),
            detail=f"HTTP {r.status_code}")
    s.check("and nothing was stored for them",
            _one("SELECT COUNT(*) AS c FROM push_subscriptions WHERE endpoint = ?",
                 (TAG + "-endpoint-3",))["c"] == 0)

    _cleanup()
    return s


if __name__ == "__main__":
    print(run().report())
